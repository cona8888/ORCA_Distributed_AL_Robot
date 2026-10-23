from __future__ import annotations

import contextlib
import errno
import hashlib
import importlib.util
import io
import json
import os
import py_compile
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import MappingProxyType
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = (
    REPO_ROOT / "scripts/comparison/analyze_visual_cohort_v1.py"
)
SPEC = importlib.util.spec_from_file_location(
    "analyze_visual_cohort_v1",
    MODULE_PATH,
)
assert SPEC and SPEC.loader
ANALYZER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ANALYZER
SPEC.loader.exec_module(ANALYZER)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _artifact(relative_path: str, data: bytes) -> dict[str, object]:
    return {
        "bytes": len(data),
        "path": relative_path,
        "sha256": _sha256(data),
    }


def _trace_metadata(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        header = json.loads(handle.readline())
    return header["metadata"]


def _exact_trace(left: Path, right: Path) -> object:
    return ANALYZER.TraceComparison(
        equal=True,
        frames_compared=ANALYZER.FRAMES_PER_PAIR,
        left_provenance=_trace_metadata(left),
        right_provenance=_trace_metadata(right),
    )


def _discordant_trace(left: Path, right: Path) -> object:
    return ANALYZER.TraceComparison(
        equal=False,
        frames_compared=17,
        left_provenance=_trace_metadata(left),
        right_provenance=_trace_metadata(right),
    )


_VIEWER_BUILDER: object | None = None


def _authenticated_viewer_builder() -> object:
    global _VIEWER_BUILDER
    if _VIEWER_BUILDER is None:
        context = ANALYZER._validate_plan(
            REPO_ROOT,
            REPO_ROOT / ANALYZER.PLAN_RELATIVE_PATH,
        )
        runtime = ANALYZER._load_trace_comparator_runtime(
            context.trace_compare_path,
            context.trace_compare_bytes,
            context.trace_compare_sha256,
        )
        _VIEWER_BUILDER = ANALYZER._load_replay_builder(
            context.viewer_path,
            context.viewer_bytes,
            context.viewer_sha256,
            runtime.module,
        )
    return _VIEWER_BUILDER


class SyntheticScene:
    """Small authenticated tree using the real frozen plan and preflight."""

    def __init__(self, temporary_root: Path, *, collision: bool = False) -> None:
        self.results_root = temporary_root / "visual-results"
        self.plan = json.loads(
            (REPO_ROOT / ANALYZER.PLAN_RELATIVE_PATH).read_text(
                encoding="utf-8"
            )
        )
        self.scene = next(
            record
            for record in self.plan["scenes"]
            if record["family"] == "open"
            and record["robot_count"] == 8
            and record["seed"] == 1
        )
        self.scene_root = self.results_root / self.scene["output_relpath"]
        self.live_scene_root = (
            REPO_ROOT
            / ANALYZER.RESULTS_RELATIVE_ROOT
            / self.scene["output_relpath"]
        )
        live_summary = json.loads(
            (self.live_scene_root / "reference/summary.json").read_text(
                encoding="utf-8"
            )
        )
        self.resolved_config = live_summary["resolved_config"]
        with (
            self.live_scene_root / "reference/trace.jsonl"
        ).open("r", encoding="utf-8") as handle:
            self.trace_observer = json.loads(handle.readline())["observer"]
        (self.scene_root / "reference").mkdir(parents=True)
        (self.scene_root / "optimized").mkdir()
        self.collision = collision
        self._build()
        self._seal()

    def _write(self, relative: str, data: bytes) -> bytes:
        path = self.scene_root / relative
        path.write_bytes(data)
        return data

    def _metadata(self, method: str, config_sha: str) -> dict[str, object]:
        metadata = json.loads(
            (
                self.live_scene_root / method / "metadata.json"
            ).read_text(encoding="utf-8")
        )
        metadata["config_path"] = str(self.scene_root / "config.yaml")
        metadata["config_sha256"] = config_sha
        return metadata

    def _summary(
        self,
        method: str,
        metadata: dict[str, object],
    ) -> dict[str, object]:
        pair_events = 1 if self.collision else 0
        pair_exposure = (
            100 * 0.10000000149011612 if self.collision else 0.0
        )
        pair_clearance = -0.25 if self.collision else 1.0
        return {
            "all_goals_reached": False,
            "final_robot_count": 8,
            "final_tick": 100,
            "first_all_goals_tick": None,
            "frames_recorded": 200,
            "goals_reached": 0,
            "goals_total": 8,
            "invalid_geometry_evaluations": 0,
            "invalid_robot_frames": 0,
            "iterations": 200,
            "maximum_robot_count": 8,
            "mean_path_length_m_per_robot": 1.0,
            "metadata": metadata,
            "minimum_obstacle_clearance_m": 1.0,
            "minimum_pair_clearance_m": pair_clearance,
            "nonfinite_numeric_observations": 0,
            "nonfinite_state_observations": 0,
            "obstacle_collision_events": 0,
            "obstacle_collision_exposure_seconds": 0.0,
            "pair_collision_events": pair_events,
            "pair_collision_exposure_seconds": pair_exposure,
            "paper_metrics": {
                "consensus_reached": 0,
                "final_connectivity": 1.0,
                "final_decision": -1,
                "final_heading_error": 0.0,
                "final_iterations": 200,
                "final_position_error": 0.0,
            },
            "per_robot_goal_tick": {},
            "per_robot_path_length_m": {
                str(robot): 1.0 for robot in range(8)
            },
            "resolved_config": self.resolved_config,
            "run_valid": True,
            "schema_version": "dancers-comparison-summary-v1",
            "simulated_time_seconds": 10.0,
            "termination_reason": "maximum_time",
            "total_path_length_m": 8.0,
            "trace_detail": "positions",
            "trace_path": str(self.scene_root / method / "trace.jsonl"),
        }

    def _trace_bytes(self, metadata: dict[str, object]) -> bytes:
        records: list[dict[str, object]] = [
            {
                "metadata": metadata,
                "observer": self.trace_observer,
                "resolved_config": self.resolved_config,
                "schema_version": "dancers-comparison-trace-v3",
                "type": "header",
            }
        ]
        for frame_index in range(ANALYZER.FRAMES_PER_PAIR):
            phase = "pre_update" if frame_index % 2 == 0 else "post_update"
            tick = (
                frame_index // 2
                if phase == "pre_update"
                else (frame_index + 1) // 2
            )
            x_position = tick * 0.01
            robots = []
            for robot in range(8):
                y_position = (
                    1.75
                    if self.collision and robot == 1
                    else robot * 3.0
                )
                state = [x_position, y_position, 0.0, 0.0, 0.0, 0.0]
                state_hex = [
                    struct.pack(">d", float(value)).hex()
                    for value in state
                ]
                robots.append(
                    {
                        "bearing": 0.0,
                        "current_waypoint": None,
                        "final_goal": [1000.0, y_position],
                        "goal_reached": False,
                        "radius_m": 1.0,
                        "rid": robot,
                        "state": state,
                        "state_hex": state_hex,
                    }
                )
            records.append(
                {
                    "metrics": {
                        "all_goals_reached": False,
                        "cumulative_invalid_geometry_evaluations": 0,
                        "cumulative_obstacle_collision_exposure_seconds": 0.0,
                        "cumulative_pair_collision_exposure_seconds": (
                            tick * 0.10000000149011612
                            if self.collision
                            else 0.0
                        ),
                        "frame_min_obstacle_clearance_m": (
                            1.0 if phase == "post_update" else None
                        ),
                        "frame_min_pair_clearance_m": (
                            (-0.25 if self.collision else 1.0)
                            if phase == "post_update"
                            else None
                        ),
                        "goals_reached": 0,
                        "obstacle_collisions": 0,
                        "pair_collisions": (
                            1
                            if self.collision and phase == "post_update"
                            else 0
                        ),
                        "robot_count": 8,
                        "updated_this_frame": phase == "post_update",
                    },
                    "phase": phase,
                    "rng_state": {
                        "integer_mt19937": "0",
                        "normal_mt19937": "0",
                        "uniform_mt19937": "0",
                    },
                    "robots": robots,
                    "simulated_time_seconds": tick * 0.1,
                    "simulator_state": {
                        "iterations": frame_index,
                        "last_created_robots": [],
                        "last_junction_creation_clock": -1,
                        "mean_number_of_neighbours_hex": "0000000000000000",
                        "mean_number_of_neighbours_time_hex": "0000000000000000",
                        "next_robot_id": 8,
                        "robot_group_clock": 0,
                        "run_flag": True,
                        "simulation_mode": 1,
                    },
                    "tick": tick,
                    "type": "frame",
                }
            )
        return b"".join(
            ANALYZER.canonical_json_bytes(record) for record in records
        )

    def _build(self) -> None:
        live_config = (self.live_scene_root / "config.yaml").read_text(
            encoding="utf-8"
        )
        live_output = str(self.live_scene_root / "legacy-output.json")
        synthetic_output = str(self.scene_root / "legacy-output.json")
        self.asserted_replacement = (live_output, synthetic_output)
        if live_config.count(live_output) != 1:
            raise AssertionError("live config output path is not unique")
        config = self._write(
            "config.yaml",
            live_config.replace(live_output, synthetic_output).encode("utf-8"),
        )
        self._write(
            "frozen_plan.json",
            (REPO_ROOT / ANALYZER.PLAN_RELATIVE_PATH).read_bytes(),
        )
        self._write(
            "preflight.json",
            (
                REPO_ROOT
                / ANALYZER.RESULTS_RELATIVE_ROOT
                / "open/n-8/seed-01/preflight.json"
            ).read_bytes(),
        )
        method_artifacts: dict[str, dict[str, dict[str, object]]] = {}
        summaries: dict[str, dict[str, object]] = {}
        for method in ANALYZER.METHODS:
            metadata = self._metadata(method, _sha256(config))
            summary = self._summary(method, metadata)
            summaries[method] = summary
            content = {
                "metadata": self._write(
                    f"{method}/metadata.json",
                    ANALYZER.canonical_json_bytes(metadata),
                ),
                "stderr": self._write(f"{method}/stderr.txt", b""),
                "stdout": self._write(f"{method}/stdout.txt", b"fixture\n"),
                "summary": self._write(
                    f"{method}/summary.json",
                    ANALYZER.canonical_json_bytes(summary),
                ),
                "trace": self._write(
                    f"{method}/trace.jsonl",
                    self._trace_bytes(metadata),
                ),
            }
            extensions = {
                "metadata": "metadata.json",
                "stderr": "stderr.txt",
                "stdout": "stdout.txt",
                "summary": "summary.json",
                "trace": "trace.jsonl",
            }
            method_artifacts[method] = {
                key: _artifact(f"{method}/{extensions[key]}", data)
                for key, data in content.items()
            }

        viewer_builder = _authenticated_viewer_builder()
        render_result = viewer_builder(
            self.scene_root / "reference/trace.jsonl",
            self.scene_root / "optimized/trace.jsonl",
            REPO_ROOT / self.scene["map"]["path"],
            title="DANCeRS diagnostic replay · open · N=8 · seed=1",
            environment_family="open",
            environment_descriptors=self.plan["environment_descriptors"][
                "open"
            ],
        )
        replay = self._write("replay.html", render_result.html_bytes)

        reference = summaries["reference"]
        diagnostics = {
            "aligned_frame_count": 200,
            "aligned_robot_frame_count": 1600,
            "all_goals_reached": reference["all_goals_reached"],
            "discordant_robot_frame_count": 0,
            "first_all_goals_tick": reference["first_all_goals_tick"],
            "goals_reached": reference["goals_reached"],
            "goals_total": reference["goals_total"],
            "max_abs_position_delta_m": 0.0,
            "max_abs_wrapped_heading_delta_rad": 0.0,
            "mean_path_length_m_per_robot": reference[
                "mean_path_length_m_per_robot"
            ],
            "minimum_obstacle_clearance_m": reference[
                "minimum_obstacle_clearance_m"
            ],
            "minimum_pair_clearance_m": reference[
                "minimum_pair_clearance_m"
            ],
            "obstacle_collision_events": reference[
                "obstacle_collision_events"
            ],
            "obstacle_collision_exposure_seconds": reference[
                "obstacle_collision_exposure_seconds"
            ],
            "pair_collision_events": reference["pair_collision_events"],
            "pair_collision_exposure_seconds": reference[
                "pair_collision_exposure_seconds"
            ],
            "paper_metrics_equal": True,
            "run_valid": reference["run_valid"],
            "state_hex_equal": True,
            "summary_parity_fields_equal": True,
            "total_path_length_m": reference["total_path_length_m"],
        }
        manifest = {
            "compute_claim": False,
            "config_cli_argument": self.scene["config_cli_argument"],
            "config_cli_path_policy": "repository-relative-no-whitespace",
            "contract_id": "dancers-simulator-demo-v1",
            "contract_sha256": self.plan["contract"]["sha256"],
            "diagnostic_only": True,
            "execution_epoch_id": ANALYZER.EXECUTION_EPOCH_ID,
            "execution_lease": {
                "held_during_execution": True,
                "manual_recovery_if_present": True,
                "path": (
                    "results/simulator-demo/v1/visual-diagnostic/"
                    ".calibration-v2-execution.lease"
                ),
                "policy": (
                    "exclusive-create-no-follow-inode-bound-crash-persists-"
                    "manual-recovery"
                ),
                "schema_version": (
                    "dancers-simulator-visual-execution-lease-v1"
                ),
            },
            "failure_policy": "seal-stop-new-epoch-no-resume",
            "methods": {
                method: {
                    "artifacts": method_artifacts[method],
                    "binary": self.plan["methods"][method]["binary"],
                    "config_cli_argument": self.scene[
                        "config_cli_argument"
                    ],
                    "fresh_process": True,
                    "run_id": self.scene["run_ids"][method],
                    "source_revision": self.plan["methods"][method][
                        "source_revision"
                    ],
                    "terminal_category": "completed",
                }
                for method in ANALYZER.METHODS
            },
            "output_schemas": self.plan["output_schemas"],
            "pair_validation": {
                "diagnostics": diagnostics,
                "frames_compared": 200,
                "paper_metrics_equal": True,
                "semantic_trace_equal": True,
                "summary_parity_fields_equal": True,
            },
            "paper_replication_evidence": False,
            "phase": "calibration",
            "plan_id": ANALYZER.PLAN_ID,
            "plan_sha256": ANALYZER.PLAN_SHA256,
            "process_policy": {
                "config_cli_path_policy": "repository-relative-no-whitespace",
                "failure_policy": "seal-stop-new-epoch-no-resume",
                "fresh_process_per_method": True,
                "method_order": list(ANALYZER.METHODS),
                "serial_execution": True,
                "shared_solver_state": False,
                "viewer_started_after_both_processes_exited": True,
            },
            "recovery": self.plan["recovery"],
            "rendering_excluded_from_compute": True,
            "replay": {
                **_artifact("replay.html", replay),
                "trace_compare_sha256": self.plan["artifacts"][
                    "trace_compare"
                ]["sha256"],
                "viewer_sha256": self.plan["artifacts"]["viewer"][
                    "sha256"
                ],
            },
            "run_class": "positions-replay",
            "run_id_prefix": ANALYZER.EXECUTION_EPOCH_ID,
            "run_ids": self.scene["run_ids"],
            "scene": {
                "config": _artifact("config.yaml", config),
                "config_cli_argument": self.scene[
                    "config_cli_argument"
                ],
                "environment_descriptors": self.plan[
                    "environment_descriptors"
                ]["open"],
                "family": "open",
                "map": self.scene["map"],
                "robot_count": 8,
                "run_ids": self.scene["run_ids"],
                "scenario": self.scene["scenario"],
                "scene_id": self.scene["scene_id"],
                "seed": 1,
                "simulated_duration_seconds": 10,
                "timestep_seconds": 0.1,
                "timesteps": 100,
            },
            "schema_version": ANALYZER.RESULT_SCHEMA,
            "status": "complete",
            "timing_unlock": False,
            "track_id": ANALYZER.TRACK_ID,
        }
        self._write(
            "manifest.json",
            ANALYZER.canonical_json_bytes(manifest),
        )

    def _seal(self) -> None:
        for path in self.scene_root.rglob("*"):
            if path.is_file():
                path.chmod(0o444)
        (self.scene_root / "reference").chmod(0o555)
        (self.scene_root / "optimized").chmod(0o555)
        self.scene_root.chmod(0o555)

    def unseal(self) -> None:
        try:
            self.scene_root.chmod(0o755)
        except FileNotFoundError:
            return
        for method in ANALYZER.METHODS:
            path = self.scene_root / method
            if path.exists():
                path.chmod(0o755)
        for path in self.scene_root.rglob("*"):
            if path.is_file():
                path.chmod(0o644)

    def mutate_summary(self) -> None:
        path = self.scene_root / "reference/summary.json"
        path.chmod(0o644)
        path.write_bytes(path.read_bytes() + b" ")
        path.chmod(0o444)

    def _rewrite(self, relative: str, data: bytes) -> None:
        path = self.scene_root / relative
        path.chmod(0o644)
        path.write_bytes(data)
        path.chmod(0o444)

    def _update_method_artifact(
        self,
        manifest: dict[str, object],
        method: str,
        artifact_key: str,
        filename: str,
        data: bytes,
    ) -> None:
        manifest["methods"][method]["artifacts"][artifact_key] = _artifact(
            f"{method}/{filename}",
            data,
        )

    def coherently_rewrite_config(self) -> None:
        config_path = self.scene_root / "config.yaml"
        config_text = config_path.read_text(encoding="utf-8")
        if config_text.count("RNG_SEED: 1") != 1:
            raise AssertionError("synthetic config seed is not unique")
        config_data = config_text.replace(
            "RNG_SEED: 1",
            "RNG_SEED: 2",
        ).encode("utf-8")
        self._rewrite("config.yaml", config_data)
        config_sha = _sha256(config_data)
        manifest = json.loads(
            (self.scene_root / "manifest.json").read_text(encoding="utf-8")
        )
        manifest["scene"]["config"] = _artifact("config.yaml", config_data)
        for method in ANALYZER.METHODS:
            metadata_path = self.scene_root / method / "metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["config_sha256"] = config_sha
            metadata_data = ANALYZER.canonical_json_bytes(metadata)
            self._rewrite(f"{method}/metadata.json", metadata_data)
            self._update_method_artifact(
                manifest,
                method,
                "metadata",
                "metadata.json",
                metadata_data,
            )

            summary_path = self.scene_root / method / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["metadata"] = metadata
            summary_data = ANALYZER.canonical_json_bytes(summary)
            self._rewrite(f"{method}/summary.json", summary_data)
            self._update_method_artifact(
                manifest,
                method,
                "summary",
                "summary.json",
                summary_data,
            )

            trace_path = self.scene_root / method / "trace.jsonl"
            trace_lines = trace_path.read_bytes().splitlines(keepends=True)
            header = json.loads(trace_lines[0])
            header["metadata"] = metadata
            trace_data = b"".join(
                [ANALYZER.canonical_json_bytes(header), *trace_lines[1:]]
            )
            self._rewrite(f"{method}/trace.jsonl", trace_data)
            self._update_method_artifact(
                manifest,
                method,
                "trace",
                "trace.jsonl",
                trace_data,
            )
        self._rewrite(
            "manifest.json",
            ANALYZER.canonical_json_bytes(manifest),
        )

    def rewrite_trace_resolved_seed(self, method: str) -> None:
        manifest = json.loads(
            (self.scene_root / "manifest.json").read_text(encoding="utf-8")
        )
        summary_path = self.scene_root / method / "summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["resolved_config"]["rng_seed"] = 2
        summary_data = ANALYZER.canonical_json_bytes(summary)
        self._rewrite(f"{method}/summary.json", summary_data)
        self._update_method_artifact(
            manifest,
            method,
            "summary",
            "summary.json",
            summary_data,
        )
        trace_path = self.scene_root / method / "trace.jsonl"
        trace_lines = trace_path.read_bytes().splitlines(keepends=True)
        header = json.loads(trace_lines[0])
        header["resolved_config"]["rng_seed"] = 2
        trace_data = b"".join(
            [ANALYZER.canonical_json_bytes(header), *trace_lines[1:]]
        )
        self._rewrite(f"{method}/trace.jsonl", trace_data)
        self._update_method_artifact(
            manifest,
            method,
            "trace",
            "trace.jsonl",
            trace_data,
        )
        self._rewrite(
            "manifest.json",
            ANALYZER.canonical_json_bytes(manifest),
        )

    def coherently_rewrite_impossible_summaries(self) -> None:
        manifest = json.loads(
            (self.scene_root / "manifest.json").read_text(encoding="utf-8")
        )
        for method in ANALYZER.METHODS:
            summary_path = self.scene_root / method / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary.update(
                {
                    "all_goals_reached": True,
                    "first_all_goals_tick": 0,
                    "goals_reached": 9,
                    "goals_total": 8,
                }
            )
            summary_data = ANALYZER.canonical_json_bytes(summary)
            self._rewrite(f"{method}/summary.json", summary_data)
            self._update_method_artifact(
                manifest,
                method,
                "summary",
                "summary.json",
                summary_data,
            )
        manifest["pair_validation"]["diagnostics"].update(
            {
                "all_goals_reached": True,
                "first_all_goals_tick": 0,
                "goals_reached": 9,
                "goals_total": 8,
            }
        )
        self._rewrite(
            "manifest.json",
            ANALYZER.canonical_json_bytes(manifest),
        )

    def coherently_rewrite_collisions_as_safe(self) -> None:
        manifest = json.loads(
            (self.scene_root / "manifest.json").read_text(encoding="utf-8")
        )
        for method in ANALYZER.METHODS:
            summary_path = self.scene_root / method / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary.update(
                {
                    "minimum_pair_clearance_m": 1.0,
                    "pair_collision_events": 0,
                    "pair_collision_exposure_seconds": 0.0,
                }
            )
            summary_data = ANALYZER.canonical_json_bytes(summary)
            self._rewrite(f"{method}/summary.json", summary_data)
            self._update_method_artifact(
                manifest,
                method,
                "summary",
                "summary.json",
                summary_data,
            )
        manifest["pair_validation"]["diagnostics"].update(
            {
                "minimum_pair_clearance_m": 1.0,
                "pair_collision_events": 0,
                "pair_collision_exposure_seconds": 0.0,
            }
        )
        self._rewrite(
            "manifest.json",
            ANALYZER.canonical_json_bytes(manifest),
        )

    def coherently_rewrite_path_lengths(self) -> None:
        manifest = json.loads(
            (self.scene_root / "manifest.json").read_text(encoding="utf-8")
        )
        for method in ANALYZER.METHODS:
            summary_path = self.scene_root / method / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["per_robot_path_length_m"] = {
                str(robot): 999.0 for robot in range(8)
            }
            summary["total_path_length_m"] = 7992.0
            summary["mean_path_length_m_per_robot"] = 999.0
            summary_data = ANALYZER.canonical_json_bytes(summary)
            self._rewrite(f"{method}/summary.json", summary_data)
            self._update_method_artifact(
                manifest,
                method,
                "summary",
                "summary.json",
                summary_data,
            )
        manifest["pair_validation"]["diagnostics"].update(
            {
                "mean_path_length_m_per_robot": 999.0,
                "total_path_length_m": 7992.0,
            }
        )
        self._rewrite(
            "manifest.json",
            ANALYZER.canonical_json_bytes(manifest),
        )

    def coherently_rewrite_collision_event_count(self) -> None:
        if not self.collision:
            raise AssertionError("collision fixture required")
        manifest = json.loads(
            (self.scene_root / "manifest.json").read_text(encoding="utf-8")
        )
        for method in ANALYZER.METHODS:
            summary_path = self.scene_root / method / "summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["pair_collision_events"] = 999
            summary_data = ANALYZER.canonical_json_bytes(summary)
            self._rewrite(f"{method}/summary.json", summary_data)
            self._update_method_artifact(
                manifest,
                method,
                "summary",
                "summary.json",
                summary_data,
            )
        manifest["pair_validation"]["diagnostics"][
            "pair_collision_events"
        ] = 999
        self._rewrite(
            "manifest.json",
            ANALYZER.canonical_json_bytes(manifest),
        )

    def mutate_preflight_value(
        self,
        keys: tuple[str, ...],
        value: object,
    ) -> None:
        preflight_path = self.scene_root / "preflight.json"
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
        target = preflight
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value
        self._rewrite(
            "preflight.json",
            ANALYZER.canonical_json_bytes(preflight),
        )

    def add_deep_manifest_value(self) -> None:
        manifest = json.loads(
            (self.scene_root / "manifest.json").read_text(encoding="utf-8")
        )
        nested: object = "leaf"
        for _ in range(140):
            nested = {"nested": nested}
        manifest["deep"] = nested
        self._rewrite(
            "manifest.json",
            ANALYZER.canonical_json_bytes(manifest),
        )

    def coherently_rewrite_summary_with_huge_integer(self) -> None:
        manifest = json.loads(
            (self.scene_root / "manifest.json").read_text(encoding="utf-8")
        )
        summary_path = self.scene_root / "reference/summary.json"
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        summary["total_path_length_m"] = 10**4000
        summary_data = ANALYZER.canonical_json_bytes(summary)
        self._rewrite("reference/summary.json", summary_data)
        self._update_method_artifact(
            manifest,
            "reference",
            "summary",
            "summary.json",
            summary_data,
        )
        self._rewrite(
            "manifest.json",
            ANALYZER.canonical_json_bytes(manifest),
        )

    def coherently_rewrite_both_traces_malformed(self) -> None:
        manifest = json.loads(
            (self.scene_root / "manifest.json").read_text(encoding="utf-8")
        )
        for method in ANALYZER.METHODS:
            trace_path = self.scene_root / method / "trace.jsonl"
            records = [
                json.loads(line)
                for line in trace_path.read_text(encoding="utf-8").splitlines()
            ]
            malformed = [records[0]]
            for frame in records[1:]:
                malformed.append(
                    {
                        "phase": frame["phase"],
                        "robots": [{"rid": 99}],
                        "tick": frame["tick"],
                        "type": "frame",
                    }
                )
            trace_data = b"".join(
                ANALYZER.canonical_json_bytes(record) for record in malformed
            )
            self._rewrite(f"{method}/trace.jsonl", trace_data)
            self._update_method_artifact(
                manifest,
                method,
                "trace",
                "trace.jsonl",
                trace_data,
            )
        self._rewrite(
            "manifest.json",
            ANALYZER.canonical_json_bytes(manifest),
        )

    def coherently_rewrite_both_state_hex_values(self) -> None:
        manifest = json.loads(
            (self.scene_root / "manifest.json").read_text(encoding="utf-8")
        )
        for method in ANALYZER.METHODS:
            trace_path = self.scene_root / method / "trace.jsonl"
            records = [
                json.loads(line)
                for line in trace_path.read_text(encoding="utf-8").splitlines()
            ]
            records[1]["robots"][0]["state_hex"][0] = "0000000000000001"
            trace_data = b"".join(
                ANALYZER.canonical_json_bytes(record) for record in records
            )
            self._rewrite(f"{method}/trace.jsonl", trace_data)
            self._update_method_artifact(
                manifest,
                method,
                "trace",
                "trace.jsonl",
                trace_data,
            )
        self._rewrite(
            "manifest.json",
            ANALYZER.canonical_json_bytes(manifest),
        )

    def coherently_mutate_pair_geometry(self, mutation: str) -> None:
        manifest = json.loads(
            (self.scene_root / "manifest.json").read_text(encoding="utf-8")
        )
        for method in ANALYZER.METHODS:
            trace_path = self.scene_root / method / "trace.jsonl"
            records = [
                json.loads(line)
                for line in trace_path.read_text(encoding="utf-8").splitlines()
            ]

            def set_xy(record_index: int, robot_id: int, x: float, y: float) -> None:
                state = records[record_index]["robots"][robot_id]["state"]
                state[0] = x
                state[1] = y
                records[record_index]["robots"][robot_id]["state_hex"] = [
                    struct.pack(">d", float(value)).hex()
                    for value in state
                ]

            if mutation == "crossing":
                set_xy(1, 0, -2.0, 0.0)
                set_xy(1, 1, 2.0, 0.0)
                set_xy(2, 0, 2.0, 0.0)
                set_xy(2, 1, -2.0, 0.0)
            elif mutation == "contact":
                set_xy(1, 0, 0.0, 0.0)
                set_xy(1, 1, 0.0, 2.0)
                set_xy(2, 0, 0.0, 0.0)
                set_xy(2, 1, 0.0, 2.0)
            elif mutation == "boundary-teleport":
                set_xy(3, 0, 99.0, 0.0)
            elif mutation == "null-minimum":
                records[2]["metrics"]["frame_min_pair_clearance_m"] = None
            elif mutation == "huge-state":
                records[1]["robots"][0]["state"][0] = 10**4000
            else:
                raise AssertionError(f"unknown pair mutation {mutation}")
            trace_data = b"".join(
                ANALYZER.canonical_json_bytes(record) for record in records
            )
            self._rewrite(f"{method}/trace.jsonl", trace_data)
            self._update_method_artifact(
                manifest,
                method,
                "trace",
                "trace.jsonl",
                trace_data,
            )
        self._rewrite(
            "manifest.json",
            ANALYZER.canonical_json_bytes(manifest),
        )

    def coherently_rewrite_midrun_exposure_then_reset(self) -> None:
        manifest = json.loads(
            (self.scene_root / "manifest.json").read_text(encoding="utf-8")
        )
        for method in ANALYZER.METHODS:
            trace_path = self.scene_root / method / "trace.jsonl"
            records = [
                json.loads(line)
                for line in trace_path.read_text(encoding="utf-8").splitlines()
            ]
            records[2]["metrics"][
                "cumulative_obstacle_collision_exposure_seconds"
            ] = 0.25
            records[2]["metrics"]["obstacle_collisions"] = 1
            records[2]["metrics"]["frame_min_obstacle_clearance_m"] = -0.25
            records[3]["metrics"][
                "cumulative_obstacle_collision_exposure_seconds"
            ] = 0.25
            trace_data = b"".join(
                ANALYZER.canonical_json_bytes(record) for record in records
            )
            self._rewrite(f"{method}/trace.jsonl", trace_data)
            self._update_method_artifact(
                manifest,
                method,
                "trace",
                "trace.jsonl",
                trace_data,
            )
        self._rewrite(
            "manifest.json",
            ANALYZER.canonical_json_bytes(manifest),
        )

    def coherently_rewrite_replay_to_badges_only(self) -> None:
        replay_data = (
            "<html>DIAGNOSTIC REPLAY; NOT LIVE SOLVER TIMING; "
            "RENDERING EXCLUDED FROM COMPUTE</html>\n"
        ).encode("utf-8")
        self._rewrite("replay.html", replay_data)
        manifest = json.loads(
            (self.scene_root / "manifest.json").read_text(encoding="utf-8")
        )
        manifest["replay"].update(_artifact("replay.html", replay_data))
        self._rewrite(
            "manifest.json",
            ANALYZER.canonical_json_bytes(manifest),
        )

    def coherently_rewrite_manifest_type_and_resource_claims(self) -> None:
        manifest = json.loads(
            (self.scene_root / "manifest.json").read_text(encoding="utf-8")
        )
        manifest["diagnostic_only"] = 1
        manifest["compute_claim"] = 0
        manifest["methods"]["reference"]["fresh_process"] = 1
        manifest["wall_seconds"] = 123.0
        self._rewrite(
            "manifest.json",
            ANALYZER.canonical_json_bytes(manifest),
        )


class VisualCohortAnalyzerTests(unittest.TestCase):
    @contextlib.contextmanager
    def synthetic_scene(self, *, collision: bool = False):
        with tempfile.TemporaryDirectory() as temporary:
            fixture = SyntheticScene(Path(temporary), collision=collision)
            try:
                yield fixture
            finally:
                fixture.unseal()

    def test_execution_checkpoint_registry_is_exact_and_immutable(self) -> None:
        seed01 = {
            "fractal_high": "c05c291f5e8c8266923a4ea2872a7c5d8180c531",
            "fractal_low": "c05c291f5e8c8266923a4ea2872a7c5d8180c531",
            "fractal_medium": "c05c291f5e8c8266923a4ea2872a7c5d8180c531",
            "open": "c33833cf5dfc5b9cf38f68f2f54df2b73be40379",
            "perfect_maze": "c05c291f5e8c8266923a4ea2872a7c5d8180c531",
        }
        continuation = "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea"
        expected = {
            f"{family}-n8-seed{seed:02d}": (
                seed01[family] if seed == 1 else continuation
            )
            for family in (
                "open",
                "perfect_maze",
                "fractal_low",
                "fractal_medium",
                "fractal_high",
            )
            for seed in range(1, 11)
        }
        registry = ANALYZER.EXECUTION_CHECKPOINT_BY_SCENE
        self.assertIsInstance(registry, MappingProxyType)
        self.assertEqual(len(registry), 50)
        self.assertEqual(dict(registry), expected)
        self.assertEqual(
            sum(value == continuation for value in registry.values()),
            45,
        )
        with self.assertRaises(TypeError):
            registry["open-n8-seed11"] = continuation

    def test_execution_checkpoint_registry_tampering_fails_closed(self) -> None:
        original = dict(ANALYZER.EXECUTION_CHECKPOINT_BY_SCENE)
        broadened = {**original, "open-n8-seed11": "69a9902bf7398e0d5308ef6b9aa43bc37eb15aea"}
        mutated = {
            **original,
            "open-n8-seed02": "0" * 40,
        }
        narrowed = dict(original)
        narrowed.pop("fractal_high-n8-seed10")
        for label, candidate in (
            ("broadened", broadened),
            ("mutated", mutated),
            ("narrowed", narrowed),
        ):
            with (
                self.subTest(label=label),
                mock.patch.object(
                    ANALYZER,
                    "EXECUTION_CHECKPOINT_BY_SCENE",
                    candidate,
                ),
                self.assertRaisesRegex(
                    ANALYZER.AnalysisError,
                    "execution-checkpoint-registry:scene-map-mismatch",
                ),
            ):
                ANALYZER._validate_execution_checkpoint_registry()

    def test_live_checkpoint_is_complete_with_ten_pair_invalid(self) -> None:
        report = ANALYZER.analyze_visual_cohort(REPO_ROOT)
        summary = report["summary"]
        self.assertEqual(summary["expected_scene_count"], 50)
        self.assertEqual(summary["present_scene_count"], 50)
        self.assertEqual(summary["authenticated_scene_count"], 50)
        self.assertEqual(summary["missing_scene_count"], 0)
        self.assertEqual(summary["authentication_failed_scene_count"], 0)
        self.assertTrue(summary["cohort_complete"])
        self.assertEqual(summary["parity_exact_scene_count"], 50)
        self.assertEqual(summary["parity_discordant_scene_count"], 0)
        self.assertEqual(summary["task_valid_scene_count"], 0)
        self.assertEqual(summary["task_invalid_scene_count"], 10)
        self.assertEqual(
            summary["task_not_independently_verified_scene_count"],
            40,
        )
        self.assertEqual(summary["task_unevaluated_scene_count"], 0)
        self.assertEqual(summary["status"], "task-invalid")
        self.assertFalse(summary["diagnostic_gate_pass"])
        invalid = [
            scene
            for scene in report["scenes"]
            if scene["task_validity"]["state"] == "invalid"
        ]
        self.assertEqual(
            [scene["scene_id"] for scene in invalid],
            [
                f"fractal_low-n8-seed{seed:02d}"
                for seed in range(1, 11)
            ],
        )
        self.assertFalse(report["governance"]["timing_unlock"])
        self.assertFalse(report["governance"]["scientific_admission"])
        clear = [
            scene
            for scene in report["scenes"]
            if scene["authentication"]["state"] == "authenticated"
            and scene["task_validity"]["state"]
            == "not_independently_verified"
        ]
        self.assertEqual(len(clear), 40)
        self.assertTrue(
            all(
                set(scene["task_validity"]["methods"].values())
                == {"not_independently_verified"}
                for scene in clear
            )
        )
        expected_pair_anchors = {
            "open": (2.2077845306029165, 0),
            "perfect_maze": (6.8080993185921865, 0),
            "fractal_low": (-0.3024641378817645, 7),
            "fractal_medium": (7.576289791556853, 0),
            "fractal_high": (0.5975760999286468, 0),
        }
        for scene in report["scenes"]:
            minimum, nonpositive_frames = expected_pair_anchors[
                scene["family"]
            ]
            self.assertAlmostEqual(
                scene["diagnostics"]["minimum_pair_clearance_m"],
                minimum,
                places=12,
            )
            self.assertEqual(
                scene["diagnostics"][
                    "pair_nonpositive_sweep_frame_count"
                ],
                nonpositive_frames,
            )

    def test_live_checkpoint_exposes_effective_replication_and_censoring(self) -> None:
        report = ANALYZER.analyze_visual_cohort(REPO_ROOT)
        replication = report["replication"]
        self.assertEqual(replication["nominal_scene_count"], 50)
        self.assertEqual(replication["unique_effective_mission_count"], 5)
        self.assertEqual(
            replication["unique_normalized_trajectory_count"], 5
        )
        self.assertTrue(replication["pseudoreplication_present"])
        self.assertEqual(
            replication["seed_axis_state"],
            "inactive-for-observed-file-scenario-zero-comms-cohort",
        )
        self.assertEqual(
            replication["goal_horizon_state"],
            "censored-for-all-effective-missions",
        )
        for key in (
            "collision_rate_estimation_permitted",
            "family_causal_effect_claim_permitted",
            "goal_completion_inference_permitted",
            "inferential_independence_claim",
        ):
            self.assertIs(replication[key], False)

        expected_nearest = {
            "open": 47.65624999999995,
            "perfect_maze": 46.12022544732944,
            "fractal_low": 41.716482143415114,
            "fractal_medium": 50.319412902415195,
            "fractal_high": 43.87016185486295,
        }
        for family in replication["families"]:
            self.assertEqual(family["nominal_scene_count"], 10)
            self.assertEqual(family["authenticated_scene_count"], 10)
            self.assertEqual(family["unique_effective_input_count"], 1)
            self.assertEqual(
                family["unique_normalized_trajectory_count"], 1
            )
            self.assertEqual(
                family["seed_axis_state"],
                "inactive-for-observed-normalized-trajectory",
            )
            self.assertEqual(len(family["duplicate_seed_groups"]), 1)
            self.assertEqual(
                family["duplicate_seed_groups"][0]["seeds"],
                list(range(1, 11)),
            )
            horizon = family["goal_horizon"]
            self.assertEqual(
                horizon["interpretation"],
                "structurally-censored-by-fixed-speed-and-horizon",
            )
            self.assertEqual(
                horizon["maximum_kinematic_displacement_m"], 20.0
            )
            self.assertEqual(horizon["goal_reach_radius_m"], 1.0)
            self.assertAlmostEqual(
                horizon["minimum_start_to_final_goal_distance_m"],
                expected_nearest[family["family"]],
                places=12,
            )
            self.assertAlmostEqual(
                horizon["minimum_required_centre_travel_to_goal_m"],
                expected_nearest[family["family"]] - 1.0,
                places=12,
            )

        for family_name in ANALYZER.FAMILIES:
            family_scenes = [
                scene
                for scene in report["scenes"]
                if scene["family"] == family_name
            ]
            self.assertEqual(
                len(
                    {
                        scene["replication_evidence"][
                            "effective_input_sha256"
                        ]
                        for scene in family_scenes
                    }
                ),
                1,
            )
            self.assertEqual(
                len(
                    {
                        scene["replication_evidence"][
                            "normalized_trajectory_sha256"
                        ]
                        for scene in family_scenes
                    }
                ),
                1,
            )

    def test_trajectory_signature_excludes_rng_but_binds_state_and_horizon(self) -> None:
        frame = {
            "phase": "pre_update",
            "rng_state": {"integer_mt19937": "seed-one"},
            "robots": [
                {
                    "current_waypoint": [20.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    "final_goal": [30.0, 0.0],
                    "goal_reached": False,
                    "radius_m": 1.0,
                    "rid": 0,
                    "state": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    "state_hex": ["0000000000000000"] * 6,
                }
            ],
            "simulated_time_seconds": 0.0,
            "tick": 0,
        }
        changed_rng = json.loads(json.dumps(frame))
        changed_rng["rng_state"]["integer_mt19937"] = "seed-two"
        self.assertEqual(
            ANALYZER._normalized_trajectory_sha256([frame]),
            ANALYZER._normalized_trajectory_sha256([changed_rng]),
        )
        changed_state = json.loads(json.dumps(frame))
        changed_state["robots"][0]["state_hex"][0] = "3ff0000000000000"
        self.assertNotEqual(
            ANALYZER._normalized_trajectory_sha256([frame]),
            ANALYZER._normalized_trajectory_sha256([changed_state]),
        )
        horizon = ANALYZER._goal_horizon_evidence([frame])
        self.assertEqual(
            horizon["interpretation"],
            "structurally-censored-by-fixed-speed-and-horizon",
        )
        self.assertFalse(horizon["goal_completion_inference_permitted"])
        reachable = json.loads(json.dumps(frame))
        reachable["robots"][0]["final_goal"] = [20.5, 0.0]
        self.assertEqual(
            ANALYZER._goal_horizon_evidence([reachable])["interpretation"],
            "not-structurally-censored",
        )
        unreachable = json.loads(json.dumps(frame))
        unreachable["robots"][0]["final_goal"] = [21.5, 0.0]
        self.assertEqual(
            ANALYZER._goal_horizon_evidence([unreachable])["interpretation"],
            "structurally-censored-by-fixed-speed-and-horizon",
        )

    def test_missing_results_are_deterministically_enumerated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=Path(temporary) / "absent",
                trace_comparator=_exact_trace,
            )
        self.assertEqual(report["summary"]["present_scene_count"], 0)
        self.assertEqual(report["summary"]["missing_scene_count"], 50)
        self.assertEqual(
            report["replication"]["unique_effective_mission_count"], 5
        )
        self.assertEqual(
            report["replication"]["unique_normalized_trajectory_count"], 0
        )
        self.assertEqual(
            report["replication"]["seed_axis_state"],
            "not-fully-evaluated",
        )
        markdown = ANALYZER.render_markdown(report)
        self.assertIn(
            "does not authenticate enough trajectories",
            markdown,
        )
        self.assertIn("Goal-horizon censoring is not fully evaluated", markdown)
        self.assertNotIn(
            "every start-to-final-goal straight-line distance exceeds",
            markdown.lower(),
        )
        self.assertNotIn("establish deterministic repeatability", markdown)
        self.assertEqual(
            [scene["scene_id"] for scene in report["scenes"][:3]],
            [
                "open-n8-seed01",
                "open-n8-seed02",
                "open-n8-seed03",
            ],
        )

    def test_manifest_bound_mutation_fails_authentication(self) -> None:
        with self.synthetic_scene() as fixture:
            fixture.mutate_summary()
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=fixture.results_root,
                trace_comparator=_exact_trace,
            )
        self.assertEqual(report["summary"]["present_scene_count"], 1)
        self.assertEqual(
            report["summary"]["authentication_failed_scene_count"], 1
        )
        present = next(
            scene
            for scene in report["scenes"]
            if scene["authentication"]["state"] != "missing"
        )
        self.assertEqual(
            present["authentication"]["reason"],
            "manifest:reference-summary:bytes-mismatch",
        )
        self.assertEqual(present["parity"]["state"], "not_evaluated")

    def test_task_invalid_is_separate_from_exact_parity(self) -> None:
        with self.synthetic_scene(collision=True) as fixture:
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=fixture.results_root,
                trace_comparator=_exact_trace,
            )
        present = next(
            scene
            for scene in report["scenes"]
            if scene["authentication"]["state"] == "authenticated"
        )
        self.assertEqual(present["parity"]["state"], "exact")
        self.assertEqual(present["task_validity"]["state"], "invalid")
        self.assertIn(
            "reference.pair_collision_events:expected-zero",
            present["task_validity"]["violations"],
        )
        self.assertFalse(report["summary"]["diagnostic_gate_pass"])

    def test_sealed_complete_discordance_is_authentication_failure(self) -> None:
        with self.synthetic_scene() as fixture:
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=fixture.results_root,
                trace_comparator=_discordant_trace,
            )
        present = next(
            scene
            for scene in report["scenes"]
            if scene["authentication"]["state"] != "missing"
        )
        self.assertEqual(
            present["authentication"]["reason"],
            "sealed-complete:parity-not-exact",
        )
        self.assertEqual(report["summary"]["parity_discordant_scene_count"], 0)

    def test_coherent_config_rewrite_fails_plan_derived_render(self) -> None:
        with self.synthetic_scene() as fixture:
            fixture.coherently_rewrite_config()
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=fixture.results_root,
                trace_comparator=_exact_trace,
            )
        present = next(
            scene
            for scene in report["scenes"]
            if scene["authentication"]["state"] != "missing"
        )
        self.assertEqual(
            present["authentication"]["reason"],
            "scene-config:plan-render-mismatch",
        )

    def test_both_trace_headers_bind_resolved_seed(self) -> None:
        for method in ANALYZER.METHODS:
            with self.subTest(method=method), self.synthetic_scene() as fixture:
                fixture.rewrite_trace_resolved_seed(method)
                report = ANALYZER.analyze_visual_cohort(
                    REPO_ROOT,
                    results_root=fixture.results_root,
                    trace_comparator=_exact_trace,
                )
            present = next(
                scene
                for scene in report["scenes"]
                if scene["authentication"]["state"] != "missing"
            )
            self.assertEqual(
                present["authentication"]["reason"],
                f"resolved-config:{method}:registered-values-mismatch",
            )

    def test_coherent_impossible_summaries_fail_semantic_authentication(self) -> None:
        with self.synthetic_scene() as fixture:
            fixture.coherently_rewrite_impossible_summaries()
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=fixture.results_root,
                trace_comparator=_exact_trace,
            )
        present = next(
            scene
            for scene in report["scenes"]
            if scene["authentication"]["state"] != "missing"
        )
        self.assertEqual(
            present["authentication"]["reason"],
            "summary:reference:goals-reached-exceeds-total",
        )

    def test_safe_summaries_cannot_hide_colliding_validated_frames(self) -> None:
        with self.synthetic_scene(collision=True) as fixture:
            fixture.coherently_rewrite_collisions_as_safe()
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=fixture.results_root,
                trace_comparator=_exact_trace,
            )
        present = next(
            scene
            for scene in report["scenes"]
            if scene["authentication"]["state"] != "missing"
        )
        self.assertEqual(
            present["authentication"]["reason"],
            "trace-task:reference:minimum_pair_clearance_m-summary-mismatch",
        )

    def test_path_metrics_are_recomputed_from_validated_positions(self) -> None:
        with self.synthetic_scene() as fixture:
            fixture.coherently_rewrite_path_lengths()
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=fixture.results_root,
                trace_comparator=_exact_trace,
            )
        present = next(
            scene
            for scene in report["scenes"]
            if scene["authentication"]["state"] != "missing"
        )
        self.assertEqual(
            present["authentication"]["reason"],
            (
                "trace-task:reference:per-robot-path-length-0-"
                "summary-mismatch"
            ),
        )

    def test_unreconstructable_event_cardinality_is_not_reported_as_exact(self) -> None:
        with self.synthetic_scene(collision=True) as fixture:
            fixture.coherently_rewrite_collision_event_count()
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=fixture.results_root,
                trace_comparator=_exact_trace,
            )
        present = next(
            scene
            for scene in report["scenes"]
            if scene["authentication"]["state"] == "authenticated"
        )
        diagnostics = present["diagnostics"]
        self.assertNotIn("pair_collision_events", diagnostics)
        self.assertNotIn("obstacle_collision_events", diagnostics)
        self.assertTrue(diagnostics["pair_collision_present"])
        self.assertEqual(diagnostics["pair_collision_frame_count"], 100)
        self.assertAlmostEqual(diagnostics["total_path_length_m"], 8.0)
        for length in diagnostics["per_robot_path_length_m"].values():
            self.assertAlmostEqual(length, 1.0, places=12)

    def test_equal_malformed_both_traces_fail_before_real_comparator(self) -> None:
        with self.synthetic_scene() as fixture:
            fixture.coherently_rewrite_both_traces_malformed()
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=fixture.results_root,
            )
        present = next(
            scene
            for scene in report["scenes"]
            if scene["authentication"]["state"] != "missing"
        )
        self.assertEqual(
            present["authentication"]["reason"],
            "trace-structure:reference:frame-0:fields-mismatch",
        )

    def test_valid_hex_shape_cannot_forge_state_binary64_identity(self) -> None:
        with self.synthetic_scene() as fixture:
            fixture.coherently_rewrite_both_state_hex_values()
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=fixture.results_root,
            )
        present = next(
            scene
            for scene in report["scenes"]
            if scene["authentication"]["state"] != "missing"
        )
        self.assertEqual(
            present["authentication"]["reason"],
            (
                "trace-structure:reference:frame-0:"
                "robot-0:state-hex-value-mismatch"
            ),
        )

    def test_pair_sweep_rejects_crossing_contact_and_boundary_teleport(self) -> None:
        cases = (
            (
                "crossing",
                "trace-structure:reference:frame-1:pair-collision-count-mismatch",
            ),
            (
                "contact",
                "trace-structure:reference:frame-1:pair-clearance-mismatch",
            ),
            (
                "boundary-teleport",
                "trace-structure:reference:frame-2:pre-update-robots-not-repeated",
            ),
        )
        for mutation, expected_reason in cases:
            with self.subTest(mutation=mutation), self.synthetic_scene() as fixture:
                fixture.coherently_mutate_pair_geometry(mutation)
                report = ANALYZER.analyze_visual_cohort(
                    REPO_ROOT,
                    results_root=fixture.results_root,
                    trace_comparator=_exact_trace,
                )
            present = next(
                scene
                for scene in report["scenes"]
                if scene["authentication"]["state"] != "missing"
            )
            self.assertEqual(
                present["authentication"]["reason"],
                expected_reason,
            )

    def test_post_update_null_clearance_is_fail_closed(self) -> None:
        with self.synthetic_scene() as fixture:
            fixture.coherently_mutate_pair_geometry("null-minimum")
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=fixture.results_root,
                trace_comparator=_exact_trace,
            )
        present = next(
            scene
            for scene in report["scenes"]
            if scene["authentication"]["state"] != "missing"
        )
        self.assertEqual(
            present["authentication"]["reason"],
            (
                "trace-structure:reference:frame-1:"
                "post-update-clearance-invalid"
            ),
        )

    def test_huge_integer_numeric_mutations_are_scene_failures(self) -> None:
        cases = (
            (
                "trace",
                lambda fixture: fixture.coherently_mutate_pair_geometry(
                    "huge-state"
                ),
                "trace-structure:reference:frame-0:robot-0:state-invalid",
            ),
            (
                "summary",
                lambda fixture: fixture.coherently_rewrite_summary_with_huge_integer(),
                "summary:reference:total_path_length_m-invalid",
            ),
        )
        for label, mutate, expected_reason in cases:
            with self.subTest(label=label), self.synthetic_scene() as fixture:
                mutate(fixture)
                report = ANALYZER.analyze_visual_cohort(
                    REPO_ROOT,
                    results_root=fixture.results_root,
                    trace_comparator=_exact_trace,
                )
            present = next(
                scene
                for scene in report["scenes"]
                if scene["authentication"]["state"] != "missing"
            )
            self.assertEqual(
                present["authentication"]["reason"],
                expected_reason,
            )

    def test_midrun_exposure_cannot_reset_before_final_summary(self) -> None:
        with self.synthetic_scene() as fixture:
            fixture.coherently_rewrite_midrun_exposure_then_reset()
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=fixture.results_root,
                trace_comparator=_exact_trace,
            )
        present = next(
            scene
            for scene in report["scenes"]
            if scene["authentication"]["state"] != "missing"
        )
        self.assertEqual(
            present["authentication"]["reason"],
            (
                "trace-structure:reference:frame-3:"
                "metrics-cumulative-not-monotonic"
            ),
        )

    def test_coherent_badge_only_replay_fails_verified_render(self) -> None:
        with self.synthetic_scene() as fixture:
            fixture.coherently_rewrite_replay_to_badges_only()
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=fixture.results_root,
                trace_comparator=_exact_trace,
            )
        present = next(
            scene
            for scene in report["scenes"]
            if scene["authentication"]["state"] != "missing"
        )
        self.assertEqual(
            present["authentication"]["reason"],
            "replay:verified-render-bytes-mismatch",
        )

    def test_viewer_result_contract_rejects_boolean_count_confusion(self) -> None:
        with self.synthetic_scene() as fixture:
            replay_bytes = (fixture.scene_root / "replay.html").read_bytes()

            class BadViewerResult:
                html_bytes = replay_bytes
                output_bytes = len(replay_bytes)
                frame_count = True
                robot_samples = 3200
                exact_semantic_match = True
                mismatch_tick = None

            with mock.patch.object(
                ANALYZER,
                "_load_replay_builder",
                return_value=lambda *args, **kwargs: BadViewerResult(),
            ):
                report = ANALYZER.analyze_visual_cohort(
                    REPO_ROOT,
                    results_root=fixture.results_root,
                    trace_comparator=_exact_trace,
                )
        present = next(
            scene
            for scene in report["scenes"]
            if scene["authentication"]["state"] != "missing"
        )
        self.assertEqual(
            present["authentication"]["reason"],
            "replay:verified-render-result-invalid",
        )

    def test_manifest_type_confusion_and_resource_claim_fail_closed(self) -> None:
        with self.synthetic_scene() as fixture:
            fixture.coherently_rewrite_manifest_type_and_resource_claims()
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=fixture.results_root,
                trace_comparator=_exact_trace,
            )
        present = next(
            scene
            for scene in report["scenes"]
            if scene["authentication"]["state"] != "missing"
        )
        self.assertEqual(
            present["authentication"]["reason"],
            "manifest:forbidden-resource-key:$.wall_seconds",
        )

    def test_deep_scene_json_is_an_authentication_failure(self) -> None:
        with self.synthetic_scene() as fixture:
            fixture.add_deep_manifest_value()
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=fixture.results_root,
                trace_comparator=_exact_trace,
            )
        present = next(
            scene
            for scene in report["scenes"]
            if scene["authentication"]["state"] != "missing"
        )
        self.assertEqual(
            present["authentication"]["reason"],
            "manifest:json-depth-out-of-bounds",
        )

    def test_preflight_payload_islands_are_independently_bound(self) -> None:
        cases = (
            (
                ("execution_checkpoint_commit",),
                "0" * 40,
                "preflight:execution-checkpoint-mismatch",
            ),
            (
                ("build", "manifest_sha256"),
                "0" * 64,
                "preflight:build-mismatch",
            ),
            (
                ("environment", "verified_unique_map_count"),
                4,
                "preflight:environment-mismatch",
            ),
            (
                ("epoch_state", "selected_destination_absent"),
                1,
                "preflight:epoch-state-values-mismatch",
            ),
            (
                ("recovery", "failure_evidence", "immutable"),
                1,
                "preflight:recovery-failure-evidence-mismatch",
            ),
            (
                ("recovery", "v1_plan", "bytes"),
                -1,
                "preflight:recovery-v1-plan-mismatch",
            ),
        )
        for keys, value, expected_reason in cases:
            with self.subTest(keys=keys), self.synthetic_scene() as fixture:
                fixture.mutate_preflight_value(keys, value)
                report = ANALYZER.analyze_visual_cohort(
                    REPO_ROOT,
                    results_root=fixture.results_root,
                    trace_comparator=_exact_trace,
                )
            present = next(
                scene
                for scene in report["scenes"]
                if scene["authentication"]["state"] != "missing"
            )
            self.assertEqual(
                present["authentication"]["reason"],
                expected_reason,
            )

    def test_report_bytes_are_deterministic_and_canonical(self) -> None:
        with self.synthetic_scene() as fixture:
            first = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=fixture.results_root,
                trace_comparator=_exact_trace,
            )
            second = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=fixture.results_root,
                trace_comparator=_exact_trace,
            )
        first_json = ANALYZER.canonical_json_bytes(first)
        self.assertEqual(first_json, ANALYZER.canonical_json_bytes(second))
        self.assertEqual(
            ANALYZER.render_markdown(first),
            ANALYZER.render_markdown(second),
        )
        self.assertEqual(
            json.loads(first_json),
            first,
        )
        self.assertTrue(first_json.endswith(b"\n"))

    def test_publication_is_create_exclusive_and_never_partial(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=root / "absent-results",
                trace_comparator=_exact_trace,
            )
            json_path = root / "report.json"
            markdown_path = root / "report.md"
            ANALYZER.write_reports_exclusive(json_path, markdown_path, report)
            self.assertEqual(
                json_path.read_bytes(),
                ANALYZER.canonical_json_bytes(report),
            )
            self.assertEqual(
                markdown_path.read_text(encoding="utf-8"),
                ANALYZER.render_markdown(report),
            )
            self.assertEqual(json_path.stat().st_mode & 0o777, 0o444)
            self.assertEqual(markdown_path.stat().st_mode & 0o777, 0o444)
            with self.assertRaisesRegex(
                ANALYZER.AnalysisError,
                "json-already-exists",
            ):
                ANALYZER.write_reports_exclusive(
                    json_path,
                    root / "second.md",
                    report,
                )
            self.assertFalse((root / "second.md").exists())

    def test_existing_second_destination_prevents_first_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=root / "absent-results",
                trace_comparator=_exact_trace,
            )
            json_path = root / "report.json"
            markdown_path = root / "report.md"
            markdown_path.write_text("sentinel", encoding="utf-8")
            with self.assertRaisesRegex(
                ANALYZER.AnalysisError,
                "markdown-already-exists",
            ):
                ANALYZER.write_reports_exclusive(
                    json_path,
                    markdown_path,
                    report,
                )
            self.assertFalse(json_path.exists())
            self.assertEqual(
                markdown_path.read_text(encoding="utf-8"),
                "sentinel",
            )

    def test_success_path_descriptor_close_failure_rolls_back(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=root / "absent-results",
                trace_comparator=_exact_trace,
            )
            json_path = root / "report.json"
            markdown_path = root / "report.md"
            real_close = ANALYZER.os.close
            failed_once = False

            def close_then_fail_once(descriptor: int) -> None:
                nonlocal failed_once
                if not failed_once:
                    failed_once = True
                    real_close(descriptor)
                    raise OSError(errno.EIO, "injected close failure")
                real_close(descriptor)

            with (
                mock.patch.object(
                    ANALYZER.os,
                    "close",
                    side_effect=close_then_fail_once,
                ),
                self.assertRaisesRegex(
                    ANALYZER.AnalysisError,
                    "report-publication:close-failed",
                ),
            ):
                ANALYZER.write_reports_exclusive(
                    json_path,
                    markdown_path,
                    report,
                )
            self.assertFalse(json_path.exists())
            self.assertFalse(markdown_path.exists())

    def test_exit_status_is_fail_closed_for_incomplete_or_failed_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=Path(temporary) / "absent",
                trace_comparator=_exact_trace,
            )
            self.assertEqual(ANALYZER.report_exit_code(report), 1)
            output_root = Path(temporary).resolve()
            json_path = output_root / "cli.json"
            markdown_path = output_root / "cli.md"

            class _Stdout:
                def __init__(self) -> None:
                    self.buffer = io.BytesIO()

            stdout = _Stdout()
            with mock.patch.object(ANALYZER.sys, "stdout", stdout):
                return_code = ANALYZER.main(
                    [
                        "--repo-root",
                        str(REPO_ROOT),
                        "--json-out",
                        str(json_path),
                        "--markdown-out",
                        str(markdown_path),
                    ]
                )
            self.assertEqual(return_code, 1)
            self.assertTrue(json_path.is_file())
            self.assertTrue(markdown_path.is_file())
        passed = {
            "summary": {
                "authentication_failed_scene_count": 0,
                "diagnostic_gate_pass": True,
            }
        }
        failed = {
            "summary": {
                "authentication_failed_scene_count": 1,
                "diagnostic_gate_pass": False,
            }
        }
        self.assertEqual(ANALYZER.report_exit_code(passed), 0)
        self.assertEqual(ANALYZER.report_exit_code(failed), 2)

    def test_stdout_publication_failure_returns_two(self) -> None:
        report = {
            "summary": {
                "authentication_failed_scene_count": 0,
                "diagnostic_gate_pass": False,
            }
        }

        class BrokenBuffer:
            def write(self, payload: bytes) -> int:
                raise BrokenPipeError(errno.EPIPE, "injected broken pipe")

            def flush(self) -> None:
                raise AssertionError("flush follows no successful write")

        class BrokenStdout:
            buffer = BrokenBuffer()

        stderr = io.StringIO()
        with (
            mock.patch.object(
                ANALYZER,
                "analyze_visual_cohort",
                return_value=report,
            ),
            mock.patch.object(ANALYZER, "write_reports_exclusive"),
            mock.patch.object(ANALYZER.sys, "stdout", BrokenStdout()),
            mock.patch.object(ANALYZER.sys, "stderr", stderr),
        ):
            return_code = ANALYZER.main(
                [
                    "--repo-root",
                    str(REPO_ROOT),
                    "--json-out",
                    "/private/tmp/unused-visual-cohort-report.json",
                    "--markdown-out",
                    "/private/tmp/unused-visual-cohort-report.md",
                ]
            )
        self.assertEqual(return_code, 2)
        self.assertIn("cli:stdout-publication-failed", stderr.getvalue())

    def test_cli_rejects_alternate_plan_and_results_before_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            alternate_plan = root / "plan-copy.json"
            alternate_plan.write_bytes(
                (REPO_ROOT / ANALYZER.PLAN_RELATIVE_PATH).read_bytes()
            )
            alternate_results = root / "alternate-results"
            alternate_results.mkdir()
            cases = (
                (
                    "plan",
                    ["--plan", str(alternate_plan)],
                    "cli:plan:noncanonical-selection",
                ),
                (
                    "results-root",
                    ["--results-root", str(alternate_results)],
                    "cli:results-root:noncanonical-selection",
                ),
            )
            analysis_bomb = mock.Mock(
                side_effect=AssertionError("analysis must not begin")
            )
            for index, (label, selection, expected_error) in enumerate(cases):
                with self.subTest(label=label):
                    json_path = root / f"rejected-{index}.json"
                    markdown_path = root / f"rejected-{index}.md"
                    stderr = io.StringIO()
                    with (
                        mock.patch.object(
                            ANALYZER,
                            "analyze_visual_cohort",
                            analysis_bomb,
                        ),
                        mock.patch.object(ANALYZER.sys, "stderr", stderr),
                    ):
                        return_code = ANALYZER.main(
                            [
                                "--repo-root",
                                str(REPO_ROOT),
                                *selection,
                                "--json-out",
                                str(json_path),
                                "--markdown-out",
                                str(markdown_path),
                            ]
                        )
                    self.assertEqual(return_code, 2)
                    self.assertIn(expected_error, stderr.getvalue())
                    self.assertFalse(json_path.exists())
                    self.assertFalse(markdown_path.exists())
            self.assertEqual(analysis_bomb.call_count, 0)

    def test_cli_selection_rejects_symlinked_canonical_results_component(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            visual_parent = repository / "results/simulator-demo/v1/visual-diagnostic"
            visual_parent.mkdir(parents=True)
            target = repository / "real-results"
            target.mkdir()
            (visual_parent / "calibration-v2").symlink_to(
                target,
                target_is_directory=True,
            )
            with self.assertRaisesRegex(
                ANALYZER.AnalysisError,
                "cli:results-root:symlink-component",
            ):
                ANALYZER._canonical_cli_selection(
                    repository,
                    None,
                    ANALYZER.RESULTS_RELATIVE_ROOT,
                    "results-root",
                )

    def test_cli_outputs_cannot_contaminate_visual_epoch_or_symlink_parent(self) -> None:
        analysis_bomb = mock.Mock(
            side_effect=AssertionError("analysis must not begin")
        )
        protected = REPO_ROOT / ANALYZER.PROTECTED_VISUAL_RESULTS_RELATIVE_ROOT
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            real_parent = root / "real-parent"
            real_parent.mkdir()
            symlink_parent = root / "symlink-parent"
            symlink_parent.symlink_to(real_parent, target_is_directory=True)
            cases = (
                (
                    "protected",
                    protected / "analyzer-must-not-create.json",
                    root / "protected.md",
                    "protected-visual-results-path",
                ),
                (
                    "case-alias-protected",
                    protected.parent
                    / protected.name.upper()
                    / "analyzer-must-not-create-case-alias.json",
                    root / "protected-case-alias.md",
                    "protected-visual-results-path",
                ),
                (
                    "symlink-parent",
                    symlink_parent / "report.json",
                    root / "symlink.md",
                    "parent-symlink-component",
                ),
            )
            for label, json_path, markdown_path, expected in cases:
                with self.subTest(label=label):
                    stderr = io.StringIO()
                    with (
                        mock.patch.object(
                            ANALYZER,
                            "analyze_visual_cohort",
                            analysis_bomb,
                        ),
                        mock.patch.object(ANALYZER.sys, "stderr", stderr),
                    ):
                        return_code = ANALYZER.main(
                            [
                                "--repo-root",
                                str(REPO_ROOT),
                                "--json-out",
                                str(json_path),
                                "--markdown-out",
                                str(markdown_path),
                            ]
                        )
                    self.assertEqual(return_code, 2)
                    self.assertIn(expected, stderr.getvalue())
                    self.assertFalse(json_path.exists())
                    self.assertFalse(markdown_path.exists())
            self.assertEqual(analysis_bomb.call_count, 0)

    def test_epoch_lease_is_checked_at_start_and_end(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary)
            lease = repository / ANALYZER.EPOCH_LEASE_RELATIVE_PATH
            lease.parent.mkdir(parents=True)
            lease.write_text("held", encoding="utf-8")
            with self.assertRaisesRegex(
                ANALYZER.AnalysisError,
                "epoch-lease:start:present",
            ):
                ANALYZER._require_epoch_lease_absent(repository, "start")

            phases: list[str] = []

            def fail_at_end(_repository: Path, phase: str) -> None:
                phases.append(phase)
                if phase == "end":
                    raise ANALYZER.AnalysisError("epoch-lease:end:present")

            with (
                mock.patch.object(
                    ANALYZER,
                    "_require_epoch_lease_absent",
                    side_effect=fail_at_end,
                ),
                self.assertRaisesRegex(
                    ANALYZER.AnalysisError,
                    "epoch-lease:end:present",
                ),
            ):
                ANALYZER.analyze_visual_cohort(
                    REPO_ROOT,
                    results_root=repository / "absent-results",
                    trace_comparator=_exact_trace,
                )
            self.assertEqual(phases, ["start", "end"])

    def test_epoch_inventory_rejects_foreign_and_partial_entries(self) -> None:
        cases = ("foreign", "partial")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                results_root = Path(temporary) / "results"
                results_root.mkdir()
                if case == "foreign":
                    (results_root / "foreign.txt").write_text(
                        "foreign",
                        encoding="utf-8",
                    )
                    expected = "epoch-inventory:unknown-file:foreign.txt"
                else:
                    (results_root / "open/n-8").mkdir(parents=True)
                    expected = "epoch-inventory:partial-empty-prefix"
                with self.assertRaisesRegex(
                    ANALYZER.AnalysisError,
                    expected,
                ):
                    ANALYZER.analyze_visual_cohort(
                        REPO_ROOT,
                        results_root=results_root,
                        trace_comparator=_exact_trace,
                    )

    def test_verified_comparator_ignores_timestamp_valid_poisoned_pyc(self) -> None:
        verified_source = b"""MARKER = "verified"
class Result:
    equal = bool(1)
    frames_compared = 7
    left_provenance = {\"origin\": \"verified\"}
    right_provenance = {\"origin\": \"verified\"}

def compare_traces(left, right):
    return Result()
"""
        poisoned_source = b"""MARKER = "poisoned"
class Result:
    equal = bool(0)
    frames_compared = 7
    left_provenance = {\"origin\": \"poisoned\"}
    right_provenance = {\"origin\": \"poisoned\"}

def compare_traces(left, right):
    return Result()
"""
        self.assertEqual(len(verified_source), len(poisoned_source))
        with tempfile.TemporaryDirectory() as temporary:
            source_path = Path(temporary) / "trace_compare.py"
            source_path.write_bytes(poisoned_source)
            fixed_mtime_ns = 1_700_000_000_000_000_000
            os.utime(
                source_path,
                ns=(fixed_mtime_ns, fixed_mtime_ns),
            )
            cache_path = Path(
                py_compile.compile(
                    str(source_path),
                    doraise=True,
                    invalidation_mode=py_compile.PycInvalidationMode.TIMESTAMP,
                )
            )
            poisoned_cache = cache_path.read_bytes()
            self.assertEqual(
                int.from_bytes(poisoned_cache[4:8], "little"),
                0,
            )
            self.assertEqual(
                int.from_bytes(poisoned_cache[8:12], "little"),
                fixed_mtime_ns // 1_000_000_000,
            )
            self.assertEqual(
                int.from_bytes(poisoned_cache[12:16], "little"),
                len(poisoned_source),
            )

            source_path.write_bytes(verified_source)
            os.utime(
                source_path,
                ns=(fixed_mtime_ns, fixed_mtime_ns),
            )
            runtime = ANALYZER._load_trace_comparator_runtime(
                source_path.resolve(),
                verified_source,
                _sha256(verified_source),
            )
            result = runtime.compare(Path("left"), Path("right"))

            self.assertTrue(result.equal)
            self.assertEqual(result.frames_compared, 7)
            self.assertEqual(result.left_provenance, {"origin": "verified"})
            self.assertEqual(result.right_provenance, {"origin": "verified"})
            self.assertEqual(cache_path.read_bytes(), poisoned_cache)
            viewer_source = b'''"""DIAGNOSTIC REPLAY NOT LIVE SOLVER TIMING RENDERING EXCLUDED FROM COMPUTE"""
import trace_compare

def build_replay_viewer(*args, **kwargs):
    return trace_compare.MARKER
'''
            viewer_path = source_path.parent / "viewer.py"
            viewer_path.write_bytes(viewer_source)
            viewer_cache = Path(importlib.util.cache_from_source(str(viewer_path)))
            builder = ANALYZER._load_replay_builder(
                viewer_path.resolve(),
                viewer_source,
                _sha256(viewer_source),
                runtime.module,
            )
            self.assertEqual(builder(), "verified")
            self.assertFalse(viewer_cache.exists())
            self.assertEqual(cache_path.read_bytes(), poisoned_cache)

    def test_verified_comparator_creates_no_bytecode_cache(self) -> None:
        source = b"""class Result:
    equal = True
    frames_compared = 1
    left_provenance = {}
    right_provenance = {}

def compare_traces(left, right):
    return Result()
"""
        with tempfile.TemporaryDirectory() as temporary:
            source_path = Path(temporary) / "trace_compare.py"
            source_path.write_bytes(source)
            cache_directory = source_path.parent / "__pycache__"
            self.assertFalse(cache_directory.exists())
            comparator = ANALYZER._load_trace_comparator(
                source_path.resolve(),
                source,
                _sha256(source),
            )
            self.assertTrue(comparator(Path("left"), Path("right")).equal)
            self.assertFalse(cache_directory.exists())
            with self.assertRaisesRegex(
                ANALYZER.AnalysisError,
                "authenticated-bytes-sha256-mismatch",
            ):
                ANALYZER._load_trace_comparator(
                    source_path.resolve(),
                    source,
                    "0" * 64,
                )

    def test_real_authenticated_viewer_audits_and_loads_without_cache(self) -> None:
        context = ANALYZER._validate_plan(
            REPO_ROOT,
            REPO_ROOT / ANALYZER.PLAN_RELATIVE_PATH,
        )
        cache_directory = context.viewer_path.parent / "__pycache__"
        before = {
            path.name: _sha256(path.read_bytes())
            for path in cache_directory.glob(
                f"{context.viewer_path.stem}.*.pyc"
            )
        } if cache_directory.exists() else {}
        runtime = ANALYZER._load_trace_comparator_runtime(
            context.trace_compare_path,
            context.trace_compare_bytes,
            context.trace_compare_sha256,
        )
        builder = ANALYZER._load_replay_builder(
            context.viewer_path,
            context.viewer_bytes,
            context.viewer_sha256,
            runtime.module,
        )
        self.assertTrue(callable(builder))
        after = {
            path.name: _sha256(path.read_bytes())
            for path in cache_directory.glob(
                f"{context.viewer_path.stem}.*.pyc"
            )
        } if cache_directory.exists() else {}
        self.assertEqual(after, before)

    def test_analysis_has_no_process_launch_surface(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("import subprocess", source)
        self.assertNotIn("from subprocess", source)
        with tempfile.TemporaryDirectory() as temporary, contextlib.ExitStack() as stack:
            bomb = mock.Mock(side_effect=AssertionError("process launch attempted"))
            for owner, names in (
                (
                    subprocess,
                    ("Popen", "run", "call", "check_call", "check_output"),
                ),
                (
                    os,
                    (
                        "system",
                        "fork",
                        "posix_spawn",
                        "posix_spawnp",
                        "spawnv",
                        "spawnve",
                        "spawnvp",
                        "spawnvpe",
                    ),
                ),
            ):
                for name in names:
                    if hasattr(owner, name):
                        stack.enter_context(mock.patch.object(owner, name, bomb))
            report = ANALYZER.analyze_visual_cohort(
                REPO_ROOT,
                results_root=Path(temporary) / "absent",
            )
        self.assertEqual(report["summary"]["missing_scene_count"], 50)
        self.assertEqual(bomb.call_count, 0)


if __name__ == "__main__":
    unittest.main()
