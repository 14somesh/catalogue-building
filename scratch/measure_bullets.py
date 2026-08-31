import os
import sys
from playwright.sync_api import sync_playwright

html_template = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <link rel="stylesheet" href="../styles/tokens.css">
  <link rel="stylesheet" href="../styles/layout.css">
  <style>
    @font-face {
      font-family: 'Inter';
      src: local('Inter'), local('Segoe UI');
    }
    body { background: #1B3A47; padding: 20px; font-family: 'Inter', sans-serif; }
    .test-card { width: 443px; background: #F5F2EC; padding: 16px 20px; border-radius: 12px; }
  </style>
</head>
<body>
  <div class="test-card">
    <div class="prod__name">01 test <em>model</em></div>
    <div class="prod__subtitle">Test subtitle for measurement</div>
    <div class="prod__rule"></div>
    <ul class="feats" id="feats-container">
    </ul>
  </div>
</body>
</html>
"""

os.makedirs("scratch", exist_ok=True)
with open("scratch/measure_test.html", "w", encoding="utf-8") as f:
    f.write(html_template)

sample_sentences = [
    "Built-in 65W Type-C cable charges MacBooks and laptops.", # 55 chars
    "20,000mAh in one of the smallest bodies in its class.", # 53 chars
    "Supports 45W Samsung Super Fast Charging 2.0.", # 45 chars
    "45W fast input recharges the powerbank quickly.", # 47 chars
    "Smallest 5000mAh magnetic wireless powerbank for iPhone.", # 56 chars
    "15W magnetic wireless charging for iPhone 12 and above.", # 55 chars
    "20W PD wired charging delivers 50% in 30 minutes.", # 49 chars
    "Breathable LED indicator shows power level at a glance.", # 55 chars
    "22.5W QC3.0 USB-A port for Android fast charging.", # 49 chars
    "Four ports charge multiple devices at once simultaneously.", # 58 chars
    "Compact 20,000mAh capacity in an ultra-compact travel design.", # 60 chars
    "20W Type-C PD charges iPhone to 50% in 30 mins.", # 47 chars
    "BIS-certified & Made in India for ultimate safety standards.", # 60 chars
    "High-speed 65W Power Delivery charges laptops and smartphones rapidly.", # 70 chars
    "Super fast 22.5W charging charges your compatible smartphone quickly.", # 69 chars
    "Ultra-compact lightweight pocket design with digital battery display.", # 69 chars
    "Comprehensive multi-layer safety protection against short circuits.", # 67 chars
    "Simultaneous multi-device fast charging with intelligent power distribution.", # 76 chars
    "Advanced GaN technology ensures cooler and more efficient power delivery.", # 73 chars
    "Massive 20000mAh capacity provides up to four full smartphone charges.", # 70 chars
    "Universal compatibility with iOS, Android, laptops, tablets, and audio.", # 71 chars
]

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1200, "height": 800})
    page.goto("file://" + os.path.abspath("scratch/measure_test.html"))
    page.wait_for_load_state("networkidle")

    results = []
    
    # Test each sample sentence and test increments of length
    for s in sample_sentences:
        char_len = len(s)
        # Evaluate line count and bounding box in DOM
        data = page.evaluate("""(text) => {
            const container = document.getElementById('feats-container');
            container.innerHTML = `<li class="feat" id="test-feat">${text}</li><li class="feat">Dummy 2</li>`;
            const feat = document.getElementById('test-feat');
            const rect = feat.getBoundingClientRect();
            const computed = window.getComputedStyle(feat);
            const lineHeight = parseFloat(computed.lineHeight);
            const lineCount = Math.round(rect.height / lineHeight);
            return {
                width: rect.width,
                height: rect.height,
                lineHeight: lineHeight,
                lineCount: lineCount,
                textWidth: feat.scrollWidth
            };
        }""", s)
        results.append({
            "text": s,
            "chars": char_len,
            "lines": data["lineCount"],
            "height": data["height"],
            "lineHeight": data["lineHeight"]
        })

    # Now let's systematically test exact character boundaries from 40 to 90 characters
    boundary_results = []
    base_text = "Charges your smartphones laptops and tablets with high speed power delivery output."
    for length in range(40, 95, 2):
        test_str = base_text[:length].rsplit(" ", 1)[0]
        actual_len = len(test_str)
        data = page.evaluate("""(text) => {
            const container = document.getElementById('feats-container');
            container.innerHTML = `<li class="feat" id="test-feat">${text}</li><li class="feat">Dummy 2</li>`;
            const feat = document.getElementById('test-feat');
            const rect = feat.getBoundingClientRect();
            const computed = window.getComputedStyle(feat);
            const lineHeight = parseFloat(computed.lineHeight);
            const lineCount = Math.round(rect.height / lineHeight);
            return {
                height: rect.height,
                lineHeight: lineHeight,
                lineCount: lineCount
            };
        }""", test_str)
        boundary_results.append({
            "chars": actual_len,
            "lines": data["lineCount"],
            "height": data["height"],
            "text": test_str
        })

    browser.close()

print(f"{'CHARS':<8} | {'LINES':<8} | {'HEIGHT':<8} | {'TEXT'}")
print("-" * 80)
for r in results:
    print(f"{r['chars']:<8} | {r['lines']:<8} | {r['height']:<8.1f} | {r['text']}")

print("\n" + "=" * 80)
print("SYSTEMATIC LENGTH BOUNDARY TEST (Max chars before Line 3 wrap)")
print("=" * 80)
print(f"{'CHARS':<8} | {'LINES':<8} | {'HEIGHT':<8} | {'TEXT'}")
print("-" * 80)
seen_lens = set()
for b in boundary_results:
    if b['chars'] not in seen_lens:
        seen_lens.add(b['chars'])
        print(f"{b['chars']:<8} | {b['lines']:<8} | {b['height']:<8.1f} | {b['text']}")
