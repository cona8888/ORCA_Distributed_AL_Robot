#!/usr/bin/env python3
"""Read-only analyzer for the frozen DANCeRS--ORCA comparison.

The module has no process-launching interface.  It authenticates an immutable
session, projects native DANCeRS and ORCA position traces onto the registered
0.1 s grid, derives the preregistered continuous-geometry outcomes, and emits a
canonical analysis document to stdout.  Official result files are never
created, changed, or removed by this module.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import re
import stat
import statistics
import struct
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence


REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.comparison import trace_compare


STUDY_ID = "dancers-orca-v1"
INPUT_SCHEMA = "dancers-orca-input-manifest-v1"
SCHEDULE_SCHEMA = "dancers-orca-schedule-v1"
CAPSULE_SCHEMA = "dancers-orca-build-capsule-v1"
SESSION_SCHEMA = "dancers-orca-session-v1"
ATTEMPT_SCHEMA = "dancers-orca-attempt-v1"
PLAN_SCHEMA = "dancers-orca-execution-plan-v1"
ANALYSIS_SCHEMA = "dancers-orca-analysis-v1"

FROZEN_INPUT_MANIFEST_SHA256 = (
    "3fc8a7572ed6c89eecfd8cdc1f0db2c9b552e63f60117e83bacf0ad24cd153f7"
)
FROZEN_SCHEDULE_SHA256 = (
    "244d268f37a976bf7af8f11ce0bb02430bd72c32372a4bb040e2b586181328c4"
)
FROZEN_BUILD_CAPSULE_SHA256 = (
    "a0fa89094f55df970bef6c63a33b4d11791972a0c303b923fa3180769616a428"
)
FROZEN_ORCA_BINARY_SHA256 = (
    "155fc6729a85f8595be4cddecd0cb17f4e58c7a0688ccc789fcabbb3c4f59a9e"
)
FROZEN_COMMON_PATCH_SHA256 = (
    "2ab81c9a9621c89de5854bbd8496cb2124845733140a913855ca61677185a95e"
)
FROZEN_PIXI_LOCK_SHA256 = (
    "636ecf1cf31431ffc19f453fef39299c3243718477e321fcea8814225bfe68be"
)
FROZEN_DANCERS_BUILD_MANIFEST_SHA256 = (
    "3c9c4305dae5e5154963ec76bb52e0bb826d6fb5b44a89503235057ad449b3cd"
)
FROZEN_LIBOMP_SHA256 = (
    "40618282b8a203af8759f46ef431cb58490d3ab9709edc421d0dc2b9059c98aa"
)
EXPECTED_LIBOMP = {"bytes": 825_088, "sha256": FROZEN_LIBOMP_SHA256}
ALGORITHM_REVISIONS = {
    "reference": "986f47deec5b079663c7d7f6120bfc20f0467901",
    "optimized": "99364a41f1535a4bb0eddbe5a10030127ea77086",
}
SOURCE_ENDPOINTS = {
    "optimized": {
        "algorithm_revision": ALGORITHM_REVISIONS["optimized"],
        "applied_head": "26c8c6f9b262a2114ac28203419cb03caf8b0d88",
        "source_archive_sha256": "c33937d249dd8ba1d69621aeb146bd1236e2c8c3743cc143f33a925c62bbee2b",
        "tree": "b6f713f47ea3aa57c824cf2c481f3647ac7c9408",
    },
    "reference": {
        "algorithm_revision": ALGORITHM_REVISIONS["reference"],
        "applied_head": "de0f60d1ad4b944c2b3264b8e3ccd348708b8b8a",
        "source_archive_sha256": "e657fe2064f404762c016ffedc882cfc395ab58f4d8b9bc9a1f0bb11de067ced",
        "tree": "cb93386e28f3824ea7ffc17605b441b92f13f87d",
    },
}
ORCA_SOURCE = {
    "bound_input_manifest_sha256": FROZEN_INPUT_MANIFEST_SHA256,
    "implementation": "snape/RVO2",
    "rvo2_commit": "1c25b27258d191e26c2df83ff6e31abc8efbdaed",
    "rvo2_tree": "754114c13a345b1d228871be7e85683e31b3af5d",
    "wrapper": "dancers-orca-wrapper",
}
EXPECTED_BINARIES = {
    "orca": {
        "bytes": 297_240,
        "sha256": FROZEN_ORCA_BINARY_SHA256,
    },
    "optimized_fidelity": {
        "bytes": 2_403_032,
        "sha256": "97c5f869a17049d87351728829cbba30ecf120d2b3b8cda27872dfdcf545ef72",
    },
    "optimized_timing": {
        "bytes": 2_182_568,
        "sha256": "4b904a0a61d7b3c46dcb4c824b49429e457b9831c4bc9198601ee99cc75733ee",
    },
    "reference_fidelity": {
        "bytes": 2_419_320,
        "sha256": "26afb1f7e6dd915381333fa47b9f0d8bd7ac2a1b97d4446e9e5788e64fb85a1e",
    },
    "reference_timing": {
        "bytes": 2_182_408,
        "sha256": "03fd120158382014201d4b3f19db536c65eab7d88a5d5edd1ecd5c13312e5472",
    },
}
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

EXPECTED_PROCESSES = 363
EXPECTED_SCENES = 23
OPEN_MISSIONS = tuple(range(31, 51))
CIRCLE_COUNTS = (8, 20, 50)
COMMON_TIMESTEP_SECONDS = 0.1
GOAL_TOLERANCE_M = 1.0
DANCERS_INITIALIZATION_ABSOLUTE_TOLERANCE = 5e-10
BOOTSTRAP_DRAWS = 100_000
BOOTSTRAP_DOMAIN = b"dancers-orca-open-bootstrap-v1\0"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TIMESTEPS_RE = re.compile(rb"(?m)^timesteps:\s*(\d+)\b")
FINAL_COUNT_RE = re.compile(rb"Final robot count:\s*\[(\d+)\]")

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
    "paper_metrics",
    "final_tick",
    "final_robot_count",
    "maximum_robot_count",
    "run_valid",
)

BEHAVIOR_DIFFERENCE_METRICS = (
    "all_agents_completion",
    "agent_completion_fraction",
    "all_agents_makespan_s",
    "mean_restricted_agent_completion_time_s",
    "mean_path_length_m",
    "mean_path_excess_m",
    "minimum_pair_clearance_m",
    "pair_contact_episodes",
    "pair_contact_exposure_pair_seconds",
    "mean_velocity_total_variation_mps",
    "mean_acceleration_rms_mps2",
    "mean_jerk_rms_mps3",
)
RESOURCE_RATIO_METRICS = (
    "dancers_wall_speedup",
    "dancers_cpu_speedup",
    "dancers_rss_optimized_over_reference",
)


def analysis_contract() -> dict[str, Any]:
    """Return the pure, machine-readable prospective analysis contract."""

    return {
        "analysis_schema_version": ANALYSIS_SCHEMA,
        "behavior": {
            "canonical_dancers_trace": (
                "reference positions trace only after both reference/optimized "
                "fidelity pairs and all four summary projections agree"
            ),
            "canonical_orca_trace": (
                "repetition 1 only after both positions attempts complete, raw "
                "trace bytes agree, and summaries agree after removing only "
                "input.attempt_sequence and input.repetition"
            ),
            "fixed_ceiling_completion_classes": {
                "all_completed": "all_agents_completed_by_fixed_ceiling",
                "ordinary_noncompletion": "noncompletion_by_fixed_ceiling",
                "registered_failure": "not_observed_due_to_registered_failure",
            },
            "metric_ids": list(BEHAVIOR_DIFFERENCE_METRICS),
            "open_difference_direction": "canonical-DANCeRS-minus-main-ORCA",
            "open_mission_order": list(OPEN_MISSIONS),
            "sensitivity_difference_direction": "ORCA-0.05s-minus-ORCA-main",
        },
        "bootstrap": {
            "draws": BOOTSTRAP_DRAWS,
            "index_rule": (
                "uint64be(SHA256(ASCII('dancers-orca-open-bootstrap-v1') || "
                "0x00 || UTF8(metric_name) || 0x00 || uint64be(draw) || "
                "uint32be(position))[0:8]) mod 20"
            ),
            "mission_count": 20,
            "mean_rule": "finite fsum(value/20 for each mission value)",
            "quantile_rule": (
                "sort n draw statistics; h=(n-1)*p; lo=floor(h); hi=ceil(h); "
                "finite convex sum (1-(h-lo))*x[lo]+(h-lo)*x[hi]"
            ),
            "resource_transform": "bootstrap mean log ratio, then exponentiate",
            "scope": "fixed-input/fixed-open-world resampling sensitivity",
        },
        "common_grid": {
            "dancers_projection": "pre_update@0 then post_update@1..H",
            "dancers_velocity_indices_zero_based": [3, 4],
            "goal_disc_closed_radius_m": GOAL_TOLERANCE_M,
            "goal_and_straight_baseline": "scenario JSON binary64 centres",
            "nominal_timestep_seconds": COMMON_TIMESTEP_SECONDS,
            "orca_projection": "initial state then recorded post-doStep common states",
            "orca_recorded_goal_check": "exact scenario binary32 cast",
            "ticks": "integer 0..H; H is schedule-variable",
        },
        "failure_policy": {
            "artifact_failure": (
                "returncode zero but required output invalid; fixed_work_verified=false"
            ),
            "designated_positions_process_failure": (
                "attempt completion is zero and continuous metrics unavailable"
            ),
            "gate_failure": (
                "scene bundle and every paired metric including completion unavailable"
            ),
            "open_missingness": (
                "any unavailable registered scene makes that 20-scene metric "
                "non-estimable with no complete-case aggregate; gate/process failures "
                "and ordinary makespan noncompletion are diagnosed separately"
            ),
            "numeric_range": (
                "a derived difference, ratio, or throughput that is non-finite or "
                "nonpositive only because binary64 division underflowed is unavailable "
                "without clipping; open aggregates use not_estimable_due_to_numeric_range "
                "or the combined registered-failure-and-numeric-range label"
            ),
            "timing_or_warmup_behavior_effect": "none",
        },
        "geometry": {
            "contact_boundary": "continuous swept centre clearance <= 0",
            "contact_intervals": "closed per-pair intervals merged when touching",
            "exposure": "sum merged durations over unordered pairs (pair-seconds)",
            "goal_first_passage": "earliest closed segment/closed goal-disc intersection",
            "minimum_clearance": "minimum swept centre distance minus summed radii",
            "tangency": "one episode with zero exposure",
        },
        "integrity": {
            "dancers_initialization_residual": {
                "absolute_position_tolerance_m": (
                    DANCERS_INITIALIZATION_ABSOLUTE_TOLERANCE
                ),
                "absolute_velocity_tolerance_mps": (
                    DANCERS_INITIALIZATION_ABSOLUTE_TOLERANCE
                ),
                "metric_treatment": "retain observed tick-0 state without clamping",
                "registered_baseline": "authenticated scenario start centre",
            },
            "dancers_no_map_sha256": EMPTY_SHA256,
            "dancers_summary_projection": list(SUMMARY_PARITY_FIELDS),
            "float_identity": (
                "trajectory position/velocity values must match binary64 (DANCeRS) "
                "or binary32 (ORCA) hex before use; recorded ORCA goal and radius "
                "values must also match their binary32 hex"
            ),
            "input_manifest_sha256": FROZEN_INPUT_MANIFEST_SHA256,
            "orca_binary_sha256": FROZEN_ORCA_BINARY_SHA256,
            "process_inventory": EXPECTED_PROCESSES,
            "schedule_sha256": FROZEN_SCHEDULE_SHA256,
        },
        "resource": {
            "admissibility": {
                "failure_effect": (
                    "all wall/CPU/RSS medians for that method and scene and "
                    "all dependent ratios or ORCA context are unavailable"
                ),
                "per_repetition": {
                    "fixed_work_verified": True,
                    "outcome": "completed",
                    "peak_rss_mib": "> 0",
                    "total_cpu_seconds": "> 0",
                },
                "required_repetitions": 3,
            },
            "metric_ids": list(RESOURCE_RATIO_METRICS),
            "sequence_diagnostics": {
                "process_failure_sequences": (
                    "non-completed or fixed-work-unverified attempts only"
                ),
                "resource_inadmissible_sequences": (
                    "completed fixed-work attempts with nonpositive CPU or RSS only"
                ),
            },
            "orca_context": (
                "absolute within-scene wall/CPU/RSS medians and "
                "robot_count*native_steps/median_total_cpu_seconds only"
            ),
            "derived_numeric_policy": {
                "dancers_ratio": "not_estimable_due_to_numeric_range",
                "no_clipping_or_infinity": True,
                "orca_throughput": (
                    "throughput_not_estimable_due_to_numeric_range; medians retained"
                ),
            },
            "reduction": "three successful fixed-work repetitions then within-scene median",
            "ratios": {
                "dancers_cpu_speedup": "reference median / optimized median",
                "dancers_rss_optimized_over_reference": (
                    "optimized median / reference median"
                ),
                "dancers_wall_speedup": "reference median / optimized median",
            },
        },
        "result_contract": {
            "attempt_schema_version": ATTEMPT_SCHEMA,
            "artifact_filename_keys": [
                "provenance.json",
                "stderr.txt",
                "stdout.txt",
                "summary.json",
                "trace.ndjson",
            ],
            "artifact_identity_exact_keys": ["bytes", "mode", "path", "sha256"],
            "command_exact_keys": ["argv", "cwd", "environment", "shell"],
            "measurement_exact_keys": [
                "peak_rss_mib",
                "peak_rss_raw",
                "peak_rss_raw_unit",
                "returncode",
                "signal",
                "system_cpu_seconds",
                "timed_out",
                "total_cpu_seconds",
                "user_cpu_seconds",
                "wall_seconds",
            ],
            "plan_schema_version": PLAN_SCHEMA,
            "record_base_exact_keys": sorted(
                {
                    "artifacts",
                    "binary",
                    "command",
                    "fixed_work_verified",
                    "measurement",
                    "outcome",
                    "runtime_binding",
                    "schedule",
                    "schema_version",
                    "started_unix_ns",
                    "study_id",
                }
            ),
            "record_artifact_failure_extra_key": "validation_error",
            "runtime_binding_exact_keys": [
                "binaries",
                "build_manifest",
                "libomp",
                "source_endpoints",
                "source_roots",
            ],
            "session_exact_keys": sorted(
                {
                    "admission_manifest_sha256",
                    "attempt_records",
                    "authorization_manifest_sha256",
                    "completed_processes",
                    "execution_complete",
                    "expected_processes",
                    "input_manifest",
                    "outcome_counts",
                    "plan",
                    "records",
                    "runtime_binding",
                    "schedule",
                    "schema_version",
                    "study_id",
                }
            ),
            "session_schema_version": SESSION_SCHEMA,
            "terminal_outcomes": [
                "artifact_failure",
                "completed",
                "nonzero_exit",
                "timeout",
            ],
        },
        "schema_version": "dancers-orca-analysis-contract-v1",
        "study_id": STUDY_ID,
    }


class AnalysisError(RuntimeError):
    """The frozen contract or sealed result is malformed."""


class _DuplicateKey(ValueError):
    pass


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value!r}")


def canonical_json_bytes(value: object, *, pretty: bool = False) -> bytes:
    options: dict[str, Any] = {
        "allow_nan": False,
        "ensure_ascii": True,
        "sort_keys": True,
    }
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    return (json.dumps(value, **options) + "\n").encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _json_exactly_equal(left: object, right: object) -> bool:
    """Compare JSON values without Python's int/float or bool/int coercions."""

    return canonical_json_bytes(left) == canonical_json_bytes(right)


def _require_finite_json(value: Any, label: str) -> Any:
    try:
        canonical_json_bytes(value)
    except (OverflowError, RecursionError, TypeError, ValueError) as error:
        raise AnalysisError(f"{label} is not finite canonical JSON") from error
    return value


def _parse_json(raw: bytes, label: str) -> Any:
    try:
        return json.loads(
            raw,
            object_pairs_hook=_no_duplicate_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise AnalysisError(f"{label} is not strict JSON: {error}") from error


def _regular_bytes(
    path: pathlib.Path,
    *,
    label: str,
    maximum_bytes: int = 1024 * 1024 * 1024,
) -> bytes:
    """Read a bounded regular file while rejecting every symlink component."""

    candidate = pathlib.Path(os.path.abspath(os.fspath(path)))
    cursor = candidate
    while True:
        try:
            metadata = cursor.lstat()
        except OSError as error:
            raise AnalysisError(f"cannot stat {label}: {error}") from error
        if stat.S_ISLNK(metadata.st_mode):
            raise AnalysisError(f"{label} has a symlink component")
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    metadata = candidate.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise AnalysisError(f"{label} must be a regular file")
    if metadata.st_size < 0 or metadata.st_size > maximum_bytes:
        raise AnalysisError(f"{label} byte count exceeds its bound")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(candidate, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino, opened.st_size)
            != (metadata.st_dev, metadata.st_ino, metadata.st_size)
        ):
            raise AnalysisError(f"{label} identity changed before reading")
        chunks: list[bytes] = []
        remaining = maximum_bytes + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        ended = os.fstat(descriptor)
        opened_identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        )
        ended_identity = (
            ended.st_dev,
            ended.st_ino,
            ended.st_size,
            ended.st_mtime_ns,
        )
        if opened_identity != ended_identity or len(raw) != opened.st_size:
            raise AnalysisError(f"{label} changed while being read")
        return raw
    except OSError as error:
        raise AnalysisError(f"cannot read {label}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _canonical_json_file(
    path: pathlib.Path, *, label: str, pretty: bool, maximum_bytes: int = 64 * 1024 * 1024
) -> tuple[Any, bytes]:
    raw = _regular_bytes(path, label=label, maximum_bytes=maximum_bytes)
    value = _parse_json(raw, label)
    if raw != canonical_json_bytes(value, pretty=pretty):
        raise AnalysisError(f"{label} is not canonical JSON")
    return value, raw


