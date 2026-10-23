#!/usr/bin/env python3
"""Static and adversarial tests for the ISO qualification contract."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path


REPOSITORY = Path(__file__).resolve().parents[3]
VALIDATOR = REPOSITORY / "scripts/comparison/validate_physics_iso_source_capsule_qualification_contract_v1.py"
CONTRACT = REPOSITORY / "docs/simulator/physics_iso_source_capsule_qualification_contract_v1.json"
EXPECTED_VALIDATOR_CONTRACT_SHA = (
    "62e00380bda281eaeaf4bacf717494964306ce4a840138b62e0707caf476e5e4"
)


def load_validator() -> types.ModuleType:
    raw = VALIDATOR.read_bytes()
    module = types.ModuleType("iso_qualification_contract_validator_test_module")
    module.__file__ = str(VALIDATOR)
    exec(compile(raw, str(VALIDATOR), "exec"), module.__dict__)
    return module


V = load_validator()


def canonical_file(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8") + b"\n"


class IsoQualificationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = CONTRACT.read_bytes()
        cls.contract = V.parse_canonical_json(cls.raw)

    def assert_rejected(self, value: object) -> None:
        with self.assertRaises(V.ValidationError):
            V.validate_contract_value(value)

    def test_01_live_contract_and_cli_are_nonauthorizing(self) -> None:
        result = V.verify_contract()
        self.assertEqual(result["contract_sha256"], EXPECTED_VALIDATOR_CONTRACT_SHA)
        self.assertFalse(result["empirical_qualification_passed"])
        self.assertFalse(result["production_backend_eligible"])

        outputs = []
        env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "PYTHONDONTWRITEBYTECODE": "1"}
        for executable in (sys.executable, "/usr/bin/python3"):
            run = subprocess.run(
                [executable, "-B", str(VALIDATOR)],
                cwd=REPOSITORY,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=20,
                check=False,
            )
            self.assertEqual(run.returncode, 0, run.stderr.decode(errors="replace"))
            self.assertEqual(run.stderr, b"")
            verdict = json.loads(run.stdout)
            self.assertTrue(verdict["valid"])
            self.assertFalse(verdict["empirical_qualification_passed"])
            self.assertFalse(verdict["production_backend_eligible"])
            self.assertTrue(all(value is False for value in verdict["classification"].values()))
            outputs.append(run.stdout)
        self.assertEqual(outputs[0], outputs[1])

        extra = subprocess.run(
            [sys.executable, "-B", str(VALIDATOR), "unexpected"],
            cwd=REPOSITORY,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
            check=False,
        )
        self.assertEqual(extra.returncode, 2)
        self.assertEqual(extra.stdout, b"")

    def test_02_canonical_json_and_caps_fail_closed(self) -> None:
        self.assertEqual(V.parse_canonical_json(canonical_file({"a": ""})), {"a": ""})
        for raw in (
            b'{"a":1, "b":2}\n',
            b'{"a":1,"a":2}\n',
            b'{"a":1.0}\n',
            b'{"a":NaN}\n',
            b'{"a":1}',
            b'{"a":1}\n\n',
        ):
            with self.assertRaises(V.ValidationError):
                V.parse_canonical_json(raw)
        with self.assertRaises(V.ValidationError):
            V.canonical_json_bytes({"k": "x" * (V.STRING_BYTES_MAX + 1)})
        with self.assertRaises(V.ValidationError):
            V.canonical_json_bytes({"x" * (V.STRING_BYTES_MAX + 1): 1})
        with self.assertRaises(V.ValidationError):
            V.canonical_json_bytes({}, V.CONTRACT_BYTES_MAX + 1)

    def test_03_classification_scope_and_receipt_mutations_reject(self) -> None:
        mutations = []
        for path, value in (
            (("classification", "production_backend_eligible"), True),
            (("classification", "schema_surface_frozen"), False),
            (("scope", "image_or_mount_created"), True),
            (("scope", "v2_root_manifest_compatible"), True),
            (("current_unqualified_receipt", "empirical_qualification_passed"), True),
            (("current_unqualified_receipt", "production_backend_eligible"), True),
            (("current_unqualified_receipt", "image"), {}),
            (("current_unqualified_receipt", "implementation_bindings"), [{}]),
        ):
            changed = copy.deepcopy(self.contract)
            changed[path[0]][path[1]] = value
            mutations.append(changed)
        for changed in mutations:
            self.assert_rejected(changed)

    def test_04_blocker_partitions_and_nested_schema_are_exact(self) -> None:
        changed = copy.deepcopy(self.contract)
        changed["current_blockers"]["empirical_qualification_blockers"].reverse()
        self.assert_rejected(changed)
        changed = copy.deepcopy(self.contract)
        changed["current_blockers"]["permanent_production_blockers"].pop()
        self.assert_rejected(changed)
        changed = copy.deepcopy(self.contract)
        changed["schemas"]["attachment_evidence"]["exact_keys"].append("production_mount_id")
        self.assert_rejected(changed)
        changed = copy.deepcopy(self.contract)
        changed["schemas"]["qualification_receipt"]["field_types"]["production_backend_eligible"] = "boolean"
        self.assert_rejected(changed)
        changed = copy.deepcopy(self.contract)
        changed["schemas"]["device_association_ledger"]["exact_keys"].append("production_mount_id")
        self.assert_rejected(changed)
        changed = copy.deepcopy(self.contract)
        changed["receipt_contract"].pop("failure_semantics")
        self.assert_rejected(changed)
        changed = copy.deepcopy(self.contract)
        changed["receipt_contract"]["failure_transport"]["failure_stdout_bytes"] = 1
        self.assert_rejected(changed)
        changed = copy.deepcopy(self.contract)
        changed["schemas"]["cleanup_evidence"]["exact_keys"].remove("final_mount_ledger")
        self.assert_rejected(changed)
        changed = copy.deepcopy(self.contract)
        changed["schemas"]["implementation_binding"]["field_types"][
            "independently_expected_by_verifier"
        ] = "boolean"
        self.assert_rejected(changed)

    def test_05_command_and_tool_surface_mutations_reject(self) -> None:
        mutations = []
        for command, token in (
            ("makehybrid", "-joliet"),
            ("makehybrid", "-ov"),
            ("attach", "-readwrite"),
            ("attach", "-shadow"),
            ("detach", "-force"),
        ):
            changed = copy.deepcopy(self.contract)
            changed["qualification_protocol"]["command_templates"][command].insert(-1, token)
            mutations.append(changed)
        changed = copy.deepcopy(self.contract)
        changed["tool_profile"]["tools"][0]["sha256"] = "0" * 64
        mutations.append(changed)
        changed = copy.deepcopy(self.contract)
        changed["tool_profile"]["dynamic_runtime_closure_authenticated"] = True
        mutations.append(changed)
        changed = copy.deepcopy(self.contract)
        changed["qualification_protocol"]["command_templates"]["hdiutil_info"].append("-verbose")
        mutations.append(changed)
        changed = copy.deepcopy(self.contract)
        changed["qualification_protocol"]["mutation_probe_registry"].reverse()
        mutations.append(changed)
        for item in mutations:
            self.assert_rejected(item)

    def test_06_iso_root_and_rock_ridge_boundaries_are_exact(self) -> None:
        mutations = []
        for key, value in (
            ("joliet_enabled", True),
            ("udf_enabled", True),
            ("rock_ridge_required", False),
            ("indexed_component_bytes_max", 35),
            ("long_component_occurrences_exact", 12),
            ("mounted_empty_or_enotsup_xattrs_are_production_absence_proof", True),
        ):
            changed = copy.deepcopy(self.contract)
            changed["image_profile"][key] = value
            mutations.append(changed)
        changed = copy.deepcopy(self.contract)
        changed["image_profile"]["roots"][0]["regular_file_count"] -= 1
        mutations.append(changed)
        changed = copy.deepcopy(self.contract)
        changed["image_profile"]["root_order"].reverse()
        mutations.append(changed)
        for item in mutations:
            self.assert_rejected(item)

    def test_07_hash_domains_formulas_and_vectors_are_exact(self) -> None:
        host = self.contract["host_profile"]
        body = dict(host)
        expected_host = body.pop("profile_sha256")
        self.assertEqual(
            hashlib.sha256(V.HOST_PROFILE_DOMAIN + V.canonical_json_bytes(body)).hexdigest(),
            expected_host,
        )
        tools = self.contract["tool_profile"]
        body = dict(tools)
        expected_tools = body.pop("profile_sha256")
        self.assertEqual(
            hashlib.sha256(V.TOOL_PROFILE_DOMAIN + V.canonical_json_bytes(body)).hexdigest(),
            expected_tools,
        )
        receipt = self.contract["current_unqualified_receipt"]
        body = dict(receipt)
        expected_receipt = body.pop("receipt_sha256")
        self.assertEqual(
            hashlib.sha256(V.RECEIPT_DOMAIN + V.canonical_json_bytes(body)).hexdigest(),
            expected_receipt,
        )
        V._validate_hash_test_vectors(self.contract)
        for vector_name in (
            "device_association_ledger",
            "diagnostic_mount_identity",
            "mount_ledger",
            "mounted_root_tree",
            "mounted_tree",
            "staged_tree",
        ):
            changed = copy.deepcopy(self.contract)
            changed["hash_test_vectors"][vector_name]["sha256"] = "0" * 64
            with self.assertRaises(V.ValidationError):
                V._validate_hash_test_vectors(changed)
        changed = copy.deepcopy(self.contract)
        changed["hash_domains"]["diagnostic_mount_identity"] = changed["hash_domains"]["image"]
        self.assert_rejected(changed)
        changed = copy.deepcopy(self.contract)
        changed["hash_formulas"]["image_id"] = "sha256(raw_image_bytes)"
        self.assert_rejected(changed)
        changed = copy.deepcopy(self.contract)
        changed["canonical_json"]["hash_domain_text_decoding"] = "hash two literal bytes"
        self.assert_rejected(changed)
        changed = copy.deepcopy(self.contract)
        changed["canonical_json"]["raw32"] = "hash lowercase text"
        self.assert_rejected(changed)
        changed = copy.deepcopy(self.contract)
        changed["hash_formulas"].pop("device_association_ledger_sha256")
        self.assert_rejected(changed)
        changed = copy.deepcopy(self.contract)
        changed["hard_caps"]["mount_ledger_entries_max"] += 1
        self.assert_rejected(changed)

    def test_08_exact_upstream_bindings_and_file_reader_reject_aliases(self) -> None:
        result = V.validate_contract_value(self.contract, REPOSITORY)
        self.assertFalse(result["production_backend_eligible"])
        changed = copy.deepcopy(self.contract)
        changed["exact_bindings"]["source_implementations"][1]["sha256"] = "f" * 64
        self.assert_rejected(changed)

        with tempfile.TemporaryDirectory(prefix="physics-iso-contract-reader-") as tmp:
            root = Path(tmp)
            target = root / "target"
            target.write_bytes(b"x")
            link = root / "link"
            link.symlink_to(target)
            with self.assertRaises(V.ValidationError):
                V.read_authenticated_file(link, 16)
            hard = root / "hard"
            os.link(target, hard)
            with self.assertRaises(V.ValidationError):
                V.read_authenticated_file(target, 16)

    def test_09_validator_has_no_execution_or_mount_surface(self) -> None:
        source = VALIDATOR.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
                    calls.append((node.func.value.id, node.func.attr))
                elif isinstance(node.func, ast.Name):
                    calls.append(("", node.func.id))
        self.assertTrue({"subprocess", "ctypes", "socket"}.isdisjoint(imported))
        forbidden = {
            ("os", "system"),
            ("os", "popen"),
            ("os", "spawnv"),
            ("os", "execv"),
            ("", "Popen"),
        }
        self.assertTrue(forbidden.isdisjoint(calls))
        self.assertNotIn("renameatx_np(", source)
        self.assertNotIn("hdiutil attach", source)
        self.assertNotIn("hdiutil makehybrid", source)

    def test_10_no_images_mounts_official_outputs_or_bytecode(self) -> None:
        self.assertFalse((REPOSITORY / "source.iso").exists())
        self.assertFalse((REPOSITORY / "image.work.iso").exists())
        official = REPOSITORY / "comparison/results/simulator-demo-v1-physics-stress-test/epoch-1"
        self.assertFalse(official.exists())
        cache = REPOSITORY / "scripts/comparison/__pycache__"
        if cache.exists():
            self.assertFalse(
                list(cache.glob("validate_physics_iso_source_capsule_qualification_contract_v1*.pyc"))
            )
        test_cache = Path(__file__).parent / "__pycache__"
        if test_cache.exists():
            self.assertFalse(
                list(test_cache.glob("test_validate_physics_iso_source_capsule_qualification_contract_v1*.pyc"))
            )


if __name__ == "__main__":
    unittest.main()
