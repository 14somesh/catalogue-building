import os
import re
import json
import hashlib
import yaml
import requests
from bs4 import BeautifulSoup
from typing import Optional, Dict, Any, List, Tuple, Set
from urllib.parse import urlparse, quote_plus, urljoin
import pdfplumber

from src.utils.logger import setup_logger
from src.parsers import get_parser_for_url, BaseParser, ParserResult
from src.utils.waf_detector import detect_waf_block

logger = setup_logger("scraper")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

DEFAULT_QUALIFIER_TOKENS = ["Max", "Ultra", "Plus", "Pro", "Mini", "Lite", "Go"]

BOILERPLATE_PHRASES = [
    "free shipping",
    "leading indian brand",
    "homegrown",
    "reliable and durable",
    "top quality",
    "extensive product portfolio",
    "wide range",
    "made in india with love",
    "pan india",
    "hassle free warranty",
    "customer support",
    "add to cart",
    "subscribe to our newsletter",
    "all rights reserved",
    "terms of service",
    "privacy policy"
]


CACHE_DIR = "cache"


def get_cached_json(cache_key: str, fetch_fn) -> Optional[Any]:
    """Retrieves JSON response from disk cache, or executes fetch_fn and stores it."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    safe_key = hashlib.md5(cache_key.encode("utf-8")).hexdigest()
    cache_file = os.path.join(CACHE_DIR, f"{safe_key}.json")

    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.debug(f"Cache read error for {cache_key}: {e}")

    # Fetch and cache
    data = fetch_fn()
    if data is not None:
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.debug(f"Cache write error for {cache_key}: {e}")
    return data


def load_brand_defaults(
    brand: str,
    category: Optional[str] = None,
    config_path: str = "config/brand_defaults.yaml"
) -> dict:
    """
    Loads brand configuration defaults.
    If category is provided, merges category-specific settings (qualifier_tokens, collection_url)
    over the shared brand defaults.
    """
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                brands_dict = data.get("brands", {})
                brand_entry = None
                if brand in brands_dict:
                    brand_entry = brands_dict[brand]
                else:
                    for b_name, b_val in brands_dict.items():
                        if b_name.lower() == str(brand).lower():
                            brand_entry = b_val
                            break

                default_entry = brands_dict.get("_default", {})
                base = dict(default_entry)
                if brand_entry:
                    base.update(brand_entry)

                categories_dict = base.get("categories") or (brand_entry.get("categories") if brand_entry else {}) or {}
                cat_entry = None
                if category and categories_dict:
                    clean_cat = str(category).strip().lower()
                    for c_name, c_val in categories_dict.items():
                        if c_name.lower() == clean_cat:
                            cat_entry = c_val
                            break
                if cat_entry and isinstance(cat_entry, dict):
                    resolved = dict(base)
                    resolved.update(cat_entry)
                    return resolved

                # If a specific category was requested but not found in categories_dict,
                # return base brand settings without category-specific collection_url / qualifier_tokens
                if category:
                    resolved = dict(base)
                    resolved["collection_url"] = None
                    resolved["qualifier_tokens"] = []
                    return resolved

                return base
        except Exception as e:
            logger.warning(f"Failed loading brand defaults from {config_path}: {e}")
    return {}


def normalize_model_tokens(text: str) -> Set[str]:
    """Normalizes text by removing commas in numbers (e.g. '10,000' -> '10000'), treating '+' as 'plus', equating 'magtag'/'magsafe', splitting alphanumeric boundaries, and extracting alphanumeric tokens."""
    # Strip commas between digits
    t = re.sub(r'(\d+),(\d+)', r'\1\2', text)
    t = re.sub(r'\+', ' plus ', t)
    t = re.sub(r'mag\s*tag|magtag|mag\s*safe|magsafe', ' magsafe ', t, flags=re.IGNORECASE)
    t = re.sub(r'([a-zA-Z]+)(\d+)', r'\1 \2', t)
    t = re.sub(r'(\d+)([a-zA-Z]+)', r'\1 \2', t)
    words = re.findall(r'[a-zA-Z0-9]+', t.lower())
    token_set = set(words)
    if "10" in token_set:
        token_set.add("10000")
    if "10000" in token_set:
        token_set.add("10")
    if "20" in token_set:
        token_set.add("20000")
    if "20000" in token_set:
        token_set.add("20")
    return token_set


def extract_model_name_portion(title: str, brand: str = "") -> str:
    """
    Extracts the core model name portion of a candidate title string before capacity,
    wattage, or descriptive category keywords. Handles multiple leading capacities/wattages cleanly,
    and splits on title delimiters like ' - ', ' | ', ' / ', ' – ', ' — '.
    """
    cleaned = title
    if brand:
        cleaned = re.sub(rf'^{re.escape(brand)}(?:one|\s+one|-one)?\s*', '', cleaned, flags=re.IGNORECASE)
    
    # Strip ALL leading capacities and wattages (e.g. '10000 mAh 15 W ...')
    cleaned = re.sub(r'^(?:\s*(?:\d+(?:,\d+)?\s*mah|\d+k\b|\d+(?:\.\d+)?\s*w\b))+\s*', '', cleaned, flags=re.IGNORECASE).strip()
    
    # If title has a clear marketing delimiter like ' - ' or ' | ' or ' – ' or ' — ', take the leading part
    delim_match = re.search(r'\s+[-|–—/]\s+', cleaned)
    if delim_match:
        leading_part = cleaned[:delim_match.start()].strip()
        if leading_part:
            cleaned = leading_part

    # Split on capacity, wattage, or generic category keywords
    split_pattern = r'\b(?:\d+(?:,\d+)?\s*mah|\d+k\b|\d+(?:\.\d+)?\s*w\b|qi\d*(?:\.\d+)?|certified|power\s*bank|powerbank|charger|with\s+built|with\s+type|with\s+stand|made\s+in)\b'
    m = re.search(split_pattern, cleaned, flags=re.IGNORECASE)
    if m:
        leading_part = cleaned[:m.start()].strip()
        if leading_part:
            return leading_part
    return cleaned


def score_candidate_match(
    target_model_name: str,
    candidate_title: str,
    candidate_url: str,
    brand: str = "",
    qualifier_tokens: Optional[List[str]] = None,
    category: Optional[str] = None,
    target_capacity: Optional[str] = None
) -> Tuple[float, bool, str]:
    """
    BIDIRECTIONAL CANDIDATE MATCHER & SCORER:
    1. Requires all target model tokens to be present in candidate (subset check or token-boundary equivalence).
    2. Enforces qualifier token mismatch checks.
    3. Rewards exact title / model portion / slug match.
    4. Strongly penalizes candidates with extra model-name tokens not in target (excluding category descriptors and internal SKUs).
    Returns (score, is_valid, diagnostic_reason).
    """
    from src.utils.category_specs import get_category_descriptor_tokens

    brand_lower = brand.lower() if brand else ""
    category_descriptors = get_category_descriptor_tokens(category)
    stopwords = {"pb", brand_lower, "powerbank", "power", "bank", "portable", "charger", "fast", "charging", "series", "the", "with", "and", "in", "for", "customized", "custom", "suction"} | category_descriptors
    
    # 1. Target tokens
    target_model_part = extract_model_name_portion(target_model_name, brand=brand)
    target_tokens = normalize_model_tokens(target_model_part) - stopwords
    if not target_tokens:
        target_tokens = normalize_model_tokens(target_model_name) - stopwords
        
    # 2. Candidate tokens
    cand_model_part = extract_model_name_portion(candidate_title, brand=brand)
    cand_model_tokens = normalize_model_tokens(cand_model_part) - stopwords
    cand_slug = candidate_url.split("/")[-1].split("?")[0].replace("-", " ")
    cand_all_tokens = normalize_model_tokens(candidate_title + " " + cand_slug) - stopwords
    
    # Subset check: every target token (or primary model name tokens without qualifiers) must be in candidate tokens
    qualifiers_norm = set()
    for q in (qualifier_tokens or []):
        qualifiers_norm.update(normalize_model_tokens(q))
    primary_target_tokens = target_tokens - qualifiers_norm
    if not primary_target_tokens:
        primary_target_tokens = target_tokens

    is_subset = target_tokens.issubset(cand_all_tokens) or primary_target_tokens.issubset(cand_all_tokens)
    if not is_subset:
        # Narrow token-boundary / compound check (e.g. 'openloop' vs 'open loop')
        cand_all_alphanumeric = re.sub(r'[^a-zA-Z0-9]', '', (candidate_title + " " + cand_slug)).lower()
        target_str_compact = re.sub(r'[^a-zA-Z0-9]', '', target_model_part).lower()
        if target_str_compact and target_str_compact in cand_all_alphanumeric:
            is_subset = True
        else:
            target_primary_compact = re.sub(r'[^a-zA-Z0-9]', '', " ".join(sorted(primary_target_tokens))).lower()
            if target_primary_compact and target_primary_compact in cand_all_alphanumeric:
                is_subset = True

    if not is_subset:
        return -1.0, False, f"Target tokens {primary_target_tokens} missing in candidate '{candidate_title}'"
        
    # Qualifier token check
    is_valid_q, reason_q = reject_qualifier_mismatch(target_model_name, candidate_title, qualifier_tokens, brand=brand)
    if not is_valid_q:
        return -1.0, False, reason_q or "Qualifier mismatch"

    # Exact matches
    target_clean = target_model_name.strip().lower()
    cand_title_clean = candidate_title.strip().lower()
    exact_title_match = (target_clean == cand_title_clean)
    
    target_part_clean = target_model_part.strip().lower()
    cand_part_clean = cand_model_part.strip().lower()
    exact_model_portion = (target_part_clean == cand_part_clean) or (target_tokens == cand_model_tokens)
    
    target_slug_clean = target_clean.replace(" ", "")
    cand_slug_clean = cand_slug.strip().lower().replace(" ", "")
    exact_slug_match = (target_slug_clean == cand_slug_clean)
    
    # Extra tokens penalty in model portion (Rule 1 & Rule 3)
    # If candidate model-name segment contains extra model tokens not in target, reject as different model
    raw_extra_tokens = (cand_model_tokens - target_tokens) - qualifiers_norm - category_descriptors
    # Ignore internal SKU part numbers (e.g. 'p0109', 'p0208', 'p0301', '0109', 'p')
    sku_tokens = {tok for tok in raw_extra_tokens if re.fullmatch(r'p\d+|\d{3,5}|p|sku', tok, re.I)}
    extra_model_tokens = raw_extra_tokens - sku_tokens
    
    # Narrow token-boundary equivalence: if candidate model segment matches target across whitespace/hyphens
    if extra_model_tokens:
        target_comp = re.sub(r'[^a-zA-Z0-9]', '', target_model_part).lower()
        cand_comp = re.sub(r'[^a-zA-Z0-9]', '', cand_model_part).lower()
        if target_comp and target_comp == cand_comp:
            extra_model_tokens = set()

    if extra_model_tokens:
        reason = f"Extra model token mismatch in '{cand_model_part}': candidate has {extra_model_tokens} not in target '{target_model_name}'."
        logger.warning(f"Rejected candidate '{candidate_title}' for '{target_model_name}': {reason}")
        return -1.0, False, reason

    score = 100.0
    if exact_title_match:
        score += 150.0
    elif exact_model_portion:
        score += 100.0
        
    if exact_slug_match:
        score += 50.0
        
    # Minor penalty for general descriptive words not in target (-5 pts each)
    cand_all_norm = normalize_model_tokens(candidate_title) - stopwords
    extra_title_tokens = cand_all_norm - target_tokens
    score -= len(extra_title_tokens) * 5.0

    if target_capacity:
        target_cap_tokens = {t for t in normalize_model_tokens(target_capacity) if re.fullmatch(r'\d{2,6}|mah', t)}
        cand_cap_tokens = {t for t in normalize_model_tokens(candidate_title + " " + cand_slug) if re.fullmatch(r'\d{2,6}', t)}
        if target_cap_tokens and cand_cap_tokens:
            score += 20.0 if (target_cap_tokens & cand_cap_tokens) else -20.0

    return score, True, f"Score: {score:.1f} (exact_model: {exact_model_portion})"


def extract_leading_model_segment(title: str, brand: str = "") -> str:
    """
    Extracts the leading model name segment of a candidate title before capacity, wattage, or descriptor words.
    E.g. 'Roam+ 20000mAh Mini wired Powerbank' -> 'Roam+'
         'Stuffcool Giga Max 65W 20000mAh Powerbank' -> 'Giga Max'
         '10000 mAh Nano Power Bank' -> 'Nano'
         '10000 mAh 15 W Arc MagTag Power Bank with stand' -> 'Arc MagTag'
    """
    cleaned = title
    if brand:
        cleaned = re.sub(rf'^{re.escape(brand)}(?:one|\s+one|-one)?\s*', '', cleaned, flags=re.IGNORECASE)
    
    # Strip ALL leading capacities and wattages (e.g. '10000 mAh 15 W ...')
    cleaned = re.sub(r'^(?:\s*(?:\d+(?:,\d+)?\s*mah|\d+k\b|\d+(?:\.\d+)?\s*w\b))+\s*', '', cleaned, flags=re.IGNORECASE).strip()
    
    delim_match = re.search(r'\s+[-|–—/]\s+', cleaned)
    if delim_match:
        leading_part = cleaned[:delim_match.start()].strip()
        if leading_part:
            cleaned = leading_part

    split_pattern = r'\b(?:\d+(?:,\d+)?\s*mah|\d+k\b|\d+(?:\.\d+)?\s*w\b|qi\d*(?:\.\d+)?|certified|power\s*bank|powerbank|charger|with\s+built|with\s+type|with\s+stand|made\s+in)\b'
    m = re.search(split_pattern, cleaned, flags=re.IGNORECASE)
    if m:
        leading_part = cleaned[:m.start()].strip()
        if leading_part:
            return leading_part
    return cleaned


def save_brand_domain_default(
    brand: str,
    domain: str,
    platform: str = "shopify",
    collection_url: Optional[str] = None,
    category: Optional[str] = None,
    config_path: str = "config/brand_defaults.yaml",
    waf_bypass_via: Optional[str] = None
) -> bool:
    """
    Persists a verified brand website domain (shared) and optional collection URL (category-scoped) to config/brand_defaults.yaml.
    """
    if not os.path.exists(config_path):
        return False
    if collection_url and (not category or not str(category).strip()):
        raise ValueError("Saving collection_url requires a category. Writing category-scoped keys at the brand root is prohibited.")

    clean_category = str(category).strip() if category and str(category).strip() else None

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        if "brands" not in data:
            data["brands"] = {}

        if brand not in data["brands"] or not isinstance(data["brands"][brand], dict):
            data["brands"][brand] = {}

        brand_entry = data["brands"][brand]
        if domain:
            brand_entry["domain"] = domain
        if platform:
            brand_entry["platform"] = platform
        if waf_bypass_via:
            brand_entry["waf_bypass_via"] = waf_bypass_via

        if collection_url and clean_category:
            if "categories" not in brand_entry or not isinstance(brand_entry["categories"], dict):
                brand_entry["categories"] = {}
            cat_entry = brand_entry["categories"].get(clean_category, {})
            cat_entry["collection_url"] = collection_url
            brand_entry["categories"][clean_category] = cat_entry

        # Guard: Never allow category-scoped keys or obsolete keys at the brand root
        brand_entry.pop("collection_url", None)
        brand_entry.pop("qualifier_tokens", None)
        brand_entry.pop("image_tier_order", None)
        brand_entry.pop("default_warranty", None)

        data["brands"][brand] = brand_entry

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False)

        logger.info(f"Successfully saved verified domain '{domain}' (platform={platform}, category={clean_category}, waf_bypass_via={waf_bypass_via}) for brand '{brand}' to {config_path}")
        return True
    except Exception as e:
        logger.error(f"Failed saving brand domain for '{brand}' to {config_path}: {e}")
        return False


def verify_brand_storefront(
    domain: str,
    brand: str,
    model_names: List[str]
) -> Tuple[bool, Optional[str], Optional[str], List[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Verifies that a candidate domain is an authentic storefront selling this brand's products.
    1. Checks if it is a parked / squatter domain or reseller directory.
    2. Cross-checks against the actual product model names from the price sheet.
    3. Detects WAF / Bot protection challenges during discovery and retries via TinyFish Fetch.
    Returns (is_verified, platform, collection_url, matched_models, waf_vendor, waf_reason, actual_domain, waf_bypass_via).
    """
    from urllib.parse import urlparse
    import requests
    from src.utils.tinyfish import is_tinyfish_configured, tinyfish_fetch

    clean_domain = domain.strip().lower().replace("http://", "").replace("https://", "").replace("www.", "").rstrip("/")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    generic_model_words = {
        "live", "go", "pro", "tour", "plus", "max", "mini", "lite", "play", "air",
        "buds", "beam", "free", "one", "tune", "flex", "ace", "neo", "ultra", "flow"
    }

    # 1. Try Shopify products.json index (fastest, most authoritative)
    try:
        pjson_url = f"https://{clean_domain}/products.json?limit=250"
        r = requests.get(pjson_url, headers=headers, timeout=(2.0, 3.0), allow_redirects=True)
        is_blocked, vendor, reason = detect_waf_block(r.status_code, dict(r.headers), r.text, pjson_url)
        
        if is_blocked and is_tinyfish_configured():
            logger.info(f"[Domain Verification] '{clean_domain}' products.json blocked by {vendor}. Retrying via TinyFish Fetch...")
            tf_res = tinyfish_fetch(pjson_url)
            if tf_res and tf_res.get("text"):
                tf_text = tf_res["text"]
                matched = []
                for m in model_names:
                    m_clean = m.strip()
                    if not m_clean:
                        continue
                    if re.search(r'\b' + re.escape(m_clean) + r'\b', tf_text, re.IGNORECASE):
                        matched.append(m)
                has_substantive_match = any(m.lower().strip() not in generic_model_words for m in matched) or len(matched) >= 2
                if matched and has_substantive_match:
                    logger.info(f"[Domain Verification] Domain '{clean_domain}' VERIFIED via TinyFish Fetch products.json for '{brand}' with matched models: {matched}")
                    return True, "shopify", None, matched, vendor, reason, clean_domain, "tinyfish"

        if is_blocked:
            actual_domain = urlparse(r.url).netloc.replace("www.", "").lower() if r.url else clean_domain
            logger.warning(f"[Domain Verification] Domain '{clean_domain}' (at {r.url}) is protected by WAF/Bot challenge ({vendor}: {reason}).")
            # Don't return immediately; allow homepage check with TinyFish fallback

        elif r.status_code == 200:
            data = r.json()
            prods = data.get("products", [])
            if prods:
                titles = [p.get("title", "") for p in prods]
                matched = []
                for m in model_names:
                    m_clean = m.strip()
                    if not m_clean:
                        continue
                    pattern = r'\b' + re.escape(m_clean) + r'\b'
                    if any(re.search(pattern, t, re.IGNORECASE) for t in titles):
                        matched.append(m)

                if matched:
                    # Check collections for category
                    coll_url = f"https://{clean_domain}/collections/power-bank"
                    try:
                        cr = requests.head(coll_url, headers=headers, timeout=(1.5, 2.0))
                        if cr.status_code != 200:
                            coll_url = f"https://{clean_domain}/collections/powerbanks"
                            cr2 = requests.head(coll_url, headers=headers, timeout=(1.5, 2.0))
                            if cr2.status_code != 200:
                                coll_url = f"https://{clean_domain}/collections/all"
                    except Exception:
                        coll_url = f"https://{clean_domain}/collections/all"

                    logger.info(f"[Domain Verification] Domain '{clean_domain}' VERIFIED as Shopify store for '{brand}' with matched models: {matched}")
                    return True, "shopify", coll_url, matched, None, None, clean_domain, None
    except Exception as e:
        logger.debug(f"products.json check failed for {clean_domain}: {e}")

    # 2. Try Homepage / Sitemap HTML check
    try:
        home_url = f"https://{clean_domain}/"
        r = requests.get(home_url, headers=headers, timeout=(2.0, 3.0), allow_redirects=True)
        is_blocked, vendor, reason = detect_waf_block(r.status_code, dict(r.headers), r.text, home_url)
        
        slug_simple = re.sub(r'[^a-zA-Z0-9]', '', brand.lower())

        if is_blocked and is_tinyfish_configured():
            logger.info(f"[Domain Verification] '{clean_domain}' homepage blocked by {vendor}. Retrying via TinyFish Fetch...")
            tf_res = tinyfish_fetch(home_url)
            if tf_res and tf_res.get("text"):
                text_lower = tf_res["text"].lower()
                parked_signatures = [
                    "buy this domain", "domain for sale", "hugedomains", "godaddy", "dan.com",
                    "sedo", "namecheap", "is parked", "domain parking", "this web page is parked"
                ]
                if not any(p in text_lower for p in parked_signatures):
                    matched = []
                    for m in model_names:
                        m_clean = m.strip()
                        if not m_clean:
                            continue
                        if re.search(r'\b' + re.escape(m_clean) + r'\b', text_lower, re.IGNORECASE):
                            matched.append(m)

                    has_substantive_match = any(m.lower().strip() not in generic_model_words for m in matched) or len(matched) >= 2
                    is_brand_official = (
                        slug_simple in clean_domain
                        and brand.lower() in text_lower
                        and any(w in text_lower for w in ["official", "store", "shop", "products", "warranty", "sound", "audio", "cart"])
                    )

                    if (matched and has_substantive_match) or is_brand_official:
                        logger.info(f"[Domain Verification] Domain '{clean_domain}' VERIFIED via TinyFish Fetch homepage for '{brand}' with matched models: {matched}")
                        return True, "custom", None, matched, vendor, reason, clean_domain, "tinyfish"

        if is_blocked:
            actual_domain = urlparse(r.url).netloc.replace("www.", "").lower() if r.url else clean_domain
            logger.warning(f"[Domain Verification] Domain '{clean_domain}' (at {r.url}) is protected by WAF/Bot challenge ({vendor}: {reason}).")
            return False, None, None, [], vendor, reason, actual_domain, None

        if r.status_code == 200:
            text_lower = r.text.lower()
            
            # Parked / Squatter / Domain sale signature checks
            parked_signatures = [
                "buy this domain", "domain for sale", "hugedomains", "godaddy", "dan.com",
                "sedo", "namecheap", "is parked", "domain parking", "this web page is parked"
            ]
            if any(p in text_lower for p in parked_signatures):
                logger.warning(f"[Domain Verification] Domain '{clean_domain}' REJECTED: Parked/Squatter domain detected.")
                return False, None, None, [], None, None, clean_domain, None

            # Check if brand name and at least one substantive model name appear on the storefront
            matched = []
            for m in model_names:
                m_clean = m.strip()
                if not m_clean:
                    continue
                pattern = r'\b' + re.escape(m_clean) + r'\b'
                if re.search(pattern, text_lower, re.IGNORECASE):
                    matched.append(m)

            has_substantive_match = any(m.lower().strip() not in generic_model_words for m in matched) or len(matched) >= 2
            is_brand_official = (
                slug_simple in clean_domain
                and brand.lower() in text_lower
                and any(w in text_lower for w in ["official", "store", "shop", "products", "warranty", "sound", "audio", "cart"])
            )

            if (matched and has_substantive_match) or is_brand_official:
                logger.info(f"[Domain Verification] Domain '{clean_domain}' VERIFIED via homepage for '{brand}' with matched models: {matched}")
                return True, "generic", None, matched, None, None, clean_domain, None
            else:
                logger.debug(f"[Domain Verification] Domain '{clean_domain}' reachable but insufficient substantive models matched ({matched}).")
    except Exception as e:
        logger.debug(f"Homepage check failed for {clean_domain}: {e}")

    return False, None, None, [], None, None, clean_domain, None


