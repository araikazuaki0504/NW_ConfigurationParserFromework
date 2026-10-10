"""Configuration hierarchy and parser entry point; interpretation is separate."""

from __future__ import annotations

from nwconfig_parser.models import ConfigDocument, ConfigNode, SemanticStatus
from nwconfig_parser.parsers.vendor_config_base import (
    ConfigInterpreter,
    VendorConfigParser,
)
from nwconfig_parser.parsers.yamaha_rtx_config_semantics import (
    YamahaRtxConfigInterpreter,
)


class YamahaRtxConfigParser(VendorConfigParser):
    vendor = "Yamaha"
    os_family = "RTX"
    command = "show config"

    def build_hierarchy(self, lines: list[str], document: ConfigDocument) -> None:
        for index, raw_line in enumerate(lines):
            command = raw_line.strip()
            if not command or command.startswith("#"):
                continue
            document.roots.append(
                ConfigNode(
                    command=command,
                    negated=command.casefold().startswith("no "),
                    line_number=index + 1,
                    raw_text=raw_line,
                    semantic_status=SemanticStatus.STRUCTURE_ONLY,
                )
            )

    def create_interpreter(
        self, document: ConfigDocument, filename: str | None, command: str
    ) -> ConfigInterpreter:
        return YamahaRtxConfigInterpreter(document, filename, command)
