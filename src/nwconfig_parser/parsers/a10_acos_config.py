"""Configuration hierarchy and parser entry point; interpretation is separate."""

from __future__ import annotations

from nwconfig_parser.models import ConfigDocument
from nwconfig_parser.parsers.a10_acos_config_semantics import (
    A10AcosConfigInterpreter,
)
from nwconfig_parser.parsers.vendor_config_base import (
    ConfigInterpreter,
    VendorConfigParser,
    build_indent_hierarchy,
)


class A10AcosConfigParser(VendorConfigParser):
    vendor = "A10"
    os_family = "ACOS"
    command = "show running-config"

    def build_hierarchy(self, lines: list[str], document: ConfigDocument) -> None:
        build_indent_hierarchy(
            lines, document, comment_prefix="!", negation_prefix="no "
        )

    def create_interpreter(
        self, document: ConfigDocument, filename: str | None, command: str
    ) -> ConfigInterpreter:
        return A10AcosConfigInterpreter(document, filename, command)
