import os
import re
import hashlib
from typing import Dict, Any, List, Tuple, Optional
from PIL import Image
import pandas as pd

from src.utils.excel_handler import (
    is_empty_value,
    slugify,
    get_effective_value,
    RAW_FIELD_TO_SOURCE_MAP
)
from src.utils.scraper import reject_qualifier_mismatch, is_boilerplate_bullet
from src.utils.logger import setup_logger

logger = setup_logger("validators")


def get_image_file_hash(file_path: str) -> Optional[str]:
    """Computes MD5 hash of an image file to detect duplicate assets across different SKUs."""
    if not os.path.exists(file_path) or not os.path.isfile(file_path):
        return None
    try:
        hasher = hashlib.md5()
        with open(file_path, "rb") as f:
            buf = f.read(65536)
            while len(buf) > 0:
                hasher.update(buf)
                buf = f.read(65536)
        return hasher.hexdigest()
    except Exception as e:
        logger.debug(f"Failed hashing image {file_path}: {e}")
        return None


def sample_image_corner_color(img: Image.Image) -> Tuple[int, int, int]:
    """Samples corner pixels and returns average RGB tuple."""
    w, h = img.size
    corners = [(5, 5), (max(0, w - 6), 5), (5, max(0, h - 6)), (max(0, w - 6), max(0, h - 6))]
    rgb_img = img.convert("RGB")
    pixels = [rgb_img.getpixel(pos) for pos in corners]
    avg_r = sum(p[0] for p in pixels) // len(pixels)
    avg_g = sum(p[1] for p in pixels) // len(pixels)
    avg_b = sum(p[2] for p in pixels) // len(pixels)
    return (avg_r, avg_g, avg_b)


