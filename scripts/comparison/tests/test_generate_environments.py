from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATOR_PATH = REPO_ROOT / "scripts" / "comparison" / "generate_environments.py"
MATERIALIZER_PATH = (
    REPO_ROOT
    / "scripts"
    / "comparison"
    / "materialize_capacity_scenario.py"
)
FROZEN_ROOT = REPO_ROOT / "comparison" / "environments"
PRESERVED_SMOKE_SCENARIO_SHA256 = {
    ("open", "calibration"): (
        "afc78d9f53966a3f1f6d142ac3eb8465bb8c43c8e0ab82430e181841138316fe"
    ),
    ("open", "validation"): (
        "90292276bacc11d47a18a72cc8c226251602f88bbf2e201c7eceb57423922113"
    ),
    ("perfect_maze", "calibration"): (
        "0139bad197f5d71f9e6460ea44d24a291834820362785e7f536b059fa2c53c56"
    ),
    ("perfect_maze", "validation"): (
        "c637c578d0d6cf4cef00628df2056e0b20875e1294927be7bdac21c3866423b2"
    ),
    ("fractal_low", "calibration"): (
        "59bf9ddf0adb9ba1737bb70d34a4b634dd0a41bfa0fa06230437dea69d5cd356"
    ),
    ("fractal_low", "validation"): (
        "a912db646bef3ef812bae42ad34e1d24220d5206019cbdde07f9b68765f81052"
    ),
    ("fractal_medium", "calibration"): (
        "c09dc5d06c91eee596e88c3d0cd3cf5dbed03f850a87d4ff14686f4d1875a3c1"
    ),
    ("fractal_medium", "validation"): (
        "e26c49db94aa2a22c8efa47f3b73e9c92f2f1af6c2012f9dffb399de7e61d66b"
    ),
    ("fractal_high", "calibration"): (
        "e47cc792791e6e82be4e13f8317a1cf5535d0d0d254b961aea6c03781d8a1c10"
    ),
    ("fractal_high", "validation"): (
        "e50c49fa37dd64b314092313f93bcfb2b2475c972239c45fd7586a76c4545264"
    ),
}

SPEC = importlib.util.spec_from_file_location(
    "generate_environments", GENERATOR_PATH
)
assert SPEC and SPEC.loader
GENERATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GENERATOR)


class FrozenEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest_bytes = (FROZEN_ROOT / "manifest.json").read_bytes()
        cls.manifest = json.loads(cls.manifest_bytes)

    def test_manifest_and_artifact_hashes(self) -> None:
        expected_manifest_hash = (FROZEN_ROOT / "manifest.sha256").read_text(
            encoding="ascii"
        ).split()[0]
        self.assertEqual(
            hashlib.sha256(self.manifest_bytes).hexdigest(),
            expected_manifest_hash,
        )
        self.assertEqual(
            hashlib.sha256(GENERATOR_PATH.read_bytes()).hexdigest(),
            self.manifest["generator"]["sha256"],
        )
        for case in self.manifest["environments"]:
            for key in ("map", "scenario"):
                artifact = case[key]
                data = (FROZEN_ROOT / artifact["path"]).read_bytes()
                self.assertEqual(len(data), artifact["bytes"])
                self.assertEqual(hashlib.sha256(data).hexdigest(), artifact["sha256"])
            bank = case["capacity"]["bank"]
            data = (FROZEN_ROOT / bank["path"]).read_bytes()
            self.assertEqual(len(data), bank["bytes"])
            self.assertEqual(
                hashlib.sha256(data).hexdigest(), bank["sha256"]
            )
        materializer = self.manifest["capacity_suite"][
            "prefix_materializer"
        ]
        self.assertEqual(
            hashlib.sha256(MATERIALIZER_PATH.read_bytes()).hexdigest(),
            materializer["sha256"],
        )

    def test_frozen_tree_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            generated_root = Path(tmp) / "environments"
            GENERATOR.generate_environment_tree(generated_root)
            expected = GENERATOR._tree_bytes(FROZEN_ROOT)
            actual = GENERATOR._tree_bytes(generated_root)
        self.assertEqual(actual, expected)

    def test_binary_pngs_and_recomputed_map_metrics(self) -> None:
        for case in self.manifest["environments"]:
            size, pixels = GENERATOR.read_grayscale_png(
                FROZEN_ROOT / case["map"]["path"]
            )
            self.assertEqual(size, GENERATOR.MAP_SIZE)
            self.assertEqual(set(pixels), {0, 255})
            self.assertEqual(
                GENERATOR.measure_map(pixels, size), case["map_metrics"]
            )
            free_space = case["map_metrics"]["free_space"]
            self.assertEqual(free_space["component_count_4_connected"], 1)
            self.assertEqual(free_space["reachable_fraction"], 1.0)

    def test_complete_held_out_family_matrix(self) -> None:
        cases = {
            (case["family"], case["split"])
            for case in self.manifest["environments"]
        }
        self.assertEqual(
            cases,
            {
                (family, split)
                for family in GENERATOR.FAMILIES
                for split in GENERATOR.SPLITS
            },
        )
        self.assertEqual(len(self.manifest["environments"]), 10)
        map_seeds = [case["map_seed"] for case in self.manifest["environments"]]
        mission_seeds = [
            case["mission_seed"] for case in self.manifest["environments"]
        ]
        self.assertEqual(len(map_seeds), len(set(map_seeds)))
        self.assertEqual(len(mission_seeds), len(set(mission_seeds)))

    def test_effective_runtime_1000_gate(self) -> None:
        for case in self.manifest["environments"]:
            source_size, source_pixels = GENERATOR.read_grayscale_png(
                FROZEN_ROOT / case["map"]["path"]
            )
            runtime = case["effective_runtime_1000"]
            self.assertEqual(
                runtime["source_resolution"],
                {"height_px": source_size, "width_px": source_size},
            )
            self.assertEqual(
                runtime["resolution"],
                {
                    "height_px": GENERATOR.RUNTIME_MAP_SIZE,
                    "width_px": GENERATOR.RUNTIME_MAP_SIZE,
                },
            )
            self.assertEqual(
                runtime["resize_rule"], GENERATOR.RUNTIME_RESIZE_RULE
            )
            self.assertEqual(
                runtime["raw_occupancy_encoding"],
                GENERATOR.RAW_OCCUPANCY_ENCODING,
            )

            expanded = GENERATOR.expand_nearest_neighbour(
                source_pixels, source_size, GENERATOR.RUNTIME_MAP_SIZE
            )
            self.assertEqual(
                len(expanded), runtime["raw_occupancy_bytes"]
            )
            self.assertEqual(
                hashlib.sha256(expanded).hexdigest(),
                runtime["raw_occupancy_sha256"],
            )
            self.assertEqual(
                GENERATOR.measure_map(
                    expanded, GENERATOR.RUNTIME_MAP_SIZE
                ),
                runtime["map_metrics"],
            )

    def test_nearest_neighbour_expansion_uses_floor_indices(self) -> None:
        source_size = 4
        target_size = 7
        source = bytearray(range(source_size * source_size))
        expected = bytearray(
            source[
                ((y * source_size) // target_size) * source_size
                + ((x * source_size) // target_size)
            ]
            for y in range(target_size)
            for x in range(target_size)
        )
        self.assertEqual(
            GENERATOR.expand_nearest_neighbour(
                source, source_size, target_size
            ),
            expected,
        )

    def test_dimensions_are_measured_descriptors(self) -> None:
        self.assertIn(
            "not a theoretical fractal-dimension guarantee",
            self.manifest["boundary_dimension_policy"],
        )
        by_split = {
            split: {
                case["family"]: case["map_metrics"]["boundary_box_counting"]
                for case in self.manifest["environments"]
                if case["split"] == split
            }
            for split in GENERATOR.SPLITS
        }
        for descriptors in by_split.values():
            for descriptor in descriptors.values():
                self.assertEqual(
                    descriptor["descriptor_note"], GENERATOR.DESCRIPTOR_NOTE
                )
                self.assertIsInstance(descriptor["dimension"], float)
                self.assertGreater(descriptor["r_squared"], 0.9)
            self.assertLess(
                descriptors["fractal_low"]["dimension"],
                descriptors["fractal_medium"]["dimension"],
            )
            self.assertLess(
                descriptors["fractal_medium"]["dimension"],
                descriptors["fractal_high"]["dimension"],
            )

    def test_maze_is_a_connected_tree_and_missions_are_reachable(self) -> None:
        for case in self.manifest["environments"]:
            size, pixels = GENERATOR.read_grayscale_png(
                FROZEN_ROOT / case["map"]["path"]
            )
            navigable = GENERATOR.clearance_mask(
                pixels, size, GENERATOR.CLEARANCE_RADIUS_PX
            )
            if case["family"] == "perfect_maze":
                topology = case["generation"]
                self.assertTrue(topology["perfect_tree"])
                self.assertEqual(
                    topology["passage_edges"],
                    topology["connected_cells"] - 1,
                )
            mission = case["scenario_metrics"]["mission_reachability"]
            self.assertEqual(mission["fraction"], 1.0)
            self.assertEqual(mission["reachable_legs"], mission["total_legs"])
            scenario = json.loads(
                (FROZEN_ROOT / case["scenario"]["path"]).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                len(scenario["waypoints"]), case["scenario_metrics"]["robot_count"]
            )
            self.assertEqual(
                scenario["metadata"]["map_sha256"], case["map"]["sha256"]
            )
            for route in scenario["waypoints"].values():
                pixel_route = []
                for x_world, y_world in route:
                    x = int((x_world / GENERATOR.WORLD_SIZE_M + 0.5) * size)
                    y = int((y_world / GENERATOR.WORLD_SIZE_M + 0.5) * size)
                    point = y * size + x
                    self.assertEqual(navigable[point], 255)
                    pixel_route.append(point)
                for left, right in zip(pixel_route, pixel_route[1:]):
                    self.assertTrue(
                        GENERATOR._line_is_free(
                            navigable, size, left, right
                        )
                    )

    def test_capacity_exact_n_and_nested_prefixes(self) -> None:
        for case in self.manifest["environments"]:
            previous = None
            previous_count = 0
            records = case["capacity"]["prefix_scenarios"]
            bank = json.loads(
                (
                    FROZEN_ROOT / case["capacity"]["bank"]["path"]
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                [record["robot_count"] for record in records],
                case["capacity"]["supported_counts"],
            )
            self.assertEqual(
                records[-1]["robot_count"],
                case["capacity"]["family_frozen_max_robot_count"],
            )
            for record in records:
                robot_count = record["robot_count"]
                scenario = GENERATOR.capacity_scenario_prefix(
                    bank, robot_count
                )
                scenario_data = GENERATOR.canonical_json_bytes(scenario)
                self.assertEqual(len(scenario_data), record["bytes"])
                self.assertEqual(
                    hashlib.sha256(scenario_data).hexdigest(),
                    record["sha256"],
                )
                GENERATOR.validate_capacity_scenario(
                    scenario, robot_count
                )
                self.assertEqual(
                    scenario["metadata"]["capacity_bank_id"],
                    case["capacity"]["bank_id"],
                )
                self.assertEqual(
                    scenario["metadata"]["map_sha256"],
                    case["map"]["sha256"],
                )
                self.assertEqual(
                    scenario["metadata"]["split"], case["split"]
                )
                if previous is not None:
                    for robot in range(previous_count):
                        robot_id = str(robot)
                        self.assertEqual(
                            scenario["decisions"][robot_id],
                            previous["decisions"][robot_id],
                        )
                        self.assertEqual(
                            scenario["waypoints"][robot_id],
                            previous["waypoints"][robot_id],
                        )
                previous = scenario
                previous_count = robot_count

    def test_capacity_starts_goals_clear_collision_free_and_reachable(
        self,
    ) -> None:
        required_distance_sq = (
            GENERATOR.MIN_INTERROBOT_CENTER_DISTANCE_PX**2
        )
        for case in self.manifest["environments"]:
            size, pixels = GENERATOR.read_grayscale_png(
                FROZEN_ROOT / case["map"]["path"]
            )
            navigable = GENERATOR.clearance_mask(
                pixels, size, GENERATOR.CLEARANCE_RADIUS_PX
            )
            scenario = json.loads(
                (
                    FROZEN_ROOT / case["capacity"]["bank"]["path"]
                ).read_text(encoding="utf-8")
            )
            starts = []
            goals = []
            for route in scenario["waypoints"].values():
                pixel_route = []
                for x_world, y_world in route:
                    x = int(
                        (x_world / GENERATOR.WORLD_SIZE_M + 0.5) * size
                    )
                    y = int(
                        (y_world / GENERATOR.WORLD_SIZE_M + 0.5) * size
                    )
                    point = y * size + x
                    self.assertEqual(navigable[point], 255)
                    pixel_route.append(point)
                starts.append(pixel_route[0])
                goals.append(pixel_route[-1])
                self.assertNotEqual(pixel_route[0], pixel_route[-1])
                for left, right in zip(pixel_route, pixel_route[1:]):
                    self.assertTrue(
                        GENERATOR._line_is_free(
                            navigable, size, left, right
                        )
                    )
            self.assertEqual(len(starts), len(set(starts)))
            self.assertEqual(len(goals), len(set(goals)))
            self.assertGreaterEqual(
                GENERATOR.minimum_pairwise_distance_sq(starts, size),
                required_distance_sq,
            )
            self.assertGreaterEqual(
                GENERATOR.minimum_pairwise_distance_sq(goals, size),
                required_distance_sq,
            )
            metrics = case["capacity"]["packing"]
            self.assertGreaterEqual(
                metrics["minimum_pairwise_center_distance_m"],
                GENERATOR.MIN_INTERROBOT_CENTER_DISTANCE_M,
            )

    def test_capacity_geometry_limits_and_disjoint_mission_pools(self) -> None:
        policy = self.manifest["capacity_suite"]
        self.assertFalse(policy["algorithm_execution_during_generation"])
        self.assertFalse(
            policy["validation_outcomes_generated_or_inspected"]
        )
        self.assertEqual(
            policy["capacity_target_robot_count"],
            GENERATOR.CAPACITY_TARGET_ROBOTS,
        )
        by_family = {
            family: {
                case["split"]: case
                for case in self.manifest["environments"]
                if case["family"] == family
            }
            for family in GENERATOR.FAMILIES
        }
        pool_ids = set()
        for family, splits in by_family.items():
            calibration = splits["calibration"]
            validation = splits["validation"]
            self.assertNotEqual(
                calibration["capacity"]["bank_id"],
                validation["capacity"]["bank_id"],
            )
            self.assertNotEqual(
                calibration["capacity"]["endpoint_pool_sha256"],
                validation["capacity"]["endpoint_pool_sha256"],
            )
            mission_sets = []
            for case in (calibration, validation):
                scenario = json.loads(
                    (
                        FROZEN_ROOT
                        / case["capacity"]["bank"]["path"]
                    ).read_text(encoding="utf-8")
                )
                mission_sets.append(
                    {
                        (tuple(route[0]), tuple(route[-1]))
                        for route in scenario["waypoints"].values()
                    }
                )
            self.assertFalse(mission_sets[0] & mission_sets[1])
            calibration_max = calibration["capacity"][
                "family_frozen_max_robot_count"
            ]
            validation_max = validation["capacity"][
                "family_frozen_max_robot_count"
            ]
            self.assertEqual(calibration_max, validation_max)
            self.assertEqual(
                calibration_max,
                min(
                    calibration["capacity"]["packing"][
                        "constructive_packing_count"
                    ],
                    validation["capacity"]["packing"][
                        "constructive_packing_count"
                    ],
                ),
            )
            for case in splits.values():
                packing = case["capacity"]["packing"]
                self.assertLessEqual(
                    packing["constructive_packing_count"],
                    packing["free_area_disk_upper_bound"],
                )
                pool_ids.add(case["capacity"]["bank_id"])
        self.assertEqual(len(pool_ids), 2 * len(GENERATOR.FAMILIES))
        self.assertLess(
            by_family["open"]["calibration"]["capacity"]["packing"][
                "free_area_disk_upper_bound"
            ],
            GENERATOR.CAPACITY_TARGET_ROBOTS,
        )

    def test_exact_n_validator_fails_closed(self) -> None:
        valid = {
            "decisions": {"0": 1, "1": 2},
            "metadata": {
                "capacity_bank_robot_count": 2,
                "prefix_robot_count": 2,
            },
            "num_robots": 2,
            "seed_robots": [],
            "waypoints": {
                "0": [[0.0, 0.0], [1.0, 1.0]],
                "1": [[2.0, 2.0], [3.0, 3.0]],
            },
        }
        GENERATOR.validate_capacity_scenario(valid, 2)
        defects = []
        wrong_count = json.loads(json.dumps(valid))
        wrong_count["num_robots"] = 1
        defects.append(wrong_count)
        missing_key = json.loads(json.dumps(valid))
        del missing_key["waypoints"]["1"]
        defects.append(missing_key)
        extra_key = json.loads(json.dumps(valid))
        extra_key["decisions"]["2"] = 0
        defects.append(extra_key)
        wrong_metadata = json.loads(json.dumps(valid))
        wrong_metadata["metadata"]["prefix_robot_count"] = 1
        defects.append(wrong_metadata)
        for defect in defects:
            with self.assertRaises(ValueError):
                GENERATOR.validate_capacity_scenario(defect, 2)

    def test_preexisting_smoke_scenarios_are_byte_preserved(self) -> None:
        observed = {}
        for case in self.manifest["environments"]:
            scenario_path = FROZEN_ROOT / case["scenario"]["path"]
            digest = hashlib.sha256(scenario_path.read_bytes()).hexdigest()
            key = (case["family"], case["split"])
            observed[key] = digest
            self.assertEqual(digest, case["scenario"]["sha256"])
        self.assertEqual(observed, PRESERVED_SMOKE_SCENARIO_SHA256)

    def test_capacity_materializer_is_registered_exact_and_exclusive(
        self,
    ) -> None:
        case = next(
            case
            for case in self.manifest["environments"]
            if case["family"] == "open"
            and case["split"] == "calibration"
        )
        registered = {
            record["robot_count"]: record
            for record in case["capacity"]["prefix_scenarios"]
        }
        robot_count = 12
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "scenario.json"
            command = [
                sys.executable,
                str(MATERIALIZER_PATH),
                "--environment-root",
                str(FROZEN_ROOT),
                "--family",
                "open",
                "--split",
                "calibration",
                "--count",
                str(robot_count),
                "--output",
                str(output),
            ]
            first = subprocess.run(
                command,
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            data_before = output.read_bytes()
            self.assertEqual(len(data_before), registered[robot_count]["bytes"])
            self.assertEqual(
                hashlib.sha256(data_before).hexdigest(),
                registered[robot_count]["sha256"],
            )
            self.assertEqual(output.stat().st_mode & 0o222, 0)

            second = subprocess.run(
                command,
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("refusing to overwrite", second.stderr)
            self.assertEqual(output.read_bytes(), data_before)

            unregistered = Path(tmp) / "unregistered.json"
            invalid = subprocess.run(
                [
                    *command[:-3],
                    "13",
                    "--output",
                    str(unregistered),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(invalid.returncode, 0)
            self.assertIn("is not registered", invalid.stderr)
            self.assertFalse(unregistered.exists())

            above_ceiling = Path(tmp) / "above-ceiling.json"
            excessive = subprocess.run(
                [
                    *command[:-3],
                    str(
                        case["capacity"][
                            "family_frozen_max_robot_count"
                        ]
                        + 1
                    ),
                    "--output",
                    str(above_ceiling),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(excessive.returncode, 0)
            self.assertIn("exceeds frozen open ceiling", excessive.stderr)
            self.assertFalse(above_ceiling.exists())

    def test_check_only_cli_accepts_frozen_tree_and_rejects_tampering(
        self,
    ) -> None:
        accepted = subprocess.run(
            [
                sys.executable,
                str(GENERATOR_PATH),
                "--output",
                str(FROZEN_ROOT),
                "--check",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "environments"
            shutil.copytree(FROZEN_ROOT, candidate)
            target = candidate / "open" / "calibration" / "scenario.json"
            target.write_bytes(target.read_bytes() + b" ")
            rejected = subprocess.run(
                [
                    sys.executable,
                    str(GENERATOR_PATH),
                    "--output",
                    str(candidate),
                    "--check",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn(
            "content mismatch: open/calibration/scenario.json",
            rejected.stderr,
        )


if __name__ == "__main__":
    unittest.main()
