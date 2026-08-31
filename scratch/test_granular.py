import os
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1200, "height": 800})
    page.goto("file://" + os.path.abspath("scratch/measure_test.html"))
    page.wait_for_load_state("networkidle")

    print(f"{'CHARS':<6} | {'LINES':<6} | {'HEIGHT':<8} | STATUS | TEXT")
    print("-" * 80)

    # Test exact character lengths from 60 to 72 with varying word lengths
    test_cases = [
        "Charges your smartphones and laptops with high speed power", # 58
        "Charges your smartphones and laptops with high speed power now", # 62
        "Charges your smartphones and laptops with high speed power fast", # 63
        "Charges your smartphones and laptops with high speed power quick", # 64
        "Charges your smartphones and laptops with high speed power output", # 65
        "Charges your smartphones and laptops with high speed power outputs", # 66
        "Charges your smartphones and laptops with high speed power delivery", # 67
        "Charges your smartphones and laptops with high speed power delivers", # 68
        "Charges your smartphones and laptops with high speed power delivering", # 70
        "Charges your smartphones and laptops with high speed power fast delivery", # 73
    ]

    for tc in test_cases:
        c_len = len(tc)
        data = page.evaluate("""(text) => {
            const container = document.getElementById('feats-container');
            container.innerHTML = `<li class="feat" id="test-feat">${text}</li><li class="feat">Dummy 2</li>`;
            const feat = document.getElementById('test-feat');
            const rect = feat.getBoundingClientRect();
            const computed = window.getComputedStyle(feat);
            const lineHeight = parseFloat(computed.lineHeight);
            const lineCount = Math.round(rect.height / lineHeight);
            return { height: rect.height, lineCount: lineCount };
        }""", tc)
        status_symbol = "2 LINES [OK]" if data['lineCount'] <= 2 else "3 LINES [WRAPPED]"
        print(f"{c_len:<6} | {data['lineCount']:<6} | {data['height']:<8.1f} | {status_symbol:<17} | {tc}")

    browser.close()
