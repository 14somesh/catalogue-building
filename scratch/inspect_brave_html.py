import os
import sys
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import DEFAULT_HEADERS

query = "site:croma.com Stuffcool Major 10000 mAh"
url = f"https://search.brave.com/search?q={quote_plus(query)}"
r = requests.get(url, headers=DEFAULT_HEADERS, timeout=8)
soup = BeautifulSoup(r.text, "html.parser")

print("Status:", r.status_code)
# Find all links containing croma.com
croma_results = []
for a in soup.find_all("a", href=True):
    href = a["href"]
    if "croma.com" in href and "/p/" in href:
        title = a.get_text().strip()
        croma_results.append((href, title))

print(f"Found {len(croma_results)} Croma product results:")
for href, title in croma_results:
    print(f"  • {href} -> '{title}'")
