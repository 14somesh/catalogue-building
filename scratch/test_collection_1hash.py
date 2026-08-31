import os
import sys
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import DEFAULT_HEADERS, reject_qualifier_mismatch

url = "https://www.stuffcool.com/collections/power-bank"
r = requests.get(url, headers=DEFAULT_HEADERS, timeout=10)
print("Collection status:", r.status_code)
soup = BeautifulSoup(r.text, "html.parser")

prods = []
for a in soup.find_all("a", href=True):
    href = a["href"]
    if "/products/" in href:
        title = a.get_text().strip()
        prods.append((href, title))

print(f"Total product links on collection page: {len(prods)}")
one_hash_matches = [p for p in prods if "1" in p[0].lower() or "1#" in p[1].lower()]
print(f"Links matching '1':")
for h, t in one_hash_matches:
    print(f"  • {h} -> '{t}'")
