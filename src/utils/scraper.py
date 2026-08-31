import os
import re
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


def fetch_and_parse_url(url: str, tier: int = 1, timeout: int = 15) -> ParserResult:
    """
    Fetches a URL, dispatches to the appropriate parser, and returns ParserResult.
    Detects 401/403/429 (Blocked), 404/410/Soft-404 (Delisted), and extracts specs/images.
    """
    logger.info(f"[Tier {tier}] Fetching URL: {url}")
    parser = get_parser_for_url(url)
    
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        return parser.parse(url=url, html=response.text, status_code=response.status_code, tier=tier)
    except requests.exceptions.Timeout:
        logger.warning(f"[Tier {tier}] Timeout fetching {url}")
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
