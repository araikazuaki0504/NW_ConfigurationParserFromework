"""Semantic interpretation of a Cisco IOS-family configuration hierarchy.

The hierarchy builder in ``cisco_config`` only stores structure.  This module
converts recognised statements into vendor-neutral models and marks every node
as PARSED, UNSUPPORTED or INVALID so that "stored" never means "understood".
Nothing is guessed: unknown statements are preserved with their line numbers.
"""

from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import dataclass, field
from ipaddress import AddressValueError, IPv4Address, IPv4Interface, IPv4Network

from nwconfig_parser.models import (
    VLAN,
    Banner,
    BGPNetwork,
    BGPPeer,
    BGPProcess,
    ConfigDocument,
    ConfigNode,
    ConfigReference,
    Interface,
    IssueSeverity,
    OSPFProcess,
    ParseIssue,
    PrefixList,
    PrefixListEntry,
    Route,
    RouteMap,
    RouteMapEntry,
    SemanticStatus,
    SourceReference,
    UnsupportedConfigLine,
)

_PREFIX_LINE = re.compile(
    r"^ip\s+prefix-list\s+(?P<name>\S+)"
    r"(?:\s+seq\s+(?P<sequence>\d+))?\s+"
    r"(?P<action>permit|deny)\s+(?P<prefix>\S+)"
    r"(?:\s+ge\s+(?P<ge>\d+))?"
    r"(?:\s+le\s+(?P<le>\d+))?$",
    re.IGNORECASE,
)
_PREFIX_NAME = re.compile(r"^ip\s+prefix-list\s+(?P<name>\S+)", re.IGNORECASE)
_NO_PREFIX_ENTRY = re.compile(
    r"^(?:seq\s+(?P<sequence>\d+))?\s*"
    r"(?:(?P<action>permit|deny)\s+(?P<prefix>\S+)"
    r"(?:\s+ge\s+(?P<ge>\d+))?(?:\s+le\s+(?P<le>\d+))?)?$",
    re.IGNORECASE,
)
_PREFIX_DESCRIPTION = re.compile(
    r"^ip\s+prefix-list\s+(?P<name>\S+)\s+description\s+(?P<text>.+)$", re.IGNORECASE
)
_HOSTNAME_LINE = re.compile(r"^hostname\s+(?P<hostname>\S+)\s*$", re.IGNORECASE)
_ROUTE_MAP_HEADER = re.compile(
    r"^route-map\s+(?P<name>\S+)\s+(?P<action>permit|deny)\s+(?P<sequence>\d+)$",
    re.IGNORECASE,
)
_INTERFACE_NAME = re.compile(r"^[A-Za-z][A-Za-z-]*\d[\w/.:-]*$")
_VLAN_LIST = re.compile(r"^\d+(-\d+)?(,\d+(-\d+)?)*$")


