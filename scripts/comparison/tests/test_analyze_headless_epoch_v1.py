#!/usr/bin/env python3
"""Closed synthetic regressions for the calibration-only headless analyzer."""

from __future__ import annotations

import json
import math
import binascii
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import tempfile
import unittest
from unittest import mock
import zlib


ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / "scripts/comparison/analyze_headless_epoch_v1.py"


def _load_raw():
    raw = SOURCE.read_bytes()
    namespace = {
        "__builtins__": __builtins__,
        "__file__": str(SOURCE),
        "__name__": "_bound_analyze_headless_epoch_v1_test",
    }
    exec(compile(raw, str(SOURCE), "exec"), namespace, namespace)
    return namespace


A = _load_raw()


def timing_row(
    *,
    family: str,
    seed: int,
    count: int,
    method: str,
    repetition: int,
    cpu: float | None,
    wall: float | None = None,
    rss: float | None = None,
    status: str = "completed",
    suffix: str = "",
):
    scene = f"{family}-n{count}-seed{seed:02d}"
    measurement = None
    if cpu is not None:
        observed_wall = wall if wall is not None else cpu
        observed_rss = rss if rss is not None else 100.0
        measurement = {
            "peak_rss_mib": observed_rss,
            "peak_rss_raw": int(observed_rss * 1024 * 1024),
            "peak_rss_raw_unit": "bytes",
            "process_group_contained": True,
            "returncode": 0,
            "schema_version": A["PROCESS_SCHEMA"],
            "stderr_bytes_observed": 0,
            "stderr_truncated": False,
            "stdout_bytes_observed": 0,
            "stdout_truncated": False,
            "system_cpu_seconds": cpu * 0.2,
            "termination_events": [],
            "timed_out": False,
            "user_cpu_seconds": cpu * 0.8,
            "wall_seconds": observed_wall,
        }
    return {
        "attempt_id": f"{scene}-{method}-r{repetition}{suffix}",
        "cluster_id": f"map-{family}+mission-{seed}",
        "digest_join_valid": True,
        "family": family,
        "input_id": f"input-{scene}",
        "measurement": measurement,
        "method": method,
        "mission_seed": seed,
        "positions_join_valid": True,
        "repetition": repetition,
        "robot_count": count,
        "scene_id": scene,
        "status": status,
        "task_valid": True,
    }


def minimal_report():
    return {
        "analysis_contract": {
            "analyzer_contract": dict(A["ANALYZER_CONTRACT"]),
            "bootstrap_cluster": "family-by-mission-bank",
            "bootstrap_draws": 10_000,
            "count_relationship_within_cluster": "nested-prefix-repeated-observation",
            "holm_family_count": 5,
            "inference_unit": "fixed-tested-family-map-mission-bank",
            "timing_cell_summary": "median-of-three-within-method-scene",
        },
        "calibration_recommendations": {
            "authority": {
                "benchmark_claim_ready": False,
                "capacity_claim_ready": False,
                "execution_authorized": False,
                "validation_unlock": False,
            },
            "family_recommendations": [],
            "recommendation_available": False,
            "rule_id": A["RECOMMENDATION_RULE_ID"],
            "rule_text": A["RECOMMENDATION_RULE_TEXT"],
        },
        "classification": dict(A["LOCKED_FALSE_FLAGS"]),
        "fidelity": {
            "all_pairs_valid": False,
            "pair_count": 0,
            "pair_rows": [],
            "valid_pair_count": 0,
        },
        "input_authentication": {
            "all_declared_files_authenticated": True,
            "analyzer_source_sha256": "1" * 64,
            "authenticated_bytes": 1,
            "authenticated_file_count": 1,
            "freezer_source_sha256": "2" * 64,
            "geometry_source_sha256": "3" * 64,
            "input_record_count": 9,
            "plan_validated_by_bound_freezer": True,
        },
        "inventory": {
            "aborted": False,
            "all_attempts_completed": False,
            "attempt_category_counts": {},
            "complete": False,
            "expected_attempt_count": 0,
            "failure_reasons": [],
            "ledger_identity": {
                "bytes": 1,
                "event_count": 1,
                "sha256": "4" * 64,
                "valid_prefix_or_complete_ledger": True,
            },
            "missing_output_count": 0,
            "no_attempt_filtering": True,
            "observed_result_count": 0,
            "retained_attempt_rows": [],
            "session_identity": {"bytes": 1, "path": "session.json", "sha256": "5" * 64},
        },
        "operations": dict(A["ANALYSIS_OPERATIONS"]),
        "phase": A["PHASE"],
        "plan_identity": {"bytes": 1, "path": "plan.json", "sha256": "1" * 64},
        "positions_replay": {
            "all_pairs_valid": False,
            "pair_count": 0,
            "pair_rows": [],
            "valid_pair_count": 0,
        },
        "schema_version": A["ANALYSIS_SCHEMA"],
        "scope": {
            "causal_family_claim_permitted": False,
            "fixed_map_per_family": True,
            "generalization_to_untested_counts_permitted": False,
            "legacy_evidence_admitted": False,
            "map_or_family_population_claim_permitted": False,
            "result_role": "calibration-bank-diagnostic-only",
            "validation_or_audit_evidence_admitted": False,
        },
        "status": {
            "analysis_completed": True,
            "benchmark_claim_ready": False,
            "calibration_candidate_recommendation_only": True,
            "confirmatory_claim_ready": False,
            "execution_unlock": False,
            "timing_unlock": False,
            "validation_unlock": False,
        },
        "task_validity": {
            "all_scenes_valid": False,
            "scene_count": 0,
            "scene_rows": [],
            "valid_scene_count": 0,
        },
        "timing": {
            "analysis_available": False,
            "bootstrap_draws": 10_000,
            "collapse": {
                "analysis_available": False,
                "cell_collapse": "median-of-three-within-method-scene",
                "failure_reasons": [],
                "median_rows": [],
                "no_outlier_deletion": True,
                "paired_rows": [],
                "retained_rows": [],
            },
            "failure_reasons": [],
            "paired_ratio_analysis": None,
            "timing_from_observer_disabled_only": True,
        },
        "track_id": A["TRACK_ID"],
    }


def complete_rows(
    *,
    counts=(8,),
    seeds=(1, 2),
    optimized_factor=0.5,
):
    rows = []
    for family_index, family in enumerate(A["FAMILIES"]):
        for count in counts:
            for seed in seeds:
                base = float(10 + family_index + count / 10 + seed)
                for method, factor in (("reference", 1.0), ("optimized", optimized_factor)):
                    for repetition, perturbation in ((1, 0.9), (2, 1.0), (3, 1.1)):
                        rows.append(
                            timing_row(
                                family=family,
                                seed=seed,
                                count=count,
                                method=method,
                                repetition=repetition,
                                cpu=base * factor * perturbation,
                                wall=base * factor * perturbation * 1.1,
                                rss=100.0 * factor * perturbation,
                            )
                        )
    return rows


