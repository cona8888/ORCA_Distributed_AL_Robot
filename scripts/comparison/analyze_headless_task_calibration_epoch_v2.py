#!/usr/bin/env python3
"""Analyze the complete epoch-2 task-only session using the audited v1 core.

This successor is frozen before execution.  It authenticates the registered
task plan, reuses the descriptor-custodied session reader and independent
geometry implementation, and emits no timing, validation, or benchmark claim.
Its public CLI remains disabled until a separately committed result-analysis
authorization names the completed immutable session.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import sys
import types
from typing import Any, Mapping, Sequence


sys.dont_write_bytecode = True

SCHEMA = "dancers-headless-task-calibration-analysis-v1"
PLAN_SCHEMA = "dancers-headless-task-calibration-epoch-plan-v1"
TRACK_ID = "simulator-demo-v1-headless-epoch-1"
EPOCH_ID = "simulator-demo-v1-headless-epoch-2-task-calibration"
RUN_CLASSES = ("digest_fidelity", "positions_replay")
PLAN_ROOT = PurePosixPath(
    "comparison/plans/simulator-demo/v1/headless-epoch-2-task-calibration"
)
RESULT_ROOT = PurePosixPath(
    "results/simulator-demo/v1/headless-scientific/epoch-2-task-calibration"
)
PLAN_IDENTITY = {
    "bytes": 2795335,
    "path": (PLAN_ROOT / "plan.json").as_posix(),
    "sha256": "f8579bbb29c2b6abece90ad86da6478edfee11dc36f7494881d1479ecd8b16a0",
}
BASE_ANALYZER_PATH = PurePosixPath("scripts/comparison/analyze_headless_epoch_v1.py")
BASE_ANALYZER = {
    "bytes": 219654,
    "path": BASE_ANALYZER_PATH.as_posix(),
    "sha256": "d25ca18aee5167a2c796606774acc5406bcdfdda83a85e914e9526fd179f64c4",
}
TASK_RUNNER_PATH = PurePosixPath(
    "scripts/comparison/run_headless_task_calibration_epoch_v1.py"
)
TASK_RUNNER = {
    "bytes": 13257,
    "path": TASK_RUNNER_PATH.as_posix(),
    "sha256": "6fa4c6bd5a75662f9c07e49a8043e68b5242f063a923434eb001518052a76cff",
}
GEOMETRY_PATH = PurePosixPath("scripts/comparison/headless_geometry_v1.py")
EXPECTED_SCENES = 180
EXPECTED_DIGEST_PAIRS = 540
EXPECTED_POSITIONS_PAIRS = 180
EXPECTED_PROCESSES = 1450


class TaskAnalysisError(ValueError):
    """The task-only evidence or analysis contract differs."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


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
        raise TaskAnalysisError(f"value is not canonical JSON: {error}") from error


def _read(root: Path, identity: Mapping[str, Any], label: str) -> bytes:
    try:
        raw = root.joinpath(*PurePosixPath(identity["path"]).parts).read_bytes()
    except (KeyError, OSError, TypeError) as error:
        raise TaskAnalysisError(f"cannot read {label}: {error}") from error
    if len(raw) != identity.get("bytes") or _sha(raw) != identity.get("sha256"):
        raise TaskAnalysisError(f"{label} identity differs")
    return raw


def _load_module(
    root: Path, identity: Mapping[str, Any], name: str
) -> types.ModuleType:
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


def _patched_core(root: Path) -> tuple[types.ModuleType, types.ModuleType]:
    core = _load_module(root, BASE_ANALYZER, "_dancers_task_analysis_core")
    runner = _load_module(root, TASK_RUNNER, "_dancers_task_analysis_runner")
    core.PLAN_SCHEMA = PLAN_SCHEMA
    core.TRACK_ID = TRACK_ID
    core.PHASE = "calibration"
    core.PLAN_ROOT = PLAN_ROOT
    core.RESULT_ROOT = RESULT_ROOT
    core.RUN_CLASSES = RUN_CLASSES
    core.Path = type(Path())
    core.flatten_plan_slots = lambda plan: [
        dict(slot) for slot, _index in runner._flatten_schedule(plan)
    ]
    return core, runner


