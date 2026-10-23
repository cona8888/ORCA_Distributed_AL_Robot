from __future__ import annotations

import ast
import hashlib
import json
import math
import pathlib
import struct
import sys
import tempfile
import unittest

from scripts.replication import analyze_orca_comparison as analyzer


def float_record(value: float, bits: int) -> dict[str, object]:
    if bits == 32:
        native = struct.unpack(">f", struct.pack(">f", value))[0]
        encoded = struct.pack(">f", native).hex()
    else:
        native = float(value)
        encoded = struct.pack(">d", native).hex()
    return {"hex": encoded, "value": native}


def point_record(x: float, y: float, bits: int) -> dict[str, object]:
    left, right = float_record(x, bits), float_record(y, bits)
    return {
        "hex": [left["hex"], right["hex"]],
        "value": [left["value"], right["value"]],
    }


def state(rid: int, position: tuple[float, float], velocity: tuple[float, float], goal=(4.0, 0.0)) -> analyzer.AgentState:
    return analyzer.AgentState(rid, position, velocity, 1.0, goal)


class FloatIdentityTests(unittest.TestCase):
    def test_binary32_returns_authenticated_native_value(self) -> None:
        encoded = struct.pack(">f", 0.1).hex()
        observed = analyzer.validate_float_hex(0.1, encoded, 32, "x")
        self.assertEqual(observed, struct.unpack(">f", bytes.fromhex(encoded))[0])
        self.assertNotEqual(observed, 0.1)

    def test_binary64_and_mismatch(self) -> None:
        encoded = struct.pack(">d", -0.0).hex()
        observed = analyzer.validate_float_hex(-0.0, encoded, 64, "x")
        self.assertEqual(math.copysign(1.0, observed), -1.0)
        with self.assertRaises(analyzer.AnalysisError):
            analyzer.validate_float_hex(1.0, struct.pack(">d", 2.0).hex(), 64, "x")

    def test_huge_json_integer_fails_as_analysis_error(self) -> None:
        with self.assertRaises(analyzer.AnalysisError):
            analyzer._finite(10**10000, "huge")

    def test_pathologically_deep_json_fails_as_analysis_error(self) -> None:
        raw = b"[" * 10000 + b"0" + b"]" * 10000
        with self.assertRaises(analyzer.AnalysisError):
            analyzer._parse_json(raw, "deep synthetic document")

    def test_exact_json_equality_preserves_numeric_types(self) -> None:
        self.assertTrue(analyzer._json_exactly_equal({"a": 1}, {"a": 1}))
        self.assertFalse(analyzer._json_exactly_equal({"a": 1}, {"a": 1.0}))
        self.assertFalse(analyzer._json_exactly_equal({"a": True}, {"a": 1}))

    def test_dancers_post_session_limits_match_runner_admission(self) -> None:
        limits = analyzer._dancers_trace_limits({"robot_count": 8, "steps": 100})
        self.assertEqual(limits.max_file_bytes, 1024 * 1024 * 1024)
        self.assertEqual(limits.max_line_bytes, 64 * 1024 * 1024)
        self.assertEqual(limits.max_frames, 201)
        self.assertEqual(limits.max_robots_per_frame, 8)
        self.assertEqual(limits.max_json_depth, 128)

    def test_extreme_finite_trajectory_fails_as_analysis_error(self) -> None:
        trajectory = analyzer.CommonTrajectory(
            "extreme",
            "open",
            "reference",
            "main",
            0.1,
            ((0.0, 0.0),),
            ((4.0, 0.0),),
            (
                analyzer.CommonFrame(0, (state(0, (0.0, 0.0), (0.0, 0.0)),)),
                analyzer.CommonFrame(
                    1, (state(0, (1e308, 0.0), (1e308, 0.0)),)
                ),
                analyzer.CommonFrame(
                    2, (state(0, (-1e308, 0.0), (-1e308, 0.0)),)
                ),
            ),
        )
        with self.assertRaises(analyzer.AnalysisError):
            analyzer.analyze_trajectory(trajectory)

    def test_checked_derived_arithmetic_never_returns_infinity_or_zero(self) -> None:
        smallest = float.fromhex("0x0.0000000000001p-1022")
        largest = sys.float_info.max
        self.assertIsNone(analyzer._finite_difference_or_none(largest, -largest))
        self.assertIsNone(
            analyzer._positive_finite_ratio_or_none(largest, smallest)
        )
        self.assertIsNone(
            analyzer._positive_finite_ratio_or_none(smallest, largest)
        )
        self.assertEqual(analyzer._positive_finite_ratio_or_none(6.0, 3.0), 2.0)

    def test_final_json_boundary_rejects_nonfinite_values(self) -> None:
        with self.assertRaises(analyzer.AnalysisError):
            analyzer._require_finite_json({"poison": math.inf}, "synthetic")


