#!/usr/bin/env python3
"""Adversarial regressions for the nonauthorizing binary attester."""

from __future__ import annotations

import ast
import copy
from contextlib import contextmanager, redirect_stderr
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/comparison/attest_headless_binaries_v1.py"
EXPECTED_SCRIPT_BYTES = 53_486
EXPECTED_SCRIPT_SHA256 = (
    "3e9fa81ffc65b6933bbdf534c0e7cdcb60dcf5da15a06c05a7cc550c86ea2859"
)

SPEC = importlib.util.spec_from_file_location("attest_headless_binaries_v1", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
ATTEST = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ATTEST
SPEC.loader.exec_module(ATTEST)


class HeadlessBinaryAttestationTests(unittest.TestCase):
    OBSERVED = "2026-08-11T12:00:00Z"

    @classmethod
    def setUpClass(cls) -> None:
        cls.live_paths = [
            ROOT / ATTEST.BUILD_MANIFEST_PATH,
            ROOT / ATTEST.PIXI_LOCK_PATH,
            *[
                ROOT / value["binary_path"]
                for value in ATTEST.EXPECTED_BUILDS.values()
            ],
        ]
        cls.live_available = all(path.is_file() for path in cls.live_paths)

    @contextmanager
    def fixture(self):
        if not self.live_available:
            self.skipTest("the fixed live build quartet is not present")
        with tempfile.TemporaryDirectory(prefix="headless-binary-attest-") as name:
            root = Path(name).resolve()
            for source in self.live_paths:
                relative = source.relative_to(ROOT)
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            yield root

    @staticmethod
    def rewrite_manifest(root: Path, mutation) -> tuple[dict, bytes]:
        path = root / ATTEST.BUILD_MANIFEST_PATH
        value = json.loads(path.read_bytes())
        mutation(value)
        raw = (json.dumps(value, indent=2) + "\n").encode("utf-8")
        path.write_bytes(raw)
        return value, raw

    def test_01_live_manifest_lock_and_binary_identities(self) -> None:
        if not self.live_available:
            self.skipTest("the fixed live build quartet is not present")
        value = ATTEST.build_attestation(
            repository_root=ROOT, observed_utc=self.OBSERVED
        )
        self.assertEqual(value["schema_version"], ATTEST.SCHEMA_VERSION)
        self.assertEqual(value["track_id"], ATTEST.TRACK_ID)
        self.assertEqual(
            value["build_manifest"]["sha256"],
            ATTEST.EXPECTED_BUILD_MANIFEST_SHA256,
        )
        self.assertEqual(
            value["pixi_lock"]["sha256"], ATTEST.EXPECTED_PIXI_LOCK_SHA256
        )
        self.assertEqual(set(value["binaries"]), set(ATTEST.EXPECTED_BUILDS))
        for key, expected in ATTEST.EXPECTED_BUILDS.items():
            with self.subTest(build=key):
                observed = value["binaries"][key]
                self.assertEqual(observed["bytes"], expected["binary_bytes"])
                self.assertEqual(observed["sha256"], expected["binary_sha256"])
                self.assertEqual(
                    observed["observer_compiled"], expected["observer_compiled"]
                )
                self.assertEqual(
                    observed["run_classes"], list(expected["run_classes"])
                )
                expected_marker_count = 1 if expected["observer_compiled"] else 0
                self.assertEqual(
                    set(observed["observer_marker_counts"]),
                    {
                        marker[:-1].decode("ascii")
                        for marker in ATTEST.OBSERVER_MARKERS
                    },
                )
                self.assertTrue(
                    all(
                        count == expected_marker_count
                        for count in observed["observer_marker_counts"].values()
                    )
                )
        self.assertTrue(all(item is False for item in value["classification"].values()))
        self.assertTrue(all(item is False for item in value["operations"].values()))
        self.assertIs(
            value["run_class_constraints"]["headless_timing"]["display"], False
        )
        self.assertIs(
            value["run_class_constraints"]["headless_timing"]["drawing"], False
        )
        raw = ATTEST.canonical_json_bytes(value)
        self.assertEqual(raw, ATTEST.canonical_json_bytes(json.loads(raw)))

    def test_02_manifest_is_strict_and_byte_pinned(self) -> None:
        for label, raw in {
            "duplicate": b'{"a":1,"a":2}\n',
            "nan": b'{"a":NaN}\n',
            "invalid-utf8": b'{"a":"\xff"}\n',
        }.items():
            with self.subTest(label=label), self.assertRaises(
                ATTEST.BinaryAttestationError
            ):
                ATTEST._strict_json(raw, label)
        with self.fixture() as root:
            path = root / ATTEST.BUILD_MANIFEST_PATH
            raw = bytearray(path.read_bytes())
            raw[-2] = ord(" ")
            path.write_bytes(raw)
            with self.assertRaisesRegex(
                ATTEST.BinaryAttestationError, "manifest byte identity"
            ):
                ATTEST.build_attestation(
                    repository_root=root, observed_utc=self.OBSERVED
                )

    def test_03_manifest_types_hashes_and_paths_are_exact(self) -> None:
        mutations = [
            lambda value: value["repository"].__setitem__("remote_count", False),
            lambda value: value["repository"].__setitem__("origin_present", 0),
            lambda value: value["repository"].__setitem__("reference_tree", "0" * 40),
            lambda value: value["toolchain"].__setitem__("pixi_lock_sha256", "0" * 64),
            lambda value: value["builds"]["optimized_fidelity"].__setitem__(
                "binary_sha256", "0" * 64
            ),
            lambda value: value["builds"]["reference_timing"].__setitem__(
                "binary_path", "../dancers"
            ),
            lambda value: value["builds"]["reference_timing"].__setitem__(
                "observer_compiled", 0
            ),
            lambda value: value["gates"].__setitem__("validation_permitted", True),
            lambda value: value.__setitem__("unexpected", None),
        ]
        for index, mutation in enumerate(mutations):
            with self.fixture() as root:
                _, raw = self.rewrite_manifest(root, mutation)
                with mock.patch.object(
                    ATTEST,
                    "EXPECTED_BUILD_MANIFEST_SHA256",
                    hashlib.sha256(raw).hexdigest(),
                ):
                    with self.subTest(index=index), self.assertRaises(
                        ATTEST.BinaryAttestationError
                    ):
                        ATTEST.build_attestation(
                            repository_root=root, observed_utc=self.OBSERVED
                        )

    def test_04_binary_hash_size_missing_and_symlink_fail_closed(self) -> None:
        with self.fixture() as root:
            path = root / ATTEST.EXPECTED_BUILDS["reference_fidelity"]["binary_path"]
            with path.open("ab") as stream:
                stream.write(b"mutation")
            with self.assertRaisesRegex(
                ATTEST.BinaryAttestationError, "size differs"
            ):
                ATTEST.build_attestation(
                    repository_root=root, observed_utc=self.OBSERVED
                )

        with self.fixture() as root:
            path = root / ATTEST.EXPECTED_BUILDS["optimized_timing"]["binary_path"]
            raw = bytearray(path.read_bytes())
            raw[-1] ^= 1
            path.write_bytes(raw)
            with self.assertRaisesRegex(
                ATTEST.BinaryAttestationError, "SHA-256 differs"
            ):
                ATTEST.build_attestation(
                    repository_root=root, observed_utc=self.OBSERVED
                )

        with self.fixture() as root:
            path = root / ATTEST.EXPECTED_BUILDS["reference_timing"]["binary_path"]
            path.unlink()
            with self.assertRaisesRegex(ATTEST.BinaryAttestationError, "cannot open"):
                ATTEST.build_attestation(
                    repository_root=root, observed_utc=self.OBSERVED
                )

        with self.fixture() as root:
            lock = root / ATTEST.PIXI_LOCK_PATH
            target = root / "real-pixi.lock"
            lock.rename(target)
            lock.symlink_to(target)
            with self.assertRaisesRegex(ATTEST.BinaryAttestationError, "symlink"):
                ATTEST.build_attestation(
                    repository_root=root, observed_utc=self.OBSERVED
                )

    def test_04b_stable_read_rejects_post_policy_ancestor_symlink_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(os.path.realpath(temporary))
            safe = root / "safe"
            safe.mkdir()
            candidate = safe / "artifact.bin"
            candidate.write_bytes(b"authenticated")
            real_open = ATTEST.os.open
            swapped = False

            def swap_then_open(path, flags, *args, **kwargs):
                nonlocal swapped
                if path == os.sep and not swapped:
                    swapped = True
                    safe.rename(root / "displaced")
                    safe.symlink_to(root / "displaced", target_is_directory=True)
                return real_open(path, flags, *args, **kwargs)

            with mock.patch.object(ATTEST.os, "open", side_effect=swap_then_open):
                with self.assertRaisesRegex(
                    ATTEST.BinaryAttestationError, "without following symlinks"
                ):
                    ATTEST._read_stable_regular(
                        candidate,
                        maximum_bytes=1024,
                        label="raced artifact",
                        executable=False,
                    )
            self.assertTrue(swapped)

    def test_05_macho_and_observer_marker_inversion_is_rejected(self) -> None:
        header = bytearray(32)
        header[:4] = ATTEST.MACHO_64_ARM64_MAGIC
        header[4:8] = ATTEST.MACHO_CPU_TYPE_ARM64.to_bytes(4, "little")
        header[12:16] = ATTEST.MACHO_FILE_TYPE_EXECUTE.to_bytes(4, "little")
        fidelity = bytes(header) + b"".join(ATTEST.OBSERVER_MARKERS)
        timing = bytes(header)
        counts = ATTEST._verify_macho_observer(fidelity, True, "fidelity")
        self.assertTrue(all(value == 1 for value in counts.values()))
        counts = ATTEST._verify_macho_observer(timing, False, "timing")
        self.assertTrue(all(value == 0 for value in counts.values()))
        with self.assertRaisesRegex(ATTEST.BinaryAttestationError, "observer markers"):
            ATTEST._verify_macho_observer(fidelity, False, "inverted timing")
        with self.assertRaisesRegex(ATTEST.BinaryAttestationError, "observer markers"):
            ATTEST._verify_macho_observer(timing, True, "inverted fidelity")
        with self.assertRaisesRegex(ATTEST.BinaryAttestationError, "observer markers"):
            ATTEST._verify_macho_observer(
                fidelity + ATTEST.OBSERVER_MARKERS[0], True, "duplicate marker"
            )
        with self.assertRaisesRegex(ATTEST.BinaryAttestationError, "Mach-O"):
            ATTEST._verify_macho_observer(b"not-macho" * 8, False, "wrong format")

    def test_06_observed_utc_is_explicit_and_only_determinism_variable(self) -> None:
        invalid = [
            None,
            0,
            "2026-08-11",
            "2026-08-11T12:00:00+00:00",
            "2026-02-30T12:00:00Z",
            "2026-08-11T12:00:00.1Z",
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(
                ATTEST.BinaryAttestationError
            ):
                ATTEST._validate_observed_utc(value)
        with self.fixture() as root:
            first = ATTEST.build_attestation(
                repository_root=root, observed_utc=self.OBSERVED
            )
            second = ATTEST.build_attestation(
                repository_root=root, observed_utc=self.OBSERVED
            )
            later = ATTEST.build_attestation(
                repository_root=root, observed_utc="2026-08-11T12:00:01Z"
            )
        self.assertEqual(
            ATTEST.canonical_json_bytes(first), ATTEST.canonical_json_bytes(second)
        )
        first_without_time = copy.deepcopy(first)
        later_without_time = copy.deepcopy(later)
        first_without_time.pop("observed_utc")
        later_without_time.pop("observed_utc")
        self.assertEqual(first_without_time, later_without_time)

    def test_07_publication_and_cli_are_categorically_disabled(self) -> None:
        with self.fixture() as root:
            value = ATTEST.build_attestation(
                repository_root=root, observed_utc=self.OBSERVED
            )
            output = root / "attestations/binaries.json"
            output.parent.mkdir()
            with mock.patch.object(
                ATTEST,
                "_validate_attestation",
                side_effect=AssertionError("validation reached"),
            ) as validate, mock.patch.object(
                ATTEST.os,
                "open",
                side_effect=AssertionError("open reached"),
            ) as open_bomb:
                with self.assertRaisesRegex(
                    ATTEST.BinaryAttestationError, "categorically disabled"
                ):
                    ATTEST.publish_attestation(
                        value, output_path=output, repository_root=root
                    )
            validate.assert_not_called()
            open_bomb.assert_not_called()
            self.assertFalse(output.exists())

            with mock.patch.object(
                ATTEST,
                "build_attestation",
                side_effect=AssertionError("repository read reached"),
            ) as build_bomb:
                with self.assertRaisesRegex(
                    ATTEST.BinaryAttestationError, "categorically disabled"
                ):
                    ATTEST.attest_and_publish(
                        repository_root=root,
                        observed_utc=self.OBSERVED,
                        output_path=output,
                    )
            build_bomb.assert_not_called()

            stderr = io.StringIO()
            with mock.patch.object(
                ATTEST,
                "attest_and_publish",
                side_effect=AssertionError("attestation capability reached"),
            ) as publisher, redirect_stderr(stderr):
                result = ATTEST.main(
                    (
                        "--output",
                        str(output),
                        "--observed-utc",
                        self.OBSERVED,
                        "--repository-root",
                        str(root),
                    )
                )
            self.assertEqual(result, 2)
            publisher.assert_not_called()
            self.assertIn("nonauthorizing", stderr.getvalue())
            self.assertFalse(output.exists())

            actual_forbidden = (
                ROOT / "comparison/plans/attester-spoof-forbidden.json"
            )
            self.assertFalse(actual_forbidden.exists())
            with self.assertRaisesRegex(
                ATTEST.BinaryAttestationError, "categorically disabled"
            ):
                ATTEST.publish_attestation(
                    value,
                    output_path=actual_forbidden,
                    repository_root=root,
                )
            self.assertFalse(actual_forbidden.exists())

    def test_10_cli_requires_output_and_observed_utc(self) -> None:
        parser = ATTEST._parser()
        for argv in ([], ["--output", "x"], ["--observed-utc", self.OBSERVED]):
            stderr = io.StringIO()
            with self.subTest(argv=argv), redirect_stderr(stderr), self.assertRaises(
                SystemExit
            ) as raised:
                parser.parse_args(argv)
            self.assertEqual(raised.exception.code, 2)
            self.assertIn("required", stderr.getvalue())

    def test_11_source_has_no_execution_build_or_network_surface(self) -> None:
        raw = SCRIPT.read_bytes()
        self.assertEqual(len(raw), EXPECTED_SCRIPT_BYTES)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), EXPECTED_SCRIPT_SHA256)
        source = raw.decode("utf-8")
        tree = ast.parse(source)
        forbidden_imports = {
            "asyncio",
            "http",
            "requests",
            "shutil",
            "socket",
            "subprocess",
            "urllib",
        }
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertFalse(imported & forbidden_imports)

        forbidden_calls = {
            "connect",
            "exec",
            "execv",
            "execve",
            "fork",
            "popen",
            "posix_spawn",
            "run",
            "spawn",
            "system",
            "urlopen",
        }
        call_names: set[str] = set()
        write_lines: list[int] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if isinstance(function, ast.Name):
                call_names.add(function.id)
            elif isinstance(function, ast.Attribute):
                call_names.add(function.attr)
                if (
                    isinstance(function.value, ast.Name)
                    and function.value.id == "os"
                    and function.attr == "write"
                ):
                    write_lines.append(node.lineno)
        self.assertFalse(call_names & forbidden_calls)
        self.assertEqual(len(write_lines), 1)
        publish = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "publish_attestation"
        )
        self.assertTrue(publish.lineno <= write_lines[0] <= publish.end_lineno)
        self.assertNotIn("os.environ", source)
        self.assertNotIn("comparison/plans/simulator-demo", source)
        self.assertNotIn("results/simulator-demo", source)


if __name__ == "__main__":
    unittest.main()
