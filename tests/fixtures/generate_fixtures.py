import os
import json
import hashlib

WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CACHE_DIR = os.path.join(WORKSPACE_ROOT, "cache")
FIXTURES_PATH = os.path.join(WORKSPACE_ROOT, "tests", "fixtures", "http_fixtures.json")

def generate_fixtures():
    fixtures = {
        "cache_files": {},
        "urls": {}
    }

    # 1. Load all cached responses from cache/
    if os.path.exists(CACHE_DIR):
        for fname in os.listdir(CACHE_DIR):
            if fname.endswith(".json"):
                fpath = os.path.join(CACHE_DIR, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        fixtures["cache_files"][fname] = json.load(f)
                except Exception as e:
                    print(f"Warning: could not load {fname}: {e}")

    # 2. Add EVM sitemap XML
    evm_sitemap = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        '  <url><loc>https://evmzone.com/evm-products/encharge.html</loc></url>\n'
        '  <url><loc>https://evmzone.com/evm-products/powerbank/enbolt-power-bank-10000mah.html</loc></url>\n'
        '  <url><loc>https://evmzone.com/enmag-ace-power-bank-evm-p0301.html</loc></url>\n'
        '  <url><loc>https://evmzone.com/evm-products/powerbank/encase-power-bank.html</loc></url>\n'
        '  <url><loc>https://evmzone.com/evm-products/powerbank/enmove.html</loc></url>\n'
        '</urlset>'
    )

    fixtures["urls"]["https://evmzone.com/sitemap.xml"] = {"text": evm_sitemap, "status_code": 200}
    fixtures["urls"]["https://www.evmzone.com/sitemap.xml"] = {"text": evm_sitemap, "status_code": 200}
    fixtures["urls"]["https://evmzone.com/sitemap-products.xml"] = {"text": evm_sitemap, "status_code": 200}
    fixtures["urls"]["https://www.evmzone.com/sitemap-products.xml"] = {"text": evm_sitemap, "status_code": 200}

    # Write out
    with open(FIXTURES_PATH, "w", encoding="utf-8") as f:
        json.dump(fixtures, f, indent=2)

    print(f"Successfully generated {FIXTURES_PATH} with {len(fixtures['cache_files'])} cache files and {len(fixtures['urls'])} explicit URLs.")

if __name__ == "__main__":
    generate_fixtures()
