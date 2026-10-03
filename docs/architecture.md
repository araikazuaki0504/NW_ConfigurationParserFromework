# Architecture

## Data path

1. Input loading decodes a text file and retains both decoded raw text and a newline-normalized representation.
2. `ParserEngine` receives the normalized text and `ParseContext`.
3. `ParserRegistry` selects exactly one parser using vendor, OS family, optional OS version, and command. It rejects overlaps at registration and fails explicitly when no unique parser is available.
4. A parser returns `ParseResult`, including status, parsed data, issues, source references, parser metadata, and source metadata.
5. The CLI serializes this result to JSON without mixing diagnostics into standard output.

## Key responsibilities

- `Parser`: common parser protocol expressed as an abstract base class.
- `ParserRegistry`: registrations, selection, conflict detection, and metadata listing.
- `ParserEngine`: stable application-facing parse/validate entry point.
- `ParseResult`, `ParseIssue`, `SourceReference`: outcome, diagnostics, and traceability.
- `models.py`: shared network, routing, policy, topology, and verification dataclasses.

## Extension boundary

Device-specific command output syntax belongs in a vendor/OS-specific parser. A new parser is registered by the caller; adding one does not require changing `ParserEngine`. Avoid promoting synthetic output into a supported operational parser. Preserve unknown data in vendor extensions rather than assigning guessed common semantics.

## Status semantics

- `SUCCESS`: the parser recognized all input covered by its declared grammar.
- `PARTIAL_SUCCESS`: useful data was recovered, but one or more ranges could not be parsed.
- `FAILED`: parsing did not yield a trustworthy result.

The CLI returns exit codes 0, 1, and 2 for these respective statuses. Input, selection, and output errors also return 2.
