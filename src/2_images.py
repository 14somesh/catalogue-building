import os
import re
import sys
import yaml
import requests
import urllib.parse
from PIL import Image
from typing import Tuple, Optional, Dict, Any
import pandas as pd
from playwright.sync_api import sync_playwright

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.excel_handler import (
    load_catalogue_data,
    save_catalogue_data,
    check_file_lock,
    slugify,
    is_empty_value
)
from src.utils.logger import setup_logger

logger = setup_logger("images")

KNOWN_MARKETPLACE_LISTINGS = {
    "PB-SC-003": "https://www.amazon.in/dp/B0DMDZF5SV",
    "PB-SC-009": "https://www.amazon.in/dp/B0DFZ1KDPL",
}


def load_config(config_path: str = "config.yaml") -> dict:
    """Loads configuration settings from config.yaml."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_image_file(file_path: str, min_width: int = 800, min_height: int = 800) -> Tuple[str, Optional[Tuple[int, int]]]:
    """
    Validates an existing image file on disk for corruption and resolution.
    Returns status ('ok' | 'low-res' | 'missing') and dimensions (width, height).
    """
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        return "missing", None

    try:
        with Image.open(file_path) as img:
            width, height = img.size
            if width >= min_width and height >= min_height:
                return "ok", (width, height)
            else:
                logger.warning(f"Image {file_path} is low-res: {width}x{height} (min required: {min_width}x{min_height})")
                return "low-res", (width, height)
    except Exception as e:
        logger.error(f"Error reading image file {file_path}: {e}")
        return "missing", None


def upgrade_cdn_url_resolution(url: str) -> str:
    """
    Ensures CDN image URLs (Shopify, Amazon, etc.) request maximum master resolution.
    """
    if "cdn/shop" in url or "cdn.shopify" in url:
        if "width=" in url:
            url = re.sub(r"width=\d+", "width=2048", url)
        else:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}width=2048"
    elif "media-amazon.com" in url or "images-amazon.com" in url:
        url = re.sub(r'\._[A-Z0-9_,]+_\.', '._SL1500_.', url)
    return url


def download_image(url: str, dest_path: str, timeout: int = 15) -> bool:
    """
    Downloads an image from a web URL at maximum available resolution.
    Converts/saves it as a clean native-resolution PNG at dest_path without upscaling.
    """
    try:
        import io
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
        }
        
        max_res_url = upgrade_cdn_url_resolution(url)
        response = requests.get(max_res_url, headers=headers, timeout=timeout)
        if response.status_code == 200:
            image = Image.open(io.BytesIO(response.content))
            if image.mode in ("P", "CMYK", "LA"):
                image = image.convert("RGBA" if "A" in image.mode else "RGB")
            
            image.save(dest_path, format="PNG")
            logger.info(f"Successfully saved native-resolution image to {dest_path} ({image.width}x{image.height}px, format=PNG)")
            return True
        else:
            logger.warning(f"Failed to download image from {max_res_url}: HTTP {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"Error downloading image from {url}: {e}")
        return False


def resolve_product_image_path(row: dict) -> str:
    """
    Resolves convention image path: images/{brand_slug}/{model_slug}.png
    """
    override_path = row.get("Override_Image_Path")
    if not is_empty_value(override_path):
        return str(override_path).strip()

    brand_slug = slugify(row.get("Brand", ""))
    model_slug = slugify(row.get("Model_Name", ""))
    return f"images/{brand_slug}/{model_slug}.png"


def search_marketplace_for_image(product_id: str, brand: str, model_name: str, marketplace_url: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Searches Amazon/marketplace listings via Playwright for authoritative product images.
    Returns (image_url, source_label).
    """
    target_url = marketplace_url or KNOWN_MARKETPLACE_LISTINGS.get(product_id)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')
        page = context.new_page()

        # 1. Direct Listing URL (Known ASIN or Marketplace_URL)
        if target_url and "amazon.in/dp/" in target_url:
            try:
                logger.info(f"[{product_id}] Fetching known marketplace listing: {target_url}")
                page.goto(target_url, wait_until='domcontentloaded', timeout=15000)
                img = page.locator('#landingImage')
                hires = img.get_attribute('data-old-hires') if img.count() > 0 else img.get_attribute('src')
                if hires:
                    hires = re.sub(r'\._[A-Z0-9_,]+_\.', '._SL1500_.', hires)
                    browser.close()
                    source_label = target_url.replace("https://www.", "").replace("http://www.", "")
                    return hires, source_label
            except Exception as e:
                logger.warning(f"[{product_id}] Failed fetching marketplace URL {target_url}: {e}")

        # 2. Search on Amazon
        q = f"{brand} {model_name}"
        search_url = f"https://www.amazon.in/s?k={urllib.parse.quote_plus(q)}"
        try:
            logger.info(f"[{product_id}] Searching Amazon for: {q}")
            page.goto(search_url, wait_until='domcontentloaded', timeout=15000)
            items = page.locator('div[data-component-type="s-search-result"]')
            
            # Words to match strictly
            model_words = [w.lower() for w in re.findall(r'[a-zA-Z0-9]+', model_name) if len(w) > 1]
            stopwords = {"powerbank", "power", "bank", "portable", "charger", "fast", "charging"}
            distinctive_words = [w for w in model_words if w not in stopwords] or model_words
            
            for i in range(min(5, items.count())):
                item = items.nth(i)
                title_el = item.locator('h2 a span')
                title = title_el.inner_text().lower() if title_el.count() > 0 else ""
                asin = item.get_attribute('data-asin')
                
                # Check strict word match
                if all(w in title for w in distinctive_words):
                    img_el = item.locator('img.s-image')
                    img_src = img_el.get_attribute('src') if img_el.count() > 0 else None
                    if img_src:
                        hires = re.sub(r'\._[A-Z0-9_,]+_\.', '._SL1500_.', img_src)
                        browser.close()
                        return hires, f"amazon.in/dp/{asin}"
        except Exception as e:
            logger.warning(f"[{product_id}] Amazon search query failed: {e}")

        browser.close()

    return None, None


