import os
import re
import sys
import pandas as pd
from typing import Dict, Any, Optional, Union, List
from src.utils.logger import setup_logger

logger = setup_logger("excel_handler")

# Standard flat schema defined in ARCHITECTURE.md
EXPECTED_COLUMNS = [
    "Product_ID", "Category", "Brand", "Model_Name", "Display_Name", "Product_URL", "Brochure_PDF", "Marketplace_URL", "MRP_Input", "MRP_Display",
    "Source_URL", "Source_Audit",
    "Raw_Title", "Source_Title", "Tier_Title",
    "Raw_Subtitle", "Source_Subtitle", "Tier_Subtitle",
    "Raw_MRP_Scraped", "Source_MRP_Scraped", "Tier_MRP_Scraped",
    "Raw_Spec_Capacity", "Source_Spec_Capacity", "Tier_Spec_Capacity",
    "Raw_Spec_Output", "Source_Spec_Output", "Tier_Spec_Output",
    "Raw_Spec_Ports", "Source_Spec_Ports", "Tier_Spec_Ports",
    "Raw_Spec_Weight", "Source_Spec_Weight", "Tier_Spec_Weight",
    "Raw_Spec_Warranty", "Source_Spec_Warranty", "Tier_Spec_Warranty",
    "Raw_Bullet_1", "Source_Bullet_1", "Tier_Bullet_1",
    "Raw_Bullet_2", "Source_Bullet_2", "Tier_Bullet_2",
    "Raw_Bullet_3", "Source_Bullet_3", "Tier_Bullet_3",
    "Raw_Bullet_4", "Source_Bullet_4", "Tier_Bullet_4",
    "Image_URL", "Image_Status", "Image_Source", "Image_Tier",
    "Override_Title", "Override_Subtitle", "Override_DP", "Override_MRP", "Override_Spec_Capacity", "Override_Spec_Output", "Override_Spec_Ports", "Override_Spec_Weight", "Override_Spec_Warranty",
    "Override_Bullet_1", "Override_Bullet_2", "Override_Bullet_3", "Override_Bullet_4", "Override_Image_Path",
    "Attempts", "Fix_Log", "Flags", "LLM_Provider", "Status"
]

RAW_FIELD_TO_SOURCE_MAP = {
    "Raw_Title": "Source_Title",
    "Raw_Subtitle": "Source_Subtitle",
    "Raw_MRP_Scraped": "Source_MRP_Scraped",
    "Raw_Spec_Capacity": "Source_Spec_Capacity",
    "Raw_Spec_Output": "Source_Spec_Output",
    "Raw_Spec_Ports": "Source_Spec_Ports",
    "Raw_Spec_Weight": "Source_Spec_Weight",
    "Raw_Spec_Warranty": "Source_Spec_Warranty",
    "Raw_Bullet_1": "Source_Bullet_1",
    "Raw_Bullet_2": "Source_Bullet_2",
    "Raw_Bullet_3": "Source_Bullet_3",
    "Raw_Bullet_4": "Source_Bullet_4",
}


class ExcelFileLockedError(Exception):
    """Raised when the Excel file is open and locked by Microsoft Excel or another process."""
    pass


class DataLayerInvariantViolation(Exception):
    """Raised when a Raw_ field is written without a verified Source_ URL (The Write Guard)."""
    pass


def is_file_locked(file_path: str) -> bool:
    """
    Checks if a file is currently locked by another application (e.g. Microsoft Excel).
    On Windows, Microsoft Excel opens files with exclusive sharing mode which prevents renaming or writing.
    """
    if not os.path.exists(file_path):
        return False
        
    try:
        os.rename(file_path, file_path)
        return False
    except (PermissionError, IOError, OSError):
        return True


def check_file_lock(file_path: str) -> None:
    """
    Checks if the Excel file is locked and raises ExcelFileLockedError with a clear actionable message.
    """
    if is_file_locked(file_path):
        msg = (
            f"\n"
            f"================================================================================\n"
            f"[ERROR] Excel File Lock Detected!\n"
            f"File '{file_path}' is currently open in Microsoft Excel or another program.\n"
            f"Please save and close the file in Excel, then re-run the command.\n"
            f"================================================================================\n"
        )
        logger.error(msg)
        raise ExcelFileLockedError(msg)


def slugify(text: str) -> str:
    """
    Generates a clean filesystem slug from a string (e.g., 'VoltMax' -> 'voltmax', 'Mag-Flow 10' -> 'mag-flow-10').
    """
    if not text or pd.isna(text):
        return ""
    text = str(text).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def is_empty_value(val: Any) -> bool:
    """Checks if a value is None, NaN, or an empty/whitespace string."""
    if val is None:
        return True
    if pd.isna(val):
        return True
    if isinstance(val, str) and val.strip() == "":
        return True
    return False


