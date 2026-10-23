#!/usr/bin/env python3
"""Publish the prospective DANCeRS--ORCA restricted-study inputs.

This is an input compiler only. It never launches either implementation and
never reads a result directory. Output is byte deterministic and create-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import shutil
import sys
from typing import Any, Mapping, Sequence


REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.replication import prepare_multimap_inputs as multimap


SCHEMA = "dancers-orca-input-manifest-v1"
SCHEDULE_SCHEMA = "dancers-orca-schedule-v1"
CROSSWALK_SCHEMA = "dancers-orca-parameter-crosswalk-v1"
STUDY_ID = "dancers-orca-v1"
OUTPUT_RELATIVE = pathlib.PurePosixPath(
    "comparison/publication/dancers-orca-v1"
)
SOURCE_RELATIVE = pathlib.PurePosixPath(
    "comparison/publication/dancers-multimap-v1"
)
PROTOCOL_RELATIVE = pathlib.PurePosixPath(
    "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_V1.md"
)
PROTOCOL_AMENDMENT_RELATIVE = pathlib.PurePosixPath(
    "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_AMENDMENT_1_V1.md"
)
PROTOCOL_AMENDMENT_2_RELATIVE = pathlib.PurePosixPath(
    "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_AMENDMENT_2_V1.md"
)
PREFREEZE_DIAGNOSTIC_RELATIVE = pathlib.PurePosixPath(
    "docs/publication/task4_external_baseline/PREFREEZE_WRAPPER_DIAGNOSTIC_V1.md"
)
OPEN_MISSIONS = tuple(range(31, 51))
CIRCLE_COUNTS = (8, 20, 50)
METHODS = ("reference", "optimized", "orca")
DANCERS_METHODS = ("reference", "optimized")
MAIN_TIMESTEP_SECONDS = 0.1
SENSITIVITY_TIMESTEP_SECONDS = 0.05
OPEN_STEPS = 100
CIRCLE_STEPS = 600
CIRCLE_SENSITIVITY_STEPS = 1200
ROBOT_RADIUS_M = 1.0
MAXIMUM_SPEED_MPS = 2.0
TIME_HORIZON_SECONDS = 1.5
NEIGHBOR_DISTANCE_M = 20.0
GOAL_TOLERANCE_M = 1.0
CIRCLE_RADIUS_M = 20.0
WORLD_SIZE_GUARD_M = 400.0
PROCESS_TIMEOUT_SECONDS = 300.0
EXPECTED_SCENES = 23
EXPECTED_WARMUPS = 12
EXPECTED_MAIN_PROCESSES = 345
EXPECTED_SENSITIVITY_PROCESSES = 6
EXPECTED_PROCESSES = 363
ORDER_DOMAIN = b"dancers-orca-order-v1\0"


class InputError(RuntimeError):
    pass


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(relative: pathlib.PurePosixPath, raw: bytes) -> dict[str, Any]:
    return {
        "bytes": len(raw),
        "path": relative.as_posix(),
        "sha256": sha256_bytes(raw),
    }


def _repository_identity(relative: pathlib.PurePosixPath) -> dict[str, Any]:
    path = REPO.joinpath(*relative.parts)
    if path.is_symlink() or not path.is_file():
        raise InputError(f"repository input is not a regular file: {relative}")
    return {
        "bytes": path.stat().st_size,
        "path": relative.as_posix(),
        "sha256": sha256_file(path),
    }


def _write(root: pathlib.Path, relative: pathlib.PurePosixPath, raw: bytes) -> None:
    path = root.joinpath(*relative.parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)


def runtime_seed(mission_label: int | None) -> int:
    if mission_label is None:
        return 1
    if mission_label not in OPEN_MISSIONS:
        raise InputError("open mission label is outside 31..50")
    return 1 + ((mission_label - 31) % 10)


def _clean_coordinate(value: float) -> float:
    rounded = round(value, 9)
    return 0.0 if abs(rounded) < 5e-10 else rounded


def circle_scenario(count: int) -> dict[str, Any]:
    if count not in CIRCLE_COUNTS:
        raise InputError("circle count is outside the frozen grid")
    waypoints: dict[str, list[list[float]]] = {}
    decisions: dict[str, int] = {}
    starts: list[tuple[float, float]] = []
    for robot_id in range(count):
        angle = 2.0 * math.pi * robot_id / count
        x = _clean_coordinate(CIRCLE_RADIUS_M * math.cos(angle))
        y = _clean_coordinate(CIRCLE_RADIUS_M * math.sin(angle))
        starts.append((x, y))
        waypoints[str(robot_id)] = [[x, y], [-x, -y]]
        decisions[str(robot_id)] = 0
    minimum_spacing = min(
        math.hypot(
            starts[index][0] - starts[(index + 1) % count][0],
            starts[index][1] - starts[(index + 1) % count][1],
        )
        for index in range(count)
    )
    if not minimum_spacing > 2.0 * ROBOT_RADIUS_M:
        raise InputError("circle starts are not strictly separated")
    return {
        "decisions": decisions,
        "metadata": {
            "circle_radius_m": CIRCLE_RADIUS_M,
            "fixed_work_timesteps": CIRCLE_STEPS,
            "maximum_linear_speed_m_per_second": MAXIMUM_SPEED_MPS,
            "minimum_initial_pair_separation_m": minimum_spacing,
            "phase": "prospective-dancers-orca-v1",
            "prefix_robot_count": count,
            "robot_radius_m": ROBOT_RADIUS_M,
            "scenario_kind": "dancers-orca-antipodal-circle-v1",
            "timestep_seconds": MAIN_TIMESTEP_SECONDS,
            "world_size_m": WORLD_SIZE_GUARD_M,
        },
        "num_robots": count,
        "seed_robots": [],
        "waypoints": waypoints,
    }


def _validate_two_waypoint_scenario(
    scenario: Mapping[str, Any], *, count: int, label: str
) -> None:
    expected = {str(index) for index in range(count)}
    if scenario.get("num_robots") != count:
        raise InputError(f"{label} robot count differs")
    waypoints = scenario.get("waypoints")
    decisions = scenario.get("decisions")
    if not isinstance(waypoints, dict) or set(waypoints) != expected:
        raise InputError(f"{label} waypoint keys differ")
    if not isinstance(decisions, dict) or set(decisions) != expected:
        raise InputError(f"{label} decision keys differ")
    starts: list[tuple[float, float]] = []
    goals: list[tuple[float, float]] = []
    for key in sorted(expected, key=int):
        route = waypoints[key]
        if not isinstance(route, list) or len(route) != 2:
            raise InputError(f"{label} robot {key} must have exactly two waypoints")
        parsed: list[tuple[float, float]] = []
        for point in route:
            if not isinstance(point, list) or len(point) != 2:
                raise InputError(f"{label} robot {key} waypoint differs")
            x, y = point
            if (
                isinstance(x, bool)
                or isinstance(y, bool)
                or not isinstance(x, (int, float))
                or not isinstance(y, (int, float))
                or not math.isfinite(float(x))
                or not math.isfinite(float(y))
            ):
                raise InputError(f"{label} contains a non-finite waypoint")
            parsed.append((float(x), float(y)))
        starts.append(parsed[0])
        goals.append(parsed[1])
    for points, kind in ((starts, "start"), (goals, "goal")):
        for index, first in enumerate(points):
            for second in points[index + 1 :]:
                if math.dist(first, second) <= 2.0 * ROBOT_RADIUS_M:
                    raise InputError(f"{label} {kind} centres are not separated")


def render_config(
    *,
    scene_id: str,
    scenario_relpath: pathlib.PurePosixPath,
    count: int,
    steps: int,
    world_size_m: float,
    rng_seed: int,
) -> bytes:
    text = f'''SIMULATION_NAME: "{scene_id}"
LOG_RESULTS: true
SCENARIO: file
SCENARIO_FILE: {(OUTPUT_RELATIVE / scenario_relpath).as_posix()}
NUM_ROBOTS: {count}
COMMUNICATION_RADIUS: 20
RNG_SEED: {rng_seed}
COMMS_FAILURE_RATE: 0.0

WORLD_SZ: {int(world_size_m)}
TIMESTEP: 0.1
MAX_TIME: {steps}
SIM_MODE: 1
ROBOT_MODEL: sphere

DISPLAY: 0
SCREEN_SZ: 800
DRAW_INTERROBOT: 0
DRAW_PATH: 0
DRAW_TRAJ: 0
MAX_TRAJ_LEN: {steps}
DRAW_WAYPOINTS: 0

FACTORGRAPH_LAYERS:
  - name: Planning
    type: planning
    params:
      T_HORIZON: 1.5
      ROBOT_RADIUS: 1.0
      MAX_SPEED: 2.0
      TURNING_RADIUS: 1.0
      SIGMA_VARIABLE_HORIZON: 0.01
      SIGMA_FACTOR_DYNAMICS: 0.1
      SIGMA_FACTOR_UNICYCLE: 0.001
      SIGMA_FACTOR_INTERROBOT: 0.01
      SIGMA_FACTOR_OBSTACLE: 0.01

NUM_ITERS: 2
DAMPING: 0.0
SYMMETRIC_INTERROBOT_FACTORS: false
'''
    if "ASSETS_DIR" in text or "OBSTACLE_FILE" in text:
        raise InputError("external-comparison config unexpectedly contains obstacles")
    _validate_config_text(text, expected_world_size_m=world_size_m)
    return text.encode("utf-8")


def _validate_config_text(text: str, *, expected_world_size_m: float) -> None:
    keys: list[str] = []
    for line in text.splitlines():
        if not line or line[0].isspace() or line.lstrip().startswith("#"):
            continue
        key, separator, _ = line.partition(":")
        if not separator or not key:
            raise InputError("DANCeRS config contains a malformed top-level line")
        keys.append(key)
    if len(keys) != len(set(keys)):
        raise InputError("DANCeRS config contains a duplicate top-level key")
    if keys.count("ROBOT_MODEL") != 1:
        raise InputError("DANCeRS config must contain exactly one ROBOT_MODEL key")
    expected_world = f"WORLD_SZ: {int(expected_world_size_m)}"
    if text.splitlines().count(expected_world) != 1:
        raise InputError("DANCeRS config world-size guard differs")


def parameter_crosswalk() -> dict[str, Any]:
    return {
        "adaptation_limits": {
            "allowed": [
                "direct preferred velocity to final goal",
                "permanent goal latch on first sampled centre distance <= 1.0 m",
                "zero preferred velocity from the latch step onward even if displaced",
                "trajectory observation through public API",
            ],
            "forbidden": [
                "global route planner",
                "unicycle projection",
                "acceleration limiter",
                "smoothing pass",
                "recovery or collision repair",
            ],
        },
        "common": {
            "common_grid_rule": "initial state plus post-step states; H+1 samples",
            "fixed_ceiling_without_agent_deletion": True,
            "initial_velocity_mps": [0.0, 0.0],
            "goal_tolerance_m": GOAL_TOLERANCE_M,
            "goal_tolerance_set": "closed centre-distance disc",
            "main_timestep_seconds": MAIN_TIMESTEP_SECONDS,
            "maximum_speed_mps": MAXIMUM_SPEED_MPS,
            "obstacles_present": False,
            "robot_radius_m": ROBOT_RADIUS_M,
            "starts_and_goals": "same authenticated scenario bytes",
        },
        "mapped_numeric_values": {
            "dancers_communication_radius_to_orca_neighbor_distance_m": 20.0,
            "dancers_planning_horizon_to_orca_time_horizon_seconds": 1.5,
            "semantic_equivalence_claimed": False,
        },
        "methods": {
            "optimized": {
                "communication_failure_rate": 0.0,
                "communication_radius_m": 20.0,
                "coordination": "Gaussian message exchange",
                "motion_model": "native DANCeRS nonholonomic/dynamic factors",
                "source_role": "optimized DANCeRS",
                "world_size_deletion_guard_m": WORLD_SIZE_GUARD_M,
            },
            "orca": {
                "coordination": "none; observed current neighbor state",
                "max_neighbors_rule": "robot_count - 1",
                "motion_model": "native RVO2 holonomic disc",
                "neighbor_distance_m": NEIGHBOR_DISTANCE_M,
                "preferred_velocity_rule": (
                    "2 m/s directly to final goal until first sampled centre distance "
                    "<= 1.0 m; permanently zero from that latch step onward"
                ),
                "source_role": "external behavioral context",
                "time_horizon_obstacles_seconds": TIME_HORIZON_SECONDS,
                "time_horizon_seconds": TIME_HORIZON_SECONDS,
            },
            "reference": {
                "communication_failure_rate": 0.0,
                "communication_radius_m": 20.0,
                "coordination": "Gaussian message exchange",
                "motion_model": "native DANCeRS nonholonomic/dynamic factors",
                "source_role": "causal implementation baseline",
                "world_size_deletion_guard_m": WORLD_SIZE_GUARD_M,
            },
        },
        "input_authority": {
            "scenario_json": "num_robots and ordered numeric-ID waypoints only",
            "manifest_and_schedule": (
                "cadence, native steps, timestep, sample stride, physical parameters, "
                "scene identity, and attempt identity"
            ),
            "scenario_metadata": "provenance and generator assertions only",
        },
        "scenario_metadata_policy": {
            "imported_open_metadata_map_sha256": "inert provenance; no map is loaded",
            "imported_open_metadata_world_size_m": (
                "inert source provenance; derivative config uses the 400 m guard"
            ),
        },
        "schema_version": CROSSWALK_SCHEMA,
        "study_id": STUDY_ID,
    }


def _method_order(key: str, methods: Sequence[str]) -> tuple[str, ...]:
    if not methods or len(set(methods)) != len(methods):
        raise InputError("method-order input differs")
    return tuple(
        sorted(
            methods,
            key=lambda method: hashlib.sha256(
                ORDER_DOMAIN + key.encode("ascii") + b"\0" + method.encode("ascii")
            ).digest(),
        )
    )


def _balanced_pair_orders(
    scenes: Sequence[Mapping[str, Any]], run_class: str
) -> dict[str, tuple[str, str]]:
    if run_class not in ("digest_fidelity", "positions_fidelity"):
        raise InputError("pair run class differs")
    ranked = sorted(
        (str(scene["scene_id"]) for scene in scenes),
        key=lambda scene_id: hashlib.sha256(
            ORDER_DOMAIN
            + b"pair\0"
            + run_class.encode("ascii")
            + b"\0"
            + scene_id.encode("ascii")
        ).digest(),
    )
    # Twenty-three scenes cannot split first position equally within one class.
    # Opposite parity across the two classes gives exact 23/23 overall balance.
    parity_offset = 0 if run_class == "digest_fidelity" else 1
    orders: dict[str, tuple[str, str]] = {}
    for rank, scene_id in enumerate(ranked):
        if (rank + parity_offset) % 2 == 0:
            orders[scene_id] = ("reference", "optimized")
        else:
            orders[scene_id] = ("optimized", "reference")
    return orders


def _balanced_warmup_orders(
    strata: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, int], tuple[str, ...]]:
    identities = [
        (str(stratum["scene_set"]), int(stratum["robot_count"]))
        for stratum in strata
    ]
    if len(identities) != 4 or len(set(identities)) != len(identities):
        raise InputError("warm-up stratum inventory differs")
    ranked = sorted(
        identities,
        key=lambda identity: hashlib.sha256(
            ORDER_DOMAIN
            + b"warmup-stratum\0"
            + identity[0].encode("ascii")
            + b"\0n"
            + str(identity[1]).encode("ascii")
        ).digest(),
    )
    base = _method_order("warmup-base", METHODS)
    orders: dict[tuple[str, int], tuple[str, ...]] = {}
    for rank, identity in enumerate(ranked):
        offset = rank % len(base)
        orders[identity] = base[offset:] + base[:offset]
    return orders


def _timing_orders(scene_id: str) -> tuple[tuple[str, ...], ...]:
    base = _method_order(f"timing-base\0{scene_id}", METHODS)
    return tuple(base[offset:] + base[:offset] for offset in range(len(base)))


def schedule_records(scenes: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if len(scenes) != EXPECTED_SCENES:
        raise InputError("scene count differs before scheduling")
    records: list[dict[str, Any]] = []
    sequence = 0

    strata: list[Mapping[str, Any]] = []
    open_scenes = [scene for scene in scenes if scene["scene_set"] == "open"]
    circle_scenes = [scene for scene in scenes if scene["scene_set"] == "circle"]
    if len(open_scenes) != 20 or len(circle_scenes) != 3:
        raise InputError("scene-set inventory differs before scheduling")
    pair_orders = {
        run_class: _balanced_pair_orders(scenes, run_class)
        for run_class in ("digest_fidelity", "positions_fidelity")
    }
    strata.append(open_scenes[0])
    strata.extend(sorted(circle_scenes, key=lambda item: item["robot_count"]))
    warmup_orders = _balanced_warmup_orders(strata)
    for stratum in strata:
        identity = (str(stratum["scene_set"]), int(stratum["robot_count"]))
        for position, method in enumerate(warmup_orders[identity]):
            records.append(
                {
                    **stratum,
                    "cadence": "main",
                    "cohort_id": f"warmup-{stratum['scene_set']}-n{stratum['robot_count']}",
                    "method": method,
                    "order_position": position,
                    "phase": "warmup",
                    "repetition": 0,
                    "run_class": "timing",
                    "sequence": sequence,
                    "trace_detail": None,
                }
            )
            sequence += 1

    for scene in scenes:
        for run_class, trace_detail in (
            ("digest_fidelity", "digest"),
            ("positions_fidelity", "positions"),
        ):
            cohort = f"{scene['scene_id']}-{run_class}-r1"
            for position, method in enumerate(
                pair_orders[run_class][str(scene["scene_id"])]
            ):
                records.append(
                    {
                        **scene,
                        "cadence": "main",
                        "cohort_id": cohort,
                        "method": method,
                        "order_position": position,
                        "phase": "measured",
                        "repetition": 1,
                        "run_class": run_class,
                        "sequence": sequence,
                        "trace_detail": trace_detail,
                    }
                )
                sequence += 1
        for repetition in (1, 2):
            records.append(
                {
                    **scene,
                    "cadence": "main",
                    "cohort_id": f"{scene['scene_id']}-orca-positions",
                    "method": "orca",
                    "order_position": repetition - 1,
                    "phase": "measured",
                    "repetition": repetition,
                    "run_class": "positions_fidelity",
                    "sequence": sequence,
                    "trace_detail": "positions",
                }
            )
            sequence += 1
        for repetition, method_order in enumerate(
            _timing_orders(str(scene["scene_id"])), start=1
        ):
            cohort = f"{scene['scene_id']}-timing-r{repetition}"
            for position, method in enumerate(method_order):
                records.append(
                    {
                        **scene,
                        "cadence": "main",
                        "cohort_id": cohort,
                        "method": method,
                        "order_position": position,
                        "phase": "measured",
                        "repetition": repetition,
                        "run_class": "timing",
                        "sequence": sequence,
                        "trace_detail": None,
                    }
                )
                sequence += 1

    for scene in sorted(circle_scenes, key=lambda item: item["robot_count"]):
        for repetition in (1, 2):
            records.append(
                {
                    **scene,
                    "cadence": "orca-0.05s-sensitivity",
                    "cohort_id": f"{scene['scene_id']}-orca-cadence-sensitivity",
                    "method": "orca",
                    "order_position": repetition - 1,
                    "phase": "sensitivity",
                    "repetition": repetition,
                    "run_class": "cadence_sensitivity",
                    "sequence": sequence,
                    "steps": CIRCLE_SENSITIVITY_STEPS,
                    "timestep_seconds": SENSITIVITY_TIMESTEP_SECONDS,
                    "duration_seconds": (
                        CIRCLE_SENSITIVITY_STEPS * SENSITIVITY_TIMESTEP_SECONDS
                    ),
                    "common_sample_count": CIRCLE_STEPS + 1,
                    "native_sample_stride": 2,
                    "resolved_parameters": resolved_parameters(
                        int(scene["robot_count"]),
                        CIRCLE_SENSITIVITY_STEPS,
                        SENSITIVITY_TIMESTEP_SECONDS,
                        2,
                        runtime_seed(None),
                    ),
                    "trace_detail": "positions",
                }
            )
            sequence += 1

    validate_schedule(records, scenes)
    return records


def validate_schedule(
    records: Sequence[Mapping[str, Any]], scenes: Sequence[Mapping[str, Any]]
) -> None:
    if len(records) != EXPECTED_PROCESSES:
        raise InputError(f"schedule size differs: {len(records)}")
    if [row.get("sequence") for row in records] != list(range(EXPECTED_PROCESSES)):
        raise InputError("schedule sequence differs")
    warmups = [row for row in records if row["phase"] == "warmup"]
    if len(warmups) != EXPECTED_WARMUPS:
        raise InputError("warmup count differs")
    warmup_cohorts = {str(row["cohort_id"]) for row in warmups}
    if len(warmup_cohorts) != 4:
        raise InputError("warmup cohort inventory differs")
    for cohort_id in warmup_cohorts:
        cohort = [row for row in warmups if row["cohort_id"] == cohort_id]
        if (
            len(cohort) != 3
            or {row["method"] for row in cohort} != set(METHODS)
            or sorted(int(row["order_position"]) for row in cohort) != [0, 1, 2]
        ):
            raise InputError("warmup cohort composition differs")
    for method in METHODS:
        positions = [
            int(row["order_position"])
            for row in warmups
            if row["method"] == method
        ]
        position_counts = sorted(
            positions.count(position) for position in range(3)
        )
        if len(positions) != 4 or position_counts != [1, 1, 2]:
            raise InputError("warmup position balance differs")
    if sum(row["phase"] == "sensitivity" for row in records) != EXPECTED_SENSITIVITY_PROCESSES:
        raise InputError("sensitivity count differs")
    if sum(row["phase"] == "measured" for row in records) != EXPECTED_MAIN_PROCESSES:
        raise InputError("main measured count differs")
    scene_ids = {scene["scene_id"] for scene in scenes}
    for scene_id in scene_ids:
        main = [
            row
            for row in records
            if row["scene_id"] == scene_id and row["phase"] == "measured"
        ]
        if len(main) != 15:
            raise InputError(f"main process count differs for {scene_id}")
        if {method: sum(row["method"] == method for row in main) for method in METHODS} != {
            "reference": 5,
            "optimized": 5,
            "orca": 5,
        }:
            raise InputError(f"method balance differs for {scene_id}")
        timing = [row for row in main if row["run_class"] == "timing"]
        for method in METHODS:
            positions = sorted(
                row["order_position"] for row in timing if row["method"] == method
            )
            if positions != [0, 1, 2]:
                raise InputError(f"timing position balance differs for {scene_id}")
    for run_class, expected_reference_first in (
        ("digest_fidelity", 12),
        ("positions_fidelity", 11),
    ):
        pairs = [
            row
            for row in records
            if row["phase"] == "measured" and row["run_class"] == run_class
        ]
        if sum(
            row["method"] == "reference" and row["order_position"] == 0
            for row in pairs
        ) != expected_reference_first:
            raise InputError(f"pair position balance differs for {run_class}")
    if any(float(row["timeout_seconds"]) != PROCESS_TIMEOUT_SECONDS for row in records):
        raise InputError("process timeout differs")
    for row in records:
        sensitivity = row["cadence"] == "orca-0.05s-sensitivity"
        expected_stride = 2 if sensitivity else 1
        expected_samples = int(row["steps"]) // expected_stride + 1
        if (
            row.get("native_sample_stride") != expected_stride
            or row.get("common_sample_count") != expected_samples
        ):
            raise InputError("common-grid sampling differs")
        execution = row.get("resolved_parameters", {}).get("execution", {})
        common = row.get("resolved_parameters", {}).get("common", {})
        if (
            execution.get("native_steps") != row["steps"]
            or execution.get("native_timestep_seconds") != row["timestep_seconds"]
            or execution.get("native_sample_stride") != expected_stride
            or common.get("common_sample_count") != expected_samples
            or common.get("common_grid_timestep_seconds") != MAIN_TIMESTEP_SECONDS
            or common.get("physical_duration_seconds")
            != row["steps"] * row["timestep_seconds"]
            or row.get("duration_seconds")
            != row["steps"] * row["timestep_seconds"]
        ):
            raise InputError("attempt resolved execution parameters differ")


def _scene_record(
    *,
    scene_id: str,
    scene_set: str,
    scenario_relpath: pathlib.PurePosixPath,
    scenario_raw: bytes,
    config_relpath: pathlib.PurePosixPath,
    config_raw: bytes,
    count: int,
    steps: int,
    rng_seed: int,
    source_scenario: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "config_relpath": config_relpath.as_posix(),
        "config_sha256": sha256_bytes(config_raw),
        "common_sample_count": steps + 1,
        "duration_seconds": steps * MAIN_TIMESTEP_SECONDS,
        "goal_tolerance_m": GOAL_TOLERANCE_M,
        "robot_count": count,
        "native_sample_stride": 1,
        "resolved_parameters": resolved_parameters(
            count, steps, MAIN_TIMESTEP_SECONDS, 1, rng_seed
        ),
        "scenario_relpath": scenario_relpath.as_posix(),
        "scenario_sha256": sha256_bytes(scenario_raw),
        "scene_id": scene_id,
        "scene_set": scene_set,
        "source_metadata_fields_inert": (
            ["metadata.map_sha256", "metadata.world_size_m"]
            if source_scenario is not None
            else []
        ),
        "source_scenario": source_scenario,
        "steps": steps,
        "timeout_seconds": PROCESS_TIMEOUT_SECONDS,
        "timestep_seconds": MAIN_TIMESTEP_SECONDS,
    }


def resolved_parameters(
    count: int,
    native_steps: int,
    native_timestep_seconds: float,
    native_sample_stride: int,
    rng_seed: int,
) -> dict[str, Any]:
    if native_steps <= 0 or native_sample_stride <= 0:
        raise InputError("resolved execution grid is invalid")
    if native_steps % native_sample_stride != 0:
        raise InputError("native steps do not align to the common grid")
    return {
        "common": {
            "common_grid_timestep_seconds": MAIN_TIMESTEP_SECONDS,
            "common_sample_count": native_steps // native_sample_stride + 1,
            "fixed_ceiling": True,
            "goal_tolerance_m": GOAL_TOLERANCE_M,
            "initial_velocity_mps": [0.0, 0.0],
            "maximum_speed_mps": MAXIMUM_SPEED_MPS,
            "obstacles_present": False,
            "physical_duration_seconds": native_steps * native_timestep_seconds,
            "retain_agents_after_goal": True,
            "robot_radius_m": ROBOT_RADIUS_M,
        },
        "dancers": {
            "communication_failure_rate": 0.0,
            "communication_radius_m": 20.0,
            "rng_seed": rng_seed,
            "world_size_deletion_guard_m": WORLD_SIZE_GUARD_M,
        },
        "execution": {
            "native_sample_stride": native_sample_stride,
            "native_steps": native_steps,
            "native_timestep_seconds": native_timestep_seconds,
        },
        "orca": {
            "goal_latch_sticky": True,
            "max_neighbors": count - 1,
            "neighbor_distance_m": NEIGHBOR_DISTANCE_M,
            "openmp_enabled": False,
            "time_horizon_obstacles_seconds": TIME_HORIZON_SECONDS,
            "time_horizon_seconds": TIME_HORIZON_SECONDS,
        },
    }


def compile_inputs(
    output_root: pathlib.Path,
    *,
    source_root: pathlib.Path | None = None,
) -> dict[str, Any]:
    root = output_root.resolve()
    source = (
        REPO.joinpath(*SOURCE_RELATIVE.parts)
        if source_root is None
        else source_root.resolve()
    )
    if root.exists() or root.is_symlink():
        raise InputError(f"output already exists: {root}")
    source_manifest = multimap.load_and_validate(source)
    selected = [
        scene
        for scene in source_manifest["scenes"]
        if scene["family"] == "open"
        and scene["map_id"] == "canonical"
        and scene["robot_count"] == 8
        and scene["mission_label"] in OPEN_MISSIONS
    ]
    selected.sort(key=lambda item: item["mission_label"])
    if [item["mission_label"] for item in selected] != list(OPEN_MISSIONS):
        raise InputError("source open-mission inventory differs")

    root.mkdir(parents=True, exist_ok=False)
    files: list[dict[str, Any]] = []
    scenes: list[dict[str, Any]] = []
    try:
        for source_scene in selected:
            label = int(source_scene["mission_label"])
            source_path = source / source_scene["scenario_relpath"]
            raw = source_path.read_bytes()
            if sha256_bytes(raw) != source_scene["scenario_sha256"]:
                raise InputError("source open scenario identity differs")
            scenario = json.loads(raw)
            _validate_two_waypoint_scenario(
                scenario, count=8, label=f"open mission {label}"
            )
            scenario_rel = pathlib.PurePosixPath(
                "scenarios", "open", f"mission-{label:02d}", "n-8", "scenario.json"
            )
            _write(root, scenario_rel, raw)
            files.append(_identity(scenario_rel, raw))
            scene_id = f"orca-open-m{label:02d}-n8"
            config_rel = pathlib.PurePosixPath(
                "configs", "open", f"mission-{label:02d}", "n-8", "config.yaml"
            )
            config_raw = render_config(
                scene_id=scene_id,
                scenario_relpath=scenario_rel,
                count=8,
                steps=OPEN_STEPS,
                world_size_m=WORLD_SIZE_GUARD_M,
                rng_seed=runtime_seed(label),
            )
            _write(root, config_rel, config_raw)
            files.append(_identity(config_rel, config_raw))
            scenes.append(
                _scene_record(
                    scene_id=scene_id,
                    scene_set="open",
                    scenario_relpath=scenario_rel,
                    scenario_raw=raw,
                    config_relpath=config_rel,
                    config_raw=config_raw,
                    count=8,
                    steps=OPEN_STEPS,
                    rng_seed=runtime_seed(label),
                    source_scenario={
                        "path": (SOURCE_RELATIVE / pathlib.PurePosixPath(source_scene["scenario_relpath"])).as_posix(),
                        "sha256": source_scene["scenario_sha256"],
                    },
                )
            )

        for count in CIRCLE_COUNTS:
            scenario = circle_scenario(count)
            _validate_two_waypoint_scenario(
                scenario, count=count, label=f"circle n={count}"
            )
            scenario_raw = canonical_json_bytes(scenario)
            scenario_rel = pathlib.PurePosixPath(
                "scenarios", "circle", f"n-{count}", "scenario.json"
            )
            _write(root, scenario_rel, scenario_raw)
            files.append(_identity(scenario_rel, scenario_raw))
            scene_id = f"orca-circle-n{count}"
            config_rel = pathlib.PurePosixPath(
                "configs", "circle", f"n-{count}", "config.yaml"
            )
            config_raw = render_config(
                scene_id=scene_id,
                scenario_relpath=scenario_rel,
                count=count,
                steps=CIRCLE_STEPS,
                world_size_m=WORLD_SIZE_GUARD_M,
                rng_seed=runtime_seed(None),
            )
            _write(root, config_rel, config_raw)
            files.append(_identity(config_rel, config_raw))
            scenes.append(
                _scene_record(
                    scene_id=scene_id,
                    scene_set="circle",
                    scenario_relpath=scenario_rel,
                    scenario_raw=scenario_raw,
                    config_relpath=config_rel,
                    config_raw=config_raw,
                    count=count,
                    steps=CIRCLE_STEPS,
                    rng_seed=runtime_seed(None),
                    source_scenario=None,
                )
            )

        crosswalk = parameter_crosswalk()
        crosswalk_raw = canonical_json_bytes(crosswalk)
        crosswalk_rel = pathlib.PurePosixPath("parameter_crosswalk.json")
        _write(root, crosswalk_rel, crosswalk_raw)
        files.append(_identity(crosswalk_rel, crosswalk_raw))

        schedule = {
            "attempts": schedule_records(scenes),
            "schema_version": SCHEDULE_SCHEMA,
            "study_id": STUDY_ID,
        }
        schedule_raw = canonical_json_bytes(schedule)
        schedule_rel = pathlib.PurePosixPath("schedule.json")
        _write(root, schedule_rel, schedule_raw)
        files.append(_identity(schedule_rel, schedule_raw))

        manifest: dict[str, Any] = {
            "classification": {
                "algorithm_execution_during_generation": False,
                "planner_outcomes_read": False,
                "result_directory_read": False,
                "scene_selection_after_outcomes": False,
            },
            "design": {
                "circle_counts": list(CIRCLE_COUNTS),
                "circle_steps": CIRCLE_STEPS,
                "expected_processes": EXPECTED_PROCESSES,
                "main_timestep_seconds": MAIN_TIMESTEP_SECONDS,
                "open_missions": list(OPEN_MISSIONS),
                "open_steps": OPEN_STEPS,
                "process_timeout_seconds": PROCESS_TIMEOUT_SECONDS,
                "sensitivity_steps": CIRCLE_SENSITIVITY_STEPS,
                "sensitivity_timestep_seconds": SENSITIVITY_TIMESTEP_SECONDS,
                "world_size_guard_m": WORLD_SIZE_GUARD_M,
            },
            "files": sorted(files, key=lambda item: item["path"]),
            "effective_parameter_crosswalk": crosswalk,
            "generator": _repository_identity(
                pathlib.PurePosixPath("scripts/replication/prepare_orca_inputs.py")
            ),
            "parameter_crosswalk": _identity(crosswalk_rel, crosswalk_raw),
            "protocol": _repository_identity(PROTOCOL_RELATIVE),
            "protocol_amendment": _repository_identity(
                PROTOCOL_AMENDMENT_RELATIVE
            ),
            "protocol_amendment_2": _repository_identity(
                PROTOCOL_AMENDMENT_2_RELATIVE
            ),
            "prefreeze_wrapper_diagnostic": _repository_identity(
                PREFREEZE_DIAGNOSTIC_RELATIVE
            ),
            "root_repository_relative": OUTPUT_RELATIVE.as_posix(),
            "scenes": scenes,
            "schedule": _identity(schedule_rel, schedule_raw),
            "schema_version": SCHEMA,
            "source_multimap_manifest": {
                "bytes": (source / "manifest.json").stat().st_size,
                "path": (SOURCE_RELATIVE / "manifest.json").as_posix(),
                "sha256": sha256_file(source / "manifest.json"),
            },
            "study_id": STUDY_ID,
        }
        validate_manifest(root, manifest, source_root=source)
        manifest_raw = canonical_json_bytes(manifest)
        _write(root, pathlib.PurePosixPath("manifest.json"), manifest_raw)
        _write(
            root,
            pathlib.PurePosixPath("manifest.sha256"),
            f"{sha256_bytes(manifest_raw)}  manifest.json\n".encode("ascii"),
        )
        return manifest
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


def validate_manifest(
    root: pathlib.Path,
    manifest: Mapping[str, Any],
    *,
    source_root: pathlib.Path | None = None,
) -> None:
    source = (
        REPO.joinpath(*SOURCE_RELATIVE.parts)
        if source_root is None
        else source_root.resolve()
    )
    if manifest.get("schema_version") != SCHEMA or manifest.get("study_id") != STUDY_ID:
        raise InputError("input manifest schema/study differs")
    for key, relative in (
        ("generator", pathlib.PurePosixPath("scripts/replication/prepare_orca_inputs.py")),
        ("protocol", PROTOCOL_RELATIVE),
        ("protocol_amendment", PROTOCOL_AMENDMENT_RELATIVE),
        ("protocol_amendment_2", PROTOCOL_AMENDMENT_2_RELATIVE),
        ("prefreeze_wrapper_diagnostic", PREFREEZE_DIAGNOSTIC_RELATIVE),
    ):
        expected = _repository_identity(relative)
        if manifest.get(key) != expected:
            raise InputError(f"{key} identity differs")
    source_identity = manifest.get("source_multimap_manifest")
    if not isinstance(source_identity, dict):
        raise InputError("source multimap identity missing")
    source_manifest_path = source / "manifest.json"
    if (
        source_manifest_path.is_symlink()
        or not source_manifest_path.is_file()
        or source_manifest_path.stat().st_size != source_identity.get("bytes")
        or sha256_file(source_manifest_path) != source_identity.get("sha256")
    ):
        raise InputError("source multimap manifest identity differs")
    multimap.load_and_validate(source)

    files = manifest.get("files")
    scenes = manifest.get("scenes")
    if not isinstance(files, list) or not isinstance(scenes, list):
        raise InputError("file or scene inventory missing")
    if len(scenes) != EXPECTED_SCENES:
        raise InputError("scene inventory must contain 23 scenes")
    if manifest.get("effective_parameter_crosswalk") != parameter_crosswalk():
        raise InputError("embedded effective parameter crosswalk differs")
    paths = [item["path"] for item in files]
    if len(paths) != len(set(paths)):
        raise InputError("file inventory contains duplicate paths")
    for item in files:
        path = root.joinpath(*pathlib.PurePosixPath(item["path"]).parts)
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != item["bytes"]
            or sha256_file(path) != item["sha256"]
        ):
            raise InputError(f"input file identity differs: {item['path']}")
    if len({scene["scene_id"] for scene in scenes}) != EXPECTED_SCENES:
        raise InputError("scene identifiers are not unique")
    if sum(scene["scene_set"] == "open" for scene in scenes) != 20:
        raise InputError("open scene count differs")
    if sum(scene["scene_set"] == "circle" for scene in scenes) != 3:
        raise InputError("circle scene count differs")
    for scene in scenes:
        scenario_path = root / scene["scenario_relpath"]
        config_path = root / scene["config_relpath"]
        if sha256_file(scenario_path) != scene["scenario_sha256"]:
            raise InputError("scene scenario hash differs")
        if sha256_file(config_path) != scene["config_sha256"]:
            raise InputError("scene config hash differs")
        config = config_path.read_text(encoding="utf-8")
        if "ASSETS_DIR" in config or "OBSTACLE_FILE" in config:
            raise InputError("scene config contains an obstacle input")
        _validate_config_text(
            config,
            expected_world_size_m=float(manifest["design"]["world_size_guard_m"]),
        )
        scenario = json.loads(scenario_path.read_bytes())
        _validate_two_waypoint_scenario(
            scenario, count=int(scene["robot_count"]), label=scene["scene_id"]
        )
        if scene["scene_set"] == "open":
            mission_label = scenario.get("metadata", {}).get("mission_label")
            expected_rng_seed = runtime_seed(int(mission_label))
        else:
            expected_rng_seed = runtime_seed(None)
        if scene.get("resolved_parameters") != resolved_parameters(
            int(scene["robot_count"]),
            int(scene["steps"]),
            float(scene["timestep_seconds"]),
            int(scene["native_sample_stride"]),
            expected_rng_seed,
        ):
            raise InputError("scene resolved parameters differ")
        source_record = scene.get("source_scenario")
        if scene["scene_set"] == "open":
            if not isinstance(source_record, dict):
                raise InputError("open source-scenario provenance missing")
            source_path = REPO / source_record["path"]
            if source_root is not None:
                source_path = source / pathlib.PurePosixPath(source_record["path"]).relative_to(SOURCE_RELATIVE)
            if (
                not source_path.is_file()
                or sha256_file(source_path) != source_record["sha256"]
                or source_path.read_bytes() != scenario_path.read_bytes()
            ):
                raise InputError("open scenario is not a byte-exact source copy")
        elif source_record is not None:
            raise InputError("circle scene unexpectedly has source-scenario provenance")

    crosswalk_path = root / manifest["parameter_crosswalk"]["path"]
    if json.loads(crosswalk_path.read_bytes()) != parameter_crosswalk():
        raise InputError("parameter crosswalk differs")
    schedule_path = root / manifest["schedule"]["path"]
    schedule = json.loads(schedule_path.read_bytes())
    if (
        schedule.get("schema_version") != SCHEDULE_SCHEMA
        or schedule.get("study_id") != STUDY_ID
        or schedule.get("attempts") != schedule_records(scenes)
    ):
        raise InputError("schedule differs")


def load_and_validate(
    root: pathlib.Path,
    *,
    source_root: pathlib.Path | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    raw = (root / "manifest.json").read_bytes()
    manifest = json.loads(raw)
    if raw != canonical_json_bytes(manifest):
        raise InputError("manifest is not canonical JSON")
    validate_manifest(root, manifest, source_root=source_root)
    sidecar = (root / "manifest.sha256").read_text(encoding="ascii").split()
    if sidecar != [sha256_bytes(raw), "manifest.json"]:
        raise InputError("manifest sidecar differs")
    return manifest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--output-root", type=pathlib.Path, required=True)
    check = subparsers.add_parser("check")
    check.add_argument("--output-root", type=pathlib.Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "prepare":
            manifest = compile_inputs(args.output_root)
        else:
            manifest = load_and_validate(args.output_root)
        print(
            f"validated {len(manifest['scenes'])} scenes and "
            f"{manifest['design']['expected_processes']} scheduled processes"
        )
        return 0
    except (InputError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"DANCeRS--ORCA input preparation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
