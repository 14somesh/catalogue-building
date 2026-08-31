import os
import sys
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, quote_plus
from ddgs import DDGS

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import DEFAULT_HEADERS, reject_qualifier_mismatch, fetch_and_parse_url

def search_retail_tier3_fixed(brand: str, model_name: str, store_name: str, qualifier_tokens: list) -> str:
    domain_map = {
        "croma": "croma.com",
        "reliance": "reliancedigital.in",
        "flipkart": "flipkart.com",
        "tatacliq": "tatacliq.com"
    }
    target_domain = domain_map.get(store_name.lower())
    if not target_domain:
        return None

    queries = [
        f"{brand} {model_name} {store_name}",
        f"{brand} {model_name} site:{target_domain}"
    ]
    
    ddgs = DDGS()
    for q in queries:
        try:
            results = list(ddgs.text(q, max_results=6))
            for r in results:
                href = r.get("href", "")
                title = r.get("title", "")
                if target_domain in href and ("/p/" in href or "/p-" in href or "/product/" in href):
                    is_valid, _ = reject_qualifier_mismatch(model_name, title, qualifier_tokens, brand=brand)
                    if is_valid:
                        return href
        except Exception as e:
            pass
            
    return None

print("=== Testing Fixed Tier 3 Retail Search ===")
qualifiers = ["Max", "Ultra", "Plus", "Pro", "Mini", "Lite", "Go"]

test_models = ["Major 10000 mAH", "Giga 20000 mAH", "Lucid"]
for m in test_models:
    print(f"\nSearching Tier 3 for: 'Stuffcool {m}'")
    for store in ["croma", "reliance", "flipkart", "tatacliq"]:
        url = search_retail_tier3_fixed("Stuffcool", m, store, qualifiers)
        if url:
            print(f"  -> Found {store} URL: {url}")
            res = fetch_and_parse_url(url, tier=3)
            print(f"     Parse Success: {res.success}")
            print(f"     Extracted Specs: {res.specs}")
            break
