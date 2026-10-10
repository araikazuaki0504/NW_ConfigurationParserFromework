# Support status

Status vocabulary: `IMPLEMENTED`, `TESTED_WITH_SYNTHETIC_DATA`, `VERIFIED_WITH_DEVICE_OUTPUT`, `NOT_IMPLEMENTED`.

| Area | Vendor / OS | Command or capability | Status | Evidence / limitation |
|---|---|---|---|---|
| Parser pipeline smoke test | Example / Synthetic | `parse text` | TESTED_WITH_SYNTHETIC_DATA | Artificial line fixture; not a network device parser |
| Configuration parser | Cisco / IOS, IOS XE | `running-config`: hierarchy, hostname, interface, VLAN, ip routing/route, router ospf, router bgp (address-family ipv4), ip prefix-list (including `no ip prefix-list` removal by whole list / seq / entry / description, applied in file order), route-map, multi-line `banner` (any delimiter) | TESTED_WITH_SYNTHETIC_DATA | Artificial config fixtures only (`tests/fixtures/synthetic/cisco_ios_running_config.txt`); IOS XE reuses IOS syntax, no device capture supplied. Removal targets absent from parsed state are not applied and mark the list incomplete. `macro name` bodies are preserved with line ranges but not interpreted; other indentation-free multi-line constructs (e.g. `crypto pki certificate` hex data) are not specially handled |
| Operational parser | Cisco / IOS | `show ip route`; `show interfaces status`; `show ip interface brief`; `show vlan brief` | TESTED_WITH_SYNTHETIC_DATA | Supported output shapes documented below; no device capture supplied |
| Operational parser | Cisco / IOS XE | `show ip route`; `show interfaces status`; `show ip interface brief`; `show vlan brief` | TESTED_WITH_SYNTHETIC_DATA | Uses IOS-compatible synthetic shapes; IOS XE device output not verified |
| Operational parser | Cisco / IOS | `show ip ospf neighbor`; `show ip bgp summary`; `show ip prefix-list` | NOT_IMPLEMENTED | Outside Phase 2 command set |
| Operational parser | Cisco / NX-OS | Device-specific show output | NOT_IMPLEMENTED | Requires separate parser and representative output |
| Operational parser | Yamaha / RTX | `show ip route` (English header) | TESTED_WITH_SYNTHETIC_DATA | Format from general knowledge, unverified on devices; Japanese header, unknown Kind values and rows without a prefix length are reported as issues |
| Operational parser | Yamaha / RTX | `show status lan`; `show ip interface` | NOT_IMPLEMENTED | Command names and output unverified |
| Operational parser | Fortinet / FortiOS | `get router info routing-table all` (Zebra style, ECMP, VRF heading) | TESTED_WITH_SYNTHETIC_DATA | FortiOS 7.x variants unverified; unmatched lines become issues |
| Operational parser | Fortinet / FortiOS | `get system interface` (`== [ name ]` blocks, key: value) | TESTED_WITH_SYNTHETIC_DATA | `status` kept raw in attributes; admin/operational NOT inferred; IPv6 fields ignored |
| Operational parser | Fortinet / FortiOS | `get router info bgp summary` | NOT_IMPLEMENTED | Outside Phase 4 priority (Interface/Routing) |
| Operational parser | A10 / ACOS | `show ip route` (Zebra style) | TESTED_WITH_SYNTHETIC_DATA | Separate parser class from FortiOS; shares only a line-grammar base; unverified |
| Operational parser | A10 / ACOS | `show interfaces` (name, state, hardware/MAC, IPv4) | TESTED_WITH_SYNTHETIC_DATA | Counter and other detail lines are not parsed and make real output PARTIAL_SUCCESS by design |
| Operational parser | HPE / Comware | `display ip routing-table` (Comware 7 style, ECMP continuation rows) | TESTED_WITH_SYNTHETIC_DATA | Direct-route NextHop kept in `attributes[next_hop_raw]`, not as a next hop; Comware 5 and VPN-instance output unverified |
| Operational parser | HPE / Comware | `display interface` (Comware 7 style) | TESTED_WITH_SYNTHETIC_DATA | `Administratively DOWN` sets admin_status only; UP/DOWN sets operational_status only; other fields (counters, media) are reported as issues |
| Operational parser | HPE / Comware | `display vlan` | NOT_IMPLEMENTED | Outside Phase 4 priority |
| Config parser | Yamaha / RTX | `show config`: `ip <lan> address`, `description`, `lan shutdown`, `ip route` | TESTED_WITH_SYNTHETIC_DATA | No hostname command (`snmp sysname` not mapped); `pp`/`tunnel select` contexts tracked by statement order (`description`, `ip pp address`); DHCP, port-level shutdown unsupported; see [vendor-config-support.md](vendor-config-support.md) |
| Config parser | Yamaha / SWX | `show running-config`: `hostname`, `vlan database` (`vlan`), `interface port/vlan/sa/po` (`description`, `shutdown`, `switchport mode/access vlan/trunk allowed vlan add\|remove\|none/trunk native vlan`, `ip address`), `ip route` | TESTED_WITH_SYNTHETIC_DATA | Checked against SWX2320 Rev.2.05-2.08 and SWX3200 Rev.4.00 command references only; SWX2300 and other models/revisions not checked; no model detection; `ip forwarding`, `allowed vlan all/except`, DHCP address, labels, ranges with a name and interface ranges are reported as unsupported; see [vendor-config-support.md](vendor-config-support.md) |
| Operational parser | Yamaha / SWX | `show interface brief`, `show vlan brief`, `show ip route`, `show ip interface brief` | TESTED_WITH_SYNTHETIC_DATA | Samples copied in structure from the SWX2320/SWX3200 command reference examples; not device-verified; `show ip route database`, `show interface` (detail) and VRF are not implemented |
| Config parser | Fortinet / FortiOS | `show`: `config system global` hostname, `config system interface`, `config router static` | TESTED_WITH_SYNTHETIC_DATA | Static route fields from the 6.2.8 reference notes (not re-read here); interface description/vrf only seen in a search index; VDOM/VRF unsupported |
| Config parser | A10 / ACOS | `show running-config`: `hostname`, `interface ethernet/management/ve`, `enable`/`disable`, `ip address`, `ip route` | TESTED_WITH_SYNTHETIC_DATA | Interface `name` and route options unsupported; partitions not mapped to VRF |
| Config parser | HPE / Comware | `display current-configuration`: `sysname`, `interface`, `ip address`, `shutdown`, `description`, `ip route-static`, `ip vpn-instance`, `ip binding vpn-instance` | TESTED_WITH_SYNTHETIC_DATA | `sysname` and VPN-instance `route-distinguisher` read in H3C references (not HPE-model verified); address-family RD, bfd/public route options unsupported |
| Config parser | Those four OS: VLAN, OSPF, BGP, prefix-list, route-map | n/a | NOT_IMPLEMENTED | Not claimed |
| Config / operational parser | Cisco / NX-OS, ArubaOS-CX, ArubaOS-Switch | All | NOT_IMPLEMENTED | Out of scope; no parser registered |
| Operational parser | ArubaOS-Switch, ArubaOS-CX | No command parser registered | NOT_IMPLEMENTED | Requires command selection and representative output |
| VRF / per-VRF routing | Cisco / IOS, IOS XE | Config VRF definition, interface `vrf forwarding`, `ip route vrf`, OSPF `vrf`, BGP `address-family ipv4 vrf`; `show ip route vrf <name\|*>`; per-VRF route queries and diff | TESTED_WITH_SYNTHETIC_DATA | See [vrf-support.md](vrf-support.md); IPv6 VRF and change-command replay not implemented |
| VRF / per-VRF routing | Fortinet / FortiOS; HPE / Comware | `Routing table for VRF=<n>` headings (ID only); `display ip routing-table vpn-instance <name>` | TESTED_WITH_SYNTHETIC_DATA | Partial: operational routes only, no config parser; FortiOS VRF ID 0 not assumed default |
| VRF / per-VRF routing | HPE / Comware config | `ip vpn-instance`, `route-distinguisher`, `ip binding vpn-instance`, `ip route-static vpn-instance` | TESTED_WITH_SYNTHETIC_DATA | Names and VPN-instance-view RD (range-checked); address-family RD unsupported |
| VRF / per-VRF routing | NX-OS, Yamaha, A10, FortiOS config, ArubaOS-CX, ArubaOS-Switch | VRF | NOT_IMPLEMENTED | No VRF claim made |
| Device-output verification | All | Real device captures | NOT_IMPLEMENTED | No verified captures supplied |
| IPv6 and LLDP | All | Excluded features | NOT_IMPLEMENTED | Explicitly out of scope |