def discover_and_verify_brand_domain(
    brand: str,
    model_names: List[str],
    category: str = "Powerbank",
    config_path: str = "config/brand_defaults.yaml"
) -> Dict[str, Any]:
    """
    AUTONOMOUS BRAND DOMAIN DISCOVERY:
    Runs when no domain is configured for a brand. Probes standard candidate domains,
    verifies they are real storefronts selling the brand's products, and cross-checks
    against models from the price sheet. If verified, persists to brand_defaults.yaml.
    Detects WAF/Bot protection challenges during domain discovery and persists blocks or bypasses.
    Falls back to TinyFish Search API if guess-and-verify returns no match.
    """
    clean_brand = brand.strip()
    if not clean_brand:
        return {"domain": None, "verified": False}

    existing_cfg = load_brand_defaults(clean_brand, category=category, config_path=config_path)
    existing_domain = existing_cfg.get("domain")
    if existing_domain and (not existing_cfg.get("waf_blocked") or existing_cfg.get("waf_bypass_via") == "tinyfish"):
        return {
            "domain": existing_domain,
            "platform": existing_cfg.get("platform", "shopify"),
            "collection_url": existing_cfg.get("collection_url"),
            "verified": True,
            "waf_bypass_via": existing_cfg.get("waf_bypass_via")
        }

    # Generate candidate domains
    slug_simple = re.sub(r'[^a-zA-Z0-9]', '', clean_brand.lower())
    slug_hyphen = re.sub(r'[^a-zA-Z0-9]+', '-', clean_brand.lower()).strip('-')

    base_slugs = [slug_simple]
    if slug_simple.endswith("s"):
        base_slugs.append(slug_simple[:-1])
    else:
        base_slugs.append(slug_simple + "s")
    if slug_hyphen != slug_simple:
        base_slugs.extend([slug_hyphen, slug_hyphen + "s"])

    prefixes = ["in.", "", "store.", "shop."]
    extensions = [
        ".in",
        ".com",
        ".co.in",
        "india.com",
        "india.in",
        "store.in",
        "shop.in",
        "tech.in",
        "cart.com",
        "zone.com",
        "world.com",
        "lifestyle.com"
    ]

    candidates = []
    for prefix in prefixes:
        for ext in extensions:
            for s in base_slugs:
                cand = f"{prefix}{s}{ext}" if ext.startswith(".") else f"{prefix}{s}{ext}"
                if cand not in candidates:
                    candidates.append(cand)

    logger.info(f"Initiating autonomous domain discovery for '{clean_brand}' against {len(candidates)} candidate domains...")

    detected_block = None
    for cand_domain in candidates:
        res = verify_brand_storefront(cand_domain, clean_brand, model_names)
        is_verified = res[0]
        platform = res[1]
        coll_url = res[2]
        matched = res[3]
        waf_vendor = res[4] if len(res) > 4 else None
        waf_reason = res[5] if len(res) > 5 else None
        actual_dom = res[6] if len(res) > 6 and res[6] else cand_domain
        waf_bypass_via = res[7] if len(res) > 7 else None

        if is_verified:
            save_brand_domain_default(
                clean_brand, actual_dom or cand_domain, platform=platform or "shopify",
                collection_url=coll_url, category=category, config_path=config_path,
                waf_bypass_via=waf_bypass_via
            )
            return {
                "domain": actual_dom or cand_domain,
                "platform": platform,
                "collection_url": coll_url,
                "verified": True,
                "matched_models": matched,
                "waf_bypass_via": waf_bypass_via
            }
        elif waf_vendor and not detected_block:
            detected_block = (actual_dom, waf_vendor, waf_reason)

    # Secondary fallback: TinyFish Search API for candidate domain discovery
    from src.utils.tinyfish import is_tinyfish_configured, search_brand_domains_via_tinyfish
    if is_tinyfish_configured():
        logger.info(f"[Domain Discovery] Standard candidates unverified. Querying TinyFish Search for '{clean_brand}' domains...")
        search_domains = search_brand_domains_via_tinyfish(clean_brand)
        for cand_domain in search_domains:
            if cand_domain in candidates:
                continue
            res = verify_brand_storefront(cand_domain, clean_brand, model_names)
            is_verified = res[0]
            platform = res[1]
            coll_url = res[2]
            matched = res[3]
            waf_vendor = res[4] if len(res) > 4 else None
            waf_reason = res[5] if len(res) > 5 else None
            actual_dom = res[6] if len(res) > 6 and res[6] else cand_domain
            waf_bypass_via = res[7] if len(res) > 7 else None

            if is_verified:
                save_brand_domain_default(
                    clean_brand, actual_dom or cand_domain, platform=platform or "shopify",
                    collection_url=coll_url, category=category, config_path=config_path,
                    waf_bypass_via=waf_bypass_via
                )
                return {
                    "domain": actual_dom or cand_domain,
                    "platform": platform,
                    "collection_url": coll_url,
                    "verified": True,
                    "matched_models": matched,
                    "waf_bypass_via": waf_bypass_via
                }
            elif waf_vendor and not detected_block:
                detected_block = (actual_dom, waf_vendor, waf_reason)

    if detected_block:
        actual_dom, waf_vendor, waf_reason = detected_block
        from src.utils.profiler import record_waf_block
        record_waf_block(clean_brand, domain=actual_dom, vendor=waf_vendor, reason=waf_reason, platform="custom", config_path=config_path)
        logger.warning(f"[Domain Discovery] Brand '{clean_brand}' storefront '{actual_dom}' is protected by WAF ({waf_vendor}: {waf_reason}). Persisted WAF block.")
        return {
            "domain": actual_dom,
            "platform": "custom",
            "collection_url": None,
            "verified": False,
            "waf_blocked": True,
            "waf_vendor": waf_vendor,
            "waf_reason": waf_reason
        }

    logger.info(f"Domain discovery completed for '{clean_brand}': No verified storefront discovered.")
    return {"domain": None, "verified": False}


