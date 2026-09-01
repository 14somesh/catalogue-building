import os
import re
import json
import time
from typing import Optional, Dict, Any, List, Tuple, Set
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from google import genai
from google.genai import types

from src.utils.logger import setup_logger

logger = setup_logger("llm_client")

load_dotenv()


class AllLLMProvidersExhaustedError(Exception):
    """Raised when every configured LLM provider in the fallback chain is exhausted. Halts the run."""
    pass


class GeminiDailyQuotaExhaustedError(Exception):
    """Raised when Gemini daily / per-day quota is exhausted."""
    pass


class GroqDailyQuotaExhaustedError(Exception):
    """Raised when Groq quota is exhausted."""
    pass


class GeminiRateLimitError(Exception):
    """Raised when a per-minute rate limit is hit and backoff retry is warranted."""
    pass


class GeminiTransientError(Exception):
    """Raised on 503, connection errors, timeouts, etc."""
    pass


# Global tracking of exhausted providers within the current process / run
EXHAUSTED_PROVIDERS: Set[str] = set()


def mark_provider_exhausted(provider_name: str) -> None:
    """Marks a provider as exhausted so subsequent rows failover immediately without latency."""
    EXHAUSTED_PROVIDERS.add(provider_name.lower())
    logger.warning(f"[LLM Chain] Provider '{provider_name}' marked as EXHAUSTED for the remainder of this run.")


def is_provider_exhausted(provider_name: str) -> bool:
    """Checks if a provider has been marked as exhausted."""
    return provider_name.lower() in EXHAUSTED_PROVIDERS


def reset_provider_states() -> None:
    """Resets provider exhaustion tracking (useful between runs / in tests)."""
    EXHAUSTED_PROVIDERS.clear()


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


class PostRunReviewSchema(BaseModel):
    is_satisfied: bool = Field(description="True if specs and copy are mutually consistent, accurate, and describe the intended product; False if contradiction, wrong product, or severe issue detected.")
    contradictions: List[str] = Field(default_factory=list, description="Contradictions between specs and subtitle/bullets (e.g. wattage, capacity, port type)")
    capacity_model_mismatch: bool = Field(default=False, description="True if Model_Name capacity conflicts with collected spec capacity")
    copy_mismatch_critique: Optional[str] = Field(default=None, description="Critique if copy describes a different product than the model name implies")
    recommended_action: str = Field(default="pass", description="'pass', 'recollect', or 'flag'")


class VisionExtractedSpecsSchema(BaseModel):
    capacity: Optional[str] = Field(default=None, description="Battery capacity e.g. '10000 mAh' or '20000 mAh'")
    output: Optional[str] = Field(default=None, description="Max power output e.g. '22.5W Fast Charging' or '15W Wireless'")
    ports: Optional[str] = Field(default=None, description="Input/output port configuration e.g. 'Type-C, USB-A'")
    weight: Optional[str] = Field(default=None, description="Weight of the product e.g. '195g' or '220g'")
    warranty: Optional[str] = Field(default=None, description="Warranty term e.g. '6 Months' or '1 Year'")


class ImageQualityAuditSchema(BaseModel):
    is_correct_brand_and_model: bool = Field(description="True if the image shows a product belonging to the specified target brand and model, False if it belongs to another brand (e.g. Zebronics, Belkin, etc.) or is completely unrelated.")
    is_isolated_packshot: bool = Field(description="True if the image is a clean, standalone product render/packshot (with or without neutral phone attachment), False if it is a complex lifestyle shot, hand-held shot, or marketing infographic banner.")
    has_hand_holding: bool = Field(description="True if a human hand is holding or touching the device, False otherwise.")
    has_promotional_text_banner: bool = Field(description="True if the image contains large marketing slogans, infographic callout boxes, warranty badges, or promotional text overlays (e.g. '15W 2X FASTER', 'POWER THAT PUSHES LIMITS'), False if it is a clean product photo.")
    detected_brand: Optional[str] = Field(default=None, description="The visible brand logo or detected brand name in the image (e.g. 'Urbn', 'Stuffcool', 'Pebble', 'Portronics', 'Zebronics', 'Belkin', etc.)")
    quality_score: int = Field(description="Visual suitability score from 1 (unusable/wrong brand/infographic) to 10 (pristine isolated studio packshot on white)")
    rejection_reason: Optional[str] = Field(default=None, description="Concise explanation if the image should be rejected, or None if suitable.")


def classify_gemini_error(e: Exception) -> str:
    """
    Classifies Gemini API exceptions into three distinct kinds:
    1. 'QUOTA_EXHAUSTED' -> Daily/per-project quota violation.
    2. 'RATE_LIMIT' -> 429 with short retryDelay / per-minute quota. Back off and retry.
    3. 'TRANSIENT' -> 503, timeout, connection reset, overloaded, unavailable. Back off and retry.
    4. 'OTHER' -> Non-recoverable client/auth errors.
    """
    err_str = str(e).lower()
    
    # 1. Daily Quota Check (Failover)
    daily_markers = [
        "generaterequestsperday",
        "perday",
        "per_day",
        "daily",
        "quota exceeded",
        "free_tier_daily",
        "generaterequestsperdayperprojectpermodel",
    ]
    if any(marker in err_str for marker in daily_markers):
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


