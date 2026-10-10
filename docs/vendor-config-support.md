# Non-Cisco configuration parsers (research record)

All four parsers return the common `ConfigDocument`. Every test input is
**hand-written synthetic text; nothing here is device-verified.**
Out of scope and **not registered**: Cisco NX-OS, ArubaOS-CX, ArubaOS-Switch.

| Vendor / OS | `--vendor` / `--os` | Command id | Module |
|---|---|---|---|
| Yamaha / RTX | Yamaha / RTX | `show config` | `parsers/yamaha_rtx_config.py` |
| Fortinet / FortiOS | Fortinet / FortiOS | `show` | `parsers/fortinet_fortios_config.py` |
| A10 / ACOS | A10 / ACOS | `show running-config` | `parsers/a10_acos_config.py` |
| HPE / Comware | HPE / Comware | `display current-configuration` | `parsers/hpe_comware_config.py` |

No command aliases are accepted. Shared logic (normalisation, empty input,
status decision, preserved unsupported lines) lives in
`parsers/vendor_config_base.py`; the engine and models have no vendor branches.

## Result policy

- `FAILED`: empty input, or no statement could be interpreted.
- `PARTIAL_SUCCESS`: any WARNING/ERROR issue (unsupported or invalid line,
  unterminated block, omitted route).
- `SUCCESS`: at least one statement interpreted and no WARNING/ERROR.
- Rejected lines stay in `ConfigDocument.unsupported_lines` with line numbers
  and in `ParseResult.unparsed_ranges`. Issue messages are static text and never
  contain configuration text, addresses or credentials (the preserved raw line
  is the only place configuration text is kept, as in the Cisco parser).
- Later statements override earlier ones in file order; `no`/`undo` removes the
  item. A removal whose target is not in the input is an INFO issue
  `NEGATION_TARGET_NOT_IN_INPUT`. Replaying whole change-command sequences is
  not a goal.
- Admin state is only set from explicit statements (never inferred).

## Support matrix (what is implemented)

| Item | Yamaha RTX | FortiOS | ACOS | Comware |
|---|---|---|---|---|
| hostname | not mapped (see below) | `config system global` / `set hostname` | `hostname` | `sysname` |
| interface + IPv4 | `ip <lanN/wanN/bridgeN/loopbackN> address a/m` | `config system interface` / `set ip a m` | `interface ethernet N / management / ve N` + `ip address` | `interface` + `ip address` (+`sub`) |
| description | `description <if> text` | `set description` | not implemented | `description` |
| admin state | `lan shutdown <lan>` / `no lan shutdown` | `set status up/down` | `enable` / `disable` | `shutdown` / `undo shutdown` |
| IPv4 static route | `ip route` (multi-gateway, `metric`, `weight`, `hide`, `filter`, `keepalive`) | `config router static` (`dst`, `gateway`, `device`, `distance`, `priority`, `status`, `comment`) | `ip route a /len gw` or `a mask gw` | `ip route-static` (`preference`, `tag`, `permanent`, `track`, `description`) |
| VRF | not implemented | not implemented (VDOM is not VRF) | not implemented (partition is not VRF) | `ip vpn-instance` (+ `route-distinguisher`), `ip binding vpn-instance`, `ip route-static vpn-instance` |
| VLAN / OSPF / BGP / prefix-list / route-map | **not implemented** | **not implemented** | **not implemented** | **not implemented** |

Everything else is reported as unsupported (e.g. Yamaha unmatched pp/tunnel contexts,
`ip ... address dhcp`, port-level `lan shutdown`; FortiOS `config vdom`,
`allowaccess`, secondary IPs, `dstaddr`/`internet-service` routes; ACOS
partitions, VLANs, `name`, route options; Comware `port link-mode`,
address-family RD, `bfd`/`public` route options, DHCP addresses).
Routes whose destination cannot be represented (FortiOS `dstaddr`, ...) are
omitted with `ROUTE_ENTRY_OMITTED` rather than rewritten as a default route.

## Evidence by vendor

Evidence levels used below: (1) body text read, (2) found only in a search
index, (3) API spec only, (4) implemented with synthetic tests, (5) verified on a
real device. **No item has level 5.** Items implemented at level 1 are still
checked only with synthetic data.

### Yamaha RTX (official RT command reference, rtpro.yamaha.co.jp)
Level 1 pages: `ip/ip_interface_address.html`, `ip/ip_route.html`,
`setup/description.html`, `setup/lan_shutdown.html`, `snmp/snmp_sysname.html`,
`operation/pp_select.html`, `operation/tunnel_select.html`. Applicable models
include RTX830/840/1210/1220/1300/3510/5000 and vRX; no firmware revision is
claimed.
- No hostname command exists; `snmp sysname` is the SNMP MIB sysName, so it is
  **not** mapped to `hostname` (reported as unsupported).
- `pp select N|none|anonymous`, `tunnel select N|none` and the `no` forms set a
  context by statement order; indentation is never used. `description pp|tunnel`
  and `ip pp address ip/mask` apply to the selected number and are stored under
  the Interface names `pp N` / `tunnel N`. The select statements themselves
  are not counted as interpreted settings.