def resolve_any_url_to_product(
    manual_url: str,
    target_model_name: str,
    brand: str = "",
    qualifier_tokens: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    UNIVERSAL MANUAL URL RESOLVER:
    Accepts ANY URL (single product page, collection/category listing, site root, or search result).
    1. Fetches and classifies the page by content.
    2. If single product: returns direct product URL for immediate extraction.
    3. If listing/collection/search: extracts all product links, scores candidates against target_model_name.
    4. If site root: discovers collections / products.json and matches against target_model_name.
    5. Learns and persists verified brand domain to brand_defaults.yaml.
    6. Returns detailed diagnostic metadata.
    """
    from urllib.parse import urlparse, urljoin
    from bs4 import BeautifulSoup
    import requests

    clean_url = manual_url.strip()
    if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
        clean_url = f"https://{clean_url}"

    parsed = urlparse(clean_url)
    domain = parsed.netloc.replace("www.", "").lower()
    path = parsed.path.rstrip("/")
    query = parsed.query

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    try:
        r = requests.get(clean_url, headers=headers, timeout=10)
    except Exception as e:
        return {
            "success": False,
            "error_type": "unreachable",
            "message": f"URL is unreachable ({e}): {clean_url}",
            "direct_product_url": None,
            "domain": domain
        }

    if r.status_code in (401, 403, 429) or (r.status_code == 200 and ("cf-challenge" in r.text.lower() or "challenge-running" in r.text.lower())):
        return {
            "success": False,
            "error_type": "blocked",
            "message": f"URL is blocked by anti-bot challenge (HTTP {r.status_code}): {clean_url}",
            "direct_product_url": None,
            "domain": domain
        }

    if r.status_code != 200:
        return {
            "success": False,
            "error_type": "http_error",
            "message": f"Server returned HTTP {r.status_code} for URL: {clean_url}",
            "direct_product_url": None,
            "domain": domain
        }

    html = r.text
    soup = BeautifulSoup(html, "html.parser")

    # Learn and persist domain if verified
    is_ver, plat, coll, _ = verify_brand_storefront(domain, brand or domain, [target_model_name])[:4]
    if is_ver and brand:
        save_brand_domain_default(brand, domain, platform=plat or "shopify", collection_url=coll)

    # 1. Check if Site Root / Homepage
    is_root = (path == "" or path == "/" or path == "/index.html" or path == "/home")

    # 2. Check if Search Page or Collection Page
    is_search = ("search" in path or "q=" in query)
    is_collection = ("/collections/" in path or "/category/" in path or "/shop/" in path or "/c/" in path or "/catalog/" in path)

    # 3. Check for product links on page
    product_links: List[Tuple[str, str]] = [] # (title, full_url)

    # 3a. If Shopify, query products.json
    try:
        pjson_r = requests.get(f"https://{domain}/products.json?limit=250", headers=headers, timeout=4)
        if pjson_r.status_code == 200:
            pj_data = pjson_r.json()
            for p in pj_data.get("products", []):
                p_title = p.get("title", "")
                p_handle = p.get("handle", "")
                if p_handle:
                    p_url = f"https://{domain}/products/{p_handle}"
                    product_links.append((p_title, p_url))
    except Exception:
        pass

    # 3b. Also extract all <a href="..."> links from HTML
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full_a_url = urljoin(clean_url, href)
        a_title = a.get_text(strip=True)
        # Check if URL looks like a product URL
        if re.search(r'/(?:products|product|item|p|pd)/[^/?#]+', href, re.I):
            if full_a_url not in [pl[1] for pl in product_links]:
                product_links.append((a_title or href.split("/")[-1].replace("-", " "), full_a_url))

    # Single product check: if page declares og:type=product, has add-to-cart form/button, or matches product URL pattern
    og_type = soup.find("meta", property=re.compile(r"^og:type$", re.I))
    is_og_product = bool(og_type and "product" in (og_type.get("content") or "").lower())
    has_buy_button = bool(
        soup.find(["button", "input", "form"], id=re.compile(r"add[-_]?to[-_]?cart|product_addtocart_form", re.I))
        or soup.find(["button", "input", "form"], class_=re.compile(r"add[-_]to[-_]cart|buy[-_]now", re.I))
    )
    is_direct_product = (
        (is_og_product and not is_collection and not is_root and not is_search)
        or (re.search(r'/(?:products?|item|p|pd)/[^/?#]+', path, re.I) and not is_collection and not is_root and not is_search)
        or (has_buy_button and not is_collection and not is_root and not is_search)
    )

    if is_direct_product:
        return {
            "success": True,
            "page_type": "single_product",
            "direct_product_url": clean_url,
            "domain": domain,
            "message": f"Direct product page resolved: {clean_url}"
        }

    # Listing / Collection / Site Root / Search Page handling
    if product_links:
        # Score candidate matches against target_model_name
        scored_candidates = []
        for cand_title, cand_url in product_links:
            score, valid, reason = score_candidate_match(
                target_model_name, cand_title, cand_url, brand=brand, qualifier_tokens=qualifier_tokens
            )
            if valid and score >= 100.0:
                scored_candidates.append((score, cand_title, cand_url))

        if scored_candidates:
            scored_candidates.sort(key=lambda x: x[0], reverse=True)
            best_score, best_title, best_url = scored_candidates[0]
            logger.info(f"[Universal URL Resolver] Matched '{target_model_name}' to '{best_title}' ({best_url}) with score {best_score:.1f}")
            return {
                "success": True,
                "page_type": "listing_match",
                "direct_product_url": best_url,
                "domain": domain,
                "matched_title": best_title,
                "message": f"Matched model '{target_model_name}' to listing item '{best_title}': {best_url}"
            }
        else:
            # Listing had no match
            sample_titles = [pl[0] for pl in product_links if pl[0] and len(pl[0]) > 2][:5]
            titles_str = ", ".join(f"'{t}'" for t in sample_titles) if sample_titles else "none named"
            return {
                "success": False,
                "error_type": "listing_no_match",
                "page_type": "listing",
                "total_products_on_page": len(product_links),
                "closest_names": sample_titles,
                "domain": domain,
                "direct_product_url": None,
                "message": f"That page lists {len(product_links)} products ({titles_str}), but none matched model '{target_model_name}'."
            }

    # Fallback if no product links were discoverable on the page
    return {
        "success": False,
        "error_type": "no_products_found",
        "page_type": "generic_page",
        "domain": domain,
        "direct_product_url": None,
        "message": f"No product listings or specifications found on page: {clean_url}"
    }


def reject_qualifier_mismatch(
    target_model_name: str,
    candidate_title: str,
    qualifier_tokens: Optional[List[str]] = None,
    brand: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    """
    STRICT QUALIFIER & BRAND GUARD:
    1. Rejects candidate titles containing an identifiable brand other than the target brand.
    2. Rejects candidate titles containing a qualifier token that does NOT appear in target_model_name.
    Returns (is_valid, rejection_reason).
    """
    if brand and str(brand).strip():
        tgt_brand = str(brand).strip().lower()
        cand_lower = candidate_title.lower()
        # If candidate title explicitly mentions the target brand, it is not a brand mismatch
        if not re.search(rf"\b{re.escape(tgt_brand)}\b", cand_lower):
            known_brands = [
                "stuffcool", "pebble", "portronics", "urbn", "evm", "ambrane", "glow gadget",
                "wangari", "jbl", "boat", "boult audio", "boult", "noise", "ptron", "mivi",
                "crossbeats", "zebronics", "realme", "redmi", "xiaomi", "oneplus", "oppo",
                "vivo", "apple", "samsung", "sony", "anker", "belkin", "hammer", "wings",
                "truke", "skullcandy", "marshall", "sennheiser", "bose", "soundcore",
                "philips", "panasonic", "jabra", "infinity", "fire-boltt", "beatxp",
                "unix", "ubon", "syska", "mi", "nothing", "cmf", "honor", "motorola", "lenovo"
            ]
            for b in known_brands:
                b_clean = b.lower()
                if b_clean != tgt_brand and b_clean not in tgt_brand and tgt_brand not in b_clean:
                    match = re.search(rf"\b{re.escape(b_clean)}\b", cand_lower)
                    if match:
                        start_pos = match.start()
                        prefix = cand_lower[:start_pos].rstrip()
                        suffix = cand_lower[match.end():].lstrip()
                        # Ignore compatibility references (e.g. 'for Apple Watch', 'compatible with Samsung')
                        if re.search(r"\b(?:for|with|compatible with|compatible|supports|suitable for|designed for)$", prefix):
                            continue
                        if re.search(r"^(?:watch|iphone|ipad|macbook|airpods|galaxy|pixel|phone|devices|cables?)\b", suffix):
                            continue
                        detected = b.title()
                        reason = f"Brand mismatch: candidate appears to be '{detected}', expected '{brand}'"
                        logger.warning(f"Rejected candidate '{candidate_title}' for '{target_model_name}': {reason}")
                        return False, reason

    tokens = qualifier_tokens or DEFAULT_QUALIFIER_TOKENS
    target_words = normalize_model_tokens(target_model_name)
    
    # Extract only the leading model segment of candidate title
    model_portion = extract_leading_model_segment(candidate_title, brand=brand)
    candidate_model_words = normalize_model_tokens(model_portion)

    for token in tokens:
        token_lower = token.lower()
        if token_lower in candidate_model_words and token_lower not in target_words:
            reason = f"Qualifier token mismatch in model segment '{model_portion}': candidate contains '{token}' but target Model_Name '{target_model_name}' does not."
            logger.warning(f"Rejected candidate '{candidate_title}' for '{target_model_name}': {reason}")
            return False, reason

    return True, None


def clean_html_text(html: str) -> str:
    """Strips noisy tags and returns clean text from HTML."""
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "svg"]):
        tag.decompose()
    return soup.get_text(separator=" ", strip=True)


def is_boilerplate_bullet(text: str) -> Tuple[bool, Optional[str]]:
    """
    BOILERPLATE DETECTOR:
    Rejects any bullet point or marketing phrase containing generic site-wide boilerplate.
    Returns (is_boilerplate, matching_phrase).
    """
    if not text:
        return True, "Empty text"
    
    text_lower = text.lower().strip()
    for phrase in BOILERPLATE_PHRASES:
        if phrase in text_lower:
            return True, phrase

    return False, None


# ==============================================================================
# DIRECT JSON DISCOVERY ENDPOINTS (NO SEARCH ENGINES)
# ==============================================================================

def fetch_shopify_catalogue(domain: str, timeout: int = 15) -> List[Dict[str, Any]]:
    """Fetches and caches the full Shopify products catalogue with pagination support."""
    cache_key = f"shopify_cat_{domain}"

    def _fetch():
        all_products = []
        page = 1
        while page <= 5:  # Up to 1,250 products
            url = f"https://www.{domain}/products.json?limit=250&page={page}"
            try:
                r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
                is_blocked, vendor, reason = detect_waf_block(r.status_code, dict(r.headers), r.text, url)
                if is_blocked or r.status_code != 200:
                    from src.utils.tinyfish import is_tinyfish_configured, tinyfish_fetch
                    if is_tinyfish_configured():
                        logger.info(f"[Shopify Catalogue] Direct fetch blocked ({vendor or r.status_code}). Retrying via TinyFish Fetch: {url}")
                        tf_res = tinyfish_fetch(url)
                        if tf_res and tf_res.get("text"):
                            try:
                                p_data = json.loads(tf_res["text"])
                                prods = p_data.get("products", [])
                                if prods:
                                    all_products.extend(prods)
                                    if len(prods) < 250:
                                        break
                                    page += 1
                                    continue
                            except Exception:
                                pass
                    break
                else:
                    prods = r.json().get("products", [])
                    if not prods:
                        break
                    all_products.extend(prods)
                    if len(prods) < 250:
                        break
                    page += 1
            except Exception as e:
                logger.warning(f"Error fetching Shopify catalogue for {domain} (page {page}): {e}")
                break
        return all_products

    return get_cached_json(cache_key, _fetch) or []


def extract_clean_candidate_model_name(title: str, brand: str = "") -> str:
    """Extracts concise candidate model name for human-readable diagnostic reporting."""
    cleaned = title
    if brand:
        cleaned = re.sub(rf'^{re.escape(brand)}\s*', '', cleaned, flags=re.I).strip()
    cleaned = re.sub(r'^(?:new\s+launch\s+|all\s+new\s+)', '', cleaned, flags=re.I).strip()
    
    # Split on common category descriptors, marketing delimiters, or capacities
    split_pat = r'\b(?:true\s+wireless|wireless|earbuds?|earphones?|headphones?|neckband|tws|anc|in\s+ear|over\s+ear|on\s+ear|noise\s+cancell?(?:ing|ation)|adaptive|spatial|smart\s+ambient|hi\s*[- ]?res|bluetooth|\d+(?:,\d+)?\s*mah|\d+k\b|\d+(?:\.\d+)?\s*w\b|qi\d*|power\s*bank|powerbank|charger|with\s+built|with\s+type|with\s+stand|made\s+in|premium)\b'
    m = re.search(split_pat, cleaned, flags=re.I)
    if m:
        cleaned = cleaned[:m.start()].strip()
    
    delim_m = re.search(r'\s+[-|–—/]\s+', cleaned)
    if delim_m:
        cleaned = cleaned[:delim_m.start()].strip()

    cleaned = re.sub(r'[,\.\-–—\s]+$', '', cleaned).strip()
    return cleaned if len(cleaned) >= 2 else title


def search_shopify_brand_store(
    brand: str,
    model_name: str,
    domain: str,
    qualifier_tokens: List[str],
    exclude_urls: Optional[Set[str]] = None,
    timeout: int = 15,
    category: Optional[str] = None,
    out_diagnostics: Optional[Dict[str, Any]] = None,
    target_capacity: Optional[str] = None
) -> Optional[str]:
    """
    Tier 1 & Tier 2 Shopify Discovery:
    Scores all candidates from suggest API and full catalogue against Model_Name.
    Prefers exact title matches, penalizes extra tokens, and ignores exclude_urls.
    Falls back to TinyFish Fetch and TinyFish Search when direct requests are blocked.
    """
    exclude = {u.strip().rstrip("/").lower() for u in (exclude_urls or set()) if u}
    candidates = []
    seen_urls = set()

    # 1. Direct Suggest API
    suggest_cache_key = f"shopify_sug_{domain}_{model_name}"

    def _fetch_sug():
        sug_url = f"https://www.{domain}/search/suggest.json?q={quote_plus(model_name)}&resources[type]=product"
        try:
            r = requests.get(sug_url, headers=DEFAULT_HEADERS, timeout=timeout)
            is_blocked, vendor, reason = detect_waf_block(r.status_code, dict(r.headers), r.text, sug_url)
            if is_blocked or r.status_code != 200:
                from src.utils.tinyfish import is_tinyfish_configured, tinyfish_fetch
                if is_tinyfish_configured():
                    logger.info(f"[Shopify Suggest] Direct suggest blocked ({vendor or r.status_code}). Retrying via TinyFish Fetch: {sug_url}")
                    tf_res = tinyfish_fetch(sug_url)
                    if tf_res and tf_res.get("text"):
                        try:
                            s_data = json.loads(tf_res["text"])
                            return s_data.get("resources", {}).get("results", {}).get("products", [])
                        except Exception:
                            pass
                return []
            if r.status_code == 200:
                return r.json().get("resources", {}).get("results", {}).get("products", [])
        except Exception as e:
            logger.debug(f"Shopify suggest API error for {model_name} on {domain}: {e}")
        return []

    suggest_products = get_cached_json(suggest_cache_key, _fetch_sug) or []
    for p in suggest_products:
        p_title = p.get("title", "")
        p_url = p.get("url", "").split("?")[0]
        full_url = urljoin(f"https://www.{domain}", p_url)
        clean_full = full_url.rstrip("/").lower()
        if clean_full in exclude or clean_full in seen_urls:
            continue

        score, is_valid, diag = score_candidate_match(
            model_name, p_title, full_url, brand=brand, qualifier_tokens=qualifier_tokens, category=category, target_capacity=target_capacity
        )
        if is_valid and score > 0:
            candidates.append({"url": full_url, "title": p_title, "score": score, "source": "suggest"})
            seen_urls.add(clean_full)
        elif out_diagnostics is not None:
            target_clean = extract_model_name_portion(model_name, brand=brand)
            target_toks = normalize_model_tokens(target_clean) - {"pb", brand.lower() if brand else "", "powerbank", "power", "bank", "portable", "charger", "earbuds", "headphones", "tws", "wireless"}
            cand_toks = normalize_model_tokens(p_title)
            if target_toks and target_toks.issubset(cand_toks):
                cand_display = extract_clean_candidate_model_name(p_title, brand=brand)
                if "ambiguous_candidates" not in out_diagnostics:
                    out_diagnostics["ambiguous_candidates"] = []
                if cand_display not in out_diagnostics["ambiguous_candidates"]:
                    out_diagnostics["ambiguous_candidates"].append(cand_display)

    # 2. Local matching against full catalogue
    catalogue = fetch_shopify_catalogue(domain, timeout=timeout)
    for p in catalogue:
        title = p.get("title", "")
        handle = p.get("handle", "")
        full_url = f"https://www.{domain}/products/{handle}"
        clean_full = full_url.rstrip("/").lower()
        if clean_full in exclude or clean_full in seen_urls:
            continue

        score, is_valid, diag = score_candidate_match(
            model_name, title, full_url, brand=brand, qualifier_tokens=qualifier_tokens, category=category, target_capacity=target_capacity
        )
        if is_valid and score > 0:
            candidates.append({"url": full_url, "title": title, "score": score, "source": "catalogue"})
            seen_urls.add(clean_full)
        elif out_diagnostics is not None:
            target_clean = extract_model_name_portion(model_name, brand=brand)
            target_toks = normalize_model_tokens(target_clean) - {"pb", brand.lower() if brand else "", "powerbank", "power", "bank", "portable", "charger", "earbuds", "headphones", "tws", "wireless"}
            cand_toks = normalize_model_tokens(title)
            if target_toks and target_toks.issubset(cand_toks):
                cand_display = extract_clean_candidate_model_name(title, brand=brand)
                if "ambiguous_candidates" not in out_diagnostics:
                    out_diagnostics["ambiguous_candidates"] = []
                if cand_display not in out_diagnostics["ambiguous_candidates"]:
                    out_diagnostics["ambiguous_candidates"].append(cand_display)

    # 3. Secondary fallback: TinyFish Search API for candidate product discovery
    if not candidates:
        from src.utils.tinyfish import is_tinyfish_configured, tinyfish_search
        if is_tinyfish_configured():
            clean_dom = domain.replace("www.", "").lower().strip()
            queries = [
                f"{brand} {model_name} site:{clean_dom}",
                f"{brand} {model_name} {clean_dom}"
            ]
            for tf_q in queries:
                tf_items = tinyfish_search(tf_q, limit=6)
                for it in tf_items:
                    cand_title = it.get("title", "")
                    cand_url = it.get("url", "")
                    if not cand_url or clean_dom not in cand_url.lower():
                        continue
                    clean_full = cand_url.rstrip("/").lower()
                    if clean_full in exclude or clean_full in seen_urls:
                        continue
                    score, is_valid, diag = score_candidate_match(
                        model_name, cand_title, cand_url, brand=brand, qualifier_tokens=qualifier_tokens, category=category, target_capacity=target_capacity
                    )
                    if is_valid and score > 0:
                        candidates.append({"url": cand_url, "title": cand_title, "score": score, "source": "tinyfish_search"})
                        seen_urls.add(clean_full)
                    elif out_diagnostics is not None:
                        target_clean = extract_model_name_portion(model_name, brand=brand)
                        target_toks = normalize_model_tokens(target_clean) - {"pb", brand.lower() if brand else "", "powerbank", "power", "bank", "portable", "charger", "earbuds", "headphones", "tws", "wireless"}
                        cand_toks = normalize_model_tokens(cand_title)
                        if target_toks and target_toks.issubset(cand_toks):
                            cand_display = extract_clean_candidate_model_name(cand_title, brand=brand)
                            if "ambiguous_candidates" not in out_diagnostics:
                                out_diagnostics["ambiguous_candidates"] = []
                            if cand_display not in out_diagnostics["ambiguous_candidates"]:
                                out_diagnostics["ambiguous_candidates"].append(cand_display)
                if candidates:
                    break

    if candidates:
        candidates.sort(key=lambda c: c["score"], reverse=True)
        best = candidates[0]
        logger.info(f"[Shopify Direct] Best candidate match for '{model_name}' (score={best['score']:.1f}, {best['source']}): {best['title']} -> {best['url']}")
        return best["url"]

    return None


def search_retail_reliance(
    brand: str,
    model_name: str,
    qualifier_tokens: List[str],
    exclude_urls: Optional[Set[str]] = None,
    timeout: int = 15,
    category: Optional[str] = None,
    out_diagnostics: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """
    Tier 3 Reliance Digital Discovery:
    Uses Reliance Digital's direct JSON autocomplete endpoint with candidate scoring.
    """
    exclude = {u.strip().rstrip("/").lower() for u in (exclude_urls or set()) if u}
    query = f"{brand} {model_name}".strip()
    cache_key = f"reliance_sug_{query}"

    def _fetch_rd():
        url = f"https://www.reliancedigital.in/ext/search/application/api/v1.0/auto-complete?q={quote_plus(query)}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*"
        }
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            if r.status_code == 200:
                return r.json().get("items", [])
        except Exception as e:
            logger.warning(f"Reliance Digital API error for '{query}': {e}")
        return []

    items = get_cached_json(cache_key, _fetch_rd) or []
    candidates = []
    seen_urls = set()

    for it in items:
        if it.get("type") != "product":
            continue
        display = it.get("display", "")
        slug_list = it.get("action", {}).get("page", {}).get("params", {}).get("slug", [])
        slug = slug_list[0] if slug_list else ""
        if not slug:
            continue

        product_url = f"https://www.reliancedigital.in/product/{slug}"
        clean_full = product_url.rstrip("/").lower()
        if clean_full in exclude or clean_full in seen_urls:
            continue

        score, is_valid, diag = score_candidate_match(
            model_name, display, product_url, brand=brand, qualifier_tokens=qualifier_tokens, category=category
        )
        if is_valid and score > 0:
            candidates.append({"url": product_url, "title": display, "score": score})
            seen_urls.add(clean_full)
        elif out_diagnostics is not None:
            target_clean = extract_model_name_portion(model_name, brand=brand)
            target_toks = normalize_model_tokens(target_clean) - {"pb", brand.lower() if brand else "", "powerbank", "power", "bank", "portable", "charger", "earbuds", "headphones", "tws", "wireless"}
            cand_toks = normalize_model_tokens(display)
            if target_toks and target_toks.issubset(cand_toks):
                cand_display = extract_clean_candidate_model_name(display, brand=brand)
                if "ambiguous_candidates" not in out_diagnostics:
                    out_diagnostics["ambiguous_candidates"] = []
                if cand_display not in out_diagnostics["ambiguous_candidates"]:
                    out_diagnostics["ambiguous_candidates"].append(cand_display)

    if candidates:
        candidates.sort(key=lambda c: c["score"], reverse=True)
        best = candidates[0]
        logger.info(f"[Reliance Direct] Best candidate match for '{model_name}' (score={best['score']:.1f}): {best['title']} -> {best['url']}")
        return best["url"]

    return None


def search_retail_croma(
    brand: str,
    model_name: str,
    qualifier_tokens: List[str],
    exclude_urls: Optional[Set[str]] = None,
    timeout: int = 15,
    category: Optional[str] = None,
    out_diagnostics: Optional[Dict[str, Any]] = None
) -> Optional[str]:
    """
    Tier 3 Croma Discovery:
    Uses Croma's OCC product search REST endpoint with candidate scoring.
    """
    exclude = {u.strip().rstrip("/").lower() for u in (exclude_urls or set()) if u}
    query = f"{brand} {model_name}".strip()
    cache_key = f"croma_occ_{query}"

    def _fetch_croma():
        url = f"https://www.croma.com/rest/v2/croma/products/search?query={quote_plus(query)}&fields=FULL&pageSize=10"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "application/json"
        }
        try:
            r = requests.get(url, headers=headers, timeout=timeout)
            is_blocked, vendor, reason = detect_waf_block(r.status_code, dict(r.headers), r.text, url)
            if is_blocked or r.status_code == 403:
                logger.warning(f"[Croma Direct] WAF / Bot protection detected ({vendor or 'Akamai'}: {reason or 'HTTP 403'}). Marking Croma blocked for this call and falling through to Reliance Digital.")
                return []
            if r.status_code == 200 and "json" in r.headers.get("Content-Type", ""):
                return r.json().get("products", [])
            else:
                logger.debug(f"Croma OCC search returned status {r.status_code} for '{query}'")
        except Exception as e:
            logger.debug(f"Croma OCC search error for '{query}': {e}")
        return []

    products = get_cached_json(cache_key, _fetch_croma) or []
    candidates = []
    seen_urls = set()

    for p in products:
        name = p.get("name", "")
        url_path = p.get("url", "")
        if not url_path:
            continue
        full_url = urljoin("https://www.croma.com", url_path)
        clean_full = full_url.rstrip("/").lower()
        if clean_full in exclude or clean_full in seen_urls:
            continue

        score, is_valid, diag = score_candidate_match(
            model_name, name, full_url, brand=brand, qualifier_tokens=qualifier_tokens, category=category
        )
        if is_valid and score > 0:
            candidates.append({"url": full_url, "title": name, "score": score})
            seen_urls.add(clean_full)
        elif out_diagnostics is not None:
            target_clean = extract_model_name_portion(model_name, brand=brand)
            target_toks = normalize_model_tokens(target_clean) - {"pb", brand.lower() if brand else "", "powerbank", "power", "bank", "portable", "charger", "earbuds", "headphones", "tws", "wireless"}
            cand_toks = normalize_model_tokens(name)
            if target_toks and target_toks.issubset(cand_toks):
                cand_display = extract_clean_candidate_model_name(name, brand=brand)
                if "ambiguous_candidates" not in out_diagnostics:
                    out_diagnostics["ambiguous_candidates"] = []
                if cand_display not in out_diagnostics["ambiguous_candidates"]:
                    out_diagnostics["ambiguous_candidates"].append(cand_display)

    if candidates:
        candidates.sort(key=lambda c: c["score"], reverse=True)
        best = candidates[0]
        logger.info(f"[Croma Direct] Best candidate match for '{model_name}' (score={best['score']:.1f}): {best['title']} -> {best['url']}")
        return best["url"]

    return None


def fetch_and_parse_url(
    url: str,
    tier: int = 1,
    timeout: int = 15,
    platform: Optional[str] = None,
    category: Optional[str] = None
) -> ParserResult:
    """
    Fetches a URL, dispatches to the appropriate parser, and returns ParserResult.
    Detects 401/403/429 (Blocked), 404/410/Soft-404 (Delisted), and extracts specs/images.
    Retries blocked URLs via TinyFish Fetch fallback.
    Hard timeout of 15s enforced.
    """
    logger.info(f"[Tier {tier}] Fetching URL: {url} (platform={platform or 'auto'}, category={category or 'powerbank'})")
    parser = get_parser_for_url(url, platform=platform)
    
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        is_blocked, vendor, reason = detect_waf_block(response.status_code, dict(response.headers), response.text, url)
        if is_blocked:
            logger.warning(f"[Tier {tier}] WAF / Bot protection detected on {url} ({vendor}: {reason})")
            from src.utils.tinyfish import is_tinyfish_configured, tinyfish_fetch, parse_tinyfish_markdown_to_result
            if is_tinyfish_configured():
                logger.info(f"[Tier {tier}] Retrying blocked URL via TinyFish Fetch: {url}")
                tf_res = tinyfish_fetch(url)
                if tf_res and tf_res.get("text"):
                    parsed_res = parse_tinyfish_markdown_to_result(tf_res, url=url, tier=tier, category=category)
                    if parsed_res.success:
                        logger.info(f"[Tier {tier}] TinyFish Fetch succeeded on blocked URL {url} with {len(parsed_res.specs)} specs")
                        return parsed_res

            res = parser.parse(url=url, html=response.text, status_code=response.status_code, tier=tier, category=category)
            res.is_blocked = True
            res.error = f"Blocked: {vendor} ({reason})"
            return res
        return parser.parse(url=url, html=response.text, status_code=response.status_code, tier=tier, category=category)
    except requests.exceptions.Timeout:
        logger.warning(f"[Tier {tier}] Timeout ({timeout}s) fetching {url}")
        from src.utils.tinyfish import is_tinyfish_configured, tinyfish_fetch, parse_tinyfish_markdown_to_result
        if is_tinyfish_configured():
            logger.info(f"[Tier {tier}] Timeout on direct fetch. Retrying via TinyFish Fetch: {url}")
            tf_res = tinyfish_fetch(url)
            if tf_res and tf_res.get("text"):
                parsed_res = parse_tinyfish_markdown_to_result(tf_res, url=url, tier=tier, category=category)
                if parsed_res.success:
                    return parsed_res
        return ParserResult(success=False, status_code=0, url=url, tier=tier, error="Connection timeout")
    except requests.exceptions.RequestException as e:
        logger.error(f"[Tier {tier}] Request error fetching {url}: {e}")
        from src.utils.tinyfish import is_tinyfish_configured, tinyfish_fetch, parse_tinyfish_markdown_to_result
        if is_tinyfish_configured():
            tf_res = tinyfish_fetch(url)
            if tf_res and tf_res.get("text"):
                parsed_res = parse_tinyfish_markdown_to_result(tf_res, url=url, tier=tier, category=category)
                if parsed_res.success:
                    return parsed_res
        return ParserResult(success=False, status_code=0, url=url, tier=tier, error=str(e))


def extract_text_from_pdf(pdf_path: str) -> Dict[str, Any]:
    """Extracts text from a local PDF brochure using pdfplumber."""
    logger.info(f"Extracting text from PDF brochure: {pdf_path}")
    if not os.path.exists(pdf_path):
        return {"path": pdf_path, "text": "", "error": "File not found"}

    try:
        pages_text = []
        with pdfplumber.open(pdf_path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text()
                if text:
                    pages_text.append(text)
        full_text = "\n\n".join(pages_text)
        return {"path": pdf_path, "text": full_text, "error": None}
    except Exception as e:
        logger.error(f"Error reading PDF {pdf_path}: {e}")
        return {"path": pdf_path, "text": "", "error": str(e)}

