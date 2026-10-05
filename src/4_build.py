import os
import re
import sys
import yaml
import base64
import mimetypes
from datetime import datetime
from typing import Optional, Callable, Dict, Any
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
    slugify,
    is_empty_value
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


def resolve_output_pdf_path(config: dict, brand: Optional[str] = None, category: Optional[str] = None) -> str:
    """
    Resolves the output PDF file path using category name and current timestamp.
    Writes to dist/{category_slug}/{brand_slug}/ when brand is provided.
    Writes to dist/{category_slug}/combined/ when building multiple brands (combined build).
    Configurable via paths.output_filename_pattern in config.yaml.
    """
    paths_cfg = config.get("paths", {})
    output_dir = paths_cfg.get("output_dir", "dist/")
    pattern = paths_cfg.get("output_filename_pattern", "{category}_catalogue_{timestamp}.pdf")
    
    category_raw = category or config.get("category", {}).get("name", "powerbank")
    category_slug = slugify(category_raw)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    
    clean_output_dir = output_dir.rstrip("/\\")
    if category_slug not in clean_output_dir.lower():
        cat_output_dir = os.path.join(clean_output_dir, category_slug)
    else:
        cat_output_dir = clean_output_dir
        
    filename = pattern.format(category=category_slug, timestamp=timestamp)
    if brand:
        brand_slug = slugify(brand)
        return os.path.join(cat_output_dir, brand_slug, filename)
    return os.path.join(cat_output_dir, "combined", filename)


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


def validate_image_aspect_ratio(image_path: str, product_id: str) -> None:
    """
    SECTION A — Rule 7: Image Aspect Ratio (Strict Square 1:1 Framing).
    Flags any image that is not square, since the tile is square (290x290px) and non-square images crop.
    """
    if not os.path.exists(image_path):
        return
    try:
        with Image.open(image_path) as img:
            w, h = img.size
            aspect_diff = abs(w - h) / max(w, h)
            if aspect_diff > 0.01:
                logger.warning(
                    f"[RENDER_RULES Section A - Aspect Ratio] Product {product_id} image '{os.path.basename(image_path)}' is not square (1:1): {w}x{h}px (ratio: {w/h:.2f}). Tile is 290x290px square and non-square images will crop."
                )
    except Exception as e:
        logger.error(f"Error inspecting aspect ratio for {image_path}: {e}")


def validate_brand_images_background(brand_name: str, image_paths: list) -> None:
    """
    SECTION A — Rule 6: Image Background Consistency.
    Samples corner pixels of every image in a brand folder.
    If they are not all within close tolerance of each other, warns with the offending filenames.
    """
    valid_images = [p for p in image_paths if os.path.exists(p)]
    if len(valid_images) < 2:
        return

    corner_colors = {}
    for p in valid_images:
        try:
            with Image.open(p) as img:
                w, h = img.size
                rgba = img.convert("RGBA")
                corners = [
                    rgba.getpixel((5, 5)),
                    rgba.getpixel((w - 6, 5)),
                    rgba.getpixel((5, h - 6)),
                    rgba.getpixel((w - 6, h - 6))
                ]
                avg_corner = np.mean([[c[0], c[1], c[2]] for c in corners], axis=0)
                hex_col = '#{:02x}{:02x}{:02x}'.format(int(avg_corner[0]), int(avg_corner[1]), int(avg_corner[2]))
                corner_colors[p] = (avg_corner, hex_col)
        except Exception as e:
            logger.warning(f"Could not sample corner background for {p}: {e}")

    if not corner_colors:
        return

    rgb_matrix = np.array([v[0] for v in corner_colors.values()])
    median_rgb = np.median(rgb_matrix, axis=0)
    median_hex = '#{:02x}{:02x}{:02x}'.format(int(median_rgb[0]), int(median_rgb[1]), int(median_rgb[2]))

    inconsistent_files = []
    for p, (avg_rgb, hex_col) in corner_colors.items():
        dist = np.linalg.norm(avg_rgb - median_rgb)
        if dist > 25.0:
            inconsistent_files.append((os.path.basename(p), hex_col, dist))

    if inconsistent_files:
        msg_lines = [f"Brand '{brand_name}' has inconsistent image background tones (brand median: {median_hex}):"]
        for fname, hex_col, dist in inconsistent_files:
            msg_lines.append(f"  • {fname}: sampled corner color {hex_col} (deviation: {dist:.1f})")
        logger.warning("\n".join(msg_lines))


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

    # 7. Image Aspect Ratio check
    validate_image_aspect_ratio(raw_img_path, pid)


