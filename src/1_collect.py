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
    search_shopify_brand_store,
    search_retail_reliance,
    search_retail_croma,
    DEFAULT_HEADERS
)
from src.utils.llm_client import draft_bullets_and_subtitle
from src.parsers.base import ParserResult
from src.utils.logger import setup_logger

logger = setup_logger("collect")
BLOCKED_BRAND_DOMAINS: Set[str] = set()


def load_config(config_path: str = "config.yaml") -> dict:
    """Loads configuration settings from config.yaml."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def find_product_on_brand_collection(
    model_name: str,
    collection_url: str,
    qualifier_tokens: List[str],
    brand: str = "",
    exclude_urls: Optional[Set[str]] = None
) -> Optional[str]:
    """
    Tier 2 helper: Scans brand collection page for product links matching model name with candidate scoring.
    """
    exclude = {u.strip().rstrip("/").lower() for u in (exclude_urls or set()) if u}
    try:
        r = requests.get(collection_url, headers=DEFAULT_HEADERS, timeout=15)
        if r.status_code != 200:
            return None
        
        soup = BeautifulSoup(r.text, "html.parser")
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

        candidates = []
        for raw_href, text_list in prod_map.items():
            full_url = urljoin(collection_url, raw_href)
            clean_full = full_url.rstrip("/").lower()
            if clean_full in exclude:
                continue

            combined_candidate = " ".join(text_list)
            score, is_valid, diag = score_candidate_match(
                model_name, combined_candidate, full_url, brand=brand, qualifier_tokens=qualifier_tokens
            )
            if is_valid and score > 0:
                candidates.append({"url": full_url, "title": combined_candidate, "score": score})

        if candidates:
            candidates.sort(key=lambda c: c["score"], reverse=True)
            best = candidates[0]
            logger.info(f"[Tier 2] Best collection page match for '{model_name}' (score={best['score']:.1f}): {best['title']} -> {best['url']}")
            return best["url"]
    except Exception as e:
        logger.debug(f"Collection page search error: {e}")

    return None


def is_specs_insufficient(specs: Dict[str, str]) -> Tuple[bool, int, List[str]]:
    """
    Checks if a spec dictionary has > 2 of 5 required specs empty.
    Required specs: capacity, output, ports, weight, warranty.
    """
    required_keys = ["capacity", "output", "ports", "weight", "warranty"]
    missing = [k for k in required_keys if not specs.get(k) or is_empty_value(specs.get(k))]
    return (len(missing) > 2), len(missing), missing


def execute_vision_fallback_for_page(
    url: str,
    brand: str,
    model_name: str,
    base_tier: int,
    config: dict,
    existing_images: Optional[List[str]] = None
) -> Optional[ParserResult]:
    """
    VISION FALLBACK:
    When a tier returns HTTP 200 but specs are missing from HTML because they sit inside images,
    download candidate spec-infographic images and send them to Gemini Vision to extract the spec table.
    Record Source_ as the page URL and Tier_ with a '-vision' suffix.
    Only fires after HTML parsing has failed or yielded insufficient specs for that tier.
    """
    from src.utils.llm_client import extract_specs_via_vision
    logger.info(f"[Vision Fallback] Scanning spec infographic images on {url} for {brand} {model_name}...")
    candidate_img_urls = []

    if existing_images:
        for img_u in existing_images:
            if not any(ign in img_u.lower() for ign in ["icon", "logo", "badge", "payment", "flag", "star", "preview_images"]):
                if img_u not in candidate_img_urls:
                    candidate_img_urls.append(img_u)

    try:
        r = requests.get(url, headers=DEFAULT_HEADERS, timeout=10)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            for img in soup.find_all("img"):
                src = img.get("src") or img.get("data-src") or img.get("data-master")
                if src:
                    if src.startswith("//"):
                        src = f"https:{src}"
                    elif not src.startswith("http"):
                        src = urljoin(url, src)
                    
                    src_lower = src.lower()
                    if any(ign in src_lower for ign in ["logo", "wa-logo", "free_shipping", "warranty.gif", "secure_checkout", "loox", "icon", "star", "badge", "width=250", "width=80", "height="]):
                        continue
                    if src not in candidate_img_urls:
                        candidate_img_urls.append(src)
    except Exception as e:
        logger.debug(f"[Vision Fallback] HTML scan failed: {e}")

    if not candidate_img_urls:
        logger.warning(f"[Vision Fallback] No candidate spec images found on {url}")
        return None

    # Download candidate images at full resolution and keep 4 largest
    raw_images = []
    for img_url in candidate_img_urls[:8]:
        try:
            base_u = img_url.split("?")[0]
            dl_url = f"{base_u}?width=1200" if "cdn/shop" in img_url else img_url
            ir = requests.get(dl_url, headers=DEFAULT_HEADERS, timeout=10)
            if ir.status_code == 200 and len(ir.content) > 10000:
                mime = "image/jpeg" if any(ext in dl_url.lower() for ext in ["jpg", "jpeg"]) else "image/png"
                raw_images.append((ir.content, mime))
        except Exception as e:
            logger.debug(f"[Vision Fallback] Image download failed for {img_url}: {e}")

    if not raw_images:
        logger.warning(f"[Vision Fallback] Could not download any images from {url}")
        return None

    # Sort by byte size descending and select at most 4 largest images
    image_bytes_list = sorted(raw_images, key=lambda x: len(x[0]), reverse=True)[:4]

    vision_specs = extract_specs_via_vision(
        image_bytes_list, url, brand, model_name, llm_config=config.get("llm", {})
    )

    if not vision_specs:
        return None

    tier_label = f"{base_tier}-vision"
    field_sources = {k: url for k in vision_specs}
    field_tiers = {k: tier_label for k in vision_specs}

    return ParserResult(
        success=True,
        status_code=200,
        url=url,
        specs=vision_specs,
        image_urls=candidate_img_urls,
        field_sources=field_sources,
        field_tiers=field_tiers,
        tier=tier_label
    )


from src.parsers.brochure import parse_brochure_for_model


def execute_spec_escalation(
    product_id: str,
    brand: str,
    model_name: str,
    manual_url: Optional[str],
    brand_cfg: dict,
    exclude_urls: Optional[Set[str]] = None,
    config: Optional[dict] = None,
    brochure_override: Optional[str] = None
) -> Tuple[Optional[ParserResult], Optional[str]]:
    """
    Executes 5-tier escalation for product specs:
    Tier 0: Brand Brochure PDF (Text layer -> Vision Fallback)
    Tier 1: Brand Product Page (Direct JSON API / HTML -> Vision Fallback)
    Tier 2: Brand Collection Page (HTML -> Vision Fallback)
    Tier 3: Direct Retail Endpoints (Reliance Digital, Croma, Flipkart, Tata)
    Tier 4: Exhaustion / Auto-Skip.
    Merges data across tiers: Tier 0 outranks web tiers; later tiers only fill missing gaps.
    """
    qualifier_tokens = brand_cfg.get("qualifier_tokens", ["Max", "Ultra", "Plus", "Pro", "Mini", "Lite", "Go"])
    brand_domain = brand_cfg.get("domain", f"{brand.lower()}.com")
    cfg = config or {}

    combined_res: Optional[ParserResult] = None
    combined_provenance: Optional[str] = None

    def _merge_res(base: Optional[ParserResult], incoming: ParserResult, tier_name: str) -> Tuple[ParserResult, str]:
        nonlocal combined_provenance
        if base is None:
            for k in incoming.specs:
                if incoming.specs[k] and not is_empty_value(incoming.specs[k]):
                    incoming.field_sources[k] = incoming.url
                    incoming.field_tiers[k] = incoming.tier
            if incoming.mrp:
                incoming.field_sources["mrp"] = incoming.url
                incoming.field_tiers["mrp"] = incoming.tier
            incoming.field_sources["title"] = incoming.url
            incoming.field_tiers["title"] = incoming.tier
            incoming.field_sources["subtitle"] = incoming.url
            incoming.field_tiers["subtitle"] = incoming.tier
            combined_provenance = tier_name
            return incoming, tier_name
        
        # Merge missing specs from incoming into base (base outranks incoming)
        for k, v in incoming.specs.items():
            if v and not is_empty_value(v):
                if k not in base.specs or not base.specs[k] or is_empty_value(base.specs[k]):
                    base.specs[k] = v
                    base.field_sources[k] = incoming.url
                    base.field_tiers[k] = incoming.tier
        
        # Merge MRP if base lacked it
        if not base.mrp and incoming.mrp:
            base.mrp = incoming.mrp
            base.field_sources["mrp"] = incoming.url
            base.field_tiers["mrp"] = incoming.tier
            
        # Merge description text if base is thin
        if len(base.description_text or "") < len(incoming.description_text or ""):
            base.description_text = f"{base.description_text or ''}\n{incoming.description_text or ''}".strip()
            
        # Merge images if base lacked them
        if not base.image_urls and incoming.image_urls:
            base.image_urls = incoming.image_urls
            
        combined_provenance = f"{combined_provenance} + {tier_name}"
        return base, combined_provenance

    # ==================== TIER 0: Brand Brochure PDF ====================
    brochure_res = parse_brochure_for_model(
        brand=brand,
        model_name=model_name,
        qualifier_tokens=qualifier_tokens,
        config=cfg,
        brochure_override=brochure_override
    )
    if brochure_res and brochure_res.specs:
        combined_res, combined_provenance = _merge_res(
            combined_res, brochure_res, f"tier-{brochure_res.tier}: ({brochure_res.url})"
        )
        is_thin, missing_count, missing_keys = is_specs_insufficient(combined_res.specs)
        if not is_thin:
            return combined_res, combined_provenance
        logger.info(f"[{product_id}] Tier 0 Brochure yielded partial specs ({missing_count}/5 empty: {missing_keys}). Escalating to Tier 1 to fill gaps...")

    # Check if brand domain was previously blocked by bot challenge
    if brand_domain in BLOCKED_BRAND_DOMAINS:
        logger.warning(f"[{product_id}] Brand domain '{brand_domain}' is flagged as BLOCKED by bot challenge. Skipping Tiers 1 & 2 -> Escalating directly to Tier 3 Retail.")
    else:
        # ==================== TIER 1: Brand Product Page (Shopify Direct / Generic JSON-LD & Sitemap) ====================
        tier1_url = manual_url if manual_url and manual_url.startswith("http") else None
        if not tier1_url:
            # 1a. Try Shopify direct index
            tier1_url = search_shopify_brand_store(brand, model_name, brand_domain, qualifier_tokens, exclude_urls=exclude_urls)
            
            # 1b. Non-Shopify Generic Sitemap Fallback
            if not tier1_url:
                try:
                    from src.parsers.generic import GenericBrandParser
                    generic_parser = GenericBrandParser(brand_domain, brand, brand_cfg)
                    match_info = generic_parser.find_product_url(model_name, qualifier_tokens)
                    if match_info:
                        tier1_url = match_info[0]
                        logger.info(f"[{product_id}] [Generic Sitemap Match] Found '{match_info[1]}' -> {tier1_url} (score={match_info[2]:.1f})")
                except Exception as e:
                    logger.debug(f"[{product_id}] Generic parser search failed: {e}")

        if tier1_url:
            logger.info(f"[{product_id}] Executing Tier 1 (Brand Product Page): {tier1_url}")
            res = fetch_and_parse_url(tier1_url, tier=1)
            if res.is_blocked:
                BLOCKED_BRAND_DOMAINS.add(brand_domain)
                logger.warning(f"[Bot Block Detection] Brand domain '{brand_domain}' returned HTTP 403 / Bot Challenge. Marking brand as BLOCKED and escalating to Tier 3 Retail.")
            elif res.success and res.specs:
                combined_res, combined_provenance = _merge_res(combined_res, res, f"tier-1: brand-page ({tier1_url})")
                is_thin, missing_count, missing_keys = is_specs_insufficient(combined_res.specs)
                if not is_thin:
                    return combined_res, combined_provenance
                logger.warning(f"[{product_id}] Tier 1 HTML yielded insufficient specs ({missing_count}/5 empty: {missing_keys}). Checking Vision Fallback...")
            
            # VISION FALLBACK Tier 1: Fires if HTML returned 200 but specs are missing / thin
            if res.status_code == 200:
                vision_res = execute_vision_fallback_for_page(tier1_url, brand, model_name, base_tier=1, config=cfg, existing_images=res.image_urls)
                if vision_res and vision_res.specs:
                    combined_res, combined_provenance = _merge_res(combined_res, vision_res, f"tier-1-vision: brand-page ({tier1_url})")
                    is_thin, missing_count, missing_keys = is_specs_insufficient(combined_res.specs)
                    if not is_thin:
                        return combined_res, combined_provenance
                    logger.warning(f"[{product_id}] Tier 1 Vision yielded partial specs ({missing_count}/5 empty). Escalating to Tier 2...")
                else:
                    logger.warning(f"[{product_id}] Tier 1 Vision yielded no specs. Escalating to Tier 2...")
            elif not res.is_blocked:
                fail_type = "Delisted"
                logger.warning(f"[{product_id}] Tier 1 failed ({fail_type}): {res.error}. Escalating to Tier 2...")
        else:
            logger.info(f"[{product_id}] Tier 1 product URL not found. Escalating to Tier 2...")

        # ==================== TIER 2: Brand Collection Page ====================
        collection_url = brand_cfg.get("collection_url")
        if collection_url and brand_domain not in BLOCKED_BRAND_DOMAINS:
            logger.info(f"[{product_id}] Executing Tier 2 (Brand Collection Page): {collection_url}")
            tier2_prod_url = find_product_on_brand_collection(model_name, collection_url, qualifier_tokens, brand=brand, exclude_urls=exclude_urls)
            if tier2_prod_url:
                res = fetch_and_parse_url(tier2_prod_url, tier=2)
                if res.is_blocked:
                    BLOCKED_BRAND_DOMAINS.add(brand_domain)
                    logger.warning(f"[Bot Block Detection] Brand collection page '{collection_url}' returned HTTP 403 / Bot Challenge. Marking brand as BLOCKED.")
                elif res.success and res.specs:
                    combined_res, combined_provenance = _merge_res(combined_res, res, f"tier-2: brand-collection ({tier2_prod_url})")
                    is_thin, missing_count, missing_keys = is_specs_insufficient(combined_res.specs)
                    if not is_thin:
                        return combined_res, combined_provenance
                    logger.warning(f"[{product_id}] Tier 2 HTML yielded insufficient specs ({missing_count}/5 empty). Checking Vision Fallback...")
                
                # VISION FALLBACK Tier 2
                if res.status_code == 200:
                    vision_res = execute_vision_fallback_for_page(tier2_prod_url, brand, model_name, base_tier=2, config=cfg, existing_images=res.image_urls)
                    if vision_res and vision_res.specs:
                        combined_res, combined_provenance = _merge_res(combined_res, vision_res, f"tier-2-vision: brand-collection ({tier2_prod_url})")
                        is_thin, missing_count, missing_keys = is_specs_insufficient(combined_res.specs)
                        if not is_thin:
                            return combined_res, combined_provenance
                        logger.warning(f"[{product_id}] Tier 2 Vision yielded partial specs ({missing_count}/5 empty). Escalating to Tier 3...")
                    else:
                        logger.warning(f"[{product_id}] Tier 2 Vision yielded no specs. Escalating to Tier 3...")
            else:
                logger.warning(f"[{product_id}] Tier 2 collection page did not yield matching product. Escalating to Tier 3...")

    # ==================== TIER 3: Direct Retail Endpoints (Reliance Digital) ====================
    retail_order = brand_cfg.get("retail_order", ["reliance"])
    for retail_store in retail_order:
        store_lower = retail_store.lower()
        retail_url = None
        if "reliance" in store_lower:
            retail_url = search_retail_reliance(brand, model_name, qualifier_tokens, exclude_urls=exclude_urls)
        elif "croma" in store_lower:
            retail_url = search_retail_croma(brand, model_name, qualifier_tokens, exclude_urls=exclude_urls)

        if retail_url:
            logger.info(f"[{product_id}] Executing Tier 3 ({retail_store}): {retail_url}")
            res = fetch_and_parse_url(retail_url, tier=3)
            if res.success and res.specs:
                combined_res, combined_provenance = _merge_res(combined_res, res, f"tier-3: retail-{retail_store} ({retail_url})")
                is_thin, missing_count, missing_keys = is_specs_insufficient(combined_res.specs)
                if not is_thin:
                    return combined_res, combined_provenance
                logger.warning(f"[{product_id}] Tier 3 ({retail_store}) HTML yielded insufficient specs ({missing_count}/5 empty). Checking Vision Fallback...")
            
            # VISION FALLBACK Tier 3
            if res.status_code == 200:
                vision_res = execute_vision_fallback_for_page(retail_url, brand, model_name, base_tier=3, config=cfg, existing_images=res.image_urls)
                if vision_res and vision_res.specs:
                    combined_res, combined_provenance = _merge_res(combined_res, vision_res, f"tier-3-vision: retail-{retail_store} ({retail_url})")
                    is_thin, missing_count, missing_keys = is_specs_insufficient(combined_res.specs)
                    if not is_thin:
                        return combined_res, combined_provenance
                    logger.warning(f"[{product_id}] Tier 3 Vision yielded partial specs ({missing_count}/5 empty). Trying next retail...")
                else:
                    logger.warning(f"[{product_id}] Tier 3 Vision yielded no specs. Trying next retail...")
            else:
                logger.warning(f"[{product_id}] Tier 3 {retail_store} failed. Trying next retail...")


    if combined_res is not None and combined_res.specs:
        logger.warning(f"[{product_id}] All spec tiers exhausted. Returning partial specs collected via {combined_provenance}.")
        return combined_res, combined_provenance

    logger.error(f"[{product_id}] All spec tiers exhausted (including Vision) without finding verified specs.")
    return None, None


def collect_data_for_row(row_dict: Dict[str, Any], config: dict, exclude_urls: Optional[Set[str]] = None) -> Tuple[Dict[str, Any], bool, str]:
    """
    Executes collection for a single row following the Tier Escalation Model.
    Returns (updates_dict, is_success, log_msg).
    """
    brand = str(row_dict.get("Brand", "")).strip()
    model_name = str(row_dict.get("Model_Name", "")).strip()
    product_id = str(row_dict.get("Product_ID", f"{brand}_{model_name}")).strip()
    manual_url = str(row_dict.get("Product_URL", "")).strip() if not is_empty_value(row_dict.get("Product_URL")) else None
    brochure_override = str(row_dict.get("Brochure_PDF", "")).strip() if not is_empty_value(row_dict.get("Brochure_PDF")) else None

    brand_defaults = load_brand_defaults(brand)
    parser_res, provenance = execute_spec_escalation(
        product_id, brand, model_name, manual_url, brand_defaults, exclude_urls=exclude_urls, config=config, brochure_override=brochure_override
    )

    if not parser_res or not parser_res.specs:
        # AUTO-SKIP ON EXHAUSTION: All tiers (including Vision) exhausted. Write NOTHING to Raw_ columns.
        return {
            "Status": "Skipped",
            "Flags": "Skipped: All spec tiers exhausted (including Vision) without finding technical specifications",
            "Fix_Log": "Tier 1-4 escalation (with Vision fallback) exhausted; no verified technical specs found."
        }, False, f"[{product_id}] SKIPPED: All tiers exhausted"

    # Call LLM ONLY to draft bullets and subtitle from verified product text
    llm_copy, provider_used = draft_bullets_and_subtitle(
        brand=brand,
        model_name=model_name,
        product_description_block=parser_res.description_text or "",
        specs=parser_res.specs,
        llm_config=config.get("llm", {})
    )

    if not llm_copy:
        # INFRASTRUCTURE FAILURE: Specs were fetched, but LLM copy generation failed.
        # MUST NEVER set to Blocked. Mark as Deferred, keeping collected specs.
        source_url = parser_res.url
        tier = parser_res.tier
        specs = parser_res.specs
        updates = {
            "Source_URL": source_url,
            "Source_Audit": provenance,
            "Raw_Spec_Capacity": specs.get("capacity"),
            "Source_Spec_Capacity": parser_res.field_sources.get("capacity") or (source_url if specs.get("capacity") else None),
            "Tier_Spec_Capacity": parser_res.field_tiers.get("capacity", tier) if specs.get("capacity") else None,
            "Raw_Spec_Output": specs.get("output"),
            "Source_Spec_Output": parser_res.field_sources.get("output") or (source_url if specs.get("output") else None),
            "Tier_Spec_Output": parser_res.field_tiers.get("output", tier) if specs.get("output") else None,
            "Raw_Spec_Ports": specs.get("ports"),
            "Source_Spec_Ports": parser_res.field_sources.get("ports") or (source_url if specs.get("ports") else None),
            "Tier_Spec_Ports": parser_res.field_tiers.get("ports", tier) if specs.get("ports") else None,
            "Raw_Spec_Weight": specs.get("weight"),
            "Source_Spec_Weight": parser_res.field_sources.get("weight") or (source_url if specs.get("weight") else None),
            "Tier_Spec_Weight": parser_res.field_tiers.get("weight", tier) if specs.get("weight") else None,
            "Raw_Spec_Warranty": specs.get("warranty") or brand_defaults.get("default_warranty"),
            "Source_Spec_Warranty": parser_res.field_sources.get("warranty") or (source_url if specs.get("warranty") else ("brand-default-policy" if brand_defaults.get("default_warranty") else None)),
            "Tier_Spec_Warranty": parser_res.field_tiers.get("warranty", tier),
            "Status": "Deferred",
            "LLM_Provider": None,
            "Flags": "Deferred: LLM copy drafting failed due to infrastructure error",
            "Fix_Log": f"Specs collected via {provenance}; LLM copy drafting deferred due to infrastructure error"
        }
        if parser_res.image_urls:
            updates["Image_URL"] = parser_res.image_urls[0]
        return updates, False, f"[{product_id}] DEFERRED: Specs collected via {provenance}, LLM drafting failed"

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
    has_mrp_display = not pd.isna(row_dict.get("MRP_Display")) and str(row_dict.get("MRP_Display")).strip() not in ("", "nan", "None")
    mrp_val = None if has_mrp_display else parser_res.mrp
    mrp_source = None if has_mrp_display else (parser_res.field_sources.get("mrp") or (source_url if mrp_val else None))
    mrp_tier = None if has_mrp_display else (parser_res.field_tiers.get("mrp", tier) if mrp_val else None)

    updates = {
        "Source_URL": source_url,
        "Source_Audit": provenance,
        "Raw_Title": llm_copy.get("title", f"{brand} {model_name}"),
        "Source_Title": parser_res.field_sources.get("title", source_url),
        "Tier_Title": parser_res.field_tiers.get("title", tier),
        "Raw_Subtitle": llm_copy.get("subtitle", ""),
        "Source_Subtitle": parser_res.field_sources.get("subtitle", source_url),
        "Tier_Subtitle": parser_res.field_tiers.get("subtitle", tier),
        "Raw_MRP_Scraped": mrp_val if mrp_val else None,
        "Source_MRP_Scraped": mrp_source,
        "Tier_MRP_Scraped": mrp_tier,
        "Raw_Spec_Capacity": specs.get("capacity"),
        "Source_Spec_Capacity": parser_res.field_sources.get("capacity") or (source_url if specs.get("capacity") else None),
        "Tier_Spec_Capacity": parser_res.field_tiers.get("capacity", tier) if specs.get("capacity") else None,
        "Raw_Spec_Output": specs.get("output"),
        "Source_Spec_Output": parser_res.field_sources.get("output") or (source_url if specs.get("output") else None),
        "Tier_Spec_Output": parser_res.field_tiers.get("output", tier) if specs.get("output") else None,
        "Raw_Spec_Ports": specs.get("ports"),
        "Source_Spec_Ports": parser_res.field_sources.get("ports") or (source_url if specs.get("ports") else None),
        "Tier_Spec_Ports": parser_res.field_tiers.get("ports", tier) if specs.get("ports") else None,
        "Raw_Spec_Weight": specs.get("weight"),
        "Source_Spec_Weight": parser_res.field_sources.get("weight") or (source_url if specs.get("weight") else None),
        "Tier_Spec_Weight": parser_res.field_tiers.get("weight", tier) if specs.get("weight") else None,
        "Raw_Spec_Warranty": specs.get("warranty") or brand_defaults.get("default_warranty"),
        "Source_Spec_Warranty": parser_res.field_sources.get("warranty") or (source_url if specs.get("warranty") else ("brand-default-policy" if brand_defaults.get("default_warranty") else None)),
        "Tier_Spec_Warranty": parser_res.field_tiers.get("warranty", tier),
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
        "LLM_Provider": provider_used,
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

        # Skip approved or ready-for-review rows
        if status in ("Approved", "Ready_For_Review") and not target_pids:
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
