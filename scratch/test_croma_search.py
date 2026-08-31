import os
import sys
import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import DEFAULT_HEADERS

print("=== 1. Testing Croma Search via Playwright Navigation ===")
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(user_agent=DEFAULT_HEADERS["User-Agent"])
    
    # 1. Search Croma via Google
    print("Navigating to Google...")
    page.goto("https://www.google.com/search?q=croma+stuffcool+major+10000+mah", timeout=20000)
    links = page.evaluate('''() => {
        return Array.from(document.querySelectorAll('a'))
            .map(a => ({href: a.href, text: a.innerText}))
            .filter(item => item.href && item.href.includes('croma.com'));
    }''')
    print("Google Croma Links found:")
    for l in links[:5]:
        print(f"  • {l['href']} -> {l['text'][:60]}")

    # 2. Search Croma directly on croma.com
    print("\nNavigating directly to Croma Search...")
    page.goto("https://www.croma.com/searchB?q=stuffcool%20major%2010000%20mah%3Arelevance&text=stuffcool%20major%2010000%20mah", timeout=20000)
    page.wait_for_timeout(3000)
    croma_prods = page.evaluate('''() => {
        return Array.from(document.querySelectorAll('a'))
            .map(a => ({href: a.href, text: a.innerText}))
            .filter(item => item.href && item.href.includes('/p/'));
    }''')
    print("Direct Croma Product Links found:")
    for cp in croma_prods[:5]:
        print(f"  • {cp['href']} -> {cp['text'][:60]}")

    # 3. Test Croma Product Page Parser on known-good URL
    known_croma_url = "https://www.croma.com/stuffcool-major-10000-mah-22-5w-fast-charging-power-bank-2-type-a-and-1-type-c-and-micro-usb-ports-led-indicator-black-/p/303296"
    print(f"\nNavigating to known Croma URL: {known_croma_url}")
    page.goto(known_croma_url, timeout=20000)
    page.wait_for_timeout(2000)
    croma_title = page.evaluate("() => document.querySelector('h1')?.innerText")
    croma_specs = page.evaluate('''() => {
        const specRows = Array.from(document.querySelectorAll('.cp-specification tr, .spec-table tr, li, .specification-details tr'));
        return specRows.map(r => r.innerText.replace(/\\s+/g, ' ').trim()).filter(t => t.length > 3).slice(0, 15);
    }''')
    print(f"Croma Title: {croma_title}")
    print(f"Croma Specs Sample ({len(croma_specs)}): {croma_specs[:5]}")

    browser.close()