Synthetic tests demonstrate code behavior only. They do not establish that a syntax or command is available on a real device or OS release.

## Phase 2 output shapes

The synthetic fixtures exercise these currently recognized forms:

- `show ip route`: protocol code, canonical IPv4 prefix, optional `[administrative-distance/metric]`, next hop and optional age/interface, directly-connected interface, and bracketed ECMP continuation.
- `show interfaces status`: Port, optional free-text Name, recognized status token, VLAN, Duplex, Speed, optional Type. Blank Name is supported.
- `show ip interface brief`: Interface, IPv4 address or `unassigned`, OK?, Method, multiword Status, Protocol.
- `show vlan brief`: numeric VLAN ID, Name, Status, optional comma/space-separated ports, and indented interface-name continuation lines.

Unknown nonempty lines and malformed records are attached to `unparsed_ranges` and prevent `SUCCESS`. A valid record alongside such a line yields `PARTIAL_SUCCESS`; unrecognized-only or malformed-only input yields `FAILED`.

## Phase 3 running-config semantics (Cisco IOS / IOS XE)

Every stored `ConfigNode` carries `semantic_status`: `PARSED`, `UNSUPPORTED`, `INVALID` (or `STRUCTURE_ONLY` before interpretation). Storing a line in the hierarchy never implies it was understood. Unsupported/invalid lines are listed in `ConfigDocument.unsupported_lines` with the original line number, raw text, reason and parent. Any such line yields `PARTIAL_SUCCESS`; a config where nothing could be interpreted yields `FAILED`.

