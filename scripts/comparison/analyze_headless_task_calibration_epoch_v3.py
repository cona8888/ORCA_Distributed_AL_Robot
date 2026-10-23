#!/usr/bin/env python3
"""Prospective epoch-3 task analyzer boundary; analysis remains disabled."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import sys
from typing import Any, Sequence


sys.dont_write_bytecode = True

PLAN_SCHEMA = "dancers-headless-task-calibration-epoch-plan-v2"
RUN_CLASSES = ("digest_fidelity", "positions_replay")
SOURCE_PATH = PurePosixPath("scripts/comparison/analyze_headless_task_calibration_epoch_v3.py")


class TaskAnalysisError(ValueError):
    """Epoch-3 analysis has not been authorized."""


def build_analyzer_record(repository_root: Path) -> dict[str, Any]:
    root = Path(os.path.abspath(repository_root))
    raw = root.joinpath(*SOURCE_PATH.parts).read_bytes()
    return {
        "classification": {
            "result_write_permitted": False,
            "scientific_evidence": False,
            "timing_claim_ready": False,
            "validation_claim_ready": False,
        },
        "contract": {
            "result_write_permitted": False,
            "run_classes": list(RUN_CLASSES),
            "supported_plan_schema": PLAN_SCHEMA,
        },
        "identity": {
            "bytes": len(raw),
            "path": SOURCE_PATH.as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "schema_version": "dancers-headless-task-analyzer-record-v2",
    }


def analyze_task_session(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise TaskAnalysisError("epoch-3 analysis is disabled before registered execution")


def main(argv: Sequence[str] | None = None) -> int:
    _ = argv
    print("epoch-3 task analysis is disabled before registered execution", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
