#!/usr/bin/env python3
"""Compile the exact 100-scene paired timing-calibration plan in memory."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import types
from typing import Any, Mapping


sys.dont_write_bytecode = True

SCHEMA = "dancers-headless-timing-calibration-epoch-plan-v1"
TRACK_ID = "simulator-demo-v1-headless-epoch-1"
EPOCH_ID = "simulator-demo-v1-headless-epoch-4-timing-calibration"
RUN_CLASSES = ("headless_timing",)
FAMILIES = ("open", "perfect_maze", "fractal_low", "fractal_medium", "fractal_high")
COUNTS = {"open": (8,), "perfect_maze": (2, 4, 6), "fractal_low": (2, 4), "fractal_medium": (2, 4), "fractal_high": (2, 4)}
PLAN_ROOT = PurePosixPath("comparison/plans/simulator-demo/v1/headless-epoch-4-timing-calibration")
RESULT_ROOT = PurePosixPath("results/simulator-demo/v1/headless-scientific/epoch-4-timing-calibration")
BASE_FREEZER = {"bytes": 29960, "path": "scripts/comparison/freeze_headless_task_calibration_epoch_v1.py", "sha256": "a05d9907ab0422e096fa9e0bba92642c0a18af303feccc85c8880c21789c14c5"}
PARENT_RUNTIME = {"bytes": 5768480, "path": "comparison/plans/simulator-demo/v1/headless-epoch-1/plan.json", "sha256": "dbf1f06f1fcb3247181e630b5cdd030967ce749fd7b96d9655142a6826376e95"}
PARENT_E2 = {"bytes": 2795335, "path": "comparison/plans/simulator-demo/v1/headless-epoch-2-task-calibration/plan.json", "sha256": "f8579bbb29c2b6abece90ad86da6478edfee11dc36f7494881d1479ecd8b16a0"}
PARENT_E3 = {"bytes": 349797, "path": "comparison/plans/simulator-demo/v1/headless-epoch-3-fractal-low-task-calibration/plan.json", "sha256": "2eb98865b61d185d7df92b79445e978b4ccab574113437cc7f1bc3bdbd6f0636"}
EVIDENCE = (
    {"bytes": 1061202, "path": "results/simulator-demo/v1/headless-scientific/epoch-2-task-calibration/task-calibration-session-001-analysis.json", "sha256": "78a51b5bd49acda58d6d7c2e12e5d9ffad7636faec06480bc5b3aa2f353180ce"},
    {"bytes": 118761, "path": "results/simulator-demo/v1/headless-scientific/epoch-3-fractal-low-task-calibration/fractal-low-task-calibration-session-001-analysis.json", "sha256": "308e8b2a2aa7373f62343469daf31fc0b083a6e4ceea20dd4b3e3b90806ecf9b"},
)


class TimingFreezeError(ValueError):
    pass


@dataclass(frozen=True)
class PlanFile:
    relative_path: PurePosixPath
    data: bytes
    sha256: str
    kind: str


@dataclass(frozen=True)
class Compilation:
    value: dict[str, Any]
    data: bytes
    sha256: str
    files: tuple[PlanFile, ...]


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def _read(root: Path, identity: Mapping[str, Any]) -> bytes:
    raw = root.joinpath(*PurePosixPath(identity["path"]).parts).read_bytes()
    if len(raw) != identity["bytes"] or _sha(raw) != identity["sha256"]:
        raise TimingFreezeError(f"input identity differs: {identity['path']}")
    return raw


def _json(root: Path, identity: Mapping[str, Any]) -> dict[str, Any]:
    raw = _read(root, identity)
    value = json.loads(raw)
    if canonical_json_bytes(value) != raw:
        raise TimingFreezeError("input JSON is not canonical")
    return value


def _helpers(root: Path) -> tuple[types.ModuleType, types.ModuleType]:
    raw = _read(root, BASE_FREEZER)
    task = types.ModuleType("_dancers_timing_task_freezer")
    task.__file__ = BASE_FREEZER["path"]
    sys.modules[task.__name__] = task
    exec(compile(raw, task.__file__, "exec", dont_inherit=True, optimize=0), task.__dict__)
    base = task._load_exact(root, task.BASE_FREEZER, "_dancers_timing_base_freezer")
    base.FUTURE_PLAN_ROOT = PLAN_ROOT.as_posix()
    base.EPOCH_ID = EPOCH_ID
    base.TRACK_ID = TRACK_ID
    base.RUN_CLASSES = RUN_CLASSES
    base.REPETITIONS_BY_RUN_CLASS = {"headless_timing": 3}
    return task, base


def compile_epoch(repository_root: Path) -> Compilation:
    root = Path(os.path.abspath(repository_root))
    runtime = _json(root, PARENT_RUNTIME)
    epoch2 = _json(root, PARENT_E2)
    epoch3 = _json(root, PARENT_E3)
    evidence = [json.loads(_read(root, item)) for item in EVIDENCE]
    task, base = _helpers(root)
    valid2 = {row["scene_id"]: row["task_valid"] for row in evidence[0]["analysis"]["task_validity"]["scene_rows"]}
    valid3 = {row["scene_id"]: row["task_valid"] for row in evidence[1]["analysis"]["task_validity"]["scene_rows"]}
    source_scenes = {row["scene_id"]: row for row in epoch2["scenes"]}
    source_scenes.update({row["scene_id"]: row for row in epoch3["scenes"]})
    scenes = []
    for family in FAMILIES:
        for count in COUNTS[family]:
            for seed in range(1, 11):
                scene_id = f"task-calibration-{family}-n-{count}-seed-{seed:02d}"
                valid = valid3 if family == "fractal_low" else valid2
                if valid.get(scene_id) is not True:
                    raise TimingFreezeError(f"selected scene is not task-valid: {scene_id}")
                scenes.append(dict(source_scenes[scene_id]))
    if len(scenes) != 100 or len({row["input_id"] for row in scenes}) != 100:
        raise TimingFreezeError("timing scene inventory differs")
    method_bindings = {
        method: {"headless_timing": dict(runtime["method_bindings"][method]["headless_timing"])}
        for method in ("reference", "optimized")
    }
    binary_by_key = {
        binding["binary_key"]: task._binary_from_binding(binding)
        for methods in method_bindings.values() for binding in methods.values()
    }
    scene_index = {(row["family"], row["robot_count"], row["seed"]): row for row in scenes}
    warmups = []
    for family in FAMILIES:
        scene = scene_index[(family, COUNTS[family][0], 1)]
        for method in ("reference", "optimized"):
            key = f"{method}-timing"
            warmups.append(base._slot(
                order_index=len(warmups), kind="warmup", run_class="headless_timing",
                family=family, count=scene["robot_count"], seed=1, repetition=0,
                pattern="warmup", pair_position=0, method=method, scene=scene,
                binary_key=key, binary=binary_by_key[key], timeout=300,
            ))
    measured = []
    for family in FAMILIES:
        for count in COUNTS[family]:
            for seed in range(1, 11):
                scene = scene_index[(family, count, seed)]
                pattern = scene["pattern"]
                for repetition, order in enumerate(base.PATTERNS[pattern][:3], 1):
                    for position, method in enumerate(order):
                        key = f"{method}-timing"
                        measured.append(base._slot(
                            order_index=len(measured), kind="measured", run_class="headless_timing",
                            family=family, count=count, seed=seed, repetition=repetition,
                            pattern=pattern, pair_position=position, method=method, scene=scene,
                            binary_key=key, binary=binary_by_key[key], timeout=300,
                        ))
    value = {
        "classification": {"execution_authorized": False, "plan_registered": False, "scientific_evidence": False, "timing_execution_authorized": False, "validation_execution_authorized": False},
        "controls": {**runtime["controls"], "task_only": False, "timing_execution_authorized": False},
        "epoch_id": EPOCH_ID,
        "failure_policy": runtime["failure_policy"],
        "input_records": {"runtime_attestation": runtime["input_records"]["runtime_attestation"]},
        "inputs": {"parent_epoch_2": PARENT_E2, "parent_epoch_3": PARENT_E3, "runtime_parent": PARENT_RUNTIME, "task_evidence": list(EVIDENCE)},
        "method_bindings": method_bindings,
        "normalized_config": runtime["normalized_config"],
        "operations": {"plan_written": False, "result_written": False, "simulator_invoked": False},
        "output_grammar": {**runtime["output_grammar"], "registered_result_namespace": RESULT_ROOT.as_posix()},
        "phase": "calibration",
        "scenes": scenes,
        "schedule": {
            "execution_order": ["warmups", "headless_timing"],
            "inventories": {"headless_timing": {"measured": measured, "measured_process_count": 600, "paired_scene_repetition_count": 300, "run_class": "headless_timing", "timed": True}},
            "prior_task_evidence_gate": {"authenticated": True, "scene_count": 100},
            "warmups": {"process_count": 10, "slots": warmups, "uniqueness_key": ["binary_key", "map_sha256"]},
        },
        "schema_version": SCHEMA,
        "status": {"execution_ready": False, "registration_state": "compiled-candidate-only-not-registered"},
        "track_id": TRACK_ID,
    }
    data = canonical_json_bytes(value)
    result = Compilation(value, data, _sha(data), (PlanFile(PurePosixPath("plan.json"), data, _sha(data), "plan"),))
    validate_compilation(result)
    return result


def validate_epoch_bytes(raw: bytes) -> dict[str, Any]:
    value = json.loads(raw)
    if canonical_json_bytes(value) != raw or value.get("schema_version") != SCHEMA or value.get("epoch_id") != EPOCH_ID:
        raise TimingFreezeError("timing plan identity differs")
    if len(value["scenes"]) != 100 or value["schedule"]["warmups"]["process_count"] != 10 or value["schedule"]["inventories"]["headless_timing"]["measured_process_count"] != 600:
        raise TimingFreezeError("timing plan denominator differs")
    if value["classification"]["timing_execution_authorized"] is not False:
        raise TimingFreezeError("timing plan grants execution authority")
    return value


def validate_compilation(compilation: Compilation) -> None:
    if compilation.data != canonical_json_bytes(compilation.value) or compilation.sha256 != _sha(compilation.data):
        raise TimingFreezeError("timing compilation bytes differ")
    validate_epoch_bytes(compilation.data)


def main() -> int:
    raise TimingFreezeError("timing plan publication is categorically disabled")


if __name__ == "__main__":
    raise SystemExit(main())
