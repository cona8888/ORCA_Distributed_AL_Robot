from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "scripts/comparison/verify_physics_source_lift_v1.py"
SPEC = importlib.util.spec_from_file_location("verify_physics_source_lift_v1", MODULE_PATH)
assert SPEC and SPEC.loader
VERIFY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VERIFY
SPEC.loader.exec_module(VERIFY)


def manifest() -> dict[str, object]:
    return json.loads((REPO_ROOT / VERIFY.DEFAULT_MANIFEST).read_text(encoding="utf-8"))


def save_manifest(root: Path, value: dict[str, object]) -> tuple[Path, str]:
    raw = VERIFY.canonical_json_bytes(value)
    path = root / "mutated.json"
    path.write_bytes(raw)
    path.chmod(0o644)
    return path, hashlib.sha256(raw).hexdigest()


class PhysicsSourceLiftV1Tests(unittest.TestCase):
    def test_live_checkpoint_reconstructs_exact_non_authorizing_feasibility(self) -> None:
        calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
        real_popen = VERIFY.subprocess.Popen

        def record(command: list[str], **kwargs: object):
            calls.append((tuple(command), kwargs))
            return real_popen(command, **kwargs)

        with mock.patch.object(VERIFY.subprocess, "Popen", side_effect=record):
            result = VERIFY.verify_repository(REPO_ROOT)
        self.assertTrue(result["historical_source_lift_feasibility_verified"])
        self.assertEqual(result["reference_prephysics_tree"], VERIFY.R_PRE_TREE)
        self.assertEqual(result["resolution_required_paths"], list(VERIFY.CONFLICT_PATCHES))
        self.assertFalse(result["build_capture_complete"])
        self.assertFalse(result["final_source_endpoints_frozen"])
        self.assertFalse(result["promotion_eligible"])
        self.assertFalse(result["scientific_evidence"])
        self.assertFalse(result["scientific_execution_authorized"])
        allowed = {"cat-file", "config", "diff", "ls-tree", "merge-base", "patch-id", "remote", "replace", "rev-list", "rev-parse"}
        observed = []
        for command, keywords in calls:
            self.assertEqual(command[0], VERIFY.GIT_EXECUTABLE)
            self.assertEqual(command[1], "--no-pager")
            index = command.index("-C")
            observed.append(command[index + 2])
            self.assertEqual(keywords["env"], VERIFY.GIT_ENVIRONMENT)
            self.assertIs(keywords["start_new_session"], True)
            self.assertIs(keywords["close_fds"], True)
        self.assertTrue(observed)
        self.assertLessEqual(set(observed), allowed)

    def test_manifest_hash_is_an_independent_gate(self) -> None:
        with self.assertRaises(VERIFY.SourceLiftError):
            VERIFY.verify_repository(REPO_ROOT, expected_manifest_sha256="0" * 64)

    def test_classification_allowlist_and_future_completion_mutations_reject_early(self) -> None:
        mutations = []
        changed = manifest()
        changed["classification"]["promotion_eligible"] = True  # type: ignore[index]
        mutations.append(changed)
        changed = manifest()
        changed["allowed_method_delta"]["paths"][0] = "src/not-registered.cpp"  # type: ignore[index]
        mutations.append(changed)
        changed = manifest()
        changed["commutative_proof_requirements"]["final_reference_source_tree"] = "0" * 40  # type: ignore[index]
        mutations.append(changed)
        changed = manifest()
        changed["commutative_proof_requirements"]["required_rules"][0] = "weakened rule"  # type: ignore[index]
        mutations.append(changed)
        changed = manifest()
        changed["repository_contract"]["forbidden_git_operations"] = []  # type: ignore[index]
        mutations.append(changed)
        for value in mutations:
            with self.subTest(value=value), self.assertRaises(VERIFY.SourceLiftError):
                VERIFY._validate_manifest_shape(value)

    def test_prephysics_tree_and_resolution_placeholders_are_semantic_gates(self) -> None:
        for mutation in ("prephysics-tree", "resolution", "resolution-requirement"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                changed = manifest()
                if mutation == "prephysics-tree":
                    changed["prephysics_lift"]["reference"]["tree"] = "0" * 40  # type: ignore[index]
                elif mutation == "resolution":
                    changed["reverse_apply_probe"]["conflicts"][0]["resolved_blob"] = "0" * 40  # type: ignore[index]
                else:
                    changed["reverse_apply_probe"]["resolution_requirements"][0] = "weakened requirement"  # type: ignore[index]
                path, digest = save_manifest(Path(temporary).resolve(), changed)
                with self.assertRaisesRegex(VERIFY.SourceLiftError, "caller-supplied manifest identity"):
                    VERIFY.verify_repository(REPO_ROOT, path, digest)
                with self.assertRaises(VERIFY.SourceLiftError):
                    VERIFY._validate_manifest_shape(changed)

    def test_nested_metadata_key_sets_are_exact(self) -> None:
        mutations = []
        changed = manifest()
        changed["allowed_method_delta"]["historical_reverse_patch"]["extra"] = None  # type: ignore[index]
        mutations.append(changed)
        changed = manifest()
        changed["prephysics_lift"]["corner_entries"][0]["optimized"]["extra"] = None  # type: ignore[index]
        mutations.append(changed)
        changed = manifest()
        changed["scaffold_endpoint_probe"]["prephysics_to_scaffold_patch"].pop("from_commit")  # type: ignore[index]
        mutations.append(changed)
        for changed in mutations:
            with self.subTest(changed=changed), self.assertRaises(VERIFY.SourceLiftError):
                VERIFY._validate_manifest_shape(changed)

    def test_git_reader_rejects_mutating_or_unlisted_operations_before_launch(self) -> None:
        reader = VERIFY.GitReader(REPO_ROOT)
        attempts = [
            ["archive"], ["checkout"], ["commit"], ["hash-object"],
            ["merge-tree"], ["read-tree"], ["status"], ["update-index"],
            ["worktree"],
            ["replace", "a" * 40, "b" * 40],
            ["diff", "--output=/private/tmp/source-lift-must-not-write", VERIFY.R_TRACE, VERIFY.R_TRACE],
            ["config", "--local", "--add", "remote.origin.url", "https://invalid"],
            ["cat-file", "--filters", "a" * 40],
            ["rev-parse", "--config-env=x=y"],
        ]
        with mock.patch.object(VERIFY.subprocess, "Popen") as popen:
            for attempt in attempts:
                with self.subTest(attempt=attempt), self.assertRaises(VERIFY.SourceLiftError):
                    reader.run(attempt)
        popen.assert_not_called()
        self.assertEqual(reader.commands, [])

    def test_caller_path_and_output_cap_cannot_replace_or_relax_git(self) -> None:
        with mock.patch.dict(os.environ, {"PATH": "/definitely/untrusted"}):
            reader = VERIFY.GitReader(REPO_ROOT)
        self.assertEqual(reader.git, "/usr/bin/git")
        self.assertNotIn("maximum", inspect.signature(reader.run).parameters)
        with mock.patch.object(VERIFY, "MAX_SMALL_GIT_OUTPUT_BYTES", 0):
            with self.assertRaisesRegex(VERIFY.SourceLiftError, "fixed output bound"):
                reader.run(["rev-parse", "--show-object-format"])

    def test_git_timeout_kills_the_bounded_read(self) -> None:
        reader = VERIFY.GitReader(REPO_ROOT)
        with mock.patch.object(VERIFY, "GIT_TIMEOUT_SECONDS", 0.0):
            with self.assertRaisesRegex(VERIFY.SourceLiftError, "timed out"):
                reader.run(["rev-parse", "--show-object-format"])

    def test_remote_partial_clone_and_alternate_storage_reject(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            objects = root / "objects"
            (objects / "info").mkdir(parents=True)

            def reader(remote: bytes = b"", keys: bytes = b""):
                fake = mock.Mock()
                fake.root = root
                fake.run.side_effect = lambda args: (
                    remote if args == ["remote"] else
                    keys if args[0] == "config" else
                    (str(objects) + "\n").encode()
                )
                return fake

            with self.assertRaisesRegex(VERIFY.SourceLiftError, "zero configured remotes"):
                VERIFY._validate_repository_storage(reader(remote=b"origin\n"))
            with self.assertRaisesRegex(VERIFY.SourceLiftError, "partial-clone configuration"):
                VERIFY._validate_repository_storage(reader(keys=b"extensions.partialclone\0"))
            (objects / "info" / "alternates").write_text("/untrusted\n", encoding="utf-8")
            with self.assertRaisesRegex(VERIFY.SourceLiftError, "alternate object store"):
                VERIFY._validate_repository_storage(reader())

    def test_static_git_surface_has_lazy_fetch_and_no_write_api(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertNotIn("shutil.which", source)
        self.assertNotIn("subprocess.run", source)
        self.assertIn('"GIT_NO_LAZY_FETCH": "1"', source)
        self.assertIn("start_new_session=True", source)
        self.assertIn("os.killpg", source)
        forbidden_calls = {"chmod", "mkdir", "rename", "replace", "symlink", "unlink", "write_bytes", "write_text"}
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertTrue(calls.isdisjoint(forbidden_calls))

    def test_public_result_and_receipt_cannot_be_misread_as_final(self) -> None:
        result = VERIFY.verify_repository(REPO_ROOT)
        self.assertTrue(result["future_post_port_superbuild_source_lift_record_required"])
        self.assertFalse(result["scaffold_endpoint_is_final"])
        self.assertNotIn("final_reference_source_tree", result)
        self.assertNotIn("final_optimized_source_tree", result)


if __name__ == "__main__":
    unittest.main()