class GeometryAndMotionTests(unittest.TestCase):
    def test_swept_tangency_is_one_zero_duration_episode(self) -> None:
        trajectory = analyzer.CommonTrajectory(
            "s", "circle", "orca", "main", 0.1,
            ((-3.0, 2.0), (0.0, 0.0)),
            ((3.0, 2.0), (0.0, 0.0)),
            (
                analyzer.CommonFrame(0, (state(0, (-3.0, 2.0), (0.0, 0.0)), state(1, (0.0, 0.0), (0.0, 0.0)))),
                analyzer.CommonFrame(1, (state(0, (3.0, 2.0), (0.0, 0.0)), state(1, (0.0, 0.0), (0.0, 0.0)))),
            ),
        )
        result = analyzer.pair_contact_geometry(trajectory)
        self.assertAlmostEqual(result["minimum_pair_clearance_m"], 0.0)
        self.assertEqual(result["pair_contact_episodes"], 1)
        self.assertAlmostEqual(result["pair_contact_exposure_pair_seconds"], 0.0)

    def test_contact_intervals_merge_across_frame_boundary(self) -> None:
        frames = tuple(
            analyzer.CommonFrame(
                tick,
                (state(0, (0.0, 0.0), (0.0, 0.0)), state(1, (1.0, 0.0), (0.0, 0.0))),
            )
            for tick in range(3)
        )
        trajectory = analyzer.CommonTrajectory(
            "s", "circle", "orca", "main", 0.1,
            ((0.0, 0.0), (1.0, 0.0)), ((4.0, 0.0), (4.0, 0.0)), frames,
        )
        result = analyzer.pair_contact_geometry(trajectory)
        self.assertEqual(result["pair_contact_episodes"], 1)
        self.assertAlmostEqual(result["pair_contact_exposure_pair_seconds"], 0.2)
        self.assertEqual(result["pair_rows"][0]["contact_intervals_seconds"], [[0.0, 0.2]])

    def test_first_passage_path_excess_and_smoothness(self) -> None:
        frames = (
            analyzer.CommonFrame(0, (state(0, (0.0, 0.0), (0.0, 0.0), (3.0, 0.0)),)),
            analyzer.CommonFrame(1, (state(0, (2.0, 0.0), (1.0, 0.0), (3.0, 0.0)),)),
            analyzer.CommonFrame(2, (state(0, (3.0, 0.0), (3.0, 0.0), (3.0, 0.0)),)),
        )
        trajectory = analyzer.CommonTrajectory(
            "s", "open", "reference", "main", 0.1,
            ((0.0, 0.0),), ((3.0, 0.0),), frames,
        )
        result = analyzer.analyze_trajectory(trajectory)
        agent = result["agent_rows"][0]
        self.assertAlmostEqual(agent["first_passage_time_s"], 0.1)
        self.assertAlmostEqual(agent["path_length_m"], 3.0)
        self.assertAlmostEqual(agent["path_excess_m"], 0.0)
        self.assertAlmostEqual(agent["velocity_total_variation_mps"], 3.0)
        self.assertAlmostEqual(agent["acceleration_rms_mps2"], math.sqrt(250.0))
        self.assertAlmostEqual(agent["jerk_rms_mps3"], 100.0)
        self.assertEqual(result["scene"]["agent_completion_fraction"], 1.0)
        self.assertEqual(
            result["scene"]["fixed_ceiling_completion_class"],
            "all_agents_completed_by_fixed_ceiling",
        )

    def test_ordinary_noncompletion_uses_registered_fixed_ceiling_label(self) -> None:
        stationary = state(0, (0.0, 0.0), (0.0, 0.0), (4.0, 0.0))
        trajectory = analyzer.CommonTrajectory(
            "s",
            "open",
            "orca",
            "main",
            0.1,
            ((0.0, 0.0),),
            ((4.0, 0.0),),
            tuple(
                analyzer.CommonFrame(tick, (stationary,)) for tick in range(3)
            ),
        )
        result = analyzer.analyze_trajectory(trajectory)
        self.assertEqual(
            result["scene"]["fixed_ceiling_completion_class"],
            "noncompletion_by_fixed_ceiling",
        )


class NormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temporary.name).resolve()
        (self.root / "scenarios").mkdir()
        scenario = {"num_robots": 1, "waypoints": {"0": [[0.0, 0.0], [4.0, 0.0]]}}
        raw = (json.dumps(scenario, sort_keys=True, separators=(",", ":")) + "\n").encode()
        (self.root / "scenarios/s.json").write_bytes(raw)
        self.scenario_sha = hashlib.sha256(raw).hexdigest()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def dancers_row(self) -> dict[str, object]:
        return {
            "cadence": "main", "common_sample_count": 3,
            "config_relpath": "configs/s.yaml", "method": "reference",
            "native_sample_stride": 1, "robot_count": 1,
            "scenario_relpath": "scenarios/s.json", "scenario_sha256": self.scenario_sha,
            "scene_id": "s", "scene_set": "open", "sequence": 7,
            "steps": 2, "trace_detail": "positions",
        }

    def dancers_robot(self, x: float, vx: float = 0.0) -> dict[str, object]:
        values = [x, 0.0, 0.0, vx, 0.0, 0.0]
        return {
            "final_goal": [4.0, 0.0], "radius_m": 1.0, "rid": 0,
            "state": values,
            "state_hex": [struct.pack(">d", value).hex() for value in values],
        }

    def test_dancers_variable_h_projection_and_all_state_hex(self) -> None:
        row = self.dancers_row()
        provenance = {"map_sha256": analyzer.EMPTY_SHA256}
        header = {
            "metadata": provenance,
            "observer": {"trace_detail": "positions"},
            "resolved_config": {
                "config_file": "comparison/publication/dancers-orca-v1/configs/s.yaml",
                "scenario_file": "comparison/publication/dancers-orca-v1/scenarios/s.json",
                "obstacle_file": "", "obstacles_present": False,
                "configured_robot_count": 1, "max_timesteps": 2,
                "timestep_seconds": struct.unpack(">f", struct.pack(">f", 0.1))[0],
            },
            "schema_version": "dancers-comparison-trace-v3", "type": "header",
        }
        frames = [
            {"type": "frame", "phase": "pre_update", "tick": 0, "robots": [self.dancers_robot(0.0)]},
            {"type": "frame", "phase": "post_update", "tick": 1, "robots": [self.dancers_robot(1.0, 1.0)]},
            {"type": "frame", "phase": "pre_update", "tick": 1, "robots": [self.dancers_robot(1.0, 1.0)]},
            {"type": "frame", "phase": "post_update", "tick": 2, "robots": [self.dancers_robot(2.0, 1.0)]},
        ]
        path = self.root / "dancers.ndjson"
        path.write_text(
            "".join(
                json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
                for item in [header, *frames]
            )
        )
        trajectory = analyzer.normalize_dancers_trace(path, row, self.root, provenance=provenance)
        self.assertEqual([frame.tick for frame in trajectory.frames], [0, 1, 2])
        self.assertEqual(trajectory.common_steps, 2)
        frames[0]["robots"][0]["state_hex"][2] = struct.pack(">d", 9.0).hex()
        path.write_text(
            "".join(
                json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
                for item in [header, *frames]
            )
        )
        with self.assertRaises(analyzer.AnalysisError):
            analyzer.normalize_dancers_trace(path, row, self.root, provenance=provenance)

    def test_dancers_initialization_residual_is_bounded_and_not_clamped(self) -> None:
        row = self.dancers_row()
        provenance = {"map_sha256": analyzer.EMPTY_SHA256}
        header = {
            "metadata": provenance,
            "observer": {"trace_detail": "positions"},
            "resolved_config": {
                "config_file": "comparison/publication/dancers-orca-v1/configs/s.yaml",
                "scenario_file": "comparison/publication/dancers-orca-v1/scenarios/s.json",
                "obstacle_file": "",
                "obstacles_present": False,
                "configured_robot_count": 1,
                "max_timesteps": 2,
                "timestep_seconds": struct.unpack(
                    ">f", struct.pack(">f", 0.1)
                )[0],
            },
            "schema_version": "dancers-comparison-trace-v3",
            "type": "header",
        }
        residual = 4e-10
        first = self.dancers_robot(residual, residual)
        frames = [
            {"phase": "pre_update", "robots": [first], "tick": 0, "type": "frame"},
            {
                "phase": "post_update",
                "robots": [self.dancers_robot(1.0, 1.0)],
                "tick": 1,
                "type": "frame",
            },
            {
                "phase": "pre_update",
                "robots": [self.dancers_robot(1.0, 1.0)],
                "tick": 1,
                "type": "frame",
            },
            {
                "phase": "post_update",
                "robots": [self.dancers_robot(2.0, 1.0)],
                "tick": 2,
                "type": "frame",
            },
        ]
        path = self.root / "dancers-residual.ndjson"

        def write() -> None:
            path.write_text(
                "".join(
                    json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
                    for item in [header, *frames]
                )
            )

        write()
        trajectory = analyzer.normalize_dancers_trace(
            path, row, self.root, provenance=provenance
        )
        self.assertEqual(trajectory.frames[0].agents[0].position[0], residual)
        self.assertEqual(trajectory.frames[0].agents[0].velocity[0], residual)

        outside = 6e-10
        frames[0]["robots"] = [self.dancers_robot(outside, outside)]
        write()
        with self.assertRaisesRegex(analyzer.AnalysisError, "initial state differs"):
            analyzer.normalize_dancers_trace(
                path, row, self.root, provenance=provenance
            )

    def test_orca_stride_two_projection_and_binary32_hex(self) -> None:
        row = {
            "cadence": "orca-0.05s-sensitivity", "cohort_id": "c",
            "common_sample_count": 3, "method": "orca", "native_sample_stride": 2,
            "phase": "sensitivity", "repetition": 1, "robot_count": 1,
            "run_class": "cadence_sensitivity", "scenario_relpath": "scenarios/s.json",
            "scenario_sha256": self.scenario_sha, "scene_id": "s", "scene_set": "open",
            "sequence": 8, "steps": 4, "timestep_seconds": 0.05,
            "trace_detail": "positions",
        }
        audit = {
            "accepted": {
                "agents": [{
                    "initial_position": point_record(0.0, 0.0, 32),
                    "initial_velocity": point_record(0.0, 0.0, 32),
                    "max_neighbors": 0,
                    "maximum_speed": float_record(2.0, 32),
                    "neighbor_distance": float_record(20.0, 32),
                    "radius": float_record(1.0, 32), "rid": 0,
                    "time_horizon": float_record(1.5, 32),
                    "time_horizon_obstacles": float_record(1.5, 32),
                }],
                "num_agents": 1, "num_obstacle_vertices": 0,
                "time_step": float_record(0.05, 32),
            },
            "requested": {
                "common_sample_count": 3,
                "goal_latch": "permanent after first native boundary at distance <= 1 m",
                "goal_tolerance_m": 1.0, "initial_velocity_mps": [0.0, 0.0],
                "maximum_speed_mps": 2.0, "max_neighbors": 0,
                "native_sample_stride": 2, "native_steps": 4,
                "native_timestep_seconds": 0.05, "neighbor_distance_m": 20.0,
                "obstacle_count": 0, "robot_radius_m": 1.0,
                "stopping": "fixed ceiling; no early stop or agent deletion",
                "time_horizon_obstacles_seconds": 1.5, "time_horizon_seconds": 1.5,
            },
            "verified_through_public_getters": True,
        }
        header = {
            "input": {
                "cadence": row["cadence"],
                "input_manifest_sha256": analyzer.FROZEN_INPUT_MANIFEST_SHA256,
                "scenario_sha256": self.scenario_sha, "scene_id": "s",
                "scene_set": "open", "schedule_sha256": analyzer.FROZEN_SCHEDULE_SHA256,
                "study_id": analyzer.STUDY_ID,
            },
            "numeric_identity": "native IEEE-754 binary32 values plus 8-digit hex bits",
            "observation_rule": "initial state plus post-doStep states; integer common ticks define time",
            "parameter_audit": audit, "schema_version": "dancers-orca-trace-v1",
            "source": analyzer.ORCA_SOURCE, "type": "header",
        }

        def robot(x: float, velocity: float) -> dict[str, object]:
            return {
                "executed_velocity": point_record(velocity, 0.0, 32),
                "first_goal_native_step": None, "goal": point_record(4.0, 0.0, 32),
                "goal_distance_m": 4.0 - x, "goal_latched": False,
                "position": point_record(x, 0.0, 32),
                "preferred_velocity": point_record(2.0, 0.0, 32),
                "radius": float_record(1.0, 32), "rid": 0,
            }

        frames = [
            {"common_tick": tick, "native_step": tick * 2,
             "nominal_time_seconds": tick / 10.0, "robots": [robot(float(tick), float(tick))],
             "type": "frame"}
            for tick in range(3)
        ]
        frames[0]["robots"][0]["preferred_velocity"] = point_record(0.0, 0.0, 32)
        path = self.root / "orca.ndjson"
        path.write_text(
            "".join(
                json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
                for item in [header, *frames]
            )
        )
        trajectory = analyzer.normalize_orca_trace(path, row, self.root, expected_parameter_audit=audit)
        self.assertEqual(trajectory.common_steps, 2)
        self.assertEqual([frame.tick for frame in trajectory.frames], [0, 1, 2])
        frames[1]["robots"][0]["executed_velocity"]["hex"][0] = struct.pack(">f", 9.0).hex()
        path.write_text(
            "".join(
                json.dumps(item, sort_keys=True, separators=(",", ":")) + "\n"
                for item in [header, *frames]
            )
        )
        with self.assertRaises(analyzer.AnalysisError):
            analyzer.normalize_orca_trace(path, row, self.root, expected_parameter_audit=audit)