class _Reject(Exception):
    """A statement is recognised but cannot be represented safely."""

    def __init__(self, reason: str, *, invalid: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.invalid = invalid


def mask_to_prefix(mask: str, *, wildcard: bool = False) -> int:
    """Convert a contiguous netmask or wildcard to a prefix length."""
    try:
        value = int(IPv4Address(mask))
    except AddressValueError as exc:
        raise _Reject(f"Invalid mask {mask!r}", invalid=True) from exc
    if wildcard:
        value ^= 0xFFFFFFFF
    prefix = bin(value).count("1")
    if value != (((1 << prefix) - 1) << (32 - prefix)) & 0xFFFFFFFF:
        raise _Reject(f"Non-contiguous mask {mask!r}", invalid=True)
    return prefix


def _address(value: str) -> IPv4Address:
    try:
        return IPv4Address(value)
    except AddressValueError as exc:
        raise _Reject(f"Invalid IPv4 address {value!r}", invalid=True) from exc


def _integer(value: str, low: int, high: int, what: str) -> int:
    if not value.isdigit() or not low <= int(value) <= high:
        raise _Reject(f"Invalid {what} {value!r}", invalid=True)
    return int(value)


def _vlan_list(value: str) -> set[int]:
    if _VLAN_LIST.fullmatch(value) is None:
        raise _Reject(f"Unsupported VLAN list {value!r}")
    vlans: set[int] = set()
    for part in value.split(","):
        low_text, _, high_text = part.partition("-")
        low = _integer(low_text, 1, 4094, "VLAN ID")
        high = _integer(high_text, 1, 4094, "VLAN ID") if high_text else low
        if low > high:
            raise _Reject(f"Descending VLAN range {part!r}", invalid=True)
        vlans.update(range(low, high + 1))
    return vlans


def _split(node: ConfigNode) -> tuple[bool, list[str]]:
    tokens = node.command.split()
    if tokens and tokens[0].casefold() == "no":
        return True, tokens[1:]
    return False, tokens


@dataclass(slots=True)
class _TrunkState:
    kind: str = "unspecified"  # unspecified | all | list | all_except
    vlans: set[int] = field(default_factory=set)


@dataclass(slots=True)
class _EntryBuilder:
    action: str
    reference: SourceReference
    matches: list[str] = field(default_factory=list)
    sets: list[str] = field(default_factory=list)
    prefix_lists: list[str] = field(default_factory=list)
    acls: list[str] = field(default_factory=list)


class CiscoConfigInterpreter:
    def __init__(self, document: ConfigDocument, filename: str | None, command: str):
        self.document = document
        self.filename = filename
        self.command = command
        self.issues: list[ParseIssue] = []
        self.unparsed: list[SourceReference] = []
        self._prefix_lists: OrderedDict[str, PrefixList] = OrderedDict()
        self._interfaces: OrderedDict[str, Interface] = OrderedDict()
        self._interface_trunks: dict[str, _TrunkState] = {}
        self._vlans: OrderedDict[int, VLAN] = OrderedDict()
        self._ospf: OrderedDict[tuple[str, str | None], OSPFProcess] = OrderedDict()
        self._bgp: BGPProcess | None = None
        self._banners: list[Banner] = []
        self._route_maps: OrderedDict[str, dict[int, _EntryBuilder]] = OrderedDict()
        self._route_map_state: dict[str, RouteMap] = {}
        self.parsed_count = 0

    # ------------------------------------------------------------------ utils
    def _reference(self, start: int, end: int | None = None) -> SourceReference:
        return SourceReference(
            filename=self.filename,
            command=self.command,
            start_line=start,
            end_line=end if end is not None else start,
        )

    @staticmethod
    def _last_line(node: ConfigNode) -> int:
        return max(
            [node.line_number, node.end_line or 0]
            + [max(c.line_number, c.end_line or 0) for c in _walk(node)]
        )

    def _parsed(self, node: ConfigNode) -> None:
        node.semantic_status = SemanticStatus.PARSED
        self.parsed_count += 1

    def _reject(
        self,
        node: ConfigNode,
        reason: str,
        *,
        invalid: bool = False,
        parent: ConfigNode | None = None,
        code: str | None = None,
        severity: IssueSeverity | None = None,
        record_issue: bool = True,
    ) -> None:
        status = SemanticStatus.INVALID if invalid else SemanticStatus.UNSUPPORTED
        node.semantic_status = status
        parent_text = parent.command if parent is not None else None
        self.document.unsupported_lines.append(
            UnsupportedConfigLine(
                line_number=node.line_number,
                raw_text=node.raw_text,
                reason=reason,
                parent=parent_text,
            )
        )
        for child in node.children:
            self._reject(
                child,
                f"Inside unsupported block: {reason}",
                invalid=invalid,
                parent=node,
                record_issue=False,
            )
        if not record_issue:
            return
        end = self._last_line(node)
        reference = self._reference(node.line_number, end)
        self.unparsed.append(reference)
        self.issues.append(
            ParseIssue(
                severity=severity
                or (IssueSeverity.ERROR if invalid else IssueSeverity.WARNING),
                code=code
                or ("INVALID_CONFIG_STATEMENT" if invalid else "UNSUPPORTED_CONFIG"),
                message=reason,
                line_number=node.line_number,
                source_reference=reference,
            )
        )

    def _info(self, node: ConfigNode, code: str, message: str) -> None:
        self.issues.append(
            ParseIssue(
                severity=IssueSeverity.INFO,
                code=code,
                message=message,
                line_number=node.line_number,
                source_reference=self._reference(node.line_number),
            )
        )

    def _run_child(
        self,
        parent: ConfigNode,
        child: ConfigNode,
        handler: object,
    ) -> None:
        """Run a child handler, converting rejections into preserved lines."""
        assert callable(handler)
        try:
            handler(child)
        except _Reject as rejection:
            self._reject(child, rejection.reason, invalid=rejection.invalid)

    # ------------------------------------------------------------------- main
    def run(self) -> None:
        for node in self.document.roots:
            try:
                self._top_level(node)
            except _Reject as rejection:
                self._reject(node, rejection.reason, invalid=rejection.invalid)
        self.document.prefix_lists = list(self._prefix_lists.values())
        self.document.banners = list(self._banners)
        self.document.interfaces = list(self._interfaces.values())
        for name, trunk in self._interface_trunks.items():
            self._finish_trunk(self._interfaces[name], trunk)
        self.document.vlans = sorted(self._vlans.values(), key=lambda v: v.vlan_id)
        self.document.ospf_processes = list(self._ospf.values())
        if self._bgp is not None:
            self.document.bgp_processes = [self._bgp]
        self._finish_route_maps()
        self._build_references()

    def _top_level(self, node: ConfigNode) -> None:
        negated, tokens = _split(node)
        words = [token.casefold() for token in tokens]
        if not tokens:
            raise _Reject("Empty statement")
        if not negated and words[0] == "hostname":
            self._hostname(node)
        elif not negated and words[0] == "end" and len(words) == 1:
            self._parsed(node)
        elif not negated and words[0] == "interface":
            self._interface(node, tokens)
        elif not negated and words[0] == "vlan":
            self._vlan(node, tokens)
        elif words[:2] == ["ip", "routing"] and len(words) == 2:
            self.document.ip_routing = not negated
            self._parsed(node)
        elif words[:2] == ["ip", "route"]:
            self._static_route(node, negated, tokens[2:])
        elif not negated and words[:2] == ["router", "ospf"]:
            self._ospf_block(node, tokens)
        elif not negated and words[:2] == ["router", "bgp"]:
            self._bgp_block(node, tokens)
        elif words[0] == "banner":
            self._banner(node, negated, tokens)
        elif not negated and words[:3] == ["macro", "name"] and node.body_lines:
            self._macro(node)
        elif words[:2] == ["ip", "prefix-list"]:
            if negated:
                self._no_prefix_list(node)
            else:
                self._prefix_list(node)
        elif not negated and words[0] == "route-map":
            self._route_map(node)
        else:
            raise _Reject("No semantic handler for this statement")

    # --------------------------------------------------------------- hostname
    def _hostname(self, node: ConfigNode) -> None:
        match = _HOSTNAME_LINE.fullmatch(node.command)
        if match is None:
            raise _Reject("hostname requires exactly one name", invalid=True)
        self.document.hostname = match.group("hostname")
        self._parsed(node)

    # -------------------------------------------------------------- interface
    def _interface(self, node: ConfigNode, tokens: list[str]) -> None:
        if len(tokens) != 2 or _INTERFACE_NAME.fullmatch(tokens[1]) is None:
            raise _Reject("Unsupported interface designation (range or spaced name)")
        name = tokens[1]
        key = name.casefold()
        interface = self._interfaces.setdefault(key, Interface(name=name))
        trunk = self._interface_trunks.setdefault(key, _TrunkState())
        interface.attributes.setdefault("source_lines", []).append(node.line_number)
        self._parsed(node)
        for child in node.children:
            try:
                self._interface_child(interface, trunk, child)
            except _Reject as rejection:
                self._reject(
                    child, rejection.reason, invalid=rejection.invalid, parent=node
                )

    def _interface_child(
        self, interface: Interface, trunk: _TrunkState, node: ConfigNode
    ) -> None:
        if node.children:
            raise _Reject("Nested block inside interface is not supported")
        negated, tokens = _split(node)
        words = [token.casefold() for token in tokens]
        if not tokens:
            raise _Reject("Empty statement")
        if words[0] == "description":
            if negated:
                interface.description = None
            else:
                text = node.command.split(None, 1)[1] if len(tokens) > 1 else ""
                if not text:
                    raise _Reject("description requires text", invalid=True)
                interface.description = text
        elif words == ["shutdown"]:
            interface.admin_status = "down" if not negated else "up"
            history = interface.attributes.setdefault("admin_status_history", [])
            history.append(
                {
                    "line": node.line_number,
                    "statement": "no shutdown" if negated else "shutdown",
                }
            )
        elif words[:2] == ["ip", "address"]:
            self._interface_ip(interface, negated, tokens[2:])
        elif words == ["switchport"]:
            if negated:
                interface.mode = "routed"
            elif interface.mode == "routed":
                interface.mode = None
        elif words[:2] == ["switchport", "mode"]:
            if negated and len(words) == 2:
                interface.mode = None
            elif not negated and len(words) == 3 and words[2] in {"access", "trunk"}:
                interface.mode = words[2]
            else:
                raise _Reject("Unsupported switchport mode")
        elif words[:3] == ["switchport", "access", "vlan"]:
            if negated and len(words) == 3:
                interface.access_vlan = None
            elif not negated and len(words) == 4:
                interface.access_vlan = _integer(tokens[3], 1, 4094, "VLAN ID")
            else:
                raise _Reject("Unsupported switchport access vlan syntax")
        elif words[:3] == ["switchport", "trunk", "native"] and words[3:4] == ["vlan"]:
            if negated and len(words) == 4:
                interface.attributes.pop("native_vlan", None)
            elif not negated and len(words) == 5:
                interface.attributes["native_vlan"] = _integer(
                    tokens[4], 1, 4094, "VLAN ID"
                )
            else:
                raise _Reject("Unsupported switchport trunk native vlan syntax")
        elif words[:4] == ["switchport", "trunk", "allowed", "vlan"]:
            self._trunk_allowed(trunk, negated, tokens[4:])
        else:
            raise _Reject("No semantic handler for this interface statement")
        self._parsed(node)

    @staticmethod
    def _interface_ip(interface: Interface, negated: bool, args: list[str]) -> None:
        secondaries: list[IPv4Interface] = interface.attributes.setdefault(
            "secondary_addresses", []
        )
        if negated and not args:
            interface.ipv4_addresses.clear()
            secondaries.clear()
        else:
            is_secondary = len(args) == 3 and args[2].casefold() == "secondary"
            if len(args) not in {2, 3} or (len(args) == 3 and not is_secondary):
                raise _Reject("Unsupported ip address syntax (e.g. dhcp, negotiated)")
            try:
                prefix = mask_to_prefix(args[1])
                address = IPv4Interface(f"{_address(args[0])}/{prefix}")
            except ValueError as exc:
                raise _Reject(f"Invalid ip address: {exc}", invalid=True) from exc
            if negated:
                if address in interface.ipv4_addresses:
                    interface.ipv4_addresses.remove(address)
                if address in secondaries:
                    secondaries.remove(address)
            elif is_secondary:
                if address not in interface.ipv4_addresses:
                    interface.ipv4_addresses.append(address)
                    secondaries.append(address)
            else:
                # A new primary address replaces the previous primary one.
                interface.ipv4_addresses = [
                    a for a in interface.ipv4_addresses if a in secondaries
                ]
                interface.ipv4_addresses.insert(0, address)
        primaries = [a for a in interface.ipv4_addresses if a not in secondaries]
        interface.ipv4_address = primaries[0].ip if primaries else None

    @staticmethod
    def _trunk_allowed(trunk: _TrunkState, negated: bool, args: list[str]) -> None:
        if negated:
            if args:
                raise _Reject("Unsupported 'no switchport trunk allowed vlan' syntax")
            trunk.kind, trunk.vlans = "unspecified", set()
            return
        words = [arg.casefold() for arg in args]
        if words == ["all"]:
            trunk.kind, trunk.vlans = "all", set()
        elif words == ["none"]:
            trunk.kind, trunk.vlans = "list", set()
        elif len(args) == 1:
            trunk.kind, trunk.vlans = "list", _vlan_list(args[0])
        elif len(args) == 2 and words[0] in {"add", "remove", "except"}:
            vlans = _vlan_list(args[1])
            if words[0] == "except":
                trunk.kind, trunk.vlans = "all_except", vlans
            elif words[0] == "add":
                if trunk.kind == "list":
                    trunk.vlans |= vlans
                elif trunk.kind == "all_except":
                    trunk.vlans -= vlans
                else:
                    trunk.kind, trunk.vlans = "all", set()
            elif trunk.kind == "list":
                trunk.vlans -= vlans
            elif trunk.kind == "all_except":
                trunk.vlans |= vlans
            else:
                trunk.kind, trunk.vlans = "all_except", vlans
        else:
            raise _Reject("Unsupported switchport trunk allowed vlan syntax")

    @staticmethod
    def _finish_trunk(interface: Interface, trunk: _TrunkState) -> None:
        if trunk.kind == "unspecified":
            return
        interface.attributes["trunk_allowed_state"] = trunk.kind
        if trunk.kind == "list":
            interface.trunk_vlans = sorted(trunk.vlans)
        elif trunk.kind == "all_except":
            interface.attributes["trunk_excluded_vlans"] = sorted(trunk.vlans)

    # ------------------------------------------------------------------- vlan
    def _vlan(self, node: ConfigNode, tokens: list[str]) -> None:
        if len(tokens) != 2 or not tokens[1].isdigit():
            raise _Reject("Unsupported vlan statement (list, range or name)")
        vlan_id = _integer(tokens[1], 1, 4094, "VLAN ID")
        vlan = self._vlans.setdefault(vlan_id, VLAN(vlan_id=vlan_id))
        self._parsed(node)
        for child in node.children:
            try:
                negated, parts = _split(child)
                if child.children:
                    raise _Reject("Nested block inside vlan is not supported")
                if parts and parts[0].casefold() == "name" and not negated:
                    if len(parts) < 2:
                        raise _Reject("vlan name requires a value", invalid=True)
                    vlan.name = child.command.split(None, 1)[1]
                    self._parsed(child)
                else:
                    raise _Reject("No semantic handler for this vlan statement")
            except _Reject as rejection:
                self._reject(
                    child, rejection.reason, invalid=rejection.invalid, parent=node
                )

    # ----------------------------------------------------------- static route
    def _static_route(self, node: ConfigNode, negated: bool, args: list[str]) -> None:
        if node.children:
            raise _Reject("Nested block inside ip route is not supported")
        vrf: str | None = None
        if args[:1] and args[0].casefold() == "vrf":
            if len(args) < 2:
                raise _Reject("ip route vrf requires a name", invalid=True)
            vrf, args = args[1], args[2:]
        if len(args) < 2:
            raise _Reject("ip route requires prefix and mask", invalid=True)
        try:
            network = IPv4Network(
                f"{_address(args[0])}/{mask_to_prefix(args[1])}", strict=True
            )
        except ValueError as exc:
            raise _Reject(f"Invalid ip route prefix: {exc}", invalid=True) from exc
        rest = args[2:]
        next_hops: list[IPv4Address] = []
        interfaces: list[str] = []
        if rest and _INTERFACE_NAME.fullmatch(rest[0]) and not _is_ipv4(rest[0]):
            interfaces.append(rest.pop(0))
        if rest and _is_ipv4(rest[0]):
            next_hops.append(IPv4Address(rest.pop(0)))
        distance: int | None = None
        attributes: dict[str, object] = {}
        if rest and rest[0].isdigit():
            distance = _integer(rest.pop(0), 1, 255, "administrative distance")
        while rest:
            option = rest.pop(0).casefold()
            if option == "permanent":
                attributes["permanent"] = True
            elif option in {"tag", "name"} and rest:
                value = rest.pop(0)
                attributes[option] = (
                    _integer(value, 1, 4294967295, "tag") if option == "tag" else value
                )
            else:
                raise _Reject(f"Unsupported ip route option {option!r}")
        if not negated and not next_hops and not interfaces:
            raise _Reject("ip route requires a next hop or interface")
        routes = self.document.static_routes
        if negated:
            kept = [
                r
                for r in routes
                if not (
                    r.network == network
                    and r.vrf == vrf
                    and (
                        (not next_hops and not interfaces)
                        or (
                            r.next_hops == next_hops
                            and r.outgoing_interfaces == interfaces
                        )
                    )
                )
            ]
            if len(kept) == len(routes):
                self._info(node, "NO_MATCHING_ROUTE", "No earlier ip route matched.")
            self.document.static_routes = kept
        else:
            attributes["line_number"] = node.line_number
            attributes["source_reference"] = self._reference(node.line_number)
            duplicate = any(
                r.network == network
                and r.vrf == vrf
                and r.next_hops == next_hops
                and r.outgoing_interfaces == interfaces
                for r in routes
            )
            if not duplicate:
                routes.append(
                    Route(
                        network=network,
                        protocol="static",
                        next_hops=next_hops,
                        outgoing_interfaces=interfaces,
                        administrative_distance=distance,
                        vrf=vrf,
                        attributes=attributes,
                    )
                )
        self._parsed(node)

    # ------------------------------------------------------------------- ospf
    def _ospf_block(self, node: ConfigNode, tokens: list[str]) -> None:
        vrf: str | None = None
        if len(tokens) == 5 and tokens[3].casefold() == "vrf":
            vrf = tokens[4]
        elif len(tokens) != 3:
            raise _Reject("Unsupported router ospf syntax")
        process_id = str(_integer(tokens[2], 1, 65535, "OSPF process ID"))
        process = self._ospf.setdefault(
            (process_id, vrf), OSPFProcess(process_id=process_id)
        )
        process.attributes.setdefault("vrf", vrf)
        statements = process.attributes.setdefault("network_statements", [])
        passive = process.attributes.setdefault("passive_interfaces", [])
        active = process.attributes.setdefault("non_passive_interfaces", [])
        process.attributes.setdefault("passive_interface_default", False)
        self._parsed(node)
        for child in node.children:
            try:
                self._ospf_child(process, statements, passive, active, child)
            except _Reject as rejection:
                self._reject(
                    child, rejection.reason, invalid=rejection.invalid, parent=node
                )

    def _ospf_child(
        self,
        process: OSPFProcess,
        statements: list[dict[str, object]],
        passive: list[str],
        active: list[str],
        node: ConfigNode,
    ) -> None:
        if node.children:
            raise _Reject("Nested block inside router ospf is not supported")
        negated, tokens = _split(node)
        words = [token.casefold() for token in tokens]
        if words[:1] == ["router-id"] and not negated and len(tokens) == 2:
            process.router_id = _address(tokens[1])
        elif words[:1] == ["network"] and len(tokens) == 5 and words[3] == "area":
            prefix = mask_to_prefix(tokens[2], wildcard=True)
            network = IPv4Network(f"{_address(tokens[1])}/{prefix}", strict=False)
            area = tokens[4]
            if not (area.isdigit() or _is_ipv4(area)):
                raise _Reject(f"Invalid OSPF area {area!r}", invalid=True)
            statement = {
                "network": str(network),
                "wildcard": tokens[2],
                "area": area,
                "line": node.line_number,
            }
            if negated:
                before = len(statements)
                statements[:] = [
                    s
                    for s in statements
                    if (s["network"], s["area"]) != (statement["network"], area)
                ]
                if len(statements) == before:
                    self._info(node, "NO_MATCHING_NETWORK", "No earlier network.")
            elif not any(
                (s["network"], s["area"]) == (statement["network"], area)
                for s in statements
            ):
                statements.append(statement)
            process.networks = [str(s["network"]) for s in statements]
            process.areas = list(dict.fromkeys(str(s["area"]) for s in statements))
        elif words[:1] == ["passive-interface"] and len(tokens) == 2:
            target = tokens[1]
            if words[1] == "default":
                process.attributes["passive_interface_default"] = not negated
                passive.clear()
                active.clear()
            elif _INTERFACE_NAME.fullmatch(target) is None:
                raise _Reject("Unsupported passive-interface target")
            elif negated:
                _discard(passive, target)
                if target not in active:
                    active.append(target)
            else:
                _discard(active, target)
                if target not in passive:
                    passive.append(target)
        else:
            raise _Reject("No semantic handler for this router ospf statement")
        self._parsed(node)

    # -------------------------------------------------------------------- bgp
    def _bgp_block(self, node: ConfigNode, tokens: list[str]) -> None:
        if len(tokens) != 3 or not tokens[2].isdigit():
            raise _Reject("Unsupported router bgp AS number format (e.g. asdot)")
        asn = _integer(tokens[2], 1, 4294967295, "AS number")
        if self._bgp is not None and self._bgp.asn != asn:
            raise _Reject(
                f"Second BGP process AS {asn} conflicts with AS {self._bgp.asn}",
                invalid=True,
            )
        bgp = self._bgp or BGPProcess(asn=asn)
        self._bgp = bgp
        self._parsed(node)
        for child in node.children:
            self._bgp_child(bgp, node, child, scope=None)

    def _bgp_child(
        self, bgp: BGPProcess, parent: ConfigNode, node: ConfigNode, scope: str | None
    ) -> None:
        try:
            self._bgp_statement(bgp, node, scope)
        except _Reject as rejection:
            self._reject(
                node, rejection.reason, invalid=rejection.invalid, parent=parent
            )

    def _bgp_statement(
        self, bgp: BGPProcess, node: ConfigNode, scope: str | None
    ) -> None:
        negated, tokens = _split(node)
        words = [token.casefold() for token in tokens]
        if not tokens:
            raise _Reject("Empty statement")
        if words == ["exit-address-family"] and not negated:
            self._parsed(node)
            return
        if words[0] == "address-family" and not negated:
            if scope is not None:
                raise _Reject("Nested address-family is not supported")
            if words[1:] not in (["ipv4"], ["ipv4", "unicast"]):
                raise _Reject(
                    "Only 'address-family ipv4 [unicast]' is supported", invalid=False
                )
            if "ipv4" not in bgp.address_families:
                bgp.address_families.append("ipv4")
            self._parsed(node)
            for child in node.children:
                self._bgp_child(bgp, node, child, scope="ipv4")
            return
        if node.children:
            raise _Reject("Nested block inside router bgp is not supported")
        family = scope or "global"
        if words[:2] == ["bgp", "router-id"] and not negated and len(tokens) == 3:
            bgp.router_id = _address(tokens[2])
        elif words[0] == "network":
            self._bgp_network(bgp, node, negated, tokens, family)
        elif words[0] == "neighbor" and len(tokens) >= 3:
            self._bgp_neighbor(bgp, node, negated, tokens, family)
        else:
            raise _Reject("No semantic handler for this router bgp statement")
        self._parsed(node)

    def _bgp_network(
        self,
        bgp: BGPProcess,
        node: ConfigNode,
        negated: bool,
        tokens: list[str],
        family: str,
    ) -> None:
        if len(tokens) < 4 or tokens[2].casefold() != "mask":
            raise _Reject("network without explicit mask is not guessed")
        try:
            network = IPv4Network(
                f"{_address(tokens[1])}/{mask_to_prefix(tokens[3])}", strict=True
            )
        except ValueError as exc:
            raise _Reject(f"Invalid network: {exc}", invalid=True) from exc
        route_map: str | None = None
        extra = tokens[4:]
        if extra:
            if len(extra) == 2 and extra[0].casefold() == "route-map":
                route_map = extra[1]
            else:
                raise _Reject("Unsupported network options")
        address_family = "ipv4"
        if negated:
            before = len(bgp.networks)
            bgp.networks = [
                n
                for n in bgp.networks
                if not (n.network == network and n.address_family == address_family)
            ]
            if len(bgp.networks) == before:
                self._info(node, "NO_MATCHING_NETWORK", "No earlier network matched.")
        else:
            bgp.networks = [
                n
                for n in bgp.networks
                if not (n.network == network and n.address_family == address_family)
            ]
            bgp.networks.append(
                BGPNetwork(
                    network=network,
                    address_family=address_family,
                    route_map=route_map,
                    line_number=node.line_number,
                )
            )
        if family == "global":
            bgp.attributes.setdefault("global_network_lines", []).append(
                node.line_number
            )

    def _bgp_neighbor(
        self,
        bgp: BGPProcess,
        node: ConfigNode,
        negated: bool,
        tokens: list[str],
        family: str,
    ) -> None:
        if not _is_ipv4(tokens[1]):
            raise _Reject("Only IPv4 neighbor addresses are supported (no peer-groups)")
        address = IPv4Address(tokens[1])
        peer = next((p for p in bgp.peers if p.neighbor_address == address), None)
        if peer is None:
            peer = BGPPeer(neighbor_address=address, local_as=bgp.asn)
            bgp.peers.append(peer)
        option = tokens[2].casefold()
        args = tokens[3:]
        if option == "remote-as" and not negated and len(args) == 1:
            peer.remote_as = _integer(args[0], 1, 4294967295, "AS number")
        elif option == "description" and not negated and args:
            peer.description = node.command.split(None, 3)[3]
        elif option == "shutdown" and not args:
            peer.attributes.setdefault("shutdown_history", []).append(
                {
                    "line": node.line_number,
                    "statement": "no shutdown" if negated else "shutdown",
                }
            )
            peer.attributes["shutdown"] = not negated
        elif option == "activate" and not args:
            active = peer.attributes.setdefault("activated_address_families", [])
            target = "ipv4"
            if negated:
                _discard(active, target)
            elif target not in active:
                active.append(target)
        elif option in {"route-map", "prefix-list"} and len(args) == 2:
            direction = args[1].casefold()
            if direction not in {"in", "out"}:
                raise _Reject(f"Invalid direction {args[1]!r}", invalid=True)
            attribute = f"{option.replace('-', '_')}_{direction}"
            current = getattr(peer, attribute)
            if negated:
                if current == args[0]:
                    setattr(peer, attribute, None)
                else:
                    self._info(node, "NO_MATCHING_POLICY", "No matching binding.")
            else:
                setattr(peer, attribute, args[0])
            bindings = peer.attributes.setdefault("policy_bindings", [])
            bindings[:] = [
                b
                for b in bindings
                if (b["type"], b["direction"]) != (option, direction)
            ]
            if not negated:
                bindings.append(
                    {
                        "type": option,
                        "direction": direction,
                        "name": args[0],
                        "address_family": family,
                        "line": node.line_number,
                    }
                )
        else:
            raise _Reject("No semantic handler for this neighbor statement")

    # ------------------------------------------------------ banner and macro
    def _banner(self, node: ConfigNode, negated: bool, tokens: list[str]) -> None:
        if negated:
            if len(tokens) != 2:
                raise _Reject("Unsupported 'no banner' form")
            kind = tokens[1].casefold()
            self._banners = [b for b in self._banners if b.kind.casefold() != kind]
            self._parsed(node)
            return
        if node.end_line is None:
            raise _Reject("banner without a delimiter and text", invalid=True)
        if not node.terminated:
            raise _Reject(
                "Banner is missing its closing delimiter; the remaining lines "
                "were consumed as banner text.",
                invalid=True,
            )
        kind = tokens[1]
        self._banners = [
            b for b in self._banners if b.kind.casefold() != kind.casefold()
        ]
        self._banners.append(
            Banner(
                kind=kind,
                text="\n".join(node.body_lines),
                source_reference=self._reference(node.line_number, node.end_line),
            )
        )
        self._parsed(node)

    def _macro(self, node: ConfigNode) -> None:
        if not node.terminated:
            raise _Reject("Macro is missing its closing '@' line.", invalid=True)
        raise _Reject("Macro bodies are preserved but not interpreted")

    # ------------------------------------------------------------ prefix-list
    @staticmethod
    def _prefix_fields(
        prefix_text: str, ge_text: str | None, le_text: str | None
    ) -> tuple[IPv4Network, int | None, int | None]:
        prefix = IPv4Network(prefix_text, strict=True)
        ge = int(ge_text) if ge_text else None
        le = int(le_text) if le_text else None
        if ge is not None and not prefix.prefixlen <= ge <= 32:
            raise ValueError("ge must be between the prefix length and 32")
        if le is not None and not prefix.prefixlen <= le <= 32:
            raise ValueError("le must be between the prefix length and 32")
        if ge is not None and le is not None and ge > le:
            raise ValueError("ge cannot be greater than le")
        return prefix, ge, le

    def _no_prefix_list(self, node: ConfigNode) -> None:
        """Apply 'no ip prefix-list ...' in file order against parsed state.

        Removal is applied only when the target is found in the state built so
        far. A missing or ambiguous target is never assumed away: the list is
        marked incomplete and the line is reported.
        """
        body = node.command.split(None, 1)[1]
        name_match = _PREFIX_NAME.match(body)
        if name_match is None:
            raise _Reject("Unsupported 'no ip prefix-list' form")
        name = name_match.group("name")
        if name.casefold() == "sequence-number":
            raise _Reject("'ip prefix-list sequence-number' is not interpreted")
        rest = body[name_match.end() :].strip()
        words = rest.split()
        if not rest:
            if self._prefix_lists.pop(name, None) is None:
                self._info(
                    node,
                    "PREFIX_LIST_NOT_IN_PARSED_STATE",
                    f"Prefix-list {name} was not defined earlier in this input.",
                )
            self._parsed(node)
            return
        if words[0].casefold() == "description":
            plist = self._prefix_lists.get(name)
            if plist is not None:
                plist.description = None
            self._parsed(node)
            return
        match = _NO_PREFIX_ENTRY.fullmatch(rest)
        if match is None or (not match.group("sequence") and not match.group("action")):
            self._taint_prefix_list(name_match, node.line_number, "unsupported removal")
            self._reject(
                node,
                "'no ip prefix-list' form is not supported.",
                code="UNSUPPORTED_PREFIX_LIST_SYNTAX",
                severity=IssueSeverity.ERROR,
            )
            return
        wanted: tuple[IPv4Network, int | None, int | None] | None = None
        if match.group("action"):
            try:
                wanted = self._prefix_fields(
                    match.group("prefix"), match.group("ge"), match.group("le")
                )
            except ValueError as exc:
                self._taint_prefix_list(name_match, node.line_number, str(exc))
                self._reject(
                    node,
                    f"Invalid prefix-list removal: {exc}",
                    invalid=True,
                    code="INVALID_PREFIX_LIST_ENTRY",
                )
                return
        plist = self._prefix_lists.get(name)
        candidates = [
            entry
            for entry in (plist.entries if plist else [])
            if self._removal_matches(entry, match, wanted)
        ]
        if plist is None or len(candidates) != 1:
            why = (
                "target entry not found in parsed state"
                if not candidates
                else "removal matches several entries"
            )
            self._taint_prefix_list(name_match, node.line_number, why)
            self._reject(
                node,
                f"Prefix-list removal not applied: {why}.",
                code="PREFIX_LIST_REMOVAL_UNRESOLVED",
            )
            return
        plist.entries.remove(candidates[0])
        self._parsed(node)

    @staticmethod
    def _removal_matches(
        entry: PrefixListEntry,
        match: re.Match[str],
        wanted: tuple[IPv4Network, int | None, int | None] | None,
    ) -> bool:
        if match.group("sequence"):
            if entry.sequence != int(match.group("sequence")):
                return False
        if wanted is not None:
            prefix, ge, le = wanted
            return (
                entry.action == match.group("action").lower()
                and entry.prefix == prefix
                and entry.ge == ge
                and entry.le == le
            )
        return True

    def _prefix_list(self, node: ConfigNode) -> None:
        command = node.command
        description = _PREFIX_DESCRIPTION.fullmatch(command)
        name_match = _PREFIX_NAME.match(command)
        if description is not None:
            plist = self._prefix_lists.setdefault(
                description.group("name"), PrefixList(name=description.group("name"))
            )
            plist.description = description.group("text").strip()
            self._parsed(node)
            return
        match = _PREFIX_LINE.fullmatch(command)
        if match is None:
            self._taint_prefix_list(name_match, node.line_number, "unsupported syntax")
            self._reject(
                node,
                "Prefix-list line does not match the supported Cisco syntax.",
                code="UNSUPPORTED_PREFIX_LIST_SYNTAX",
                severity=IssueSeverity.ERROR,
            )
            return
        try:
            prefix, ge, le = self._prefix_fields(
                match.group("prefix"), match.group("ge"), match.group("le")
            )
        except ValueError as exc:
            self._taint_prefix_list(name_match, node.line_number, str(exc))
            self._reject(
                node,
                f"Invalid prefix-list entry: {exc}",
                invalid=True,
                code="INVALID_PREFIX_LIST_ENTRY",
            )
            return
        name = match.group("name")
        plist = self._prefix_lists.setdefault(name, PrefixList(name=name))
        sequence = int(match.group("sequence")) if match.group("sequence") else None
        if sequence is not None and any(e.sequence == sequence for e in plist.entries):
            plist.complete = False
            plist.incomplete_reasons.append(
                f"duplicate sequence {sequence} at line {node.line_number}"
            )
            self._reject(
                node,
                f"Duplicate prefix-list sequence {sequence}; device behaviour "
                "is not assumed.",
                invalid=True,
                code="DUPLICATE_PREFIX_LIST_SEQUENCE",
            )
            return
        plist.entries.append(
            PrefixListEntry(
                sequence=sequence,
                action=match.group("action").lower(),
                prefix=prefix,
                ge=ge,
                le=le,
                source_reference=self._reference(node.line_number),
            )
        )
        self._parsed(node)

    def _taint_prefix_list(
        self, match: re.Match[str] | None, line: int, why: str
    ) -> None:
        if match is None:
            return
        name = match.group("name")
        plist = self._prefix_lists.setdefault(name, PrefixList(name=name))
        plist.complete = False
        plist.incomplete_reasons.append(f"line {line}: {why}")

    # -------------------------------------------------------------- route-map
    def _route_map(self, node: ConfigNode) -> None:
        tokens = node.command.split()
        name = tokens[1] if len(tokens) > 1 else None
        match = _ROUTE_MAP_HEADER.fullmatch(node.command)
        if match is None:
            if name is not None:
                self._taint_route_map(name, node.line_number, "unsupported header")
            raise _Reject(
                "route-map requires explicit name, permit|deny and sequence "
                "(defaults are not assumed)"
            )
        name = match.group("name")
        action = match.group("action").lower()
        sequence = int(match.group("sequence"))
        entries = self._route_maps.setdefault(name, {})
        builder = entries.get(sequence)
        if builder is not None and builder.action != action:
            self._taint_route_map(name, node.line_number, "conflicting action")
            raise _Reject(
                f"route-map {name} sequence {sequence} redefined with a different "
                "action",
                invalid=True,
            )
        if builder is None:
            builder = _EntryBuilder(action, self._reference(node.line_number))
            entries[sequence] = builder
        self._parsed(node)
        for child in node.children:
            try:
                self._route_map_child(builder, child)
            except _Reject as rejection:
                self._taint_route_map(
                    name, child.line_number, f"unparsed line: {rejection.reason}"
                )
                self._reject(
                    child, rejection.reason, invalid=rejection.invalid, parent=node
                )

    def _route_map_child(self, builder: _EntryBuilder, node: ConfigNode) -> None:
        if node.children:
            raise _Reject("Nested block inside route-map is not supported")
        negated, tokens = _split(node)
        words = [token.casefold() for token in tokens]
        if negated:
            raise _Reject("Negated route-map statements are not supported")
        if words[:1] == ["description"]:
            pass
        elif words[:3] == ["match", "ip", "address"] and len(tokens) >= 4:
            if words[3] == "prefix-list":
                if len(tokens) < 5:
                    raise _Reject("Missing prefix-list name", invalid=True)
                builder.prefix_lists.extend(tokens[4:])
            else:
                builder.acls.extend(tokens[3:])
            builder.matches.append(node.command)
        elif words[:2] == ["set", "local-preference"] and len(tokens) == 3:
            _integer(tokens[2], 0, 4294967295, "local-preference")
            builder.sets.append(node.command)
        elif words[:2] == ["set", "metric"] and len(tokens) == 3:
            _integer(tokens[2], 0, 4294967295, "metric")
            builder.sets.append(node.command)
        elif words[:2] == ["set", "weight"] and len(tokens) == 3:
            _integer(tokens[2], 0, 65535, "weight")
            builder.sets.append(node.command)
        elif words[:3] == ["set", "ip", "next-hop"] and len(tokens) == 4:
            _address(tokens[3])
            builder.sets.append(node.command)
        else:
            raise _Reject("No semantic handler for this route-map statement")
        self._parsed(node)

    def _taint_route_map(self, name: str, line: int, why: str) -> None:
        state = self._route_map_state.setdefault(name, RouteMap(name=name))
        state.complete = False
        state.incomplete_reasons.append(f"line {line}: {why}")
        self._route_maps.setdefault(name, {})

    def _finish_route_maps(self) -> None:
        maps: list[RouteMap] = []
        for name, entries in self._route_maps.items():
            state = self._route_map_state.get(name)
            route_map = RouteMap(name=name)
            if state is not None:
                route_map.complete = state.complete
                route_map.incomplete_reasons = state.incomplete_reasons
            for sequence in sorted(entries):
                builder = entries[sequence]
                route_map.entries.append(
                    RouteMapEntry(
                        sequence=sequence,
                        action=builder.action,
                        match_conditions=tuple(builder.matches),
                        set_actions=tuple(builder.sets),
                        prefix_list_references=tuple(builder.prefix_lists),
                        acl_references=tuple(builder.acls),
                        source_reference=builder.reference,
                    )
                )
            maps.append(route_map)
        self.document.route_maps = maps

    # ------------------------------------------------------------- references
    def _build_references(self) -> None:
        prefix_names = {p.name for p in self.document.prefix_lists}
        map_names = {m.name for m in self.document.route_maps}
        references: list[ConfigReference] = []
        for route_map in self.document.route_maps:
            for entry in route_map.entries:
                line = (
                    entry.source_reference.start_line
                    if entry.source_reference
                    else None
                )
                for target in entry.prefix_list_references:
                    references.append(
                        ConfigReference(
                            "route-map",
                            route_map.name,
                            "match-prefix-list",
                            "prefix-list",
                            target,
                            line,
                            target in prefix_names,
                            entry.sequence,
                        )
                    )
                for target in entry.acl_references:
                    references.append(
                        ConfigReference(
                            "route-map",
                            route_map.name,
                            "match-acl",
                            "acl",
                            target,
                            line,
                            None,
                            entry.sequence,
                        )
                    )
        for bgp in self.document.bgp_processes:
            for peer in bgp.peers:
                for binding in peer.attributes.get("policy_bindings", []):
                    kind = binding["type"]
                    target_type = "route-map" if kind == "route-map" else "prefix-list"
                    names = map_names if kind == "route-map" else prefix_names
                    references.append(
                        ConfigReference(
                            "bgp-neighbor",
                            f"AS{bgp.asn}/{peer.neighbor_address}",
                            f"{kind}-{binding['direction']}",
                            target_type,
                            binding["name"],
                            binding["line"],
                            binding["name"] in names,
                        )
                    )
            for network in bgp.networks:
                if network.route_map is not None:
                    references.append(
                        ConfigReference(
                            "bgp-network",
                            f"AS{bgp.asn}/{network.network}",
                            "route-map",
                            "route-map",
                            network.route_map,
                            network.line_number,
                            network.route_map in map_names,
                        )
                    )
        for reference in references:
            if reference.resolved is False:
                self.issues.append(
                    ParseIssue(
                        severity=IssueSeverity.INFO,
                        code="DANGLING_REFERENCE",
                        message=(
                            f"{reference.source_type} {reference.source_name} "
                            f"references undefined {reference.target_type} "
                            f"{reference.target_name!r} (it may be defined in "
                            "unparsed or external configuration)."
                        ),
                        line_number=reference.line_number,
                        source_reference=(
                            self._reference(reference.line_number)
                            if reference.line_number
                            else None
                        ),
                    )
                )
        self.document.references = references


def _walk(node: ConfigNode) -> list[ConfigNode]:
    nodes: list[ConfigNode] = []
    for child in node.children:
        nodes.append(child)
        nodes.extend(_walk(child))
    return nodes


def _is_ipv4(value: str) -> bool:
    try:
        IPv4Address(value)
    except AddressValueError:
        return False
    return True


def _discard(items: list[str], value: str) -> None:
    if value in items:
        items.remove(value)
