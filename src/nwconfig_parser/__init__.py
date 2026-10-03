"""Extensible network device configuration parser framework."""

from nwconfig_parser.core.engine import ParserEngine
from nwconfig_parser.models import ParseResult, ParseStatus, PrefixList, PrefixListEntry

__all__ = [
    "ParseResult",
    "ParseStatus",
    "ParserEngine",
    "PrefixList",
    "PrefixListEntry",
]

__version__ = "0.1.0"
