"""Yamaha SWX operational parsers (English-console output).

Formats follow the examples in the SWX2320 (Rev.2.05-2.08) and SWX3200
(Rev.4.00) command references. Operational state (link up/down) is kept apart
from configured admin state, which is set only where a column is documented as
the admin status.
"""

from __future__ import annotations

import re
from ipaddress import IPv4Address, IPv4Interface, IPv4Network

from nwconfig_parser.models import VLAN, Interface, Route, RoutingTable
from nwconfig_parser.parsers.operational_base import Collector, OperationalParser

_RULE = re.compile(r"^-{10,}$")
_ROUTE = re.compile(
    r"^(?P<code>[A-Za-z]+)(?P<star>\*?)\s+(?P<net>[\d.]+/\d+)\s+"
    r"(?:\[(?P<ad>\d+)/(?P<metric>\d+)\]\s+)?"
    r"(?:via\s+(?P<gw>[\d.]+),\s*(?P<ifn>\S+)"
    r"|is directly connected,\s*(?P<cifn>\S+))$"
)
_LEGEND = re.compile(r"(?P<key>[A-Za-z*>]{1,2}) - (?P<name>[^,]+)")
_MEMBER = re.compile(r"^(?P<name>[A-Za-z]+[\d.]+)\((?P<tag>[ut])\)$")
_STATES = {"up", "down"}


def _dash(value: str) -> str | None:
    return None if value == "--" else value


class YamahaSwxShowInterfaceBriefParser(OperationalParser):
    vendor = "Yamaha"
    os_family = "SWX"
    command = "show interface brief"

    def empty_data(self) -> object:
        return []

    def parse_lines(self, lines: list[str], collector: Collector) -> list[Interface]:
        interfaces: list[Interface] = []
        section: str | None = None
        for number, raw in enumerate(lines, start=1):
            if collector.is_noise(raw):
                continue
            line = raw.strip()
            tokens = line.split()
            if _RULE.fullmatch(line) or line.startswith("Codes:"):
                continue
            if raw[:1].isspace() and re.match(r"^[A-Z]{2} - ", line):
                continue
            if tokens[0] == "Ethernet" and "PVID" in tokens:
                section = "port"
                collector.format_seen = True
                continue
            if tokens[:4] == ["Interface", "Status", "Reason", "Description"]:
                section = "vlan"
                collector.format_seen = True
                continue
            if tokens[0] == "Port-channel" and "PVID" in tokens:
                section = "channel"
                collector.format_seen = True
                continue
            if tokens[0] == "Interface" and tokens[-2:] == ["Ch", "#"]:
                continue
            if tokens == ["Interface"]:
                continue
            row = self._row(section, tokens)
            if row is None:
                collector.issue(
                    number,
                    "UNSUPPORTED_INTERFACE_LINE",
                    "Line is not a recognised SWX interface brief row or header.",
                )
                continue
            row.attributes["source_reference"] = collector.reference(number)
            interfaces.append(row)
            collector.record()
        return interfaces

    @staticmethod
    def _row(section: str | None, tokens: list[str]) -> Interface | None:
        if section in {"port", "channel"}:
            if len(tokens) < 8 or tokens[4] not in _STATES:
                return None
            name, kind, pvid, mode, status, reason, speed, channel = tokens[:8]
            if mode not in {"access", "trunk"} or not pvid.isdigit():
                return None
            entry = Interface(
                name=name,
                operational_status=status,
                mode=mode,
                speed=speed,
                description=_dash(" ".join(tokens[8:])) if tokens[8:] else None,
            )
            entry.attributes.update(type=kind, pvid=int(pvid), reason=_dash(reason))
            if _dash(channel) is not None:
                entry.channel_group = channel
            return entry
        if section == "vlan":
            if len(tokens) < 3 or tokens[1] not in _STATES:
                return None
            entry = Interface(
                name=tokens[0],
                operational_status=tokens[1],
                description=_dash(" ".join(tokens[3:])) if tokens[3:] else None,
            )
            entry.attributes["reason"] = _dash(tokens[2])
            return entry
        return None


class YamahaSwxShowVlanBriefParser(OperationalParser):
    vendor = "Yamaha"
    os_family = "SWX"
    command = "show vlan brief"

    _ROW = re.compile(
        r"^(?P<id>\d+)\s+(?P<name>\S+)\s+(?P<state>ACTIVE|SUSPEND)\b\s*(?P<ports>.*)$"
    )

    def empty_data(self) -> object:
        return []

    def parse_lines(self, lines: list[str], collector: Collector) -> list[VLAN]:
        vlans: list[VLAN] = []
        current: VLAN | None = None
        for number, raw in enumerate(lines, start=1):
            if collector.is_noise(raw):
                continue
            line = raw.strip()
            if line.startswith("(u)-Untagged") or set(line) <= {"=", " "}:
                continue
            if re.fullmatch(r"VLAN ID\s+Name\s+State\s+Member ports", line):
                collector.format_seen = True
                continue
            match = None if raw[:1].isspace() else self._ROW.fullmatch(line)
            if match is not None:
                vlan = VLAN(
                    vlan_id=int(match.group("id")),
                    name=match.group("name"),
                    status=match.group("state").lower(),
                )
                vlan.attributes["source_reference"] = collector.reference(number)
                if not self._members(vlan, match.group("ports")):
                    self._bad(collector, number)
                    continue
                vlans.append(vlan)
                current = vlan
                collector.record()
            elif raw[:1].isspace() and current is not None:
                if not self._members(current, line):
                    self._bad(collector, number)
            else:
                self._bad(collector, number)
        return vlans

    @staticmethod
    def _bad(collector: Collector, number: int) -> None:
        collector.issue(
            number,
            "UNSUPPORTED_VLAN_LINE",
            "Line is not a recognised SWX VLAN row, member list or header.",
        )

    @staticmethod
    def _members(vlan: VLAN, text: str) -> bool:
        tagging: dict[str, str] = vlan.attributes.setdefault("member_tagging", {})
        for token in text.split():
            match = _MEMBER.fullmatch(token)
            if match is None:
                return False
            vlan.interfaces.append(match.group("name"))
            tagging[match.group("name")] = (
                "untagged" if match.group("tag") == "u" else "tagged"
            )
        return True


