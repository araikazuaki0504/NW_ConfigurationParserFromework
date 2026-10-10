"""Cisco IOS and IOS XE parsers for selected IPv4 operational commands."""

from __future__ import annotations

import re
from collections.abc import Callable
from ipaddress import IPv4Address, IPv4Network

from nwconfig_parser.models import (
    VLAN,
    ImplementationStatus,
    Interface,
    IssueSeverity,
    ParseIssue,
    ParseResult,
    ParseStatus,
    Route,
    RoutingTable,
    SourceReference,
    VrfScope,
)
from nwconfig_parser.parsers.base import ParseContext, Parser
from nwconfig_parser.utilities import strip_cli_prompt

_IPV4_PREFIX = r"(?:\d{1,3}\.){3}\d{1,3}/\d{1,2}"
_ROUTE_LINE = re.compile(
    rf"^(?P<protocol>(?:[A-Z*]+\s+)*[A-Z*]+)\s+"
    rf"(?P<network>{_IPV4_PREFIX})\s+(?P<details>.+)$"
)
_ROUTE_CONTINUATION = re.compile(
    r"^\[(?P<distance>\d+)/(?P<metric>\d+)\]\s+via\s+"
    r"(?P<next_hop>(?:\d{1,3}\.){3}\d{1,3})(?P<rest>.*)$"
)
_VIA = re.compile(r"\bvia\s+((?:\d{1,3}\.){3}\d{1,3})(.*)$")
_DIRECT = re.compile(r"\bis directly connected,\s*(?P<interface>\S+)\s*$")
_INTERFACE_NAME = re.compile(r"^(?:Gi|Fa|Te|Eth|Po|Vl|Twe|Hu|Lo)\S*$", re.I)
_INTERFACE_STATUSES = {
    "connected",
    "notconnect",
    "disabled",
    "err-disabled",
    "inactive",
    "sfpabsent",
    "monitoring",
    "suspended",
}


def _reference(context: ParseContext, line_number: int) -> SourceReference:
    return SourceReference(
        filename=context.filename,
        command=context.command,
        start_line=line_number,
        end_line=line_number,
    )


