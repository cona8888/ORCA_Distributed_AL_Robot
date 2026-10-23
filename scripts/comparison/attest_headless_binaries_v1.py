#!/usr/bin/env python3
"""Attest the existing trace-v3 binary quartet without executing it.

This module performs byte-level, read-only authentication.  It is not a
builder, simulator runner, plan freezer, validation gate, or benchmark
authorizer.  Publication is categorically disabled at this code-only
checkpoint; the attestation can only be constructed and validated in memory.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = "dancers-headless-binary-attestation-v1"
TRACK_ID = "simulator-demo-v1-headless-epoch-1"
BUILD_MANIFEST_PATH = "comparison/build_manifest.json"
PIXI_LOCK_PATH = "pixi.lock"
EXPECTED_BUILD_MANIFEST_SHA256 = (
    "57a154c7a366583ff4a6cd742d1e586575899baef823544feef2b606aad142d0"
)
EXPECTED_BUILD_MANIFEST_BYTES = 9_718
EXPECTED_PIXI_LOCK_SHA256 = (
    "636ecf1cf31431ffc19f453fef39299c3243718477e321fcea8814225bfe68be"
)
EXPECTED_PIXI_LOCK_BYTES = 31_051
MAX_MANIFEST_BYTES = 64 * 1024
MAX_LOCK_BYTES = 2 * 1024 * 1024
MAX_BINARY_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 256 * 1024
MAX_PATH_BYTES = 1024
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
UTC_SECONDS = re.compile(
    r"^(?:19|20)[0-9]{2}-(?:0[1-9]|1[0-2])-"
    r"(?:0[1-9]|[12][0-9]|3[01])T(?:[01][0-9]|2[0-3]):"
    r"[0-5][0-9]:[0-5][0-9]Z$"
)

MACHO_64_ARM64_MAGIC = b"\xcf\xfa\xed\xfe"
MACHO_CPU_TYPE_ARM64 = 0x0100000C
MACHO_FILE_TYPE_EXECUTE = 2
OBSERVER_MARKERS = (
    b"DANCERS_REQUIRE_PROVENANCE\x00",
    b"DANCERS_RUN_METADATA\x00",
    b"DANCERS_SUMMARY_OUT\x00",
    b"DANCERS_TRACE_DETAIL\x00",
    b"DANCERS_TRACE_OUT\x00",
    b"dancers-comparison-trace-v3\x00",
)

EXPECTED_REPOSITORY = {
    "reference_pin": "986f47deec5b079663c7d7f6120bfc20f0467901",
    "optimized_pin": "99364a41f1535a4bb0eddbe5a10030127ea77086",
    "common_patch_commit": "26c8c6f9b262a2114ac28203419cb03caf8b0d88",
    "common_patch_format_email_sha256": (
        "2ab81c9a9621c89de5854bbd8496cb2124845733140a913855ca61677185a95e"
    ),
    "common_patch_full_index_diff_sha256": (
        "5641cb37493f2c62618c43301459d7ce5e33c95101d344ba3157a9ae478de3f8"
    ),
    "reference_applied_head": "de0f60d1ad4b944c2b3264b8e3ccd348708b8b8a",
    "reference_tree": "cb93386e28f3824ea7ffc17605b441b92f13f87d",
    "optimized_applied_head": "26c8c6f9b262a2114ac28203419cb03caf8b0d88",
    "optimized_tree": "b6f713f47ea3aa57c824cf2c481f3647ac7c9408",
}

EXPECTED_COMMON_PATCH_HISTORY = (
    {
        "purpose": "common comparison correctness and observer layer",
        "optimized_commit": "a992bee99ce64ae6548698ddcc48811e50fa47e7",
        "format_email_sha256": (
            "feba83f12d25f22ba04857b82dee514e75e8136cff8416f872e83cf0fb41359b"
        ),
        "full_index_diff_sha256": (
            "0eb4722ce67646d75c85fcd20b66bb7f62b019601db613af052e25774248edfc"
        ),
    },
    {
        "purpose": "planning-only result logging bounds correction",
        "optimized_commit": "91f5f05735ba249c657bb8789597635f0755d74f",
        "format_email_sha256": (
            "629e50f9dada3e2f95808a29aada750c53bc7fb21c7f85f99bbc08aac7e56df1"
        ),
        "full_index_diff_sha256": (
            "12d1d18f6e6a923c74604ef5cbc54fb3e69cb08907aab80721ef8585959d1c2a"
        ),
    },
    {
        "purpose": "compact trace-v3 semantic observer and storage correction",
        "optimized_commit": "26c8c6f9b262a2114ac28203419cb03caf8b0d88",
        "format_email_sha256": (
            "2ab81c9a9621c89de5854bbd8496cb2124845733140a913855ca61677185a95e"
        ),
        "full_index_diff_sha256": (
            "5641cb37493f2c62618c43301459d7ce5e33c95101d344ba3157a9ae478de3f8"
        ),
    },
)

EXPECTED_BUILDS: dict[str, dict[str, Any]] = {
    "reference_fidelity": {
        "algorithm_revision": EXPECTED_REPOSITORY["reference_pin"],
        "applied_head": EXPECTED_REPOSITORY["reference_applied_head"],
        "binary_path": "build/comparison/trace-v3/build/reference-fidelity/dancers",
        "binary_sha256": (
            "45d894ae4ef5212672347b9e8351e366e47cc1eb809f8cb48900263624a03f6e"
        ),
        "binary_bytes": 2_419_320,
        "build_flavor": "fidelity",
        "method": "reference",
        "observer_compiled": True,
        "run_classes": ("digest_fidelity", "positions_replay"),
        "source_dir": "build/comparison/trace-v3/reference-src",
    },
    "reference_timing": {
        "algorithm_revision": EXPECTED_REPOSITORY["reference_pin"],
        "applied_head": EXPECTED_REPOSITORY["reference_applied_head"],
        "binary_path": "build/comparison/trace-v3/build/reference-timing/dancers",
        "binary_sha256": (
            "a84e8fb74933f945cba131ebb11438e6666321fd0121ad09eef4a954e006ea68"
        ),
        "binary_bytes": 2_182_408,
        "build_flavor": "timing",
        "method": "reference",
        "observer_compiled": False,
        "run_classes": ("headless_timing",),
        "source_dir": "build/comparison/trace-v3/reference-src",
    },
    "optimized_fidelity": {
        "algorithm_revision": EXPECTED_REPOSITORY["optimized_pin"],
        "applied_head": EXPECTED_REPOSITORY["optimized_applied_head"],
        "binary_path": "build/comparison/trace-v3/build/optimized-fidelity/dancers",
        "binary_sha256": (
            "3886d9df72356d0dd71e94a95cea6998fc62b60401ed8701496e7f280bfaf081"
        ),
        "binary_bytes": 2_403_032,
        "build_flavor": "fidelity",
        "method": "optimized",
        "observer_compiled": True,
        "run_classes": ("digest_fidelity", "positions_replay"),
        "source_dir": "build/comparison/trace-v3/optimized-src",
    },
    "optimized_timing": {
        "algorithm_revision": EXPECTED_REPOSITORY["optimized_pin"],
        "applied_head": EXPECTED_REPOSITORY["optimized_applied_head"],
        "binary_path": "build/comparison/trace-v3/build/optimized-timing/dancers",
        "binary_sha256": (
            "c060992bc411aab7b05ebbb9da30af34bc96f597bb8c6585024acf9892c6df30"
        ),
        "binary_bytes": 2_182_568,
        "build_flavor": "timing",
        "method": "optimized",
        "observer_compiled": False,
        "run_classes": ("headless_timing",),
        "source_dir": "build/comparison/trace-v3/optimized-src",
    },
}

CLAIM_FLAGS = {
    "benchmark_claim_ready": False,
    "benchmark_execution_authorized": False,
    "calibration_execution_authorized": False,
    "capacity_claim_ready": False,
    "execution_authorized": False,
    "fidelity_claim_ready": False,
    "plan_frozen": False,
    "registered_scientific_run": False,
    "scientific_evidence": False,
    "scientific_evidence_eligible": False,
    "timing_claim_ready": False,
    "validation_claim_ready": False,
    "validation_execution_authorized": False,
    "validation_results_present": False,
}

OPERATIONS = {
    "build_invoked": False,
    "network_accessed": False,
    "plan_written": False,
    "result_written": False,
    "simulator_invoked": False,
}

EVIDENCE_BOUNDARY = (
    "identity-only attestation of the byte-pinned manifest, Pixi lock, and existing Mach-O binaries",
    "source pins, applied heads, trees, and common-patch identities are authenticated manifest assertions, not a fresh live Git checkout proof",
    "headless display and drawing off are mandatory runtime constraints and are not proven by this binary-only attestation",
    "no build, simulator, scientific process, network access, plan write, or result write occurred",
)

RUN_CLASS_CONSTRAINTS = {
    "digest_fidelity": {
        "observer_compiled": True,
        "timed": False,
        "trace_detail": "digest",
    },
    "headless_timing": {
        "display": False,
        "drawing": False,
        "observer_compiled": False,
        "timed": True,
    },
    "positions_replay": {
        "observer_compiled": True,
        "timed": False,
        "trace_detail": "positions",
    },
}


class BinaryAttestationError(ValueError):
    """The fixed binary quartet cannot be authenticated safely."""


@dataclass(frozen=True)
class StableFile:
    path: Path
    raw: bytes
    mode: int

    @property
    def bytes(self) -> int:
        return len(self.raw)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()


@dataclass(frozen=True)
class PublishedAttestation:
    path: Path
    bytes: int
    sha256: str

    def as_json(self) -> dict[str, Any]:
        return {
            "bytes": self.bytes,
            "path": str(self.path),
            "sha256": self.sha256,
        }


class _DuplicateKey(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value!r} is forbidden")


def canonical_json_bytes(value: Any) -> bytes:
    """Encode finite JSON deterministically with exactly one terminal LF."""

    try:
        raw = (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise BinaryAttestationError("value is not finite canonical JSON") from exc
    if len(raw) > MAX_OUTPUT_BYTES:
        raise BinaryAttestationError("canonical JSON exceeds the output bound")
    return raw


def _strict_json(raw: bytes, label: str) -> Any:
    try:
        return json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise BinaryAttestationError(f"{label} is not strict UTF-8 JSON") from exc


def _canonical_relative(value: Any, label: str) -> str:
    if type(value) is not str or not value or "\\" in value or "\x00" in value:
        raise BinaryAttestationError(f"{label} is not a canonical relative path")
    pure = PurePosixPath(value)
    if (
        pure.is_absolute()
        or pure.as_posix() != value
        or any(part in ("", ".", "..") for part in pure.parts)
        or len(value.encode("utf-8", "strict")) > MAX_PATH_BYTES
    ):
        raise BinaryAttestationError(f"{label} is not a canonical relative path")
    return value


def _absolute_root(root: Path) -> Path:
    candidate = Path(os.path.abspath(os.fspath(root)))
    if os.path.realpath(candidate) != os.fspath(candidate):
        raise BinaryAttestationError("repository root has a symlink component")
    try:
        metadata = os.stat(candidate, follow_symlinks=False)
    except OSError as exc:
        raise BinaryAttestationError("repository root is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise BinaryAttestationError("repository root is not a directory")
    return candidate


def _bound_path(root: Path, relative: Any, label: str) -> Path:
    rendered = _canonical_relative(relative, label)
    candidate = Path(os.path.abspath(root / rendered))
    if os.path.commonpath((os.fspath(root), os.fspath(candidate))) != os.fspath(root):
        raise BinaryAttestationError(f"{label} escapes the repository root")
    return candidate


def _identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_nlink,
        metadata.st_uid,
        metadata.st_gid,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _required_flag(name: str) -> int:
    value = getattr(os, name, None)
    if type(value) is not int or value == 0:
        raise BinaryAttestationError(f"required safe-open flag {name} is unavailable")
    return value


def _directory_open_flags() -> int:
    return (
        os.O_RDONLY
        | _required_flag("O_DIRECTORY")
        | _required_flag("O_NOFOLLOW")
        | _required_flag("O_CLOEXEC")
    )


def _open_directory_path_exact(path: Path, label: str) -> int:
    absolute = Path(os.path.abspath(os.fspath(path)))
    flags = _directory_open_flags()
    try:
        current = os.open(os.sep, flags)
    except OSError as error:
        raise BinaryAttestationError(
            f"cannot open filesystem root for {label}"
        ) from error
    try:
        for part in absolute.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=current)
            except OSError as error:
                raise BinaryAttestationError(
                    f"cannot traverse {label} without following symlinks"
                ) from error
            try:
                _close_descriptor_exact(current, f"{label} ancestor")
            except BaseException as primary:
                try:
                    _close_descriptor_exact(child, f"{label} child")
                except BaseException:
                    pass
                raise primary
            current = child
        metadata = os.fstat(current)
        if not stat.S_ISDIR(metadata.st_mode):
            raise BinaryAttestationError(f"{label} is not a directory")
        return current
    except BaseException as primary:
        try:
            _close_descriptor_exact(current, label)
        except BaseException:
            pass
        raise primary


def _read_stable_regular(
    path: Path,
    *,
    maximum_bytes: int,
    label: str,
    executable: bool,
) -> StableFile:
    if type(maximum_bytes) is not int or maximum_bytes < 1:
        raise BinaryAttestationError("invalid stable-read byte bound")
    candidate = Path(os.path.abspath(os.fspath(path)))
    if os.path.realpath(candidate) != os.fspath(candidate):
        raise BinaryAttestationError(f"{label} has a symlink ancestor or leaf")
    flags = os.O_RDONLY | _required_flag("O_CLOEXEC") | _required_flag("O_NOFOLLOW")
    parent_descriptor: int | None = None
    descriptor: int | None = None
    before: os.stat_result | None = None
    after: os.stat_result | None = None
    named: os.stat_result | None = None
    parent_before: tuple[int, int] | None = None
    chunks: list[bytes] = []
    primary: BaseException | None = None
    try:
        parent_descriptor = _open_directory_path_exact(
            candidate.parent, f"{label} parent"
        )
        parent_metadata = os.fstat(parent_descriptor)
        parent_before = (parent_metadata.st_dev, parent_metadata.st_ino)
        descriptor = os.open(candidate.name, flags, dir_fd=parent_descriptor)
        before = os.fstat(descriptor)
        permissions = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or permissions & 0o022
            or before.st_size < 1
            or before.st_size > maximum_bytes
            or bool(permissions & 0o111) is not executable
        ):
            raise BinaryAttestationError(
                f"{label} type, owner, mode, links, or size is unsafe"
            )
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                raise BinaryAttestationError(f"{label} was truncated while read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise BinaryAttestationError(f"{label} grew while read")
        after = os.fstat(descriptor)
        named = os.stat(
            candidate.name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        reopened_parent = _open_directory_path_exact(
            candidate.parent, f"{label} parent reauthentication"
        )
        try:
            reopened_metadata = os.fstat(reopened_parent)
            if (reopened_metadata.st_dev, reopened_metadata.st_ino) != parent_before:
                raise BinaryAttestationError(f"{label} parent identity changed")
        finally:
            _close_descriptor_exact(
                reopened_parent, f"{label} parent reauthentication"
            )
    except BaseException as error:
        primary = error
    cleanup_error: BaseException | None = None
    if descriptor is not None:
        try:
            _close_descriptor_exact(descriptor, label)
        except BaseException as error:
            cleanup_error = error
    if parent_descriptor is not None:
        try:
            _close_descriptor_exact(parent_descriptor, f"{label} parent")
        except BaseException as error:
            if cleanup_error is None:
                cleanup_error = error
    if primary is not None:
        if isinstance(primary, OSError):
            action = "open" if descriptor is None else "read"
            raise BinaryAttestationError(f"cannot {action} {label}") from primary
        raise primary
    if cleanup_error is not None:
        raise BinaryAttestationError(f"cannot close {label}") from cleanup_error
    assert before is not None and after is not None and named is not None
    if _identity(before) != _identity(after) or _identity(after) != _identity(named):
        raise BinaryAttestationError(f"{label} identity changed while read")
    return StableFile(candidate, b"".join(chunks), stat.S_IMODE(after.st_mode))


def _exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise BinaryAttestationError(f"{label} schema differs")
    return value


def _exact_value(value: Any, expected: Any, label: str) -> None:
    if type(value) is not type(expected) or value != expected:
        raise BinaryAttestationError(f"{label} differs")


def _json_exact(value: Any, expected: Any) -> bool:
    """Compare JSON values without Python's bool/integer equivalence."""

    if type(value) is not type(expected):
        return False
    if type(expected) is dict:
        return set(value) == set(expected) and all(
            _json_exact(value[key], expected[key]) for key in expected
        )
    if type(expected) is list:
        return len(value) == len(expected) and all(
            _json_exact(left, right) for left, right in zip(value, expected)
        )
    return value == expected


