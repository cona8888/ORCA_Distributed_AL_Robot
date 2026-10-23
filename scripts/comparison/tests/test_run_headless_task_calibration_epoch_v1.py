#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]


def load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


RUNNER = load(
    "_test_task_runner",
    "scripts/comparison/run_headless_task_calibration_epoch_v1.py",
)
FREEZER = load(
    "_test_task_runner_freezer",
    "scripts/comparison/freeze_headless_task_calibration_epoch_v1.py",
)


class TaskRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compilation = FREEZER.compile_epoch(ROOT)
        cls.base = RUNNER._load_base(ROOT)
        cls.schedule = cls.base._parse_schedule(cls.compilation.value)

    def test_01_schedule_adapter_is_exact_and_task_only(self) -> None:
        self.assertEqual(len(self.schedule), 1450)
        self.assertEqual(sum(item.attempt_kind == "warmup" for item in self.schedule), 10)
        self.assertEqual(sum(item.run_class == "digest_fidelity" for item in self.schedule), 1090)
        self.assertEqual(sum(item.run_class == "positions_replay" for item in self.schedule), 360)
        self.assertTrue(all(item.run_class in RUNNER.RUN_CLASSES for item in self.schedule))
        self.assertTrue(all(item.raw["timed"] is False for item in self.schedule))

    def test_02_runtime_attestation_exactly_binds_every_scheduled_product(self) -> None:
        plan = self.base.AuthenticatedPlan(
            ROOT,
            ROOT / "candidate.json",
            self.compilation.data,
            self.compilation.sha256,
            self.compilation.value,
            self.schedule,
            RUNNER.RESULT_ROOT,
        )
        shim, products = self.base._runtime_attestation_executables(plan)
        self.base._validate_descriptor_schedule_bindings(plan, products)
        self.assertEqual(set(products), {
            "reference-timing", "reference-fidelity", "optimized-timing", "optimized-fidelity"
        })
        self.assertEqual(len(shim.expected_cdhash), 20)

    def test_03_live_runner_record_matches_plan_component(self) -> None:
        self.assertEqual(
            RUNNER.build_runner_record(ROOT),
            self.compilation.value["input_records"]["runner"]["content"],
        )

    def test_04_public_cli_rejects_before_plan_or_process_access(self) -> None:
        with mock.patch.object(RUNNER, "_load_base", side_effect=AssertionError("read")) as load_base:
            code = RUNNER.main(
                ["--plan", "bomb.json", "--session", "bomb-session", "--preflight-only"]
            )
        self.assertEqual(code, 1)
        load_base.assert_not_called()


if __name__ == "__main__":
    unittest.main()
