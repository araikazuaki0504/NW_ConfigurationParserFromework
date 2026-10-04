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

    def matches_command(self, command: str) -> bool:
        """Return whether this parser handles the (whitespace-normalised) command.

        The default is exact, case-insensitive equality with ``command``.
        Parsers whose command carries an argument (for example a VRF name)
        override this; ``command`` stays the unique registration key.
        """
        return " ".join(self.command.casefold().split()) == " ".join(
            command.casefold().split()
        )

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
