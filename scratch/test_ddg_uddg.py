import os
import sys
from urllib.parse import unquote
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
    
    q = "stuffcool major 10000 croma"
    print(f"Navigating to DDG for: '{q}'...")
    page.goto(f"https://duckduckgo.com/?q={q.replace(' ', '+')}", wait_until="networkidle", timeout=15000)
    page.wait_for_timeout(2000)
    
    # Extract all raw links and unquote them
    raw_links = page.evaluate('''() => {
        return Array.from(document.querySelectorAll('a'))
            .map(a => ({href: a.href, text: a.innerText}));
    }''')
    
    print(f"Total <a> elements on page: {len(raw_links)}")
    croma_found = []
    for l in raw_links:
        unquoted = unquote(l["href"])
        if "croma.com" in unquoted:
            croma_found.append((unquoted, l["text"]))
            
    print(f"Found {len(croma_found)} Croma links (after unquoting uddg):")
    for link, text in croma_found[:5]:
        print(f"  • {link}\n    Text: '{text}'")
        
    browser.close()
