#!/usr/bin/env python3
"""
LEVEL 3: OFFLINE END-TO-END SMOKE TEST
Runs the real pipeline (collect -> specs -> images -> review -> approve -> PDF build) for two synthetic brands
that cover both storefront paths, entirely offline:
  Zylo  (TWS, custom / non-Shopify site)  -> GenericParser path
  Volto (Powerbank, Shopify site)          -> ShopifyParser path

Isolation: the needed project folders are copied into a temporary directory and the pipeline runs there in a
separate Python process, so real data/, config/, images/ and dist/ are never touched.
Network and AI providers are replaced with in-memory fakes (no API keys or internet needed).

Usage:
  python tests/e2e_smoke.py            # run and report (exit code 0 = pass)
Optional:
  E2E_CHROMIUM_PATH=/path/to/chrome     # only if Playwright's bundled Chromium is unavailable
"""
import io
import os
import sys
import json
import shutil
import tempfile
import subprocess
import traceback
from typing import List, Tuple

WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
COPY_ITEMS = ["src", "config", "config.yaml", "data", "templates", "styles", "fonts", "assets", "tests/e2e_smoke.py"]


# ----------------------------------------------------------------------------------------------
# Parent side: build isolated copy, run child process, return verdict
# ----------------------------------------------------------------------------------------------
def run_e2e_smoke(timeout: int = 600) -> Tuple[bool, List[str]]:
    tmp = tempfile.mkdtemp(prefix="catalogue_e2e_")
    try:
        for item in COPY_ITEMS:
            src_path = os.path.join(WORKSPACE_ROOT, item)
            dst_path = os.path.join(tmp, item)
            if not os.path.exists(src_path):
                return False, [f"Required project item missing: {item}"]
            os.makedirs(os.path.dirname(dst_path) or tmp, exist_ok=True)
            if os.path.isdir(src_path):
                shutil.copytree(src_path, dst_path, ignore=shutil.ignore_patterns("__pycache__"))
            else:
                shutil.copy2(src_path, dst_path)
        for d in ("images", "dist", "logs", "cache"):
            os.makedirs(os.path.join(tmp, d), exist_ok=True)

        proc = subprocess.run(
            [sys.executable, os.path.join("tests", "e2e_smoke.py"), "--child"],
            cwd=tmp, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
            env={**os.environ, "PYTHONPATH": tmp, "PYTHONIOENCODING": "utf-8"}
        )
        result_lines = [l[len("E2E_RESULT "):] for l in proc.stdout.splitlines() if l.startswith("E2E_RESULT ")]
        if not result_lines:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-15:]
            return False, ["End-to-end run crashed before reporting:"] + tail
        verdict = json.loads(result_lines[-1])
        return verdict["ok"], verdict["messages"]
    except subprocess.TimeoutExpired:
        return False, [f"End-to-end run exceeded {timeout}s"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------------------------------
# Child side: fakes + pipeline run (executes inside the temporary copy)
# ----------------------------------------------------------------------------------------------
_PNGS = {}


def _png_bytes(url: str) -> bytes:
    if url not in _PNGS:
        import hashlib
        from PIL import Image, ImageDraw
        h = hashlib.md5(url.encode()).digest()
        img = Image.new("RGB", (900, 900), (255, 255, 255))
        ImageDraw.Draw(img).ellipse((250, 250, 650, 650), fill=(h[0], h[1], h[2]))
        b = io.BytesIO()
        img.save(b, "PNG")
        _PNGS[url] = b.getvalue()
    return _PNGS[url]


class _Resp:
    def __init__(self, status=200, text="", content=None, ctype="text/html"):
        self.status_code = status
        self.text = text
        self.content = content if content is not None else text.encode()
        self.headers = {"Content-Type": ctype}
        self.url = ""
        self.ok = status < 400

    def json(self):
        return json.loads(self.text)

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))

    def iter_content(self, chunk_size=8192):
        yield self.content