def binary_png(size: int, occupied: set[tuple[int, int]]) -> bytes:
    pixels = bytearray([255]) * (size * size)
    for x, y in occupied:
        pixels[y * size + x] = 0

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", binascii.crc32(payload, binascii.crc32(kind)) & 0xFFFFFFFF)
        )

    raw = b"".join(
        b"\x00" + bytes(pixels[row * size : (row + 1) * size])
        for row in range(size)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def positions_robot(rid: int, x: float, y: float):
    state = [x, y, 0.0, 0.0, 0.0, 0.0]
    return {
        "bearing": 0.0,
        "current_waypoint": [x, y, 0.0, 0.0, 0.0, 0.0],
        "final_goal": [x, y],
        "goal_reached": False,
        "radius_m": 0.25,
        "rid": rid,
        "state": state,
        "state_hex": [struct.pack(">d", item).hex() for item in state],
    }


def fixture_resolved_config(
    *, robot_count: int, timesteps: int, radius: float, seed: int
):
    value = A["_expected_runtime_resolved_config"](
        config_path="comparison/plans/simulator-demo/v1/headless-epoch-1/configs/fixture.yaml",
        map_path="comparison/environments/open/calibration/map.png",
        scenario_path="comparison/plans/simulator-demo/v1/headless-epoch-1/missions/fixture.json",
        scene_id="fixture-scene",
        robot_count=robot_count,
        mission_seed=seed,
    )
    value["max_timesteps"] = timesteps
    value["robot_radius_m"] = radius
    value["factorgraph_layers"][0]["numeric_params"]["ROBOT_RADIUS"] = radius
    return value


def trace_frame(tick: int, phase: str, positions: list[tuple[float, float]]):
    return {
        "metrics": {
            "all_goals_reached": False,
            "cumulative_invalid_geometry_evaluations": 0,
            "cumulative_obstacle_collision_exposure_seconds": 0.0,
            "cumulative_pair_collision_exposure_seconds": 0.0,
            "frame_min_obstacle_clearance_m": None,
            "frame_min_pair_clearance_m": None,
            "goals_reached": 0,
            "obstacle_collisions": 0,
            "pair_collisions": 0,
            "robot_count": len(positions),
            "updated_this_frame": phase == "post_update",
        },
        "phase": phase,
        "rng_state": {
            "integer_mt19937": "state",
            "normal_mt19937": "state",
            "uniform_mt19937": "state",
        },
        "robots": [positions_robot(rid, x, y) for rid, (x, y) in enumerate(positions)],
        "simulated_time_seconds": A["_runtime_simulated_time"](
            tick, A["RUNTIME_TIMESTEP_SECONDS"]
        ),
        "simulator_state": {
            "iterations": tick * 2,
            "last_created_robots": [],
            "last_junction_creation_clock": -1,
            "mean_number_of_neighbours_hex": "0" * 16,
            "mean_number_of_neighbours_time_hex": "0" * 16,
            "next_robot_id": len(positions),
            "robot_group_clock": tick,
            "run_flag": True,
            "simulation_mode": 1,
        },
        "tick": tick,
        "type": "frame",
    }


def positions_trace_bytes(path: list[tuple[float, float]]) -> bytes:
    timesteps = len(path) - 1
    if timesteps < 1:
        raise ValueError("positions fixture needs at least two boundary states")
    header = {
        "metadata": {"fixture": "positions"},
        "observer": {
            **A["_expected_observer"]("positions"),
        },
        "resolved_config": fixture_resolved_config(
            robot_count=1, timesteps=timesteps, radius=0.25, seed=1
        ),
        "schema_version": "dancers-comparison-trace-v3",
        "type": "header",
    }
    records = [header]
    for index in range(timesteps):
        records.append(trace_frame(index, "pre_update", [path[index]]))
        records.append(trace_frame(index + 1, "post_update", [path[index + 1]]))
    records[-1]["simulator_state"]["run_flag"] = False
    return b"".join(A["canonical_json_bytes"](record) for record in records)


def mini_measurement(cpu: float = 1.0):
    return timing_row(
        family="open",
        seed=1,
        count=8,
        method="reference",
        repetition=1,
        cpu=cpu,
    )["measurement"]


def open_descriptor_count() -> int:
    for directory in (Path("/dev/fd"), Path("/proc/self/fd")):
        if directory.is_dir():
            return len(os.listdir(directory))
    raise unittest.SkipTest("this runtime exposes no descriptor inventory")


def mini_session_fixture(root: Path):
    plan_sha = "a" * 64
    input_id = "b" * 64
    scene = {
        "family": "open",
        "input_id": input_id,
        "robot_count": 8,
        "scenario": {"sha256": "c" * 64},
        "scene_id": "calibration-open-n-8-seed-01",
        "seed": 1,
    }
    slots = []
    for index, method in enumerate(("reference", "optimized")):
        slot_id = hashlib.sha256(f"slot-{index}".encode()).hexdigest()
        slots.append(
            {
                "binary_key": f"{method}-timing",
                "binary_sha256": hashlib.sha256(f"binary-{method}".encode()).hexdigest(),
                "config": {"sha256": "d" * 64},
                "controller_timesteps": 100,
                "destination": (
                    f"calibration/headless_timing/open/n-8/seed-01/rep-1/{index}-{method}"
                ),
                "effective_runtime_raster_sha256": "e" * 64,
                "family": "open",
                "input_id": input_id,
                "kind": "measured",
                "map_sha256": "f" * 64,
                "method": method,
                "normalized_config_sha256": "1" * 64,
                "outputs": [
                    "attempt.json",
                    "provenance.json",
                    "resource.json",
                    "stdout.txt",
                    "stderr.txt",
                ],
                "pattern": "ABA",
                "repetition": 1,
                "robot_count": 8,
                "run_class": "headless_timing",
                "scene_id": scene["scene_id"],
                "seed": 1,
                "slot_id": slot_id,
                "timed": True,
                "timestep_seconds": 0.1,
                "trace_detail": None,
            }
        )
    empty = lambda name, timed: {
        "measured": [],
        "measured_process_count": 0,
        "paired_scene_repetition_count": 0,
        "run_class": name,
        "timed": timed,
    }
    plan = {
        "controls": {"omp_threads": 1},
        "input_records": {
            "runtime_attestation": {
                "content": {
                    "products": {
                        "reference-timing": {"algorithm_revision": "2" * 40},
                        "optimized-timing": {"algorithm_revision": "3" * 40},
                    },
                    "pixi_lock": {"sha256": "4" * 64},
                    "source_identity": {
                        "common_patch": {"format_email_sha256": "5" * 64}
                    },
                }
            }
        },
        "scenes": [scene],
        "schedule": {
            "assignment_algorithm": {},
            "balance": {},
            "execution_order": [
                "warmups",
                "digest_fidelity",
                "positions_replay",
                "headless_timing",
            ],
            "inventories": {
                "digest_fidelity": empty("digest_fidelity", False),
                "positions_replay": empty("positions_replay", False),
                "headless_timing": {
                    "measured": slots,
                    "measured_process_count": 2,
                    "paired_scene_repetition_count": 1,
                    "run_class": "headless_timing",
                    "timed": True,
                },
            },
            "timing_gate": {},
            "warmups": {
                "process_count": 0,
                "slots": [],
                "uniqueness_key": ["binary_key", "map_sha256"],
            },
        },
    }
    authenticated = {
        "plan": plan,
        "plan_identity": {"sha256": plan_sha},
        "repository_root": root,
    }
    session_root = root.joinpath(*A["RESULT_ROOT"].parts) / "mini-session"
    results = []
    for sequence, slot in enumerate(slots, start=1):
        measurement = mini_measurement(1.0 + sequence)
        attempt = {
            "attempt_id": slot["slot_id"],
            "category": "completed",
            "input_id": input_id,
            "measurement": measurement,
            "method": slot["method"],
            "products": {},
            "repetition": 1,
            "run_class": "headless_timing",
            "schema_version": A["ATTEMPT_SCHEMA"],
            "sequence": sequence,
            "task_validity_determined": False,
        }
        results.append(attempt)
        attempt_root = session_root / "attempts" / slot["destination"]
        attempt_root.mkdir(parents=True, exist_ok=True)
        provenance = A["_expected_provenance"](slot, scene, plan_sha, plan)
        resource = {
            "measurement": measurement,
            "reason": None,
            "schema_version": A["RESOURCE_SCHEMA"],
            "timed_process_started": True,
        }
        for name, data in (
            ("attempt.json", A["canonical_json_bytes"](attempt)),
            ("provenance.json", A["canonical_json_bytes"](provenance)),
            ("resource.json", A["canonical_json_bytes"](resource)),
            ("stdout.txt", b""),
            ("stderr.txt", b""),
        ):
            (attempt_root / name).write_bytes(data)
    session = {
        "aborted": False,
        "attempt_count": 2,
        "expected_attempt_count": 2,
        "failure": None,
        "plan_sha256": plan_sha,
        "results": results,
        "schema_version": A["SESSION_SCHEMA"],
    }
    events = [{"kind": "session-start", "plan_sha256": plan_sha}]
    host = {
        "ac_power": True,
        "free_bytes": A["MIN_HOST_FREE_BYTES"],
        "free_bytes_ok": True,
        "thermal_nominal": True,
    }
    for result in results:
        events.extend(
            [
                {
                    "attempt_id": result["attempt_id"],
                    "host": host,
                    "kind": "attempt-start",
                    "sequence": result["sequence"],
                },
                {
                    "attempt_id": result["attempt_id"],
                    "category": result["category"],
                    "kind": "attempt-complete",
                    "sequence": result["sequence"],
                },
            ]
        )
    events.append({"attempt_count": 2, "kind": "session-complete"})

    def write_session(current_session, current_events):
        (session_root / "session.json").write_bytes(
            A["canonical_json_bytes"](current_session)
        )
        ledger = b"".join(
            A["canonical_json_bytes"](
                {
                    "event": event,
                    "index": index,
                    "schema_version": A["LEDGER_SCHEMA"],
                }
            )
            for index, event in enumerate(current_events, start=1)
        )
        (session_root / "ledger.ndjson").write_bytes(ledger)

    write_session(session, events)
    return authenticated, session_root, session, events, write_session


class HeadlessAnalyzerPureTests(unittest.TestCase):
    def test_source_load_is_raw_and_has_no_default_write(self):
        before = set(ROOT.rglob("*headless-calibration-analysis-v1*"))
        loaded = _load_raw()
        after = set(ROOT.rglob("*headless-calibration-analysis-v1*"))
        self.assertEqual(before, after)
        self.assertEqual(loaded["ANALYSIS_SCHEMA"], "dancers-headless-calibration-analysis-v1")

    def test_analyzer_record_binds_live_source_and_plan_envelope(self):
        record = A["build_analyzer_record"](ROOT)
        raw = A["canonical_json_bytes"](record)
        envelope = {
            "bytes": len(raw),
            "content": record,
            "path": "comparison/plans/simulator-demo/v1/headless-epoch-1/records/analyzer.json",
            "schema_version": A["ANALYZER_RECORD_SCHEMA"],
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        source_raw = SOURCE.read_bytes()
        authenticated = A["authenticate_component_record"](
            raw,
            planned_record_identity=envelope,
            expected_schema=A["ANALYZER_RECORD_SCHEMA"],
            expected_contract=A["ANALYZER_CONTRACT"],
            expected_source_path="scripts/comparison/analyze_headless_epoch_v1.py",
            source_raw=source_raw,
            label="analyzer record",
        )
        self.assertEqual(authenticated, record)
        mutated = dict(envelope)
        mutated["sha256"] = "0" * 64
        with self.assertRaisesRegex(A["HeadlessAnalysisError"], "identity differs"):
            A["authenticate_component_record"](
                raw,
                planned_record_identity=mutated,
                expected_schema=A["ANALYZER_RECORD_SCHEMA"],
                expected_contract=A["ANALYZER_CONTRACT"],
                expected_source_path="scripts/comparison/analyze_headless_epoch_v1.py",
                source_raw=source_raw,
                label="analyzer record",
            )

    def test_repository_reads_are_descriptor_rooted_and_reject_symlink_ancestors(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            real_parent = root / "real"
            real_parent.mkdir()
            (real_parent / "artifact.bin").write_bytes(b"bound")
            (root / "alias").symlink_to(real_parent, target_is_directory=True)
            self.assertEqual(
                A["_repository_file_bytes"](
                    root,
                    A["PurePosixPath"]("real/artifact.bin"),
                    5,
                    "descriptor fixture",
                ),
                b"bound",
            )
            with self.assertRaises(A["HeadlessAnalysisError"]):
                A["_repository_file_bytes"](
                    root,
                    A["PurePosixPath"]("alias/artifact.bin"),
                    5,
                    "symlink fixture",
                )

    def test_bound_freezer_is_raw_compiled_and_contract_exact(self):
        path = ROOT / "scripts/comparison/freeze_headless_epoch_v2.py"
        raw = path.read_bytes()
        namespace = A["_raw_exec_namespace"](
            raw,
            identity={
                "bytes": len(raw),
                "path": "scripts/comparison/freeze_headless_epoch_v2.py",
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
            expected_path=A["FREEZER_SOURCE_PATH"],
            required_names={"EPOCH_SCHEMA", "ANALYZER_CONTRACT", "validate_epoch_bytes"},
            label="freezer",
        )
        self.assertEqual(namespace["EPOCH_SCHEMA"], A["PLAN_SCHEMA"])
        self.assertEqual(namespace["ANALYZER_CONTRACT"], A["ANALYZER_CONTRACT"])
        self.assertEqual(namespace["RUN_CLASSES"], A["RUN_CLASSES"])
        self.assertEqual(namespace["RESULT_ROOT"], A["RESULT_ROOT"])
        self.assertEqual(namespace["FREEZER_CONTRACT"], A["FREEZER_CONTRACT"])

    def test_attempt_and_process_schemas_are_closed_and_calibration_only(self):
        attempt = {
            "attempt_id": "a" * 64,
            "category": "completed",
            "input_id": "b" * 64,
            "measurement": mini_measurement(),
            "method": "reference",
            "products": {},
            "repetition": 1,
            "run_class": "headless_timing",
            "schema_version": A["ATTEMPT_SCHEMA"],
            "sequence": 1,
            "task_validity_determined": False,
        }
        self.assertEqual(
            A["validate_attempt_record"](attempt, label="attempt"), attempt
        )
        for mutation in (
            lambda value: value.__setitem__("extra", False),
            lambda value: value.__setitem__("schema_version", "legacy-attempt-v1"),
            lambda value: value.__setitem__("task_validity_determined", True),
            lambda value: value.__setitem__("run_class", "validation"),
        ):
            changed = json.loads(json.dumps(attempt))
            mutation(changed)
            with self.assertRaises(A["HeadlessAnalysisError"]):
                A["validate_attempt_record"](changed, label="changed attempt")
        changed_measurement = json.loads(json.dumps(attempt["measurement"]))
        changed_measurement["extra"] = 1
        with self.assertRaisesRegex(A["HeadlessAnalysisError"], "schema differs"):
            A["validate_process_record"](changed_measurement, label="measurement")

    def test_canonical_json_rejects_nonfinite_and_strict_parser_rejects_duplicates(self):
        self.assertEqual(A["canonical_json_bytes"]({"b": 2, "a": 1}), b'{"a":1,"b":2}\n')
        with self.assertRaises(A["HeadlessAnalysisError"]):
            A["canonical_json_bytes"]({"bad": math.nan})
        with self.assertRaises(A["HeadlessAnalysisError"]):
            A["strict_json_bytes"](b'{"a":1,"a":2}\n', "duplicate")
        with self.assertRaises(A["HeadlessAnalysisError"]):
            A["strict_json_bytes"](b'{"a":NaN}\n', "nonfinite")

    def test_bootstrap_is_deterministic_clustered_and_requires_ten_thousand(self):
        ratios = [
            {"cluster_id": "map-a+mission-1", "ratio": 0.5},
            {"cluster_id": "map-a+mission-1", "ratio": 0.8},
            {"cluster_id": "map-a+mission-2", "ratio": 0.6},
            {"cluster_id": "map-a+mission-2", "ratio": 0.9},
        ]
        first = A["map_mission_cluster_bootstrap"](
            ratios, draws=10_000, seed_label="golden"
        )
        second = A["map_mission_cluster_bootstrap"](
            ratios, draws=10_000, seed_label="golden"
        )
        self.assertEqual(first, second)
        self.assertEqual(first["cluster_count"], 2)
        self.assertEqual(first["row_count"], 4)
        self.assertEqual(first["bootstrap"]["cluster"], "family+mission_bank")
        self.assertEqual(first["scope"], "fixed-tested-family-map-mission-banks-only")
        self.assertLess(first["ratio_ci95"][1], 1.0)
        with self.assertRaisesRegex(A["HeadlessAnalysisError"], "below its minimum"):
            A["map_mission_cluster_bootstrap"](
                ratios, draws=9_999, seed_label="too-few"
            )

    def test_holm_is_closed_over_exact_five_families(self):
        raw = {
            "open": 0.01,
            "perfect_maze": 0.04,
            "fractal_low": 0.03,
            "fractal_medium": 0.20,
            "fractal_high": 0.002,
        }
        adjusted = A["holm_adjust"](raw)
        self.assertEqual(adjusted["fractal_high"], 0.01)
        self.assertEqual(adjusted["open"], 0.04)
        self.assertEqual(adjusted["fractal_low"], 0.09)
        self.assertEqual(adjusted["perfect_maze"], 0.09)
        self.assertEqual(adjusted["fractal_medium"], 0.20)
        with self.assertRaisesRegex(A["HeadlessAnalysisError"], "inventory differs"):
            A["holm_adjust"]({"open": 0.1})

    def test_three_repetitions_collapse_to_method_scene_medians_and_ratios(self):
        rows = complete_rows()
        collapsed = A["collapse_timing_rows"](
            rows, fixed_timesteps=100, repetitions=3
        )
        self.assertTrue(collapsed["analysis_available"])
        self.assertEqual(len(collapsed["retained_rows"]), len(rows))
        self.assertTrue(all(row["analysis_admissible"] for row in collapsed["retained_rows"]))
        self.assertEqual(len(collapsed["median_rows"]), 5 * 2 * 2)
        self.assertEqual(len(collapsed["paired_rows"]), 5 * 2)
        for row in collapsed["paired_rows"]:
            self.assertAlmostEqual(row["total_cpu_seconds_ratio"], 0.5)
            self.assertAlmostEqual(row["wall_seconds_ratio"], 0.5)
            self.assertAlmostEqual(row["peak_rss_mib_ratio"], 0.5)
            self.assertAlmostEqual(row["robot_timesteps_per_cpu_second_ratio"], 2.0)

        analysis = A["paired_ratio_analysis"](
            collapsed["paired_rows"], draws=10_000
        )
        self.assertEqual(set(analysis["families"]), set(A["FAMILIES"]))
        self.assertLess(
            analysis["overall"]["total_cpu_seconds"]["ratio_ci95"][1], 1.0
        )
        self.assertEqual(
            analysis["holm_family_compute"]["family_count"], 5
        )

    def test_missing_failed_timeout_and_duplicate_rows_are_retained_and_block_analysis(self):
        rows = complete_rows()
        removed = rows.pop()
        failed = rows[0]
        failed["status"] = "timeout"
        duplicate = dict(rows[7])
        duplicate["attempt_id"] += "-duplicate"
        rows.append(duplicate)
        collapsed = A["collapse_timing_rows"](
            rows, fixed_timesteps=100, repetitions=3
        )
        self.assertFalse(collapsed["analysis_available"])
        self.assertEqual(len(collapsed["retained_rows"]), len(rows))
        self.assertEqual(collapsed["paired_rows"], [])
        reasons = "\n".join(collapsed["failure_reasons"])
        self.assertIn("unsuccessful-cell", reasons)
        self.assertIn("duplicate-cell", reasons)
        self.assertIn("incomplete-cell", reasons)
        self.assertIsNotNone(removed)

    def test_unsuccessful_rows_retain_resource_measurement_but_cannot_enter_median(self):
        row = timing_row(
            family="open",
            seed=1,
            count=8,
            method="reference",
            repetition=1,
            cpu=2.0,
            status="timeout",
        )
        collapsed = A["collapse_timing_rows"]([row], fixed_timesteps=100)
        self.assertFalse(collapsed["analysis_available"])
        self.assertEqual(collapsed["retained_rows"][0]["measurement"], row["measurement"])
        self.assertFalse(collapsed["retained_rows"][0]["analysis_admissible"])
        self.assertEqual(collapsed["median_rows"], [])

        joined_rows = complete_rows()
        joined_rows[0]["digest_join_valid"] = False
        joined_rows[1]["positions_join_valid"] = False
        joined = A["collapse_timing_rows"](
            joined_rows, fixed_timesteps=100, repetitions=3
        )
        self.assertFalse(joined["analysis_available"])
        self.assertEqual(len(joined["retained_rows"]), len(joined_rows))
        self.assertFalse(joined["retained_rows"][0]["analysis_admissible"])
        self.assertFalse(joined["retained_rows"][1]["analysis_admissible"])

    def test_capacity_compares_optimized_nstar_to_reference_n0_conservatively(self):
        rows = complete_rows(counts=(8, 16), optimized_factor=0.2)
        collapsed = A["collapse_timing_rows"](
            rows, fixed_timesteps=100, repetitions=3
        )
        self.assertTrue(collapsed["analysis_available"])
        result = A["conservative_capacity_comparison"](
            collapsed["median_rows"],
            family="open",
            reference_n0=8,
            optimized_n_star=16,
            draws=10_000,
        )
        self.assertTrue(result["available"])
        self.assertTrue(result["candidate_pass"])
        self.assertFalse(result["claim_ready"])
        self.assertLessEqual(
            result["metrics"]["total_cpu_seconds"]["ratio_ci95"][1], 1.0
        )
        unavailable = A["conservative_capacity_comparison"](
            collapsed["median_rows"],
            family="open",
            reference_n0=8,
            optimized_n_star=32,
            draws=10_000,
        )
        self.assertFalse(unavailable["available"])
        self.assertFalse(unavailable["candidate_pass"])

    def test_calibration_recommendation_is_frozen_prefix_only_and_nonauthorizing(self):
        task_rows = [
            {
                "family": "open",
                "robot_count": 8,
                "task_valid": True,
            }
            for _seed in range(1, 11)
        ]
        recommendation = A["recommend_calibration_counts"](
            {
                "design": {
                    "family_grids": [
                        {"counts": [8], "family": "open", "n0": 8}
                    ]
                }
            },
            {
                "analysis_available": True,
                "collapse": {"median_rows": []},
            },
            {"scene_rows": task_rows},
            draws=10_000,
        )
        self.assertTrue(recommendation["recommendation_available"])
        self.assertEqual(
            recommendation["rule_id"],
            A["RECOMMENDATION_RULE_ID"],
        )
        self.assertEqual(
            recommendation["family_recommendations"][0]["candidate_family_n0"],
            8,
        )
        self.assertTrue(
            all(value is False for value in recommendation["authority"].values())
        )

    def test_exact_object_and_numeric_types_do_not_coerce_bool(self):
        with self.assertRaises(A["HeadlessAnalysisError"]):
            A["_integer"](True, "bool")
        with self.assertRaises(A["HeadlessAnalysisError"]):
            A["_finite"](False, "bool")
        with self.assertRaises(A["HeadlessAnalysisError"]):
            A["_finite"](10**4000, "overflow")

    def test_session_ledger_attempt_and_conditional_inventory_authenticate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            authenticated, session_root, _session, _events, _write = mini_session_fixture(root)
            pathname_bomb = mock.Mock(side_effect=AssertionError("pathname session read"))
            with mock.patch.object(Path, "rglob", pathname_bomb), mock.patch.dict(
                A,
                {
                    "_canonical_json_file": pathname_bomb,
                    "_regular_file_bytes": pathname_bomb,
                },
            ):
                audited = A["audit_session"](authenticated, session_root)
            self.assertEqual(audited["session_root"], session_root)
            self.assertTrue(audited["report"]["complete"])
            self.assertTrue(audited["report"]["all_attempts_completed"])
            self.assertEqual(len(audited["report"]["retained_attempt_rows"]), 2)
            self.assertEqual(len(audited["evidence"]), 2)
            pathname_bomb.assert_not_called()
            (session_root / "unlisted.txt").write_text("no", encoding="utf-8")
            with self.assertRaisesRegex(A["HeadlessAnalysisError"], "unlisted outputs"):
                A["audit_session"](authenticated, session_root)

    def test_session_reader_rejects_reparent_and_member_replace(self):
        reader_type = A["_SessionDescriptorReader"]
        real_read = reader_type.read

        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            authenticated, session_root, _session, _events, _write = mini_session_fixture(root)
            moved = session_root.with_name("reparented-session")
            changed = False

            def read_then_reparent(reader, relative, maximum, label):
                nonlocal changed
                raw = real_read(reader, relative, maximum, label)
                if not changed:
                    changed = True
                    session_root.rename(moved)
                    session_root.mkdir()
                return raw

            with mock.patch.object(reader_type, "read", read_then_reparent):
                with self.assertRaisesRegex(
                    A["HeadlessAnalysisError"], "identity changed"
                ):
                    A["audit_session"](authenticated, session_root)
            self.assertTrue(changed)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            authenticated, session_root, _session, _events, _write = mini_session_fixture(root)
            replaced = False

            def replace_before_ledger_read(reader, relative, maximum, label):
                nonlocal replaced
                if relative == Path("ledger.ndjson") and not replaced:
                    replaced = True
                    target = session_root / "ledger.ndjson"
                    replacement = session_root / "ledger.replacement"
                    replacement.write_bytes(target.read_bytes())
                    os.replace(replacement, target)
                return real_read(reader, relative, maximum, label)

            with mock.patch.object(reader_type, "read", replace_before_ledger_read):
                with self.assertRaisesRegex(
                    A["HeadlessAnalysisError"], "identity changed before read"
                ):
                    A["audit_session"](authenticated, session_root)
            self.assertTrue(replaced)

    def test_session_reader_rejects_intermediate_ancestry_replacement(self):
        reader_type = A["_SessionDescriptorReader"]
        real_read = reader_type.read

        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            authenticated, session_root, _session, _events, _write = mini_session_fixture(root)
            replaced = False

            def read_then_replace_ancestry(reader, relative, maximum, label):
                nonlocal replaced
                raw = real_read(reader, relative, maximum, label)
                if not replaced:
                    original_results = root / "results"
                    retained_results = root / "retained-results"
                    original_results.rename(retained_results)
                    replacement_parent = root.joinpath(
                        "results",
                        "simulator-demo",
                        "v1",
                        "headless-scientific",
                    )
                    replacement_parent.mkdir(parents=True)
                    retained_epoch = retained_results.joinpath(
                        "simulator-demo",
                        "v1",
                        "headless-scientific",
                        "epoch-1",
                    )
                    retained_epoch.rename(replacement_parent / "epoch-1")
                    replaced = True
                return raw

            with mock.patch.object(reader_type, "read", read_then_replace_ancestry):
                with self.assertRaisesRegex(
                    A["HeadlessAnalysisError"], "changed during audit"
                ):
                    A["audit_session"](authenticated, session_root)
            self.assertTrue(replaced)

    def test_session_reader_rejects_same_size_in_place_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            authenticated, session_root, _session, _events, _write = mini_session_fixture(root)
            reader_type = A["_SessionDescriptorReader"]
            real_read = reader_type.read
            mutated = False

            def mutate_after_attempt_read(reader, relative, maximum, label):
                nonlocal mutated
                raw = real_read(reader, relative, maximum, label)
                if relative.name == "attempt.json" and not mutated:
                    mutated = True
                    target = session_root.joinpath(*relative.parts)
                    changed = b"[" + raw[1:]
                    self.assertEqual(len(changed), len(raw))
                    target.write_bytes(changed)
                    self.assertEqual(target.stat().st_size, len(raw))
                return raw

            with mock.patch.object(reader_type, "read", mutate_after_attempt_read):
                with self.assertRaisesRegex(
                    A["HeadlessAnalysisError"], "changed during audit"
                ):
                    A["audit_session"](authenticated, session_root)
            self.assertTrue(mutated)

    def test_session_reader_rechecks_content_after_final_ancestry_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            authenticated, session_root, _session, _events, _write = mini_session_fixture(root)
            real_open_directory = A["_open_directory_descriptor"]
            calls = 0
            mutated = False

            def open_directory_then_mutate(path, label):
                nonlocal calls, mutated
                descriptor = real_open_directory(path, label)
                calls += 1
                if calls == 2:
                    target = session_root / "ledger.ndjson"
                    raw = target.read_bytes()
                    target.write_bytes(b"[" + raw[1:])
                    self.assertEqual(target.stat().st_size, len(raw))
                    mutated = True
                return descriptor

            with mock.patch.dict(
                A, {"_open_directory_descriptor": open_directory_then_mutate}
            ):
                with self.assertRaisesRegex(
                    A["HeadlessAnalysisError"], "changed during audit"
                ):
                    A["audit_session"](authenticated, session_root)
            self.assertEqual(calls, 2)
            self.assertTrue(mutated)

    def test_session_reader_rechecks_ancestry_after_final_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            authenticated, session_root, _session, _events, _write = mini_session_fixture(root)
            reader_type = A["_SessionDescriptorReader"]
            real_inventory = reader_type._inventory
            calls = 0
            replaced = False

            def inventory_then_replace_ancestry(reader):
                nonlocal calls, replaced
                result = real_inventory(reader)
                calls += 1
                if calls == 2:
                    original_results = root / "results"
                    retained_results = root / "retained-results"
                    original_results.rename(retained_results)
                    original_results.mkdir()
                    replaced = True
                return result

            with mock.patch.object(
                reader_type, "_inventory", inventory_then_replace_ancestry
            ):
                with self.assertRaisesRegex(
                    A["HeadlessAnalysisError"], "changed during audit"
                ):
                    A["audit_session"](authenticated, session_root)
            self.assertEqual(calls, 2)
            self.assertTrue(replaced)

    def test_session_reader_rechecks_content_through_final_named_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            authenticated, session_root, _session, _events, _write = mini_session_fixture(root)
            reader_type = A["_SessionDescriptorReader"]
            real_reauthenticate = reader_type._reauthenticate_named_ancestry
            mutated = False

            def mutate_then_reauthenticate(reader, phase, *, verify_inventory):
                nonlocal mutated
                if phase == "after final inventory" and not mutated:
                    target = session_root / "ledger.ndjson"
                    raw = target.read_bytes()
                    target.write_bytes(b"[" + raw[1:])
                    self.assertEqual(target.stat().st_size, len(raw))
                    mutated = True
                return real_reauthenticate(
                    reader, phase, verify_inventory=verify_inventory
                )

            with mock.patch.object(
                reader_type,
                "_reauthenticate_named_ancestry",
                mutate_then_reauthenticate,
            ):
                with self.assertRaisesRegex(
                    A["HeadlessAnalysisError"], "canonical session inventory changed"
                ):
                    A["audit_session"](authenticated, session_root)
            self.assertTrue(mutated)

    def test_session_reader_rechecks_ancestry_after_final_canonical_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            authenticated, session_root, _session, _events, _write = mini_session_fixture(root)
            reader_type = A["_SessionDescriptorReader"]
            real_inventory = reader_type._inventory_from_descriptor
            calls = 0
            replaced = False

            def inventory_then_replace_ancestry(reader, descriptor):
                nonlocal calls, replaced
                result = real_inventory(reader, descriptor)
                calls += 1
                if calls == 3:
                    original_results = root / "results"
                    retained_results = root / "retained-results"
                    original_results.rename(retained_results)
                    original_results.mkdir()
                    replaced = True
                return result

            with mock.patch.object(
                reader_type,
                "_inventory_from_descriptor",
                inventory_then_replace_ancestry,
            ):
                with self.assertRaisesRegex(
                    A["HeadlessAnalysisError"],
                    "canonical session ancestry changed during final inventory",
                ):
                    A["audit_session"](authenticated, session_root)
            self.assertEqual(calls, 3)
            self.assertTrue(replaced)

    def test_session_reader_nonblocking_rejects_fifo_replacement(self):
        if not hasattr(os, "mkfifo") or not getattr(os, "O_NONBLOCK", 0):
            self.skipTest("FIFO and O_NONBLOCK are required")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            authenticated, session_root, _session, _events, _write = mini_session_fixture(root)
            reader_type = A["_SessionDescriptorReader"]
            real_read = reader_type.read
            real_open = A["os"].open
            replaced = False
            nonblocking_observed = False

            def replace_before_ledger_read(reader, relative, maximum, label):
                nonlocal replaced
                if relative == Path("ledger.ndjson") and not replaced:
                    target = session_root / "ledger.ndjson"
                    target.unlink()
                    os.mkfifo(target)
                    replaced = True
                return real_read(reader, relative, maximum, label)

            def require_nonblocking(path, flags, *args, **kwargs):
                nonlocal nonblocking_observed
                if path == "ledger.ndjson" and replaced:
                    self.assertNotEqual(flags & os.O_NONBLOCK, 0)
                    nonblocking_observed = True
                return real_open(path, flags, *args, **kwargs)

            with mock.patch.object(
                reader_type, "read", replace_before_ledger_read
            ), mock.patch.object(A["os"], "open", side_effect=require_nonblocking):
                with self.assertRaisesRegex(
                    A["HeadlessAnalysisError"], "identity changed before read"
                ):
                    A["audit_session"](authenticated, session_root)
            self.assertTrue(replaced)
            self.assertTrue(nonblocking_observed)

    def test_session_reader_preserves_primary_baseexception_over_close_failure(self):
        class FatalSessionRead(BaseException):
            pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            authenticated, session_root, _session, _events, _write = mini_session_fixture(root)
            real_close = A["os"].close
            read_failed = False
            close_failed = False

            def fatal_read(_descriptor, _size):
                nonlocal read_failed
                read_failed = True
                raise FatalSessionRead("primary read failure")

            def close_then_raise(descriptor):
                nonlocal close_failed
                real_close(descriptor)
                if read_failed and not close_failed:
                    close_failed = True
                    raise OSError("post-effect close failure")

            before = open_descriptor_count()
            with mock.patch.object(A["os"], "read", side_effect=fatal_read), mock.patch.object(
                A["os"], "close", side_effect=close_then_raise
            ):
                with self.assertRaises(FatalSessionRead) as caught:
                    A["audit_session"](authenticated, session_root)
            self.assertTrue(close_failed)
            self.assertTrue(
                any(
                    "post-effect close failure" in detail
                    for detail in getattr(
                        caught.exception, "_headless_cleanup_failures", ()
                    )
                )
            )
            self.assertEqual(open_descriptor_count(), before)

    def test_directory_descriptor_handoff_surfaces_ambiguous_close_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            nested = root / "one" / "two"
            nested.mkdir(parents=True)
            real_close = A["os"].close
            failed = False
            retained: int | None = None

            def fail_before_first_close(descriptor):
                nonlocal failed, retained
                if not failed:
                    failed = True
                    retained = descriptor
                    raise OSError("pre-effect close failure")
                real_close(descriptor)

            before = open_descriptor_count()
            with mock.patch.object(A["os"], "close", side_effect=fail_before_first_close):
                with self.assertRaisesRegex(
                    A["HeadlessAnalysisError"], "cannot traverse"
                ):
                    A["_open_directory_descriptor"](nested, "handoff fixture")
            self.assertTrue(failed)
            self.assertIsNotNone(retained)
            os.fstat(retained)
            self.assertEqual(open_descriptor_count(), before + 1)
            real_close(retained)
            self.assertEqual(open_descriptor_count(), before)

    def test_first_cleanup_baseexception_remains_primary(self):
        class FatalClose(BaseException):
            pass

        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            authenticated, session_root, _session, _events, _write = mini_session_fixture(root)
            real_read = A["os"].read
            real_close = A["os"].close
            read_completed = False
            close_failed = False

            def tracked_read(descriptor, size):
                nonlocal read_completed
                result = real_read(descriptor, size)
                read_completed = True
                return result

            def close_then_fatal(descriptor):
                nonlocal close_failed
                real_close(descriptor)
                if read_completed and not close_failed:
                    close_failed = True
                    raise FatalClose("first cleanup failure")

            before = open_descriptor_count()
            with mock.patch.object(A["os"], "read", side_effect=tracked_read), mock.patch.object(
                A["os"], "close", side_effect=close_then_fatal
            ):
                with self.assertRaises(FatalClose):
                    A["audit_session"](authenticated, session_root)
            self.assertTrue(close_failed)
            self.assertEqual(open_descriptor_count(), before)

    def test_close_reconciliation_never_closes_a_reused_numeric_fd(self):
        with tempfile.TemporaryDirectory() as directory:
            target_path = Path(directory) / "owned.txt"
            target_path.write_bytes(b"owned")
            descriptor = os.open(target_path, os.O_RDONLY)
            real_close = A["os"].close
            reused: int | None = None
            calls = 0

            def close_reopen_then_raise(value):
                nonlocal reused, calls
                calls += 1
                real_close(value)
                reused = os.open(os.devnull, os.O_RDONLY)
                self.assertEqual(reused, value)
                raise OSError("post-effect close with immediate fd reuse")

            with mock.patch.object(A["os"], "close", side_effect=close_reopen_then_raise):
                failure = A["_close_owned_descriptor"](
                    descriptor, "numeric reuse fixture"
                )
            self.assertIsInstance(failure, OSError)
            self.assertEqual(calls, 1)
            self.assertIsNotNone(reused)
            os.fstat(reused)
            real_close(reused)

    def test_close_reconciliation_never_closes_same_file_reused_numeric_fd(self):
        with tempfile.TemporaryDirectory() as directory:
            target_path = Path(directory) / "owned.txt"
            target_path.write_bytes(b"owned")
            descriptor = os.open(target_path, os.O_RDONLY)
            real_close = A["os"].close
            reused: int | None = None
            calls = 0

            def close_reopen_same_file_then_raise(value):
                nonlocal reused, calls
                calls += 1
                real_close(value)
                reused = os.open(target_path, os.O_RDONLY)
                self.assertEqual(reused, value)
                raise OSError("post-effect close with same-file fd reuse")

            with mock.patch.object(
                A["os"], "close", side_effect=close_reopen_same_file_then_raise
            ):
                failure = A["_close_owned_descriptor"](
                    descriptor, "same-file numeric reuse fixture"
                )
            self.assertIsInstance(failure, OSError)
            self.assertEqual(calls, 1)
            self.assertIsNotNone(reused)
            os.fstat(reused)
            real_close(reused)

    def test_pre_effect_close_baseexception_is_surfaced_once(self):
        class FatalClose(BaseException):
            pass

        with tempfile.TemporaryDirectory() as directory:
            target_path = Path(directory) / "owned.txt"
            target_path.write_bytes(b"owned")
            descriptor = os.open(target_path, os.O_RDONLY)
            real_close = A["os"].close
            calls = 0

            def fail_close(value):
                nonlocal calls
                self.assertEqual(value, descriptor)
                calls += 1
                raise FatalClose("pre-effect close failure")

            with mock.patch.object(A["os"], "close", side_effect=fail_close):
                failure = A["_close_owned_descriptor"](
                    descriptor, "pre-effect fixture"
                )
            self.assertIsInstance(failure, FatalClose)
            self.assertEqual(calls, 1)
            os.fstat(descriptor)
            real_close(descriptor)

    def test_nested_parent_handoff_clears_stale_owner_before_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            authenticated, session_root, _session, _events, _write = mini_session_fixture(root)
            reader_type = A["_SessionDescriptorReader"]
            real_close_owned = A["_close_owned_descriptor"]
            reused: int | None = None

            def fail_parent_handoff(descriptor, label):
                nonlocal reused
                if "prior parent descriptor" in label and reused is None:
                    os.close(descriptor)
                    reused = os.open(os.devnull, os.O_RDONLY)
                    self.assertEqual(reused, descriptor)
                    return OSError("post-effect parent handoff failure")
                return real_close_owned(descriptor, label)

            session_relative = PurePosixPath(
                session_root.relative_to(root).as_posix()
            )
            attempt_relative = PurePosixPath(
                next(session_root.rglob("attempt.json"))
                .relative_to(session_root)
                .as_posix()
            )
            with reader_type(root, session_relative) as reader, mock.patch.dict(
                A, {"_close_owned_descriptor": fail_parent_handoff}
            ):
                with self.assertRaises(A["HeadlessAnalysisError"]):
                    reader.read(attempt_relative, 1024 * 1024, "handoff attempt")
            self.assertIsNotNone(reused)
            os.fstat(reused)
            os.close(reused)

    def test_session_reader_closes_all_descriptors_after_baseexception(self):
        class FatalSessionRead(BaseException):
            pass

        for operation in ("read", "listdir"):
            with self.subTest(operation=operation), tempfile.TemporaryDirectory() as directory:
                root = Path(os.path.realpath(directory))
                authenticated, session_root, _session, _events, _write = mini_session_fixture(root)
                before = open_descriptor_count()
                with mock.patch.object(
                    A["os"], operation, side_effect=FatalSessionRead(operation)
                ):
                    with self.assertRaises(FatalSessionRead):
                        A["audit_session"](authenticated, session_root)
                self.assertEqual(open_descriptor_count(), before)

    def test_duplicate_and_missing_results_are_retained_without_denominator_shrink(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            authenticated, session_root, session, events, write_session = mini_session_fixture(root)
            duplicate = json.loads(json.dumps(session["results"][0]))
            session["results"] = [session["results"][0], duplicate]
            duplicate_events = [events[0]]
            host = events[1]["host"]
            for result in session["results"]:
                duplicate_events.extend(
                    [
                        {
                            "attempt_id": result["attempt_id"],
                            "host": host,
                            "kind": "attempt-start",
                            "sequence": result["sequence"],
                        },
                        {
                            "attempt_id": result["attempt_id"],
                            "category": result["category"],
                            "kind": "attempt-complete",
                            "sequence": result["sequence"],
                        },
                    ]
                )
            duplicate_events.append({"attempt_count": 2, "kind": "session-complete"})
            write_session(session, duplicate_events)
            audited = A["audit_session"](authenticated, session_root)
            self.assertFalse(audited["report"]["complete"])
            rows = audited["report"]["retained_attempt_rows"]
            self.assertEqual(len(rows), 3)
            self.assertEqual(sum(row["status"] == "missing" for row in rows), 1)
            self.assertEqual(
                sum("duplicate-result-sequence" in row["failure_reasons"] for row in rows),
                2,
            )

    def test_session_ancestor_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            authenticated, session_root, _session, _events, _write = mini_session_fixture(root)
            epoch_root = root.joinpath(*A["RESULT_ROOT"].parts)
            moved = root / "moved-epoch"
            epoch_root.rename(moved)
            epoch_root.symlink_to(moved, target_is_directory=True)
            with self.assertRaisesRegex(A["HeadlessAnalysisError"], "ancestry"):
                A["audit_session"](authenticated, session_root)

    def test_ledger_rejects_failed_host_gates_and_noncanonical_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            authenticated, session_root, session, events, write_session = mini_session_fixture(root)
            valid_host = dict(events[1]["host"])
            mutations = (
                ("ac_power", False),
                ("thermal_nominal", False),
                ("free_bytes_ok", False),
                ("free_bytes", None),
                ("free_bytes", True),
                ("free_bytes", 0),
                ("free_bytes", A["MIN_HOST_FREE_BYTES"] - 1),
            )
            for key, value in mutations:
                with self.subTest(key=key, value=value):
                    events[1]["host"] = dict(valid_host)
                    events[1]["host"][key] = value
                    write_session(session, events)
                    with self.assertRaises(A["HeadlessAnalysisError"]):
                        A["audit_session"](authenticated, session_root)
            events[1]["host"] = valid_host
            write_session(session, events)
            raw = (session_root / "ledger.ndjson").read_bytes()
            first, rest = raw.split(b"\n", 1)
            (session_root / "ledger.ndjson").write_bytes(first + b" \n" + rest)
            with self.assertRaisesRegex(A["HeadlessAnalysisError"], "canonical JSON"):
                A["audit_session"](authenticated, session_root)

        dangling_events = [
            {"kind": "session-start", "plan_sha256": "a" * 64},
            {
                "attempt_id": "b" * 64,
                "host": {
                    "ac_power": True,
                    "free_bytes": None,
                    "free_bytes_ok": True,
                    "thermal_nominal": True,
                },
                "kind": "attempt-start",
                "sequence": 1,
            },
        ]
        dangling_raw = b"".join(
            A["canonical_json_bytes"](
                {
                    "event": event,
                    "index": index,
                    "schema_version": A["LEDGER_SCHEMA"],
                }
            )
            for index, event in enumerate(dangling_events, start=1)
        )
        with self.assertRaisesRegex(A["HeadlessAnalysisError"], "must be an integer"):
            A["_validate_ledger"](
                dangling_raw,
                session={"aborted": True, "attempt_count": 0, "results": []},
                plan_sha256="a" * 64,
            )

    def test_positions_trace_requires_exact_cadence_ids_and_parity(self):
        raw = positions_trace_bytes([(-2.0, 0.5), (2.0, 0.5), (3.0, 0.5)])
        reference = A["validate_trace_bytes"](
            raw,
            trace_detail="positions",
            robot_count=1,
            controller_timesteps=2,
            label="reference positions",
            expected_metadata={"fixture": "positions"},
        )
        optimized = A["validate_trace_bytes"](
            raw,
            trace_detail="positions",
            robot_count=1,
            controller_timesteps=2,
            label="optimized positions",
            expected_metadata={"fixture": "positions"},
        )
        parity = A["compare_trace_pair"](
            reference, optimized, label="positions pair"
        )
        self.assertEqual(parity["frame_count"], 4)
        self.assertEqual(len(A["positions_trace_sweeps"](reference)), 2)

        records = [json.loads(line) for line in raw.splitlines()]
        records[3]["tick"] = 2
        bad_cadence = b"".join(A["canonical_json_bytes"](record) for record in records)
        with self.assertRaisesRegex(A["HeadlessAnalysisError"], "cadence differs"):
            A["validate_trace_bytes"](
                bad_cadence,
                trace_detail="positions",
                robot_count=1,
                controller_timesteps=2,
                label="bad cadence",
            )

        records = [json.loads(line) for line in raw.splitlines()]
        for frame in records[1:]:
            frame["simulator_state"]["iterations"] = 0
        no_controller_work = b"".join(
            A["canonical_json_bytes"](record) for record in records
        )
        with self.assertRaisesRegex(A["HeadlessAnalysisError"], "fixed-work"):
            A["validate_trace_bytes"](
                no_controller_work,
                trace_detail="positions",
                robot_count=1,
                controller_timesteps=2,
                label="no controller work",
            )

        records = [json.loads(line) for line in raw.splitlines()]
        records[-1]["robots"][0]["state"][0] += 1.0
        records[-1]["robots"][0]["state_hex"][0] = struct.pack(
            ">d", records[-1]["robots"][0]["state"][0]
        ).hex()
        mismatch = b"".join(A["canonical_json_bytes"](record) for record in records)
        changed = A["validate_trace_bytes"](
            mismatch,
            trace_detail="positions",
            robot_count=1,
            controller_timesteps=2,
            label="changed positions",
        )
        with self.assertRaisesRegex(A["HeadlessAnalysisError"], "frames"):
            A["compare_trace_pair"](reference, changed, label="mismatch pair")

    def test_trace_time_reproduces_recorder_binary32_multiplication(self):
        raw = positions_trace_bytes(
            [(-2.0, 0.5), (-1.0, 0.5), (0.0, 0.5), (1.0, 0.5)]
        )
        records = [json.loads(line) for line in raw.splitlines()]
        self.assertEqual(records[-1]["tick"], 3)
        self.assertEqual(
            records[-1]["simulated_time_seconds"],
            0.30000001192092896,
        )
        A["validate_trace_bytes"](
            raw,
            trace_detail="positions",
            robot_count=1,
            controller_timesteps=3,
            label="recorder binary32 cadence",
            expected_metadata={"fixture": "positions"},
        )

        records[-1]["simulated_time_seconds"] = 3 * A["RUNTIME_TIMESTEP_SECONDS"]
        binary64_product = b"".join(
            A["canonical_json_bytes"](record) for record in records
        )
        with self.assertRaisesRegex(A["HeadlessAnalysisError"], "simulated time differs"):
            A["validate_trace_bytes"](
                binary64_product,
                trace_detail="positions",
                robot_count=1,
                controller_timesteps=3,
                label="binary64 cadence mutant",
                expected_metadata={"fixture": "positions"},
            )

    def test_descriptor_runtime_resolved_config_binds_relative_map_identity(self):
        expected = fixture_resolved_config(
            robot_count=1, timesteps=2, radius=1.0, seed=1
        )
        relative_map = "comparison/environments/open/calibration/map.png"
        self.assertEqual(expected["obstacle_file"], relative_map)
        self.assertFalse(Path(expected["obstacle_file"]).is_absolute())

        changed = json.loads(json.dumps(expected))
        changed["obstacle_file"] = str(ROOT / relative_map)
        with self.assertRaises(A["HeadlessAnalysisError"]):
            A["_validate_resolved_config"](
                changed,
                robot_count=1,
                controller_timesteps=2,
                label="absolute legacy obstacle path",
                expected=expected,
            )

        with self.assertRaises(A["HeadlessAnalysisError"]):
            A["_expected_runtime_resolved_config"](
                config_path="comparison/plans/simulator-demo/v1/headless-epoch-1/configs/fixture.yaml",
                map_path=str(ROOT / relative_map),
                scenario_path="comparison/plans/simulator-demo/v1/headless-epoch-1/missions/fixture.json",
                scene_id="fixture-scene",
                robot_count=1,
                mission_seed=1,
            )

    def test_trace_parser_accepts_nlohmann_round_trip_numeric_spelling(self):
        raw = b'{"value":1.8736750980874879}\n'
        records = A["_strict_ndjson_records"](
            raw, "nlohmann trace spelling", canonical_lines=False
        )
        self.assertEqual(records, [{"value": 1.8736750980874879}])
        self.assertNotEqual(A["canonical_json_bytes"](records[0]), raw)
        with self.assertRaises(A["HeadlessAnalysisError"]):
            A["_strict_ndjson_records"](
                b'{"value":1,"value":1}\n',
                "duplicate trace key",
                canonical_lines=False,
            )

    def test_summary_provenance_paper_and_registered_parity_fields_are_exact(self):
        metadata = {"fixture": "summary"}
        summary = {
            "all_goals_reached": False,
            "final_robot_count": 1,
            "final_tick": 2,
            "first_all_goals_tick": None,
            "frames_recorded": 4,
            "goals_reached": 0,
            "goals_total": 1,
            "invalid_geometry_evaluations": 0,
            "invalid_robot_frames": 0,
            "iterations": 4,
            "maximum_robot_count": 1,
            "mean_path_length_m_per_robot": 1.0,
            "metadata": metadata,
            "minimum_obstacle_clearance_m": 1.0,
            "minimum_pair_clearance_m": 1.0,
            "nonfinite_numeric_observations": 0,
            "nonfinite_state_observations": 0,
            "obstacle_collision_events": 0,
            "obstacle_collision_exposure_seconds": 0.0,
            "pair_collision_events": 0,
            "pair_collision_exposure_seconds": 0.0,
            "paper_metrics": {
                "consensus_reached": 0,
                "final_connectivity": 0.0,
                "final_decision": 0,
                "final_heading_error": 0.0,
                "final_iterations": 4,
                "final_position_error": 0.0,
            },
            "per_robot_goal_tick": {},
            "per_robot_path_length_m": {"0": 1.0},
            "resolved_config": {
                "configured_robot_count": 1,
                "display": False,
                "max_timesteps": 2,
                "rng_seed": 1,
                "robot_radius_m": 1.0,
                "timestep_seconds": 0.1,
            },
            "run_valid": True,
            "schema_version": "dancers-comparison-summary-v1",
            "simulated_time_seconds": 0.2,
            "termination_reason": "maximum_time",
            "total_path_length_m": 1.0,
            "trace_detail": "digest",
            "trace_path": "/sealed/fixture/trace.ndjson",
        }
        summary["resolved_config"] = fixture_resolved_config(
            robot_count=1, timesteps=2, radius=1.0, seed=1
        )
        raw = A["canonical_json_bytes"](summary)
        parsed = A["validate_summary_bytes"](
            raw,
            expected_metadata=metadata,
            robot_count=1,
            controller_timesteps=2,
            mission_seed=1,
            trace_detail="digest",
            expected_trace_path="/sealed/fixture/trace.ndjson",
            label="summary",
        )
        self.assertEqual(set(parsed["paper_metrics"]), set(A["PAPER_FIELDS"]))
        self.assertEqual(
            set(parsed["summary_parity_fields"]), set(A["SUMMARY_PARITY_FIELDS"])
        )
        recorder_style = (
            json.dumps(
                summary,
                allow_nan=False,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        self.assertNotEqual(recorder_style, raw)
        pretty_parsed = A["validate_summary_bytes"](
            recorder_style,
            expected_metadata=metadata,
            robot_count=1,
            controller_timesteps=2,
            mission_seed=1,
            trace_detail="digest",
            expected_trace_path="/sealed/fixture/trace.ndjson",
            label="recorder-style summary",
        )
        self.assertEqual(pretty_parsed["record"], parsed["record"])
        for mutate in (
            lambda value: value["metadata"].__setitem__("fixture", "wrong"),
            lambda value: value["paper_metrics"].__setitem__("extra", 0),
            lambda value: value.pop("per_robot_goal_tick"),
            lambda value: value.__setitem__("trace_path", "/tmp/not-captured"),
        ):
            changed = json.loads(json.dumps(summary))
            mutate(changed)
            with self.assertRaises(A["HeadlessAnalysisError"]):
                A["validate_summary_bytes"](
                    A["canonical_json_bytes"](changed),
                    expected_metadata=metadata,
                    robot_count=1,
                    controller_timesteps=2,
                    mission_seed=1,
                    trace_detail="digest",
                    expected_trace_path="/sealed/fixture/trace.ndjson",
                    label="mutated summary",
                )

    def test_plan_bound_geometry_detects_between_frame_obstacle_tunnelling(self):
        geometry_path = ROOT / "scripts/comparison/headless_geometry_v1.py"
        geometry_raw = geometry_path.read_bytes()
        identity = {
            "bytes": len(geometry_raw),
            "path": "scripts/comparison/headless_geometry_v1.py",
            "sha256": hashlib.sha256(geometry_raw).hexdigest(),
        }
        geometry = A["load_bound_geometry_source"](geometry_raw, identity=identity)
        trace = A["validate_trace_bytes"](
            positions_trace_bytes([(-2.0, 0.5), (2.0, 0.5), (3.0, 0.5)]),
            trace_detail="positions",
            robot_count=1,
            controller_timesteps=2,
            label="tunnelling positions",
        )
        map_bytes = binary_png(10, {(5, 5)})
        effective = geometry["effective_raster_from_png_bytes"](map_bytes)
        report = A["independently_analyze_geometry"](
            geometry_namespace=geometry,
            map_png_bytes=map_bytes,
            expected_map_png_sha256=hashlib.sha256(map_bytes).hexdigest(),
            expected_effective_raster_sha256=effective.sha256,
            positions_trace=trace,
            controller_timesteps=2,
        )
        self.assertFalse(report["geometry_contact_free"])
        self.assertGreater(report["obstacle_contact_exposure_seconds"], 0.0)
        with self.assertRaisesRegex(A["HeadlessAnalysisError"], "effective raster"):
            A["independently_analyze_geometry"](
                geometry_namespace=geometry,
                map_png_bytes=map_bytes,
                expected_map_png_sha256=hashlib.sha256(map_bytes).hexdigest(),
                expected_effective_raster_sha256="0" * 64,
                positions_trace=trace,
                controller_timesteps=2,
            )

    def test_analyzer_derives_task_validity_not_geometry_helper_task_flag(self):
        geometry_path = ROOT / "scripts/comparison/headless_geometry_v1.py"
        geometry_raw = geometry_path.read_bytes()
        geometry = A["load_bound_geometry_source"](
            geometry_raw,
            identity={
                "bytes": len(geometry_raw),
                "path": "scripts/comparison/headless_geometry_v1.py",
                "sha256": hashlib.sha256(geometry_raw).hexdigest(),
            },
        )
        trace = A["validate_trace_bytes"](
            positions_trace_bytes([(-2.0, -2.0), (-1.0, -2.0), (0.0, -2.0)]),
            trace_detail="positions",
            robot_count=1,
            controller_timesteps=2,
            label="clear positions",
        )
        map_bytes = binary_png(10, set())
        effective = geometry["effective_raster_from_png_bytes"](map_bytes)
        report = A["independently_analyze_geometry"](
            geometry_namespace=geometry,
            map_png_bytes=map_bytes,
            expected_map_png_sha256=hashlib.sha256(map_bytes).hexdigest(),
            expected_effective_raster_sha256=effective.sha256,
            positions_trace=trace,
            controller_timesteps=2,
        )
        self.assertTrue(report["geometry_contact_free"])
        self.assertFalse(report["task_valid"])
        self.assertFalse(report["task_validity_determined"])
        summary = {
            "final_robot_count": 1,
            "final_tick": 2,
            "frames_recorded": 4,
            "invalid_geometry_evaluations": 0,
            "invalid_robot_frames": 0,
            "iterations": 4,
            "maximum_robot_count": 1,
            "minimum_obstacle_clearance_m": 1.0,
            "minimum_pair_clearance_m": 1.0,
            "nonfinite_numeric_observations": 0,
            "nonfinite_state_observations": 0,
            "obstacle_collision_events": 0,
            "obstacle_collision_exposure_seconds": 0.0,
            "pair_collision_events": 0,
            "pair_collision_exposure_seconds": 0.0,
            "run_valid": True,
            "termination_reason": "maximum_time",
            "trace_detail": "positions",
        }
        validity = A["summary_task_validity"](
            summary,
            robot_count=1,
            controller_timesteps=2,
            expected_trace_detail="positions",
            geometry_report=report,
        )
        self.assertTrue(validity["task_valid"])
        invalid = dict(summary)
        invalid["pair_collision_events"] = 1
        validity = A["summary_task_validity"](
            invalid,
            robot_count=1,
            controller_timesteps=2,
            expected_trace_detail="positions",
            geometry_report=report,
        )
        self.assertFalse(validity["task_valid"])

    def test_public_entrypoints_are_categorically_nonmutating_capability_bombs(self):
        bomb = mock.Mock(side_effect=AssertionError("capability reached"))
        with mock.patch.dict(
            A,
            {
                "analyze_headless_epoch": bomb,
                "validate_analysis_report": bomb,
                "_safe_output_parent": bomb,
            },
        ), mock.patch.object(A["os"], "open", side_effect=bomb), mock.patch.object(
            A["os"], "read", side_effect=bomb
        ), mock.patch.object(A["os"], "write", side_effect=bomb):
            with self.assertRaisesRegex(
                A["HeadlessAnalysisError"],
                "publication is categorically disabled",
            ):
                A["publish_analysis_create_exclusive"](
                    {},
                    json_path=Path("relative.json"),
                    markdown_path=Path("relative.md"),
                )
        bomb.assert_not_called()

        bomb = mock.Mock(side_effect=AssertionError("capability reached"))
        stderr = io.StringIO()
        stdout = io.StringIO()
        with mock.patch.dict(
            A,
            {
                "analyze_headless_epoch": bomb,
                "publish_analysis_create_exclusive": bomb,
                "_regular_file_bytes": bomb,
            },
        ), mock.patch.object(A["os"], "open", side_effect=bomb), mock.patch.object(
            A["os"], "read", side_effect=bomb
        ), mock.patch.object(A["os"], "write", side_effect=bomb), mock.patch.object(
            A["sys"], "stderr", stderr
        ), mock.patch.object(A["sys"], "stdout", stdout):
            status = A["main"](
                [
                    "--plan",
                    "does-not-exist.json",
                    "--session",
                    "does-not-exist-session",
                    "--json-out",
                    "must-not-exist.json",
                    "--markdown-out",
                    "must-not-exist.md",
                ]
            )
        self.assertEqual(status, 1)
        self.assertEqual(stderr.getvalue(), A["NONAUTHORIZING_MESSAGE"])
        self.assertEqual(stdout.getvalue(), "")
        bomb.assert_not_called()

    def test_publication_is_explicit_create_exclusive_and_rolls_back_pair(self):
        report = minimal_report()
        publisher = A["_publish_analysis_create_exclusive_dormant"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            json_path = root / "analysis.json"
            markdown_path = root / "analysis.md"
            receipt = publisher(
                report, json_path=json_path, markdown_path=markdown_path
            )
            self.assertTrue(json_path.is_file())
            self.assertTrue(markdown_path.is_file())
            self.assertEqual(
                receipt["json"]["sha256"], hashlib.sha256(json_path.read_bytes()).hexdigest()
            )
            with self.assertRaisesRegex(A["HeadlessAnalysisError"], "already exists"):
                publisher(
                    report,
                    json_path=root / "second.json",
                    markdown_path=markdown_path,
                )
            self.assertFalse((root / "second.json").exists())

        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            existing = root / "existing.md"
            existing.write_text("owned", encoding="utf-8")
            json_path = root / "rolled-back.json"
            with self.assertRaisesRegex(A["HeadlessAnalysisError"], "already exists"):
                publisher(
                    report, json_path=json_path, markdown_path=existing
                )
            self.assertFalse(json_path.exists())
            self.assertEqual(existing.read_text(encoding="utf-8"), "owned")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            same = root / "same.json"
            with self.assertRaisesRegex(A["HeadlessAnalysisError"], "identify one name"):
                publisher(
                    report, json_path=same, markdown_path=same
                )
            self.assertFalse(same.exists())

    def test_publication_rolls_back_post_create_write_fsync_close_and_baseexception(self):
        report = minimal_report()
        publisher = A["_publish_analysis_create_exclusive_dormant"]

        def exercise(function_name, side_effect, expected_exception=Exception):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(os.path.realpath(directory))
                json_path = root / "analysis.json"
                markdown_path = root / "analysis.md"
                with mock.patch.object(A["os"], function_name, side_effect=side_effect):
                    with self.assertRaises(expected_exception):
                        publisher(
                            report,
                            json_path=json_path,
                            markdown_path=markdown_path,
                        )
                self.assertFalse(json_path.exists(), function_name)
                self.assertFalse(markdown_path.exists(), function_name)

        real_write = os.write
        write_calls = 0

        def fail_write_once(fd, data):
            nonlocal write_calls
            write_calls += 1
            if write_calls == 1:
                raise OSError(5, "injected write failure")
            return real_write(fd, data)

        exercise("write", fail_write_once)

        real_fsync = os.fsync
        fsync_calls = 0

        def fail_fsync_once(fd):
            nonlocal fsync_calls
            fsync_calls += 1
            if fsync_calls == 1:
                raise OSError(5, "injected fsync failure")
            return real_fsync(fd)

        exercise("fsync", fail_fsync_once)

        real_close = os.close
        regular_close_failed = False

        def fail_close_once(fd):
            nonlocal regular_close_failed
            mode = os.fstat(fd).st_mode
            if stat.S_ISREG(mode) and not regular_close_failed:
                regular_close_failed = True
                real_close(fd)
                raise OSError(5, "injected close failure after close")
            return real_close(fd)

        exercise("close", fail_close_once)

        interrupt_calls = 0

        def interrupt_write_once(fd, data):
            nonlocal interrupt_calls
            interrupt_calls += 1
            if interrupt_calls == 1:
                raise KeyboardInterrupt("injected")
            return real_write(fd, data)

        exercise("write", interrupt_write_once, KeyboardInterrupt)

    def test_publication_post_create_fstat_rollback_and_retained_partial_reporting(self):
        report = minimal_report()
        publisher = A["_publish_analysis_create_exclusive_dormant"]
        real_fstat = os.fstat
        calls = 0
        regular_failed = False

        def fail_created_fstat_once(fd):
            nonlocal calls, regular_failed
            calls += 1
            observed = real_fstat(fd)
            if stat.S_ISREG(observed.st_mode) and not regular_failed:
                regular_failed = True
                raise OSError(5, "injected created-file fstat failure")
            return observed

        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            json_path = root / "analysis.json"
            markdown_path = root / "analysis.md"
            with mock.patch.object(A["os"], "fstat", side_effect=fail_created_fstat_once):
                with self.assertRaises(A["HeadlessAnalysisError"]):
                    publisher(
                        report, json_path=json_path, markdown_path=markdown_path
                    )
            self.assertFalse(json_path.exists())
            self.assertFalse(markdown_path.exists())

        calls = 0

        def fail_created_identity_fstats(fd):
            nonlocal calls
            calls += 1
            observed = real_fstat(fd)
            if stat.S_ISREG(observed.st_mode):
                raise OSError(5, "persistent created-file fstat failure")
            return observed

        with tempfile.TemporaryDirectory() as directory:
            root = Path(os.path.realpath(directory))
            json_path = root / "retained.json"
            markdown_path = root / "retained.md"
            with mock.patch.object(A["os"], "fstat", side_effect=fail_created_identity_fstats):
                with self.assertRaisesRegex(
                    A["HeadlessAnalysisError"], "retained partial output"
                ):
                    publisher(
                        report, json_path=json_path, markdown_path=markdown_path
                    )
            self.assertTrue(json_path.exists())
            self.assertFalse(markdown_path.exists())

    def test_report_schema_rejects_any_claim_unlock_or_extra_field(self):
        report = minimal_report()
        A["validate_analysis_report"](report)
        for mutation in (
            lambda value: value["classification"].__setitem__("timing_unlock", True),
            lambda value: value["status"].__setitem__("validation_unlock", True),
            lambda value: value["scope"].__setitem__(
                "causal_family_claim_permitted", True
            ),
            lambda value: value["analysis_contract"].__setitem__(
                "bootstrap_cluster", "scene"
            ),
            lambda value: value["timing"]["collapse"].__setitem__(
                "no_outlier_deletion", False
            ),
            lambda value: value["calibration_recommendations"].__setitem__(
                "rule_id", "adaptive"
            ),
            lambda value: value.__setitem__("benchmark_result", {}),
            lambda value: value.__setitem__("phase", "validation"),
        ):
            changed = json.loads(json.dumps(report))
            mutation(changed)
            with self.assertRaises(A["HeadlessAnalysisError"]):
                A["validate_analysis_report"](changed)


if __name__ == "__main__":
    unittest.main()
