"""Semantic interpretation of vendor configuration into common models.

Command syntax is taken from the H3C Comware 7 command references (ip address,
shutdown, description, ip route-static, ip vpn-instance and ip binding
vpn-instance). ``sysname`` is not covered by a retrieved official page; see
docs/vendor-config-support.md.
"""

from __future__ import annotations

import re
from ipaddress import IPv4Interface, IPv4Network

from nwconfig_parser.models import VRF, ConfigDocument, ConfigNode, Route
from nwconfig_parser.parsers.vendor_config_base import (
    ConfigInterpreter,
    Reject,
    make_interface,
    make_network,
    mask_to_prefix,
    parse_ipv4,
)

_INTERFACE_NAME = re.compile(r"^[A-Za-z][A-Za-z-]*\d[\w/.:-]*$")
_ROUTE_KEYWORDS = {
    "preference",
    "tag",
    "permanent",
    "track",
    "description",
    "bfd",
    "backup-interface",
    "public",
}


_DECIMAL = re.compile(r"[0-9]{1,10}")


def _route_distinguisher(text: str) -> str:
    """Validate the three RD formats of the Comware VPN instance view."""
    if not 3 <= len(text) <= 21 or text.count(":") != 1:
        raise Reject("Route distinguisher is malformed.", invalid=True)
    left, right = text.split(":")
    if _DECIMAL.fullmatch(right) is None:
        raise Reject("Route distinguisher is malformed.", invalid=True)
    number = int(right)
    if "." in left:
        try:
            parse_ipv4(left)
        except Reject:
            raise Reject("Route distinguisher is malformed.", invalid=True) from None
        if number > 0xFFFF:
            raise Reject("Route distinguisher number is out of range.", invalid=True)
    elif _DECIMAL.fullmatch(left) is None:
        raise Reject("Route distinguisher is malformed.", invalid=True)
    elif int(left) <= 0xFFFF:
        if number > 0xFFFFFFFF:
            raise Reject("Route distinguisher number is out of range.", invalid=True)
    elif int(left) > 0xFFFFFFFF or number > 0xFFFF:
        raise Reject("Route distinguisher is out of range.", invalid=True)
    return text


class _RouteSpec:
    def __init__(self) -> None:
        self.vrf: str | None = None
        self.next_hop_vrf: str | None = None
        self.network: IPv4Network | None = None
        self.next_hops: list[str] = []
        self.interfaces: list[str] = []
        self.preference: int | None = None
        self.attributes: dict[str, object] = {}


def _parse_route(tokens: list[str], *, require_target: bool) -> _RouteSpec:
    spec = _RouteSpec()
    index = 0
    if tokens and tokens[0] == "vpn-instance":
        if len(tokens) < 2:
            raise Reject("Incomplete static route.", invalid=True)
        spec.vrf = tokens[1]
        index = 2
    if len(tokens) - index < 2:
        raise Reject("Incomplete static route.", invalid=True)
    if "." not in tokens[index]:
        raise Reject("Static route form is not supported by this parser.")
    spec.network = make_network(tokens[index], mask_to_prefix(tokens[index + 1]))
    index += 2
    if index < len(tokens) and tokens[index] == "vpn-instance":
        if index + 1 >= len(tokens):
            raise Reject("Incomplete static route.", invalid=True)
        spec.next_hop_vrf = tokens[index + 1]
        index += 2
    if (
        index < len(tokens)
        and tokens[index] not in _ROUTE_KEYWORDS
        and "." not in tokens[index]
    ):
        name = tokens[index]
        index += 1
        if index < len(tokens) and name.isalpha() and tokens[index].isdigit():
            name += tokens[index]
            index += 1
        spec.interfaces.append(name)
    if index < len(tokens) and "." in tokens[index]:
        spec.next_hops.append(str(parse_ipv4(tokens[index])))
        index += 1
    while index < len(tokens):
        key = tokens[index]
        if key == "permanent":
            spec.attributes["permanent"] = True
            index += 1
        elif key in {"preference", "tag", "track"} and index + 1 < len(tokens):
            value = tokens[index + 1]
            if not value.isdigit():
                raise Reject("Invalid numeric value.", invalid=True)
            number = int(value)
            if key == "preference":
                if not 1 <= number <= 255:
                    raise Reject("Preference out of range.", invalid=True)
                spec.preference = number
            elif key == "tag":
                if not 1 <= number <= 4294967295:
                    raise Reject("Tag out of range.", invalid=True)
                spec.attributes["tag"] = number
            else:
                spec.attributes["track"] = number
            index += 2
        elif key == "description" and index + 1 < len(tokens):
            text = " ".join(tokens[index + 1 :])
            if not 1 <= len(text) <= 60:
                raise Reject("Description length out of range.", invalid=True)
            spec.attributes["description"] = text
            index = len(tokens)
        else:
            raise Reject("Static route option is not supported by this parser.")
    if require_target and not spec.next_hops and not spec.interfaces:
        raise Reject("Route without next hop or interface is not supported.")
    return spec