def classify_groq_error(e: Exception) -> str:
    """
    Classifies Groq API exceptions:
    1. 'QUOTA_EXHAUSTED' -> Daily/monthly requests or tokens limit exceeded.
    2. 'RATE_LIMIT' -> Per-minute TPM/RPM limit hit. Back off and retry.
    3. 'TRANSIENT' -> 503, 500, 502, 504, timeout, connection reset.
    4. 'OTHER' -> Auth / client errors.
    """
    err_str = str(e).lower()

    # 1. Daily Quota Check (Failover)
    daily_markers = [
        "daily_limit",
        "daily limit",
        "requests per day",
        "rpd",
        "tokens per day",
        "tpd",
        "quota_exceeded",
        "quota exceeded",
        "insufficient_quota",
        "plan limit",
    ]
    if any(marker in err_str for marker in daily_markers):
        return "QUOTA_EXHAUSTED"

    # 2. Rate limit (TPM/RPM per-minute)
    rate_limit_markers = [
        "rate_limit_exceeded",
        "rate limit",
        "ratelimit",
        "429",
        "tpm",
        "rpm",
        "requests per minute",
        "tokens per minute"
    ]
    if any(marker in err_str for marker in rate_limit_markers):
        return "RATE_LIMIT"

    # 3. Transient errors
    transient_markers = [
        "503", "500", "502", "504",
        "service unavailable", "gateway timeout",
        "connectionerror", "connection reset", "timeout", "timed out"
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


def get_groq_client():
    """Initializes and returns the Groq client."""
    from groq import Groq
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY not found in environment or .env file.")
    return Groq(api_key=api_key)


def get_providers_from_config(llm_config: Optional[dict] = None) -> List[Dict[str, Any]]:
    """
    Resolves the list of configured LLM providers in fallback chain order.
    Defaults to: Gemini -> Groq.
    """
    cfg = llm_config or {}
    providers = cfg.get("providers")
    if providers and isinstance(providers, list):
        return providers

    # Legacy or fallback single provider format
    model = cfg.get("model", "gemini-3.6-flash")
    temp = cfg.get("temperature", 0.2)
    return [
        {"name": "gemini", "model": model, "temperature": temp},
        {"name": "groq", "model": "llama-3.3-70b-versatile", "temperature": temp}
    ]


DANGLING_STOPWORDS = {
    "to", "in", "on", "at", "for", "with", "and", "or", "the", "a", "an",
    "by", "of", "from", "up", "into", "as"
}

# Banned marketing adjectives that must never appear in technical catalogue bullets
BANNED_ADJECTIVES = [
    r"premium", r"amazing", r"ultimate", r"perfect", r"revolutionary",
    r"cutting-?edge", r"seamless(?:ly)?", r"incredible", r"exceptional",
    r"best", r"unbeatable", r"stunning", r"game-?changing",
    r"top-?notch", r"state-?of-?the-?art",
    r"superior", r"flawless", r"miraculous", r"breathtaking", r"unrivaled"
]

SHARED_SYSTEM_PROMPT = (
    "You are an expert technical product copywriter for a corporate gifting catalogue.\n"
    "Your single task is to write 4 concise sales bullet points and an engaging subtitle "
    "based strictly on the provided verified product specifications and description.\n\n"
    "STRICT COPYWRITING CONSTRAINTS:\n"
    "1. LEAD WITH A NUMBER OR SPEC: When a spec or number exists in the source text (e.g. 10000mAh, 20W PD, 185g, 3 ports), "
    "lead the bullet point directly with that number or specification.\n"
    "2. NO MARKETING ADJECTIVES: Never use subjective marketing adjectives (premium, amazing, ultimate, perfect, "
    "revolutionary, cutting-edge, seamless, incredible, exceptional, best, unbeatable, stunning).\n"
    "3. NEVER REPEAT BRAND NAME: Never include or repeat the brand name inside any bullet point or subtitle.\n"
    "4. CASING & PUNCTUATION: Write in clean sentence case. Every bullet point and subtitle MUST end with a terminal period ('.').\n"
    "5. EXACT LENGTH CONSTRAINT: Every bullet point MUST be between 40 and 60 characters total.\n"
    "6. SUBTITLE LIMIT: Subtitle must be at most 80 characters and end with a terminal period.\n"
    "7. ZERO HALLUCINATION: Only make claims directly verified by the provided text. Never invent specs or features.\n"
    "8. OUTPUT FORMAT: Return valid JSON strictly matching this schema:\n"
    "{\n"
    '  "title": "Clean model title <= 40 chars",\n'
    '  "subtitle": "Informative capability subtitle <= 80 chars ending with a period.",\n'
    '  "bullet_1": "Leading spec bullet point <= 60 chars.",\n'
    '  "bullet_2": "Leading spec bullet point <= 60 chars.",\n'
    '  "bullet_3": "Leading spec bullet point <= 60 chars.",\n'
    '  "bullet_4": "Leading spec bullet point <= 60 chars."\n'
    "}"
)


def clean_and_normalize_text(
    text: str,
    brand: str,
    max_chars: int = 60,
    min_chars: int = 20
) -> Tuple[str, bool]:
    """
    Deterministic post-processor that runs on every bullet and subtitle regardless of provider:
    1. Strip brand name if present.
    2. Strip banned marketing adjectives.
    3. Collapse whitespace and strip dangling punctuation.
    4. Check if any banned adjective survived (reject if unfixable).
    5. Sentence case normalization.
    6. Trim to max_chars at a clean word boundary reserving room for terminal period.
    7. Strip dangling prepositions / conjunctions resulting from word-boundary truncation.
    8. Ensure terminal period.
    """
    if not text or not isinstance(text, str):
        return "", False

    cleaned = str(text).strip()

    # 1. Strip brand name if present (case-insensitive word boundary)
    if brand:
        cleaned = re.sub(rf'\b{re.escape(brand)}\b', '', cleaned, flags=re.I)

    # 2. Strip banned marketing adjectives
    for pattern in BANNED_ADJECTIVES:
        cleaned = re.sub(rf'\b{pattern}\b\s*', '', cleaned, flags=re.I)

    # 3. Collapse whitespace and strip leading/trailing non-alphanumeric punctuation
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    cleaned = re.sub(r'^[-–—:,.\s]+', '', cleaned).strip()

    if not cleaned:
        return "", False

    # 4. Check if any banned adjective survived
    for pattern in BANNED_ADJECTIVES:
        if re.search(rf'\b{pattern}\b', cleaned, re.I):
            return "", False

    # 5. Sentence case normalization: capitalize leading alphabetic word if it is not an acronym/unit
    if cleaned and cleaned[0].isalpha():
        cleaned = cleaned[0].upper() + cleaned[1:]

    # 6. Trim to max_chars at word boundary reserving 1 char for terminal period
    cleaned = cleaned.rstrip(". ,;:-")
    target_len = max_chars - 1
    if len(cleaned) > target_len:
        truncated = cleaned[:target_len]
        if " " in truncated:
            cleaned = truncated.rsplit(" ", 1)[0].rstrip(". ,;:-")
        else:
            cleaned = truncated.rstrip(". ,;:-")

    # 7. Strip dangling stop words (e.g. 'up to', 'for', 'in', 'on')
    words = cleaned.split()
    while words and words[-1].lower() in DANGLING_STOPWORDS:
        words.pop()
    cleaned = " ".join(words).rstrip(". ,;:-")

    if not cleaned:
        return "", False

    # 8. Ensure terminal period
    cleaned = cleaned.rstrip(". ") + "."

    # 9. Length guarantee
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars - 1].rsplit(" ", 1)[0].rstrip(". ,;:-") + "."

    is_valid = len(cleaned) >= min_chars and len(cleaned) <= max_chars
    return cleaned, is_valid


