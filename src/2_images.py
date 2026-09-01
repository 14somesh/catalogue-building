import os
import re
import sys
import yaml
import requests
import urllib.parse
from PIL import Image
from typing import Tuple, Optional, Dict, Any, List
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
from src.utils.scraper import load_brand_defaults, reject_qualifier_mismatch
from src.parsers.amazon import AmazonParser
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


def validate_image_file(file_path: str, min_width: int = 800, min_height: int = 800) -> Tuple[str, Optional[Tuple[int, int]], bool]:
    """
    Validates an existing image file on disk for corruption, resolution, and square aspect ratio.
    Returns status ('ok' | 'low-res' | 'missing'), dimensions (width, height), and is_square.
    """
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        return "missing", None, False

    try:
        with Image.open(file_path) as img:
            width, height = img.size
            is_square = abs(width - height) / max(width, height) <= 0.01
            if width >= min_width and height >= min_height:
                return "ok", (width, height), is_square
            else:
                logger.warning(f"Image {file_path} is low-res: {width}x{height} (min required: {min_width}x{min_height})")
                return "low-res", (width, height), is_square
    except Exception as e:
        logger.error(f"Error reading image file {file_path}: {e}")
        return "missing", None, False


def upgrade_cdn_url_resolution(url: str) -> str:
    """Ensures CDN image URLs (Shopify, Amazon, etc.) request maximum master resolution."""
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
    Converts and saves as a clean PNG at dest_path without upscaling.
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
            logger.info(f"Successfully saved image to {dest_path} ({image.width}x{image.height}px, format=PNG)")
            return True
        else:
            logger.warning(f"Failed to download image from {max_res_url}: HTTP {response.status_code}")
            return False
    except Exception as e:
        logger.error(f"Error downloading image from {url}: {e}")
        return False


def resolve_product_image_path(row: dict, images_dir: str = "images") -> str:
    """Resolves convention image path: {images_dir}/{brand_slug}/{model_slug}.png or {images_dir}/{model_slug}.png"""
    override_path = row.get("Override_Image_Path")
    if not is_empty_value(override_path):
        return str(override_path).strip()

    brand_slug = slugify(row.get("Brand", ""))
    model_slug = slugify(row.get("Model_Name", ""))
    
    clean_dir = images_dir.rstrip("/\\")
    if brand_slug and brand_slug in clean_dir.lower():
        return f"{clean_dir}/{model_slug}.png"
    return f"{clean_dir}/{brand_slug}/{model_slug}.png"


