"""Per-VRF route queries, route diffs and VRF reference checks.

Rules (see docs/vrf-support.md):

* A query never mixes VRFs. Omitting ``vrf``/``vrf_id`` selects the default
  (global) table only.
* A VRF name and a numeric VRF ID are different identifiers; both given at
  once is rejected because the mapping between them is not known.
* Routes whose VRF membership is unknown (``VrfScope.UNKNOWN``) are never
  assumed to belong to, or not to belong to, any VRF. If such a route could
  affect the answer the result is ``INDETERMINATE``.
* ``NOT_FOUND`` is only returned when a complete routing table for the
  requested VRF exists in the input. A missing or partially parsed table gives
  ``INDETERMINATE`` ("insufficient input"), never ``NOT_FOUND``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from ipaddress import IPv4Address, IPv4Network

from nwconfig_parser.models import (
    VRF,
    ConfigReference,
    ParseResult,
    ParseStatus,
    Route,
    RoutingTable,
    VrfScope,
)


class LookupStatus(str, Enum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    INDETERMINATE = "INDETERMINATE"


@dataclass(slots=True)
class RouteLookup:
    status: LookupStatus
    routes: list[Route] = field(default_factory=list)
    vrf: str | None = None
    vrf_id: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class VrfKey:
    scope: VrfScope
    name: str | None = None
    vrf_id: str | None = None


def route_key(route: Route) -> VrfKey:
    if route.vrf_scope is VrfScope.NAMED:
        return VrfKey(VrfScope.NAMED, name=route.vrf)
    if route.vrf_scope is VrfScope.ID_ONLY:
        return VrfKey(VrfScope.ID_ONLY, vrf_id=route.vrf_id)
    if route.vrf_scope is VrfScope.DEFAULT:
        return VrfKey(VrfScope.DEFAULT)
    return VrfKey(VrfScope.UNKNOWN)


def _identity(route: Route) -> tuple[object, ...]:
    return (
        route.network,
        route.protocol,
        tuple(route.next_hops),
        tuple(route.outgoing_interfaces),
    )


class RoutingInventory:
    """Routing tables of one device, kept separate per VRF."""

    def __init__(self) -> None:
        self._tables: list[tuple[RoutingTable, bool]] = []

    def add_table(self, table: RoutingTable, *, complete: bool = True) -> None:
        self._tables.append((table, complete))

    def add_result(self, result: ParseResult) -> None:
        """Add the table(s) of a routing parse; non-SUCCESS means incomplete."""
        complete = result.status is ParseStatus.SUCCESS
        data = result.data
        tables = [data] if isinstance(data, RoutingTable) else list(data or [])
        for table in tables:
            if not isinstance(table, RoutingTable):
                raise TypeError("ParseResult.data does not contain routing tables")
            self.add_table(table, complete=complete)

    @property
    def tables(self) -> list[RoutingTable]:
        return [table for table, _ in self._tables]

    def _all_routes(self) -> list[Route]:
        return [r for table, _ in self._tables for r in table.routes]

    def _target(self, vrf: str | None, vrf_id: str | None) -> VrfKey:
        if vrf is not None and vrf_id is not None:
            raise ValueError("Specify either vrf (name) or vrf_id, not both")
        if vrf is not None:
            return VrfKey(VrfScope.NAMED, name=vrf)
        if vrf_id is not None:
            return VrfKey(VrfScope.ID_ONLY, vrf_id=vrf_id)
        return VrfKey(VrfScope.DEFAULT)

    def _has_complete_table(self, key: VrfKey) -> bool:
        for table, complete in self._tables:
            if not complete:
                continue
            if table.vrf_scope is VrfScope.MIXED:
                if any(route_key(r) == key for r in table.routes):
                    return True
                continue
            if _table_key(table) == key:
                return True
        return False

    def _incomplete_for(self, key: VrfKey) -> bool:
        return any(
            not complete and _table_key(table) in (key, VrfKey(VrfScope.UNKNOWN))
            for table, complete in self._tables
        ) or any(
            not complete and table.vrf_scope is VrfScope.MIXED
            for table, complete in self._tables
        )

    def get_routes(
        self, vrf: str | None = None, vrf_id: str | None = None
    ) -> RouteLookup:
        key = self._target(vrf, vrf_id)
        routes = [r for r in self._all_routes() if route_key(r) == key]
        unknown = [
            r for r in self._all_routes() if route_key(r).scope is VrfScope.UNKNOWN
        ]
        return self._finish(key, routes, unknown, vrf, vrf_id)

    def find_route(
        self,
        network: IPv4Network,
        vrf: str | None = None,
        vrf_id: str | None = None,
    ) -> RouteLookup:
        key = self._target(vrf, vrf_id)
        routes = [
            r
            for r in self._all_routes()
            if route_key(r) == key and r.network == network
        ]
        unknown = [
            r
            for r in self._all_routes()
            if route_key(r).scope is VrfScope.UNKNOWN and r.network == network
        ]
        return self._finish(key, routes, unknown, vrf, vrf_id)

    def longest_prefix_match(
        self,
        address: IPv4Address,
        vrf: str | None = None,
        vrf_id: str | None = None,
    ) -> RouteLookup:
        """Longest-prefix match inside one VRF; equal-length ties are all kept."""
        key = self._target(vrf, vrf_id)
        candidates = [
            r
            for r in self._all_routes()
            if route_key(r) == key and address in r.network
        ]
        best = max((r.network.prefixlen for r in candidates), default=-1)
        routes = [r for r in candidates if r.network.prefixlen == best]
        unknown = [
            r
            for r in self._all_routes()
            if route_key(r).scope is VrfScope.UNKNOWN
            and address in r.network
            and r.network.prefixlen >= best
        ]
        return self._finish(key, routes, unknown, vrf, vrf_id)

    def _finish(
        self,
        key: VrfKey,
        routes: list[Route],
        unknown: list[Route],
        vrf: str | None,
        vrf_id: str | None,
    ) -> RouteLookup:
        def lookup(status: LookupStatus, reason: str | None) -> RouteLookup:
            return RouteLookup(status, routes, vrf, vrf_id, reason)

        if unknown:
            return lookup(
                LookupStatus.INDETERMINATE,
                "Routes with unknown VRF membership could affect this result.",
            )
        if self._incomplete_for(key):
            return lookup(
                LookupStatus.INDETERMINATE,
                "A routing table for this VRF was only partially parsed.",
            )
        if routes:
            return lookup(LookupStatus.FOUND, None)
        if not self._has_complete_table(key):
            return lookup(
                LookupStatus.INDETERMINATE,
                "No routing table for this VRF is present in the input "
                "(insufficient input, not proof that the VRF has no route).",
            )
        return lookup(LookupStatus.NOT_FOUND, None)


def _table_key(table: RoutingTable) -> VrfKey:
    if table.vrf_scope is VrfScope.NAMED:
        return VrfKey(VrfScope.NAMED, name=table.vrf)
    if table.vrf_scope is VrfScope.ID_ONLY:
        return VrfKey(VrfScope.ID_ONLY, vrf_id=table.vrf_id)
    if table.vrf_scope is VrfScope.DEFAULT:
        return VrfKey(VrfScope.DEFAULT)
    return VrfKey(table.vrf_scope)


@dataclass(slots=True)
class VrfRouteDiff:
    key: VrfKey
    added: list[Route] = field(default_factory=list)
    removed: list[Route] = field(default_factory=list)
    indeterminate: bool = False
    reason: str | None = None


def diff_routes(old: RoutingInventory, new: RoutingInventory) -> list[VrfRouteDiff]:
    """Compare two inventories per VRF; identical prefixes in different VRFs differ.

    Route identity is (network, protocol, next hops, outgoing interfaces).
    Routes of unknown VRF membership are never matched to a VRF; if either side
    has them, that bucket is reported as indeterminate with no added/removed.
    """
    old_routes: dict[VrfKey, dict[tuple[object, ...], Route]] = {}
    new_routes: dict[VrfKey, dict[tuple[object, ...], Route]] = {}
    for inventory, store in ((old, old_routes), (new, new_routes)):
        for route in inventory._all_routes():
            store.setdefault(route_key(route), {})[_identity(route)] = route
    keys = list(dict.fromkeys([*old_routes, *new_routes]))
    diffs: list[VrfRouteDiff] = []
    for key in keys:
        if key.scope is VrfScope.UNKNOWN:
            diffs.append(
                VrfRouteDiff(
                    key,
                    indeterminate=True,
                    reason="VRF membership unknown; routes cannot be compared.",
                )
            )
            continue
        before, after = old_routes.get(key, {}), new_routes.get(key, {})
        added = [after[i] for i in after if i not in before]
        removed = [before[i] for i in before if i not in after]
        if added or removed:
            diffs.append(VrfRouteDiff(key, added=added, removed=removed))
    return diffs


def check_routing_table_vrfs(
    vrfs: Iterable[VRF], inventory: RoutingInventory
) -> list[ConfigReference]:
    """Relate routing tables to VRF definitions (resolved None = cannot tell)."""
    defined = {vrf.name for vrf in vrfs}
    references: list[ConfigReference] = []
    for table in inventory.tables:
        if table.vrf_scope is VrfScope.NAMED and table.vrf is not None:
            references.append(
                ConfigReference(
                    "routing-table",
                    table.vrf,
                    "vrf",
                    "vrf",
                    table.vrf,
                    table.source_reference.start_line
                    if table.source_reference
                    else None,
                    table.vrf in defined,
                )
            )
        elif table.vrf_scope in (VrfScope.ID_ONLY, VrfScope.UNKNOWN, VrfScope.MIXED):
            references.append(
                ConfigReference(
                    "routing-table",
                    table.vrf_id or table.vrf_scope.value,
                    "vrf",
                    "vrf",
                    table.vrf_id or "",
                    None,
                    None,
                )
            )
    return references
