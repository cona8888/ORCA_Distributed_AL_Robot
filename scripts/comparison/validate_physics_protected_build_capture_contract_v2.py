#!/usr/bin/env python3
"""Validate the frozen, non-authorizing protected-build capture v2 contract.

This module deliberately does not sign, capture, execute, observe, publish, or
verify build provenance.  Its public hash and page-closure helpers exist only
to make the frozen byte-level contract independently testable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys
import unicodedata
from typing import Any, Mapping, Sequence


CONTRACT_SCHEMA = "dancers-planar-physics-protected-build-capture-contract-v2"
VERDICT_SCHEMA = "dancers-planar-physics-protected-build-capture-contract-verdict-v2"
PAGE_SCHEMA = "dancers-planar-physics-build-support-page-v2"
INDEX_SCHEMA = "dancers-planar-physics-build-support-index-v2"
EPOCH_ID = "simulator-demo-v1-physics-stress-test-epoch-1"
CONTRACT_BYTES = 24_617
CONTRACT_SHA256 = "5d96db2931644581644c7117f95dbcc94b8f155e7aac74b77a53cfd381be3b1a"
CONTRACT_BYTES_MAX = 1 << 20
JSON_DEPTH_MAX = 32
JSON_NODES_MAX = 2_000_000
RECORD_BYTES_MAX = 2 << 20
RECORDS_PER_PAGE_MAX = 8192
SUPPORT_PAGE_BYTES_MAX = 16 << 20
SUPPORT_INDEX_BYTES_MAX = 8 << 20
SUPPORT_PAGES_MAX = 4084
SUPPORT_INDEX_FILES_EXACT = 12
SUPPORT_FILES_MAX = 4096
SUPPORT_TOTAL_BYTES_MAX = 2 << 30
INT64_MIN = -(1 << 63)
INT64_MAX = (1 << 63) - 1

TOP_KEYS = {
    "authorization_interface", "canonical_json", "capture_id",
    "capture_semantics", "classification", "current_blockers", "design_bindings", "epoch_id",
    "file_identity", "hard_caps", "hash_domains", "identity_order",
    "network_policy", "page_closure", "publication_policy", "root_policy",
    "schema_version", "schemas", "scope", "verdict_contract",
}

CLASSIFICATION = {
    "build_capture_complete": False,
    "causal_capture_verified": False,
    "contract_only": True,
    "contract_structure_complete": False,
    "execution_authorized": False,
    "outcome_independent": True,
    "promotion_eligible": False,
    "provenance_complete": False,
    "registered_scientific_run": False,
    "schema_surface_frozen": True,
    "scientific_evidence": False,
    "scientific_evidence_eligible": False,
}

VERDICT_CLASSIFICATION = {
    "build_capture_complete": False,
    "causal_capture_verified": False,
    "contract_structure_complete": False,
    "execution_authorized": False,
    "promotion_eligible": False,
    "provenance_complete": False,
    "schema_surface_frozen": True,
    "scientific_evidence": False,
    "scientific_evidence_eligible": False,
}

SCOPE = {
    "actual_capture_artifacts_created": False,
    "capture_grant_or_ticket_created": False,
    "capture_observer_or_executor_implemented": False,
    "capture_packager_implemented": False,
    "capture_signer_implemented": False,
    "capture_trust_root_created": False,
    "contract_validator_only": True,
    "official_namespace_materialized": False,
    "real_capture_verifier_implemented": False,
    "registered_build_or_simulator_permitted": False,
    "v1_contract_design_or_freezer_modified": False,
}

HARD_CAPS = {
    "action_stream_bytes_max": 4 << 20,
    "actions_max": 200_000,
    "all_action_stream_bytes_max": 512 << 20,
    "all_roots_regular_bytes_max": 128 << 30,
    "arg_bytes_max": 32_768,
    "argv_items_max": 8192,
    "authentication_cache_entries_max": 1_000_000,
    "authentication_reference_lookups_max": 10_000_000,
    "authentication_unique_bytes_max": 128 << 30,
    "caller_override_permitted": False,
    "capture_grant_bytes_max": 256 << 10,
    "capture_seal_bytes_max": 1 << 20,
    "consumption_receipt_bytes_max": 256 << 10,
    "contract_bytes_max": CONTRACT_BYTES_MAX,
    "dynamic_inputs_max": 4096,
    "environments_max": 256,
    "execution_records_max": 400_000,
    "filesystem_events_max": 5_000_000,
    "json_depth_max": JSON_DEPTH_MAX,
    "json_nodes_per_file_max": JSON_NODES_MAX,
    "main_manifest_bytes_max": 4 << 20,
    "network_events_max": 100_000,
    "network_receipt_bytes_max": 2 << 20,
    "objects_max": 100_000,
    "path_bytes_max": 1024,
    "path_generations_max": 2_000_000,
    "process_events_max": 1_200_000,
    "process_instances_max": 400_000,
    "products_exact": 4,
    "record_canonical_bytes_max": RECORD_BYTES_MAX,
    "records_per_page_max": RECORDS_PER_PAGE_MAX,
    "regular_file_bytes_max": 512 << 20,
    "response_files_per_action_max": 1024,
    "root_count_max": 32,
    "support_files_max": SUPPORT_FILES_MAX,
    "support_index_files_exact": SUPPORT_INDEX_FILES_EXACT,
    "support_index_bytes_max": SUPPORT_INDEX_BYTES_MAX,
    "support_page_bytes_max": SUPPORT_PAGE_BYTES_MAX,
    "support_pages_max": SUPPORT_PAGES_MAX,
    "support_total_bytes_max": SUPPORT_TOTAL_BYTES_MAX,
    "tree_files_per_root_max": 500_000,
    "tree_regular_bytes_per_root_max": 32 << 30,
    "trust_root_bytes_max": 2 << 20,
}

DOMAIN_TEXT = {
    "action_completion": "dancers-planar-physics-build-action-completion-v2\\0",
    "action_id": "dancers-planar-physics-build-action-id-v2\\0",
    "artifact_id": "dancers-planar-physics-build-artifact-id-v2\\0",
    "authority_key_id": "dancers-planar-physics-build-capture-authority-key-id-v2\\0",
    "capture_id": "dancers-planar-physics-build-capture-id-v2\\0",
    "capture_seal": "dancers-planar-physics-build-capture-seal-v2\\0",
    "grant_signature": "dancers-planar-physics-build-capture-grant-signature-v2\\0",
    "path_generation": "dancers-planar-physics-build-path-generation-v2\\0",
    "process_instance": "dancers-planar-physics-build-process-instance-v2\\0",
    "registry_entry": "dancers-planar-physics-build-capture-registry-entry-v2\\0",
    "root_tree": "dancers-planar-physics-build-root-tree-v2\\0",
    "support_closure": "dancers-planar-physics-build-support-closure-v2\\0",
    "support_records": "dancers-planar-physics-build-support-records-v2\\0",
    "trust_root_signature": "dancers-planar-physics-build-trust-root-signature-v2\\0",
}

BLOCKERS = [
    "production-build-capture-verifier-not-implemented",
    "independently-pinned-ed25519-authority-key-absent",
    "capture-specific-v2-trust-root-absent",
    "signed-single-use-capture-grant-absent",
    "durable-authority-consumption-registry-absent",
    "prospective-immutable-input-manifest-absent",
    "prospective-complete-action-plan-absent",
    "superbuild-common-object-partition-not-bound-or-implemented",
    "normalized-read-only-toolchain-stage-absent",
    "normalized-read-only-sdk-stage-absent",
    "protected-single-writer-capture-namespace-absent",
    "macos-lossless-process-observer-absent",
    "macos-lossless-filesystem-observer-absent",
    "macos-lossless-network-observer-absent",
    "prospectively-pinned-network-sandbox-backend-absent",
    "production-macho-and-recursive-dynamic-resolver-absent",
    "fresh-protected-four-product-capture-absent",
    "sealed-or-failed-authority-registry-receipt-absent",
    "build-test-attestation-absent",
    "external-execution-identity-manifest-absent",
    "execution-design-and-freezer-v2-rebind-absent",
    "nested-type-and-cross-ledger-schema-closure-incomplete",
    "signed-consumption-and-registry-protocol-incomplete",
    "hash-formula-closure-incomplete",
    "package-wide-cap-accounting-incomplete",
    "utc-validity-and-trusted-clock-policy-incomplete",
]

DESIGN_BINDINGS = {
    "source_lift": {
        "bytes": 12_123,
        "path": "docs/simulator/physics_source_lift_epoch_1_v1.json",
        "schema_version": "dancers-planar-physics-source-lift-feasibility-v1",
        "sha256": "52ab7a20d01978107b1122ce70c8fe2a3719f34719e5c61cff5a960581d46bee",
    },
    "superbuild_graph": {
        "bytes": 14_455,
        "path": "comparison/physics-superbuild-v1/source_graph_v1.json",
        "schema_version": "dancers-planar-physics-superbuild-source-graph-v1",
        "sha256": "3df23a2292833277e707d46d961d640928653317664206171787e590491e7c8d",
    },
}

EXTERNAL_VERIFIER_ARGUMENTS = [
    "authority_public_key_path",
    "expected_authority_public_key_bytes",
    "expected_authority_public_key_sha256",
    "expected_authority_key_id",
    "expected_capture_id",
    "expected_trust_root_bytes",
    "expected_trust_root_sha256",
    "expected_provenance_bytes",
    "expected_provenance_sha256",
    "capture_seal_path",
    "expected_capture_seal_bytes",
    "expected_capture_seal_sha256",
    "terminal_registry_receipt_path",
    "expected_terminal_registry_receipt_bytes",
    "expected_terminal_registry_receipt_sha256",
    "expected_registry_genesis_sha256",
    "expected_registry_head_index",
    "expected_registry_head_sha256",
    "trusted_clock_receipt_path",
    "expected_trusted_clock_receipt_bytes",
    "expected_trusted_clock_receipt_sha256",
]

SUPPORT_KINDS = [
    "root-snapshots", "toolchain-sdk-dependencies", "environments",
    "action-receipts", "process-events", "execution-records",
    "filesystem-events", "path-generations", "network-events",
    "macho-dynamic-closure", "object-product-closure", "diagnostics",
]

ORDERED_STAGES = [
    "independently-pin-contract-code-authority-public-key-and-host-policy",
    "authority-issues-one-capture-nonce-and-capture-id",
    "freeze-complete-prospective-input-and-action-plan-identities",
    "freeze-and-independently-pin-capture-specific-trust-root",
    "authority-signs-capture-grant",
    "authority-durably-consumes-capture-id-before-any-guarded-child",
    "activate-sandbox-and-lossless-observers",
    "authenticate-empty-writable-roots-and-immutable-input-roots",
    "capture-actions-processes-filesystem-generations-and-local-network-denials",
    "rehash-terminal-roots-and-seal-support-pages-and-indexes",
    "publish-main-build-provenance",
    "publish-capture-seal",
    "authority-publishes-signed-terminal-sealed-or-failed-registry-receipt",
    "later-build-test-attestation-binds-capture-seal-and-provenance",
    "later-external-execution-identity-manifest-binds-build-and-test",
]

PAGE_KEYS = {
    "schema_version", "epoch_id", "capture_id", "kind",
    "record_schema_version", "page_index", "first_record_index",
    "record_count", "records", "records_sha256",
}
INDEX_KEYS = {
    "schema_version", "epoch_id", "capture_id", "kind",
    "record_schema_version", "record_count", "records_canonical_bytes",
    "page_count", "pages", "records_sha256",
}
PAGE_REF_KEYS = {
    "page_index", "first_record_index", "record_count", "relative_path",
    "bytes", "sha256", "records_sha256",
}


class ContractError(ValueError):
    """A fail-closed contract or helper validation error."""


def _reject_constant(token: str) -> None:
    raise ContractError(f"non-finite JSON number is forbidden: {token}")


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _json_shape(value: Any, *, maximum_bytes: int) -> None:
    nodes = 0
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > JSON_NODES_MAX:
            raise ContractError("JSON node cap exceeded")
        if depth > JSON_DEPTH_MAX:
            raise ContractError("JSON depth cap exceeded")
        if item is None or type(item) is bool:
            continue
        if type(item) is int:
            if not INT64_MIN <= item <= INT64_MAX:
                raise ContractError("integer is outside signed 64-bit range")
            continue
        if type(item) is str:
            if len(item) > maximum_bytes:
                raise ContractError("JSON string exceeds byte ceiling")
            if unicodedata.normalize("NFC", item) != item:
                raise ContractError("JSON string is not NFC")
            if any(0xD800 <= ord(character) <= 0xDFFF for character in item):
                raise ContractError("surrogate code point is forbidden")
            continue
        if type(item) is list:
            stack.extend((child, depth + 1) for child in reversed(item))
            continue
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise ContractError("JSON object keys must be exact strings")
                stack.append((child, depth + 1))
                stack.append((key, depth + 1))
            continue
        raise ContractError(f"unsupported JSON type: {type(item).__name__}")


def _canonical_fragment(value: Any, maximum_bytes: int) -> bytes:
    if type(maximum_bytes) is not int or not 1 <= maximum_bytes <= SUPPORT_PAGE_BYTES_MAX:
        raise ContractError("invalid or caller-raised canonical byte ceiling")
    _json_shape(value, maximum_bytes=maximum_bytes)
    encoder = json.JSONEncoder(
        ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )
    chunks: list[bytes] = []
    total = 0
    try:
        for text_chunk in encoder.iterencode(value):
            chunk = text_chunk.encode("utf-8", "strict")
            total += len(chunk)
            if total > maximum_bytes:
                raise ContractError("canonical JSON byte ceiling exceeded")
            chunks.append(chunk)
    except (RecursionError, UnicodeError, ValueError) as error:
        if isinstance(error, ContractError):
            raise
        raise ContractError("JSON serialization failed") from error
    return b"".join(chunks)


def canonical_json_bytes(value: Any) -> bytes:
    """Return one canonical contract-sized JSON file; callers cannot raise its cap."""
    fragment = _canonical_fragment(value, CONTRACT_BYTES_MAX - 1)
    return fragment + b"\n"


def parse_canonical_json(raw: bytes) -> Any:
    if type(raw) is not bytes or not 1 <= len(raw) <= CONTRACT_BYTES_MAX:
        raise ContractError("contract byte length is outside the fixed ceiling")
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise ContractError("canonical JSON file must end in exactly one LF")
    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
            parse_float=lambda _: (_ for _ in ()).throw(ContractError("floats are forbidden")),
        )
    except ContractError:
        raise
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ContractError("invalid JSON") from error
    if canonical_json_bytes(value) != raw:
        raise ContractError("contract is not canonical JSON")
    return value


def _strict_equal(actual: Any, expected: Any, label: str) -> None:
    if type(actual) is not type(expected):
        raise ContractError(f"{label} has the wrong JSON type")
    if type(expected) is dict:
        if set(actual) != set(expected):
            raise ContractError(f"{label} has the wrong exact key set")
        for key in expected:
            _strict_equal(actual[key], expected[key], f"{label}.{key}")
    elif type(expected) is list:
        if len(actual) != len(expected):
            raise ContractError(f"{label} has the wrong list length")
        for index, item in enumerate(expected):
            _strict_equal(actual[index], item, f"{label}[{index}]")
    elif actual != expected:
        raise ContractError(f"{label} has the wrong value")


def _exact_keys(value: Any, expected: set[str], label: str) -> Mapping[str, Any]:
    if type(value) is not dict or set(value) != expected:
        raise ContractError(f"{label} has the wrong exact key set")
    return value


def _exact_int(value: Any, label: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ContractError(f"{label} must be an exact integer >= {minimum}")
    return value


def _hex(value: Any, label: str, characters: int = 64) -> str:
    if type(value) is not str or len(value) != characters:
        raise ContractError(f"{label} must be {characters} lowercase hexadecimal characters")
    if any(character not in "0123456789abcdef" for character in value):
        raise ContractError(f"{label} is not lowercase hexadecimal")
    return value


def validate_contract_value(contract: Any) -> None:
    value = _exact_keys(contract, TOP_KEYS, "contract")
    if value["schema_version"] != CONTRACT_SCHEMA or value["epoch_id"] != EPOCH_ID:
        raise ContractError("contract schema or epoch mismatch")
    _strict_equal(value["classification"], CLASSIFICATION, "classification")
    _strict_equal(value["scope"], SCOPE, "scope")
    _strict_equal(value["hard_caps"], HARD_CAPS, "hard_caps")
    _strict_equal(value["hash_domains"], DOMAIN_TEXT, "hash_domains")
    _strict_equal(value["current_blockers"], BLOCKERS, "current_blockers")
    _strict_equal(value["design_bindings"], DESIGN_BINDINGS, "design_bindings")
    if len(set(BLOCKERS)) != len(BLOCKERS):
        raise ContractError("blocker identifiers are not unique")
    if value["identity_order"].get("ordered_stages") != ORDERED_STAGES:
        raise ContractError("one-way identity ordering mismatch")
    closure = value["page_closure"]
    if closure.get("empty_pages_permitted") is not False:
        raise ContractError("empty support pages must be categorically forbidden")
    if closure.get("required_support_kinds") != SUPPORT_KINDS:
        raise ContractError("support-kind closure mismatch")
    if closure.get("page_fields") != list(PAGE_KEYS):
        # Order is itself frozen by the full contract hash; compare membership here.
        if set(closure.get("page_fields", [])) != PAGE_KEYS:
            raise ContractError("page field closure mismatch")
    if set(closure.get("index_fields", [])) != INDEX_KEYS:
        raise ContractError("index field closure mismatch")
    if set(closure.get("page_reference_fields", [])) != PAGE_REF_KEYS:
        raise ContractError("page-reference field closure mismatch")
    schemas = value["schemas"]
    if type(schemas) is not dict or not schemas:
        raise ContractError("schema registry must be a nonempty object")
    for name, schema in schemas.items():
        _exact_keys(schema, {"schema_version", "exact_keys"}, f"schemas.{name}")
        if type(schema["schema_version"]) is not str or not schema["schema_version"].endswith("-v2"):
            raise ContractError(f"schemas.{name}.schema_version is invalid")
        keys = schema["exact_keys"]
        if type(keys) is not list or not keys or any(type(key) is not str for key in keys):
            raise ContractError(f"schemas.{name}.exact_keys is invalid")
        if len(set(keys)) != len(keys):
            raise ContractError(f"schemas.{name}.exact_keys contains a duplicate")
    auth = value["authorization_interface"]
    if auth.get("signature_algorithm") != "Ed25519":
        raise ContractError("signature algorithm is not Ed25519")
    if auth.get("raw_public_key_bytes") != 32 or auth.get("raw_signature_bytes") != 64:
        raise ContractError("Ed25519 byte lengths are not exact")
    if auth.get("independent_public_key_required") is not True:
        raise ContractError("authority key is not independently anchored")
    _strict_equal(
        auth.get("external_verifier_arguments"),
        EXTERNAL_VERIFIER_ARGUMENTS,
        "authorization_interface.external_verifier_arguments",
    )
    roots = value["root_policy"]
    if "common-source" not in roots.get("required_root_ids", []):
        raise ContractError("common-source root is not required")
    if "common-source" not in roots.get("immutable_complete_roots", []):
        raise ContractError("common-source root is not immutable and complete")
    if HARD_CAPS["support_pages_max"] + HARD_CAPS["support_index_files_exact"] != HARD_CAPS["support_files_max"]:
        raise ContractError("support page/index/file ceilings do not close")
    network = value["network_policy"]
    if network.get("local_only") is not True:
        raise ContractError("network canary policy is not local-only")
    if network.get("test_net_or_external_address_permitted") is not False:
        raise ContractError("external canary address is permitted")
    if network.get("build_process_network_events_permitted") != 0:
        raise ContractError("build-process network events are not categorically zero")
    publication = value["publication_policy"]
    for key in (
        "official_execution_identity_namespace_permitted",
        "official_plan_namespace_permitted",
        "official_result_namespace_permitted",
    ):
        if publication.get(key) is not False:
            raise ContractError(f"{key} must be false")
    verdict = value["verdict_contract"]
    if verdict.get("contract_validator_schema_version") != VERDICT_SCHEMA:
        raise ContractError("verdict schema mismatch")
    _strict_equal(
        verdict.get("required_contract_validator_classification"),
        VERDICT_CLASSIFICATION,
        "verdict classification",
    )
    encoded = canonical_json_bytes(value)
    if len(encoded) != CONTRACT_BYTES:
        raise ContractError("contract byte identity mismatch")
    if hashlib.sha256(encoded).hexdigest() != CONTRACT_SHA256:
        raise ContractError("contract SHA-256 identity mismatch")


def _read_contract(path: Path) -> bytes:
    if not path.is_absolute() or os.path.normpath(str(path)) != str(path):
        raise ContractError("contract path must be canonical and absolute")
    if os.path.realpath(path) != str(path):
        raise ContractError("contract path or an ancestor is a symlink")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ContractError("cannot open contract as a no-follow file") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise ContractError("contract must be one regular, non-hardlinked file")
        if before.st_mode & 0o022:
            raise ContractError("contract must not be group/world writable")
        if not 1 <= before.st_size <= CONTRACT_BYTES_MAX:
            raise ContractError("contract file size is outside the fixed ceiling")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                raise ContractError("contract was truncated during read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ContractError("contract grew during read")
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    try:
        named = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise ContractError("contract name disappeared after read") from error
    identity = lambda item: (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_mode)
    if identity(before) != identity(after) or identity(after) != identity(named):
        raise ContractError("contract identity changed during read")
    return b"".join(chunks)


def validate_contract_file(path: Path) -> dict[str, Any]:
    raw = _read_contract(path)
    contract = parse_canonical_json(raw)
    validate_contract_value(contract)
    return {
        "schema_version": VERDICT_SCHEMA,
        "valid": True,
        "classification": dict(VERDICT_CLASSIFICATION),
        "contract": {
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
        },
        "capture_blockers": list(BLOCKERS),
        "promotion_blockers": list(BLOCKERS),
    }


def _domain(name: str) -> bytes:
    text = DOMAIN_TEXT[name]
    if not text.endswith("\\0"):
        raise ContractError("internal hash domain is malformed")
    return text[:-2].encode("ascii") + b"\0"


def derive_capture_id(epoch_id: str, nonce_hex: str) -> str:
    if epoch_id != EPOCH_ID:
        raise ContractError("capture-id epoch mismatch")
    _hex(nonce_hex, "capture nonce", 64)
    return hashlib.sha256(_domain("capture_id") + epoch_id.encode() + b"\0" + bytes.fromhex(nonce_hex)).hexdigest()


def derive_authority_key_id(raw_public_key: bytes) -> str:
    if type(raw_public_key) is not bytes or len(raw_public_key) != 32:
        raise ContractError("Ed25519 public key must be exactly 32 raw bytes")
    return hashlib.sha256(_domain("authority_key_id") + raw_public_key).hexdigest()


def _schema_keys(name: str) -> set[str]:
    # Do not consult the live contract here: helpers remain independently fixed.
    if name == "action_intent":
        return {"kind", "method", "target", "parent_action_id", "cwd", "executable", "argv", "environment_id", "stdin", "network_session_id"}
    if name == "artifact_without_id":
        return {"schema_version", "relative_path", "file_type", "mode", "bytes", "sha256"}
    raise ContractError(f"unknown helper schema: {name}")


def derive_action_id(action_intent: Mapping[str, Any]) -> str:
    _exact_keys(action_intent, _schema_keys("action_intent"), "action_intent")
    return hashlib.sha256(_domain("action_id") + _canonical_fragment(action_intent, RECORD_BYTES_MAX)).hexdigest()


def derive_artifact_id(artifact_without_id: Mapping[str, Any]) -> str:
    _exact_keys(artifact_without_id, _schema_keys("artifact_without_id"), "artifact")
    return hashlib.sha256(_domain("artifact_id") + _canonical_fragment(artifact_without_id, RECORD_BYTES_MAX)).hexdigest()


def support_records_sha256(kind: str, records: Sequence[Any]) -> str:
    if type(kind) is not str or kind not in SUPPORT_KINDS:
        raise ContractError("unknown support kind")
    if type(records) not in (list, tuple) or len(records) > SUPPORT_PAGES_MAX * RECORDS_PER_PAGE_MAX:
        raise ContractError("support record count exceeds the fixed closure cap")
    digest = hashlib.sha256()
    digest.update(_domain("support_records"))
    digest.update(kind.encode("utf-8"))
    digest.update(b"\0")
    for record in records:
        encoded = _canonical_fragment(record, RECORD_BYTES_MAX)
        digest.update(struct.pack(">Q", len(encoded)))
        digest.update(encoded)
    return digest.hexdigest()


def make_support_page(
    *, capture_id: str, kind: str, record_schema_version: str,
    page_index: int, first_record_index: int, records: list[Any],
) -> dict[str, Any]:
    _hex(capture_id, "capture_id")
    if kind not in SUPPORT_KINDS:
        raise ContractError("unknown support kind")
    if type(record_schema_version) is not str or not record_schema_version.endswith("-v2"):
        raise ContractError("record schema version must be a v2 string")
    _exact_int(page_index, "page_index")
    _exact_int(first_record_index, "first_record_index")
    if type(records) is not list or not 1 <= len(records) <= RECORDS_PER_PAGE_MAX:
        raise ContractError("records must be a nonempty bounded exact array")
    page = {
        "schema_version": PAGE_SCHEMA,
        "epoch_id": EPOCH_ID,
        "capture_id": capture_id,
        "kind": kind,
        "record_schema_version": record_schema_version,
        "page_index": page_index,
        "first_record_index": first_record_index,
        "record_count": len(records),
        "records": records,
        "records_sha256": support_records_sha256(kind, records),
    }
    if len(_canonical_fragment(page, SUPPORT_PAGE_BYTES_MAX - 1)) + 1 > SUPPORT_PAGE_BYTES_MAX:
        raise ContractError("support page exceeds the fixed byte cap")
    return page


def make_support_index(
    *, capture_id: str, kind: str, record_schema_version: str,
    pages: list[Mapping[str, Any]],
) -> dict[str, Any]:
    if type(pages) is not list or not 1 <= len(pages) <= SUPPORT_PAGES_MAX:
        raise ContractError("support page count is outside the fixed bound")
    references: list[dict[str, Any]] = []
    all_records: list[Any] = []
    total_bytes = 0
    for page_index, page in enumerate(pages):
        _exact_keys(page, PAGE_KEYS, f"page[{page_index}]")
        raw = _canonical_fragment(page, SUPPORT_PAGE_BYTES_MAX - 1) + b"\n"
        total_bytes += len(raw)
        references.append({
            "page_index": page["page_index"],
            "first_record_index": page["first_record_index"],
            "record_count": page["record_count"],
            "relative_path": f"pages/{kind}/{page_index:06d}.json",
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "records_sha256": page["records_sha256"],
        })
        all_records.extend(page["records"])
    index = {
        "schema_version": INDEX_SCHEMA,
        "epoch_id": EPOCH_ID,
        "capture_id": capture_id,
        "kind": kind,
        "record_schema_version": record_schema_version,
        "record_count": len(all_records),
        "records_canonical_bytes": sum(len(_canonical_fragment(record, RECORD_BYTES_MAX)) for record in all_records),
        "page_count": len(pages),
        "pages": references,
        "records_sha256": support_records_sha256(kind, all_records),
    }
    index_bytes = len(_canonical_fragment(index, SUPPORT_INDEX_BYTES_MAX - 1)) + 1
    if index_bytes > SUPPORT_INDEX_BYTES_MAX or total_bytes + index_bytes > SUPPORT_TOTAL_BYTES_MAX:
        raise ContractError("support closure exceeds a fixed byte cap")
    return index


def validate_support_page_closure(
    index: Mapping[str, Any], pages_by_path: Mapping[str, Mapping[str, Any]],
) -> None:
    _exact_keys(index, INDEX_KEYS, "index")
    if index["schema_version"] != INDEX_SCHEMA or index["epoch_id"] != EPOCH_ID:
        raise ContractError("index schema or epoch mismatch")
    capture_id = _hex(index["capture_id"], "index.capture_id")
    kind = index["kind"]
    if type(kind) is not str or kind not in SUPPORT_KINDS:
        raise ContractError("index kind is not registered")
    record_schema = index["record_schema_version"]
    if type(record_schema) is not str or not record_schema.endswith("-v2"):
        raise ContractError("index record schema is invalid")
    references = index["pages"]
    if type(references) is not list or not 1 <= len(references) <= SUPPORT_PAGES_MAX:
        raise ContractError("index page array is outside the fixed bound")
    if type(pages_by_path) is not dict or len(pages_by_path) != len(references):
        raise ContractError("page closure has a missing or extra page")
    expected_first = 0
    records: list[Any] = []
    total_file_bytes = 0
    for page_number, reference in enumerate(references):
        _exact_keys(reference, PAGE_REF_KEYS, f"page reference {page_number}")
        if reference["relative_path"] != f"pages/{kind}/{page_number:06d}.json":
            raise ContractError("page path is not exact and contiguous")
        page = pages_by_path.get(reference["relative_path"])
        if page is None:
            raise ContractError("referenced page is missing")
        _exact_keys(page, PAGE_KEYS, f"page {page_number}")
        for key, expected in (
            ("schema_version", PAGE_SCHEMA), ("epoch_id", EPOCH_ID),
            ("capture_id", capture_id), ("kind", kind),
            ("record_schema_version", record_schema), ("page_index", page_number),
            ("first_record_index", expected_first),
        ):
            _strict_equal(page[key], expected, f"page[{page_number}].{key}")
        page_records = page["records"]
        if type(page_records) is not list or len(page_records) > RECORDS_PER_PAGE_MAX:
            raise ContractError("page records exceed the fixed bound")
        if not page_records:
            raise ContractError("empty pages are forbidden in this scaffold")
        if page["record_count"] != len(page_records) or type(page["record_count"]) is not int:
            raise ContractError("page record count mismatch")
        expected_record_hash = support_records_sha256(kind, page_records)
        if page["records_sha256"] != expected_record_hash:
            raise ContractError("page record hash mismatch")
        raw = _canonical_fragment(page, SUPPORT_PAGE_BYTES_MAX - 1) + b"\n"
        total_file_bytes += len(raw)
        expected_reference = {
            "page_index": page_number,
            "first_record_index": expected_first,
            "record_count": len(page_records),
            "relative_path": f"pages/{kind}/{page_number:06d}.json",
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "records_sha256": expected_record_hash,
        }
        _strict_equal(reference, expected_reference, f"page reference {page_number}")
        records.extend(page_records)
        expected_first += len(page_records)
    expected_index = make_support_index(
        capture_id=capture_id,
        kind=kind,
        record_schema_version=record_schema,
        pages=[pages_by_path[reference["relative_path"]] for reference in references],
    )
    _strict_equal(index, expected_index, "index")
    index_bytes = len(_canonical_fragment(index, SUPPORT_INDEX_BYTES_MAX - 1)) + 1
    if total_file_bytes + index_bytes > SUPPORT_TOTAL_BYTES_MAX:
        raise ContractError("support closure exceeds the aggregate cap")


def _default_contract() -> Path:
    return Path(__file__).resolve().parents[2] / "docs/simulator/physics_protected_build_capture_contract_v2.json"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", nargs="?", type=Path, default=_default_contract())
    arguments = parser.parse_args(argv)
    try:
        verdict = validate_contract_file(arguments.contract)
    except ContractError as error:
        print(f"physics protected-build capture contract v2 rejected: {error}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(canonical_json_bytes(verdict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
