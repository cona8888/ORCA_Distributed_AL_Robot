#!/usr/bin/env python3
"""Deterministic, non-authorizing audit of the frozen visual N=8 cohort.

The analyzer is deliberately read-only until explicit report publication.  It
does not import the visual controller, execute Git, or launch a child process.
The only executable dependencies are the plan-pinned trace comparator and
replay viewer, each compiled from source bytes only after its exact SHA-256
has been authenticated.
"""

from __future__ import annotations

import argparse
import ast
import builtins
import hashlib
import json
import math
import os
import re
import stat
import struct
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import MappingProxyType, ModuleType
from typing import Any, Callable, Mapping, Sequence


SCHEMA_VERSION = "dancers-simulator-visual-cohort-analysis-v1"
ANALYSIS_ID = "visual-diagnostic-calibration-v2-n8-seeds1-10"
PLAN_RELATIVE_PATH = (
    "comparison/plans/simulator-demo/v1/"
    "visual-diagnostic-calibration-v2/plan.json"
)
PLAN_SHA256 = "9c473ce64722e810fbf6684f06b0195f19e052990551b734e6e2cc9014745a8d"
RESULTS_RELATIVE_ROOT = "results/simulator-demo/v1/visual-diagnostic/calibration-v2"
PROTECTED_VISUAL_RESULTS_RELATIVE_ROOT = (
    "results/simulator-demo/v1/visual-diagnostic"
)
EPOCH_LEASE_RELATIVE_PATH = (
    "results/simulator-demo/v1/visual-diagnostic/"
    ".calibration-v2-execution.lease"
)
PLAN_ID = "visual-diagnostic-calibration-v2"
PLAN_SCHEMA = "dancers-simulator-visual-plan-v2"
RESULT_SCHEMA = "dancers-simulator-visual-diagnostic-result-v2"
EXECUTION_EPOCH_ID = "simulator-demo-v1-visual-calibration-v2-recovery-1"
CONTINUATION_EXECUTION_CHECKPOINT = (
    "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea"
)
SEED01_EXECUTION_CHECKPOINT_BY_FAMILY = MappingProxyType(
    {
        "fractal_high": "c05c291f5e8c8266923a4ea2872a7c5d8180c531",
        "fractal_low": "c05c291f5e8c8266923a4ea2872a7c5d8180c531",
        "fractal_medium": "c05c291f5e8c8266923a4ea2872a7c5d8180c531",
        "open": "c33833cf5dfc5b9cf38f68f2f54df2b73be40379",
        "perfect_maze": "c05c291f5e8c8266923a4ea2872a7c5d8180c531",
    }
)
EXECUTION_CHECKPOINT_BY_SCENE = MappingProxyType({
    "fractal_high-n8-seed01": "c05c291f5e8c8266923a4ea2872a7c5d8180c531",
    "fractal_high-n8-seed02": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_high-n8-seed03": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_high-n8-seed04": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_high-n8-seed05": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_high-n8-seed06": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_high-n8-seed07": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_high-n8-seed08": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_high-n8-seed09": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_high-n8-seed10": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_low-n8-seed01": "c05c291f5e8c8266923a4ea2872a7c5d8180c531",
    "fractal_low-n8-seed02": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_low-n8-seed03": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_low-n8-seed04": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_low-n8-seed05": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_low-n8-seed06": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_low-n8-seed07": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_low-n8-seed08": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_low-n8-seed09": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_low-n8-seed10": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_medium-n8-seed01": "c05c291f5e8c8266923a4ea2872a7c5d8180c531",
    "fractal_medium-n8-seed02": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_medium-n8-seed03": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_medium-n8-seed04": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_medium-n8-seed05": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_medium-n8-seed06": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_medium-n8-seed07": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_medium-n8-seed08": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_medium-n8-seed09": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "fractal_medium-n8-seed10": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "open-n8-seed01": "c33833cf5dfc5b9cf38f68f2f54df2b73be40379",
    "open-n8-seed02": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "open-n8-seed03": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "open-n8-seed04": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "open-n8-seed05": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "open-n8-seed06": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "open-n8-seed07": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "open-n8-seed08": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "open-n8-seed09": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "open-n8-seed10": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "perfect_maze-n8-seed01": "c05c291f5e8c8266923a4ea2872a7c5d8180c531",
    "perfect_maze-n8-seed02": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "perfect_maze-n8-seed03": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "perfect_maze-n8-seed04": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "perfect_maze-n8-seed05": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "perfect_maze-n8-seed06": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "perfect_maze-n8-seed07": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "perfect_maze-n8-seed08": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "perfect_maze-n8-seed09": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
    "perfect_maze-n8-seed10": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
})
TRACK_ID = "simulator-demo-v1-kinematic-visual-diagnostic"
TASK_PREDICATE_ID = "fixed-horizon-safe-navigation-v1"
ROBOT_COUNT = 8
TIMESTEPS = 100
FRAMES_PER_PAIR = 200
SIMULATED_DURATION_SECONDS = 10.0
MAX_SPEED_METRES_PER_SECOND = 2.0
ROBOT_RADIUS_METRES = 1.0
FAMILIES = (
    "open",
    "perfect_maze",
    "fractal_low",
    "fractal_medium",
    "fractal_high",
)
SEEDS = tuple(range(1, 11))
METHODS = ("reference", "optimized")
EXPECTED_SCENE_COUNT = len(FAMILIES) * len(SEEDS)
EXPECTED_FULL_PLAN_SCENES = 250
EXPECTED_FULL_PLAN_ATTEMPTS = 500
EXPECTED_PLAN_COUNTS = (8, 16, 32, 64, 128)
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_TRACE_LINE_BYTES = 64 * 1024 * 1024
ANALYZER_RELATIVE_PATH = "scripts/comparison/analyze_visual_cohort_v1.py"
HEX64_RE = re.compile(r"^[0-9a-f]{16}$")
FORBIDDEN_RESOURCE_KEY_RE = re.compile(r"(?:^|_)(?:wall|cpu|rss)(?:_|$)")
FRAME_KEYS = frozenset(
    {
        "metrics",
        "phase",
        "rng_state",
        "robots",
        "simulated_time_seconds",
        "simulator_state",
        "tick",
        "type",
    }
)
ROBOT_KEYS = frozenset(
    {
        "bearing",
        "current_waypoint",
        "final_goal",
        "goal_reached",
        "radius_m",
        "rid",
        "state",
        "state_hex",
    }
)
FRAME_METRIC_KEYS = frozenset(
    {
        "all_goals_reached",
        "cumulative_invalid_geometry_evaluations",
        "cumulative_obstacle_collision_exposure_seconds",
        "cumulative_pair_collision_exposure_seconds",
        "frame_min_obstacle_clearance_m",
        "frame_min_pair_clearance_m",
        "goals_reached",
        "obstacle_collisions",
        "pair_collisions",
        "robot_count",
        "updated_this_frame",
    }
)
RNG_STATE_KEYS = frozenset(
    {"integer_mt19937", "normal_mt19937", "uniform_mt19937"}
)
SIMULATOR_STATE_KEYS = frozenset(
    {
        "iterations",
        "last_created_robots",
        "last_junction_creation_clock",
        "mean_number_of_neighbours_hex",
        "mean_number_of_neighbours_time_hex",
        "next_robot_id",
        "robot_group_clock",
        "run_flag",
        "simulation_mode",
    }
)

REQUIRED_DIRECTORIES = (".", "optimized", "reference")
METHOD_ARTIFACT_NAMES = (
    "metadata.json",
    "stderr.txt",
    "stdout.txt",
    "summary.json",
    "trace.jsonl",
)
REQUIRED_FILES = (
    "config.yaml",
    "frozen_plan.json",
    "manifest.json",
    "preflight.json",
    "replay.html",
    *(f"optimized/{name}" for name in METHOD_ARTIFACT_NAMES),
    *(f"reference/{name}" for name in METHOD_ARTIFACT_NAMES),
)
REQUIRED_REPLAY_BADGES = (
    "DIAGNOSTIC REPLAY",
    "NOT LIVE SOLVER TIMING",
    "RENDERING EXCLUDED FROM COMPUTE",
)
PROTECTED_CONFIG_KEYS = frozenset(
    {
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
)
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
    "run_valid",
    "final_tick",
    "final_robot_count",
    "maximum_robot_count",
    "frames_recorded",
    "simulated_time_seconds",
    "trace_detail",
)
PAPER_METRIC_FIELDS = frozenset(
    {
        "consensus_reached",
        "final_connectivity",
        "final_decision",
        "final_heading_error",
        "final_iterations",
        "final_position_error",
    }
)
PREFLIGHT_KEYS = frozenset(
    {
        "artifacts",
        "attempt_count",
        "build",
        "compute_claim",
        "config_cli_argument",
        "config_cli_path_policy",
        "contract",
        "diagnostic_only",
        "environment",
        "epoch_state",
        "execution_checkpoint_commit",
        "execution_epoch_id",
        "execution_lease",
        "failure_policy",
        "full_crossing_verified",
        "git_remote_count",
        "output_schemas",
        "paper_replication_evidence",
        "plan_id",
        "plan_path",
        "plan_sha256",
        "recovery",
        "run_id_prefix",
        "run_ids",
        "scene_count",
        "schema_version",
        "selected_output_relpath",
        "selected_scene_id",
        "timing_unlock",
        "viewer_passivity",
    }
)
METADATA_KEYS = frozenset(
    {
        "algorithm_revision",
        "binary_sha256",
        "build_manifest_sha256",
        "common_patch_full_index_diff_sha256",
        "common_patch_sha256",
        "compute_claim",
        "config_cli_argument",
        "config_cli_path_policy",
        "config_path",
        "config_sha256",
        "contract_id",
        "contract_sha256",
        "controller_sha256",
        "diagnostic_only",
        "environment_manifest_sha256",
        "execution_epoch_id",
        "failure_policy",
        "implementation_id",
        "map_path",
        "map_sha256",
        "omp_threads",
        "output_schemas",
        "phase",
        "pixi_lock_sha256",
        "plan_id",
        "plan_sha256",
        "recovery",
        "robot_count",
        "run_class",
        "run_id",
        "run_id_prefix",
        "scenario_path",
        "scenario_sha256",
        "scene_id",
        "seed",
        "source_head_revision",
        "timestep_seconds",
        "timesteps",
        "timing_unlock",
        "trace_compare_sha256",
        "track_id",
        "viewer_sha256",
    }
)
RESOLVED_CONFIG_KEYS = frozenset(
    {
        "communication_radius_m",
        "communications_failure_rate",
        "config_file",
        "configured_robot_count",
        "damping",
        "display",
        "factorgraph_layers",
        "gbp_external_iterations",
        "max_speed_mps",
        "max_timesteps",
        "obstacle_distance_cap_pixels",
        "obstacle_field_height",
        "obstacle_field_width",
        "obstacle_file",
        "obstacles_present",
        "planning_horizon_seconds",
        "rng_seed",
        "robot_model",
        "robot_radius_m",
        "scenario",
        "scenario_file",
        "screen_size_px",
        "simulation_mode",
        "simulation_name",
        "symmetric_interrobot_factors",
        "timestep_seconds",
        "turning_radius_m",
        "world_size_m",
    }
)
TRACE_HEADER_KEYS = frozenset(
    {"metadata", "observer", "resolved_config", "schema_version", "type"}
)
OBSERVER_KEYS = frozenset(
    {
        "digest_algorithm",
        "digest_boundary_reuse_policy",
        "effective_raster_resize",
        "future_state_scope",
        "message_checksum_scope",
        "numeric_identity",
        "obstacle_collision_model",
        "planner_distance_field",
        "robot_collision_model",
        "strict_provenance_required",
        "timing_eligible",
        "trace_detail",
        "visualization",
    }
)
SUMMARY_KEYS = frozenset(
    {
        *SUMMARY_PARITY_FIELDS,
        "metadata",
        "paper_metrics",
        "resolved_config",
        "schema_version",
        "trace_path",
    }
)
MANIFEST_KEYS = frozenset(
    {
        "compute_claim",
        "config_cli_argument",
        "config_cli_path_policy",
        "contract_id",
        "contract_sha256",
        "diagnostic_only",
        "execution_epoch_id",
        "execution_lease",
        "failure_policy",
        "methods",
        "output_schemas",
        "pair_validation",
        "paper_replication_evidence",
        "phase",
        "plan_id",
        "plan_sha256",
        "process_policy",
        "recovery",
        "rendering_excluded_from_compute",
        "replay",
        "run_class",
        "run_id_prefix",
        "run_ids",
        "scene",
        "schema_version",
        "status",
        "timing_unlock",
        "track_id",
    }
)
MANIFEST_SCENE_KEYS = frozenset(
    {
        "config",
        "config_cli_argument",
        "environment_descriptors",
        "family",
        "map",
        "robot_count",
        "run_ids",
        "scenario",
        "scene_id",
        "seed",
        "simulated_duration_seconds",
        "timestep_seconds",
        "timesteps",
    }
)
PAIR_DIAGNOSTIC_KEYS = frozenset(
    {
        "aligned_frame_count",
        "aligned_robot_frame_count",
        "all_goals_reached",
        "discordant_robot_frame_count",
        "first_all_goals_tick",
        "goals_reached",
        "goals_total",
        "max_abs_position_delta_m",
        "max_abs_wrapped_heading_delta_rad",
        "mean_path_length_m_per_robot",
        "minimum_obstacle_clearance_m",
        "minimum_pair_clearance_m",
        "obstacle_collision_events",
        "obstacle_collision_exposure_seconds",
        "pair_collision_events",
        "pair_collision_exposure_seconds",
        "paper_metrics_equal",
        "run_valid",
        "state_hex_equal",
        "summary_parity_fields_equal",
        "total_path_length_m",
    }
)
EXPECTED_OBSERVER = {
    "digest_algorithm": (
        "SHA-256 over robot-semantic-digest-v2 canonical typed binary encoding"
    ),
    "digest_boundary_reuse_policy": {
        "eligibility": (
            "digest detail, headless execution, SCENARIO=file, immediately "
            "adjacent post_update(t) to pre_update(t), exact ordered (rid, "
            "Robot*, shared-owner) membership, and unchanged recorder "
            "goal-state revision"
        ),
        "fallback": (
            "recompute every robot digest after any membership, deletion, "
            "replacement, goal-state, phase, tick, scenario, display, or "
            "trace-detail mismatch"
        ),
        "scope": (
            "robot digest objects and their per-robot nonfinite counts only; "
            "frame RNG, simulator state, geometry and all other metrics are "
            "always recomputed"
        ),
    },
    "effective_raster_resize": (
        "explicit nearest neighbour: "
        "source[floor(target_index*source_size/target_size)]"
    ),
    "future_state_scope": (
        "all implementation-neutral path-planning robot, waypoint, graph-node, "
        "live-message, node-counter, simulator-bookkeeping and RNG state used "
        "by the frozen static comparison missions"
    ),
    "message_checksum_scope": (
        "robot-semantic-digest-v2 directly encodes external inbox/outbox/"
        "temporary messages, variable/factor priors and local mailboxes, and "
        "factor last-outboxes; local mailboxes exclude moved-from external "
        "staging entries"
    ),
    "numeric_identity": (
        "IEEE-754 binary64 identity: fixed-width big-endian bits in "
        "robot-semantic-digest-v2 and 16-digit hexadecimal bit patterns in "
        "full traces"
    ),
    "obstacle_collision_model": (
        "independent exact signed distance to the closed occupied raster-square "
        "union; exact swept-closed-disc collision classification with contact "
        "counted; minimum clearance and exposure sampled at half a raster pixel"
    ),
    "planner_distance_field": (
        "capped 3-4 chamfer transform; out-of-bounds occupied"
    ),
    "robot_collision_model": (
        "exact minimum separation and overlap duration of linearly swept discs "
        "per timestep"
    ),
    "strict_provenance_required": True,
    "timing_eligible": False,
    "trace_detail": "positions",
    "visualization": "post-run synchronized replay",
}


class AnalysisError(RuntimeError):
    """The frozen plan, a registered dependency, or publication is invalid."""


