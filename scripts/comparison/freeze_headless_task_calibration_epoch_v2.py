#!/usr/bin/env python3
"""Compile the exact epoch-3 fractal-low task-only plan in memory."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import sys
import types
from typing import Any


sys.dont_write_bytecode = True

EPOCH_SCHEMA = "dancers-headless-task-calibration-epoch-plan-v2"
TRACK_ID = "simulator-demo-v1-headless-epoch-1"
EPOCH_ID = "simulator-demo-v1-headless-epoch-3-fractal-low-task-calibration"
PHASE = "calibration"
RUN_CLASSES = ("digest_fidelity", "positions_replay")
FAMILIES = ("fractal_low",)
COUNTS = {"fractal_low": (2, 4)}
SEEDS = tuple(range(1, 11))
REPETITIONS = {"digest_fidelity": 3, "positions_replay": 1}
PLAN_ROOT = PurePosixPath(
    "comparison/plans/simulator-demo/v1/headless-epoch-3-fractal-low-task-calibration"
)
RESULT_ROOT = PurePosixPath(
    "results/simulator-demo/v1/headless-scientific/epoch-3-fractal-low-task-calibration"
)
SOURCE_PATH = PurePosixPath("scripts/comparison/freeze_headless_task_calibration_epoch_v2.py")
RUNNER_PATH = PurePosixPath("scripts/comparison/run_headless_task_calibration_epoch_v2.py")
ANALYZER_PATH = PurePosixPath("scripts/comparison/analyze_headless_task_calibration_epoch_v3.py")
MISSION_COMPILER_PATH = PurePosixPath(
    "scripts/comparison/generate_headless_task_calibration_missions_v2.py"
)
BASE = {
    "bytes": 29960,
    "path": "scripts/comparison/freeze_headless_task_calibration_epoch_v1.py",
    "sha256": "a05d9907ab0422e096fa9e0bba92642c0a18af303feccc85c8880c21789c14c5",
}
MISSION_COMPILER = {
    "bytes": 3836,
    "path": MISSION_COMPILER_PATH.as_posix(),
    "sha256": "b4aa055ab38e63c0c379b15b2b4b7baa14ccf32a27988f4db89608538bc7e71f",
}
EXPECTED_SCENES = 20
EXPECTED_WARMUPS = 2
EXPECTED_DIGEST = 120
EXPECTED_POSITIONS = 40
EXPECTED_PROCESSES = 162
ORDER_SEED = "e3" * 32


class EpochFreezeError(ValueError):
    """The exact epoch-3 plan compiler inputs or output differ."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _load(root: Path, identity: dict[str, Any], name: str) -> types.ModuleType:
    raw = root.joinpath(*PurePosixPath(identity["path"]).parts).read_bytes()
    if len(raw) != identity["bytes"] or _sha(raw) != identity["sha256"]:
        raise EpochFreezeError(f"{name} identity differs")
    module = types.ModuleType(name)
    module.__file__ = identity["path"]
    sys.modules[name] = module
    exec(compile(raw, module.__file__, "exec", dont_inherit=True, optimize=0), module.__dict__)
    return module


def _core(repository_root: Path) -> types.ModuleType:
    root = Path(os.path.abspath(repository_root))
    module = _load(root, BASE, "_dancers_epoch3_task_freezer_core")
    module.EPOCH_SCHEMA = EPOCH_SCHEMA
    module.EPOCH_ID = EPOCH_ID
    module.PHASE = PHASE
    module.RUN_CLASSES = RUN_CLASSES
    module.FAMILIES = FAMILIES
    module.COUNTS = dict(COUNTS)
    module.SEEDS = SEEDS
    module.REPETITIONS = dict(REPETITIONS)
    module.PLAN_ROOT = PLAN_ROOT
    module.RESULT_ROOT = RESULT_ROOT
    module.SOURCE_PATH = SOURCE_PATH
    module.RUNNER_PATH = RUNNER_PATH
    module.ANALYZER_PATH = ANALYZER_PATH
    module.MISSION_COMPILER_PATH = MISSION_COMPILER_PATH
    module.MISSION_COMPILER = dict(MISSION_COMPILER)
    module.EXPECTED_SCENES = EXPECTED_SCENES
    module.EXPECTED_WARMUPS = EXPECTED_WARMUPS
    module.EXPECTED_DIGEST = EXPECTED_DIGEST
    module.EXPECTED_POSITIONS = EXPECTED_POSITIONS
    module.EXPECTED_PROCESSES = EXPECTED_PROCESSES
    module.ORDER_SEED = ORDER_SEED
    module.REGISTERED_CLASSIFICATION = {
        **module.REGISTERED_CLASSIFICATION,
        "calibration_execution_authorized": False,
        "execution_authorized": False,
        "scientific_evidence_eligible": False,
    }
    return module


def compile_epoch(repository_root: Path) -> Any:
    root = Path(os.path.abspath(repository_root))
    core = _core(root)
    original_loader = core._load_exact

    def load_exact(passed_root: Path, identity: dict[str, Any], name: str) -> types.ModuleType:
        loaded = original_loader(passed_root, identity, name)
        if identity == MISSION_COMPILER:
            return loaded
        return loaded

    core._load_exact = load_exact
    compilation = core.compile_epoch(root)
    core.validate_compilation(compilation)
    return compilation


def canonical_json_bytes(value: object) -> bytes:
    return _core(Path(__file__).resolve().parents[2]).canonical_json_bytes(value)


REGISTERED_CLASSIFICATION = _core(Path(__file__).resolve().parents[2]).REGISTERED_CLASSIFICATION
REGISTERED_STATUS = _core(Path(__file__).resolve().parents[2]).REGISTERED_STATUS


def validate_epoch_bytes(raw: bytes) -> dict[str, Any]:
    return _core(Path(__file__).resolve().parents[2]).validate_epoch_bytes(raw)


def validate_compilation(compilation: Any) -> None:
    _core(Path(__file__).resolve().parents[2]).validate_compilation(compilation)


def main() -> int:
    raise EpochFreezeError("epoch-3 plan publication is categorically disabled")


if __name__ == "__main__":
    try:
        main()
    except EpochFreezeError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
