#!/usr/bin/env python3
"""Data-only verifier for prospective planar-physics build provenance v1.

The verifier authenticates an already captured, independently pinned package.
It never invokes Git, CMake, a compiler, a test, a runner, or the simulator and
it never creates an official provenance, plan, identity, receipt, or result.

The frozen JSON contract deliberately specifies several field lists without a
complete nested schema.  This module supplies the strict implementation profile
used by the verifier.  In particular:

* an action ID is ``SHA256(ACTION_ID_DOMAIN || canonical(action without id))``;
* the action ledger hashes the ordered, complete action records;
* actions bind an immutable network session ID, while the isolation receipt
  binds the final action-ledger digest (there is no receipt/ledger cycle); and
* the expected bytes and SHA-256 values for both the provenance and trust-root
  manifests are arguments obtained outside the package being verified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import struct
import sys
import unicodedata
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = "dancers-planar-physics-build-provenance-v1"
TRUST_ROOT_SCHEMA = "dancers-planar-physics-build-trust-root-v1"
NETWORK_RECEIPT_SCHEMA = "dancers-planar-physics-network-isolation-receipt-v1"
ROOT_TREE_SCHEMA = "dancers-planar-physics-root-tree-v1"
SOURCE_IDENTITY_SCHEMA = "dancers-planar-physics-source-identity-v1"
OBJECT_GRAPH_SCHEMA = "dancers-planar-physics-object-graph-v1"
ACTION_LEDGER_SCHEMA = "dancers-planar-physics-action-ledger-v1"
EPOCH_ID = "simulator-demo-v1-physics-stress-test-epoch-1"

ACTION_ID_DOMAIN = b"dancers-planar-physics-build-action-id-v1\0"
ACTION_LEDGER_DOMAIN = b"dancers-planar-physics-build-action-ledger-v1\0"
TREE_DOMAIN = b"dancers-planar-physics-file-tree-v1\0"
ENVIRONMENT_DOMAIN = b"dancers-planar-physics-build-environment-v1\0"
DEPENDENCY_CLOSURE_DOMAIN = b"dancers-planar-physics-dependency-closure-v1\0"
OBJECT_ID_DOMAIN = b"dancers-planar-physics-object-id-v1\0"
OFFLINE_TREE_DOMAIN = b"dancers-offline-build-input-tree-v1\0"
GIT_BLOB_PREFIX = b"blob "
GIT_TREE_PREFIX = b"tree "

MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_TRUST_ROOT_BYTES = 2 * 1024 * 1024
MAX_IDENTITY_FILE_BYTES = 16 * 1024 * 1024
MAX_REGULAR_FILE_BYTES = 512 * 1024 * 1024
MAX_TREE_FILES = 200_000
MAX_TREE_BYTES = 8 * 1024 * 1024 * 1024
# Embedded v1 ledgers cannot approach the contract's theoretical process count
# while remaining within the 16 MiB canonical manifest ceiling.  A future
# supporting-manifest contract is required before this cap can be raised.
MAX_ACTIONS = 10_000
MAX_ACTION_IO_BYTES = 4 * 1024 * 1024
MAX_PATH_BYTES = 1_024
MAX_ARG_BYTES = 32_768
MAX_ARGV_ITEMS = 8_192
MAX_ENVIRONMENTS = 64
MAX_PACKAGES = 4_096
MAX_OBJECTS = 100_000
MAX_PRODUCTS = 4
MAX_JSON_DEPTH = 32

LIMITS = {
    "action_io_bytes_max": MAX_ACTION_IO_BYTES,
    "actions_max": MAX_ACTIONS,
    "argv_items_max": MAX_ARGV_ITEMS,
    "identity_file_bytes_max": MAX_IDENTITY_FILE_BYTES,
    "json_depth_max": MAX_JSON_DEPTH,
    "manifest_bytes_max": MAX_MANIFEST_BYTES,
    "objects_max": MAX_OBJECTS,
    "packages_max": MAX_PACKAGES,
    "path_bytes_max": MAX_PATH_BYTES,
    "regular_file_bytes_max": MAX_REGULAR_FILE_BYTES,
    "tree_files_max": MAX_TREE_FILES,
    "tree_regular_bytes_max": MAX_TREE_BYTES,
    "trust_root_bytes_max": MAX_TRUST_ROOT_BYTES,
}
OFFLINE_V2_LIMITS = {
    "archive_bytes_max": 4_194_304,
    "archive_member_bytes_max": 2_097_152,
    "archive_members_max": 4_096,
    "archive_regular_bytes_max": 33_554_432,
    "archive_tar_stream_bytes_max": 25_165_824,
    "cmake_bytes_max": 1_048_576,
    "license_bytes_max": 1_048_576,
    "manifest_bytes_max": 262_144,
    "path_bytes_max": 512,
    "predecessor_bytes_max": 1_048_576,
    "tree_file_bytes_max": 8_388_608,
    "tree_files_max": 4_096,
    "tree_regular_bytes_max": 16_777_216,
}

TOP_KEYS = {
    "schema_version",
    "epoch_id",
    "classification",
    "identity_bindings",
    "roots",
    "build_policy",
    "environments",
    "toolchain",
    "sdk",
    "dependencies",
    "action_ledger",
    "network_isolation",
    "object_graph",
    "common_objects",
    "products",
}
CLASSIFICATION = {
    "execution_authorized": False,
    "outcome_independent": True,
    "registered_scientific_run": False,
    "scientific_evidence": False,
    "scientific_evidence_eligible": False,
}
BUILD_POLICY = {
    "architectures": ["arm64"],
    "build_type": "Release",
    "common_runtime_artifact_root_required": True,
    "compiler_cache_permitted": False,
    "fresh_empty_build_directories_required": True,
    "network_access_permitted": False,
    "registered_scientific_process_permitted": False,
    "source_worktrees_clean_and_remote_free": True,
    "uncontrolled_ambient_environment_permitted": False,
}
FILE_IDENTITY_KEYS = {
    "root_id",
    "relative_path",
    "file_type",
    "mode",
    "bytes",
    "sha256",
}
ROOT_KEYS = {
    "root_id",
    "canonical_absolute_path",
    "owner_uid",
    "mode",
    "tree_manifest",
    "entry_count",
    "total_regular_bytes",
    "tree_sha256",
}
REQUIRED_ROOT_IDS = {
    "repository",
    "runtime-artifact-root",
    "reference-source",
    "optimized-source",
    "reference-build",
    "optimized-build",
    "toolchain-prefix",
    "sdk-root",
    "private-home",
    "private-tmp",
}
PRIVATE_ROOT_IDS = {
    "reference-build",
    "optimized-build",
    "private-home",
    "private-tmp",
}
REQUIRED_IDENTITY_BINDINGS = {
    "build_provenance_contract",
    "execution_design",
    "physics_registry",
    "cmake_lists",
    "pixi_toml",
    "pixi_lock",
    "build_trust_root",
    "reference_source_identity",
    "optimized_source_identity",
    "kinematic_fixture",
    "kinematic_expected_golden_digest",
    "object_split_contract",
    "build_executor",
    "action_wrapper",
    "tree_hasher",
    "macho_parser",
    "provenance_verifier",
    "offline_build_inputs_manifest",
    "offline_build_inputs_verifier",
}
TRUST_IDENTITY_NAMES = {
    "provenance_verifier",
    "hermetic_build_executor",
    "action_wrapper",
    "tree_and_package_closure_generator",
    "macho_parser",
    "network_guard",
    "network_policy",
    "permitted_host_os_mechanism",
    "canonicalization_and_size_policy",
}
TOOL_ROLES = {
    "cmake",
    "ninja",
    "c-driver",
    "cxx-driver",
    "compiler-frontend",
    "linker",
    "archiver",
    "ranlib",
    "nm",
    "strip",
}
TOOL_TREE_ROLES = {
    "pixi-package-archives",
    "pixi-extracted-prefix",
    "cmake-module-tree",
    "compiler-resource-tree",
    "libcxx-headers-and-libraries",
    "compiler-rt",
    "libomp",
}
REQUIRED_ENVIRONMENT_VARIABLES = {
    "PATH",
    "HOME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "TZ",
    "SOURCE_DATE_EPOCH",
    "ZERO_AR_DATE",
    "CC",
    "CXX",
    "SDKROOT",
    "MACOSX_DEPLOYMENT_TARGET",
    "OMP_NUM_THREADS",
    "OMP_DYNAMIC",
    "OMP_WAIT_POLICY",
}
ALLOWED_ACTION_KINDS = {
    "configure",
    "compiler-probe",
    "compile",
    "aggregate-object",
    "archive",
    "link",
}
ACTION_KEYS = {
    "sequence_index",
    "action_id",
    "kind",
    "method",
    "target",
    "cwd_root_id",
    "cwd_relative_path",
    "executable",
    "argv",
    "environment_id",
    "stdin",
    "response_files",
    "declared_inputs",
    "discovered_inputs",
    "outputs",
    "exit_code",
    "stdout",
    "stderr",
    "network_session_id",
}
ACTION_KEYS_WITHOUT_ID = ACTION_KEYS - {"action_id"}
OBJECT_KEYS = {
    "object_id",
    "role",
    "method",
    "source",
    "producer_action_id",
    "compile_argv_sha256",
    "environment_id",
    "dependency_closure_sha256",
    "object",
    "sharing_scope",
}
PRODUCT_KEYS = {
    "method",
    "role",
    "binary",
    "link_action_id",
    "ordered_object_ids",
    "ordered_library_inputs",
    "macho_load_commands",
    "resolved_non_system_dynamic_inputs",
    "observer_mode",
    "runtime_artifact_root",
}
PRODUCT_KEYS_EXPECTED = {
    ("reference", "fidelity"),
    ("reference", "timing"),
    ("optimized", "fidelity"),
    ("optimized", "timing"),
}
COMMON_OBJECT_ROLES = {
    "physics-engine",
    "controller-adapter",
    "artifact-loader",
    "simulator-bridge",
    "sha256",
}
FORBIDDEN_BINDING_FRAGMENTS = {
    "test_attestation",
    "test-attestation",
    "execution_identity",
    "execution-identity",
    "scientific_result",
    "scientific-result",
    "phase_receipt",
    "phase-receipt",
}


class VerificationError(ValueError):
    """A strict provenance-contract violation."""


def canonical_json_bytes(value: Any) -> bytes:
    _enforce_json_depth(value, "canonical JSON")
    try:
        raw = (
            json.dumps(
                value,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError, UnicodeError) as exc:
        raise VerificationError(f"value is not bounded finite canonical JSON: {exc}") from exc
    if len(raw) > MAX_MANIFEST_BYTES:
        raise VerificationError("canonical JSON exceeds the verifier's 16 MiB ceiling")
    return raw


def _enforce_json_depth(value: Any, label: str) -> None:
    pending: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while pending:
        current, depth = pending.pop()
        nodes += 1
        if nodes > 2_000_000:
            raise VerificationError(f"{label} exceeds the JSON node bound")
        if depth > MAX_JSON_DEPTH:
            raise VerificationError(f"{label} exceeds maximum JSON depth {MAX_JSON_DEPTH}")
        if type(current) is dict:
            pending.extend((child, depth + 1) for child in current.values())
        elif type(current) is list:
            pending.extend((child, depth + 1) for child in current)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _len_prefixed(payload: bytes) -> bytes:
    return len(payload).to_bytes(8, "big") + payload


def action_id(action_without_action_id: Mapping[str, Any]) -> str:
    value = dict(action_without_action_id)
    if set(value) != ACTION_KEYS_WITHOUT_ID:
        raise VerificationError("action_without_action_id has the wrong fields")
    return sha256_bytes(ACTION_ID_DOMAIN + canonical_json_bytes(value))


def action_ledger_sha256(actions: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    digest.update(ACTION_LEDGER_DOMAIN)
    for action in actions:
        payload = canonical_json_bytes(dict(action))
        digest.update(_len_prefixed(payload))
    return digest.hexdigest()


def environment_id(variables: Mapping[str, str]) -> str:
    return sha256_bytes(
        ENVIRONMENT_DOMAIN + canonical_json_bytes({"variables": dict(variables)})
    )


def dependency_closure_sha256(identities: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    digest.update(DEPENDENCY_CLOSURE_DOMAIN)
    for identity in identities:
        digest.update(_len_prefixed(canonical_json_bytes(dict(identity))))
    return digest.hexdigest()


def _reject_constant(value: str) -> None:
    raise VerificationError(f"non-standard JSON constant {value!r}")


def _object_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VerificationError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _parse_json_document(raw: bytes, label: str, *, require_canonical: bool) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VerificationError(f"{label} is not UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        raise VerificationError(f"{label} is not strict JSON: {exc}") from exc
    _enforce_json_depth(value, label)
    try:
        encoded = canonical_json_bytes(value)
    except (TypeError, ValueError) as exc:
        raise VerificationError(f"{label} is not canonical JSON data") from exc
    if require_canonical and encoded != raw:
        raise VerificationError(f"{label} is not canonical JSON")
    return value


def _parse_json(raw: bytes, label: str) -> Any:
    return _parse_json_document(raw, label, require_canonical=True)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise VerificationError(f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> list[Any]:
    if type(value) is not list:
        raise VerificationError(f"{label} must be an array")
    return value


def _string(value: Any, label: str, *, nonempty: bool = True) -> str:
    if type(value) is not str or (nonempty and not value):
        raise VerificationError(f"{label} must be a{' nonempty' if nonempty else ''} string")
    if "\x00" in value or unicodedata.normalize("NFC", value) != value:
        raise VerificationError(f"{label} is not canonical Unicode text")
    return value


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise VerificationError(f"{label} must be an integer >= {minimum}")
    return value


def _boolean(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise VerificationError(f"{label} must be a boolean")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise VerificationError(
            f"{label} keys differ: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _same_json_type_and_value(actual: Any, expected: Any) -> bool:
    """Compare JSON values without Python's bool/int/float aliasing."""
    return canonical_json_bytes(actual) == canonical_json_bytes(expected)


def _sha256(value: Any, label: str) -> str:
    digest = _string(value, label)
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise VerificationError(f"{label} must be lowercase SHA-256 hex")
    return digest


def _sha1(value: Any, label: str) -> str:
    digest = _string(value, label)
    if not re.fullmatch(r"[0-9a-f]{40}", digest):
        raise VerificationError(f"{label} must be lowercase SHA-1 hex")
    return digest


def _bounded_text(value: Any, label: str, limit: int = MAX_ACTION_IO_BYTES) -> str:
    text = _string(value, label, nonempty=False)
    if len(text.encode("utf-8")) > limit:
        raise VerificationError(f"{label} exceeds its byte bound")
    return text


def _relative_path(value: Any, label: str, *, allow_dot: bool = False) -> str:
    path = _string(value, label)
    if path == "." and allow_dot:
        return path
    if "\\" in path or len(path.encode("utf-8")) > MAX_PATH_BYTES:
        raise VerificationError(f"{label} is not a bounded POSIX relative path")
    pure = PurePosixPath(path)
    if (
        pure.is_absolute()
        or not pure.parts
        or pure.as_posix() != path
        or any(part in ("", ".", "..") for part in pure.parts)
    ):
        raise VerificationError(f"{label} is not a canonical relative path")
    return path


def _absolute_path(value: Any, label: str) -> str:
    path = _string(value, label)
    if len(path.encode("utf-8")) > 4 * MAX_PATH_BYTES:
        raise VerificationError(f"{label} exceeds the path bound")
    if not os.path.isabs(path) or os.path.normpath(path) != path:
        raise VerificationError(f"{label} is not a normalized absolute path")
    if os.path.realpath(path) != path:
        raise VerificationError(f"{label} is not canonical/no-follow")
    return path


def _casefold_unique(paths: Iterable[str], label: str) -> None:
    seen: dict[str, str] = {}
    for path in paths:
        folded = unicodedata.normalize("NFC", path).casefold()
        if folded in seen:
            raise VerificationError(
                f"{label} has casefold-colliding paths {seen[folded]!r} and {path!r}"
            )
        seen[folded] = path


def _no_file_ancestor_conflicts(paths: Iterable[str], label: str) -> None:
    values = set(paths)
    for path in values:
        parts = PurePosixPath(path).parts
        for index in range(1, len(parts)):
            ancestor = PurePosixPath(*parts[:index]).as_posix()
            if ancestor in values:
                raise VerificationError(
                    f"{label} has file/ancestor conflict {ancestor!r} vs {path!r}"
                )


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _read_fd(fd: int, expected_bytes: int, limit: int, label: str) -> bytes:
    if expected_bytes > limit:
        raise VerificationError(f"{label} exceeds its byte bound")
    chunks: list[bytes] = []
    remaining = expected_bytes
    while remaining:
        chunk = os.read(fd, min(remaining, 1024 * 1024))
        if not chunk:
            raise VerificationError(f"{label} ended before its declared size")
        chunks.append(chunk)
        remaining -= len(chunk)
    if os.read(fd, 1):
        raise VerificationError(f"{label} grew while it was read")
    return b"".join(chunks)


