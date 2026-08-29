import os
import re
import sys
import yaml
import base64
import mimetypes
from datetime import datetime
from PIL import Image
import numpy as np
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.excel_handler import (
    load_catalogue_data,
    get_effective_product_dict,
    check_file_lock,
    slugify
)
from src.utils.logger import setup_logger

logger = setup_logger("build")


def image_to_base64(filepath: str) -> str:
    """Converts a local image file to a base64 Data URI for deterministic PDF rendering."""
    if not os.path.exists(filepath):
        return ""
    mime_type, _ = mimetypes.guess_type(filepath)
    if not mime_type:
        mime_type = "image/png"
    with open(filepath, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def load_self_hosted_fonts_css() -> str:
    """Reads self-hosted fonts.css and embeds font files as base64 data URIs for offline PDF compilation."""
    fonts_css_path = "fonts/fonts.css"
    if not os.path.exists(fonts_css_path):
        return ""
    with open(fonts_css_path, "r", encoding="utf-8") as f:
        css = f.read()

    def replace_font_url(match):
        filename = match.group(1).strip("'\"")
        font_path = os.path.join("fonts", filename)
        if os.path.exists(font_path):
            with open(font_path, "rb") as ff:
                b64 = base64.b64encode(ff.read()).decode("utf-8")
            return f"url(data:font/woff2;base64,{b64})"
        return match.group(0)

    embedded_css = re.sub(r"url\(([^)]+\.woff2)\)", replace_font_url, css)
    return embedded_css


def load_config(config_path: str = "config.yaml") -> dict:
    """Loads configuration settings from config.yaml."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_output_pdf_path(config: dict) -> str:
    """
    Resolves the output PDF file path using category name and current timestamp.
    Configurable via paths.output_filename_pattern in config.yaml.
    """
    paths_cfg = config.get("paths", {})
    output_dir = paths_cfg.get("output_dir", "dist/")
    pattern = paths_cfg.get("output_filename_pattern", "{category}_catalogue_{timestamp}.pdf")
    
    category_raw = config.get("category", {}).get("name", "catalogue")
    category_slug = slugify(category_raw)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    
    filename = pattern.format(category=category_slug, timestamp=timestamp)
    return os.path.join(output_dir, filename)


def format_name_html(model_name: str) -> str:
    """
    Accent the trailing word ONLY when a real split exists.
    Single-word names (aura, giga, lucid) stay solid ink.
    Multi-word names (click 10, major ultra) accent trailing word in <em>.
    """
    name = str(model_name).strip().lower()
    parts = name.split()
    if len(parts) < 2:
        return name
    head, tail = " ".join(parts[:-1]), parts[-1]
    return f"{head} <em>{tail}</em>"


SPEC = re.compile(r'\b\d{1,3}(?:,\d{3})*(?:\.\d+)?\s*(?:mAh|W|V|A)\b', re.I)


def format_subtitle_html(subtitle: str) -> str:
    """Accent the FIRST spec figure only in crimson."""
    if not subtitle:
        return ""
    m = SPEC.search(subtitle)
    if not m:
        return subtitle
    s, e = m.span()
    return f'{subtitle[:s]}<b class="spec">{subtitle[s:e]}</b>{subtitle[e:]}'


def validate_product_data(prod: dict, raw_row: dict) -> None:
    """
    SECTION A — Build-time validation rules enforced automatically.
    Fails loudly before PDF compilation if data integrity issues exist.
    """
    pid = prod.get("product_id") or raw_row.get("Product_ID") or "UNKNOWN"
    model = str(prod.get("model_name", "")).strip()
    brand = str(prod.get("brand", "")).strip()
    subtitle = str(prod.get("subtitle", "")).strip()
    bullets = prod.get("bullets", [])
    raw_img_path = prod.get("image_full_path", "")
    price_val = prod.get("mrp_raw") or prod.get("mrp")

    # 1. All required fields non-empty before rendering
    if not model:
        raise ValueError(f"Build validation failed: Product {pid} is missing 'Model_Name'")
    if not brand:
        raise ValueError(f"Build validation failed: Product {pid} is missing 'Brand'")
    if not subtitle:
        raise ValueError(f"Build validation failed: Product {pid} ({model}) is missing 'Subtitle'")
    if not bullets or len(bullets) == 0:
        raise ValueError(f"Build validation failed: Product {pid} ({model}) has no bullets defined")
    if not raw_img_path:
        raise ValueError(f"Build validation failed: Product {pid} ({model}) is missing 'Local_Image_Path'")
    if price_val is None or str(price_val).strip() in ("", "nan", "None"):
        raise ValueError(f"Build validation failed: Product {pid} ({model}) is missing pricing/MRP field")

    # 2. Every product has an image file present at the expected path
    if not os.path.exists(raw_img_path) or os.path.getsize(raw_img_path) == 0:
        raise FileNotFoundError(
            f"Build validation failed: Product {pid} ({model}) image file not found or empty at '{raw_img_path}'"
        )

    # 3. No collapsed or doubled spaces in product names or between number and name
    if "  " in model:
        raise ValueError(f"Build validation failed: Product {pid} Model_Name contains double spaces: '{model}'")
    if model.startswith(" ") or model.endswith(" "):
        raise ValueError(f"Build validation failed: Product {pid} Model_Name has leading/trailing spaces: '{model}'")
    
    formatted_name = format_name_html(model)
    if "  " in formatted_name:
        raise ValueError(f"Build validation failed: Product {pid} formatted name HTML contains double spaces: '{formatted_name}'")
    if re.search(r'\w<em>', formatted_name) or re.search(r'</em>\w', formatted_name):
        raise ValueError(f"Build validation failed: Product {pid} formatted name HTML has collapsed space before/after <em>: '{formatted_name}'")

    # 4. No letter-spacing applied that breaks words apart
    for field_name, field_val in [("Model_Name", model), ("Brand", brand), ("Subtitle", subtitle)]:
        if re.search(r'\b(?:[A-Za-z]\s+){3,}[A-Za-z]\b', str(field_val)):
            raise ValueError(f"Build validation failed: Product {pid} {field_name} contains broken spaced-out letters: '{field_val}'")


def build_catalogue_pdf(config_path: str = "config.yaml") -> str:
    """
    Main PDF builder: reads Excel, validates data against Section A rules,
    arranges 2-products-per-page with 1-up odd remainder, renders Jinja2 templates,
    and compiles print-ready A4 PDF via Playwright.
    """
    config = load_config(config_path)
    excel_path = config.get("paths", {}).get("excel_file", "data/catalogue_data.xlsx")
    output_pdf = resolve_output_pdf_path(config)
    
    os.makedirs(os.path.dirname(output_pdf), exist_ok=True)
    check_file_lock(excel_path)
    
    logger.info(f"Reading master dataset from {excel_path}...")
    df = load_catalogue_data(excel_path)
    
    # 1. Determine brand sequence
    config_brand_order = config.get("brand_order") or []
    sheet_brands = df["Brand"].dropna().unique().tolist()
    if config_brand_order:
        ordered_brands = [b for b in config_brand_order if b in sheet_brands]
        for b in sheet_brands:
            if b not in ordered_brands:
                ordered_brands.append(b)
    else:
        ordered_brands = sheet_brands

    logger.info(f"Brand ordering sequence: {ordered_brands}")

    # 2. Resolve logo
    logo_path = "images/vianet-logo.png"
    logo_mark_url = image_to_base64(logo_path)

    category_cfg = config.get("category", {})
    category_name = category_cfg.get("name", "POWERBANK")
    category_display_title = category_cfg.get("display_title", "Powerbanks & Portable Chargers")

    # 3. Group products by brand and build 2-up paginated structure
    brand_groups = []
    
    for brand_name in ordered_brands:
        brand_df = df[df["Brand"] == brand_name]
        if brand_df.empty:
            continue
            
        products = []
        for idx_in_brand, (_, row) in enumerate(brand_df.iterrows(), 1):
            prod = get_effective_product_dict(row)
            validate_product_data(prod, row.to_dict())
            
            # Resolve product image
            raw_img_path = prod.get("image_full_path", "")
            image_b64 = image_to_base64(raw_img_path) if os.path.exists(raw_img_path) else ""
            
            # Format price
            mrp_raw = prod.get("mrp_raw")
            if mrp_raw is not None:
                price_str = f"{int(mrp_raw):,}"
            else:
                mrp_val = str(prod.get("mrp", "TBD")).replace("₹", "").replace("MRP", "").strip()
                price_str = mrp_val if mrp_val else "TBD"

            raw_model = prod.get("model_name", "")
            subtitle_val = prod.get("subtitle", "")
            bullets_list = prod.get("bullets", [])

            prod_ctx = {
                "index": f"{idx_in_brand:02d}",
                "brand": brand_name,
                "series": "SERIES",
                "name": raw_model,
                "name_html": format_name_html(raw_model),
                "subtitle_html": format_subtitle_html(subtitle_val),
                "image_url": image_b64,
                "bullets": bullets_list,
                "price": price_str,
                "category": category_name,
            }
            products.append(prod_ctx)
            
        # Create pages (2-up per page, with 1-up for odd remainder)
        pages = []
        product_count = len(products)
        
        for i in range(0, product_count, 2):
            chunk = products[i:i+2]
            start_index = i + 1
            
            if len(chunk) == 2:
                pages.append({
                    "type": "2-up",
                    "products": chunk,
                    "start_index": start_index
                })
            else:
                pages.append({
                    "type": "1-up",
                    "products": chunk,
                    "start_index": start_index
                })
                
        brand_groups.append({
            "brand": brand_name,
            "brand_slug": slugify(brand_name),
            "pages": pages,
            "total_products": product_count,
            "total_pages": len(pages)
        })

    # 4. Read CSS styles & self-hosted fonts
    with open("styles/tokens.css", "r", encoding="utf-8") as f:
        tokens_css = f.read()
    with open("styles/layout.css", "r", encoding="utf-8") as f:
        layout_css = f.read()
    fonts_css = load_self_hosted_fonts_css()

    # 5. Render Jinja2 template
    env = Environment(loader=FileSystemLoader("templates"))
    template = env.get_template("catalogue.html")
    
    company_cfg = config.get("company", {})
    company_name = company_cfg.get("name", "VIANET")
    company_location = company_cfg.get("location", "PUNE")
    contact_line = company_cfg.get("contact_line", "Corporate gifting · Pune · sales@vianet.co.in")

    cover_img_path = "images/cover.png"
    cover_image_url = image_to_base64(cover_img_path) if os.path.exists(cover_img_path) else None

    rendered_html = template.render(
        fonts_css=fonts_css,
        tokens_css=tokens_css,
        layout_css=layout_css,
        category=category_name,
        category_display_title=category_display_title,
        company_name=company_name,
        company_location=company_location,
        contact_line=contact_line,
        email=company_cfg.get("email", "sales@vianet.co.in"),
        brand_groups=brand_groups,
        logo_mark_url=logo_mark_url,
        cover_image_url=cover_image_url
    )

    preview_html_path = os.path.abspath("dist/catalogue_preview.html")
    with open(preview_html_path, "w", encoding="utf-8") as f:
        f.write(rendered_html)
    logger.info(f"Saved HTML preview to {preview_html_path}")

    # 6. Compile PDF with Playwright Chromium
    logger.info(f"Compiling PDF via Playwright Headless Chromium to {output_pdf}...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1200, "height": 1697},
            device_scale_factor=2
        )
        page = context.new_page()
        page.goto(f"file:///{preview_html_path.replace(os.sep, '/')}", wait_until="networkidle")
        
        # Wait for self-hosted fonts
        page.evaluate("() => document.fonts.ready")
        
        # Wait for auto-shrink JS to fit all product names
        page.wait_for_function("window.__namesFitted === true", timeout=15000)
        
        # Small stabilization timeout
        page.wait_for_timeout(300)
        
        # Render PDF
        page.pdf(
            path=output_pdf,
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
            margin={"top": "0mm", "bottom": "0mm", "left": "0mm", "right": "0mm"}
        )
        browser.close()

    logger.info(f"PDF build complete: {output_pdf}")
    return output_pdf


if __name__ == "__main__":
    build_catalogue_pdf()