- Not guessed (kept as unsupported with line numbers): a `pp`/`tunnel` statement
  with no matching active context, `anonymous` context, `ip pp address` without a
  mask (the default mask is not documented), `ip tunnel address` (not documented),
  and every other line inside a pp/tunnel section. `pp N` and `tunnel N` are held
  in a single context slot: whether selecting one clears the other is not
  documented, so a later `select` simply replaces the context. The valid range of
  numbers depends on model and license and is not checked.
- Mask forms accepted: bit count, dotted, `0x` hex. Route network `default`,
  `a/len`, or a bare address (/32).
- The official troubleshooting page shows indented `show config` output after
  `pp select` (RTX1000 Rev.7.01.08). This is one example, not a guarantee for all
  models, so the parser does not depend on whitespace.

### Fortinet FortiOS
- Level 1 (FortiOS 7.0.9 Administration Guide, "Basic configuration"):
  `config system global`/`set hostname`, `config system interface`/`edit`/`set ip`/
  `set allowaccess`/`set alias`, `config router static`/`edit`/`set gateway`/
  `set device`, a route without `dst` being the default route.
- Level 1 per the reference notes (FortiOS 6.2.8 CLI Reference `router static`):
  `dst`, `gateway`, `device`, `distance` (1-255), `priority` (0-4294967295),
  `status enable|disable`, `vrf` exists. **This session's fetch of that page
  returned only the page frame, so this is not independently re-read here.** It
  covers 6.2.8 only; 7.x equivalence is not assumed.
- Level 2 only: interface `set description` and `vrf` (7.4.4 `config system
  interface`, search index). Body not retrieved, so `set vrf` stays unsupported;
  VRF ID 0 is never mapped to a default VRF and VDOM is never treated as VRF.
- Implemented ahead of level 1 evidence: interface `status`, `vdom`
  (attribute only) and route `comment`.
- Quoted values (with `\"` escapes and multi-line values) are tokenised; an
  unterminated edit/config block is `UNTERMINATED_BLOCK`.

### A10 ACOS (ACOS 7.0.2 System Configuration and Administration Guide / CLI Reference)
Level 1: `hostname`, `interface ethernet N` / `management` / `ve N` blocks with
`enable` and `ip address a.b.c.d /len` (or dotted mask), `ip route 0.0.0.0 /0 gw`,
`!` separators. Level 2: `disable` for ethernet interfaces (CLI Reference index).
Level 3 only: route `distance` (AXAPI `ip route`, `distance-nexthop-ip`, 1-255)
-- the API field name is **not** treated as CLI syntax, so route options stay
unsupported. Interface `name` has no CLI evidence (AXAPI `interface ethernet` is
not CLI syntax) and stays unsupported. Interface names are kept as written,
e.g. `ethernet 1`.

### HPE Comware (H3C Comware 7 command references)
Level 1: `ip route-static` (all forms incl. `vpn-instance`, `preference` 1-255,
`tag`, `description` 1-60), `ip vpn-instance`, VPN instance `description`
(1-79 chars), interface `ip binding vpn-instance` (binding or unbinding clears
the interface IP configuration, reproduced with
`ADDRESS_CLEARED_BY_VPN_BINDING`), interface `ip address` (prefix 1-31, 32 on
loopback; `sub` secondary; a repeated primary replaces), `shutdown` /
`undo shutdown`, `description`, and `sysname` (H3C S6550X-HI System Management
Command Reference, 202506; undo restores the default). These are H3C
references; applicability to a specific HPE model/release is not confirmed.
HPE Comware 7 Fundamentals (support.hpe.com `c05030686`) was not retrievable.
`route-distinguisher` (H3C S9825/S9855 R932x MCE command reference, 202509,
body read): 3-21 chars, `ASN16:N32`, `IPv4:N16`, `ASN32(>=65536):N16`;
each component range is checked. It is interpreted **only in VPN instance
view** (`VRF.route_distinguisher`); `undo route-distinguisher` clears it.
The same command is also valid in the address-family views, where the
documentation says families must use the instance RD or may differ when none is set;
since that interaction is not modelled, address-family blocks (and any RD in
them) remain unsupported. The `version` header line is accepted with INFO
`HEADER_LINE_NOT_MAPPED`.
Static routes with a leading `vpn-instance S` get `vrf="S"` (NAMED); a
`vpn-instance D` after the destination is the next-hop VPN and is kept in
`attributes["next_hop_vrf"]` with `vrf=None`.
## Usage

```python
from nwconfig_parser.cli import build_registry
from nwconfig_parser.core.engine import ParserEngine
from nwconfig_parser.parsers.base import ParseContext

result = ParserEngine(build_registry()).parse(
    text,
    ParseContext(
        vendor="HPE",
        os_family="Comware",
        command="display current-configuration",
        filename="sw1.cfg",
    ),
)
port = next(i for i in result.data.interfaces if i.name == "GigabitEthernet1/0/1")
print(port.ipv4_addresses, port.admin_status, result.data.static_routes)
```

CLI: `--vendor Yamaha --os RTX --command "show config"`.
