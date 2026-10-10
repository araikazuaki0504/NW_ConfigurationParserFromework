"""Command-line interface for parser execution and registry discovery."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields, is_dataclass
from enum import Enum
from ipaddress import IPv4Address, IPv4Interface, IPv4Network
from pathlib import Path
from typing import Any, TextIO

from nwconfig_parser.core.engine import ParserEngine
from nwconfig_parser.input import load_text_file
from nwconfig_parser.models import ParseResult, ParseStatus
from nwconfig_parser.parsers.a10_acos_config import A10AcosConfigParser
from nwconfig_parser.parsers.a10_operational import (
    A10ShowInterfacesParser,
    A10ShowIpRouteParser,
)
from nwconfig_parser.parsers.base import ParseContext, ParserSelectionError
from nwconfig_parser.parsers.cisco_config import CiscoConfigParser
from nwconfig_parser.parsers.cisco_operational import (
    CiscoOperationalParser,
    CiscoShowIpRouteVrfParser,
)
from nwconfig_parser.parsers.example import ExampleTextParser
from nwconfig_parser.parsers.fortinet_fortios_config import FortinetFortiosConfigParser
from nwconfig_parser.parsers.fortinet_operational import (
    FortinetRoutingTableAllParser,
    FortinetSystemInterfaceParser,
)
from nwconfig_parser.parsers.hpe_comware_config import HpeComwareConfigParser
from nwconfig_parser.parsers.hpe_comware_operational import (
    HpeComwareDisplayInterfaceParser,
    HpeComwareDisplayIpRoutingTableParser,
    HpeComwareDisplayIpRoutingTableVpnParser,
)
from nwconfig_parser.parsers.registry import ParserRegistry
from nwconfig_parser.parsers.yamaha_operational import YamahaShowIpRouteParser
from nwconfig_parser.parsers.yamaha_rtx_config import YamahaRtxConfigParser


def _json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (IPv4Address, IPv4Interface, IPv4Network)):
        return str(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: getattr(value, item.name) for item in fields(value)}
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def build_registry() -> ParserRegistry:
    registry = ParserRegistry()
    registry.register(ExampleTextParser())
    for os_family in ("IOS", "IOS XE"):
        registry.register(CiscoConfigParser(os_family))
    for os_family in ("IOS", "IOS XE"):
        for command in (
            "show ip route",
            "show interfaces status",
            "show ip interface brief",
            "show vlan brief",
        ):
            registry.register(CiscoOperationalParser(command, os_family))
        registry.register(CiscoShowIpRouteVrfParser(os_family))
    for parser_class in (
        YamahaShowIpRouteParser,
        FortinetRoutingTableAllParser,
        FortinetSystemInterfaceParser,
        A10ShowIpRouteParser,
        A10ShowInterfacesParser,
        HpeComwareDisplayIpRoutingTableParser,
        HpeComwareDisplayInterfaceParser,
        HpeComwareDisplayIpRoutingTableVpnParser,
        YamahaRtxConfigParser,
        FortinetFortiosConfigParser,
        A10AcosConfigParser,
        HpeComwareConfigParser,
    ):
        registry.register(parser_class())
    return registry


def _write_json(value: object, destination: str | None, stdout: TextIO) -> None:
    rendered = json.dumps(value, default=_json_default, ensure_ascii=False, indent=2)
    if destination:
        Path(destination).write_text(rendered + "\n", encoding="utf-8")
    else:
        stdout.write(rendered + "\n")


def _add_parser_selector(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--vendor", required=True)
    parser.add_argument("--os", "--os-family", dest="os_family", required=True)
    parser.add_argument("--version", "--os-version", dest="os_version")
    parser.add_argument("--command", required=True)
    parser.add_argument("--input", dest="input_file", required=True)
    parser.add_argument("--output")


def _context_from_args(
    args: argparse.Namespace, metadata: dict[str, str]
) -> ParseContext:
    return ParseContext(
        vendor=args.vendor,
        os_family=args.os_family,
        os_version=args.os_version,
        command=args.command,
        filename=args.input_file,
        source_metadata=metadata,
    )


def _exit_code(status: ParseStatus) -> int:
    if status is ParseStatus.SUCCESS:
        return 0
    if status is ParseStatus.PARTIAL_SUCCESS:
        return 1
    return 2


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nwconfig-parser",
        description="Parse captured network-device text into structured JSON.",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)

    parse_command = subparsers.add_parser("parse", help="Parse an input file")
    _add_parser_selector(parse_command)

    validate_command = subparsers.add_parser(
        "validate", help="Parse an input file and report completeness"
    )
    _add_parser_selector(validate_command)

    list_command = subparsers.add_parser("list", help="List available parsers")
    list_command.add_argument("--vendor")
    list_command.add_argument("--os", "--os-family", dest="os_family")

    info_command = subparsers.add_parser("info", help="Show parser metadata")
    info_command.add_argument("--vendor", required=True)
    info_command.add_argument("--os", "--os-family", dest="os_family", required=True)
    info_command.add_argument("--version", "--os-version", dest="os_version")
    info_command.add_argument("--command", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_argument_parser().parse_args(argv)
    registry = build_registry()

    try:
        if args.action == "list":
            _write_json(
                registry.list_descriptions(
                    vendor=args.vendor,
                    os_family=args.os_family,
                ),
                None,
                sys.stdout,
            )
            return 0

        if args.action == "info":
            context = ParseContext(
                vendor=args.vendor,
                os_family=args.os_family,
                os_version=args.os_version,
                command=args.command,
            )
            parser = registry.select(context)
            _write_json(registry.describe(parser), None, sys.stdout)
            return 0

        document = load_text_file(args.input_file)
        context = _context_from_args(args, document.source_metadata)
        engine = ParserEngine(registry)
        result: ParseResult
        if args.action == "validate":
            result = engine.validate(document.normalized_text, context)
            parser = registry.select(context)
            payload: object = {
                "status": result.status,
                "issues": result.issues,
                "unparsed_ranges": result.unparsed_ranges,
                "parser": registry.describe(parser),
            }
        else:
            result = engine.parse(document.normalized_text, context)
            payload = result
        _write_json(payload, args.output, sys.stdout)
        return _exit_code(result.status)
    except (OSError, UnicodeError, ParserSelectionError, ValueError) as exc:
        print(f"nwconfig-parser: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
