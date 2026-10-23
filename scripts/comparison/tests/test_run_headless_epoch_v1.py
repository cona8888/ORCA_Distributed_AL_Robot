from __future__ import annotations

import ast
import copy
from dataclasses import replace
import fcntl
import hashlib
import importlib.util
import json
import marshal
import os
from pathlib import Path, PurePosixPath
import stat
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[3]
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))
from scripts.comparison import run_headless_epoch_v1 as runner


class PipePollTests(unittest.TestCase):
    def test_native_poll_accepts_descriptor_above_fd_setsize_and_rejects_invalid(self) -> None:
        read_descriptor, write_descriptor = os.pipe()
        high_descriptor = -1
        try:
            high_descriptor = fcntl.fcntl(
                read_descriptor,
                fcntl.F_DUPFD_CLOEXEC,
                1100,
            )
            os.close(read_descriptor)
            read_descriptor = -1
            os.set_blocking(high_descriptor, False)
            numbers = {"captured_read": high_descriptor}
            os.write(write_descriptor, b"x")
            self.assertEqual(
                runner._select_pipe_names(numbers, {"captured"}, 0.1),
                ("captured",),
            )
            self.assertEqual(os.read(high_descriptor, 1), b"x")
            self.assertEqual(
                runner._select_pipe_names(numbers, {"captured"}, 0.0),
                (),
            )
            os.close(high_descriptor)
            high_descriptor = -1
            with self.assertRaises(runner.ContainmentAbort):
                runner._select_pipe_names(numbers, {"captured"}, 0.0)
        finally:
            if read_descriptor >= 0:
                os.close(read_descriptor)
            if high_descriptor >= 0:
                os.close(high_descriptor)
            os.close(write_descriptor)


def identity(path: PurePosixPath, raw: bytes) -> runner.FileIdentity:
    return runner.FileIdentity(path, len(raw), hashlib.sha256(raw).hexdigest())


