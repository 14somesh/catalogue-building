import os
import sys
import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import DEFAULT_HEADERS

print("=== 1. Testing Google Search on Playwright ===")
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(user_agent=DEFAULT_HEADERS["User-Agent"])
    
    # Test Google Search
    page.goto(f"https://www.google.com/search?q={quote_plus('site:croma.com Stuffcool Major 10000 mAH')}", timeout=15000)
    g_links = page.evaluate('''() => {
        return Array.from(document.querySelectorAll('a'))
            .map(a => a.href)
            .filter(h => h && h.includes('croma.com') && h.includes('/p/'));
    }''')
    print("Google Croma product links:", g_links)
    
    # Test Flipkart Search & Scraping
    print("\n=== 2. Testing Flipkart Scraping (Specs & Images) ===")
    page.goto("https://www.flipkart.com/search?q=Stuffcool+Major+10000mAh", timeout=15000)
    fk_links = page.evaluate('''() => {
        return Array.from(document.querySelectorAll('a'))
            .map(a => a.href)
            .filter(h => h && h.includes('/p/'));
    }''')
    print("Flipkart search product links:", fk_links[:3])
    
    if fk_links:
        fk_url = fk_links[0]
        print(f"Navigating to Flipkart product page: {fk_url}")
        page.goto(fk_url, timeout=15000)
        fk_title = page.evaluate("() => document.querySelector('h1')?.innerText")
        fk_specs_count = page.evaluate("() => document.querySelectorAll('table tr').length")
        fk_image = page.evaluate("() => document.querySelector('img._396cs4, img.DByuf4, img._2r_T1I')?.src")
        print(f"Flipkart Title: {fk_title}")
        print(f"Flipkart Specs Table Rows: {fk_specs_count}")
        print(f"Flipkart Image: {fk_image}")

    browser.close()
