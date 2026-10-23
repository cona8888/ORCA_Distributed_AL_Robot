#!/usr/bin/env python3

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/generate_headless_task_calibration_missions_v1.py"
NAME = "_test_generate_headless_task_calibration_missions_v1"
SPEC = importlib.util.spec_from_file_location(NAME, SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[NAME] = MODULE
SPEC.loader.exec_module(MODULE)


class TaskMissionCompilerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compilation = MODULE.compile_task_missions(ROOT)

    def test_01_exact_inventory_and_deterministic_receipt(self) -> None:
        compilation = self.compilation
        MODULE.validate_compilation(compilation)
        self.assertEqual(len(compilation.files), 232)
        self.assertEqual(
            compilation.manifest["inventory"],
            {
                "bank_count": 50,
                "file_count_including_manifest": 232,
                "scene_count": 180,
            },
        )
        self.assertEqual(
            compilation.manifest_sha256,
            "cd1537793c05b71d7af1edfc97bfa0e351e4fc9a71e14293c06c9faa21a7da5b",
        )
        self.assertEqual(len(compilation.manifest_bytes), 68422)

    def test_02_domain_vector_and_epoch_1_nonidentity_are_exact(self) -> None:
        self.assertEqual(
            MODULE._fresh_generator_seed(
                "open",
                1,
                "44e3f028d43cc2fe0bcc05179be340a1619c944b871a20a568944becf04b0dba",
            ),
            10095783394965845080,
        )
        proof = self.compilation.manifest["epoch_1_overlap_nonidentity"]
        self.assertEqual(proof["overlap_count"], 60)
        self.assertEqual(len(proof["rows"]), 60)
        self.assertTrue(
            all(
                row["epoch_1_effective_scenario_sha256"]
                != row["epoch_2_effective_scenario_sha256"]
                for row in proof["rows"]
            )
        )

    def test_03_counts_are_nested_prefixes_and_every_authority_is_false(self) -> None:
        by_path = self.compilation.by_path()
        for family, counts in MODULE.EXPECTED_COUNTS.items():
            for seed in MODULE.EXPECTED_SEEDS:
                scenarios = []
                for count in counts:
                    path = Path(
                        f"missions/{family}/seed-{seed:04d}/n-{count}/scenario.json"
                    )
                    scenarios.append(json.loads(by_path[MODULE.PurePosixPath(path)].data))
                largest = scenarios[-1]
                for count, scenario in zip(counts, scenarios):
                    self.assertEqual(scenario["num_robots"], count)
                    expected_ids = {str(index) for index in range(count)}
                    self.assertEqual(set(scenario["waypoints"]), expected_ids)
                    self.assertEqual(
                        scenario["waypoints"],
                        {key: largest["waypoints"][key] for key in expected_ids},
                    )
                    self.assertEqual(
                        scenario["decisions"],
                        {key: largest["decisions"][key] for key in expected_ids},
                    )
        self.assertTrue(
            all(
                value is False
                for value in self.compilation.manifest["classification"].values()
            )
        )

    def test_04_design_mutations_and_public_cli_fail_closed(self) -> None:
        design = copy.deepcopy(self.compilation.design)
        design["requests"][0]["counts"] = [8, 12]
        with self.assertRaisesRegex(MODULE.TaskMissionError, "count ladder differs"):
            MODULE._validate_design(design)
        design = copy.deepcopy(self.compilation.design)
        design["authority"]["execution_authorized"] = True
        with self.assertRaisesRegex(MODULE.TaskMissionError, "grants authority"):
            MODULE._validate_design(design)
        with self.assertRaisesRegex(MODULE.TaskMissionError, "categorically disabled"):
            MODULE.main()
        completed = subprocess.run(
            [sys.executable, "-I", "-S", "-B", str(SOURCE)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(completed.stdout, b"")
        self.assertIn(b"categorically disabled", completed.stderr)


if __name__ == "__main__":
    unittest.main()
