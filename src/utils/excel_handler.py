import os
import re
import sys
import pandas as pd
from typing import Dict, Any, Optional, Union
from src.utils.logger import setup_logger

logger = setup_logger("excel_handler")

# Standard flat schema defined in ARCHITECTURE.md
EXPECTED_COLUMNS = [
    "Product_ID", "Brand", "Model_Name", "Product_URL", "Brochure_PDF", "Marketplace_URL", "MRP_Input",
    "Source_URL", "Raw_Title", "Raw_Subtitle", "Raw_MRP_Scraped", "Raw_Spec_Capacity", "Raw_Spec_Output", "Raw_Spec_Ports", "Raw_Spec_Weight", "Raw_Spec_Warranty",
    "Raw_Bullet_1", "Raw_Bullet_2", "Raw_Bullet_3", "Raw_Bullet_4", "Source_Audit", "Image_URL", "Image_Status", "Image_Source",
    "Override_Title", "Override_Subtitle", "Override_MRP", "Override_Spec_Capacity", "Override_Spec_Output", "Override_Spec_Ports", "Override_Spec_Weight", "Override_Spec_Warranty",
    "Override_Bullet_1", "Override_Bullet_2", "Override_Bullet_3", "Override_Bullet_4", "Override_Image_Path",
    "Flags", "Status"
]

class ExcelFileLockedError(Exception):
    """Raised when the Excel file is open and locked by Microsoft Excel or another process."""
    pass


def is_file_locked(file_path: str) -> bool:
    """
    Checks if a file is currently locked by another application (e.g. Microsoft Excel).
    On Windows, Microsoft Excel opens files with exclusive sharing mode which prevents renaming or writing.
    """
    if not os.path.exists(file_path):
        return False
        
    try:
        # On Windows, renaming a file to itself fails with PermissionError [WinError 32]
        # if Microsoft Excel or another program has the file open.
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


def load_catalogue_data(file_path: str = "data/catalogue_data.xlsx", sheet_name: str = "CatalogueData") -> pd.DataFrame:
    """
    Safely reads the catalogue data from Excel, checking for file locks and validating schema columns.
    """
    check_file_lock(file_path)
    
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Catalogue data file not found at: {file_path}")
        
    logger.info(f"Loading catalogue data from {file_path} (sheet: {sheet_name})...")
    df = pd.read_excel(file_path, sheet_name=sheet_name)
    
    # Check for missing columns
    missing_cols = [col for col in EXPECTED_COLUMNS if col not in df.columns]
    if missing_cols:
        logger.warning(f"Missing expected columns in sheet: {missing_cols}")
        for col in missing_cols:
            df[col] = None

    # Ensure all non-numeric columns are object dtype so string assignment is safe
    numeric_columns = {"MRP_Input", "Raw_MRP_Scraped", "Override_MRP"}
    for col in df.columns:
        if col not in numeric_columns:
            df[col] = df[col].astype("object")
        
    return df


def save_catalogue_data(df: pd.DataFrame, file_path: str = "data/catalogue_data.xlsx", sheet_name: str = "CatalogueData") -> None:
    """
    Safely writes DataFrame back to Excel with lock checking.
    """
    check_file_lock(file_path)
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    logger.info(f"Saving {len(df)} rows to {file_path}...")
    with pd.ExcelWriter(file_path, engine="openpyxl", mode="w") as writer:
        df.to_excel(writer, sheet_name=sheet_name, index=False)
    logger.info(f"Successfully saved catalogue data to {file_path}")


def get_effective_value(row: Union[pd.Series, Dict[str, Any]], field: str) -> Any:
    """
    Resolves effective value following ARCHITECTURE.md precedence:
    - MRP: Override_MRP > MRP_Input > Raw_MRP_Scraped
    - Text / Spec / Bullet: Override_{Field} > Raw_{Field}
    - Image: Override_Image_Path > Convention path images/{brand_slug}/{model_slug}.png
    """
    if isinstance(row, pd.Series):
        row_dict = row.to_dict()
    else:
        row_dict = row

    if field == "MRP":
        if not is_empty_value(row_dict.get("Override_MRP")):
            return row_dict.get("Override_MRP")
        if not is_empty_value(row_dict.get("MRP_Input")):
            return row_dict.get("MRP_Input")
        if not is_empty_value(row_dict.get("Raw_MRP_Scraped")):
            return row_dict.get("Raw_MRP_Scraped")
        return None

    if field == "Image_Path":
        if not is_empty_value(row_dict.get("Override_Image_Path")):
            return str(row_dict.get("Override_Image_Path")).strip()
        brand_slug = slugify(row_dict.get("Brand", ""))
        model_slug = slugify(row_dict.get("Model_Name", ""))
        return f"images/{brand_slug}/{model_slug}.png"

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

    # Resolve image path
    image_rel_path = get_effective_value(row_dict, "Image_Path")
    full_image_path = os.path.join(base_dir, image_rel_path) if image_rel_path else ""
    image_exists = os.path.exists(full_image_path) and os.path.isfile(full_image_path)

    # Format MRP
    mrp_val = get_effective_value(row_dict, "MRP")
    try:
        if isinstance(mrp_val, str):
            clean_str = re.sub(r"[^\d.]", "", mrp_val)
            mrp_num = float(clean_str) if clean_str else None
        elif mrp_val is not None and not pd.isna(mrp_val):
            mrp_num = float(mrp_val)
        else:
            mrp_num = None
            
        mrp_formatted = f"₹{int(mrp_num):,}" if mrp_num is not None else "TBD"
    except (ValueError, TypeError):
        mrp_num = None
        mrp_formatted = "TBD"

    bullets = [
        get_effective_value(row_dict, "Bullet_1") or "",
        get_effective_value(row_dict, "Bullet_2") or "",
        get_effective_value(row_dict, "Bullet_3") or "",
        get_effective_value(row_dict, "Bullet_4") or ""
    ]
    # Filter out empty bullets if any
    bullets = [str(b).strip() for b in bullets if str(b).strip() != ""]

    specs = {
        "Capacity": get_effective_value(row_dict, "Spec_Capacity") or "",
        "Output": get_effective_value(row_dict, "Spec_Output") or "",
        "Ports": get_effective_value(row_dict, "Spec_Ports") or "",
        "Weight": get_effective_value(row_dict, "Spec_Weight") or "",
        "Warranty": get_effective_value(row_dict, "Spec_Warranty") or ""
    }

    return {
        "product_id": str(row_dict.get("Product_ID", "")).strip(),
        "brand": brand,
        "brand_slug": brand_slug,
        "model_name": model_name,
        "model_slug": model_slug,
        "title": str(get_effective_value(row_dict, "Title") or model_name).strip(),
        "subtitle": str(get_effective_value(row_dict, "Subtitle") or "").strip(),
        "mrp_raw": mrp_num,
        "mrp": mrp_formatted,
        "specs": specs,
        "bullets": bullets,
        "image_path": image_rel_path,
        "image_full_path": full_image_path,
        "image_exists": image_exists,
        "image_status": str(row_dict.get("Image_Status", "")).strip(),
        "status": str(row_dict.get("Status", "")).strip(),
        "flags": str(row_dict.get("Flags", "")).strip(),
        "source_audit": str(row_dict.get("Source_Audit", "")).strip(),
    }
