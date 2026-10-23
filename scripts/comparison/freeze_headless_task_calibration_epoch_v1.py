#!/usr/bin/env python3
"""Compile the prospective epoch-2 task-only plan entirely in memory."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import sys
import types
from typing import Any, Mapping


sys.dont_write_bytecode = True

EPOCH_SCHEMA = "dancers-headless-task-calibration-epoch-plan-v1"
TRACK_ID = "simulator-demo-v1-headless-epoch-1"
EPOCH_ID = "simulator-demo-v1-headless-epoch-2-task-calibration"
PHASE = "calibration"
RUN_CLASSES = ("digest_fidelity", "positions_replay")
METHODS = ("reference", "optimized")
FAMILIES = (
    "open",
    "perfect_maze",
    "fractal_low",
    "fractal_medium",
    "fractal_high",
)
COUNTS = {
    "open": (8, 10, 12),
    "perfect_maze": (2, 4, 6, 8),
    "fractal_low": (6, 8, 10),
    "fractal_medium": (2, 4, 6, 8),
    "fractal_high": (2, 4, 6, 8),
}
SEEDS = tuple(range(1, 11))
REPETITIONS = {"digest_fidelity": 3, "positions_replay": 1}
PLAN_ROOT = PurePosixPath(
    "comparison/plans/simulator-demo/v1/headless-epoch-2-task-calibration"
)
RESULT_ROOT = PurePosixPath(
    "results/simulator-demo/v1/headless-scientific/epoch-2-task-calibration"
)
SOURCE_PATH = PurePosixPath(
    "scripts/comparison/freeze_headless_task_calibration_epoch_v1.py"
)
RUNNER_PATH = PurePosixPath(
    "scripts/comparison/run_headless_task_calibration_epoch_v1.py"
)
ANALYZER_PATH = PurePosixPath(
    "scripts/comparison/analyze_headless_task_calibration_epoch_v1.py"
)
MISSION_COMPILER_PATH = PurePosixPath(
    "scripts/comparison/generate_headless_task_calibration_missions_v1.py"
)
GEOMETRY_PATH = PurePosixPath("scripts/comparison/headless_geometry_v1.py")
BASE_FREEZER_PATH = PurePosixPath("scripts/comparison/freeze_headless_epoch_v1.py")
PARENT_PLAN_PATH = PurePosixPath(
    "comparison/plans/simulator-demo/v1/headless-epoch-1/plan.json"
)
PARENT_PLAN = {
    "bytes": 5768480,
    "path": PARENT_PLAN_PATH.as_posix(),
    "sha256": "dbf1f06f1fcb3247181e630b5cdd030967ce749fd7b96d9655142a6826376e95",
}
BASE_FREEZER = {
    "bytes": 110059,
    "path": BASE_FREEZER_PATH.as_posix(),
    "sha256": "12398b9b372d4ca4e870a307b640eb2c959130181a4ae090c106cd0847a86391",
}
MISSION_COMPILER = {
    "bytes": 17294,
    "path": MISSION_COMPILER_PATH.as_posix(),
    "sha256": "b24ecc3abd9f936b133f821ea164207c4792e72ecd7ae725c23429529a9f5b91",
}
EXPECTED_SCENES = 180
EXPECTED_WARMUPS = 10
EXPECTED_DIGEST = 1080
EXPECTED_POSITIONS = 360
EXPECTED_PROCESSES = 1450
ORDER_SEED = "e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2e2"
COMPONENT_CLASSIFICATION = {
    "execution_authorized": False,
    "scientific_evidence": False,
    "timing_execution_authorized": False,
    "validation_execution_authorized": False,
}
CANDIDATE_CLASSIFICATION = {
    "benchmark_claim_ready": False,
    "benchmark_execution_authorized": False,
    "calibration_execution_authorized": False,
    "capacity_claim_ready": False,
    "execution_authorized": False,
    "fidelity_claim_ready": False,
    "plan_registered": False,
    "scientific_evidence": False,
    "scientific_evidence_eligible": False,
    "timing_claim_ready": False,
    "timing_execution_authorized": False,
    "validation_claim_ready": False,
    "validation_execution_authorized": False,
    "validation_results_present": False,
}
REGISTERED_CLASSIFICATION = {
    **CANDIDATE_CLASSIFICATION,
    "calibration_execution_authorized": True,
    "execution_authorized": True,
    "plan_registered": True,
    "scientific_evidence_eligible": True,
}
CANDIDATE_STATUS = {
    "execution_ready": False,
    "registration_state": "compiled-candidate-only-not-registered",
}
REGISTERED_STATUS = {
    "execution_ready": True,
    "registration_state": "registered-committed",
}


class EpochFreezeError(ValueError):
    """The task-only plan differs from its closed prospective design."""


@dataclass(frozen=True)
class PlanFile:
    relative_path: PurePosixPath
    data: bytes
    sha256: str
    kind: str


@dataclass(frozen=True)
class EpochCompilation:
    value: dict[str, Any]
    data: bytes
    sha256: str
    files: tuple[PlanFile, ...]

    def by_path(self) -> dict[PurePosixPath, PlanFile]:
        return {item.relative_path: item for item in self.files}


def canonical_json_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (RecursionError, TypeError, UnicodeError, ValueError) as error:
        raise EpochFreezeError(f"value is not canonical JSON: {error}") from error


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise EpochFreezeError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _strict(raw: bytes, label: str) -> Any:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                EpochFreezeError(f"{label} contains {token}")
            ),
        )
    except EpochFreezeError:
        raise
    except (RecursionError, UnicodeError, ValueError) as error:
        raise EpochFreezeError(f"{label} is not strict JSON: {error}") from error
    return value


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(root: Path, identity: Mapping[str, Any], label: str) -> bytes:
    path = root / str(identity["path"])
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise EpochFreezeError(f"cannot read {label}: {error}") from error
    if len(raw) != identity["bytes"] or _sha(raw) != identity["sha256"]:
        raise EpochFreezeError(f"{label} identity differs")
    return raw


def _source_identity(root: Path, relative: PurePosixPath) -> dict[str, Any]:
    path = root.joinpath(*relative.parts)
    raw = path.read_bytes()
    return {"bytes": len(raw), "path": relative.as_posix(), "sha256": _sha(raw)}


def _load_exact(root: Path, identity: Mapping[str, Any], name: str) -> types.ModuleType:
    raw = _read(root, identity, name)
    module = types.ModuleType(name)
    module.__file__ = str(identity["path"])
    sys.modules[name] = module
    try:
        exec(compile(raw, module.__file__, "exec", dont_inherit=True, optimize=0), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _record(
    *, schema: str, identity: Mapping[str, Any], contract: Mapping[str, Any]
) -> dict[str, Any]:
    return {
        "classification": dict(COMPONENT_CLASSIFICATION),
        "contract": dict(contract),
        "identity": dict(identity),
        "schema_version": schema,
        "track_id": TRACK_ID,
    }


def _wrapper(name: str, content: Mapping[str, Any]) -> tuple[dict[str, Any], PlanFile]:
    raw = canonical_json_bytes(content)
    path = PLAN_ROOT / "records" / f"{name}.json"
    wrapper = {
        "bytes": len(raw),
        "content": dict(content),
        "path": path.as_posix(),
        "schema_version": content["schema_version"],
        "sha256": _sha(raw),
    }
    return wrapper, PlanFile(path.relative_to(PLAN_ROOT), raw, _sha(raw), "record")


def _assignment(family: str, count: int, seed: int) -> str:
    ranked = sorted(
        SEEDS,
        key=lambda item: (
            hashlib.sha256(
                "\0".join(
                    (
                        "dancers-headless-task-calibration-order-v1",
                        ORDER_SEED,
                        family,
                        str(count),
                        str(item),
                    )
                ).encode("ascii")
            ).digest(),
            item,
        ),
    )
    return "ABA" if seed in ranked[:5] else "BAB"


def _block_score(family: str, count: int, seed: int) -> bytes:
    return hashlib.sha256(
        "\0".join(
            (
                "dancers-headless-task-calibration-block-v1",
                ORDER_SEED,
                family,
                str(count),
                str(seed),
            )
        ).encode("ascii")
    ).digest()


def _semantic(domain: str, value: Mapping[str, Any]) -> str:
    return _sha(domain.encode("ascii") + b"\0" + canonical_json_bytes(value))


def _binary_from_binding(binding: Mapping[str, Any]) -> dict[str, Any]:
    return {"bytes": binding["bytes"], "path": binding["path"], "sha256": binding["sha256"]}


def compile_epoch(repository_root: Path) -> EpochCompilation:
    root = Path(os.path.abspath(repository_root))
    parent_raw = _read(root, PARENT_PLAN, "registered epoch-1 plan")
    parent = _strict(parent_raw, "registered epoch-1 plan")
    base = _load_exact(root, BASE_FREEZER, "_epoch2_base_freezer")
    mission = _load_exact(root, MISSION_COMPILER, "_epoch2_mission_compiler")
    mission_compilation = mission.compile_task_missions(root)

    base.FUTURE_PLAN_ROOT = PLAN_ROOT.as_posix()
    base.EPOCH_ID = EPOCH_ID
    base.TRACK_ID = TRACK_ID
    base.RUN_CLASSES = RUN_CLASSES
    base.REPETITIONS_BY_RUN_CLASS = dict(REPETITIONS)
    normalized = parent["normalized_config"]
    design = {
        "family_grids": [
            {"counts": list(COUNTS[family]), "family": family, "n0": COUNTS[family][0]}
            for family in FAMILIES
        ],
        "normalized_config": normalized,
        "order_seed": ORDER_SEED,
        "phase": PHASE,
        "sampling_structure": {
            "clustering_unit": "family-by-mission-bank",
            "clustering_unit_fields": ["family", "mission_bank_id"],
            "count_relationship_within_bank": "nested-prefix",
            "independent_scene_assumption": False,
        },
        "schema_version": "dancers-headless-task-calibration-epoch-design-v1",
        "seed_roles": {
            "mission_selection": "fresh-epoch-2-domain-seeds-1-through-10",
            "runtime_rng": "same-numeric-seed-no-activity-assumption",
        },
        "timeout_seconds": 300,
        "track_id": TRACK_ID,
    }
    source_by_key: dict[tuple[str, int, int], dict[str, Any]] = {}
    for artifact in mission_compilation.base_compilation.manifest["artifacts"]:
        for row in artifact["counts"]:
            source_by_key[(artifact["family"], row["robot_count"], artifact["seed"])] = {
                "bank_id": artifact["bank_id"],
                "effective_scenario_sha256": row["effective_scenario_sha256"],
                "generator_seed_uint64": artifact["generator_seed_uint64"],
                "map": artifact["map"],
                "mission_seed": artifact["seed"],
                "scenario": row["scenario"],
            }
    scenes: list[dict[str, Any]] = []
    configs: list[dict[str, Any]] = []
    scene_index: dict[tuple[str, int, int], dict[str, Any]] = {}
    for family in FAMILIES:
        for count in COUNTS[family]:
            for seed in SEEDS:
                source = source_by_key[(family, count, seed)]
                scene_id = f"task-calibration-{family}-n-{count}-seed-{seed:02d}"
                scenario = dict(source["scenario"])
                scenario["path"] = (PLAN_ROOT / scenario["path"]).as_posix()
                config_path = PLAN_ROOT / "configs" / family / f"n-{count}" / f"seed-{seed:02d}" / "config.yaml"
                config_bytes = base._render_config_bytes(
                    scene_id=scene_id,
                    family=family,
                    count=count,
                    seed=seed,
                    map_path=source["map"]["path"],
                    scenario_path=scenario["path"],
                )
                config = {"bytes": len(config_bytes), "path": config_path.as_posix(), "sha256": _sha(config_bytes)}
                binding = {
                    "map_cli_path": source["map"]["path"],
                    "normalized_config": normalized,
                    "num_robots": count,
                    "rng_seed": seed,
                    "scenario_cli_path": scenario["path"],
                    "simulation_name": scene_id,
                }
                normalized_sha = _semantic("dancers-headless-task-normalized-scene-config-v1", binding)
                input_payload = {
                    "controller_timesteps": 100,
                    "effective_runtime_raster_sha256": base.EFFECTIVE_RUNTIME_RASTERS[family],
                    "effective_scenario_sha256": source["effective_scenario_sha256"],
                    "family": family,
                    "map_sha256": source["map"]["sha256"],
                    "mission_bank_id": source["bank_id"],
                    "mission_generator_seed_uint64": source["generator_seed_uint64"],
                    "mission_selection_seed": seed,
                    "normalized_config_sha256": normalized_sha,
                    "phase": PHASE,
                    "rendered_config_sha256": config["sha256"],
                    "robot_count": count,
                    "runtime_rng_seed": seed,
                    "scenario_sha256": scenario["sha256"],
                    "timestep_seconds": 0.1,
                }
                sampling = base._scene_sampling_unit(
                    family=family, count=count, mission_bank_id=source["bank_id"]
                )
                row = {
                    "config": config,
                    "config_binding": binding,
                    "effective_runtime_raster": base._effective_runtime_raster(family),
                    "effective_scenario_sha256": source["effective_scenario_sha256"],
                    "family": family,
                    "input_id": _semantic("dancers-headless-task-exact-input-v1", input_payload),
                    "is_n0": count == COUNTS[family][0],
                    "map": dict(source["map"]),
                    "normalized_config_sha256": normalized_sha,
                    "pattern": _assignment(family, count, seed),
                    "robot_count": count,
                    "sampling_unit": sampling,
                    "scenario": scenario,
                    "scene_id": scene_id,
                    "seed": seed,
                    "seed_roles": base._scene_seed_roles(source, runtime_rng_seed=seed),
                }
                scenes.append(row)
                scene_index[(family, count, seed)] = row
                configs.append({**config, "content_utf8": config_bytes.decode("utf-8")})

    method_bindings = {
        method: {
            run_class: dict(parent["method_bindings"][method][run_class])
            for run_class in RUN_CLASSES
        }
        for method in METHODS
    }
    binary_by_key = {
        binding["binary_key"]: _binary_from_binding(binding)
        for methods in method_bindings.values()
        for binding in methods.values()
    }
    warmups: list[dict[str, Any]] = []
    for family in FAMILIES:
        scene = scene_index[(family, COUNTS[family][0], 1)]
        for method in METHODS:
            key = f"{method}-fidelity"
            warmups.append(
                base._slot(
                    order_index=len(warmups), kind="warmup", run_class="digest_fidelity",
                    family=family, count=COUNTS[family][0], seed=1, repetition=0,
                    pattern="warmup", pair_position=0, method=method, scene=scene,
                    binary_key=key, binary=binary_by_key[key], timeout=300,
                )
            )
    inventories: dict[str, Any] = {}
    for run_class in RUN_CLASSES:
        measured: list[dict[str, Any]] = []
        for family in FAMILIES:
            for count in COUNTS[family]:
                for seed in sorted(SEEDS, key=lambda item: (_block_score(family, count, item), item)):
                    pattern = _assignment(family, count, seed)
                    orders = base.PATTERNS[pattern][: REPETITIONS[run_class]]
                    scene = scene_index[(family, count, seed)]
                    for repetition, order in enumerate(orders, 1):
                        for position, method in enumerate(order):
                            key = f"{method}-fidelity"
                            measured.append(
                                base._slot(
                                    order_index=len(measured), kind="measured", run_class=run_class,
                                    family=family, count=count, seed=seed, repetition=repetition,
                                    pattern=pattern, pair_position=position, method=method, scene=scene,
                                    binary_key=key, binary=binary_by_key[key], timeout=300,
                                )
                            )
        inventories[run_class] = {
            "measured": measured,
            "measured_process_count": len(measured),
            "paired_scene_repetition_count": len(measured) // 2,
            "run_class": run_class,
            "timed": False,
        }

    contracts = {
        "protocol": ("dancers-headless-task-protocol-record-v1", _source_identity(root, PurePosixPath("docs/simulator/HEADLESS_TASK_CALIBRATION_EPOCH_2_ADDENDUM.md")), {"task_only": True, "execution_authorized": False}),
        "mission_compiler": ("dancers-headless-task-mission-compiler-record-v1", _source_identity(root, MISSION_COMPILER_PATH), {"publication_permitted": False, "fresh_epoch_domain": EPOCH_ID}),
        "freezer": ("dancers-headless-task-freezer-record-v1", _source_identity(root, SOURCE_PATH), {"publication_permitted": False, "supported_plan_schema": EPOCH_SCHEMA}),
        "runner": ("dancers-headless-task-runner-record-v1", _source_identity(root, RUNNER_PATH), {"execution_authorized": False, "run_classes": list(RUN_CLASSES), "supported_plan_schema": EPOCH_SCHEMA}),
        "analyzer": ("dancers-headless-task-analyzer-record-v1", _source_identity(root, ANALYZER_PATH), {"result_write_permitted": False, "run_classes": list(RUN_CLASSES), "supported_plan_schema": EPOCH_SCHEMA}),
        "geometry": ("dancers-headless-task-geometry-record-v1", _source_identity(root, GEOMETRY_PATH), {"continuous_swept_geometry": True, "positions_required": True}),
        "parent_plan": ("dancers-headless-task-parent-plan-record-v1", dict(PARENT_PLAN), {"reuse_scope": "activated-runtime-and-normalized-dynamics-only"}),
    }
    input_records: dict[str, Any] = {}
    record_files: list[PlanFile] = []
    for name, (schema, identity, contract) in contracts.items():
        wrapper, file = _wrapper(name, _record(schema=schema, identity=identity, contract=contract))
        input_records[name] = wrapper
        record_files.append(file)
    for name in ("runtime_attestation", "activation_receipt"):
        wrapper, file = _wrapper(name, parent["input_records"][name]["content"])
        input_records[name] = wrapper
        record_files.append(file)
    wrapper, file = _wrapper("mission_receipt", mission_compilation.manifest)
    input_records["mission_receipt"] = wrapper
    record_files.append(file)

    value = {
        "classification": dict(CANDIDATE_CLASSIFICATION),
        "config_artifacts": configs,
        "controls": {
            **parent["controls"],
            "task_only": True,
            "timing_execution_authorized": False,
            "warmups_per_binary_map": 1,
        },
        "design": design,
        "epoch_id": EPOCH_ID,
        "failure_policy": dict(parent["failure_policy"]),
        "input_records": input_records,
        "method_bindings": method_bindings,
        "normalized_config": normalized,
        "operations": {
            "build_invoked": False, "network_accessed": False, "plan_written": False,
            "result_read": False, "result_written": False, "simulator_invoked": False,
            "subprocess_spawned": False,
        },
        "output_grammar": {
            "append_permitted": False, "closed_inventory": True, "create_exclusive": True,
            "destination_root": "explicit-unregistered-candidate-output-root",
            "destination_templates": parent["output_grammar"]["destination_templates"],
            "failed_attempts_retained": True,
            "files_by_kind": {key: parent["output_grammar"]["files_by_kind"][key] for key in (*RUN_CLASSES, "warmup")},
            "registered_result_namespace": RESULT_ROOT.as_posix(),
            "unlisted_outputs_permitted": False,
        },
        "phase": PHASE,
        "scenes": scenes,
        "schedule": {
            "assignment_algorithm": "sha256-ranked-five-five-aba-bab-task-v1",
            "balance": [{"aba_scene_count": 5, "bab_scene_count": 5, "family": family, "robot_count": count} for family in FAMILIES for count in COUNTS[family]],
            "execution_order": ["warmups", *RUN_CLASSES],
            "inventories": inventories,
            "timing_gate": {"headless_timing_permitted": False, "timing_inventory_present": False},
            "warmups": {"process_count": len(warmups), "slots": warmups, "uniqueness_key": ["binary_key", "map_sha256"]},
        },
        "schema_version": EPOCH_SCHEMA,
        "status": dict(CANDIDATE_STATUS),
        "track_id": TRACK_ID,
    }
    data = canonical_json_bytes(value)
    files: list[PlanFile] = []
    for item in mission_compilation.files:
        files.append(PlanFile(item.relative_path, item.data, item.sha256, item.kind))
    for item in configs:
        relative = PurePosixPath(item["path"]).relative_to(PLAN_ROOT)
        raw = item["content_utf8"].encode("utf-8")
        files.append(PlanFile(relative, raw, item["sha256"], "config"))
    files.extend(record_files)
    files.append(PlanFile(PurePosixPath("plan.json"), data, _sha(data), "plan"))
    result = EpochCompilation(value, data, _sha(data), tuple(files))
    validate_compilation(result)
    return result


def validate_epoch_bytes(raw: bytes) -> dict[str, Any]:
    value = _strict(raw, "task-calibration plan")
    if type(value) is not dict or canonical_json_bytes(value) != raw:
        raise EpochFreezeError("task-calibration plan is not canonical JSON")
    required = {"classification","config_artifacts","controls","design","epoch_id","failure_policy","input_records","method_bindings","normalized_config","operations","output_grammar","phase","scenes","schedule","schema_version","status","track_id"}
    if set(value) != required or value["schema_version"] != EPOCH_SCHEMA or value["track_id"] != TRACK_ID or value["epoch_id"] != EPOCH_ID or value["phase"] != PHASE:
        raise EpochFreezeError("task-calibration plan identity differs")
    if (value["classification"], value["status"]) not in ((CANDIDATE_CLASSIFICATION, CANDIDATE_STATUS), (REGISTERED_CLASSIFICATION, REGISTERED_STATUS)):
        raise EpochFreezeError("task-calibration registration state differs")
    if value["classification"]["timing_execution_authorized"] is not False:
        raise EpochFreezeError("timing execution is not locked")
    if len(value["scenes"]) != EXPECTED_SCENES or len(value["config_artifacts"]) != EXPECTED_SCENES:
        raise EpochFreezeError("task-calibration scene denominator differs")
    grids = value["design"].get("family_grids")
    expected_grids = [
        {"counts": list(COUNTS[family]), "family": family, "n0": COUNTS[family][0]}
        for family in FAMILIES
    ]
    if grids != expected_grids or value["controls"].get("task_only") is not True:
        raise EpochFreezeError("task-calibration design grid differs")
    expected_scene_keys = {
        (family, count, seed)
        for family in FAMILIES
        for count in COUNTS[family]
        for seed in SEEDS
    }
    observed_scene_keys: set[tuple[str, int, int]] = set()
    input_ids: set[str] = set()
    config_by_path: dict[str, dict[str, Any]] = {}
    for artifact in value["config_artifacts"]:
        if (
            type(artifact) is not dict
            or set(artifact) != {"bytes", "content_utf8", "path", "sha256"}
            or type(artifact["content_utf8"]) is not str
            or artifact["bytes"] != len(artifact["content_utf8"].encode("utf-8"))
            or artifact["sha256"] != _sha(artifact["content_utf8"].encode("utf-8"))
            or artifact["path"] in config_by_path
        ):
            raise EpochFreezeError("task-calibration config artifact differs")
        config_by_path[artifact["path"]] = artifact
    for scene in value["scenes"]:
        if type(scene) is not dict:
            raise EpochFreezeError("task-calibration scene is not an object")
        key = (scene.get("family"), scene.get("robot_count"), scene.get("seed"))
        if key not in expected_scene_keys or key in observed_scene_keys:
            raise EpochFreezeError("task-calibration scene key differs")
        observed_scene_keys.add(key)
        family, count, seed = key
        expected_id = f"task-calibration-{family}-n-{count}-seed-{seed:02d}"
        expected_scenario = (
            PLAN_ROOT / "missions" / family / f"seed-{seed:04d}" / f"n-{count}" / "scenario.json"
        ).as_posix()
        expected_config = (
            PLAN_ROOT / "configs" / family / f"n-{count}" / f"seed-{seed:02d}" / "config.yaml"
        ).as_posix()
        if (
            scene.get("scene_id") != expected_id
            or scene.get("pattern") != _assignment(family, count, seed)
            or scene.get("is_n0") is not (count == COUNTS[family][0])
            or scene.get("scenario", {}).get("path") != expected_scenario
            or scene.get("config", {}).get("path") != expected_config
            or scene.get("config") != {
                key_name: config_by_path.get(expected_config, {}).get(key_name)
                for key_name in ("bytes", "path", "sha256")
            }
            or scene.get("input_id") in input_ids
        ):
            raise EpochFreezeError("task-calibration scene binding differs")
        input_ids.add(scene["input_id"])
    if observed_scene_keys != expected_scene_keys or len(input_ids) != EXPECTED_SCENES:
        raise EpochFreezeError("task-calibration scene inventory differs")
    schedule = value["schedule"]
    if schedule["execution_order"] != ["warmups", *RUN_CLASSES] or schedule["timing_gate"] != {"headless_timing_permitted": False, "timing_inventory_present": False}:
        raise EpochFreezeError("task-only execution order differs")
    if set(schedule["inventories"]) != set(RUN_CLASSES) or schedule["warmups"]["process_count"] != EXPECTED_WARMUPS:
        raise EpochFreezeError("task-only inventory differs")
    expected = {"digest_fidelity": EXPECTED_DIGEST, "positions_replay": EXPECTED_POSITIONS}
    destinations: set[str] = set()
    slot_ids: set[str] = set()
    joins: dict[str, set[tuple[str, str, int, int, str]]] = {
        run_class: set() for run_class in RUN_CLASSES
    }
    all_slots = list(schedule["warmups"]["slots"])
    for run_class in RUN_CLASSES:
        inventory = schedule["inventories"][run_class]
        if inventory["measured_process_count"] != expected[run_class] or inventory["timed"] is not False:
            raise EpochFreezeError("task-only measured denominator differs")
        for slot in inventory["measured"]:
            joins[run_class].add(
                (
                    slot["scene_id"], slot["input_id"], slot["repetition"],
                    slot["pair_position"], slot["method"],
                )
            )
        all_slots.extend(inventory["measured"])
    if len(all_slots) != EXPECTED_PROCESSES:
        raise EpochFreezeError("task-only process denominator differs")
    for slot in all_slots:
        if slot["run_class"] not in RUN_CLASSES or slot["timed"] is not False or slot["destination"] in destinations or slot["slot_id"] in slot_ids:
            raise EpochFreezeError("task-only slot identity differs")
        destinations.add(slot["destination"]); slot_ids.add(slot["slot_id"])
    expected_positions = {
        row for row in joins["digest_fidelity"] if row[2] == 1
    }
    if joins["positions_replay"] != expected_positions:
        raise EpochFreezeError("task-only positions join differs")
    if set(value["method_bindings"]) != set(METHODS) or any(set(value["method_bindings"][method]) != set(RUN_CLASSES) for method in METHODS):
        raise EpochFreezeError("task-only method bindings differ")
    if any(
        value["method_bindings"][method][run_class].get("binary_key")
        != f"{method}-fidelity"
        for method in METHODS
        for run_class in RUN_CLASSES
    ):
        raise EpochFreezeError("task-only method binary role differs")
    if set(value["input_records"]) != {
        "activation_receipt", "analyzer", "freezer", "geometry", "mission_compiler",
        "mission_receipt", "parent_plan", "protocol", "runner", "runtime_attestation",
    }:
        raise EpochFreezeError("task-calibration input record inventory differs")
    return value


def validate_compilation(compilation: EpochCompilation) -> None:
    if type(compilation) is not EpochCompilation or compilation.sha256 != _sha(compilation.data):
        raise EpochFreezeError("task-calibration compilation identity differs")
    if validate_epoch_bytes(compilation.data) != compilation.value:
        raise EpochFreezeError("task-calibration compilation value differs")
    paths: set[PurePosixPath] = set()
    for item in compilation.files:
        if item.relative_path in paths or item.sha256 != _sha(item.data):
            raise EpochFreezeError("task-calibration compiled file inventory differs")
        paths.add(item.relative_path)


def main() -> int:
    raise EpochFreezeError("task-calibration plan publication is categorically disabled")


if __name__ == "__main__":
    try:
        main()
    except EpochFreezeError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
