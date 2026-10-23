#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/generate_headless_task_calibration_missions_v2.py"
SPEC = importlib.util.spec_from_file_location("_test_epoch3_task_missions", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class Epoch3TaskMissionTests(unittest.TestCase):
    def test_compilation_is_exact_fresh_domain_and_deterministic(self) -> None:
        first = MODULE.compile_task_missions(ROOT)
        second = MODULE.compile_task_missions(ROOT)
        self.assertEqual(first.manifest_bytes, second.manifest_bytes)
        self.assertEqual(first.manifest_sha256, second.manifest_sha256)
        self.assertEqual(first.manifest["inventory"], {
            "bank_count": 10,
            "file_count_including_manifest": 32,
            "scene_count": 20,
        })
        self.assertEqual(first.manifest["epoch_1_overlap_nonidentity"]["overlap_count"], 0)
        self.assertEqual(len(first.files), 32)
        scenarios = [item.sha256 for item in first.files if item.kind == "scenario"]
        self.assertEqual(len(scenarios), 20)
        self.assertEqual(len(set(scenarios)), 20)

    def test_publication_is_disabled(self) -> None:
        with self.assertRaisesRegex(MODULE.Epoch3MissionError, "disabled"):
            MODULE.main()


if __name__ == "__main__":
    unittest.main()
