"""Yamaha RTX operational parsers (English-console output)."""

from __future__ import annotations

import re
from ipaddress import IPv4Address, IPv4Network

from nwconfig_parser.models import Route, RoutingTable
from nwconfig_parser.parsers.operational_base import Collector, OperationalParser

_HEADER = re.compile(
    r"^Destination\s+Gateway\s+Interface\s+Kind(?:\s+Additional Info\.?)?$",
    re.IGNORECASE,
)
_ROW = re.compile(
    r"^(?P<dest>default|(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?)\s+"
    r"(?P<gateway>\S+)\s+(?P<interface>\S+)\s+(?P<kind>\S+)"
    r"(?:\s+(?P<info>.+))?$"
)


class YamahaShowIpRouteParser(OperationalParser):
    vendor = "Yamaha"
    os_family = "RTX"
    command = "show ip route"

    def empty_data(self) -> object:
        return RoutingTable()

    def parse_lines(self, lines: list[str], collector: Collector) -> RoutingTable:
        table = RoutingTable()
        for number, raw in enumerate(lines, start=1):
            if collector.is_noise(raw):
                continue
            line = raw.strip()
            if _HEADER.fullmatch(line):
                collector.format_seen = True
                continue
            match = _ROW.fullmatch(line)
            if match is None:
                collector.issue(
                    number,
                    "UNSUPPORTED_ROUTE_LINE",
                    "Line is not a recognised Yamaha RTX route row or header "
                    "(Japanese-language headers are not supported).",
                )
                continue
            try:
                route = self._route(match)
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
    def _route(match: re.Match[str]) -> Route:
        dest = match.group("dest")
        if dest == "default":
            network = IPv4Network("0.0.0.0/0")
        elif "/" in dest:
            network = IPv4Network(dest, strict=True)
        else:
            raise ValueError(f"destination {dest!r} has no prefix length")
        route = Route(network=network, protocol=match.group("kind").lower())
        route.attributes["kind"] = match.group("kind")
        gateway = match.group("gateway")
        if gateway != "-":
            route.next_hops.append(IPv4Address(gateway))
        route.outgoing_interfaces.append(match.group("interface"))
        if match.group("info"):
            route.attributes["additional_info"] = match.group("info").strip()
        return route