def resolve_effective_dp_float(row: Any) -> float:
    """Extracts numeric effective DP for sorting; products without DP return infinity to sort last."""
    prod = get_effective_product_dict(row)
    dp_val = prod.get("dp_raw") if prod.get("dp_raw") is not None else prod.get("dp")
    if dp_val is None and prod.get("mrp_raw") is not None:
        dp_val = prod.get("mrp_raw")
    if dp_val is not None and not is_empty_value(dp_val):
        try:
            return float(str(dp_val).replace("₹", "").replace("MRP", "").replace(",", "").strip())
        except Exception:
            pass
    return float("inf")


def build_catalogue_pdf(
    config_path: str = "config.yaml",
    brand: Optional[str] = None,
    category: Optional[str] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None
) -> str:
    """
    Main PDF builder: reads Excel, validates data against Section A rules,
    arranges 2-products-per-page with 1-up odd remainder, renders Jinja2 templates,
    enforces rendered title width checks (no auto-shrink), and compiles print-ready A4 PDF via Playwright.
    """
    config = load_config(config_path)
    excel_path = config.get("paths", {}).get("excel_file", "data/catalogue_data.xlsx")
    check_file_lock(excel_path)
    
    if progress_callback:
        progress_callback({
            "stage": "load",
            "product_id": None,
            "current": 1,
            "total": 5,
            "message": f"Reading master dataset from {excel_path}..."
        })

    logger.info(f"Reading master dataset from {excel_path}...")
    df = load_catalogue_data(excel_path)

    # Filter to brand if specified, and filter to approved/ready rows
    if brand:
        df = df[df["Brand"].astype(str).str.lower() == brand.lower()]
        if df.empty:
            raise ValueError(f"No products found for brand '{brand}' in {excel_path}")

    # Filter to category if specified
    if category and str(category).strip():
        clean_category = str(category).strip()
        if "Category" in df.columns:
            df = df[df["Category"].astype(str).str.strip().str.lower() == clean_category.lower()]
            if df.empty:
                raise ValueError(f"No products found for category '{clean_category}'" + (f" and brand '{brand}'" if brand else "") + f" in {excel_path}")
    
    # Build only approved rows (exclude Skipped, Blocked, Pending, Deferred, Ready_For_Review)
    df = df[df["Status"] == "Approved"]
    if df.empty:
        raise ValueError(f"No approved products to build in {excel_path}")

    # Pre-build validation: refuse compilation if any approved product lacks an image on disk
    missing_images = []
    for _, row in df.iterrows():
        prod = get_effective_product_dict(row)
        raw_img_path = prod.get("image_full_path", "")
        pid = prod.get("product_id") or row.get("Product_ID") or "UNKNOWN"
        model = str(prod.get("model_name", "")).strip()
        if not raw_img_path or not os.path.exists(raw_img_path) or os.path.getsize(raw_img_path) == 0:
            missing_images.append((pid, model, raw_img_path or "unspecified"))

    if missing_images:
        details = "\n".join(f"  • {pid} ({model}): image missing at '{p}'" for pid, model, p in missing_images)
        logger.error(f"Cannot compile catalogue: {len(missing_images)} approved product(s) lack an image on disk:\n{details}")
        raise FileNotFoundError(
            f"Cannot compile catalogue: {len(missing_images)} approved product(s) lack an image on disk:\n{details}\n"
            f"Please upload an image in Stage 4 before building."
        )
    
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

    # Detect category from active products in df if present
    df_cat = category if category and str(category).strip() else None
    if not df_cat and "Category" in df.columns:
        cats = [str(c).strip() for c in df["Category"].dropna().unique() if str(c).strip()]
        if len(cats) == 1:
            df_cat = cats[0]
        elif len(cats) > 1 and brand:
            brand_cats = [str(c).strip() for c in df[df["Brand"].astype(str).str.lower() == brand.lower()]["Category"].dropna().unique() if str(c).strip()]
            if brand_cats:
                df_cat = brand_cats[0]

    brand_for_output = ordered_brands[0] if len(ordered_brands) == 1 else (brand or None)
    output_pdf = resolve_output_pdf_path(config, brand=brand_for_output, category=df_cat)
    os.makedirs(os.path.dirname(output_pdf), exist_ok=True)

    # 2. Resolve logo
    logo_path = "assets/vianet-logo.png" if os.path.exists("assets/vianet-logo.png") else "images/vianet-logo.png"
    logo_mark_url = image_to_base64(logo_path)

    category_cfg = config.get("category", {})
    category_name = (df_cat or category_cfg.get("name", "POWERBANK")).upper()
    category_display_title = (f"{df_cat.capitalize()}s" if df_cat and df_cat.lower() != "powerbank" else category_cfg.get("display_title", "Powerbanks & Portable Chargers"))

    # 3. Group products by brand and build 2-up paginated structure
    brand_groups = []
    
    for brand_name in ordered_brands:
        brand_df = df[df["Brand"] == brand_name].copy()
        if brand_df.empty:
            continue
        
        # Sort by resolved DP ascending (cheapest first), missing DP last, ties broken by Product_ID
        brand_df["_sort_dp"] = brand_df.apply(resolve_effective_dp_float, axis=1)
        brand_df = brand_df.sort_values(by=["_sort_dp", "Product_ID"], ascending=[True, True])
            
        products = []
        brand_image_paths = []
        for idx_in_brand, (_, row) in enumerate(brand_df.iterrows(), 1):
            prod = get_effective_product_dict(row)
            validate_product_data(prod, row.to_dict())
            
            # Resolve product image
            raw_img_path = prod.get("image_full_path", "")
            brand_image_paths.append(raw_img_path)
            image_b64 = image_to_base64(raw_img_path) if os.path.exists(raw_img_path) else ""
            
            # Format price:
            # 1. DP (Dealer Price): Override_DP > MRP_Input
            dp_val = prod.get("dp_raw") if prod.get("dp_raw") is not None else prod.get("dp")
            if dp_val is not None and not is_empty_value(dp_val):
                try:
                    clean_dp = float(str(dp_val).replace("₹", "").replace("MRP", "").replace(",", "").strip())
                    price_str = f"{int(clean_dp):,}"
                except Exception:
                    price_str = str(dp_val).strip()
            else:
                price_str = None

            # 2. MRP (Maximum Retail Price): Override_MRP > MRP_Display > Raw_MRP_Scraped
            mrp_display_val = prod.get("mrp_display") or prod.get("mrp")
            if mrp_display_val is not None and not is_empty_value(mrp_display_val):
                try:
                    clean_num = float(str(mrp_display_val).replace("₹", "").replace("MRP", "").replace(",", "").strip())
                    mrp_display_str = f"{int(clean_num):,}"
                except Exception:
                    mrp_display_str = str(mrp_display_val).strip()
            else:
                mrp_display_str = None

            raw_model = prod.get("model_name", "")
            display_name = prod.get("display_name") or raw_model
            subtitle_val = prod.get("subtitle", "")
            bullets_list = prod.get("bullets", [])

            prod_ctx = {
                "product_id": prod.get("product_id") or row.get("Product_ID"),
                "index": f"{idx_in_brand:02d}",
                "brand": brand_name,
                "series": "SERIES",
                "name": display_name,
                "name_html": format_name_html(display_name),
                "subtitle_html": format_subtitle_html(subtitle_val),
                "image_url": image_b64,
                "bullets": bullets_list,
                "price": price_str,
                "mrp_display": mrp_display_str,
                "category": category_name,
            }
            products.append(prod_ctx)

        # Rule 6: Image Background Consistency per brand
        validate_brand_images_background(brand_name, brand_image_paths)
            
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

    if progress_callback:
        progress_callback({
            "stage": "paginate",
            "product_id": None,
            "current": 2,
            "total": 5,
            "message": f"Paginated layout constructed: {len(brand_groups)} brands, {sum(g['total_products'] for g in brand_groups)} products"
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

    preview_html_path = os.path.join(os.path.dirname(output_pdf), "catalogue_preview.html")
    with open(preview_html_path, "w", encoding="utf-8") as f:
        f.write(rendered_html)
    logger.info(f"Saved HTML preview to {preview_html_path}")

    if progress_callback:
        progress_callback({
            "stage": "render",
            "product_id": None,
            "current": 3,
            "total": 5,
            "message": f"Saved HTML preview to {preview_html_path}"
        })

    # 6. Compile PDF with Playwright Chromium and Validate Rendered Dimensions
    logger.info(f"Compiling PDF via Playwright Headless Chromium to {output_pdf}...")
    if progress_callback:
        progress_callback({
            "stage": "compile",
            "product_id": None,
            "current": 4,
            "total": 5,
            "message": f"Compiling PDF via Playwright Headless Chromium to {output_pdf}..."
        })
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1200, "height": 1697},
            device_scale_factor=2
        )
        page = context.new_page()
        page.goto(f"file:///{preview_html_path.replace(os.sep, '/')}", wait_until="networkidle")
        
        # Wait for self-hosted fonts & brand divider scaling
        page.evaluate("() => document.fonts.ready")
        page.wait_for_function("window.__namesFitted === true", timeout=15000)
        page.wait_for_timeout(300)

        # Rule 5 Check: Title width validation (No auto-shrink)
        overflow_issues = page.evaluate('''() => {
            const cards = document.querySelectorAll('.prod');
            const issues = [];
            cards.forEach(card => {
                const titleEl = card.querySelector('.prod__name');
                if (!titleEl) return;
                const range = document.createRange();
                range.selectNodeContents(titleEl);
                const textWidth = range.getBoundingClientRect().width;
                const containerWidth = titleEl.clientWidth;
                if (textWidth > containerWidth + 1.0) {
                    issues.push({
                        title: titleEl.innerText.replace(/\\s+/g, ' ').trim(),
                        textWidth: Math.round(textWidth),
                        containerWidth: Math.round(containerWidth)
                    });
                }
            });
            return issues;
        }''')

        if overflow_issues:
            err_details = [
                f"  • Title '{item['title']}' exceeds panel available width ({item['textWidth']}px > {item['containerWidth']}px)"
                for item in overflow_issues
            ]
            error_message = (
                f"\n================================================================================\n"
                f"[BUILD ERROR] RENDER_RULES.md Section A (Rule 5: Title Width Violation):\n"
                f"Product titles exceed the available panel width and cannot fit without overflow.\n"
                f"Auto-shrink is disabled per design specification.\n"
                + "\n".join(err_details) +
                f"\n================================================================================\n"
            )
            browser.close()
            raise ValueError(error_message)
        
        # Render PDF
        page.pdf(
            path=output_pdf,
            format="A4",
            print_background=True,
            prefer_css_page_size=True,
            margin={"top": "0mm", "bottom": "0mm", "left": "0mm", "right": "0mm"}
        )
        browser.close()

    # Rule Check: Page Count Sanity Verification
    import pdfplumber
    import math

    # Calculate expected page count
    expected_pages = 1  # Cover page
    for group in brand_groups:
        prod_count = group["total_products"]
        brand_pages = 1 + math.ceil(prod_count / 2)  # 1 divider + 2-up product pages
        expected_pages += brand_pages

    with pdfplumber.open(output_pdf) as pdf:
        actual_pages = len(pdf.pages)

    if actual_pages != expected_pages:
        raise ValueError(
            f"\n================================================================================\n"
            f"[BUILD ERROR] Page count sanity check failed!\n"
            f"Expected {expected_pages} total pages (1 cover + brand dividers + 2-up stacks),\n"
            f"but compiled PDF contains {actual_pages} pages. Check for layout overflows.\n"
            f"================================================================================\n"
        )

    logger.info(f"PDF build complete: {output_pdf} (verified {actual_pages}/{expected_pages} pages)")
    if progress_callback:
        progress_callback({
            "stage": "complete",
            "product_id": None,
            "current": 5,
            "total": 5,
            "message": f"PDF build complete: {output_pdf} (verified {actual_pages} pages)"
        })
    return output_pdf


# Convenience alias for external callers
build_catalogue = build_catalogue_pdf


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Compile print-ready A4 PDF catalogue")
    parser.add_argument("--brand", "-b", type=str, default=None, help="Brand name to build PDF for")
    parser.add_argument("--config", "-c", type=str, default="config.yaml", help="Path to config.yaml")
    args = parser.parse_args()
    build_catalogue_pdf(config_path=args.config, brand=args.brand)
