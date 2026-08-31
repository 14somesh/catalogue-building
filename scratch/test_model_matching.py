import os
import sys
import re
from ddgs import DDGS

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import reject_qualifier_mismatch, extract_model_name_portion, normalize_model_tokens

def search_retail_strict(brand: str, model_name: str, store_name: str, qualifier_tokens: list) -> str:
    domain_map = {
        "croma": "croma.com",
        "reliance": "reliancedigital.in",
        "flipkart": "flipkart.com",
        "tatacliq": "tatacliq.com"
    }
    target_domain = domain_map.get(store_name.lower())
    if not target_domain:
        return None

    # Required distinctive model tokens (e.g. 'major', 'giga', 'lucid', '1')
    model_part = extract_model_name_portion(model_name, brand=brand)
    required_tokens = normalize_model_tokens(model_part)
    # Filter out stopwords
    stopwords = {"powerbank", "power", "bank", "portable", "charger", "fast", "charging", "series", "the", "with", "and"}
    required_tokens = {t for t in required_tokens if t not in stopwords}

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
                    # 1. Qualifier check
                    is_valid, _ = reject_qualifier_mismatch(model_name, title, qualifier_tokens, brand=brand)
                    if not is_valid:
                        continue
                    # 2. Required model token check
                    cand_tokens = normalize_model_tokens(title + " " + href.replace("-", " "))
                    if required_tokens and all(tok in cand_tokens for tok in required_tokens):
                        return href
        except Exception as e:
            pass
            
    return None

print("=== Testing Strict Model Name Retail Search ===")
qualifiers = ["Max", "Ultra", "Plus", "Pro", "Mini", "Lite", "Go"]

test_models = ["Major 10000 mAH", "Giga 20000 mAH", "Lucid", "1#", "Roam Plus"]
for m in test_models:
    for store in ["croma", "reliance", "flipkart", "tatacliq"]:
        url = search_retail_strict("Stuffcool", m, store, qualifiers)
        if url:
            print(f"Model '{m}' -> Found {store} URL: {url}")
            break
    else:
        print(f"Model '{m}' -> No retail listing found")
