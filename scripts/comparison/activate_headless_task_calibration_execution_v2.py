#!/usr/bin/env python3
"""Execute the one committed epoch-3 task-only session."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import sys
import types
from typing import Any


sys.dont_write_bytecode = True

BASE = {
    "bytes": 7976,
    "path": "scripts/comparison/activate_headless_task_calibration_execution_v1.py",
    "sha256": "f70d9e81f59e25f87dd20a730a0bdc417e0d4f67774ec80eb53c540afc34573a",
}
AUTH_PATH = "docs/simulator/headless_task_calibration_epoch_3_execution_v1.json"
RUNNER_IDENTITY = {
    "bytes": 3697,
    "path": "scripts/comparison/run_headless_task_calibration_epoch_v2.py",
    "sha256": "6a44501b5071e5d8e992c8731a0ab8095c3b3b0227402da0d25c7eae2a554bad",
}
ANALYZER_IDENTITY = {
    "bytes": 1793,
    "path": "scripts/comparison/analyze_headless_task_calibration_epoch_v3.py",
    "sha256": "fb9d399c4839264d3f2f8a5cedade284641b6a26940c95be915b28a975d6c86d",
}
PLAN = {
    "bytes": 349797,
    "path": "comparison/plans/simulator-demo/v1/headless-epoch-3-fractal-low-task-calibration/plan.json",
    "sha256": "2eb98865b61d185d7df92b79445e978b4ccab574113437cc7f1bc3bdbd6f0636",
}
RESULT_ROOT = PurePosixPath(
    "results/simulator-demo/v1/headless-scientific/epoch-3-fractal-low-task-calibration"
)
SESSION_NAME = "fractal-low-task-calibration-session-001"
EXPECTED_PROCESSES = 162


class ExecutionActivationError(ValueError):
    """The exact epoch-3 execution authority differs."""


def _core(root: Path) -> types.ModuleType:
    raw = root.joinpath(*PurePosixPath(BASE["path"]).parts).read_bytes()
    if len(raw) != BASE["bytes"] or hashlib.sha256(raw).hexdigest() != BASE["sha256"]:
        raise ExecutionActivationError("base execution activation identity differs")
    name = "_dancers_epoch3_execution_activation_core"
    module = types.ModuleType(name)
    module.__file__ = BASE["path"]
    sys.modules[name] = module
    exec(compile(raw, module.__file__, "exec", dont_inherit=True, optimize=0), module.__dict__)
    module.AUTH_PATH = AUTH_PATH
    module.RUNNER_PATH = RUNNER_IDENTITY["path"]
    module.RUNNER_IDENTITY = dict(RUNNER_IDENTITY)
    module.ANALYZER_IDENTITY = dict(ANALYZER_IDENTITY)
    module.PLAN = dict(PLAN)
    module.RESULT_ROOT = RESULT_ROOT
    module.SESSION_NAME = SESSION_NAME
    module.EXPECTED_PROCESSES = EXPECTED_PROCESSES
    return module


def execute_one_use_session(repository_root: Path) -> dict[str, Any]:
    root = Path(repository_root).resolve()
    return _core(root).execute_one_use_session(root)


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    try:
        receipt = execute_one_use_session(root)
    except BaseException as error:
        print(f"epoch-3 task execution rejected: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(_core(root)._canonical(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
