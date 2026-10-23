#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/analyze_headless_task_calibration_epoch_v1.py"
SPEC = importlib.util.spec_from_file_location("_test_task_analyzer", SOURCE)
assert SPEC is not None and SPEC.loader is not None
ANALYZER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ANALYZER
SPEC.loader.exec_module(ANALYZER)


class TaskAnalyzerBoundaryTests(unittest.TestCase):
    def test_record_is_exact_and_nonauthorizing(self) -> None:
        record = ANALYZER.build_analyzer_record(ROOT)
        self.assertEqual(record["schema_version"], ANALYZER.ANALYZER_RECORD_SCHEMA)
        self.assertEqual(record["contract"], ANALYZER.ANALYZER_CONTRACT)
        self.assertTrue(all(value is False for value in record["classification"].values()))

    def test_analysis_and_cli_are_disabled_before_session_contract_freeze(self) -> None:
        with self.assertRaisesRegex(ANALYZER.TaskAnalysisError, "disabled"):
            ANALYZER.analyze_task_session()
        self.assertEqual(
            ANALYZER.main(["--plan", "x", "--session", "y", "--output", "z"]),
            1,
        )


if __name__ == "__main__":
    unittest.main()
