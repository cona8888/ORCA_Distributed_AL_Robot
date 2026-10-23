#!/usr/bin/env python3
"""Prospective epoch-5 validation analyzer boundary; analysis is disabled."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import sys
from typing import Any, Sequence


sys.dont_write_bytecode = True

PLAN_SCHEMA = "dancers-headless-validation-epoch-plan-v1"
RUN_CLASSES = ("digest_fidelity", "positions_replay", "headless_timing")
SOURCE_PATH = PurePosixPath("scripts/comparison/analyze_headless_validation_epoch_v1.py")


class ValidationAnalysisError(ValueError):
    """Epoch-5 analysis has not been authorized."""


def build_analyzer_record(repository_root: Path) -> dict[str, Any]:
    root = Path(os.path.abspath(repository_root))
    raw = root.joinpath(*SOURCE_PATH.parts).read_bytes()
    return {
        "classification": {
            "benchmark_claim_ready": False,
            "result_write_permitted": False,
            "scientific_evidence": False,
            "validation_claim_ready": False,
        },
        "contract": {
            "bootstrap_replicates": 100000,
            "cluster": "family-by-mission-bank",
            "result_write_permitted": False,
            "run_classes": list(RUN_CLASSES),
            "supported_plan_schema": PLAN_SCHEMA,
        },
        "identity": {
            "bytes": len(raw),
            "path": SOURCE_PATH.as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "schema_version": "dancers-headless-validation-analyzer-record-v1",
    }


def analyze_validation_session(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise ValidationAnalysisError("epoch-5 analysis is disabled before registered execution")


def main(argv: Sequence[str] | None = None) -> int:
    _ = argv
    print("epoch-5 validation analysis is disabled before registered execution", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