class SceneError(RuntimeError):
    """One present scene cannot be authenticated."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _DuplicateKey(ValueError):
    pass


@dataclass(frozen=True)
class FileSnapshot:
    relative_path: str
    data: bytes
    bytes: int
    sha256: str
    mode: int


@dataclass(frozen=True)
class PlanContext:
    repo_root: Path
    plan: dict[str, Any]
    plan_bytes: bytes
    target_scenes: tuple[dict[str, Any], ...]
    dependencies: tuple[dict[str, Any], ...]
    build_manifest_bytes: bytes
    trace_compare_path: Path
    trace_compare_bytes: bytes
    trace_compare_sha256: str
    viewer_path: Path
    viewer_bytes: bytes
    viewer_sha256: str


@dataclass(frozen=True)
class TraceComparison:
    equal: bool
    frames_compared: int
    left_provenance: dict[str, Any]
    right_provenance: dict[str, Any]


TraceComparator = Callable[[Path, Path], TraceComparison]
ViewerBuilder = Callable[..., Any]


@dataclass(frozen=True)
class TraceComparatorRuntime:
    module: ModuleType
    compare: TraceComparator


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
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


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite_number(value: Any) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _finite_float(value: Any) -> bool:
    return isinstance(value, float) and math.isfinite(value)


def _json_exact(observed: Any, expected: Any) -> bool:
    if type(observed) is not type(expected):
        return False
    if isinstance(expected, dict):
        return set(observed) == set(expected) and all(
            _json_exact(observed[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return len(observed) == len(expected) and all(
            _json_exact(left, right)
            for left, right in zip(observed, expected)
        )
    return observed == expected


def _expect_json_exact(
    observed: Any,
    expected: Any,
    code: str,
    *,
    scene: bool = False,
) -> None:
    _expect(_json_exact(observed, expected), code, scene=scene)


def _validate_execution_checkpoint_registry() -> None:
    expected_seed01 = {
        "fractal_high": "c05c291f5e8c8266923a4ea2872a7c5d8180c531",
        "fractal_low": "c05c291f5e8c8266923a4ea2872a7c5d8180c531",
        "fractal_medium": "c05c291f5e8c8266923a4ea2872a7c5d8180c531",
        "open": "c33833cf5dfc5b9cf38f68f2f54df2b73be40379",
        "perfect_maze": "c05c291f5e8c8266923a4ea2872a7c5d8180c531",
    }
    _expect_json_exact(
        CONTINUATION_EXECUTION_CHECKPOINT,
        "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea",
        "execution-checkpoint-registry:continuation-mismatch",
    )
    _expect(
        isinstance(SEED01_EXECUTION_CHECKPOINT_BY_FAMILY, Mapping),
        "execution-checkpoint-registry:seed01-type-invalid",
    )
    _expect_json_exact(
        dict(SEED01_EXECUTION_CHECKPOINT_BY_FAMILY),
        expected_seed01,
        "execution-checkpoint-registry:seed01-mismatch",
    )
    expected_scene_mapping = {
        f"{family}-n8-seed{seed:02d}": (
            expected_seed01[family]
            if seed == 1
            else "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea"
        )
        for family in FAMILIES
        for seed in SEEDS
    }
    _expect(
        isinstance(EXECUTION_CHECKPOINT_BY_SCENE, Mapping),
        "execution-checkpoint-registry:scene-map-type-invalid",
    )
    _expect_json_exact(
        dict(EXECUTION_CHECKPOINT_BY_SCENE),
        expected_scene_mapping,
        "execution-checkpoint-registry:scene-map-mismatch",
    )


def _json_depth(value: Any) -> int:
    if isinstance(value, dict):
        return 1 + max((_json_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_json_depth(item) for item in value), default=0)
    return 1


def _assert_no_resource_claim_keys(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if FORBIDDEN_RESOURCE_KEY_RE.search(key):
                raise SceneError(f"manifest:forbidden-resource-key:{path}.{key}")
            _assert_no_resource_claim_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_resource_claim_keys(item, f"{path}[{index}]")


def _safe_relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise AnalysisError(f"{label}:path-invalid")
    pure = PurePosixPath(value)
    if pure.is_absolute() or ".." in pure.parts or "." in pure.parts:
        raise AnalysisError(f"{label}:path-escapes-repository")
    if pure.as_posix() != value:
        raise AnalysisError(f"{label}:path-not-canonical")
    return value


def _lexical_absolute(path: Path | str) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _canonical_repository_root(path: Path | str) -> Path:
    repository = _lexical_absolute(path)
    try:
        metadata = os.lstat(repository)
        resolved = repository.resolve(strict=True)
    except OSError as error:
        raise AnalysisError(f"repo-root:invalid:{error.errno}") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or resolved != repository
    ):
        raise AnalysisError("repo-root:symlink-or-noncanonical")
    return repository


def _require_no_symlink_components(
    repository: Path,
    relative_path: str,
    label: str,
    *,
    allow_missing: bool,
) -> None:
    relative = PurePosixPath(_safe_relative_path(relative_path, label))
    current = repository
    for index, component in enumerate(relative.parts):
        current = current / component
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            if allow_missing:
                return
            raise AnalysisError(f"{label}:component-missing")
        except OSError as error:
            raise AnalysisError(
                f"{label}:component-stat-failed:{error.errno}"
            ) from error
        if stat.S_ISLNK(metadata.st_mode):
            raise AnalysisError(f"{label}:symlink-component")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(
            metadata.st_mode
        ):
            raise AnalysisError(f"{label}:ancestor-not-directory")


def _yaml_scalar(value: bool | int | float | str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return json.dumps(value, ensure_ascii=False)


def _replace_top_level_config(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"(?m)^{re.escape(key)}\s*:[^\r\n]*$")
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise AnalysisError(f"config-template:{key}-cardinality-mismatch")
    return pattern.sub(f"{key}: {value}", text, count=1)


def _expected_config_bytes(
    context: PlanContext,
    scene: Mapping[str, Any],
    scene_root: Path,
) -> bytes:
    template_record = context.plan["artifacts"]["config_template"]
    template_path = context.repo_root / template_record["path"]
    template_bytes = _open_regular_file(
        template_path,
        MAX_JSON_BYTES,
        "config-template",
    )
    if _sha256(template_bytes) != template_record["sha256"]:
        raise AnalysisError("config-template:sha256-changed-after-plan-audit")
    try:
        text = template_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise AnalysisError("config-template:utf8-invalid") from error
    for key in PROTECTED_CONFIG_KEYS:
        count = len(
            re.findall(rf"(?m)^{re.escape(key)}\s*:[^\r\n]*$", text)
        )
        if count != 1:
            raise AnalysisError(f"config-template:{key}-cardinality-mismatch")
    map_path = context.repo_root / scene["map"]["path"]
    scenario_path = context.repo_root / scene["scenario"]["path"]
    replacements: dict[str, bool | int | float | str] = {
        "SIMULATION_NAME": scene["scene_id"],
        "LOG_RESULTS": True,
        "ASSETS_DIR": str(map_path.parent),
        "OBSTACLE_FILE": map_path.name,
        "SCENARIO": "file",
        "SCENARIO_FILE": str(scenario_path),
        "NUM_ROBOTS": ROBOT_COUNT,
        "RNG_SEED": scene["seed"],
        "MAX_TIME": TIMESTEPS,
        "TIMESTEP": 0.1,
        "DISPLAY": 0,
        "DRAW_INTERROBOT": 0,
        "DRAW_PATH": 0,
        "DRAW_TRAJ": 0,
        "DRAW_WAYPOINTS": 0,
    }
    for key in sorted(replacements):
        text = _replace_top_level_config(
            text,
            key,
            _yaml_scalar(replacements[key]),
        )
    output_value = _yaml_scalar(str(scene_root / "legacy-output.json"))
    if re.search(r"(?m)^OUTPUT_FILE\s*:", text):
        text = _replace_top_level_config(text, "OUTPUT_FILE", output_value)
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += f"OUTPUT_FILE: {output_value}\n"
    return text.encode("utf-8")


def _open_regular_file(path: Path, maximum_bytes: int, label: str) -> bytes:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise AnalysisError(f"{label}:open-failed:{error.errno}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise AnalysisError(f"{label}:not-regular-file")
        if metadata.st_size < 0 or metadata.st_size > maximum_bytes:
            raise AnalysisError(f"{label}:file-size-out-of-bounds")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise AnalysisError(f"{label}:short-read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise AnalysisError(f"{label}:grew-during-read")
        after = os.fstat(descriptor)
        if (
            after.st_dev != metadata.st_dev
            or after.st_ino != metadata.st_ino
            or after.st_size != metadata.st_size
            or after.st_mtime_ns != metadata.st_mtime_ns
        ):
            raise AnalysisError(f"{label}:changed-during-read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _parse_canonical_json(
    data: bytes,
    label: str,
    *,
    scene: bool = False,
    require_canonical: bool = True,
) -> dict[str, Any]:
    error_type: type[RuntimeError] = SceneError if scene else AnalysisError
    if len(data) > MAX_JSON_BYTES:
        raise error_type(f"{label}:json-size-out-of-bounds")
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_no_duplicate_object,
            parse_constant=_reject_json_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ) as error:
        raise error_type(f"{label}:json-invalid") from error
    if not isinstance(value, dict):
        raise error_type(f"{label}:json-root-not-object")
    try:
        depth = _json_depth(value)
    except RecursionError as error:
        raise error_type(f"{label}:json-depth-out-of-bounds") from error
    if depth > 128:
        raise error_type(f"{label}:json-depth-out-of-bounds")
    try:
        canonical = canonical_json_bytes(value)
    except (TypeError, ValueError, RecursionError) as error:
        raise error_type(f"{label}:json-not-canonicalizable") from error
    if require_canonical and canonical != data:
        raise error_type(f"{label}:json-not-canonical")
    return value


def _expect(condition: bool, code: str, *, scene: bool = False) -> None:
    if not condition:
        if scene:
            raise SceneError(code)
        raise AnalysisError(code)


def _registered_file(
    repo_root: Path,
    record: Mapping[str, Any],
    label: str,
) -> tuple[dict[str, Any], bytes]:
    path_text = _safe_relative_path(record.get("path"), label)
    _require_no_symlink_components(
        repo_root,
        path_text,
        label,
        allow_missing=False,
    )
    expected_sha = record.get("sha256")
    if not isinstance(expected_sha, str) or len(expected_sha) != 64:
        raise AnalysisError(f"{label}:sha256-invalid")
    data = _open_regular_file(
        repo_root / path_text,
        MAX_FILE_BYTES,
        label,
    )
    observed_sha = _sha256(data)
    if observed_sha != expected_sha:
        raise AnalysisError(f"{label}:sha256-mismatch")
    return (
        {
            "bytes": len(data),
            "label": label,
            "path": path_text,
            "sha256": observed_sha,
        },
        data,
    )


def _validate_plan(
    repo_root: Path,
    plan_path: Path,
) -> PlanContext:
    _validate_execution_checkpoint_registry()
    plan_bytes = _open_regular_file(
        plan_path,
        MAX_JSON_BYTES,
        "frozen-plan",
    )
    _expect(_sha256(plan_bytes) == PLAN_SHA256, "frozen-plan:sha256-mismatch")
    plan = _parse_canonical_json(plan_bytes, "frozen-plan")
    for key, expected in (
        ("schema_version", PLAN_SCHEMA),
        ("plan_id", PLAN_ID),
        ("status", "frozen"),
        ("execution_epoch_id", EXECUTION_EPOCH_ID),
        ("track_id", TRACK_ID),
        ("allowed_output_root", RESULTS_RELATIVE_ROOT),
        ("phase", "calibration"),
        ("diagnostic_only", True),
        ("compute_claim", False),
        ("paper_replication_evidence", False),
        ("timing_unlock", False),
        ("expected_scene_count", EXPECTED_FULL_PLAN_SCENES),
        ("expected_attempt_count", EXPECTED_FULL_PLAN_ATTEMPTS),
    ):
        _expect(plan.get(key) == expected, f"frozen-plan:{key}-mismatch")

    execution = plan.get("execution")
    _expect(isinstance(execution, dict), "frozen-plan:execution-invalid")
    assert isinstance(execution, dict)
    for key, expected in (
        ("families", list(FAMILIES)),
        ("counts", list(EXPECTED_PLAN_COUNTS)),
        ("seeds", list(SEEDS)),
        ("method_order", list(METHODS)),
        ("timesteps", TIMESTEPS),
        ("timestep_seconds", 0.1),
        ("simulated_duration_seconds", 10),
        ("trace_detail", "positions"),
        ("fresh_process_per_method", True),
        ("serial_execution", True),
    ):
        _expect(execution.get(key) == expected, f"frozen-plan:execution-{key}-mismatch")

    scenes = plan.get("scenes")
    _expect(
        isinstance(scenes, list) and len(scenes) == EXPECTED_FULL_PLAN_SCENES,
        "frozen-plan:scene-count-mismatch",
    )
    assert isinstance(scenes, list)
    by_key: dict[tuple[str, int, int], dict[str, Any]] = {}
    for raw_scene in scenes:
        _expect(isinstance(raw_scene, dict), "frozen-plan:scene-record-invalid")
        assert isinstance(raw_scene, dict)
        key = (
            raw_scene.get("family"),
            raw_scene.get("robot_count"),
            raw_scene.get("seed"),
        )
        _expect(
            isinstance(key[0], str)
            and _is_integer(key[1])
            and _is_integer(key[2]),
            "frozen-plan:scene-key-invalid",
        )
        typed_key = (str(key[0]), int(key[1]), int(key[2]))
        _expect(typed_key not in by_key, "frozen-plan:duplicate-scene")
        by_key[typed_key] = raw_scene

    target: list[dict[str, Any]] = []
    run_prefix = plan.get("run_id_prefix")
    _expect(run_prefix == EXECUTION_EPOCH_ID, "frozen-plan:run-prefix-mismatch")
    for family in FAMILIES:
        for seed in SEEDS:
            key = (family, ROBOT_COUNT, seed)
            _expect(key in by_key, "frozen-plan:target-scene-missing")
            record = by_key[key]
            scene_id = f"{family}-n8-seed{seed:02d}"
            output_relpath = f"{family}/n-8/seed-{seed:02d}"
            expected_run_ids = {
                method: f"{run_prefix}__{scene_id}__{method}"
                for method in METHODS
            }
            for field, expected in (
                ("scene_id", scene_id),
                ("output_relpath", output_relpath),
                (
                    "config_cli_argument",
                    f"{RESULTS_RELATIVE_ROOT}/{output_relpath}/config.yaml",
                ),
                ("run_ids", expected_run_ids),
            ):
                _expect(
                    record.get(field) == expected,
                    f"frozen-plan:target-{field}-mismatch",
                )
            for artifact_key in ("map", "scenario"):
                artifact = record.get(artifact_key)
                _expect(
                    isinstance(artifact, dict),
                    f"frozen-plan:target-{artifact_key}-invalid",
                )
                assert isinstance(artifact, dict)
                _safe_relative_path(
                    artifact.get("path"),
                    f"target-{artifact_key}",
                )
                _expect(
                    isinstance(artifact.get("sha256"), str)
                    and len(artifact["sha256"]) == 64,
                    f"frozen-plan:target-{artifact_key}-sha256-invalid",
                )
            target.append(record)

    _expect(
        len(target) == EXPECTED_SCENE_COUNT,
        "frozen-plan:target-count-mismatch",
    )
    artifacts = plan.get("artifacts")
    _expect(isinstance(artifacts, dict), "frozen-plan:artifacts-invalid")
    methods = plan.get("methods")
    _expect(
        isinstance(methods, dict) and set(methods) == set(METHODS),
        "frozen-plan:methods-invalid",
    )
    assert isinstance(artifacts, dict) and isinstance(methods, dict)

    requested: list[tuple[str, Mapping[str, Any]]] = []
    for key in sorted(artifacts):
        record = artifacts[key]
        _expect(
            isinstance(record, dict),
            f"frozen-plan:artifact-{key}-invalid",
        )
        assert isinstance(record, dict)
        requested.append((f"plan-artifact:{key}", record))
    contract = plan.get("contract")
    _expect(isinstance(contract, dict), "frozen-plan:contract-invalid")
    assert isinstance(contract, dict)
    requested.append(("plan-contract", contract))
    recovery = plan.get("recovery")
    _expect(
        isinstance(recovery, dict)
        and set(recovery) == {"addendum", "commit", "registry"},
        "frozen-plan:recovery-invalid",
    )
    assert isinstance(recovery, dict)
    _expect(
        isinstance(recovery.get("commit"), str)
        and re.fullmatch(r"[0-9a-f]{40}", recovery["commit"]) is not None,
        "frozen-plan:recovery-commit-invalid",
    )
    for recovery_key in ("addendum", "registry"):
        recovery_record = recovery.get(recovery_key)
        _expect(
            isinstance(recovery_record, dict),
            f"frozen-plan:recovery-{recovery_key}-invalid",
        )
        assert isinstance(recovery_record, dict)
        requested.append((f"plan-recovery:{recovery_key}", recovery_record))
    for method in METHODS:
        method_record = methods[method]
        _expect(
            isinstance(method_record, dict)
            and isinstance(method_record.get("binary"), dict),
            f"frozen-plan:{method}-binary-invalid",
        )
        requested.append((f"plan-binary:{method}", method_record["binary"]))
    unique_scene_inputs: dict[tuple[str, str], tuple[str, Mapping[str, Any]]] = {}
    for scene_record in target:
        for artifact_key in ("map", "scenario"):
            record = scene_record[artifact_key]
            identity = (record["path"], record["sha256"])
            unique_scene_inputs.setdefault(
                identity,
                (f"target-{artifact_key}:{record['path']}", record),
            )
    requested.extend(unique_scene_inputs.values())

    trace_compare = artifacts.get("trace_compare")
    assert isinstance(trace_compare, dict)
    trace_compare_relative_path = _safe_relative_path(
        trace_compare.get("path"), "trace-compare"
    )
    trace_compare_expected_sha256 = trace_compare.get("sha256")
    _expect(
        isinstance(trace_compare_expected_sha256, str)
        and len(trace_compare_expected_sha256) == 64,
        "trace-compare:sha256-invalid",
    )
    viewer = artifacts.get("viewer")
    _expect(isinstance(viewer, dict), "viewer:plan-record-invalid")
    assert isinstance(viewer, dict)
    viewer_relative_path = _safe_relative_path(viewer.get("path"), "viewer")
    viewer_expected_sha256 = viewer.get("sha256")
    _expect(
        isinstance(viewer_expected_sha256, str)
        and len(viewer_expected_sha256) == 64,
        "viewer:sha256-invalid",
    )
    build_manifest = artifacts.get("build_manifest")
    _expect(
        isinstance(build_manifest, dict),
        "build-manifest:plan-record-invalid",
    )
    assert isinstance(build_manifest, dict)
    build_manifest_relative_path = _safe_relative_path(
        build_manifest.get("path"), "build-manifest"
    )

    dependency_by_path: dict[str, dict[str, Any]] = {}
    trace_compare_bytes: bytes | None = None
    viewer_bytes: bytes | None = None
    build_manifest_bytes: bytes | None = None
    for label, record in requested:
        evidence, authenticated_bytes = _registered_file(
            repo_root, record, label
        )
        if evidence["path"] == trace_compare_relative_path:
            _expect(
                evidence["sha256"] == trace_compare_expected_sha256,
                "trace-compare:dependency-pin-mismatch",
            )
            if trace_compare_bytes is None:
                trace_compare_bytes = authenticated_bytes
            else:
                _expect(
                    trace_compare_bytes == authenticated_bytes,
                    "trace-compare:inconsistent-authenticated-bytes",
                )
        if evidence["path"] == viewer_relative_path:
            _expect(
                evidence["sha256"] == viewer_expected_sha256,
                "viewer:dependency-pin-mismatch",
            )
            viewer_bytes = authenticated_bytes
        if evidence["path"] == build_manifest_relative_path:
            build_manifest_bytes = authenticated_bytes
        prior = dependency_by_path.get(evidence["path"])
        if prior is not None:
            _expect(
                prior["sha256"] == evidence["sha256"],
                "frozen-plan:conflicting-dependency-pins",
            )
            continue
        dependency_by_path[evidence["path"]] = evidence
    dependencies = tuple(
        dependency_by_path[path] for path in sorted(dependency_by_path)
    )
    _expect(
        trace_compare_bytes is not None,
        "trace-compare:authenticated-bytes-missing",
    )
    _expect(viewer_bytes is not None, "viewer:authenticated-bytes-missing")
    _expect(
        build_manifest_bytes is not None,
        "build-manifest:authenticated-bytes-missing",
    )
    assert (
        trace_compare_bytes is not None
        and viewer_bytes is not None
        and build_manifest_bytes is not None
    )
    trace_compare_path = repo_root / trace_compare_relative_path
    return PlanContext(
        repo_root=repo_root,
        plan=plan,
        plan_bytes=plan_bytes,
        target_scenes=tuple(target),
        dependencies=dependencies,
        build_manifest_bytes=build_manifest_bytes,
        trace_compare_path=trace_compare_path,
        trace_compare_bytes=trace_compare_bytes,
        trace_compare_sha256=trace_compare_expected_sha256,
        viewer_path=repo_root / viewer_relative_path,
        viewer_bytes=viewer_bytes,
        viewer_sha256=viewer_expected_sha256,
    )


def _execute_authenticated_module(
    path: Path,
    authenticated_source: bytes,
    expected_sha256: str,
    module_name: str,
    label: str,
    *,
    module_bindings: Mapping[str, ModuleType] | None = None,
) -> ModuleType:
    if not path.is_absolute():
        raise AnalysisError(f"{label}:path-not-absolute")
    if _sha256(authenticated_source) != expected_sha256:
        raise AnalysisError(f"{label}:authenticated-bytes-sha256-mismatch")
    try:
        code = compile(
            authenticated_source,
            str(path),
            "exec",
            dont_inherit=True,
            optimize=0,
        )
    except Exception as error:
        raise AnalysisError(f"{label}:verified-source-compile-failed") from error
    module = ModuleType(module_name)
    module.__file__ = str(path)
    module.__loader__ = None
    module.__package__ = ""
    module.__spec__ = None
    module.__dict__["__builtins__"] = dict(vars(builtins))
    missing_module = object()
    prior_module = sys.modules.get(module_name, missing_module)
    prior_bindings = {
        name: sys.modules.get(name, missing_module)
        for name in (module_bindings or {})
    }
    sys.modules[module_name] = module
    for name, binding in (module_bindings or {}).items():
        sys.modules[name] = binding
    try:
        exec(code, module.__dict__, module.__dict__)
    except Exception as error:
        raise AnalysisError(f"{label}:verified-source-exec-failed") from error
    finally:
        if prior_module is missing_module:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = prior_module
        for name, prior in prior_bindings.items():
            if prior is missing_module:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior
    return module


def _load_trace_comparator_runtime(
    path: Path,
    authenticated_source: bytes,
    expected_sha256: str,
) -> TraceComparatorRuntime:
    module = _execute_authenticated_module(
        path,
        authenticated_source,
        expected_sha256,
        f"_visual_cohort_trace_compare_{PLAN_SHA256[:16]}",
        "trace-comparator",
    )
    compare = getattr(module, "compare_traces", None)
    if not callable(compare):
        raise AnalysisError("trace-comparator:entrypoint-missing")

    def invoke(left: Path, right: Path) -> TraceComparison:
        try:
            result = compare(left, right)
        except Exception as error:
            raise SceneError("trace-comparison-failed") from error
        equal = getattr(result, "equal", None)
        frames = getattr(result, "frames_compared", None)
        left_provenance = getattr(result, "left_provenance", None)
        right_provenance = getattr(result, "right_provenance", None)
        if (
            not isinstance(equal, bool)
            or not _is_integer(frames)
            or not isinstance(left_provenance, dict)
            or not isinstance(right_provenance, dict)
        ):
            raise SceneError("trace-comparison-result-invalid")
        return TraceComparison(
            equal=equal,
            frames_compared=int(frames),
            left_provenance=left_provenance,
            right_provenance=right_provenance,
        )

    return TraceComparatorRuntime(module=module, compare=invoke)


def _load_trace_comparator(
    path: Path,
    authenticated_source: bytes,
    expected_sha256: str,
) -> TraceComparator:
    return _load_trace_comparator_runtime(
        path,
        authenticated_source,
        expected_sha256,
    ).compare


def _audit_viewer_source(source: bytes, path: Path) -> None:
    try:
        text = source.decode("utf-8")
        tree = ast.parse(text, filename=str(path))
    except (UnicodeDecodeError, SyntaxError) as error:
        raise AnalysisError("replay-viewer:source-audit-failed") from error
    forbidden_modules = {
        "argparse",
        "ctypes",
        "multiprocessing",
        "shutil",
        "socket",
        "subprocess",
        "tempfile",
    }
    forbidden_calls = {
        "exec",
        "eval",
        "compile",
        "__import__",
        "system",
        "popen",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnlpe",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
    }
    filesystem_write_calls = {
        "chmod",
        "hardlink_to",
        "link",
        "mkdir",
        "open",
        "remove",
        "rename",
        "rmdir",
        "symlink",
        "symlink_to",
        "touch",
        "truncate",
        "unlink",
        "write",
        "write_bytes",
        "write_text",
        "writelines",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots = {alias.name.split(".", 1)[0] for alias in node.names}
            _expect(
                not roots & forbidden_modules,
                "replay-viewer:forbidden-module",
            )
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            _expect(
                root not in forbidden_modules,
                "replay-viewer:forbidden-module",
            )
        elif isinstance(node, ast.Call):
            function = node.func
            name = (
                function.id
                if isinstance(function, ast.Name)
                else function.attr
                if isinstance(function, ast.Attribute)
                else None
            )
            _expect(
                name not in forbidden_calls,
                "replay-viewer:forbidden-execution-call",
            )
            _expect(
                name not in filesystem_write_calls,
                "replay-viewer:forbidden-filesystem-write-call",
            )
    _expect(
        all(badge in text for badge in REQUIRED_REPLAY_BADGES),
        "replay-viewer:required-badge-missing",
    )


def _load_replay_builder(
    path: Path,
    authenticated_source: bytes,
    expected_sha256: str,
    comparator_module: ModuleType,
) -> ViewerBuilder:
    _audit_viewer_source(authenticated_source, path)
    module = _execute_authenticated_module(
        path,
        authenticated_source,
        expected_sha256,
        f"_visual_cohort_replay_viewer_{PLAN_SHA256[:16]}",
        "replay-viewer",
        module_bindings={"trace_compare": comparator_module},
    )
    builder = getattr(module, "build_replay_viewer", None)
    if not callable(builder):
        raise AnalysisError("replay-viewer:entrypoint-missing")
    return builder


def _scene_base_record(scene: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "authentication": {"state": "not_evaluated"},
        "family": scene["family"],
        "output_relpath": scene["output_relpath"],
        "parity": {"state": "not_evaluated"},
        "robot_count": scene["robot_count"],
        "scene_id": scene["scene_id"],
        "seed": scene["seed"],
        "task_validity": {
            "predicate_id": TASK_PREDICATE_ID,
            "state": "not_evaluated",
        },
    }


def _scene_missing(scene: Mapping[str, Any]) -> dict[str, Any]:
    result = _scene_base_record(scene)
    result["authentication"] = {"state": "missing"}
    return result


def _scene_failed(scene: Mapping[str, Any], code: str) -> dict[str, Any]:
    result = _scene_base_record(scene)
    result["authentication"] = {
        "reason": code,
        "state": "failed",
    }
    return result


def _scene_tree(scene_root: Path) -> tuple[dict[str, FileSnapshot], dict[str, Any]]:
    expected_root_entries = {
        "config.yaml",
        "frozen_plan.json",
        "manifest.json",
        "preflight.json",
        "replay.html",
        "optimized",
        "reference",
    }
    try:
        root_stat = os.lstat(scene_root)
    except OSError as error:
        raise SceneError(f"tree-root-stat-failed:{error.errno}") from error
    if not stat.S_ISDIR(root_stat.st_mode) or stat.S_ISLNK(root_stat.st_mode):
        raise SceneError("tree-root-not-directory")
    if stat.S_IMODE(root_stat.st_mode) != 0o555:
        raise SceneError("tree-directory-mode-mismatch:.")
    try:
        root_names = {entry.name for entry in os.scandir(scene_root)}
    except OSError as error:
        raise SceneError(f"tree-root-scan-failed:{error.errno}") from error
    if root_names != expected_root_entries:
        raise SceneError("tree-root-entry-set-mismatch")

    directory_records = [{"kind": "directory", "mode": "0555", "path": "."}]
    for method in METHODS:
        method_root = scene_root / method
        try:
            method_stat = os.lstat(method_root)
        except OSError as error:
            raise SceneError(f"tree-directory-stat-failed:{method}:{error.errno}") from error
        if not stat.S_ISDIR(method_stat.st_mode) or stat.S_ISLNK(method_stat.st_mode):
            raise SceneError(f"tree-entry-not-directory:{method}")
        if stat.S_IMODE(method_stat.st_mode) != 0o555:
            raise SceneError(f"tree-directory-mode-mismatch:{method}")
        try:
            names = {entry.name for entry in os.scandir(method_root)}
        except OSError as error:
            raise SceneError(f"tree-directory-scan-failed:{method}:{error.errno}") from error
        if names != set(METHOD_ARTIFACT_NAMES):
            raise SceneError(f"tree-method-entry-set-mismatch:{method}")
        directory_records.append(
            {"kind": "directory", "mode": "0555", "path": method}
        )

    snapshots: dict[str, FileSnapshot] = {}
    file_records: list[dict[str, Any]] = []
    for relative in sorted(REQUIRED_FILES):
        path = scene_root / relative
        try:
            metadata = os.lstat(path)
        except OSError as error:
            raise SceneError(f"tree-file-stat-failed:{relative}:{error.errno}") from error
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise SceneError(f"tree-entry-not-regular:{relative}")
        mode = stat.S_IMODE(metadata.st_mode)
        if mode != 0o444:
            raise SceneError(f"tree-file-mode-mismatch:{relative}")
        try:
            data = _open_regular_file(path, MAX_FILE_BYTES, f"scene-file:{relative}")
        except AnalysisError as error:
            raise SceneError(f"tree-file-read-failed:{relative}") from error
        snapshot = FileSnapshot(
            relative_path=relative,
            data=data,
            bytes=len(data),
            sha256=_sha256(data),
            mode=mode,
        )
        snapshots[relative] = snapshot
        file_records.append(
            {
                "bytes": snapshot.bytes,
                "kind": "file",
                "mode": "0444",
                "path": relative,
                "sha256": snapshot.sha256,
            }
        )
    inventory = sorted(
        [*directory_records, *file_records],
        key=lambda record: (record["path"], record["kind"]),
    )
    evidence = {
        "directory_count": len(directory_records),
        "file_count": len(file_records),
        "inventory_sha256": _sha256(canonical_json_bytes(inventory)),
        "sealed_directory_mode": "0555",
        "sealed_file_mode": "0444",
    }
    return snapshots, evidence


def _artifact_record_matches(
    record: Any,
    snapshot: FileSnapshot,
    expected_path: str,
    code_prefix: str,
) -> None:
    _expect(isinstance(record, dict), f"{code_prefix}:record-invalid", scene=True)
    assert isinstance(record, dict)
    _expect(
        set(record) == {"bytes", "path", "sha256"},
        f"{code_prefix}:record-fields-mismatch",
        scene=True,
    )
    _expect_json_exact(
        record.get("path"),
        expected_path,
        f"{code_prefix}:path-mismatch",
        scene=True,
    )
    _expect_json_exact(
        record.get("bytes"),
        snapshot.bytes,
        f"{code_prefix}:bytes-mismatch",
        scene=True,
    )
    _expect_json_exact(
        record.get("sha256"),
        snapshot.sha256,
        f"{code_prefix}:sha256-mismatch",
        scene=True,
    )


def _validate_preflight(
    preflight: Mapping[str, Any],
    scene: Mapping[str, Any],
    context: PlanContext,
) -> None:
    _expect(
        set(preflight) == PREFLIGHT_KEYS,
        "preflight:fields-mismatch",
        scene=True,
    )
    selected_output = f"{RESULTS_RELATIVE_ROOT}/{scene['output_relpath']}"
    for key, expected in (
        ("schema_version", PLAN_SCHEMA),
        ("plan_id", PLAN_ID),
        ("plan_path", PLAN_RELATIVE_PATH),
        ("plan_sha256", PLAN_SHA256),
        ("execution_epoch_id", EXECUTION_EPOCH_ID),
        ("run_id_prefix", EXECUTION_EPOCH_ID),
        ("selected_scene_id", scene["scene_id"]),
        ("selected_output_relpath", selected_output),
        ("config_cli_argument", scene["config_cli_argument"]),
        ("config_cli_path_policy", "repository-relative-no-whitespace"),
        ("failure_policy", "seal-stop-new-epoch-no-resume"),
        ("run_ids", scene["run_ids"]),
        ("scene_count", EXPECTED_FULL_PLAN_SCENES),
        ("attempt_count", EXPECTED_FULL_PLAN_ATTEMPTS),
        ("full_crossing_verified", True),
        ("diagnostic_only", True),
        ("compute_claim", False),
        ("paper_replication_evidence", False),
        ("timing_unlock", False),
        ("output_schemas", context.plan["output_schemas"]),
    ):
        _expect_json_exact(
            preflight.get(key),
            expected,
            f"preflight:{key}-mismatch",
            scene=True,
        )
    expected_execution_checkpoint = EXECUTION_CHECKPOINT_BY_SCENE.get(
        scene["scene_id"]
    )
    _expect(
        expected_execution_checkpoint is not None,
        "preflight:execution-checkpoint-unregistered",
        scene=True,
    )
    _expect_json_exact(
        preflight.get("execution_checkpoint_commit"),
        expected_execution_checkpoint,
        "preflight:execution-checkpoint-mismatch",
        scene=True,
    )
    _expect_json_exact(
        preflight.get("git_remote_count"),
        0,
        "preflight:git-remote-count-mismatch",
        scene=True,
    )
    expected_lease = {
        "dry_run_no_write": False,
        "held_during_execution": True,
        "manual_recovery_if_present": True,
        "path": (
            "results/simulator-demo/v1/visual-diagnostic/"
            ".calibration-v2-execution.lease"
        ),
        "policy": (
            "exclusive-create-no-follow-inode-bound-crash-persists-"
            "manual-recovery"
        ),
        "schema_version": "dancers-simulator-visual-execution-lease-v1",
    }
    _expect_json_exact(
        preflight.get("execution_lease"),
        expected_lease,
        "preflight:execution-lease-mismatch",
        scene=True,
    )
    passivity = preflight.get("viewer_passivity")
    _expect(isinstance(passivity, dict), "preflight:viewer-passivity-invalid", scene=True)
    assert isinstance(passivity, dict)
    _expect_json_exact(
        passivity,
        {
            "filesystem_write_surface": False,
            "output_published_exclusively_by_controller": True,
            "required_badges": list(REQUIRED_REPLAY_BADGES),
            "sealed_result_write_surface": False,
            "solver_launch_surface": False,
        },
        "preflight:viewer-passivity-mismatch",
        scene=True,
    )
    artifacts = preflight.get("artifacts")
    _expect(isinstance(artifacts, dict), "preflight:artifacts-invalid", scene=True)
    assert isinstance(artifacts, dict)
    plan_artifacts = context.plan["artifacts"]
    _expect(
        set(artifacts) == set(plan_artifacts),
        "preflight:artifacts-fields-mismatch",
        scene=True,
    )
    dependency_by_path = {
        record["path"]: record for record in context.dependencies
    }
    for key, registered in plan_artifacts.items():
        observed = artifacts.get(key)
        _expect(
            isinstance(observed, dict),
            f"preflight:artifact-{key}-missing",
            scene=True,
        )
        assert isinstance(observed, dict)
        path = registered["path"]
        dependency = dependency_by_path[path]
        _expect_json_exact(
            observed,
            {
                "bytes": dependency["bytes"],
                "path": path,
                "sha256": registered["sha256"],
            },
            f"preflight:artifact-{key}-mismatch",
            scene=True,
        )
    contract = context.plan["contract"]
    contract_dependency = dependency_by_path[contract["path"]]
    expected_contract = {
        "bytes": contract_dependency["bytes"],
        "commit": contract["commit"],
        "contract_id": "dancers-simulator-demo-v1",
        "path": contract["path"],
        "sha256": contract["sha256"],
        "track_id": TRACK_ID,
    }
    _expect_json_exact(
        preflight.get("contract"),
        expected_contract,
        "preflight:contract-mismatch",
        scene=True,
    )
    recovery = preflight.get("recovery")
    _expect(
        isinstance(recovery, dict)
        and set(recovery)
        == {"addendum", "commit", "contract", "failure_evidence", "registry", "v1_plan"},
        "preflight:recovery-fields-mismatch",
        scene=True,
    )
    assert isinstance(recovery, dict)
    _expect_json_exact(
        recovery.get("commit"),
        context.plan["recovery"]["commit"],
        "preflight:recovery-commit-mismatch",
        scene=True,
    )
    _expect_json_exact(
        recovery.get("contract"),
        expected_contract,
        "preflight:recovery-contract-mismatch",
        scene=True,
    )
    for key in ("addendum", "registry"):
        registered = context.plan["recovery"][key]
        dependency = dependency_by_path[registered["path"]]
        _expect_json_exact(
            recovery.get(key),
            {
                "bytes": dependency["bytes"],
                "path": registered["path"],
                "sha256": registered["sha256"],
            },
            f"preflight:recovery-{key}-mismatch",
            scene=True,
        )

    build_manifest = _parse_canonical_json(
        context.build_manifest_bytes,
        "build-manifest",
        require_canonical=False,
    )
    build_repository = build_manifest.get("repository")
    _expect(
        isinstance(build_repository, dict),
        "preflight:build-repository-invalid",
        scene=True,
    )
    assert isinstance(build_repository, dict)
    expected_build_methods: dict[str, Any] = {}
    for method in METHODS:
        registered_method = context.plan["methods"][method]
        binary = registered_method["binary"]
        binary_dependency = dependency_by_path[binary["path"]]
        expected_build_methods[method] = {
            "binary": {
                "bytes": binary_dependency["bytes"],
                "path": binary["path"],
                "sha256": binary["sha256"],
            },
            "build_manifest_key": registered_method["build_manifest_key"],
            "source_revision": registered_method["source_revision"],
        }
    _expect_json_exact(
        preflight.get("build"),
        {
            "common_patch_full_index_diff_sha256": build_repository[
                "common_patch_full_index_diff_sha256"
            ],
            "common_patch_sha256": build_repository[
                "common_patch_format_email_sha256"
            ],
            "manifest_sha256": context.plan["artifacts"]["build_manifest"][
                "sha256"
            ],
            "methods": expected_build_methods,
        },
        "preflight:build-mismatch",
        scene=True,
    )

    expected_environment_families: dict[str, Any] = {}
    for family in FAMILIES:
        family_scene = next(
            item
            for item in context.target_scenes
            if item["family"] == family and item["seed"] == 1
        )
        scenario_record = family_scene["scenario"]
        scenario_bytes = _open_regular_file(
            context.repo_root / scenario_record["path"],
            MAX_JSON_BYTES,
            f"preflight-environment-scenario:{family}",
        )
        _expect(
            _sha256(scenario_bytes) == scenario_record["sha256"],
            f"preflight:environment-{family}-scenario-sha256-mismatch",
            scene=True,
        )
        scenario_payload = _parse_canonical_json(
            scenario_bytes,
            f"preflight-environment-scenario:{family}",
            require_canonical=False,
        )
        scenario_metadata = scenario_payload.get("metadata")
        _expect(
            isinstance(scenario_metadata, dict)
            and isinstance(scenario_metadata.get("capacity_bank_id"), str),
            f"preflight:environment-{family}-capacity-bank-invalid",
            scene=True,
        )
        assert isinstance(scenario_metadata, dict)
        map_record = family_scene["map"]
        map_dependency = dependency_by_path[map_record["path"]]
        expected_environment_families[family] = {
            "capacity_bank_id": scenario_metadata["capacity_bank_id"],
            "map": {
                "bytes": map_dependency["bytes"],
                "path": map_record["path"],
                "sha256": map_record["sha256"],
            },
            "map_metrics": context.plan["environment_descriptors"][family],
            "scenario_counts": list(EXPECTED_PLAN_COUNTS),
        }
    _expect_json_exact(
        preflight.get("environment"),
        {
            "families": expected_environment_families,
            "manifest_sha256": context.plan["artifacts"][
                "environment_manifest"
            ]["sha256"],
            "verified_unique_map_count": len(FAMILIES),
            "verified_unique_scenario_count": (
                len(FAMILIES) * len(EXPECTED_PLAN_COUNTS)
            ),
        },
        "preflight:environment-mismatch",
        scene=True,
    )

    registry_record = context.plan["recovery"]["registry"]
    registry_bytes = _open_regular_file(
        context.repo_root / registry_record["path"],
        MAX_JSON_BYTES,
        "recovery-registry",
    )
    _expect(
        _sha256(registry_bytes) == registry_record["sha256"],
        "preflight:recovery-registry-sha256-mismatch",
        scene=True,
    )
    registry = _parse_canonical_json(
        registry_bytes,
        "recovery-registry",
        require_canonical=False,
    )
    registered_failure = registry.get("failure_evidence")
    registered_v1_plan = registry.get("v1_plan")
    _expect(
        isinstance(registered_failure, dict)
        and isinstance(registered_failure.get("inventory"), dict)
        and isinstance(registered_v1_plan, dict),
        "preflight:recovery-registry-structure-invalid",
        scene=True,
    )
    assert isinstance(registered_failure, dict)
    assert isinstance(registered_failure["inventory"], dict)
    assert isinstance(registered_v1_plan, dict)
    expected_failure = {
        "commit": registered_failure["commit"],
        "directory_count": registered_failure["inventory"]["directory_count"],
        "file_count": registered_failure["inventory"]["file_count"],
        "files": registered_failure["files"],
        "immutable": True,
        "path": registered_failure["path"],
        "scientific_admission": registered_failure["scientific_admission"],
    }
    _expect_json_exact(
        recovery.get("failure_evidence"),
        expected_failure,
        "preflight:recovery-failure-evidence-mismatch",
        scene=True,
    )
    v1_plan_path = context.repo_root / registered_v1_plan["path"]
    v1_plan_bytes = _open_regular_file(
        v1_plan_path,
        MAX_JSON_BYTES,
        "recovery-v1-plan",
    )
    _expect(
        _sha256(v1_plan_bytes) == registered_v1_plan["sha256"],
        "preflight:recovery-v1-plan-sha256-mismatch",
        scene=True,
    )
    _expect_json_exact(
        recovery.get("v1_plan"),
        {**registered_v1_plan, "bytes": len(v1_plan_bytes)},
        "preflight:recovery-v1-plan-mismatch",
        scene=True,
    )

    epoch_state = preflight.get("epoch_state")
    _expect(
        isinstance(epoch_state, dict)
        and set(epoch_state)
        == {
            "completed_scene_count",
            "completed_scenes",
            "selected_destination_absent",
            "state",
        },
        "preflight:epoch-state-fields-mismatch",
        scene=True,
    )
    assert isinstance(epoch_state, dict)
    completed = epoch_state.get("completed_scenes")
    completed_count = epoch_state.get("completed_scene_count")
    _expect(
        isinstance(completed, list)
        and _is_integer(completed_count)
        and completed_count == len(completed)
        and _json_exact(epoch_state.get("selected_destination_absent"), True)
        and _json_exact(
            epoch_state.get("state"),
            "unused"
            if completed_count == 0
            else "active-with-authenticated-completions",
        ),
        "preflight:epoch-state-values-mismatch",
        scene=True,
    )
    known_scenes = {
        item["scene_id"]: item for item in context.plan["scenes"]
    }
    seen_completed: set[str] = set()
    for record in completed:
        _expect(
            isinstance(record, dict)
            and set(record)
            == {
                "directory_count",
                "file_count",
                "manifest_sha256",
                "scene_id",
                "sealed",
            },
            "preflight:epoch-completed-record-fields-mismatch",
            scene=True,
        )
        assert isinstance(record, dict)
        completed_scene_id = record.get("scene_id")
        _expect(
            isinstance(completed_scene_id, str)
            and completed_scene_id in known_scenes
            and completed_scene_id not in seen_completed
            and _json_exact(record.get("directory_count"), 3)
            and _json_exact(record.get("file_count"), 15)
            and isinstance(record.get("manifest_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", record["manifest_sha256"])
            is not None
            and _json_exact(record.get("sealed"), True),
            "preflight:epoch-completed-record-values-mismatch",
            scene=True,
        )
        assert isinstance(completed_scene_id, str)
        completed_scene = known_scenes[completed_scene_id]
        completed_output = _safe_relative_path(
            completed_scene.get("output_relpath"),
            "preflight-epoch-completed-output",
        )
        completed_manifest_relative = (
            f"{RESULTS_RELATIVE_ROOT}/{completed_output}/manifest.json"
        )
        _require_no_symlink_components(
            context.repo_root,
            completed_manifest_relative,
            "preflight-epoch-completed-manifest",
            allow_missing=False,
        )
        completed_manifest_bytes = _open_regular_file(
            context.repo_root / completed_manifest_relative,
            MAX_JSON_BYTES,
            "preflight-epoch-completed-manifest",
        )
        _expect_json_exact(
            record["manifest_sha256"],
            _sha256(completed_manifest_bytes),
            "preflight:epoch-completed-manifest-sha256-mismatch",
            scene=True,
        )
        seen_completed.add(completed_scene_id)
    _expect(
        [record["scene_id"] for record in completed]
        == sorted(seen_completed)
        and scene["scene_id"] not in seen_completed,
        "preflight:epoch-completed-order-or-selection-mismatch",
        scene=True,
    )


def _validate_metadata(
    metadata: Mapping[str, Any],
    method: str,
    scene: Mapping[str, Any],
    scene_root: Path,
    config_sha256: str,
    context: PlanContext,
) -> None:
    method_plan = context.plan["methods"][method]
    build_manifest = _parse_canonical_json(
        context.build_manifest_bytes,
        "build-manifest",
        require_canonical=False,
    )
    repository = build_manifest.get("repository")
    builds = build_manifest.get("builds")
    _expect(
        isinstance(repository, dict) and isinstance(builds, dict),
        "build-manifest:registered-structure-invalid",
    )
    assert isinstance(repository, dict) and isinstance(builds, dict)
    build = builds.get(method_plan["build_manifest_key"])
    _expect(
        isinstance(build, dict),
        f"build-manifest:{method}:record-invalid",
    )
    assert isinstance(build, dict)
    expected = {
        "algorithm_revision": method_plan["source_revision"],
        "binary_sha256": method_plan["binary"]["sha256"],
        "build_manifest_sha256": context.plan["artifacts"]["build_manifest"]["sha256"],
        "common_patch_full_index_diff_sha256": repository[
            "common_patch_full_index_diff_sha256"
        ],
        "common_patch_sha256": repository[
            "common_patch_format_email_sha256"
        ],
        "compute_claim": False,
        "config_cli_argument": scene["config_cli_argument"],
        "config_cli_path_policy": "repository-relative-no-whitespace",
        "config_path": str(scene_root / "config.yaml"),
        "config_sha256": config_sha256,
        "contract_id": "dancers-simulator-demo-v1",
        "contract_sha256": context.plan["contract"]["sha256"],
        "controller_sha256": context.plan["artifacts"]["controller"]["sha256"],
        "diagnostic_only": True,
        "environment_manifest_sha256": context.plan["artifacts"]["environment_manifest"]["sha256"],
        "execution_epoch_id": EXECUTION_EPOCH_ID,
        "failure_policy": "seal-stop-new-epoch-no-resume",
        "implementation_id": method,
        "map_path": str(context.repo_root / scene["map"]["path"]),
        "map_sha256": scene["map"]["sha256"],
        "omp_threads": 1,
        "output_schemas": context.plan["output_schemas"],
        "phase": "calibration",
        "pixi_lock_sha256": context.plan["artifacts"]["pixi_lock"]["sha256"],
        "plan_id": PLAN_ID,
        "plan_sha256": PLAN_SHA256,
        "recovery": context.plan["recovery"],
        "robot_count": ROBOT_COUNT,
        "run_class": "positions-replay",
        "run_id": scene["run_ids"][method],
        "run_id_prefix": EXECUTION_EPOCH_ID,
        "scenario_path": str(
            context.repo_root / scene["scenario"]["path"]
        ),
        "scenario_sha256": scene["scenario"]["sha256"],
        "scene_id": scene["scene_id"],
        "seed": scene["seed"],
        "source_head_revision": build.get("applied_head"),
        "timestep_seconds": 0.1,
        "timesteps": TIMESTEPS,
        "timing_unlock": False,
        "trace_compare_sha256": context.plan["artifacts"]["trace_compare"]["sha256"],
        "track_id": TRACK_ID,
        "viewer_sha256": context.plan["artifacts"]["viewer"]["sha256"],
    }
    _expect(
        set(metadata) == METADATA_KEYS,
        f"metadata:{method}:fields-mismatch",
        scene=True,
    )
    _expect_json_exact(
        dict(metadata),
        expected,
        f"metadata:{method}:registered-values-mismatch",
        scene=True,
    )


def _validate_resolved_config(
    resolved: Mapping[str, Any],
    method: str,
    scene: Mapping[str, Any],
    context: PlanContext,
) -> None:
    expected = {
        "communication_radius_m": 20.0,
        "communications_failure_rate": 0.0,
        "config_file": scene["config_cli_argument"],
        "configured_robot_count": ROBOT_COUNT,
        "damping": 0.0,
        "display": False,
        "factorgraph_layers": [
            {
                "id": 0,
                "name": "Planning",
                "numeric_params": {
                    "MAX_SPEED": MAX_SPEED_METRES_PER_SECOND,
                    "ROBOT_RADIUS": ROBOT_RADIUS_METRES,
                    "SIGMA_FACTOR_DYNAMICS": 0.1,
                    "SIGMA_FACTOR_INTERROBOT": 0.01,
                    "SIGMA_FACTOR_OBSTACLE": 0.01,
                    "SIGMA_FACTOR_UNICYCLE": 0.001,
                    "SIGMA_VARIABLE_HORIZON": 0.01,
                    "TURNING_RADIUS": 1.0,
                    "T_HORIZON": 1.5,
                },
                "string_params": {},
                "type": "planning",
            }
        ],
        "gbp_external_iterations": 2,
        "max_speed_mps": MAX_SPEED_METRES_PER_SECOND,
        "max_timesteps": TIMESTEPS,
        "obstacle_distance_cap_pixels": 20.0,
        "obstacle_field_height": 1000,
        "obstacle_field_width": 1000,
        "obstacle_file": str(context.repo_root / scene["map"]["path"]),
        "obstacles_present": True,
        "planning_horizon_seconds": 1.5,
        "rng_seed": scene["seed"],
        "robot_model": "sphere",
        "robot_radius_m": ROBOT_RADIUS_METRES,
        "scenario": "file",
        "scenario_file": str(
            context.repo_root / scene["scenario"]["path"]
        ),
        "screen_size_px": 800,
        "simulation_mode": 1,
        "simulation_name": scene["scene_id"],
        "symmetric_interrobot_factors": False,
        "timestep_seconds": 0.10000000149011612,
        "turning_radius_m": 1.0,
        "world_size_m": 100,
    }
    _expect(
        set(resolved) == RESOLVED_CONFIG_KEYS,
        f"resolved-config:{method}:fields-mismatch",
        scene=True,
    )
    _expect_json_exact(
        dict(resolved),
        expected,
        f"resolved-config:{method}:registered-values-mismatch",
        scene=True,
    )


def _parse_trace_record(
    raw: bytes,
    method: str,
    line_number: int,
) -> dict[str, Any]:
    _expect(
        0 < len(raw) <= MAX_TRACE_LINE_BYTES,
        f"trace-record:{method}:line-{line_number}:size-invalid",
        scene=True,
    )
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_no_duplicate_object,
            parse_constant=_reject_json_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
    ) as error:
        raise SceneError(
            f"trace-record:{method}:line-{line_number}:json-invalid"
        ) from error
    _expect(
        isinstance(value, dict),
        f"trace-record:{method}:line-{line_number}:not-object",
        scene=True,
    )
    assert isinstance(value, dict)
    try:
        depth = _json_depth(value)
    except RecursionError as error:
        raise SceneError(
            f"trace-record:{method}:line-{line_number}:depth-invalid"
        ) from error
    _expect(
        depth <= 128,
        f"trace-record:{method}:line-{line_number}:depth-invalid",
        scene=True,
    )
    return value


def _swept_disc_pair(
    left_start: Sequence[float],
    left_end: Sequence[float],
    right_start: Sequence[float],
    right_end: Sequence[float],
    combined_radius: float,
) -> tuple[float, float]:
    relative_start = (
        float(left_start[0]) - float(right_start[0]),
        float(left_start[1]) - float(right_start[1]),
    )
    relative_delta = (
        (float(left_end[0]) - float(left_start[0]))
        - (float(right_end[0]) - float(right_start[0])),
        (float(left_end[1]) - float(left_start[1]))
        - (float(right_end[1]) - float(right_start[1])),
    )
    scale = max(
        abs(relative_start[0]),
        abs(relative_start[1]),
        abs(relative_delta[0]),
        abs(relative_delta[1]),
        combined_radius,
    )
    if scale == 0.0:
        return 0.0, 1.0 if combined_radius > 0.0 else 0.0
    scaled_start = (
        relative_start[0] / scale,
        relative_start[1] / scale,
    )
    scaled_delta = (
        relative_delta[0] / scale,
        relative_delta[1] / scale,
    )
    scaled_radius = combined_radius / scale
    quadratic = (
        scaled_delta[0] * scaled_delta[0]
        + scaled_delta[1] * scaled_delta[1]
    )
    dot = (
        scaled_start[0] * scaled_delta[0]
        + scaled_start[1] * scaled_delta[1]
    )
    minimum_parameter = (
        min(1.0, max(0.0, -dot / quadratic))
        if quadratic > 0.0
        else 0.0
    )
    minimum_distance = scale * math.hypot(
        scaled_start[0] + minimum_parameter * scaled_delta[0],
        scaled_start[1] + minimum_parameter * scaled_delta[1],
    )
    constant = (
        scaled_start[0] * scaled_start[0]
        + scaled_start[1] * scaled_start[1]
        - scaled_radius * scaled_radius
    )
    if quadratic == 0.0:
        return minimum_distance, 1.0 if constant < 0.0 else 0.0
    linear = 2.0 * dot
    discriminant = (
        linear * linear - 4.0 * quadratic * constant
    )
    if discriminant <= 0.0:
        return minimum_distance, 0.0
    root = math.sqrt(discriminant)
    enter = (-linear - root) / (2.0 * quadratic)
    leave = (-linear + root) / (2.0 * quadratic)
    overlap_fraction = max(
        0.0,
        min(1.0, leave) - max(0.0, enter),
    )
    return minimum_distance, overlap_fraction


def _validate_positions_trace(
    snapshot: FileSnapshot,
    method: str,
) -> list[dict[str, Any]]:
    lines = snapshot.data.splitlines(keepends=True)
    _expect(
        len(lines) == FRAMES_PER_PAIR + 1,
        f"trace-structure:{method}:record-count-mismatch",
        scene=True,
    )
    frames: list[dict[str, Any]] = []
    previous_time = -math.inf
    expected_ids = list(range(ROBOT_COUNT))
    for frame_index, raw_line in enumerate(lines[1:]):
        line_number = frame_index + 2
        if raw_line.endswith(b"\n"):
            raw_line = raw_line[:-1]
        frame = _parse_trace_record(raw_line, method, line_number)
        _expect(
            set(frame) == FRAME_KEYS,
            f"trace-structure:{method}:frame-{frame_index}:fields-mismatch",
            scene=True,
        )
        expected_phase = "pre_update" if frame_index % 2 == 0 else "post_update"
        expected_tick = (
            frame_index // 2
            if expected_phase == "pre_update"
            else (frame_index + 1) // 2
        )
        _expect_json_exact(
            frame.get("type"),
            "frame",
            f"trace-structure:{method}:frame-{frame_index}:type-mismatch",
            scene=True,
        )
        _expect(
            _is_integer(frame.get("tick"))
            and frame["tick"] == expected_tick,
            f"trace-structure:{method}:frame-{frame_index}:tick-mismatch",
            scene=True,
        )
        _expect_json_exact(
            frame.get("phase"),
            expected_phase,
            f"trace-structure:{method}:frame-{frame_index}:phase-mismatch",
            scene=True,
        )
        simulated_time = frame.get("simulated_time_seconds")
        _expect(
            _finite_float(simulated_time),
            f"trace-structure:{method}:frame-{frame_index}:time-invalid",
            scene=True,
        )
        observed_time = float(simulated_time)
        _expect(
            observed_time >= previous_time
            and math.isclose(
                observed_time,
                float(expected_tick) * 0.1,
                rel_tol=0.0,
                abs_tol=1e-5,
            ),
            f"trace-structure:{method}:frame-{frame_index}:time-mismatch",
            scene=True,
        )
        previous_time = observed_time
        robots = frame.get("robots")
        _expect(
            isinstance(robots, list) and len(robots) == ROBOT_COUNT,
            f"trace-structure:{method}:frame-{frame_index}:robots-invalid",
            scene=True,
        )
        assert isinstance(robots, list)
        _expect(
            all(isinstance(robot, dict) for robot in robots),
            f"trace-structure:{method}:frame-{frame_index}:robot-not-object",
            scene=True,
        )
        robot_ids = [robot.get("rid") for robot in robots]
        _expect(
            all(_is_integer(robot_id) for robot_id in robot_ids)
            and robot_ids == expected_ids,
            f"trace-structure:{method}:frame-{frame_index}:robot-ids-mismatch",
            scene=True,
        )
        for robot_index, robot in enumerate(robots):
            _expect(
                set(robot) == ROBOT_KEYS,
                (
                    f"trace-structure:{method}:frame-{frame_index}:"
                    f"robot-{robot_index}:fields-mismatch"
                ),
                scene=True,
            )
            state_values = robot.get("state")
            state_hex = robot.get("state_hex")
            _expect(
                isinstance(state_values, list)
                and len(state_values) == 6
                and all(_finite_float(item) for item in state_values),
                (
                    f"trace-structure:{method}:frame-{frame_index}:"
                    f"robot-{robot_index}:state-invalid"
                ),
                scene=True,
            )
            assert isinstance(state_values, list)
            _expect(
                isinstance(state_hex, list)
                and len(state_hex) == len(state_values)
                and all(
                    isinstance(item, str)
                    and HEX64_RE.fullmatch(item) is not None
                    for item in state_hex
                ),
                (
                    f"trace-structure:{method}:frame-{frame_index}:"
                    f"robot-{robot_index}:state-hex-invalid"
                ),
                scene=True,
            )
            assert isinstance(state_hex, list)
            _expect(
                state_hex
                == [struct.pack(">d", float(item)).hex() for item in state_values],
                (
                    f"trace-structure:{method}:frame-{frame_index}:"
                    f"robot-{robot_index}:state-hex-value-mismatch"
                ),
                scene=True,
            )
            _expect(
                _finite_float(robot.get("bearing"))
                and _finite_float(robot.get("radius_m"))
                and _json_exact(robot["radius_m"], 1.0)
                and isinstance(robot.get("goal_reached"), bool),
                (
                    f"trace-structure:{method}:frame-{frame_index}:"
                    f"robot-{robot_index}:visual-fields-invalid"
                ),
                scene=True,
            )
            current_waypoint = robot.get("current_waypoint")
            _expect(
                current_waypoint is None
                or (
                    isinstance(current_waypoint, list)
                    and len(current_waypoint) == 6
                    and all(
                        _finite_float(item) for item in current_waypoint
                    )
                ),
                (
                    f"trace-structure:{method}:frame-{frame_index}:"
                    f"robot-{robot_index}:current_waypoint-invalid"
                ),
                scene=True,
            )
            final_goal = robot.get("final_goal")
            _expect(
                isinstance(final_goal, list)
                and len(final_goal) == 2
                and all(_finite_float(item) for item in final_goal),
                (
                    f"trace-structure:{method}:frame-{frame_index}:"
                    f"robot-{robot_index}:final_goal-invalid"
                ),
                scene=True,
            )
            assert isinstance(final_goal, list)
            if frame_index:
                _expect_json_exact(
                    final_goal,
                    frames[0]["robots"][robot_index]["final_goal"],
                    (
                        f"trace-structure:{method}:frame-{frame_index}:"
                        f"robot-{robot_index}:final-goal-changed"
                    ),
                    scene=True,
                )
            prior_goal_reached = (
                frames[-1]["robots"][robot_index]["goal_reached"]
                if frame_index
                else False
            )
            independently_reached = math.hypot(
                float(state_values[0]) - float(final_goal[0]),
                float(state_values[1]) - float(final_goal[1]),
            ) <= float(robot["radius_m"])
            _expect_json_exact(
                robot["goal_reached"],
                prior_goal_reached or independently_reached,
                (
                    f"trace-structure:{method}:frame-{frame_index}:"
                    f"robot-{robot_index}:goal-flag-mismatch"
                ),
                scene=True,
            )
        metrics = frame.get("metrics")
        _expect(
            isinstance(metrics, dict),
            f"trace-structure:{method}:frame-{frame_index}:metrics-invalid",
            scene=True,
        )
        assert isinstance(metrics, dict)
        _expect(
            set(metrics) == FRAME_METRIC_KEYS,
            f"trace-structure:{method}:frame-{frame_index}:metrics-fields-mismatch",
            scene=True,
        )
        _expect_json_exact(
            metrics.get("robot_count"),
            ROBOT_COUNT,
            f"trace-structure:{method}:frame-{frame_index}:robot-count-mismatch",
            scene=True,
        )
        for key in (
            "cumulative_invalid_geometry_evaluations",
            "goals_reached",
            "obstacle_collisions",
            "pair_collisions",
        ):
            _expect(
                _is_integer(metrics.get(key)) and metrics[key] >= 0,
                f"trace-structure:{method}:frame-{frame_index}:metrics-{key}-invalid",
                scene=True,
            )
        _expect(
            metrics["goals_reached"] <= ROBOT_COUNT
            and isinstance(metrics.get("all_goals_reached"), bool)
            and isinstance(metrics.get("updated_this_frame"), bool),
            f"trace-structure:{method}:frame-{frame_index}:metrics-state-invalid",
            scene=True,
        )
        _expect_json_exact(
            metrics["updated_this_frame"],
            expected_phase == "post_update",
            (
                f"trace-structure:{method}:frame-{frame_index}:"
                "updated-phase-mismatch"
            ),
            scene=True,
        )
        for key in (
            "cumulative_obstacle_collision_exposure_seconds",
            "cumulative_pair_collision_exposure_seconds",
        ):
            _expect(
                _finite_float(metrics.get(key)) and float(metrics[key]) >= 0.0,
                f"trace-structure:{method}:frame-{frame_index}:metrics-{key}-invalid",
                scene=True,
            )
        for key in (
            "frame_min_obstacle_clearance_m",
            "frame_min_pair_clearance_m",
        ):
            _expect(
                metrics.get(key) is None or _finite_float(metrics[key]),
                f"trace-structure:{method}:frame-{frame_index}:metrics-{key}-invalid",
                scene=True,
            )
        if expected_phase == "post_update":
            _expect(
                _finite_float(metrics["frame_min_obstacle_clearance_m"])
                and _finite_float(metrics["frame_min_pair_clearance_m"]),
                (
                    f"trace-structure:{method}:frame-{frame_index}:"
                    "post-update-clearance-invalid"
                ),
                scene=True,
            )
        rng_state = frame.get("rng_state")
        _expect(
            isinstance(rng_state, dict)
            and set(rng_state) == RNG_STATE_KEYS
            and all(isinstance(value, str) and value for value in rng_state.values()),
            f"trace-structure:{method}:frame-{frame_index}:rng-state-invalid",
            scene=True,
        )
        simulator_state = frame.get("simulator_state")
        _expect(
            isinstance(simulator_state, dict)
            and set(simulator_state) == SIMULATOR_STATE_KEYS,
            f"trace-structure:{method}:frame-{frame_index}:simulator-state-fields-mismatch",
            scene=True,
        )
        assert isinstance(simulator_state, dict)
        _expect(
            _is_integer(simulator_state.get("iterations"))
            and simulator_state["iterations"] >= 0
            and _is_integer(simulator_state.get("last_junction_creation_clock"))
            and _is_integer(simulator_state.get("next_robot_id"))
            and simulator_state["next_robot_id"] >= ROBOT_COUNT
            and _is_integer(simulator_state.get("robot_group_clock"))
            and simulator_state["robot_group_clock"] >= 0
            and isinstance(simulator_state.get("run_flag"), bool)
            and _json_exact(simulator_state.get("simulation_mode"), 1),
            f"trace-structure:{method}:frame-{frame_index}:simulator-state-values-invalid",
            scene=True,
        )
        _expect(
            isinstance(simulator_state.get("last_created_robots"), list)
            and all(
                _is_integer(robot_id) and 0 <= robot_id < ROBOT_COUNT
                for robot_id in simulator_state["last_created_robots"]
            )
            and all(
                isinstance(simulator_state.get(key), str)
                and HEX64_RE.fullmatch(simulator_state[key]) is not None
                for key in (
                    "mean_number_of_neighbours_hex",
                    "mean_number_of_neighbours_time_hex",
                )
            ),
            f"trace-structure:{method}:frame-{frame_index}:simulator-state-shape-invalid",
            scene=True,
        )

        goal_flags = [robot["goal_reached"] for robot in robots]
        _expect_json_exact(
            metrics["goals_reached"],
            sum(goal_flags),
            (
                f"trace-structure:{method}:frame-{frame_index}:"
                "goal-count-mismatch"
            ),
            scene=True,
        )
        _expect_json_exact(
            metrics["all_goals_reached"],
            all(goal_flags),
            (
                f"trace-structure:{method}:frame-{frame_index}:"
                "all-goals-mismatch"
            ),
            scene=True,
        )
        cumulative_fields = (
            "cumulative_invalid_geometry_evaluations",
            "cumulative_obstacle_collision_exposure_seconds",
            "cumulative_pair_collision_exposure_seconds",
        )
        if frame_index == 0:
            _expect(
                all(metrics[field] == 0 for field in cumulative_fields)
                and metrics["obstacle_collisions"] == 0
                and metrics["pair_collisions"] == 0
                and metrics["frame_min_obstacle_clearance_m"] is None
                and metrics["frame_min_pair_clearance_m"] is None,
                f"trace-structure:{method}:initial-metrics-not-baseline",
                scene=True,
            )
        else:
            previous_frame = frames[-1]
            previous_metrics = previous_frame["metrics"]
            _expect(
                all(
                    float(metrics[field]) >= float(previous_metrics[field])
                    for field in cumulative_fields
                ),
                (
                    f"trace-structure:{method}:frame-{frame_index}:"
                    "metrics-cumulative-not-monotonic"
                ),
                scene=True,
            )
            _expect(
                all(
                    not previous_robot["goal_reached"]
                    or robot["goal_reached"]
                    for previous_robot, robot in zip(
                        previous_frame["robots"], robots
                    )
                ),
                (
                    f"trace-structure:{method}:frame-{frame_index}:"
                    "robot-goal-state-regressed"
                ),
                scene=True,
            )
            if expected_phase == "pre_update":
                persistent_fields = (
                    "all_goals_reached",
                    *cumulative_fields,
                    "goals_reached",
                    "robot_count",
                )
                _expect(
                    all(
                        _json_exact(metrics[field], previous_metrics[field])
                        for field in persistent_fields
                    )
                    and metrics["obstacle_collisions"] == 0
                    and metrics["pair_collisions"] == 0
                    and metrics["frame_min_obstacle_clearance_m"] is None
                    and metrics["frame_min_pair_clearance_m"] is None,
                    (
                        f"trace-structure:{method}:frame-{frame_index}:"
                        "pre-update-metrics-not-baseline"
                    ),
                    scene=True,
                )
                _expect_json_exact(
                    robots,
                    previous_frame["robots"],
                    (
                        f"trace-structure:{method}:frame-{frame_index}:"
                        "pre-update-robots-not-repeated"
                    ),
                    scene=True,
                )
            else:
                independently_observed_pair_collisions = 0
                independently_observed_pair_exposure = 0.0
                independently_observed_pair_clearance = math.inf
                previous_robots = previous_frame["robots"]
                for left_id in range(ROBOT_COUNT):
                    for right_id in range(left_id + 1, ROBOT_COUNT):
                        left_previous = previous_robots[left_id]
                        left_current = robots[left_id]
                        right_previous = previous_robots[right_id]
                        right_current = robots[right_id]
                        combined_radius = (
                            float(left_current["radius_m"])
                            + float(right_current["radius_m"])
                        )
                        minimum_distance, overlap_fraction = (
                            _swept_disc_pair(
                                left_previous["state"],
                                left_current["state"],
                                right_previous["state"],
                                right_current["state"],
                                combined_radius,
                            )
                        )
                        independently_observed_pair_clearance = min(
                            independently_observed_pair_clearance,
                            minimum_distance - combined_radius,
                        )
                        independently_observed_pair_exposure += (
                            overlap_fraction
                        )
                        independently_observed_pair_collisions += (
                            overlap_fraction > 0.0
                        )
                _expect_json_exact(
                    metrics["pair_collisions"],
                    independently_observed_pair_collisions,
                    (
                        f"trace-structure:{method}:frame-{frame_index}:"
                        "pair-collision-count-mismatch"
                    ),
                    scene=True,
                )
                _expect(
                    math.isclose(
                        float(metrics["frame_min_pair_clearance_m"]),
                        independently_observed_pair_clearance,
                        rel_tol=0.0,
                        abs_tol=1e-12,
                    ),
                    (
                        f"trace-structure:{method}:frame-{frame_index}:"
                        "pair-clearance-mismatch"
                    ),
                    scene=True,
                )
                expected_pair_exposure = (
                    float(previous_metrics[
                        "cumulative_pair_collision_exposure_seconds"
                    ])
                    + independently_observed_pair_exposure
                    * 0.10000000149011612
                )
                _expect(
                    math.isclose(
                        float(metrics[
                            "cumulative_pair_collision_exposure_seconds"
                        ]),
                        expected_pair_exposure,
                        rel_tol=0.0,
                        abs_tol=1e-12,
                    ),
                    (
                        f"trace-structure:{method}:frame-{frame_index}:"
                        "pair-exposure-mismatch"
                    ),
                    scene=True,
                )
        frames.append(frame)
    return frames


def _validate_trace_header(
    snapshot: FileSnapshot,
    method: str,
    scene: Mapping[str, Any],
    metadata: Mapping[str, Any],
    summary: Mapping[str, Any],
    context: PlanContext,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    first_line, separator, _ = snapshot.data.partition(b"\n")
    _expect(
        separator == b"\n" and bool(first_line),
        f"trace-header:{method}:missing",
        scene=True,
    )
    header = _parse_canonical_json(
        first_line + b"\n",
        f"trace-header:{method}",
        scene=True,
    )
    _expect(
        set(header) == TRACE_HEADER_KEYS,
        f"trace-header:{method}:fields-mismatch",
        scene=True,
    )
    for key, expected in (
        ("type", "header"),
        ("schema_version", "dancers-comparison-trace-v3"),
    ):
        _expect_json_exact(
            header.get(key),
            expected,
            f"trace-header:{method}:{key}-mismatch",
            scene=True,
        )
    _expect_json_exact(
        header.get("metadata"),
        dict(metadata),
        f"trace-header:{method}:metadata-mismatch",
        scene=True,
    )
    observer = header.get("observer")
    _expect(
        isinstance(observer, dict),
        f"trace-header:{method}:observer-invalid",
        scene=True,
    )
    assert isinstance(observer, dict)
    _expect(
        set(observer) == OBSERVER_KEYS,
        f"trace-header:{method}:observer-fields-mismatch",
        scene=True,
    )
    _expect_json_exact(
        observer,
        EXPECTED_OBSERVER,
        f"trace-header:{method}:observer-values-mismatch",
        scene=True,
    )
    resolved = header.get("resolved_config")
    _expect(
        isinstance(resolved, dict),
        f"trace-header:{method}:resolved-config-invalid",
        scene=True,
    )
    assert isinstance(resolved, dict)
    _expect_json_exact(
        summary.get("resolved_config"),
        resolved,
        f"trace-header:{method}:summary-resolved-config-mismatch",
        scene=True,
    )
    _validate_resolved_config(resolved, method, scene, context)
    return header, _validate_positions_trace(snapshot, method)


def _validate_summary_schema(
    summary: Mapping[str, Any],
    method: str,
    scene: Mapping[str, Any],
    trace_path: Path,
) -> None:
    _expect(
        set(summary) == SUMMARY_KEYS,
        f"summary:{method}:fields-mismatch",
        scene=True,
    )
    for key, expected in (
        ("schema_version", "dancers-comparison-summary-v1"),
        ("trace_detail", "positions"),
        ("frames_recorded", FRAMES_PER_PAIR),
        ("final_tick", TIMESTEPS),
        ("final_robot_count", ROBOT_COUNT),
        ("maximum_robot_count", ROBOT_COUNT),
        ("termination_reason", "maximum_time"),
    ):
        _expect_json_exact(
            summary.get(key),
            expected,
            f"summary:{method}:{key}-mismatch",
            scene=True,
        )
    declared_trace = summary.get("trace_path")
    _expect_json_exact(
        declared_trace,
        str(trace_path),
        f"summary:{method}:trace-path-mismatch",
        scene=True,
    )
    _expect(
        _finite_float(summary.get("simulated_time_seconds"))
        and math.isclose(
            float(summary["simulated_time_seconds"]),
            SIMULATED_DURATION_SECONDS,
            rel_tol=0.0,
            abs_tol=1e-6,
        ),
        f"summary:{method}:simulated-time-mismatch",
        scene=True,
    )
    missing = [field for field in SUMMARY_PARITY_FIELDS if field not in summary]
    _expect(not missing, f"summary:{method}:parity-fields-missing", scene=True)
    paper = summary.get("paper_metrics")
    _expect(
        isinstance(paper, dict) and set(paper) == PAPER_METRIC_FIELDS,
        f"summary:{method}:paper-metrics-fields-mismatch",
        scene=True,
    )
    assert isinstance(paper, dict)
    for key in ("consensus_reached", "final_decision", "final_iterations"):
        _expect(
            _is_integer(paper.get(key)),
            f"summary:{method}:paper-{key}-invalid",
            scene=True,
        )
    for key in ("final_connectivity", "final_heading_error", "final_position_error"):
        _expect(
            _finite_float(paper.get(key)),
            f"summary:{method}:paper-{key}-invalid",
            scene=True,
        )
    integer_fields = (
        "goals_reached",
        "goals_total",
        "iterations",
        "obstacle_collision_events",
        "pair_collision_events",
        "invalid_geometry_evaluations",
        "invalid_robot_frames",
        "nonfinite_numeric_observations",
        "nonfinite_state_observations",
    )
    for key in integer_fields:
        _expect(
            _is_integer(summary.get(key)) and summary[key] >= 0,
            f"summary:{method}:{key}-invalid",
            scene=True,
        )
    for key in (
        "total_path_length_m",
        "mean_path_length_m_per_robot",
        "obstacle_collision_exposure_seconds",
        "pair_collision_exposure_seconds",
    ):
        _expect(
            _finite_float(summary.get(key)) and float(summary[key]) >= 0.0,
            f"summary:{method}:{key}-invalid",
            scene=True,
        )
    for key in ("minimum_obstacle_clearance_m", "minimum_pair_clearance_m"):
        _expect(
            _finite_float(summary.get(key)),
            f"summary:{method}:{key}-invalid",
            scene=True,
        )
    for key in ("all_goals_reached", "run_valid"):
        _expect(
            isinstance(summary.get(key), bool),
            f"summary:{method}:{key}-invalid",
            scene=True,
        )
    _expect_json_exact(
        summary.get("goals_total"),
        ROBOT_COUNT,
        f"summary:{method}:goals-total-mismatch",
        scene=True,
    )
    _expect(
        summary["goals_reached"] <= summary["goals_total"],
        f"summary:{method}:goals-reached-exceeds-total",
        scene=True,
    )
    first_tick = summary.get("first_all_goals_tick")
    _expect(
        first_tick is None
        or (
            _is_integer(first_tick)
            and 0 <= first_tick <= TIMESTEPS
        ),
        f"summary:{method}:first-all-goals-tick-invalid",
        scene=True,
    )
    all_goals = summary["all_goals_reached"]
    if all_goals:
        _expect(
            summary["goals_reached"] == ROBOT_COUNT
            and first_tick is not None,
            f"summary:{method}:all-goals-incoherent",
            scene=True,
        )
    else:
        _expect(
            summary["goals_reached"] < ROBOT_COUNT
            and first_tick is None,
            f"summary:{method}:incomplete-goals-incoherent",
            scene=True,
        )
    expected_robot_ids = {str(index) for index in range(ROBOT_COUNT)}
    per_path = summary.get("per_robot_path_length_m")
    _expect(
        isinstance(per_path, dict)
        and set(per_path) == expected_robot_ids
        and all(
            _finite_float(value) and float(value) >= 0.0
            for value in per_path.values()
        ),
        f"summary:{method}:per-robot-path-invalid",
        scene=True,
    )
    assert isinstance(per_path, dict)
    _expect(
        math.isclose(
            float(summary["total_path_length_m"]),
            sum(float(value) for value in per_path.values()),
            rel_tol=0.0,
            abs_tol=1e-9,
        )
        and math.isclose(
            float(summary["mean_path_length_m_per_robot"]),
            float(summary["total_path_length_m"]) / ROBOT_COUNT,
            rel_tol=0.0,
            abs_tol=1e-9,
        ),
        f"summary:{method}:path-aggregates-incoherent",
        scene=True,
    )
    per_goal = summary.get("per_robot_goal_tick")
    _expect(
        isinstance(per_goal, dict)
        and set(per_goal).issubset(expected_robot_ids)
        and len(per_goal) == summary["goals_reached"]
        and all(
            _is_integer(value) and 0 <= value <= TIMESTEPS
            for value in per_goal.values()
        ),
        f"summary:{method}:per-robot-goal-invalid",
        scene=True,
    )
    assert isinstance(per_goal, dict)
    if all_goals:
        _expect(
            first_tick == max(per_goal.values()),
            f"summary:{method}:first-goal-tick-incoherent",
            scene=True,
        )
    _expect(
        isinstance(summary.get("metadata"), dict),
        f"summary:{method}:metadata-invalid",
        scene=True,
    )
    _expect(
        isinstance(summary.get("resolved_config"), dict),
        f"summary:{method}:resolved-config-invalid",
        scene=True,
    )


def _trace_task_evidence(
    summary: Mapping[str, Any],
    frames: Sequence[Mapping[str, Any]],
    method: str,
) -> dict[str, Any]:
    frame_metrics = [frame["metrics"] for frame in frames]
    per_robot_path_lengths = {
        str(robot_id): 0.0 for robot_id in range(ROBOT_COUNT)
    }
    for previous_frame, current_frame in zip(frames, frames[1:]):
        for robot_id in range(ROBOT_COUNT):
            previous_state = previous_frame["robots"][robot_id]["state"]
            current_state = current_frame["robots"][robot_id]["state"]
            per_robot_path_lengths[str(robot_id)] += math.hypot(
                float(current_state[0]) - float(previous_state[0]),
                float(current_state[1]) - float(previous_state[1]),
            )
    total_path_length = sum(per_robot_path_lengths.values())
    pair_collision_present = any(
        metrics["pair_collisions"] > 0 for metrics in frame_metrics
    )
    obstacle_collision_present = any(
        metrics["obstacle_collisions"] > 0 for metrics in frame_metrics
    )
    final_metrics = frame_metrics[-1]
    pair_clearances = [
        float(metrics["frame_min_pair_clearance_m"])
        for metrics in frame_metrics
        if metrics["frame_min_pair_clearance_m"] is not None
    ]
    obstacle_clearances = [
        float(metrics["frame_min_obstacle_clearance_m"])
        for metrics in frame_metrics
        if metrics["frame_min_obstacle_clearance_m"] is not None
    ]
    _expect(
        bool(pair_clearances) and bool(obstacle_clearances),
        f"trace-task:{method}:clearance-series-empty",
        scene=True,
    )
    goal_ticks: dict[str, int] = {}
    for frame in frames:
        for robot in frame["robots"]:
            if robot["goal_reached"]:
                goal_ticks.setdefault(str(robot["rid"]), frame["tick"])
    final_goal_count = sum(
        robot["goal_reached"] for robot in frames[-1]["robots"]
    )
    evidence = {
        "all_goals_reached": final_goal_count == ROBOT_COUNT,
        "first_all_goals_tick": (
            max(goal_ticks.values())
            if final_goal_count == ROBOT_COUNT
            else None
        ),
        "goals_reached": final_goal_count,
        "invalid_geometry_evaluations": final_metrics[
            "cumulative_invalid_geometry_evaluations"
        ],
        "minimum_obstacle_clearance_m": min(obstacle_clearances),
        "minimum_pair_clearance_m": min(pair_clearances),
        "obstacle_collision_exposure_seconds": final_metrics[
            "cumulative_obstacle_collision_exposure_seconds"
        ],
        "obstacle_collision_frame_count": sum(
            metrics["obstacle_collisions"] > 0 for metrics in frame_metrics
        ),
        "obstacle_collision_present": obstacle_collision_present,
        "pair_collision_exposure_seconds": final_metrics[
            "cumulative_pair_collision_exposure_seconds"
        ],
        "pair_collision_frame_count": sum(
            metrics["pair_collisions"] > 0 for metrics in frame_metrics
        ),
        "pair_nonpositive_sweep_frame_count": sum(
            metrics["frame_min_pair_clearance_m"] is not None
            and float(metrics["frame_min_pair_clearance_m"]) <= 0.0
            for metrics in frame_metrics
        ),
        "pair_collision_present": pair_collision_present,
        "per_robot_path_length_m": per_robot_path_lengths,
        "per_robot_goal_tick": goal_ticks,
        "total_path_length_m": total_path_length,
        "mean_path_length_m_per_robot": total_path_length / ROBOT_COUNT,
    }
    for field in (
        "all_goals_reached",
        "goals_reached",
        "invalid_geometry_evaluations",
        "minimum_obstacle_clearance_m",
        "minimum_pair_clearance_m",
        "obstacle_collision_exposure_seconds",
        "pair_collision_exposure_seconds",
    ):
        observed = evidence[field]
        declared = summary[field]
        matches = (
            math.isclose(
                float(observed),
                float(declared),
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            if _finite_number(observed) and _finite_number(declared)
            else _json_exact(observed, declared)
        )
        _expect(
            matches,
            f"trace-task:{method}:{field}-summary-mismatch",
            scene=True,
        )
    _expect_json_exact(
        summary["first_all_goals_tick"],
        evidence["first_all_goals_tick"],
        f"trace-task:{method}:first-all-goals-tick-summary-mismatch",
        scene=True,
    )
    _expect_json_exact(
        summary["per_robot_goal_tick"],
        goal_ticks,
        f"trace-task:{method}:per-robot-goal-tick-summary-mismatch",
        scene=True,
    )
    declared_path_lengths = summary["per_robot_path_length_m"]
    for robot_id, observed in per_robot_path_lengths.items():
        _expect(
            math.isclose(
                observed,
                float(declared_path_lengths[robot_id]),
                rel_tol=0.0,
                abs_tol=1e-12,
            ),
            (
                f"trace-task:{method}:per-robot-path-length-{robot_id}-"
                "summary-mismatch"
            ),
            scene=True,
        )
    for field in (
        "total_path_length_m",
        "mean_path_length_m_per_robot",
    ):
        _expect(
            math.isclose(
                float(evidence[field]),
                float(summary[field]),
                rel_tol=0.0,
                abs_tol=1e-12,
            ),
            f"trace-task:{method}:{field}-summary-mismatch",
            scene=True,
        )
    _expect(
        pair_collision_present
        == (summary["pair_collision_events"] > 0),
        f"trace-task:{method}:pair-collision-presence-summary-mismatch",
        scene=True,
    )
    _expect(
        obstacle_collision_present
        == (summary["obstacle_collision_events"] > 0),
        f"trace-task:{method}:obstacle-collision-presence-summary-mismatch",
        scene=True,
    )
    return evidence


def _effective_input_sha256(scene: Mapping[str, Any]) -> str:
    """Bind the map and file-scenario that determine one effective mission."""
    return _sha256(
        canonical_json_bytes(
            {
                "map_sha256": scene["map"]["sha256"],
                "scenario_sha256": scene["scenario"]["sha256"],
            }
        )
    )


def _normalized_trajectory_sha256(
    frames: Sequence[Mapping[str, Any]],
) -> str:
    """Hash motion/goal evidence while deliberately excluding RNG provenance."""
    normalized = []
    for frame in frames:
        normalized.append(
            {
                "phase": frame["phase"],
                "robots": [
                    {
                        "current_waypoint": robot["current_waypoint"],
                        "final_goal": robot["final_goal"],
                        "goal_reached": robot["goal_reached"],
                        "radius_m": robot["radius_m"],
                        "rid": robot["rid"],
                        "state_hex": robot["state_hex"],
                    }
                    for robot in frame["robots"]
                ],
                "simulated_time_seconds": frame["simulated_time_seconds"],
                "tick": frame["tick"],
            }
        )
    return _sha256(canonical_json_bytes(normalized))


def _goal_horizon_evidence(
    frames: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Prove whether fixed horizon/speed can reach even the nearest goal."""
    _expect(bool(frames), "replication:goal-horizon-frames-empty", scene=True)
    start = frames[0]
    distances = []
    required_centre_travel = []
    radii = []
    for robot in start["robots"]:
        final_goal = robot["final_goal"]
        state = robot["state"]
        radius = float(robot["radius_m"])
        distance = math.hypot(
            float(state[0]) - float(final_goal[0]),
            float(state[1]) - float(final_goal[1]),
        )
        distances.append(distance)
        radii.append(radius)
        required_centre_travel.append(max(0.0, distance - radius))
    _expect(
        all(radius == ROBOT_RADIUS_METRES for radius in radii),
        "replication:goal-horizon-radius-mismatch",
        scene=True,
    )
    maximum_displacement = (
        MAX_SPEED_METRES_PER_SECOND * SIMULATED_DURATION_SECONDS
    )
    censored = all(
        distance > maximum_displacement
        for distance in required_centre_travel
    )
    return {
        "all_required_centre_travel_exceeds_maximum_displacement": censored,
        "goal_reach_radius_m": ROBOT_RADIUS_METRES,
        "goal_completion_inference_permitted": not censored,
        "interpretation": (
            "structurally-censored-by-fixed-speed-and-horizon"
            if censored
            else "not-structurally-censored"
        ),
        "maximum_kinematic_displacement_m": maximum_displacement,
        "minimum_required_centre_travel_to_goal_m": min(
            required_centre_travel
        ),
        "minimum_start_to_final_goal_distance_m": min(distances),
    }


