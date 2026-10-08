"""
PRODUCT PAGE VERIFIER AGENT
Decides whether a fetched product page is EXACTLY the product named on the dealer price sheet.

Flow (called from the collect step for every candidate page, best-ranked first):
  1. Hard checks the AI cannot override (no AI cost):
       - capacity written in the sheet name (mAh / 'K') must not contradict the page title
  2. AI verdict (Groq first, Gemini fallback via call_llm_json): match / not_match / unsure + reason,
     given the sheet name, sheet prices, the page's real title/price/specs/text, and the OTHER candidate
     listings found, so it can say 'unsure' when the sheet name fits several variants equally.
  3. Price sanity guard: a 'match' whose page price is wildly off the sheet price is downgraded to 'unsure'.
  4. If no AI provider is available: deterministic fallback (accept only a clear, untied rule match).

Verdicts:
  match      -> use this page
  not_match  -> remember the page as rejected (never retried for this product) and try the next candidate
  unsure     -> do not use; offered to the person as an option if nothing matches
"""
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, ConfigDict

from src.utils.logger import setup_logger

logger = setup_logger("verifier")

MAX_CANDIDATES_PER_SOURCE = 4


class ProductMatchVerdict(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)
    decision: str = Field(description="Exactly one of: 'match', 'not_match', 'unsure'")
    confidence: int = Field(default=50, description="0-100 confidence in the decision")
    reason: str = Field(default="", description="One short sentence explaining the decision")


SYSTEM_PROMPT = (
    "You verify product listings for a B2B electronics catalogue.\n"
    "Decide if the WEB PAGE is exactly the same product as the PRICE SHEET entry.\n"
    "Rules:\n"
    "1. Same brand family and same model AND same variant. Model numbers and letters must agree "
    "(e.g. 'Buds 2' is NOT 'Buds 2 Plus', 'Buds 2a' or 'Buds Pro 2'; 'Ear (a)' is NOT 'Ear (3a)').\n"
    "2. Capacity (mAh) must agree. A small wattage difference between the sheet and the page can be a spec "
    "update of the same product; judge by name and price.\n"
    "3. Colour differences do NOT matter (same model in another colour is a match). Sub-brands count as the "
    "brand (e.g. 'CMF' products are made by 'Nothing').\n"
    "4. A special edition, bundle or combo of the model is not_match unless the sheet name mentions it.\n"
    "5. If the sheet name could equally describe one of the OTHER listings shown and price does not settle it, "
    "answer 'unsure'. Other listings that are the same model in another colour do not make it unsure. "
    "If the page is clearly a different product, answer 'not_match'.\n"
    "Answer with decision = 'match' | 'not_match' | 'unsure', a confidence 0-100 and a one-sentence reason."
)


def _price_out_of_range(page_price: Optional[float], sheet_dp: Optional[float], sheet_mrp: Optional[float]) -> bool:
    """
    True only when the page price is implausible for the sheet product (catches a different model / bundle).
    Page prices are MRP or selling price; dealer price (DP) is normally well below MRP, so DP-only bounds are wide.
    Page prices under Rs 200 are ignored (unreliable parses).
    """
    def _num(v):
        try:
            f = float(v)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None
    price, dp, mrp = _num(page_price), _num(sheet_dp), _num(sheet_mrp)
    if not price or price < 200:
        return False
    if mrp:
        return price > 2.0 * mrp or price < 0.3 * mrp
    if dp:
        return price > 6.0 * dp or price < 0.5 * dp
    return False


