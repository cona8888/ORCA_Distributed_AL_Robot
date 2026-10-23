#!/usr/bin/env python3
"""Compile the complete prospective epoch-5 held-out validation plan in memory."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import types
from typing import Any, Mapping


sys.dont_write_bytecode = True

EPOCH_SCHEMA = "dancers-headless-validation-epoch-plan-v1"
TRACK_ID = "simulator-demo-v1-headless-epoch-1"
EPOCH_ID = "simulator-demo-v1-headless-epoch-5-validation"
PHASE = "validation"
RUN_CLASSES = ("digest_fidelity", "positions_replay", "headless_timing")
TASK_CLASSES = RUN_CLASSES[:2]
FAMILIES = ("open", "perfect_maze", "fractal_low", "fractal_medium", "fractal_high")
COUNTS = {"open": (8,), "perfect_maze": (2, 4, 6), "fractal_low": (2, 4), "fractal_medium": (2, 4), "fractal_high": (2, 4)}
SEEDS = tuple(range(11, 31))
REPETITIONS = {"digest_fidelity": 3, "positions_replay": 1, "headless_timing": 3}
PLAN_ROOT = PurePosixPath("comparison/plans/simulator-demo/v1/headless-epoch-5-validation")
RESULT_ROOT = PurePosixPath("results/simulator-demo/v1/headless-scientific/epoch-5-validation")
SOURCE_PATH = PurePosixPath("scripts/comparison/freeze_headless_validation_epoch_v1.py")
RUNNER_PATH = PurePosixPath("scripts/comparison/run_headless_validation_epoch_v1.py")
ANALYZER_PATH = PurePosixPath("scripts/comparison/analyze_headless_validation_epoch_v1.py")
MISSION_PATH = PurePosixPath("scripts/comparison/generate_headless_validation_missions_v1.py")
BASE = {"bytes": 29960, "path": "scripts/comparison/freeze_headless_task_calibration_epoch_v1.py", "sha256": "a05d9907ab0422e096fa9e0bba92642c0a18af303feccc85c8880c21789c14c5"}
HEADLESS_BASE = {"bytes": 110059, "path": "scripts/comparison/freeze_headless_epoch_v1.py", "sha256": "12398b9b372d4ca4e870a307b640eb2c959130181a4ae090c106cd0847a86391"}
MISSION = {"bytes": 7754, "path": MISSION_PATH.as_posix(), "sha256": "d36c070aa91e19f73752f9797ef2407b45170c1b7bf284af81e0d66d13de1aa8"}
RUNNER = {"bytes": 8605, "path": RUNNER_PATH.as_posix(), "sha256": "86752cd303a7175b5af4bd47288ef4251e790b6d4a58538bf83f90a25d88ee36"}
ANALYZER = {"bytes": 1927, "path": ANALYZER_PATH.as_posix(), "sha256": "a175309ca3fa873bef478d963f9278031182b3868f0b8c21b4621d3a6c186d7e"}
DESIGN = {"bytes": 2201, "path": "docs/simulator/headless_validation_epoch_5_design.json", "sha256": "e710fdfb5fde916bf73db6ce604ba7859ae486cbef9b2b6aa36e4f3863db1be3"}
PROTOCOL = {"bytes": 1481, "path": "docs/simulator/HEADLESS_VALIDATION_EPOCH_5_ADDENDUM.md", "sha256": "3ce1df5d8b731ec9d65974f16c895179af336547caf23d4962d9919dad945e6c"}
RNG_AMENDMENT = {"bytes": 384, "path": "docs/simulator/headless_validation_epoch_5_runtime_rng_amendment_v1.json", "sha256": "71a0a62fa551140c14c85f30969410873e71a4776d01c98e7af4c5fbaca624dc"}
PARENT_PLAN = {"bytes": 5768480, "path": "comparison/plans/simulator-demo/v1/headless-epoch-1/plan.json", "sha256": "dbf1f06f1fcb3247181e630b5cdd030967ce749fd7b96d9655142a6826376e95"}
ORDER_SEED = "e5" * 32

CANDIDATE_CLASSIFICATION = {
    "benchmark_claim_ready": False, "benchmark_execution_authorized": False,
    "calibration_execution_authorized": False, "capacity_claim_ready": False,
    "execution_authorized": False, "fidelity_claim_ready": False,
    "plan_registered": False, "scientific_evidence": False,
    "scientific_evidence_eligible": False, "timing_claim_ready": False,
    "timing_execution_authorized": False, "validation_claim_ready": False,
    "validation_execution_authorized": False, "validation_results_present": False,
}
REGISTERED_CLASSIFICATION = {
    **CANDIDATE_CLASSIFICATION,
    "plan_registered": True,
}
ACTIVATED_CLASSIFICATION = {
    **REGISTERED_CLASSIFICATION,
    "execution_authorized": True,
    "scientific_evidence_eligible": True,
    "validation_execution_authorized": True,
}
CANDIDATE_STATUS = {"execution_ready": False, "registration_state": "compiled-candidate-only-not-registered"}
REGISTERED_STATUS = {"execution_ready": False, "registration_state": "registered-committed"}
ACTIVATED_STATUS = {"execution_ready": True, "registration_state": "registered-committed"}


class ValidationFreezeError(ValueError):
    """The prospective validation plan or an authenticated input differs."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def _read(root: Path, identity: Mapping[str, Any], label: str) -> bytes:
    raw = root.joinpath(*PurePosixPath(identity["path"]).parts).read_bytes()
    if len(raw) != identity["bytes"] or _sha(raw) != identity["sha256"]:
        raise ValidationFreezeError(f"{label} identity differs")
    return raw


