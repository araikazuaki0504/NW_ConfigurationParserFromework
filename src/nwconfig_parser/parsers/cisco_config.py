"""Cisco IOS/IOS XE running-config parser: hierarchy plus semantic conversion."""

from __future__ import annotations

import re

from nwconfig_parser.models import (
    ConfigDocument,
    ConfigNode,
    ImplementationStatus,
    IssueSeverity,
    ParseIssue,
    ParseResult,
    ParseStatus,
    SemanticStatus,
    SourceReference,
)
from nwconfig_parser.parsers.base import ParseContext, Parser
from nwconfig_parser.parsers.cisco_config_semantics import CiscoConfigInterpreter


class CiscoConfigParser(Parser):
    vendor = "Cisco"
    os_family = "IOS"
    supported_versions: tuple[str, ...] = ()
    command = "running-config"
    parser_version = "2.0"
    implementation_status = ImplementationStatus.TESTED_WITH_SYNTHETIC_DATA

    def __init__(self, os_family: str = "IOS") -> None:
        self.os_family = os_family

    def _metadata(self) -> dict[str, str]:
        return {
            "vendor": self.vendor,
            "os_family": self.os_family,
            "command": self.command,
            "parser_version": self.parser_version,
        }

    def parse(self, text: str, context: ParseContext) -> ParseResult:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        document = ConfigDocument(raw_text=text)
        if not normalized.strip():
            issue = ParseIssue(
                severity=IssueSeverity.ERROR,
                code="EMPTY_INPUT",
                message="Input contains no configuration text.",
                source_reference=SourceReference(
                    filename=context.filename,
                    command=context.command or self.command,
                ),
            )
            return ParseResult(
                status=ParseStatus.FAILED,
                data=document,
                issues=[issue],
                parser_metadata=self._metadata(),
                source_metadata=dict(context.source_metadata),
            )

        self._build_hierarchy(normalized, document)
        interpreter = CiscoConfigInterpreter(
            document, context.filename, context.command or self.command
        )
        interpreter.run()
        issues = interpreter.issues

        # Structure alone is not a semantic success: require at least one
        # interpreted statement, and any non-informational issue means PARTIAL.
        if interpreter.parsed_count == 0:
            status = ParseStatus.FAILED
        elif any(issue.severity is not IssueSeverity.INFO for issue in issues):
            status = ParseStatus.PARTIAL_SUCCESS
        else:
            status = ParseStatus.SUCCESS
        return ParseResult(
            status=status,
            data=document,
            issues=issues,
            unparsed_ranges=interpreter.unparsed,
            parser_metadata=self._metadata(),
            source_metadata=dict(context.source_metadata),
        )

    @staticmethod
    def _build_hierarchy(normalized: str, document: ConfigDocument) -> None:
        lines = normalized.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        stack: list[tuple[int, ConfigNode]] = []
        index = 0
        while index < len(lines):
            raw_line = lines[index]
            line_number = index + 1
            index += 1
            if not raw_line.strip() or raw_line.lstrip().startswith("!"):
                continue
            indent = len(raw_line) - len(raw_line.lstrip())
            command = raw_line.strip()
            multiline = _multiline_start(command) if indent == 0 else None
            if multiline is not None:
                kind, terminator, first_text = multiline
                node = ConfigNode(
                    command=command,
                    negated=False,
                    line_number=line_number,
                    raw_text=raw_line,
                    semantic_status=SemanticStatus.STRUCTURE_ONLY,
                    end_line=line_number,
                )
                trailing, index = _read_multiline_body(
                    node, kind, terminator, first_text, lines, index
                )
                document.roots.append(node)
                stack.clear()
                if trailing:
                    # Text after the closing delimiter is not part of the body;
                    # keep it as its own root so it is reported, not dropped.
                    document.roots.append(
                        ConfigNode(
                            command=trailing,
                            negated=False,
                            line_number=node.end_line or line_number,
                            raw_text=trailing,
                            semantic_status=SemanticStatus.STRUCTURE_ONLY,
                        )
                    )
                continue
            node = ConfigNode(
                command=command,
                negated=command.casefold().startswith("no "),
                line_number=line_number,
                raw_text=raw_line,
                semantic_status=SemanticStatus.STRUCTURE_ONLY,
            )
            while stack and stack[-1][0] >= indent:
                stack.pop()
            if stack:
                stack[-1][1].children.append(node)
            else:
                document.roots.append(node)
            stack.append((indent, node))


_BANNER = re.compile(r"^banner\s+(?P<kind>\S+)\s+(?P<rest>\S.*)$", re.IGNORECASE)
_MACRO = re.compile(r"^macro\s+name\s+\S+\s*$", re.IGNORECASE)


def _multiline_start(command: str) -> tuple[str, str, str] | None:
    """Detect a top-level construct whose body ignores indentation.

    Returns (kind, terminator, text after the opening delimiter).
    """
    banner = _BANNER.match(command)
    if banner is not None:
        rest = banner.group("rest")
        for delimiter in ("^C", "\x03"):
            if rest.startswith(delimiter):
                return "banner", delimiter, rest[len(delimiter) :]
        return "banner", rest[0], rest[1:]
    if _MACRO.match(command):
        return "macro", "@", ""
    return None


def _read_multiline_body(
    node: ConfigNode,
    kind: str,
    terminator: str,
    first_text: str,
    lines: list[str],
    index: int,
) -> tuple[str, int]:
    """Consume body lines; returns (text after terminator, next line index)."""
    pending = first_text
    while True:
        if kind == "macro":
            closed = pending.strip() == terminator
            before, after = ("", "") if closed else (pending, "")
        else:
            position = pending.find(terminator)
            closed = position >= 0
            before = pending[:position] if closed else pending
            after = pending[position + len(terminator) :] if closed else ""
        if closed:
            if kind == "banner" and (before or not node.body_lines):
                node.body_lines.append(before)
            return after.strip(), index
        if node.body_lines or pending:
            node.body_lines.append(pending)
        if index >= len(lines):
            node.terminated = False
            return "", index
        pending = lines[index]
        index += 1
        node.end_line = index
