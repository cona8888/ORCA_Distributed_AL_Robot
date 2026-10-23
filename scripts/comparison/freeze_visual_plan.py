#!/usr/bin/env python3
"""Freeze the complete laptop visual-diagnostic calibration plan.

The plan is diagnostic-only.  This utility never launches a DANCeRS binary,
never opens a replay, and never writes beneath ``results``.  It verifies the
committed simulator contract and the registered build/environment identities,
materializes the 25 registered exact-N scenario prefixes, enumerates the full
250-scene crossing, and publishes the plan once without overwriting.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import math
import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


PLAN_SCHEMA = "dancers-simulator-visual-plan-v1"
PLAN_ID = "visual-diagnostic-calibration-v1"
TRACK_ID = "simulator-demo-v1-kinematic-visual-diagnostic"
CONTRACT_COMMIT = "b8ecf36b98ca1ec415110946864a8bb7711afa4c"
CONTRACT_PATH = PurePosixPath(
    "docs/simulator/simulator_contract_v1.json"
)
PLAN_DIRECTORY = PurePosixPath(
    "comparison/plans/simulator-demo/v1/"
    "visual-diagnostic-calibration-v1"
)
PLAN_PATH = PLAN_DIRECTORY / "plan.json"
RESULT_ROOT = PurePosixPath("results/simulator-demo")
ALLOWED_OUTPUT_ROOT = PurePosixPath(
    "results/simulator-demo/v1/visual-diagnostic/calibration"
)
FAMILIES = (
    "open",
    "perfect_maze",
    "fractal_low",
    "fractal_medium",
    "fractal_high",
)
COUNTS = (8, 16, 32, 64, 128)
SEEDS = tuple(range(1, 11))
METHOD_ORDER = ("reference", "optimized")
EXPECTED_SCENE_COUNT = len(FAMILIES) * len(COUNTS) * len(SEEDS)
EXPECTED_ATTEMPT_COUNT = EXPECTED_SCENE_COUNT * len(METHOD_ORDER)
REFERENCE_REVISION = "986f47deec5b079663c7d7f6120bfc20f0467901"
OPTIMIZED_REVISION = "99364a41f1535a4bb0eddbe5a10030127ea77086"
ARTIFACT_PATHS = {
    "build_manifest": PurePosixPath("comparison/build_manifest.json"),
    "environment_manifest": PurePosixPath(
        "comparison/environments/manifest.json"
    ),
    "pixi_lock": PurePosixPath("pixi.lock"),
    "config_template": PurePosixPath(
        "config/comparison/laptop_benchmark.yaml"
    ),
    "trace_compare": PurePosixPath(
        "scripts/comparison/trace_compare.py"
    ),
    "viewer": PurePosixPath(
        "scripts/comparison/build_replay_viewer.py"
    ),
    "controller": PurePosixPath(
        "scripts/comparison/run_visual_diagnostic.py"
    ),
    "plan_freezer": PurePosixPath(
        "scripts/comparison/freeze_visual_plan.py"
    ),
    "capacity_materializer": PurePosixPath(
        "scripts/comparison/materialize_capacity_scenario.py"
    ),
}
TRACKED_INPUTS = tuple(
    dict.fromkeys((CONTRACT_PATH, *ARTIFACT_PATHS.values()))
)


class PlanFreezeError(RuntimeError):
    """An identity, registry, plan, or publication failure."""


class _DuplicateKey(ValueError):
    pass


@dataclass(frozen=True)
class PlanBundle:
    plan: dict[str, Any]
    plan_bytes: bytes
    scenarios: dict[PurePosixPath, bytes]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise _DuplicateKey(f"duplicate JSON key {key!r}")
        output[key] = value
    return output


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


def canonical_scenario_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _strict_json_bytes(data: bytes, label: str) -> Any:
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateKey,
        ValueError,
        RecursionError,
    ) as error:
        raise PlanFreezeError(f"{label} is not strict JSON: {error}") from error


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PlanFreezeError(f"{label} must be an object")
    return value


def _exact_keys(
    value: Mapping[str, Any], expected: Iterable[str], label: str
) -> None:
    expected_set = set(expected)
    missing = expected_set - value.keys()
    unknown = value.keys() - expected_set
    if missing or unknown:
        raise PlanFreezeError(
            f"{label} keys differ; missing={sorted(missing)}, "
            f"unknown={sorted(unknown)}"
        )


def _regular_file(path: Path, label: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise PlanFreezeError(f"{label} is unavailable: {error}") from error
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise PlanFreezeError(
            f"{label} must be a regular non-symlink file: {path}"
        )


def _resolve(root: Path, relative: PurePosixPath, label: str) -> Path:
    if (
        relative.is_absolute()
        or any(part in ("", ".", "..") for part in relative.parts)
    ):
        raise PlanFreezeError(f"{label} is not repository-relative")
    candidate = root.joinpath(*relative.parts)
    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise PlanFreezeError(
                f"{label} contains a symlink component: {current}"
            )
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise PlanFreezeError(f"{label} escapes the repository") from error
    return resolved


def _read_regular(
    root: Path, relative: PurePosixPath, label: str
) -> tuple[Path, bytes]:
    path = _resolve(root, relative, label)
    _regular_file(path, label)
    try:
        return path, path.read_bytes()
    except OSError as error:
        raise PlanFreezeError(f"cannot read {label}: {error}") from error


def _artifact(root: Path, relative: PurePosixPath, label: str) -> dict[str, str]:
    _, data = _read_regular(root, relative, label)
    return {"path": relative.as_posix(), "sha256": sha256_bytes(data)}


def _git(
    root: Path, arguments: Sequence[str], *, binary: bool = False
) -> bytes | str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=not binary,
    )
    if completed.returncode != 0:
        stderr = completed.stderr
        detail = (
            stderr.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes)
            else stderr
        ).strip()
        raise PlanFreezeError(
            f"git {' '.join(arguments)} failed: {detail}"
        )
    return completed.stdout


def _verify_git_state(root: Path) -> None:
    remotes = str(_git(root, ("remote",))).splitlines()
    if any(line.strip() for line in remotes):
        raise PlanFreezeError("repository must have zero Git remotes")
    resolved = str(
        _git(
            root,
            ("rev-parse", "--verify", f"{CONTRACT_COMMIT}^{{commit}}"),
        )
    ).strip()
    if resolved != CONTRACT_COMMIT:
        raise PlanFreezeError("contract commit does not resolve exactly")
    for relative in TRACKED_INPUTS:
        path_text = relative.as_posix()
        _git(root, ("ls-files", "--error-unmatch", "--", path_text))
        status = str(
            _git(
                root,
                ("status", "--porcelain", "--untracked-files=all", "--", path_text),
            )
        )
        if status:
            raise PlanFreezeError(
                f"tracked plan input is not clean: {path_text}"
            )


def _committed_contract(root: Path, working_bytes: bytes) -> None:
    committed = _git(
        root,
        ("show", f"{CONTRACT_COMMIT}:{CONTRACT_PATH.as_posix()}"),
        binary=True,
    )
    assert isinstance(committed, bytes)
    if committed != working_bytes:
        raise PlanFreezeError(
            "working contract bytes differ from the registered commit"
        )


def _verify_contract(root: Path) -> tuple[dict[str, Any], bytes]:
    _, data = _read_regular(root, CONTRACT_PATH, "simulator contract")
    value = _strict_json_bytes(data, "simulator contract")
    contract = _mapping(value, "simulator contract")
    if data != canonical_json_bytes(contract):
        raise PlanFreezeError("simulator contract is not canonical JSON")
    if contract.get("contract_id") != "dancers-simulator-demo-v1":
        raise PlanFreezeError("simulator contract ID differs")
    status = _mapping(contract.get("status"), "contract.status")
    if (
        status.get("classification") != "prospective-execution-contract"
        or status.get("execution_authorized_by_this_checkpoint") is not False
        or status.get("result_root_state_at_freeze") != "absent"
    ):
        raise PlanFreezeError("simulator contract status differs")
    visual = _mapping(
        contract.get("visual_diagnostic"), "contract.visual_diagnostic"
    )
    expected_visual = {
        "track_id": TRACK_ID,
        "initial_counts": list(COUNTS),
        "initial_seeds": list(SEEDS),
        "paired_scene_count": EXPECTED_SCENE_COUNT,
        "fresh_process_attempt_count": EXPECTED_ATTEMPT_COUNT,
        "timesteps": 100,
        "timestep_seconds": 0.1,
        "simulated_duration_seconds": 10,
        "trace_detail": "positions",
        "trace_schema": "dancers-comparison-trace-v3",
        "rendering_in_compute_measurement": False,
    }
    for key, expected in expected_visual.items():
        if visual.get(key) != expected:
            raise PlanFreezeError(
                f"contract visual_diagnostic.{key} differs"
            )
    process = _mapping(
        visual.get("process_policy"),
        "contract.visual_diagnostic.process_policy",
    )
    if (
        process.get("serial_execution") is not True
        or process.get("fresh_process_per_method") is not True
        or process.get("viewer_active_during_solver") is not False
    ):
        raise PlanFreezeError("contract visual process policy differs")
    return contract, data


def _verify_registered_artifacts(
    root: Path, contract: dict[str, Any]
) -> dict[str, dict[str, str]]:
    artifacts = {
        key: _artifact(root, relative, f"artifact {key}")
        for key, relative in ARTIFACT_PATHS.items()
    }
    identities = _mapping(contract.get("identities"), "contract.identities")
    expected_contract_hashes = {
        "build_manifest": artifacts["build_manifest"]["sha256"],
        "environment_manifest": artifacts["environment_manifest"]["sha256"],
    }
    for key, digest in expected_contract_hashes.items():
        record = _mapping(identities.get(key), f"contract.identities.{key}")
        if (
            record.get("path") != artifacts[key]["path"]
            or record.get("sha256") != digest
        ):
            raise PlanFreezeError(f"contract {key} identity differs")
    if identities.get("pixi_lock_sha256") != artifacts["pixi_lock"]["sha256"]:
        raise PlanFreezeError("contract Pixi lock identity differs")
    binaries = _mapping(
        identities.get("initial_visual_binaries"),
        "contract.identities.initial_visual_binaries",
    )
    if set(binaries) != {
        "reference_fidelity_sha256",
        "optimized_fidelity_sha256",
    }:
        raise PlanFreezeError("contract visual binary identity keys differ")
    return artifacts


def _build_methods(
    root: Path,
    build_manifest: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    if (
        build_manifest.get("schema_version")
        != "dancers-comparison-build-manifest-v1"
    ):
        raise PlanFreezeError("unsupported build manifest")
    repository = _mapping(
        build_manifest.get("repository"), "build manifest.repository"
    )
    if (
        repository.get("remote_count") != 0
        or repository.get("origin_present") is not False
        or repository.get("reference_pin") != REFERENCE_REVISION
        or repository.get("optimized_pin") != OPTIMIZED_REVISION
    ):
        raise PlanFreezeError("build manifest local source identity differs")
    builds = _mapping(build_manifest.get("builds"), "build manifest.builds")
    contract_binaries = _mapping(
        _mapping(contract.get("identities"), "contract.identities").get(
            "initial_visual_binaries"
        ),
        "contract initial visual binaries",
    )
    methods: dict[str, Any] = {}
    revisions = {
        "reference": REFERENCE_REVISION,
        "optimized": OPTIMIZED_REVISION,
    }
    for method_id in METHOD_ORDER:
        build_key = f"{method_id}_fidelity"
        build = _mapping(builds.get(build_key), f"builds.{build_key}")
        binary_relative = PurePosixPath(str(build.get("binary_path")))
        binary = _artifact(
            root, binary_relative, f"{method_id} fidelity binary"
        )
        expected_digest = contract_binaries[
            f"{method_id}_fidelity_sha256"
        ]
        if (
            build.get("observer_compiled") is not True
            or build.get("algorithm_revision") != revisions[method_id]
            or build.get("binary_sha256") != binary["sha256"]
            or binary["sha256"] != expected_digest
        ):
            raise PlanFreezeError(f"{method_id} fidelity build differs")
        binary_path = _resolve(
            root, binary_relative, f"{method_id} fidelity binary"
        )
        if not os.access(binary_path, os.X_OK):
            raise PlanFreezeError(
                f"{method_id} fidelity binary is not executable"
            )
        methods[method_id] = {
            "build_manifest_key": build_key,
            "binary": binary,
            "source_revision": revisions[method_id],
        }
    if (
        methods["reference"]["binary"]["path"]
        == methods["optimized"]["binary"]["path"]
    ):
        raise PlanFreezeError("reference and optimized binaries are identical")
    return methods


def _environment_cases(
    root: Path, environment_manifest: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    if (
        environment_manifest.get("schema_version") != 1
        or environment_manifest.get("artifact_hash") != "SHA-256"
        or environment_manifest.get("families") != list(FAMILIES)
    ):
        raise PlanFreezeError("environment manifest identity differs")
    values = environment_manifest.get("environments")
    if not isinstance(values, list):
        raise PlanFreezeError("environment manifest cases are not an array")
    cases: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(values):
        case = _mapping(value, f"environment case {index}")
        if case.get("split") != "calibration":
            continue
        family = case.get("family")
        if family not in FAMILIES or family in cases:
            raise PlanFreezeError(
                "calibration environment families are not unique"
            )
        cases[family] = case
    if tuple(cases) != FAMILIES:
        raise PlanFreezeError(
            "calibration environment family order differs"
        )
    return cases


def _environment_descriptor(
    case: dict[str, Any], family: str
) -> dict[str, Any]:
    runtime = _mapping(
        case.get("effective_runtime_1000"),
        f"{family}.effective_runtime_1000",
    )
    metrics = _mapping(
        runtime.get("map_metrics"),
        f"{family}.effective_runtime_1000.map_metrics",
    )
    box = _mapping(
        metrics.get("boundary_box_counting"),
        f"{family}.boundary_box_counting",
    )
    free = _mapping(metrics.get("free_space"), f"{family}.free_space")
    descriptor = {
        "obstacle_occupancy": metrics.get("obstacle_occupancy"),
        "boundary_density": metrics.get("boundary_density"),
        "boundary_box_counting": {
            "dimension": box.get("dimension"),
            "r_squared": box.get("r_squared"),
        },
        "free_space": {
            "component_count_4_connected": free.get(
                "component_count_4_connected"
            ),
            "reachable_fraction": free.get("reachable_fraction"),
        },
    }
    numbers = (
        ("obstacle_occupancy", descriptor["obstacle_occupancy"]),
        ("boundary_density", descriptor["boundary_density"]),
        (
            "boundary_box_counting.dimension",
            descriptor["boundary_box_counting"]["dimension"],
        ),
        (
            "boundary_box_counting.r_squared",
            descriptor["boundary_box_counting"]["r_squared"],
        ),
        (
            "free_space.reachable_fraction",
            descriptor["free_space"]["reachable_fraction"],
        ),
    )
    for name, value in numbers:
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            raise PlanFreezeError(
                f"{family} descriptor {name} is not finite"
            )
    for name, value in (
        ("obstacle_occupancy", descriptor["obstacle_occupancy"]),
        ("boundary_density", descriptor["boundary_density"]),
        (
            "boundary_box_counting.r_squared",
            descriptor["boundary_box_counting"]["r_squared"],
        ),
        (
            "free_space.reachable_fraction",
            descriptor["free_space"]["reachable_fraction"],
        ),
    ):
        if not 0.0 <= float(value) <= 1.0:
            raise PlanFreezeError(
                f"{family} descriptor {name} is outside [0, 1]"
            )
    if float(descriptor["boundary_box_counting"]["dimension"]) <= 0.0:
        raise PlanFreezeError(
            f"{family} boundary box-counting descriptor is not positive"
        )
    component_count = descriptor["free_space"][
        "component_count_4_connected"
    ]
    if (
        not isinstance(component_count, int)
        or isinstance(component_count, bool)
        or component_count < 0
    ):
        raise PlanFreezeError(
            f"{family} connected-component count is invalid"
        )
    return descriptor


def _registered_file(
    root: Path,
    base: PurePosixPath,
    record: Any,
    label: str,
) -> tuple[PurePosixPath, bytes]:
    item = _mapping(record, label)
    relative_value = item.get("path")
    if not isinstance(relative_value, str):
        raise PlanFreezeError(f"{label}.path must be a string")
    relative = base / PurePosixPath(relative_value)
    _, data = _read_regular(root, relative, label)
    if (
        item.get("bytes") != len(data)
        or item.get("sha256") != sha256_bytes(data)
    ):
        raise PlanFreezeError(f"{label} hash or byte count differs")
    return relative, data


def _scenario_prefix(
    bank: dict[str, Any], family: str, count: int
) -> dict[str, Any]:
    metadata = _mapping(bank.get("metadata"), f"{family} capacity metadata")
    bank_count = metadata.get("capacity_bank_robot_count")
    if (
        not isinstance(bank_count, int)
        or isinstance(bank_count, bool)
        or count > bank_count
    ):
        raise PlanFreezeError(f"{family} capacity bank is too small")
    expected_ids = [str(index) for index in range(count)]
    decisions = _mapping(bank.get("decisions"), f"{family} bank decisions")
    waypoints = _mapping(bank.get("waypoints"), f"{family} bank waypoints")
    try:
        prefix = {
            "decisions": {
                robot_id: decisions[robot_id] for robot_id in expected_ids
            },
            "metadata": {**metadata, "prefix_robot_count": count},
            "num_robots": count,
            "seed_robots": [
                robot_id
                for robot_id in bank.get("seed_robots", [])
                if int(robot_id) < count
            ],
            "waypoints": {
                robot_id: waypoints[robot_id] for robot_id in expected_ids
            },
        }
    except (KeyError, TypeError, ValueError) as error:
        raise PlanFreezeError(
            f"{family} capacity bank cannot form N={count}"
        ) from error
    if (
        set(prefix["decisions"]) != set(expected_ids)
        or set(prefix["waypoints"]) != set(expected_ids)
    ):
        raise PlanFreezeError(f"{family} N={count} robot keys differ")
    return prefix


def _family_materials(
    root: Path,
    family: str,
    case: dict[str, Any],
) -> tuple[dict[str, str], dict[int, tuple[PurePosixPath, bytes]]]:
    map_relative, map_data = _registered_file(
        root,
        PurePosixPath("comparison/environments"),
        case.get("map"),
        f"{family} calibration map",
    )
    capacity = _mapping(case.get("capacity"), f"{family} capacity")
    bank_relative, bank_data = _registered_file(
        root,
        PurePosixPath("comparison/environments"),
        capacity.get("bank"),
        f"{family} capacity bank",
    )
    del bank_relative
    bank_value = _strict_json_bytes(bank_data, f"{family} capacity bank")
    bank = _mapping(bank_value, f"{family} capacity bank")
    metadata = _mapping(bank.get("metadata"), f"{family} capacity metadata")
    if (
        metadata.get("environment_id") != family
        or metadata.get("split") != "calibration"
        or metadata.get("map_sha256") != sha256_bytes(map_data)
        or metadata.get("capacity_bank_id") != capacity.get("bank_id")
    ):
        raise PlanFreezeError(f"{family} capacity bank metadata differs")
    prefixes = capacity.get("prefix_scenarios")
    if not isinstance(prefixes, list):
        raise PlanFreezeError(f"{family} prefix registry is not an array")
    registered = {
        item.get("robot_count"): item
        for item in prefixes
        if isinstance(item, dict) and item.get("robot_count") in COUNTS
    }
    if set(registered) != set(COUNTS):
        raise PlanFreezeError(
            f"{family} lacks all five registered exact-N prefixes"
        )
    scenario_materials: dict[int, tuple[PurePosixPath, bytes]] = {}
    for count in COUNTS:
        scenario = _scenario_prefix(bank, family, count)
        data = canonical_scenario_bytes(scenario)
        record = _mapping(
            registered[count], f"{family} N={count} prefix registry"
        )
        if (
            record.get("bytes") != len(data)
            or record.get("sha256") != sha256_bytes(data)
        ):
            raise PlanFreezeError(
                f"{family} N={count} derived scenario differs"
            )
        scenario_relative = (
            PLAN_DIRECTORY / "scenarios" / family / f"n-{count}.json"
        )
        scenario_materials[count] = (scenario_relative, data)
    return (
        {"path": map_relative.as_posix(), "sha256": sha256_bytes(map_data)},
        scenario_materials,
    )


def validate_plan(plan: Any) -> dict[str, Any]:
    record = _mapping(plan, "visual plan")
    _exact_keys(
        record,
        {
            "schema_version",
            "plan_id",
            "track_id",
            "phase",
            "status",
            "diagnostic_only",
            "compute_claim",
            "timing_unlock",
            "paper_replication_evidence",
            "contract",
            "artifacts",
            "methods",
            "environment_descriptors",
            "execution",
            "scenes",
            "expected_scene_count",
            "expected_attempt_count",
            "allowed_output_root",
        },
        "visual plan",
    )
    expected_scalars = {
        "schema_version": PLAN_SCHEMA,
        "plan_id": PLAN_ID,
        "track_id": TRACK_ID,
        "phase": "calibration",
        "status": "frozen",
        "diagnostic_only": True,
        "compute_claim": False,
        "timing_unlock": False,
        "paper_replication_evidence": False,
        "expected_scene_count": EXPECTED_SCENE_COUNT,
        "expected_attempt_count": EXPECTED_ATTEMPT_COUNT,
        "allowed_output_root": ALLOWED_OUTPUT_ROOT.as_posix(),
    }
    for key, expected in expected_scalars.items():
        if record.get(key) != expected:
            raise PlanFreezeError(f"visual plan {key} differs")
    scenes = record.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != EXPECTED_SCENE_COUNT:
        raise PlanFreezeError("visual plan does not contain 250 scenes")
    descriptors = _mapping(
        record.get("environment_descriptors"),
        "visual plan environment_descriptors",
    )
    if set(descriptors) != set(FAMILIES):
        raise PlanFreezeError(
            "visual plan environment descriptor families differ"
        )
    for family in FAMILIES:
        descriptor = _mapping(
            descriptors[family], f"environment_descriptors.{family}"
        )
        if set(descriptor) != {
            "obstacle_occupancy",
            "boundary_density",
            "boundary_box_counting",
            "free_space",
        }:
            raise PlanFreezeError(
                f"{family} environment descriptor fields differ"
            )
    expected_conditions = [
        (family, count, seed)
        for family in FAMILIES
        for count in COUNTS
        for seed in SEEDS
    ]
    seen_ids: set[str] = set()
    seen_outputs: set[str] = set()
    for index, (scene, expected) in enumerate(
        zip(scenes, expected_conditions)
    ):
        item = _mapping(scene, f"scene {index}")
        _exact_keys(
            item,
            {
                "scene_id",
                "family",
                "robot_count",
                "seed",
                "map",
                "scenario",
                "output_relpath",
            },
            f"scene {index}",
        )
        family, count, seed = expected
        scene_id = f"{family}-n{count}-seed{seed:02d}"
        output = f"{family}/n-{count}/seed-{seed:02d}"
        if (
            item.get("scene_id") != scene_id
            or item.get("family") != family
            or item.get("robot_count") != count
            or item.get("seed") != seed
            or item.get("output_relpath") != output
        ):
            raise PlanFreezeError(f"scene {index} crossing differs")
        if scene_id in seen_ids or output in seen_outputs:
            raise PlanFreezeError("scene identifiers or outputs repeat")
        seen_ids.add(scene_id)
        seen_outputs.add(output)
    return record


def build_bundle(
    root: Path, *, require_git_clean: bool = True
) -> PlanBundle:
    root = root.resolve()
    if require_git_clean:
        _verify_git_state(root)
    contract, contract_data = _verify_contract(root)
    if require_git_clean:
        _committed_contract(root, contract_data)
    artifacts = _verify_registered_artifacts(root, contract)
    build_value = _strict_json_bytes(
        _read_regular(
            root, ARTIFACT_PATHS["build_manifest"], "build manifest"
        )[1],
        "build manifest",
    )
    build_manifest = _mapping(build_value, "build manifest")
    methods = _build_methods(root, build_manifest, contract)
    environment_value = _strict_json_bytes(
        _read_regular(
            root,
            ARTIFACT_PATHS["environment_manifest"],
            "environment manifest",
        )[1],
        "environment manifest",
    )
    environment_manifest = _mapping(
        environment_value, "environment manifest"
    )
    cases = _environment_cases(root, environment_manifest)

    scenarios: dict[PurePosixPath, bytes] = {}
    family_maps: dict[str, dict[str, str]] = {}
    environment_descriptors = {
        family: _environment_descriptor(cases[family], family)
        for family in FAMILIES
    }
    family_scenarios: dict[str, dict[int, tuple[PurePosixPath, bytes]]] = {}
    for family in FAMILIES:
        map_record, materials = _family_materials(
            root, family, cases[family]
        )
        family_maps[family] = map_record
        family_scenarios[family] = materials
        for relative, data in materials.values():
            if relative in scenarios and scenarios[relative] != data:
                raise PlanFreezeError("scenario destination collision")
            scenarios[relative] = data

    scenes: list[dict[str, Any]] = []
    for family in FAMILIES:
        for count in COUNTS:
            scenario_relative, scenario_data = family_scenarios[family][count]
            for seed in SEEDS:
                scenes.append(
                    {
                        "scene_id": f"{family}-n{count}-seed{seed:02d}",
                        "family": family,
                        "robot_count": count,
                        "seed": seed,
                        "map": family_maps[family],
                        "scenario": {
                            "path": scenario_relative.as_posix(),
                            "sha256": sha256_bytes(scenario_data),
                        },
                        "output_relpath": (
                            f"{family}/n-{count}/seed-{seed:02d}"
                        ),
                    }
                )

    plan = {
        "schema_version": PLAN_SCHEMA,
        "plan_id": PLAN_ID,
        "track_id": TRACK_ID,
        "phase": "calibration",
        "status": "frozen",
        "diagnostic_only": True,
        "compute_claim": False,
        "timing_unlock": False,
        "paper_replication_evidence": False,
        "contract": {
            "commit": CONTRACT_COMMIT,
            "path": CONTRACT_PATH.as_posix(),
            "sha256": sha256_bytes(contract_data),
        },
        "artifacts": artifacts,
        "methods": methods,
        "environment_descriptors": environment_descriptors,
        "execution": {
            "families": list(FAMILIES),
            "counts": list(COUNTS),
            "seeds": list(SEEDS),
            "timesteps": 100,
            "timestep_seconds": 0.1,
            "simulated_duration_seconds": 10,
            "trace_detail": "positions",
            "run_class": "positions-replay",
            "serial_execution": True,
            "fresh_process_per_method": True,
            "method_order": list(METHOD_ORDER),
            "timeout_seconds": 300,
            "termination_grace_seconds": 5,
            "omp_threads": 1,
        },
        "scenes": scenes,
        "expected_scene_count": EXPECTED_SCENE_COUNT,
        "expected_attempt_count": EXPECTED_ATTEMPT_COUNT,
        "allowed_output_root": ALLOWED_OUTPUT_ROOT.as_posix(),
    }
    validate_plan(plan)
    return PlanBundle(
        plan=plan,
        plan_bytes=canonical_json_bytes(plan),
        scenarios=scenarios,
    )


def _write_exclusive(path: Path, data: bytes, mode: int = 0o444) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, mode)
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
        os.chmod(path, mode)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def publish_bundle(bundle: PlanBundle, root: Path, destination: Path) -> Path:
    root = root.resolve()
    destination = destination.resolve(strict=False)
    try:
        destination.relative_to(root)
    except ValueError as error:
        raise PlanFreezeError("plan destination escapes repository") from error
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite existing plan directory: {destination}"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.mkdir(mode=0o755)
    try:
        for relative, data in sorted(
            bundle.scenarios.items(), key=lambda item: item[0].as_posix()
        ):
            within_plan = relative.relative_to(PLAN_DIRECTORY)
            _write_exclusive(
                destination.joinpath(*within_plan.parts), data
            )
        _write_exclusive(destination / "plan.json", bundle.plan_bytes)
        directories = sorted(
            (
                path
                for path in destination.rglob("*")
                if path.is_dir()
            ),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for directory in directories:
            directory.chmod(0o555)
        destination.chmod(0o555)
    except BaseException:
        for directory in [destination, *destination.rglob("*")]:
            if directory.is_dir() and not directory.is_symlink():
                directory.chmod(0o755)
        shutil.rmtree(destination)
        raise
    return destination / "plan.json"


def freeze_registered_plan(root: Path) -> Path:
    root = root.resolve()
    result_root = _resolve(root, RESULT_ROOT, "simulator result root")
    if result_root.exists() or result_root.is_symlink():
        raise PlanFreezeError(
            "results/simulator-demo must remain absent before plan freeze"
        )
    destination = _resolve(root, PLAN_DIRECTORY, "visual plan directory")
    bundle = build_bundle(root, require_git_clean=True)
    published = publish_bundle(bundle, root, destination)
    if published.read_bytes() != bundle.plan_bytes:
        raise PlanFreezeError("published visual plan bytes changed")
    return published


def default_repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="verify and construct in memory without publishing",
    )
    args = parser.parse_args(argv)
    root = default_repository_root()
    try:
        if args.check_only:
            bundle = build_bundle(root, require_git_clean=True)
            print(
                f"verified {len(bundle.plan['scenes'])} scenes and "
                f"{len(bundle.scenarios)} exact-N scenarios; no files written"
            )
            return 0
        output = freeze_registered_plan(root)
    except (
        FileExistsError,
        OSError,
        PlanFreezeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        print(f"visual plan freeze failed: {error}", file=sys.stderr)
        return 1
    print(
        f"wrote {output.relative_to(root)} "
        f"(sha256={sha256_file(output)}, "
        f"{EXPECTED_SCENE_COUNT} scenes, {EXPECTED_ATTEMPT_COUNT} processes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
