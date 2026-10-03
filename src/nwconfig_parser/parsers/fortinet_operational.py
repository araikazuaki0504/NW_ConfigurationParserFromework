"""Fortinet FortiOS operational parsers (routing table and interfaces)."""

from __future__ import annotations

import re
from ipaddress import IPv4Interface

from nwconfig_parser.models import Interface
from nwconfig_parser.parsers.operational_base import Collector, OperationalParser
from nwconfig_parser.parsers.zebra_routes import ZebraStyleRouteParser
from nwconfig_parser.utilities import netmask_to_prefix

_HEADER = re.compile(r"^==\s*\[\s*(?P<name>[^\]]+?)\s*\]$")
_PAIR = re.compile(
    r"(?P<key>[A-Za-z][\w-]*):\s*(?P<value>.*?)(?=\s{2,}[A-Za-z][\w-]*:|\s*$)"
)


class FortinetRoutingTableAllParser(ZebraStyleRouteParser):
    vendor = "Fortinet"
    os_family = "FortiOS"
    command = "get router info routing-table all"
    heading_patterns = (re.compile(r"Routing table for VRF=\d+"),)


class FortinetSystemInterfaceParser(OperationalParser):
    vendor = "Fortinet"
    os_family = "FortiOS"
    command = "get system interface"

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
                current = Interface(name=header.group("name"))
                current.attributes["source_reference"] = collector.reference(number)
                current.attributes["raw_fields"] = {}
                interfaces.append(current)
                collector.record()
                continue
            pairs = [
                (m.group("key"), m.group("value").strip()) for m in _PAIR.finditer(line)
            ]
            starts_with_key = bool(pairs) and _PAIR.match(line) is not None
            if current is None or not starts_with_key:
                collector.issue(
                    number,
                    "UNSUPPORTED_INTERFACE_LINE",
                    "Line is not a FortiOS interface header or key/value line.",
                )
                continue
            self._apply(current, pairs, number, collector)
        return interfaces

    @staticmethod
    def _apply(
        interface: Interface,
        pairs: list[tuple[str, str]],
        number: int,
        collector: Collector,
    ) -> None:
        raw_fields: dict[str, str] = interface.attributes["raw_fields"]
        for key, value in pairs:
            raw_fields[key] = value
            if key == "name" and value != interface.name:
                collector.issue(
                    number,
                    "INTERFACE_NAME_MISMATCH",
                    f"Header names {interface.name!r} but key says {value!r}.",
                )
            elif key == "ip":
                try:
                    address, mask = value.split()
                    if (address, mask) == ("0.0.0.0", "0.0.0.0"):
                        continue
                    parsed = IPv4Interface(f"{address}/{netmask_to_prefix(mask)}")
                except ValueError as exc:
                    collector.issue(
                        number,
                        "INVALID_INTERFACE_ADDRESS",
                        f"Invalid 'ip' value {value!r}: {exc}",
                    )
                    continue
                interface.ipv4_addresses = [parsed]
                interface.ipv4_address = parsed.ip
            elif key == "status":
                # Not mapped to admin/operational state: unverified semantics.
                interface.attributes["status"] = value
            elif key == "mode":
                interface.attributes["addressing_mode"] = value
            elif key == "type":
                interface.attributes["interface_type"] = value
