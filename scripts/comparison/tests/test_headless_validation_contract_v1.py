#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RUNNER = load("_test_validation_runner", "scripts/comparison/run_headless_validation_epoch_v1.py")
ANALYZER = load("_test_validation_analyzer", "scripts/comparison/analyze_headless_validation_epoch_v1.py")


class ValidationContractTests(unittest.TestCase):
    def test_runner_record_is_exact_and_nonauthorizing(self) -> None:
        record = RUNNER.build_runner_record(ROOT)
        self.assertEqual(record["contract"]["run_classes"], list(RUNNER.RUN_CLASSES))
        self.assertTrue(all(value is False for value in record["classification"].values()))
        self.assertEqual(record["identity"]["path"], RUNNER.SOURCE_PATH.as_posix())

    def test_analyzer_record_is_exact_and_nonauthorizing(self) -> None:
        record = ANALYZER.build_analyzer_record(ROOT)
        self.assertEqual(record["contract"]["bootstrap_replicates"], 100000)
        self.assertTrue(all(value is False for value in record["classification"].values()))

    def test_public_guards_do_not_load_a_plan(self) -> None:
        runner_core = RUNNER._core
        analyzer_builder = ANALYZER.build_analyzer_record
        RUNNER._core = lambda *_args, **_kwargs: self.fail("runner loaded core")
        ANALYZER.build_analyzer_record = lambda *_args, **_kwargs: self.fail("analyzer read source")
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(RUNNER.main([]), 1)
                self.assertEqual(ANALYZER.main([]), 1)
        finally:
            RUNNER._core = runner_core
            ANALYZER.build_analyzer_record = analyzer_builder

    def test_closed_schedule_denominator(self) -> None:
        slot = {"placeholder": True}
        plan = {"schedule": {
            "assignment_algorithm": "prospective",
            "balance": [],
            "execution_order": ["warmups", *RUNNER.RUN_CLASSES],
            "inventories": {
                name: {
                    "measured": [slot] * RUNNER.EXPECTED[name],
                    "measured_process_count": RUNNER.EXPECTED[name],
                    "paired_scene_repetition_count": RUNNER.EXPECTED[name] // 2,
                    "run_class": name,
                    "timed": name == "headless_timing",
                }
                for name in RUNNER.RUN_CLASSES
            },
            "timing_gate": {
                "all_prior_digest_and_positions_required": True,
                "on_prior_failure": "do-not-start-timing",
                "timing_inventory_present": True,
            },
            "warmups": {"process_count": 10, "slots": [slot] * 10, "uniqueness_key": []},
        }}
        self.assertEqual(len(RUNNER._flatten_schedule(plan)), 2810)


if __name__ == "__main__":
    unittest.main()