class HpeComwareConfigInterpreter(ConfigInterpreter):
    def __init__(
        self, document: ConfigDocument, filename: str | None, command: str
    ) -> None:
        super().__init__(document, filename, command)
        self._primary: dict[str, IPv4Interface] = {}
        self._vrfs: dict[str, VRF] = {}
        self._referenced: dict[str, ConfigNode] = {}

    def handle_root(self, node: ConfigNode) -> None:
        tokens = node.command.split()
        words = tokens[1:] if node.negated else tokens
        keyword = words[0].casefold() if words else ""
        if node.negated:
            if words[:2] == ["ip", "route-static"] and not node.children:
                self._route_static(node, words[2:])
                return
            raise Reject("Negated statement is not supported by this parser.")
        if keyword == "sysname" and len(words) == 2 and not node.children:
            self.document.hostname = words[1]
            self.parsed(node)
        elif keyword == "version" and not node.children:
            self.parsed(node, count=False)
            self.info(node, "HEADER_LINE_NOT_MAPPED", "Version header is not mapped.")
        elif keyword == "return" and len(words) == 1 and not node.children:
            self.parsed(node, count=False)
        elif keyword == "interface" and len(words) == 2:
            self._interface(node, words[1])
        elif words[:2] == ["ip", "vpn-instance"] and len(words) == 3:
            self._vpn_instance(node, words[2])
        elif words[:2] == ["ip", "route-static"] and not node.children:
            self._route_static(node, words[2:])
        else:
            raise Reject("Statement is not supported by this parser.")

    def finalize(self) -> None:
        super().finalize()
        for entry in self.interfaces.values():
            if entry.vrf is not None and entry.vrf in self._vrfs:
                self._vrfs[entry.vrf].interfaces.append(entry.name)
        self.document.vrfs = list(self._vrfs.values())
        for name, node in self._referenced.items():
            if name not in self._vrfs:
                self.info(
                    node,
                    "VRF_DEFINITION_NOT_IN_INPUT",
                    "A referenced VPN instance is not defined in the input.",
                )

    def _refer(self, name: str, node: ConfigNode) -> None:
        self._referenced.setdefault(name, node)

    def _vpn_instance(self, node: ConfigNode, name: str) -> None:
        vrf = self._vrfs.get(name)
        if vrf is None:
            vrf = VRF(name=name, source_reference=self.reference(node.line_number))
            self._vrfs[name] = vrf
        self.parsed(node)
        for child in node.children:
            self.run_child(child, lambda item: self._vpn_child(vrf, item))

    def _vpn_child(self, vrf: VRF, node: ConfigNode) -> None:
        text = node.command.split(None, 1)[1] if node.negated else node.command
        words = text.split(None, 1) or [""]
        if node.children:
            raise Reject("VPN instance setting is not supported by this parser.")
        if words[0] == "route-distinguisher":
            if node.negated and len(words) == 1:
                vrf.route_distinguisher = None
            elif not node.negated and len(words) == 2 and len(words[1].split()) == 1:
                vrf.route_distinguisher = _route_distinguisher(words[1])
            elif node.negated:
                raise Reject("VPN instance setting is not supported by this parser.")
            else:
                raise Reject("Route distinguisher is malformed.", invalid=True)
        elif words[0] == "description" and len(words) == 2 and not node.negated:
            if not 1 <= len(words[1]) <= 79:
                raise Reject(
                    "VPN instance description length is out of range.", invalid=True
                )
            vrf.description = words[1]
        elif words[0] == "description" and len(words) == 1 and node.negated:
            vrf.description = None
        else:
            raise Reject("VPN instance setting is not supported by this parser.")
        self.parsed(node)

    def _interface(self, node: ConfigNode, name: str) -> None:
        if _INTERFACE_NAME.fullmatch(name) is None:
            raise Reject("Interface name form is not supported.", invalid=True)
        self.interface(name)
        self.parsed(node)
        for child in node.children:
            self.run_child(child, lambda item: self._interface_child(name, item))

    def _interface_child(self, name: str, node: ConfigNode) -> None:
        entry = self.interface(name)
        tokens = node.command.split()
        words = tokens[1:] if node.negated else tokens
        keyword = words[0] if words else ""
        if node.children:
            raise Reject("Statement is not supported by this parser.")
        if keyword == "description" and not node.negated and len(tokens) >= 2:
            entry.description = node.command.split(None, 1)[1]
        elif keyword == "description" and node.negated and len(words) == 1:
            entry.description = None
        elif keyword == "shutdown" and len(words) == 1:
            entry.admin_status = "up" if node.negated else "down"
        elif words[:2] == ["ip", "address"]:
            self._address(node, entry.name, words[2:])
        elif words[:3] == ["ip", "binding", "vpn-instance"]:
            self._binding(node, name, words[3:])
        else:
            raise Reject("Interface setting is not supported by this parser.")
        self.parsed(node)

    def _address(self, node: ConfigNode, name: str, args: list[str]) -> None:
        entry = self.interface(name)
        if not args:
            if not node.negated:
                raise Reject("Address configuration is incomplete.", invalid=True)
            entry.ipv4_addresses = []
            self._primary.pop(name, None)
            self._sync_secondary(name)
            return
        secondary = args[-1] == "sub"
        core = args[:-1] if secondary else args
        if len(core) != 2 or "." not in core[0]:
            raise Reject("Address form is not supported by this parser.")
        prefix = mask_to_prefix(core[1])
        limit = 32 if name.casefold().startswith(("loopback", "inloopback")) else 31
        if core[1].isdigit() and not 1 <= prefix <= limit:
            raise Reject("Prefix length out of range.", invalid=True)
        address = make_interface(core[0], prefix)
        if node.negated:
            if address in entry.ipv4_addresses:
                entry.ipv4_addresses.remove(address)
            if self._primary.get(name) == address:
                del self._primary[name]
            self._sync_secondary(name)
            return
        if secondary:
            if address not in entry.ipv4_addresses:
                entry.ipv4_addresses.append(address)
        else:
            old = self._primary.get(name)
            if old is not None and old in entry.ipv4_addresses:
                entry.ipv4_addresses.remove(old)
            entry.ipv4_addresses.insert(0, address)
            self._primary[name] = address
        self._sync_secondary(name)

    def _sync_secondary(self, name: str) -> None:
        entry = self.interface(name)
        primary = self._primary.get(name)
        secondary = [str(a) for a in entry.ipv4_addresses if a != primary]
        if secondary:
            entry.attributes["secondary_ipv4_addresses"] = secondary
        else:
            entry.attributes.pop("secondary_ipv4_addresses", None)

    def _binding(self, node: ConfigNode, name: str, args: list[str]) -> None:
        entry = self.interface(name)
        if node.negated:
            if len(args) > 1:
                raise Reject("Statement is not supported by this parser.")
            new_vrf = None
        else:
            if len(args) != 1:
                raise Reject("Incomplete VPN binding.", invalid=True)
            new_vrf = args[0]
        # Per the Comware reference, binding or unbinding clears the address.
        if entry.ipv4_addresses:
            entry.ipv4_addresses = []
            self._primary.pop(name, None)
            self._sync_secondary(name)
            self.info(
                node,
                "ADDRESS_CLEARED_BY_VPN_BINDING",
                "Earlier interface addresses are cleared by the VPN binding change.",
            )
        entry.vrf = new_vrf
        entry.vrf_source_reference = (
            self.reference(node.line_number) if new_vrf is not None else None
        )
        if new_vrf is not None:
            self._refer(new_vrf, node)

    def _route_static(self, node: ConfigNode, tokens: list[str]) -> None:
        spec = _parse_route(tokens, require_target=not node.negated)
        assert spec.network is not None
        targets = (spec.next_hops, spec.interfaces)
        if node.negated:
            before = len(self.document.static_routes)
            self.document.static_routes = [
                route
                for route in self.document.static_routes
                if not (
                    route.network == spec.network
                    and route.vrf == spec.vrf
                    and (
                        not any(targets)
                        or (
                            [str(h) for h in route.next_hops] == spec.next_hops
                            and route.outgoing_interfaces == spec.interfaces
                        )
                    )
                )
            ]
            if len(self.document.static_routes) == before:
                self.info(
                    node,
                    "NEGATION_TARGET_NOT_IN_INPUT",
                    "The configuration removed by this statement is not in the input.",
                )
            self.parsed(node)
            return
        attributes: dict[str, object] = {
            "line_number": node.line_number,
            **spec.attributes,
        }
        if spec.next_hop_vrf is not None:
            attributes["next_hop_vrf"] = spec.next_hop_vrf
        route = Route(
            network=spec.network,
            protocol="static",
            next_hops=[parse_ipv4(h) for h in spec.next_hops],
            outgoing_interfaces=spec.interfaces,
            administrative_distance=spec.preference,
            vrf=spec.vrf,
            attributes=attributes,
        )
        self.document.static_routes = [
            existing
            for existing in self.document.static_routes
            if not (
                existing.network == route.network
                and existing.vrf == route.vrf
                and existing.next_hops == route.next_hops
                and existing.outgoing_interfaces == route.outgoing_interfaces
            )
        ]
        self.document.static_routes.append(route)
        for name in (spec.vrf, spec.next_hop_vrf):
            if name is not None:
                self._refer(name, node)
        self.parsed(node)