def load_authenticated_plan(repository_root: Path) -> dict[str, Any]:
    root = Path(os.path.abspath(repository_root))
    if Path(os.path.realpath(root)) != root:
        raise TaskAnalysisError("repository root is not canonical")
    plan_raw = _read(root, PLAN_IDENTITY, "registered task-only plan")
    core, runner = _patched_core(root)
    authenticated = runner.load_authenticated_plan(
        root.joinpath(*PurePosixPath(PLAN_IDENTITY["path"]).parts),
        root,
        production=False,
    )
    if authenticated.plan_bytes != plan_raw or len(authenticated.schedule) != EXPECTED_PROCESSES:
        raise TaskAnalysisError("runner-authenticated task-only plan differs")
    try:
        geometry_identity = authenticated.plan["input_records"]["geometry"]["content"]["identity"]
    except (KeyError, TypeError) as error:
        raise TaskAnalysisError("task-only geometry identity is absent") from error
    geometry_raw = _read(root, geometry_identity, "task-only geometry source")
    geometry = core.load_bound_geometry_source(geometry_raw, identity=geometry_identity)
    return {
        "core": core,
        "file_authentication": {"all_declared_files_authenticated": True},
        "geometry_namespace": geometry,
        "plan": authenticated.plan,
        "plan_identity": dict(PLAN_IDENTITY),
        "repository_root": root,
        "runner_plan": authenticated,
    }


def _recommend(plan: Mapping[str, Any], task: Mapping[str, Any]) -> list[dict[str, Any]]:
    validity = {row["scene_id"]: row["task_valid"] for row in task["scene_rows"]}
    rows: list[dict[str, Any]] = []
    grids = {row["family"]: row["counts"] for row in plan["design"]["family_grids"]}
    for family in ("open", "perfect_maze", "fractal_low", "fractal_medium", "fractal_high"):
        prefix: list[int] = []
        per_count: list[dict[str, Any]] = []
        prefix_open = True
        for count in grids[family]:
            passed = sum(
                validity[f"task-calibration-{family}-n-{count}-seed-{seed:02d}"]
                for seed in range(1, 11)
            )
            all_ten = passed == 10
            if prefix_open and all_ten:
                prefix.append(count)
            else:
                prefix_open = False
            per_count.append(
                {
                    "all_ten_banks_valid": all_ten,
                    "robot_count": count,
                    "task_scene_count": 10,
                    "task_valid_scene_count": passed,
                }
            )
        rows.append(
            {
                "candidate_task_valid_prefix": prefix,
                "family": family,
                "family_baseline": prefix[0] if prefix else None,
                "per_count": per_count,
            }
        )
    return rows


def analyze_task_session(repository_root: Path, session_path: Path) -> dict[str, Any]:
    authenticated = load_authenticated_plan(repository_root)
    core = authenticated["core"]
    try:
        session = core.audit_session(authenticated, session_path)
        fidelity = core.analyze_fidelity_and_task(authenticated, session)
    except Exception as error:
        raise TaskAnalysisError(f"task-only evidence analysis failed: {error}") from error
    report = {
        "analysis": {
            "fidelity": fidelity["fidelity_report"],
            "inventory": session["report"],
            "positions_replay": fidelity["positions_report"],
            "recommendations": _recommend(authenticated["plan"], fidelity["task_report"]),
            "task_validity": fidelity["task_report"],
        },
        "classification": {
            "benchmark_claim_ready": False,
            "capacity_claim_ready": False,
            "scientific_evidence": False,
            "task_calibration_evidence": True,
            "timing_claim_ready": False,
            "timing_execution_authorized": False,
            "validation_claim_ready": False,
            "validation_execution_authorized": False,
        },
        "epoch_id": EPOCH_ID,
        "plan": dict(PLAN_IDENTITY),
        "schema_version": SCHEMA,
        "status": {
            "analysis_completed": True,
            "execution_unlock": False,
            "timing_unlock": False,
            "validation_unlock": False,
        },
    }
    validate_report(report)
    return report


def validate_report(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {
        "analysis", "classification", "epoch_id", "plan", "schema_version", "status",
    }:
        raise TaskAnalysisError("task-only report schema differs")
    if value["schema_version"] != SCHEMA or value["epoch_id"] != EPOCH_ID or value["plan"] != PLAN_IDENTITY:
        raise TaskAnalysisError("task-only report identity differs")
    if value["classification"] != {
        "benchmark_claim_ready": False,
        "capacity_claim_ready": False,
        "scientific_evidence": False,
        "task_calibration_evidence": True,
        "timing_claim_ready": False,
        "timing_execution_authorized": False,
        "validation_claim_ready": False,
        "validation_execution_authorized": False,
    } or any(value["status"][key] is not False for key in ("execution_unlock", "timing_unlock", "validation_unlock")):
        raise TaskAnalysisError("task-only report authority differs")
    analysis = value["analysis"]
    if (
        type(analysis) is not dict
        or analysis["inventory"]["expected_attempt_count"] != EXPECTED_PROCESSES
        or analysis["fidelity"]["pair_count"] != EXPECTED_DIGEST_PAIRS
        or analysis["positions_replay"]["pair_count"] != EXPECTED_POSITIONS_PAIRS
        or analysis["task_validity"]["scene_count"] != EXPECTED_SCENES
        or len(analysis["recommendations"]) != 5
    ):
        raise TaskAnalysisError("task-only report denominator differs")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    _ = parser.parse_args(argv)
    print(
        "task-calibration analysis remains disabled pending an immutable completed-session authorization",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
