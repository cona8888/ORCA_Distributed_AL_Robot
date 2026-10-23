#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/activate_headless_task_calibration_execution_v3.py"
SPEC = importlib.util.spec_from_file_location("_test_epoch3_execution_repair", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Epoch3ExecutionRepairTests(unittest.TestCase):
    def test_repair_is_exact_zero_effect_and_task_only(self) -> None:
        value = MODULE._load_repair(ROOT)
        self.assertEqual(value["prior_attempt"]["simulator_process_count"], 0)
        self.assertEqual(value["prior_attempt"]["result_namespace_state"], "absent")
        self.assertFalse(value["timing_execution_authorized"])
        self.assertFalse(value["validation_execution_authorized"])
        self.assertFalse(value["benchmark_execution_authorized"])

    def test_main_does_not_execute_during_test(self) -> None:
        with mock.patch.object(MODULE, "execute_repaired_one_use_session", side_effect=MODULE.ExecutionRepairError("blocked")):
            self.assertEqual(MODULE.main(), 1)


if __name__ == "__main__":
    unittest.main()
