from __future__ import annotations

import hashlib
import json
import math
import subprocess
import unittest
from decimal import Decimal
from pathlib import Path, PurePosixPath


REPO_ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = (
    REPO_ROOT / "docs" / "simulator" / "physics_stress_test_v1.json"
)
ADDENDUM_PATH = (
    REPO_ROOT / "docs" / "simulator" / "PHYSICS_STRESS_TEST_V1.md"
)


class _DuplicateKey(ValueError):
    pass


def _reject_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value}")


def _strict_registry(data: bytes) -> dict[str, object]:
    value = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_keys,
        parse_float=Decimal,
        parse_constant=_reject_constant,
    )
    if not isinstance(value, dict):
        raise TypeError("physics registry must be an object")
    return value


def _canonical_json_bytes(value: object) -> bytes:
    def encode_decimal(item: object) -> float:
        if isinstance(item, Decimal):
            return float(item)
        raise TypeError(f"unsupported canonical JSON value: {type(item)!r}")

    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=encode_decimal,
        )
        + "\n"
    ).encode("utf-8")


class PhysicsStressTestContractV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry_bytes = REGISTRY_PATH.read_bytes()
        cls.registry = _strict_registry(cls.registry_bytes)
        cls.addendum = ADDENDUM_PATH.read_text(encoding="utf-8")

    def test_registry_is_strict_canonical_json(self) -> None:
        self.assertEqual(
            self.registry_bytes,
            _canonical_json_bytes(self.registry),
        )
        self.assertTrue(self.registry_bytes.endswith(b"\n"))

    def test_parent_contract_identity_is_exact(self) -> None:
        parent = self.registry["parent_contract"]
        self.assertEqual(parent["contract_id"], "dancers-simulator-demo-v1")
        self.assertEqual(
            parent["physics_track_id"],
            "simulator-demo-v1-physics-stress-test-epoch-1",
        )
        relative = PurePosixPath(parent["path"])
        self.assertFalse(relative.is_absolute())
        self.assertNotIn("..", relative.parts)
        data = REPO_ROOT.joinpath(*relative.parts).read_bytes()
        self.assertEqual(hashlib.sha256(data).hexdigest(), parent["sha256"])
        self.assertIn(
            parent["checkpoint_commit_at_design"],
            subprocess.run(
                ["git", "rev-list", "--all"],
                cwd=REPO_ROOT,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.splitlines(),
        )

    def test_checkpoint_is_execution_locked(self) -> None:
        status = self.registry["status"]
        self.assertEqual(
            status["classification"],
            "prospective-design-frozen-execution-locked",
        )
        for field in (
            "engine_implemented",
            "execution_authorized_by_this_checkpoint",
            "missions_generated",
            "plans_frozen",
            "scientific_processes_permitted",
        ):
            self.assertIs(status[field], False)
        self.assertIs(status["documentation_only"], True)
        self.assertIs(
            self.registry["authorization"]["execution_authorized"], False
        )
        self.assertIn("Experimental execution authorized: **no**", self.addendum)

        namespaces = self.registry["namespaces"]
        for field in ("plan_root", "result_root"):
            relative = PurePosixPath(namespaces[field])
            self.assertFalse(relative.is_absolute())
            self.assertNotIn("..", relative.parts)
            destination = REPO_ROOT.joinpath(*relative.parts)
            self.assertFalse(destination.exists() or destination.is_symlink())

    def test_future_unlock_requires_host_hypotheses_and_golden_baseline(
        self,
    ) -> None:
        authorization = self.registry["authorization"]
        required = set(authorization["required_future_plan_sha256_fields"])
        self.assertTrue(
            {
                "host_inventory_sha256",
                "hypothesis_claim_manifest_sha256",
                "kinematic_regression_fixture_sha256",
                "kinematic_regression_golden_digest_sha256",
                "reference_fidelity_binary_sha256",
                "optimized_fidelity_binary_sha256",
                "reference_timing_binary_sha256",
                "optimized_timing_binary_sha256",
                "collider_manifest_sha256",
                "mission_manifest_sha256",
            }.issubset(required)
        )
        self.assertIn("validation_analysis_seal_sha256", authorization[
            "phase_unlocks"
        ]["audit"])
        self.assertIn("hypotheses", authorization["phase_unlocks"]["validation"])

    def test_engine_constants_and_derived_values_are_consistent(self) -> None:
        engine = self.registry["engine"]
        self.assertEqual(engine["engine_id"], "dancers-planar-disc-engine-v1")
        self.assertEqual(
            engine["dependency_policy"]["core_language"], "C++20"
        )
        self.assertTrue(
            engine["dependency_policy"]["core_standard_library_only"]
        )
        self.assertFalse(
            engine["dependency_policy"]["external_physics_dependency"]
        )

        body = engine["body"]
        actuator = engine["actuator"]
        self.assertEqual(
            body["inertia_kg_m2"],
            Decimal("0.5")
            * body["mass_kg"]
            * body["radius_m"]
            * body["radius_m"],
        )
        self.assertEqual(
            actuator["maximum_force_n"],
            body["mass_kg"]
            * actuator["maximum_linear_acceleration_m_s2"],
        )
        self.assertEqual(
            actuator["maximum_torque_n_m"],
            body["inertia_kg_m2"]
            * actuator["maximum_angular_acceleration_rad_s2"],
        )
        self.assertEqual(body["restitution"], 0)
        self.assertEqual(body["radius_m"], 1)

        step = engine["step"]
        self.assertEqual(step["controller_timestep_s"], Decimal("0.1"))
        self.assertEqual(step["physics_timestep_s"], Decimal("0.01"))
        self.assertEqual(step["physics_substeps_per_controller_tick"], 10)
        self.assertEqual(
            step["physics_timestep_s"]
            * step["physics_substeps_per_controller_tick"],
            step["controller_timestep_s"],
        )
        self.assertFalse(step["variable_timestep_permitted"])
        self.assertEqual(engine["contact_solver"]["velocity_iterations"], 8)
        self.assertEqual(engine["contact_solver"]["position_iterations"], 3)
        coordinates = engine["coordinates"]
        self.assertEqual(coordinates["x_direction"], "increasing-raster-column")
        self.assertEqual(coordinates["y_direction"], "increasing-raster-row")
        self.assertEqual(coordinates["displayed_positive_rotation"], "clockwise")
        self.assertIn("atan2(sin(angle),cos(angle))", coordinates[
            "angle_wrap_function"
        ])

    def test_collider_mapping_matches_simulator_coordinates(self) -> None:
        collider = self.registry["collider_conversion"]
        self.assertEqual(collider["raster_width"], 1000)
        self.assertEqual(collider["raster_height"], 1000)
        self.assertEqual(collider["pixel_size_m"], Decimal("0.1"))
        mapping = collider["world_mapping"]
        self.assertEqual(mapping["coordinate_extent_m"], "[-50,50)")
        self.assertEqual(
            mapping["column_c_x_interval_m"],
            "[-50+0.1*c,-50+0.1*(c+1)]",
        )
        self.assertEqual(
            mapping["row_r_y_interval_m"],
            "[-50+0.1*r,-50+0.1*(r+1)]",
        )
        self.assertIn(
            "no vertical flip",
            mapping["row_and_simulator_y_direction"],
        )
        packing = collider["occupancy_bit_packing"]
        self.assertEqual(packing["bit_index"], "i = row * 1000 + column")
        self.assertEqual(packing["bit_position_in_byte"], "7 - (i mod 8)")
        self.assertEqual(packing["unused_final_low_bits"], 0)
        self.assertEqual(collider["outside_world"], "occupied")
        self.assertIn(
            "virtual occupied outside-world",
            collider["segment_algorithm"]["emit_rule"],
        )
        self.assertIn("external", collider["file_hash_rule"])
        self.assertIn("not embedded", collider["file_hash_rule"])

    def test_phase_seeds_are_complete_disjoint_and_held_out(self) -> None:
        splits = self.registry["missions"]["splits"]
        expected = {
            "calibration": list(range(1, 11)),
            "validation": list(range(11, 31)),
            "audit": list(range(31, 51)),
        }
        observed: set[int] = set()
        for phase, seeds in expected.items():
            self.assertEqual(splits[phase]["seeds"], seeds)
            self.assertTrue(observed.isdisjoint(seeds))
            observed.update(seeds)
        self.assertEqual(observed, set(range(1, 51)))
        self.assertFalse(splits["validation"]["retuning_permitted"])
        self.assertFalse(
            splits["audit"]["replacement_for_validation_permitted"]
        )

    def test_missions_require_real_geometry_and_nested_counts(self) -> None:
        missions = self.registry["missions"]
        self.assertFalse(
            missions["count_grids"]["candidate_values_frozen_by_this_checkpoint"]
        )
        self.assertIn("byte-identical", missions["nested_count_prefix_policy"])
        self.assertIn(
            "robot_count is intentionally excluded",
            missions["generator_seed_derivation"],
        )
        semantic = missions["semantic_hash"]
        self.assertFalse(semantic["seed_metadata_only_is_distinct_mission"])
        self.assertTrue(
            semantic["pairwise_distinct_across_scientific_mission_identities"]
        )
        self.assertIn("integer-millimetre starts", semantic["includes"])
        requirements = missions["mission_requirements"]
        self.assertEqual(
            requirements["minimum_route_centerline_obstacle_clearance_m"],
            Decimal("1.05"),
        )
        self.assertEqual(
            requirements[
                "initial_and_final_goal_minimum_center_separation_m"
            ],
            Decimal("2.1"),
        )

    def test_horizon_formula_is_integer_exact_and_not_ten_seconds(self) -> None:
        formula = self.registry["missions"]["horizon"]["formula"]
        self.assertEqual(
            formula,
            [
                "leg_upper_mm = ceil_sqrt(dx_mm^2 + dy_mm^2)",
                "robot_route_upper_mm = sum(leg_upper_mm)",
                "Lmax_mm = max(robot_route_upper_mm)",
                "nominal_min_travel_ms = ceil_div(1000 * Lmax_mm, 2000)",
                "raw_budget_ms = max(30000, 2 * nominal_min_travel_ms + 10000)",
                "mission_controller_ticks = ceil_div(raw_budget_ms, 100)",
                "mission_horizon_ms = 100 * mission_controller_ticks",
                "physics_steps = 10 * mission_controller_ticks",
            ],
        )

        def ceil_sqrt(value: int) -> int:
            root = math.isqrt(value)
            return root if root * root == value else root + 1

        def ceil_div(left: int, right: int) -> int:
            return (left + right - 1) // right

        # A two-leg 30 m + 40 m route has an exact 70 m upper length.
        route_mm = ceil_sqrt(30_000**2) + ceil_sqrt(40_000**2)
        nominal_ms = ceil_div(1000 * route_mm, 2000)
        raw_ms = max(30_000, 2 * nominal_ms + 10_000)
        ticks = ceil_div(raw_ms, 100)
        self.assertEqual(route_mm, 70_000)
        self.assertEqual(nominal_ms, 35_000)
        self.assertEqual(raw_ms, 80_000)
        self.assertEqual(ticks, 800)
        self.assertEqual(10 * ticks, 8_000)

    def test_fixed_work_and_mission_estimands_are_separate(self) -> None:
        classes = self.registry["run_classes"]
        fixed = classes["fixed_work"]
        mission = classes["route_mission"]
        self.assertEqual(fixed["controller_ticks"], 100)
        self.assertEqual(fixed["physics_steps"], 1000)
        self.assertEqual(fixed["simulated_seconds"], 10)
        self.assertTrue(fixed["compute_reduction_evidence_permitted_after_gates"])
        self.assertFalse(mission["compute_reduction_claim_source"])
        self.assertFalse(mission["early_termination_on_all_goals"])
        self.assertIn("remain distinct", classes["separation_rule"])
        directories = self.registry["namespaces"]["run_class_directories"]
        self.assertEqual(
            directories,
            {
                "physics-fixed-work-v1": "fixed-work",
                "physics-route-mission-v1": "route-mission",
            },
        )

    def test_contact_goal_and_validity_semantics_are_unambiguous(self) -> None:
        gates = self.registry["gates"]
        self.assertEqual(gates["per_process_validity"]["invalid_geometry_count"], 0)
        self.assertIn(
            "controller boundary",
            gates["mission_completion_claim"],
        )
        contact = self.registry["metrics"]["contact_semantics"]
        self.assertIn("final accumulated", contact["maximum_impulse"])
        self.assertIn("final accumulated", contact["total_impulse"])
        self.assertIn("null unless final goal is reached", contact[
            "route_efficiency"
        ])

    def test_required_metrics_and_nonclaims_are_registered(self) -> None:
        metrics = self.registry["metrics"]
        for field in (
            "pair_contact_events",
            "obstacle_contact_events",
            "maximum_residual_penetration_m",
            "maximum_pair_normal_impulse_n_s",
            "maximum_obstacle_normal_impulse_n_s",
        ):
            self.assertIn(field, metrics["safety"])
        for field in (
            "total_cpu_seconds",
            "wall_seconds",
            "peak_rss_mib",
            "robot_physics_steps_per_cpu_second",
        ):
            self.assertIn(field, metrics["resources"])
        prohibited = self.registry["claim_boundaries"]["prohibited"]
        self.assertIn("DANCeRS paper physics replication", prohibited)
        self.assertIn("universal maximum swarm size", prohibited)
        self.assertIn("cross-platform bitwise determinism", prohibited)


if __name__ == "__main__":
    unittest.main()
