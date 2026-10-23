#!/usr/bin/env python3
"""Temp-only regressions for the nonauthorizing superbuild-integration record."""

from __future__ import annotations

import builtins
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
VERIFY_PATH = ROOT / "scripts/comparison/verify_physics_superbuild_integration_record_v1.py"
RECORD_PATH = ROOT / "docs/simulator/physics_superbuild_integration_record_epoch_1_v1.json"

SPEC = importlib.util.spec_from_file_location("physics_superbuild_integration_record_v1", VERIFY_PATH)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VERIFY
SPEC.loader.exec_module(VERIFY)


class SuperbuildIntegrationRecordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = RECORD_PATH.read_bytes()
        cls.record = json.loads(cls.raw)
        source_path = ROOT / VERIFY.SOURCE_VERIFIER["path"]
        source_raw = source_path.read_bytes()
        cls.source = VERIFY._module_from_identity(
            source_raw, VERIFY.SOURCE_VERIFIER, source_path,
            "dancers_test_authenticated_source_lift_record_v1",
        )
        Reader = VERIFY._integration_git_reader(cls.source)
        cls.reader = Reader(ROOT)
        cls.base_tree = cls.source.feasibility._tree(cls.reader, VERIFY.BASE)
        cls.optimized_tree = cls.source.feasibility._tree(cls.reader, VERIFY.OPTIMIZED)
        cls.reference_tree = cls.source.feasibility._tree(cls.reader, VERIFY.REFERENCE)
        cls.offline_manifest = json.loads(
            (ROOT / cls.record["bindings"]["offline_inputs_v2"]["manifest"]["path"]).read_bytes()
        )

    def test_01_frozen_identity_and_live_cli_verdict(self) -> None:
        self.assertEqual(len(self.raw), VERIFY.EXPECTED_RECORD_BYTES)
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), VERIFY.EXPECTED_RECORD_SHA256)
        self.assertEqual(self.raw, VERIFY.canonical_json_bytes(self.record))
        completed = subprocess.run(
            [sys.executable, "-W", "error", os.fspath(VERIFY_PATH)],
            cwd=ROOT, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", "replace"))
        self.assertEqual(completed.stderr, b"")
        verdict = json.loads(completed.stdout)
        self.assertEqual(verdict["status"], "pass-structural-integration-only")
        self.assertTrue(verdict["structural_integration_verified"])
        for key in (
            "build_authorized", "build_capture_complete", "build_executed",
            "capture_complete", "execution_authorized", "four_products_linked",
            "literal_object_reuse_attested", "promotion_eligible",
            "protected_external_source_roots_materialized", "provenance_complete",
            "scientific_evidence", "scientific_execution_authorized",
        ):
            self.assertIs(verdict[key], False, key)

    def test_02_authenticated_modules_reject_before_execution(self) -> None:
        sentinel = "_dancers_integration_untrusted_module_executed"
        if hasattr(builtins, sentinel):
            delattr(builtins, sentinel)
        code = f"import builtins\nbuiltins.{sentinel} = True\n".encode("utf-8")
        try:
            for label, identity in (
                ("source", VERIFY.SOURCE_VERIFIER),
                ("offline", VERIFY.OFFLINE_VERIFIER),
            ):
                with self.subTest(label=label):
                    candidate = code + b"#" * (identity["bytes"] - len(code))
                    self.assertEqual(len(candidate), identity["bytes"])
                    with self.assertRaisesRegex(VERIFY.IntegrationRecordError, "module bytes differ"):
                        VERIFY._module_from_identity(candidate, identity, Path(f"/{label}.py"), f"bad_{label}")
                    self.assertFalse(hasattr(builtins, sentinel))
        finally:
            if hasattr(builtins, sentinel):
                delattr(builtins, sentinel)

    def test_03_strict_record_and_file_reader(self) -> None:
        with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
            root = Path(temporary)
            cases = {
                "pretty": json.dumps(self.record, indent=2).encode("utf-8"),
                "duplicate": b'{"schema_version":"x","schema_version":"y"}\n',
                "nan": b'{"value":NaN}\n',
            }
            for label, raw in cases.items():
                path = root / f"{label}.json"
                path.write_bytes(raw)
                with self.subTest(label=label), self.assertRaises(VERIFY.IntegrationRecordError):
                    VERIFY._read_record(path)
            regular = root / "regular.json"
            regular.write_bytes(self.raw)
            symlink = root / "symlink.json"
            symlink.symlink_to(regular)
            with self.assertRaisesRegex(VERIFY.IntegrationRecordError, "symlink"):
                VERIFY._read_record(symlink)
            hardlink = root / "hardlink.json"
            os.link(regular, hardlink)
            with self.assertRaisesRegex(VERIFY.IntegrationRecordError, "links"):
                VERIFY._read_record(regular)

    def test_04_nonauthorization_and_root_hash_locks(self) -> None:
        mutations = [
            lambda value: value["classification"].__setitem__("build_authorized", True),
            lambda value: value["classification"].__setitem__("four_products_linked", True),
            lambda value: value["classification"].__setitem__("scientific_evidence", True),
            lambda value: value["scope"].__setitem__("configure_compile_link_or_simulator_invoked", True),
            lambda value: value["scope"].__setitem__("new_cmake_registration_added", True),
            lambda value: value["scope"].__setitem__("production_source_roots_present", True),
            lambda value: value["root_tree_hash_profile"].__setitem__("status", "frozen"),
            lambda value: value["root_tree_hash_profile"].__setitem__("v2_expected_pre_tree", {}),
            lambda value: value["root_tree_hash_profile"]["candidate_root_tree_sha256"].__setitem__("common-source", "0" * 64),
        ]
        for index, mutation in enumerate(mutations):
            changed = copy.deepcopy(self.record)
            mutation(changed)
            with self.subTest(index=index), self.assertRaises(VERIFY.IntegrationRecordError):
                VERIFY._shape(changed)

    def test_05_topology_and_endpoint_shape_locks(self) -> None:
        mutations = [
            lambda value: value["anchor"]["parents"].reverse(),
            lambda value: value["anchor"].__setitem__("tree", "0" * 40),
            lambda value: value["anchor"]["commit_object"].__setitem__("sha256", "0" * 64),
            lambda value: value["endpoint_bindings"]["optimized"].__setitem__("parent", "0" * 40),
            lambda value: value["endpoint_bindings"]["reference"].__setitem__("parent", "0" * 40),
            lambda value: value["endpoint_bindings"]["source_record_checkpoint"].__setitem__("tree", "0" * 40),
        ]
        for index, mutation in enumerate(mutations):
            changed = copy.deepcopy(self.record)
            mutation(changed)
            with self.subTest(index=index), self.assertRaises(VERIFY.IntegrationRecordError):
                VERIFY._shape(changed)

    def test_06_delta_and_projection_reconstruction(self) -> None:
        paths = VERIFY._verify_delta(
            self.source, self.reader, self.record, self.base_tree,
            self.optimized_tree, self.reference_tree,
        )
        self.assertEqual(len(paths), 27)
        for label, mutation in (
            ("path", lambda value: value["implementation_delta"]["path_list"].__setitem__("sha256", "0" * 64)),
            ("optimized", lambda value: value["implementation_delta"]["optimized_identity_manifest"].__setitem__("sha256", "0" * 64)),
        ):
            changed = copy.deepcopy(self.record)
            mutation(changed)
            with self.subTest(label=label), self.assertRaises(VERIFY.IntegrationRecordError):
                VERIFY._verify_delta(
                    self.source, self.reader, changed, self.base_tree,
                    self.optimized_tree, self.reference_tree,
                )
        common, method = VERIFY._verify_projections(
            self.source, self.reader, self.record, self.optimized_tree, self.reference_tree,
        )
        self.assertEqual((len(common), len(method)), (371, 53))
        for root_name, field in (("common-source", "projected_git_tree"),):
            changed = copy.deepcopy(self.record)
            changed["projected_source_roots"][root_name][field] = "0" * 40 if field == "projected_git_tree" else ("0" * 64 if field.endswith("sha256") else 1)
            with self.subTest(root=root_name), self.assertRaises(VERIFY.IntegrationRecordError):
                VERIFY._verify_projections(
                    self.source, self.reader, changed, self.optimized_tree, self.reference_tree,
                )

    def test_07_graph_split_and_surface_locks(self) -> None:
        mutations = [
            lambda value: value["graph_blockers"]["active"].reverse(),
            lambda value: value["graph_blockers"]["cleared_at_committed_source_scope"].pop(),
            lambda value: value["graph_blockers"].__setitem__("predecessor_total", 7),
            lambda value: value["implementation_surface"]["product_order"].reverse(),
            lambda value: value["implementation_surface"].__setitem__("controller_aggregate_source_count", 15),
            lambda value: value["implementation_surface"].__setitem__("common_bridge_definition_occurrence_count", 7),
            lambda value: value["implementation_surface"].__setitem__("timing_observer_source_count", 1),
            lambda value: value["implementation_surface"]["literal_common_files"].append(value["implementation_surface"]["endpoint_equal_method_dispatch_files"].pop()),
        ]
        for index, mutation in enumerate(mutations):
            changed = copy.deepcopy(self.record)
            mutation(changed)
            with self.subTest(index=index), self.assertRaises(VERIFY.IntegrationRecordError):
                VERIFY._shape(changed)

    def test_08_dependency_and_cmake_cross_binding(self) -> None:
        standalone = (ROOT / self.record["bindings"]["standalone_cmake"]["path"]).read_bytes()
        VERIFY._verify_dependencies(self.record, self.offline_manifest, standalone)
        mutations = [
            lambda value: value["dependency_cross_bindings"]["archives"][0].__setitem__("sha256", "0" * 64),
            lambda value: value["dependency_cross_bindings"]["archives"][1].__setitem__("path", "thirdparty/dist/other.tar.gz"),
            lambda value: value["dependency_cross_bindings"]["eigen"].__setitem__("version", "3.4.1"),
            lambda value: value["dependency_cross_bindings"]["eigen"].__setitem__("selected_tree_sha256", "0" * 64),
            lambda value: value["dependency_cross_bindings"].__setitem__("cross_binding_verified", False),
            lambda value: value["dependency_cross_bindings"].__setitem__("standalone_cmake_authenticated_by_offline_inputs_v2", True),
        ]
        for index, mutation in enumerate(mutations):
            changed = copy.deepcopy(self.record)
            mutation(changed)
            with self.subTest(index=index), self.assertRaises(VERIFY.IntegrationRecordError):
                VERIFY._verify_dependencies(changed, self.offline_manifest, standalone)

    def test_09_bound_source_identity_and_registration_locks(self) -> None:
        VERIFY._verify_root_registrations(
            self.record, (ROOT / self.record["bindings"]["root_cmake"]["path"]).read_bytes(),
        )
        changed = copy.deepcopy(self.record)
        changed["bindings"]["source_lift"]["verifier"]["sha256"] = "0" * 64
        with self.assertRaises(VERIFY.IntegrationRecordError):
            VERIFY._shape(changed)
        changed = copy.deepcopy(self.record)
        changed["implementation_surface"]["simulator_sources"]["reference"]["oid"] = "not-a-git-oid"
        with self.assertRaises(VERIFY.IntegrationRecordError):
            VERIFY._shape(changed)
        root_cmake = (ROOT / self.record["bindings"]["root_cmake"]["path"]).read_bytes()
        with self.assertRaisesRegex(VERIFY.IntegrationRecordError, "unexpectedly CMake-registered"):
            VERIFY._verify_root_registrations(
                self.record,
                root_cmake + b"\n# test_verify_physics_superbuild_integration_record_v1.py\n",
            )

    def test_10_git_command_allowlist(self) -> None:
        Reader = type(self.reader)
        for command in (
            ("checkout", VERIFY.OPTIMIZED),
            ("hash-object", "-w", "file"),
            ("status", "--short"),
            ("show", VERIFY.ANCHOR),
        ):
            with self.subTest(command=command), self.assertRaises(Exception):
                Reader._command_cap(command, None)
        self.assertGreater(
            Reader._command_cap(("rev-parse", f"{VERIFY.ANCHOR}^{{tree}}"), None), 0,
        )


if __name__ == "__main__":
    unittest.main()
