"""Configuration hierarchy and parser entry point; interpretation is separate."""

from __future__ import annotations

from nwconfig_parser.models import ConfigDocument
from nwconfig_parser.parsers.hpe_comware_config_semantics import (
    HpeComwareConfigInterpreter,
)
from nwconfig_parser.parsers.vendor_config_base import (
    ConfigInterpreter,
    VendorConfigParser,
    build_indent_hierarchy,
)


class HpeComwareConfigParser(VendorConfigParser):
    vendor = "HPE"
    os_family = "Comware"
    command = "display current-configuration"

    def build_hierarchy(self, lines: list[str], document: ConfigDocument) -> None:
        build_indent_hierarchy(
            lines, document, comment_prefix="#", negation_prefix="undo "
        )

    def create_interpreter(
        self, document: ConfigDocument, filename: str | None, command: str
    ) -> ConfigInterpreter:
        return HpeComwareConfigInterpreter(document, filename, command)
