"""HPE Comware operational parsers (Comware 7 style output)."""

from __future__ import annotations

import re
from ipaddress import IPv4Address, IPv4Interface, IPv4Network

from nwconfig_parser.models import Interface, Route, RoutingTable, VrfScope
from nwconfig_parser.parsers.operational_base import Collector, OperationalParser
from nwconfig_parser.utilities import normalize_mac_address

_IP = r"(?:\d{1,3}\.){3}\d{1,3}"
_SUMMARY = re.compile(
    r"^(?:Destinations\s*:\s*\d+\s+Routes\s*:\s*\d+|Routing Tables:.*)$"
)
_HEADER = re.compile(r"^Destination/Mask\s+Proto\s+Pre\s+Cost\s+NextHop\s+Interface$")
_FULL_ROW = re.compile(
    rf"^(?P<dest>{_IP}/\d{{1,2}})\s+(?P<rest>\S+\s+\d+\s+\d+\s+{_IP}\s+\S+)$"
)
_PROTOCOLS = {
    "direct": "connected",
    "static": "static",
    "rip": "rip",
    "ospf": "ospf",
    "bgp": "bgp",
}


class HpeComwareDisplayIpRoutingTableParser(OperationalParser):
    vendor = "HPE"
    os_family = "Comware"
    command = "display ip routing-table"

    def empty_data(self) -> object:
        return RoutingTable()

    def parse_lines(self, lines: list[str], collector: Collector) -> RoutingTable:
        table = RoutingTable()
        previous: IPv4Network | None = None
        for number, raw in enumerate(lines, start=1):
            if collector.is_noise(raw):
                continue
            line = raw.strip()
            if _HEADER.fullmatch(line):
                collector.format_seen = True
                continue
            if _SUMMARY.fullmatch(line):
                continue
            full = _FULL_ROW.fullmatch(line)
            tokens = line.split()
            try:
                if full is not None:
                    previous = IPv4Network(full.group("dest"), strict=True)
                    route = self._route(previous, full.group("rest").split())
                elif len(tokens) == 5 and previous is not None and raw[:1].isspace():
                    route = self._route(previous, tokens)
                    route.attributes["continuation"] = True
                else:
                    collector.issue(
                        number,
                        "UNSUPPORTED_ROUTE_LINE",
                        "Line is not a recognised Comware IPv4 route row or header.",
                    )
                    continue
            except ValueError as exc:
                collector.issue(
                    number, "INVALID_ROUTE", f"Route line could not be parsed: {exc}"
                )
                continue
            route.attributes["source_reference"] = collector.reference(number)
            table.routes.append(route)
            collector.record()
        return table

    @staticmethod
    def _route(network: IPv4Network, tokens: list[str]) -> Route:
        proto, pre, cost, next_hop, interface = tokens
        route = Route(
            network=network,
            protocol=_PROTOCOLS.get(proto.casefold(), proto),
            administrative_distance=int(pre),
            metric=int(cost),
        )
        route.attributes["raw_protocol"] = proto
        route.outgoing_interfaces.append(interface)
        if proto.casefold() == "direct":
            # For direct routes the NextHop column is the local address.
            route.attributes["next_hop_raw"] = next_hop
        else:
            route.next_hops.append(IPv4Address(next_hop))
        return route


_VPN_COMMAND = re.compile(
    r"^display\s+ip\s+routing-table\s+vpn-instance\s+(?P<name>\S+)\s*$",
    re.IGNORECASE,
)
_VPN_HEADING = re.compile(r"^Routing Tables:\s*(?P<name>\S+)\s*$")


class HpeComwareDisplayIpRoutingTableVpnParser(HpeComwareDisplayIpRoutingTableParser):
    """``display ip routing-table vpn-instance <name>`` (single VPN instance)."""

    command = "display ip routing-table vpn-instance *"

    def matches_command(self, command: str) -> bool:
        return _VPN_COMMAND.fullmatch(" ".join(command.split())) is not None

    def parse_lines(self, lines: list[str], collector: Collector) -> RoutingTable:
        table = super().parse_lines(lines, collector)
        match = _VPN_COMMAND.fullmatch(" ".join(collector.command.split()))
        requested = match.group("name") if match else None
        mismatch = False
        for number, raw in enumerate(lines, start=1):
            heading = _VPN_HEADING.fullmatch(raw.strip())
            if heading is not None and heading.group("name") != requested:
                mismatch = True
                collector.issue(
                    number,
                    "VRF_HEADING_MISMATCH",
                    f"Heading names {heading.group('name')!r} but the command "
                    f"requested {requested!r}; VRF membership is not assigned.",
                )
        name = None if mismatch else requested
        scope = VrfScope.NAMED if name else VrfScope.UNKNOWN
        table.vrf, table.vrf_scope = name, scope
        table.source_command = collector.command
        for route in table.routes:
            route.vrf, route.vrf_scope = name, scope
        return table


