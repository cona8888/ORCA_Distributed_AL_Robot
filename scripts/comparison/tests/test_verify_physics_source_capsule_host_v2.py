#!/usr/bin/env python3
"""Temp-only adversarial regressions for source-capsule host probing v2."""

from __future__ import annotations

import copy
import ctypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[3]
PROBE_PATH = REPOSITORY / "scripts/comparison/probe_physics_source_capsule_host_v2.py"
VERIFIER_PATH = REPOSITORY / "scripts/comparison/verify_physics_source_capsule_host_v2.py"


def load_source(path: Path, name: str) -> types.ModuleType:
    raw = path.read_bytes()
    module = types.ModuleType(name)
    module.__file__ = os.fspath(path)
    module.__package__ = ""
    sys.modules[name] = module
    exec(compile(raw, os.fspath(path), "exec"), module.__dict__)
    return module


probe = load_source(PROBE_PATH, "physics_source_capsule_host_probe_v2_under_test")
verifier = load_source(
    VERIFIER_PATH, "physics_source_capsule_host_verifier_v2_under_test"
)


def canonical_bytes(value) -> bytes:
    return (json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ) + "\n").encode("utf-8")


class _PrivateParent:
    def __init__(self) -> None:
        self.temporary = None
        self.path = None

    def __enter__(self) -> Path:
        self.temporary = tempfile.TemporaryDirectory(prefix=probe.PARENT_PREFIX)
        os.chmod(self.temporary.name, 0o700)
        self.path = Path(os.path.realpath(self.temporary.name))
        return self.path

    def __exit__(self, exception_type, exception, traceback) -> None:
        assert self.temporary is not None
        self.temporary.cleanup()


def run_python(path: Path, *arguments: str) -> subprocess.CompletedProcess:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-B", os.fspath(path), *arguments],
        cwd=os.fspath(REPOSITORY),
        env=environment,
        check=False,
        capture_output=True,
        timeout=60,
    )


class _FakeFunction:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.argtypes = None
        self.restype = None

    def __call__(self, *arguments):
        return self.callback(*arguments)


class _FakeXattrLibrary:
    def __init__(self, list_callback, get_callback=None) -> None:
        if get_callback is None:
            get_callback = lambda *arguments: 0
        self.flistxattr = _FakeFunction(list_callback)
        self.fgetxattr = _FakeFunction(get_callback)