def _hash(value: Any, label: str, *, length: int = 64) -> str:
    pattern = HEX64 if length == 64 else HEX40
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise BinaryAttestationError(f"{label} is not a lowercase hexadecimal hash")
    return value


def _validate_smoke(value: Any, *, historical: bool) -> None:
    common = {
        "both_collision_events",
        "both_invalid_geometry_evaluations",
        "both_run_valid",
        "canonical_semantic_sha256",
        "environment",
        "frames_compared",
        "optimized_raw_trace_sha256",
        "optimized_summary_sha256",
        "paper_metrics_equal",
        "purpose",
        "reference_raw_trace_sha256",
        "reference_summary_sha256",
        "robot_count",
        "semantic_equal",
        "status",
        "timesteps",
        "trace_detail",
    }
    current_only = {
        "canonical_summary_sha256",
        "digest_algorithm",
        "digest_state_schema",
        "optimized_trace_bytes",
        "reference_trace_bytes",
        "trace_schema_version",
    }
    record = _exact_object(
        value, common if historical else common | current_only, "build manifest smoke"
    )
    bool_fields = {
        "both_run_valid",
        "paper_metrics_equal",
        "semantic_equal",
    }
    int_fields = {
        "both_collision_events",
        "both_invalid_geometry_evaluations",
        "frames_compared",
        "robot_count",
        "timesteps",
    }
    if not historical:
        int_fields |= {"optimized_trace_bytes", "reference_trace_bytes"}
    hash_fields = {key for key in record if key.endswith("_sha256")}
    for key, item in record.items():
        if key in bool_fields and type(item) is not bool:
            raise BinaryAttestationError(f"build manifest smoke.{key} type differs")
        if key in int_fields and (type(item) is not int or item < 0):
            raise BinaryAttestationError(f"build manifest smoke.{key} type differs")
        if key in hash_fields:
            _hash(item, f"build manifest smoke.{key}")
        if key not in bool_fields | int_fields | hash_fields and type(item) is not str:
            raise BinaryAttestationError(f"build manifest smoke.{key} type differs")


