"""Semantic interpretation of vendor configuration into common models.

Only statements whose syntax is documented in the Yamaha RT command reference
are interpreted. ``pp select``/``tunnel select`` set a statement-order context
(never inferred from indentation) used by ``description pp|tunnel`` and
``ip pp address``; other lines in those sections are reported as unsupported.
"""

from __future__ import annotations

import re
from ipaddress import IPv4Network
from typing import Any

from nwconfig_parser.models import ConfigNode, Route
from nwconfig_parser.parsers.vendor_config_base import (
    ConfigInterpreter,
    Reject,
    make_interface,
    make_network,
    mask_to_prefix,
    parse_ipv4,
)

_INTERFACE = re.compile(r"^(?:lan|wan|bridge|loopback)\d+$", re.IGNORECASE)
_DESCRIPTION = re.compile(r"^description\s+(?P<target>\S+)\s+(?P<text>\S.*)$")
_CONTEXT_TARGETS = {"pp", "tunnel"}
_NUMBER = re.compile(r"^\d+$")


class YamahaRtxConfigInterpreter(ConfigInterpreter):
    # (kind, number) chosen by the latest `pp select` / `tunnel select`.
    _context: tuple[str, str] | None = None

    def handle_root(self, node: ConfigNode) -> None:
        tokens = node.command.split()
        negated = node.negated
        if negated:
            tokens = tokens[1:]
        keyword = tokens[0].casefold() if tokens else ""
        if keyword in _CONTEXT_TARGETS and len(tokens) >= 2 and tokens[1] == "select":
            self._select(node, keyword, tokens[2:], negated)
        elif keyword == "description" and not negated:
            self._description(node)
        elif keyword == "ip" and len(tokens) >= 2 and tokens[1].casefold() == "route":
            self._route(node, tokens[2:], negated)
        elif keyword == "ip" and len(tokens) >= 3 and tokens[2].casefold() == "address":
            self._address(node, tokens[1], tokens[3:], negated)
        elif keyword == "lan" and len(tokens) >= 3 and tokens[1] == "shutdown":
            self._shutdown(node, tokens[2:], negated)
        elif keyword == "snmp" and len(tokens) >= 2 and tokens[1] == "sysname":
            raise Reject("snmp sysname is not a hostname command; not mapped.")
        else:
            raise Reject("Statement is not supported by this parser.")

    def _select(
        self, node: ConfigNode, kind: str, rest: list[str], negated: bool
    ) -> None:
        if negated:
            if rest:
                raise Reject("Statement is not supported by this parser.")
            self._context = None
        elif len(rest) != 1:
            raise Reject("Statement is not supported by this parser.", invalid=True)
        elif rest[0] == "none":
            self._context = None
        elif _NUMBER.fullmatch(rest[0]) is not None:
            self._context = (kind, rest[0])
        elif kind == "pp" and rest[0] == "anonymous":
            self._context = (kind, rest[0])
        else:
            raise Reject("Select value is not valid.", invalid=True)
        self.parsed(node, count=False)

    def _name(self, token: str) -> str:
        kind = token.casefold()
        if kind in _CONTEXT_TARGETS:
            if self._context is None or self._context[0] != kind:
                raise Reject("No matching pp/tunnel select context is active.")
            if self._context[1] == "anonymous":
                raise Reject("The anonymous pp context is not modelled.")
            return f"{kind} {self._context[1]}"
        if _INTERFACE.fullmatch(token) is None:
            raise Reject("Interface name form is not supported.")
        return token.casefold()

    def _description(self, node: ConfigNode) -> None:
        match = _DESCRIPTION.match(node.command)
        if match is None:
            raise Reject("Statement is not supported by this parser.")
        name = self._name(match.group("target"))
        self.interface(name).description = match.group("text")
        self.parsed(node)

    def _address(
        self, node: ConfigNode, target: str, rest: list[str], negated: bool
    ) -> None:
        name = self._name(target)
        if target.casefold() == "tunnel":
            raise Reject("ip tunnel address is not documented; not supported.")
        if negated:
            if len(rest) > 1:
                raise Reject("Statement is not supported by this parser.")
            if not rest:
                if name in self.interfaces:
                    self.interfaces[name].ipv4_addresses.clear()
                else:
                    self._missing(node)
            else:
                self._remove_address(node, name, rest[0])
            self.parsed(node)
            return
        if len(rest) != 1:
            raise Reject("Address options are not supported.")
        if rest[0].casefold() == "dhcp":
            raise Reject("DHCP address assignment is not modelled.")
        address, _, mask = rest[0].partition("/")
        if not mask:
            if target.casefold() == "pp":
                raise Reject("pp address without a mask is not supported.")
            raise Reject("Address without mask is not supported.", invalid=True)
        interface = make_interface(address, mask_to_prefix(mask, allow_hex=True))
        entry = self.interface(name)
        entry.ipv4_addresses = [interface]
        self.parsed(node)

    def _remove_address(self, node: ConfigNode, name: str, value: str) -> None:
        address, _, mask = value.partition("/")
        wanted = make_interface(address, mask_to_prefix(mask, allow_hex=True))
        entry = self.interfaces.get(name)
        if entry is None or wanted not in entry.ipv4_addresses:
            self._missing(node)
        else:
            entry.ipv4_addresses.remove(wanted)

    def _missing(self, node: ConfigNode) -> None:
        self.info(
            node,
            "NEGATION_TARGET_NOT_IN_INPUT",
            "The configuration removed by this statement is not in the input.",
        )

    def _shutdown(self, node: ConfigNode, rest: list[str], negated: bool) -> None:
        if len(rest) != 1:
            raise Reject("Port-level shutdown is not an interface state.")
        name = self._name(rest[0])
        self.interface(name).admin_status = "up" if negated else "down"
        self.parsed(node)

    def _route(self, node: ConfigNode, tokens: list[str], negated: bool) -> None:
        if not tokens:
            raise Reject("Statement is not supported by this parser.")
        network = self._network(tokens[0])
        gateways = self._gateways(tokens[1:], allow_empty=negated)
        targets = [item["target"] for item in gateways]
        if negated:
            before = len(self.document.static_routes)
            self.document.static_routes = [
                route
                for route in self.document.static_routes
                if not (
                    route.network == network
                    and (
                        not targets
                        or targets == route.attributes.get("gateway_targets")
                    )
                )
            ]
            if len(self.document.static_routes) == before:
                self._missing(node)
            self.parsed(node)
            return
        next_hops = [
            parse_ipv4(item["target"]) for item in gateways if "." in item["target"]
        ]
        interfaces = [item["target"] for item in gateways if "." not in item["target"]]
        metric = gateways[0].get("metric") if len(gateways) == 1 else None
        route = Route(
            network=network,
            protocol="static",
            next_hops=next_hops,
            outgoing_interfaces=interfaces,
            metric=metric,
            attributes={
                "line_number": node.line_number,
                "gateways": gateways,
                "gateway_targets": targets,
            },
        )
        self.document.static_routes = [
            existing
            for existing in self.document.static_routes
            if not (
                existing.network == network
                and existing.attributes.get("gateway_targets") == targets
            )
        ]
        self.document.static_routes.append(route)
        self.parsed(node)

    @staticmethod
    def _network(token: str) -> IPv4Network:
        if token.casefold() == "default":
            return make_network("0.0.0.0", 0)
        address, _, mask = token.partition("/")
        prefix = mask_to_prefix(mask, allow_hex=True) if mask else 32
        return make_network(address, prefix)

    @staticmethod
    def _gateways(tokens: list[str], *, allow_empty: bool) -> list[dict[str, Any]]:
        if not tokens:
            if allow_empty:
                return []
            raise Reject("Route without a gateway is not supported.")
        gateways: list[dict[str, Any]] = []
        index = 0
        while index < len(tokens):
            if tokens[index].casefold() != "gateway" or index + 1 >= len(tokens):
                raise Reject("Route option is not supported.")
            index += 1
            first = tokens[index]
            if "." in first:
                parse_ipv4(first)
                gateway: dict[str, Any] = {"target": first}
                index += 1
            elif (
                first.casefold() in _CONTEXT_TARGETS
                and index + 1 < len(tokens)
                and tokens[index + 1].isdigit()
            ):
                gateway = {"target": f"{first.casefold()} {tokens[index + 1]}"}
                index += 2
            else:
                raise Reject("Gateway form is not supported.")
            while index < len(tokens) and tokens[index].casefold() != "gateway":
                index = _gateway_parameter(gateway, tokens, index)
            gateways.append(gateway)
        return gateways


def _gateway_parameter(gateway: dict[str, Any], tokens: list[str], index: int) -> int:
    key = tokens[index].casefold()
    if key == "hide":
        gateway["hide"] = True
        return index + 1
    if key in {"metric", "weight", "keepalive"} and index + 1 < len(tokens):
        value = tokens[index + 1]
        if not value.isdigit():
            raise Reject("Invalid route parameter value.", invalid=True)
        gateway[key] = int(value)
        return index + 2
    if key == "filter":
        numbers: list[int] = []
        index += 1
        while index < len(tokens) and tokens[index].isdigit():
            numbers.append(int(tokens[index]))
            index += 1
        if not numbers:
            raise Reject("Invalid route parameter value.", invalid=True)
        gateway["filter"] = numbers
        return index
    raise Reject("Route option is not supported.")
