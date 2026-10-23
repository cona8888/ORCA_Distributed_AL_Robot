#!/usr/bin/env python3
"""Adversarial static tests for the sealed runtime-build verifier."""

from __future__ import annotations

import importlib.util
import json
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

import build_headless_runtime_artifacts_v1 as B
import verify_headless_runtime_build_v1 as V

FIXTURE_PATH = Path(__file__).with_name("_headless_runtime_build_v1_fixture.py")
SPEC = importlib.util.spec_from_file_location("headless_runtime_build_v1_fixture", FIXTURE_PATH)
assert SPEC is not None and SPEC.loader is not None
F = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(F)


class RuntimeBuildVerifierTests(unittest.TestCase):
    def test_01_valid_synthetic_seal_passes_static_read_only_verification(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            with mock.patch.object(B.subprocess, "run", side_effect=AssertionError("process invoked")):
                verdict = V.verify_build(repository, build_root)
        self.assertTrue(verdict["build_complete"])
        self.assertTrue(verdict["host_bound"])
        self.assertTrue(verdict["static_read_only_verification"])
        self.assertEqual(verdict["artifact_count"], 61)
        for value in verdict["science_flags"].values():
            self.assertIs(value, False)

    def test_02_verification_performs_no_filesystem_write(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            with mock.patch.object(Path, "write_bytes", side_effect=AssertionError("write invoked")):
                verdict = V.verify_build(repository, build_root)
        self.assertTrue(verdict["static_read_only_verification"])

    def test_03_internally_consistent_timing_observer_marker_is_rejected(self) -> None:
        with F.sealed_fixture(ROOT, B, V, timing_marker=True) as (repository, build_root):
            with self.assertRaisesRegex(V.VerificationError, "timing binary contains observer"):
                V.verify_build(repository, build_root)

    def test_04_product_activation_claim_is_rejected(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            manifest_path = build_root / "build-manifest-v1.json"
            os.chmod(build_root, 0o755)
            os.chmod(manifest_path, 0o644)
            value = json.loads(manifest_path.read_bytes())
            value["operations"]["runtime_product_invoked"] = True
            manifest_path.write_bytes(B._canonical_json(value))
            os.chmod(manifest_path, 0o444)
            os.chmod(build_root, 0o555)
            with self.assertRaisesRegex(V.VerificationError, "operation record differs"):
                V.verify_build(repository, build_root)

    def test_05_claim_flag_escalation_is_rejected_before_seal_acceptance(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            manifest_path = build_root / "build-manifest-v1.json"
            os.chmod(build_root, 0o755)
            os.chmod(manifest_path, 0o644)
            value = json.loads(manifest_path.read_bytes())
            value["classification"]["scientific_evidence"] = True
            manifest_path.write_bytes(B._canonical_json(value))
            os.chmod(manifest_path, 0o444)
            os.chmod(build_root, 0o555)
            with self.assertRaisesRegex(V.VerificationError, "classification differs"):
                V.verify_build(repository, build_root)

    def test_06_noncanonical_or_duplicate_manifest_is_rejected(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            manifest_path = build_root / "build-manifest-v1.json"
            os.chmod(build_root, 0o755)
            os.chmod(manifest_path, 0o644)
            manifest_path.write_bytes(b'{"schema_version":"x","schema_version":"y"}\n')
            os.chmod(manifest_path, 0o444)
            os.chmod(build_root, 0o555)
            with self.assertRaisesRegex(V.VerificationError, "duplicate JSON key"):
                V.verify_build(repository, build_root)

    def test_07_symlink_substitution_is_rejected(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            target = build_root / "logs/pass-1/reference-timing.build.log"
            parent = target.parent
            os.chmod(build_root, 0o755)
            os.chmod(build_root / "logs", 0o755)
            os.chmod(build_root / "logs/pass-1", 0o755)
            target.unlink()
            target.symlink_to(repository / B.SENTINEL_PATH)
            os.chmod(parent, 0o555)
            os.chmod(build_root / "logs", 0o555)
            os.chmod(build_root, 0o555)
            with self.assertRaisesRegex(V.VerificationError, "symlink"):
                V.verify_build(repository, build_root)

    def test_08_toolchain_drift_is_rejected_even_with_valid_shape(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, _):
            value = F._toolchain(B, repository)
            value["probe_outputs"]["ninja"] = "1.13.1"
            with self.assertRaisesRegex(V.VerificationError, "probe output differs"):
                V._verify_toolchain(value, repository)

    def test_09_verifier_source_has_no_process_or_write_surface(self) -> None:
        source = (ROOT / "scripts/comparison/verify_headless_runtime_build_v1.py").read_text(encoding="utf-8")
        for token in ("subprocess", "Popen(", "os.system", ".write_bytes(", "open(\"w"):
            self.assertNotIn(token, source)

    def test_10_builder_implementation_drift_is_rejected(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            builder = repository / V.BUILDER_PATH
            marker = repository / "MISMATCHED_BUILDER_EXECUTED"
            builder.write_bytes(
                builder.read_bytes()
                + f"\nopen({str(marker)!r}, 'wb').write(b'executed')\n".encode("utf-8")
            )
            with self.assertRaisesRegex(V.VerificationError, "builder implementation identity differs"):
                V.verify_build(repository, build_root)
            self.assertFalse(marker.exists())

    def test_11_non_system_dynamic_input_is_rejected_from_consistent_seal(self) -> None:
        with F.sealed_fixture(ROOT, B, V, dynamic_fault=True) as (repository, build_root):
            with self.assertRaisesRegex(V.VerificationError, "non-system input"):
                V.verify_build(repository, build_root)

    def test_12_toolchain_claiming_loaded_openmp_runtime_is_rejected(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, _):
            value = F._toolchain(B, repository)
            value["openmp_policy"]["runtime_library_loaded"] = True
            with self.assertRaisesRegex(V.VerificationError, "no-OpenMP"):
                V._verify_toolchain(value, repository)

    def test_13_shim_non_system_load_command_is_rejected(self) -> None:
        with F.sealed_fixture(
            ROOT, B, V, shim_dynamic_fault=True
        ) as (repository, build_root):
            with self.assertRaisesRegex(V.VerificationError, "non-system dynamic input"):
                V.verify_build(repository, build_root)

    def test_14_shim_descriptor_contract_mutation_is_rejected(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            manifest_path = build_root / "build-manifest-v1.json"
            os.chmod(build_root, 0o755)
            os.chmod(manifest_path, 0o644)
            value = json.loads(manifest_path.read_bytes())
            value["shim_passes"][0]["runtime_descriptor_contract"][
                "descriptor_fds"
            ]["shim_executable"] = 251
            manifest_path.write_bytes(B._canonical_json(value))
            os.chmod(manifest_path, 0o444)
            os.chmod(build_root, 0o555)
            with self.assertRaisesRegex(V.VerificationError, "descriptor contract differs"):
                V.verify_build(repository, build_root)

    def test_15_shim_hard_link_is_rejected(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            shim = build_root / "products" / B.SHIM_PRODUCT_ID / B.SHIM_BINARY_NAME
            os.link(shim, repository / "shim-hardlink")
            with self.assertRaisesRegex(V.VerificationError, "link count differs"):
                V.verify_build(repository, build_root)

    def test_16_runtime_code_page_swap_is_rejected_by_code_directory(self) -> None:
        with F.sealed_fixture(
            ROOT, B, V, runtime_signature_fault=True
        ) as (repository, build_root):
            with self.assertRaisesRegex(V.VerificationError, "code-slot hash differs"):
                V.verify_build(repository, build_root)

    def test_17_shim_code_page_swap_is_rejected_by_code_directory(self) -> None:
        with F.sealed_fixture(
            ROOT, B, V, shim_signature_fault=True
        ) as (repository, build_root):
            with self.assertRaisesRegex(V.VerificationError, "code-slot hash differs"):
                V.verify_build(repository, build_root)

    def test_18_non_shim_artifact_hard_link_is_rejected(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            artifact = build_root / "logs/pass-1/reference-timing.build.log"
            os.link(artifact, repository / "artifact-hardlink")
            with self.assertRaisesRegex(V.VerificationError, "link count differs"):
                V.verify_build(repository, build_root)

    def test_19_manifest_and_seal_hard_links_are_rejected(self) -> None:
        for name in ("build-manifest-v1.json", "BUILD_COMPLETE.seal.json"):
            with self.subTest(name=name), F.sealed_fixture(
                ROOT, B, V
            ) as (repository, build_root):
                os.link(build_root / name, repository / f"{name}.hardlink")
                with self.assertRaisesRegex(V.VerificationError, "link count differs"):
                    V.verify_build(repository, build_root)

    def test_20_artifact_swap_between_semantic_read_and_final_custody_is_rejected(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            relative = "products/reference-timing/dancers"

            def swap_artifact(
                root: Path,
                artifacts: object,
            ) -> None:
                del artifacts
                path = root / relative
                os.chmod(root, 0o755)
                os.chmod(path.parent.parent, 0o755)
                os.chmod(path.parent, 0o755)
                os.chmod(path, 0o755)
                path.write_bytes(b"persistent-swap-before-final-custody")
                os.chmod(path, 0o555)
                os.chmod(path.parent, 0o555)
                os.chmod(path.parent.parent, 0o555)
                os.chmod(root, 0o555)

            with mock.patch.object(
                V, "_post_artifact_semantic_hook", side_effect=swap_artifact
            ), self.assertRaisesRegex(
                V.VerificationError, "identity differs|changed across verification"
            ):
                V.verify_build(repository, build_root)

    def test_21_raw_builder_bootstrap_ignores_matching_malicious_pyc_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="headless-verifier-bootstrap-") as name:
            repository = Path(name).resolve()
            comparison = repository / "scripts/comparison"
            comparison.mkdir(parents=True)
            for relative in (V.BUILDER_PATH, "scripts/comparison/verify_headless_runtime_build_v1.py"):
                destination = repository / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, destination)
            marker = repository / "MALICIOUS_PYC_EXECUTED"
            cache = comparison / "__pycache__"
            cache.mkdir()
            code = compile(
                f"open({str(marker)!r}, 'wb').write(b'executed')",
                str(repository / V.BUILDER_PATH),
                "exec",
            )
            pyc = (
                importlib.util.MAGIC_NUMBER
                + struct.pack("<I", 1)
                + b"\0" * 8
                + marshal.dumps(code)
            )
            pyc_path = cache / (
                "build_headless_runtime_artifacts_v1."
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
                    str(comparison / "verify_headless_runtime_build_v1.py"),
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

    def test_22_public_verifier_rejects_nonisolated_invocation(self) -> None:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/comparison/verify_headless_runtime_build_v1.py"), "--help"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"requires python -I -S -B", result.stderr)

    def test_23_bootstrap_close_effect_error_never_recloses_reused_fd(self) -> None:
        original_close = V.os.close
        reused: list[int] = []
        close_calls: list[int] = []

        def close_then_error(fd: int) -> None:
            close_calls.append(fd)
            original_close(fd)
            if not reused:
                replacement = os.open("/dev/null", os.O_RDONLY)
                self.assertEqual(replacement, fd)
                reused.append(replacement)
                raise OSError("injected close effect error")

        try:
            with mock.patch.object(V.os, "close", side_effect=close_then_error):
                with self.assertRaisesRegex(V._BootstrapError, "descriptor cleanup failed"):
                    V._capture_authenticated_source(
                        ROOT,
                        V.BUILDER_PATH,
                        V.EXPECTED_BUILDER_BYTES,
                        V.EXPECTED_BUILDER_SHA256,
                        "close regression",
                    )
            self.assertEqual(close_calls.count(reused[0]), 1)
            os.fstat(reused[0])
        finally:
            for fd in reused:
                original_close(fd)

    def test_24_bootstrap_primary_baseexception_survives_close_failure(self) -> None:
        original_close = V.os.close
        close_faulted = False

        def close_then_error(fd: int) -> None:
            nonlocal close_faulted
            original_close(fd)
            if not close_faulted:
                close_faulted = True
                raise OSError("secondary close failure")

        with mock.patch.object(V.os, "read", side_effect=KeyboardInterrupt("primary")), mock.patch.object(
            V.os, "close", side_effect=close_then_error
        ):
            with self.assertRaisesRegex(KeyboardInterrupt, "primary"):
                V._capture_authenticated_source(
                    ROOT,
                    V.BUILDER_PATH,
                    V.EXPECTED_BUILDER_BYTES,
                    V.EXPECTED_BUILDER_SHA256,
                    "primary regression",
                )

    def test_25_nonisolated_verifier_rejects_before_shadow_stdlib_import(self) -> None:
        with tempfile.TemporaryDirectory(prefix="headless-verifier-shadow-") as name:
            repository = Path(name).resolve()
            comparison = repository / "scripts/comparison"
            comparison.mkdir(parents=True)
            marker = repository / "SHADOW_ARGPARSE_EXECUTED"
            (comparison / "argparse.py").write_text(
                f"open({str(marker)!r}, 'wb').write(b'executed')\n",
                encoding="utf-8",
            )
            for relative in (V.BUILDER_PATH, "scripts/comparison/verify_headless_runtime_build_v1.py"):
                shutil.copyfile(ROOT / relative, repository / relative)
            result = subprocess.run(
                [
                    sys.executable, "-S", "-B",
                    str(comparison / "verify_headless_runtime_build_v1.py"),
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

    def test_26_two_pass_descriptor_data_in_code_receipt_drift_is_rejected(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            manifest_path = build_root / "build-manifest-v1.json"
            os.chmod(build_root, 0o755)
            os.chmod(manifest_path, 0o644)
            value = json.loads(manifest_path.read_bytes())
            receipt = value["passes"][0]["descriptor_test_macho"][
                "linkedit_metadata"
            ]["LC_DATA_IN_CODE"]
            receipt["data_offset"] += 8
            manifest_path.write_bytes(B._canonical_json(value))
            os.chmod(manifest_path, 0o444)
            os.chmod(build_root, 0o555)
            with self.assertRaisesRegex(
                V.VerificationError,
                "two-pass descriptor test Mach-O receipt differs",
            ):
                V.verify_build(repository, build_root)

    def test_27_two_pass_descriptor_chained_pool_receipt_drift_is_rejected(self) -> None:
        with F.sealed_fixture(ROOT, B, V) as (repository, build_root):
            manifest_path = build_root / "build-manifest-v1.json"
            os.chmod(build_root, 0o755)
            os.chmod(manifest_path, 0o644)
            value = json.loads(manifest_path.read_bytes())
            receipt = value["passes"][0]["descriptor_test_macho"][
                "dyld_chained_fixups"
            ]
            self.assertEqual(
                receipt["symbol_pool_encoding"],
                B.CHAINED_SYMBOL_POOL_ENCODING,
            )
            receipt["symbol_pool_sha256"] = "0" * 64
            manifest_path.write_bytes(B._canonical_json(value))
            os.chmod(manifest_path, 0o444)
            os.chmod(build_root, 0o555)
            with self.assertRaisesRegex(
                V.VerificationError,
                "two-pass descriptor test Mach-O receipt differs",
            ):
                V.verify_build(repository, build_root)


if __name__ == "__main__":
    unittest.main()