def verify_product_page(
    brand: str,
    model_name: str,
    category: Optional[str],
    page_url: str,
    page_title: Optional[str],
    page_text: Optional[str] = None,
    page_specs: Optional[Dict[str, str]] = None,
    page_price: Optional[float] = None,
    sheet_dp: Optional[float] = None,
    sheet_mrp: Optional[float] = None,
    candidate_title: Optional[str] = None,
    other_candidates: Optional[List[str]] = None,
    rule_valid: bool = True,
    rule_tied: bool = False,
    llm_config: Optional[dict] = None
) -> Tuple[str, str, Optional[str]]:
    """Returns (decision, reason, provider). decision is 'match', 'not_match' or 'unsure'."""
    from src.utils.scraper import extract_spec_quantities
    from src.utils.llm_client import call_llm_json

    title = (page_title or candidate_title or "").strip()
    slug = page_url.rstrip("/").split("/")[-1].split("?")[0].replace("-", " ")

    # 1. Hard check: capacity in the sheet name vs the page
    tgt_q = extract_spec_quantities(model_name)
    page_q = extract_spec_quantities(f"{title} {candidate_title or ''} {slug}")
    if tgt_q["mah"] and page_q["mah"] and not (tgt_q["mah"] & page_q["mah"]):
        return "not_match", f"Capacity differs: sheet {sorted(tgt_q['mah'])} mAh vs page {sorted(page_q['mah'])} mAh", None

    # 2. AI verdict
    specs_str = ", ".join(f"{k}: {v}" for k, v in (page_specs or {}).items() if v) or "none found"
    own = {title.strip().lower(), (candidate_title or "").strip().lower()}
    others = []
    for o in other_candidates or []:
        o_clean = (o or "").strip()
        if o_clean and o_clean.lower() not in own and o_clean not in others:
            others.append(o_clean)
    others = others[:6]
    user_prompt = (
        f"PRICE SHEET ENTRY\n"
        f"  Brand: {brand}\n  Model name: {model_name}\n  Category: {category or 'unknown'}\n"
        f"  Dealer price (DP): {sheet_dp if sheet_dp else 'not given'}\n"
        f"  MRP: {sheet_mrp if sheet_mrp else 'not given'}\n\n"
        f"WEB PAGE\n  URL: {page_url}\n  Page title: {title or 'unknown'}\n"
        f"  Listing title: {candidate_title or 'unknown'}\n"
        f"  Price on page: {page_price if page_price else 'not found'}\n"
        f"  Specs on page: {specs_str}\n"
        f"  Page text (excerpt): {(page_text or '')[:1200]}\n\n"
        f"OTHER LISTINGS FOUND FOR THIS SEARCH: {'; '.join(others) if others else 'none'}\n"
    )
    data, provider = call_llm_json(SYSTEM_PROMPT, user_prompt, ProductMatchVerdict, llm_config=llm_config,
                                   purpose="Product Verifier", timeout_s=25.0, temperature=0.0)

    if not data:
        # 4. No AI available: accept only a clear rule match, otherwise ask the person
        if rule_valid and not rule_tied:
            return "match", "AI verifier unavailable; accepted clear rule-based match", None
        return "unsure", "AI verifier unavailable and rule match was not clear", None

    decision = str(data.get("decision", "")).strip().lower().replace(" ", "_").replace("-", "_")
    if decision not in ("match", "not_match", "unsure"):
        decision = "unsure"
    reason = str(data.get("reason") or "").strip() or "no reason given"

    # 3. Price sanity guard
    if decision == "match" and _price_out_of_range(page_price, sheet_dp, sheet_mrp):
        return "unsure", f"AI said match but page price {page_price} is far from sheet price (DP {sheet_dp}, MRP {sheet_mrp})", provider
    return decision, reason, provider


def order_candidates(candidates: List[Dict[str, Any]], exclude: Optional[set] = None) -> List[Dict[str, Any]]:
    """Rule-valid candidates first (highest score first), then near-misses; excluded URLs removed."""
    excl = {u.strip().rstrip("/").lower() for u in (exclude or set()) if u}
    kept = [c for c in candidates if c["url"].strip().rstrip("/").lower() not in excl]
    return sorted(kept, key=lambda c: (not c.get("valid"), -float(c.get("score") or 0)))


def is_tied(candidate: Dict[str, Any], candidates: List[Dict[str, Any]]) -> bool:
    """True when another rule-valid candidate has the same score (the rules alone cannot tell them apart)."""
    if not candidate.get("valid"):
        return False
    return any(c is not candidate and c.get("valid") and abs(float(c.get("score") or 0) - float(candidate.get("score") or 0)) < 1e-6
               for c in candidates)
