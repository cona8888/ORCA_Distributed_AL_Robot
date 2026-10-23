from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts.comparison import analyze_results_v2 as analysis
from scripts.comparison import run_paired_v2 as runner


def timing_observations() -> list[dict[str, object]]:
    observations: list[dict[str, object]] = []
    repetition_factor = {1: 0.9, 2: 1.0, 3: 1.1}
    for method in analysis.METHODS:
        for count in analysis.COUNTS:
            for seed in analysis.SEEDS:
                if method == "reference" and count == 192:
                    cpu = 100.0 + seed
                    wall = 50.0 + seed
                elif method == "optimized" and count == 256:
                    cpu = (0.70 + 0.01 * seed) * (100.0 + seed)
                    wall = (0.75 + 0.005 * seed) * (50.0 + seed)
                else:
                    cpu = 80.0 + seed + count / 100.0
                    wall = 40.0 + seed + count / 200.0
                for repetition in analysis.TIMING_REPETITIONS:
                    factor = repetition_factor[repetition]
                    observations.append(
                        {
                            "attempt_id": (
                                f"{method}-n{count}-s{seed}-r{repetition}"
                            ),
                            "method": method,
                            "robot_count": count,
                            "seed": seed,
                            "repetition": repetition,
                            "terminal_category": "completed",
                            "host_valid": True,
                            "process_control_valid": True,
                            "total_cpu_seconds": cpu * factor,
                            "wall_seconds": wall * factor,
                            "peak_rss_mib": (
                                1_000.0
                                + count
                                + seed
                                + repetition / 10.0
                            ),
                        }
                    )
    return observations


def fidelity_attempts() -> list[dict[str, object]]:
    return [
        {
            "attempt_id": f"attempt-{index:03d}",
            "terminal_category": "completed",
            "host_valid": True,
            "process_control_valid": True,
            "task_valid": True,
            "resource_valid": True,
            "artifacts_valid": True,
        }
        for index in range(40)
    ]


def fidelity_pairs() -> list[dict[str, object]]:
    return [
        {
            "pair_id": f"pair-{index:03d}",
            "host_valid": True,
            "pair_valid": True,
            "frames_compared": 200,
            "config_sha256_equal": True,
            "semantic_trace_equal": True,
            "paper_metrics_equal": True,
            "summary_parity_fields_equal": True,
            "task_valid": True,
        }
        for index in range(20)
    ]


def host_snapshot(
    *,
    percent: int = 90,
    stage: str = "before-attempt",
    cooldown_baseline: int = 90,
    attempt_before: int | None = None,
) -> dict[str, object]:
    baseline_drop = cooldown_baseline - percent
    attempt_drop = (
        None if attempt_before is None else attempt_before - percent
    )
    return {
        "checked_utc": "2026-07-28T12:00:00Z",
        "filesystem_path": "/tmp",
        "free_disk_bytes": 30_000_000_000,
        "minimum_free_disk_bytes": 21_474_836_480,
        "disk_valid": True,
        "power_mode": "AC Power",
        "power_policy_id": "darwin-ac-battery-stable-v1",
        "power_probe": {
            "command": ["pmset", "-g", "batt"],
            "returncode": 0,
            "stdout": (
                "Now drawing from 'AC Power'\n"
                f" -InternalBattery-0 (id=1)\t{percent}%; charged; "
                "(no estimate) present: true\n"
            ),
            "stderr": "",
            "source": "AC Power",
            "battery_record": {
                "name": "InternalBattery-0",
                "id": 1,
                "percent": percent,
                "status": "charged",
                "present": True,
            },
            "parse_error": None,
            "valid": True,
        },
        "power_policy_evaluation": {
            "stage": stage,
            "cooldown_baseline_percent": cooldown_baseline,
            "attempt_boundary_before_percent": attempt_before,
            "drop_from_cooldown_baseline_percentage_points": baseline_drop,
            "drop_from_attempt_before_percentage_points": attempt_drop,
            "valid": True,
            "violations": [],
        },
        "thermal_policy": analysis.THERMAL_POLICY,
        "thermal_probe": {
            "command": ["swift", "thermal.swift"],
            "module_cache_path": "/tmp/cache",
            "returncode": 0,
            "stdout": "0",
            "stderr": "",
            "state": 0,
            "valid": True,
        },
        "valid": True,
    }


def process_control() -> dict[str, object]:
    return {
        "schema_version": "dancers-process-control-v1",
        "process_started": True,
        "timed_out": False,
        "child_reaped": True,
        "process_group_empty_after_reap": True,
        "wait_interrupted_count": 0,
        "anomaly": False,
        "events": [
            {
                "stage": "wait-before-timeout",
                "operation": "wait4",
                "signal": None,
                "outcome": "reaped",
                "error": None,
            },
            {
                "stage": "group-empty-check",
                "operation": "killpg",
                "signal": None,
                "outcome": "empty",
                "error": None,
            },
        ],
        "valid": True,
    }


def timing_run_record() -> dict[str, object]:
    attempt_id = "attempt-001"
    provenance = {
        "run_id": attempt_id,
        "implementation_id": "reference",
        "algorithm_revision": "revision",
        "source_head_revision": "1" * 40,
        "source_tree_hash": "2" * 40,
        "common_patch_sha256": "3" * 64,
        "common_patch_full_index_diff_sha256": "4" * 64,
        "binary_sha256": "5" * 64,
        "build_manifest_sha256": "6" * 64,
        "pixi_lock_sha256": "7" * 64,
        "protocol_sha256": "8" * 64,
        "experiment_manifest_sha256": "9" * 64,
        "analysis_revision_sha256": "a" * 64,
        "runner_sha256": "b" * 64,
        "config_sha256": "c" * 64,
        "map_sha256": "d" * 64,
        "scenario_sha256": "e" * 64,
        "omp_threads": 1,
        "phase": "calibration",
        "mode": "timing",
        "pair_id": "pair-001",
        "repetition": 1,
        "order": "AB",
        "attempt_kind": "measured",
        "maximum_peak_rss_mib": 49_152.0,
        "power_mode": "AC Power",
        "thermal_policy": analysis.THERMAL_POLICY,
        "task_validity_predicate_id": "fixed-horizon-safe-navigation-v1",
    }
    return {
        "schema_version": "dancers-laptop-comparison-attempt-v2",
        "sequence": 1,
        "attempt_id": attempt_id,
        "block_id": "block-001",
        "pair_id": "pair-001",
        "repetition": 1,
        "attempt_kind": "measured",
        "order": "AB",
        "order_position": 1,
        "phase": "calibration",
        "mode": "timing",
        "environment": "fractal_high",
        "seed": 1,
        "robot_count": 192,
        "method": "reference",
        "terminal_category": "completed",
        "orchestration_error": None,
        "resource": {
            "returncode": 0,
            "exit_code": 0,
            "signal": None,
            "timed_out": False,
            "wall_seconds": 1.5,
            "user_cpu_seconds": 0.75,
            "system_cpu_seconds": 0.25,
            "total_cpu_seconds": 1.0,
            "peak_rss_raw": 1_048_576,
            "peak_rss_raw_unit": "bytes",
            "peak_rss_mib": 1.0,
            "maximum_peak_rss_mib": 49_152.0,
            "peak_rss_within_limit": True,
        },
        "provenance": provenance,
        "artifacts": {
            "config": {"path": "/tmp/config", "sha256": "c" * 64},
            "stdout": {"path": "/tmp/stdout", "sha256": "f" * 64},
            "stderr": {"path": "/tmp/stderr", "sha256": "0" * 64},
            "trace": None,
            "summary": None,
        },
        "completed_utc": "2026-07-28T12:00:01Z",
        "task_valid": None,
        "fidelity_metrics": None,
        "host_conditions_before_attempt": host_snapshot(percent=90),
        "host_conditions_after_attempt": host_snapshot(
            percent=90,
            stage="after-attempt",
            attempt_before=90,
        ),
        "host_attempt_valid": True,
        "process_control": process_control(),
    }


