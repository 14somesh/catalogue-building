import os
import sys
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
}

query = "site:croma.com Stuffcool Major 10000 mAh"
url = f"https://www.bing.com/search?q={quote_plus(query)}"
r = requests.get(url, headers=headers, timeout=8)
print("Status:", r.status_code)
soup = BeautifulSoup(r.text, "html.parser")

# On Bing, results are in <li class="b_algo"> <h2><a href="...">
results = []
for li in soup.find_all("li", class_="b_algo"):
    h2 = li.find("h2")
    if h2:
        a = h2.find("a")
        if a and a.get("href"):
            p = li.find("p") or li.find("div", class_="b_caption")
            results.append((a["href"], a.get_text().strip(), p.get_text().strip() if p else ""))

print(f"Found {len(results)} b_algo results:")
for href, title, snippet in results:
    print(f"  • {href}\n    Title: '{title}'\n    Snippet: '{snippet[:100]}'")
