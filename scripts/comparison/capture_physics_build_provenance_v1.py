#!/usr/bin/env python3
"""Canonically package already-captured planar-physics build provenance.

This module is deliberately not a build launcher, test runner, simulator, or
eligibility verifier.  It accepts independently supplied JSON, materializes
domain-separated action identities and one ordered action-ledger commitment,
then publishes the resulting non-authorizing payload create-exclusively.

No clock or host-derived value is added: identical inputs produce identical
bytes.  In particular, this packager cannot turn a retrospective description
into causal build or network-isolation evidence.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import sys
from typing import Any, Mapping, Sequence
import unicodedata


PROVENANCE_SCHEMA = "dancers-planar-physics-build-provenance-v1"
ACTION_LEDGER_SCHEMA = "dancers-planar-physics-action-ledger-v1"
CAPTURE_RECEIPT_SCHEMA = "dancers-planar-physics-build-provenance-capture-receipt-v1"
EPOCH_ID = "simulator-demo-v1-physics-stress-test-epoch-1"

# The terminal NUL makes concatenation with canonical JSON unambiguous.
ACTION_ID_DOMAIN = b"dancers-planar-physics-build-action-id-v1\x00"
ACTION_LEDGER_DOMAIN = b"dancers-planar-physics-build-action-ledger-v1\x00"

# The packager and verifier deliberately share one hard manifest ceiling.
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_REFERENCED_FILE_BYTES = 512 * 1024 * 1024
MAX_ACTIONS = 10_000
MAX_PATH_BYTES = 1_024
MAX_ARG_BYTES = 32_768
MAX_ARGV_ITEMS = 8_192
MAX_ACTION_IO_BYTES = 4 * 1024 * 1024
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 2_000_000
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

FILE_IDENTITY_FIELDS = (
    "root_id",
    "relative_path",
    "file_type",
    "mode",
    "bytes",
    "sha256",
)

TOP_LEVEL_KEYS = (
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
)

PROVENANCE_CLASSIFICATION = {
    "execution_authorized": False,
    "outcome_independent": True,
    "registered_scientific_run": False,
    "scientific_evidence": False,
    "scientific_evidence_eligible": False,
}

ACTION_FIELDS = (
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
)

ACTION_KINDS = frozenset(
    {
        "configure",
        "compiler-probe",
        "compile",
        "aggregate-object",
        "archive",
        "link",
    }
)

FORBIDDEN_TRUE_FLAGS = frozenset(
    {
        "execution_authorized",
        "registered_scientific_run",
        "registered_scientific_process_permitted",
        "registered_physics_scenarios_executed",
        "scientific_evidence",
        "scientific_evidence_eligible",
        "scientific_execution_authorized",
        "scientific_results_present",
        "promotion_eligible",
        "causal_capture_verified",
        "build_capture_complete",
        "provenance_complete",
    }
)

FORBIDDEN_BUILD_BINDINGS = frozenset(
    {
        "execution_identity_manifest",
        "execution_identity_manifest_sha256",
        "scientific_result",
        "scientific_result_sha256",
        "test_attestation",
        "test_attestation_sha256",
        "phase_receipt",
        "phase_receipt_sha256",
    }
)


class ProvenanceCaptureError(ValueError):
    """The supplied evidence cannot be packaged or safely published."""


class _DuplicateKey(ValueError):
    pass


@dataclass(frozen=True)
class CapturedProvenance:
    """Identity of one successfully published canonical provenance payload."""

    path: Path
    bytes: int
    sha256: str
    ledger_sha256: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "bytes": self.bytes,
            "path": str(self.path),
            "sha256": self.sha256,
        }


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value!r} is forbidden")


def canonical_json_bytes(value: Any, *, maximum_bytes: int = MAX_JSON_BYTES) -> bytes:
    """Return sorted, compact, finite UTF-8 JSON with one terminal newline."""

    if (
        type(maximum_bytes) is not int
        or maximum_bytes < 1
        or maximum_bytes > MAX_JSON_BYTES
    ):
        raise ProvenanceCaptureError("maximum_bytes must be a positive integer")
    _enforce_json_shape(value, "canonical JSON", maximum_bytes=maximum_bytes)
    try:
        encoder = json.JSONEncoder(
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        chunks: list[bytes] = []
        total = 0
        for text in encoder.iterencode(value):
            chunk = text.encode("utf-8", "strict")
            if total > maximum_bytes - len(chunk) - 1:
                raise ProvenanceCaptureError(
                    f"canonical JSON exceeds the {maximum_bytes}-byte bound"
                )
            chunks.append(chunk)
            total += len(chunk)
        chunks.append(b"\n")
        encoded = b"".join(chunks)
    except ProvenanceCaptureError:
        raise
    except (TypeError, ValueError, UnicodeError, RecursionError) as error:
        raise ProvenanceCaptureError(
            f"value is not finite canonical JSON data: {error}"
        ) from error
    return encoded


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _enforce_json_shape(
    value: Any, label: str, *, maximum_bytes: int = MAX_JSON_BYTES
) -> None:
    pending: list[tuple[Any, int]] = [(value, 1)]
    nodes = 0
    while pending:
        current, depth = pending.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES:
            raise ProvenanceCaptureError(f"{label} exceeds the JSON node bound")
        if depth > MAX_JSON_DEPTH:
            raise ProvenanceCaptureError(f"{label} exceeds JSON depth {MAX_JSON_DEPTH}")
        if type(current) is dict:
            if any(type(key) is str and len(key) > maximum_bytes for key in current):
                raise ProvenanceCaptureError(f"{label} contains an oversized JSON key")
            pending.extend((child, depth + 1) for child in current.values())
        elif type(current) is list:
            pending.extend((child, depth + 1) for child in current)
        elif type(current) is str and len(current) > maximum_bytes:
            raise ProvenanceCaptureError(f"{label} contains an oversized string token")


def parse_json_bytes(raw: bytes, *, label: str = "JSON input") -> Any:
    """Parse strict JSON while rejecting duplicate keys and non-finite constants."""

    if not isinstance(raw, bytes):
        raise ProvenanceCaptureError(f"{label} must be bytes")
    if len(raw) > MAX_JSON_BYTES:
        raise ProvenanceCaptureError(f"{label} exceeds the input byte bound")
    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateKey,
        ValueError,
        RecursionError,
    ) as error:
        raise ProvenanceCaptureError(f"{label} is not strict JSON: {error}") from error
    _enforce_json_shape(value, label)
    return value


def _json_clone(value: Any, *, label: str) -> Any:
    """Normalize public-helper inputs to ordinary JSON containers."""

    return parse_json_bytes(canonical_json_bytes(value), label=label)


def _exact_keys(value: Mapping[str, Any], expected: Sequence[str], label: str) -> None:
    expected_set = set(expected)
    observed = set(value)
    if observed != expected_set:
        missing = sorted(expected_set - observed)
        extra = sorted(observed - expected_set)
        raise ProvenanceCaptureError(
            f"{label} keys differ (missing={missing}, extra={extra})"
        )


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ProvenanceCaptureError(
            f"{label} must be 64 lowercase hexadecimal digits"
        )
    return value


def _nonempty_string(value: Any, label: str) -> str:
    if (
        type(value) is not str
        or not value
        or "\x00" in value
        or unicodedata.normalize("NFC", value) != value
    ):
        raise ProvenanceCaptureError(
            f"{label} must be nonempty canonical NUL-free Unicode text"
        )
    return value


def _file_identity(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise ProvenanceCaptureError(f"{label} must be a file identity object")
    _exact_keys(value, FILE_IDENTITY_FIELDS, label)
    root_id = _nonempty_string(value["root_id"], f"{label}.root_id")
    relative = _nonempty_string(
        value["relative_path"], f"{label}.relative_path"
    )
    pure = PurePosixPath(relative)
    if (
        "\\" in relative
        or len(relative.encode("utf-8")) > MAX_PATH_BYTES
        or pure.is_absolute()
        or not pure.parts
        or pure.as_posix() != relative
        or any(part in ("", ".", "..") for part in pure.parts)
    ):
        raise ProvenanceCaptureError(
            f"{label}.relative_path must be a bounded canonical POSIX relative path"
        )
    if value["file_type"] != "regular":
        raise ProvenanceCaptureError(f"{label}.file_type must be regular")
    mode = value["mode"]
    if type(mode) is not int or mode not in (0o400, 0o500, 0o600, 0o700, 0o644, 0o755):
        raise ProvenanceCaptureError(f"{label}.mode is outside the frozen modes")
    size = value["bytes"]
    if type(size) is not int or not 0 <= size <= MAX_REFERENCED_FILE_BYTES:
        raise ProvenanceCaptureError(f"{label}.bytes is outside its bound")
    return {
        "root_id": root_id,
        "relative_path": relative,
        "file_type": "regular",
        "mode": mode,
        "bytes": size,
        "sha256": _sha256(value["sha256"], f"{label}.sha256"),
    }


def _reject_authorizing_flags(value: Any) -> None:
    """Reject dangerous true flags anywhere in the packaged JSON tree."""

    pending: list[tuple[str, Any]] = [("$", value)]
    while pending:
        path, current = pending.pop()
        if type(current) is dict:
            for key, child in current.items():
                child_path = f"{path}.{key}"
                if key in FORBIDDEN_BUILD_BINDINGS:
                    raise ProvenanceCaptureError(
                        f"{child_path} is a forbidden later-stage binding"
                    )
                if key in FORBIDDEN_TRUE_FLAGS and (
                    type(child) is not bool or child is True
                ):
                    raise ProvenanceCaptureError(
                        f"{child_path} must be the JSON boolean false"
                    )
                pending.append((child_path, child))
        elif type(current) is list:
            pending.extend(
                (f"{path}[{index}]", child)
                for index, child in enumerate(current)
            )


def compute_action_id(action: Mapping[str, Any]) -> str:
    """Compute ``SHA256(ACTION_ID_DOMAIN || canonical(action without ID))``.

    This primitive deliberately accepts any JSON object so it can be reused by
    independent capture wrappers.  Complete action-shape checks occur when an
    action ledger is materialized.
    """

    if not isinstance(action, Mapping):
        raise ProvenanceCaptureError("action must be a JSON object")
    basis = {key: value for key, value in action.items() if key != "action_id"}
    return sha256_bytes(ACTION_ID_DOMAIN + canonical_json_bytes(basis))


def action_id(action_without_action_id: Mapping[str, Any]) -> str:
    """Verifier-compatible public name for :func:`compute_action_id`."""

    if "action_id" in action_without_action_id:
        raise ProvenanceCaptureError("action_without_action_id contains action_id")
    return compute_action_id(action_without_action_id)


def materialize_action_record(action: Mapping[str, Any]) -> dict[str, Any]:
    """Return one complete action with a verified or newly computed ID."""

    if not isinstance(action, Mapping):
        raise ProvenanceCaptureError("action must be a JSON object")
    normalized = _json_clone(dict(action), label="action")
    allowed_input_keys = set(ACTION_FIELDS)
    required_input_keys = allowed_input_keys - {"action_id"}
    observed = set(normalized)
    if observed not in (required_input_keys, allowed_input_keys):
        missing = sorted(required_input_keys - observed)
        extra = sorted(observed - allowed_input_keys)
        raise ProvenanceCaptureError(
            f"action keys differ (missing={missing}, extra={extra})"
        )

    sequence_index = normalized["sequence_index"]
    if (
        not isinstance(sequence_index, int)
        or isinstance(sequence_index, bool)
        or sequence_index < 0
    ):
        raise ProvenanceCaptureError(
            "action.sequence_index must be a nonnegative integer"
        )
    if normalized["kind"] not in ACTION_KINDS:
        raise ProvenanceCaptureError("action.kind is not a frozen build action kind")
    for field in (
        "method",
        "target",
        "cwd_root_id",
        "environment_id",
        "network_session_id",
    ):
        _nonempty_string(normalized[field], f"action.{field}")
    cwd = normalized["cwd_relative_path"]
    if not isinstance(cwd, str) or "\x00" in cwd:
        raise ProvenanceCaptureError(
            "action.cwd_relative_path must be a NUL-free string"
        )
    if cwd != ".":
        pure_cwd = PurePosixPath(cwd)
        if (
            "\\" in cwd
            or len(cwd.encode("utf-8")) > MAX_PATH_BYTES
            or pure_cwd.is_absolute()
            or not pure_cwd.parts
            or pure_cwd.as_posix() != cwd
            or any(part in ("", ".", "..") for part in pure_cwd.parts)
        ):
            raise ProvenanceCaptureError(
                "action.cwd_relative_path must be '.' or a bounded canonical POSIX path"
            )
    normalized["executable"] = _file_identity(
        normalized["executable"], "action.executable"
    )
    if (
        not isinstance(normalized["argv"], list)
        or not normalized["argv"]
        or len(normalized["argv"]) > MAX_ARGV_ITEMS
        or any(
            not isinstance(argument, str)
            or "\x00" in argument
            or len(argument.encode("utf-8")) > MAX_ARG_BYTES
            for argument in normalized["argv"]
        )
    ):
        raise ProvenanceCaptureError(
            "action.argv must be a nonempty array of NUL-free strings"
        )
    if normalized["stdin"] != "closed":
        raise ProvenanceCaptureError("action.stdin must be exactly 'closed'")
    for field in ("response_files", "declared_inputs", "discovered_inputs", "outputs"):
        if not isinstance(normalized[field], list):
            raise ProvenanceCaptureError(f"action.{field} must be an array")
    for field in ("declared_inputs", "discovered_inputs", "outputs"):
        normalized[field] = [
            _file_identity(identity, f"action.{field}[{index}]")
            for index, identity in enumerate(normalized[field])
        ]
    responses: list[dict[str, Any]] = []
    for index, response_value in enumerate(normalized["response_files"]):
        if type(response_value) is not dict:
            raise ProvenanceCaptureError(f"action.response_files[{index}] must be an object")
        _exact_keys(response_value, ("argument", "file"), "action response file")
        argument = _nonempty_string(response_value["argument"], "response-file argument")
        if (
            not argument.startswith("@")
            or argument not in normalized["argv"]
            or len(argument.encode("utf-8")) > MAX_ARG_BYTES
        ):
            raise ProvenanceCaptureError("response-file argument is not a bounded literal argv item")
        responses.append(
            {
                "argument": argument,
                "file": _file_identity(response_value["file"], "response-file identity"),
            }
        )
    normalized["response_files"] = responses
    input_identities = normalized["declared_inputs"] + normalized["discovered_inputs"]
    if any(response["file"] not in input_identities for response in responses):
        raise ProvenanceCaptureError(
            "every response-file identity must be in the declared/discovered input closure"
        )
    if not normalized["outputs"]:
        raise ProvenanceCaptureError("action.outputs must not be empty")
    for field in ("stdout", "stderr"):
        stream = normalized[field]
        if type(stream) is not dict:
            raise ProvenanceCaptureError(f"action.{field} must be a stream object")
        _exact_keys(stream, ("bytes", "sha256", "utf8"), f"action.{field}")
        text = stream["utf8"]
        if type(text) is not str or "\x00" in text:
            raise ProvenanceCaptureError(f"action.{field}.utf8 must be NUL-free text")
        raw = text.encode("utf-8")
        if len(raw) > MAX_ACTION_IO_BYTES:
            raise ProvenanceCaptureError(f"action.{field} exceeds the stream cap")
        if stream["bytes"] != len(raw) or _sha256(
            stream["sha256"], f"action.{field}.sha256"
        ) != sha256_bytes(raw):
            raise ProvenanceCaptureError(f"action.{field} bytes/SHA differ")
    if (
        not isinstance(normalized["exit_code"], int)
        or isinstance(normalized["exit_code"], bool)
        or normalized["exit_code"] != 0
    ):
        raise ProvenanceCaptureError("action.exit_code must be exactly zero")

    supplied = normalized.pop("action_id", None)
    computed = compute_action_id(normalized)
    if supplied is not None and _sha256(supplied, "action.action_id") != computed:
        raise ProvenanceCaptureError("supplied action.action_id differs from computed ID")
    normalized["action_id"] = computed
    return normalized


def compute_action_ledger_sha256(actions: Sequence[Mapping[str, Any]]) -> str:
    """Hash the canonical ordered array of complete action records.

    Array order is retained by JSON canonicalization; object keys are sorted.
    Every record must already contain its correct action ID.
    """

    if isinstance(actions, (str, bytes, bytearray)) or not isinstance(actions, Sequence):
        raise ProvenanceCaptureError("actions must be an ordered sequence")
    complete: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        if not isinstance(action, Mapping):
            raise ProvenanceCaptureError(f"actions[{index}] must be a JSON object")
        normalized = _json_clone(dict(action), label=f"actions[{index}]")
        if "action_id" not in normalized:
            raise ProvenanceCaptureError(f"actions[{index}] has no action_id")
        supplied = _sha256(normalized["action_id"], f"actions[{index}].action_id")
        if supplied != compute_action_id(normalized):
            raise ProvenanceCaptureError(f"actions[{index}].action_id differs")
        complete.append(normalized)
    digest = hashlib.sha256()
    digest.update(ACTION_LEDGER_DOMAIN)
    for action in complete:
        payload = canonical_json_bytes(action)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def action_ledger_sha256(actions: Sequence[Mapping[str, Any]]) -> str:
    """Verifier-compatible public name for the ordered ledger commitment."""

    return compute_action_ledger_sha256(actions)


def materialize_action_ledger(value: Any) -> dict[str, Any]:
    """Materialize action IDs and the ordered complete-record ledger digest."""

    if type(value) is not dict:
        raise ProvenanceCaptureError("action_ledger must be an object")
    _exact_keys(
        value,
        (
            "schema_version",
            "network_session_id",
            "wrapper",
            "actions",
            "ledger_sha256",
        ),
        "action_ledger",
    )
    if value["schema_version"] != ACTION_LEDGER_SCHEMA:
        raise ProvenanceCaptureError("action_ledger schema_version differs")
    network_session_id = _nonempty_string(
        value["network_session_id"], "action_ledger.network_session_id"
    )
    wrapper = _file_identity(value["wrapper"], "action_ledger.wrapper")
    actions = value["actions"]
    if not isinstance(actions, list):
        raise ProvenanceCaptureError("action_ledger.actions must be an array")
    if len(actions) > MAX_ACTIONS:
        raise ProvenanceCaptureError("action ledger exceeds the action-count bound")
    supplied_digest = value["ledger_sha256"]
    if supplied_digest is not None:
        supplied_digest = _sha256(
            supplied_digest, "action_ledger.ledger_sha256"
        )

    materialized: list[dict[str, Any]] = []
    action_ids: set[str] = set()
    for index, action in enumerate(actions):
        try:
            record = materialize_action_record(action)
        except ProvenanceCaptureError as error:
            raise ProvenanceCaptureError(f"action_ledger.actions[{index}]: {error}") from error
        sequence_index = record["sequence_index"]
        if sequence_index != index:
            raise ProvenanceCaptureError(
                "action sequence_index values must be exact contiguous array indices"
            )
        if record["action_id"] in action_ids:
            raise ProvenanceCaptureError("action IDs must be unique")
        if record["network_session_id"] != network_session_id:
            raise ProvenanceCaptureError(
                "action network_session_id differs from the ledger session"
            )
        action_ids.add(record["action_id"])
        materialized.append(record)

    digest = compute_action_ledger_sha256(materialized)
    if supplied_digest is not None and supplied_digest != digest:
        raise ProvenanceCaptureError("supplied action_ledger.ledger_sha256 differs")
    return {
        "actions": materialized,
        "ledger_sha256": digest,
        "network_session_id": network_session_id,
        "schema_version": ACTION_LEDGER_SCHEMA,
        "wrapper": wrapper,
    }


def package_provenance(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deterministic, non-authorizing provenance package.

    This function validates packaging invariants only.  It does not authenticate
    any named file, replay an action, or establish build eligibility.
    """

    if not isinstance(value, Mapping):
        raise ProvenanceCaptureError("provenance input must be a JSON object")
    packaged = _json_clone(dict(value), label="provenance input")
    _exact_keys(packaged, TOP_LEVEL_KEYS, "provenance input")
    if packaged["schema_version"] != PROVENANCE_SCHEMA:
        raise ProvenanceCaptureError("provenance schema_version differs")
    if packaged["epoch_id"] != EPOCH_ID:
        raise ProvenanceCaptureError("provenance epoch_id differs")
    if canonical_json_bytes(packaged["classification"]) != canonical_json_bytes(
        PROVENANCE_CLASSIFICATION
    ):
        raise ProvenanceCaptureError(
            "provenance classification must be the exact non-authorizing classification"
        )
    if not isinstance(packaged["identity_bindings"], dict):
        raise ProvenanceCaptureError("identity_bindings must be a JSON object")
    forbidden = sorted(
        set(packaged["identity_bindings"]) & FORBIDDEN_BUILD_BINDINGS
    )
    if forbidden:
        raise ProvenanceCaptureError(
            f"build provenance cannot bind later or scientific artifacts: {forbidden}"
        )

    expected_section_types: tuple[tuple[str, type], ...] = (
        ("roots", list),
        ("build_policy", dict),
        ("environments", list),
        ("toolchain", dict),
        ("sdk", dict),
        ("dependencies", dict),
        ("network_isolation", dict),
        ("object_graph", dict),
        ("common_objects", dict),
        ("products", list),
    )
    for field, expected_type in expected_section_types:
        if not isinstance(packaged[field], expected_type):
            raise ProvenanceCaptureError(
                f"{field} must be a JSON {expected_type.__name__}"
            )

    _reject_authorizing_flags(packaged)
    packaged["action_ledger"] = materialize_action_ledger(
        packaged["action_ledger"]
    )
    receipt = packaged["network_isolation"]
    if "action_ledger_sha256" not in receipt:
        raise ProvenanceCaptureError(
            "network_isolation must contain action_ledger_sha256"
        )
    ledger_sha = packaged["action_ledger"]["ledger_sha256"]
    supplied_receipt_sha = receipt["action_ledger_sha256"]
    if supplied_receipt_sha is None:
        receipt["action_ledger_sha256"] = ledger_sha
    elif _sha256(
        supplied_receipt_sha, "network_isolation.action_ledger_sha256"
    ) != ledger_sha:
        raise ProvenanceCaptureError(
            "network isolation receipt differs from materialized action ledger"
        )
    if receipt.get("session_id") != packaged["action_ledger"]["network_session_id"]:
        raise ProvenanceCaptureError(
            "network isolation session differs from materialized action ledger"
        )
    _reject_authorizing_flags(packaged)
    # Exercise the final encoder here so callers cannot receive an object that
    # the publication primitive would later fail to canonicalize.
    canonical_json_bytes(packaged)
    return packaged


