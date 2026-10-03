"""A10 Networks ACOS operational parsers (routing table and interfaces)."""

from __future__ import annotations

import re
from ipaddress import IPv4Interface

from nwconfig_parser.models import Interface
from nwconfig_parser.parsers.operational_base import Collector, OperationalParser
from nwconfig_parser.parsers.zebra_routes import ZebraStyleRouteParser
from nwconfig_parser.utilities import netmask_to_prefix, normalize_mac_address

_HEADER = re.compile(
    r"^(?P<name>[A-Za-z][\w-]*(?: \d+)?) is (?P<state>[A-Za-z ]+?), "
    r"line protocol is (?P<protocol>\w+)$"
)
_HARDWARE = re.compile(r"^Hardware is (?P<hardware>[^,]+), Address is (?P<mac>\S+)$")
_ADDRESS = re.compile(
    r"^Internet address is (?P<ip>(?:\d{1,3}\.){3}\d{1,3}), "
    r"Subnet mask is (?P<mask>(?:\d{1,3}\.){3}\d{1,3})$"
)


class A10ShowIpRouteParser(ZebraStyleRouteParser):
    vendor = "A10"
    os_family = "ACOS"
    command = "show ip route"


class A10ShowInterfacesParser(OperationalParser):
    vendor = "A10"
    os_family = "ACOS"
    command = "show interfaces"

    def empty_data(self) -> object:
        return []

    def parse_lines(self, lines: list[str], collector: Collector) -> list[Interface]:
        interfaces: list[Interface] = []
        current: Interface | None = None
        for number, raw in enumerate(lines, start=1):
            if collector.is_noise(raw):
                continue
            line = raw.strip()
            header = _HEADER.fullmatch(line)
            if header is not None:
                state = header.group("state")
                current = Interface(
                    name=header.group("name"),
                    operational_status=header.group("protocol").lower(),
                )
                if state.casefold() == "administratively down":
                    current.admin_status = "down"
                current.attributes["link_state"] = state
                current.attributes["source_reference"] = collector.reference(number)
                interfaces.append(current)
                collector.record()
                continue
            if current is None or not self._detail(current, line):
                collector.issue(
                    number,
                    "UNSUPPORTED_INTERFACE_LINE",
                    "Line is not a supported ACOS interface header or detail line.",
                )
                continue
            if current.attributes.pop("_invalid", None):
                collector.issue(
                    number,
                    "INVALID_INTERFACE_VALUE",
                    "Interface detail line contains an invalid value.",
                )
        return interfaces

    @staticmethod
    def _detail(interface: Interface, line: str) -> bool:
        hardware = _HARDWARE.fullmatch(line)
        if hardware is not None:
            interface.attributes["hardware"] = hardware.group("hardware")
            try:
                interface.mac_address = normalize_mac_address(hardware.group("mac"))
            except ValueError:
                interface.attributes["_invalid"] = True
            return True
        address = _ADDRESS.fullmatch(line)
        if address is not None:
            try:
                parsed = IPv4Interface(
                    f"{address.group('ip')}/{netmask_to_prefix(address.group('mask'))}"
                )
            except ValueError:
                interface.attributes["_invalid"] = True
                return True
            interface.ipv4_addresses.append(parsed)
            if interface.ipv4_address is None:
                interface.ipv4_address = parsed.ip
            return True
        return False
