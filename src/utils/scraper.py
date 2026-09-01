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
    """Normalizes text by removing commas in numbers (e.g. '10,000' -> '10000'), treating '+' as 'plus', equating 'magtag'/'magsafe', splitting alphanumeric boundaries, and extracting alphanumeric tokens."""
    # Strip commas between digits
    t = re.sub(r'(\d+),(\d+)', r'\1\2', text)
    t = re.sub(r'\+', ' plus ', t)
    t = re.sub(r'mag\s*tag|magtag|mag\s*safe|magsafe', ' magsafe ', t, flags=re.IGNORECASE)
    t = re.sub(r'([a-zA-Z]+)(\d+)', r'\1 \2', t)
    t = re.sub(r'(\d+)([a-zA-Z]+)', r'\1 \2', t)
    words = re.findall(r'[a-zA-Z0-9]+', t.lower())
    return set(words)


def extract_model_name_portion(title: str, brand: str = "") -> str:
    """
    Extracts the core model name portion of a candidate title string before capacity,
    wattage, or descriptive category keywords. Handles multiple leading capacities/wattages cleanly.
    """
    cleaned = title
    if brand:
        cleaned = re.sub(rf'^{re.escape(brand)}(?:one|\s+one|-one)?\s*', '', cleaned, flags=re.IGNORECASE)
    
    # Strip ALL leading capacities and wattages (e.g. '10000 mAh 15 W ...')
    cleaned = re.sub(r'^(?:\s*(?:\d+(?:,\d+)?\s*mah|\d+k\b|\d+(?:\.\d+)?\s*w\b))+\s*', '', cleaned, flags=re.IGNORECASE).strip()
    
    # Split on capacity, wattage, or generic category keywords
    split_pattern = r'\b(?:\d+(?:,\d+)?\s*mah|\d+k\b|\d+(?:\.\d+)?\s*w\b|power\s*bank|powerbank|charger|with\s+built|with\s+type|with\s+stand|made\s+in)\b'
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
    qualifier_tokens: Optional[List[str]] = None
) -> Tuple[float, bool, str]:
    """
    BIDIRECTIONAL CANDIDATE MATCHER & SCORER:
    1. Requires all target model tokens to be present in candidate (subset check).
    2. Enforces qualifier token mismatch checks.
    3. Rewards exact title / model portion / slug match.
    4. Strongly penalizes candidates with extra model-name tokens not in target.
    Returns (score, is_valid, diagnostic_reason).
    """
    brand_lower = brand.lower() if brand else ""
    stopwords = {"pb", brand_lower, "powerbank", "power", "bank", "portable", "charger", "fast", "charging", "series", "the", "with", "and", "in", "for"}
    
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

    if not target_tokens.issubset(cand_all_tokens) and not primary_target_tokens.issubset(cand_all_tokens):
        return -1.0, False, "Target tokens missing in candidate"
        
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
    extra_model_tokens = (cand_model_tokens - target_tokens) - qualifiers_norm
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
    
    split_pattern = r'\b(?:\d+(?:,\d+)?\s*mah|\d+k\b|\d+(?:\.\d+)?\s*w\b|power\s*bank|powerbank|charger|with\s+built|with\s+type|with\s+stand|made\s+in)\b'
    m = re.search(split_pattern, cleaned, flags=re.IGNORECASE)
    if m:
        leading_part = cleaned[:m.start()].strip()
        if leading_part:
            return leading_part
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
    LEADING MODEL NAME SEGMENT of the candidate title (before capacity, wattage,
    or descriptor words). Also treats '+' and 'Plus' as equivalent tokens.
    Returns (is_valid, rejection_reason).
    """
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
    exclude_urls: Optional[Set[str]] = None,
    timeout: int = 15
) -> Optional[str]:
    """
    Tier 1 & Tier 2 Shopify Discovery:
    Scores all candidates from suggest API and full catalogue against Model_Name.
    Prefers exact title matches, penalizes extra tokens, and ignores exclude_urls.
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
            model_name, p_title, full_url, brand=brand, qualifier_tokens=qualifier_tokens
        )
        if is_valid and score > 0:
            candidates.append({"url": full_url, "title": p_title, "score": score, "source": "suggest"})
            seen_urls.add(clean_full)

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
            model_name, title, full_url, brand=brand, qualifier_tokens=qualifier_tokens
        )
        if is_valid and score > 0:
            candidates.append({"url": full_url, "title": title, "score": score, "source": "catalogue"})
            seen_urls.add(clean_full)

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
    timeout: int = 15
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
            model_name, display, product_url, brand=brand, qualifier_tokens=qualifier_tokens
        )
        if is_valid and score > 0:
            candidates.append({"url": product_url, "title": display, "score": score})
            seen_urls.add(clean_full)

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
    timeout: int = 15
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
            model_name, name, full_url, brand=brand, qualifier_tokens=qualifier_tokens
        )
        if is_valid and score > 0:
            candidates.append({"url": full_url, "title": name, "score": score})
            seen_urls.add(clean_full)

    if candidates:
        candidates.sort(key=lambda c: c["score"], reverse=True)
        best = candidates[0]
        logger.info(f"[Croma Direct] Best candidate match for '{model_name}' (score={best['score']:.1f}): {best['title']} -> {best['url']}")
        return best["url"]

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

