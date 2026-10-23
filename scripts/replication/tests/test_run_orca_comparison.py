from __future__ import annotations

import contextlib
import io
import json
import pathlib
import shutil
import struct
import tempfile
import types
import unittest
from unittest import mock

from scripts.replication import analyze_orca_comparison as analyzer
from scripts.replication import run_orca_comparison as runner


class _Stdout:
    def __init__(self) -> None:
        self.buffer = io.BytesIO()


def _fake_runtime(root: pathlib.Path) -> runner.RuntimeBindings:
    return runner.RuntimeBindings(
        orca_binary=root / "orca",
        optimized_fidelity_binary=root / "optimized-fidelity",
        optimized_timing_binary=root / "optimized-timing",
        reference_fidelity_binary=root / "reference-fidelity",
        reference_timing_binary=root / "reference-timing",
        optimized_source_root=root / "optimized-source",
        reference_source_root=root / "reference-source",
        libomp=runner.DEFAULT_LIBOMP,
    )


def _make_executable(path: pathlib.Path, raw: bytes) -> dict[str, object]:
    path.write_bytes(raw)
    path.chmod(0o755)
    return {
        "bytes": len(raw),
        "sha256": runner.sha256_bytes(raw),
    }


def _f32_record(value: float) -> dict[str, object]:
    native = struct.unpack(">f", struct.pack(">f", value))[0]
    return {"hex": struct.pack(">f", native).hex(), "value": native}


def _f32_point(x: float, y: float) -> dict[str, object]:
    left, right = _f32_record(x), _f32_record(y)
    return {
        "hex": [left["hex"], right["hex"]],
        "value": [left["value"], right["value"]],
    }


def _orca_parameter_audit(
    row: dict[str, object], input_root: pathlib.Path
) -> dict[str, object]:
    scenario = json.loads((input_root / str(row["scenario_relpath"])).read_bytes())
    starts = [scenario["waypoints"][str(rid)][0] for rid in range(int(row["robot_count"]))]
    agents = []
    for rid, start in enumerate(starts):
        agents.append(
            {
                "initial_position": _f32_point(float(start[0]), float(start[1])),
                "initial_velocity": _f32_point(0.0, 0.0),
                "max_neighbors": int(row["robot_count"]) - 1,
                "maximum_speed": _f32_record(2.0),
                "neighbor_distance": _f32_record(20.0),
                "radius": _f32_record(1.0),
                "rid": rid,
                "time_horizon": _f32_record(1.5),
                "time_horizon_obstacles": _f32_record(1.5),
            }
        )
    return {
        "accepted": {
            "agents": agents,
            "num_agents": row["robot_count"],
            "num_obstacle_vertices": 0,
            "time_step": _f32_record(float(row["timestep_seconds"])),
        },
        "requested": {
            "common_sample_count": row["common_sample_count"],
            "goal_latch": "permanent after first native boundary at distance <= 1 m",
            "goal_tolerance_m": 1.0,
            "initial_velocity_mps": [0.0, 0.0],
            "maximum_speed_mps": 2.0,
            "max_neighbors": int(row["robot_count"]) - 1,
            "native_sample_stride": row["native_sample_stride"],
            "native_steps": row["steps"],
            "native_timestep_seconds": row["timestep_seconds"],
            "neighbor_distance_m": 20.0,
            "obstacle_count": 0,
            "robot_radius_m": 1.0,
            "stopping": "fixed ceiling; no early stop or agent deletion",
            "time_horizon_obstacles_seconds": 1.5,
            "time_horizon_seconds": 1.5,
        },
        "verified_through_public_getters": True,
    }


def _orca_summary_document(
    row: dict[str, object], input_root: pathlib.Path
) -> dict[str, object]:
    final_time, final_hex = runner._expected_orca_final_time(row)
    return {
        "all_goals_latched": False,
        "common_states_evaluated": row["common_sample_count"],
        "completed_native_steps": row["steps"],
        "final_native_global_time": {"hex": final_hex, "value": final_time},
        "finite_state": True,
        "goal_latches": [
            {"first_goal_native_step": None, "goal_latched": False, "rid": rid}
            for rid in range(int(row["robot_count"]))
        ],
        "input": {
            "attempt_sequence": row["sequence"],
            "cadence": row["cadence"],
            "cohort_id": row["cohort_id"],
            "input_manifest_sha256": runner.INPUT_MANIFEST_SHA256,
            "phase": row["phase"],
            "repetition": row["repetition"],
            "run_class": row["run_class"],
            "scenario_sha256": row["scenario_sha256"],
            "scene_id": row["scene_id"],
            "scene_set": row["scene_set"],
            "schedule_sha256": runner.SCHEDULE_SHA256,
            "study_id": runner.STUDY_ID,
        },
        "parameter_audit": _orca_parameter_audit(row, input_root),
        "schema_version": "dancers-orca-summary-v1",
        "source": dict(runner.ORCA_SOURCE),
        "termination": "fixed_ceiling",
        "trace_bytes": None,
        "trace_frames_written": 0,
        "trace_sha256": None,
    }


class OrcaComparisonRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.input_root = runner.DEFAULT_INPUT_ROOT
        cls.manifest, cls.rows, cls.snapshot = runner.load_frozen_plan(
            cls.input_root
        )

    def test_frozen_plan_has_exact_inventory_and_bindings(self) -> None:
        document = runner.plan_document(self.input_root)
        self.assertEqual(document["schema_version"], runner.PLAN_SCHEMA)
        self.assertEqual(document["inventory"]["processes"], 363)
        self.assertEqual(
            document["inventory"]["methods"],
            {"optimized": 119, "orca": 125, "reference": 119},
        )
        self.assertEqual(
            document["artifacts"]["input_manifest_sha256"],
            runner.INPUT_MANIFEST_SHA256,
        )
        self.assertEqual(
            document["artifacts"]["schedule_sha256"], runner.SCHEDULE_SHA256
        )
        self.assertEqual(
            document["artifacts"]["orca_build_capsule_manifest_sha256"],
            runner.ORCA_BUILD_CAPSULE_SHA256,
        )
        self.assertEqual(
            document["artifacts"]["expected_binaries"], runner.EXPECTED_BINARIES
        )
        self.assertEqual(
            document["artifacts"]["expected_libomp"], runner.EXPECTED_LIBOMP
        )
        self.assertEqual(
            document["artifacts"]["dancers_build_manifest"]["sha256"],
            runner.DANCERS_BUILD_MANIFEST_SHA256,
        )
        self.assertEqual(
            set(document["artifacts"]["protocols"]), set(runner.PROTOCOL_SHA256)
        )
        self.assertFalse(document["admission"]["execution_authorized"])
        self.assertFalse(document["policy"]["selective_retry_allowed"])
        self.assertEqual(
            [row["sequence"] for row in document["attempts"]], list(range(363))
        )

    def test_public_check_and_plan_are_read_only_and_never_spawn(self) -> None:
        stdout = _Stdout()
        with (
            mock.patch.object(runner.subprocess, "Popen") as popen,
            mock.patch.object(runner.sys, "stdout", stdout),
        ):
            self.assertEqual(runner.main(["check"]), 0)
            self.assertEqual(runner.main(["plan"]), 0)
        popen.assert_not_called()
        documents = stdout.buffer.getvalue().splitlines()
        self.assertEqual(len(documents), 2)
        first = json.loads(documents[0])
        self.assertEqual(first["status"], "accepted_without_execution")
        self.assertEqual(json.loads(documents[1])["inventory"]["processes"], 363)

    def test_public_execute_refuses_before_validation_write_or_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary).resolve() / "must-not-exist"
            stderr = io.StringIO()
            with (
                mock.patch.object(runner, "load_frozen_plan") as load,
                mock.patch.object(runner, "execute_authorized") as execute,
                mock.patch.object(runner.subprocess, "Popen") as popen,
                mock.patch.object(pathlib.Path, "mkdir") as mkdir,
                contextlib.redirect_stderr(stderr),
            ):
                status = runner.main(["execute", "--output-root", str(output)])
            self.assertEqual(status, 2)
            self.assertIn("authorization is deliberately absent", stderr.getvalue())
            self.assertFalse(output.exists())
            load.assert_not_called()
            execute.assert_not_called()
            popen.assert_not_called()
            mkdir.assert_not_called()

    def test_all_363_commands_are_derived_from_rows_with_exact_firewall(self) -> None:
        runtime = runner.default_runtime_bindings()
        identities = runner.EXPECTED_BINARIES
        with tempfile.TemporaryDirectory() as temporary:
            stage = pathlib.Path(temporary).resolve() / "attempt"
            (stage / "cwd").mkdir(parents=True)
            commands = []
            environments = []
            for row in self.rows:
                role = "timing" if row["run_class"] == "timing" else "fidelity"
                key, _binary = runner._runtime_binary(runtime, row["method"], role)
                argv, cwd, environment = runner.derive_command(
                    row,
                    runtime,
                    stage,
                    self.input_root,
                    identities[key],
                )
                self.assertEqual(cwd, stage / "cwd")
                commands.append((row, argv))
                environments.append((row, environment))

        orca = [(row, argv) for row, argv in commands if row["method"] == "orca"]
        dancers = [(row, argv) for row, argv in commands if row["method"] != "orca"]
        self.assertEqual(len(orca), 125)
        self.assertEqual(len(dancers), 238)
        self.assertEqual(
            [int(argv[argv.index("--attempt-sequence") + 1]) for _row, argv in orca],
            [row["sequence"] for row, _argv in orca],
        )
        self.assertTrue(all(argv[1] == "run" for _row, argv in orca))
        self.assertTrue(all(argv[1] == "--cfg" and len(argv) == 3 for _row, argv in dancers))
        self.assertEqual(sum("--trace-out" in argv for _row, argv in orca), 52)
        self.assertEqual(sum("--summary-out" in argv for _row, argv in orca), 125)
        templates = [runner.command_template(row) for row in self.rows]
        self.assertEqual([item["sequence"] for item in templates], list(range(363)))
        self.assertEqual(sum(item["expects_trace_on_success"] for item in templates), 144)
        self.assertTrue(
            all(item["cwd"] == "${ATTEMPT_STAGE}/cwd" for item in templates)
        )
        for row, environment in environments:
            observer_keys = {key for key in environment if key.startswith("DANCERS_")}
            if row["method"] != "orca" and row["run_class"] != "timing":
                self.assertEqual(
                    observer_keys,
                    {
                        "DANCERS_REQUIRE_PROVENANCE",
                        "DANCERS_RUN_METADATA",
                        "DANCERS_SUMMARY_OUT",
                        "DANCERS_TRACE_DETAIL",
                        "DANCERS_TRACE_OUT",
                    },
                )
            else:
                self.assertEqual(observer_keys, set())

    def test_input_tamper_is_rejected_against_external_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            copied = pathlib.Path(temporary).resolve() / "input"
            shutil.copytree(self.input_root, copied)
            manifest = copied / "manifest.json"
            manifest.write_bytes(manifest.read_bytes() + b" ")
            with self.assertRaisesRegex(runner.RunnerError, "input manifest"):
                runner.load_frozen_plan(copied)

    def test_binary_identity_is_exact_and_tamper_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            binary = root / "orca"
            expected = _make_executable(binary, b"frozen-binary")
            with mock.patch.dict(
                runner.EXPECTED_BINARIES, {"orca": expected}, clear=False
            ):
                actual = runner._require_binary(binary, "orca")
                self.assertEqual(actual["sha256"], expected["sha256"])
                binary.chmod(0o644)
                with self.assertRaisesRegex(runner.RunnerError, "identity differs"):
                    runner._require_binary(binary, "orca")
                binary.chmod(0o755)
                binary.write_bytes(b"tampered-binary")
                binary.chmod(0o755)
                with self.assertRaisesRegex(runner.RunnerError, "identity differs"):
                    runner._require_binary(binary, "orca")

    def test_invalid_permit_refuses_before_plan_output_or_spawn(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = pathlib.Path(temporary).resolve()
            output = parent / "session"
            permit = runner.ExecutionPermit(
                execution_authorized=False,
                study_id=runner.STUDY_ID,
                plan_sha256="0" * 64,
                input_manifest_sha256=runner.INPUT_MANIFEST_SHA256,
                schedule_sha256=runner.SCHEDULE_SHA256,
                authorization_manifest_sha256="1" * 64,
                admission_manifest_sha256="2" * 64,
                output_root=output,
            )
            process = mock.Mock()
            with (
                mock.patch.object(runner, "load_frozen_plan") as load,
                mock.patch.object(pathlib.Path, "mkdir") as mkdir,
            ):
                with self.assertRaisesRegex(runner.RunnerError, "not affirmative"):
                    runner.execute_authorized(
                        input_root=self.input_root,
                        output_root=output,
                        runtime=_fake_runtime(parent),
                        permit=permit,
                        process_runner=process,
                    )
            load.assert_not_called()
            mkdir.assert_not_called()
            process.assert_not_called()
            self.assertFalse(output.exists())

    def test_authorized_entry_forbids_process_runner_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = pathlib.Path(temporary).resolve()
            output = parent / "session"
            permit = runner.ExecutionPermit(
                execution_authorized=True,
                study_id=runner.STUDY_ID,
                plan_sha256="0" * 64,
                input_manifest_sha256=runner.INPUT_MANIFEST_SHA256,
                schedule_sha256=runner.SCHEDULE_SHA256,
                authorization_manifest_sha256="1" * 64,
                admission_manifest_sha256="2" * 64,
                output_root=output,
            )
            process = mock.Mock()
            with mock.patch.object(runner, "load_frozen_plan") as load:
                with self.assertRaisesRegex(runner.RunnerError, "override"):
                    runner.execute_authorized(
                        input_root=self.input_root,
                        output_root=output,
                        runtime=_fake_runtime(parent),
                        permit=permit,
                        process_runner=process,
                    )
            load.assert_not_called()
            process.assert_not_called()
            self.assertFalse(output.exists())

    def test_output_root_inside_frozen_input_refuses_before_plan_or_spawn(self) -> None:
        output = self.input_root / "forbidden-session"
        permit = runner.ExecutionPermit(
            execution_authorized=True,
            study_id=runner.STUDY_ID,
            plan_sha256="0" * 64,
            input_manifest_sha256=runner.INPUT_MANIFEST_SHA256,
            schedule_sha256=runner.SCHEDULE_SHA256,
            authorization_manifest_sha256="1" * 64,
            admission_manifest_sha256="2" * 64,
            output_root=output,
        )
        with (
            mock.patch.object(runner, "_plan_document_from_frozen") as plan,
            mock.patch.object(runner, "_validate_runtime") as runtime_validation,
            mock.patch.object(runner.subprocess, "Popen") as process,
        ):
            with self.assertRaisesRegex(runner.RunnerError, "disjoint"):
                runner.execute_authorized(
                    input_root=self.input_root,
                    output_root=output,
                    runtime=runner.default_runtime_bindings(),
                    permit=permit,
                )
        plan.assert_not_called()
        runtime_validation.assert_not_called()
        process.assert_not_called()
        self.assertFalse(output.exists())

    def test_authorized_plan_is_materialized_once_and_must_match_permit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = pathlib.Path(temporary).resolve()
            output = parent / "session"
            permit = runner.ExecutionPermit(
                execution_authorized=True,
                study_id=runner.STUDY_ID,
                plan_sha256="0" * 64,
                input_manifest_sha256=runner.INPUT_MANIFEST_SHA256,
                schedule_sha256=runner.SCHEDULE_SHA256,
                authorization_manifest_sha256="1" * 64,
                admission_manifest_sha256="2" * 64,
                output_root=output,
            )
            with (
                mock.patch.object(
                    runner,
                    "load_frozen_plan",
                    return_value=({}, self.rows, self.snapshot),
                ),
                mock.patch.object(
                    runner,
                    "_plan_document_from_frozen",
                    return_value={"changed": True},
                ) as plan,
                mock.patch.object(runner, "_validate_runtime") as runtime_validation,
                mock.patch.object(runner.subprocess, "Popen") as process,
            ):
                with self.assertRaisesRegex(runner.RunnerError, "plan digest differs"):
                    runner.execute_authorized(
                        input_root=self.input_root,
                        output_root=output,
                        runtime=_fake_runtime(parent),
                        permit=permit,
                    )
            plan.assert_called_once()
            runtime_validation.assert_not_called()
            process.assert_not_called()
            self.assertFalse(output.exists())

    def test_orca_nonzero_and_timeout_are_terminal_not_retried(self) -> None:
        row = next(
            item
            for item in self.rows
            if item["method"] == "orca" and item["run_class"] == "timing"
        )
        for label, child in (
            (
                "nonzero_exit",
                runner.ChildResult(7, None, False, 0.2, 0.0, 0.0, 0),
            ),
            (
                "timeout",
                runner.ChildResult(-15, signal_number(), True, 300.1, 1.0, 0.1, 2048),
            ),
        ):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                root = pathlib.Path(temporary).resolve()
                output = root / "session"
                (output / "attempts").mkdir(parents=True)
                runtime = _fake_runtime(root)
                expected = _make_executable(runtime.orca_binary, b"orca-test")

                calls = []

                def fake_process(**kwargs: object) -> runner.ChildResult:
                    calls.append(kwargs["argv"])
                    pathlib.Path(kwargs["stdout_path"]).write_bytes(b"partial\n")
                    pathlib.Path(kwargs["stderr_path"]).write_bytes(b"failure\n")
                    return child

                with mock.patch.dict(
                    runner.EXPECTED_BINARIES, {"orca": expected}, clear=False
                ):
                    record = runner._run_one(
                        row=row,
                        runtime=runtime,
                        runtime_record={"test": True},
                        input_root=self.input_root,
                        snapshot=self.snapshot,
                        output_root=output,
                        process_runner=fake_process,
                    )
                self.assertEqual(record["outcome"], label)
                self.assertFalse(record["fixed_work_verified"])
                self.assertEqual(len(calls), 1)
                final = output / "attempts" / runner._attempt_name(row)
                self.assertTrue((final / "record.json").is_file())
                self.assertFalse(
                    (output / "attempts" / f".staging-{runner._attempt_name(row)}").exists()
                )
                stored = json.loads((final / "record.json").read_bytes())
                self.assertEqual(stored, record)
                self.assertTrue(
                    all(not item["path"].startswith("attempts/.staging") for item in record["artifacts"].values())
                )

    def test_orca_mock_success_requires_exact_summary_fixed_work(self) -> None:
        row = next(
            item
            for item in self.rows
            if item["method"] == "orca"
            and item["run_class"] == "timing"
            and item["phase"] == "measured"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            output = root / "session"
            (output / "attempts").mkdir(parents=True)
            runtime = _fake_runtime(root)
            expected = _make_executable(runtime.orca_binary, b"orca-success")

            def fake_process(**kwargs: object) -> runner.ChildResult:
                argv = list(kwargs["argv"])
                pathlib.Path(kwargs["stdout_path"]).write_bytes(b"summary on stdout\n")
                pathlib.Path(kwargs["stderr_path"]).write_bytes(b"")
                summary_path = pathlib.Path(argv[argv.index("--summary-out") + 1])
                summary = _orca_summary_document(row, self.input_root)
                summary_path.write_bytes(
                    runner.canonical_json_bytes(summary, pretty=True)
                )
                return runner.ChildResult(0, None, False, 0.2, 0.1, 0.01, 4096)

            with mock.patch.dict(
                runner.EXPECTED_BINARIES, {"orca": expected}, clear=False
            ):
                record = runner._run_one(
                    row=row,
                    runtime=runtime,
                    runtime_record={"test": True},
                    input_root=self.input_root,
                    snapshot=self.snapshot,
                    output_root=output,
                    process_runner=fake_process,
                )
            self.assertEqual(record["outcome"], "completed")
            self.assertTrue(record["fixed_work_verified"])

    def test_zero_exit_orca_semantic_malformed_summary_is_artifact_failure(self) -> None:
        row = next(
            item
            for item in self.rows
            if item["method"] == "orca"
            and item["run_class"] == "timing"
            and item["phase"] == "measured"
        )
        for case in ("extra-key", "wrong-radius", "inconsistent-latch"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = pathlib.Path(temporary).resolve()
                output = root / "session"
                (output / "attempts").mkdir(parents=True)
                runtime = _fake_runtime(root)
                expected = _make_executable(runtime.orca_binary, b"orca-semantic")

                def fake_process(**kwargs: object) -> runner.ChildResult:
                    argv = list(kwargs["argv"])
                    pathlib.Path(kwargs["stdout_path"]).write_bytes(b"")
                    pathlib.Path(kwargs["stderr_path"]).write_bytes(b"")
                    summary = _orca_summary_document(row, self.input_root)
                    if case == "extra-key":
                        summary["unregistered"] = True
                    elif case == "wrong-radius":
                        summary["parameter_audit"]["accepted"]["agents"][0][
                            "radius"
                        ] = _f32_record(2.0)
                    else:
                        summary["goal_latches"][0]["goal_latched"] = True
                    pathlib.Path(argv[argv.index("--summary-out") + 1]).write_bytes(
                        runner.canonical_json_bytes(summary, pretty=True)
                    )
                    return runner.ChildResult(0, None, False, 0.2, 0.1, 0.01, 4096)

                with mock.patch.dict(
                    runner.EXPECTED_BINARIES, {"orca": expected}, clear=False
                ):
                    record = runner._run_one(
                        row=row,
                        runtime=runtime,
                        runtime_record={"test": True},
                        input_root=self.input_root,
                        snapshot=self.snapshot,
                        output_root=output,
                        process_runner=fake_process,
                    )
                self.assertEqual(record["outcome"], "artifact_failure")
                self.assertFalse(record["fixed_work_verified"])
                self.assertIn("analysis-contract", record["validation_error"])

    def test_dancers_timing_uses_isolated_copy_and_stdout_fixed_work(self) -> None:
        row = next(
            item
            for item in self.rows
            if item["method"] == "reference" and item["run_class"] == "timing"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            output = root / "session"
            (output / "attempts").mkdir(parents=True)
            runtime = _fake_runtime(root)
            runtime.reference_source_root.mkdir()
            expected = _make_executable(
                runtime.reference_timing_binary, b"reference-timing"
            )
            observed_cwd: list[pathlib.Path] = []

            def fake_process(**kwargs: object) -> runner.ChildResult:
                cwd = pathlib.Path(kwargs["cwd"])
                observed_cwd.append(cwd)
                copied_manifest = cwd.joinpath(*runner.INPUT_RELATIVE.parts) / "manifest.json"
                self.assertEqual(
                    runner.sha256_file(copied_manifest), runner.INPUT_MANIFEST_SHA256
                )
                pathlib.Path(kwargs["stdout_path"]).write_bytes(
                    f"timesteps: {row['steps']}\nFinal robot count: [{row['robot_count']}]\n".encode()
                )
                pathlib.Path(kwargs["stderr_path"]).write_bytes(b"")
                self.assertFalse(
                    any(key.startswith("DANCERS_") for key in kwargs["environment"])
                )
                return runner.ChildResult(0, None, False, 0.3, 0.2, 0.02, 8192)

            with mock.patch.dict(
                runner.EXPECTED_BINARIES,
                {"reference_timing": expected},
                clear=False,
            ):
                record = runner._run_one(
                    row=row,
                    runtime=runtime,
                    runtime_record={"test": True},
                    input_root=self.input_root,
                    snapshot=self.snapshot,
                    output_root=output,
                    process_runner=fake_process,
                )
            self.assertEqual(len(observed_cwd), 1)
            self.assertEqual(record["outcome"], "completed")
            self.assertTrue(record["fixed_work_verified"])
            self.assertIn("provenance.json", record["artifacts"])

    def test_success_without_fixed_step_evidence_is_terminal_artifact_failure(self) -> None:
        row = next(
            item
            for item in self.rows
            if item["method"] == "orca" and item["run_class"] == "timing"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            output = root / "session"
            (output / "attempts").mkdir(parents=True)
            runtime = _fake_runtime(root)
            expected = _make_executable(runtime.orca_binary, b"orca-invalid")

            def fake_process(**kwargs: object) -> runner.ChildResult:
                argv = list(kwargs["argv"])
                pathlib.Path(kwargs["stdout_path"]).write_bytes(b"")
                pathlib.Path(kwargs["stderr_path"]).write_bytes(b"")
                pathlib.Path(argv[argv.index("--summary-out") + 1]).write_bytes(
                    runner.canonical_json_bytes({}, pretty=True)
                )
                return runner.ChildResult(0, None, False, 0.1, 0.1, 0.0, 1)

            with mock.patch.dict(
                runner.EXPECTED_BINARIES, {"orca": expected}, clear=False
            ):
                record = runner._run_one(
                    row=row,
                    runtime=runtime,
                    runtime_record={"test": True},
                    input_root=self.input_root,
                    snapshot=self.snapshot,
                    output_root=output,
                    process_runner=fake_process,
                )
            self.assertEqual(record["outcome"], "artifact_failure")
            self.assertFalse(record["fixed_work_verified"])
            self.assertIn("fixed-work", record["validation_error"])
            final = output / "attempts" / runner._attempt_name(row)
            self.assertTrue((final / "record.json").is_file())
            self.assertFalse(
                (output / "attempts" / f".staging-{runner._attempt_name(row)}").exists()
            )

    def test_zero_exit_missing_or_malformed_summary_is_terminal_artifact_failure(self) -> None:
        row = next(
            item
            for item in self.rows
            if item["method"] == "orca" and item["run_class"] == "timing"
        )
        for case in ("missing", "malformed", "pathologically-deep"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = pathlib.Path(temporary).resolve()
                output = root / "session"
                (output / "attempts").mkdir(parents=True)
                runtime = _fake_runtime(root)
                expected = _make_executable(runtime.orca_binary, b"orca-bad-output")

                def fake_process(**kwargs: object) -> runner.ChildResult:
                    pathlib.Path(kwargs["stdout_path"]).write_bytes(b"")
                    pathlib.Path(kwargs["stderr_path"]).write_bytes(b"")
                    if case in {"malformed", "pathologically-deep"}:
                        argv = list(kwargs["argv"])
                        raw = (
                            b"{not-json}\n"
                            if case == "malformed"
                            else b"[" * 10000 + b"0" + b"]" * 10000
                        )
                        pathlib.Path(argv[argv.index("--summary-out") + 1]).write_bytes(raw)
                    return runner.ChildResult(0, None, False, 0.1, 0.1, 0.0, 1)

                with mock.patch.dict(
                    runner.EXPECTED_BINARIES, {"orca": expected}, clear=False
                ):
                    record = runner._run_one(
                        row=row,
                        runtime=runtime,
                        runtime_record={"test": True},
                        input_root=self.input_root,
                        snapshot=self.snapshot,
                        output_root=output,
                        process_runner=fake_process,
                    )
                self.assertEqual(record["outcome"], "artifact_failure")
                self.assertTrue(record["validation_error"])
                self.assertTrue(
                    (output / "attempts" / runner._attempt_name(row) / "record.json").is_file()
                )

    def test_zero_exit_malformed_trace_is_terminal_artifact_failure(self) -> None:
        row = next(
            item
            for item in self.rows
            if item["method"] == "orca"
            and item["run_class"] == "positions_fidelity"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            output = root / "session"
            (output / "attempts").mkdir(parents=True)
            runtime = _fake_runtime(root)
            expected = _make_executable(runtime.orca_binary, b"orca-bad-trace")

            def fake_process(**kwargs: object) -> runner.ChildResult:
                argv = list(kwargs["argv"])
                pathlib.Path(kwargs["stdout_path"]).write_bytes(b"")
                pathlib.Path(kwargs["stderr_path"]).write_bytes(b"")
                pathlib.Path(argv[argv.index("--summary-out") + 1]).write_text("{}")
                header = {
                    "input": {
                        "cadence": row["cadence"],
                        "input_manifest_sha256": runner.INPUT_MANIFEST_SHA256,
                        "scenario_sha256": row["scenario_sha256"],
                        "scene_id": row["scene_id"],
                        "scene_set": row["scene_set"],
                        "schedule_sha256": runner.SCHEDULE_SHA256,
                        "study_id": runner.STUDY_ID,
                    },
                    "numeric_identity": "native IEEE-754 binary32 values plus 8-digit hex bits",
                    "observation_rule": "initial state plus post-doStep states; integer common ticks define time",
                    "parameter_audit": {
                        "accepted": {
                            "num_agents": row["robot_count"],
                            "num_obstacle_vertices": 0,
                        },
                        "verified_through_public_getters": True,
                    },
                    "schema_version": "dancers-orca-trace-v1",
                    "source": runner.ORCA_SOURCE,
                    "type": "header",
                }
                trace_raw = runner.canonical_json_bytes(header) + b"".join(
                    runner.canonical_json_bytes({})
                    for _ in range(row["common_sample_count"])
                )
                pathlib.Path(argv[argv.index("--trace-out") + 1]).write_bytes(
                    trace_raw
                )
                return runner.ChildResult(0, None, False, 0.1, 0.1, 0.0, 1)

            with mock.patch.dict(
                runner.EXPECTED_BINARIES, {"orca": expected}, clear=False
            ):
                record = runner._run_one(
                    row=row,
                    runtime=runtime,
                    runtime_record={"test": True},
                    input_root=self.input_root,
                    snapshot=self.snapshot,
                    output_root=output,
                    process_runner=fake_process,
                )
            self.assertEqual(record["outcome"], "artifact_failure")
            self.assertIn("trace frame", record["validation_error"])

    def test_metric_reduction_failure_prevents_completed_classification(self) -> None:
        row = next(
            item
            for item in self.rows
            if item["method"] == "orca"
            and item["run_class"] == "positions_fidelity"
        )
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary).resolve() / "session"
            attempt = output / "candidate"
            attempt.mkdir(parents=True)
            for name in ("stdout.txt", "stderr.txt", "summary.json", "trace.ndjson"):
                (attempt / name).write_bytes(b"placeholder\n")
            trajectory = object()
            with (
                mock.patch.object(runner, "_validate_trace_structure"),
                mock.patch.object(runner, "_verify_fixed_work", return_value=True),
                mock.patch.object(
                    analyzer,
                    "_orca_summary",
                    return_value=(
                        {"goal_latches": [], "parameter_audit": {}},
                        {},
                    ),
                ),
                mock.patch.object(
                    analyzer, "normalize_orca_trace", return_value=trajectory
                ),
                mock.patch.object(
                    analyzer,
                    "analyze_trajectory",
                    side_effect=analyzer.AnalysisError("extreme metric reduction"),
                ) as reduce,
            ):
                with self.assertRaisesRegex(
                    runner.RunnerError, "analysis-contract validation failed"
                ):
                    runner._validate_success_outputs(
                        row,
                        attempt,
                        input_root=self.input_root,
                        output_root=output,
                        binary_identity={},
                    )
            reduce.assert_called_once_with(trajectory)

    def test_malformed_numeric_records_fail_closed_without_overflow(self) -> None:
        huge = 10**10000
        self.assertFalse(runner._finite_number(huge))
        self.assertFalse(
            runner._validate_float_record(
                {"hex": "00000000", "value": huge}, bits=32, dimensions=1
            )
        )
        self.assertFalse(
            runner._validate_float_record(
                {"hex": "7fe1ccf385ebc8a0", "value": 1e300},
                bits=32,
                dimensions=1,
            )
        )

    def test_orca_trace_header_requires_exact_numeric_and_observation_rules(self) -> None:
        row = next(item for item in self.rows if item["method"] == "orca")
        header = {
            "input": {
                "cadence": row["cadence"],
                "input_manifest_sha256": runner.INPUT_MANIFEST_SHA256,
                "scenario_sha256": row["scenario_sha256"],
                "scene_id": row["scene_id"],
                "scene_set": row["scene_set"],
                "schedule_sha256": runner.SCHEDULE_SHA256,
                "study_id": runner.STUDY_ID,
            },
            "numeric_identity": "native IEEE-754 binary32 values plus 8-digit hex bits",
            "observation_rule": "initial state plus post-doStep states; integer common ticks define time",
            "parameter_audit": {
                "accepted": {
                    "num_agents": row["robot_count"],
                    "num_obstacle_vertices": 0,
                },
                "verified_through_public_getters": True,
            },
            "schema_version": "dancers-orca-trace-v1",
            "source": runner.ORCA_SOURCE,
            "type": "header",
        }
        runner._validate_orca_trace_header(header, row)
        for key in ("numeric_identity", "observation_rule"):
            malformed = dict(header)
            malformed[key] = "different"
            with self.subTest(key=key), self.assertRaisesRegex(
                runner.RunnerError, "trace header differs"
            ):
                runner._validate_orca_trace_header(malformed, row)

    def test_result_tree_closure_reauthenticates_earlier_attempt_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = pathlib.Path(temporary).resolve() / "session"
            attempts_root = output / "attempts"
            attempts_root.mkdir(parents=True)
            root_payloads = {
                "input_manifest.json": b"input\n",
                "plan.json": b"plan\n",
                "records.ndjson": b"records\n",
                "schedule.json": b"schedule\n",
            }
            for name, raw in root_payloads.items():
                (output / name).write_bytes(raw)
            attempts = [
                {"method": "orca", "sequence": 0},
                {"method": "reference", "sequence": 1},
            ]
            records = []
            for row in attempts:
                directory = attempts_root / runner._attempt_name(row)
                directory.mkdir()
                artifact = directory / "stdout.txt"
                artifact.write_bytes(f"attempt-{row['sequence']}\n".encode())
                record = {
                    "artifacts": {
                        "stdout.txt": runner.file_identity(
                            artifact, relative_to=output
                        )
                    },
                    "sequence": row["sequence"],
                }
                (directory / "record.json").write_bytes(
                    runner.canonical_json_bytes(record)
                )
                records.append(record)

            with mock.patch.object(runner, "EXPECTED_PROCESSES", 2):
                runner._require_result_tree(
                    output,
                    attempts,
                    records,
                    root_payloads=root_payloads,
                    sealed=False,
                )
                first_artifact = attempts_root / "000-orca" / "stdout.txt"
                original_artifact = first_artifact.read_bytes()
                first_artifact.write_bytes(b"mutated during a later attempt\n")
                with self.assertRaisesRegex(
                    runner.RunnerError, "artifact identity differs"
                ):
                    runner._require_result_tree(
                        output,
                        attempts,
                        records,
                        root_payloads=root_payloads,
                        sealed=False,
                    )
                first_artifact.write_bytes(original_artifact)
                first_record = attempts_root / "000-orca" / "record.json"
                first_record.write_bytes(first_record.read_bytes() + b" ")
                with self.assertRaisesRegex(
                    runner.RunnerError, "record bytes differ"
                ):
                    runner._require_result_tree(
                        output,
                        attempts,
                        records,
                        root_payloads=root_payloads,
                        sealed=False,
                    )

    def test_dancers_fidelity_gate_binds_frames_trace_and_empty_obstacle_path(self) -> None:
        row = next(
            item
            for item in self.rows
            if item["method"] == "reference"
            and item["run_class"] == "positions_fidelity"
        )
        with tempfile.TemporaryDirectory() as temporary:
            attempt = pathlib.Path(temporary).resolve()
            (attempt / "stdout.txt").write_bytes(
                f"timesteps: {row['steps']}\nFinal robot count: [{row['robot_count']}]\n".encode()
            )
            summary = {
                "all_goals_reached": False,
                "configured_robot_count": row["robot_count"],
                "final_robot_count": row["robot_count"],
                "final_tick": row["steps"],
                "first_all_goals_tick": None,
                "frames_recorded": 2 * row["steps"],
                "goals_reached": 0,
                "goals_total": row["robot_count"],
                "iterations": 2 * row["steps"],
                "invalid_geometry_evaluations": 0,
                "invalid_robot_frames": 0,
                "maximum_robot_count": row["robot_count"],
                "mean_path_length_m_per_robot": 1.0,
                "metadata": {},
                "minimum_obstacle_clearance_m": None,
                "minimum_pair_clearance_m": 1.0,
                "nonfinite_numeric_observations": 0,
                "nonfinite_state_observations": 0,
                "obstacle_collision_events": 0,
                "obstacle_collision_exposure_seconds": 0.0,
                "pair_collision_events": 0,
                "pair_collision_exposure_seconds": 0.0,
                "paper_metrics": {},
                "per_robot_goal_tick": {},
                "per_robot_path_length_m": {
                    str(rid): 1.0 for rid in range(row["robot_count"])
                },
                "resolved_config": {
                    "configured_robot_count": row["robot_count"],
                    "max_timesteps": row["steps"],
                    "obstacle_file": "",
                    "obstacles_present": False,
                },
                "run_valid": True,
                "schema_version": "dancers-comparison-summary-v1",
                "termination_reason": "maximum_time",
                "total_path_length_m": float(row["robot_count"]),
                "trace_detail": row["trace_detail"],
            }
            (attempt / "provenance.json").write_bytes(
                runner.canonical_json_bytes({})
            )
            (attempt / "summary.json").write_bytes(
                runner.canonical_json_bytes(summary, pretty=True)
            )
            self.assertTrue(runner._verify_fixed_work(row, attempt))
            summary["frames_recorded"] -= 1
            (attempt / "summary.json").write_bytes(
                runner.canonical_json_bytes(summary, pretty=True)
            )
            self.assertFalse(runner._verify_fixed_work(row, attempt))
            summary["frames_recorded"] = 2 * row["steps"]
            summary["resolved_config"]["obstacle_file"] = "unexpected.png"
            (attempt / "summary.json").write_bytes(
                runner.canonical_json_bytes(summary, pretty=True)
            )
            self.assertFalse(runner._verify_fixed_work(row, attempt))

    def test_dancers_child_mutating_isolated_input_stops_session(self) -> None:
        row = next(
            item
            for item in self.rows
            if item["method"] == "optimized" and item["run_class"] == "timing"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            output = root / "session"
            (output / "attempts").mkdir(parents=True)
            runtime = _fake_runtime(root)
            expected = _make_executable(
                runtime.optimized_timing_binary, b"optimized-timing"
            )

            def fake_process(**kwargs: object) -> runner.ChildResult:
                cwd = pathlib.Path(kwargs["cwd"])
                copied_manifest = (
                    cwd.joinpath(*runner.INPUT_RELATIVE.parts) / "manifest.json"
                )
                copied_manifest.write_bytes(copied_manifest.read_bytes() + b" ")
                pathlib.Path(kwargs["stdout_path"]).write_bytes(b"")
                pathlib.Path(kwargs["stderr_path"]).write_bytes(b"")
                return runner.ChildResult(7, None, False, 0.1, 0.1, 0.0, 1)

            with mock.patch.dict(
                runner.EXPECTED_BINARIES,
                {"optimized_timing": expected},
                clear=False,
            ):
                with self.assertRaisesRegex(runner.RunnerError, "isolated optimized input"):
                    runner._run_one(
                        row=row,
                        runtime=runtime,
                        runtime_record={"test": True},
                        input_root=self.input_root,
                        snapshot=self.snapshot,
                        output_root=output,
                        process_runner=fake_process,
                    )

    def test_dancers_child_mutating_libomp_stops_session(self) -> None:
        row = next(
            item
            for item in self.rows
            if item["method"] == "optimized" and item["run_class"] == "timing"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            output = root / "session"
            (output / "attempts").mkdir(parents=True)
            runtime = _fake_runtime(root)
            expected_binary = _make_executable(
                runtime.optimized_timing_binary, b"optimized-timing"
            )
            runtime = runner.RuntimeBindings(
                **{
                    **runtime.__dict__,
                    "libomp": root / "libomp.dylib",
                }
            )
            expected_libomp = _make_executable(runtime.libomp, b"frozen-libomp")

            def fake_process(**kwargs: object) -> runner.ChildResult:
                pathlib.Path(kwargs["stdout_path"]).write_bytes(
                    f"timesteps: {row['steps']}\nFinal robot count: [{row['robot_count']}]\n".encode()
                )
                pathlib.Path(kwargs["stderr_path"]).write_bytes(b"")
                runtime.libomp.write_bytes(b"mutated-libomp")
                runtime.libomp.chmod(0o755)
                return runner.ChildResult(0, None, False, 0.1, 0.1, 0.0, 1)

            with (
                mock.patch.dict(
                    runner.EXPECTED_BINARIES,
                    {"optimized_timing": expected_binary},
                    clear=False,
                ),
                mock.patch.object(runner, "DEFAULT_LIBOMP", runtime.libomp),
                mock.patch.dict(runner.EXPECTED_LIBOMP, expected_libomp, clear=True),
            ):
                with self.assertRaisesRegex(runner.RunnerError, "libomp identity"):
                    runner._run_one(
                        row=row,
                        runtime=runtime,
                        runtime_record={"test": True},
                        input_root=self.input_root,
                        snapshot=self.snapshot,
                        output_root=output,
                        process_runner=fake_process,
                    )

    def test_runtime_validation_needs_roots_but_not_archived_input_mirrors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            runtime = _fake_runtime(root)
            runtime.reference_source_root.mkdir()
            runtime.optimized_source_root.mkdir()
            identities = {
                key: {"bytes": value["bytes"], "mode": "0755", "path": str(root / key), "sha256": value["sha256"]}
                for key, value in runner.EXPECTED_BINARIES.items()
            }
            with (
                mock.patch.object(
                    runner,
                    "load_frozen_plan",
                    return_value=({}, self.rows, self.snapshot),
                ),
                mock.patch.object(
                    runner,
                    "_require_binary",
                    side_effect=lambda _path, key: identities[key],
                ),
            ):
                record = runner.validate_runtime_bindings(runtime, self.input_root)
            self.assertEqual(set(record["binaries"]), set(runner.EXPECTED_BINARIES))
            self.assertEqual(record["libomp"]["sha256"], runner.EXPECTED_LIBOMP["sha256"])
            self.assertEqual(
                record["source_roots"]["reference"],
                str(runtime.reference_source_root),
            )
            self.assertFalse(
                runtime.reference_source_root.joinpath(*runner.INPUT_RELATIVE.parts).exists()
            )

    def test_default_process_runner_is_shell_false_new_session_and_wait4(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            stdout = root / "stdout"
            stderr = root / "stderr"
            process = types.SimpleNamespace(pid=1234)
            usage = types.SimpleNamespace(ru_maxrss=512, ru_utime=0.2, ru_stime=0.1)
            with (
                mock.patch.object(runner.subprocess, "Popen", return_value=process) as popen,
                mock.patch.object(runner, "_wait_child", return_value=(0, usage, False)) as wait,
            ):
                result = runner.run_process(
                    argv=["/private/tmp/frozen", "--fixed"],
                    cwd=root,
                    environment={"LC_ALL": "C"},
                    stdout_path=stdout,
                    stderr_path=stderr,
                    timeout_seconds=300.0,
                )
            self.assertEqual(result.returncode, 0)
            self.assertIs(popen.call_args.kwargs["shell"], False)
            self.assertIs(popen.call_args.kwargs["start_new_session"], True)
            self.assertIsNone(popen.call_args.kwargs["executable"])
            wait.assert_called_once_with(1234, 300.0)

    def test_interrupt_terminates_and_reaps_child_group_before_propagating(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            process = types.SimpleNamespace(pid=4321)
            with (
                mock.patch.object(runner.subprocess, "Popen", return_value=process),
                mock.patch.object(runner, "_wait_child", side_effect=KeyboardInterrupt),
                mock.patch.object(runner, "_terminate_and_reap_child") as terminate,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    runner.run_process(
                        argv=["/private/tmp/frozen", "--fixed"],
                        cwd=root,
                        environment={"LC_ALL": "C"},
                        stdout_path=root / "stdout",
                        stderr_path=root / "stderr",
                        timeout_seconds=300.0,
                    )
            terminate.assert_called_once_with(4321)


def signal_number() -> int:
    return 15


if __name__ == "__main__":
    unittest.main()
