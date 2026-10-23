#!/usr/bin/env python3
"""Analyze the completed epoch-3 fractal-low task-only cohort in memory."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import sys
import types
from typing import Any, Mapping


sys.dont_write_bytecode = True

BASE = {
    "bytes": 10612,
    "path": "scripts/comparison/analyze_headless_task_calibration_epoch_v2.py",
    "sha256": "90184be7b38413745d0629433943aafaa78ce8c6abc8ffbfd087b2b7fdf06afe",
}
PLAN = {
    "bytes": 349797,
    "path": "comparison/plans/simulator-demo/v1/headless-epoch-3-fractal-low-task-calibration/plan.json",
    "sha256": "2eb98865b61d185d7df92b79445e978b4ccab574113437cc7f1bc3bdbd6f0636",
}
RUNNER = {
    "bytes": 3697,
    "path": "scripts/comparison/run_headless_task_calibration_epoch_v2.py",
    "sha256": "6a44501b5071e5d8e992c8731a0ab8095c3b3b0227402da0d25c7eae2a554bad",
}
PLAN_ROOT = PurePosixPath(PLAN["path"]).parent
RESULT_ROOT = PurePosixPath(
    "results/simulator-demo/v1/headless-scientific/epoch-3-fractal-low-task-calibration"
)
SCHEMA = "dancers-headless-task-calibration-analysis-v2"
EPOCH_ID = "simulator-demo-v1-headless-epoch-3-fractal-low-task-calibration"


class TaskAnalysisError(ValueError):
    """The exact epoch-3 session or task analysis differs."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load(root: Path) -> types.ModuleType:
    raw = root.joinpath(*PurePosixPath(BASE["path"]).parts).read_bytes()
    if len(raw) != BASE["bytes"] or _sha(raw) != BASE["sha256"]:
        raise TaskAnalysisError("epoch-2 analyzer identity differs")
    name = "_dancers_epoch3_task_analysis_core"
    module = types.ModuleType(name)
    module.__file__ = BASE["path"]
    sys.modules[name] = module
    exec(compile(raw, module.__file__, "exec", dont_inherit=True, optimize=0), module.__dict__)
    module.SCHEMA = SCHEMA
    module.PLAN_SCHEMA = "dancers-headless-task-calibration-epoch-plan-v2"
    module.EPOCH_ID = EPOCH_ID
    module.PLAN_ROOT = PLAN_ROOT
    module.RESULT_ROOT = RESULT_ROOT
    module.PLAN_IDENTITY = dict(PLAN)
    module.TASK_RUNNER_PATH = PurePosixPath(RUNNER["path"])
    module.TASK_RUNNER = dict(RUNNER)
    module.EXPECTED_SCENES = 20
    module.EXPECTED_DIGEST_PAIRS = 60
    module.EXPECTED_POSITIONS_PAIRS = 20
    module.EXPECTED_PROCESSES = 162
    return module


def analyze_task_session(repository_root: Path, session_path: Path) -> dict[str, Any]:
    root = Path(os.path.abspath(repository_root))
    module = _load(root)
    authenticated = module.load_authenticated_plan(root)
    core = authenticated["core"]
    try:
        session = core.audit_session(authenticated, session_path)
        fidelity = core.analyze_fidelity_and_task(authenticated, session)
    except Exception as error:
        raise TaskAnalysisError(f"epoch-3 task analysis failed: {error}") from error
    validity = {row["scene_id"]: row["task_valid"] for row in fidelity["task_report"]["scene_rows"]}
    per_count = []
    prefix = []
    prefix_open = True
    for count in (2, 4):
        passed = sum(validity[f"task-calibration-fractal_low-n-{count}-seed-{seed:02d}"] for seed in range(1, 11))
        all_ten = passed == 10
        if prefix_open and all_ten:
            prefix.append(count)
        else:
            prefix_open = False
        per_count.append({
            "all_ten_banks_valid": all_ten,
            "robot_count": count,
            "task_scene_count": 10,
            "task_valid_scene_count": passed,
        })
    report = {
        "analysis": {
            "fidelity": fidelity["fidelity_report"],
            "inventory": session["report"],
            "positions_replay": fidelity["positions_report"],
            "recommendation": {
                "candidate_task_valid_prefix": prefix,
                "family": "fractal_low",
                "family_baseline": prefix[0] if prefix else None,
                "negative_stop": not prefix,
                "per_count": per_count,
            },
            "task_validity": fidelity["task_report"],
        },
        "classification": {
            "benchmark_claim_ready": False,
            "capacity_claim_ready": False,
            "scientific_evidence": False,
            "task_calibration_evidence": True,
            "timing_claim_ready": False,
            "validation_claim_ready": False,
        },
        "epoch_id": EPOCH_ID,
        "plan": dict(PLAN),
        "schema_version": SCHEMA,
        "status": {"analysis_completed": True, "timing_unlock": False, "validation_unlock": False},
    }
    validate_report(report)
    return report


def validate_report(value: Any) -> dict[str, Any]:
    if type(value) is not dict or value.get("schema_version") != SCHEMA or value.get("plan") != PLAN:
        raise TaskAnalysisError("epoch-3 report identity differs")
    analysis = value.get("analysis")
    if (
        type(analysis) is not dict
        or analysis["inventory"]["expected_attempt_count"] != 162
        or analysis["fidelity"]["pair_count"] != 60
        or analysis["positions_replay"]["pair_count"] != 20
        or analysis["task_validity"]["scene_count"] != 20
        or analysis["recommendation"]["family"] != "fractal_low"
        or any(value["classification"][key] for key in ("benchmark_claim_ready", "capacity_claim_ready", "scientific_evidence", "timing_claim_ready", "validation_claim_ready"))
        or value["status"]["timing_unlock"] is not False
        or value["status"]["validation_unlock"] is not False
    ):
        raise TaskAnalysisError("epoch-3 report denominator or authority differs")
    return value


def canonical_json_bytes(value: object) -> bytes:
    return _load(Path(__file__).resolve().parents[2]).canonical_json_bytes(value)


def main() -> int:
    print("epoch-3 task analysis requires a committed completed-session authorization", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