class PhysicsSourceCapsuleHostV2Tests(unittest.TestCase):
    def test_01_exact_sources_and_live_current_host_verifier(self) -> None:
        probe_raw = PROBE_PATH.read_bytes()
        verifier_raw = VERIFIER_PATH.read_bytes()
        self.assertEqual(len(probe_raw), verifier.PROBE_BYTES)
        self.assertEqual(hashlib.sha256(probe_raw).hexdigest(), verifier.PROBE_SHA256)
        self.assertEqual(len(verifier_raw), 17_455)
        self.assertEqual(
            hashlib.sha256(verifier_raw).hexdigest(),
            "55b30ce7d8fd8c55b4955ff49ae09ab402059789c972b19d8c18a8b867ca472d",
        )

        verdict = verifier.verify_current_host()
        self.assertTrue(verdict["valid"])
        self.assertFalse(verdict["host_metadata_profile_eligible"])
        self.assertEqual(verdict["blockers"], ["xattrs-absent-unavailable"])
        self.assertTrue(all(value is False for value in verdict["classification"].values()))

        completed = run_python(VERIFIER_PATH)
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
        self.assertEqual(completed.stderr, b"")
        self.assertEqual(canonical_bytes(json.loads(completed.stdout)), completed.stdout)
        self.assertEqual(json.loads(completed.stdout), verdict)

    def test_02_probe_cli_is_canonical_exit_two_and_cleans_parent(self) -> None:
        with _PrivateParent() as parent:
            completed = run_python(PROBE_PATH, os.fspath(parent))
            self.assertEqual(completed.returncode, 2, completed.stderr.decode("utf-8", "replace"))
            self.assertEqual(completed.stderr, b"")
            value = json.loads(completed.stdout)
            self.assertEqual(probe.canonical_verdict_bytes(value), completed.stdout)
            verifier.validate_host_verdict(value, probe, require_current_host_blocker=True)
            self.assertEqual(list(parent.iterdir()), [])
            self.assertTrue(value["cleanup"]["complete"])
            self.assertEqual(value["cleanup"]["residual_paths"], [])
            self.assertEqual(value["cleanup"]["parent_inventory_after"], [])

    def test_03_relative_wrong_mode_nonempty_and_symlink_parents_reject(self) -> None:
        with self.assertRaisesRegex(probe.HostProbeError, "absolute path"):
            probe.probe_host(Path("relative-parent"))

        with _PrivateParent() as parent:
            os.chmod(parent, 0o755)
            with self.assertRaisesRegex(probe.HostProbeError, "mode 0700"):
                probe.probe_host(parent)
            self.assertEqual(list(parent.iterdir()), [])
            os.chmod(parent, 0o700)

        with _PrivateParent() as parent:
            marker = parent / "preexisting"
            marker.write_bytes(b"not fresh\n")
            with self.assertRaisesRegex(probe.HostProbeError, "fresh and empty"):
                probe.probe_host(parent)
            self.assertEqual([item.name for item in parent.iterdir()], ["preexisting"])
            marker.unlink()

        temporary_root = Path(os.path.realpath(tempfile.gettempdir()))
        target = Path(tempfile.mkdtemp(prefix="dancers-host-probe-symlink-target-"))
        link = temporary_root / (probe.PARENT_PREFIX + secrets.token_hex(16))
        try:
            os.chmod(target, 0o700)
            os.symlink(os.fspath(target), os.fspath(link))
            with self.assertRaises((OSError, probe.HostProbeError)):
                probe.probe_host(link)
            self.assertEqual(list(target.iterdir()), [])
        finally:
            if link.is_symlink():
                link.unlink()
            target.rmdir()

    def test_04_probe_and_policy_bytes_are_authenticated_before_execution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="dancers-mutated-probe-source-") as raw:
            repository = Path(os.path.realpath(raw))
            copied_probe = repository / verifier.PROBE_RELATIVE
            copied_probe.parent.mkdir(parents=True)
            mutated = bytearray(PROBE_PATH.read_bytes())
            mutated[-2] ^= 1
            copied_probe.write_bytes(bytes(mutated))
            with self.assertRaisesRegex(
                verifier.HostProbeVerificationError, "SHA-256 differs"
            ):
                verifier._read_probe_source(repository)

        with tempfile.TemporaryDirectory(prefix="dancers-mutated-policy-") as raw:
            repository = Path(os.path.realpath(raw))
            for relative in probe.TRUSTED_FILES:
                destination = repository / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPOSITORY / relative, destination)
            relative = sorted(probe.TRUSTED_FILES, key=lambda item: item.encode("utf-8"))[0]
            target = repository / relative
            mutated = bytearray(target.read_bytes())
            mutated[-2] ^= 1
            target.write_bytes(bytes(mutated))
            with _PrivateParent() as parent:
                with mock.patch.object(probe, "_repository_root", return_value=repository):
                    with self.assertRaisesRegex(probe.HostProbeError, "SHA-256 differs"):
                        probe.probe_host(parent)
                self.assertEqual(list(parent.iterdir()), [])

    @unittest.skipUnless(sys.platform == "darwin", "Darwin extended ACL probe")
    def test_05_real_darwin_acl_is_detected_and_rejected_on_parent(self) -> None:
        with _PrivateParent() as parent:
            subprocess.run(
                ["/bin/chmod", "+a", "everyone allow read", os.fspath(parent)],
                check=True,
                capture_output=True,
                timeout=10,
            )
            descriptor = os.open(parent, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY)
            try:
                self.assertTrue(probe._acl_present(descriptor, "real ACL fixture"))
            finally:
                os.close(descriptor)
            try:
                with self.assertRaisesRegex(probe.HostProbeError, "unsafe ACLs"):
                    probe.probe_host(parent)
                self.assertEqual(list(parent.iterdir()), [])
            finally:
                subprocess.run(
                    ["/bin/chmod", "-N", os.fspath(parent)],
                    check=True,
                    capture_output=True,
                    timeout=10,
                )

    def test_06_cleanup_failure_propagates_without_a_clean_verdict(self) -> None:
        with _PrivateParent() as parent:
            with mock.patch.object(
                probe.os, "unlink", side_effect=PermissionError("synthetic unlink denial")
            ):
                with self.assertRaisesRegex(
                    probe.HostProbeCleanupError, "cleanup residual"
                ):
                    probe.probe_host(parent)
            residuals = list(parent.iterdir())
            self.assertEqual(len(residuals), 1)
            self.assertTrue(residuals[0].name.startswith(probe.PROBE_PREFIX))
            leaf = residuals[0] / probe.PROBE_FILE
            self.assertEqual(leaf.read_bytes(), probe.PROBE_BYTES)
            leaf.unlink()
            residuals[0].rmdir()
            self.assertEqual(list(parent.iterdir()), [])

    def test_07_classification_blocker_cleanup_and_scope_mutations_reject(self) -> None:
        with _PrivateParent() as parent:
            baseline = probe.probe_host(parent)
            verifier.validate_host_verdict(baseline, probe, require_current_host_blocker=True)
            mutations = []

            changed = copy.deepcopy(baseline)
            changed["classification"]["build_authorized"] = True
            mutations.append(("authorization", changed))

            changed = copy.deepcopy(baseline)
            changed["blockers"] = []
            mutations.append(("blocker", changed))

            changed = copy.deepcopy(baseline)
            changed["cleanup"]["complete"] = False
            mutations.append(("cleanup-complete", changed))

            changed = copy.deepcopy(baseline)
            changed["cleanup"]["residual_paths"] = ["synthetic-residual"]
            mutations.append(("cleanup-residual", changed))

            changed = copy.deepcopy(baseline)
            changed["scope"]["root_manifests_issued"] = True
            mutations.append(("root-manifest-scope", changed))

            changed = copy.deepcopy(baseline)
            changed["observations"][0]["xattrs"][0]["sha256"] = "0" * 64
            mutations.append(("provenance-record", changed))

            for label, value in mutations:
                with self.subTest(label=label), self.assertRaises(
                    verifier.HostProbeVerificationError
                ):
                    verifier.validate_host_verdict(
                        value, probe, require_current_host_blocker=True
                    )
            self.assertEqual(list(parent.iterdir()), [])

    def test_08_xattr_enumeration_and_verdict_limits_fail_closed(self) -> None:
        too_large = probe.MAX_XATTR_LIST_BYTES + 1
        library = _FakeXattrLibrary(lambda *arguments: too_large)
        with mock.patch.object(probe, "_libc", return_value=library):
            with self.assertRaisesRegex(probe.HostProbeError, "list exceeds"):
                probe._xattrs(0, "oversized-list")

        names = b"".join(
            ("user.bound-%03d\0" % index).encode("ascii")
            for index in range(probe.MAX_XATTR_COUNT + 1)
        )

        def list_names(*arguments):
            buffer = arguments[1]
            if buffer is None:
                return len(names)
            ctypes.memmove(buffer, names, len(names))
            return len(names)

        library = _FakeXattrLibrary(list_names)
        with mock.patch.object(probe, "_libc", return_value=library):
            with self.assertRaisesRegex(probe.HostProbeError, "inventory is invalid"):
                probe._xattrs(0, "too-many-names")

        one_name = b"user.large-value\0"

        def list_one(*arguments):
            buffer = arguments[1]
            if buffer is None:
                return len(one_name)
            ctypes.memmove(buffer, one_name, len(one_name))
            return len(one_name)

        def oversized_value(*arguments):
            return probe.MAX_XATTR_VALUE_BYTES + 1

        library = _FakeXattrLibrary(list_one, oversized_value)
        with mock.patch.object(probe, "_libc", return_value=library):
            with self.assertRaisesRegex(probe.HostProbeError, "value exceeds"):
                probe._xattrs(0, "oversized-value")

        records = [
            {"name": "user.%03d" % index, "bytes": 0, "sha256": "0" * 64}
            for index in range(probe.MAX_XATTR_COUNT + 1)
        ]
        with self.assertRaisesRegex(
            verifier.HostProbeVerificationError, "xattr list differs"
        ):
            verifier._validate_xattrs(records, "bounded-verdict")

    def test_09_canonical_path_and_json_limits_fail_closed(self) -> None:
        invalid_paths = (
            Path("relative"),
            Path("//tmp/double-leading-slash"),
            Path("/tmp/e\u0301"),
            Path("/" + "a" * (probe.MAX_PATH_BYTES + 1)),
        )
        for path in invalid_paths:
            with self.subTest(path=os.fspath(path)), self.assertRaises(probe.HostProbeError):
                probe._canonical_absolute(path, "bounded path")

        nested = None
        for _ in range(probe.MAX_JSON_DEPTH + 2):
            nested = [nested]
        invalid_values = (
            {"nested": nested},
            {"string": "x" * (probe.MAX_STRING_BYTES + 1)},
            {"float": 1.0},
            {"integer": 1 << 63},
            {"oversized": ["x" * probe.MAX_STRING_BYTES for _ in range(33)]},
        )
        for value in invalid_values:
            with self.subTest(keys=list(value)), self.assertRaises(probe.HostProbeError):
                probe.canonical_verdict_bytes(value)

    def test_10_probe_never_issues_roots_manifests_official_paths_or_pycs(self) -> None:
        with _PrivateParent() as parent:
            verdict = probe.probe_host(parent)
            self.assertEqual(list(parent.iterdir()), [])
        self.assertFalse(verdict["scope"]["source_roots_materialized"])
        self.assertFalse(verdict["scope"]["root_manifests_issued"])
        self.assertFalse(verdict["scope"]["official_namespace_materialized"])
        self.assertFalse(verdict["scope"]["production_materializer_implemented"])
        self.assertFalse(verdict["scope"]["production_verifier_invoked"])
        self.assertFalse(verdict["classification"]["production_source_roots_materialized"])
        self.assertFalse(verdict["classification"]["official_namespace_materialized"])

        targets = (
            "probe_physics_source_capsule_host_v2",
            "verify_physics_source_capsule_host_v2",
            "test_verify_physics_source_capsule_host_v2",
        )
        for directory in (
            REPOSITORY / "scripts/comparison/__pycache__",
            REPOSITORY / "scripts/comparison/tests/__pycache__",
        ):
            if directory.is_dir():
                for item in directory.iterdir():
                    self.assertFalse(
                        item.suffix == ".pyc"
                        and any(item.name.startswith(target + ".") for target in targets),
                        os.fspath(item),
                    )


if __name__ == "__main__":
    unittest.main()
