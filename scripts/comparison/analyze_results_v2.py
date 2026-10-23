#!/usr/bin/env python3
"""Fail-closed analysis for the amended fractal-high comparison epoch.

This module is deliberately versioned separately from ``analyze_results.py``.
The historical analyzer remains the authority for historical v1 sessions.
This analyzer reuses its trace-v3 reader and numerical conventions, adds the
amended v2 power/process contracts, and implements the registered cross-count
timing screen without allowing partial-cell estimates.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import random
import re
import stat
import statistics
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts.comparison import analyze_results as _v1
    from scripts.comparison import run_paired as _runner_v1
    from scripts.comparison import trace_compare as _trace
except ModuleNotFoundError:  # Direct execution from scripts/comparison.
    import analyze_results as _v1
    import run_paired as _runner_v1
    import trace_compare as _trace


ANALYSIS_SCHEMA = "dancers-comparison-analysis-v2"
PLAN_SCHEMA = "dancers-laptop-comparison-plan-v2"
SESSION_SCHEMA = "dancers-laptop-comparison-session-v2"
RUN_SCHEMA = "dancers-laptop-comparison-attempt-v2"
PAIR_SCHEMA = "dancers-laptop-comparison-pair-v2"
FIDELITY_GATE_RECEIPT_SCHEMA = "dancers-amended-fidelity-gate-v1"
ORCHESTRATION_ABORT_SCHEMA = "dancers-orchestration-abort-v2"

AMENDMENT_ID = "fh-lower-count-fidelity-2026-07-28-v1"
FIDELITY_DESIGN_ROLE = "outcome-informed-exploratory-fidelity"
TIMING_DESIGN_ROLE = "exploratory-screening-only"
POWER_POLICY_ID = "darwin-ac-battery-stable-v1"
TASK_VALIDITY_PREDICATE_ID = "fixed-horizon-safe-navigation-v1"
COHORT_IDS = {
    "fidelity": "fractal-high-lower-count-fidelity-amended-v1",
    "timing": "fractal-high-lower-count-timing-amended-v1",
}
RESULT_ROOT_NAMES = {
    "fidelity": "fractal-high-lower-count-fidelity",
    "timing": "fractal-high-lower-count-timing",
}

METHODS = ("reference", "optimized")
COUNTS = (192, 256)
SEEDS = tuple(range(1, 11))
TIMING_REPETITIONS = (1, 2, 3)
FIDELITY_REPETITIONS = (1,)
REFERENCE_COUNT = 192
OPTIMIZED_COUNT = 256
FIXED_WORK_TIMESTEPS = 100
TIMESTEP_SECONDS = 0.1
TIMEOUT_SECONDS = 300.0
MAXIMUM_PEAK_RSS_MIB = 49_152.0
MINIMUM_FREE_DISK_BYTES = 21_474_836_480

BOOTSTRAP_DRAWS = 10_000
BOOTSTRAP_SEED = 250_818_153
BOOTSTRAP_CONFIDENCE_LEVEL = 0.95
POST_OUTCOME_AMENDMENT_SHA256 = (
    "cb9dcd355648102cac35e06d0e2e6575f5ae6a60c9319690c2373efffc3ce7cb"
)
HISTORICAL_DEPENDENCY_SHA256 = {
    "analyzer_v1_base": (
        "8587bd3f6fcb4f8150534d222a52b5704deb4882d088a236429745e406c5e047"
    ),
    "trace_compare": (
        "9d18dfd18032a3af2fcf83ac918671a0ba4616d073f01e50848945950a63d8e7"
    ),
    "runner_v1_base": (
        "28c185189b756498c9a1030f80b092504d3e4a19f5c09fdfadc2860cb4a69fb2"
    ),
    "freeze_capacity_decision": (
        "451f0287232a9318f73b139b65f42daab9d97b016f836e6775f4e74b5f5eeccc"
    ),
}

POWER_MODE = "AC Power"
THERMAL_POLICY = _v1.THERMAL_POLICY
EXPECTED_POWER_POLICY: dict[str, Any] = {
    "allowed_statuses": ["charging", "charged", "finishing charge"],
    "battery_record_count": 1,
    "comparison_rules": {
        "absolute_floor_overrides_drop_tolerance": True,
        "attempt_boundary": "after_level >= before_level - 1",
        "cooldown": "after_level >= before_level - 1",
        "session": "level >= post_cooldown_baseline - 2",
    },
    "line_parsing": {
        "additional_battery_lines_rejected": True,
        "additional_source_lines_rejected": True,
        "battery_line_count": 1,
        "battery_record_name_prefix": "InternalBattery",
        "line_endings_normalized": True,
        "malformed_trailing_fields_rejected": True,
        "source_line_count": 1,
        "status_normalization": "trim, collapse ASCII whitespace, lowercase",
        "unknown_required_fields_rejected": True,
    },
    "maximum_attempt_boundary_drop_percentage_points": 1,
    "maximum_battery_percent": 100,
    "maximum_cooldown_drop_percentage_points": 1,
    "maximum_session_drop_percentage_points": 2,
    "minimum_battery_percent": 80,
    "policy_id": POWER_POLICY_ID,
    "power_source": POWER_MODE,
    "present_required": True,
    "probe_during_timed_process": False,
    "probe_points": [
        "preflight",
        "cooldown-complete",
        "before-each-method-attempt",
        "after-each-method-attempt",
        "session-complete",
    ],
    "session_drop_baseline": "cooldown-complete",
    "unknown_or_ambiguous_fails_closed": True,
}
EXPECTED_PROCESS_CONTROL_POLICY: dict[str, Any] = {
    "already_exited_process_lookup_is_anomaly": False,
    "anomaly_terminal": "measurement-wrapper-failure",
    "group_eperm_direct_child_fallback": True,
    "initial_group_eperm_remains_anomaly": True,
    "post_measurement_artifact_failure_terminal": (
        "measurement-wrapper-failure"
    ),
    "process_group_empty_before_finalize": True,
    "reap_before_result_or_hash": True,
    "retain_resource_when_wait4_succeeds": True,
    "retry_interrupted_waits": True,
    "unrecoverable_control_action": "abort-session-unsealed",
}

HOST_SNAPSHOT_V2_KEYS = {
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
    "power_policy_id",
    "power_policy_evaluation",
}
POWER_PROBE_V2_KEYS = {
    "command",
    "returncode",
    "stdout",
    "stderr",
    "source",
    "battery_record",
    "parse_error",
    "valid",
}
BATTERY_RECORD_KEYS = {"name", "id", "percent", "status", "present"}
POWER_POLICY_EVALUATION_KEYS = {
    "stage",
    "cooldown_baseline_percent",
    "attempt_boundary_before_percent",
    "drop_from_cooldown_baseline_percentage_points",
    "drop_from_attempt_before_percentage_points",
    "valid",
    "violations",
}
PROCESS_CONTROL_KEYS = {
    "schema_version",
    "process_started",
    "timed_out",
    "child_reaped",
    "process_group_empty_after_reap",
    "wait_interrupted_count",
    "anomaly",
    "events",
    "valid",
}
PROCESS_CONTROL_EVENT_KEYS = {
    "stage",
    "operation",
    "signal",
    "outcome",
    "error",
}
RUN_V2_KEYS = set(_v1.RUN_KEYS) | {
    "host_conditions_before_attempt",
    "host_conditions_after_attempt",
    "host_attempt_valid",
    "process_control",
}
PAIR_V2_KEYS = set(_v1.PAIR_KEYS) | {"attempt_host_validity"}
SESSION_V2_KEYS = set(_v1.SESSION_KEYS) | {
    "cohort_id",
    "result_root",
    "host_abort",
    "power_policy",
    "process_control_policy",
    "session_completion_host_conditions",
    "session_power_valid",
    "planned_method_attempt_slots",
    "planned_pair_blocks",
    "executed_method_attempt_slots",
    "completed_method_attempt_slots",
    "valid_method_attempt_slots",
    "executed_pair_blocks",
    "valid_pair_blocks",
}
MANIFEST_V2_KEYS = set(_v1.MANIFEST_KEYS) | {
    "cohort_id",
    "result_root",
    "amendment_id",
    "design_role",
    "power_policy",
    "process_control_policy",
    "planned_method_attempt_slots",
    "planned_pair_blocks",
    "analyzer_sha256",
}
PLAN_V2_KEYS = {
    "schema_version",
    "amendment_id",
    "design_role",
    "cohort_id",
    "result_root",
    "phase",
    "mode",
    "fixed_work_timesteps",
    "timestep_seconds",
    "idle_cooldown_seconds",
    "timeout_seconds",
    "termination_grace_seconds",
    "minimum_free_disk_bytes",
    "maximum_peak_rss_mib",
    "power_mode",
    "power_policy",
    "process_control_policy",
    "thermal_policy",
    "task_validity_predicate_id",
    "repetitions",
    "warmups_per_binary_map",
    "omp_threads",
    "serial_execution",
    "order_seed",
    "runner_sha256",
    "analyzer_sha256",
    "trace_detail",
    "run_cwd",
    "planned_method_attempt_slots",
    "planned_pair_blocks",
    "artifacts",
    "methods",
    "pairs",
}

TIMING_OBSERVATION_KEYS = {
    "attempt_id",
    "method",
    "robot_count",
    "seed",
    "repetition",
    "terminal_category",
    "host_valid",
    "process_control_valid",
    "total_cpu_seconds",
    "wall_seconds",
    "peak_rss_mib",
}

FIDELITY_ATTEMPT_KEYS = {
    "attempt_id",
    "terminal_category",
    "host_valid",
    "process_control_valid",
    "task_valid",
    "resource_valid",
    "artifacts_valid",
}

FIDELITY_PAIR_KEYS = {
    "pair_id",
    "host_valid",
    "pair_valid",
    "frames_compared",
    "config_sha256_equal",
    "semantic_trace_equal",
    "paper_metrics_equal",
    "summary_parity_fields_equal",
    "task_valid",
}
PREFLIGHT_V2_KEYS = {
    "schema_version",
    "verified_utc",
    "plan_path",
    "plan_sha256",
    "phase",
    "mode",
    "artifacts",
    "methods",
    "pairs",
    "environment_registry",
    "runner_sha256",
    "build_attestation",
    "host_conditions",
    "idle_cooldown",
    "resource_controls",
    "power_policy",
    "process_control_policy",
    "amendment_id",
}
SUMMARY_PARITY_FIELDS = (
    "termination_reason",
    "all_goals_reached",
    "goals_reached",
    "goals_total",
    "first_all_goals_tick",
    "iterations",
    "total_path_length_m",
    "mean_path_length_m_per_robot",
    "per_robot_goal_tick",
    "per_robot_path_length_m",
    "obstacle_collision_events",
    "obstacle_collision_exposure_seconds",
    "pair_collision_events",
    "pair_collision_exposure_seconds",
    "minimum_obstacle_clearance_m",
    "minimum_pair_clearance_m",
    "invalid_geometry_evaluations",
    "invalid_robot_frames",
    "nonfinite_numeric_observations",
    "nonfinite_state_observations",
)
TASK_VALIDITY_FIELDS = (
    "termination_reason",
    "final_tick",
    "final_robot_count",
    "maximum_robot_count",
    "run_valid",
    "invalid_geometry_evaluations",
    "invalid_robot_frames",
    "nonfinite_numeric_observations",
    "nonfinite_state_observations",
    "obstacle_collision_events",
    "obstacle_collision_exposure_seconds",
    "pair_collision_events",
    "pair_collision_exposure_seconds",
)
OBSERVER_MARKERS = (
    "DANCERS_TRACE_OUT",
    _v1.TRACE_SCHEMA,
    _v1.TRACE_DIGEST_STATE_SCHEMA,
    _v1.TRACE_DIGEST_ALGORITHM,
)
_SOURCE_RE = re.compile(r"^Now drawing from '([^']+)'$")
_BATTERY_RE = re.compile(
    r"^[ \t]*-(?P<name>InternalBattery[A-Za-z0-9_-]*)[ \t]+"
    r"\(id=(?P<id>[0-9]+)\)[ \t]+"
    r"(?P<percent>[0-9]{1,3})%[ \t]*;[ \t]*"
    r"(?P<status>[^;\r\n]+?)[ \t]*; "
    r"(?P<trailing>[^;\r\n]+?)$"
)
_PRESENT_RE = re.compile(
    r"^(?:[0-9]+:[0-9]{2} remaining|\(no estimate\)) "
    r"present: (true|false)$"
)


class AnalysisError(RuntimeError):
    """The amended records cannot support a trustworthy analysis."""


@dataclass(frozen=True)
class _EvidenceSnapshot:
    """One immutable private copy made from a no-follow stable descriptor."""

    source_path: Path
    private_path: Path
    sha256: str
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int


class _EvidenceGuard:
    """Keep authenticated evidence copies and detect publication-time swaps."""

    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(
            prefix="dancers-analysis-evidence-"
        )
        self._snapshots: list[_EvidenceSnapshot] = []
        self.trace_paths: dict[str, Path] = {}
        self._inventory_root: Path | None = None
        self._inventory: (
            dict[str, tuple[str, int, int, int, int, int]] | None
        ) = None
        self._closed = False

    @staticmethod
    def _open_no_follow(path: Path, label: str) -> int:
        no_follow = getattr(os, "O_NOFOLLOW", None)
        if no_follow is None:
            raise AnalysisError(
                f"{label} cannot be authenticated: O_NOFOLLOW is unavailable"
            )
        try:
            return os.open(path, os.O_RDONLY | no_follow)
        except OSError as exc:
            raise AnalysisError(
                f"{label} cannot be opened without following links: {exc}"
            ) from exc

    @staticmethod
    def _identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            int(info.st_dev),
            int(info.st_ino),
            int(info.st_size),
            int(info.st_mtime_ns),
            int(info.st_ctime_ns),
        )

    def capture(
        self,
        path: Path,
        label: str,
        *,
        maximum_bytes: int | None = None,
    ) -> _EvidenceSnapshot:
        """Hash and copy exactly the bytes read from one stable descriptor."""

        if self._closed:
            raise AnalysisError("evidence guard is already closed")
        descriptor = self._open_no_follow(path, label)
        private_path = (
            Path(self._temporary.name)
            / f"{len(self._snapshots):06d}.evidence"
        )
        digest = hashlib.sha256()
        copied = 0
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise AnalysisError(f"{label} is not a regular file")
            if maximum_bytes is not None and before.st_size > maximum_bytes:
                raise AnalysisError(
                    f"{label} exceeds the {maximum_bytes}-byte evidence limit"
                )
            with private_path.open("xb") as private:
                while True:
                    block = os.read(descriptor, 1024 * 1024)
                    if not block:
                        break
                    copied += len(block)
                    if maximum_bytes is not None and copied > maximum_bytes:
                        raise AnalysisError(
                            f"{label} exceeds the "
                            f"{maximum_bytes}-byte evidence limit"
                        )
                    digest.update(block)
                    private.write(block)
                private.flush()
                os.fsync(private.fileno())
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
        if self._identity(before) != self._identity(after):
            raise AnalysisError(f"{label} changed while it was snapshotted")
        if copied != before.st_size:
            raise AnalysisError(
                f"{label} byte count changed while it was snapshotted"
            )
        private_path.chmod(0o400)
        snapshot = _EvidenceSnapshot(
            source_path=path,
            private_path=private_path,
            sha256=digest.hexdigest(),
            device=int(before.st_dev),
            inode=int(before.st_ino),
            size=int(before.st_size),
            mtime_ns=int(before.st_mtime_ns),
            ctime_ns=int(before.st_ctime_ns),
        )
        self._snapshots.append(snapshot)
        return snapshot

    def bind_inventory(
        self,
        root: Path,
        inventory: Mapping[str, tuple[str, int, int, int, int, int]],
    ) -> None:
        self._inventory_root = root
        self._inventory = dict(inventory)

    def reverify(self) -> None:
        """Reject any path replacement or byte change before publication."""

        if self._closed:
            raise AnalysisError("evidence guard is already closed")
        for snapshot in self._snapshots:
            descriptor = self._open_no_follow(
                snapshot.source_path,
                f"publication evidence {snapshot.source_path}",
            )
            digest = hashlib.sha256()
            try:
                before = os.fstat(descriptor)
                if not stat.S_ISREG(before.st_mode):
                    raise AnalysisError(
                        f"publication evidence is no longer regular: "
                        f"{snapshot.source_path}"
                    )
                while True:
                    block = os.read(descriptor, 1024 * 1024)
                    if not block:
                        break
                    digest.update(block)
                after = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            observed = self._identity(before)
            expected = (
                snapshot.device,
                snapshot.inode,
                snapshot.size,
                snapshot.mtime_ns,
                snapshot.ctime_ns,
            )
            if observed != expected or self._identity(after) != expected:
                raise AnalysisError(
                    f"publication evidence identity changed: "
                    f"{snapshot.source_path}"
                )
            if digest.hexdigest() != snapshot.sha256:
                raise AnalysisError(
                    f"publication evidence hash changed: "
                    f"{snapshot.source_path}"
                )
        if self._inventory_root is not None and self._inventory is not None:
            observed_inventory = _scan_session_tree(self._inventory_root)
            if observed_inventory != self._inventory:
                raise AnalysisError(
                    "session tree changed after inventory validation"
                )

    def close(self) -> None:
        if not self._closed:
            self._temporary.cleanup()
            self._closed = True

    def __del__(self) -> None:
        self.close()


def canonical_json_bytes(value: Any) -> bytes:
    """Return the repository's canonical strict-JSON representation."""

    try:
        return _v1.canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise AnalysisError(f"result is not strict JSON: {exc}") from exc


def sha256_file(path: Path) -> str:
    return _v1.sha256_file(path)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AnalysisError(f"{label} must be an object")
    return value


def _exact_keys(
    record: Mapping[str, Any], expected: set[str], label: str
) -> None:
    actual = set(record)
    if actual != expected:
        raise AnalysisError(
            f"{label} keys mismatch: missing={sorted(expected - actual)} "
            f"unknown={sorted(actual - expected)}"
        )


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise AnalysisError(f"{label} must be boolean")
    return value


