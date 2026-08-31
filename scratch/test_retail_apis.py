import os
import sys
import requests
import json
from urllib.parse import quote_plus

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import DEFAULT_HEADERS

print("=== 1. Testing Reliance Digital Search API ===")
reliance_api = "https://www.reliancedigital.in/rildigitalws/v2/rrL/products/search?q=Stuffcool+Major:relevance&page=0&pageSize=10"
try:
    r = requests.get(reliance_api, headers=DEFAULT_HEADERS, timeout=10)
    print("Reliance API Status:", r.status_code)
    if r.status_code == 200:
        data = r.json()
        prods = data.get("data", {}).get("products", []) or data.get("products", [])
        print(f"Reliance Products found ({len(prods)}):")
        for p in prods[:3]:
            print(f"  • {p.get('name')} -> https://www.reliancedigital.in{p.get('url')}")
except Exception as e:
    print("Reliance API error:", e)

print("\n=== 2. Testing Tata CLiQ Search API ===")
tatacliq_api = "https://www.tatacliq.com/marketingservices/v2/products/search?searchText=Stuffcool%20Major&isKeywordRedirect=false&isKeywordRedirectEnabled=false&channel=WEB"
try:
    r = requests.get(tatacliq_api, headers=DEFAULT_HEADERS, timeout=10)
    print("Tata CLiQ API Status:", r.status_code)
    if r.status_code == 200:
        data = r.json()
        prods = data.get("searchresult", [])
        print(f"Tata CLiQ Products found ({len(prods)}):")
        for p in prods[:3]:
            print(f"  • {p.get('productName')} -> https://www.tatacliq.com{p.get('webURL')}")
except Exception as e:
    print("Tata CLiQ API error:", e)

print("\n=== 3. Testing Croma Search API ===")
croma_api = "https://api.croma.com/search/v1/search?query=Stuffcool+Major&channel=WEB"
try:
    r = requests.get(croma_api, headers=DEFAULT_HEADERS, timeout=10)
    print("Croma API Status:", r.status_code)
    if r.status_code == 200:
        print("Croma API data:", r.text[:300])
except Exception as e:
    print("Croma API error:", e)

print("\n=== 4. Testing Brave Search HTML ===")
try:
    r_brave = requests.get("https://search.brave.com/search?q=site:croma.com+Stuffcool+Major+10000+mAh", headers=DEFAULT_HEADERS, timeout=10)
    print("Brave Status:", r_brave.status_code)
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(r_brave.text, "html.parser")
    b_links = [a.get("href") for a in soup.find_all("a", href=True) if "croma.com" in a.get("href")]
    print("Brave Croma links found:", set(b_links))
except Exception as e:
    print("Brave error:", e)
