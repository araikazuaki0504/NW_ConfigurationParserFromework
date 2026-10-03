"""Route-table parser shared by Zebra-style outputs (FortiOS, ACOS).

The two vendors print similar layouts but remain separate parser classes with
their own registration keys; this base only holds the common line grammar.
Protocol codes are kept exactly as printed (no normalisation to a common name).
"""

from __future__ import annotations

import re
from ipaddress import IPv4Address, IPv4Network
from typing import ClassVar

from nwconfig_parser.models import Route, RoutingTable
from nwconfig_parser.parsers.operational_base import Collector, OperationalParser

_IP = r"(?:\d{1,3}\.){3}\d{1,3}"
_ROUTE_LINE = re.compile(
    r"^(?P<code>(?:[A-Za-z*][A-Za-z0-9*]*\s+)*[A-Za-z*][A-Za-z0-9*]*)\s+"
    rf"(?P<network>{_IP}/\d{{1,2}})\s+(?P<details>.+)$"
)
_VIA = re.compile(
    rf"^\[(?P<distance>\d+)/(?P<metric>\d+)\]\s+via\s+(?P<hop>{_IP})(?P<rest>.*)$"
)
_DIRECT = re.compile(r"^is directly connected,\s*(?P<interface>.+?)\s*$")
_AGE = re.compile(r"^(?:\d{2}:\d{2}:\d{2}|\d+[wdh]\d+[dhm]?\d*)$")


class ZebraStyleRouteParser(OperationalParser):
    heading_patterns: ClassVar[tuple[re.Pattern[str], ...]] = ()

    def empty_data(self) -> object:
        return RoutingTable()

    def parse_lines(self, lines: list[str], collector: Collector) -> RoutingTable:
        table = RoutingTable()
        in_legend = False
        for number, raw in enumerate(lines, start=1):
            if collector.is_noise(raw):
                in_legend = False
                continue
            line = raw.strip()
            if line.startswith("Codes:"):
                in_legend = True
                collector.format_seen = True
                continue
            if in_legend and raw[:1].isspace() and " - " in line:
                continue
            in_legend = False
            if any(p.fullmatch(line) for p in self.heading_patterns):
                collector.format_seen = True
                continue
            if line.startswith("["):
                self._continuation(table, line, number, collector)
                continue
            match = _ROUTE_LINE.fullmatch(line)
            if match is None:
                collector.issue(
                    number,
                    "UNSUPPORTED_ROUTE_LINE",
                    f"Line is not a recognised {self.vendor} IPv4 route or heading.",
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

    def _route(self, match: re.Match[str]) -> Route:
        code = " ".join(match.group("code").split())
        route = Route(
            network=IPv4Network(match.group("network"), strict=True),
            protocol=code.replace("*", "").strip() or code,
        )
        route.attributes["code"] = code
        if "*" in code:
            route.attributes["candidate_default"] = True
        details = match.group("details")
        direct = _DIRECT.fullmatch(details)
        if direct is not None:
            route.outgoing_interfaces.append(direct.group("interface"))
            return route
        via = _VIA.fullmatch(details)
        if via is None:
            raise ValueError(
                "expected '[AD/metric] via <next-hop>' or 'is directly connected'"
            )
        route.administrative_distance = int(via.group("distance"))
        route.metric = int(via.group("metric"))
        route.next_hops.append(IPv4Address(via.group("hop")))
        self._tail(route, via.group("rest"))
        return route

    @staticmethod
    def _tail(route: Route, tail: str) -> None:
        interface: str | None = None
        for component in (c.strip() for c in tail.split(",")):
            if not component:
                continue
            if _AGE.fullmatch(component):
                route.attributes["age"] = component
            elif interface is None:
                interface = component
            else:
                raise ValueError(f"unexpected route detail {component!r}")
        if interface is not None:
            route.outgoing_interfaces.append(interface)

    def _continuation(
        self, table: RoutingTable, line: str, number: int, collector: Collector
    ) -> None:
        via = _VIA.fullmatch(line)
        if via is None or not table.routes:
            collector.issue(
                number,
                "ORPHAN_ROUTE_CONTINUATION"
                if via is not None
                else "UNSUPPORTED_ROUTE_LINE",
                "ECMP continuation is malformed or has no preceding route.",
            )
            return
        current = table.routes[-1]
        distance, metric = int(via.group("distance")), int(via.group("metric"))
        if (distance, metric) != (current.administrative_distance, current.metric):
            collector.issue(
                number,
                "ECMP_ATTRIBUTE_MISMATCH",
                "ECMP path has a different distance/metric; not merged.",
            )
            return
        scratch = Route(network=current.network, protocol=current.protocol)
        try:
            scratch.next_hops.append(IPv4Address(via.group("hop")))
            self._tail(scratch, via.group("rest"))
        except ValueError as exc:
            collector.issue(number, "INVALID_ROUTE", f"Invalid ECMP path: {exc}")
            return
        current.next_hops.extend(scratch.next_hops)
        current.outgoing_interfaces.extend(scratch.outgoing_interfaces)
        collector.record()
