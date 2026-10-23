from __future__ import annotations

import json
import pathlib
import shlex
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.replication import prepare_orca_build_capsule as capsule


class OrcaBuildCapsuleTests(unittest.TestCase):
    def test_frozen_input_and_orca_sequence_identity(self) -> None:
        root = capsule.REPO / "comparison/publication/dancers-orca-v1"
        manifest, attempts, snapshot = capsule.verify_input(root)
        self.assertEqual(manifest["study_id"], "dancers-orca-v1")
        self.assertEqual(len(attempts), 363)
        self.assertEqual(len(snapshot), 50)
        sequences = tuple(
            row["sequence"] for row in attempts if row["method"] == "orca"
        )
        self.assertEqual(sequences, capsule.EXPECTED_SEQUENCES)
        self.assertEqual(
            capsule.sha256_bytes(capsule.compact_json_bytes(list(sequences))),
            capsule.SEQUENCE_SHA256,
        )

    def test_wrapper_command_firewall_never_forms_run(self) -> None:
        with mock.patch.object(capsule.subprocess, "run") as run:
            with self.assertRaisesRegex(capsule.CapsuleError, "forbidden"):
                capsule.invoke_wrapper(
                    pathlib.Path("/private/tmp/fake-wrapper"),
                    "run",
                    [],
                    cwd=pathlib.Path("/private/tmp"),
                    env={},
                    expected_returncode=2,
                )
            run.assert_not_called()

    def test_wrapper_invocation_is_argv_only_and_shell_false(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"{}\n", stderr=b""
        )
        with mock.patch.object(capsule.subprocess, "run", return_value=completed) as run:
            actual = capsule.invoke_wrapper(
                pathlib.Path("/private/tmp/fake-wrapper"),
                "validate",
                ["--input-root", "/private/tmp/input", "--attempt-sequence", "2"],
                cwd=pathlib.Path("/private/tmp"),
                env={"LC_ALL": "C"},
                expected_returncode=0,
            )
        self.assertIs(actual, completed)
        argv = run.call_args.args[0]
        self.assertEqual(argv[1], "validate")
        self.assertNotIn("run", argv)
        self.assertIs(run.call_args.kwargs["shell"], False)

    def test_compile_command_normalization_and_openmp_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = pathlib.Path(temporary).resolve()
            build = root / "build-a"
            rvo2 = root / "RVO2"
            build.mkdir()
            (rvo2 / "src").mkdir(parents=True)
            (build / "rvo2/src").mkdir(parents=True)
            entries = []
            for name in sorted(capsule.EXPECTED_COMPILE_UNITS):
                path = (
                    capsule.REPO / "tools/orca_baseline/rvo2_runner.cpp"
                    if name == "rvo2_runner.cpp"
                    else capsule.REPO / "src/Sha256.cpp"
                    if name == "Sha256.cpp"
                    else rvo2 / "src" / name
                )
                if not path.exists():
                    path.touch()
                definitions = ""
                if name in {"rvo2_runner.cpp", "Sha256.cpp"}:
                    definitions = (
                        " -DDANCERS_SHA256_FORCE_PORTABLE=1"
                        f" -DORCA_INPUT_MANIFEST_SHA256={capsule.INPUT_MANIFEST_SHA256}"
                        f" -DORCA_RVO2_COMMIT={capsule.RVO2_COMMIT}"
                        f" -DORCA_RVO2_TREE={capsule.RVO2_TREE}"
                    )
                command = (
                    f"{shlex.quote(str(capsule.REPO / '.pixi/envs/default/bin/clang++'))}"
                    f" -I{shlex.quote(str(build / 'rvo2/src'))}"
                    f" -I{shlex.quote(str(rvo2 / 'src'))}"
                    f"{definitions}"
                    f" -c -o {shlex.quote(str(build / (name + '.o')))}"
                    f" {shlex.quote(str(path))}"
                )
                entries.append(
                    {"command": command, "directory": str(build), "file": str(path)}
                )
            (build / "compile_commands.json").write_text(json.dumps(entries))
            normalized = capsule.normalize_compile_commands(build, rvo2)
            encoded = json.dumps(normalized)
            self.assertIn("${BUILD_ROOT}", encoded)
            self.assertIn("${RVO2_SOURCE}", encoded)
            self.assertIn("${REPO}", encoded)
            entries[0]["command"] = entries[0]["command"].replace(
                " -c ", " -fopenmp -c ", 1
            )
            (build / "compile_commands.json").write_text(json.dumps(entries))
            with self.assertRaisesRegex(capsule.CapsuleError, "OpenMP"):
                capsule.normalize_compile_commands(build, rvo2)

    def test_validation_record_is_deeply_checked(self) -> None:
        input_root = capsule.REPO / "comparison/publication/dancers-orca-v1"
        _, attempts, _ = capsule.verify_input(input_root)
        row = attempts[capsule.EXPECTED_SEQUENCES[0]]
        scenario = json.loads((input_root / row["scenario_relpath"]).read_bytes())
        agents = []
        for rid in range(row["robot_count"]):
            agents.append(
                {
                    "initial_position": capsule.point_record(
                        scenario["waypoints"][str(rid)][0]
                    ),
                    "initial_velocity": capsule.point_record([0.0, 0.0]),
                    "max_neighbors": row["robot_count"] - 1,
                    "maximum_speed": capsule.f32_record(2.0),
                    "neighbor_distance": capsule.f32_record(20.0),
                    "radius": capsule.f32_record(1.0),
                    "rid": rid,
                    "time_horizon": capsule.f32_record(1.5),
                    "time_horizon_obstacles": capsule.f32_record(1.5),
                }
            )
        record = {
            "input": capsule.expected_input(row),
            "parameter_audit": {
                "accepted": {
                    "agents": agents,
                    "num_agents": row["robot_count"],
                    "num_obstacle_vertices": 0,
                    "time_step": capsule.f32_record(row["timestep_seconds"]),
                },
                "requested": capsule.expected_requested(row),
                "verified_through_public_getters": True,
            },
            "schema_version": "dancers-orca-validation-v1",
            "source": capsule.SOURCE_RECORD,
            "status": "accepted_without_simulation",
        }
        capsule.verify_validation_record(record, row, input_root)
        record["parameter_audit"]["accepted"]["agents"][0]["radius"] = (
            capsule.f32_record(1.25)
        )
        with self.assertRaisesRegex(capsule.CapsuleError, "agent audit"):
            capsule.verify_validation_record(record, row, input_root)

    def test_capsule_publication_is_create_only_and_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = pathlib.Path(temporary).resolve()
            output = parent / "capsule"
            payloads = {
                relative: f"payload:{relative}\n".encode()
                for relative in capsule.PAYLOAD_PATHS
            }
            manifest = {
                "capsule_id": capsule.CAPSULE_ID,
                "files": [
                    capsule.payload_identity(relative, payloads[relative])
                    for relative in capsule.PAYLOAD_PATHS
                ],
                "schema_version": capsule.SCHEMA,
            }
            digest = capsule.publish_capsule(
                output,
                payloads,
                manifest,
                validator=lambda stage, _digest: capsule.check_payload_inventory(
                    stage, manifest
                ),
            )
            self.assertEqual(
                (output / "manifest.sha256").read_bytes(),
                f"{digest}  manifest.json\n".encode(),
            )
            self.assertEqual(
                (output / "manifest.json").read_bytes(),
                capsule.canonical_json_bytes(manifest),
            )
            capsule.check_payload_inventory(output, manifest)
            with self.assertRaisesRegex(capsule.CapsuleError, "already exists"):
                capsule.publish_capsule(
                    output,
                    payloads,
                    manifest,
                    validator=lambda _stage, _digest: None,
                )

    def test_capsule_payload_paths_cannot_escape_staging_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = pathlib.Path(temporary).resolve()
            for index, relative in enumerate(("../escape", "/private/tmp/escape", "a/../../escape", "./value")):
                output = parent / f"capsule-{index}"
                with self.assertRaisesRegex(capsule.CapsuleError, "unsafe"):
                    capsule.publish_capsule(
                        output,
                        {relative: b"forbidden\n"},
                        {"files": []},
                        validator=lambda _stage, _digest: None,
                    )
                self.assertFalse(output.exists())
            self.assertFalse((parent / "escape").exists())

    def test_capsule_is_validated_before_exclusive_finalization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = pathlib.Path(temporary).resolve()
            output = parent / "capsule"
            payloads = {relative: b"value\n" for relative in capsule.PAYLOAD_PATHS}
            manifest = {
                "files": [
                    capsule.payload_identity(relative, payloads[relative])
                    for relative in capsule.PAYLOAD_PATHS
                ]
            }

            def reject(stage: pathlib.Path, digest: str) -> None:
                self.assertTrue(stage.is_dir())
                self.assertFalse(output.exists())
                self.assertEqual(capsule.sha256_file(stage / "manifest.json"), digest)
                raise capsule.CapsuleError("reject staged capsule")

            with self.assertRaisesRegex(capsule.CapsuleError, "reject staged"):
                capsule.publish_capsule(
                    output, payloads, manifest, validator=reject
                )
            self.assertFalse(output.exists())
            self.assertFalse(any(parent.iterdir()))

    def test_safe_git_disables_hooks_configs_and_optional_locks(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=b"value\n", stderr=b""
        )
        with mock.patch.object(capsule, "run_probe", return_value=completed) as probe:
            actual = capsule.safe_git(capsule.REPO, "status", "--porcelain=v1")
        self.assertIs(actual, completed)
        argv = probe.call_args.args[0]
        self.assertIn("--no-optional-locks", argv)
        self.assertIn("core.fsmonitor=false", argv)
        self.assertIn("core.hooksPath=/dev/null", argv)
        environment = probe.call_args.kwargs["env"]
        self.assertEqual(environment["GIT_CONFIG_GLOBAL"], "/dev/null")
        self.assertEqual(environment["GIT_CONFIG_NOSYSTEM"], "1")
        self.assertEqual(environment["GIT_OPTIONAL_LOCKS"], "0")

    def test_symlink_alias_is_rejected_without_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = pathlib.Path(temporary).resolve()
            real = parent / "real"
            alias = parent / "alias"
            real.mkdir()
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(capsule.CapsuleError, "symlink"):
                capsule.require_real_absolute(alias, "alias", directory=True)

    def test_one_json_line_is_strict(self) -> None:
        self.assertEqual(capsule.one_json_line(b'{"a":1}\n', "value"), {"a": 1})
        for invalid in (b'{"a":1}', b'{"a":1}\n\n', b'\n{"a":1}\n'):
            with self.assertRaises(capsule.CapsuleError):
                capsule.one_json_line(invalid, "value")


if __name__ == "__main__":
    unittest.main()
