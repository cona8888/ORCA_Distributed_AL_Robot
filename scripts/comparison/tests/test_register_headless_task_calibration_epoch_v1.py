#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/register_headless_task_calibration_epoch_v1.py"
SPEC = importlib.util.spec_from_file_location("_test_task_registrar", SOURCE)
assert SPEC is not None and SPEC.loader is not None
REGISTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = REGISTER
SPEC.loader.exec_module(REGISTER)


class TaskRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compilation = REGISTER.prepare_registered_compilation(ROOT)

    def test_prepared_registration_is_exact_and_execution_nonauthorizing(self) -> None:
        self.assertEqual(len(self.compilation.files), 423)
        self.assertEqual(len(self.compilation.value["scenes"]), 180)
        self.assertTrue(self.compilation.value["classification"]["execution_authorized"])
        self.assertTrue(self.compilation.value["classification"]["calibration_execution_authorized"])
        self.assertFalse(self.compilation.value["classification"]["timing_execution_authorized"])
        self.assertFalse(self.compilation.value["classification"]["validation_execution_authorized"])
        self.assertTrue(self.compilation.value["operations"]["plan_written"])
        self.assertFalse(self.compilation.value["operations"]["simulator_invoked"])

    def test_publisher_is_create_exclusive_and_verifies_all_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent = root / "comparison/plans/simulator-demo/v1"
            parent.mkdir(parents=True)
            receipt = REGISTER.publish_registered_create_exclusive(root, self.compilation)
            self.assertEqual(receipt["file_count"], 423)
            self.assertEqual(receipt["plan_sha256"], self.compilation.sha256)
            with self.assertRaisesRegex(REGISTER.RegistrationError, "already exists"):
                REGISTER.publish_registered_create_exclusive(root, self.compilation)

    def test_public_entry_point_is_disabled(self) -> None:
        with self.assertRaisesRegex(REGISTER.RegistrationError, "one-use authorization"):
            REGISTER.main()


if __name__ == "__main__":
    unittest.main()
