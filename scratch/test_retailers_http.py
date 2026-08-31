import os
import sys
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import DEFAULT_HEADERS

print("=== 1. Testing Croma with requests ===")
croma_url = "https://www.croma.com/stuffcool-major-10000-mah-22-5w-fast-charging-power-bank-2-type-a-and-1-type-c-and-micro-usb-ports-led-indicator-black-/p/303296"
try:
    r = requests.get(croma_url, headers=DEFAULT_HEADERS, timeout=10)
    print("Croma Status:", r.status_code, "Length:", len(r.text))
    if "Access Denied" in r.text or "<title>Access Denied</title>" in r.text:
        print("Croma returned: Access Denied")
    else:
        soup = BeautifulSoup(r.text, "html.parser")
        print("Croma Title:", soup.find("h1"))
except Exception as e:
    print("Croma error:", e)

print("\n=== 2. Testing Reliance Digital with requests ===")
reliance_url = "https://www.reliancedigital.in/stuffcool-major-10000-mah-22-5-w-fast-charging-power-bank-black/p/493838481"
try:
    r = requests.get(reliance_url, headers=DEFAULT_HEADERS, timeout=10)
    print("Reliance Status:", r.status_code, "Length:", len(r.text))
    soup = BeautifulSoup(r.text, "html.parser")
    print("Reliance Title:", soup.find("h1"))
except Exception as e:
    print("Reliance error:", e)

print("\n=== 3. Testing Tata CLiQ with requests ===")
tatacliq_url = "https://www.tatacliq.com/stuffcool-major-10000mah-powerbank/p-mp000000018392123"
try:
    r = requests.get(tatacliq_url, headers=DEFAULT_HEADERS, timeout=10)
    print("Tata CLiQ Status:", r.status_code, "Length:", len(r.text))
    soup = BeautifulSoup(r.text, "html.parser")
    print("Tata CLiQ Title:", soup.find("h1"))
except Exception as e:
    print("Tata CLiQ error:", e)

print("\n=== 4. Testing Flipkart with requests ===")
flipkart_url = "https://www.flipkart.com/stuffcool-major-10000-mah-power-bank-22-5-w-fast-charging/p/itm53cf73bb3aef2"
try:
    r = requests.get(flipkart_url, headers=DEFAULT_HEADERS, timeout=10)
    print("Flipkart Status:", r.status_code, "Length:", len(r.text))
    soup = BeautifulSoup(r.text, "html.parser")
    h1 = soup.find("h1") or soup.find("span", class_="B_NuCI")
    print("Flipkart Title:", h1)
except Exception as e:
    print("Flipkart error:", e)

print("\n=== 5. Testing DuckDuckGo text search module ===")
try:
    import duckduckgo_search
    print("duckduckgo_search installed:", duckduckgo_search.__version__)
    from duckduckgo_search import DDGS
    results = DDGS().text("Stuffcool Major 10000 mAH croma OR reliancedigital OR flipkart", max_results=5)
    print("DDGS Results:", results)
except Exception as e:
    print("DDGS Error / Not installed:", e)