def _load(raw: bytes, path: str, name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__file__ = path
    sys.modules[name] = module
    exec(compile(raw, path, "exec", dont_inherit=True, optimize=0), module.__dict__)
    return module


def _core(root: Path) -> types.ModuleType:
    raw = _read(root, BASE, "task freezer")
    old_scene = b"scene = scene_index[(family, COUNTS[family][0], 1)]"
    new_scene = b"scene = scene_index[(family, COUNTS[family][0], SEEDS[0])]"
    old_seed = b"family=family, count=COUNTS[family][0], seed=1, repetition=0,"
    new_seed = b"family=family, count=COUNTS[family][0], seed=SEEDS[0], repetition=0,"
    if raw.count(old_scene) != 1 or raw.count(old_seed) != 1:
        raise ValidationFreezeError("task freezer transformation anchor differs")
    core = _load(raw.replace(old_scene, new_scene).replace(old_seed, new_seed), BASE["path"], "_dancers_validation_freezer_core")
    core.EPOCH_SCHEMA = EPOCH_SCHEMA
    core.EPOCH_ID = EPOCH_ID
    core.PHASE = PHASE
    core.RUN_CLASSES = TASK_CLASSES
    core.FAMILIES = FAMILIES
    core.COUNTS = dict(COUNTS)
    core.SEEDS = SEEDS
    core.REPETITIONS = {key: REPETITIONS[key] for key in TASK_CLASSES}
    core.PLAN_ROOT = PLAN_ROOT
    core.RESULT_ROOT = RESULT_ROOT
    core.SOURCE_PATH = SOURCE_PATH
    core.RUNNER_PATH = RUNNER_PATH
    core.ANALYZER_PATH = ANALYZER_PATH
    core.MISSION_COMPILER_PATH = MISSION_PATH
    core.MISSION_COMPILER = dict(MISSION)
    core.EXPECTED_SCENES = 200
    core.EXPECTED_WARMUPS = 10
    core.EXPECTED_DIGEST = 1200
    core.EXPECTED_POSITIONS = 400
    core.EXPECTED_PROCESSES = 1610
    core.ORDER_SEED = ORDER_SEED
    core.CANDIDATE_CLASSIFICATION = dict(CANDIDATE_CLASSIFICATION)
    core.REGISTERED_CLASSIFICATION = dict(REGISTERED_CLASSIFICATION)
    core.CANDIDATE_STATUS = dict(CANDIDATE_STATUS)
    core.REGISTERED_STATUS = dict(REGISTERED_STATUS)
    original = core._load_exact

    def load_exact(passed_root: Path, identity: Mapping[str, Any], name: str) -> types.ModuleType:
        loaded = original(passed_root, identity, name)
        if dict(identity) == MISSION:
            loaded.compile_task_missions = loaded.compile_validation_missions
        return loaded

    core._load_exact = load_exact
    return core


def compile_epoch(repository_root: Path) -> Any:
    root = Path(os.path.abspath(repository_root))
    if Path(os.path.realpath(root)) != root:
        raise ValidationFreezeError("repository root is not canonical")
    design_raw = _read(root, DESIGN, "validation design")
    design = json.loads(design_raw)
    _read(root, PROTOCOL, "validation protocol")
    rng_amendment = json.loads(_read(root, RNG_AMENDMENT, "runtime RNG amendment"))
    _read(root, RUNNER, "validation runner")
    _read(root, ANALYZER, "validation analyzer")
    core = _core(root)
    headless = _load(_read(root, HEADLESS_BASE, "headless freezer"), HEADLESS_BASE["path"], "_dancers_validation_headless_freezer")
    task_compilation = core.compile_epoch(root)
    value = json.loads(json.dumps(task_compilation.value))
    replacement_configs: dict[PurePosixPath, Any] = {}
    config_artifacts = {item["path"]: item for item in value["config_artifacts"]}
    for scene in value["scenes"]:
        mission_seed = scene["seed"]
        runtime_seed = 1 + ((mission_seed - 11) % 10)
        config_bytes = headless._render_config_bytes(
            scene_id=scene["scene_id"], family=scene["family"],
            count=scene["robot_count"], seed=runtime_seed,
            map_path=scene["map"]["path"], scenario_path=scene["scenario"]["path"],
        )
        config = {"bytes": len(config_bytes), "path": scene["config"]["path"], "sha256": _sha(config_bytes)}
        artifact = config_artifacts[config["path"]]
        artifact.clear(); artifact.update({**config, "content_utf8": config_bytes.decode("utf-8")})
        scene["config"] = config
        scene["config_binding"]["rng_seed"] = runtime_seed
        normalized_sha = core._semantic("dancers-headless-validation-normalized-scene-config-v1", scene["config_binding"])
        scene["normalized_config_sha256"] = normalized_sha
        scene["seed_roles"]["runtime_rng"]["runtime_rng_seed"] = runtime_seed
        input_payload = {
            "controller_timesteps": 100,
            "effective_runtime_raster_sha256": scene["effective_runtime_raster"]["sha256"],
            "effective_scenario_sha256": scene["effective_scenario_sha256"],
            "family": scene["family"], "map_sha256": scene["map"]["sha256"],
            "mission_bank_id": scene["sampling_unit"]["mission_bank_id"],
            "mission_generator_seed_uint64": scene["seed_roles"]["mission_selection"]["generator_seed_uint64"],
            "mission_selection_seed": mission_seed, "normalized_config_sha256": normalized_sha,
            "phase": PHASE, "rendered_config_sha256": config["sha256"],
            "robot_count": scene["robot_count"], "runtime_rng_seed": runtime_seed,
            "scenario_sha256": scene["scenario"]["sha256"], "timestep_seconds": 0.1,
        }
        scene["input_id"] = core._semantic("dancers-headless-validation-exact-input-v1", input_payload)
        relative = PurePosixPath(config["path"]).relative_to(PLAN_ROOT)
        replacement_configs[relative] = core.PlanFile(relative, config_bytes, config["sha256"], "config")
    scene_by_id = {scene["scene_id"]: scene for scene in value["scenes"]}
    for run_class in TASK_CLASSES:
        for slot in value["schedule"]["inventories"][run_class]["measured"]:
            scene = scene_by_id[slot["scene_id"]]
            slot["config"] = dict(scene["config"]); slot["input_id"] = scene["input_id"]
            slot["normalized_config_sha256"] = scene["normalized_config_sha256"]
    for slot in value["schedule"]["warmups"]["slots"]:
        scene = scene_by_id[slot["scene_id"]]
        slot["config"] = dict(scene["config"]); slot["input_id"] = scene["input_id"]
        slot["normalized_config_sha256"] = scene["normalized_config_sha256"]
    runtime = json.loads(_read(root, PARENT_PLAN, "runtime parent"))
    headless.FUTURE_PLAN_ROOT = PLAN_ROOT.as_posix()
    headless.EPOCH_ID = EPOCH_ID
    headless.TRACK_ID = TRACK_ID
    headless.RUN_CLASSES = RUN_CLASSES
    headless.REPETITIONS_BY_RUN_CLASS = dict(REPETITIONS)

    value["design"]["prospective_validation_design"] = design
    value["design"]["runtime_rng_amendment"] = rng_amendment
    value["design"]["schema_version"] = "dancers-headless-validation-epoch-design-v1"
    value["design"]["seed_roles"] = {
        "mission_selection": "fresh-validation-domain-seeds-11-through-30",
        "runtime_rng": "same-numeric-seed-no-activity-assumption",
    }
    value["controls"]["task_only"] = False
    value["controls"]["timing_execution_authorized"] = False
    value["controls"]["validation_execution_authorized"] = False
    for method in ("reference", "optimized"):
        value["method_bindings"][method]["headless_timing"] = dict(runtime["method_bindings"][method]["headless_timing"])
    binary_by_key = {
        binding["binary_key"]: core._binary_from_binding(binding)
        for bindings in value["method_bindings"].values()
        for binding in bindings.values()
    }
    scene_index = {(scene["family"], scene["robot_count"], scene["seed"]): scene for scene in value["scenes"]}
    measured: list[dict[str, Any]] = []
    for family in FAMILIES:
        for count in COUNTS[family]:
            for seed in sorted(SEEDS, key=lambda item: (core._block_score(family, count, item), item)):
                scene = scene_index[(family, count, seed)]
                for repetition, order in enumerate(headless.PATTERNS[scene["pattern"]][:3], 1):
                    for position, method in enumerate(order):
                        key = f"{method}-timing"
                        measured.append(headless._slot(
                            order_index=len(measured), kind="measured", run_class="headless_timing",
                            family=family, count=count, seed=seed, repetition=repetition,
                            pattern=scene["pattern"], pair_position=position, method=method,
                            scene=scene, binary_key=key, binary=binary_by_key[key], timeout=300,
                        ))
    value["schedule"]["assignment_algorithm"] = "sha256-ranked-ten-ten-aba-bab-validation-v1"
    value["schedule"]["balance"] = [
        {"aba_scene_count": 10, "bab_scene_count": 10, "family": family, "robot_count": count}
        for family in FAMILIES for count in COUNTS[family]
    ]
    value["schedule"]["execution_order"] = ["warmups", *RUN_CLASSES]
    value["schedule"]["inventories"]["headless_timing"] = {
        "measured": measured,
        "measured_process_count": 1200,
        "paired_scene_repetition_count": 600,
        "run_class": "headless_timing",
        "timed": True,
    }
    value["schedule"]["timing_gate"] = {
        "all_prior_digest_and_positions_required": True,
        "on_prior_failure": "do-not-start-timing",
        "timing_inventory_present": True,
    }
    value["schema_version"] = EPOCH_SCHEMA

    protocol_record = core._record(
        schema="dancers-headless-validation-protocol-record-v1",
        identity=PROTOCOL,
        contract={"design": DESIGN, "execution_authorized": False, "held_out": True, "runtime_rng_amendment": RNG_AMENDMENT},
    )
    protocol_wrapper, protocol_file = core._wrapper("protocol", protocol_record)
    value["input_records"]["protocol"] = protocol_wrapper
    runner_record = core._record(
        schema="dancers-headless-task-runner-record-v1",
        identity=RUNNER,
        contract={"execution_authorized": False, "run_classes": list(RUN_CLASSES), "supported_plan_schema": EPOCH_SCHEMA},
    )
    runner_wrapper, runner_file = core._wrapper("runner", runner_record)
    value["input_records"]["runner"] = runner_wrapper
    analyzer_record = {
        "classification": {
            "benchmark_claim_ready": False,
            "result_write_permitted": False,
            "scientific_evidence": False,
            "validation_claim_ready": False,
        },
        "contract": {
            "bootstrap_replicates": 100000,
            "cluster": "family-by-mission-bank",
            "result_write_permitted": False,
            "run_classes": list(RUN_CLASSES),
            "supported_plan_schema": EPOCH_SCHEMA,
        },
        "identity": dict(ANALYZER),
        "schema_version": "dancers-headless-validation-analyzer-record-v1",
    }
    analyzer_wrapper, analyzer_file = core._wrapper("analyzer", analyzer_record)
    value["input_records"]["analyzer"] = analyzer_wrapper
    data = canonical_json_bytes(value)
    replaced_records = {PurePosixPath("records/analyzer.json"), PurePosixPath("records/protocol.json"), PurePosixPath("records/runner.json")}
    files = [replacement_configs.get(item.relative_path, item) for item in task_compilation.files if item.relative_path != PurePosixPath("plan.json") and item.relative_path not in replaced_records]
    files.extend((analyzer_file, protocol_file, runner_file))
    files.append(core.PlanFile(PurePosixPath("plan.json"), data, _sha(data), "plan"))
    result = core.EpochCompilation(value, data, _sha(data), tuple(files))
    validate_compilation(result)
    return result


def validate_epoch_bytes(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeError, ValueError) as error:
        raise ValidationFreezeError(f"validation plan is not JSON: {error}") from error
    if canonical_json_bytes(value) != raw or value.get("schema_version") != EPOCH_SCHEMA or value.get("epoch_id") != EPOCH_ID or value.get("phase") != PHASE:
        raise ValidationFreezeError("validation plan identity differs")
    if (value.get("classification"), value.get("status")) not in (
        (CANDIDATE_CLASSIFICATION, CANDIDATE_STATUS),
        (REGISTERED_CLASSIFICATION, REGISTERED_STATUS),
        (ACTIVATED_CLASSIFICATION, ACTIVATED_STATUS),
    ):
        raise ValidationFreezeError("validation registration state differs")
    if len(value.get("scenes", ())) != 200 or len(value.get("config_artifacts", ())) != 200:
        raise ValidationFreezeError("validation scene denominator differs")
    if value["design"].get("prospective_validation_design") is None:
        raise ValidationFreezeError("prospective validation design is absent")
    schedule = value.get("schedule", {})
    expected = {"digest_fidelity": 1200, "positions_replay": 400, "headless_timing": 1200}
    if schedule.get("execution_order") != ["warmups", *RUN_CLASSES] or schedule.get("warmups", {}).get("process_count") != 10:
        raise ValidationFreezeError("validation schedule differs")
    if set(schedule.get("inventories", {})) != set(RUN_CLASSES):
        raise ValidationFreezeError("validation inventory set differs")
    slots = list(schedule["warmups"]["slots"])
    for run_class in RUN_CLASSES:
        inventory = schedule["inventories"][run_class]
        if inventory.get("measured_process_count") != expected[run_class] or len(inventory.get("measured", ())) != expected[run_class]:
            raise ValidationFreezeError("validation process denominator differs")
        if inventory.get("timed") is not (run_class == "headless_timing"):
            raise ValidationFreezeError("validation timing classification differs")
        slots.extend(inventory["measured"])
    if len(slots) != 2810 or len({slot["slot_id"] for slot in slots}) != 2810 or len({slot["destination"] for slot in slots}) != 2810:
        raise ValidationFreezeError("validation slot inventory differs")
    if value["schedule"].get("timing_gate") != {
        "all_prior_digest_and_positions_required": True,
        "on_prior_failure": "do-not-start-timing",
        "timing_inventory_present": True,
    }:
        raise ValidationFreezeError("validation timing gate differs")
    return value


def validate_compilation(compilation: Any) -> None:
    if compilation.sha256 != _sha(compilation.data) or validate_epoch_bytes(compilation.data) != compilation.value:
        raise ValidationFreezeError("validation compilation identity differs")
    paths = set()
    for item in compilation.files:
        if item.relative_path in paths or item.sha256 != _sha(item.data):
            raise ValidationFreezeError("validation compiled file inventory differs")
        paths.add(item.relative_path)
    if len(paths) != 513:
        raise ValidationFreezeError("validation compiled file count differs")


def main() -> int:
    raise ValidationFreezeError("validation plan publication is categorically disabled")


if __name__ == "__main__":
    try:
        main()
    except ValidationFreezeError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
