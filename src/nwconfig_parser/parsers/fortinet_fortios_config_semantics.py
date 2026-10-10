"""Semantic interpretation of vendor configuration into common models.

Interpreted: ``config system global`` hostname, ``config system interface``
(ip, description, status, alias, vdom) and ``config router static``.
The official CLI reference could not be retrieved while this was written, so
only the documented "Basic configuration" examples are doc-backed; see
docs/vendor-config-support.md. VDOM is not VRF and is never mapped to one.
"""

from __future__ import annotations

from ipaddress import IPv4Network

from nwconfig_parser.models import (
    ConfigNode,
    Interface,
    Route,
    SemanticStatus,
)
from nwconfig_parser.parsers.vendor_config_base import (
    ConfigInterpreter,
    Reject,
    make_interface,
    make_network,
    mask_to_prefix,
    parse_ipv4,
)

_ROUTE_FIELDS = {"dst", "gateway", "device", "distance", "priority", "status"}
_ROUTE_BLOCKING = {
    "dstaddr",
    "internet-service",
    "internet-service-custom",
    "blackhole",
    "vrf",
    "sdwan-zone",
    "dynamic-gateway",
}


def _scan(text: str) -> tuple[list[str], bool]:
    """Split FortiOS text into tokens; the flag is True if a quote is open."""
    tokens: list[str] = []
    current: list[str] = []
    quoted = False
    started = False
    index = 0
    while index < len(text):
        char = text[index]
        if quoted:
            if char == "\\" and index + 1 < len(text):
                index += 1
                current.append(text[index])
            elif char == '"':
                quoted = False
            else:
                current.append(char)
        elif char == '"':
            quoted = True
            started = True
        elif char.isspace():
            if current or started:
                tokens.append("".join(current))
                current, started = [], False
        else:
            current.append(char)
        index += 1
    if current or started:
        tokens.append("".join(current))
    return tokens, quoted


def _statement(node: ConfigNode) -> str:
    return "\n".join([node.command, *node.body_lines])


def _tokens(node: ConfigNode) -> list[str]:
    tokens, open_quote = _scan(_statement(node))
    if open_quote:
        raise Reject("Unterminated quoted string.", invalid=True)
    return tokens


def _keyword(command: str) -> str:
    parts = command.split(None, 1)
    return parts[0].casefold() if parts else ""