def _task_violations(
    summary: Mapping[str, Any],
    method: str,
    trace_evidence: Mapping[str, Any],
) -> list[str]:
    violations: list[str] = []
    exact_values = (
        ("termination_reason", "maximum_time"),
        ("final_tick", TIMESTEPS),
        ("frames_recorded", FRAMES_PER_PAIR),
        ("final_robot_count", ROBOT_COUNT),
        ("maximum_robot_count", ROBOT_COUNT),
        ("goals_total", ROBOT_COUNT),
        ("trace_detail", "positions"),
        ("run_valid", True),
    )
    for field, expected in exact_values:
        if summary.get(field) != expected:
            violations.append(f"{method}.{field}:expected-{expected}")
    if not math.isclose(
        float(summary["simulated_time_seconds"]),
        SIMULATED_DURATION_SECONDS,
        rel_tol=0.0,
        abs_tol=1e-9,
    ):
        violations.append(f"{method}.simulated_time_seconds:expected-10")
    zero_integer_fields = (
        "invalid_geometry_evaluations",
        "invalid_robot_frames",
        "nonfinite_numeric_observations",
        "nonfinite_state_observations",
        "obstacle_collision_events",
        "pair_collision_events",
    )
    for field in zero_integer_fields:
        if summary[field] != 0:
            violations.append(f"{method}.{field}:expected-zero")
    for field in (
        "obstacle_collision_exposure_seconds",
        "pair_collision_exposure_seconds",
    ):
        if float(summary[field]) != 0.0:
            violations.append(f"{method}.{field}:expected-zero")
    for field in ("minimum_obstacle_clearance_m", "minimum_pair_clearance_m"):
        if float(summary[field]) <= 0.0:
            violations.append(f"{method}.{field}:not-strictly-positive")
    for field in ("pair_collision_present", "obstacle_collision_present"):
        if trace_evidence[field]:
            violations.append(f"{method}.trace.{field}:observed")
    for field in (
        "pair_collision_exposure_seconds",
        "obstacle_collision_exposure_seconds",
    ):
        if float(trace_evidence[field]) != 0.0:
            violations.append(f"{method}.trace.{field}:expected-zero")
    if trace_evidence["invalid_geometry_evaluations"] != 0:
        violations.append(
            f"{method}.trace.invalid_geometry_evaluations:expected-zero"
        )
    for field in ("minimum_pair_clearance_m", "minimum_obstacle_clearance_m"):
        if float(trace_evidence[field]) <= 0.0:
            violations.append(f"{method}.trace.{field}:not-strictly-positive")
    return violations


