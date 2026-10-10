"""Configuration hierarchy and parser entry point; interpretation is separate."""

from __future__ import annotations

from nwconfig_parser.models import (
    ConfigDocument,
    ConfigNode,
    SemanticStatus,
)
from nwconfig_parser.parsers.fortinet_fortios_config_semantics import (
    FortinetFortiosConfigInterpreter,
    _keyword,
    _scan,
    _statement,
)
from nwconfig_parser.parsers.vendor_config_base import (
    ConfigInterpreter,
    VendorConfigParser,
)


class FortinetFortiosConfigParser(VendorConfigParser):
    vendor = "Fortinet"
    os_family = "FortiOS"
    command = "show"

    def build_hierarchy(self, lines: list[str], document: ConfigDocument) -> None:
        stack: list[ConfigNode] = []

        def attach(node: ConfigNode) -> None:
            if stack:
                stack[-1].children.append(node)
            else:
                document.roots.append(node)

        index = 0
        while index < len(lines):
            raw_line = lines[index]
            index += 1
            command = raw_line.strip()
            if not command or command.startswith("#"):
                continue
            node = ConfigNode(
                command=command,
                negated=False,
                line_number=index,
                raw_text=raw_line,
                semantic_status=SemanticStatus.STRUCTURE_ONLY,
                end_line=index,
            )
            _, open_quote = _scan(command)
            while open_quote:
                if index >= len(lines):
                    node.terminated = False
                    break
                node.body_lines.append(lines[index])
                index += 1
                node.end_line = index
                _, open_quote = _scan(_statement(node))
            keyword = _keyword(command)
            if keyword in {"config", "edit"}:
                attach(node)
                stack.append(node)
            elif keyword == "next":
                if stack and _keyword(stack[-1].command) == "edit":
                    stack.pop()
                else:
                    attach(node)
            elif keyword == "end":
                while stack and _keyword(stack[-1].command) == "edit":
                    stack.pop().terminated = False
                if stack:
                    stack.pop()
                else:
                    attach(node)
            else:
                attach(node)
        for open_node in stack:
            open_node.terminated = False

    def create_interpreter(
        self, document: ConfigDocument, filename: str | None, command: str
    ) -> ConfigInterpreter:
        return FortinetFortiosConfigInterpreter(document, filename, command)