_NAME = re.compile(r"^[A-Za-z][\w-]*\d[\w/.:-]*$")
_STATE = re.compile(r"^Current state:\s*(?P<state>.+)$")
_PROTOCOL_STATE = re.compile(r"^Line protocol state:\s*(?P<state>.+)$")
_DESCRIPTION = re.compile(r"^Description:\s*(?P<text>.*)$")
_MTU = re.compile(r"^Maximum Transmit Unit:\s*(?P<mtu>\d+)$")
_BANDWIDTH = re.compile(r"^Bandwidth:\s*(?P<kbps>\d+)\s*kbps$")
_ADDRESS = re.compile(
    rf"^Internet address:\s*(?P<addr>{_IP}/\d{{1,2}})(?:\s*\((?P<kind>\w+)\))?$"
)
_FRAME = re.compile(
    r"^IP packet frame type:\s*(?P<frame>[^,]+),\s*hardware address:\s*(?P<mac>\S+)$"
)


class HpeComwareDisplayInterfaceParser(OperationalParser):
    vendor = "HPE"
    os_family = "Comware"
    command = "display interface"

    def empty_data(self) -> object:
        return []

    def parse_lines(self, lines: list[str], collector: Collector) -> list[Interface]:
        interfaces: list[Interface] = []
        current: Interface | None = None
        for number, raw in enumerate(lines, start=1):
            if collector.is_noise(raw):
                continue
            line = raw.strip()
            if not raw[:1].isspace() and _NAME.fullmatch(line):
                current = Interface(name=line)
                current.attributes["source_reference"] = collector.reference(number)
                interfaces.append(current)
                collector.record()
                continue
            if current is None:
                collector.issue(
                    number,
                    "UNSUPPORTED_INTERFACE_LINE",
                    "Line appears before any Comware interface name line.",
                )
                continue
            try:
                handled = self._detail(current, line)
            except ValueError as exc:
                collector.issue(
                    number, "INVALID_INTERFACE_VALUE", f"Invalid value: {exc}"
                )
                continue
            if not handled:
                collector.issue(
                    number,
                    "UNSUPPORTED_INTERFACE_LINE",
                    "Line is not a supported Comware 7 display interface field.",
                )
        return interfaces

    @staticmethod
    def _detail(interface: Interface, line: str) -> bool:
        if (m := _STATE.fullmatch(line)) is not None:
            state = m.group("state").strip()
            interface.attributes["current_state"] = state
            if state.casefold() == "administratively down":
                # Only the administrative side is known from this value.
                interface.admin_status = "down"
            elif state.casefold() in {"up", "down"}:
                interface.operational_status = state.lower()
            else:
                raise ValueError(f"unknown state {state!r}")
        elif (m := _PROTOCOL_STATE.fullmatch(line)) is not None:
            interface.attributes["line_protocol_state"] = m.group("state").strip()
        elif (m := _DESCRIPTION.fullmatch(line)) is not None:
            interface.description = m.group("text").strip()
        elif (m := _MTU.fullmatch(line)) is not None:
            interface.mtu = int(m.group("mtu"))
        elif (m := _BANDWIDTH.fullmatch(line)) is not None:
            interface.attributes["bandwidth_kbps"] = int(m.group("kbps"))
        elif (m := _ADDRESS.fullmatch(line)) is not None:
            parsed = IPv4Interface(m.group("addr"))
            interface.ipv4_addresses.append(parsed)
            if interface.ipv4_address is None:
                interface.ipv4_address = parsed.ip
        elif (m := _FRAME.fullmatch(line)) is not None:
            interface.mac_address = normalize_mac_address(m.group("mac"))
            interface.attributes["frame_type"] = m.group("frame").strip()
        else:
            return False
        return True
