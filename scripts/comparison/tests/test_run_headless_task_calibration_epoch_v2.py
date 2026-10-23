#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/run_headless_task_calibration_epoch_v2.py"
SPEC = importlib.util.spec_from_file_location("_test_epoch3_task_runner", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Epoch3TaskRunnerTests(unittest.TestCase):
    def test_record_is_exact_and_nonauthorizing(self) -> None:
        record = MODULE.build_runner_record(ROOT)
        self.assertEqual(record["identity"]["path"], MODULE.SOURCE_PATH.as_posix())
        self.assertEqual(record["contract"], MODULE.RUNNER_CONTRACT)
        self.assertTrue(all(value is False for value in record["classification"].values()))

    def test_public_main_is_disabled(self) -> None:
        self.assertEqual(MODULE.main([]), 1)


if __name__ == "__main__":
    unittest.main()