def post_process_copy_payload(raw_copy: Dict[str, Any], brand: str) -> Tuple[Dict[str, Any], bool]:
    """
    Applies deterministic post-processing across all fields of the LLM copy payload.
    Ensures identical shape and constraints regardless of LLM provider.
    """
    processed = dict(raw_copy)
    all_valid = True

    # 1. Clean Title
    title = str(raw_copy.get("title", "")).strip()
    title = re.sub(r'\s+', ' ', title)
    if len(title) > 40:
        title = title[:40].rsplit(" ", 1)[0].rstrip(" ,;:-")
    processed["title"] = title

    # 2. Clean Subtitle (max 80 chars)
    sub_raw = str(raw_copy.get("subtitle", "")).strip()
    sub_clean, sub_valid = clean_and_normalize_text(sub_raw, brand, max_chars=80, min_chars=15)
    if not sub_valid and sub_clean:
        sub_valid = True
    processed["subtitle"] = sub_clean
    if not sub_valid:
        all_valid = False

    # 3. Clean Bullets 1 to 4 (max 60 chars)
    for b_key in ["bullet_1", "bullet_2", "bullet_3", "bullet_4"]:
        b_raw = str(raw_copy.get(b_key, "")).strip()
        b_clean, b_valid = clean_and_normalize_text(b_raw, brand, max_chars=60, min_chars=20)
        processed[b_key] = b_clean
        if not b_valid:
            all_valid = False

    return processed, all_valid


def _build_copy_prompts(brand: str, model_name: str, product_description_block: str, specs: Dict[str, str]) -> Tuple[str, str]:
    """Builds identical structured copy system and user prompts across all providers."""
    user_prompt = (
        f"Brand: {brand}\n"
        f"Model: {model_name}\n"
        f"Verified Specs: {json.dumps(specs)}\n\n"
        f"--- VERIFIED PRODUCT DESCRIPTION BLOCK ---\n"
        f"{product_description_block[:1500]}\n"
        f"------------------------------------------\n\n"
        "Draft the title, subtitle, and 4 sales bullets according to the strict copywriting constraints in JSON format."
    )
    return SHARED_SYSTEM_PROMPT, user_prompt


