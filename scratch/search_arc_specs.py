import os
import sys
import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.scraper import search_retail_reliance, fetch_and_parse_url, DEFAULT_HEADERS

def search_arc():
    print("Searching Reliance Digital for Urbn Arc...")
    qualifiers = ["MagSafe", "MagTag", "Mag", "Mini", "Volt", "Pro", "Plus", "Max"]
    rel_url = search_retail_reliance("Urbn", "Arc MagSafe 10000mAh", qualifiers)
    print(f"Reliance URL found: {rel_url}")
    
    if rel_url:
        res = fetch_and_parse_url(rel_url, tier=3)
        print(f"Reliance Parse Result: success={res.success}, specs={res.specs}")

if __name__ == "__main__":
    search_arc()
