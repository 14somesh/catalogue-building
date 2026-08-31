import os
import json
import time
from typing import Optional, Dict, Any, List, Tuple
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from google import genai
from google.genai import types

from src.utils.logger import setup_logger

logger = setup_logger("llm_client")

load_dotenv()


class GeminiDailyQuotaExhaustedError(Exception):
    """Raised when the Gemini daily / per-day quota is exhausted. Requires immediate fast-fail without retry."""
    pass


class GeminiRateLimitError(Exception):
    """Raised when a per-minute rate limit is hit and backoff retry is warranted."""
    pass


class GeminiTransientError(Exception):
    """Raised on 503, connection errors, timeouts, etc."""
    pass


class ProductCopySchema(BaseModel):
    title: str = Field(description="Crisp marketing product title (max 40 characters)")
    subtitle: str = Field(description="Engaging subtitle or key capability statement (max 80 characters)")
    bullet_1: str = Field(description="Sales bullet point 1 (MUST be <= 60 characters)")
    bullet_2: str = Field(description="Sales bullet point 2 (MUST be <= 60 characters)")
    bullet_3: str = Field(description="Sales bullet point 3 (MUST be <= 60 characters)")
    bullet_4: str = Field(description="Sales bullet point 4 (MUST be <= 60 characters)")


class SemanticAuditSchema(BaseModel):
    is_valid: bool = Field(description="True if product data is completely consistent and free of contradictions/hallucinations, False if issues exist")
    contradictions: List[str] = Field(default_factory=list, description="Contradictions between subtitle, bullets, and specs")
    factual_discrepancies: List[str] = Field(default_factory=list, description="Claims that contradict or cannot be substantiated by source text")
    tone_and_quality_issues: List[str] = Field(default_factory=list, description="Tone, grammar, or awkward phrasing issues")
    summary_flags: List[str] = Field(default_factory=list, description="Concise error/warning flags to record in the Excel Flags column")


def classify_gemini_error(e: Exception) -> str:
    """
    Classifies Gemini API exceptions into three distinct kinds:
    1. 'QUOTA_EXHAUSTED' -> Daily/per-project quota violation (e.g. GenerateRequestsPerDayPerProjectPerModel-FreeTier). Fast fail!
    2. 'RATE_LIMIT' -> 429 with short retryDelay / per-minute quota. Back off and retry.
    3. 'TRANSIENT' -> 503, timeout, connection reset, overloaded, unavailable. Back off and retry.
    4. 'OTHER' -> Non-recoverable client/auth errors.
    """
    err_str = str(e).lower()
    
    # 1. Daily Quota Check (FAIL FAST)
    daily_markers = [
        "generaterequestsperday",
        "perday",
        "per_day",
        "daily",
        "quota exceeded",
        "free_tier_daily",
    ]
    if any(marker in err_str for marker in daily_markers):
        return "QUOTA_EXHAUSTED"

    # Also check if error string explicitly includes GenerateRequestsPerDayPerProjectPerModel
    if "generaterequestsperdayperprojectpermodel" in err_str:
        return "QUOTA_EXHAUSTED"

    # 2. Rate Limit (Per-Minute, recoverable via backoff)
    rate_limit_markers = [
        "generaterequestsperminute",
        "perminute",
        "per_minute",
        "rate limit",
        "ratelimit",
        "429",
        "resource_exhausted",
        "resourceexhausted"
    ]
    if any(marker in err_str for marker in rate_limit_markers):
        return "RATE_LIMIT"

    # 3. Transient errors (503, connection issues, timeouts)
    transient_markers = [
        "503", "500", "502", "504",
        "overloaded", "unavailable", "deadlineexceeded",
        "connectionerror", "connection reset", "broken pipe",
        "remotedisconnected", "timeout", "timed out"
    ]
    if any(marker in err_str for marker in transient_markers):
        return "TRANSIENT"

    return "OTHER"