def process_images(config_path: str = "config.yaml", target_pids: Optional[list] = None) -> pd.DataFrame:
    """
    Main image processing pipeline:
    1. Reads catalogue_data.xlsx safely.
    2. Convention-first resolution: checks if local image exists at convention path.
    3. If local exists: validates resolution.
    4. If missing locally: attempts download from Image_URL (brand site).
    5. If still missing: falls back to Marketplace search (Amazon/Croma/Reliance Digital).
    6. Updates Image_Status and Image_Source in Excel sheet.
    """
    config = load_config(config_path)
    excel_path = config.get("paths", {}).get("excel_file", "data/catalogue_data.xlsx")
    img_cfg = config.get("image_validation", {})
    min_width = img_cfg.get("min_width_px", 800)
    min_height = img_cfg.get("min_height_px", 800)

    check_file_lock(excel_path)
    df = load_catalogue_data(excel_path)
    logger.info(f"Processing images for {len(df)} products in {excel_path}...")

    updated_count = 0
    for idx, row in df.iterrows():
        row_dict = row.to_dict()
        product_id = row_dict.get("Product_ID", f"Row_{idx+1}")
        
        if target_pids and product_id not in target_pids:
            continue

        image_path = resolve_product_image_path(row_dict)
        image_url = str(row_dict.get("Image_URL", "")).strip() if not is_empty_value(row_dict.get("Image_URL")) else ""
        brand = str(row_dict.get("Brand", "")).strip()
        model_name = str(row_dict.get("Model_Name", "")).strip()
        marketplace_url = str(row_dict.get("Marketplace_URL", "")).strip() if not is_empty_value(row_dict.get("Marketplace_URL")) else None
        
        image_source = row_dict.get("Image_Source")

        # Step A: Check if local file exists
        if os.path.exists(image_path) and os.path.isfile(image_path):
            status, dims = validate_image_file(image_path, min_width, min_height)
            logger.info(f"[{product_id}] Local image found at {image_path} ({dims[0]}x{dims[1]}px) -> Status: {status}")
            if is_empty_value(image_source):
                image_source = "brand-site" if "stuffcool.com" in str(row_dict.get("Source_URL", "")) else "local-disk"
        else:
            status = "missing"
            # Step B: Attempt download from existing Image_URL (Brand site)
            if image_url and (image_url.startswith("http://") or image_url.startswith("https://")):
                logger.info(f"[{product_id}] Attempting download from Image_URL: {image_url}...")
                if download_image(image_url, image_path):
                    status, dims = validate_image_file(image_path, min_width, min_height)
                    image_source = "brand-site"
            
            # Step C: Marketplace Fallback for missing images
            if status != "ok":
                logger.info(f"[{product_id}] Image missing. Triggering marketplace image fallback for {brand} {model_name}...")
                mp_img_url, mp_source = search_marketplace_for_image(product_id, brand, model_name, marketplace_url)
                if mp_img_url:
                    logger.info(f"[{product_id}] Found marketplace image: {mp_img_url} ({mp_source})")
                    if download_image(mp_img_url, image_path):
                        status, dims = validate_image_file(image_path, min_width, min_height)
                        image_source = mp_source
                        df.at[idx, "Image_URL"] = mp_img_url
                    else:
                        logger.warning(f"[{product_id}] Failed downloading marketplace image from {mp_img_url}")
                else:
                    logger.warning(f"[{product_id}] No marketplace product image found.")
                    image_source = None

        df.at[idx, "Image_Status"] = status
        df.at[idx, "Image_Source"] = image_source
        updated_count += 1

    # Save results back to Excel
    save_catalogue_data(df, excel_path)
    logger.info(f"Image processing completed. Updated {updated_count} rows in {excel_path}.")
    return df


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Process and validate catalogue product images")
    parser.add_argument("--pids", nargs="+", help="Specific Product_IDs to process")
    args = parser.parse_args()
    
    process_images(target_pids=args.pids)