def validate_write_guard(row: Union[pd.Series, Dict[str, Any]]) -> None:
    """
    THE WRITE GUARD:
    Refuses to write any Raw_ field when its matching Source_ field is empty.
    Raises DataLayerInvariantViolation immediately.
    Override_ columns are exempt (human-supplied).
    """
    row_dict = row.to_dict() if isinstance(row, pd.Series) else row
    pid = row_dict.get("Product_ID", "UNKNOWN")
    
    for raw_col, source_col in RAW_FIELD_TO_SOURCE_MAP.items():
        raw_val = row_dict.get(raw_col)
        if not is_empty_value(raw_val):
            source_val = row_dict.get(source_col)
            general_source = row_dict.get("Source_URL")
            effective_source = source_val if not is_empty_value(source_val) else general_source
            if is_empty_value(effective_source):
                raise DataLayerInvariantViolation(
                    f"[WRITE GUARD VIOLATION] Product {pid}: Field '{raw_col}' has value '{raw_val}' "
                    f"but its matching source field '{source_col}' and 'Source_URL' are empty! "
                    f"A field without a verified source URL cannot be written."
                )


def load_catalogue_data(file_path: str = "data/catalogue_data.xlsx", sheet_name: str = "CatalogueData") -> pd.DataFrame:
    """
    Safely reads the catalogue data from Excel, checking for file locks and validating schema columns.
    """
    check_file_lock(file_path)
    
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Catalogue data file not found at: {file_path}")
        
    logger.info(f"Loading catalogue data from {file_path} (sheet: {sheet_name})...")
    df = pd.read_excel(file_path, sheet_name=sheet_name)
    
    # Check for missing columns and append in expected schema order
    missing_cols = [col for col in EXPECTED_COLUMNS if col not in df.columns]
    if missing_cols:
        for col in missing_cols:
            df[col] = None

    # Ensure all non-numeric columns are object dtype so string assignment is safe
    numeric_columns = {"MRP_Input", "Raw_MRP_Scraped", "Override_MRP", "Override_DP", "Attempts"}
    for col in df.columns:
        if col not in numeric_columns:
            df[col] = df[col].astype("object")
        
    return df


def load_catalogue_data_readonly(
    file_path: str = "data/catalogue_data.xlsx",
    sheet_name: str = "CatalogueData",
    max_retries: int = 5,
    retry_delay: float = 0.1
) -> pd.DataFrame:
    """
    Safely reads a snapshot copy of catalogue data without holding file locks.
    Reads via in-memory bytes with retry logic so that HTTP read requests are never
    blocked by background job writes.
    """
    import io
    import time

    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Catalogue data file not found at: {file_path}")

    last_err = None
    for attempt in range(max_retries):
        try:
            with open(file_path, "rb") as f:
                content = f.read()
            df = pd.read_excel(io.BytesIO(content), sheet_name=sheet_name)

            missing_cols = [col for col in EXPECTED_COLUMNS if col not in df.columns]
            if missing_cols:
                for col in missing_cols:
                    df[col] = None

            numeric_columns = {"MRP_Input", "Raw_MRP_Scraped", "Override_MRP", "Override_DP", "Attempts"}
            for col in df.columns:
                if col not in numeric_columns:
                    df[col] = df[col].astype("object")

            return df
        except (PermissionError, IOError, OSError) as e:
            last_err = e
            time.sleep(retry_delay * (attempt + 1))

    raise last_err or RuntimeError(f"Unable to read catalogue data from {file_path}")


def save_catalogue_data(df: pd.DataFrame, file_path: str = "data/catalogue_data.xlsx", sheet_name: str = "CatalogueData") -> None:
    """
    Safely writes DataFrame back to Excel with lock checking and Write Guard enforcement.
    """
    check_file_lock(file_path)
    
    # Enforce write guard on all rows before writing to disk
    for idx, row in df.iterrows():
        validate_write_guard(row)
        
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    logger.info(f"Saving {len(df)} rows to {file_path}...")
    with pd.ExcelWriter(file_path, engine="openpyxl", mode="w") as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)
    logger.info(f"Successfully saved catalogue data to {file_path}")


