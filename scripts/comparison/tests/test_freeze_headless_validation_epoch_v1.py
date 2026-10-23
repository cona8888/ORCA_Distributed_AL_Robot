#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/freeze_headless_validation_epoch_v1.py"
SPEC = importlib.util.spec_from_file_location("_test_validation_freezer", SOURCE)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ValidationFreezerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.compilation = MODULE.compile_epoch(ROOT)

    def test_exact_denominators_and_authority(self) -> None:
        value = self.compilation.value
        self.assertEqual(len(value["scenes"]), 200)
        self.assertEqual(len(self.compilation.files), 513)
        self.assertEqual(sum(
            row["measured_process_count"] for row in value["schedule"]["inventories"].values()
        ) + value["schedule"]["warmups"]["process_count"], 2810)
        self.assertTrue(all(item is False for item in value["classification"].values()))

    def test_pairing_and_nested_clusters(self) -> None:
        scenes = self.compilation.value["scenes"]
        self.assertEqual(len({scene["input_id"] for scene in scenes}), 200)
        self.assertEqual(len({(scene["family"], scene["sampling_unit"]["clustering_unit_id"]) for scene in scenes}), 100)
        for inventory in self.compilation.value["schedule"]["inventories"].values():
            pairs = {}
            for slot in inventory["measured"]:
                key = (slot["scene_id"], slot["repetition"])
                pairs.setdefault(key, set()).add((slot["method"], slot["input_id"]))
            self.assertTrue(all(len(row) == 2 and len({item[1] for item in row}) == 1 for row in pairs.values()))

    def test_three_class_component_records_are_exact(self) -> None:
        records = self.compilation.value["input_records"]
        self.assertEqual(records["runner"]["content"]["contract"]["run_classes"], list(MODULE.RUN_CLASSES))
        self.assertEqual(records["analyzer"]["content"]["contract"]["run_classes"], list(MODULE.RUN_CLASSES))

    def test_plan_is_deterministic(self) -> None:
        second = MODULE.compile_epoch(ROOT)
        self.assertEqual(self.compilation.data, second.data)
        self.assertEqual(
            [(item.relative_path, item.data) for item in self.compilation.files],
            [(item.relative_path, item.data) for item in second.files],
        )

    def test_publication_is_disabled(self) -> None:
        with self.assertRaisesRegex(MODULE.ValidationFreezeError, "disabled"):
            MODULE.main()


if __name__ == "__main__":
    unittest.main()