def identity_record(path: PurePosixPath, raw: bytes) -> dict[str, object]:
    return {
        "bytes": len(raw),
        "path": path.as_posix(),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def measurement(*, returncode: int = 0, timed_out: bool = False) -> runner.ProcessMeasurement:
    return runner.ProcessMeasurement(
        returncode=returncode,
        timed_out=timed_out,
        wall_seconds=0.01,
        user_cpu_seconds=0.001,
        system_cpu_seconds=0.001,
        peak_rss_raw=1,
        peak_rss_raw_unit="bytes",
        peak_rss_mib=1 / (1024 * 1024),
        stdout_bytes_observed=0,
        stderr_bytes_observed=0,
        stdout_truncated=False,
        stderr_truncated=False,
        process_group_contained=True,
        termination_events=(),
    )


class RepositoryFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.family = "open"
        self.count = 2
        self.seed = 1
        self.map_path = PurePosixPath("comparison/environments/open/calibration/map.png")
        self.scenario_path = runner.PLAN_ROOT / "missions/open/seed-0001/n-2/scenario.json"
        self.config_path = runner.PLAN_ROOT / "configs/open/n-2/seed-01/config.yaml"
        self.binary_paths = {
            ("reference", "fidelity"): PurePosixPath("build/reference-fidelity"),
            ("optimized", "fidelity"): PurePosixPath("build/optimized-fidelity"),
            ("reference", "timing"): PurePosixPath("build/reference-timing"),
            ("optimized", "timing"): PurePosixPath("build/optimized-timing"),
        }
        self.map_raw = b"synthetic-map"
        self.scenario_raw = b'{"synthetic":true}\n'
        self.config_raw = self.render_config()
        self.write(self.map_path, self.map_raw)
        self.write(self.scenario_path, self.scenario_raw)
        self.write(self.config_path, self.config_raw)
        for path in self.binary_paths.values():
            target = self.write(path, b"#!/bin/sh\nexit 0\n")
            target.chmod(0o755)
        runner_source = Path(runner.__file__).read_bytes()
        self.write(PurePosixPath("scripts/comparison/run_headless_epoch_v1.py"), runner_source)

    def write(self, relative: PurePosixPath, raw: bytes) -> Path:
        path = self.root.joinpath(*relative.parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def render_config(self) -> bytes:
        return (
            'SIMULATION_NAME: "calibration-open-n-2-seed-01"\n'
            "LOG_RESULTS: true\n"
            "ASSETS_DIR: comparison/environments/open/calibration\n"
            "OBSTACLE_FILE: map.png\n"
            "SCENARIO: file\n"
            f"SCENARIO_FILE: {self.scenario_path.as_posix()}\n"
            "NUM_ROBOTS: 2\n"
            "RNG_SEED: 1\n"
            "TIMESTEP: 0.1\n"
            "MAX_TIME: 100\n"
            "DISPLAY: 0\n"
            "DRAW_INTERROBOT: 0\n"
            "DRAW_PATH: 0\n"
            "DRAW_TRAJ: 0\n"
            "DRAW_WAYPOINTS: 0\n"
            "ROBOT_RADIUS: 1.0\n"
            "MAX_SPEED: 2.0\n"
        ).encode()

    def file_identity(self, relative: PurePosixPath, raw: bytes) -> runner.FileIdentity:
        return identity(relative, raw)

    def scene(self) -> dict[str, object]:
        return {
            "config": identity_record(self.config_path, self.config_raw),
            "effective_runtime_raster": {
                "bytes": 1_000_000,
                "encoding": "synthetic",
                "height_px": 1_000,
                "sha256": "4" * 64,
                "source_height_px": 256,
                "source_width_px": 256,
                "width_px": 1_000,
            },
            "family": self.family,
            "input_id": "1" * 64,
            "map": {
                **identity_record(self.map_path, self.map_raw),
                "size_px": 256,
            },
            "normalized_config_sha256": "2" * 64,
            "robot_count": self.count,
            "scenario": identity_record(self.scenario_path, self.scenario_raw),
            "scene_id": "calibration-open-n-2-seed-01",
            "seed": self.seed,
        }

    def binding(self, method: str, run_class: str) -> dict[str, object]:
        flavor = "timing" if run_class == "headless_timing" else "fidelity"
        path = self.binary_paths[(method, flavor)]
        raw = self.root.joinpath(*path.parts).read_bytes()
        return {
            "binary_key": f"{method}_{flavor}",
            **identity_record(path, raw),
        }

    def slot(
        self,
        *,
        run_class: str,
        method: str,
        order_index: int,
        pair_position: int,
        kind: str = "measured",
        repetition: int = 1,
        suffix: str = "",
    ) -> dict[str, object]:
        binding = self.binding(method, run_class)
        observed = run_class in ("digest_fidelity", "positions_replay")
        outputs = (
            ["attempt.json", "stdout.txt", "stderr.txt"]
            if kind == "warmup"
            else (
                [
                    "attempt.json",
                    "provenance.json",
                    "summary.json",
                    "trace.ndjson",
                    "stdout.txt",
                    "stderr.txt",
                ]
                if observed
                else [
                    "attempt.json",
                    "provenance.json",
                    "resource.json",
                    "stdout.txt",
                    "stderr.txt",
                ]
            )
        )
        directory = run_class.replace("_", "-")
        destination = (
            f"calibration/{directory}/open/warmup/{method}{suffix}"
            if kind == "warmup"
            else f"calibration/{directory}/open/n-2/seed-01/rep-{repetition}/{pair_position}-{method}{suffix}"
        )
        return {
            "binary_key": binding["binary_key"],
            "binary_sha256": binding["sha256"],
            "config": identity_record(self.config_path, self.config_raw),
            "controller_timesteps": 100,
            "destination": destination,
            "effective_runtime_raster_sha256": "4" * 64,
            "family": "open",
            "input_id": "1" * 64,
            "kind": kind,
            "map_sha256": hashlib.sha256(self.map_raw).hexdigest(),
            "method": method,
            "normalized_config_sha256": "2" * 64,
            "order_index": order_index,
            "outputs": outputs,
            "pair_position": pair_position,
            "pattern": "warmup" if kind == "warmup" else "ABA",
            "repetition": repetition,
            "robot_count": 2,
            "run_class": run_class,
            "scene_id": "calibration-open-n-2-seed-01",
            "seed": 1,
            "slot_id": hashlib.sha256(
                f"{run_class}:{method}:{kind}:{repetition}:{pair_position}:{suffix}".encode()
            ).hexdigest(),
            "timed": run_class == "headless_timing" and kind == "measured",
            "timeout_seconds": 2,
            "timestep_seconds": 0.1,
            "trace_detail": (
                None
                if kind == "warmup" or run_class == "headless_timing"
                else ("digest" if run_class == "digest_fidelity" else "positions")
            ),
        }

    def parse_plan(self) -> dict[str, object]:
        bindings = {
            method: {
                run_class: self.binding(method, run_class)
                for run_class in runner.RUN_CLASSES
            }
            for method in runner.METHODS
        }
        inventories: dict[str, object] = {}
        for run_class in runner.RUN_CLASSES:
            measured = [
                self.slot(
                    run_class=run_class,
                    method=method,
                    order_index=position,
                    pair_position=position,
                )
                for position, method in enumerate(("reference", "optimized"))
            ]
            inventories[run_class] = {
                "measured": measured,
                "measured_process_count": 2,
                "paired_scene_repetition_count": 1,
                "run_class": run_class,
                "timed": run_class == "headless_timing",
            }
        return {
            "method_bindings": bindings,
            "scenes": [self.scene()],
            "schedule": {
                "assignment_algorithm": {},
                "balance": {},
                "execution_order": [
                    "warmups",
                    "digest_fidelity",
                    "positions_replay",
                    "headless_timing",
                ],
                "inventories": inventories,
                "timing_gate": {
                    "on_prior_failure": "do-not-start-timing",
                    "required_prior_inventories": [
                        "digest_fidelity",
                        "positions_replay",
                    ],
                    "requires_authenticated_complete_attempts": True,
                    "runtime_override_permitted": False,
                },
                "warmups": {
                    "process_count": 0,
                    "slots": [],
                    "uniqueness_key": ["binary_key", "map_sha256"],
                },
            },
        }

    def direct_plan(self, attempts: list[runner.ScheduledAttempt]) -> runner.AuthenticatedPlan:
        live_runner = runner.build_runner_record(self.root)
        binaries = {
            str(attempt.raw["binary_key"]): {
                "algorithm_revision": (
                    "a" * 40 if attempt.method == "reference" else "b" * 40
                )
            }
            for attempt in attempts
        }
        value = {
            "controls": {"omp_threads": 1},
            "input_records": {
                "runtime_attestation": {
                    "content": {
                        "products": {
                            key.replace("_", "-"): value
                            for key, value in binaries.items()
                        },
                        "pixi_lock": {"sha256": "c" * 64},
                        "source_identity": {
                            "common_patch": {"format_email_sha256": "d" * 64}
                        },
                    }
                },
                "runner": {"content": live_runner},
            },
        }
        raw = runner.canonical_json_bytes(value)
        plan_path = self.write(runner.PLAN_ROOT / "epoch.json", raw)
        result = self.root.joinpath(*runner.RESULT_ROOT.parts)
        result.mkdir(parents=True, exist_ok=True)
        return runner.AuthenticatedPlan(
            repository_root=self.root,
            plan_path=plan_path,
            plan_bytes=raw,
            plan_sha256=hashlib.sha256(raw).hexdigest(),
            plan=value,
            schedule=tuple(attempts),
            result_namespace=runner.RESULT_ROOT,
        )

    def direct_attempt(
        self,
        *,
        run_class: str,
        method: str = "reference",
        sequence: int = 1,
        repetition: int = 1,
        suffix: str = "",
        kind: str = "measured",
    ) -> runner.ScheduledAttempt:
        if kind == "warmup":
            repetition = 0
        slot = self.slot(
            run_class=run_class,
            method=method,
            order_index=0,
            pair_position=0,
            repetition=repetition,
            suffix=suffix,
            kind=kind,
        )
        binding = self.binding(method, run_class)
        flavor = "timing" if run_class == "headless_timing" else "fidelity"
        binary_path = self.binary_paths[(method, flavor)]
        binary_raw = self.root.joinpath(*binary_path.parts).read_bytes()
        return runner.ScheduledAttempt(
            sequence=sequence,
            attempt_id=str(slot["slot_id"]),
            block_id=f"block-{sequence}",
            scene_id=str(slot["scene_id"]),
            run_class=run_class,
            attempt_kind=kind,
            method=method,
            repetition=repetition,
            order=str(slot["pattern"]),
            order_position=0,
            timeout_seconds=2.0,
            termination_grace_seconds=0.5,
            binary=identity(binary_path, binary_raw),
            config=identity(self.config_path, self.config_raw),
            map_artifact=identity(self.map_path, self.map_raw),
            scenario=identity(self.scenario_path, self.scenario_raw),
            expected_outputs=tuple(slot["outputs"]),
            raw=slot,
        )


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.temporary.name))
        self.fixture = RepositoryFixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _v2_private_plan(
        self, attempts: list[runner.ScheduledAttempt]
    ) -> runner.AuthenticatedPlan:
        build_root = self.root / "build/runtime-v2"
        product_ids = (
            "reference-timing",
            "reference-fidelity",
            "optimized-timing",
            "optimized-fidelity",
        )
        products: dict[str, object] = {}
        bound: list[runner.ScheduledAttempt] = []
        identities: dict[str, runner.FileIdentity] = {}
        for index, product_id in enumerate(product_ids, 1):
            relative = PurePosixPath(f"products/{product_id}/dancers")
            raw = f"synthetic-{product_id}\n".encode()
            path = build_root.joinpath(*relative.parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            path.chmod(0o555)
            digest = hashlib.sha256(raw).hexdigest()
            identities[product_id] = identity(
                PurePosixPath(path.relative_to(self.root).as_posix()), raw
            )
            method, role = product_id.split("-", 1)
            cdhash = f"{index:x}" * 40
            products[product_id] = {
                "algorithm_revision": ("a" if method == "reference" else "b") * 40,
                "applied_head": "c" * 40,
                "binary": {
                    "bytes": len(raw),
                    "mode": 0o555,
                    "path": relative.as_posix(),
                    "sha256": digest,
                },
                "build_role": role,
                "code_signature": {"cdhash": cdhash},
                "descriptor_test_binary": {},
                "expected_cdhash": cdhash,
                "expected_lc_uuid": f"0000000{index}-0000-0000-0000-00000000000{index}",
                "method": method,
                "observer_compiled": role == "fidelity",
                "source_tree": "d" * 40,
                "two_pass_binary_sha256": [digest, digest],
                "two_pass_descriptor_test_sha256": [digest, digest],
            }
        shim_relative = PurePosixPath(
            "products/headless-darwin-spawn-shim-v1/headless-darwin-spawn-shim-v1"
        )
        shim_raw = b"synthetic-shim\n"
        shim_path = build_root.joinpath(*shim_relative.parts)
        shim_path.parent.mkdir(parents=True, exist_ok=True)
        shim_path.write_bytes(shim_raw)
        shim_path.chmod(0o555)
        shim_sha = hashlib.sha256(shim_raw).hexdigest()
        for attempt in attempts:
            role = "timing" if attempt.run_class == "headless_timing" else "fidelity"
            product_id = f"{attempt.method}-{role}"
            raw = dict(attempt.raw)
            raw["binary_key"] = product_id
            raw["binary_sha256"] = identities[product_id].sha256
            bound.append(
                replace(
                    attempt,
                    binary=identities[product_id],
                    raw=raw,
                )
            )
        attestation = {
            "build": {
                "build_root": str(build_root),
                "host_bound": True,
                "manifest": {},
                "seal": {},
                "static_verifier_manifest_sha256": "e" * 64,
            },
            "classification": {},
            "design": {},
            "evidence_boundary": [],
            "implementation": {},
            "operations": {},
            "pixi_lock": {"bytes": 1, "path": "pixi.lock", "sha256": "c" * 64},
            "products": products,
            "repository": {},
            "runtime_artifact_root": {},
            "schema_version": runner.RUNTIME_ATTESTATION_SCHEMA,
            "source_identity": {
                "common_patch": {
                    "commit": "f" * 40,
                    "format_email_sha256": "d" * 64,
                    "full_index_diff_sha256": "e" * 64,
                    "history": [],
                }
            },
            "spawn_shim": {
                "binary": {
                    "bytes": len(shim_raw),
                    "mode": 0o555,
                    "path": shim_relative.as_posix(),
                    "sha256": shim_sha,
                },
                "code_signature": {"cdhash": "f" * 40},
                "expected_cdhash": "f" * 40,
                "expected_lc_uuid": "ffffffff-ffff-ffff-ffff-ffffffffffff",
                "product_id": runner.SPAWN_SHIM_PRODUCT_ID,
                "runtime_descriptor_contract": {},
                "source_identities": [],
                "two_pass_binary_sha256": [shim_sha, shim_sha],
            },
            "status": runner.RUNTIME_ATTESTATION_STATUS,
            "toolchain": {},
            "track_id": runner.TRACK_ID,
        }
        value = {
            "controls": {"omp_threads": 1},
            "input_records": {
                "runtime_attestation": {"content": attestation},
            },
        }
        raw = runner.canonical_json_bytes(value)
        plan_path = self.fixture.write(runner.PLAN_ROOT / "private-v2.json", raw)
        result_root = self.root.joinpath(*runner.RESULT_ROOT.parts)
        result_root.mkdir(parents=True, exist_ok=True)
        return runner.AuthenticatedPlan(
            repository_root=self.root,
            plan_path=plan_path,
            plan_bytes=raw,
            plan_sha256=hashlib.sha256(raw).hexdigest(),
            plan=value,
            schedule=tuple(bound),
            result_namespace=runner.RESULT_ROOT,
        )

    def _write_fake_digest_products(
        self, request: runner.CustodiedProcessRequest
    ) -> None:
        metadata_file = request.roles["metadata"]
        metadata = runner.strict_json_bytes(
            os.pread(metadata_file.descriptor, metadata_file.identity.size, 0),
            "synthetic metadata",
        )
        resolved = {
            "configured_robot_count": request.runtime.num_robots,
            "display": False,
            "max_timesteps": 100,
            "rng_seed": request.runtime.rng_seed,
            "robot_radius_m": 1.0,
            "timestep_seconds": runner.RUNTIME_TIMESTEP_SECONDS,
        }
        records: list[object] = [
            {
                "metadata": metadata,
                "observer": {
                    "strict_provenance_required": True,
                    "timing_eligible": False,
                    "trace_detail": "digest",
                },
                "resolved_config": resolved,
                "schema_version": "dancers-comparison-trace-v3",
                "type": "header",
            }
        ]
        metrics = {key: 0 for key in runner.TRACE_METRIC_KEYS}
        metrics["robot_count"] = request.runtime.num_robots
        rng = {key: "state" for key in runner.TRACE_RNG_KEYS}
        simulator = {key: 0 for key in runner.TRACE_SIMULATOR_KEYS}
        for index in range(200):
            phase = "pre_update" if index % 2 == 0 else "post_update"
            records.append(
                {
                    "metrics": metrics,
                    "phase": phase,
                    "rng_state": rng,
                    "robots": [
                        {
                            "rid": rid,
                            "state_schema": "robot-semantic-digest-v2",
                            "state_sha256": f"{rid + 3:x}" * 64,
                        }
                        for rid in range(request.runtime.num_robots)
                    ],
                    "simulated_time_seconds": index * 0.05,
                    "simulator_state": simulator,
                    "tick": index // 2 + (phase == "post_update"),
                    "type": "frame",
                }
            )
        trace_raw = b"".join(runner.canonical_json_bytes(item) for item in records)
        runner._write_all(request.roles["trace"].descriptor, trace_raw, "fake trace")
        summary_raw = runner.canonical_json_bytes(
            {
                "frames_recorded": 200,
                "metadata": metadata,
                "resolved_config": resolved,
                "schema_version": "dancers-comparison-summary-v1",
                "trace_detail": "digest",
                "trace_path": request.roles["trace"].logical_path,
            }
        )
        runner._write_all(
            request.roles["summary"].descriptor,
            summary_raw,
            "fake summary",
        )

    def test_canonical_json_and_duplicate_rejection(self) -> None:
        raw = runner.canonical_json_bytes({"z": "é", "a": False})
        self.assertEqual(raw, b'{"a":false,"z":"\xc3\xa9"}\n')
        self.assertEqual(runner.strict_json_bytes(raw, "record"), {"a": False, "z": "é"})
        with self.assertRaises(runner.RunnerError):
            runner.strict_json_bytes(b'{"x":1,"x":1}\n', "record")

    def test_legacy_pathname_environments_are_categorically_disabled(self) -> None:
        trace = self.root / "trace"
        summary = self.root / "summary"
        metadata = self.root / "metadata"
        for run_class in runner.RUN_CLASSES:
            with self.subTest(run_class=run_class):
                with self.assertRaisesRegex(
                    runner.InfrastructureAbort,
                    "pathname attempt environments are disabled",
                ):
                    runner.clean_attempt_environment(
                        run_class,
                        trace_path=trace,
                        summary_path=summary,
                        metadata_path=metadata,
                    )

    def test_legacy_measured_process_cannot_spawn(self) -> None:
        script = self.root / "emit"
        script.write_bytes(b"#!/bin/sh\nprintf 12345678901234567890\nprintf err >&2\n")
        script.chmod(0o755)
        with self.assertRaisesRegex(
            runner.InfrastructureAbort,
            "legacy measured-process execution is disabled",
        ):
            runner.run_measured_process(
                [str(script)],
                cwd=self.root,
                environment=dict(runner.ENVIRONMENT_BASE),
                stdout_path=self.root / "stdout",
                stderr_path=self.root / "stderr",
                timeout_seconds=2,
                termination_grace_seconds=0.2,
                capture_limit_bytes=10,
            )
        self.assertFalse((self.root / "stdout").exists())
        self.assertFalse((self.root / "stderr").exists())

    def test_timeout_terms_and_reaps_process_group(self) -> None:
        with self.assertRaises(runner.InfrastructureAbort):
            runner.run_measured_process(
                ["/bin/sleep", "5"],
                cwd=self.root,
                environment=dict(runner.ENVIRONMENT_BASE),
                stdout_path=self.root / "stdout-timeout",
                stderr_path=self.root / "stderr-timeout",
                timeout_seconds=0.05,
                termination_grace_seconds=0.2,
            )
        self.assertFalse((self.root / "stdout-timeout").exists())

    def test_capture_fsync_and_close_failures_never_return_measurement(self) -> None:
        script = self.root / "quiet"
        script.write_bytes(b"#!/bin/sh\nexit 0\n")
        script.chmod(0o755)
        with mock.patch.object(runner.os, "fsync", side_effect=AssertionError("fsync")) as fsync:
            with self.assertRaises(runner.InfrastructureAbort):
                runner.run_measured_process(
                    [str(script)],
                    cwd=self.root,
                    environment=dict(runner.ENVIRONMENT_BASE),
                    stdout_path=self.root / "fsync-stdout",
                    stderr_path=self.root / "fsync-stderr",
                    timeout_seconds=2,
                    termination_grace_seconds=0.2,
                )
        fsync.assert_not_called()

        with mock.patch.object(Path, "open", side_effect=AssertionError("open")) as opened:
            with self.assertRaises(runner.InfrastructureAbort):
                runner.run_measured_process(
                    [str(script)],
                    cwd=self.root,
                    environment=dict(runner.ENVIRONMENT_BASE),
                    stdout_path=self.root / "close-stdout",
                    stderr_path=self.root / "close-stderr",
                    timeout_seconds=2,
                    termination_grace_seconds=0.2,
                )
        opened.assert_not_called()

    def test_literal_schedule_order_and_all_run_classes(self) -> None:
        parsed = runner._parse_schedule(self.fixture.parse_plan())
        self.assertEqual(
            [attempt.run_class for attempt in parsed],
            [
                "digest_fidelity",
                "digest_fidelity",
                "positions_replay",
                "positions_replay",
                "headless_timing",
                "headless_timing",
            ],
        )
        self.assertEqual([attempt.sequence for attempt in parsed], list(range(1, 7)))

    def test_bare_mission_and_config_paths_are_rejected(self) -> None:
        for field, bare in (
            ("scenario", "missions/open/seed-0001/n-2/scenario.json"),
            ("config", "configs/open/n-2/seed-01/config.yaml"),
        ):
            with self.subTest(field=field):
                plan = self.fixture.parse_plan()
                plan["scenes"][0][field]["path"] = bare
                if field == "config":
                    for inventory in plan["schedule"]["inventories"].values():
                        for slot in inventory["measured"]:
                            slot["config"]["path"] = bare
                with self.assertRaisesRegex(runner.RunnerError, "plan-root rebased"):
                    runner._parse_schedule(plan)

    def test_validation_rejects_before_repository_environment_read(self) -> None:
        raw = runner.canonical_json_bytes(
            {
                "input_records": {},
                "phase": "validation",
                "schema_version": runner.EPOCH_SCHEMA,
                "track_id": runner.TRACK_ID,
            }
        )
        with mock.patch.object(
            runner, "_read_repository_file", side_effect=AssertionError("environment read")
        ) as read:
            with self.assertRaisesRegex(runner.RunnerError, "remain locked"):
                runner._load_bound_freezer(raw, self.root)
        read.assert_not_called()

    def _bound_freezer_plan(self) -> tuple[bytes, Path]:
        source = (
            "import json\n"
            f"EPOCH_SCHEMA={runner.EPOCH_SCHEMA!r}\n"
            f"TRACK_ID={runner.TRACK_ID!r}\n"
            f"PHASE={runner.PHASE!r}\n"
            f"RUN_CLASSES={runner.RUN_CLASSES!r}\n"
            "def canonical_json_bytes(v):\n"
            " return (json.dumps(v,allow_nan=False,ensure_ascii=False,separators=(',', ':'),sort_keys=True)+'\\n').encode()\n"
            "def validate_epoch_bytes(raw):\n return json.loads(raw)\n"
        ).encode()
        source_path = self.fixture.write(runner.FREEZER_SOURCE_PATH, source)
        component = {
            "classification": {},
            "contract": {},
            "identity": identity_record(runner.FREEZER_SOURCE_PATH, source),
            "schema_version": "dancers-headless-freezer-record-v2",
            "track_id": runner.TRACK_ID,
        }
        record_raw = runner.canonical_json_bytes(component)
        self.fixture.write(runner.INPUT_RECORD_PATHS["successor_freezer"], record_raw)
        wrapper = {
            "bytes": len(record_raw),
            "content": component,
            "path": runner.INPUT_RECORD_PATHS["successor_freezer"].as_posix(),
            "schema_version": "dancers-headless-freezer-record-v2",
            "sha256": hashlib.sha256(record_raw).hexdigest(),
        }
        value = {
            "input_records": {"successor_freezer": wrapper},
            "phase": runner.PHASE,
            "schema_version": runner.EPOCH_SCHEMA,
            "track_id": runner.TRACK_ID,
        }
        return runner.canonical_json_bytes(value), source_path

    def test_bound_freezer_ignores_poisoned_pyc_and_detects_source_swap(self) -> None:
        raw, source_path = self._bound_freezer_plan()
        poison = (
            source_path.parent
            / "__pycache__"
            / f"freeze_headless_epoch_v2.{sys.implementation.cache_tag}.pyc"
        )
        poison.parent.mkdir()
        metadata = source_path.stat()
        poisoned_code = compile("raise RuntimeError('poisoned pyc executed')", str(source_path), "exec")
        poison.write_bytes(
            importlib.util.MAGIC_NUMBER
            + struct.pack("<III", 0, int(metadata.st_mtime), metadata.st_size)
            + marshal.dumps(poisoned_code)
        )
        module, candidate = runner._load_bound_freezer(raw, self.root)
        self.assertEqual(candidate["phase"], "calibration")
        self.assertEqual(module.validate_epoch_bytes(raw), candidate)
        source_path.write_bytes(source_path.read_bytes() + b"# swapped\n")
        with self.assertRaisesRegex(runner.RunnerError, "byte identity"):
            runner._load_bound_freezer(raw, self.root)

    def test_stable_repository_read_rejects_symlink(self) -> None:
        target = self.fixture.write(PurePosixPath("safe/target"), b"data")
        link = self.root / "safe/link"
        link.symlink_to(target)
        with self.assertRaises(runner.RunnerError):
            runner._read_repository_file(self.root, PurePosixPath("safe/link"), "link")

    def test_authentication_drift_aborts_before_attempt_process(self) -> None:
        attempt = self.fixture.direct_attempt(
            run_class="digest_fidelity", sequence=1, kind="warmup"
        )
        plan = self.fixture.direct_plan([attempt])
        self.root.joinpath(*self.fixture.config_path.parts).write_bytes(b"mutated")
        process = mock.Mock(side_effect=AssertionError("process spawned"))
        with self.assertRaisesRegex(runner.InfrastructureAbort, "input drift"):
            runner._execute_attempt(
                plan,
                attempt,
                self.root / "must-not-exist",
                process_runner=process,
                digest_success=set(),
                positions_success=set(),
                timing_inventory_open=False,
            )
        process.assert_not_called()
        self.assertFalse((self.root / "must-not-exist").exists())

    def test_loader_authenticates_declared_bytes_before_any_spawn(self) -> None:
        value = {
            "classification": {},
            "config_artifacts": [
                {
                    "bytes": 7,
                    "content_utf8": "missing",
                    "path": (runner.PLAN_ROOT / "configs/missing.yaml").as_posix(),
                    "sha256": "0" * 64,
                }
            ],
            "controls": {},
            "design": {},
            "epoch_id": "candidate",
            "failure_policy": {},
            "input_records": {},
            "method_bindings": {},
            "normalized_config": {},
            "operations": {},
            "output_grammar": {},
            "phase": runner.PHASE,
            "scenes": [],
            "schedule": {},
            "schema_version": runner.EPOCH_SCHEMA,
            "status": "candidate",
            "track_id": runner.TRACK_ID,
        }
        raw = runner.canonical_json_bytes(value)
        plan_path = self.fixture.write(runner.PLAN_ROOT / "candidate.json", raw)
        fake_freezer = SimpleNamespace(
            EpochFreezeError=ValueError,
            RUN_CLASSES=runner.RUN_CLASSES,
            validate_epoch_bytes=lambda _raw: value,
        )
        with (
            mock.patch.object(runner, "_load_bound_freezer", return_value=(fake_freezer, value)),
            mock.patch.object(
                runner,
                "run_custodied_process",
                side_effect=AssertionError("spawn"),
            ) as spawn,
        ):
            with self.assertRaises(runner.RunnerError):
                runner.load_authenticated_plan(plan_path, self.root, production=True)
        spawn.assert_not_called()
        self.assertFalse(hasattr(runner, "subprocess"))

    def test_live_frozen_candidate_preflight_authenticates_full_inventory(self) -> None:
        live_root = Path(__file__).resolve().parents[3]
        plan_path = live_root.joinpath(*runner.PLAN_ROOT.parts) / "plan.json"
        if not plan_path.is_file():
            self.skipTest("registered successor plan has not been materialized yet")
        with mock.patch.object(
            runner,
            "run_custodied_process",
            side_effect=AssertionError("spawn"),
        ) as spawn:
            authenticated = runner.load_authenticated_plan(
                plan_path, live_root, production=False
            )
        spawn.assert_not_called()
        self.assertEqual(len(authenticated.schedule), 3_520)
        self.assertEqual(
            [
                authenticated.schedule[index].run_class
                for index in (20, 1_520, 2_020)
            ],
            ["digest_fidelity", "positions_replay", "headless_timing"],
        )

    def _recording_process_runner(self, observed: list[dict[str, str]], *, failures: set[int] = set()):
        count = 0

        def run_process(_command, **kwargs):
            nonlocal count
            count += 1
            kwargs["stdout_path"].write_bytes(b"")
            kwargs["stderr_path"].write_bytes(b"")
            environment = kwargs["environment"]
            observed.append(dict(environment))
            if "DANCERS_TRACE_OUT" in environment:
                Path(environment["DANCERS_TRACE_OUT"]).write_bytes(b"placeholder\n")
                Path(environment["DANCERS_SUMMARY_OUT"]).write_bytes(b"{}\n")
            return measurement(returncode=1 if count in failures else 0)

        return run_process

    def test_execute_three_classes_gates_timing_and_writes_canonical_session(self) -> None:
        attempts = [
            self.fixture.direct_attempt(run_class="digest_fidelity", sequence=1),
            self.fixture.direct_attempt(run_class="positions_replay", sequence=2),
            self.fixture.direct_attempt(run_class="headless_timing", sequence=3),
        ]
        plan = self.fixture.direct_plan(attempts)
        session = self.root.joinpath(*runner.RESULT_ROOT.parts) / "session-a"
        with (
            mock.patch.object(
                runner,
                "run_custodied_process",
                side_effect=AssertionError("spawn"),
            ) as spawn,
            self.assertRaisesRegex(
                runner.InfrastructureAbort,
                "legacy pathname schedule execution is disabled",
            ),
        ):
            runner.execute_schedule(
                plan,
                session,
                process_runner=self._recording_process_runner([]),
                host_probe=runner._passing_host_probe,
            )
        spawn.assert_not_called()
        self.assertFalse(session.exists())

    def test_private_v2_pending_status_is_a_pre_capability_hard_guard(self) -> None:
        plan = self._v2_private_plan(
            [
                self.fixture.direct_attempt(
                    run_class="digest_fidelity",
                    sequence=1,
                    kind="warmup",
                    suffix="-descriptor",
                ),
                self.fixture.direct_attempt(
                    run_class="headless_timing",
                    sequence=2,
                    kind="warmup",
                    suffix="-descriptor",
                ),
            ]
        )
        session_path = self.root.joinpath(*runner.RESULT_ROOT.parts) / "private-session"
        ambiguity: list[str] = []
        process = mock.Mock(side_effect=AssertionError("process executed"))
        host = mock.Mock(side_effect=AssertionError("host probed"))
        with (
            mock.patch.object(
                runner.DescriptorSessionCustody,
                "acquire_from_repository",
                side_effect=AssertionError("session created"),
            ) as acquire,
            mock.patch.object(
                runner,
                "_authenticate_attested_executable",
                side_effect=AssertionError("executable opened"),
            ) as authenticate,
            self.assertRaisesRegex(
                runner.InfrastructureAbort, "activation receipt is absent"
            ),
        ):
            runner.execute_custodied_schedule(
                plan,
                session_path,
                process_runner=process,
                host_probe=host,
                ambiguity_terminal=ambiguity.append,
            )
        self.assertEqual(ambiguity, [])
        process.assert_not_called()
        host.assert_not_called()
        acquire.assert_not_called()
        authenticate.assert_not_called()
        self.assertFalse((session_path.parent / ".epoch-lease").exists())
        self.assertFalse(session_path.exists())

    def test_private_descriptor_route_rejects_v1_before_session_or_process(self) -> None:
        attempt = self.fixture.direct_attempt(
            run_class="digest_fidelity", sequence=1, kind="warmup"
        )
        plan = self.fixture.direct_plan([attempt])
        session_path = self.root.joinpath(*runner.RESULT_ROOT.parts) / "v1-rejected"
        process = mock.Mock(side_effect=AssertionError("process executed"))
        with self.assertRaisesRegex(runner.RunnerError, "v2 top-level schema"):
            runner.execute_custodied_schedule(
                plan,
                session_path,
                process_runner=process,
                host_probe=lambda _descriptor: runner._passing_host_probe(),
                ambiguity_terminal=[].append,
            )
        process.assert_not_called()
        self.assertFalse(session_path.exists())
        self.assertFalse((session_path.parent / ".epoch-lease").exists())

    def test_descriptor_outputs_are_absolute_but_recorded_map_stays_relative(self) -> None:
        attempt = self.fixture.direct_attempt(
            run_class="digest_fidelity",
            sequence=1,
            kind="warmup",
            suffix="-logical-paths",
        )
        result_root = self.root / "descriptor-results"
        result_root.mkdir()
        session_name = "logical-path-session"
        session_root = result_root / session_name
        repository = runner.RepositoryDescriptorCustody(self.root)
        session = runner.DescriptorSessionCustody.acquire(
            result_root,
            "5" * 64,
            session_name,
        )
        attempt_directory = session.create_attempt_path(
            runner._safe_relative(attempt.raw["destination"], "attempt destination")
        )
        staging = attempt_directory.create_directory(".descriptor-io-v1")
        prepared = None
        try:
            prepared = runner.prepare_descriptor_attempt(
                repository,
                attempt_directory,
                config=attempt.config,
                map_artifact=attempt.map_artifact,
                scenario=attempt.scenario,
                runtime=runner._descriptor_runtime_for_attempt(attempt),
                staging_directory=staging,
                metadata_bytes=b"{}\n",
                metadata_logical_path=runner._descriptor_logical_path(
                    session_root, attempt, "provenance.json"
                ),
                trace_logical_path=runner._descriptor_logical_path(
                    session_root, attempt, "trace.ndjson"
                ),
                summary_logical_path=runner._descriptor_logical_path(
                    session_root, attempt, "summary.json"
                ),
            )
            manifest = runner.strict_json_bytes(
                runner.build_descriptor_manifest_bytes(
                    prepared.roles,
                    runner._descriptor_runtime_for_attempt(attempt),
                ),
                "descriptor logical-path manifest",
            )
            roles = {record["role"]: record for record in manifest["roles"]}
            expected_trace = os.fspath(
                session_root
                / "attempts"
                / Path(attempt.raw["destination"])
                / "trace.ndjson"
            )
            self.assertEqual(roles["trace"]["logical_path"], expected_trace)
            self.assertTrue(Path(roles["trace"]["logical_path"]).is_absolute())
            self.assertEqual(
                roles["map"]["logical_path"],
                self.fixture.map_path.as_posix(),
            )
            self.assertFalse(Path(roles["map"]["logical_path"]).is_absolute())
        finally:
            if prepared is not None:
                prepared.close()
            staging.close()
            repository.close()
            session.close()

    def test_public_and_private_schedule_ast_have_no_legacy_capability_route(self) -> None:
        module = ast.parse(Path(runner.__file__).read_text(encoding="utf-8"))
        functions = {
            node.name: node
            for node in module.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        forbidden = {
            "EpochLease",
            "_execute_attempt",
            "_materialize_skipped_timing",
            "_seal_tree",
            "run_measured_process",
        }
        for name in ("execute_schedule", "execute_custodied_schedule"):
            calls = {
                node.func.id
                for node in ast.walk(functions[name])
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            self.assertEqual(calls & forbidden, set())
        public_calls = {
            node.func.id
            for node in ast.walk(functions["main"])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertNotIn("execute_custodied_schedule", public_calls)

    def test_observer_validation_is_structural_and_does_not_decide_task_validity(self) -> None:
        attempt = self.fixture.direct_attempt(run_class="digest_fidelity", sequence=1)
        plan = self.fixture.direct_plan([attempt])
        provenance = runner._attempt_provenance(plan, attempt)
        self.assertEqual(
            {
                "algorithm_revision",
                "binary_sha256",
                "common_patch_sha256",
                "config_sha256",
                "map_sha256",
                "omp_threads",
                "pixi_lock_sha256",
                "run_id",
                "scenario_sha256",
            }
            - set(provenance),
            set(),
        )
        self.assertEqual(provenance["omp_threads"], 1)
        attempt_dir = self.root / "observer"
        attempt_dir.mkdir()
        resolved = {
            "configured_robot_count": 2,
            "display": False,
            "max_timesteps": 100,
            "rng_seed": 1,
            "robot_radius_m": 1.0,
            "timestep_seconds": runner.RUNTIME_TIMESTEP_SECONDS,
        }
        header = {
            "metadata": provenance,
            "observer": {
                "strict_provenance_required": True,
                "timing_eligible": False,
                "trace_detail": "digest",
            },
            "resolved_config": resolved,
            "schema_version": "dancers-comparison-trace-v3",
            "type": "header",
        }
        metrics = {key: 0 for key in runner.TRACE_METRIC_KEYS}
        metrics["robot_count"] = 2
        rng = {key: "state" for key in runner.TRACE_RNG_KEYS}
        simulator = {key: 0 for key in runner.TRACE_SIMULATOR_KEYS}
        records = [header]
        for index in range(200):
            phase = "pre_update" if index % 2 == 0 else "post_update"
            records.append(
                {
                    "metrics": metrics,
                    "phase": phase,
                    "rng_state": rng,
                    "robots": [
                        {
                            "rid": rid,
                            "state_schema": "robot-semantic-digest-v2",
                            "state_sha256": f"{rid + 3:x}" * 64,
                        }
                        for rid in range(2)
                    ],
                    "simulated_time_seconds": index * 0.05,
                    "simulator_state": simulator,
                    "tick": index // 2 + (phase == "post_update"),
                    "type": "frame",
                }
            )
        trace_raw = b"".join(runner.canonical_json_bytes(record) for record in records)
        (attempt_dir / "trace.ndjson").write_bytes(trace_raw)
        (attempt_dir / "summary.json").write_bytes(
            runner.canonical_json_bytes(
                {
                    "frames_recorded": 200,
                    "metadata": provenance,
                    "resolved_config": resolved,
                    "run_valid": False,
                    "schema_version": "dancers-comparison-summary-v1",
                    "trace_detail": "digest",
                    "trace_path": str(attempt_dir / "trace.ndjson"),
                }
            )
        )
        products = runner._validate_observer_products(attempt_dir, provenance, attempt)
        self.assertEqual(products["artifact_authentication"], "passed")
        self.assertFalse(products["task_validity_determined"])
        self.assertEqual(
            resolved["timestep_seconds"], 0.10000000149011612
        )
        header["resolved_config"]["timestep_seconds"] = 0.1
        records[0] = header
        (attempt_dir / "trace.ndjson").write_bytes(
            b"".join(runner.canonical_json_bytes(record) for record in records)
        )
        with self.assertRaises(runner.ScientificAttemptFailure):
            runner._validate_observer_products(attempt_dir, provenance, attempt)
        header["resolved_config"]["timestep_seconds"] = runner.RUNTIME_TIMESTEP_SECONDS
        records[0] = header
        (attempt_dir / "trace.ndjson").write_bytes(
            b"".join(runner.canonical_json_bytes(record) for record in records[:-1])
        )
        with self.assertRaises(runner.ScientificAttemptFailure):
            runner._validate_observer_products(attempt_dir, provenance, attempt)

    def test_timing_needs_both_digest_and_positions(self) -> None:
        attempts = [
            self.fixture.direct_attempt(run_class="digest_fidelity", sequence=1),
            self.fixture.direct_attempt(run_class="headless_timing", sequence=2),
        ]
        plan = self.fixture.direct_plan(attempts)
        session = self.root.joinpath(*runner.RESULT_ROOT.parts) / "session-gate"
        with self.assertRaises(runner.InfrastructureAbort):
            runner.execute_schedule(
                plan,
                session,
                process_runner=self._recording_process_runner([]),
                host_probe=runner._passing_host_probe,
            )
        self.assertFalse(session.exists())

    def test_any_prior_fidelity_failure_closes_the_global_timing_gate(self) -> None:
        attempts = [
            self.fixture.direct_attempt(
                run_class="digest_fidelity", sequence=1, repetition=1, suffix="-a"
            ),
            self.fixture.direct_attempt(
                run_class="digest_fidelity", sequence=2, repetition=2, suffix="-b"
            ),
            self.fixture.direct_attempt(
                run_class="positions_replay", sequence=3, repetition=1, suffix="-c"
            ),
            self.fixture.direct_attempt(
                run_class="headless_timing", sequence=4, repetition=1, suffix="-d"
            ),
        ]
        plan = self.fixture.direct_plan(attempts)
        session = self.root.joinpath(*runner.RESULT_ROOT.parts) / "session-global-gate"
        passed = {
            "artifact_authentication": "passed",
            "summary_sha256": "a" * 64,
            "task_validity_determined": False,
            "trace_sha256": "b" * 64,
        }
        self.assertIsNotNone(passed)
        with self.assertRaises(runner.InfrastructureAbort):
            runner.execute_schedule(
                plan,
                session,
                process_runner=self._recording_process_runner([]),
                host_probe=runner._passing_host_probe,
            )
        self.assertFalse(session.exists())

    def test_scientific_process_failure_continues_complete_grid(self) -> None:
        attempts = [
            self.fixture.direct_attempt(
                run_class="digest_fidelity", sequence=1, kind="warmup", suffix="-a"
            ),
            self.fixture.direct_attempt(
                run_class="headless_timing", sequence=2, kind="warmup", suffix="-b"
            ),
        ]
        plan = self.fixture.direct_plan(attempts)
        session = self.root.joinpath(*runner.RESULT_ROOT.parts) / "session-failure"
        with self.assertRaises(runner.InfrastructureAbort):
            runner.execute_schedule(
                plan,
                session,
                process_runner=self._recording_process_runner([], failures={1}),
                host_probe=runner._passing_host_probe,
            )
        self.assertFalse(session.exists())

    def test_host_failure_aborts_retains_and_seals(self) -> None:
        attempts = [
            self.fixture.direct_attempt(
                run_class="digest_fidelity", sequence=1, kind="warmup", suffix="-a"
            ),
            self.fixture.direct_attempt(
                run_class="headless_timing", sequence=2, kind="warmup", suffix="-b"
            ),
        ]
        plan = self.fixture.direct_plan(attempts)
        calls = 0

        def host():
            nonlocal calls
            calls += 1
            return {
                "ac_power": calls == 1,
                "free_bytes": 32 * 1024 * 1024 * 1024,
                "free_bytes_ok": True,
                "thermal_nominal": True,
            }

        session = self.root.joinpath(*runner.RESULT_ROOT.parts) / "session-abort"
        with self.assertRaises(runner.InfrastructureAbort):
            runner.execute_schedule(
                plan,
                session,
                process_runner=self._recording_process_runner([]),
                host_probe=host,
            )
        self.assertEqual(calls, 0)
        self.assertFalse(session.exists())

    def test_host_probe_requires_exact_integer_free_space_observation(self) -> None:
        self.assertEqual(runner.MIN_HOST_FREE_BYTES, 20 * 1024 * 1024 * 1024)
        valid = {
            "ac_power": True,
            "free_bytes": runner.MIN_HOST_FREE_BYTES,
            "free_bytes_ok": True,
            "thermal_nominal": True,
        }
        self.assertEqual(runner._require_host(lambda: valid), valid)
        invalid = []
        missing = dict(valid)
        del missing["free_bytes"]
        invalid.append(missing)
        for value in (None, True, -1, 0, runner.MIN_HOST_FREE_BYTES - 1):
            mutated = dict(valid)
            mutated["free_bytes"] = value
            invalid.append(mutated)
        for probe in invalid:
            with self.subTest(probe=probe):
                with self.assertRaises(runner.InfrastructureAbort):
                    runner._require_host(lambda probe=probe: probe)

    def test_lease_is_create_exclusive_and_no_session_is_published(self) -> None:
        attempt = self.fixture.direct_attempt(
            run_class="digest_fidelity", sequence=1, kind="warmup"
        )
        plan = self.fixture.direct_plan([attempt])
        result_root = self.root.joinpath(*runner.RESULT_ROOT.parts)
        (result_root / ".epoch-lease").write_bytes(b"occupied")
        session = result_root / "session-lease"
        with self.assertRaises(runner.StorageAbort):
            runner.DescriptorSessionCustody.acquire(
                result_root,
                plan.plan_sha256,
                session.name,
            )
        self.assertFalse(session.exists())

    def test_production_execution_has_no_synthetic_host_default(self) -> None:
        plan = self.fixture.direct_plan([])
        with self.assertRaisesRegex(
            runner.InfrastructureAbort,
            "legacy pathname schedule execution is disabled",
        ):
            runner.execute_schedule(
                plan,
                self.root.joinpath(*runner.RESULT_ROOT.parts) / "session-no-probe",
            )

    def test_cli_is_source_guarded_before_plan_read_write_lease_probe_or_spawn(self) -> None:
        class Output:
            def __init__(self) -> None:
                import io

                self.buffer = io.BytesIO()

            def write(self, _value: str) -> int:
                self.buffer.write(_value.encode("utf-8"))
                return len(_value)

            def flush(self) -> None:
                return None

        missing_stderr = Output()
        with mock.patch.object(runner.sys, "stderr", missing_stderr):
            with self.assertRaises(SystemExit) as missing:
                runner.main([])
        self.assertEqual(missing.exception.code, 2)
        for preflight in (False, True):
            with self.subTest(preflight=preflight):
                output = Output()
                error = Output()
                session = self.root / f"must-not-exist-{preflight}"
                arguments = [
                    "--plan",
                    str(self.root / "must-not-read.json"),
                    "--session",
                    str(session),
                    "--repository-root",
                    str(self.root),
                ]
                if preflight:
                    arguments.append("--preflight-only")
                with (
                    mock.patch.object(
                        runner,
                        "load_authenticated_plan",
                        side_effect=AssertionError("plan read"),
                    ) as load,
                    mock.patch.object(
                        runner,
                        "_read_repository_file",
                        side_effect=AssertionError("repository read"),
                    ) as read,
                    mock.patch.object(
                        runner,
                        "_write_bytes_exclusive",
                        side_effect=AssertionError("write"),
                    ) as write,
                    mock.patch.object(
                        runner,
                        "EpochLease",
                        side_effect=AssertionError("lease"),
                    ) as lease,
                    mock.patch.object(
                        runner,
                        "_real_host_probe",
                        side_effect=AssertionError("host probe"),
                    ) as host,
                    mock.patch.object(
                        runner._RawDarwinPosixSpawner,
                        "spawn",
                        side_effect=AssertionError("spawn"),
                    ) as spawn,
                    mock.patch.object(runner.sys, "stdout", output),
                    mock.patch.object(runner.sys, "stderr", error),
                ):
                    code = runner.main(arguments)
                self.assertEqual(code, 1)
                load.assert_not_called()
                read.assert_not_called()
                write.assert_not_called()
                lease.assert_not_called()
                host.assert_not_called()
                spawn.assert_not_called()
                self.assertEqual(output.buffer.getvalue(), b"")
                self.assertEqual(
                    error.buffer.getvalue(),
                    b"headless epoch runner remains nonauthorizing; no plan read, output, host probe, or process is permitted\n",
                )
                self.assertFalse(session.exists())

    def test_runner_source_has_no_network_client_import(self) -> None:
        source = Path(runner.__file__).read_text(encoding="utf-8")
        for forbidden in ("import socket", "import urllib", "import requests", "import http.client"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
