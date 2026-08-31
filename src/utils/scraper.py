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


def load_brand_defaults(brand: str, config_path: str = "config/brand_defaults.yaml") -> dict:
    """Loads brand configuration defaults (collection URL, retail order, qualifier tokens)."""
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                brands_dict = data.get("brands", {})
                return brands_dict.get(brand, brands_dict.get("_default", {}))
        except Exception as e:
            logger.warning(f"Failed loading brand defaults from {config_path}: {e}")
    return {}


def normalize_model_tokens(text: str) -> Set[str]:
    """Normalizes text by treating '+' as 'plus' and extracting alphanumeric tokens."""
    t = re.sub(r'\+', ' plus ', text)
    words = re.findall(r'[a-zA-Z0-9]+', t.lower())
    return set(words)


def extract_model_name_portion(title: str, brand: str = "") -> str:
    """
    Extracts the leading model name portion of a candidate title string before capacity,
    wattage, or descriptive category keywords (powerbank, wired, wireless, portable, etc.).
    """
    cleaned = title
    if brand:
        cleaned = re.sub(rf'^{re.escape(brand)}\s+', '', cleaned, flags=re.IGNORECASE)
    
    # Split on first occurrence of capacity, wattage, or descriptor keywords
    split_pattern = r'\b(?:\d+(?:,\d+)?\s*mah|\d+(?:\.\d+)?\s*w|power\s*bank|powerbank|charger|wireless|wired|magnetic|magsafe|fast\s+charging|super\s+fast|smallest|slimmest|slim|pocket|portable|with|for)\b'
    m = re.search(split_pattern, cleaned, flags=re.IGNORECASE)
    if m:
        model_part = cleaned[:m.start()].strip()
        if model_part:
            return model_part
    return cleaned


def reject_qualifier_mismatch(
    target_model_name: str,
    candidate_title: str,
    qualifier_tokens: Optional[List[str]] = None,
    brand: str = ""
) -> Tuple[bool, Optional[str]]:
    """
    QUALIFIER TOKEN CHECK:
    When matching a candidate page to a row, only compares qualifier tokens in the
    MODEL NAME PORTION of the candidate title (the leading segment before capacity,
    wattage, or descriptor words). Also treats '+' and 'Plus' as equivalent tokens.
    Returns (is_valid, rejection_reason).
    """
    tokens = qualifier_tokens or DEFAULT_QUALIFIER_TOKENS
    target_words = normalize_model_tokens(target_model_name)
    
    # Extract only the model name portion of candidate title
    model_portion = extract_model_name_portion(candidate_title, brand=brand)
    candidate_model_words = normalize_model_tokens(model_portion)

    for token in tokens:
        token_lower = token.lower()
        if token_lower in candidate_model_words and token_lower not in target_words:
            reason = f"Qualifier token mismatch in model segment '{model_portion}': candidate contains '{token}' but target Model_Name '{target_model_name}' does not."
            logger.warning(f"Rejected candidate '{candidate_title}' for '{target_model_name}': {reason}")
            return False, reason

    return True, None


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
    """Fetches and caches the full Shopify products catalogue (up to 250 items)."""
    cache_key = f"shopify_cat_{domain}"

    def _fetch():
        url = f"https://www.{domain}/products.json?limit=250"
        try:
            r = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r.json().get("products", [])
        except Exception as e:
            logger.warning(f"Error fetching Shopify catalogue for {domain}: {e}")
        return []

    return get_cached_json(cache_key, _fetch) or []