ZYLO_PRODUCT = """<html><head><title>Zylo Air 2 Wireless Earbuds</title>
<script type="application/ld+json">{"@context":"https://schema.org","@type":"Product","name":"Zylo Air 2",
"image":["https://www.zylo-audio.in/media/zylo-air-2-main.png"],
"offers":{"@type":"Offer","price":"2999","priceCurrency":"INR"}}</script></head><body>
<h1 class="product-title">Zylo Air 2</h1>
<div class="product-description">Zylo Air 2 true wireless earbuds with up to 40 hours total playtime with charging case.
13mm dynamic drivers for deep bass. Bluetooth 5.3 with low latency gaming mode. Active Noise Cancellation up to 32dB.
IPX5 water resistance. Quad mic ENC for clear calls. 1 year warranty.</div>
<img src="https://www.zylo-audio.in/media/zylo-air-2-main.png" alt="Zylo Air 2"></body></html>"""
ZYLO_HOME = """<html><body><a href="/product/zylo-air-2">Zylo Air 2</a></body></html>"""
ZYLO_SITEMAP = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://www.zylo-audio.in/product/zylo-air-2</loc></url></urlset>"""

VOLTO_PRODUCT = {
    "title": "Volto Max 20K 22.5W Power Bank", "handle": "volto-max-20k", "vendor": "Volto",
    "body_html": "<p>20000 mAh capacity, 22.5W fast charging output, 2 USB-A + 1 Type-C ports, weighs 380g, 1 year warranty.</p>",
    "images": [{"src": "https://cdn.volto.in/volto-max-20k.png"}],
    "variants": [{"price": "1999.00", "compare_at_price": "3499.00"}], "tags": []}
VOLTO_PAGE = """<html><head><title>Volto Max 20K</title></head><body><h1 class="product__title">Volto Max 20K</h1>
<div class="product__description rte">20000 mAh capacity, 22.5W fast charging output, 2 USB-A + 1 Type-C ports,
weighs 380g. Regular price MRP: Rs. 3,499</div>
<img src="https://cdn.volto.in/volto-max-20k.png" alt="Volto Max 20K"></body></html>"""


def _fake_get(url, *a, **k):
    u = str(url)
    ul = u.lower()
    if ul.endswith((".png", ".jpg", ".jpeg", ".webp")) or "/media/" in ul or "cdn.volto.in" in ul:
        return _Resp(200, "", _png_bytes(u), "image/png")
    if "zylo-audio.in" in ul:
        if "products.json" in ul or "suggest.json" in ul:
            return _Resp(404, "Not found")
        if "sitemap" in ul:
            return _Resp(200, ZYLO_SITEMAP, ctype="application/xml")
        if "/product/zylo-air-2" in ul:
            return _Resp(200, ZYLO_PRODUCT)
        return _Resp(200, ZYLO_HOME)
    if "volto.in" in ul:
        if "products.json" in ul:
            return _Resp(200, json.dumps({"products": [VOLTO_PRODUCT]}), ctype="application/json")
        if "suggest.json" in ul:
            return _Resp(200, json.dumps({"resources": {"results": {"products": [
                {"title": VOLTO_PRODUCT["title"], "url": "/products/volto-max-20k"}]}}}), ctype="application/json")
        if "/products/volto-max-20k.json" in ul or "/products/volto-max-20k.js" in ul:
            return _Resp(200, json.dumps({"product": VOLTO_PRODUCT}), ctype="application/json")
        if "/products/volto-max-20k" in ul:
            return _Resp(200, VOLTO_PAGE)
        return _Resp(200, "<html><body>Volto</body></html>")
    return _Resp(404, "<html><title>404 Not Found</title></html>")


def _fake_post(url, *a, **k):
    return _Resp(404, "{}", ctype="application/json")


class _FakeGenaiResp:
    def __init__(self, text):
        self.text = text