def _safe_relative(root: pathlib.Path, relative: object, label: str) -> pathlib.Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise AnalysisError(f"{label} path is invalid")
    pure = pathlib.PurePosixPath(relative)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise AnalysisError(f"{label} path escapes its root")
    candidate = root.joinpath(*pure.parts)
    root_absolute = pathlib.Path(os.path.abspath(os.fspath(root)))
    candidate_absolute = pathlib.Path(os.path.abspath(os.fspath(candidate)))
    try:
        candidate_absolute.relative_to(root_absolute)
    except ValueError as error:
        raise AnalysisError(f"{label} path escapes its root") from error
    return candidate_absolute


def _require_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise AnalysisError(f"{label} must be a lowercase SHA-256")
    return value


def _identity_bytes(root: pathlib.Path, identity: object, label: str) -> tuple[pathlib.Path, bytes]:
    if not isinstance(identity, Mapping):
        raise AnalysisError(f"{label} identity must be an object")
    path = _safe_relative(root, identity.get("path"), label)
    raw = _regular_bytes(path, label=label)
    size = identity.get("bytes")
    digest = _require_sha(identity.get("sha256"), f"{label}.sha256")
    if type(size) is not int or size < 0 or len(raw) != size or sha256_bytes(raw) != digest:
        raise AnalysisError(f"{label} identity differs")
    mode = identity.get("mode")
    if mode is not None:
        if not isinstance(mode, str) or mode != f"{stat.S_IMODE(path.stat().st_mode):04o}":
            raise AnalysisError(f"{label} mode differs")
    return path, raw


