#!/usr/bin/env python3

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/analyze_headless_task_calibration_epoch_v2.py"
SPEC = importlib.util.spec_from_file_location("_test_task_analyzer_v2", SOURCE)
assert SPEC is not None and SPEC.loader is not None
ANALYZER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ANALYZER
SPEC.loader.exec_module(ANALYZER)


class TaskAnalyzerV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.authenticated = ANALYZER.load_authenticated_plan(ROOT)

    def test_plan_geometry_and_complete_task_denominator_are_authenticated(self) -> None:
        self.assertEqual(len(self.authenticated["runner_plan"].schedule), 1450)
        self.assertEqual(len(self.authenticated["plan"]["scenes"]), 180)
        self.assertIn("analyze_sweeps", self.authenticated["geometry_namespace"])
        self.assertEqual(self.authenticated["plan_identity"], ANALYZER.PLAN_IDENTITY)

    def test_prospective_rule_is_largest_contiguous_all_ten_prefix(self) -> None:
        plan = self.authenticated["plan"]
        rows = []
        for scene in plan["scenes"]:
            valid = not (
                scene["family"] == "open"
                and scene["robot_count"] in (10, 12)
                and scene["seed"] == 10
            )
            rows.append({"scene_id": scene["scene_id"], "task_valid": valid})
        recommendation = ANALYZER._recommend(plan, {"scene_rows": rows})
        open_row = next(row for row in recommendation if row["family"] == "open")
        self.assertEqual(open_row["candidate_task_valid_prefix"], [8])
        self.assertEqual(open_row["family_baseline"], 8)

    def test_report_authority_mutation_is_rejected(self) -> None:
        skeleton = {
            "analysis": {
                "fidelity": {"pair_count": 540},
                "inventory": {"expected_attempt_count": 1450},
                "positions_replay": {"pair_count": 180},
                "recommendations": [{}, {}, {}, {}, {}],
                "task_validity": {"scene_count": 180},
            },
            "classification": {
                "benchmark_claim_ready": False,
                "capacity_claim_ready": False,
                "scientific_evidence": False,
                "task_calibration_evidence": True,
                "timing_claim_ready": False,
                "timing_execution_authorized": False,
                "validation_claim_ready": False,
                "validation_execution_authorized": False,
            },
            "epoch_id": ANALYZER.EPOCH_ID,
            "plan": dict(ANALYZER.PLAN_IDENTITY),
            "schema_version": ANALYZER.SCHEMA,
            "status": {
                "analysis_completed": True,
                "execution_unlock": False,
                "timing_unlock": False,
                "validation_unlock": False,
            },
        }
        ANALYZER.validate_report(skeleton)
        mutant = copy.deepcopy(skeleton)
        mutant["classification"]["timing_execution_authorized"] = True
        with self.assertRaisesRegex(ANALYZER.TaskAnalysisError, "authority differs"):
            ANALYZER.validate_report(mutant)

    def test_public_cli_is_disabled(self) -> None:
        self.assertEqual(ANALYZER.main(["--session", "x", "--output", "y"]), 1)


if __name__ == "__main__":
    unittest.main()