def search_amazon_for_image(product_id: str, brand: str, model_name: str, marketplace_url: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """
    Tier 3 Image Helper: Searches Amazon India listings via Playwright for master product images ONLY.
    """
    target_url = marketplace_url if not is_empty_value(marketplace_url) else KNOWN_MARKETPLACE_LISTINGS.get(product_id)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')
        page = context.new_page()

        if target_url and isinstance(target_url, str) and "amazon.in/dp/" in target_url:
            try:
                logger.info(f"[{product_id}] [Tier 3 Image] Fetching direct Amazon listing: {target_url}")
                page.goto(target_url, wait_until='domcontentloaded', timeout=15000)
                img = page.locator('#landingImage')
                hires = img.get_attribute('data-old-hires') if img.count() > 0 else img.get_attribute('src')
                if hires:
                    hires = re.sub(r'\._[A-Z0-9_,]+_\.', '._SL1500_.', hires)
                    browser.close()
                    source_label = target_url.replace("https://www.", "").replace("http://www.", "")
                    return hires, source_label
            except Exception as e:
                logger.warning(f"[{product_id}] Failed fetching Amazon URL {target_url}: {e}")

        # Search Amazon
        q = f"{brand} {model_name}"
        search_url = f"https://www.amazon.in/s?k={urllib.parse.quote_plus(q)}"
        try:
            logger.info(f"[{product_id}] [Tier 3 Image] Searching Amazon for: {q}")
            page.goto(search_url, wait_until='domcontentloaded', timeout=15000)
            items = page.locator('div[data-component-type="s-search-result"]')
            
            brand_defaults = load_brand_defaults(brand)
            qualifier_tokens = brand_defaults.get("qualifier_tokens", ["Max", "Ultra", "Plus", "Pro", "Mini", "Lite", "Go"])
            
            for i in range(min(5, items.count())):
                item = items.nth(i)
                title_el = item.locator('h2 a span')
                title = title_el.inner_text() if title_el.count() > 0 else ""
                asin = item.get_attribute('data-asin')
                
                # Qualifier Token Check
                is_valid, _ = reject_qualifier_mismatch(model_name, title, qualifier_tokens)
                if not is_valid:
                    continue

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


def execute_image_tier_escalation(
    product_id: str,
    brand: str,
    model_name: str,
    image_url: Optional[str],
    marketplace_url: Optional[str],
    dest_path: str
) -> Tuple[str, Optional[str], Optional[int]]:
    """
    Executes the 4-tier image resolution chain:
    Tier 1: Brand Product Page -> Tier 2: Retail -> Tier 3: Amazon -> Tier 4: Collection Thumbnail.
    Never overwrites a manually placed local file.
    Returns (status, image_source, image_tier).
    """
    # Check if local image already exists (Manual / Pre-existing asset)
    if os.path.exists(dest_path) and os.path.isfile(dest_path):
        status, dims, is_sq = validate_image_file(dest_path)
        logger.info(f"[{product_id}] Preserving existing local image at {dest_path} ({dims[0]}x{dims[1]}px) -> Status: {status}")
        return status, "local-verified", 1

    # Tier 1: Brand Product Page URL
    if image_url and isinstance(image_url, str) and (image_url.startswith("http://") or image_url.startswith("https://")):
        logger.info(f"[{product_id}] [Tier 1 Image] Downloading from brand URL: {image_url}")
        if download_image(image_url, dest_path):
            status, _, _ = validate_image_file(dest_path)
            if status == "ok":
                return "ok", "brand-product-page", 1

    # Tier 3: Amazon India Fallback (Images Only)
    logger.info(f"[{product_id}] [Tier 3 Image] Searching Amazon fallback for {brand} {model_name}...")
    amz_url, amz_src = search_amazon_for_image(product_id, brand, model_name, marketplace_url)
    if amz_url:
        logger.info(f"[{product_id}] Found Amazon image: {amz_url} ({amz_src})")
        if download_image(amz_url, dest_path):
            status, _, _ = validate_image_file(dest_path)
            if status == "ok":
                return "ok", amz_src, 3

    return "missing", None, None


def process_images(config_path: str = "config.yaml", target_pids: Optional[list] = None) -> pd.DataFrame:
    """
    Main image processing pipeline enforcing the Image Tier Chain and quality gates.
    """
    config = load_config(config_path)
    excel_path = config.get("paths", {}).get("excel_file", "data/catalogue_data.xlsx")
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
        image_url = str(row_dict.get("Image_URL", "")).strip() if not is_empty_value(row_dict.get("Image_URL")) else None
        brand = str(row_dict.get("Brand", "")).strip()
        model_name = str(row_dict.get("Model_Name", "")).strip()
        marketplace_url = str(row_dict.get("Marketplace_URL", "")).strip() if not is_empty_value(row_dict.get("Marketplace_URL")) else None

        status, img_source, img_tier = execute_image_tier_escalation(
            product_id, brand, model_name, image_url, marketplace_url, image_path
        )

        df.at[idx, "Image_Status"] = status
        if img_source:
            df.at[idx, "Image_Source"] = img_source
        if img_tier:
            df.at[idx, "Image_Tier"] = img_tier
        updated_count += 1

    save_catalogue_data(df, excel_path)
    logger.info(f"Image tier processing completed. Updated {updated_count} rows in {excel_path}.")
    return df


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Process and validate catalogue product images")
    parser.add_argument("--pids", nargs="+", help="Specific Product_IDs to process")
    args = parser.parse_args()
    
    process_images(target_pids=args.pids)
