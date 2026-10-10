"""Shared scaffolding for vendor configuration parsers (non-Cisco).

Vendor modules supply the hierarchy builder and the semantic interpreter; this
module owns normalisation, empty-input handling, status decisions and the
helpers that keep rejected statements visible. Issue messages are static text:
they never embed configuration text, addresses or credentials.
"""

from __future__ import annotations

from abc import abstractmethod
from collections import OrderedDict
from collections.abc import Iterator
from ipaddress import AddressValueError, IPv4Address, IPv4Interface, IPv4Network

from nwconfig_parser.models import (
    ConfigDocument,
    ConfigNode,
    ImplementationStatus,
    Interface,
    IssueSeverity,
    ParseIssue,
    ParseResult,
    ParseStatus,
    SemanticStatus,
    SourceReference,
    UnsupportedConfigLine,
)
from nwconfig_parser.parsers.base import ParseContext, Parser


class Reject(Exception):
    """A statement is recognised but cannot be represented safely."""

    def __init__(self, reason: str, *, invalid: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.invalid = invalid


def walk(node: ConfigNode) -> Iterator[ConfigNode]:
    for child in node.children:
        yield child
        yield from walk(child)


def parse_ipv4(value: str) -> IPv4Address:
    try:
        return IPv4Address(value)
    except AddressValueError as exc:
        raise Reject("Invalid IPv4 address.", invalid=True) from exc


def mask_to_prefix(token: str, *, allow_hex: bool = False) -> int:
    """Convert a bit count, dotted netmask or (optionally) 0x mask to a prefix."""
    if token.isdigit():
        prefix = int(token)
        if prefix > 32:
            raise Reject("Invalid prefix length.", invalid=True)
        return prefix
    if allow_hex and token.lower().startswith("0x"):
        try:
            value = int(token, 16)
        except ValueError as exc:
            raise Reject("Invalid netmask.", invalid=True) from exc
        if not 0 <= value <= 0xFFFFFFFF:
            raise Reject("Invalid netmask.", invalid=True)
    else:
        try:
            value = int(IPv4Address(token))
        except AddressValueError as exc:
            raise Reject("Invalid netmask.", invalid=True) from exc
    prefix = bin(value).count("1")
    if value != (((1 << prefix) - 1) << (32 - prefix)) & 0xFFFFFFFF:
        raise Reject("Non-contiguous netmask.", invalid=True)
    return prefix


def make_network(address: str, prefix: int) -> IPv4Network:
    try:
        return IPv4Network(f"{parse_ipv4(address)}/{prefix}", strict=True)
    except ValueError as exc:
        raise Reject("Network address has host bits set.", invalid=True) from exc


def make_interface(address: str, prefix: int) -> IPv4Interface:
    return IPv4Interface(f"{parse_ipv4(address)}/{prefix}")


def build_indent_hierarchy(
    lines: list[str],
    document: ConfigDocument,
    *,
    comment_prefix: str,
    negation_prefix: str,
    separator_resets: bool = True,
) -> None:
    """Indentation hierarchy. Comment lines never become nodes."""
    stack: list[tuple[int, ConfigNode]] = []
    for index, raw_line in enumerate(lines):
        if not raw_line.strip():
            continue
        if raw_line.lstrip().startswith(comment_prefix):
            if separator_resets:
                stack.clear()
            continue
        indent = len(raw_line) - len(raw_line.lstrip())
        command = raw_line.strip()
        node = ConfigNode(
            command=command,
            negated=command.casefold().startswith(negation_prefix),
            line_number=index + 1,
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


class ConfigInterpreter:
    """Converts a hierarchy into the common model and records what it skipped."""

    def __init__(
        self, document: ConfigDocument, filename: str | None, command: str
    ) -> None:
        self.document = document
        self.filename = filename
        self.command = command
        self.issues: list[ParseIssue] = []
        self.unparsed: list[SourceReference] = []
        self.parsed_count = 0
        self.interfaces: OrderedDict[str, Interface] = OrderedDict()

    # Subclasses implement these two.
    @abstractmethod
    def handle_root(self, node: ConfigNode) -> None: ...

    def finalize(self) -> None:
        self.document.interfaces = list(self.interfaces.values())

    def run(self) -> None:
        for node in self.document.roots:
            try:
                self.handle_root(node)
            except Reject as rejection:
                self.reject(node, rejection.reason, invalid=rejection.invalid)
        self.finalize()

    def interface(self, name: str) -> Interface:
        found = self.interfaces.get(name)
        if found is None:
            found = Interface(name=name)
            self.interfaces[name] = found
        return found

    def reference(self, start: int, end: int | None = None) -> SourceReference:
        return SourceReference(
            filename=self.filename,
            command=self.command,
            start_line=start,
            end_line=end if end is not None else start,
        )

    @staticmethod
    def last_line(node: ConfigNode) -> int:
        return max(
            [node.line_number, node.end_line or 0]
            + [max(c.line_number, c.end_line or 0) for c in walk(node)]
        )

    def parsed(self, node: ConfigNode, *, count: bool = True) -> None:
        node.semantic_status = SemanticStatus.PARSED
        if count:
            self.parsed_count += 1

    def run_child(self, child: ConfigNode, handler: object) -> None:
        assert callable(handler)
        try:
            handler(child)
        except Reject as rejection:
            self.reject(child, rejection.reason, invalid=rejection.invalid)

    def reject(
        self,
        node: ConfigNode,
        reason: str,
        *,
        invalid: bool = False,
        parent: ConfigNode | None = None,
        record_issue: bool = True,
        code: str | None = None,
        reject_children: bool = True,
    ) -> None:
        node.semantic_status = (
            SemanticStatus.INVALID if invalid else SemanticStatus.UNSUPPORTED
        )
        self.document.unsupported_lines.append(
            UnsupportedConfigLine(
                line_number=node.line_number,
                raw_text=node.raw_text,
                reason=reason,
                parent=parent.command if parent is not None else None,
            )
        )
        for child in node.children if reject_children else ():
            self.reject(
                child,
                f"Inside unsupported block: {reason}",
                invalid=invalid,
                parent=node,
                record_issue=False,
            )
        if not record_issue:
            return
        reference = self.reference(node.line_number, self.last_line(node))
        self.unparsed.append(reference)
        self.issues.append(
            ParseIssue(
                severity=IssueSeverity.ERROR if invalid else IssueSeverity.WARNING,
                code=code
                or ("INVALID_CONFIG_STATEMENT" if invalid else "UNSUPPORTED_CONFIG"),
                message=reason,
                line_number=node.line_number,
                source_reference=reference,
            )
        )

    def info(self, node: ConfigNode, code: str, message: str) -> None:
        self.issues.append(
            ParseIssue(
                severity=IssueSeverity.INFO,
                code=code,
                message=message,
                line_number=node.line_number,
                source_reference=self.reference(node.line_number),
            )
        )

    def report_unterminated(self) -> None:
        for root in self.document.roots:
            for node in (root, *walk(root)):
                if node.terminated:
                    continue
                reference = self.reference(node.line_number, self.last_line(node))
                self.unparsed.append(reference)
                self.issues.append(
                    ParseIssue(
                        severity=IssueSeverity.WARNING,
                        code="UNTERMINATED_BLOCK",
                        message="Block is not closed before the end of input.",
                        line_number=node.line_number,
                        source_reference=reference,
                    )
                )


class VendorConfigParser(Parser):
    supported_versions: tuple[str, ...] = ()
    parser_version = "1.0"
    implementation_status = ImplementationStatus.TESTED_WITH_SYNTHETIC_DATA

    @abstractmethod
    def build_hierarchy(self, lines: list[str], document: ConfigDocument) -> None: ...

    @abstractmethod
    def create_interpreter(
        self, document: ConfigDocument, filename: str | None, command: str
    ) -> ConfigInterpreter: ...

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
        command = context.command or self.command
        if not normalized.strip():
            return ParseResult(
                status=ParseStatus.FAILED,
                data=document,
                issues=[
                    ParseIssue(
                        severity=IssueSeverity.ERROR,
                        code="EMPTY_INPUT",
                        message="Input contains no configuration text.",
                        source_reference=SourceReference(
                            filename=context.filename, command=command
                        ),
                    )
                ],
                parser_metadata=self._metadata(),
                source_metadata=dict(context.source_metadata),
            )
        lines = normalized.split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        self.build_hierarchy(lines, document)
        interpreter = self.create_interpreter(document, context.filename, command)
        interpreter.run()
        interpreter.report_unterminated()
        issues = interpreter.issues
        # Structure alone is not semantic success: require an interpreted
        # statement, and any non-informational issue means PARTIAL_SUCCESS.
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
