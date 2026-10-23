from __future__ import annotations

import importlib.util
import math
import pathlib
import sys
import unittest


REPO = pathlib.Path(__file__).resolve().parents[3]
MODULE_PATH = REPO / "scripts" / "replication" / "run_multimap_study.py"
SPEC = importlib.util.spec_from_file_location("run_multimap_study_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
S = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = S
SPEC.loader.exec_module(S)
P = S.inputs_module

REPAIR_PATH = REPO / "scripts" / "replication" / "analyze_multimap_study_v2.py"
REPAIR_SPEC = importlib.util.spec_from_file_location("analyze_multimap_v2_tested", REPAIR_PATH)
assert REPAIR_SPEC is not None and REPAIR_SPEC.loader is not None
R = importlib.util.module_from_spec(REPAIR_SPEC)
sys.modules[REPAIR_SPEC.name] = R
REPAIR_SPEC.loader.exec_module(R)

V3_PATH = REPO / "scripts" / "replication" / "analyze_multimap_study_v3.py"
V3_SPEC = importlib.util.spec_from_file_location("analyze_multimap_v3_tested", V3_PATH)
assert V3_SPEC is not None and V3_SPEC.loader is not None
V3 = importlib.util.module_from_spec(V3_SPEC)
sys.modules[V3_SPEC.name] = V3
V3_SPEC.loader.exec_module(V3)


def synthetic_manifest() -> dict:
    scenes = []
    for spec in P.map_specs():
        for mission_label in spec["mission_labels"]:
            for count in P.COUNTS[spec["family"]]:
                scene_id = f"{spec['family']}-{spec['map_id']}-m{mission_label}-n{count}"
                scenes.append(
                    {
                        "cell_id": f"{spec['family']}-n{count}",
                        "config_relpath": "config.yaml",
                        "config_sha256": "a" * 64,
                        "family": spec["family"],
                        "map_id": spec["map_id"],
                        "map_relpath": "map.png",
                        "map_sha256": "b" * 64,
                        "mission_id": f"mission-{mission_label}",
                        "mission_label": mission_label,
                        "robot_count": count,
                        "runtime_rng_seed": P.runtime_seed(mission_label),
                        "scenario_relpath": "scenario.json",
                        "scenario_sha256": "c" * 64,
                        "scene_id": scene_id,
                    }
                )
    return {"scenes": scenes}


class MultiMapStudyTests(unittest.TestCase):
    def test_registered_positions_tick_convention(self) -> None:
        self.assertTrue(
            R._frame_pair_matches(
                {"phase": "pre_update", "tick": 0},
                {"phase": "post_update", "tick": 1},
                0,
            )
        )

    def test_frozen_preregistration_blob_is_still_authenticated(self) -> None:
        identity = {
            "bytes": 10204,
            "path": "docs/publication/DANCERS_MULTIMAP_REPLICATION_STUDY_V1.md",
            "sha256": "d30c26c0d0eda260fae6c71c7c2d64970738f48b4750033208193ad5b27b4bd1",
        }
        S._v2_original_require_identity = S.require_identity
        R.study = S
        R._historical_protocol_identity(
            REPO / identity["path"], identity, "protocol preregistration"
        )

    def test_task_validity_is_derived_not_copied_from_helper_lock(self) -> None:
        summary = {
            **V3.SUMMARY_EXPECTED,
            "final_robot_count": 4,
            "maximum_robot_count": 4,
            "minimum_obstacle_clearance_m": 0.1,
            "minimum_pair_clearance_m": 0.2,
        }
        geometry_row = {
            "geometry_contact_free": True,
            "geometry_failure_reasons": [],
            # This is the helper's intentional authorization lock. It must not
            # participate in the derived scientific predicate.
            "task_valid": False,
        }
        self.assertTrue(V3.derive_scene_task_validity(summary, geometry_row, 4)["task_valid"])
        geometry_row["geometry_contact_free"] = False
        self.assertFalse(V3.derive_scene_task_validity(summary, geometry_row, 4)["task_valid"])
        self.assertFalse(
            R._frame_pair_matches(
                {"phase": "pre_update", "tick": 0},
                {"phase": "post_update", "tick": 0},
                0,
            )
        )

    def test_schedule_is_complete_and_balanced(self) -> None:
        schedule = S.schedule_records(synthetic_manifest())
        self.assertEqual(len(schedule), 2082)
        self.assertEqual(sum(row["phase"] == "warmup" for row in schedule), 82)
        self.assertEqual(sum(row["phase"] == "measured" for row in schedule), 2000)
        self.assertEqual([row["sequence"] for row in schedule], list(range(2082)))
        pairs = {}
        for row in schedule:
            pairs.setdefault(row["pair_id"], []).append(row)
        self.assertTrue(all(len(rows) == 2 for rows in pairs.values()))
        self.assertTrue(
            all({row["method"] for row in rows} == set(S.METHODS) for rows in pairs.values())
        )
        timing = [
            row
            for row in schedule
            if row["phase"] == "measured" and row["run_class"] == "timing"
        ]
        self.assertEqual(len(timing), 1200)

    def test_rss_conversion_is_explicit(self) -> None:
        self.assertEqual(S.rss_to_mib(1_048_576, "darwin"), ("bytes", 1.0))
        self.assertEqual(S.rss_to_mib(1024, "linux"), ("kibibytes", 1.0))
        with self.assertRaises(S.StudyError):
            S.rss_to_mib(1, "win32")
        with self.assertRaises(S.StudyError):
            S.rss_to_mib(-1, "darwin")

    def test_bootstrap_is_deterministic_and_map_clustered(self) -> None:
        ratios = {
            (f"map-{index:02d}", f"mission-{mission}"): 1.2 + index / 100
            for index in range(1, 11)
            for mission in (1, 2)
        }
        first = S.bootstrap_interval(
            ratios,
            family="perfect_maze",
            metric="wall_speedup",
            salt=4,
            draws=500,
        )
        second = S.bootstrap_interval(
            ratios,
            family="perfect_maze",
            metric="wall_speedup",
            salt=4,
            draws=500,
        )
        self.assertEqual(first, second)
        self.assertLess(first["lower_95"], first["estimate"])
        self.assertGreater(first["upper_95"], first["estimate"])
        self.assertEqual(first["resampling_unit"], "map-outer-and-mission-within-map")

    def test_resource_analysis_uses_medians_and_retains_all_scenes(self) -> None:
        records = []
        sequence = 0
        for scene in synthetic_manifest()["scenes"]:
            for method, factor in (("reference", 1.3), ("optimized", 1.0)):
                for repetition, noise in enumerate((0.99, 1.0, 1.01), start=1):
                    wall = factor * noise
                    records.append(
                        {
                            **scene,
                            "method": method,
                            "phase": "measured",
                            "repetition": repetition,
                            "run_class": "timing",
                            "sequence": sequence,
                            "measurement": {
                                "peak_rss_mib": 16.0 if method == "reference" else 16.16,
                                "total_cpu_seconds": wall * 0.95,
                                "wall_seconds": wall,
                            },
                        }
                    )
                    sequence += 1
        previous = S.BOOTSTRAP_DRAWS
        S.BOOTSTRAP_DRAWS = 300
        try:
            analysis = S.analyze_resources(records)
        finally:
            S.BOOTSTRAP_DRAWS = previous
        self.assertEqual(len(analysis["scene_rows"]), 200)
        self.assertEqual(len(analysis["cell_rows"]), 10)
        for row in analysis["cell_rows"]:
            self.assertAlmostEqual(row["intervals"]["wall_speedup"]["estimate"], 1.3)
            self.assertAlmostEqual(row["intervals"]["cpu_speedup"]["estimate"], 1.3)
            self.assertAlmostEqual(
                row["intervals"]["rss_optimized_over_reference"]["estimate"], 1.01
            )
            self.assertTrue(row["wall_improvement_gate"])
            self.assertTrue(row["memory_no_increase_above_5pct_gate"])

    def test_geometric_mean_is_stable(self) -> None:
        values = [1.1, 1.2, 1.3]
        expected = math.exp(math.fsum(math.log(value) for value in values) / 3)
        self.assertEqual(S.geometric_mean(values), expected)


if __name__ == "__main__":
    unittest.main()
