import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Any
from abc import ABC, abstractmethod


# Shared by every HTML parser so the "what counts as noise" list can't drift between them (a brand's
# site can be dispatched to GenericParser or ShopifyParser depending on detected platform, and both must
# treat the same things as noise).
NOISE_TAGS = ["script", "style", "nav", "footer", "header", "aside", "noscript", "svg"]

# Class-name substrings (case-insensitive) that mark navigational chrome, legal/marketing boilerplate,
# and same-page "other products" widgets (recommendations, upsells, recently-viewed, reviews). These
# must be stripped before any full-page text fallback (see fallback_page_text below), or a "You may
# also like" carousel full of OTHER products' spec text would silently contaminate this product's specs.
NOISE_CLASS_PATTERN = re.compile(
    r"site-footer|footer|site-header|header|announcement-bar|cookie|newsletter|"
    r"related-products|related|product-recommendations|recommend|you-may-like|you-may-also-like|"
    r"also-viewed|recently-viewed|trending-products|similar-products|upsell|cross-?sell|"
    r"also-bought|frequently-bought|bought-together|more-from|pairs-well|complete-the-look|bundle-offer|"
    r"product-carousel|product-card|testimonial|\breview\b",
    re.I
)

# How many ancestor levels fallback_page_text will climb from an anchor element (e.g. the product
# title) before giving up and scoping to <main>/<body> instead. Bounded so a flat/malformed tree can't
# make this climb indefinitely.
_ANCHOR_CLIMB_LIMIT = 12


def strip_noise_elements(soup) -> None:
    """Removes NOISE_TAGS and any element whose class matches NOISE_CLASS_PATTERN, in place."""
    for tag in soup(NOISE_TAGS):
        tag.decompose()
    for el in soup.find_all(class_=NOISE_CLASS_PATTERN):
        el.decompose()


def fallback_page_text(soup, targeted_text_len: int, threshold: int = 200, anchor=None) -> Optional[str]:
    """
    When a parser's targeted spec/description selectors (written for WooCommerce/Shopify-theme-style
    class names like "product-attributes" or "tech-spec") find little or nothing, this returns a
    markup-agnostic fallback -- it works regardless of a site's CSS naming convention (Tailwind utility
    classes, CSS-module hashed names, a fully custom build), which the targeted, class-name-based
    selectors cannot handle.

    Callers MUST call strip_noise_elements(soup) first -- this function does not re-strip, so noise
    left in the tree (nav/footer/recommendation widgets/etc.) would leak into the returned text.

    `anchor` (recommended): a tag known to sit inside the real product's own content block -- pass the
    product's <h1> when you have it. The fallback then climbs from the anchor through its ancestors and
    returns the text of the SMALLEST ancestor that already meets `threshold`, rather than the whole
    page. A "you may also like" section or other later-in-document noise is almost always a *sibling* of
    the product's content block, not nested inside it, so this keeps such noise out even when its class
    name isn't one strip_noise_elements recognizes (or it has no class at all) -- a blocklist of class
    names can never be exhaustive, but DOM position reliably separates "this product" from "other
    products" on real pages. Falls back to <main>/<body>/the whole soup when no anchor is given, the
    anchor has no suitable ancestor within _ANCHOR_CLIMB_LIMIT levels, or climbing never reaches
    `threshold` short of the page root.

    Returns None when the targeted text already met the threshold, or the fallback found nothing
    bigger than what the targeted selectors already had.
    """
    if targeted_text_len >= threshold:
        return None

    if anchor is not None:
        node = anchor
        best_text = ""
        for _ in range(_ANCHOR_CLIMB_LIMIT):
            node = getattr(node, "parent", None)
            if node is None or getattr(node, "name", None) in (None, "[document]"):
                break
            if len(node.find_all(["h1", "h2", "h3", "h4"])) > 1:
                # Climbed into a section holding more than this one product (a list/grid, a "you may
                # also like" or "frequently bought together" widget, ...) -- stop trusting anything from
                # here up, whatever its text length, rather than risk pulling in another product's
                # specs. Use whatever smaller-but-safe text was found below this point.
                break
            text = node.get_text(separator=" ", strip=True)
            if len(text) > len(best_text):
                best_text = text
            if len(text) >= threshold:
                break
        return best_text if len(best_text) > targeted_text_len else None

    # No anchor given: fall back to page-level scope. Less precise (no DOM-position guard against
    # sibling noise) -- only used when a caller has no product-title element to anchor on.
    scope = soup.find("main") or soup.find("body") or soup
    text = scope.get_text(separator=" ", strip=True)
    return text if len(text) > targeted_text_len else None


@dataclass
class ParserResult:
    """Standardized result returned by all source parsers."""
    success: bool
    status_code: int
    url: str
    is_blocked: bool = False
    is_delisted: bool = False
    title: Optional[str] = None
    description_text: Optional[str] = None
    specs: Dict[str, str] = field(default_factory=dict)
    mrp: Optional[float] = None
    image_urls: List[str] = field(default_factory=list)
    field_sources: Dict[str, str] = field(default_factory=dict)
    field_tiers: Dict[str, int] = field(default_factory=dict)
    tier: int = 1
    error: Optional[str] = None


class BaseParser(ABC):
    """Abstract base class for all source-specific parsers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name identifier for the parser (e.g. 'shopify', 'croma', 'amazon')."""
        pass

    @property
    @abstractmethod
    def capabilities(self) -> Set[str]:
        """Declared capabilities: {'specs', 'images'} or subset."""
        pass

    @abstractmethod
    def parse(self, url: str, html: str, status_code: int = 200, tier: int = 1, category: Optional[str] = None) -> ParserResult:
        """Parses HTML into a standardized ParserResult."""
        pass


# Backward compatibility alias
BaseBrandParser = BaseParser
