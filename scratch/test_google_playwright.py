import os
import sys
from urllib.parse import quote_plus
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"]
    )
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
    page = context.new_page()

    queries = [
        "croma stuffcool major 10000 mah",
        "croma stuffcool giga 20000 mah",
        "croma stuffcool lucid",
        "flipkart stuffcool major 10000",
        "reliance digital stuffcool major"
    ]

    for q in queries:
        print(f"\n--- Google Query: '{q}' ---")
        try:
            page.goto(f"https://www.google.com/search?q={quote_plus(q)}", wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(2000)
            
            links = page.evaluate('''() => {
                const results = [];
                document.querySelectorAll('a[href]').forEach(a => {
                    const href = a.href;
                    if (href && (href.includes('croma.com') || href.includes('reliancedigital.in') || href.includes('flipkart.com') || href.includes('tatacliq.com'))) {
                        results.push({href: href, text: a.innerText});
                    }
                });
                return results;
            }''')
            print(f"Found {len(links)} links on Google:")
            for l in links[:5]:
                print(f"  • {l['href']} -> '{l['text'][:60]}'")
        except Exception as e:
            print("Google error:", e)

    browser.close()