class BootstrapAndPolicyTests(unittest.TestCase):
    def test_hash_index_rule_is_machine_exact(self) -> None:
        metric, draw, position = "all_agents_completion", 17, 9
        raw = hashlib.sha256(
            b"dancers-orca-open-bootstrap-v1\0" + metric.encode() + b"\0"
            + struct.pack(">Q", draw) + struct.pack(">I", position)
        ).digest()
        self.assertEqual(analyzer.bootstrap_index(metric, draw, position), int.from_bytes(raw[:8], "big") % 20)

    def pairs(self) -> list[dict[str, object]]:
        pairs = []
        for mission in analyzer.OPEN_MISSIONS:
            left = {metric: 1.0 for metric in analyzer.BEHAVIOR_DIFFERENCE_METRICS}
            right = {metric: 0.0 for metric in analyzer.BEHAVIOR_DIFFERENCE_METRICS}
            left["all_agents_completion"] = True
            right["all_agents_completion"] = False
            pairs.append({"scene_id": f"orca-open-m{mission}-n8", "estimable": True, "dancers": left, "orca": right})
        return pairs

    def test_open_median_and_no_complete_case_policy(self) -> None:
        pairs = self.pairs()
        result = analyzer.summarize_open_behavior_pairs(list(reversed(pairs)), draws=3)
        summary = result["metric_summaries"]["mean_path_length_m"]
        self.assertEqual(summary["mean_difference"], 1.0)
        self.assertEqual(summary["median_difference"], 1.0)
        self.assertEqual(len(summary["mission_differences"]), 20)
        pairs[0]["estimable"] = False
        missing = analyzer.summarize_open_behavior_pairs(pairs, draws=1)
        self.assertFalse(missing["metric_summaries"]["all_agents_completion"]["estimable"])
        self.assertEqual(
            missing["metric_summaries"]["all_agents_completion"]["status"],
            "not_estimable_due_to_registered_failure",
        )
        self.assertEqual(len(missing["metric_summaries"]["mean_path_length_m"]["missing_registered_scenes"]), 1)

        pairs = self.pairs()
        pairs[0]["dancers"]["all_agents_makespan_s"] = None
        missing = analyzer.summarize_open_behavior_pairs(pairs, draws=1)
        self.assertEqual(
            missing["metric_summaries"]["all_agents_makespan_s"]["status"],
            "not_estimable_due_to_noncompletion",
        )

    def test_resource_geometric_reduction_and_failure(self) -> None:
        rows = [
            {
                "scene_id": f"orca-open-m{mission}-n8",
                "ratios": {metric: 2.0 for metric in analyzer.RESOURCE_RATIO_METRICS},
            }
            for mission in analyzer.OPEN_MISSIONS
        ]
        result = analyzer.summarize_open_resource_rows(rows, draws=2)
        self.assertAlmostEqual(result["metric_summaries"]["dancers_wall_speedup"]["geometric_mean_ratio"], 2.0)
        rows[3]["ratios"] = None
        result = analyzer.summarize_open_resource_rows(rows, draws=1)
        self.assertEqual(
            result["metric_summaries"]["dancers_cpu_speedup"]["status"],
            "not_estimable_due_to_registered_failure",
        )

    def test_large_finite_values_remain_closed_under_mean_and_quantile(self) -> None:
        interval = analyzer.bootstrap_mean_interval(
            [1e307] * 20, "large-finite-synthetic", draws=2
        )
        self.assertTrue(math.isfinite(interval["estimate"]))
        self.assertEqual(interval["estimate"], 1e307)
        self.assertEqual(analyzer.percentile([-1e308, 1e308], 0.5), 0.0)
        self.assertEqual(
            analyzer._finite_arithmetic_mean(
                [sys.float_info.max, sys.float_info.max], "large median"
            ),
            sys.float_info.max,
        )

    def test_numeric_range_missingness_is_explicit_and_not_complete_case(self) -> None:
        pairs = self.pairs()
        pairs[0]["dancers"]["mean_path_length_m"] = sys.float_info.max
        pairs[0]["orca"]["mean_path_length_m"] = -sys.float_info.max
        result = analyzer.summarize_open_behavior_pairs(pairs, draws=1)
        summary = result["metric_summaries"]["mean_path_length_m"]
        self.assertFalse(summary["estimable"])
        self.assertEqual(summary["status"], "not_estimable_due_to_numeric_range")
        self.assertEqual(summary["numeric_range_scenes"], ["orca-open-m31-n8"])

        rows = [
            {
                "dancers_ratio_status": "estimable",
                "ratios": {metric: 2.0 for metric in analyzer.RESOURCE_RATIO_METRICS},
                "scene_id": f"orca-open-m{mission}-n8",
            }
            for mission in analyzer.OPEN_MISSIONS
        ]
        rows[0]["ratios"]["dancers_cpu_speedup"] = math.inf
        resource = analyzer.summarize_open_resource_rows(rows, draws=1)
        resource_summary = resource["metric_summaries"]["dancers_cpu_speedup"]
        self.assertEqual(
            resource_summary["status"], "not_estimable_due_to_numeric_range"
        )


