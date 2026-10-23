#!/usr/bin/env python3
"""Regression and mutation tests for the final source-lift record verifier."""

from __future__ import annotations

import copy
import builtins
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "scripts/comparison/verify_physics_source_lift_record_v1.py"
SPEC = importlib.util.spec_from_file_location("verify_physics_source_lift_record_v1", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("unable to load source-lift record verifier")
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class SourceLiftRecordVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest_path = REPO_ROOT / VERIFY.DEFAULT_MANIFEST
        cls.raw = cls.manifest_path.read_bytes()
        cls.record = json.loads(cls.raw)

    def _write(self, value: object, *, canonical: bool = True) -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
        temporary = tempfile.TemporaryDirectory(dir="/private/tmp")
        path = Path(temporary.name) / "record.json"
        raw = (VERIFY.feasibility.canonical_json_bytes(value) if canonical
               else json.dumps(value, indent=2).encode("utf-8") + b"\n")
        path.write_bytes(raw)
        return temporary, path, hashlib.sha256(raw).hexdigest()

    def _semantic_reject(self, value: object, pattern: str = "differs|invalid|wrong|reject|malformed|does not") -> None:
        temporary, path, digest = self._write(value)
        try:
            with mock.patch.object(VERIFY, "EXPECTED_MANIFEST_SHA256", digest):
                with self.assertRaisesRegex(VERIFY.SourceLiftRecordError, pattern):
                    VERIFY.verify_repository(REPO_ROOT, path, digest)
        finally:
            temporary.cleanup()

    @staticmethod
    def _rebind_resolution_bundle(record: dict[str, object]) -> str:
        allowed = record["allowed_method_delta"]
        assert isinstance(allowed, dict)
        records = allowed["path_records"]
        resolution_paths = allowed["resolution_paths"]
        assert isinstance(records, list) and isinstance(resolution_paths, list)
        selected = [item for item in records if isinstance(item, dict) and item.get("path") in resolution_paths]
        fragment = json.dumps(
            selected, sort_keys=True, separators=(",", ":"),
            ensure_ascii=False, allow_nan=False,
        ).encode("utf-8")
        digest = hashlib.sha256(VERIFY.RESOLUTION_DOMAIN + fragment).hexdigest()
        bundle = allowed["resolution_bundle"]
        assert isinstance(bundle, dict)
        bundle["sha256"] = digest
        return digest

    def _naive_candidate_rejects(self, path_name: str) -> None:
        changed = copy.deepcopy(self.record)
        records = changed["allowed_method_delta"]["path_records"]
        item = next(value for value in records if value["path"] == path_name)
        rejected = item["resolution"]["rejected_naive_candidate"]
        self.assertIsNotNone(rejected)
        item["reference_final"] = {
            "bytes": rejected["bytes"],
            "mode": "100644",
            "oid": rejected["oid"],
            "sha256": rejected["sha256"],
            "type": "blob",
        }
        bundle_digest = self._rebind_resolution_bundle(changed)
        temporary, manifest, digest = self._write(changed)
        try:
            with mock.patch.multiple(
                    VERIFY,
                    EXPECTED_MANIFEST_SHA256=digest,
                    RESOLUTION_BUNDLE_SHA256=bundle_digest):
                with self.assertRaises(VERIFY.SourceLiftRecordError):
                    VERIFY.verify_repository(REPO_ROOT, manifest, digest)
        finally:
            temporary.cleanup()

    def test_canonical_record_identity(self) -> None:
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), VERIFY.EXPECTED_MANIFEST_SHA256)
        self.assertEqual(self.raw, VERIFY.feasibility.canonical_json_bytes(self.record))
        self.assertEqual(self.record["schema_version"], VERIFY.SCHEMA)

    def test_live_repository_verifies(self) -> None:
        result = VERIFY.verify_repository(REPO_ROOT)
        self.assertTrue(result["source_lift_verified"])
        self.assertTrue(result["final_source_endpoints_frozen"])
        self.assertEqual(result["reference_source_tree"], VERIFY.R_FINAL_TREE)
        self.assertEqual(result["resolution_bundle_sha256"], VERIFY.RESOLUTION_BUNDLE_SHA256)
        self.assertFalse(result["build_capture_complete"])
        self.assertFalse(result["promotion_eligible"])
        self.assertFalse(result["scientific_evidence"])

    def test_predecessor_verifier_loaded_from_pinned_bytes(self) -> None:
        path = REPO_ROOT / "scripts/comparison/verify_physics_source_lift_v1.py"
        raw = path.read_bytes()
        self.assertEqual(len(raw), VERIFY.TRUSTED_FEASIBILITY_VERIFIER_BYTES)
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            VERIFY.TRUSTED_FEASIBILITY_VERIFIER_SHA256,
        )
        self.assertEqual(Path(VERIFY.feasibility.__file__), path)

    def test_substituted_predecessor_rejects_before_top_level_execution(self) -> None:
        sentinel = "_dancers_untrusted_predecessor_executed"
        if hasattr(builtins, sentinel):
            delattr(builtins, sentinel)
        prefix = (
            "import builtins\n"
            f"builtins.{sentinel} = True\n"
        ).encode("utf-8")
        self.assertLess(len(prefix), VERIFY.TRUSTED_FEASIBILITY_VERIFIER_BYTES)
        candidate = prefix + b"#" * (
            VERIFY.TRUSTED_FEASIBILITY_VERIFIER_BYTES - len(prefix)
        )
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "verify_physics_source_lift_v1.py"
            path.write_bytes(candidate)
            with self.assertRaisesRegex(RuntimeError, "SHA-256 differs"):
                VERIFY._load_trusted_feasibility_module(path)
        self.assertFalse(hasattr(builtins, sentinel))

    def test_noncanonical_serialization_rejects(self) -> None:
        temporary, path, digest = self._write(self.record, canonical=False)
        try:
            with mock.patch.object(VERIFY, "EXPECTED_MANIFEST_SHA256", digest):
                with self.assertRaisesRegex(VERIFY.SourceLiftRecordError, "not canonical"):
                    VERIFY.verify_repository(REPO_ROOT, path, digest)
        finally:
            temporary.cleanup()

    def test_duplicate_key_rejects(self) -> None:
        raw = b'{"schema_version":"x","schema_version":"y"}\n'
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            path = Path(directory) / "record.json"
            path.write_bytes(raw)
            digest = hashlib.sha256(raw).hexdigest()
            with mock.patch.object(VERIFY, "EXPECTED_MANIFEST_SHA256", digest):
                with self.assertRaisesRegex(VERIFY.SourceLiftRecordError, "duplicate key"):
                    VERIFY.verify_repository(REPO_ROOT, path, digest)

    def test_authorization_mutations_reject(self) -> None:
        for field in (
            "build_authorized", "build_capture_complete", "capture_complete",
            "execution_authorized", "promotion_eligible", "registered_scientific_run",
            "scientific_evidence", "scientific_execution_authorized",
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(self.record)
                changed["classification"][field] = True
                self._semantic_reject(changed, "classification")

    def test_endpoint_and_route_mutations_reject(self) -> None:
        mutations = [
            lambda value: value["endpoint_bindings"]["reference_source"].__setitem__("tree", "0" * 40),
            lambda value: value["endpoint_bindings"]["reference_source"].__setitem__("parent", "0" * 40),
            lambda value: value["commutative_proof"].__setitem__("routes_equal", False),
            lambda value: value["commutative_proof"]["outside_allowlist"].__setitem__("sha256", "0" * 64),
            lambda value: value["commutative_proof"]["post_port_changed_path_surface"].__setitem__("count", 117),
            lambda value: value["commutative_proof"]["post_port_changed_path_surface"].__setitem__("sha256", "0" * 64),
            lambda value: value["commutative_proof"]["post_port_changed_path_surface"].__setitem__("optimized_and_reference_equal", False),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                changed = copy.deepcopy(self.record)
                mutate(changed)
                self._semantic_reject(changed)

    def test_prephysics_corner_mutations_reject(self) -> None:
        changed = copy.deepcopy(self.record)
        changed["allowed_method_delta"]["path_records"][4]["reference_prephysics"]["oid"] = "0" * 40
        self._semantic_reject(changed)
        changed = copy.deepcopy(self.record)
        changed["allowed_method_delta"]["path_records"][0]["reference_prephysics"] = changed["allowed_method_delta"]["path_records"][0]["optimized_prephysics"]
        self._semantic_reject(changed)

    def test_partition_and_patch_mutations_reject(self) -> None:
        changed = copy.deepcopy(self.record)
        changed["allowed_method_delta"]["automatic_paths"].append("src/Simulator.cpp")
        self._semantic_reject(changed, "path|partition")
        changed = copy.deepcopy(self.record)
        changed["allowed_method_delta"]["path_records"][2]["final_patch"]["bytes"] += 1
        self._semantic_reject(changed)
        changed = copy.deepcopy(self.record)
        changed["allowed_method_delta"]["path_records"][7]["application"] = "automatic-exact-context"
        self._semantic_reject(changed, "application")

    def test_resolution_bundle_and_reviewer_mutations_reject(self) -> None:
        changed = copy.deepcopy(self.record)
        changed["allowed_method_delta"]["resolution_bundle"]["sha256"] = "0" * 64
        self._semantic_reject(changed, "bundle")
        changed = copy.deepcopy(self.record)
        changed["allowed_method_delta"]["path_records"][2]["resolution"]["reviewers"][1]["signed"] = True
        self._semantic_reject(changed, "reviewer|resolution")
        changed = copy.deepcopy(self.record)
        changed["allowed_method_delta"]["path_records"][9]["resolution"]["rationale"] = "weakened"
        self._semantic_reject(changed, "resolution")

    def test_naive_reference_map_header_rejects(self) -> None:
        self._naive_candidate_rejects("inc/Simulator.h")

    def test_naive_optimized_neighbour_flow_rejects(self) -> None:
        self._naive_candidate_rejects("src/Simulator.cpp")

    def test_common_projection_mutations_reject(self) -> None:
        changed = copy.deepcopy(self.record)
        changed["common_source_projection"]["entry_count"] = 370
        self._semantic_reject(changed, "common-source")
        changed = copy.deepcopy(self.record)
        changed["common_source_projection"]["project_paths"][0] = "inc/not-common.h"
        self._semantic_reject(changed, "common-source")
        changed = copy.deepcopy(self.record)
        changed["common_source_projection"]["projected_git_tree"] = "0" * 40
        self._semantic_reject(changed, "common-source")

    def test_method_and_interface_inventory_mutations_reject(self) -> None:
        changed = copy.deepcopy(self.record)
        sources = changed["method_inventory"]["controller_aggregate_sources"]
        sources[0], sources[1] = sources[1], sources[0]
        self._semantic_reject(changed, "method")
        changed = copy.deepcopy(self.record)
        changed["interface_equality"]["endpoint_equality"] = False
        self._semantic_reject(changed, "interface")

    def test_disclosure_and_blocker_mutations_reject(self) -> None:
        changed = copy.deepcopy(self.record)
        changed["review_disclosure"]["protected_external_source_roots_materialized"] = True
        self._semantic_reject(changed, "review|trust")
        changed = copy.deepcopy(self.record)
        changed["current_blockers"].pop()
        self._semantic_reject(changed, "blocker")

    def test_predecessor_binding_mutations_reject(self) -> None:
        changed = copy.deepcopy(self.record)
        changed["upstream_bindings"]["feasibility_manifest"]["sha256"] = "0" * 64
        self._semantic_reject(changed, "predecessor")
        changed = copy.deepcopy(self.record)
        changed["upstream_bindings"]["superbuild_graph"]["bytes"] += 1
        self._semantic_reject(changed, "predecessor")
        changed = copy.deepcopy(self.record)
        changed["upstream_bindings"]["feasibility_verifier"]["sha256"] = "0" * 64
        self._semantic_reject(changed, "predecessor")
        changed = copy.deepcopy(self.record)
        changed["upstream_bindings"]["feasibility_verifier"]["bytes"] += 1
        self._semantic_reject(changed, "predecessor")

    def test_git_reader_rejects_mutating_or_unplanned_commands(self) -> None:
        reader = VERIFY.RecordGitReader(REPO_ROOT)
        for command in (
            ["read-tree", VERIFY.O_FINAL],
            ["checkout", VERIFY.R_FINAL],
            ["hash-object", "-w", "--stdin"],
            ["worktree", "add", "/private/tmp/forbidden", VERIFY.R_FINAL],
        ):
            with self.subTest(command=command):
                with self.assertRaisesRegex(VERIFY.SourceLiftRecordError, "outside the exact read-only templates"):
                    reader.run(command)


if __name__ == "__main__":
    unittest.main()
