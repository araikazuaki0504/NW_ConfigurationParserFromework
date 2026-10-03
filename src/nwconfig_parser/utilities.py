"""Reusable parsing helpers that do not encode vendor-specific semantics."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Interface, IPv4Network


def parse_ipv4_address(value: str) -> IPv4Address:
    return ipaddress.IPv4Address(value.strip())


def parse_ipv4_network(value: str, *, strict: bool = True) -> IPv4Network:
    return ipaddress.IPv4Network(value.strip(), strict=strict)


def parse_ipv4_interface(value: str) -> IPv4Interface:
    return ipaddress.IPv4Interface(value.strip())


def netmask_to_prefix(mask: str) -> int:
    return ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen


def normalize_mac_address(value: str) -> str:
    compact = re.sub(r"[^0-9A-Fa-f]", "", value)
    if len(compact) != 12 or not re.fullmatch(r"[0-9A-Fa-f]{12}", compact):
        raise ValueError(f"Invalid MAC address: {value!r}")
    return ":".join(compact[index : index + 2] for index in range(0, 12, 2)).lower()


def parse_integer(value: str) -> int:
    return int(value.strip(), 10)


def parse_quantity(value: str, units: dict[str, int]) -> int:
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([A-Za-z]+)?\s*", value)
    if match is None:
        raise ValueError(f"Invalid quantity: {value!r}")
    amount = float(match.group(1))
    unit = (match.group(2) or "").lower()
    multiplier = units.get(unit)
    if multiplier is None:
        raise ValueError(f"Unsupported unit {unit!r}")
    result = amount * multiplier
    if not result.is_integer():
        raise ValueError(f"Quantity {value!r} cannot be represented as an integer")
    return int(result)


def split_whitespace_table(
    lines: Iterable[str],
    *,
    minimum_columns: int = 1,
) -> list[list[str]]:
    rows = [line.split() for line in lines if line.strip()]
    return [row for row in rows if len(row) >= minimum_columns]


def strip_cli_prompt(line: str) -> str:
    return re.sub(r"^[^\s#>]+[>#]\s*", "", line)


def remove_command_echo(text: str, command: str) -> str:
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if strip_cli_prompt(line).strip() == command.strip():
            del lines[index]
            break
    return "\n".join(lines)


def remove_pager_markers(text: str) -> str:
    return re.sub(r"--\s*More\s*--|\x1b\[.*?[@-~]", "", text)


@dataclass(slots=True)
class IndentedLine:
    text: str
    line_number: int
    indent: int
    children: list[IndentedLine] = field(default_factory=list)


def parse_indented_hierarchy(text: str) -> list[IndentedLine]:
    roots: list[IndentedLine] = []
    stack: list[IndentedLine] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if not raw_line.strip():
            continue
        indent = len(raw_line) - len(raw_line.lstrip())
        node = IndentedLine(raw_line.strip(), line_number, indent)
        while stack and stack[-1].indent >= indent:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        stack.append(node)
    return roots


def extract_keyword(line: str, keyword: str) -> str | None:
    match = re.search(rf"(?:^|\s){re.escape(keyword)}(?:\s+(.+))?$", line.strip())
    return match.group(1) if match else None


def extract_blocks(text: str, header_pattern: str) -> list[tuple[int, list[str]]]:
    pattern = re.compile(header_pattern)
    blocks: list[tuple[int, list[str]]] = []
    current: list[str] | None = None
    start_line = 0
    for number, line in enumerate(text.splitlines(), start=1):
        if pattern.match(line):
            if current is not None:
                blocks.append((start_line, current))
            current = [line]
            start_line = number
        elif current is not None:
            current.append(line)
    if current is not None:
        blocks.append((start_line, current))
    return blocks
