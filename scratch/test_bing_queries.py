import os
import sys
import re
import requests
import base64
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9"
}

def decode_bing_u(u_str: str) -> str:
    try:
        # u is usually aHR0cHM6Ly9...
        if u_str.startswith("a1"):
            u_str = u_str[2:]
        u_str += "=" * ((4 - len(u_str) % 4) % 4)
        return base64.b64decode(u_str).decode('utf-8', errors='ignore')
    except Exception as e:
        return ""

def search_bing(query: str):
    url = f"https://www.bing.com/search?q={quote_plus(query)}"
    r = requests.get(url, headers=headers, timeout=8)
    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    for li in soup.find_all("li", class_="b_algo"):
        a = li.find("a", href=True)
        if a:
            raw_href = a["href"]
            actual = raw_href
            if "bing.com/ck/a?" in raw_href and "u=" in raw_href:
                m = re.search(r'u=([^&]+)', raw_href)
                if m:
                    actual = decode_bing_u(m.group(1))
            results.append((actual, a.get_text().strip()))
    return results

print("=== 1. Natural Bing Query ===")
res = search_bing("Stuffcool Major 10000 mAh croma")
for u, t in res:
    print(f"  • {u} -> '{t}'")

print("\n=== 2. Croma Store Search Query ===")
res = search_bing("Stuffcool Giga 20000 mAh croma")
for u, t in res:
    print(f"  • {u} -> '{t}'")

print("\n=== 3. Reliance Digital Query ===")
res = search_bing("Stuffcool Major 10000 mAh reliancedigital")
for u, t in res:
    print(f"  • {u} -> '{t}'")