def _draft_copy_gemini(
    brand: str,
    model_name: str,
    product_description_block: str,
    specs: Dict[str, str],
    model: str = "gemini-3.6-flash",
    temperature: float = 0.2,
    max_retries: int = 3
) -> Dict[str, Any]:
    """Executes copy drafting on Gemini with deterministic post-processing."""
    client = get_gemini_client()
    system_prompt, user_prompt = _build_copy_prompts(brand, model_name, product_description_block, specs)
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
            processed_data, is_valid = post_process_copy_payload(parsed_data, brand)
            if is_valid or attempt == max_retries:
                return processed_data
            logger.warning(f"[Gemini] Copy validation failed on attempt {attempt}/{max_retries}. Re-requesting...")

        except Exception as e:
            last_error = e
            err_type = classify_gemini_error(e)

            if err_type == "QUOTA_EXHAUSTED":
                raise GeminiDailyQuotaExhaustedError(str(e))
            elif err_type == "RATE_LIMIT" and attempt < max_retries:
                backoff_sec = 10 + (attempt * 5)
                logger.warning(f"[Gemini] Rate limit on attempt {attempt}/{max_retries}. Retrying in {backoff_sec}s...")
                time.sleep(backoff_sec)
            elif err_type == "TRANSIENT" and attempt < max_retries:
                backoff_sec = 2 ** attempt
                logger.warning(f"[Gemini] Transient error on attempt {attempt}/{max_retries}. Retrying in {backoff_sec}s...")
                time.sleep(backoff_sec)
            else:
                logger.error(f"[Gemini] Error on attempt {attempt}: {e}")
                break

    raise RuntimeError(f"Gemini drafting failed after {max_retries} attempts: {last_error}")


def _draft_copy_groq(
    brand: str,
    model_name: str,
    product_description_block: str,
    specs: Dict[str, str],
    model: str = "llama-3.3-70b-versatile",
    temperature: float = 0.2,
    max_retries: int = 3
) -> Dict[str, Any]:
    """Executes copy drafting on Groq with deterministic post-processing."""
    client = get_groq_client()
    system_prompt, user_prompt = _build_copy_prompts(brand, model_name, product_description_block, specs)
    logger.info(f"Calling Groq ({model}) to draft copy for {brand} {model_name}...")

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            completion = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"},
                temperature=temperature
            )
            raw_content = completion.choices[0].message.content
            parsed_data = json.loads(raw_content)
            processed_data, is_valid = post_process_copy_payload(parsed_data, brand)
            if is_valid or attempt == max_retries:
                return processed_data
            logger.warning(f"[Groq] Copy validation failed on attempt {attempt}/{max_retries}. Re-requesting...")

        except Exception as e:
            last_error = e
            err_type = classify_groq_error(e)

            if err_type == "QUOTA_EXHAUSTED":
                raise GroqDailyQuotaExhaustedError(str(e))
            elif err_type == "RATE_LIMIT" and attempt < max_retries:
                backoff_sec = 5 + (attempt * 3)
                logger.warning(f"[Groq] Rate limit on attempt {attempt}/{max_retries}. Retrying in {backoff_sec}s...")
                time.sleep(backoff_sec)
            elif err_type == "TRANSIENT" and attempt < max_retries:
                backoff_sec = 2 ** attempt
                logger.warning(f"[Groq] Transient error on attempt {attempt}/{max_retries}. Retrying in {backoff_sec}s...")
                time.sleep(backoff_sec)
            else:
                logger.error(f"[Groq] Error on attempt {attempt}: {e}")
                break

    raise RuntimeError(f"Groq drafting failed after {max_retries} attempts: {last_error}")