def _recompute_pair_diagnostics(
    reference_frames: Sequence[Mapping[str, Any]],
    optimized_frames: Sequence[Mapping[str, Any]],
    reference_summary: Mapping[str, Any],
    paper_equal: bool,
    summary_equal: bool,
) -> dict[str, Any]:
    discordant = 0
    aligned_robot_frames = 0
    max_position_delta = 0.0
    max_heading_delta = 0.0
    for left_frame, right_frame in zip(reference_frames, optimized_frames):
        left_robots = {robot["rid"]: robot for robot in left_frame["robots"]}
        right_robots = {robot["rid"]: robot for robot in right_frame["robots"]}
        for robot_id in sorted(left_robots):
            left = left_robots[robot_id]
            right = right_robots[robot_id]
            aligned_robot_frames += 1
            if left["state_hex"] != right["state_hex"]:
                discordant += 1
            left_state = left["state"]
            right_state = right["state"]
            max_position_delta = max(
                max_position_delta,
                abs(float(left_state[0]) - float(right_state[0])),
                abs(float(left_state[1]) - float(right_state[1])),
            )
            heading_delta = float(left_state[2]) - float(right_state[2])
            wrapped = math.atan2(math.sin(heading_delta), math.cos(heading_delta))
            max_heading_delta = max(max_heading_delta, abs(wrapped))
    return {
        "aligned_frame_count": len(reference_frames),
        "aligned_robot_frame_count": aligned_robot_frames,
        "all_goals_reached": reference_summary["all_goals_reached"],
        "discordant_robot_frame_count": discordant,
        "first_all_goals_tick": reference_summary["first_all_goals_tick"],
        "goals_reached": reference_summary["goals_reached"],
        "goals_total": reference_summary["goals_total"],
        "max_abs_position_delta_m": max_position_delta,
        "max_abs_wrapped_heading_delta_rad": max_heading_delta,
        "mean_path_length_m_per_robot": reference_summary[
            "mean_path_length_m_per_robot"
        ],
        "minimum_obstacle_clearance_m": reference_summary[
            "minimum_obstacle_clearance_m"
        ],
        "minimum_pair_clearance_m": reference_summary[
            "minimum_pair_clearance_m"
        ],
        "obstacle_collision_events": reference_summary[
            "obstacle_collision_events"
        ],
        "obstacle_collision_exposure_seconds": reference_summary[
            "obstacle_collision_exposure_seconds"
        ],
        "pair_collision_events": reference_summary["pair_collision_events"],
        "pair_collision_exposure_seconds": reference_summary[
            "pair_collision_exposure_seconds"
        ],
        "paper_metrics_equal": paper_equal,
        "run_valid": reference_summary["run_valid"],
        "state_hex_equal": discordant == 0,
        "summary_parity_fields_equal": summary_equal,
        "total_path_length_m": reference_summary["total_path_length_m"],
    }


