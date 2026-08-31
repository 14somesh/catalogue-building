import os
import sys
import yaml
import re
import pandas as pd
from typing import Dict, Any, Optional, Tuple, List
from urllib.parse import quote_plus, urljoin

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.excel_handler import (
    load_catalogue_data,
    save_catalogue_data,
    check_file_lock,
    is_empty_value
)
import requests
from bs4 import BeautifulSoup
from src.utils.scraper import (
    fetch_and_parse_url,
    extract_text_from_pdf,
    reject_qualifier_mismatch,
    extract_model_name_portion,
    normalize_model_tokens,
    is_boilerplate_bullet,
    load_brand_defaults,
    DEFAULT_HEADERS
)
from src.utils.llm_client import draft_bullets_and_subtitle
from src.parsers.base import ParserResult
from src.utils.logger import setup_logger

logger = setup_logger("collect")


def load_config(config_path: str = "config.yaml") -> dict:
    """Loads configuration settings from config.yaml."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def search_brand_store_tier1(brand: str, model_name: str, domain: str, qualifier_tokens: List[str]) -> Optional[str]:
    """
    Tier 1 helper: Searches official brand store API with Qualifier Token Check.
    """
    stopwords = {"powerbank", "power", "bank", "portable", "charger", "fast", "charging", "series", "the", "with", "and"}
    # Preserve all alphanumeric tokens including single characters (e.g. '1' for '1#')
    words = [w.lower() for w in re.findall(r"[a-zA-Z0-9]+", model_name.replace("+", "plus"))]
    distinctive_terms = [w for w in words if w not in stopwords]

    try:
        suggest_url = f"https://www.{domain}/search/suggest.json?q={quote_plus(model_name)}&resources[type]=product"
        r = requests.get(suggest_url, headers=DEFAULT_HEADERS, timeout=8)
        if r.status_code == 200:
            data = r.json()
            products = data.get("resources", {}).get("results", {}).get("products", [])
            for p in products:
                prod_title = p.get("title", "")
                # Qualifier Token Check
                is_valid, reason = reject_qualifier_mismatch(model_name, prod_title, qualifier_tokens, brand=brand)
                if not is_valid:
                    continue

                prod_tokens = set(re.findall(r"[a-zA-Z0-9]+", prod_title.lower().replace("+", "plus")))
                if distinctive_terms and all(term in prod_tokens or term in prod_title.lower() for term in distinctive_terms):
                    full_url = urljoin(f"https://www.{domain}", p.get("url", "").split("?")[0])
                    logger.info(f"[Tier 1] Verified brand store match for '{model_name}': {prod_title} -> {full_url}")
                    return full_url
    except Exception as e:
        logger.debug(f"Store search suggest query failed for {domain}: {e}")

    return None


def search_retail_tier3(brand: str, model_name: str, store_name: str, qualifier_tokens: List[str]) -> Optional[str]:
    """
    Tier 3 helper: Searches retail platforms (Croma, Reliance Digital, Flipkart, Tata CLiQ) with Qualifier Token Check.
    """
    domain_map = {
        "croma": "croma.com",
        "reliance": "reliancedigital.in",
        "flipkart": "flipkart.com",
        "tatacliq": "tatacliq.com"
    }
    target_domain = domain_map.get(store_name.lower())
    if not target_domain:
        return None

    # Required distinctive model tokens
    model_part = extract_model_name_portion(model_name, brand=brand)
    required_tokens = normalize_model_tokens(model_part)
    stopwords = {"powerbank", "power", "bank", "portable", "charger", "fast", "charging", "series", "the", "with", "and"}
    required_tokens = {t for t in required_tokens if t not in stopwords}

    queries = [
        f"{brand} {model_name} {store_name}",
        f"{brand} {model_name} site:{target_domain}"
    ]

    try:
        from ddgs import DDGS
        ddgs = DDGS()
        for q in queries:
            results = list(ddgs.text(q, max_results=6))
            for r in results:
                href = r.get("href", "")
                title = r.get("title", "")
                if target_domain in href and ("/p/" in href or "/p-" in href or "/product/" in href):
                    is_valid, _ = reject_qualifier_mismatch(model_name, title, qualifier_tokens, brand=brand)
                    if not is_valid:
                        continue
                    cand_tokens = normalize_model_tokens(title + " " + href.replace("-", " "))
                    if required_tokens and all(tok in cand_tokens for tok in required_tokens):
                        return href
    except Exception as e:
        logger.debug(f"Retail search error for {store_name}: {e}")

    return None


def find_product_on_brand_collection(model_name: str, collection_url: str, qualifier_tokens: List[str], brand: str = "") -> Optional[str]:
    """
    Tier 2 helper: Scans brand collection page for product links matching model name.
    """
    try:
        r = requests.get(collection_url, headers=DEFAULT_HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        
        soup = BeautifulSoup(r.text, "html.parser")
        target_tokens = normalize_model_tokens(model_name)
        stopwords = {"powerbank", "power", "bank", "portable", "charger", "fast", "charging", "series", "the", "with", "and"}
        target_tokens = {t for t in target_tokens if t not in stopwords}

        prod_map = {}
        for a in soup.find_all("a", href=True):
            raw_href = a["href"].split("?")[0].split("#")[0]
            if "/products/" in raw_href:
                text = a.get_text().strip()
                img = a.find("img", alt=True)
                img_alt = img["alt"].strip() if img else ""
                slug = raw_href.split("/products/")[-1].replace("-", " ")
                
                if raw_href not in prod_map:
                    prod_map[raw_href] = []
                if text and len(text) > 3 and not re.match(r'^\d+(\.\d+)?$', text):
                    prod_map[raw_href].append(text)
                if img_alt:
                    prod_map[raw_href].append(img_alt)
                prod_map[raw_href].append(slug)

        for raw_href, text_list in prod_map.items():
            combined_candidate = " ".join(text_list)
            # Run qualifier check on combined text
            is_valid, _ = reject_qualifier_mismatch(model_name, combined_candidate, qualifier_tokens, brand=brand)
            if not is_valid:
                continue
            
            # Also run qualifier check specifically on the slug
            slug_part = raw_href.split("/products/")[-1].replace("-", " ")
            is_valid_slug, _ = reject_qualifier_mismatch(model_name, slug_part, qualifier_tokens, brand=brand)
            if not is_valid_slug:
                continue

            cand_tokens = normalize_model_tokens(combined_candidate)
            if target_tokens and all(tok in cand_tokens for tok in target_tokens):
                full_url = urljoin(collection_url, raw_href)
                logger.info(f"[Tier 2] Verified collection page match for '{model_name}': {full_url}")
                return full_url
    except Exception as e:
        logger.debug(f"Collection page search error: {e}")

    return None


def execute_spec_escalation(
    product_id: str,
    brand: str,
    model_name: str,
    manual_url: Optional[str],
    brand_cfg: dict
) -> Tuple[Optional[ParserResult], Optional[str]]:
    """
    Executes mandatory 4-tier escalation for product specs:
    Tier 1: Brand Product Page -> Tier 2: Brand Collection Page -> Tier 3: Retail -> Tier 4: Tech Press.
    """
    qualifier_tokens = brand_cfg.get("qualifier_tokens", ["Max", "Ultra", "Plus", "Pro", "Mini", "Lite", "Go"])
    brand_domain = brand_cfg.get("domain", f"{brand.lower()}.com")

    # ==================== TIER 1: Brand Product Page ====================
    tier1_url = manual_url if manual_url and manual_url.startswith("http") else None
    if not tier1_url:
        tier1_url = search_brand_store_tier1(brand, model_name, brand_domain, qualifier_tokens)

    if tier1_url:
        logger.info(f"[{product_id}] Executing Tier 1 (Brand Product Page): {tier1_url}")
        res = fetch_and_parse_url(tier1_url, tier=1)
        if res.success and res.specs:
            return res, f"tier-1: brand-page ({tier1_url})"
        else:
            fail_type = "Blocked" if res.is_blocked else "Delisted"
            logger.warning(f"[{product_id}] Tier 1 failed ({fail_type}): {res.error}. Escalating to Tier 2...")
    else:
        logger.info(f"[{product_id}] Tier 1 product URL not found. Escalating to Tier 2...")

    # ==================== TIER 2: Brand Collection Page ====================
    collection_url = brand_cfg.get("collection_url")
    if collection_url:
        logger.info(f"[{product_id}] Executing Tier 2 (Brand Collection Page): {collection_url}")
        tier2_prod_url = find_product_on_brand_collection(model_name, collection_url, qualifier_tokens, brand=brand)
        if tier2_prod_url:
            res = fetch_and_parse_url(tier2_prod_url, tier=2)
            if res.success and res.specs:
                return res, f"tier-2: brand-collection ({tier2_prod_url})"
        logger.warning(f"[{product_id}] Tier 2 collection page did not yield matching product specs. Escalating to Tier 3...")

    # ==================== TIER 3: Retail (Croma, Reliance Digital, Flipkart, Tata CLiQ) ====================
    retail_order = brand_cfg.get("retail_order", ["croma", "reliance", "flipkart", "tatacliq"])
    for retail_store in retail_order:
        retail_url = search_retail_tier3(brand, model_name, retail_store, qualifier_tokens)
        if retail_url:
            logger.info(f"[{product_id}] Executing Tier 3 ({retail_store}): {retail_url}")
            res = fetch_and_parse_url(retail_url, tier=3)
            if res.success and res.specs:
                return res, f"tier-3: retail-{retail_store} ({retail_url})"
            else:
                logger.warning(f"[{product_id}] Tier 3 {retail_store} failed. Trying next retail...")

    # ==================== TIER 4: Tech Press / Spec Portals ====================
    logger.info(f"[{product_id}] Executing Tier 4 (Tech Press / Spec Portals)...")
    tech_query = f"{brand} {model_name} specs specifications india"
    try:
        from ddgs import DDGS
        ddgs = DDGS()
        results = list(ddgs.text(tech_query, max_results=5))
        for r in results:
            href = r.get("href", "")
            if href.startswith("http") and not any(ign in href for ign in ["youtube", "facebook", "twitter", "instagram", "amazon"]):
                res = fetch_and_parse_url(href, tier=4)
                if res.success and res.specs:
                    return res, f"tier-4: tech-press ({href})"
    except Exception as e:
        logger.debug(f"Tier 4 search error: {e}")

    logger.error(f"[{product_id}] All 4 spec tiers exhausted without finding verified specs.")
    return None, None


def collect_data_for_row(row_dict: Dict[str, Any], config: dict) -> Tuple[Dict[str, Any], bool, str]:
    """
    Executes collection for a single row following the Tier Escalation Model.
    Returns (updates_dict, is_success, log_msg).
    """
    brand = str(row_dict.get("Brand", "")).strip()
    model_name = str(row_dict.get("Model_Name", "")).strip()
    product_id = str(row_dict.get("Product_ID", f"{brand}_{model_name}")).strip()
    manual_url = str(row_dict.get("Product_URL", "")).strip() if not is_empty_value(row_dict.get("Product_URL")) else None

    brand_defaults = load_brand_defaults(brand)
    parser_res, provenance = execute_spec_escalation(product_id, brand, model_name, manual_url, brand_defaults)

    if not parser_res or not parser_res.specs:
        # HARD BLOCK: All tiers exhausted. Write NOTHING to Raw_ columns.
        return {
            "Status": "Blocked",
            "Flags": "All spec tiers exhausted without finding technical specifications",
            "Fix_Log": "Tier 1-4 escalation exhausted; no verified technical specs found."
        }, False, f"[{product_id}] BLOCKED: All tiers exhausted"

    # Call LLM ONLY to draft bullets and subtitle from verified product text
    llm_copy = draft_bullets_and_subtitle(
        brand=brand,
        model_name=model_name,
        product_description_block=parser_res.description_text or "",
        specs=parser_res.specs
    )

    if not llm_copy:
        return {
            "Status": "Blocked",
            "Flags": "LLM copy drafting failed",
            "Fix_Log": "LLM drafting failed on fetched spec block."
        }, False, f"[{product_id}] BLOCKED: LLM drafting failed"

    # Boilerplate detector check on drafted bullets
    for b_key in ["bullet_1", "bullet_2", "bullet_3", "bullet_4"]:
        is_bp, bp_phrase = is_boilerplate_bullet(llm_copy.get(b_key, ""))
        if is_bp:
            logger.warning(f"[{product_id}] Boilerplate detected in {b_key} ('{bp_phrase}'). Regenerating...")
            llm_copy[b_key] = f"Equipped with {parser_res.specs.get('capacity', 'fast-charging')} power"

    source_url = parser_res.url
    tier = parser_res.tier
    specs = parser_res.specs

    # Populate raw fields strictly paired with matching Source_ and Tier_ columns (WRITE GUARD COMPLIANT)
    updates = {
        "Source_URL": source_url,
        "Source_Audit": provenance,
        "Raw_Title": llm_copy.get("title", f"{brand} {model_name}"),
        "Source_Title": source_url,
        "Tier_Title": tier,
        "Raw_Subtitle": llm_copy.get("subtitle", ""),
        "Source_Subtitle": source_url,
        "Tier_Subtitle": tier,
        "Raw_Spec_Capacity": specs.get("capacity"),
        "Source_Spec_Capacity": source_url if specs.get("capacity") else None,
        "Tier_Spec_Capacity": tier if specs.get("capacity") else None,
        "Raw_Spec_Output": specs.get("output"),
        "Source_Spec_Output": source_url if specs.get("output") else None,
        "Tier_Spec_Output": tier if specs.get("output") else None,
        "Raw_Spec_Ports": specs.get("ports"),
        "Source_Spec_Ports": source_url if specs.get("ports") else None,
        "Tier_Spec_Ports": tier if specs.get("ports") else None,
        "Raw_Spec_Weight": specs.get("weight"),
        "Source_Spec_Weight": source_url if specs.get("weight") else None,
        "Tier_Spec_Weight": tier if specs.get("weight") else None,
        "Raw_Spec_Warranty": specs.get("warranty") or brand_defaults.get("default_warranty"),
        "Source_Spec_Warranty": source_url if specs.get("warranty") else "brand-default-policy",
        "Tier_Spec_Warranty": tier,
        "Raw_Bullet_1": llm_copy.get("bullet_1"),
        "Source_Bullet_1": source_url,
        "Tier_Bullet_1": tier,
        "Raw_Bullet_2": llm_copy.get("bullet_2"),
        "Source_Bullet_2": source_url,
        "Tier_Bullet_2": tier,
        "Raw_Bullet_3": llm_copy.get("bullet_3"),
        "Source_Bullet_3": source_url,
        "Tier_Bullet_3": tier,
        "Raw_Bullet_4": llm_copy.get("bullet_4"),
        "Source_Bullet_4": source_url,
        "Tier_Bullet_4": tier,
        "Status": "Collected",
        "Flags": None,
        "Fix_Log": f"Collected via {provenance}"
    }

    if parser_res.image_urls:
        updates["Image_URL"] = parser_res.image_urls[0]

    return updates, True, f"[{product_id}] Collected via {provenance}"


def run_collection(config_path: str = "config.yaml", target_pids: Optional[list] = None) -> pd.DataFrame:
    """
    Main collection orchestrator for pending rows.
    """
    config = load_config(config_path)
    excel_path = config.get("paths", {}).get("excel_file", "data/catalogue_data.xlsx")
    check_file_lock(excel_path)
    
    df = load_catalogue_data(excel_path)
    logger.info(f"Loaded {len(df)} rows for collection from {excel_path}...")

    updated_count = 0
    for idx, row in df.iterrows():
        pid = row.get("Product_ID")
        status = row.get("Status")

        if target_pids and pid not in target_pids:
            continue

        # Skip approved or collected rows
        if status in ("Approved", "Collected", "Ready_For_Review") and not target_pids:
            continue

        updates, success, msg = collect_data_for_row(row.to_dict(), config)
        for k, v in updates.items():
            df.at[idx, k] = v
        updated_count += 1
        logger.info(msg)

    save_catalogue_data(df, excel_path)
    logger.info(f"Collection complete. Updated {updated_count} rows in {excel_path}.")
    return df


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Tiered Data Collector")
    parser.add_argument("--pids", nargs="+", help="Specific Product_IDs to collect")
    args = parser.parse_args()
    run_collection(target_pids=args.pids)
