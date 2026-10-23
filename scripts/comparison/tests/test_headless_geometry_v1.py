#!/usr/bin/env python3
"""Focused pure regressions for Track-B swept geometry primitives."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/headless_geometry_v1.py"


def _load():
    spec = importlib.util.spec_from_file_location("_headless_geometry_v1", SOURCE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


geometry = _load()


def raster(size: int, occupied: set[tuple[int, int]], world: float = 10.0):
    pixels = bytearray([255]) * (size * size)
    for x, y in occupied:
        pixels[y * size + x] = 0
    raw = bytes(pixels)
    return geometry.Raster(size, world, raw, hashlib.sha256(raw).hexdigest())


def robot(rid: int, x: float, y: float, radius: float = 0.5):
    return {"rid": rid, "position": [x, y], "radius_m": radius}


class HeadlessGeometryTests(unittest.TestCase):
    def test_pair_crossing_and_tangency_are_unsafe(self):
        crossing = geometry.swept_disc_pair(
            [-2.0, 0.0], [2.0, 0.0], [0.0, -2.0], [0.0, 2.0], 0.5, 0.5
        )
        self.assertAlmostEqual(crossing.minimum_clearance_m, -1.0)
        self.assertGreater(crossing.contact_fraction, 0.0)
        self.assertIsNotNone(crossing.entry_u)
        self.assertIsNotNone(crossing.exit_u)

        tangent = geometry.swept_disc_pair(
            [-2.0, 0.0], [2.0, 0.0], [0.0, 1.0], [0.0, 1.0], 0.5, 0.5
        )
        self.assertAlmostEqual(tangent.minimum_clearance_m, 0.0)
        self.assertEqual(tangent.contact_fraction, 0.0)
        self.assertEqual(tangent.entry_u, tangent.exit_u)

        below_numeric_margin = geometry.analyze_sweeps(
            [
                {
                    "tick": 0,
                    "start": [
                        robot(0, 0.0, 0.0),
                        robot(1, 1.0 + geometry.SAFETY_EPSILON_M / 2.0, 0.0),
                    ],
                    "end": [
                        robot(0, 0.0, 0.0),
                        robot(1, 1.0 + geometry.SAFETY_EPSILON_M / 2.0, 0.0),
                    ],
                }
            ],
            raster(100, set()),
        )
        self.assertFalse(below_numeric_margin["geometry_contact_free"])
        self.assertIn(
            "pair-contact-or-clearance-not-above-safety-epsilon",
            below_numeric_margin["geometry_failure_reasons"],
        )

    def test_obstacle_tunnelling_is_detected_continuously(self):
        # Cell (5,5) is the closed square [0,1] x [0,1].  Both endpoints
        # are clear; the segment crosses it between observations.
        result = geometry.swept_disc_obstacles(
            [-2.0, 0.5], [2.0, 0.5], 0.25, raster(10, {(5, 5)})
        )
        self.assertLessEqual(result.minimum_clearance_lower_bound_m, 0.0)
        self.assertGreater(result.contact_fraction, 0.0)
        self.assertFalse(result.positive_clearance_proved)
        self.assertTrue(result.minimum_clearance_exact)

    def test_rounded_cell_corner_does_not_use_square_overapproximation(self):
        # Distance from (-0.3,-0.3) to the occupied cell corner (0,0) is
        # sqrt(.18), which is greater than radius .4.  Expanding the AABB by
        # .4 in both axes would incorrectly classify this as contact.
        result = geometry.swept_disc_obstacles(
            [-0.3, -0.3], [-0.3, -0.3], 0.4, raster(10, {(5, 5)})
        )
        self.assertEqual(result.contact_fraction, 0.0)
        self.assertTrue(result.positive_clearance_proved)
        self.assertGreater(result.minimum_clearance_lower_bound_m, 0.0)

    def test_world_exterior_is_occupied(self):
        result = geometry.swept_disc_obstacles(
            [4.0, 0.0], [4.9, 0.0], 0.25, raster(10, set())
        )
        self.assertGreater(result.contact_fraction, 0.0)
        self.assertLessEqual(result.minimum_clearance_lower_bound_m, 0.0)

    def test_analyze_sweeps_separates_validity_from_authorization(self):
        clear = raster(100, set())
        sweeps = [
            {
                "tick": 0,
                "start": [robot(0, -2.0, -1.0), robot(1, -2.0, 1.0)],
                "end": [robot(0, -1.0, -1.0), robot(1, -1.0, 1.0)],
            },
            {
                "tick": 1,
                "start": [robot(0, -1.0, -1.0), robot(1, -1.0, 1.0)],
                "end": [robot(0, 0.0, -1.0), robot(1, 0.0, 1.0)],
            },
        ]
        report = geometry.analyze_sweeps(sweeps, clear)
        self.assertTrue(report["geometry_contact_free"])
        self.assertFalse(report["task_valid"])
        self.assertFalse(report["task_validity_determined"])
        self.assertEqual(report["pair_sweeps_verified"], 2)
        self.assertEqual(report["obstacle_sweeps_verified"], 4)
        self.assertFalse(report["scientific_execution_authorized"])
        self.assertFalse(report["timing_unlock"])
        self.assertFalse(report["validation_unlock"])
        self.assertFalse(report["benchmark_claim_ready"])

    def test_analyze_sweeps_rejects_collision_and_schema_drift(self):
        clear = raster(100, set())
        collision = [
            {
                "tick": 0,
                "start": [robot(0, -2.0, 0.0), robot(1, 0.0, -2.0)],
                "end": [robot(0, 2.0, 0.0), robot(1, 0.0, 2.0)],
            }
        ]
        report = geometry.analyze_sweeps(collision, clear)
        self.assertFalse(report["geometry_contact_free"])
        self.assertFalse(report["task_valid"])
        self.assertIn(
            "pair-contact-or-clearance-not-above-safety-epsilon",
            report["geometry_failure_reasons"],
        )
        malformed = [{**collision[0], "extra": False}]
        with self.assertRaisesRegex(geometry.GeometryError, "schema differs"):
            geometry.analyze_sweeps(malformed, clear)

    def test_analyze_sweeps_rejects_unverified_inter_sweep_teleport(self):
        clear = raster(100, set())
        sweeps = [
            {
                "tick": 0,
                "start": [robot(0, -2.0, 0.0)],
                "end": [robot(0, -1.0, 0.0)],
            },
            {
                "tick": 1,
                "start": [robot(0, 1.0, 0.0)],
                "end": [robot(0, 2.0, 0.0)],
            },
        ]
        with self.assertRaisesRegex(geometry.GeometryError, "unverified state gap"):
            geometry.analyze_sweeps(sweeps, clear)

    def test_live_maps_match_registered_effective_raster_hashes(self):
        manifest = json.loads(
            (ROOT / "comparison/environments/manifest.json").read_text(
                encoding="utf-8"
            )
        )
        for entry in manifest["environments"]:
            if entry["split"] != "calibration":
                continue
            map_path = ROOT / "comparison/environments" / entry["map"]["path"]
            effective = geometry.effective_raster_from_png(
                map_path,
                expected_png_sha256=entry["map"]["sha256"],
            )
            self.assertEqual(effective.size, 1000)
            self.assertEqual(
                effective.sha256,
                entry["effective_runtime_1000"]["raw_occupancy_sha256"],
            )

    def test_png_and_raster_mutations_fail_closed(self):
        open_map = ROOT / "comparison/environments/open/calibration/map.png"
        data = open_map.read_bytes()
        with self.assertRaisesRegex(geometry.GeometryError, "SHA-256 differs"):
            geometry.effective_raster_from_png(
                open_map, expected_png_sha256="0" * 64
            )
        corrupted = bytearray(data)
        corrupted[-8] ^= 1
        with self.assertRaises(geometry.GeometryError):
            geometry.effective_raster_from_png_bytes(bytes(corrupted))
        with self.assertRaisesRegex(geometry.GeometryError, "SHA-256 differs"):
            geometry.Raster(1, 1.0, b"\xff", "0" * 64)

    def test_symlink_map_is_rejected(self):
        source = ROOT / "comparison/environments/open/calibration/map.png"
        with tempfile.TemporaryDirectory() as directory:
            link = Path(directory) / "map.png"
            link.symlink_to(source)
            with self.assertRaisesRegex(geometry.GeometryError, "symlink ancestor"):
                geometry.effective_raster_from_png(link)

    def test_symlinked_map_parent_is_rejected(self):
        source_parent = ROOT / "comparison/environments/open/calibration"
        with tempfile.TemporaryDirectory() as directory:
            parent_link = Path(directory) / "calibration"
            parent_link.symlink_to(source_parent, target_is_directory=True)
            with self.assertRaisesRegex(geometry.GeometryError, "symlink ancestor"):
                geometry.effective_raster_from_png(parent_link / "map.png")

    def test_nonfinite_and_bool_numeric_values_fail_closed(self):
        for value in (True, False, math.nan, math.inf, 10**4000):
            with self.subTest(value=repr(value)):
                with self.assertRaises(geometry.GeometryError):
                    geometry.swept_disc_pair(
                        [0.0, 0.0], [1.0, 0.0], [0.0, 2.0], [1.0, 2.0], value, 0.5
                    )


if __name__ == "__main__":
    unittest.main()