def get_gemini_client() -> genai.Client:
    """Initializes and returns the Google GenAI client."""
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not found in environment or .env file.")
    return genai.Client(api_key=api_key)


def preflight_quota_check(model: str = "gemini-3.6-flash") -> Tuple[bool, Optional[str]]:
    """
    PRE-FLIGHT QUOTA CHECK:
    Makes a single minimal, cheap test call to confirm the API is reachable and has quota.
    If daily quota is exhausted, halts immediately with GeminiDailyQuotaExhaustedError.
    """
    client = get_gemini_client()
    try:
        logger.info(f"[Pre-flight] Testing Gemini API connectivity & quota on model '{model}'...")
        response = client.models.generate_content(
            model=model,
            contents="ping",
            config=types.GenerateContentConfig(
                max_output_tokens=5,
                temperature=0.0
            )
        )
        logger.info("[Pre-flight] Gemini API check passed successfully.")
        return True, None
    except Exception as e:
        err_type = classify_gemini_error(e)
        if err_type == "QUOTA_EXHAUSTED":
            msg = "Gemini daily quota exhausted. Run halted. No rows were modified."
            logger.error(msg)
            raise GeminiDailyQuotaExhaustedError(msg)
        logger.error(f"[Pre-flight] Gemini API check failed: {e}")
        return False, str(e)


def draft_bullets_and_subtitle(
    brand: str,
    model_name: str,
    product_description_block: str,
    specs: Dict[str, str],
    model: str = "gemini-3.6-flash",
    temperature: float = 0.2,
    max_retries: int = 4
) -> Optional[Dict[str, Any]]:
    """
    LLM SCOPED JOB:
    Given clean product text and specs ALREADY FETCHED by scripts from a verified URL,
    drafts 4 concise sales bullets (<= 60 chars each) and an engaging subtitle (<= 80 chars).
    Retries transient API/network errors with exponential backoff.
    FAILS FAST on daily quota exhaustion.
    """
    client = get_gemini_client()

    system_prompt = (
        "You are an expert technical product copywriter for a corporate gifting catalogue. "
        "Your single task is to write 4 concise, high-impact sales bullet points and an engaging subtitle "
        "based strictly on the provided verified product description and specifications.\n\n"
        "STRICT CONSTRAINTS:\n"
        "1. ZERO HALLUCINATION: Only make claims directly supported by the provided text. Never invent features.\n"
        "2. BULLET LENGTH LIMIT: Every bullet point (bullet_1 to bullet_4) MUST BE AT MOST 60 CHARACTERS.\n"
        "3. SUBTITLE LIMIT: Subtitle must be at most 80 characters.\n"
        "4. NO GENERIC BOILERPLATE: Avoid phrases like 'free shipping', 'leading brand', 'reliable and durable', 'homegrown'."
    )

    user_prompt = (
        f"Brand: {brand}\n"
        f"Model: {model_name}\n"
        f"Verified Specs: {json.dumps(specs)}\n\n"
        f"--- VERIFIED PRODUCT DESCRIPTION BLOCK ---\n"
        f"{product_description_block[:1500]}\n"
        f"------------------------------------------\n\n"
        "Draft the title, subtitle, and 4 sales bullets according to ProductCopySchema."
    )

    logger.info(f"Calling Gemini ({model}) to draft copy for {brand} {model_name}...")
    
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=[system_prompt, user_prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ProductCopySchema,
                    temperature=temperature
                )
            )

            parsed_data = json.loads(response.text)
            
            # Post-process enforce 60-char bullet limit
            for b_key in ["bullet_1", "bullet_2", "bullet_3", "bullet_4"]:
                val = str(parsed_data.get(b_key, "")).strip()
                if len(val) > 60:
                    logger.warning(f"{b_key} exceeded 60 chars ({len(val)} chars). Trimming...")
                    parsed_data[b_key] = val[:60].rsplit(" ", 1)[0]

            return parsed_data

        except Exception as e:
            last_error = e
            err_type = classify_gemini_error(e)

            if err_type == "QUOTA_EXHAUSTED":
                msg = "Gemini daily quota exhausted. Run halted. No rows were modified."
                logger.error(msg)
                raise GeminiDailyQuotaExhaustedError(msg)
            
            elif err_type == "RATE_LIMIT":
                if attempt < max_retries:
                    backoff_sec = 15 + (attempt * 5)
                    logger.warning(f"Rate limit (429 per-minute) on attempt {attempt}/{max_retries}. Retrying in {backoff_sec}s...")
                    time.sleep(backoff_sec)
                else:
                    logger.error(f"Rate limit exceeded after {max_retries} attempts for {brand} {model_name}.")
                    break

            elif err_type == "TRANSIENT":
                if attempt < max_retries:
                    backoff_sec = 2 ** attempt
                    logger.warning(f"Transient API error ({e}) on attempt {attempt}/{max_retries}. Retrying in {backoff_sec}s...")
                    time.sleep(backoff_sec)
                else:
                    logger.error(f"Transient error persisted after {max_retries} attempts for {brand} {model_name}: {e}")
                    break

            else:
                logger.error(f"Error calling Gemini API for {brand} {model_name} on attempt {attempt}: {e}")
                break

    logger.error(f"Gemini copy drafting failed after {max_retries} attempts for {brand} {model_name}: {last_error}")
    return None