class CiscoOperationalParser(Parser):
    """Command-specific Cisco parser selected by an exact registry key."""

    vendor = "Cisco"
    supported_versions: tuple[str, ...] = ()
    parser_version = "1.0"
    implementation_status = ImplementationStatus.TESTED_WITH_SYNTHETIC_DATA
    _supported_commands = {
        "show ip route",
        "show interfaces status",
        "show ip interface brief",
        "show vlan brief",
    }

    def __init__(self, command: str, os_family: str) -> None:
        normalized_command = " ".join(command.casefold().split())
        if normalized_command not in self._supported_commands:
            raise ValueError(f"Unsupported Cisco operational command: {command}")
        normalized_os = " ".join(os_family.casefold().split())
        if normalized_os not in {"ios", "ios xe"}:
            raise ValueError(f"Unsupported Cisco OS family: {os_family}")
        self.command = normalized_command
        self.os_family = "IOS XE" if normalized_os == "ios xe" else "IOS"

    def parse(self, text: str, context: ParseContext) -> ParseResult:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        if not normalized.strip():
            issue = ParseIssue(
                severity=IssueSeverity.ERROR,
                code="EMPTY_INPUT",
                message="Input contains no command output.",
            )
            return self._result(
                context,
                data=self._empty_data(),
                issues=[issue],
                format_seen=False,
                parsed_records=0,
            )

        handlers: dict[
            str,
            Callable[
                [list[str], ParseContext],
                tuple[object, list[ParseIssue], bool, int],
            ],
        ] = {
            "show ip route": self._parse_routes,
            "show interfaces status": self._parse_interface_status,
            "show ip interface brief": self._parse_interface_brief,
            "show vlan brief": self._parse_vlans,
        }
        data, issues, format_seen, parsed_records = handlers[self.command](
            normalized.split("\n"), context
        )
        return self._result(
            context,
            data=data,
            issues=issues,
            format_seen=format_seen,
            parsed_records=parsed_records,
        )

    def _empty_data(self) -> object:
        if self.command == "show ip route":
            return RoutingTable()
        return []

    def _result(
        self,
        context: ParseContext,
        *,
        data: object,
        issues: list[ParseIssue],
        format_seen: bool,
        parsed_records: int,
    ) -> ParseResult:
        if format_seen and not issues:
            status = ParseStatus.SUCCESS
        elif format_seen and parsed_records:
            status = ParseStatus.PARTIAL_SUCCESS
        else:
            status = ParseStatus.FAILED
        return ParseResult(
            status=status,
            data=data,
            issues=issues,
            unparsed_ranges=[
                issue.source_reference
                for issue in issues
                if issue.source_reference is not None
            ],
            parser_metadata={
                "vendor": self.vendor,
                "os_family": self.os_family,
                "command": self.command,
                "parser_version": self.parser_version,
            },
            source_metadata=dict(context.source_metadata),
        )

    def _is_echo_or_prompt(self, line: str, context: ParseContext) -> bool:
        stripped = strip_cli_prompt(line).strip()
        return bool(
            not stripped
            or (context.command and stripped.casefold() == context.command.casefold())
            or re.fullmatch(r"[^\s#>]+[>#]", line.strip())
        )

    @staticmethod
    def _issue(
        context: ParseContext,
        line_number: int,
        code: str,
        message: str,
    ) -> ParseIssue:
        return ParseIssue(
            severity=IssueSeverity.WARNING,
            code=code,
            message=message,
            line_number=line_number,
            source_reference=_reference(context, line_number),
        )

    def _parse_routes(
        self, lines: list[str], context: ParseContext
    ) -> tuple[RoutingTable, list[ParseIssue], bool, int]:
        table = RoutingTable()
        issues: list[ParseIssue] = []
        format_seen = False
        parsed_records = 0
        in_code_legend = False

        for line_number, raw_line in enumerate(lines, start=1):
            line = strip_cli_prompt(raw_line).strip()
            if self._is_echo_or_prompt(raw_line, context):
                continue
            if not line:
                in_code_legend = False
                continue
            if line.startswith("Codes:"):
                in_code_legend = True
                format_seen = True
                continue
            if in_code_legend and (" - " in line or line.startswith("Codes:")):
                continue
            in_code_legend = False
            if line.casefold().startswith("gateway of last resort is "):
                format_seen = True
                continue

            continuation = _ROUTE_CONTINUATION.fullmatch(line)
            if continuation is not None:
                if not table.routes:
                    issues.append(
                        self._issue(
                            context,
                            line_number,
                            "ORPHAN_ROUTE_CONTINUATION",
                            "ECMP continuation has no preceding route.",
                        )
                    )
                    continue
                try:
                    next_hop = IPv4Address(continuation.group("next_hop"))
                except ValueError as exc:
                    issues.append(
                        self._issue(
                            context,
                            line_number,
                            "INVALID_ROUTE_NEXT_HOP",
                            f"Invalid IPv4 next hop: {exc}",
                        )
                    )
                    continue
                current = table.routes[-1]
                current.next_hops.append(next_hop)
                current.administrative_distance = int(continuation.group("distance"))
                current.metric = int(continuation.group("metric"))
                self._append_route_tail(current, continuation.group("rest"))
                parsed_records += 1
                format_seen = True
                continue

            match = _ROUTE_LINE.fullmatch(line)
            if match is None:
                issues.append(
                    self._issue(
                        context,
                        line_number,
                        "UNSUPPORTED_ROUTE_LINE",
                        (
                            "Line is not a recognized Cisco IPv4 route or "
                            "route-table heading."
                        ),
                    )
                )
                continue
            try:
                network = IPv4Network(match.group("network"), strict=True)
                route = self._parse_route_details(
                    network,
                    match.group("protocol"),
                    match.group("details"),
                )
            except ValueError as exc:
                issues.append(
                    self._issue(
                        context,
                        line_number,
                        "INVALID_ROUTE",
                        f"Route line could not be parsed: {exc}",
                    )
                )
                continue
            route.attributes["source_reference"] = _reference(context, line_number)
            table.routes.append(route)
            parsed_records += 1
            format_seen = True
        return table, issues, format_seen, parsed_records

    @staticmethod
    def _parse_route_details(
        network: IPv4Network, protocol: str, details: str
    ) -> Route:
        route = Route(network=network, protocol=" ".join(protocol.split()))
        distance_metric = re.match(r"^\[(\d+)/(\d+)\]\s+", details)
        if distance_metric is not None:
            route.administrative_distance = int(distance_metric.group(1))
            route.metric = int(distance_metric.group(2))
            details = details[distance_metric.end() :]

        direct = _DIRECT.search(details)
        if direct is not None:
            route.outgoing_interfaces.append(direct.group("interface"))
            return route

        via = _VIA.search(details)
        if via is None:
            raise ValueError("expected a next-hop or directly-connected route")
        route.next_hops.append(IPv4Address(via.group(1)))
        CiscoOperationalParser._append_route_tail(route, via.group(2))
        return route

    @staticmethod
    def _append_route_tail(route: Route, tail: str) -> None:
        components = [
            component.strip() for component in tail.split(",") if component.strip()
        ]
        if not components:
            return
        if re.fullmatch(r"\d{2}:\d{2}:\d{2}", components[0]):
            route.attributes["age"] = components.pop(0)
        if components:
            route.outgoing_interfaces.append(", ".join(components))

    def _parse_interface_status(
        self, lines: list[str], context: ParseContext
    ) -> tuple[list[Interface], list[ParseIssue], bool, int]:
        interfaces: list[Interface] = []
        issues: list[ParseIssue] = []
        format_seen = False
        parsed_records = 0

        for line_number, raw_line in enumerate(lines, start=1):
            line = strip_cli_prompt(raw_line).strip()
            if self._is_echo_or_prompt(raw_line, context):
                continue
            if not line or re.fullmatch(r"[-\s]+", line):
                continue
            if re.match(
                r"^Port\s+Name\s+Status\s+Vlan\s+Duplex\s+Speed(?:\s+Type)?$",
                line,
                re.IGNORECASE,
            ):
                format_seen = True
                continue
            tokens = line.split()
            if tokens and tokens[0].casefold() == "port":
                format_seen = True
                continue
            status_index = next(
                (
                    index
                    for index, token in enumerate(tokens[1:], start=1)
                    if token.casefold() in _INTERFACE_STATUSES
                ),
                None,
            )
            if status_index is None or len(tokens) < status_index + 4:
                issues.append(
                    self._issue(
                        context,
                        line_number,
                        "UNSUPPORTED_INTERFACE_STATUS_LINE",
                        "Line does not match the supported interface-status columns.",
                    )
                )
                continue
            name = tokens[0]
            interface_name = " ".join(tokens[1:status_index])
            status, vlan, duplex, speed = tokens[status_index : status_index + 4]
            interface_type = " ".join(tokens[status_index + 4 :]) or None
            if not name or not re.fullmatch(r"[\w./:-]+", name):
                issues.append(
                    self._issue(
                        context,
                        line_number,
                        "INVALID_INTERFACE_NAME",
                        "Interface name is missing or malformed.",
                    )
                )
                continue
            if status.casefold() not in _INTERFACE_STATUSES:
                issues.append(
                    self._issue(
                        context,
                        line_number,
                        "UNSUPPORTED_INTERFACE_STATUS",
                        f"Unsupported interface status {status!r}.",
                    )
                )
                continue
            attributes: dict[str, object] = {
                "vlan": vlan,
                "duplex": duplex,
                "speed": speed,
            }
            if interface_name:
                attributes["name"] = interface_name
            if interface_type:
                attributes["type"] = interface_type
            interface = Interface(
                name=name,
                operational_status=status,
                mode="trunk" if vlan.casefold() == "trunk" else None,
                attributes=attributes,
            )
            interface.attributes["source_reference"] = _reference(context, line_number)
            if vlan.isdecimal():
                interface.access_vlan = int(vlan)
            interfaces.append(interface)
            parsed_records += 1
            format_seen = True
        return interfaces, issues, format_seen, parsed_records

    def _parse_interface_brief(
        self, lines: list[str], context: ParseContext
    ) -> tuple[list[Interface], list[ParseIssue], bool, int]:
        interfaces: list[Interface] = []
        issues: list[ParseIssue] = []
        format_seen = False
        parsed_records = 0

        for line_number, raw_line in enumerate(lines, start=1):
            line = strip_cli_prompt(raw_line).strip()
            if self._is_echo_or_prompt(raw_line, context):
                continue
            if not line or re.fullmatch(r"[-\s]+", line):
                continue
            if re.match(
                r"^Interface\s+IP-Address\s+OK\?\s+Method\s+Status\s+Protocol$",
                line,
                re.IGNORECASE,
            ):
                format_seen = True
                continue
            columns = line.split()
            if columns and columns[0].casefold() == "interface":
                format_seen = True
                continue
            if len(columns) < 6:
                issues.append(
                    self._issue(
                        context,
                        line_number,
                        "UNSUPPORTED_INTERFACE_BRIEF_LINE",
                        "Line does not contain all supported interface-brief columns.",
                    )
                )
                continue
            name, address_text, ok_value, method = columns[:4]
            status = " ".join(columns[4:-1])
            protocol = columns[-1]
            address: IPv4Address | None = None
            if address_text.casefold() != "unassigned":
                try:
                    address = IPv4Address(address_text)
                except ValueError as exc:
                    issues.append(
                        self._issue(
                            context,
                            line_number,
                            "INVALID_INTERFACE_IPV4_ADDRESS",
                            f"Invalid interface IPv4 address: {exc}",
                        )
                    )
                    continue
            interface = Interface(
                name=name,
                ipv4_address=address,
                operational_status=status,
                admin_status=(
                    "down" if status.casefold() == "administratively down" else None
                ),
                attributes={
                    "ok": ok_value,
                    "method": method,
                    "protocol": protocol,
                },
            )
            interface.attributes["source_reference"] = _reference(context, line_number)
            interfaces.append(interface)
            parsed_records += 1
            format_seen = True
        return interfaces, issues, format_seen, parsed_records

    def _parse_vlans(
        self, lines: list[str], context: ParseContext
    ) -> tuple[list[VLAN], list[ParseIssue], bool, int]:
        vlans: list[VLAN] = []
        issues: list[ParseIssue] = []
        format_seen = False
        parsed_records = 0

        for line_number, raw_line in enumerate(lines, start=1):
            line = strip_cli_prompt(raw_line).strip()
            if self._is_echo_or_prompt(raw_line, context):
                continue
            if not line or re.fullmatch(r"[-\s]+", line):
                continue
            if re.match(
                r"^VLAN\s+Name\s+Status\s+Ports$",
                line,
                re.IGNORECASE,
            ):
                format_seen = True
                continue
            columns = line.split(maxsplit=3)
            if columns and columns[0].casefold() == "vlan":
                format_seen = True
                continue
            if columns and columns[0].isdecimal():
                if len(columns) < 3:
                    issues.append(
                        self._issue(
                            context,
                            line_number,
                            "MALFORMED_VLAN_ROW",
                            "VLAN row is missing its name or status.",
                        )
                    )
                    continue
                vlan_id = int(columns[0])
                if not 1 <= vlan_id <= 4094:
                    issues.append(
                        self._issue(
                            context,
                            line_number,
                            "INVALID_VLAN_ID",
                            f"VLAN ID {vlan_id} is outside the supported range.",
                        )
                    )
                    continue
                ports = self._split_vlan_ports(columns[3] if len(columns) > 3 else "")
                vlan = VLAN(
                    vlan_id=vlan_id,
                    name=columns[1],
                    status=columns[2],
                    interfaces=ports,
                    attributes={"source_reference": _reference(context, line_number)},
                )
                vlans.append(vlan)
                parsed_records += 1
                format_seen = True
                continue

            if (
                vlans
                and raw_line[:1].isspace()
                and _INTERFACE_NAME.fullmatch(line.split(",", maxsplit=1)[0].strip())
            ):
                vlans[-1].interfaces.extend(self._split_vlan_ports(line))
                vlans[-1].attributes.setdefault("continuation_lines", []).append(
                    _reference(context, line_number)
                )
                parsed_records += 1
                format_seen = True
                continue
            issues.append(
                self._issue(
                    context,
                    line_number,
                    "UNSUPPORTED_VLAN_LINE",
                    "Line is not a supported VLAN row or port continuation.",
                )
            )
        return vlans, issues, format_seen, parsed_records

    @staticmethod
    def _split_vlan_ports(value: str) -> list[str]:
        return [port for port in re.split(r"[\s,]+", value.strip()) if port]


