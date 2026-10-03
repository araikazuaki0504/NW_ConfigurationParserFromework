"""Shared scaffolding for line-oriented operational parsers (Phase 4 vendors).

Each vendor/command keeps its own parser class because output formats differ.
This module only provides issue collection, prompt/echo handling and the common
SUCCESS / PARTIAL_SUCCESS / FAILED decision so that status is reported the same
way everywhere.
"""

from __future__ import annotations

import re
from abc import abstractmethod

from nwconfig_parser.models import (
    ImplementationStatus,
    IssueSeverity,
    ParseIssue,
    ParseResult,
    ParseStatus,
    SourceReference,
)
from nwconfig_parser.parsers.base import ParseContext, Parser

_PROMPT = re.compile(r"^(?:<[^<>\s]+>|[^\s#>]+\s?[#>])\s*(?P<rest>.*)$")


class Collector:
    """Accumulates issues and record counts for one parse run."""

    def __init__(self, context: ParseContext, default_command: str) -> None:
        self.context = context
        self.command = context.command or default_command
        self.issues: list[ParseIssue] = []
        self.format_seen = False
        self.records = 0

    def reference(self, line_number: int) -> SourceReference:
        return SourceReference(
            filename=self.context.filename,
            command=self.command,
            start_line=line_number,
            end_line=line_number,
        )

    def issue(
        self,
        line_number: int,
        code: str,
        message: str,
        severity: IssueSeverity = IssueSeverity.WARNING,
    ) -> None:
        self.issues.append(
            ParseIssue(
                severity=severity,
                code=code,
                message=message,
                line_number=line_number,
                source_reference=self.reference(line_number),
            )
        )

    def record(self) -> None:
        self.records += 1
        self.format_seen = True

    def is_noise(self, raw_line: str) -> bool:
        """Blank lines, bare prompts and the echoed command carry no data."""
        stripped = raw_line.strip()
        if not stripped:
            return True
        match = _PROMPT.match(stripped)
        if match is None:
            return False
        rest = " ".join(match.group("rest").casefold().split())
        return not rest or rest == " ".join(self.command.casefold().split())


class OperationalParser(Parser):
    parser_version = "1.0"
    implementation_status = ImplementationStatus.TESTED_WITH_SYNTHETIC_DATA

    @abstractmethod
    def empty_data(self) -> object:
        """Return the empty result container for this command."""

    @abstractmethod
    def parse_lines(self, lines: list[str], collector: Collector) -> object:
        """Parse numbered-by-position lines; report every unhandled line."""

    def parse(self, text: str, context: ParseContext) -> ParseResult:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        collector = Collector(context, self.command)
        if not normalized.strip():
            collector.issues.append(
                ParseIssue(
                    severity=IssueSeverity.ERROR,
                    code="EMPTY_INPUT",
                    message="Input contains no command output.",
                )
            )
            data = self.empty_data()
        else:
            data = self.parse_lines(normalized.split("\n"), collector)

        blocking = [i for i in collector.issues if i.severity is not IssueSeverity.INFO]
        if collector.format_seen and not blocking:
            status = ParseStatus.SUCCESS
        elif collector.format_seen and collector.records:
            status = ParseStatus.PARTIAL_SUCCESS
        else:
            status = ParseStatus.FAILED
        return ParseResult(
            status=status,
            data=data,
            issues=collector.issues,
            unparsed_ranges=[
                issue.source_reference
                for issue in collector.issues
                if issue.source_reference is not None
            ],
            parser_metadata={
                "vendor": self.vendor,
                "os_family": self.os_family,
                "command": self.command,
                "parser_version": self.parser_version,
            },
            source_metadata=dict(context.source_metadata),
        )
