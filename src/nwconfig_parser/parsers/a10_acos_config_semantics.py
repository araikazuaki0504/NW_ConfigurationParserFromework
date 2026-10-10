"""Semantic interpretation of vendor configuration into common models.

Interpreted: ``hostname``, ``interface ethernet|management|ve`` blocks
(``enable``/``disable``, ``ip address``) and global ``ip route``. Partitions,
VLANs and everything else are reported as unsupported; an ACOS partition is
not mapped to a VRF.
"""

from __future__ import annotations

from nwconfig_parser.models import ConfigNode, Route
from nwconfig_parser.parsers.vendor_config_base import (
    ConfigInterpreter,
    Reject,
    make_interface,
    make_network,
    mask_to_prefix,
    parse_ipv4,
)


def _prefix(token: str) -> int:
    if token.startswith("/"):
        if not token[1:].isdigit():
            raise Reject("Invalid prefix length.", invalid=True)
        return mask_to_prefix(token[1:])
    return mask_to_prefix(token)


class A10AcosConfigInterpreter(ConfigInterpreter):
    def handle_root(self, node: ConfigNode) -> None:
        if node.negated:
            raise Reject("Negated statements are not interpreted.")
        tokens = node.command.split()
        keyword = tokens[0].casefold()
        if keyword == "hostname" and len(tokens) == 2 and not node.children:
            self.document.hostname = tokens[1]
            self.parsed(node)
        elif keyword == "interface":
            self._interface(node, tokens)
        elif keyword == "ip" and len(tokens) >= 2 and tokens[1] == "route":
            self._route(node, tokens[2:])
        else:
            raise Reject("Statement is not supported by this parser.")

    def _interface(self, node: ConfigNode, tokens: list[str]) -> None:
        kind = tokens[1].casefold() if len(tokens) > 1 else ""
        if kind in {"ethernet", "ve"} and len(tokens) == 3 and tokens[2].isdigit():
            name = f"{kind} {tokens[2]}"
        elif kind == "management" and len(tokens) == 2:
            name = "management"
        else:
            raise Reject("Interface type is not supported by this parser.")
        self.interface(name)
        self.parsed(node)
        for child in node.children:
            self.run_child(child, lambda item: self._interface_child(name, item))

    def _interface_child(self, name: str, node: ConfigNode) -> None:
        tokens = node.command.split()
        entry = self.interface(name)
        keyword = tokens[0].casefold()
        if node.negated or node.children:
            raise Reject("Statement is not supported by this parser.")
        if keyword == "enable" and len(tokens) == 1:
            entry.admin_status = "up"
        elif keyword == "disable" and len(tokens) == 1:
            entry.admin_status = "down"
        elif keyword == "ip" and len(tokens) == 4 and tokens[1] == "address":
            entry.ipv4_addresses = [make_interface(tokens[2], _prefix(tokens[3]))]
        else:
            raise Reject("Interface setting is not supported by this parser.")
        self.parsed(node)

    def _route(self, node: ConfigNode, tokens: list[str]) -> None:
        if node.children:
            raise Reject("Statement is not supported by this parser.")
        if len(tokens) != 3:
            raise Reject("Route options are not supported by this parser.")
        network = make_network(tokens[0], _prefix(tokens[1]))
        self.document.static_routes.append(
            Route(
                network=network,
                protocol="static",
                next_hops=[parse_ipv4(tokens[2])],
                attributes={"line_number": node.line_number},
            )
        )
        self.parsed(node)