class YamahaSwxShowIpRouteParser(OperationalParser):
    vendor = "Yamaha"
    os_family = "SWX"
    command = "show ip route"

    def empty_data(self) -> object:
        return RoutingTable()

    def parse_lines(self, lines: list[str], collector: Collector) -> RoutingTable:
        table = RoutingTable()
        codes: dict[str, str] = {}
        in_legend = False
        for number, raw in enumerate(lines, start=1):
            if collector.is_noise(raw):
                continue
            line = raw.strip()
            if line.startswith("Codes:") or (in_legend and raw[:1].isspace()):
                in_legend = True
                for item in _LEGEND.finditer(line.removeprefix("Codes:")):
                    codes[item.group("key")] = item.group("name").strip()
                collector.format_seen = True
                continue
            in_legend = False
            if line.startswith("Gateway of last resort"):
                collector.format_seen = True
                continue
            match = _ROUTE.fullmatch(line)
            if match is None:
                collector.issue(
                    number,
                    "UNSUPPORTED_ROUTE_LINE",
                    "Line is not a recognised SWX route row or header.",
                )
                continue
            protocol = codes.get(match.group("code"))
            if protocol is None:
                collector.issue(
                    number,
                    "UNKNOWN_ROUTE_CODE",
                    "Route code is not defined by the Codes legend.",
                )
                continue
            try:
                route = self._route(match, protocol)
            except ValueError:
                collector.issue(number, "INVALID_ROUTE", "Route could not be parsed.")
                continue
            route.attributes["source_reference"] = collector.reference(number)
            table.routes.append(route)
            collector.record()
        return table

    @staticmethod
    def _route(match: re.Match[str], protocol: str) -> Route:
        route = Route(
            network=IPv4Network(match.group("net"), strict=True),
            protocol=protocol,
        )
        route.attributes["candidate_default"] = bool(match.group("star"))
        if match.group("gw") is not None:
            route.next_hops.append(IPv4Address(match.group("gw")))
            route.outgoing_interfaces.append(match.group("ifn"))
        else:
            route.outgoing_interfaces.append(match.group("cifn"))
        if match.group("ad") is not None:
            route.administrative_distance = int(match.group("ad"))
            route.metric = int(match.group("metric"))
        return route


class YamahaSwxShowIpInterfaceBriefParser(OperationalParser):
    vendor = "Yamaha"
    os_family = "SWX"
    command = "show ip interface brief"

    _HEADER = re.compile(r"Interface\s+IP-Address\s+Admin-Status\s+Link-Status")

    def empty_data(self) -> object:
        return []

    def parse_lines(self, lines: list[str], collector: Collector) -> list[Interface]:
        interfaces: list[Interface] = []
        entry: Interface | None = None
        done = True
        start = 0

        def close() -> None:
            if entry is not None and not done:
                collector.issue(
                    start,
                    "INCOMPLETE_INTERFACE_ROW",
                    "Interface row has no Admin-Status and Link-Status columns.",
                )
                interfaces.remove(entry)

        for number, raw in enumerate(lines, start=1):
            if collector.is_noise(raw):
                continue
            line = raw.strip()
            if self._HEADER.fullmatch(line):
                collector.format_seen = True
                continue
            tokens = line.split()
            if not raw[:1].isspace():
                close()
                entry = Interface(name=tokens[0])
                entry.attributes["source_reference"] = collector.reference(number)
                interfaces.append(entry)
                done, start = False, number
                tokens = tokens[1:]
            elif entry is None or done:
                collector.issue(
                    number, "UNSUPPORTED_INTERFACE_LINE", "Unexpected continuation."
                )
                continue
            if not self._fields(entry, tokens):
                collector.issue(
                    number,
                    "UNSUPPORTED_INTERFACE_LINE",
                    "Line is not a recognised SWX IPv4 interface row.",
                )
                interfaces.remove(entry)
                entry, done = None, True
                continue
            if entry.admin_status is not None:
                done = True
                collector.record()
        close()
        return interfaces

    @staticmethod
    def _fields(entry: Interface, tokens: list[str]) -> bool:
        if len(tokens) >= 2 and tokens[-1] in _STATES and tokens[-2] in _STATES:
            entry.admin_status, entry.operational_status = tokens[-2], tokens[-1]
            tokens = tokens[:-2]
        if not tokens:
            return True
        address = tokens[0]
        if address == "unassigned" and len(tokens) == 1:
            return True
        if address == "searching" and len(tokens) == 1:
            entry.attributes["dhcp_state"] = "searching"
            return True
        if address.startswith("*"):
            entry.attributes["dhcp"] = True
            address = address[1:]
        if tokens[1:] not in ([], ["(secondary)"]):
            return False
        try:
            parsed = IPv4Interface(address)
        except ValueError:
            return False
        if tokens[1:]:
            entry.attributes.setdefault("secondary", []).append(str(parsed))
        elif entry.ipv4_address is None:
            entry.ipv4_address = parsed.ip
        entry.ipv4_addresses.append(parsed)
        return True
