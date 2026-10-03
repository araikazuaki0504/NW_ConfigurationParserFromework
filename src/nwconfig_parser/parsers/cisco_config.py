"""Cisco IOS/IOS XE running-config parser: hierarchy plus semantic conversion."""

from __future__ import annotations

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
        stack: list[tuple[int, ConfigNode]] = []
        for line_number, raw_line in enumerate(normalized.split("\n"), start=1):
            if not raw_line.strip() or raw_line.lstrip().startswith("!"):
                continue
            indent = len(raw_line) - len(raw_line.lstrip())
            command = raw_line.strip()
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
