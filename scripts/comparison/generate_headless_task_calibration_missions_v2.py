#!/usr/bin/env python3
"""Compile the fresh-domain epoch-3 fractal-low task mission inventory."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import sys
import types
from typing import Any


sys.dont_write_bytecode = True

SOURCE = {
    "bytes": 17294,
    "path": "scripts/comparison/generate_headless_task_calibration_missions_v1.py",
    "sha256": "b24ecc3abd9f936b133f821ea164207c4792e72ecd7ae725c23429529a9f5b91",
}
DESIGN = {
    "bytes": 2227,
    "path": "docs/simulator/headless_task_calibration_epoch_3_design.json",
    "sha256": "323f0a6570e485d8c7ef4374cfec158c875c84854597fd6ba27858624d1063de",
}
PROTOCOL = {
    "bytes": 1225,
    "path": "docs/simulator/HEADLESS_TASK_CALIBRATION_EPOCH_3_ADDENDUM.md",
    "sha256": "69d7d4223353db2089b6a39ee51d0390b6d21d249adedbd1d61b85001422c1f6",
}
EPOCH_ID = "simulator-demo-v1-headless-epoch-3-fractal-low-task-calibration"
SCHEMA = "dancers-headless-task-calibration-mission-manifest-v2"


class Epoch3MissionError(ValueError):
    """The exact epoch-3 mission compiler inputs or output differ."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read(root: Path, identity: dict[str, Any], label: str) -> bytes:
    raw = root.joinpath(*PurePosixPath(identity["path"]).parts).read_bytes()
    if len(raw) != identity["bytes"] or _sha(raw) != identity["sha256"]:
        raise Epoch3MissionError(f"{label} identity differs")
    return raw


def _load_source(root: Path) -> types.ModuleType:
    raw = _read(root, SOURCE, "epoch-2 mission compiler")
    name = "_dancers_epoch3_task_mission_core"
    module = types.ModuleType(name)
    module.__file__ = SOURCE["path"]
    sys.modules[name] = module
    try:
        exec(compile(raw, module.__file__, "exec", dont_inherit=True, optimize=0), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def compile_task_missions(repository_root: Path) -> Any:
    root = Path(os.path.abspath(repository_root))
    if Path(os.path.realpath(root)) != root:
        raise Epoch3MissionError("repository root is not canonical")
    _read(root, DESIGN, "epoch-3 design")
    _read(root, PROTOCOL, "epoch-3 protocol")
    core = _load_source(root)
    core.SCHEMA = SCHEMA
    core.DESIGN_SCHEMA = "dancers-headless-task-calibration-epoch-3-design-v1"
    core.EPOCH_ID = EPOCH_ID
    core.ROOT_DOMAIN = b"dancers-headless-mission-bank-root-v3"
    core.STREAM_DOMAIN = b"dancers-headless-mission-bank-stream-v3\0"
    core.DESIGN_PATH = PurePosixPath(DESIGN["path"])
    core.PROTOCOL_PATH = PurePosixPath(PROTOCOL["path"])
    core.DESIGN_IDENTITY = dict(DESIGN)
    core.PROTOCOL_IDENTITY = dict(PROTOCOL)
    core.EXPECTED_FAMILIES = ("fractal_low",)
    core.EXPECTED_COUNTS = {"fractal_low": (2, 4)}
    core.EXPECTED_SEEDS = tuple(range(1, 11))
    core.EXPECTED_BANKS = 10
    core.EXPECTED_SCENES = 20
    core.EXPECTED_OVERLAPS = 0
    compilation = core.compile_task_missions(root)
    core.validate_compilation(compilation)
    if compilation.manifest["schema_version"] != SCHEMA:
        raise Epoch3MissionError("epoch-3 manifest schema differs")
    if compilation.manifest["derivation"] != {
        "count_enters_root": False,
        "epoch_id": EPOCH_ID,
        "root_domain_hex": core.ROOT_DOMAIN.hex(),
        "stream_domain_hex": core.STREAM_DOMAIN.hex(),
    }:
        raise Epoch3MissionError("epoch-3 derivation receipt differs")
    return compilation


def main() -> int:
    raise Epoch3MissionError(
        "epoch-3 mission publication is categorically disabled at this checkpoint"
    )


if __name__ == "__main__":
    try:
        main()
    except Epoch3MissionError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
