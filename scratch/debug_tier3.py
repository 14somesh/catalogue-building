import os
import sys
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

# Ensure project root in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.scraper import DEFAULT_HEADERS

print("=" * 80)
print("DEBUGGING TIER 3 RETAIL SEARCH FOR 'Major 10000 mAH'")
print("=" * 80)

brand = "Stuffcool"
model_name = "Major 10000 mAH"
target_domain = "croma.com"

# 1. DuckDuckGo HTML Search
query = f"site:{target_domain} {brand} {model_name}"
search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
print(f"\n1. DuckDuckGo Search URL:\n   {search_url}\n")

try:
    r = requests.get(search_url, headers=DEFAULT_HEADERS, timeout=10)
    print(f"   HTTP Status: {r.status_code}")
    print(f"   Response Length: {len(r.text)} bytes")
    soup = BeautifulSoup(r.text, "html.parser")
    
    # Check result snippets
    results = []
    for a in soup.find_all("a", class_="result__url"):
        results.append((a.get("href", ""), a.get_text().strip()))
    
    print(f"   Found {len(results)} 'result__url' tags:")
    for href, text in results:
        print(f"     • href: {href} | text: {text}")

    # Check result titles
    title_links = soup.find_all("a", class_="result__snippet")
    print(f"   Found {len(title_links)} 'result__snippet' tags.")
    
    all_links = soup.find_all("a")
    print(f"   Total <a> tags: {len(all_links)}")
    croma_links = [a.get("href") for a in all_links if a.get("href") and "croma.com" in a.get("href")]
    print(f"   Croma links found in HTML: {croma_links}")

except Exception as e:
    print(f"   DuckDuckGo Request Error: {e}")

# 2. Test Direct Croma Store Search API / URL
print("\n2. Testing Direct Croma Search / Store endpoint...")
croma_query = f"{brand} {model_name}"
croma_search_url = f"https://www.croma.com/searchB?q={quote_plus(croma_query)}%3Arelevance&text={quote_plus(croma_query)}"
print(f"   Croma Search URL: {croma_search_url}")
try:
    cr = requests.get(croma_search_url, headers=DEFAULT_HEADERS, timeout=10)
    print(f"   Croma HTTP Status: {cr.status_code}")
    print(f"   Croma HTML Length: {len(cr.text)}")
    csoup = BeautifulSoup(cr.text, "html.parser")
    product_links = []
    for a in csoup.find_all("a", href=True):
        if "/p/" in a["href"]:
            product_links.append(a["href"])
    print(f"   Product links on Croma search: {set(product_links[:5])}")
except Exception as e:
    print(f"   Croma Search Error: {e}")

print("=" * 80)
