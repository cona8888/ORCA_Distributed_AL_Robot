#!/usr/bin/env python3
"""Publish the one exact completed epoch-3 task-only analysis."""

from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import sys
import types
from typing import Any


sys.dont_write_bytecode = True

BASE = {
    "bytes": 9796,
    "path": "scripts/comparison/activate_headless_task_calibration_analysis_v1.py",
    "sha256": "4bba92a2e4a5326bfa57f8565cb1eea4481912e67765377744a710320fad5d31",
}
AUTH_PATH = PurePosixPath("docs/simulator/headless_task_calibration_epoch_3_analysis_v1.json")
EXECUTION_RECEIPT = {
    "bytes": 1004,
    "path": "docs/simulator/headless_task_calibration_epoch_3_execution_receipt_v1.json",
    "sha256": "d77682ba4c795ff22c3231a7e91c174d27cd10d8bfe207f2a52557a3d69d885c",
}
ANALYZER = {
    "bytes": 5971,
    "path": "scripts/comparison/analyze_headless_task_calibration_epoch_v4.py",
    "sha256": "e9baf3a5138ae6699553baed166fa10d03b81f58ee74675d30b888ed89906bd3",
}
PLAN = {
    "bytes": 349797,
    "path": "comparison/plans/simulator-demo/v1/headless-epoch-3-fractal-low-task-calibration/plan.json",
    "sha256": "2eb98865b61d185d7df92b79445e978b4ccab574113437cc7f1bc3bdbd6f0636",
}
SESSION = {
    "bytes": 178481,
    "path": "results/simulator-demo/v1/headless-scientific/epoch-3-fractal-low-task-calibration/fractal-low-task-calibration-session-001/session.json",
    "sha256": "5008506084ac7cd9cacf6ba924a8233672e03d6393b24312eedead44ed59b397",
}
LEDGER = {
    "bytes": 86186,
    "path": "results/simulator-demo/v1/headless-scientific/epoch-3-fractal-low-task-calibration/fractal-low-task-calibration-session-001/ledger.ndjson",
    "sha256": "8f52e27d62e031dccd0e62575e9f86fc13703a6ad5c09dfa98a80fc238c5bb1a",
}
OUTPUT = PurePosixPath(
    "results/simulator-demo/v1/headless-scientific/epoch-3-fractal-low-task-calibration/"
    "fractal-low-task-calibration-session-001-analysis.json"
)
SESSION_DIR = PurePosixPath(SESSION["path"]).parent


class AnalysisActivationError(ValueError):
    """The exact epoch-3 analysis authority differs."""


def _core(root: Path) -> types.ModuleType:
    raw = root.joinpath(*PurePosixPath(BASE["path"]).parts).read_bytes()
    if len(raw) != BASE["bytes"] or hashlib.sha256(raw).hexdigest() != BASE["sha256"]:
        raise AnalysisActivationError("base analysis activation identity differs")
    name = "_dancers_epoch3_analysis_activation_core"
    module = types.ModuleType(name)
    module.__file__ = BASE["path"]
    sys.modules[name] = module
    exec(compile(raw, module.__file__, "exec", dont_inherit=True, optimize=0), module.__dict__)
    module.AUTH_PATH = AUTH_PATH
    module.EXECUTION_RECEIPT = dict(EXECUTION_RECEIPT)
    module.ANALYZER = dict(ANALYZER)
    module.PLAN = dict(PLAN)
    module.SESSION = dict(SESSION)
    module.LEDGER = dict(LEDGER)
    module.OUTPUT = OUTPUT
    module.SESSION_DIR = SESSION_DIR
    return module


def execute_authorized_analysis(repository_root: Path) -> dict[str, Any]:
    root = Path(repository_root).resolve()
    return _core(root).execute_authorized_analysis(root)


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    try:
        receipt = execute_authorized_analysis(root)
    except BaseException as error:
        print(f"epoch-3 task analysis rejected: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(_core(root)._canonical(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
