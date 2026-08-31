import os
import sys
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus, unquote

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import DEFAULT_HEADERS

print("=== 1. Testing DDG html search with session and referer ===")
s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://duckduckgo.com/",
})

try:
    # First get home page to establish session/cookies
    s.get("https://html.duckduckgo.com/html/", timeout=5)
    r = s.post("https://html.duckduckgo.com/html/", data={"q": "site:croma.com Stuffcool Major 10000 mAH"}, timeout=10)
    print("DDG POST status:", r.status_code)
    soup = BeautifulSoup(r.text, "html.parser")
    links = []
    for a in soup.find_all("a", class_="result__url"):
        links.append((a.get("href"), a.get_text().strip()))
    for a in soup.find_all("a", class_="result__snippet"):
        links.append((a.get("href"), a.get_text().strip()))
    print("DDG POST links found:", len(links), links[:3])
except Exception as e:
    print("DDG POST error:", e)

print("\n=== 2. Testing Bing RSS / Web query ===")
try:
    r_rss = requests.get("https://www.bing.com/search?q=Stuffcool+Major+10000+mAh+croma&format=rss", headers=DEFAULT_HEADERS, timeout=10)
    print("Bing RSS status:", r_rss.status_code)
    soup_rss = BeautifulSoup(r_rss.text, "xml")
    items = soup_rss.find_all("item")
    print(f"Bing RSS items ({len(items)}):")
    for it in items[:3]:
        title = it.find("title").text if it.find("title") else ""
        link = it.find("link").text if it.find("link") else ""
        desc = it.find("description").text if it.find("description") else ""
        print(f"  • Title: {title}\n    Link: {link}\n    Desc: {desc[:100]}...")
except Exception as e:
    print("Bing RSS error:", e)