def search_shopify_brand_store(
    brand: str,
    model_name: str,
    domain: str,
    qualifier_tokens: List[str],
    timeout: int = 15
) -> Optional[str]:
    """
    Tier 1 & Tier 2 Shopify Discovery:
    1. Direct suggest API: /search/suggest.json?q=<model>&resources[type]=product
    2. Fallback / Catalogue Match: /products.json?limit=250
    Resolves full URLs and handles tricky single-character names (e.g. '1#').
    """
    target_clean = model_name.strip().lower()
    stopwords = {"powerbank", "power", "bank", "portable", "charger", "fast", "charging", "series", "the", "with", "and"}
    model_tokens = normalize_model_tokens(extract_model_name_portion(model_name, brand=brand)) - stopwords

    # 1. Direct Suggest API
    suggest_cache_key = f"shopify_sug_{domain}_{model_name}"

    def _fetch_sug():
        sug_url = f"https://www.{domain}/search/suggest.json?q={quote_plus(model_name)}&resources[type]=product"
        try:
            r = requests.get(sug_url, headers=DEFAULT_HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r.json().get("resources", {}).get("results", {}).get("products", [])
        except Exception as e:
            logger.debug(f"Shopify suggest API error for {model_name} on {domain}: {e}")
        return []

    suggest_products = get_cached_json(suggest_cache_key, _fetch_sug) or []
    for p in suggest_products:
        p_title = p.get("title", "")
        p_url = p.get("url", "").split("?")[0]
        is_valid, _ = reject_qualifier_mismatch(model_name, p_title, qualifier_tokens, brand=brand)
        if not is_valid:
            continue

        cand_tokens = normalize_model_tokens(p_title + " " + p_url.replace("-", " "))
        if model_tokens and model_tokens.issubset(cand_tokens):
            full_url = urljoin(f"https://www.{domain}", p_url)
            logger.info(f"[Shopify Direct] Verified suggest match for '{model_name}': {p_title} -> {full_url}")
            return full_url

    # 2. Local matching against full catalogue
    catalogue = fetch_shopify_catalogue(domain, timeout=timeout)
    candidates = []
    for p in catalogue:
        title = p.get("title", "")
        handle = p.get("handle", "")
        is_valid, _ = reject_qualifier_mismatch(model_name, title, qualifier_tokens, brand=brand)
        if not is_valid:
            continue

        title_lower = title.lower()
        # Exact match or prefix match (e.g. '1#' matching '1# 22.5W 10000mAh Powerbank')
        if title_lower.startswith(target_clean + " ") or title_lower == target_clean or handle.startswith(target_clean + "-"):
            full_url = f"https://www.{domain}/products/{handle}"
            logger.info(f"[Shopify Direct] Exact catalogue match for '{model_name}': {title} -> {full_url}")
            return full_url

        cand_tokens = normalize_model_tokens(title + " " + handle.replace("-", " "))
        if model_tokens and model_tokens.issubset(cand_tokens):
            candidates.append((p, len(cand_tokens & model_tokens)))

    if candidates:
        best_p, _ = candidates[0]
        full_url = f"https://www.{domain}/products/{best_p['handle']}"
        logger.info(f"[Shopify Direct] Token catalogue match for '{model_name}': {best_p['title']} -> {full_url}")
        return full_url

    return None


def search_retail_reliance(
    brand: str,
    model_name: str,
    qualifier_tokens: List[str],
    timeout: int = 15
) -> Optional[str]:
    """
    Tier 3 Reliance Digital Discovery:
    Uses Reliance Digital's direct JSON autocomplete endpoint.
    Endpoint: https://www.reliancedigital.in/ext/search/application/api/v1.0/auto-complete?q=<term>
    """
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
    stopwords = {"powerbank", "power", "bank", "portable", "charger", "fast", "charging", "series", "the", "with", "and"}
    model_tokens = normalize_model_tokens(extract_model_name_portion(model_name, brand=brand)) - stopwords

    for it in items:
        if it.get("type") != "product":
            continue
        display = it.get("display", "")
        slug_list = it.get("action", {}).get("page", {}).get("params", {}).get("slug", [])
        slug = slug_list[0] if slug_list else ""
        if not slug:
            continue

        # Qualifier token check
        is_valid, _ = reject_qualifier_mismatch(model_name, display, qualifier_tokens, brand=brand)
        if not is_valid:
            continue

        cand_tokens = normalize_model_tokens(display + " " + slug.replace("-", " "))
        if model_tokens and model_tokens.issubset(cand_tokens):
            product_url = f"https://www.reliancedigital.in/product/{slug}"
            logger.info(f"[Reliance Direct] Found product match for '{model_name}': {display} -> {product_url}")
            return product_url

    return None


def search_retail_croma(
    brand: str,
    model_name: str,
    qualifier_tokens: List[str],
    timeout: int = 15
) -> Optional[str]:
    """
    Tier 3 Croma Discovery:
    Uses Croma's OCC product search REST endpoint.
    Endpoint: https://www.croma.com/rest/v2/croma/products/search?query=<term>&fields=FULL&pageSize=10
    """
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
            if r.status_code == 200 and "json" in r.headers.get("Content-Type", ""):
                return r.json().get("products", [])
            else:
                logger.debug(f"Croma OCC search returned status {r.status_code} for '{query}'")
        except Exception as e:
            logger.debug(f"Croma OCC search error for '{query}': {e}")
        return []

    products = get_cached_json(cache_key, _fetch_croma) or []
    stopwords = {"powerbank", "power", "bank", "portable", "charger", "fast", "charging", "series", "the", "with", "and"}
    model_tokens = normalize_model_tokens(extract_model_name_portion(model_name, brand=brand)) - stopwords

    for p in products:
        name = p.get("name", "")
        code = p.get("code", "")
        url_path = p.get("url", "")
        is_valid, _ = reject_qualifier_mismatch(model_name, name, qualifier_tokens, brand=brand)
        if not is_valid:
            continue

        cand_tokens = normalize_model_tokens(name + " " + url_path.replace("-", " "))
        if model_tokens and model_tokens.issubset(cand_tokens):
            full_url = urljoin("https://www.croma.com", url_path)
            logger.info(f"[Croma Direct] Found OCC product match for '{model_name}': {name} -> {full_url}")
            return full_url

    return None


def fetch_and_parse_url(url: str, tier: int = 1, timeout: int = 15) -> ParserResult:
    """
    Fetches a URL, dispatches to the appropriate parser, and returns ParserResult.
    Detects 401/403/429 (Blocked), 404/410/Soft-404 (Delisted), and extracts specs/images.
    Hard timeout of 15s enforced.
    """
    logger.info(f"[Tier {tier}] Fetching URL: {url}")
    parser = get_parser_for_url(url)
    
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        return parser.parse(url=url, html=response.text, status_code=response.status_code, tier=tier)
    except requests.exceptions.Timeout:
        logger.warning(f"[Tier {tier}] Timeout ({timeout}s) fetching {url}")
        return ParserResult(success=False, status_code=0, url=url, tier=tier, error="Connection timeout")
    except requests.exceptions.RequestException as e:
        logger.error(f"[Tier {tier}] Request error fetching {url}: {e}")
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