def _validate_manifest(manifest: Any) -> dict[str, Any]:
    top = _exact_object(
        manifest,
        {
            "builds",
            "created_date",
            "fidelity_smoke",
            "gates",
            "historical_fidelity_smoke_v2",
            "repository",
            "schema_version",
            "scope",
            "status",
            "toolchain",
            "trace_contract",
        },
        "build manifest",
    )
    _exact_value(top["schema_version"], "dancers-comparison-build-manifest-v1", "build manifest schema")
    _exact_value(top["created_date"], "2026-07-28", "build manifest date")
    _exact_value(
        top["status"],
        "dual-build-parity-passed-smoke-fidelity-only",
        "build manifest status",
    )
    _exact_value(
        top["scope"],
        "local-only source-pinned Release trace-v3 builds; no calibration or validation result",
        "build manifest scope",
    )

    repository = _exact_object(
        top["repository"],
        {
            "common_patch_commit",
            "common_patch_format_email_sha256",
            "common_patch_full_index_diff_sha256",
            "common_patch_history",
            "optimized_applied_head",
            "optimized_pin",
            "optimized_tree",
            "origin_present",
            "patched_cross_tree_difference",
            "reference_applied_head",
            "reference_pin",
            "reference_tree",
            "remote_count",
        },
        "build manifest.repository",
    )
    _exact_value(repository["remote_count"], 0, "repository.remote_count")
    _exact_value(repository["origin_present"], False, "repository.origin_present")
    for key, expected in EXPECTED_REPOSITORY.items():
        _exact_value(repository[key], expected, f"repository.{key}")
        _hash(repository[key], f"repository.{key}", length=40 if key.endswith(("pin", "head", "tree", "commit")) else 64)
    history = repository["common_patch_history"]
    if type(history) is not list or len(history) != len(EXPECTED_COMMON_PATCH_HISTORY):
        raise BinaryAttestationError("repository.common_patch_history schema differs")
    for index, (item, expected) in enumerate(zip(history, EXPECTED_COMMON_PATCH_HISTORY)):
        record = _exact_object(item, set(expected), f"common patch history[{index}]")
        for key, expected_value in expected.items():
            _exact_value(record[key], expected_value, f"common patch history[{index}].{key}")
    cross_tree = _exact_object(
        repository["patched_cross_tree_difference"],
        {"path_count", "paths", "rule", "status"},
        "repository.patched_cross_tree_difference",
    )
    _exact_value(cross_tree["status"], "pass", "cross-tree status")
    if (
        type(cross_tree["path_count"]) is not int
        or cross_tree["path_count"] != 12
        or type(cross_tree["rule"]) is not str
        or type(cross_tree["paths"]) is not list
        or len(cross_tree["paths"]) != 12
        or any(type(item) is not str for item in cross_tree["paths"])
        or len(set(cross_tree["paths"])) != 12
    ):
        raise BinaryAttestationError("cross-tree difference schema differs")

    toolchain = _exact_object(
        top["toolchain"],
        {
            "build_type",
            "cmake_version",
            "compiler",
            "generator",
            "macos_version",
            "pixi_lock_sha256",
            "pixi_version",
            "platform",
        },
        "build manifest.toolchain",
    )
    expected_toolchain = {
        "build_type": "Release",
        "cmake_version": "4.4.0",
        "compiler": "clang++ 19.1.7",
        "generator": "Ninja",
        "macos_version": "26.5.1",
        "pixi_lock_sha256": EXPECTED_PIXI_LOCK_SHA256,
        "pixi_version": "0.72.2",
        "platform": "osx-arm64",
    }
    for key, expected in expected_toolchain.items():
        _exact_value(toolchain[key], expected, f"toolchain.{key}")

    builds = _exact_object(top["builds"], set(EXPECTED_BUILDS), "build manifest.builds")
    common_build_keys = {
        "algorithm_revision",
        "applied_head",
        "binary_path",
        "binary_sha256",
        "clean_build_log_sha256",
        "cmake_cache_sha256",
        "compile_commands_sha256",
        "observer_compiled",
        "registered_tests",
        "source_dir",
    }
    paths: list[str] = []
    for build_key, expected in EXPECTED_BUILDS.items():
        timing_only = {
            "invalid_observer_environment_probe",
            "observer_symbol_string_probe",
        }
        record = _exact_object(
            builds[build_key],
            common_build_keys | (timing_only if expected["build_flavor"] == "timing" else set()),
            f"build manifest.builds.{build_key}",
        )
        for key in (
            "algorithm_revision",
            "applied_head",
            "binary_path",
            "binary_sha256",
            "observer_compiled",
            "source_dir",
        ):
            _exact_value(record[key], expected[key], f"builds.{build_key}.{key}")
        _canonical_relative(record["binary_path"], f"builds.{build_key}.binary_path")
        _canonical_relative(record["source_dir"], f"builds.{build_key}.source_dir")
        _hash(record["algorithm_revision"], f"builds.{build_key}.algorithm_revision", length=40)
        _hash(record["applied_head"], f"builds.{build_key}.applied_head", length=40)
        for key in (
            "binary_sha256",
            "clean_build_log_sha256",
            "cmake_cache_sha256",
            "compile_commands_sha256",
        ):
            _hash(record[key], f"builds.{build_key}.{key}")
        _exact_value(record["registered_tests"], "3/3 passed", f"builds.{build_key}.registered_tests")
        if expected["build_flavor"] == "timing":
            _exact_value(record["observer_symbol_string_probe"], "passed-no-match", f"builds.{build_key}.observer probe")
            _exact_value(record["invalid_observer_environment_probe"], "exit-0-no-artifacts", f"builds.{build_key}.environment probe")
        paths.append(record["binary_path"])
    if len(paths) != len(set(paths)):
        raise BinaryAttestationError("all four binary paths must be distinct")
    if builds["reference_fidelity"]["source_dir"] != builds["reference_timing"]["source_dir"]:
        raise BinaryAttestationError("reference source directories differ")
    if builds["optimized_fidelity"]["source_dir"] != builds["optimized_timing"]["source_dir"]:
        raise BinaryAttestationError("optimized source directories differ")
    if builds["reference_fidelity"]["source_dir"] == builds["optimized_fidelity"]["source_dir"]:
        raise BinaryAttestationError("method source directories are not distinct")

    trace = _exact_object(
        top["trace_contract"],
        {
            "digest_algorithm",
            "digest_state_schema",
            "gating_trace_detail",
            "historical_v2_status",
            "schema_version",
        },
        "build manifest.trace_contract",
    )
    expected_trace = {
        "digest_state_schema": "robot-semantic-digest-v2",
        "gating_trace_detail": "digest",
        "schema_version": "dancers-comparison-trace-v3",
    }
    for key, expected in expected_trace.items():
        _exact_value(trace[key], expected, f"trace_contract.{key}")
    if type(trace["digest_algorithm"]) is not str or type(trace["historical_v2_status"]) is not str:
        raise BinaryAttestationError("trace contract string field type differs")

    gates = _exact_object(
        top["gates"],
        {
            "calibration_permitted",
            "common_registered_tests_all_four_builds",
            "dual_clean_builds",
            "smoke_semantic_trace_identity",
            "timing_observer_compile_absence",
            "validation_permitted",
            "validation_reason",
        },
        "build manifest.gates",
    )
    for key in (
        "common_registered_tests_all_four_builds",
        "dual_clean_builds",
        "smoke_semantic_trace_identity",
        "timing_observer_compile_absence",
    ):
        _exact_value(gates[key], "pass", f"gates.{key}")
    _exact_value(gates["calibration_permitted"], True, "gates.calibration_permitted")
    _exact_value(gates["validation_permitted"], False, "gates.validation_permitted")
    if type(gates["validation_reason"]) is not str or not gates["validation_reason"]:
        raise BinaryAttestationError("gates.validation_reason type differs")
    _validate_smoke(top["historical_fidelity_smoke_v2"], historical=True)
    _validate_smoke(top["fidelity_smoke"], historical=False)
    return top


