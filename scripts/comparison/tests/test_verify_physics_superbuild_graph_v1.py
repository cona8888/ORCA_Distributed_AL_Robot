#!/usr/bin/env python3
"""Analytic, temporary-only tests for the prospective superbuild graph v1."""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "scripts/comparison/verify_physics_superbuild_graph_v1.py"
GRAPH_PATH = REPO_ROOT / "comparison/physics-superbuild-v1/source_graph_v1.json"
SPEC = importlib.util.spec_from_file_location("verify_physics_superbuild_graph_v1", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
VERIFY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VERIFY)


class SuperbuildGraphV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = GRAPH_PATH.read_bytes()
        cls.graph = json.loads(cls.raw)

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def write_graph(self, graph: object, name: str = "graph.json") -> Path:
        path = self.root / name
        path.write_bytes(VERIFY.canonical_bytes(graph))
        return path

    def reject_mutation(self, mutator) -> None:
        graph = copy.deepcopy(self.graph)
        mutator(graph)
        with self.assertRaises(VERIFY.VerificationError):
            VERIFY.verify_graph(self.write_graph(graph))

    def reject_semantic_mutation(self, mutator) -> None:
        graph = copy.deepcopy(self.graph)
        mutator(graph)
        with self.assertRaises(VERIFY.VerificationError):
            VERIFY._validate_semantics(graph)

    def test_registered_graph_passes_as_structural_design_only(self) -> None:
        verdict = VERIFY.verify_graph(GRAPH_PATH)
        self.assertEqual(verdict["status"], "pass-structural-design-only")
        self.assertFalse(verdict["build_authorized"])
        self.assertFalse(verdict["actual_build_captured"])
        self.assertFalse(verdict["execution_authorized"])
        self.assertFalse(verdict["promotion_eligible"])
        self.assertFalse(verdict["scientific_evidence"])

    def test_frozen_identity_matches_exact_bytes(self) -> None:
        self.assertEqual(len(self.raw), VERIFY.EXPECTED_GRAPH_BYTES)
        self.assertEqual(
            VERIFY.hashlib.sha256(self.raw).hexdigest(),
            VERIFY.EXPECTED_GRAPH_SHA256,
        )
        self.assertEqual(self.raw, VERIFY.canonical_bytes(self.graph))

    def test_classification_unlock_rejects(self) -> None:
        self.reject_semantic_mutation(lambda value: value["classification"].__setitem__(
            "build_authorized", True
        ))

    def test_missing_or_extra_top_level_key_rejects(self) -> None:
        self.reject_mutation(lambda value: value.pop("parity_contract"))
        self.reject_mutation(lambda value: value.__setitem__("unreviewed", {}))

    def test_historical_boundary_mutations_reject(self) -> None:
        self.reject_semantic_mutation(lambda value: value["historical_method_boundary"][
            "paths"
        ].append("src/Robot.cpp"))
        self.reject_semantic_mutation(lambda value: value["historical_method_boundary"].__setitem__(
            "path_count", 13
        ))

    def test_common_role_source_and_order_mutations_reject(self) -> None:
        self.reject_semantic_mutation(lambda value: value["literal_common_objects"]["roles"][0].__setitem__(
            "source", "src/Simulator.cpp"
        ))
        self.reject_semantic_mutation(lambda value: value["literal_common_objects"]["link_order"].reverse())
        self.reject_semantic_mutation(lambda value: value["literal_common_objects"].__setitem__(
            "compile_once", False
        ))

    def test_controller_core_dispatch_and_aggregate_mutations_reject(self) -> None:
        self.reject_semantic_mutation(lambda value: value["method_controller_aggregate"][
            "controller_core_sources"
        ].pop())
        self.reject_semantic_mutation(lambda value: value["method_controller_aggregate"][
            "dispatch_sources"
        ].reverse())
        self.reject_semantic_mutation(lambda value: value["method_controller_aggregate"][
            "aggregate_operation"
        ].__setitem__("operation", "archive"))
        self.reject_semantic_mutation(lambda value: value["method_controller_aggregate"][
            "dispatch_policy"
        ].__setitem__("timing_stub_objects", True))
        self.assertEqual(
            self.graph["method_controller_aggregate"]["controller_core_sources"][-2:],
            ["src/gbp/Variable.cpp", "src/gbp/Factor.cpp"],
        )

    def test_observer_partition_mutations_reject(self) -> None:
        self.reject_mutation(lambda value: value["observer_partition"][
            "fidelity_sources_per_method"
        ].pop())
        self.reject_mutation(lambda value: value["observer_partition"].__setitem__(
            "timing_observer_count", 1
        ))
        self.reject_mutation(lambda value: value["observer_partition"][
            "timing_observer_sources"
        ].append("src/PhysicsEvidenceHooksNoop.cpp"))

    def test_product_order_name_mode_and_equation_mutations_reject(self) -> None:
        self.reject_semantic_mutation(lambda value: value["products"]["order"].reverse())
        self.reject_semantic_mutation(lambda value: value["products"]["records"][1].__setitem__(
            "observer_mode", "present"
        ))
        self.reject_semantic_mutation(lambda value: value["products"]["records"][1][
            "object_equation"
        ].append("observer:optimized:comparison-recorder"))
        self.reject_semantic_mutation(lambda value: value["products"]["records"][0].__setitem__(
            "output_name", "dancers"
        ))
        self.reject_semantic_mutation(lambda value: value["products"]["order_resolution"].__setitem__(
            "v1_execution_authorization_permitted", True
        ))

    def test_port_version_and_lifecycle_ownership_mutations_reject(self) -> None:
        self.reject_semantic_mutation(lambda value: value["simulator_controller_port"]["abi"].__setitem__(
            "version", 2
        ))
        self.reject_semantic_mutation(lambda value: value["simulator_controller_port"]["abi"][
            "table_fields"
        ].pop())
        self.reject_semantic_mutation(lambda value: value["simulator_controller_port"][
            "definitions_moved_to_common_bridge"
        ].pop())
        self.reject_semantic_mutation(lambda value: value["simulator_controller_port"].__setitem__(
            "method_implementation_location", "src/SimulatorPhysicsBridge.cpp"
        ))

    def test_source_lift_binding_root_and_parity_mutations_reject_semantically(self) -> None:
        self.assertEqual(
            self.graph["source_lift_feasibility_binding"],
            VERIFY.EXPECTED_SOURCE_LIFT_BINDING,
        )
        self.assertEqual(
            self.graph["source_lift_feasibility_binding"]["bytes"],
            12_123,
        )
        self.assertEqual(
            self.graph["source_lift_feasibility_binding"]["sha256"],
            "52ab7a20d01978107b1122ce70c8fe2a3719f34719e5c61cff5a960581d46bee",
        )
        for field, replacement in (
            ("path", "docs/simulator/unbound.json"),
            ("schema_version", "unreviewed-v1"),
            ("bytes", 12_124),
            ("sha256", "0" * 64),
            ("final_source_endpoints_frozen", True),
        ):
            self.reject_semantic_mutation(
                lambda value, key=field, item=replacement: value[
                    "source_lift_feasibility_binding"
                ].__setitem__(key, item)
            )
        self.reject_semantic_mutation(lambda value: value["source_roots"][
            "common_source"
        ].__setitem__("root_id", "optimized-source"))
        self.reject_semantic_mutation(lambda value: value["source_roots"].__setitem__(
            "configure_count", 2
        ))
        self.reject_semantic_mutation(lambda value: value["parity_contract"]["compile"][
            "method_action_difference_allowlist"
        ].append("compiler-flags"))
        self.reject_semantic_mutation(lambda value: value["parity_contract"]["link"].__setitem__(
            "ordered_library_identity_sequence_equal_for_all_products", False
        ))

    def test_blocker_clear_remove_or_reorder_rejects(self) -> None:
        self.reject_mutation(lambda value: value["blockers"][0].__setitem__("cleared", True))
        self.reject_mutation(lambda value: value["blockers"].pop())
        self.reject_mutation(lambda value: value["blockers"].reverse())

    def test_official_namespace_fragment_rejects(self) -> None:
        for root in VERIFY.PROTECTED_REPOSITORY_ROOTS:
            for candidate in (root, root + "/descendant.json"):
                self.reject_semantic_mutation(lambda value, path=candidate: value[
                    "source_roots"
                ].__setitem__("standalone_superbuild_path", path))

    def test_noncanonical_pretty_json_rejects(self) -> None:
        path = self.root / "pretty.json"
        path.write_text(json.dumps(self.graph, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(VERIFY.VerificationError, "canonical"):
            VERIFY.verify_graph(path)

    def test_duplicate_key_and_nonfinite_constant_reject(self) -> None:
        duplicate = self.root / "duplicate.json"
        duplicate.write_bytes(b'{"a":1,"a":2}\n')
        with self.assertRaisesRegex(VERIFY.VerificationError, "duplicate"):
            VERIFY.verify_graph(duplicate)
        nonfinite = self.root / "nonfinite.json"
        nonfinite.write_bytes(b'{"value":NaN}\n')
        with self.assertRaisesRegex(VERIFY.VerificationError, "non-finite"):
            VERIFY.verify_graph(nonfinite)

    def test_symlink_and_hardlink_inputs_reject(self) -> None:
        target = self.root / "target.json"
        target.write_bytes(self.raw)
        symlink = self.root / "symlink.json"
        symlink.symlink_to(target)
        with self.assertRaisesRegex(VERIFY.VerificationError, "symlink"):
            VERIFY.verify_graph(symlink)
        hardlink = self.root / "hardlink.json"
        os.link(target, hardlink)
        with self.assertRaisesRegex(VERIFY.VerificationError, "hard link"):
            VERIFY.verify_graph(target)

    def test_relative_symlink_ancestor_and_writable_mode_reject(self) -> None:
        relative = Path("comparison/physics-superbuild-v1/source_graph_v1.json")
        with self.assertRaisesRegex(VERIFY.VerificationError, "absolute"):
            VERIFY.verify_graph(relative)
        real = self.root / "real"
        real.mkdir()
        nested = real / "graph.json"
        nested.write_bytes(self.raw)
        alias = self.root / "alias"
        alias.symlink_to(real, target_is_directory=True)
        with self.assertRaisesRegex(VERIFY.VerificationError, "symlink"):
            VERIFY.verify_graph(alias / "graph.json")
        writable = self.root / "writable.json"
        writable.write_bytes(self.raw)
        writable.chmod(0o666)
        with self.assertRaisesRegex(VERIFY.VerificationError, "writable"):
            VERIFY.verify_graph(writable)

    def test_empty_and_oversize_inputs_reject(self) -> None:
        empty = self.root / "empty.json"
        empty.write_bytes(b"")
        with self.assertRaisesRegex(VERIFY.VerificationError, "byte count"):
            VERIFY.verify_graph(empty)
        large = self.root / "large.json"
        large.write_bytes(b" " * (VERIFY.MAX_GRAPH_BYTES + 1))
        with self.assertRaisesRegex(VERIFY.VerificationError, "byte count"):
            VERIFY.verify_graph(large)

    def test_verifier_has_no_process_network_or_write_capability(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        self.assertTrue(imported.isdisjoint({
            "http", "requests", "shutil", "socket", "subprocess", "urllib"
        }))
        for forbidden in ("Popen(", "system(", "write_bytes(", "write_text(", "unlink("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
