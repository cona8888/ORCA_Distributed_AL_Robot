from __future__ import annotations

import argparse
import copy
import contextlib
import io
import json
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from scripts.replication import prepare_orca_execution_capsule as capsule


class OrcaExecutionCapsuleTests(unittest.TestCase):
    def _payloads(self) -> dict[str, bytes]:
        return {
            relative: capsule.canonical_json_bytes(
                {"payload": relative, "prospective": True}
            )
            for relative in capsule.PAYLOAD_PATHS
        }

    def _file_manifest(self, payloads: dict[str, bytes]) -> dict[str, object]:
        return {
            "capsule_id": capsule.CAPSULE_ID,
            "files": [
                capsule.payload_identity(relative, payloads[relative])
                for relative in capsule.PAYLOAD_PATHS
            ],
            "schema_version": capsule.SCHEMA,
        }

    def _full_manifest(self, payloads: dict[str, bytes]) -> dict[str, object]:
        defaults = capsule.default_inputs()
        libomp_identity = capsule.file_identity(defaults.libomp)
        binary_paths = {
            "orca": defaults.runtime.orca_binary,
            "optimized_fidelity": defaults.runtime.optimized_fidelity_binary,
            "optimized_timing": defaults.runtime.optimized_timing_binary,
            "reference_fidelity": defaults.runtime.reference_fidelity_binary,
            "reference_timing": defaults.runtime.reference_timing_binary,
        }
        return {
            "authentication": {"rule": capsule.AUTHENTICATION_RULE},
            "capsule_id": capsule.CAPSULE_ID,
            "classification": capsule.CLASSIFICATION,
            "contracts": {
                "analysis_schema_version": "dancers-orca-analysis-contract-v1",
                "command_templates": capsule.EXPECTED_PROCESSES,
                "execution_plan_schema_version": "dancers-orca-execution-plan-v1",
                "result_session_schema_version": "dancers-orca-session-v1",
            },
            "dependencies": {
                "dancers": {
                    "committed_build_manifest": {
                        "path": str(defaults.dancers_build_manifest)
                    },
                    "libomp": libomp_identity,
                },
                "git": capsule.file_identity(capsule.GIT),
                "orca": {
                    "build_capsule_root": str(defaults.orca_build_capsule_root)
                },
                "pixi_lock": {},
            },
            "files": [
                capsule.payload_identity(relative, payloads[relative])
                for relative in capsule.PAYLOAD_PATHS
            ],
            "input": {"root": str(defaults.input_root)},
            "repository": {
                "commit": "1" * 40,
                "source_policy": "all bound source bytes equal the named commit",
                "tree": "2" * 40,
            },
            "runtime": {
                "binaries": {
                    key: {"path": str(path)} for key, path in binary_paths.items()
                },
                "libomp": libomp_identity,
                "source_endpoints": capsule.runner.SOURCE_ENDPOINTS,
                "source_roots": {
                    "optimized": str(defaults.runtime.optimized_source_root),
                    "reference": str(defaults.runtime.reference_source_root),
                },
                "source_roots_role": capsule.SOURCE_ROOTS_ROLE,
            },
            "schema_version": capsule.SCHEMA,
            "source_files": [],
            "study_id": "dancers-orca-v1",
        }

    def _publish_fixture(
        self, parent: pathlib.Path, *, full: bool = False
    ) -> tuple[pathlib.Path, str, dict[str, bytes], dict[str, object]]:
        payloads = self._payloads()
        manifest = (
            self._full_manifest(payloads)
            if full
            else self._file_manifest(payloads)
        )
        output = parent / "capsule"
        digest = capsule.publish_capsule(
            output,
            payloads,
            manifest,
            validator=lambda stage, expected: capsule.load_capsule_files(
                stage, expected
            ),
        )
        return output, digest, payloads, manifest

    def test_pure_apis_freeze_exactly_363_commands_without_subprocess(self) -> None:
        with mock.patch.object(
            capsule.subprocess, "run", side_effect=AssertionError("child process")
        ):
            payloads, values = capsule.build_payloads(
                capsule.default_inputs().input_root
            )
        self.assertEqual(tuple(sorted(payloads)), capsule.PAYLOAD_PATHS)
        command_document = values["command_templates.json"]
        self.assertEqual(command_document["count"], 363)
        self.assertEqual(len(command_document["templates"]), 363)
        self.assertEqual(
            [item["sequence"] for item in command_document["templates"]],
            list(range(363)),
        )
        self.assertEqual(
            values["execution_plan.json"]["attempts"],
            command_document["templates"],
        )
        self.assertIs(
            values["execution_plan.json"]["classification"][
                "execution_authorized"
            ],
            False,
        )
        self.assertEqual(
            capsule.CLASSIFICATION,
            {
                "execution_authorized": False,
                "publication_processes_executed": 0,
                "publication_simulation_steps": 0,
                "result_namespace": "absent",
                "scope": "prospective commands and result-analysis contracts only",
            },
        )
        self.assertEqual(
            json.loads(payloads["analysis_contract.json"]),
            values["analysis_contract.json"],
        )

    def test_cross_contract_rejects_every_frozen_root_drift(self) -> None:
        _payloads, values = capsule.build_payloads(
            capsule.default_inputs().input_root
        )
        arguments = (
            values["execution_plan.json"],
            values["command_templates.json"]["templates"],
            values["result_contract.json"],
            values["analysis_contract.json"],
        )
        frozen_roots = (
            "FROZEN_BUILD_CAPSULE_SHA256",
            "FROZEN_COMMON_PATCH_SHA256",
            "FROZEN_DANCERS_BUILD_MANIFEST_SHA256",
            "FROZEN_INPUT_MANIFEST_SHA256",
            "FROZEN_LIBOMP_SHA256",
            "FROZEN_ORCA_BINARY_SHA256",
            "FROZEN_PIXI_LOCK_SHA256",
            "FROZEN_SCHEDULE_SHA256",
        )
        for name in frozen_roots:
            with self.subTest(root=name), mock.patch.object(
                capsule.analyzer, name, "0" * 64
            ):
                with self.assertRaisesRegex(
                    capsule.CapsuleError, "cross-contract"
                ):
                    capsule.verify_runner_analyzer_cross_contract(*arguments)

    def test_cross_contract_rejects_endpoint_binary_and_libomp_drift(self) -> None:
        _payloads, values = capsule.build_payloads(
            capsule.default_inputs().input_root
        )
        arguments = (
            values["execution_plan.json"],
            values["command_templates.json"]["templates"],
            values["result_contract.json"],
            values["analysis_contract.json"],
        )
        endpoints = copy.deepcopy(capsule.analyzer.SOURCE_ENDPOINTS)
        endpoints["optimized"]["tree"] = "0" * 40
        binaries = copy.deepcopy(capsule.analyzer.EXPECTED_BINARIES)
        binaries["reference_timing"]["sha256"] = "0" * 64
        libomp = copy.deepcopy(capsule.analyzer.EXPECTED_LIBOMP)
        libomp["bytes"] += 1
        mutations = (
            ("SOURCE_ENDPOINTS", endpoints),
            ("EXPECTED_BINARIES", binaries),
            ("EXPECTED_LIBOMP", libomp),
        )
        for name, value in mutations:
            with self.subTest(contract=name), mock.patch.object(
                capsule.analyzer, name, value
            ):
                with self.assertRaisesRegex(
                    capsule.CapsuleError, "cross-contract"
                ):
                    capsule.verify_runner_analyzer_cross_contract(*arguments)

    def test_cross_contract_rejects_inventory_schema_and_projection_drift(self) -> None:
        _payloads, values = capsule.build_payloads(
            capsule.default_inputs().input_root
        )
        base = (
            values["execution_plan.json"],
            values["command_templates.json"]["templates"],
            values["result_contract.json"],
            values["analysis_contract.json"],
        )

        mutations: list[tuple[str, tuple[object, ...]]] = []
        plan = copy.deepcopy(base[0])
        plan["inventory"]["methods"]["orca"] -= 1
        mutations.append(("method inventory", (plan, base[1], base[2], base[3])))
        templates = copy.deepcopy(base[1])
        templates[-1]["sequence"] = 0
        mutations.append(("sequence inventory", (base[0], templates, base[2], base[3])))
        result = copy.deepcopy(base[2])
        result["root_files"].pop()
        mutations.append(("root files", (base[0], base[1], result, base[3])))
        analysis = copy.deepcopy(base[3])
        analysis["result_contract"]["attempt_schema_version"] = "changed"
        mutations.append(("schema", (base[0], base[1], base[2], analysis)))
        analysis = copy.deepcopy(base[3])
        analysis["result_contract"]["runtime_binding_exact_keys"].remove("libomp")
        mutations.append(("runtime projection", (base[0], base[1], base[2], analysis)))
        analysis = copy.deepcopy(base[3])
        analysis["result_contract"]["terminal_outcomes"].reverse()
        mutations.append(("terminal outcomes", (base[0], base[1], base[2], analysis)))

        for label, arguments in mutations:
            with self.subTest(contract=label), self.assertRaises(
                capsule.CapsuleError
            ):
                capsule.verify_runner_analyzer_cross_contract(*arguments)

    def test_live_pixi_lock_is_bound_to_runner_and_analyzer(self) -> None:
        identity = capsule.verify_pixi_lock()
        self.assertEqual(identity["path"], "pixi.lock")
        self.assertEqual(identity["sha256"], capsule.runner.PIXI_LOCK_SHA256)
        with mock.patch.object(capsule.runner, "PIXI_LOCK_SHA256", "0" * 64):
            with self.assertRaisesRegex(capsule.CapsuleError, "live pixi.lock"):
                capsule.verify_pixi_lock()

    def test_public_cli_has_create_and_check_but_no_execute(self) -> None:
        parser = capsule.parser()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["execute"])
        source = pathlib.Path(capsule.__file__).read_text(encoding="utf-8")
        self.assertNotIn("subprocess.Popen", source)
        self.assertNotIn("execute_authorized(", source)
        self.assertNotIn("run_process(", source)

    def test_only_child_surface_is_argument_firewalled_read_only_git(self) -> None:
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=("a" * 40 + "\n").encode(), stderr=b""
        )
        poison = {
            "DYLD_INSERT_LIBRARIES": "/attacker/loader.dylib",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": "/attacker/objects",
            "GIT_DIR": "/attacker/repository",
            "GIT_TRACE2_EVENT": "/attacker/trace",
            "LD_PRELOAD": "/attacker/loader.so",
        }
        with mock.patch.dict(os.environ, poison, clear=False), mock.patch.object(
            capsule.subprocess, "run", return_value=completed
        ) as run:
            self.assertEqual(capsule._git_text(("rev-parse", "HEAD")), "a" * 40)
            argv = run.call_args.args[0]
            self.assertEqual(argv[0], str(capsule.GIT))
            self.assertEqual(argv[-2:], ["rev-parse", "HEAD"])
            self.assertIs(run.call_args.kwargs["shell"], False)
            self.assertEqual(run.call_args.kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(
                run.call_args.kwargs["env"], capsule.MINIMAL_CHILD_ENVIRONMENT
            )
            self.assertEqual(
                run.call_args.kwargs["timeout"], capsule.CHILD_TIMEOUT_SECONDS
            )
            self.assertTrue(set(poison).isdisjoint(run.call_args.kwargs["env"]))
            with self.assertRaisesRegex(capsule.CapsuleError, "firewall"):
                capsule._run_git(("status", "--porcelain"))
            with self.assertRaisesRegex(capsule.CapsuleError, "firewall"):
                capsule._run_git(("show", "HEAD:../../wrapper"))
        self.assertEqual(run.call_count, 1)

    def test_git_is_exactly_authenticated_before_spawn_and_queries_timeout(self) -> None:
        with mock.patch.object(capsule, "GIT_SHA256", "0" * 64), mock.patch.object(
            capsule.subprocess, "run"
        ) as run:
            with self.assertRaisesRegex(capsule.CapsuleError, "exact trust root"):
                capsule._run_git(("rev-parse", "HEAD"))
        run.assert_not_called()

        timeout = subprocess.TimeoutExpired([str(capsule.GIT)], 1)
        with mock.patch.object(capsule.subprocess, "run", side_effect=timeout):
            with self.assertRaisesRegex(capsule.CapsuleError, "timed out"):
                capsule._run_git(("rev-parse", "HEAD"))

    def test_committed_dancers_manifest_four_binaries_and_libomp(self) -> None:
        defaults = capsule.default_inputs()
        record = capsule.verify_dancers_build(
            defaults.dancers_build_manifest, defaults.libomp, defaults.runtime
        )
        self.assertEqual(
            set(record["binaries"]),
            {
                "optimized_fidelity",
                "optimized_timing",
                "reference_fidelity",
                "reference_timing",
            },
        )
        self.assertEqual(
            record["committed_build_manifest"]["sha256"],
            capsule.DANCERS_BUILD_MANIFEST_SHA256,
        )
        self.assertEqual(record["libomp"]["sha256"], capsule.LIBOMP_SHA256)
        wrong = defaults.dancers_build_manifest.parent / "analysis.json"
        with self.assertRaisesRegex(capsule.CapsuleError, "trust-root path"):
            capsule.verify_dancers_build(wrong, defaults.libomp, defaults.runtime)

    def test_orca_capsule_check_uses_exact_external_digest_and_binary(self) -> None:
        defaults = capsule.default_inputs()
        binary = defaults.runtime.orca_binary
        identity = capsule.file_identity(binary)
        build_manifest = {
            "builds": [
                {
                    "audit": {
                        "binary": {
                            "bytes": identity["bytes"],
                            "path": str(binary),
                            "sha256": identity["sha256"],
                        }
                    }
                },
                {
                    "audit": {
                        "binary": {
                            "bytes": identity["bytes"],
                            "path": str(
                                capsule.REPO
                                / "build/orca-wrapper-publication-v1-pass-2/dancers-orca-wrapper"
                            ),
                            "sha256": identity["sha256"],
                        }
                    }
                },
            ]
        }
        with mock.patch.object(
            capsule.orca_build_capsule,
            "check_capsule",
            return_value=build_manifest,
        ) as check:
            record = capsule.verify_orca_build(
                defaults.orca_build_capsule_root, binary
            )
        check.assert_called_once_with(
            defaults.orca_build_capsule_root,
            capsule.ORCA_BUILD_CAPSULE_SHA256,
        )
        self.assertEqual(record["binary"]["sha256"], capsule.ORCA_BINARY_SHA256)
        self.assertEqual(record["selected_build_index"], 0)

    def test_delegated_build_checker_uses_clean_environment_and_timeout(self) -> None:
        defaults = capsule.default_inputs()
        binary = defaults.runtime.orca_binary
        identity = capsule.file_identity(binary)
        build_manifest = {
            "builds": [
                {
                    "audit": {
                        "binary": {
                            "bytes": identity["bytes"],
                            "path": str(binary),
                            "sha256": identity["sha256"],
                        }
                    }
                }
            ]
        }
        completed = subprocess.CompletedProcess(
            args=["/usr/bin/true"], returncode=0, stdout=b"", stderr=b""
        )
        poison = {
            "DYLD_INSERT_LIBRARIES": "/attacker/loader.dylib",
            "GIT_DIR": "/attacker/repository",
            "GIT_TRACE": "/attacker/trace",
            "LD_PRELOAD": "/attacker/loader.so",
        }

        def delegated_check(_root: pathlib.Path, _digest: str) -> dict[str, object]:
            self.assertEqual(dict(os.environ), capsule.MINIMAL_CHILD_ENVIRONMENT)
            capsule.orca_build_capsule.run_probe(["/usr/bin/true"])
            return build_manifest

        with mock.patch.dict(os.environ, poison, clear=False), mock.patch.object(
            capsule.orca_build_capsule,
            "check_capsule",
            side_effect=delegated_check,
        ), mock.patch.object(
            capsule.subprocess, "run", return_value=completed
        ) as run:
            capsule.verify_orca_build(defaults.orca_build_capsule_root, binary)
            self.assertEqual(os.environ["GIT_DIR"], poison["GIT_DIR"])

        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs["env"], capsule.MINIMAL_CHILD_ENVIRONMENT)
        self.assertEqual(run.call_args.kwargs["timeout"], capsule.CHILD_TIMEOUT_SECONDS)
        self.assertTrue(set(poison).isdisjoint(run.call_args.kwargs["env"]))

    def test_orca_delegate_is_not_entered_before_git_authentication(self) -> None:
        defaults = capsule.default_inputs()
        with mock.patch.object(
            capsule,
            "authenticate_git_executable",
            side_effect=capsule.CapsuleError("unauthenticated Git"),
        ), mock.patch.object(capsule.orca_build_capsule, "check_capsule") as check:
            with self.assertRaisesRegex(capsule.CapsuleError, "unauthenticated Git"):
                capsule.verify_orca_build(
                    defaults.orca_build_capsule_root, defaults.runtime.orca_binary
                )
        check.assert_not_called()

    def test_publication_is_create_exclusive_same_parent_and_sidecar_last(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = pathlib.Path(temporary).resolve()
            payloads = self._payloads()
            manifest = self._file_manifest(payloads)
            output = parent / "capsule"
            moved: list[str] = []
            original_rename = pathlib.Path.rename

            def recording_rename(
                source: pathlib.Path, destination: pathlib.Path
            ) -> pathlib.Path:
                moved.append(source.name)
                return original_rename(source, destination)

            def validate(stage: pathlib.Path, digest: str) -> None:
                self.assertEqual(stage.parent, parent)
                self.assertFalse(output.exists())
                capsule.load_capsule_files(stage, digest)

            with mock.patch.object(
                capsule.subprocess, "run", side_effect=AssertionError("child process")
            ), mock.patch.object(
                pathlib.Path, "rename", autospec=True, side_effect=recording_rename
            ):
                digest = capsule.publish_capsule(
                    output, payloads, manifest, validator=validate
                )
            self.assertEqual(moved[-1], "manifest.sha256")
            self.assertEqual(
                (output / "manifest.sha256").read_bytes(),
                f"{digest}  manifest.json\n".encode(),
            )
            capsule.load_capsule_files(output, digest)
            with self.assertRaisesRegex(capsule.CapsuleError, "already exists"):
                capsule.publish_capsule(
                    output,
                    payloads,
                    manifest,
                    validator=lambda _stage, _digest: None,
                )

    def test_file_check_detects_missing_extra_symlink_content_and_mode(self) -> None:
        mutations = ("missing", "extra", "symlink", "content", "mode")
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                parent = pathlib.Path(temporary).resolve()
                output, digest, _payloads, _manifest = self._publish_fixture(parent)
                target = output / capsule.PAYLOAD_PATHS[0]
                if mutation == "missing":
                    target.unlink()
                elif mutation == "extra":
                    (output / "extra.json").write_bytes(b"{}\n")
                elif mutation == "symlink":
                    target.unlink()
                    target.symlink_to(output / capsule.PAYLOAD_PATHS[1])
                elif mutation == "content":
                    target.write_bytes(b"{}\n")
                else:
                    target.chmod(0o600)
                with self.assertRaises(capsule.CapsuleError):
                    capsule.load_capsule_files(output, digest)

    def test_sidecar_cannot_replace_external_trust_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = pathlib.Path(temporary).resolve()
            output, digest, _payloads, manifest = self._publish_fixture(parent)
            changed = dict(manifest)
            changed["attacker_note"] = "self-consistent but externally unauthenticated"
            raw = capsule.canonical_json_bytes(changed)
            forged = capsule.sha256_bytes(raw)
            (output / "manifest.json").write_bytes(raw)
            (output / "manifest.sha256").write_bytes(
                f"{forged}  manifest.json\n".encode()
            )
            with self.assertRaisesRegex(capsule.CapsuleError, "external trust root"):
                capsule.load_capsule_files(output, digest)

    def test_full_check_is_read_only_when_dependencies_match(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = pathlib.Path(temporary).resolve()
            output, digest, payloads, manifest = self._publish_fixture(
                parent, full=True
            )
            before = capsule.snapshot_tree(output)
            with mock.patch.object(
                capsule, "verify_repository_record", return_value=[]
            ) as repository, mock.patch.object(
                capsule, "collect_current", return_value=(payloads, manifest)
            ) as collect, mock.patch.object(
                capsule.subprocess, "run", side_effect=AssertionError("child process")
            ):
                checked = capsule.check_capsule(output, digest)
            self.assertEqual(checked, manifest)
            repository.assert_called_once()
            collect.assert_called_once()
            self.assertEqual(capsule.snapshot_tree(output), before)

    def test_create_rejects_output_overlap_before_authentication(self) -> None:
        defaults = capsule.default_inputs()
        output = defaults.input_root / "forbidden-capsule"
        with mock.patch.object(capsule, "verify_repository_anchor") as repository:
            with self.assertRaisesRegex(capsule.CapsuleError, "overlap"):
                capsule.create_capsule(defaults, output)
        repository.assert_not_called()
        self.assertFalse(output.exists())

    def test_create_route_publishes_only_the_prebuilt_contract_capsule(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = pathlib.Path(temporary).resolve()
            output = parent / "execution-capsule"
            payloads = self._payloads()
            manifest = self._file_manifest(payloads)

            def validate(stage: pathlib.Path, digest: str) -> dict[str, object]:
                checked, _payloads = capsule.load_capsule_files(stage, digest)
                return checked

            with mock.patch.object(
                capsule,
                "verify_repository_anchor",
                return_value=({"commit": "1" * 40}, []),
            ) as repository, mock.patch.object(
                capsule,
                "collect_current",
                return_value=(payloads, manifest),
            ) as collect, mock.patch.object(
                capsule, "check_capsule", side_effect=validate
            ) as check, mock.patch.object(
                capsule.subprocess, "run", side_effect=AssertionError("child process")
            ):
                digest = capsule.create_capsule(capsule.default_inputs(), output)
            self.assertTrue(output.is_dir())
            self.assertFalse((parent / "results").exists())
            capsule.load_capsule_files(output, digest)
            repository.assert_called_once_with()
            collect.assert_called_once()
            check.assert_called_once()

    def test_main_check_requires_explicit_external_digest(self) -> None:
        parser = capsule.parser()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(["check", "--capsule-root", "/private/tmp/value"])
        args = parser.parse_args(
            [
                "check",
                "--capsule-root",
                "/private/tmp/value",
                "--expected-manifest-sha256",
                "0" * 64,
            ]
        )
        self.assertEqual(args.expected_manifest_sha256, "0" * 64)


if __name__ == "__main__":
    unittest.main()