def _integer(
    value: Any,
    label: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise AnalysisError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise AnalysisError(f"{label} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise AnalysisError(f"{label} must be <= {maximum}")
    return value


def _positive_finite(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise AnalysisError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise AnalysisError(f"{label} must be finite and positive")
    return result


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AnalysisError(f"{label} must be a non-empty string")
    return value


def _nullable_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return _string(value, label)


def _nullable_boolean(value: Any, label: str) -> bool | None:
    if value is None:
        return None
    return _boolean(value, label)


def _nullable_integer(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return _integer(value, label)


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise AnalysisError(f"{label} must be an array")
    return [
        _string(item, f"{label}[{index}]")
        for index, item in enumerate(value)
    ]


def _assert_equal(actual: Any, expected: Any, label: str) -> None:
    scalar_types = (type(None), bool, int, float, str)
    scalar_type_mismatch = (
        isinstance(actual, scalar_types)
        and isinstance(expected, scalar_types)
        and type(actual) is not type(expected)
    )
    if scalar_type_mismatch or actual != expected:
        raise AnalysisError(
            f"{label} is {actual!r}; expected {expected!r}"
        )


def _assert_exact_json_scalar(
    actual: Any,
    expected: bool | int | float | str | None,
    label: str,
) -> None:
    """Compare JSON scalars without Python's ``True == 1`` coercion."""

    if type(actual) is not type(expected) or actual != expected:
        raise AnalysisError(
            f"{label} is {actual!r} ({type(actual).__name__}); expected "
            f"{expected!r} ({type(expected).__name__})"
        )


def _assert_exact_json_value(actual: Any, expected: Any, label: str) -> None:
    """Compare arbitrary strict-JSON values with type preservation."""

    if canonical_json_bytes(actual) != canonical_json_bytes(expected):
        raise AnalysisError(f"{label} does not exactly match its binding")


def validate_power_policy(value: Any, label: str = "power_policy") -> None:
    """Require the byte-level semantic policy registered by the amendment."""

    policy = _mapping(value, label)
    _assert_exact_json_value(policy, EXPECTED_POWER_POLICY, label)


def validate_process_control_policy(
    value: Any,
    label: str = "process_control_policy",
) -> None:
    """Require the exact amended reaping and process-group policy."""

    policy = _mapping(value, label)
    _assert_exact_json_value(policy, EXPECTED_PROCESS_CONTROL_POLICY, label)


def _validate_battery_record(
    value: Any,
    label: str,
) -> dict[str, Any] | None:
    if value is None:
        return None
    record = _mapping(value, label)
    _exact_keys(record, BATTERY_RECORD_KEYS, label)
    name = _string(record["name"], f"{label}.name")
    if not name.startswith("InternalBattery"):
        raise AnalysisError(f"{label}.name has an unregistered prefix")
    battery_id = _integer(record["id"], f"{label}.id", minimum=0)
    percent = _integer(
        record["percent"],
        f"{label}.percent",
        minimum=0,
        maximum=100,
    )
    status = _string(record["status"], f"{label}.status")
    if status != " ".join(status.strip().lower().split()):
        raise AnalysisError(f"{label}.status is not normalized")
    present = _boolean(record["present"], f"{label}.present")
    return {
        "name": name,
        "id": battery_id,
        "percent": percent,
        "status": status,
        "present": present,
    }


def _validate_power_policy_evaluation(
    value: Any,
    label: str,
    *,
    observed_percent: int | None,
) -> dict[str, Any]:
    evaluation = _mapping(value, label)
    _exact_keys(evaluation, POWER_POLICY_EVALUATION_KEYS, label)
    stage = _string(evaluation["stage"], f"{label}.stage")
    allowed_stages = {
        "preflight",
        "cooldown-complete",
        "block-boundary",
        "before-attempt",
        "after-attempt",
        "session-complete",
    }
    if stage not in allowed_stages:
        raise AnalysisError(f"{label}.stage is invalid")
    baseline = _nullable_integer(
        evaluation["cooldown_baseline_percent"],
        f"{label}.cooldown_baseline_percent",
    )
    attempt_before = _nullable_integer(
        evaluation["attempt_boundary_before_percent"],
        f"{label}.attempt_boundary_before_percent",
    )
    baseline_drop = _nullable_integer(
        evaluation["drop_from_cooldown_baseline_percentage_points"],
        f"{label}.drop_from_cooldown_baseline_percentage_points",
    )
    attempt_drop = _nullable_integer(
        evaluation["drop_from_attempt_before_percentage_points"],
        f"{label}.drop_from_attempt_before_percentage_points",
    )
    for field_name, percentage in (
        ("cooldown_baseline_percent", baseline),
        ("attempt_boundary_before_percent", attempt_before),
    ):
        if percentage is not None and not 0 <= percentage <= 100:
            raise AnalysisError(f"{label}.{field_name} is outside 0..100")
    if observed_percent is None:
        if baseline_drop is not None or attempt_drop is not None:
            raise AnalysisError(
                f"{label} has a drop without an observed battery percentage"
            )
    else:
        expected_baseline_drop = (
            None if baseline is None else baseline - observed_percent
        )
        expected_attempt_drop = (
            None
            if attempt_before is None
            else attempt_before - observed_percent
        )
        _assert_equal(
            baseline_drop,
            expected_baseline_drop,
            f"{label}.drop_from_cooldown_baseline_percentage_points",
        )
        _assert_equal(
            attempt_drop,
            expected_attempt_drop,
            f"{label}.drop_from_attempt_before_percentage_points",
        )
    violations = _string_list(
        evaluation["violations"], f"{label}.violations"
    )
    valid = _boolean(evaluation["valid"], f"{label}.valid")
    later_stage = stage in {
        "before-attempt",
        "after-attempt",
        "block-boundary",
        "session-complete",
    }
    expected_valid = bool(
        observed_percent is not None
        and observed_percent >= EXPECTED_POWER_POLICY["minimum_battery_percent"]
        and (
            not later_stage
            or (
                baseline_drop is not None
                and baseline_drop
                <= EXPECTED_POWER_POLICY[
                    "maximum_session_drop_percentage_points"
                ]
            )
        )
        and (
            stage != "cooldown-complete"
            or (
                attempt_drop is not None
                and attempt_drop
                <= EXPECTED_POWER_POLICY[
                    "maximum_cooldown_drop_percentage_points"
                ]
            )
        )
        and (
            stage != "after-attempt"
            or (
                attempt_drop is not None
                and attempt_drop
                <= EXPECTED_POWER_POLICY[
                    "maximum_attempt_boundary_drop_percentage_points"
                ]
            )
        )
        and not violations
    )
    _assert_equal(valid, expected_valid, f"{label}.valid")
    return evaluation


def _reparse_darwin_battery_probe(
    *,
    returncode: int | None,
    stdout: str,
    stderr: str,
) -> dict[str, Any]:
    """Independently reproduce the frozen anchored ``pmset`` grammar."""

    record: dict[str, Any] = {
        "command": ["pmset", "-g", "batt"],
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "source": None,
        "battery_record": None,
        "parse_error": None,
        "valid": False,
    }
    if returncode != 0:
        record["parse_error"] = "pmset-returncode-not-zero"
        return record
    normalized = stdout.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) != 2 or any(line == "" for line in lines):
        record["parse_error"] = "expected-exactly-two-complete-lines"
        return record
    source_matches = [
        match
        for line in lines
        if (match := _SOURCE_RE.fullmatch(line)) is not None
    ]
    battery_matches = [
        match
        for line in lines
        if (match := _BATTERY_RE.fullmatch(line)) is not None
    ]
    if len(source_matches) != 1:
        record["parse_error"] = "expected-exactly-one-source-line"
        return record
    if len(battery_matches) != 1:
        record["parse_error"] = "expected-exactly-one-battery-line"
        return record
    source = source_matches[0].group(1)
    battery_match = battery_matches[0]
    raw_status = battery_match.group("status")
    if any(
        character.isspace() and character not in " \t"
        for character in raw_status
    ):
        record["parse_error"] = "status-contains-non-ascii-whitespace"
        return record
    status = re.sub(r"[ \t]+", " ", raw_status.strip(" \t")).lower()
    trailing = battery_match.group("trailing")
    if trailing != trailing.strip(" \t"):
        record["parse_error"] = "trailing-field-has-surrounding-whitespace"
        return record
    if any(
        character.isspace() and character not in " \t"
        for character in trailing
    ):
        record["parse_error"] = "trailing-field-has-non-ascii-whitespace"
        return record
    present_match = _PRESENT_RE.fullmatch(trailing)
    if present_match is None:
        record["parse_error"] = "malformed-present-trailing-field"
        return record
    percent = int(battery_match.group("percent"))
    present = present_match.group(1) == "true"
    record["source"] = source
    record["battery_record"] = {
        "name": battery_match.group("name"),
        "id": int(battery_match.group("id")),
        "percent": percent,
        "status": status,
        "present": present,
    }
    violations: list[str] = []
    if source != POWER_MODE:
        violations.append("power-source-not-ac")
    if not (
        EXPECTED_POWER_POLICY["minimum_battery_percent"]
        <= percent
        <= EXPECTED_POWER_POLICY["maximum_battery_percent"]
    ):
        violations.append("battery-percent-outside-80-100")
    if status not in EXPECTED_POWER_POLICY["allowed_statuses"]:
        violations.append("battery-status-not-allowed")
    if not present:
        violations.append("battery-not-present")
    if violations:
        record["parse_error"] = ",".join(violations)
        return record
    record["valid"] = True
    return record


def _validate_power_probe(value: Any, label: str) -> dict[str, Any]:
    power = _mapping(value, label)
    _exact_keys(power, POWER_PROBE_V2_KEYS, label)
    _assert_equal(
        power["command"],
        ["pmset", "-g", "batt"],
        f"{label}.command",
    )
    returncode = power["returncode"]
    if returncode is not None:
        _integer(returncode, f"{label}.returncode")
    for field in ("stdout", "stderr"):
        if not isinstance(power[field], str):
            raise AnalysisError(f"{label}.{field} must be a string")

    # Exceptional snapshots are emitted only when sampling itself raised.
    exceptional = bool(
        returncode is None
        and power["stdout"] == ""
        and power["source"] is None
        and power["battery_record"] is None
        and power["parse_error"] == "host-snapshot-exception"
        and power["valid"] is False
    )
    if exceptional:
        _string(power["stderr"], f"{label}.stderr")
        return power

    reparsed = _reparse_darwin_battery_probe(
        returncode=returncode,
        stdout=power["stdout"],
        stderr=power["stderr"],
    )
    _assert_equal(power, reparsed, f"{label} parsed record")
    return power


def validate_host_snapshot(
    value: Any,
    label: str = "host snapshot",
) -> dict[str, Any]:
    """Validate and independently recompute one amended host snapshot."""

    snapshot = _mapping(value, label)
    _exact_keys(snapshot, HOST_SNAPSHOT_V2_KEYS, label)
    _string(snapshot["checked_utc"], f"{label}.checked_utc")
    _string(snapshot["filesystem_path"], f"{label}.filesystem_path")
    free_disk = _integer(
        snapshot["free_disk_bytes"],
        f"{label}.free_disk_bytes",
        minimum=0,
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
        snapshot["power_policy_id"],
        POWER_POLICY_ID,
        f"{label}.power_policy_id",
    )
    _assert_equal(
        snapshot["thermal_policy"],
        THERMAL_POLICY,
        f"{label}.thermal_policy",
    )

    power = _validate_power_probe(
        snapshot["power_probe"], f"{label}.power_probe"
    )
    returncode = power["returncode"]
    source = power["source"]
    battery = _validate_battery_record(
        power["battery_record"], f"{label}.power_probe.battery_record"
    )
    parse_error = power["parse_error"]
    if parse_error is not None:
        _string(parse_error, f"{label}.power_probe.parse_error")
    probe_valid = _boolean(power["valid"], f"{label}.power_probe.valid")
    expected_probe_valid = bool(
        returncode == 0
        and source == POWER_MODE
        and battery is not None
        and battery["present"] is True
        and battery["status"] in EXPECTED_POWER_POLICY["allowed_statuses"]
        and parse_error is None
    )
    _assert_equal(
        probe_valid,
        expected_probe_valid,
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
    if not isinstance(thermal["command"], list):
        raise AnalysisError(f"{label}.thermal_probe.command must be an array")
    for index, item in enumerate(thermal["command"]):
        _string(item, f"{label}.thermal_probe.command[{index}]")
    _string(
        thermal["module_cache_path"],
        f"{label}.thermal_probe.module_cache_path",
    )
    thermal_returncode = thermal["returncode"]
    if thermal_returncode is not None:
        _integer(
            thermal_returncode,
            f"{label}.thermal_probe.returncode",
        )
    for field in ("stdout", "stderr"):
        if not isinstance(thermal[field], str):
            raise AnalysisError(
                f"{label}.thermal_probe.{field} must be a string"
            )
    thermal_state = thermal["state"]
    if thermal_state is not None:
        _integer(thermal_state, f"{label}.thermal_probe.state")
    thermal_valid = _boolean(
        thermal["valid"], f"{label}.thermal_probe.valid"
    )
    _assert_equal(
        thermal_valid,
        thermal_returncode == 0 and thermal_state == 0,
        f"{label}.thermal_probe.valid",
    )

    evaluation = _validate_power_policy_evaluation(
        snapshot["power_policy_evaluation"],
        f"{label}.power_policy_evaluation",
        observed_percent=None if battery is None else int(battery["percent"]),
    )
    valid = _boolean(snapshot["valid"], f"{label}.valid")
    _assert_equal(
        valid,
        bool(
            disk_valid
            and probe_valid
            and thermal_valid
            and evaluation["valid"]
        ),
        f"{label}.valid",
    )
    return snapshot


def host_attempt_valid(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
) -> bool:
    """Recompute attempt validity, including the one-point battery rule."""

    before_battery = before["power_probe"]["battery_record"]
    after_battery = after["power_probe"]["battery_record"]
    if before_battery is None or after_battery is None:
        return False
    after_evaluation = after["power_policy_evaluation"]
    return bool(
        before["valid"]
        and after["valid"]
        and before["power_probe"]["source"] == POWER_MODE
        and after["power_probe"]["source"] == POWER_MODE
        and before["thermal_probe"]["state"]
        == after["thermal_probe"]["state"]
        == 0
        and after_battery["percent"] >= before_battery["percent"] - 1
        and after_evaluation["attempt_boundary_before_percent"]
        == before_battery["percent"]
        and after_evaluation[
            "drop_from_attempt_before_percentage_points"
        ]
        == before_battery["percent"] - after_battery["percent"]
    )


def bind_cooldown_baseline(
    snapshot: Mapping[str, Any],
    cooldown_baseline_percent: int,
    label: str,
) -> None:
    """Bind a post-cooldown sample to the one immutable session baseline."""

    battery = snapshot["power_probe"]["battery_record"]
    evaluation = snapshot["power_policy_evaluation"]
    _assert_equal(
        evaluation["cooldown_baseline_percent"],
        cooldown_baseline_percent,
        f"{label}.power_policy_evaluation.cooldown_baseline_percent",
    )
    if battery is None:
        _assert_equal(
            evaluation["drop_from_cooldown_baseline_percentage_points"],
            None,
            (
                f"{label}.power_policy_evaluation."
                "drop_from_cooldown_baseline_percentage_points"
            ),
        )
        return
    _assert_equal(
        evaluation["drop_from_cooldown_baseline_percentage_points"],
        cooldown_baseline_percent - int(battery["percent"]),
        (
            f"{label}.power_policy_evaluation."
            "drop_from_cooldown_baseline_percentage_points"
        ),
    )


def validate_host_abort(
    value: Any,
    label: str = "host_abort",
) -> dict[str, Any] | None:
    if value is None:
        return None
    record = _mapping(value, label)
    _exact_keys(
        record,
        {
            "stage",
            "attempt_id",
            "block_id",
            "method",
            "order_position",
            "failed_snapshot",
            "probe_error",
            "reason",
        },
        label,
    )
    stage = _string(record["stage"], f"{label}.stage")
    if stage not in {
        "block-boundary",
        "before-attempt",
        "after-attempt",
        "orchestration",
    }:
        raise AnalysisError(f"{label}.stage is invalid")
    for field in ("attempt_id", "block_id", "method", "reason"):
        _string(record[field], f"{label}.{field}")
    if record["method"] not in METHODS:
        raise AnalysisError(f"{label}.method is invalid")
    _integer(
        record["order_position"],
        f"{label}.order_position",
        minimum=1,
        maximum=2,
    )
    snapshot_value = record["failed_snapshot"]
    probe_error = _nullable_string(
        record["probe_error"], f"{label}.probe_error"
    )
    if (snapshot_value is None) == (probe_error is None):
        raise AnalysisError(
            f"{label} must contain exactly one of failed_snapshot/probe_error"
        )
    if stage == "orchestration":
        expected_prefix = "post-containment orchestration failure:"
        if (
            snapshot_value is not None
            or probe_error is None
            or not probe_error.startswith(expected_prefix)
            or record["reason"] != probe_error
        ):
            raise AnalysisError(
                f"{label} orchestration failure evidence is invalid"
            )
    if snapshot_value is not None:
        snapshot = validate_host_snapshot(
            snapshot_value, f"{label}.failed_snapshot"
        )
        _assert_equal(
            snapshot["power_policy_evaluation"]["stage"],
            stage,
            f"{label}.failed_snapshot stage",
        )
        if snapshot["valid"] is not False:
            raise AnalysisError(f"{label}.failed_snapshot must be invalid")
    return record


def validate_orchestration_abort(
    value: Any,
    host_abort: Mapping[str, Any],
    label: str = "orchestration_abort.json",
) -> dict[str, Any]:
    record = _mapping(value, label)
    _exact_keys(
        record,
        {
            "schema_version",
            "stage",
            "error",
            "process_started",
            "attempt_id",
            "block_id",
            "method",
            "order_position",
            "completed_utc",
        },
        label,
    )
    _assert_equal(
        record["schema_version"],
        ORCHESTRATION_ABORT_SCHEMA,
        f"{label}.schema_version",
    )
    _assert_equal(record["stage"], "pre-first-attempt", f"{label}.stage")
    _assert_equal(
        record["process_started"],
        False,
        f"{label}.process_started",
    )
    _string(record["completed_utc"], f"{label}.completed_utc")
    _assert_equal(
        record["error"],
        host_abort["probe_error"],
        f"{label}.error",
    )
    for field in (
        "attempt_id",
        "block_id",
        "method",
        "order_position",
    ):
        _assert_equal(record[field], host_abort[field], f"{label}.{field}")
    return record


def validate_nonzero_orchestration_abort(
    value: Any,
    last_run: Mapping[str, Any],
    label: str = "orchestration_abort.json",
) -> dict[str, Any]:
    """Bind the generic sealer's nonzero diagnostic to the ledger suffix."""

    record = _mapping(value, label)
    _exact_keys(
        record,
        {
            "schema_version",
            "error",
            "last_attempt_id",
            "completed_utc",
        },
        label,
    )
    _assert_equal(
        record["schema_version"],
        "dancers-orchestration-abort-v1",
        f"{label}.schema_version",
    )
    error = _string(record["error"], f"{label}.error")
    if not error.startswith("post-containment orchestration failure:"):
        raise AnalysisError(f"{label}.error lacks the registered prefix")
    _assert_equal(
        record["last_attempt_id"],
        last_run["attempt_id"],
        f"{label}.last_attempt_id",
    )
    _string(record["completed_utc"], f"{label}.completed_utc")
    run_error = last_run.get("orchestration_error")
    if not isinstance(run_error, str) or (
        run_error != error
        and not run_error.startswith(f"{error}; prior attempt error: ")
    ):
        raise AnalysisError(
            f"{label}.error is not the terminal run's sealer failure"
        )
    return record


def validate_process_control(
    value: Any,
    label: str = "process_control",
) -> dict[str, Any]:
    """Validate v2 wait/reap/process-group evidence independently."""

    record = _mapping(value, label)
    _exact_keys(record, PROCESS_CONTROL_KEYS, label)
    _assert_equal(
        record["schema_version"],
        "dancers-process-control-v1",
        f"{label}.schema_version",
    )
    process_started = _boolean(
        record["process_started"], f"{label}.process_started"
    )
    timed_out = _nullable_boolean(record["timed_out"], f"{label}.timed_out")
    child_reaped = _boolean(record["child_reaped"], f"{label}.child_reaped")
    group_empty = _nullable_boolean(
        record["process_group_empty_after_reap"],
        f"{label}.process_group_empty_after_reap",
    )
    _integer(
        record["wait_interrupted_count"],
        f"{label}.wait_interrupted_count",
        minimum=0,
    )
    anomaly = _boolean(record["anomaly"], f"{label}.anomaly")
    events = record["events"]
    if not isinstance(events, list):
        raise AnalysisError(f"{label}.events must be an array")
    allowed_stages = {
        "wait-before-timeout",
        "sigterm",
        "wait-after-sigterm",
        "sigkill",
        "wait-after-sigkill",
        "group-empty-check",
    }
    allowed_operations = {"wait4", "killpg", "kill"}
    allowed_outcomes = {
        "reaped",
        "not-ready",
        "interrupted",
        "sent",
        "not-found",
        "eperm",
        "error",
        "empty",
        "members-remain",
    }
    interrupted_events = 0
    for index, raw_event in enumerate(events):
        event_label = f"{label}.events[{index}]"
        event = _mapping(raw_event, event_label)
        _exact_keys(event, PROCESS_CONTROL_EVENT_KEYS, event_label)
        stage = _string(event["stage"], f"{event_label}.stage")
        operation = _string(event["operation"], f"{event_label}.operation")
        outcome = _string(event["outcome"], f"{event_label}.outcome")
        if stage not in allowed_stages:
            raise AnalysisError(f"{event_label}.stage is invalid")
        if operation not in allowed_operations:
            raise AnalysisError(f"{event_label}.operation is invalid")
        if outcome not in allowed_outcomes:
            raise AnalysisError(f"{event_label}.outcome is invalid")
        _nullable_string(event["signal"], f"{event_label}.signal")
        _nullable_string(event["error"], f"{event_label}.error")
        if outcome in {"error", "eperm", "interrupted"} and event["error"] is None:
            raise AnalysisError(f"{event_label}.error is required")
        if (
            outcome not in {"error", "eperm", "interrupted"}
            and event["error"] is not None
        ):
            raise AnalysisError(f"{event_label}.error must be null")
        interrupted_events += (
            outcome == "interrupted" and operation == "wait4"
        )
    _assert_equal(
        record["wait_interrupted_count"],
        interrupted_events,
        f"{label}.wait_interrupted_count",
    )
    valid = _boolean(record["valid"], f"{label}.valid")
    if not process_started:
        if (
            timed_out is not None
            or group_empty is not None
            or child_reaped
            or events
        ):
            raise AnalysisError(
                f"{label} has terminal process state without a process"
            )
        _assert_equal(anomaly, True, f"{label}.anomaly")
        _assert_equal(valid, False, f"{label}.valid")
        return record
    if timed_out is None:
        raise AnalysisError(f"{label}.timed_out is required after start")

    cursor = 0
    sequence_anomaly = False

    def consume_wait(stage: str) -> bool:
        nonlocal cursor
        while cursor < len(events):
            event = events[cursor]
            if event["stage"] != stage:
                break
            if (
                event["operation"] != "wait4"
                or event["signal"] is not None
                or event["outcome"]
                not in {"interrupted", "not-ready", "reaped"}
            ):
                raise AnalysisError(
                    f"{label}.events[{cursor}] is not a legal {stage} wait"
                )
            cursor += 1
            if event["outcome"] == "reaped":
                return True
        return False

    def consume_signal(stage: str, signal_name: str) -> None:
        nonlocal cursor, sequence_anomaly
        if cursor >= len(events):
            raise AnalysisError(f"{label} lacks its {stage} action")
        group = events[cursor]
        if (
            group["stage"] != stage
            or group["operation"] != "killpg"
            or group["signal"] != signal_name
            or group["outcome"]
            not in {"sent", "not-found", "eperm", "error"}
        ):
            raise AnalysisError(
                f"{label}.events[{cursor}] is not a legal {stage} group action"
            )
        cursor += 1
        if group["outcome"] not in {"eperm", "error"}:
            return
        sequence_anomaly = True
        if cursor >= len(events):
            raise AnalysisError(
                f"{label} lacks direct-child fallback after {stage} "
                f"{group['outcome']}"
            )
        fallback = events[cursor]
        if (
            fallback["stage"] != stage
            or fallback["operation"] != "kill"
            or fallback["signal"] != signal_name
            or fallback["outcome"]
            not in {"sent", "not-found", "eperm", "error"}
        ):
            raise AnalysisError(
                f"{label}.events[{cursor}] is not the required direct-child "
                f"fallback"
            )
        if fallback["outcome"] in {"eperm", "error"}:
            sequence_anomaly = True
        cursor += 1

    reaped_before_timeout = consume_wait("wait-before-timeout")
    if timed_out is False:
        if not reaped_before_timeout:
            raise AnalysisError(
                f"{label} natural completion lacks its direct-child reap"
            )
    else:
        if reaped_before_timeout:
            raise AnalysisError(
                f"{label} reports timeout after natural child reap"
            )
        consume_signal("sigterm", "SIGTERM")
        reaped_after_term = consume_wait("wait-after-sigterm")
        if not reaped_after_term:
            consume_signal("sigkill", "SIGKILL")
            if not consume_wait("wait-after-sigkill"):
                raise AnalysisError(
                    f"{label} timeout sequence lacks its direct-child reap"
                )

    while cursor < len(events):
        event = events[cursor]
        if (
            event["stage"] == "group-empty-check"
            and event["operation"] == "killpg"
            and event["signal"] is None
            and event["outcome"] == "interrupted"
        ):
            cursor += 1
            continue
        break
    if cursor >= len(events):
        raise AnalysisError(f"{label} lacks its final group-empty proof")
    final_event = events[cursor]
    if (
        final_event["stage"] != "group-empty-check"
        or final_event["operation"] != "killpg"
        or final_event["signal"] is not None
        or final_event["outcome"] != "empty"
    ):
        raise AnalysisError(
            f"{label}.events[{cursor}] is not the final group-empty proof"
        )
    cursor += 1
    if cursor != len(events):
        raise AnalysisError(f"{label} has events after group-empty proof")

    _assert_equal(child_reaped, True, f"{label}.child_reaped")
    _assert_equal(
        group_empty, True, f"{label}.process_group_empty_after_reap"
    )
    _assert_equal(anomaly, sequence_anomaly, f"{label}.anomaly")
    _assert_equal(valid, not sequence_anomaly, f"{label}.valid")
    return record


def _call_v1(function: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return function(*args, **kwargs)
    except _v1.AnalysisError as exc:
        raise AnalysisError(str(exc)) from exc


def _validate_artifact_shape(
    value: Any,
    label: str,
) -> dict[str, str] | None:
    if value is None:
        return None
    record = _mapping(value, label)
    _exact_keys(record, {"path", "sha256"}, label)
    path = _string(record["path"], f"{label}.path")
    digest = _call_v1(_v1._sha256, record["sha256"], f"{label}.sha256")
    return {"path": path, "sha256": digest}


def _snapshot_artifact_record(
    value: Any,
    label: str,
    *,
    session_root: Path,
    expected_path: Path,
    guard: _EvidenceGuard,
    nullable: bool,
    maximum_bytes: int | None = None,
) -> tuple[dict[str, str] | None, _EvidenceSnapshot | None]:
    """Bind an artifact record to a stable private copy of exact bytes."""

    if value is None:
        if nullable:
            return None, None
        raise AnalysisError(f"{label} must not be null")
    record = _validate_artifact_shape(value, label)
    assert record is not None
    raw_path = Path(record["path"])
    if not raw_path.is_absolute():
        raise AnalysisError(f"{label}.path must be absolute")
    _assert_equal(raw_path, expected_path, f"{label}.path attempt binding")
    try:
        raw_path.relative_to(session_root)
    except ValueError as exc:
        raise AnalysisError(f"{label}.path escapes the session") from exc
    snapshot = guard.capture(
        raw_path,
        label,
        maximum_bytes=maximum_bytes,
    )
    _assert_equal(snapshot.sha256, record["sha256"], f"{label}.sha256")
    return record, snapshot


def _load_json_snapshot(
    snapshot: _EvidenceSnapshot,
    label: str,
) -> Any:
    """Parse the private bytes whose digest was admitted by the guard."""

    try:
        raw = snapshot.private_path.read_bytes()
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_v1._reject_duplicate_keys,
            parse_constant=_v1._reject_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AnalysisError(f"{label} is malformed JSON: {exc}") from exc
    _assert_equal(
        hashlib.sha256(raw).hexdigest(),
        snapshot.sha256,
        f"{label} private-copy hash",
    )
    return value


def _validate_run_identity(run: dict[str, Any], label: str) -> None:
    _assert_equal(run["schema_version"], RUN_SCHEMA, f"{label}.schema_version")
    _integer(run["sequence"], f"{label}.sequence", minimum=1)
    for field in ("attempt_id", "block_id", "pair_id", "environment"):
        _string(run[field], f"{label}.{field}")
    _integer(run["repetition"], f"{label}.repetition", minimum=0)
    if run["attempt_kind"] not in {"warmup", "measured"}:
        raise AnalysisError(f"{label}.attempt_kind is invalid")
    if run["order"] not in {"warmup", "AB", "BA"}:
        raise AnalysisError(f"{label}.order is invalid")
    _integer(
        run["order_position"],
        f"{label}.order_position",
        minimum=1,
        maximum=2,
    )
    _string(run["phase"], f"{label}.phase")
    if run["mode"] not in {"timing", "fidelity"}:
        raise AnalysisError(f"{label}.mode is invalid")
    _integer(run["seed"], f"{label}.seed", minimum=1)
    _integer(run["robot_count"], f"{label}.robot_count", minimum=1)
    if run["method"] not in METHODS:
        raise AnalysisError(f"{label}.method is invalid")
    _string(run["terminal_category"], f"{label}.terminal_category")
    if run["orchestration_error"] is not None and not isinstance(
        run["orchestration_error"], str
    ):
        raise AnalysisError(f"{label}.orchestration_error is invalid")
    if (
        run["terminal_category"] == "completed"
        and run["orchestration_error"] is not None
    ):
        raise AnalysisError(
            f"{label} completed but has an orchestration error"
        )
    _string(run["completed_utc"], f"{label}.completed_utc")


def _validate_fidelity_metrics(run: dict[str, Any], label: str) -> None:
    task_valid = run["task_valid"]
    metrics = run["fidelity_metrics"]
    if run["mode"] == "timing":
        _assert_equal(task_valid, None, f"{label}.task_valid")
        _assert_equal(metrics, None, f"{label}.fidelity_metrics")
        return
    if run["terminal_category"] != "completed":
        _assert_equal(task_valid, None, f"{label}.task_valid")
        _assert_equal(metrics, None, f"{label}.fidelity_metrics")
        return
    task_valid = _boolean(task_valid, f"{label}.task_valid")
    record = _mapping(metrics, f"{label}.fidelity_metrics")
    keys = {
        "task_validity_predicate_id",
        "task_valid",
        "task_fields",
        "paper_metrics",
        "summary_parity_fields",
    }
    _exact_keys(record, keys, f"{label}.fidelity_metrics")
    _assert_equal(
        record["task_validity_predicate_id"],
        TASK_VALIDITY_PREDICATE_ID,
        f"{label}.fidelity_metrics.task_validity_predicate_id",
    )
    _assert_equal(
        record["task_valid"],
        task_valid,
        f"{label}.fidelity_metrics.task_valid",
    )
    for field in ("task_fields", "paper_metrics", "summary_parity_fields"):
        _mapping(record[field], f"{label}.fidelity_metrics.{field}")


def _finite_number(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise AnalysisError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise AnalysisError(f"{label} must be finite")
    return result


def recompute_fidelity_metrics_from_summary(
    summary: Any,
    *,
    provenance: Mapping[str, Any],
    robot_count: int,
    seed: int,
    label: str = "fidelity summary",
) -> dict[str, Any]:
    """Recompute the runner's registered task and parity record from bytes."""

    record = _mapping(summary, label)
    exact_scalars: tuple[tuple[str, Any], ...] = (
        ("schema_version", "dancers-comparison-summary-v1"),
        ("run_valid", True),
        ("final_tick", FIXED_WORK_TIMESTEPS),
        ("final_robot_count", robot_count),
        ("maximum_robot_count", robot_count),
        ("frames_recorded", FIXED_WORK_TIMESTEPS * 2),
        ("termination_reason", "maximum_time"),
        ("trace_detail", "digest"),
        ("invalid_geometry_evaluations", 0),
        ("invalid_robot_frames", 0),
        ("nonfinite_numeric_observations", 0),
        ("nonfinite_state_observations", 0),
    )
    for field, expected in exact_scalars:
        if field not in record:
            raise AnalysisError(f"{label}.{field} is missing")
        _assert_exact_json_scalar(
            record[field], expected, f"{label}.{field}"
        )

    metadata = _mapping(record.get("metadata"), f"{label}.metadata")
    for field in (
        "run_id",
        "implementation_id",
        "algorithm_revision",
        "common_patch_sha256",
        "binary_sha256",
        "pixi_lock_sha256",
        "config_sha256",
        "map_sha256",
        "scenario_sha256",
        "omp_threads",
    ):
        _assert_equal(
            metadata.get(field),
            provenance[field],
            f"{label}.metadata.{field}",
        )
    resolved = _mapping(
        record.get("resolved_config"), f"{label}.resolved_config"
    )
    for field, expected in (
        ("max_timesteps", FIXED_WORK_TIMESTEPS),
        ("configured_robot_count", robot_count),
        ("rng_seed", seed),
        ("display", False),
    ):
        _assert_exact_json_scalar(
            resolved.get(field), expected, f"{label}.resolved_config.{field}"
        )

    paper_metrics = _mapping(
        record.get("paper_metrics"), f"{label}.paper_metrics"
    )
    required_metrics = {
        "consensus_reached",
        "final_connectivity",
        "final_decision",
        "final_heading_error",
        "final_iterations",
        "final_position_error",
    }
    _exact_keys(paper_metrics, required_metrics, f"{label}.paper_metrics")
    for field in (
        "consensus_reached",
        "final_decision",
        "final_iterations",
    ):
        _integer(paper_metrics[field], f"{label}.paper_metrics.{field}")
    for field in (
        "final_connectivity",
        "final_heading_error",
        "final_position_error",
    ):
        _finite_number(
            paper_metrics[field], f"{label}.paper_metrics.{field}"
        )

    missing_parity = [
        field for field in SUMMARY_PARITY_FIELDS if field not in record
    ]
    if missing_parity:
        raise AnalysisError(
            f"{label} is missing registered parity fields: {missing_parity}"
        )
    for field in (
        "minimum_pair_clearance_m",
        "minimum_obstacle_clearance_m",
        "pair_collision_exposure_seconds",
        "obstacle_collision_exposure_seconds",
    ):
        _finite_number(record[field], f"{label}.{field}")
    for field in ("pair_collision_events", "obstacle_collision_events"):
        _integer(record[field], f"{label}.{field}", minimum=0)

    selected = {field: record[field] for field in SUMMARY_PARITY_FIELDS}
    task_fields = {field: record[field] for field in TASK_VALIDITY_FIELDS}
    task_fields.update(
        {
            "minimum_pair_clearance_m": record[
                "minimum_pair_clearance_m"
            ],
            "minimum_obstacle_clearance_m": record[
                "minimum_obstacle_clearance_m"
            ],
        }
    )
    task_valid = bool(
        all(
            record[field] == 0
            for field in (
                "invalid_geometry_evaluations",
                "invalid_robot_frames",
                "nonfinite_numeric_observations",
                "nonfinite_state_observations",
                "pair_collision_events",
                "obstacle_collision_events",
                "pair_collision_exposure_seconds",
                "obstacle_collision_exposure_seconds",
            )
        )
        and float(record["minimum_pair_clearance_m"]) > 0.0
        and float(record["minimum_obstacle_clearance_m"]) > 0.0
    )
    return {
        "task_validity_predicate_id": TASK_VALIDITY_PREDICATE_ID,
        "task_valid": task_valid,
        "task_fields": task_fields,
        "paper_metrics": paper_metrics,
        "summary_parity_fields": selected,
    }


def validate_run_record(
    value: Any,
    *,
    label: str = "run",
    session_root: Path | None = None,
    analysis_sha256: str | None = None,
    evidence_guard: _EvidenceGuard | None = None,
) -> dict[str, Any]:
    """Validate one amended run and return its registered normalized row.

    When ``session_root`` is supplied, artifact hashes, result copies,
    provenance sidecars, and completed fidelity trace-v3 files are also
    independently verified.
    """

    run = _mapping(value, label)
    _exact_keys(run, RUN_V2_KEYS, label)
    _validate_run_identity(run, label)
    _validate_fidelity_metrics(run, label)
    terminal = str(run["terminal_category"])

    before = validate_host_snapshot(
        run["host_conditions_before_attempt"],
        f"{label}.host_conditions_before_attempt",
    )
    after = validate_host_snapshot(
        run["host_conditions_after_attempt"],
        f"{label}.host_conditions_after_attempt",
    )
    _assert_equal(
        before["power_policy_evaluation"]["stage"],
        "before-attempt",
        f"{label}.host_conditions_before_attempt stage",
    )
    _assert_equal(
        after["power_policy_evaluation"]["stage"],
        "after-attempt",
        f"{label}.host_conditions_after_attempt stage",
    )
    before_baseline = before["power_policy_evaluation"][
        "cooldown_baseline_percent"
    ]
    after_baseline = after["power_policy_evaluation"][
        "cooldown_baseline_percent"
    ]
    if before_baseline is None or after_baseline is None:
        raise AnalysisError(f"{label} attempt snapshots lack cooldown baseline")
    _assert_equal(
        after_baseline,
        before_baseline,
        f"{label} attempt cooldown baseline",
    )
    expected_host_valid = host_attempt_valid(before, after)
    host_valid = _boolean(
        run["host_attempt_valid"], f"{label}.host_attempt_valid"
    )
    _assert_equal(
        host_valid,
        expected_host_valid,
        f"{label}.host_attempt_valid",
    )

    process = validate_process_control(
        run["process_control"], f"{label}.process_control"
    )
    if process["anomaly"]:
        _assert_equal(
            terminal,
            "measurement-wrapper-failure",
            f"{label}.terminal_category for process anomaly",
        )
    if terminal == "completed" and not process["valid"]:
        raise AnalysisError(
            f"{label} completed with invalid process-control evidence"
        )

    resource = _mapping(run["resource"], f"{label}.resource")
    _call_v1(
        _v1._validate_resource,
        resource,
        f"{label}.resource",
        terminal,
    )
    if (
        process["timed_out"] is not None
        and resource["timed_out"] != process["timed_out"]
    ):
        raise AnalysisError(
            f"{label}.resource.timed_out disagrees with process control"
        )

    provenance = _mapping(run["provenance"], f"{label}.provenance")
    if analysis_sha256 is not None:
        _call_v1(
            _v1._validate_provenance,
            provenance,
            run,
            f"{label}.provenance",
            analysis_sha256,
        )
    else:
        _exact_keys(provenance, set(_v1.PROVENANCE_KEYS), f"{label}.provenance")

    artifacts = _mapping(run["artifacts"], f"{label}.artifacts")
    _exact_keys(artifacts, set(_v1.ARTIFACT_KEYS), f"{label}.artifacts")
    normalized_artifacts: dict[str, dict[str, str] | None] = {}
    evidence: dict[str, _EvidenceSnapshot | None] = {
        "trace": None,
        "summary": None,
    }
    if session_root is None:
        for name in ("config", "stdout", "stderr", "trace", "summary"):
            normalized_artifacts[name] = _validate_artifact_shape(
                artifacts[name], f"{label}.artifacts.{name}"
            )
    else:
        if evidence_guard is None:
            raise AnalysisError(
                f"{label} session validation lacks an evidence guard"
            )
        attempt_root = session_root / "attempts" / run["attempt_id"]
        for name in ("config", "stdout", "stderr"):
            normalized_artifacts[name] = _call_v1(
                _v1._artifact_record,
                artifacts[name],
                f"{label}.artifacts.{name}",
                session_root,
                nullable=False,
            )
        expected_simple_paths = {
            "config": (
                session_root
                / "blocks"
                / str(run["block_id"])
                / "config.yaml"
            ),
            "stdout": attempt_root / "stdout.log",
            "stderr": attempt_root / "stderr.log",
        }
        for name, expected_path in expected_simple_paths.items():
            artifact = normalized_artifacts[name]
            assert artifact is not None
            _assert_equal(
                Path(artifact["path"]),
                expected_path,
                f"{label}.artifacts.{name}.path",
            )
        for name in ("trace", "summary"):
            artifact, snapshot = _snapshot_artifact_record(
                artifacts[name],
                f"{label}.artifacts.{name}",
                session_root=session_root,
                expected_path=attempt_root
                / ("trace.jsonl" if name == "trace" else "summary.json"),
                guard=evidence_guard,
                nullable=True,
                maximum_bytes=(
                    _trace.DEFAULT_LIMITS.max_file_bytes
                    if name == "trace"
                    else 64 * 1024 * 1024
                ),
            )
            normalized_artifacts[name] = artifact
            evidence[name] = snapshot
        if evidence["trace"] is not None:
            evidence_guard.trace_paths[str(run["attempt_id"])] = evidence[
                "trace"
            ].private_path
    if normalized_artifacts["config"] is None:
        raise AnalysisError(f"{label} lacks its configuration artifact")
    _assert_equal(
        normalized_artifacts["config"]["sha256"],
        provenance["config_sha256"],
        f"{label}.config provenance",
    )
    if run["mode"] == "timing":
        if (
            normalized_artifacts["trace"] is not None
            or normalized_artifacts["summary"] is not None
        ):
            raise AnalysisError(f"{label} timing run contains observer output")
    elif terminal == "completed":
        trace = normalized_artifacts["trace"]
        summary = normalized_artifacts["summary"]
        if trace is None or summary is None:
            raise AnalysisError(
                f"{label} completed fidelity run lacks observer output"
            )
        if session_root is not None:
            trace_snapshot = evidence["trace"]
            summary_snapshot = evidence["summary"]
            assert trace_snapshot is not None
            assert summary_snapshot is not None
            trace_contract = _call_v1(
                _v1._validate_scientific_trace,
                {
                    "path": str(trace_snapshot.private_path),
                    "sha256": trace_snapshot.sha256,
                },
                f"{label}.artifacts.trace",
                expected_path=trace_snapshot.private_path,
                expected_provenance=provenance,
            )
            summary_value = _load_json_snapshot(
                summary_snapshot,
                f"{label} observer summary",
            )
            summary_record = _mapping(
                summary_value, f"{label} observer summary"
            )
            _assert_equal(
                summary_record.get("trace_detail"),
                trace_contract["trace_detail"],
                f"{label} observer summary.trace_detail",
            )
            recomputed_metrics = recompute_fidelity_metrics_from_summary(
                summary_record,
                provenance=provenance,
                robot_count=int(run["robot_count"]),
                seed=int(run["seed"]),
                label=f"{label} observer summary",
            )
            _assert_exact_json_value(
                run["fidelity_metrics"],
                recomputed_metrics,
                f"{label}.fidelity_metrics summary binding",
            )
            _assert_equal(
                run["task_valid"],
                recomputed_metrics["task_valid"],
                f"{label}.task_valid summary binding",
            )

    if session_root is not None:
        attempt_root = session_root / "attempts" / run["attempt_id"]
        result_copy, _ = _call_v1(
            _v1._load_json,
            attempt_root / "result.json",
            f"{label} result copy",
        )
        _assert_equal(result_copy, run, f"{label} result copy")
        metadata, _ = _call_v1(
            _v1._load_json,
            attempt_root / "runner_metadata.json",
            f"{label} runner metadata",
        )
        _assert_equal(metadata, provenance, f"{label} runner metadata")
        observer_path = attempt_root / "observer_metadata.json"
        if run["mode"] == "fidelity":
            observer, _ = _call_v1(
                _v1._load_json,
                observer_path,
                f"{label} observer metadata",
            )
            _assert_equal(observer, provenance, f"{label} observer metadata")
        elif observer_path.exists() or observer_path.is_symlink():
            raise AnalysisError(
                f"{label} timing attempt has observer metadata"
            )

    resource_valid = bool(
        terminal == "completed"
        and resource["total_cpu_seconds"] is not None
        and float(resource["total_cpu_seconds"]) > 0.0
        and resource["wall_seconds"] is not None
        and float(resource["wall_seconds"]) > 0.0
        and resource["peak_rss_mib"] is not None
        and 0.0 < float(resource["peak_rss_mib"]) <= MAXIMUM_PEAK_RSS_MIB
    )
    if run["mode"] == "timing":
        return {
            "attempt_id": run["attempt_id"],
            "method": run["method"],
            "robot_count": run["robot_count"],
            "seed": run["seed"],
            "repetition": run["repetition"],
            "terminal_category": terminal,
            "host_valid": host_valid,
            "process_control_valid": process["valid"],
            "total_cpu_seconds": resource["total_cpu_seconds"],
            "wall_seconds": resource["wall_seconds"],
            "peak_rss_mib": resource["peak_rss_mib"],
        }
    return {
        "attempt_id": run["attempt_id"],
        "terminal_category": terminal,
        "host_valid": host_valid,
        "process_control_valid": process["valid"],
        "task_valid": run["task_valid"] is True,
        "resource_valid": resource_valid,
        "artifacts_valid": True,
    }


def validate_pair_record(
    value: Any,
    *,
    label: str = "pair",
) -> dict[str, Any]:
    """Validate a v2 pair's host binding and registered fidelity fields."""

    pair = _mapping(value, label)
    _exact_keys(pair, PAIR_V2_KEYS, label)
    _assert_equal(pair["schema_version"], PAIR_SCHEMA, f"{label}.schema_version")
    for field in ("block_id", "pair_id", "environment", "phase"):
        _string(pair[field], f"{label}.{field}")
    _integer(pair["repetition"], f"{label}.repetition", minimum=1)
    if pair["order"] not in {"AB", "BA"}:
        raise AnalysisError(f"{label}.order is invalid")
    if pair["mode"] not in {"timing", "fidelity"}:
        raise AnalysisError(f"{label}.mode is invalid")
    _integer(pair["seed"], f"{label}.seed", minimum=1)
    _integer(pair["robot_count"], f"{label}.robot_count", minimum=1)
    attempt_ids = _mapping(pair["attempt_ids"], f"{label}.attempt_ids")
    terminals = _mapping(
        pair["terminal_categories"], f"{label}.terminal_categories"
    )
    host_by_method = _mapping(
        pair["attempt_host_validity"],
        f"{label}.attempt_host_validity",
    )
    for field_name, record in (
        ("attempt_ids", attempt_ids),
        ("terminal_categories", terminals),
        ("attempt_host_validity", host_by_method),
    ):
        _exact_keys(record, set(METHODS), f"{label}.{field_name}")
    for method in METHODS:
        _string(attempt_ids[method], f"{label}.attempt_ids.{method}")
        _string(terminals[method], f"{label}.terminal_categories.{method}")
        _boolean(
            host_by_method[method],
            f"{label}.attempt_host_validity.{method}",
        )

    before = validate_host_snapshot(
        pair["host_conditions_before"],
        f"{label}.host_conditions_before",
    )
    after = validate_host_snapshot(
        pair["host_conditions_after"],
        f"{label}.host_conditions_after",
    )
    for boundary, snapshot in (("before", before), ("after", after)):
        _assert_equal(
            snapshot["power_policy_evaluation"]["stage"],
            "block-boundary",
            f"{label}.host_conditions_{boundary} stage",
        )
        if (
            snapshot["power_policy_evaluation"][
                "cooldown_baseline_percent"
            ]
            is None
        ):
            raise AnalysisError(
                f"{label}.host_conditions_{boundary} lacks cooldown baseline"
            )
    _assert_equal(
        after["power_policy_evaluation"]["cooldown_baseline_percent"],
        before["power_policy_evaluation"]["cooldown_baseline_percent"],
        f"{label} block cooldown baseline",
    )
    expected_host_valid = bool(
        before["valid"]
        and after["valid"]
        and before["power_probe"]["source"]
        == after["power_probe"]["source"]
        == POWER_MODE
        and before["thermal_probe"]["state"]
        == after["thermal_probe"]["state"]
        == 0
        and all(host_by_method[method] for method in METHODS)
    )
    host_valid = _boolean(
        pair["host_block_valid"], f"{label}.host_block_valid"
    )
    _assert_equal(
        host_valid,
        expected_host_valid,
        f"{label}.host_block_valid",
    )
    _assert_equal(
        pair["block_terminal_category"],
        (
            "completed"
            if host_valid
            else "host-power-stability-contamination"
        ),
        f"{label}.block_terminal_category",
    )
    config_equal = _boolean(
        pair["config_sha256_equal"], f"{label}.config_sha256_equal"
    )
    for field in (
        "semantic_trace_equal",
        "paper_metrics_equal",
        "summary_parity_fields_equal",
        "task_valid",
    ):
        if pair[field] is not None:
            _boolean(pair[field], f"{label}.{field}")
    if pair["frames_compared"] is not None:
        _integer(
            pair["frames_compared"],
            f"{label}.frames_compared",
            minimum=0,
        )
    if pair["mismatch"] is not None:
        _mapping(pair["mismatch"], f"{label}.mismatch")
    if pair["summary_mismatch"] is not None:
        _mapping(pair["summary_mismatch"], f"{label}.summary_mismatch")
    _assert_equal(
        pair["task_validity_predicate_id"],
        TASK_VALIDITY_PREDICATE_ID,
        f"{label}.task_validity_predicate_id",
    )
    expected_pair_valid = bool(
        host_valid
        and config_equal
        and all(terminals[method] == "completed" for method in METHODS)
        and (
            pair["mode"] == "timing"
            or (
                pair["semantic_trace_equal"] is True
                and pair["paper_metrics_equal"] is True
                and pair["summary_parity_fields_equal"] is True
                and pair["task_valid"] is True
            )
        )
    )
    pair_valid = _boolean(pair["pair_valid"], f"{label}.pair_valid")
    _assert_equal(pair_valid, expected_pair_valid, f"{label}.pair_valid")
    return {
        "pair_id": pair["pair_id"],
        "host_valid": host_valid,
        "pair_valid": pair_valid,
        "frames_compared": pair["frames_compared"],
        "config_sha256_equal": config_equal,
        "semantic_trace_equal": pair["semantic_trace_equal"],
        "paper_metrics_equal": pair["paper_metrics_equal"],
        "summary_parity_fields_equal": pair["summary_parity_fields_equal"],
        "task_valid": pair["task_valid"],
    }


def _artifact_on_disk(
    value: Any,
    label: str,
) -> dict[str, str]:
    record = _validate_artifact_shape(value, label)
    if record is None:
        raise AnalysisError(f"{label} must not be null")
    path = Path(record["path"])
    if not path.is_absolute():
        raise AnalysisError(f"{label}.path must be absolute")
    if path.is_symlink():
        raise AnalysisError(f"{label}.path must not be a symbolic link")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AnalysisError(f"{label}.path cannot be resolved: {exc}") from exc
    if not resolved.is_file():
        raise AnalysisError(f"{label}.path is not a regular file")
    _assert_equal(
        sha256_file(resolved),
        record["sha256"],
        f"{label}.sha256",
    )
    return {"path": str(resolved), "sha256": record["sha256"]}


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _registered_result_root(mode: str) -> Path:
    return (
        _repository_root()
        / "results"
        / "comparison"
        / "calibration"
        / "trace-v3-amended-v1"
        / RESULT_ROOT_NAMES[mode]
    )


def validate_plan_artifact_paths(
    artifacts: Mapping[str, Mapping[str, str]],
    *,
    run_cwd: Any,
    mode: str,
    label: str = "frozen_plan.json",
) -> None:
    """Anchor all code/data identities to their registered repository paths."""

    repository = _repository_root()
    _assert_equal(str(run_cwd), str(repository), f"{label}.run_cwd")
    expected_relative_paths = {
        "analysis_revision": "scripts/comparison/analyze_results_v2.py",
        "analyzer_v1_base": "scripts/comparison/analyze_results.py",
        "trace_compare": "scripts/comparison/trace_compare.py",
        "runner": "scripts/comparison/run_paired_v2.py",
        "runner_v1_base": "scripts/comparison/run_paired.py",
        "freeze_capacity_decision": (
            "scripts/comparison/freeze_capacity_decision.py"
        ),
        "plan_freezer": "scripts/comparison/freeze_plan_v2.py",
        "amendment_protocol": (
            "docs/comparison/EXPERIMENT_PROTOCOL_AMENDMENT_FH_LOWER_V1.md"
        ),
        "post_outcome_amendment": (
            "docs/comparison/fractal_high_lower_count_amendment_v1.json"
        ),
        "experiment_manifest": "docs/comparison/experiment_manifest.json",
        "environment_manifest": "comparison/environments/manifest.json",
        "environment_manifest_sidecar": (
            "comparison/environments/manifest.sha256"
        ),
        "capacity_materializer": (
            "scripts/comparison/materialize_capacity_scenario.py"
        ),
        "environment_generator": (
            "scripts/comparison/generate_environments.py"
        ),
        "fractal_high_calibration_map": (
            "comparison/environments/fractal_high/calibration/map.png"
        ),
        "fractal_high_calibration_bank": (
            "comparison/environments/fractal_high/calibration/capacity/"
            "bank.json"
        ),
        "protocol": "docs/comparison/EXPERIMENT_PROTOCOL.md",
        "pixi_lock": "pixi.lock",
        "config_template": "config/comparison/laptop_benchmark.yaml",
        "build_manifest": "comparison/build_manifest.json",
        "trigger_analysis": (
            "results/comparison/calibration/trace-v3/"
            "fractal-high-full-grid-fidelity-analysis.json"
        ),
        "trigger_frozen_plan": (
            "results/comparison/calibration/trace-v3/"
            "fractal-high-full-grid-fidelity/frozen_plan.json"
        ),
        "trigger_session": (
            "results/comparison/calibration/trace-v3/"
            "fractal-high-full-grid-fidelity/session.json"
        ),
        "trigger_runs": (
            "results/comparison/calibration/trace-v3/"
            "fractal-high-full-grid-fidelity/runs.jsonl"
        ),
        "trigger_pairs": (
            "results/comparison/calibration/trace-v3/"
            "fractal-high-full-grid-fidelity/pairs.jsonl"
        ),
    }
    for name, relative in expected_relative_paths.items():
        expected = (repository / relative).resolve()
        _assert_equal(
            Path(artifacts[name]["path"]),
            expected,
            f"{label}.artifacts.{name}.path",
        )
    _assert_equal(
        artifacts["post_outcome_amendment"]["sha256"],
        POST_OUTCOME_AMENDMENT_SHA256,
        f"{label}.artifacts.post_outcome_amendment.sha256",
    )
    if mode == "timing":
        fidelity_root = _registered_result_root("fidelity")
        expected_fidelity_paths = {
            "fidelity_gate_analysis": (
                _repository_root()
                / "results"
                / "comparison"
                / "calibration"
                / "trace-v3-amended-v1"
                / "fidelity-gate-receipt.json"
            ),
            "fidelity_frozen_plan": fidelity_root / "frozen_plan.json",
            "fidelity_run_manifest": fidelity_root / "run_manifest.json",
            "fidelity_preflight": fidelity_root / "preflight.json",
            "fidelity_session": fidelity_root / "session.json",
            "fidelity_runs": fidelity_root / "runs.jsonl",
            "fidelity_pairs": fidelity_root / "pairs.jsonl",
        }
        for name, expected in expected_fidelity_paths.items():
            _assert_equal(
                Path(artifacts[name]["path"]),
                expected,
                f"{label}.artifacts.{name}.path",
            )
        receipt = Path(artifacts["fidelity_gate_analysis"]["path"])
        try:
            receipt.relative_to(repository)
        except ValueError as exc:
            raise AnalysisError(
                f"{label}.artifacts.fidelity_gate_analysis.path "
                "must be inside the registered repository"
            ) from exc


def _validate_amendment_artifact_bindings(
    artifacts: Mapping[str, Mapping[str, str]],
    label: str,
) -> None:
    amendment_path = Path(artifacts["post_outcome_amendment"]["path"])
    amendment, _ = _json_artifact_file(
        amendment_path, f"{label}.post_outcome_amendment"
    )
    _assert_equal(
        amendment.get("schema_version"),
        "dancers-post-outcome-amendment-v1",
        f"{label}.post_outcome_amendment.schema_version",
    )
    _assert_equal(
        amendment.get("amendment_id"),
        AMENDMENT_ID,
        f"{label}.post_outcome_amendment.amendment_id",
    )
    _assert_equal(
        amendment.get("status"),
        "frozen-before-any-amended-attempt",
        f"{label}.post_outcome_amendment.status",
    )
    validate_power_policy(
        amendment.get("power_policy"),
        f"{label}.post_outcome_amendment.power_policy",
    )
    validate_process_control_policy(
        amendment.get("process_control_policy"),
        f"{label}.post_outcome_amendment.process_control_policy",
    )
    base_artifacts = _mapping(
        amendment.get("base_artifacts"),
        f"{label}.post_outcome_amendment.base_artifacts",
    )
    _exact_keys(
        base_artifacts,
        {"experiment_manifest", "environment_manifest", "protocol"},
        f"{label}.post_outcome_amendment.base_artifacts",
    )
    repository = _repository_root()
    for name, raw in base_artifacts.items():
        record = _mapping(
            raw, f"{label}.post_outcome_amendment.base_artifacts.{name}"
        )
        _exact_keys(
            record,
            {"path", "sha256"},
            f"{label}.post_outcome_amendment.base_artifacts.{name}",
        )
        _assert_equal(
            Path(artifacts[name]["path"]),
            (repository / str(record["path"])).resolve(),
            f"{label}.artifacts.{name}.path amendment binding",
        )
        _assert_equal(
            artifacts[name]["sha256"],
            record["sha256"],
            f"{label}.artifacts.{name}.sha256 amendment binding",
        )
    protocol = _mapping(
        amendment.get("amendment_protocol"),
        f"{label}.post_outcome_amendment.amendment_protocol",
    )
    _exact_keys(
        protocol,
        {"path", "sha256"},
        f"{label}.post_outcome_amendment.amendment_protocol",
    )
    _assert_equal(
        Path(artifacts["amendment_protocol"]["path"]),
        (repository / str(protocol["path"])).resolve(),
        f"{label}.artifacts.amendment_protocol.path amendment binding",
    )
    _assert_equal(
        artifacts["amendment_protocol"]["sha256"],
        protocol["sha256"],
        f"{label}.artifacts.amendment_protocol.sha256 amendment binding",
    )
    trigger = _mapping(
        amendment.get("trigger"),
        f"{label}.post_outcome_amendment.trigger",
    )
    trigger_analysis = _mapping(
        trigger.get("analysis"),
        f"{label}.post_outcome_amendment.trigger.analysis",
    )
    _assert_equal(
        artifacts["trigger_analysis"]["sha256"],
        trigger_analysis.get("sha256"),
        f"{label}.artifacts.trigger_analysis.sha256 amendment binding",
    )
    trigger_hashes = _mapping(
        trigger.get("hashes"),
        f"{label}.post_outcome_amendment.trigger.hashes",
    )
    for field, artifact_name in (
        ("frozen_plan_json_sha256", "trigger_frozen_plan"),
        ("session_json_sha256", "trigger_session"),
        ("runs_jsonl_sha256", "trigger_runs"),
        ("pairs_jsonl_sha256", "trigger_pairs"),
    ):
        _assert_equal(
            artifacts[artifact_name]["sha256"],
            trigger_hashes.get(field),
            f"{label}.artifacts.{artifact_name}.sha256 amendment binding",
        )


def _validate_environment_dependency_bindings(
    artifacts: Mapping[str, Mapping[str, str]],
    label: str,
) -> None:
    manifest_path = Path(artifacts["environment_manifest"]["path"])
    manifest, _ = _json_artifact_file(
        manifest_path, f"{label}.environment_manifest"
    )
    expected_sidecar = (
        f"{artifacts['environment_manifest']['sha256']}  manifest.json\n"
    ).encode("ascii")
    if (
        Path(artifacts["environment_manifest_sidecar"]["path"]).read_bytes()
        != expected_sidecar
    ):
        raise AnalysisError(
            f"{label}.artifacts.environment_manifest_sidecar is not the "
            "canonical manifest binding"
        )
    capacity_suite = _mapping(
        manifest.get("capacity_suite"),
        f"{label}.environment_manifest.capacity_suite",
    )
    materializer = _mapping(
        capacity_suite.get("prefix_materializer"),
        f"{label}.environment_manifest.capacity_suite.prefix_materializer",
    )
    _assert_equal(
        artifacts["capacity_materializer"]["sha256"],
        materializer.get("sha256"),
        f"{label}.artifacts.capacity_materializer.sha256",
    )
    generator = _mapping(
        manifest.get("generator"), f"{label}.environment_manifest.generator"
    )
    _assert_equal(
        artifacts["environment_generator"]["sha256"],
        generator.get("sha256"),
        f"{label}.artifacts.environment_generator.sha256",
    )
    environments = manifest.get("environments")
    if not isinstance(environments, list):
        raise AnalysisError(
            f"{label}.environment_manifest.environments must be an array"
        )
    matches = [
        item
        for item in environments
        if isinstance(item, dict)
        and item.get("family") == "fractal_high"
        and item.get("split") == "calibration"
    ]
    if len(matches) != 1:
        raise AnalysisError(
            f"{label}.environment_manifest lacks one fractal_high "
            "calibration record"
        )
    map_record = _mapping(
        matches[0].get("map"),
        f"{label}.environment_manifest fractal_high map",
    )
    capacity = _mapping(
        matches[0].get("capacity"),
        f"{label}.environment_manifest fractal_high capacity",
    )
    bank_record = _mapping(
        capacity.get("bank"),
        f"{label}.environment_manifest fractal_high capacity bank",
    )
    for artifact_name, record in (
        ("fractal_high_calibration_map", map_record),
        ("fractal_high_calibration_bank", bank_record),
    ):
        _assert_equal(
            artifacts[artifact_name]["sha256"],
            record.get("sha256"),
            f"{label}.artifacts.{artifact_name}.sha256",
        )
        _assert_equal(
            Path(artifacts[artifact_name]["path"]).stat().st_size,
            record.get("bytes"),
            f"{label}.artifacts.{artifact_name}.bytes",
        )


def _registered_pair_artifact_contract(
    artifacts: Mapping[str, Mapping[str, str]],
    *,
    mode: str,
    label: str,
) -> dict[str, Any]:
    """Resolve the one map and two exact-N prefixes registered by amendment."""

    amendment, _ = _json_artifact_file(
        Path(artifacts["post_outcome_amendment"]["path"]),
        f"{label}.post_outcome_amendment",
    )
    fidelity = _mapping(
        amendment.get("followup_fidelity_design"),
        f"{label}.post_outcome_amendment.followup_fidelity_design",
    )
    _assert_equal(
        fidelity.get("map_sha256"),
        artifacts["fractal_high_calibration_map"]["sha256"],
        f"{label} amendment map SHA-256",
    )
    registered_prefixes = _mapping(
        fidelity.get("registered_scenario_prefixes"),
        f"{label} amendment registered_scenario_prefixes",
    )
    _exact_keys(
        registered_prefixes,
        {str(count) for count in COUNTS},
        f"{label} amendment registered_scenario_prefixes",
    )

    environment, _ = _json_artifact_file(
        Path(artifacts["environment_manifest"]["path"]),
        f"{label}.environment_manifest",
    )
    environments = environment.get("environments")
    if not isinstance(environments, list):
        raise AnalysisError(f"{label}.environment_manifest is malformed")
    matches = [
        item
        for item in environments
        if isinstance(item, dict)
        and item.get("family") == "fractal_high"
        and item.get("split") == "calibration"
    ]
    if len(matches) != 1:
        raise AnalysisError(
            f"{label}.environment_manifest must contain exactly one "
            "fractal_high/calibration record"
        )
    case = matches[0]
    map_record = _mapping(case.get("map"), f"{label} registered map")
    _exact_keys(
        map_record, {"path", "sha256", "bytes"}, f"{label} registered map"
    )
    expected_map_path = (
        Path(artifacts["environment_manifest"]["path"]).parent
        / str(map_record["path"])
    ).resolve()
    _assert_equal(
        expected_map_path,
        Path(artifacts["fractal_high_calibration_map"]["path"]),
        f"{label} registered map path",
    )
    _assert_equal(
        map_record["sha256"],
        artifacts["fractal_high_calibration_map"]["sha256"],
        f"{label} registered map SHA-256",
    )
    map_bytes = _integer(
        map_record["bytes"], f"{label} registered map bytes", minimum=1
    )

    capacity = _mapping(
        case.get("capacity"), f"{label} registered capacity"
    )
    bank_id = _call_v1(
        _v1._sha256,
        capacity.get("bank_id"),
        f"{label} registered capacity.bank_id",
    )
    raw_prefixes = capacity.get("prefix_scenarios")
    if not isinstance(raw_prefixes, list):
        raise AnalysisError(
            f"{label} registered capacity prefixes must be an array"
        )
    environment_prefixes: dict[int, Mapping[str, Any]] = {}
    for index, raw in enumerate(raw_prefixes):
        prefix = _mapping(
            raw, f"{label} registered capacity prefix[{index}]"
        )
        count = _integer(
            prefix.get("robot_count"),
            f"{label} registered capacity prefix[{index}].robot_count",
            minimum=1,
        )
        if count in environment_prefixes:
            raise AnalysisError(
                f"{label} duplicate registered prefix N={count}"
            )
        environment_prefixes[count] = prefix

    scenario_root = (
        _repository_root()
        / "comparison"
        / "plans"
        / "calibration"
        / "trace-v3-amended-v1"
        / RESULT_ROOT_NAMES[mode]
        / "scenarios"
    )
    prefixes: dict[int, dict[str, Any]] = {}
    for count in COUNTS:
        environment_prefix = _mapping(
            environment_prefixes.get(count),
            f"{label} environment prefix N={count}",
        )
        amendment_prefix = _mapping(
            registered_prefixes[str(count)],
            f"{label} amendment prefix N={count}",
        )
        _exact_keys(
            amendment_prefix,
            {"bytes", "sha256"},
            f"{label} amendment prefix N={count}",
        )
        for field in ("bytes", "sha256"):
            _assert_equal(
                environment_prefix.get(field),
                amendment_prefix[field],
                f"{label} N={count} {field} dual registration",
            )
        prefixes[count] = {
            "path": (scenario_root / f"n-{count}.json").resolve(),
            "sha256": _call_v1(
                _v1._sha256,
                amendment_prefix["sha256"],
                f"{label} N={count} scenario SHA-256",
            ),
            "bytes": _integer(
                amendment_prefix["bytes"],
                f"{label} N={count} scenario bytes",
                minimum=1,
            ),
        }
    return {
        "map": {
            "path": expected_map_path,
            "sha256": str(map_record["sha256"]),
            "bytes": map_bytes,
        },
        "prefixes": prefixes,
        "capacity_bank_id": bank_id,
    }


def _validate_materialized_scenario(
    path: Path,
    *,
    expected_path: Path,
    expected_sha256: str,
    expected_bytes: int,
    robot_count: int,
    map_sha256: str,
    capacity_bank_id: str,
    label: str,
) -> None:
    _assert_equal(path, expected_path, f"{label}.path")
    guard = _EvidenceGuard()
    try:
        snapshot = guard.capture(
            path,
            label,
            maximum_bytes=expected_bytes,
        )
        _assert_equal(snapshot.size, expected_bytes, f"{label}.bytes")
        _assert_equal(snapshot.sha256, expected_sha256, f"{label}.sha256")
        scenario = _mapping(
            _load_json_snapshot(snapshot, label),
            label,
        )
        guard.reverify()
    finally:
        guard.close()
    _exact_keys(
        scenario,
        {
            "decisions",
            "metadata",
            "num_robots",
            "seed_robots",
            "waypoints",
        },
        label,
    )
    _assert_exact_json_scalar(
        scenario.get("num_robots"), robot_count, f"{label}.num_robots"
    )
    metadata = _mapping(scenario.get("metadata"), f"{label}.metadata")
    _exact_keys(
        metadata,
        {
            "capacity_bank_id",
            "capacity_bank_robot_count",
            "clearance_radius_px",
            "environment_id",
            "map_sha256",
            "minimum_interrobot_center_distance_m",
            "mission_seed",
            "prefix_robot_count",
            "robot_radius_m",
            "scenario_kind",
            "split",
            "world_size_m",
        },
        f"{label}.metadata",
    )
    for field, expected in (
        ("split", "calibration"),
        ("environment_id", "fractal_high"),
        ("map_sha256", map_sha256),
        ("prefix_robot_count", robot_count),
        ("capacity_bank_id", capacity_bank_id),
        ("scenario_kind", "capacity-prefix"),
    ):
        _assert_exact_json_scalar(
            metadata.get(field), expected, f"{label}.metadata.{field}"
        )
    bank_count = _integer(
        metadata["capacity_bank_robot_count"],
        f"{label}.metadata.capacity_bank_robot_count",
        minimum=robot_count,
    )
    if bank_count < robot_count:
        raise AnalysisError(
            f"{label}.metadata.capacity_bank_robot_count is below N"
        )
    for field in ("clearance_radius_px", "mission_seed"):
        _integer(
            metadata[field],
            f"{label}.metadata.{field}",
            minimum=0,
        )
    for field in (
        "minimum_interrobot_center_distance_m",
        "robot_radius_m",
        "world_size_m",
    ):
        _positive_finite(metadata[field], f"{label}.metadata.{field}")

    expected_robot_keys = {str(index) for index in range(robot_count)}
    decisions = _mapping(scenario.get("decisions"), f"{label}.decisions")
    waypoints = _mapping(scenario.get("waypoints"), f"{label}.waypoints")
    for field, values in (("decisions", decisions), ("waypoints", waypoints)):
        _assert_equal(
            set(values),
            expected_robot_keys,
            f"{label}.{field} robot keys",
        )
    for robot_id, decision in decisions.items():
        _integer(
            decision,
            f"{label}.decisions.{robot_id}",
            minimum=0,
            maximum=9,
        )
    for robot_id, raw_route in waypoints.items():
        if not isinstance(raw_route, list) or len(raw_route) < 2:
            raise AnalysisError(
                f"{label}.waypoints.{robot_id} must contain at least "
                "two points"
            )
        for point_index, raw_point in enumerate(raw_route):
            if not isinstance(raw_point, list) or len(raw_point) != 2:
                raise AnalysisError(
                    f"{label}.waypoints.{robot_id}[{point_index}] must be "
                    "an [x, y] point"
                )
            for coordinate_index, coordinate in enumerate(raw_point):
                _finite_number(
                    coordinate,
                    (
                        f"{label}.waypoints.{robot_id}[{point_index}]"
                        f"[{coordinate_index}]"
                    ),
                )
    seed_robots = scenario["seed_robots"]
    if not isinstance(seed_robots, list):
        raise AnalysisError(f"{label}.seed_robots must be an array")
    observed_seeds: set[str] = set()
    for index, raw_robot_id in enumerate(seed_robots):
        robot_id = _string(raw_robot_id, f"{label}.seed_robots[{index}]")
        if robot_id not in expected_robot_keys or robot_id in observed_seeds:
            raise AnalysisError(
                f"{label}.seed_robots is not a unique in-prefix subset"
            )
        observed_seeds.add(robot_id)


def _validate_build_manifest_bindings(
    artifacts: Mapping[str, Mapping[str, str]],
    methods: Mapping[str, Any],
    *,
    mode: str,
    label: str,
) -> dict[str, Any]:
    manifest_value, _ = _call_v1(
        _v1._load_json,
        Path(artifacts["build_manifest"]["path"]),
        f"{label}.build_manifest",
    )
    manifest = _mapping(manifest_value, f"{label}.build_manifest")
    _assert_equal(
        manifest.get("schema_version"),
        "dancers-comparison-build-manifest-v1",
        f"{label}.build_manifest.schema_version",
    )
    repository = _mapping(
        manifest.get("repository"), f"{label}.build_manifest.repository"
    )
    for field, expected in (
        ("reference_pin", _v1.REFERENCE_PIN),
        ("optimized_pin", _v1.OPTIMIZED_PIN),
        ("reference_applied_head", methods["reference"]["head_revision"]),
        ("reference_tree", methods["reference"]["tree_hash"]),
        ("optimized_applied_head", methods["optimized"]["head_revision"]),
        ("optimized_tree", methods["optimized"]["tree_hash"]),
    ):
        _assert_equal(
            repository.get(field),
            expected,
            f"{label}.build_manifest.repository.{field}",
        )
    _assert_exact_json_scalar(
        repository.get("remote_count"),
        0,
        f"{label}.build_manifest.repository.remote_count",
    )
    _assert_exact_json_scalar(
        repository.get("origin_present"),
        False,
        f"{label}.build_manifest.repository.origin_present",
    )
    _assert_equal(
        repository.get("common_patch_commit"),
        methods["optimized"]["head_revision"],
        f"{label}.build_manifest.repository.common_patch_commit",
    )
    toolchain = _mapping(
        manifest.get("toolchain"), f"{label}.build_manifest.toolchain"
    )
    _assert_equal(
        toolchain.get("pixi_lock_sha256"),
        artifacts["pixi_lock"]["sha256"],
        f"{label}.build_manifest.toolchain.pixi_lock_sha256",
    )
    _assert_equal(
        manifest.get("status"),
        "dual-build-parity-passed-smoke-fidelity-only",
        f"{label}.build_manifest.status",
    )
    gates = _mapping(
        manifest.get("gates"), f"{label}.build_manifest.gates"
    )
    for gate in (
        "dual_clean_builds",
        "common_registered_tests_all_four_builds",
        "smoke_semantic_trace_identity",
    ):
        _assert_equal(
            gates.get(gate), "pass", f"{label}.build_manifest.gates.{gate}"
        )
    if mode == "timing":
        _assert_equal(
            gates.get("timing_observer_compile_absence"),
            "pass",
            (
                f"{label}.build_manifest.gates."
                "timing_observer_compile_absence"
            ),
        )
    _assert_exact_json_scalar(
        gates.get("calibration_permitted"),
        True,
        f"{label}.build_manifest.gates.calibration_permitted",
    )
    builds = _mapping(
        manifest.get("builds"), f"{label}.build_manifest.builds"
    )
    repository_root = Path(artifacts["build_manifest"]["path"]).parent.parent
    _assert_equal(
        repository_root,
        _repository_root(),
        f"{label}.build_manifest repository root",
    )
    for method in METHODS:
        method_record = _mapping(
            methods[method], f"{label}.methods.{method}"
        )
        build_key = f"{method}_{mode}"
        build = _mapping(
            builds.get(build_key), f"{label}.build_manifest.{build_key}"
        )
        for field, expected in (
            ("algorithm_revision", method_record["base_revision"]),
            ("applied_head", method_record["head_revision"]),
            ("observer_compiled", mode == "fidelity"),
            ("binary_sha256", method_record["binary"]["sha256"]),
            ("registered_tests", "3/3 passed"),
        ):
            if isinstance(expected, bool):
                _assert_exact_json_scalar(
                    build.get(field),
                    expected,
                    f"{label}.build_manifest.{build_key}.{field}",
                )
            else:
                _assert_equal(
                    build.get(field),
                    expected,
                    f"{label}.build_manifest.{build_key}.{field}",
                )
        raw_binary = Path(
            _string(
                build.get("binary_path"),
                f"{label}.build_manifest.{build_key}.binary_path",
            )
        )
        resolved_binary = (
            raw_binary
            if raw_binary.is_absolute()
            else repository_root / raw_binary
        ).resolve(strict=True)
        _assert_equal(
            resolved_binary,
            Path(method_record["binary"]["path"]),
            f"{label}.build_manifest.{build_key}.binary_path",
        )
        raw_source = Path(
            _string(
                build.get("source_dir"),
                f"{label}.build_manifest.{build_key}.source_dir",
            )
        )
        resolved_source = (
            raw_source
            if raw_source.is_absolute()
            else repository_root / raw_source
        ).resolve(strict=True)
        _assert_equal(
            resolved_source,
            Path(method_record["source_dir"]),
            f"{label}.build_manifest.{build_key}.source_dir",
        )
    return manifest


def validate_run_provenance_binding(
    run: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    build_manifest: Mapping[str, Any],
    label: str,
) -> None:
    """Bind each observed attempt directly to the frozen global/method plan."""

    provenance = _mapping(run["provenance"], f"{label}.provenance")
    artifact_fields = {
        "build_manifest_sha256": "build_manifest",
        "pixi_lock_sha256": "pixi_lock",
        "protocol_sha256": "protocol",
        "experiment_manifest_sha256": "experiment_manifest",
        "analysis_revision_sha256": "analysis_revision",
    }
    for provenance_field, artifact_name in artifact_fields.items():
        _assert_equal(
            provenance[provenance_field],
            plan["artifacts"][artifact_name]["sha256"],
            f"{label}.provenance.{provenance_field} plan binding",
        )
    _assert_equal(
        provenance["runner_sha256"],
        plan["runner_sha256"],
        f"{label}.provenance.runner_sha256 plan binding",
    )
    repository = _mapping(
        build_manifest.get("repository"),
        f"{label}.build_manifest.repository",
    )
    _assert_equal(
        provenance["common_patch_sha256"],
        repository.get("common_patch_format_email_sha256"),
        f"{label}.provenance.common_patch_sha256 build binding",
    )
    _assert_equal(
        provenance["common_patch_full_index_diff_sha256"],
        repository.get("common_patch_full_index_diff_sha256"),
        (
            f"{label}.provenance.common_patch_full_index_diff_sha256 "
            "build binding"
        ),
    )
    method = str(run["method"])
    planned_method = _mapping(
        plan["methods"][method], f"{label} planned method"
    )
    for provenance_field, plan_field in (
        ("algorithm_revision", "base_revision"),
        ("source_head_revision", "head_revision"),
        ("source_tree_hash", "tree_hash"),
    ):
        _assert_equal(
            provenance[provenance_field],
            planned_method[plan_field],
            f"{label}.provenance.{provenance_field} method binding",
        )
    _assert_equal(
        provenance["binary_sha256"],
        planned_method["binary"]["sha256"],
        f"{label}.provenance.binary_sha256 method binding",
    )
    for field in (
        "phase",
        "mode",
        "maximum_peak_rss_mib",
        "power_mode",
        "thermal_policy",
        "task_validity_predicate_id",
    ):
        _assert_equal(
            provenance[field],
            plan[field],
            f"{label}.provenance.{field} plan binding",
        )
    _assert_exact_json_scalar(
        provenance["omp_threads"],
        plan["omp_threads"],
        f"{label}.provenance.omp_threads plan binding",
    )
    planned_pairs = {
        str(item["pair_id"]): item for item in plan["pairs"]
    }
    planned_pair = planned_pairs.get(str(run["pair_id"]))
    if planned_pair is None:
        raise AnalysisError(
            f"{label}.pair_id is absent from the frozen plan"
        )
    for provenance_field, artifact_name in (
        ("map_sha256", "map"),
        ("scenario_sha256", "scenario"),
    ):
        _assert_equal(
            provenance[provenance_field],
            planned_pair[artifact_name]["sha256"],
            f"{label}.provenance.{provenance_field} pair binding",
        )


def _validate_timing_fidelity_receipt_binding(
    artifacts: Mapping[str, Mapping[str, str]],
    *,
    analyzer_sha256: str,
    label: str,
) -> None:
    receipt, _ = _canonical_json_file(
        Path(artifacts["fidelity_gate_analysis"]["path"]),
        f"{label}.fidelity_gate_analysis",
    )
    expected_top = {
        "schema_version": FIDELITY_GATE_RECEIPT_SCHEMA,
        "amendment_id": AMENDMENT_ID,
        "design_role": FIDELITY_DESIGN_ROLE,
        "analyzer_sha256": analyzer_sha256,
        "planned_method_attempt_slots": 40,
        "executed_attempt_count": 40,
        "planned_pair_blocks": 20,
        "completed_attempt_count": 40,
        "valid_pair_count": 20,
        "host_valid_pair_count": 20,
        "frames_compared_per_pair": 200,
        "gate_pass": True,
    }
    _exact_keys(
        receipt,
        set(expected_top) | {"input_sessions"},
        f"{label}.fidelity_gate_analysis",
    )
    for field, expected in expected_top.items():
        _assert_equal(
            receipt[field],
            expected,
            f"{label}.fidelity_gate_analysis.{field}",
        )
    sessions = receipt["input_sessions"]
    if not isinstance(sessions, list) or len(sessions) != 1:
        raise AnalysisError(
            f"{label}.fidelity_gate_analysis must bind one session"
        )
    session = _mapping(
        sessions[0], f"{label}.fidelity_gate_analysis.input_sessions[0]"
    )
    ledger = {
        "frozen_plan_json_sha256": "fidelity_frozen_plan",
        "run_manifest_json_sha256": "fidelity_run_manifest",
        "preflight_json_sha256": "fidelity_preflight",
        "session_json_sha256": "fidelity_session",
        "runs_jsonl_sha256": "fidelity_runs",
        "pairs_jsonl_sha256": "fidelity_pairs",
    }
    _exact_keys(
        session,
        {"path", *ledger},
        f"{label}.fidelity_gate_analysis.input_sessions[0]",
    )
    _assert_equal(
        session["path"],
        str(_registered_result_root("fidelity")),
        f"{label}.fidelity_gate_analysis.input_sessions[0].path",
    )
    for field, artifact_name in ledger.items():
        _assert_equal(
            session[field],
            artifacts[artifact_name]["sha256"],
            f"{label}.fidelity_gate_analysis.input_sessions[0].{field}",
        )


def validate_plan(
    value: Any,
    *,
    label: str = "frozen_plan.json",
) -> dict[str, Any]:
    """Validate the amended design and bind every imported analyzer module."""

    plan = _mapping(value, label)
    _exact_keys(plan, PLAN_V2_KEYS, label)
    _assert_equal(plan["schema_version"], PLAN_SCHEMA, f"{label}.schema_version")
    _assert_equal(plan["amendment_id"], AMENDMENT_ID, f"{label}.amendment_id")
    if plan["mode"] not in {"fidelity", "timing"}:
        raise AnalysisError(f"{label}.mode is invalid")
    expected_role = (
        FIDELITY_DESIGN_ROLE
        if plan["mode"] == "fidelity"
        else TIMING_DESIGN_ROLE
    )
    _assert_equal(plan["design_role"], expected_role, f"{label}.design_role")
    _assert_equal(
        plan["cohort_id"], COHORT_IDS[str(plan["mode"])], f"{label}.cohort_id"
    )
    _assert_equal(
        plan["result_root"],
        str(_registered_result_root(str(plan["mode"]))),
        f"{label}.result_root",
    )
    _assert_equal(plan["phase"], "calibration", f"{label}.phase")
    expected_scalars = {
        "fixed_work_timesteps": FIXED_WORK_TIMESTEPS,
        "timestep_seconds": TIMESTEP_SECONDS,
        "idle_cooldown_seconds": 300,
        "timeout_seconds": 300.0,
        "termination_grace_seconds": 0.5,
        "minimum_free_disk_bytes": MINIMUM_FREE_DISK_BYTES,
        "maximum_peak_rss_mib": 49_152.0,
        "power_mode": POWER_MODE,
        "thermal_policy": THERMAL_POLICY,
        "task_validity_predicate_id": TASK_VALIDITY_PREDICATE_ID,
        "omp_threads": 1,
        "serial_execution": True,
        "trace_detail": "digest",
    }
    for field, expected in expected_scalars.items():
        _assert_exact_json_scalar(plan[field], expected, f"{label}.{field}")
    validate_power_policy(plan["power_policy"], f"{label}.power_policy")
    validate_process_control_policy(
        plan["process_control_policy"],
        f"{label}.process_control_policy",
    )
    repetitions = 1 if plan["mode"] == "fidelity" else 3
    warmups = 0 if plan["mode"] == "fidelity" else 1
    attempt_slots = 40 if plan["mode"] == "fidelity" else 120
    pair_blocks = 20 if plan["mode"] == "fidelity" else 60
    for field, expected in (
        ("repetitions", repetitions),
        ("warmups_per_binary_map", warmups),
        ("planned_method_attempt_slots", attempt_slots),
        ("planned_pair_blocks", pair_blocks),
    ):
        _assert_exact_json_scalar(plan[field], expected, f"{label}.{field}")
    _integer(
        plan["order_seed"],
        f"{label}.order_seed",
        minimum=1,
        maximum=(1 << 63) - 1,
    )
    _string(plan["run_cwd"], f"{label}.run_cwd")
    analyzer_sha = _call_v1(
        _v1._sha256, plan["analyzer_sha256"], f"{label}.analyzer_sha256"
    )
    runner_sha = _call_v1(
        _v1._sha256, plan["runner_sha256"], f"{label}.runner_sha256"
    )

    artifacts = _mapping(plan["artifacts"], f"{label}.artifacts")
    required_artifacts = {
        "analysis_revision",
        "analyzer_v1_base",
        "trace_compare",
        "runner",
        "runner_v1_base",
        "freeze_capacity_decision",
        "plan_freezer",
        "amendment_protocol",
        "post_outcome_amendment",
        "experiment_manifest",
        "environment_manifest",
        "environment_manifest_sidecar",
        "capacity_materializer",
        "environment_generator",
        "fractal_high_calibration_map",
        "fractal_high_calibration_bank",
        "protocol",
        "pixi_lock",
        "config_template",
        "build_manifest",
        "trigger_analysis",
        "trigger_frozen_plan",
        "trigger_session",
        "trigger_runs",
        "trigger_pairs",
    }
    if plan["mode"] == "timing":
        required_artifacts.update(
            {
                "fidelity_gate_analysis",
                "fidelity_frozen_plan",
                "fidelity_run_manifest",
                "fidelity_preflight",
                "fidelity_session",
                "fidelity_runs",
                "fidelity_pairs",
            }
        )
    _exact_keys(artifacts, required_artifacts, f"{label}.artifacts")
    normalized_artifacts = {
        name: _artifact_on_disk(
            artifact, f"{label}.artifacts.{name}"
        )
        for name, artifact in artifacts.items()
    }
    validate_plan_artifact_paths(
        normalized_artifacts,
        run_cwd=plan["run_cwd"],
        mode=str(plan["mode"]),
        label=label,
    )
    for name, expected_sha in HISTORICAL_DEPENDENCY_SHA256.items():
        _assert_equal(
            normalized_artifacts[name]["sha256"],
            expected_sha,
            f"{label}.artifacts.{name}.sha256 historical binding",
        )
    _validate_amendment_artifact_bindings(normalized_artifacts, label)
    _validate_environment_dependency_bindings(normalized_artifacts, label)
    if plan["mode"] == "timing":
        _validate_timing_fidelity_receipt_binding(
            normalized_artifacts,
            analyzer_sha256=analyzer_sha,
            label=label,
        )
    _assert_equal(
        normalized_artifacts["analysis_revision"]["sha256"],
        analyzer_sha,
        f"{label}.analyzer_sha256 binding",
    )
    _assert_equal(
        normalized_artifacts["runner"]["sha256"],
        runner_sha,
        f"{label}.runner_sha256 binding",
    )

    methods = _mapping(plan["methods"], f"{label}.methods")
    _exact_keys(methods, set(METHODS), f"{label}.methods")
    normalized_method_paths: dict[str, tuple[Path, Path]] = {}
    for method in METHODS:
        method_record = _mapping(
            methods[method], f"{label}.methods.{method}"
        )
        _exact_keys(
            method_record,
            {
                "source_dir",
                "base_revision",
                "head_revision",
                "tree_hash",
                "binary",
            },
            f"{label}.methods.{method}",
        )
        source = Path(
            _string(
                method_record["source_dir"],
                f"{label}.methods.{method}.source_dir",
            )
        )
        if not source.is_absolute() or source.is_symlink():
            raise AnalysisError(
                f"{label}.methods.{method}.source_dir must be a real "
                "absolute directory"
            )
        try:
            source = source.resolve(strict=True)
        except OSError as exc:
            raise AnalysisError(
                f"{label}.methods.{method}.source_dir cannot be resolved"
            ) from exc
        if not source.is_dir():
            raise AnalysisError(
                f"{label}.methods.{method}.source_dir is not a directory"
            )
        for field in ("base_revision", "head_revision", "tree_hash"):
            _call_v1(
                _v1._git_object,
                method_record[field],
                f"{label}.methods.{method}.{field}",
            )
        binary = _artifact_on_disk(
            method_record["binary"],
            f"{label}.methods.{method}.binary",
        )
        normalized_method_paths[method] = (source, Path(binary["path"]))
    _assert_equal(
        methods["reference"]["base_revision"],
        _v1.REFERENCE_PIN,
        f"{label}.methods.reference.base_revision",
    )
    _assert_equal(
        methods["optimized"]["base_revision"],
        _v1.OPTIMIZED_PIN,
        f"{label}.methods.optimized.base_revision",
    )
    if (
        normalized_method_paths["reference"][0]
        == normalized_method_paths["optimized"][0]
        or normalized_method_paths["reference"][1]
        == normalized_method_paths["optimized"][1]
    ):
        raise AnalysisError(f"{label} method worktrees/binaries are not distinct")
    _validate_build_manifest_bindings(
        normalized_artifacts,
        methods,
        mode=str(plan["mode"]),
        label=label,
    )

    pairs = plan["pairs"]
    if not isinstance(pairs, list):
        raise AnalysisError(f"{label}.pairs must be an array")
    if len(pairs) != 20:
        raise AnalysisError(f"{label}.pairs must contain 20 design pairs")
    observed_conditions: set[tuple[int, int]] = set()
    observed_condition_order: list[tuple[int, int]] = []
    pair_ids: set[str] = set()
    artifact_contract = _registered_pair_artifact_contract(
        normalized_artifacts,
        mode=str(plan["mode"]),
        label=label,
    )
    validated_scenario_counts: set[int] = set()
    for index, raw_pair in enumerate(pairs):
        pair_label = f"{label}.pairs[{index}]"
        pair = _mapping(raw_pair, pair_label)
        _exact_keys(
            pair,
            {
                "pair_id",
                "environment",
                "seed",
                "robot_count",
                "map",
                "scenario",
            },
            pair_label,
        )
        pair_id = _string(pair["pair_id"], f"{pair_label}.pair_id")
        if pair_id in pair_ids:
            raise AnalysisError(f"{label}.pairs has duplicate pair ID {pair_id}")
        pair_ids.add(pair_id)
        _assert_equal(
            pair["environment"], "fractal_high", f"{pair_label}.environment"
        )
        seed = _integer(pair["seed"], f"{pair_label}.seed", minimum=1)
        count = _integer(
            pair["robot_count"], f"{pair_label}.robot_count", minimum=1
        )
        _assert_equal(
            pair_id,
            f"fractal_high-calibration-seed-{seed}-n-{count}",
            f"{pair_label}.pair_id",
        )
        observed_conditions.add((count, seed))
        observed_condition_order.append((count, seed))
        for artifact_name in ("map", "scenario"):
            artifact = _mapping(
                pair[artifact_name], f"{pair_label}.{artifact_name}"
            )
            _exact_keys(
                artifact,
                {"path", "sha256", "bytes"},
                f"{pair_label}.{artifact_name}",
            )
            normalized = _artifact_on_disk(
                {"path": artifact["path"], "sha256": artifact["sha256"]},
                f"{pair_label}.{artifact_name}",
            )
            _assert_equal(
                Path(normalized["path"]).stat().st_size,
                _integer(
                    artifact["bytes"],
                    f"{pair_label}.{artifact_name}.bytes",
                    minimum=1,
                ),
                f"{pair_label}.{artifact_name}.bytes",
            )
            expected = (
                artifact_contract["map"]
                if artifact_name == "map"
                else artifact_contract["prefixes"].get(count)
            )
            if expected is None:
                raise AnalysisError(
                    f"{pair_label}.scenario has unregistered N={count}"
                )
            for field in ("path", "sha256", "bytes"):
                actual = (
                    Path(normalized["path"])
                    if field == "path"
                    else (
                        artifact["bytes"]
                        if field == "bytes"
                        else normalized["sha256"]
                    )
                )
                _assert_equal(
                    actual,
                    expected[field],
                    f"{pair_label}.{artifact_name}.{field} registration",
                )
        if count not in validated_scenario_counts:
            expected_scenario = artifact_contract["prefixes"][count]
            _validate_materialized_scenario(
                Path(pair["scenario"]["path"]),
                expected_path=expected_scenario["path"],
                expected_sha256=expected_scenario["sha256"],
                expected_bytes=expected_scenario["bytes"],
                robot_count=count,
                map_sha256=artifact_contract["map"]["sha256"],
                capacity_bank_id=artifact_contract["capacity_bank_id"],
                label=f"{pair_label}.scenario content",
            )
            validated_scenario_counts.add(count)
    expected_conditions = {
        (count, seed) for count in COUNTS for seed in SEEDS
    }
    _assert_equal(
        observed_conditions,
        expected_conditions,
        f"{label} design condition grid",
    )
    _assert_equal(
        observed_condition_order,
        [(count, seed) for count in COUNTS for seed in SEEDS],
        f"{label} design condition order",
    )
    return plan


def _validate_idle_cooldown_v2(
    value: Any,
    label: str,
) -> dict[str, Any]:
    cooldown = _mapping(value, label)
    _exact_keys(
        cooldown,
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
    _assert_equal(cooldown["required_seconds"], 300, f"{label}.required_seconds")
    before = validate_host_snapshot(cooldown["before"], f"{label}.before")
    after = validate_host_snapshot(cooldown["after"], f"{label}.after")
    _assert_equal(
        before["power_policy_evaluation"]["stage"],
        "preflight",
        f"{label}.before stage",
    )
    _assert_equal(
        after["power_policy_evaluation"]["stage"],
        "cooldown-complete",
        f"{label}.after stage",
    )
    eligible = _boolean(cooldown["eligible"], f"{label}.eligible")
    performed = _boolean(cooldown["performed"], f"{label}.performed")
    elapsed = _positive_finite(
        cooldown["elapsed_seconds"], f"{label}.elapsed_seconds"
    )
    before_battery = before["power_probe"]["battery_record"]
    after_battery = after["power_probe"]["battery_record"]
    power_stable = bool(
        before_battery is not None
        and after_battery is not None
        and after_battery["percent"] >= before_battery["percent"] - 1
        and after["power_policy_evaluation"]["cooldown_baseline_percent"]
        is None
        and after["power_policy_evaluation"][
            "attempt_boundary_before_percent"
        ]
        == before_battery["percent"]
        and after["power_policy_evaluation"][
            "drop_from_attempt_before_percentage_points"
        ]
        == before_battery["percent"] - after_battery["percent"]
    )
    expected_valid = bool(
        eligible
        and performed
        and elapsed >= 300.0
        and before["valid"]
        and after["valid"]
        and power_stable
    )
    _assert_equal(eligible, before["valid"], f"{label}.eligible")
    _assert_equal(
        _boolean(cooldown["valid"], f"{label}.valid"),
        expected_valid,
        f"{label}.valid",
    )
    if not expected_valid:
        raise AnalysisError(f"{label} violates the amended cooldown policy")
    return cooldown


def validate_manifest(
    value: Any,
    *,
    plan: Mapping[str, Any],
    label: str = "run_manifest.json",
) -> dict[str, Any]:
    """Validate the v2 execution manifest against its frozen plan."""

    manifest = _mapping(value, label)
    _exact_keys(manifest, MANIFEST_V2_KEYS, label)
    _assert_equal(
        manifest["schema_version"], PLAN_SCHEMA, f"{label}.schema_version"
    )
    _assert_equal(
        manifest["amendment_id"], AMENDMENT_ID, f"{label}.amendment_id"
    )
    for field in (
        "created_utc",
        "plan_path",
        "order_policy",
        "count_order_policy",
    ):
        _string(manifest[field], f"{label}.{field}")
    _call_v1(_v1._sha256, manifest["plan_sha256"], f"{label}.plan_sha256")
    validate_power_policy(manifest["power_policy"], f"{label}.power_policy")
    validate_process_control_policy(
        manifest["process_control_policy"],
        f"{label}.process_control_policy",
    )
    compared_fields = (
        "amendment_id",
        "design_role",
        "cohort_id",
        "result_root",
        "phase",
        "mode",
        "serial_execution",
        "order_seed",
        "fixed_work_timesteps",
        "timestep_seconds",
        "idle_cooldown_seconds",
        "repetitions",
        "warmups_per_binary_map",
        "task_validity_predicate_id",
        "maximum_peak_rss_mib",
        "minimum_free_disk_bytes",
        "power_mode",
        "power_policy",
        "process_control_policy",
        "thermal_policy",
        "planned_method_attempt_slots",
        "planned_pair_blocks",
        "analyzer_sha256",
    )
    for field in compared_fields:
        _assert_exact_json_value(
            manifest[field], plan[field], f"{label}.{field}"
        )
    preflight = validate_host_snapshot(
        manifest["preflight_host_conditions"],
        f"{label}.preflight_host_conditions",
    )
    _assert_equal(
        preflight["power_policy_evaluation"]["stage"],
        "preflight",
        f"{label}.preflight_host_conditions stage",
    )
    cooldown = _validate_idle_cooldown_v2(
        manifest["idle_cooldown"], f"{label}.idle_cooldown"
    )
    _assert_equal(
        cooldown["before"],
        preflight,
        f"{label}.idle_cooldown.before",
    )
    warmups = manifest["warmup_blocks"]
    blocks = manifest["blocks"]
    if not isinstance(warmups, list) or not isinstance(blocks, list):
        raise AnalysisError(f"{label} block schedules must be arrays")
    normalized_warmups = [
        _call_v1(
            _v1._validate_block,
            item,
            f"{label}.warmup_blocks[{index}]",
            warmup=True,
        )
        for index, item in enumerate(warmups)
    ]
    normalized_blocks = [
        _call_v1(
            _v1._validate_block,
            item,
            f"{label}.blocks[{index}]",
            warmup=False,
        )
        for index, item in enumerate(blocks)
    ]
    expected_blocks = int(plan["planned_pair_blocks"])
    if len(normalized_blocks) != expected_blocks:
        raise AnalysisError(
            f"{label}.blocks has {len(normalized_blocks)} entries; "
            f"expected {expected_blocks}"
        )
    expected_warmup_blocks = 0 if plan["mode"] == "fidelity" else 1
    if len(normalized_warmups) != expected_warmup_blocks:
        raise AnalysisError(
            f"{label}.warmup_blocks has {len(normalized_warmups)} entries; "
            f"expected {expected_warmup_blocks}"
        )
    identifiers = [
        block["block_id"] for block in normalized_warmups + normalized_blocks
    ]
    if len(identifiers) != len(set(identifiers)):
        raise AnalysisError(f"{label} has duplicate block IDs")
    planned_pairs = {
        str(item["pair_id"]): item for item in plan["pairs"]
    }
    for block_index, block in enumerate(
        normalized_warmups + normalized_blocks
    ):
        block_label = f"{label}.scheduled_blocks[{block_index}]"
        planned_pair = planned_pairs.get(str(block["pair_id"]))
        if planned_pair is None:
            raise AnalysisError(
                f"{block_label}.pair_id is absent from the frozen plan"
            )
        for field in ("environment", "seed", "robot_count"):
            _assert_exact_json_scalar(
                block[field], planned_pair[field], f"{block_label}.{field}"
            )
        if "map_sha256" in block:
            _assert_equal(
                block["map_sha256"],
                planned_pair["map"]["sha256"],
                f"{block_label}.map_sha256",
            )
    observed_cells = {
        (
            int(block["robot_count"]),
            int(block["seed"]),
            int(block["repetition"]),
        )
        for block in normalized_blocks
    }
    expected_cells = {
        (count, seed, repetition)
        for count in COUNTS
        for seed in SEEDS
        for repetition in range(1, int(plan["repetitions"]) + 1)
    }
    _assert_equal(
        observed_cells,
        expected_cells,
        f"{label} measured block grid",
    )
    _assert_equal(
        manifest["capacity_search"], None, f"{label}.capacity_search"
    )
    _assert_equal(
        manifest["capacity_evaluation"], None, f"{label}.capacity_evaluation"
    )
    return manifest


def _validate_attested_artifact(
    value: Any,
    *,
    expected_path: Path,
    expected_sha256: str,
    label: str,
) -> dict[str, Any]:
    record = _mapping(value, label)
    _exact_keys(record, {"path", "sha256", "bytes"}, label)
    _assert_equal(
        Path(_string(record["path"], f"{label}.path")),
        expected_path,
        f"{label}.path",
    )
    _assert_equal(record["sha256"], expected_sha256, f"{label}.sha256")
    observed_bytes = expected_path.stat().st_size
    _assert_equal(
        _integer(record["bytes"], f"{label}.bytes", minimum=1),
        observed_bytes,
        f"{label}.bytes",
    )
    _assert_equal(
        sha256_file(expected_path), expected_sha256, f"{label} disk SHA-256"
    )
    return record


def _expected_environment_registry(
    plan: Mapping[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    artifact = plan["artifacts"]["environment_manifest"]
    manifest_path = Path(str(artifact["path"]))
    manifest, _ = _json_artifact_file(
        manifest_path, f"{label}.environment_manifest"
    )
    cases = manifest.get("environments")
    if not isinstance(cases, list):
        raise AnalysisError(f"{label}.environment_manifest is malformed")
    root = manifest_path.parent.resolve()
    expected_cases: dict[str, Any] = {}
    for index, raw_case in enumerate(cases):
        case = _mapping(
            raw_case, f"{label}.environment_manifest.environments[{index}]"
        )
        family = _string(
            case.get("family"),
            f"{label}.environment_manifest.environments[{index}].family",
        )
        split = _string(
            case.get("split"),
            f"{label}.environment_manifest.environments[{index}].split",
        )
        key = f"{family}/{split}"
        if key in expected_cases:
            raise AnalysisError(f"{label} duplicate environment case {key}")
        registered: dict[str, Any] = {}
        for output_name, raw_record in (
            ("map", case.get("map")),
            ("scenario", case.get("scenario")),
            (
                "capacity_bank",
                _mapping(
                    case.get("capacity"),
                    f"{label}.{key}.capacity",
                ).get("bank"),
            ),
        ):
            record = _mapping(
                raw_record, f"{label}.{key}.{output_name}"
            )
            _exact_keys(
                record,
                {"path", "sha256", "bytes"},
                f"{label}.{key}.{output_name}",
            )
            registered[output_name] = {
                "path": str((root / str(record["path"])).resolve()),
                "sha256": record["sha256"],
                "bytes": record["bytes"],
            }
        capacity = _mapping(case["capacity"], f"{label}.{key}.capacity")
        prefixes = capacity.get("prefix_scenarios")
        if not isinstance(prefixes, list):
            raise AnalysisError(f"{label}.{key} prefixes are malformed")
        counts = sorted(
            _integer(
                _mapping(item, f"{label}.{key}.prefix[{prefix_index}]").get(
                    "robot_count"
                ),
                f"{label}.{key}.prefix[{prefix_index}].robot_count",
                minimum=1,
            )
            for prefix_index, item in enumerate(prefixes)
        )
        registered["registered_prefix_counts"] = counts
        expected_cases[key] = registered
    return {
        "root": str(root),
        "manifest_sha256": artifact["sha256"],
        "registered_file_count": 2 + 3 * len(expected_cases),
        "cases": expected_cases,
    }


def _validate_preflight_build_attestation(
    value: Any,
    *,
    plan: Mapping[str, Any],
    build_manifest: Mapping[str, Any],
    label: str,
) -> None:
    attestation = _mapping(value, label)
    _exact_keys(
        attestation,
        {
            "schema_version",
            "common_patch_commit",
            "common_patch_format_email_sha256",
            "common_patch_full_index_diff_sha256",
            "cross_tree_difference",
            "builds",
        },
        label,
    )
    _assert_equal(
        attestation["schema_version"],
        "dancers-comparison-build-manifest-v1",
        f"{label}.schema_version",
    )
    repository = _mapping(
        build_manifest.get("repository"), f"{label}.repository"
    )
    for field in (
        "common_patch_commit",
        "common_patch_format_email_sha256",
        "common_patch_full_index_diff_sha256",
    ):
        _assert_equal(
            attestation[field],
            repository.get(field),
            f"{label}.{field}",
        )
    cross = _mapping(
        repository.get("patched_cross_tree_difference"),
        f"{label}.patched_cross_tree_difference",
    )
    expected_paths = cross.get("paths")
    if not isinstance(expected_paths, list):
        raise AnalysisError(f"{label} cross-tree paths are malformed")
    differences = attestation["cross_tree_difference"]
    if not isinstance(differences, list) or len(differences) != len(
        expected_paths
    ):
        raise AnalysisError(f"{label}.cross_tree_difference is malformed")
    for index, (raw_difference, expected_path) in enumerate(
        zip(differences, expected_paths, strict=True)
    ):
        if (
            not isinstance(raw_difference, list)
            or len(raw_difference) != 2
            or raw_difference[0] not in {"A", "M", "D"}
        ):
            raise AnalysisError(
                f"{label}.cross_tree_difference[{index}] is malformed"
            )
        _assert_equal(
            raw_difference[1],
            expected_path,
            f"{label}.cross_tree_difference[{index}] path",
        )

    builds = _mapping(attestation["builds"], f"{label}.builds")
    expected_build_keys = {
        f"{method}_{plan['mode']}" for method in METHODS
    }
    _exact_keys(builds, expected_build_keys, f"{label}.builds")
    manifest_builds = _mapping(
        build_manifest.get("builds"), f"{label}.manifest_builds"
    )
    for method in METHODS:
        build_key = f"{method}_{plan['mode']}"
        record = _mapping(builds[build_key], f"{label}.builds.{build_key}")
        _exact_keys(
            record,
            {"binary", "observer_compiled", "auxiliary"},
            f"{label}.builds.{build_key}",
        )
        method_record = plan["methods"][method]
        _validate_attested_artifact(
            record["binary"],
            expected_path=Path(method_record["binary"]["path"]),
            expected_sha256=method_record["binary"]["sha256"],
            label=f"{label}.builds.{build_key}.binary",
        )
        _assert_exact_json_scalar(
            record["observer_compiled"],
            plan["mode"] == "fidelity",
            f"{label}.builds.{build_key}.observer_compiled",
        )
        build = _mapping(
            manifest_builds.get(build_key),
            f"{label}.manifest_builds.{build_key}",
        )
        auxiliary = _mapping(
            record["auxiliary"], f"{label}.builds.{build_key}.auxiliary"
        )
        expected_auxiliary = {
            "CMakeCache.txt": "cmake_cache_sha256",
            "compile_commands.json": "compile_commands_sha256",
            "build.log": "clean_build_log_sha256",
        }
        _exact_keys(
            auxiliary,
            set(expected_auxiliary),
            f"{label}.builds.{build_key}.auxiliary",
        )
        binary_parent = Path(method_record["binary"]["path"]).parent
        for filename, hash_field in expected_auxiliary.items():
            _validate_attested_artifact(
                auxiliary[filename],
                expected_path=binary_parent / filename,
                expected_sha256=_call_v1(
                    _v1._sha256,
                    build.get(hash_field),
                    f"{label}.{build_key}.{hash_field}",
                ),
                label=f"{label}.builds.{build_key}.{filename}",
            )


def validate_preflight_record(
    value: Any,
    *,
    plan: Mapping[str, Any],
    manifest: Mapping[str, Any],
    plan_sha256: str,
    label: str = "preflight.json",
) -> dict[str, Any]:
    """Validate every v2 preflight attestation and bind it to execution."""

    preflight = _mapping(value, label)
    _exact_keys(preflight, PREFLIGHT_V2_KEYS, label)
    for field, expected in (
        ("schema_version", PLAN_SCHEMA),
        ("amendment_id", AMENDMENT_ID),
        ("phase", plan["phase"]),
        ("mode", plan["mode"]),
        ("plan_path", manifest["plan_path"]),
        ("plan_sha256", plan_sha256),
        ("runner_sha256", plan["runner_sha256"]),
    ):
        _assert_exact_json_scalar(
            preflight[field], expected, f"{label}.{field}"
        )
    _string(preflight["verified_utc"], f"{label}.verified_utc")
    validate_power_policy(preflight["power_policy"], f"{label}.power_policy")
    validate_process_control_policy(
        preflight["process_control_policy"],
        f"{label}.process_control_policy",
    )

    artifact_attestations = _mapping(
        preflight["artifacts"], f"{label}.artifacts"
    )
    _exact_keys(
        artifact_attestations,
        set(plan["artifacts"]),
        f"{label}.artifacts",
    )
    for name, planned in plan["artifacts"].items():
        _validate_attested_artifact(
            artifact_attestations[name],
            expected_path=Path(planned["path"]),
            expected_sha256=planned["sha256"],
            label=f"{label}.artifacts.{name}",
        )

    method_attestations = _mapping(
        preflight["methods"], f"{label}.methods"
    )
    _exact_keys(method_attestations, set(METHODS), f"{label}.methods")
    expected_markers = {
        marker: plan["mode"] == "fidelity" for marker in OBSERVER_MARKERS
    }
    for method in METHODS:
        attestation = _mapping(
            method_attestations[method], f"{label}.methods.{method}"
        )
        _exact_keys(
            attestation,
            {
                "method_id",
                "source_dir",
                "base_revision",
                "head_revision",
                "tree_hash",
                "binary",
                "observer_markers",
            },
            f"{label}.methods.{method}",
        )
        planned = plan["methods"][method]
        for field, expected in (
            ("method_id", method),
            ("source_dir", planned["source_dir"]),
            ("base_revision", planned["base_revision"]),
            ("head_revision", planned["head_revision"]),
            ("tree_hash", planned["tree_hash"]),
        ):
            _assert_exact_json_scalar(
                attestation[field],
                expected,
                f"{label}.methods.{method}.{field}",
            )
        _validate_attested_artifact(
            attestation["binary"],
            expected_path=Path(planned["binary"]["path"]),
            expected_sha256=planned["binary"]["sha256"],
            label=f"{label}.methods.{method}.binary",
        )
        _assert_exact_json_value(
            attestation["observer_markers"],
            expected_markers,
            f"{label}.methods.{method}.observer_markers",
        )

    pair_attestations = _mapping(preflight["pairs"], f"{label}.pairs")
    planned_pairs = {
        str(pair["pair_id"]): pair for pair in plan["pairs"]
    }
    _exact_keys(pair_attestations, set(planned_pairs), f"{label}.pairs")
    for pair_id, planned in planned_pairs.items():
        attestation = _mapping(
            pair_attestations[pair_id], f"{label}.pairs.{pair_id}"
        )
        _exact_keys(
            attestation,
            {"map", "scenario", "registry"},
            f"{label}.pairs.{pair_id}",
        )
        for artifact_name in ("map", "scenario"):
            _validate_attested_artifact(
                attestation[artifact_name],
                expected_path=Path(planned[artifact_name]["path"]),
                expected_sha256=planned[artifact_name]["sha256"],
                label=f"{label}.pairs.{pair_id}.{artifact_name}",
            )
        _assert_exact_json_value(
            attestation["registry"],
            {
                "family": "fractal_high",
                "split": "calibration",
                "registration": "exact-N-prefix",
            },
            f"{label}.pairs.{pair_id}.registry",
        )

    _assert_exact_json_value(
        preflight["environment_registry"],
        _expected_environment_registry(plan, label=label),
        f"{label}.environment_registry",
    )
    build_value, _ = _call_v1(
        _v1._load_json,
        Path(plan["artifacts"]["build_manifest"]["path"]),
        f"{label}.build_manifest",
    )
    _validate_preflight_build_attestation(
        preflight["build_attestation"],
        plan=plan,
        build_manifest=_mapping(
            build_value, f"{label}.build_manifest"
        ),
        label=f"{label}.build_attestation",
    )
    _assert_exact_json_value(
        preflight["host_conditions"],
        manifest["preflight_host_conditions"],
        f"{label}.host_conditions",
    )
    _assert_exact_json_value(
        preflight["idle_cooldown"],
        manifest["idle_cooldown"],
        f"{label}.idle_cooldown",
    )
    controls = _mapping(
        preflight["resource_controls"], f"{label}.resource_controls"
    )
    expected_controls = {
        field: plan[field]
        for field in (
            "timestep_seconds",
            "idle_cooldown_seconds",
            "minimum_free_disk_bytes",
            "maximum_peak_rss_mib",
            "power_mode",
            "thermal_policy",
            "task_validity_predicate_id",
        )
    }
    _assert_exact_json_value(
        controls, expected_controls, f"{label}.resource_controls"
    )
    return preflight


def _json_artifact_file(path: Path, label: str) -> tuple[dict[str, Any], str]:
    """Load a SHA-bound committed JSON artifact without rewriting its style."""

    value, digest = _call_v1(_v1._load_json, path, label)
    return _mapping(value, label), digest


def _canonical_json_file(path: Path, label: str) -> tuple[dict[str, Any], str]:
    value, digest = _call_v1(_v1._load_json, path, label)
    record = _mapping(value, label)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AnalysisError(f"{label} cannot be read: {exc}") from exc
    if raw != canonical_json_bytes(record):
        raise AnalysisError(f"{label} is not canonical JSON")
    return record, digest


def _scan_session_tree(
    root: Path,
) -> dict[str, tuple[str, int, int, int, int, int]]:
    """List every session entry without following symbolic links."""

    inventory: dict[str, tuple[str, int, int, int, int, int]] = {}

    def visit(directory: Path) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as exc:
            raise AnalysisError(
                f"session inventory cannot scan {directory}: {exc}"
            ) from exc
        for entry in entries:
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise AnalysisError(
                    f"session inventory cannot stat {relative}: {exc}"
                ) from exc
            if stat.S_ISLNK(info.st_mode):
                raise AnalysisError(
                    f"session inventory contains symbolic link: {relative}"
                )
            if stat.S_ISDIR(info.st_mode):
                kind = "directory"
            elif stat.S_ISREG(info.st_mode):
                kind = "file"
            else:
                raise AnalysisError(
                    f"session inventory contains unsupported entry: {relative}"
                )
            inventory[relative] = (
                kind,
                int(info.st_dev),
                int(info.st_ino),
                int(info.st_size),
                int(info.st_mtime_ns),
                int(info.st_ctime_ns),
            )
            if kind == "directory":
                visit(path)

    visit(root)
    return inventory


def _expected_runs_csv_bytes(runs: Sequence[Mapping[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=_runner_v1.CSV_FIELDS)
    writer.writeheader()
    for run in runs:
        writer.writerow(_runner_v1._csv_row(dict(run)))
    return buffer.getvalue().encode("utf-8")


def _validate_runs_csv(
    root: Path,
    runs: Sequence[Mapping[str, Any]],
    guard: _EvidenceGuard,
) -> None:
    snapshot = guard.capture(
        root / "runs.csv",
        "runs.csv",
        maximum_bytes=64 * 1024 * 1024,
    )
    try:
        observed = snapshot.private_path.read_bytes()
    except OSError as exc:
        raise AnalysisError(f"runs.csv private copy cannot be read: {exc}") from exc
    expected = _expected_runs_csv_bytes(runs)
    if observed != expected:
        raise AnalysisError(
            "runs.csv bytes do not exactly match the runs.jsonl ledger"
        )


def _validate_session_inventory(
    root: Path,
    *,
    runs: Sequence[Mapping[str, Any]],
    pairs: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    host_abort: Mapping[str, Any] | None,
    orchestration_abort_present: bool,
    guard: _EvidenceGuard,
) -> None:
    """Require the runner's exact root, block, and attempt artifact tree."""

    expected_files = {
        "frozen_plan.json",
        "preflight.json",
        "run_manifest.json",
        "runs.jsonl",
        "pairs.jsonl",
        "runs.csv",
        "session.json",
    }
    if orchestration_abort_present:
        expected_files.add("orchestration_abort.json")
    expected_directories = {"attempts", "blocks"}
    optional_files: set[str] = set()
    optional_directories: set[str] = set()

    runs_by_block: dict[str, list[Mapping[str, Any]]] = {}
    for run in runs:
        attempt_id = str(run["attempt_id"])
        block_id = str(run["block_id"])
        attempt_relative = f"attempts/{attempt_id}"
        expected_directories.add(attempt_relative)
        attempt_files = {
            "runner_metadata.json",
            "stdout.log",
            "stderr.log",
            "result.json",
        }
        if run["mode"] == "fidelity":
            attempt_files.add("observer_metadata.json")
        for artifact_name, filename in (
            ("trace", "trace.jsonl"),
            ("summary", "summary.json"),
        ):
            if run["artifacts"][artifact_name] is not None:
                attempt_files.add(filename)
        expected_files.update(
            f"{attempt_relative}/{name}" for name in attempt_files
        )
        expected_directories.add(f"blocks/{block_id}")
        expected_files.add(f"blocks/{block_id}/config.yaml")
        runs_by_block.setdefault(block_id, []).append(run)

    if host_abort is not None:
        block_id = str(host_abort["block_id"])
        if host_abort["stage"] == "orchestration":
            optional_directories.add(f"blocks/{block_id}")
            optional_files.add(f"blocks/{block_id}/config.yaml")
        else:
            expected_directories.add(f"blocks/{block_id}")
            expected_files.add(f"blocks/{block_id}/config.yaml")

    for pair in pairs:
        block_id = str(pair["block_id"])
        expected_directories.add(f"blocks/{block_id}")
        expected_files.add(f"blocks/{block_id}/pair.json")

    for block in manifest["warmup_blocks"]:
        block_id = str(block["block_id"])
        attempts = runs_by_block.get(block_id, [])
        if len(attempts) == 2:
            expected_directories.add(f"blocks/{block_id}")
            expected_files.add(f"blocks/{block_id}/warmup.json")

    actual = _scan_session_tree(root)
    actual_files = {
        relative
        for relative, identity in actual.items()
        if identity[0] == "file"
    }
    actual_directories = {
        relative
        for relative, identity in actual.items()
        if identity[0] == "directory"
    }
    missing_files = expected_files - actual_files
    unexpected_files = actual_files - expected_files - optional_files
    if missing_files or unexpected_files:
        raise AnalysisError(
            "session file inventory mismatch: "
            f"missing={sorted(missing_files)} "
            f"unexpected={sorted(unexpected_files)}"
        )
    missing_directories = expected_directories - actual_directories
    unexpected_directories = (
        actual_directories - expected_directories - optional_directories
    )
    if missing_directories or unexpected_directories:
        raise AnalysisError(
            "session directory inventory mismatch: "
            f"missing={sorted(missing_directories)} "
            f"unexpected={sorted(unexpected_directories)}"
        )
    for relative in actual_files & optional_files:
        parent = str(Path(relative).parent).replace(os.sep, "/")
        if parent not in actual_directories:
            raise AnalysisError(
                f"optional staged file lacks its directory: {relative}"
            )
    guard.bind_inventory(root, actual)


def _session_power_valid(
    completion: Mapping[str, Any],
    cooldown: Mapping[str, Any],
    runs: Sequence[Mapping[str, Any]],
    host_contaminated_blocks: Sequence[Any],
    host_abort: Mapping[str, Any] | None,
) -> bool:
    battery = completion["power_probe"]["battery_record"]
    baseline_battery = cooldown["after"]["power_probe"]["battery_record"]
    evaluation = completion["power_policy_evaluation"]
    if battery is None or baseline_battery is None:
        return False
    return bool(
        cooldown["before"]["valid"]
        and cooldown["valid"]
        and all(run["host_attempt_valid"] is True for run in runs)
        and not host_contaminated_blocks
        and host_abort is None
        and
        completion["valid"]
        and evaluation["stage"] == "session-complete"
        and evaluation["cooldown_baseline_percent"]
        == baseline_battery["percent"]
        and evaluation["drop_from_cooldown_baseline_percentage_points"]
        == baseline_battery["percent"] - battery["percent"]
        and battery["percent"] >= baseline_battery["percent"] - 2
    )


def _is_contained_post_orchestration_failure(
    run: Mapping[str, Any],
) -> bool:
    process = run["process_control"]
    process_contained = bool(
        process["process_started"] is False
        or (
            process["child_reaped"] is True
            and process["process_group_empty_after_reap"] is True
        )
    )
    error = run["orchestration_error"]
    return bool(
        run["terminal_category"] == "measurement-wrapper-failure"
        and isinstance(error, str)
        and error.startswith("post-containment orchestration failure:")
        and run["host_attempt_valid"] is True
        and process_contained
    )


def validate_generic_abort_suffix(
    runs: Sequence[Mapping[str, Any]],
    pairs: Sequence[Mapping[str, Any]],
    manifest: Mapping[str, Any],
    diagnostic: Mapping[str, Any] | None,
    host_abort: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """Admit only the generic sealer's one terminal one/two-attempt block."""

    if diagnostic is None:
        return None
    if not runs:
        raise AnalysisError(
            "nonzero orchestration diagnostic has an empty run ledger"
        )
    if host_abort is not None:
        raise AnalysisError(
            "generic orchestration abort cannot coexist with host_abort"
        )
    last = runs[-1]
    if not _is_contained_post_orchestration_failure(last):
        raise AnalysisError(
            "generic orchestration diagnostic lacks a contained wrapper "
            "failure at the run-ledger suffix"
        )
    _assert_equal(
        last["sequence"],
        len(runs),
        "generic orchestration abort trailing sequence",
    )
    block_id = str(last["block_id"])
    block_attempts = [
        run for run in runs if str(run["block_id"]) == block_id
    ]
    block_attempts.sort(key=lambda run: int(run["order_position"]))
    if len(block_attempts) not in {1, 2}:
        raise AnalysisError(
            "generic orchestration abort terminal block must have one or "
            "two attempts"
        )
    _assert_equal(
        [int(run["order_position"]) for run in block_attempts],
        list(range(1, len(block_attempts) + 1)),
        "generic orchestration abort attempt positions",
    )
    _assert_equal(
        block_attempts[-1]["attempt_id"],
        last["attempt_id"],
        "generic orchestration abort ledger suffix",
    )
    if any(
        run["attempt_kind"] != last["attempt_kind"]
        for run in block_attempts
    ):
        raise AnalysisError(
            "generic orchestration abort terminal block mixes attempt kinds"
        )

    if last["attempt_kind"] == "warmup":
        if any(run["attempt_kind"] == "measured" for run in runs) or pairs:
            raise AnalysisError(
                "generic warmup abort has later measured evidence"
            )
        warmup_ids = {
            str(block["block_id"]) for block in manifest["warmup_blocks"]
        }
        if block_id not in warmup_ids:
            raise AnalysisError(
                "generic warmup abort is absent from the frozen schedule"
            )
    else:
        matching_pairs = [
            pair for pair in pairs if str(pair["block_id"]) == block_id
        ]
        if len(block_attempts) == 1:
            if matching_pairs:
                raise AnalysisError(
                    "one-attempt generic abort has a synthesized pair"
                )
        else:
            if len(matching_pairs) != 1 or pairs[-1] is not matching_pairs[0]:
                raise AnalysisError(
                    "two-attempt generic abort lacks exactly one terminal "
                    "synthesized pair"
                )
    return last


def validate_trailing_measured_prefix(
    measured_runs: Sequence[Mapping[str, Any]],
    referenced_attempts: set[str],
    completed_pairs: Sequence[Mapping[str, Any]],
    planned_blocks: Sequence[Mapping[str, Any]],
    *,
    total_run_count: int,
    host_abort: Mapping[str, Any] | None,
) -> Mapping[str, Any] | None:
    """Admit only one explicitly justified first-method trailing attempt."""

    by_id = {str(run["attempt_id"]): run for run in measured_runs}
    if len(by_id) != len(measured_runs):
        raise AnalysisError("measured attempts contain duplicate IDs")
    measured_ids = set(by_id)
    missing_referenced = referenced_attempts - measured_ids
    if missing_referenced:
        raise AnalysisError(
            f"pairs reference missing measured attempts: "
            f"{sorted(missing_referenced)}"
        )
    unpaired = measured_ids - referenced_attempts
    if len(unpaired) > 1:
        raise AnalysisError(
            "more than one trailing measured attempt is unpaired"
        )
    if not unpaired:
        return None
    trailing = by_id[next(iter(unpaired))]
    post_containment_failure = (
        host_abort is None
        and _is_contained_post_orchestration_failure(trailing)
    )
    if host_abort is None and not post_containment_failure:
        raise AnalysisError(
            "an unpaired measured attempt requires session.host_abort or "
            "one contained post-containment wrapper failure"
        )
    if len(completed_pairs) >= len(planned_blocks):
        raise AnalysisError(
            "trailing measured attempt exists after the full pair schedule"
        )
    next_block = planned_blocks[len(completed_pairs)]
    _assert_equal(
        trailing["block_id"],
        next_block["block_id"],
        "trailing measured attempt next block",
    )
    for field in (
        "pair_id",
        "repetition",
        "order",
        "environment",
        "seed",
        "robot_count",
    ):
        _assert_equal(
            trailing[field],
            next_block[field],
            f"trailing measured attempt.{field}",
        )
    _assert_equal(
        trailing["order_position"],
        1,
        "trailing measured attempt.order_position",
    )
    ordered_methods = (
        METHODS
        if next_block["order"] == "AB"
        else tuple(reversed(METHODS))
    )
    _assert_equal(
        trailing["method"],
        ordered_methods[0],
        "trailing measured attempt.method",
    )
    _assert_equal(
        trailing["sequence"],
        total_run_count,
        "trailing measured attempt sequence",
    )
    if post_containment_failure:
        return trailing
    assert host_abort is not None
    _assert_equal(
        host_abort["block_id"],
        next_block["block_id"],
        "session.host_abort.block_id",
    )
    abort_position = int(host_abort["order_position"])
    _assert_equal(
        host_abort["method"],
        ordered_methods[abort_position - 1],
        "session.host_abort.method",
    )
    if host_abort["stage"] == "after-attempt":
        _assert_equal(
            abort_position, 1, "after-attempt host_abort.order_position"
        )
        _assert_equal(
            trailing["host_attempt_valid"],
            False,
            "trailing measured attempt.host_attempt_valid",
        )
        _assert_equal(
            host_abort["attempt_id"],
            trailing["attempt_id"],
            "after-attempt host_abort.attempt_id",
        )
    elif host_abort["stage"] in {"before-attempt", "block-boundary"}:
        _assert_equal(
            abort_position,
            2,
            f"{host_abort['stage']} host_abort.order_position",
        )
        _assert_equal(
            trailing["host_attempt_valid"],
            True,
            "first completed method host validity",
        )
        expected_attempt_id = (
            f"{total_run_count + 1:06d}__"
            f"{next_block['block_id']}__{host_abort['method']}"
        )
        _assert_equal(
            host_abort["attempt_id"],
            expected_attempt_id,
            f"{host_abort['stage']} host_abort.attempt_id",
        )
    else:
        raise AnalysisError("trailing host_abort stage is invalid")
    return trailing


def validate_host_abort_block_prefix(
    host_abort: Mapping[str, Any],
    block: Mapping[str, Any],
    executed_attempts: Sequence[Mapping[str, Any]],
    ordered_methods: Sequence[str],
    *,
    total_run_count: int,
) -> None:
    """Bind a host abort to the sole next slot in an incomplete block."""

    if len(executed_attempts) > 2:
        raise AnalysisError("host abort block has more than two attempts")
    _assert_equal(
        host_abort["block_id"], block["block_id"], "session.host_abort.block_id"
    )
    for run in executed_attempts:
        _assert_equal(
            run["block_id"], block["block_id"], "host abort executed block"
        )
    stage = str(host_abort["stage"])
    if stage == "orchestration":
        _assert_equal(
            total_run_count,
            0,
            "orchestration host_abort total run count",
        )
        _assert_equal(
            len(executed_attempts),
            0,
            "orchestration host_abort executed attempt count",
        )
        _assert_equal(
            host_abort["order_position"],
            1,
            "orchestration host_abort.order_position",
        )
        _assert_equal(
            host_abort["method"],
            ordered_methods[0],
            "orchestration host_abort.method",
        )
        expected_attempt_id = (
            f"000001__{block['block_id']}__{host_abort['method']}"
        )
        _assert_equal(
            host_abort["attempt_id"],
            expected_attempt_id,
            "orchestration host_abort.attempt_id",
        )
        return
    if stage in {"before-attempt", "block-boundary"}:
        if len(executed_attempts) > 1:
            raise AnalysisError(
                f"{stage} host abort follows a complete block"
            )
        expected_position = len(executed_attempts) + 1
        _assert_equal(
            host_abort["order_position"],
            expected_position,
            f"{stage} host_abort.order_position",
        )
        _assert_equal(
            host_abort["method"],
            ordered_methods[expected_position - 1],
            f"{stage} host_abort.method",
        )
        if any(run["host_attempt_valid"] is not True for run in executed_attempts):
            raise AnalysisError(
                "before-attempt host abort follows an invalid completed method"
            )
        expected_attempt_id = (
            f"{total_run_count + 1:06d}__"
            f"{block['block_id']}__{host_abort['method']}"
        )
        _assert_equal(
            host_abort["attempt_id"],
            expected_attempt_id,
            f"{stage} host_abort.attempt_id",
        )
        return
    _assert_equal(
        bool(executed_attempts),
        True,
        "after-attempt host abort executed attempt presence",
    )
    run = executed_attempts[-1]
    expected_position = len(executed_attempts)
    _assert_equal(
        host_abort["order_position"],
        expected_position,
        "after-attempt host_abort.order_position",
    )
    _assert_equal(
        host_abort["method"],
        ordered_methods[expected_position - 1],
        "after-attempt host_abort.method",
    )
    _assert_equal(
        host_abort["attempt_id"],
        run["attempt_id"],
        "after-attempt host_abort.attempt_id",
    )
    _assert_equal(
        run["host_attempt_valid"],
        False,
        "after-attempt host-abort run validity",
    )
    if host_abort["failed_snapshot"] is not None:
        _assert_equal(
            host_abort["failed_snapshot"],
            run["host_conditions_after_attempt"],
            "after-attempt host_abort.failed_snapshot",
        )


def validate_session(session_root: Path) -> dict[str, Any]:
    """Validate a complete amended session and normalize registered evidence."""

    expanded_root = session_root.expanduser()
    if expanded_root.is_symlink():
        raise AnalysisError(
            f"session directory must not be a symbolic link: {session_root}"
        )
    try:
        root = expanded_root.resolve(strict=True)
    except OSError as exc:
        raise AnalysisError(
            f"session directory cannot be resolved: {session_root}: {exc}"
        ) from exc
    if root.is_symlink() or not root.is_dir():
        raise AnalysisError(f"session is not a regular directory: {root}")
    try:
        root.relative_to(_repository_root())
    except ValueError as exc:
        raise AnalysisError(
            "amended session must be inside the registered repository"
        ) from exc
    evidence_guard = _EvidenceGuard()

    plan, plan_copy_sha = _canonical_json_file(
        root / "frozen_plan.json", "frozen_plan.json"
    )
    plan = validate_plan(plan)
    _assert_equal(root, Path(plan["result_root"]), "session result_root")
    current_analyzer_sha = sha256_file(Path(__file__).resolve())
    _assert_equal(
        plan["analyzer_sha256"],
        current_analyzer_sha,
        "frozen_plan.json analyzer revision",
    )

    manifest, manifest_sha = _canonical_json_file(
        root / "run_manifest.json", "run_manifest.json"
    )
    manifest = validate_manifest(manifest, plan=plan)
    _assert_equal(
        manifest["plan_sha256"],
        plan_copy_sha,
        "run_manifest.json.plan_sha256",
    )
    source_plan_path = Path(str(manifest["plan_path"]))
    if not source_plan_path.is_absolute():
        raise AnalysisError("run_manifest.json.plan_path must be absolute")
    expected_source_plan_path = (
        _repository_root()
        / "comparison"
        / "plans"
        / "calibration"
        / "trace-v3-amended-v1"
        / RESULT_ROOT_NAMES[str(plan["mode"])]
        / "plan.json"
    )
    _assert_equal(
        source_plan_path,
        expected_source_plan_path,
        "run_manifest.json.plan_path registered source",
    )
    if source_plan_path.is_symlink():
        raise AnalysisError(
            "run_manifest.json.plan_path must not be a symbolic link"
        )
    try:
        source_plan_path.resolve(strict=True).relative_to(_repository_root())
    except (OSError, ValueError) as exc:
        raise AnalysisError(
            "run_manifest.json.plan_path escapes the registered repository"
        ) from exc
    try:
        source_plan_bytes = source_plan_path.read_bytes()
    except OSError as exc:
        raise AnalysisError(
            f"source frozen plan cannot be read: {exc}"
        ) from exc
    _assert_equal(
        source_plan_bytes,
        (root / "frozen_plan.json").read_bytes(),
        "frozen plan exact source copy",
    )

    preflight, preflight_sha = _canonical_json_file(
        root / "preflight.json", "preflight.json"
    )
    preflight = validate_preflight_record(
        preflight,
        plan=plan,
        manifest=manifest,
        plan_sha256=plan_copy_sha,
    )
    baseline_battery = manifest["idle_cooldown"]["after"]["power_probe"][
        "battery_record"
    ]
    if baseline_battery is None:
        raise AnalysisError("cooldown.after lacks its battery baseline")
    cooldown_baseline_percent = _integer(
        baseline_battery["percent"],
        "run_manifest.json.idle_cooldown.after battery percent",
        minimum=0,
        maximum=100,
    )
    build_manifest_value, _ = _call_v1(
        _v1._load_json,
        Path(plan["artifacts"]["build_manifest"]["path"]),
        "frozen build manifest",
    )
    build_manifest = _mapping(
        build_manifest_value, "frozen build manifest"
    )

    runs, runs_sha = _call_v1(
        _v1._load_jsonl, root / "runs.jsonl", "runs.jsonl"
    )
    pairs, pairs_sha = _call_v1(
        _v1._load_jsonl, root / "pairs.jsonl", "pairs.jsonl"
    )
    session, session_sha = _canonical_json_file(
        root / "session.json", "session.json"
    )
    _exact_keys(session, SESSION_V2_KEYS, "session.json")
    _assert_equal(
        session["schema_version"],
        SESSION_SCHEMA,
        "session.json.schema_version",
    )
    _string(session["completed_utc"], "session.json.completed_utc")
    host_abort = validate_host_abort(
        session["host_abort"], "session.json.host_abort"
    )
    orchestration_abort_path = root / "orchestration_abort.json"
    generic_orchestration_abort: dict[str, Any] | None = None
    if (
        host_abort is not None
        and host_abort["stage"] == "orchestration"
    ):
        orchestration_abort, _ = _canonical_json_file(
            orchestration_abort_path,
            "orchestration_abort.json",
        )
        validate_orchestration_abort(orchestration_abort, host_abort)
    elif (
        orchestration_abort_path.exists()
        or orchestration_abort_path.is_symlink()
    ):
        if not runs:
            raise AnalysisError(
                "nonzero orchestration diagnostic has an empty run ledger"
            )
        generic_orchestration_abort, _ = _canonical_json_file(
            orchestration_abort_path,
            "orchestration_abort.json",
        )
        generic_orchestration_abort = validate_nonzero_orchestration_abort(
            generic_orchestration_abort,
            _mapping(runs[-1], "runs.jsonl[-1]"),
        )
    if host_abort is not None and host_abort["failed_snapshot"] is not None:
        bind_cooldown_baseline(
            host_abort["failed_snapshot"],
            cooldown_baseline_percent,
            "session.json.host_abort.failed_snapshot",
        )
    normalized_attempt_by_id: dict[str, dict[str, Any]] = {}
    run_by_id: dict[str, dict[str, Any]] = {}
    runs_by_block: dict[str, list[dict[str, Any]]] = {}
    sequences: list[int] = []
    for index, raw_run in enumerate(runs):
        run_label = f"runs.jsonl[{index}]"
        run = _mapping(raw_run, run_label)
        normalized = validate_run_record(
            run,
            label=run_label,
            session_root=root,
            analysis_sha256=current_analyzer_sha,
            evidence_guard=evidence_guard,
        )
        validate_run_provenance_binding(
            run,
            plan=plan,
            build_manifest=build_manifest,
            label=run_label,
        )
        for boundary in ("before_attempt", "after_attempt"):
            bind_cooldown_baseline(
                run[f"host_conditions_{boundary}"],
                cooldown_baseline_percent,
                f"{run_label}.host_conditions_{boundary}",
            )
        _assert_equal(run["phase"], plan["phase"], f"{run_label}.phase")
        _assert_equal(run["mode"], plan["mode"], f"{run_label}.mode")
        attempt_id = str(run["attempt_id"])
        if attempt_id in run_by_id:
            raise AnalysisError(f"duplicate attempt ID: {attempt_id}")
        run_by_id[attempt_id] = run
        normalized_attempt_by_id[attempt_id] = normalized
        runs_by_block.setdefault(str(run["block_id"]), []).append(run)
        sequences.append(int(run["sequence"]))
    _assert_equal(
        sequences,
        list(range(1, len(runs) + 1)),
        "runs.jsonl sequence order",
    )
    generic_abort_run = validate_generic_abort_suffix(
        runs,
        pairs,
        manifest,
        generic_orchestration_abort,
        host_abort,
    )

    normalized_pairs: list[dict[str, Any]] = []
    pair_by_block: dict[str, dict[str, Any]] = {}
    referenced_attempts: set[str] = set()
    derived_host_contamination: list[str] = []
    plan_pairs = {
        str(item["pair_id"]): item for item in plan["pairs"]
    }
    manifest_blocks = {
        str(item["block_id"]): item for item in manifest["blocks"]
    }
    known_block_ids = set(manifest_blocks) | {
        str(item["block_id"]) for item in manifest["warmup_blocks"]
    }
    unknown_block_ids = set(runs_by_block) - known_block_ids
    if unknown_block_ids:
        raise AnalysisError(
            f"runs.jsonl references unknown blocks: "
            f"{sorted(unknown_block_ids)}"
        )
    warmups_valid = True
    host_abort_consumed = False
    for index, block in enumerate(manifest["warmup_blocks"]):
        warmup_label = f"warmup_blocks[{index}]"
        block_id = str(block["block_id"])
        attempts = runs_by_block.get(block_id, [])
        warmup_copy_path = root / "blocks" / block_id / "warmup.json"
        if len(attempts) < 2:
            warmups_valid = False
            if len(attempts) > 1:
                raise AnalysisError(
                    f"{warmup_label} has an invalid partial cardinality"
                )
            if any(
                run["attempt_kind"] == "measured" for run in runs
            ) or pairs:
                raise AnalysisError(
                    f"{warmup_label} is incomplete but measured work exists"
                )
            if warmup_copy_path.exists() or warmup_copy_path.is_symlink():
                raise AnalysisError(
                    f"{warmup_label} incomplete block has warmup.json"
                )
            post_containment_failure = bool(
                len(attempts) == 1
                and host_abort is None
                and _is_contained_post_orchestration_failure(attempts[0])
            )
            if host_abort is None and not post_containment_failure:
                raise AnalysisError(
                    f"{warmup_label} is incomplete without session.host_abort "
                    "or one contained post-containment wrapper failure"
                )
            if post_containment_failure and (
                generic_abort_run is None
                or generic_abort_run["attempt_id"]
                != attempts[0]["attempt_id"]
            ):
                raise AnalysisError(
                    f"{warmup_label} contained wrapper failure lacks its "
                    "bound orchestration diagnostic"
                )
            if host_abort is not None:
                validate_host_abort_block_prefix(
                    host_abort,
                    block,
                    attempts,
                    METHODS,
                    total_run_count=len(runs),
                )
                host_abort_consumed = True
            if len(attempts) == 1:
                run = attempts[0]
                _assert_equal(
                    run["attempt_kind"], "warmup", f"{warmup_label} kind"
                )
                for field in (
                    "pair_id",
                    "repetition",
                    "order",
                    "environment",
                    "seed",
                    "robot_count",
                ):
                    _assert_equal(
                        run[field], block[field], f"{warmup_label}.{field}"
                    )
                _assert_equal(
                    run["order_position"],
                    1,
                    f"{warmup_label}.order_position",
                )
                _assert_equal(
                    run["method"],
                    METHODS[0],
                    f"{warmup_label}.method",
                )
                _assert_equal(
                    run["sequence"],
                    len(runs),
                    f"{warmup_label} trailing sequence",
                )
                if run["host_attempt_valid"] is False:
                    derived_host_contamination.append(block_id)
            continue
        if len(attempts) != 2:
            raise AnalysisError(
                f"{warmup_label} does not have exactly two attempts"
            )
        by_method = {str(run["method"]): run for run in attempts}
        if set(by_method) != set(METHODS):
            raise AnalysisError(f"{warmup_label} is missing a method")
        for method in METHODS:
            run = by_method[method]
            _assert_equal(
                run["attempt_kind"], "warmup", f"{warmup_label} kind"
            )
            for field in (
                "pair_id",
                "repetition",
                "order",
                "environment",
                "seed",
                "robot_count",
            ):
                _assert_equal(
                    run[field], block[field], f"{warmup_label}.{field}"
                )
            _assert_equal(
                run["method"],
                METHODS[int(run["order_position"]) - 1],
                f"{warmup_label}.order_position",
            )
        same_config = (
            by_method["reference"]["provenance"]["config_sha256"]
            == by_method["optimized"]["provenance"]["config_sha256"]
        )
        warmup_copy, _ = _call_v1(
            _v1._load_json,
            warmup_copy_path,
            f"{warmup_label} copy",
        )
        warmup_copy = _mapping(warmup_copy, f"{warmup_label} copy")
        _exact_keys(
            warmup_copy,
            {
                "schema_version",
                "block_id",
                "pair_id",
                "environment",
                "seed",
                "robot_count",
                "map_sha256",
                "attempt_ids",
                "config_sha256_equal",
                "terminal_categories",
                "host_conditions_before",
                "host_conditions_after",
                "host_block_valid",
                "warmup_valid",
                "block_terminal_category",
            },
            f"{warmup_label} copy",
        )
        _assert_equal(
            warmup_copy["schema_version"],
            "dancers-comparison-warmup-v1",
            f"{warmup_label}.schema_version",
        )
        for field in (
            "block_id",
            "pair_id",
            "environment",
            "seed",
            "robot_count",
            "map_sha256",
        ):
            _assert_equal(
                warmup_copy[field],
                block[field],
                f"{warmup_label}.{field}",
            )
        before = validate_host_snapshot(
            warmup_copy.get("host_conditions_before"),
            f"{warmup_label}.host_conditions_before",
        )
        after = validate_host_snapshot(
            warmup_copy.get("host_conditions_after"),
            f"{warmup_label}.host_conditions_after",
        )
        bind_cooldown_baseline(
            before,
            cooldown_baseline_percent,
            f"{warmup_label}.host_conditions_before",
        )
        bind_cooldown_baseline(
            after,
            cooldown_baseline_percent,
            f"{warmup_label}.host_conditions_after",
        )
        for boundary, snapshot in (("before", before), ("after", after)):
            _assert_equal(
                snapshot["power_policy_evaluation"]["stage"],
                "block-boundary",
                f"{warmup_label}.host_conditions_{boundary} stage",
            )
            if (
                snapshot["power_policy_evaluation"][
                    "cooldown_baseline_percent"
                ]
                is None
            ):
                raise AnalysisError(
                    f"{warmup_label}.host_conditions_{boundary} "
                    "lacks cooldown baseline"
                )
        _assert_equal(
            after["power_policy_evaluation"]["cooldown_baseline_percent"],
            before["power_policy_evaluation"]["cooldown_baseline_percent"],
            f"{warmup_label} cooldown baseline",
        )
        host_valid = bool(
            before["valid"]
            and after["valid"]
            and before["power_probe"]["source"]
            == after["power_probe"]["source"]
            and before["thermal_probe"]["state"]
            == after["thermal_probe"]["state"]
        )
        _assert_equal(
            warmup_copy.get("host_block_valid"),
            host_valid,
            f"{warmup_label}.host_block_valid",
        )
        _assert_equal(
            warmup_copy["block_terminal_category"],
            (
                "completed"
                if host_valid
                else "host-power-thermal-contamination"
            ),
            f"{warmup_label}.block_terminal_category",
        )
        _assert_equal(
            warmup_copy.get("warmup_valid"),
            bool(
                same_config
                and host_valid
                and all(
                    run["terminal_category"] == "completed"
                    for run in attempts
                )
            ),
            f"{warmup_label}.warmup_valid",
        )
        expected_attempt_ids = {
            method: by_method[method]["attempt_id"] for method in METHODS
        }
        expected_terminals = {
            method: by_method[method]["terminal_category"]
            for method in METHODS
        }
        _assert_equal(
            warmup_copy.get("attempt_ids"),
            expected_attempt_ids,
            f"{warmup_label}.attempt_ids",
        )
        _assert_equal(
            warmup_copy.get("terminal_categories"),
            expected_terminals,
            f"{warmup_label}.terminal_categories",
        )
        _assert_equal(
            warmup_copy.get("config_sha256_equal"),
            same_config,
            f"{warmup_label}.config_sha256_equal",
        )
        if not host_valid:
            derived_host_contamination.append(block_id)
        warmups_valid = bool(
            warmups_valid and warmup_copy.get("warmup_valid") is True
        )
        if (
            host_abort is not None
            and not host_abort_consumed
            and host_abort["block_id"] == block_id
        ):
            validate_host_abort_block_prefix(
                host_abort,
                block,
                attempts,
                METHODS,
                total_run_count=len(runs),
            )
            _assert_equal(
                host_abort["stage"],
                "after-attempt",
                f"{warmup_label} complete host_abort stage",
            )
            _assert_equal(
                host_abort["order_position"],
                2,
                f"{warmup_label} complete host_abort order_position",
            )
            _assert_equal(
                attempts[-1]["sequence"],
                len(runs),
                f"{warmup_label} complete host_abort trailing sequence",
            )
            if any(
                run["attempt_kind"] == "measured" for run in runs
            ) or pairs:
                raise AnalysisError(
                    f"{warmup_label} host abort has later measured evidence"
                )
            if (
                attempts[-1]["host_attempt_valid"] is False
                and block_id not in derived_host_contamination
            ):
                derived_host_contamination.append(block_id)
            host_abort_consumed = True

    observed_block_order: list[str] = []
    for index, raw_pair in enumerate(pairs):
        pair_label = f"pairs.jsonl[{index}]"
        pair = _mapping(raw_pair, pair_label)
        normalized_pair = validate_pair_record(pair, label=pair_label)
        bind_cooldown_baseline(
            pair["host_conditions_before"],
            cooldown_baseline_percent,
            f"{pair_label}.host_conditions_before",
        )
        bind_cooldown_baseline(
            pair["host_conditions_after"],
            cooldown_baseline_percent,
            f"{pair_label}.host_conditions_after",
        )
        block_id = str(pair["block_id"])
        if block_id in pair_by_block:
            raise AnalysisError(f"duplicate pair block ID: {block_id}")
        pair_by_block[block_id] = pair
        observed_block_order.append(block_id)
        block = manifest_blocks.get(block_id)
        if block is None:
            raise AnalysisError(f"{pair_label} references an unknown block")
        for field in (
            "pair_id",
            "repetition",
            "order",
            "environment",
            "seed",
            "robot_count",
        ):
            _assert_equal(pair[field], block[field], f"{pair_label}.{field}")
        attempts = runs_by_block.get(block_id, [])
        if len(attempts) != 2:
            raise AnalysisError(
                f"{pair_label} does not have exactly two attempts"
            )
        by_method = {str(run["method"]): run for run in attempts}
        if set(by_method) != set(METHODS):
            raise AnalysisError(f"{pair_label} is missing a method")
        for method in METHODS:
            run = by_method[method]
            _assert_equal(
                pair["attempt_ids"][method],
                run["attempt_id"],
                f"{pair_label}.attempt_ids.{method}",
            )
            _assert_equal(
                pair["terminal_categories"][method],
                run["terminal_category"],
                f"{pair_label}.terminal_categories.{method}",
            )
            _assert_equal(
                pair["attempt_host_validity"][method],
                run["host_attempt_valid"],
                f"{pair_label}.attempt_host_validity.{method}",
            )
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
                _assert_equal(run[field], pair[field], f"{pair_label}.{field}")
            ordered_methods = (
                METHODS if pair["order"] == "AB" else tuple(reversed(METHODS))
            )
            _assert_equal(
                run["method"],
                ordered_methods[int(run["order_position"]) - 1],
                f"{pair_label}.order_position",
            )
            referenced_attempts.add(str(run["attempt_id"]))
        same_config = (
            by_method["reference"]["provenance"]["config_sha256"]
            == by_method["optimized"]["provenance"]["config_sha256"]
        )
        _assert_equal(
            pair["config_sha256_equal"],
            same_config,
            f"{pair_label}.config_sha256_equal",
        )
        registered_pair = plan_pairs.get(str(pair["pair_id"]))
        if registered_pair is None:
            raise AnalysisError(f"{pair_label} is absent from the frozen plan")
        for provenance_field, artifact_field in (
            ("map_sha256", "map"),
            ("scenario_sha256", "scenario"),
        ):
            for method in METHODS:
                _assert_equal(
                    by_method[method]["provenance"][provenance_field],
                    registered_pair[artifact_field]["sha256"],
                    f"{pair_label}.{method}.{provenance_field}",
                )

        complete = all(
            pair["terminal_categories"][method] == "completed"
            for method in METHODS
        )
        if pair["mode"] == "timing":
            for field in (
                "semantic_trace_equal",
                "frames_compared",
                "mismatch",
                "paper_metrics_equal",
                "summary_parity_fields_equal",
                "summary_mismatch",
                "task_valid",
            ):
                _assert_equal(pair[field], None, f"{pair_label}.{field}")
        elif complete:
            reference_trace = by_method["reference"]["artifacts"]["trace"]
            optimized_trace = by_method["optimized"]["artifacts"]["trace"]
            if reference_trace is None or optimized_trace is None:
                raise AnalysisError(f"{pair_label} lacks completed traces")
            try:
                reference_evidence_path = evidence_guard.trace_paths.get(
                    str(by_method["reference"]["attempt_id"])
                )
                optimized_evidence_path = evidence_guard.trace_paths.get(
                    str(by_method["optimized"]["attempt_id"])
                )
                if (
                    reference_evidence_path is None
                    or optimized_evidence_path is None
                ):
                    raise AnalysisError(
                        f"{pair_label} lacks authenticated trace snapshots"
                    )
                comparison = _trace.compare_traces(
                    reference_evidence_path,
                    optimized_evidence_path,
                )
            except (_trace.TraceFormatError, OSError, ValueError) as exc:
                raise AnalysisError(
                    f"{pair_label} trace comparison failed: {exc}"
                ) from exc
            _assert_equal(
                pair["semantic_trace_equal"],
                comparison.equal,
                f"{pair_label}.semantic_trace_equal",
            )
            _assert_equal(
                pair["frames_compared"],
                comparison.frames_compared,
                f"{pair_label}.frames_compared",
            )
            reference_metrics = by_method["reference"]["fidelity_metrics"]
            optimized_metrics = by_method["optimized"]["fidelity_metrics"]
            _assert_equal(
                pair["paper_metrics_equal"],
                reference_metrics["paper_metrics"]
                == optimized_metrics["paper_metrics"],
                f"{pair_label}.paper_metrics_equal",
            )
            _assert_equal(
                pair["summary_parity_fields_equal"],
                reference_metrics["summary_parity_fields"]
                == optimized_metrics["summary_parity_fields"],
                f"{pair_label}.summary_parity_fields_equal",
            )
            _assert_equal(
                pair["task_valid"],
                all(by_method[method]["task_valid"] is True for method in METHODS),
                f"{pair_label}.task_valid",
            )
        elif (
            generic_abort_run is not None
            and generic_abort_run["block_id"] == block_id
        ):
            for field in (
                "semantic_trace_equal",
                "frames_compared",
                "mismatch",
                "paper_metrics_equal",
                "summary_parity_fields_equal",
                "summary_mismatch",
                "task_valid",
            ):
                _assert_equal(
                    pair[field],
                    None,
                    f"{pair_label}.{field} generic abort",
                )
            _assert_equal(
                pair["pair_valid"],
                False,
                f"{pair_label}.pair_valid generic abort",
            )
        pair_copy, _ = _call_v1(
            _v1._load_json,
            root / "blocks" / block_id / "pair.json",
            f"{pair_label} copy",
        )
        _assert_equal(pair_copy, pair, f"{pair_label} copy")
        if pair["host_block_valid"] is not True:
            derived_host_contamination.append(block_id)
        if (
            host_abort is not None
            and not host_abort_consumed
            and host_abort["block_id"] == block_id
        ):
            ordered_attempts = sorted(
                attempts, key=lambda item: int(item["order_position"])
            )
            ordered_methods = (
                METHODS if pair["order"] == "AB" else tuple(reversed(METHODS))
            )
            validate_host_abort_block_prefix(
                host_abort,
                block,
                ordered_attempts,
                ordered_methods,
                total_run_count=len(runs),
            )
            _assert_equal(
                host_abort["stage"],
                "after-attempt",
                f"{pair_label} complete host_abort stage",
            )
            _assert_equal(
                host_abort["order_position"],
                2,
                f"{pair_label} complete host_abort order_position",
            )
            _assert_equal(
                index,
                len(pairs) - 1,
                f"{pair_label} host abort pair position",
            )
            _assert_equal(
                ordered_attempts[-1]["sequence"],
                len(runs),
                f"{pair_label} host abort trailing sequence",
            )
            _assert_equal(
                pair["host_block_valid"],
                False,
                f"{pair_label} host abort pair validity",
            )
            host_abort_consumed = True
        normalized_pairs.append(normalized_pair)

    planned_block_order = [
        str(item["block_id"]) for item in manifest["blocks"]
    ]
    _assert_equal(
        observed_block_order,
        planned_block_order[: len(observed_block_order)],
        "pairs.jsonl executed block prefix",
    )
    measured_runs = [
        run for run in runs if run["attempt_kind"] == "measured"
    ]
    trailing = validate_trailing_measured_prefix(
        measured_runs,
        referenced_attempts,
        pairs,
        manifest["blocks"],
        total_run_count=len(runs),
        host_abort=host_abort,
    )
    if trailing is not None:
        next_block = manifest["blocks"][len(pairs)]
        ordered_methods = (
            METHODS
            if next_block["order"] == "AB"
            else tuple(reversed(METHODS))
        )
        if host_abort is not None:
            validate_host_abort_block_prefix(
                host_abort,
                next_block,
                [trailing],
                ordered_methods,
                total_run_count=len(runs),
            )
            host_abort_consumed = True
        elif (
            generic_abort_run is None
            or generic_abort_run["attempt_id"] != trailing["attempt_id"]
        ):
            raise AnalysisError(
                "trailing contained wrapper failure lacks its bound "
                "orchestration diagnostic"
            )
        _assert_equal(
            len(runs_by_block[str(next_block["block_id"])]),
            1,
            "trailing measured block attempt count",
        )
        partial_pair_path = (
            root / "blocks" / str(next_block["block_id"]) / "pair.json"
        )
        if partial_pair_path.exists() or partial_pair_path.is_symlink():
            raise AnalysisError(
                "trailing incomplete measured block has pair.json"
            )
        if str(next_block["block_id"]) not in derived_host_contamination:
            if trailing["host_attempt_valid"] is False:
                derived_host_contamination.append(str(next_block["block_id"]))
    elif host_abort is not None and not host_abort_consumed:
        if len(pairs) >= len(manifest["blocks"]):
            raise AnalysisError(
                "session.host_abort exists after the complete block schedule"
            )
        next_block = manifest["blocks"][len(pairs)]
        ordered_methods = (
            METHODS
            if next_block["order"] == "AB"
            else tuple(reversed(METHODS))
        )
        validate_host_abort_block_prefix(
            host_abort,
            next_block,
            [],
            ordered_methods,
            total_run_count=len(runs),
        )
        host_abort_consumed = True
    if host_abort is not None and not host_abort_consumed:
        raise AnalysisError("session.host_abort is not bound to execution")
    if not runs and host_abort is None:
        raise AnalysisError(
            "a zero-run sealed session requires a bound pre-launch "
            "host_abort"
        )
    observed_attempt_cells = {
        (
            str(run["method"]),
            int(run["robot_count"]),
            int(run["seed"]),
            int(run["repetition"]),
        )
        for run in measured_runs
    }
    expected_attempt_cells = {
        (method, count, seed, repetition)
        for method in METHODS
        for count in COUNTS
        for seed in SEEDS
        for repetition in range(1, int(plan["repetitions"]) + 1)
    }
    if len(measured_runs) == int(plan["planned_method_attempt_slots"]):
        _assert_equal(
            observed_attempt_cells,
            expected_attempt_cells,
            "measured attempt grid",
        )

    for field in (
        "cohort_id",
        "result_root",
        "timestep_seconds",
        "idle_cooldown_seconds",
        "idle_cooldown",
        "minimum_free_disk_bytes",
        "maximum_peak_rss_mib",
        "power_mode",
        "thermal_policy",
        "task_validity_predicate_id",
        "power_policy",
        "process_control_policy",
        "planned_method_attempt_slots",
        "planned_pair_blocks",
    ):
        _assert_equal(session[field], manifest[field], f"session.json.{field}")
    _assert_equal(session["capacity"], None, "session.json.capacity")
    _assert_equal(
        session["capacity_evaluation"],
        None,
        "session.json.capacity_evaluation",
    )
    _assert_equal(
        _integer(session["attempt_count"], "session.json.attempt_count"),
        len(runs),
        "session.json.attempt_count",
    )
    warmup_runs = [
        run for run in runs if run["attempt_kind"] == "warmup"
    ]
    expected_warmup_attempts = len(manifest["warmup_blocks"]) * 2
    warmups_valid = bool(
        warmups_valid
        and len(warmup_runs) == expected_warmup_attempts
        and all(
            run["terminal_category"] == "completed"
            and run["host_attempt_valid"] is True
            and run["process_control"]["valid"] is True
            for run in warmup_runs
        )
    )
    _assert_equal(
        session["warmup_attempt_count"],
        len(warmup_runs),
        "session.json.warmup_attempt_count",
    )
    _assert_equal(
        _boolean(session["warmups_valid"], "session.json.warmups_valid"),
        warmups_valid,
        "session.json.warmups_valid",
    )
    _assert_equal(
        session["pair_count"], len(pairs), "session.json.pair_count"
    )
    _assert_equal(
        session["executed_method_attempt_slots"],
        len(measured_runs),
        "session.json.executed_method_attempt_slots",
    )
    completed_measured = sum(
        run["terminal_category"] == "completed" for run in measured_runs
    )
    valid_measured = sum(
        run["terminal_category"] == "completed"
        and run["host_attempt_valid"] is True
        and run["process_control"]["valid"] is True
        for run in measured_runs
    )
    for field, expected in (
        ("completed_method_attempt_slots", completed_measured),
        ("valid_method_attempt_slots", valid_measured),
        ("executed_pair_blocks", len(pairs)),
        ("valid_pair_blocks", sum(pair["pair_valid"] is True for pair in pairs)),
    ):
        _assert_equal(session[field], expected, f"session.json.{field}")
    _assert_equal(
        _boolean(
            session["all_attempts_completed"],
            "session.json.all_attempts_completed",
        ),
        bool(
            len(measured_runs) == int(plan["planned_method_attempt_slots"])
            and len(warmup_runs) == expected_warmup_attempts
            and all(run["terminal_category"] == "completed" for run in runs)
        ),
        "session.json.all_attempts_completed",
    )
    expected_stopped_early = len(pairs) < len(manifest["blocks"])
    _assert_equal(
        _boolean(session["stopped_early"], "session.json.stopped_early"),
        expected_stopped_early,
        "session.json.stopped_early",
    )
    _assert_equal(
        session["host_contaminated_blocks"],
        derived_host_contamination,
        "session.json.host_contaminated_blocks",
    )
    completion = validate_host_snapshot(
        session["session_completion_host_conditions"],
        "session.json.session_completion_host_conditions",
    )
    bind_cooldown_baseline(
        completion,
        cooldown_baseline_percent,
        "session.json.session_completion_host_conditions",
    )
    expected_session_power = _session_power_valid(
        completion,
        manifest["idle_cooldown"],
        runs,
        session["host_contaminated_blocks"],
        host_abort,
    )
    _assert_equal(
        _boolean(
            session["session_power_valid"],
            "session.json.session_power_valid",
        ),
        expected_session_power,
        "session.json.session_power_valid",
    )
    _assert_equal(
        _boolean(session["all_pairs_valid"], "session.json.all_pairs_valid"),
        bool(
            len(pairs) == int(plan["planned_pair_blocks"])
            and all(pair["pair_valid"] for pair in pairs)
            and warmups_valid
            and expected_session_power
        ),
        "session.json.all_pairs_valid",
    )

    _validate_runs_csv(root, runs, evidence_guard)
    _validate_session_inventory(
        root,
        runs=runs,
        pairs=pairs,
        manifest=manifest,
        host_abort=host_abort,
        orchestration_abort_present=bool(
            generic_orchestration_abort is not None
            or (
                host_abort is not None
                and host_abort["stage"] == "orchestration"
            )
        ),
        guard=evidence_guard,
    )

    normalized_measured = [
        normalized_attempt_by_id[str(run["attempt_id"])]
        for run in measured_runs
    ]
    return {
        "root": str(root),
        "mode": plan["mode"],
        "design_role": plan["design_role"],
        "attempts": normalized_measured,
        "pairs": normalized_pairs,
        "attempt_cells": [
            {
                "method": run["method"],
                "robot_count": run["robot_count"],
                "seed": run["seed"],
                "repetition": run["repetition"],
            }
            for run in measured_runs
        ],
        "session_power_valid": expected_session_power,
        "input_session": {
            "path": str(root),
            "frozen_plan_json_sha256": plan_copy_sha,
            "run_manifest_json_sha256": manifest_sha,
            "preflight_json_sha256": preflight_sha,
            "session_json_sha256": session_sha,
            "runs_jsonl_sha256": runs_sha,
            "pairs_jsonl_sha256": pairs_sha,
        },
        "run_manifest_json_sha256": manifest_sha,
        "frozen_plan_sha256": plan_copy_sha,
        "_evidence_guard": evidence_guard,
    }


def _percentile(sorted_values: list[float], probability: float) -> float:
    """Use the historical analyzer's registered linear interpolation."""

    try:
        return _v1._percentile(sorted_values, probability)
    except _v1.AnalysisError as exc:
        raise AnalysisError(str(exc)) from exc


def _unavailable_timing_screen(
    reasons: Sequence[str],
    *,
    observed_attempt_count: int,
) -> dict[str, Any]:
    return {
        "available": False,
        "progression_pass": False,
        "failure_reasons": sorted(set(reasons)),
        "planned_method_attempt_slots": 120,
        "observed_attempt_count": observed_attempt_count,
        "planned_pair_blocks": 60,
        "reference_count": REFERENCE_COUNT,
        "optimized_count": OPTIMIZED_COUNT,
        "seed_count": len(SEEDS),
        "repetitions_per_cell": len(TIMING_REPETITIONS),
        "bootstrap": {
            "cluster": "seed",
            "draws": BOOTSTRAP_DRAWS,
            "confidence_level": BOOTSTRAP_CONFIDENCE_LEVEL,
            "interval": "percentile-linear-interpolation",
            "rng": "MT19937",
            "seed": BOOTSTRAP_SEED,
        },
        "per_seed_ratios": None,
        "metrics": None,
        "optimized_n256_peak_rss_mib": None,
        "rss_within_limit": False,
    }


def registered_timing_screen(
    observations: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Calculate the exact registered N=256/N=192 timing screen.

    Every one of the 120 planned method/count/seed/repetition cells must be
    present exactly once, completed, host-valid, process-control-valid, and
    contain positive CPU, wall, and RSS measurements.  If any cell fails,
    no point estimate or interval is calculated.
    """

    expected = {
        (method, count, seed, repetition)
        for method in METHODS
        for count in COUNTS
        for seed in SEEDS
        for repetition in TIMING_REPETITIONS
    }
    parsed: dict[tuple[str, int, int, int], dict[str, Any]] = {}
    reasons: list[str] = []
    attempt_ids: set[str] = set()

    for index, raw in enumerate(observations):
        label = f"timing observations[{index}]"
        try:
            record = _mapping(raw, label)
            _exact_keys(record, TIMING_OBSERVATION_KEYS, label)
            attempt_id = _string(record["attempt_id"], f"{label}.attempt_id")
            method = _string(record["method"], f"{label}.method")
            count = _integer(
                record["robot_count"], f"{label}.robot_count", minimum=1
            )
            seed = _integer(record["seed"], f"{label}.seed", minimum=1)
            repetition = _integer(
                record["repetition"], f"{label}.repetition", minimum=1
            )
            key = (method, count, seed, repetition)
            if attempt_id in attempt_ids:
                reasons.append(f"duplicate-attempt-id:{attempt_id}")
            attempt_ids.add(attempt_id)
            if key in parsed:
                reasons.append(
                    "duplicate-cell:"
                    f"{method}:n{count}:seed{seed}:rep{repetition}"
                )
                continue
            if key not in expected:
                reasons.append(
                    "unexpected-cell:"
                    f"{method}:n{count}:seed{seed}:rep{repetition}"
                )
                continue
            terminal = _string(
                record["terminal_category"],
                f"{label}.terminal_category",
            )
            host_valid = _boolean(
                record["host_valid"], f"{label}.host_valid"
            )
            process_valid = _boolean(
                record["process_control_valid"],
                f"{label}.process_control_valid",
            )
            cpu = _positive_finite(
                record["total_cpu_seconds"],
                f"{label}.total_cpu_seconds",
            )
            wall = _positive_finite(
                record["wall_seconds"], f"{label}.wall_seconds"
            )
            rss = _positive_finite(
                record["peak_rss_mib"], f"{label}.peak_rss_mib"
            )
            parsed[key] = {
                "attempt_id": attempt_id,
                "terminal_category": terminal,
                "host_valid": host_valid,
                "process_control_valid": process_valid,
                "total_cpu_seconds": cpu,
                "wall_seconds": wall,
                "peak_rss_mib": rss,
            }
            if terminal != "completed":
                reasons.append(f"failed-attempt:{attempt_id}:{terminal}")
            if not host_valid:
                reasons.append(f"host-invalid:{attempt_id}")
            if not process_valid:
                reasons.append(f"process-control-invalid:{attempt_id}")
        except AnalysisError as exc:
            reasons.append(f"invalid-cell:{index}:{exc}")

    for method, count, seed, repetition in sorted(expected - set(parsed)):
        reasons.append(
            f"missing-cell:{method}:n{count}:seed{seed}:rep{repetition}"
        )
    if reasons:
        return _unavailable_timing_screen(
            reasons, observed_attempt_count=len(observations)
        )

    collapsed: dict[tuple[str, int, int], dict[str, float]] = {}
    for method in METHODS:
        for count in COUNTS:
            for seed in SEEDS:
                rows = [
                    parsed[(method, count, seed, repetition)]
                    for repetition in TIMING_REPETITIONS
                ]
                collapsed[(method, count, seed)] = {
                    metric: float(statistics.median(row[metric] for row in rows))
                    for metric in (
                        "total_cpu_seconds",
                        "wall_seconds",
                        "peak_rss_mib",
                    )
                }

    ratios: dict[str, list[float]] = {
        "total_cpu_seconds": [],
        "wall_seconds": [],
    }
    per_seed: list[dict[str, Any]] = []
    for seed in SEEDS:
        reference = collapsed[("reference", REFERENCE_COUNT, seed)]
        optimized = collapsed[("optimized", OPTIMIZED_COUNT, seed)]
        cpu_ratio = (
            optimized["total_cpu_seconds"] / reference["total_cpu_seconds"]
        )
        wall_ratio = optimized["wall_seconds"] / reference["wall_seconds"]
        if (
            not math.isfinite(cpu_ratio)
            or cpu_ratio <= 0.0
            or not math.isfinite(wall_ratio)
            or wall_ratio <= 0.0
        ):
            return _unavailable_timing_screen(
                [f"nonpositive-seed-ratio:seed{seed}"],
                observed_attempt_count=len(observations),
            )
        ratios["total_cpu_seconds"].append(cpu_ratio)
        ratios["wall_seconds"].append(wall_ratio)
        per_seed.append(
            {
                "seed": seed,
                "total_cpu_seconds_ratio": cpu_ratio,
                "wall_seconds_ratio": wall_ratio,
            }
        )

    point = {
        metric: math.exp(statistics.fmean(math.log(item) for item in values))
        for metric, values in ratios.items()
    }
    samples: dict[str, list[float]] = {
        "total_cpu_seconds": [],
        "wall_seconds": [],
    }
    rng = random.Random(BOOTSTRAP_SEED)
    for _draw in range(BOOTSTRAP_DRAWS):
        indices = [rng.randrange(len(SEEDS)) for _ in SEEDS]
        for metric, values in ratios.items():
            samples[metric].append(
                math.exp(
                    statistics.fmean(math.log(values[index]) for index in indices)
                )
            )

    metrics: dict[str, Any] = {}
    for metric in ("total_cpu_seconds", "wall_seconds"):
        ordered = sorted(samples[metric])
        interval = [
            _percentile(ordered, 0.025),
            _percentile(ordered, 0.975),
        ]
        metrics[metric] = {
            "unit": "seconds",
            "geometric_mean_optimized_n256_over_reference_n192_ratio": point[
                metric
            ],
            "ratio_ci95": interval,
            "upper_95_bound_at_most_one": interval[1] <= 1.0,
        }

    optimized_rss = max(
        parsed[("optimized", OPTIMIZED_COUNT, seed, repetition)][
            "peak_rss_mib"
        ]
        for seed in SEEDS
        for repetition in TIMING_REPETITIONS
    )
    rss_pass = optimized_rss <= MAXIMUM_PEAK_RSS_MIB
    progression_pass = bool(
        metrics["total_cpu_seconds"]["upper_95_bound_at_most_one"]
        and metrics["wall_seconds"]["upper_95_bound_at_most_one"]
        and rss_pass
    )
    return {
        "available": True,
        "progression_pass": progression_pass,
        "failure_reasons": [],
        "planned_method_attempt_slots": 120,
        "observed_attempt_count": len(observations),
        "planned_pair_blocks": 60,
        "reference_count": REFERENCE_COUNT,
        "optimized_count": OPTIMIZED_COUNT,
        "seed_count": len(SEEDS),
        "repetitions_per_cell": len(TIMING_REPETITIONS),
        "cell_collapse": "median of three within method/count/seed",
        "paired_ratio": "optimized N=256 divided by reference N=192",
        "point_estimate": "exp(mean(log(seed_ratio)))",
        "bootstrap": {
            "cluster": "seed",
            "draws": BOOTSTRAP_DRAWS,
            "confidence_level": BOOTSTRAP_CONFIDENCE_LEVEL,
            "interval": "percentile-linear-interpolation",
            "rng": "MT19937",
            "seed": BOOTSTRAP_SEED,
        },
        "per_seed_ratios": per_seed,
        "metrics": metrics,
        "optimized_n256_peak_rss_mib": optimized_rss,
        "rss_within_limit": rss_pass,
    }


def registered_fidelity_gate(
    attempts: Sequence[Mapping[str, Any]],
    pairs: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Evaluate the amended 40-attempt/20-pair trace-v3 fidelity gate."""

    attempt_failures: list[str] = []
    attempt_ids: set[str] = set()
    for index, raw in enumerate(attempts):
        label = f"fidelity attempts[{index}]"
        record = _mapping(raw, label)
        _exact_keys(record, FIDELITY_ATTEMPT_KEYS, label)
        attempt_id = _string(record["attempt_id"], f"{label}.attempt_id")
        if attempt_id in attempt_ids:
            raise AnalysisError(f"duplicate fidelity attempt ID {attempt_id}")
        attempt_ids.add(attempt_id)
        if record["terminal_category"] != "completed":
            attempt_failures.append(
                f"{attempt_id}:terminal:{record['terminal_category']}"
            )
        for field in (
            "host_valid",
            "process_control_valid",
            "task_valid",
            "resource_valid",
            "artifacts_valid",
        ):
            if not _boolean(record[field], f"{label}.{field}"):
                attempt_failures.append(f"{attempt_id}:{field}")

    pair_failures: list[str] = []
    pair_ids: set[str] = set()
    required_true = (
        "host_valid",
        "pair_valid",
        "config_sha256_equal",
        "semantic_trace_equal",
        "paper_metrics_equal",
        "summary_parity_fields_equal",
        "task_valid",
    )
    for index, raw in enumerate(pairs):
        label = f"fidelity pairs[{index}]"
        record = _mapping(raw, label)
        _exact_keys(record, FIDELITY_PAIR_KEYS, label)
        pair_id = _string(record["pair_id"], f"{label}.pair_id")
        if pair_id in pair_ids:
            raise AnalysisError(f"duplicate fidelity pair ID {pair_id}")
        pair_ids.add(pair_id)
        for field in required_true:
            if not _boolean(record[field], f"{label}.{field}"):
                pair_failures.append(f"{pair_id}:{field}")
        frames = _integer(
            record["frames_compared"],
            f"{label}.frames_compared",
            minimum=0,
        )
        if frames != 200:
            pair_failures.append(f"{pair_id}:frames_compared:{frames}")

    if len(attempts) != 40:
        attempt_failures.append(f"attempt-count:{len(attempts)}")
    if len(pairs) != 20:
        pair_failures.append(f"pair-count:{len(pairs)}")
    gate_pass = not attempt_failures and not pair_failures
    frame_values = {
        int(pair["frames_compared"])
        for pair in pairs
        if isinstance(pair.get("frames_compared"), int)
        and not isinstance(pair.get("frames_compared"), bool)
    }
    return {
        "planned_method_attempt_slots": 40,
        "executed_attempt_count": len(attempts),
        "planned_pair_blocks": 20,
        "completed_attempt_count": sum(
            attempt.get("terminal_category") == "completed"
            for attempt in attempts
        ),
        "valid_pair_count": sum(pair.get("pair_valid") is True for pair in pairs),
        "host_valid_pair_count": sum(
            pair.get("host_valid") is True for pair in pairs
        ),
        "frames_compared_per_pair": (
            next(iter(frame_values)) if len(frame_values) == 1 else None
        ),
        "gate_pass": gate_pass,
        "attempt_failures": sorted(attempt_failures),
        "pair_failures": sorted(pair_failures),
    }


def analyze_validated_sessions(
    sessions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Combine already validated raw sessions and apply session-level gates."""

    if not sessions:
        raise AnalysisError("at least one amended session is required")
    if len(sessions) != 1:
        raise AnalysisError(
            "the amended design is one immutable session; pooling or "
            "resuming partial cohorts is prohibited"
        )
    modes = {str(session["mode"]) for session in sessions}
    if len(modes) != 1:
        raise AnalysisError("input sessions mix fidelity and timing modes")
    mode = next(iter(modes))
    expected_role = (
        FIDELITY_DESIGN_ROLE if mode == "fidelity" else TIMING_DESIGN_ROLE
    )
    roles = {str(session["design_role"]) for session in sessions}
    _assert_equal(roles, {expected_role}, "session design roles")
    attempts = [
        attempt
        for session in sessions
        for attempt in session["attempts"]
    ]
    pairs = [pair for session in sessions for pair in session["pairs"]]
    invalid_power_sessions = sorted(
        str(session["input_session"]["path"])
        for session in sessions
        if session["session_power_valid"] is not True
    )
    power_failures = [
        f"session-power-invalid:{path}" for path in invalid_power_sessions
    ]

    fidelity_gate: dict[str, Any] | None = None
    timing_screen: dict[str, Any] | None = None
    if mode == "fidelity":
        fidelity_gate = registered_fidelity_gate(attempts, pairs)
        observed_cells = [
            (
                str(cell["method"]),
                int(cell["robot_count"]),
                int(cell["seed"]),
                int(cell["repetition"]),
            )
            for session in sessions
            for cell in session["attempt_cells"]
        ]
        expected_cells = {
            (method, count, seed, 1)
            for method in METHODS
            for count in COUNTS
            for seed in SEEDS
        }
        if (
            len(observed_cells) != len(expected_cells)
            or set(observed_cells) != expected_cells
        ):
            fidelity_gate["gate_pass"] = False
            fidelity_gate["attempt_failures"] = sorted(
                set(fidelity_gate["attempt_failures"])
                | {"measured-attempt-grid-mismatch"}
            )
        if power_failures:
            fidelity_gate["gate_pass"] = False
            fidelity_gate["attempt_failures"] = sorted(
                set(fidelity_gate["attempt_failures"]) | set(power_failures)
            )
    elif mode == "timing":
        if power_failures:
            timing_screen = _unavailable_timing_screen(
                power_failures,
                observed_attempt_count=len(attempts),
            )
        else:
            timing_screen = registered_timing_screen(attempts)
    else:
        raise AnalysisError(f"unsupported amended mode: {mode}")
    return {
        "mode": mode,
        "design_role": expected_role,
        "input_sessions": [
            session["input_session"] for session in sessions
        ],
        "fidelity_gate": fidelity_gate,
        "timing_screen": timing_screen,
    }


def fidelity_gate_receipt(
    *,
    analyzer_sha256: str,
    input_sessions: Sequence[Mapping[str, Any]],
    gate: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the exact external receipt consumed by the timing-plan freezer."""

    if not gate.get("gate_pass"):
        raise AnalysisError("cannot issue a fidelity receipt for a failed gate")
    normalized_sessions: list[dict[str, str]] = []
    required_session_keys = {
        "path",
        "frozen_plan_json_sha256",
        "run_manifest_json_sha256",
        "preflight_json_sha256",
        "session_json_sha256",
        "runs_jsonl_sha256",
        "pairs_jsonl_sha256",
    }
    for index, raw in enumerate(input_sessions):
        label = f"input_sessions[{index}]"
        record = _mapping(raw, label)
        _exact_keys(record, required_session_keys, label)
        normalized_record = {
            "path": _string(record["path"], f"{label}.path"),
            "frozen_plan_json_sha256": _call_v1(
                _v1._sha256,
                record["frozen_plan_json_sha256"],
                f"{label}.frozen_plan_json_sha256",
            ),
            "run_manifest_json_sha256": _call_v1(
                _v1._sha256,
                record["run_manifest_json_sha256"],
                f"{label}.run_manifest_json_sha256",
            ),
            "preflight_json_sha256": _call_v1(
                _v1._sha256,
                record["preflight_json_sha256"],
                f"{label}.preflight_json_sha256",
            ),
            "session_json_sha256": _call_v1(
                _v1._sha256,
                record["session_json_sha256"],
                f"{label}.session_json_sha256",
            ),
            "runs_jsonl_sha256": _call_v1(
                _v1._sha256,
                record["runs_jsonl_sha256"],
                f"{label}.runs_jsonl_sha256",
            ),
            "pairs_jsonl_sha256": _call_v1(
                _v1._sha256,
                record["pairs_jsonl_sha256"],
                f"{label}.pairs_jsonl_sha256",
            ),
        }
        normalized_sessions.append(normalized_record)
    receipt = {
        "schema_version": FIDELITY_GATE_RECEIPT_SCHEMA,
        "amendment_id": AMENDMENT_ID,
        "design_role": FIDELITY_DESIGN_ROLE,
        "analyzer_sha256": _call_v1(
            _v1._sha256, analyzer_sha256, "analyzer_sha256"
        ),
        "input_sessions": normalized_sessions,
        "planned_method_attempt_slots": gate["planned_method_attempt_slots"],
        "executed_attempt_count": gate["executed_attempt_count"],
        "planned_pair_blocks": gate["planned_pair_blocks"],
        "completed_attempt_count": gate["completed_attempt_count"],
        "valid_pair_count": gate["valid_pair_count"],
        "host_valid_pair_count": gate["host_valid_pair_count"],
        "frames_compared_per_pair": gate["frames_compared_per_pair"],
        "gate_pass": True,
    }
    canonical_json_bytes(receipt)
    return receipt


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--session",
        type=Path,
        required=True,
        help="the single complete immutable amended v2 session directory",
    )
    parser.add_argument(
        "--fidelity-gate-receipt-output",
        type=Path,
        help=(
            "write the exact timing-freezer receipt after a complete "
            "fidelity gate pass"
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="new canonical JSON output path",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    sessions: list[dict[str, Any]] = []
    output = args.output.resolve()
    if output.exists() or output.is_symlink():
        print(f"analysis error: output already exists: {output}", file=sys.stderr)
        return 2
    receipt_output = (
        None
        if args.fidelity_gate_receipt_output is None
        else args.fidelity_gate_receipt_output.resolve()
    )
    if receipt_output is not None and (
        receipt_output.exists() or receipt_output.is_symlink()
    ):
        print(
            f"analysis error: receipt output already exists: {receipt_output}",
            file=sys.stderr,
        )
        return 2
    try:
        sessions = [validate_session(args.session)]
        combined = analyze_validated_sessions(sessions)
        mode = str(combined["mode"])
        fidelity_gate = combined["fidelity_gate"]
        timing_screen = combined["timing_screen"]
        input_sessions = combined["input_sessions"]
        receipt: dict[str, Any] | None = None
        analyzer_sha = sha256_file(Path(__file__).resolve())
        if mode == "fidelity":
            assert fidelity_gate is not None
            if receipt_output is not None and fidelity_gate["gate_pass"]:
                receipt = fidelity_gate_receipt(
                    analyzer_sha256=analyzer_sha,
                    input_sessions=input_sessions,
                    gate=fidelity_gate,
                )
        else:
            if receipt_output is not None:
                raise AnalysisError(
                    "--fidelity-gate-receipt-output is fidelity-only"
                )
        result = {
            "schema_version": ANALYSIS_SCHEMA,
            "analysis_revision_sha256": analyzer_sha,
            "amendment_id": AMENDMENT_ID,
            "design_role": combined["design_role"],
            "input_sessions": input_sessions,
            "fidelity_gate": fidelity_gate,
            "timing_screen": timing_screen,
        }
        for session in sessions:
            guard = session.get("_evidence_guard")
            if isinstance(guard, _EvidenceGuard):
                guard.reverify()
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("xb") as handle:
            handle.write(canonical_json_bytes(result))
            handle.flush()
        if receipt_output is not None:
            if receipt is not None:
                receipt_output.parent.mkdir(parents=True, exist_ok=True)
                with receipt_output.open("xb") as handle:
                    handle.write(canonical_json_bytes(receipt))
                    handle.flush()
    except (AnalysisError, OSError) as exc:
        print(f"analysis error: {exc}", file=sys.stderr)
        return 2
    finally:
        for session in sessions:
            guard = session.get("_evidence_guard")
            if isinstance(guard, _EvidenceGuard):
                guard.close()
    print(f"wrote {output} (sha256={sha256_file(output)})")
    scientific_pass = bool(
        fidelity_gate["gate_pass"]
        if fidelity_gate is not None
        else timing_screen is not None
        and timing_screen["progression_pass"]
    )
    if not scientific_pass:
        if receipt_output is not None:
            print(
                "scientific gate failed; analysis written and no fidelity "
                "receipt emitted",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
