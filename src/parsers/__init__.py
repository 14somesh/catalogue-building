from src.parsers.base import BaseParser, ParserResult
from src.parsers.shopify import ShopifyParser
from src.parsers.croma import CromaParser
from src.parsers.reliance import RelianceParser
from src.parsers.tatacliq import TataCliqParser
from src.parsers.amazon import AmazonParser

PARSER_REGISTRY = {
    "shopify": ShopifyParser(),
    "croma": CromaParser(),
    "reliance": RelianceParser(),
    "tatacliq": TataCliqParser(),
    "amazon": AmazonParser(),
}

def get_parser_for_url(url: str) -> BaseParser:
    """Dispatches the appropriate parser instance based on URL domain."""
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
        # Default brand sites (Shopify or standard semantic DOM)
        return PARSER_REGISTRY["shopify"]
