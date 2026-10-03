"""IPv4 prefix-list evaluation that refuses to decide on incomplete data."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import IPv4Network

from nwconfig_parser.models import EvaluationStatus, PrefixList, PrefixListEntry


class PrefixListEvaluationError(ValueError):
    """The list cannot be evaluated without making an unsupported assumption."""


@dataclass(frozen=True, slots=True)
class PrefixListEvaluation:
    status: EvaluationStatus
    action: str | None
    matched_sequence: int | None = None
    matched_entry: PrefixListEntry | None = None
    implicit_deny: bool = False
    reason: str | None = None


def matches_entry(candidate: IPv4Network, entry: PrefixListEntry) -> bool:
    if not candidate.subnet_of(entry.prefix):
        return False
    candidate_length = candidate.prefixlen
    if entry.ge is None and entry.le is None:
        return candidate_length == entry.prefix.prefixlen
    minimum = entry.ge if entry.ge is not None else entry.prefix.prefixlen
    maximum = entry.le if entry.le is not None else 32
    return minimum <= candidate_length <= maximum


def evaluate_prefix_list_detailed(
    prefix_list: PrefixList, candidate: str | IPv4Network
) -> PrefixListEvaluation:
    """Evaluate in sequence order; INDETERMINATE when the list may be incomplete."""
    network = (
        IPv4Network(candidate, strict=True) if isinstance(candidate, str) else candidate
    )
    if not prefix_list.complete:
        return PrefixListEvaluation(
            status=EvaluationStatus.INDETERMINATE,
            action=None,
            reason="Prefix-list parsing was incomplete: "
            + "; ".join(prefix_list.incomplete_reasons),
        )
    if any(entry.sequence is None for entry in prefix_list.entries):
        return PrefixListEvaluation(
            status=EvaluationStatus.INDETERMINATE,
            action=None,
            reason=(
                "At least one entry has no sequence number; effective device "
                "ordering cannot be determined safely."
            ),
        )
    ordered = sorted(prefix_list.entries, key=lambda entry: entry.sequence or 0)
    for entry in ordered:
        if matches_entry(network, entry):
            return PrefixListEvaluation(
                status=EvaluationStatus.EVALUATED,
                action=entry.action,
                matched_sequence=entry.sequence,
                matched_entry=entry,
            )
    return PrefixListEvaluation(
        status=EvaluationStatus.EVALUATED, action="deny", implicit_deny=True
    )


def evaluate_prefix_list(prefix_list: PrefixList, candidate: str | IPv4Network) -> str:
    result = evaluate_prefix_list_detailed(prefix_list, candidate)
    if result.status is not EvaluationStatus.EVALUATED or result.action is None:
        raise PrefixListEvaluationError(result.reason or "indeterminate")
    return result.action
