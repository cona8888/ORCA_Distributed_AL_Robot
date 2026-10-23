#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/activate_headless_task_calibration_registration_v2.py"
SPEC = importlib.util.spec_from_file_location("_test_epoch3_registration_activation", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Epoch3RegistrationActivationTests(unittest.TestCase):
    def test_authorization_is_exact_and_nonauthorizing(self) -> None:
        core = MODULE._core(ROOT)
        value = core._load_authorization(ROOT)
        self.assertEqual(value["plan"], MODULE.EXPECTED_PLAN)
        self.assertFalse(value["simulator_execution_authorized"])
        self.assertFalse(value["result_write_authorized"])

    def test_no_publication_during_test(self) -> None:
        with mock.patch.object(MODULE, "execute_one_use_registration", side_effect=MODULE.ActivationError("blocked")):
            self.assertEqual(MODULE.main(), 1)


if __name__ == "__main__":
    unittest.main()