class _FakeGenaiModels:
    def generate_content(self, model=None, contents=None, config=None, **k):
        schema = getattr(config, "response_schema", None)
        name = getattr(schema, "__name__", str(schema))
        blob = json.dumps(contents, default=str) if contents is not None else ""
        brand = "Zylo" if "Zylo" in blob else ("Volto" if "Volto" in blob else "Unknown")
        if name == "ProductCopySchema":
            if brand == "Zylo":
                d = {"title": "Air 2", "subtitle": "True wireless earbuds with 40 hours of total playtime.",
                     "bullet_1": "40 hours total playtime with the charging case.",
                     "bullet_2": "13mm dynamic drivers tuned for deep, punchy bass.",
                     "bullet_3": "Bluetooth 5.3 with a low latency gaming mode.",
                     "bullet_4": "Active noise cancellation of up to 32dB depth."}
            else:
                d = {"title": "Max 20K", "subtitle": "20000mAh power bank with 22.5W fast charging output.",
                     "bullet_1": "20000mAh capacity charges a phone several times.",
                     "bullet_2": "22.5W fast charging output for quick top ups.",
                     "bullet_3": "Two USB-A ports and one Type-C port for devices.",
                     "bullet_4": None}
            return _FakeGenaiResp(json.dumps(d))
        if name == "SemanticAuditSchema":
            return _FakeGenaiResp(json.dumps({"is_valid": True, "contradictions": [], "factual_discrepancies": [],
                                              "tone_and_quality_issues": [], "summary_flags": []}))
        if name == "PostRunReviewSchema":
            return _FakeGenaiResp(json.dumps({"is_satisfied": True, "contradictions": [], "capacity_model_mismatch": False,
                                              "copy_mismatch_critique": None, "recommended_action": "pass"}))
        if name == "VisionExtractedSpecsSchema":
            return _FakeGenaiResp(json.dumps({"capacity": None, "output": None, "ports": None, "weight": None}))
        if name == "ImageQualityAuditSchema":
            return _FakeGenaiResp(json.dumps({"is_correct_brand_and_model": True, "is_isolated_packshot": True,
                                              "has_hand_holding": False, "has_promotional_text_banner": False,
                                              "detected_brand": brand, "quality_score": 9, "rejection_reason": None}))
        return _FakeGenaiResp("{}")


class _FakeGenaiClient:
    def __init__(self, *a, **k):
        self.models = _FakeGenaiModels()


