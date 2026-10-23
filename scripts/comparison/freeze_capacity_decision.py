#!/usr/bin/env python3
"""Freeze the post-calibration swarm-capacity decision, without running trials.

The input analysis must be the fail-closed calibration analysis produced by
``analyze_results.py``.  This command derives every family decision from the
analysis' all-gate records, verifies the registered calibration sessions and
preregistered grids, derives the validation timeout, and writes one immutable
canonical JSON artifact.  It never reads validation/audit results.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


DECISION_SCHEMA = "dancers-capacity-decision-v1"
DECISION_STATUS = "frozen-before-validation"
SELECTION_RULE_ID = "highest-all-gate-calibration-passing-n-v1"
FAMILIES = (
    "open",
    "perfect_maze",
    "fractal_low",
    "fractal_medium",
    "fractal_high",
)
VALIDATION_SEEDS = tuple(range(11, 31))
AUDIT_SEEDS = tuple(range(31, 51))
MAXIMUM_TIMEOUT_SECONDS = 1_800.0
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FULL_GATE_KEYS = (
    "cpu_budget_pass",
    "wall_budget_pass",
    "memory_pass",
    "same_n_fidelity_pass",
    "paper_metrics_pass",
    "summary_parity_pass",
    "task_validity_pass",
    "collision_state_pass",
    "complete_seed_coverage",
)


class DecisionError(RuntimeError):
    """The calibration evidence cannot support an immutable decision."""


class _DuplicateKey(ValueError):
    pass


@dataclass(frozen=True)
class FrozenCapacityDecision:
    """Path and digest of a newly frozen decision artifact."""

    path: Path
    sha256: str


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value!r}")


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _regular_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if path.is_symlink() or resolved.is_symlink() or not resolved.is_file():
        raise DecisionError(f"{label} must be a non-symlink regular file")
    return resolved


def _directory(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if path.is_symlink() or resolved.is_symlink() or not resolved.is_dir():
        raise DecisionError(f"{label} must be a non-symlink directory")
    return resolved


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    resolved = _regular_file(path, label)
    data = resolved.read_bytes()
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateKey,
        ValueError,
    ) as error:
        raise DecisionError(f"{label} is not strict JSON: {error}") from error
    if not isinstance(value, dict):
        raise DecisionError(f"{label} must contain one JSON object")
    return value, data


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise DecisionError(
            f"{label} must be 64 lower-case hexadecimal digits"
        )
    return value


def _artifact(path: Path, label: str) -> dict[str, str]:
    resolved = _regular_file(path, label)
    return {"path": str(resolved), "sha256": sha256_file(resolved)}


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DecisionError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise DecisionError(f"{label} must be an array")
    return value


def _positive_number(value: Any, label: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
        or float(value) <= 0.0
    ):
        raise DecisionError(f"{label} must be a finite positive number")
    return float(value)


def _strict_grid(value: Any, label: str) -> tuple[int, ...]:
    raw = _list(value, label)
    if any(
        not isinstance(item, int) or isinstance(item, bool) or item <= 0
        for item in raw
    ):
        raise DecisionError(f"{label} must contain positive integers")
    grid = tuple(raw)
    if not grid or tuple(sorted(set(grid))) != grid:
        raise DecisionError(f"{label} must be strictly increasing and unique")
    return grid


def _registered_artifact_sha(value: Any, label: str) -> str:
    if isinstance(value, str):
        return _sha256(value, label)
    if isinstance(value, dict):
        return _sha256(value.get("sha256"), label)
    raise DecisionError(f"{label} is not an artifact registration")


def _verify_hash(path: Path, expected: Any, label: str) -> str:
    checked = _sha256(expected, f"{label} SHA-256")
    actual = sha256_file(_regular_file(path, label))
    if actual != checked:
        raise DecisionError(f"{label} SHA-256 mismatch: {actual} != {checked}")
    return checked


def _verify_session_records(
    analysis_input: dict[str, Any],
    *,
    analyzer_sha256: str,
    experiment_manifest_sha256: str,
    environment_manifest_sha256: str,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    records = _list(
        analysis_input.get("sessions"), "analysis.input.sessions"
    )
    if analysis_input.get("session_count") != len(records) or not records:
        raise DecisionError("analysis session_count is missing or inconsistent")
    verified: list[dict[str, Any]] = []
    capacity_plans: dict[str, dict[str, Any]] = {}
    seen_ids: set[str] = set()
    file_by_hash_key = {
        "session_json_sha256": "session.json",
        "run_manifest_json_sha256": "run_manifest.json",
        "frozen_plan_sha256": "frozen_plan.json",
        "runs_jsonl_sha256": "runs.jsonl",
        "pairs_jsonl_sha256": "pairs.jsonl",
        "capacity_json_sha256": "capacity.json",
    }
    for index, value in enumerate(records):
        label = f"analysis.input.sessions[{index}]"
        record = _mapping(value, label)
        session_id = record.get("session_id")
        if (
            not isinstance(session_id, str)
            or not session_id
            or session_id in seen_ids
        ):
            raise DecisionError(f"{label}.session_id is invalid or duplicated")
        seen_ids.add(session_id)
        mode = record.get("mode")
        if mode not in {"timing", "fidelity"}:
            raise DecisionError(f"{label}.mode must be timing or fidelity")
        path_value = record.get("path")
        if not isinstance(path_value, str) or not path_value:
            raise DecisionError(f"{label}.path must be a non-empty string")
        root = _directory(Path(path_value), f"{label}.path")
        hashes = _mapping(record.get("hashes"), f"{label}.hashes")
        if set(hashes) != set(file_by_hash_key):
            raise DecisionError(f"{label}.hashes has an unexpected schema")
        checked_hashes: dict[str, str | None] = {}
        for hash_key, filename in file_by_hash_key.items():
            expected = hashes[hash_key]
            target = root / filename
            if hash_key == "capacity_json_sha256" and expected is None:
                if target.exists() or target.is_symlink():
                    raise DecisionError(
                        f"{label} unexpectedly contains capacity.json"
                    )
                checked_hashes[hash_key] = None
                continue
            checked_hashes[hash_key] = _verify_hash(
                target, expected, f"{label} {filename}"
            )
        plan, _ = _load_json(root / "frozen_plan.json", f"{label} plan")
        if plan.get("phase") != "calibration" or plan.get("mode") != mode:
            raise DecisionError(f"{label} is not a matching calibration plan")
        if (
            plan.get("fixed_work_timesteps") != 100
            or plan.get("timestep_seconds") != 0.1
            or plan.get("idle_cooldown_seconds") != 300
            or plan.get("timeout_seconds") != 300.0
        ):
            raise DecisionError(
                f"{label} plan timing controls violate preregistration"
            )
        plan_artifacts = _mapping(
            plan.get("artifacts"), f"{label} plan.artifacts"
        )
        for artifact_name, expected_hash in (
            ("analysis_revision", analyzer_sha256),
            ("experiment_manifest", experiment_manifest_sha256),
            ("environment_manifest", environment_manifest_sha256),
        ):
            artifact_record = _mapping(
                plan_artifacts.get(artifact_name),
                f"{label} plan.artifacts.{artifact_name}",
            )
            if artifact_record.get("sha256") != expected_hash:
                raise DecisionError(
                    f"{label} plan {artifact_name} binding disagrees"
                )
        capacity = plan.get("capacity_search")
        if capacity is not None:
            capacity = _mapping(capacity, f"{label}.capacity_search")
            environment = capacity.get("environment")
            if mode != "timing" or environment not in FAMILIES:
                raise DecisionError(f"{label} has invalid capacity identity")
            if environment in capacity_plans:
                raise DecisionError(
                    f"multiple calibration capacity plans for {environment}"
                )
            capacity_plans[environment] = {
                "session_id": session_id,
                "plan": plan,
            }
        verified.append(
            {
                "session_id": session_id,
                "path": str(root),
                "mode": mode,
                "hashes": checked_hashes,
            }
        )
    if set(capacity_plans) != set(FAMILIES):
        missing = sorted(set(FAMILIES) - set(capacity_plans))
        raise DecisionError(
            f"one capacity calibration plan is required per family; "
            f"missing {missing}"
        )
    return verified, capacity_plans


def _validate_environment_registry(
    manifest: dict[str, Any],
    decisions: dict[str, Any],
) -> None:
    if manifest.get("families") != list(FAMILIES):
        raise DecisionError("environment manifest family registry changed")
    cases = _list(manifest.get("environments"), "environment environments")
    for family in FAMILIES:
        grid = tuple(decisions[family]["grid"])
        for split in ("calibration", "validation"):
            matches = [
                item
                for item in cases
                if isinstance(item, dict)
                and item.get("family") == family
                and item.get("split") == split
            ]
            if len(matches) != 1:
                raise DecisionError(
                    f"expected one environment registry for {family}/{split}"
                )
            capacity = _mapping(
                matches[0].get("capacity"),
                f"environment {family}/{split}.capacity",
            )
            registered = tuple(
                item.get("robot_count")
                for item in _list(
                    capacity.get("prefix_scenarios"),
                    f"environment {family}/{split}.prefix_scenarios",
                )
                if isinstance(item, dict)
            )
            if any(count not in registered for count in grid):
                raise DecisionError(
                    f"{family} decision grid is not registered in {split}"
                )


def _ceil_to_ten_seconds(value: float) -> float:
    return float(math.ceil(value / 10.0) * 10)


def _validate_decision_payload(value: dict[str, Any]) -> None:
    if set(value) != {
        "schema_version",
        "status",
        "selection_rule_id",
        "provenance",
        "seeds",
        "environments",
    }:
        raise DecisionError("decision has an unexpected top-level schema")
    if value.get("schema_version") != DECISION_SCHEMA:
        raise DecisionError(f"decision schema must be {DECISION_SCHEMA}")
    if value.get("status") != DECISION_STATUS:
        raise DecisionError(f"decision status must be {DECISION_STATUS}")
    if value.get("selection_rule_id") != SELECTION_RULE_ID:
        raise DecisionError("decision selection_rule_id changed")
    seeds = _mapping(value.get("seeds"), "decision.seeds")
    if seeds != {
        "validation": list(VALIDATION_SEEDS),
        "audit": list(AUDIT_SEEDS),
    }:
        raise DecisionError("decision held-out seed sets changed")
    provenance = _mapping(value.get("provenance"), "decision.provenance")
    if set(provenance) != {
        "calibration_analysis",
        "analyzer",
        "experiment_manifest",
        "environment_manifest",
        "calibration_sessions",
        "timeout_derivation",
    }:
        raise DecisionError("decision provenance has an unexpected schema")
    for name in (
        "calibration_analysis",
        "analyzer",
        "experiment_manifest",
        "environment_manifest",
    ):
        artifact = _mapping(provenance.get(name), f"provenance.{name}")
        if set(artifact) != {"path", "sha256"}:
            raise DecisionError(f"provenance.{name} has invalid artifact shape")
        if (
            not isinstance(artifact["path"], str)
            or not artifact["path"]
            or not Path(artifact["path"]).is_absolute()
        ):
            raise DecisionError(f"provenance.{name}.path is invalid")
        _sha256(artifact["sha256"], f"provenance.{name}.sha256")
    sessions = _list(
        provenance.get("calibration_sessions"),
        "provenance.calibration_sessions",
    )
    if not sessions:
        raise DecisionError("decision has no calibration session provenance")
    seen_session_ids: set[str] = set()
    session_hash_keys = {
        "session_json_sha256",
        "run_manifest_json_sha256",
        "frozen_plan_sha256",
        "runs_jsonl_sha256",
        "pairs_jsonl_sha256",
        "capacity_json_sha256",
    }
    for index, raw_session in enumerate(sessions):
        session = _mapping(
            raw_session, f"provenance.calibration_sessions[{index}]"
        )
        if set(session) != {"session_id", "path", "mode", "hashes"}:
            raise DecisionError("calibration session provenance is malformed")
        session_id = session["session_id"]
        if (
            not isinstance(session_id, str)
            or not session_id
            or session_id in seen_session_ids
        ):
            raise DecisionError("calibration session IDs are invalid")
        seen_session_ids.add(session_id)
        if session["mode"] not in {"timing", "fidelity"}:
            raise DecisionError("calibration session mode is invalid")
        if (
            not isinstance(session["path"], str)
            or not Path(session["path"]).is_absolute()
        ):
            raise DecisionError("calibration session path is invalid")
        hashes = _mapping(session["hashes"], "calibration session hashes")
        if set(hashes) != session_hash_keys:
            raise DecisionError("calibration session hashes are malformed")
        for key, item in hashes.items():
            if key == "capacity_json_sha256" and item is None:
                continue
            _sha256(item, f"calibration session {key}")
    timeout_basis = _mapping(
        provenance.get("timeout_derivation"),
        "provenance.timeout_derivation",
    )
    records = _list(value.get("environments"), "decision.environments")
    if len(records) != len(FAMILIES):
        raise DecisionError("decision must contain exactly five environments")
    by_family: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(records):
        record = _mapping(raw, f"decision.environments[{index}]")
        if set(record) != {
            "environment",
            "reference_anchor_n",
            "grid",
            "candidate_n_star",
            "next_grid_point",
            "validation_timing_counts",
            "validation_fidelity_counts",
            "validation_timeout_seconds",
        }:
            raise DecisionError("decision environment record is malformed")
        family = record.get("environment")
        if family not in FAMILIES or family in by_family:
            raise DecisionError("decision environments are unknown or duplicated")
        grid = _strict_grid(record.get("grid"), f"{family}.grid")
        anchor = record.get("reference_anchor_n")
        candidate = record.get("candidate_n_star")
        if anchor != grid[0]:
            raise DecisionError(f"{family} reference anchor is not grid[0]")
        if candidate not in grid:
            raise DecisionError(f"{family} candidate N* is not on its grid")
        candidate_index = grid.index(candidate)
        expected_next = (
            grid[candidate_index + 1]
            if candidate_index + 1 < len(grid)
            else None
        )
        if record.get("next_grid_point") != expected_next:
            raise DecisionError(
                f"{family} next_grid_point is not the immediate successor"
            )
        if record.get("validation_timing_counts") != list(grid):
            raise DecisionError(f"{family} timing counts are not the full grid")
        if record.get("validation_fidelity_counts") != list(grid):
            raise DecisionError(f"{family} fidelity counts are not the full grid")
        timeout = _positive_number(
            record.get("validation_timeout_seconds"),
            f"{family}.validation_timeout_seconds",
        )
        if timeout > MAXIMUM_TIMEOUT_SECONDS:
            raise DecisionError(f"{family} validation timeout exceeds 1800 s")
        basis = _mapping(timeout_basis.get(family), f"timeout basis {family}")
        if set(basis) != {
            "calibration_plan_timeout_seconds",
            "maximum_completed_measured_wall_seconds",
            "three_times_wall_ceiled_to_ten_seconds",
        }:
            raise DecisionError(f"{family} timeout basis is malformed")
        plan_timeout = _positive_number(
            basis.get("calibration_plan_timeout_seconds"),
            f"{family} calibration plan timeout",
        )
        maximum_wall = _positive_number(
            basis.get("maximum_completed_measured_wall_seconds"),
            f"{family} maximum completed wall time",
        )
        ceiled = _ceil_to_ten_seconds(3.0 * maximum_wall)
        if basis.get("three_times_wall_ceiled_to_ten_seconds") != ceiled:
            raise DecisionError(f"{family} timeout derivation was altered")
        if timeout != max(plan_timeout, ceiled):
            raise DecisionError(f"{family} validation timeout is not derived")
        by_family[family] = record
    if set(by_family) != set(FAMILIES) or list(by_family) != list(FAMILIES):
        raise DecisionError("decision environments must use canonical order")
    if set(timeout_basis) != set(FAMILIES):
        raise DecisionError("timeout derivation must cover all five families")


def load_capacity_decision(
    path: Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Load and structurally validate a frozen decision artifact."""

    resolved = _regular_file(path, "capacity decision")
    value, data = _load_json(resolved, "capacity decision")
    _validate_decision_payload(value)
    if data != canonical_json_bytes(value):
        raise DecisionError("capacity decision is not canonical JSON")
    return value, {"path": str(resolved), "sha256": sha256_file(resolved)}


