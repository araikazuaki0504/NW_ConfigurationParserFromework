# VRF and per-VRF routing support

All behaviour below is verified only with **synthetic** inputs
(`TESTED_WITH_SYNTHETIC_DATA`). No real device output was used, so nothing
here is device-verified. Cisco IOS / IOS XE is the reference implementation.

## Per-vendor status

| Vendor / OS | Capability | Status | Limitation |
|---|---|---|---|
| Cisco IOS, IOS XE | Config: `vrf definition` / `ip vrf` (description, `rd`, `route-target`, `address-family ipv4`), `vrf forwarding` / `ip vrf forwarding`, `ip route vrf` (and `no`), `router ospf N vrf X`, BGP `address-family ipv4 [unicast] vrf X` (neighbors, networks, route-map / prefix-list bindings) | TESTED_WITH_SYNTHETIC_DATA | `address-family ipv6`, other BGP families (vpnv4 etc.), `bgp router-id` inside a VRF family, VRF-aware OSPF area/passive details beyond existing OSPF support: preserved as unsupported lines |
| Cisco IOS, IOS XE | Operational: `show ip route vrf <name>` and `show ip route vrf *` | TESTED_WITH_SYNTHETIC_DATA | Heading format `Routing Table: NAME` is from general knowledge. A literal VRF called `default` is treated as an ordinary name, never as the global table. Route-line grammar is that of `show ip route` (e.g. "is variably subnetted" lines are reported as unsupported) |
| Fortinet FortiOS | Operational: `Routing table for VRF=<n>` headings in `get router info routing-table all` | TESTED_WITH_SYNTHETIC_DATA (partial) | Only the numeric VRF ID is known (`vrf_scope=ID_ONLY`, `vrf_id`, `vrf=None`). ID 0 is **not** declared the default VRF. Name-to-ID mapping and FortiOS config are not implemented |
| HPE Comware | Operational: `display ip routing-table vpn-instance <name>`; config: `ip vpn-instance`, `ip binding vpn-instance`, `ip route-static vpn-instance` (see [vendor-config-support.md](vendor-config-support.md)) | TESTED_WITH_SYNTHETIC_DATA (partial) | Config VRF names and VPN-instance-view `route-distinguisher`; address-family RD unsupported. Plain `display ip routing-table` stays default scope |
| Cisco NX-OS | VRF config / operational | NOT_IMPLEMENTED | No NX-OS parser |
| Yamaha RTX, Yamaha SWX, A10 ACOS | VRF / partition | NOT_IMPLEMENTED | No VRF claim is made (no VRF text in the SWX2320/SWX3200 references); routes keep default scope |
| ArubaOS-CX, ArubaOS-Switch | VRF | NOT_IMPLEMENTED | No parser registered |

## Model changes (all additive)

New fields all have defaults, so existing JSON output gains keys but loses
none, and existing constructors keep working.

| Model | Change | Reason |
|---|---|---|
| `VrfScope` (new enum) | `DEFAULT`, `NAMED`, `ID_ONLY`, `UNKNOWN`, `MIXED` | Distinguish "default table", "known name", "only a numeric ID is known", "membership undeterminable" and an aggregate of several VRFs |
| `Route` | `vrf_id`, `vrf_scope` | `vrf` alone cannot express "unknown" vs "default" |
| `RoutingTable` | `vrf_id`, `vrf_scope`, `source_command`, `source_reference` | Per-VRF table with provenance |
| `VRF` | `vrf_id`, `source_reference` | Name and ID are separate identifiers |
| `Interface` | `vrf`, `vrf_source_reference` | Interface-to-VRF assignment; `None` means "not assigned in the input", never filled with "default" |
| `OSPFProcess` | `vrf` | Also kept in `attributes["vrf"]` for compatibility |
| `BGPVrfAddressFamily` (new), `BGPProcess.vrf_address_families` | VRF address-family with its own peers / networks | Keeps VRF neighbors apart from global ones |
| `ConfigDocument` | `vrfs` | VRF definitions |

`Parser.matches_command()` was added (default: exact normalised equality, so
existing parsers are unchanged). VRF parsers override it to accept
`show ip route vrf <name|*>` / `display ip routing-table vpn-instance <name>`;
their `command` attribute (`... *`) remains the unique registration key. The
registry selects through `matches_command`; an exact `show ip route` never
selects the VRF parser.

Fortinet's `get router info routing-table all` still returns one aggregate
`RoutingTable` (existing `.routes` consumers keep working); per-route
`vrf_id`/`vrf_scope` carry the VRF. `show ip route vrf ...` is a new command
and returns `list[RoutingTable]`.

## Rules

* The VRF context switches at each heading. It is never guessed. A route
  before any heading (and no single VRF named in the command), an unparseable
  heading, or a heading that disagrees with the command yields
  `VrfScope.UNKNOWN`, a WARNING issue and `PARTIAL_SUCCESS`.
* The same prefix in different VRFs is never merged or de-duplicated.
* A missing `vrf definition` in a single file is not an error: it is
  `ConfigReference.resolved=False` plus an INFO issue
  `VRF_DEFINITION_NOT_IN_INPUT` (the input may be partial), not
  `DANGLING_REFERENCE`.
* `vrf forwarding` after `ip address` on an interface keeps the address, sets
  `attributes["address_validity_uncertain"]` and emits
  `VRF_ASSIGNMENT_AFTER_IP_ADDRESS` (a device removes the address). Replaying
  change-command sequences is **not implemented**.
* A rejected or unsupported `vrf definition` is reported as an unsupported
  line; references to it are then `resolved=False` (the interpreter does not
  separately track rejected names).

## Analysis (`nwconfig_parser.analysis.vrf`)

* `RoutingInventory.get_routes / find_route / longest_prefix_match(vrf=, vrf_id=)`.
  Omitting both selects the **default table only**; tables are never merged.
  Passing both raises `ValueError` (name/ID mapping is unknown).
* Result `RouteLookup.status`: `FOUND`, `NOT_FOUND`, `INDETERMINATE`.
  `NOT_FOUND` requires a complete table for that VRF. A missing or partially
  parsed table, or an unknown-VRF route that could match, gives
  `INDETERMINATE`. Longest-prefix ties are all returned.
* `diff_routes(old, new)`: per-VRF added / removed (identity: network,
  protocol, next hops, interfaces). Unknown-VRF routes are reported as
  indeterminate, not diffed.
* `check_routing_table_vrfs(vrfs, inventory)`: VRF-to-RoutingTable references
  (`resolved=None` for ID-only / unknown / mixed tables).

## Not implemented / unverified

NX-OS, Yamaha, A10 partitions, Aruba, FortiOS config VRF, Comware address-family RD; IPv6 VRF
(`address-family ipv6`); route-leaking analysis (import/export of
route-targets is recorded, not evaluated); `show ip route vrf X <prefix>`
forms; real-device verification.