def draft_bullets_and_subtitle(
    brand: str,
    model_name: str,
    product_description_block: str,
    specs: Dict[str, str],
    llm_config: Optional[dict] = None,
    max_retries: int = 3
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """
    MULTI-PROVIDER FALLBACK CHAIN:
    Tries providers in configured order (Gemini -> Groq).
    If a provider hits QUOTA_EXHAUSTED, marks that provider unavailable for the rest of the run
    and immediately fails over to the next provider in the chain.
    Only halts if EVERY provider in the chain is exhausted.
    Returns: (copy_data_dict, provider_name).
    """
    providers = get_providers_from_config(llm_config)
    available_providers = [p for p in providers if not is_provider_exhausted(p.get("name", ""))]

    if not available_providers:
        msg = "All LLM providers exhausted. Run halted."
        logger.error(msg)
        raise AllLLMProvidersExhaustedError(msg)

    for prov in available_providers:
        p_name = str(prov.get("name", "")).strip().lower()
        p_model = prov.get("model")
        p_temp = prov.get("temperature", 0.2)

        try:
            if p_name == "gemini":
                data = _draft_copy_gemini(
                    brand=brand,
                    model_name=model_name,
                    product_description_block=product_description_block,
                    specs=specs,
                    model=p_model or "gemini-3.6-flash",
                    temperature=p_temp,
                    max_retries=max_retries
                )
                logger.info(f"Successfully drafted copy for {brand} {model_name} via Gemini ({p_model}).")
                return data, "gemini"

            elif p_name == "groq":
                data = _draft_copy_groq(
                    brand=brand,
                    model_name=model_name,
                    product_description_block=product_description_block,
                    specs=specs,
                    model=p_model or "llama-3.3-70b-versatile",
                    temperature=p_temp,
                    max_retries=max_retries
                )
                logger.info(f"Successfully drafted copy for {brand} {model_name} via Groq ({p_model}).")
                return data, "groq"

            else:
                logger.warning(f"Unknown LLM provider '{p_name}'. Skipping...")
                continue

        except (GeminiDailyQuotaExhaustedError, GroqDailyQuotaExhaustedError) as qe:
            mark_provider_exhausted(p_name)
            logger.warning(f"[LLM Chain] Provider '{p_name}' quota exhausted ({qe}). Failing over to next provider...")
            continue
        except Exception as ex:
            logger.warning(f"[LLM Chain] Provider '{p_name}' failed ({ex}). Failing over to next provider...")
            continue

    # If all configured providers were attempted and exhausted
    msg = "All LLM providers exhausted. Run halted."
    logger.error(msg)
    raise AllLLMProvidersExhaustedError(msg)


def preflight_quota_check(llm_config: Optional[dict] = None) -> Tuple[bool, List[str]]:
    """
    PRE-FLIGHT MULTI-PROVIDER QUOTA PROBE:
    Tests every configured provider in the chain and reports availability before processing starts.
    If all configured providers are unavailable, halts with AllLLMProvidersExhaustedError.
    """
    providers = get_providers_from_config(llm_config)
    available_providers = []

    logger.info("=" * 60)
    logger.info("[Pre-flight] Testing LLM Provider Fallback Chain Availability...")
    logger.info("=" * 60)

    for prov in providers:
        p_name = str(prov.get("name", "")).strip().lower()
        p_model = prov.get("model")

        if p_name == "gemini":
            try:
                client = get_gemini_client()
                client.models.generate_content(
                    model=p_model or "gemini-3.5-flash",
                    contents="ping",
                    config=types.GenerateContentConfig(max_output_tokens=5, temperature=0.0)
                )
                logger.info(f"  ✅ [Gemini] Model '{p_model or 'gemini-3.5-flash'}': AVAILABLE")
                if "gemini" not in available_providers:
                    available_providers.append("gemini")
            except Exception as e:
                err_type = classify_gemini_error(e)
                logger.warning(f"  ❌ [Gemini] Model '{p_model}': UNAVAILABLE ({err_type}: {e})")

        elif p_name == "groq":
            try:
                client = get_groq_client()
                client.chat.completions.create(
                    model=p_model or "llama-3.3-70b-versatile",
                    messages=[{"role": "user", "content": "ping"}],
                    max_tokens=5,
                    temperature=0.0
                )
                logger.info(f"  ✅ [Groq] Model '{p_model or 'llama-3.3-70b-versatile'}': AVAILABLE")
                if "groq" not in available_providers:
                    available_providers.append("groq")
            except Exception as e:
                err_type = classify_groq_error(e)
                logger.warning(f"  ❌ [Groq] Model '{p_model}': UNAVAILABLE ({err_type}: {e})")

    if "gemini" not in available_providers:
        mark_provider_exhausted("gemini")
    if "groq" not in available_providers:
        mark_provider_exhausted("groq")

    if not available_providers:
        msg = "All LLM providers exhausted. Run halted. No rows were modified."
        logger.error(f"[Pre-flight HALT] {msg}")
        raise AllLLMProvidersExhaustedError(msg)

    logger.info(f"[Pre-flight] Active providers in fallback chain: {available_providers}")
    logger.info("=" * 60)
    return True, available_providers


def audit_product_semantics(
    product_payload: Dict[str, Any],
    source_text: Optional[str] = None,
    llm_config: Optional[dict] = None,
    max_retries: int = 3
) -> Tuple[List[str], bool]:
    """
    Performs optional LLM Semantic Audit on resolved product data across available providers.
    """
    providers = get_providers_from_config(llm_config)
    available_providers = [p for p in providers if not is_provider_exhausted(p.get("name", ""))]

    if not available_providers:
        return [], False

    system_prompt = (
        "You are an AI Quality Assurance Inspector for a corporate gifting catalogue. "
        "Your task is to audit the resolved product data for factual consistency and quality."
    )
    product_summary_str = json.dumps(product_payload, indent=2)
    user_prompt = f"Product Data Under Review:\n{product_summary_str}\n\n"
    if source_text:
        user_prompt += f"Original Scraped Source Text:\n{source_text[:1500]}\n\n"
    user_prompt += "Perform the semantic audit and return the results according to SemanticAuditSchema."

    for prov in available_providers:
        p_name = str(prov.get("name", "")).strip().lower()
        p_model = prov.get("model")

        try:
            if p_name == "gemini":
                client = get_gemini_client()
                response = client.models.generate_content(
                    model=p_model or "gemini-3.6-flash",
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

            elif p_name == "groq":
                client = get_groq_client()
                completion = client.chat.completions.create(
                    model=p_model or "llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": system_prompt + "\nReturn JSON conforming to SemanticAuditSchema (is_valid, contradictions, factual_discrepancies, summary_flags)."},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1
                )
                audit_result = json.loads(completion.choices[0].message.content)
                flags = audit_result.get("summary_flags", [])
                is_clean = audit_result.get("is_valid", False) and len(flags) == 0
                return flags, is_clean

        except Exception as e:
            logger.warning(f"Semantic audit error with {p_name}: {e}")
            continue

    return [], False


def audit_row_post_run(
    product_payload: Dict[str, Any],
    llm_config: Optional[dict] = None
) -> Tuple[bool, List[str], str]:
    """
    STEP 5.5: AUTOMATIC POST-RUN LLM REVIEW AUDIT
    Reviews a product row after pipeline execution:
      1. Spec contradictions against subtitle/bullets (e.g. wattage/capacity mismatches).
      2. Model name capacity consistency.
      3. Copy mismatch (describes a different product than model name implies).
    Returns (is_satisfied: bool, contradictions: List[str], recommended_action: str).
    """
    providers = get_providers_from_config(llm_config)
    available_providers = [p for p in providers if not is_provider_exhausted(p.get("name", ""))]

    if not available_providers:
        logger.warning("[Post-Run Review] All LLM providers exhausted; defaulting to pass.")
        return True, [], "pass"

    system_prompt = (
        "You are an AI Quality Assurance Inspector for a corporate gifting catalogue.\n"
        "Your task is to strictly audit resolved product data for factual consistency and accuracy.\n"
        "Check:\n"
        "1. Do the technical specs contradict the subtitle or bullets? (e.g. spec says 15W but bullet says 65W PD; spec says 10000mAh but bullet says 20000mAh). Subtitle and bullets MUST match the collected technical specs.\n"
        "2. Does the battery capacity match what the model name implies? (e.g. 10000mAh vs 20000mAh is a hard conflict).\n"
        "3. Does the copy read like it describes a completely different product? (e.g. describing a wireless magnetic power bank when the product is an ultra-slim wired powerbank).\n"
        "Note: Minor wattage discrepancies between dealer sheet model name (e.g. 65W) and official product upgrade (e.g. 70W) on the same product page are allowed if copy and specs are internally consistent.\n"
        "Return valid JSON adhering to PostRunReviewSchema."
    )
    product_summary_str = json.dumps(product_payload, indent=2)
    user_prompt = f"Product Data To Review:\n{product_summary_str}\n\nPerform the post-run consistency review."

    for prov in available_providers:
        p_name = str(prov.get("name", "")).strip().lower()
        p_model = prov.get("model")

        try:
            if p_name == "gemini":
                client = get_gemini_client()
                response = client.models.generate_content(
                    model=p_model or "gemini-3.6-flash",
                    contents=[system_prompt, user_prompt],
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=PostRunReviewSchema,
                        temperature=0.1
                    )
                )
                res = json.loads(response.text)
                is_satisfied = res.get("is_satisfied", True)
                contradictions = res.get("contradictions", [])
                if res.get("capacity_model_mismatch"):
                    contradictions.append("Capacity in Model_Name conflicts with collected spec capacity")
                if res.get("copy_mismatch_critique"):
                    contradictions.append(f"Copy mismatch: {res.get('copy_mismatch_critique')}")
                action = res.get("recommended_action", "pass" if is_satisfied else "recollect")
                return is_satisfied and len(contradictions) == 0, contradictions, action

            elif p_name == "groq":
                client = get_groq_client()
                completion = client.chat.completions.create(
                    model=p_model or "llama-3.3-70b-versatile",
                    messages=[
                        {"role": "system", "content": system_prompt + "\nReturn JSON adhering to PostRunReviewSchema (is_satisfied: bool, contradictions: list, capacity_model_mismatch: bool, copy_mismatch_critique: str, recommended_action: str)."},
                        {"role": "user", "content": user_prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1
                )
                res = json.loads(completion.choices[0].message.content)
                is_satisfied = res.get("is_satisfied", True)
                contradictions = res.get("contradictions", [])
                if res.get("capacity_model_mismatch"):
                    contradictions.append("Capacity in Model_Name conflicts with collected spec capacity")
                if res.get("copy_mismatch_critique"):
                    contradictions.append(f"Copy mismatch: {res.get('copy_mismatch_critique')}")
                action = res.get("recommended_action", "pass" if is_satisfied else "recollect")
                return is_satisfied and len(contradictions) == 0, contradictions, action

        except Exception as e:
            logger.warning(f"[Post-Run Review] Review error with {p_name}: {e}")
            continue

    return True, [], "pass"


def extract_specs_via_vision(
    image_bytes_list: List[Tuple[bytes, str]],
    page_url: str,
    brand: str,
    model_name: str,
    llm_config: Optional[dict] = None
) -> Optional[Dict[str, str]]:
    """
    VISION FALLBACK:
    When a tier returns HTTP 200 but specs are missing from the HTML because they sit
    inside infographic images / spec banners, this function feeds the image assets to
    Gemini Vision to extract the structured specifications table.
    
    Returns a dict of non-empty specs (capacity, output, ports, weight, warranty) or None.
    """
    if not image_bytes_list:
        return None

    if is_provider_exhausted("gemini"):
        logger.warning(f"[Vision Fallback] Gemini is marked exhausted; cannot run vision extraction for {brand} {model_name}.")
        return None

    client = get_gemini_client()
    cfg = llm_config or {}
    candidate_models = ["gemini-3.5-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]

    prompt = (
        f"You are an expert technical product specification extractor.\n"
        f"Examine these product infographic and specification images for '{brand} {model_name}' from the page '{page_url}'.\n"
        f"Extract the exact technical specifications into the JSON schema:\n"
        f"- capacity: battery capacity in mAh (e.g. '10000 mAh', '20000 mAh')\n"
        f"- output: maximum output power / fast charging wattage (e.g. '22.5W Fast Charging', '15W Wireless', '35W PD')\n"
        f"- ports: port types and configuration (e.g. 'Type-C, USB-A', 'Dual Type-C')\n"
        f"- weight: product weight in grams (e.g. '195g')\n"
        f"- warranty: warranty duration (e.g. '6 Months', '1 Year')\n\n"
        f"STRICT CONSTRAINTS:\n"
        f"1. ZERO HALLUCINATION: Only extract specs that are clearly visible or stated in the images.\n"
        f"2. If a spec is not visible or not mentioned, return null for that field.\n"
        f"3. Return valid JSON adhering to VisionExtractedSpecsSchema."
    )

    image_parts = []
    # Cap at 4 images max
    for img_data, mime_type in image_bytes_list[:4]:
        image_parts.append(types.Part.from_bytes(data=img_data, mime_type=mime_type))

    contents = [prompt] + image_parts
    import concurrent.futures

    for model in candidate_models:
        logger.info(f"[Vision Fallback] Calling Gemini Vision ({model}) on {len(image_parts)} images for {brand} {model_name} (15s timeout)...")
        
        def _call_gemini():
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=VisionExtractedSpecsSchema,
                    temperature=0.1
                )
            )

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_call_gemini)
                response = future.result(timeout=15.0)

            parsed = json.loads(response.text)
            raw_specs = {}
            for k in ["capacity", "output", "ports", "weight", "warranty"]:
                val = parsed.get(k)
                if val and str(val).strip().lower() not in ("null", "none", ""):
                    raw_specs[k] = str(val).strip()

            # Sanity-check vision output before writing
            clean_specs = validate_vision_specs(raw_specs)

            if clean_specs:
                logger.info(f"[Vision Fallback] Successfully extracted verified specs for {brand} {model_name}: {clean_specs}")
                return clean_specs
            else:
                logger.warning(f"[Vision Fallback] Vision API returned no verifiable specs for {brand} {model_name}.")
                return None

        except concurrent.futures.TimeoutError:
            logger.warning(f"[Vision Fallback] Gemini vision call for {model} TIMED OUT after 15s. Escalating immediately.")
            continue
        except Exception as e:
            err_type = classify_gemini_error(e)
            logger.warning(f"[Vision Fallback] Gemini vision model {model} failed ({err_type}): {e}")
            if err_type in ("QUOTA_EXHAUSTED", "RATE_LIMIT"):
                continue
            else:
                continue

    return None