def validate_row_deterministic(
    row_dict: Dict[str, Any],
    all_rows: Optional[List[Dict[str, Any]]] = None,
    brand_defaults: Optional[dict] = None
) -> Tuple[bool, List[str], List[str]]:
    """
    DETERMINISTIC ROW VALIDATOR (No LLM).
    Returns (is_passed, hard_flags, warnings).
    - If hard_flags is non-empty -> is_passed = False (Row must be Auto-Fixed or Blocked).
    - If hard_flags is empty -> is_passed = True (Row may have non-blocking warnings).
    """
    hard_flags: List[str] = []
    warnings: List[str] = []

    pid = str(row_dict.get("Product_ID", "UNKNOWN")).strip()
    brand = str(row_dict.get("Brand", "")).strip()
    model_name = str(row_dict.get("Model_Name", "")).strip()
    raw_title = str(row_dict.get("Raw_Title", "")).strip() if not is_empty_value(row_dict.get("Raw_Title")) else ""
    override_title = str(row_dict.get("Override_Title", "")).strip() if not is_empty_value(row_dict.get("Override_Title")) else ""
    
    # --------------------------------------------------------------------------
    # HARD CHECK a: All spec tiers exhausted / No data collected
    # --------------------------------------------------------------------------
    if row_dict.get("Status") == "Blocked" or (not raw_title and not override_title):
        hard_flags.append("All spec tiers exhausted without finding technical specifications")

    # --------------------------------------------------------------------------
    # HARD CHECK b: Qualifier-token sibling mismatch
    # --------------------------------------------------------------------------
    if raw_title:
        qualifier_tokens = (brand_defaults or {}).get("qualifier_tokens", ["Max", "Ultra", "Plus", "Pro", "Mini", "Lite", "Go"])
        is_valid_qualifier, qualifier_err = reject_qualifier_mismatch(model_name, raw_title, qualifier_tokens)
        if not is_valid_qualifier:
            hard_flags.append(f"Qualifier token mismatch: {qualifier_err}")

    # --------------------------------------------------------------------------
    # HARD CHECK c: Capacity stated in Model_Name conflicts with collected capacity
    # --------------------------------------------------------------------------
    model_cap_match = re.search(r'\b(5000|10000|15000|20000|25000|30000)\s*(?:mah)?\b', model_name, re.I)
    if model_cap_match:
        model_cap_num = model_cap_match.group(1).replace(",", "")
        collected_cap = str(get_effective_value(row_dict, "Spec_Capacity") or "")
        spec_cap_match = re.search(r'\b(5000|10000|15000|20000|25000|30000)\b', collected_cap.replace(",", ""))
        if spec_cap_match and spec_cap_match.group(1) != model_cap_num:
            hard_flags.append(
                f"Capacity mismatch: Model_Name specifies {model_cap_num}mAh but collected spec is {spec_cap_match.group(1)}mAh"
            )

    # --------------------------------------------------------------------------
    # HARD CHECK d: Boilerplate detected in bullets
    # --------------------------------------------------------------------------
    for b_idx in range(1, 5):
        b_val = get_effective_value(row_dict, f"Bullet_{b_idx}")
        if not is_empty_value(b_val):
            is_bp, bp_phrase = is_boilerplate_bullet(str(b_val))
            if is_bp:
                hard_flags.append(f"Boilerplate detected in Bullet_{b_idx}: '{bp_phrase}'")

    # --------------------------------------------------------------------------
    # HARD CHECK e: More than 2 of 5 spec fields empty
    # --------------------------------------------------------------------------
    spec_fields = ["Spec_Capacity", "Spec_Output", "Spec_Ports", "Spec_Weight", "Spec_Warranty"]
    empty_spec_count = sum(1 for sf in spec_fields if is_empty_value(get_effective_value(row_dict, sf)))
    if empty_spec_count > 2:
        hard_flags.append(f"Insufficient specifications: {empty_spec_count} of 5 required specs are empty")

    # --------------------------------------------------------------------------
    # HARD CHECK f: Any populated field with an empty Source_ (Write Guard Integrity)
    # --------------------------------------------------------------------------
    for raw_col, source_col in RAW_FIELD_TO_SOURCE_MAP.items():
        raw_val = row_dict.get(raw_col)
        if not is_empty_value(raw_val):
            source_val = row_dict.get(source_col)
            general_source = row_dict.get("Source_URL")
            if is_empty_value(source_val) and is_empty_value(general_source):
                hard_flags.append(f"FATAL BUG: Field '{raw_col}' populated without verified source URL in '{source_col}' or 'Source_URL'")

    # --------------------------------------------------------------------------
    # HARD CHECK g, h, i: Image validations
    # --------------------------------------------------------------------------
    override_img = row_dict.get("Override_Image_Path")
    brand_slug = slugify(brand)
    model_slug = slugify(model_name)
    images_dir = (brand_defaults or {}).get("images_dir", "images")
    clean_dir = images_dir.rstrip("/\\")
    if brand_slug and brand_slug in clean_dir.lower():
        default_img_path = f"{clean_dir}/{model_slug}.png"
    else:
        default_img_path = f"{clean_dir}/{brand_slug}/{model_slug}.png"
    expected_img_path = str(override_img).strip() if not is_empty_value(override_img) else default_img_path
    
    if not os.path.exists(expected_img_path) or not os.path.isfile(expected_img_path) or os.path.getsize(expected_img_path) == 0:
        hard_flags.append(f"Image missing on disk at '{expected_img_path}'")
    else:
        # Check i: Filename slug check (if not override)
        if is_empty_value(override_img):
            actual_filename = os.path.basename(expected_img_path)
            expected_filename = f"{model_slug}.png"
            if actual_filename != expected_filename:
                hard_flags.append(f"Image filename mismatch: expected '{expected_filename}', got '{actual_filename}'")

        # Check h: Duplicate image hash across rows
        img_hash = get_image_file_hash(expected_img_path)
        if img_hash and all_rows:
            for other_row in all_rows:
                other_pid = str(other_row.get("Product_ID", "")).strip()
                if other_pid != pid:
                    other_override = other_row.get("Override_Image_Path")
                    other_path = str(other_override).strip() if not is_empty_value(other_override) else f"images/{slugify(other_row.get('Brand', ''))}/{slugify(other_row.get('Model_Name', ''))}.png"
                    if os.path.exists(other_path) and os.path.isfile(other_path):
                        other_hash = get_image_file_hash(other_path)
                        if other_hash == img_hash:
                            hard_flags.append(f"Duplicate image asset: identical file hash to product [{other_pid}]")
                            break

        # Check resolution & square aspect ratio
        try:
            with Image.open(expected_img_path) as img:
                w, h = img.size
                # HARD CHECK j is for bullets, but WARN k and l are for images:
                if w < 800 or h < 800:
                    warnings.append(f"Image resolution below target: {w}x{h}px (min required 800x800px)")
                
                is_square = abs(w - h) / max(w, h) <= 0.01
                if not is_square:
                    warnings.append(f"Image is not square (1:1): {w}x{h}px; tile is 235x235px square and will crop")
        except Exception as e:
            hard_flags.append(f"Corrupted image file at '{expected_img_path}': {e}")

    # --------------------------------------------------------------------------
    # HARD CHECK j: Bullet length > 60 chars (The Measured Wrap Cliff)
    # --------------------------------------------------------------------------
    for b_idx in range(1, 5):
        b_val = get_effective_value(row_dict, f"Bullet_{b_idx}")
        if not is_empty_value(b_val):
            b_str = str(b_val).strip()
            if len(b_str) > 60:
                hard_flags.append(f"Bullet_{b_idx} exceeds 60-character wrap limit ({len(b_str)} chars): '{b_str}'")

    # --------------------------------------------------------------------------
    # WARN n: Image from non-brand source
    # --------------------------------------------------------------------------
    img_source = row_dict.get("Image_Source")
    if not is_empty_value(img_source):
        img_src_str = str(img_source).lower()
        if not any(k in img_src_str for k in ["brand", "official", "local-verified"]):
            warnings.append(f"Image sourced from secondary channel: {img_source}")

    # --------------------------------------------------------------------------
    # WARN o: Override data present without scraped source to cross-check
    # --------------------------------------------------------------------------
    has_any_override = any(not is_empty_value(row_dict.get(f"Override_{f}")) for f in ["Title", "Subtitle", "MRP", "Spec_Capacity", "Bullet_1"])
    has_scraped_source = not is_empty_value(row_dict.get("Source_URL")) and str(row_dict.get("Source_URL")).startswith("http")
    if has_any_override and not has_scraped_source:
        warnings.append("Manual override data present with no live scraped source URL to cross-check")

    is_passed = len(hard_flags) == 0
    return is_passed, hard_flags, warnings
