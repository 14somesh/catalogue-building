import os
from playwright.sync_api import sync_playwright

test_phrases = [
    "Comprehensive multi-layer safety protection against short circuits.", # 67
    "Simultaneous multi-device fast charging with intelligent power distribution.", # 76
    "Charges your smartphones laptops and tablets with high speed power delivery output.", # 83
    "High-speed 65W Power Delivery charges laptops and smartphones rapidly.", # 70
    "Ultra-compact lightweight pocket design with digital battery display.", # 69
    "Universal compatibility with iOS, Android, laptops, tablets, and audio.", # 71
    "Massive 20000mAh capacity provides up to four full smartphone charges.", # 70
    "Fast 22.5W QC3.0 and PD3.0 charging powers all mobile devices quickly.", # 69
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1200, "height": 800})
    page.goto("file://" + os.path.abspath("scratch/measure_test.html"))
    page.wait_for_load_state("networkidle")

    print(f"{'CHARS':<6} | {'LINES':<6} | {'HEIGHT':<8} | STATUS | TEXT")
    print("-" * 80)

    for phrase in test_phrases:
        words = phrase.split()
        for i in range(3, len(words) + 1):
            sub = " ".join(words[:i])
            c_len = len(sub)
            data = page.evaluate("""(text) => {
                const container = document.getElementById('feats-container');
                container.innerHTML = `<li class="feat" id="test-feat">${text}</li><li class="feat">Dummy 2</li>`;
                const feat = document.getElementById('test-feat');
                const rect = feat.getBoundingClientRect();
                const computed = window.getComputedStyle(feat);
                const lineHeight = parseFloat(computed.lineHeight);
                const lineCount = Math.round(rect.height / lineHeight);
                return { height: rect.height, lineCount: lineCount };
            }""", sub)
            
            if 50 <= c_len <= 78:
                status_symbol = "2 LINES [OK]" if data['lineCount'] <= 2 else "3 LINES [WRAPPED]"
                print(f"{c_len:<6} | {data['lineCount']:<6} | {data['height']:<8.1f} | {status_symbol:<17} | {sub}")

    browser.close()
