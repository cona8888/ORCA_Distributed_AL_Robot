#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/activate_headless_task_calibration_execution_v2.py"
SPEC = importlib.util.spec_from_file_location("_test_epoch3_execution_activation", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Epoch3ExecutionActivationTests(unittest.TestCase):
    def test_authorization_is_exact_task_only(self) -> None:
        value = MODULE._core(ROOT)._load_authorization(ROOT)
        self.assertEqual(value["plan"], MODULE.PLAN)
        self.assertEqual(value["runner"], MODULE.RUNNER_IDENTITY)
        self.assertEqual(value["analyzer"], MODULE.ANALYZER_IDENTITY)
        self.assertEqual(value["expected_process_count"], 162)
        self.assertFalse(value["timing_execution_authorized"])
        self.assertFalse(value["validation_execution_authorized"])
        self.assertFalse(value["benchmark_execution_authorized"])

    def test_no_execution_during_test(self) -> None:
        with mock.patch.object(MODULE, "execute_one_use_session", side_effect=MODULE.ExecutionActivationError("blocked")):
            self.assertEqual(MODULE.main(), 1)


if __name__ == "__main__":
    unittest.main()
