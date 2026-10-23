from __future__ import annotations

import importlib.util
import json
import pathlib
import sys
import tempfile
import unittest


REPO = pathlib.Path(__file__).resolve().parents[3]
MODULE_PATH = REPO / "scripts" / "replication" / "prepare_multimap_inputs.py"
SPEC = importlib.util.spec_from_file_location("prepare_multimap_inputs_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
M = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)


class MultiMapInputTests(unittest.TestCase):
    def test_frozen_inventory(self) -> None:
        specs = M.map_specs()
        self.assertEqual(len(specs), 41)
        self.assertEqual(sum(len(spec["mission_labels"]) for spec in specs), 100)
        expected_scenes = sum(
            len(spec["mission_labels"]) * len(M.COUNTS[spec["family"]])
            for spec in specs
        )
        self.assertEqual(expected_scenes, 200)
        self.assertEqual(len(set(M.MAP_SEEDS)), 10)

    def test_mission_seed_binds_map_and_not_count(self) -> None:
        arguments = ("fractal_low", "map-01", "a" * 64, 31)
        first = M.mission_generator_seed(*arguments)
        second = M.mission_generator_seed(*arguments)
        different_map = M.mission_generator_seed(
            "fractal_low", "map-02", "a" * 64, 31
        )
        self.assertEqual(first, second)
        self.assertNotEqual(first, different_map)
        self.assertTrue(0 <= first < 2**64)

    def test_full_tree_is_reproducible_and_nested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            left = root / "left"
            right = root / "right"
            first = M.compile_inputs(left)
            second = M.compile_inputs(right)
            self.assertEqual(first, second)
            self.assertEqual(
                (left / "manifest.json").read_bytes(),
                (right / "manifest.json").read_bytes(),
            )
            checked = M.load_and_validate(left)
            self.assertEqual(len(checked["scenes"]), 200)
            self.assertEqual(len(checked["maps"]), 41)
            maze = [
                scene
                for scene in checked["scenes"]
                if scene["family"] == "perfect_maze"
                and scene["map_id"] == "map-01"
                and scene["mission_id"] == "mission-31"
            ]
            self.assertEqual([item["robot_count"] for item in maze], [2, 4, 6])
            scenarios = [
                json.loads((left / item["scenario_relpath"]).read_bytes())
                for item in maze
            ]
            for smaller, larger in zip(scenarios, scenarios[1:]):
                for field in ("decisions", "waypoints"):
                    self.assertEqual(
                        smaller[field],
                        {key: larger[field][key] for key in smaller[field]},
                    )

    def test_create_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "inputs"
            root.mkdir()
            with self.assertRaises(M.InputError):
                M.compile_inputs(root)


if __name__ == "__main__":
    unittest.main()
