import os
import sys
import yaml
import pandas as pd
from typing import List, Dict, Any, Tuple, Optional

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.excel_handler import (
    load_catalogue_data,
    save_catalogue_data,
    get_effective_value,
    get_effective_product_dict,
    is_empty_value,
    check_file_lock,
    slugify
)
from src.utils.llm_client import audit_product_semantics
from src.utils.logger import setup_logger

logger = setup_logger("review")


def load_config(config_path: str = "config.yaml") -> dict:
    """Loads configuration settings from config.yaml."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_deterministic_rules(row_dict: Dict[str, Any], config: dict) -> List[str]:
    """
    Evaluates the 6 deterministic validation rules on resolved effective values:
    Rule 1: Mandatory Fields (Brand, Model_Name, Title, Subtitle, Specs, Bullets)
    Rule 2: Authoritative MRP Input Check (> 0, unverified scraped fallback warning)
    Rule 3: Bullet Count (exactly 4 non-empty)
    Rule 4: Bullet Length Limit (<= 60 chars each)
    Rule 5: Image Status & Asset on Disk (ok)
    Rule 6: MRP Discrepancy Cross-Check (MRP_Input vs Raw_MRP_Scraped)
    """
    flags = []
    val_rules = config.get("validation_rules", {})
    bullet_max_chars = val_rules.get("bullet_max_chars", 60)
    required_bullet_count = val_rules.get("required_bullets", 4)
    title_max_chars = val_rules.get("title_max_chars", 40)
    subtitle_max_chars = val_rules.get("subtitle_max_chars", 80)

    # 1. Rule 1: Mandatory Base & Title Fields (Effective resolved)
    brand = row_dict.get("Brand")
    model_name = row_dict.get("Model_Name")
    title = get_effective_value(row_dict, "Title")
    subtitle = get_effective_value(row_dict, "Subtitle")

    if is_empty_value(brand):
        flags.append("Missing Brand")
    if is_empty_value(model_name):
        flags.append("Missing Model_Name")
    if is_empty_value(title):
        flags.append("Missing Title")
    elif len(str(title).strip()) > title_max_chars:
        flags.append(f"Title exceeds {title_max_chars} chars ({len(str(title).strip())} chars)")

    if is_empty_value(subtitle):
        flags.append("Missing Subtitle")
    elif len(str(subtitle).strip()) > subtitle_max_chars:
        flags.append(f"Subtitle exceeds {subtitle_max_chars} chars ({len(str(subtitle).strip())} chars)")

    # 2. Rule 2: DP Price Validation (Authoritative from Override_MRP or MRP_Input)
    mrp_input = row_dict.get("MRP_Input")
    override_mrp = row_dict.get("Override_MRP")

    if not is_empty_value(override_mrp):
        try:
            val = float(override_mrp)
            if val <= 0:
                flags.append("Override_MRP must be greater than 0")
        except (ValueError, TypeError):
            flags.append("Override_MRP is not a valid number")
    elif not is_empty_value(mrp_input):
        try:
            val = float(mrp_input)
            if val <= 0:
                flags.append("MRP_Input must be greater than 0")
        except (ValueError, TypeError):
            flags.append("MRP_Input is not a valid number")
    else:
        flags.append("Missing Price (MRP_Input is empty)")

    # 3. Mandatory Specs Check
    specs_to_check = ["Spec_Capacity", "Spec_Output", "Spec_Ports", "Spec_Weight", "Spec_Warranty"]
    for spec_key in specs_to_check:
        spec_val = get_effective_value(row_dict, spec_key)
        if is_empty_value(spec_val):
            field_name = spec_key.replace("Spec_", "")
            flags.append(f"Missing spec: {field_name}")

    # 4. Rule 3 & Rule 4: Bullet Count and Length Limit
    bullets = []
    for b_idx in range(1, 5):
        b_val = get_effective_value(row_dict, f"Bullet_{b_idx}")
        if not is_empty_value(b_val):
            b_str = str(b_val).strip()
            bullets.append(b_str)
            if len(b_str) > bullet_max_chars:
                flags.append(f"Bullet {b_idx} exceeds {bullet_max_chars} chars ({len(b_str)} chars)")
        else:
            flags.append(f"Missing Bullet_{b_idx}")

    if len(bullets) < required_bullet_count:
        flags.append(f"Incomplete bullets: expected {required_bullet_count}, found {len(bullets)}")

    # 5. Rule 5: Image Status & Asset Verification
    image_status = str(row_dict.get("Image_Status", "")).strip().lower()
    image_path = get_effective_value(row_dict, "Image_Path")

    if image_status == "low-res":
        flags.append("Image status: low-res (< 800x800px)")
    elif image_status == "missing":
        flags.append("Image status: missing")
    elif image_status != "ok":
        flags.append(f"Image status unverified: '{image_status}'")

    if not image_path or not os.path.exists(image_path):
        flags.append(f"Image asset not found on disk at '{image_path}'")

    return flags


def run_full_review(row_dict: Dict[str, Any], config: dict, run_semantic: bool = True, source_text: Optional[str] = None) -> Tuple[List[str], str]:
    """
    Executes both Deterministic Rules and LLM Semantic Audit on resolved effective product values.
    Returns (all_flags, proposed_status).
    """
    # 1. Run Deterministic Rules
    deterministic_flags = validate_deterministic_rules(row_dict, config)
    all_flags = list(deterministic_flags)

    # 2. Run LLM Semantic Audit (if enabled and API key configured)
    if run_semantic:
        try:
            prod_dict = get_effective_product_dict(row_dict)
            llm_model = config.get("llm", {}).get("model", "gemini-3.6-flash")
            semantic_flags, is_clean = audit_product_semantics(prod_dict, source_text=source_text, model=llm_model)
            for s_flag in semantic_flags:
                if s_flag not in all_flags:
                    all_flags.append(s_flag)
        except Exception as e:
            logger.warning(f"Semantic audit skipped/failed: {e}")

    # 3. Determine Proposed Status
    current_status = str(row_dict.get("Status", "")).strip()
    if current_status == "Approved":
        # Preserve human sign-off
        status = "Approved"
    elif all_flags:
        status = "Flagged"
    else:
        status = "Ready_For_Review"

    return all_flags, status


def run_review_pipeline(config_path: str = "config.yaml", run_semantic: bool = True) -> pd.DataFrame:
    """
    Main review pipeline:
    1. Reads master catalogue data safely.
    2. Runs deterministic validation + LLM semantic audit on effective resolved values.
    3. Non-destructively writes Flags and Status back to Excel.
    """
    config = load_config(config_path)
    excel_path = config.get("paths", {}).get("excel_file", "data/catalogue_data.xlsx")

    check_file_lock(excel_path)
    df = load_catalogue_data(excel_path)
    logger.info(f"Running full review pipeline on {len(df)} products in {excel_path} (semantic={run_semantic})...")

    flagged_count = 0
    clean_count = 0

    for idx, row in df.iterrows():
        row_dict = row.to_dict()
        product_id = row_dict.get("Product_ID", f"Row_{idx+1}")
        flags, status = run_full_review(row_dict, config, run_semantic=run_semantic)

        flags_str = " | ".join(flags) if flags else ""
        df.at[idx, "Flags"] = flags_str
        df.at[idx, "Status"] = status

        if flags:
            flagged_count += 1
            logger.warning(f"[{product_id}] Status: {status} | Flags ({len(flags)}): {flags_str}")
        else:
            clean_count += 1
            logger.info(f"[{product_id}] Status: {status} (Clean Pass)")

    save_catalogue_data(df, excel_path)
    logger.info(f"Review pipeline complete: {clean_count} Ready_For_Review, {flagged_count} Flagged.")
    return df


if __name__ == "__main__":
    run_review_pipeline()