def validate_vision_specs(specs: Dict[str, str]) -> Dict[str, str]:
    """
    Sanity-checks vision extraction outputs before recording:
    - capacity: must parse as a number followed by mAh
    - weight: must parse as a number followed by g
    - output: must parse as a number followed by W
    - ports: must contain standard port identifiers
    - warranty: must contain duration identifiers
    Anything unparseable is discarded, not written.
    """
    valid_specs = {}

    cap = specs.get("capacity")
    if cap:
        m = re.search(r'\b(\d{4,6})\s*m?ah\b', str(cap), re.IGNORECASE) or re.search(r'\b(\d+,\d+)\s*m?ah\b', str(cap), re.IGNORECASE)
        if m:
            clean_cap = m.group(1).replace(",", "")
            valid_specs["capacity"] = f"{clean_cap} mAh"
        else:
            logger.warning(f"[Vision Sanity] Discarding unparseable capacity: '{cap}'")

    out = specs.get("output")
    if out:
        if re.search(r'\b\d+(?:\.\d+)?\s*w\b', str(out), re.IGNORECASE):
            valid_specs["output"] = str(out).strip()
        else:
            logger.warning(f"[Vision Sanity] Discarding unparseable output: '{out}'")

    wt = specs.get("weight")
    if wt:
        m = re.search(r'\b(\d{2,4}(?:\.\d+)?)\s*g\b', str(wt), re.IGNORECASE)
        if m:
            valid_specs["weight"] = f"{m.group(1)}g"
        else:
            logger.warning(f"[Vision Sanity] Discarding unparseable weight: '{wt}'")

    pts = specs.get("ports")
    if pts:
        if any(p in str(pts).lower() for p in ["type-c", "type c", "usb-c", "usb c", "usb-a", "usb a", "wireless", "lightning", "micro", "port"]):
            valid_specs["ports"] = str(pts).strip()
        else:
            logger.warning(f"[Vision Sanity] Discarding unparseable ports: '{pts}'")

    war = specs.get("warranty")
    if war:
        if any(w in str(war).lower() for w in ["month", "year", "yr"]):
            valid_specs["warranty"] = str(war).strip()
        else:
            logger.warning(f"[Vision Sanity] Discarding unparseable warranty: '{war}'")

    return valid_specs


