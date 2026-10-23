from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = (
    REPO_ROOT / "scripts" / "comparison" / "run_visual_diagnostic.py"
)
SPEC = importlib.util.spec_from_file_location(
    "run_visual_diagnostic", MODULE_PATH
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


class VisualFixture:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.plan_dir = (
            self.root
            / "comparison"
            / "plans"
            / "simulator-demo"
            / "v1"
            / "visual-diagnostic-calibration-v1"
        )
        self.plan_path = self.plan_dir / "plan.json"
        self._make_tools()
        self._make_binaries()
        self._make_scenarios_and_environment()
        self._make_build_manifest()
        self._make_contract()
        self._make_plan()
        self.plan = CONTROLLER.load_plan(self.plan_path)

    def _write(self, relative: str, data: bytes) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def _make_tools(self) -> None:
        self.pixi = self._write("pixi.lock", b"version = 6\n")
        self.config = self._write(
            "config/comparison/laptop_benchmark.yaml",
            (
                'SIMULATION_NAME: "template"\n'
                "LOG_RESULTS: true\n"
                'ASSETS_DIR: "assets"\n'
                'OBSTACLE_FILE: "map.png"\n'
                "SCENARIO: file\n"
                'SCENARIO_FILE: "scenario.json"\n'
                "NUM_ROBOTS: 8\n"
                "RNG_SEED: 1\n"
                "MAX_TIME: 100\n"
                "TIMESTEP: 0.1\n"
                "DISPLAY: 1\n"
                "DRAW_INTERROBOT: 1\n"
                "DRAW_PATH: 1\n"
                "DRAW_TRAJ: 1\n"
                "DRAW_WAYPOINTS: 1\n"
            ).encode("utf-8"),
        )
        self.trace_compare = self._write(
            "scripts/comparison/trace_compare.py",
            (
                REPO_ROOT / "scripts/comparison/trace_compare.py"
            ).read_bytes(),
        )
        self.controller = self._write(
            "scripts/comparison/run_visual_diagnostic.py",
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
            "scripts/comparison/freeze_visual_diagnostic_plan.py",
            b"# frozen test plan freezer\n",
        )
        self.materializer = self._write(
            "scripts/comparison/materialize_capacity_scenario.py",
            b"# frozen test scenario materializer\n",
        )

    def _make_binaries(self) -> None:
        self.reference_binary = self._write(
            "build/comparison/trace-v3/build/reference-fidelity/dancers",
            b"reference observer binary\n",
        )
        self.optimized_binary = self._write(
            "build/comparison/trace-v3/build/optimized-fidelity/dancers",
            b"optimized observer binary\n",
        )
        self.reference_binary.chmod(0o755)
        self.optimized_binary.chmod(0o755)

    def _scenario(
        self, family: str, count: int, map_sha256: str, bank_id: str
    ) -> dict:
        return {
            "num_robots": count,
            "metadata": {
                "split": "calibration",
                "environment_id": family,
                "map_sha256": map_sha256,
                "prefix_robot_count": count,
                "capacity_bank_id": bank_id,
            },
            "decisions": {str(index): index % 2 for index in range(count)},
            "waypoints": {
                str(index): [
                    [float(index), float(index), 0.0],
                    [float(index + 1), float(index + 1), 0.0],
                ]
                for index in range(count)
            },
        }

    def _make_scenarios_and_environment(self) -> None:
        cases: list[dict] = []
        self.environment_descriptors: dict[str, dict] = {}
        self.scene_artifacts: dict[
            tuple[str, int], tuple[Path, str, Path, str]
        ] = {}
        for family_index, family in enumerate(CONTROLLER.FAMILIES):
            descriptor = {
                "obstacle_occupancy": 0.10 + family_index * 0.01,
                "boundary_density": 0.01 + family_index * 0.001,
                "boundary_box_counting": {
                    "dimension": 1.0 + family_index * 0.02,
                    "r_squared": 0.99,
                },
                "free_space": {
                    "component_count_4_connected": 1,
                    "reachable_fraction": 1.0,
                },
            }
            self.environment_descriptors[family] = descriptor
            map_path = self._write(
                f"comparison/environments/{family}/calibration/map.png",
                b"test-map-" + family.encode("ascii"),
            )
            map_sha = digest(map_path)
            bank_id = hashlib.sha256(
                f"{family}-bank".encode("ascii")
            ).hexdigest()
            prefixes = []
            for count in CONTROLLER.COUNTS:
                scenario_path = (
                    self.plan_dir / "scenarios" / family / f"n-{count}.json"
                )
                scenario = self._scenario(
                    family, count, map_sha, bank_id
                )
                write_canonical(scenario_path, scenario)
                scenario_sha = digest(scenario_path)
                prefixes.append(
                    {
                        "robot_count": count,
                        "sha256": scenario_sha,
                        "bytes": scenario_path.stat().st_size,
                    }
                )
                self.scene_artifacts[(family, count)] = (
                    map_path,
                    map_sha,
                    scenario_path,
                    scenario_sha,
                )
            cases.append(
                {
                    "family": family,
                    "split": "calibration",
                    "map": {
                        "path": f"{family}/calibration/map.png",
                        "sha256": map_sha,
                        "bytes": map_path.stat().st_size,
                    },
                    "effective_runtime_1000": {
                        "map_metrics": descriptor,
                    },
                    "capacity": {
                        "bank_id": bank_id,
                        "prefix_scenarios": prefixes,
                    },
                }
            )
        self.environment_manifest = (
            self.root / "comparison/environments/manifest.json"
        )
        write_canonical(
            self.environment_manifest,
            {
                "schema_version": 1,
                "artifact_hash": "SHA-256",
                "families": list(CONTROLLER.FAMILIES),
                "environments": cases,
            },
        )

    def _make_build_manifest(self) -> None:
        self.build_manifest = (
            self.root / "comparison/build_manifest.json"
        )
        self.build_record = {
            "schema_version": "dancers-comparison-build-manifest-v1",
            "repository": {
                "remote_count": 0,
                "origin_present": False,
                "reference_pin": CONTROLLER.REFERENCE_REVISION,
                "optimized_pin": CONTROLLER.OPTIMIZED_REVISION,
                "common_patch_format_email_sha256": "a" * 64,
                "common_patch_full_index_diff_sha256": "b" * 64,
            },
            "toolchain": {"pixi_lock_sha256": digest(self.pixi)},
            "gates": {
                "dual_clean_builds": "pass",
                "common_registered_tests_all_four_builds": "pass",
                "smoke_semantic_trace_identity": "pass",
                "calibration_permitted": True,
            },
            "builds": {
                "reference_fidelity": {
                    "observer_compiled": True,
                    "algorithm_revision": CONTROLLER.REFERENCE_REVISION,
                    "applied_head": "c" * 40,
                    "binary_path": self.reference_binary.relative_to(
                        self.root
                    ).as_posix(),
                    "binary_sha256": digest(self.reference_binary),
                },
                "optimized_fidelity": {
                    "observer_compiled": True,
                    "algorithm_revision": CONTROLLER.OPTIMIZED_REVISION,
                    "applied_head": "d" * 40,
                    "binary_path": self.optimized_binary.relative_to(
                        self.root
                    ).as_posix(),
                    "binary_sha256": digest(self.optimized_binary),
                },
            },
        }
        write_canonical(self.build_manifest, self.build_record)

    def _make_contract(self) -> None:
        self.contract_path = (
            self.root / "docs/simulator/simulator_contract_v1.json"
        )
        write_canonical(
            self.contract_path,
            {
                "contract_id": CONTROLLER.CONTRACT_ID,
                "visual_diagnostic": {
                    "track_id": CONTROLLER.TRACK_ID,
                    "initial_counts": list(CONTROLLER.COUNTS),
                    "initial_seeds": list(CONTROLLER.SEEDS),
                    "paired_scene_count": CONTROLLER.EXPECTED_SCENE_COUNT,
                    "fresh_process_attempt_count": (
                        CONTROLLER.EXPECTED_ATTEMPT_COUNT
                    ),
                    "timesteps": 100,
                    "timestep_seconds": 0.1,
                    "simulated_duration_seconds": 10,
                    "trace_detail": "positions",
                    "trace_schema": CONTROLLER.TRACE_SCHEMA,
                    "rendering_in_compute_measurement": False,
                    "process_policy": {
                        "fresh_process_per_method": True,
                        "serial_execution": True,
                        "viewer_active_during_solver": False,
                    },
                },
                "output_namespaces": {
                    "visual_diagnostic": (
                        "results/simulator-demo/v1/visual-diagnostic"
                    )
                },
                "identities": {
                    "build_manifest": {
                        "sha256": digest(self.build_manifest)
                    },
                    "environment_manifest": {
                        "sha256": digest(self.environment_manifest)
                    },
                    "pixi_lock_sha256": digest(self.pixi),
                    "initial_visual_binaries": {
                        "reference_fidelity_sha256": digest(
                            self.reference_binary
                        ),
                        "optimized_fidelity_sha256": digest(
                            self.optimized_binary
                        ),
                    },
                },
            },
        )
        self.contract_commit = "e" * 40

    def _make_plan(self) -> None:
        scenes = []
        for family in CONTROLLER.FAMILIES:
            for count in CONTROLLER.COUNTS:
                map_path, map_sha, scenario_path, scenario_sha = (
                    self.scene_artifacts[(family, count)]
                )
                for seed in CONTROLLER.SEEDS:
                    scenes.append(
                        {
                            "scene_id": (
                                f"{family}-n{count}-seed{seed:02d}"
                            ),
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
                            "output_relpath": (
                                f"{family}/n-{count}/seed-{seed:02d}"
                            ),
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
        return self.contract_path.read_bytes()

    def assert_contract_request(
        self, root: Path, commit: str, relative_path: str
    ) -> None:
        if root != self.root:
            raise AssertionError("wrong repository root")
        if commit != self.contract_commit:
            raise AssertionError("wrong contract commit")
        if relative_path != CONTROLLER.CONTRACT_PATH:
            raise AssertionError("wrong contract path")

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

    def options(self) -> dict:
        return {
            "controller_path": self.controller,
            "contract_blob_reader": self.contract_blob,
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
    ) -> None:
        self.mismatch_optimized = mismatch_optimized
        self.nonzero_reference = nonzero_reference
        self.methods: list[str] = []
        self.environments: list[dict[str, str]] = []

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
        del command, cwd, timeout, grace
        metadata_path = Path(environment["DANCERS_RUN_METADATA"])
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        method = metadata["implementation_id"]
        self.methods.append(method)
        self.environments.append(dict(environment))
        stdout.write(f"{method} complete\n".encode("utf-8"))
        stderr.write(b"")
        if method == "reference" and self.nonzero_reference:
            return CONTROLLER.ProcessExit(7, False)

        trace_path = Path(environment["DANCERS_TRACE_OUT"])
        summary_path = Path(environment["DANCERS_SUMMARY_OUT"])
        resolved = {
            "config_file": metadata["config_path"],
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
            "metadata": metadata,
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
        summary = {
            "schema_version": CONTROLLER.SUMMARY_SCHEMA,
            "trace_detail": "positions",
            "frames_recorded": 200,
            "final_tick": 100,
            "final_robot_count": count,
            "maximum_robot_count": count,
            "termination_reason": "maximum_time",
            "run_valid": True,
            "metadata": metadata,
            "trace_path": str(trace_path),
            "simulated_time_seconds": 10.0,
            "resolved_config": resolved,
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
        return VisualFixture(Path(temporary))

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

    def test_dry_run_verifies_everything_without_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            result = CONTROLLER.dry_run(
                fixture.plan,
                fixture.selected_scene_id,
                **fixture.options(),
            )
            self.assertTrue(result["full_crossing_verified"])
            self.assertEqual(result["scene_count"], 250)
            self.assertEqual(result["attempt_count"], 500)
            self.assertEqual(result["git_remote_count"], 0)
            self.assertFalse(fixture.selected_output.exists())
            self.assertFalse(
                (fixture.root / "results/simulator-demo").exists()
            )

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
            self.assertEqual(len(viewer_calls), 1)
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
            manifest_text = json.dumps(manifest, sort_keys=True).lower()
            self.assertNotIn('"wall_', manifest_text)
            self.assertNotIn('"cpu_', manifest_text)
            self.assertNotIn('"rss_', manifest_text)

    def test_semantic_mismatch_fails_without_replay(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self.fixture(temporary)
            runner = FakeProcessRunner(mismatch_optimized=True)
            viewer_called = False

            def viewer_builder(*args, **kwargs):
                del args, kwargs
                nonlocal viewer_called
                viewer_called = True

            with self.assertRaisesRegex(
                CONTROLLER.ControllerError, "semantic trace mismatch"
            ):
                CONTROLLER.execute_scene(
                    fixture.plan,
                    fixture.selected_scene_id,
                    process_runner=runner,
                    viewer_builder=viewer_builder,
                    freeze_tree=lambda path: None,
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

                with self.assertRaisesRegex(
                    CONTROLLER.ProcessContainmentError,
                    "left a live process group",
                ):
                    CONTROLLER.execute_scene(
                        fixture.plan,
                        fixture.selected_scene_id,
                        process_runner=uncontained,
                        viewer_builder=lambda *args, **kwargs: None,
                        freeze_tree=freezer,
                        **fixture.options(),
                    )
                freezer.assert_not_called()
                self.assertTrue(fixture.selected_output.is_dir())
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
