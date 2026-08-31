import os
import sys
import requests
import re
from bs4 import BeautifulSoup
from urllib.parse import unquote

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://html.duckduckgo.com",
    "Referer": "https://html.duckduckgo.com/"
}

data = {
    "q": "Stuffcool Major 10000 croma",
    "b": "",
    "kl": "in-en"
}

r = requests.post("https://html.duckduckgo.com/html/", headers=headers, data=data, timeout=10)
print("Status code:", r.status_code)
print("Length:", len(r.text))

soup = BeautifulSoup(r.text, "html.parser")
results = []
for a in soup.find_all("a", class_="result__url"):
    raw_href = a.get("href", "")
    m = re.search(r'uddg=([^&]+)', raw_href)
    actual_url = unquote(m.group(1)) if m else raw_href
    snippet = a.find_parent("div", class_="result__body")
    snip_text = snippet.get_text().strip() if snippet else ""
    results.append((actual_url, snip_text))

print(f"Extracted {len(results)} results:")
for u, s in results[:5]:
    print(f"  • {u}\n    Snippet: {s[:100]}...")