def audit_product_semantics(
    product_payload: Dict[str, Any],
    source_text: Optional[str] = None,
    model: str = "gemini-3.6-flash",
    max_retries: int = 3
) -> Tuple[List[str], bool]:
    """
    Performs LLM Semantic Audit on resolved product data with exponential backoff retries.
    FAILS FAST on daily quota exhaustion.
    """
    client = get_gemini_client()

    system_prompt = (
        "You are an AI Quality Assurance Inspector for a corporate gifting catalogue. "
        "Your task is to audit the resolved product data for factual consistency and quality."
    )

    product_summary_str = json.dumps(product_payload, indent=2)
    user_prompt = f"Product Data Under Review:\n{product_summary_str}\n\n"
    if source_text:
        user_prompt += f"Original Scraped Source Text:\n{source_text[:1500]}\n\n"
    user_prompt += "Perform the semantic audit and return the results according to SemanticAuditSchema."

    logger.info(f"Running LLM semantic audit for {product_payload.get('brand')} {product_payload.get('model_name')}...")
    
    for attempt in range(1, max_retries + 1):
        try:
            response = client.models.generate_content(
                model=model,
                contents=[system_prompt, user_prompt],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=SemanticAuditSchema,
                    temperature=0.1
                )
            )

            audit_result = json.loads(response.text)
            flags = audit_result.get("summary_flags", [])
            
            for item in audit_result.get("contradictions", []):
                if item not in flags:
                    flags.append(f"Semantic contradiction: {item}")
            for item in audit_result.get("factual_discrepancies", []):
                if item not in flags:
                    flags.append(f"Factual discrepancy: {item}")

            is_clean = audit_result.get("is_valid", False) and len(flags) == 0
            return flags, is_clean

        except Exception as e:
            err_type = classify_gemini_error(e)

            if err_type == "QUOTA_EXHAUSTED":
                msg = "Gemini daily quota exhausted. Run halted. No rows were modified."
                logger.error(msg)
                raise GeminiDailyQuotaExhaustedError(msg)

            elif err_type in ("RATE_LIMIT", "TRANSIENT") and attempt < max_retries:
                backoff_sec = 2 ** attempt
                logger.warning(f"Transient audit error ({str(e)[:120]}) on attempt {attempt}/{max_retries}. Retrying in {backoff_sec}s...")
                time.sleep(backoff_sec)
            else:
                logger.warning(f"Error during LLM semantic audit (skipping semantic check): {e}")
                return [], False

    return [], False
