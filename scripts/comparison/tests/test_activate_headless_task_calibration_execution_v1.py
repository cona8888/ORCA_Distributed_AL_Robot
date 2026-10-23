#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/activate_headless_task_calibration_execution_v1.py"
SPEC = importlib.util.spec_from_file_location("_test_task_execution_activation", SOURCE)
assert SPEC is not None and SPEC.loader is not None
ACTIVATE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ACTIVATE
SPEC.loader.exec_module(ACTIVATE)


class TaskExecutionActivationTests(unittest.TestCase):
    def test_authorization_is_exact_task_only_and_binds_live_sources(self) -> None:
        value = ACTIVATE._load_authorization(ROOT)
        self.assertFalse(value["timing_execution_authorized"])
        self.assertFalse(value["validation_execution_authorized"])
        self.assertFalse(value["benchmark_execution_authorized"])
        self.assertEqual(value["expected_process_count"], 1450)
        for key, path in (
            ("activation", SOURCE),
            ("runner", ROOT / ACTIVATE.RUNNER_PATH),
            ("analyzer", ROOT / ACTIVATE.ANALYZER_IDENTITY["path"]),
        ):
            raw = path.read_bytes()
            self.assertEqual(value[key]["bytes"], len(raw))
            self.assertEqual(value[key]["sha256"], ACTIVATE._sha(raw))

    def test_exactly_one_task_schedule_call_and_no_timing_slots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            for relative in (ACTIVATE.AUTH_PATH, ACTIVATE.ANALYZER_IDENTITY["path"]):
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, destination)
            (root / "results/simulator-demo/v1/headless-scientific").mkdir(parents=True)
            schedule = tuple(
                types.SimpleNamespace(run_class="digest_fidelity", raw={"timed": False})
                for _ in range(ACTIVATE.EXPECTED_PROCESSES)
            )
            authenticated = types.SimpleNamespace(
                plan_sha256=ACTIVATE.PLAN["sha256"], schedule=schedule
            )
            runner = mock.Mock()
            runner.RUN_CLASSES = ("digest_fidelity", "positions_replay")
            runner.load_authenticated_plan.return_value = authenticated
            runner.execute_custodied_schedule.return_value = {
                "aborted": False,
                "attempt_count": ACTIVATE.EXPECTED_PROCESSES,
            }
            with (
                mock.patch.object(ACTIVATE, "_require_clean_committed"),
                mock.patch.object(ACTIVATE, "_load_runner", return_value=runner),
            ):
                receipt = ACTIVATE.execute_one_use_session(root)
            runner.load_authenticated_plan.assert_called_once()
            runner.execute_custodied_schedule.assert_called_once()
            self.assertEqual(receipt["observed_attempt_count"], 1450)
            self.assertTrue(all(value is False for value in receipt["classification"].values()))


if __name__ == "__main__":
    unittest.main()