def fidelity_pair_record() -> dict[str, object]:
    return {
        "schema_version": "dancers-laptop-comparison-pair-v2",
        "block_id": "block-001",
        "pair_id": "pair-001",
        "repetition": 1,
        "order": "AB",
        "phase": "calibration",
        "mode": "fidelity",
        "environment": "fractal_high",
        "seed": 1,
        "robot_count": 192,
        "attempt_ids": {
            "reference": "attempt-reference",
            "optimized": "attempt-optimized",
        },
        "config_sha256_equal": True,
        "terminal_categories": {
            "reference": "completed",
            "optimized": "completed",
        },
        "semantic_trace_equal": True,
        "frames_compared": 200,
        "mismatch": None,
        "pair_valid": True,
        "paper_metrics_equal": True,
        "summary_parity_fields_equal": True,
        "summary_mismatch": None,
        "task_validity_predicate_id": (
            "fixed-horizon-safe-navigation-v1"
        ),
        "task_valid": True,
        "host_conditions_before": host_snapshot(
            stage="block-boundary"
        ),
        "host_conditions_after": host_snapshot(
            stage="block-boundary"
        ),
        "attempt_host_validity": {
            "reference": True,
            "optimized": True,
        },
        "host_block_valid": True,
        "block_terminal_category": "completed",
    }


def fidelity_summary(
    provenance: dict[str, object],
    *,
    robot_count: int = 192,
    seed: int = 1,
) -> dict[str, object]:
    summary: dict[str, object] = {
        "schema_version": "dancers-comparison-summary-v1",
        "run_valid": True,
        "final_tick": 100,
        "final_robot_count": robot_count,
        "maximum_robot_count": robot_count,
        "frames_recorded": 200,
        "termination_reason": "maximum_time",
        "trace_detail": "digest",
        "invalid_geometry_evaluations": 0,
        "invalid_robot_frames": 0,
        "nonfinite_numeric_observations": 0,
        "nonfinite_state_observations": 0,
        "metadata": {
            field: provenance[field]
            for field in (
                "run_id",
                "implementation_id",
                "algorithm_revision",
                "common_patch_sha256",
                "binary_sha256",
                "pixi_lock_sha256",
                "config_sha256",
                "map_sha256",
                "scenario_sha256",
                "omp_threads",
            )
        },
        "resolved_config": {
            "max_timesteps": 100,
            "configured_robot_count": robot_count,
            "rng_seed": seed,
            "display": False,
        },
        "paper_metrics": {
            "consensus_reached": 1,
            "final_connectivity": 0.75,
            "final_decision": 1,
            "final_heading_error": 0.1,
            "final_iterations": 5,
            "final_position_error": 0.2,
        },
        "all_goals_reached": False,
        "goals_reached": 0,
        "goals_total": robot_count,
        "first_all_goals_tick": None,
        "iterations": 5,
        "total_path_length_m": 12.5,
        "mean_path_length_m_per_robot": 0.5,
        "per_robot_goal_tick": {},
        "per_robot_path_length_m": {},
        "obstacle_collision_events": 0,
        "obstacle_collision_exposure_seconds": 0.0,
        "pair_collision_events": 0,
        "pair_collision_exposure_seconds": 0.0,
        "minimum_obstacle_clearance_m": 0.25,
        "minimum_pair_clearance_m": 0.3,
    }
    return summary


