"""Parser registry and deterministic parser selection."""

from __future__ import annotations

from nwconfig_parser.parsers.base import (
    AmbiguousParserError,
    DuplicateParserError,
    ParseContext,
    Parser,
    UnsupportedParserError,
)


def _normalize(value: str) -> str:
    return " ".join(value.casefold().split())


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers: list[Parser] = []

    def register(self, parser: Parser) -> None:
        for existing in self._parsers:
            same_key = (
                _normalize(existing.vendor) == _normalize(parser.vendor)
                and _normalize(existing.os_family) == _normalize(parser.os_family)
                and _normalize(existing.command) == _normalize(parser.command)
            )
            versions_overlap = (
                not existing.supported_versions
                or not parser.supported_versions
                or bool(
                    set(existing.supported_versions) & set(parser.supported_versions)
                )
            )
            if same_key and versions_overlap:
                raise DuplicateParserError(
                    f"Parser registration overlaps: {existing.__class__.__name__} "
                    f"and {parser.__class__.__name__}"
                )
        self._parsers.append(parser)

    def select(self, context: ParseContext) -> Parser:
        if not context.vendor or not context.os_family or not context.command:
            raise UnsupportedParserError(
                "vendor, os_family, and command are required for parser selection"
            )
        matches = [
            parser
            for parser in self._parsers
            if _normalize(parser.vendor) == _normalize(context.vendor)
            and _normalize(parser.os_family) == _normalize(context.os_family)
            and _normalize(parser.command) == _normalize(context.command)
            and (
                not parser.supported_versions
                or (
                    context.os_version is not None
                    and context.os_version in parser.supported_versions
                )
            )
        ]
        if not matches:
            raise UnsupportedParserError(
                "No parser registered for the supplied vendor, OS, version, and command"
            )
        if len(matches) > 1:
            raise AmbiguousParserError(
                "Multiple parsers match the supplied vendor, OS, version, and command"
            )
        return matches[0]

    def parsers(self) -> tuple[Parser, ...]:
        return tuple(
            sorted(
                self._parsers,
                key=lambda parser: (
                    _normalize(parser.vendor),
                    _normalize(parser.os_family),
                    _normalize(parser.command),
                    parser.supported_versions,
                ),
            )
        )

    def describe(self, parser: Parser) -> dict[str, object]:
        return {
            "vendor": parser.vendor,
            "os_family": parser.os_family,
            "supported_versions": list(parser.supported_versions),
            "command": parser.command,
            "parser_version": parser.parser_version,
            "implementation_status": parser.implementation_status,
            "parser_class": (
                f"{parser.__class__.__module__}.{parser.__class__.__qualname__}"
            ),
        }

    def list_descriptions(
        self,
        *,
        vendor: str | None = None,
        os_family: str | None = None,
    ) -> list[dict[str, object]]:
        return [
            self.describe(parser)
            for parser in self.parsers()
            if (vendor is None or _normalize(parser.vendor) == _normalize(vendor))
            and (
                os_family is None
                or _normalize(parser.os_family) == _normalize(os_family)
            )
        ]
