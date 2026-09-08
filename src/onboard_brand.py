import os
import re
import sys
import yaml
import json
import pandas as pd
from typing import List, Dict, Any, Optional, Tuple
from pydantic import BaseModel, Field

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.utils.excel_handler import load_catalogue_data, save_catalogue_data, is_empty_value, slugify
from src.utils.logger import setup_logger

logger = setup_logger("onboard")


class RawProductItem(BaseModel):
    raw_text: str = Field(description="Raw line or cell text describing the product")
    model_name: str = Field(description="Cleaned model name with capacity/specs (e.g. 'Mega 20000mAh')")
    display_name: str = Field(description="Shortest clean display name only (e.g. 'Mega', 'Roam Plus')")
    dp: Optional[float] = Field(None, description="Dealer price (DP) in INR")
    mrp: Optional[float] = Field(None, description="Maximum retail price (MRP) in INR")
    notes: Optional[str] = Field(None, description="Brief note on what was cleaned (e.g. 'Stripped color Black')")


class QualifierTokenItem(BaseModel):
    token: str = Field(description="Variant modifier token e.g. Plus, Pro, Max, Mini")
    rationale: str = Field(description="Short rationale explaining why it is a variant modifier")


class ColumnMappingItem(BaseModel):
    column_name: str = Field(description="Original sheet header name")
    role: str = Field(description="Role: 'Model_Name', 'DP', 'MRP', or 'ignored'")


class BrandInferenceSchema(BaseModel):
    brand_name: str = Field(description="Brand name (e.g. 'Stuffcool', 'Pebble', 'Portronics', 'Anker')")
    brand_code: str = Field(description="2-4 character uppercase brand code for Product_ID (e.g. 'SC', 'PEB', 'POR', 'ANK')")
    category: Optional[str] = Field(default="Powerbank", description="Category name (e.g. 'Powerbank', 'Smartwatch', 'Audio')")
    domain: Optional[str] = Field(default=None, description="Official brand website domain ONLY IF explicitly written in the price sheet text, else null")
    platform: Optional[str] = Field(default=None, description="Website platform ('shopify' or 'custom') ONLY if domain was verified, else null")
    qualifier_tokens: List[QualifierTokenItem] = Field(
        default_factory=list,
        description="List of variant qualifier tokens appropriate for this brand with rationale. Tokens must be variant modifiers (Plus, Pro, Max, Mini) and NOT product model names.",
    )
    dp_column_explanation: str = Field(description="Explanation of how DP and MRP columns were identified")
    column_mapping: List[ColumnMappingItem] = Field(
        default_factory=list,
        description="List of mapped sheet columns and their roles"
    )
    products: List[RawProductItem] = Field(description="List of extracted product rows")


class AIServiceUnavailableError(Exception):
    """Raised when all AI providers and retries are exhausted."""
    def __init__(self, message: str = "The AI service is unavailable. Try again in a few minutes.", technical_details: Optional[str] = None):
        super().__init__(message)
        self.user_message = message
        self.technical_details = technical_details