def _verify_macho_observer(raw: bytes, observer_compiled: bool, label: str) -> dict[str, int]:
    """Verify one arm64 Mach-O header and exact observer-marker multiplicities."""

    if type(raw) is not bytes or len(raw) < 32:
        raise BinaryAttestationError(f"{label} is too small to be a Mach-O executable")
    if raw[:4] != MACHO_64_ARM64_MAGIC:
        raise BinaryAttestationError(f"{label} is not a little-endian 64-bit Mach-O")
    if int.from_bytes(raw[4:8], "little") != MACHO_CPU_TYPE_ARM64:
        raise BinaryAttestationError(f"{label} is not an arm64 Mach-O")
    if int.from_bytes(raw[12:16], "little") != MACHO_FILE_TYPE_EXECUTE:
        raise BinaryAttestationError(f"{label} is not a Mach-O executable")
    expected_count = 1 if observer_compiled else 0
    counts = {
        marker[:-1].decode("ascii"): raw.count(marker)
        for marker in OBSERVER_MARKERS
    }
    if any(count != expected_count for count in counts.values()):
        state = "present exactly once" if observer_compiled else "absent"
        raise BinaryAttestationError(f"{label} observer markers are not {state}")
    return counts


def _file_identity(relative: str, item: StableFile) -> dict[str, Any]:
    return {"bytes": item.bytes, "path": relative, "sha256": item.sha256}


