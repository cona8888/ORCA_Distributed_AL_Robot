#!/usr/bin/env python3
"""Fail-closed analysis of frozen paired DANCeRS comparison sessions.

Only complete runner sessions are accepted.  The analyzer verifies the
session, manifest, attempt, pair, and on-disk artifact records before it
calculates any estimate.  Failed attempts remain in the failure ledger;
structurally missing or contradictory records invalidate the analysis.

Trace v2 remains inspectable with ``trace_compare.py`` and reproducible with
the pinned historical analyzer revision recorded below.  This analyzer admits
only independently verified trace-v3 digest artifacts as new evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    from scripts.comparison.trace_compare import (
        TraceFormatError,
        TraceReader,
    )
except ModuleNotFoundError:  # Direct execution from scripts/comparison.
    from trace_compare import TraceFormatError, TraceReader


ANALYSIS_SCHEMA = "dancers-comparison-analysis-v1"
SESSION_SCHEMA = "dancers-laptop-comparison-session-v1"
PLAN_SCHEMA = "dancers-laptop-comparison-plan-v1"
RUN_SCHEMA = "dancers-laptop-comparison-attempt-v1"
PAIR_SCHEMA = "dancers-laptop-comparison-pair-v1"
CAPACITY_SCHEMA = "dancers-bounded-capacity-search-v2"
FROZEN_BOOTSTRAP_SEED = 250818153
MIN_BOOTSTRAP_SAMPLES = 10_000
TASK_VALIDITY_PREDICATE_ID = "fixed-horizon-safe-navigation-v1"
MAXIMUM_PEAK_RSS_MIB = 49_152.0
MINIMUM_FREE_DISK_BYTES = 21_474_836_480
POWER_MODE = "AC Power"
THERMAL_POLICY = (
    "ProcessInfo.thermalState rawValue must be 0 before every block; "
    "fail closed on probe error/change; five-minute idle cooldown before "
    "a measured session"
)
METHODS = ("reference", "optimized")
MODES = ("timing", "fidelity")
PHASE_SEEDS = {
    "calibration": set(range(1, 11)),
    "validation": set(range(11, 31)),
    "audit": set(range(31, 51)),
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,239}$")
REFERENCE_PIN = "986f47deec5b079663c7d7f6120bfc20f0467901"
OPTIMIZED_PIN = "99364a41f1535a4bb0eddbe5a10030127ea77086"
BUILD_MANIFEST_SCHEMA = "dancers-comparison-build-manifest-v1"
TRACE_SCHEMA = "dancers-comparison-trace-v3"
HISTORICAL_TRACE_SCHEMA = "dancers-comparison-trace-v2"
TRACE_DIGEST_STATE_SCHEMA = "robot-semantic-digest-v2"
TRACE_DIGEST_ALGORITHM = (
    "SHA-256 over robot-semantic-digest-v2 canonical typed binary encoding"
)
HISTORICAL_V2_ANALYZER_REVISION = (
    "3e48d1abf1ba109c474383533112b9219f0574e6"
)

SESSION_KEYS = {
    "schema_version",
    "completed_utc",
    "attempt_count",
    "warmup_attempt_count",
    "warmups_valid",
    "timestep_seconds",
    "idle_cooldown_seconds",
    "idle_cooldown",
    "pair_count",
    "all_attempts_completed",
    "all_pairs_valid",
    "stopped_early",
    "capacity",
    "capacity_evaluation",
    "minimum_free_disk_bytes",
    "maximum_peak_rss_mib",
    "power_mode",
    "thermal_policy",
    "task_validity_predicate_id",
    "host_contaminated_blocks",
}
MANIFEST_KEYS = {
    "schema_version",
    "created_utc",
    "plan_path",
    "plan_sha256",
    "phase",
    "mode",
    "serial_execution",
    "order_policy",
    "order_seed",
    "fixed_work_timesteps",
    "timestep_seconds",
    "idle_cooldown_seconds",
    "idle_cooldown",
    "repetitions",
    "warmups_per_binary_map",
    "warmup_blocks",
    "blocks",
    "capacity_search",
    "capacity_evaluation",
    "task_validity_predicate_id",
    "maximum_peak_rss_mib",
    "minimum_free_disk_bytes",
    "power_mode",
    "thermal_policy",
    "count_order_policy",
    "preflight_host_conditions",
}
BLOCK_KEYS = {
    "block_id",
    "pair_id",
    "repetition",
    "order",
    "environment",
    "seed",
    "robot_count",
}
WARMUP_BLOCK_KEYS = BLOCK_KEYS | {"map_sha256"}
RUN_KEYS = {
    "schema_version",
    "sequence",
    "attempt_id",
    "block_id",
    "pair_id",
    "repetition",
    "attempt_kind",
    "order",
    "order_position",
    "phase",
    "mode",
    "environment",
    "seed",
    "robot_count",
    "method",
    "terminal_category",
    "orchestration_error",
    "resource",
    "provenance",
    "artifacts",
    "completed_utc",
    "task_valid",
    "fidelity_metrics",
}
RESOURCE_KEYS = {
    "returncode",
    "exit_code",
    "signal",
    "timed_out",
    "wall_seconds",
    "user_cpu_seconds",
    "system_cpu_seconds",
    "total_cpu_seconds",
    "peak_rss_raw",
    "peak_rss_raw_unit",
    "peak_rss_mib",
    "maximum_peak_rss_mib",
    "peak_rss_within_limit",
}
PROVENANCE_KEYS = {
    "run_id",
    "implementation_id",
    "algorithm_revision",
    "source_head_revision",
    "source_tree_hash",
    "common_patch_sha256",
    "common_patch_full_index_diff_sha256",
    "binary_sha256",
    "build_manifest_sha256",
    "pixi_lock_sha256",
    "protocol_sha256",
    "experiment_manifest_sha256",
    "analysis_revision_sha256",
    "runner_sha256",
    "config_sha256",
    "map_sha256",
    "scenario_sha256",
    "omp_threads",
    "phase",
    "mode",
    "pair_id",
    "repetition",
    "order",
    "attempt_kind",
    "maximum_peak_rss_mib",
    "power_mode",
    "thermal_policy",
    "task_validity_predicate_id",
}
ARTIFACT_KEYS = {"config", "stdout", "stderr", "trace", "summary"}
ARTIFACT_RECORD_KEYS = {"path", "sha256"}
PAIR_KEYS = {
    "schema_version",
    "block_id",
    "pair_id",
    "repetition",
    "order",
    "phase",
    "mode",
    "environment",
    "seed",
    "robot_count",
    "attempt_ids",
    "config_sha256_equal",
    "terminal_categories",
    "semantic_trace_equal",
    "frames_compared",
    "mismatch",
    "pair_valid",
    "paper_metrics_equal",
    "summary_parity_fields_equal",
    "summary_mismatch",
    "task_validity_predicate_id",
    "task_valid",
    "host_conditions_before",
    "host_conditions_after",
    "host_block_valid",
    "block_terminal_category",
}
CAPACITY_KEYS = {
    "schema_version",
    "environment",
    "grid",
    "max_points",
    "budget",
    "evaluations",
    "largest_passing_n",
    "first_failing_n",
    "stop_reason",
    "unexecuted_grid_points",
    "status",
    "validated",
    "provisional_largest_compute_passing_n",
    "first_provisional_compute_failing_n",
    "pending_gates",
    "complete",
}
GLOBAL_PROVENANCE_KEYS = (
    "common_patch_sha256",
    "common_patch_full_index_diff_sha256",
    "build_manifest_sha256",
    "pixi_lock_sha256",
    "protocol_sha256",
    "experiment_manifest_sha256",
    "analysis_revision_sha256",
    "runner_sha256",
)
RESOURCE_METRICS = {
    "total_cpu_seconds": "seconds",
    "wall_seconds": "seconds",
    "peak_rss_mib": "MiB",
    "cpu_seconds_per_robot_timestep": "seconds/robot-timestep",
    "robot_timesteps_per_cpu_second": "robot-timesteps/second",
}
CONFIRMATORY_ENVIRONMENTS = (
    "open",
    "perfect_maze",
    "fractal_low",
    "fractal_medium",
    "fractal_high",
)
HOLM_ALPHA = 0.05
FROZEN_PLAN_CONTROL_KEYS = {
    "schema_version",
    "phase",
    "mode",
    "fixed_work_timesteps",
    "timestep_seconds",
    "idle_cooldown_seconds",
    "timeout_seconds",
    "repetitions",
    "warmups_per_binary_map",
    "omp_threads",
    "order_seed",
    "minimum_free_disk_bytes",
    "maximum_peak_rss_mib",
    "power_mode",
    "thermal_policy",
    "task_validity_predicate_id",
}
FROZEN_PLAN_REQUIRED_KEYS = FROZEN_PLAN_CONTROL_KEYS | {
    "runner_sha256",
    "run_cwd",
    "artifacts",
    "methods",
    "pairs",
}
FROZEN_PLAN_OPTIONAL_KEYS = {
    "termination_grace_seconds",
    "trace_detail",
    "capacity_search",
    "capacity_evaluation",
}
PLAN_ARTIFACT_KEYS = {
    "pixi_lock",
    "build_manifest",
    "environment_manifest",
    "config_template",
    "protocol",
    "experiment_manifest",
    "analysis_revision",
}
CAPACITY_EVALUATION_KEYS = {
    "environment",
    "role",
    "reference_anchor_n",
    "candidate_n_star",
    "next_grid_point",
    "grid",
}
PROTECTED_CONFIG_KEYS = {
    "SIMULATION_NAME",
    "LOG_RESULTS",
    "ASSETS_DIR",
    "OBSTACLE_FILE",
    "SCENARIO",
    "SCENARIO_FILE",
    "NUM_ROBOTS",
    "RNG_SEED",
    "MAX_TIME",
    "TIMESTEP",
    "DISPLAY",
    "DRAW_INTERROBOT",
    "DRAW_PATH",
    "DRAW_TRAJ",
    "DRAW_WAYPOINTS",
}
class AnalysisError(RuntimeError):
    """Raised when raw inputs cannot support a trustworthy analysis."""


class _DuplicateKey(ValueError):
    pass


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


def _require_regular_file(path: Path, label: str) -> None:
    if path.is_symlink():
        raise AnalysisError(f"{label} must not be a symbolic link: {path}")
    if not path.is_file():
        raise AnalysisError(f"{label} is not a regular file: {path}")


def _load_json(path: Path, label: str) -> tuple[Any, str]:
    _require_regular_file(path, label)
    raw = path.read_bytes()
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AnalysisError(f"{label} is malformed JSON: {exc}") from exc
    return value, hashlib.sha256(raw).hexdigest()


def _load_jsonl(path: Path, label: str) -> tuple[list[dict[str, Any]], str]:
    _require_regular_file(path, label)
    raw = path.read_bytes()
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            raise AnalysisError(f"{label}:{line_number} is blank")
        try:
            value = json.loads(
                line,
                object_pairs_hook=_reject_duplicate_keys,
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise AnalysisError(
                f"{label}:{line_number} is malformed JSON: {exc}"
            ) from exc
        records.append(_mapping(value, f"{label}:{line_number}"))
    return records, hashlib.sha256(raw).hexdigest()


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AnalysisError(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise AnalysisError(f"{label} must be an array")
    return value


def _exact_keys(value: dict[str, Any], keys: set[str], label: str) -> None:
    missing = keys - value.keys()
    unknown = value.keys() - keys
    if missing:
        raise AnalysisError(f"{label} is missing keys: {sorted(missing)}")
    if unknown:
        raise AnalysisError(f"{label} has unknown keys: {sorted(unknown)}")


def _integer(
    value: Any,
    label: str,
    *,
    minimum: int = 0,
    maximum: int = 1_000_000_000,
) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise AnalysisError(
            f"{label} must be an integer in [{minimum}, {maximum}]"
        )
    return value


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise AnalysisError(f"{label} must be a boolean")
    return value


def _string(value: Any, label: str, *, identifier: bool = False) -> str:
    if not isinstance(value, str) or not value:
        raise AnalysisError(f"{label} must be a non-empty string")
    if identifier and IDENTIFIER_RE.fullmatch(value) is None:
        raise AnalysisError(f"{label} is not a safe identifier")
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise AnalysisError(f"{label} must be a lowercase SHA-256 digest")
    return value


def _git_object(value: Any, label: str) -> str:
    if not isinstance(value, str) or GIT_OBJECT_RE.fullmatch(value) is None:
        raise AnalysisError(f"{label} must be a 40- or 64-digit Git object")
    return value


def _finite(
    value: Any,
    label: str,
    *,
    positive: bool = False,
    nullable: bool = False,
) -> float | None:
    if value is None and nullable:
        return None
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(float(value))
    ):
        raise AnalysisError(f"{label} must be a finite number")
    result = float(value)
    if positive and result <= 0.0:
        raise AnalysisError(f"{label} must be greater than zero")
    return result


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    if actual != expected:
        raise AnalysisError(
            f"{label} is {actual!r}; expected {expected!r}"
        )


def _validate_host_snapshot(value: Any, label: str) -> dict[str, Any]:
    snapshot = _mapping(value, label)
    _exact_keys(
        snapshot,
        {
            "checked_utc",
            "filesystem_path",
            "free_disk_bytes",
            "minimum_free_disk_bytes",
            "disk_valid",
            "power_mode",
            "power_probe",
            "thermal_policy",
            "thermal_probe",
            "valid",
        },
        label,
    )
    _string(snapshot["checked_utc"], f"{label}.checked_utc")
    _string(snapshot["filesystem_path"], f"{label}.filesystem_path")
    free_disk = _integer(
        snapshot["free_disk_bytes"],
        f"{label}.free_disk_bytes",
        maximum=(1 << 63) - 1,
    )
    _assert_equal(
        snapshot["minimum_free_disk_bytes"],
        MINIMUM_FREE_DISK_BYTES,
        f"{label}.minimum_free_disk_bytes",
    )
    disk_valid = _boolean(snapshot["disk_valid"], f"{label}.disk_valid")
    _assert_equal(
        disk_valid,
        free_disk >= MINIMUM_FREE_DISK_BYTES,
        f"{label}.disk_valid",
    )
    _assert_equal(snapshot["power_mode"], POWER_MODE, f"{label}.power_mode")
    _assert_equal(
        snapshot["thermal_policy"], THERMAL_POLICY, f"{label}.thermal_policy"
    )
    power = _mapping(snapshot["power_probe"], f"{label}.power_probe")
    _exact_keys(
        power,
        {"command", "returncode", "stdout", "stderr", "source", "valid"},
        f"{label}.power_probe",
    )
    _list(power["command"], f"{label}.power_probe.command")
    if power["returncode"] is not None:
        _integer(
            power["returncode"],
            f"{label}.power_probe.returncode",
            minimum=-1_000_000,
        )
    for field in ("stdout", "stderr"):
        if not isinstance(power[field], str):
            raise AnalysisError(f"{label}.power_probe.{field} is invalid")
    if power["source"] is not None and not isinstance(power["source"], str):
        raise AnalysisError(f"{label}.power_probe.source is invalid")
    power_valid = _boolean(power["valid"], f"{label}.power_probe.valid")
    _assert_equal(
        power_valid,
        power["returncode"] == 0 and power["source"] == POWER_MODE,
        f"{label}.power_probe.valid",
    )
    thermal = _mapping(snapshot["thermal_probe"], f"{label}.thermal_probe")
    _exact_keys(
        thermal,
        {
            "command",
            "module_cache_path",
            "returncode",
            "stdout",
            "stderr",
            "state",
            "valid",
        },
        f"{label}.thermal_probe",
    )
    _list(thermal["command"], f"{label}.thermal_probe.command")
    _string(
        thermal["module_cache_path"],
        f"{label}.thermal_probe.module_cache_path",
    )
    if thermal["returncode"] is not None:
        _integer(
            thermal["returncode"],
            f"{label}.thermal_probe.returncode",
            minimum=-1_000_000,
        )
    for field in ("stdout", "stderr"):
        if not isinstance(thermal[field], str):
            raise AnalysisError(f"{label}.thermal_probe.{field} is invalid")
    if thermal["state"] is not None:
        _integer(thermal["state"], f"{label}.thermal_probe.state")
    thermal_valid = _boolean(
        thermal["valid"], f"{label}.thermal_probe.valid"
    )
    _assert_equal(
        thermal_valid,
        thermal["returncode"] == 0 and thermal["state"] == 0,
        f"{label}.thermal_probe.valid",
    )
    valid = _boolean(snapshot["valid"], f"{label}.valid")
    _assert_equal(
        valid,
        disk_valid and power_valid and thermal_valid,
        f"{label}.valid",
    )
    return snapshot


def _host_block_valid(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return bool(
        before["valid"]
        and after["valid"]
        and before["power_probe"]["source"]
        == after["power_probe"]["source"]
        and before["thermal_probe"]["state"]
        == after["thermal_probe"]["state"]
    )


def _validate_idle_cooldown(value: Any, label: str) -> dict[str, Any]:
    record = _mapping(value, label)
    _exact_keys(
        record,
        {
            "required_seconds",
            "eligible",
            "performed",
            "elapsed_seconds",
            "before",
            "after",
            "valid",
        },
        label,
    )
    _assert_equal(
        _integer(
            record["required_seconds"],
            f"{label}.required_seconds",
            minimum=1,
        ),
        300,
        f"{label}.required_seconds",
    )
    before = _validate_host_snapshot(record["before"], f"{label}.before")
    after = _validate_host_snapshot(record["after"], f"{label}.after")
    eligible = _boolean(record["eligible"], f"{label}.eligible")
    performed = _boolean(record["performed"], f"{label}.performed")
    elapsed = _finite(
        record["elapsed_seconds"],
        f"{label}.elapsed_seconds",
        positive=True,
    )
    valid = _boolean(record["valid"], f"{label}.valid")
    expected_valid = bool(
        eligible
        and performed
        and elapsed is not None
        and elapsed >= 300.0
        and _host_block_valid(before, after)
    )
    _assert_equal(eligible, before["valid"], f"{label}.eligible")
    _assert_equal(valid, expected_valid, f"{label}.valid")
    if not valid:
        raise AnalysisError(f"{label} did not preserve frozen host controls")
    return record


def _artifact_record(
    value: Any,
    label: str,
    session_root: Path,
    *,
    nullable: bool,
) -> dict[str, str] | None:
    if value is None and nullable:
        return None
    record = _mapping(value, label)
    _exact_keys(record, ARTIFACT_RECORD_KEYS, label)
    raw_path = _string(record["path"], f"{label}.path")
    expected_sha = _sha256(record["sha256"], f"{label}.sha256")
    path = Path(raw_path)
    if not path.is_absolute():
        raise AnalysisError(f"{label}.path must be absolute")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AnalysisError(f"{label}.path cannot be resolved: {exc}") from exc
    try:
        resolved.relative_to(session_root)
    except ValueError as exc:
        raise AnalysisError(
            f"{label}.path escapes the session directory: {resolved}"
        ) from exc
    _require_regular_file(resolved, label)
    actual_sha = sha256_file(resolved)
    if actual_sha != expected_sha:
        raise AnalysisError(
            f"{label} hash mismatch: {actual_sha} != {expected_sha}"
        )
    return {"path": str(resolved), "sha256": expected_sha}


def _validate_scientific_trace(
    artifact: dict[str, str],
    label: str,
    *,
    expected_path: Path,
    expected_provenance: dict[str, Any],
) -> dict[str, str]:
    """Independently admit one trace artifact to the scientific evidence set."""
    actual_path = Path(artifact["path"])
    try:
        resolved_expected = expected_path.resolve(strict=True)
    except OSError as exc:
        raise AnalysisError(
            f"{label} expected path cannot be resolved: {exc}"
        ) from exc
    _assert_equal(
        actual_path,
        resolved_expected,
        f"{label}.path attempt binding",
    )
    try:
        with TraceReader(actual_path) as reader:
            header = reader.header
            if header.schema_version == HISTORICAL_TRACE_SCHEMA:
                raise AnalysisError(
                    f"{label} uses historical trace-v2, which is "
                    "inspection-only and cannot be admitted as new "
                    "scientific evidence; reproduce the committed v2 "
                    "analysis with revision "
                    f"{HISTORICAL_V2_ANALYZER_REVISION}"
                )
            _assert_equal(
                header.schema_version,
                TRACE_SCHEMA,
                f"{label} header.schema_version",
            )
            _assert_equal(
                header.trace_detail,
                "digest",
                f"{label} header.observer.trace_detail",
            )
            _assert_equal(
                header.provenance,
                expected_provenance,
                f"{label} header.metadata provenance",
            )
            observer = _mapping(
                header.canonical.get("observer"),
                f"{label} header.observer",
            )
            _assert_equal(
                observer.get("timing_eligible"),
                False,
                f"{label} header.observer.timing_eligible",
            )
            _assert_equal(
                observer.get("digest_algorithm"),
                TRACE_DIGEST_ALGORITHM,
                f"{label} header.observer.digest_algorithm",
            )
            for field in (
                "future_state_scope",
                "message_checksum_scope",
            ):
                scope = observer.get(field)
                if not isinstance(scope, str) or not scope.strip():
                    raise AnalysisError(
                        f"{label} header.observer.{field} must be a "
                        "non-empty digest-scope description"
                    )
    except AnalysisError:
        raise
    except (TraceFormatError, OSError, ValueError) as exc:
        raise AnalysisError(
            f"{label} trace-v3 digest contract is invalid: {exc}"
        ) from exc
    return {
        "schema_version": TRACE_SCHEMA,
        "trace_detail": "digest",
        "state_schema": TRACE_DIGEST_STATE_SCHEMA,
        "digest_algorithm": TRACE_DIGEST_ALGORITHM,
    }


def _validate_resource(record: dict[str, Any], label: str, terminal: str) -> None:
    _exact_keys(record, RESOURCE_KEYS, label)
    timed_out = _boolean(record["timed_out"], f"{label}.timed_out")
    for name in (
        "returncode",
        "exit_code",
        "signal",
        "peak_rss_raw",
    ):
        value = record[name]
        if value is not None:
            _integer(
                value,
                f"{label}.{name}",
                minimum=-1_000_000 if name == "returncode" else 0,
                maximum=10**15 if name == "peak_rss_raw" else 1_000_000_000,
            )
    for name in (
        "wall_seconds",
        "user_cpu_seconds",
        "system_cpu_seconds",
        "total_cpu_seconds",
        "peak_rss_mib",
    ):
        measured = _finite(record[name], f"{label}.{name}", nullable=True)
        if measured is not None and measured < 0.0:
            raise AnalysisError(f"{label}.{name} must not be negative")
    raw_unit = record["peak_rss_raw_unit"]
    if raw_unit is not None and not isinstance(raw_unit, str):
        raise AnalysisError(f"{label}.peak_rss_raw_unit is invalid")
    maximum_rss = _finite(
        record["maximum_peak_rss_mib"],
        f"{label}.maximum_peak_rss_mib",
        positive=True,
    )
    _assert_equal(
        maximum_rss,
        MAXIMUM_PEAK_RSS_MIB,
        f"{label}.maximum_peak_rss_mib",
    )
    within_limit = _boolean(
        record["peak_rss_within_limit"],
        f"{label}.peak_rss_within_limit",
    )
    observed_rss = record["peak_rss_mib"]
    _assert_equal(
        within_limit,
        observed_rss is not None
        and float(observed_rss) <= MAXIMUM_PEAK_RSS_MIB,
        f"{label}.peak_rss_within_limit",
    )

    if terminal == "completed":
        _assert_equal(record["returncode"], 0, f"{label}.returncode")
        _assert_equal(record["exit_code"], 0, f"{label}.exit_code")
        _assert_equal(record["signal"], None, f"{label}.signal")
        _assert_equal(timed_out, False, f"{label}.timed_out")
        for name in ("wall_seconds", "total_cpu_seconds", "peak_rss_mib"):
            _finite(record[name], f"{label}.{name}", positive=True)
        user = _finite(
            record["user_cpu_seconds"],
            f"{label}.user_cpu_seconds",
        )
        system = _finite(
            record["system_cpu_seconds"],
            f"{label}.system_cpu_seconds",
        )
        total = _finite(
            record["total_cpu_seconds"],
            f"{label}.total_cpu_seconds",
        )
        assert user is not None and system is not None and total is not None
        if not math.isclose(total, user + system, rel_tol=1e-12, abs_tol=1e-12):
            raise AnalysisError(
                f"{label}.total_cpu_seconds is not user + system"
            )


def _validate_provenance(
    record: dict[str, Any],
    run: dict[str, Any],
    label: str,
    analysis_sha256: str,
) -> None:
    _exact_keys(record, PROVENANCE_KEYS, label)
    field_map = {
        "run_id": "attempt_id",
        "implementation_id": "method",
        "phase": "phase",
        "mode": "mode",
        "pair_id": "pair_id",
        "repetition": "repetition",
        "order": "order",
        "attempt_kind": "attempt_kind",
    }
    for provenance_key, run_key in field_map.items():
        _assert_equal(
            record[provenance_key],
            run[run_key],
            f"{label}.{provenance_key}",
        )
    for name in (
        "algorithm_revision",
        "source_head_revision",
        "source_tree_hash",
    ):
        _string(record[name], f"{label}.{name}")
    for name in (
        "common_patch_sha256",
        "common_patch_full_index_diff_sha256",
        "binary_sha256",
        "build_manifest_sha256",
        "pixi_lock_sha256",
        "protocol_sha256",
        "experiment_manifest_sha256",
        "analysis_revision_sha256",
        "runner_sha256",
        "config_sha256",
        "map_sha256",
        "scenario_sha256",
    ):
        _sha256(record[name], f"{label}.{name}")
    _assert_equal(
        record["analysis_revision_sha256"],
        analysis_sha256,
        f"{label}.analysis_revision_sha256",
    )
    _assert_equal(
        _integer(record["omp_threads"], f"{label}.omp_threads", minimum=1),
        1,
        f"{label}.omp_threads",
    )
    _assert_equal(
        _finite(
            record["maximum_peak_rss_mib"],
            f"{label}.maximum_peak_rss_mib",
            positive=True,
        ),
        MAXIMUM_PEAK_RSS_MIB,
        f"{label}.maximum_peak_rss_mib",
    )
    _assert_equal(record["power_mode"], POWER_MODE, f"{label}.power_mode")
    _assert_equal(
        record["thermal_policy"], THERMAL_POLICY, f"{label}.thermal_policy"
    )
    _assert_equal(
        record["task_validity_predicate_id"],
        TASK_VALIDITY_PREDICATE_ID,
        f"{label}.task_validity_predicate_id",
    )


def _validate_run(
    run: dict[str, Any],
    label: str,
    session_root: Path,
    manifest: dict[str, Any],
    analysis_sha256: str,
) -> None:
    _exact_keys(run, RUN_KEYS, label)
    _assert_equal(run["schema_version"], RUN_SCHEMA, f"{label}.schema_version")
    _integer(run["sequence"], f"{label}.sequence", minimum=1)
    for field in ("attempt_id", "block_id", "pair_id", "environment"):
        _string(run[field], f"{label}.{field}", identifier=True)
    _integer(run["repetition"], f"{label}.repetition")
    if run["attempt_kind"] not in {"warmup", "measured"}:
        raise AnalysisError(f"{label}.attempt_kind is invalid")
    if run["order"] not in {"warmup", "AB", "BA"}:
        raise AnalysisError(f"{label}.order is invalid")
    _integer(run["order_position"], f"{label}.order_position", minimum=1, maximum=2)
    _assert_equal(run["phase"], manifest["phase"], f"{label}.phase")
    _assert_equal(run["mode"], manifest["mode"], f"{label}.mode")
    _integer(run["seed"], f"{label}.seed", minimum=1)
    _integer(run["robot_count"], f"{label}.robot_count", minimum=1)
    if run["method"] not in METHODS:
        raise AnalysisError(f"{label}.method is invalid")
    terminal = _string(
        run["terminal_category"],
        f"{label}.terminal_category",
        identifier=True,
    )
    if run["orchestration_error"] is not None and not isinstance(
        run["orchestration_error"], str
    ):
        raise AnalysisError(f"{label}.orchestration_error is invalid")
    if terminal == "completed" and run["orchestration_error"] is not None:
        raise AnalysisError(
            f"{label} completed but has an orchestration error"
        )
    task_valid = run["task_valid"]
    fidelity_metrics = run["fidelity_metrics"]
    if run["mode"] == "timing":
        _assert_equal(task_valid, None, f"{label}.task_valid")
        _assert_equal(fidelity_metrics, None, f"{label}.fidelity_metrics")
    elif terminal == "completed":
        task_valid = _boolean(task_valid, f"{label}.task_valid")
        fidelity_metrics = _mapping(
            fidelity_metrics, f"{label}.fidelity_metrics"
        )
        _exact_keys(
            fidelity_metrics,
            {
                "task_validity_predicate_id",
                "task_valid",
                "task_fields",
                "paper_metrics",
                "summary_parity_fields",
            },
            f"{label}.fidelity_metrics",
        )
        _assert_equal(
            fidelity_metrics["task_validity_predicate_id"],
            TASK_VALIDITY_PREDICATE_ID,
            f"{label}.fidelity_metrics.task_validity_predicate_id",
        )
        _assert_equal(
            fidelity_metrics["task_valid"],
            task_valid,
            f"{label}.fidelity_metrics.task_valid",
        )
        for field in ("task_fields", "paper_metrics", "summary_parity_fields"):
            _mapping(
                fidelity_metrics[field],
                f"{label}.fidelity_metrics.{field}",
            )
    _string(run["completed_utc"], f"{label}.completed_utc")
    resource = _mapping(run["resource"], f"{label}.resource")
    _validate_resource(resource, f"{label}.resource", terminal)
    provenance = _mapping(run["provenance"], f"{label}.provenance")
    _validate_provenance(
        provenance,
        run,
        f"{label}.provenance",
        analysis_sha256,
    )

    artifacts = _mapping(run["artifacts"], f"{label}.artifacts")
    _exact_keys(artifacts, ARTIFACT_KEYS, f"{label}.artifacts")
    normalized: dict[str, dict[str, str] | None] = {}
    for name in ("config", "stdout", "stderr"):
        normalized[name] = _artifact_record(
            artifacts[name],
            f"{label}.artifacts.{name}",
            session_root,
            nullable=False,
        )
    for name in ("trace", "summary"):
        normalized[name] = _artifact_record(
            artifacts[name],
            f"{label}.artifacts.{name}",
            session_root,
            nullable=True,
        )
    assert normalized["config"] is not None
    _assert_equal(
        normalized["config"]["sha256"],
        provenance["config_sha256"],
        f"{label}.config provenance",
    )
    if run["mode"] == "timing":
        if normalized["trace"] is not None or normalized["summary"] is not None:
            raise AnalysisError(f"{label} timing run contains observer output")
    elif terminal == "completed":
        if normalized["trace"] is None or normalized["summary"] is None:
            raise AnalysisError(
                f"{label} completed fidelity run lacks observer output"
            )

    attempt_root = session_root / "attempts" / run["attempt_id"]
    if run["mode"] == "fidelity" and terminal == "completed":
        assert normalized["trace"] is not None
        assert normalized["summary"] is not None
        trace_contract = _validate_scientific_trace(
            normalized["trace"],
            f"{label}.artifacts.trace",
            expected_path=attempt_root / "trace.jsonl",
            expected_provenance=provenance,
        )
        summary_value, _ = _load_json(
            Path(normalized["summary"]["path"]),
            f"{label} observer summary",
        )
        summary_record = _mapping(
            summary_value,
            f"{label} observer summary",
        )
        _assert_equal(
            summary_record.get("trace_detail"),
            trace_contract["trace_detail"],
            f"{label} observer summary.trace_detail",
        )

    result, _ = _load_json(attempt_root / "result.json", f"{label} result copy")
    _assert_equal(result, run, f"{label} result copy")
    metadata, _ = _load_json(
        attempt_root / "runner_metadata.json",
        f"{label} runner metadata",
    )
    _assert_equal(metadata, provenance, f"{label} runner metadata")
    observer_metadata = attempt_root / "observer_metadata.json"
    if run["mode"] == "fidelity":
        observed, _ = _load_json(
            observer_metadata,
            f"{label} observer metadata",
        )
        _assert_equal(observed, provenance, f"{label} observer metadata")
    elif observer_metadata.exists() or observer_metadata.is_symlink():
        raise AnalysisError(
            f"{label} timing attempt has observer metadata"
        )


def _validate_block(
    value: Any,
    label: str,
    *,
    warmup: bool,
) -> dict[str, Any]:
    block = _mapping(value, label)
    _exact_keys(block, WARMUP_BLOCK_KEYS if warmup else BLOCK_KEYS, label)
    for field in ("block_id", "pair_id", "environment"):
        _string(block[field], f"{label}.{field}", identifier=True)
    repetition = _integer(block["repetition"], f"{label}.repetition")
    order = block["order"]
    if warmup:
        _assert_equal(repetition, 0, f"{label}.repetition")
        _assert_equal(order, "warmup", f"{label}.order")
        _sha256(block["map_sha256"], f"{label}.map_sha256")
    else:
        if repetition < 1:
            raise AnalysisError(f"{label}.repetition must be positive")
        if order not in {"AB", "BA"}:
            raise AnalysisError(f"{label}.order is invalid")
    _integer(block["seed"], f"{label}.seed", minimum=1)
    _integer(block["robot_count"], f"{label}.robot_count", minimum=1)
    return block


def _validate_capacity_evaluation_record(
    value: Any,
    label: str,
    *,
    phase: str,
    mode: str,
) -> dict[str, Any] | None:
    if value is None:
        return None
    record = _mapping(value, label)
    _exact_keys(record, CAPACITY_EVALUATION_KEYS, label)
    if phase not in {"validation", "audit"}:
        raise AnalysisError(f"{label} is permitted only for validation/audit")
    _string(record["environment"], f"{label}.environment", identifier=True)
    _assert_equal(
        record["role"],
        f"{mode}-grid",
        f"{label}.role",
    )
    anchor = _integer(
        record["reference_anchor_n"],
        f"{label}.reference_anchor_n",
        minimum=1,
        maximum=100_000,
    )
    candidate = _integer(
        record["candidate_n_star"],
        f"{label}.candidate_n_star",
        minimum=1,
        maximum=100_000,
    )
    successor = (
        None
        if record["next_grid_point"] is None
        else _integer(
            record["next_grid_point"],
            f"{label}.next_grid_point",
            minimum=1,
            maximum=100_000,
        )
    )
    grid = [
        _integer(
            item,
            f"{label}.grid[{index}]",
            minimum=1,
            maximum=100_000,
        )
        for index, item in enumerate(_list(record["grid"], f"{label}.grid"))
    ]
    if grid != sorted(set(grid)) or len(grid) < 2:
        raise AnalysisError(
            f"{label}.grid must contain at least two increasing counts"
        )
    if (
        anchor != grid[0]
        or candidate not in grid
        or (successor is not None and successor not in grid)
    ):
        raise AnalysisError(f"{label} counts must be members of its grid")
    candidate_index = grid.index(candidate)
    expected_successor = (
        grid[candidate_index + 1]
        if candidate_index + 1 < len(grid)
        else None
    )
    if expected_successor != successor:
        raise AnalysisError(
            f"{label}.next_grid_point must immediately follow candidate_n_star"
        )
    return record


def _validate_manifest(value: Any, label: str) -> dict[str, Any]:
    manifest = _mapping(value, label)
    _exact_keys(manifest, MANIFEST_KEYS, label)
    _assert_equal(
        manifest["schema_version"], PLAN_SCHEMA, f"{label}.schema_version"
    )
    for field in (
        "created_utc",
        "plan_path",
        "order_policy",
        "count_order_policy",
    ):
        _string(manifest[field], f"{label}.{field}")
    _sha256(manifest["plan_sha256"], f"{label}.plan_sha256")
    phase = _string(manifest["phase"], f"{label}.phase", identifier=True)
    if phase not in PHASE_SEEDS:
        raise AnalysisError(f"{label}.phase is invalid")
    if manifest["mode"] not in MODES:
        raise AnalysisError(f"{label}.mode is invalid")
    _assert_equal(
        _boolean(manifest["serial_execution"], f"{label}.serial_execution"),
        True,
        f"{label}.serial_execution",
    )
    _integer(
        manifest["order_seed"],
        f"{label}.order_seed",
        maximum=(1 << 63) - 1,
    )
    _assert_equal(
        _integer(
            manifest["fixed_work_timesteps"],
            f"{label}.fixed_work_timesteps",
            minimum=1,
            maximum=1_000_000,
        ),
        100,
        f"{label}.fixed_work_timesteps",
    )
    _assert_equal(
        _finite(
            manifest["timestep_seconds"],
            f"{label}.timestep_seconds",
            positive=True,
        ),
        0.1,
        f"{label}.timestep_seconds",
    )
    _assert_equal(
        _integer(
            manifest["idle_cooldown_seconds"],
            f"{label}.idle_cooldown_seconds",
            minimum=1,
        ),
        300,
        f"{label}.idle_cooldown_seconds",
    )
    _assert_equal(
        manifest["task_validity_predicate_id"],
        TASK_VALIDITY_PREDICATE_ID,
        f"{label}.task_validity_predicate_id",
    )
    maximum_rss = _finite(
        manifest["maximum_peak_rss_mib"],
        f"{label}.maximum_peak_rss_mib",
        positive=True,
    )
    _assert_equal(
        maximum_rss,
        MAXIMUM_PEAK_RSS_MIB,
        f"{label}.maximum_peak_rss_mib",
    )
    _assert_equal(
        _integer(
            manifest["minimum_free_disk_bytes"],
            f"{label}.minimum_free_disk_bytes",
            minimum=1,
            maximum=(1 << 63) - 1,
        ),
        MINIMUM_FREE_DISK_BYTES,
        f"{label}.minimum_free_disk_bytes",
    )
    _assert_equal(
        manifest["power_mode"], POWER_MODE, f"{label}.power_mode"
    )
    _assert_equal(
        manifest["thermal_policy"],
        THERMAL_POLICY,
        f"{label}.thermal_policy",
    )
    preflight_host = _validate_host_snapshot(
        manifest["preflight_host_conditions"],
        f"{label}.preflight_host_conditions",
    )
    if not preflight_host["valid"]:
        raise AnalysisError(f"{label}.preflight_host_conditions is invalid")
    cooldown = _validate_idle_cooldown(
        manifest["idle_cooldown"],
        f"{label}.idle_cooldown",
    )
    _assert_equal(
        cooldown["before"],
        preflight_host,
        f"{label}.idle_cooldown.before",
    )
    repetitions = _integer(
        manifest["repetitions"],
        f"{label}.repetitions",
        minimum=1,
        maximum=100,
    )
    _assert_equal(
        repetitions,
        3 if manifest["mode"] == "timing" else 1,
        f"{label}.repetitions",
    )
    warmup_count = _integer(
        manifest["warmups_per_binary_map"],
        f"{label}.warmups_per_binary_map",
    )
    _assert_equal(warmup_count, 1, f"{label}.warmups_per_binary_map")
    warmups = [
        _validate_block(item, f"{label}.warmup_blocks[{index}]", warmup=True)
        for index, item in enumerate(
            _list(manifest["warmup_blocks"], f"{label}.warmup_blocks")
        )
    ]
    blocks = [
        _validate_block(item, f"{label}.blocks[{index}]", warmup=False)
        for index, item in enumerate(
            _list(manifest["blocks"], f"{label}.blocks")
        )
    ]
    if not blocks:
        raise AnalysisError(f"{label}.blocks must not be empty")
    if manifest["mode"] == "timing" and not warmups:
        raise AnalysisError(f"{label} timing plan has no warm-up blocks")
    if manifest["mode"] == "fidelity" and warmups:
        raise AnalysisError(f"{label} fidelity plan has warm-up blocks")
    block_ids = [item["block_id"] for item in warmups + blocks]
    if len(block_ids) != len(set(block_ids)):
        raise AnalysisError(f"{label} has duplicate block IDs")
    for block in blocks:
        if block["repetition"] > repetitions:
            raise AnalysisError(
                f"{label} block repetition exceeds the frozen count"
            )
    capacity = manifest["capacity_search"]
    if capacity is not None:
        if manifest["mode"] != "timing" or phase != "calibration":
            raise AnalysisError(
                f"{label}.capacity_search is calibration-timing only"
            )
        capacity = _mapping(capacity, f"{label}.capacity_search")
        _exact_keys(
            capacity,
            {"environment", "reference_anchor_n", "grid", "max_points"},
            f"{label}.capacity_search",
        )
        _string(
            capacity["environment"],
            f"{label}.capacity_search.environment",
            identifier=True,
        )
        anchor = _integer(
            capacity["reference_anchor_n"],
            f"{label}.capacity_search.reference_anchor_n",
            minimum=1,
        )
        grid = [
            _integer(
                item,
                f"{label}.capacity_search.grid[{index}]",
                minimum=1,
            )
            for index, item in enumerate(
                _list(capacity["grid"], f"{label}.capacity_search.grid")
            )
        ]
        if not grid or grid != sorted(set(grid)) or grid[0] != anchor:
            raise AnalysisError(
                f"{label}.capacity_search.grid is not a strict grid "
                "beginning at the anchor"
            )
        max_points = _integer(
            capacity["max_points"],
            f"{label}.capacity_search.max_points",
            minimum=1,
        )
        if len(grid) > max_points:
            raise AnalysisError(f"{label}.capacity_search exceeds max_points")
    capacity_evaluation = _validate_capacity_evaluation_record(
        manifest["capacity_evaluation"],
        f"{label}.capacity_evaluation",
        phase=phase,
        mode=manifest["mode"],
    )
    if phase in {"validation", "audit"} and capacity_evaluation is None:
        raise AnalysisError(
            f"{label}.capacity_evaluation is required for {phase}"
        )
    if capacity is not None and capacity_evaluation is not None:
        raise AnalysisError(
            f"{label} cannot contain capacity_search and capacity_evaluation"
        )
    return manifest


def _validate_pair_shape(pair: dict[str, Any], label: str) -> None:
    _exact_keys(pair, PAIR_KEYS, label)
    _assert_equal(pair["schema_version"], PAIR_SCHEMA, f"{label}.schema_version")
    for field in ("block_id", "pair_id", "environment"):
        _string(pair[field], f"{label}.{field}", identifier=True)
    _integer(pair["repetition"], f"{label}.repetition", minimum=1)
    if pair["order"] not in {"AB", "BA"}:
        raise AnalysisError(f"{label}.order is invalid")
    _string(pair["phase"], f"{label}.phase", identifier=True)
    if pair["mode"] not in MODES:
        raise AnalysisError(f"{label}.mode is invalid")
    _integer(pair["seed"], f"{label}.seed", minimum=1)
    _integer(pair["robot_count"], f"{label}.robot_count", minimum=1)
    _boolean(pair["config_sha256_equal"], f"{label}.config_sha256_equal")
    _boolean(pair["pair_valid"], f"{label}.pair_valid")
    attempt_ids = _mapping(pair["attempt_ids"], f"{label}.attempt_ids")
    terminals = _mapping(
        pair["terminal_categories"], f"{label}.terminal_categories"
    )
    _exact_keys(attempt_ids, set(METHODS), f"{label}.attempt_ids")
    _exact_keys(terminals, set(METHODS), f"{label}.terminal_categories")
    for method in METHODS:
        _string(
            attempt_ids[method],
            f"{label}.attempt_ids.{method}",
            identifier=True,
        )
        _string(
            terminals[method],
            f"{label}.terminal_categories.{method}",
            identifier=True,
        )
    semantic = pair["semantic_trace_equal"]
    if semantic is not None and not isinstance(semantic, bool):
        raise AnalysisError(f"{label}.semantic_trace_equal is invalid")
    frames = pair["frames_compared"]
    if frames is not None:
        _integer(frames, f"{label}.frames_compared")
    if pair["mismatch"] is not None and not isinstance(pair["mismatch"], dict):
        raise AnalysisError(f"{label}.mismatch is invalid")
    for field in (
        "paper_metrics_equal",
        "summary_parity_fields_equal",
        "task_valid",
    ):
        if pair[field] is not None and not isinstance(pair[field], bool):
            raise AnalysisError(f"{label}.{field} is invalid")
    if pair["summary_mismatch"] is not None and not isinstance(
        pair["summary_mismatch"], dict
    ):
        raise AnalysisError(f"{label}.summary_mismatch is invalid")
    _assert_equal(
        pair["task_validity_predicate_id"],
        TASK_VALIDITY_PREDICATE_ID,
        f"{label}.task_validity_predicate_id",
    )
    before = _validate_host_snapshot(
        pair["host_conditions_before"],
        f"{label}.host_conditions_before",
    )
    after = _validate_host_snapshot(
        pair["host_conditions_after"],
        f"{label}.host_conditions_after",
    )
    expected_host_valid = _host_block_valid(before, after)
    _assert_equal(
        _boolean(pair["host_block_valid"], f"{label}.host_block_valid"),
        expected_host_valid,
        f"{label}.host_block_valid",
    )
    expected_terminal = (
        "completed"
        if expected_host_valid
        else "host-power-thermal-contamination"
    )
    _assert_equal(
        pair["block_terminal_category"],
        expected_terminal,
        f"{label}.block_terminal_category",
    )


def _expected_method(order: str, position: int) -> str:
    if order == "warmup":
        return METHODS[position - 1]
    ordered = METHODS if order == "AB" else tuple(reversed(METHODS))
    return ordered[position - 1]


def _validate_capacity(
    value: Any,
    manifest_capacity: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    state = _mapping(value, label)
    _exact_keys(state, CAPACITY_KEYS, label)
    _assert_equal(
        state["schema_version"], CAPACITY_SCHEMA, f"{label}.schema_version"
    )
    _assert_equal(
        state["environment"],
        manifest_capacity["environment"],
        f"{label}.environment",
    )
    _assert_equal(state["grid"], manifest_capacity["grid"], f"{label}.grid")
    _assert_equal(
        state["max_points"],
        manifest_capacity["max_points"],
        f"{label}.max_points",
    )
    grid = manifest_capacity["grid"]
    _assert_equal(state["status"], "provisional-compute-only", f"{label}.status")
    _assert_equal(state["validated"], False, f"{label}.validated")
    _assert_equal(state["largest_passing_n"], None, f"{label}.largest_passing_n")
    _assert_equal(state["first_failing_n"], None, f"{label}.first_failing_n")
    expected_pending = [
        "task_validity",
        "semantic_fidelity",
        "memory_hypothesis",
    ]
    _assert_equal(state["pending_gates"], expected_pending, f"{label}.pending_gates")
    _boolean(state["complete"], f"{label}.complete")
    for field in (
        "provisional_largest_compute_passing_n",
        "first_provisional_compute_failing_n",
    ):
        if state[field] is not None:
            count = _integer(state[field], f"{label}.{field}", minimum=1)
            if count not in grid:
                raise AnalysisError(f"{label}.{field} is outside the grid")
    if state["stop_reason"] is not None:
        _string(state["stop_reason"], f"{label}.stop_reason", identifier=True)
    unexecuted = [
        _integer(item, f"{label}.unexecuted_grid_points[{index}]", minimum=1)
        for index, item in enumerate(
            _list(
                state["unexecuted_grid_points"],
                f"{label}.unexecuted_grid_points",
            )
        )
    ]
    if unexecuted != sorted(set(unexecuted)) or any(
        item not in grid for item in unexecuted
    ):
        raise AnalysisError(f"{label}.unexecuted_grid_points is invalid")
    evaluations = _list(state["evaluations"], f"{label}.evaluations")
    evaluated_counts: list[int] = []
    passing: list[int] = []
    failing: list[int] = []
    for index, raw in enumerate(evaluations):
        item_label = f"{label}.evaluations[{index}]"
        item = _mapping(raw, item_label)
        _exact_keys(
            item,
            {
                "robot_count",
                "complete",
                "reference",
                "optimized",
                "provisional_compute_budget_pass",
                "validated_capacity_pass",
                "pending_gates",
            },
            item_label,
        )
        count = _integer(item["robot_count"], f"{item_label}.robot_count", minimum=1)
        if count not in grid:
            raise AnalysisError(f"{item_label}.robot_count is outside grid")
        complete = _boolean(item["complete"], f"{item_label}.complete")
        passed = _boolean(
            item["provisional_compute_budget_pass"],
            f"{item_label}.provisional_compute_budget_pass",
        )
        _mapping(item["reference"], f"{item_label}.reference")
        _mapping(item["optimized"], f"{item_label}.optimized")
        _assert_equal(
            item["validated_capacity_pass"],
            False,
            f"{item_label}.validated_capacity_pass",
        )
        _assert_equal(item["pending_gates"], expected_pending, f"{item_label}.pending_gates")
        if passed and not complete:
            raise AnalysisError(f"{item_label} cannot pass while incomplete")
        evaluated_counts.append(count)
        (passing if passed else failing).append(count)
    if evaluated_counts != grid[: len(evaluated_counts)]:
        raise AnalysisError(f"{label}.evaluations is not a grid prefix")
    expected_largest = max(passing) if passing else None
    _assert_equal(
        state["provisional_largest_compute_passing_n"],
        expected_largest,
        f"{label}.largest_passing_n",
    )
    if failing:
        _assert_equal(
            state["first_provisional_compute_failing_n"],
            failing[0],
            f"{label}.first_provisional_compute_failing_n",
        )
    budget = state["budget"]
    if budget is not None:
        budget = _mapping(budget, f"{label}.budget")
        _exact_keys(
            budget,
            {
                "status",
                "source",
                "reference_anchor_n",
                "cpu_seconds",
                "wall_seconds",
                "frozen_after_complete_anchor",
                "reference_seed_units",
            },
            f"{label}.budget",
        )
        _string(budget["source"], f"{label}.budget.source")
        _assert_equal(
            budget["status"],
            "provisional-compute-budget",
            f"{label}.budget.status",
        )
        _assert_equal(
            budget["reference_anchor_n"],
            manifest_capacity["reference_anchor_n"],
            f"{label}.budget.reference_anchor_n",
        )
        _finite(
            budget["cpu_seconds"],
            f"{label}.budget.cpu_seconds",
            positive=True,
        )
        _finite(
            budget["wall_seconds"],
            f"{label}.budget.wall_seconds",
            positive=True,
        )
        _assert_equal(
            _boolean(
                budget["frozen_after_complete_anchor"],
                f"{label}.budget.frozen_after_complete_anchor",
            ),
            True,
            f"{label}.budget.frozen_after_complete_anchor",
        )
        _list(
            budget["reference_seed_units"],
            f"{label}.budget.reference_seed_units",
        )
    return state


def _condition_identity(run: dict[str, Any]) -> tuple[Any, ...]:
    provenance = run["provenance"]
    return (
        run["environment"],
        provenance["map_sha256"],
        provenance["scenario_sha256"],
        run["seed"],
        run["robot_count"],
    )


def _plan_artifact(
    value: Any,
    base: Path,
    label: str,
) -> dict[str, str]:
    record = _mapping(value, label)
    _exact_keys(record, ARTIFACT_RECORD_KEYS, label)
    raw = Path(_string(record["path"], f"{label}.path"))
    path = raw if raw.is_absolute() else base / raw
    if path.is_symlink():
        raise AnalysisError(f"{label} must not be a symbolic link")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AnalysisError(f"{label} cannot be resolved: {exc}") from exc
    _require_regular_file(resolved, label)
    expected = _sha256(record["sha256"], f"{label}.sha256")
    actual = sha256_file(resolved)
    _assert_equal(actual, expected, f"{label}.sha256")
    return {"path": str(resolved), "sha256": expected}


def _validate_capacity_decision_binding(
    artifact: dict[str, str],
    evaluation: dict[str, Any],
    *,
    plan_artifacts: dict[str, dict[str, str]],
    phase: str,
    timeout_seconds: float,
    label: str,
) -> None:
    path = Path(artifact["path"])
    decision, _ = _load_json(path, label)
    decision = _mapping(decision, label)
    if path.read_bytes() != canonical_json_bytes(decision):
        raise AnalysisError(f"{label} is not canonical JSON")
    _exact_keys(
        decision,
        {
            "schema_version",
            "status",
            "selection_rule_id",
            "provenance",
            "seeds",
            "environments",
        },
        label,
    )
    _assert_equal(
        decision["schema_version"],
        "dancers-capacity-decision-v1",
        f"{label}.schema_version",
    )
    _assert_equal(
        decision["status"],
        "frozen-before-validation",
        f"{label}.status",
    )
    _assert_equal(
        decision["selection_rule_id"],
        "highest-all-gate-calibration-passing-n-v1",
        f"{label}.selection_rule_id",
    )
    seeds = _mapping(decision["seeds"], f"{label}.seeds")
    _exact_keys(seeds, {"validation", "audit"}, f"{label}.seeds")
    for seed_phase in ("validation", "audit"):
        _assert_equal(
            seeds[seed_phase],
            sorted(PHASE_SEEDS[seed_phase]),
            f"{label}.seeds.{seed_phase}",
        )

    provenance = _mapping(decision["provenance"], f"{label}.provenance")
    _exact_keys(
        provenance,
        {
            "calibration_analysis",
            "analyzer",
            "experiment_manifest",
            "environment_manifest",
            "calibration_sessions",
            "timeout_derivation",
        },
        f"{label}.provenance",
    )
    verified_provenance: dict[str, dict[str, str]] = {}
    for name in (
        "calibration_analysis",
        "analyzer",
        "experiment_manifest",
        "environment_manifest",
    ):
        raw_record = _mapping(
            provenance[name], f"{label}.provenance.{name}"
        )
        raw_path = Path(
            _string(
                raw_record.get("path"),
                f"{label}.provenance.{name}.path",
            )
        )
        if not raw_path.is_absolute():
            raise AnalysisError(
                f"{label}.provenance.{name}.path must be absolute"
            )
        verified_provenance[name] = _plan_artifact(
            raw_record,
            path.parent,
            f"{label}.provenance.{name}",
        )
    for decision_name, plan_name in (
        ("analyzer", "analysis_revision"),
        ("experiment_manifest", "experiment_manifest"),
        ("environment_manifest", "environment_manifest"),
    ):
        _assert_equal(
            verified_provenance[decision_name]["sha256"],
            plan_artifacts[plan_name]["sha256"],
            f"{label}.provenance.{decision_name} plan binding",
        )

    session_hash_keys = {
        "session_json_sha256",
        "run_manifest_json_sha256",
        "frozen_plan_sha256",
        "runs_jsonl_sha256",
        "pairs_jsonl_sha256",
        "capacity_json_sha256",
    }
    calibration_sessions = _list(
        provenance["calibration_sessions"],
        f"{label}.provenance.calibration_sessions",
    )
    if not calibration_sessions:
        raise AnalysisError(f"{label} has no calibration-session provenance")
    seen_session_ids: set[str] = set()
    for index, value in enumerate(calibration_sessions):
        session_label = f"{label}.provenance.calibration_sessions[{index}]"
        record = _mapping(value, session_label)
        _exact_keys(
            record,
            {"session_id", "path", "mode", "hashes"},
            session_label,
        )
        session_id = _string(
            record["session_id"], f"{session_label}.session_id", identifier=True
        )
        if session_id in seen_session_ids:
            raise AnalysisError(
                f"{label} has duplicate calibration session IDs"
            )
        seen_session_ids.add(session_id)
        if record["mode"] not in MODES:
            raise AnalysisError(f"{session_label}.mode is invalid")
        session_path = Path(
            _string(record["path"], f"{session_label}.path")
        )
        if not session_path.is_absolute():
            raise AnalysisError(f"{session_label}.path must be absolute")
        hashes = _mapping(record["hashes"], f"{session_label}.hashes")
        _exact_keys(hashes, session_hash_keys, f"{session_label}.hashes")
        for hash_name, value in hashes.items():
            if hash_name == "capacity_json_sha256" and value is None:
                continue
            _sha256(value, f"{session_label}.hashes.{hash_name}")

    timeout_basis = _mapping(
        provenance["timeout_derivation"],
        f"{label}.provenance.timeout_derivation",
    )
    _exact_keys(
        timeout_basis,
        set(CONFIRMATORY_ENVIRONMENTS),
        f"{label}.provenance.timeout_derivation",
    )

    environments = _list(
        decision["environments"], f"{label}.environments"
    )
    if len(environments) != len(CONFIRMATORY_ENVIRONMENTS):
        raise AnalysisError(
            f"{label}.environments must contain all registered families"
        )
    by_environment: dict[str, dict[str, Any]] = {}
    for index, (expected_environment, value) in enumerate(
        zip(CONFIRMATORY_ENVIRONMENTS, environments)
    ):
        record_label = f"{label}.environments[{index}]"
        record = _mapping(value, record_label)
        _exact_keys(
            record,
            {
                "environment",
                "reference_anchor_n",
                "grid",
                "candidate_n_star",
                "next_grid_point",
                "validation_timing_counts",
                "validation_fidelity_counts",
                "validation_timeout_seconds",
            },
            record_label,
        )
        _assert_equal(
            record["environment"],
            expected_environment,
            f"{record_label}.environment",
        )
        grid = [
            _integer(item, f"{record_label}.grid[{grid_index}]", minimum=1)
            for grid_index, item in enumerate(
                _list(record["grid"], f"{record_label}.grid")
            )
        ]
        if not grid or grid != sorted(set(grid)):
            raise AnalysisError(
                f"{record_label}.grid must be strictly increasing"
            )
        anchor = _integer(
            record["reference_anchor_n"],
            f"{record_label}.reference_anchor_n",
            minimum=1,
        )
        candidate = _integer(
            record["candidate_n_star"],
            f"{record_label}.candidate_n_star",
            minimum=1,
        )
        if anchor != grid[0] or candidate not in grid:
            raise AnalysisError(f"{record_label} has invalid N0/N*")
        candidate_index = grid.index(candidate)
        expected_next = (
            grid[candidate_index + 1]
            if candidate_index + 1 < len(grid)
            else None
        )
        _assert_equal(
            record["next_grid_point"],
            expected_next,
            f"{record_label}.next_grid_point",
        )
        for count_field in (
            "validation_timing_counts",
            "validation_fidelity_counts",
        ):
            _assert_equal(
                record[count_field],
                grid,
                f"{record_label}.{count_field}",
            )
        validation_timeout = _finite(
            record["validation_timeout_seconds"],
            f"{record_label}.validation_timeout_seconds",
            positive=True,
        )
        assert validation_timeout is not None
        if validation_timeout > 1_800.0:
            raise AnalysisError(
                f"{record_label}.validation_timeout_seconds exceeds 1800"
            )
        basis = _mapping(
            timeout_basis[expected_environment],
            f"{label}.provenance.timeout_derivation.{expected_environment}",
        )
        _exact_keys(
            basis,
            {
                "calibration_plan_timeout_seconds",
                "maximum_completed_measured_wall_seconds",
                "three_times_wall_ceiled_to_ten_seconds",
            },
            f"{label}.provenance.timeout_derivation.{expected_environment}",
        )
        plan_timeout = _finite(
            basis["calibration_plan_timeout_seconds"],
            f"{record_label}.calibration_plan_timeout_seconds",
            positive=True,
        )
        maximum_wall = _finite(
            basis["maximum_completed_measured_wall_seconds"],
            f"{record_label}.maximum_completed_measured_wall_seconds",
            positive=True,
        )
        assert plan_timeout is not None and maximum_wall is not None
        ceiled = float(math.ceil(3.0 * maximum_wall / 10.0) * 10)
        _assert_equal(
            basis["three_times_wall_ceiled_to_ten_seconds"],
            ceiled,
            f"{record_label}.three_times_wall_ceiled_to_ten_seconds",
        )
        _assert_equal(
            validation_timeout,
            max(plan_timeout, ceiled),
            f"{record_label}.validation_timeout_seconds derivation",
        )
        by_environment[expected_environment] = record

    record = by_environment[evaluation["environment"]]
    for field in (
        "reference_anchor_n",
        "grid",
        "candidate_n_star",
        "next_grid_point",
    ):
        _assert_equal(
            record[field],
            evaluation[field],
            f"{label}.{evaluation['environment']}.{field}",
        )
    role_counts = (
        "validation_timing_counts"
        if evaluation["role"] == "timing-grid"
        else "validation_fidelity_counts"
    )
    _assert_equal(
        record[role_counts],
        evaluation["grid"],
        f"{label}.{evaluation['environment']}.{role_counts}",
    )
    _assert_equal(
        float(record["validation_timeout_seconds"]),
        timeout_seconds,
        f"{label}.{evaluation['environment']}.validation_timeout_seconds",
    )


def _validate_frozen_plan(
    manifest: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    raw_path = Path(manifest["plan_path"])
    if not raw_path.is_absolute():
        raise AnalysisError(f"{label}.plan_path must be absolute")
    plan, plan_sha = _load_json(raw_path, f"{label} frozen plan")
    plan = _mapping(plan, f"{label} frozen plan")
    missing = FROZEN_PLAN_REQUIRED_KEYS - plan.keys()
    unknown = plan.keys() - FROZEN_PLAN_REQUIRED_KEYS - FROZEN_PLAN_OPTIONAL_KEYS
    if missing or unknown:
        raise AnalysisError(
            f"{label} frozen plan schema mismatch: "
            f"missing={sorted(missing)} unknown={sorted(unknown)}"
        )
    _assert_equal(
        plan_sha, manifest["plan_sha256"], f"{label}.plan_sha256"
    )
    _assert_equal(
        plan["schema_version"], PLAN_SCHEMA, f"{label} plan schema"
    )
    for field in (
        "phase",
        "mode",
        "fixed_work_timesteps",
        "timestep_seconds",
        "idle_cooldown_seconds",
        "repetitions",
        "warmups_per_binary_map",
        "order_seed",
        "minimum_free_disk_bytes",
        "maximum_peak_rss_mib",
        "power_mode",
        "thermal_policy",
        "task_validity_predicate_id",
    ):
        _assert_equal(
            plan[field], manifest[field], f"{label} frozen plan.{field}"
        )
    _assert_equal(
        _integer(plan["omp_threads"], f"{label} frozen plan.omp_threads"),
        1,
        f"{label} frozen plan.omp_threads",
    )
    timeout = _finite(
        plan["timeout_seconds"],
        f"{label} frozen plan.timeout_seconds",
        positive=True,
    )
    assert timeout is not None
    if timeout > 86_400.0:
        raise AnalysisError(
            f"{label} frozen plan.timeout_seconds exceeds 86400"
        )
    if "termination_grace_seconds" in plan:
        grace = _finite(
            plan["termination_grace_seconds"],
            f"{label} frozen plan.termination_grace_seconds",
            positive=True,
        )
        assert grace is not None
        if grace > 10.0:
            raise AnalysisError(
                f"{label} frozen plan.termination_grace_seconds exceeds 10"
            )
    trace_detail = plan.get("trace_detail", "digest")
    if trace_detail != "digest":
        raise AnalysisError(
            f"{label} frozen plan.trace_detail must be digest for "
            "registered scientific evidence; full and positions traces "
            "are diagnostic-only"
        )
    _sha256(plan["runner_sha256"], f"{label} frozen plan.runner_sha256")
    base = raw_path.parent
    run_cwd_raw = Path(
        _string(plan["run_cwd"], f"{label} frozen plan.run_cwd")
    )
    run_cwd = run_cwd_raw if run_cwd_raw.is_absolute() else base / run_cwd_raw
    if run_cwd.is_symlink() or not run_cwd.resolve().is_dir():
        raise AnalysisError(f"{label} frozen plan.run_cwd is not a real directory")
    resolved_run_cwd = run_cwd.resolve()

    raw_artifacts = _mapping(
        plan["artifacts"], f"{label} frozen plan.artifacts"
    )
    capacity_evaluation = _validate_capacity_evaluation_record(
        plan.get("capacity_evaluation"),
        f"{label} frozen plan.capacity_evaluation",
        phase=manifest["phase"],
        mode=manifest["mode"],
    )
    if (
        manifest["phase"] in {"validation", "audit"}
        and capacity_evaluation is None
    ):
        raise AnalysisError(
            f"{label} frozen plan.capacity_evaluation is required for "
            f"{manifest['phase']}"
        )
    expected_artifacts = set(PLAN_ARTIFACT_KEYS)
    if capacity_evaluation is not None:
        expected_artifacts.add("capacity_decision")
    _exact_keys(
        raw_artifacts,
        expected_artifacts,
        f"{label} frozen plan.artifacts",
    )
    artifacts = {
        name: _plan_artifact(
            raw_artifacts[name],
            base,
            f"{label} frozen plan.artifacts.{name}",
        )
        for name in sorted(raw_artifacts)
    }

    raw_methods = _mapping(
        plan["methods"], f"{label} frozen plan.methods"
    )
    _exact_keys(raw_methods, set(METHODS), f"{label} frozen plan.methods")
    methods: dict[str, dict[str, Any]] = {}
    for method in METHODS:
        method_label = f"{label} frozen plan.methods.{method}"
        record = _mapping(raw_methods[method], method_label)
        _exact_keys(
            record,
            {"source_dir", "base_revision", "head_revision", "tree_hash", "binary"},
            method_label,
        )
        source_raw = Path(
            _string(record["source_dir"], f"{method_label}.source_dir")
        )
        source = source_raw if source_raw.is_absolute() else base / source_raw
        if source.is_symlink() or not source.resolve().is_dir():
            raise AnalysisError(f"{method_label}.source_dir is not a real directory")
        methods[method] = {
            "source_dir": str(source.resolve()),
            "base_revision": _git_object(
                record["base_revision"], f"{method_label}.base_revision"
            ),
            "head_revision": _git_object(
                record["head_revision"], f"{method_label}.head_revision"
            ),
            "tree_hash": _git_object(
                record["tree_hash"], f"{method_label}.tree_hash"
            ),
            "binary": _plan_artifact(
                record["binary"], base, f"{method_label}.binary"
            ),
        }
    _assert_equal(
        methods["reference"]["base_revision"],
        REFERENCE_PIN,
        f"{label} frozen plan.methods.reference.base_revision",
    )
    _assert_equal(
        methods["optimized"]["base_revision"],
        OPTIMIZED_PIN,
        f"{label} frozen plan.methods.optimized.base_revision",
    )
    if (
        methods["reference"]["source_dir"]
        == methods["optimized"]["source_dir"]
    ):
        raise AnalysisError(
            f"{label} frozen plan method source directories are identical"
        )
    if (
        methods["reference"]["binary"]["path"]
        == methods["optimized"]["binary"]["path"]
    ):
        raise AnalysisError(
            f"{label} frozen plan method binaries are identical"
        )

    build_manifest, _ = _load_json(
        Path(artifacts["build_manifest"]["path"]),
        f"{label} frozen build manifest",
    )
    build_manifest = _mapping(
        build_manifest, f"{label} frozen build manifest"
    )
    environment_manifest, _ = _load_json(
        Path(artifacts["environment_manifest"]["path"]),
        f"{label} frozen environment manifest",
    )
    _mapping(
        environment_manifest,
        f"{label} frozen environment manifest",
    )
    _assert_equal(
        build_manifest.get("schema_version"),
        BUILD_MANIFEST_SCHEMA,
        f"{label} frozen build manifest.schema_version",
    )
    repository = _mapping(
        build_manifest.get("repository"),
        f"{label} frozen build manifest.repository",
    )
    for field, expected in (
        ("reference_pin", REFERENCE_PIN),
        ("optimized_pin", OPTIMIZED_PIN),
        ("reference_applied_head", methods["reference"]["head_revision"]),
        ("reference_tree", methods["reference"]["tree_hash"]),
        ("optimized_applied_head", methods["optimized"]["head_revision"]),
        ("optimized_tree", methods["optimized"]["tree_hash"]),
    ):
        _assert_equal(
            repository.get(field),
            expected,
            f"{label} frozen build manifest.repository.{field}",
        )
    build_root = Path(artifacts["build_manifest"]["path"]).parent.parent
    _assert_equal(
        build_root,
        resolved_run_cwd,
        f"{label} frozen build manifest repository root",
    )
    build_records = _mapping(
        build_manifest.get("builds"),
        f"{label} frozen build manifest.builds",
    )
    for method in METHODS:
        build_key = f"{method}_{manifest['mode']}"
        build = _mapping(
            build_records.get(build_key),
            f"{label} frozen build manifest.builds.{build_key}",
        )
        _assert_equal(
            build.get("observer_compiled"),
            manifest["mode"] == "fidelity",
            f"{label} frozen build manifest.{build_key}.observer_compiled",
        )
        _assert_equal(
            build.get("algorithm_revision"),
            methods[method]["base_revision"],
            f"{label} frozen build manifest.{build_key}.algorithm_revision",
        )
        _assert_equal(
            build.get("applied_head"),
            methods[method]["head_revision"],
            f"{label} frozen build manifest.{build_key}.applied_head",
        )
        raw_binary = Path(
            _string(
                build.get("binary_path"),
                f"{label} frozen build manifest.{build_key}.binary_path",
            )
        )
        build_binary = (
            raw_binary if raw_binary.is_absolute() else build_root / raw_binary
        )
        try:
            resolved_binary = build_binary.resolve(strict=True)
        except OSError as exc:
            raise AnalysisError(
                f"{label} frozen build manifest.{build_key}.binary_path "
                f"cannot be resolved: {exc}"
            ) from exc
        _assert_equal(
            str(resolved_binary),
            methods[method]["binary"]["path"],
            f"{label} frozen build manifest.{build_key}.binary_path",
        )
        _assert_equal(
            build.get("binary_sha256"),
            methods[method]["binary"]["sha256"],
            f"{label} frozen build manifest.{build_key}.binary_sha256",
        )

    raw_pairs = _list(plan["pairs"], f"{label} frozen plan.pairs")
    if not raw_pairs:
        raise AnalysisError(f"{label} frozen plan.pairs must not be empty")
    pairs: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(raw_pairs):
        pair_label = f"{label} frozen plan.pairs[{index}]"
        record = _mapping(value, pair_label)
        required_pair = {
            "pair_id",
            "environment",
            "seed",
            "robot_count",
            "map",
            "scenario",
        }
        unknown_pair = record.keys() - required_pair - {"config_overrides"}
        missing_pair = required_pair - record.keys()
        if missing_pair or unknown_pair:
            raise AnalysisError(
                f"{pair_label} schema mismatch: missing={sorted(missing_pair)} "
                f"unknown={sorted(unknown_pair)}"
            )
        pair_id = _string(record["pair_id"], f"{pair_label}.pair_id", identifier=True)
        if pair_id in pairs:
            raise AnalysisError(f"{label} frozen plan has duplicate pair_id")
        environment = _string(
            record["environment"], f"{pair_label}.environment", identifier=True
        )
        seed = _integer(
            record["seed"],
            f"{pair_label}.seed",
            minimum=1,
            maximum=(1 << 31) - 1,
        )
        if seed not in PHASE_SEEDS[manifest["phase"]]:
            raise AnalysisError(f"{pair_label}.seed is outside the frozen phase")
        robot_count = _integer(
            record["robot_count"],
            f"{pair_label}.robot_count",
            minimum=1,
            maximum=100_000,
        )
        overrides = _mapping(
            record.get("config_overrides", {}),
            f"{pair_label}.config_overrides",
        )
        collision = PROTECTED_CONFIG_KEYS & overrides.keys()
        if collision:
            raise AnalysisError(
                f"{pair_label}.config_overrides replaces protected keys "
                f"{sorted(collision)}"
            )
        for key, scalar in overrides.items():
            _string(key, f"{pair_label}.config_overrides key")
            if isinstance(scalar, (dict, list)) or scalar is None:
                raise AnalysisError(
                    f"{pair_label}.config_overrides.{key} is not a scalar"
                )
            if isinstance(scalar, float) and not math.isfinite(scalar):
                raise AnalysisError(
                    f"{pair_label}.config_overrides.{key} is non-finite"
                )
            if isinstance(scalar, str) and (
                "\n" in scalar or "\r" in scalar
            ):
                raise AnalysisError(
                    f"{pair_label}.config_overrides.{key} contains a newline"
                )
        pairs[pair_id] = {
            "pair_id": pair_id,
            "environment": environment,
            "seed": seed,
            "robot_count": robot_count,
            "map": _plan_artifact(record["map"], base, f"{pair_label}.map"),
            "scenario": _plan_artifact(
                record["scenario"], base, f"{pair_label}.scenario"
            ),
        }

    capacity_search = plan.get("capacity_search")
    if capacity_search is not None:
        environment = capacity_search["environment"]
        grid = set(capacity_search["grid"])
        if any(
            pair["environment"] != environment
            or pair["robot_count"] not in grid
            for pair in pairs.values()
        ):
            raise AnalysisError(
                f"{label} frozen capacity-search pairs violate environment/grid"
            )
        seeds_by_count: dict[int, set[int]] = defaultdict(set)
        for pair in pairs.values():
            seeds_by_count[pair["robot_count"]].add(pair["seed"])
        if set(seeds_by_count) != grid or len(
            {tuple(sorted(value)) for value in seeds_by_count.values()}
        ) != 1:
            raise AnalysisError(
                f"{label} frozen capacity-search pairs lack equal seed sets"
            )
    if capacity_evaluation is not None:
        expected_conditions = {
            (seed, count)
            for seed in PHASE_SEEDS[manifest["phase"]]
            for count in capacity_evaluation["grid"]
        }
        observed_conditions = [
            (pair["seed"], pair["robot_count"]) for pair in pairs.values()
        ]
        if (
            any(
                pair["environment"] != capacity_evaluation["environment"]
                for pair in pairs.values()
            )
            or len(observed_conditions) != len(expected_conditions)
            or set(observed_conditions) != expected_conditions
        ):
            raise AnalysisError(
                f"{label} frozen capacity evaluation lacks the exact "
                "phase-seed by full-grid condition set"
            )
        _validate_capacity_decision_binding(
            artifacts["capacity_decision"],
            capacity_evaluation,
            plan_artifacts=artifacts,
            phase=manifest["phase"],
            timeout_seconds=timeout,
            label=f"{label} frozen capacity decision",
        )

    execution_grid = (
        list(capacity_search["grid"])
        if capacity_search is not None
        else (
            list(capacity_evaluation["grid"])
            if capacity_evaluation is not None
            else None
        )
    )
    ordered_pairs = list(pairs.values())
    schedule: list[tuple[dict[str, Any], int]] = []
    if execution_grid is None:
        for repetition in range(1, manifest["repetitions"] + 1):
            schedule.extend(
                (pair, repetition) for pair in ordered_pairs
            )
    else:
        count_rank = {
            count: index for index, count in enumerate(execution_grid)
        }
        ordered_pairs.sort(
            key=lambda pair: (
                pair["seed"],
                count_rank[pair["robot_count"]],
                pair["pair_id"],
            )
        )
        by_seed_count = {
            (pair["seed"], pair["robot_count"]): pair
            for pair in ordered_pairs
        }
        seeds = sorted({pair["seed"] for pair in ordered_pairs})
        base_counts = list(execution_grid)
        count_randomizer = random.Random(
            plan["order_seed"] ^ 0x4341504143495459
        )
        count_randomizer.shuffle(base_counts)
        cycle = 0
        for repetition in range(1, manifest["repetitions"] + 1):
            for seed in seeds:
                count_order = (
                    base_counts if cycle % 2 == 0 else base_counts[::-1]
                )
                schedule.extend(
                    (by_seed_count[(seed, count)], repetition)
                    for count in count_order
                )
                cycle += 1
    expected_blocks = [
        {
            "block_id": (
                f"{pair['pair_id']}__rep-{repetition:03d}"
            ),
            "pair_id": pair["pair_id"],
            "repetition": repetition,
            "order": None,
            "environment": pair["environment"],
            "seed": pair["seed"],
            "robot_count": pair["robot_count"],
        }
        for pair, repetition in schedule
    ]
    order_randomizer = random.Random(plan["order_seed"])
    extra_order = (
        "AB" if order_randomizer.getrandbits(1) == 0 else "BA"
    )
    order_counts = {
        "AB": len(expected_blocks) // 2,
        "BA": len(expected_blocks) // 2,
    }
    if len(expected_blocks) % 2:
        order_counts[extra_order] += 1
    expected_orders = (
        ["AB"] * order_counts["AB"] + ["BA"] * order_counts["BA"]
    )
    order_randomizer.shuffle(expected_orders)
    for block, order in zip(expected_blocks, expected_orders):
        block["order"] = order
    _assert_equal(
        manifest["blocks"],
        expected_blocks,
        f"{label} frozen deterministic block schedule",
    )
    if manifest["mode"] == "timing":
        expected_warmups: list[dict[str, Any]] = []
        seen_warmup_maps: set[str] = set()
        for pair in ordered_pairs:
            map_sha = pair["map"]["sha256"]
            if map_sha in seen_warmup_maps:
                continue
            seen_warmup_maps.add(map_sha)
            expected_warmups.append(
                {
                    "block_id": (
                        f"warmup-{len(expected_warmups) + 1:03d}-"
                        f"{map_sha[:12]}"
                    ),
                    "pair_id": pair["pair_id"],
                    "repetition": 0,
                    "order": "warmup",
                    "environment": pair["environment"],
                    "seed": pair["seed"],
                    "robot_count": pair["robot_count"],
                    "map_sha256": map_sha,
                }
            )
        _assert_equal(
            manifest["warmup_blocks"],
            expected_warmups,
            f"{label} frozen deterministic warm-up schedule",
        )
        expected_maps = {pair["map"]["sha256"] for pair in pairs.values()}
        warmup_maps = {
            block["map_sha256"] for block in manifest["warmup_blocks"]
        }
        _assert_equal(
            warmup_maps,
            expected_maps,
            f"{label} frozen plan warm-up map binding",
        )
        _assert_equal(
            len(manifest["warmup_blocks"]),
            len(expected_maps),
            f"{label} frozen plan warm-up cardinality",
        )
        for block in manifest["warmup_blocks"]:
            pair = pairs.get(block["pair_id"])
            if pair is None:
                raise AnalysisError(f"{label} warm-up references unknown plan pair")
            for field in ("environment", "seed", "robot_count"):
                _assert_equal(
                    block[field], pair[field], f"{label} warm-up.{field}"
                )
            _assert_equal(
                block["map_sha256"],
                pair["map"]["sha256"],
                f"{label} warm-up.map_sha256",
            )

    _assert_equal(
        plan.get("capacity_search"),
        manifest["capacity_search"],
        f"{label} frozen plan.capacity_search",
    )
    _assert_equal(
        capacity_evaluation,
        manifest["capacity_evaluation"],
        f"{label} frozen plan.capacity_evaluation",
    )
    return {
        "path": str(raw_path.resolve(strict=True)),
        "sha256": plan_sha,
        "timeout_seconds": timeout,
        "timestep_seconds": float(plan["timestep_seconds"]),
        "idle_cooldown_seconds": plan["idle_cooldown_seconds"],
        "runner_sha256": plan["runner_sha256"],
        "artifacts": artifacts,
        "methods": methods,
        "pairs": pairs,
        "capacity_evaluation": capacity_evaluation,
    }


def _validate_session(
    session_root: Path,
    analysis_sha256: str,
) -> dict[str, Any]:
    try:
        root = session_root.resolve(strict=True)
    except OSError as exc:
        raise AnalysisError(
            f"session directory cannot be resolved: {session_root}: {exc}"
        ) from exc
    if not root.is_dir() or root.is_symlink():
        raise AnalysisError(f"session is not a regular directory: {root}")
    session, session_sha = _load_json(root / "session.json", "session.json")
    session = _mapping(session, "session.json")
    _exact_keys(session, SESSION_KEYS, "session.json")
    _assert_equal(
        session["schema_version"],
        SESSION_SCHEMA,
        "session.json.schema_version",
    )
    _string(session["completed_utc"], "session.json.completed_utc")

    manifest_value, manifest_sha = _load_json(
        root / "run_manifest.json", "run_manifest.json"
    )
    manifest = _validate_manifest(manifest_value, "run_manifest.json")
    frozen_plan = _validate_frozen_plan(manifest, "run_manifest.json")
    local_plan_path = root / "frozen_plan.json"
    local_plan, local_plan_sha = _load_json(
        local_plan_path, "frozen_plan.json"
    )
    local_plan = _mapping(local_plan, "frozen_plan.json")
    local_plan_bytes = local_plan_path.read_bytes()
    if local_plan_bytes != canonical_json_bytes(local_plan):
        raise AnalysisError("frozen_plan.json is not canonical JSON")
    source_plan_path = Path(manifest["plan_path"])
    _assert_equal(
        local_plan_bytes,
        source_plan_path.read_bytes(),
        "frozen_plan.json exact source-plan copy",
    )
    _assert_equal(
        local_plan_sha,
        frozen_plan["sha256"],
        "frozen_plan.json SHA-256",
    )
    _assert_equal(
        session["timestep_seconds"],
        manifest["timestep_seconds"],
        "session.json.timestep_seconds",
    )
    _assert_equal(
        session["idle_cooldown_seconds"],
        manifest["idle_cooldown_seconds"],
        "session.json.idle_cooldown_seconds",
    )
    _assert_equal(
        session["idle_cooldown"],
        manifest["idle_cooldown"],
        "session.json.idle_cooldown",
    )
    _assert_equal(
        session["capacity_evaluation"],
        manifest["capacity_evaluation"],
        "session.json.capacity_evaluation",
    )
    for field in (
        "minimum_free_disk_bytes",
        "maximum_peak_rss_mib",
        "power_mode",
        "thermal_policy",
        "task_validity_predicate_id",
    ):
        _assert_equal(
            session[field],
            manifest[field],
            f"session.json.{field}",
        )
    preflight, _ = _load_json(root / "preflight.json", "preflight.json")
    preflight = _mapping(preflight, "preflight.json")
    if (root / "preflight.json").read_bytes() != canonical_json_bytes(
        preflight
    ):
        raise AnalysisError("preflight.json is not canonical JSON")
    _assert_equal(
        preflight.get("host_conditions"),
        manifest["preflight_host_conditions"],
        "preflight.json.host_conditions",
    )
    _assert_equal(
        preflight.get("idle_cooldown"),
        manifest["idle_cooldown"],
        "preflight.json.idle_cooldown",
    )
    runs, runs_sha = _load_jsonl(root / "runs.jsonl", "runs.jsonl")
    pairs, pairs_sha = _load_jsonl(root / "pairs.jsonl", "pairs.jsonl")
    if not runs:
        raise AnalysisError("runs.jsonl must not be empty")
    for index, run in enumerate(runs):
        _validate_run(
            run,
            f"runs.jsonl[{index}]",
            root,
            manifest,
            analysis_sha256,
        )
        if run["seed"] not in PHASE_SEEDS[manifest["phase"]]:
            raise AnalysisError(
                f"runs.jsonl[{index}].seed is outside the frozen phase"
            )
        if run["terminal_category"] == "completed":
            for metric, ceiling in (
                ("total_cpu_seconds", frozen_plan["timeout_seconds"]),
                ("wall_seconds", frozen_plan["timeout_seconds"]),
                ("peak_rss_mib", manifest["maximum_peak_rss_mib"]),
            ):
                if float(run["resource"][metric]) > float(ceiling):
                    raise AnalysisError(
                        f"runs.jsonl[{index}] is completed but {metric} "
                        "exceeds its frozen ceiling"
                    )
    for index, pair in enumerate(pairs):
        _validate_pair_shape(pair, f"pairs.jsonl[{index}]")

    sequences = [run["sequence"] for run in runs]
    if sequences != list(range(1, len(runs) + 1)):
        raise AnalysisError("run sequence numbers are not contiguous JSONL order")
    attempt_ids = [run["attempt_id"] for run in runs]
    if len(attempt_ids) != len(set(attempt_ids)):
        raise AnalysisError("runs.jsonl has duplicate attempt IDs")
    by_attempt = {run["attempt_id"]: run for run in runs}
    by_block: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for run in runs:
        by_block[run["block_id"]].append(run)

    warmup_blocks = manifest["warmup_blocks"]
    planned_blocks = manifest["blocks"]
    warmup_by_id = {item["block_id"]: item for item in warmup_blocks}
    planned_by_id = {item["block_id"]: item for item in planned_blocks}
    known_blocks = set(warmup_by_id) | set(planned_by_id)
    unknown_blocks = set(by_block) - known_blocks
    if unknown_blocks:
        raise AnalysisError(
            f"runs.jsonl references unknown blocks: {sorted(unknown_blocks)}"
        )

    warmups_valid = True
    derived_host_contaminated_blocks: list[str] = []
    for block in warmup_blocks:
        label = f"warm-up block {block['block_id']}"
        attempts = by_block.get(block["block_id"], [])
        if len(attempts) != 2:
            raise AnalysisError(f"{label} does not have exactly two attempts")
        by_method = {item["method"]: item for item in attempts}
        if set(by_method) != set(METHODS):
            raise AnalysisError(f"{label} is missing a method")
        for attempt in attempts:
            _assert_equal(attempt["attempt_kind"], "warmup", f"{label} kind")
            for field in (
                "pair_id",
                "repetition",
                "order",
                "environment",
                "seed",
                "robot_count",
            ):
                _assert_equal(attempt[field], block[field], f"{label}.{field}")
            _assert_equal(
                attempt["provenance"]["map_sha256"],
                block["map_sha256"],
                f"{label}.map_sha256",
            )
            _assert_equal(
                attempt["method"],
                _expected_method("warmup", attempt["order_position"]),
                f"{label}.order_position",
            )
        same_config = (
            by_method["reference"]["provenance"]["config_sha256"]
            == by_method["optimized"]["provenance"]["config_sha256"]
        )
        warmup_copy, _ = _load_json(
            root / "blocks" / block["block_id"] / "warmup.json",
            f"{label} copy",
        )
        warmup_copy = _mapping(warmup_copy, f"{label} copy")
        host_before = _validate_host_snapshot(
            warmup_copy.get("host_conditions_before"),
            f"{label}.host_conditions_before",
        )
        host_after = _validate_host_snapshot(
            warmup_copy.get("host_conditions_after"),
            f"{label}.host_conditions_after",
        )
        host_valid = _host_block_valid(host_before, host_after)
        if not host_valid:
            derived_host_contaminated_blocks.append(block["block_id"])
        valid = (
            same_config
            and all(
                item["terminal_category"] == "completed" for item in attempts
            )
            and host_valid
        )
        warmups_valid = warmups_valid and valid
        expected_copy = {
            "schema_version": "dancers-comparison-warmup-v1",
            "block_id": block["block_id"],
            "pair_id": block["pair_id"],
            "environment": block["environment"],
            "seed": block["seed"],
            "robot_count": block["robot_count"],
            "map_sha256": block["map_sha256"],
            "attempt_ids": {
                method: by_method[method]["attempt_id"] for method in METHODS
            },
            "config_sha256_equal": same_config,
            "terminal_categories": {
                method: by_method[method]["terminal_category"]
                for method in METHODS
            },
            "warmup_valid": valid,
            "host_conditions_before": host_before,
            "host_conditions_after": host_after,
            "host_block_valid": host_valid,
            "block_terminal_category": (
                "completed"
                if host_valid
                else "host-power-thermal-contamination"
            ),
        }
        _assert_equal(warmup_copy, expected_copy, f"{label} copy")

    pair_block_ids = [pair["block_id"] for pair in pairs]
    if len(pair_block_ids) != len(set(pair_block_ids)):
        raise AnalysisError("pairs.jsonl has duplicate block IDs")
    planned_order = [item["block_id"] for item in planned_blocks]
    if pair_block_ids != planned_order[: len(pair_block_ids)]:
        raise AnalysisError(
            "pairs.jsonl is not an exact executed prefix of planned blocks"
        )
    referenced_attempts: set[str] = set()
    condition_by_pair_id: dict[str, tuple[Any, ...]] = {}
    for index, pair in enumerate(pairs):
        label = f"pairs.jsonl[{index}]"
        block = planned_by_id[pair["block_id"]]
        for field in (
            "pair_id",
            "repetition",
            "order",
            "environment",
            "seed",
            "robot_count",
        ):
            _assert_equal(pair[field], block[field], f"{label}.{field}")
        _assert_equal(pair["phase"], manifest["phase"], f"{label}.phase")
        _assert_equal(pair["mode"], manifest["mode"], f"{label}.mode")
        attempts = by_block.get(pair["block_id"], [])
        if len(attempts) != 2:
            raise AnalysisError(f"{label} does not have exactly two attempts")
        by_method = {item["method"]: item for item in attempts}
        if set(by_method) != set(METHODS):
            raise AnalysisError(f"{label} is missing a method")
        for attempt in attempts:
            _assert_equal(attempt["attempt_kind"], "measured", f"{label} kind")
            for field in (
                "pair_id",
                "repetition",
                "order",
                "phase",
                "mode",
                "environment",
                "seed",
                "robot_count",
            ):
                _assert_equal(attempt[field], pair[field], f"{label}.{field}")
            _assert_equal(
                attempt["method"],
                _expected_method(pair["order"], attempt["order_position"]),
                f"{label}.order_position",
            )
        expected_attempt_ids = {
            method: by_method[method]["attempt_id"] for method in METHODS
        }
        _assert_equal(
            pair["attempt_ids"], expected_attempt_ids, f"{label}.attempt_ids"
        )
        expected_terminals = {
            method: by_method[method]["terminal_category"]
            for method in METHODS
        }
        _assert_equal(
            pair["terminal_categories"],
            expected_terminals,
            f"{label}.terminal_categories",
        )
        same_config = (
            by_method["reference"]["provenance"]["config_sha256"]
            == by_method["optimized"]["provenance"]["config_sha256"]
        )
        _assert_equal(
            pair["config_sha256_equal"],
            same_config,
            f"{label}.config_sha256_equal",
        )
        for field in ("map_sha256", "scenario_sha256"):
            _assert_equal(
                by_method["reference"]["provenance"][field],
                by_method["optimized"]["provenance"][field],
                f"{label}.{field}",
            )
        plan_pair = frozen_plan["pairs"].get(pair["pair_id"])
        if plan_pair is None:
            raise AnalysisError(f"{label} has no matching frozen plan pair")
        _assert_equal(
            by_method["reference"]["provenance"]["map_sha256"],
            plan_pair["map"]["sha256"],
            f"{label} frozen map binding",
        )
        _assert_equal(
            by_method["reference"]["provenance"]["scenario_sha256"],
            plan_pair["scenario"]["sha256"],
            f"{label} frozen scenario binding",
        )
        complete = all(
            category == "completed"
            for category in expected_terminals.values()
        )
        if manifest["mode"] == "timing":
            _assert_equal(
                pair["semantic_trace_equal"],
                None,
                f"{label}.semantic_trace_equal",
            )
            _assert_equal(
                pair["frames_compared"], None, f"{label}.frames_compared"
            )
            _assert_equal(pair["mismatch"], None, f"{label}.mismatch")
            for field in (
                "paper_metrics_equal",
                "summary_parity_fields_equal",
                "summary_mismatch",
                "task_valid",
            ):
                _assert_equal(pair[field], None, f"{label}.{field}")
        elif complete and pair["semantic_trace_equal"] is None:
            raise AnalysisError(
                f"{label} completed fidelity pair has no trace verdict"
            )
        if manifest["mode"] == "fidelity" and complete:
            reference_metrics = by_method["reference"]["fidelity_metrics"]
            optimized_metrics = by_method["optimized"]["fidelity_metrics"]
            expected_paper_equal = (
                reference_metrics["paper_metrics"]
                == optimized_metrics["paper_metrics"]
            )
            expected_summary_equal = (
                reference_metrics["summary_parity_fields"]
                == optimized_metrics["summary_parity_fields"]
            )
            expected_task_valid = all(
                by_method[method]["task_valid"] is True for method in METHODS
            )
            _assert_equal(
                pair["paper_metrics_equal"],
                expected_paper_equal,
                f"{label}.paper_metrics_equal",
            )
            _assert_equal(
                pair["summary_parity_fields_equal"],
                expected_summary_equal,
                f"{label}.summary_parity_fields_equal",
            )
            _assert_equal(
                pair["task_valid"],
                expected_task_valid,
                f"{label}.task_valid",
            )
        expected_valid = pair["host_block_valid"] and same_config and complete and (
            manifest["mode"] == "timing"
            or (
                pair["semantic_trace_equal"] is True
                and pair["paper_metrics_equal"] is True
                and pair["summary_parity_fields_equal"] is True
                and pair["task_valid"] is True
            )
        )
        _assert_equal(pair["pair_valid"], expected_valid, f"{label}.pair_valid")
        if not pair["host_block_valid"]:
            derived_host_contaminated_blocks.append(pair["block_id"])
        referenced_attempts.update(expected_attempt_ids.values())
        pair_copy, _ = _load_json(
            root / "blocks" / pair["block_id"] / "pair.json",
            f"{label} copy",
        )
        _assert_equal(pair_copy, pair, f"{label} copy")

        identity = _condition_identity(by_method["reference"])
        previous = condition_by_pair_id.setdefault(pair["pair_id"], identity)
        _assert_equal(identity, previous, f"{label} condition identity")

    measured_attempts = {
        run["attempt_id"] for run in runs if run["attempt_kind"] == "measured"
    }
    if measured_attempts != referenced_attempts:
        missing = measured_attempts - referenced_attempts
        extra = referenced_attempts - measured_attempts
        raise AnalysisError(
            "measured attempts are unpaired: "
            f"unreferenced={sorted(missing)} missing={sorted(extra)}"
        )

    _assert_equal(
        _integer(session["attempt_count"], "session.json.attempt_count"),
        len(runs),
        "session.json.attempt_count",
    )
    warmup_attempt_count = sum(
        run["attempt_kind"] == "warmup" for run in runs
    )
    _assert_equal(
        session["warmup_attempt_count"],
        warmup_attempt_count,
        "session.json.warmup_attempt_count",
    )
    _assert_equal(
        _integer(session["pair_count"], "session.json.pair_count"),
        len(pairs),
        "session.json.pair_count",
    )
    _assert_equal(
        _boolean(session["warmups_valid"], "session.json.warmups_valid"),
        warmups_valid,
        "session.json.warmups_valid",
    )
    all_attempts_completed = all(
        run["terminal_category"] == "completed" for run in runs
    )
    _assert_equal(
        _boolean(
            session["all_attempts_completed"],
            "session.json.all_attempts_completed",
        ),
        all_attempts_completed,
        "session.json.all_attempts_completed",
    )
    all_pairs_valid = all(pair["pair_valid"] for pair in pairs) and warmups_valid
    _assert_equal(
        _boolean(session["all_pairs_valid"], "session.json.all_pairs_valid"),
        all_pairs_valid,
        "session.json.all_pairs_valid",
    )
    stopped_early = len(pairs) < len(planned_blocks)
    _assert_equal(
        _boolean(session["stopped_early"], "session.json.stopped_early"),
        stopped_early,
        "session.json.stopped_early",
    )
    recorded_host_contamination = [
        _string(
            item,
            f"session.json.host_contaminated_blocks[{index}]",
            identifier=True,
        )
        for index, item in enumerate(
            _list(
                session["host_contaminated_blocks"],
                "session.json.host_contaminated_blocks",
            )
        )
    ]
    _assert_equal(
        recorded_host_contamination,
        derived_host_contaminated_blocks,
        "session.json.host_contaminated_blocks",
    )

    capacity_sha: str | None = None
    capacity_state: dict[str, Any] | None = None
    manifest_capacity = manifest["capacity_search"]
    capacity_path = root / "capacity.json"
    if manifest_capacity is None:
        _assert_equal(session["capacity"], None, "session.json.capacity")
        if capacity_path.exists() or capacity_path.is_symlink():
            raise AnalysisError("non-capacity session has capacity.json")
    else:
        capacity_value, capacity_sha = _load_json(
            capacity_path, "capacity.json"
        )
        capacity_state = _validate_capacity(
            capacity_value, manifest_capacity, "capacity.json"
        )
        _assert_equal(
            session["capacity"], capacity_state, "session.json.capacity"
        )

    globals_seen: dict[str, str] = {}
    method_provenance: dict[str, dict[str, str]] = {}
    observer_summaries: dict[str, dict[str, Any]] = {}
    for run in runs:
        provenance = run["provenance"]
        for key in GLOBAL_PROVENANCE_KEYS:
            value = provenance[key]
            if key in globals_seen:
                _assert_equal(value, globals_seen[key], f"provenance.{key}")
            else:
                globals_seen[key] = value
        method_key = f"{run['mode']}:{run['method']}"
        identity = {
            key: provenance[key]
            for key in (
                "algorithm_revision",
                "source_head_revision",
                "source_tree_hash",
                "binary_sha256",
            )
        }
        if method_key in method_provenance:
            _assert_equal(
                identity,
                method_provenance[method_key],
                f"provenance identity {method_key}",
            )
        else:
            method_provenance[method_key] = identity
        summary_artifact = run["artifacts"]["summary"]
        if summary_artifact is not None:
            summary, _ = _load_json(
                Path(summary_artifact["path"]),
                f"observer summary {run['attempt_id']}",
            )
            observer_summaries[run["attempt_id"]] = _mapping(
                summary, f"observer summary {run['attempt_id']}"
            )

    _assert_equal(
        frozen_plan["runner_sha256"],
        globals_seen["runner_sha256"],
        "frozen plan runner provenance",
    )
    artifact_provenance = {
        "pixi_lock": "pixi_lock_sha256",
        "build_manifest": "build_manifest_sha256",
        "protocol": "protocol_sha256",
        "experiment_manifest": "experiment_manifest_sha256",
        "analysis_revision": "analysis_revision_sha256",
    }
    for artifact_name, provenance_name in artifact_provenance.items():
        _assert_equal(
            frozen_plan["artifacts"][artifact_name]["sha256"],
            globals_seen[provenance_name],
            f"frozen plan artifact provenance {artifact_name}",
        )
    for method in METHODS:
        identity = method_provenance[f"{manifest['mode']}:{method}"]
        planned = frozen_plan["methods"][method]
        for planned_name, identity_name in (
            ("base_revision", "algorithm_revision"),
            ("head_revision", "source_head_revision"),
            ("tree_hash", "source_tree_hash"),
        ):
            _assert_equal(
                planned[planned_name],
                identity[identity_name],
                f"frozen plan method provenance {method}.{planned_name}",
            )
        _assert_equal(
            planned["binary"]["sha256"],
            identity["binary_sha256"],
            f"frozen plan method provenance {method}.binary",
        )

    session_id = hashlib.sha256(
        (
            session_sha
            + manifest_sha
            + local_plan_sha
            + runs_sha
            + pairs_sha
        ).encode("ascii")
    ).hexdigest()[:16]
    return {
        "session_id": session_id,
        "path": str(root),
        "session": session,
        "manifest": manifest,
        "runs": runs,
        "pairs": pairs,
        "by_attempt": by_attempt,
        "capacity": capacity_state,
        "frozen_plan": frozen_plan,
        "hashes": {
            "session_json_sha256": session_sha,
            "run_manifest_json_sha256": manifest_sha,
            "frozen_plan_sha256": local_plan_sha,
            "runs_jsonl_sha256": runs_sha,
            "pairs_jsonl_sha256": pairs_sha,
            "capacity_json_sha256": capacity_sha,
        },
        "global_provenance": globals_seen,
        "method_provenance": method_provenance,
        "observer_summaries": observer_summaries,
    }


def _median(values: Iterable[float]) -> float:
    items = list(values)
    if not items:
        raise AnalysisError("cannot calculate a median of no values")
    result = float(statistics.median(items))
    if not math.isfinite(result):
        raise AnalysisError("calculated a non-finite median")
    return result


def _metric_values(
    reference: dict[str, float],
    optimized: dict[str, float],
) -> dict[str, dict[str, float]]:
    effects: dict[str, dict[str, float]] = {}
    for metric in RESOURCE_METRICS:
        ref = reference[metric]
        opt = optimized[metric]
        if ref <= 0.0 or opt <= 0.0:
            raise AnalysisError(f"{metric} must be positive for ratio analysis")
        effects[metric] = {
            "optimized_over_reference_ratio": opt / ref,
            "optimized_minus_reference": opt - ref,
        }
    return effects


def _collapse_timing(
    sessions: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[tuple[Any, ...], list[tuple[dict[str, Any], dict[str, Any]]]] = (
        defaultdict(list)
    )
    expected_repetitions: dict[tuple[Any, ...], int] = {}
    for session in sessions:
        if session["manifest"]["mode"] != "timing":
            continue
        for pair in session["pairs"]:
            attempts = {
                method: session["by_attempt"][pair["attempt_ids"][method]]
                for method in METHODS
            }
            reference = attempts["reference"]
            key = (session["session_id"],) + _condition_identity(reference)
            groups[key].append((pair, attempts))
            expected_repetitions[key] = session["manifest"]["repetitions"]

    collapsed: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for key in sorted(groups):
        entries = groups[key]
        session_id, environment, map_sha, scenario_sha, seed, robot_count = key
        repetitions = [pair["repetition"] for pair, _ in entries]
        if len(repetitions) != len(set(repetitions)):
            raise AnalysisError(
                f"duplicate measured repetition for timing condition {key}"
            )
        valid = [
            (pair, attempts)
            for pair, attempts in entries
            if pair["pair_valid"]
        ]
        expected = expected_repetitions[key]
        eligible = len(entries) == expected and len(valid) == expected
        base = {
            "session_id": session_id,
            "environment": environment,
            "map_sha256": map_sha,
            "scenario_sha256": scenario_sha,
            "seed": seed,
            "robot_count": robot_count,
            "planned_repetitions": expected,
            "observed_repetitions": len(entries),
            "paired_completed_repetitions": len(valid),
            "failed_repetitions": sorted(
                pair["repetition"] for pair, _ in entries if not pair["pair_valid"]
            ),
        }
        if not eligible:
            reason = (
                "missing-measured-repetition"
                if len(entries) != expected
                else "failed-measured-repetition"
            )
            excluded.append({**base, "reason": reason})
            continue
        method_metrics: dict[str, dict[str, float]] = {}
        for method in METHODS:
            cpu = _median(
                attempts[method]["resource"]["total_cpu_seconds"]
                for _, attempts in valid
            )
            wall = _median(
                attempts[method]["resource"]["wall_seconds"]
                for _, attempts in valid
            )
            rss = _median(
                attempts[method]["resource"]["peak_rss_mib"]
                for _, attempts in valid
            )
            timesteps = next(
                session["manifest"]["fixed_work_timesteps"]
                for session in sessions
                if session["session_id"] == session_id
            )
            robot_timesteps = robot_count * timesteps
            method_metrics[method] = {
                "total_cpu_seconds": cpu,
                "wall_seconds": wall,
                "peak_rss_mib": rss,
                "cpu_seconds_per_robot_timestep": cpu / robot_timesteps,
                "robot_timesteps_per_cpu_second": robot_timesteps / cpu,
            }
        collapsed.append(
            {
                **base,
                "fixed_work_timesteps": timesteps,
                "methods": method_metrics,
                "effects": _metric_values(
                    method_metrics["reference"],
                    method_metrics["optimized"],
                ),
            }
        )
    return collapsed, excluded


def _percentile(sorted_values: list[float], probability: float) -> float:
    if not sorted_values:
        raise AnalysisError("cannot calculate a percentile of no values")
    if len(sorted_values) == 1:
        return sorted_values[0]
    location = (len(sorted_values) - 1) * probability
    low = math.floor(location)
    high = math.ceil(location)
    if low == high:
        return sorted_values[low]
    fraction = location - low
    return (
        sorted_values[low] * (1.0 - fraction)
        + sorted_values[high] * fraction
    )


def _bootstrap_seed(label: str) -> int:
    payload = f"{FROZEN_BOOTSTRAP_SEED}:{label}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big")


def _exact_cluster_sign_flip_p(rows: list[dict[str, Any]]) -> float:
    """Exact one-sided test of lower optimized total CPU at map×seed level."""
    clusters: dict[str, float] = defaultdict(float)
    for row in rows:
        name = f"{row['environment']}:{row['map_sha256']}:{row['seed']}"
        ratio = row["effects"]["total_cpu_seconds"][
            "optimized_over_reference_ratio"
        ]
        clusters[name] += math.log(ratio)
    values = [clusters[name] for name in sorted(clusters)]
    if not values:
        raise AnalysisError("compute sign-flip test has no map-seed clusters")
    if len(values) > 20:
        raise AnalysisError(
            "exact compute sign-flip test exceeds the frozen 20-cluster phase"
        )
    observed_sum = sum(values)
    tolerance = 1e-12 * max(1.0, abs(observed_sum))
    extreme = 0
    permutations = 1 << len(values)
    for mask in range(permutations):
        signed_sum = 0.0
        for index, value in enumerate(values):
            signed_sum += value if mask & (1 << index) else -value
        if signed_sum <= observed_sum + tolerance:
            extreme += 1
    return extreme / permutations


def _effect_summary(
    rows: list[dict[str, Any]],
    label: str,
    bootstrap_samples: int,
) -> dict[str, Any]:
    if not rows:
        return {
            "eligible_condition_count": 0,
            "cluster_count": 0,
            "metrics": {},
        }
    clusters: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        cluster = (
            f"{row['environment']}:{row['map_sha256']}:{row['seed']}"
        )
        clusters[cluster].append(row)
    cluster_names = sorted(clusters)
    metrics = tuple(RESOURCE_METRICS)
    point: dict[str, dict[str, float]] = {}
    for metric in metrics:
        ratios = [
            row["effects"][metric]["optimized_over_reference_ratio"]
            for row in rows
        ]
        differences = [
            row["effects"][metric]["optimized_minus_reference"] for row in rows
        ]
        ratio = math.exp(statistics.fmean(math.log(item) for item in ratios))
        point[metric] = {
            "ratio": ratio,
            "percentage_change": (ratio - 1.0) * 100.0,
            "absolute_difference": statistics.fmean(differences),
        }

    ratio_samples = {metric: [] for metric in metrics}
    difference_samples = {metric: [] for metric in metrics}
    randomizer = random.Random(_bootstrap_seed(label))
    cluster_count = len(cluster_names)
    cluster_aggregates: list[
        tuple[int, list[float], list[float]]
    ] = []
    for name in cluster_names:
        cluster_rows = clusters[name]
        cluster_aggregates.append(
            (
                len(cluster_rows),
                [
                    sum(
                        math.log(
                            row["effects"][metric][
                                "optimized_over_reference_ratio"
                            ]
                        )
                        for row in cluster_rows
                    )
                    for metric in metrics
                ],
                [
                    sum(
                        row["effects"][metric][
                            "optimized_minus_reference"
                        ]
                        for row in cluster_rows
                    )
                    for metric in metrics
                ],
            )
        )
    for _ in range(bootstrap_samples):
        sampled_count = 0
        sampled_log_ratios = [0.0] * len(metrics)
        sampled_differences = [0.0] * len(metrics)
        for _cluster_draw in range(cluster_count):
            count, log_ratios, differences = cluster_aggregates[
                randomizer.randrange(cluster_count)
            ]
            sampled_count += count
            for index in range(len(metrics)):
                sampled_log_ratios[index] += log_ratios[index]
                sampled_differences[index] += differences[index]
        for index, metric in enumerate(metrics):
            ratio_samples[metric].append(
                math.exp(sampled_log_ratios[index] / sampled_count)
            )
            difference_samples[metric].append(
                sampled_differences[index] / sampled_count
            )
    result_metrics: dict[str, Any] = {}
    for metric in metrics:
        ratios = sorted(ratio_samples[metric])
        differences = sorted(difference_samples[metric])
        ratio = point[metric]["ratio"]
        result_metrics[metric] = {
            "unit": RESOURCE_METRICS[metric],
            "geometric_mean_optimized_over_reference_ratio": ratio,
            "percentage_change": point[metric]["percentage_change"],
            "ratio_ci95": [
                _percentile(ratios, 0.025),
                _percentile(ratios, 0.975),
            ],
            "mean_optimized_minus_reference": point[metric][
                "absolute_difference"
            ],
            "absolute_difference_ci95": [
                _percentile(differences, 0.025),
                _percentile(differences, 0.975),
            ],
        }
    cpu_metric = result_metrics["total_cpu_seconds"]
    if cluster_count <= 20:
        cpu_metric["one_sided_exact_map_seed_sign_flip_p_value"] = (
            _exact_cluster_sign_flip_p(rows)
        )
        cpu_metric["sign_flip_availability_reason"] = None
    else:
        cpu_metric["one_sided_exact_map_seed_sign_flip_p_value"] = None
        cpu_metric["sign_flip_availability_reason"] = (
            "exact enumeration is restricted to an environment-level "
            "maximum of 20 independent map-seed clusters"
        )
    return {
        "eligible_condition_count": len(rows),
        "cluster_count": cluster_count,
        "metrics": result_metrics,
    }


def _holm_environment_compute_tests(
    by_environment: dict[str, dict[str, Any]],
    *,
    complete: bool = True,
) -> dict[str, Any]:
    observed = set(by_environment)
    required = set(CONFIRMATORY_ENVIRONMENTS)
    missing = sorted(required - observed)
    unexpected = sorted(observed - required)
    unavailable = [
        environment
        for environment in CONFIRMATORY_ENVIRONMENTS
        if (
            environment in by_environment
            and (
                "total_cpu_seconds"
                not in by_environment[environment].get("metrics", {})
                or by_environment[environment]
                .get("metrics", {})
                .get("total_cpu_seconds", {})
                .get("one_sided_exact_map_seed_sign_flip_p_value")
                is None
            )
        )
    ]
    if not complete or missing or unexpected or unavailable:
        return {
            "available": False,
            "method": "Holm step-down correction",
            "familywise_alpha": HOLM_ALPHA,
            "required_environments": list(CONFIRMATORY_ENVIRONMENTS),
            "missing_environments": missing,
            "unexpected_environments": unexpected,
            "environments_without_estimates": unavailable,
            "tests": {},
            "reason": (
                "all five complete environment-specific compute tests are "
                "required before multiplicity-adjusted decisions"
            ),
        }
    raw = {
        environment: by_environment[environment]["metrics"][
            "total_cpu_seconds"
        ]["one_sided_exact_map_seed_sign_flip_p_value"]
        for environment in CONFIRMATORY_ENVIRONMENTS
    }
    ordered = sorted(raw, key=lambda name: (raw[name], name))
    adjusted: dict[str, float] = {}
    running = 0.0
    family_size = len(ordered)
    for rank, environment in enumerate(ordered, start=1):
        running = max(
            running,
            min(1.0, (family_size - rank + 1) * raw[environment]),
        )
        adjusted[environment] = running
    tests = {
        environment: {
            "unadjusted_one_sided_p_value": raw[environment],
            "holm_adjusted_p_value": adjusted[environment],
            "reject_no_compute_reduction": (
                adjusted[environment] <= HOLM_ALPHA
            ),
        }
        for environment in CONFIRMATORY_ENVIRONMENTS
    }
    return {
        "available": True,
        "method": (
            "Holm step-down correction of exact one-sided map-seed cluster "
            "sign-flip tests on total-CPU log ratios"
        ),
        "familywise_alpha": HOLM_ALPHA,
        "required_environments": list(CONFIRMATORY_ENVIRONMENTS),
        "missing_environments": [],
        "unexpected_environments": [],
        "environments_without_estimates": [],
        "tests": tests,
        "all_five_reject": all(
            item["reject_no_compute_reduction"] for item in tests.values()
        ),
    }


def _paired_binary_summary(
    rows: list[dict[str, Any]],
    label: str,
    bootstrap_samples: int,
) -> dict[str, Any]:
    if not rows:
        return {"available": False, "paired_condition_count": 0}
    independent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        cluster = f"{row['environment']}:{row['map_sha256']}:{row['seed']}"
        independent[cluster].append(row)
    aggregated = [
        {
            "cluster": cluster,
            "reference": all(
                _boolean(row["reference"], f"{label}.reference")
                for row in cluster_rows
            ),
            "optimized": all(
                _boolean(row["optimized"], f"{label}.optimized")
                for row in cluster_rows
            ),
        }
        for cluster, cluster_rows in sorted(independent.items())
    ]
    counts = {"n00": 0, "n01": 0, "n10": 0, "n11": 0}
    clusters: dict[str, list[float]] = defaultdict(list)
    for row in aggregated:
        reference = row["reference"]
        optimized = row["optimized"]
        counts[f"n{int(reference)}{int(optimized)}"] += 1
        clusters[row["cluster"]].append(float(optimized) - float(reference))
    discordant = counts["n01"] + counts["n10"]
    if discordant == 0:
        mcnemar_p = 1.0
    else:
        smaller = min(counts["n01"], counts["n10"])
        lower_tail = sum(
            math.comb(discordant, value)
            for value in range(smaller + 1)
        ) / (2**discordant)
        mcnemar_p = min(1.0, 2.0 * lower_tail)
    cluster_names = sorted(clusters)
    randomizer = random.Random(_bootstrap_seed(label))
    samples: list[float] = []
    for _ in range(bootstrap_samples):
        total = 0.0
        count = 0
        for _draw in cluster_names:
            values = clusters[
                cluster_names[randomizer.randrange(len(cluster_names))]
            ]
            total += sum(values)
            count += len(values)
        samples.append(total / count)
    samples.sort()
    risk_difference = (
        counts["n01"] - counts["n10"]
    ) / len(aggregated)
    return {
        "available": True,
        "paired_condition_count": len(aggregated),
        "source_condition_count": len(rows),
        "aggregation": (
            "independent environment-map-seed endpoint; a method succeeds "
            "only if all preregistered count-level conditions succeed"
        ),
        "cell_orientation": (
            "nXY: X=reference success (0/1), "
            "Y=optimized success (0/1)"
        ),
        "cells": counts,
        "discordant_pair_count": discordant,
        "exact_two_sided_mcnemar_p_value": mcnemar_p,
        "optimized_minus_reference_risk_difference": risk_difference,
        "risk_difference_ci95": [
            _percentile(samples, 0.025),
            _percentile(samples, 0.975),
        ],
        "risk_difference_interval_method": (
            "map-seed cluster percentile bootstrap"
        ),
    }


def _binary_endpoint(
    rows: list[dict[str, Any]],
    incomplete: list[dict[str, Any]],
    label: str,
    bootstrap_samples: int,
    *,
    not_applicable_reason: str | None = None,
) -> dict[str, Any]:
    if not_applicable_reason is not None:
        return {
            "applicable": False,
            "reason": not_applicable_reason,
            "complete": not incomplete,
            "incomplete_conditions": incomplete,
            "overall": {"available": False, "paired_condition_count": 0},
            "by_environment": {},
        }
    environments = sorted({row["environment"] for row in rows})
    return {
        "applicable": True,
        "complete": not incomplete,
        "incomplete_conditions": incomplete,
        "overall": _paired_binary_summary(
            rows, f"{label}:overall", bootstrap_samples
        ),
        "by_environment": {
            environment: _paired_binary_summary(
                [row for row in rows if row["environment"] == environment],
                f"{label}:environment:{environment}",
                bootstrap_samples,
            )
            for environment in environments
        },
    }


def _timing_sensitivity_and_completion(
    sessions: list[dict[str, Any]],
    bootstrap_samples: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    collapsed: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    completion_rows: list[dict[str, Any]] = []
    imputed = {method: 0 for method in METHODS}
    ceilings: list[dict[str, Any]] = []
    for session in sessions:
        timeout = session["frozen_plan"]["timeout_seconds"]
        rss_ceiling = session["manifest"]["maximum_peak_rss_mib"]
        ceilings.append(
            {
                "session_id": session["session_id"],
                "total_cpu_seconds": timeout,
                "wall_seconds": timeout,
                "peak_rss_mib": rss_ceiling,
                "source_plan_sha256": session["frozen_plan"]["sha256"],
            }
        )
        pair_by_block = {
            pair["block_id"]: pair for pair in session["pairs"]
        }
        planned: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for block in session["manifest"]["blocks"]:
            planned[block["pair_id"]].append(block)
        for pair_id in sorted(planned):
            blocks = planned[pair_id]
            first = blocks[0]
            base = {
                "session_id": session["session_id"],
                "pair_id": pair_id,
                "environment": first["environment"],
                "seed": first["seed"],
                "robot_count": first["robot_count"],
                "planned_repetitions": session["manifest"]["repetitions"],
            }
            expected_repetitions = list(
                range(1, session["manifest"]["repetitions"] + 1)
            )
            if sorted(block["repetition"] for block in blocks) != (
                expected_repetitions
            ):
                unavailable.append(
                    {**base, "reason": "incomplete-frozen-repetition-set"}
                )
                continue
            pairs = [pair_by_block.get(block["block_id"]) for block in blocks]
            if any(pair is None for pair in pairs):
                unavailable.append(
                    {**base, "reason": "unexecuted-planned-block"}
                )
                continue
            present_pairs = [pair for pair in pairs if pair is not None]
            if any(
                not pair["host_block_valid"]
                or not pair["config_sha256_equal"]
                for pair in present_pairs
            ):
                unavailable.append(
                    {**base, "reason": "host-or-configuration-contamination"}
                )
                continue
            attempts_by_repetition = [
                {
                    method: session["by_attempt"][pair["attempt_ids"][method]]
                    for method in METHODS
                }
                for pair in present_pairs
            ]
            identity = _condition_identity(
                attempts_by_repetition[0]["reference"]
            )
            _, map_sha, scenario_sha, _, _ = identity
            method_values: dict[str, dict[str, list[float]]] = {
                method: {
                    "total_cpu_seconds": [],
                    "wall_seconds": [],
                    "peak_rss_mib": [],
                }
                for method in METHODS
            }
            method_success = {method: True for method in METHODS}
            for attempts in attempts_by_repetition:
                for method in METHODS:
                    attempt = attempts[method]
                    if attempt["terminal_category"] == "completed":
                        observed: dict[str, float] = {}
                        for metric, ceiling in (
                            ("total_cpu_seconds", timeout),
                            ("wall_seconds", timeout),
                            ("peak_rss_mib", rss_ceiling),
                        ):
                            value = _finite(
                                attempt["resource"][metric],
                                f"{attempt['attempt_id']}.{metric}",
                                positive=True,
                            )
                            assert value is not None
                            observed[metric] = value
                        if any(
                            observed[metric] > ceiling
                            for metric, ceiling in (
                                ("total_cpu_seconds", timeout),
                                ("wall_seconds", timeout),
                                ("peak_rss_mib", rss_ceiling),
                            )
                        ):
                            raise AnalysisError(
                                f"{attempt['attempt_id']} is completed above "
                                "a frozen resource ceiling"
                            )
                        for metric, value in observed.items():
                            method_values[method][metric].append(value)
                    else:
                        method_success[method] = False
                        imputed[method] += 1
                        method_values[method]["total_cpu_seconds"].append(
                            timeout
                        )
                        method_values[method]["wall_seconds"].append(timeout)
                        method_values[method]["peak_rss_mib"].append(
                            rss_ceiling
                        )
            robot_timesteps = (
                first["robot_count"]
                * session["manifest"]["fixed_work_timesteps"]
            )
            method_metrics: dict[str, dict[str, float]] = {}
            for method in METHODS:
                cpu = _median(method_values[method]["total_cpu_seconds"])
                method_metrics[method] = {
                    "total_cpu_seconds": cpu,
                    "wall_seconds": _median(
                        method_values[method]["wall_seconds"]
                    ),
                    "peak_rss_mib": _median(
                        method_values[method]["peak_rss_mib"]
                    ),
                    "cpu_seconds_per_robot_timestep": (
                        cpu / robot_timesteps
                    ),
                    "robot_timesteps_per_cpu_second": (
                        robot_timesteps / cpu
                    ),
                }
            collapsed.append(
                {
                    **base,
                    "map_sha256": map_sha,
                    "scenario_sha256": scenario_sha,
                    "fixed_work_timesteps": session["manifest"][
                        "fixed_work_timesteps"
                    ],
                    "methods": method_metrics,
                    "effects": _metric_values(
                        method_metrics["reference"],
                        method_metrics["optimized"],
                    ),
                }
            )
            completion_rows.append(
                {
                    **base,
                    "map_sha256": map_sha,
                    "scenario_sha256": scenario_sha,
                    "reference": method_success["reference"],
                    "optimized": method_success["optimized"],
                }
            )
    environments = sorted(
        {
            block["environment"]
            for session in sessions
            for block in session["manifest"]["blocks"]
        }
    )
    by_environment = {
        environment: _effect_summary(
            [row for row in collapsed if row["environment"] == environment],
            f"timing:sensitivity:environment:{environment}",
            bootstrap_samples,
        )
        for environment in environments
    }
    sensitivity_complete = not unavailable
    sensitivity = {
        "available": sensitivity_complete and bool(collapsed),
        "estimand": (
            "intention-to-test; identical frozen CPU, wall, and RSS ceilings "
            "assigned to every failed attempt from either method"
        ),
        "ceilings": ceilings,
        "imputed_attempt_counts": {
            **imputed,
            "total": sum(imputed.values()),
        },
        "collapsed_conditions": collapsed,
        "unavailable_conditions": unavailable,
        "by_environment": by_environment,
        "overall": _effect_summary(
            collapsed, "timing:sensitivity:overall", bootstrap_samples
        ),
        "holm_environment_compute_tests": (
            _holm_environment_compute_tests(
                by_environment, complete=sensitivity_complete
            )
        ),
    }
    completion = _binary_endpoint(
        completion_rows,
        unavailable,
        "timing:process-completion",
        bootstrap_samples,
    )
    return sensitivity, completion


def _timing_analysis(
    sessions: list[dict[str, Any]],
    bootstrap_samples: int,
) -> dict[str, Any] | None:
    timing_sessions = [
        item for item in sessions if item["manifest"]["mode"] == "timing"
    ]
    if not timing_sessions:
        return None
    timestep_counts = {
        item["manifest"]["fixed_work_timesteps"] for item in timing_sessions
    }
    if len(timestep_counts) != 1:
        raise AnalysisError("timing sessions use different work budgets")
    collapsed, excluded = _collapse_timing(timing_sessions)
    environments = sorted(
        {
            pair["environment"]
            for session in timing_sessions
            for pair in session["pairs"]
        }
    )
    by_environment = {
        environment: _effect_summary(
            [row for row in collapsed if row["environment"] == environment],
            f"timing:environment:{environment}",
            bootstrap_samples,
        )
        for environment in environments
    }
    by_environment_and_robot_count: dict[str, dict[str, Any]] = {}
    for environment in environments:
        counts = sorted(
            {
                row["robot_count"]
                for row in collapsed
                if row["environment"] == environment
            }
        )
        by_environment_and_robot_count[environment] = {
            str(count): _effect_summary(
                [
                    row
                    for row in collapsed
                    if row["environment"] == environment
                    and row["robot_count"] == count
                ],
                f"timing:environment:{environment}:n:{count}",
                bootstrap_samples,
            )
            for count in counts
        }
    sensitivity, process_completion = _timing_sensitivity_and_completion(
        timing_sessions, bootstrap_samples
    )
    return {
        "fixed_work_timesteps": next(iter(timestep_counts)),
        "collapsed_conditions": collapsed,
        "excluded_conditions": excluded,
        "by_environment": by_environment,
        "holm_environment_compute_tests": (
            _holm_environment_compute_tests(by_environment)
        ),
        "by_environment_and_robot_count": by_environment_and_robot_count,
        "overall": _effect_summary(
            collapsed, "timing:overall", bootstrap_samples
        ),
        "failed_run_resource_ceiling_sensitivity": sensitivity,
        "paired_process_completion": process_completion,
    }


def _fidelity_counts(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    exact = sum(pair["semantic_trace_equal"] is True for pair in pairs)
    discordant = sum(pair["semantic_trace_equal"] is False for pair in pairs)
    indeterminate = sum(pair["semantic_trace_equal"] is None for pair in pairs)
    frames = [
        pair["frames_compared"]
        for pair in pairs
        if pair["frames_compared"] is not None
    ]
    condition_groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for pair in pairs:
        condition_groups[
            (
                pair["environment"],
                pair["seed"],
                pair["robot_count"],
                pair["pair_id"],
            )
        ].append(pair)
    exact_conditions = 0
    discordant_conditions = 0
    indeterminate_conditions = 0
    for items in condition_groups.values():
        verdicts = [item["semantic_trace_equal"] for item in items]
        if all(verdict is True for verdict in verdicts):
            exact_conditions += 1
        elif any(verdict is False for verdict in verdicts):
            discordant_conditions += 1
        else:
            indeterminate_conditions += 1
    return {
        "attempted_pair_blocks": len(pairs),
        "valid_pair_blocks": sum(pair["pair_valid"] for pair in pairs),
        "exact_trace_pair_blocks": exact,
        "discordant_trace_pair_blocks": discordant,
        "indeterminate_trace_pair_blocks": indeterminate,
        "independent_conditions": len(condition_groups),
        "exact_trace_conditions": exact_conditions,
        "discordant_trace_conditions": discordant_conditions,
        "indeterminate_trace_conditions": indeterminate_conditions,
        "frames_compared_total": sum(frames),
        "frames_compared_min": min(frames) if frames else None,
        "frames_compared_max": max(frames) if frames else None,
    }


def _restricted_time_observation(
    summary: dict[str, Any],
    label: str,
    expected_final_tick: int,
    frozen_timestep_seconds: float,
) -> dict[str, Any]:
    for field in (
        "final_tick",
        "simulated_time_seconds",
        "all_goals_reached",
        "first_all_goals_tick",
    ):
        if field not in summary:
            raise AnalysisError(f"{label} is missing {field}")
    final_tick = _integer(summary["final_tick"], f"{label}.final_tick")
    _assert_equal(final_tick, expected_final_tick, f"{label}.final_tick")
    observed_ceiling = _finite(
        summary["simulated_time_seconds"],
        f"{label}.simulated_time_seconds",
        positive=True,
    )
    assert observed_ceiling is not None
    ceiling = expected_final_tick * frozen_timestep_seconds
    _assert_equal(
        observed_ceiling,
        ceiling,
        f"{label}.simulated_time_seconds frozen ceiling",
    )
    all_goals = _boolean(
        summary["all_goals_reached"], f"{label}.all_goals_reached"
    )
    first_tick = summary["first_all_goals_tick"]
    if all_goals:
        first_tick = _integer(
            first_tick,
            f"{label}.first_all_goals_tick",
            maximum=final_tick,
        )
        restricted_time = ceiling * first_tick / final_tick
    else:
        _assert_equal(
            first_tick, None, f"{label}.first_all_goals_tick"
        )
        restricted_time = ceiling
    return {
        "ceiling_seconds": ceiling,
        "event": all_goals,
        "restricted_time_seconds": restricted_time,
    }


def _restricted_mean_summary(
    rows: list[dict[str, Any]],
    label: str,
    bootstrap_samples: int,
) -> dict[str, Any]:
    if not rows:
        return {"available": False, "paired_condition_count": 0}
    clusters: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        cluster = f"{row['environment']}:{row['map_sha256']}:{row['seed']}"
        clusters[cluster].append(
            row["optimized_time_seconds"] - row["reference_time_seconds"]
        )
    names = sorted(clusters)
    randomizer = random.Random(_bootstrap_seed(label))
    samples: list[float] = []
    for _ in range(bootstrap_samples):
        total = 0.0
        count = 0
        for _draw in names:
            values = clusters[names[randomizer.randrange(len(names))]]
            total += sum(values)
            count += len(values)
        samples.append(total / count)
    samples.sort()
    reference = statistics.fmean(
        row["reference_time_seconds"] for row in rows
    )
    optimized = statistics.fmean(
        row["optimized_time_seconds"] for row in rows
    )
    return {
        "available": True,
        "paired_condition_count": len(rows),
        "reference_rmst_seconds": reference,
        "optimized_rmst_seconds": optimized,
        "optimized_minus_reference_rmst_seconds": optimized - reference,
        "optimized_minus_reference_ci95": [
            _percentile(samples, 0.025),
            _percentile(samples, 0.975),
        ],
        "reference_completion_events": sum(
            row["reference_event"] for row in rows
        ),
        "optimized_completion_events": sum(
            row["optimized_event"] for row in rows
        ),
        "interval_method": "map-seed cluster percentile bootstrap",
    }


def _fidelity_secondary_analyses(
    sessions: list[dict[str, Any]],
    bootstrap_samples: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    binary_rows: list[dict[str, Any]] = []
    binary_incomplete: list[dict[str, Any]] = []
    time_rows: list[dict[str, Any]] = []
    time_incomplete: list[dict[str, Any]] = []
    ceilings: set[float] = set()
    for session in sessions:
        frozen_timestep = session["frozen_plan"]["timestep_seconds"]
        frozen_ceiling = (
            session["manifest"]["fixed_work_timesteps"] * frozen_timestep
        )
        ceilings.add(frozen_ceiling)
        pair_by_block = {
            pair["block_id"]: pair for pair in session["pairs"]
        }
        for block in session["manifest"]["blocks"]:
            base = {
                "session_id": session["session_id"],
                "pair_id": block["pair_id"],
                "environment": block["environment"],
                "seed": block["seed"],
                "robot_count": block["robot_count"],
            }
            pair = pair_by_block.get(block["block_id"])
            if pair is None:
                missing = {**base, "reason": "unexecuted-planned-block"}
                binary_incomplete.append(missing)
                time_incomplete.append(missing)
                continue
            if (
                not pair["host_block_valid"]
                or not pair["config_sha256_equal"]
            ):
                missing = {
                    **base,
                    "reason": "host-or-configuration-contamination",
                }
                binary_incomplete.append(missing)
                time_incomplete.append(missing)
                continue
            attempts = {
                method: session["by_attempt"][pair["attempt_ids"][method]]
                for method in METHODS
            }
            reference = attempts["reference"]
            _, map_sha, scenario_sha, _, _ = _condition_identity(reference)
            binary_rows.append(
                {
                    **base,
                    "map_sha256": map_sha,
                    "scenario_sha256": scenario_sha,
                    "reference": (
                        reference["terminal_category"] == "completed"
                        and reference["task_valid"] is True
                    ),
                    "optimized": (
                        attempts["optimized"]["terminal_category"]
                        == "completed"
                        and attempts["optimized"]["task_valid"] is True
                    ),
                }
            )
            if any(
                attempts[method]["terminal_category"] != "completed"
                for method in METHODS
            ):
                time_incomplete.append(
                    {**base, "reason": "process-failure-before-observation"}
                )
                continue
            observations: dict[str, dict[str, Any]] = {}
            for method in METHODS:
                attempt_id = attempts[method]["attempt_id"]
                summary = session["observer_summaries"].get(attempt_id)
                if summary is None:
                    raise AnalysisError(
                        f"completed fidelity attempt {attempt_id} has no summary"
                    )
                observations[method] = _restricted_time_observation(
                    summary,
                    f"summary {attempt_id}",
                    session["manifest"]["fixed_work_timesteps"],
                    frozen_timestep,
                )
            if (
                observations["reference"]["ceiling_seconds"]
                != observations["optimized"]["ceiling_seconds"]
            ):
                raise AnalysisError(
                    f"{block['block_id']} has unequal frozen time ceilings"
                )
            time_rows.append(
                {
                    **base,
                    "map_sha256": map_sha,
                    "scenario_sha256": scenario_sha,
                    "reference_time_seconds": observations["reference"][
                        "restricted_time_seconds"
                    ],
                    "optimized_time_seconds": observations["optimized"][
                        "restricted_time_seconds"
                    ],
                    "reference_event": observations["reference"]["event"],
                    "optimized_event": observations["optimized"]["event"],
                }
            )
    if len(ceilings) > 1:
        raise AnalysisError(
            "fidelity sessions use different frozen simulated-time ceilings"
        )
    binary = _binary_endpoint(
        binary_rows,
        binary_incomplete,
        "fidelity:task-validity",
        bootstrap_samples,
        not_applicable_reason=(
            "canonical same-input trace identity is decisive for paired "
            "task-validity equality"
            if not binary_incomplete
            and all(
                pair["semantic_trace_equal"] is True
                for session in sessions
                for pair in session["pairs"]
            )
            else None
        ),
    )
    environments = sorted({row["environment"] for row in time_rows})
    rmst_available = not time_incomplete and bool(time_rows)
    restricted = {
        "available": rmst_available,
        "endpoint": (
            "time to all robots reaching goals, censored at fixed horizon"
        ),
        "frozen_ceiling_seconds": (
            next(iter(ceilings)) if ceilings else None
        ),
        "incomplete_conditions": time_incomplete,
        "paired_observations": time_rows if rmst_available else [],
        "overall": (
            _restricted_mean_summary(
                time_rows, "fidelity:rmst:overall", bootstrap_samples
            )
            if rmst_available
            else {
                "available": False,
                "paired_condition_count": 0,
                "reason": "complete all-attempt RMST treatment is unavailable",
            }
        ),
        "by_environment": (
            {
                environment: _restricted_mean_summary(
                    [
                        row
                        for row in time_rows
                        if row["environment"] == environment
                    ],
                    f"fidelity:rmst:environment:{environment}",
                    bootstrap_samples,
                )
                for environment in environments
            }
            if rmst_available
            else {}
        ),
    }
    return binary, restricted


def _fidelity_analysis(
    sessions: list[dict[str, Any]],
    bootstrap_samples: int,
) -> dict[str, Any] | None:
    fidelity_sessions = [
        item for item in sessions if item["manifest"]["mode"] == "fidelity"
    ]
    if not fidelity_sessions:
        return None
    pairs = [
        pair for session in fidelity_sessions for pair in session["pairs"]
    ]
    environments = sorted({pair["environment"] for pair in pairs})
    task_validity, restricted_time = _fidelity_secondary_analyses(
        fidelity_sessions, bootstrap_samples
    )
    return {
        "overall": _fidelity_counts(pairs),
        "by_environment": {
            environment: _fidelity_counts(
                [pair for pair in pairs if pair["environment"] == environment]
            )
            for environment in environments
        },
        "paired_task_validity": task_validity,
        "restricted_mean_time_to_completion": restricted_time,
    }


def _fidelity_key(
    session: dict[str, Any],
    pair: dict[str, Any],
) -> tuple[Any, ...]:
    reference = session["by_attempt"][pair["attempt_ids"]["reference"]]
    provenance = reference["provenance"]
    return (
        pair["environment"],
        provenance["map_sha256"],
        provenance["scenario_sha256"],
        pair["seed"],
        pair["robot_count"],
    )


def _summary_quality_observation(
    summary: dict[str, Any],
    label: str,
    *,
    expected_robot_count: int,
    expected_final_tick: int,
) -> dict[str, Any]:
    required = {
        "schema_version",
        "trace_detail",
        "run_valid",
        "termination_reason",
        "final_tick",
        "final_robot_count",
        "maximum_robot_count",
        "minimum_pair_clearance_m",
        "minimum_obstacle_clearance_m",
        "pair_collision_exposure_seconds",
        "obstacle_collision_exposure_seconds",
        "pair_collision_events",
        "obstacle_collision_events",
        "nonfinite_state_observations",
        "nonfinite_numeric_observations",
        "invalid_robot_frames",
        "invalid_geometry_evaluations",
        "goals_total",
        "goals_reached",
        "all_goals_reached",
        "total_path_length_m",
        "mean_path_length_m_per_robot",
        "paper_metrics",
    }
    missing = sorted(required - summary.keys())
    if missing:
        return {
            "available": False,
            "reason": f"{label} missing fields {missing}",
        }
    if summary["schema_version"] != "dancers-comparison-summary-v1":
        return {
            "available": False,
            "reason": f"{label} has an unsupported summary schema",
        }
    try:
        trace_detail = _string(
            summary["trace_detail"], f"{label}.trace_detail"
        )
        run_valid = _boolean(summary["run_valid"], f"{label}.run_valid")
        termination_reason = _string(
            summary["termination_reason"], f"{label}.termination_reason"
        )
        final_tick = _integer(
            summary["final_tick"], f"{label}.final_tick"
        )
        final_robot_count = _integer(
            summary["final_robot_count"], f"{label}.final_robot_count"
        )
        maximum_robot_count = _integer(
            summary["maximum_robot_count"],
            f"{label}.maximum_robot_count",
        )
        minimum_pair_clearance = _finite(
            summary["minimum_pair_clearance_m"],
            f"{label}.minimum_pair_clearance_m",
            positive=True,
        )
        minimum_obstacle_clearance = _finite(
            summary["minimum_obstacle_clearance_m"],
            f"{label}.minimum_obstacle_clearance_m",
            positive=True,
        )
        pair_exposure = _finite(
            summary["pair_collision_exposure_seconds"],
            f"{label}.pair_collision_exposure_seconds",
        )
        obstacle_exposure = _finite(
            summary["obstacle_collision_exposure_seconds"],
            f"{label}.obstacle_collision_exposure_seconds",
        )
        pair_events = _integer(
            summary["pair_collision_events"],
            f"{label}.pair_collision_events",
        )
        obstacle_events = _integer(
            summary["obstacle_collision_events"],
            f"{label}.obstacle_collision_events",
        )
        invalid_counts = {
            field: _integer(summary[field], f"{label}.{field}")
            for field in (
                "nonfinite_state_observations",
                "nonfinite_numeric_observations",
                "invalid_robot_frames",
                "invalid_geometry_evaluations",
            )
        }
        goals_total = _integer(
            summary["goals_total"], f"{label}.goals_total"
        )
        goals_reached = _integer(
            summary["goals_reached"], f"{label}.goals_reached"
        )
        all_goals = _boolean(
            summary["all_goals_reached"], f"{label}.all_goals_reached"
        )
        total_path_length = _finite(
            summary["total_path_length_m"],
            f"{label}.total_path_length_m",
        )
        mean_path_length = _finite(
            summary["mean_path_length_m_per_robot"],
            f"{label}.mean_path_length_m_per_robot",
        )
        paper_metrics = _mapping(
            summary["paper_metrics"], f"{label}.paper_metrics"
        )
    except AnalysisError as exc:
        return {"available": False, "reason": str(exc)}
    assert pair_exposure is not None and obstacle_exposure is not None
    assert minimum_pair_clearance is not None
    assert minimum_obstacle_clearance is not None
    assert total_path_length is not None and mean_path_length is not None
    collision_and_state_valid = (
        run_valid
        and pair_exposure == 0.0
        and obstacle_exposure == 0.0
        and pair_events == 0
        and obstacle_events == 0
        and all(value == 0 for value in invalid_counts.values())
    )
    task_valid = (
        termination_reason == "maximum_time"
        and final_tick == expected_final_tick
        and final_robot_count == expected_robot_count
        and maximum_robot_count == expected_robot_count
        and collision_and_state_valid
    )
    return {
        "available": True,
        "trace_detail": trace_detail,
        "collision_and_state_valid": collision_and_state_valid,
        "task_validity_predicate_id": TASK_VALIDITY_PREDICATE_ID,
        "task_valid": task_valid,
        "run_valid": run_valid,
        "termination_reason": termination_reason,
        "final_tick": final_tick,
        "final_robot_count": final_robot_count,
        "maximum_robot_count": maximum_robot_count,
        "minimum_pair_clearance_m": minimum_pair_clearance,
        "minimum_obstacle_clearance_m": minimum_obstacle_clearance,
        "pair_collision_exposure_seconds": pair_exposure,
        "obstacle_collision_exposure_seconds": obstacle_exposure,
        "pair_collision_events": pair_events,
        "obstacle_collision_events": obstacle_events,
        "invalid_counts": invalid_counts,
        "goals_total": goals_total,
        "goals_reached": goals_reached,
        "all_goals_reached": all_goals,
        "total_path_length_m": total_path_length,
        "mean_path_length_m_per_robot": mean_path_length,
        "paper_metrics": paper_metrics,
    }


def _capacity_evidence_gates(
    session: dict[str, Any],
    sessions: list[dict[str, Any]],
    state: dict[str, Any],
    anchor: int,
    largest: int | None,
    fixed_budget_rows: list[dict[str, Any]],
    estimates: dict[str, Any],
    collapsed: list[dict[str, Any]],
) -> dict[str, Any]:
    phase = session["manifest"]["phase"]
    relevant_sessions = [
        item
        for item in sessions
        if item["manifest"]["phase"] == phase
        and any(
            pair["environment"] == state["environment"]
            for pair in item["pairs"]
        )
    ]
    no_early_stop = all(
        not item["session"]["stopped_early"] for item in relevant_sessions
    )
    required_seeds = PHASE_SEEDS[phase]
    observed_target_seeds = {
        row["seed"]
        for row in collapsed
        if row["session_id"] == session["session_id"]
        and largest is not None
        and row["robot_count"] == largest
    }
    seed_coverage = observed_target_seeds == required_seeds

    expected_keys: set[tuple[Any, ...]] = set()
    if largest is not None:
        for pair in session["pairs"]:
            if pair["robot_count"] != largest:
                continue
            reference = session["by_attempt"][pair["attempt_ids"]["reference"]]
            provenance = reference["provenance"]
            expected_keys.add(
                (
                    pair["environment"],
                    provenance["map_sha256"],
                    provenance["scenario_sha256"],
                    pair["seed"],
                    pair["robot_count"],
                )
            )

    fidelity_by_key: dict[
        tuple[Any, ...], list[tuple[dict[str, Any], dict[str, Any]]]
    ] = defaultdict(list)
    for fidelity_session in sessions:
        if (
            fidelity_session["manifest"]["mode"] != "fidelity"
            or fidelity_session["manifest"]["phase"] != phase
        ):
            continue
        for pair in fidelity_session["pairs"]:
            key = _fidelity_key(fidelity_session, pair)
            if key in expected_keys:
                fidelity_by_key[key].append((fidelity_session, pair))

    matched_keys = set(fidelity_by_key)
    exact_full = bool(expected_keys) and matched_keys == expected_keys
    quality_observations: list[dict[str, Any]] = []
    if exact_full:
        for key in sorted(expected_keys):
            entries = fidelity_by_key[key]
            if not entries:
                exact_full = False
                continue
            for fidelity_session, pair in entries:
                if (
                    not pair["pair_valid"]
                    or pair["semantic_trace_equal"] is not True
                ):
                    exact_full = False
                method_observations: dict[str, dict[str, Any]] = {}
                for method in METHODS:
                    attempt_id = pair["attempt_ids"][method]
                    raw_summary = fidelity_session["observer_summaries"].get(
                        attempt_id
                    )
                    if raw_summary is None:
                        observation = {
                            "available": False,
                            "reason": f"{attempt_id} has no observer summary",
                        }
                    else:
                        observation = _summary_quality_observation(
                            raw_summary,
                            f"summary {attempt_id}",
                            expected_robot_count=key[4],
                            expected_final_tick=fidelity_session["manifest"][
                                "fixed_work_timesteps"
                            ],
                        )
                    method_observations[method] = observation
                    if (
                        not observation.get("available")
                        or observation.get("trace_detail") != "digest"
                    ):
                        exact_full = False
                selected = {
                    name: method_observations["reference"].get(name)
                    for name in (
                        "goals_total",
                        "goals_reached",
                        "all_goals_reached",
                        "paper_metrics",
                    )
                }
                quality_observations.append(
                    {
                        "environment": key[0],
                        "map_sha256": key[1],
                        "scenario_sha256": key[2],
                        "seed": key[3],
                        "robot_count": key[4],
                        "methods": method_observations,
                        "task_metrics_identical": all(
                            method_observations["reference"].get(name)
                            == method_observations["optimized"].get(name)
                            for name in (
                                "goals_total",
                                "goals_reached",
                                "all_goals_reached",
                                "paper_metrics",
                            )
                        ),
                        "task_metrics": selected,
                        "secondary_progress": {
                            method: {
                                name: method_observations[method].get(name)
                                for name in (
                                    "goals_total",
                                    "goals_reached",
                                    "all_goals_reached",
                                    "total_path_length_m",
                                    "mean_path_length_m_per_robot",
                                )
                            }
                            for method in METHODS
                        },
                    }
                )

    quality_available = bool(quality_observations) and all(
        observation["methods"][method].get("available") is True
        for observation in quality_observations
        for method in METHODS
    )
    collision_pass = quality_available and all(
        observation["methods"][method].get("collision_and_state_valid") is True
        for observation in quality_observations
        for method in METHODS
    )
    task_pass = quality_available and all(
        observation["methods"][method].get("task_valid") is True
        for observation in quality_observations
        for method in METHODS
    )
    paper_metrics_identical = quality_available and all(
        observation["methods"]["reference"].get("paper_metrics")
        == observation["methods"]["optimized"].get("paper_metrics")
        for observation in quality_observations
    )

    cpu_metric = estimates["metrics"].get("total_cpu_seconds")
    wall_metric = estimates["metrics"].get("wall_seconds")
    budget_available = (
        cpu_metric is not None
        and wall_metric is not None
        and len(fixed_budget_rows) == len(required_seeds)
    )
    cpu_pass = (
        budget_available and cpu_metric["ratio_ci95"][1] <= 1.0
    )
    wall_pass = (
        budget_available and wall_metric["ratio_ci95"][1] <= 1.0
    )

    memory_limit = session["manifest"]["maximum_peak_rss_mib"]
    target_attempts = [
        run
        for run in session["runs"]
        if run["attempt_kind"] == "measured"
        and largest is not None
        and run["robot_count"] == largest
    ]
    target_rss_values = [
        run["resource"]["peak_rss_mib"]
        for run in target_attempts
        if run["resource"]["peak_rss_mib"] is not None
    ]
    all_target_rss_recorded = (
        bool(target_attempts)
        and len(target_rss_values) == len(target_attempts)
    )
    observed_peak_rss = (
        max(target_rss_values) if target_rss_values else None
    )
    memory_gate = (
        all_target_rss_recorded
        and observed_peak_rss is not None
        and observed_peak_rss <= memory_limit
    )
    larger_swarm = largest is not None and largest > anchor
    required_gate_values: list[bool | None] = [
        larger_swarm,
        exact_full,
        collision_pass,
        task_pass,
        paper_metrics_identical,
        cpu_pass,
        wall_pass,
        memory_gate,
        no_early_stop if phase == "validation" else False,
        seed_coverage if phase == "validation" else False,
    ]
    claim_ready = all(value is True for value in required_gate_values)
    return {
        "runner_capacity_state_interpretation": (
            "exploratory search bookkeeping only; it is not a validated "
            "capacity conclusion"
        ),
        "phase": phase,
        "claim_status": (
            "validated" if claim_ready else
            "calibration-provisional" if phase == "calibration" else
            "not-validated"
        ),
        "validated_capacity_claim_ready": claim_ready,
        "larger_swarm_gate": {
            "pass": larger_swarm,
            "reference_anchor_n": anchor,
            "candidate_n": largest,
        },
        "same_n_full_semantic_fidelity_gate": {
            "pass": exact_full,
            "expected_map_seed_conditions": len(expected_keys),
            "matched_map_seed_conditions": len(matched_keys),
            "accepted_trace_details": ["digest"],
        },
        "collision_and_invalid_state_gate": {
            "pass": collision_pass,
            "evidence_available": quality_available,
            "observations": quality_observations,
        },
        "task_validity_gate": {
            "pass": task_pass,
            "registered_criterion_available": True,
            "predicate_id": session["manifest"][
                "task_validity_predicate_id"
            ],
            "goals_and_path_progress_are_secondary": True,
        },
        "paper_metrics_identity_gate": {
            "pass": paper_metrics_identical,
        },
        "fixed_cpu_and_wall_budget_gate": {
            "pass": bool(cpu_pass and wall_pass),
            "cpu_pass": cpu_pass,
            "wall_pass": wall_pass,
            "complete_paired_seed_count": len(fixed_budget_rows),
            "required_seed_count": len(required_seeds),
        },
        "memory_safety_gate": {
            "pass": memory_gate,
            "frozen_limit_mib": memory_limit,
            "observed_peak_rss_mib": observed_peak_rss,
            "all_candidate_attempts_recorded_peak_rss": (
                all_target_rss_recorded
            ),
        },
        "validation_execution_gate": {
            "pass": (
                no_early_stop and seed_coverage
                if phase == "validation"
                else False
            ),
            "no_early_stop": no_early_stop,
            "required_seeds": sorted(required_seeds),
            "observed_candidate_seeds": sorted(observed_target_seeds),
            "complete_seed_coverage": seed_coverage,
            "reason": (
                None
                if phase == "validation" and no_early_stop and seed_coverage
                else "only complete non-early-stopped validation can support "
                "a capacity claim"
            ),
        },
    }


def _fixed_budget_rows(
    collapsed: list[dict[str, Any]],
    *,
    environment: str,
    anchor: int,
    candidate: int,
    session_ids: set[str],
) -> list[dict[str, Any]]:
    anchors: dict[tuple[str, int], dict[str, Any]] = {}
    candidates: dict[tuple[str, int], dict[str, Any]] = {}
    for row in collapsed:
        if (
            row["session_id"] not in session_ids
            or row["environment"] != environment
        ):
            continue
        key = (row["map_sha256"], row["seed"])
        if row["robot_count"] == anchor:
            if key in anchors:
                raise AnalysisError(f"duplicate capacity anchor row {key}")
            anchors[key] = row
        if row["robot_count"] == candidate:
            if key in candidates:
                raise AnalysisError(f"duplicate capacity candidate row {key}")
            candidates[key] = row
    rows: list[dict[str, Any]] = []
    for key in sorted(set(anchors) & set(candidates)):
        rows.append(
            {
                "environment": environment,
                "map_sha256": key[0],
                "seed": key[1],
                "reference_robot_count": anchor,
                "optimized_robot_count": candidate,
                "effects": _metric_values(
                    anchors[key]["methods"]["reference"],
                    candidates[key]["methods"]["optimized"],
                ),
            }
        )
    return rows


def _calibration_completed_wall_seconds(
    timing_session: dict[str, Any],
    sessions: list[dict[str, Any]],
    *,
    environment: str,
    grid: list[int],
) -> list[float]:
    expected_fidelity_conditions = {
        (seed, count)
        for seed in PHASE_SEEDS["calibration"]
        for count in grid
    }
    included = [timing_session]
    included_ids = {timing_session["session_id"]}
    for session in sessions:
        if session["session_id"] in included_ids:
            continue
        if (
            session["manifest"]["phase"] != "calibration"
            or session["manifest"]["mode"] != "fidelity"
        ):
            continue
        observed = [
            (pair["seed"], pair["robot_count"])
            for pair in session["pairs"]
        ]
        if (
            any(pair["environment"] != environment for pair in session["pairs"])
            or len(observed) != len(expected_fidelity_conditions)
            or set(observed) != expected_fidelity_conditions
        ):
            continue
        included.append(session)
        included_ids.add(session["session_id"])
    return sorted(
        float(run["resource"]["wall_seconds"])
        for session in included
        for run in session["runs"]
        if run["attempt_kind"] == "measured"
        and run["terminal_category"] == "completed"
        and run["environment"] == environment
        and run["robot_count"] in grid
    )


def _calibration_capacity_decision(
    session: dict[str, Any],
    sessions: list[dict[str, Any]],
    state: dict[str, Any],
    collapsed: list[dict[str, Any]],
    bootstrap_samples: int,
) -> dict[str, Any]:
    manifest_capacity = session["manifest"]["capacity_search"]
    assert manifest_capacity is not None
    environment = state["environment"]
    anchor = manifest_capacity["reference_anchor_n"]
    grid = list(manifest_capacity["grid"])
    per_grid: dict[str, Any] = {}
    for count in grid:
        rows = _fixed_budget_rows(
            collapsed,
            environment=environment,
            anchor=anchor,
            candidate=count,
            session_ids={session["session_id"]},
        )
        estimates = _effect_summary(
            rows,
            f"capacity-calibration:{environment}:{count}:{session['session_id']}",
            bootstrap_samples,
        )
        gates = _capacity_evidence_gates(
            session,
            sessions,
            state,
            anchor,
            count,
            rows,
            estimates,
            collapsed,
        )
        validation_gate = gates["validation_execution_gate"]
        values = {
            "cpu_budget_pass": gates["fixed_cpu_and_wall_budget_gate"][
                "cpu_pass"
            ],
            "wall_budget_pass": gates["fixed_cpu_and_wall_budget_gate"][
                "wall_pass"
            ],
            "memory_pass": gates["memory_safety_gate"]["pass"],
            "same_n_fidelity_pass": gates[
                "same_n_full_semantic_fidelity_gate"
            ]["pass"],
            "paper_metrics_pass": gates["paper_metrics_identity_gate"]["pass"],
            "summary_parity_pass": gates[
                "same_n_full_semantic_fidelity_gate"
            ]["pass"],
            "task_validity_pass": gates["task_validity_gate"]["pass"],
            "collision_state_pass": gates[
                "collision_and_invalid_state_gate"
            ]["pass"],
            "complete_seed_coverage": validation_gate[
                "complete_seed_coverage"
            ],
        }
        values["all_gates_pass"] = (
            not session["session"]["stopped_early"]
            and all(value is True for value in values.values())
        )
        per_grid[str(count)] = values
    selected, next_point = _select_calibration_candidate(
        grid, anchor, per_grid
    )
    wall_seconds = _calibration_completed_wall_seconds(
        session,
        sessions,
        environment=environment,
        grid=grid,
    )
    return {
        "reference_anchor_n": anchor,
        "grid": grid,
        "per_grid": per_grid,
        "calibration_passing_n": selected,
        "next_grid_point": next_point,
        "completed_measured_wall_seconds": wall_seconds,
    }


def _select_calibration_candidate(
    grid: list[int],
    anchor: int,
    per_grid: dict[str, dict[str, Any]],
) -> tuple[int | None, int | None]:
    passing = [
        count
        for count in grid
        if per_grid[str(count)]["all_gates_pass"]
    ]
    selected = max(passing) if passing else None
    next_point = (
        grid[grid.index(selected) + 1]
        if selected is not None and grid.index(selected) + 1 < len(grid)
        else None
    )
    return selected, next_point


def _capacity_phase_outcomes(
    phase: str,
    complete_evidence_without_phase_gate: bool,
) -> dict[str, bool]:
    if phase not in {"validation", "audit"}:
        raise AnalysisError("capacity evaluation phase must be validation/audit")
    validation_phase_gate = phase == "validation"
    return {
        "validation_phase_gate": validation_phase_gate,
        "complete_evidence_without_phase_gate": (
            complete_evidence_without_phase_gate
        ),
        "audit_reproduction_ready": (
            phase == "audit" and complete_evidence_without_phase_gate
        ),
        "validated_capacity_claim_ready": (
            validation_phase_gate
            and complete_evidence_without_phase_gate
        ),
    }


def _capacity_evaluation_record(
    environment: str,
    evaluation_sessions: list[dict[str, Any]],
    sessions: list[dict[str, Any]],
    collapsed: list[dict[str, Any]],
    bootstrap_samples: int,
) -> dict[str, Any]:
    records = [
        session["frozen_plan"]["capacity_evaluation"]
        for session in evaluation_sessions
    ]
    role_sessions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for session, record in zip(evaluation_sessions, records):
        role_sessions[record["role"]].append(session)
    required_roles = {"timing-grid", "fidelity-grid"}
    duplicate_roles = sorted(
        role
        for role, grouped in role_sessions.items()
        if len(grouped) > 1
    )
    if duplicate_roles:
        raise AnalysisError(
            f"capacity evaluation {environment} has duplicate role sessions: "
            f"{duplicate_roles}"
        )
    missing_roles = sorted(required_roles - set(role_sessions))
    complete_role_coverage = not missing_roles
    role_coverage_reason = (
        None
        if complete_role_coverage
        else "missing required capacity-evaluation roles: "
        + ", ".join(missing_roles)
    )
    canonical = {
        key: records[0][key]
        for key in CAPACITY_EVALUATION_KEYS
        if key != "role"
    }
    for record in records[1:]:
        _assert_equal(
            {key: record[key] for key in canonical},
            canonical,
            f"capacity evaluation agreement for {environment}",
        )
    decision_hashes = {
        session["frozen_plan"]["artifacts"]["capacity_decision"]["sha256"]
        for session in evaluation_sessions
    }
    if len(decision_hashes) != 1:
        raise AnalysisError(
            f"capacity evaluation {environment} uses different decisions"
        )
    anchor = canonical["reference_anchor_n"]
    candidate = canonical["candidate_n_star"]
    timing_sessions = [
        session
        for session in evaluation_sessions
        if session["manifest"]["mode"] == "timing"
    ]
    timing_ids = {session["session_id"] for session in timing_sessions}
    rows = _fixed_budget_rows(
        collapsed,
        environment=environment,
        anchor=anchor,
        candidate=candidate,
        session_ids=timing_ids,
    )
    estimates = _effect_summary(
        rows,
        f"capacity-evaluation:{environment}:{candidate}",
        bootstrap_samples,
    )
    required_seeds = PHASE_SEEDS[evaluation_sessions[0]["manifest"]["phase"]]
    observed_seeds = {row["seed"] for row in rows}
    complete_seed_coverage = observed_seeds == required_seeds
    cpu = estimates["metrics"].get("total_cpu_seconds")
    wall = estimates["metrics"].get("wall_seconds")
    cpu_pass = bool(cpu and cpu["ratio_ci95"][1] <= 1.0)
    wall_pass = bool(wall and wall["ratio_ci95"][1] <= 1.0)
    no_early_stop = all(
        not session["session"]["stopped_early"]
        for session in evaluation_sessions
    )

    target_attempts = [
        run
        for session in timing_sessions
        for run in session["runs"]
        if run["attempt_kind"] == "measured"
        and run["environment"] == environment
        and run["robot_count"] == candidate
        and run["method"] == "optimized"
    ]
    memory_pass = bool(target_attempts) and all(
        run["terminal_category"] == "completed"
        and run["resource"]["peak_rss_mib"] is not None
        and run["resource"]["peak_rss_mib"]
        <= run["resource"]["maximum_peak_rss_mib"]
        for run in target_attempts
    )

    expected_keys = {
        (
            pair["environment"],
            timing["by_attempt"][pair["attempt_ids"]["optimized"]][
                "provenance"
            ]["map_sha256"],
            timing["by_attempt"][pair["attempt_ids"]["optimized"]][
                "provenance"
            ]["scenario_sha256"],
            pair["seed"],
            pair["robot_count"],
        )
        for timing in timing_sessions
        for pair in timing["pairs"]
        if pair["environment"] == environment
        and pair["robot_count"] == candidate
    }
    fidelity_pairs: dict[
        tuple[Any, ...], list[tuple[dict[str, Any], dict[str, Any]]]
    ] = defaultdict(list)
    for fidelity in evaluation_sessions:
        if fidelity["manifest"]["mode"] != "fidelity":
            continue
        for pair in fidelity["pairs"]:
            key = _fidelity_key(fidelity, pair)
            if key in expected_keys:
                fidelity_pairs[key].append((fidelity, pair))
    matched = set(fidelity_pairs)
    fidelity_pass = bool(expected_keys) and matched == expected_keys and all(
        pair["semantic_trace_equal"] is True and pair["pair_valid"]
        for entries in fidelity_pairs.values()
        for _, pair in entries
    )
    expected_full_grid_conditions = {
        (seed, count)
        for seed in required_seeds
        for count in canonical["grid"]
    }
    full_grid_entries = [
        (fidelity, pair)
        for fidelity in evaluation_sessions
        if fidelity["manifest"]["mode"] == "fidelity"
        for pair in fidelity["pairs"]
        if pair["environment"] == environment
    ]
    observed_full_grid_conditions = [
        (pair["seed"], pair["robot_count"])
        for _, pair in full_grid_entries
    ]
    full_grid_coverage = (
        complete_role_coverage
        and len(observed_full_grid_conditions)
        == len(expected_full_grid_conditions)
        and set(observed_full_grid_conditions)
        == expected_full_grid_conditions
    )
    full_grid_fidelity_pass = full_grid_coverage and all(
        pair["semantic_trace_equal"] is True and pair["pair_valid"]
        for _, pair in full_grid_entries
    )
    paper_pass = full_grid_coverage and all(
        pair["paper_metrics_equal"] is True
        for _, pair in full_grid_entries
    )
    summary_pass = full_grid_coverage and all(
        pair["summary_parity_fields_equal"] is True
        for _, pair in full_grid_entries
    )
    task_pass = full_grid_coverage and all(
        pair["task_valid"] is True
        for _, pair in full_grid_entries
    )
    quality: list[dict[str, Any]] = []
    for fidelity, pair in full_grid_entries:
        for method in METHODS:
            attempt_id = pair["attempt_ids"][method]
            summary = fidelity["observer_summaries"].get(attempt_id)
            if summary is None:
                continue
            quality.append(
                _summary_quality_observation(
                    summary,
                    f"summary {attempt_id}",
                    expected_robot_count=pair["robot_count"],
                    expected_final_tick=fidelity["manifest"][
                        "fixed_work_timesteps"
                    ],
                )
            )
    collision_pass = bool(quality) and all(
        item.get("available") is True
        and item.get("collision_and_state_valid") is True
        for item in quality
    ) and len(quality) == 2 * len(full_grid_entries)
    complete_evidence = all(
        value is True
        for value in (
            candidate > anchor,
            complete_role_coverage,
            complete_seed_coverage,
            no_early_stop,
            cpu_pass,
            wall_pass,
            memory_pass,
            fidelity_pass,
            full_grid_fidelity_pass,
            paper_pass,
            summary_pass,
            task_pass,
            collision_pass,
        )
    )
    phase = evaluation_sessions[0]["manifest"]["phase"]
    phase_outcomes = _capacity_phase_outcomes(phase, complete_evidence)
    return {
        "environment": environment,
        "phase": phase,
        "capacity_decision_sha256": next(iter(decision_hashes)),
        "reference_anchor_n": anchor,
        "candidate_n_star": candidate,
        "next_grid_point": canonical["next_grid_point"],
        "grid": canonical["grid"],
        "fixed_budget_estimates": estimates,
        "evidence_gates": {
            "larger_swarm_pass": candidate > anchor,
            "complete_role_coverage": complete_role_coverage,
            "role_coverage_reason": role_coverage_reason,
            "role_session_ids": {
                role: grouped[0]["session_id"]
                for role, grouped in sorted(role_sessions.items())
            },
            "complete_phase_seed_coverage": complete_seed_coverage,
            "observed_seeds": sorted(observed_seeds),
            "required_seeds": sorted(required_seeds),
            "no_early_stop": no_early_stop,
            "cpu_budget_pass": cpu_pass,
            "wall_budget_pass": wall_pass,
            "memory_pass": memory_pass,
            "same_n_star_fidelity_pass": fidelity_pass,
            "full_grid_fidelity_pass": full_grid_fidelity_pass,
            "full_grid_condition_coverage": full_grid_coverage,
            "full_grid_discordant_or_invalid_pair_ids": sorted(
                pair["block_id"]
                for _, pair in full_grid_entries
                if (
                    pair["semantic_trace_equal"] is not True
                    or pair["pair_valid"] is not True
                    or pair["paper_metrics_equal"] is not True
                    or pair["summary_parity_fields_equal"] is not True
                    or pair["task_valid"] is not True
                )
            ),
            "paper_metrics_pass": paper_pass,
            "summary_parity_pass": summary_pass,
            "task_validity_pass": task_pass,
            "collision_state_pass": collision_pass,
            **phase_outcomes,
        },
    }


def _capacity_analysis(
    sessions: list[dict[str, Any]],
    collapsed: list[dict[str, Any]],
    bootstrap_samples: int,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    calibration_decisions: dict[str, Any] = {}
    for session in sessions:
        state = session["capacity"]
        if state is None:
            continue
        manifest_capacity = session["manifest"]["capacity_search"]
        assert manifest_capacity is not None
        anchor = manifest_capacity["reference_anchor_n"]
        largest = state["provisional_largest_compute_passing_n"]
        fixed_budget_rows: list[dict[str, Any]] = []
        if largest is not None:
            anchor_rows: dict[tuple[str, int], dict[str, Any]] = {}
            target_rows: dict[tuple[str, int], dict[str, Any]] = {}
            for row in collapsed:
                if row["session_id"] != session["session_id"]:
                    continue
                if row["robot_count"] not in {anchor, largest}:
                    continue
                destination = (
                    anchor_rows
                    if row["robot_count"] == anchor
                    else target_rows
                )
                key = (row["map_sha256"], row["seed"])
                if key in destination:
                    raise AnalysisError(
                        "capacity pairing is ambiguous for "
                        f"environment={state['environment']} "
                        f"map={key[0]} seed={key[1]} "
                        f"N={row['robot_count']}"
                    )
                destination[key] = row
            if largest == anchor:
                target_rows = dict(anchor_rows)
            for key in sorted(set(anchor_rows) & set(target_rows)):
                reference_row = anchor_rows[key]
                optimized_row = target_rows[key]
                reference = reference_row["methods"]["reference"]
                optimized = optimized_row["methods"]["optimized"]
                fixed_budget_rows.append(
                    {
                        "environment": state["environment"],
                        "map_sha256": key[0],
                        "seed": key[1],
                        "reference_robot_count": anchor,
                        "optimized_robot_count": largest,
                        "effects": _metric_values(reference, optimized),
                    }
                )
        estimates = _effect_summary(
            fixed_budget_rows,
            f"capacity:{state['environment']}:{session['session_id']}",
            bootstrap_samples,
        )
        gates = _capacity_evidence_gates(
            session,
            sessions,
            state,
            anchor,
            largest,
            fixed_budget_rows,
            estimates,
            collapsed,
        )
        records.append(
            {
                "session_id": session["session_id"],
                "environment": state["environment"],
                "reference_anchor_n": anchor,
                "grid": state["grid"],
                "largest_passing_n": largest,
                "first_failing_n": state[
                    "first_provisional_compute_failing_n"
                ],
                "capacity_ratio": (
                    largest / anchor if largest is not None else None
                ),
                "stop_reason": state["stop_reason"],
                "unexecuted_grid_points": state["unexecuted_grid_points"],
                "runner_budget": state["budget"],
                "runner_evaluations": state["evaluations"],
                "fixed_budget_estimates": estimates,
                "evidence_gates": gates,
            }
        )
        if session["manifest"]["phase"] == "calibration":
            if state["environment"] in calibration_decisions:
                raise AnalysisError(
                    "duplicate calibration capacity decision for "
                    f"{state['environment']}"
                )
            calibration_decisions[state["environment"]] = (
                _calibration_capacity_decision(
                    session,
                    sessions,
                    state,
                    collapsed,
                    bootstrap_samples,
                )
            )
    evaluation_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for session in sessions:
        evaluation = session["frozen_plan"]["capacity_evaluation"]
        if evaluation is not None:
            evaluation_groups[evaluation["environment"]].append(session)
    evaluations = {
        environment: _capacity_evaluation_record(
            environment,
            grouped_sessions,
            sessions,
            collapsed,
            bootstrap_samples,
        )
        for environment, grouped_sessions in sorted(evaluation_groups.items())
    }
    by_environment: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_environment[record["environment"]].append(record)
    return {
        "session_count": len(records),
        "sessions": records,
        "calibration_decisions": calibration_decisions,
        "capacity_evaluations": evaluations,
        "by_environment": {
            key: value for key, value in sorted(by_environment.items())
        },
    }


def _failure_ledger(sessions: list[dict[str, Any]]) -> dict[str, Any]:
    attempt_failures: list[dict[str, Any]] = []
    pair_failures: list[dict[str, Any]] = []
    warmup_blocks: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for session in sessions:
        for run in session["runs"]:
            if run["attempt_kind"] == "warmup":
                warmup_blocks[(session["session_id"], run["block_id"])].append(run)
            if run["terminal_category"] != "completed":
                attempt_failures.append(
                    {
                        "session_id": session["session_id"],
                        "attempt_id": run["attempt_id"],
                        "block_id": run["block_id"],
                        "pair_id": run["pair_id"],
                        "attempt_kind": run["attempt_kind"],
                        "phase": run["phase"],
                        "mode": run["mode"],
                        "environment": run["environment"],
                        "seed": run["seed"],
                        "robot_count": run["robot_count"],
                        "method": run["method"],
                        "terminal_category": run["terminal_category"],
                        "orchestration_error": run["orchestration_error"],
                        "resource": run["resource"],
                        "config_sha256": run["provenance"]["config_sha256"],
                        "map_sha256": run["provenance"]["map_sha256"],
                        "scenario_sha256": run["provenance"][
                            "scenario_sha256"
                        ],
                    }
                )
        for pair in session["pairs"]:
            if not pair["pair_valid"]:
                pair_failures.append(
                    {
                        "session_id": session["session_id"],
                        "block_id": pair["block_id"],
                        "pair_id": pair["pair_id"],
                        "phase": pair["phase"],
                        "mode": pair["mode"],
                        "environment": pair["environment"],
                        "seed": pair["seed"],
                        "robot_count": pair["robot_count"],
                        "terminal_categories": pair["terminal_categories"],
                        "config_sha256_equal": pair["config_sha256_equal"],
                        "semantic_trace_equal": pair["semantic_trace_equal"],
                        "mismatch": pair["mismatch"],
                    }
                )
    warmup_failures: list[dict[str, Any]] = []
    for (session_id, block_id), attempts in sorted(warmup_blocks.items()):
        configs = {
            item["provenance"]["config_sha256"] for item in attempts
        }
        valid = len(configs) == 1 and all(
            item["terminal_category"] == "completed" for item in attempts
        )
        if not valid:
            warmup_failures.append(
                {
                    "session_id": session_id,
                    "block_id": block_id,
                    "config_sha256_equal": len(configs) == 1,
                    "terminal_categories": {
                        item["method"]: item["terminal_category"]
                        for item in attempts
                    },
                }
            )
    total_runs = sum(len(session["runs"]) for session in sessions)
    warmup_runs = sum(
        run["attempt_kind"] == "warmup"
        for session in sessions
        for run in session["runs"]
    )
    measured_runs = total_runs - warmup_runs
    total_pairs = sum(len(session["pairs"]) for session in sessions)
    return {
        "all_attempt_denominator": total_runs,
        "warmup_attempt_count": warmup_runs,
        "measured_attempt_count": measured_runs,
        "measured_pair_block_count": total_pairs,
        "failed_attempt_count": len(attempt_failures),
        "invalid_pair_block_count": len(pair_failures),
        "invalid_warmup_block_count": len(warmup_failures),
        "attempt_failures": attempt_failures,
        "pair_failures": pair_failures,
        "warmup_block_failures": warmup_failures,
    }


def _validate_cross_session_provenance(
    sessions: list[dict[str, Any]],
    analysis_sha256: str,
) -> dict[str, Any]:
    phases = {session["manifest"]["phase"] for session in sessions}
    if len(phases) != 1:
        raise AnalysisError(
            "calibration, validation, and audit phases must be analyzed separately"
        )
    timesteps = {
        float(session["manifest"]["timestep_seconds"])
        for session in sessions
    }
    if timesteps != {0.1}:
        raise AnalysisError("sessions use incompatible timestep durations")
    cooldowns = {
        session["manifest"]["idle_cooldown_seconds"] for session in sessions
    }
    if cooldowns != {300}:
        raise AnalysisError("sessions use incompatible idle cooldowns")
    task_predicates = {
        session["manifest"]["task_validity_predicate_id"]
        for session in sessions
    }
    if task_predicates != {TASK_VALIDITY_PREDICATE_ID}:
        raise AnalysisError("sessions use incompatible task-validity predicates")
    memory_limits = {
        float(session["manifest"]["maximum_peak_rss_mib"])
        for session in sessions
    }
    if memory_limits != {MAXIMUM_PEAK_RSS_MIB}:
        raise AnalysisError("sessions use incompatible peak-RSS safety limits")
    disk_limits = {
        session["manifest"]["minimum_free_disk_bytes"]
        for session in sessions
    }
    if disk_limits != {MINIMUM_FREE_DISK_BYTES}:
        raise AnalysisError("sessions use incompatible free-disk safety limits")
    power_modes = {
        session["manifest"]["power_mode"] for session in sessions
    }
    if power_modes != {POWER_MODE}:
        raise AnalysisError("sessions use incompatible power modes")
    thermal_policies = {
        session["manifest"]["thermal_policy"] for session in sessions
    }
    if thermal_policies != {THERMAL_POLICY}:
        raise AnalysisError("sessions use incompatible thermal policies")
    consensus: dict[str, str] = {}
    method_identities: dict[str, dict[str, str]] = {}
    for session in sessions:
        for key, value in session["global_provenance"].items():
            if key in consensus:
                _assert_equal(value, consensus[key], f"cross-session {key}")
            else:
                consensus[key] = value
        for key, value in session["method_provenance"].items():
            if key in method_identities:
                _assert_equal(
                    value,
                    method_identities[key],
                    f"cross-session method identity {key}",
                )
            else:
                method_identities[key] = value
        for artifact_name in ("environment_manifest", "config_template"):
            key = f"{artifact_name}_sha256"
            value = session["frozen_plan"]["artifacts"][artifact_name][
                "sha256"
            ]
            if key in consensus:
                _assert_equal(
                    value, consensus[key], f"cross-session {key}"
                )
            else:
                consensus[key] = value
        if session["frozen_plan"]["capacity_evaluation"] is not None:
            key = "capacity_decision_sha256"
            value = session["frozen_plan"]["artifacts"][
                "capacity_decision"
            ]["sha256"]
            if key in consensus:
                _assert_equal(
                    value, consensus[key], f"cross-session {key}"
                )
            else:
                consensus[key] = value
    _assert_equal(
        consensus["analysis_revision_sha256"],
        analysis_sha256,
        "analysis revision provenance",
    )
    return {
        "phase": next(iter(phases)),
        "task_validity_predicate_id": next(iter(task_predicates)),
        "timestep_seconds": next(iter(timesteps)),
        "idle_cooldown_seconds": next(iter(cooldowns)),
        "maximum_peak_rss_mib": next(iter(memory_limits)),
        "minimum_free_disk_bytes": next(iter(disk_limits)),
        "power_mode": next(iter(power_modes)),
        "thermal_policy": next(iter(thermal_policies)),
        "shared_artifacts": consensus,
        "method_builds": method_identities,
    }


def analyze_sessions(
    session_paths: Sequence[Path | str],
    *,
    bootstrap_samples: int = MIN_BOOTSTRAP_SAMPLES,
) -> dict[str, Any]:
    if (
        not isinstance(bootstrap_samples, int)
        or isinstance(bootstrap_samples, bool)
        or bootstrap_samples < MIN_BOOTSTRAP_SAMPLES
    ):
        raise AnalysisError(
            f"bootstrap_samples must be an integer >= {MIN_BOOTSTRAP_SAMPLES}"
        )
    if not session_paths:
        raise AnalysisError("at least one session directory is required")
    roots = [Path(path).resolve() for path in session_paths]
    if len(roots) != len(set(roots)):
        raise AnalysisError("the same session directory was provided twice")
    analysis_path = Path(__file__).resolve()
    analysis_sha256 = sha256_file(analysis_path)
    sessions = [
        _validate_session(path, analysis_sha256) for path in roots
    ]
    provenance = _validate_cross_session_provenance(
        sessions, analysis_sha256
    )
    timing = _timing_analysis(sessions, bootstrap_samples)
    fidelity = _fidelity_analysis(sessions, bootstrap_samples)
    collapsed = (
        timing["collapsed_conditions"] if timing is not None else []
    )
    result = {
        "schema_version": ANALYSIS_SCHEMA,
        "analysis_revision_sha256": analysis_sha256,
        "bootstrap": {
            "method": (
                "map-seed cluster percentile bootstrap; clusters sampled "
                "with replacement; repetitions first collapsed to medians"
            ),
            "frozen_seed": FROZEN_BOOTSTRAP_SEED,
            "samples": bootstrap_samples,
            "confidence_level": 0.95,
        },
        "input": {
            "phase": provenance["phase"],
            "task_validity_predicate_id": provenance[
                "task_validity_predicate_id"
            ],
            "timestep_seconds": provenance["timestep_seconds"],
            "idle_cooldown_seconds": provenance["idle_cooldown_seconds"],
            "maximum_peak_rss_mib": provenance["maximum_peak_rss_mib"],
            "minimum_free_disk_bytes": provenance[
                "minimum_free_disk_bytes"
            ],
            "power_mode": provenance["power_mode"],
            "thermal_policy": provenance["thermal_policy"],
            "session_count": len(sessions),
            "sessions": [
                {
                    "session_id": session["session_id"],
                    "path": session["path"],
                    "mode": session["manifest"]["mode"],
                    "hashes": session["hashes"],
                }
                for session in sessions
            ],
            "shared_artifacts": provenance["shared_artifacts"],
            "method_builds": provenance["method_builds"],
        },
        "failure_ledger": _failure_ledger(sessions),
        "timing": timing,
        "fidelity": fidelity,
        "capacity": _capacity_analysis(
            sessions, collapsed, bootstrap_samples
        ),
    }
    # This is an explicit final guard against accidental NaN/Infinity output.
    canonical_json_bytes(result)
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--session",
        action="append",
        type=Path,
        required=True,
        help="complete frozen runner session directory; repeat as needed",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="new JSON output path (existing paths are rejected)",
    )
    parser.add_argument(
        "--bootstrap-samples",
        type=int,
        default=MIN_BOOTSTRAP_SAMPLES,
        help=f"cluster bootstrap draws (minimum {MIN_BOOTSTRAP_SAMPLES})",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        print(f"analysis error: output already exists: {output}", file=sys.stderr)
        return 2
    try:
        result = analyze_sessions(
            args.session,
            bootstrap_samples=args.bootstrap_samples,
        )
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as handle:
            handle.write(canonical_json_bytes(result))
            handle.flush()
    except (AnalysisError, OSError) as exc:
        print(f"analysis error: {exc}", file=sys.stderr)
        return 2
    print(
        f"wrote {output} "
        f"(sha256={sha256_file(output)}, "
        f"bootstrap_samples={args.bootstrap_samples})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