def get_effective_value(row: Union[pd.Series, Dict[str, Any]], field: str) -> Any:
    """
    Resolves effective value following ARCHITECTURE.md precedence:
    - DP: Override_DP > MRP_Input
    - MRP: Override_MRP > MRP_Display > Raw_MRP_Scraped
    - Text / Spec / Bullet: Override_{Field} > Raw_{Field}
    - Image: Override_Image_Path > Convention path images/{brand_slug}/{model_slug}.png
    """
    if isinstance(row, pd.Series):
        row_dict = row.to_dict()
    else:
        row_dict = row

    if field == "DP":
        if not is_empty_value(row_dict.get("Override_DP")):
            return row_dict.get("Override_DP")
        if not is_empty_value(row_dict.get("MRP_Input")):
            return row_dict.get("MRP_Input")
        return None

    if field == "MRP":
        if not is_empty_value(row_dict.get("Override_MRP")):
            return row_dict.get("Override_MRP")
        if not is_empty_value(row_dict.get("MRP_Display")):
            return row_dict.get("MRP_Display")
        if not is_empty_value(row_dict.get("Raw_MRP_Scraped")):
            return row_dict.get("Raw_MRP_Scraped")
        return None

    if field == "Image_Path":
        if not is_empty_value(row_dict.get("Override_Image_Path")):
            return str(row_dict.get("Override_Image_Path")).strip()
        brand_slug = slugify(row_dict.get("Brand", ""))
        model_slug = slugify(row_dict.get("Model_Name", ""))
        category_slug = slugify(str(row_dict.get("Category") or "powerbank"))
        
        cat_path = f"images/{category_slug}/{brand_slug}/{model_slug}.png"
        if os.path.exists(cat_path):
            return cat_path
        old_path = f"images/{brand_slug}/{model_slug}.png"
        if os.path.exists(old_path):
            return old_path
        return cat_path

    # Standard override precedence: Override_{field} > Raw_{field} > {field}
    override_key = f"Override_{field}"
    raw_key = f"Raw_{field}"
    
    if override_key in row_dict and not is_empty_value(row_dict.get(override_key)):
        return row_dict.get(override_key)
        
    if raw_key in row_dict and not is_empty_value(row_dict.get(raw_key)):
        return row_dict.get(raw_key)
        
    if field in row_dict and not is_empty_value(row_dict.get(field)):
        return row_dict.get(field)
        
    return None


def get_effective_product_dict(row: Union[pd.Series, Dict[str, Any]], base_dir: str = ".") -> Dict[str, Any]:
    """
    Constructs a complete resolved dictionary for a product row ready for Jinja2 template rendering.
    """
    if isinstance(row, pd.Series):
        row_dict = row.to_dict()
    else:
        row_dict = row

    brand = str(row_dict.get("Brand", "")).strip()
    model_name = str(row_dict.get("Model_Name", "")).strip()
    brand_slug = slugify(brand)
    model_slug = slugify(model_name)

    image_path = get_effective_value(row_dict, "Image_Path")

    bullets = []
    for b_idx in range(1, 5):
        b_val = get_effective_value(row_dict, f"Bullet_{b_idx}")
        if not is_empty_value(b_val):
            bullets.append(str(b_val).strip())

    # Card title resolution chain: Override_Title > Display_Name > Model_Name
    override_title = row_dict.get("Override_Title")
    display_name_val = row_dict.get("Display_Name")
    if not is_empty_value(override_title):
        effective_display_name = str(override_title).strip()
    elif not is_empty_value(display_name_val):
        effective_display_name = str(display_name_val).strip()
    else:
        effective_display_name = model_name
    
    # Price resolution:
    # DP (Dealer Price): Override_DP > MRP_Input
    dp_val = get_effective_value(row_dict, "DP")

    # MRP (Maximum Retail Price): Override_MRP > MRP_Display > Raw_MRP_Scraped
    mrp_val = get_effective_value(row_dict, "MRP")

    category_raw = str(row_dict.get("Category") or "Powerbank").strip()
    category_slug = slugify(category_raw)

    return {
        "product_id": row_dict.get("Product_ID"),
        "category": category_raw,
        "category_slug": category_slug,
        "brand": brand,
        "brand_slug": brand_slug,
        "model_name": model_name,
        "display_name": effective_display_name,
        "model_slug": model_slug,
        "title": get_effective_value(row_dict, "Title") or f"{brand} {model_name}",
        "subtitle": get_effective_value(row_dict, "Subtitle") or "",
        "dp": dp_val,
        "dp_raw": dp_val,
        "price": dp_val,
        "mrp": mrp_val,
        "mrp_raw": dp_val,  # For backward-compatibility with callers reading mrp_raw as DP
        "mrp_display": str(mrp_val).strip() if mrp_val and not is_empty_value(mrp_val) else None,
        "specs": {
            "capacity": get_effective_value(row_dict, "Spec_Capacity") or "",
            "output": get_effective_value(row_dict, "Spec_Output") or "",
            "ports": get_effective_value(row_dict, "Spec_Ports") or "",
            "weight": get_effective_value(row_dict, "Spec_Weight") or "",
            "warranty": get_effective_value(row_dict, "Spec_Warranty") or ""
        },
        "bullets": bullets,
        "image_full_path": os.path.join(base_dir, image_path) if image_path else "",
        "image_status": row_dict.get("Image_Status", "missing"),
        "image_source": row_dict.get("Image_Source"),
        "flags": row_dict.get("Flags"),
        "status": row_dict.get("Status", "Pending")
    }