def _validate_observed_utc(value: Any) -> str:
    if type(value) is not str or UTC_SECONDS.fullmatch(value) is None:
        raise BinaryAttestationError(
            "observed_utc must be an explicit UTC timestamp at whole-second precision"
        )
    # Reject impossible month/day combinations without consulting a clock.
    try:
        import datetime as _datetime

        _datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise BinaryAttestationError("observed_utc is not a real UTC date/time") from exc
    return value


def build_attestation(*, repository_root: Path, observed_utc: str) -> dict[str, Any]:
    """Authenticate the fixed manifest, Pixi lock, and four live binaries.

    No clock is read.  ``observed_utc`` is an explicit descriptive input; with
    identical live bytes and the same supplied value, the result is identical.
    """

    root = _absolute_root(repository_root)
    observed = _validate_observed_utc(observed_utc)
    manifest_file = _read_stable_regular(
        _bound_path(root, BUILD_MANIFEST_PATH, "build manifest path"),
        maximum_bytes=MAX_MANIFEST_BYTES,
        label="build manifest",
        executable=False,
    )
    if manifest_file.sha256 != EXPECTED_BUILD_MANIFEST_SHA256:
        raise BinaryAttestationError("build manifest byte identity differs")
    manifest = _validate_manifest(_strict_json(manifest_file.raw, "build manifest"))

    lock_file = _read_stable_regular(
        _bound_path(root, PIXI_LOCK_PATH, "Pixi lock path"),
        maximum_bytes=MAX_LOCK_BYTES,
        label="Pixi lock",
        executable=False,
    )
    if (
        lock_file.sha256 != EXPECTED_PIXI_LOCK_SHA256
        or manifest["toolchain"]["pixi_lock_sha256"] != lock_file.sha256
    ):
        raise BinaryAttestationError("Pixi lock byte identity differs")

    binary_records: dict[str, Any] = {}
    for build_key, expected in EXPECTED_BUILDS.items():
        binary = _read_stable_regular(
            _bound_path(root, expected["binary_path"], f"{build_key} binary path"),
            maximum_bytes=MAX_BINARY_BYTES,
            label=f"{build_key} binary",
            executable=True,
        )
        if binary.bytes != expected["binary_bytes"]:
            raise BinaryAttestationError(f"{build_key} binary size differs")
        if binary.sha256 != expected["binary_sha256"]:
            raise BinaryAttestationError(f"{build_key} binary SHA-256 differs")
        marker_counts = _verify_macho_observer(
            binary.raw, expected["observer_compiled"], f"{build_key} binary"
        )
        binary_records[build_key] = {
            "algorithm_revision": expected["algorithm_revision"],
            "applied_head": expected["applied_head"],
            "build_flavor": expected["build_flavor"],
            "build_manifest_key": build_key,
            "bytes": binary.bytes,
            "mach_o": {
                "architecture": "arm64",
                "file_type": "executable",
                "format": "mach-o-64-little-endian",
            },
            "method": expected["method"],
            "observer_compiled": expected["observer_compiled"],
            "observer_marker_counts": marker_counts,
            "path": expected["binary_path"],
            "run_classes": list(expected["run_classes"]),
            "sha256": binary.sha256,
            "source_dir": expected["source_dir"],
        }

    attestation = {
        "binaries": binary_records,
        "build_manifest": {
            **_file_identity(BUILD_MANIFEST_PATH, manifest_file),
            "schema_version": manifest["schema_version"],
            "scope": manifest["scope"],
            "status": manifest["status"],
        },
        "classification": dict(CLAIM_FLAGS),
        "evidence_boundary": list(EVIDENCE_BOUNDARY),
        "observed_utc": observed,
        "operations": dict(OPERATIONS),
        "pixi_lock": _file_identity(PIXI_LOCK_PATH, lock_file),
        "run_class_constraints": {
            key: dict(item) for key, item in RUN_CLASS_CONSTRAINTS.items()
        },
        "schema_version": SCHEMA_VERSION,
        "source_identity": {
            "common_patch": {
                "commit": EXPECTED_REPOSITORY["common_patch_commit"],
                "format_email_sha256": EXPECTED_REPOSITORY[
                    "common_patch_format_email_sha256"
                ],
                "full_index_diff_sha256": EXPECTED_REPOSITORY[
                    "common_patch_full_index_diff_sha256"
                ],
                "history": [dict(item) for item in EXPECTED_COMMON_PATCH_HISTORY],
            },
            "optimized": {
                "applied_head": EXPECTED_REPOSITORY["optimized_applied_head"],
                "source_pin": EXPECTED_REPOSITORY["optimized_pin"],
                "tree": EXPECTED_REPOSITORY["optimized_tree"],
            },
            "reference": {
                "applied_head": EXPECTED_REPOSITORY["reference_applied_head"],
                "source_pin": EXPECTED_REPOSITORY["reference_pin"],
                "tree": EXPECTED_REPOSITORY["reference_tree"],
            },
        },
        "status": "pass-identity-only-nonauthorizing",
        "track_id": TRACK_ID,
    }
    _validate_attestation(attestation)
    return attestation