def _no_follow_flag() -> int:
    flag = getattr(os, "O_NOFOLLOW", None)
    if flag is None:
        raise ProvenanceCaptureError("platform lacks required no-follow file opens")
    return flag


def _stable_file_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_uid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def read_json_file(path: Path, *, maximum_bytes: int = MAX_JSON_BYTES) -> Any:
    """Read one stable, regular, non-symlink JSON input with a hard size cap."""

    if (
        type(maximum_bytes) is not int
        or maximum_bytes < 1
        or maximum_bytes > MAX_JSON_BYTES
    ):
        raise ProvenanceCaptureError("maximum_bytes must be within the fixed JSON ceiling")
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    canonical = Path(os.path.abspath(candidate))
    if os.path.realpath(canonical) != os.fspath(canonical):
        raise ProvenanceCaptureError("JSON input path must not contain symlink ancestors")
    flags = os.O_RDONLY | os.O_CLOEXEC | _no_follow_flag()
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(canonical, flags)
    except OSError as error:
        raise ProvenanceCaptureError(f"cannot open JSON input: {error}") from error
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) & 0o022
        ):
            raise ProvenanceCaptureError(
                "JSON input must be an euid-owned, non-writable-by-others, "
                "single-link regular file"
            )
        if before.st_size < 0 or before.st_size > maximum_bytes:
            raise ProvenanceCaptureError("JSON input exceeds its byte bound")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            try:
                chunk = os.read(fd, min(1 << 20, remaining))
            except InterruptedError:
                continue
            if not chunk:
                raise ProvenanceCaptureError("JSON input ended before its stable size")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            raise ProvenanceCaptureError("JSON input grew while being read")
        after = os.fstat(fd)
        named = os.stat(canonical, follow_symlinks=False)
        if (
            _stable_file_identity(before) != _stable_file_identity(after)
            or _stable_file_identity(after) != _stable_file_identity(named)
        ):
            raise ProvenanceCaptureError("JSON input changed while being read")
        return parse_json_bytes(b"".join(chunks), label=str(canonical))
    finally:
        os.close(fd)


