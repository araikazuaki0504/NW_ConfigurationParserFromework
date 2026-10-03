"""Parser contract and invocation context."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from nwconfig_parser.models import ImplementationStatus, ParseResult


@dataclass(frozen=True, slots=True)
class ParseContext:
    vendor: str | None = None
    os_family: str | None = None
    os_version: str | None = None
    command: str | None = None
    filename: str | None = None
    source_metadata: dict[str, str] = field(default_factory=dict)


class Parser(ABC):
    vendor: str
    os_family: str
    supported_versions: tuple[str, ...] = ()
    command: str
    parser_version: str = "1"
    implementation_status: ImplementationStatus = ImplementationStatus.IMPLEMENTED

    @abstractmethod
    def parse(self, text: str, context: ParseContext) -> ParseResult:
        """Parse input and preserve partial successes and source references."""


class ParserSelectionError(LookupError):
    """Base exception for parser selection failures."""


class UnsupportedParserError(ParserSelectionError):
    """No parser is registered for the supplied metadata."""


class AmbiguousParserError(ParserSelectionError):
    """More than one parser matches; selection must not guess."""


class DuplicateParserError(ValueError):
    """A parser registration overlaps an existing registration."""
