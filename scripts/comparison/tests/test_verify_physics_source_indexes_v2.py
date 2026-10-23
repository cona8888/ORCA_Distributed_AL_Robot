#!/usr/bin/env python3
"""Read-only and temp-only regressions for physics source indexes v2."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest


REPOSITORY = Path(__file__).resolve().parents[3]
VERIFIER_PATH = REPOSITORY / "scripts/comparison/verify_physics_source_indexes_v2.py"
INDEX_DIRECTORY = REPOSITORY / "comparison/physics-superbuild-v1/source-indexes"


def load_verifier() -> types.ModuleType:
    raw = VERIFIER_PATH.read_bytes()
    module = types.ModuleType("physics_source_indexes_v2_under_test")
    module.__file__ = os.fspath(VERIFIER_PATH)
    module.__package__ = ""
    sys.modules[module.__name__] = module
    exec(compile(raw, os.fspath(VERIFIER_PATH), "exec"), module.__dict__)
    return module


verifier = load_verifier()


def canonical_bytes(value):
    return (json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ) + "\n").encode("utf-8")


class PhysicsSourceIndexesV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.live = {
            name: (INDEX_DIRECTORY / name).read_bytes()
            for name in verifier.ARTIFACT_IDENTITIES
        }

    def _copy_fixture(self, destination):
        for name, raw in self.live.items():
            (destination / name).write_bytes(raw)

    def _run_cli(self, directory=None):
        command = [sys.executable, "-B", os.fspath(VERIFIER_PATH)]
        if directory is not None:
            command.append(os.fspath(directory))
        return subprocess.run(command, check=False, capture_output=True, timeout=120)

    def test_01_exact_canonical_file_identities_are_frozen(self):
        self.assertEqual(set(self.live), set(verifier.ARTIFACT_IDENTITIES))
        for name, raw in self.live.items():
            with self.subTest(name=name):
                expected = verifier.ARTIFACT_IDENTITIES[name]
                self.assertEqual(len(raw), expected["bytes"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected["sha256"])
                value = json.loads(raw)
                self.assertEqual(canonical_bytes(value), raw)
                if name == verifier.HANDOFF_NAME:
                    self.assertEqual(
                        value["source_input_handoff_sha256"],
                        expected["source_input_handoff_sha256"],
                    )
                    self.assertTrue(all(flag is False for flag in value["classification"].values()))
                else:
                    self.assertEqual(value["root_id"], expected["root_id"])
                    self.assertEqual(value["source_index_id"], expected["source_index_id"])

    def test_02_live_verification_recomputes_the_git_closure(self):
        result = verifier.verify_source_indexes()
        self.assertTrue(result["valid"])
        self.assertTrue(result["scope"]["git_closure_recomputed_read_only"])
        self.assertTrue(result["scope"]["source_indexes_verified"])
        self.assertTrue(all(flag is False for flag in result["classification"].values()))
        self.assertTrue(all(
            value is False
            for key, value in result["scope"].items()
            if key.endswith("created_by_verifier")
            or key == "verifier_invoked_configure_build_or_simulator"
        ))

    def test_03_cli_emits_one_canonical_nonauthorizing_verdict(self):
        completed = self._run_cli()
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
        self.assertEqual(completed.stderr, b"")
        self.assertTrue(completed.stdout.endswith(b"\n"))
        verdict = json.loads(completed.stdout)
        self.assertTrue(verdict["valid"])
        self.assertTrue(all(flag is False for flag in verdict["classification"].values()))

    def test_04_an_exact_temp_copy_verifies_without_creating_repository_files(self):
        before = set(path.relative_to(REPOSITORY) for path in REPOSITORY.rglob("*"))
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            self._copy_fixture(directory)
            result = verifier.verify_source_indexes(directory)
            self.assertTrue(result["valid"])
        after = set(path.relative_to(REPOSITORY) for path in REPOSITORY.rglob("*"))
        self.assertEqual(after, before)

    def test_05_temp_only_content_mutations_fail_closed(self):
        mutations = {
            "entry-sha": ("optimized-source-v2.json", lambda item: item["entries"][0].__setitem__("sha256", "0" * 64)),
            "root-tree": ("reference-source-v2.json", lambda item: item.__setitem__("root_tree_sha256", "0" * 64)),
            "endpoint": ("common-source-v2.json", lambda item: item["endpoint_anchors"][0].__setitem__("tree", "0" * 40)),
            "authorization": (verifier.HANDOFF_NAME, lambda item: item["classification"].__setitem__("build_authorized", True)),
            "action-plan-edge": (verifier.HANDOFF_NAME, lambda item: item.__setitem__("prospective_action_plan_sha256", "0" * 64)),
        }
        for label, (name, mutate) in mutations.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary).resolve()
                self._copy_fixture(directory)
                value = json.loads((directory / name).read_bytes())
                mutate(value)
                (directory / name).write_bytes(canonical_bytes(value))
                with self.assertRaises(verifier.SourceIndexVerificationError):
                    verifier.verify_source_indexes(directory)

    def test_06_temp_only_order_inventory_and_link_mutations_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            self._copy_fixture(directory)
            value = json.loads((directory / "optimized-source-v2.json").read_bytes())
            value["entries"][0], value["entries"][1] = value["entries"][1], value["entries"][0]
            (directory / "optimized-source-v2.json").write_bytes(canonical_bytes(value))
            with self.assertRaises(verifier.SourceIndexVerificationError):
                verifier.verify_source_indexes(directory)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            self._copy_fixture(directory)
            (directory / "extra.json").write_bytes(b"{}\n")
            with self.assertRaisesRegex(verifier.SourceIndexVerificationError, "inventory"):
                verifier.verify_source_indexes(directory)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            self._copy_fixture(directory)
            target = directory / "optimized-source-v2.json"
            target.unlink()
            target.symlink_to(INDEX_DIRECTORY / "optimized-source-v2.json")
            with self.assertRaises(verifier.SourceIndexVerificationError):
                verifier.verify_source_indexes(directory)

    def test_07_hardlinked_temp_artifact_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            self._copy_fixture(directory)
            original = directory / "common-source-v2.json"
            alias = directory / "alias"
            os.link(os.fspath(original), os.fspath(alias))
            alias.unlink()
            # The link count returns to one after unlink; retain a hard link in a
            # sibling directory so the exact artifact inventory remains four.
            sibling = directory.parent / (directory.name + "-hardlink")
            os.link(os.fspath(original), os.fspath(sibling))
            try:
                with self.assertRaisesRegex(verifier.SourceIndexVerificationError, "links"):
                    verifier.verify_source_indexes(directory)
            finally:
                sibling.unlink()

    def test_08_helpers_are_authenticated_before_execution(self):
        source = VERIFIER_PATH.read_text(encoding="utf-8")
        self.assertNotIn("import generate_physics_source_indexes_v2", source)
        self.assertNotIn("import validate_physics_protected_source_roots_contract_v2", source)
        self.assertIn("_load_authenticated_module", source)
        self.assertIn("terminal reauthentication differs", source)
        self.assertIn("source-index directory final named identity differs", source)
        self.assertIn("source-index directory terminal named reauthentication differs", source)
        self.assertIn("os.stat(name, dir_fd=directory_fd, follow_symlinks=False)", source)
        self.assertIn("os.stat(os.fspath(canonical), follow_symlinks=False)", source)
        self.assertGreater(verifier.CONTRACT_VALIDATOR_BYTES, 0)
        self.assertGreater(verifier.SOURCE_GENERATOR_BYTES, 0)
        self.assertNotEqual(verifier.SOURCE_GENERATOR_SHA256, "0" * 64)

    def test_09_mutated_or_linked_helper_source_cannot_execute(self):
        helper = REPOSITORY / "scripts/comparison/validate_physics_protected_source_roots_contract_v2.py"
        raw = bytearray(helper.read_bytes())
        raw[0] ^= 1
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            changed = directory / "validator.py"
            changed.write_bytes(bytes(raw))
            with self.assertRaisesRegex(verifier.SourceIndexVerificationError, "exact-byte identity"):
                verifier._load_authenticated_module(
                    changed,
                    verifier.CONTRACT_VALIDATOR_BYTES,
                    verifier.CONTRACT_VALIDATOR_SHA256,
                    "mutated_validator_under_test",
                )
            link = directory / "validator-link.py"
            link.symlink_to(helper)
            with self.assertRaisesRegex(verifier.SourceIndexVerificationError, "symlink ancestor"):
                verifier._load_authenticated_module(
                    link,
                    verifier.CONTRACT_VALIDATOR_BYTES,
                    verifier.CONTRACT_VALIDATOR_SHA256,
                    "linked_validator_under_test",
                )

    def test_10_verifier_contains_no_write_build_network_or_materializer_surface(self):
        source = VERIFIER_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "import socket", "import urllib", "import requests", "os.mkdir",
            "os.rename", "os.replace", "os.link(", "write_bytes(", "CMakeLists",
            "cmake", "ninja", "make", "checkout", "archive",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