def _canonical_output_path(path: Path) -> Path:
    requested = Path(path).expanduser()
    if not requested.is_absolute():
        requested = Path.cwd() / requested
    if requested.name in ("", ".", "..") or "\x00" in requested.name:
        raise ProvenanceCaptureError("output must name one ordinary file")
    if len(requested.name.encode("utf-8")) > 255:
        raise ProvenanceCaptureError("output filename exceeds 255 UTF-8 bytes")
    try:
        parent = requested.parent.resolve(strict=True)
    except OSError as error:
        raise ProvenanceCaptureError(f"cannot resolve output parent: {error}") from error
    requested_parent = Path(os.path.abspath(requested.parent))
    if os.path.realpath(requested_parent) != os.fspath(requested_parent):
        raise ProvenanceCaptureError("output parent path must not contain symlink ancestors")
    canonical = parent / requested.name
    parts = tuple(unicodedata.normalize("NFC", part).casefold() for part in canonical.parts)
    forbidden_roots = (
        ("comparison", "plans", "simulator-demo", "v1", "physics-stress-test", "epoch-1"),
        ("results", "simulator-demo", "v1", "physics-stress-test", "epoch-1"),
    )
    forbidden_file = (
        "docs",
        "simulator",
        "physics_execution_identities_epoch_1_v1.json",
    )
    def contains(sequence: tuple[str, ...]) -> bool:
        return any(
            tuple(parts[index : index + len(sequence)]) == sequence
            for index in range(0, len(parts) - len(sequence) + 1)
        )
    if any(contains(sequence) for sequence in forbidden_roots) or contains(
        forbidden_file
    ):
        raise ProvenanceCaptureError(
            "packager output may not occupy an official plan/result/identity namespace"
        )
    return canonical


