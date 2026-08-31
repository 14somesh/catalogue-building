import os
import sys
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import DEFAULT_HEADERS

query = "site:croma.com Stuffcool Major 10000 mAH"

print("--- 1. Testing DuckDuckGo Lite (POST) ---")
try:
    r_lite = requests.post("https://lite.duckduckgo.com/lite/", data={"q": query}, headers=DEFAULT_HEADERS, timeout=10)
    print("Lite status:", r_lite.status_code)
    soup_lite = BeautifulSoup(r_lite.text, "html.parser")
    links_lite = [a.get("href") for a in soup_lite.find_all("a", class_="result-link")]
    print("Lite result-link tags:", links_lite)
    if not links_lite:
        all_a = [a.get("href") for a in soup_lite.find_all("a") if a.get("href") and "croma.com" in a.get("href")]
        print("Lite croma links:", all_a)
except Exception as e:
    print("Lite error:", e)

print("\n--- 2. Testing Bing HTML Search ---")
try:
    r_bing = requests.get(f"https://www.bing.com/search?q={quote_plus(query)}", headers=DEFAULT_HEADERS, timeout=10)
    print("Bing status:", r_bing.status_code)
    soup_bing = BeautifulSoup(r_bing.text, "html.parser")
    b_links = [a.get("href") for a in soup_bing.find_all("a") if a.get("href") and "croma.com" in a.get("href")]
    print("Bing croma links:", set(b_links))
except Exception as e:
    print("Bing error:", e)

print("\n--- 3. Testing Yahoo HTML Search ---")
try:
    r_yahoo = requests.get(f"https://search.yahoo.com/search?p={quote_plus(query)}", headers=DEFAULT_HEADERS, timeout=10)
    print("Yahoo status:", r_yahoo.status_code)
    soup_yahoo = BeautifulSoup(r_yahoo.text, "html.parser")
    y_links = [a.get("href") for a in soup_yahoo.find_all("a") if a.get("href") and "croma.com" in a.get("href")]
    print("Yahoo croma links:", set(y_links))
except Exception as e:
    print("Yahoo error:", e)

print("\n--- 4. Testing Playwright Headless Search on DuckDuckGo / Bing ---")
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=DEFAULT_HEADERS["User-Agent"])
        page = context.new_page()
        page.goto(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}", timeout=15000)
        p_links = page.evaluate('''() => {
            return Array.from(document.querySelectorAll('a'))
                .map(a => a.href)
                .filter(h => h && h.includes('croma.com'));
        }''')
        print("Playwright DuckDuckGo croma links:", p_links)
        
        # Test Bing on Playwright
        page.goto(f"https://www.bing.com/search?q={quote_plus(query)}", timeout=15000)
        p_bing = page.evaluate('''() => {
            return Array.from(document.querySelectorAll('a'))
                .map(a => a.href)
                .filter(h => h && h.includes('croma.com'));
        }''')
        print("Playwright Bing croma links:", p_bing)
        browser.close()
except Exception as e:
    print("Playwright search error:", e)
