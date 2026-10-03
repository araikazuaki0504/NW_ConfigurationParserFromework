"""Parser engine coordinating registry selection and parser execution."""

from __future__ import annotations

from nwconfig_parser.models import ParseResult
from nwconfig_parser.parsers.base import ParseContext
from nwconfig_parser.parsers.registry import ParserRegistry


class ParserEngine:
    def __init__(self, registry: ParserRegistry) -> None:
        self._registry = registry

    @property
    def registry(self) -> ParserRegistry:
        return self._registry

    def parse(self, text: str, context: ParseContext) -> ParseResult:
        parser = self._registry.select(context)
        result = parser.parse(text, context)
        result.parser_metadata.setdefault("vendor", parser.vendor)
        result.parser_metadata.setdefault("os_family", parser.os_family)
        result.parser_metadata.setdefault("command", parser.command)
        result.parser_metadata.setdefault("parser_version", parser.parser_version)
        result.source_metadata.update(context.source_metadata)
        if context.filename:
            result.source_metadata.setdefault("filename", context.filename)
        return result

    def validate(self, text: str, context: ParseContext) -> ParseResult:
        """Run the selected parser and return its actual completeness status."""
        return self.parse(text, context)
