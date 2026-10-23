#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/analyze_headless_task_calibration_epoch_v3.py"
SPEC = importlib.util.spec_from_file_location("_test_epoch3_task_analyzer", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Epoch3TaskAnalyzerTests(unittest.TestCase):
    def test_record_is_nonauthorizing(self) -> None:
        record = MODULE.build_analyzer_record(ROOT)
        self.assertTrue(all(value is False for value in record["classification"].values()))
        self.assertEqual(record["contract"]["supported_plan_schema"], MODULE.PLAN_SCHEMA)

    def test_analysis_and_main_are_disabled(self) -> None:
        with self.assertRaisesRegex(MODULE.TaskAnalysisError, "disabled"):
            MODULE.analyze_task_session()
        self.assertEqual(MODULE.main([]), 1)


if __name__ == "__main__":
    unittest.main()
