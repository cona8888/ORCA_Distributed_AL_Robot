from __future__ import annotations

import importlib.util
import json
import math
import pathlib
import sys
import tempfile
import unittest


REPO = pathlib.Path(__file__).resolve().parents[3]
MODULE_PATH = REPO / "scripts" / "replication" / "prepare_orca_inputs.py"
SPEC = importlib.util.spec_from_file_location("prepare_orca_inputs_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
M = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = M
SPEC.loader.exec_module(M)


class OrcaInputTests(unittest.TestCase):
    def test_frozen_inventory_and_schedule(self) -> None:
        scenes = []
        for label in M.OPEN_MISSIONS:
            scenes.append(
                {
                    "scene_id": f"open-{label}",
                    "scene_set": "open",
                    "robot_count": 8,
                    "duration_seconds": M.OPEN_STEPS * 0.1,
                    "common_sample_count": M.OPEN_STEPS + 1,
                    "native_sample_stride": 1,
                    "resolved_parameters": M.resolved_parameters(
                        8, M.OPEN_STEPS, 0.1, 1, M.runtime_seed(label)
                    ),
                    "steps": M.OPEN_STEPS,
                    "timestep_seconds": M.MAIN_TIMESTEP_SECONDS,
                    "timeout_seconds": M.PROCESS_TIMEOUT_SECONDS,
                }
            )
        for count in M.CIRCLE_COUNTS:
            scenes.append(
                {
                    "scene_id": f"circle-{count}",
                    "scene_set": "circle",
                    "robot_count": count,
                    "duration_seconds": M.CIRCLE_STEPS * 0.1,
                    "common_sample_count": M.CIRCLE_STEPS + 1,
                    "native_sample_stride": 1,
                    "resolved_parameters": M.resolved_parameters(
                        count, M.CIRCLE_STEPS, 0.1, 1, M.runtime_seed(None)
                    ),
                    "steps": M.CIRCLE_STEPS,
                    "timestep_seconds": M.MAIN_TIMESTEP_SECONDS,
                    "timeout_seconds": M.PROCESS_TIMEOUT_SECONDS,
                }
            )
        schedule = M.schedule_records(scenes)
        self.assertEqual(len(schedule), 363)
        self.assertEqual(sum(row["phase"] == "warmup" for row in schedule), 12)
        self.assertEqual(sum(row["phase"] == "measured" for row in schedule), 345)
        self.assertEqual(sum(row["phase"] == "sensitivity" for row in schedule), 6)
        self.assertEqual([row["sequence"] for row in schedule], list(range(363)))
        class_counts = {
            "warmup": sum(row["phase"] == "warmup" for row in schedule),
            "digest": sum(
                row["phase"] == "measured"
                and row["run_class"] == "digest_fidelity"
                for row in schedule
            ),
            "positions": sum(
                row["phase"] == "measured"
                and row["run_class"] == "positions_fidelity"
                for row in schedule
            ),
            "timing": sum(
                row["phase"] == "measured" and row["run_class"] == "timing"
                for row in schedule
            ),
            "sensitivity": sum(row["phase"] == "sensitivity" for row in schedule),
        }
        self.assertEqual(
            class_counts,
            {"warmup": 12, "digest": 46, "positions": 92, "timing": 207, "sensitivity": 6},
        )
        self.assertEqual(
            {
                method: sum(row["method"] == method for row in schedule)
                for method in M.METHODS
            },
            {"reference": 119, "optimized": 119, "orca": 125},
        )
        warmups = [row for row in schedule if row["phase"] == "warmup"]
        for cohort_id in {row["cohort_id"] for row in warmups}:
            cohort = [row for row in warmups if row["cohort_id"] == cohort_id]
            self.assertEqual({row["method"] for row in cohort}, set(M.METHODS))
            self.assertEqual(
                sorted(row["order_position"] for row in cohort), [0, 1, 2]
            )
        for method in M.METHODS:
            positions = [
                row["order_position"]
                for row in warmups
                if row["method"] == method
            ]
            self.assertEqual(len(positions), 4)
            self.assertEqual(
                sorted(positions.count(position) for position in range(3)),
                [1, 1, 2],
            )
        self.assertEqual(
            {
                (row["scene_set"], row["robot_count"])
                for row in schedule[:12]
            },
            {("open", 8), ("circle", 8), ("circle", 20), ("circle", 50)},
        )
        for stratum in {("open", 8), ("circle", 8), ("circle", 20), ("circle", 50)}:
            self.assertEqual(
                {
                    row["method"]
                    for row in schedule[:12]
                    if (row["scene_set"], row["robot_count"]) == stratum
                },
                set(M.METHODS),
            )
        logical = [
            (
                row["phase"],
                row["scene_id"],
                row["run_class"],
                row["method"],
                row["repetition"],
                row["cohort_id"],
            )
            for row in schedule
        ]
        self.assertEqual(len(logical), len(set(logical)))
        for row in schedule:
            stride = 2 if row["cadence"] == "orca-0.05s-sensitivity" else 1
            self.assertEqual(row["native_sample_stride"], stride)
            self.assertEqual(row["common_sample_count"], row["steps"] // stride + 1)
            execution = row["resolved_parameters"]["execution"]
            common = row["resolved_parameters"]["common"]
            self.assertEqual(execution["native_steps"], row["steps"])
            self.assertEqual(
                execution["native_timestep_seconds"], row["timestep_seconds"]
            )
            self.assertEqual(execution["native_sample_stride"], stride)
            self.assertEqual(common["common_sample_count"], row["common_sample_count"])
            self.assertEqual(common["common_grid_timestep_seconds"], 0.1)
            self.assertEqual(
                common["physical_duration_seconds"],
                row["steps"] * row["timestep_seconds"],
            )
        for run_class, expected_reference_first in (
            ("digest_fidelity", 12),
            ("positions_fidelity", 11),
        ):
            pairs = [
                row
                for row in schedule
                if row["phase"] == "measured"
                and row["run_class"] == run_class
            ]
            self.assertEqual(
                sum(
                    row["method"] == "reference"
                    and row["order_position"] == 0
                    for row in pairs
                ),
                expected_reference_first,
            )
        scene_ids = {row["scene_id"] for row in schedule if row["phase"] == "measured"}
        for scene_id in scene_ids:
            main = [
                row
                for row in schedule
                if row["scene_id"] == scene_id and row["phase"] == "measured"
            ]
            self.assertEqual(len(main), 15)
            orca_positions = [
                row
                for row in main
                if row["method"] == "orca"
                and row["run_class"] == "positions_fidelity"
            ]
            self.assertEqual([row["repetition"] for row in orca_positions], [1, 2])
            self.assertEqual(
                [row["sequence"] for row in orca_positions],
                list(
                    range(
                        orca_positions[0]["sequence"],
                        orca_positions[0]["sequence"] + 2,
                    )
                ),
            )
            timing = [
                row
                for row in schedule
                if row["scene_id"] == scene_id
                and row["phase"] == "measured"
                and row["run_class"] == "timing"
            ]
            for method in M.METHODS:
                self.assertEqual(
                    sorted(
                        row["order_position"]
                        for row in timing
                        if row["method"] == method
                    ),
                    [0, 1, 2],
                )
        sensitivity = [row for row in schedule if row["phase"] == "sensitivity"]
        self.assertEqual(
            {row["robot_count"] for row in sensitivity}, set(M.CIRCLE_COUNTS)
        )
        for count in M.CIRCLE_COUNTS:
            rows = [row for row in sensitivity if row["robot_count"] == count]
            self.assertEqual([row["repetition"] for row in rows], [1, 2])
            self.assertTrue(
                all(
                    row["steps"] == 1200
                    and row["timestep_seconds"] == 0.05
                    and row["duration_seconds"] == 60.0
                    for row in rows
                )
            )

    def test_circle_geometry_is_explicit_and_safe(self) -> None:
        for count in M.CIRCLE_COUNTS:
            scenario = M.circle_scenario(count)
            self.assertEqual(scenario["num_robots"], count)
            points = [scenario["waypoints"][str(i)][0] for i in range(count)]
            goals = [scenario["waypoints"][str(i)][1] for i in range(count)]
            for point, goal in zip(points, goals):
                self.assertAlmostEqual(math.hypot(*point), 20.0, places=7)
                self.assertEqual(goal, [-point[0], -point[1]])
            spacing = min(
                math.dist(points[i], points[(i + 1) % count])
                for i in range(count)
            )
            self.assertGreater(spacing, 2.0)

    def test_full_tree_is_reproducible_and_obstacle_free(self) -> None:
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
            self.assertEqual(len(checked["scenes"]), 23)
            self.assertEqual(len(checked["files"]), 48)
            self.assertEqual(
                checked["protocol_amendment_2"]["path"],
                M.PROTOCOL_AMENDMENT_2_RELATIVE.as_posix(),
            )
            self.assertEqual(sum(path.is_file() for path in left.rglob("*")), 50)
            schedule = json.loads((left / "schedule.json").read_bytes())
            self.assertEqual(len(schedule["attempts"]), 363)
            for scene in checked["scenes"]:
                self.assertEqual(
                    scene["resolved_parameters"]["orca"]["max_neighbors"],
                    scene["robot_count"] - 1,
                )
                self.assertTrue(
                    scene["resolved_parameters"]["common"]["retain_agents_after_goal"]
                )
                config = (left / scene["config_relpath"]).read_text()
                self.assertNotIn("OBSTACLE_FILE", config)
                self.assertNotIn("ASSETS_DIR", config)
                self.assertEqual(config.splitlines().count("WORLD_SZ: 400"), 1)
                self.assertEqual(config.splitlines().count("ROBOT_MODEL: sphere"), 1)
                top_level_keys = [
                    line.partition(":")[0]
                    for line in config.splitlines()
                    if line and not line[0].isspace()
                ]
                self.assertEqual(len(top_level_keys), len(set(top_level_keys)))
                if scene["scene_set"] == "open":
                    source = REPO / scene["source_scenario"]["path"]
                    self.assertEqual(
                        (left / scene["scenario_relpath"]).read_bytes(),
                        source.read_bytes(),
                    )

    def test_crosswalk_freezes_native_model_difference(self) -> None:
        crosswalk = M.parameter_crosswalk()
        self.assertEqual(
            crosswalk["methods"]["orca"]["motion_model"],
            "native RVO2 holonomic disc",
        )
        self.assertIn(
            "unicycle projection", crosswalk["adaptation_limits"]["forbidden"]
        )
        self.assertFalse(crosswalk["common"]["obstacles_present"])
        self.assertEqual(
            crosswalk["methods"]["reference"]["world_size_deletion_guard_m"],
            400.0,
        )
        self.assertNotIn("communication_failure_rate", crosswalk["common"])
        self.assertNotIn("neighbor_distance_m", crosswalk["common"])
        self.assertEqual(
            crosswalk["input_authority"]["scenario_json"],
            "num_robots and ordered numeric-ID waypoints only",
        )
        self.assertIn(
            "permanent goal latch",
            crosswalk["adaptation_limits"]["allowed"][1],
        )

    def test_create_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp) / "inputs"
            root.mkdir()
            with self.assertRaises(M.InputError):
                M.compile_inputs(root)


if __name__ == "__main__":
    unittest.main()
