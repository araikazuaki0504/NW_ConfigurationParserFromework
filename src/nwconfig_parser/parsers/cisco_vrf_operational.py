"""Cisco IOS / IOS XE ``show ip route vrf <name|*>`` parser.

Route lines are parsed by the existing ``show ip route`` grammar; this module
only decides which VRF each section belongs to. VRF membership is never
guessed: a section without a usable heading and without a named VRF in the
command is reported as ``UNKNOWN`` scope with an issue.
"""

from __future__ import annotations

import re

from nwconfig_parser.models import (
    ImplementationStatus,
    IssueSeverity,
    ParseIssue,
    ParseResult,
    ParseStatus,
    RoutingTable,
    SourceReference,
    VrfScope,
)
from nwconfig_parser.parsers.base import ParseContext, Parser
from nwconfig_parser.parsers.cisco_operational import CiscoOperationalParser

_HEADING = re.compile(r"^Routing Table:\s*(?P<name>\S+)\s*$")
_HEADING_LOOSE = re.compile(r"^Routing Table\b", re.IGNORECASE)
_COMMAND = re.compile(r"^show\s+ip\s+route\s+vrf\s+(?P<name>\S+)\s*$", re.IGNORECASE)


class CiscoShowIpRouteVrfParser(Parser):
    """Parses one named VRF (``vrf NAME``) or all VRFs (``vrf *``)."""

    vendor = "Cisco"
    command = "show ip route vrf *"
    parser_version = "1.0"
    implementation_status = ImplementationStatus.TESTED_WITH_SYNTHETIC_DATA

    def __init__(self, os_family: str) -> None:
        normalized = " ".join(os_family.casefold().split())
        if normalized not in {"ios", "ios xe"}:
            raise ValueError(f"Unsupported Cisco OS family: {os_family}")
        self.os_family = "IOS XE" if normalized == "ios xe" else "IOS"
        self._routes = CiscoOperationalParser("show ip route", self.os_family)

    def matches_command(self, command: str) -> bool:
        return _COMMAND.fullmatch(" ".join(command.split())) is not None

    def parse(self, text: str, context: ParseContext) -> ParseResult:
        command = " ".join((context.command or "").split())
        match = _COMMAND.fullmatch(command)
        requested = match.group("name") if match else None
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        issues: list[ParseIssue] = []
        tables: list[RoutingTable] = []
        records = 0
        format_seen = False

        if not text.strip():
            issues.append(
                ParseIssue(
                    severity=IssueSeverity.ERROR,
                    code="EMPTY_INPUT",
                    message="Input contains no command output.",
                )
            )
        else:
            for kind, name, start, end in self._sections(lines, issues, context):
                section = [""] * len(lines)
                first = start if kind == "pre" else start + 1
                section[first:end] = lines[first:end]
                table, section_issues, seen, count = self._routes._parse_routes(
                    section, context
                )
                if kind == "pre" and not (table.routes or section_issues or seen):
                    continue
                issues.extend(section_issues)
                format_seen = format_seen or seen or kind != "pre"
                records += count
                scope_name, scope = self._scope(
                    kind, name, requested, start, issues, context
                )
                self._apply(table, scope_name, scope, command, start, end, context)
                tables.append(table)

        blocking = [i for i in issues if i.severity is not IssueSeverity.INFO]
        if format_seen and not blocking:
            status = "SUCCESS"
        elif format_seen and records:
            status = "PARTIAL_SUCCESS"
        else:
            status = "FAILED"
        return self._result(status, tables, issues, context)

    @staticmethod
    def _sections(
        lines: list[str], issues: list[ParseIssue], context: ParseContext
    ) -> list[tuple[str, str | None, int, int]]:
        """Return (kind, heading name, start, end); kind is pre/named/broken."""
        headings: list[tuple[str, str | None, int]] = []
        for index, raw in enumerate(lines):
            stripped = raw.strip()
            matched = _HEADING.fullmatch(stripped)
            if matched is not None:
                headings.append(("named", matched.group("name"), index))
            elif _HEADING_LOOSE.match(stripped):
                headings.append(("broken", None, index))
                issues.append(
                    ParseIssue(
                        severity=IssueSeverity.WARNING,
                        code="VRF_HEADING_UNPARSEABLE",
                        message=(
                            "VRF heading could not be parsed; VRF membership of "
                            "the following routes is unknown."
                        ),
                        line_number=index + 1,
                        source_reference=_ref(context, index + 1, index + 1),
                    )
                )
        first = headings[0][2] if headings else len(lines)
        sections: list[tuple[str, str | None, int, int]] = [("pre", None, 0, first)]
        for position, (kind, name, index) in enumerate(headings):
            end = (
                headings[position + 1][2]
                if position + 1 < len(headings)
                else len(lines)
            )
            sections.append((kind, name, index, end))
        return sections

    @staticmethod
    def _scope(
        kind: str,
        heading: str | None,
        requested: str | None,
        start: int,
        issues: list[ParseIssue],
        context: ParseContext,
    ) -> tuple[str | None, VrfScope]:
        def warn(code: str, message: str) -> None:
            issues.append(
                ParseIssue(
                    severity=IssueSeverity.WARNING,
                    code=code,
                    message=message,
                    line_number=start + 1,
                    source_reference=_ref(context, start + 1, start + 1),
                )
            )

        if kind == "broken":
            return None, VrfScope.UNKNOWN
        if kind == "pre":
            if requested not in (None, "*"):
                return requested, VrfScope.NAMED
            warn(
                "VRF_CONTEXT_MISSING",
                "Routes appear before any VRF heading and the command names "
                "no single VRF; VRF membership is unknown.",
            )
            return None, VrfScope.UNKNOWN
        if requested not in (None, "*") and heading != requested:
            warn(
                "VRF_HEADING_MISMATCH",
                f"Heading names VRF {heading!r} but the command requested "
                f"{requested!r}; VRF membership is not assigned.",
            )
            return None, VrfScope.UNKNOWN
        return heading, VrfScope.NAMED

    @staticmethod
    def _apply(
        table: RoutingTable,
        name: str | None,
        scope: VrfScope,
        command: str,
        start: int,
        end: int,
        context: ParseContext,
    ) -> None:
        table.vrf, table.vrf_scope = name, scope
        table.source_command = command
        table.source_reference = _ref(context, start + 1, end)
        for route in table.routes:
            route.vrf, route.vrf_scope = name, scope

    def _result(
        self,
        status: str,
        tables: list[RoutingTable],
        issues: list[ParseIssue],
        context: ParseContext,
    ) -> ParseResult:
        return ParseResult(
            status=ParseStatus[status],
            data=tables,
            issues=issues,
            unparsed_ranges=[
                i.source_reference for i in issues if i.source_reference is not None
            ],
            parser_metadata={
                "vendor": self.vendor,
                "os_family": self.os_family,
                "command": self.command,
                "parser_version": self.parser_version,
            },
            source_metadata=dict(context.source_metadata),
        )


def _ref(context: ParseContext, start: int, end: int) -> SourceReference:
    return SourceReference(
        filename=context.filename,
        command=context.command,
        start_line=start,
        end_line=end,
    )
