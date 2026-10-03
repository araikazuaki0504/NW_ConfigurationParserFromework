"""Small synthetic parser used to exercise the Phase 1 execution pipeline."""

from __future__ import annotations

from dataclasses import dataclass

from nwconfig_parser.models import (
    ImplementationStatus,
    IssueSeverity,
    ParseIssue,
    ParseResult,
    ParseStatus,
    SourceReference,
)
from nwconfig_parser.parsers.base import ParseContext, Parser


@dataclass(frozen=True, slots=True)
class ParsedTextLine:
    line_number: int
    text: str
    source_reference: SourceReference


class ExampleTextParser(Parser):
    vendor = "Example"
    os_family = "Synthetic"
    supported_versions: tuple[str, ...] = ()
    command = "parse text"
    parser_version = "1.0"
    implementation_status = ImplementationStatus.TESTED_WITH_SYNTHETIC_DATA

    def parse(self, text: str, context: ParseContext) -> ParseResult:
        if not text:
            issue = ParseIssue(
                severity=IssueSeverity.ERROR,
                code="EMPTY_INPUT",
                message="Input contains no text.",
            )
            return ParseResult(
                status=ParseStatus.FAILED,
                data=[],
                issues=[issue],
                parser_metadata={
                    "vendor": self.vendor,
                    "os_family": self.os_family,
                    "command": self.command,
                    "parser_version": self.parser_version,
                },
                source_metadata=dict(context.source_metadata),
            )

        parsed = [
            ParsedTextLine(
                line_number=line_number,
                text=line,
                source_reference=SourceReference(
                    filename=context.filename,
                    command=context.command or self.command,
                    start_line=line_number,
                    end_line=line_number,
                ),
            )
            for line_number, line in enumerate(text.splitlines(), start=1)
        ]
        return ParseResult(
            status=ParseStatus.SUCCESS,
            data=parsed,
            parser_metadata={
                "vendor": self.vendor,
                "os_family": self.os_family,
                "command": self.command,
                "parser_version": self.parser_version,
            },
            source_metadata=dict(context.source_metadata),
        )