def _open_regular_absolute(path: str, limit: int, label: str) -> tuple[bytes, os.stat_result]:
    canonical = _absolute_path(path, f"{label} path")
    components = PurePosixPath(canonical).parts[1:]
    current_fd = os.open(
        "/",
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0),
    )
    ancestry: list[tuple[int, str, tuple[int, int]]] = []
    try:
        for index, component in enumerate(components):
            final = index == len(components) - 1
            before = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            if final:
                flags |= getattr(os, "O_NONBLOCK", 0)
            else:
                flags |= getattr(os, "O_DIRECTORY", 0)
            child = os.open(component, flags, dir_fd=current_fd)
            opened = os.fstat(child)
            after = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            if not (_same_inode(before, opened) and _same_inode(opened, after)):
                os.close(child)
                raise VerificationError(f"{label} path component changed while opened")
            ancestry.append(
                (os.dup(current_fd), component, (opened.st_dev, opened.st_ino))
            )
            os.close(current_fd)
            current_fd = child
    except OSError as exc:
        for parent_fd, _, _ in ancestry:
            os.close(parent_fd)
        os.close(current_fd)
        raise VerificationError(f"cannot descriptor-walk {label}: {exc}") from exc
    try:
        opened = os.fstat(current_fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise VerificationError(f"{label} must be a single-link regular file")
        if opened.st_uid != os.geteuid() or opened.st_mode & 0o022:
            raise VerificationError(f"{label} must be euid-owned and non-writable by group/world")
        raw = _read_fd(current_fd, opened.st_size, limit, label)
        for parent_fd, component, expected_identity in ancestry:
            named = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            if (named.st_dev, named.st_ino) != expected_identity:
                raise VerificationError(f"{label} ancestor changed while read")
        named_final = os.stat(canonical, follow_symlinks=False)
        if not _same_inode(opened, named_final):
            raise VerificationError(f"{label} final name changed while read")
        if (named_final.st_size, named_final.st_mtime_ns) != (
            opened.st_size,
            opened.st_mtime_ns,
        ):
            raise VerificationError(f"{label} changed while read")
        return raw, opened
    finally:
        for parent_fd, _, _ in ancestry:
            os.close(parent_fd)
        os.close(current_fd)


def _open_root_fd(path: str, label: str) -> tuple[int, os.stat_result]:
    canonical = _absolute_path(path, label)
    before = os.stat(canonical, follow_symlinks=False)
    if not stat.S_ISDIR(before.st_mode):
        raise VerificationError(f"{label} must be a directory")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(canonical, flags)
    try:
        opened = os.fstat(fd)
        after = os.stat(canonical, follow_symlinks=False)
        if not stat.S_ISDIR(opened.st_mode) or not (
            _same_inode(before, opened) and _same_inode(opened, after)
        ):
            raise VerificationError(f"{label} changed identity while opened")
        return fd, opened
    except Exception:
        os.close(fd)
        raise


def _read_identity_from_root(
    root_fd: int,
    identity: Mapping[str, Any],
    label: str,
) -> tuple[bytes, os.stat_result]:
    record = _validate_file_identity(identity, label)
    parts = PurePosixPath(record["relative_path"]).parts
    current_fd = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            before = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode):
                raise VerificationError(f"{label} parent is not a directory")
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0)
            next_fd = os.open(component, flags, dir_fd=current_fd)
            opened = os.fstat(next_fd)
            after = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            if not (_same_inode(before, opened) and _same_inode(opened, after)):
                os.close(next_fd)
                raise VerificationError(f"{label} parent changed identity")
            os.close(current_fd)
            current_fd = next_fd
        leaf = parts[-1]
        before = os.stat(leaf, dir_fd=current_fd, follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise VerificationError(f"{label} leaf is not a single-link regular file")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(leaf, flags, dir_fd=current_fd)
        try:
            opened = os.fstat(fd)
            raw = _read_fd(fd, opened.st_size, MAX_REGULAR_FILE_BYTES, label)
            after = os.stat(leaf, dir_fd=current_fd, follow_symlinks=False)
            if not (_same_inode(before, opened) and _same_inode(opened, after)):
                raise VerificationError(f"{label} changed identity while read")
            if stat.S_IMODE(opened.st_mode) != record["mode"]:
                raise VerificationError(f"{label} mode does not match")
            if len(raw) != record["bytes"] or sha256_bytes(raw) != record["sha256"]:
                raise VerificationError(f"{label} bytes or SHA-256 do not match")
            return raw, opened
        finally:
            os.close(fd)
    except OSError as exc:
        raise VerificationError(f"cannot authenticate {label}: {exc}") from exc
    finally:
        os.close(current_fd)


def _validate_file_identity(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    record = _mapping(value, label)
    _exact_keys(record, FILE_IDENTITY_KEYS, label)
    root_id = _string(record["root_id"], f"{label}.root_id")
    relative = _relative_path(record["relative_path"], f"{label}.relative_path")
    if any(fragment in relative.casefold() for fragment in FORBIDDEN_BINDING_FRAGMENTS):
        raise VerificationError(f"{label} path binds a forbidden later-stage artifact")
    if record["file_type"] != "regular":
        raise VerificationError(f"{label}.file_type must be regular")
    mode = _integer(record["mode"], f"{label}.mode")
    if mode not in (0o400, 0o500, 0o600, 0o700, 0o644, 0o755):
        raise VerificationError(f"{label}.mode is outside the frozen regular-file modes")
    size = _integer(record["bytes"], f"{label}.bytes")
    if size > MAX_REGULAR_FILE_BYTES:
        raise VerificationError(f"{label}.bytes exceeds the file bound")
    digest = _sha256(record["sha256"], f"{label}.sha256")
    return {
        "root_id": root_id,
        "relative_path": relative,
        "file_type": "regular",
        "mode": mode,
        "bytes": size,
        "sha256": digest,
    }


def _validate_identity_file(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    record = _validate_file_identity(value, label)
    if record["bytes"] > MAX_IDENTITY_FILE_BYTES:
        raise VerificationError(f"{label}.bytes exceeds the identity-file bound")
    return record


def tree_sha256(entries: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    digest.update(TREE_DOMAIN)
    for identity in entries:
        record = _validate_file_identity(identity, "tree entry")
        payload = canonical_json_bytes(record)
        digest.update(_len_prefixed(payload))
    return digest.hexdigest()


def _walk_regular_tree(
    root_fd: int, root_id: str, expected_owner_uid: int
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    cumulative_bytes = 0

    def discover(fd: int, prefix: str) -> None:
        nonlocal cumulative_bytes
        names = os.listdir(fd)
        _casefold_unique(names, f"root {root_id} directory {prefix or '.'}")
        for name in sorted(names, key=lambda item: item.encode("utf-8")):
            relative = name if not prefix else f"{prefix}/{name}"
            _relative_path(relative, f"root {root_id} entry")
            before = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if before.st_uid != expected_owner_uid or before.st_mode & 0o022:
                raise VerificationError(
                    f"root {root_id}/{relative} owner or write permissions differ"
                )
            if stat.S_ISDIR(before.st_mode):
                flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0)
                child = os.open(name, flags, dir_fd=fd)
                try:
                    opened = os.fstat(child)
                    after = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if not (_same_inode(before, opened) and _same_inode(opened, after)):
                        raise VerificationError(f"root {root_id}/{relative} changed")
                    discover(child, relative)
                    final_named = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    if not _same_inode(opened, final_named):
                        raise VerificationError(
                            f"root {root_id}/{relative} changed during descent"
                        )
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise VerificationError(
                    f"root {root_id} has non-regular or multiply linked leaf {relative}"
                )
            if len(result) >= MAX_TREE_FILES:
                raise VerificationError(f"root {root_id} exceeds the file-count bound")
            if before.st_size > MAX_REGULAR_FILE_BYTES:
                raise VerificationError(f"root {root_id}/{relative} exceeds file-byte cap")
            if cumulative_bytes > MAX_TREE_BYTES - before.st_size:
                raise VerificationError(f"root {root_id} exceeds total-byte cap")
            cumulative_bytes += before.st_size
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
            child = os.open(name, flags, dir_fd=fd)
            try:
                opened = os.fstat(child)
                raw = _read_fd(child, opened.st_size, MAX_REGULAR_FILE_BYTES, relative)
                after = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not (_same_inode(before, opened) and _same_inode(opened, after)):
                    raise VerificationError(f"root {root_id}/{relative} changed")
                result.append(
                    {
                        "root_id": root_id,
                        "relative_path": relative,
                        "file_type": "regular",
                        "mode": stat.S_IMODE(opened.st_mode),
                        "bytes": len(raw),
                        "sha256": sha256_bytes(raw),
                    }
                )
            finally:
                os.close(child)

    discover(root_fd, "")
    result.sort(key=lambda item: item["relative_path"].encode("utf-8"))
    return result


def _validate_tree_manifest(
    value: Any,
    root_id: str,
    label: str,
) -> tuple[bool, list[dict[str, Any]]]:
    tree = _mapping(value, label)
    _exact_keys(tree, {"schema_version", "complete", "entries"}, label)
    if tree["schema_version"] != ROOT_TREE_SCHEMA:
        raise VerificationError(f"{label} has the wrong schema")
    complete = _boolean(tree["complete"], f"{label}.complete")
    raw_entries = _sequence(tree["entries"], f"{label}.entries")
    if len(raw_entries) > MAX_TREE_FILES:
        raise VerificationError(f"{label} exceeds the file-count bound")
    entries = [
        _validate_file_identity(item, f"{label}.entries[{index}]")
        for index, item in enumerate(raw_entries)
    ]
    if any(item["root_id"] != root_id for item in entries):
        raise VerificationError(f"{label} entry names a different root")
    paths = [item["relative_path"] for item in entries]
    if paths != sorted(paths, key=lambda item: item.encode("utf-8")) or len(paths) != len(set(paths)):
        raise VerificationError(f"{label} entries are not unique UTF-8-bytewise sorted paths")
    _casefold_unique(paths, label)
    _no_file_ancestor_conflicts(paths, label)
    if sum(item["bytes"] for item in entries) > MAX_TREE_BYTES:
        raise VerificationError(f"{label} exceeds the total-byte bound")
    return complete, entries


def _git_blob_sha1(raw: bytes) -> str:
    return hashlib.sha1(GIT_BLOB_PREFIX + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def _git_tree_sha1(files: Sequence[tuple[str, int, str]]) -> str:
    tree: dict[str, Any] = {}
    for path, mode, blob in files:
        cursor = tree
        parts = PurePosixPath(path).parts
        for part in parts[:-1]:
            child = cursor.setdefault(part, {})
            if not isinstance(child, dict):
                raise VerificationError("source identity has a Git file/directory conflict")
            cursor = child
        if parts[-1] in cursor:
            raise VerificationError("source identity has duplicate Git path")
        cursor[parts[-1]] = (mode, blob)

    def encode(node: dict[str, Any]) -> str:
        encoded: list[tuple[bytes, bytes]] = []
        for name, value in node.items():
            name_bytes = name.encode("utf-8")
            if isinstance(value, dict):
                object_id = encode(value)
                sort_key = name_bytes + b"/"
                payload = b"40000 " + name_bytes + b"\0" + bytes.fromhex(object_id)
            else:
                mode, object_id = value
                git_mode = b"100755" if mode & 0o111 else b"100644"
                sort_key = name_bytes
                payload = git_mode + b" " + name_bytes + b"\0" + bytes.fromhex(object_id)
            encoded.append((sort_key, payload))
        body = b"".join(payload for _, payload in sorted(encoded, key=lambda item: item[0]))
        return hashlib.sha1(GIT_TREE_PREFIX + str(len(body)).encode("ascii") + b"\0" + body).hexdigest()

    return encode(tree)


def _validate_trust_root(value: Any) -> dict[str, Any]:
    trust = _mapping(value, "trust root")
    _exact_keys(
        trust,
        {
            "schema_version",
            "epoch_id",
            "classification",
            "limits",
            "root_paths",
            "identities",
            "network_backend",
        },
        "trust root",
    )
    if trust["schema_version"] != TRUST_ROOT_SCHEMA or trust["epoch_id"] != EPOCH_ID:
        raise VerificationError("trust root schema or epoch differs")
    expected_classification = {
        "execution_authorized": False,
        "outcome_independent": True,
        "prospective": True,
        "scientific_evidence": False,
        "scientific_evidence_eligible": False,
    }
    if not _same_json_type_and_value(trust["classification"], expected_classification):
        raise VerificationError("trust root classification differs")
    if not _same_json_type_and_value(trust["limits"], LIMITS):
        raise VerificationError("trust root limits differ from the verifier profile")

    root_paths_raw = _sequence(trust["root_paths"], "trust root.root_paths")
    if not root_paths_raw:
        raise VerificationError("trust root has no pinned roots")
    root_paths: dict[str, str] = {}
    previous: str | None = None
    for index, item in enumerate(root_paths_raw):
        record = _mapping(item, f"trust root.root_paths[{index}]")
        _exact_keys(record, {"root_id", "canonical_absolute_path"}, "trust root root")
        root_id = _string(record["root_id"], "trust root root_id")
        path = _absolute_path(record["canonical_absolute_path"], "trust root root path")
        if previous is not None and root_id <= previous:
            raise VerificationError("trust root roots are not sorted and unique")
        previous = root_id
        root_paths[root_id] = path
    if not REQUIRED_ROOT_IDS <= set(root_paths):
        raise VerificationError("trust root omits a required build root")

    identities_raw = _sequence(trust["identities"], "trust root.identities")
    identities: dict[str, dict[str, Any]] = {}
    previous = None
    for index, item in enumerate(identities_raw):
        record = _mapping(item, f"trust root.identities[{index}]")
        _exact_keys(record, {"name", "file"}, "trust-root identity")
        name = _string(record["name"], "trust-root identity name")
        if previous is not None and name <= previous:
            raise VerificationError("trust-root identities are not sorted and unique")
        previous = name
        identities[name] = _validate_identity_file(
            record["file"], f"trust-root identity {name}"
        )
    if set(identities) != TRUST_IDENTITY_NAMES:
        raise VerificationError("trust root does not contain the exact identity set")
    for name, identity in identities.items():
        if identity["root_id"] not in root_paths:
            raise VerificationError(f"trust identity {name} names an unknown root")

    backend = _mapping(trust["network_backend"], "trust root.network_backend")
    _exact_keys(
        backend,
        {"backend", "guard_identity", "policy_identity", "host_mechanism_identity"},
        "trust root.network_backend",
    )
    backend_name = _string(backend["backend"], "trust root network backend")
    expected_names = {
        "guard_identity": "network_guard",
        "policy_identity": "network_policy",
        "host_mechanism_identity": "permitted_host_os_mechanism",
    }
    for field, expected in expected_names.items():
        if backend[field] != expected:
            raise VerificationError(f"trust root network {field} differs")

    return {
        "raw": trust,
        "root_paths": root_paths,
        "identities": identities,
        "network_backend": backend_name,
    }


def _verify_root_records(
    value: Any,
    trust: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, int], dict[str, list[dict[str, Any]]]]:
    roots_raw = _sequence(value, "roots")
    root_records: dict[str, dict[str, Any]] = {}
    root_fds: dict[str, int] = {}
    root_entries: dict[str, list[dict[str, Any]]] = {}
    previous: str | None = None
    aggregate_regular_bytes = 0
    try:
        for index, item in enumerate(roots_raw):
            record = _mapping(item, f"roots[{index}]")
            _exact_keys(record, ROOT_KEYS, f"roots[{index}]")
            root_id = _string(record["root_id"], f"roots[{index}].root_id")
            if previous is not None and root_id <= previous:
                raise VerificationError("roots are not sorted and unique")
            previous = root_id
            if root_id not in trust["root_paths"]:
                raise VerificationError(f"root {root_id} is not pinned by the trust root")
            path = _absolute_path(
                record["canonical_absolute_path"], f"root {root_id} path"
            )
            if path != trust["root_paths"][root_id]:
                raise VerificationError(f"root {root_id} path differs from trust root")
            owner_uid = _integer(record["owner_uid"], f"root {root_id}.owner_uid")
            mode = _integer(record["mode"], f"root {root_id}.mode")
            if mode > 0o7777:
                raise VerificationError(f"root {root_id}.mode is invalid")
            complete, entries = _validate_tree_manifest(
                record["tree_manifest"], root_id, f"root {root_id}.tree_manifest"
            )
            if root_id in {"reference-build", "optimized-build"} and not complete:
                raise VerificationError(
                    f"fresh build root {root_id} must have a complete final inventory"
                )
            entry_count = _integer(record["entry_count"], f"root {root_id}.entry_count")
            total_bytes = _integer(
                record["total_regular_bytes"], f"root {root_id}.total_regular_bytes"
            )
            if aggregate_regular_bytes > MAX_TREE_BYTES - total_bytes:
                raise VerificationError("all provenance roots exceed aggregate byte cap")
            aggregate_regular_bytes += total_bytes
            expected_tree = _sha256(record["tree_sha256"], f"root {root_id}.tree_sha256")
            if entry_count != len(entries) or total_bytes != sum(
                entry["bytes"] for entry in entries
            ):
                raise VerificationError(f"root {root_id} tree counts differ")
            if tree_sha256(entries) != expected_tree:
                raise VerificationError(f"root {root_id} tree SHA-256 differs")
            fd, info = _open_root_fd(path, f"root {root_id}")
            root_fds[root_id] = fd
            if info.st_uid != owner_uid or stat.S_IMODE(info.st_mode) != mode:
                raise VerificationError(f"root {root_id} owner or mode differs")
            if info.st_mode & 0o022:
                raise VerificationError(f"root {root_id} is group/world writable")
            if root_id in PRIVATE_ROOT_IDS and (
                info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700
            ):
                raise VerificationError(f"private root {root_id} must be euid-owned mode 0700")
            if complete:
                live = _walk_regular_tree(fd, root_id, owner_uid)
                if live != entries:
                    raise VerificationError(f"complete root {root_id} differs from disk")
            else:
                for entry_index, entry in enumerate(entries):
                    _read_identity_from_root(
                        fd,
                        entry,
                        f"root {root_id} entry[{entry_index}]",
                    )
            root_records[root_id] = {
                **record,
                "complete": complete,
                "entries": entries,
            }
            root_entries[root_id] = entries
        if set(root_records) != set(trust["root_paths"]):
            raise VerificationError("provenance roots differ from trust-root roots")
        if not REQUIRED_ROOT_IDS <= set(root_records):
            raise VerificationError("provenance omits a required root")
        root_ids = sorted(root_records)
        permitted_alias = frozenset({"repository", "runtime-artifact-root"})
        for left_index, left_id in enumerate(root_ids):
            left_path = root_records[left_id]["canonical_absolute_path"]
            left_info = os.fstat(root_fds[left_id])
            for right_id in root_ids[left_index + 1 :]:
                right_path = root_records[right_id]["canonical_absolute_path"]
                right_info = os.fstat(root_fds[right_id])
                pair = frozenset({left_id, right_id})
                same_inode = _same_inode(left_info, right_info)
                same_path = left_path == right_path
                if pair == permitted_alias:
                    if not same_inode or not same_path:
                        raise VerificationError(
                            "repository/runtime-artifact-root alias is not literal"
                        )
                    continue
                if same_inode or same_path:
                    raise VerificationError("unregistered build-root alias is forbidden")
                common = os.path.commonpath([left_path, right_path])
                if common in {left_path, right_path}:
                    raise VerificationError("unregistered build-root overlap is forbidden")
        if root_records["repository"]["canonical_absolute_path"] != root_records[
            "runtime-artifact-root"
        ]["canonical_absolute_path"]:
            raise VerificationError(
                "repository and runtime-artifact-root must be the same canonical root"
            )
        for root_id, fd in root_fds.items():
            opened = os.fstat(fd)
            named = os.stat(
                root_records[root_id]["canonical_absolute_path"], follow_symlinks=False
            )
            if not _same_inode(opened, named):
                raise VerificationError(f"root {root_id} final pathname changed")
        return root_records, root_fds, root_entries
    except Exception:
        for fd in root_fds.values():
            os.close(fd)
        raise


def _identity_key(identity: Mapping[str, Any]) -> tuple[str, str, int, int, str]:
    return (
        identity["root_id"],
        identity["relative_path"],
        identity["mode"],
        identity["bytes"],
        identity["sha256"],
    )


def _authenticate_identity(
    identity: Mapping[str, Any],
    roots: Mapping[str, Mapping[str, Any]],
    root_fds: Mapping[str, int],
    label: str,
) -> bytes:
    record = _validate_file_identity(identity, label)
    if record["root_id"] not in roots:
        raise VerificationError(f"{label} names an unknown root")
    raw, info = _read_identity_from_root(root_fds[record["root_id"]], record, label)
    if info.st_uid != roots[record["root_id"]]["owner_uid"] or info.st_mode & 0o022:
        raise VerificationError(f"{label} owner or write permissions differ from root policy")
    return raw


def _verify_identity_bindings(
    value: Any,
    roots: Mapping[str, Mapping[str, Any]],
    root_fds: Mapping[str, int],
    trust: Mapping[str, Any],
    trust_raw_sha256: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, bytes]]:
    bindings = _mapping(value, "identity_bindings")
    if set(bindings) != REQUIRED_IDENTITY_BINDINGS:
        raise VerificationError("identity_bindings differs from the frozen exact set")
    for name in bindings:
        lowered = name.casefold()
        if any(fragment in lowered for fragment in FORBIDDEN_BINDING_FRAGMENTS):
            raise VerificationError(f"forbidden acyclic binding {name!r}")
    validated: dict[str, dict[str, Any]] = {}
    raw_values: dict[str, bytes] = {}
    for name in sorted(bindings):
        identity = _validate_identity_file(bindings[name], f"identity_bindings.{name}")
        raw = _authenticate_identity(identity, roots, root_fds, f"identity binding {name}")
        validated[name] = identity
        raw_values[name] = raw

    expected_trust = trust["identities"]
    cross = {
        "provenance_verifier": "provenance_verifier",
        "build_executor": "hermetic_build_executor",
        "action_wrapper": "action_wrapper",
        "tree_hasher": "tree_and_package_closure_generator",
        "macho_parser": "macho_parser",
    }
    for binding_name, trust_name in cross.items():
        if _identity_key(validated[binding_name]) != _identity_key(expected_trust[trust_name]):
            raise VerificationError(
                f"identity binding {binding_name} differs from prospective trust root"
            )
    trust_binding = validated["build_trust_root"]
    if trust_binding["sha256"] != trust_raw_sha256:
        raise VerificationError("build_trust_root binding differs from external trust bytes")
    if validated["offline_build_inputs_manifest"]["relative_path"] != (
        "thirdparty/dist/offline_build_inputs_v2.json"
    ) or validated["offline_build_inputs_verifier"]["relative_path"] != (
        "scripts/comparison/verify_offline_build_inputs_v2.py"
    ):
        raise VerificationError("offline v2 manifest/verifier binding paths differ")
    return validated, raw_values


def _verify_source_identity(
    raw: bytes,
    expected_method: str,
    expected_root: str,
    roots: Mapping[str, Mapping[str, Any]],
    root_fds: Mapping[str, int],
) -> None:
    value = _mapping(_parse_json(raw, f"{expected_method} source identity"), "source identity")
    _exact_keys(
        value,
        {
            "schema_version",
            "method",
            "worktree_root",
            "git_commit",
            "git_commit_object",
            "git_tree",
            "classification",
            "files",
            "source_tree_sha256",
        },
        "source identity",
    )
    if value["schema_version"] != SOURCE_IDENTITY_SCHEMA or value["method"] != expected_method:
        raise VerificationError("source identity schema or method differs")
    if value["worktree_root"] != expected_root:
        raise VerificationError("source identity names the wrong worktree root")
    classification = {
        "clean": True,
        "remote_free": True,
        "submodules_absent": True,
        "alternates_absent": True,
        "replace_refs_absent": True,
        "untracked_files_absent": True,
    }
    if not _same_json_type_and_value(value["classification"], classification):
        raise VerificationError("source identity does not prove a clean remote-free worktree")
    commit = _sha1(value["git_commit"], "source git_commit")
    tree = _sha1(value["git_tree"], "source git_tree")
    commit_identity = _validate_file_identity(
        value["git_commit_object"], "source git commit object"
    )
    commit_raw = _authenticate_identity(
        commit_identity, roots, root_fds, "source git commit object"
    )
    calculated_commit = hashlib.sha1(
        b"commit " + str(len(commit_raw)).encode("ascii") + b"\0" + commit_raw
    ).hexdigest()
    if calculated_commit != commit:
        raise VerificationError("source Git commit object hash differs")
    first_line = commit_raw.splitlines()[0] if commit_raw else b""
    if first_line != b"tree " + tree.encode("ascii"):
        raise VerificationError("source Git commit object names a different tree")

    files_raw = _sequence(value["files"], "source identity.files")
    if not files_raw or len(files_raw) > MAX_TREE_FILES:
        raise VerificationError("source identity file count is invalid")
    source_files: list[tuple[str, int, str]] = []
    identities: list[dict[str, Any]] = []
    paths: list[str] = []
    for index, item in enumerate(files_raw):
        record = _mapping(item, f"source identity.files[{index}]")
        _exact_keys(record, {"file", "git_blob_sha1"}, "source file")
        identity = _validate_file_identity(record["file"], "source file identity")
        if identity["root_id"] != expected_root:
            raise VerificationError("source file names the wrong source root")
        raw_file = _authenticate_identity(identity, roots, root_fds, "source file")
        blob = _sha1(record["git_blob_sha1"], "source git blob")
        if _git_blob_sha1(raw_file) != blob:
            raise VerificationError("source Git blob identity differs")
        paths.append(identity["relative_path"])
        identities.append(identity)
        source_files.append((identity["relative_path"], identity["mode"], blob))
    if paths != sorted(paths, key=lambda item: item.encode("utf-8")) or len(paths) != len(set(paths)):
        raise VerificationError("source files are not unique UTF-8-bytewise sorted")
    if _git_tree_sha1(source_files) != tree:
        raise VerificationError("source Git tree identity differs")
    if tree_sha256(identities) != _sha256(value["source_tree_sha256"], "source tree SHA"):
        raise VerificationError("source SHA-256 tree identity differs")
    root_entries = roots[expected_root]["entries"]
    if identities != root_entries or not roots[expected_root]["complete"]:
        raise VerificationError("source identity must cover the complete source worktree")


def _parse_macho(raw: bytes, label: str) -> dict[str, Any]:
    """Parse the bounded load-command surface needed by the provenance contract."""
    if len(raw) < 32 or raw[:4] != b"\xcf\xfa\xed\xfe":
        raise VerificationError(f"{label} is not a little-endian 64-bit Mach-O")
    _, cpu_type, cpu_subtype, file_type, ncmds, sizeofcmds, flags, reserved = struct.unpack(
        "<IIIIIIII", raw[:32]
    )
    del cpu_subtype, flags, reserved
    if cpu_type != 0x0100000C:
        raise VerificationError(f"{label} is not arm64 Mach-O")
    if ncmds > 65_536 or sizeofcmds > len(raw) - 32:
        raise VerificationError(f"{label} has invalid load-command bounds")
    type_names = {1: "object", 2: "execute", 6: "dylib", 8: "bundle"}
    if file_type not in type_names:
        raise VerificationError(f"{label} has an unsupported Mach-O file type")
    cursor = 32
    end = cursor + sizeofcmds
    dylibs: list[str] = []
    rpaths: list[str] = []
    code_signature_sha256: str | None = None
    load_parts: list[bytes] = []
    for _ in range(ncmds):
        if cursor + 8 > end:
            raise VerificationError(f"{label} has a truncated load command")
        cmd, cmdsize = struct.unpack("<II", raw[cursor : cursor + 8])
        if cmdsize < 8 or cursor + cmdsize > end or cmdsize % 4:
            raise VerificationError(f"{label} has an invalid load command")
        command = raw[cursor : cursor + cmdsize]
        load_parts.append(command)
        base_cmd = cmd & 0x7FFFFFFF
        if base_cmd in (0xC, 0x18, 0x1F, 0x20, 0x23):
            if cmdsize < 24:
                raise VerificationError(f"{label} has a short dylib command")
            offset = struct.unpack("<I", command[8:12])[0]
            if offset < 24 or offset >= cmdsize:
                raise VerificationError(f"{label} has an invalid dylib name offset")
            name = command[offset:].split(b"\0", 1)[0]
            try:
                dylibs.append(name.decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise VerificationError(f"{label} has a non-UTF8 dylib name") from exc
        elif base_cmd == 0x1C:
            if cmdsize < 12:
                raise VerificationError(f"{label} has a short rpath command")
            offset = struct.unpack("<I", command[8:12])[0]
            if offset < 12 or offset >= cmdsize:
                raise VerificationError(f"{label} has an invalid rpath offset")
            name = command[offset:].split(b"\0", 1)[0]
            try:
                rpaths.append(name.decode("utf-8"))
            except UnicodeDecodeError as exc:
                raise VerificationError(f"{label} has a non-UTF8 rpath") from exc
        elif base_cmd == 0x1D:
            if cmdsize != 16:
                raise VerificationError(f"{label} has an invalid code-signature command")
            offset, size = struct.unpack("<II", command[8:16])
            if offset > len(raw) or size > len(raw) - offset:
                raise VerificationError(f"{label} has an invalid code-signature range")
            code_signature_sha256 = sha256_bytes(raw[offset : offset + size])
        cursor += cmdsize
    if cursor != end:
        raise VerificationError(f"{label} load commands do not consume sizeofcmds")
    return {
        "architectures": ["arm64"],
        "code_signature_sha256": code_signature_sha256,
        "dylibs": dylibs,
        "file_type": type_names[file_type],
        "load_commands_sha256": sha256_bytes(b"".join(load_parts)),
        "rpaths": rpaths,
    }


def _validate_macho_record(value: Any, raw: bytes, label: str) -> dict[str, Any]:
    record = _mapping(value, label)
    expected_keys = {
        "architectures",
        "code_signature_sha256",
        "dylibs",
        "file_type",
        "load_commands_sha256",
        "rpaths",
    }
    _exact_keys(record, expected_keys, label)
    parsed = _parse_macho(raw, label)
    if record != parsed:
        raise VerificationError(f"{label} Mach-O metadata differs from file bytes")
    return parsed


def _parse_pixi_lock_packages(raw: bytes) -> list[tuple[str, str]]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VerificationError("pixi.lock is not UTF-8") from exc
    marker = "\npackages:\n"
    if marker not in text:
        raise VerificationError("pixi.lock lacks a top-level packages section")
    package_text = text.split(marker, 1)[1]
    lines = package_text.splitlines()
    result: list[tuple[str, str]] = []
    pending: str | None = None
    for line in lines:
        match = re.fullmatch(r"- conda: (\S+)", line)
        if match:
            if pending is not None:
                raise VerificationError("pixi.lock package lacks SHA-256")
            pending = match.group(1)
            continue
        if pending is not None:
            sha_match = re.fullmatch(r"  sha256: ([0-9a-f]{64})", line)
            if sha_match:
                result.append((pending, sha_match.group(1)))
                pending = None
                continue
            if line.startswith("- "):
                raise VerificationError("pixi.lock package lacks adjacent SHA-256")
    if pending is not None:
        raise VerificationError("pixi.lock final package lacks SHA-256")
    if not result or len(result) > MAX_PACKAGES or len(result) != len(set(result)):
        raise VerificationError("pixi.lock package closure is empty, duplicate, or too large")
    return sorted(result)


def _identity_sequence(
    value: Any,
    label: str,
    *,
    required_root_id: str | None = None,
) -> list[dict[str, Any]]:
    raw = _sequence(value, label)
    if len(raw) > MAX_TREE_FILES:
        raise VerificationError(f"{label} exceeds the file-count bound")
    result = [
        _validate_file_identity(item, f"{label}[{index}]")
        for index, item in enumerate(raw)
    ]
    paths = [f"{item['root_id']}:{item['relative_path']}" for item in result]
    if paths != sorted(paths, key=lambda item: item.encode("utf-8")) or len(paths) != len(set(paths)):
        raise VerificationError(f"{label} is not unique UTF-8-bytewise sorted")
    _casefold_unique(paths, label)
    if required_root_id is not None and any(
        item["root_id"] != required_root_id for item in result
    ):
        raise VerificationError(f"{label} names a root other than {required_root_id}")
    return result


def _ordered_identity_sequence(value: Any, label: str) -> list[dict[str, Any]]:
    raw = _sequence(value, label)
    if len(raw) > MAX_TREE_FILES:
        raise VerificationError(f"{label} exceeds the file-count bound")
    result = [
        _validate_file_identity(item, f"{label}[{index}]")
        for index, item in enumerate(raw)
    ]
    keys = [(item["root_id"], item["relative_path"]) for item in result]
    if len(keys) != len(set(keys)):
        raise VerificationError(f"{label} contains a duplicate identity")
    _casefold_unique([f"{root}:{path}" for root, path in keys], label)
    return result


def _verify_environment_records(
    value: Any,
    roots: Mapping[str, Mapping[str, Any]],
    tools_by_role: Mapping[str, Mapping[str, Any]],
    sdk: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    raw = _sequence(value, "environments")
    if not raw or len(raw) > MAX_ENVIRONMENTS:
        raise VerificationError("environment count is empty or exceeds its bound")
    result: dict[str, dict[str, str]] = {}
    previous: str | None = None
    for index, item in enumerate(raw):
        record = _mapping(item, f"environments[{index}]")
        _exact_keys(record, {"environment_id", "variables"}, "environment record")
        variables_raw = _mapping(record["variables"], "environment variables")
        if set(variables_raw) != REQUIRED_ENVIRONMENT_VARIABLES:
            raise VerificationError("environment variable names differ from the exact frozen set")
        variables = {
            key: _bounded_text(value, f"environment {key}", MAX_ARG_BYTES)
            for key, value in variables_raw.items()
        }
        expected_id = environment_id(variables)
        if record["environment_id"] != expected_id:
            raise VerificationError("environment_id differs from exact variables")
        if previous is not None and expected_id <= previous:
            raise VerificationError("environments are not sorted and unique")
        previous = expected_id
        if variables["HOME"] != roots["private-home"]["canonical_absolute_path"]:
            raise VerificationError("environment HOME differs from private-home root")
        if variables["TMPDIR"] != roots["private-tmp"]["canonical_absolute_path"]:
            raise VerificationError("environment TMPDIR differs from private-tmp root")
        if variables["SDKROOT"] != roots["sdk-root"]["canonical_absolute_path"]:
            raise VerificationError("environment SDKROOT differs from sdk-root")
        if variables["MACOSX_DEPLOYMENT_TARGET"] != sdk["deployment_target"]:
            raise VerificationError("environment deployment target differs from SDK")
        expected_cc = os.path.join(
            roots[tools_by_role["c-driver"]["file"]["root_id"]]["canonical_absolute_path"],
            tools_by_role["c-driver"]["file"]["relative_path"],
        )
        expected_cxx = os.path.join(
            roots[tools_by_role["cxx-driver"]["file"]["root_id"]]["canonical_absolute_path"],
            tools_by_role["cxx-driver"]["file"]["relative_path"],
        )
        if variables["CC"] != expected_cc or variables["CXX"] != expected_cxx:
            raise VerificationError("environment compiler driver paths differ")
        path_parts = variables["PATH"].split(":")
        if not path_parts or any(
            not os.path.isabs(part)
            or os.path.normpath(part) != part
            or part != unicodedata.normalize("NFC", part)
            for part in path_parts
        ):
            raise VerificationError(
                "environment PATH must contain only canonical absolute directories"
            )
        toolchain_path = roots["toolchain-prefix"]["canonical_absolute_path"]
        if any(os.path.commonpath([toolchain_path, part]) != toolchain_path for part in path_parts):
            raise VerificationError("environment PATH escapes the authenticated toolchain prefix")
        authenticated_tool_directories = {
            os.path.dirname(
                os.path.join(
                    roots[tool["file"]["root_id"]]["canonical_absolute_path"],
                    tool["file"]["relative_path"],
                )
            )
            for tool in tools_by_role.values()
        }
        if any(part not in authenticated_tool_directories for part in path_parts):
            raise VerificationError(
                "environment PATH names a directory without an authenticated tool"
            )
        fixed = {
            "LANG": "C",
            "LC_ALL": "C",
            "TZ": "UTC",
            "ZERO_AR_DATE": "1",
            "OMP_NUM_THREADS": "1",
            "OMP_DYNAMIC": "FALSE",
            "OMP_WAIT_POLICY": "PASSIVE",
        }
        for key, expected in fixed.items():
            if variables[key] != expected:
                raise VerificationError(f"environment {key} differs from frozen value")
        if not re.fullmatch(r"(?:0|[1-9][0-9]*)", variables["SOURCE_DATE_EPOCH"]):
            raise VerificationError("SOURCE_DATE_EPOCH must be a canonical nonnegative integer")
        result[expected_id] = variables
    return result


def _verify_toolchain(
    value: Any,
    roots: Mapping[str, Mapping[str, Any]],
    root_fds: Mapping[str, int],
    pixi_lock_raw: bytes,
    pixi_lock_identity: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], set[tuple[str, str, int, int, str]]]:
    toolchain = _mapping(value, "toolchain")
    _exact_keys(toolchain, {"package_closure", "tools", "trees"}, "toolchain")
    tools_raw = _sequence(toolchain["tools"], "toolchain.tools")
    tools: dict[str, dict[str, Any]] = {}
    previous: str | None = None
    authenticated_files: set[tuple[str, str, int, int, str]] = set()
    for index, item in enumerate(tools_raw):
        record = _mapping(item, f"toolchain.tools[{index}]")
        _exact_keys(
            record,
            {"role", "file", "version_argv", "version_stdout_sha256", "macho"},
            "tool record",
        )
        role = _string(record["role"], "tool role")
        if previous is not None and role <= previous:
            raise VerificationError("tool roles are not sorted and unique")
        previous = role
        identity = _validate_file_identity(record["file"], f"tool {role}")
        if identity["root_id"] != "toolchain-prefix":
            raise VerificationError(f"tool {role} is outside toolchain-prefix")
        raw_file = _authenticate_identity(identity, roots, root_fds, f"tool {role}")
        if identity["mode"] & stat.S_IXUSR == 0:
            raise VerificationError(f"tool {role} is not owner-executable")
        argv = _sequence(record["version_argv"], f"tool {role}.version_argv")
        if not argv or len(argv) > MAX_ARGV_ITEMS:
            raise VerificationError(f"tool {role} version argv is empty or too long")
        argv_values = [_bounded_text(arg, f"tool {role} version arg", MAX_ARG_BYTES) for arg in argv]
        absolute_tool = os.path.join(
            roots["toolchain-prefix"]["canonical_absolute_path"], identity["relative_path"]
        )
        if argv_values[0] != absolute_tool:
            raise VerificationError(f"tool {role} version argv does not invoke exact tool")
        _sha256(record["version_stdout_sha256"], f"tool {role} version stdout")
        tool_macho = _validate_macho_record(record["macho"], raw_file, f"tool {role}")
        if tool_macho["file_type"] != "execute":
            raise VerificationError(f"tool {role} is not an executable Mach-O")
        authenticated_files.add(_identity_key(identity))
        tools[role] = {**record, "file": identity, "version_argv": argv_values}
    if not TOOL_ROLES <= set(tools) or any(
        role not in TOOL_ROLES and not role.startswith("helper:") for role in tools
    ):
        raise VerificationError("toolchain omits a required role or has an unregistered helper")

    trees_raw = _sequence(toolchain["trees"], "toolchain.trees")
    tree_roles: set[str] = set()
    tool_tree_entries: dict[str, list[dict[str, Any]]] = {}
    previous = None
    for index, item in enumerate(trees_raw):
        record = _mapping(item, f"toolchain.trees[{index}]")
        _exact_keys(record, {"role", "entries", "tree_sha256"}, "tool tree")
        role = _string(record["role"], "tool tree role")
        if previous is not None and role <= previous:
            raise VerificationError("tool trees are not sorted and unique")
        previous = role
        entries = _identity_sequence(record["entries"], f"tool tree {role}")
        if not entries:
            raise VerificationError(f"tool tree {role} is empty")
        if role != "pixi-package-archives" and any(
            entry["root_id"] != "toolchain-prefix" for entry in entries
        ):
            raise VerificationError(f"tool tree {role} escapes toolchain-prefix")
        for entry in entries:
            _authenticate_identity(entry, roots, root_fds, f"tool tree {role}")
            authenticated_files.add(_identity_key(entry))
        if tree_sha256(entries) != _sha256(record["tree_sha256"], f"tool tree {role} SHA"):
            raise VerificationError(f"tool tree {role} SHA differs")
        tree_roles.add(role)
        tool_tree_entries[role] = entries
    if tree_roles != TOOL_TREE_ROLES:
        raise VerificationError("toolchain tree roles differ from exact required set")

    closure = _mapping(toolchain["package_closure"], "toolchain.package_closure")
    _exact_keys(
        closure,
        {"pixi_lock", "packages", "prefix_entries", "prefix_tree_sha256"},
        "package closure",
    )
    lock_identity = _validate_file_identity(closure["pixi_lock"], "package closure pixi_lock")
    if lock_identity != pixi_lock_identity or sha256_bytes(pixi_lock_raw) != lock_identity[
        "sha256"
    ]:
        raise VerificationError("package closure pixi_lock differs from binding")
    packages_raw = _sequence(closure["packages"], "package closure packages")
    if not packages_raw or len(packages_raw) > MAX_PACKAGES:
        raise VerificationError("package closure package count is invalid")
    packages: list[tuple[str, str]] = []
    package_archives: list[dict[str, Any]] = []
    extracted_union: dict[tuple[str, str], dict[str, Any]] = {}
    previous_url: str | None = None
    for index, item in enumerate(packages_raw):
        record = _mapping(item, f"package closure packages[{index}]")
        _exact_keys(
            record,
            {"locked_url", "locked_sha256", "archive", "extracted_entries", "extracted_tree_sha256"},
            "package record",
        )
        url = _string(record["locked_url"], "package URL")
        if not url.startswith("https://conda.anaconda.org/conda-forge/"):
            raise VerificationError("package URL is outside the frozen conda-forge origin")
        if previous_url is not None and url <= previous_url:
            raise VerificationError("package records are not URL-sorted and unique")
        previous_url = url
        digest = _sha256(record["locked_sha256"], "package locked SHA")
        archive = _validate_file_identity(record["archive"], "package archive")
        if archive["sha256"] != digest:
            raise VerificationError("package archive SHA differs from pixi lock SHA")
        _authenticate_identity(archive, roots, root_fds, "package archive")
        package_archives.append(archive)
        entries = _identity_sequence(
            record["extracted_entries"], "package extracted entries", required_root_id="toolchain-prefix"
        )
        if tree_sha256(entries) != _sha256(
            record["extracted_tree_sha256"], "package extracted tree SHA"
        ):
            raise VerificationError("package extracted-tree SHA differs")
        for entry in entries:
            key = (entry["root_id"], entry["relative_path"])
            if key in extracted_union:
                raise VerificationError("two packages claim the same extracted prefix path")
            _authenticate_identity(entry, roots, root_fds, "package extracted file")
            extracted_union[key] = entry
            authenticated_files.add(_identity_key(entry))
        packages.append((url, digest))
    if sorted(packages) != _parse_pixi_lock_packages(pixi_lock_raw):
        raise VerificationError("package closure does not exactly reconcile pixi.lock")
    prefix_entries = _identity_sequence(
        closure["prefix_entries"], "package prefix entries", required_root_id="toolchain-prefix"
    )
    if prefix_entries != sorted(
        extracted_union.values(), key=lambda item: item["relative_path"].encode("utf-8")
    ):
        raise VerificationError("package extracted union differs from prefix entries")
    if roots["toolchain-prefix"]["entries"] != prefix_entries or not roots[
        "toolchain-prefix"
    ]["complete"]:
        raise VerificationError("package closure must cover the complete toolchain prefix")
    if tree_sha256(prefix_entries) != _sha256(
        closure["prefix_tree_sha256"], "package prefix tree SHA"
    ):
        raise VerificationError("package prefix tree SHA differs")
    expected_archives = sorted(
        package_archives,
        key=lambda item: f"{item['root_id']}:{item['relative_path']}".encode("utf-8"),
    )
    if tool_tree_entries["pixi-package-archives"] != expected_archives:
        raise VerificationError("package-archive tool tree differs from exact locked archives")
    if tool_tree_entries["pixi-extracted-prefix"] != prefix_entries:
        raise VerificationError("extracted-prefix tool tree differs from complete package prefix")
    prefix_keys = {_identity_key(item) for item in prefix_entries}
    if not authenticated_files <= prefix_keys | {
        _identity_key(item) for item in package_archives
    }:
        raise VerificationError("tool/tree identity is outside package archive/prefix closure")
    return tools, authenticated_files


def _verify_sdk(
    value: Any,
    roots: Mapping[str, Mapping[str, Any]],
    root_fds: Mapping[str, int],
) -> tuple[dict[str, Any], set[tuple[str, str, int, int, str]]]:
    sdk = _mapping(value, "sdk")
    _exact_keys(
        sdk,
        {
            "root",
            "sdk_settings",
            "sysroot_argument",
            "deployment_target",
            "architectures",
            "platform",
            "link_input_closure_manifest",
        },
        "sdk",
    )
    if sdk["root"] != "sdk-root":
        raise VerificationError("SDK root differs")
    settings = _validate_file_identity(sdk["sdk_settings"], "SDK settings")
    if settings["root_id"] != "sdk-root":
        raise VerificationError("SDK settings is outside sdk-root")
    _authenticate_identity(settings, roots, root_fds, "SDK settings")
    expected_sysroot = roots["sdk-root"]["canonical_absolute_path"]
    if sdk["sysroot_argument"] != expected_sysroot:
        raise VerificationError("SDK sysroot argument is not the exact canonical SDK root")
    deployment = _string(sdk["deployment_target"], "SDK deployment target")
    if not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)?", deployment):
        raise VerificationError("SDK deployment target is not explicit canonical version text")
    if sdk["architectures"] != ["arm64"] or sdk["platform"] != "macos":
        raise VerificationError("SDK platform or architecture differs")
    closure = _mapping(sdk["link_input_closure_manifest"], "SDK link closure")
    _exact_keys(closure, {"entries", "tree_sha256"}, "SDK link closure")
    entries = _identity_sequence(
        closure["entries"], "SDK link closure entries", required_root_id="sdk-root"
    )
    if not entries:
        raise VerificationError("SDK link closure is empty")
    suffixes = {PurePosixPath(entry["relative_path"]).suffix for entry in entries}
    if not ({".h", ".tbd"} <= suffixes):
        raise VerificationError("SDK link closure must contain authenticated headers and .tbd files")
    authenticated: set[tuple[str, str, int, int, str]] = {_identity_key(settings)}
    for entry in entries:
        _authenticate_identity(entry, roots, root_fds, "SDK link input")
        authenticated.add(_identity_key(entry))
    if tree_sha256(entries) != _sha256(closure["tree_sha256"], "SDK link closure SHA"):
        raise VerificationError("SDK link closure SHA differs")
    root_entries = roots["sdk-root"]["entries"]
    if not roots["sdk-root"]["complete"] or root_entries != sorted(
        {_identity_key(item): item for item in [settings, *entries]}.values(),
        key=lambda item: item["relative_path"].encode("utf-8"),
    ):
        raise VerificationError("SDK record must cover the complete authenticated SDK root")
    return sdk, authenticated


def _offline_tree_sha256(
    entries: Sequence[Mapping[str, Any]],
    relative_root: str,
) -> str:
    root = PurePosixPath(relative_root)
    normalized: list[tuple[str, Mapping[str, Any]]] = []
    for entry in entries:
        path = PurePosixPath(entry["relative_path"])
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError as exc:
            raise VerificationError("dependency extracted entry escapes its source root") from exc
        _relative_path(relative, "dependency upstream-relative path")
        normalized.append((relative, entry))
    normalized.sort(key=lambda item: item[0].encode("utf-8"))
    digest = hashlib.sha256()
    digest.update(OFFLINE_TREE_DOMAIN)
    for path, entry in normalized:
        encoded = path.encode("utf-8")
        digest.update(b"file\0")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(entry["bytes"].to_bytes(8, "big"))
        digest.update(bytes.fromhex(entry["sha256"]))
    return digest.hexdigest()


def _offline_repository_identity(value: Any, label: str) -> dict[str, Any]:
    record = _mapping(value, label)
    _exact_keys(record, {"bytes", "mode", "path", "sha256"}, label)
    return _validate_file_identity(
        {
            "root_id": "repository",
            "relative_path": record["path"],
            "file_type": "regular",
            "mode": record["mode"],
            "bytes": record["bytes"],
            "sha256": record["sha256"],
        },
        label,
    )


def _verify_dependency_record(
    name: str,
    value: Any,
    expected: Mapping[str, Any],
    roots: Mapping[str, Mapping[str, Any]],
    root_fds: Mapping[str, int],
) -> set[tuple[str, str, int, int, str]]:
    record = _mapping(value, f"dependency {name}")
    _exact_keys(
        record,
        {
            "source_kind",
            "version",
            "immutable_revision",
            "archive",
            "extracted_tree",
            "cmake_source_root",
            "nested_dependencies",
        },
        f"dependency {name}",
    )
    for key in ("source_kind", "version", "immutable_revision"):
        if record[key] != expected[key]:
            raise VerificationError(f"dependency {name}.{key} differs")
    authenticated: set[tuple[str, str, int, int, str]] = set()
    archive = _validate_file_identity(record["archive"], f"dependency {name} archive")
    if archive != expected["archive"]:
        raise VerificationError(f"dependency {name} archive differs from offline manifest")
    _authenticate_identity(archive, roots, root_fds, f"dependency {name} archive")
    authenticated.add(_identity_key(archive))

    extracted_raw = _sequence(record["extracted_tree"], f"dependency {name}.extracted_tree")
    if len(extracted_raw) != 2:
        raise VerificationError(f"dependency {name} must have two method-specific trees")
    trees: dict[str, tuple[str, str, list[dict[str, Any]]]] = {}
    previous: str | None = None
    for index, item in enumerate(extracted_raw):
        tree_record = _mapping(item, f"dependency {name} tree[{index}]")
        _exact_keys(
            tree_record,
            {"method", "root_id", "relative_path", "entries", "upstream_tree_sha256"},
            f"dependency {name} extracted tree",
        )
        method = _string(tree_record["method"], "dependency method")
        if method not in ("optimized", "reference") or (
            previous is not None and method <= previous
        ):
            raise VerificationError(f"dependency {name} methods are not sorted and exact")
        previous = method
        root_id = _string(tree_record["root_id"], "dependency tree root_id")
        expected_root = f"{method}-source" if name == "eigen" else f"{method}-build"
        if root_id != expected_root:
            raise VerificationError(f"dependency {name} tree uses the wrong root")
        relative = _relative_path(tree_record["relative_path"], "dependency source root")
        if name != "eigen" and not relative.startswith("_deps/"):
            raise VerificationError(f"dependency {name} is not populated inside its fresh build root")
        entries = _identity_sequence(
            tree_record["entries"], f"dependency {name} {method} entries", required_root_id=root_id
        )
        if not entries:
            raise VerificationError(f"dependency {name} {method} tree is empty")
        if "regular_file_count" in expected and (
            len(entries) != expected["regular_file_count"]
            or sum(entry["bytes"] for entry in entries) != expected["regular_bytes"]
        ):
            raise VerificationError(f"dependency {name} selected-tree counts differ")
        upstream_sha = _sha256(
            tree_record["upstream_tree_sha256"], f"dependency {name} upstream tree"
        )
        if upstream_sha != expected["tree_sha256"]:
            raise VerificationError(f"dependency {name} upstream tree differs from offline manifest")
        if _offline_tree_sha256(entries, relative) != upstream_sha:
            raise VerificationError(f"dependency {name} extracted bytes differ from upstream tree")
        for entry in entries:
            _authenticate_identity(entry, roots, root_fds, f"dependency {name} extracted file")
            authenticated.add(_identity_key(entry))
        trees[method] = (root_id, relative, entries)
    if set(trees) != {"reference", "optimized"}:
        raise VerificationError(f"dependency {name} lacks both method trees")

    source_roots_raw = _sequence(
        record["cmake_source_root"], f"dependency {name}.cmake_source_root"
    )
    if len(source_roots_raw) != 2:
        raise VerificationError(f"dependency {name} must have two CMake source roots")
    source_roots: dict[str, tuple[str, str]] = {}
    previous = None
    for item in source_roots_raw:
        source = _mapping(item, f"dependency {name} CMake source root")
        _exact_keys(source, {"method", "root_id", "relative_path"}, "CMake source root")
        method = _string(source["method"], "CMake source-root method")
        if method not in ("optimized", "reference") or (
            previous is not None and method <= previous
        ):
            raise VerificationError("dependency CMake source roots are not sorted and exact")
        previous = method
        root_id = _string(source["root_id"], "CMake source-root root_id")
        relative = _relative_path(source["relative_path"], "CMake source-root path")
        source_roots[method] = (root_id, relative)
    if any(source_roots[method] != trees[method][:2] for method in trees):
        raise VerificationError(f"dependency {name} CMake roots differ from extracted trees")
    nested = _sequence(record["nested_dependencies"], f"dependency {name}.nested_dependencies")
    if nested:
        raise VerificationError(f"dependency {name} has unregistered nested dependencies")
    return authenticated


def _verify_dependencies(
    value: Any,
    identities: Mapping[str, Mapping[str, Any]],
    identity_raw: Mapping[str, bytes],
    roots: Mapping[str, Mapping[str, Any]],
    root_fds: Mapping[str, int],
) -> dict[str, Any]:
    dependencies = _mapping(value, "dependencies")
    _exact_keys(
        dependencies,
        {
            "offline_build_inputs_manifest",
            "eigen_cmake_config",
            "eigen_cmake_config_version",
            "eigen",
            "manif",
            "raylib",
        },
        "dependencies",
    )
    offline_identity = _validate_file_identity(
        dependencies["offline_build_inputs_manifest"], "dependencies offline manifest"
    )
    if offline_identity["relative_path"] != "thirdparty/dist/offline_build_inputs_v2.json":
        raise VerificationError("offline v2 manifest path differs")
    if offline_identity != identities["offline_build_inputs_manifest"]:
        raise VerificationError("dependencies offline manifest differs from identity binding")
    offline = _mapping(
        _parse_json(identity_raw["offline_build_inputs_manifest"], "offline input manifest"),
        "offline input manifest",
    )
    _exact_keys(
        offline,
        {
            "classification",
            "cmake",
            "limits",
            "predecessor",
            "schema_version",
            "tree_digest",
            "vendored_eigen",
        },
        "offline input manifest",
    )
    if offline.get("schema_version") != "dancers-offline-build-inputs-v2":
        raise VerificationError("offline input manifest schema differs")
    expected_offline_classification = {
        "eligible_as_build_input": True,
        "execution_eligible": False,
        "offline_build_inputs_authenticated": True,
        "provenance_complete": True,
        "scientific_execution_authorized": False,
    }
    if not _same_json_type_and_value(
        offline.get("classification"), expected_offline_classification
    ):
        raise VerificationError(
            "offline inputs are authenticated but not semantically complete/eligible"
        )
    if not _same_json_type_and_value(offline["limits"], OFFLINE_V2_LIMITS):
        raise VerificationError("offline v2 limit profile differs")
    if offline["tree_digest"] != {
        "algorithm": "sha256",
        "domain": "dancers-offline-build-input-tree-v1",
        "entry_encoding": (
            "sha256(domain_utf8_nul || for each UTF-8 bytewise-sorted regular file: "
            "'file\\0' || uint64be(path_bytes) || path || uint64be(file_bytes) || "
            "sha256(file_bytes))"
        ),
    }:
        raise VerificationError("offline v2 tree-digest profile differs")
    predecessor = _mapping(offline.get("predecessor"), "offline v2 predecessor")
    _exact_keys(
        predecessor, {"manifest", "schema_version", "verifier"}, "offline v2 predecessor"
    )
    if predecessor["schema_version"] != "dancers-offline-build-inputs-v1":
        raise VerificationError("offline v2 predecessor schema differs")
    predecessor_manifest_identity = _offline_repository_identity(
        predecessor["manifest"], "offline v1 predecessor manifest"
    )
    predecessor_verifier_identity = _offline_repository_identity(
        predecessor["verifier"], "offline v1 predecessor verifier"
    )
    if (
        predecessor_manifest_identity["relative_path"]
        != "thirdparty/dist/offline_build_inputs_v1.json"
        or predecessor_verifier_identity["relative_path"]
        != "scripts/comparison/verify_offline_build_inputs_v1.py"
    ):
        raise VerificationError("offline v2 predecessor paths differ")
    predecessor_raw = _authenticate_identity(
        predecessor_manifest_identity, roots, root_fds, "offline v1 predecessor manifest"
    )
    _authenticate_identity(
        predecessor_verifier_identity, roots, root_fds, "offline v1 predecessor verifier"
    )
    predecessor_value = _mapping(
        _parse_json(predecessor_raw, "offline v1 predecessor manifest"),
        "offline v1 predecessor manifest",
    )
    if predecessor_value.get("schema_version") != "dancers-offline-build-inputs-v1":
        raise VerificationError("offline predecessor bytes have the wrong schema")
    predecessor_classification = _mapping(
        predecessor_value.get("classification"), "offline v1 predecessor classification"
    )
    if (
        predecessor_classification.get("offline_build_inputs_authenticated") is not True
        or predecessor_classification.get("scientific_execution_authorized") is not False
    ):
        raise VerificationError("offline v1 predecessor is not authenticated/non-authorizing")
    archives_raw = _sequence(
        predecessor_value.get("archives"), "offline predecessor archives"
    )
    archives: dict[str, dict[str, Any]] = {}
    for item in archives_raw:
        archive = _mapping(item, "offline input archive")
        name = _string(archive.get("name"), "offline archive name")
        if name in archives:
            raise VerificationError("offline input manifest has duplicate archive name")
        archive_identity = _offline_repository_identity(
            archive.get("archive"), f"offline archive {name} identity"
        )
        source = _mapping(archive.get("source"), f"offline archive {name} source")
        archives[name] = {
            "archive": archive_identity,
            "version": _string(archive.get("version"), f"offline archive {name} version"),
            "immutable_revision": _string(
                source.get("upstream_commit"), f"offline archive {name} revision"
            ),
            "tree_sha256": _sha256(
                archive.get("tree_sha256"), f"offline archive {name} tree SHA"
            ),
        }
    if set(archives) != {"manif", "raylib"}:
        raise VerificationError("offline input archive set differs")
    required_archive_values = {
        "manif": ("0.0.5", "c49fec8150e62821d9be7e2a3ac49db75bf7bb92"),
        "raylib": ("5.5", "c1ab645ca298a2801097931d1079b10ff7eb9df8"),
    }
    for name, (version, revision) in required_archive_values.items():
        if (archives[name]["version"], archives[name]["immutable_revision"]) != (
            version,
            revision,
        ):
            raise VerificationError(f"offline {name} immutable identity differs")
    expected_archive_paths = {
        "manif": "thirdparty/dist/manif-0.0.5-build-source.tar.gz",
        "raylib": "thirdparty/dist/raylib-5.5-build-source.tar.gz",
    }
    if any(
        archives[name]["archive"]["relative_path"] != expected_archive_paths[name]
        for name in expected_archive_paths
    ):
        raise VerificationError("offline dependency archive path differs")
    eigen_offline = _mapping(offline.get("vendored_eigen"), "offline vendored Eigen")
    _exact_keys(
        eigen_offline,
        {
            "config",
            "config_version",
            "licenses",
            "normalized_repository_modes",
            "provenance_complete",
            "root",
            "selected_tree",
            "upstream_archive",
            "version",
        },
        "offline vendored Eigen",
    )
    if eigen_offline["normalized_repository_modes"] != {
        "directory": 0o755,
        "regular": [0o644, 0o755],
    } or eigen_offline["root"] != "thirdparty/Eigen":
        raise VerificationError("offline Eigen root or normalized modes differ")
    if eigen_offline.get("version") != "3.4.0" or eigen_offline.get(
        "provenance_complete"
    ) is not True:
        raise VerificationError("offline Eigen provenance is incomplete")
    selected_tree = _mapping(eigen_offline.get("selected_tree"), "offline Eigen selected tree")
    _exact_keys(
        selected_tree,
        {"archive_path", "regular_bytes", "regular_file_count", "sha256"},
        "offline Eigen selected tree",
    )
    if (
        selected_tree["archive_path"] != "Eigen"
        or selected_tree["regular_file_count"] != 337
        or selected_tree["regular_bytes"] != 6_115_017
    ):
        raise VerificationError("offline Eigen selected-tree surface differs")
    eigen_tree = _sha256(selected_tree["sha256"], "offline Eigen tree SHA")
    upstream_archive = _mapping(
        eigen_offline.get("upstream_archive"), "offline Eigen upstream archive"
    )
    _exact_keys(
        upstream_archive,
        {
            "archive",
            "directory_count",
            "format",
            "full_tree",
            "member_count",
            "regular_file_count",
            "root_directory",
            "source",
            "tar_stream_bytes",
            "uncompressed_regular_bytes",
        },
        "offline Eigen upstream archive",
    )
    _exact_keys(
        _mapping(upstream_archive["format"], "offline Eigen archive format"),
        {"directory_mode", "file_modes", "gid", "gname", "mtime", "pax_comment", "uid", "uname"},
        "offline Eigen archive format",
    )
    full_tree = _mapping(upstream_archive["full_tree"], "offline Eigen full tree")
    _exact_keys(
        full_tree,
        {"regular_bytes", "regular_file_count", "sha256"},
        "offline Eigen full tree",
    )
    _sha256(full_tree["sha256"], "offline Eigen full-tree SHA")
    upstream_source = _mapping(
        upstream_archive.get("source"), "offline Eigen upstream source"
    )
    _exact_keys(
        upstream_source,
        {"repository", "tag", "upstream_commit"},
        "offline Eigen upstream source",
    )
    if (
        upstream_source.get("repository") != "gitlab.com/libeigen/eigen"
        or upstream_source.get("tag") != "3.4.0"
        or upstream_source.get("upstream_commit")
        != "3147391d946bb4b6c68edd901f2add6ac1f31f8c"
        or upstream_archive.get("root_directory")
        != "eigen-3147391d946bb4b6c68edd901f2add6ac1f31f8c"
    ):
        raise VerificationError("offline Eigen upstream source identity differs")
    eigen_archive = _offline_repository_identity(
        upstream_archive.get("archive"), "offline Eigen upstream archive identity"
    )
    if (
        eigen_archive["relative_path"]
        != "thirdparty/dist/eigen-3.4.0-upstream-3147391d.tar.gz"
    ):
        raise VerificationError("offline Eigen archive path differs")
    _authenticate_identity(eigen_archive, roots, root_fds, "offline Eigen upstream archive")
    eigen_config = _validate_file_identity(
        dependencies["eigen_cmake_config"], "Eigen CMake config"
    )
    offline_config = _offline_repository_identity(
        eigen_offline.get("config"), "offline Eigen config"
    )
    if eigen_config != offline_config:
        raise VerificationError("Eigen CMake config differs from offline manifest")
    _authenticate_identity(eigen_config, roots, root_fds, "Eigen CMake config")
    if eigen_config["relative_path"] != "thirdparty/Eigen3Config.cmake":
        raise VerificationError("Eigen CMake config path differs")
    eigen_config_version = _validate_file_identity(
        dependencies["eigen_cmake_config_version"], "Eigen CMake config-version"
    )
    offline_config_version = _offline_repository_identity(
        eigen_offline.get("config_version"), "offline Eigen config-version"
    )
    if eigen_config_version != offline_config_version:
        raise VerificationError("Eigen CMake config-version differs from offline manifest")
    _authenticate_identity(
        eigen_config_version, roots, root_fds, "Eigen CMake config-version"
    )
    if eigen_config_version["relative_path"] != "thirdparty/Eigen3ConfigVersion.cmake":
        raise VerificationError("Eigen CMake config-version path differs")
    authenticated = {
        _identity_key(offline_identity),
        _identity_key(eigen_config),
        _identity_key(eigen_config_version),
        _identity_key(eigen_archive),
        _identity_key(predecessor_manifest_identity),
        _identity_key(predecessor_verifier_identity),
    }
    expected_license_names = [
        "COPYING.APACHE",
        "COPYING.BSD",
        "COPYING.GPL",
        "COPYING.LGPL",
        "COPYING.MINPACK",
        "COPYING.MPL2",
        "COPYING.README",
    ]
    licenses = _sequence(eigen_offline.get("licenses"), "offline Eigen licenses")
    if len(licenses) != len(expected_license_names):
        raise VerificationError("offline Eigen license closure is not the exact seven files")
    for index, (license_value, expected_name) in enumerate(
        zip(licenses, expected_license_names, strict=True)
    ):
        license_record = _mapping(license_value, f"offline Eigen license[{index}]")
        _exact_keys(license_record, {"archive_path", "file"}, "offline Eigen license")
        if license_record["archive_path"] != expected_name:
            raise VerificationError("offline Eigen licenses are not in frozen archive order")
        license_identity = _offline_repository_identity(
            license_record["file"], f"offline Eigen license {expected_name}"
        )
        if license_identity["relative_path"] != f"thirdparty/{expected_name}":
            raise VerificationError("offline Eigen license repository path differs")
        _authenticate_identity(
            license_identity, roots, root_fds, f"offline Eigen license {expected_name}"
        )
        authenticated.add(_identity_key(license_identity))
    expected_records = {
        "eigen": {
            "source_kind": "vendored-tree",
            "version": "3.4.0",
            "immutable_revision": "3147391d946bb4b6c68edd901f2add6ac1f31f8c",
            "tree_sha256": eigen_tree,
            "archive": eigen_archive,
            "regular_file_count": selected_tree["regular_file_count"],
            "regular_bytes": selected_tree["regular_bytes"],
        },
        "manif": {**archives["manif"], "source_kind": "offline-archive"},
        "raylib": {**archives["raylib"], "source_kind": "offline-archive"},
    }
    for name in ("eigen", "manif", "raylib"):
        authenticated.update(
            _verify_dependency_record(
                name, dependencies[name], expected_records[name], roots, root_fds
            )
        )
    return {
        "authenticated": authenticated,
        "configure_inputs": [eigen_config, eigen_config_version],
    }


def _stream_record(value: Any, label: str) -> None:
    record = _mapping(value, label)
    _exact_keys(record, {"bytes", "sha256", "utf8"}, label)
    text = _bounded_text(record["utf8"], f"{label}.utf8")
    raw = text.encode("utf-8")
    if len(raw) != _integer(record["bytes"], f"{label}.bytes"):
        raise VerificationError(f"{label}.bytes differs from UTF-8 bytes")
    if sha256_bytes(raw) != _sha256(record["sha256"], f"{label}.sha256"):
        raise VerificationError(f"{label}.sha256 differs from UTF-8 bytes")


def _action_identity_list(
    value: Any,
    label: str,
) -> list[dict[str, Any]]:
    return _identity_sequence(value, label)


def _verify_action_ledger(
    value: Any,
    identities: Mapping[str, Mapping[str, Any]],
    roots: Mapping[str, Mapping[str, Any]],
    root_fds: Mapping[str, int],
    tools: Mapping[str, Mapping[str, Any]],
    environments: Mapping[str, Mapping[str, str]],
) -> dict[str, Any]:
    ledger = _mapping(value, "action_ledger")
    _exact_keys(
        ledger,
        {"schema_version", "network_session_id", "wrapper", "actions", "ledger_sha256"},
        "action_ledger",
    )
    if ledger["schema_version"] != ACTION_LEDGER_SCHEMA:
        raise VerificationError("action ledger schema differs")
    session_id = _string(ledger["network_session_id"], "action ledger session ID")
    wrapper = _validate_file_identity(ledger["wrapper"], "action ledger wrapper")
    if wrapper != identities["action_wrapper"]:
        raise VerificationError("action ledger wrapper differs from identity binding")
    _authenticate_identity(wrapper, roots, root_fds, "action ledger wrapper")
    actions_raw = _sequence(ledger["actions"], "action ledger actions")
    if not actions_raw or len(actions_raw) > MAX_ACTIONS:
        raise VerificationError("action count is empty or exceeds its bound")
    actions: list[dict[str, Any]] = []
    actions_by_id: dict[str, dict[str, Any]] = {}
    produced: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
    tool_keys = {_identity_key(tool["file"]) for tool in tools.values()}
    final_root_entries: dict[tuple[str, str], dict[str, Any]] = {
        (entry["root_id"], entry["relative_path"]): entry
        for root in roots.values()
        for entry in root["entries"]
    }

    for index, item in enumerate(actions_raw):
        action = _mapping(item, f"action[{index}]")
        _exact_keys(action, ACTION_KEYS, f"action[{index}]")
        sequence_index = _integer(
            action["sequence_index"], f"action[{index}].sequence_index"
        )
        if sequence_index != index:
            raise VerificationError("action sequence_index is not exact contiguous order")
        supplied_id = _sha256(action["action_id"], f"action[{index}].action_id")
        without_id = {key: action[key] for key in ACTION_KEYS_WITHOUT_ID}
        if action_id(without_id) != supplied_id:
            raise VerificationError(f"action[{index}] ID differs from complete canonical action")
        if supplied_id in actions_by_id:
            raise VerificationError("action IDs are not unique")
        if action["kind"] not in ALLOWED_ACTION_KINDS:
            raise VerificationError(f"action[{index}] kind is not registered")
        method = _string(action["method"], f"action[{index}].method")
        if method not in ("common", "optimized", "reference"):
            raise VerificationError(f"action[{index}] method differs")
        _string(action["target"], f"action[{index}].target")
        cwd_root = _string(action["cwd_root_id"], f"action[{index}].cwd_root_id")
        if cwd_root not in roots:
            raise VerificationError(f"action[{index}] cwd root is unknown")
        _relative_path(
            action["cwd_relative_path"], f"action[{index}].cwd_relative_path", allow_dot=True
        )
        executable = _validate_file_identity(action["executable"], f"action[{index}] executable")
        if _identity_key(executable) not in tool_keys:
            raise VerificationError(f"action[{index}] executable is not a registered tool")
        _authenticate_identity(executable, roots, root_fds, f"action[{index}] executable")
        argv_raw = _sequence(action["argv"], f"action[{index}].argv")
        if not argv_raw or len(argv_raw) > MAX_ARGV_ITEMS:
            raise VerificationError(f"action[{index}] argv is empty or exceeds its bound")
        argv = [
            _bounded_text(arg, f"action[{index}] argv[{arg_index}]", MAX_ARG_BYTES)
            for arg_index, arg in enumerate(argv_raw)
        ]
        executable_path = os.path.join(
            roots[executable["root_id"]]["canonical_absolute_path"],
            executable["relative_path"],
        )
        if argv[0] != executable_path:
            raise VerificationError(f"action[{index}] argv[0] differs from executable")
        environment = _string(action["environment_id"], f"action[{index}] environment")
        if environment not in environments:
            raise VerificationError(f"action[{index}] names an unknown environment")
        if action["stdin"] != "closed":
            raise VerificationError(f"action[{index}] stdin was not closed")
        if _integer(action["exit_code"], f"action[{index}].exit_code") != 0:
            raise VerificationError(f"action[{index}] did not exit successfully")
        if action["network_session_id"] != session_id:
            raise VerificationError(f"action[{index}] network session differs")
        _stream_record(action["stdout"], f"action[{index}].stdout")
        _stream_record(action["stderr"], f"action[{index}].stderr")

        response_raw = _sequence(action["response_files"], f"action[{index}].response_files")
        response_files: list[dict[str, Any]] = []
        response_expansions: dict[str, list[str]] = {}
        for response_index, response_value in enumerate(response_raw):
            response = _mapping(
                response_value, f"action[{index}].response_files[{response_index}]"
            )
            _exact_keys(response, {"argument", "file"}, "response file")
            argument = _bounded_text(response["argument"], "response-file argument", MAX_ARG_BYTES)
            identity = _validate_file_identity(response["file"], "response-file identity")
            expected_argument = "@" + os.path.join(
                roots[identity["root_id"]]["canonical_absolute_path"],
                identity["relative_path"],
            ) if identity["root_id"] in roots else ""
            if argument not in argv or argument != expected_argument:
                raise VerificationError("response-file argument is not literal in argv")
            response_bytes = _authenticate_identity(
                identity, roots, root_fds, f"action[{index}] response file"
            )
            if len(response_bytes) > MAX_ACTION_IO_BYTES:
                raise VerificationError("response-file bytes exceed action-I/O cap")
            try:
                response_text = response_bytes.decode("utf-8")
                expansion = shlex.split(response_text, posix=True)
            except (UnicodeDecodeError, ValueError) as exc:
                raise VerificationError("response file is not bounded POSIX UTF-8 argv") from exc
            if not expansion or len(expansion) > MAX_ARGV_ITEMS or any(
                not token or "\x00" in token or len(token.encode("utf-8")) > MAX_ARG_BYTES
                for token in expansion
            ):
                raise VerificationError("response-file expansion is empty or exceeds argv caps")
            if any(token.startswith("@") for token in expansion):
                raise VerificationError("nested response files are not permitted")
            response_expansions[argument] = expansion
            response_files.append({"argument": argument, "file": identity})
        expanded_argv: list[str] = []
        for argument in argv:
            expanded_argv.extend(response_expansions.get(argument, [argument]))
            if len(expanded_argv) > MAX_ARGV_ITEMS:
                raise VerificationError(f"action[{index}] expanded argv exceeds item cap")
        inputs = {
            "response": [item["file"] for item in response_files],
            "declared": _action_identity_list(
                action["declared_inputs"], f"action[{index}].declared_inputs"
            ),
            "discovered": _action_identity_list(
                action["discovered_inputs"], f"action[{index}].discovered_inputs"
            ),
        }
        for response in response_files:
            if response["file"] not in inputs["declared"] + inputs["discovered"]:
                raise VerificationError(
                    f"action[{index}] response-file bytes are absent from input closure"
                )
        combined_input_keys: dict[tuple[str, str], dict[str, Any]] = {}
        for category, input_values in inputs.items():
            for input_identity in input_values:
                key = (input_identity["root_id"], input_identity["relative_path"])
                if key in combined_input_keys and combined_input_keys[key] != input_identity:
                    raise VerificationError(f"action[{index}] gives one input two identities")
                combined_input_keys[key] = input_identity
                if key in produced:
                    producer_index, producer_identity = produced[key]
                    if producer_index >= index or producer_identity != input_identity:
                        raise VerificationError(f"action[{index}] generated input differs")
                else:
                    if input_identity["root_id"] in {"reference-build", "optimized-build"}:
                        raise VerificationError(
                            f"action[{index}] consumes an unproduced build-root input"
                        )
                    if final_root_entries.get(key) != input_identity:
                        raise VerificationError(f"action[{index}] input is not authenticated")
                _authenticate_identity(
                    input_identity, roots, root_fds, f"action[{index}] {category} input"
                )
        outputs = _action_identity_list(action["outputs"], f"action[{index}].outputs")
        if not outputs:
            raise VerificationError(f"action[{index}] has no registered output")
        for output in outputs:
            key = (output["root_id"], output["relative_path"])
            if output["root_id"] not in {"reference-build", "optimized-build"}:
                raise VerificationError(f"action[{index}] output is outside fresh build roots")
            if method in ("reference", "optimized") and output["root_id"] != f"{method}-build":
                raise VerificationError(f"action[{index}] writes the other method build root")
            if key in produced:
                raise VerificationError(f"action output {key} has multiple producers")
            if final_root_entries.get(key) != output:
                raise VerificationError(f"action[{index}] output differs from final tree")
            _authenticate_identity(output, roots, root_fds, f"action[{index}] output")
            produced[key] = (index, output)
        normalized = {
            **action,
            "executable": executable,
            "argv": argv,
            "expanded_argv": expanded_argv,
            "response_files": response_files,
            "declared_inputs": inputs["declared"],
            "discovered_inputs": inputs["discovered"],
            "outputs": outputs,
        }
        actions.append(normalized)
        actions_by_id[supplied_id] = normalized

    expected_build_outputs = {
        (entry["root_id"], entry["relative_path"])
        for root_id in ("reference-build", "optimized-build")
        for entry in roots[root_id]["entries"]
    }
    if set(produced) != expected_build_outputs:
        raise VerificationError("action outputs do not cover both complete final build trees")
    expected_ledger = _sha256(ledger["ledger_sha256"], "action ledger SHA")
    if action_ledger_sha256(actions_raw) != expected_ledger:
        raise VerificationError("action ledger SHA differs from ordered complete records")
    return {
        "session_id": session_id,
        "ledger_sha256": expected_ledger,
        "actions": actions,
        "actions_by_id": actions_by_id,
        "produced": produced,
    }


def _verify_canaries(value: Any, label: str) -> None:
    canaries = _sequence(value, label)
    expected = [
        {
            "address": "127.0.0.1",
            "errno": "EPERM",
            "family": "AF_INET",
            "operation": "connect",
            "outcome": "denied",
            "port": 9,
            "transport": "SOCK_STREAM",
        },
        {
            "address": "::1",
            "errno": "EPERM",
            "family": "AF_INET6",
            "operation": "connect",
            "outcome": "denied",
            "port": 9,
            "transport": "SOCK_STREAM",
        },
    ]
    if not _same_json_type_and_value(canaries, expected):
        raise VerificationError(
            f"{label} differs from exact local-only denied IPv4/IPv6 canaries"
        )


def _verify_network_isolation(
    value: Any,
    ledger: Mapping[str, Any],
    identities: Mapping[str, Mapping[str, Any]],
    roots: Mapping[str, Mapping[str, Any]],
    root_fds: Mapping[str, int],
    trust: Mapping[str, Any],
    tools: Mapping[str, Mapping[str, Any]],
) -> None:
    receipt = _mapping(value, "network_isolation")
    _exact_keys(
        receipt,
        {
            "schema_version",
            "epoch_id",
            "session_id",
            "backend",
            "host_identity",
            "executor",
            "guard",
            "policy",
            "root_process",
            "monotonic_interval",
            "action_ledger_sha256",
            "process_ledger",
            "preflight_canaries",
            "postflight_canaries",
            "network_device_or_policy_state",
            "outcome",
        },
        "network isolation receipt",
    )
    if receipt["schema_version"] != NETWORK_RECEIPT_SCHEMA or receipt["epoch_id"] != EPOCH_ID:
        raise VerificationError("network isolation receipt schema or epoch differs")
    if receipt["session_id"] != ledger["session_id"]:
        raise VerificationError("network receipt session differs from action ledger")
    if receipt["backend"] != trust["network_backend"] or receipt["outcome"] != "enforced":
        raise VerificationError("network backend or enforced outcome differs")
    if receipt["action_ledger_sha256"] != ledger["ledger_sha256"]:
        raise VerificationError("network receipt does not bind finalized action ledger")
    identity_cross = {
        "executor": trust["identities"]["hermetic_build_executor"],
        "guard": trust["identities"]["network_guard"],
        "policy": trust["identities"]["network_policy"],
    }
    for field, expected in identity_cross.items():
        identity = _validate_file_identity(receipt[field], f"network receipt {field}")
        if identity != expected:
            raise VerificationError(f"network receipt {field} differs from trust root")
        _authenticate_identity(identity, roots, root_fds, f"network receipt {field}")
    host_identity = _validate_file_identity(
        receipt["host_identity"], "network receipt host_identity"
    )
    if host_identity == trust["identities"]["permitted_host_os_mechanism"]:
        raise VerificationError("host identity must be distinct from OS isolation mechanism")
    _authenticate_identity(host_identity, roots, root_fds, "network receipt host_identity")
    if receipt["executor"] != identities["build_executor"]:
        raise VerificationError("network executor differs from build-executor binding")

    interval = _mapping(receipt["monotonic_interval"], "network monotonic interval")
    _exact_keys(interval, {"start_ns", "end_ns"}, "network monotonic interval")
    start = _integer(interval["start_ns"], "network interval start")
    end = _integer(interval["end_ns"], "network interval end")
    if end <= start:
        raise VerificationError("network protected interval is empty or reversed")
    root_process = _mapping(receipt["root_process"], "network root process")
    _exact_keys(
        root_process,
        {"pid", "parent_pid", "executable", "wrapper", "start_ns", "end_ns", "exit_code"},
        "network root process",
    )
    root_pid = _integer(root_process["pid"], "root process pid", 1)
    _integer(root_process["parent_pid"], "root process parent pid", 1)
    root_start = _integer(root_process["start_ns"], "root process start")
    root_end = _integer(root_process["end_ns"], "root process end")
    root_exit = _integer(root_process["exit_code"], "root process exit_code")
    if root_process["executable"] != receipt["executor"] or root_process["wrapper"] != identities[
        "action_wrapper"
    ]:
        raise VerificationError("network root process identities differ")
    if (
        root_start < start
        or root_end > end
        or root_end <= root_start
        or root_exit != 0
    ):
        raise VerificationError("network root process lies outside protected interval or failed")

    processes = _sequence(receipt["process_ledger"], "network process ledger")
    if len(processes) < 2 * len(ledger["actions"]) or len(processes) > 4 * MAX_ACTIONS:
        raise VerificationError("process ledger cannot cover every wrapper/child or exceeds cap")
    wrappers_by_action: dict[str, int] = {}
    children_by_action: set[str] = set()
    seen_pids: set[int] = set()
    known_parents: set[int] = {root_pid}
    intervals_by_pid: dict[int, tuple[int, int]] = {
        root_pid: (root_start, root_end)
    }
    action_by_pid: dict[int, str | None] = {root_pid: None}
    kind_by_pid: dict[int, str] = {root_pid: "executor-root"}
    previous_start = -1
    tool_keys = {_identity_key(tool["file"]) for tool in tools.values()}
    for index, item in enumerate(processes):
        process = _mapping(item, f"network process[{index}]")
        _exact_keys(
            process,
            {
                "process_index",
                "process_kind",
                "pid",
                "parent_pid",
                "action_id",
                "executable",
                "start_ns",
                "end_ns",
                "exit_code",
            },
            "network process record",
        )
        process_index = _integer(process["process_index"], "process index")
        if process_index != index:
            raise VerificationError("process ledger indices are not contiguous")
        pid = _integer(process["pid"], "process pid", 1)
        parent = _integer(process["parent_pid"], "process parent pid", 1)
        if pid in seen_pids or parent not in known_parents:
            raise VerificationError("process ledger pid/parent relation differs")
        seen_pids.add(pid)
        known_parents.add(pid)
        action_key = _sha256(process["action_id"], "process action_id")
        if action_key not in ledger["actions_by_id"]:
            raise VerificationError("process ledger action mapping is unknown")
        action = ledger["actions_by_id"][action_key]
        process_kind = _string(process["process_kind"], "process kind")
        executable = _validate_file_identity(process["executable"], "process executable")
        _authenticate_identity(executable, roots, root_fds, "process executable")
        if process_kind == "action-wrapper":
            if action_key in wrappers_by_action or executable != identities["action_wrapper"]:
                raise VerificationError("action wrapper process is duplicate or untrusted")
            if parent != root_pid:
                raise VerificationError("action wrapper is not a direct executor child")
            wrappers_by_action[action_key] = pid
            action_by_pid[pid] = action_key
            kind_by_pid[pid] = process_kind
        elif process_kind == "action-child":
            if action_key in children_by_action or executable != action["executable"]:
                raise VerificationError("action child process is duplicate or differs")
            if wrappers_by_action.get(action_key) != parent:
                raise VerificationError("action child is not descended from its wrapper")
            children_by_action.add(action_key)
            action_by_pid[pid] = action_key
            kind_by_pid[pid] = process_kind
        elif process_kind == "registered-helper":
            if _identity_key(executable) not in tool_keys:
                raise VerificationError("helper executable is not in authenticated tool closure")
            if action_key not in wrappers_by_action:
                raise VerificationError("helper precedes its registered action wrapper")
            if action_by_pid.get(parent) != action_key or kind_by_pid.get(parent) not in {
                "action-child",
                "registered-helper",
            }:
                raise VerificationError("helper crosses action ancestry or has invalid parent")
            action_by_pid[pid] = action_key
            kind_by_pid[pid] = process_kind
        else:
            raise VerificationError("process kind is not registered")
        process_start = _integer(process["start_ns"], "process start")
        process_end = _integer(process["end_ns"], "process end")
        parent_start, parent_end = intervals_by_pid[parent]
        if (
            process_start < start
            or process_end > end
            or process_end <= process_start
            or process_start < parent_start
            or process_end > parent_end
            or process_start < previous_start
            or _integer(process["exit_code"], "process exit_code") != 0
        ):
            raise VerificationError("process interval/parent nesting/order/exit differs")
        intervals_by_pid[pid] = (process_start, process_end)
        previous_start = process_start
    expected_actions = set(ledger["actions_by_id"])
    if set(wrappers_by_action) != expected_actions or children_by_action != expected_actions:
        raise VerificationError("process ledger omits an action wrapper or child")
    _verify_canaries(receipt["preflight_canaries"], "preflight canaries")
    _verify_canaries(receipt["postflight_canaries"], "postflight canaries")
    state = _mapping(
        receipt["network_device_or_policy_state"], "network/device policy state"
    )
    _exact_keys(
        state,
        {
            "interfaces_before_sha256",
            "interfaces_after_sha256",
            "policy_state_sha256",
            "preflight_empty_build_roots",
            "postflight_build_tree_sha256",
        },
        "network/device policy state",
    )
    for field in ("interfaces_before_sha256", "interfaces_after_sha256", "policy_state_sha256"):
        _sha256(state[field], f"network state {field}")
    if state["interfaces_before_sha256"] != state["interfaces_after_sha256"]:
        raise VerificationError("network interface state changed during build")
    if state["preflight_empty_build_roots"] != ["optimized-build", "reference-build"]:
        raise VerificationError("receipt does not bind both initially empty build roots")
    expected_post = {
        root_id: roots[root_id]["tree_sha256"]
        for root_id in ("optimized-build", "reference-build")
    }
    if state["postflight_build_tree_sha256"] != expected_post:
        raise VerificationError("network receipt postflight build trees differ")


def object_id(object_without_object_id: Mapping[str, Any]) -> str:
    value = dict(object_without_object_id)
    if set(value) != OBJECT_KEYS - {"object_id"}:
        raise VerificationError("object_without_object_id has the wrong fields")
    return sha256_bytes(OBJECT_ID_DOMAIN + canonical_json_bytes(value))


def _verify_object_graph(
    value: Any,
    ledger: Mapping[str, Any],
    environments: Mapping[str, Mapping[str, str]],
    roots: Mapping[str, Mapping[str, Any]],
    root_fds: Mapping[str, int],
) -> dict[str, Any]:
    graph = _mapping(value, "object_graph")
    _exact_keys(
        graph,
        {"schema_version", "dependency_closures", "objects", "aggregates"},
        "object_graph",
    )
    if graph["schema_version"] != OBJECT_GRAPH_SCHEMA:
        raise VerificationError("object graph schema differs")
    closures_raw = _sequence(graph["dependency_closures"], "dependency closures")
    closures: dict[str, list[dict[str, Any]]] = {}
    previous: str | None = None
    for index, item in enumerate(closures_raw):
        record = _mapping(item, f"dependency closure[{index}]")
        _exact_keys(record, {"dependency_closure_sha256", "inputs"}, "dependency closure")
        entries = _identity_sequence(record["inputs"], "dependency closure inputs")
        if not entries:
            raise VerificationError("object dependency closure is empty")
        digest = _sha256(
            record["dependency_closure_sha256"], "dependency closure SHA-256"
        )
        if dependency_closure_sha256(entries) != digest:
            raise VerificationError("dependency closure SHA differs from exact ordered inputs")
        if previous is not None and digest <= previous:
            raise VerificationError("dependency closures are not sorted and unique")
        previous = digest
        closures[digest] = entries
    if not closures:
        raise VerificationError("object graph has no dependency closure")

    objects_raw = _sequence(graph["objects"], "object graph objects")
    if not objects_raw or len(objects_raw) > MAX_OBJECTS:
        raise VerificationError("object count is empty or exceeds its bound")
    objects: dict[str, dict[str, Any]] = {}
    previous = None
    for index, item in enumerate(objects_raw):
        record = _mapping(item, f"object[{index}]")
        _exact_keys(record, OBJECT_KEYS, f"object[{index}]")
        supplied_id = _sha256(record["object_id"], f"object[{index}].object_id")
        without_id = {key: record[key] for key in OBJECT_KEYS - {"object_id"}}
        if object_id(without_id) != supplied_id:
            raise VerificationError(f"object[{index}] ID differs from complete object record")
        if previous is not None and supplied_id <= previous:
            raise VerificationError("objects are not sorted and unique by object_id")
        previous = supplied_id
        role = _string(record["role"], f"object[{index}].role")
        method = _string(record["method"], f"object[{index}].method")
        if method not in ("common", "optimized", "reference"):
            raise VerificationError(f"object[{index}] method differs")
        sharing = _string(record["sharing_scope"], f"object[{index}].sharing_scope")
        allowed_sharing = {
            "all-products",
            "method-products",
            "fidelity-only",
            "private-intermediate",
        }
        if sharing not in allowed_sharing:
            raise VerificationError(f"object[{index}] sharing scope differs")
        source = _validate_file_identity(record["source"], f"object[{index}] source")
        if source["root_id"] in {"reference-build", "optimized-build"}:
            raise VerificationError(f"object[{index}] source is generated rather than source-pinned")
        _authenticate_identity(source, roots, root_fds, f"object[{index}] source")
        producer_id = _sha256(
            record["producer_action_id"], f"object[{index}] producer action"
        )
        if producer_id not in ledger["actions_by_id"]:
            raise VerificationError(f"object[{index}] producer action is unknown")
        producer = ledger["actions_by_id"][producer_id]
        expected_kind = "aggregate-object" if role == "controller-aggregate" else "compile"
        if producer["kind"] != expected_kind:
            raise VerificationError(f"object[{index}] producer action kind differs")
        if producer["method"] != method:
            raise VerificationError(f"object[{index}] producer method differs")
        if source not in producer["declared_inputs"] + producer["discovered_inputs"]:
            raise VerificationError(f"object[{index}] source is absent from producer inputs")
        compile_sha = _sha256(
            record["compile_argv_sha256"], f"object[{index}] compile argv SHA"
        )
        if compile_sha != sha256_bytes(canonical_json_bytes(producer["argv"])):
            raise VerificationError(f"object[{index}] compile argv SHA differs")
        env_id = _string(record["environment_id"], f"object[{index}] environment")
        if env_id != producer["environment_id"] or env_id not in environments:
            raise VerificationError(f"object[{index}] environment differs")
        closure_sha = _sha256(
            record["dependency_closure_sha256"], f"object[{index}] dependency closure"
        )
        if closure_sha not in closures:
            raise VerificationError(f"object[{index}] dependency closure is unknown")
        if closures[closure_sha] != producer["discovered_inputs"]:
            raise VerificationError(
                f"object[{index}] dependency closure differs from exact discovered inputs"
            )
        output = _validate_file_identity(record["object"], f"object[{index}] output")
        if output not in producer["outputs"]:
            raise VerificationError(f"object[{index}] output is absent from producer outputs")
        if expected_kind == "compile":
            source_path = os.path.join(
                roots[source["root_id"]]["canonical_absolute_path"],
                source["relative_path"],
            )
            output_path = os.path.join(
                roots[output["root_id"]]["canonical_absolute_path"],
                output["relative_path"],
            )
            expanded = producer["expanded_argv"]
            compile_positions = [
                position for position, token in enumerate(expanded) if token == "-c"
            ]
            output_positions = [
                position for position, token in enumerate(expanded) if token == "-o"
            ]
            if (
                len(compile_positions) != 1
                or compile_positions[0] + 1 >= len(expanded)
                or expanded[compile_positions[0] + 1] != source_path
                or expanded.count(source_path) != 1
                or len(output_positions) != 1
                or output_positions[0] + 1 >= len(expanded)
                or expanded[output_positions[0] + 1] != output_path
                or expanded.count(output_path) != 1
            ):
                raise VerificationError(
                    f"object[{index}] compile argv does not bind its exact source and output"
                )
        raw_output = _authenticate_identity(output, roots, root_fds, f"object[{index}] output")
        parsed = _parse_macho(raw_output, f"object[{index}] output")
        if parsed["file_type"] != "object":
            raise VerificationError(f"object[{index}] output is not a Mach-O object")
        if output["mode"] & 0o111:
            raise VerificationError(f"object[{index}] output must not be executable")
        objects[supplied_id] = {
            **record,
            "source": source,
            "object": output,
            "parsed_macho": parsed,
        }

    object_outputs = {_identity_key(record["object"]) for record in objects.values()}
    action_object_outputs = {
        _identity_key(output)
        for action in ledger["actions"]
        if action["kind"] in {"compile", "aggregate-object"}
        for output in action["outputs"]
        if output["relative_path"].endswith(".o")
    }
    if object_outputs != action_object_outputs:
        raise VerificationError("object graph does not cover every produced .o exactly")

    aggregates_raw = _sequence(graph["aggregates"], "object aggregates")
    if len(aggregates_raw) != 2:
        raise VerificationError("object graph must have reference and optimized aggregates")
    aggregates: dict[str, dict[str, Any]] = {}
    previous = None
    for item in aggregates_raw:
        aggregate = _mapping(item, "object aggregate")
        _exact_keys(
            aggregate,
            {"method", "aggregate_object_id", "ordered_contributing_object_ids"},
            "object aggregate",
        )
        method = _string(aggregate["method"], "aggregate method")
        if method not in ("optimized", "reference") or (
            previous is not None and method <= previous
        ):
            raise VerificationError("aggregate methods are not sorted and exact")
        previous = method
        aggregate_id = _sha256(aggregate["aggregate_object_id"], "aggregate object ID")
        if aggregate_id not in objects:
            raise VerificationError("aggregate object ID is unknown")
        aggregate_object = objects[aggregate_id]
        if (
            aggregate_object["role"] != "controller-aggregate"
            or aggregate_object["method"] != method
            or aggregate_object["sharing_scope"] != "method-products"
        ):
            raise VerificationError("aggregate object role/method/sharing differs")
        contributors_raw = _sequence(
            aggregate["ordered_contributing_object_ids"], "aggregate contributors"
        )
        contributors = [_sha256(item, "aggregate contributor ID") for item in contributors_raw]
        if not contributors or len(contributors) != len(set(contributors)):
            raise VerificationError("aggregate contributors are empty or duplicate")
        producer = ledger["actions_by_id"][aggregate_object["producer_action_id"]]
        expected_inputs: list[dict[str, Any]] = []
        for contributor_id in contributors:
            if contributor_id not in objects:
                raise VerificationError("aggregate contributor is unknown")
            contributor = objects[contributor_id]
            if (
                contributor["method"] != method
                or contributor["role"] != "controller-translation-unit"
                or contributor["sharing_scope"] != "private-intermediate"
            ):
                raise VerificationError("aggregate contributor role/method/sharing differs")
            expected_inputs.append(contributor["object"])
        producer_inputs = producer["declared_inputs"] + producer["discovered_inputs"]
        if any(identity not in producer_inputs for identity in expected_inputs):
            raise VerificationError("aggregate action omits a contributing object")
        positions = [producer_inputs.index(identity) for identity in expected_inputs]
        if positions != sorted(positions):
            raise VerificationError("aggregate action does not bind ordered contributing objects")
        aggregates[method] = {
            "aggregate_object_id": aggregate_id,
            "contributors": contributors,
        }
    return {"objects": objects, "aggregates": aggregates, "closures": closures}


def _verify_common_objects(value: Any, graph: Mapping[str, Any]) -> dict[str, Any]:
    common = _mapping(value, "common_objects")
    _exact_keys(
        common,
        {"roles", "controller_aggregates", "registered_fidelity_observer_object_ids"},
        "common_objects",
    )
    roles = _mapping(common["roles"], "common object roles")
    if set(roles) != COMMON_OBJECT_ROLES:
        raise VerificationError("common object roles differ from exact registered roles")
    resolved_roles: dict[str, str] = {}
    for role, object_value in roles.items():
        object_key = _sha256(object_value, f"common object role {role}")
        if object_key not in graph["objects"]:
            raise VerificationError(f"common object role {role} is unknown")
        record = graph["objects"][object_key]
        if (
            record["role"] != role
            or record["method"] != "common"
            or record["sharing_scope"] != "all-products"
        ):
            raise VerificationError(f"common object role {role} metadata differs")
        resolved_roles[role] = object_key
    if len(set(resolved_roles.values())) != len(COMMON_OBJECT_ROLES):
        raise VerificationError("common object roles do not identify distinct objects")

    aggregates = _mapping(common["controller_aggregates"], "common controller aggregates")
    if set(aggregates) != {"optimized", "reference"}:
        raise VerificationError("controller aggregate method set differs")
    for method, value_id in aggregates.items():
        if value_id != graph["aggregates"][method]["aggregate_object_id"]:
            raise VerificationError(f"controller aggregate {method} differs from object graph")
    observers = _mapping(
        common["registered_fidelity_observer_object_ids"], "registered observers"
    )
    if set(observers) != {"optimized", "reference"}:
        raise VerificationError("registered observer method set differs")
    resolved_observers: dict[str, list[str]] = {}
    for method, values in observers.items():
        raw_ids = _sequence(values, f"{method} observer IDs")
        ids = [_sha256(item, f"{method} observer ID") for item in raw_ids]
        if not ids or ids != sorted(ids) or len(ids) != len(set(ids)):
            raise VerificationError(f"{method} observer IDs are empty, unsorted, or duplicate")
        for observer_id in ids:
            if observer_id not in graph["objects"]:
                raise VerificationError(f"{method} observer is unknown")
            record = graph["objects"][observer_id]
            if (
                record["method"] != method
                or not record["role"].startswith("fidelity-observer-")
                or record["sharing_scope"] != "fidelity-only"
            ):
                raise VerificationError(f"{method} observer role/method/sharing differs")
        resolved_observers[method] = ids
    return {
        "roles": resolved_roles,
        "aggregates": dict(aggregates),
        "observers": resolved_observers,
    }


def _require_one_pair(argv: Sequence[str], flag: str, value: str, label: str) -> None:
    positions = [index for index, token in enumerate(argv) if token == flag]
    if len(positions) != 1 or positions[0] + 1 >= len(argv) or argv[positions[0] + 1] != value:
        raise VerificationError(f"{label} must occur exactly once with its frozen value")

def _verify_action_semantics(
    ledger: Mapping[str, Any],
    roots: Mapping[str, Mapping[str, Any]],
    sdk: Mapping[str, Any],
    tools: Mapping[str, Mapping[str, Any]],
    required_configure_inputs: Sequence[Mapping[str, Any]],
) -> None:
    runtime_root = roots["runtime-artifact-root"]["canonical_absolute_path"]
    sdk_root = roots["sdk-root"]["canonical_absolute_path"]
    deployment = sdk["deployment_target"]
    forbidden_fragments = (
        "http://",
        "https://",
        "FETCHCONTENT_SOURCE_DIR_",
        "FETCHCONTENT_BASE_DIR",
        "CMAKE_PROJECT_INCLUDE",
        "CMAKE_TOOLCHAIN_FILE",
        "CMAKE_DEPENDENCY_PROVIDER",
        "CMAKE_C_COMPILER_LAUNCHER",
        "CMAKE_CXX_COMPILER_LAUNCHER",
        "ccache",
        "sccache",
    )
    kinds_by_method: dict[str, set[str]] = {"optimized": set(), "reference": set()}
    configure_counts = {"optimized": 0, "reference": 0}
    allowed_tools_by_kind = {
        "configure": {"cmake"},
        "compile": {"cxx-driver"},
        "aggregate-object": {"linker"},
        "archive": {"archiver"},
        "link": {"cxx-driver"},
    }
    for action in ledger["actions"]:
        # The wrapper executes the authenticated response-file expansion, so
        # every semantic policy must inspect that exact effective argv rather
        # than only the outer command line.
        argv = action["expanded_argv"]
        joined = "\0".join(argv)
        if any(fragment.casefold() in joined.casefold() for fragment in forbidden_fragments):
            raise VerificationError("action argv contains a forbidden network/substitution surface")
        method = action["method"]
        if action["kind"] in allowed_tools_by_kind:
            allowed_tool_identities = {
                _identity_key(tools[role]["file"])
                for role in allowed_tools_by_kind[action["kind"]]
            }
            if _identity_key(action["executable"]) not in allowed_tool_identities:
                raise VerificationError(
                    f"{action['kind']} action executable has the wrong registered tool role"
                )
        if method in kinds_by_method:
            kinds_by_method[method].add(action["kind"])
        if action["kind"] == "configure":
            if method not in configure_counts:
                raise VerificationError("configure action must belong to one source method")
            configure_counts[method] += 1
            source_path = roots[f"{method}-source"]["canonical_absolute_path"]
            expected_argv = [
                argv[0],
                source_path,
                "-DCMAKE_BUILD_TYPE=Release",
                "-DCMAKE_OSX_ARCHITECTURES=arm64",
                f"-DCMAKE_OSX_SYSROOT={sdk_root}",
                f"-DCMAKE_OSX_DEPLOYMENT_TARGET={deployment}",
                f"-DDANCERS_RUNTIME_ARTIFACT_ROOT={runtime_root}",
            ]
            if argv != expected_argv:
                raise VerificationError(
                    "configure expanded argv differs from the exact frozen option/source grammar"
                )
            if action["cwd_root_id"] != f"{method}-build" or action[
                "cwd_relative_path"
            ] != ".":
                raise VerificationError("configure action did not start in the fresh build root")
            action_inputs = action["declared_inputs"] + action["discovered_inputs"]
            if any(identity not in action_inputs for identity in required_configure_inputs):
                raise VerificationError(
                    "configure action omits Eigen config or config-version identity"
                )
        tool_version_probe = action["kind"] == "compiler-probe" and action[
            "target"
        ].startswith("tool-version:")
        if action["kind"] in {"compiler-probe", "compile", "aggregate-object", "link"} and not tool_version_probe:
            if any(
                token in {"-target", "--target"}
                or token.startswith("--target=")
                or token.startswith("-UDANCERS_RUNTIME_ARTIFACT_ROOT")
                for token in argv
            ) or any(
                argv[index] == "-U"
                and index + 1 < len(argv)
                and argv[index + 1] == "DANCERS_RUNTIME_ARTIFACT_ROOT"
                for index in range(len(argv))
            ):
                raise VerificationError(
                    "compiler/link action contains a conflicting target/runtime option"
                )
            _require_one_pair(argv, "-arch", "arm64", "architecture option")
            _require_one_pair(argv, "-isysroot", sdk_root, "SDK sysroot option")
            if any(
                token.startswith("-arch=") or token.startswith("--sysroot")
                for token in argv
            ):
                raise VerificationError("compiler/link action contains a conflicting target option")
            deployment_flags = {
                f"-mmacosx-version-min={deployment}",
                f"-mmacos-version-min={deployment}",
            }
            deployment_options = [
                token
                for token in argv
                if token.startswith("-mmacosx-version-min=")
                or token.startswith("-mmacos-version-min=")
            ]
            if len(deployment_options) != 1 or deployment_options[0] not in deployment_flags:
                raise VerificationError(
                    "deployment-target option must occur exactly once with its frozen value"
                )
        if action["kind"] == "compile":
            runtime_flags = {
                f"-DDANCERS_RUNTIME_ARTIFACT_ROOT={runtime_root}",
                f'-DDANCERS_RUNTIME_ARTIFACT_ROOT="{runtime_root}"',
            }
            runtime_options = [
                token
                for token in argv
                if token.startswith("-DDANCERS_RUNTIME_ARTIFACT_ROOT=")
            ]
            if len(runtime_options) != 1 or runtime_options[0] not in runtime_flags:
                raise VerificationError(
                    "compile runtime-root definition must occur exactly once with its frozen value"
                )
    if configure_counts != {"optimized": 1, "reference": 1}:
        raise VerificationError("build must have exactly one configure action per source method")
    required_per_method = {
        "configure",
        "compiler-probe",
        "compile",
        "aggregate-object",
        "link",
    }
    for method, kinds in kinds_by_method.items():
        if not required_per_method <= kinds:
            raise VerificationError(f"{method} action ledger omits required build-action kinds")
    archive_actions = [
        action for action in ledger["actions"] if action["kind"] == "archive"
    ]
    if len(archive_actions) != 1 or archive_actions[0]["method"] != "common":
        raise VerificationError(
            "action ledger must contain exactly one common archive action"
        )
    for role, tool in tools.items():
        matching_probes = [
            action
            for action in ledger["actions"]
            if action["kind"] == "compiler-probe"
            and action["target"] == f"tool-version:{role}"
            and action["executable"] == tool["file"]
            and action["argv"] == tool["version_argv"]
            and action["stdout"]["sha256"] == tool["version_stdout_sha256"]
        ]
        if len(matching_probes) != 1:
            raise VerificationError(
                f"tool {role} lacks exactly one causally recorded version probe"
            )


def _verify_products(
    value: Any,
    graph: Mapping[str, Any],
    common: Mapping[str, Any],
    ledger: Mapping[str, Any],
    roots: Mapping[str, Mapping[str, Any]],
    root_fds: Mapping[str, int],
) -> list[dict[str, Any]]:
    products_raw = _sequence(value, "products")
    if len(products_raw) != MAX_PRODUCTS:
        raise VerificationError("products must contain exactly four records")
    products: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    seen_binaries: set[tuple[str, str, int, int, str]] = set()
    expected_order = [
        ("optimized", "fidelity"),
        ("optimized", "timing"),
        ("reference", "fidelity"),
        ("reference", "timing"),
    ]
    for index, item in enumerate(products_raw):
        record = _mapping(item, f"products[{index}]")
        _exact_keys(record, PRODUCT_KEYS, f"products[{index}]")
        method = _string(record["method"], "product method")
        role = _string(record["role"], "product role")
        key = (method, role)
        if key != expected_order[index] or key not in PRODUCT_KEYS_EXPECTED:
            raise VerificationError("products are not the exact canonical four-product order")
        if key in seen_keys:
            raise VerificationError("duplicate product")
        seen_keys.add(key)
        binary = _validate_file_identity(record["binary"], f"product {method}-{role} binary")
        if binary["root_id"] != f"{method}-build":
            raise VerificationError("product binary is outside its method build root")
        binary_key = _identity_key(binary)
        if binary_key in seen_binaries:
            raise VerificationError("two products identify the same binary")
        seen_binaries.add(binary_key)
        binary_raw = _authenticate_identity(
            binary, roots, root_fds, f"product {method}-{role} binary"
        )
        parsed = _validate_macho_record(
            record["macho_load_commands"], binary_raw, f"product {method}-{role}"
        )
        if parsed["file_type"] != "execute":
            raise VerificationError("product is not an executable Mach-O")
        if binary["mode"] & stat.S_IXUSR == 0:
            raise VerificationError("product binary is not owner-executable")
        runtime_root = roots["runtime-artifact-root"]["canonical_absolute_path"]
        if runtime_root.encode("utf-8") not in binary_raw:
            raise VerificationError("product does not contain exact common runtime artifact root")
        if record["runtime_artifact_root"] != "runtime-artifact-root":
            raise VerificationError("product runtime-root binding differs")
        link_id = _sha256(record["link_action_id"], "product link action ID")
        if link_id not in ledger["actions_by_id"]:
            raise VerificationError("product link action is unknown")
        link = ledger["actions_by_id"][link_id]
        if link["kind"] != "link" or link["method"] != method or binary not in link["outputs"]:
            raise VerificationError("product link action/method/output differs")
        object_ids_raw = _sequence(record["ordered_object_ids"], "product ordered objects")
        object_ids = [_sha256(item, "product object ID") for item in object_ids_raw]
        if not object_ids or len(object_ids) != len(set(object_ids)):
            raise VerificationError("product object IDs are empty or duplicate")
        for object_key in object_ids:
            if object_key not in graph["objects"]:
                raise VerificationError("product names an unknown object")
        required_common = set(common["roles"].values())
        if not required_common <= set(object_ids):
            raise VerificationError("product does not literally reuse every common object")
        aggregate_id = common["aggregates"][method]
        if aggregate_id not in object_ids:
            raise VerificationError("product omits its method controller aggregate")
        other_aggregate = common["aggregates"][
            "reference" if method == "optimized" else "optimized"
        ]
        if other_aggregate in object_ids:
            raise VerificationError("product includes the other method aggregate")
        observers = set(common["observers"][method])
        actual_observers = {
            object_key
            for object_key in object_ids
            if graph["objects"][object_key]["role"].startswith("fidelity-observer-")
        }
        if role == "timing":
            if record["observer_mode"] != "absent" or actual_observers:
                raise VerificationError("timing product contains an evidence observer")
            expected_object_set = required_common | {aggregate_id}
        else:
            if record["observer_mode"] != "present" or actual_observers != observers:
                raise VerificationError("fidelity product observer set differs")
            expected_object_set = required_common | {aggregate_id} | observers
        if set(object_ids) != expected_object_set:
            raise VerificationError(
                "product object set has unclassified, private, missing, or extra objects"
            )
        for object_key in object_ids:
            identity = graph["objects"][object_key]["object"]
            if identity not in link["declared_inputs"] + link["discovered_inputs"]:
                raise VerificationError("linked object is absent from link action inputs")
        libraries = _ordered_identity_sequence(
            record["ordered_library_inputs"], "product ordered library inputs"
        )
        if not libraries:
            raise VerificationError("product library input closure is empty")
        for library in libraries:
            _authenticate_identity(library, roots, root_fds, "product library input")
            if library not in link["declared_inputs"] + link["discovered_inputs"]:
                raise VerificationError("product library is absent from link action inputs")
        expanded_link_argv = link["expanded_argv"]
        ordered_link_identities = [
            graph["objects"][object_key]["object"] for object_key in object_ids
        ] + libraries
        ordered_link_keys = {_identity_key(identity) for identity in ordered_link_identities}
        ordered_paths = [
            os.path.join(
                roots[identity["root_id"]]["canonical_absolute_path"],
                identity["relative_path"],
            )
            for identity in ordered_link_identities
        ]
        positions: list[int] = []
        for path in ordered_paths:
            occurrences = [
                position
                for position, token in enumerate(expanded_link_argv)
                if token == path
            ]
            if len(occurrences) != 1:
                raise VerificationError(
                    "linked object/library must occur exactly once in expanded link argv"
                )
            positions.append(occurrences[0])
        if positions != sorted(positions):
            raise VerificationError("product object/library order differs from expanded link argv")
        for input_identity in link["declared_inputs"] + link["discovered_inputs"]:
            input_path = os.path.join(
                roots[input_identity["root_id"]]["canonical_absolute_path"],
                input_identity["relative_path"],
            )
            if (
                input_path in expanded_link_argv
                and _identity_key(input_identity) not in ordered_link_keys
            ):
                raise VerificationError(
                    "expanded link argv contains an unclassified literal file input"
                )
        dynamics_raw = _sequence(
            record["resolved_non_system_dynamic_inputs"], "product dynamic inputs"
        )
        dynamics: list[tuple[str, dict[str, Any]]] = []
        previous_install: str | None = None
        for dynamic_value in dynamics_raw:
            dynamic = _mapping(dynamic_value, "resolved dynamic input")
            _exact_keys(dynamic, {"install_name", "file"}, "resolved dynamic input")
            install_name = _string(dynamic["install_name"], "dynamic install name")
            if previous_install is not None and install_name <= previous_install:
                raise VerificationError("dynamic inputs are not install-name sorted and unique")
            previous_install = install_name
            identity = _validate_file_identity(dynamic["file"], "resolved dynamic file")
            _authenticate_identity(identity, roots, root_fds, "resolved dynamic file")
            if identity not in libraries:
                raise VerificationError("resolved dynamic input is absent from library inputs")
            dynamics.append((install_name, identity))
        expected_dynamic_names = sorted(
            name
            for name in parsed["dylibs"]
            if not name.startswith("/usr/lib/")
            and not name.startswith("/System/Library/Frameworks/")
        )
        if [name for name, _ in dynamics] != expected_dynamic_names:
            raise VerificationError("resolved non-system dynamic closure differs from Mach-O loads")
        products.append(
            {
                **record,
                "binary": binary,
                "ordered_object_ids": object_ids,
                "ordered_library_inputs": libraries,
                "parsed_macho": parsed,
            }
        )
    shared_libraries = products[0]["ordered_library_inputs"]
    if any(product["ordered_library_inputs"] != shared_libraries for product in products[1:]):
        raise VerificationError("all four products must reuse one literal ordered library closure")
    product_link_ids = {product["link_action_id"] for product in products}
    ledger_link_ids = {
        action["action_id"] for action in ledger["actions"] if action["kind"] == "link"
    }
    if product_link_ids != ledger_link_ids:
        raise VerificationError("product graph does not cover exactly every link action")
    shared_library_keys = {_identity_key(identity) for identity in shared_libraries}
    archive_output_keys = {
        _identity_key(output)
        for action in ledger["actions"]
        if action["kind"] == "archive"
        for output in action["outputs"]
        if output["relative_path"].endswith((".a", ".dylib"))
    }
    if not archive_output_keys <= shared_library_keys:
        raise VerificationError("archive outputs are outside the literal shared library closure")
    return products


def _reject_acyclic_forbidden_names(value: Any, path: str = "manifest") -> None:
    if type(value) is dict:
        for key, child in value.items():
            lowered = str(key).casefold()
            if any(fragment in lowered for fragment in FORBIDDEN_BINDING_FRAGMENTS):
                raise VerificationError(f"{path} contains forbidden later-stage binding {key!r}")
            _reject_acyclic_forbidden_names(child, f"{path}.{key}")
    elif type(value) is list:
        for index, child in enumerate(value):
            _reject_acyclic_forbidden_names(child, f"{path}[{index}]")


def _verify_contract_binding(raw: bytes) -> None:
    # This committed, independently hash-authenticated contract predates the
    # canonical manifest encoding and is intentionally pretty-printed.  Keep
    # strict JSON, duplicate-key, non-finite-number, and depth checks without
    # pretending its frozen bytes use the canonical wire representation.
    contract = _mapping(
        _parse_json_document(
            raw, "build provenance contract", require_canonical=False
        ),
        "build contract",
    )
    if contract.get("schema_version") != "dancers-planar-physics-build-provenance-contract-v1":
        raise VerificationError("bound build-provenance contract schema differs")
    if contract.get("epoch_id") != EPOCH_ID:
        raise VerificationError("bound build-provenance contract epoch differs")
    manifest = _mapping(contract.get("manifest_schema"), "contract manifest schema")
    if manifest.get("schema_version") != SCHEMA_VERSION or set(
        _sequence(manifest.get("top_level_keys"), "contract top-level keys")
    ) != TOP_KEYS:
        raise VerificationError("bound contract manifest surface differs")
    action_contract = _mapping(
        contract.get("action_ledger_contract"), "contract action ledger"
    )
    if set(_sequence(action_contract.get("action_fields"), "contract action fields")) != ACTION_KEYS:
        raise VerificationError("bound contract action fields differ")
    acyclic = _mapping(contract.get("acyclic_binding"), "contract acyclic binding")
    if set(acyclic.get("build_provenance_must_not_bind", [])) != {
        "test_attestation",
        "execution_identity_manifest",
        "scientific_result",
    }:
        raise VerificationError("bound contract acyclic exclusions differ")


def verify_build_provenance(
    provenance_path: str | os.PathLike[str],
    *,
    expected_provenance_bytes: int,
    expected_provenance_sha256: str,
    trust_root_path: str | os.PathLike[str],
    expected_trust_root_bytes: int,
    expected_trust_root_sha256: str,
) -> dict[str, Any]:
    """Authenticate one prospective build package and return a non-authorizing verdict."""
    provenance_name = os.path.abspath(os.fspath(provenance_path))
    trust_name = os.path.abspath(os.fspath(trust_root_path))
    if provenance_name != os.fspath(provenance_path) or trust_name != os.fspath(trust_root_path):
        raise VerificationError("provenance and trust-root paths must be absolute")
    provenance_size = _integer(
        expected_provenance_bytes, "expected provenance bytes", 1
    )
    trust_size = _integer(expected_trust_root_bytes, "expected trust-root bytes", 1)
    if provenance_size > MAX_MANIFEST_BYTES or trust_size > MAX_TRUST_ROOT_BYTES:
        raise VerificationError("external expected byte count exceeds verifier caps")
    provenance_sha = _sha256(expected_provenance_sha256, "expected provenance SHA-256")
    trust_sha = _sha256(expected_trust_root_sha256, "expected trust-root SHA-256")
    provenance_raw, _ = _open_regular_absolute(
        provenance_name, MAX_MANIFEST_BYTES, "provenance manifest"
    )
    trust_raw, _ = _open_regular_absolute(trust_name, MAX_TRUST_ROOT_BYTES, "trust-root manifest")
    if len(provenance_raw) != provenance_size or sha256_bytes(provenance_raw) != provenance_sha:
        raise VerificationError("provenance bytes differ from independent expected identity")
    if len(trust_raw) != trust_size or sha256_bytes(trust_raw) != trust_sha:
        raise VerificationError("trust-root bytes differ from independent expected identity")
    provenance = _mapping(_parse_json(provenance_raw, "provenance manifest"), "provenance")
    trust_value = _parse_json(trust_raw, "trust-root manifest")
    _exact_keys(provenance, TOP_KEYS, "provenance manifest")
    if provenance["schema_version"] != SCHEMA_VERSION or provenance["epoch_id"] != EPOCH_ID:
        raise VerificationError("provenance schema or epoch differs")
    if not _same_json_type_and_value(provenance["classification"], CLASSIFICATION):
        raise VerificationError("provenance classification differs from exact non-authorizing set")
    if not _same_json_type_and_value(provenance["build_policy"], BUILD_POLICY):
        raise VerificationError("provenance build policy differs")
    _reject_acyclic_forbidden_names(provenance)
    trust = _validate_trust_root(trust_value)
    roots: dict[str, dict[str, Any]] = {}
    root_fds: dict[str, int] = {}
    try:
        roots, root_fds, _ = _verify_root_records(provenance["roots"], trust)
        for name, identity in trust["identities"].items():
            _authenticate_identity(identity, roots, root_fds, f"trust identity {name}")
        identities, identity_raw = _verify_identity_bindings(
            provenance["identity_bindings"], roots, root_fds, trust, trust_sha
        )
        _verify_contract_binding(identity_raw["build_provenance_contract"])
        _verify_source_identity(
            identity_raw["reference_source_identity"],
            "reference",
            "reference-source",
            roots,
            root_fds,
        )
        _verify_source_identity(
            identity_raw["optimized_source_identity"],
            "optimized",
            "optimized-source",
            roots,
            root_fds,
        )
        tools, _ = _verify_toolchain(
            provenance["toolchain"],
            roots,
            root_fds,
            identity_raw["pixi_lock"],
            identities["pixi_lock"],
        )
        sdk, _ = _verify_sdk(provenance["sdk"], roots, root_fds)
        environments = _verify_environment_records(
            provenance["environments"], roots, tools, sdk
        )
        dependency_result = _verify_dependencies(
            provenance["dependencies"], identities, identity_raw, roots, root_fds
        )
        ledger = _verify_action_ledger(
            provenance["action_ledger"], identities, roots, root_fds, tools, environments
        )
        _verify_action_semantics(
            ledger,
            roots,
            sdk,
            tools,
            dependency_result["configure_inputs"],
        )
        _verify_network_isolation(
            provenance["network_isolation"],
            ledger,
            identities,
            roots,
            root_fds,
            trust,
            tools,
        )
        graph = _verify_object_graph(
            provenance["object_graph"], ledger, environments, roots, root_fds
        )
        common = _verify_common_objects(provenance["common_objects"], graph)
        products = _verify_products(
            provenance["products"], graph, common, ledger, roots, root_fds
        )
        return {
            "schema_version": "dancers-planar-physics-build-provenance-verdict-v1",
            "epoch_id": EPOCH_ID,
            "valid": True,
            "data_consistent": True,
            "build_provenance_authenticated": True,
            "package_identity_authenticated": True,
            "authenticated_against_supplied_external_identity": True,
            "causal_capture_verified": False,
            "provenance_complete": False,
            "promotion_eligible": False,
            "execution_authorized": False,
            "registered_scientific_run": False,
            "scientific_evidence": False,
            "scientific_evidence_eligible": False,
            "provenance_sha256": provenance_sha,
            "trust_root_sha256": trust_sha,
            "action_ledger_sha256": ledger["ledger_sha256"],
            "object_count": len(graph["objects"]),
            "product_count": len(products),
            "promotion_blockers": [
                "capture-profile-revision-required",
                "frozen-contract-offline-v2-profile-revision-required",
                "embedded-ledger-16mib-profile-limit",
                "normalized-no-symlink-staged-prefix-absent",
                "independent-protected-capture-absent",
                "offline-v2-semantic-verdict-not-bound-or-recomputed",
                "generation-aware-action-ledger-revision-required",
                "fork-exec-exit-event-ledger-revision-required",
                "complete-helper-action-surface-revision-required",
                "complete-network-canary-matrix-revision-required",
                "complete-link-line-resolution-profile-required",
                "complete-action-argv-grammar-profile-required",
                "recursive-dynamic-resolution-profile-required",
                "production-macho-semantic-profile-required",
                "bounded-authentication-cache-profile-required",
                "root-alias-and-overlap-profile-revision-required",
                "private-home-and-tmp-lifecycle-profile-required",
                "cross-method-environment-and-action-parity-profile-required",
                "prospective-input-identity-closure-not-trust-root-bound",
                "literal-common-object-superbuild-capture-absent",
                "test-attestation-absent",
                "external-execution-identity-manifest-absent",
                "execution-design-and-freezer-remain-locked",
            ],
        }
    finally:
        for fd in root_fds.values():
            os.close(fd)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Authenticate a prospective, non-authorizing physics build-provenance package"
    )
    parser.add_argument("--provenance", required=True)
    parser.add_argument("--expected-provenance-bytes", required=True, type=int)
    parser.add_argument("--expected-provenance-sha256", required=True)
    parser.add_argument("--trust-root", required=True)
    parser.add_argument("--expected-trust-root-bytes", required=True, type=int)
    parser.add_argument("--expected-trust-root-sha256", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        verdict = verify_build_provenance(
            args.provenance,
            expected_provenance_bytes=args.expected_provenance_bytes,
            expected_provenance_sha256=args.expected_provenance_sha256,
            trust_root_path=args.trust_root,
            expected_trust_root_bytes=args.expected_trust_root_bytes,
            expected_trust_root_sha256=args.expected_trust_root_sha256,
        )
    except (OSError, VerificationError) as exc:
        print(f"physics build provenance verification failed: {exc}", file=sys.stderr)
        return 1
    print(canonical_json_bytes(verdict).decode("utf-8"), end="")
    return 2 if verdict["promotion_blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
