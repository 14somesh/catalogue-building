import os
import sys
import re
from urllib.parse import quote_plus
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from src.utils.scraper import reject_qualifier_mismatch

def test_store_search(store_name: str, brand: str, model_name: str):
    q = f"{brand} {model_name}"
    qualifiers = ["Max", "Ultra", "Plus", "Pro", "Mini", "Lite", "Go"]
    
    print(f"\n--- Testing Store '{store_name}' for '{q}' ---")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox"
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
            locale="en-IN"
        )
        page = context.new_page()

        if store_name == "croma":
            # Search Croma via mobile search / store URL
            croma_url = f"https://www.croma.com/searchB?q={quote_plus(q)}%3Arelevance&text={quote_plus(q)}"
            try:
                page.goto("https://www.croma.com/", wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(1000)
                page.goto(croma_url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2000)
                prods = page.evaluate('''() => {
                    return Array.from(document.querySelectorAll('a[href*="/p/"]'))
                        .map(a => ({href: a.href, text: a.innerText}))
                        .filter(item => item.text && item.text.length > 5);
                }''')
                print(f"Croma Results ({len(prods)}):")
                for item in prods[:5]:
                    print(f"  • {item['href']} -> '{item['text'][:60]}'")
            except Exception as e:
                print("Croma error:", e)

        elif store_name == "reliance":
            rel_url = f"https://www.reliancedigital.in/search?q={quote_plus(q)}"
            try:
                page.goto(rel_url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2500)
                prods = page.evaluate('''() => {
                    return Array.from(document.querySelectorAll('a[href*="/p/"]'))
                        .map(a => ({href: a.href, text: a.innerText}))
                        .filter(item => item.text && item.text.length > 5);
                }''')
                print(f"Reliance Results ({len(prods)}):")
                for item in prods[:5]:
                    print(f"  • {item['href']} -> '{item['text'][:60]}'")
            except Exception as e:
                print("Reliance error:", e)

        elif store_name == "flipkart":
            fk_url = f"https://www.flipkart.com/search?q={quote_plus(q)}"
            try:
                page.goto(fk_url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(2500)
                prods = page.evaluate('''() => {
                    return Array.from(document.querySelectorAll('a[href*="/p/"]'))
                        .map(a => ({href: a.href, text: a.innerText}))
                        .filter(item => item.text && item.text.length > 5);
                }''')
                print(f"Flipkart Results ({len(prods)}):")
                for item in prods[:5]:
                    print(f"  • {item['href']} -> '{item['text'][:60]}'")
            except Exception as e:
                print("Flipkart error:", e)

        browser.close()

test_store_search("croma", "Stuffcool", "Major 10000 mAH")
test_store_search("reliance", "Stuffcool", "Major 10000 mAH")
test_store_search("flipkart", "Stuffcool", "Major 10000 mAH")
