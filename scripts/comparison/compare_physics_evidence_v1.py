#!/usr/bin/env python3
"""Pair comparator and first-discordance locator for physics evidence v1."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from physics_evidence_v1 import (
        DOMAIN_KEYS,
        EvidenceFormatError,
        EvidenceTrace,
        parse_physics_evidence_v1,
        read_verified_event,
    )
except ImportError:  # pragma: no cover - package-style import convenience
    from .physics_evidence_v1 import (
        DOMAIN_KEYS,
        EvidenceFormatError,
        EvidenceTrace,
        parse_physics_evidence_v1,
        read_verified_event,
    )


COMPONENT_DOMAIN = {
    "command": "canonical_command_sha256",
    "body": "canonical_body_state_sha256",
    "contact": "canonical_contact_and_clearance_sha256",
    "clearance": "canonical_contact_and_clearance_sha256",
    "contact_and_clearance": "canonical_contact_and_clearance_sha256",
    "mission": "canonical_mission_summary_sha256",
}


class EvidencePairError(ValueError):
    """Both artifacts may be valid individually but are not a valid pair."""


@dataclass(frozen=True)
class FirstDiscordance:
    event_index: int | None
    event: str
    boundary: int | None
    tick: int | None
    substep: int | None
    engine_step: int | None
    simulated_time_ms: int | None
    primary_domain: str
    coincident_domains: tuple[str, ...]
    coincident_components: tuple[str, ...]
    resolution: str
    leaf_path: str | None
    left: Any
    right: Any
    ordering_note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_index": self.event_index,
            "event": self.event,
            "boundary": self.boundary,
            "tick": self.tick,
            "substep": self.substep,
            "engine_step": self.engine_step,
            "simulated_time_ms": self.simulated_time_ms,
            "primary_domain": self.primary_domain,
            "coincident_domains": list(self.coincident_domains),
            "coincident_components": list(self.coincident_components),
            "resolution": self.resolution,
            "leaf_path": self.leaf_path,
            "left": self.left,
            "right": self.right,
            "ordering_note": self.ordering_note,
        }


@dataclass(frozen=True)
class PairComparison:
    equal: bool
    events_compared: int
    left: EvidenceTrace
    right: EvidenceTrace
    first_discordance: FirstDiscordance | None

    def public_report(self) -> dict[str, Any]:
        return {
            "schema": "dancers-planar-physics-evidence-pair-comparison-v1",
            "valid_pair": True,
            "equal": self.equal,
            "events_compared": self.events_compared,
            "projection_pair": [self.left.detail, self.right.detail],
            "cross_projection": self.left.detail != self.right.detail,
            "common_scene_semantic_sha256": self.left.common_scene_semantic_sha256,
            "left": {
                "source": str(self.left.source),
                "detail": self.left.detail,
                "file_sha256": self.left.file_sha256,
                "domain_digests": self.left.domain_digests,
            },
            "right": {
                "source": str(self.right.source),
                "detail": self.right.detail,
                "file_sha256": self.right.file_sha256,
                "domain_digests": self.right.domain_digests,
            },
            "first_discordance": (
                None if self.first_discordance is None else self.first_discordance.as_dict()
            ),
            "classification": {
                "scientific_pair_eligible": False,
                "reference_optimized_claim_authenticated": False,
                "paper_metrics_applicable": False,
                "role": "engineering semantic comparison only",
                "reason": "physics evidence v1 has null method/source/binary/controller/configuration/plan bindings",
            },
            "payload_note": (
                "projection-specific payload SHA values are artifact-integrity fields and are intentionally ignored across digest/replay projections"
            ),
        }


def _bounded(value: Any, limit: int = 512) -> Any:
    if isinstance(value, (str, int, bool)) or value is None:
        text = repr(value)
        return value if len(text) <= limit else text[: limit - 3] + "..."
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value if len(text) <= limit else text[: limit - 3] + "..."


def _first_leaf(left: Any, right: Any, path: str = "$") -> tuple[str, Any, Any] | None:
    if type(left) is not type(right):
        return path, left, right
    if isinstance(left, dict):
        left_keys = list(left)
        right_keys = list(right)
        if left_keys != right_keys:
            return path + ".<keys>", left_keys, right_keys
        for key in left_keys:
            found = _first_leaf(left[key], right[key], f"{path}.{key}")
            if found is not None:
                return found
        return None
    if isinstance(left, list):
        if len(left) != len(right):
            return path + ".length", len(left), len(right)
        for index, (left_item, right_item) in enumerate(zip(left, right)):
            found = _first_leaf(left_item, right_item, f"{path}[{index}]")
            if found is not None:
                return found
        return None
    return None if left == right else (path, left, right)


def _component_value(event: Mapping[str, Any], component: str) -> Any:
    if component == "command":
        return event.get("records")
    if component == "body":
        return event.get("body_state_frame")
    if component == "contact":
        return event.get("contact_frame")
    if component in ("clearance", "contact_and_clearance"):
        return event.get("clearance_frame")
    if component == "mission":
        return event.get("mission")
    return event


def _unique(sequence: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for item in sequence:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return tuple(result)


def _event_discordance(
    left: EvidenceTrace,
    right: EvidenceTrace,
    index: int,
) -> FirstDiscordance | None:
    left_locator = left.event_locators[index]
    right_locator = right.event_locators[index]
    chronology_left = (
        left_locator.event,
        left_locator.boundary,
        left_locator.tick,
        left_locator.substep,
        left_locator.engine_step,
        left_locator.simulated_time_ms,
    )
    chronology_right = (
        right_locator.event,
        right_locator.boundary,
        right_locator.tick,
        right_locator.substep,
        right_locator.engine_step,
        right_locator.simulated_time_ms,
    )
    if chronology_left != chronology_right:
        # Exact lattice validation makes this unreachable for a valid common
        # scene, but retaining it avoids silently misaligning future changes.
        return FirstDiscordance(
            event_index=index,
            event="lattice",
            boundary=left_locator.boundary,
            tick=left_locator.tick,
            substep=left_locator.substep,
            engine_step=left_locator.engine_step,
            simulated_time_ms=left_locator.simulated_time_ms,
            primary_domain="event_lattice",
            coincident_domains=("event_lattice",),
            coincident_components=("event_lattice",),
            resolution="structural",
            leaf_path="$.event-locator",
            left=chronology_left,
            right=chronology_right,
            ordering_note="lattice mismatch is structural, not a causal behavior ordering",
        )
    left_components = left_locator.components
    right_components = right_locator.components
    names = _unique(tuple(left_components) + tuple(right_components))
    changed = tuple(
        name for name in names if left_components.get(name) != right_components.get(name)
    )
    counts_changed = left_locator.counts != right_locator.counts
    semantic_changed = left_locator.semantic_sha256 != right_locator.semantic_sha256
    if not changed and not counts_changed and not semantic_changed:
        return None
    if changed:
        domains = _unique(tuple(COMPONENT_DOMAIN[name] for name in changed))
        primary = domains[0]
    elif counts_changed:
        domains = ("event_counts",)
        primary = domains[0]
        changed = ("event_counts",)
    else:
        domains = ("event_semantic_observer",)
        primary = domains[0]
        changed = ("event_semantic_observer",)
    resolution = "frame-only"
    leaf_path: str | None = None
    left_leaf: Any = None
    right_leaf: Any = None
    if left.detail == right.detail == "replay":
        left_event = read_verified_event(left, index)
        right_event = read_verified_event(right, index)
        if changed[0] in COMPONENT_DOMAIN:
            left_value = _component_value(left_event, changed[0])
            right_value = _component_value(right_event, changed[0])
            found = _first_leaf(left_value, right_value, f"$.{changed[0]}")
        else:
            found = _first_leaf(left_event, right_event)
        if found is not None:
            leaf_path, left_leaf, right_leaf = found
            resolution = "replay-leaf"
    if resolution == "frame-only":
        if changed[0] in left_components or changed[0] in right_components:
            left_leaf = left_components.get(changed[0])
            right_leaf = right_components.get(changed[0])
        elif counts_changed:
            left_leaf = dict(left_locator.counts)
            right_leaf = dict(right_locator.counts)
        else:
            left_leaf = left_locator.semantic_sha256
            right_leaf = right_locator.semantic_sha256
    return FirstDiscordance(
        event_index=index,
        event=left_locator.event,
        boundary=left_locator.boundary,
        tick=left_locator.tick,
        substep=left_locator.substep,
        engine_step=left_locator.engine_step,
        simulated_time_ms=left_locator.simulated_time_ms,
        primary_domain=primary,
        coincident_domains=domains,
        coincident_components=changed,
        resolution=resolution,
        leaf_path=leaf_path,
        left=_bounded(left_leaf),
        right=_bounded(right_leaf),
        ordering_note=(
            "all changed components at this timestamp are coincident; schema component order selects a stable primary and is not a causal claim"
        ),
    )


def compare_verified_traces(left: EvidenceTrace, right: EvidenceTrace) -> PairComparison:
    """Compare two already-verified artifacts without trusting filename labels."""
    if left.common_scene_semantic_sha256 != right.common_scene_semantic_sha256:
        raise EvidencePairError(
            "common-scene semantic SHA-256 differs; artifacts are not the same scene/workload pair"
        )
    if left.header["initialization"] != right.header["initialization"]:
        raise EvidencePairError(
            "v1 initialization/classification differs; current engineering schema has no method envelope that could legitimately vary"
        )
    if len(left.event_locators) != len(right.event_locators):
        raise EvidencePairError("verified event inventories differ despite a common scene")
    for index in range(len(left.event_locators)):
        discordance = _event_discordance(left, right, index)
        if discordance is not None:
            return PairComparison(False, index + 1, left, right, discordance)
    if left.mission_terminal_frame_sha256 != right.mission_terminal_frame_sha256:
        resolution = "frame-only"
        leaf_path: str | None = None
        left_leaf: Any = left.mission_terminal_frame_sha256
        right_leaf: Any = right.mission_terminal_frame_sha256
        if left.detail == right.detail == "replay":
            found = _first_leaf(left.terminal["summary"], right.terminal["summary"], "$.summary")
            if found is not None:
                leaf_path, left_leaf, right_leaf = found
                resolution = "replay-leaf"
        discordance = FirstDiscordance(
            event_index=None,
            event="terminal",
            boundary=left.controller_ticks,
            tick=None,
            substep=None,
            engine_step=left.physics_steps,
            simulated_time_ms=left.simulated_time_ms,
            primary_domain="canonical_mission_summary_sha256",
            coincident_domains=("canonical_mission_summary_sha256",),
            coincident_components=("mission_terminal",),
            resolution=resolution,
            leaf_path=leaf_path,
            left=_bounded(left_leaf),
            right=_bounded(right_leaf),
            ordering_note="terminal summary follows every fixed-H event",
        )
        return PairComparison(False, len(left.event_locators), left, right, discordance)
    changed_domains = tuple(
        key for key in DOMAIN_KEYS if left.domain_digests[key] != right.domain_digests[key]
    )
    if changed_domains:
        # A valid parser should have localized any frame-ref change first.  This
        # branch remains a fail-closed assertion for corrupted/future objects.
        discordance = FirstDiscordance(
            event_index=None,
            event="terminal-domain",
            boundary=left.controller_ticks,
            tick=None,
            substep=None,
            engine_step=left.physics_steps,
            simulated_time_ms=left.simulated_time_ms,
            primary_domain=changed_domains[0],
            coincident_domains=changed_domains,
            coincident_components=("terminal_domain_digest",),
            resolution="domain-only",
            leaf_path=None,
            left={key: left.domain_digests[key] for key in changed_domains},
            right={key: right.domain_digests[key] for key in changed_domains},
            ordering_note="domain ordering is stable schema order and is not a causal claim",
        )
        return PairComparison(False, len(left.event_locators), left, right, discordance)
    return PairComparison(True, len(left.event_locators), left, right, None)


def compare_physics_evidence_v1(
    left_path: os.PathLike[str] | str,
    right_path: os.PathLike[str] | str,
) -> PairComparison:
    """Fully verify both artifacts before reporting equality or discordance."""
    # Parsing both before comparison deliberately rejects a malformed suffix
    # even when an early semantic mismatch exists.
    left = parse_physics_evidence_v1(left_path)
    right = parse_physics_evidence_v1(right_path)
    return compare_verified_traces(left, right)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Strictly compare two complete planar-physics evidence v1 artifacts"
    )
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = compare_physics_evidence_v1(args.left, args.right)
    except (EvidenceFormatError, EvidencePairError, OSError) as error:
        report = {
            "schema": "dancers-planar-physics-evidence-pair-comparison-v1",
            "valid_pair": False,
            "equal": False,
            "error": str(error),
            "classification": {
                "scientific_pair_eligible": False,
                "reference_optimized_claim_authenticated": False,
            },
        }
        print(json.dumps(report, indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":")), file=sys.stderr)
        return 2
    print(json.dumps(result.public_report(), indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":"), sort_keys=False))
    return 0 if result.equal else 1


if __name__ == "__main__":
    raise SystemExit(main())
