import os
import sys
import subprocess
import json
import re
from bs4 import BeautifulSoup
from urllib.parse import unquote

print("=== 1. Testing DuckDuckGo via curl ===")
query = "site:croma.com Stuffcool Major 10000 mAh"
curl_cmd = [
    "curl", "-s", "-L",
    "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    f"https://html.duckduckgo.com/html/?q={query.replace(' ', '+')}"
]

try:
    res = subprocess.run(curl_cmd, capture_output=True, text=True, timeout=10)
    print("Curl returncode:", res.returncode, "Length:", len(res.stdout))
    soup = BeautifulSoup(res.stdout, "html.parser")
    urls = []
    for a in soup.find_all("a", class_="result__url"):
        raw_href = a.get("href", "")
        # Extract uddg target
        m = re.search(r'uddg=([^&]+)', raw_href)
        if m:
            actual = unquote(m.group(1))
            urls.append(actual)
        elif raw_href.startswith("http"):
            urls.append(raw_href)
    print(f"Curl DDG extracted {len(urls)} URLs:", urls[:3])
except Exception as e:
    print("Curl error:", e)

print("\n=== 2. Testing DuckDuckGo via Playwright with real browser session ===")
try:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
        page.goto("https://duckduckgo.com/?q=site%3Acroma.com+Stuffcool+Major+10000+mAh", wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(2000)
        links = page.evaluate('''() => {
            return Array.from(document.querySelectorAll('a[data-testid="result-title-a"], a.result__url, a.result__a, article a'))
                .map(a => ({href: a.href, text: a.innerText}))
                .filter(item => item.href && item.href.includes('croma.com'));
        }''')
        print(f"Playwright DDG found {len(links)} links:")
        for l in links[:3]:
            print(f"  • {l['href']} -> {l['text']}")
        browser.close()
except Exception as e:
    print("Playwright DDG error:", e)
