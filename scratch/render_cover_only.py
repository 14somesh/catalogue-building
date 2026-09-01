import os
import sys
import yaml
import base64
import mimetypes
import importlib
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

build_mod = importlib.import_module("src.4_build")
image_to_base64 = build_mod.image_to_base64
load_self_hosted_fonts_css = build_mod.load_self_hosted_fonts_css
load_config = build_mod.load_config

def render_cover():
    config = load_config("config.yaml")
    
    with open("styles/tokens.css", "r", encoding="utf-8") as f:
        tokens_css = f.read()
    with open("styles/layout.css", "r", encoding="utf-8") as f:
        layout_css = f.read()
    fonts_css = load_self_hosted_fonts_css()

    logo_path = "assets/vianet-logo.png" if os.path.exists("assets/vianet-logo.png") else "images/vianet-logo.png"
    logo_mark_url = image_to_base64(logo_path)
    
    category_cfg = config.get("category", {})
    category_name = category_cfg.get("name", "POWERBANK")
    category_display_title = category_cfg.get("display_title", "Powerbanks & Portable Chargers")
    
    env = Environment(loader=FileSystemLoader("templates"))
    
    # Load cover_hook template directly into content
    cover_template = env.get_template("components/cover_hook.html")
    cover_html = cover_template.render(
        logo_mark_url=logo_mark_url,
        category=category_name
    )
    
    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Cover Page Preview</title>
  <style>
    {fonts_css}
    {tokens_css}
    {layout_css}
    html, body {{
      margin: 0 !important;
      padding: 0 !important;
      background: #1B3A47 !important;
      background-color: #1B3A47 !important;
      width: 794px;
      height: 1123px;
      overflow: hidden;
    }}
    .page--cover {{
      width: 794px !important;
      height: 1123px !important;
      min-height: 1123px !important;
      max-height: 1123px !important;
    }}
  </style>
</head>
<body>
  {cover_html}
</body>
</html>"""

    os.makedirs("dist", exist_ok=True)
    preview_html_path = "dist/cover_preview.html"
    with open(preview_html_path, "w", encoding="utf-8") as f:
        f.write(full_html)
    print(f"Saved cover preview HTML to {preview_html_path}")

    screenshot_path = "dist/cover_page_preview.png"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 794, "height": 1123},
            device_scale_factor=2
        )
        page = context.new_page()
        page.goto(f"file:///{os.path.abspath(preview_html_path)}", wait_until="networkidle")
        page.screenshot(path=screenshot_path)
        browser.close()
        
    print(f"Rendered cover screenshot to {screenshot_path}")

if __name__ == "__main__":
    render_cover()
