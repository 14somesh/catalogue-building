import os
import sys
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import DEFAULT_HEADERS
from src.parsers.croma import CromaParser

print("=== Testing Brave Search across Stuffcool Missing Products ===")

test_products = [
    ("Stuffcool", "Major 10000 mAH"),
    ("Stuffcool", "Giga 20000 mAH"),
    ("Stuffcool", "Lucid"),
    ("Stuffcool", "1#"),
    ("Stuffcool", "Click 20"),
    ("Stuffcool", "Roam Plus")
]

for brand, model in test_products:
    query = f"site:croma.com {brand} {model}"
    url = f"https://search.brave.com/search?q={quote_plus(query)}"
    try:
        r = requests.get(url, headers=DEFAULT_HEADERS, timeout=8)
        soup = BeautifulSoup(r.text, "html.parser")
        
        # Find search result links
        croma_links = []
        for result in soup.find_all("div", class_="snippet"):
            a = result.find("a", href=True)
            title = result.find("div", class_="title")
            desc = result.find("div", class_="snippet-description")
            if a and "/p/" in a["href"]:
                croma_links.append((
                    a["href"],
                    title.get_text().strip() if title else "",
                    desc.get_text().strip() if desc else ""
                ))
        
        print(f"\nProduct: '{brand} {model}' -> Found {len(croma_links)} Croma links:")
        for link, t, d in croma_links[:3]:
            print(f"  • {link}")
            print(f"    Title: {t}")
            print(f"    Snippet: {d[:100]}...")
            
    except Exception as e:
        print(f"Error for {model}: {e}")
