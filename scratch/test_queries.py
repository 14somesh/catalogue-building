import os
import sys
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
    
    test_queries = [
        "stuffcool major 10000 croma",
        "croma stuffcool powerbank",
        "stuffcool giga powerbank croma",
        "stuffcool major powerbank reliance digital",
        "stuffcool major powerbank flipkart"
    ]
    
    for q in test_queries:
        print(f"\n--- Testing DDG query: '{q}' ---")
        page.goto(f"https://duckduckgo.com/?q={q.replace(' ', '+')}", wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2500)
        
        links = page.evaluate('''() => {
            return Array.from(document.querySelectorAll('a'))
                .map(a => ({href: a.href, text: a.innerText}))
                .filter(item => item.href && (item.href.includes('croma.com') || item.href.includes('reliancedigital.in') || item.href.includes('flipkart.com') || item.href.includes('tatacliq.com')));
        }''')
        print(f"Found {len(links)} retail links:")
        for l in links[:5]:
            print(f"  • {l['href']} -> '{l['text'][:60]}'")
            
    browser.close()