def _validate_manifest_and_scene(
    context: PlanContext,
    scene: Mapping[str, Any],
    scene_root: Path,
    snapshots: Mapping[str, FileSnapshot],
    tree_evidence: Mapping[str, Any],
    trace_comparator: TraceComparator,
    viewer_builder: ViewerBuilder,
) -> dict[str, Any]:
    manifest = _parse_canonical_json(
        snapshots["manifest.json"].data,
        "manifest",
        scene=True,
    )
    _assert_no_resource_claim_keys(manifest)
    _expect(
        set(manifest) == MANIFEST_KEYS,
        "manifest:fields-mismatch",
        scene=True,
    )
    expected_execution_lease = {
        "held_during_execution": True,
        "manual_recovery_if_present": True,
        "path": (
            "results/simulator-demo/v1/visual-diagnostic/"
            ".calibration-v2-execution.lease"
        ),
        "policy": (
            "exclusive-create-no-follow-inode-bound-crash-persists-"
            "manual-recovery"
        ),
        "schema_version": "dancers-simulator-visual-execution-lease-v1",
    }
    for key, expected in (
        ("schema_version", RESULT_SCHEMA),
        ("status", "complete"),
        ("plan_id", PLAN_ID),
        ("plan_sha256", PLAN_SHA256),
        ("execution_epoch_id", EXECUTION_EPOCH_ID),
        ("run_id_prefix", EXECUTION_EPOCH_ID),
        ("track_id", TRACK_ID),
        ("phase", "calibration"),
        ("run_class", "positions-replay"),
        ("diagnostic_only", True),
        ("compute_claim", False),
        ("paper_replication_evidence", False),
        ("timing_unlock", False),
        ("rendering_excluded_from_compute", True),
        ("output_schemas", context.plan["output_schemas"]),
        ("config_cli_argument", scene["config_cli_argument"]),
        ("config_cli_path_policy", "repository-relative-no-whitespace"),
        ("contract_id", "dancers-simulator-demo-v1"),
        ("contract_sha256", context.plan["contract"]["sha256"]),
        ("execution_lease", expected_execution_lease),
        ("failure_policy", "seal-stop-new-epoch-no-resume"),
        ("output_schemas", context.plan["output_schemas"]),
        ("recovery", context.plan["recovery"]),
        ("run_ids", scene["run_ids"]),
    ):
        _expect_json_exact(
            manifest.get(key),
            expected,
            f"manifest:{key}-mismatch",
            scene=True,
        )

    frozen_plan = snapshots["frozen_plan.json"]
    _expect(
        frozen_plan.data == context.plan_bytes,
        "frozen-plan-copy:bytes-mismatch",
        scene=True,
    )
    preflight = _parse_canonical_json(
        snapshots["preflight.json"].data,
        "preflight",
        scene=True,
    )
    _validate_preflight(preflight, scene, context)

    manifest_scene = manifest.get("scene")
    _expect(isinstance(manifest_scene, dict), "manifest:scene-invalid", scene=True)
    assert isinstance(manifest_scene, dict)
    _expect(
        set(manifest_scene) == MANIFEST_SCENE_KEYS,
        "manifest:scene-fields-mismatch",
        scene=True,
    )
    for key, expected in (
        ("family", scene["family"]),
        ("robot_count", ROBOT_COUNT),
        ("seed", scene["seed"]),
        ("scene_id", scene["scene_id"]),
        ("map", scene["map"]),
        ("scenario", scene["scenario"]),
        ("run_ids", scene["run_ids"]),
        ("config_cli_argument", scene["config_cli_argument"]),
        ("timesteps", TIMESTEPS),
        ("timestep_seconds", 0.1),
        ("simulated_duration_seconds", 10),
        (
            "environment_descriptors",
            context.plan["environment_descriptors"][scene["family"]],
        ),
    ):
        _expect_json_exact(
            manifest_scene.get(key),
            expected,
            f"manifest:scene-{key}-mismatch",
            scene=True,
        )
    _artifact_record_matches(
        manifest_scene.get("config"),
        snapshots["config.yaml"],
        "config.yaml",
        "manifest:scene-config",
    )
    try:
        expected_config = _expected_config_bytes(context, scene, scene_root)
    except AnalysisError as error:
        raise SceneError("scene-config:independent-render-failed") from error
    _expect(
        snapshots["config.yaml"].data == expected_config,
        "scene-config:plan-render-mismatch",
        scene=True,
    )

    process_policy = manifest.get("process_policy")
    _expect(isinstance(process_policy, dict), "manifest:process-policy-invalid", scene=True)
    assert isinstance(process_policy, dict)
    _expect_json_exact(
        process_policy,
        {
            "config_cli_path_policy": "repository-relative-no-whitespace",
            "failure_policy": "seal-stop-new-epoch-no-resume",
            "fresh_process_per_method": True,
            "method_order": list(METHODS),
            "serial_execution": True,
            "shared_solver_state": False,
            "viewer_started_after_both_processes_exited": True,
        },
        "manifest:process-policy-mismatch",
        scene=True,
    )

    summaries: dict[str, dict[str, Any]] = {}
    trace_headers: dict[str, dict[str, Any]] = {}
    trace_frames: dict[str, list[dict[str, Any]]] = {}
    trace_task_evidence: dict[str, dict[str, Any]] = {}
    manifest_methods = manifest.get("methods")
    _expect(
        isinstance(manifest_methods, dict) and set(manifest_methods) == set(METHODS),
        "manifest:methods-invalid",
        scene=True,
    )
    assert isinstance(manifest_methods, dict)
    for method in METHODS:
        method_manifest = manifest_methods[method]
        _expect(isinstance(method_manifest, dict), f"manifest:{method}-invalid", scene=True)
        assert isinstance(method_manifest, dict)
        _expect(
            set(method_manifest)
            == {
                "artifacts",
                "binary",
                "config_cli_argument",
                "fresh_process",
                "run_id",
                "source_revision",
                "terminal_category",
            },
            f"manifest:{method}:fields-mismatch",
            scene=True,
        )
        method_plan = context.plan["methods"][method]
        for key, expected in (
            ("binary", method_plan["binary"]),
            ("source_revision", method_plan["source_revision"]),
            ("run_id", scene["run_ids"][method]),
            ("config_cli_argument", scene["config_cli_argument"]),
            ("fresh_process", True),
            ("terminal_category", "completed"),
        ):
            _expect_json_exact(
                method_manifest.get(key),
                expected,
                f"manifest:{method}-{key}-mismatch",
                scene=True,
            )
        artifacts = method_manifest.get("artifacts")
        _expect(
            isinstance(artifacts, dict)
            and set(artifacts) == {name.removesuffix(".json").removesuffix(".txt").removesuffix(".jsonl") for name in METHOD_ARTIFACT_NAMES},
            f"manifest:{method}-artifacts-invalid",
            scene=True,
        )
        assert isinstance(artifacts, dict)
        artifact_names = {
            "metadata": "metadata.json",
            "stderr": "stderr.txt",
            "stdout": "stdout.txt",
            "summary": "summary.json",
            "trace": "trace.jsonl",
        }
        for key, filename in artifact_names.items():
            relative = f"{method}/{filename}"
            _artifact_record_matches(
                artifacts.get(key),
                snapshots[relative],
                relative,
                f"manifest:{method}-{key}",
            )
        _expect(
            snapshots[f"{method}/stderr.txt"].bytes == 0,
            f"manifest:{method}-stderr-not-empty",
            scene=True,
        )
        metadata = _parse_canonical_json(
            snapshots[f"{method}/metadata.json"].data,
            f"metadata:{method}",
            scene=True,
        )
        summary = _parse_canonical_json(
            snapshots[f"{method}/summary.json"].data,
            f"summary:{method}",
            scene=True,
            require_canonical=False,
        )
        trace_path = scene_root / method / "trace.jsonl"
        _validate_summary_schema(summary, method, scene, trace_path)
        _expect_json_exact(
            summary.get("metadata"),
            metadata,
            f"summary:{method}:metadata-copy-mismatch",
            scene=True,
        )
        _validate_metadata(
            metadata,
            method,
            scene,
            scene_root,
            snapshots["config.yaml"].sha256,
            context,
        )
        trace_header, frames = _validate_trace_header(
            snapshots[f"{method}/trace.jsonl"],
            method,
            scene,
            metadata,
            summary,
            context,
        )
        trace_headers[method] = trace_header
        trace_frames[method] = frames
        trace_task_evidence[method] = _trace_task_evidence(
            summary,
            frames,
            method,
        )
        summaries[method] = summary

    replay = manifest.get("replay")
    _expect(isinstance(replay, dict), "manifest:replay-invalid", scene=True)
    assert isinstance(replay, dict)
    replay_snapshot = snapshots["replay.html"]
    _expect_json_exact(
        replay,
        {
            "bytes": replay_snapshot.bytes,
            "path": "replay.html",
            "sha256": replay_snapshot.sha256,
            "trace_compare_sha256": context.plan["artifacts"][
                "trace_compare"
            ]["sha256"],
            "viewer_sha256": context.plan["artifacts"]["viewer"]["sha256"],
        },
        "manifest:replay-record-mismatch",
        scene=True,
    )
    try:
        replay_text = replay_snapshot.data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SceneError("replay:utf8-invalid") from error
    for badge in REQUIRED_REPLAY_BADGES:
        _expect(badge in replay_text, f"replay:badge-missing:{badge}", scene=True)
    try:
        render_result = viewer_builder(
            scene_root / "reference/trace.jsonl",
            scene_root / "optimized/trace.jsonl",
            context.repo_root / scene["map"]["path"],
            title=(
                "DANCeRS diagnostic replay · "
                f"{scene['family']} · N={ROBOT_COUNT} · seed={scene['seed']}"
            ),
            environment_family=scene["family"],
            environment_descriptors=context.plan["environment_descriptors"][
                scene["family"]
            ],
        )
    except Exception as error:
        raise SceneError("replay:verified-render-failed") from error
    expected_replay = getattr(render_result, "html_bytes", None)
    declared_replay_bytes = getattr(render_result, "output_bytes", None)
    declared_frame_count = getattr(render_result, "frame_count", None)
    declared_robot_samples = getattr(render_result, "robot_samples", None)
    declared_exact_match = getattr(
        render_result, "exact_semantic_match", None
    )
    declared_mismatch_tick = getattr(render_result, "mismatch_tick", None)
    _expect(
        isinstance(expected_replay, bytes)
        and bool(expected_replay)
        and _is_integer(declared_replay_bytes)
        and declared_replay_bytes == len(expected_replay)
        and _json_exact(declared_frame_count, FRAMES_PER_PAIR)
        and _json_exact(
            declared_robot_samples,
            FRAMES_PER_PAIR * ROBOT_COUNT * len(METHODS),
        )
        and _json_exact(declared_exact_match, True)
        and declared_mismatch_tick is None,
        "replay:verified-render-result-invalid",
        scene=True,
    )
    _expect(
        replay_snapshot.data == expected_replay,
        "replay:verified-render-bytes-mismatch",
        scene=True,
    )

    reference_summary = summaries["reference"]
    optimized_summary = summaries["optimized"]
    paper_equal = canonical_json_bytes(reference_summary["paper_metrics"]) == canonical_json_bytes(
        optimized_summary["paper_metrics"]
    )
    reference_parity = {field: reference_summary[field] for field in SUMMARY_PARITY_FIELDS}
    optimized_parity = {field: optimized_summary[field] for field in SUMMARY_PARITY_FIELDS}
    summary_equal = canonical_json_bytes(reference_parity) == canonical_json_bytes(optimized_parity)
    trace_comparison = trace_comparator(
        scene_root / "reference/trace.jsonl",
        scene_root / "optimized/trace.jsonl",
    )
    _expect_json_exact(
        trace_comparison.left_provenance,
        trace_headers["reference"]["metadata"],
        "trace-comparison:left-provenance-mismatch",
        scene=True,
    )
    _expect_json_exact(
        trace_comparison.right_provenance,
        trace_headers["optimized"]["metadata"],
        "trace-comparison:right-provenance-mismatch",
        scene=True,
    )
    trace_equal = trace_comparison.equal
    frames_compared = trace_comparison.frames_compared
    _expect(
        trace_equal
        and frames_compared == FRAMES_PER_PAIR
        and paper_equal
        and summary_equal,
        "sealed-complete:parity-not-exact",
        scene=True,
    )
    pair_validation = manifest.get("pair_validation")
    _expect(isinstance(pair_validation, dict), "manifest:pair-validation-invalid", scene=True)
    assert isinstance(pair_validation, dict)
    diagnostics = _recompute_pair_diagnostics(
        trace_frames["reference"],
        trace_frames["optimized"],
        reference_summary,
        paper_equal,
        summary_equal,
    )
    _expect(
        set(diagnostics) == PAIR_DIAGNOSTIC_KEYS,
        "manifest:diagnostics-recomputed-fields-mismatch",
        scene=True,
    )
    _expect_json_exact(
        pair_validation,
        {
            "diagnostics": diagnostics,
            "frames_compared": frames_compared,
            "paper_metrics_equal": paper_equal,
            "semantic_trace_equal": trace_equal,
            "summary_parity_fields_equal": summary_equal,
        },
        "manifest:pair-validation-recomputed-mismatch",
        scene=True,
    )

    parity_exact = (
        trace_equal
        and paper_equal
        and summary_equal
        and frames_compared == FRAMES_PER_PAIR
    )
    task_violations: list[str] = []
    method_task_states: dict[str, str] = {}
    for method in METHODS:
        violations = _task_violations(
            summaries[method],
            method,
            trace_task_evidence[method],
        )
        task_violations.extend(violations)
        method_task_states[method] = (
            "invalid"
            if violations
            else "not_independently_verified"
        )
    task_state = (
        "invalid"
        if task_violations
        else "not_independently_verified"
    )
    replication_evidence = {
        "effective_input_sha256": _effective_input_sha256(scene),
        "goal_horizon": _goal_horizon_evidence(
            trace_frames["reference"]
        ),
        "normalized_trajectory_sha256": _normalized_trajectory_sha256(
            trace_frames["reference"]
        ),
    }

    _verify_scene_inputs_unchanged(
        context,
        scene,
        scene_root,
        snapshots,
        tree_evidence,
    )

    return {
        "authentication": {"state": "authenticated"},
        "diagnostics": {
            "all_goals_reached": trace_task_evidence["reference"][
                "all_goals_reached"
            ],
            "goals_reached": trace_task_evidence["reference"][
                "goals_reached"
            ],
            "goals_total": ROBOT_COUNT,
            "mean_path_length_m_per_robot": trace_task_evidence[
                "reference"
            ]["mean_path_length_m_per_robot"],
            "minimum_pair_clearance_m": trace_task_evidence["reference"][
                "minimum_pair_clearance_m"
            ],
            "obstacle_safety_state": "not_independently_verified",
            "pair_collision_exposure_seconds": trace_task_evidence[
                "reference"
            ]["pair_collision_exposure_seconds"],
            "pair_collision_frame_count": trace_task_evidence["reference"][
                "pair_collision_frame_count"
            ],
            "pair_collision_present": trace_task_evidence["reference"][
                "pair_collision_present"
            ],
            "pair_nonpositive_sweep_frame_count": trace_task_evidence[
                "reference"
            ]["pair_nonpositive_sweep_frame_count"],
            "pair_safety_state": (
                "violated"
                if trace_task_evidence["reference"][
                    "pair_nonpositive_sweep_frame_count"
                ]
                else "verified_clear"
            ),
            "per_robot_path_length_m": trace_task_evidence["reference"][
                "per_robot_path_length_m"
            ],
            "total_path_length_m": trace_task_evidence["reference"][
                "total_path_length_m"
            ],
        },
        "evidence": {
            "manifest": {
                "bytes": snapshots["manifest.json"].bytes,
                "sha256": snapshots["manifest.json"].sha256,
            },
            "preflight": {
                "bytes": snapshots["preflight.json"].bytes,
                "sha256": snapshots["preflight.json"].sha256,
            },
            "replay": {
                "bytes": replay_snapshot.bytes,
                "sha256": replay_snapshot.sha256,
            },
            "tree": dict(tree_evidence),
        },
        "family": scene["family"],
        "output_relpath": scene["output_relpath"],
        "parity": {
            "frames_compared": frames_compared,
            "paper_metrics_equal": paper_equal,
            "semantic_trace_equal": trace_equal,
            "state": "exact" if parity_exact else "discordant",
            "summary_parity_fields_equal": summary_equal,
        },
        "robot_count": ROBOT_COUNT,
        "replication_evidence": replication_evidence,
        "scene_id": scene["scene_id"],
        "seed": scene["seed"],
        "task_validity": {
            "methods": method_task_states,
            "obstacle_safety_state": "not_independently_verified",
            "pair_safety_state": (
                "violated"
                if trace_task_evidence["reference"][
                    "pair_nonpositive_sweep_frame_count"
                ]
                else "verified_clear"
            ),
            "predicate_id": TASK_PREDICATE_ID,
            "state": task_state,
            "unverified_reasons": (
                ["obstacle-swept-disc-safety-not-independently-verified"]
                if task_state == "not_independently_verified"
                else []
            ),
            "violations": task_violations,
        },
    }


