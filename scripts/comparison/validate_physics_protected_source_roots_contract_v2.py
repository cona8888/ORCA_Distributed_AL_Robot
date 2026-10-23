#!/usr/bin/env python3
"""Validate the frozen, non-authorizing protected-source-roots v2 contract.

The module is data-only.  It does not materialize a source root, invoke Git,
configure or build software, open a network connection, or authorize capture
or execution.  Its helpers make the frozen index, typed-tree, manifest, and
source-input-set hash formulas independently testable with synthetic values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple
import unicodedata


CONTRACT_SCHEMA = "dancers-planar-physics-protected-source-roots-contract-v2"
VERDICT_SCHEMA = "dancers-planar-physics-protected-source-roots-contract-verdict-v2"
SOURCE_INDEX_SCHEMA = "dancers-planar-physics-source-index-v2"
ROOT_MANIFEST_SCHEMA = "dancers-planar-physics-source-root-manifest-v2"
HANDOFF_SCHEMA = "dancers-planar-physics-source-input-handoff-v2"
EPOCH_ID = "simulator-demo-v1-physics-stress-test-epoch-1"
CONTRACT_BYTES = 19_728
CONTRACT_SHA256 = "21fb7fde7dc8b44eba46d4223e9c441f38888b6dc427ff1a7b3993eab92675a6"
CONTRACT_BYTES_MAX = 1 << 20
JSON_DEPTH_MAX = 32
JSON_NODES_MAX = 200_000
PATH_BYTES_MAX = 1024
SOURCE_INDEX_BYTES_MAX = 2 << 20
ROOT_MANIFEST_BYTES_MAX = 2 << 20
REGULAR_FILE_BYTES_MAX = 4 << 20
INT64_MIN = -(1 << 63)
INT64_MAX = (1 << 63) - 1
SCHEMAS_SHA256 = "240bba806b32c760503b3e5c85f2c0caf7161a8e48318d246deec35b39579d7c"

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")

TOP_KEYS = {
    "canonical_json", "classification", "current_blockers", "design_bindings",
    "epoch_id", "handoff", "hard_caps", "hash_domains", "hash_formulas",
    "materialization_policy", "projected_roots", "schema_version", "schemas",
    "scope", "verdict_contract",
}

CLASSIFICATION = {
    "build_authorized": False,
    "build_capture_complete": False,
    "build_executed": False,
    "capture_complete": False,
    "contract_only": True,
    "execution_authorized": False,
    "official_namespace_materialized": False,
    "production_source_roots_materialized": False,
    "promotion_eligible": False,
    "provenance_complete": False,
    "registered_scientific_run": False,
    "schema_surface_frozen": True,
    "scientific_evidence": False,
    "scientific_evidence_eligible": False,
    "scientific_execution_authorized": False,
}

VERDICT_CLASSIFICATION = {
    key: value for key, value in CLASSIFICATION.items()
    if key not in {"contract_only", "official_namespace_materialized",
                   "registered_scientific_run", "schema_surface_frozen"}
}

SCOPE = {
    "action_plan_created": False,
    "build_input_manifest_created": False,
    "capture_grant_created": False,
    "capture_trust_root_created": False,
    "contract_validator_only": True,
    "diagnostic_fixture_materialization_only": True,
    "official_namespace_materialized": False,
    "production_materializer_implemented": False,
    "production_roots_created": False,
    "registered_build_or_simulator_permitted": False,
    "root_verifier_implemented": False,
}

BLOCKERS = [
    "independently-retained-signed-source-review-receipt-absent",
    "production-source-root-materializer-not-implemented",
    "production-source-root-verifier-not-implemented",
    "protected-single-writer-source-root-namespace-absent",
    "prospectively-pinned-read-only-enforcement-backend-absent",
    "production-protected-source-roots-absent",
    "capture-specific-v2-trust-root-absent",
    "signed-single-use-capture-grant-absent",
    "prospective-build-input-handoff-absent",
    "prospective-complete-action-plan-absent",
    "protected-four-product-build-capture-absent",
    "build-test-attestation-absent",
    "external-execution-identity-manifest-absent",
    "execution-design-and-freezer-v2-rebind-absent",
]

DOMAINS = {
    "root_manifest": "dancers-planar-physics-source-root-manifest-v2\\0",
    "root_tree": "dancers-planar-physics-build-root-tree-v2\\0",
    "source_index": "dancers-planar-physics-source-index-v2\\0",
    "source_input_handoff": "dancers-planar-physics-source-input-handoff-v2\\0",
}

FORMULAS = {
    "root_manifest_sha256": "sha256(root_manifest_domain_bytes || canonical_json_fragment(root_manifest))",
    "root_tree_domain_compatibility": "every successor using dancers-planar-physics-build-root-tree-v2 must use this same generic typed length-prefixed encoding with an explicitly frozen root-specific mode policy, otherwise it must allocate a distinct domain",
    "root_tree_sha256": "sha256(root_tree_domain_bytes || for the exact root record followed by every exact typed directory and regular-file record sorted by relative-path UTF-8 bytes: uint64be(length(canonical_json_fragment(root_tree_entry))) || canonical_json_fragment(root_tree_entry))",
    "source_index_id": "sha256(source_index_domain_bytes || canonical_json_fragment(source_index_without_source_index_id))",
    "source_input_handoff_sha256": "sha256(source_input_handoff_domain_bytes || canonical_json_fragment(source_input_handoff_without_source_input_handoff_sha256))",
}

ROOT_SPECS = [
    {
        "root_id": "common-source", "entry_count": 371,
        "implicit_directory_count": 54, "payload_bytes": 13_288_895,
        "manifest_bytes": 62_339,
        "manifest_sha256": "d570fbe72e5c47125666292e938ee43faf3b920070157204fdd5e6e54f4ce3c8",
        "path_list_sha256": "a9c0ca8926fe5a68d4646bb0337527fb6fdec33294dddf713e9de9678d81e03d",
        "projected_git_tree": "47f49279f69191b7a7835b8e4e8aa6ae51a2c1fb",
        "root_tree_sha256": "34c19c0a486ffde250de7e75d0672cefc857e8751972dfa06f6c528a89182ae6",
        "tree_entry_count": 426,
        "git_mode_counts": {"100644": 362, "100755": 9},
        "selection": "the exact thirteen common project paths frozen by the integration record plus every recursive Git blob below thirdparty/, and no other entry",
        "endpoint_anchors": [
            {"commit": "5383edc2cd17e72f88c6629f0c3f25125a3bd5a5", "tree": "d0f2a23cc6649887d326321ff3b3df721a3b4afc"},
            {"commit": "d1a9cd3bd3bf7ede17ed9f4014b84e64289a09af", "tree": "e548979ec8c43716bd32da25793e60d2b3f45e94"},
        ],
    },
    {
        "root_id": "optimized-source", "entry_count": 53,
        "implicit_directory_count": 7, "payload_bytes": 657_139,
        "manifest_bytes": 7_901,
        "manifest_sha256": "14336a315f5d8b2c40b9e28a1befd15c86bc394b0077d1e07af27f0d77ecf48c",
        "path_list_sha256": "f25cad29eab535de673e82763baeb74652d13a3e6b0b6fe48eaccd40b4b0310c",
        "projected_git_tree": "702a8d82521385f6ce9374f8591ce5a3f5a22c25",
        "root_tree_sha256": "90702954c86f05e25546a4b31825f8c0efb2a20d6adeb72aceee6f973d1827ba",
        "tree_entry_count": 61,
        "git_mode_counts": {"100644": 53},
        "selection": "every recursive Git blob below inc/ plus the exact sixteen controller-aggregate and five fidelity-observer source paths, and no other entry",
        "endpoint_anchors": [
            {"commit": "5383edc2cd17e72f88c6629f0c3f25125a3bd5a5", "tree": "d0f2a23cc6649887d326321ff3b3df721a3b4afc"},
        ],
    },
    {
        "root_id": "reference-source", "entry_count": 53,
        "implicit_directory_count": 7, "payload_bytes": 649_522,
        "manifest_bytes": 7_900,
        "manifest_sha256": "e095c923550460de9fbcfa6dc6aaabaf7d54d9773898230815a7168cb792508f",
        "path_list_sha256": "f25cad29eab535de673e82763baeb74652d13a3e6b0b6fe48eaccd40b4b0310c",
        "projected_git_tree": "a9a319cd4259e9acba848a83eafae3a992bdfc87",
        "root_tree_sha256": "852bd7fd755d90a2f3f470046a37c397dcdd5877a427431f0d5373bd400327ef",
        "tree_entry_count": 61,
        "git_mode_counts": {"100644": 53},
        "selection": "every recursive Git blob below inc/ plus the exact sixteen controller-aggregate and five fidelity-observer source paths, and no other entry",
        "endpoint_anchors": [
            {"commit": "d1a9cd3bd3bf7ede17ed9f4014b84e64289a09af", "tree": "e548979ec8c43716bd32da25793e60d2b3f45e94"},
        ],
    },
]

FILE_IDENTITY_KEYS = {"root_id", "relative_path", "file_type", "mode", "bytes", "sha256"}
TREE_ENTRY_KEYS = {"root_id", "relative_path", "entry_type", "mode", "bytes", "sha256"}
INDEX_ENTRY_KEYS = {"relative_path", "git_mode", "git_type", "git_oid", "bytes", "sha256", "materialized_mode"}
INDEX_KEYS = {"source_index_id", "schema_version", "epoch_id", "root_id", "role", "endpoint_anchors", "projection", "entries", "entry_count", "implicit_directory_count", "total_regular_bytes", "projected_git_tree", "root_tree_sha256"}
MANIFEST_KEYS = {"schema_version", "epoch_id", "root_id", "canonical_absolute_path", "device", "inode", "mount_id", "owner_uid", "root_mode", "complete", "source_index_id", "entries", "tree_entry_count", "regular_file_count", "implicit_directory_count", "total_regular_bytes", "root_tree_sha256", "protection"}
PROTECTION_KEYS = {"purpose", "created_exclusively", "single_writer_enforced", "read_only_enforced", "symlinks_absent", "hardlinks_absent", "published_atomically", "xattrs_absent", "acls_absent", "bsd_flags_absent", "resource_forks_absent", "special_files_absent", "casefold_collisions_absent", "empty_directories_absent", "extra_entries_absent"}
REFERENCE_KEYS = {"root_id", "source_index_id", "root_tree_sha256", "projected_git_tree"}
HANDOFF_KEYS = {"source_input_handoff_sha256", "schema_version", "epoch_id", "classification", "root_order", "sources"}


class ContractError(ValueError):
    """The contract or one of its frozen data values is invalid."""


def _exact(value: Any, keys: Set[str], label: str) -> Mapping[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ContractError("%s has the wrong exact key set" % label)
    return value


def _strict_string(value: Any, label: str) -> str:
    if type(value) is not str or not value or unicodedata.normalize("NFC", value) != value:
        raise ContractError("%s must be one nonempty NFC string" % label)
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ContractError("%s contains a surrogate" % label)
    return value


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum or value > INT64_MAX:
        raise ContractError("%s must be an integer >= %d" % (label, minimum))
    return value


def _hex(value: Any, expression: re.Pattern, label: str) -> str:
    if type(value) is not str or expression.fullmatch(value) is None:
        raise ContractError("%s is not one lowercase hexadecimal digest" % label)
    return value


def _relative_path(value: Any, label: str, allow_root: bool = False) -> str:
    text = _strict_string(value, label)
    if allow_root and text == ".":
        return text
    if "\\" in text or "\x00" in text or len(text.encode("utf-8")) > PATH_BYTES_MAX:
        raise ContractError("%s is not a bounded POSIX path" % label)
    pure = PurePosixPath(text)
    if pure.is_absolute() or pure.as_posix() != text or any(part in {"", ".", ".."} for part in pure.parts):
        raise ContractError("%s is not a canonical relative POSIX path" % label)
    return text


def _walk_json(value: Any, depth: int = 0, counter: Optional[List[int]] = None,
               maximum_string_bytes: int = CONTRACT_BYTES_MAX) -> None:
    if counter is None:
        counter = [0]
    counter[0] += 1
    if counter[0] > JSON_NODES_MAX or depth > JSON_DEPTH_MAX:
        raise ContractError("JSON depth or node ceiling exceeded")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if value < INT64_MIN or value > INT64_MAX:
            raise ContractError("JSON integer is outside signed 64-bit range")
        return
    if type(value) is str:
        if unicodedata.normalize("NFC", value) != value or any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise ContractError("JSON string is not NFC or contains a surrogate")
        if len(value) > maximum_string_bytes or len(value.encode("utf-8")) > maximum_string_bytes:
            raise ContractError("JSON string exceeds its fixed byte ceiling")
        return
    if type(value) is list:
        for item in value:
            _walk_json(item, depth + 1, counter, maximum_string_bytes)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or not key or unicodedata.normalize("NFC", key) != key or any(0xD800 <= ord(character) <= 0xDFFF for character in key):
                raise ContractError("JSON object key is invalid")
            if len(key) > maximum_string_bytes or len(key.encode("utf-8")) > maximum_string_bytes:
                raise ContractError("JSON object key exceeds its fixed byte ceiling")
            _walk_json(item, depth + 1, counter, maximum_string_bytes)
        return
    raise ContractError("JSON contains a forbidden value type")


def canonical_fragment_bytes(value: Any, maximum_bytes: int = CONTRACT_BYTES_MAX) -> bytes:
    if type(maximum_bytes) is not int or maximum_bytes < 1 or maximum_bytes > ROOT_MANIFEST_BYTES_MAX:
        raise TypeError("maximum_bytes is outside the fixed canonical-fragment ceiling")
    _walk_json(value, maximum_string_bytes=maximum_bytes)
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    result = bytearray()
    for text_chunk in encoder.iterencode(value):
        if len(text_chunk) > maximum_bytes:
            raise ContractError("canonical JSON fragment exceeds its fixed byte ceiling")
        chunk = text_chunk.encode("utf-8")
        if len(result) > maximum_bytes - len(chunk):
            raise ContractError("canonical JSON fragment exceeds its fixed byte ceiling")
        result.extend(chunk)
    return bytes(result)


def canonical_json_bytes(value: Any, maximum_bytes: int = CONTRACT_BYTES_MAX) -> bytes:
    if type(maximum_bytes) is not int or maximum_bytes < 2 or maximum_bytes > ROOT_MANIFEST_BYTES_MAX:
        raise TypeError("maximum_bytes is outside the fixed canonical-file ceiling")
    raw = canonical_fragment_bytes(value, maximum_bytes - 1) + b"\n"
    if len(raw) > maximum_bytes:
        raise ContractError("canonical JSON exceeds its fixed byte ceiling")
    return raw


def parse_canonical_json(raw: bytes, maximum_bytes: int = CONTRACT_BYTES_MAX) -> Any:
    if type(maximum_bytes) is not int or maximum_bytes < 2 or maximum_bytes > ROOT_MANIFEST_BYTES_MAX:
        raise TypeError("maximum_bytes is outside the fixed canonical-file ceiling")
    if type(raw) is not bytes or not raw or len(raw) > maximum_bytes:
        raise ContractError("JSON bytes are empty or oversized")
    def pairs(items: List[Tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ContractError("duplicate JSON key %r" % key)
            result[key] = value
        return result
    try:
        value = json.loads(
            raw.decode("utf-8", "strict"), object_pairs_hook=pairs,
            parse_float=lambda value: (_ for _ in ()).throw(ContractError("floating point is forbidden")),
            parse_constant=lambda value: (_ for _ in ()).throw(ContractError("nonfinite value is forbidden")),
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ContractError("invalid strict JSON: %s" % error) from error
    if canonical_json_bytes(value, maximum_bytes) != raw:
        raise ContractError("JSON is not canonical")
    return value


def _domain(name: str) -> bytes:
    return DOMAINS[name].replace("\\0", "\x00").encode("utf-8")


def _tree_entry(value: Any, expected_root: Optional[str] = None) -> Dict[str, Any]:
    item = _exact(value, TREE_ENTRY_KEYS, "root tree entry")
    root_id = _strict_string(item["root_id"], "tree root_id")
    if expected_root is not None and root_id != expected_root:
        raise ContractError("tree entry names a different root")
    kind = item["entry_type"]
    mode = _integer(item["mode"], "tree mode")
    if kind == "root":
        if item["relative_path"] != "" or mode != 0o555 or item["bytes"] != 0 or item["sha256"] is not None:
            raise ContractError("root tree entry differs from the frozen profile")
        path = ""
    else:
        path = _relative_path(item["relative_path"], "tree relative_path")
    if kind == "directory":
        if mode != 0o555 or item["bytes"] != 0 or item["sha256"] is not None:
            raise ContractError("directory tree entry differs from the frozen profile")
    elif kind == "regular":
        if mode not in {0o444, 0o555}:
            raise ContractError("regular tree entry differs from the frozen mode profile")
        size = _integer(item["bytes"], "tree bytes")
        if size > REGULAR_FILE_BYTES_MAX:
            raise ContractError("regular tree entry exceeds the byte cap")
        _hex(item["sha256"], HEX64, "tree SHA-256")
    elif kind != "root":
        raise ContractError("tree entry type is not permitted")
    return dict(item)


def derive_root_tree_sha256(entries: Sequence[Mapping[str, Any]]) -> str:
    if type(entries) is not list or not entries or len(entries) > 512:
        raise ContractError("root tree must be one nonempty list")
    normalized = [_tree_entry(item) for item in entries]
    root_id = normalized[0]["root_id"]
    if any(item["root_id"] != root_id for item in normalized):
        raise ContractError("root tree mixes root identifiers")
    if normalized[0] != {"root_id": root_id, "relative_path": "", "entry_type": "root", "mode": 0o555, "bytes": 0, "sha256": None}:
        raise ContractError("root tree does not begin with the exact root record")
    rest = normalized[1:]
    paths = [item["relative_path"] for item in rest]
    if paths != sorted(paths, key=lambda value: value.encode("utf-8")) or len(paths) != len(set(paths)):
        raise ContractError("root tree paths are not unique UTF-8-bytewise sorted")
    if len({path.casefold() for path in paths}) != len(paths):
        raise ContractError("root tree has a case-fold collision")
    if any(item["entry_type"] == "root" for item in rest):
        raise ContractError("root tree has more than one root record")
    files = {item["relative_path"] for item in rest if item["entry_type"] == "regular"}
    directories = {item["relative_path"] for item in rest if item["entry_type"] == "directory"}
    required: Set[str] = set()
    for path in files:
        parent = PurePosixPath(path).parent
        while parent.as_posix() != ".":
            required.add(parent.as_posix())
            parent = parent.parent
    if directories != required:
        raise ContractError("root tree directories are not the exact nonempty implicit closure")
    digest = hashlib.sha256(_domain("root_tree"))
    for item in normalized:
        raw = canonical_fragment_bytes(item, 16 << 10)
        digest.update(struct.pack(">Q", len(raw)))
        digest.update(raw)
    return digest.hexdigest()


def _git_tree_oid(entries: Sequence[Mapping[str, Any]]) -> str:
    root: Dict[str, Any] = {}
    for item in entries:
        path = item["relative_path"]
        cursor = root
        parts = path.split("/")
        for part in parts[:-1]:
            cursor = cursor.setdefault(part, {})
            if type(cursor) is not dict:
                raise ContractError("Git projection has a file/directory conflict")
        if parts[-1] in cursor:
            raise ContractError("Git projection has a duplicate path")
        cursor[parts[-1]] = (item["git_mode"], item["git_oid"])
    def emit(node: Mapping[str, Any]) -> str:
        rows: List[Tuple[bytes, bytes]] = []
        for name, value in node.items():
            encoded = name.encode("utf-8")
            if type(value) is dict:
                oid = emit(value)
                rows.append((encoded + b"/", b"40000 " + encoded + b"\0" + bytes.fromhex(oid)))
            else:
                mode, oid = value
                rows.append((encoded, mode.encode("ascii") + b" " + encoded + b"\0" + bytes.fromhex(oid)))
        body = b"".join(payload for _, payload in sorted(rows, key=lambda pair: pair[0]))
        return hashlib.sha1(b"tree " + str(len(body)).encode("ascii") + b"\0" + body).hexdigest()
    return emit(root)


def validate_source_index(value: Any) -> Dict[str, Any]:
    index = dict(_exact(value, INDEX_KEYS, "source index"))
    if index["schema_version"] != SOURCE_INDEX_SCHEMA or index["epoch_id"] != EPOCH_ID:
        raise ContractError("source index schema or epoch differs")
    root_id = _strict_string(index["root_id"], "source index root_id")
    if root_id not in {spec["root_id"] for spec in ROOT_SPECS}:
        raise ContractError("source index root_id is not registered")
    spec = next(item for item in ROOT_SPECS if item["root_id"] == root_id)
    if index["role"] != root_id:
        raise ContractError("source index role differs from its registered root")
    anchors = index["endpoint_anchors"]
    if type(anchors) is not list or not anchors:
        raise ContractError("source index endpoint anchors are invalid")
    for anchor in anchors:
        record = _exact(anchor, {"commit", "tree"}, "endpoint anchor")
        _hex(record["commit"], HEX40, "endpoint commit")
        _hex(record["tree"], HEX40, "endpoint tree")
    if anchors != spec["endpoint_anchors"]:
        raise ContractError("source index endpoint anchors differ from the frozen projection")
    projection = _exact(index["projection"], {"selection", "path_list_sha256", "manifest_bytes", "manifest_sha256"}, "projection")
    _strict_string(projection["selection"], "projection selection")
    _hex(projection["path_list_sha256"], HEX64, "projection path-list SHA")
    _integer(projection["manifest_bytes"], "projection manifest bytes")
    _hex(projection["manifest_sha256"], HEX64, "projection manifest SHA")
    if projection != {"selection": spec["selection"], "path_list_sha256": spec["path_list_sha256"], "manifest_bytes": spec["manifest_bytes"], "manifest_sha256": spec["manifest_sha256"]}:
        raise ContractError("source index projection identity differs")
    raw_entries = index["entries"]
    if type(raw_entries) is not list or not raw_entries or len(raw_entries) > 512:
        raise ContractError("source index entries are invalid")
    entries: List[Dict[str, Any]] = []
    for raw in raw_entries:
        item = dict(_exact(raw, INDEX_ENTRY_KEYS, "source index entry"))
        item["relative_path"] = _relative_path(item["relative_path"], "source entry path")
        if item["git_mode"] not in {"100644", "100755"} or item["git_type"] != "blob":
            raise ContractError("source entry Git mode or type is forbidden")
        _hex(item["git_oid"], HEX40, "source entry Git oid")
        size = _integer(item["bytes"], "source entry bytes")
        if size > REGULAR_FILE_BYTES_MAX:
            raise ContractError("source entry exceeds the byte cap")
        _hex(item["sha256"], HEX64, "source entry SHA-256")
        expected_mode = 0o444 if item["git_mode"] == "100644" else 0o555
        if item["materialized_mode"] != expected_mode:
            raise ContractError("source entry materialized mode differs")
        entries.append(item)
    paths = [item["relative_path"] for item in entries]
    if paths != sorted(paths, key=lambda value: value.encode("utf-8")) or len(set(paths)) != len(paths):
        raise ContractError("source index paths are not sorted and unique")
    if len({path.casefold() for path in paths}) != len(paths):
        raise ContractError("source index paths have a case-fold collision")
    if index["entry_count"] != len(entries) or index["total_regular_bytes"] != sum(item["bytes"] for item in entries):
        raise ContractError("source index counts differ")
    mode_counts: Dict[str, int] = {}
    for item in entries:
        mode_counts[item["git_mode"]] = mode_counts.get(item["git_mode"], 0) + 1
    if len(entries) != spec["entry_count"] or index["total_regular_bytes"] != spec["payload_bytes"] or mode_counts != spec["git_mode_counts"]:
        raise ContractError("source index inventory differs from the frozen projection")
    dirs: Set[str] = set()
    for path in paths:
        parent = PurePosixPath(path).parent
        while parent.as_posix() != ".":
            dirs.add(parent.as_posix())
            parent = parent.parent
    if index["implicit_directory_count"] != len(dirs):
        raise ContractError("source index implicit-directory count differs")
    if len(dirs) != spec["implicit_directory_count"]:
        raise ContractError("source index directory closure differs from the frozen projection")
    if 1 + len(dirs) + len(entries) != spec["tree_entry_count"]:
        raise ContractError("source index typed-tree count differs from the frozen projection")
    if _git_tree_oid(entries) != _hex(index["projected_git_tree"], HEX40, "projected Git tree"):
        raise ContractError("source index projected Git tree differs")
    if index["projected_git_tree"] != spec["projected_git_tree"] or index["root_tree_sha256"] != spec["root_tree_sha256"]:
        raise ContractError("source index tree identities differ from the frozen projection")
    tree_entries: List[Dict[str, Any]] = [
        {"root_id": root_id, "relative_path": "", "entry_type": "root", "mode": 0o555, "bytes": 0, "sha256": None}
    ]
    tree_entries.extend(
        {"root_id": root_id, "relative_path": path, "entry_type": "directory", "mode": 0o555, "bytes": 0, "sha256": None}
        for path in sorted(dirs, key=lambda value: value.encode("utf-8"))
    )
    tree_entries.extend(
        {"root_id": root_id, "relative_path": item["relative_path"], "entry_type": "regular", "mode": item["materialized_mode"], "bytes": item["bytes"], "sha256": item["sha256"]}
        for item in entries
    )
    tree_entries = [tree_entries[0]] + sorted(tree_entries[1:], key=lambda item: item["relative_path"].encode("utf-8"))
    if derive_root_tree_sha256(tree_entries) != _hex(index["root_tree_sha256"], HEX64, "source root tree SHA"):
        raise ContractError("source index root tree differs")
    expected_id = derive_source_index_id(index, validate=False)
    if index["source_index_id"] != expected_id:
        raise ContractError("source index id differs")
    return index


def derive_source_index_id(value: Mapping[str, Any], validate: bool = True) -> str:
    index = dict(value)
    if validate:
        validate_source_index(index)
    if set(index) != INDEX_KEYS:
        raise ContractError("source index has the wrong exact key set")
    index.pop("source_index_id")
    return hashlib.sha256(_domain("source_index") + canonical_fragment_bytes(index, SOURCE_INDEX_BYTES_MAX)).hexdigest()


def derive_root_manifest_sha256(value: Mapping[str, Any]) -> str:
    if set(value) != MANIFEST_KEYS:
        raise ContractError("root manifest has the wrong exact key set")
    entries = value.get("entries")
    if type(entries) is not list or not entries or len(entries) > 512:
        raise ContractError("root manifest entry list exceeds its fixed cap")
    return hashlib.sha256(_domain("root_manifest") + canonical_fragment_bytes(value, ROOT_MANIFEST_BYTES_MAX)).hexdigest()


def _canonical_absolute_path(value: Any, label: str) -> str:
    text = _strict_string(value, label)
    if (len(text.encode("utf-8")) > PATH_BYTES_MAX or "\x00" in text or "\\" in text
            or not text.startswith("/") or text.startswith("//")):
        raise ContractError("%s is not a bounded canonical absolute path" % label)
    pure = PurePosixPath(text)
    if not pure.is_absolute() or pure.as_posix() != text or os.path.normpath(text) != text:
        raise ContractError("%s is not a bounded canonical absolute path" % label)
    return text


def validate_root_manifest(value: Any, source_index: Mapping[str, Any], require_production: bool = True) -> Dict[str, Any]:
    manifest = dict(_exact(value, MANIFEST_KEYS, "root manifest"))
    index = validate_source_index(source_index)
    if manifest["schema_version"] != ROOT_MANIFEST_SCHEMA or manifest["epoch_id"] != EPOCH_ID:
        raise ContractError("root manifest schema or epoch differs")
    if manifest["root_id"] != index["root_id"] or manifest["source_index_id"] != index["source_index_id"]:
        raise ContractError("root manifest differs from its source index")
    _canonical_absolute_path(manifest["canonical_absolute_path"], "root absolute path")
    for key in ("device", "inode", "mount_id", "owner_uid"):
        _integer(manifest[key], "root manifest %s" % key)
    if manifest["root_mode"] != 0o555 or manifest["complete"] is not True:
        raise ContractError("root manifest mode or completeness differs")
    entries = manifest["entries"]
    if type(entries) is not list or not entries or len(entries) > 512:
        raise ContractError("root manifest entries must be a list")
    normalized = [_tree_entry(item, index["root_id"]) for item in entries]
    digest = derive_root_tree_sha256(normalized)
    if digest != index["root_tree_sha256"] or manifest["root_tree_sha256"] != digest:
        raise ContractError("root manifest tree differs from source index")
    regular = [item for item in normalized if item["entry_type"] == "regular"]
    directories = [item for item in normalized if item["entry_type"] == "directory"]
    if manifest["tree_entry_count"] != len(normalized) or manifest["regular_file_count"] != len(regular):
        raise ContractError("root manifest entry counts differ")
    if manifest["implicit_directory_count"] != len(directories) or manifest["total_regular_bytes"] != sum(item["bytes"] for item in regular):
        raise ContractError("root manifest directory or byte counts differ")
    protection = _exact(manifest["protection"], PROTECTION_KEYS, "root protection")
    _strict_string(protection["purpose"], "root protection purpose")
    for key in PROTECTION_KEYS - {"purpose"}:
        if type(protection[key]) is not bool:
            raise ContractError("root protection booleans are not exact")
    absence_keys = {"symlinks_absent", "hardlinks_absent", "xattrs_absent", "acls_absent", "bsd_flags_absent", "resource_forks_absent", "special_files_absent", "casefold_collisions_absent", "empty_directories_absent", "extra_entries_absent"}
    if any(protection[key] is not True for key in absence_keys):
        raise ContractError("root protection permits unbound metadata or entries")
    if protection["purpose"] == "production-protected":
        if any(protection[key] is not True for key in PROTECTION_KEYS - {"purpose"}):
            raise ContractError("production-protected root has an incomplete protection predicate")
    elif protection["purpose"] == "diagnostic-fixture":
        expected_diagnostic = {
            "created_exclusively": True, "single_writer_enforced": False,
            "read_only_enforced": False, "published_atomically": False,
        }
        if any(protection[key] is not value for key, value in expected_diagnostic.items()):
            raise ContractError("diagnostic root protection profile differs")
    else:
        raise ContractError("root protection purpose is invalid")
    if require_production and protection["purpose"] != "production-protected":
        raise ContractError("diagnostic or incompletely protected root is not production eligible")
    return manifest


def derive_source_input_handoff_sha256(value: Mapping[str, Any]) -> str:
    _validate_source_input_handoff_shape(value, verify_id=False)
    payload = dict(value)
    payload.pop("source_input_handoff_sha256")
    return hashlib.sha256(_domain("source_input_handoff") + canonical_fragment_bytes(payload, SOURCE_INDEX_BYTES_MAX)).hexdigest()


def _validate_source_input_handoff_shape(value: Any, verify_id: bool = True) -> Dict[str, Any]:
    handoff = dict(_exact(value, HANDOFF_KEYS, "source input handoff"))
    if handoff["schema_version"] != HANDOFF_SCHEMA or handoff["epoch_id"] != EPOCH_ID:
        raise ContractError("source handoff schema or epoch differs")
    classification = handoff["classification"]
    expected = {"build_authorized": False, "capture_complete": False, "execution_authorized": False, "scientific_evidence": False}
    if classification != expected:
        raise ContractError("source handoff classification is authorizing or malformed")
    sources = handoff["sources"]
    if type(sources) is not list or [item.get("root_id") if type(item) is dict else None for item in sources] != [spec["root_id"] for spec in ROOT_SPECS]:
        raise ContractError("source handoff root order differs")
    for source, spec in zip(sources, ROOT_SPECS):
        item = _exact(source, REFERENCE_KEYS, "source reference")
        _hex(item["source_index_id"], HEX64, "source reference index id")
        if item["root_tree_sha256"] != spec["root_tree_sha256"] or item["projected_git_tree"] != spec["projected_git_tree"]:
            raise ContractError("source reference differs from frozen projection")
    if handoff["root_order"] != [spec["root_id"] for spec in ROOT_SPECS]:
        raise ContractError("source handoff explicit root order differs")
    _hex(handoff["source_input_handoff_sha256"], HEX64, "source handoff id")
    if verify_id and handoff["source_input_handoff_sha256"] != derive_source_input_handoff_sha256(handoff):
        raise ContractError("source handoff id differs")
    return handoff


def validate_source_input_handoff(value: Any, source_indexes: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    handoff = _validate_source_input_handoff_shape(value)
    if type(source_indexes) is not list or len(source_indexes) != 3:
        raise ContractError("production handoff requires all three verified source indexes")
    indexes = [validate_source_index(item) for item in source_indexes]
    if [item["root_id"] for item in indexes] != [spec["root_id"] for spec in ROOT_SPECS]:
        raise ContractError("production handoff source-index order differs")
    for reference, index in zip(handoff["sources"], indexes):
        if reference["source_index_id"] != index["source_index_id"]:
            raise ContractError("production handoff source reference differs from verified index")
    return handoff


def _validate_contract_shape(value: Any) -> Dict[str, Any]:
    contract = dict(_exact(value, TOP_KEYS, "contract"))
    if contract["schema_version"] != CONTRACT_SCHEMA or contract["epoch_id"] != EPOCH_ID:
        raise ContractError("contract schema or epoch differs")
    if contract["classification"] != CLASSIFICATION or contract["scope"] != SCOPE:
        raise ContractError("contract classification or scope differs")
    if contract["current_blockers"] != BLOCKERS:
        raise ContractError("contract blockers differ")
    if contract["hash_domains"] != DOMAINS or contract["hash_formulas"] != FORMULAS:
        raise ContractError("contract hash domains or formulas differ")
    canonical = {"allowed_values": "null exact booleans signed 64-bit integers NFC strings arrays and objects", "duplicate_keys_permitted": False, "file_termination": "one LF byte", "floating_point_permitted": False, "fragment_termination": "no trailing byte", "member_order": "ascending Unicode code-point order", "nonfinite_numbers_permitted": False, "serialization": "UTF-8 without BOM; ensure_ascii=false; separators comma and colon with no whitespace", "strings": "NFC Unicode with no surrogate code points"}
    if contract["canonical_json"] != canonical:
        raise ContractError("canonical JSON profile differs")
    bindings = contract["design_bindings"]
    if bindings != {
        "integration_anchor": {"commit": "a138ebfb13fe9ba33d723a5ed4f7f0a931eb3999", "commit_object_bytes": 231, "commit_object_sha256": "b00abd08e9971b34ae58243c264c54610655b65f9f52a5219ddde258cc1f88ae", "parent": "0c2bbf405a23755bec5dd23614598f25d3bddb0f", "tree": "f2ba2ff2faef26b917992745110e9306ad773fd2"},
        "integration_record": {"bytes": 13_587, "path": "docs/simulator/physics_superbuild_integration_record_epoch_1_v1.json", "schema_version": "dancers-planar-physics-superbuild-integration-record-v1", "sha256": "31b7b9a8b024d41c060df72e2791a0ff640a3ebd0d792649fb2f7551aa315010"},
        "protected_build_capture_contract": {"bytes": 24_617, "path": "docs/simulator/physics_protected_build_capture_contract_v2.json", "schema_version": "dancers-planar-physics-protected-build-capture-contract-v2", "sha256": "5d96db2931644581644c7117f95dbcc94b8f155e7aac74b77a53cfd381be3b1a"},
    }:
        raise ContractError("design bindings differ")
    roots = contract["projected_roots"]
    if type(roots) is not list or len(roots) != 3:
        raise ContractError("projected root list differs")
    for value_root, expected in zip(roots, ROOT_SPECS):
        root = _exact(value_root, {"endpoint_anchors", "entry_count", "git_mode_counts", "implicit_directory_count", "manifest_bytes", "manifest_sha256", "path_list_sha256", "payload_bytes", "projected_git_tree", "root_id", "root_tree_sha256", "selection", "tree_entry_count"}, "projected root")
        for key, expected_value in expected.items():
            if root[key] != expected_value:
                raise ContractError("projected root %s field %s differs" % (expected["root_id"], key))
        _strict_string(root["selection"], "projected root selection")
    caps = contract["hard_caps"]
    expected_caps = {"caller_override_permitted": False, "contract_bytes_max": 1 << 20, "json_depth_max": 32, "json_nodes_max": 200_000, "path_bytes_max": 1024, "projected_entries_exact": 477, "projected_payload_bytes_exact": 14_595_556, "regular_file_bytes_max": 4 << 20, "root_count_exact": 3, "root_entries_max": 512, "root_manifest_bytes_max": 2 << 20, "source_index_bytes_max": 2 << 20, "total_projected_bytes_max": 16 << 20, "typed_tree_entries_exact": 548}
    if caps != expected_caps:
        raise ContractError("hard caps differ")
    policy = contract["materialization_policy"]
    if policy.get("production_materialization_permitted") is not False or policy.get("official_namespace_permitted") is not False:
        raise ContractError("production or official materialization is permitted")
    if policy.get("independent_read_only_mount_or_equivalent_required_for_production") is not True:
        raise ContractError("independent read-only enforcement is not required")
    if policy.get("git_mode_to_materialized_mode") != {"100644": 0o444, "100755": 0o555} or policy.get("source_root_mode") != 0o555 or policy.get("directory_materialized_mode") != 0o555:
        raise ContractError("materialized mode profile differs")
    if "atomic no-replace directory publication has no ordinary rename fallback" not in policy.get("descriptor_requirements", []):
        raise ContractError("descriptor-safe no-replace publication is not frozen")
    if "regular leaves are opened descriptor-relatively with O_WRONLY O_CREAT O_EXCL O_CLOEXEC and O_NOFOLLOW" not in policy.get("descriptor_requirements", []):
        raise ContractError("descriptor-safe writable exclusive leaf creation is not frozen")
    if "the destination parent directory is fsynced after atomic no-replace publication and before final-name reauthentication or manifest issuance" not in policy.get("descriptor_requirements", []):
        raise ContractError("post-publication parent durability is not frozen")
    if "owner-controlled 0444 0555 modes do not protect against same-euid chmod or mutation and never establish production immutability" not in policy.get("requirements", []):
        raise ContractError("same-euid mode limitation is not disclosed")
    handoff = contract["handoff"]
    if handoff.get("action_id_formula_frozen") is not False or handoff.get("action_ids_present") is not False:
        raise ContractError("unfrozen action identities were promoted")
    if handoff.get("handoff_is_capture_independent_and_prior") is not True or handoff.get("source_index_entry_count_semantics") != "regular-file entry count only" or not str(handoff.get("capture_root_snapshot_entry_count_semantics", "")).startswith("typed tree_entry_count"):
        raise ContractError("handoff ordering or entry-count semantics differ")
    if handoff.get("root_order") != [spec["root_id"] for spec in ROOT_SPECS] or handoff.get("standalone_cmake_digest_arguments_are_correlation_only") is not True or handoff.get("standalone_cmake_requires_prior_descriptor_verification") is not True:
        raise ContractError("source handoff order or CMake correlation boundary differs")
    schemas = contract["schemas"]
    expected_schema_keys = {"endpoint_anchor", "file_identity", "projection", "root_manifest", "root_protection", "root_tree_entry", "source_handoff_classification", "source_index", "source_index_entry", "source_input_handoff", "source_reference"}
    if type(schemas) is not dict or set(schemas) != expected_schema_keys:
        raise ContractError("schema registry differs")
    exact_sets = {
        "file_identity": FILE_IDENTITY_KEYS, "root_manifest": MANIFEST_KEYS,
        "root_protection": PROTECTION_KEYS, "root_tree_entry": TREE_ENTRY_KEYS,
        "source_index": INDEX_KEYS, "source_index_entry": INDEX_ENTRY_KEYS,
        "source_input_handoff": HANDOFF_KEYS, "source_reference": REFERENCE_KEYS,
        "endpoint_anchor": {"commit", "tree"},
        "projection": {"selection", "path_list_sha256", "manifest_bytes", "manifest_sha256"},
        "source_handoff_classification": {"build_authorized", "capture_complete", "execution_authorized", "scientific_evidence"},
    }
    for name, keys in exact_sets.items():
        record = _exact(schemas[name], {"exact_keys", "field_types", "schema_version"}, "schema %s" % name)
        if set(record["exact_keys"]) != keys or len(record["exact_keys"]) != len(keys):
            raise ContractError("schema %s exact keys differ" % name)
        if type(record["field_types"]) is not dict or set(record["field_types"]) != keys or any(type(item) is not str or not item for item in record["field_types"].values()):
            raise ContractError("schema %s field types differ" % name)
        _strict_string(record["schema_version"], "schema version")
    if hashlib.sha256(canonical_fragment_bytes(schemas)).hexdigest() != SCHEMAS_SHA256:
        raise ContractError("schema versions or field types differ from the frozen registry")
    verdict = contract["verdict_contract"]
    if verdict != {"required_classification": VERDICT_CLASSIFICATION, "schema_version": VERDICT_SCHEMA}:
        raise ContractError("verdict contract differs")
    return contract


def validate_contract_value(value: Any) -> Dict[str, Any]:
    _walk_json(value)
    return _validate_contract_shape(value)


def _read_contract(path: Path) -> bytes:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    canonical = Path(os.path.abspath(str(candidate)))
    if os.path.realpath(str(canonical)) != str(canonical):
        raise ContractError("contract path has a symlink ancestor")
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ContractError("O_NOFOLLOW is unavailable")
    fd = os.open(str(canonical), os.O_RDONLY | os.O_CLOEXEC | nofollow | getattr(os, "O_NONBLOCK", 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_uid != os.geteuid() or before.st_mode & 0o022:
            raise ContractError("contract must be an euid-owned safe single-link regular file")
        if before.st_size < 1 or before.st_size > CONTRACT_BYTES_MAX:
            raise ContractError("contract size is invalid")
        chunks: List[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(fd, min(1 << 20, remaining))
            if not chunk:
                raise ContractError("contract ended early")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise ContractError("contract grew while read")
        after = os.fstat(fd)
        named = os.stat(str(canonical), follow_symlinks=False)
        identity = lambda item: (item.st_dev, item.st_ino, item.st_mode, item.st_nlink, item.st_uid, item.st_size, item.st_mtime_ns, item.st_ctime_ns)
        if identity(before) != identity(after) or identity(after) != identity(named):
            raise ContractError("contract changed while read")
        return b"".join(chunks)
    finally:
        os.close(fd)


def validate_contract_file(path: Path) -> Dict[str, Any]:
    raw = _read_contract(path)
    if len(raw) != CONTRACT_BYTES or hashlib.sha256(raw).hexdigest() != CONTRACT_SHA256:
        raise ContractError("contract bytes differ from the independently frozen identity")
    value = parse_canonical_json(raw)
    validate_contract_value(value)
    return {
        "schema_version": VERDICT_SCHEMA,
        "epoch_id": EPOCH_ID,
        "valid": True,
        "contract_sha256": CONTRACT_SHA256,
        "classification": dict(VERDICT_CLASSIFICATION),
        "production_roots_materialized": False,
        "build_input_handoff_present": False,
        "action_plan_present": False,
        "blockers": list(BLOCKERS),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", nargs="?", default="docs/simulator/physics_protected_source_roots_contract_v2.json")
    arguments = parser.parse_args(argv)
    try:
        verdict = validate_contract_file(Path(arguments.contract))
    except (ContractError, OSError) as error:
        print("protected source-roots contract rejected: %s" % error, file=sys.stderr)
        return 1
    sys.stdout.buffer.write(canonical_json_bytes(verdict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
