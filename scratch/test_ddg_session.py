import os
import sys
import requests
import re
from bs4 import BeautifulSoup
from urllib.parse import unquote

s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "DNT": "1",
    "Sec-GPC": "1",
    "Upgrade-Insecure-Requests": "1"
})

# First establish cookies from duckduckgo.com
try:
    s.get("https://duckduckgo.com/", timeout=5)
    
    # Query html.duckduckgo.com
    r = s.post("https://html.duckduckgo.com/html/", data={"q": "Stuffcool Major 10000 mAh croma"}, timeout=10)
    print("DDG POST Status:", r.status_code)
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.find_all("a", class_="result__url"):
        raw = a.get("href", "")
        m = re.search(r'uddg=([^&]+)', raw)
        print("  DDG link:", unquote(m.group(1)) if m else raw)
except Exception as e:
    print("DDG error:", e)