class FortinetFortiosConfigInterpreter(ConfigInterpreter):
    def handle_root(self, node: ConfigNode) -> None:
        keyword = _keyword(node.command)
        if keyword in {"next", "end"}:
            raise Reject("Block terminator without an open block.", invalid=True)
        if keyword != "config":
            raise Reject("Statement outside a config block is not supported.")
        section = tuple(token.casefold() for token in _tokens(node)[1:])
        if section == ("system", "global"):
            self.parsed(node)
            self._children(node, self._global_child)
        elif section == ("system", "interface"):
            self.parsed(node)
            self._children(node, self._interface_edit)
        elif section == ("router", "static"):
            self.parsed(node)
            self._children(node, self._route_edit)
        else:
            raise Reject("Configuration section is not supported.")

    def _children(self, node: ConfigNode, handler: object) -> None:
        for child in node.children:
            self.run_child(child, handler)

    def _global_child(self, node: ConfigNode) -> None:
        tokens = _tokens(node)
        if node.children or len(tokens) < 2 or tokens[0].casefold() != "set":
            raise Reject("Statement is not supported by this parser.")
        if tokens[1].casefold() != "hostname":
            raise Reject("Global setting is not supported by this parser.")
        if len(tokens) != 3 or not tokens[2]:
            raise Reject("Invalid hostname statement.", invalid=True)
        self.document.hostname = tokens[2]
        self.parsed(node)

    def _interface_edit(self, node: ConfigNode) -> None:
        tokens = _tokens(node)
        if _keyword(node.command) != "edit" or len(tokens) != 2 or not tokens[1]:
            raise Reject("Statement is not supported by this parser.")
        entry = self.interface(tokens[1])
        self.parsed(node)
        for child in node.children:
            self.run_child(child, lambda item: self._interface_set(entry, item))

    def _interface_set(self, entry: Interface, node: ConfigNode) -> None:
        tokens = _tokens(node)
        if node.children or len(tokens) < 3 or tokens[0].casefold() != "set":
            raise Reject("Statement is not supported by this parser.")
        key = tokens[1].casefold()
        if key == "ip" and len(tokens) == 4:
            if tokens[2] == "0.0.0.0" and tokens[3] == "0.0.0.0":
                entry.ipv4_addresses = []
            else:
                entry.ipv4_addresses = [
                    make_interface(tokens[2], mask_to_prefix(tokens[3]))
                ]
        elif key == "description" and len(tokens) == 3:
            entry.description = tokens[2]
        elif key == "status" and len(tokens) == 3 and tokens[2] in {"up", "down"}:
            entry.admin_status = tokens[2]
        elif key == "alias" and len(tokens) == 3:
            entry.attributes["alias"] = tokens[2]
        elif key == "vdom" and len(tokens) == 3:
            entry.attributes["vdom"] = tokens[2]
        elif key == "vrf":
            raise Reject("VRF syntax is not verified; VDOM is not VRF.")
        else:
            raise Reject("Interface setting is not supported by this parser.")
        self.parsed(node)

    def _route_edit(self, node: ConfigNode) -> None:
        tokens = _tokens(node)
        if _keyword(node.command) != "edit" or len(tokens) != 2 or not tokens[1]:
            raise Reject("Statement is not supported by this parser.")
        for child in node.children:
            child_tokens, _ = _scan(_statement(child))
            if len(child_tokens) > 1 and child_tokens[1].casefold() in _ROUTE_BLOCKING:
                raise Reject("Route uses destination syntax that is not supported.")
        fields: dict[str, list[str]] = {}
        self.parsed(node)
        for child in node.children:
            self.run_child(child, lambda item: self._route_set(fields, item))
        failed = any(
            child.semantic_status is not SemanticStatus.PARSED
            and _field_key(child) in _ROUTE_FIELDS
            for child in node.children
        )
        if failed:
            self.reject(
                node,
                "Route entry omitted because a field could not be interpreted.",
                reject_children=False,
                code="ROUTE_ENTRY_OMITTED",
            )
            self.parsed_count -= 1
            return
        network = (
            _dst_network(fields["dst"])
            if "dst" in fields
            else make_network("0.0.0.0", 0)
        )
        route = Route(
            network=network,
            protocol="static",
            next_hops=[parse_ipv4(fields["gateway"][0])] if "gateway" in fields else [],
            outgoing_interfaces=fields.get("device", []),
            administrative_distance=_optional_int(fields, "distance"),
            attributes={"line_number": node.line_number, "seq_number": tokens[1]},
        )
        if "priority" in fields:
            route.attributes["priority"] = _optional_int(fields, "priority")
        if "status" in fields:
            route.attributes["status"] = fields["status"][0]
        if "comment" in fields:
            route.attributes["comment"] = fields["comment"][0]
        self.document.static_routes.append(route)

    def _route_set(self, fields: dict[str, list[str]], node: ConfigNode) -> None:
        tokens = _tokens(node)
        if node.children or len(tokens) < 3 or tokens[0].casefold() != "set":
            raise Reject("Statement is not supported by this parser.")
        key = tokens[1].casefold()
        values = tokens[2:]
        if key == "dst" and len(values) in {1, 2}:
            _dst_network(values)
        elif key == "gateway" and len(values) == 1:
            parse_ipv4(values[0])
        elif key == "device" and len(values) == 1:
            pass
        elif key in {"distance", "priority"} and len(values) == 1:
            if not values[0].isdigit():
                raise Reject("Invalid numeric value.", invalid=True)
        elif key == "status" and values in (["enable"], ["disable"]):
            pass
        elif key == "comment" and len(values) == 1:
            pass
        else:
            raise Reject("Route setting is not supported by this parser.")
        fields[key] = values
        self.parsed(node)


def _optional_int(fields: dict[str, list[str]], key: str) -> int | None:
    return int(fields[key][0]) if key in fields else None


def _field_key(node: ConfigNode) -> str:
    tokens, _ = _scan(_statement(node))
    return tokens[1].casefold() if len(tokens) > 1 else ""


def _dst_network(values: list[str]) -> IPv4Network:
    if len(values) == 2:
        return make_network(values[0], mask_to_prefix(values[1]))
    address, _, mask = values[0].partition("/")
    return make_network(address, mask_to_prefix(mask) if mask else 32)