def _validate_attestation(value: Any) -> dict[str, Any]:
    record = _exact_object(
        value,
        {
            "binaries",
            "build_manifest",
            "classification",
            "evidence_boundary",
            "observed_utc",
            "operations",
            "pixi_lock",
            "run_class_constraints",
            "schema_version",
            "source_identity",
            "status",
            "track_id",
        },
        "binary attestation",
    )
    _exact_value(record["schema_version"], SCHEMA_VERSION, "attestation schema")
    _exact_value(record["track_id"], TRACK_ID, "attestation track")
    _exact_value(record["status"], "pass-identity-only-nonauthorizing", "attestation status")
    _validate_observed_utc(record["observed_utc"])
    if record["classification"] != CLAIM_FLAGS or any(
        type(item) is not bool or item for item in record["classification"].values()
    ):
        raise BinaryAttestationError("attestation claim flags must all be false")
    if record["operations"] != OPERATIONS or any(
        type(item) is not bool or item for item in record["operations"].values()
    ):
        raise BinaryAttestationError("attestation operation flags must all be false")
    if not _json_exact(record["evidence_boundary"], list(EVIDENCE_BOUNDARY)):
        raise BinaryAttestationError("attestation evidence boundary schema differs")
    if not _json_exact(record["run_class_constraints"], RUN_CLASS_CONSTRAINTS):
        raise BinaryAttestationError("attestation run-class constraints differ")

    manifest = _exact_object(
        record["build_manifest"],
        {"bytes", "path", "schema_version", "scope", "sha256", "status"},
        "attestation build manifest",
    )
    manifest_expected = {
        "bytes": EXPECTED_BUILD_MANIFEST_BYTES,
        "path": BUILD_MANIFEST_PATH,
        "schema_version": "dancers-comparison-build-manifest-v1",
        "scope": "local-only source-pinned Release trace-v3 builds; no calibration or validation result",
        "sha256": EXPECTED_BUILD_MANIFEST_SHA256,
        "status": "dual-build-parity-passed-smoke-fidelity-only",
    }
    if not _json_exact(manifest, manifest_expected):
        raise BinaryAttestationError("attestation build-manifest identity differs")
    lock = _exact_object(
        record["pixi_lock"], {"bytes", "path", "sha256"}, "attestation Pixi lock"
    )
    if not _json_exact(
        lock,
        {
            "bytes": EXPECTED_PIXI_LOCK_BYTES,
            "path": PIXI_LOCK_PATH,
            "sha256": EXPECTED_PIXI_LOCK_SHA256,
        },
    ):
        raise BinaryAttestationError("attestation Pixi-lock identity differs")

    binaries = _exact_object(
        record["binaries"], set(EXPECTED_BUILDS), "attestation binaries"
    )
    binary_keys = {
        "algorithm_revision",
        "applied_head",
        "build_flavor",
        "build_manifest_key",
        "bytes",
        "mach_o",
        "method",
        "observer_compiled",
        "observer_marker_counts",
        "path",
        "run_classes",
        "sha256",
        "source_dir",
    }
    marker_names = {marker[:-1].decode("ascii") for marker in OBSERVER_MARKERS}
    for build_key, expected in EXPECTED_BUILDS.items():
        binary = _exact_object(
            binaries[build_key], binary_keys, f"attestation binary {build_key}"
        )
        expected_scalars = {
            "algorithm_revision": expected["algorithm_revision"],
            "applied_head": expected["applied_head"],
            "build_flavor": expected["build_flavor"],
            "build_manifest_key": build_key,
            "bytes": expected["binary_bytes"],
            "method": expected["method"],
            "observer_compiled": expected["observer_compiled"],
            "path": expected["binary_path"],
            "run_classes": list(expected["run_classes"]),
            "sha256": expected["binary_sha256"],
            "source_dir": expected["source_dir"],
        }
        for key, expected_value in expected_scalars.items():
            _exact_value(binary[key], expected_value, f"attestation {build_key}.{key}")
        if not _json_exact(
            binary["mach_o"],
            {
                "architecture": "arm64",
                "file_type": "executable",
                "format": "mach-o-64-little-endian",
            },
        ):
            raise BinaryAttestationError(f"attestation {build_key}.mach_o differs")
        markers = _exact_object(
            binary["observer_marker_counts"],
            marker_names,
            f"attestation {build_key}.observer_marker_counts",
        )
        expected_count = 1 if expected["observer_compiled"] else 0
        for marker_name in marker_names:
            _exact_value(
                markers[marker_name],
                expected_count,
                f"attestation {build_key}.observer_marker_counts.{marker_name}",
            )

    expected_source_identity = {
        "common_patch": {
            "commit": EXPECTED_REPOSITORY["common_patch_commit"],
            "format_email_sha256": EXPECTED_REPOSITORY[
                "common_patch_format_email_sha256"
            ],
            "full_index_diff_sha256": EXPECTED_REPOSITORY[
                "common_patch_full_index_diff_sha256"
            ],
            "history": [dict(item) for item in EXPECTED_COMMON_PATCH_HISTORY],
        },
        "optimized": {
            "applied_head": EXPECTED_REPOSITORY["optimized_applied_head"],
            "source_pin": EXPECTED_REPOSITORY["optimized_pin"],
            "tree": EXPECTED_REPOSITORY["optimized_tree"],
        },
        "reference": {
            "applied_head": EXPECTED_REPOSITORY["reference_applied_head"],
            "source_pin": EXPECTED_REPOSITORY["reference_pin"],
            "tree": EXPECTED_REPOSITORY["reference_tree"],
        },
    }
    if not _json_exact(record["source_identity"], expected_source_identity):
        raise BinaryAttestationError("attestation source identity differs")
    canonical_json_bytes(record)
    return record


