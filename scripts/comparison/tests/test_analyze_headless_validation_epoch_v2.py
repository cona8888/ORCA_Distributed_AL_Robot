from __future__ import annotations

import importlib.util
import math
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/analyze_headless_validation_epoch_v2.py"
SPEC = importlib.util.spec_from_file_location("validation_analysis", SOURCE)
assert SPEC is not None and SPEC.loader is not None
A = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(A)


class ValidationAnalysisTests(unittest.TestCase):
    def test_historical_plan_binding_fails_closed_after_supersession(self) -> None:
        with self.assertRaises(A.ValidationAnalysisError):
            A.load_authenticated_plan(ROOT)

    def test_runtime_seed_mapping_is_explicit_and_bounded(self) -> None:
        self.assertEqual([A._runtime_seed(seed) for seed in range(11, 31)], list(range(1, 11)) * 2)
        for bad in (10, 31, True):
            with self.assertRaises(A.ValidationAnalysisError):
                A._runtime_seed(bad)

    def test_bootstrap_is_deterministic_and_uses_twenty_clusters(self) -> None:
        logs = {seed: math.log(1.2 + (seed - 11) / 1000.0) for seed in A.MISSION_SEEDS}
        left = A.cluster_bootstrap(logs, family="open", robot_count=8, draws=100)
        right = A.cluster_bootstrap(logs, family="open", robot_count=8, draws=100)
        self.assertEqual(left, right)
        self.assertEqual(left["cluster_count"], 20)
        self.assertEqual(left["draw_count"], 100)
        self.assertGreater(left["lower_95"], 1.0)

    def test_bootstrap_domain_separates_cells(self) -> None:
        logs = {seed: (-0.2 if seed == 11 else 0.2) for seed in A.MISSION_SEEDS}
        one = A.cluster_bootstrap(logs, family="open", robot_count=8, draws=50)
        two = A.cluster_bootstrap(logs, family="perfect_maze", robot_count=8, draws=50)
        self.assertNotEqual((one["lower_95"], one["upper_95"]), (two["lower_95"], two["upper_95"]))

    def test_type7_boundaries(self) -> None:
        self.assertEqual(A._type7([1.0, 2.0, 3.0], 0.0), 1.0)
        self.assertEqual(A._type7([1.0, 2.0, 3.0], 0.5), 2.0)
        self.assertEqual(A._type7([1.0, 2.0, 3.0], 1.0), 3.0)

    def test_cell_timing_denominator_is_sixty_pairs(self) -> None:
        self.assertEqual(len(A.MISSION_SEEDS) * 3, 60)

    def test_public_cli_is_nonauthorizing(self) -> None:
        self.assertEqual(A.main([]), 1)


if __name__ == "__main__":
    unittest.main()
