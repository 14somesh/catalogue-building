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
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def clean_html_to_text(html_content: str) -> Tuple[str, Optional[str], bool]:
    """
    Extracts readable text, tech-spec accordion content, and prominent product image from HTML.
    Returns (cleaned_text, image_url, has_specs_flag).
    """
    soup = BeautifulSoup(html_content, "html.parser")

    # Extract og:image or high-res product image
    image_url = None
    og_img = soup.find("meta", property="og:image") or soup.find("meta", attrs={"name": "og:image"})
    if og_img and og_img.get("content"):
        image_url = og_img["content"].strip()
        if image_url.startswith("//"):
            image_url = "https:" + image_url

    # Check for soft 404 indicators
    title_str = soup.title.get_text(strip=True) if soup.title else ""
    if "404" in title_str or "page not found" in title_str.lower():
        return "", None, False

    # Extract structured spec blocks (accordions, tabs, tables, details)
    spec_chunks = []
    spec_elements = soup.find_all(
        ["details", "table", "div", "section", "ul"],
        class_=lambda c: c and any(k in str(c).lower() for k in ["spec", "accord", "tab", "feature", "desc", "detail", "product-single"])
    )
    for el in spec_elements:
        chunk_text = el.get_text(separator="\n", strip=True)
        if chunk_text and len(chunk_text) > 10:
            spec_chunks.append(chunk_text)

    # Remove script, style, navigation, footer tags
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "svg"]):
        tag.decompose()

    # Get clean general text
    main_text = soup.get_text(separator="\n", strip=True)
    all_lines = [line.strip() for line in (spec_chunks + [main_text]) if line.strip()]
    
    # Deduplicate lines while preserving order
    seen = set()
    deduped_lines = []
    for line in all_lines:
        if line not in seen:
            seen.add(line)
            deduped_lines.append(line)

    cleaned_text = "\n".join(deduped_lines[:450])

    # Guard: check if text contains actual product technical specifications
    spec_keywords = [r"\b\d+\s*mah\b", r"\b\d+\s*w\b", r"\bpd\b", r"\btype-c\b", r"\bwireless\b", r"\boutput\b", r"\binput\b", r"\bports\b", r"\bweight\b", r"\bdimensions\b"]
    has_specs = any(re.search(pat, cleaned_text, re.I) for pat in spec_keywords)

    if not has_specs:
        return "", None, False

    return cleaned_text, image_url, has_specs


def scrape_web_page(url: str, timeout: int = 15) -> Dict[str, Any]:
    """
    Fetches a web page and extracts clean text and metadata.
    Fails on non-200, soft 404, or pages without substantive technical specs.
    """
    logger.info(f"Scraping web page: {url}")
    try:
        response = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        if response.status_code != 200:
            logger.warning(f"Failed to fetch {url}: HTTP {response.status_code}")
            return {
                "url": url,
                "text": "",
                "image_url": None,
                "status_code": response.status_code,
                "has_specs": False,
                "error": f"HTTP {response.status_code}: Non-200 response"
            }

        text, image_url, has_specs = clean_html_to_text(response.text)
        
        if not text:
            return {
                "url": url,
                "text": "",
                "image_url": None,
                "status_code": 404,
                "has_specs": False,
                "error": "HTTP 404: Page not found (Soft 404)"
            }

        if not has_specs:
            logger.warning(f"Page at {url} yielded no technical specifications.")
            return {
                "url": url,
                "text": text,
                "image_url": image_url,
                "status_code": 200,
                "has_specs": False,
                "error": "No product specifications found on page"
            }

        return {
            "url": url,
            "text": text,
            "image_url": image_url,
            "status_code": 200,
            "has_specs": True,
            "error": None
        }
    except Exception as e:
        logger.error(f"Error scraping {url}: {e}")
        return {
            "url": url,
            "text": "",
            "image_url": None,
            "status_code": 0,
            "has_specs": False,
            "error": str(e)
        }


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
    stopwords = {"powerbank", "power", "bank", "portable", "charger", "fast", "charging", "series", "the", "with", "and"}
    words = [w.lower() for w in re.findall(r"[a-zA-Z0-9]+", model_name) if len(w) > 1]
    distinctive_terms = [w for w in words if w not in stopwords]

    try:
        suggest_url = f"https://www.{domain}/search/suggest.json?q={quote_plus(model_name)}&resources[type]=product"
        r = requests.get(suggest_url, headers=DEFAULT_HEADERS, timeout=8)
        if r.status_code == 200:
            data = r.json()
            products = data.get("resources", {}).get("results", {}).get("products", [])
            for p in products:
                prod_title = p.get("title", "").lower()
                prod_url = p.get("url", "")
                
                # Strict matching: query distinctive terms must match
                if distinctive_terms and all(term in prod_title for term in distinctive_terms):
                    full_url = urljoin(f"https://www.{domain}", prod_url.split("?")[0])
                    logger.info(f"Verified brand store match for '{model_name}': {p.get('title')} -> {full_url}")
                    return full_url
    except Exception as e:
        logger.debug(f"Store search suggest query failed for {domain}: {e}")

    return None


def search_product_web(brand: str, model_name: str, config: dict) -> Tuple[Optional[str], str]:
    """
    Finds authoritative product URLs with brand store prioritization.
    """
    brand_cfg = config.get("brand_domains", {})
    brand_domain = brand_cfg.get(brand.lower())

    if brand_domain:
        logger.info(f"Checking official brand domain '{brand_domain}' for '{model_name}'...")
        store_match = search_brand_store(brand, model_name, brand_domain)
        if store_match:
            return store_match, f"brand-site ({brand_domain})"

    logger.info(f"Searching web/marketplaces for {brand} {model_name}...")
    marketplaces = config.get("marketplace_domains", ["amazon.in", "flipkart.com", "reliancedigital.in", "croma.com"])
    
    query = f"{brand} {model_name} specification official"
    search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        r = requests.get(search_url, headers=DEFAULT_HEADERS, timeout=10)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            for link in soup.find_all("a", class_="result__url"):
                href = link.get("href", "")
                parsed = urlparse(href)
                domain = parsed.netloc.lower().replace("www.", "")
                
                if brand_domain and brand_domain in domain:
                    return href, f"brand-site ({brand_domain})"
                for mp in marketplaces:
                    if mp in domain:
                        return href, f"marketplace ({mp})"
    except Exception as e:
        logger.error(f"DuckDuckGo search failed: {e}")

    return None, ""