def _identity_path(root: pathlib.Path, identity: object, label: str) -> pathlib.Path:
    """Authenticate an identity by streaming it, without retaining its payload."""

    if not isinstance(identity, Mapping):
        raise AnalysisError(f"{label} identity must be an object")
    path = _safe_relative(root, identity.get("path"), label)
    candidate = pathlib.Path(os.path.abspath(os.fspath(path)))
    cursor = candidate
    while True:
        metadata = cursor.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise AnalysisError(f"{label} has a symlink component")
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    metadata = candidate.lstat()
    expected_size = identity.get("bytes")
    expected_digest = _require_sha(identity.get("sha256"), f"{label}.sha256")
    if (
        not stat.S_ISREG(metadata.st_mode)
        or type(expected_size) is not int
        or expected_size < 0
        or metadata.st_size != expected_size
    ):
        raise AnalysisError(f"{label} identity differs")
    mode = identity.get("mode")
    if mode is not None and mode != f"{stat.S_IMODE(metadata.st_mode):04o}":
        raise AnalysisError(f"{label} mode differs")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(candidate, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino, opened.st_size)
            != (metadata.st_dev, metadata.st_ino, metadata.st_size)
        ):
            raise AnalysisError(f"{label} identity changed before hashing")
        digest = hashlib.sha256()
        observed = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            observed += len(chunk)
            digest.update(chunk)
        ended = os.fstat(descriptor)
        if (
            observed != expected_size
            or digest.hexdigest() != expected_digest
            or (
                opened.st_dev,
                opened.st_ino,
                opened.st_size,
                opened.st_mtime_ns,
            )
            != (ended.st_dev, ended.st_ino, ended.st_size, ended.st_mtime_ns)
        ):
            raise AnalysisError(f"{label} identity changed or differs")
    except OSError as error:
        raise AnalysisError(f"cannot authenticate {label}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return candidate


def _finite(value: object, label: str) -> float:
    if type(value) not in (int, float):
        raise AnalysisError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise AnalysisError(f"{label} must be a finite number") from error
    if not math.isfinite(result):
        raise AnalysisError(f"{label} must be a finite number")
    return result


def _positive(value: object, label: str) -> float:
    result = _finite(value, label)
    if result <= 0.0:
        raise AnalysisError(f"{label} must be positive")
    return result


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise AnalysisError(f"{label} must be an integer >= {minimum}")
    return value


def _point(value: object, label: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise AnalysisError(f"{label} must contain x and y")
    return (_finite(value[0], f"{label}.x"), _finite(value[1], f"{label}.y"))


def _float_hex(value: float, bits: int) -> str:
    if bits == 32:
        return struct.pack(">f", value).hex()
    if bits == 64:
        return struct.pack(">d", value).hex()
    raise AssertionError("unsupported floating-point width")


def validate_float_hex(value: object, encoded: object, bits: int, label: str) -> float:
    number = _finite(value, label)
    width = 8 if bits == 32 else 16 if bits == 64 else 0
    if not isinstance(encoded, str) or len(encoded) != width or encoded != encoded.lower():
        raise AnalysisError(f"{label} hexadecimal identity is invalid")
    try:
        bytes.fromhex(encoded)
    except ValueError as error:
        raise AnalysisError(f"{label} hexadecimal identity is invalid") from error
    try:
        actual = _float_hex(number, bits)
    except OverflowError as error:
        raise AnalysisError(f"{label} is outside binary{bits}") from error
    if actual != encoded:
        raise AnalysisError(f"{label} value and binary{bits} identity differ")
    # JSON's shortest binary32 spelling (for example ``0.1``) parses as a
    # binary64 approximation.  Metrics must use the authenticated native bits,
    # not that parser approximation.
    return struct.unpack(">f" if bits == 32 else ">d", bytes.fromhex(encoded))[0]


def _float_record(record: object, bits: int, label: str) -> float:
    if not isinstance(record, Mapping) or set(record) != {"hex", "value"}:
        raise AnalysisError(f"{label} floating-point record schema differs")
    return validate_float_hex(record["value"], record["hex"], bits, label)


def _point_record(record: object, bits: int, label: str) -> tuple[float, float]:
    if not isinstance(record, Mapping) or set(record) != {"hex", "value"}:
        raise AnalysisError(f"{label} point record schema differs")
    values, encoded = record["value"], record["hex"]
    if not isinstance(values, list) or not isinstance(encoded, list) or len(values) != 2 or len(encoded) != 2:
        raise AnalysisError(f"{label} point record dimensions differ")
    return (
        validate_float_hex(values[0], encoded[0], bits, f"{label}.x"),
        validate_float_hex(values[1], encoded[1], bits, f"{label}.y"),
    )


@dataclass(frozen=True)
class AgentState:
    rid: int
    position: tuple[float, float]
    velocity: tuple[float, float]
    radius_m: float
    goal: tuple[float, float]


@dataclass(frozen=True)
class CommonFrame:
    tick: int
    agents: tuple[AgentState, ...]


@dataclass(frozen=True)
class CommonTrajectory:
    scene_id: str
    scene_set: str
    method: str
    cadence: str
    timestep_seconds: float
    registered_starts: tuple[tuple[float, float], ...]
    registered_goals: tuple[tuple[float, float], ...]
    frames: tuple[CommonFrame, ...]

    @property
    def common_steps(self) -> int:
        return len(self.frames) - 1

    @property
    def ceiling_seconds(self) -> float:
        return self.common_steps * self.timestep_seconds


@dataclass(frozen=True)
class SessionData:
    input_manifest: dict[str, Any]
    schedule: list[dict[str, Any]]
    plan: dict[str, Any]
    records: list[dict[str, Any]]
    session: dict[str, Any]


def _require_exact_keys(value: object, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise AnalysisError(f"{label} keys differ")
    return value


def _verify_input_tree(input_root: pathlib.Path, manifest: Mapping[str, Any]) -> None:
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != 48:
        raise AnalysisError("input manifest file inventory differs")
    registered: set[str] = set()
    for index, identity in enumerate(files):
        if not isinstance(identity, Mapping):
            raise AnalysisError(f"input file identity {index} is not an object")
        relative = identity.get("path")
        if not isinstance(relative, str) or relative in registered:
            raise AnalysisError("input file paths are invalid or duplicated")
        registered.add(relative)
        _identity_bytes(input_root, identity, f"input file {relative}")
    actual: set[str] = set()
    expected_directories = {""}
    for relative in registered:
        parent = pathlib.PurePosixPath(relative).parent
        while parent != pathlib.PurePosixPath("."):
            expected_directories.add(parent.as_posix())
            parent = parent.parent
    actual_directories = {""}
    for directory, directories, filenames in os.walk(input_root, followlinks=False):
        base = pathlib.Path(directory)
        for name in list(directories):
            path = base / name
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                raise AnalysisError("input tree contains a non-real directory")
            actual_directories.add(path.relative_to(input_root).as_posix())
        for name in filenames:
            path = base / name
            if path.is_symlink() or not path.is_file():
                raise AnalysisError("input tree contains a non-regular file")
            actual.add(path.relative_to(input_root).as_posix())
    expected = registered | {"manifest.json", "manifest.sha256"}
    if actual != expected or actual_directories != expected_directories:
        raise AnalysisError("input tree has missing or unregistered entries")


def _schedule_inventory(attempts: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    scene_ids: set[str] = set()
    for sequence, row in enumerate(attempts):
        if not isinstance(row, Mapping) or row.get("sequence") != sequence:
            raise AnalysisError(f"schedule sequence {sequence} differs")
        method = row.get("method")
        phase = row.get("phase")
        run_class = row.get("run_class")
        cadence = row.get("cadence")
        if method not in ("reference", "optimized", "orca"):
            raise AnalysisError(f"schedule method differs at sequence {sequence}")
        if phase not in ("warmup", "measured", "sensitivity"):
            raise AnalysisError(f"schedule phase differs at sequence {sequence}")
        key = "/".join((str(phase), str(method), str(run_class), str(cadence)))
        counts[key] = counts.get(key, 0) + 1
        scene_id = row.get("scene_id")
        if not isinstance(scene_id, str):
            raise AnalysisError(f"schedule scene ID differs at sequence {sequence}")
        scene_ids.add(scene_id)
        steps = _integer(row.get("steps"), f"schedule[{sequence}].steps", minimum=1)
        stride = _integer(
            row.get("native_sample_stride"),
            f"schedule[{sequence}].native_sample_stride",
            minimum=1,
        )
        samples = _integer(
            row.get("common_sample_count"),
            f"schedule[{sequence}].common_sample_count",
            minimum=2,
        )
        if steps % stride or samples != steps // stride + 1:
            raise AnalysisError(f"schedule common-grid dimensions differ at sequence {sequence}")
        if _finite(row.get("timeout_seconds"), f"schedule[{sequence}].timeout") != 300.0:
            raise AnalysisError(f"schedule timeout differs at sequence {sequence}")
    expected = {
        "measured/reference/digest_fidelity/main": 23,
        "measured/optimized/digest_fidelity/main": 23,
        "measured/reference/positions_fidelity/main": 23,
        "measured/optimized/positions_fidelity/main": 23,
        "measured/orca/positions_fidelity/main": 46,
        "measured/reference/timing/main": 69,
        "measured/optimized/timing/main": 69,
        "measured/orca/timing/main": 69,
        "warmup/reference/timing/main": 4,
        "warmup/optimized/timing/main": 4,
        "warmup/orca/timing/main": 4,
        "sensitivity/orca/cadence_sensitivity/orca-0.05s-sensitivity": 6,
    }
    if counts != expected or len(scene_ids) != EXPECTED_SCENES:
        raise AnalysisError("schedule class or scene inventory differs")
    return counts


def check_frozen_contract(
    input_root: pathlib.Path,
    build_capsule_root: pathlib.Path,
    *,
    expected_input_manifest_sha256: str,
    expected_schedule_sha256: str,
    expected_build_capsule_sha256: str,
) -> dict[str, Any]:
    """Authenticate the frozen input and nonexecuting ORCA build capsule."""

    for candidate, label in (
        (input_root, "input root"),
        (build_capsule_root, "build capsule root"),
    ):
        if (
            not candidate.is_absolute()
            or candidate != candidate.resolve()
            or candidate.is_symlink()
            or not candidate.is_dir()
        ):
            raise AnalysisError(f"{label} must be an absolute real directory")
    expected_input = _require_sha(
        expected_input_manifest_sha256, "expected input manifest SHA-256"
    )
    expected_schedule = _require_sha(
        expected_schedule_sha256, "expected schedule SHA-256"
    )
    expected_capsule = _require_sha(
        expected_build_capsule_sha256, "expected build capsule SHA-256"
    )
    if (
        expected_input != FROZEN_INPUT_MANIFEST_SHA256
        or expected_schedule != FROZEN_SCHEDULE_SHA256
        or expected_capsule != FROZEN_BUILD_CAPSULE_SHA256
    ):
        raise AnalysisError("external trust roots differ from the frozen publication roots")
    manifest, manifest_raw = _canonical_json_file(
        input_root / "manifest.json", label="input manifest", pretty=True
    )
    if sha256_bytes(manifest_raw) != expected_input:
        raise AnalysisError("input manifest differs from the external digest")
    sidecar = _regular_bytes(input_root / "manifest.sha256", label="input sidecar")
    if sidecar != f"{expected_input}  manifest.json\n".encode("ascii"):
        raise AnalysisError("input manifest sidecar differs")
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != INPUT_SCHEMA
        or manifest.get("study_id") != STUDY_ID
    ):
        raise AnalysisError("input manifest schema or study differs")
    schedule_identity = manifest.get("schedule")
    schedule_path, schedule_raw = _identity_bytes(
        input_root, schedule_identity, "input schedule"
    )
    if sha256_bytes(schedule_raw) != expected_schedule:
        raise AnalysisError("schedule differs from the external digest")
    schedule = _parse_json(schedule_raw, "input schedule")
    if schedule_raw != canonical_json_bytes(schedule, pretty=True):
        raise AnalysisError("input schedule is not generator-canonical JSON")
    if (
        not isinstance(schedule, dict)
        or schedule.get("schema_version") != SCHEDULE_SCHEMA
        or schedule.get("study_id") != STUDY_ID
        or not isinstance(schedule.get("attempts"), list)
        or len(schedule["attempts"]) != EXPECTED_PROCESSES
    ):
        raise AnalysisError("schedule schema, study, or size differs")
    counts = _schedule_inventory(schedule["attempts"])
    _verify_input_tree(input_root, manifest)

    capsule, capsule_raw = _canonical_json_file(
        build_capsule_root / "manifest.json",
        label="ORCA build capsule manifest",
        pretty=True,
    )
    if sha256_bytes(capsule_raw) != expected_capsule:
        raise AnalysisError("ORCA build capsule differs from the external digest")
    capsule_sidecar = _regular_bytes(
        build_capsule_root / "manifest.sha256", label="ORCA build capsule sidecar"
    )
    if capsule_sidecar != f"{expected_capsule}  manifest.json\n".encode("ascii"):
        raise AnalysisError("ORCA build capsule sidecar differs")
    if (
        not isinstance(capsule, dict)
        or capsule.get("schema_version") != CAPSULE_SCHEMA
        or capsule.get("input", {}).get("manifest_sha256") != expected_input
        or capsule.get("input", {}).get("schedule_sha256") != expected_schedule
        or capsule.get("byte_identical_final_executables") is not True
    ):
        raise AnalysisError("ORCA build capsule bindings differ")
    builds = capsule.get("builds")
    if (
        not isinstance(builds, list)
        or len(builds) != 2
        or any(
            item.get("audit", {}).get("binary", {}).get("sha256")
            != FROZEN_ORCA_BINARY_SHA256
            for item in builds
            if isinstance(item, Mapping)
        )
        or any(not isinstance(item, Mapping) for item in builds)
    ):
        raise AnalysisError("ORCA executable reproduction identities differ")
    return {
        "build_capsule_manifest_sha256": expected_capsule,
        "execution_authorized_by_build_capsule": capsule.get("classification", {}).get(
            "execution_authorized"
        ),
        "input_manifest_sha256": expected_input,
        "orca_binary_sha256": FROZEN_ORCA_BINARY_SHA256,
        "processes": EXPECTED_PROCESSES,
        "schedule_counts": counts,
        "schedule_path": schedule_path.relative_to(input_root).as_posix(),
        "schedule_sha256": expected_schedule,
        "scenes": EXPECTED_SCENES,
        "study_id": STUDY_ID,
    }


def _parse_canonical_ndjson(raw: bytes, label: str) -> list[dict[str, Any]]:
    if not raw or not raw.endswith(b"\n"):
        raise AnalysisError(f"{label} must be nonempty and LF-terminated")
    result: list[dict[str, Any]] = []
    for index, line in enumerate(raw.splitlines(keepends=True)):
        value = _parse_json(line, f"{label} line {index + 1}")
        if not isinstance(value, dict) or line != canonical_json_bytes(value):
            raise AnalysisError(f"{label} line {index + 1} is not canonical JSON")
        result.append(value)
    return result


def _dancers_trace_limits(row: Mapping[str, Any]) -> trace_compare.TraceLimits:
    """Mirror the runner's exact DANCeRS trace-admission limits."""

    return trace_compare.TraceLimits(
        max_file_bytes=1024 * 1024 * 1024,
        max_line_bytes=64 * 1024 * 1024,
        max_frames=2 * _integer(row.get("steps"), "DANCeRS trace steps", minimum=1)
        + 1,
        max_robots_per_frame=_integer(
            row.get("robot_count"), "DANCeRS trace robot count", minimum=1
        ),
        max_json_depth=128,
    )


def _validate_binary_identity(identity: object, label: str) -> Mapping[str, Any]:
    if not isinstance(identity, Mapping) or set(identity) != {
        "bytes",
        "mode",
        "path",
        "sha256",
    }:
        raise AnalysisError(f"{label} binary identity is not an object")
    if type(identity.get("bytes")) is not int or identity["bytes"] <= 0:
        raise AnalysisError(f"{label} binary byte count differs")
    _require_sha(identity.get("sha256"), f"{label} binary SHA-256")
    if not isinstance(identity.get("path"), str) or not identity["path"]:
        raise AnalysisError(f"{label} binary path differs")
    if identity.get("mode") != "0755":
        raise AnalysisError(f"{label} binary mode differs")
    return identity


def _validate_result_identity(identity: object, label: str) -> Mapping[str, Any]:
    if not isinstance(identity, Mapping) or set(identity) != {
        "bytes",
        "mode",
        "path",
        "sha256",
    }:
        raise AnalysisError(f"{label} identity keys differ")
    if not isinstance(identity.get("mode"), str) or re.fullmatch(
        r"[0-7]{4}", identity["mode"]
    ) is None:
        raise AnalysisError(f"{label} mode differs")
    return identity


def _validate_runtime_binding(value: object) -> Mapping[str, Any]:
    runtime = _require_exact_keys(
        value,
        {"binaries", "build_manifest", "libomp", "source_endpoints", "source_roots"},
        "runtime binding",
    )
    binaries = _require_exact_keys(
        runtime["binaries"], set(EXPECTED_BINARIES), "runtime binaries"
    )
    for role, expected in EXPECTED_BINARIES.items():
        identity = _validate_binary_identity(binaries[role], f"runtime {role}")
        if identity["bytes"] != expected["bytes"] or identity["sha256"] != expected["sha256"]:
            raise AnalysisError(f"runtime {role} identity differs from the frozen binary")
        path = pathlib.Path(identity["path"])
        if not path.is_absolute() or pathlib.PurePath(path).parts.count(".."):
            raise AnalysisError(f"runtime {role} path is not absolute")
    roots = _require_exact_keys(
        runtime["source_roots"], {"optimized", "reference"}, "runtime source roots"
    )
    for method, raw in roots.items():
        if not isinstance(raw, str) or not pathlib.Path(raw).is_absolute():
            raise AnalysisError(f"runtime {method} source root is not absolute")
    build_manifest = _validate_result_identity(
        runtime["build_manifest"], "runtime DANCeRS build manifest"
    )
    if build_manifest != {
        "bytes": 4_995,
        "mode": "0644",
        "path": "results/publication/dancers-multimap-v1-host-1/build_manifest.json",
        "sha256": FROZEN_DANCERS_BUILD_MANIFEST_SHA256,
    }:
        raise AnalysisError("runtime DANCeRS build manifest identity differs")
    libomp = _validate_result_identity(runtime["libomp"], "runtime libomp")
    expected_libomp_path = str(
        (REPO / ".pixi/envs/default/lib/libomp.dylib").resolve()
    )
    if libomp != {
        "bytes": EXPECTED_LIBOMP["bytes"],
        "mode": "0755",
        "path": expected_libomp_path,
        "sha256": EXPECTED_LIBOMP["sha256"],
    }:
        raise AnalysisError("runtime libomp identity differs")
    if runtime["source_endpoints"] != SOURCE_ENDPOINTS:
        raise AnalysisError("runtime source endpoint identities differ")
    return runtime


def _validate_measurement(measurement: object, outcome: str, label: str) -> None:
    required = {
        "returncode",
        "timed_out",
        "wall_seconds",
        "user_cpu_seconds",
        "system_cpu_seconds",
        "total_cpu_seconds",
        "peak_rss_raw",
        "peak_rss_raw_unit",
        "peak_rss_mib",
        "signal",
    }
    if not isinstance(measurement, Mapping) or set(measurement) != required:
        raise AnalysisError(f"{label} measurement schema differs")
    returncode = measurement["returncode"]
    timed_out = measurement["timed_out"]
    if type(returncode) is not int or type(timed_out) is not bool:
        raise AnalysisError(f"{label} process status differs")
    if outcome == "completed" and (returncode != 0 or timed_out):
        raise AnalysisError(f"{label} completed status is inconsistent")
    if outcome == "timeout" and not timed_out:
        raise AnalysisError(f"{label} timeout status is inconsistent")
    if outcome == "nonzero_exit" and (timed_out or returncode == 0):
        raise AnalysisError(f"{label} nonzero status is inconsistent")
    if outcome == "artifact_failure" and (timed_out or returncode != 0):
        raise AnalysisError(f"{label} artifact-failure status is inconsistent")
    signal_value = measurement["signal"]
    if signal_value is not None and (type(signal_value) is not int or signal_value < 1):
        raise AnalysisError(f"{label} signal differs")
    if returncode < 0:
        if signal_value != -returncode:
            raise AnalysisError(f"{label} signal/returncode identity differs")
    elif signal_value is not None:
        raise AnalysisError(f"{label} non-signalled process records a signal")
    wall = _positive(measurement["wall_seconds"], f"{label}.wall_seconds")
    user = _finite(measurement["user_cpu_seconds"], f"{label}.user_cpu_seconds")
    system = _finite(measurement["system_cpu_seconds"], f"{label}.system_cpu_seconds")
    total = _finite(measurement["total_cpu_seconds"], f"{label}.total_cpu_seconds")
    if user < 0.0 or system < 0.0 or total < 0.0 or total != user + system:
        raise AnalysisError(f"{label} CPU identity differs")
    raw_rss = _integer(measurement["peak_rss_raw"], f"{label}.peak_rss_raw")
    unit = measurement["peak_rss_raw_unit"]
    try:
        expected_rss = (
            raw_rss / (1024.0 * 1024.0)
            if unit == "bytes"
            else raw_rss / 1024.0
            if unit == "kibibytes"
            else None
        )
    except (OverflowError, TypeError) as error:
        raise AnalysisError(f"{label} RSS conversion overflowed") from error
    rss = _finite(measurement["peak_rss_mib"], f"{label}.peak_rss_mib")
    if expected_rss is None or rss < 0.0 or rss != expected_rss or wall <= 0.0:
        raise AnalysisError(f"{label} RSS identity differs")


def _validate_dancers_stdout(raw: bytes, steps: int, count: int, label: str) -> None:
    ticks = [int(match.group(1)) for match in TIMESTEPS_RE.finditer(raw)]
    counts = [int(match.group(1)) for match in FINAL_COUNT_RE.finditer(raw)]
    if not ticks or ticks[-1] != steps or any(tick > steps for tick in ticks):
        raise AnalysisError(f"{label} fixed-work timestep gate failed")
    if not counts or counts[-1] != count:
        raise AnalysisError(f"{label} final robot count gate failed")


def _validate_attempt_record(
    input_root: pathlib.Path,
    result_root: pathlib.Path,
    record: Mapping[str, Any],
    planned: Mapping[str, Any],
    command_template: Mapping[str, Any],
    runtime_binding: Mapping[str, Any],
) -> None:
    sequence = planned["sequence"]
    label = f"attempt {sequence}"
    required = {
        "schema_version",
        "study_id",
        "schedule",
        "command",
        "binary",
        "measurement",
        "outcome",
        "runtime_binding",
        "artifacts",
        "started_unix_ns",
        "fixed_work_verified",
    }
    outcome = record.get("outcome")
    expected_keys = required | ({"validation_error"} if outcome == "artifact_failure" else set())
    if set(record) != expected_keys:
        raise AnalysisError(f"{label} record keys differ")
    if (
        record["schema_version"] != ATTEMPT_SCHEMA
        or record["study_id"] != STUDY_ID
        or not _json_exactly_equal(record["schedule"], planned)
    ):
        raise AnalysisError(f"{label} schedule or schema binding differs")
    command = _require_exact_keys(
        record["command"], {"argv", "cwd", "environment", "shell"}, f"{label} command"
    )
    if (
        not isinstance(command["argv"], list)
        or not command["argv"]
        or any(not isinstance(item, str) or not item for item in command["argv"])
        or not isinstance(command["cwd"], str)
        or command["shell"] is not False
        or not isinstance(command["environment"], Mapping)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in command["environment"].items()
        )
    ):
        raise AnalysisError(f"{label} command values differ")
    method = planned["method"]
    template = _require_exact_keys(
        command_template,
        {
            "argv",
            "binary_role",
            "command_kind",
            "cwd",
            "environment",
            "expected_artifacts_on_success",
            "expects_summary_on_success",
            "expects_trace_on_success",
            "method",
            "phase",
            "run_class",
            "sequence",
            "steps",
            "timeout_seconds",
        },
        f"{label} plan command",
    )
    if (
        template["sequence"] != sequence
        or template["method"] != method
        or template["phase"] != planned["phase"]
        or template["run_class"] != planned["run_class"]
        or template["steps"] != planned["steps"]
        or template["timeout_seconds"] != planned["timeout_seconds"]
    ):
        raise AnalysisError(f"{label} plan command does not bind its schedule row")
    stage = result_root / "attempts" / f".staging-{sequence:03d}-{method}"
    replacements = {
        "${ATTEMPT_STAGE}": str(stage),
        "${INPUT_ROOT}": str(input_root),
        "${REPO}": str(REPO),
    }
    binary_role = template["binary_role"]
    if binary_role not in runtime_binding["binaries"]:
        raise AnalysisError(f"{label} plan binary role differs")
    replacements[f"${{BINARY:{binary_role}}}"] = runtime_binding["binaries"][
        binary_role
    ]["path"]

    def materialize(value: object) -> object:
        if isinstance(value, str):
            result = value
            for token, replacement in replacements.items():
                result = result.replace(token, replacement)
            if "${" in result:
                raise AnalysisError(f"{label} plan contains an unresolved token")
            return result
        if isinstance(value, list):
            return [materialize(item) for item in value]
        if isinstance(value, Mapping):
            return {key: materialize(item) for key, item in value.items()}
        return value

    expected_command = {
        "argv": materialize(template["argv"]),
        "cwd": materialize(template["cwd"]),
        "environment": materialize(template["environment"]),
        "shell": False,
    }
    if not _json_exactly_equal(command, expected_command):
        raise AnalysisError(f"{label} command differs from the sealed plan")
    binary = _validate_binary_identity(record["binary"], label)
    if not _json_exactly_equal(binary, runtime_binding["binaries"][binary_role]):
        raise AnalysisError(f"{label} binary differs from its exact runtime role")
    if not _json_exactly_equal(record["runtime_binding"], runtime_binding):
        raise AnalysisError(f"{label} runtime binding differs from the session")
    if outcome not in ("artifact_failure", "completed", "timeout", "nonzero_exit"):
        raise AnalysisError(f"{label} outcome differs")
    if outcome == "artifact_failure":
        diagnostic = record.get("validation_error")
        if not isinstance(diagnostic, str) or not diagnostic:
            raise AnalysisError(f"{label} artifact failure lacks its diagnostic")
    _validate_measurement(record["measurement"], outcome, label)
    _integer(record["started_unix_ns"], f"{label}.started_unix_ns", minimum=1)
    artifacts = record["artifacts"]
    if not isinstance(artifacts, Mapping):
        raise AnalysisError(f"{label} artifacts must be an object")
    known_artifacts = {
        "provenance.json",
        "stderr.txt",
        "stdout.txt",
        "summary.json",
        "trace.ndjson",
    }
    if not set(artifacts).issubset(known_artifacts):
        raise AnalysisError(f"{label} artifact name inventory differs")
    required_artifacts = {"stdout.txt", "stderr.txt"}
    if method in ("reference", "optimized"):
        required_artifacts.add("provenance.json")
    if not required_artifacts.issubset(artifacts):
        raise AnalysisError(f"{label} base artifact inventory differs")
    artifact_paths: set[pathlib.Path] = set()
    stdout_path: pathlib.Path | None = None
    for name, identity in artifacts.items():
        _validate_result_identity(identity, f"{label} artifact {name}")
        expected_artifact_path = f"attempts/{sequence:03d}-{method}/{name}"
        if identity.get("path") != expected_artifact_path:
            raise AnalysisError(f"{label} artifact {name} path differs")
        path = _identity_path(result_root, identity, f"{label} artifact {name}")
        if name == "stdout.txt":
            stdout_path = path
        if path in artifact_paths:
            raise AnalysisError(f"{label} artifact paths are duplicated")
        artifact_paths.add(path)
    if outcome == "completed":
        run_class = planned["run_class"]
        needs_trace = run_class in ("positions_fidelity", "cadence_sensitivity")
        if method in ("reference", "optimized") and run_class == "digest_fidelity":
            needs_trace = True
        needs_summary = method == "orca" or method in ("reference", "optimized") and run_class != "timing"
        expected_artifacts = set(required_artifacts)
        if needs_trace:
            expected_artifacts.add("trace.ndjson")
        if needs_summary:
            expected_artifacts.add("summary.json")
        if set(artifacts) != expected_artifacts:
            raise AnalysisError(f"{label} completed artifact inventory differs")
        if needs_trace and "trace.ndjson" not in artifacts:
            raise AnalysisError(f"{label} completed trace is missing")
        if needs_summary and "summary.json" not in artifacts:
            raise AnalysisError(f"{label} completed summary is missing")
        if method in ("reference", "optimized"):
            if stdout_path is None:
                raise AnalysisError(f"{label} stdout artifact is missing")
            _validate_dancers_stdout(
                _regular_bytes(
                    stdout_path,
                    label=f"{label} stdout",
                    maximum_bytes=1024 * 1024 * 1024,
                ),
                planned["steps"],
                planned["robot_count"],
                label,
            )
        if record["fixed_work_verified"] is not True:
            raise AnalysisError(f"{label} completed fixed-work gate is false")
    elif record["fixed_work_verified"] is not False:
        raise AnalysisError(f"{label} failed process claims fixed-work completion")


def _validate_result_tree(
    result_root: pathlib.Path,
    schedule: Sequence[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
) -> None:
    root_names = {entry.name for entry in os.scandir(result_root)}
    expected_root = {
        "attempts",
        "input_manifest.json",
        "plan.json",
        "records.ndjson",
        "schedule.json",
        "session.json",
    }
    if root_names != expected_root:
        raise AnalysisError("sealed result root entry inventory differs")
    for name in expected_root - {"attempts"}:
        metadata = (result_root / name).lstat()
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise AnalysisError(f"sealed root entry {name} is not a regular file")
    attempts_root = result_root / "attempts"
    attempts_metadata = attempts_root.lstat()
    if not stat.S_ISDIR(attempts_metadata.st_mode) or stat.S_ISLNK(
        attempts_metadata.st_mode
    ):
        raise AnalysisError("sealed attempts root is not a real directory")
    expected_attempts = {
        f"{row['sequence']:03d}-{row['method']}" for row in schedule
    }
    if {entry.name for entry in os.scandir(attempts_root)} != expected_attempts:
        raise AnalysisError("sealed attempt directory inventory differs")
    for row, record in zip(schedule, records):
        name = f"{row['sequence']:03d}-{row['method']}"
        directory = attempts_root / name
        metadata = directory.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise AnalysisError(f"attempt directory {name} is not a real directory")
        expected_files = {"record.json"} | set(record["artifacts"])
        entries = list(os.scandir(directory))
        if {entry.name for entry in entries} != expected_files:
            raise AnalysisError(f"attempt directory {name} entry inventory differs")
        for entry in entries:
            item = pathlib.Path(entry.path).lstat()
            if not stat.S_ISREG(item.st_mode) or stat.S_ISLNK(item.st_mode):
                raise AnalysisError(f"attempt entry {name}/{entry.name} is not regular")


def read_session(
    input_root: pathlib.Path,
    result_root: pathlib.Path,
    *,
    expected_input_manifest_sha256: str,
    expected_schedule_sha256: str,
    expected_plan_sha256: str,
    expected_authorization_manifest_sha256: str,
    expected_admission_manifest_sha256: str,
) -> SessionData:
    """Authenticate a create-last sealed session and every attempt record."""

    for candidate, label in ((input_root, "input root"), (result_root, "result root")):
        if (
            not candidate.is_absolute()
            or candidate != candidate.resolve()
            or candidate.is_symlink()
            or not candidate.is_dir()
        ):
            raise AnalysisError(f"{label} must be an absolute real directory")
    expected_input = _require_sha(expected_input_manifest_sha256, "expected input SHA-256")
    expected_schedule = _require_sha(expected_schedule_sha256, "expected schedule SHA-256")
    expected_plan = _require_sha(expected_plan_sha256, "expected plan SHA-256")
    expected_authorization = _require_sha(
        expected_authorization_manifest_sha256,
        "expected authorization manifest SHA-256",
    )
    expected_admission = _require_sha(
        expected_admission_manifest_sha256, "expected admission manifest SHA-256"
    )
    if (
        expected_input != FROZEN_INPUT_MANIFEST_SHA256
        or expected_schedule != FROZEN_SCHEDULE_SHA256
    ):
        raise AnalysisError("external input or schedule trust root is not the frozen root")
    session, _session_raw = _canonical_json_file(
        result_root / "session.json", label="sealed session", pretty=False
    )
    session_keys = {
        "schema_version",
        "study_id",
        "execution_complete",
        "expected_processes",
        "completed_processes",
        "input_manifest",
        "schedule",
        "plan",
        "records",
        "attempt_records",
        "admission_manifest_sha256",
        "authorization_manifest_sha256",
        "outcome_counts",
        "runtime_binding",
    }
    _require_exact_keys(session, session_keys, "sealed session")
    if (
        session["schema_version"] != SESSION_SCHEMA
        or session["study_id"] != STUDY_ID
        or session["execution_complete"] is not True
        or session["expected_processes"] != EXPECTED_PROCESSES
        or session["completed_processes"] != EXPECTED_PROCESSES
        or session["authorization_manifest_sha256"] != expected_authorization
        or session["admission_manifest_sha256"] != expected_admission
    ):
        raise AnalysisError("sealed session header differs")
    runtime_binding = _validate_runtime_binding(session["runtime_binding"])
    expected_root_paths = {
        "input_manifest": "input_manifest.json",
        "schedule": "schedule.json",
        "plan": "plan.json",
        "records": "records.ndjson",
    }
    for key, expected_path in expected_root_paths.items():
        _validate_result_identity(session[key], f"session {key}")
        if session[key].get("path") != expected_path:
            raise AnalysisError(f"session {key} path differs")
    _input_path, session_input_raw = _identity_bytes(
        result_root, session["input_manifest"], "session input manifest"
    )
    input_manifest, registered_input_raw = _canonical_json_file(
        input_root / "manifest.json", label="registered input manifest", pretty=True
    )
    if (
        sha256_bytes(session_input_raw) != expected_input
        or session_input_raw != registered_input_raw
        or input_manifest.get("schema_version") != INPUT_SCHEMA
    ):
        raise AnalysisError("session input manifest differs from the registered input")
    registered_sidecar = _regular_bytes(
        input_root / "manifest.sha256", label="registered input manifest sidecar"
    )
    if registered_sidecar != f"{expected_input}  manifest.json\n".encode("ascii"):
        raise AnalysisError("registered input manifest sidecar differs")
    _verify_input_tree(input_root, input_manifest)
    _schedule_path, session_schedule_raw = _identity_bytes(
        result_root, session["schedule"], "session schedule"
    )
    registered_schedule_raw = _regular_bytes(
        input_root / "schedule.json", label="registered schedule"
    )
    if (
        sha256_bytes(session_schedule_raw) != expected_schedule
        or session_schedule_raw != registered_schedule_raw
    ):
        raise AnalysisError("session schedule differs from the registered schedule")
    schedule_doc = _parse_json(session_schedule_raw, "session schedule")
    if session_schedule_raw != canonical_json_bytes(schedule_doc, pretty=True):
        raise AnalysisError("session schedule is not generator-canonical JSON")
    if (
        not isinstance(schedule_doc, dict)
        or schedule_doc.get("schema_version") != SCHEDULE_SCHEMA
        or schedule_doc.get("study_id") != STUDY_ID
        or not isinstance(schedule_doc.get("attempts"), list)
        or len(schedule_doc["attempts"]) != EXPECTED_PROCESSES
    ):
        raise AnalysisError("session schedule schema differs")
    schedule = schedule_doc["attempts"]
    _schedule_inventory(schedule)

    _plan_path, plan_raw = _identity_bytes(result_root, session["plan"], "session plan")
    if sha256_bytes(plan_raw) != expected_plan:
        raise AnalysisError("session plan differs from the external digest")
    plan = _parse_json(plan_raw, "session plan")
    if plan_raw != canonical_json_bytes(plan):
        raise AnalysisError("session plan is not canonical JSON")
    plan_keys = {
        "admission",
        "artifacts",
        "attempts",
        "classification",
        "input",
        "inventory",
        "policy",
        "result_contract",
        "schema_version",
        "study_id",
    }
    if not isinstance(plan, dict) or set(plan) != plan_keys:
        raise AnalysisError("session plan keys differ")
    plan_admission = plan.get("admission")
    plan_classification = plan.get("classification")
    if (
        plan.get("schema_version") != PLAN_SCHEMA
        or plan.get("study_id") != STUDY_ID
        or not isinstance(plan_admission, Mapping)
        or plan_admission.get("execution_authorized") is not False
        or plan_admission.get("authorization_manifest_sha256") is not None
        or plan_admission.get("admission_manifest_sha256") is not None
        or not isinstance(plan_classification, Mapping)
        or plan_classification.get("execution_authorized") is not False
    ):
        raise AnalysisError("session plan must remain prospectively non-authorizing")
    artifacts = plan.get("artifacts")
    plan_input = plan.get("input")
    inventory = plan.get("inventory")
    if (
        not isinstance(artifacts, Mapping)
        or artifacts.get("input_manifest_sha256") != expected_input
        or artifacts.get("schedule_sha256") != expected_schedule
        or artifacts.get("common_patch_sha256") != FROZEN_COMMON_PATCH_SHA256
        or artifacts.get("pixi_lock_sha256") != FROZEN_PIXI_LOCK_SHA256
        or artifacts.get("orca_build_capsule_manifest_sha256")
        != FROZEN_BUILD_CAPSULE_SHA256
        or artifacts.get("expected_binaries") != EXPECTED_BINARIES
        or artifacts.get("expected_libomp") != EXPECTED_LIBOMP
        or artifacts.get("source_endpoints") != SOURCE_ENDPOINTS
        or not isinstance(plan_input, Mapping)
        or plan_input.get("manifest_sha256") != expected_input
        or plan_input.get("schedule_sha256") != expected_schedule
        or not isinstance(inventory, Mapping)
        or inventory.get("processes") != EXPECTED_PROCESSES
        or not isinstance(plan.get("attempts"), list)
        or len(plan["attempts"]) != EXPECTED_PROCESSES
    ):
        raise AnalysisError("session plan frozen bindings differ")
    analyzer_identity = _validate_result_identity(
        artifacts.get("analyzer"), "plan analyzer"
    )
    analyzer_path = pathlib.Path(__file__).resolve()
    analyzer_raw = _regular_bytes(analyzer_path, label="current analyzer")
    if analyzer_identity != {
        "bytes": len(analyzer_raw),
        "mode": f"{stat.S_IMODE(analyzer_path.stat().st_mode):04o}",
        "path": "scripts/replication/analyze_orca_comparison.py",
        "sha256": sha256_bytes(analyzer_raw),
    }:
        raise AnalysisError("sealed plan does not bind the current analyzer bytes")
    plan_contract = plan.get("result_contract")
    if (
        not isinstance(plan_contract, Mapping)
        or set(plan_contract) != {"sha256", "value"}
        or _require_sha(plan_contract.get("sha256"), "plan result-contract SHA-256")
        != sha256_bytes(canonical_json_bytes(plan_contract.get("value")))
        or not isinstance(plan_contract.get("value"), Mapping)
        or plan_contract["value"].get("session_schema_version") != SESSION_SCHEMA
        or plan_contract["value"].get("terminal_outcomes")
        != ["artifact_failure", "completed", "nonzero_exit", "timeout"]
    ):
        raise AnalysisError("session plan result contract differs")

    _records_path, records_raw = _identity_bytes(
        result_root, session["records"], "session records ledger"
    )
    records = _parse_canonical_ndjson(records_raw, "session records ledger")
    attempt_identities = session["attempt_records"]
    if (
        len(records) != EXPECTED_PROCESSES
        or not isinstance(attempt_identities, list)
        or len(attempt_identities) != EXPECTED_PROCESSES
    ):
        raise AnalysisError("session attempt inventory differs")
    outcome_counts = {
        "artifact_failure": 0,
        "completed": 0,
        "nonzero_exit": 0,
        "timeout": 0,
    }
    for sequence, (planned, record, identity) in enumerate(
        zip(schedule, records, attempt_identities)
    ):
        method = planned["method"]
        expected_path = f"attempts/{sequence:03d}-{method}/record.json"
        if not isinstance(identity, Mapping) or identity.get("path") != expected_path:
            raise AnalysisError(f"attempt {sequence} record path differs")
        _validate_result_identity(identity, f"attempt {sequence} record")
        _record_path, record_raw = _identity_bytes(
            result_root, identity, f"attempt {sequence} record"
        )
        value = _parse_json(record_raw, f"attempt {sequence} record")
        if record_raw != canonical_json_bytes(value) or not _json_exactly_equal(
            value, record
        ):
            raise AnalysisError(f"attempt {sequence} record and ledger differ")
        _validate_attempt_record(
            input_root,
            result_root,
            record,
            planned,
            plan["attempts"][sequence],
            runtime_binding,
        )
        outcome_counts[record["outcome"]] += 1
    sparse_counts = {key: count for key, count in outcome_counts.items() if count}
    if session["outcome_counts"] != sparse_counts:
        raise AnalysisError("session outcome counts differ")
    _validate_result_tree(result_root, schedule, records)
    return SessionData(input_manifest, schedule, plan, records, session)


def _trace_records(path: pathlib.Path, label: str) -> tuple[dict[str, Any], list[dict[str, Any]], bytes]:
    raw = _regular_bytes(path, label=label, maximum_bytes=1024 * 1024 * 1024)
    if not raw or not raw.endswith(b"\n"):
        raise AnalysisError(f"{label} must be nonempty and LF-terminated")
    values: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(keepends=True), 1):
        if len(line) > 64 * 1024 * 1024:
            raise AnalysisError(f"{label} line {line_number} exceeds its bound")
        try:
            value = trace_compare.parse_portable_trace_json_line(line)
        except ValueError as error:
            raise AnalysisError(
                f"{label} line {line_number} violates the portable trace JSON contract: {error}"
            ) from error
        if not isinstance(value, dict):
            raise AnalysisError(f"{label} line {line_number} must be an object")
        values.append(value)
    if len(values) < 2 or values[0].get("type") != "header":
        raise AnalysisError(f"{label} header is missing")
    return values[0], values[1:], raw


def _trace_header(path: pathlib.Path, label: str) -> dict[str, Any]:
    """Read and validate only a trace's bounded first record."""

    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise AnalysisError(f"{label} is not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
        ):
            raise AnalysisError(f"{label} changed before reading")
        line = bytearray()
        while b"\n" not in line:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                raise AnalysisError(f"{label} header is not LF-terminated")
            line.extend(chunk)
            if len(line) > 64 * 1024 * 1024:
                raise AnalysisError(f"{label} header exceeds its bound")
        header_raw = bytes(line[: line.index(b"\n") + 1])
        ended = os.fstat(descriptor)
        if (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        ) != (ended.st_dev, ended.st_ino, ended.st_size, ended.st_mtime_ns):
            raise AnalysisError(f"{label} changed while reading")
    except OSError as error:
        raise AnalysisError(f"cannot read {label}: {error}") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        header = trace_compare.parse_portable_trace_json_line(header_raw)
    except ValueError as error:
        raise AnalysisError(
            f"{label} header violates the portable trace JSON contract: {error}"
        ) from error
    if not isinstance(header, dict) or header.get("type") != "header":
        raise AnalysisError(f"{label} header is missing")
    return header


def _regular_files_equal(left: pathlib.Path, right: pathlib.Path, label: str) -> bool:
    """Compare two authenticated regular files byte-for-byte with bounded memory."""

    left_meta, right_meta = left.lstat(), right.lstat()
    for metadata in (left_meta, right_meta):
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise AnalysisError(f"{label} input is not a regular file")
    if left_meta.st_size != right_meta.st_size:
        return False
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    try:
        descriptors = [os.open(left, flags), os.open(right, flags)]
        opened = [os.fstat(descriptor) for descriptor in descriptors]
        expected = [left_meta, right_meta]
        for current, prior in zip(opened, expected):
            if (current.st_dev, current.st_ino, current.st_size) != (
                prior.st_dev,
                prior.st_ino,
                prior.st_size,
            ):
                raise AnalysisError(f"{label} input changed before comparison")
        equal = True
        while True:
            chunks = [os.read(descriptor, 1024 * 1024) for descriptor in descriptors]
            if chunks[0] != chunks[1]:
                equal = False
                break
            if not chunks[0]:
                break
        ended = [os.fstat(descriptor) for descriptor in descriptors]
        for before, after in zip(opened, ended):
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise AnalysisError(f"{label} input changed during comparison")
        return equal
    except OSError as error:
        raise AnalysisError(f"cannot compare {label}: {error}") from error
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def _scenario_points(
    input_root: pathlib.Path, row: Mapping[str, Any]
) -> tuple[tuple[tuple[float, float], ...], tuple[tuple[float, float], ...]]:
    relative = row.get("scenario_relpath")
    path = _safe_relative(input_root, relative, "scenario")
    raw = _regular_bytes(path, label="scenario", maximum_bytes=16 * 1024 * 1024)
    if sha256_bytes(raw) != row.get("scenario_sha256"):
        raise AnalysisError("scenario identity differs from the schedule")
    scenario = _parse_json(raw, "scenario")
    if not isinstance(scenario, Mapping):
        raise AnalysisError("scenario must be an object")
    count = _integer(row.get("robot_count"), "schedule robot_count", minimum=1)
    if scenario.get("num_robots") != count or not isinstance(scenario.get("waypoints"), Mapping):
        raise AnalysisError("scenario robot inventory differs")
    waypoints = scenario["waypoints"]
    if set(waypoints) != {str(index) for index in range(count)}:
        raise AnalysisError("scenario robot IDs differ")
    starts: list[tuple[float, float]] = []
    goals: list[tuple[float, float]] = []
    for rid in range(count):
        route = waypoints[str(rid)]
        if not isinstance(route, list) or len(route) != 2:
            raise AnalysisError(f"scenario robot {rid} must have one start and one goal")
        starts.append(_point(route[0], f"scenario robot {rid} start"))
        goals.append(_point(route[1], f"scenario robot {rid} goal"))
    return tuple(starts), tuple(goals)


def _dancers_agent(
    value: object,
    *,
    expected_rid: int,
    expected_goal: tuple[float, float],
    label: str,
) -> AgentState:
    if not isinstance(value, Mapping) or value.get("rid") != expected_rid:
        raise AnalysisError(f"{label} robot ID differs")
    state = value.get("state")
    encoded = value.get("state_hex")
    if (
        not isinstance(state, list)
        or not isinstance(encoded, list)
        or len(state) != 6
        or len(encoded) != 6
    ):
        raise AnalysisError(f"{label} state dimensions differ")
    decoded = [
        validate_float_hex(
            state[index], encoded[index], 64, f"{label}.state[{index}]"
        )
        for index in range(6)
    ]
    radius = _positive(value.get("radius_m"), f"{label}.radius_m")
    goal = _point(value.get("final_goal"), f"{label}.final_goal")
    if goal != expected_goal or radius != 1.0:
        raise AnalysisError(f"{label} goal or radius differs from the scenario")
    return AgentState(
        rid=expected_rid,
        position=(decoded[0], decoded[1]),
        velocity=(decoded[3], decoded[4]),
        radius_m=radius,
        goal=goal,
    )


def _frame_agents(
    robots: object,
    *,
    count: int,
    goals: Sequence[tuple[float, float]],
    parser: Any,
    label: str,
) -> tuple[AgentState, ...]:
    if not isinstance(robots, list) or len(robots) != count:
        raise AnalysisError(f"{label} robot inventory differs")
    result = tuple(
        parser(
            robots[rid],
            expected_rid=rid,
            expected_goal=goals[rid],
            label=f"{label}.robots[{rid}]",
        )
        for rid in range(count)
    )
    if tuple(item.rid for item in result) != tuple(range(count)):
        raise AnalysisError(f"{label} robot ordering differs")
    return result


def normalize_dancers_trace(
    path: pathlib.Path,
    row: Mapping[str, Any],
    input_root: pathlib.Path,
    *,
    provenance: Mapping[str, Any] | None = None,
) -> CommonTrajectory:
    """Project a native DANCeRS pre/post positions trace onto H+1 states."""

    label = f"DANCeRS positions trace for sequence {row.get('sequence')}"
    header, native_frames, _raw = _trace_records(path, label)
    if provenance is None:
        metadata = header.get("metadata")
        if not isinstance(metadata, Mapping):
            raise AnalysisError(f"{label} provenance is missing")
        provenance = metadata
    _validate_dancers_header(header, row, provenance, label=label)
    starts, goals = _scenario_points(input_root, row)
    count = len(starts)
    native_steps = _integer(row.get("steps"), f"{label} steps", minimum=1)
    if row.get("cadence") != "main" or row.get("native_sample_stride") != 1:
        raise AnalysisError(f"{label} schedule cadence differs")
    if len(native_frames) != 2 * native_steps:
        raise AnalysisError(f"{label} native frame count differs")
    projected: list[CommonFrame] = []
    previous_post: tuple[AgentState, ...] | None = None
    for sweep in range(native_steps):
        before = native_frames[2 * sweep]
        after = native_frames[2 * sweep + 1]
        if (
            before.get("type") != "frame"
            or before.get("phase") != "pre_update"
            or before.get("tick") != sweep
            or after.get("type") != "frame"
            or after.get("phase") != "post_update"
            or after.get("tick") != sweep + 1
        ):
            raise AnalysisError(f"{label} frame pair differs at sweep {sweep}")
        before_agents = _frame_agents(
            before.get("robots"),
            count=count,
            goals=goals,
            parser=_dancers_agent,
            label=f"{label}.pre[{sweep}]",
        )
        after_agents = _frame_agents(
            after.get("robots"),
            count=count,
            goals=goals,
            parser=_dancers_agent,
            label=f"{label}.post[{sweep + 1}]",
        )
        if previous_post is not None and before_agents != previous_post:
            raise AnalysisError(f"{label} duplicate boundary state differs at tick {sweep}")
        if sweep == 0:
            projected.append(CommonFrame(0, before_agents))
        projected.append(CommonFrame(sweep + 1, after_agents))
        previous_post = after_agents
    if len(projected) != row.get("common_sample_count"):
        raise AnalysisError(f"{label} common sample count differs")
    for rid, agent in enumerate(projected[0].agents):
        if any(
            abs(agent.position[dimension] - starts[rid][dimension])
            > DANCERS_INITIALIZATION_ABSOLUTE_TOLERANCE
            for dimension in range(2)
        ) or any(
            abs(agent.velocity[dimension])
            > DANCERS_INITIALIZATION_ABSOLUTE_TOLERANCE
            for dimension in range(2)
        ):
            raise AnalysisError(f"{label} initial state differs for robot {rid}")
    return CommonTrajectory(
        scene_id=str(row["scene_id"]),
        scene_set=str(row["scene_set"]),
        method=str(row["method"]),
        cadence=str(row["cadence"]),
        timestep_seconds=COMMON_TIMESTEP_SECONDS,
        registered_starts=starts,
        registered_goals=goals,
        frames=tuple(projected),
    )


def _binary32(value: float) -> float:
    return struct.unpack(">f", struct.pack(">f", value))[0]


def _expected_orca_input(
    row: Mapping[str, Any], *, semantic_trace: bool
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "cadence": row["cadence"],
        "input_manifest_sha256": FROZEN_INPUT_MANIFEST_SHA256,
        "scenario_sha256": row["scenario_sha256"],
        "scene_id": row["scene_id"],
        "scene_set": row["scene_set"],
        "schedule_sha256": FROZEN_SCHEDULE_SHA256,
        "study_id": STUDY_ID,
    }
    if not semantic_trace:
        result.update(
            {
                "attempt_sequence": row["sequence"],
                "cohort_id": row["cohort_id"],
                "phase": row["phase"],
                "repetition": row["repetition"],
                "run_class": row["run_class"],
            }
        )
    return result


def _validate_orca_parameter_audit(
    audit: object,
    row: Mapping[str, Any],
    starts: Sequence[tuple[float, float]],
    *,
    label: str,
) -> None:
    value = _require_exact_keys(
        audit, {"accepted", "requested", "verified_through_public_getters"}, label
    )
    if value["verified_through_public_getters"] is not True:
        raise AnalysisError(f"{label} getter verification differs")
    requested = value["requested"]
    expected_requested = {
        "common_sample_count": row["common_sample_count"],
        "goal_latch": "permanent after first native boundary at distance <= 1 m",
        "goal_tolerance_m": 1.0,
        "initial_velocity_mps": [0.0, 0.0],
        "maximum_speed_mps": 2.0,
        "max_neighbors": row["robot_count"] - 1,
        "native_sample_stride": row["native_sample_stride"],
        "native_steps": row["steps"],
        "native_timestep_seconds": row["timestep_seconds"],
        "neighbor_distance_m": 20.0,
        "obstacle_count": 0,
        "robot_radius_m": 1.0,
        "stopping": "fixed ceiling; no early stop or agent deletion",
        "time_horizon_obstacles_seconds": 1.5,
        "time_horizon_seconds": 1.5,
    }
    if not _json_exactly_equal(requested, expected_requested):
        raise AnalysisError(f"{label} requested parameters differ")
    accepted = _require_exact_keys(
        value["accepted"],
        {"agents", "num_agents", "num_obstacle_vertices", "time_step"},
        f"{label}.accepted",
    )
    if (
        accepted["num_agents"] != row["robot_count"]
        or accepted["num_obstacle_vertices"] != 0
        or _float_record(accepted["time_step"], 32, f"{label}.time_step")
        != _binary32(row["timestep_seconds"])
        or not isinstance(accepted["agents"], list)
        or len(accepted["agents"]) != row["robot_count"]
    ):
        raise AnalysisError(f"{label} accepted simulator parameters differ")
    expected_float_values = {
        "maximum_speed": 2.0,
        "neighbor_distance": 20.0,
        "radius": 1.0,
        "time_horizon": 1.5,
        "time_horizon_obstacles": 1.5,
    }
    agent_keys = {
        "initial_position",
        "initial_velocity",
        "max_neighbors",
        "maximum_speed",
        "neighbor_distance",
        "radius",
        "rid",
        "time_horizon",
        "time_horizon_obstacles",
    }
    for rid, agent in enumerate(accepted["agents"]):
        item = _require_exact_keys(agent, agent_keys, f"{label}.agents[{rid}]")
        expected_start = (_binary32(starts[rid][0]), _binary32(starts[rid][1]))
        if (
            item["rid"] != rid
            or item["max_neighbors"] != row["robot_count"] - 1
            or _point_record(
                item["initial_position"], 32, f"{label}.agents[{rid}].initial_position"
            )
            != expected_start
            or _point_record(
                item["initial_velocity"], 32, f"{label}.agents[{rid}].initial_velocity"
            )
            != (0.0, 0.0)
        ):
            raise AnalysisError(f"{label} accepted agent {rid} differs")
        for key, expected in expected_float_values.items():
            if _float_record(item[key], 32, f"{label}.agents[{rid}].{key}") != expected:
                raise AnalysisError(f"{label} accepted agent {rid} {key} differs")


def _orca_agent(
    value: object,
    *,
    expected_rid: int,
    expected_goal: tuple[float, float],
    label: str,
) -> AgentState:
    keys = {
        "executed_velocity",
        "first_goal_native_step",
        "goal",
        "goal_distance_m",
        "goal_latched",
        "position",
        "preferred_velocity",
        "radius",
        "rid",
    }
    if not isinstance(value, Mapping) or set(value) != keys or value.get("rid") != expected_rid:
        raise AnalysisError(f"{label} robot ID differs")
    position = _point_record(value.get("position"), 32, f"{label}.position")
    velocity = _point_record(
        value.get("executed_velocity"), 32, f"{label}.executed_velocity"
    )
    goal = _point_record(value.get("goal"), 32, f"{label}.goal")
    _point_record(value.get("preferred_velocity"), 32, f"{label}.preferred_velocity")
    radius = _float_record(value.get("radius"), 32, f"{label}.radius")
    distance = _finite(value.get("goal_distance_m"), f"{label}.goal_distance_m")
    latched = value.get("goal_latched")
    first_step = value.get("first_goal_native_step")
    if (
        distance < 0.0
        or type(latched) is not bool
        or (first_step is not None and (type(first_step) is not int or first_step < 0))
        or latched != (first_step is not None)
    ):
        raise AnalysisError(f"{label} goal-latch fields differ")
    expected_binary32_goal = (_binary32(expected_goal[0]), _binary32(expected_goal[1]))
    if goal != expected_binary32_goal or radius != 1.0:
        raise AnalysisError(f"{label} goal or radius differs from the scenario")
    return AgentState(expected_rid, position, velocity, radius, goal)


def normalize_orca_trace(
    path: pathlib.Path,
    row: Mapping[str, Any],
    input_root: pathlib.Path,
    *,
    expected_parameter_audit: Mapping[str, Any] | None = None,
    expected_goal_latches: Sequence[Mapping[str, Any]] | None = None,
) -> CommonTrajectory:
    """Validate and normalize a native ORCA trace at its recorded common states."""

    label = f"ORCA positions trace for sequence {row.get('sequence')}"
    header, frames, _raw = _trace_records(path, label)
    if (
        set(header)
        != {
            "input",
            "numeric_identity",
            "observation_rule",
            "parameter_audit",
            "schema_version",
            "source",
            "type",
        }
        or header.get("type") != "header"
        or header.get("schema_version") != "dancers-orca-trace-v1"
        or header.get("numeric_identity")
        != "native IEEE-754 binary32 values plus 8-digit hex bits"
        or header.get("observation_rule")
        != "initial state plus post-doStep states; integer common ticks define time"
    ):
        raise AnalysisError(f"{label} header contract differs")
    if not _json_exactly_equal(header.get("source"), ORCA_SOURCE):
        raise AnalysisError(f"{label} source binding differs")
    trace_input = header.get("input")
    if not _json_exactly_equal(
        trace_input, _expected_orca_input(row, semantic_trace=True)
    ):
        raise AnalysisError(f"{label} input binding differs")
    starts, goals = _scenario_points(input_root, row)
    count = len(starts)
    _validate_orca_parameter_audit(
        header.get("parameter_audit"), row, starts, label=f"{label}.parameter_audit"
    )
    if expected_parameter_audit is not None and not _json_exactly_equal(
        header.get("parameter_audit"), expected_parameter_audit
    ):
        raise AnalysisError(f"{label} header/summary parameter audit differs")
    stride = _integer(
        row.get("native_sample_stride"), f"{label} sample stride", minimum=1
    )
    expected_samples = _integer(
        row.get("common_sample_count"), f"{label} sample count", minimum=2
    )
    if len(frames) != expected_samples:
        raise AnalysisError(f"{label} frame count differs")
    projected: list[CommonFrame] = []
    first_steps: list[int | None] = [None] * count
    for tick, frame in enumerate(frames):
        if (
            set(frame)
            != {"common_tick", "native_step", "nominal_time_seconds", "robots", "type"}
            or frame.get("type") != "frame"
            or frame.get("common_tick") != tick
            or frame.get("native_step") != tick * stride
            or _finite(frame.get("nominal_time_seconds"), f"{label} time {tick}")
            != tick / 10.0
        ):
            raise AnalysisError(f"{label} common frame differs at tick {tick}")
        agents = _frame_agents(
            frame.get("robots"),
            count=count,
            goals=goals,
            parser=_orca_agent,
            label=f"{label}.frames[{tick}]",
        )
        raw_robots = frame["robots"]
        for rid, raw_agent in enumerate(raw_robots):
            first_step = raw_agent["first_goal_native_step"]
            latched = raw_agent["goal_latched"]
            if first_step is not None and first_step > frame["native_step"]:
                raise AnalysisError(f"{label} future goal latch for robot {rid}")
            if first_steps[rid] is None and latched:
                first_steps[rid] = first_step
            if first_steps[rid] != first_step:
                raise AnalysisError(f"{label} goal latch is not sticky for robot {rid}")
        projected.append(CommonFrame(tick, agents))
    for rid, agent in enumerate(projected[0].agents):
        expected_start = (_binary32(starts[rid][0]), _binary32(starts[rid][1]))
        if agent.position != expected_start or agent.velocity != (0.0, 0.0):
            raise AnalysisError(f"{label} initial state differs for robot {rid}")
    if expected_goal_latches is not None:
        expected_steps = [
            item.get("first_goal_native_step")
            if isinstance(item, Mapping)
            else object()
            for item in expected_goal_latches
        ]
        if first_steps != expected_steps:
            raise AnalysisError(f"{label} trace/summary goal latches differ")
    return CommonTrajectory(
        scene_id=str(row["scene_id"]),
        scene_set=str(row["scene_set"]),
        method="orca",
        cadence=str(row["cadence"]),
        timestep_seconds=COMMON_TIMESTEP_SECONDS,
        registered_starts=starts,
        registered_goals=goals,
        frames=tuple(projected),
    )


def _segment_circle_interval(
    start: tuple[float, float],
    end: tuple[float, float],
    center: tuple[float, float],
    radius: float,
) -> tuple[float, float] | None:
    """Closed parameter interval for a line segment inside a closed circle."""

    sx, sy = start[0] - center[0], start[1] - center[1]
    dx, dy = end[0] - start[0], end[1] - start[1]
    scale = max(abs(sx), abs(sy), abs(dx), abs(dy), radius)
    if not math.isfinite(scale) or scale <= 0.0:
        raise AnalysisError("segment-circle scale is invalid")
    sx, sy, dx, dy, rr = sx / scale, sy / scale, dx / scale, dy / scale, radius / scale
    quadratic = dx * dx + dy * dy
    constant = sx * sx + sy * sy - rr * rr
    if quadratic == 0.0:
        return (0.0, 1.0) if constant <= 0.0 else None
    dot = sx * dx + sy * dy
    minimum_u = min(1.0, max(0.0, -dot / quadratic))
    minimum_sq = (sx + minimum_u * dx) ** 2 + (sy + minimum_u * dy) ** 2
    if minimum_sq > rr * rr:
        return None
    linear = 2.0 * dot
    discriminant = max(0.0, linear * linear - 4.0 * quadratic * constant)
    root = math.sqrt(discriminant)
    enter = max(0.0, (-linear - root) / (2.0 * quadratic))
    leave = min(1.0, (-linear + root) / (2.0 * quadratic))
    return (enter, leave) if enter <= leave else None


def _segment_pair_clearance(
    left_start: tuple[float, float],
    left_end: tuple[float, float],
    right_start: tuple[float, float],
    right_end: tuple[float, float],
    radius_sum: float,
) -> float:
    sx, sy = left_start[0] - right_start[0], left_start[1] - right_start[1]
    dx = (left_end[0] - left_start[0]) - (right_end[0] - right_start[0])
    dy = (left_end[1] - left_start[1]) - (right_end[1] - right_start[1])
    scale = max(abs(sx), abs(sy), abs(dx), abs(dy), radius_sum)
    if not math.isfinite(scale) or scale <= 0.0:
        raise AnalysisError("pair-clearance scale is invalid")
    nsx, nsy, ndx, ndy = sx / scale, sy / scale, dx / scale, dy / scale
    quadratic = ndx * ndx + ndy * ndy
    u = 0.0 if quadratic == 0.0 else min(
        1.0, max(0.0, -(nsx * ndx + nsy * ndy) / quadratic)
    )
    return scale * math.hypot(nsx + u * ndx, nsy + u * ndy) - radius_sum


def _merge_closed_intervals(
    intervals: Iterable[tuple[float, float]],
) -> tuple[tuple[float, float], ...]:
    ordered = sorted(intervals)
    merged: list[tuple[float, float]] = []
    for start, end in ordered:
        if not math.isfinite(start) or not math.isfinite(end) or start > end:
            raise AnalysisError("contact interval is invalid")
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return tuple(merged)


def pair_contact_geometry(trajectory: CommonTrajectory) -> dict[str, Any]:
    """Derive continuous clearance, contact episodes, and pair-time exposure."""

    _validate_trajectory_shape(trajectory)
    count = len(trajectory.frames[0].agents)
    dt = trajectory.timestep_seconds
    minimum = math.inf
    episode_count = 0
    exposure = 0.0
    pair_rows: list[dict[str, Any]] = []
    for left in range(count):
        for right in range(left + 1, count):
            intervals: list[tuple[float, float]] = []
            pair_minimum = math.inf
            for index in range(trajectory.common_steps):
                first = trajectory.frames[index].agents
                last = trajectory.frames[index + 1].agents
                radius_sum = first[left].radius_m + first[right].radius_m
                clearance = _segment_pair_clearance(
                    first[left].position,
                    last[left].position,
                    first[right].position,
                    last[right].position,
                    radius_sum,
                )
                pair_minimum = min(pair_minimum, clearance)
                relative_start = (
                    first[left].position[0] - first[right].position[0],
                    first[left].position[1] - first[right].position[1],
                )
                relative_end = (
                    last[left].position[0] - last[right].position[0],
                    last[left].position[1] - last[right].position[1],
                )
                contact = _segment_circle_interval(
                    relative_start, relative_end, (0.0, 0.0), radius_sum
                )
                if contact is not None:
                    intervals.append(
                        ((index + contact[0]) * dt, (index + contact[1]) * dt)
                    )
            merged = _merge_closed_intervals(intervals)
            pair_exposure = math.fsum(end - start for start, end in merged)
            minimum = min(minimum, pair_minimum)
            episode_count += len(merged)
            exposure += pair_exposure
            pair_rows.append(
                {
                    "left_rid": left,
                    "right_rid": right,
                    "minimum_clearance_m": pair_minimum,
                    "contact_episodes": len(merged),
                    "contact_exposure_seconds": pair_exposure,
                    "contact_intervals_seconds": [list(item) for item in merged],
                }
            )
    if count < 2:
        minimum_value: float | None = None
    else:
        minimum_value = minimum
    return {
        "minimum_pair_clearance_m": minimum_value,
        "pair_contact_episodes": episode_count,
        "pair_contact_exposure_pair_seconds": exposure,
        "pair_rows": pair_rows,
    }


def _validate_trajectory_shape(trajectory: CommonTrajectory) -> None:
    if (
        not isinstance(trajectory, CommonTrajectory)
        or trajectory.timestep_seconds != COMMON_TIMESTEP_SECONDS
        or len(trajectory.frames) < 2
    ):
        raise AnalysisError("common trajectory header differs")
    count = len(trajectory.frames[0].agents)
    if (
        count < 1
        or len(trajectory.registered_starts) != count
        or len(trajectory.registered_goals) != count
    ):
        raise AnalysisError("common trajectory robot inventory differs")
    identities = tuple(range(count))
    radii = tuple(agent.radius_m for agent in trajectory.frames[0].agents)
    for tick, frame in enumerate(trajectory.frames):
        if frame.tick != tick or tuple(agent.rid for agent in frame.agents) != identities:
            raise AnalysisError(f"common trajectory frame {tick} identities differ")
        if tuple(agent.radius_m for agent in frame.agents) != radii:
            raise AnalysisError(f"common trajectory frame {tick} radii differ")
        if any(agent.radius_m <= 0.0 for agent in frame.agents):
            raise AnalysisError(f"common trajectory frame {tick} radius is invalid")


def _first_passage_time(
    positions: Sequence[tuple[float, float]],
    goal: tuple[float, float],
    radius: float,
    timestep: float,
) -> float | None:
    if math.hypot(positions[0][0] - goal[0], positions[0][1] - goal[1]) <= radius:
        return 0.0
    for index, (first, last) in enumerate(zip(positions, positions[1:])):
        interval = _segment_circle_interval(first, last, goal, radius)
        if interval is not None:
            return (index + interval[0]) * timestep
    return None


def _agent_motion_metrics(
    trajectory: CommonTrajectory, rid: int
) -> dict[str, Any]:
    positions = [frame.agents[rid].position for frame in trajectory.frames]
    velocities = [frame.agents[rid].velocity for frame in trajectory.frames]
    goal = trajectory.registered_goals[rid]
    start = trajectory.registered_starts[rid]
    passage = _first_passage_time(
        positions, goal, GOAL_TOLERANCE_M, trajectory.timestep_seconds
    )
    event = passage is not None and passage <= trajectory.ceiling_seconds
    restricted = passage if event else trajectory.ceiling_seconds
    path_length = math.fsum(
        math.hypot(last[0] - first[0], last[1] - first[1])
        for first, last in zip(positions, positions[1:])
    )
    straight = math.hypot(goal[0] - start[0], goal[1] - start[1])
    delta_velocities = [
        (last[0] - first[0], last[1] - first[1])
        for first, last in zip(velocities, velocities[1:])
    ]
    velocity_tv = math.fsum(math.hypot(item[0], item[1]) for item in delta_velocities)
    accelerations = [
        (item[0] / trajectory.timestep_seconds, item[1] / trajectory.timestep_seconds)
        for item in delta_velocities
    ]
    acceleration_rms = math.sqrt(
        math.fsum(item[0] * item[0] + item[1] * item[1] for item in accelerations)
        / len(accelerations)
    )
    jerks = [
        (
            (last[0] - first[0]) / trajectory.timestep_seconds,
            (last[1] - first[1]) / trajectory.timestep_seconds,
        )
        for first, last in zip(accelerations, accelerations[1:])
    ]
    if not jerks:
        raise AnalysisError("common trajectory has too few frames for jerk RMS")
    jerk_rms = math.sqrt(
        math.fsum(item[0] * item[0] + item[1] * item[1] for item in jerks)
        / len(jerks)
    )
    return {
        "rid": rid,
        "goal_passage_event": event,
        "first_passage_time_s": passage if event else None,
        "restricted_passage_time_s": restricted,
        "path_length_m": path_length,
        "straight_start_goal_distance_m": straight,
        "path_excess_m": path_length - straight,
        "velocity_total_variation_mps": velocity_tv,
        "acceleration_rms_mps2": acceleration_rms,
        "jerk_rms_mps3": jerk_rms,
    }


def _analyze_trajectory_unchecked(trajectory: CommonTrajectory) -> dict[str, Any]:
    """Calculate registered outcomes before the finite-result boundary."""

    _validate_trajectory_shape(trajectory)
    count = len(trajectory.frames[0].agents)
    agents = [_agent_motion_metrics(trajectory, rid) for rid in range(count)]
    geometry = pair_contact_geometry(trajectory)
    goals_reached = sum(item["goal_passage_event"] for item in agents)
    complete = goals_reached == count
    makespan = (
        max(float(item["first_passage_time_s"]) for item in agents)
        if complete
        else None
    )
    scene = {
        "scene_id": trajectory.scene_id,
        "scene_set": trajectory.scene_set,
        "method": trajectory.method,
        "cadence": trajectory.cadence,
        "robot_count": count,
        "common_steps": trajectory.common_steps,
        "ceiling_seconds": trajectory.ceiling_seconds,
        "behavior_available": True,
        "failure_reason": None,
        "goals_reached": goals_reached,
        "agent_completion_fraction": goals_reached / count,
        "all_agents_completion": complete,
        "fixed_ceiling_completion_class": (
            "all_agents_completed_by_fixed_ceiling"
            if complete
            else "noncompletion_by_fixed_ceiling"
        ),
        "all_agents_makespan_s": makespan,
        "mean_restricted_agent_completion_time_s": statistics.fmean(
            item["restricted_passage_time_s"] for item in agents
        ),
        "total_path_length_m": math.fsum(item["path_length_m"] for item in agents),
        "mean_path_length_m": statistics.fmean(item["path_length_m"] for item in agents),
        "total_path_excess_m": math.fsum(item["path_excess_m"] for item in agents),
        "mean_path_excess_m": statistics.fmean(item["path_excess_m"] for item in agents),
        "minimum_pair_clearance_m": geometry["minimum_pair_clearance_m"],
        "pair_contact_episodes": geometry["pair_contact_episodes"],
        "pair_contact_exposure_pair_seconds": geometry[
            "pair_contact_exposure_pair_seconds"
        ],
        "mean_velocity_total_variation_mps": statistics.fmean(
            item["velocity_total_variation_mps"] for item in agents
        ),
        "mean_acceleration_rms_mps2": statistics.fmean(
            item["acceleration_rms_mps2"] for item in agents
        ),
        "mean_jerk_rms_mps3": statistics.fmean(
            item["jerk_rms_mps3"] for item in agents
        ),
    }
    return {"agent_rows": agents, "pair_rows": geometry["pair_rows"], "scene": scene}


def analyze_trajectory(trajectory: CommonTrajectory) -> dict[str, Any]:
    """Calculate outcomes and reject arithmetic that cannot serialize finitely."""

    try:
        result = _analyze_trajectory_unchecked(trajectory)
        canonical_json_bytes(result)
        return result
    except AnalysisError:
        raise
    except (ArithmeticError, RecursionError, TypeError, ValueError) as error:
        raise AnalysisError(
            "common trajectory metric reduction is non-finite or invalid"
        ) from error


def failed_behavior_row(row: Mapping[str, Any], reason: str) -> dict[str, Any]:
    """Registered process failures count as completion zero, never as exclusions."""

    result: dict[str, Any] = {
        "scene_id": row["scene_id"],
        "scene_set": row["scene_set"],
        "method": row["method"],
        "cadence": row["cadence"],
        "robot_count": row["robot_count"],
        "common_steps": row["common_sample_count"] - 1,
        "ceiling_seconds": row["duration_seconds"],
        "behavior_available": False,
        "failure_reason": reason,
        "goals_reached": 0,
        "agent_completion_fraction": 0.0,
        "all_agents_completion": False,
        "fixed_ceiling_completion_class": (
            "not_observed_due_to_registered_failure"
        ),
        "all_agents_makespan_s": None,
    }
    for metric in BEHAVIOR_DIFFERENCE_METRICS:
        if metric not in ("all_agents_completion", "agent_completion_fraction"):
            result[metric] = None
    result["total_path_length_m"] = None
    result["total_path_excess_m"] = None
    return result


def _artifact_path(result_root: pathlib.Path, record: Mapping[str, Any], name: str) -> pathlib.Path:
    artifacts = record.get("artifacts")
    if not isinstance(artifacts, Mapping) or name not in artifacts:
        raise AnalysisError(f"sequence {record.get('schedule', {}).get('sequence')} lacks {name}")
    return _identity_path(result_root, artifacts[name], f"artifact {name}")


def _dancers_provenance(
    result_root: pathlib.Path, record: Mapping[str, Any]
) -> dict[str, Any]:
    row = record["schedule"]
    method = row["method"]
    if method not in ALGORITHM_REVISIONS:
        raise AnalysisError("ORCA does not have DANCeRS provenance")
    path = _artifact_path(result_root, record, "provenance.json")
    provenance, _raw = _canonical_json_file(
        path, label=f"DANCeRS provenance {row['sequence']}", pretty=False
    )
    expected = {
        "algorithm_revision": ALGORITHM_REVISIONS[method],
        "binary_sha256": record["binary"]["sha256"],
        "common_patch_sha256": FROZEN_COMMON_PATCH_SHA256,
        "config_sha256": row["config_sha256"],
        "implementation_id": method,
        "map_sha256": EMPTY_SHA256,
        "map_status": "absent; SHA-256 is the empty-byte sentinel",
        "omp_threads": 1,
        "pixi_lock_sha256": FROZEN_PIXI_LOCK_SHA256,
        "run_id": f"{STUDY_ID}-{row['sequence']:03d}",
        "scenario_sha256": row["scenario_sha256"],
        "scene_id": row["scene_id"],
    }
    if not _json_exactly_equal(provenance, expected):
        raise AnalysisError(f"DANCeRS provenance differs at sequence {row['sequence']}")
    return provenance


def _validate_dancers_header(
    header: Mapping[str, Any],
    row: Mapping[str, Any],
    provenance: Mapping[str, Any],
    *,
    label: str,
) -> None:
    observer = header.get("observer")
    metadata = header.get("metadata")
    resolved = header.get("resolved_config")
    expected_prefix = "comparison/publication/dancers-orca-v1/"
    if (
        header.get("schema_version") != trace_compare.TRACE_SCHEMA_V3
        or not isinstance(observer, Mapping)
        or observer.get("trace_detail") != row.get("trace_detail")
        or not _json_exactly_equal(metadata, provenance)
        or not isinstance(resolved, Mapping)
        or resolved.get("config_file") != expected_prefix + row["config_relpath"]
        or resolved.get("scenario_file") != expected_prefix + row["scenario_relpath"]
        or resolved.get("obstacle_file") != ""
        or resolved.get("obstacles_present") is not False
        or resolved.get("configured_robot_count") != row["robot_count"]
        or resolved.get("max_timesteps") != row["steps"]
        or _finite(resolved.get("timestep_seconds"), f"{label} timestep")
        != _binary32(COMMON_TIMESTEP_SECONDS)
    ):
        raise AnalysisError(f"{label} header or registered paths differ")


def _dancers_summary(
    result_root: pathlib.Path, record: Mapping[str, Any]
) -> dict[str, Any]:
    row = record["schedule"]
    path = _artifact_path(result_root, record, "summary.json")
    summary, _raw = _canonical_json_file(path, label=f"DANCeRS summary {row['sequence']}", pretty=True)
    provenance = _dancers_provenance(result_root, record)
    steps = row["steps"]
    count = row["robot_count"]
    expected_trace_detail = row["trace_detail"]
    if (
        not isinstance(summary, dict)
        or summary.get("schema_version") != "dancers-comparison-summary-v1"
        or summary.get("trace_detail") != expected_trace_detail
        or summary.get("final_tick") != steps
        or summary.get("frames_recorded") != 2 * steps
        or summary.get("final_robot_count") != count
        or summary.get("maximum_robot_count") != count
        or summary.get("iterations") != 2 * steps
        or summary.get("run_valid") is not True
        or summary.get("termination_reason") != "maximum_time"
        or not _json_exactly_equal(summary.get("metadata"), provenance)
    ):
        raise AnalysisError(f"DANCeRS summary fixed-work fields differ at sequence {row['sequence']}")
    zero_fields = (
        "invalid_geometry_evaluations",
        "invalid_robot_frames",
        "nonfinite_numeric_observations",
        "nonfinite_state_observations",
        "obstacle_collision_events",
    )
    if any(summary.get(field) != 0 for field in zero_fields) or summary.get(
        "obstacle_collision_exposure_seconds"
    ) != 0.0:
        raise AnalysisError(f"DANCeRS summary validity fields differ at sequence {row['sequence']}")
    missing = [field for field in SUMMARY_PARITY_FIELDS if field not in summary]
    if missing:
        raise AnalysisError(f"DANCeRS summary parity fields are missing: {missing}")
    return {field: summary[field] for field in SUMMARY_PARITY_FIELDS}


def _orca_summary(
    input_root: pathlib.Path,
    result_root: pathlib.Path,
    record: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    row = record["schedule"]
    path = _artifact_path(result_root, record, "summary.json")
    summary, _raw = _canonical_json_file(path, label=f"ORCA summary {row['sequence']}", pretty=True)
    summary_keys = {
        "all_goals_latched",
        "common_states_evaluated",
        "completed_native_steps",
        "finite_state",
        "final_native_global_time",
        "goal_latches",
        "input",
        "parameter_audit",
        "schema_version",
        "source",
        "termination",
        "trace_bytes",
        "trace_frames_written",
        "trace_sha256",
    }
    if (
        not isinstance(summary, dict)
        or set(summary) != summary_keys
        or summary.get("schema_version") != "dancers-orca-summary-v1"
        or not _json_exactly_equal(summary.get("source"), ORCA_SOURCE)
    ):
        raise AnalysisError(f"ORCA summary schema differs at sequence {row['sequence']}")
    trace_expected = row["trace_detail"] == "positions"
    if (
        summary.get("completed_native_steps") != row["steps"]
        or summary.get("common_states_evaluated") != row["common_sample_count"]
        or summary.get("finite_state") is not True
        or summary.get("termination") != "fixed_ceiling"
        or summary.get("trace_frames_written")
        != (row["common_sample_count"] if trace_expected else 0)
    ):
        raise AnalysisError(f"ORCA summary fixed-work fields differ at sequence {row['sequence']}")
    observed_final_time = _float_record(
        summary.get("final_native_global_time"),
        32,
        f"ORCA summary {row['sequence']} final time",
    )
    expected_final_time = _binary32(0.0)
    native_timestep = _binary32(row["timestep_seconds"])
    for _step in range(row["steps"]):
        expected_final_time = _binary32(expected_final_time + native_timestep)
    if observed_final_time != expected_final_time:
        raise AnalysisError(f"ORCA final native time differs at sequence {row['sequence']}")
    summary_input = summary.get("input")
    if not _json_exactly_equal(
        summary_input, _expected_orca_input(row, semantic_trace=False)
    ):
        raise AnalysisError(f"ORCA summary input binding differs at sequence {row['sequence']}")
    parameter_audit = summary.get("parameter_audit")
    starts, _goals = _scenario_points(input_root, row)
    _validate_orca_parameter_audit(
        parameter_audit, row, starts, label=f"ORCA summary {row['sequence']} parameter audit"
    )
    latches = summary.get("goal_latches")
    if not isinstance(latches, list) or len(latches) != row["robot_count"]:
        raise AnalysisError(f"ORCA goal latch inventory differs at sequence {row['sequence']}")
    latch_values: list[bool] = []
    for rid, latch in enumerate(latches):
        item = _require_exact_keys(
            latch,
            {"first_goal_native_step", "goal_latched", "rid"},
            f"ORCA summary {row['sequence']} latch {rid}",
        )
        first_step = item["first_goal_native_step"]
        if (
            item["rid"] != rid
            or type(item["goal_latched"]) is not bool
            or (
                first_step is not None
                and (type(first_step) is not int or not 0 <= first_step <= row["steps"])
            )
            or item["goal_latched"] != (first_step is not None)
        ):
            raise AnalysisError(f"ORCA goal latch differs at sequence {row['sequence']}")
        latch_values.append(item["goal_latched"])
    if summary.get("all_goals_latched") is not all(latch_values):
        raise AnalysisError(f"ORCA all-goal latch differs at sequence {row['sequence']}")
    if trace_expected:
        trace_identity = record["artifacts"].get("trace.ndjson")
        if not isinstance(trace_identity, Mapping):
            raise AnalysisError(f"ORCA trace identity is missing at sequence {row['sequence']}")
        if (
            summary.get("trace_sha256") != trace_identity.get("sha256")
            or summary.get("trace_bytes") != trace_identity.get("bytes")
        ):
            raise AnalysisError(f"ORCA summary trace identity differs at sequence {row['sequence']}")
    elif summary.get("trace_sha256") is not None or summary.get("trace_bytes") is not None:
        raise AnalysisError(f"ORCA observer-free summary contains a trace at sequence {row['sequence']}")
    semantic = json.loads(json.dumps(summary, allow_nan=False))
    del semantic["input"]["repetition"]
    del semantic["input"]["attempt_sequence"]
    return summary, semantic


def _group_records(
    records: Sequence[Mapping[str, Any]],
    predicate: Any,
    key: Any,
) -> dict[Any, list[Mapping[str, Any]]]:
    grouped: dict[Any, list[Mapping[str, Any]]] = {}
    for record in records:
        if predicate(record):
            grouped.setdefault(key(record), []).append(record)
    return grouped


def analyze_dancers_fidelity(
    records: Sequence[Mapping[str, Any]], result_root: pathlib.Path
) -> dict[str, Any]:
    """Apply both exact DANCeRS trace gates without deleting failures."""

    measured = _group_records(
        records,
        lambda record: record["schedule"]["phase"] == "measured"
        and record["schedule"]["method"] in ("reference", "optimized")
        and record["schedule"]["run_class"] in ("digest_fidelity", "positions_fidelity"),
        lambda record: (
            record["schedule"]["scene_id"],
            record["schedule"]["run_class"],
        ),
    )
    if len(measured) != 46:
        raise AnalysisError("DANCeRS fidelity pair inventory differs")
    class_rows: list[dict[str, Any]] = []
    class_summaries: dict[tuple[str, str], dict[str, Any]] = {}
    for (scene_id, run_class), pair in sorted(measured.items()):
        if len(pair) != 2 or {item["schedule"]["method"] for item in pair} != {
            "reference",
            "optimized",
        }:
            raise AnalysisError(f"DANCeRS fidelity methods differ for {scene_id}/{run_class}")
        by_method = {item["schedule"]["method"]: item for item in pair}
        reference, optimized = by_method["reference"], by_method["optimized"]
        row: dict[str, Any] = {
            "scene_id": scene_id,
            "run_class": run_class,
            "reference_sequence": reference["schedule"]["sequence"],
            "optimized_sequence": optimized["schedule"]["sequence"],
            "processes_completed": reference["outcome"] == optimized["outcome"] == "completed",
            "semantic_trace_equal": False,
            "summary_equal": False,
            "gate_passed": False,
            "failure_reason": None,
        }
        if not row["processes_completed"]:
            row["failure_reason"] = "registered-process-failure"
            class_rows.append(row)
            continue
        reference_summary = _dancers_summary(result_root, reference)
        optimized_summary = _dancers_summary(result_root, optimized)
        row["summary_equal"] = _json_exactly_equal(
            reference_summary, optimized_summary
        )
        class_summaries[(scene_id, run_class)] = reference_summary
        for candidate in (reference, optimized):
            trace_path = _artifact_path(result_root, candidate, "trace.ndjson")
            header = _trace_header(
                trace_path,
                f"DANCeRS fidelity trace {candidate['schedule']['sequence']}",
            )
            _validate_dancers_header(
                header,
                candidate["schedule"],
                _dancers_provenance(result_root, candidate),
                label=f"DANCeRS fidelity trace {candidate['schedule']['sequence']}",
            )
        comparison = trace_compare.compare_traces(
            _artifact_path(result_root, reference, "trace.ndjson"),
            _artifact_path(result_root, optimized, "trace.ndjson"),
            limits=_dancers_trace_limits(reference["schedule"]),
        )
        expected_frames = 2 * reference["schedule"]["steps"]
        row["frames_compared"] = comparison.frames_compared
        row["semantic_trace_equal"] = comparison.equal and comparison.frames_compared == expected_frames
        if comparison.mismatch is not None:
            row["first_mismatch"] = comparison.mismatch.format()
        row["gate_passed"] = row["summary_equal"] and row["semantic_trace_equal"]
        if not row["gate_passed"]:
            row["failure_reason"] = "semantic-fidelity-mismatch"
        class_rows.append(row)

    scene_rows: list[dict[str, Any]] = []
    scene_ids = sorted({item["scene_id"] for item in class_rows})
    for scene_id in scene_ids:
        rows = [item for item in class_rows if item["scene_id"] == scene_id]
        if len(rows) != 2:
            raise AnalysisError(f"DANCeRS fidelity class inventory differs for {scene_id}")
        cross_detail = False
        if (scene_id, "digest_fidelity") in class_summaries and (
            scene_id,
            "positions_fidelity",
        ) in class_summaries:
            cross_detail = _json_exactly_equal(
                class_summaries[(scene_id, "digest_fidelity")],
                class_summaries[(scene_id, "positions_fidelity")],
            )
        passed = all(item["gate_passed"] for item in rows) and cross_detail
        scene_rows.append(
            {
                "scene_id": scene_id,
                "digest_gate_passed": next(
                    item["gate_passed"] for item in rows if item["run_class"] == "digest_fidelity"
                ),
                "positions_gate_passed": next(
                    item["gate_passed"] for item in rows if item["run_class"] == "positions_fidelity"
                ),
                "cross_detail_summary_equal": cross_detail,
                "behavior_admissible": passed,
                "resource_admissible": passed,
            }
        )
    return {
        "all_scenes_passed": all(item["behavior_admissible"] for item in scene_rows),
        "class_rows": class_rows,
        "digest_pair_count": sum(item["run_class"] == "digest_fidelity" for item in class_rows),
        "positions_pair_count": sum(item["run_class"] == "positions_fidelity" for item in class_rows),
        "scene_rows": scene_rows,
    }


def analyze_orca_repeatability(
    records: Sequence[Mapping[str, Any]],
    input_root: pathlib.Path,
    result_root: pathlib.Path,
) -> dict[str, Any]:
    grouped = _group_records(
        records,
        lambda record: record["schedule"]["method"] == "orca"
        and record["schedule"]["run_class"] in ("positions_fidelity", "cadence_sensitivity"),
        lambda record: (record["schedule"]["scene_id"], record["schedule"]["cadence"]),
    )
    if len(grouped) != 26:
        raise AnalysisError("ORCA repeatability cohort inventory differs")
    rows: list[dict[str, Any]] = []
    for (scene_id, cadence), pair in sorted(grouped.items()):
        pair = sorted(pair, key=lambda item: item["schedule"]["repetition"])
        if len(pair) != 2 or [item["schedule"]["repetition"] for item in pair] != [1, 2]:
            raise AnalysisError(f"ORCA repetition inventory differs for {scene_id}/{cadence}")
        row: dict[str, Any] = {
            "scene_id": scene_id,
            "cadence": cadence,
            "first_sequence": pair[0]["schedule"]["sequence"],
            "second_sequence": pair[1]["schedule"]["sequence"],
            "processes_completed": all(item["outcome"] == "completed" for item in pair),
            "trace_bytes_equal": False,
            "semantic_summaries_equal": False,
            "gate_passed": False,
            "failure_reason": None,
        }
        if not row["processes_completed"]:
            row["failure_reason"] = "registered-process-failure"
            rows.append(row)
            continue
        row["trace_bytes_equal"] = _regular_files_equal(
            _artifact_path(result_root, pair[0], "trace.ndjson"),
            _artifact_path(result_root, pair[1], "trace.ndjson"),
            f"ORCA repeated traces for {scene_id}/{cadence}",
        )
        _first_summary, first_semantic = _orca_summary(input_root, result_root, pair[0])
        _second_summary, second_semantic = _orca_summary(input_root, result_root, pair[1])
        row["semantic_summaries_equal"] = _json_exactly_equal(
            first_semantic, second_semantic
        )
        row["gate_passed"] = row["trace_bytes_equal"] and row["semantic_summaries_equal"]
        if not row["gate_passed"]:
            row["failure_reason"] = "repeatability-mismatch"
        rows.append(row)
    return {
        "all_main_passed": all(
            item["gate_passed"] for item in rows if item["cadence"] == "main"
        ),
        "all_sensitivity_passed": all(
            item["gate_passed"]
            for item in rows
            if item["cadence"] == "orca-0.05s-sensitivity"
        ),
        "rows": rows,
    }


def bootstrap_index(metric_name: str, draw: int, position: int) -> int:
    """Machine-exact registered mission index for one bootstrap position."""

    if not isinstance(metric_name, str) or not metric_name:
        raise AnalysisError("bootstrap metric name is invalid")
    if type(draw) is not int or draw < 0 or draw >= 2**64:
        raise AnalysisError("bootstrap draw index is invalid")
    if type(position) is not int or position < 0 or position >= 20:
        raise AnalysisError("bootstrap position is invalid")
    digest = hashlib.sha256(
        BOOTSTRAP_DOMAIN
        + metric_name.encode("utf-8")
        + b"\0"
        + struct.pack(">Q", draw)
        + struct.pack(">I", position)
    ).digest()
    return int.from_bytes(digest[:8], "big") % 20


def percentile(values: Sequence[float], probability: float) -> float:
    if (
        not values
        or any(not math.isfinite(value) for value in values)
        or not 0.0 <= probability <= 1.0
    ):
        raise AnalysisError("percentile arguments are invalid")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    weight = position - lower
    try:
        result = math.fsum(
            ((1.0 - weight) * ordered[lower], weight * ordered[upper])
        )
    except OverflowError as error:
        raise AnalysisError("percentile interpolation overflowed") from error
    if not math.isfinite(result):
        raise AnalysisError("percentile interpolation is non-finite")
    return result


def _finite_arithmetic_mean(values: Sequence[float], label: str) -> float:
    if not values or any(not math.isfinite(value) for value in values):
        raise AnalysisError(f"{label} requires finite values")
    divisor = float(len(values))
    try:
        result = math.fsum(value / divisor for value in values)
    except OverflowError as error:
        raise AnalysisError(f"{label} overflowed") from error
    if not math.isfinite(result):
        raise AnalysisError(f"{label} is non-finite")
    return result


def _finite_difference_or_none(
    left: int | float, right: int | float
) -> float | None:
    try:
        result = float(left) - float(right)
    except (OverflowError, TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _positive_finite_ratio_or_none(
    numerator: float, denominator: float
) -> float | None:
    if (
        not math.isfinite(numerator)
        or not math.isfinite(denominator)
        or numerator <= 0.0
        or denominator <= 0.0
    ):
        return None
    try:
        result = numerator / denominator
    except (OverflowError, ZeroDivisionError):
        return None
    return result if math.isfinite(result) and result > 0.0 else None


def bootstrap_mean_interval(
    values: Sequence[float], metric_name: str, *, draws: int = BOOTSTRAP_DRAWS
) -> dict[str, Any]:
    if len(values) != 20 or any(not math.isfinite(value) for value in values):
        raise AnalysisError("open bootstrap requires twenty finite mission values")
    if type(draws) is not int or draws < 1:
        raise AnalysisError("bootstrap draw count must be positive")
    samples = [
        _finite_arithmetic_mean(
            [
                values[bootstrap_index(metric_name, draw, position)]
                for position in range(20)
            ],
            f"bootstrap draw {draw} for {metric_name}",
        )
        for draw in range(draws)
    ]
    return {
        "draws": draws,
        "estimate": _finite_arithmetic_mean(values, f"estimate for {metric_name}"),
        "lower_95": percentile(samples, 0.025),
        "metric": metric_name,
        "resampling_unit": "mission-within-one-fixed-open-world",
        "selection_rule": "SHA-256 hash-index-v1",
        "upper_95": percentile(samples, 0.975),
    }


def summarize_open_behavior_pairs(
    scene_pairs: Sequence[Mapping[str, Any]], *, draws: int = BOOTSTRAP_DRAWS
) -> dict[str, Any]:
    if len(scene_pairs) != 20:
        raise AnalysisError("open behavioral analysis requires twenty scene pairs")
    by_mission: dict[int, Mapping[str, Any]] = {}
    for pair in scene_pairs:
        match = re.fullmatch(r"orca-open-m(\d+)-n8", str(pair.get("scene_id")))
        if match is None:
            raise AnalysisError("open behavioral scene ID differs")
        mission = int(match.group(1))
        if mission in by_mission:
            raise AnalysisError("open behavioral scene IDs are duplicated")
        by_mission[mission] = pair
    if tuple(sorted(by_mission)) != OPEN_MISSIONS:
        raise AnalysisError("open behavioral mission order/inventory differs")
    ordered = [by_mission[mission] for mission in OPEN_MISSIONS]
    summaries: dict[str, Any] = {}
    for metric in BEHAVIOR_DIFFERENCE_METRICS:
        differences: list[float] = []
        missing: list[str] = []
        numeric_missing: list[str] = []
        for pair in ordered:
            if pair.get("estimable") is not True:
                missing.append(pair["scene_id"])
                continue
            dancers = pair["dancers"].get(metric)
            orca = pair["orca"].get(metric)
            if metric == "all_agents_completion":
                dancers = float(bool(dancers))
                orca = float(bool(orca))
            if type(dancers) not in (int, float) or type(orca) not in (int, float):
                missing.append(pair["scene_id"])
                continue
            difference = _finite_difference_or_none(dancers, orca)
            if difference is None:
                missing.append(pair["scene_id"])
                numeric_missing.append(pair["scene_id"])
                continue
            differences.append(difference)
        if missing:
            registered_failure = any(
                pair.get("estimable") is not True
                for pair in ordered
                if pair["scene_id"] in missing
            )
            if registered_failure and numeric_missing:
                status = "not_estimable_due_to_registered_failure_and_numeric_range"
            elif numeric_missing:
                status = "not_estimable_due_to_numeric_range"
            elif registered_failure:
                status = "not_estimable_due_to_registered_failure"
            else:
                status = "not_estimable_due_to_noncompletion"
            summary = {
                "estimable": False,
                "missing_registered_scenes": missing,
                "policy": "no complete-case aggregation",
                "status": status,
            }
            if numeric_missing:
                summary["numeric_range_scenes"] = numeric_missing
            summaries[metric] = summary
            continue
        interval = bootstrap_mean_interval(differences, metric, draws=draws)
        sorted_differences = sorted(differences)
        summaries[metric] = {
            "estimable": True,
            "difference_direction": "DANCeRS-minus-ORCA",
            "mission_differences": differences,
            "mean_difference": interval["estimate"],
            "median_difference": _finite_arithmetic_mean(
                sorted_differences[9:11], f"median for {metric}"
            ),
            "mean_difference_interval": interval,
        }
    return {
        "inference_scope": "fixed-input/fixed-open-world resampling sensitivity",
        "mission_count": 20,
        "metric_summaries": summaries,
    }


def _unique_record(
    records: Sequence[Mapping[str, Any]],
    *,
    scene_id: str,
    method: str,
    run_class: str,
    repetition: int | None = None,
    cadence: str = "main",
) -> Mapping[str, Any]:
    matches = [
        record
        for record in records
        if record["schedule"]["scene_id"] == scene_id
        and record["schedule"]["method"] == method
        and record["schedule"]["run_class"] == run_class
        and record["schedule"]["cadence"] == cadence
        and (repetition is None or record["schedule"]["repetition"] == repetition)
    ]
    if len(matches) != 1:
        raise AnalysisError(
            f"record lookup differs for {scene_id}/{method}/{run_class}/{cadence}"
        )
    return matches[0]


def analyze_behavior(
    records: Sequence[Mapping[str, Any]],
    input_root: pathlib.Path,
    result_root: pathlib.Path,
    dancers_fidelity: Mapping[str, Any],
    orca_repeatability: Mapping[str, Any],
    *,
    draws: int = BOOTSTRAP_DRAWS,
) -> dict[str, Any]:
    """Build gated canonical scene bundles and the registered reductions."""

    fidelity = {row["scene_id"]: row for row in dancers_fidelity["scene_rows"]}
    repeatability = {
        (row["scene_id"], row["cadence"]): row
        for row in orca_repeatability["rows"]
    }
    scene_templates: dict[str, Mapping[str, Any]] = {}
    for record in records:
        row = record["schedule"]
        if row["phase"] == "measured" and row["run_class"] == "positions_fidelity":
            scene_templates[row["scene_id"]] = row
    if len(scene_templates) != EXPECTED_SCENES:
        raise AnalysisError("behavioral scene inventory differs")

    attempt_outcomes: list[dict[str, Any]] = []
    for record in records:
        row = record["schedule"]
        if row["run_class"] not in ("positions_fidelity", "cadence_sensitivity"):
            continue
        failed = record["outcome"] != "completed"
        attempt_outcomes.append(
            {
                "agent_completion_fraction": 0.0 if failed else None,
                "all_agents_completion": False if failed else None,
                "cadence": row["cadence"],
                "continuous_metrics_available": False if failed else None,
                "fixed_work_verified": record["fixed_work_verified"],
                "method": row["method"],
                "outcome": record["outcome"],
                "policy": (
                    "failed designated positions attempt counts as completion zero"
                    if failed
                    else "successful attempt remains subject to its complete scene gate"
                ),
                "repetition": row["repetition"],
                "scene_id": row["scene_id"],
                "sequence": row["sequence"],
            }
        )

    dancers_cache: dict[str, dict[str, Any]] = {}
    orca_cache: dict[tuple[str, str], dict[str, Any]] = {}
    scene_pairs: list[dict[str, Any]] = []
    canonical_scene_bundles: list[dict[str, Any]] = []
    for scene_id, template in sorted(scene_templates.items()):
        dancers_gate = fidelity.get(scene_id)
        orca_gate = repeatability.get((scene_id, "main"))
        reasons: list[str] = []
        if not dancers_gate or dancers_gate["behavior_admissible"] is not True:
            reasons.append("dancers-fidelity-gate-failed")
        if not orca_gate or orca_gate["gate_passed"] is not True:
            reasons.append("orca-repeatability-gate-failed")
        pair: dict[str, Any] = {
            "estimable": not reasons,
            "failure_reasons": reasons,
            "robot_count": template["robot_count"],
            "scene_id": scene_id,
            "scene_set": template["scene_set"],
        }
        if not reasons:
            dancers_record = _unique_record(
                records,
                scene_id=scene_id,
                method="reference",
                run_class="positions_fidelity",
            )
            provenance = _dancers_provenance(result_root, dancers_record)
            dancers_result = analyze_trajectory(
                normalize_dancers_trace(
                    _artifact_path(result_root, dancers_record, "trace.ndjson"),
                    dancers_record["schedule"],
                    input_root,
                    provenance=provenance,
                )
            )
            orca_record = _unique_record(
                records,
                scene_id=scene_id,
                method="orca",
                run_class="positions_fidelity",
                repetition=1,
            )
            orca_summary, _semantic = _orca_summary(
                input_root, result_root, orca_record
            )
            orca_result = analyze_trajectory(
                normalize_orca_trace(
                    _artifact_path(result_root, orca_record, "trace.ndjson"),
                    orca_record["schedule"],
                    input_root,
                    expected_parameter_audit=orca_summary["parameter_audit"],
                    expected_goal_latches=orca_summary["goal_latches"],
                )
            )
            dancers_cache[scene_id] = dancers_result
            orca_cache[(scene_id, "main")] = orca_result
            pair["dancers"] = dancers_result["scene"]
            pair["orca"] = orca_result["scene"]
            canonical_scene_bundles.append(
                {
                    "dancers": dancers_result,
                    "orca": orca_result,
                    "scene_id": scene_id,
                }
            )
        else:
            pair["dancers"] = None
            pair["orca"] = None
        scene_pairs.append(pair)

    open_pairs = [pair for pair in scene_pairs if pair["scene_set"] == "open"]
    circle_pairs = sorted(
        (pair for pair in scene_pairs if pair["scene_set"] == "circle"),
        key=lambda pair: pair["robot_count"],
    )
    sensitivity_rows: list[dict[str, Any]] = []
    for pair in circle_pairs:
        scene_id = pair["scene_id"]
        main_gate = repeatability.get((scene_id, "main"))
        sensitivity_gate = repeatability.get((scene_id, "orca-0.05s-sensitivity"))
        row: dict[str, Any] = {
            "estimable": bool(
                main_gate
                and main_gate["gate_passed"]
                and sensitivity_gate
                and sensitivity_gate["gate_passed"]
            ),
            "robot_count": pair["robot_count"],
            "scene_id": scene_id,
        }
        if row["estimable"]:
            if (scene_id, "main") not in orca_cache:
                main_record = _unique_record(
                    records,
                    scene_id=scene_id,
                    method="orca",
                    run_class="positions_fidelity",
                    repetition=1,
                )
                main_summary, _ = _orca_summary(input_root, result_root, main_record)
                orca_cache[(scene_id, "main")] = analyze_trajectory(
                    normalize_orca_trace(
                        _artifact_path(result_root, main_record, "trace.ndjson"),
                        main_record["schedule"],
                        input_root,
                        expected_parameter_audit=main_summary["parameter_audit"],
                        expected_goal_latches=main_summary["goal_latches"],
                    )
                )
            sensitivity_record = _unique_record(
                records,
                scene_id=scene_id,
                method="orca",
                run_class="cadence_sensitivity",
                repetition=1,
                cadence="orca-0.05s-sensitivity",
            )
            sensitivity_summary, _ = _orca_summary(
                input_root, result_root, sensitivity_record
            )
            sensitivity_result = analyze_trajectory(
                normalize_orca_trace(
                    _artifact_path(result_root, sensitivity_record, "trace.ndjson"),
                    sensitivity_record["schedule"],
                    input_root,
                    expected_parameter_audit=sensitivity_summary["parameter_audit"],
                    expected_goal_latches=sensitivity_summary["goal_latches"],
                )
            )
            main_scene = orca_cache[(scene_id, "main")]["scene"]
            sensitivity_scene = sensitivity_result["scene"]
            differences: dict[str, float | None] = {}
            numeric_unavailable: list[str] = []
            for metric in BEHAVIOR_DIFFERENCE_METRICS:
                left, right = sensitivity_scene.get(metric), main_scene.get(metric)
                if type(left) is bool and type(right) is bool:
                    differences[metric] = float(left) - float(right)
                elif type(left) in (int, float) and type(right) in (int, float):
                    difference = _finite_difference_or_none(left, right)
                    differences[metric] = difference
                    if difference is None:
                        numeric_unavailable.append(metric)
                else:
                    differences[metric] = None
            row.update(
                {
                    "difference_direction": "ORCA-0.05s-minus-ORCA-main",
                    "differences": differences,
                    "main": main_scene,
                    "main_bundle": orca_cache[(scene_id, "main")],
                    "numeric_range_unavailable_metrics": numeric_unavailable,
                    "sensitivity": sensitivity_scene,
                    "sensitivity_bundle": sensitivity_result,
                }
            )
        else:
            row["failure_reason"] = "required-ORCA-repeatability-gate-failed"
        sensitivity_rows.append(row)
    return {
        "attempt_outcomes": attempt_outcomes,
        "canonical_scene_bundles": canonical_scene_bundles,
        "circle_descriptive_rows": circle_pairs,
        "open": summarize_open_behavior_pairs(open_pairs, draws=draws),
        "open_scene_pairs": open_pairs,
        "orca_cadence_sensitivity": sensitivity_rows,
        "scene_pairs": scene_pairs,
    }


def summarize_open_resource_rows(
    scene_rows: Sequence[Mapping[str, Any]], *, draws: int = BOOTSTRAP_DRAWS
) -> dict[str, Any]:
    by_mission: dict[int, Mapping[str, Any]] = {}
    for row in scene_rows:
        match = re.fullmatch(r"orca-open-m(\d+)-n8", str(row.get("scene_id")))
        if match:
            by_mission[int(match.group(1))] = row
    if tuple(sorted(by_mission)) != OPEN_MISSIONS:
        raise AnalysisError("open resource mission inventory differs")
    result: dict[str, Any] = {}
    for metric in RESOURCE_RATIO_METRICS:
        ratios: list[float] = []
        missing: list[str] = []
        numeric_missing: list[str] = []
        for mission in OPEN_MISSIONS:
            row = by_mission[mission]
            ratio_map = row.get("ratios")
            ratio = ratio_map.get(metric) if isinstance(ratio_map, Mapping) else None
            if (
                type(ratio) not in (int, float)
                or not math.isfinite(float(ratio))
                or float(ratio) <= 0.0
            ):
                missing.append(row["scene_id"])
                if (
                    row.get("dancers_ratio_status")
                    == "not_estimable_due_to_numeric_range"
                    or type(ratio) in (int, float)
                    and not math.isfinite(float(ratio))
                ):
                    numeric_missing.append(row["scene_id"])
            else:
                ratios.append(float(ratio))
        if missing:
            registered_missing = len(numeric_missing) != len(missing)
            if registered_missing and numeric_missing:
                status = "not_estimable_due_to_registered_failure_and_numeric_range"
            elif numeric_missing:
                status = "not_estimable_due_to_numeric_range"
            else:
                status = "not_estimable_due_to_registered_failure"
            summary = {
                "estimable": False,
                "missing_registered_scenes": missing,
                "status": status,
            }
            if numeric_missing:
                summary["numeric_range_scenes"] = numeric_missing
            result[metric] = summary
            continue
        logs = [math.log(value) for value in ratios]
        interval = bootstrap_mean_interval(logs, metric, draws=draws)
        estimate = math.exp(interval["estimate"])
        lower = math.exp(interval["lower_95"])
        upper = math.exp(interval["upper_95"])
        gate = (
            upper <= 1.05
            if metric == "dancers_rss_optimized_over_reference"
            else estimate > 1.05 and lower > 1.0
        )
        result[metric] = {
            "estimable": True,
            "geometric_mean_ratio": estimate,
            "lower_95": lower,
            "mission_ratios": ratios,
            "registered_gate_passed": gate,
            "upper_95": upper,
            "bootstrap_on_mean_log_ratio": interval,
        }
    return {"metric_summaries": result, "mission_count": 20}


def analyze_resources(
    records: Sequence[Mapping[str, Any]],
    input_root: pathlib.Path,
    result_root: pathlib.Path,
    dancers_fidelity: Mapping[str, Any],
    *,
    draws: int = BOOTSTRAP_DRAWS,
) -> dict[str, Any]:
    fidelity = {row["scene_id"]: row for row in dancers_fidelity["scene_rows"]}
    groups = _group_records(
        records,
        lambda record: record["schedule"]["phase"] == "measured"
        and record["schedule"]["run_class"] == "timing",
        lambda record: (record["schedule"]["scene_id"], record["schedule"]["method"]),
    )
    if len(groups) != EXPECTED_SCENES * 3 or any(len(group) != 3 for group in groups.values()):
        raise AnalysisError("timing repetition inventory differs")
    scene_rows: list[dict[str, Any]] = []
    for scene_id in sorted({key[0] for key in groups}):
        medians: dict[str, dict[str, float] | None] = {}
        process_failures: dict[str, list[int]] = {}
        resource_inadmissible: dict[str, list[int]] = {}
        template = groups[(scene_id, "reference")][0]["schedule"]
        for method in ("reference", "optimized", "orca"):
            group = sorted(groups[(scene_id, method)], key=lambda item: item["schedule"]["repetition"])
            if [item["schedule"]["repetition"] for item in group] != [1, 2, 3]:
                raise AnalysisError(f"timing repetitions differ for {scene_id}/{method}")
            failed = [
                item["schedule"]["sequence"]
                for item in group
                if item["outcome"] != "completed" or item["fixed_work_verified"] is not True
            ]
            process_failures[method] = failed
            resource_inadmissible[method] = []
            if failed:
                medians[method] = None
                continue
            if method == "orca":
                for item in group:
                    _orca_summary(input_root, result_root, item)
            measurements = [item["measurement"] for item in group]
            inadmissible = [
                item["schedule"]["sequence"]
                for item in group
                if item["measurement"]["total_cpu_seconds"] <= 0.0
                or item["measurement"]["peak_rss_mib"] <= 0.0
            ]
            if inadmissible:
                medians[method] = None
                resource_inadmissible[method] = inadmissible
                continue
            medians[method] = {
                metric: statistics.median(item[metric] for item in measurements)
                for metric in ("wall_seconds", "total_cpu_seconds", "peak_rss_mib")
            }
        dancers_admissible = (
            fidelity.get(scene_id, {}).get("resource_admissible") is True
            and medians["reference"] is not None
            and medians["optimized"] is not None
        )
        ratios: dict[str, float] | None = None
        dancers_ratio_status = "not_estimable_due_to_registered_failure"
        if dancers_admissible:
            reference = medians["reference"]
            optimized = medians["optimized"]
            assert reference is not None and optimized is not None
            candidates = {
                "dancers_cpu_speedup": _positive_finite_ratio_or_none(
                    reference["total_cpu_seconds"],
                    optimized["total_cpu_seconds"],
                ),
                "dancers_rss_optimized_over_reference": (
                    _positive_finite_ratio_or_none(
                        optimized["peak_rss_mib"], reference["peak_rss_mib"]
                    )
                ),
                "dancers_wall_speedup": _positive_finite_ratio_or_none(
                    reference["wall_seconds"], optimized["wall_seconds"]
                ),
            }
            if all(value is not None for value in candidates.values()):
                ratios = {
                    key: float(value) for key, value in candidates.items()
                }
                dancers_ratio_status = "estimable"
            else:
                dancers_ratio_status = "not_estimable_due_to_numeric_range"
        orca_context: dict[str, Any] | None = None
        orca_context_status = "not_estimable_due_to_registered_failure"
        if medians["orca"] is not None:
            orca = medians["orca"]
            assert orca is not None
            throughput = _positive_finite_ratio_or_none(
                float(template["robot_count"] * template["steps"]),
                orca["total_cpu_seconds"],
            )
            orca_context = {
                "agent_native_steps_per_cpu_second": throughput,
                "medians": orca,
            }
            orca_context_status = (
                "estimable"
                if throughput is not None
                else "throughput_not_estimable_due_to_numeric_range"
            )
        scene_rows.append(
            {
                "dancers_medians": {
                    "optimized": medians["optimized"],
                    "reference": medians["reference"],
                },
                "dancers_ratio_status": dancers_ratio_status,
                "orca_context": orca_context,
                "orca_context_status": orca_context_status,
                "process_failure_sequences": process_failures,
                "ratios": ratios,
                "resource_inadmissible_sequences": resource_inadmissible,
                "robot_count": template["robot_count"],
                "scene_id": scene_id,
                "scene_set": template["scene_set"],
            }
        )
    open_rows = [row for row in scene_rows if row["scene_set"] == "open"]
    circle_rows = sorted(
        (row for row in scene_rows if row["scene_set"] == "circle"),
        key=lambda row: row["robot_count"],
    )
    return {
        "circle_descriptive_rows": circle_rows,
        "open": summarize_open_resource_rows(open_rows, draws=draws),
        "scene_rows": scene_rows,
    }


def analyze_session(
    input_root: pathlib.Path,
    result_root: pathlib.Path,
    *,
    expected_input_manifest_sha256: str,
    expected_schedule_sha256: str,
    expected_plan_sha256: str,
    expected_authorization_manifest_sha256: str,
    expected_admission_manifest_sha256: str,
    bootstrap_draws: int = BOOTSTRAP_DRAWS,
) -> dict[str, Any]:
    """Authenticate and analyze one sealed, complete prospective session."""

    if bootstrap_draws != BOOTSTRAP_DRAWS:
        raise AnalysisError("official analysis requires exactly 100000 bootstrap draws")
    session = read_session(
        input_root,
        result_root,
        expected_input_manifest_sha256=expected_input_manifest_sha256,
        expected_schedule_sha256=expected_schedule_sha256,
        expected_plan_sha256=expected_plan_sha256,
        expected_authorization_manifest_sha256=expected_authorization_manifest_sha256,
        expected_admission_manifest_sha256=expected_admission_manifest_sha256,
    )
    for record in session.records:
        if record["schedule"]["method"] in ("reference", "optimized"):
            _dancers_provenance(result_root, record)
    dancers_fidelity = analyze_dancers_fidelity(session.records, result_root)
    orca_repeatability = analyze_orca_repeatability(
        session.records, input_root, result_root
    )
    behavior = analyze_behavior(
        session.records,
        input_root,
        result_root,
        dancers_fidelity,
        orca_repeatability,
        draws=bootstrap_draws,
    )
    resources = analyze_resources(
        session.records,
        input_root,
        result_root,
        dancers_fidelity,
        draws=bootstrap_draws,
    )
    result = {
        "analysis_contract": analysis_contract(),
        "behavior": behavior,
        "dancers_fidelity": dancers_fidelity,
        "integrity": {
            "admission_manifest_sha256": expected_admission_manifest_sha256,
            "authorization_manifest_sha256": expected_authorization_manifest_sha256,
            "input_manifest_sha256": expected_input_manifest_sha256,
            "plan_sha256": expected_plan_sha256,
            "schedule_sha256": expected_schedule_sha256,
            "sealed_session_valid": True,
        },
        "orca_repeatability": orca_repeatability,
        "resources": resources,
        "schema_version": ANALYSIS_SCHEMA,
        "study_id": STUDY_ID,
    }
    return _require_finite_json(result, "official analysis result")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    contract = commands.add_parser("check-contract", help="authenticate frozen inputs")
    contract.add_argument("--input-root", type=pathlib.Path, required=True)
    contract.add_argument("--build-capsule-root", type=pathlib.Path, required=True)
    contract.add_argument(
        "--expected-input-manifest-sha256", default=FROZEN_INPUT_MANIFEST_SHA256
    )
    contract.add_argument("--expected-schedule-sha256", default=FROZEN_SCHEDULE_SHA256)
    contract.add_argument(
        "--expected-build-capsule-sha256", default=FROZEN_BUILD_CAPSULE_SHA256
    )
    analyze = commands.add_parser("analyze", help="analyze a sealed result root")
    analyze.add_argument("--input-root", type=pathlib.Path, required=True)
    analyze.add_argument("--result-root", type=pathlib.Path, required=True)
    analyze.add_argument(
        "--expected-input-manifest-sha256", default=FROZEN_INPUT_MANIFEST_SHA256
    )
    analyze.add_argument("--expected-schedule-sha256", default=FROZEN_SCHEDULE_SHA256)
    analyze.add_argument("--expected-plan-sha256", required=True)
    analyze.add_argument("--expected-authorization-manifest-sha256", required=True)
    analyze.add_argument("--expected-admission-manifest-sha256", required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "check-contract":
            value = check_frozen_contract(
                args.input_root,
                args.build_capsule_root,
                expected_input_manifest_sha256=args.expected_input_manifest_sha256,
                expected_schedule_sha256=args.expected_schedule_sha256,
                expected_build_capsule_sha256=args.expected_build_capsule_sha256,
            )
        else:
            value = analyze_session(
                args.input_root,
                args.result_root,
                expected_input_manifest_sha256=args.expected_input_manifest_sha256,
                expected_schedule_sha256=args.expected_schedule_sha256,
                expected_plan_sha256=args.expected_plan_sha256,
                expected_authorization_manifest_sha256=(
                    args.expected_authorization_manifest_sha256
                ),
                expected_admission_manifest_sha256=args.expected_admission_manifest_sha256,
            )
        sys.stdout.buffer.write(canonical_json_bytes(value, pretty=True))
        return 0
    except (
        AnalysisError,
        OSError,
        ValueError,
        KeyError,
        trace_compare.TraceFormatError,
    ) as error:
        print(f"DANCeRS--ORCA analysis failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