def _output_path(value: Path, repository_root: Path) -> Path:
    output = Path(os.path.abspath(os.fspath(value)))
    if len(os.fspath(output).encode("utf-8", "strict")) > 4096:
        raise BinaryAttestationError("output path exceeds the path bound")
    parent = output.parent
    if os.path.realpath(parent) != os.fspath(parent):
        raise BinaryAttestationError("output parent has a symlink component")
    if output.name in ("", ".", ".."):
        raise BinaryAttestationError("output path has no regular leaf name")
    for protected in (ROOT / "results", ROOT / "comparison/plans"):
        current = parent
        while True:
            try:
                if os.path.samefile(current, protected):
                    raise BinaryAttestationError(
                        "binary attestation must never write a plan or result namespace"
                    )
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise BinaryAttestationError(
                    "cannot authenticate output-parent ancestry"
                ) from exc
            following = current.parent
            if following == current:
                break
            current = following
    try:
        relative = output.relative_to(repository_root)
    except ValueError:
        return output
    parts = relative.parts
    if parts[:1] == ("results",) or parts[:2] == ("comparison", "plans"):
        raise BinaryAttestationError(
            "binary attestation must never write a plan or result namespace"
        )
    return output


def _close_descriptor_exact(descriptor: int, label: str) -> None:
    try:
        os.close(descriptor)
        return
    except OSError as close_error:
        try:
            os.fstat(descriptor)
        except OSError as probe_error:
            if probe_error.errno == errno.EBADF:
                return
        raise BinaryAttestationError(f"cannot close {label}") from close_error


