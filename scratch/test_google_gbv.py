import os
import sys
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus, parse_qs, urlparse

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Google GBV=1 (Google Basic Version for HTML clients)
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

queries = [
    "site:croma.com Stuffcool Major 10000 mAh",
    "site:croma.com Stuffcool Giga 20000 mAh",
    "site:croma.com Stuffcool Lucid",
    "site:flipkart.com Stuffcool Major 10000",
    "site:reliancedigital.in Stuffcool Major"
]

print("=== Testing Google Basic HTML (gbv=1) ===")
for q in queries:
    url = f"https://www.google.com/search?q={quote_plus(q)}&gbv=1"
    try:
        r = requests.get(url, headers=headers, timeout=8)
        print(f"\nQuery: '{q}' -> Status: {r.status_code}, Length: {len(r.text)}")
        soup = BeautifulSoup(r.text, "html.parser")
        
        # In Google gbv=1, links are <a href="/url?q=https://...">
        found_links = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/url?q=" in href:
                actual_url = parse_qs(urlparse(href).query).get("q", [""])[0]
                if actual_url.startswith("http") and not any(x in actual_url for x in ["google.", "support.google", "youtube."]):
                    title = a.get_text().strip()
                    found_links.append((actual_url, title))
        
        print(f"Found {len(found_links)} links:")
        for link, t in found_links[:3]:
            print(f"  • {link}")
            print(f"    Text: {t[:80]}")
    except Exception as e:
        print(f"Error on '{q}': {e}")