_VRF_HEADING = re.compile(r"^Routing Table:\s*(?P<name>\S+)\s*$")
_VRF_HEADING_LOOSE = re.compile(r"^Routing Table\b", re.IGNORECASE)
_VRF_COMMAND = re.compile(
    r"^show\s+ip\s+route\s+vrf\s+(?P<name>\S+)\s*$", re.IGNORECASE
)


class CiscoShowIpRouteVrfParser(Parser):
    """Parses one named VRF (``vrf NAME``) or all VRFs (``vrf *``)."""

    vendor = "Cisco"
    command = "show ip route vrf *"
    parser_version = "1.0"
    implementation_status = ImplementationStatus.TESTED_WITH_SYNTHETIC_DATA

    def __init__(self, os_family: str) -> None:
        normalized = " ".join(os_family.casefold().split())
        if normalized not in {"ios", "ios xe"}:
            raise ValueError(f"Unsupported Cisco OS family: {os_family}")
        self.os_family = "IOS XE" if normalized == "ios xe" else "IOS"
        self._routes = CiscoOperationalParser("show ip route", self.os_family)

    def matches_command(self, command: str) -> bool:
        return _VRF_COMMAND.fullmatch(" ".join(command.split())) is not None

    def parse(self, text: str, context: ParseContext) -> ParseResult:
        command = " ".join((context.command or "").split())
        match = _VRF_COMMAND.fullmatch(command)
        requested = match.group("name") if match else None
        lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        issues: list[ParseIssue] = []
        tables: list[RoutingTable] = []
        records = 0
        format_seen = False

        if not text.strip():
            issues.append(
                ParseIssue(
                    severity=IssueSeverity.ERROR,
                    code="EMPTY_INPUT",
                    message="Input contains no command output.",
                )
            )
        else:
            for kind, name, start, end in self._sections(lines, issues, context):
                section = [""] * len(lines)
                first = start if kind == "pre" else start + 1
                section[first:end] = lines[first:end]
                table, section_issues, seen, count = self._routes._parse_routes(
                    section, context
                )
                if kind == "pre" and not (table.routes or section_issues or seen):
                    continue
                issues.extend(section_issues)
                format_seen = format_seen or seen or kind != "pre"
                records += count
                scope_name, scope = self._scope(
                    kind, name, requested, start, issues, context
                )
                self._apply(table, scope_name, scope, command, start, end, context)
                tables.append(table)

        blocking = [i for i in issues if i.severity is not IssueSeverity.INFO]
        if format_seen and not blocking:
            status = "SUCCESS"
        elif format_seen and records:
            status = "PARTIAL_SUCCESS"
        else:
            status = "FAILED"
        return self._result(status, tables, issues, context)

    @staticmethod
    def _sections(
        lines: list[str], issues: list[ParseIssue], context: ParseContext
    ) -> list[tuple[str, str | None, int, int]]:
        """Return (kind, heading name, start, end); kind is pre/named/broken."""
        headings: list[tuple[str, str | None, int]] = []
        for index, raw in enumerate(lines):
            stripped = raw.strip()
            matched = _VRF_HEADING.fullmatch(stripped)
            if matched is not None:
                headings.append(("named", matched.group("name"), index))
            elif _VRF_HEADING_LOOSE.match(stripped):
                headings.append(("broken", None, index))
                issues.append(
                    ParseIssue(
                        severity=IssueSeverity.WARNING,
                        code="VRF_HEADING_UNPARSEABLE",
                        message=(
                            "VRF heading could not be parsed; VRF membership of "
                            "the following routes is unknown."
                        ),
                        line_number=index + 1,
                        source_reference=_vrf_reference(context, index + 1, index + 1),
                    )
                )
        first = headings[0][2] if headings else len(lines)
        sections: list[tuple[str, str | None, int, int]] = [("pre", None, 0, first)]
        for position, (kind, name, index) in enumerate(headings):
            end = (
                headings[position + 1][2]
                if position + 1 < len(headings)
                else len(lines)
            )
            sections.append((kind, name, index, end))
        return sections

    @staticmethod
    def _scope(
        kind: str,
        heading: str | None,
        requested: str | None,
        start: int,
        issues: list[ParseIssue],
        context: ParseContext,
    ) -> tuple[str | None, VrfScope]:
        def warn(code: str, message: str) -> None:
            issues.append(
                ParseIssue(
                    severity=IssueSeverity.WARNING,
                    code=code,
                    message=message,
                    line_number=start + 1,
                    source_reference=_vrf_reference(context, start + 1, start + 1),
                )
            )

        if kind == "broken":
            return None, VrfScope.UNKNOWN
        if kind == "pre":
            if requested not in (None, "*"):
                return requested, VrfScope.NAMED
            warn(
                "VRF_CONTEXT_MISSING",
                "Routes appear before any VRF heading and the command names "
                "no single VRF; VRF membership is unknown.",
            )
            return None, VrfScope.UNKNOWN
        if requested not in (None, "*") and heading != requested:
            warn(
                "VRF_HEADING_MISMATCH",
                f"Heading names VRF {heading!r} but the command requested "
                f"{requested!r}; VRF membership is not assigned.",
            )
            return None, VrfScope.UNKNOWN
        return heading, VrfScope.NAMED

    @staticmethod
    def _apply(
        table: RoutingTable,
        name: str | None,
        scope: VrfScope,
        command: str,
        start: int,
        end: int,
        context: ParseContext,
    ) -> None:
        table.vrf, table.vrf_scope = name, scope
        table.source_command = command
        table.source_reference = _vrf_reference(context, start + 1, end)
        for route in table.routes:
            route.vrf, route.vrf_scope = name, scope

    def _result(
        self,
        status: str,
        tables: list[RoutingTable],
        issues: list[ParseIssue],
        context: ParseContext,
    ) -> ParseResult:
        return ParseResult(
            status=ParseStatus[status],
            data=tables,
            issues=issues,
            unparsed_ranges=[
                i.source_reference for i in issues if i.source_reference is not None
            ],
            parser_metadata={
                "vendor": self.vendor,
                "os_family": self.os_family,
                "command": self.command,
                "parser_version": self.parser_version,
            },
            source_metadata=dict(context.source_metadata),
        )


def _vrf_reference(context: ParseContext, start: int, end: int) -> SourceReference:
    return SourceReference(
        filename=context.filename,
        command=context.command,
        start_line=start,
        end_line=end,
    )
