import os
import sys
import requests
from bs4 import BeautifulSoup

url = "https://www.croma.com/stuffcool-major-10000-mah-22-5w-fast-charging-power-bank-2-type-a-and-1-type-c-and-micro-usb-ports-led-indicator-black-/p/303296"

headers_list = [
    {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en-GB;q=0.9,en;q=0.8"
    },
    {
        "User-Agent": "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9"
    },
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.croma.com/",
        "Origin": "https://www.croma.com"
    }
]

for i, h in enumerate(headers_list):
    try:
        r = requests.get(url, headers=h, timeout=8)
        print(f"Header {i+1} -> Status: {r.status_code}, Length: {len(r.text)}")
        if r.status_code == 200 and "Access Denied" not in r.text:
            soup = BeautifulSoup(r.text, "html.parser")
            print("  H1:", soup.find("h1"))
    except Exception as e:
        print(f"Header {i+1} error: {e}")
