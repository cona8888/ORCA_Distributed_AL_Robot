#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/freeze_headless_task_calibration_epoch_v2.py"
SPEC = importlib.util.spec_from_file_location("_test_epoch3_task_freezer", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Epoch3TaskFreezerTests(unittest.TestCase):
    def test_exact_plan_denominators_and_locks(self) -> None:
        compiled = MODULE.compile_epoch(ROOT)
        value = compiled.value
        self.assertEqual(len(value["scenes"]), 20)
        self.assertEqual(value["output_grammar"]["registered_result_namespace"], MODULE.RESULT_ROOT.as_posix())
        self.assertEqual(value["schedule"]["warmups"]["process_count"], 2)
        self.assertEqual(value["schedule"]["inventories"]["digest_fidelity"]["measured_process_count"], 120)
        self.assertEqual(value["schedule"]["inventories"]["positions_replay"]["measured_process_count"], 40)
        self.assertFalse(value["schedule"]["timing_gate"]["headless_timing_permitted"])
        self.assertEqual(
            value["status"]["registration_state"],
            "compiled-candidate-only-not-registered",
        )

    def test_compilation_is_deterministic_and_publication_disabled(self) -> None:
        first = MODULE.compile_epoch(ROOT)
        second = MODULE.compile_epoch(ROOT)
        self.assertEqual(first.data, second.data)
        self.assertEqual(first.sha256, second.sha256)
        with self.assertRaisesRegex(MODULE.EpochFreezeError, "disabled"):
            MODULE.main()


if __name__ == "__main__":
    unittest.main()
