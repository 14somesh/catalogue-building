import os
import sys
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus, unquote, parse_qs, urlparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import DEFAULT_HEADERS, reject_qualifier_mismatch

def search_retail_url(brand: str, model_name: str, domain: str, qualifier_tokens: list) -> str:
    query = f"{brand} {model_name} {domain}"
    clean_model = model_name.lower().replace("+", "plus").strip()
    target_terms = set(re.findall(r'[a-zA-Z0-9]+', clean_model))
    
    # Engine 1: Brave Search
    try:
        url = f"https://search.brave.com/search?q={quote_plus(f'site:{domain} {brand} {model_name}')}"
        r = requests.get(url, headers=DEFAULT_HEADERS, timeout=8)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                if domain in href and ("/p/" in href or "/p-" in href or "/product/" in href):
                    text = a.get_text().strip() or href
                    is_valid, _ = reject_qualifier_mismatch(model_name, text, qualifier_tokens)
                    if is_valid:
                        return href
    except Exception as e:
        pass

    # Engine 2: Bing
    try:
        url = f"https://www.bing.com/search?q={quote_plus(f'site:{domain} {brand} {model_name}')}"
        r = requests.get(url, headers=DEFAULT_HEADERS, timeout=8)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, "html.parser")
            for li in soup.find_all("li", class_="b_algo"):
                a = li.find("a", href=True)
                if a:
                    href = a["href"]
                    # If Bing redirect
                    if "bing.com/ck/a?" in href and "u=a1" in href:
                        import base64
                        try:
                            b64 = href.split("u=a1")[-1].split("&")[0]
                            # Pad base64
                            b64 += "=" * ((4 - len(b64) % 4) % 4)
                            href = base64.b64decode(b64).decode('utf-8', errors='ignore')
                        except:
                            pass
                    if domain in href and ("/p/" in href or "/p-" in href or "/product/" in href):
                        text = a.get_text().strip()
                        is_valid, _ = reject_qualifier_mismatch(model_name, text, qualifier_tokens)
                        if is_valid:
                            return href
    except Exception as e:
        pass

    return None

print("=== Testing Retail URL Discovery ===")
qualifiers = ["Max", "Ultra", "Plus", "Pro", "Mini", "Lite", "Go"]
models = ["Major 10000 mAH", "Giga 20000 mAH", "Lucid", "Odin", "Click 10"]

for m in models:
    res = search_retail_url("Stuffcool", m, "croma.com", qualifiers)
    print(f"Model '{m}' -> Croma URL: {res}")