def extract_text_from_file(file_path: str, llm_config: dict, progress_cb: Optional[Any] = None) -> Tuple[str, str]:
    """
    Detects file format and extracts text content.
    Returns (extracted_text, format_type).
    """
    ext = os.path.splitext(file_path)[1].lower()

    if ext in [".xlsx", ".xls", ".csv"]:
        if ext == ".csv":
            df = pd.read_csv(file_path)
        else:
            df = pd.read_excel(file_path)
        csv_text = df.to_csv(index=False)
        return csv_text, "table"

    elif ext == ".pdf":
        import pdfplumber
        text_lines = []
        with pdfplumber.open(file_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text_lines.append(t)
        combined_text = "\n".join(text_lines)
        if len(combined_text.strip()) > 50:
            return combined_text, "pdf_text"
        else:
            # Fallback to rendering first few pages to Gemini Vision
            import pypdfium2 as pdfium
            import io
            doc = pdfium.PdfDocument(file_path)
            images = []
            for i in range(min(5, len(doc))):
                pil_img = doc[i].render(scale=2.0).to_pil()
                buf = io.BytesIO()
                pil_img.save(buf, format="PNG")
                images.append((buf.getvalue(), "image/png"))
            doc.close()
            return _ocr_images_via_gemini(images, llm_config, progress_cb=progress_cb), "pdf_vision"

    elif ext in [".png", ".jpg", ".jpeg", ".webp"]:
        with open(file_path, "rb") as f:
            img_bytes = f.read()
        mime = "image/png" if ext == ".png" else "image/jpeg"
        return _ocr_images_via_gemini([(img_bytes, mime)], llm_config, progress_cb=progress_cb), "image_vision"

    elif ext in [".txt", ".md"]:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read(), "text"

    else:
        raise ValueError(f"Unsupported file format: {ext}")


def _ocr_images_via_gemini(
    image_parts: List[Tuple[bytes, str]],
    llm_config: dict,
    progress_cb: Optional[Any] = None
) -> str:
    """
    Uses Gemini Vision with retries and fallback to Groq Vision to OCR price sheet images/pages.
    """
    import time
    from google.genai import types
    from src.utils.llm_client import classify_gemini_error, get_gemini_client

    candidate_models = ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.6-flash"]
    configured_model = llm_config.get("model")
    if configured_model and configured_model in candidate_models:
        candidate_models.remove(configured_model)
        candidate_models.insert(0, configured_model)

    prompt = (
        "You are an expert tabular OCR engine. Extract all rows, headers, and product pricing information "
        "from the given dealer price sheet images. Preserve product codes, names, capacities, DP, and MRP. "
        "Output the result as a clean markdown table or structured CSV text."
    )

    contents = [prompt]
    for b_data, m_type in image_parts:
        contents.append(types.Part.from_bytes(data=b_data, mime_type=m_type))

    last_error = None

    # 1. Primary: Gemini Models with Backoff Retries
    for model in candidate_models:
        for attempt in range(3):
            try:
                client = get_gemini_client()
                logger.info(f"[Ingest OCR] Calling Gemini Vision ({model}), attempt {attempt+1}/3...")
                response = client.models.generate_content(
                    model=model,
                    contents=contents
                )
                text = response.text or ""
                if text.strip():
                    logger.info(f"[Ingest OCR] Successfully extracted text via Gemini Vision ({model}).")
                    return text
            except Exception as e:
                last_error = e
                err_type = classify_gemini_error(e)
                logger.warning(f"[Ingest OCR] Error on model '{model}' attempt {attempt+1}: {e} (type={err_type})")

                if err_type in ("TRANSIENT", "RATE_LIMIT") and attempt < 2:
                    backoff_sec = 2.0 * (attempt + 1)
                    if progress_cb:
                        progress_cb({
                            "stage": "retry",
                            "message": f"The AI service is busy right now. Trying again in {backoff_sec:.0f}s..."
                        })
                    time.sleep(backoff_sec)
                    continue
                else:
                    break

    # 2. Secondary: Groq Vision Fallback if configured
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        try:
            from src.utils.llm_client import get_groq_client, classify_groq_error
            import base64
            groq_client = get_groq_client()
            groq_vision_models = ["llama-3.2-11b-vision-preview", "llama-3.2-90b-vision-preview"]
            for g_model in groq_vision_models:
                logger.info(f"[Ingest OCR] Falling back to Groq Vision ({g_model})...")
                if progress_cb:
                    progress_cb({"stage": "fallback", "message": "Falling back to secondary AI provider (Groq)..."})

                content_items = [{"type": "text", "text": prompt}]
                for b_data, m_type in image_parts[:2]:
                    b64 = base64.b64encode(b_data).decode("utf-8")
                    content_items.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{m_type};base64,{b64}"}
                    })

                res = groq_client.chat.completions.create(
                    model=g_model,
                    messages=[{"role": "user", "content": content_items}],
                    temperature=0.1
                )
                text = res.choices[0].message.content or ""
                if text.strip():
                    logger.info(f"[Ingest OCR] Successfully extracted text via Groq Vision ({g_model}).")
                    return text
        except Exception as ge:
            logger.warning(f"[Ingest OCR] Groq Vision fallback failed: {ge}")
            last_error = ge

    raise AIServiceUnavailableError(
        "The AI service is unavailable. Try again in a few minutes.",
        technical_details=f"OCR failed across all vision models and retries. Last error: {last_error}"
    )