def _open_private_parent(path: Path) -> tuple[Path, int, tuple[int, ...]]:
    canonical = _canonical_output_path(path)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | _no_follow_flag()
    parent_fd: int | None = None
    try:
        before = os.stat(canonical.parent, follow_symlinks=False)
        parent_fd = os.open(canonical.parent, flags)
        opened = os.fstat(parent_fd)
        named = os.stat(canonical.parent, follow_symlinks=False)
    except OSError as error:
        if parent_fd is not None:
            os.close(parent_fd)
        raise ProvenanceCaptureError(f"cannot open output parent: {error}") from error
    assert parent_fd is not None
    directory_identity = (
        opened.st_dev,
        opened.st_ino,
        opened.st_mode,
        opened.st_uid,
    )
    if (
        not stat.S_ISDIR(opened.st_mode)
        or opened.st_uid != os.geteuid()
        or stat.S_IMODE(opened.st_mode) & 0o022
        or (before.st_dev, before.st_ino, before.st_mode, before.st_uid)
        != directory_identity
        or (named.st_dev, named.st_ino, named.st_mode, named.st_uid)
        != directory_identity
    ):
        os.close(parent_fd)
        raise ProvenanceCaptureError(
            "output parent must be a stable euid-owned directory not writable "
            "by group or other users"
        )
    return canonical, parent_fd, directory_identity


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    written = 0
    while written < len(view):
        try:
            count = os.write(fd, view[written:])
        except InterruptedError:
            continue
        if count <= 0:
            raise ProvenanceCaptureError("canonical provenance write made no progress")
        written += count


