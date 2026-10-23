#!/usr/bin/env python3

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]


def load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FREEZER = load(
    "_test_task_freezer",
    "scripts/comparison/freeze_headless_task_calibration_epoch_v1.py",
)


class TaskFreezerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compilation = FREEZER.compile_epoch(ROOT)

    def test_01_exact_closed_inventory(self) -> None:
        value = FREEZER.validate_epoch_bytes(self.compilation.data)
        self.assertEqual(value, self.compilation.value)
        self.assertEqual(len(value["scenes"]), 180)
        self.assertEqual(len(value["config_artifacts"]), 180)
        self.assertEqual(len(self.compilation.files), 423)
        self.assertEqual(value["schedule"]["warmups"]["process_count"], 10)
        self.assertEqual(
            {
                key: item["measured_process_count"]
                for key, item in value["schedule"]["inventories"].items()
            },
            {"digest_fidelity": 1080, "positions_replay": 360},
        )

    def test_02_timing_and_all_claim_authority_are_locked(self) -> None:
        value = self.compilation.value
        self.assertFalse(value["classification"]["execution_authorized"])
        self.assertFalse(value["classification"]["timing_execution_authorized"])
        self.assertEqual(set(value["method_bindings"]["reference"]), set(FREEZER.RUN_CLASSES))
        self.assertEqual(value["schedule"]["execution_order"], ["warmups", *FREEZER.RUN_CLASSES])
        self.assertNotIn("headless_timing", value["schedule"]["inventories"])
        self.assertTrue(all(slot["timed"] is False for slot in value["schedule"]["warmups"]["slots"]))
        self.assertTrue(
            all(
                slot["timed"] is False
                for inventory in value["schedule"]["inventories"].values()
                for slot in inventory["measured"]
            )
        )

    def test_03_every_plan_artifact_is_unique_and_hash_bound(self) -> None:
        paths = [item.relative_path for item in self.compilation.files]
        self.assertEqual(len(paths), len(set(paths)))
        for item in self.compilation.files:
            self.assertEqual(item.sha256, FREEZER._sha(item.data))
        receipt = self.compilation.value["input_records"]["mission_receipt"]
        receipt_bytes = FREEZER.canonical_json_bytes(receipt["content"])
        self.assertEqual(receipt["bytes"], len(receipt_bytes))
        self.assertEqual(receipt["sha256"], FREEZER._sha(receipt_bytes))
        mission = self.compilation.by_path()[Path("manifest.json")]
        self.assertEqual(json.loads(mission.data), receipt["content"])

    def test_04_mutations_fail_closed(self) -> None:
        for mutation, message in (
            (("classification", "timing_execution_authorized", True), "registration state differs"),
            (("schedule", "timing_gate", {}), "execution order differs"),
        ):
            value = copy.deepcopy(self.compilation.value)
            value[mutation[0]][mutation[1]] = mutation[2]
            with self.assertRaisesRegex(FREEZER.EpochFreezeError, message):
                FREEZER.validate_epoch_bytes(FREEZER.canonical_json_bytes(value))
        value = copy.deepcopy(self.compilation.value)
        value["config_artifacts"][0]["sha256"] = "0" * 64
        with self.assertRaisesRegex(FREEZER.EpochFreezeError, "config artifact differs"):
            FREEZER.validate_epoch_bytes(FREEZER.canonical_json_bytes(value))
        with self.assertRaisesRegex(FREEZER.EpochFreezeError, "categorically disabled"):
            FREEZER.main()


if __name__ == "__main__":
    unittest.main()
