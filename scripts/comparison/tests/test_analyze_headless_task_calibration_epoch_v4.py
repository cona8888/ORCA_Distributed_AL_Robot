#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/analyze_headless_task_calibration_epoch_v4.py"
SPEC = importlib.util.spec_from_file_location("_test_epoch3_task_analysis", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
SESSION = ROOT / "results/simulator-demo/v1/headless-scientific/epoch-3-fractal-low-task-calibration/fractal-low-task-calibration-session-001"


class Epoch3TaskAnalysisTests(unittest.TestCase):
    def test_complete_real_session_analysis(self) -> None:
        report = MODULE.analyze_task_session(ROOT, SESSION)
        self.assertEqual(report["analysis"]["fidelity"]["pair_count"], 60)
        self.assertEqual(report["analysis"]["positions_replay"]["pair_count"], 20)
        self.assertEqual(report["analysis"]["task_validity"]["scene_count"], 20)
        self.assertFalse(report["classification"]["scientific_evidence"])
        self.assertFalse(report["status"]["timing_unlock"])

    def test_public_main_is_disabled(self) -> None:
        self.assertEqual(MODULE.main(), 1)


if __name__ == "__main__":
    unittest.main()
