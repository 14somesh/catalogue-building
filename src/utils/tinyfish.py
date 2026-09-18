import os
import re
import json
import logging
from typing import Dict, Any, List, Optional, Tuple, Set
from urllib.parse import urlparse
import requests
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("tinyfish")

FETCH_ENDPOINT = "https://api.fetch.tinyfish.ai"
SEARCH_ENDPOINT = "https://api.search.tinyfish.ai"


def get_tinyfish_api_key() -> Optional[str]:
    """Retrieves TINYFISH_API_KEY from environment."""
    key = os.getenv("TINYFISH_API_KEY")
    if key and key.strip():
        return key.strip()
    return None


def is_tinyfish_configured() -> bool:
    """Returns True if TINYFISH_API_KEY is configured."""
    return get_tinyfish_api_key() is not None


def tinyfish_fetch(
    url: str,
    timeout: int = 30,
    purpose: str = "Extract product catalog and specifications"
) -> Optional[Dict[str, Any]]:
    """
    Fetches a web page through TinyFish Fetch API (anti-bot / WAF bypass).
    Returns dict with keys: url, final_url, title, text, status_code, headers
    or None if fetch fails or API key is not configured.
    """
    api_key = get_tinyfish_api_key()
    if not api_key:
        logger.debug("TinyFish API key not configured; skipping tinyfish_fetch.")
        return None

    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json"
    }
    payload = {
        "urls": [url],
        "format": "markdown",
        "purpose": purpose
    }

    try:
        r = requests.post(FETCH_ENDPOINT, headers=headers, json=payload, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            results = data.get("results", [])
            if results and isinstance(results, list):
                item = results[0]
                text_content = item.get("text") or item.get("markdown") or ""
                return {
                    "url": item.get("url", url),
                    "final_url": item.get("final_url", url),
                    "title": item.get("title", ""),
                    "description": item.get("description", ""),
                    "text": text_content,
                    "status_code": 200,
                    "headers": dict(r.headers)
                }
            logger.warning(f"TinyFish fetch returned no results in payload for {url}")
        else:
            logger.warning(f"TinyFish fetch failed for {url} with HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"TinyFish fetch exception for {url}: {e}")

    return None


def tinyfish_search(
    query: str,
    location: str = "IN",
    limit: int = 10,
    timeout: int = 15
) -> List[Dict[str, Any]]:
    """
    Performs search via TinyFish Search API.
    Returns list of result dicts: [{"title": ..., "url": ..., "snippet": ..., "position": ...}]
    """
    api_key = get_tinyfish_api_key()
    if not api_key:
        logger.debug("TinyFish API key not configured; skipping tinyfish_search.")
        return []

    headers = {
        "X-API-Key": api_key
    }
    params = {
        "query": query,
        "location": location
    }

    try:
        r = requests.get(SEARCH_ENDPOINT, headers=headers, params=params, timeout=timeout)
        if r.status_code == 200:
            data = r.json()
            results = data.get("results", [])
            if isinstance(results, list):
                return results[:limit]
        else:
            logger.warning(f"TinyFish search failed for '{query}' with HTTP {r.status_code}: {r.text[:200]}")
    except Exception as e:
        logger.error(f"TinyFish search exception for '{query}': {e}")

    return []


def search_brand_domains_via_tinyfish(brand: str, location: str = "IN") -> List[str]:
    """
    Uses TinyFish search to discover candidate official domains for a brand.
    Extracts netloc/domain from search results, excluding major market aggregates.
    """
    clean_brand = brand.strip()
    if not clean_brand:
        return []

    queries = [
        f"{clean_brand} official store India",
        f"{clean_brand} official website India"
    ]

    excluded_domains = {
        "amazon.in", "amazon.com", "flipkart.com", "reliancedigital.in", "croma.com",
        "indiamart.com", "wikipedia.org", "en.wikipedia.org", "youtube.com", "facebook.com",
        "instagram.com", "twitter.com", "linkedin.com", "tatacliq.com", "myntra.com",
        "gadgets360.com", "91mobiles.com", "smartprix.com", "mysmartprice.com", "eci.gov.in"
    }

    candidate_domains = []
    seen = set()

    for q in queries:
        results = tinyfish_search(q, location=location, limit=8)
        for res in results:
            u = res.get("url", "")
            if not u:
                continue
            parsed = urlparse(u)
            dom = parsed.netloc.replace("www.", "").lower().strip()
            if dom and dom not in seen and dom not in excluded_domains:
                seen.add(dom)
                candidate_domains.append(dom)

    return candidate_domains


def parse_tinyfish_markdown_to_result(
    fetch_res: Dict[str, Any],
    url: str,
    tier: int = 1,
    category: Optional[str] = None
) -> Any:
    """
    Converts TinyFish markdown response into a standard ParserResult.
    Deterministic regex-based specification, price, title, and image extraction.
    """
    from src.parsers.base import ParserResult
    from src.utils.category_specs import extract_category_specs
    from src.parsers.brochure import parse_specs_from_text

    text = fetch_res.get("text", "")
    title = fetch_res.get("title", "")
    if not title:
        # Try extracting from first markdown header
        h1_m = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        if h1_m:
            title = h1_m.group(1).strip()

    # 1. Specs extraction
    # First use category-specific extractor
    specs = extract_category_specs(text, category=category)
    # Merge with general brochure spec regexes for powerbank / audio keys if missing
    brochure_specs = parse_specs_from_text(text)
    for k, v in brochure_specs.items():
        if k not in specs or not specs[k]:
            specs[k] = v

    # 2. MRP / Price extraction
    mrp: Optional[float] = None
    price_patterns = [
        r"(?:regular\s+price|mrp|price)\s*[:\-]?\s*(?:[₹]|rs\.?|inr)?\s*([0-9,]+(?:\.[0-9]{2})?)",
        r"(?:[₹]|rs\.?|inr)\s*([0-9,]+(?:\.[0-9]{2})?)"
    ]
    for pat in price_patterns:
        pm = re.search(pat, text, re.I)
        if pm:
            try:
                val_str = pm.group(1).replace(",", "")
                val_f = float(val_str)
                if 100 <= val_f <= 200000:
                    mrp = val_f
                    break
            except ValueError:
                pass

    # 3. Image URLs extraction (from markdown image tags or raw image URLs)
    candidate_images: List[str] = []
    # Markdown image tags: ![alt](url)
    md_imgs = re.findall(r'!\[.*?\]\((https?://[^\s\)]+)\)', text)
    for img_u in md_imgs:
        if not any(ign in img_u.lower() for ign in ["logo", "icon", "badge", "payment", "star", "flag", "banner"]):
            if img_u not in candidate_images:
                candidate_images.append(img_u)

    # Also look for raw image URLs in text
    raw_imgs = re.findall(r'https?://[^\s\'"<>]+?\.(?:jpg|jpeg|png|webp)', text, re.I)
    for img_u in raw_imgs:
        if not any(ign in img_u.lower() for ign in ["logo", "icon", "badge", "payment", "star", "flag", "banner"]):
            if img_u not in candidate_images:
                candidate_images.append(img_u)

    field_sources = {k: url for k in specs}
    field_tiers = {k: tier for k in specs}
    if mrp:
        field_sources["mrp"] = url
        field_tiers["mrp"] = tier

    return ParserResult(
        success=(len(specs) >= 1 or len(candidate_images) > 0),
        status_code=200,
        url=url,
        title=title,
        description_text=text[:1000],
        specs=specs,
        mrp=mrp,
        image_urls=candidate_images,
        field_sources=field_sources,
        field_tiers=field_tiers,
        tier=tier
    )