class AmendedRecordValidationTests(unittest.TestCase):
    def test_host_attempt_recomputes_battery_boundary(self) -> None:
        before = host_snapshot(percent=90)
        after = host_snapshot(
            percent=89,
            stage="after-attempt",
            attempt_before=90,
        )
        analysis.validate_host_snapshot(before, "before")
        analysis.validate_host_snapshot(after, "after")
        self.assertTrue(analysis.host_attempt_valid(before, after))

        bad_after = host_snapshot(
            percent=88,
            stage="after-attempt",
            attempt_before=90,
        )
        bad_after["power_policy_evaluation"]["valid"] = False
        bad_after["power_policy_evaluation"]["violations"] = [
            "attempt-boundary-drop"
        ]
        bad_after["valid"] = False
        analysis.validate_host_snapshot(bad_after, "bad_after")
        self.assertFalse(analysis.host_attempt_valid(before, bad_after))

    def test_host_abort_requires_one_complete_failure_explanation(self) -> None:
        failed = host_snapshot(
            percent=87,
            stage="before-attempt",
            cooldown_baseline=90,
        )
        failed["power_policy_evaluation"]["valid"] = False
        failed["power_policy_evaluation"]["violations"] = [
            "session-drop-exceeds-2"
        ]
        failed["valid"] = False
        abort = {
            "stage": "before-attempt",
            "attempt_id": "000001__block-001__reference",
            "block_id": "block-001",
            "method": "reference",
            "order_position": 1,
            "failed_snapshot": failed,
            "probe_error": None,
            "reason": "before-attempt host-power stability failed",
        }
        analysis.validate_host_abort(abort)
        ambiguous = copy.deepcopy(abort)
        ambiguous["probe_error"] = "OSError: probe failed"
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_host_abort(ambiguous)

    def test_host_snapshot_rejects_self_reported_valid_contradiction(self) -> None:
        snapshot = host_snapshot()
        snapshot["power_probe"]["source"] = "Battery Power"
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_host_snapshot(snapshot)

        cumulative_drop = host_snapshot(
            percent=87,
            stage="before-attempt",
            cooldown_baseline=90,
        )
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_host_snapshot(cumulative_drop)

    def test_process_control_recomputes_validity_and_rejects_bad_events(
        self,
    ) -> None:
        record = process_control()
        analysis.validate_process_control(record)

        unreaped = copy.deepcopy(record)
        unreaped["child_reaped"] = False
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_process_control(unreaped)

        malformed = copy.deepcopy(record)
        malformed["events"][0]["outcome"] = "made-up"
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_process_control(malformed)

        eperm = process_control()
        eperm["timed_out"] = True
        eperm["anomaly"] = True
        eperm["valid"] = False
        eperm["events"] = [
            {
                "stage": "wait-before-timeout",
                "operation": "wait4",
                "signal": None,
                "outcome": "not-ready",
                "error": None,
            },
            {
                "stage": "sigterm",
                "operation": "killpg",
                "signal": "SIGTERM",
                "outcome": "eperm",
                "error": "PermissionError: denied",
            },
            {
                "stage": "sigterm",
                "operation": "kill",
                "signal": "SIGTERM",
                "outcome": "sent",
                "error": None,
            },
            {
                "stage": "wait-after-sigterm",
                "operation": "wait4",
                "signal": None,
                "outcome": "reaped",
                "error": None,
            },
            {
                "stage": "group-empty-check",
                "operation": "killpg",
                "signal": None,
                "outcome": "empty",
                "error": None,
            },
        ]
        analysis.validate_process_control(eperm)

    def test_run_and_pair_records_normalize_only_recomputed_validity(
        self,
    ) -> None:
        normalized_run = analysis.validate_run_record(timing_run_record())
        self.assertTrue(normalized_run["host_valid"])
        self.assertTrue(normalized_run["process_control_valid"])
        self.assertEqual(normalized_run["total_cpu_seconds"], 1.0)

        normalized_pair = analysis.validate_pair_record(
            fidelity_pair_record()
        )
        self.assertTrue(normalized_pair["pair_valid"])
        self.assertEqual(normalized_pair["frames_compared"], 200)

        bad_pair = fidelity_pair_record()
        bad_pair["attempt_host_validity"]["reference"] = False
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_pair_record(bad_pair)

    def test_plan_artifact_paths_are_repository_anchored(self) -> None:
        repository = Path(analysis.__file__).resolve().parents[2]
        relative_paths = {
            "analysis_revision": (
                "scripts/comparison/analyze_results_v2.py"
            ),
            "analyzer_v1_base": "scripts/comparison/analyze_results.py",
            "trace_compare": "scripts/comparison/trace_compare.py",
            "runner": "scripts/comparison/run_paired_v2.py",
            "runner_v1_base": "scripts/comparison/run_paired.py",
            "freeze_capacity_decision": (
                "scripts/comparison/freeze_capacity_decision.py"
            ),
            "plan_freezer": "scripts/comparison/freeze_plan_v2.py",
            "amendment_protocol": (
                "docs/comparison/"
                "EXPERIMENT_PROTOCOL_AMENDMENT_FH_LOWER_V1.md"
            ),
            "post_outcome_amendment": (
                "docs/comparison/"
                "fractal_high_lower_count_amendment_v1.json"
            ),
            "experiment_manifest": (
                "docs/comparison/experiment_manifest.json"
            ),
            "environment_manifest": (
                "comparison/environments/manifest.json"
            ),
            "environment_manifest_sidecar": (
                "comparison/environments/manifest.sha256"
            ),
            "capacity_materializer": (
                "scripts/comparison/materialize_capacity_scenario.py"
            ),
            "environment_generator": (
                "scripts/comparison/generate_environments.py"
            ),
            "fractal_high_calibration_map": (
                "comparison/environments/fractal_high/calibration/map.png"
            ),
            "fractal_high_calibration_bank": (
                "comparison/environments/fractal_high/calibration/"
                "capacity/bank.json"
            ),
            "protocol": "docs/comparison/EXPERIMENT_PROTOCOL.md",
            "pixi_lock": "pixi.lock",
            "config_template": (
                "config/comparison/laptop_benchmark.yaml"
            ),
            "build_manifest": "comparison/build_manifest.json",
            "trigger_analysis": (
                "results/comparison/calibration/trace-v3/"
                "fractal-high-full-grid-fidelity-analysis.json"
            ),
            "trigger_frozen_plan": (
                "results/comparison/calibration/trace-v3/"
                "fractal-high-full-grid-fidelity/frozen_plan.json"
            ),
            "trigger_session": (
                "results/comparison/calibration/trace-v3/"
                "fractal-high-full-grid-fidelity/session.json"
            ),
            "trigger_runs": (
                "results/comparison/calibration/trace-v3/"
                "fractal-high-full-grid-fidelity/runs.jsonl"
            ),
            "trigger_pairs": (
                "results/comparison/calibration/trace-v3/"
                "fractal-high-full-grid-fidelity/pairs.jsonl"
            ),
        }
        artifacts = {
            name: {
                "path": str((repository / relative).resolve()),
                "sha256": (
                    analysis.POST_OUTCOME_AMENDMENT_SHA256
                    if name == "post_outcome_amendment"
                    else "0" * 64
                ),
            }
            for name, relative in relative_paths.items()
        }
        analysis.validate_plan_artifact_paths(
            artifacts,
            run_cwd=str(repository),
            mode="fidelity",
        )
        artifacts["runner"]["path"] = "/tmp/alternate-runner.py"
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_plan_artifact_paths(
                artifacts,
                run_cwd=str(repository),
                mode="fidelity",
            )

    def test_only_one_first_method_host_abort_may_trail_pairs(self) -> None:
        block = {
            "block_id": "block-002",
            "pair_id": "pair-002",
            "repetition": 1,
            "order": "BA",
            "environment": "fractal_high",
            "seed": 2,
            "robot_count": 192,
        }
        trailing = {
            "attempt_id": "attempt-trailing",
            **block,
            "method": "optimized",
            "order_position": 1,
            "host_attempt_valid": False,
            "sequence": 3,
        }
        host_abort = {
            "stage": "after-attempt",
            "attempt_id": "attempt-trailing",
            "block_id": "block-002",
            "method": "optimized",
            "order_position": 1,
        }
        admitted = analysis.validate_trailing_measured_prefix(
            [trailing],
            set(),
            [],
            [block],
            total_run_count=3,
            host_abort=host_abort,
        )
        self.assertIs(admitted, trailing)

        valid_first = copy.deepcopy(trailing)
        valid_first["host_attempt_valid"] = True
        before_second = {
            "stage": "before-attempt",
            "attempt_id": "000004__block-002__reference",
            "block_id": "block-002",
            "method": "reference",
            "order_position": 2,
        }
        admitted_before_second = (
            analysis.validate_trailing_measured_prefix(
                [valid_first],
                set(),
                [],
                [block],
                total_run_count=3,
                host_abort=before_second,
            )
        )
        self.assertIs(admitted_before_second, valid_first)

        contained_failure = copy.deepcopy(valid_first)
        contained_failure.update(
            {
                "terminal_category": "measurement-wrapper-failure",
                "orchestration_error": (
                    "post-containment orchestration failure: ledger seal"
                ),
                "process_control": {
                    "process_started": True,
                    "child_reaped": True,
                    "process_group_empty_after_reap": True,
                },
            }
        )
        admitted_contained_failure = (
            analysis.validate_trailing_measured_prefix(
                [contained_failure],
                set(),
                [],
                [block],
                total_run_count=3,
                host_abort=None,
            )
        )
        self.assertIs(admitted_contained_failure, contained_failure)
        hostile_contained_failure = copy.deepcopy(contained_failure)
        hostile_contained_failure["terminal_category"] = "completed"
        with self.assertRaisesRegex(
            analysis.AnalysisError, "requires session.host_abort"
        ):
            analysis.validate_trailing_measured_prefix(
                [hostile_contained_failure],
                set(),
                [],
                [block],
                total_run_count=3,
                host_abort=None,
            )

        second = {
            **copy.deepcopy(trailing),
            "attempt_id": "attempt-second",
            "method": "reference",
            "order_position": 2,
            "host_attempt_valid": False,
            "sequence": 4,
        }
        after_second = {
            "stage": "after-attempt",
            "attempt_id": "attempt-second",
            "block_id": "block-002",
            "method": "reference",
            "order_position": 2,
            "failed_snapshot": None,
        }
        analysis.validate_host_abort_block_prefix(
            after_second,
            block,
            [valid_first, second],
            ("optimized", "reference"),
            total_run_count=4,
        )

        wrong_method = copy.deepcopy(trailing)
        wrong_method["method"] = "reference"
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_trailing_measured_prefix(
                [wrong_method],
                set(),
                [],
                [block],
                total_run_count=3,
                host_abort=host_abort,
            )

        second_unpaired = copy.deepcopy(trailing)
        second_unpaired["attempt_id"] = "attempt-extra"
        with self.assertRaisesRegex(
            analysis.AnalysisError, "more than one trailing"
        ):
            analysis.validate_trailing_measured_prefix(
                [trailing, second_unpaired],
                set(),
                [],
                [block],
                total_run_count=4,
                host_abort=host_abort,
            )


