from typing import Optional
from src.parsers.base import BaseParser, ParserResult
from src.parsers.shopify import ShopifyParser
from src.parsers.generic import GenericParser
from src.parsers.croma import CromaParser
from src.parsers.reliance import RelianceParser
from src.parsers.tatacliq import TataCliqParser
from src.parsers.amazon import AmazonParser

PARSER_REGISTRY = {
    "shopify": ShopifyParser(),
    "generic": GenericParser(),
    "croma": CromaParser(),
    "reliance": RelianceParser(),
    "tatacliq": TataCliqParser(),
    "amazon": AmazonParser(),
}


def get_parser_for_url(url: str, platform: Optional[str] = None) -> BaseParser:
    """
    Dispatches the appropriate parser instance based on URL domain and detected platform.
    If platform is WooCommerce, Magento, BigCommerce, or custom, routes to GenericParser.
    If unknown, falls back to GenericParser (HTML + JSON-LD) rather than assuming Shopify.
    """
    url_lower = url.lower()
    if "amazon.in" in url_lower or "amazon.com" in url_lower:
        return PARSER_REGISTRY["amazon"]
    elif "croma.com" in url_lower:
        return PARSER_REGISTRY["croma"]
    elif "reliancedigital.in" in url_lower:
        return PARSER_REGISTRY["reliance"]
    elif "tatacliq.com" in url_lower:
        return PARSER_REGISTRY["tatacliq"]
    else:
        # Brand site routing by detected platform
        if platform == "shopify":
            return PARSER_REGISTRY["shopify"]
        elif platform in ("woocommerce", "magento", "bigcommerce", "custom", "generic"):
            return PARSER_REGISTRY["generic"]
        elif platform is None:
            # Check fast url heuristic if platform was not explicitly passed
            return PARSER_REGISTRY["shopify"]
        return PARSER_REGISTRY["generic"]
