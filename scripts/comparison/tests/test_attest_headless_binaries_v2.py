#!/usr/bin/env python3
"""Static and adversarial tests for the v2 headless binary attester."""

from __future__ import annotations

import importlib.util
import marshal
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SCRIPT_DIR = ROOT / "scripts/comparison"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import attest_headless_binaries_v2 as A
import build_headless_runtime_artifacts_v1 as B
import verify_headless_runtime_build_v1 as V

FIXTURE_PATH = Path(__file__).with_name("_headless_runtime_build_v1_fixture.py")
SPEC = importlib.util.spec_from_file_location("headless_runtime_build_v1_fixture_for_attest", FIXTURE_PATH)
assert SPEC is not None and SPEC.loader is not None
F = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(F)


class HeadlessBinaryAttestationV2Tests(unittest.TestCase):
    def test_01_valid_sealed_quartet_produces_exact_v2_shape(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            value = A.attest_build(repository, build_root)
        self.assertEqual(value["schema_version"], A.SCHEMA_VERSION)
        self.assertEqual(set(value), {
            "build", "classification", "design", "evidence_boundary",
            "implementation", "operations", "pixi_lock", "products",
            "repository", "runtime_artifact_root", "schema_version",
            "source_identity", "spawn_shim", "status", "toolchain", "track_id",
        })
        self.assertEqual(set(value["build"]), {
            "build_root", "host_bound", "manifest", "seal",
            "static_verifier_manifest_sha256",
        })
        self.assertEqual(value["track_id"], A.TRACK_ID)
        self.assertEqual(set(value["products"]), {item[0] for item in B.MATRIX})
        self.assertEqual(value["spawn_shim"]["product_id"], B.SHIM_PRODUCT_ID)
        self.assertEqual(
            value["spawn_shim"]["runtime_descriptor_contract"],
            {
                "descriptor_fds": B.SHIM_DESCRIPTOR_FDS,
                "launch": B.SHIM_LAUNCH_CONTRACT,
            },
        )
        self.assertEqual(len(set(value["spawn_shim"]["two_pass_binary_sha256"])), 1)
        self.assertEqual(len(value["spawn_shim"]["expected_cdhash"]), 40)
        self.assertEqual(len(value["spawn_shim"]["expected_lc_uuid"]), 36)
        self.assertTrue(value["classification"]["host_bound"])
        self.assertTrue(value["classification"]["identity_attestation_only"])
        self.assertFalse(value["classification"]["attestation_published"])

    def test_02_all_science_and_execution_authority_flags_stay_false(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            value = A.attest_build(repository, build_root)
        for key, expected in B.SCIENCE_FLAGS.items():
            self.assertIs(value["classification"][key], expected)
        self.assertEqual(value["operations"], {
            "attestation_written": False,
            "binary_executed": False,
            "network_accessed": False,
            "plan_written": False,
            "result_written": False,
            "simulator_invoked": False,
            "static_reads_only": True,
        })

    def test_03_attestation_is_byte_deterministic_for_one_seal(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            first = B._canonical_json(A.attest_build(repository, build_root))
            second = B._canonical_json(A.attest_build(repository, build_root))
        self.assertEqual(first, second)

    def test_04_attester_neither_spawns_nor_writes(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            with mock.patch.object(B.subprocess, "run", side_effect=AssertionError("process invoked")), mock.patch.object(
                Path, "write_bytes", side_effect=AssertionError("write invoked")
            ):
                value = A.attest_build(repository, build_root)
        self.assertTrue(value["operations"]["static_reads_only"])

    def test_05_activation_is_explicitly_pending_and_probe_artifacts_are_absent(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            value = A.attest_build(repository, build_root)
        self.assertEqual(
            value["status"],
            "pass-build-identity-only-activation-pending-nonauthorizing",
        )
        self.assertFalse(any("runtime_root_probe" in item for item in value["products"].values()))

    def test_06_product_source_and_role_bindings_are_exact(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            value = A.attest_build(repository, build_root)
        for product_id, method, role, observer in B.MATRIX:
            product = value["products"][product_id]
            self.assertEqual(set(product), {
                "algorithm_revision", "applied_head", "binary", "build_role",
                "code_signature", "descriptor_test_binary", "expected_cdhash",
                "expected_lc_uuid", "method", "observer_compiled", "source_tree",
                "two_pass_binary_sha256", "two_pass_descriptor_test_sha256",
            })
            endpoint = B.ENDPOINTS[method]
            self.assertEqual(product["method"], method)
            self.assertEqual(product["build_role"], role)
            self.assertIs(product["observer_compiled"], observer)
            self.assertEqual(product["algorithm_revision"], endpoint["algorithm_revision"])
            self.assertEqual(product["applied_head"], endpoint["applied_head"])
            self.assertEqual(product["source_tree"], endpoint["tree"])
            self.assertEqual(len(set(product["two_pass_binary_sha256"])), 1)
            self.assertEqual(len(product["expected_cdhash"]), 40)
            self.assertEqual(len(product["expected_lc_uuid"]), 36)

    def test_07_parser_has_no_attestation_output_or_execution_switch(self) -> None:
        actions = {option for action in A._parser()._actions for option in action.option_strings}
        self.assertNotIn("--output", actions)
        self.assertNotIn("--execute", actions)
        self.assertEqual(actions, {"-h", "--help", "--repository-root", "--build-root"})

    def test_08_evidence_boundary_is_explicitly_nonauthorizing(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            value = A.attest_build(repository, build_root)
        joined = " ".join(value["evidence_boundary"])
        self.assertIn("static reads only", joined)
        self.assertIn("no plan, result, calibration, validation, timing, capacity, or benchmark claim", joined)

    def test_09_attester_source_has_no_process_or_write_primitive(self) -> None:
        source = (ROOT / "scripts/comparison/attest_headless_binaries_v2.py").read_text(encoding="utf-8")
        for token in ("subprocess", "Popen(", "os.system", ".write_bytes(", "open(\"w"):
            self.assertNotIn(token, source)

    def test_10_verifier_implementation_drift_blocks_attestation(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            verifier = repository / A.VERIFIER_PATH
            verifier.write_bytes(verifier.read_bytes() + b"\n")
            with self.assertRaisesRegex(A.AttestationError, "verifier implementation identity differs"):
                A.attest_build(repository, build_root)

    def test_11_attester_consumes_verified_manifest_bytes_without_path_reread(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            captured = {}

            def swap_after_verify(
                verified: V.VerifiedBuild, repo: Path, build: Path
            ) -> None:
                del repo
                captured["sha256"] = B._sha256(verified.manifest_raw)
                manifest_path = build / "build-manifest-v1.json"
                os.chmod(build, 0o755)
                os.chmod(manifest_path, 0o644)
                manifest_path.write_bytes(b'{"swapped":true}\n')
                os.chmod(manifest_path, 0o444)
                os.chmod(build, 0o555)

            with mock.patch.object(A, "_post_verified_build_hook", side_effect=swap_after_verify):
                value = A.attest_build(repository, build_root)
            self.assertEqual(value["build"]["manifest"]["sha256"], captured["sha256"])
            self.assertNotEqual(
                value["build"]["manifest"]["sha256"],
                B._sha256((build_root / "build-manifest-v1.json").read_bytes()),
            )

    def test_12_attester_consumes_verified_artifact_snapshot_without_path_reread(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            captured = {}

            def swap_after_verify(
                verified: V.VerifiedBuild, repo: Path, build: Path
            ) -> None:
                del repo
                relative = "products/reference-timing/dancers"
                captured["sha256"] = B._sha256(
                    verified.artifact_raw_by_path[relative]
                )
                path = build / relative
                os.chmod(build, 0o755)
                os.chmod(path.parent.parent, 0o755)
                os.chmod(path.parent, 0o755)
                os.chmod(path, 0o755)
                path.write_bytes(b"swapped-after-verified-snapshot")
                os.chmod(path, 0o555)
                os.chmod(path.parent, 0o555)
                os.chmod(path.parent.parent, 0o555)
                os.chmod(build, 0o555)

            with mock.patch.object(A, "_post_verified_build_hook", side_effect=swap_after_verify):
                value = A.attest_build(repository, build_root)
            self.assertEqual(
                value["products"]["reference-timing"]["binary"]["sha256"],
                captured["sha256"],
            )

    def test_13_raw_verifier_bootstrap_ignores_matching_malicious_pyc_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="headless-attester-bootstrap-") as name:
            repository = Path(name).resolve()
            comparison = repository / "scripts/comparison"
            comparison.mkdir(parents=True)
            for relative in (
                V.BUILDER_PATH,
                A.VERIFIER_PATH,
                A.ATTESTER_PATH,
            ):
                destination = repository / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, destination)
            marker = repository / "MALICIOUS_PYC_EXECUTED"
            cache = comparison / "__pycache__"
            cache.mkdir()
            code = compile(
                f"open({str(marker)!r}, 'wb').write(b'executed')",
                str(repository / A.VERIFIER_PATH),
                "exec",
            )
            pyc = (
                importlib.util.MAGIC_NUMBER
                + struct.pack("<I", 1)
                + b"\0" * 8
                + marshal.dumps(code)
            )
            pyc_path = cache / (
                "verify_headless_runtime_build_v1."
                f"{sys.implementation.cache_tag}.pyc"
            )
            pyc_path.write_bytes(pyc)
            before = {
                str(path.relative_to(repository)): path.read_bytes()
                for path in repository.rglob("*") if path.is_file()
            }
            result = subprocess.run(
                [
                    sys.executable, "-I", "-S", "-B",
                    str(comparison / "attest_headless_binaries_v2.py"),
                    "--help",
                ],
                cwd=repository,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=20,
            )
            after = {
                str(path.relative_to(repository)): path.read_bytes()
                for path in repository.rglob("*") if path.is_file()
            }
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", "replace"))
            self.assertFalse(marker.exists())
            self.assertEqual(after, before)

    def test_14_public_attester_rejects_nonisolated_invocation(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / A.ATTESTER_PATH), "--help"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"requires python -I -S -B", result.stderr)

    def test_15_nonisolated_attester_rejects_before_shadow_stdlib_import(self) -> None:
        with tempfile.TemporaryDirectory(prefix="headless-attester-shadow-") as name:
            repository = Path(name).resolve()
            comparison = repository / "scripts/comparison"
            comparison.mkdir(parents=True)
            marker = repository / "SHADOW_ARGPARSE_EXECUTED"
            (comparison / "argparse.py").write_text(
                f"open({str(marker)!r}, 'wb').write(b'executed')\n",
                encoding="utf-8",
            )
            for relative in (
                V.BUILDER_PATH,
                A.VERIFIER_PATH,
                A.ATTESTER_PATH,
            ):
                shutil.copyfile(ROOT / relative, repository / relative)
            result = subprocess.run(
                [
                    sys.executable, "-S", "-B",
                    str(comparison / "attest_headless_binaries_v2.py"),
                    "--help",
                ],
                cwd=repository,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=20,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn(b"requires python -I -S -B", result.stderr)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
