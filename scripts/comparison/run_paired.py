#!/usr/bin/env python3
"""Fail-closed serial runner for the preregistered DANCeRS comparison.

The runner consumes a fully hashed JSON plan.  It does not discover binaries,
change seeds, generate scenarios, resume an old run, or overwrite output.  A
timing plan clears all observer variables and requires observer-free binaries;
a fidelity plan requires observer-enabled binaries and compares their traces.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import random
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterable, Sequence

try:
    from scripts.comparison.trace_compare import (
        TraceFormatError,
        TraceReader,
        compare_traces,
        first_json_difference,
    )
    from scripts.comparison.freeze_capacity_decision import (
        DecisionError as CapacityDecisionError,
        load_capacity_decision,
    )
except ModuleNotFoundError:  # Direct execution from scripts/comparison.
    from trace_compare import (
        TraceFormatError,
        TraceReader,
        compare_traces,
        first_json_difference,
    )
    from freeze_capacity_decision import (
        DecisionError as CapacityDecisionError,
        load_capacity_decision,
    )


PLAN_SCHEMA = "dancers-laptop-comparison-plan-v1"
BUILD_MANIFEST_SCHEMA = "dancers-comparison-build-manifest-v1"
RESULT_SCHEMA = "dancers-laptop-comparison-attempt-v1"
PAIR_SCHEMA = "dancers-laptop-comparison-pair-v1"
REFERENCE_PIN = "986f47deec5b079663c7d7f6120bfc20f0467901"
OPTIMIZED_PIN = "99364a41f1535a4bb0eddbe5a10030127ea77086"
PHASE_SEEDS = {
    "calibration": range(1, 11),
    "validation": range(11, 31),
    "audit": range(31, 51),
}
PHASE_SPLIT = {
    "calibration": "calibration",
    "validation": "validation",
    "audit": "validation",
}
TASK_VALIDITY_PREDICATE_ID = "fixed-horizon-safe-navigation-v1"
MINIMUM_FREE_DISK_BYTES = 21_474_836_480
MAXIMUM_PEAK_RSS_MIB = 49_152.0
TRACE_SCHEMA = "dancers-comparison-trace-v3"
TRACE_DIGEST_STATE_SCHEMA = "robot-semantic-digest-v2"
TRACE_DIGEST_ALGORITHM = (
    "SHA-256 over robot-semantic-digest-v2 canonical typed binary encoding"
)
OBSERVER_ENVIRONMENT = {
    "DANCERS_TRACE_OUT",
    "DANCERS_SUMMARY_OUT",
    "DANCERS_RUN_METADATA",
    "DANCERS_REQUIRE_PROVENANCE",
    "DANCERS_TRACE_DETAIL",
}
OBSERVER_BINARY_MARKERS = (
    b"DANCERS_TRACE_OUT",
    TRACE_SCHEMA.encode("ascii"),
    TRACE_DIGEST_STATE_SCHEMA.encode("ascii"),
    TRACE_DIGEST_ALGORITHM.encode("ascii"),
)
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
CAPACITY_EVALUATION_KEYS = {
    "environment",
    "role",
    "reference_anchor_n",
    "candidate_n_star",
    "next_grid_point",
    "grid",
}
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
TIMESTEP_RE = re.compile(r"(?m)^timesteps:\s*(\d+)\b")
ROBOT_COUNT_RE = re.compile(r"Final robot count:\s*\[(\d+)\]")
SUMMARY_SCHEMA = "dancers-comparison-summary-v1"
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


class RunnerError(RuntimeError):
    """A preflight, plan-integrity, or orchestration failure."""


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


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require_regular_file(path: Path, label: str) -> None:
    if path.is_symlink():
        raise RunnerError(f"{label} must not be a symbolic link: {path}")
    if not path.is_file():
        raise RunnerError(f"{label} is not a regular file: {path}")


def _resolve_path(value: Any, base: Path, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise RunnerError(f"{label} must be a non-empty path string")
    path = Path(value)
    return (path if path.is_absolute() else base / path).resolve()


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RunnerError(f"{label} must be an object")
    return value


def _require_exact_keys(
    value: dict[str, Any],
    required: set[str],
    optional: set[str],
    label: str,
) -> None:
    missing = required - value.keys()
    unknown = value.keys() - required - optional
    if missing:
        raise RunnerError(f"{label} is missing keys: {sorted(missing)}")
    if unknown:
        raise RunnerError(f"{label} has unknown keys: {sorted(unknown)}")


def _positive_int(value: Any, label: str, *, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
        or value > maximum
    ):
        raise RunnerError(f"{label} must be an integer in [1, {maximum}]")
    return value


def _positive_float(value: Any, label: str, *, maximum: float) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0 < float(value) <= maximum
    ):
        raise RunnerError(f"{label} must be in (0, {maximum}]")
    return float(value)


def _bounded_string(value: Any, label: str, *, maximum: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or "\n" in value
        or "\r" in value
    ):
        raise RunnerError(
            f"{label} must be a non-empty single-line string of at most "
            f"{maximum} characters"
        )
    return value


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise RunnerError(f"{label} must be 64 lower-case hexadecimal digits")
    return value


def _git_object(value: Any, label: str) -> str:
    if not isinstance(value, str) or GIT_OBJECT_RE.fullmatch(value) is None:
        raise RunnerError(f"{label} must be a full Git object identifier")
    return value


@dataclass(frozen=True)
class Artifact:
    path: Path
    sha256: str
    label: str

    @classmethod
    def parse(
        cls, value: Any, base: Path, label: str
    ) -> "Artifact":
        record = _require_mapping(value, label)
        _require_exact_keys(record, {"path", "sha256"}, set(), label)
        return cls(
            path=_resolve_path(record["path"], base, f"{label}.path"),
            sha256=_sha256(record["sha256"], f"{label}.sha256"),
            label=label,
        )

    def verify(self) -> dict[str, Any]:
        _require_regular_file(self.path, self.label)
        actual = sha256_file(self.path)
        if actual != self.sha256:
            raise RunnerError(
                f"{self.label} SHA-256 mismatch: expected {self.sha256}, "
                f"observed {actual}"
            )
        stat = self.path.stat()
        return {
            "path": str(self.path),
            "sha256": actual,
            "bytes": stat.st_size,
        }


@dataclass(frozen=True)
class Method:
    method_id: str
    source_dir: Path
    base_revision: str
    head_revision: str
    tree_hash: str
    binary: Artifact


@dataclass(frozen=True)
class Pair:
    pair_id: str
    environment: str
    seed: int
    robot_count: int
    map_artifact: Artifact
    scenario: Artifact
    config_overrides: dict[str, bool | int | float | str]


@dataclass(frozen=True)
class CapacitySearch:
    environment: str
    reference_anchor_n: int
    grid: tuple[int, ...]
    max_points: int


@dataclass(frozen=True)
class CapacityEvaluation:
    environment: str
    role: str
    reference_anchor_n: int
    candidate_n_star: int
    next_grid_point: int | None
    grid: tuple[int, ...]

    def record(self) -> dict[str, Any]:
        return {
            "environment": self.environment,
            "role": self.role,
            "reference_anchor_n": self.reference_anchor_n,
            "candidate_n_star": self.candidate_n_star,
            "next_grid_point": self.next_grid_point,
            "grid": list(self.grid),
        }


@dataclass(frozen=True)
class Plan:
    path: Path
    plan_sha256: str
    phase: str
    mode: str
    fixed_work_timesteps: int
    timestep_seconds: float
    idle_cooldown_seconds: int
    timeout_seconds: float
    termination_grace_seconds: float
    repetitions: int
    warmups_per_binary_map: int
    omp_threads: int
    order_seed: int
    minimum_free_disk_bytes: int
    maximum_peak_rss_mib: float
    power_mode: str
    thermal_policy: str
    task_validity_predicate_id: str
    trace_detail: str
    run_cwd: Path
    artifacts: dict[str, Artifact]
    methods: dict[str, Method]
    pairs: tuple[Pair, ...]
    capacity: CapacitySearch | None
    capacity_evaluation: CapacityEvaluation | None
    runner_sha256: str
    build_manifest: dict[str, Any]
    environment_manifest: dict[str, Any]


def _load_json(path: Path, label: str) -> tuple[Any, bytes]:
    _require_regular_file(path, label)
    data = path.read_bytes()
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKey, ValueError) as exc:
        raise RunnerError(f"{label} is not strict JSON: {exc}") from exc
    return value, data


def _parse_method(
    method_id: str, value: Any, base: Path
) -> Method:
    label = f"methods.{method_id}"
    record = _require_mapping(value, label)
    _require_exact_keys(
        record,
        {
            "source_dir",
            "base_revision",
            "head_revision",
            "tree_hash",
            "binary",
        },
        set(),
        label,
    )
    source_dir = _resolve_path(record["source_dir"], base, f"{label}.source_dir")
    if source_dir.is_symlink() or not source_dir.is_dir():
        raise RunnerError(f"{label}.source_dir must be a real directory")
    return Method(
        method_id=method_id,
        source_dir=source_dir,
        base_revision=_git_object(
            record["base_revision"], f"{label}.base_revision"
        ),
        head_revision=_git_object(
            record["head_revision"], f"{label}.head_revision"
        ),
        tree_hash=_git_object(record["tree_hash"], f"{label}.tree_hash"),
        binary=Artifact.parse(record["binary"], base, f"{label}.binary"),
    )


def _parse_scalar(value: Any, label: str) -> bool | int | float | str:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value == value and abs(value) != float("inf"):
        return value
    if isinstance(value, str) and "\n" not in value and "\r" not in value:
        return value
    raise RunnerError(f"{label} must be a finite scalar without newlines")


def _parse_pair(value: Any, base: Path, phase: str, index: int) -> Pair:
    label = f"pairs[{index}]"
    record = _require_mapping(value, label)
    _require_exact_keys(
        record,
        {
            "pair_id",
            "environment",
            "seed",
            "robot_count",
            "map",
            "scenario",
        },
        {"config_overrides"},
        label,
    )
    pair_id = record["pair_id"]
    environment = record["environment"]
    if not isinstance(pair_id, str) or IDENTIFIER_RE.fullmatch(pair_id) is None:
        raise RunnerError(f"{label}.pair_id is not a safe identifier")
    if (
        not isinstance(environment, str)
        or IDENTIFIER_RE.fullmatch(environment) is None
    ):
        raise RunnerError(f"{label}.environment is not a safe identifier")
    seed = _positive_int(record["seed"], f"{label}.seed", maximum=2**31 - 1)
    if seed not in PHASE_SEEDS[phase]:
        allowed = PHASE_SEEDS[phase]
        raise RunnerError(
            f"{label}.seed={seed} is outside the locked {phase} range "
            f"{allowed.start}-{allowed.stop - 1}"
        )
    robot_count = _positive_int(
        record["robot_count"], f"{label}.robot_count", maximum=100_000
    )
    overrides_raw = record.get("config_overrides", {})
    overrides = _require_mapping(
        overrides_raw, f"{label}.config_overrides"
    )
    collision = PROTECTED_CONFIG_KEYS & overrides.keys()
    if collision:
        raise RunnerError(
            f"{label}.config_overrides cannot replace protected keys: "
            f"{sorted(collision)}"
        )
    return Pair(
        pair_id=pair_id,
        environment=environment,
        seed=seed,
        robot_count=robot_count,
        map_artifact=Artifact.parse(record["map"], base, f"{label}.map"),
        scenario=Artifact.parse(
            record["scenario"], base, f"{label}.scenario"
        ),
        config_overrides={
            key: _parse_scalar(item, f"{label}.config_overrides.{key}")
            for key, item in overrides.items()
        },
    )


def load_plan(path: Path | str) -> Plan:
    plan_path = Path(path).resolve()
    raw, data = _load_json(plan_path, "comparison plan")
    record = _require_mapping(raw, "comparison plan")
    _require_exact_keys(
        record,
        {
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
            "runner_sha256",
            "run_cwd",
            "artifacts",
            "methods",
            "pairs",
        },
        {
            "termination_grace_seconds",
            "trace_detail",
            "capacity_search",
            "capacity_evaluation",
        },
        "comparison plan",
    )
    if record["schema_version"] != PLAN_SCHEMA:
        raise RunnerError(
            f"unsupported plan schema {record['schema_version']!r}"
        )
    phase = record["phase"]
    if phase not in PHASE_SEEDS:
        raise RunnerError(f"phase must be one of {sorted(PHASE_SEEDS)}")
    mode = record["mode"]
    if mode not in {"timing", "fidelity"}:
        raise RunnerError("mode must be 'timing' or 'fidelity'")
    fixed_work = _positive_int(
        record["fixed_work_timesteps"],
        "fixed_work_timesteps",
        maximum=1_000_000,
    )
    if fixed_work != 100:
        raise RunnerError(
            "fixed_work_timesteps must be the preregistered value 100"
        )
    timestep_seconds = _positive_float(
        record["timestep_seconds"],
        "timestep_seconds",
        maximum=86_400,
    )
    if timestep_seconds != 0.1:
        raise RunnerError(
            "timestep_seconds must be the preregistered value 0.1"
        )
    idle_cooldown_seconds = _positive_int(
        record["idle_cooldown_seconds"],
        "idle_cooldown_seconds",
        maximum=86_400,
    )
    if idle_cooldown_seconds != 300:
        raise RunnerError(
            "idle_cooldown_seconds must be the preregistered value 300"
        )
    omp_threads = _positive_int(
        record["omp_threads"], "omp_threads", maximum=1024
    )
    if omp_threads != 1:
        raise RunnerError("omp_threads must be the preregistered value 1")
    repetitions = _positive_int(
        record["repetitions"], "repetitions", maximum=100
    )
    required_repetitions = 3 if mode == "timing" else 1
    if repetitions != required_repetitions:
        raise RunnerError(
            f"{mode} repetitions must be the preregistered value "
            f"{required_repetitions}"
        )
    warmups = _positive_int(
        record["warmups_per_binary_map"],
        "warmups_per_binary_map",
        maximum=10,
    )
    if warmups != 1:
        raise RunnerError(
            "warmups_per_binary_map must be the preregistered value 1"
        )
    order_seed = _positive_int(
        record["order_seed"], "order_seed", maximum=2**63 - 1
    )
    minimum_free_disk_bytes = _positive_int(
        record["minimum_free_disk_bytes"],
        "minimum_free_disk_bytes",
        maximum=2**63 - 1,
    )
    if minimum_free_disk_bytes != MINIMUM_FREE_DISK_BYTES:
        raise RunnerError(
            "minimum_free_disk_bytes must be the preregistered 20 GiB "
            "safeguard"
        )
    maximum_peak_rss_mib = _positive_float(
        record["maximum_peak_rss_mib"],
        "maximum_peak_rss_mib",
        maximum=float(2**53),
    )
    if maximum_peak_rss_mib != MAXIMUM_PEAK_RSS_MIB:
        raise RunnerError(
            "maximum_peak_rss_mib must be the preregistered 49152 MiB "
            "safeguard"
        )
    power_mode = _bounded_string(record["power_mode"], "power_mode")
    thermal_policy = _bounded_string(
        record["thermal_policy"], "thermal_policy"
    )
    task_validity_predicate_id = _bounded_string(
        record["task_validity_predicate_id"],
        "task_validity_predicate_id",
    )
    if task_validity_predicate_id != TASK_VALIDITY_PREDICATE_ID:
        raise RunnerError(
            "task_validity_predicate_id must be "
            f"{TASK_VALIDITY_PREDICATE_ID!r}"
        )
    runner_sha256 = _sha256(record["runner_sha256"], "runner_sha256")
    timeout = _positive_float(
        record["timeout_seconds"], "timeout_seconds", maximum=86_400
    )
    grace = _positive_float(
        record.get("termination_grace_seconds", 0.5),
        "termination_grace_seconds",
        maximum=10,
    )
    trace_detail = record.get("trace_detail", "digest")
    if trace_detail != "digest":
        raise RunnerError(
            "trace_detail must be digest for registered scientific runs; "
            "full and positions traces are diagnostic-only"
        )
    base = plan_path.parent
    run_cwd = _resolve_path(record["run_cwd"], base, "run_cwd")
    if run_cwd.is_symlink() or not run_cwd.is_dir():
        raise RunnerError("run_cwd must be a real directory")

    artifacts_raw = _require_mapping(record["artifacts"], "artifacts")
    _require_exact_keys(
        artifacts_raw,
        {
            "pixi_lock",
            "build_manifest",
            "environment_manifest",
            "config_template",
            "protocol",
            "experiment_manifest",
            "analysis_revision",
        },
        {"capacity_decision"},
        "artifacts",
    )
    artifacts = {
        key: Artifact.parse(value, base, f"artifacts.{key}")
        for key, value in artifacts_raw.items()
    }
    methods_raw = _require_mapping(record["methods"], "methods")
    if set(methods_raw) != {"reference", "optimized"}:
        raise RunnerError("methods must contain exactly reference and optimized")
    methods = {
        key: _parse_method(key, value, base)
        for key, value in methods_raw.items()
    }
    if methods["reference"].base_revision != REFERENCE_PIN:
        raise RunnerError(f"reference base pin must be {REFERENCE_PIN}")
    if methods["optimized"].base_revision != OPTIMIZED_PIN:
        raise RunnerError(f"optimized base pin must be {OPTIMIZED_PIN}")
    if methods["reference"].source_dir == methods["optimized"].source_dir:
        raise RunnerError("reference and optimized source worktrees must differ")
    if methods["reference"].binary.path == methods["optimized"].binary.path:
        raise RunnerError("reference and optimized binaries must differ")

    build_manifest_raw, _ = _load_json(
        artifacts["build_manifest"].path, "build manifest"
    )
    build_manifest = _require_mapping(
        build_manifest_raw, "build manifest"
    )
    environment_manifest_raw, _ = _load_json(
        artifacts["environment_manifest"].path, "environment manifest"
    )
    environment_manifest = _require_mapping(
        environment_manifest_raw, "environment manifest"
    )
    if build_manifest.get("schema_version") != BUILD_MANIFEST_SCHEMA:
        raise RunnerError(
            f"build manifest schema must be {BUILD_MANIFEST_SCHEMA}"
        )
    repository_record = _require_mapping(
        build_manifest.get("repository"), "build manifest.repository"
    )
    if repository_record.get("reference_pin") != REFERENCE_PIN:
        raise RunnerError("build manifest reference pin is not approved")
    if repository_record.get("optimized_pin") != OPTIMIZED_PIN:
        raise RunnerError("build manifest optimized pin is not approved")
    build_root = artifacts["build_manifest"].path.parent.parent
    if build_root != run_cwd:
        raise RunnerError(
            "run_cwd must be the repository root containing the build manifest"
        )
    build_records = _require_mapping(
        build_manifest.get("builds"), "build manifest.builds"
    )
    for method_id, method in methods.items():
        build_key = f"{method_id}_{mode}"
        build = _require_mapping(
            build_records.get(build_key),
            f"build manifest.builds.{build_key}",
        )
        expected_observer = mode == "fidelity"
        if build.get("observer_compiled") is not expected_observer:
            raise RunnerError(
                f"{build_key} observer_compiled does not match mode"
            )
        if build.get("algorithm_revision") != method.base_revision:
            raise RunnerError(f"{build_key} algorithm revision mismatch")
        if build.get("applied_head") != method.head_revision:
            raise RunnerError(f"{build_key} applied HEAD mismatch")
        binary_path = _resolve_path(
            build.get("binary_path"), build_root, f"{build_key}.binary_path"
        )
        if binary_path != method.binary.path:
            raise RunnerError(f"{build_key} binary path mismatch")
        if build.get("binary_sha256") != method.binary.sha256:
            raise RunnerError(f"{build_key} binary SHA-256 mismatch")
    if (
        repository_record.get("reference_applied_head")
        != methods["reference"].head_revision
        or repository_record.get("reference_tree")
        != methods["reference"].tree_hash
        or repository_record.get("optimized_applied_head")
        != methods["optimized"].head_revision
        or repository_record.get("optimized_tree")
        != methods["optimized"].tree_hash
    ):
        raise RunnerError("method heads/trees disagree with build manifest")

    pairs_raw = record["pairs"]
    if not isinstance(pairs_raw, list) or not pairs_raw:
        raise RunnerError("pairs must be a non-empty array")
    pairs = tuple(
        _parse_pair(item, base, phase, index)
        for index, item in enumerate(pairs_raw)
    )
    ids = [pair.pair_id for pair in pairs]
    if len(ids) != len(set(ids)):
        raise RunnerError("pair_id values must be unique")

    capacity: CapacitySearch | None = None
    if "capacity_search" in record:
        if mode != "timing":
            raise RunnerError("capacity_search is permitted only in timing mode")
        if phase != "calibration":
            raise RunnerError(
                "capacity_search is permitted only during calibration"
            )
        raw_capacity = _require_mapping(
            record["capacity_search"], "capacity_search"
        )
        _require_exact_keys(
            raw_capacity,
            {"environment", "reference_anchor_n", "grid", "max_points"},
            set(),
            "capacity_search",
        )
        environment = raw_capacity["environment"]
        if (
            not isinstance(environment, str)
            or IDENTIFIER_RE.fullmatch(environment) is None
        ):
            raise RunnerError("capacity_search.environment is invalid")
        anchor = _positive_int(
            raw_capacity["reference_anchor_n"],
            "capacity_search.reference_anchor_n",
            maximum=100_000,
        )
        grid_raw = raw_capacity["grid"]
        if not isinstance(grid_raw, list) or not grid_raw:
            raise RunnerError("capacity_search.grid must be non-empty")
        grid = tuple(
            _positive_int(
                item, f"capacity_search.grid[{index}]", maximum=100_000
            )
            for index, item in enumerate(grid_raw)
        )
        if tuple(sorted(set(grid))) != grid:
            raise RunnerError(
                "capacity_search.grid must be strictly increasing and unique"
            )
        if grid[0] != anchor:
            raise RunnerError(
                "capacity_search.grid must begin at reference_anchor_n"
            )
        max_points = _positive_int(
            raw_capacity["max_points"],
            "capacity_search.max_points",
            maximum=1000,
        )
        if len(grid) > max_points:
            raise RunnerError(
                "capacity_search.grid exceeds its frozen max_points bound"
            )
        if any(pair.environment != environment for pair in pairs):
            raise RunnerError(
                "a capacity plan may contain only its registered environment"
            )
        by_n: dict[int, set[int]] = {}
        for pair in pairs:
            if pair.robot_count not in grid:
                raise RunnerError(
                    f"pair {pair.pair_id} has N outside the capacity grid"
                )
            by_n.setdefault(pair.robot_count, set()).add(pair.seed)
        if set(by_n) != set(grid):
            raise RunnerError("every capacity grid point needs planned pairs")
        seed_sets = list(by_n.values())
        if any(seed_set != seed_sets[0] for seed_set in seed_sets[1:]):
            raise RunnerError(
                "capacity grid points must use identical paired seed sets"
            )
        capacity = CapacitySearch(environment, anchor, grid, max_points)

    capacity_evaluation: CapacityEvaluation | None = None
    if "capacity_evaluation" in record:
        if phase == "calibration":
            raise RunnerError(
                "capacity_evaluation is forbidden during calibration"
            )
        if capacity is not None:
            raise RunnerError(
                "capacity_evaluation and capacity_search are mutually exclusive"
            )
        raw_evaluation = _require_mapping(
            record["capacity_evaluation"], "capacity_evaluation"
        )
        _require_exact_keys(
            raw_evaluation,
            CAPACITY_EVALUATION_KEYS,
            set(),
            "capacity_evaluation",
        )
        evaluation_environment = raw_evaluation["environment"]
        if (
            not isinstance(evaluation_environment, str)
            or IDENTIFIER_RE.fullmatch(evaluation_environment) is None
        ):
            raise RunnerError("capacity_evaluation.environment is invalid")
        role = raw_evaluation["role"]
        expected_role = f"{mode}-grid"
        if role != expected_role:
            raise RunnerError(
                f"capacity_evaluation.role must be {expected_role!r}"
            )
        evaluation_grid_raw = raw_evaluation["grid"]
        if not isinstance(evaluation_grid_raw, list):
            raise RunnerError("capacity_evaluation.grid must be an array")
        evaluation_grid = tuple(
            _positive_int(
                item,
                f"capacity_evaluation.grid[{index}]",
                maximum=100_000,
            )
            for index, item in enumerate(evaluation_grid_raw)
        )
        if (
            len(evaluation_grid) < 2
            or tuple(sorted(set(evaluation_grid))) != evaluation_grid
        ):
            raise RunnerError(
                "capacity_evaluation.grid must contain at least two "
                "strictly increasing unique counts"
            )
        evaluation_anchor = _positive_int(
            raw_evaluation["reference_anchor_n"],
            "capacity_evaluation.reference_anchor_n",
            maximum=100_000,
        )
        candidate_n_star = _positive_int(
            raw_evaluation["candidate_n_star"],
            "capacity_evaluation.candidate_n_star",
            maximum=100_000,
        )
        raw_next_grid_point = raw_evaluation["next_grid_point"]
        next_grid_point = (
            None
            if raw_next_grid_point is None
            else _positive_int(
                raw_next_grid_point,
                "capacity_evaluation.next_grid_point",
                maximum=100_000,
            )
        )
        if evaluation_grid[0] != evaluation_anchor:
            raise RunnerError(
                "capacity_evaluation.grid must begin at reference_anchor_n"
            )
        if candidate_n_star not in evaluation_grid:
            raise RunnerError(
                "capacity_evaluation.candidate_n_star is outside the grid"
            )
        candidate_index = evaluation_grid.index(candidate_n_star)
        expected_next_grid_point = (
            evaluation_grid[candidate_index + 1]
            if candidate_index + 1 < len(evaluation_grid)
            else None
        )
        if next_grid_point != expected_next_grid_point:
            raise RunnerError(
                "capacity_evaluation.next_grid_point must be the immediate "
                "successor of candidate_n_star, or null at the grid ceiling"
            )
        expected_conditions = {
            (seed, count)
            for seed in PHASE_SEEDS[phase]
            for count in evaluation_grid
        }
        observed_conditions = [
            (pair.seed, pair.robot_count) for pair in pairs
        ]
        if any(
            pair.environment != evaluation_environment for pair in pairs
        ):
            raise RunnerError(
                "capacity_evaluation plans may contain only the registered "
                "environment"
            )
        if (
            len(observed_conditions) != len(expected_conditions)
            or set(observed_conditions) != expected_conditions
        ):
            raise RunnerError(
                "capacity_evaluation requires exactly one pair for every "
                "phase seed and grid count"
            )
        decision_artifact = artifacts.get("capacity_decision")
        if decision_artifact is None:
            raise RunnerError(
                "capacity_evaluation requires artifacts.capacity_decision"
            )
        try:
            decision, loaded_decision_artifact = (
                load_capacity_decision(decision_artifact.path)
            )
        except (CapacityDecisionError, OSError, ValueError) as exc:
            raise RunnerError(
                f"capacity decision is invalid: {exc}"
            ) from exc
        if (
            loaded_decision_artifact["sha256"]
            != decision_artifact.sha256
        ):
            raise RunnerError("capacity decision SHA-256 mismatch")
        decision_provenance = _require_mapping(
            decision["provenance"], "capacity decision.provenance"
        )
        for decision_name, plan_name in (
            ("analyzer", "analysis_revision"),
            ("experiment_manifest", "experiment_manifest"),
            ("environment_manifest", "environment_manifest"),
        ):
            provenance_artifact = _require_mapping(
                decision_provenance[decision_name],
                f"capacity decision.provenance.{decision_name}",
            )
            if provenance_artifact["sha256"] != artifacts[plan_name].sha256:
                raise RunnerError(
                    f"capacity decision {decision_name} disagrees with plan"
                )
        for provenance_name in (
            "calibration_analysis",
            "analyzer",
            "experiment_manifest",
            "environment_manifest",
        ):
            provenance_artifact = _require_mapping(
                decision_provenance[provenance_name],
                f"capacity decision.provenance.{provenance_name}",
            )
            Artifact(
                Path(provenance_artifact["path"]),
                provenance_artifact["sha256"],
                f"capacity decision {provenance_name}",
            ).verify()
        decision_seeds = _require_mapping(
            decision["seeds"], "capacity decision.seeds"
        )
        if decision_seeds.get(phase) != list(PHASE_SEEDS[phase]):
            raise RunnerError(
                f"capacity decision does not register the complete {phase} "
                "seed set"
            )
        environment_records = decision.get("environments")
        if not isinstance(environment_records, list):
            raise RunnerError(
                "capacity decision.environments must be an array"
            )
        matching_records = [
            _require_mapping(
                item, f"capacity decision.environments[{index}]"
            )
            for index, item in enumerate(environment_records)
            if isinstance(item, dict)
            and item.get("environment") == evaluation_environment
        ]
        if len(matching_records) != 1:
            raise RunnerError(
                "capacity decision must contain exactly one matching "
                f"{evaluation_environment!r} environment record"
            )
        decision_environment = matching_records[0]
        expected_decision_fields = {
            "reference_anchor_n": evaluation_anchor,
            "grid": list(evaluation_grid),
            "candidate_n_star": candidate_n_star,
            "next_grid_point": next_grid_point,
        }
        for field, expected in expected_decision_fields.items():
            if decision_environment.get(field) != expected:
                raise RunnerError(
                    f"capacity decision {evaluation_environment}.{field} "
                    "does not match capacity_evaluation"
                )
        role_counts_key = (
            "validation_timing_counts"
            if role == "timing-grid"
            else "validation_fidelity_counts"
        )
        if decision_environment.get(role_counts_key) != list(
            evaluation_grid
        ):
            raise RunnerError(
                f"capacity decision {evaluation_environment}."
                f"{role_counts_key} does not match the full grid"
            )
        decision_timeout = _positive_float(
            decision_environment.get("validation_timeout_seconds"),
            (
                "capacity decision "
                f"{evaluation_environment}.validation_timeout_seconds"
            ),
            maximum=1_800,
        )
        if decision_timeout != timeout:
            raise RunnerError(
                "plan timeout_seconds does not match the frozen capacity "
                "decision"
            )
        capacity_evaluation = CapacityEvaluation(
            environment=evaluation_environment,
            role=role,
            reference_anchor_n=evaluation_anchor,
            candidate_n_star=candidate_n_star,
            next_grid_point=next_grid_point,
            grid=evaluation_grid,
        )
    elif phase in {"validation", "audit"}:
        raise RunnerError(
            f"{phase} plans require capacity_evaluation and a frozen "
            "capacity decision"
        )
    elif "capacity_decision" in artifacts:
        raise RunnerError(
            "artifacts.capacity_decision requires capacity_evaluation"
        )

    return Plan(
        path=plan_path,
        plan_sha256=hashlib.sha256(data).hexdigest(),
        phase=phase,
        mode=mode,
        fixed_work_timesteps=fixed_work,
        timestep_seconds=timestep_seconds,
        idle_cooldown_seconds=idle_cooldown_seconds,
        timeout_seconds=timeout,
        termination_grace_seconds=grace,
        repetitions=repetitions,
        warmups_per_binary_map=warmups,
        omp_threads=omp_threads,
        order_seed=order_seed,
        minimum_free_disk_bytes=minimum_free_disk_bytes,
        maximum_peak_rss_mib=maximum_peak_rss_mib,
        power_mode=power_mode,
        thermal_policy=thermal_policy,
        task_validity_predicate_id=task_validity_predicate_id,
        trace_detail=trace_detail,
        run_cwd=run_cwd,
        artifacts=artifacts,
        methods=methods,
        pairs=pairs,
        capacity=capacity,
        capacity_evaluation=capacity_evaluation,
        runner_sha256=runner_sha256,
        build_manifest=build_manifest,
        environment_manifest=environment_manifest,
    )


def _git(source: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source), *args],
        check=False,
        text=True,
        capture_output=True,
    )
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RunnerError(
            f"git {' '.join(args)} failed in {source}: {detail}"
        )
    return completed.stdout.strip()


def _verify_source(method: Method, lock: Artifact) -> dict[str, Any]:
    top = Path(_git(method.source_dir, "rev-parse", "--show-toplevel")).resolve()
    if top != method.source_dir:
        raise RunnerError(
            f"{method.method_id} source_dir is not the worktree root"
        )
    status = _git(
        method.source_dir, "status", "--porcelain", "--untracked-files=all"
    )
    if status:
        raise RunnerError(
            f"{method.method_id} source worktree is dirty:\n{status}"
        )
    head = _git(method.source_dir, "rev-parse", "HEAD")
    tree = _git(method.source_dir, "rev-parse", "HEAD^{tree}")
    if head != method.head_revision:
        raise RunnerError(
            f"{method.method_id} HEAD mismatch: expected "
            f"{method.head_revision}, observed {head}"
        )
    if tree != method.tree_hash:
        raise RunnerError(
            f"{method.method_id} tree mismatch: expected "
            f"{method.tree_hash}, observed {tree}"
        )
    ancestor = subprocess.run(
        [
            "git",
            "-C",
            str(method.source_dir),
            "merge-base",
            "--is-ancestor",
            method.base_revision,
            head,
        ],
        check=False,
        capture_output=True,
    )
    if ancestor.returncode != 0:
        raise RunnerError(
            f"{method.method_id} base pin is not an ancestor of HEAD"
        )
    if head == method.base_revision:
        raise RunnerError(
            f"{method.method_id} HEAD does not contain the common patch"
        )
    source_lock = Artifact(
        method.source_dir / "pixi.lock",
        lock.sha256,
        f"{method.method_id} source pixi.lock",
    )
    source_lock.verify()
    if not os.access(method.binary.path, os.X_OK):
        raise RunnerError(f"{method.method_id} binary is not executable")
    return {
        "method_id": method.method_id,
        "source_dir": str(method.source_dir),
        "base_revision": method.base_revision,
        "head_revision": head,
        "tree_hash": tree,
        "binary": method.binary.verify(),
    }


def _contains_markers(path: Path) -> dict[str, bool]:
    data = path.read_bytes()
    return {
        marker.decode("ascii"): marker in data
        for marker in OBSERVER_BINARY_MARKERS
    }


def _verify_observer_flavor(method: Method, mode: str) -> dict[str, bool]:
    markers = _contains_markers(method.binary.path)
    if mode == "timing" and any(markers.values()):
        raise RunnerError(
            f"{method.method_id} timing binary contains comparison-observer "
            f"markers; use a compile-disabled build"
        )
    if mode == "fidelity" and not all(markers.values()):
        raise RunnerError(
            f"{method.method_id} fidelity binary lacks comparison-observer "
            f"markers; use a compile-enabled build"
        )
    return markers


def _validate_template(template: Artifact) -> str:
    try:
        text = template.path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise RunnerError("config template must be UTF-8") from exc
    for key in PROTECTED_CONFIG_KEYS:
        count = len(
            re.findall(rf"(?m)^{re.escape(key)}\s*:[^\r\n]*$", text)
        )
        if count != 1:
            raise RunnerError(
                f"config template must contain exactly one top-level {key}; "
                f"found {count}"
            )
    return text


def _registered_file(
    root: Path, value: Any, label: str
) -> tuple[Path, dict[str, Any]]:
    record = _require_mapping(value, label)
    path_value = record.get("path")
    if not isinstance(path_value, str) or not path_value:
        raise RunnerError(f"{label}.path must be a non-empty string")
    relative = Path(path_value)
    if relative.is_absolute():
        raise RunnerError(f"{label}.path must be relative to the manifest")
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise RunnerError(f"{label}.path escapes the environment root") from exc
    expected_hash = _sha256(record.get("sha256"), f"{label}.sha256")
    expected_bytes = _positive_int(
        record.get("bytes"), f"{label}.bytes", maximum=10**9
    )
    artifact = Artifact(path, expected_hash, label)
    observed = artifact.verify()
    if observed["bytes"] != expected_bytes:
        raise RunnerError(
            f"{label} byte count mismatch: expected {expected_bytes}, "
            f"observed {observed['bytes']}"
        )
    return path, observed


def _verify_environment_manifest(
    plan: Plan,
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, Any]]:
    manifest = plan.environment_manifest
    if manifest.get("schema_version") != 1:
        raise RunnerError("environment manifest schema_version must be 1")
    if manifest.get("artifact_hash") != "SHA-256":
        raise RunnerError("environment manifest artifact hash is not SHA-256")
    families = manifest.get("families")
    splits = manifest.get("splits")
    if (
        not isinstance(families, list)
        or not families
        or len(set(families)) != len(families)
        or not all(
            isinstance(item, str)
            and IDENTIFIER_RE.fullmatch(item) is not None
            for item in families
        )
    ):
        raise RunnerError("environment manifest families are malformed")
    if (
        not isinstance(splits, dict)
        or set(splits) != {"calibration", "validation"}
        or not all(isinstance(value, dict) for value in splits.values())
    ):
        raise RunnerError(
            "environment manifest must register calibration and validation"
        )
    root = plan.artifacts["environment_manifest"].path.parent.resolve()
    sidecar = root / "manifest.sha256"
    _require_regular_file(sidecar, "environment manifest sidecar")
    try:
        parts = sidecar.read_text(encoding="ascii").split()
    except UnicodeDecodeError as exc:
        raise RunnerError(
            "environment manifest sidecar must be ASCII"
        ) from exc
    if (
        len(parts) != 2
        or parts[0] != plan.artifacts["environment_manifest"].sha256
        or parts[1] != "manifest.json"
    ):
        raise RunnerError("environment manifest sidecar does not match")

    cases_raw = manifest.get("environments")
    if not isinstance(cases_raw, list):
        raise RunnerError("environment manifest environments must be an array")
    cases: dict[tuple[str, str], dict[str, Any]] = {}
    expected_files = {
        plan.artifacts["environment_manifest"].path.resolve(),
        sidecar.resolve(),
    }
    file_records: dict[str, Any] = {}
    for index, raw_case in enumerate(cases_raw):
        label = f"environment manifest environments[{index}]"
        case = _require_mapping(raw_case, label)
        family = case.get("family")
        split = case.get("split")
        if family not in families or split not in {"calibration", "validation"}:
            raise RunnerError(f"{label} has an unregistered family/split")
        key = (family, split)
        if key in cases:
            raise RunnerError(f"duplicate environment case {family}/{split}")
        map_path, map_observed = _registered_file(
            root, case.get("map"), f"{label}.map"
        )
        scenario_path, scenario_observed = _registered_file(
            root, case.get("scenario"), f"{label}.scenario"
        )
        capacity = _require_mapping(
            case.get("capacity"), f"{label}.capacity"
        )
        bank_path, bank_observed = _registered_file(
            root, capacity.get("bank"), f"{label}.capacity.bank"
        )
        prefixes = capacity.get("prefix_scenarios")
        if not isinstance(prefixes, list) or not prefixes:
            raise RunnerError(f"{label} has no registered exact-N prefixes")
        prefix_by_n: dict[int, dict[str, Any]] = {}
        for prefix_index, prefix_raw in enumerate(prefixes):
            prefix_label = (
                f"{label}.capacity.prefix_scenarios[{prefix_index}]"
            )
            prefix = _require_mapping(prefix_raw, prefix_label)
            count = _positive_int(
                prefix.get("robot_count"),
                f"{prefix_label}.robot_count",
                maximum=100_000,
            )
            if count in prefix_by_n:
                raise RunnerError(
                    f"{family}/{split} registers N={count} twice"
                )
            _sha256(prefix.get("sha256"), f"{prefix_label}.sha256")
            _positive_int(
                prefix.get("bytes"),
                f"{prefix_label}.bytes",
                maximum=10**9,
            )
            prefix_by_n[count] = prefix
        expected_files.update((map_path, scenario_path, bank_path))
        file_records[f"{family}/{split}"] = {
            "map": map_observed,
            "scenario": scenario_observed,
            "capacity_bank": bank_observed,
            "registered_prefix_counts": sorted(prefix_by_n),
        }
        cases[key] = {
            "record": case,
            "map_path": map_path,
            "scenario_path": scenario_path,
            "prefix_by_n": prefix_by_n,
        }

    expected_cases = {
        (family, split)
        for family in families
        for split in ("calibration", "validation")
    }
    if set(cases) != expected_cases:
        raise RunnerError(
            "environment manifest does not contain exactly one case for "
            "every family/split"
        )
    actual_files: set[Path] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RunnerError(
                f"environment manifest tree contains a symlink: {path}"
            )
        if path.is_file():
            actual_files.add(path.resolve())
    if actual_files != expected_files:
        unexpected = sorted(str(path) for path in actual_files - expected_files)
        missing = sorted(str(path) for path in expected_files - actual_files)
        raise RunnerError(
            "environment manifest file tree differs from registration: "
            f"unexpected={unexpected}, missing={missing}"
        )
    return cases, {
        "root": str(root),
        "manifest_sha256": plan.artifacts[
            "environment_manifest"
        ].sha256,
        "registered_file_count": len(expected_files),
        "cases": file_records,
    }


def _exact_robot_keys(value: Any, count: int, label: str) -> None:
    mapping = _require_mapping(value, label)
    expected = {str(index) for index in range(count)}
    if set(mapping) != expected:
        raise RunnerError(
            f"{label} must contain exactly robot keys 0..{count - 1}"
        )


def _validate_pair_artifacts(
    plan: Plan,
    pair: Pair,
    cases: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    map_record = pair.map_artifact.verify()
    scenario_record = pair.scenario.verify()
    scenario, _ = _load_json(pair.scenario.path, pair.scenario.label)
    scenario = _require_mapping(scenario, pair.scenario.label)
    if scenario.get("num_robots") != pair.robot_count:
        raise RunnerError(
            f"{pair.pair_id}: scenario num_robots="
            f"{scenario.get('num_robots')!r} does not equal planned "
            f"N={pair.robot_count}"
        )
    metadata = _require_mapping(
        scenario.get("metadata"), f"{pair.pair_id}.scenario.metadata"
    )
    split = PHASE_SPLIT[plan.phase]
    if metadata.get("split") != split:
        raise RunnerError(
            f"{pair.pair_id}: scenario split does not match registered "
            f"{split} geometry for phase {plan.phase}"
        )
    if metadata.get("environment_id") != pair.environment:
        raise RunnerError(
            f"{pair.pair_id}: scenario environment_id does not match"
        )
    if metadata.get("map_sha256") != pair.map_artifact.sha256:
        raise RunnerError(
            f"{pair.pair_id}: scenario map_sha256 does not match map bytes"
        )
    _exact_robot_keys(
        scenario.get("decisions"),
        pair.robot_count,
        f"{pair.pair_id}.scenario.decisions",
    )
    _exact_robot_keys(
        scenario.get("waypoints"),
        pair.robot_count,
        f"{pair.pair_id}.scenario.waypoints",
    )

    case = cases.get((pair.environment, split))
    if case is None:
        raise RunnerError(
            f"{pair.pair_id}: environment family/split is not registered"
        )
    registered_map = _require_mapping(
        case["record"].get("map"), "registered environment map"
    )
    if (
        pair.map_artifact.path != case["map_path"]
        or pair.map_artifact.sha256 != registered_map.get("sha256")
        or map_record["bytes"] != registered_map.get("bytes")
    ):
        raise RunnerError(
            f"{pair.pair_id}: map is not the registered "
            f"{pair.environment}/{split} artifact"
        )

    base_scenario = _require_mapping(
        case["record"].get("scenario"), "registered base scenario"
    )
    base_match = (
        pair.scenario.sha256 == base_scenario.get("sha256")
        and scenario_record["bytes"] == base_scenario.get("bytes")
    )
    prefix = case["prefix_by_n"].get(pair.robot_count)
    prefix_match = (
        prefix is not None
        and pair.scenario.sha256 == prefix.get("sha256")
        and scenario_record["bytes"] == prefix.get("bytes")
    )
    if prefix_match:
        capacity_record = _require_mapping(
            case["record"].get("capacity"),
            "registered environment capacity",
        )
        if (
            metadata.get("prefix_robot_count") != pair.robot_count
            or metadata.get("capacity_bank_id")
            != capacity_record.get("bank_id")
        ):
            raise RunnerError(
                f"{pair.pair_id}: exact-N prefix metadata does not bind "
                "the registered count and capacity bank"
            )
    if plan.capacity is not None and not prefix_match:
        raise RunnerError(
            f"{pair.pair_id}: capacity scenario is not the registered "
            f"exact-N={pair.robot_count} prefix"
        )
    if plan.capacity is None and not (base_match or prefix_match):
        raise RunnerError(
            f"{pair.pair_id}: scenario is neither the registered base "
            "scenario nor a registered exact-N prefix"
        )
    return {
        "map": map_record,
        "scenario": scenario_record,
        "registry": {
            "family": pair.environment,
            "split": split,
            "registration": "exact-N-prefix" if prefix_match else "base",
        },
    }


def _git_bytes(source: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(source), *args],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise RunnerError(
            f"git {' '.join(args)} failed in {source}: "
            f"{completed.stderr.decode('utf-8', errors='replace').strip()}"
        )
    return completed.stdout


def _name_status(source: Path, left: str, right: str) -> list[tuple[str, str]]:
    output = _git_bytes(
        source, "diff", "--name-status", "--no-renames", left, right
    ).decode("utf-8")
    records: list[tuple[str, str]] = []
    for line in output.splitlines():
        status, separator, path = line.partition("\t")
        if not separator or status not in {"A", "M", "D"} or not path:
            raise RunnerError(f"unexpected git name-status record: {line!r}")
        records.append((status, path))
    return records


def _verify_build_manifest(plan: Plan) -> dict[str, Any]:
    manifest = plan.build_manifest
    repository = _require_mapping(
        manifest.get("repository"), "build manifest.repository"
    )
    toolchain = _require_mapping(
        manifest.get("toolchain"), "build manifest.toolchain"
    )
    builds = _require_mapping(
        manifest.get("builds"), "build manifest.builds"
    )
    gates = _require_mapping(
        manifest.get("gates"), "build manifest.gates"
    )
    if manifest.get("status") != "dual-build-parity-passed-smoke-fidelity-only":
        raise RunnerError("build manifest dual-build status is not approved")
    for gate in (
        "dual_clean_builds",
        "common_registered_tests_all_four_builds",
        "timing_observer_compile_absence",
        "smoke_semantic_trace_identity",
    ):
        if gates.get(gate) != "pass":
            raise RunnerError(f"build manifest gate {gate} did not pass")
    phase_gate = f"{plan.phase}_permitted"
    if gates.get(phase_gate) is not True:
        raise RunnerError(
            f"build manifest gate {phase_gate} does not permit this phase"
        )
    if toolchain.get("pixi_lock_sha256") != plan.artifacts["pixi_lock"].sha256:
        raise RunnerError("build manifest Pixi lock hash mismatch")

    common_commit = _git_object(
        repository.get("common_patch_commit"),
        "build manifest common_patch_commit",
    )
    common_email_sha = _sha256(
        repository.get("common_patch_format_email_sha256"),
        "build manifest common_patch_format_email_sha256",
    )
    common_diff_sha = _sha256(
        repository.get("common_patch_full_index_diff_sha256"),
        "build manifest common_patch_full_index_diff_sha256",
    )
    if common_commit != plan.methods["optimized"].head_revision:
        raise RunnerError(
            "common patch commit must equal the optimized applied HEAD"
        )
    parent = _git(
        plan.methods["optimized"].source_dir,
        "rev-parse",
        f"{common_commit}^",
    )
    observed_diff_sha = hashlib.sha256(
        _git_bytes(
            plan.methods["optimized"].source_dir,
            "diff",
            "--full-index",
            parent,
            common_commit,
        )
    ).hexdigest()
    if observed_diff_sha != common_diff_sha:
        raise RunnerError(
            "common patch full-index diff hash does not match Git objects"
        )

    cross = _require_mapping(
        repository.get("patched_cross_tree_difference"),
        "build manifest patched_cross_tree_difference",
    )
    expected_paths = cross.get("paths")
    if (
        cross.get("status") != "pass"
        or not isinstance(expected_paths, list)
        or not all(isinstance(path, str) for path in expected_paths)
        or cross.get("path_count") != len(expected_paths)
        or len(expected_paths) != len(set(expected_paths))
    ):
        raise RunnerError("invalid patched cross-tree whitelist")
    reference = plan.methods["reference"]
    optimized = plan.methods["optimized"]
    pinned_delta = _name_status(
        reference.source_dir,
        reference.base_revision,
        optimized.base_revision,
    )
    patched_delta = _name_status(
        reference.source_dir,
        reference.head_revision,
        optimized.head_revision,
    )
    if pinned_delta != patched_delta:
        raise RunnerError(
            "patched cross-tree status/path delta differs from pinned bases"
        )
    if [path for _, path in patched_delta] != expected_paths:
        raise RunnerError(
            "patched cross-tree delta differs from manifest whitelist"
        )

    build_records: dict[str, Any] = {}
    repository_root = plan.artifacts["build_manifest"].path.parent.parent
    for method_id, method in plan.methods.items():
        build_key = f"{method_id}_{plan.mode}"
        build = _require_mapping(
            builds.get(build_key), f"build manifest.builds.{build_key}"
        )
        if build.get("registered_tests") != "3/3 passed":
            raise RunnerError(f"{build_key} registered tests did not pass")
        binary = method.binary.verify()
        build_dir = method.binary.path.parent
        auxiliary = {}
        for filename, hash_field in (
            ("CMakeCache.txt", "cmake_cache_sha256"),
            ("compile_commands.json", "compile_commands_sha256"),
            ("build.log", "clean_build_log_sha256"),
        ):
            expected = _sha256(
                build.get(hash_field), f"{build_key}.{hash_field}"
            )
            artifact = Artifact(
                build_dir / filename,
                expected,
                f"{build_key} {filename}",
            )
            auxiliary[filename] = artifact.verify()
        try:
            method.binary.path.relative_to(repository_root)
        except ValueError as exc:
            raise RunnerError(
                f"{build_key} binary escapes the attested repository"
            ) from exc
        build_records[build_key] = {
            "binary": binary,
            "observer_compiled": build.get("observer_compiled"),
            "auxiliary": auxiliary,
        }
    return {
        "schema_version": BUILD_MANIFEST_SCHEMA,
        "common_patch_commit": common_commit,
        "common_patch_format_email_sha256": common_email_sha,
        "common_patch_full_index_diff_sha256": common_diff_sha,
        "cross_tree_difference": patched_delta,
        "builds": build_records,
    }


def _existing_filesystem_path(path: Path) -> Path:
    candidate = path.resolve()
    while not candidate.exists():
        if candidate.parent == candidate:
            raise RunnerError(f"no existing filesystem ancestor for {path}")
        candidate = candidate.parent
    return candidate


def _bounded_probe_output(value: str) -> str:
    return value if len(value) <= 64 * 1024 else value[: 64 * 1024]


def _host_condition_snapshot(
    plan: Plan, filesystem_path: Path
) -> dict[str, Any]:
    disk_path = _existing_filesystem_path(filesystem_path)
    usage = shutil.disk_usage(disk_path)
    power: dict[str, Any]
    thermal: dict[str, Any]
    if sys.platform != "darwin":
        power = {
            "command": ["pmset", "-g", "batt"],
            "returncode": None,
            "stdout": "",
            "stderr": "host is not Darwin",
            "source": None,
            "valid": False,
        }
        thermal = {
            "command": [
                "swift",
                "-e",
                "import Foundation; print("
                "ProcessInfo.processInfo.thermalState.rawValue)",
            ],
            "returncode": None,
            "stdout": "",
            "stderr": "host is not Darwin",
            "state": None,
            "valid": False,
        }
    else:
        try:
            completed = subprocess.run(
                ["pmset", "-g", "batt"],
                check=False,
                text=True,
                capture_output=True,
                timeout=30,
            )
            source = (
                "AC Power"
                if "Now drawing from 'AC Power'" in completed.stdout
                else None
            )
            power = {
                "command": ["pmset", "-g", "batt"],
                "returncode": completed.returncode,
                "stdout": _bounded_probe_output(completed.stdout),
                "stderr": _bounded_probe_output(completed.stderr),
                "source": source,
                "valid": completed.returncode == 0 and source == "AC Power",
            }
        except (OSError, subprocess.TimeoutExpired) as exc:
            power = {
                "command": ["pmset", "-g", "batt"],
                "returncode": None,
                "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}",
                "source": None,
                "valid": False,
            }

        cache = (
            Path(tempfile.gettempdir())
            / f"dancers-swift-module-cache-{plan.runner_sha256[:16]}"
        )
        try:
            if cache.is_symlink():
                raise RunnerError(
                    f"thermal probe cache is a symbolic link: {cache}"
                )
            cache.mkdir(mode=0o700, parents=True, exist_ok=True)
            probe_environment = os.environ.copy()
            probe_environment.update(
                {
                    "CLANG_MODULE_CACHE_PATH": str(cache),
                    "SWIFT_MODULECACHE_PATH": str(cache),
                }
            )
            command = [
                "swift",
                "-e",
                "import Foundation; print("
                "ProcessInfo.processInfo.thermalState.rawValue)",
            ]
            completed = subprocess.run(
                command,
                check=False,
                text=True,
                capture_output=True,
                timeout=120,
                env=probe_environment,
            )
            raw_state = completed.stdout.strip()
            try:
                state = int(raw_state)
            except ValueError:
                state = None
            thermal = {
                "command": command,
                "module_cache_path": str(cache),
                "returncode": completed.returncode,
                "stdout": _bounded_probe_output(completed.stdout),
                "stderr": _bounded_probe_output(completed.stderr),
                "state": state,
                "valid": completed.returncode == 0 and state == 0,
            }
        except (
            OSError,
            RunnerError,
            subprocess.TimeoutExpired,
        ) as exc:
            thermal = {
                "command": ["swift", "-e", "<thermal-state-probe>"],
                "module_cache_path": str(cache),
                "returncode": None,
                "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}",
                "state": None,
                "valid": False,
            }
    snapshot = {
        "checked_utc": utc_now(),
        "filesystem_path": str(disk_path),
        "free_disk_bytes": usage.free,
        "minimum_free_disk_bytes": plan.minimum_free_disk_bytes,
        "disk_valid": usage.free >= plan.minimum_free_disk_bytes,
        "power_mode": plan.power_mode,
        "power_probe": power,
        "thermal_policy": plan.thermal_policy,
        "thermal_probe": thermal,
    }
    snapshot["valid"] = (
        snapshot["disk_valid"]
        and power["valid"]
        and thermal["valid"]
    )
    return snapshot


def _host_block_valid(
    before: dict[str, Any], after: dict[str, Any]
) -> bool:
    return bool(
        before.get("valid")
        and after.get("valid")
        and before["power_probe"].get("source")
        == after["power_probe"].get("source")
        and before["thermal_probe"].get("state")
        == after["thermal_probe"].get("state")
    )


_cooldown_sleep = time.sleep
_cooldown_clock = time.monotonic


def _enforce_idle_cooldown(
    plan: Plan,
    before: dict[str, Any],
    filesystem_path: Path,
) -> dict[str, Any]:
    started = _cooldown_clock()
    deadline = started + plan.idle_cooldown_seconds
    while True:
        remaining = deadline - _cooldown_clock()
        if remaining <= 0.0:
            break
        _cooldown_sleep(remaining)
    finished = _cooldown_clock()
    after = _host_condition_snapshot(plan, filesystem_path)
    elapsed = finished - started
    valid = (
        elapsed >= plan.idle_cooldown_seconds
        and _host_block_valid(before, after)
    )
    record = {
        "required_seconds": plan.idle_cooldown_seconds,
        "eligible": bool(before["valid"]),
        "performed": True,
        "elapsed_seconds": elapsed,
        "before": before,
        "after": after,
        "valid": valid,
    }
    if not valid:
        raise RunnerError(
            "idle cooldown did not preserve the frozen power/thermal controls"
        )
    return record


def _validate_experiment_controls(
    plan: Plan, experiment: dict[str, Any]
) -> None:
    if experiment.get("schema_version") != "1.0":
        raise RunnerError("experiment manifest schema must be '1.0'")
    controls = _require_mapping(
        experiment.get("control_variables"),
        "experiment manifest.control_variables",
    )
    expected_controls = {
        "fixed_work_timesteps": plan.fixed_work_timesteps,
        "runner_jobs": 1,
        "omp_threads": 1,
        "measured_repetitions": 3,
        "warmups_per_binary_map": 1,
        "minimum_free_disk_bytes": MINIMUM_FREE_DISK_BYTES,
        "maximum_peak_rss_mib": MAXIMUM_PEAK_RSS_MIB,
        "task_validity_predicate_id": TASK_VALIDITY_PREDICATE_ID,
    }
    for field, expected in expected_controls.items():
        if controls.get(field) != expected:
            raise RunnerError(
                f"experiment control {field} disagrees with the plan"
            )
    robot = _require_mapping(
        experiment.get("robot_configuration"),
        "experiment manifest.robot_configuration",
    )
    if (
        robot.get("fixed_work_timesteps") != plan.fixed_work_timesteps
        or robot.get("timestep_seconds") != plan.timestep_seconds
    ):
        raise RunnerError(
            "experiment robot timing disagrees with the plan"
        )
    design = _require_mapping(
        experiment.get("calibration_design"),
        "experiment manifest.calibration_design",
    )
    if (
        design.get("status") != "frozen-before-calibration"
        or design.get("timeout_seconds") != 300.0
        or design.get("idle_cooldown_seconds")
        != plan.idle_cooldown_seconds
        or design.get("full_grid_execution") is not True
    ):
        raise RunnerError("experiment calibration design is not frozen")
    if plan.phase != "calibration":
        return
    if plan.timeout_seconds != 300.0:
        raise RunnerError(
            "calibration timeout_seconds must equal 300"
        )

    observed = [
        (pair.environment, pair.seed, pair.robot_count)
        for pair in plan.pairs
    ]
    initial = _require_mapping(
        design.get("initial_fidelity"),
        "experiment manifest.calibration_design.initial_fidelity",
    )
    initial_counts = initial.get("counts")
    if not isinstance(initial_counts, list):
        raise RunnerError("initial calibration counts are invalid")
    expected_initial = {
        (initial.get("environment"), seed, count)
        for seed in PHASE_SEEDS["calibration"]
        for count in initial_counts
    }
    if (
        len(observed) == len(expected_initial)
        and set(observed) == expected_initial
    ):
        if (
            plan.mode != "fidelity"
            or plan.capacity is not None
            or plan.trace_detail != initial.get("trace_detail")
            or plan.repetitions != initial.get("repetitions")
        ):
            raise RunnerError(
                "initial calibration fidelity plan violates preregistration"
            )
        return

    environments = {pair.environment for pair in plan.pairs}
    if len(environments) != 1:
        raise RunnerError(
            "calibration full-grid plans must contain one environment"
        )
    environment = next(iter(environments))
    designs = _require_mapping(
        design.get("environments"),
        "experiment manifest.calibration_design.environments",
    )
    family_design = _require_mapping(
        designs.get(environment),
        f"calibration design {environment}",
    )
    counts_field = (
        "timing_counts" if plan.mode == "timing" else "fidelity_counts"
    )
    expected_counts = family_design.get(counts_field)
    if (
        not isinstance(expected_counts, list)
        or not expected_counts
        or any(
            not isinstance(count, int) or isinstance(count, bool)
            for count in expected_counts
        )
    ):
        raise RunnerError(
            f"calibration design {environment}.{counts_field} is invalid"
        )
    expected_conditions = {
        (environment, seed, count)
        for seed in PHASE_SEEDS["calibration"]
        for count in expected_counts
    }
    if (
        len(observed) != len(expected_conditions)
        or set(observed) != expected_conditions
    ):
        raise RunnerError(
            "calibration plan is not the complete preregistered "
            "seed-by-grid condition set"
        )
    if plan.mode == "timing":
        if plan.capacity is None:
            raise RunnerError(
                "calibration timing requires capacity_search"
            )
        if (
            plan.capacity.environment != environment
            or list(plan.capacity.grid) != expected_counts
            or plan.capacity.reference_anchor_n
            != family_design.get("reference_anchor_n")
            or plan.capacity.max_points != len(expected_counts)
        ):
            raise RunnerError(
                "calibration timing capacity_search disagrees with "
                "preregistration"
            )
    elif plan.capacity is not None:
        raise RunnerError(
            "calibration fidelity must not contain capacity_search"
        )


def preflight(
    plan: Plan, filesystem_path: Path | None = None
) -> dict[str, Any]:
    observed_runner_sha = sha256_file(Path(__file__).resolve())
    if observed_runner_sha != plan.runner_sha256:
        raise RunnerError(
            "runner SHA-256 does not match the frozen plan"
        )
    observed_plan_sha = sha256_file(plan.path)
    if observed_plan_sha != plan.plan_sha256:
        raise RunnerError("plan bytes changed after loading")
    artifact_records = {
        key: artifact.verify()
        for key, artifact in plan.artifacts.items()
    }
    experiment, _ = _load_json(
        plan.artifacts["experiment_manifest"].path,
        "experiment manifest",
    )
    experiment = _require_mapping(experiment, "experiment manifest")
    _validate_experiment_controls(plan, experiment)
    hardware = _require_mapping(
        experiment.get("hardware_versions"),
        "experiment manifest.hardware_versions",
    )
    if hardware.get("power_requirement") != plan.power_mode:
        raise RunnerError(
            "plan power_mode disagrees with the experiment manifest"
        )
    if hardware.get("thermal_policy") != plan.thermal_policy:
        raise RunnerError(
            "plan thermal_policy disagrees with the experiment manifest"
        )
    environment_cases, environment_record = (
        _verify_environment_manifest(plan)
    )
    template = _validate_template(plan.artifacts["config_template"])
    for pair in plan.pairs:
        for key in pair.config_overrides:
            count = len(
                re.findall(
                    rf"(?m)^{re.escape(key)}\s*:[^\r\n]*$", template
                )
            )
            if count != 1:
                raise RunnerError(
                    f"config override {key!r} must appear exactly once"
                )
        render_config(plan, pair, f"preflight-{pair.pair_id}")
    method_records = {
        method_id: _verify_source(
            method,
            plan.artifacts["pixi_lock"],
        )
        for method_id, method in plan.methods.items()
    }
    for method_id, method in plan.methods.items():
        method_records[method_id]["observer_markers"] = (
            _verify_observer_flavor(method, plan.mode)
        )
    pair_records = {
        pair.pair_id: _validate_pair_artifacts(
            plan, pair, environment_cases
        )
        for pair in plan.pairs
    }
    build_attestation = _verify_build_manifest(plan)
    host_conditions = _host_condition_snapshot(
        plan, filesystem_path or plan.run_cwd
    )
    if not host_conditions["valid"]:
        raise RunnerError(
            "host power/thermal/disk preflight did not satisfy the frozen "
            "controls"
        )
    return {
        "schema_version": PLAN_SCHEMA,
        "verified_utc": utc_now(),
        "plan_path": str(plan.path),
        "plan_sha256": plan.plan_sha256,
        "phase": plan.phase,
        "mode": plan.mode,
        "artifacts": artifact_records,
        "methods": method_records,
        "pairs": pair_records,
        "environment_registry": environment_record,
        "runner_sha256": observed_runner_sha,
        "build_attestation": build_attestation,
        "host_conditions": host_conditions,
        "idle_cooldown": {
            "required_seconds": plan.idle_cooldown_seconds,
            "eligible": bool(host_conditions["valid"]),
            "performed": False,
            "elapsed_seconds": None,
            "before": host_conditions,
            "after": None,
            "valid": None,
        },
        "resource_controls": {
            "timestep_seconds": plan.timestep_seconds,
            "idle_cooldown_seconds": plan.idle_cooldown_seconds,
            "minimum_free_disk_bytes": plan.minimum_free_disk_bytes,
            "maximum_peak_rss_mib": plan.maximum_peak_rss_mib,
            "power_mode": plan.power_mode,
            "thermal_policy": plan.thermal_policy,
            "task_validity_predicate_id": (
                plan.task_validity_predicate_id
            ),
        },
    }


def _yaml_scalar(value: bool | int | float | str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(value, ensure_ascii=False)


def _replace_top_level(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"(?m)^{re.escape(key)}\s*:[^\r\n]*$")
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise RunnerError(
            f"cannot deterministically replace top-level config key {key}"
        )
    return pattern.sub(f"{key}: {value}", text, count=1)


def render_config(plan: Plan, pair: Pair, block_id: str) -> bytes:
    text = plan.artifacts["config_template"].path.read_text(encoding="utf-8")
    replacements: dict[str, bool | int | float | str] = {
        "SIMULATION_NAME": block_id,
        "LOG_RESULTS": True,
        "ASSETS_DIR": str(pair.map_artifact.path.parent),
        "OBSTACLE_FILE": pair.map_artifact.path.name,
        "SCENARIO": "file",
        "SCENARIO_FILE": str(pair.scenario.path),
        "NUM_ROBOTS": pair.robot_count,
        "RNG_SEED": pair.seed,
        "MAX_TIME": plan.fixed_work_timesteps,
        "TIMESTEP": plan.timestep_seconds,
        "DISPLAY": 0,
        "DRAW_INTERROBOT": 0,
        "DRAW_PATH": 0,
        "DRAW_TRAJ": 0,
        "DRAW_WAYPOINTS": 0,
    }
    replacements.update(pair.config_overrides)
    for key in sorted(replacements):
        text = _replace_top_level(text, key, _yaml_scalar(replacements[key]))
    return text.encode("utf-8")


def _write_exclusive(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=False, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _write_json_exclusive(path: Path, value: Any) -> None:
    _write_exclusive(path, canonical_json_bytes(value))


def _freeze_tree(path: Path) -> None:
    for child in sorted(path.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        try:
            child.chmod(0o555 if child.is_dir() else 0o444)
        except OSError:
            pass
    try:
        path.chmod(0o555)
    except OSError:
        pass


@dataclass(frozen=True)
class ProcessMeasurement:
    returncode: int
    timed_out: bool
    wall_seconds: float
    user_cpu_seconds: float
    system_cpu_seconds: float
    peak_rss_raw: int
    peak_rss_raw_unit: str
    peak_rss_mib: float


def _rss_to_mib(raw: int) -> tuple[str, float]:
    if sys.platform == "darwin":
        return "bytes", raw / (1024 * 1024)
    return "KiB", raw / 1024


def _wait_for_pid(
    pid: int, timeout: float, grace: float
) -> tuple[int, Any, bool]:
    deadline = time.monotonic() + timeout
    while True:
        waited_pid, status, usage = os.wait4(pid, os.WNOHANG)
        if waited_pid == pid:
            return status, usage, False
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.01, remaining))

    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    grace_deadline = time.monotonic() + grace
    while True:
        waited_pid, status, usage = os.wait4(pid, os.WNOHANG)
        if waited_pid == pid:
            return status, usage, True
        if time.monotonic() >= grace_deadline:
            break
        time.sleep(0.01)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    _, status, usage = os.wait4(pid, 0)
    return status, usage, True


def run_measured_process(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    stdout: BinaryIO,
    stderr: BinaryIO,
    timeout: float,
    grace: float,
) -> ProcessMeasurement:
    started = time.monotonic()
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
        close_fds=True,
    )
    status, usage, timed_out = _wait_for_pid(process.pid, timeout, grace)
    wall = time.monotonic() - started
    returncode = os.waitstatus_to_exitcode(status)
    process.returncode = returncode
    unit, rss_mib = _rss_to_mib(usage.ru_maxrss)
    return ProcessMeasurement(
        returncode=returncode,
        timed_out=timed_out,
        wall_seconds=wall,
        user_cpu_seconds=float(usage.ru_utime),
        system_cpu_seconds=float(usage.ru_stime),
        peak_rss_raw=int(usage.ru_maxrss),
        peak_rss_raw_unit=unit,
        peak_rss_mib=rss_mib,
    )


def _terminal_category(measurement: ProcessMeasurement) -> str:
    if measurement.timed_out:
        return "runner-timeout"
    if measurement.returncode < 0:
        return "process-signal"
    if measurement.returncode != 0:
        return "nonzero-simulator-exit"
    return "completed"


def _artifact_snapshot(plan: Plan, pair: Pair, method: Method, config: Artifact) -> None:
    if sha256_file(plan.path) != plan.plan_sha256:
        raise RunnerError("plan bytes changed during execution")
    if sha256_file(Path(__file__).resolve()) != plan.runner_sha256:
        raise RunnerError("runner bytes changed during execution")
    for artifact in (
        *plan.artifacts.values(),
        pair.map_artifact,
        pair.scenario,
        method.binary,
        config,
    ):
        artifact.verify()


def _attempt_environment(
    plan: Plan,
    run_dir: Path,
    metadata_path: Path,
) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in OBSERVER_ENVIRONMENT
    }
    environment.update(
        {
            "OMP_NUM_THREADS": str(plan.omp_threads),
            "OMP_DYNAMIC": "FALSE",
            "OMP_WAIT_POLICY": "passive",
            "LC_ALL": "C",
            "LANG": "C",
        }
    )
    if plan.mode == "fidelity":
        environment.update(
            {
                "DANCERS_TRACE_OUT": str(run_dir / "trace.jsonl"),
                "DANCERS_SUMMARY_OUT": str(run_dir / "summary.json"),
                "DANCERS_RUN_METADATA": str(metadata_path),
                "DANCERS_REQUIRE_PROVENANCE": "1",
                "DANCERS_TRACE_DETAIL": plan.trace_detail,
            }
        )
    return environment


def _validate_timing_stdout(
    stdout_path: Path, expected_tick: int, expected_robots: int
) -> str | None:
    if stdout_path.stat().st_size > 64 * 1024 * 1024:
        return "stdout exceeds the 64 MiB validation bound"
    text = stdout_path.read_text(encoding="utf-8", errors="strict")
    ticks = [int(match.group(1)) for match in TIMESTEP_RE.finditer(text)]
    robots = [
        int(match.group(1)) for match in ROBOT_COUNT_RE.finditer(text)
    ]
    if not ticks or ticks[-1] != expected_tick:
        return (
            f"final reported timestep is {ticks[-1] if ticks else None}; "
            f"expected {expected_tick}"
        )
    if any(tick > expected_tick for tick in ticks):
        return "stdout reports work beyond the fixed timestep"
    if not robots or robots[-1] != expected_robots:
        return (
            f"final reported robot count is "
            f"{robots[-1] if robots else None}; expected {expected_robots}"
        )
    return None


def _validate_fidelity_summary(
    summary_path: Path,
    metadata: dict[str, Any],
    plan: Plan,
    pair: Pair,
) -> tuple[str | None, dict[str, Any] | None]:
    try:
        raw, _ = _load_json(summary_path, "fidelity summary")
        summary = _require_mapping(raw, "fidelity summary")
    except RunnerError as exc:
        return str(exc), None
    checks = {
        "schema_version": SUMMARY_SCHEMA,
        "run_valid": True,
        "final_tick": plan.fixed_work_timesteps,
        "final_robot_count": pair.robot_count,
        "maximum_robot_count": pair.robot_count,
        "frames_recorded": plan.fixed_work_timesteps * 2,
        "termination_reason": "maximum_time",
        "trace_detail": plan.trace_detail,
        "invalid_geometry_evaluations": 0,
        "invalid_robot_frames": 0,
        "nonfinite_numeric_observations": 0,
        "nonfinite_state_observations": 0,
    }
    for key, expected in checks.items():
        if summary.get(key) != expected:
            return (
                f"summary {key}={summary.get(key)!r}; "
                f"expected {expected!r}",
                None,
            )
    summary_metadata = summary.get("metadata")
    if not isinstance(summary_metadata, dict):
        return "summary metadata is missing", None
    for key in (
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
        if summary_metadata.get(key) != metadata[key]:
            return (
                f"summary metadata {key} does not match runner provenance",
                None,
            )
    resolved = summary.get("resolved_config")
    if not isinstance(resolved, dict):
        return "summary resolved_config is missing", None
    for key, expected in (
        ("max_timesteps", plan.fixed_work_timesteps),
        ("configured_robot_count", pair.robot_count),
        ("rng_seed", pair.seed),
        ("display", False),
    ):
        if resolved.get(key) != expected:
            return (
                f"summary resolved_config.{key}={resolved.get(key)!r}; "
                f"expected {expected!r}",
                None,
            )
    paper_metrics = summary.get("paper_metrics")
    required_metrics = {
        "consensus_reached",
        "final_connectivity",
        "final_decision",
        "final_heading_error",
        "final_iterations",
        "final_position_error",
    }
    if (
        not isinstance(paper_metrics, dict)
        or set(paper_metrics) != required_metrics
    ):
        return "summary paper_metrics contract is not exact", None
    for key in (
        "consensus_reached",
        "final_decision",
        "final_iterations",
    ):
        value = paper_metrics[key]
        if not isinstance(value, int) or isinstance(value, bool):
            return f"summary paper_metrics.{key} must be an integer", None
    for key in (
        "final_connectivity",
        "final_heading_error",
        "final_position_error",
    ):
        value = paper_metrics[key]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            return f"summary paper_metrics.{key} must be finite", None
    missing_parity = [
        key for key in SUMMARY_PARITY_FIELDS if key not in summary
    ]
    if missing_parity:
        return (
            f"summary is missing registered parity fields: {missing_parity}",
            None,
        )
    for key in (
        "minimum_pair_clearance_m",
        "minimum_obstacle_clearance_m",
        "pair_collision_exposure_seconds",
        "obstacle_collision_exposure_seconds",
    ):
        value = summary[key]
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            return f"summary {key} must be finite", None
    for key in (
        "pair_collision_events",
        "obstacle_collision_events",
    ):
        value = summary[key]
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or value < 0
        ):
            return f"summary {key} must be a non-negative integer", None

    selected = {key: summary[key] for key in SUMMARY_PARITY_FIELDS}
    task_fields = {key: summary[key] for key in TASK_VALIDITY_FIELDS}
    task_fields.update(
        {
            "minimum_pair_clearance_m": summary[
                "minimum_pair_clearance_m"
            ],
            "minimum_obstacle_clearance_m": summary[
                "minimum_obstacle_clearance_m"
            ],
        }
    )
    task_valid = bool(
        summary["termination_reason"] == "maximum_time"
        and summary["final_tick"] == plan.fixed_work_timesteps
        and summary["final_robot_count"] == pair.robot_count
        and summary["maximum_robot_count"] == pair.robot_count
        and summary["run_valid"] is True
        and all(
            summary[key] == 0
            for key in (
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
        and summary["minimum_pair_clearance_m"] > 0.0
        and summary["minimum_obstacle_clearance_m"] > 0.0
    )
    return None, {
        "task_validity_predicate_id": plan.task_validity_predicate_id,
        "task_valid": task_valid,
        "task_fields": task_fields,
        "paper_metrics": paper_metrics,
        "summary_parity_fields": selected,
    }


def _parse_trace_schema_contract(
    trace_path: Path,
    expected_detail: str,
) -> None:
    try:
        with TraceReader(trace_path) as reader:
            header = reader.header
            if header.schema_version != TRACE_SCHEMA:
                raise RunnerError(
                    "fidelity trace schema_version must be "
                    f"{TRACE_SCHEMA!r}; observed "
                    f"{header.schema_version!r}"
                )
            if header.trace_detail != expected_detail:
                raise RunnerError(
                    "fidelity trace detail must be "
                    f"{expected_detail!r}; observed "
                    f"{header.trace_detail!r}"
                )
            observer = header.canonical["observer"]
            if observer.get("digest_algorithm") != TRACE_DIGEST_ALGORITHM:
                raise RunnerError(
                    "fidelity trace digest_algorithm does not match "
                    "the v3 contract"
                )
            future_state_scope = observer.get("future_state_scope")
            message_checksum_scope = observer.get(
                "message_checksum_scope"
            )
            if (
                not isinstance(future_state_scope, str)
                or not future_state_scope.strip()
                or not isinstance(message_checksum_scope, str)
                or not message_checksum_scope.strip()
            ):
                raise RunnerError(
                    "fidelity trace v3 scope metadata is incomplete"
                )
    except (TraceFormatError, OSError, ValueError) as exc:
        raise RunnerError(
            f"fidelity trace schema contract is invalid: {exc}"
        ) from exc


def _run_attempt(
    plan: Plan,
    pair: Pair,
    block_id: str,
    repetition: int,
    order: str,
    order_position: int,
    sequence: int,
    method: Method,
    config: Artifact,
    attempts_root: Path,
    attempt_kind: str = "measured",
) -> dict[str, Any]:
    attempt_id = (
        f"{sequence:06d}__{block_id}__{method.method_id}"
    )
    run_dir = attempts_root / attempt_id
    run_dir.mkdir(exist_ok=False)
    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"
    metadata_path = run_dir / "observer_metadata.json"
    metadata = {
        "run_id": attempt_id,
        "implementation_id": method.method_id,
        "algorithm_revision": method.base_revision,
        "source_head_revision": method.head_revision,
        "source_tree_hash": method.tree_hash,
        "common_patch_sha256": plan.build_manifest["repository"][
            "common_patch_format_email_sha256"
        ],
        "common_patch_full_index_diff_sha256": plan.build_manifest[
            "repository"
        ]["common_patch_full_index_diff_sha256"],
        "binary_sha256": method.binary.sha256,
        "build_manifest_sha256": plan.artifacts["build_manifest"].sha256,
        "pixi_lock_sha256": plan.artifacts["pixi_lock"].sha256,
        "protocol_sha256": plan.artifacts["protocol"].sha256,
        "experiment_manifest_sha256": plan.artifacts[
            "experiment_manifest"
        ].sha256,
        "analysis_revision_sha256": plan.artifacts[
            "analysis_revision"
        ].sha256,
        "runner_sha256": plan.runner_sha256,
        "config_sha256": config.sha256,
        "map_sha256": pair.map_artifact.sha256,
        "scenario_sha256": pair.scenario.sha256,
        "omp_threads": plan.omp_threads,
        "phase": plan.phase,
        "mode": plan.mode,
        "pair_id": pair.pair_id,
        "repetition": repetition,
        "order": order,
        "attempt_kind": attempt_kind,
        "maximum_peak_rss_mib": plan.maximum_peak_rss_mib,
        "power_mode": plan.power_mode,
        "thermal_policy": plan.thermal_policy,
        "task_validity_predicate_id": (
            plan.task_validity_predicate_id
        ),
    }
    _write_json_exclusive(run_dir / "runner_metadata.json", metadata)
    if plan.mode == "fidelity":
        _write_json_exclusive(metadata_path, metadata)

    measurement: ProcessMeasurement | None = None
    orchestration_error: str | None = None
    try:
        _artifact_snapshot(plan, pair, method, config)
        config_argument = os.path.relpath(config.path, plan.run_cwd)
        environment = _attempt_environment(plan, run_dir, metadata_path)
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            measurement = run_measured_process(
                [str(method.binary.path), "--cfg", config_argument],
                cwd=plan.run_cwd,
                environment=environment,
                stdout=stdout,
                stderr=stderr,
                timeout=plan.timeout_seconds,
                grace=plan.termination_grace_seconds,
            )
            stdout.flush()
            stderr.flush()
            os.fsync(stdout.fileno())
            os.fsync(stderr.fileno())
        _artifact_snapshot(plan, pair, method, config)
    except Exception as exc:
        orchestration_error = f"{type(exc).__name__}: {exc}"
        if not stdout_path.exists():
            _write_exclusive(stdout_path, b"")
        if not stderr_path.exists():
            _write_exclusive(
                stderr_path, (orchestration_error + "\n").encode("utf-8")
            )

    if measurement is None:
        terminal = "measurement-wrapper-failure"
        resource: dict[str, Any] = {
            "returncode": None,
            "exit_code": None,
            "signal": None,
            "timed_out": False,
            "wall_seconds": None,
            "user_cpu_seconds": None,
            "system_cpu_seconds": None,
            "total_cpu_seconds": None,
            "peak_rss_raw": None,
            "peak_rss_raw_unit": None,
            "peak_rss_mib": None,
        }
    else:
        terminal = _terminal_category(measurement)
        resource = {
            "returncode": measurement.returncode,
            "exit_code": (
                measurement.returncode
                if measurement.returncode >= 0
                else None
            ),
            "signal": (
                -measurement.returncode
                if measurement.returncode < 0
                else None
            ),
            "timed_out": measurement.timed_out,
            "wall_seconds": measurement.wall_seconds,
            "user_cpu_seconds": measurement.user_cpu_seconds,
            "system_cpu_seconds": measurement.system_cpu_seconds,
            "total_cpu_seconds": (
                measurement.user_cpu_seconds
                + measurement.system_cpu_seconds
            ),
            "peak_rss_raw": measurement.peak_rss_raw,
            "peak_rss_raw_unit": measurement.peak_rss_raw_unit,
            "peak_rss_mib": measurement.peak_rss_mib,
        }
    resource["maximum_peak_rss_mib"] = plan.maximum_peak_rss_mib
    resource["peak_rss_within_limit"] = (
        resource["peak_rss_mib"] is not None
        and resource["peak_rss_mib"] <= plan.maximum_peak_rss_mib
    )

    trace_path = run_dir / "trace.jsonl"
    summary_path = run_dir / "summary.json"
    fidelity_record: dict[str, Any] | None = None
    if (
        terminal == "completed"
        and resource["peak_rss_mib"] > plan.maximum_peak_rss_mib
    ):
        terminal = "peak-rss-limit-exceeded"
        orchestration_error = (
            f"peak RSS {resource['peak_rss_mib']:.6f} MiB exceeds "
            f"the frozen {plan.maximum_peak_rss_mib:.6f} MiB limit"
        )
    if plan.mode == "fidelity" and terminal == "completed":
        if (
            not trace_path.is_file()
            or trace_path.stat().st_size == 0
            or not summary_path.is_file()
            or summary_path.stat().st_size == 0
        ):
            terminal = "missing-or-malformed-observer-output"
        else:
            try:
                _parse_trace_schema_contract(
                    trace_path,
                    plan.trace_detail,
                )
            except RunnerError as exc:
                terminal = "missing-or-malformed-observer-output"
                orchestration_error = str(exc)
            if terminal == "completed":
                validation_error, fidelity_record = (
                    _validate_fidelity_summary(
                        summary_path,
                        metadata,
                        plan,
                        pair,
                    )
                )
                if validation_error is not None:
                    terminal = "missing-or-malformed-final-metrics"
                    orchestration_error = validation_error
    elif plan.mode == "timing" and terminal == "completed":
        try:
            validation_error = _validate_timing_stdout(
                stdout_path,
                plan.fixed_work_timesteps,
                pair.robot_count,
            )
        except (OSError, UnicodeDecodeError) as exc:
            validation_error = f"{type(exc).__name__}: {exc}"
        if validation_error is not None:
            terminal = "missing-or-malformed-final-metrics"
            orchestration_error = validation_error

    result = {
        "schema_version": RESULT_SCHEMA,
        "sequence": sequence,
        "attempt_id": attempt_id,
        "block_id": block_id,
        "pair_id": pair.pair_id,
        "repetition": repetition,
        "attempt_kind": attempt_kind,
        "order": order,
        "order_position": order_position,
        "phase": plan.phase,
        "mode": plan.mode,
        "environment": pair.environment,
        "seed": pair.seed,
        "robot_count": pair.robot_count,
        "method": method.method_id,
        "terminal_category": terminal,
        "orchestration_error": orchestration_error,
        "task_valid": (
            fidelity_record["task_valid"]
            if fidelity_record is not None
            else None
        ),
        "fidelity_metrics": fidelity_record,
        "resource": resource,
        "provenance": metadata,
        "artifacts": {
            "config": {"path": str(config.path), "sha256": config.sha256},
            "stdout": {
                "path": str(stdout_path),
                "sha256": sha256_file(stdout_path),
            },
            "stderr": {
                "path": str(stderr_path),
                "sha256": sha256_file(stderr_path),
            },
            "trace": (
                {
                    "path": str(trace_path),
                    "sha256": sha256_file(trace_path),
                }
                if trace_path.is_file()
                else None
            ),
            "summary": (
                {
                    "path": str(summary_path),
                    "sha256": sha256_file(summary_path),
                }
                if summary_path.is_file()
                else None
            ),
        },
        "completed_utc": utc_now(),
    }
    _write_json_exclusive(run_dir / "result.json", result)
    _freeze_tree(run_dir)
    return result


def _pair_result(
    plan: Plan,
    block_id: str,
    pair: Pair,
    repetition: int,
    order: str,
    attempts: list[dict[str, Any]],
    host_conditions_before: dict[str, Any],
    host_conditions_after: dict[str, Any],
) -> dict[str, Any]:
    by_method = {item["method"]: item for item in attempts}
    result: dict[str, Any] = {
        "schema_version": PAIR_SCHEMA,
        "block_id": block_id,
        "pair_id": pair.pair_id,
        "repetition": repetition,
        "order": order,
        "phase": plan.phase,
        "mode": plan.mode,
        "environment": pair.environment,
        "seed": pair.seed,
        "robot_count": pair.robot_count,
        "attempt_ids": {
            key: by_method[key]["attempt_id"]
            for key in ("reference", "optimized")
        },
        "config_sha256_equal": (
            by_method["reference"]["provenance"]["config_sha256"]
            == by_method["optimized"]["provenance"]["config_sha256"]
        ),
        "terminal_categories": {
            key: by_method[key]["terminal_category"]
            for key in ("reference", "optimized")
        },
        "semantic_trace_equal": None,
        "frames_compared": None,
        "mismatch": None,
        "paper_metrics_equal": None,
        "summary_parity_fields_equal": None,
        "summary_mismatch": None,
        "task_validity_predicate_id": (
            plan.task_validity_predicate_id
        ),
        "task_valid": None,
        "host_conditions_before": host_conditions_before,
        "host_conditions_after": host_conditions_after,
        "host_block_valid": _host_block_valid(
            host_conditions_before, host_conditions_after
        ),
    }
    if plan.mode == "fidelity" and all(
        by_method[key]["terminal_category"] == "completed"
        for key in ("reference", "optimized")
    ):
        left = Path(by_method["reference"]["artifacts"]["trace"]["path"])
        right = Path(by_method["optimized"]["artifacts"]["trace"]["path"])
        try:
            comparison = compare_traces(left, right)
            result["semantic_trace_equal"] = comparison.equal
            result["frames_compared"] = comparison.frames_compared
            if comparison.mismatch is not None:
                result["mismatch"] = {
                    "tick": comparison.mismatch.tick,
                    "phase": comparison.mismatch.phase,
                    "path": comparison.mismatch.path,
                    "reason": comparison.mismatch.reason,
                    "left": comparison.mismatch.left,
                    "right": comparison.mismatch.right,
                }
        except (TraceFormatError, OSError, ValueError) as exc:
            result["semantic_trace_equal"] = False
            result["mismatch"] = {
                "tick": None,
                "phase": "trace-validation",
                "path": "$",
                "reason": f"{type(exc).__name__}: {exc}",
                "left": None,
                "right": None,
            }
    if plan.mode == "fidelity" and all(
        by_method[key]["terminal_category"] == "completed"
        and by_method[key]["fidelity_metrics"] is not None
        for key in ("reference", "optimized")
    ):
        reference_metrics = by_method["reference"]["fidelity_metrics"]
        optimized_metrics = by_method["optimized"]["fidelity_metrics"]
        paper_difference = first_json_difference(
            reference_metrics["paper_metrics"],
            optimized_metrics["paper_metrics"],
            "$.paper_metrics",
        )
        summary_difference = first_json_difference(
            reference_metrics["summary_parity_fields"],
            optimized_metrics["summary_parity_fields"],
            "$.summary_parity_fields",
        )
        result["paper_metrics_equal"] = paper_difference is None
        result["summary_parity_fields_equal"] = (
            summary_difference is None
        )
        difference = paper_difference or summary_difference
        if difference is not None:
            path, reason, left, right = difference
            result["summary_mismatch"] = {
                "path": path,
                "reason": reason,
                "reference": left,
                "optimized": right,
            }
        result["task_valid"] = all(
            by_method[key]["task_valid"] is True
            for key in ("reference", "optimized")
        )
    result["block_terminal_category"] = (
        "completed"
        if result["host_block_valid"]
        else "host-power-thermal-contamination"
    )
    result["pair_valid"] = (
        result["host_block_valid"]
        and
        result["config_sha256_equal"]
        and all(
            category == "completed"
            for category in result["terminal_categories"].values()
        )
        and (
            plan.mode == "timing"
            or (
                result["semantic_trace_equal"] is True
                and result["paper_metrics_equal"] is True
                and result["summary_parity_fields_equal"] is True
                and result["task_valid"] is True
            )
        )
    )
    return result


def _append_jsonl(handle: BinaryIO, value: Any) -> None:
    handle.write(canonical_json_bytes(value))
    handle.flush()
    os.fsync(handle.fileno())


def _csv_row(result: dict[str, Any]) -> dict[str, Any]:
    resource = result["resource"]
    artifacts = result["artifacts"]
    return {
        "sequence": result["sequence"],
        "attempt_id": result["attempt_id"],
        "block_id": result["block_id"],
        "pair_id": result["pair_id"],
        "repetition": result["repetition"],
        "attempt_kind": result["attempt_kind"],
        "order": result["order"],
        "order_position": result["order_position"],
        "phase": result["phase"],
        "mode": result["mode"],
        "environment": result["environment"],
        "seed": result["seed"],
        "robot_count": result["robot_count"],
        "method": result["method"],
        "terminal_category": result["terminal_category"],
        "returncode": resource["returncode"],
        "exit_code": resource["exit_code"],
        "signal": resource["signal"],
        "timed_out": resource["timed_out"],
        "wall_seconds": resource["wall_seconds"],
        "user_cpu_seconds": resource["user_cpu_seconds"],
        "system_cpu_seconds": resource["system_cpu_seconds"],
        "total_cpu_seconds": resource["total_cpu_seconds"],
        "peak_rss_raw": resource["peak_rss_raw"],
        "peak_rss_raw_unit": resource["peak_rss_raw_unit"],
        "peak_rss_mib": resource["peak_rss_mib"],
        "maximum_peak_rss_mib": resource["maximum_peak_rss_mib"],
        "peak_rss_within_limit": resource["peak_rss_within_limit"],
        "config_sha256": result["provenance"]["config_sha256"],
        "binary_sha256": result["provenance"]["binary_sha256"],
        "map_sha256": result["provenance"]["map_sha256"],
        "scenario_sha256": result["provenance"]["scenario_sha256"],
        "stdout_sha256": artifacts["stdout"]["sha256"],
        "stderr_sha256": artifacts["stderr"]["sha256"],
        "trace_sha256": (
            artifacts["trace"]["sha256"] if artifacts["trace"] else None
        ),
        "summary_sha256": (
            artifacts["summary"]["sha256"] if artifacts["summary"] else None
        ),
    }


CSV_FIELDS = list(
    _csv_row(
        {
            "sequence": None,
            "attempt_id": None,
            "block_id": None,
            "pair_id": None,
            "repetition": None,
            "attempt_kind": None,
            "order": None,
            "order_position": None,
            "phase": None,
            "mode": None,
            "environment": None,
            "seed": None,
            "robot_count": None,
            "method": None,
            "terminal_category": None,
            "resource": {
                key: None
                for key in (
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
                )
            },
            "provenance": {
                key: None
                for key in (
                    "config_sha256",
                    "binary_sha256",
                    "map_sha256",
                    "scenario_sha256",
                )
            },
            "artifacts": {
                "stdout": {"sha256": None},
                "stderr": {"sha256": None},
                "trace": None,
                "summary": None,
            },
        }
    )
)


def _execution_grid(plan: Plan) -> tuple[int, ...] | None:
    if plan.capacity is not None:
        return plan.capacity.grid
    if plan.capacity_evaluation is not None:
        return plan.capacity_evaluation.grid
    return None


def _ordered_pairs(plan: Plan) -> list[Pair]:
    grid = _execution_grid(plan)
    if grid is None:
        return list(plan.pairs)
    rank = {count: index for index, count in enumerate(grid)}
    return sorted(
        plan.pairs,
        key=lambda pair: (
            pair.seed,
            rank[pair.robot_count],
            pair.pair_id,
        ),
    )


def _planned_blocks(
    plan: Plan, ordered_pairs: list[Pair]
) -> list[dict[str, Any]]:
    schedule: list[tuple[Pair, int]] = []
    grid = _execution_grid(plan)
    if grid is None:
        for repetition in range(1, plan.repetitions + 1):
            schedule.extend(
                (pair, repetition) for pair in ordered_pairs
            )
    else:
        by_seed_count = {
            (pair.seed, pair.robot_count): pair for pair in ordered_pairs
        }
        seeds = sorted({pair.seed for pair in ordered_pairs})
        base_counts = list(grid)
        count_randomizer = random.Random(
            plan.order_seed ^ 0x4341504143495459
        )
        count_randomizer.shuffle(base_counts)
        cycle = 0
        for repetition in range(1, plan.repetitions + 1):
            for seed in seeds:
                count_order = (
                    base_counts if cycle % 2 == 0 else base_counts[::-1]
                )
                schedule.extend(
                    (by_seed_count[(seed, count)], repetition)
                    for count in count_order
                )
                cycle += 1
    return [
        {
            "block_id": f"{pair.pair_id}__rep-{repetition:03d}",
            "pair_id": pair.pair_id,
            "repetition": repetition,
            "order": None,
            "environment": pair.environment,
            "seed": pair.seed,
            "robot_count": pair.robot_count,
        }
        for pair, repetition in schedule
    ]


def _seed_unit_aggregate(
    plan: Plan,
    attempts: list[dict[str, Any]],
    pair_results: list[dict[str, Any]],
    method: str,
    robot_count: int,
) -> dict[str, Any]:
    valid_blocks = {
        item["block_id"]
        for item in pair_results
        if item["pair_valid"]
    }
    expected_seeds = sorted(
        {
            pair.seed
            for pair in plan.pairs
            if pair.robot_count == robot_count
        }
    )
    seed_units = []
    complete = True
    for seed in expected_seeds:
        measurements = [
            item
            for item in attempts
            if item["method"] == method
            and item["robot_count"] == robot_count
            and item["seed"] == seed
            and item["attempt_kind"] == "measured"
            and item["terminal_category"] == "completed"
            and item["block_id"] in valid_blocks
        ]
        unit_complete = len(measurements) == plan.repetitions
        complete = complete and unit_complete
        seed_units.append(
            {
                "seed": seed,
                "complete": unit_complete,
                "measurement_count": len(measurements),
                "expected_repetitions": plan.repetitions,
                "median_cpu_seconds": (
                    statistics.median(
                        item["resource"]["total_cpu_seconds"]
                        for item in measurements
                    )
                    if unit_complete
                    else None
                ),
                "median_wall_seconds": (
                    statistics.median(
                        item["resource"]["wall_seconds"]
                        for item in measurements
                    )
                    if unit_complete
                    else None
                ),
                "median_peak_rss_mib": (
                    statistics.median(
                        item["resource"]["peak_rss_mib"]
                        for item in measurements
                    )
                    if unit_complete
                    else None
                ),
            }
        )
    complete_units = [item for item in seed_units if item["complete"]]
    return {
        "method": method,
        "robot_count": robot_count,
        "complete": complete and bool(expected_seeds),
        "seed_unit_count": len(complete_units),
        "expected_seed_unit_count": len(expected_seeds),
        "seed_units": seed_units,
        "median_across_seed_units_cpu_seconds": (
            statistics.median(
                item["median_cpu_seconds"] for item in complete_units
            )
            if complete and complete_units
            else None
        ),
        "median_across_seed_units_wall_seconds": (
            statistics.median(
                item["median_wall_seconds"] for item in complete_units
            )
            if complete and complete_units
            else None
        ),
        "median_across_seed_units_peak_rss_mib": (
            statistics.median(
                item["median_peak_rss_mib"] for item in complete_units
            )
            if complete and complete_units
            else None
        ),
    }


def _capacity_evaluation(
    plan: Plan,
    attempts: list[dict[str, Any]],
    pair_results: list[dict[str, Any]],
    state: dict[str, Any],
) -> None:
    assert plan.capacity is not None
    aggregates = {
        (method, count): _seed_unit_aggregate(
            plan, attempts, pair_results, method, count
        )
        for count in plan.capacity.grid
        for method in ("reference", "optimized")
    }
    anchor = aggregates[
        ("reference", plan.capacity.reference_anchor_n)
    ]
    if anchor["complete"]:
        state["budget"] = {
            "status": "provisional-compute-budget",
            "source": (
                "median across seed units after median of three "
                "within-seed repetitions"
            ),
            "reference_anchor_n": plan.capacity.reference_anchor_n,
            "cpu_seconds": anchor[
                "median_across_seed_units_cpu_seconds"
            ],
            "wall_seconds": anchor[
                "median_across_seed_units_wall_seconds"
            ],
            "frozen_after_complete_anchor": True,
            "reference_seed_units": anchor["seed_units"],
        }
    provisional_passing: list[int] = []
    for count in plan.capacity.grid:
        reference = aggregates[("reference", count)]
        optimized = aggregates[("optimized", count)]
        complete = (
            state["budget"] is not None
            and reference["complete"]
            and optimized["complete"]
        )
        provisional_pass = bool(
            complete
            and optimized["median_across_seed_units_cpu_seconds"]
            <= state["budget"]["cpu_seconds"]
            and optimized["median_across_seed_units_wall_seconds"]
            <= state["budget"]["wall_seconds"]
        )
        if provisional_pass:
            provisional_passing.append(count)
        state["evaluations"].append(
            {
                "robot_count": count,
                "complete": complete,
                "reference": reference,
                "optimized": optimized,
                "provisional_compute_budget_pass": provisional_pass,
                "validated_capacity_pass": False,
                "pending_gates": [
                    "task_validity",
                    "semantic_fidelity",
                    "memory_hypothesis",
                ],
            }
        )
    state["provisional_largest_compute_passing_n"] = (
        max(provisional_passing) if provisional_passing else None
    )
    failures = [
        item["robot_count"]
        for item in state["evaluations"]
        if not item["provisional_compute_budget_pass"]
    ]
    state["first_provisional_compute_failing_n"] = (
        min(failures) if failures else None
    )
    state["complete"] = all(
        item["complete"] for item in state["evaluations"]
    )


def _is_initial_calibration_fidelity_gate(plan: Plan) -> bool:
    expected = {
        ("open", seed, 8) for seed in PHASE_SEEDS["calibration"]
    }
    observed = {
        (pair.environment, pair.seed, pair.robot_count)
        for pair in plan.pairs
    }
    return bool(
        plan.phase == "calibration"
        and plan.mode == "fidelity"
        and plan.trace_detail == "digest"
        and len(plan.pairs) == len(expected)
        and observed == expected
    )


def execute(plan: Plan, output: Path | str) -> dict[str, Any]:
    output_path = Path(output).resolve()
    if output_path.exists() or output_path.is_symlink():
        raise RunnerError(f"output path already exists: {output_path}")
    for method in plan.methods.values():
        try:
            output_path.relative_to(method.source_dir)
        except ValueError:
            pass
        else:
            raise RunnerError("output must be outside both source worktrees")

    verification = preflight(plan, output_path.parent)
    verification["idle_cooldown"] = _enforce_idle_cooldown(
        plan,
        verification["host_conditions"],
        output_path.parent,
    )
    output_path.mkdir(parents=True, exist_ok=False)
    blocks_root = output_path / "blocks"
    attempts_root = output_path / "attempts"
    blocks_root.mkdir()
    attempts_root.mkdir()
    _write_exclusive(
        output_path / "frozen_plan.json", plan.path.read_bytes()
    )
    _write_json_exclusive(output_path / "preflight.json", verification)

    ordered_pairs = _ordered_pairs(plan)
    planned_blocks = _planned_blocks(plan, ordered_pairs)
    randomizer = random.Random(plan.order_seed)
    extra_order = "AB" if randomizer.getrandbits(1) == 0 else "BA"
    order_counts = {
        "AB": len(planned_blocks) // 2,
        "BA": len(planned_blocks) // 2,
    }
    if len(planned_blocks) % 2:
        order_counts[extra_order] += 1
    orders = (
        ["AB"] * order_counts["AB"] + ["BA"] * order_counts["BA"]
    )
    randomizer.shuffle(orders)
    for block, order in zip(planned_blocks, orders):
        block["order"] = order

    warmup_blocks: list[dict[str, Any]] = []
    if plan.mode == "timing":
        seen_maps: set[str] = set()
        for pair in ordered_pairs:
            if pair.map_artifact.sha256 in seen_maps:
                continue
            seen_maps.add(pair.map_artifact.sha256)
            warmup_blocks.append(
                {
                    "block_id": (
                        f"warmup-{len(warmup_blocks) + 1:03d}-"
                        f"{pair.map_artifact.sha256[:12]}"
                    ),
                    "pair_id": pair.pair_id,
                    "repetition": 0,
                    "order": "warmup",
                    "environment": pair.environment,
                    "seed": pair.seed,
                    "robot_count": pair.robot_count,
                    "map_sha256": pair.map_artifact.sha256,
                }
            )
    manifest = {
        "schema_version": PLAN_SCHEMA,
        "created_utc": utc_now(),
        "plan_path": str(plan.path),
        "plan_sha256": plan.plan_sha256,
        "phase": plan.phase,
        "mode": plan.mode,
        "serial_execution": True,
        "order_policy": (
            "seeded balanced AB/BA assignment; measured blocks only"
        ),
        "count_order_policy": (
            "seeded base permutation alternated forward/reverse across "
            "seed-repetition units; all finite grid points execute "
            "nonadaptively"
            if _execution_grid(plan) is not None
            else "frozen pair order repeated in complete rounds"
        ),
        "order_seed": plan.order_seed,
        "fixed_work_timesteps": plan.fixed_work_timesteps,
        "timestep_seconds": plan.timestep_seconds,
        "idle_cooldown_seconds": plan.idle_cooldown_seconds,
        "idle_cooldown": verification["idle_cooldown"],
        "repetitions": plan.repetitions,
        "warmups_per_binary_map": plan.warmups_per_binary_map,
        "minimum_free_disk_bytes": plan.minimum_free_disk_bytes,
        "maximum_peak_rss_mib": plan.maximum_peak_rss_mib,
        "power_mode": plan.power_mode,
        "thermal_policy": plan.thermal_policy,
        "task_validity_predicate_id": (
            plan.task_validity_predicate_id
        ),
        "preflight_host_conditions": verification["host_conditions"],
        "warmup_blocks": warmup_blocks,
        "blocks": planned_blocks,
        "capacity_search": (
            {
                "environment": plan.capacity.environment,
                "reference_anchor_n": plan.capacity.reference_anchor_n,
                "grid": list(plan.capacity.grid),
                "max_points": plan.capacity.max_points,
            }
            if plan.capacity
            else None
        ),
        "capacity_evaluation": (
            plan.capacity_evaluation.record()
            if plan.capacity_evaluation is not None
            else None
        ),
    }
    _write_json_exclusive(output_path / "run_manifest.json", manifest)

    attempts: list[dict[str, Any]] = []
    pair_results: list[dict[str, Any]] = []
    capacity_state: dict[str, Any] | None = None
    if plan.capacity:
        capacity_state = {
            "schema_version": "dancers-bounded-capacity-search-v2",
            "status": "provisional-compute-only",
            "validated": False,
            "environment": plan.capacity.environment,
            "grid": list(plan.capacity.grid),
            "max_points": plan.capacity.max_points,
            "budget": None,
            "evaluations": [],
            "largest_passing_n": None,
            "first_failing_n": None,
            "provisional_largest_compute_passing_n": None,
            "first_provisional_compute_failing_n": None,
            "pending_gates": [
                "task_validity",
                "semantic_fidelity",
                "memory_hypothesis",
            ],
            "complete": False,
            "stop_reason": None,
            "unexecuted_grid_points": [],
        }

    sequence = 0
    stop = False
    warmups_valid = True
    host_contaminated_blocks: list[str] = []
    with (output_path / "runs.jsonl").open("xb") as runs_handle, (
        output_path / "pairs.jsonl"
    ).open("xb") as pairs_handle:
        for block in warmup_blocks:
            host_before = _host_condition_snapshot(plan, output_path)
            pair = next(
                item for item in plan.pairs
                if item.pair_id == block["pair_id"]
            )
            block_dir = blocks_root / block["block_id"]
            block_dir.mkdir(exist_ok=False)
            config_bytes = render_config(
                plan, pair, block["block_id"]
            )
            config_path = block_dir / "config.yaml"
            _write_exclusive(config_path, config_bytes)
            config = Artifact(
                config_path,
                hashlib.sha256(config_bytes).hexdigest(),
                f"{block['block_id']} rendered config",
            )
            block_attempts: list[dict[str, Any]] = []
            for position, method_id in enumerate(
                ("reference", "optimized"), start=1
            ):
                sequence += 1
                result = _run_attempt(
                    plan,
                    pair,
                    block["block_id"],
                    block["repetition"],
                    block["order"],
                    position,
                    sequence,
                    plan.methods[method_id],
                    config,
                    attempts_root,
                    attempt_kind="warmup",
                )
                attempts.append(result)
                block_attempts.append(result)
                _append_jsonl(runs_handle, result)
            host_after = _host_condition_snapshot(plan, output_path)
            by_method = {
                item["method"]: item for item in block_attempts
            }
            warmup_result = {
                "schema_version": "dancers-comparison-warmup-v1",
                "block_id": block["block_id"],
                "pair_id": pair.pair_id,
                "environment": pair.environment,
                "seed": pair.seed,
                "robot_count": pair.robot_count,
                "map_sha256": pair.map_artifact.sha256,
                "attempt_ids": {
                    key: by_method[key]["attempt_id"]
                    for key in ("reference", "optimized")
                },
                "config_sha256_equal": (
                    by_method["reference"]["provenance"][
                        "config_sha256"
                    ]
                    == by_method["optimized"]["provenance"][
                        "config_sha256"
                    ]
                ),
                "terminal_categories": {
                    key: by_method[key]["terminal_category"]
                    for key in ("reference", "optimized")
                },
                "host_conditions_before": host_before,
                "host_conditions_after": host_after,
                "host_block_valid": _host_block_valid(
                    host_before, host_after
                ),
            }
            warmup_result["warmup_valid"] = (
                warmup_result["config_sha256_equal"]
                and all(
                    category == "completed"
                    for category in warmup_result[
                        "terminal_categories"
                    ].values()
                )
                and warmup_result["host_block_valid"]
            )
            if not warmup_result["host_block_valid"]:
                warmup_result["block_terminal_category"] = (
                    "host-power-thermal-contamination"
                )
                host_contaminated_blocks.append(block["block_id"])
            else:
                warmup_result["block_terminal_category"] = "completed"
            warmups_valid = (
                warmups_valid and warmup_result["warmup_valid"]
            )
            _write_json_exclusive(
                block_dir / "warmup.json", warmup_result
            )
            _freeze_tree(block_dir)

        if not warmups_valid:
            stop = True

        for block in planned_blocks:
            if stop:
                break
            pair = next(
                item for item in plan.pairs
                if item.pair_id == block["pair_id"]
            )
            host_before = _host_condition_snapshot(plan, output_path)

            block_dir = blocks_root / block["block_id"]
            block_dir.mkdir(exist_ok=False)
            config_bytes = render_config(
                plan, pair, block["block_id"]
            )
            config_path = block_dir / "config.yaml"
            _write_exclusive(config_path, config_bytes)
            config = Artifact(
                config_path,
                hashlib.sha256(config_bytes).hexdigest(),
                f"{block['block_id']} rendered config",
            )
            method_order = (
                ("reference", "optimized")
                if block["order"] == "AB"
                else ("optimized", "reference")
            )
            block_attempts: list[dict[str, Any]] = []
            for position, method_id in enumerate(method_order, start=1):
                sequence += 1
                result = _run_attempt(
                    plan,
                    pair,
                    block["block_id"],
                    block["repetition"],
                    block["order"],
                    position,
                    sequence,
                    plan.methods[method_id],
                    config,
                    attempts_root,
                )
                attempts.append(result)
                block_attempts.append(result)
                _append_jsonl(runs_handle, result)
            host_after = _host_condition_snapshot(plan, output_path)
            pair_result = _pair_result(
                plan,
                block["block_id"],
                pair,
                block["repetition"],
                block["order"],
                block_attempts,
                host_before,
                host_after,
            )
            _write_json_exclusive(block_dir / "pair.json", pair_result)
            pair_results.append(pair_result)
            _append_jsonl(pairs_handle, pair_result)
            _freeze_tree(block_dir)
            if not pair_result["host_block_valid"]:
                host_contaminated_blocks.append(block["block_id"])
                stop = True
            elif (
                _is_initial_calibration_fidelity_gate(plan)
                and not pair_result["pair_valid"]
            ):
                stop = True

        if plan.capacity:
            assert capacity_state is not None
            _capacity_evaluation(
                plan, attempts, pair_results, capacity_state
            )

    if capacity_state is not None:
        executed_counts = {
            item["robot_count"]
            for item in attempts
            if item["attempt_kind"] == "measured"
        }
        capacity_state["unexecuted_grid_points"] = [
            count
            for count in plan.capacity.grid
            if count not in executed_counts
        ]
        if capacity_state["stop_reason"] is None:
            capacity_state["stop_reason"] = (
                "host-or-warmup-contamination"
                if capacity_state["unexecuted_grid_points"]
                else "finite-grid-complete-no-early-outcome-stop"
            )
        _write_json_exclusive(
            output_path / "capacity.json", capacity_state
        )

    with (output_path / "runs.csv").open(
        "x", newline="", encoding="utf-8"
    ) as csv_handle:
        writer = csv.DictWriter(csv_handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for result in attempts:
            writer.writerow(_csv_row(result))
        csv_handle.flush()
        os.fsync(csv_handle.fileno())

    completed = {
        "schema_version": "dancers-laptop-comparison-session-v1",
        "completed_utc": utc_now(),
        "attempt_count": len(attempts),
        "warmup_attempt_count": sum(
            item["attempt_kind"] == "warmup" for item in attempts
        ),
        "warmups_valid": warmups_valid,
        "timestep_seconds": plan.timestep_seconds,
        "idle_cooldown_seconds": plan.idle_cooldown_seconds,
        "idle_cooldown": verification["idle_cooldown"],
        "minimum_free_disk_bytes": plan.minimum_free_disk_bytes,
        "maximum_peak_rss_mib": plan.maximum_peak_rss_mib,
        "power_mode": plan.power_mode,
        "thermal_policy": plan.thermal_policy,
        "task_validity_predicate_id": (
            plan.task_validity_predicate_id
        ),
        "host_contaminated_blocks": host_contaminated_blocks,
        "pair_count": len(pair_results),
        "all_attempts_completed": all(
            item["terminal_category"] == "completed"
            for item in attempts
        ),
        "all_pairs_valid": all(
            item["pair_valid"] for item in pair_results
        ) and warmups_valid,
        "stopped_early": len(pair_results) < len(planned_blocks),
        "capacity": capacity_state,
        "capacity_evaluation": (
            plan.capacity_evaluation.record()
            if plan.capacity_evaluation is not None
            else None
        ),
    }
    _write_json_exclusive(output_path / "session.json", completed)
    _freeze_tree(output_path)
    return completed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument(
        "--phase",
        choices=tuple(PHASE_SEEDS),
        required=True,
        help="must exactly match the frozen plan phase",
    )
    parser.add_argument(
        "--mode",
        choices=("timing", "fidelity"),
        required=True,
        help="must exactly match the frozen plan mode",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--unlock-validation",
        action="store_true",
        help="required in addition to --phase validation",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        plan = load_plan(args.plan)
        if args.phase != plan.phase:
            raise RunnerError(
                f"--phase {args.phase} does not match plan phase {plan.phase}"
            )
        if args.mode != plan.mode:
            raise RunnerError(
                f"--mode {args.mode} does not match plan mode {plan.mode}"
            )
        if plan.phase == "validation" and not args.unlock_validation:
            raise RunnerError(
                "validation remains locked; explicitly pass "
                "--phase validation --unlock-validation"
            )
        if args.dry_run:
            verification = preflight(plan)
            print(json.dumps(verification, sort_keys=True))
            return 0
        if args.output is None:
            raise RunnerError("--output is required unless --dry-run is used")
        result = execute(plan, args.output)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["all_pairs_valid"] else 1
    except RunnerError as exc:
        print(f"comparison runner rejected the request: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
