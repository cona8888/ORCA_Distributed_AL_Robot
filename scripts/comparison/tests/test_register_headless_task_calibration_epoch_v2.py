#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/register_headless_task_calibration_epoch_v2.py"
SPEC = importlib.util.spec_from_file_location("_test_epoch3_task_registrar", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Epoch3TaskRegistrarTests(unittest.TestCase):
    def test_registered_candidate_is_exact_and_nonauthorizing(self) -> None:
        compiled = MODULE.prepare_registered_compilation(ROOT)
        self.assertEqual(len(compiled.files), 63)
        self.assertEqual(compiled.value["status"]["registration_state"], "registered-committed")
        self.assertTrue(compiled.value["operations"]["plan_written"])
        for key in (
            "execution_authorized", "timing_execution_authorized",
            "validation_execution_authorized", "benchmark_execution_authorized",
        ):
            self.assertFalse(compiled.value["classification"][key])

    def test_publication_is_disabled(self) -> None:
        with self.assertRaisesRegex(MODULE.RegistrationError, "disabled"):
            MODULE.main()


if __name__ == "__main__":
    unittest.main()
