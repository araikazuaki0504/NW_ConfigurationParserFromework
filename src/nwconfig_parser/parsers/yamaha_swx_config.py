"""Yamaha SWX configuration hierarchy and parser entry point.

SWX uses a Cisco-like running-config: ``!`` separates blocks and child lines are
indented. Statement interpretation lives in ``yamaha_swx_config_semantics``.
"""

from __future__ import annotations

import re

from nwconfig_parser.models import ConfigDocument
from nwconfig_parser.parsers.vendor_config_base import (
    ConfigInterpreter,
    VendorConfigParser,
    build_indent_hierarchy,
)
from nwconfig_parser.parsers.yamaha_swx_config_semantics import (
    YamahaSwxConfigInterpreter,
)

_PROMPT_LINE = re.compile(r"\S+[#>]\s*(?:show\s+running-config)?\s*", re.IGNORECASE)


class YamahaSwxConfigParser(VendorConfigParser):
    vendor = "Yamaha"
    os_family = "SWX"
    command = "show running-config"

    def build_hierarchy(self, lines: list[str], document: ConfigDocument) -> None:
        # Console prompt / command-echo lines are blanked, keeping line numbers.
        cleaned = ["" if _PROMPT_LINE.fullmatch(line) else line for line in lines]
        build_indent_hierarchy(
            cleaned, document, comment_prefix="!", negation_prefix="no "
        )

    def create_interpreter(
        self, document: ConfigDocument, filename: str | None, command: str
    ) -> ConfigInterpreter:
        return YamahaSwxConfigInterpreter(document, filename, command)
