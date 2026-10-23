from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from scripts.comparison import freeze_capacity_decision as DECISION
from scripts.comparison import run_paired as RUNNER


TRACE_SCHEMA_V2 = "dancers-comparison-trace-v2"
V2_DIGEST_STATE_SCHEMA = "robot-frame-v1"
V2_DIGEST_ALGORITHM = (
    "SHA-256 over canonical nlohmann JSON serialization of each "
    "implementation-neutral robot frame"
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(source: Path, *args: str, capture: bool = False) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source), *args],
        check=True,
        text=True,
        capture_output=capture,
    )
    return completed.stdout.strip() if capture else ""


class RunnerFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.ledger = root / "ledger.jsonl"
        self.lock = root / "pixi.lock"
        self.lock.write_text("version = 6\n", encoding="utf-8")
        self.reference = root / "reference"
        self.optimized = root / "optimized"
        self.reference_base = self._init_repo(self.reference, optimized=False)
        self.optimized_base = self._init_repo(self.optimized, optimized=True)
        self.patch = root / "common.patch"
        self._make_common_patch()
        self.reference_head, self.reference_tree = self._apply_patch(
            self.reference
        )
        self.optimized_head, self.optimized_tree = self._apply_patch(
            self.optimized
        )
        git(
            self.reference,
            "fetch",
            "-q",
            str(self.optimized),
            self.optimized_head,
        )
        self.template = root / "template.yaml"
        self.template.write_text(
            "\n".join(
                [
                    'SIMULATION_NAME: "template"',
                    "LOG_RESULTS: false",
                    'ASSETS_DIR: ""',
                    'OBSTACLE_FILE: ""',
                    "SCENARIO: file",
                    'SCENARIO_FILE: ""',
                    "NUM_ROBOTS: 1",
                    "COMMUNICATION_RADIUS: 20",
                    "RNG_SEED: 1",
                    "MAX_TIME: 1",
                    "TIMESTEP: 0.1",
                    "DISPLAY: 1",
                    "DRAW_INTERROBOT: 1",
                    "DRAW_PATH: 1",
                    "DRAW_TRAJ: 1",
                    "DRAW_WAYPOINTS: 1",
                    "NUM_ITERS: 2",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        self.protocol = root / "protocol.md"
        self.protocol.write_text(
            "# Frozen fake comparison protocol\n", encoding="utf-8"
        )
        self.experiment_manifest = root / "experiment-manifest.json"
        self.experiment_manifest.write_text(
            json.dumps(
                {
                    "schema_version": "fake-experiment-manifest-v1",
                    "frozen": True,
                    "hardware_versions": {
                        "power_requirement": "test AC power",
                        "thermal_policy": "test nominal thermal state",
                    },
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self.analysis_revision = root / "analysis_revision.py"
        self.analysis_revision.write_text(
            "ANALYSIS_REVISION = 1\n", encoding="utf-8"
        )
        self.environment_root = root / "environments"
        self.environment_root.mkdir()
        self.map = self.environment_root / "map.png"
        self.map.write_bytes(b"fake-map-bytes")
        self.environment_manifest = (
            self.environment_root / "manifest.json"
        )
        self.build_manifest = root / "comparison" / "build_manifest.json"
        self.build_manifest.parent.mkdir()
        self._write_binary(self.reference, "reference")
        self._write_binary(self.optimized, "optimized")

    def _init_repo(self, path: Path, *, optimized: bool) -> str:
        path.mkdir()
        git(path, "init", "-q")
        (path / ".gitignore").write_text("build/\n", encoding="utf-8")
        (path / "pixi.lock").write_bytes(self.lock.read_bytes())
        (path / "common.txt").write_text("before\n", encoding="utf-8")
        if optimized:
            (path / "optimized.txt").write_text(
                "optimized implementation\n", encoding="utf-8"
            )
        git(path, "add", ".")
        git(
            path,
            "-c",
            "user.name=Comparison Test",
            "-c",
            "user.email=comparison@example.invalid",
            "commit",
            "-q",
            "-m",
            "base",
        )
        return git(path, "rev-parse", "HEAD", capture=True)

    def _make_common_patch(self) -> None:
        common = self.reference / "common.txt"
        common.write_text("after\n", encoding="utf-8")
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(self.reference),
                "diff",
                "--binary",
                "--",
                "common.txt",
            ],
            check=True,
            capture_output=True,
        )
        self.patch.write_bytes(completed.stdout)
        git(self.reference, "restore", "common.txt")

    def _apply_patch(self, source: Path) -> tuple[str, str]:
        git(source, "apply", str(self.patch))
        git(source, "add", "common.txt")
        git(
            source,
            "-c",
            "user.name=Comparison Test",
            "-c",
            "user.email=comparison@example.invalid",
            "commit",
            "-q",
            "-m",
            "common correctness",
        )
        return (
            git(source, "rev-parse", "HEAD", capture=True),
            git(source, "rev-parse", "HEAD^{tree}", capture=True),
        )

    def _write_build_manifest(self, mode: str) -> None:
        builds = {}
        methods = {
            "reference": (
                self.reference,
                self.reference_base,
                self.reference_head,
            ),
            "optimized": (
                self.optimized,
                self.optimized_base,
                self.optimized_head,
            ),
        }
        for method, (source, base, head) in methods.items():
            build_dir = source / "build"
            auxiliaries = {
                "CMakeCache.txt": f"{method} fake cache\n",
                "compile_commands.json": "[]\n",
                "build.log": f"{method} clean fake build\n",
            }
            for filename, contents in auxiliaries.items():
                (build_dir / filename).write_text(
                    contents, encoding="utf-8"
                )
            binary = build_dir / "fake_dancers"
            for build_mode in ("timing", "fidelity"):
                builds[f"{method}_{build_mode}"] = {
                    "algorithm_revision": base,
                    "applied_head": head,
                    "observer_compiled": build_mode == "fidelity",
                    "binary_path": str(binary.relative_to(self.root)),
                    "binary_sha256": digest(binary),
                    "cmake_cache_sha256": digest(
                        build_dir / "CMakeCache.txt"
                    ),
                    "compile_commands_sha256": digest(
                        build_dir / "compile_commands.json"
                    ),
                    "clean_build_log_sha256": digest(
                        build_dir / "build.log"
                    ),
                    "registered_tests": "3/3 passed",
                }

        completed = subprocess.run(
            [
                "git",
                "-C",
                str(self.optimized),
                "diff",
                "--full-index",
                f"{self.optimized_head}^",
                self.optimized_head,
            ],
            check=True,
            capture_output=True,
        )
        manifest = {
            "schema_version": RUNNER.BUILD_MANIFEST_SCHEMA,
            "status": "dual-build-parity-passed-smoke-fidelity-only",
            "repository": {
                "reference_pin": self.reference_base,
                "optimized_pin": self.optimized_base,
                "common_patch_commit": self.optimized_head,
                "common_patch_format_email_sha256": digest(self.patch),
                "common_patch_full_index_diff_sha256": hashlib.sha256(
                    completed.stdout
                ).hexdigest(),
                "reference_applied_head": self.reference_head,
                "reference_tree": self.reference_tree,
                "optimized_applied_head": self.optimized_head,
                "optimized_tree": self.optimized_tree,
                "patched_cross_tree_difference": {
                    "status": "pass",
                    "path_count": 1,
                    "paths": ["optimized.txt"],
                },
            },
            "toolchain": {
                "pixi_lock_sha256": digest(self.lock),
            },
            "builds": builds,
            "gates": {
                "dual_clean_builds": "pass",
                "common_registered_tests_all_four_builds": "pass",
                "timing_observer_compile_absence": "pass",
                "smoke_semantic_trace_identity": "pass",
                "calibration_permitted": True,
                "validation_permitted": True,
                "audit_permitted": True,
            },
        }
        self.build_manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def _write_environment_manifest(
        self, scenarios: list[tuple[int, Path]]
    ) -> None:
        by_count = {}
        for count, path in scenarios:
            by_count.setdefault(
                count,
                {
                    "robot_count": count,
                    "bytes": path.stat().st_size,
                    "sha256": digest(path),
                },
            )
        first_path = scenarios[0][1]
        registered_scenario = (
            self.environment_root / "registered-scenario.json"
        )
        registered_scenario.write_bytes(first_path.read_bytes())
        bank = self.environment_root / "bank.json"
        bank.write_text(
            json.dumps(
                {"metadata": {"capacity_bank_id": "fake-bank"}},
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        case = {
            "family": "open",
            "map": {
                "path": "map.png",
                "bytes": self.map.stat().st_size,
                "sha256": digest(self.map),
            },
            "scenario": {
                "path": "registered-scenario.json",
                "bytes": registered_scenario.stat().st_size,
                "sha256": digest(registered_scenario),
            },
            "capacity": {
                "bank_id": "fake-bank",
                "bank": {
                    "path": "bank.json",
                    "bytes": bank.stat().st_size,
                    "sha256": digest(bank),
                },
                "prefix_scenarios": list(by_count.values()),
            },
        }
        manifest = {
            "schema_version": 1,
            "artifact_hash": "SHA-256",
            "families": ["open"],
            "splits": {
                "calibration": {},
                "validation": {},
            },
            "environments": [
                {**case, "split": "calibration"},
                {**case, "split": "validation"},
            ],
        }
        self.environment_manifest.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (self.environment_root / "manifest.sha256").write_text(
            f"{digest(self.environment_manifest)}  manifest.json\n",
            encoding="ascii",
        )

    def _write_binary(
        self,
        source: Path,
        method: str,
        *,
        fidelity: bool = False,
        exit_code: int = 0,
        sleep_seconds: float = 0.0,
        cpu_by_n: dict[int, float] | None = None,
        different_trace: bool = False,
        trace_schema: str = RUNNER.TRACE_SCHEMA,
    ) -> Path:
        binary = source / "build" / "fake_dancers"
        binary.parent.mkdir(parents=True, exist_ok=True)
        marker = "# DANCERS_TRACE_OUT\n" if fidelity else ""
        fidelity_body = ""
        if fidelity:
            if trace_schema == RUNNER.TRACE_SCHEMA:
                digest_state_schema = RUNNER.TRACE_DIGEST_STATE_SCHEMA
                digest_algorithm = RUNNER.TRACE_DIGEST_ALGORITHM
            elif trace_schema == TRACE_SCHEMA_V2:
                digest_state_schema = V2_DIGEST_STATE_SCHEMA
                digest_algorithm = V2_DIGEST_ALGORITHM
            else:
                raise ValueError(
                    f"unsupported fake trace schema {trace_schema!r}"
                )
            fidelity_body = (
                f"TRACE_SCHEMA = {trace_schema!r}\n"
                f"DIGEST_STATE_SCHEMA = {digest_state_schema!r}\n"
                f"DIGEST_ALGORITHM = {digest_algorithm!r}\n"
            ) + """
metadata = json.loads(Path(os.environ["DANCERS_RUN_METADATA"]).read_text())
trace_path = Path(os.environ["DANCERS_TRACE_OUT"])
summary_path = Path(os.environ["DANCERS_SUMMARY_OUT"])
character = "c" if DIFFERENT_TRACE else "a"
header = {
    "type": "header",
    "schema_version": TRACE_SCHEMA,
    "metadata": metadata,
    "resolved_config": {
        "config_file": str(cfg),
        "scenario_file": "comparison/calibration/scenario.json",
        "obstacle_file": "comparison/calibration/map.png",
        "world_size_m": 100,
        "rng_seed": seed,
    },
    "observer": {
        "trace_detail": "digest",
        "timing_eligible": False,
        "digest_algorithm": DIGEST_ALGORITHM,
        "future_state_scope": "fixture common future-affecting state",
        "message_checksum_scope": "fixture common live-message state",
    },
}
robot = {
    "rid": 0,
    "state_schema": DIGEST_STATE_SCHEMA,
    "state_sha256": character * 64,
}
frames = []
for tick in range(100):
    frames.append({
        "type": "frame",
        "tick": tick,
        "phase": "pre_update",
        "robots": [robot],
    })
    frames.append({
        "type": "frame",
        "tick": tick + 1,
        "phase": "post_update",
        "robots": [robot],
    })
trace_path.write_text(
    "".join(json.dumps(item) + "\\n" for item in [header, *frames]),
    encoding="utf-8",
)
summary = {
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
    "all_goals_reached": False,
    "goals_reached": 0,
    "goals_total": robot_count,
    "first_all_goals_tick": None,
    "iterations": 200,
    "total_path_length_m": float(robot_count),
    "mean_path_length_m_per_robot": 1.0,
    "per_robot_goal_tick": {},
    "per_robot_path_length_m": {
        str(index): 1.0 for index in range(robot_count)
    },
    "obstacle_collision_events": 0,
    "obstacle_collision_exposure_seconds": 0.0,
    "pair_collision_events": 0,
    "pair_collision_exposure_seconds": 0.0,
    "minimum_obstacle_clearance_m": 1.0,
    "minimum_pair_clearance_m": 1.0,
    "metadata": metadata,
    "resolved_config": {
        "max_timesteps": 100,
        "configured_robot_count": robot_count,
        "rng_seed": seed,
        "display": False,
    },
    "paper_metrics": {
        "consensus_reached": 0,
        "final_connectivity": 0.0,
        "final_decision": -1,
        "final_heading_error": 0.0,
        "final_iterations": 0,
        "final_position_error": 0.0,
    },
}
summary_path.write_text(
    json.dumps(summary) + "\\n",
    encoding="utf-8",
)
"""
        script = f"""#!/usr/bin/env python3
{marker}import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

METHOD = {method!r}
LEDGER = Path({str(self.ledger)!r})
EXIT_CODE = {exit_code}
SLEEP_SECONDS = {sleep_seconds!r}
CPU_BY_N = {cpu_by_n or {}!r}
DIFFERENT_TRACE = {different_trace!r}
cfg = Path(sys.argv[sys.argv.index("--cfg") + 1]).resolve()
config_bytes = cfg.read_bytes()
config_text = config_bytes.decode("utf-8")
robot_count = int(re.search(r"(?m)^NUM_ROBOTS:\\s*(\\d+)\\s*$", config_text).group(1))
seed = int(re.search(r"(?m)^RNG_SEED:\\s*(\\d+)\\s*$", config_text).group(1))
observer_keys = [
    "DANCERS_" + "TRACE_OUT",
    "DANCERS_" + "SUMMARY_OUT",
    "DANCERS_" + "RUN_METADATA",
    "DANCERS_" + "REQUIRE_PROVENANCE",
    "DANCERS_" + "TRACE_DETAIL",
]
if {not fidelity!r} and any(key in os.environ for key in observer_keys):
    raise SystemExit(99)
start = time.monotonic_ns()
with LEDGER.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({{
        "event": "start",
        "method": METHOD,
        "time_ns": start,
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "robot_count": robot_count,
    }}, sort_keys=True) + "\\n")
if SLEEP_SECONDS:
    time.sleep(SLEEP_SECONDS)
cpu_seconds = CPU_BY_N.get(robot_count, 0.0)
cpu_deadline = time.process_time() + cpu_seconds
while time.process_time() < cpu_deadline:
    pass
{fidelity_body}
end = time.monotonic_ns()
with LEDGER.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({{
        "event": "end",
        "method": METHOD,
        "time_ns": end,
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "robot_count": robot_count,
    }}, sort_keys=True) + "\\n")
print("timesteps: 100 | fake")
print(f"Final robot count: [{{robot_count}}]")
raise SystemExit(EXIT_CODE)
"""
        binary.write_text(script, encoding="utf-8")
        binary.chmod(0o755)
        return binary

    def set_binaries(
        self,
        *,
        fidelity: bool = False,
        reference_exit: int = 0,
        optimized_exit: int = 0,
        reference_sleep: float = 0.0,
        optimized_sleep: float = 0.0,
        reference_cpu: dict[int, float] | None = None,
        optimized_cpu: dict[int, float] | None = None,
        optimized_trace_differs: bool = False,
        fidelity_trace_schema: str = RUNNER.TRACE_SCHEMA,
    ) -> None:
        self._write_binary(
            self.reference,
            "reference",
            fidelity=fidelity,
            exit_code=reference_exit,
            sleep_seconds=reference_sleep,
            cpu_by_n=reference_cpu,
            trace_schema=fidelity_trace_schema,
        )
        self._write_binary(
            self.optimized,
            "optimized",
            fidelity=fidelity,
            exit_code=optimized_exit,
            sleep_seconds=optimized_sleep,
            cpu_by_n=optimized_cpu,
            different_trace=optimized_trace_differs,
            trace_schema=fidelity_trace_schema,
        )

    def scenario(
        self,
        *,
        environment: str,
        phase: str,
        robot_count: int,
        suffix: str,
    ) -> Path:
        path = self.root / f"scenario-{suffix}.json"
        split = "validation" if phase == "audit" else phase
        path.write_text(
            json.dumps(
                {
                    "metadata": {
                        "environment_id": environment,
                        "split": split,
                        "map_sha256": digest(self.map),
                        "prefix_robot_count": robot_count,
                        "capacity_bank_id": "fake-bank",
                    },
                    "num_robots": robot_count,
                    "decisions": {
                        str(index): index % 10
                        for index in range(robot_count)
                    },
                    "waypoints": {
                        str(index): [[0.0, 0.0], [1.0, 1.0]]
                        for index in range(robot_count)
                    },
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def make_plan(
        self,
        *,
        phase: str = "calibration",
        mode: str = "timing",
        pair_specs: list[tuple[str, int, int]] | None = None,
        repetitions: int | None = None,
        timeout: float = 2.0,
        capacity: dict | None = None,
        capacity_evaluation: dict | None = None,
    ) -> Path:
        pair_specs = pair_specs or [("open", 1, 2)]
        self._write_build_manifest(mode)
        pairs = []
        registered_scenarios: list[tuple[int, Path]] = []
        for index, (environment, seed, robot_count) in enumerate(pair_specs):
            scenario = self.scenario(
                environment=environment,
                phase=phase,
                robot_count=robot_count,
                suffix=f"{phase}-{index}-{robot_count}",
            )
            registered_scenarios.append((robot_count, scenario))
            pairs.append(
                {
                    "pair_id": f"{environment}-seed-{seed}-n-{robot_count}",
                    "environment": environment,
                    "seed": seed,
                    "robot_count": robot_count,
                    "map": {
                        "path": str(self.map),
                        "sha256": digest(self.map),
                    },
                    "scenario": {
                        "path": str(scenario),
                        "sha256": digest(scenario),
                    },
                }
            )
        self._write_environment_manifest(registered_scenarios)
        if repetitions is None:
            repetitions = 3 if mode == "timing" else 1
        plan = {
            "schema_version": RUNNER.PLAN_SCHEMA,
            "phase": phase,
            "mode": mode,
            "fixed_work_timesteps": 100,
            "timestep_seconds": 0.1,
            "idle_cooldown_seconds": 300,
            "timeout_seconds": timeout,
            "termination_grace_seconds": 0.1,
            "repetitions": repetitions,
            "warmups_per_binary_map": 1,
            "omp_threads": 1,
            "order_seed": 12345,
            "minimum_free_disk_bytes": RUNNER.MINIMUM_FREE_DISK_BYTES,
            "maximum_peak_rss_mib": RUNNER.MAXIMUM_PEAK_RSS_MIB,
            "power_mode": "test AC power",
            "thermal_policy": "test nominal thermal state",
            "task_validity_predicate_id": (
                RUNNER.TASK_VALIDITY_PREDICATE_ID
            ),
            "runner_sha256": digest(Path(RUNNER.__file__).resolve()),
            "trace_detail": "digest",
            "run_cwd": str(self.root),
            "artifacts": {
                "pixi_lock": {
                    "path": str(self.lock),
                    "sha256": digest(self.lock),
                },
                "build_manifest": {
                    "path": str(self.build_manifest),
                    "sha256": digest(self.build_manifest),
                },
                "environment_manifest": {
                    "path": str(self.environment_manifest),
                    "sha256": digest(self.environment_manifest),
                },
                "config_template": {
                    "path": str(self.template),
                    "sha256": digest(self.template),
                },
                "protocol": {
                    "path": str(self.protocol),
                    "sha256": digest(self.protocol),
                },
                "experiment_manifest": {
                    "path": str(self.experiment_manifest),
                    "sha256": digest(self.experiment_manifest),
                },
                "analysis_revision": {
                    "path": str(self.analysis_revision),
                    "sha256": digest(self.analysis_revision),
                },
            },
            "methods": {
                "reference": {
                    "source_dir": str(self.reference),
                    "base_revision": self.reference_base,
                    "head_revision": self.reference_head,
                    "tree_hash": self.reference_tree,
                    "binary": {
                        "path": str(
                            self.reference / "build" / "fake_dancers"
                        ),
                        "sha256": digest(
                            self.reference / "build" / "fake_dancers"
                        ),
                    },
                },
                "optimized": {
                    "source_dir": str(self.optimized),
                    "base_revision": self.optimized_base,
                    "head_revision": self.optimized_head,
                    "tree_hash": self.optimized_tree,
                    "binary": {
                        "path": str(
                            self.optimized / "build" / "fake_dancers"
                        ),
                        "sha256": digest(
                            self.optimized / "build" / "fake_dancers"
                        ),
                    },
                },
            },
            "pairs": pairs,
        }
        if capacity is not None:
            plan["capacity_search"] = capacity
        if capacity_evaluation is not None:
            decision_path = (
                self.root / f"capacity-decision-{phase}-{mode}.json"
            )
            environments = []
            timeout_derivation = {}
            for family in DECISION.FAMILIES:
                if family == capacity_evaluation["environment"]:
                    grid = capacity_evaluation["grid"]
                    anchor = capacity_evaluation["reference_anchor_n"]
                    candidate = capacity_evaluation["candidate_n_star"]
                    next_point = capacity_evaluation["next_grid_point"]
                else:
                    grid = [2, 4]
                    anchor = 2
                    candidate = 2
                    next_point = 4
                environments.append(
                    {
                        "environment": family,
                        "reference_anchor_n": anchor,
                        "grid": grid,
                        "candidate_n_star": candidate,
                        "next_grid_point": next_point,
                        "validation_timing_counts": grid,
                        "validation_fidelity_counts": grid,
                        "validation_timeout_seconds": timeout,
                    }
                )
                timeout_derivation[family] = {
                    "calibration_plan_timeout_seconds": timeout,
                    "maximum_completed_measured_wall_seconds": 1.0,
                    "three_times_wall_ceiled_to_ten_seconds": 10.0,
                }
            provenance_hash = digest(self.analysis_revision)
            capacity_decision = {
                "schema_version": DECISION.DECISION_SCHEMA,
                "status": DECISION.DECISION_STATUS,
                "selection_rule_id": DECISION.SELECTION_RULE_ID,
                "provenance": {
                    "calibration_analysis": {
                        "path": str(self.analysis_revision.resolve()),
                        "sha256": provenance_hash,
                    },
                    "analyzer": {
                        "path": str(self.analysis_revision.resolve()),
                        "sha256": provenance_hash,
                    },
                    "experiment_manifest": {
                        "path": str(self.experiment_manifest.resolve()),
                        "sha256": digest(self.experiment_manifest),
                    },
                    "environment_manifest": {
                        "path": str(self.environment_manifest.resolve()),
                        "sha256": digest(self.environment_manifest),
                    },
                    "calibration_sessions": [
                        {
                            "session_id": "fixture-calibration",
                            "path": str(self.root.resolve()),
                            "mode": "timing",
                            "hashes": {
                                "session_json_sha256": provenance_hash,
                                "run_manifest_json_sha256": provenance_hash,
                                "frozen_plan_sha256": provenance_hash,
                                "runs_jsonl_sha256": provenance_hash,
                                "pairs_jsonl_sha256": provenance_hash,
                                "capacity_json_sha256": provenance_hash,
                            },
                        }
                    ],
                    "timeout_derivation": timeout_derivation,
                },
                "seeds": {
                    "validation": list(
                        RUNNER.PHASE_SEEDS["validation"]
                    ),
                    "audit": list(RUNNER.PHASE_SEEDS["audit"]),
                },
                "environments": environments,
            }
            decision_path.write_bytes(
                RUNNER.canonical_json_bytes(capacity_decision)
            )
            plan["artifacts"]["capacity_decision"] = {
                "path": str(decision_path),
                "sha256": digest(decision_path),
            }
            plan["capacity_evaluation"] = capacity_evaluation
        path = self.root / f"plan-{phase}-{mode}.json"
        path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return path

    @contextmanager
    def pinned(self):
        with mock.patch.object(
            RUNNER, "REFERENCE_PIN", self.reference_base
        ), mock.patch.object(RUNNER, "OPTIMIZED_PIN", self.optimized_base):
            yield

    def load(self, path: Path) -> RUNNER.Plan:
        with self.pinned():
            return RUNNER.load_plan(path)

    def events(self) -> list[dict]:
        if not self.ledger.exists():
            return []
        return [
            json.loads(line)
            for line in self.ledger.read_text(encoding="utf-8").splitlines()
        ]


class PairedRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        def controlled_host(
            plan: RUNNER.Plan, filesystem_path: Path
        ) -> dict:
            return {
                "checked_utc": "2026-07-28T00:00:00Z",
                "filesystem_path": str(filesystem_path),
                "free_disk_bytes": 10**12,
                "minimum_free_disk_bytes": (
                    plan.minimum_free_disk_bytes
                ),
                "disk_valid": True,
                "power_mode": plan.power_mode,
                "power_probe": {
                    "returncode": 0,
                    "stdout": "Now drawing from 'AC Power'\n",
                    "stderr": "",
                    "source": "AC Power",
                    "valid": True,
                },
                "thermal_policy": plan.thermal_policy,
                "thermal_probe": {
                    "returncode": 0,
                    "stdout": "0\n",
                    "stderr": "",
                    "state": 0,
                    "valid": True,
                },
                "valid": True,
            }

        patcher = mock.patch.object(
            RUNNER,
            "_host_condition_snapshot",
            side_effect=controlled_host,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        controls_patcher = mock.patch.object(
            RUNNER, "_validate_experiment_controls"
        )
        controls_patcher.start()
        self.addCleanup(controls_patcher.stop)
        cooldown_clock = [0.0]

        def advance_cooldown(seconds: float) -> None:
            cooldown_clock[0] += seconds

        sleep_patcher = mock.patch.object(
            RUNNER, "_cooldown_sleep", side_effect=advance_cooldown
        )
        clock_patcher = mock.patch.object(
            RUNNER, "_cooldown_clock", side_effect=lambda: cooldown_clock[0]
        )
        self.cooldown_sleep = sleep_patcher.start()
        self.cooldown_clock = clock_patcher.start()
        self.addCleanup(sleep_patcher.stop)
        self.addCleanup(clock_patcher.stop)

    def test_serial_ab_ba_order_identical_configs_and_machine_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            plan_path = fixture.make_plan(
                pair_specs=[("open", 1, 2), ("open", 2, 2)]
            )
            plan = fixture.load(plan_path)
            output = fixture.root / "output"
            poisoned = {
                "DANCERS_TRACE_OUT": "must-be-cleared",
                "DANCERS_SUMMARY_OUT": "must-be-cleared",
            }
            with mock.patch.dict(os.environ, poisoned, clear=False):
                session = RUNNER.execute(plan, output)

            self.assertTrue(session["all_pairs_valid"])
            self.assertTrue(session["warmups_valid"])
            self.assertEqual(session["warmup_attempt_count"], 2)
            events = fixture.events()
            starts = [item for item in events if item["event"] == "start"]
            run_manifest = json.loads(
                (output / "run_manifest.json").read_text(encoding="utf-8")
            )
            expected_methods = ["reference", "optimized"]
            for block in run_manifest["blocks"]:
                expected_methods.extend(
                    ["reference", "optimized"]
                    if block["order"] == "AB"
                    else ["optimized", "reference"]
                )
            self.assertEqual(
                [item["method"] for item in starts],
                expected_methods,
            )
            self.assertEqual(
                sorted(block["order"] for block in run_manifest["blocks"]),
                ["AB", "AB", "AB", "BA", "BA", "BA"],
            )
            for left, right in zip(events[1::2], events[2::2]):
                self.assertEqual(left["event"], "end")
                self.assertEqual(right["event"], "start")
                self.assertLessEqual(left["time_ns"], right["time_ns"])
            for index in range(0, len(starts), 2):
                self.assertEqual(
                    starts[index]["config_sha256"],
                    starts[index + 1]["config_sha256"],
                )

            with (output / "runs.csv").open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            jsonl = [
                json.loads(line)
                for line in (output / "runs.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(len(rows), 14)
            self.assertEqual(len(jsonl), 14)
            self.assertEqual(
                [item["attempt_kind"] for item in jsonl[:2]],
                ["warmup", "warmup"],
            )
            self.assertTrue(
                all(
                    item["attempt_kind"] == "measured"
                    for item in jsonl[2:]
                )
            )
            self.assertTrue(
                all(item["terminal_category"] == "completed" for item in jsonl)
            )
            self.assertTrue(
                all(item["resource"]["peak_rss_mib"] >= 0 for item in jsonl)
            )
            self.assertFalse(os.access(output, os.W_OK))
            self.assertEqual(
                digest(output / "frozen_plan.json"), plan.plan_sha256
            )

    def test_dry_run_validates_without_executing_or_creating_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            plan_path = fixture.make_plan()
            output = fixture.root / "must-not-exist"
            with fixture.pinned():
                with redirect_stdout(io.StringIO()):
                    status = RUNNER.main(
                        [
                            "--plan",
                            str(plan_path),
                            "--phase",
                            "calibration",
                            "--mode",
                            "timing",
                            "--dry-run",
                        ]
                    )
            self.assertEqual(status, 0)
            self.assertFalse(output.exists())
            self.assertEqual(fixture.events(), [])
            self.cooldown_sleep.assert_not_called()

    def test_hash_tamper_and_existing_output_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            plan = fixture.load(fixture.make_plan())
            fixture.map.write_bytes(b"tampered")
            with self.assertRaisesRegex(RUNNER.RunnerError, "SHA-256 mismatch"):
                RUNNER.preflight(plan)

            output = fixture.root / "existing"
            output.mkdir()
            with self.assertRaisesRegex(
                RUNNER.RunnerError, "already exists"
            ):
                RUNNER.execute(plan, output)

    def test_dirty_source_and_scenario_count_mismatch_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            plan = fixture.load(fixture.make_plan())
            (fixture.reference / "untracked.txt").write_text(
                "dirty\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(RUNNER.RunnerError, "worktree is dirty"):
                RUNNER.preflight(plan)

        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            plan_path = fixture.make_plan()
            raw = json.loads(plan_path.read_text(encoding="utf-8"))
            raw["pairs"][0]["robot_count"] = 3
            plan_path.write_text(
                json.dumps(raw, sort_keys=True) + "\n", encoding="utf-8"
            )
            plan = fixture.load(plan_path)
            with self.assertRaisesRegex(
                RUNNER.RunnerError, "scenario num_robots"
            ):
                RUNNER.preflight(plan)

    def test_validation_requires_explicit_unlock_without_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            grid = [2, 4]
            evaluation = {
                "environment": "open",
                "role": "timing-grid",
                "reference_anchor_n": 2,
                "candidate_n_star": 4,
                "next_grid_point": None,
                "grid": grid,
            }
            plan_path = fixture.make_plan(
                phase="validation",
                pair_specs=[
                    ("open", seed, count)
                    for seed in RUNNER.PHASE_SEEDS["validation"]
                    for count in grid
                ],
                timeout=300.0,
                capacity_evaluation=evaluation,
            )
            with fixture.pinned():
                with redirect_stderr(io.StringIO()):
                    status = RUNNER.main(
                        [
                            "--plan",
                            str(plan_path),
                            "--phase",
                            "validation",
                            "--mode",
                            "timing",
                            "--dry-run",
                        ]
                    )
            self.assertEqual(status, 2)
            self.assertEqual(fixture.events(), [])

    def test_validation_without_frozen_decision_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            plan_path = fixture.make_plan(
                phase="validation",
                pair_specs=[("open", 11, 2)],
            )
            with self.assertRaisesRegex(
                RUNNER.RunnerError, "require capacity_evaluation"
            ):
                fixture.load(plan_path)

    def test_fidelity_binary_requires_all_v3_contract_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            fixture.set_binaries(fidelity=True)
            plan = fixture.load(
                fixture.make_plan(mode="fidelity")
            )
            markers = RUNNER._verify_observer_flavor(
                plan.methods["reference"],
                "fidelity",
            )
            self.assertEqual(len(markers), 4)
            self.assertTrue(all(markers.values()))
            self.assertEqual(
                set(markers),
                {
                    marker.decode("ascii")
                    for marker in RUNNER.OBSERVER_BINARY_MARKERS
                },
            )

        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            fixture.set_binaries(
                fidelity=True,
                fidelity_trace_schema=TRACE_SCHEMA_V2,
            )
            plan = fixture.load(
                fixture.make_plan(mode="fidelity")
            )
            with self.assertRaisesRegex(
                RUNNER.RunnerError,
                "lacks comparison-observer markers",
            ):
                RUNNER._verify_observer_flavor(
                    plan.methods["reference"],
                    "fidelity",
                )

    def test_runner_accepts_v3_and_rejects_v2_trace_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / "trace.jsonl"
            resolved_config = {
                "config_file": "config/comparison/test.yaml",
                "scenario_file": (
                    "comparison/environments/open/scenario.json"
                ),
                "obstacle_file": (
                    "comparison/environments/open/map.png"
                ),
            }
            trace.write_text(
                json.dumps(
                    {
                        "type": "header",
                        "schema_version": RUNNER.TRACE_SCHEMA,
                        "metadata": {},
                        "resolved_config": resolved_config,
                        "observer": {
                            "trace_detail": "digest",
                            "timing_eligible": False,
                            "digest_algorithm": (
                                RUNNER.TRACE_DIGEST_ALGORITHM
                            ),
                            "future_state_scope": (
                                "fixture future-affecting state"
                            ),
                            "message_checksum_scope": (
                                "fixture live-message state"
                            ),
                        },
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            self.assertIsNone(
                RUNNER._parse_trace_schema_contract(
                    trace,
                    "digest",
                )
            )

            trace.write_text(
                json.dumps(
                    {
                        "type": "header",
                        "schema_version": TRACE_SCHEMA_V2,
                        "metadata": {},
                        "resolved_config": resolved_config,
                        "observer": {
                            "trace_detail": "digest",
                            "timing_eligible": False,
                            "digest_algorithm": V2_DIGEST_ALGORITHM,
                        },
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RUNNER.RunnerError,
                "schema_version must be "
                "'dancers-comparison-trace-v3'",
            ):
                RUNNER._parse_trace_schema_contract(
                    trace,
                    "digest",
                )

    def test_registered_plans_reject_diagnostic_trace_details(self) -> None:
        for detail in ("full", "positions"):
            with self.subTest(trace_detail=detail):
                with tempfile.TemporaryDirectory() as tmp:
                    fixture = RunnerFixture(Path(tmp))
                    plan_path = fixture.make_plan(mode="fidelity")
                    raw = json.loads(
                        plan_path.read_text(encoding="utf-8")
                    )
                    raw["trace_detail"] = detail
                    plan_path.write_bytes(
                        RUNNER.canonical_json_bytes(raw)
                    )
                    with self.assertRaisesRegex(
                        RUNNER.RunnerError,
                        "must be digest.*diagnostic-only",
                    ):
                        fixture.load(plan_path)

    def test_fidelity_mode_compares_semantic_traces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            fixture.set_binaries(fidelity=True)
            plan = fixture.load(
                fixture.make_plan(mode="fidelity")
            )
            output = fixture.root / "fidelity-output"
            session = RUNNER.execute(plan, output)
            self.assertTrue(session["all_pairs_valid"])
            pair = json.loads(
                (output / "pairs.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()[0]
            )
            self.assertTrue(pair["semantic_trace_equal"])
            self.assertEqual(pair["frames_compared"], 200)
            attempts = [
                json.loads(line)
                for line in (output / "runs.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertTrue(attempts)
            self.assertTrue(
                all(
                    set(attempt["artifacts"]["trace"])
                    == {"path", "sha256"}
                    for attempt in attempts
                )
            )

    def test_full_grid_fidelity_mismatch_does_not_censor_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            fixture.set_binaries(
                fidelity=True, optimized_trace_differs=True
            )
            plan = fixture.load(
                fixture.make_plan(
                    mode="fidelity",
                    pair_specs=[("open", 1, 2), ("open", 2, 2)],
                )
            )
            output = fixture.root / "mismatch-output"
            session = RUNNER.execute(plan, output)
            self.assertFalse(session["all_pairs_valid"])
            self.assertFalse(session["stopped_early"])
            self.assertEqual(session["attempt_count"], 4)

    def test_initial_fidelity_gate_stops_after_first_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            fixture.set_binaries(
                fidelity=True, optimized_trace_differs=True
            )
            plan = fixture.load(
                fixture.make_plan(
                    mode="fidelity",
                    pair_specs=[
                        ("open", seed, 8)
                        for seed in RUNNER.PHASE_SEEDS["calibration"]
                    ],
                )
            )
            self.assertTrue(
                RUNNER._is_initial_calibration_fidelity_gate(plan)
            )
            output = fixture.root / "initial-gate-mismatch-output"
            session = RUNNER.execute(plan, output)
            self.assertFalse(session["all_pairs_valid"])
            self.assertTrue(session["stopped_early"])
            self.assertEqual(session["attempt_count"], 2)

    def test_timeout_and_nonzero_exit_both_remain_in_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            fixture.set_binaries(
                reference_sleep=1.0,
                optimized_exit=7,
            )
            plan = fixture.load(
                fixture.make_plan(timeout=0.8)
            )
            output = fixture.root / "failure-output"
            session = RUNNER.execute(plan, output)
            self.assertFalse(session["all_pairs_valid"])
            rows = [
                json.loads(line)
                for line in (output / "runs.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(
                {item["terminal_category"] for item in rows},
                {"runner-timeout", "nonzero-simulator-exit"},
                rows,
            )
            self.assertEqual(len(rows), 2)
            self.assertTrue(
                all(item["attempt_kind"] == "warmup" for item in rows)
            )
            self.assertFalse(session["warmups_valid"])
            self.assertEqual(session["pair_count"], 0)

    def test_capacity_evaluation_is_nonadaptive_and_provenanced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            grid = [2, 4]
            evaluation = {
                "environment": "open",
                "role": "timing-grid",
                "reference_anchor_n": 2,
                "candidate_n_star": 4,
                "next_grid_point": None,
                "grid": grid,
            }
            pair_specs = [
                ("open", seed, count)
                for seed in RUNNER.PHASE_SEEDS["validation"]
                for count in grid
            ]
            plan = fixture.load(
                fixture.make_plan(
                    phase="validation",
                    mode="timing",
                    pair_specs=pair_specs,
                    timeout=300.0,
                    capacity_evaluation=evaluation,
                )
            )
            self.assertIsNone(plan.capacity)
            self.assertEqual(plan.capacity_evaluation.record(), evaluation)

            def completed_attempt(
                plan: RUNNER.Plan,
                pair: RUNNER.Pair,
                block_id: str,
                repetition: int,
                order: str,
                order_position: int,
                sequence: int,
                method: RUNNER.Method,
                config: RUNNER.Artifact,
                attempts_root: Path,
                *,
                attempt_kind: str = "measured",
            ) -> dict:
                del attempts_root
                attempt_id = (
                    f"{sequence:06d}__{block_id}__{method.method_id}"
                )
                return {
                    "schema_version": RUNNER.RESULT_SCHEMA,
                    "sequence": sequence,
                    "attempt_id": attempt_id,
                    "block_id": block_id,
                    "pair_id": pair.pair_id,
                    "repetition": repetition,
                    "attempt_kind": attempt_kind,
                    "order": order,
                    "order_position": order_position,
                    "phase": plan.phase,
                    "mode": plan.mode,
                    "environment": pair.environment,
                    "seed": pair.seed,
                    "robot_count": pair.robot_count,
                    "method": method.method_id,
                    "terminal_category": "completed",
                    "orchestration_error": None,
                    "task_valid": None,
                    "fidelity_metrics": None,
                    "resource": {
                        "returncode": 0,
                        "exit_code": 0,
                        "signal": None,
                        "timed_out": False,
                        "wall_seconds": 1.0,
                        "user_cpu_seconds": 0.5,
                        "system_cpu_seconds": 0.5,
                        "total_cpu_seconds": 1.0,
                        "peak_rss_raw": 1024,
                        "peak_rss_raw_unit": "KiB",
                        "peak_rss_mib": 1.0,
                        "maximum_peak_rss_mib": (
                            plan.maximum_peak_rss_mib
                        ),
                        "peak_rss_within_limit": True,
                    },
                    "provenance": {
                        "config_sha256": config.sha256,
                        "binary_sha256": method.binary.sha256,
                        "map_sha256": pair.map_artifact.sha256,
                        "scenario_sha256": pair.scenario.sha256,
                    },
                    "artifacts": {
                        "config": {
                            "path": str(config.path),
                            "sha256": config.sha256,
                        },
                        "stdout": {"path": "mock", "sha256": "0" * 64},
                        "stderr": {"path": "mock", "sha256": "0" * 64},
                        "trace": None,
                        "summary": None,
                    },
                    "completed_utc": "2026-07-28T00:00:00Z",
                }

            output = fixture.root / "capacity-evaluation-output"
            with mock.patch.object(
                RUNNER, "_run_attempt", side_effect=completed_attempt
            ):
                session = RUNNER.execute(plan, output)

            run_manifest = json.loads(
                (output / "run_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                run_manifest["capacity_evaluation"], evaluation
            )
            self.assertEqual(session["capacity_evaluation"], evaluation)
            self.assertIsNone(run_manifest["capacity_search"])
            self.assertIsNone(session["capacity"])
            self.assertEqual(
                run_manifest["count_order_policy"],
                "seeded base permutation alternated forward/reverse across "
                "seed-repetition units; all finite grid points execute "
                "nonadaptively",
            )
            self.assertEqual(
                len(run_manifest["blocks"]), len(pair_specs) * 3
            )
            first_unit = run_manifest["blocks"][:2]
            second_unit = run_manifest["blocks"][2:4]
            self.assertEqual(
                {block["seed"] for block in first_unit}, {11}
            )
            self.assertEqual(
                {block["robot_count"] for block in first_unit}, set(grid)
            )
            self.assertEqual(
                [block["robot_count"] for block in second_unit],
                list(reversed(
                    [block["robot_count"] for block in first_unit]
                )),
            )
            self.assertEqual(session["pair_count"], len(pair_specs) * 3)
            self.assertEqual(run_manifest["timestep_seconds"], 0.1)
            self.assertEqual(session["timestep_seconds"], 0.1)
            self.assertEqual(
                run_manifest["idle_cooldown"]["elapsed_seconds"], 300
            )
            self.assertTrue(run_manifest["idle_cooldown"]["valid"])
            self.cooldown_sleep.assert_called_once_with(300)
            first_config = next((output / "blocks").glob("*/config.yaml"))
            self.assertIn(
                "TIMESTEP: 0.1",
                first_config.read_text(encoding="utf-8"),
            )

    def test_capacity_evaluation_rejects_wrong_role_and_incomplete_grid(
        self,
    ) -> None:
        evaluation = {
            "environment": "open",
            "role": "fidelity-grid",
            "reference_anchor_n": 2,
            "candidate_n_star": 4,
            "next_grid_point": 6,
            "grid": [2, 4, 6],
        }
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            plan_path = fixture.make_plan(
                phase="validation",
                mode="timing",
                pair_specs=[("open", 11, 2)],
                capacity_evaluation=evaluation,
            )
            with self.assertRaisesRegex(
                RUNNER.RunnerError, "role must be 'timing-grid'"
            ):
                fixture.load(plan_path)

        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            evaluation["role"] = "timing-grid"
            plan_path = fixture.make_plan(
                phase="validation",
                mode="timing",
                pair_specs=[("open", 11, 2)],
                capacity_evaluation=evaluation,
            )
            with self.assertRaisesRegex(
                RUNNER.RunnerError, "exactly one pair"
            ):
                fixture.load(plan_path)

    def test_capacity_evaluation_accepts_anchor_as_negative_outcome(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            grid = [2, 4]
            evaluation = {
                "environment": "open",
                "role": "timing-grid",
                "reference_anchor_n": 2,
                "candidate_n_star": 2,
                "next_grid_point": 4,
                "grid": grid,
            }
            pair_specs = [
                ("open", seed, count)
                for seed in RUNNER.PHASE_SEEDS["validation"]
                for count in grid
            ]
            plan = fixture.load(
                fixture.make_plan(
                    phase="validation",
                    mode="timing",
                    pair_specs=pair_specs,
                    timeout=300.0,
                    capacity_evaluation=evaluation,
                )
            )
            self.assertEqual(
                plan.capacity_evaluation.candidate_n_star, 2
            )
            self.assertEqual(plan.capacity_evaluation.next_grid_point, 4)

    def test_capacity_evaluation_rejects_truncated_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            grid = [2, 4]
            evaluation = {
                "environment": "open",
                "role": "timing-grid",
                "reference_anchor_n": 2,
                "candidate_n_star": 4,
                "next_grid_point": None,
                "grid": grid,
            }
            plan_path = fixture.make_plan(
                phase="validation",
                mode="timing",
                pair_specs=[
                    ("open", seed, count)
                    for seed in RUNNER.PHASE_SEEDS["validation"]
                    for count in grid
                ],
                timeout=300.0,
                capacity_evaluation=evaluation,
            )
            raw_plan = json.loads(
                plan_path.read_text(encoding="utf-8")
            )
            decision_path = Path(
                raw_plan["artifacts"]["capacity_decision"]["path"]
            )
            decision = json.loads(
                decision_path.read_text(encoding="utf-8")
            )
            decision.pop("selection_rule_id")
            decision_path.write_bytes(
                RUNNER.canonical_json_bytes(decision)
            )
            raw_plan["artifacts"]["capacity_decision"]["sha256"] = (
                digest(decision_path)
            )
            plan_path.write_text(
                json.dumps(raw_plan, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RUNNER.RunnerError, "unexpected top-level schema"
            ):
                fixture.load(plan_path)

    def test_timestep_and_calibration_evaluation_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            plan_path = fixture.make_plan()
            raw = json.loads(plan_path.read_text(encoding="utf-8"))
            raw["timestep_seconds"] = 0.2
            plan_path.write_text(
                json.dumps(raw, sort_keys=True) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(
                RUNNER.RunnerError, "preregistered value 0.1"
            ):
                fixture.load(plan_path)

        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            plan_path = fixture.make_plan()
            raw = json.loads(plan_path.read_text(encoding="utf-8"))
            raw["minimum_free_disk_bytes"] = 1
            plan_path.write_text(
                json.dumps(raw, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RUNNER.RunnerError, "20 GiB safeguard"
            ):
                fixture.load(plan_path)

        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            evaluation = {
                "environment": "open",
                "role": "timing-grid",
                "reference_anchor_n": 2,
                "candidate_n_star": 4,
                "next_grid_point": 6,
                "grid": [2, 4, 6],
            }
            plan_path = fixture.make_plan(
                phase="calibration",
                mode="timing",
                pair_specs=[
                    ("open", seed, count)
                    for seed in RUNNER.PHASE_SEEDS["calibration"]
                    for count in evaluation["grid"]
                ],
                capacity_evaluation=evaluation,
            )
            with self.assertRaisesRegex(
                RUNNER.RunnerError, "forbidden during calibration"
            ):
                fixture.load(plan_path)

    def test_capacity_uses_seed_units_and_executes_full_interleaved_grid(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            fixture.set_binaries(
                reference_cpu={2: 0.08, 4: 0.08, 6: 0.08},
                optimized_cpu={2: 0.01, 4: 0.16, 6: 0.01},
            )
            capacity = {
                "environment": "open",
                "reference_anchor_n": 2,
                "grid": [2, 4, 6],
                "max_points": 3,
            }
            plan = fixture.load(
                fixture.make_plan(
                    pair_specs=[
                        ("open", 1, 2),
                        ("open", 1, 4),
                        ("open", 1, 6),
                    ],
                    capacity=capacity,
                )
            )
            output = fixture.root / "capacity-output"
            session = RUNNER.execute(plan, output)
            capacity_result = session["capacity"]
            self.assertIsNone(capacity_result["largest_passing_n"])
            self.assertEqual(
                capacity_result[
                    "provisional_largest_compute_passing_n"
                ],
                6,
            )
            self.assertEqual(
                capacity_result[
                    "first_provisional_compute_failing_n"
                ],
                4,
            )
            self.assertEqual(
                capacity_result["unexecuted_grid_points"], []
            )
            self.assertFalse(capacity_result["validated"])
            self.assertEqual(
                capacity_result["budget"]["source"],
                "median across seed units after median of three "
                "within-seed repetitions",
            )
            self.assertEqual(
                {
                    event["robot_count"]
                    for event in fixture.events()
                },
                {2, 4, 6},
            )


class ExperimentControlBindingTests(unittest.TestCase):
    def test_only_preregistered_calibration_shape_is_accepted(self) -> None:
        experiment = json.loads(
            (
                Path(__file__).resolve().parents[3]
                / "docs"
                / "comparison"
                / "experiment_manifest.json"
            ).read_text(encoding="utf-8")
        )
        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            initial = fixture.load(
                fixture.make_plan(
                    phase="calibration",
                    mode="fidelity",
                    pair_specs=[
                        ("open", seed, 8)
                        for seed in RUNNER.PHASE_SEEDS["calibration"]
                    ],
                    timeout=300.0,
                )
            )
            RUNNER._validate_experiment_controls(initial, experiment)

        with tempfile.TemporaryDirectory() as tmp:
            fixture = RunnerFixture(Path(tmp))
            arbitrary = fixture.load(fixture.make_plan())
            with self.assertRaisesRegex(
                RUNNER.RunnerError,
                "calibration timeout_seconds must equal 300",
            ):
                RUNNER._validate_experiment_controls(
                    arbitrary, experiment
                )


if __name__ == "__main__":
    unittest.main()
