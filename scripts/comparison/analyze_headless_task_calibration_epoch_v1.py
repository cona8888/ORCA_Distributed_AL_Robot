#!/usr/bin/env python3
"""Closed analyzer contract for epoch-2 task-only calibration.

This checkpoint exposes only its authenticated component record.  Result reads
and report publication remain disabled until the task-session schema is frozen
and independently tested.
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sys
from typing import Any, Sequence


sys.dont_write_bytecode = True

PLAN_SCHEMA = "dancers-headless-task-calibration-epoch-plan-v1"
TRACK_ID = "simulator-demo-v1-headless-epoch-1"
RUN_CLASSES = ("digest_fidelity", "positions_replay")
SOURCE_PATH = "scripts/comparison/analyze_headless_task_calibration_epoch_v1.py"
ANALYZER_RECORD_SCHEMA = "dancers-headless-task-analyzer-record-v1"
ANALYZER_CONTRACT = {
    "result_write_permitted": False,
    "run_classes": list(RUN_CLASSES),
    "supported_plan_schema": PLAN_SCHEMA,
}


class TaskAnalysisError(ValueError):
    """The task-only analyzer boundary is not yet activated."""


def build_analyzer_record(repository_root: Path) -> dict[str, Any]:
    root = Path(os.path.abspath(repository_root))
    raw = root.joinpath(*SOURCE_PATH.split("/")).read_bytes()
    return {
        "classification": {
            "execution_authorized": False,
            "scientific_evidence": False,
            "timing_execution_authorized": False,
            "validation_execution_authorized": False,
        },
        "contract": dict(ANALYZER_CONTRACT),
        "identity": {
            "bytes": len(raw),
            "path": SOURCE_PATH,
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "schema_version": ANALYZER_RECORD_SCHEMA,
        "track_id": TRACK_ID,
    }


def analyze_task_session(*_args: Any, **_kwargs: Any) -> None:
    raise TaskAnalysisError(
        "task-calibration result analysis is disabled until its session contract is frozen"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--session", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    _ = parser.parse_args(argv)
    print(
        "task-calibration analyzer remains nonauthorizing; no plan, result, or output is accessed",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