def _analyze_one_scene(
    context: PlanContext,
    scene: Mapping[str, Any],
    results_root: Path,
    trace_comparator: TraceComparator,
    viewer_builder: ViewerBuilder,
) -> dict[str, Any]:
    scene_root = results_root / scene["output_relpath"]
    try:
        os.lstat(scene_root)
    except FileNotFoundError:
        return _scene_missing(scene)
    except OSError as error:
        return _scene_failed(scene, f"tree-root-stat-failed:{error.errno}")
    try:
        snapshots, tree_evidence = _scene_tree(scene_root)
        return _validate_manifest_and_scene(
            context,
            scene,
            scene_root,
            snapshots,
            tree_evidence,
            trace_comparator,
            viewer_builder,
        )
    except SceneError as error:
        return _scene_failed(scene, error.code)


def _summarize(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    present = sum(record["authentication"]["state"] != "missing" for record in records)
    missing = sum(record["authentication"]["state"] == "missing" for record in records)
    authenticated = sum(record["authentication"]["state"] == "authenticated" for record in records)
    authentication_failed = sum(record["authentication"]["state"] == "failed" for record in records)
    parity_exact = sum(record["parity"]["state"] == "exact" for record in records)
    parity_discordant = sum(record["parity"]["state"] == "discordant" for record in records)
    task_valid = sum(record["task_validity"]["state"] == "valid" for record in records)
    task_invalid = sum(record["task_validity"]["state"] == "invalid" for record in records)
    task_unverified = sum(
        record["task_validity"]["state"] == "not_independently_verified"
        for record in records
    )
    complete = missing == 0 and authentication_failed == 0
    diagnostic_gate_pass = (
        complete
        and parity_exact == len(records)
        and task_valid == len(records)
    )
    if authentication_failed:
        status = "authentication-failed"
    elif missing and task_invalid:
        status = "incomplete-task-invalid"
    elif missing and task_unverified:
        status = "incomplete-task-unverified"
    elif missing:
        status = "incomplete"
    elif parity_discordant:
        status = "parity-discordant"
    elif task_invalid:
        status = "task-invalid"
    elif task_unverified:
        status = "task-not-independently-verified"
    else:
        status = "diagnostic-pass"
    return {
        "authenticated_scene_count": authenticated,
        "authentication_failed_scene_count": authentication_failed,
        "cohort_complete": complete,
        "diagnostic_gate_pass": diagnostic_gate_pass,
        "expected_scene_count": len(records),
        "missing_scene_count": missing,
        "parity_discordant_scene_count": parity_discordant,
        "parity_exact_scene_count": parity_exact,
        "present_scene_count": present,
        "status": status,
        "task_invalid_scene_count": task_invalid,
        "task_not_independently_verified_scene_count": task_unverified,
        "task_unevaluated_scene_count": (
            len(records) - task_valid - task_invalid - task_unverified
        ),
        "task_valid_scene_count": task_valid,
    }


def _summarize_replication(
    context: PlanContext,
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Separate nominal scene identities from effective missions/behaviors."""
    scene_by_id = {
        scene["scene_id"]: scene for scene in context.target_scenes
    }
    record_by_id = {record["scene_id"]: record for record in records}
    family_summaries = []
    all_trajectory_hashes: set[str] = set()
    all_horizon_records: dict[bytes, Mapping[str, Any]] = {}

    for family in FAMILIES:
        family_scenes = [
            scene
            for scene in context.target_scenes
            if scene["family"] == family
        ]
        family_records = [record_by_id[scene["scene_id"]] for scene in family_scenes]
        input_groups: dict[str, list[int]] = {}
        for scene in family_scenes:
            input_groups.setdefault(
                _effective_input_sha256(scene), []
            ).append(scene["seed"])

        authenticated = [
            record
            for record in family_records
            if record["authentication"]["state"] == "authenticated"
        ]
        trajectory_groups: dict[str, list[int]] = {}
        horizon_records: dict[bytes, Mapping[str, Any]] = {}
        for record in authenticated:
            evidence = record["replication_evidence"]
            trajectory_sha256 = evidence["normalized_trajectory_sha256"]
            trajectory_groups.setdefault(trajectory_sha256, []).append(
                record["seed"]
            )
            all_trajectory_hashes.add(trajectory_sha256)
            horizon = evidence["goal_horizon"]
            encoded_horizon = canonical_json_bytes(horizon)
            horizon_records[encoded_horizon] = horizon
            all_horizon_records[encoded_horizon] = horizon

        complete = len(authenticated) == len(family_scenes)
        inactive = (
            complete
            and len(input_groups) == 1
            and len(trajectory_groups) == 1
            and len(family_scenes) > 1
        )
        duplicate_groups = []
        if complete:
            for trajectory_sha256, seeds in sorted(trajectory_groups.items()):
                if len(seeds) > 1:
                    effective_inputs = {
                        record_by_id[
                            f"{family}-n{ROBOT_COUNT}-seed{seed:02d}"
                        ]["replication_evidence"]["effective_input_sha256"]
                        for seed in seeds
                    }
                    _expect(
                        len(effective_inputs) == 1,
                        f"replication:{family}:trajectory-spans-effective-inputs",
                    )
                    duplicate_groups.append(
                        {
                            "effective_input_sha256": next(iter(effective_inputs)),
                            "normalized_trajectory_sha256": trajectory_sha256,
                            "seeds": sorted(seeds),
                        }
                    )

        family_summaries.append(
            {
                "authenticated_scene_count": len(authenticated),
                "duplicate_seed_groups": duplicate_groups,
                "family": family,
                "goal_horizon": (
                    next(iter(horizon_records.values()))
                    if complete and len(horizon_records) == 1
                    else {"interpretation": "not-fully-evaluated"}
                ),
                "nominal_scene_count": len(family_scenes),
                "seed_axis_state": (
                    "inactive-for-observed-normalized-trajectory"
                    if inactive
                    else "not-fully-evaluated"
                ),
                "unique_effective_input_count": len(input_groups),
                "unique_normalized_trajectory_count": len(trajectory_groups),
            }
        )

    all_input_hashes = {
        _effective_input_sha256(scene)
        for scene in scene_by_id.values()
    }
    cohort_complete = all(
        record["authentication"]["state"] == "authenticated"
        for record in records
    )
    seed_axis_inactive = cohort_complete and all(
        family["seed_axis_state"]
        == "inactive-for-observed-normalized-trajectory"
        for family in family_summaries
    )
    all_goals_censored = cohort_complete and all(
        family["goal_horizon"].get(
            "all_required_centre_travel_exceeds_maximum_displacement"
        )
        is True
        for family in family_summaries
    )
    return {
        "collision_rate_estimation_permitted": False,
        "effective_mission_definition": (
            "unique-map-sha256-plus-scenario-sha256"
        ),
        "family_causal_effect_claim_permitted": False,
        "families": family_summaries,
        "goal_completion_inference_permitted": False,
        "goal_horizon_state": (
            "censored-for-all-effective-missions"
            if all_goals_censored
            else "not-fully-evaluated"
        ),
        "inferential_independence_claim": False,
        "nominal_scene_count": len(records),
        "pseudoreplication_present": seed_axis_inactive,
        "seed_axis_state": (
            "inactive-for-observed-file-scenario-zero-comms-cohort"
            if seed_axis_inactive
            else "not-fully-evaluated"
        ),
        "trajectory_signature_definition": (
            "phase-tick-time-and-robot-state-goal-projection-excluding-"
            "rng-and-provenance"
        ),
        "unique_effective_mission_count": len(all_input_hashes),
        "unique_normalized_trajectory_count": len(all_trajectory_hashes),
    }


def _require_epoch_lease_absent(repository: Path, phase: str) -> None:
    _require_no_symlink_components(
        repository,
        EPOCH_LEASE_RELATIVE_PATH,
        f"epoch-lease:{phase}",
        allow_missing=True,
    )
    lease_path = repository / EPOCH_LEASE_RELATIVE_PATH
    try:
        os.lstat(lease_path)
    except FileNotFoundError:
        return
    except OSError as error:
        raise AnalysisError(
            f"epoch-lease:{phase}:stat-failed:{error.errno}"
        ) from error
    raise AnalysisError(f"epoch-lease:{phase}:present")


def _audit_epoch_inventory(
    context: PlanContext,
    results_root: Path,
) -> None:
    try:
        root_metadata = os.lstat(results_root)
    except FileNotFoundError:
        return
    except OSError as error:
        raise AnalysisError(f"epoch-inventory:root-stat-failed:{error.errno}") from error
    _expect(
        stat.S_ISDIR(root_metadata.st_mode) and not stat.S_ISLNK(root_metadata.st_mode),
        "epoch-inventory:root-not-directory",
    )
    leaf_paths = {
        scene["output_relpath"] for scene in context.plan["scenes"]
    }
    allowed_directories = {"."}
    allowed_files: set[str] = set()
    for leaf in leaf_paths:
        parts = PurePosixPath(leaf).parts
        for count in range(1, len(parts) + 1):
            allowed_directories.add(PurePosixPath(*parts[:count]).as_posix())
        for method in METHODS:
            allowed_directories.add(f"{leaf}/{method}")
        for relative in REQUIRED_FILES:
            allowed_files.add(f"{leaf}/{relative}")

    actual_directories = {"."}
    actual_files: set[str] = set()
    stack: list[tuple[Path, PurePosixPath]] = [
        (results_root, PurePosixPath("."))
    ]
    while stack:
        directory, relative_directory = stack.pop()
        try:
            entries = list(os.scandir(directory))
        except OSError as error:
            raise AnalysisError(
                f"epoch-inventory:scan-failed:{error.errno}"
            ) from error
        for entry in entries:
            relative = (
                PurePosixPath(entry.name)
                if relative_directory.as_posix() == "."
                else relative_directory / entry.name
            )
            relative_text = relative.as_posix()
            try:
                if entry.is_symlink():
                    raise AnalysisError(
                        f"epoch-inventory:symlink:{relative_text}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    _expect(
                        relative_text in allowed_directories,
                        f"epoch-inventory:unknown-directory:{relative_text}",
                    )
                    actual_directories.add(relative_text)
                    stack.append((Path(entry.path), relative))
                elif entry.is_file(follow_symlinks=False):
                    _expect(
                        relative_text in allowed_files,
                        f"epoch-inventory:unknown-file:{relative_text}",
                    )
                    actual_files.add(relative_text)
                else:
                    raise AnalysisError(
                        f"epoch-inventory:special-entry:{relative_text}"
                    )
            except OSError as error:
                raise AnalysisError(
                    f"epoch-inventory:entry-stat-failed:{relative_text}:{error.errno}"
                ) from error

    actual_leaves = leaf_paths & actual_directories
    expected_present_directories = {"."}
    for leaf in actual_leaves:
        parts = PurePosixPath(leaf).parts
        for count in range(1, len(parts) + 1):
            expected_present_directories.add(
                PurePosixPath(*parts[:count]).as_posix()
            )
        expected_present_directories.update(
            {f"{leaf}/reference", f"{leaf}/optimized"}
        )
    _expect(
        actual_directories == expected_present_directories,
        "epoch-inventory:partial-empty-prefix",
    )
    expected_present_files = {
        f"{leaf}/{relative}"
        for leaf in actual_leaves
        for relative in REQUIRED_FILES
    }
    _expect(
        actual_files == expected_present_files,
        "epoch-inventory:partial-scene-files",
    )
    for leaf in sorted(actual_leaves):
        try:
            _scene_tree(results_root / leaf)
        except SceneError as error:
            raise AnalysisError(
                f"epoch-inventory:sealed-scene-invalid:{leaf}:{error.code}"
            ) from error


def _verify_scene_inputs_unchanged(
    context: PlanContext,
    scene: Mapping[str, Any],
    scene_root: Path,
    snapshots: Mapping[str, FileSnapshot],
    tree_evidence: Mapping[str, Any],
) -> None:
    later_snapshots, later_evidence = _scene_tree(scene_root)
    _expect_json_exact(
        dict(later_evidence),
        dict(tree_evidence),
        "scene-drift:tree-evidence-changed",
        scene=True,
    )
    _expect(
        set(later_snapshots) == set(snapshots)
        and all(
            later_snapshots[path] == snapshots[path]
            for path in snapshots
        ),
        "scene-drift:file-snapshot-changed",
        scene=True,
    )
    for artifact_key in ("map", "scenario"):
        registered = scene[artifact_key]
        try:
            data = _open_regular_file(
                context.repo_root / registered["path"],
                MAX_FILE_BYTES,
                f"scene-drift:{artifact_key}",
            )
        except AnalysisError as error:
            raise SceneError(f"scene-drift:{artifact_key}:read-failed") from error
        _expect(
            _sha256(data) == registered["sha256"],
            f"scene-drift:{artifact_key}:sha256-mismatch",
            scene=True,
        )


def analyze_visual_cohort(
    repo_root: Path | str,
    plan_path: Path | str | None = None,
    results_root: Path | str | None = None,
    *,
    trace_comparator: TraceComparator | None = None,
) -> dict[str, Any]:
    """Authenticate and classify the fixed 50-scene diagnostic cohort."""
    repository = _canonical_repository_root(repo_root)
    selected_plan = (
        (
            Path(plan_path)
            if Path(plan_path).is_absolute()
            else repository / Path(plan_path)
        )
        if plan_path is not None
        else repository / PLAN_RELATIVE_PATH
    )
    selected_results = (
        (
            Path(results_root)
            if Path(results_root).is_absolute()
            else repository / Path(results_root)
        )
        if results_root is not None
        else repository / RESULTS_RELATIVE_ROOT
    )
    selected_plan = _lexical_absolute(selected_plan)
    selected_results = _lexical_absolute(selected_results)
    canonical_plan = repository / PLAN_RELATIVE_PATH
    canonical_results = repository / RESULTS_RELATIVE_ROOT
    if selected_plan == canonical_plan:
        _require_no_symlink_components(
            repository,
            PLAN_RELATIVE_PATH,
            "frozen-plan",
            allow_missing=False,
        )
    if selected_results == canonical_results:
        _require_no_symlink_components(
            repository,
            RESULTS_RELATIVE_ROOT,
            "results-root",
            allow_missing=True,
        )
    _require_epoch_lease_absent(repository, "start")
    context = _validate_plan(repository, selected_plan)
    _audit_epoch_inventory(context, selected_results)
    comparator_runtime = _load_trace_comparator_runtime(
        context.trace_compare_path,
        context.trace_compare_bytes,
        context.trace_compare_sha256,
    )
    comparator = trace_comparator or comparator_runtime.compare
    viewer_builder = _load_replay_builder(
        context.viewer_path,
        context.viewer_bytes,
        context.viewer_sha256,
        comparator_runtime.module,
    )
    scene_records = [
        _analyze_one_scene(
            context,
            scene,
            selected_results,
            comparator,
            viewer_builder,
        )
        for scene in context.target_scenes
    ]
    summary = _summarize(scene_records)
    replication = _summarize_replication(context, scene_records)
    families = []
    for family in FAMILIES:
        family_records = [record for record in scene_records if record["family"] == family]
        family_summary = _summarize(family_records)
        family_summary["family"] = family
        families.append(family_summary)

    analyzer_path = Path(__file__)
    analyzer_bytes = _open_regular_file(
        analyzer_path,
        MAX_JSON_BYTES,
        "visual-cohort-analyzer",
    )
    dependency_inventory = list(context.dependencies)
    report = {
        "analysis_id": ANALYSIS_ID,
        "analyzer": {
            "bytes": len(analyzer_bytes),
            "path": ANALYZER_RELATIVE_PATH,
            "process_launch_surface": False,
            "sha256": _sha256(analyzer_bytes),
        },
        "families": families,
        "governance": {
            "compute_claim": False,
            "diagnostic_only": True,
            "paper_replication_evidence": False,
            "positions_trace_only": True,
            "scientific_admission": False,
            "task_validity_predicate_id": TASK_PREDICATE_ID,
            "timing_unlock": False,
            "validation_evidence": False,
        },
        "plan": {
            "authenticated": True,
            "bytes": len(context.plan_bytes),
            "dependency_count": len(dependency_inventory),
            "dependency_inventory": dependency_inventory,
            "dependency_inventory_sha256": _sha256(
                canonical_json_bytes(dependency_inventory)
            ),
            "path": PLAN_RELATIVE_PATH,
            "sha256": PLAN_SHA256,
        },
        "replication": replication,
        "scenes": scene_records,
        "schema_version": SCHEMA_VERSION,
        "scope": {
            "families": list(FAMILIES),
            "phase": "calibration",
            "results_root": RESULTS_RELATIVE_ROOT,
            "robot_count": ROBOT_COUNT,
            "seeds": list(SEEDS),
        },
        "summary": summary,
    }
    _require_epoch_lease_absent(repository, "end")
    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    replication = report["replication"]
    lines = [
        "# Visual diagnostic N=8 cohort analysis",
        "",
        "This is deterministic diagnostic replay evidence only. It is not timing, "
        "compute, capacity, validation, physical-safety, or paper-replication "
        "evidence and it never unlocks scientific claims.",
        "",
        "## Bound scope",
        "",
        f"- Plan SHA-256: `{report['plan']['sha256']}`",
        f"- Families: `{', '.join(report['scope']['families'])}`",
        "- Robot count: `8`",
        "- Seeds: `1–10`",
        f"- Nominal scene identities: `{replication['nominal_scene_count']}`",
        f"- Unique effective missions: `{replication['unique_effective_mission_count']}`",
        f"- Unique normalized trajectories: `{replication['unique_normalized_trajectory_count']}`",
        f"- Task predicate: `{report['governance']['task_validity_predicate_id']}`",
        "- Timing unlock: `false`",
        "- Scientific admission: `false`",
        "",
        "## Effective replication and horizon",
        "",
    ]
    if (
        replication["seed_axis_state"]
        == "inactive-for-observed-file-scenario-zero-comms-cohort"
    ):
        lines.append(
            "The ten seed-labelled rows per family reuse one fixed map/scenario "
            "mission. For this file-scenario, zero-communications-failure cohort, "
            "the runtime seed changes RNG provenance but not the observed normalized "
            "trajectory. These rows establish deterministic repeatability, not ten "
            "independent trials. Collision rates and family causal effects must not "
            "be estimated from them."
        )
    else:
        lines.append(
            "The plan reuses one fixed map/scenario mission across each family's "
            "ten seed labels, but the present cohort does not authenticate enough "
            "trajectories to evaluate the observed seed axis. No repeatability or "
            "inferential-independence conclusion is permitted."
        )
    lines.append("")
    if replication["goal_horizon_state"] == "censored-for-all-effective-missions":
        lines.append(
            "The fixed 10-second horizon at 2 m/s permits at most 20 m displacement. "
            "Even after subtracting the 1 m goal-reach radius, every robot's required "
            "centre travel exceeds that bound. Zero goal completions are structurally "
            "censored and cannot diagnose controller or environment performance."
        )
    else:
        lines.append(
            "Goal-horizon censoring is not fully evaluated for the present cohort; "
            "no goal-completion interpretation is permitted."
        )
    lines.extend(
        [
            "",
            "| Family | Nominal scenes | Effective inputs | Normalized trajectories | Seed axis | Nearest goal (m) | Goal horizon |",
            "| --- | ---: | ---: | ---: | --- | ---: | --- |",
        ]
    )
    for family in replication["families"]:
        goal_horizon = family["goal_horizon"]
        nearest = goal_horizon.get("minimum_start_to_final_goal_distance_m")
        nearest_text = f"{nearest:.6f}" if isinstance(nearest, float) else "n/a"
        lines.append(
            f"| `{family['family']}` | {family['nominal_scene_count']} | "
            f"{family['unique_effective_input_count']} | "
            f"{family['unique_normalized_trajectory_count']} | "
            f"`{family['seed_axis_state']}` | {nearest_text} | "
            f"`{goal_horizon['interpretation']}` |"
        )
    lines.extend(
        [
        "",
        "## Cohort result",
        "",
        "| Status | Expected | Present | Authenticated | Missing | Auth failures | Exact parity | Task-valid | Task-unverified | Task-invalid |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        (
            f"| `{summary['status']}` | {summary['expected_scene_count']} | "
            f"{summary['present_scene_count']} | {summary['authenticated_scene_count']} | "
            f"{summary['missing_scene_count']} | "
            f"{summary['authentication_failed_scene_count']} | "
            f"{summary['parity_exact_scene_count']} | "
            f"{summary['task_valid_scene_count']} | "
            f"{summary['task_not_independently_verified_scene_count']} | "
            f"{summary['task_invalid_scene_count']} |"
        ),
        "",
        "## Family coverage",
        "",
        "| Family | Present / expected | Authenticated | Missing | Exact parity | Task-valid | Task-unverified | Task-invalid |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for family in report["families"]:
        lines.append(
            f"| `{family['family']}` | {family['present_scene_count']} / "
            f"{family['expected_scene_count']} | "
            f"{family['authenticated_scene_count']} | "
            f"{family['missing_scene_count']} | "
            f"{family['parity_exact_scene_count']} | "
            f"{family['task_valid_scene_count']} | "
            f"{family['task_not_independently_verified_scene_count']} | "
            f"{family['task_invalid_scene_count']} |"
        )
    lines.extend(
        [
            "",
            "## Scene matrix",
            "",
            "| Family | Seed | Authentication | Parity | Task validity |",
            "| --- | ---: | --- | --- | --- |",
        ]
    )
    for scene in report["scenes"]:
        lines.append(
            f"| `{scene['family']}` | {scene['seed']} | "
            f"`{scene['authentication']['state']}` | "
            f"`{scene['parity']['state']}` | "
            f"`{scene['task_validity']['state']}` |"
        )
    invalid = [
        scene
        for scene in report["scenes"]
        if scene["task_validity"]["state"] == "invalid"
    ]
    failed = [
        scene
        for scene in report["scenes"]
        if scene["authentication"]["state"] == "failed"
    ]
    if invalid:
        lines.extend(["", "## Task-invalid scenes", ""])
        for scene in invalid:
            reasons = "; ".join(scene["task_validity"]["violations"])
            lines.append(f"- `{scene['scene_id']}`: {reasons}")
    if failed:
        lines.extend(["", "## Authentication failures", ""])
        for scene in failed:
            lines.append(
                f"- `{scene['scene_id']}`: "
                f"`{scene['authentication']['reason']}`"
            )
    lines.extend(
        [
            "",
            "## Governance result",
            "",
            f"Diagnostic cohort gate pass: `{str(summary['diagnostic_gate_pass']).lower()}`.",
            "Regardless of that diagnostic state, `timing_unlock`, "
            "`scientific_admission`, and `validation_evidence` remain `false`.",
            "",
        ]
    )
    return "\n".join(lines)


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise AnalysisError("report-publication:short-write")
        offset += written


def _validate_output_parent(path: Path, label: str) -> None:
    parent = path.parent
    try:
        metadata = os.lstat(parent)
    except OSError as error:
        raise AnalysisError(f"report-publication:{label}-parent-invalid:{error.errno}") from error
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise AnalysisError(f"report-publication:{label}-parent-not-directory")


def write_reports_exclusive(
    json_path: Path | str,
    markdown_path: Path | str,
    report: Mapping[str, Any],
) -> None:
    """Create both reports without overwriting either destination."""
    json_destination = Path(json_path)
    markdown_destination = Path(markdown_path)
    if os.path.abspath(json_destination) == os.path.abspath(markdown_destination):
        raise AnalysisError("report-publication:destinations-must-differ")
    _validate_output_parent(json_destination, "json")
    _validate_output_parent(markdown_destination, "markdown")
    for path, label in (
        (json_destination, "json"),
        (markdown_destination, "markdown"),
    ):
        try:
            os.lstat(path)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise AnalysisError(f"report-publication:{label}-stat-failed:{error.errno}") from error
        else:
            raise AnalysisError(f"report-publication:{label}-already-exists")

    try:
        payloads = (
            (json_destination, canonical_json_bytes(report)),
            (markdown_destination, render_markdown(report).encode("utf-8")),
        )
    except (TypeError, ValueError, RecursionError) as error:
        raise AnalysisError("report-publication:payload-invalid") from error
    opened: list[tuple[Path, int, tuple[int, int]]] = []
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        for path, _ in payloads:
            descriptor = os.open(path, flags, 0o600)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise AnalysisError("report-publication:created-entry-not-regular")
            opened.append((path, descriptor, (metadata.st_dev, metadata.st_ino)))
        for (_, payload), (_, descriptor, _) in zip(payloads, opened):
            _write_all(descriptor, payload)
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o444)
    except (OSError, AnalysisError) as error:
        for _, descriptor, _ in opened:
            try:
                os.close(descriptor)
            except OSError:
                pass
        for path, _, identity in opened:
            try:
                current = os.lstat(path)
                if (current.st_dev, current.st_ino) == identity:
                    os.unlink(path)
            except OSError:
                pass
        if isinstance(error, AnalysisError):
            raise
        raise AnalysisError(f"report-publication:exclusive-create-failed:{error.errno}") from error
    else:
        close_error: OSError | None = None
        for _, descriptor, _ in opened:
            try:
                os.close(descriptor)
            except OSError as error:
                if close_error is None:
                    close_error = error
        if close_error is not None:
            for _, descriptor, _ in opened:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            for path, _, identity in opened:
                try:
                    current = os.lstat(path)
                    if (current.st_dev, current.st_ino) == identity:
                        os.unlink(path)
                except OSError:
                    pass
            raise AnalysisError(
                f"report-publication:close-failed:{close_error.errno}"
            ) from close_error


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Authenticate and report the frozen N=8 visual cohort."
    )
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--results-root", type=Path)
    parser.add_argument("--json-out", type=Path, required=True)
    parser.add_argument("--markdown-out", type=Path, required=True)
    return parser


def _canonical_cli_selection(
    repository: Path,
    selected: Path | None,
    relative_path: str,
    label: str,
) -> Path:
    canonical = repository / relative_path
    candidate = (
        _lexical_absolute(
            selected if selected.is_absolute() else repository / selected
        )
        if selected is not None
        else canonical
    )
    if candidate != canonical:
        raise AnalysisError(f"cli:{label}:noncanonical-selection")
    _require_no_symlink_components(
        repository,
        relative_path,
        f"cli:{label}",
        allow_missing=label == "results-root",
    )
    return canonical


def _validate_cli_output_destinations(
    repository: Path,
    json_output: Path,
    markdown_output: Path,
) -> None:
    destinations = {
        "json": _lexical_absolute(json_output),
        "markdown": _lexical_absolute(markdown_output),
    }
    if destinations["json"] == destinations["markdown"]:
        raise AnalysisError("cli:report-destinations-must-differ")
    protected_root = repository / PROTECTED_VISUAL_RESULTS_RELATIVE_ROOT
    try:
        protected_metadata = os.lstat(protected_root)
    except OSError as error:
        raise AnalysisError(
            f"cli:protected-visual-results-invalid:{error.errno}"
        ) from error
    protected_identity = (
        protected_metadata.st_dev,
        protected_metadata.st_ino,
    )
    for label, destination in destinations.items():
        try:
            destination.relative_to(protected_root)
        except ValueError:
            pass
        else:
            raise AnalysisError(
                f"cli:{label}-out:protected-visual-results-path"
            )
        parent = destination.parent
        current = Path(parent.anchor)
        for component in parent.parts[1:]:
            current = current / component
            try:
                metadata = os.lstat(current)
            except OSError as error:
                raise AnalysisError(
                    f"cli:{label}-out:parent-component-invalid:{error.errno}"
                ) from error
            if stat.S_ISLNK(metadata.st_mode):
                raise AnalysisError(
                    f"cli:{label}-out:parent-symlink-component"
                )
            if not stat.S_ISDIR(metadata.st_mode):
                raise AnalysisError(
                    f"cli:{label}-out:parent-component-not-directory"
                )
            if (metadata.st_dev, metadata.st_ino) == protected_identity:
                raise AnalysisError(
                    f"cli:{label}-out:protected-visual-results-path"
                )


def report_exit_code(report: Mapping[str, Any]) -> int:
    """Return fail-closed CLI status for a successfully constructed report."""
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise AnalysisError("report-exit-status:summary-invalid")
    authentication_failures = summary.get(
        "authentication_failed_scene_count"
    )
    diagnostic_pass = summary.get("diagnostic_gate_pass")
    if not _is_integer(authentication_failures) or authentication_failures < 0:
        raise AnalysisError(
            "report-exit-status:authentication-count-invalid"
        )
    if not isinstance(diagnostic_pass, bool):
        raise AnalysisError("report-exit-status:diagnostic-gate-invalid")
    if authentication_failures:
        return 2
    return 0 if diagnostic_pass else 1


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _argument_parser().parse_args(argv)
    try:
        repository = _canonical_repository_root(arguments.repo_root)
        selected_plan = _canonical_cli_selection(
            repository,
            arguments.plan,
            PLAN_RELATIVE_PATH,
            "plan",
        )
        selected_results = _canonical_cli_selection(
            repository,
            arguments.results_root,
            RESULTS_RELATIVE_ROOT,
            "results-root",
        )
        _validate_cli_output_destinations(
            repository,
            arguments.json_out,
            arguments.markdown_out,
        )
        report = analyze_visual_cohort(
            repository,
            selected_plan,
            selected_results,
        )
        semantic_return_code = report_exit_code(report)
        write_reports_exclusive(
            arguments.json_out,
            arguments.markdown_out,
            report,
        )
        receipt = {
            "analysis_id": ANALYSIS_ID,
            "json_output": str(arguments.json_out),
            "markdown_output": str(arguments.markdown_out),
            "schema_version": SCHEMA_VERSION,
            "summary": report["summary"],
        }
        try:
            sys.stdout.buffer.write(canonical_json_bytes(receipt))
            sys.stdout.buffer.flush()
        except (BrokenPipeError, OSError) as error:
            raise AnalysisError(
                f"cli:stdout-publication-failed:{error.errno}"
            ) from error
    except AnalysisError as error:
        try:
            print(f"visual cohort analysis failed: {error}", file=sys.stderr)
            sys.stderr.flush()
        except (BrokenPipeError, OSError):
            pass
        return 2
    return semantic_return_code


if __name__ == "__main__":
    raise SystemExit(main())
