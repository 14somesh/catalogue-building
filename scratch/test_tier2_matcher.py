import os
import sys
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import DEFAULT_HEADERS, reject_qualifier_mismatch

def find_product_on_collection_page(model_name: str, collection_url: str, qualifier_tokens: list) -> tuple:
    r = requests.get(collection_url, headers=DEFAULT_HEADERS, timeout=10)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    
    soup = BeautifulSoup(r.text, "html.parser")
    # Clean model search terms
    # Treat 1# as '1' or '1#'
    norm_target = model_name.lower().replace("+", "plus").strip()
    target_tokens = set(re.findall(r'[a-zA-Z0-9]+', norm_target))
    
    candidates = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/products/" in href:
            title = a.get_text().strip()
            # Title might be inside img alt or inner div
            if not title:
                img = a.find("img", alt=True)
                if img:
                    title = img["alt"].strip()
            if not title:
                # Use slug
                slug = href.split("/products/")[-1].split("?")[0].replace("-", " ")
                title = slug
                
            clean_url = urljoin(collection_url, href.split("?")[0])
            candidates.append((clean_url, title))

    # Deduplicate
    unique_candidates = list(dict(candidates).items())
    
    # Match candidate against model_name
    for prod_url, prod_title in unique_candidates:
        is_valid, _ = reject_qualifier_mismatch(model_name, prod_title, qualifier_tokens)
        if not is_valid:
            continue
            
        norm_title = prod_title.lower().replace("+", "plus")
        cand_tokens = set(re.findall(r'[a-zA-Z0-9]+', norm_title))
        
        # Check if target tokens are present
        if target_tokens and all(tok in cand_tokens for tok in target_tokens):
            return prod_url, prod_title
            
        # Special check for 1# / 1
        if "1" in target_tokens and ("1" in cand_tokens or "1#" in prod_title):
            # Verify capacity if present in model_name or title
            return prod_url, prod_title

    return None, "No match found on collection page"

print("=== Testing Collection Page Product Matcher ===")
collection_url = "https://www.stuffcool.com/collections/power-bank"
qualifier_tokens = ["Max", "Ultra", "Plus", "Pro", "Mini", "Lite", "Go"]

test_models = ["1#", "Roam Plus", "Giga 20000 mAH", "Lucid", "Major 10000 mAH"]
for m in test_models:
    url, title = find_product_on_collection_page(m, collection_url, qualifier_tokens)
    print(f"\nModel: '{m}'")
    print(f"  -> Matched URL: {url}")
    print(f"  -> Title: {title}")
