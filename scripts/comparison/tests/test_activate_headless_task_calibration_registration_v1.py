#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/activate_headless_task_calibration_registration_v1.py"
SPEC = importlib.util.spec_from_file_location("_test_task_registration_activation", SOURCE)
assert SPEC is not None and SPEC.loader is not None
ACTIVATE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ACTIVATE
SPEC.loader.exec_module(ACTIVATE)


class TaskRegistrationActivationTests(unittest.TestCase):
    def test_authorization_is_canonical_exact_and_nonauthorizing_for_execution(self) -> None:
        value = ACTIVATE._load_authorization(ROOT)
        self.assertFalse(value["simulator_execution_authorized"])
        self.assertFalse(value["result_write_authorized"])
        self.assertEqual(value["plan"], ACTIVATE.EXPECTED_PLAN)
        self.assertEqual(value["registrar"], ACTIVATE.REGISTRAR_IDENTITY)
        activation = value["activation"]
        raw = SOURCE.read_bytes()
        self.assertEqual(activation["bytes"], len(raw))
        self.assertEqual(activation["sha256"], ACTIVATE._sha(raw))

    def test_effect_is_exactly_one_publisher_call(self) -> None:
        compilation = mock.Mock()
        compilation.files = tuple(range(ACTIVATE.EXPECTED_PLAN["file_count"]))
        compilation.data = b"x" * ACTIVATE.EXPECTED_PLAN["plan_bytes"]
        compilation.sha256 = ACTIVATE.EXPECTED_PLAN["plan_sha256"]
        compilation.total_bytes = ACTIVATE.EXPECTED_PLAN["total_bytes"]
        registrar = mock.Mock()
        registrar.prepare_registered_compilation.return_value = compilation
        registrar.publish_registered_create_exclusive.return_value = {
            **ACTIVATE.EXPECTED_PLAN,
            "classification": {},
            "plan_path": "comparison/plans/simulator-demo/v1/headless-epoch-2-task-calibration/plan.json",
            "schema_version": "dancers-headless-task-calibration-registration-receipt-v1",
        }
        with (
            mock.patch.object(ACTIVATE, "_require_clean_committed"),
            mock.patch.object(ACTIVATE, "_load_registrar", return_value=registrar),
        ):
            receipt = ACTIVATE.execute_one_use_registration(ROOT)
        registrar.prepare_registered_compilation.assert_called_once_with(ROOT)
        registrar.publish_registered_create_exclusive.assert_called_once_with(ROOT, compilation)
        self.assertTrue(all(value is False for value in receipt["classification"].values()))

    def test_plan_mismatch_fails_before_publication(self) -> None:
        compilation = mock.Mock()
        compilation.files = ()
        compilation.data = b""
        compilation.sha256 = "0" * 64
        compilation.total_bytes = 0
        registrar = mock.Mock()
        registrar.prepare_registered_compilation.return_value = compilation
        with (
            mock.patch.object(ACTIVATE, "_require_clean_committed"),
            mock.patch.object(ACTIVATE, "_load_registrar", return_value=registrar),
            self.assertRaisesRegex(ACTIVATE.ActivationError, "differs from authorization"),
        ):
            ACTIVATE.execute_one_use_registration(ROOT)
        registrar.publish_registered_create_exclusive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
