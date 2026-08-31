import os
import sys
from urllib.parse import quote_plus
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        locale="en-US"
    )
    page = context.new_page()
    page.goto("https://www.google.com/search?q=croma+stuffcool+major+10000+mah&hl=en", wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(2000)
    
    # Check if consent button exists
    try:
        consent_btn = page.locator('button:has-text("Accept all"), button:has-text("I agree"), button#L2AGLb')
        if consent_btn.count() > 0:
            print("Clicking consent button...")
            consent_btn.first.click()
            page.wait_for_timeout(2000)
    except:
        pass
        
    print("Current page title:", page.title())
    
    # Extract links
    all_links = page.evaluate('''() => {
        return Array.from(document.querySelectorAll('a'))
            .map(a => ({href: a.href, text: a.innerText}))
            .filter(item => item.href && item.href.startsWith('http') && !item.href.includes('google.'));
    }''')
    
    print(f"Total external links found on Google: {len(all_links)}")
    for l in all_links[:10]:
        print(f"  • {l['href']}\n    '{l['text'][:60]}'")
        
    browser.close()
