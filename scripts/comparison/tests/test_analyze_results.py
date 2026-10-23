from __future__ import annotations

import copy
import hashlib
import json
import random
import tempfile
import unittest
from pathlib import Path

from scripts.comparison import analyze_results as ANALYZER


HISTORICAL_TRACE_DIGEST_ALGORITHM = (
    "SHA-256 over canonical nlohmann JSON serialization of each "
    "implementation-neutral robot frame"
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ANALYZER.canonical_json_bytes(value))


def sha(character: str) -> str:
    return character * 64


class SessionFactory:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.analysis_sha = digest(Path(ANALYZER.__file__).resolve())
        self.assets = root / "_frozen_inputs"
        self.assets.mkdir()
        self.sources = {}
        for method in ANALYZER.METHODS:
            source = self.assets / f"{method}-source"
            source.mkdir()
            self.sources[method] = source
        self.plan_artifacts = {}
        for name in (
            "pixi_lock",
            "build_manifest",
            "environment_manifest",
            "config_template",
            "protocol",
            "experiment_manifest",
        ):
            path = self.assets / f"{name}.txt"
            if name == "environment_manifest":
                write_json(
                    path,
                    {
                        "families": list(
                            ANALYZER.CONFIRMATORY_ENVIRONMENTS
                        ),
                        "environments": [],
                    },
                )
            else:
                path.write_text(f"{name}\n", encoding="utf-8")
            self.plan_artifacts[name] = {
                "path": str(path),
                "sha256": digest(path),
            }
        analysis_copy = self.assets / "analyze_results.py"
        analysis_copy.write_bytes(Path(ANALYZER.__file__).resolve().read_bytes())
        self.plan_artifacts["analysis_revision"] = {
            "path": str(analysis_copy),
            "sha256": digest(analysis_copy),
        }
        self.calibration_analysis = self.assets / "calibration-analysis.json"
        write_json(
            self.calibration_analysis,
            {"schema_version": "dancers-comparison-analysis-v1"},
        )
        runner = self.assets / "run_paired.py"
        runner.write_text("runner\n", encoding="utf-8")
        self.runner_sha = digest(runner)
        self.binaries = {}
        for mode in ANALYZER.MODES:
            for method in ANALYZER.METHODS:
                path = self.assets / f"{mode}-{method}.bin"
                path.write_text(f"{mode}-{method}\n", encoding="utf-8")
                self.binaries[(mode, method)] = {
                    "path": str(path),
                    "sha256": digest(path),
                }
        self.method_revisions = {
            "reference": {
                "base_revision": (
                    "986f47deec5b079663c7d7f6120bfc20f0467901"
                ),
                "head_revision": "1" * 40,
                "tree_hash": "2" * 40,
            },
            "optimized": {
                "base_revision": (
                    "99364a41f1535a4bb0eddbe5a10030127ea77086"
                ),
                "head_revision": "3" * 40,
                "tree_hash": "4" * 40,
            },
        }
        build_manifest = {
            "schema_version": "dancers-comparison-build-manifest-v1",
            "repository": {
                "reference_pin": self.method_revisions["reference"][
                    "base_revision"
                ],
                "optimized_pin": self.method_revisions["optimized"][
                    "base_revision"
                ],
                "reference_applied_head": self.method_revisions["reference"][
                    "head_revision"
                ],
                "reference_tree": self.method_revisions["reference"][
                    "tree_hash"
                ],
                "optimized_applied_head": self.method_revisions["optimized"][
                    "head_revision"
                ],
                "optimized_tree": self.method_revisions["optimized"][
                    "tree_hash"
                ],
            },
            "builds": {
                f"{method}_{mode}": {
                    "observer_compiled": mode == "fidelity",
                    "algorithm_revision": self.method_revisions[method][
                        "base_revision"
                    ],
                    "applied_head": self.method_revisions[method][
                        "head_revision"
                    ],
                    "binary_path": self.binaries[(mode, method)]["path"],
                    "binary_sha256": self.binaries[(mode, method)]["sha256"],
                }
                for mode in ANALYZER.MODES
                for method in ANALYZER.METHODS
            },
        }
        build_manifest_path = Path(
            self.plan_artifacts["build_manifest"]["path"]
        )
        write_json(build_manifest_path, build_manifest)
        self.plan_artifacts["build_manifest"]["sha256"] = digest(
            build_manifest_path
        )
        self.globals = {
            "common_patch_sha256": sha("a"),
            "common_patch_full_index_diff_sha256": sha("b"),
            "build_manifest_sha256": self.plan_artifacts[
                "build_manifest"
            ]["sha256"],
            "pixi_lock_sha256": self.plan_artifacts["pixi_lock"]["sha256"],
            "protocol_sha256": self.plan_artifacts["protocol"]["sha256"],
            "experiment_manifest_sha256": self.plan_artifacts[
                "experiment_manifest"
            ]["sha256"],
            "analysis_revision_sha256": self.analysis_sha,
            "runner_sha256": self.runner_sha,
        }

    def host_snapshot(self, session: Path) -> dict:
        return {
            "checked_utc": "2026-07-28T11:30:00Z",
            "filesystem_path": str(session),
            "free_disk_bytes": 100_000_000_000,
            "minimum_free_disk_bytes": 21_474_836_480,
            "disk_valid": True,
            "power_mode": "AC Power",
            "power_probe": {
                "command": ["pmset", "-g", "batt"],
                "returncode": 0,
                "stdout": "Now drawing from 'AC Power'",
                "stderr": "",
                "source": "AC Power",
                "valid": True,
            },
            "thermal_policy": (
                "ProcessInfo.thermalState rawValue must be 0 before every "
                "block; fail closed on probe error/change; five-minute idle "
                "cooldown before a measured session"
            ),
            "thermal_probe": {
                "command": ["swift", "-e", "probe"],
                "module_cache_path": str(session / "module-cache"),
                "returncode": 0,
                "stdout": "0\n",
                "stderr": "",
                "state": 0,
                "valid": True,
            },
            "valid": True,
        }

    def condition(
        self,
        pair_id: str,
        environment: str,
        seed: int,
        robot_count: int,
        map_digit: str,
        scenario_digit: str,
        reference_cpu: list[float],
        optimized_cpu: list[float],
        *,
        reference_wall: list[float] | None = None,
        optimized_wall: list[float] | None = None,
        reference_rss: list[float] | None = None,
        optimized_rss: list[float] | None = None,
        semantic_trace_equal: bool = True,
    ) -> dict:
        map_path = self.assets / f"map-{map_digit}.dat"
        scenario_path = self.assets / f"scenario-{scenario_digit}.dat"
        if not map_path.exists():
            map_path.write_text(f"map-{map_digit}\n", encoding="utf-8")
        if not scenario_path.exists():
            scenario_path.write_text(
                f"scenario-{scenario_digit}\n", encoding="utf-8"
            )
        return {
            "pair_id": pair_id,
            "environment": environment,
            "seed": seed,
            "robot_count": robot_count,
            "map_sha256": digest(map_path),
            "scenario_sha256": digest(scenario_path),
            "map_path": str(map_path),
            "scenario_path": str(scenario_path),
            "reference_cpu": reference_cpu,
            "optimized_cpu": optimized_cpu,
            "reference_wall": reference_wall or [
                value * 2 for value in reference_cpu
            ],
            "optimized_wall": optimized_wall or [
                value * 2 for value in optimized_cpu
            ],
            "reference_rss": reference_rss or [
                100.0 for _ in reference_cpu
            ],
            "optimized_rss": optimized_rss or [
                80.0 for _ in optimized_cpu
            ],
            "semantic_trace_equal": semantic_trace_equal,
        }

    def _provenance(
        self,
        *,
        attempt_id: str,
        method: str,
        mode: str,
        pair_id: str,
        repetition: int,
        order: str,
        attempt_kind: str,
        config_sha: str,
        map_sha: str,
        scenario_sha: str,
        phase: str,
    ) -> dict:
        return {
            "run_id": attempt_id,
            "implementation_id": method,
            "algorithm_revision": self.method_revisions[method][
                "base_revision"
            ],
            "source_head_revision": self.method_revisions[method][
                "head_revision"
            ],
            "source_tree_hash": self.method_revisions[method]["tree_hash"],
            **self.globals,
            "binary_sha256": self.binaries[(mode, method)]["sha256"],
            "config_sha256": config_sha,
            "map_sha256": map_sha,
            "scenario_sha256": scenario_sha,
            "omp_threads": 1,
            "phase": phase,
            "mode": mode,
            "pair_id": pair_id,
            "repetition": repetition,
            "order": order,
            "attempt_kind": attempt_kind,
            "maximum_peak_rss_mib": 49_152.0,
            "power_mode": "AC Power",
            "thermal_policy": (
                "ProcessInfo.thermalState rawValue must be 0 before every "
                "block; fail closed on probe error/change; five-minute idle "
                "cooldown before a measured session"
            ),
            "task_validity_predicate_id": (
                "fixed-horizon-safe-navigation-v1"
            ),
        }

    def _summary(
        self,
        provenance: dict,
        robot_count: int,
        *,
        trace_detail: str,
    ) -> dict:
        return {
            "schema_version": "dancers-comparison-summary-v1",
            "metadata": provenance,
            "trace_detail": trace_detail,
            "run_valid": True,
            "termination_reason": "maximum_time",
            "final_tick": 100,
            "simulated_time_seconds": 10.0,
            "final_robot_count": robot_count,
            "maximum_robot_count": robot_count,
            "minimum_pair_clearance_m": 2.5,
            "minimum_obstacle_clearance_m": 1.25,
            "pair_collision_exposure_seconds": 0.0,
            "obstacle_collision_exposure_seconds": 0.0,
            "pair_collision_events": 0,
            "obstacle_collision_events": 0,
            "nonfinite_state_observations": 0,
            "nonfinite_numeric_observations": 0,
            "invalid_robot_frames": 0,
            "invalid_geometry_evaluations": 0,
            "goals_total": robot_count,
            "goals_reached": robot_count,
            "all_goals_reached": True,
            "first_all_goals_tick": 80,
            "iterations": 4,
            "total_path_length_m": float(robot_count * 10),
            "mean_path_length_m_per_robot": 10.0,
            "per_robot_goal_tick": {
                str(index): 80 for index in range(robot_count)
            },
            "per_robot_path_length_m": {
                str(index): 10.0 for index in range(robot_count)
            },
            "paper_metrics": {
                "consensus_reached": True,
                "final_connectivity": 2.0,
                "final_decision": 1,
                "final_heading_error": 0.0,
                "final_iterations": 4,
                "final_position_error": 0.0,
            },
        }

    def _attempt(
        self,
        session: Path,
        *,
        sequence: int,
        block: dict,
        condition: dict,
        method: str,
        position: int,
        mode: str,
        attempt_kind: str,
        config_path: Path,
        cpu: float,
        wall: float,
        rss: float,
        terminal: str,
        trace_detail: str,
        trace_schema: str,
        phase: str,
    ) -> dict:
        attempt_id = (
            f"{sequence:06d}__{block['block_id']}__{method}"
        )
        attempt_root = session / "attempts" / attempt_id
        attempt_root.mkdir(parents=True)
        stdout = attempt_root / "stdout.log"
        stderr = attempt_root / "stderr.log"
        stdout.write_text("fixed work complete\n", encoding="utf-8")
        stderr.write_text(
            "" if terminal == "completed" else "timed out\n",
            encoding="utf-8",
        )
        provenance = self._provenance(
            attempt_id=attempt_id,
            method=method,
            mode=mode,
            pair_id=condition["pair_id"],
            repetition=block["repetition"],
            order=block["order"],
            attempt_kind=attempt_kind,
            config_sha=digest(config_path),
            map_sha=condition["map_sha256"],
            scenario_sha=condition["scenario_sha256"],
            phase=phase,
        )
        write_json(attempt_root / "runner_metadata.json", provenance)
        trace_record = None
        summary_record = None
        fidelity_metrics = None
        if mode == "fidelity":
            write_json(attempt_root / "observer_metadata.json", provenance)
            trace = attempt_root / "trace.jsonl"
            if trace_schema == ANALYZER.TRACE_SCHEMA:
                digest_algorithm = ANALYZER.TRACE_DIGEST_ALGORITHM
                scopes = {
                    "future_state_scope": (
                        "fixture common future-affecting state"
                    ),
                    "message_checksum_scope": (
                        "fixture common live-message state"
                    ),
                }
            elif trace_schema == ANALYZER.HISTORICAL_TRACE_SCHEMA:
                digest_algorithm = HISTORICAL_TRACE_DIGEST_ALGORITHM
                scopes = {}
            else:
                raise ValueError(f"unsupported trace schema {trace_schema!r}")
            trace_header = {
                "type": "header",
                "schema_version": trace_schema,
                "metadata": provenance,
                "resolved_config": {
                    "config_file": str(config_path),
                    "scenario_file": condition["scenario_path"],
                    "obstacle_file": condition["map_path"],
                },
                "observer": {
                    "trace_detail": trace_detail,
                    "timing_eligible": False,
                    "digest_algorithm": digest_algorithm,
                    **scopes,
                },
            }
            trace.write_bytes(ANALYZER.canonical_json_bytes(trace_header))
            summary = attempt_root / "summary.json"
            summary_value = self._summary(
                provenance,
                condition["robot_count"],
                trace_detail=trace_detail,
            )
            write_json(summary, summary_value)
            parity_names = (
                "termination_reason",
                "all_goals_reached",
                "goals_reached",
                "goals_total",
                "first_all_goals_tick",
                "iterations",
                "total_path_length_m",
                "mean_path_length_m_per_robot",
                "per_robot_goal_tick",
                "per_robot_path_length_m",
                "obstacle_collision_events",
                "obstacle_collision_exposure_seconds",
                "pair_collision_events",
                "pair_collision_exposure_seconds",
                "minimum_obstacle_clearance_m",
                "minimum_pair_clearance_m",
                "invalid_geometry_evaluations",
                "invalid_robot_frames",
                "nonfinite_numeric_observations",
                "nonfinite_state_observations",
            )
            task_names = (
                "termination_reason",
                "final_tick",
                "final_robot_count",
                "maximum_robot_count",
                "run_valid",
                "invalid_geometry_evaluations",
                "invalid_robot_frames",
                "nonfinite_numeric_observations",
                "nonfinite_state_observations",
                "obstacle_collision_events",
                "obstacle_collision_exposure_seconds",
                "pair_collision_events",
                "pair_collision_exposure_seconds",
            )
            fidelity_metrics = {
                "task_validity_predicate_id": (
                    "fixed-horizon-safe-navigation-v1"
                ),
                "task_valid": True,
                "task_fields": {
                    **{key: summary_value[key] for key in task_names},
                    "minimum_pair_clearance_m": summary_value[
                        "minimum_pair_clearance_m"
                    ],
                    "minimum_obstacle_clearance_m": summary_value[
                        "minimum_obstacle_clearance_m"
                    ],
                },
                "paper_metrics": summary_value["paper_metrics"],
                "summary_parity_fields": {
                    key: summary_value[key] for key in parity_names
                },
            }
            trace_record = {"path": str(trace), "sha256": digest(trace)}
            summary_record = {
                "path": str(summary),
                "sha256": digest(summary),
            }
        if terminal == "completed":
            resource = {
                "returncode": 0,
                "exit_code": 0,
                "signal": None,
                "timed_out": False,
                "wall_seconds": wall,
                "user_cpu_seconds": cpu * 0.75,
                "system_cpu_seconds": cpu * 0.25,
                "total_cpu_seconds": cpu,
                "peak_rss_raw": int(rss * 1024),
                "peak_rss_raw_unit": "KiB",
                "peak_rss_mib": rss,
                "maximum_peak_rss_mib": 49_152.0,
                "peak_rss_within_limit": rss <= 49_152.0,
            }
            error = None
        else:
            resource = {
                "returncode": -15,
                "exit_code": None,
                "signal": 15,
                "timed_out": True,
                "wall_seconds": wall,
                "user_cpu_seconds": cpu * 0.75,
                "system_cpu_seconds": cpu * 0.25,
                "total_cpu_seconds": cpu,
                "peak_rss_raw": int(rss * 1024),
                "peak_rss_raw_unit": "KiB",
                "peak_rss_mib": rss,
                "maximum_peak_rss_mib": 49_152.0,
                "peak_rss_within_limit": rss <= 49_152.0,
            }
            error = "frozen timeout"
        run = {
            "schema_version": "dancers-laptop-comparison-attempt-v1",
            "sequence": sequence,
            "attempt_id": attempt_id,
            "block_id": block["block_id"],
            "pair_id": condition["pair_id"],
            "repetition": block["repetition"],
            "attempt_kind": attempt_kind,
            "order": block["order"],
            "order_position": position,
            "phase": phase,
            "mode": mode,
            "environment": condition["environment"],
            "seed": condition["seed"],
            "robot_count": condition["robot_count"],
            "method": method,
            "terminal_category": terminal,
            "orchestration_error": error,
            "task_valid": (
                True if mode == "fidelity" and terminal == "completed" else None
            ),
            "fidelity_metrics": (
                fidelity_metrics
                if mode == "fidelity" and terminal == "completed"
                else None
            ),
            "resource": resource,
            "provenance": provenance,
            "artifacts": {
                "config": {
                    "path": str(config_path),
                    "sha256": digest(config_path),
                },
                "stdout": {"path": str(stdout), "sha256": digest(stdout)},
                "stderr": {"path": str(stderr), "sha256": digest(stderr)},
                "trace": trace_record,
                "summary": summary_record,
            },
            "completed_utc": "2026-07-28T12:00:00Z",
        }
        write_json(attempt_root / "result.json", run)
        return run

    def create(
        self,
        name: str,
        mode: str,
        conditions: list[dict],
        *,
        repetitions: int,
        failed_attempts: set[tuple[str, int, str]] | None = None,
        capacity: dict | None = None,
        capacity_evaluation: dict | None = None,
        trace_detail: str = "digest",
        trace_schema: str = ANALYZER.TRACE_SCHEMA,
        phase: str = "calibration",
    ) -> Path:
        failed_attempts = failed_attempts or set()
        session = self.root / name
        session.mkdir()
        execution_grid = (
            list(capacity["grid"])
            if capacity is not None
            else (
                list(capacity_evaluation["grid"])
                if capacity_evaluation is not None
                else None
            )
        )
        ordered_conditions = list(conditions)
        schedule: list[tuple[dict, int]] = []
        if execution_grid is None:
            for repetition in range(1, repetitions + 1):
                schedule.extend(
                    (condition, repetition)
                    for condition in ordered_conditions
                )
        else:
            count_rank = {
                count: index for index, count in enumerate(execution_grid)
            }
            ordered_conditions.sort(
                key=lambda condition: (
                    condition["seed"],
                    count_rank[condition["robot_count"]],
                    condition["pair_id"],
                )
            )
            by_seed_count = {
                (condition["seed"], condition["robot_count"]): condition
                for condition in ordered_conditions
            }
            seeds = sorted(
                {condition["seed"] for condition in ordered_conditions}
            )
            base_counts = list(execution_grid)
            count_randomizer = random.Random(
                42 ^ 0x4341504143495459
            )
            count_randomizer.shuffle(base_counts)
            cycle = 0
            for repetition in range(1, repetitions + 1):
                for seed in seeds:
                    count_order = (
                        base_counts
                        if cycle % 2 == 0
                        else base_counts[::-1]
                    )
                    schedule.extend(
                        (
                            by_seed_count[(seed, count)],
                            repetition,
                        )
                        for count in count_order
                    )
                    cycle += 1
        blocks = [
            {
                "block_id": (
                    f"{condition['pair_id']}__rep-{repetition:03d}"
                ),
                "pair_id": condition["pair_id"],
                "repetition": repetition,
                "order": None,
                "environment": condition["environment"],
                "seed": condition["seed"],
                "robot_count": condition["robot_count"],
            }
            for condition, repetition in schedule
        ]
        order_randomizer = random.Random(42)
        extra_order = (
            "AB" if order_randomizer.getrandbits(1) == 0 else "BA"
        )
        order_counts = {
            "AB": len(blocks) // 2,
            "BA": len(blocks) // 2,
        }
        if len(blocks) % 2:
            order_counts[extra_order] += 1
        orders = (
            ["AB"] * order_counts["AB"]
            + ["BA"] * order_counts["BA"]
        )
        order_randomizer.shuffle(orders)
        for block, order in zip(blocks, orders):
            block["order"] = order
        warmup_blocks: list[dict] = []
        if mode == "timing":
            seen_maps: set[str] = set()
            for condition in ordered_conditions:
                if condition["map_sha256"] in seen_maps:
                    continue
                seen_maps.add(condition["map_sha256"])
                warmup_blocks.append(
                    {
                        "block_id": (
                            f"warmup-{len(warmup_blocks) + 1:03d}-"
                            f"{condition['map_sha256'][:12]}"
                        ),
                        "pair_id": condition["pair_id"],
                        "repetition": 0,
                        "order": "warmup",
                        "environment": condition["environment"],
                        "seed": condition["seed"],
                        "robot_count": condition["robot_count"],
                        "map_sha256": condition["map_sha256"],
                    }
                )
        capacity_manifest = None
        if capacity is not None:
            capacity_manifest = {
                "environment": capacity["environment"],
                "reference_anchor_n": capacity["budget"][
                    "reference_anchor_n"
                ],
                "grid": capacity["grid"],
                "max_points": capacity["max_points"],
            }
        frozen_plan = {
            "schema_version": "dancers-laptop-comparison-plan-v1",
            "phase": phase,
            "mode": mode,
            "fixed_work_timesteps": 100,
            "timestep_seconds": 0.1,
            "idle_cooldown_seconds": 300,
            "timeout_seconds": 120.0,
            "repetitions": repetitions,
            "warmups_per_binary_map": 1,
            "omp_threads": 1,
            "order_seed": 42,
            "minimum_free_disk_bytes": 21_474_836_480,
            "maximum_peak_rss_mib": 49_152.0,
            "power_mode": "AC Power",
            "thermal_policy": (
                "ProcessInfo.thermalState rawValue must be 0 before every "
                "block; fail closed on probe error/change; five-minute idle "
                "cooldown before a measured session"
            ),
            "task_validity_predicate_id": (
                "fixed-horizon-safe-navigation-v1"
            ),
            "trace_detail": trace_detail,
            "runner_sha256": self.runner_sha,
            "run_cwd": str(self.root),
            "artifacts": copy.deepcopy(self.plan_artifacts),
            "methods": {
                method: {
                    "source_dir": str(self.sources[method]),
                    **self.method_revisions[method],
                    "binary": self.binaries[(mode, method)],
                }
                for method in ANALYZER.METHODS
            },
            "pairs": [
                {
                    "pair_id": condition["pair_id"],
                    "environment": condition["environment"],
                    "seed": condition["seed"],
                    "robot_count": condition["robot_count"],
                    "map": {
                        "path": condition["map_path"],
                        "sha256": condition["map_sha256"],
                    },
                    "scenario": {
                        "path": condition["scenario_path"],
                        "sha256": condition["scenario_sha256"],
                    },
                }
                for condition in conditions
            ],
        }
        if capacity_manifest is not None:
            frozen_plan["capacity_search"] = capacity_manifest
        if capacity_evaluation is not None:
            decision_path = self.assets / (
                f"capacity-decision-{capacity_evaluation['environment']}.json"
            )
            if not decision_path.exists():
                timeout_derivation = {
                    environment: {
                        "calibration_plan_timeout_seconds": 120.0,
                        "maximum_completed_measured_wall_seconds": 1.0,
                        "three_times_wall_ceiled_to_ten_seconds": 10.0,
                    }
                    for environment in ANALYZER.CONFIRMATORY_ENVIRONMENTS
                }
                environment_records = []
                for environment in ANALYZER.CONFIRMATORY_ENVIRONMENTS:
                    if environment == capacity_evaluation["environment"]:
                        candidate = capacity_evaluation["candidate_n_star"]
                        next_point = capacity_evaluation["next_grid_point"]
                    else:
                        candidate = capacity_evaluation["grid"][0]
                        next_point = capacity_evaluation["grid"][1]
                    environment_records.append(
                        {
                            "environment": environment,
                            "reference_anchor_n": capacity_evaluation[
                                "reference_anchor_n"
                            ],
                            "grid": capacity_evaluation["grid"],
                            "candidate_n_star": candidate,
                            "next_grid_point": next_point,
                            "validation_timing_counts": (
                                capacity_evaluation["grid"]
                            ),
                            "validation_fidelity_counts": (
                                capacity_evaluation["grid"]
                            ),
                            "validation_timeout_seconds": 120.0,
                        }
                    )
                write_json(
                    decision_path,
                    {
                        "schema_version": "dancers-capacity-decision-v1",
                        "status": "frozen-before-validation",
                        "selection_rule_id": (
                            "highest-all-gate-calibration-passing-n-v1"
                        ),
                        "provenance": {
                            "calibration_analysis": {
                                "path": str(self.calibration_analysis),
                                "sha256": digest(
                                    self.calibration_analysis
                                ),
                            },
                            "analyzer": copy.deepcopy(
                                self.plan_artifacts["analysis_revision"]
                            ),
                            "experiment_manifest": copy.deepcopy(
                                self.plan_artifacts["experiment_manifest"]
                            ),
                            "environment_manifest": copy.deepcopy(
                                self.plan_artifacts["environment_manifest"]
                            ),
                            "calibration_sessions": [
                                {
                                    "session_id": (
                                        "calibration-provenance-session"
                                    ),
                                    "path": str(self.root),
                                    "mode": "timing",
                                    "hashes": {
                                        "session_json_sha256": sha("1"),
                                        "run_manifest_json_sha256": sha("2"),
                                        "frozen_plan_sha256": sha("3"),
                                        "runs_jsonl_sha256": sha("4"),
                                        "pairs_jsonl_sha256": sha("5"),
                                        "capacity_json_sha256": sha("6"),
                                    },
                                }
                            ],
                            "timeout_derivation": timeout_derivation,
                        },
                        "seeds": {
                            "validation": list(range(11, 31)),
                            "audit": list(range(31, 51)),
                        },
                        "environments": environment_records,
                    },
                )
            frozen_plan["artifacts"]["capacity_decision"] = {
                "path": str(decision_path),
                "sha256": digest(decision_path),
            }
            frozen_plan["capacity_evaluation"] = capacity_evaluation
        plan_path = session / "frozen-plan.json"
        write_json(plan_path, frozen_plan)
        write_json(session / "frozen_plan.json", frozen_plan)
        preflight_host = self.host_snapshot(session)
        idle_cooldown = {
            "required_seconds": 300,
            "eligible": True,
            "performed": True,
            "elapsed_seconds": 300.0,
            "before": preflight_host,
            "after": self.host_snapshot(session),
            "valid": True,
        }
        write_json(
            session / "preflight.json",
            {
                "host_conditions": preflight_host,
                "idle_cooldown": idle_cooldown,
            },
        )
        manifest = {
            "schema_version": "dancers-laptop-comparison-plan-v1",
            "created_utc": "2026-07-28T11:00:00Z",
            "plan_path": str(plan_path),
            "plan_sha256": digest(plan_path),
            "phase": phase,
            "mode": mode,
            "serial_execution": True,
            "order_policy": "seeded balanced AB/BA assignment",
            "count_order_policy": "frozen pair order repeated in complete rounds",
            "order_seed": 42,
            "fixed_work_timesteps": 100,
            "timestep_seconds": 0.1,
            "idle_cooldown_seconds": 300,
            "idle_cooldown": idle_cooldown,
            "repetitions": repetitions,
            "warmups_per_binary_map": 1,
            "warmup_blocks": warmup_blocks,
            "blocks": blocks,
            "capacity_search": capacity_manifest,
            "capacity_evaluation": capacity_evaluation,
            "task_validity_predicate_id": (
                "fixed-horizon-safe-navigation-v1"
            ),
            "maximum_peak_rss_mib": 49_152.0,
            "minimum_free_disk_bytes": 21_474_836_480,
            "power_mode": "AC Power",
            "thermal_policy": (
                "ProcessInfo.thermalState rawValue must be 0 before every "
                "block; fail closed on probe error/change; five-minute idle "
                "cooldown before a measured session"
            ),
            "preflight_host_conditions": preflight_host,
        }
        write_json(session / "run_manifest.json", manifest)

        by_pair_id = {
            condition["pair_id"]: condition for condition in conditions
        }
        runs: list[dict] = []
        pairs: list[dict] = []
        sequence = 0
        for block in warmup_blocks:
            condition = by_pair_id[block["pair_id"]]
            block_root = session / "blocks" / block["block_id"]
            block_root.mkdir(parents=True)
            config = block_root / "config.yaml"
            config.write_text(
                f"simulation: {block['block_id']}\n", encoding="utf-8"
            )
            block_runs: dict[str, dict] = {}
            for position, method in enumerate(("reference", "optimized"), 1):
                sequence += 1
                run = self._attempt(
                    session,
                    sequence=sequence,
                    block=block,
                    condition=condition,
                    method=method,
                    position=position,
                    mode=mode,
                    attempt_kind="warmup",
                    config_path=config,
                    cpu=1.0,
                    wall=1.0,
                    rss=10.0,
                    terminal="completed",
                    trace_detail=trace_detail,
                    trace_schema=trace_schema,
                    phase=phase,
                )
                runs.append(run)
                block_runs[method] = run
            write_json(
                block_root / "warmup.json",
                {
                    "schema_version": "dancers-comparison-warmup-v1",
                    "block_id": block["block_id"],
                    "pair_id": block["pair_id"],
                    "environment": block["environment"],
                    "seed": block["seed"],
                    "robot_count": block["robot_count"],
                    "map_sha256": block["map_sha256"],
                    "attempt_ids": {
                        method: block_runs[method]["attempt_id"]
                        for method in ANALYZER.METHODS
                    },
                    "config_sha256_equal": True,
                    "terminal_categories": {
                        method: "completed" for method in ANALYZER.METHODS
                    },
                    "warmup_valid": True,
                    "host_conditions_before": self.host_snapshot(session),
                    "host_conditions_after": self.host_snapshot(session),
                    "host_block_valid": True,
                    "block_terminal_category": "completed",
                },
            )

        for block in blocks:
            condition = by_pair_id[block["pair_id"]]
            repetition = block["repetition"]
            index = repetition - 1
            block_root = session / "blocks" / block["block_id"]
            block_root.mkdir(parents=True)
            config = block_root / "config.yaml"
            config.write_text(
                f"simulation: {block['block_id']}\n", encoding="utf-8"
            )
            order = (
                ("reference", "optimized")
                if block["order"] == "AB"
                else ("optimized", "reference")
            )
            block_runs: dict[str, dict] = {}
            for position, method in enumerate(order, 1):
                sequence += 1
                terminal = (
                    "runner-timeout"
                    if (condition["pair_id"], repetition, method)
                    in failed_attempts
                    else "completed"
                )
                run = self._attempt(
                    session,
                    sequence=sequence,
                    block=block,
                    condition=condition,
                    method=method,
                    position=position,
                    mode=mode,
                    attempt_kind="measured",
                    config_path=config,
                    cpu=condition[f"{method}_cpu"][index],
                    wall=condition[f"{method}_wall"][index],
                    rss=condition[f"{method}_rss"][index],
                    terminal=terminal,
                    trace_detail=trace_detail,
                    trace_schema=trace_schema,
                    phase=phase,
                )
                runs.append(run)
                block_runs[method] = run
            terminals = {
                method: block_runs[method]["terminal_category"]
                for method in ANALYZER.METHODS
            }
            completed = all(value == "completed" for value in terminals.values())
            semantic = (
                condition["semantic_trace_equal"]
                if mode == "fidelity" and completed
                else None
            )
            pair_valid = completed and (
                mode == "timing" or semantic is True
            )
            pair = {
                "schema_version": "dancers-laptop-comparison-pair-v1",
                "block_id": block["block_id"],
                "pair_id": block["pair_id"],
                "repetition": repetition,
                "order": block["order"],
                "phase": phase,
                "mode": mode,
                "environment": condition["environment"],
                "seed": condition["seed"],
                "robot_count": condition["robot_count"],
                "attempt_ids": {
                    method: block_runs[method]["attempt_id"]
                    for method in ANALYZER.METHODS
                },
                "config_sha256_equal": True,
                "terminal_categories": terminals,
                "semantic_trace_equal": semantic,
                "frames_compared": 200 if mode == "fidelity" else None,
                "mismatch": (
                    {
                        "tick": 20,
                        "phase": "post_update",
                        "path": "$.robots[0].state",
                        "reason": "different",
                        "left": 1,
                        "right": 2,
                    }
                    if mode == "fidelity" and semantic is False
                    else None
                ),
                "paper_metrics_equal": (
                    True if mode == "fidelity" and completed else None
                ),
                "summary_parity_fields_equal": (
                    True if mode == "fidelity" and completed else None
                ),
                "summary_mismatch": None,
                "task_validity_predicate_id": (
                    "fixed-horizon-safe-navigation-v1"
                ),
                "task_valid": (
                    True if mode == "fidelity" and completed else None
                ),
                "host_conditions_before": self.host_snapshot(session),
                "host_conditions_after": self.host_snapshot(session),
                "host_block_valid": True,
                "block_terminal_category": "completed",
                "pair_valid": pair_valid,
            }
            pairs.append(pair)
            write_json(block_root / "pair.json", pair)

        (session / "runs.jsonl").write_bytes(
            b"".join(ANALYZER.canonical_json_bytes(run) for run in runs)
        )
        (session / "pairs.jsonl").write_bytes(
            b"".join(ANALYZER.canonical_json_bytes(pair) for pair in pairs)
        )
        if capacity is not None:
            write_json(session / "capacity.json", capacity)
        session_record = {
            "schema_version": "dancers-laptop-comparison-session-v1",
            "completed_utc": "2026-07-28T13:00:00Z",
            "attempt_count": len(runs),
            "warmup_attempt_count": sum(
                run["attempt_kind"] == "warmup" for run in runs
            ),
            "warmups_valid": True,
            "timestep_seconds": 0.1,
            "idle_cooldown_seconds": 300,
            "idle_cooldown": idle_cooldown,
            "minimum_free_disk_bytes": 21_474_836_480,
            "maximum_peak_rss_mib": 49_152.0,
            "power_mode": "AC Power",
            "thermal_policy": (
                "ProcessInfo.thermalState rawValue must be 0 before every "
                "block; fail closed on probe error/change; five-minute idle "
                "cooldown before a measured session"
            ),
            "task_validity_predicate_id": (
                "fixed-horizon-safe-navigation-v1"
            ),
            "host_contaminated_blocks": [],
            "pair_count": len(pairs),
            "all_attempts_completed": all(
                run["terminal_category"] == "completed" for run in runs
            ),
            "all_pairs_valid": all(pair["pair_valid"] for pair in pairs),
            "stopped_early": False,
            "capacity": capacity,
            "capacity_evaluation": capacity_evaluation,
        }
        write_json(session / "session.json", session_record)
        return session


class AnalyzeResultsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.factory = SessionFactory(self.root)
        self.open_seed_1 = self.factory.condition(
            "open-s1-n2",
            "open",
            1,
            2,
            "7",
            "8",
            [10.0, 12.0, 14.0],
            [5.0, 6.0, 7.0],
        )
        self.open_seed_2 = self.factory.condition(
            "open-s2-n2",
            "open",
            2,
            2,
            "7",
            "9",
            [20.0, 22.0, 24.0],
            [10.0, 11.0, 12.0],
        )
        self.maze_seed_1 = self.factory.condition(
            "maze-s1-n2",
            "perfect_maze",
            1,
            2,
            "0",
            "a",
            [8.0, 10.0, 12.0],
            [6.0, 7.5, 9.0],
        )
        self.open_seed_1_n4 = self.factory.condition(
            "open-s1-n4",
            "open",
            1,
            4,
            "7",
            "b",
            [14.0, 16.0, 18.0],
            [5.0, 6.0, 7.0],
        )
        self.open_seed_2_n4 = self.factory.condition(
            "open-s2-n4",
            "open",
            2,
            4,
            "7",
            "c",
            [24.0, 26.0, 28.0],
            [10.0, 11.0, 12.0],
        )
        self.capacity = {
            "schema_version": "dancers-bounded-capacity-search-v2",
            "status": "provisional-compute-only",
            "validated": False,
            "environment": "open",
            "grid": [2, 4],
            "max_points": 2,
            "budget": {
                "status": "provisional-compute-budget",
                "source": "median reference measurements at frozen anchor",
                "reference_anchor_n": 2,
                "cpu_seconds": 17.0,
                "wall_seconds": 34.0,
                "frozen_after_complete_anchor": True,
                "reference_seed_units": [],
            },
            "evaluations": [
                {
                    "robot_count": 2,
                    "complete": True,
                    "reference": {"complete": True},
                    "optimized": {"complete": True},
                    "provisional_compute_budget_pass": True,
                    "validated_capacity_pass": False,
                    "pending_gates": [
                        "task_validity",
                        "semantic_fidelity",
                        "memory_hypothesis",
                    ],
                },
                {
                    "robot_count": 4,
                    "complete": True,
                    "reference": {"complete": True},
                    "optimized": {"complete": True},
                    "provisional_compute_budget_pass": True,
                    "validated_capacity_pass": False,
                    "pending_gates": [
                        "task_validity",
                        "semantic_fidelity",
                        "memory_hypothesis",
                    ],
                },
            ],
            "largest_passing_n": None,
            "first_failing_n": None,
            "provisional_largest_compute_passing_n": 4,
            "first_provisional_compute_failing_n": None,
            "pending_gates": [
                "task_validity",
                "semantic_fidelity",
                "memory_hypothesis",
            ],
            "complete": True,
            "stop_reason": "finite-grid-exhausted",
            "unexecuted_grid_points": [],
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _complete_sessions(self) -> tuple[Path, Path]:
        timing = self.factory.create(
            "timing",
            "timing",
            [
                self.open_seed_1,
                self.open_seed_2,
                self.open_seed_1_n4,
                self.open_seed_2_n4,
            ],
            repetitions=3,
            capacity=self.capacity,
        )
        fidelity_conditions = [
            {
                **copy.deepcopy(self.open_seed_1_n4),
                "reference_cpu": [1.0],
                "optimized_cpu": [1.0],
                "reference_wall": [1.0],
                "optimized_wall": [1.0],
                "reference_rss": [10.0],
                "optimized_rss": [10.0],
            },
            {
                **copy.deepcopy(self.open_seed_2_n4),
                "reference_cpu": [1.0],
                "optimized_cpu": [1.0],
                "reference_wall": [1.0],
                "optimized_wall": [1.0],
                "reference_rss": [10.0],
                "optimized_rss": [10.0],
            },
            {
                **copy.deepcopy(self.maze_seed_1),
                "reference_cpu": [1.0],
                "optimized_cpu": [1.0],
                "reference_wall": [1.0],
                "optimized_wall": [1.0],
                "reference_rss": [10.0],
                "optimized_rss": [10.0],
                "semantic_trace_equal": False,
            },
        ]
        fidelity = self.factory.create(
            "fidelity",
            "fidelity",
            fidelity_conditions,
            repetitions=1,
        )
        return timing, fidelity

    def _rewrite_trace_header(
        self,
        session: Path,
        transform,
    ) -> str:
        runs_path = session / "runs.jsonl"
        runs = [
            json.loads(line)
            for line in runs_path.read_text(encoding="utf-8").splitlines()
        ]
        target = next(
            run
            for run in runs
            if run["mode"] == "fidelity"
            and run["terminal_category"] == "completed"
        )
        trace_path = Path(target["artifacts"]["trace"]["path"])
        header = json.loads(trace_path.read_text(encoding="utf-8"))
        transform(header)
        trace_path.write_bytes(ANALYZER.canonical_json_bytes(header))
        trace_sha = digest(trace_path)
        target["artifacts"]["trace"]["sha256"] = trace_sha
        runs_path.write_bytes(
            b"".join(
                ANALYZER.canonical_json_bytes(run) for run in runs
            )
        )
        result_path = (
            session / "attempts" / target["attempt_id"] / "result.json"
        )
        result = json.loads(result_path.read_text(encoding="utf-8"))
        result["artifacts"]["trace"]["sha256"] = trace_sha
        write_json(result_path, result)
        return target["attempt_id"]

    def test_medians_metrics_bootstrap_fidelity_and_capacity_gates(self) -> None:
        timing, fidelity = self._complete_sessions()
        result = ANALYZER.analyze_sessions(
            [timing, fidelity],
            bootstrap_samples=10_000,
        )

        self.assertEqual(result["schema_version"], ANALYZER.ANALYSIS_SCHEMA)
        self.assertEqual(result["input"]["phase"], "calibration")
        self.assertEqual(result["failure_ledger"]["warmup_attempt_count"], 2)
        self.assertEqual(result["failure_ledger"]["failed_attempt_count"], 0)
        rows = result["timing"]["collapsed_conditions"]
        row = next(
            item
            for item in rows
            if item["environment"] == "open"
            and item["seed"] == 1
            and item["robot_count"] == 2
        )
        self.assertEqual(row["paired_completed_repetitions"], 3)
        self.assertEqual(
            row["methods"]["reference"]["total_cpu_seconds"], 12.0
        )
        self.assertEqual(
            row["methods"]["optimized"]["total_cpu_seconds"], 6.0
        )
        self.assertEqual(
            row["effects"]["total_cpu_seconds"][
                "optimized_over_reference_ratio"
            ],
            0.5,
        )
        self.assertEqual(
            row["effects"]["total_cpu_seconds"][
                "optimized_minus_reference"
            ],
            -6.0,
        )
        self.assertAlmostEqual(
            row["methods"]["reference"][
                "cpu_seconds_per_robot_timestep"
            ],
            0.06,
        )
        self.assertAlmostEqual(
            row["methods"]["reference"][
                "robot_timesteps_per_cpu_second"
            ],
            200.0 / 12.0,
        )
        open_summary = result["timing"]["by_environment"]["open"]
        self.assertGreaterEqual(open_summary["cluster_count"], 2)
        self.assertEqual(
            result["bootstrap"]["frozen_seed"],
            ANALYZER.FROZEN_BOOTSTRAP_SEED,
        )

        fidelity_counts = result["fidelity"]["overall"]
        self.assertEqual(fidelity_counts["attempted_pair_blocks"], 3)
        self.assertEqual(fidelity_counts["exact_trace_pair_blocks"], 2)
        self.assertEqual(fidelity_counts["discordant_trace_pair_blocks"], 1)
        rmst_result = result["fidelity"][
            "restricted_mean_time_to_completion"
        ]
        self.assertTrue(rmst_result["available"])
        self.assertEqual(rmst_result["frozen_ceiling_seconds"], 10.0)
        capacity = result["capacity"]["sessions"][0]
        self.assertEqual(capacity["capacity_ratio"], 2.0)
        self.assertEqual(
            capacity["fixed_budget_estimates"]["eligible_condition_count"],
            2,
        )
        gates = capacity["evidence_gates"]
        self.assertEqual(
            gates["runner_capacity_state_interpretation"],
            (
                "exploratory search bookkeeping only; it is not a validated "
                "capacity conclusion"
            ),
        )
        self.assertTrue(
            gates["same_n_full_semantic_fidelity_gate"]["pass"]
        )
        self.assertEqual(
            gates["same_n_full_semantic_fidelity_gate"][
                "accepted_trace_details"
            ],
            ["digest"],
        )
        self.assertTrue(
            gates["collision_and_invalid_state_gate"]["pass"]
        )
        self.assertTrue(gates["task_validity_gate"]["pass"])
        self.assertTrue(gates["paper_metrics_identity_gate"]["pass"])
        self.assertTrue(gates["memory_safety_gate"]["pass"])
        self.assertEqual(
            gates["memory_safety_gate"]["frozen_limit_mib"], 49_152.0
        )
        self.assertFalse(gates["validated_capacity_claim_ready"])
        self.assertEqual(gates["claim_status"], "calibration-provisional")

    def test_analyzer_independently_admits_only_v3_digest_traces(self) -> None:
        for detail in ("full", "positions"):
            with self.subTest(trace_detail=detail):
                session = self.factory.create(
                    f"diagnostic-{detail}",
                    "fidelity",
                    [self.open_seed_1],
                    repetitions=1,
                    trace_detail=detail,
                )
                with self.assertRaisesRegex(
                    ANALYZER.AnalysisError,
                    "must be digest.*diagnostic-only",
                ):
                    ANALYZER.analyze_sessions(
                        [session],
                        bootstrap_samples=10_000,
                    )

        historical = self.factory.create(
            "historical-v2",
            "fidelity",
            [self.open_seed_1],
            repetitions=1,
            trace_schema=ANALYZER.HISTORICAL_TRACE_SCHEMA,
        )
        with self.assertRaisesRegex(
            ANALYZER.AnalysisError,
            "historical trace-v2.*inspection-only.*"
            + ANALYZER.HISTORICAL_V2_ANALYZER_REVISION,
        ):
            ANALYZER.analyze_sessions(
                [historical],
                bootstrap_samples=10_000,
            )

    def test_trace_header_is_rechecked_after_artifact_hash_verification(
        self,
    ) -> None:
        bad_scope = self.factory.create(
            "trace-bad-scope",
            "fidelity",
            [self.open_seed_1],
            repetitions=1,
        )
        self._rewrite_trace_header(
            bad_scope,
            lambda header: header["observer"].update(
                {"future_state_scope": ""}
            ),
        )
        with self.assertRaisesRegex(
            ANALYZER.AnalysisError,
            "trace-v3 digest contract is invalid.*future_state_scope",
        ):
            ANALYZER.analyze_sessions(
                [bad_scope],
                bootstrap_samples=10_000,
            )

        wrong_metadata = self.factory.create(
            "trace-wrong-provenance",
            "fidelity",
            [self.open_seed_1],
            repetitions=1,
        )
        attempt_id = self._rewrite_trace_header(
            wrong_metadata,
            lambda header: header["metadata"].update(
                {"run_id": "different-attempt"}
            ),
        )
        with self.assertRaisesRegex(
            ANALYZER.AnalysisError,
            "header.metadata provenance",
        ):
            ANALYZER.analyze_sessions(
                [wrong_metadata],
                bootstrap_samples=10_000,
            )
        self.assertNotEqual(attempt_id, "different-attempt")

    def test_warmups_and_failed_condition_are_excluded_but_retained(self) -> None:
        failed = self.factory.create(
            "failed",
            "timing",
            [self.open_seed_1],
            repetitions=3,
            failed_attempts={("open-s1-n2", 2, "optimized")},
        )
        result = ANALYZER.analyze_sessions(
            [failed],
            bootstrap_samples=10_000,
        )
        self.assertEqual(result["timing"]["collapsed_conditions"], [])
        excluded = result["timing"]["excluded_conditions"]
        self.assertEqual(len(excluded), 1)
        self.assertEqual(excluded[0]["reason"], "failed-measured-repetition")
        self.assertEqual(excluded[0]["failed_repetitions"], [2])
        ledger = result["failure_ledger"]
        self.assertEqual(ledger["failed_attempt_count"], 1)
        self.assertEqual(ledger["invalid_pair_block_count"], 1)
        self.assertEqual(ledger["attempt_failures"][0]["attempt_kind"], "measured")
        self.assertEqual(
            ledger["attempt_failures"][0]["terminal_category"],
            "runner-timeout",
        )
        self.assertEqual(
            ledger["attempt_failures"][0]["resource"]["total_cpu_seconds"],
            6.0,
        )

    def test_bootstrap_is_deterministic_and_minimum_is_enforced(self) -> None:
        timing, fidelity = self._complete_sessions()
        first = ANALYZER.analyze_sessions(
            [timing, fidelity],
            bootstrap_samples=10_000,
        )
        second = ANALYZER.analyze_sessions(
            [timing, fidelity],
            bootstrap_samples=10_000,
        )
        self.assertEqual(first, second)
        with self.assertRaisesRegex(ANALYZER.AnalysisError, ">= 10000"):
            ANALYZER.analyze_sessions(
                [timing],
                bootstrap_samples=9_999,
            )

    def test_missing_attempt_and_tampered_artifact_fail_closed(self) -> None:
        timing, _ = self._complete_sessions()
        runs_path = timing / "runs.jsonl"
        lines = runs_path.read_bytes().splitlines(keepends=True)
        runs_path.write_bytes(b"".join(lines[:-1]))
        with self.assertRaises(ANALYZER.AnalysisError):
            ANALYZER.analyze_sessions(
                [timing],
                bootstrap_samples=10_000,
            )

        clean = self.factory.create(
            "tampered",
            "timing",
            [self.open_seed_1],
            repetitions=3,
        )
        stdout = next((clean / "attempts").glob("*/stdout.log"))
        stdout.write_text("tampered\n", encoding="utf-8")
        with self.assertRaisesRegex(ANALYZER.AnalysisError, "hash mismatch"):
            ANALYZER.analyze_sessions(
                [clean],
                bootstrap_samples=10_000,
            )

    def test_duplicate_json_key_and_nonfinite_value_fail_closed(self) -> None:
        duplicate = self.factory.create(
            "duplicate",
            "timing",
            [self.open_seed_1],
            repetitions=3,
        )
        session_path = duplicate / "session.json"
        raw = session_path.read_text(encoding="utf-8")
        session_path.write_text(
            raw.replace(
                "{",
                '{"schema_version":"dancers-laptop-comparison-session-v1",',
                1,
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ANALYZER.AnalysisError, "duplicate JSON key"):
            ANALYZER.analyze_sessions(
                [duplicate],
                bootstrap_samples=10_000,
            )

        nonfinite = self.factory.create(
            "nonfinite",
            "timing",
            [self.open_seed_1],
            repetitions=3,
        )
        runs_path = nonfinite / "runs.jsonl"
        text = runs_path.read_text(encoding="utf-8")
        text = text.replace('"wall_seconds":1.0', '"wall_seconds":NaN', 1)
        runs_path.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ANALYZER.AnalysisError, "malformed JSON"):
            ANALYZER.analyze_sessions(
                [nonfinite],
                bootstrap_samples=10_000,
            )

    def test_analysis_revision_and_cross_phase_provenance_are_frozen(self) -> None:
        timing = self.factory.create(
            "revision",
            "timing",
            [self.open_seed_1],
            repetitions=3,
        )
        first_result = next((timing / "attempts").glob("*/result.json"))
        record = json.loads(first_result.read_text(encoding="utf-8"))
        record["provenance"]["analysis_revision_sha256"] = sha("9")
        write_json(first_result, record)
        runs_path = timing / "runs.jsonl"
        records = [
            json.loads(line)
            for line in runs_path.read_text(encoding="utf-8").splitlines()
        ]
        target = record["attempt_id"]
        for item in records:
            if item["attempt_id"] == target:
                item["provenance"]["analysis_revision_sha256"] = sha("9")
        runs_path.write_bytes(
            b"".join(ANALYZER.canonical_json_bytes(item) for item in records)
        )
        with self.assertRaisesRegex(
            ANALYZER.AnalysisError, "analysis_revision_sha256"
        ):
            ANALYZER.analyze_sessions(
                [timing],
                bootstrap_samples=10_000,
            )

        calibration = self.factory.create(
            "clean-calibration",
            "timing",
            [self.open_seed_1],
            repetitions=3,
        )
        validation_condition = copy.deepcopy(self.open_seed_1)
        validation_condition["pair_id"] = "open-s11-n2-cross-phase"
        validation_condition["seed"] = 11
        validation = self.factory.create(
            "clean-validation",
            "timing",
            [validation_condition],
            repetitions=3,
            phase="validation",
        )
        with self.assertRaisesRegex(
            ANALYZER.AnalysisError, "capacity_evaluation is required"
        ):
            ANALYZER.analyze_sessions(
                [calibration, validation],
                bootstrap_samples=10_000,
            )

    def test_plan_block_binding_and_completed_ceiling_fail_closed(self) -> None:
        tampered = self.factory.create(
            "plan-binding",
            "timing",
            [self.open_seed_1],
            repetitions=3,
        )
        manifest_path = tampered / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["blocks"].append(
            {
                "block_id": "post-freeze-block",
                "pair_id": "post-freeze-pair",
                "repetition": 1,
                "order": "AB",
                "environment": "open",
                "seed": 2,
                "robot_count": 2,
            }
        )
        write_json(manifest_path, manifest)
        with self.assertRaisesRegex(
            ANALYZER.AnalysisError, "deterministic block schedule"
        ):
            ANALYZER.analyze_sessions([tampered], bootstrap_samples=10_000)

        all_ab = self.factory.create(
            "all-ab-schedule",
            "timing",
            [self.open_seed_1, self.open_seed_2],
            repetitions=3,
        )
        manifest_path = all_ab / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for block in manifest["blocks"]:
            block["order"] = "AB"
        write_json(manifest_path, manifest)
        with self.assertRaisesRegex(
            ANALYZER.AnalysisError, "deterministic block schedule"
        ):
            ANALYZER.analyze_sessions([all_ab], bootstrap_samples=10_000)

        above = copy.deepcopy(self.open_seed_1)
        above["pair_id"] = "completed-above-ceiling"
        above["optimized_rss"] = [80.0, 50_000.0, 80.0]
        contradictory = self.factory.create(
            "completed-above-ceiling",
            "timing",
            [above],
            repetitions=3,
        )
        with self.assertRaisesRegex(
            ANALYZER.AnalysisError, "completed but peak_rss_mib"
        ):
            ANALYZER.analyze_sessions(
                [contradictory], bootstrap_samples=10_000
            )

        cooldown = self.factory.create(
            "short-idle-cooldown",
            "timing",
            [self.open_seed_1],
            repetitions=3,
        )
        manifest_path = cooldown / "run_manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["idle_cooldown"]["elapsed_seconds"] = 299.0
        write_json(manifest_path, manifest)
        with self.assertRaisesRegex(
            ANALYZER.AnalysisError, "idle_cooldown.valid"
        ):
            ANALYZER.analyze_sessions(
                [cooldown], bootstrap_samples=10_000
            )

    def test_local_plan_host_probes_and_session_controls_fail_closed(
        self,
    ) -> None:
        bad_probe = self.factory.host_snapshot(self.root)
        bad_probe["power_probe"]["returncode"] = 1
        with self.assertRaisesRegex(
            ANALYZER.AnalysisError, "power_probe.valid"
        ):
            ANALYZER._validate_host_snapshot(bad_probe, "bad host")

        local_copy = self.factory.create(
            "noncanonical-local-plan",
            "timing",
            [self.open_seed_1],
            repetitions=3,
        )
        local_plan = local_copy / "frozen_plan.json"
        local_plan.write_bytes(local_plan.read_bytes() + b" ")
        with self.assertRaisesRegex(
            ANALYZER.AnalysisError, "not canonical JSON"
        ):
            ANALYZER.analyze_sessions(
                [local_copy], bootstrap_samples=10_000
            )

        controls = self.factory.create(
            "session-control-mismatch",
            "timing",
            [self.open_seed_1],
            repetitions=3,
        )
        session_path = controls / "session.json"
        session = json.loads(session_path.read_text(encoding="utf-8"))
        session["minimum_free_disk_bytes"] = 1
        write_json(session_path, session)
        with self.assertRaisesRegex(
            ANALYZER.AnalysisError, "minimum_free_disk_bytes"
        ):
            ANALYZER.analyze_sessions(
                [controls], bootstrap_samples=10_000
            )

        contamination = self.factory.create(
            "host-contamination-mismatch",
            "timing",
            [self.open_seed_1],
            repetitions=3,
        )
        session_path = contamination / "session.json"
        session = json.loads(session_path.read_text(encoding="utf-8"))
        session["host_contaminated_blocks"] = ["invented-block"]
        write_json(session_path, session)
        with self.assertRaisesRegex(
            ANALYZER.AnalysisError, "host_contaminated_blocks"
        ):
            ANALYZER.analyze_sessions(
                [contamination], bootstrap_samples=10_000
            )

    def test_holm_mcnemar_and_rmst_helpers(self) -> None:
        p_values = {
            "open": 0.001,
            "perfect_maze": 0.01,
            "fractal_low": 0.02,
            "fractal_medium": 0.03,
            "fractal_high": 0.04,
        }
        by_environment = {
            environment: {
                "metrics": {
                    "total_cpu_seconds": {
                        "one_sided_exact_map_seed_sign_flip_p_value": value
                    }
                }
            }
            for environment, value in p_values.items()
        }
        holm = ANALYZER._holm_environment_compute_tests(by_environment)
        self.assertTrue(holm["available"])
        self.assertAlmostEqual(
            holm["tests"]["open"]["holm_adjusted_p_value"], 0.005
        )
        self.assertAlmostEqual(
            holm["tests"]["perfect_maze"]["holm_adjusted_p_value"], 0.04
        )
        self.assertFalse(holm["all_five_reject"])

        binary_rows = [
            {
                "environment": "open",
                "map_sha256": sha("a"),
                "seed": seed,
                "reference": reference,
                "optimized": optimized,
            }
            for seed, reference, optimized in (
                (1, False, True),
                (2, False, True),
                (3, False, True),
                (4, True, False),
            )
        ]
        binary = ANALYZER._paired_binary_summary(
            binary_rows, "binary-helper-test", 100
        )
        self.assertEqual(binary["discordant_pair_count"], 4)
        self.assertAlmostEqual(
            binary["exact_two_sided_mcnemar_p_value"], 0.625
        )
        self.assertAlmostEqual(
            binary["optimized_minus_reference_risk_difference"], 0.5
        )

        rmst_rows = [
            {
                "environment": "open",
                "map_sha256": sha("b"),
                "seed": seed,
                "reference_time_seconds": reference,
                "optimized_time_seconds": optimized,
                "reference_event": reference_event,
                "optimized_event": optimized_event,
            }
            for seed, reference, optimized, reference_event, optimized_event in (
                (1, 10.0, 8.0, True, True),
                (2, 10.0, 10.0, False, False),
            )
        ]
        rmst = ANALYZER._restricted_mean_summary(
            rmst_rows, "rmst-helper-test", 100
        )
        self.assertAlmostEqual(rmst["reference_rmst_seconds"], 10.0)
        self.assertAlmostEqual(rmst["optimized_rmst_seconds"], 9.0)
        self.assertAlmostEqual(
            rmst["optimized_minus_reference_rmst_seconds"], -1.0
        )
        wrong_tau = self.factory._summary(
            {"implementation_id": "reference"},
            2,
            trace_detail="digest",
        )
        wrong_tau["simulated_time_seconds"] = 9.0
        with self.assertRaisesRegex(
            ANALYZER.AnalysisError, "frozen ceiling"
        ):
            ANALYZER._restricted_time_observation(
                wrong_tau, "wrong-tau", 100, 0.1
            )

        selected, successor = ANALYZER._select_calibration_candidate(
            [2, 4, 8],
            2,
            {
                "2": {"all_gates_pass": False},
                "4": {
                    "all_gates_pass": True,
                    "cpu_budget_pass": True,
                    "task_validity_pass": True,
                },
                "8": {
                    "all_gates_pass": False,
                    "cpu_budget_pass": True,
                    "task_validity_pass": False,
                },
            },
        )
        self.assertEqual(selected, 4)
        self.assertEqual(successor, 8)

        selected, successor = ANALYZER._select_calibration_candidate(
            [2, 4, 8],
            2,
            {
                "2": {"all_gates_pass": True},
                "4": {"all_gates_pass": False},
                "8": {"all_gates_pass": False},
            },
        )
        self.assertEqual(selected, 2)
        self.assertEqual(successor, 4)

        selected, successor = ANALYZER._select_calibration_candidate(
            [2, 4, 8],
            2,
            {
                "2": {"all_gates_pass": True},
                "4": {"all_gates_pass": True},
                "8": {"all_gates_pass": True},
            },
        )
        self.assertEqual(selected, 8)
        self.assertIsNone(successor)
        self.assertEqual(
            ANALYZER._capacity_phase_outcomes("audit", True),
            {
                "validation_phase_gate": False,
                "complete_evidence_without_phase_gate": True,
                "audit_reproduction_ready": True,
                "validated_capacity_claim_ready": False,
            },
        )
        self.assertTrue(
            ANALYZER._capacity_phase_outcomes("validation", True)[
                "validated_capacity_claim_ready"
            ]
        )

        full_fidelity_pairs = [
            {
                "environment": "open",
                "seed": seed,
                "robot_count": count,
            }
            for seed in range(1, 11)
            for count in (2, 4)
        ]
        full_fidelity_runs = [
            {
                "attempt_kind": "measured",
                "terminal_category": "completed",
                "environment": "open",
                "robot_count": pair["robot_count"],
                "resource": {"wall_seconds": 2.0},
            }
            for pair in full_fidelity_pairs
        ]
        timing_wall_session = {
            "session_id": "timing-wall-source",
            "manifest": {"phase": "calibration", "mode": "timing"},
            "pairs": [],
            "runs": [
                {
                    "attempt_kind": "measured",
                    "terminal_category": "completed",
                    "environment": "open",
                    "robot_count": 2,
                    "resource": {"wall_seconds": 1.0},
                },
                {
                    "attempt_kind": "measured",
                    "terminal_category": "completed",
                    "environment": "open",
                    "robot_count": 8,
                    "resource": {"wall_seconds": 99.0},
                },
            ],
        }
        full_fidelity_session = {
            "session_id": "full-fidelity-wall-source",
            "manifest": {"phase": "calibration", "mode": "fidelity"},
            "pairs": full_fidelity_pairs,
            "runs": full_fidelity_runs,
        }
        partial_fidelity_session = {
            "session_id": "partial-fidelity-wall-source",
            "manifest": {"phase": "calibration", "mode": "fidelity"},
            "pairs": full_fidelity_pairs[:-1],
            "runs": [
                {
                    "attempt_kind": "measured",
                    "terminal_category": "completed",
                    "environment": "open",
                    "robot_count": 2,
                    "resource": {"wall_seconds": 88.0},
                }
            ],
        }
        walls = ANALYZER._calibration_completed_wall_seconds(
            timing_wall_session,
            [
                timing_wall_session,
                full_fidelity_session,
                partial_fidelity_session,
            ],
            environment="open",
            grid=[2, 4],
        )
        self.assertEqual(walls, [1.0] + [2.0] * 20)

        anchor_evaluation = {
            "environment": "open",
            "role": "timing-grid",
            "reference_anchor_n": 2,
            "candidate_n_star": 2,
            "next_grid_point": 4,
            "grid": [2, 4, 8],
        }
        self.assertEqual(
            ANALYZER._validate_capacity_evaluation_record(
                anchor_evaluation,
                "anchor evaluation",
                phase="validation",
                mode="timing",
            ),
            anchor_evaluation,
        )
        ceiling_evaluation = {
            **anchor_evaluation,
            "candidate_n_star": 8,
            "next_grid_point": None,
        }
        self.assertEqual(
            ANALYZER._validate_capacity_evaluation_record(
                ceiling_evaluation,
                "ceiling evaluation",
                phase="audit",
                mode="timing",
            ),
            ceiling_evaluation,
        )
        duplicate_role_session = {
            "session_id": "duplicate-role",
            "frozen_plan": {
                "capacity_evaluation": anchor_evaluation,
            },
        }
        with self.assertRaisesRegex(
            ANALYZER.AnalysisError, "duplicate role sessions"
        ):
            ANALYZER._capacity_evaluation_record(
                "open",
                [duplicate_role_session, duplicate_role_session],
                [],
                [],
                10_000,
            )

    def test_overall_sign_flip_is_explicitly_unavailable_above_20_clusters(
        self,
    ) -> None:
        rows = []
        for seed in range(1, 22):
            reference = {
                "total_cpu_seconds": 2.0,
                "wall_seconds": 2.0,
                "peak_rss_mib": 2.0,
                "cpu_seconds_per_robot_timestep": 2.0,
                "robot_timesteps_per_cpu_second": 2.0,
            }
            optimized = {
                "total_cpu_seconds": 1.0,
                "wall_seconds": 1.0,
                "peak_rss_mib": 1.0,
                "cpu_seconds_per_robot_timestep": 1.0,
                "robot_timesteps_per_cpu_second": 1.0,
            }
            rows.append(
                {
                    "environment": f"environment-{seed % 5}",
                    "map_sha256": hashlib.sha256(
                        f"map-{seed}".encode()
                    ).hexdigest(),
                    "seed": seed,
                    "effects": ANALYZER._metric_values(
                        reference, optimized
                    ),
                }
            )
        summary = ANALYZER._effect_summary(rows, "more-than-20", 100)
        metric = summary["metrics"]["total_cpu_seconds"]
        self.assertIsNone(
            metric["one_sided_exact_map_seed_sign_flip_p_value"]
        )
        self.assertIn("20 independent", metric["sign_flip_availability_reason"])

    def test_validation_capacity_evaluates_preselected_candidate(self) -> None:
        timing_conditions = []
        fidelity_conditions = []
        for seed in range(11, 31):
            for count in (2, 4, 8):
                pair_id = f"open-validation-seed-{seed}-n-{count}"
                common = (
                    pair_id,
                    "open",
                    seed,
                    count,
                    "validation-open",
                    f"validation-seed-{seed}-n-{count}",
                )
                timing_conditions.append(
                    self.factory.condition(
                        *common,
                        [10.0, 10.0, 10.0],
                        [5.0, 5.0, 5.0],
                    )
                )
                fidelity_conditions.append(
                    self.factory.condition(
                        *common,
                        [1.0],
                        [1.0],
                        reference_wall=[1.0],
                        optimized_wall=[1.0],
                        reference_rss=[10.0],
                        optimized_rss=[10.0],
                    )
                )
        common_evaluation = {
            "environment": "open",
            "reference_anchor_n": 2,
            "candidate_n_star": 4,
            "next_grid_point": 8,
            "grid": [2, 4, 8],
        }
        timing = self.factory.create(
            "validation-capacity-timing",
            "timing",
            timing_conditions,
            repetitions=3,
            phase="validation",
            capacity_evaluation={
                **common_evaluation,
                "role": "timing-grid",
            },
        )
        fidelity = self.factory.create(
            "validation-capacity-fidelity",
            "fidelity",
            fidelity_conditions,
            repetitions=1,
            phase="validation",
            capacity_evaluation={
                **common_evaluation,
                "role": "fidelity-grid",
            },
        )
        result = ANALYZER.analyze_sessions(
            [timing, fidelity], bootstrap_samples=10_000
        )
        evaluation = result["capacity"]["capacity_evaluations"]["open"]
        self.assertEqual(evaluation["candidate_n_star"], 4)
        self.assertEqual(evaluation["next_grid_point"], 8)
        self.assertEqual(
            evaluation["evidence_gates"]["observed_seeds"],
            list(range(11, 31)),
        )
        self.assertTrue(
            evaluation["evidence_gates"][
                "validated_capacity_claim_ready"
            ]
        )

        analysis_sha = digest(Path(ANALYZER.__file__).resolve())
        timing_session = ANALYZER._validate_session(timing, analysis_sha)
        fidelity_session = ANALYZER._validate_session(
            fidelity, analysis_sha
        )
        different_decision = copy.deepcopy(fidelity_session)
        different_decision["frozen_plan"]["artifacts"][
            "capacity_decision"
        ]["sha256"] = sha("9")
        with self.assertRaisesRegex(
            ANALYZER.AnalysisError, "capacity_decision_sha256"
        ):
            ANALYZER._validate_cross_session_provenance(
                [timing_session, different_decision],
                analysis_sha,
            )
        timing_rows, _ = ANALYZER._collapse_timing([timing_session])
        noncandidate_discordance = copy.deepcopy(fidelity_session)
        discordant_pair = next(
            pair
            for pair in noncandidate_discordance["pairs"]
            if pair["robot_count"] == 8
        )
        discordant_pair["semantic_trace_equal"] = False
        discordant_pair["pair_valid"] = False
        discordant_evaluation = ANALYZER._capacity_evaluation_record(
            "open",
            [timing_session, noncandidate_discordance],
            [timing_session, noncandidate_discordance],
            timing_rows,
            10_000,
        )
        discordant_gates = discordant_evaluation["evidence_gates"]
        self.assertTrue(discordant_gates["same_n_star_fidelity_pass"])
        self.assertFalse(discordant_gates["full_grid_fidelity_pass"])
        self.assertFalse(
            discordant_gates["validated_capacity_claim_ready"]
        )
        self.assertIn(
            discordant_pair["block_id"],
            discordant_gates[
                "full_grid_discordant_or_invalid_pair_ids"
            ],
        )
        missing_role = ANALYZER._capacity_evaluation_record(
            "open",
            [timing_session],
            [timing_session],
            timing_rows,
            10_000,
        )
        self.assertFalse(
            missing_role["evidence_gates"][
                "validated_capacity_claim_ready"
            ]
        )
        self.assertFalse(
            missing_role["evidence_gates"]["complete_role_coverage"]
        )
        self.assertIn(
            "fidelity-grid",
            missing_role["evidence_gates"]["role_coverage_reason"],
        )

    def test_contact_clearance_fails_the_fixed_horizon_task_gate(self) -> None:
        summary = self.factory._summary(
            {"implementation_id": "reference"},
            2,
            trace_detail="digest",
        )
        summary["minimum_pair_clearance_m"] = 0.0
        observation = ANALYZER._summary_quality_observation(
            summary,
            "contact-summary",
            expected_robot_count=2,
            expected_final_tick=100,
        )
        self.assertFalse(observation["available"])
        self.assertIn("greater than zero", observation["reason"])


if __name__ == "__main__":
    unittest.main()
