# NWConfig Parser

Extensible Python framework for reading captured network-device configuration and command output. The common foundation is in place, and the operational parsing scope now includes four Cisco IOS/IOS XE commands.

## Requirements and installation

- Python 3.12 or newer
- No runtime third-party dependencies

```powershell
python -m pip install -e ".[dev]"
```

The `dev` extra installs pytest, Ruff, and mypy. The package uses a `src/` layout and exposes the `nwconfig-parser` command.

## Architecture

- `src/nwconfig_parser/models.py`: common typed data models, parse outcomes, source references, topology primitives, and verification status.
- `src/nwconfig_parser/parsers/base.py`: parser interface and selection exceptions.
- `src/nwconfig_parser/parsers/registry.py`: explicit registration, conflict detection, matching, and parser descriptions.
- `src/nwconfig_parser/core/engine.py`: execution facade over the registry.
- `src/nwconfig_parser/input.py`: file decoding and newline normalization while retaining the decoded original text.
- `src/nwconfig_parser/cli.py`: CLI and JSON serialization.
- `src/nwconfig_parser/parsers/example.py`: synthetic line parser for exercising the Phase 1 path end to end.
- `src/nwconfig_parser/parsers/cisco_operational.py`: command-specific handlers for four IOS/IOS XE operational commands.
- `src/nwconfig_parser/parsers/cisco_config.py`: Cisco IOS/IOS XE running-config hierarchy builder; `cisco_config_semantics.py` converts recognised statements to common models and marks each node PARSED/UNSUPPORTED/INVALID.
- `src/nwconfig_parser/analysis/prefix_list.py`: prefix-list evaluation that returns INDETERMINATE for incomplete lists.

The data flow is InputDocument (raw and normalized text) → ParserEngine → ParserRegistry selection → selected parser → ParseResult (parsed data, status, issues, source references). The Registry selects and describes parsers; the Engine invokes them and enriches metadata. A parser must report partial or failed results explicitly; parser selection never falls back to a guessed implementation.

## CLI

List registered parsers:

```powershell
nwconfig-parser list
nwconfig-parser list --vendor Example
```

Inspect parser metadata:

```powershell
nwconfig-parser info --vendor Example --os Synthetic --command "parse text"
```

Parse captured text as JSON:

```powershell
nwconfig-parser parse --vendor Example --os Synthetic --command "parse text" --input .\capture.txt --output .\result.json
nwconfig-parser parse --vendor Cisco --os IOS --command "show ip route" --input .\route.txt --output .\route.json
```

Equivalent after installation: `python -m nwconfig_parser parse ...`.

Validate parse completeness:

```powershell
nwconfig-parser validate --vendor Example --os Synthetic --command "parse text" --input .\capture.txt
```

`--os-family` is an alias for `--os`, and `--version` is an alias for `--os-version`. Parse writes only JSON to standard output unless `--output` is supplied; operational errors go to standard error. Exit codes are `0` for SUCCESS, `1` for PARTIAL_SUCCESS, and `2` for FAILED or CLI/input/selection errors.

## Python API

```python
from nwconfig_parser.core.engine import ParserEngine
from nwconfig_parser.parsers.base import ParseContext
from nwconfig_parser.parsers.example import ExampleTextParser
from nwconfig_parser.parsers.registry import ParserRegistry

registry = ParserRegistry()
registry.register(ExampleTextParser())
engine = ParserEngine(registry)
result = engine.parse(
    "sample output\n",
    ParseContext(vendor="Example", os_family="Synthetic", command="parse text"),
)
```

`Parser.parse(text, context)` returns `ParseResult`. `SourceReference` links parsed data and issues back to file, command, and line information. Models use dataclasses and retain vendor-specific extensions in attributes where appropriate.

## Registry and adding a parser

Each parser declares `vendor`, `os_family`, `supported_versions`, `command`, and `parser_version`. Empty `supported_versions` means the parser accepts any version; this broad registration conflicts with another parser registered for the same vendor/OS/command. Registration conflicts raise `DuplicateParserError`. Missing or ambiguous selection raises an explicit `ParserSelectionError` subclass.

To add a parser:

1. Implement the `Parser` interface in the relevant vendor/OS module.
2. Keep syntax and semantics specific to the device family in that parser.
3. Return `ParseResult` with an accurate status, issues, and source references.
4. Register it in the application registry.
5. Add synthetic fixtures and pytest coverage before claiming device-output verification.

## Data models

The shared dataclasses include Device, Interface, VLAN, Route/RoutingTable, VRF, OSPFProcess/OSPFNeighbor, BGPPeer, ACL, NATRule, VPN, PrefixList/PrefixListEntry, RouteMap/RouteMapEntry, ParseResult/ParseIssue/SourceReference, TopologyNode/TopologyLink/LogicalDomain, and VerificationResult. IPv4 fields use `ipaddress` types. `TopologyLink` allows unknown endpoints and confidence; confirmed links require both devices. Verification status distinguishes PASS, FAIL, UNKNOWN, and ERROR.

## Status and support

Implementation status uses `IMPLEMENTED`, `TESTED_WITH_SYNTHETIC_DATA`, `VERIFIED_WITH_DEVICE_OUTPUT`, and `NOT_IMPLEMENTED`. Synthetic test coverage is not evidence of verification against a device. See [docs/support-status.md](docs/support-status.md) for the current matrix.

No sanitized real-device command outputs have been supplied, so no device output is marked verified. The example and Cisco parsers are marked tested with synthetic data only. The full command-level matrix and recognized output shapes are in [docs/support-status.md](docs/support-status.md).

## Testing and quality

```powershell
pytest
ruff check .
mypy
```

Synthetic test input is under `tests/fixtures/synthetic/`. Never add real captures without sanitizing addresses, hostnames, credentials, and other sensitive values.

## Scope and limitations

Implemented operational commands are `show ip route`, `show interfaces status`, `show ip interface brief`, and `show vlan brief` under IOS and IOS XE registry keys. Unsupported line formats are reported rather than silently discarded; see [docs/support-status.md](docs/support-status.md) for accepted forms and limitations.

OSPF/BGP operational parsers, broader Cisco configuration semantics, other vendors, default inference, natural-language checks, semantic diff, and topology discovery remain unimplemented. IPv6 and LLDP remain explicitly excluded.
