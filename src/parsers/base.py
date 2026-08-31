from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Any
from abc import ABC, abstractmethod


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
    image_urls: List[str] = field(default_factory=list)
    field_sources: Dict[str, str] = field(default_factory=dict)
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
    def parse(self, url: str, html: str, status_code: int = 200, tier: int = 1) -> ParserResult:
        """Parses HTML into a standardized ParserResult."""
        pass
