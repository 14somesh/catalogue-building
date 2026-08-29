import os
import re
import requests
from bs4 import BeautifulSoup
from typing import Optional, Dict, Any, List, Tuple
from urllib.parse import urlparse, quote_plus, urljoin
import pdfplumber

from src.utils.logger import setup_logger

logger = setup_logger("scraper")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def clean_html_to_text(html_content: str) -> Tuple[str, Optional[str]]:
    """
    Extracts readable text and prominent product image from HTML content.
    Returns (cleaned_text, image_url).
    """
    soup = BeautifulSoup(html_content, "html.parser")

    # Extract og:image or high-res product image
    image_url = None
    og_img = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
    if og_img and og_img.get("content"):
        image_url = og_img["content"].strip()
        if image_url.startswith("//"):
            image_url = "https:" + image_url

    # Remove script, style, navigation, footer tags
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "svg"]):
        tag.decompose()

    # Get clean text
    text = soup.get_text(separator="\n", strip=True)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    cleaned_text = "\n".join(lines[:350])  # limit to top relevant lines for LLM prompt
    return cleaned_text, image_url


def scrape_web_page(url: str, timeout: int = 15) -> Dict[str, Any]:
    """
    Fetches a web page and extracts clean text and metadata.
    """
    logger.info(f"Scraping web page: {url}")
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        if response.status_code != 200:
            logger.warning(f"Failed to fetch {url}: HTTP {response.status_code}")
            return {"url": url, "text": "", "image_url": None, "status_code": response.status_code, "error": f"HTTP {response.status_code}"}

        text, image_url = clean_html_to_text(response.text)
        return {
            "url": url,
            "text": text,
            "image_url": image_url,
            "status_code": 200,
            "error": None
        }
    except Exception as e:
        logger.error(f"Error scraping {url}: {e}")
        return {"url": url, "text": "", "image_url": None, "status_code": 0, "error": str(e)}


def extract_text_from_pdf(pdf_path: str) -> Dict[str, Any]:
    """
    Extracts text from a local PDF brochure using pdfplumber.
    """
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


def search_brand_store(brand: str, model_name: str, domain: str) -> Optional[str]:
    """
    Directly searches the brand's official store search API (Shopify suggest API or store search).
    Validates that the returned product title actually matches the query keywords to avoid false positives.
    """
    # Extract significant keywords from model_name (ignore generic words like powerbank, mah, w, etc.)
    stopwords = {"powerbank", "power", "bank", "portable", "charger", "fast", "charging", "series", "the", "with", "and"}
    words = [w.lower() for w in re.findall(r"[a-zA-Z0-9]+", model_name) if len(w) > 1]
    distinctive_terms = [w for w in words if w not in stopwords]
    if not distinctive_terms:
        distinctive_terms = words

    urls_to_try = [
        f"https://{domain}/search/suggest.json?q={quote_plus(model_name)}&resources[type]=product",
        f"https://{domain}/search/suggest.json?q={quote_plus(' '.join(distinctive_terms))}&resources[type]=product",
        f"https://www.{domain}/search/suggest.json?q={quote_plus(model_name)}&resources[type]=product"
    ]

    for api_url in urls_to_try:
        try:
            resp = requests.get(api_url, headers=DEFAULT_HEADERS, timeout=8)
            if resp.status_code == 200 and "application/json" in resp.headers.get("Content-Type", ""):
                data = resp.json()
                products = data.get("resources", {}).get("results", {}).get("products", [])
                
                # Check each returned product for clear name match
                for prod in products:
                    prod_title = str(prod.get("title", "")).lower()
                    prod_handle = str(prod.get("handle", "")).lower()
                    
                    # Verify that distinctive terms (e.g. 'omni', 'go') appear in title or handle
                    matches = sum(1 for term in distinctive_terms if term in prod_title or term in prod_handle)
                    match_ratio = matches / len(distinctive_terms) if distinctive_terms else 0
                    
                    if match_ratio >= 0.6 or (len(distinctive_terms) == 1 and matches == 1):
                        product_url = prod.get("url")
                        if product_url:
                            if not product_url.startswith("http"):
                                base = f"https://www.{domain}" if not domain.startswith("www.") else f"https://{domain}"
                                product_url = urljoin(base, product_url)
                            product_url = product_url.split("?")[0]
                            logger.info(f"Verified brand store match for '{model_name}': {prod.get('title')} -> {product_url}")
                            return product_url
                    else:
                        logger.debug(f"Rejecting loose match: '{prod.get('title')}' for query '{model_name}' (match ratio: {match_ratio:.2f})")
        except Exception as e:
            logger.debug(f"Store search attempt failed on {api_url}: {e}")

    logger.warning(f"No strict product name match found on {domain} for '{model_name}'")
    return None


def search_product_web(brand: str, model_name: str, config: dict) -> Tuple[Optional[str], str]:
    """
    Searches for a product page using:
    1. Direct Official Brand Store Search
    2. Fallback to web search
    Returns (discovered_url, source_type).
    """
    search_cfg = config.get("search", {})
    official_domains = search_cfg.get("official_domains", {})
    brand_key = brand.strip().lower()
    official_domain = official_domains.get(brand_key)

    # 1. Official brand store direct search
    if official_domain:
        logger.info(f"Checking official brand domain '{official_domain}' for '{model_name}'...")
        store_url = search_brand_store(brand, model_name, official_domain)
        if store_url:
            return store_url, f"brand-site ({official_domain})"

    # 2. Marketplace / Web fallback
    logger.info(f"Searching web/marketplaces for {brand} {model_name}...")
    queries = [
        f"https://www.amazon.in/s?k={quote_plus(f'{brand} {model_name} power bank')}",
        f"https://www.flipkart.com/search?q={quote_plus(f'{brand} {model_name} power bank')}"
    ]

    for q_url in queries:
        try:
            resp = requests.get(q_url, headers=DEFAULT_HEADERS, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                # Look for product link in Amazon
                if "amazon.in" in q_url:
                    for a in soup.find_all("a", href=True):
                        href = a["href"]
                        if "/dp/" in href or "/gp/product/" in href:
                            full_url = "https://www.amazon.in" + href.split("?")[0]
                            logger.info(f"Discovered Amazon listing: {full_url}")
                            return full_url, "marketplace (amazon.in)"
        except Exception as e:
            logger.debug(f"Marketplace query failed on {q_url}: {e}")

    return None, "none"
