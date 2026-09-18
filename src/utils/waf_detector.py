"""
WAF & Bot Protection Signature Detector
Deterministic detection of Cloudflare, Akamai, DataDome, AWS WAF, Kasada, and generic bot challenges.
No LLM dependencies — pure header, status code, cookie, and body signature analysis.
"""
from typing import Tuple, Optional, Dict, Any
import re


def detect_waf_block(
    status_code: int,
    headers: Optional[Dict[str, str]] = None,
    body: Optional[str] = None,
    url: Optional[str] = None
) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Evaluates HTTP response status code, headers, and body for bot protection / WAF block signatures.
    Returns:
      (is_blocked: bool, vendor: Optional[str], reason: Optional[str])
    """
    headers = headers or {}
    # Lowercase header keys and values for case-insensitive lookup
    norm_headers = {str(k).lower(): str(v).lower() for k, v in headers.items()}
    headers_str = " ".join(f"{k}:{v}" for k, v in norm_headers.items())
    body_lower = (body or "").lower()

    # 1. Cloudflare Detection
    # Headers: cf-ray, server: cloudflare, cf-chl-bypass
    # Body: id="cmsg", "please enable js and disable any ad blocker", "challenge-platform", "cf-wrapper"
    is_cf_header = "cf-ray" in norm_headers or norm_headers.get("server") == "cloudflare"
    is_cf_body = any(sig in body_lower for sig in [
        'id="cmsg"',
        "please enable js and disable any ad blocker",
        "challenge-platform",
        "cf-browser-verification",
        "cloudflare ray id",
        "attention required! | cloudflare",
        "just a moment..."
    ])
    if (is_cf_header and status_code in (403, 429, 503)) or is_cf_body:
        return True, "cloudflare", f"HTTP {status_code} Cloudflare Bot Challenge"

    # 2. Akamai Detection
    # Headers / Cookies: _abck, akamai-grn, x-akamai-transformed
    # Body: <title>access denied</title>, "you don't have permission to access", "reference #"
    is_akamai_header = any(k in norm_headers for k in ["x-akamai-transformed", "akamai-grn"]) or "_abck" in norm_headers.get("set-cookie", "")
    is_akamai_body = ("<title>access denied</title>" in body_lower and "you don't have permission to access" in body_lower) or "akamai-grn" in body_lower
    if (status_code in (403, 429) and (is_akamai_header or is_akamai_body)) or is_akamai_body:
        return True, "akamai", f"HTTP {status_code} Akamai Bot Protection / Access Denied"

    # 3. DataDome Detection
    # Headers: x-datadome, x-datadome-response, set-cookie with datadome=
    is_datadome = any(k.startswith("x-datadome") for k in norm_headers) or "datadome" in norm_headers.get("set-cookie", "") or "datadome" in body_lower
    if is_datadome and status_code in (403, 429, 200):
        return True, "datadome", f"HTTP {status_code} DataDome Anti-Bot Challenge"

    # 4. AWS WAF Detection
    # Headers: x-amzn-waf-action, x-amzn-errortype
    if "x-amzn-waf-action" in norm_headers or "x-amz-cf-id" in norm_headers and status_code in (403, 429):
        return True, "aws_waf", f"HTTP {status_code} AWS WAF Block"

    # 5. Kasada Detection
    # Headers: x-kpsdk-ct, x-kpsdk-cd
    # Status 429 with empty or minimal payload (< 100 bytes)
    is_kasada_header = any(k.startswith("x-kpsdk") for k in norm_headers)
    is_kasada_payload = (status_code == 429 and len(body or "") < 100)
    if is_kasada_header or is_kasada_payload:
        return True, "kasada", f"HTTP {status_code} Kasada Bot Mitigation"

    # 6. Generic HTTP 403 / 429 / 401 Block
    if status_code in (403, 429):
        return True, "generic_waf", f"HTTP {status_code} Forbidden / Rate Limit"

    if status_code == 503 and any(sig in body_lower for sig in ["captcha", "bot challenge", "security check", "ddos protection"]):
        return True, "generic_waf", "HTTP 503 Bot Protection Challenge"

    return False, None, None
