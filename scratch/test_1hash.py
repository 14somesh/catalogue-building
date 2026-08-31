import os
import sys
import requests
import json
from urllib.parse import quote_plus

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import DEFAULT_HEADERS

print("=== Testing Shopify Store Suggest for '1#' ===")

domain = "stuffcool.com"
for q in ["1#", "1", "1 10000"]:
    url = f"https://www.{domain}/search/suggest.json?q={quote_plus(q)}&resources[type]=product"
    r = requests.get(url, headers=DEFAULT_HEADERS, timeout=8)
    print(f"\nQuery: '{q}' -> Status: {r.status_code}")
    if r.status_code == 200:
        data = r.json()
        prods = data.get("resources", {}).get("results", {}).get("products", [])
        print(f"Products returned ({len(prods)}):")
        for p in prods:
            print(f"  • Title: {p.get('title')}\n    URL: {p.get('url')}")
