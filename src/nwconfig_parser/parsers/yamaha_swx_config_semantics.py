"""Semantic interpretation of Yamaha SWX running-config.

Only syntax documented in the SWX2320 (Rev.2.05-2.08) and SWX3200 (Rev.4.00)
command references is interpreted. Documented defaults (hostname, switchport
mode, access VLAN 1, native VLAN 1, ip route distance 1) are never filled in:
absent settings stay ``None``. Statements whose meaning differs between the
documented models, or that depend on the set of defined VLANs, are reported as
unsupported rather than guessed.
"""

from __future__ import annotations

import re
from ipaddress import IPv4Network

from nwconfig_parser.models import VLAN, ConfigDocument, ConfigNode, Interface, Route
from nwconfig_parser.parsers.vendor_config_base import (
    ConfigInterpreter,
    Reject,
    make_interface,
    make_network,
    mask_to_prefix,
    parse_ipv4,
)

_PORT = re.compile(r"^port\d+\.\d+$", re.IGNORECASE)
_VLAN_IF = re.compile(r"^vlan(?P<id>\d+)$", re.IGNORECASE)
_LOGICAL = re.compile(r"^(?:sa|po)\d+$", re.IGNORECASE)
_ADDRESS = re.compile(r"^(?P<ip>[\d.]+)/(?P<mask>\d+)$")
_UNSUPPORTED = "Statement is not supported by this parser."


def _vlan_id(token: str, *, lowest: int = 1) -> int:
    if not token.isdigit() or not lowest <= int(token) <= 4094:
        raise Reject("VLAN ID is out of range.", invalid=True)
    return int(token)


def _vlan_list(token: str, *, lowest: int = 1) -> list[int]:
    """Parse ``2-4,6``; ranges and commas are documented for SWX."""
    result: list[int] = []
    for part in token.split(","):
        low, dash, high = part.partition("-")
        first = _vlan_id(low, lowest=lowest)
        last = _vlan_id(high, lowest=lowest) if dash else first
        if last < first:
            raise Reject("VLAN range is reversed.", invalid=True)
        result.extend(range(first, last + 1))
    return result