class ContractAndSurfaceTests(unittest.TestCase):
    def test_input_tree_rejects_an_unregistered_empty_directory(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = pathlib.Path(temporary)
            identities = []
            for index in range(48):
                path = root / f"registered-{index}.json"
                raw = b"{}\n"
                path.write_bytes(raw)
                identities.append(
                    {
                        "bytes": len(raw),
                        "mode": f"{path.stat().st_mode & 0o7777:04o}",
                        "path": path.name,
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                )
            (root / "manifest.json").write_bytes(b"{}\n")
            (root / "manifest.sha256").write_bytes(b"placeholder\n")
            analyzer._verify_input_tree(root, {"files": identities})
            (root / "unregistered-empty-directory").mkdir()
            with self.assertRaises(analyzer.AnalysisError):
                analyzer._verify_input_tree(root, {"files": identities})

    def test_analysis_contract_is_pure_and_exactly_names_registered_metrics(self) -> None:
        first = analyzer.analysis_contract()
        second = analyzer.analysis_contract()
        self.assertEqual(first, second)
        self.assertEqual(first["behavior"]["metric_ids"], list(analyzer.BEHAVIOR_DIFFERENCE_METRICS))
        self.assertEqual(first["resource"]["metric_ids"], list(analyzer.RESOURCE_RATIO_METRICS))
        self.assertEqual(
            first["behavior"]["fixed_ceiling_completion_classes"][
                "ordinary_noncompletion"
            ],
            "noncompletion_by_fixed_ceiling",
        )
        self.assertEqual(
            first["resource"]["admissibility"],
            {
                "failure_effect": (
                    "all wall/CPU/RSS medians for that method and scene and "
                    "all dependent ratios or ORCA context are unavailable"
                ),
                "per_repetition": {
                    "fixed_work_verified": True,
                    "outcome": "completed",
                    "peak_rss_mib": "> 0",
                    "total_cpu_seconds": "> 0",
                },
                "required_repetitions": 3,
            },
        )
        self.assertEqual(
            first["resource"]["sequence_diagnostics"],
            {
                "process_failure_sequences": (
                    "non-completed or fixed-work-unverified attempts only"
                ),
                "resource_inadmissible_sequences": (
                    "completed fixed-work attempts with nonpositive CPU or RSS only"
                ),
            },
        )
        self.assertIn(
            "not_estimable_due_to_numeric_range",
            first["failure_policy"]["numeric_range"],
        )
        self.assertTrue(
            first["resource"]["derived_numeric_policy"][
                "no_clipping_or_infinity"
            ]
        )
        self.assertEqual(
            first["integrity"]["dancers_initialization_residual"],
            {
                "absolute_position_tolerance_m": 5e-10,
                "absolute_velocity_tolerance_mps": 5e-10,
                "metric_treatment": "retain observed tick-0 state without clamping",
                "registered_baseline": "authenticated scenario start centre",
            },
        )
        self.assertEqual(first["integrity"]["dancers_no_map_sha256"], hashlib.sha256(b"").hexdigest())
        self.assertEqual(
            first["result_contract"]["runtime_binding_exact_keys"],
            ["binaries", "build_manifest", "libomp", "source_endpoints", "source_roots"],
        )

    def test_analyzer_has_no_process_launch_surface(self) -> None:
        source_path = pathlib.Path(analyzer.__file__)
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports |= {
            node.module.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }
        self.assertNotIn("sub" + "process", imports)
        self.assertNotIn("P" + "open", source)
        self.assertNotIn("random.Random", source)

    def test_zero_cpu_and_rss_are_valid_terminal_measurements(self) -> None:
        measurement = {
            "peak_rss_mib": 0.0, "peak_rss_raw": 0,
            "peak_rss_raw_unit": "bytes", "returncode": 1, "signal": None,
            "system_cpu_seconds": 0.0, "timed_out": False,
            "total_cpu_seconds": 0.0, "user_cpu_seconds": 0.0,
            "wall_seconds": 0.001,
        }
        analyzer._validate_measurement(measurement, "nonzero_exit", "synthetic")

    def test_huge_raw_rss_fails_as_analysis_error(self) -> None:
        measurement = {
            "peak_rss_mib": 0.0,
            "peak_rss_raw": 10**10000,
            "peak_rss_raw_unit": "bytes",
            "returncode": 0,
            "signal": None,
            "system_cpu_seconds": 0.0,
            "timed_out": False,
            "total_cpu_seconds": 0.0,
            "user_cpu_seconds": 0.0,
            "wall_seconds": 0.001,
        }
        with self.assertRaises(analyzer.AnalysisError):
            analyzer._validate_measurement(measurement, "completed", "synthetic")


if __name__ == "__main__":
    unittest.main()
