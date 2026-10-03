"""Input loading and non-destructive text normalization."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from nwconfig_parser.models import SourceReference


@dataclass(frozen=True, slots=True)
class InputDocument:
    raw_text: str
    normalized_text: str
    source_metadata: dict[str, str]


def normalize_newlines(text: str) -> str:
    """Return text with CRLF and CR line endings converted to LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def load_text_file(
    path: str | Path,
    encodings: tuple[str, ...] = ("utf-8-sig", "utf-16"),
) -> InputDocument:
    """Load text without altering the decoded original.

    Decoding failures are reported instead of silently replacing input bytes.
    """
    file_path = Path(path)
    payload = file_path.read_bytes()
    errors: list[str] = []
    for encoding in encodings:
        try:
            text = payload.decode(encoding)
        except (UnicodeDecodeError, LookupError) as exc:
            errors.append(f"{encoding}: {exc}")
            continue
        return InputDocument(
            raw_text=text,
            normalized_text=normalize_newlines(text),
            source_metadata={"filename": str(file_path), "encoding": encoding},
        )
    raise UnicodeError(
        f"Could not decode {file_path} using the configured encodings: "
        + "; ".join(errors)
    )


def source_reference(
    filename: str | None,
    command: str | None,
    start_line: int,
    end_line: int | None = None,
) -> SourceReference:
    return SourceReference(
        filename=filename,
        command=command,
        start_line=start_line,
        end_line=end_line if end_line is not None else start_line,
    )
