import os
import sys
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import DEFAULT_HEADERS

print("=== Testing Bing RSS Search for Retail Sites ===")

queries = [
    "Stuffcool Major 10000 mAh croma",
    "Stuffcool Giga 20000 mAh croma",
    "Stuffcool Lucid 5000 mAh croma",
    "Stuffcool 1# 10000 mAh",
    "site:reliancedigital.in Stuffcool Major",
    "site:tatacliq.com Stuffcool Major",
    "site:flipkart.com Stuffcool Major 10000"
]

for q in queries:
    rss_url = f"https://www.bing.com/search?q={quote_plus(q)}&format=rss"
    try:
        r = requests.get(rss_url, headers=DEFAULT_HEADERS, timeout=8)
        soup = BeautifulSoup(r.text, "html.parser")
        items = soup.find_all("item")
        print(f"\nQuery: '{q}' -> Status: {r.status_code}, Results: {len(items)}")
        for it in items[:3]:
            title = it.find("title").text if it.find("title") else ""
            link = it.find("link").text if it.find("link") else ""
            desc = it.find("description").text if it.find("description") else ""
            print(f"  • {title}")
            print(f"    Link: {link}")
            print(f"    Snippet: {desc[:120]}...")
    except Exception as e:
        print(f"Error on '{q}': {e}")