Supported (synthetic-tested):

- `hostname`; `ip routing` / `no ip routing` (last statement wins, unset = `None`).
- `interface NAME`: `description`, `shutdown` / `no shutdown` (last statement wins, history in `attributes["admin_status_history"]`, unset = `None`, no device default assumed), `ip address A M [secondary]`, `no ip address`, `switchport`, `no switchport` (mode `routed`), `switchport mode access|trunk`, `switchport access vlan N`, `switchport trunk native vlan N`, `switchport trunk allowed vlan <list>|all|none|add|remove|except`. Repeated interface blocks merge in order.
- `vlan N` with `name`.
- `ip route [vrf X] PREFIX MASK (NEXTHOP | INTERFACE [NEXTHOP]) [AD] [tag N] [name X] [permanent]` and `no ip route ...`.
- `router ospf PID [vrf X]`: `router-id`, `network A WILDCARD area N`, `passive-interface IF|default` and the `no` forms (order preserved).
- `router bgp ASN` (plain integer): `bgp router-id`, IPv4 `neighbor X remote-as|description|route-map in|out|prefix-list in|out|activate|shutdown`, `address-family ipv4 [unicast]`, `network A mask M [route-map R]`.
- `ip prefix-list NAME [seq N] permit|deny PREFIX [ge N] [le N]` and `ip prefix-list NAME description TEXT`; ge/le validation; duplicate sequences are rejected, not resolved by guessing.
- `route-map NAME permit|deny SEQ` (explicit action and sequence required) with `match ip address [prefix-list] ...`, `set local-preference|metric|weight|ip next-hop`, `description`.

References (`ConfigDocument.references`): route-map -> prefix-list / ACL, BGP neighbor -> route-map / prefix-list, BGP network -> route-map. `resolved` is `True`/`False`, or `None` when the target type (ACL) is not parsed. Undefined targets produce an `INFO` `DANGLING_REFERENCE` issue; they do not downgrade the parse status.

Prefix-list evaluation: `evaluate_prefix_list_detailed` returns `status` (`EVALUATED` / `INDETERMINATE`), `action`, `matched_sequence`, `matched_entry`, `implicit_deny`. It returns `INDETERMINATE` (no permit/deny) when the list has any unparsed/invalid/duplicate line or an entry without a sequence number. `evaluate_prefix_list` keeps the earlier string API and raises `PrefixListEvaluationError` in those cases. Route-maps with an unparsed line are flagged `complete=False`.

Not supported (preserved as unsupported lines): IPv6 (not implemented by design), LLDP, `interface range`, `ip address dhcp`, peer-groups, BGP address families other than IPv4 unicast (vpnv4, vrf, multicast, ...), classful `network` without mask, `asdot` AS numbers, `vlan` lists/ranges, OSPFv3 and OSPF area/interface sub-commands, `no ip prefix-list`, `no route-map`, `match`/`set` clauses other than those listed, `continue`, ACL definitions, and every other statement without a handler (e.g. `service`, `line`, `banner`, `version`).