class AdversarialBindingTests(unittest.TestCase):
    def test_battery_parser_is_byte_for_byte_differential_with_runner(
        self,
    ) -> None:
        valid = (
            "Now drawing from 'AC Power'\n"
            " -InternalBattery-0 (id=23658595)\t96%; charging; "
            "(no estimate) present: true\n"
        )
        estimated = valid.replace(
            "(no estimate) present: true",
            "23:59 remaining present: true",
        )
        cases = {
            "valid-no-estimate": valid,
            "valid-estimated": estimated,
            "valid-crlf": valid.replace("\n", "\r\n"),
            "legacy-no-estimate-remaining": valid.replace(
                "(no estimate) present",
                "(no estimate) remaining present",
            ),
            "estimated-missing-remaining": estimated.replace(
                "23:59 remaining present",
                "23:59 present",
            ),
            "estimated-short-minute": estimated.replace("23:59", "23:5"),
            "estimated-long-minute": estimated.replace("23:59", "23:599"),
            "zero-space-after-second-delimiter": valid.replace(
                "charging; (", "charging;("
            ),
            "extra-space-after-second-delimiter": valid.replace(
                "charging; (", "charging;  ("
            ),
            "tab-after-second-delimiter": valid.replace(
                "charging; (", "charging;\t("
            ),
            "unicode-space-after-second-delimiter": valid.replace(
                "charging; (", "charging;\u00a0("
            ),
            "unicode-status-space": valid.replace(
                "charging", "finishing\u2003charge"
            ),
            "double-space-before-present": valid.replace(
                ") present", ")  present"
            ),
            "tab-before-present": valid.replace(") present", ")\tpresent"),
            "unicode-space-before-present": valid.replace(
                ") present", ")\u00a0present"
            ),
            "present-false": valid.replace(
                "present: true", "present: false"
            ),
            "trailing-extra": valid.replace(
                "present: true", "present: true extra"
            ),
            "extra-line": valid + "diagnostic\n",
            "extra-battery": valid + valid.splitlines()[1] + "\n",
        }
        accepted = {"valid-no-estimate", "valid-estimated", "valid-crlf"}
        for name, stdout in cases.items():
            with self.subTest(name=name):
                observed = analysis._reparse_darwin_battery_probe(
                    returncode=0,
                    stdout=stdout,
                    stderr="",
                )
                expected = runner.parse_darwin_battery_probe(
                    returncode=0,
                    stdout=stdout,
                    stderr="",
                )
                self.assertEqual(observed, expected)
                if name in accepted:
                    self.assertTrue(observed["valid"], observed)
                else:
                    self.assertFalse(observed["valid"])

    def test_power_probe_is_reparsed_from_exact_command_and_stdout(
        self,
    ) -> None:
        snapshot = host_snapshot()
        analysis.validate_host_snapshot(snapshot)
        for mutation in ("command", "stdout", "unicode"):
            hostile = copy.deepcopy(snapshot)
            if mutation == "command":
                hostile["power_probe"]["command"] = ["pmset", "-g"]
            elif mutation == "stdout":
                hostile["power_probe"]["stdout"] = hostile["power_probe"][
                    "stdout"
                ].replace("90%", "89%")
            else:
                hostile["power_probe"]["stdout"] = hostile["power_probe"][
                    "stdout"
                ].replace("charged", "charg\u00a0ed")
            with self.subTest(mutation=mutation):
                with self.assertRaises(analysis.AnalysisError):
                    analysis.validate_host_snapshot(hostile)

    def test_zero_and_nonzero_orchestration_diagnostics_are_disjoint(
        self,
    ) -> None:
        error = "post-containment orchestration failure: OSError: injected"
        host_abort = {
            "stage": "orchestration",
            "attempt_id": "000001__block-001__reference",
            "block_id": "block-001",
            "method": "reference",
            "order_position": 1,
            "failed_snapshot": None,
            "probe_error": error,
            "reason": error,
        }
        validated_abort = analysis.validate_host_abort(host_abort)
        self.assertIsNotNone(validated_abort)
        zero_diagnostic = {
            "schema_version": "dancers-orchestration-abort-v2",
            "stage": "pre-first-attempt",
            "error": error,
            "process_started": False,
            "attempt_id": "000001__block-001__reference",
            "block_id": "block-001",
            "method": "reference",
            "order_position": 1,
            "completed_utc": "2026-07-28T12:00:00Z",
        }
        analysis.validate_orchestration_abort(
            zero_diagnostic,
            validated_abort,
        )
        block = {
            "block_id": "block-001",
            "order": "AB",
        }
        analysis.validate_host_abort_block_prefix(
            validated_abort,
            block,
            [],
            analysis.METHODS,
            total_run_count=0,
        )
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_host_abort_block_prefix(
                validated_abort,
                block,
                [],
                analysis.METHODS,
                total_run_count=1,
            )

        run = {
            "attempt_id": "000001__block-001__reference",
            "orchestration_error": error,
        }
        nonzero_diagnostic = {
            "schema_version": "dancers-orchestration-abort-v1",
            "error": error,
            "last_attempt_id": run["attempt_id"],
            "completed_utc": "2026-07-28T12:00:00Z",
        }
        analysis.validate_nonzero_orchestration_abort(
            nonzero_diagnostic,
            run,
        )
        hostile = copy.deepcopy(nonzero_diagnostic)
        hostile["last_attempt_id"] = "hidden-retry"
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_nonzero_orchestration_abort(hostile, run)

    def test_generic_abort_suffix_allows_only_terminal_one_or_two_attempts(
        self,
    ) -> None:
        error = "post-containment orchestration failure: OSError: injected"

        def attempt(
            position: int,
            *,
            kind: str = "measured",
            block_id: str = "block-001",
        ) -> dict[str, object]:
            return {
                "attempt_id": (
                    f"{position:06d}__{block_id}__"
                    f"{analysis.METHODS[position - 1]}"
                ),
                "block_id": block_id,
                "attempt_kind": kind,
                "order_position": position,
                "sequence": position,
                "terminal_category": (
                    "measurement-wrapper-failure"
                    if position == 2
                    else "completed"
                ),
                "orchestration_error": error if position == 2 else None,
                "host_attempt_valid": True,
                "process_control": process_control(),
            }

        first = attempt(1)
        second = attempt(2)
        diagnostic = {
            "schema_version": "dancers-orchestration-abort-v1",
            "error": error,
            "last_attempt_id": second["attempt_id"],
            "completed_utc": "2026-07-28T12:00:00Z",
        }
        pair = {"block_id": "block-001"}
        manifest = {"warmup_blocks": []}
        admitted = analysis.validate_generic_abort_suffix(
            [first, second],
            [pair],
            manifest,
            diagnostic,
            None,
        )
        self.assertIs(admitted, second)

        with self.assertRaisesRegex(
            analysis.AnalysisError, "terminal synthesized pair"
        ):
            analysis.validate_generic_abort_suffix(
                [first, second],
                [],
                manifest,
                diagnostic,
                None,
            )
        later = attempt(1, block_id="block-002")
        later["sequence"] = 3
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_generic_abort_suffix(
                [first, second, later],
                [pair],
                manifest,
                diagnostic,
                None,
            )

        warmup_first = attempt(1, kind="warmup", block_id="warmup-001")
        warmup_second = attempt(2, kind="warmup", block_id="warmup-001")
        warmup_diagnostic = copy.deepcopy(diagnostic)
        warmup_diagnostic["last_attempt_id"] = warmup_second["attempt_id"]
        admitted_warmup = analysis.validate_generic_abort_suffix(
            [warmup_first, warmup_second],
            [],
            {"warmup_blocks": [{"block_id": "warmup-001"}]},
            warmup_diagnostic,
            None,
        )
        self.assertIs(admitted_warmup, warmup_second)

    def test_exact_session_inventory_and_runs_csv_reject_hidden_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "session"
            (root / "attempts").mkdir(parents=True)
            (root / "blocks").mkdir()
            for name in (
                "frozen_plan.json",
                "preflight.json",
                "run_manifest.json",
                "runs.jsonl",
                "pairs.jsonl",
                "session.json",
                "orchestration_abort.json",
            ):
                (root / name).write_bytes(b"{}\n")
            (root / "runs.csv").write_bytes(
                analysis._expected_runs_csv_bytes([])
            )
            host_abort = {
                "stage": "orchestration",
                "block_id": "block-001",
            }
            guard = analysis._EvidenceGuard()
            try:
                analysis._validate_runs_csv(root, [], guard)
                analysis._validate_session_inventory(
                    root,
                    runs=[],
                    pairs=[],
                    manifest={"warmup_blocks": []},
                    host_abort=host_abort,
                    orchestration_abort_present=True,
                    guard=guard,
                )

                staged = root / "blocks" / "block-001"
                staged.mkdir()
                (staged / "config.yaml").write_text(
                    "staged: true\n", encoding="utf-8"
                )
                analysis._validate_session_inventory(
                    root,
                    runs=[],
                    pairs=[],
                    manifest={"warmup_blocks": []},
                    host_abort=host_abort,
                    orchestration_abort_present=True,
                    guard=guard,
                )

                (root / ".hidden-retry").write_bytes(b"unlisted")
                with self.assertRaisesRegex(
                    analysis.AnalysisError, "inventory mismatch"
                ):
                    analysis._validate_session_inventory(
                        root,
                        runs=[],
                        pairs=[],
                        manifest={"warmup_blocks": []},
                        host_abort=host_abort,
                        orchestration_abort_present=True,
                        guard=guard,
                    )
            finally:
                guard.close()

    def test_runs_csv_requires_exact_runner_bytes_and_row_order(self) -> None:
        runs = [timing_run_record()]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            csv_path = root / "runs.csv"
            csv_path.write_bytes(analysis._expected_runs_csv_bytes(runs))
            guard = analysis._EvidenceGuard()
            try:
                analysis._validate_runs_csv(root, runs, guard)
            finally:
                guard.close()
            csv_path.write_bytes(
                analysis._expected_runs_csv_bytes(list(reversed(runs)))
                + b"hidden,retry\n"
            )
            hostile_guard = analysis._EvidenceGuard()
            try:
                with self.assertRaisesRegex(
                    analysis.AnalysisError, "exactly match"
                ):
                    analysis._validate_runs_csv(
                        root,
                        runs,
                        hostile_guard,
                    )
            finally:
                hostile_guard.close()

    def test_concurrent_trace_summary_path_swap_is_detected_before_publish(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "summary.json"
            source.write_bytes(b'{"value":"admitted"}\n')
            guard = analysis._EvidenceGuard()
            try:
                snapshot = guard.capture(source, "summary")
                replacement = root / "replacement.json"
                replacement.write_bytes(b'{"value":"swapped"}\n')
                os.replace(replacement, source)
                admitted = analysis._load_json_snapshot(
                    snapshot,
                    "summary",
                )
                self.assertEqual(admitted, {"value": "admitted"})
                with self.assertRaisesRegex(
                    analysis.AnalysisError,
                    "identity changed",
                ):
                    guard.reverify()
            finally:
                guard.close()

    def test_inventory_is_reverified_immediately_before_publication(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "attempts").mkdir()
            (root / "blocks").mkdir()
            for name in (
                "frozen_plan.json",
                "preflight.json",
                "run_manifest.json",
                "runs.jsonl",
                "pairs.jsonl",
                "runs.csv",
                "session.json",
            ):
                (root / name).write_bytes(b"")
            guard = analysis._EvidenceGuard()
            try:
                inventory = analysis._scan_session_tree(root)
                guard.bind_inventory(root, inventory)
                guard.reverify()
                (root / "attempts" / ".retry").write_bytes(b"hidden")
                with self.assertRaisesRegex(
                    analysis.AnalysisError,
                    "session tree changed",
                ):
                    guard.reverify()
            finally:
                guard.close()

    def test_cumulative_baseline_cannot_reset_between_attempts(self) -> None:
        reset = host_snapshot(
            percent=88,
            stage="before-attempt",
            cooldown_baseline=88,
        )
        analysis.validate_host_snapshot(reset)
        with self.assertRaisesRegex(
            analysis.AnalysisError, "cooldown_baseline_percent"
        ):
            analysis.bind_cooldown_baseline(reset, 90, "attempt")

    def test_block_boundary_abort_binds_the_next_slot(self) -> None:
        block = {
            "block_id": "block-001",
            "pair_id": "pair-001",
            "repetition": 1,
            "order": "AB",
            "environment": "fractal_high",
            "seed": 1,
            "robot_count": 192,
        }
        failed = host_snapshot(
            percent=87,
            stage="block-boundary",
            cooldown_baseline=90,
        )
        failed["power_policy_evaluation"]["valid"] = False
        failed["power_policy_evaluation"]["violations"] = [
            "session-drop-exceeds-2"
        ]
        failed["valid"] = False
        abort = {
            "stage": "block-boundary",
            "attempt_id": "000001__block-001__reference",
            "block_id": "block-001",
            "method": "reference",
            "order_position": 1,
            "failed_snapshot": failed,
            "probe_error": None,
            "reason": "block boundary failed before launch",
        }
        analysis.validate_host_abort(abort)
        analysis.validate_host_abort_block_prefix(
            abort,
            block,
            [],
            analysis.METHODS,
            total_run_count=0,
        )
        wrong = copy.deepcopy(abort)
        wrong["method"] = "optimized"
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_host_abort_block_prefix(
                wrong,
                block,
                [],
                analysis.METHODS,
                total_run_count=0,
            )

    def test_process_event_sequence_rejects_reordering_and_free_fallback(
        self,
    ) -> None:
        natural = process_control()
        reordered = copy.deepcopy(natural)
        reordered["events"].reverse()
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_process_control(reordered)

        fallback = process_control()
        fallback["timed_out"] = True
        fallback["events"] = [
            {
                "stage": "sigterm",
                "operation": "kill",
                "signal": "SIGTERM",
                "outcome": "sent",
                "error": None,
            },
            {
                "stage": "wait-after-sigterm",
                "operation": "wait4",
                "signal": None,
                "outcome": "reaped",
                "error": None,
            },
            {
                "stage": "group-empty-check",
                "operation": "killpg",
                "signal": None,
                "outcome": "empty",
                "error": None,
            },
        ]
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_process_control(fallback)

        no_process = {
            "schema_version": "dancers-process-control-v1",
            "process_started": False,
            "timed_out": None,
            "child_reaped": False,
            "process_group_empty_after_reap": None,
            "wait_interrupted_count": 0,
            "anomaly": True,
            "events": [],
            "valid": False,
        }
        analysis.validate_process_control(no_process)
        fictitious = copy.deepcopy(no_process)
        fictitious["events"] = [copy.deepcopy(natural["events"][0])]
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_process_control(fictitious)

    def test_every_observed_run_is_bound_directly_to_plan(self) -> None:
        run = timing_run_record()
        provenance = run["provenance"]
        plan = {
            "runner_sha256": provenance["runner_sha256"],
            "phase": run["phase"],
            "mode": run["mode"],
            "maximum_peak_rss_mib": provenance[
                "maximum_peak_rss_mib"
            ],
            "power_mode": provenance["power_mode"],
            "thermal_policy": provenance["thermal_policy"],
            "task_validity_predicate_id": provenance[
                "task_validity_predicate_id"
            ],
            "omp_threads": 1,
            "artifacts": {
                name: {"sha256": provenance[field]}
                for field, name in (
                    ("build_manifest_sha256", "build_manifest"),
                    ("pixi_lock_sha256", "pixi_lock"),
                    ("protocol_sha256", "protocol"),
                    (
                        "experiment_manifest_sha256",
                        "experiment_manifest",
                    ),
                    ("analysis_revision_sha256", "analysis_revision"),
                )
            },
            "methods": {
                "reference": {
                    "base_revision": provenance["algorithm_revision"],
                    "head_revision": provenance["source_head_revision"],
                    "tree_hash": provenance["source_tree_hash"],
                    "binary": {"sha256": provenance["binary_sha256"]},
                }
            },
            "pairs": [
                {
                    "pair_id": run["pair_id"],
                    "map": {"sha256": provenance["map_sha256"]},
                    "scenario": {
                        "sha256": provenance["scenario_sha256"]
                    },
                }
            ],
        }
        build = {
            "repository": {
                "common_patch_format_email_sha256": provenance[
                    "common_patch_sha256"
                ],
                "common_patch_full_index_diff_sha256": provenance[
                    "common_patch_full_index_diff_sha256"
                ],
            }
        }
        analysis.validate_run_provenance_binding(
            run, plan=plan, build_manifest=build, label="run"
        )
        for field in (
            "runner_sha256",
            "binary_sha256",
            "map_sha256",
            "pixi_lock_sha256",
        ):
            hostile = copy.deepcopy(run)
            hostile["provenance"][field] = "f" * 64
            with self.subTest(field=field):
                with self.assertRaises(analysis.AnalysisError):
                    analysis.validate_run_provenance_binding(
                        hostile,
                        plan=plan,
                        build_manifest=build,
                        label="run",
                    )

    def test_summary_metrics_are_recomputed_not_trusted(self) -> None:
        provenance = timing_run_record()["provenance"]
        summary = fidelity_summary(provenance)
        original = analysis.recompute_fidelity_metrics_from_summary(
            summary,
            provenance=provenance,
            robot_count=192,
            seed=1,
        )
        hostile_summary = copy.deepcopy(summary)
        hostile_summary["paper_metrics"]["final_iterations"] = 99
        recomputed = analysis.recompute_fidelity_metrics_from_summary(
            hostile_summary,
            provenance=provenance,
            robot_count=192,
            seed=1,
        )
        with self.assertRaises(analysis.AnalysisError):
            analysis._assert_exact_json_value(
                original, recomputed, "recorded fidelity metrics"
            )
        bool_as_int = copy.deepcopy(summary)
        bool_as_int["run_valid"] = 1
        with self.assertRaises(analysis.AnalysisError):
            analysis.recompute_fidelity_metrics_from_summary(
                bool_as_int,
                provenance=provenance,
                robot_count=192,
                seed=1,
            )

    def test_materialized_scenario_content_is_bound(self) -> None:
        scenario = {
            "num_robots": 2,
            "metadata": {
                "capacity_bank_robot_count": 471,
                "clearance_radius_px": 4,
                "split": "calibration",
                "environment_id": "fractal_high",
                "map_sha256": "a" * 64,
                "minimum_interrobot_center_distance_m": 2.0,
                "mission_seed": 1,
                "prefix_robot_count": 2,
                "capacity_bank_id": "b" * 64,
                "robot_radius_m": 1.0,
                "scenario_kind": "capacity-prefix",
                "world_size_m": 100.0,
            },
            "decisions": {"0": 1, "1": 2},
            "seed_robots": [],
            "waypoints": {
                "0": [[0.0, 0.0], [1.0, 1.0]],
                "1": [[1.0, 0.0], [0.0, 1.0]],
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scenario.json"
            pretty = (
                json.dumps(scenario, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            path.write_bytes(pretty)
            analysis._validate_materialized_scenario(
                path,
                expected_path=path,
                expected_sha256=analysis.sha256_file(path),
                expected_bytes=len(pretty),
                robot_count=2,
                map_sha256="a" * 64,
                capacity_bank_id="b" * 64,
                label="scenario",
            )
            hostile = copy.deepcopy(scenario)
            hostile["metadata"]["map_sha256"] = "c" * 64
            hostile_bytes = (
                json.dumps(hostile, indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            path.write_bytes(hostile_bytes)
            with self.assertRaises(analysis.AnalysisError):
                analysis._validate_materialized_scenario(
                    path,
                    expected_path=path,
                    expected_sha256=analysis.sha256_file(path),
                    expected_bytes=len(hostile_bytes),
                    robot_count=2,
                    map_sha256="a" * 64,
                    capacity_bank_id="b" * 64,
                    label="scenario",
                )

            duplicate = b'{"num_robots":2,"num_robots":2}\n'
            path.write_bytes(duplicate)
            with self.assertRaisesRegex(
                analysis.AnalysisError, "duplicate"
            ):
                analysis._validate_materialized_scenario(
                    path,
                    expected_path=path,
                    expected_sha256=analysis.sha256_file(path),
                    expected_bytes=len(duplicate),
                    robot_count=2,
                    map_sha256="a" * 64,
                    capacity_bank_id="b" * 64,
                    label="scenario",
                )

            nonfinite_scenario = copy.deepcopy(scenario)
            nonfinite_scenario["waypoints"]["0"][0][0] = float("nan")
            nonfinite = (
                json.dumps(
                    nonfinite_scenario,
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8")
            path.write_bytes(nonfinite)
            with self.assertRaisesRegex(
                analysis.AnalysisError, "non-standard JSON constant"
            ):
                analysis._validate_materialized_scenario(
                    path,
                    expected_path=path,
                    expected_sha256=hashlib.sha256(nonfinite).hexdigest(),
                    expected_bytes=len(nonfinite),
                    robot_count=2,
                    map_sha256="a" * 64,
                    capacity_bank_id="b" * 64,
                    label="scenario",
                )

            invalid_utf8 = pretty + b"\xff"
            path.write_bytes(invalid_utf8)
            with self.assertRaisesRegex(
                analysis.AnalysisError, "malformed JSON"
            ):
                analysis._validate_materialized_scenario(
                    path,
                    expected_path=path,
                    expected_sha256=hashlib.sha256(invalid_utf8).hexdigest(),
                    expected_bytes=len(invalid_utf8),
                    robot_count=2,
                    map_sha256="a" * 64,
                    capacity_bank_id="b" * 64,
                    label="scenario",
                )

            path.write_bytes(pretty + b" ")
            with self.assertRaisesRegex(
                analysis.AnalysisError, "sha256"
            ):
                analysis._validate_materialized_scenario(
                    path,
                    expected_path=path,
                    expected_sha256=hashlib.sha256(pretty).hexdigest(),
                    expected_bytes=len(pretty) + 1,
                    robot_count=2,
                    map_sha256="a" * 64,
                    capacity_bank_id="b" * 64,
                    label="scenario",
                )

    def test_real_freezer_plan_accepts_registered_pretty_scenarios(
        self,
    ) -> None:
        repository = Path(analysis.__file__).resolve().parents[2]
        plan_path = (
            repository
            / "comparison/plans/calibration/trace-v3-amended-v1/"
            "fractal-high-lower-count-fidelity/plan.json"
        )
        plan = json.loads(plan_path.read_bytes())
        analyzer_sha = analysis.sha256_file(Path(analysis.__file__))
        plan["analyzer_sha256"] = analyzer_sha
        plan["artifacts"]["analysis_revision"]["sha256"] = analyzer_sha
        runner_sha = analysis.sha256_file(Path(runner.__file__))
        plan["runner_sha256"] = runner_sha
        plan["artifacts"]["runner"]["sha256"] = runner_sha
        validated = analysis.validate_plan(plan)
        self.assertEqual(len(validated["pairs"]), 20)
        scenario_by_count = {
            pair["robot_count"]: pair["scenario"]
            for pair in validated["pairs"]
        }
        self.assertEqual(
            scenario_by_count[192]["sha256"],
            "add7ddc2b610b0dcd583f97854cf6c918e7640788b6489c3df114634adfd1d22",
        )
        self.assertEqual(
            scenario_by_count[256]["sha256"],
            "a0a68737ab6c52445ca0bfe5f64c328cee223752c90c8930d339201bbd165023",
        )

    def test_registered_pair_contract_has_exact_paths_and_dual_hashes(
        self,
    ) -> None:
        repository = Path(analysis.__file__).resolve().parents[2]
        paths = {
            "post_outcome_amendment": (
                repository
                / "docs/comparison/"
                "fractal_high_lower_count_amendment_v1.json"
            ),
            "environment_manifest": (
                repository / "comparison/environments/manifest.json"
            ),
            "fractal_high_calibration_map": (
                repository
                / "comparison/environments/fractal_high/calibration/map.png"
            ),
        }
        artifacts = {
            name: {
                "path": str(path),
                "sha256": analysis.sha256_file(path),
            }
            for name, path in paths.items()
        }
        contract = analysis._registered_pair_artifact_contract(
            artifacts, mode="fidelity", label="plan"
        )
        self.assertEqual(
            contract["prefixes"][192]["path"],
            (
                repository
                / "comparison/plans/calibration/trace-v3-amended-v1/"
                "fractal-high-lower-count-fidelity/scenarios/n-192.json"
            ),
        )
        hostile = copy.deepcopy(artifacts)
        hostile["fractal_high_calibration_map"]["sha256"] = "0" * 64
        with self.assertRaises(analysis.AnalysisError):
            analysis._registered_pair_artifact_contract(
                hostile, mode="fidelity", label="plan"
            )

    def test_build_manifest_binds_method_identity_binary_and_pixi(
        self,
    ) -> None:
        repository = Path(analysis.__file__).resolve().parents[2]
        manifest_path = repository / "comparison/build_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        methods = {}
        for method in analysis.METHODS:
            build = manifest["builds"][f"{method}_timing"]
            binary = repository / build["binary_path"]
            methods[method] = {
                "source_dir": str(repository / build["source_dir"]),
                "base_revision": build["algorithm_revision"],
                "head_revision": build["applied_head"],
                "tree_hash": manifest["repository"][f"{method}_tree"],
                "binary": {
                    "path": str(binary),
                    "sha256": analysis.sha256_file(binary),
                },
            }
        artifacts = {
            "build_manifest": {
                "path": str(manifest_path),
                "sha256": analysis.sha256_file(manifest_path),
            },
            "pixi_lock": {
                "path": str(repository / "pixi.lock"),
                "sha256": analysis.sha256_file(repository / "pixi.lock"),
            },
        }
        analysis._validate_build_manifest_bindings(
            artifacts, methods, mode="timing", label="plan"
        )
        hostile = copy.deepcopy(methods)
        hostile["reference"]["head_revision"] = "0" * 40
        with self.assertRaises(analysis.AnalysisError):
            analysis._validate_build_manifest_bindings(
                artifacts, hostile, mode="timing", label="plan"
            )

    def test_preflight_requires_complete_exact_plan_attestation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            environment = root / "environment.json"
            build = root / "build.json"
            binary = root / "binary"
            map_path = root / "map.png"
            scenario = root / "scenario.json"
            for path, payload in (
                (environment, b"{}"),
                (build, b"{}"),
                (binary, b"binary"),
                (map_path, b"map"),
                (scenario, b"scenario"),
            ):
                path.write_bytes(payload)
            artifact_paths = {
                "environment_manifest": environment,
                "build_manifest": build,
            }
            artifacts = {
                name: {
                    "path": str(path),
                    "sha256": analysis.sha256_file(path),
                }
                for name, path in artifact_paths.items()
            }
            method_record = {
                "source_dir": str(root),
                "base_revision": "1" * 40,
                "head_revision": "2" * 40,
                "tree_hash": "3" * 40,
                "binary": {
                    "path": str(binary),
                    "sha256": analysis.sha256_file(binary),
                },
            }
            pair = {
                "pair_id": "fractal_high-calibration-seed-1-n-192",
                "map": {
                    "path": str(map_path),
                    "sha256": analysis.sha256_file(map_path),
                },
                "scenario": {
                    "path": str(scenario),
                    "sha256": analysis.sha256_file(scenario),
                },
            }
            plan = {
                "phase": "calibration",
                "mode": "timing",
                "runner_sha256": "a" * 64,
                "timestep_seconds": 0.1,
                "idle_cooldown_seconds": 300,
                "minimum_free_disk_bytes": 21_474_836_480,
                "maximum_peak_rss_mib": 49_152.0,
                "power_mode": analysis.POWER_MODE,
                "thermal_policy": analysis.THERMAL_POLICY,
                "task_validity_predicate_id": (
                    analysis.TASK_VALIDITY_PREDICATE_ID
                ),
                "artifacts": artifacts,
                "methods": {
                    "reference": copy.deepcopy(method_record),
                    "optimized": copy.deepcopy(method_record),
                },
                "pairs": [pair],
            }
            manifest = {
                "plan_path": str(root / "plan.json"),
                "preflight_host_conditions": {},
                "idle_cooldown": {},
            }

            def attested(path: Path) -> dict[str, object]:
                return {
                    "path": str(path),
                    "sha256": analysis.sha256_file(path),
                    "bytes": path.stat().st_size,
                }

            methods = {}
            for method in analysis.METHODS:
                methods[method] = {
                    "method_id": method,
                    **copy.deepcopy(method_record),
                    "binary": attested(binary),
                    "observer_markers": {
                        marker: False
                        for marker in analysis.OBSERVER_MARKERS
                    },
                }
            preflight = {
                "schema_version": analysis.PLAN_SCHEMA,
                "verified_utc": "2026-07-28T12:00:00Z",
                "plan_path": manifest["plan_path"],
                "plan_sha256": "b" * 64,
                "phase": "calibration",
                "mode": "timing",
                "artifacts": {
                    name: attested(path)
                    for name, path in artifact_paths.items()
                },
                "methods": methods,
                "pairs": {
                    pair["pair_id"]: {
                        "map": attested(map_path),
                        "scenario": attested(scenario),
                        "registry": {
                            "family": "fractal_high",
                            "split": "calibration",
                            "registration": "exact-N-prefix",
                        },
                    }
                },
                "environment_registry": {},
                "runner_sha256": plan["runner_sha256"],
                "build_attestation": {},
                "host_conditions": {},
                "idle_cooldown": {},
                "resource_controls": {
                    field: plan[field]
                    for field in (
                        "timestep_seconds",
                        "idle_cooldown_seconds",
                        "minimum_free_disk_bytes",
                        "maximum_peak_rss_mib",
                        "power_mode",
                        "thermal_policy",
                        "task_validity_predicate_id",
                    )
                },
                "power_policy": copy.deepcopy(
                    analysis.EXPECTED_POWER_POLICY
                ),
                "process_control_policy": copy.deepcopy(
                    analysis.EXPECTED_PROCESS_CONTROL_POLICY
                ),
                "amendment_id": analysis.AMENDMENT_ID,
            }
            with (
                mock.patch.object(
                    analysis,
                    "_expected_environment_registry",
                    return_value={},
                ),
                mock.patch.object(
                    analysis, "_validate_preflight_build_attestation"
                ),
            ):
                analysis.validate_preflight_record(
                    preflight,
                    plan=plan,
                    manifest=manifest,
                    plan_sha256="b" * 64,
                )
                partial = copy.deepcopy(preflight)
                partial.pop("methods")
                with self.assertRaises(analysis.AnalysisError):
                    analysis.validate_preflight_record(
                        partial,
                        plan=plan,
                        manifest=manifest,
                        plan_sha256="b" * 64,
                    )
                wrong_type = copy.deepcopy(preflight)
                wrong_type["resource_controls"][
                    "maximum_peak_rss_mib"
                ] = 49_152
                with self.assertRaises(analysis.AnalysisError):
                    analysis.validate_preflight_record(
                        wrong_type,
                        plan=plan,
                        manifest=manifest,
                        plan_sha256="b" * 64,
                    )

    def test_strict_json_types_reject_bool_and_numeric_substitution(
        self,
    ) -> None:
        for actual, expected in ((True, 1), (300, 300.0), (1, True)):
            with self.subTest(actual=actual, expected=expected):
                with self.assertRaises(analysis.AnalysisError):
                    analysis._assert_exact_json_scalar(
                        actual, expected, "control"
                    )
        hostile_policy = copy.deepcopy(analysis.EXPECTED_POWER_POLICY)
        hostile_policy["present_required"] = 1
        with self.assertRaises(analysis.AnalysisError):
            analysis.validate_power_policy(hostile_policy)


class RegisteredTimingScreenTests(unittest.TestCase):
    def test_exact_registered_estimator_and_bootstrap_are_deterministic(
        self,
    ) -> None:
        observations = timing_observations()
        first = analysis.registered_timing_screen(observations)
        second = analysis.registered_timing_screen(
            list(reversed(copy.deepcopy(observations)))
        )

        self.assertEqual(first, second)
        self.assertTrue(first["available"])
        self.assertTrue(first["progression_pass"])
        self.assertEqual(first["observed_attempt_count"], 120)
        self.assertEqual(
            first["bootstrap"],
            {
                "cluster": "seed",
                "draws": 10_000,
                "confidence_level": 0.95,
                "interval": "percentile-linear-interpolation",
                "rng": "MT19937",
                "seed": 250_818_153,
            },
        )

        cpu_ratios = [0.70 + 0.01 * seed for seed in analysis.SEEDS]
        wall_ratios = [0.75 + 0.005 * seed for seed in analysis.SEEDS]
        expected_cpu = math.exp(
            sum(math.log(value) for value in cpu_ratios)
            / len(cpu_ratios)
        )
        expected_wall = math.exp(
            sum(math.log(value) for value in wall_ratios)
            / len(wall_ratios)
        )
        self.assertAlmostEqual(
            first["metrics"]["total_cpu_seconds"][
                "geometric_mean_optimized_n256_over_reference_n192_ratio"
            ],
            expected_cpu,
        )
        self.assertAlmostEqual(
            first["metrics"]["wall_seconds"][
                "geometric_mean_optimized_n256_over_reference_n192_ratio"
            ],
            expected_wall,
        )
        self.assertEqual(
            first["metrics"]["total_cpu_seconds"]["ratio_ci95"],
            [0.7373284720837228, 0.7725111279470328],
        )
        self.assertEqual(
            first["metrics"]["wall_seconds"]["ratio_ci95"],
            [0.7688368102542197, 0.7863811531212939],
        )
        self.assertEqual(
            first["optimized_n256_peak_rss_mib"],
            1_000.0 + 256 + 10 + 0.3,
        )

    def test_missing_nonpositive_failed_or_invalid_cell_fails_closed(
        self,
    ) -> None:
        mutations = []

        missing = timing_observations()
        missing.pop()
        mutations.append((missing, "missing-cell:"))

        nonpositive = timing_observations()
        nonpositive[0]["total_cpu_seconds"] = 0.0
        mutations.append((nonpositive, "invalid-cell:"))

        failed = timing_observations()
        failed[0]["terminal_category"] = "timeout"
        mutations.append((failed, "failed-attempt:"))

        host_invalid = timing_observations()
        host_invalid[0]["host_valid"] = False
        mutations.append((host_invalid, "host-invalid:"))

        process_invalid = timing_observations()
        process_invalid[0]["process_control_valid"] = False
        mutations.append((process_invalid, "process-control-invalid:"))

        for observations, reason_prefix in mutations:
            with self.subTest(reason=reason_prefix):
                result = analysis.registered_timing_screen(observations)
                self.assertFalse(result["available"])
                self.assertFalse(result["progression_pass"])
                self.assertIsNone(result["metrics"])
                self.assertIsNone(result["per_seed_ratios"])
                self.assertTrue(
                    any(
                        reason.startswith(reason_prefix)
                        for reason in result["failure_reasons"]
                    )
                )

    def test_upper_bound_and_rss_are_joint_progression_requirements(
        self,
    ) -> None:
        observations = timing_observations()
        for observation in observations:
            if (
                observation["method"] == "optimized"
                and observation["robot_count"] == 256
            ):
                observation["peak_rss_mib"] = 49_152.1
        result = analysis.registered_timing_screen(observations)
        self.assertTrue(result["available"])
        self.assertFalse(result["rss_within_limit"])
        self.assertFalse(result["progression_pass"])

    def test_invalid_session_completion_power_suppresses_all_estimates(
        self,
    ) -> None:
        combined = analysis.analyze_validated_sessions(
            [
                {
                    "mode": "timing",
                    "design_role": "exploratory-screening-only",
                    "attempts": timing_observations(),
                    "pairs": [],
                    "attempt_cells": [],
                    "session_power_valid": False,
                    "input_session": {"path": "/tmp/power-invalid"},
                }
            ]
        )
        screen = combined["timing_screen"]
        self.assertFalse(screen["available"])
        self.assertFalse(screen["progression_pass"])
        self.assertIsNone(screen["metrics"])
        self.assertEqual(
            screen["failure_reasons"],
            ["session-power-invalid:/tmp/power-invalid"],
        )

    def test_partial_sessions_cannot_be_pooled(self) -> None:
        session = {
            "mode": "timing",
            "design_role": "exploratory-screening-only",
            "attempts": timing_observations()[:60],
            "pairs": [],
            "attempt_cells": [],
            "session_power_valid": True,
            "input_session": {"path": "/tmp/partial"},
        }
        with self.assertRaisesRegex(
            analysis.AnalysisError, "pooling or resuming"
        ):
            analysis.analyze_validated_sessions(
                [session, copy.deepcopy(session)]
            )


class RegisteredFidelityGateTests(unittest.TestCase):
    def test_complete_gate_and_receipt(self) -> None:
        gate = analysis.registered_fidelity_gate(
            fidelity_attempts(), fidelity_pairs()
        )
        self.assertTrue(gate["gate_pass"])
        self.assertEqual(gate["completed_attempt_count"], 40)
        self.assertEqual(gate["valid_pair_count"], 20)
        self.assertEqual(gate["host_valid_pair_count"], 20)
        self.assertEqual(gate["frames_compared_per_pair"], 200)

        receipt = analysis.fidelity_gate_receipt(
            analyzer_sha256="a" * 64,
            input_sessions=[
                {
                    "path": "/tmp/session",
                    "frozen_plan_json_sha256": "0" * 64,
                    "run_manifest_json_sha256": "1" * 64,
                    "preflight_json_sha256": "2" * 64,
                    "session_json_sha256": "b" * 64,
                    "runs_jsonl_sha256": "c" * 64,
                    "pairs_jsonl_sha256": "d" * 64,
                }
            ],
            gate=gate,
        )
        self.assertEqual(
            set(receipt),
            {
                "schema_version",
                "amendment_id",
                "design_role",
                "analyzer_sha256",
                "input_sessions",
                "planned_method_attempt_slots",
                "executed_attempt_count",
                "planned_pair_blocks",
                "completed_attempt_count",
                "valid_pair_count",
                "host_valid_pair_count",
                "frames_compared_per_pair",
                "gate_pass",
            },
        )
        self.assertEqual(
            receipt["schema_version"],
            "dancers-amended-fidelity-gate-v1",
        )
        self.assertEqual(
            set(receipt["input_sessions"][0]),
            {
                "path",
                "frozen_plan_json_sha256",
                "run_manifest_json_sha256",
                "preflight_json_sha256",
                "session_json_sha256",
                "runs_jsonl_sha256",
                "pairs_jsonl_sha256",
            },
        )

    def test_any_bad_attempt_or_pair_fails_the_complete_gate(self) -> None:
        attempts = fidelity_attempts()
        pairs = fidelity_pairs()
        attempts[0]["process_control_valid"] = False
        pairs[0]["frames_compared"] = 199
        gate = analysis.registered_fidelity_gate(attempts, pairs)
        self.assertFalse(gate["gate_pass"])
        self.assertIn(
            "attempt-000:process_control_valid",
            gate["attempt_failures"],
        )
        self.assertIn("pair-000:frames_compared:199", gate["pair_failures"])
        with self.assertRaises(analysis.AnalysisError):
            analysis.fidelity_gate_receipt(
                analyzer_sha256="a" * 64,
                input_sessions=[],
                gate=gate,
            )

    def test_invalid_session_completion_power_fails_fidelity_gate(self) -> None:
        cells = [
            {
                "method": method,
                "robot_count": count,
                "seed": seed,
                "repetition": 1,
            }
            for method in analysis.METHODS
            for count in analysis.COUNTS
            for seed in analysis.SEEDS
        ]
        combined = analysis.analyze_validated_sessions(
            [
                {
                    "mode": "fidelity",
                    "design_role": (
                        "outcome-informed-exploratory-fidelity"
                    ),
                    "attempts": fidelity_attempts(),
                    "pairs": fidelity_pairs(),
                    "attempt_cells": cells,
                    "session_power_valid": False,
                    "input_session": {"path": "/tmp/power-invalid"},
                }
            ]
        )
        gate = combined["fidelity_gate"]
        self.assertFalse(gate["gate_pass"])
        self.assertIn(
            "session-power-invalid:/tmp/power-invalid",
            gate["attempt_failures"],
        )

    def test_failed_gate_writes_analysis_but_not_receipt(self) -> None:
        failed_session = {
            "mode": "fidelity",
            "design_role": "outcome-informed-exploratory-fidelity",
            "attempts": [],
            "pairs": [],
            "attempt_cells": [],
            "session_power_valid": False,
            "input_session": {"path": "/tmp/failed-session"},
        }
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "analysis.json"
            receipt = Path(temporary) / "receipt.json"
            with mock.patch.object(
                analysis,
                "validate_session",
                return_value=failed_session,
            ):
                status = analysis.main(
                    [
                        "--session",
                        "/tmp/failed-session",
                        "--output",
                        str(output),
                        "--fidelity-gate-receipt-output",
                        str(receipt),
                    ]
                )
            self.assertEqual(status, 1)
            self.assertTrue(output.is_file())
            self.assertFalse(receipt.exists())
            written = json.loads(output.read_text(encoding="utf-8"))
            self.assertFalse(written["fidelity_gate"]["gate_pass"])


if __name__ == "__main__":
    unittest.main()
