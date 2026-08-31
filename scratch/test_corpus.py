import os
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1200, "height": 800})
    page.goto("file://" + os.path.abspath("scratch/measure_test.html"))
    page.wait_for_load_state("networkidle")

    # Generate various realistic product bullets at lengths 55, 58, 60, 62, 64, 65
    test_corpus = [
        "Smallest 5000mAh magnetic wireless powerbank for iPhone.", # 56
        "15W magnetic wireless charging for iPhone 12 and above.", # 55
        "20W PD wired charging delivers 50% in 30 minutes.", # 49
        "Breathable LED indicator shows power level at a glance.", # 55
        "22.5W QC3.0 USB-A port for Android fast charging.", # 49
        "Four ports charge multiple devices at once simultaneously.", # 58
        "Compact 20,000mAh capacity in an ultra-compact travel design.", # 60
        "20W Type-C PD charges iPhone to 50% in 30 mins.", # 47
        "BIS-certified & Made in India for ultimate safety standards.", # 60
        "Built-in 65W Type-C cable charges MacBooks and laptops.", # 55
        "20,000mAh in one of the smallest bodies in its class.", # 53
        "Supports 45W Samsung Super Fast Charging 2.0.", # 45
        "45W fast input recharges the powerbank quickly.", # 47
        "Pass-through charging powers device and powerbank together.", # 59
        "Aircraft-grade aluminum alloy body with matte finish.", # 53
        "Intelligent LED display shows accurate battery percentage.", # 58
        "Dual input ports allow flexible Type-C and Micro charging.", # 58
        "Lightweight pocket-sized design weighs just 185 grams.", # 54
        "Advanced multi-protection against overcharging and heat.", # 56
        "Fast 22.5W charging for OnePlus, Vivo and Samsung phones.", # 57
    ]

    print(f"{'CHARS':<6} | {'LINES':<6} | {'HEIGHT':<8} | STATUS | TEXT")
    print("-" * 80)
    
    wrap_3_count = 0
    wrap_2_count = 0
    for bullet in test_corpus:
        c_len = len(bullet)
        data = page.evaluate("""(text) => {
            const container = document.getElementById('feats-container');
            container.innerHTML = `<li class="feat" id="test-feat">${text}</li><li class="feat">Dummy 2</li>`;
            const feat = document.getElementById('test-feat');
            const rect = feat.getBoundingClientRect();
            const computed = window.getComputedStyle(feat);
            const lineHeight = parseFloat(computed.lineHeight);
            const lineCount = Math.round(rect.height / lineHeight);
            return { height: rect.height, lineCount: lineCount };
        }""", bullet)
        if data['lineCount'] > 2:
            wrap_3_count += 1
            status = "3 LINES [FAIL]"
        else:
            wrap_2_count += 1
            status = "2 LINES [OK]"
        print(f"{c_len:<6} | {data['lineCount']:<6} | {data['height']:<8.1f} | {status:<15} | {bullet}")

    print("-" * 80)
    print(f"Summary: {wrap_2_count} passed (<= 2 lines), {wrap_3_count} failed (> 2 lines)")
    browser.close()
