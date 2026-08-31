import os
import sys
from playwright.sync_api import sync_playwright

print("=== Testing Playwright with Stealth Options for Croma & Flipkart ===")

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-infobars",
            "--start-maximized"
        ]
    )
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        viewport={"width": 1366, "height": 768},
        locale="en-US",
        timezone_id="Asia/Kolkata"
    )
    # Add webdriver bypass script
    context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined
        });
    """)
    page = context.new_page()

    # 1. Test Croma known URL
    croma_url = "https://www.croma.com/stuffcool-major-10000-mah-22-5w-fast-charging-power-bank-2-type-a-and-1-type-c-and-micro-usb-ports-led-indicator-black-/p/303296"
    print(f"\n1. Navigating to Croma URL with bypass: {croma_url}")
    try:
        page.goto(croma_url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(3000)
        title = page.title()
        h1 = page.evaluate("() => document.querySelector('h1')?.innerText")
        print(f"   Page Title: {title}")
        print(f"   H1: {h1}")
        
        # Check specs on page
        specs = page.evaluate('''() => {
            const data = {};
            const rows = document.querySelectorAll('li, tr, .cp-specification tr, .spec-table tr');
            rows.forEach(r => {
                const text = r.innerText.replace(/\\s+/g, ' ').trim();
                if (text.includes(':') || text.includes('mAh') || text.includes('Output')) {
                    const parts = text.split(/[:|\\t]/);
                    if (parts.length >= 2) {
                        data[parts[0].trim()] = parts[1].trim();
                    }
                }
            });
            return data;
        }''')
        print(f"   Extracted Specs ({len(specs)} entries):", list(specs.items())[:6])
    except Exception as e:
        print(f"   Croma Error: {e}")

    # 2. Test Flipkart known URL
    fk_url = "https://www.flipkart.com/stuffcool-major-10000-mah-power-bank-22-5-w-fast-charging/p/itm53cf73bb3aef2"
    print(f"\n2. Navigating to Flipkart URL with bypass: {fk_url}")
    try:
        page.goto(fk_url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(3000)
        fk_title = page.title()
        fk_h1 = page.evaluate("() => document.querySelector('h1, span.B_NuCI')?.innerText")
        print(f"   Flipkart Page Title: {fk_title}")
        print(f"   Flipkart H1: {fk_h1}")
        
        fk_specs = page.evaluate('''() => {
            const data = {};
            document.querySelectorAll('table tr, div._3k-BhJ tr, div._1q8noS tr').forEach(r => {
                const tds = r.querySelectorAll('td');
                if (tds.length >= 2) {
                    data[tds[0].innerText.trim()] = tds[1].innerText.trim();
                }
            });
            return data;
        }''')
        print(f"   Flipkart Specs ({len(fk_specs)} entries):", list(fk_specs.items())[:6])
        
        # Flipkart Image
        fk_img = page.evaluate('''() => {
            const img = document.querySelector('img._396cs4, img.DByuf4, img._2r_T1I, img[src*="image"]');
            return img ? img.src : null;
        }''')
        print(f"   Flipkart Master Image: {fk_img}")
    except Exception as e:
        print(f"   Flipkart Error: {e}")

    browser.close()
