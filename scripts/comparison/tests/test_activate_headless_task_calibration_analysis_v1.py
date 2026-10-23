from __future__ import annotations

import contextlib
import io
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/activate_headless_task_calibration_analysis_v1.py"
SPEC = importlib.util.spec_from_file_location("_test_task_analysis_activation", SOURCE)
assert SPEC is not None and SPEC.loader is not None
activation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = activation
SPEC.loader.exec_module(activation)


class AnalysisActivationTests(unittest.TestCase):
    def test_01_authorization_is_exact_and_all_claim_unlocks_are_false(self) -> None:
        value, raw = activation._load_authorization(ROOT)
        self.assertEqual(raw, activation._canonical(value))
        self.assertEqual(value["analysis"], activation.ANALYZER)
        self.assertEqual(value["execution_receipt"], activation.EXECUTION_RECEIPT)
        self.assertEqual(value["session"], activation.SESSION)
        self.assertEqual(value["ledger"], activation.LEDGER)
        for key in (
            "scientific_evidence_authorized",
            "timing_execution_authorized",
            "timing_claim_authorized",
            "validation_execution_authorized",
            "validation_claim_authorized",
            "benchmark_claim_authorized",
        ):
            self.assertIs(value[key], False)

    def test_02_bound_completed_session_and_analyzer_identities_match(self) -> None:
        for identity, label in (
            (activation.EXECUTION_RECEIPT, "execution receipt"),
            (activation.ANALYZER, "analyzer"),
            (activation.SESSION, "session"),
            (activation.LEDGER, "ledger"),
        ):
            raw = activation._read_identity(ROOT, identity, label)
            self.assertEqual(len(raw), identity["bytes"])

    def test_03_main_emits_only_the_mocked_closed_receipt(self) -> None:
        receipt = {
            "analysis_authorization_sha256": "0" * 64,
            "benchmark_claim_ready": False,
            "operation": "analyze-exact-completed-task-only-session",
            "output": {"bytes": 1, "path": activation.OUTPUT.as_posix(), "sha256": "1" * 64},
            "schema_version": "dancers-headless-task-calibration-analysis-receipt-v1",
            "scientific_evidence": False,
            "timing_claim_ready": False,
            "validation_claim_ready": False,
        }
        output = io.BytesIO()
        stdout = io.TextIOWrapper(output, encoding="utf-8")
        with mock.patch.object(activation, "execute_authorized_analysis", return_value=receipt):
            with mock.patch.object(activation.sys, "stdout", stdout):
                self.assertEqual(activation.main(), 0)
                stdout.flush()
        self.assertEqual(json.loads(output.getvalue()), receipt)


if __name__ == "__main__":
    unittest.main()