def publish_attestation(
    attestation: Mapping[str, Any], *, output_path: Path, repository_root: Path
) -> PublishedAttestation:
    """Reject every binary-attestation publication at this checkpoint."""

    raise BinaryAttestationError(
        "binary-attestation publication is categorically disabled at this nonauthorizing checkpoint"
    )

    root = _absolute_root(repository_root)
    validated = _validate_attestation(attestation)
    raw = canonical_json_bytes(validated)
    output = _output_path(output_path, root)
    parent_flags = os.O_RDONLY | _required_flag("O_DIRECTORY") | _required_flag("O_NOFOLLOW") | _required_flag("O_CLOEXEC")
    parent_descriptor: int | None = None
    try:
        parent_descriptor = os.open(output.parent, parent_flags)
    except OSError as exc:
        raise BinaryAttestationError("cannot open output parent safely") from exc
    created_descriptor: int | None = None
    created_identity: tuple[int, int] | None = None
    published = False
    try:
        assert parent_descriptor is not None
        parent_before = os.fstat(parent_descriptor)
        if not stat.S_ISDIR(parent_before.st_mode):
            raise BinaryAttestationError("output parent is not a directory")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _required_flag("O_CLOEXEC") | _required_flag("O_NOFOLLOW")
        try:
            created_descriptor = os.open(
                output.name, flags, 0o600, dir_fd=parent_descriptor
            )
        except FileExistsError as exc:
            raise BinaryAttestationError(
                "output already exists; publication is create-exclusive"
            ) from exc
        except OSError as exc:
            raise BinaryAttestationError("cannot create output safely") from exc
        created_stat = os.fstat(created_descriptor)
        created_identity = (created_stat.st_dev, created_stat.st_ino)
        if (
            not stat.S_ISREG(created_stat.st_mode)
            or created_stat.st_nlink != 1
            or created_stat.st_uid != os.geteuid()
            or stat.S_IMODE(created_stat.st_mode) != 0o600
        ):
            raise BinaryAttestationError("created output identity or mode is unsafe")
        offset = 0
        while offset < len(raw):
            try:
                written = os.write(created_descriptor, raw[offset:])
            except OSError as exc:
                raise BinaryAttestationError("cannot write published output") from exc
            if written <= 0:
                raise BinaryAttestationError("output write made no progress")
            offset += written
        try:
            os.fsync(created_descriptor)
        except OSError as exc:
            raise BinaryAttestationError("cannot synchronize published output") from exc
        final_descriptor_stat = os.fstat(created_descriptor)
        if final_descriptor_stat.st_size != len(raw):
            raise BinaryAttestationError("published output size differs")
        _close_descriptor_exact(created_descriptor, "published output")
        created_descriptor = None
        named = os.stat(output.name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (
            (named.st_dev, named.st_ino) != created_identity
            or _identity(named) != _identity(final_descriptor_stat)
        ):
            raise BinaryAttestationError("published output name identity differs")
        try:
            os.fsync(parent_descriptor)
        except OSError as exc:
            raise BinaryAttestationError("cannot synchronize output parent") from exc
        parent_after = os.fstat(parent_descriptor)
        parent_named = os.stat(output.parent, follow_symlinks=False)
        parent_identity = lambda item: (
            item.st_dev,
            item.st_ino,
            item.st_mode,
            item.st_uid,
            item.st_gid,
        )
        if (
            parent_identity(parent_before) != parent_identity(parent_after)
            or parent_identity(parent_after) != parent_identity(parent_named)
        ):
            raise BinaryAttestationError("output parent identity changed")
        verification = _read_stable_regular(
            output,
            maximum_bytes=MAX_OUTPUT_BYTES,
            label="published binary attestation",
            executable=False,
        )
        if verification.raw != raw or verification.mode != 0o600:
            raise BinaryAttestationError("published output verification differs")
        _close_descriptor_exact(parent_descriptor, "output parent")
        parent_descriptor = None
        published = True
    except BaseException as primary:
        cleanup_errors: list[str] = []
        if created_descriptor is not None:
            try:
                _close_descriptor_exact(created_descriptor, "failed published output")
            except BaseException as cleanup_error:
                cleanup_errors.append(str(cleanup_error))
        if created_identity is not None and parent_descriptor is not None:
            try:
                named = os.stat(output.name, dir_fd=parent_descriptor, follow_symlinks=False)
                if (named.st_dev, named.st_ino) == created_identity:
                    os.unlink(output.name, dir_fd=parent_descriptor)
                    os.fsync(parent_descriptor)
                else:
                    cleanup_errors.append(
                        "published output name no longer identifies the created file"
                    )
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                cleanup_errors.append(f"cannot roll back published output: {cleanup_error}")
            try:
                os.stat(output.name, dir_fd=parent_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                cleanup_errors.append(
                    f"cannot prove published-output rollback: {cleanup_error}"
                )
            else:
                cleanup_errors.append("published output remains after rollback")
        elif created_identity is not None:
            cleanup_errors.append(
                "output parent closed before published-output rollback could be proved"
            )
        if parent_descriptor is not None:
            try:
                _close_descriptor_exact(parent_descriptor, "failed output parent")
            except BaseException as cleanup_error:
                cleanup_errors.append(str(cleanup_error))
        if cleanup_errors:
            details = tuple(cleanup_errors)
            try:
                setattr(primary, "publication_cleanup_errors", details)
            except BaseException:
                pass
            if isinstance(primary, Exception):
                raise BinaryAttestationError(
                    "binary-attestation publication failed and rollback was not fully proved: "
                    + "; ".join(details)
                ) from primary
        raise
    if not published:
        raise BinaryAttestationError("output was not published")
    return PublishedAttestation(output, len(raw), hashlib.sha256(raw).hexdigest())


def attest_and_publish(
    *, repository_root: Path, observed_utc: str, output_path: Path
) -> PublishedAttestation:
    raise BinaryAttestationError(
        "binary-attestation publication is categorically disabled at this nonauthorizing checkpoint"
    )

    attestation = build_attestation(
        repository_root=repository_root, observed_utc=observed_utc
    )
    return publish_attestation(
        attestation, output_path=output_path, repository_root=repository_root
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Attest, but do not execute or authorize, the fixed Track-B binaries."
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Explicit create-exclusive output path outside plan/result namespaces.",
    )
    parser.add_argument(
        "--observed-utc",
        required=True,
        help="Caller-supplied whole-second UTC observation timestamp.",
    )
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=ROOT,
        help="Repository root (defaults to this source tree).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    _ = arguments
    print(
        "binary attester remains nonauthorizing; no repository read or output is permitted",
        file=sys.stderr,
    )
    return 2

    try:
        receipt = attest_and_publish(
            repository_root=arguments.repository_root,
            observed_utc=arguments.observed_utc,
            output_path=arguments.output,
        )
    except BinaryAttestationError as exc:
        print(f"binary attestation failed: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(canonical_json_bytes(receipt.as_json()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
