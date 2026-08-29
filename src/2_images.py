import os
import sys
import yaml
import requests
from PIL import Image
from typing import Tuple, Optional
import pandas as pd

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
    Ensures CDN image URLs (e.g. Shopify CDN) request maximum master resolution (e.g., width=2048).
    Never requests a constrained or downscaled variant.
    """
    import re
    if "cdn/shop" in url or "cdn.shopify" in url:
        # Replace existing width query or append width=2048 for master asset
        if "width=" in url:
            url = re.sub(r"width=\d+", "width=2048", url)
        else:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}width=2048"
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
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        # Rule: Always request highest resolution source offers
        max_res_url = upgrade_cdn_url_resolution(url)
        
        response = requests.get(max_res_url, headers=headers, timeout=timeout)
        if response.status_code == 200:
            image = Image.open(io.BytesIO(response.content))
            # Convert palette/CMYK to RGB/RGBA
            if image.mode in ("P", "CMYK", "LA"):
                image = image.convert("RGBA" if "A" in image.mode else "RGB")
            
            # Save at native resolution — never upscale beyond native pixel size
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
    Resolves image path:
    1. If Override_Image_Path is specified and not empty -> use it.
    2. Otherwise, convention path: images/{brand_slug}/{model_slug}.png
    """
    override_path = row.get("Override_Image_Path")
    if not is_empty_value(override_path):
        return str(override_path).strip()

    brand_slug = slugify(row.get("Brand", ""))
    model_slug = slugify(row.get("Model_Name", ""))
    return f"images/{brand_slug}/{model_slug}.png"


def process_images(config_path: str = "config.yaml") -> pd.DataFrame:
    """
    Main image processing pipeline:
    1. Reads catalogue_data.xlsx safely.
    2. Convention-first resolution: checks if local image exists at convention path.
    3. If local exists: validates resolution and skips download.
    4. If missing locally: attempts download from Image_URL.
    5. Updates Image_Status in Excel sheet.
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
        image_path = resolve_product_image_path(row_dict)
        image_url = str(row_dict.get("Image_URL", "")).strip() if not is_empty_value(row_dict.get("Image_URL")) else ""

        # Step A: Check if local file exists (Convention-First)
        if os.path.exists(image_path) and os.path.isfile(image_path):
            status, dims = validate_image_file(image_path, min_width, min_height)
            logger.info(f"[{product_id}] Local image found at {image_path} ({dims[0]}x{dims[1]}px) -> Status: {status} (Download SKIPPED)")
        else:
            # Step B: Local file missing -> Attempt download if URL available
            if image_url and (image_url.startswith("http://") or image_url.startswith("https://")):
                logger.info(f"[{product_id}] Local image missing. Attempting download from {image_url} to {image_path}...")
                downloaded = download_image(image_url, image_path)
                if downloaded:
                    status, dims = validate_image_file(image_path, min_width, min_height)
                else:
                    status = "missing"
            else:
                logger.warning(f"[{product_id}] Local image missing and no valid Image_URL provided -> Status: missing")
                status = "missing"

        df.at[idx, "Image_Status"] = status
        updated_count += 1

    # Save results back to Excel
    save_catalogue_data(df, excel_path)
    logger.info(f"Image processing completed. Updated {updated_count} rows in {excel_path}.")
    return df


if __name__ == "__main__":
    process_images()