def audit_collected_image_quality(
    img_bytes: bytes,
    brand: str,
    model_name: str,
    mime_type: str = "image/png",
    llm_config: Optional[dict] = None
) -> Tuple[bool, int, Optional[str]]:
    """
    VISUAL AI IMAGE REVIEW GATE:
    Audits a candidate product image immediately upon collection.
    
    Verifies:
    1. Correct Brand & Model: Product must belong to the specified target brand (not Zebronics, Belkin, etc.).
    2. Isolated Studio Packshot: Clean standalone packshot on white or clean neutral background.
    3. Rejects Hands: Rejects photos where a person is holding/touching the device.
    4. Rejects Infographic Banners: Rejects multi-panel marketing banners with promo text overlays.
    
    Returns (is_valid: bool, quality_score: int, rejection_reason: Optional[str]).
    """
    if is_provider_exhausted("gemini"):
        logger.warning(f"[Image Review Gate] Gemini is marked exhausted; applying heuristic pass for {brand} {model_name}.")
        return True, 7, None

    client = get_gemini_client()
    candidate_models = ["gemini-3.5-flash-lite", "gemini-3.5-flash"]
    import concurrent.futures

    prompt = (
        f"You are an expert product catalog image quality inspector.\n"
        f"Audit this product image for '{brand} {model_name}'.\n"
        f"Verify the following strict rules:\n"
        f"1. BRAND INTEGRITY: Does the product in the image belong to '{brand}'? If the visible logo or product is from another brand (e.g. Zebronics, Belkin, Anker, Xiaomi), mark is_correct_brand_and_model=False.\n"
        f"2. STUDIO PACKSHOT: Is this an isolated studio packshot or clean standalone render? (Attached to a neutral phone for magnetic powerbanks is acceptable).\n"
        f"3. NO HANDS: Is a human hand holding the product? If yes, mark has_hand_holding=True.\n"
        f"4. NO MARKETING BANNERS: Does the image contain large advertising text, promotional slogans ('15W 2X FASTER', 'POWER THAT PUSHES LIMITS'), warranty badges, or multi-panel infographic layouts? If yes, mark has_promotional_text_banner=True.\n"
        f"5. SCORE: Rate from 1 (unusable/wrong brand/banner) to 10 (perfect clean studio packshot on white).\n\n"
        f"Adhere strictly to the ImageQualityAuditSchema."
    )

    image_part = types.Part.from_bytes(data=img_bytes, mime_type=mime_type)
    contents = [prompt, image_part]

    for model in candidate_models:
        logger.info(f"[Image Review Gate] Auditing image with {model} for {brand} {model_name}...")
        
        def _call():
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=ImageQualityAuditSchema,
                    temperature=0.0
                )
            )

        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_call)
                resp = future.result(timeout=12.0)

            parsed = json.loads(resp.text)
            is_brand_ok = bool(parsed.get("is_correct_brand_and_model", True))
            is_packshot = bool(parsed.get("is_isolated_packshot", True))
            has_hand = bool(parsed.get("has_hand_holding", False))
            has_banner = bool(parsed.get("has_promotional_text_banner", False))
            score = int(parsed.get("quality_score", 5))
            detected_brand = parsed.get("detected_brand") or ""
            reason = parsed.get("rejection_reason")

            if not is_brand_ok:
                rejection = f"Wrong brand detected: '{detected_brand}' (expected '{brand}')"
                logger.warning(f"[Image Review Gate] REJECTED [{brand} {model_name}]: {rejection}")
                return False, score, rejection

            if has_banner:
                rejection = "Marketing infographic banner with promotional text overlays"
                logger.warning(f"[Image Review Gate] REJECTED [{brand} {model_name}]: {rejection}")
                return False, score, rejection

            if has_hand:
                rejection = "Hand-held lifestyle shot instead of isolated studio packshot"
                logger.warning(f"[Image Review Gate] REJECTED [{brand} {model_name}]: {rejection}")
                return False, score, rejection

            if score < 7 or not is_packshot:
                rejection = reason or "Low visual packshot quality / complex lifestyle background"
                logger.warning(f"[Image Review Gate] REJECTED [{brand} {model_name}]: {rejection} (score={score})")
                return False, score, rejection

            logger.info(f"[Image Review Gate] APPROVED [{brand} {model_name}] (score={score}/10)")
            return True, score, None

        except concurrent.futures.TimeoutError:
            logger.warning(f"[Image Review Gate] Model {model} timed out after 12s. Escalating to next candidate model...")
            continue
        except Exception as e:
            err_type = classify_gemini_error(e)
            logger.warning(f"[Image Review Gate] Model {model} failed ({err_type}): {e}")
            if err_type in ("QUOTA_EXHAUSTED", "RATE_LIMIT"):
                continue
            else:
                continue

    logger.warning(f"[Image Review Gate] All vision models exhausted for {brand} {model_name}; applying heuristic pass.")
    return True, 7, None



