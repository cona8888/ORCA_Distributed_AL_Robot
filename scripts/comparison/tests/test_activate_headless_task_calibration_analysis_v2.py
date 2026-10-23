#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/activate_headless_task_calibration_analysis_v2.py"
SPEC = importlib.util.spec_from_file_location("_test_epoch3_analysis_activation", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Epoch3AnalysisActivationTests(unittest.TestCase):
    def test_authorization_is_exact_and_all_claims_false(self) -> None:
        value, _raw = MODULE._core(ROOT)._load_authorization(ROOT)
        self.assertEqual(value["analysis"], MODULE.ANALYZER)
        self.assertEqual(value["session"], MODULE.SESSION)
        self.assertEqual(value["ledger"], MODULE.LEDGER)
        for key in (
            "scientific_evidence_authorized", "timing_execution_authorized",
            "timing_claim_authorized", "validation_execution_authorized",
            "validation_claim_authorized", "benchmark_claim_authorized",
        ):
            self.assertFalse(value[key])

    def test_no_analysis_during_test(self) -> None:
        with mock.patch.object(MODULE, "execute_authorized_analysis", side_effect=MODULE.AnalysisActivationError("blocked")):
            self.assertEqual(MODULE.main(), 1)


if __name__ == "__main__":
    unittest.main()
