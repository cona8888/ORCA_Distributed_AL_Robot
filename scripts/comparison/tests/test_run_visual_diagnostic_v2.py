from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import py_compile
import struct
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = (
    REPO_ROOT / "scripts" / "comparison" / "run_visual_diagnostic_v2.py"
)
SPEC = importlib.util.spec_from_file_location(
    "run_visual_diagnostic_v2", MODULE_PATH
)
assert SPEC and SPEC.loader
CONTROLLER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = CONTROLLER
SPEC.loader.exec_module(CONTROLLER)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact(root: Path, path: Path) -> dict[str, str]:
    return {
        "path": path.relative_to(root).as_posix(),
        "sha256": digest(path),
    }


def write_canonical(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(CONTROLLER.canonical_json_bytes(value))


def filesystem_snapshot(root: Path) -> tuple[tuple, ...]:
    records = []
    for path in sorted((root, *root.rglob("*"))):
        metadata = path.lstat()
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            kind = "symlink"
            content = os.readlink(path)
        elif path.is_dir():
            kind = "directory"
            content = None
        elif path.is_file():
            kind = "file"
            content = digest(path)
        else:
            kind = "special"
            content = None
        records.append(
            (
                relative,
                kind,
                metadata.st_mode & 0o7777,
                metadata.st_size,
                metadata.st_mtime_ns,
                content,
            )
        )
    return tuple(records)


class VisualFixture:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.plan_dir = (
            self.root
            / "comparison"
            / "plans"
            / "simulator-demo"
            / "v1"
            / "visual-diagnostic-calibration-v2"
        )
        self.plan_path = self.plan_dir / "plan.json"
        self._make_tools()
        self._make_binaries()
        self._make_scenarios_and_environment()
        self._make_build_manifest()
        self._make_contract()
        self._make_recovery()
        self._make_plan()
        self.plan = CONTROLLER.load_plan(self.plan_path)

    def _write(self, relative: str, data: bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def _make_tools(self) -> None:
        self.pixi = self._write(
            "pixi.lock", (REPO_ROOT / "pixi.lock").read_bytes()
        )
        self.config = self._write(
            "config/comparison/laptop_benchmark.yaml",
            (
                REPO_ROOT / "config/comparison/laptop_benchmark.yaml"
            ).read_bytes(),
        )
        self.trace_compare = self._write(
            "scripts/comparison/trace_compare.py",
            (
                REPO_ROOT / "scripts/comparison/trace_compare.py"
            ).read_bytes(),
        )
        self.controller = self._write(
            "scripts/comparison/run_visual_diagnostic_v2.py",
            MODULE_PATH.read_bytes(),
        )
        self.viewer = self._write(
            "scripts/comparison/build_replay_viewer.py",
            (
                b'"""DIAGNOSTIC REPLAY; NOT LIVE SOLVER TIMING; '
                b'RENDERING EXCLUDED FROM COMPUTE."""\n'
                b"def build_replay_viewer(*args, **kwargs):\n"
                b"    raise RuntimeError\n"
            ),
        )
        self.freezer = self._write(
            "scripts/comparison/freeze_visual_plan_v2.py",
            b"# frozen test plan freezer\n",
        )
        self.materializer = self._write(
            "scripts/comparison/materialize_capacity_scenario.py",
            b"# frozen test scenario materializer\n",
        )

    def _make_binaries(self) -> None:
        self.reference_binary = self._write(
            "build/comparison/trace-v3/build/reference-fidelity/dancers",
            (
                REPO_ROOT
                / "build/comparison/trace-v3/build/"
                "reference-fidelity/dancers"
            ).read_bytes(),
        )
        self.optimized_binary = self._write(
            "build/comparison/trace-v3/build/optimized-fidelity/dancers",
            (
                REPO_ROOT
                / "build/comparison/trace-v3/build/"
                "optimized-fidelity/dancers"
            ).read_bytes(),
        )
        self.reference_binary.chmod(0o755)
        self.optimized_binary.chmod(0o755)

    def _make_scenarios_and_environment(self) -> None:
        source_plan = json.loads(
            (REPO_ROOT / CONTROLLER.V1_PLAN_PATH).read_text(
                encoding="utf-8"
            )
        )
        self.environment_descriptors = source_plan[
            "environment_descriptors"
        ]
        source_scenes = {
            (
                record["family"],
                record["robot_count"],
            ): record
            for record in source_plan["scenes"]
            if record["seed"] == 1
        }
        self.scene_artifacts: dict[
            tuple[str, int], tuple[Path, str, Path, str]
        ] = {}
        for family in CONTROLLER.FAMILIES:
            first_scene = source_scenes[(family, CONTROLLER.COUNTS[0])]
            source_map = REPO_ROOT / first_scene["map"]["path"]
            map_path = self._write(
                first_scene["map"]["path"],
                source_map.read_bytes(),
            )
            map_sha = digest(map_path)
            if map_sha != first_scene["map"]["sha256"]:
                raise AssertionError("registered test map pin differs")
            for count in CONTROLLER.COUNTS:
                source_scene = source_scenes[(family, count)]
                source_scenario = REPO_ROOT / source_scene["scenario"][
                    "path"
                ]
                scenario_path = (
                    self.plan_dir / "scenarios" / family / f"n-{count}.json"
                )
                scenario_path = self._write(
                    scenario_path.relative_to(self.root).as_posix(),
                    source_scenario.read_bytes(),
                )
                scenario_sha = digest(scenario_path)
                if (
                    source_scene["map"]["sha256"] != map_sha
                    or source_scene["scenario"]["sha256"] != scenario_sha
                ):
                    raise AssertionError(
                        "registered visual scene artifact pin differs"
                    )
                self.scene_artifacts[(family, count)] = (
                    map_path,
                    map_sha,
                    scenario_path,
                    scenario_sha,
                )
        self.environment_manifest = (
            self.root / "comparison/environments/manifest.json"
        )
        self._write(
            self.environment_manifest.relative_to(self.root).as_posix(),
            (
                REPO_ROOT / "comparison/environments/manifest.json"
            ).read_bytes(),
        )

    def _make_build_manifest(self) -> None:
        self.build_manifest = (
            self.root / "comparison/build_manifest.json"
        )
        source = REPO_ROOT / "comparison/build_manifest.json"
        self._write(
            self.build_manifest.relative_to(self.root).as_posix(),
            source.read_bytes(),
        )
        self.build_record = json.loads(
            self.build_manifest.read_text(encoding="utf-8")
        )

    def _make_contract(self) -> None:
        self.contract_path = (
            self.root / "docs/simulator/simulator_contract_v1.json"
        )
        self.committed_contract_bytes = (
            REPO_ROOT / CONTROLLER.CONTRACT_PATH
        ).read_bytes()
        self._write(
            self.contract_path.relative_to(self.root).as_posix(),
            self.committed_contract_bytes,
        )
        if digest(self.contract_path) != CONTROLLER.PARENT_CONTRACT_SHA256:
            raise AssertionError("test parent contract pin differs")
        self.contract_commit = CONTROLLER.PARENT_CONTRACT_COMMIT

    def _make_recovery(self) -> None:
        self.committed_blobs: dict[tuple[str, str], bytes] = {}
        self.recovery_registry = self._write(
            CONTROLLER.RECOVERY_REGISTRY_PATH,
            (REPO_ROOT / CONTROLLER.RECOVERY_REGISTRY_PATH).read_bytes(),
        )
        self.recovery_addendum = self._write(
            CONTROLLER.RECOVERY_ADDENDUM_PATH,
            (REPO_ROOT / CONTROLLER.RECOVERY_ADDENDUM_PATH).read_bytes(),
        )
        if digest(self.recovery_registry) != (
            CONTROLLER.RECOVERY_REGISTRY_SHA256
        ):
            raise AssertionError("test recovery registry pin differs")
        if digest(self.recovery_addendum) != (
            CONTROLLER.RECOVERY_ADDENDUM_SHA256
        ):
            raise AssertionError("test recovery addendum pin differs")
        self.committed_blobs[
            (
                CONTROLLER.RECOVERY_COMMIT,
                CONTROLLER.RECOVERY_REGISTRY_PATH,
            )
        ] = self.recovery_registry.read_bytes()
        self.committed_blobs[
            (
                CONTROLLER.RECOVERY_COMMIT,
                CONTROLLER.RECOVERY_ADDENDUM_PATH,
            )
        ] = self.recovery_addendum.read_bytes()
        self.committed_blobs[
            (
                CONTROLLER.PARENT_CONTRACT_COMMIT,
                CONTROLLER.CONTRACT_PATH,
            )
        ] = self.committed_contract_bytes

        self.v1_plan = self._write(
            CONTROLLER.V1_PLAN_PATH,
            (REPO_ROOT / CONTROLLER.V1_PLAN_PATH).read_bytes(),
        )
        if digest(self.v1_plan) != CONTROLLER.V1_PLAN_SHA256:
            raise AssertionError("test v1 plan pin differs")
        self.committed_blobs[
            (CONTROLLER.V1_PLAN_COMMIT, CONTROLLER.V1_PLAN_PATH)
        ] = self.v1_plan.read_bytes()

        source_failure = (
            REPO_ROOT
            / Path(*CONTROLLER.V1_FAILURE_RELATIVE_PATH.parts)
        )
        self.v1_failure_root = (
            self.root
            / Path(*CONTROLLER.V1_FAILURE_RELATIVE_PATH.parts)
        )
        registry = json.loads(
            self.recovery_registry.read_text(encoding="utf-8")
        )
        for record in registry["failure_evidence"]["files"]:
            relative = Path(*Path(record["path"]).parts)
            data = (source_failure / relative).read_bytes()
            path = self._write(
                (
                    CONTROLLER.V1_FAILURE_RELATIVE_PATH
                    / CONTROLLER.PurePosixPath(record["path"])
                ).as_posix(),
                data,
            )
            path.chmod(0o444)
            self.committed_blobs[
                (
                    CONTROLLER.V1_FAILURE_COMMIT,
                    (
                        CONTROLLER.V1_FAILURE_RELATIVE_PATH
                        / CONTROLLER.PurePosixPath(record["path"])
                    ).as_posix(),
                )
            ] = data
        (self.v1_failure_root / "reference").chmod(0o555)
        self.v1_failure_root.chmod(0o555)

    def _make_plan(self) -> None:
        scenes = []
        for family in CONTROLLER.FAMILIES:
            for count in CONTROLLER.COUNTS:
                map_path, map_sha, scenario_path, scenario_sha = (
                    self.scene_artifacts[(family, count)]
                )
                for seed in CONTROLLER.SEEDS:
                    scene_id = f"{family}-n{count}-seed{seed:02d}"
                    output_relpath = (
                        f"{family}/n-{count}/seed-{seed:02d}"
                    )
                    scenes.append(
                        {
                            "scene_id": scene_id,
                            "family": family,
                            "robot_count": count,
                            "seed": seed,
                            "map": {
                                "path": map_path.relative_to(
                                    self.root
                                ).as_posix(),
                                "sha256": map_sha,
                            },
                            "scenario": {
                                "path": scenario_path.relative_to(
                                    self.root
                                ).as_posix(),
                                "sha256": scenario_sha,
                            },
                            "output_relpath": output_relpath,
                            "config_cli_argument": (
                                f"{CONTROLLER.ALLOWED_OUTPUT_ROOT.as_posix()}/"
                                f"{output_relpath}/config.yaml"
                            ),
                            "run_ids": {
                                method_id: (
                                    f"{CONTROLLER.RUN_ID_PREFIX}__"
                                    f"{scene_id}__{method_id}"
                                )
                                for method_id in CONTROLLER.METHOD_ORDER
                            },
                        }
                    )
        self.plan_record = {
            "schema_version": CONTROLLER.PLAN_SCHEMA,
            "plan_id": CONTROLLER.PLAN_ID,
            "track_id": CONTROLLER.TRACK_ID,
            "phase": "calibration",
            "status": "frozen",
            "diagnostic_only": True,
            "compute_claim": False,
            "timing_unlock": False,
            "paper_replication_evidence": False,
            "execution_epoch_id": CONTROLLER.EXECUTION_EPOCH_ID,
            "run_id_prefix": CONTROLLER.RUN_ID_PREFIX,
            "config_cli_path_policy": (
                CONTROLLER.CONFIG_CLI_PATH_POLICY
            ),
            "output_schemas": dict(CONTROLLER.OUTPUT_SCHEMAS),
            "recovery": {
                "commit": CONTROLLER.RECOVERY_COMMIT,
                "registry": artifact(
                    self.root, self.recovery_registry
                ),
                "addendum": artifact(
                    self.root, self.recovery_addendum
                ),
            },
            "contract": {
                "commit": self.contract_commit,
                **artifact(self.root, self.contract_path),
            },
            "artifacts": {
                "build_manifest": artifact(
                    self.root, self.build_manifest
                ),
                "environment_manifest": artifact(
                    self.root, self.environment_manifest
                ),
                "pixi_lock": artifact(self.root, self.pixi),
                "config_template": artifact(self.root, self.config),
                "trace_compare": artifact(
                    self.root, self.trace_compare
                ),
                "viewer": artifact(self.root, self.viewer),
                "controller": artifact(self.root, self.controller),
                "plan_freezer": artifact(self.root, self.freezer),
                "capacity_materializer": artifact(
                    self.root, self.materializer
                ),
            },
            "methods": {
                "reference": {
                    "build_manifest_key": "reference_fidelity",
                    "binary": artifact(
                        self.root, self.reference_binary
                    ),
                    "source_revision": CONTROLLER.REFERENCE_REVISION,
                },
                "optimized": {
                    "build_manifest_key": "optimized_fidelity",
                    "binary": artifact(
                        self.root, self.optimized_binary
                    ),
                    "source_revision": CONTROLLER.OPTIMIZED_REVISION,
                },
            },
            "environment_descriptors": self.environment_descriptors,
            "execution": {
                "families": list(CONTROLLER.FAMILIES),
                "counts": list(CONTROLLER.COUNTS),
                "seeds": list(CONTROLLER.SEEDS),
                "timesteps": 100,
                "timestep_seconds": 0.1,
                "simulated_duration_seconds": 10,
                "trace_detail": "positions",
                "run_class": "positions-replay",
                "serial_execution": True,
                "fresh_process_per_method": True,
                "method_order": list(CONTROLLER.METHOD_ORDER),
                "timeout_seconds": 30,
                "termination_grace_seconds": 1,
                "omp_threads": 1,
                "failure_policy": CONTROLLER.FAILURE_POLICY,
            },
            "scenes": scenes,
            "expected_scene_count": (
                CONTROLLER.EXPECTED_SCENE_COUNT
            ),
            "expected_attempt_count": (
                CONTROLLER.EXPECTED_ATTEMPT_COUNT
            ),
            "allowed_output_root": (
                CONTROLLER.ALLOWED_OUTPUT_ROOT.as_posix()
            ),
        }
        write_canonical(self.plan_path, self.plan_record)

    def contract_blob(
        self, root: Path, commit: str, relative_path: str
    ) -> bytes:
        self.assert_contract_request(root, commit, relative_path)
        return self.committed_contract_bytes

    def assert_contract_request(
        self, root: Path, commit: str, relative_path: str
    ) -> None:
        if root != self.root:
            raise AssertionError("wrong repository root")
        if commit != CONTROLLER.PARENT_CONTRACT_COMMIT:
            raise AssertionError("wrong contract commit")
        if relative_path != CONTROLLER.CONTRACT_PATH:
            raise AssertionError("wrong contract path")

    def recovery_blob(
        self, root: Path, commit: str, relative_path: str
    ) -> bytes:
        if root != self.root:
            raise AssertionError("wrong recovery repository root")
        key = (commit, relative_path)
        if key not in self.committed_blobs:
            raise AssertionError(
                f"wrong registered blob request: {commit}:{relative_path}"
            )
        return self.committed_blobs[key]

    def v1_evidence_snapshot(self) -> tuple[tuple, ...]:
        root = self.root / Path(*CONTROLLER.V1_FAILURE_ROOT.parts)
        plan_metadata = self.v1_plan.lstat()
        records = [
            (
                "@v1-plan",
                "file",
                plan_metadata.st_mode & 0o7777,
                plan_metadata.st_size,
                digest(self.v1_plan),
            )
        ]
        for path in sorted((root, *root.rglob("*"))):
            metadata = path.lstat()
            relative = path.relative_to(root).as_posix()
            kind = (
                "symlink"
                if path.is_symlink()
                else "directory"
                if path.is_dir()
                else "file"
            )
            file_sha = (
                digest(path) if kind == "file" else None
            )
            records.append(
                (
                    relative,
                    kind,
                    metadata.st_mode & 0o7777,
                    metadata.st_size,
                    file_sha,
                )
            )
        return tuple(records)

    @staticmethod
    def no_remotes(root: Path) -> tuple[str, ...]:
        del root
        return ()

    @staticmethod
    def frozen_inputs(
        root: Path, paths: tuple
    ) -> str:
        del root
        if paths[0] != CONTROLLER.PLAN_RELATIVE_PATH or len(paths) != 26:
            raise AssertionError("wrong frozen plan input inventory")
        return "f" * 40

    @property
    def selected_scene_id(self) -> str:
        return "open-n8-seed01"

    @property
    def selected_output(self) -> Path:
        return (
            self.root
            / CONTROLLER.ALLOWED_OUTPUT_ROOT
            / "open"
            / "n-8"
            / "seed-01"
        )

    @property
    def lease_path(self) -> Path:
        return (
            self.root
            / Path(*CONTROLLER.EXECUTION_LEASE_RELATIVE_PATH.parts)
        )

    def options(self) -> dict:
        return {
            "controller_path": self.controller,
            "contract_blob_reader": self.contract_blob,
            "recovery_blob_reader": self.recovery_blob,
            "remote_reader": self.no_remotes,
            "frozen_input_verifier": self.frozen_inputs,
        }


def double_hex(value: float) -> str:
    return struct.pack(">d", float(value)).hex()


class FakeProcessRunner:
    def __init__(
        self,
        *,
        mismatch_optimized: bool = False,
        nonzero_reference: bool = False,
        config_file_override: str | None = None,
        summary_config_file_override: str | None = None,
        trace_metadata_drift: bool = False,
        run_valid: bool = True,
    ) -> None:
        self.mismatch_optimized = mismatch_optimized
        self.nonzero_reference = nonzero_reference
        self.config_file_override = config_file_override
        self.summary_config_file_override = summary_config_file_override
        self.trace_metadata_drift = trace_metadata_drift
        self.run_valid = run_valid
        self.methods: list[str] = []
        self.environments: list[dict[str, str]] = []
        self.commands: list[list[str]] = []
        self.config_arguments: list[str] = []
        self.working_directories: list[Path] = []

    def __call__(
        self,
        command: list[str],
        *,
        cwd: Path,
        environment: dict[str, str],
        stdout,
        stderr,
        timeout: float,
        grace: float,
    ):
        del timeout, grace
        if len(command) != 3 or command[1] != "--cfg":
            raise AssertionError("unexpected simulator command shape")
        config_argument = command[2]
        if Path(config_argument).is_absolute():
            raise AssertionError("config CLI argument must be relative")
        if "\\" in config_argument or any(
            character.isspace() for character in config_argument
        ):
            raise AssertionError("config CLI argument is unsafe")
        if not any(character.isspace() for character in str(cwd)):
            raise AssertionError("fixture repository root must contain spaces")
        metadata_path = Path(environment["DANCERS_RUN_METADATA"])
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        config_bytes = (cwd / config_argument).read_bytes()
        if hashlib.sha256(config_bytes).hexdigest() != (
            metadata["config_sha256"]
        ):
            raise AssertionError("runner opened the wrong config file")
        if metadata["config_cli_argument"] != config_argument:
            raise AssertionError("metadata config CLI argument differs")
        if metadata["config_cli_path_policy"] != (
            CONTROLLER.CONFIG_CLI_PATH_POLICY
        ):
            raise AssertionError("metadata config CLI policy differs")
        if metadata["execution_epoch_id"] != (
            CONTROLLER.EXECUTION_EPOCH_ID
        ):
            raise AssertionError("metadata execution epoch differs")
        method = metadata["implementation_id"]
        expected_run_id = (
            f"{CONTROLLER.RUN_ID_PREFIX}__{metadata['scene_id']}__{method}"
        )
        if metadata["run_id"] != expected_run_id:
            raise AssertionError("metadata run ID is not epoch-prefixed")
        self.methods.append(method)
        self.environments.append(dict(environment))
        self.commands.append(list(command))
        self.config_arguments.append(config_argument)
        self.working_directories.append(cwd)
        stdout.write(f"{method} complete\n".encode("utf-8"))
        stderr.write(b"")
        if method == "reference" and self.nonzero_reference:
            return CONTROLLER.ProcessExit(7, False)

        trace_path = Path(environment["DANCERS_TRACE_OUT"])
        summary_path = Path(environment["DANCERS_SUMMARY_OUT"])
        resolved = {
            "config_file": (
                self.config_file_override
                if self.config_file_override is not None
                else config_argument
            ),
            "scenario_file": metadata["scenario_path"],
            "obstacle_file": metadata["map_path"],
            "world_size_m": 100,
            "configured_robot_count": metadata["robot_count"],
            "rng_seed": metadata["seed"],
            "max_timesteps": 100,
            "display": False,
            "scenario": "file",
            "timestep_seconds": 0.1,
        }
        header = {
            "type": "header",
            "schema_version": CONTROLLER.TRACE_SCHEMA,
            "metadata": (
                {**metadata, "seed": metadata["seed"] + 1}
                if self.trace_metadata_drift
                else metadata
            ),
            "resolved_config": resolved,
            "observer": {
                "trace_detail": "positions",
                "timing_eligible": False,
                "digest_algorithm": (
                    CONTROLLER.TRACE_DIGEST_ALGORITHM
                ),
                "future_state_scope": "test future state",
                "message_checksum_scope": "test message state",
            },
        }
        frames = []
        phase_keys = [(0, "pre_update")]
        for tick in range(1, 101):
            phase_keys.append((tick, "post_update"))
            if tick < 100:
                phase_keys.append((tick, "pre_update"))
        for frame_index, (tick, phase) in enumerate(phase_keys):
            robots = []
            for robot_id in range(metadata["robot_count"]):
                x = float(robot_id) + tick * 0.01
                if (
                    self.mismatch_optimized
                    and method == "optimized"
                    and frame_index == len(phase_keys) - 1
                    and robot_id == 0
                ):
                    x += 0.5
                state = [x, float(robot_id) + tick * 0.02, 0.0]
                robots.append(
                    {
                        "rid": robot_id,
                        "state": state,
                        "state_hex": [
                            double_hex(item) for item in state
                        ],
                        "bearing": 0.0,
                        "radius_m": 1.0,
                        "current_waypoint": [10.0, 10.0, 0.0],
                        "final_goal": [20.0, 20.0],
                        "goal_reached": False,
                    }
                )
            frames.append(
                {
                    "type": "frame",
                    "tick": tick,
                    "phase": phase,
                    "simulated_time_seconds": tick * 0.1,
                    "robots": robots,
                    "metrics": {
                        "robot_count": metadata["robot_count"],
                        "goals_reached": 0,
                        "all_goals_reached": False,
                    },
                }
            )
        with trace_path.open("x", encoding="utf-8") as handle:
            for record in (header, *frames):
                handle.write(
                    json.dumps(
                        record,
                        sort_keys=True,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    + "\n"
                )
        count = metadata["robot_count"]
        summary_resolved = dict(resolved)
        if self.summary_config_file_override is not None:
            summary_resolved["config_file"] = (
                self.summary_config_file_override
            )
        summary = {
            "schema_version": CONTROLLER.SUMMARY_SCHEMA,
            "trace_detail": "positions",
            "frames_recorded": 200,
            "final_tick": 100,
            "final_robot_count": count,
            "maximum_robot_count": count,
            "termination_reason": "maximum_time",
            "run_valid": self.run_valid,
            "metadata": metadata,
            "trace_path": str(trace_path),
            "simulated_time_seconds": 10.0,
            "resolved_config": summary_resolved,
            "paper_metrics": {
                "consensus_reached": 0,
                "final_connectivity": 1.0,
                "final_decision": -1,
                "final_heading_error": 0.0,
                "final_iterations": 200,
                "final_position_error": 0.0,
            },
            "all_goals_reached": False,
            "goals_reached": 0,
            "goals_total": count,
            "first_all_goals_tick": None,
            "iterations": 200,
            "total_path_length_m": float(count),
            "mean_path_length_m_per_robot": 1.0,
            "per_robot_goal_tick": {},
            "per_robot_path_length_m": {
                str(index): 1.0 for index in range(count)
            },
            "obstacle_collision_events": 0,
            "obstacle_collision_exposure_seconds": 0.0,
            "pair_collision_events": 0,
            "pair_collision_exposure_seconds": 0.0,
            "minimum_obstacle_clearance_m": 1.0,
            "minimum_pair_clearance_m": 1.0,
            "invalid_geometry_evaluations": 0,
            "invalid_robot_frames": 0,
            "nonfinite_numeric_observations": 0,
            "nonfinite_state_observations": 0,
        }
        write_canonical(summary_path, summary)
        return CONTROLLER.ProcessExit(0, False)


class VisualControllerTests(unittest.TestCase):
    def fixture(self, temporary: str) -> VisualFixture:
        root = Path(temporary) / "repository root with spaces"
        root.mkdir()
        return VisualFixture(root)

    @staticmethod
    def viewer_builder(*args, **kwargs) -> SimpleNamespace:
        del args, kwargs
        replay = (
            b"DIAGNOSTIC REPLAY\n"
            b"NOT LIVE SOLVER TIMING\n"
            b"RENDERING EXCLUDED FROM COMPUTE\n"
        )
        return SimpleNamespace(
            html_bytes=replay,
            output_bytes=len(replay),
        )

    def complete_scene(
        self, fixture: VisualFixture, scene_id: str
    ) -> dict:
        return CONTROLLER.execute_scene(
            fixture.plan,
            scene_id,
            process_runner=FakeProcessRunner(),
            viewer_builder=self.viewer_builder,
            **fixture.options(),
        )

    def test_load_plan_requires_complete_ordered_crossing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            self.assertEqual(
                len(fixture.plan.scenes),
                CONTROLLER.EXPECTED_SCENE_COUNT,
            )
            record = dict(fixture.plan_record)
            record["scenes"] = list(record["scenes"][:-1])
            write_canonical(fixture.plan_path, record)
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "exactly 250",
            ):
                CONTROLLER.load_plan(fixture.plan_path)

    def test_load_plan_rejects_every_unsafe_config_cli_form(self) -> None:
        cases = {
            "absolute": "/tmp/config.yaml",
            "space": "results/unsafe path/config.yaml",
            "backslash": r"results\unsafe\config.yaml",
            "dot": "results/./unsafe/config.yaml",
            "dotdot": "results/unsafe/../config.yaml",
            "control": "results/unsafe/\x01config.yaml",
            "repeated_separator": "results//unsafe/config.yaml",
            "trailing_separator": "results/unsafe/config.yaml/",
            "drive_prefix": "C:/results/unsafe/config.yaml",
            "nbsp": "results/unsafe/\u00a0config.yaml",
            "delete_control": "results/unsafe/\x7fconfig.yaml",
            "format_control": "results/unsafe/\u200econfig.yaml",
        }
        for case, unsafe in cases.items():
            with (
                self.subTest(case=case),
                tempfile.TemporaryDirectory() as temporary,
            ):
                fixture = self.fixture(temporary)
                record = json.loads(json.dumps(fixture.plan_record))
                record["scenes"][0]["config_cli_argument"] = unsafe
                write_canonical(fixture.plan_path, record)
                with self.assertRaisesRegex(
                    CONTROLLER.ControllerError,
                    "config_cli_argument",
                ):
                    CONTROLLER.load_plan(fixture.plan_path)

    def test_load_plan_rejects_run_id_without_recovery_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            record = json.loads(json.dumps(fixture.plan_record))
            record["scenes"][0]["run_ids"]["reference"] = (
                "open-n8-seed01__reference"
            )
            write_canonical(fixture.plan_path, record)
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "run_ids.reference",
            ):
                CONTROLLER.load_plan(fixture.plan_path)

    def test_load_plan_requires_exact_parent_contract_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            cases = (
                (
                    "commit",
                    "0" * 40,
                    "contract.commit must be",
                ),
                (
                    "sha256",
                    "0" * 64,
                    "contract.sha256 must be",
                ),
                (
                    "path",
                    "docs/simulator/substituted_contract.json",
                    "contract.path must be",
                ),
            )
            for field, replacement, message in cases:
                with self.subTest(field=field):
                    record = json.loads(
                        json.dumps(fixture.plan_record)
                    )
                    record["contract"][field] = replacement
                    write_canonical(fixture.plan_path, record)
                    with self.assertRaisesRegex(
                        CONTROLLER.ControllerError,
                        message,
                    ):
                        CONTROLLER.load_plan(fixture.plan_path)

    def test_recovery_registry_contract_requires_exact_record(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            original = json.loads(
                fixture.recovery_registry.read_text(encoding="utf-8")
            )
            cases = (
                (
                    "substitution",
                    lambda value: value["contract"].__setitem__(
                        "contract_id", "substituted-contract"
                    ),
                    "recovery registry.contract.contract_id differs",
                ),
                (
                    "unknown-key",
                    lambda value: value["contract"].__setitem__(
                        "substitute", True
                    ),
                    "recovery registry.contract has unknown keys",
                ),
                (
                    "missing-key",
                    lambda value: value["contract"].pop("track_id"),
                    "recovery registry.contract is missing keys",
                ),
            )
            for case, mutate, message in cases:
                with self.subTest(case=case):
                    substituted = json.loads(json.dumps(original))
                    mutate(substituted)
                    write_canonical(
                        fixture.recovery_registry, substituted
                    )
                    recovery_artifact = replace(
                        fixture.plan.recovery_registry,
                        sha256=digest(fixture.recovery_registry),
                    )
                    substituted_plan = replace(
                        fixture.plan,
                        recovery_registry=recovery_artifact,
                    )
                    with self.assertRaisesRegex(
                        CONTROLLER.ControllerError,
                        message,
                    ):
                        CONTROLLER._verify_recovery_bindings(
                            substituted_plan,
                            fixture.recovery_blob,
                        )

    def test_dry_run_verifies_everything_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            v1_before = fixture.v1_evidence_snapshot()
            result = CONTROLLER.dry_run(
                fixture.plan,
                fixture.selected_scene_id,
                **fixture.options(),
            )
            self.assertTrue(result["full_crossing_verified"])
            self.assertEqual(result["scene_count"], 250)
            self.assertEqual(result["attempt_count"], 500)
            self.assertEqual(result["git_remote_count"], 0)
            self.assertEqual(
                result["execution_epoch_id"],
                CONTROLLER.EXECUTION_EPOCH_ID,
            )
            self.assertEqual(
                result["config_cli_path_policy"],
                CONTROLLER.CONFIG_CLI_PATH_POLICY,
            )
            self.assertEqual(
                result["recovery"]["commit"],
                CONTROLLER.RECOVERY_COMMIT,
            )
            self.assertTrue(
                result["execution_lease"]["dry_run_no_write"]
            )
            self.assertFalse(fixture.lease_path.exists())
            self.assertFalse(fixture.selected_output.exists())
            self.assertFalse(
                (
                    fixture.root
                    / Path(*CONTROLLER.ALLOWED_OUTPUT_ROOT.parts)
                ).exists()
            )
            self.assertEqual(
                fixture.v1_evidence_snapshot(), v1_before
            )

    def test_stale_execution_lease_requires_manual_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            fixture.lease_path.write_bytes(b"stale lease\n")
            before = fixture.lease_path.read_bytes()
            runner = FakeProcessRunner()
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "manual recovery is required",
            ):
                CONTROLLER.execute_scene(
                    fixture.plan,
                    fixture.selected_scene_id,
                    process_runner=runner,
                    viewer_builder=self.viewer_builder,
                    **fixture.options(),
                )
            self.assertEqual(runner.methods, [])
            self.assertEqual(fixture.lease_path.read_bytes(), before)
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "manual recovery is required",
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    fixture.selected_scene_id,
                    **fixture.options(),
                )
            self.assertEqual(fixture.lease_path.read_bytes(), before)

    def test_execution_lease_serializes_controller_processes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            entered = threading.Event()
            release = threading.Event()
            delegate = FakeProcessRunner()
            first_results: list[dict] = []
            first_errors: list[BaseException] = []

            def blocking_runner(*args, **kwargs):
                entered.set()
                if not release.wait(10):
                    raise AssertionError("concurrency test timed out")
                return delegate(*args, **kwargs)

            def first_execution() -> None:
                try:
                    first_results.append(
                        CONTROLLER.execute_scene(
                            fixture.plan,
                            fixture.selected_scene_id,
                            process_runner=blocking_runner,
                            viewer_builder=self.viewer_builder,
                            **fixture.options(),
                        )
                    )
                except BaseException as error:
                    first_errors.append(error)

            worker = threading.Thread(target=first_execution)
            worker.start()
            self.assertTrue(
                entered.wait(10), "first controller did not reach runner"
            )
            second_runner = FakeProcessRunner()
            try:
                with self.assertRaisesRegex(
                    CONTROLLER.ControllerError,
                    "manual recovery is required",
                ):
                    CONTROLLER.execute_scene(
                        fixture.plan,
                        "open-n8-seed02",
                        process_runner=second_runner,
                        viewer_builder=self.viewer_builder,
                        **fixture.options(),
                    )
                self.assertEqual(second_runner.methods, [])
                self.assertTrue(fixture.lease_path.is_file())
            finally:
                release.set()
                worker.join(15)
            self.assertFalse(worker.is_alive())
            self.assertEqual(first_errors, [])
            self.assertEqual(len(first_results), 1)
            self.assertEqual(delegate.methods, ["reference", "optimized"])
            self.assertFalse(fixture.lease_path.exists())

    def test_same_inode_execution_lease_tamper_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            delegate = FakeProcessRunner()

            def tampering_runner(*args, **kwargs):
                outcome = delegate(*args, **kwargs)
                payload = bytearray(fixture.lease_path.read_bytes())
                payload[0] ^= 1
                with fixture.lease_path.open("r+b") as handle:
                    handle.seek(0)
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                return outcome

            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "manual recovery",
            ):
                CONTROLLER.execute_scene(
                    fixture.plan,
                    fixture.selected_scene_id,
                    process_runner=tampering_runner,
                    viewer_builder=self.viewer_builder,
                    **fixture.options(),
                )
            self.assertEqual(delegate.methods, ["reference"])
            self.assertTrue(fixture.lease_path.is_file())
            self.assertFalse(
                (fixture.selected_output / "failure.json").exists()
            )

    def test_epoch_audit_allows_authenticated_prior_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            self.complete_scene(fixture, fixture.selected_scene_id)
            self.assertFalse(
                any(
                    "__pycache__" in path.parts
                    for path in fixture.root.rglob("*")
                )
            )
            before = filesystem_snapshot(fixture.root)
            next_scene = "open-n8-seed02"
            result = CONTROLLER.dry_run(
                fixture.plan,
                next_scene,
                **fixture.options(),
            )
            self.assertEqual(
                result["epoch_state"]["state"],
                "active-with-authenticated-completions",
            )
            self.assertEqual(
                result["epoch_state"]["completed_scene_count"], 1
            )
            self.assertEqual(
                result["epoch_state"]["completed_scenes"][0][
                    "scene_id"
                ],
                fixture.selected_scene_id,
            )
            self.assertFalse(
                fixture.plan.allowed_output_root
                .joinpath("open", "n-8", "seed-02")
                .exists()
            )
            self.assertEqual(filesystem_snapshot(fixture.root), before)

    def test_paired_run_invalid_remains_diagnostic_not_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            manifest = CONTROLLER.execute_scene(
                fixture.plan,
                fixture.selected_scene_id,
                process_runner=FakeProcessRunner(run_valid=False),
                viewer_builder=self.viewer_builder,
                **fixture.options(),
            )
            self.assertFalse(
                manifest["pair_validation"]["diagnostics"]["run_valid"]
            )
            result = CONTROLLER.dry_run(
                fixture.plan,
                "open-n8-seed02",
                **fixture.options(),
            )
            self.assertEqual(
                result["epoch_state"]["completed_scene_count"], 1
            )

    def test_epoch_audit_rejects_prior_failed_scene(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError, "exited with 7"
            ):
                CONTROLLER.execute_scene(
                    fixture.plan,
                    fixture.selected_scene_id,
                    process_runner=FakeProcessRunner(
                        nonzero_reference=True
                    ),
                    viewer_builder=self.viewer_builder,
                    **fixture.options(),
                )
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "prior failed scene closes the v2 epoch",
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    "open-n8-seed02",
                    **fixture.options(),
                )

    def test_epoch_audit_rejects_partial_scene(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            partial = fixture.selected_output
            partial.mkdir(parents=True)
            partial.chmod(0o555)
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "completed tree directories differ",
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    "open-n8-seed02",
                    **fixture.options(),
                )

    def test_epoch_audit_rejects_unknown_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            unknown = (
                fixture.plan.allowed_output_root
                / "unknown/n-8/seed-01"
            )
            unknown.mkdir(parents=True)
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "unknown destination",
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    fixture.selected_scene_id,
                    **fixture.options(),
                )

    def test_epoch_audit_rejects_unsealed_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            self.complete_scene(fixture, fixture.selected_scene_id)
            config = fixture.selected_output / "config.yaml"
            config.chmod(0o644)
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "file is not sealed 0444",
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    "open-n8-seed02",
                    **fixture.options(),
                )

    def test_epoch_audit_rejects_resealed_config_byte_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            self.complete_scene(fixture, fixture.selected_scene_id)
            config = fixture.selected_output / "config.yaml"
            config.chmod(0o644)
            config.write_bytes(config.read_bytes() + b"# drift\n")
            config.chmod(0o444)
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "rendered config bytes differ",
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    "open-n8-seed02",
                    **fixture.options(),
                )

    def test_epoch_audit_rejects_symlink_and_special_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            target = fixture.root / "outside-v2"
            target.mkdir()
            fixture.plan.allowed_output_root.parent.mkdir(
                parents=True, exist_ok=True
            )
            fixture.plan.allowed_output_root.symlink_to(
                target, target_is_directory=True
            )
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "symbolic link",
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    fixture.selected_scene_id,
                    **fixture.options(),
                )
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            fixture.plan.allowed_output_root.mkdir(parents=True)
            os.mkfifo(fixture.plan.allowed_output_root / "special")
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "contains a special file",
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    fixture.selected_scene_id,
                    **fixture.options(),
                )

    def test_epoch_audit_rejects_manifest_identity_drift(self) -> None:
        cases = {
            "schema_version": (
                ("schema_version",),
                "dancers-simulator-visual-diagnostic-result-v1",
            ),
            "plan_sha256": (("plan_sha256",), "0" * 64),
            "execution_epoch_id": (
                ("execution_epoch_id",),
                "wrong-epoch",
            ),
            "scene_id": (("scene", "scene_id"), "wrong-scene"),
            "config_cli_argument": (
                ("config_cli_argument",),
                "results/wrong/config.yaml",
            ),
            "run_ids": (
                ("run_ids", "reference"),
                "wrong-run-id",
            ),
        }
        for case, (path, drifted) in cases.items():
            with (
                self.subTest(case=case),
                tempfile.TemporaryDirectory() as temporary,
            ):
                fixture = self.fixture(temporary)
                self.complete_scene(fixture, fixture.selected_scene_id)
                manifest_path = fixture.selected_output / "manifest.json"
                manifest_path.chmod(0o644)
                manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
                target = manifest
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = drifted
                write_canonical(manifest_path, manifest)
                manifest_path.chmod(0o444)
                with self.assertRaisesRegex(
                    CONTROLLER.ControllerError,
                    "manifest.*differs",
                ):
                    CONTROLLER.dry_run(
                        fixture.plan,
                        "open-n8-seed02",
                        **fixture.options(),
                    )

    def test_epoch_audit_reauthenticates_pair_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            self.complete_scene(fixture, fixture.selected_scene_id)
            manifest_path = fixture.selected_output / "manifest.json"
            manifest_path.chmod(0o644)
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            manifest["pair_validation"]["semantic_trace_equal"] = False
            write_canonical(manifest_path, manifest)
            manifest_path.chmod(0o444)
            runner = FakeProcessRunner()
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "manifest pair_validation differs",
            ):
                CONTROLLER.execute_scene(
                    fixture.plan,
                    "open-n8-seed02",
                    process_runner=runner,
                    viewer_builder=self.viewer_builder,
                    **fixture.options(),
                )
            self.assertEqual(runner.methods, [])

    def test_epoch_audit_reauthenticates_mutated_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            self.complete_scene(fixture, fixture.selected_scene_id)
            summary_path = (
                fixture.selected_output / "reference/summary.json"
            )
            summary_path.chmod(0o644)
            summary = json.loads(
                summary_path.read_text(encoding="utf-8")
            )
            summary["total_path_length_m"] += 1.0
            write_canonical(summary_path, summary)
            summary_path.chmod(0o444)
            manifest_path = fixture.selected_output / "manifest.json"
            manifest_path.chmod(0o644)
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
            summary_artifact = manifest["methods"]["reference"][
                "artifacts"
            ]["summary"]
            summary_artifact["sha256"] = digest(summary_path)
            summary_artifact["bytes"] = summary_path.stat().st_size
            write_canonical(manifest_path, manifest)
            manifest_path.chmod(0o444)
            runner = FakeProcessRunner()
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "prior summary parity mismatch",
            ):
                CONTROLLER.execute_scene(
                    fixture.plan,
                    "open-n8-seed02",
                    process_runner=runner,
                    viewer_builder=self.viewer_builder,
                    **fixture.options(),
                )
            self.assertEqual(runner.methods, [])

    def test_preflight_rejects_hash_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            fixture.viewer.write_bytes(b"changed\n")
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError, "viewer SHA-256 mismatch"
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    fixture.selected_scene_id,
                    **fixture.options(),
                )

    def test_preflight_rejects_recovery_provenance_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            fixture.recovery_registry.write_bytes(b"changed\n")
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "recovery.registry SHA-256 mismatch",
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    fixture.selected_scene_id,
                    **fixture.options(),
                )

    def test_preflight_rejects_parent_contract_byte_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            fixture.contract_path.write_bytes(b"{}\n")
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "working parent contract SHA-256 differs",
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    fixture.selected_scene_id,
                    **fixture.options(),
                )

    def test_preflight_rejects_committed_parent_contract_blob_drift(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            options = fixture.options()

            def drifted_blob(
                root: Path, commit: str, relative_path: str
            ) -> bytes:
                if (
                    commit == CONTROLLER.PARENT_CONTRACT_COMMIT
                    and relative_path == CONTROLLER.CONTRACT_PATH
                ):
                    return b"drifted committed parent contract\n"
                return fixture.recovery_blob(root, commit, relative_path)

            options["recovery_blob_reader"] = drifted_blob
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "committed parent contract SHA-256 differs",
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    fixture.selected_scene_id,
                    **options,
                )

    def test_preflight_rejects_committed_recovery_blob_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            options = fixture.options()

            def drifted_blob(
                root: Path, commit: str, relative_path: str
            ) -> bytes:
                if relative_path == CONTROLLER.RECOVERY_ADDENDUM_PATH:
                    return b"drifted committed bytes\n"
                return fixture.recovery_blob(root, commit, relative_path)

            options["recovery_blob_reader"] = drifted_blob
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "committed recovery addendum differs",
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    fixture.selected_scene_id,
                    **options,
                )

    def test_preflight_rejects_committed_v1_plan_blob_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            options = fixture.options()

            def drifted_blob(
                root: Path, commit: str, relative_path: str
            ) -> bytes:
                if (
                    commit == CONTROLLER.V1_PLAN_COMMIT
                    and relative_path == CONTROLLER.V1_PLAN_PATH
                ):
                    return b"drifted committed v1 plan\n"
                return fixture.recovery_blob(root, commit, relative_path)

            options["recovery_blob_reader"] = drifted_blob
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "committed v1 plan differs",
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    fixture.selected_scene_id,
                    **options,
                )

    def test_preflight_rejects_committed_v1_failure_blob_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            options = fixture.options()

            def drifted_blob(
                root: Path, commit: str, relative_path: str
            ) -> bytes:
                if (
                    commit == CONTROLLER.V1_FAILURE_COMMIT
                    and relative_path.endswith("/failure.json")
                ):
                    return b"drifted committed failure\n"
                return fixture.recovery_blob(root, commit, relative_path)

            options["recovery_blob_reader"] = drifted_blob
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "committed v1 failure failure.json differs",
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    fixture.selected_scene_id,
                    **options,
                )

    def test_preflight_rejects_v1_failure_byte_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            config = fixture.v1_failure_root / "config.yaml"
            config.chmod(0o644)
            config.write_bytes(config.read_bytes() + b"drift")
            config.chmod(0o444)
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "byte count differs: config.yaml",
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    fixture.selected_scene_id,
                    **fixture.options(),
                )

    def test_preflight_rejects_v1_failure_mode_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            stderr = fixture.v1_failure_root / "reference/stderr.txt"
            stderr.chmod(0o644)
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "file mode differs: reference/stderr.txt",
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    fixture.selected_scene_id,
                    **fixture.options(),
                )

    def test_preflight_rejects_v1_failure_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            reference = fixture.v1_failure_root / "reference"
            target = reference / "stdout.txt"
            reference.chmod(0o755)
            target.unlink()
            target.symlink_to("stderr.txt")
            reference.chmod(0o555)
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "symbolic link",
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    fixture.selected_scene_id,
                    **fixture.options(),
                )

    def test_preflight_rejects_extra_v1_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            extra = (
                fixture.root
                / Path(*CONTROLLER.V1_FAILURE_ROOT.parts)
                / "open/n-8/seed-02"
            )
            extra.mkdir()
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "extra or missing destination",
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    fixture.selected_scene_id,
                    **fixture.options(),
                )

    def test_preflight_rejects_symlinked_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            viewer_bytes = fixture.viewer.read_bytes()
            alternate = fixture.viewer.with_name("alternate_viewer.py")
            alternate.write_bytes(viewer_bytes)
            fixture.viewer.unlink()
            fixture.viewer.symlink_to(alternate.name)
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError, "symbolic link"
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    fixture.selected_scene_id,
                    **fixture.options(),
                )

    def test_observer_environment_is_sanitized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            run_dir = fixture.root / "run"
            metadata = run_dir / "metadata.json"
            with mock.patch.dict(
                os.environ,
                {
                    "DANCERS_TRACE_OUT": "/sealed/trace",
                    "DANCERS_TEST_DISABLE_DIGEST_BOUNDARY_REUSE": "1",
                    "DISPLAY": ":99",
                    "UNCHANGED_VALUE": "yes",
                },
                clear=False,
            ):
                environment = CONTROLLER.sanitized_observer_environment(
                    fixture.plan, run_dir, metadata
                )
            self.assertNotIn(
                "DANCERS_TEST_DISABLE_DIGEST_BOUNDARY_REUSE",
                environment,
            )
            self.assertNotIn("DISPLAY", environment)
            self.assertEqual(
                environment["DANCERS_TRACE_OUT"],
                str(run_dir / "trace.jsonl"),
            )
            self.assertEqual(environment["DANCERS_TRACE_DETAIL"], "positions")
            self.assertEqual(environment["OMP_NUM_THREADS"], "1")
            self.assertEqual(environment["UNCHANGED_VALUE"], "yes")

    def test_mocked_processes_publish_only_after_exact_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            v1_before = fixture.v1_evidence_snapshot()
            runner = FakeProcessRunner()
            viewer_calls: list[tuple[Path, Path, Path]] = []

            def viewer_builder(
                reference: Path,
                optimized: Path,
                map_path: Path,
                **kwargs,
            ) -> SimpleNamespace:
                self.assertTrue(reference.is_file())
                self.assertTrue(optimized.is_file())
                self.assertTrue(map_path.is_file())
                self.assertEqual(
                    kwargs["environment_family"], "open"
                )
                self.assertEqual(
                    kwargs["environment_descriptors"],
                    fixture.environment_descriptors["open"],
                )
                viewer_calls.append((reference, optimized, map_path))
                replay = (
                    b"DIAGNOSTIC REPLAY\n"
                    b"NOT LIVE SOLVER TIMING\n"
                    b"RENDERING EXCLUDED FROM COMPUTE\n"
                )
                return SimpleNamespace(
                    html_bytes=replay,
                    output_bytes=len(replay),
                )

            manifest = CONTROLLER.execute_scene(
                fixture.plan,
                fixture.selected_scene_id,
                process_runner=runner,
                viewer_builder=viewer_builder,
                freeze_tree=lambda path: None,
                **fixture.options(),
            )
            self.assertEqual(runner.methods, ["reference", "optimized"])
            self.assertEqual(
                runner.config_arguments,
                [
                    fixture.plan.scenes_by_id[
                        fixture.selected_scene_id
                    ].config_cli_argument
                ]
                * 2,
            )
            self.assertTrue(
                all(
                    not Path(argument).is_absolute()
                    and not any(
                        character.isspace() for character in argument
                    )
                    for argument in runner.config_arguments
                )
            )
            self.assertTrue(
                all(
                    any(
                        character.isspace()
                        for character in str(working_directory)
                    )
                    for working_directory in runner.working_directories
                )
            )
            self.assertEqual(len(viewer_calls), 1)
            self.assertEqual(
                manifest["execution_epoch_id"],
                CONTROLLER.EXECUTION_EPOCH_ID,
            )
            self.assertEqual(
                manifest["config_cli_path_policy"],
                CONTROLLER.CONFIG_CLI_PATH_POLICY,
            )
            self.assertEqual(
                manifest["execution_lease"],
                {
                    "schema_version": (
                        CONTROLLER.EXECUTION_LEASE_SCHEMA
                    ),
                    "path": (
                        CONTROLLER.EXECUTION_LEASE_RELATIVE_PATH.as_posix()
                    ),
                    "policy": CONTROLLER.EXECUTION_LEASE_POLICY,
                    "held_during_execution": True,
                    "manual_recovery_if_present": True,
                },
            )
            self.assertEqual(
                manifest["scene"]["run_ids"],
                fixture.plan.scenes_by_id[
                    fixture.selected_scene_id
                ].run_ids,
            )
            self.assertTrue(manifest["diagnostic_only"])
            self.assertFalse(manifest["compute_claim"])
            self.assertFalse(manifest["timing_unlock"])
            self.assertTrue(
                manifest["pair_validation"]["semantic_trace_equal"]
            )
            self.assertTrue(
                manifest["pair_validation"]["diagnostics"][
                    "state_hex_equal"
                ]
            )
            self.assertEqual(
                manifest["scene"]["environment_descriptors"],
                fixture.environment_descriptors["open"],
            )
            self.assertEqual(
                manifest["pair_validation"]["diagnostics"][
                    "discordant_robot_frame_count"
                ],
                0,
            )
            rendered_config = (
                fixture.selected_output / "config.yaml"
            ).read_text(encoding="utf-8")
            self.assertIn("DISPLAY: 0", rendered_config)
            self.assertTrue(
                (fixture.selected_output / "manifest.json").is_file()
            )
            self.assertFalse(
                (fixture.selected_output / "failure.json").exists()
            )
            preflight_record = json.loads(
                (fixture.selected_output / "preflight.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertTrue(
                preflight_record["execution_lease"][
                    "held_during_execution"
                ]
            )
            self.assertFalse(fixture.lease_path.exists())
            manifest_text = json.dumps(manifest, sort_keys=True).lower()
            self.assertNotIn('"wall_', manifest_text)
            self.assertNotIn('"cpu_', manifest_text)
            self.assertNotIn('"rss_', manifest_text)
            self.assertEqual(
                fixture.v1_evidence_snapshot(), v1_before
            )

    def test_absolute_config_report_fails_before_optimized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            runner = FakeProcessRunner(
                config_file_override=str(
                    fixture.selected_output / "config.yaml"
                )
            )
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "literal config CLI argument",
            ):
                CONTROLLER.execute_scene(
                    fixture.plan,
                    fixture.selected_scene_id,
                    process_runner=runner,
                    viewer_builder=lambda *args, **kwargs: None,
                    freeze_tree=lambda path: None,
                    **fixture.options(),
                )
            self.assertEqual(runner.methods, ["reference"])
            self.assertFalse(
                (fixture.selected_output / "optimized").exists()
            )

    def test_raw_dot_alias_config_report_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            argument = fixture.plan.scenes_by_id[
                fixture.selected_scene_id
            ].config_cli_argument
            runner = FakeProcessRunner(
                config_file_override=f"./{argument}"
            )
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "literal config CLI argument",
            ):
                CONTROLLER.execute_scene(
                    fixture.plan,
                    fixture.selected_scene_id,
                    process_runner=runner,
                    viewer_builder=self.viewer_builder,
                    freeze_tree=lambda path: None,
                    **fixture.options(),
                )
            self.assertEqual(runner.methods, ["reference"])

    def test_summary_only_config_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            runner = FakeProcessRunner(
                summary_config_file_override=(
                    "results/wrong/config.yaml"
                )
            )
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "summary resolved_config.config_file differs",
            ):
                CONTROLLER.execute_scene(
                    fixture.plan,
                    fixture.selected_scene_id,
                    process_runner=runner,
                    viewer_builder=self.viewer_builder,
                    freeze_tree=lambda path: None,
                    **fixture.options(),
                )
            self.assertEqual(runner.methods, ["reference"])

    def test_trace_provenance_drift_fails_before_optimized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            runner = FakeProcessRunner(trace_metadata_drift=True)
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "trace provenance differs",
            ):
                CONTROLLER.execute_scene(
                    fixture.plan,
                    fixture.selected_scene_id,
                    process_runner=runner,
                    viewer_builder=lambda *args, **kwargs: None,
                    freeze_tree=lambda path: None,
                    **fixture.options(),
                )
            self.assertEqual(runner.methods, ["reference"])
            failure = json.loads(
                (fixture.selected_output / "failure.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                failure["execution_epoch_id"],
                CONTROLLER.EXECUTION_EPOCH_ID,
            )
            self.assertEqual(
                failure["config_cli_argument"],
                fixture.plan.scenes_by_id[
                    fixture.selected_scene_id
                ].config_cli_argument,
            )

    def test_semantic_mismatch_fails_without_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            v1_before = fixture.v1_evidence_snapshot()
            runner = FakeProcessRunner(mismatch_optimized=True)
            viewer_called = False
            freeze_calls: list[Path] = []

            def viewer_builder(*args, **kwargs):
                del args, kwargs
                nonlocal viewer_called
                viewer_called = True

            def failure_freezer(path: Path) -> None:
                self.assertTrue(fixture.lease_path.is_file())
                freeze_calls.append(path)

            with self.assertRaisesRegex(
                CONTROLLER.ControllerError, "semantic trace mismatch"
            ):
                CONTROLLER.execute_scene(
                    fixture.plan,
                    fixture.selected_scene_id,
                    process_runner=runner,
                    viewer_builder=viewer_builder,
                    freeze_tree=failure_freezer,
                    **fixture.options(),
                )
            self.assertEqual(runner.methods, ["reference", "optimized"])
            self.assertFalse(viewer_called)
            self.assertFalse(
                (fixture.selected_output / "replay.html").exists()
            )
            failure = json.loads(
                (fixture.selected_output / "failure.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(failure["status"], "failed-closed")
            self.assertFalse(failure["replay_published"])
            self.assertEqual(freeze_calls, [fixture.selected_output])
            self.assertEqual(
                failure["execution_lease"]["policy"],
                CONTROLLER.EXECUTION_LEASE_POLICY,
            )
            self.assertFalse(fixture.lease_path.exists())
            self.assertEqual(
                fixture.v1_evidence_snapshot(), v1_before
            )

    def test_nonzero_reference_stops_before_optimized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            runner = FakeProcessRunner(nonzero_reference=True)
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError, "exited with 7"
            ):
                CONTROLLER.execute_scene(
                    fixture.plan,
                    fixture.selected_scene_id,
                    process_runner=runner,
                    viewer_builder=lambda *args, **kwargs: None,
                    freeze_tree=lambda path: None,
                    **fixture.options(),
                )
            self.assertEqual(runner.methods, ["reference"])
            self.assertFalse(
                (fixture.selected_output / "optimized").exists()
            )
            self.assertFalse(
                (fixture.selected_output / "replay.html").exists()
            )

    def test_uncontained_process_is_never_sealed(self) -> None:
        for timed_out in (False, True):
            with (
                self.subTest(timed_out=timed_out),
                tempfile.TemporaryDirectory() as temporary,
            ):
                fixture = self.fixture(temporary)
                freezer = mock.Mock()

                def uncontained(*args, **kwargs):
                    del args, kwargs
                    return CONTROLLER.ProcessExit(
                        returncode=0,
                        timed_out=timed_out,
                        process_group_contained=False,
                    )

                with (
                    mock.patch.object(
                        CONTROLLER,
                        "_flush_capture_streams",
                        side_effect=OSError("post-return flush failure"),
                    ) as flush_capture,
                    self.assertRaisesRegex(
                        CONTROLLER.ProcessContainmentError,
                        "left a live process group",
                    ),
                ):
                    CONTROLLER.execute_scene(
                        fixture.plan,
                        fixture.selected_scene_id,
                        process_runner=uncontained,
                        viewer_builder=lambda *args, **kwargs: None,
                        freeze_tree=freezer,
                        **fixture.options(),
                    )
                flush_capture.assert_not_called()
                freezer.assert_not_called()
                self.assertTrue(fixture.selected_output.is_dir())
                self.assertFalse(
                    (fixture.selected_output / "failure.json").exists()
                )
                self.assertTrue(fixture.lease_path.is_file())
                blocked_runner = FakeProcessRunner()
                with self.assertRaisesRegex(
                    CONTROLLER.ControllerError,
                    "manual recovery is required",
                ):
                    CONTROLLER.execute_scene(
                        fixture.plan,
                        "open-n8-seed02",
                        process_runner=blocked_runner,
                        viewer_builder=self.viewer_builder,
                        **fixture.options(),
                    )
                self.assertEqual(blocked_runner.methods, [])

    def test_runner_exception_or_invalid_result_preserves_lease(self) -> None:
        def raises(*args, **kwargs):
            del args, kwargs
            raise RuntimeError("runner wrapper broke")

        def invalid(*args, **kwargs):
            del args, kwargs
            return object()

        for case, runner in (("exception", raises), ("invalid", invalid)):
            with (
                self.subTest(case=case),
                tempfile.TemporaryDirectory() as temporary,
            ):
                fixture = self.fixture(temporary)
                freezer = mock.Mock()
                with self.assertRaisesRegex(
                    CONTROLLER.ProcessContainmentError,
                    "containment is unproven",
                ):
                    CONTROLLER.execute_scene(
                        fixture.plan,
                        fixture.selected_scene_id,
                        process_runner=runner,
                        viewer_builder=self.viewer_builder,
                        freeze_tree=freezer,
                        **fixture.options(),
                    )
                freezer.assert_not_called()
                self.assertTrue(fixture.lease_path.is_file())
                self.assertFalse(
                    (fixture.selected_output / "failure.json").exists()
                )

    def test_destination_is_create_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            fixture.selected_output.mkdir(parents=True)
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "destination already exists",
            ):
                CONTROLLER.dry_run(
                    fixture.plan,
                    fixture.selected_scene_id,
                    **fixture.options(),
                )

    def test_process_group_cleanup_escalates_and_rechecks(self) -> None:
        process = mock.Mock()
        process.pid = 4242
        process.poll.return_value = 0
        process.wait.return_value = 0
        with (
            mock.patch.object(
                CONTROLLER,
                "_wait_for_group_exit",
                side_effect=(False, True),
            ) as wait_for_exit,
            mock.patch.object(CONTROLLER.os, "killpg") as kill_group,
        ):
            contained = CONTROLLER._terminate_group(process, 0.01)
        self.assertTrue(contained)
        self.assertEqual(wait_for_exit.call_count, 2)
        self.assertEqual(
            kill_group.call_args_list,
            [
                mock.call(4242, CONTROLLER.signal.SIGTERM),
                mock.call(4242, CONTROLLER.signal.SIGKILL),
            ],
        )

    def test_config_cli_target_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            scene = fixture.plan.scenes_by_id[
                fixture.selected_scene_id
            ]
            config_path = (
                fixture.root
                / Path(*CONTROLLER.PurePosixPath(
                    scene.config_cli_argument
                ).parts)
            )
            config_path.parent.mkdir(parents=True)
            target = fixture.root / "alternate-config.yaml"
            target.write_bytes(b"config\n")
            config_path.symlink_to(target)
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "symbolic link",
            ):
                CONTROLLER._prove_config_cli_target(
                    fixture.plan, scene, config_path
                )

    def test_config_cli_argument_must_match_independent_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            scene = fixture.plan.scenes_by_id[
                fixture.selected_scene_id
            ]
            wrong = fixture.root / "results/other/config.yaml"
            wrong.parent.mkdir(parents=True)
            wrong.write_bytes(b"config\n")
            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "independently derived",
            ):
                CONTROLLER._prove_config_cli_target(
                    fixture.plan, scene, wrong
                )

    def test_pinned_loader_ignores_poisoned_timestamp_pyc(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def poison_source(
                name: str,
                poisoned: bytes,
                source: bytes,
            ) -> tuple[Path, Path, bytes]:
                maximum = max(len(poisoned), len(source))
                poisoned = poisoned.rstrip(b"\n").ljust(
                    maximum - 1, b" "
                ) + b"\n"
                source = source.rstrip(b"\n").ljust(
                    maximum - 1, b" "
                ) + b"\n"
                path = root / f"{name}.py"
                path.write_bytes(poisoned)
                original = path.stat()
                cache = Path(importlib.util.cache_from_source(str(path)))
                cache.parent.mkdir(parents=True, exist_ok=True)
                py_compile.compile(
                    str(path),
                    cfile=str(cache),
                    doraise=True,
                    invalidation_mode=(
                        py_compile.PycInvalidationMode.TIMESTAMP
                    ),
                )
                path.write_bytes(source)
                os.utime(
                    path,
                    ns=(original.st_atime_ns, original.st_mtime_ns),
                )
                self.assertEqual(path.stat().st_size, original.st_size)
                return path, cache, source

            comparator_path, comparator_cache, comparator_source = (
                poison_source(
                    "trace_compare",
                    b"VALUE = 'poison'\n",
                    b"VALUE = 'source'\n",
                )
            )
            viewer_path, viewer_cache, viewer_source = poison_source(
                "viewer",
                (
                    b"import trace_compare\n"
                    b"VALUE = 'poison-viewer'\n"
                ),
                (
                    b"import trace_compare\n"
                    b"VALUE = trace_compare.VALUE\n"
                ),
            )
            cache_before = filesystem_snapshot(root / "__pycache__")
            comparator_name = "_test_pinned_comparator"
            viewer_name = "_test_pinned_viewer"
            try:
                comparator = CONTROLLER._load_module(
                    comparator_path,
                    comparator_name,
                    hashlib.sha256(comparator_source).hexdigest(),
                )
                self.assertEqual(comparator.VALUE, "source")
                viewer = CONTROLLER._load_module(
                    viewer_path,
                    viewer_name,
                    hashlib.sha256(viewer_source).hexdigest(),
                    module_bindings={"trace_compare": comparator},
                )
                self.assertEqual(viewer.VALUE, "source")
            finally:
                sys.modules.pop(comparator_name, None)
                sys.modules.pop(viewer_name, None)
            self.assertTrue(comparator_cache.is_file())
            self.assertTrue(viewer_cache.is_file())
            self.assertEqual(
                filesystem_snapshot(root / "__pycache__"),
                cache_before,
            )

    def test_viewer_passivity_rejects_write_and_cli_surfaces(self) -> None:
        badges = (
            '"""DIAGNOSTIC REPLAY; NOT LIVE SOLVER TIMING; '
            'RENDERING EXCLUDED FROM COMPUTE."""\n'
        )
        cases = {
            "writer": (
                badges
                + "from pathlib import Path\n"
                + "def build_replay_viewer(*args, **kwargs):\n"
                + '    Path("replay.html").write_text("unsafe")\n',
                "forbidden filesystem-write call write_text",
            ),
            "cli": (
                badges
                + "import argparse\n"
                + "def build_replay_viewer(*args, **kwargs):\n"
                + "    return None\n",
                "viewer imports a forbidden module",
            ),
        }
        for case, (source, message) in cases.items():
            with (
                self.subTest(case=case),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary)
                path = root / "scripts" / "comparison" / "viewer.py"
                path.parent.mkdir(parents=True)
                path.write_text(source, encoding="utf-8")
                candidate = CONTROLLER.Artifact(
                    root=root,
                    relative_path=CONTROLLER.PurePosixPath(
                        "scripts/comparison/viewer.py"
                    ),
                    path=path,
                    sha256=digest(path),
                    label="viewer",
                )
                with self.assertRaisesRegex(
                    CONTROLLER.ControllerError,
                    message,
                ):
                    CONTROLLER._verify_viewer_passivity(candidate)

    def test_freeze_tree_rejects_symlink_without_touching_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "output"
            output.mkdir()
            target = root / "outside.txt"
            target.write_text("outside\n", encoding="utf-8")
            target.chmod(0o600)
            (output / "escape").symlink_to(target)

            with self.assertRaisesRegex(
                CONTROLLER.ControllerError, "containing a symlink"
            ):
                CONTROLLER._freeze_tree(output)
            self.assertEqual(target.read_text(encoding="utf-8"), "outside\n")
            self.assertEqual(
                target.stat().st_mode & 0o777,
                0o600,
            )

            outside = root / "outside-directory"
            outside.mkdir()
            outside.chmod(0o700)
            outside_file = outside / "retained.txt"
            outside_file.write_text("retained\n", encoding="utf-8")
            outside_file.chmod(0o600)
            root_alias = root / "output-alias"
            root_alias.symlink_to(outside, target_is_directory=True)

            with self.assertRaisesRegex(
                CONTROLLER.ControllerError,
                "output root must be a real directory",
            ):
                CONTROLLER._freeze_tree(root_alias)
            self.assertEqual(outside.stat().st_mode & 0o777, 0o700)
            self.assertEqual(
                outside_file.stat().st_mode & 0o777,
                0o600,
            )


if __name__ == "__main__":
    unittest.main()
