"""
Category Specification Engine:
Defines specifications, regex extraction rules, and quality gate requirements
for each product category (Powerbanks, TWS / Audio, Smartwatches, Cables / Chargers, and Generic Fallback).
"""

import re
from typing import Dict, Any, List, Optional, Tuple


CATEGORY_SPEC_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "powerbank": {
        "label": "Powerbank",
        "spec_keys": ["capacity", "output", "ports", "weight", "warranty"],
        "primary_specs": ["capacity", "output", "ports", "weight"],
        "min_required_specs": 2,
        "spec_mappings": {
            "capacity": "Spec_Capacity",
            "output": "Spec_Output",
            "ports": "Spec_Ports",
            "weight": "Spec_Weight",
            "warranty": "Spec_Warranty"
        },
        "conflict_checks": ["capacity_model_mismatch"]
    },
    "tws": {
        "label": "TWS / Earbuds",
        "spec_keys": ["playtime", "drivers", "noise_cancellation", "bluetooth", "ports", "weight", "warranty"],
        "primary_specs": ["playtime", "drivers", "noise_cancellation", "bluetooth", "ports"],
        "min_required_specs": 1,  # 1 structured spec or >= 2 valid feature bullets
        "spec_mappings": {
            "playtime": "Spec_Capacity",      # Reuses capacity column for battery/playtime duration
            "drivers": "Spec_Output",         # Reuses output column for driver/acoustic spec
            "noise_cancellation": "Spec_Ports", # Reuses ports column for ANC/ENC/mic spec
            "bluetooth": "Spec_Weight",       # Reuses weight column for BT version / connectivity
            "warranty": "Spec_Warranty"
        },
        "conflict_checks": []
    },
    "audio": {
        "label": "Audio / Headphones / Speakers",
        "spec_keys": ["playtime", "drivers", "output_power", "bluetooth", "ports", "weight", "warranty"],
        "primary_specs": ["playtime", "drivers", "output_power", "bluetooth", "ports"],
        "min_required_specs": 1,
        "spec_mappings": {
            "playtime": "Spec_Capacity",
            "drivers": "Spec_Output",
            "bluetooth": "Spec_Ports",
            "output_power": "Spec_Weight",
            "warranty": "Spec_Warranty"
        },
        "conflict_checks": []
    },
    "smartwatch": {
        "label": "Smartwatch",
        "spec_keys": ["display", "battery", "calling", "water_resistance", "warranty"],
        "primary_specs": ["display", "battery", "calling", "water_resistance"],
        "min_required_specs": 1,
        "spec_mappings": {
            "display": "Spec_Capacity",
            "battery": "Spec_Output",
            "calling": "Spec_Ports",
            "water_resistance": "Spec_Weight",
            "warranty": "Spec_Warranty"
        },
        "conflict_checks": []
    },
    "generic": {
        "label": "Generic Electronics",
        "spec_keys": ["capacity", "output", "ports", "weight", "warranty"],
        "primary_specs": ["capacity", "output", "ports", "weight"],
        "min_required_specs": 1,
        "spec_mappings": {
            "capacity": "Spec_Capacity",
            "output": "Spec_Output",
            "ports": "Spec_Ports",
            "weight": "Spec_Weight",
            "warranty": "Spec_Warranty"
        },
        "conflict_checks": []
    }
}


def normalize_category_key(category: Optional[str]) -> str:
    """Normalizes category name to registered key."""
    if not category:
        return "powerbank"
    clean = str(category).strip().lower()
    if any(k in clean for k in ["tws", "earbud", "earphone", "airbud", "headphone"]):
        return "tws"
    if any(k in clean for k in ["speaker", "soundbar", "audio"]):
        return "audio"
    if any(k in clean for k in ["smartwatch", "watch", "band", "wearable"]):
        return "smartwatch"
    if any(k in clean for k in ["powerbank", "power bank", "battery pack"]):
        return "powerbank"
    return "generic"


def get_category_spec_definition(category: Optional[str]) -> Dict[str, Any]:
    """Returns the spec definition configuration for a category."""
    key = normalize_category_key(category)
    return CATEGORY_SPEC_DEFINITIONS.get(key, CATEGORY_SPEC_DEFINITIONS["generic"])