def _exclusive_write(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o444)
    except OSError as error:
        if error.errno == errno.EEXIST:
            raise FileExistsError(
                f"refusing to overwrite existing output: {path}"
            ) from error
        raise
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path, 0o444)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def freeze_capacity_decision(
    *,
    calibration_analysis: Path,
    analyzer: Path,
    experiment_manifest: Path,
    environment_manifest: Path,
    output: Path,
) -> FrozenCapacityDecision:
    """Derive and exclusively freeze all five post-calibration decisions."""

    analysis_artifact = _artifact(
        calibration_analysis, "calibration analysis"
    )
    analyzer_artifact = _artifact(analyzer, "analyzer")
    experiment_artifact = _artifact(
        experiment_manifest, "experiment manifest"
    )
    environment_artifact = _artifact(
        environment_manifest, "environment manifest"
    )
    analysis, _ = _load_json(
        Path(analysis_artifact["path"]), "calibration analysis"
    )
    experiment, _ = _load_json(
        Path(experiment_artifact["path"]), "experiment manifest"
    )
    environment, _ = _load_json(
        Path(environment_artifact["path"]), "environment manifest"
    )
    if analysis.get("analysis_revision_sha256") != analyzer_artifact["sha256"]:
        raise DecisionError("analysis is not bound to the supplied analyzer")
    analysis_input = _mapping(analysis.get("input"), "analysis.input")
    if analysis_input.get("phase") != "calibration":
        raise DecisionError("capacity decisions require calibration analysis")
    shared = _mapping(
        analysis_input.get("shared_artifacts"),
        "analysis.input.shared_artifacts",
    )
    for key, expected in (
        ("analysis_revision_sha256", analyzer_artifact["sha256"]),
        ("experiment_manifest_sha256", experiment_artifact["sha256"]),
        ("environment_manifest_sha256", environment_artifact["sha256"]),
    ):
        if shared.get(key) != expected:
            raise DecisionError(f"analysis shared artifact {key} disagrees")
    software = _mapping(
        experiment.get("software_versions"),
        "experiment.software_versions",
    )
    registered_analysis = _registered_artifact_sha(
        software.get("analysis_revision"),
        "experiment analysis revision",
    )
    if registered_analysis != analyzer_artifact["sha256"]:
        raise DecisionError("experiment analyzer registration disagrees")
    environment_registration = _mapping(
        experiment.get("environment_and_map"),
        "experiment.environment_and_map",
    )
    if (
        environment_registration.get("map_manifest_sha256")
        != environment_artifact["sha256"]
    ):
        raise DecisionError("experiment environment registration disagrees")
    design = _mapping(
        experiment.get("calibration_design"),
        "experiment.calibration_design",
    )
    if (
        design.get("status") != "frozen-before-calibration"
        or design.get("full_grid_execution") is not True
        or design.get("idle_cooldown_seconds") != 300
    ):
        raise DecisionError("experiment calibration design is not frozen")
    design_timeout = _positive_number(
        design.get("timeout_seconds"), "calibration design timeout"
    )
    if design_timeout != 300.0:
        raise DecisionError("calibration design timeout must equal 300 seconds")
    design_environments = _mapping(
        design.get("environments"), "calibration design environments"
    )
    if set(design_environments) != set(FAMILIES):
        raise DecisionError("calibration design does not cover five families")
    verified_sessions, capacity_plans = _verify_session_records(
        analysis_input,
        analyzer_sha256=analyzer_artifact["sha256"],
        experiment_manifest_sha256=experiment_artifact["sha256"],
        environment_manifest_sha256=environment_artifact["sha256"],
    )
    capacity = _mapping(analysis.get("capacity"), "analysis.capacity")
    decisions = _mapping(
        capacity.get("calibration_decisions"),
        "analysis.capacity.calibration_decisions",
    )
    if set(decisions) != set(FAMILIES):
        raise DecisionError(
            "analysis must contain one calibration decision per family"
        )

    frozen_records: list[dict[str, Any]] = []
    timeout_derivation: dict[str, dict[str, float]] = {}
    decision_by_family: dict[str, dict[str, Any]] = {}
    for family in FAMILIES:
        label = f"analysis.capacity.calibration_decisions.{family}"
        source = _mapping(decisions[family], label)
        grid = _strict_grid(source.get("grid"), f"{label}.grid")
        design_record = _mapping(
            design_environments.get(family),
            f"calibration design {family}",
        )
        design_grid = _strict_grid(
            design_record.get("grid"), f"calibration design {family}.grid"
        )
        if grid != design_grid:
            raise DecisionError(f"{family} analysis grid is not preregistered")
        if design_record.get("timing_counts") != list(grid):
            raise DecisionError(f"{family} timing grid is not preregistered")
        if design_record.get("fidelity_counts") != list(grid):
            raise DecisionError(f"{family} fidelity grid is not preregistered")
        anchor = source.get("reference_anchor_n")
        if anchor != grid[0] or anchor != design_record.get(
            "reference_anchor_n"
        ):
            raise DecisionError(f"{family} reference anchor disagrees")
        per_grid = _mapping(source.get("per_grid"), f"{label}.per_grid")
        if set(per_grid) != {str(count) for count in grid}:
            raise DecisionError(
                f"{family} lacks full per-grid all-gate evidence"
            )
        passing: list[int] = []
        for count in grid:
            gate_label = f"{label}.per_grid.{count}"
            gates = _mapping(per_grid[str(count)], gate_label)
            if set(gates) != set(FULL_GATE_KEYS) | {"all_gates_pass"}:
                raise DecisionError(f"{gate_label} lacks the full gate set")
            for key in FULL_GATE_KEYS + ("all_gates_pass",):
                if not isinstance(gates[key], bool):
                    raise DecisionError(f"{gate_label}.{key} must be boolean")
            combined = all(gates[key] for key in FULL_GATE_KEYS)
            if gates["all_gates_pass"] is not combined:
                raise DecisionError(f"{gate_label}.all_gates_pass disagrees")
            if combined:
                passing.append(count)
        if not passing:
            raise DecisionError(f"{family} has no all-gate passing grid point")
        candidate = max(passing)
        if source.get("calibration_passing_n") != candidate:
            raise DecisionError(
                f"{family} calibration_passing_n is not the highest pass"
            )
        candidate_index = grid.index(candidate)
        next_grid_point = (
            grid[candidate_index + 1]
            if candidate_index + 1 < len(grid)
            else None
        )
        if source.get("next_grid_point") != next_grid_point:
            raise DecisionError(
                f"{family} analyzer next_grid_point is not immediate"
            )
        plan = capacity_plans[family]["plan"]
        plan_capacity = _mapping(
            plan.get("capacity_search"),
            f"{family} calibration plan capacity_search",
        )
        if (
            plan_capacity.get("reference_anchor_n") != anchor
            or plan_capacity.get("grid") != list(grid)
            or plan.get("timeout_seconds") != design_timeout
        ):
            raise DecisionError(
                f"{family} calibration plan violates preregistration"
            )
        plan_timeout = _positive_number(
            plan.get("timeout_seconds"), f"{family} plan timeout"
        )
        walls = [
            _positive_number(item, f"{family} completed wall time")
            for item in _list(
                source.get("completed_measured_wall_seconds"),
                f"{label}.completed_measured_wall_seconds",
            )
        ]
        if not walls:
            raise DecisionError(
                f"{family} has no completed measured wall observation"
            )
        if walls != sorted(walls):
            raise DecisionError(
                f"{family} completed wall observations are not sorted"
            )
        maximum_wall = max(walls)
        ceiled = _ceil_to_ten_seconds(3.0 * maximum_wall)
        required_timeout = max(plan_timeout, ceiled)
        if required_timeout > MAXIMUM_TIMEOUT_SECONDS:
            raise DecisionError(
                f"{family} required timeout exceeds 1800 seconds"
            )
        timeout_derivation[family] = {
            "calibration_plan_timeout_seconds": plan_timeout,
            "maximum_completed_measured_wall_seconds": maximum_wall,
            "three_times_wall_ceiled_to_ten_seconds": ceiled,
        }
        record = {
            "environment": family,
            "reference_anchor_n": anchor,
            "grid": list(grid),
            "candidate_n_star": candidate,
            "next_grid_point": next_grid_point,
            "validation_timing_counts": list(grid),
            "validation_fidelity_counts": list(grid),
            "validation_timeout_seconds": required_timeout,
        }
        frozen_records.append(record)
        decision_by_family[family] = record

    _validate_environment_registry(environment, decision_by_family)
    value = {
        "schema_version": DECISION_SCHEMA,
        "status": DECISION_STATUS,
        "selection_rule_id": SELECTION_RULE_ID,
        "provenance": {
            "calibration_analysis": analysis_artifact,
            "analyzer": analyzer_artifact,
            "experiment_manifest": experiment_artifact,
            "environment_manifest": environment_artifact,
            "calibration_sessions": verified_sessions,
            "timeout_derivation": timeout_derivation,
        },
        "seeds": {
            "validation": list(VALIDATION_SEEDS),
            "audit": list(AUDIT_SEEDS),
        },
        "environments": frozen_records,
    }
    _validate_decision_payload(value)
    data = canonical_json_bytes(value)
    destination = output.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    _exclusive_write(destination, data)
    return FrozenCapacityDecision(
        path=destination, sha256=hashlib.sha256(data).hexdigest()
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calibration-analysis", type=Path, required=True)
    parser.add_argument("--analyzer", type=Path, required=True)
    parser.add_argument("--experiment-manifest", type=Path, required=True)
    parser.add_argument("--environment-manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        frozen = freeze_capacity_decision(
            calibration_analysis=args.calibration_analysis,
            analyzer=args.analyzer,
            experiment_manifest=args.experiment_manifest,
            environment_manifest=args.environment_manifest,
            output=args.output,
        )
    except (
        DecisionError,
        FileExistsError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        print(f"capacity decision freeze failed: {error}", file=sys.stderr)
        return 1
    print(f"{frozen.sha256}  {frozen.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
