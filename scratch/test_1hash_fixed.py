import os
import sys
import re
from urllib.parse import quote_plus, urljoin
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import DEFAULT_HEADERS, reject_qualifier_mismatch, normalize_model_tokens, extract_model_name_portion

def test_tier1_tier2(model_name: str, brand: str = "Stuffcool", domain: str = "stuffcool.com", collection_url: str = "https://www.stuffcool.com/collections/power-bank"):
    qualifier_tokens = ["Max", "Ultra", "Plus", "Pro", "Mini", "Lite", "Go"]
    
    # 1. Tier 1
    stopwords = {"powerbank", "power", "bank", "portable", "charger", "fast", "charging", "series", "the", "with", "and"}
    words = [w.lower() for w in re.findall(r"[a-zA-Z0-9]+", model_name.replace("+", "plus"))]
    distinctive_terms = [w for w in words if w not in stopwords]
    
    print(f"\nTesting for '{model_name}':")
    print(f"Distinctive terms: {distinctive_terms}")
    
    # Tier 1 suggest
    suggest_url = f"https://www.{domain}/search/suggest.json?q={quote_plus(model_name)}&resources[type]=product"
    r = requests.get(suggest_url, headers=DEFAULT_HEADERS, timeout=8)
    matched_tier1 = None
    if r.status_code == 200:
        data = r.json()
        products = data.get("resources", {}).get("results", {}).get("products", [])
        for p in products:
            prod_title = p.get("title", "")
            is_valid, _ = reject_qualifier_mismatch(model_name, prod_title, qualifier_tokens, brand=brand)
            if not is_valid:
                continue
            prod_tokens = set(re.findall(r"[a-zA-Z0-9]+", prod_title.lower().replace("+", "plus")))
            if distinctive_terms and all(term in prod_tokens for term in distinctive_terms):
                matched_tier1 = urljoin(f"https://www.{domain}", p.get("url", "").split("?")[0])
                print(f"  Tier 1 Match: {prod_title} -> {matched_tier1}")
                break
    if not matched_tier1:
        print("  Tier 1: No match (Escalating to Tier 2)")

    # Tier 2 Collection scan
    r2 = requests.get(collection_url, headers=DEFAULT_HEADERS, timeout=10)
    soup = BeautifulSoup(r2.text, "html.parser")
    target_tokens = set(re.findall(r"[a-zA-Z0-9]+", model_name.lower().replace("+", "plus")))
    target_tokens = {t for t in target_tokens if t not in stopwords}

    prod_map = {}
    for a in soup.find_all("a", href=True):
        raw_href = a["href"].split("?")[0].split("#")[0]
        if "/products/" in raw_href:
            text = a.get_text().strip()
            img = a.find("img", alt=True)
            img_alt = img["alt"].strip() if img else ""
            slug = raw_href.split("/products/")[-1].replace("-", " ")
            
            if raw_href not in prod_map:
                prod_map[raw_href] = []
            if text and len(text) > 3 and not re.match(r'^\d+(\.\d+)?$', text):
                prod_map[raw_href].append(text)
            if img_alt:
                prod_map[raw_href].append(img_alt)
            prod_map[raw_href].append(slug)

    matched_tier2 = None
    for raw_href, text_list in prod_map.items():
        combined_candidate = " ".join(text_list)
        is_valid, _ = reject_qualifier_mismatch(model_name, combined_candidate, qualifier_tokens, brand=brand)
        if not is_valid:
            continue
        
        slug_part = raw_href.split("/products/")[-1].replace("-", " ")
        is_valid_slug, _ = reject_qualifier_mismatch(model_name, slug_part, qualifier_tokens, brand=brand)
        if not is_valid_slug:
            continue

        cand_tokens = set(re.findall(r"[a-zA-Z0-9]+", combined_candidate.lower().replace("+", "plus")))
        if target_tokens and all(tok in cand_tokens for tok in target_tokens):
            matched_tier2 = urljoin(collection_url, raw_href)
            print(f"  Tier 2 Match: {matched_tier2}")
            break
            
    if not matched_tier2:
        print("  Tier 2: No match")

test_tier1_tier2("1#")
test_tier1_tier2("Roam Plus")
test_tier1_tier2("Major 10000 mAH")
test_tier1_tier2("Giga 20000 mAH")
test_tier1_tier2("Lucid")