def analyze_price_sheet(
    raw_content: str,
    category_name: str,
    brand_name: Optional[str] = None,
    llm_config: Optional[dict] = None,
    progress_cb: Optional[Any] = None
) -> BrandInferenceSchema:
    """
    Uses structured LLM output to parse raw sheet content and extract product rows.
    If brand_name is specified by the user, brand inference is skipped and the provided brand is enforced.
    Implements multi-model exponential backoff retry and Groq fallback chain.
    """
    if not raw_content or not raw_content.strip():
        raise ValueError("No readable text found in price sheet to analyze.")

    llm_config = llm_config or {}

    if brand_name and brand_name.strip():
        clean_brand = brand_name.strip()
        prompt = f"""
You are analyzing a dealer price sheet.
The user has already specified the Brand: '{clean_brand}' and Category: '{category_name}'.
Do NOT infer or override the Brand Name. Set brand_name to '{clean_brand}' and category to '{category_name}'.

Execute these tasks:
1. Assign a concise 2-4 uppercase brand code for Product_ID generation (e.g. derived from '{clean_brand}', like 'SC', 'PEB', 'POR', 'ANK').
2. DOMAIN & PLATFORM (CRITICAL RULE):
   - Do NOT guess, invent, or fabricate a website domain from the brand name.
   - ONLY return a domain if an official website URL or domain is EXPLICITLY written in the price sheet text itself (e.g. 'www.stuffcool.com' appears in the header or footer of the sheet).
   - If no website URL or domain is explicitly written in the price sheet text, you MUST return null for `domain` and null for `platform`.
   - Example 1: Sheet text contains 'For warranty visit www.portronics.com' -> domain: 'portronics.com'.
   - Example 2: Sheet text has only product names and prices without an explicit website URL -> domain: null, platform: null. (NEVER guess '{clean_brand.lower().replace(" ", "")}.com'!).
3. Disambiguate price columns:
   - If two prices exist: the lower price is Dealer Price (DP), the higher price is Maximum Retail Price (MRP).
   - If one price exists: it is Dealer Price (DP).
4. Extract product rows:
   - Strip internal SKU codes, brand prefixes, and color variant suffixes (e.g. 'Black', 'White') to produce `model_name`.
   - Generate `display_name`: the SHORTEST clean product title for the catalogue card (e.g. 'Mega', 'Major', 'Roam Plus', 'Power Shutter' — NOT 'Mega 20000mAh Powerbank').
   - Extract `dp` and `mrp` as numbers if found in the row.
5. Select Brand-Specific Qualifier Tokens:
   - Analyze naming patterns for true variant/modifier suffixes (e.g. 'Plus', 'Pro', 'Max', 'Mini', 'Ultra', 'Lite', 'Go').
   - DO NOT include actual product model names.
6. Column Mapping:
   - Map original headers to roles: 'Model_Name', 'DP', 'MRP', or 'ignored'.

PRICE SHEET CONTENT:
{raw_content}
"""
    else:
        prompt = f"""
Analyze this dealer price sheet for a brand in the '{category_name}' category.

Execute these tasks autonomously:
1. Infer the Brand Name (e.g. 'Stuffcool', 'Pebble', 'Portronics', 'Anker', 'Ambrane').
2. Assign a concise 2-4 uppercase brand code for Product_ID generation (e.g. 'SC', 'PEB', 'POR', 'ANK').
3. DOMAIN & PLATFORM (CRITICAL RULE):
   - Do NOT guess, invent, or fabricate a website domain from the brand name.
   - ONLY return a domain if an official website URL or domain is EXPLICITLY written in the price sheet text itself.
   - If no website URL or domain is explicitly present in the price sheet text, you MUST return null for `domain` and null for `platform`.
   - Example: Sheet text has only product names and prices without a URL -> domain: null, platform: null.
4. Disambiguate price columns:
   - If two prices exist: the lower price is Dealer Price (DP), the higher price is Maximum Retail Price (MRP).
   - If one price exists: it is Dealer Price (DP).
5. Extract product rows:
   - Strip internal SKU codes, brand prefixes, and color variant suffixes (e.g. 'Black', 'White') to produce `model_name`.
   - Generate `display_name`: the SHORTEST clean product title for the catalogue card (e.g. 'Mega', 'Major', 'Roam Plus', 'Power Shutter' — NOT 'Mega 20000mAh Powerbank').
   - Extract `dp` and `mrp` as numbers if found in the row.
6. Select Brand-Specific Qualifier Tokens:
   - Analyze the catalog's naming patterns to identify true variant/modifier suffixes (e.g. 'Plus', 'Pro', 'Max', 'Mini', 'Ultra', 'Lite', 'Go').
   - Provide a short rationale for each token explaining why it is a variant modifier.
   - DO NOT include actual product model names.
7. Column Mapping:
   - Provide a key-value mapping of each original column/header in the sheet to its mapped role: 'Model_Name', 'DP', 'MRP', or 'ignored'.

PRICE SHEET CONTENT:
{raw_content}
"""

    import time
    from google.genai import types
    from src.utils.llm_client import classify_gemini_error, classify_groq_error, get_gemini_client, get_groq_client

    candidate_gemini_models = ["gemini-3.5-flash-lite", "gemini-3.5-flash", "gemini-3.6-flash"]
    configured_model = llm_config.get("model")
    if configured_model and configured_model in candidate_gemini_models:
        candidate_gemini_models.remove(configured_model)
        candidate_gemini_models.insert(0, configured_model)

    last_error = None

    # 1. Primary: Gemini Models with Backoff Retries
    for model in candidate_gemini_models:
        for attempt in range(3):
            try:
                client = get_gemini_client()
                logger.info(f"[Analyze Sheet] Calling Gemini ({model}), attempt {attempt+1}/3...")
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=BrandInferenceSchema,
                        temperature=0.1
                    )
                )
                result_json = json.loads(response.text)
                logger.info(f"[Analyze Sheet] Successfully analyzed price sheet via Gemini ({model}).")
                inference = BrandInferenceSchema(**result_json)
                if brand_name and brand_name.strip():
                    inference.brand_name = brand_name.strip()
                if category_name and category_name.strip():
                    inference.category = category_name.strip()

                # Verify domain against live storefront rules
                v_domain, v_platform = verify_storefront_domain(inference.domain)
                inference.domain = v_domain
                inference.platform = v_platform
                return inference
            except Exception as e:
                last_error = e
                err_type = classify_gemini_error(e)
                logger.warning(f"[Analyze Sheet] Error on Gemini '{model}' attempt {attempt+1}: {e} (type={err_type})")

                if err_type in ("TRANSIENT", "RATE_LIMIT") and attempt < 2:
                    backoff_sec = 2.0 * (attempt + 1)
                    if progress_cb:
                        progress_cb({
                            "stage": "retry",
                            "message": f"The AI service is busy right now. Trying again in {backoff_sec:.0f}s..."
                        })
                    time.sleep(backoff_sec)
                    continue
                else:
                    break

    # 2. Secondary: Groq Fallback Chain
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        groq_model = "llama-3.3-70b-versatile"
        logger.info(f"[Analyze Sheet] Failing over to Groq ({groq_model})...")
        if progress_cb:
            progress_cb({
                "stage": "fallback",
                "message": "The AI service is busy. Failing over to secondary provider (Groq)..."
            })

        for attempt in range(3):
            try:
                groq_client = get_groq_client()
                system_inst = (
                    "You are an expert dealer price sheet analyzer for corporate gifting catalogues.\n"
                    "You MUST return valid JSON matching BrandInferenceSchema with fields: "
                    "brand_name, brand_code, domain, platform, qualifier_tokens, dp_column_explanation, column_mapping, products."
                )
                res = groq_client.chat.completions.create(
                    model=groq_model,
                    messages=[
                        {"role": "system", "content": system_inst},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1
                )
                result_json = json.loads(res.choices[0].message.content)
                logger.info(f"[Analyze Sheet] Successfully analyzed price sheet via Groq ({groq_model}).")
                inference = BrandInferenceSchema(**result_json)
                if brand_name and brand_name.strip():
                    inference.brand_name = brand_name.strip()
                if category_name and category_name.strip():
                    inference.category = category_name.strip()

                # Verify domain against live storefront rules
                v_domain, v_platform = verify_storefront_domain(inference.domain)
                inference.domain = v_domain
                inference.platform = v_platform
                return inference
            except Exception as ge:
                last_error = ge
                err_type = classify_groq_error(ge)
                logger.warning(f"[Analyze Sheet] Groq error on attempt {attempt+1}: {ge} (type={err_type})")
                if err_type in ("TRANSIENT", "RATE_LIMIT") and attempt < 2:
                    backoff_sec = 2.0 * (attempt + 1)
                    if progress_cb:
                        progress_cb({
                            "stage": "retry",
                            "message": f"The AI service is busy right now. Trying again in {backoff_sec:.0f}s..."
                        })
                    time.sleep(backoff_sec)
                    continue
                else:
                    break

    # 3. All Exhausted
    logger.error(f"[Analyze Sheet] All AI providers exhausted after retries. Last error: {last_error}")
    raise AIServiceUnavailableError(
        "The AI service is unavailable. Try again in a few minutes.",
        technical_details=f"All AI providers exhausted. Last error: {last_error}"
    )


def generate_onboarding_summary(inference: BrandInferenceSchema) -> str:
    """Generates the formatted inference summary markdown table with platform & bot-block probe."""
    lines = []
    lines.append(f"### 📋 Onboarding Inference Summary for Brand '{inference.brand_name}'")
    lines.append(f"- **Brand Inferred:** `{inference.brand_name}` (Brand Code: `{inference.brand_code}`)")
    lines.append(f"- **Price Mapping:** {inference.dp_column_explanation}")

    if inference.domain:
        from src.parsers.generic import detect_ecommerce_platform
        platform_detected, platform_detail = detect_ecommerce_platform(inference.domain)
        lines.append(f"- **Official Domain & Platform:** `{inference.domain}` (Inferred: `{inference.platform}`, Live Probe: `{platform_detected}` — {platform_detail})")
        if platform_detected == "custom":
            lines.append(f"- **Platform Status:** ⚠️ E-commerce platform is non-standard / custom. Generic JSON-LD + Sitemap scraper will be active.")
        if platform_detail and ("403" in platform_detail or "error" in platform_detail.lower()):
            lines.append(f"- **Bot Challenge Status:** 🚨 Brand domain is protected or blocking requests ({platform_detail}). Brand will fall straight through to Retail Tier 3.")
    else:
        lines.append("- **Official Domain & Platform:** No verified brand website found in price sheet. Tiers 1 and 2 will be skipped.")

    lines.append(f"- **Selected Qualifier Tokens & Justification:**")
    for q in inference.qualifier_tokens:
        token_name = q.token if hasattr(q, "token") else (q.get("token") if isinstance(q, dict) else str(q))
        rationale = q.rationale if hasattr(q, "rationale") else (q.get("rationale") if isinstance(q, dict) else "")
        lines.append(f"  - `{token_name}`: {rationale}")
    
    # Check duplicates
    seen = {}
    duplicates = []
    for idx, p in enumerate(inference.products, 1):
        key = (inference.brand_name.lower(), p.model_name.lower(), p.dp)
        if key in seen:
            duplicates.append(f"Row {idx} ('{p.model_name}') duplicates Row {seen[key]}")
        else:
            seen[key] = idx

    lines.append(f"- **Duplicates Identified:** {', '.join(duplicates) if duplicates else 'None'}\n")
    
    lines.append("| Product_ID | Model_Name | Display_Name | DP (₹) | MRP (₹) | Inferred Notes |")
    lines.append("| :--- | :--- | :--- | :--- | :--- | :--- |")
    
    for idx, p in enumerate(inference.products, 1):
        pid = f"PB-{inference.brand_code}-{idx:03d}"
        dp_str = f"{int(p.dp):,}" if p.dp is not None else "TBD"
        mrp_str = f"{int(p.mrp):,}" if p.mrp is not None else "TBD"
        notes_str = p.notes or "-"
        lines.append(f"| `{pid}` | {p.model_name} | **{p.display_name}** | ₹{dp_str} | ₹{mrp_str} | {notes_str} |")

    return "\n".join(lines)

def verify_storefront_domain(domain: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Verifies that a candidate domain extracted from the price sheet is a live storefront.
    Rejects parked domains, registrar landing pages, empty shells, and redirect landers.
    Returns (verified_domain, verified_platform) if genuine storefront, else (None, None).
    """
    if not domain or not isinstance(domain, str):
        return None, None
    
    clean_domain = domain.strip().lower()
    clean_domain = re.sub(r"^https?://", "", clean_domain).split("/")[0].strip()
    if not clean_domain or "." not in clean_domain:
        return None, None
        
    url = f"https://{clean_domain}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    
    import urllib.request
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=6) as resp:
            final_url = resp.geturl().lower()
            # 1. Check redirect to parked lander
            if any(p in final_url for p in ["/lander", "/parking", "sedo.com", "godaddy.com", "hugedomains", "dan.com", "afternic"]):
                logger.warning(f"[Domain Verification] '{clean_domain}' redirected to parked landing page: {final_url}")
                return None, None
                
            raw_html = resp.read(65536).decode("utf-8", errors="ignore").lower()
            
            # 2. Check parked domain signatures in page content
            parked_signatures = [
                "window.location.href=\"/lander\"",
                "window.location.href='/lander'",
                "buy this domain",
                "is parked free",
                "this domain is for sale",
                "domain may be for sale",
                "registered at namecheap",
                "parked domain",
                "renew now",
                "inquire about this domain"
            ]
            if any(sig in raw_html for sig in parked_signatures):
                logger.warning(f"[Domain Verification] '{clean_domain}' contains parked domain indicators.")
                return None, None
                
            # 3. Confirm presence of commerce/product content
            commerce_indicators = [
                "cart", "checkout", "product", "shop", "price", "add to cart", "catalog", "collection", "inr", "₹", "rs."
            ]
            matches = sum(1 for ind in commerce_indicators if ind in raw_html)
            if matches < 2:
                logger.warning(f"[Domain Verification] '{clean_domain}' has insufficient commerce indicators (score={matches}). Rejecting.")
                return None, None
                
            # 4. Check platform (Shopify or custom)
            platform = "custom"
            try:
                p_url = f"https://{clean_domain}/products.json?limit=1"
                p_req = urllib.request.Request(p_url, headers=headers)
                with urllib.request.urlopen(p_req, timeout=4) as p_resp:
                    if p_resp.status == 200:
                        data = json.loads(p_resp.read(4096).decode("utf-8", errors="ignore"))
                        if "products" in data:
                            platform = "shopify"
            except Exception:
                pass
                
            logger.info(f"[Domain Verification] Verified live storefront for '{clean_domain}' (platform={platform}).")
            return clean_domain, platform
            
    except Exception as e:
        logger.warning(f"[Domain Verification] Verification failed for '{clean_domain}': {e}")
        return None, None


def derive_category_prefix(category: str) -> str:
    """Derives a standard uppercase prefix for Product_ID based on category."""
    cat_clean = str(category or "Powerbank").strip()
    cat_upper = cat_clean.upper()
    prefix_map = {
        "POWERBANK": "PB",
        "POWERBANKS": "PB",
        "TWS": "TWS",
        "EARBUDS": "TWS",
        "SMARTWATCH": "SW",
        "SMARTWATCHES": "SW",
        "CHARGER": "CH",
        "CHARGERS": "CH",
        "CABLE": "CB",
        "CABLES": "CB",
        "SPEAKER": "SPK",
        "SPEAKERS": "SPK",
        "AUDIO": "AUD",
        "ACCESSORY": "ACC",
        "ACCESSORIES": "ACC"
    }
    if cat_upper in prefix_map:
        return prefix_map[cat_upper]
    clean_alpha = re.sub(r'[^A-Za-z0-9]', '', cat_clean).upper()
    return clean_alpha[:3] if clean_alpha else "PRD"


def register_brand_config(
    inference: BrandInferenceSchema,
    category: Optional[str] = None,
    config_path: str = "config/brand_defaults.yaml"
) -> None:
    """Updates config/brand_defaults.yaml with the inferred brand and category configuration."""
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    if os.path.exists(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            brand_cfg = yaml.safe_load(f) or {}
    else:
        brand_cfg = {}

    if "brands" not in brand_cfg or not isinstance(brand_cfg["brands"], dict):
        brand_cfg["brands"] = {}

    cat_name = category or getattr(inference, "category", None) or "Powerbank"
    clean_brand = inference.brand_name.strip()
    
    # Locate existing brand entry (case-insensitive)
    existing_key = None
    for k in brand_cfg["brands"]:
        if k.lower() == clean_brand.lower():
            existing_key = k
            break

    target_key = existing_key or clean_brand
    current_entry = brand_cfg["brands"].get(target_key, {})

    # Update shared brand properties if newly inferred domain exists
    if inference.domain:
        current_entry["domain"] = inference.domain
    if inference.platform:
        current_entry["platform"] = inference.platform

    # Extract qualifier tokens
    token_list = []
    for q in inference.qualifier_tokens:
        tok = q.token if hasattr(q, "token") else (q.get("token") if isinstance(q, dict) and "token" in q else str(q))
        if tok and tok not in token_list:
            token_list.append(tok)

    if "categories" not in current_entry or not isinstance(current_entry["categories"], dict):
        current_entry["categories"] = {}

    cat_entry = current_entry["categories"].get(cat_name, {})
    if token_list:
        cat_entry["qualifier_tokens"] = token_list

    current_entry["categories"][cat_name] = cat_entry
    brand_cfg["brands"][target_key] = current_entry

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(brand_cfg, f, sort_keys=False, default_flow_style=False)
    logger.info(f"Registered '{clean_brand}' [{cat_name}] defaults in {config_path}")


def append_products_to_catalogue(
    inference: BrandInferenceSchema,
    excel_path: str = "data/catalogue_data.xlsx",
    category: Optional[str] = None
) -> Tuple[int, List[Dict[str, Any]]]:
    """
    Appends or updates newly onboarded product rows in the master Excel catalogue idempotently.
    Matches rows on (Brand + Category + Model_Name).
    Assigns sequential Product_IDs with dynamic category prefix for new rows without collisions.
    """
    df = load_catalogue_data(excel_path)
    cat = category or getattr(inference, "category", None) or "Powerbank"
    clean_brand = inference.brand_name.strip()
    cat_prefix = derive_category_prefix(cat)
    
    existing_brand_mask = df["Brand"].astype(str).str.strip().str.lower() == clean_brand.lower()
    brand_df = df[existing_brand_mask]

    # Find maximum existing sequence number for this brand and prefix pattern
    pattern = re.compile(rf"^{re.escape(cat_prefix)}-{re.escape(inference.brand_code)}-(\d+)$", re.IGNORECASE)
    existing_seqs = []
    for raw_pid in brand_df["Product_ID"].dropna():
        m = pattern.search(str(raw_pid).strip())
        if m:
            try:
                existing_seqs.append(int(m.group(1)))
            except ValueError:
                continue

    next_seq = max(existing_seqs) + 1 if existing_seqs else 1
    all_existing_pids = set(df["Product_ID"].dropna().astype(str).str.strip().str.lower())

    result_rows = []
    new_rows_to_append = []

    for p in inference.products:
        clean_model = p.model_name.strip()
        # Check if an existing row matches this brand, category, AND model_name
        model_match_mask = (
            existing_brand_mask
            & (df["Category"].astype(str).str.strip().str.lower() == cat.strip().lower())
            & (df["Model_Name"].astype(str).str.strip().str.lower() == clean_model.lower())
        )
        
        if model_match_mask.any():
            # Update existing row in place idempotently
            match_idx = df[model_match_mask].index[0]
            existing_pid = df.at[match_idx, "Product_ID"]
            
            df.at[match_idx, "Category"] = cat
            df.at[match_idx, "Display_Name"] = p.display_name
            df.at[match_idx, "MRP_Input"] = p.dp
            df.at[match_idx, "MRP_Display"] = p.mrp
            
            # If status was Pending or Skipped, reset to Pending so collection runs on it
            if df.at[match_idx, "Status"] in ("Pending", "Skipped", "Deferred"):
                df.at[match_idx, "Status"] = "Pending"
                df.at[match_idx, "Attempts"] = 0
                df.at[match_idx, "Flags"] = None
                df.at[match_idx, "Fix_Log"] = None
                
            updated_dict = df.loc[match_idx].to_dict()
            result_rows.append(updated_dict)
            logger.info(f"Updated existing row for '{clean_brand}' [{cat}] - '{clean_model}' ({existing_pid}) in {excel_path}")
        else:
            # Assign next sequential Product_ID using dynamic category prefix
            pid = f"{cat_prefix}-{inference.brand_code}-{next_seq:03d}"
            while pid.lower() in all_existing_pids:
                next_seq += 1
                pid = f"{cat_prefix}-{inference.brand_code}-{next_seq:03d}"
            all_existing_pids.add(pid.lower())
            next_seq += 1

            row_dict = {
                "Product_ID": pid,
                "Brand": clean_brand,
                "Category": cat,
                "Model_Name": clean_model,
                "Display_Name": p.display_name,
                "MRP_Input": p.dp,
                "MRP_Display": p.mrp,
                "Status": "Pending",
                "Attempts": 0
            }
            new_rows_to_append.append(row_dict)
            result_rows.append(row_dict)

    if new_rows_to_append:
        new_df = pd.DataFrame(new_rows_to_append)
        combined_df = pd.concat([df, new_df], ignore_index=True)
    else:
        combined_df = df

    save_catalogue_data(combined_df, excel_path)
    logger.info(f"Processed {len(result_rows)} rows ({len(new_rows_to_append)} new, {len(result_rows) - len(new_rows_to_append)} updated) for brand '{clean_brand}' [{cat}] to {excel_path}")
    return len(result_rows), result_rows