def _child_main() -> None:
    sys.path.insert(0, os.getcwd())
    messages: List[str] = []
    ok = True
    try:
        import yaml
        import pandas as pd
        from unittest.mock import patch

        chromium = os.environ.get("E2E_CHROMIUM_PATH")
        if chromium:
            from playwright.sync_api import BrowserType
            _orig_launch = BrowserType.launch

            def _launch(self, *a, **k):
                k.setdefault("executable_path", chromium)
                return _orig_launch(self, *a, **k)
            BrowserType.launch = _launch

        with open("config/brand_defaults.yaml", encoding="utf-8") as f:
            bd = yaml.safe_load(f) or {}
        bd.setdefault("brands", {})
        bd["brands"]["Zylo"] = {"domain": "zylo-audio.in", "platform": "custom", "retail_order": ["reliance"],
                                "categories": {"TWS": {"qualifier_tokens": ["Pro", "Max", "Plus"]}}}
        bd["brands"]["Volto"] = {"domain": "volto.in", "platform": "shopify", "retail_order": ["reliance"],
                                 "categories": {"Powerbank": {"qualifier_tokens": ["Pro", "Max", "Mini"]}}}
        with open("config/brand_defaults.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(bd, f, sort_keys=False)

        from src.utils.excel_handler import load_catalogue_data, save_catalogue_data
        df = load_catalogue_data("data/catalogue_data.xlsx")
        df = pd.concat([df, pd.DataFrame([
            {"Product_ID": "TWS-E2E-001", "Category": "TWS", "Brand": "Zylo", "Model_Name": "Zylo Air 2", "MRP_Input": 2999},
            {"Product_ID": "PB-E2E-001", "Category": "Powerbank", "Brand": "Volto", "Model_Name": "Volto Max 20K", "MRP_Input": 1999},
        ])], ignore_index=True)
        save_catalogue_data(df, "data/catalogue_data.xlsx")

        import src.run_brand as rb
        fakes = [
            patch("requests.get", side_effect=_fake_get),
            patch("requests.post", side_effect=_fake_post),
            patch("requests.Session.get", side_effect=lambda self, url, *a, **k: _fake_get(url, *a, **k)),
            patch.object(rb, "preflight_quota_check", return_value=None),
            patch("src.utils.llm_client.genai.Client", _FakeGenaiClient),
            patch("src.utils.tinyfish.is_tinyfish_configured", return_value=False),
            patch.dict(os.environ, {"GEMINI_API_KEY": "offline-e2e-fake-key"}),
        ]
        for f in fakes:
            f.start()

        expectations = {
            "Zylo": {"category": "TWS", "spec_col": "Raw_Spec_Capacity", "spec_contains": "40"},
            "Volto": {"category": "Powerbank", "spec_col": "Raw_Spec_Capacity", "spec_contains": "20000"},
        }
        # Stages 3 (collect + images + review)
        for brand, exp in expectations.items():
            res = rb.run_brand(brand, category=exp["category"], return_summary=True)
            if isinstance(res, dict) and res.get("halt_reason"):
                ok = False
                messages.append(f"{brand}: collection halted: {res['halt_reason']}")
        df = load_catalogue_data("data/catalogue_data.xlsx")
        for brand, exp in expectations.items():
            r = df[df["Brand"] == brand].iloc[0].to_dict()
            label = f"{brand} ({exp['category']})"
            checks = [
                (r.get("Status") == "Ready_For_Review", f"status is '{r.get('Status')}' (expected Ready_For_Review); flags: {r.get('Flags')}"),
                (str(r.get("Source_URL") or "").startswith("http"), f"no Source_URL collected (got '{r.get('Source_URL')}')"),
                (exp["spec_contains"] in str(r.get(exp["spec_col"]) or ""), f"{exp['spec_col']} = '{r.get(exp['spec_col'])}', expected to contain '{exp['spec_contains']}'"),
                (r.get("Image_Status") == "ok", f"Image_Status = '{r.get('Image_Status')}'"),
                (str(r.get("Display_Name") or "").strip().lower() not in ("", "nan", "none"), f"Display_Name is empty/'nan' ('{r.get('Display_Name')}')"),
            ]
            for passed, why in checks:
                if not passed:
                    ok = False
                    messages.append(f"{label}: {why}")
            if all(p for p, _ in checks):
                messages.append(f"{label}: collected, specs + image OK, Ready_For_Review")

        # Stages 4-5 (approve + build PDF)
        for brand in expectations:
            df.loc[df["Brand"] == brand, "Status"] = "Approved"
        save_catalogue_data(df, "data/catalogue_data.xlsx")
        import importlib
        build_mod = importlib.import_module("src.4_build")
        import pypdfium2 as pdfium
        for brand, exp in expectations.items():
            out = build_mod.build_catalogue(brand=brand, category=exp["category"])
            pages = len(pdfium.PdfDocument(out)) if out and os.path.exists(out) else 0
            if pages < 2:
                ok = False
                messages.append(f"{brand}: PDF build produced {pages} page(s) at '{out}'")
            else:
                messages.append(f"{brand}: PDF built ({pages} pages)")
    except Exception as e:
        ok = False
        tb = traceback.format_exc().strip().splitlines()
        messages.append(f"CRASH: {type(e).__name__}: {e}")
        messages.extend(tb[-6:])
    print("E2E_RESULT " + json.dumps({"ok": ok, "messages": messages}), flush=True)


if __name__ == "__main__":
    if "--child" in sys.argv:
        _child_main()
    else:
        passed, msgs = run_e2e_smoke()
        for m in msgs:
            print(("  ✓ " if passed else "  • ") + m)
        print("E2E SMOKE:", "PASSED" if passed else "FAILED")
        sys.exit(0 if passed else 1)
