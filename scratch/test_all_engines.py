import os
import sys
import requests
import re
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

query = "Stuffcool Major 10000 mAh croma"

# 1. Test Yahoo
print("=== 1. Testing Yahoo ===")
try:
    ry = requests.get(f"https://search.yahoo.com/search?p={quote_plus(query)}", headers=headers, timeout=8)
    print("Yahoo status:", ry.status_code)
    sy = BeautifulSoup(ry.text, "html.parser")
    for a in sy.find_all("a", href=True):
        if "croma.com" in a["href"] and "/p/" in a["href"]:
            print("  Yahoo Croma link:", a["href"])
except Exception as e:
    print("Yahoo error:", e)

# 2. Test Bing HTML
print("\n=== 2. Testing Bing HTML ===")
try:
    rb = requests.get(f"https://www.bing.com/search?q={quote_plus(query)}", headers=headers, timeout=8)
    print("Bing status:", rb.status_code)
    sb = BeautifulSoup(rb.text, "html.parser")
    for a in sb.find_all("a", href=True):
        if "croma.com" in a["href"] and "/p/" in a["href"]:
            print("  Bing Croma link:", a["href"])
except Exception as e:
    print("Bing error:", e)

# 3. Test Ask.com
print("\n=== 3. Testing Ask.com ===")
try:
    ra = requests.get(f"https://www.ask.com/web?q={quote_plus(query)}", headers=headers, timeout=8)
    print("Ask status:", ra.status_code)
    sa = BeautifulSoup(ra.text, "html.parser")
    for a in sa.find_all("a", href=True):
        if "croma.com" in a["href"] and "/p/" in a["href"]:
            print("  Ask Croma link:", a["href"])
except Exception as e:
    print("Ask error:", e)

# 4. Test Qwant
print("\n=== 4. Testing Qwant ===")
try:
    rq = requests.get(f"https://api.qwant.com/v3/search/web?q={quote_plus(query)}&count=10&locale=en_IN", headers=headers, timeout=8)
    print("Qwant status:", rq.status_code)
    if rq.status_code == 200:
        data = rq.json()
        for it in data.get("data", {}).get("result", {}).get("items", {}).get("main", []):
            print("  Qwant item:", it.get("url"), it.get("title"))
except Exception as e:
    print("Qwant error:", e)
