"""Shared, vendor-neutral data models for parser implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from ipaddress import IPv4Address, IPv4Interface, IPv4Network
from typing import Any


class ParseStatus(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    FAILED = "FAILED"


class ImplementationStatus(str, Enum):
    IMPLEMENTED = "IMPLEMENTED"
    TESTED_WITH_SYNTHETIC_DATA = "TESTED_WITH_SYNTHETIC_DATA"
    VERIFIED_WITH_DEVICE_OUTPUT = "VERIFIED_WITH_DEVICE_OUTPUT"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"


class LinkStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    UNKNOWN = "UNKNOWN"
    INFERRED = "INFERRED"


class SemanticStatus(str, Enum):
    """Whether a config line was semantically interpreted, not just stored."""

    STRUCTURE_ONLY = "STRUCTURE_ONLY"
    PARSED = "PARSED"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID = "INVALID"


class EvaluationStatus(str, Enum):
    EVALUATED = "EVALUATED"
    INDETERMINATE = "INDETERMINATE"


class IssueSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class SourceReference:
    filename: str | None = None
    command: str | None = None
    start_line: int | None = None
    end_line: int | None = None


@dataclass(frozen=True, slots=True)
class ParseIssue:
    severity: IssueSeverity
    code: str
    message: str
    line_number: int | None = None
    source_reference: SourceReference | None = None


@dataclass(slots=True)
class ParseResult:
    status: ParseStatus
    data: Any = None
    issues: list[ParseIssue] = field(default_factory=list)
    unparsed_ranges: list[SourceReference] = field(default_factory=list)
    parser_metadata: dict[str, str] = field(default_factory=dict)
    source_metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VerificationResult:
    status: CheckStatus
    message: str
    evidence: SourceReference | None = None


@dataclass(slots=True)
class Device:
    hostname: str | None = None
    vendor: str | None = None
    os_family: str | None = None
    os_version: str | None = None
    model: str | None = None
    serial_number: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Interface:
    name: str
    description: str | None = None
    admin_status: str | None = None
    operational_status: str | None = None
    ipv4_addresses: list[IPv4Interface] = field(default_factory=list)
    ipv4_address: IPv4Address | None = None
    mac_address: str | None = None
    mtu: int | None = None
    speed: str | None = None
    duplex: str | None = None
    mode: str | None = None
    access_vlan: int | None = None
    trunk_vlans: list[int] = field(default_factory=list)
    channel_group: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class VLAN:
    vlan_id: int
    name: str | None = None
    status: str | None = None
    interfaces: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Route:
    network: IPv4Network
    protocol: str
    next_hops: list[IPv4Address] = field(default_factory=list)
    outgoing_interfaces: list[str] = field(default_factory=list)
    administrative_distance: int | None = None
    metric: int | None = None
    vrf: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RoutingTable:
    device: Device | None = None
    vrf: str | None = None
    routes: list[Route] = field(default_factory=list)


@dataclass(slots=True)
class VRF:
    name: str
    description: str | None = None
    route_distinguisher: str | None = None
    interfaces: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OSPFNeighbor:
    neighbor_id: str
    address: IPv4Address | None = None
    state: str | None = None
    interface: str | None = None
    priority: int | None = None


@dataclass(slots=True)
class OSPFProcess:
    process_id: str
    router_id: IPv4Address | None = None
    areas: list[str] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    interfaces: list[str] = field(default_factory=list)
    neighbors: list[OSPFNeighbor] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class BGPPeer:
    neighbor_address: IPv4Address
    remote_as: int | None = None
    local_as: int | None = None
    state: str | None = None
    description: str | None = None
    route_map_in: str | None = None
    route_map_out: str | None = None
    prefix_list_in: str | None = None
    prefix_list_out: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ACL:
    name: str
    type: str | None = None
    entries: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NATRule:
    rule_id: str
    source: str | None = None
    destination: str | None = None
    translation: str | None = None
    interface: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class VPN:
    name: str
    peer: str | None = None
    ike_version: str | None = None
    encryption: str | None = None
    authentication: str | None = None
    status: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PrefixListEntry:
    sequence: int | None
    action: str
    prefix: IPv4Network
    ge: int | None = None
    le: int | None = None
    source_reference: SourceReference | None = None


@dataclass(slots=True)
class PrefixList:
    name: str
    entries: list[PrefixListEntry] = field(default_factory=list)
    description: str | None = None
    complete: bool = True
    incomplete_reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RouteMapEntry:
    sequence: int
    action: str
    match_conditions: tuple[str, ...] = ()
    set_actions: tuple[str, ...] = ()
    prefix_list_references: tuple[str, ...] = ()
    acl_references: tuple[str, ...] = ()
    source_reference: SourceReference | None = None


@dataclass(slots=True)
class RouteMap:
    name: str
    entries: list[RouteMapEntry] = field(default_factory=list)
    complete: bool = True
    incomplete_reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TopologyNode:
    device_id: str
    hostname: str | None = None
    vendor: str | None = None
    model: str | None = None
    interfaces: list[str] = field(default_factory=list)


@dataclass(slots=True)
class TopologyLink:
    source_device: str | None = None
    source_interface: str | None = None
    target_device: str | None = None
    target_interface: str | None = None
    evidence: list[SourceReference] = field(default_factory=list)
    confidence: float | None = None
    status: LinkStatus = LinkStatus.UNKNOWN

    def __post_init__(self) -> None:
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        if self.status is LinkStatus.CONFIRMED and (
            self.source_device is None or self.target_device is None
        ):
            raise ValueError("confirmed links require both endpoint devices")


@dataclass(slots=True)
class LogicalDomain:
    domain_type: str
    domain_id: str
    member_devices: list[str] = field(default_factory=list)
    member_interfaces: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BGPNetwork:
    network: IPv4Network
    address_family: str = "ipv4"
    route_map: str | None = None
    line_number: int | None = None


@dataclass(slots=True)
class BGPProcess:
    asn: int
    router_id: IPv4Address | None = None
    peers: list[BGPPeer] = field(default_factory=list)
    networks: list[BGPNetwork] = field(default_factory=list)
    address_families: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class UnsupportedConfigLine:
    line_number: int
    raw_text: str
    reason: str
    parent: str | None = None


@dataclass(frozen=True, slots=True)
class ConfigReference:
    """A reference between configuration objects (e.g. route-map -> prefix-list).

    resolved is None when the target type is not parsed by this framework.
    """

    source_type: str
    source_name: str
    relation: str
    target_type: str
    target_name: str
    line_number: int | None = None
    resolved: bool | None = None
    source_sequence: int | None = None


@dataclass(slots=True)
class ConfigNode:
    command: str
    negated: bool
    line_number: int
    raw_text: str
    children: list[ConfigNode] = field(default_factory=list)
    semantic_status: SemanticStatus = SemanticStatus.STRUCTURE_ONLY
    # Set only for multi-line constructs (banner, macro) whose body is not
    # indentation-structured. body_lines never become child commands.
    end_line: int | None = None
    body_lines: list[str] = field(default_factory=list)
    terminated: bool = True


@dataclass(frozen=True, slots=True)
class Banner:
    kind: str
    text: str
    source_reference: SourceReference | None = None


@dataclass(slots=True)
class ConfigDocument:
    raw_text: str
    roots: list[ConfigNode] = field(default_factory=list)
    hostname: str | None = None
    prefix_lists: list[PrefixList] = field(default_factory=list)
    interfaces: list[Interface] = field(default_factory=list)
    vlans: list[VLAN] = field(default_factory=list)
    ip_routing: bool | None = None
    static_routes: list[Route] = field(default_factory=list)
    ospf_processes: list[OSPFProcess] = field(default_factory=list)
    bgp_processes: list[BGPProcess] = field(default_factory=list)
    route_maps: list[RouteMap] = field(default_factory=list)
    unsupported_lines: list[UnsupportedConfigLine] = field(default_factory=list)
    banners: list[Banner] = field(default_factory=list)
    references: list[ConfigReference] = field(default_factory=list)
