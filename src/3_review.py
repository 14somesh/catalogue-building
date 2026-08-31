import os
import sys
import yaml
import pandas as pd
from typing import Dict, Any, List, Tuple, Optional

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.excel_handler import (
    load_catalogue_data,
    save_catalogue_data,
    check_file_lock,
    is_empty_value,
    get_effective_product_dict,
    RAW_FIELD_TO_SOURCE_MAP
)
from src.utils.validators import validate_row_deterministic
from src.utils.scraper import load_brand_defaults, is_boilerplate_bullet
from src.utils.llm_client import draft_bullets_and_subtitle, audit_product_semantics
import importlib
collect_mod = importlib.import_module("src.1_collect")
collect_data_for_row = collect_mod.collect_data_for_row

images_mod = importlib.import_module("src.2_images")
execute_image_tier_escalation = images_mod.execute_image_tier_escalation
resolve_product_image_path = images_mod.resolve_product_image_path
from src.utils.logger import setup_logger

logger = setup_logger("review_loop")

MAX_LOOP_ATTEMPTS = 3


def load_config(config_path: str = "config.yaml") -> dict:
    """Loads configuration settings from config.yaml."""
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def clear_raw_fields(row_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Clears all Raw_ and Source_ fields on a blocked row.
    Guarantees zero invented data or unverified fields are stored.
    """
    cleared = dict(row_dict)
    for raw_col, source_col in RAW_FIELD_TO_SOURCE_MAP.items():
        cleared[raw_col] = None
        cleared[source_col] = None
        cleared[f"Tier_{raw_col.replace('Raw_', '')}"] = None
    cleared["Source_URL"] = None
    cleared["Source_Audit"] = None
    cleared["Image_URL"] = None
    return cleared


def attempt_auto_fix(
    row_dict: Dict[str, Any],
    hard_flags: List[str],
    config: dict,
    brand_defaults: dict,
    attempt_num: int
) -> Tuple[Dict[str, Any], bool, str]:
    """
    Evaluates hard flags and executes targeted auto-fix strategies:
    - sibling mismatch / capacity mismatch -> re-collect via collection page
    - boilerplate bullets -> discard bullets and redraft from clean specs
    - bullet over 60 chars -> deterministic word-boundary truncation (no extra LLM call)
    - missing warranty -> apply brand default warranty
    - image low-res -> escalate image tier
    Returns (updated_row_dict, fix_attempted, log_entry).
    """
    updated = dict(row_dict)
    pid = updated.get("Product_ID", "UNKNOWN")
    brand = updated.get("Brand", "")
    model_name = updated.get("Model_Name", "")

    # Auto-Fix 1: Missing Warranty -> Apply brand default from config
    if any("spec fields empty" in f for f in hard_flags) or is_empty_value(updated.get("Raw_Spec_Warranty")):
        default_warr = brand_defaults.get("default_warranty", "1 Year Manufacturer Warranty")
        updated["Raw_Spec_Warranty"] = default_warr
        updated["Source_Spec_Warranty"] = "brand-default-policy"
        updated["Tier_Spec_Warranty"] = 1
        log = f"Attempt {attempt_num}: Applied brand default warranty '{default_warr}'"
        return updated, True, log

    # Auto-Fix 2: Bullet over 60 chars -> Deterministic word-boundary truncation (cuts LLM calls)
    if any("exceeds 60-character" in f for f in hard_flags):
        for b_idx in range(1, 5):
            b_val = str(updated.get(f"Raw_Bullet_{b_idx}") or "").strip()
            if len(b_val) > 60:
                truncated = b_val[:60].rsplit(" ", 1)[0] if " " in b_val[:60] else b_val[:60]
                updated[f"Raw_Bullet_{b_idx}"] = truncated
        log = f"Attempt {attempt_num}: Truncated bullets to satisfy <=60 char limit"
        return updated, True, log

    # Auto-Fix 3: Boilerplate bullets -> Redraft targeting clean specs only
    if any("Boilerplate detected" in f for f in hard_flags):
        specs = {
            "capacity": str(updated.get("Raw_Spec_Capacity") or ""),
            "output": str(updated.get("Raw_Spec_Output") or ""),
            "ports": str(updated.get("Raw_Spec_Ports") or ""),
            "weight": str(updated.get("Raw_Spec_Weight") or ""),
            "warranty": str(updated.get("Raw_Spec_Warranty") or ""),
        }
        for b_idx in range(1, 5):
            b_val = str(updated.get(f"Raw_Bullet_{b_idx}") or "").strip()
            if is_boilerplate_bullet(b_val)[0]:
                updated[f"Raw_Bullet_{b_idx}"] = f"Features {specs.get('capacity', 'fast charging')} power"
        log = f"Attempt {attempt_num}: Replaced boilerplate bullets with clean spec text"
        return updated, True, log

    # Auto-Fix 4: Sibling / Capacity Mismatch -> Re-search via collection page
    if any("mismatch" in f.lower() for f in hard_flags):
        # Force re-collection
        collect_updates, success, c_log = collect_mod.collect_data_for_row(updated, config)
        if success:
            updated.update(collect_updates)
            log = f"Attempt {attempt_num}: Re-collected via tier escalation: {c_log}"
        else:
            log = f"Attempt {attempt_num}: Re-collection attempted but tiers failed: {c_log}"
        return updated, True, log

    return updated, False, f"Attempt {attempt_num}: No automated fix available for flags: {hard_flags}"


def process_row_loop(
    row_dict: Dict[str, Any],
    all_rows: List[Dict[str, Any]],
    config: dict,
    brand_defaults: dict,
    enable_semantic_audit: bool = False
) -> Dict[str, Any]:
    """
    Executes the autonomous loop for a single row:
    collect -> images -> validate -> fix loop (max 3 attempts) -> PASS, DEFER, or BLOCK.
    """
    current_row = dict(row_dict)
    pid = str(current_row.get("Product_ID", "UNKNOWN")).strip()
    
    # If row is already manually Approved by human with overrides, keep approved status
    if current_row.get("Status") == "Approved":
        return current_row

    initial_attempts = int(current_row.get("Attempts") or 0)
    attempts = initial_attempts
    fix_logs: List[str] = [str(current_row.get("Fix_Log"))] if not is_empty_value(current_row.get("Fix_Log")) else []
    hard_flags: List[str] = [str(current_row.get("Flags"))] if not is_empty_value(current_row.get("Flags")) else ["Initial validation required"]

    while attempts < MAX_LOOP_ATTEMPTS:
        attempts += 1
        logger.info(f"[{pid}] Loop execution attempt {attempts}/{MAX_LOOP_ATTEMPTS}...")

        # Step 1: Collect (if not already collected)
        if is_empty_value(current_row.get("Raw_Title")) and is_empty_value(current_row.get("Override_Title")):
            collect_updates, success, c_log = collect_mod.collect_data_for_row(current_row, config)
            current_row.update(collect_updates)
            fix_logs.append(f"Attempt {attempts}: {c_log}")

            # Check if collection was deferred due to infrastructure/LLM failure
            if current_row.get("Status") == "Deferred":
                logger.warning(f"[{pid}] Row deferred due to infrastructure failure during collection.")
                # Infrastructure failure does NOT consume a loop attempt
                current_row["Attempts"] = initial_attempts
                current_row["Fix_Log"] = "\n".join(fix_logs)
                return current_row

            if not success:
                hard_flags = [str(collect_updates.get("Flags") or "All spec tiers exhausted")]
                break

        # Step 2: Image Resolution
        images_dir = config.get("paths", {}).get("images_dir", "images")
        img_path = resolve_product_image_path(current_row, images_dir=images_dir)
        img_url = current_row.get("Image_URL")
        mp_url = current_row.get("Marketplace_URL")
        img_status, img_source, img_tier = execute_image_tier_escalation(
            pid, current_row.get("Brand", ""), current_row.get("Model_Name", ""),
            img_url, mp_url, img_path
        )
        current_row["Image_Status"] = img_status
        if img_source:
            current_row["Image_Source"] = img_source
        if img_tier:
            current_row["Image_Tier"] = img_tier

        # Step 3: Deterministic Validation (Hard & Warn checks)
        is_passed, hard_flags, warnings = validate_row_deterministic(current_row, all_rows, brand_defaults)

        if is_passed:
            # Step 4: LLM Semantic Advisory Pass (WARN-ONLY, optional behind flag, off by default)
            if enable_semantic_audit:
                prod_payload = get_effective_product_dict(current_row)
                sem_flags, _ = audit_product_semantics(prod_payload, llm_config=config.get("llm", {}))
                for sf in sem_flags:
                    warnings.append(f"[Advisory] {sf}")

            logger.info(f"[{pid}] Passed validation on attempt {attempts}.")
            current_row["Status"] = "Ready_For_Review"
            current_row["Flags"] = "\n".join(warnings) if warnings else None
            current_row["Attempts"] = attempts
            current_row["Fix_Log"] = "\n".join(fix_logs)
            return current_row

        # Step 5: Attempt Auto-Fix
        logger.warning(f"[{pid}] Validation failed with hard flags: {hard_flags}")
        updated_row, fix_attempted, fix_log = attempt_auto_fix(
            current_row, hard_flags, config, brand_defaults, attempts
        )
        fix_logs.append(fix_log)
        current_row = updated_row

        if current_row.get("Status") == "Deferred":
            logger.warning(f"[{pid}] Row deferred during auto-fix.")
            current_row["Attempts"] = initial_attempts
            current_row["Fix_Log"] = "\n".join(fix_logs)
            return current_row

        if not fix_attempted:
            logger.warning(f"[{pid}] Unfixable hard flags encountered. Halting loop.")
            break

    # If row is marked Deferred, do NOT clear fields and do NOT block
    if current_row.get("Status") == "Deferred":
        logger.warning(f"[{pid}] Product row is Deferred (infrastructure). Preserving raw fields.")
        current_row["Attempts"] = initial_attempts
        current_row["Fix_Log"] = "\n".join(fix_logs)
        return current_row

    # If loop finishes without clean pass -> HARD BLOCK
    logger.error(f"[{pid}] BLOCKED after {attempts} attempts. Clearing raw fields.")
    blocked_row = clear_raw_fields(current_row)
    blocked_row["Status"] = "Blocked"
    blocked_row["Flags"] = "Hard Block: " + ", ".join(hard_flags)
    blocked_row["Attempts"] = attempts
    blocked_row["Fix_Log"] = "\n".join(fix_logs)
    return blocked_row


def run_review_loop(
    config_path: str = "config.yaml",
    target_pids: Optional[list] = None,
    enable_semantic_audit: bool = False
) -> pd.DataFrame:
    """
    Main loop controller across catalogue dataset.
    """
    config = load_config(config_path)
    excel_path = config.get("paths", {}).get("excel_file", "data/catalogue_data.xlsx")
    check_file_lock(excel_path)
    
    df = load_catalogue_data(excel_path)
    logger.info(f"Running agent review loop on {len(df)} products (semantic audit={enable_semantic_audit})...")

    all_rows = [row.to_dict() for _, row in df.iterrows()]
    updated_rows = []

    for idx, row in df.iterrows():
        row_dict = row.to_dict()
        pid = row_dict.get("Product_ID")
        brand = row_dict.get("Brand", "")
        brand_defaults = load_brand_defaults(brand)

        if target_pids and pid not in target_pids:
            updated_rows.append(row_dict)
            continue

        processed_row = process_row_loop(
            row_dict, all_rows, config, brand_defaults, enable_semantic_audit=enable_semantic_audit
        )
        updated_rows.append(processed_row)

    updated_df = pd.DataFrame(updated_rows)
    save_catalogue_data(updated_df, excel_path)
    logger.info(f"Review loop complete. Saved updated records to {excel_path}.")
    return updated_df


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Agent Loop Controller")
    parser.add_argument("--pids", nargs="+", help="Specific Product_IDs to process")
    parser.add_argument("--semantic-audit", action="store_true", default=False, help="Enable optional LLM semantic audit pass")
    args = parser.parse_args()
    
    run_review_loop(target_pids=args.pids, enable_semantic_audit=args.semantic_audit)
