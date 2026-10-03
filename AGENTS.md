# Development guidance

- Target Python 3.12+ and keep runtime dependencies in the standard library unless a concrete need is documented.
- Keep device-specific parsing in vendor/OS parser modules; keep common models and parser orchestration vendor-neutral.
- Do not infer unsupported syntax, device defaults, OS identity, or physical links.
- Return `SUCCESS`, `PARTIAL_SUCCESS`, or `FAILED` accurately. Preserve issues and source references for recoverable failures.
- Keep credentials and captured configuration text out of logs and error messages.
- Do not claim device verification from synthetic fixtures. Update `docs/support-status.md` when support status changes.
- Add focused pytest coverage for new behavior and regression tests for changed behavior.
- Before completing a change, run `pytest`, `ruff check .`, and `mypy` in a Python 3.12+ environment.
- Do not automatically advance the roadmap to a later phase without explicit user confirmation.