def extract_category_specs(text: str, category: Optional[str] = None) -> Dict[str, str]:
    """
    Extracts structured specifications from raw text based on category context.
    """
    if not text:
        return {}

    cat_key = normalize_category_key(category)
    specs: Dict[str, str] = {}

    # Common Warranty extraction (all categories)
    warr = re.search(r'\b(\d+)\s*(?:month|year)s?\s*(?:manufacturer\s*)?warranty\b', text, re.I)
    if warr:
        specs["warranty"] = warr.group(0).title()

    if cat_key in ("tws", "audio"):
        # 1. Playtime / Battery life
        pt = re.search(r'\b(up to\s*)?(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)\s*(?:of\s*)?(?:playtime|playback|battery\s*life|music|talk\s*time|play\s*time)?\b', text, re.I)
        if pt and int(float(pt.group(2))) > 1:
            hours = pt.group(2)
            specs["playtime"] = f"{hours} Hours Playtime"

        # 2. Driver size
        drv = re.search(r'\b(\d+(?:\.\d+)?)\s*mm\s*(?:dynamic\s*)?(?:bass\s*)?drivers?\b', text, re.I)
        if drv:
            specs["drivers"] = f"{drv.group(1)}mm Drivers"

        # 3. Noise cancellation / Mics
        if re.search(r'\b(?:quad|4)\s*mics?\s*(?:with\s*)?enc\b', text, re.I):
            specs["noise_cancellation"] = "Quad Mic ENC"
        elif re.search(r'\b(?:dual|2)\s*mics?\s*(?:with\s*)?enc\b', text, re.I):
            specs["noise_cancellation"] = "Dual Mic ENC"
        elif re.search(r'\bactive\s*noise\s*cancellation\b|\banc\b', text, re.I):
            specs["noise_cancellation"] = "Active Noise Cancellation"
        elif re.search(r'\benvironmental\s*noise\s*cancellation\b|\benc\b', text, re.I):
            specs["noise_cancellation"] = "Environmental Noise Cancellation"

        # 4. Bluetooth version
        bt = re.search(r'\b(?:bluetooth|bt)\s*(?:v(?:ersion)?\.?\s*)?([45]\.\d)\b', text, re.I)
        if bt:
            specs["bluetooth"] = f"Bluetooth v{bt.group(1)}"
        elif re.search(r'\bbluetooth\s*(?:5|v5)\b', text, re.I):
            specs["bluetooth"] = "Bluetooth v5.3"

        # 5. Charging Port
        if re.search(r'type[-\s]?c|usb[-\s]?c', text, re.I):
            specs["ports"] = "Type-C Charging"

        # 6. Weight
        wt = re.search(r'\b(\d{1,4}(?:\.\d+)?)\s*(?:g|grams|gm)\b', text, re.I)
        if wt and float(wt.group(1)) < 250:
            specs["weight"] = f"{wt.group(1)}g"

    elif cat_key == "smartwatch":
        # 1. Display
        disp = re.search(r'\b(\d+(?:\.\d+)?)\s*(?:inch|")\s*(?:hd|amoled|tft|lcd)?\s*display\b', text, re.I)
        if disp:
            specs["display"] = f'{disp.group(1)}" Display'
        elif re.search(r'\bamoled\s*display\b', text, re.I):
            specs["display"] = "AMOLED Display"

        # 2. Battery Life
        sb = re.search(r'\b(\d+)\s*days?\s*battery\b', text, re.I)
        if sb:
            specs["battery"] = f"{sb.group(1)} Days Battery"

        # 3. Calling
        if re.search(r'\bbluetooth\s*calling\b|\bbt\s*calling\b', text, re.I):
            specs["calling"] = "Bluetooth Calling"

        # 4. Water resistance
        wr = re.search(r'\b(ip67|ip68|5atm|3atm)\b', text, re.I)
        if wr:
            specs["water_resistance"] = wr.group(1).upper()

    else:
        # Default Powerbank / Generic extraction
        # 1. Capacity
        cap = re.search(r'\b(5000|10000|10,000|15000|20000|20,000|25000|27000|30000)\s*(?:mAh|mah)\b', text, re.I)
        if cap:
            specs["capacity"] = f"{cap.group(1).replace(',', '')}mAh"

        # 2. Output / Wattage
        watt = re.search(r'\b(\d+(?:\.\d+)?\s*W(?:att)?)\b', text, re.I)
        if watt:
            specs["output"] = f"{watt.group(1)} Fast Charging"
        else:
            va = re.search(r'(\d+(?:\.\d+)?\s*V)\s*[/xX,\s]\s*(\d+(?:\.\d+)?\s*A)', text, re.I)
            if va:
                try:
                    volts = float(re.search(r'\d+(?:\.\d+)?', va.group(1)).group(0))
                    amps = float(re.search(r'\d+(?:\.\d+)?', va.group(2)).group(0))
                    calc_w = int(round(volts * amps))
                    specs["output"] = f"{calc_w}W Output"
                except Exception:
                    pass

        # 3. Ports
        ports = []
        if re.search(r'type[-\s]?c|usb[-\s]?c', text, re.I):
            ports.append("Type-C")
        if re.search(r'usb[-\s]?a|qc\s*3\.0|\busb\s*output\b', text, re.I):
            ports.append("USB-A")
        if re.search(r'micro\s*usb|\bmicro\b', text, re.I):
            ports.append("Micro-USB")
        if re.search(r'wireless|magsafe|qi2?', text, re.I):
            ports.append("Magnetic Wireless")
        if re.search(r'lightning', text, re.I):
            ports.append("Lightning")
        if ports:
            specs["ports"] = ", ".join(ports)

        # 4. Weight
        wt = re.search(r'\b(\d{2,4}(?:\.\d+)?)\s*(?:g|grams|gm)\b', text, re.I)
        if wt:
            specs["weight"] = f"{wt.group(1)}g"

    return specs