class YamahaSwxConfigInterpreter(ConfigInterpreter):
    def __init__(
        self, document: ConfigDocument, filename: str | None, command: str
    ) -> None:
        super().__init__(document, filename, command)
        self._vlans: dict[int, VLAN] = {}

    def finalize(self) -> None:
        super().finalize()
        self.document.vlans = [self._vlans[key] for key in sorted(self._vlans)]

    def handle_root(self, node: ConfigNode) -> None:
        tokens = node.command.split()
        negated = node.negated
        if negated:
            tokens = tokens[1:]
        keyword = tokens[0].casefold() if tokens else ""
        if keyword == "hostname":
            self._hostname(node, tokens[1:], negated)
        elif keyword == "interface" and not negated:
            self._interface(node, tokens[1:])
        elif keyword == "vlan" and tokens[1:2] == ["database"] and not negated:
            self._vlan_database(node, tokens[2:])
        elif keyword == "ip" and tokens[1:2] == ["route"]:
            self._route(node, tokens[2:], negated)
        elif keyword == "end" and len(tokens) == 1 and not negated:
            self.parsed(node, count=False)
        elif keyword == "ip" and tokens[1:2] == ["forwarding"]:
            raise Reject("ip forwarding is not mapped to the ip routing state.")
        else:
            raise Reject(_UNSUPPORTED)

    def _hostname(self, node: ConfigNode, rest: list[str], negated: bool) -> None:
        if negated:
            if len(rest) > 1:
                raise Reject(_UNSUPPORTED)
            if not rest or rest[0] == self.document.hostname:
                self.document.hostname = None
            self.parsed(node)
            return
        if len(rest) != 1:
            raise Reject(_UNSUPPORTED, invalid=True)
        if len(rest[0]) > 63:
            raise Reject("Hostname is longer than 63 characters.", invalid=True)
        self.document.hostname = rest[0]
        self.parsed(node)

    def _vlan_database(self, node: ConfigNode, rest: list[str]) -> None:
        if rest:
            raise Reject(_UNSUPPORTED)
        self.parsed(node)
        for child in node.children:
            self.run_child(child, self._vlan_statement)

    def _vlan_statement(self, node: ConfigNode) -> None:
        tokens = node.command.split()
        if node.negated:
            tokens = tokens[1:]
        if tokens[:1] != ["vlan"] or len(tokens) < 2:
            raise Reject(_UNSUPPORTED)
        ids = _vlan_list(tokens[1], lowest=2)
        options = tokens[2:]
        if node.negated:
            if options:
                raise Reject(_UNSUPPORTED)
            for vlan_id in ids:
                self._vlans.pop(vlan_id, None)
            self.parsed(node)
            return
        name: str | None = None
        state: str | None = None
        index = 0
        while index < len(options):
            key = options[index]
            if index + 1 >= len(options) or key not in {"name", "state"}:
                raise Reject(_UNSUPPORTED)
            value = options[index + 1]
            if key == "name" and name is None:
                name = value
            elif key == "state" and state is None:
                state = value
            else:
                raise Reject(_UNSUPPORTED)
            index += 2
        if name is not None and len(name) > 32:
            raise Reject("VLAN name is longer than 32 characters.", invalid=True)
        if name is not None and len(ids) > 1:
            # SWX2320 documents one shared name; SWX3200 forbids it.
            raise Reject("A name together with a VLAN range differs by model.")
        if state not in {None, "enable", "disable"}:
            raise Reject("VLAN state is not valid.", invalid=True)
        for vlan_id in ids:
            if state == "disable":
                self._vlans.pop(vlan_id, None)
                continue
            vlan = self._vlans.setdefault(vlan_id, VLAN(vlan_id=vlan_id))
            vlan.status = "enable"
            if name is not None:
                vlan.name = name
        self.parsed(node)

    def _interface(self, node: ConfigNode, rest: list[str]) -> None:
        if len(rest) != 1:
            raise Reject("Interface selection form is not supported.")
        name = rest[0].casefold()
        match = _VLAN_IF.fullmatch(name)
        if match is not None:
            _vlan_id(match.group("id"))
        elif _PORT.fullmatch(name) is None and _LOGICAL.fullmatch(name) is None:
            raise Reject("Interface name form is not supported.")
        interface = self.interface(name)
        self.parsed(node)
        for child in node.children:
            self.run_child(
                child, lambda item: self._interface_statement(item, interface)
            )

    def _interface_statement(self, node: ConfigNode, interface: Interface) -> None:
        tokens = node.command.split()
        negated = node.negated
        if negated:
            tokens = tokens[1:]
        keyword = tokens[0] if tokens else ""
        is_vlan = _VLAN_IF.fullmatch(interface.name) is not None
        if keyword == "description":
            self._description(node, interface, negated)
        elif keyword == "shutdown" and len(tokens) == 1:
            interface.admin_status = "up" if negated else "down"
        elif keyword == "switchport" and len(tokens) > 1 and not is_vlan:
            self._switchport(interface, tokens[1:], negated)
        elif keyword == "ip" and tokens[1:2] == ["address"] and is_vlan:
            self._address(interface, tokens[2:], negated)
        else:
            raise Reject(_UNSUPPORTED)
        self.parsed(node)

    def _description(
        self, node: ConfigNode, interface: Interface, negated: bool
    ) -> None:
        text = node.command.split(None, 2 if negated else 1)
        if negated:
            if len(text) > 2:
                raise Reject(_UNSUPPORTED)
            interface.description = None
            return
        if len(text) != 2:
            raise Reject(_UNSUPPORTED, invalid=True)
        if len(text[1]) > 80:
            raise Reject("Description is longer than 80 characters.", invalid=True)
        interface.description = text[1]

    def _switchport(self, interface: Interface, rest: list[str], negated: bool) -> None:
        attributes = interface.attributes
        if rest[0] == "mode" and not negated and len(rest) >= 2:
            self._mode(interface, rest[1:])
        elif rest[:2] == ["access", "vlan"] and len(rest) == (2 if negated else 3):
            if interface.mode == "trunk":
                raise Reject("Access VLAN requires an access port.", invalid=True)
            interface.access_vlan = None if negated else _vlan_id(rest[2])
        elif rest == ["trunk"] and negated:
            interface.mode = "access"
            self._reset_trunk(interface)
        elif rest[:3] == ["trunk", "native", "vlan"] and len(rest) == (
            3 if negated else 4
        ):
            if interface.mode != "trunk":
                raise Reject("Native VLAN requires an explicit trunk port.")
            if negated:
                attributes.pop("native_vlan", None)
            elif rest[3] == "none":
                attributes["native_vlan"] = "none"
            else:
                native = _vlan_id(rest[3])
                attributes["native_vlan"] = native
                if native in interface.trunk_vlans:
                    interface.trunk_vlans.remove(native)
        elif (
            rest[:3] == ["trunk", "allowed", "vlan"] and not negated and len(rest) >= 4
        ):
            self._allowed(interface, rest[3:])
        else:
            raise Reject(_UNSUPPORTED)

    @staticmethod
    def _reset_trunk(interface: Interface) -> None:
        interface.trunk_vlans.clear()
        interface.attributes.pop("native_vlan", None)
        interface.attributes.pop("trunk_vlans_complete", None)

    def _mode(self, interface: Interface, rest: list[str]) -> None:
        if rest[0] == "access" and len(rest) == 1:
            if interface.mode == "trunk":
                self._reset_trunk(interface)
            interface.mode = "access"
        elif rest[0] == "trunk" and len(rest) in {1, 3}:
            if len(rest) == 3:
                if rest[1] != "ingress-filter" or rest[2] not in {"enable", "disable"}:
                    raise Reject(_UNSUPPORTED)
                interface.attributes["ingress_filter"] = rest[2]
            if interface.mode != "trunk":
                interface.access_vlan = None
            interface.mode = "trunk"
        else:
            raise Reject(_UNSUPPORTED)

    def _allowed(self, interface: Interface, rest: list[str]) -> None:
        if interface.mode != "trunk":
            raise Reject("Allowed VLANs require an explicit trunk port.")
        action = rest[0]
        attributes = interface.attributes
        if action == "none" and len(rest) == 1:
            interface.trunk_vlans.clear()
            attributes.pop("trunk_vlans_complete", None)
            return
        if action in {"all", "except"}:
            # These follow later VLAN definitions, so no fixed list exists.
            attributes["trunk_vlans_complete"] = False
            raise Reject("Dynamic allowed-VLAN forms are not supported.")
        if action not in {"add", "remove"} or len(rest) != 2:
            raise Reject(_UNSUPPORTED)
        ids = _vlan_list(rest[1])
        if attributes.get("trunk_vlans_complete") is False:
            raise Reject("Allowed VLAN list is incomplete after an earlier form.")
        if action == "remove":
            if any(vlan_id not in interface.trunk_vlans for vlan_id in ids):
                raise Reject("Removing VLANs that are not allowed.", invalid=True)
            interface.trunk_vlans = [v for v in interface.trunk_vlans if v not in ids]
            return
        merged = sorted(set(interface.trunk_vlans) | set(ids))
        native = attributes.get("native_vlan")
        if native in ids:
            attributes["native_vlan"] = "none"
        interface.trunk_vlans = merged

    @staticmethod
    def _address(interface: Interface, rest: list[str], negated: bool) -> None:
        if negated and not rest:
            interface.ipv4_addresses.clear()
            interface.ipv4_address = None
            return
        if not rest or rest[0] == "dhcp":
            raise Reject("DHCP address assignment is not modelled.")
        secondary = len(rest) > 1 and rest[-1] == "secondary"
        body = rest[:-1] if secondary else rest
        match = _ADDRESS.fullmatch(body[0]) if len(body) == 1 else None
        if match is not None:
            prefix = mask_to_prefix(match.group("mask"))
            address = match.group("ip")
        elif len(body) == 2 and "." in body[1]:
            address, prefix = body[0], mask_to_prefix(body[1])
        else:
            raise Reject("Address form is not supported.")
        parsed = make_interface(address, prefix)
        if negated:
            if parsed not in interface.ipv4_addresses:
                raise Reject("Address to remove is not configured.")
            interface.ipv4_addresses.remove(parsed)
        elif secondary:
            if not interface.ipv4_addresses:
                raise Reject("Secondary address needs a primary.", invalid=True)
            interface.ipv4_addresses.append(parsed)
        else:
            interface.ipv4_addresses[:1] = [parsed]
        primary = interface.ipv4_addresses[0] if interface.ipv4_addresses else None
        interface.ipv4_address = primary.ip if primary is not None else None

    def _route(self, node: ConfigNode, tokens: list[str], negated: bool) -> None:
        if len(tokens) < 1:
            raise Reject(_UNSUPPORTED, invalid=not negated)
        network, consumed = self._network(tokens)
        rest = tokens[consumed:]
        gateway: str | None = rest[0] if rest else None
        if gateway is None and not negated:
            raise Reject("Route without a gateway is not supported.", invalid=True)
        distance: int | None = None
        if len(rest) == 2:
            if not rest[1].isdigit() or not 1 <= int(rest[1]) <= 255:
                raise Reject("Administrative distance is out of range.", invalid=True)
            distance = int(rest[1])
        elif len(rest) > 2:
            raise Reject(_UNSUPPORTED)
        is_null = gateway == "null"
        next_hop = None if gateway is None or is_null else parse_ipv4(gateway)
        routes = self.document.static_routes
        if negated:
            kept = [
                route
                for route in routes
                if not (
                    route.network == network
                    and (gateway is None or self._same_target(route, next_hop, is_null))
                    and (distance is None or route.administrative_distance == distance)
                )
            ]
            if len(kept) == len(routes):
                self.info(
                    node,
                    "NEGATION_TARGET_NOT_IN_INPUT",
                    "The configuration removed by this statement is not in the input.",
                )
            self.document.static_routes = kept
            self.parsed(node)
            return
        route = Route(
            network=network,
            protocol="static",
            next_hops=[next_hop] if next_hop is not None else [],
            administrative_distance=distance,
            attributes={"line_number": node.line_number, "null_route": is_null},
        )
        self.document.static_routes = [
            existing
            for existing in routes
            if not (
                existing.network == network
                and self._same_target(existing, next_hop, is_null)
            )
        ]
        self.document.static_routes.append(route)
        self.parsed(node)

    @staticmethod
    def _same_target(route: Route, next_hop: object, is_null: bool) -> bool:
        if is_null:
            return bool(route.attributes.get("null_route"))
        return route.next_hops == ([next_hop] if next_hop is not None else [])

    @staticmethod
    def _network(tokens: list[str]) -> tuple[IPv4Network, int]:
        match = _ADDRESS.fullmatch(tokens[0])
        if match is not None:
            prefix, consumed, address = (
                mask_to_prefix(match.group("mask")),
                1,
                match.group("ip"),
            )
        elif len(tokens) >= 2 and "." in tokens[1]:
            prefix, consumed, address = mask_to_prefix(tokens[1]), 2, tokens[0]
        else:
            raise Reject("Route destination form is not supported.", invalid=True)
        if prefix > 31:
            raise Reject("Prefix length is out of range.", invalid=True)
        return make_network(address, prefix), consumed
