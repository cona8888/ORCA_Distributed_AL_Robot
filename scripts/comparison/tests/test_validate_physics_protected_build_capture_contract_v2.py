#!/usr/bin/env python3
"""Temp-only regressions for the protected-build capture v2 contract validator."""

from __future__ import annotations

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


REPOSITORY = Path(__file__).resolve().parents[3]
VALIDATOR_PATH = REPOSITORY / "scripts/comparison/validate_physics_protected_build_capture_contract_v2.py"
CONTRACT_PATH = REPOSITORY / "docs/simulator/physics_protected_build_capture_contract_v2.json"
SPEC = importlib.util.spec_from_file_location("protected_capture_v2", VALIDATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class ContractV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = CONTRACT_PATH.read_bytes()
        cls.contract = json.loads(cls.raw)

    def test_01_live_contract_is_canonical_and_non_authorizing(self) -> None:
        verdict = validator.validate_contract_file(CONTRACT_PATH)
        self.assertEqual(len(self.raw), validator.CONTRACT_BYTES)
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), validator.CONTRACT_SHA256)
        self.assertTrue(verdict["valid"])
        self.assertFalse(verdict["classification"]["contract_structure_complete"])
        self.assertTrue(verdict["classification"]["schema_surface_frozen"])
        for key in (
            "build_capture_complete", "causal_capture_verified", "provenance_complete",
            "promotion_eligible", "execution_authorized", "scientific_evidence",
            "scientific_evidence_eligible",
        ):
            self.assertIs(verdict["classification"][key], False)
        self.assertEqual(verdict["capture_blockers"], validator.BLOCKERS)
        self.assertEqual(verdict["promotion_blockers"], validator.BLOCKERS)

    def test_02_cli_accepts_exact_contract_and_rejects_mutation(self) -> None:
        accepted = subprocess.run(
            [sys.executable, str(VALIDATOR_PATH), str(CONTRACT_PATH)],
            check=False, capture_output=True,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr.decode())
        verdict = json.loads(accepted.stdout)
        self.assertFalse(verdict["classification"]["promotion_eligible"])
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary).resolve() / "contract.json"
            changed = copy.deepcopy(self.contract)
            changed["classification"]["execution_authorized"] = True
            path.write_bytes(validator.canonical_json_bytes(changed))
            path.chmod(0o600)
            rejected = subprocess.run(
                [sys.executable, str(VALIDATOR_PATH), str(path)],
                check=False, capture_output=True,
            )
        self.assertEqual(rejected.returncode, 1)
        self.assertEqual(rejected.stdout, b"")

    def test_03_categorical_contract_mutations_fail_closed(self) -> None:
        mutations = {
            "extra-top-key": lambda item: item.__setitem__("extra", None),
            "wrong-schema": lambda item: item.__setitem__("schema_version", "v3"),
            "raised-cap": lambda item: item["hard_caps"].__setitem__("support_page_bytes_max", 1 << 30),
            "domain": lambda item: item["hash_domains"].__setitem__("capture_id", "wrong\\0"),
            "order": lambda item: item["identity_order"]["ordered_stages"].reverse(),
            "signature": lambda item: item["authorization_interface"].__setitem__("signature_algorithm", "self-signed"),
            "external-canary": lambda item: item["network_policy"].__setitem__("test_net_or_external_address_permitted", True),
            "official-results": lambda item: item["publication_policy"].__setitem__("official_result_namespace_permitted", True),
            "missing-schema-key": lambda item: item["schemas"]["capture_grant"]["exact_keys"].pop(),
            "missing-page-kind": lambda item: item["page_closure"]["required_support_kinds"].pop(),
            "missing-blocker": lambda item: item["current_blockers"].pop(),
            "numeric-bool-alias": lambda item: item["classification"].__setitem__("execution_authorized", 0),
            "schema-not-frozen": lambda item: item["classification"].__setitem__("schema_surface_frozen", False),
            "stale-source-design": lambda item: item["design_bindings"]["source_lift"].__setitem__("sha256", "0" * 64),
            "stale-graph-design": lambda item: item["design_bindings"]["superbuild_graph"].__setitem__("bytes", 14454),
            "missing-seal-argument": lambda item: item["authorization_interface"]["external_verifier_arguments"].remove("capture_seal_path"),
            "empty-pages-enabled": lambda item: item["page_closure"].__setitem__("empty_pages_permitted", True),
            "page-file-cap-does-not-close": lambda item: item["hard_caps"].__setitem__("support_pages_max", 4096),
            "common-source-not-required": lambda item: item["root_policy"]["required_root_ids"].remove("common-source"),
            "common-source-not-immutable": lambda item: item["root_policy"]["immutable_complete_roots"].remove("common-source"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                changed = copy.deepcopy(self.contract)
                mutate(changed)
                with self.assertRaises(validator.ContractError):
                    validator.validate_contract_value(changed)

    def test_04_strict_canonical_json_rejections(self) -> None:
        malformed = [
            b'{"a":1,"a":1}\n',
            b'{"a":NaN}\n',
            b'{"a":1.0}\n',
            b'{ "a":1}\n',
            b'{"a":1}',
            b'{"a":1}\n\n',
        ]
        for raw in malformed:
            with self.subTest(raw=raw):
                with self.assertRaises(validator.ContractError):
                    validator.parse_canonical_json(raw)
        nested: object = None
        for _ in range(validator.JSON_DEPTH_MAX + 1):
            nested = [nested]
        with self.assertRaises(validator.ContractError):
            validator.canonical_json_bytes(nested)
        with self.assertRaises(validator.ContractError):
            validator.canonical_json_bytes({"x": "x" * validator.CONTRACT_BYTES_MAX})
        with self.assertRaises(TypeError):
            validator.canonical_json_bytes({}, maximum_bytes=validator.CONTRACT_BYTES_MAX + 1)

    def test_05_capture_and_authority_domains_have_fixed_results(self) -> None:
        nonce = "00" * 32
        expected_capture = hashlib.sha256(
            b"dancers-planar-physics-build-capture-id-v2\0"
            + validator.EPOCH_ID.encode() + b"\0" + bytes(32)
        ).hexdigest()
        self.assertEqual(validator.derive_capture_id(validator.EPOCH_ID, nonce), expected_capture)
        expected_key = hashlib.sha256(
            b"dancers-planar-physics-build-capture-authority-key-id-v2\0" + bytes(32)
        ).hexdigest()
        self.assertEqual(validator.derive_authority_key_id(bytes(32)), expected_key)
        with self.assertRaises(validator.ContractError):
            validator.derive_capture_id("another-epoch", nonce)
        with self.assertRaises(validator.ContractError):
            validator.derive_capture_id(validator.EPOCH_ID, "AA" * 32)
        with self.assertRaises(validator.ContractError):
            validator.derive_authority_key_id(bytes(31))

    def test_06_action_and_artifact_domains_are_exact_and_distinct(self) -> None:
        action = {
            "kind": "compile", "method": "common", "target": "physics.o",
            "parent_action_id": None, "cwd": "/capture/source", "executable": {"artifact_id": "a" * 64},
            "argv": ["c++", "-c", "physics.cpp"], "environment_id": "b" * 64,
            "stdin": None, "network_session_id": "c" * 64,
        }
        artifact = {
            "schema_version": "dancers-planar-physics-build-artifact-identity-v2",
            "relative_path": "objects/physics.o", "file_type": "regular",
            "mode": 0o400, "bytes": 7, "sha256": "d" * 64,
        }
        action_bytes = json.dumps(action, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        artifact_bytes = json.dumps(artifact, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        self.assertEqual(
            validator.derive_action_id(action),
            hashlib.sha256(b"dancers-planar-physics-build-action-id-v2\0" + action_bytes).hexdigest(),
        )
        self.assertEqual(
            validator.derive_artifact_id(artifact),
            hashlib.sha256(b"dancers-planar-physics-build-artifact-id-v2\0" + artifact_bytes).hexdigest(),
        )
        self.assertNotEqual(validator.derive_action_id(action), validator.derive_artifact_id(artifact))

    def _closure(self):
        capture_id = validator.derive_capture_id(validator.EPOCH_ID, "01" * 32)
        kind = "process-events"
        schema = "dancers-planar-physics-build-process-event-v2"
        page0 = validator.make_support_page(
            capture_id=capture_id, kind=kind, record_schema_version=schema,
            page_index=0, first_record_index=0, records=[{"event_index": 0}, {"event_index": 1}],
        )
        page1 = validator.make_support_page(
            capture_id=capture_id, kind=kind, record_schema_version=schema,
            page_index=1, first_record_index=2, records=[{"event_index": 2}],
        )
        pages = [page0, page1]
        index = validator.make_support_index(
            capture_id=capture_id, kind=kind, record_schema_version=schema, pages=pages,
        )
        by_path = {reference["relative_path"]: page for reference, page in zip(index["pages"], pages)}
        return index, by_path

    def test_07_two_page_closure_and_independent_record_hash(self) -> None:
        index, pages = self._closure()
        validator.validate_support_page_closure(index, pages)
        records = pages["pages/process-events/000000.json"]["records"]
        digest = hashlib.sha256()
        digest.update(b"dancers-planar-physics-build-support-records-v2\0process-events\0")
        for record in records:
            raw = json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
            digest.update(len(raw).to_bytes(8, "big"))
            digest.update(raw)
        self.assertEqual(pages["pages/process-events/000000.json"]["records_sha256"], digest.hexdigest())

    def test_08_page_closure_mutations_fail_closed(self) -> None:
        mutations = {
            "missing-page": lambda i, p: p.pop("pages/process-events/000001.json"),
            "extra-page": lambda i, p: p.__setitem__("pages/process-events/999999.json", next(iter(p.values()))),
            "wrong-path": lambda i, p: i["pages"][0].__setitem__("relative_path", "../escape.json"),
            "wrong-index": lambda i, p: p["pages/process-events/000001.json"].__setitem__("page_index", 0),
            "wrong-first": lambda i, p: p["pages/process-events/000001.json"].__setitem__("first_record_index", 3),
            "wrong-page-hash": lambda i, p: p["pages/process-events/000000.json"].__setitem__("records_sha256", "0" * 64),
            "wrong-reference-bytes": lambda i, p: i["pages"][0].__setitem__("bytes", i["pages"][0]["bytes"] + 1),
            "wrong-overall-hash": lambda i, p: i.__setitem__("records_sha256", "0" * 64),
            "numeric-index-alias": lambda i, p: p["pages/process-events/000000.json"].__setitem__("page_index", 0.0),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                index, pages = self._closure()
                mutate(index, pages)
                with self.assertRaises(validator.ContractError):
                    validator.validate_support_page_closure(index, pages)

    def test_09_page_record_cap_is_not_caller_raiseable(self) -> None:
        capture_id = validator.derive_capture_id(validator.EPOCH_ID, "02" * 32)
        with self.assertRaises(validator.ContractError):
            validator.make_support_page(
                capture_id=capture_id, kind="diagnostics",
                record_schema_version="dancers-planar-physics-build-diagnostic-v2",
                page_index=0, first_record_index=0,
                records=[None] * (validator.RECORDS_PER_PAGE_MAX + 1),
            )
        with self.assertRaises(TypeError):
            validator.make_support_page(
                capture_id=capture_id, kind="diagnostics",
                record_schema_version="dancers-planar-physics-build-diagnostic-v2",
                page_index=0, first_record_index=0, records=[], records_per_page_max=999999,
            )
        with self.assertRaises(validator.ContractError):
            validator.make_support_page(
                capture_id=capture_id, kind="diagnostics",
                record_schema_version="dancers-planar-physics-build-diagnostic-v2",
                page_index=0, first_record_index=0, records=[],
            )
        index, pages = self._closure()
        with self.assertRaises(TypeError):
            validator.validate_support_page_closure(index, pages, allow_empty=True)

    def test_10_design_and_external_authority_surface_is_exact(self) -> None:
        self.assertEqual(self.contract["design_bindings"], validator.DESIGN_BINDINGS)
        self.assertEqual(
            self.contract["authorization_interface"]["external_verifier_arguments"],
            validator.EXTERNAL_VERIFIER_ARGUMENTS,
        )
        self.assertEqual(self.contract["hard_caps"]["support_index_files_exact"], 12)
        self.assertEqual(
            self.contract["hard_caps"]["support_pages_max"]
            + self.contract["hard_caps"]["support_index_files_exact"],
            self.contract["hard_caps"]["support_files_max"],
        )
        self.assertFalse(self.contract["page_closure"]["empty_pages_permitted"])
        self.assertTrue({
            "nested-type-and-cross-ledger-schema-closure-incomplete",
            "signed-consumption-and-registry-protocol-incomplete",
            "hash-formula-closure-incomplete",
            "package-wide-cap-accounting-incomplete",
            "utc-validity-and-trusted-clock-policy-incomplete",
        }.issubset(validator.BLOCKERS))
        self.assertIn("superbuild-common-object-partition-not-bound-or-implemented", validator.BLOCKERS)
        self.assertNotIn("superbuild-common-object-partition-not-frozen", validator.BLOCKERS)
        self.assertIn("common-source", self.contract["root_policy"]["required_root_ids"])
        self.assertIn("common-source", self.contract["root_policy"]["immutable_complete_roots"])

    def test_11_regular_file_identity_rejects_symlink_and_fifo(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            regular = root / "contract.json"
            regular.write_bytes(self.raw)
            regular.chmod(0o600)
            validator.validate_contract_file(regular)
            symlink = root / "contract-link.json"
            symlink.symlink_to(regular)
            with self.assertRaises(validator.ContractError):
                validator.validate_contract_file(symlink)
            fifo = root / "contract.fifo"
            os.mkfifo(fifo, 0o600)
            with self.assertRaises(validator.ContractError):
                validator.validate_contract_file(fifo)

    def test_12_validator_has_no_capture_or_network_runtime(self) -> None:
        source = VALIDATOR_PATH.read_text(encoding="utf-8")
        self.assertNotIn("import socket", source)
        self.assertNotIn("import subprocess", source)
        self.assertNotIn("comparison/results/simulator-demo-v1-physics-stress-test/epoch-1", source)
        self.assertNotIn("comparison/plans/simulator-demo-v1-physics-stress-test/epoch-1", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