def _cleanup_same_inode(parent_fd: int, name: str, identity: tuple[int, int]) -> None:
    """Best-effort inode-checked cleanup inside the required private parent."""

    try:
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            stat.S_ISREG(named.st_mode)
            and (named.st_dev, named.st_ino) == identity
        ):
            os.unlink(name, dir_fd=parent_fd)
            try:
                os.fsync(parent_fd)
            except OSError:
                pass
    except OSError:
        pass


def write_exclusive_canonical(path: Path, value: Any) -> CapturedProvenance:
    """Publish one canonical JSON file create-exclusively and durably.

    The parent must already exist, be owned by the effective user, and not be
    group/world writable. Cleanup is best-effort and inode-checked. Hostile or
    concurrent mutation by another process with the same effective UID is
    outside this non-authorizing packager's protection model; a future causal
    capture publisher must run inside a separately protected single-writer
    namespace.
    """

    payload = canonical_json_bytes(value)
    canonical, parent_fd, parent_identity = _open_private_parent(Path(path))
    fd: int | None = None
    stage_name: str | None = None
    created_identity: tuple[int, int] | None = None
    final_link_created = False
    complete = False
    try:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | os.O_CLOEXEC
            | _no_follow_flag()
        )
        for _ in range(32):
            candidate = (
                f".physics-build-provenance-stage-{os.getpid()}-"
                f"{secrets.token_hex(16)}"
            )
            try:
                fd = os.open(candidate, flags, 0o400, dir_fd=parent_fd)
                stage_name = candidate
                break
            except FileExistsError:
                continue
            except OSError as error:
                raise ProvenanceCaptureError(
                    f"cannot create private provenance stage: {error}"
                ) from error
        if fd is None or stage_name is None:
            raise ProvenanceCaptureError("cannot allocate a unique private provenance stage")

        created = os.fstat(fd)
        created_identity = (created.st_dev, created.st_ino)
        if (
            not stat.S_ISREG(created.st_mode)
            or created.st_nlink != 1
            or created.st_uid != os.geteuid()
        ):
            raise ProvenanceCaptureError("new provenance output identity is unsafe")
        os.fchmod(fd, 0o400)
        _write_all(fd, payload)
        os.fsync(fd)

        after = os.fstat(fd)
        named = os.stat(stage_name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(after.st_mode)
            or after.st_nlink != 1
            or after.st_uid != os.geteuid()
            or stat.S_IMODE(after.st_mode) != 0o400
            or after.st_size != len(payload)
            or _stable_file_identity(after) != _stable_file_identity(named)
        ):
            raise ProvenanceCaptureError(
                "private provenance stage changed during publication"
            )
        parent_named = os.stat(canonical.parent, follow_symlinks=False)
        if (
            parent_named.st_dev,
            parent_named.st_ino,
            parent_named.st_mode,
            parent_named.st_uid,
        ) != parent_identity:
            raise ProvenanceCaptureError("output parent identity changed")

        os.close(fd)
        fd = None

        # A same-directory hard-link is an atomic no-replace publication: the
        # final basename cannot become visible until the complete, fsynced
        # stage exists, and an existing destination is never overwritten.
        try:
            os.link(
                stage_name,
                canonical.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
                follow_symlinks=False,
            )
            final_link_created = True
        except FileExistsError as error:
            raise ProvenanceCaptureError(
                "output already exists; provenance publication is create-exclusive"
            ) from error
        except OSError as error:
            raise ProvenanceCaptureError(
                f"cannot atomically publish provenance output: {error}"
            ) from error
        final_named = os.stat(canonical.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(final_named.st_mode)
            or (final_named.st_dev, final_named.st_ino) != created_identity
            or final_named.st_nlink != 2
            or final_named.st_uid != os.geteuid()
            or stat.S_IMODE(final_named.st_mode) != 0o400
            or final_named.st_size != len(payload)
        ):
            raise ProvenanceCaptureError("atomically published provenance identity differs")
        os.unlink(stage_name, dir_fd=parent_fd)
        stage_name = None
        final_named = os.stat(canonical.name, dir_fd=parent_fd, follow_symlinks=False)
        if final_named.st_nlink != 1 or (
            final_named.st_dev,
            final_named.st_ino,
        ) != created_identity:
            raise ProvenanceCaptureError("published provenance link count differs")
        os.fsync(parent_fd)
        complete = True
        return CapturedProvenance(
            path=canonical,
            bytes=len(payload),
            sha256=sha256_bytes(payload),
        )
    except ProvenanceCaptureError:
        raise
    except OSError as error:
        raise ProvenanceCaptureError(
            f"cannot complete provenance publication: {error}"
        ) from error
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if not complete and created_identity is not None:
            if final_link_created:
                _cleanup_same_inode(parent_fd, canonical.name, created_identity)
            if stage_name is not None:
                _cleanup_same_inode(parent_fd, stage_name, created_identity)
        os.close(parent_fd)


