#!/usr/bin/env python3
"""Epoch-3 fractal-low task runner adapter; public execution is disabled."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import sys
import types
from typing import Any, Mapping, Sequence


sys.dont_write_bytecode = True

EPOCH_SCHEMA = "dancers-headless-task-calibration-epoch-plan-v2"
TRACK_ID = "simulator-demo-v1-headless-epoch-1"
EPOCH_ID = "simulator-demo-v1-headless-epoch-3-fractal-low-task-calibration"
PHASE = "calibration"
RUN_CLASSES = ("digest_fidelity", "positions_replay")
PLAN_ROOT = PurePosixPath(
    "comparison/plans/simulator-demo/v1/headless-epoch-3-fractal-low-task-calibration"
)
RESULT_ROOT = PurePosixPath(
    "results/simulator-demo/v1/headless-scientific/epoch-3-fractal-low-task-calibration"
)
SOURCE_PATH = PurePosixPath("scripts/comparison/run_headless_task_calibration_epoch_v2.py")
FREEZER_PATH = PurePosixPath("scripts/comparison/freeze_headless_task_calibration_epoch_v2.py")
BASE = {
    "bytes": 13257,
    "path": "scripts/comparison/run_headless_task_calibration_epoch_v1.py",
    "sha256": "6fa4c6bd5a75662f9c07e49a8043e68b5242f063a923434eb001518052a76cff",
}
RUNNER_RECORD_SCHEMA = "dancers-headless-task-runner-record-v1"
RUNNER_CONTRACT = {
    "execution_authorized": False,
    "run_classes": list(RUN_CLASSES),
    "supported_plan_schema": EPOCH_SCHEMA,
}
_CACHE: dict[str, types.ModuleType] = {}


class TaskRunnerError(ValueError):
    """The exact epoch-3 task runner binding differs."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _core(root: Path) -> types.ModuleType:
    canonical = Path(os.path.abspath(root))
    key = os.fspath(canonical)
    if key in _CACHE:
        return _CACHE[key]
    raw = canonical.joinpath(*PurePosixPath(BASE["path"]).parts).read_bytes()
    if len(raw) != BASE["bytes"] or _sha(raw) != BASE["sha256"]:
        raise TaskRunnerError("epoch-2 task runner identity differs")
    name = "_dancers_epoch3_task_runner_core"
    module = types.ModuleType(name)
    module.__file__ = BASE["path"]
    sys.modules[name] = module
    exec(compile(raw, module.__file__, "exec", dont_inherit=True, optimize=0), module.__dict__)
    module.EPOCH_SCHEMA = EPOCH_SCHEMA
    module.EPOCH_ID = EPOCH_ID
    module.PHASE = PHASE
    module.RUN_CLASSES = RUN_CLASSES
    module.PLAN_ROOT = PLAN_ROOT
    module.RESULT_ROOT = RESULT_ROOT
    module.SOURCE_PATH = SOURCE_PATH
    module.FREEZER_PATH = FREEZER_PATH
    module.RUNNER_CONTRACT = dict(RUNNER_CONTRACT)
    module._BASE_CACHE.clear()
    _CACHE[key] = module
    return module


def _flatten_schedule(plan: Mapping[str, Any]) -> list[tuple[Mapping[str, Any], int]]:
    return _core(Path(__file__).resolve().parents[2])._flatten_schedule(plan)


def build_runner_record(repository_root: Path) -> dict[str, Any]:
    return _core(repository_root).build_runner_record(repository_root)


def load_authenticated_plan(*args: Any, **kwargs: Any) -> Any:
    if len(args) < 2:
        raise TaskRunnerError("plan and repository root are required")
    return _core(Path(args[1])).load_authenticated_plan(*args, **kwargs)


def execute_custodied_schedule(*args: Any, **kwargs: Any) -> dict[str, Any]:
    if not args:
        raise TaskRunnerError("authenticated plan is required")
    return _core(args[0].repository_root).execute_custodied_schedule(*args, **kwargs)


def main(argv: Sequence[str] | None = None) -> int:
    _ = argv
    print(
        "epoch-3 task runner remains nonauthorizing; no plan read, output, host probe, or process is permitted",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
