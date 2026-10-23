#!/usr/bin/env python3
"""Read-only regressions for the N=16 visual-diagnostic fail-stop record."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import stat
import unittest
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
RECORD_PATH = ROOT / "docs/simulator/visual_diagnostic_n16_engineering_screen_v1.json"
PLAN_PATH = ROOT / "comparison/plans/simulator-demo/v1/visual-diagnostic-calibration-v2/plan.json"
RESULTS_ROOT = ROOT / "results/simulator-demo/v1/visual-diagnostic/calibration-v2"
LEASE_PATH = ROOT / "results/simulator-demo/v1/visual-diagnostic/.calibration-v2-execution.lease"
DEFERRED_ROOT = RESULTS_ROOT / "fractal_medium/n-16/seed-01"

EXPECTED_RECORD_BYTES = 11064
EXPECTED_RECORD_SHA256 = "6b5e8547e78e699146a2092fe9b2505a776a22d0d78d88804eb4d8a132e134ca"
SCHEMA = "dancers-visual-diagnostic-n16-engineering-screen-v1"
CHECKPOINT = "87cda29dce75cd5ca23504713de2904d88cd1316"
CHECKPOINT_TREE = "ed447326bec210698a7cc3e5f278bfb44729371e"
PLAN_SHA256 = "9c473ce64722e810fbf6684f06b0195f19e052990551b734e6e2cc9014745a8d"

AUTHORIZATION = {
    "benchmark_claim_ready": False,
    "capacity_unlock": False,
    "further_visual_execution_permitted_by_this_checkpoint": False,
    "physics_execution_authorized": False,
    "scientific_evidence": False,
    "timing_unlock": False,
    "track_b_execution_authorized": False,
    "validation_unlock": False,
}

SCREEN = {
    "executed_scene_ids": ["open-n16-seed01", "perfect_maze-n16-seed01"],
    "not_executed_scene_ids": ["fractal_medium-n16-seed01"],
    "ordered_scene_ids": [
        "open-n16-seed01",
        "perfect_maze-n16-seed01",
        "fractal_medium-n16-seed01",
    ],
    "status": "stopped-on-first-task-failure",
    "stop_reason": "perfect_maze-n16-seed01-pair-collision",
    "stopped_on_first_task_failure": True,
}

SCENE_EXPECTATIONS = {
    "open-n16-seed01": {
        "classification": {
            "controller_run_valid": True,
            "diagnostic_evidence_valid": True,
            "fail_stop_trigger": False,
            "independent_pair_geometry_state": "clear",
            "independent_task_valid": False,
            "screen_result": "continue-to-next-diagnostic-scene",
            "task_validity_limit": "obstacle-safety-not-independently-reconstructed",
        },
        "geometry": {
            "colliding_robot_pairs": [],
            "collision_event_count": 0,
            "minimum_pair_clearance_m": 1.9062499972507743,
            "nonpositive_pair_sweep_count": 0,
            "pair_collision_exposure_seconds": 0.0,
            "swept_pair_evaluation_count": 12000,
        },
        "metrics": {
            "aligned_frame_count": 200,
            "aligned_robot_frame_count": 3200,
            "discordant_robot_frame_count": 0,
            "goals_reached": 0,
            "goals_total": 16,
            "max_abs_position_delta_m": 0.0,
            "max_abs_wrapped_heading_delta_rad": 0.0,
            "minimum_obstacle_clearance_m": 2.8788498561775553,
            "minimum_pair_clearance_m": 1.9062499972507743,
            "obstacle_collision_events": 0,
            "obstacle_collision_exposure_seconds": 0.0,
            "pair_collision_events": 0,
            "pair_collision_exposure_seconds": 0.0,
            "paper_metrics_equal": True,
            "semantic_trace_equal": True,
            "summary_parity_fields_equal": True,
            "total_path_length_m": 305.7078701365022,
        },
        "root": "results/simulator-demo/v1/visual-diagnostic/calibration-v2/open/n-16/seed-01",
    },
    "perfect_maze-n16-seed01": {
        "classification": {
            "controller_run_valid": True,
            "diagnostic_evidence_valid": True,
            "fail_stop_trigger": True,
            "independent_pair_geometry_state": "pair-collision",
            "independent_task_valid": False,
            "screen_result": "fail-stop-task-invalid",
            "task_validity_limit": "strict-noncontact-pair-predicate-violated",
        },
        "geometry": {
            "colliding_robot_pairs": [[5, 11]],
            "collision_event_count": 1,
            "event_entry_sweep": {"start_tick": 80, "u": 0.854063969289},
            "event_exit_sweep": {"start_tick": 92, "u": 0.827167188695},
            "minimum_clearance_sweep": {
                "clearance_m": -1.4114548777238176,
                "start_tick": 87,
                "u": 0.940505155646,
            },
            "minimum_pair_clearance_m": -1.4114548777238176,
            "nonpositive_pair_sweep_count": 13,
            "pair_collision_exposure_seconds": 1.1973103397819445,
            "swept_pair_evaluation_count": 12000,
        },
        "metrics": {
            "aligned_frame_count": 200,
            "aligned_robot_frame_count": 3200,
            "discordant_robot_frame_count": 0,
            "goals_reached": 0,
            "goals_total": 16,
            "max_abs_position_delta_m": 0.0,
            "max_abs_wrapped_heading_delta_rad": 0.0,
            "minimum_obstacle_clearance_m": 0.6878032821211171,
            "minimum_pair_clearance_m": -1.4114548777238176,
            "obstacle_collision_events": 0,
            "obstacle_collision_exposure_seconds": 0.0,
            "pair_collision_events": 1,
            "pair_collision_exposure_seconds": 1.1973103397819445,
            "paper_metrics_equal": True,
            "semantic_trace_equal": True,
            "summary_parity_fields_equal": True,
            "total_path_length_m": 254.85184415609922,
        },
        "root": "results/simulator-demo/v1/visual-diagnostic/calibration-v2/perfect_maze/n-16/seed-01",
    },
}


class RecordError(RuntimeError):
    """The frozen screen record or its live evidence does not match."""


def _reject_constant(value: str) -> None:
    raise RecordError(f"non-finite JSON constant is forbidden: {value}")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RecordError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _strict_json(raw: bytes) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecordError("input is not strict UTF-8 JSON") from exc


def _canonical_bytes(value: Any) -> bytes:
    try:
        return (json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        ) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise RecordError("value is not canonical JSON") from exc


def _json_exact(observed: Any, expected: Any) -> bool:
    if type(observed) is not type(expected):
        return False
    if type(expected) is dict:
        return (
            set(observed) == set(expected)
            and all(_json_exact(observed[key], expected[key]) for key in expected)
        )
    if type(expected) is list:
        return (
            len(observed) == len(expected)
            and all(_json_exact(left, right) for left, right in zip(observed, expected))
        )
    return observed == expected


def _relative_path(value: Any) -> str:
    if type(value) is not str or not value or "\\" in value or "\x00" in value:
        raise RecordError("path is not canonical repository-relative text")
    pure = PurePosixPath(value)
    if pure.is_absolute() or pure.as_posix() != value or any(
        part in ("", ".", "..") for part in pure.parts
    ):
        raise RecordError("path is not canonical repository-relative text")
    return value


def _validate_record(record: Any) -> dict[str, Any]:
    top_keys = {
        "authorization",
        "basis",
        "global_epoch_state",
        "recommended_screen",
        "scenes",
        "schema_version",
    }
    if type(record) is not dict or set(record) != top_keys:
        raise RecordError("record root shape differs")
    if record["schema_version"] != SCHEMA:
        raise RecordError("record schema differs")
    if not _json_exact(record["authorization"], AUTHORIZATION):
        raise RecordError("authorization literals differ")
    expected_basis = {
        "execution_checkpoint_commit": CHECKPOINT,
        "execution_checkpoint_tree": CHECKPOINT_TREE,
        "observed_date": "2026-08-11",
        "plan": {
            "bytes": 206516,
            "path": "comparison/plans/simulator-demo/v1/visual-diagnostic-calibration-v2/plan.json",
            "plan_id": "visual-diagnostic-calibration-v2",
            "sha256": PLAN_SHA256,
        },
    }
    if not _json_exact(record["basis"], expected_basis):
        raise RecordError("basis differs")
    expected_global = {
        "authenticated_completed_scene_count": 52,
        "execution_lease_expected_state": "absent",
        "execution_lease_path": "results/simulator-demo/v1/visual-diagnostic/.calibration-v2-execution.lease",
        "partial_or_unknown_outputs_detected": False,
        "registered_scene_count": 250,
    }
    if not _json_exact(record["global_epoch_state"], expected_global):
        raise RecordError("global epoch state differs")
    if not _json_exact(record["recommended_screen"], SCREEN):
        raise RecordError("fail-stop screen literals differ")
    if type(record["scenes"]) is not list or len(record["scenes"]) != 2:
        raise RecordError("scene registry must contain exactly two executed scenes")
    if [item.get("scene_id") for item in record["scenes"]] != SCREEN["executed_scene_ids"]:
        raise RecordError("scene registry order differs")
    for scene in record["scenes"]:
        if type(scene) is not dict or set(scene) != {
            "classification", "files", "geometry", "metrics", "root", "scene_id", "tree"
        }:
            raise RecordError("scene shape differs")
        scene_id = scene["scene_id"]
        expected = SCENE_EXPECTATIONS.get(scene_id)
        if expected is None:
            raise RecordError("unregistered scene")
        for key in ("classification", "geometry", "metrics", "root"):
            if not _json_exact(scene[key], expected[key]):
                raise RecordError(f"{scene_id} {key} differs")
        if not _json_exact(scene["tree"], {
            "directory_count": 3,
            "directory_mode": "0555",
            "directories": ["", "optimized", "reference"],
            "file_count": 15,
            "file_mode": "0444",
        }):
            raise RecordError(f"{scene_id} tree contract differs")
        if type(scene["files"]) is not list or len(scene["files"]) != 15:
            raise RecordError(f"{scene_id} file inventory differs")
        seen: list[str] = []
        for item in scene["files"]:
            if type(item) is not dict or set(item) != {"bytes", "path", "sha256"}:
                raise RecordError("file identity shape differs")
            path = _relative_path(item["path"])
            if type(item["bytes"]) is not int or item["bytes"] < 0:
                raise RecordError("file byte count differs")
            if (
                type(item["sha256"]) is not str
                or len(item["sha256"]) != 64
                or any(char not in "0123456789abcdef" for char in item["sha256"])
            ):
                raise RecordError("file digest differs")
            seen.append(path)
        if seen != sorted(seen) or len(set(seen)) != 15:
            raise RecordError("file inventory order or uniqueness differs")
    return record


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_json(path: Path) -> Any:
    return _strict_json(path.read_bytes())


def _sweep(
    left_before: list[float],
    left_after: list[float],
    right_before: list[float],
    right_after: list[float],
    radius_sum: float,
) -> tuple[float, float, float, float]:
    d0x = float(left_before[0]) - float(right_before[0])
    d0y = float(left_before[1]) - float(right_before[1])
    dvx = (
        float(left_after[0]) - float(left_before[0])
        - float(right_after[0]) + float(right_before[0])
    )
    dvy = (
        float(left_after[1]) - float(left_before[1])
        - float(right_after[1]) + float(right_before[1])
    )
    a = dvx * dvx + dvy * dvy
    if a == 0.0:
        u_min = 0.0
    else:
        u_min = min(1.0, max(0.0, -(d0x * dvx + d0y * dvy) / a))
    closest_x = d0x + u_min * dvx
    closest_y = d0y + u_min * dvy
    clearance = math.hypot(closest_x, closest_y) - radius_sum

    if a == 0.0:
        fraction = 1.0 if clearance <= 0.0 else 0.0
        return clearance, u_min, 0.0, fraction
    b = 2.0 * (d0x * dvx + d0y * dvy)
    c = d0x * d0x + d0y * d0y - radius_sum * radius_sum
    discriminant = b * b - 4.0 * a * c
    if discriminant < 0.0:
        return clearance, u_min, 0.0, 0.0
    root = math.sqrt(max(0.0, discriminant))
    entry = max(0.0, (-b - root) / (2.0 * a))
    exit_ = min(1.0, (-b + root) / (2.0 * a))
    if exit_ <= entry:
        return clearance, u_min, 0.0, 0.0
    return clearance, u_min, entry, exit_ - entry


def _trace_geometry(path: Path) -> dict[str, Any]:
    lines = path.read_bytes().splitlines()
    if len(lines) != 201:
        raise RecordError("trace must contain one header and 200 frames")
    frames = [_strict_json(line) for line in lines[1:]]
    effective_timestep_seconds = (
        float(frames[1]["simulated_time_seconds"])
        - float(frames[0]["simulated_time_seconds"])
    )
    if effective_timestep_seconds != 0.10000000149011612:
        raise RecordError("trace effective timestep differs")
    minimum = math.inf
    minimum_pair: tuple[int, int] | None = None
    minimum_tick = -1
    minimum_u = -1.0
    nonpositive = 0
    exposure = 0.0
    evaluations = 0
    intervals: dict[tuple[int, int], list[tuple[int, float, float]]] = {}
    for index in range(0, len(frames), 2):
        before = frames[index]
        after = frames[index + 1]
        if before["phase"] != "pre_update" or after["phase"] != "post_update":
            raise RecordError("trace phase order differs")
        start_tick = before["tick"]
        if after["tick"] != start_tick + 1:
            raise RecordError("trace tick order differs")
        timestep_seconds = (
            float(after["simulated_time_seconds"])
            - float(before["simulated_time_seconds"])
        )
        if not 0.099999 <= timestep_seconds <= 0.100001:
            raise RecordError("trace timestep differs")
        before_robots = before["robots"]
        after_robots = after["robots"]
        if [item["rid"] for item in before_robots] != list(range(16)):
            raise RecordError("trace robot IDs differ")
        if [item["rid"] for item in after_robots] != list(range(16)):
            raise RecordError("trace robot IDs differ")
        for left in range(16):
            for right in range(left + 1, 16):
                radius_sum = (
                    float(before_robots[left]["radius_m"])
                    + float(before_robots[right]["radius_m"])
                )
                if radius_sum != 2.0:
                    raise RecordError("trace robot radius differs")
                clearance, u_min, entry, fraction = _sweep(
                    before_robots[left]["state"],
                    after_robots[left]["state"],
                    before_robots[right]["state"],
                    after_robots[right]["state"],
                    radius_sum,
                )
                evaluations += 1
                if clearance < minimum:
                    minimum = clearance
                    minimum_pair = (left, right)
                    minimum_tick = start_tick
                    minimum_u = u_min
                if clearance <= 0.0:
                    nonpositive += 1
                if fraction > 0.0:
                    intervals.setdefault((left, right), []).append(
                        (start_tick, entry, entry + fraction)
                    )
                    exposure += fraction * effective_timestep_seconds
    pairs = sorted([list(pair) for pair in intervals])
    result: dict[str, Any] = {
        "colliding_robot_pairs": pairs,
        "minimum_pair": list(minimum_pair) if minimum_pair is not None else None,
        "minimum_pair_clearance_m": minimum,
        "minimum_start_tick": minimum_tick,
        "minimum_u": minimum_u,
        "nonpositive_pair_sweep_count": nonpositive,
        "pair_collision_exposure_seconds": exposure,
        "swept_pair_evaluation_count": evaluations,
    }
    if pairs:
        only = tuple(pairs[0])
        pair_intervals = intervals[only]
        result["entry"] = [pair_intervals[0][0], pair_intervals[0][1]]
        result["exit"] = [pair_intervals[-1][0], pair_intervals[-1][2]]
    return result


class N16EngineeringScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.record_raw = RECORD_PATH.read_bytes()
        cls.record = _validate_record(_strict_json(cls.record_raw))

    def test_01_record_identity_canonicality_and_shape(self) -> None:
        self.assertEqual(len(self.record_raw), EXPECTED_RECORD_BYTES)
        self.assertEqual(_sha256(self.record_raw), EXPECTED_RECORD_SHA256)
        self.assertEqual(self.record_raw, _canonical_bytes(self.record))

    def test_02_authority_and_fail_stop_mutations_reject(self) -> None:
        mutations = [
            lambda value: value["authorization"].__setitem__("timing_unlock", True),
            lambda value: value["authorization"].__setitem__("scientific_evidence", True),
            lambda value: value["authorization"].__setitem__(
                "further_visual_execution_permitted_by_this_checkpoint", True
            ),
            lambda value: value["recommended_screen"].__setitem__(
                "stopped_on_first_task_failure", False
            ),
            lambda value: value["recommended_screen"]["not_executed_scene_ids"].clear(),
            lambda value: value["scenes"][0]["classification"].__setitem__(
                "independent_task_valid", True
            ),
            lambda value: value["scenes"][1]["classification"].__setitem__(
                "fail_stop_trigger", False
            ),
            lambda value: value["scenes"][1]["geometry"].__setitem__(
                "collision_event_count", 0
            ),
            lambda value: value["global_epoch_state"].__setitem__(
                "partial_or_unknown_outputs_detected", True
            ),
        ]
        for index, mutation in enumerate(mutations):
            changed = copy.deepcopy(self.record)
            mutation(changed)
            with self.subTest(index=index), self.assertRaises(RecordError):
                _validate_record(changed)

    def test_03_plan_preflight_and_deferred_destination(self) -> None:
        plan_raw = PLAN_PATH.read_bytes()
        self.assertEqual(len(plan_raw), 206516)
        self.assertEqual(_sha256(plan_raw), PLAN_SHA256)
        plan = _strict_json(plan_raw)
        self.assertEqual(plan["plan_id"], "visual-diagnostic-calibration-v2")
        self.assertEqual(len(plan["scenes"]), 250)
        selected = {
            item["scene_id"]: item
            for item in plan["scenes"]
            if item["scene_id"] in SCREEN["ordered_scene_ids"]
        }
        self.assertEqual(list(selected), SCREEN["ordered_scene_ids"])
        self.assertFalse(DEFERRED_ROOT.exists())
        self.assertFalse(LEASE_PATH.exists())
        for scene in self.record["scenes"]:
            root = ROOT / scene["root"]
            preflight = _read_json(root / "preflight.json")
            manifest = _read_json(root / "manifest.json")
            self.assertEqual(preflight["selected_scene_id"], scene["scene_id"])
            self.assertEqual(preflight["execution_checkpoint_commit"], CHECKPOINT)
            self.assertEqual(preflight["plan_sha256"], PLAN_SHA256)
            self.assertEqual(manifest["scene"]["scene_id"], scene["scene_id"])
            self.assertEqual(manifest["scene"]["robot_count"], 16)
            self.assertEqual(manifest["scene"]["seed"], 1)
            self.assertEqual(manifest["plan_sha256"], PLAN_SHA256)
            self.assertEqual((root / "frozen_plan.json").read_bytes(), plan_raw)

    def test_04_full_scene_trees_are_exact_sealed_and_bound(self) -> None:
        for scene in self.record["scenes"]:
            root = ROOT / scene["root"]
            self.assertEqual(os.path.realpath(root), os.fspath(root.absolute()))
            actual_dirs = [""]
            actual_files: dict[str, Path] = {}
            for path in root.rglob("*"):
                self.assertFalse(path.is_symlink(), path)
                relative = path.relative_to(root).as_posix()
                if path.is_dir():
                    actual_dirs.append(relative)
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o555, path)
                elif path.is_file():
                    actual_files[relative] = path
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o444, path)
                else:
                    self.fail(f"unexpected scene entry type: {path}")
            self.assertEqual(sorted(actual_dirs), scene["tree"]["directories"])
            expected_files = {item["path"]: item for item in scene["files"]}
            self.assertEqual(set(actual_files), set(expected_files))
            for relative, path in actual_files.items():
                raw = path.read_bytes()
                expected = expected_files[relative]
                self.assertEqual(len(raw), expected["bytes"], path)
                self.assertEqual(_sha256(raw), expected["sha256"], path)

    def test_05_manifest_semantics_and_frame_payload_parity(self) -> None:
        for scene in self.record["scenes"]:
            root = ROOT / scene["root"]
            manifest = _read_json(root / "manifest.json")
            self.assertEqual(manifest["status"], "complete")
            self.assertIs(manifest["diagnostic_only"], True)
            self.assertIs(manifest["compute_claim"], False)
            self.assertIs(manifest["timing_unlock"], False)
            self.assertIs(manifest["paper_replication_evidence"], False)
            comparison = manifest["pair_validation"]
            diagnostics = comparison["diagnostics"]
            self.assertIs(comparison["semantic_trace_equal"], True)
            self.assertIs(comparison["paper_metrics_equal"], True)
            self.assertIs(comparison["summary_parity_fields_equal"], True)
            self.assertIs(diagnostics["run_valid"], True)
            expected = scene["metrics"]
            for key in (
                "aligned_frame_count",
                "aligned_robot_frame_count",
                "discordant_robot_frame_count",
                "goals_reached",
                "goals_total",
                "max_abs_position_delta_m",
                "max_abs_wrapped_heading_delta_rad",
                "minimum_obstacle_clearance_m",
                "minimum_pair_clearance_m",
                "obstacle_collision_events",
                "obstacle_collision_exposure_seconds",
                "pair_collision_events",
                "pair_collision_exposure_seconds",
                "total_path_length_m",
            ):
                self.assertTrue(_json_exact(diagnostics[key], expected[key]), key)
            reference_lines = (root / "reference/trace.jsonl").read_bytes().splitlines()
            optimized_lines = (root / "optimized/trace.jsonl").read_bytes().splitlines()
            self.assertEqual(reference_lines[1:], optimized_lines[1:])

    def test_06_independent_swept_pair_geometry(self) -> None:
        observed: dict[str, dict[str, Any]] = {}
        for scene in self.record["scenes"]:
            observed[scene["scene_id"]] = _trace_geometry(
                ROOT / scene["root"] / "reference/trace.jsonl"
            )
        open_geometry = observed["open-n16-seed01"]
        self.assertEqual(open_geometry["swept_pair_evaluation_count"], 12000)
        self.assertEqual(open_geometry["colliding_robot_pairs"], [])
        self.assertEqual(open_geometry["nonpositive_pair_sweep_count"], 0)
        self.assertAlmostEqual(open_geometry["minimum_pair_clearance_m"], 1.9062499972507743, places=12)
        self.assertEqual(open_geometry["minimum_pair"], [2, 11])

        maze = observed["perfect_maze-n16-seed01"]
        self.assertEqual(maze["swept_pair_evaluation_count"], 12000)
        self.assertEqual(maze["colliding_robot_pairs"], [[5, 11]])
        self.assertEqual(maze["nonpositive_pair_sweep_count"], 13)
        self.assertEqual(maze["minimum_pair"], [5, 11])
        self.assertEqual(maze["minimum_start_tick"], 87)
        self.assertAlmostEqual(maze["minimum_u"], 0.940505155646, places=11)
        self.assertAlmostEqual(maze["minimum_pair_clearance_m"], -1.4114548777238176, places=12)
        self.assertAlmostEqual(maze["pair_collision_exposure_seconds"], 1.1973103397819445, places=12)
        self.assertEqual(maze["entry"][0], 80)
        self.assertAlmostEqual(maze["entry"][1], 0.854063969289, places=11)
        self.assertEqual(maze["exit"][0], 92)
        self.assertAlmostEqual(maze["exit"][1], 0.827167188695, places=11)

    def test_07_epoch_inventory_has_52_complete_registered_scenes(self) -> None:
        plan = _read_json(PLAN_PATH)
        planned = {item["output_relpath"] for item in plan["scenes"]}
        manifests = sorted(RESULTS_ROOT.glob("*/*/seed-*/manifest.json"))
        completed = {
            path.parent.relative_to(RESULTS_ROOT).as_posix()
            for path in manifests
        }
        self.assertEqual(len(completed), 52)
        self.assertTrue(completed <= planned)
        for path in RESULTS_ROOT.rglob("*"):
            relative = path.relative_to(RESULTS_ROOT)
            parts = relative.parts
            if path.is_symlink():
                self.fail(f"epoch contains symlink: {path}")
            if len(parts) >= 3 and parts[2].startswith("seed-"):
                scene_root = PurePosixPath(*parts[:3]).as_posix()
                if path.is_file() or len(parts) > 3:
                    self.assertIn(scene_root, completed, path)
            elif path.is_file():
                self.fail(f"epoch file is outside a completed scene: {path}")

    def test_08_suite_has_no_process_network_or_write_surface(self) -> None:
        tree = ast.parse(Path(__file__).read_text(encoding="utf-8"))
        forbidden_imports = {"socket", "subprocess", "urllib", "http", "requests"}
        forbidden_calls = {
            "chmod", "mkdir", "open", "remove", "rename", "replace", "rmdir",
            "spawn", "system", "unlink", "write", "write_bytes", "write_text",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.assertFalse({alias.name.split(".")[0] for alias in node.names} & forbidden_imports)
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn((node.module or "").split(".")[0], forbidden_imports)
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, forbidden_calls)
                elif isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, forbidden_calls)


if __name__ == "__main__":
    unittest.main()