def capture_provenance(input_path: Path, output_path: Path) -> CapturedProvenance:
    """Read, package, and create-exclusively publish supplied provenance JSON."""

    supplied = read_json_file(Path(input_path))
    if not isinstance(supplied, dict):
        raise ProvenanceCaptureError("provenance input must be a JSON object")
    packaged = package_provenance(supplied)
    published = write_exclusive_canonical(Path(output_path), packaged)
    return CapturedProvenance(
        path=published.path,
        bytes=published.bytes,
        sha256=published.sha256,
        ledger_sha256=packaged["action_ledger"]["ledger_sha256"],
    )


def _receipt(captured: CapturedProvenance) -> dict[str, Any]:
    if captured.ledger_sha256 is None:
        raise ProvenanceCaptureError(
            "capture receipt requires the packaged action-ledger identity"
        )
    return {
        "artifact": captured.as_json(),
        "classification": {
            "causal_capture_verified": False,
            "data_packaged": True,
            "execution_authorized": False,
            "promotion_eligible": False,
            "provenance_complete": False,
            "scientific_evidence": False,
            "scientific_evidence_eligible": False,
        },
        "ledger_sha256": captured.ledger_sha256,
        "schema_version": CAPTURE_RECEIPT_SCHEMA,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Canonically package independently supplied, non-authorizing "
            "planar-physics build provenance"
        )
    )
    parser.add_argument(
        "--input",
        "--input-json",
        dest="input_path",
        required=True,
        type=Path,
        help="strict JSON provenance draft supplied by the capture system",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="new canonical JSON destination in an existing private parent",
    )
    args = parser.parse_args(argv)
    try:
        captured = capture_provenance(args.input_path, args.output)
    except ProvenanceCaptureError as error:
        print(f"physics build provenance capture rejected: {error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(canonical_json_bytes(_receipt(captured)))
    return 0


__all__ = (
    "ACTION_FIELDS",
    "ACTION_ID_DOMAIN",
    "ACTION_LEDGER_DOMAIN",
    "ACTION_LEDGER_SCHEMA",
    "CapturedProvenance",
    "EPOCH_ID",
    "PROVENANCE_CLASSIFICATION",
    "PROVENANCE_SCHEMA",
    "ProvenanceCaptureError",
    "action_id",
    "action_ledger_sha256",
    "canonical_json_bytes",
    "capture_provenance",
    "compute_action_id",
    "compute_action_ledger_sha256",
    "main",
    "materialize_action_ledger",
    "materialize_action_record",
    "package_provenance",
    "parse_json_bytes",
    "read_json_file",
    "sha256_bytes",
    "write_exclusive_canonical",
)


if __name__ == "__main__":
    raise SystemExit(main())
