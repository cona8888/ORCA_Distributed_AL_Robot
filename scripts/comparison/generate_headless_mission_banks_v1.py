#!/usr/bin/env python3
"""Compile deterministic calibration-only headless mission banks.

This module is deliberately a compiler, not a runner.  It authenticates the
frozen 256 px calibration maps, constructs one maximum-count mission bank for
each caller-supplied ``(family, seed)`` request, and materializes every
requested count as an exact nested prefix.  Compilation is entirely in memory.
Publication is categorically disabled at this code-only checkpoint; a later
registration checkpoint must supply and qualify a new publisher.

The compiler never launches a subprocess or simulator, has no network code,
does not inspect result data, and rejects validation and audit designs before
reading any environment artifact.
"""

from __future__ import annotations

import argparse
import binascii
import errno
import hashlib
import json
import math
import os
import stat
import struct
import sys
import zlib
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence


sys.dont_write_bytecode = True

DESIGN_SCHEMA = "dancers-headless-mission-design-v1"
BANK_SCHEMA = "dancers-headless-mission-bank-v1"
MANIFEST_SCHEMA = "dancers-headless-mission-manifest-v1"
SCENARIO_KIND = "dancers-headless-calibration-prefix-v1"

PHASE = "calibration"
REJECTED_PHASES = frozenset(("validation", "audit"))
FAMILIES = (
    "open",
    "perfect_maze",
    "fractal_low",
    "fractal_medium",
    "fractal_high",
)

ENVIRONMENT_ROOT = PurePosixPath("comparison/environments")
ENVIRONMENT_MANIFEST_PATH = ENVIRONMENT_ROOT / "manifest.json"
ENVIRONMENT_MANIFEST_SIDECAR_PATH = ENVIRONMENT_ROOT / "manifest.sha256"
ENVIRONMENT_MANIFEST_SHA256 = (
    "2785458ea32028f8a59498650677d1aa4c9dfb749b5f1a80754577e1eb703589"
)

MAP_SIZE_PX = 256
WORLD_SIZE_M = 100.0
PIXEL_SIZE_M = WORLD_SIZE_M / MAP_SIZE_PX
CLEARANCE_RADIUS_PX = 4
ROBOT_RADIUS_M = 1.0
MINIMUM_ENDPOINT_OBSTACLE_CLEARANCE_M = 1.25
MINIMUM_ENDPOINT_PAIR_CENTER_DISTANCE_M = 2.25
MINIMUM_GOAL_REACH_TRAVEL_DISTANCE_M = 8.0
MAXIMUM_GOAL_REACH_TRAVEL_DISTANCE_M = 12.0
MINIMUM_ROUTE_LENGTH_M = (
    MINIMUM_GOAL_REACH_TRAVEL_DISTANCE_M + ROBOT_RADIUS_M
)
MAXIMUM_ROUTE_LENGTH_M = (
    MAXIMUM_GOAL_REACH_TRAVEL_DISTANCE_M + ROBOT_RADIUS_M
)
STATIC_ROUTE_DISTANCE_DIAGNOSTIC_M = 6.0
FIXED_WORK_TIMESTEPS = 100
CONTROLLER_TIMESTEP_SECONDS = 0.1
MAXIMUM_LINEAR_SPEED_M_PER_SECOND = 2.0
HORIZON_DISTANCE_M = (
    FIXED_WORK_TIMESTEPS
    * CONTROLLER_TIMESTEP_SECONDS
    * MAXIMUM_LINEAR_SPEED_M_PER_SECOND
)
MAXIMUM_ROBOT_COUNT = 256
MAXIMUM_ATTEMPTS_PER_ROBOT = 50_000
SEED_DOMAIN = b"dancers-headless-mission-bank-v1"

SEED_ROLES = {
    "mission_selection": {
        "active_via": "distinct-effective-scenario-bytes",
        "field": "mission_seed",
    },
    "runtime_rng": {
        "activity_assumed": False,
        "config_key": "RNG_SEED",
        "field": "runtime_rng_seed",
    },
}
SAMPLING_STRUCTURE = {
    "clustering_unit": "family-by-mission-bank",
    "clustering_unit_fields": ["family", "mission_bank_id"],
    "count_relationship_within_bank": "nested-prefix",
    "independent_scene_assumption": False,
}

_SHA256_HEX = frozenset("0123456789abcdef")
_MIN_ROUTE_LENGTH_PX_SQUARED = math.ceil(
    (MINIMUM_ROUTE_LENGTH_M / PIXEL_SIZE_M) ** 2
)
_MAX_ROUTE_LENGTH_PX_SQUARED = math.floor(
    (MAXIMUM_ROUTE_LENGTH_M / PIXEL_SIZE_M) ** 2
)
_PIXEL_SIZE_M_EXACT = Fraction(100, MAP_SIZE_PX)
_MIN_ENDPOINT_DISTANCE_PX_SQUARED = (
    Fraction(9, 4) / _PIXEL_SIZE_M_EXACT
) ** 2
_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | os.O_CLOEXEC
    | os.O_DIRECTORY
    | os.O_NOFOLLOW
)
_REGULAR_OPEN_FLAGS = (
    os.O_RDONLY
    | os.O_CLOEXEC
    | os.O_NOFOLLOW
    | os.O_NONBLOCK
)
_EXCLUSIVE_WRITE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | os.O_CLOEXEC
    | os.O_NOFOLLOW
)
_READ_CHUNK_BYTES = 1024 * 1024
_MAXIMUM_AUTHENTICATED_INPUT_BYTES = 64 * 1024 * 1024
_LIVE_REPOSITORY_ROOT = Path(os.path.abspath(__file__)).parents[2]
_PROTECTED_OUTPUT_ROOTS = (
    PurePosixPath("comparison/plans"),
    PurePosixPath("results"),
)


class MissionCompileError(ValueError):
    """A design, authenticated input, or compiled artifact is invalid."""


class _DuplicateKeyError(MissionCompileError):
    pass


@dataclass(frozen=True)
class AuthenticatedMap:
    family: str
    relative_path: PurePosixPath
    source_bytes: bytes
    source_sha256: str
    pixels: bytes


@dataclass(frozen=True)
class CompiledFile:
    relative_path: PurePosixPath
    data: bytes
    sha256: str
    kind: str


@dataclass(frozen=True)
class MissionCompilation:
    design: dict[str, Any]
    design_bytes: bytes
    design_sha256: str
    environment_manifest_bytes: bytes
    environment_manifest_sha256: str
    maps: tuple[AuthenticatedMap, ...]
    files: tuple[CompiledFile, ...]
    manifest: dict[str, Any]
    manifest_bytes: bytes
    manifest_sha256: str

    def by_path(self) -> dict[PurePosixPath, CompiledFile]:
        return {artifact.relative_path: artifact for artifact in self.files}


class _HashStream:
    """Domain-separated SHA-256 counter stream with rejection sampling."""

    def __init__(self, seed_uint64: int) -> None:
        if not _is_int(seed_uint64) or not 0 <= seed_uint64 < 1 << 64:
            raise MissionCompileError("generator seed must be uint64")
        self._key = SEED_DOMAIN + seed_uint64.to_bytes(8, "big")
        self._counter = 0

    def uint64(self) -> int:
        material = self._key + self._counter.to_bytes(16, "big")
        self._counter += 1
        return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")

    def randbelow(self, upper: int) -> int:
        if not _is_int(upper) or upper <= 0:
            raise MissionCompileError("sampling upper bound must be positive")
        limit = (1 << 64) - ((1 << 64) % upper)
        while True:
            value = self.uint64()
            if value < limit:
                return value % upper


def _is_int(value: object) -> bool:
    return type(value) is int


def sha256_bytes(data: bytes) -> str:
    if type(data) is not bytes:
        raise MissionCompileError("SHA-256 input must be bytes")
    return hashlib.sha256(data).hexdigest()


def _seed_roles() -> dict[str, Any]:
    return {
        "mission_selection": dict(SEED_ROLES["mission_selection"]),
        "runtime_rng": dict(SEED_ROLES["runtime_rng"]),
    }


def _sampling_structure() -> dict[str, Any]:
    return {
        "clustering_unit": SAMPLING_STRUCTURE["clustering_unit"],
        "clustering_unit_fields": list(
            SAMPLING_STRUCTURE["clustering_unit_fields"]
        ),
        "count_relationship_within_bank": SAMPLING_STRUCTURE[
            "count_relationship_within_bank"
        ],
        "independent_scene_assumption": SAMPLING_STRUCTURE[
            "independent_scene_assumption"
        ],
    }


def canonical_json_bytes(value: object) -> bytes:
    """Return the single accepted JSON representation for compiled files."""

    try:
        text = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        return (text + "\n").encode("utf-8")
    except (RecursionError, TypeError, UnicodeError, ValueError) as error:
        raise MissionCompileError(f"value is not canonical JSON: {error}") from error


def _object_without_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json_bytes(data: bytes, label: str) -> Any:
    if type(data) is not bytes:
        raise MissionCompileError(f"{label} must be bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise MissionCompileError(f"{label} is not UTF-8") from error

    def reject_constant(token: str) -> None:
        raise MissionCompileError(f"{label} contains non-finite {token}")

    try:
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=reject_constant,
        )
    except (
        json.JSONDecodeError,
        _DuplicateKeyError,
        RecursionError,
        ValueError,
    ) as error:
        raise MissionCompileError(f"{label} is not strict JSON: {error}") from error


def _safe_relative(value: object, label: str) -> PurePosixPath:
    if type(value) is not str:
        raise MissionCompileError(f"{label} must be a string")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or not path.parts
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise MissionCompileError(f"{label} is not a safe canonical relative path")
    return path


def _require_exact_keys(value: object, keys: set[str], label: str) -> Mapping[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise MissionCompileError(f"{label} keys differ")
    return value


def _json_exact_equal(actual: object, expected: object) -> bool:
    """Compare JSON values without bool/int or int/float coercion."""

    if type(actual) is not type(expected):
        return False
    if type(actual) is dict:
        return set(actual) == set(expected) and all(
            _json_exact_equal(actual[key], expected[key]) for key in expected
        )
    if type(actual) is list:
        return len(actual) == len(expected) and all(
            _json_exact_equal(left, right)
            for left, right in zip(actual, expected)
        )
    return actual == expected


def _descriptor_identity(metadata: os.stat_result) -> tuple[int, ...]:
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


def _record_secondary_failure(
    primary: BaseException,
    secondary: BaseException,
    detail: str,
    *,
    annotate_message: bool = False,
) -> None:
    try:
        failures = list(getattr(primary, "mission_secondary_failures", ()))
        failures.append((detail, secondary))
        setattr(primary, "mission_secondary_failures", tuple(failures))
        if annotate_message:
            original = str(primary)
            primary.args = (
                f"{original} [{detail}: {type(secondary).__name__}: {secondary}]",
            )
        add_note = getattr(primary, "add_note", None)
        if add_note is not None:
            add_note(f"{detail}: {type(secondary).__name__}: {secondary}")
    except BaseException:
        pass


def _raise_primary_with_secondary(
    primary: BaseException,
    secondary: BaseException,
    detail: str,
    *,
    annotate_message: bool = False,
) -> None:
    """Keep the first exception primary while making cleanup failure observable."""

    _record_secondary_failure(
        primary,
        secondary,
        detail,
        annotate_message=annotate_message,
    )
    raise primary.with_traceback(primary.__traceback__) from secondary


def _close_fd(fd: int, label: str) -> None:
    try:
        os.close(fd)
    except BaseException as error:
        raise MissionCompileError(f"{label} descriptor close failed") from error


def _close_fd_preserving(fd: int, label: str, primary: BaseException) -> None:
    try:
        os.close(fd)
    except BaseException as secondary:
        _raise_primary_with_secondary(
            primary,
            secondary,
            f"{label} descriptor close also failed",
        )


def _lexical_path_parts(path: Path, label: str) -> tuple[str, tuple[str, ...]]:
    try:
        raw = os.fspath(path)
    except TypeError as error:
        raise MissionCompileError(f"{label} must be a filesystem path") from error
    if type(raw) is not str or not raw or "\0" in raw:
        raise MissionCompileError(f"{label} must be a non-empty text path")
    lexical = Path(raw)
    anchor = lexical.anchor
    if anchor not in ("", os.sep):
        raise MissionCompileError(f"{label} uses an unsupported path anchor")
    parts = lexical.parts[1:] if anchor else lexical.parts
    if any(part in ("", ".", "..") for part in parts):
        raise MissionCompileError(f"{label} is not a safe lexical path")
    return (os.sep if anchor else "."), tuple(parts)


def _open_directory_components(
    anchor: str, parts: Sequence[str], label: str
) -> int:
    try:
        current = os.open(anchor, _DIRECTORY_OPEN_FLAGS)
    except OSError as error:
        raise MissionCompileError(f"{label} anchor cannot be opened") from error
    try:
        if not stat.S_ISDIR(os.fstat(current).st_mode):
            raise MissionCompileError(f"{label} anchor is not a directory")
        for part in parts:
            try:
                following = os.open(
                    part,
                    _DIRECTORY_OPEN_FLAGS,
                    dir_fd=current,
                )
            except OSError as error:
                raise MissionCompileError(
                    f"{label} contains a missing, non-directory, or symlink component"
                ) from error
            try:
                if not stat.S_ISDIR(os.fstat(following).st_mode):
                    raise MissionCompileError(
                        f"{label} contains a non-directory component"
                    )
                _close_fd(current, f"{label} ancestor")
            except BaseException as primary:
                _close_fd_preserving(following, f"{label} child", primary)
                raise
            current = following
        return current
    except BaseException as primary:
        _close_fd_preserving(current, label, primary)
        raise


def _open_directory_path(path: Path, label: str) -> int:
    anchor, parts = _lexical_path_parts(path, label)
    return _open_directory_components(anchor, parts, label)


def _open_parent_directory(path: Path, label: str) -> tuple[int, str, Path]:
    anchor, parts = _lexical_path_parts(path, label)
    if not parts:
        raise MissionCompileError(f"{label} must name a child of a directory")
    parent_fd = _open_directory_components(anchor, parts[:-1], f"{label} parent")
    if anchor == os.sep:
        parent_path = Path(os.sep).joinpath(*parts[:-1])
    else:
        parent_path = Path(".").joinpath(*parts[:-1])
    return parent_fd, parts[-1], parent_path


def _open_relative_directory(
    root_fd: int, parts: Sequence[str], label: str
) -> int:
    try:
        current = os.dup(root_fd)
    except OSError as error:
        raise MissionCompileError(f"{label} root descriptor cannot be duplicated") from error
    try:
        if not stat.S_ISDIR(os.fstat(current).st_mode):
            raise MissionCompileError(f"{label} root is not a directory")
        for part in parts:
            try:
                following = os.open(
                    part,
                    _DIRECTORY_OPEN_FLAGS,
                    dir_fd=current,
                )
            except OSError as error:
                raise MissionCompileError(
                    f"{label} contains a missing, non-directory, or symlink component"
                ) from error
            try:
                _close_fd(current, f"{label} ancestor")
            except BaseException as primary:
                _close_fd_preserving(following, f"{label} child", primary)
                raise
            current = following
        return current
    except BaseException as primary:
        _close_fd_preserving(current, label, primary)
        raise


def _read_open_regular_fd(fd: int, label: str) -> bytes:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode) or before.st_nlink <= 0:
        raise MissionCompileError(f"{label} is not a linked regular file")
    if not 0 <= before.st_size <= _MAXIMUM_AUTHENTICATED_INPUT_BYTES:
        raise MissionCompileError(f"{label} exceeds the authenticated input bound")
    remaining = before.st_size
    chunks: list[bytes] = []
    while remaining:
        chunk = os.read(fd, min(remaining, _READ_CHUNK_BYTES))
        if not chunk:
            raise MissionCompileError(f"{label} changed or truncated while being read")
        chunks.append(chunk)
        remaining -= len(chunk)
    if os.read(fd, 1) != b"":
        raise MissionCompileError(f"{label} grew while being read")
    after = os.fstat(fd)
    data = b"".join(chunks)
    if (
        _descriptor_identity(before) != _descriptor_identity(after)
        or len(data) != before.st_size
    ):
        raise MissionCompileError(f"{label} changed while being read")
    return data


def _read_regular_at(
    root_fd: int,
    relative: PurePosixPath,
    label: str,
) -> bytes:
    safe = _safe_relative(relative.as_posix(), label)
    parent_fd = _open_relative_directory(root_fd, safe.parts[:-1], f"{label} parent")
    try:
        try:
            file_fd = os.open(
                safe.parts[-1],
                _REGULAR_OPEN_FLAGS,
                dir_fd=parent_fd,
            )
        except OSError as error:
            raise MissionCompileError(
                f"{label} cannot be opened as a regular non-symlink file"
            ) from error
        try:
            data = _read_open_regular_fd(file_fd, label)
        except BaseException as primary:
            _close_fd_preserving(file_fd, label, primary)
            raise
        _close_fd(file_fd, label)
    except BaseException as primary:
        _close_fd_preserving(parent_fd, f"{label} parent", primary)
        raise
    _close_fd(parent_fd, f"{label} parent")
    return data


def _read_regular_path(path: Path, label: str) -> bytes:
    parent_fd, name, _parent_path = _open_parent_directory(path, label)
    try:
        data = _read_regular_at(parent_fd, PurePosixPath(name), label)
    except BaseException as primary:
        _close_fd_preserving(parent_fd, f"{label} parent", primary)
        raise
    _close_fd(parent_fd, f"{label} parent")
    return data


def _validate_sha256(value: object, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in _SHA256_HEX for character in value)
    ):
        raise MissionCompileError(f"{label} is not lowercase SHA-256")
    return value


def normalize_design(design: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and canonically order a caller-supplied calibration design."""

    design_map = _require_exact_keys(
        design,
        {"phase", "requests", "schema_version"},
        "mission design",
    )
    phase = design_map["phase"]
    if type(phase) is not str:
        raise MissionCompileError("mission design phase must be a string")
    if phase in REJECTED_PHASES:
        raise MissionCompileError(
            f"{phase} mission compilation is prohibited by this checkpoint"
        )
    if phase != PHASE:
        raise MissionCompileError("mission design phase must be calibration")
    if not _json_exact_equal(design_map["schema_version"], DESIGN_SCHEMA):
        raise MissionCompileError("mission design schema differs")
    requests = design_map["requests"]
    if type(requests) is not list or not requests:
        raise MissionCompileError("mission design requests must be a non-empty list")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for index, request in enumerate(requests):
        request_map = _require_exact_keys(
            request,
            {"counts", "family", "seed"},
            f"mission request {index}",
        )
        family = request_map["family"]
        seed = request_map["seed"]
        counts = request_map["counts"]
        if type(family) is not str or family not in FAMILIES:
            raise MissionCompileError(f"mission request {index} family differs")
        if not _is_int(seed) or not 1 <= seed <= 10:
            raise MissionCompileError(
                f"mission request {index} calibration seed must be in 1..10"
            )
        if type(counts) is not list or not counts:
            raise MissionCompileError(
                f"mission request {index} counts must be a non-empty list"
            )
        if any(
            not _is_int(count)
            or not 1 <= count <= MAXIMUM_ROBOT_COUNT
            for count in counts
        ):
            raise MissionCompileError(
                f"mission request {index} counts must be positive bounded integers"
            )
        if counts != sorted(set(counts)):
            raise MissionCompileError(
                f"mission request {index} counts must be strictly increasing"
            )
        identity = (family, seed)
        if identity in seen:
            raise MissionCompileError("mission design repeats a family/seed bank")
        seen.add(identity)
        normalized.append(
            {"counts": list(counts), "family": family, "seed": seed}
        )
    family_rank = {family: rank for rank, family in enumerate(FAMILIES)}
    normalized.sort(key=lambda item: (family_rank[item["family"]], item["seed"]))
    return {
        "phase": PHASE,
        "requests": normalized,
        "schema_version": DESIGN_SCHEMA,
    }


def load_canonical_design(path: Path) -> dict[str, Any]:
    data = _read_regular_path(path, "mission design")
    value = strict_json_bytes(data, "mission design")
    if type(value) is not dict:
        raise MissionCompileError("mission design must be an object")
    normalized = normalize_design(value)
    if not _json_exact_equal(value, normalized) or data != canonical_json_bytes(
        normalized
    ):
        raise MissionCompileError(
            "mission design must use canonical request order and canonical JSON"
        )
    return normalized


def generator_seed_uint64(family: str, seed: int, map_sha256: str) -> int:
    """Derive one count-independent root for a family/seed mission bank."""

    if family not in FAMILIES:
        raise MissionCompileError("generator seed family differs")
    if not _is_int(seed) or not 1 <= seed <= 10:
        raise MissionCompileError("generator calibration seed must be in 1..10")
    _validate_sha256(map_sha256, "generator seed map SHA-256")
    material = b"\0".join(
        (
            SEED_DOMAIN,
            PHASE.encode("ascii"),
            family.encode("ascii"),
            str(seed).encode("ascii"),
            map_sha256.encode("ascii"),
        )
    )
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def _read_regular_relative(repo_root: Path, relative: PurePosixPath, label: str) -> bytes:
    root_fd = _open_directory_path(repo_root, "repository root")
    try:
        data = _read_regular_at(root_fd, relative, label)
    except BaseException as primary:
        _close_fd_preserving(root_fd, "repository root", primary)
        raise
    _close_fd(root_fd, "repository root")
    return data


def _load_environment_manifest_at(
    repo_fd: int,
) -> tuple[dict[str, Any], bytes]:
    data = _read_regular_at(
        repo_fd, ENVIRONMENT_MANIFEST_PATH, "environment manifest"
    )
    if sha256_bytes(data) != ENVIRONMENT_MANIFEST_SHA256:
        raise MissionCompileError("environment manifest SHA-256 differs")
    value = strict_json_bytes(data, "environment manifest")
    if type(value) is not dict or data != canonical_json_bytes(value):
        raise MissionCompileError("environment manifest is not canonical JSON")
    sidecar = _read_regular_at(
        repo_fd,
        ENVIRONMENT_MANIFEST_SIDECAR_PATH,
        "environment manifest sidecar",
    )
    try:
        tokens = sidecar.decode("ascii").split()
    except UnicodeDecodeError as error:
        raise MissionCompileError("environment manifest sidecar is not ASCII") from error
    if tokens != [ENVIRONMENT_MANIFEST_SHA256, "manifest.json"]:
        raise MissionCompileError("environment manifest sidecar differs")
    return value, data


def _load_environment_manifest(repo_root: Path) -> tuple[dict[str, Any], bytes]:
    repo_fd = _open_directory_path(repo_root, "repository root")
    try:
        result = _load_environment_manifest_at(repo_fd)
    except BaseException as primary:
        _close_fd_preserving(repo_fd, "repository root", primary)
        raise
    _close_fd(repo_fd, "repository root")
    return result


def _decode_png(data: bytes, label: str) -> tuple[int, bytes]:
    signature = b"\x89PNG\r\n\x1a\n"
    if type(data) is not bytes or not data.startswith(signature):
        raise MissionCompileError(f"{label} is not a PNG")
    cursor = len(signature)
    width: int | None = None
    height: int | None = None
    compressed = bytearray()
    seen_iend = False
    while cursor < len(data):
        if cursor + 12 > len(data):
            raise MissionCompileError(f"{label} has a truncated chunk")
        length = struct.unpack(">I", data[cursor : cursor + 4])[0]
        end = cursor + 12 + length
        if end > len(data):
            raise MissionCompileError(f"{label} has a truncated payload")
        kind = data[cursor + 4 : cursor + 8]
        payload = data[cursor + 8 : cursor + 8 + length]
        expected_crc = struct.unpack(">I", data[end - 4 : end])[0]
        actual_crc = binascii.crc32(payload, binascii.crc32(kind)) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise MissionCompileError(f"{label} has a bad {kind!r} CRC")
        cursor = end
        if kind == b"IHDR":
            if width is not None or length != 13:
                raise MissionCompileError(f"{label} has an invalid IHDR")
            width, height, depth, color, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", payload)
            )
            if (
                width != height
                or depth != 8
                or color != 0
                or compression != 0
                or filtering != 0
                or interlace != 0
            ):
                raise MissionCompileError(f"{label} uses an unsupported PNG format")
        elif kind == b"IDAT":
            if width is None or seen_iend:
                raise MissionCompileError(f"{label} has misplaced IDAT data")
            compressed.extend(payload)
        elif kind == b"IEND":
            if length != 0:
                raise MissionCompileError(f"{label} has an invalid IEND")
            seen_iend = True
            break
    if width is None or height is None or not seen_iend or cursor != len(data):
        raise MissionCompileError(f"{label} has an incomplete PNG structure")
    try:
        raw = zlib.decompress(bytes(compressed))
    except zlib.error as error:
        raise MissionCompileError(f"{label} has invalid compressed data") from error
    stride = width + 1
    if len(raw) != stride * height:
        raise MissionCompileError(f"{label} has an unexpected raster size")
    pixels = bytearray()
    for row_index in range(height):
        row = raw[row_index * stride : (row_index + 1) * stride]
        if row[0] != 0:
            raise MissionCompileError(f"{label} uses a nonzero PNG filter")
        pixels.extend(row[1:])
    if set(pixels) - {0, 255}:
        raise MissionCompileError(f"{label} is not binary occupancy")
    return width, bytes(pixels)


def _load_calibration_map_at(
    repo_fd: int, environment_manifest: Mapping[str, Any], family: str
) -> AuthenticatedMap:
    environments = environment_manifest.get("environments")
    if type(environments) is not list:
        raise MissionCompileError("environment manifest cases differ")
    cases = [
        case
        for case in environments
        if type(case) is dict
        and case.get("family") == family
        and case.get("split") == PHASE
    ]
    if len(cases) != 1:
        raise MissionCompileError(f"registered calibration map for {family} is ambiguous")
    map_record = cases[0].get("map")
    if type(map_record) is not dict:
        raise MissionCompileError(f"registered calibration map for {family} differs")
    if set(map_record) != {"bytes", "path", "sha256"}:
        raise MissionCompileError(f"registered calibration map record for {family} differs")
    relative_under_environment = _safe_relative(
        map_record["path"], f"{family} calibration map path"
    )
    expected = PurePosixPath(family) / PHASE / "map.png"
    if relative_under_environment != expected:
        raise MissionCompileError(f"registered calibration map path for {family} differs")
    source_sha = _validate_sha256(
        map_record["sha256"], f"{family} calibration map SHA-256"
    )
    if not _is_int(map_record["bytes"]) or map_record["bytes"] <= 0:
        raise MissionCompileError(f"registered calibration map bytes for {family} differ")
    repository_relative = ENVIRONMENT_ROOT / relative_under_environment
    source = _read_regular_at(
        repo_fd, repository_relative, f"{family} calibration map"
    )
    if len(source) != map_record["bytes"] or sha256_bytes(source) != source_sha:
        raise MissionCompileError(f"authenticated calibration map for {family} differs")
    size, pixels = _decode_png(source, f"{family} calibration map")
    if size != MAP_SIZE_PX or len(pixels) != MAP_SIZE_PX * MAP_SIZE_PX:
        raise MissionCompileError(f"{family} calibration map is not 256 px square")
    return AuthenticatedMap(
        family=family,
        relative_path=repository_relative,
        source_bytes=source,
        source_sha256=source_sha,
        pixels=pixels,
    )


def _load_calibration_map(
    repo_root: Path, environment_manifest: Mapping[str, Any], family: str
) -> AuthenticatedMap:
    repo_fd = _open_directory_path(repo_root, "repository root")
    try:
        result = _load_calibration_map_at(repo_fd, environment_manifest, family)
    except BaseException as primary:
        _close_fd_preserving(repo_fd, "repository root", primary)
        raise
    _close_fd(repo_fd, "repository root")
    return result


def clearance_mask(pixels: Sequence[int], size: int, radius: int) -> bytes:
    """Return pixels whose square radius contains no occupied cell."""

    if (
        not _is_int(size)
        or not _is_int(radius)
        or size <= 0
        or radius < 0
        or len(pixels) != size * size
        or any(pixel not in (0, 255) for pixel in pixels)
    ):
        raise MissionCompileError("clearance-mask input differs")
    stride = size + 1
    integral = [0] * (stride * stride)
    for y in range(size):
        row_sum = 0
        for x in range(size):
            row_sum += pixels[y * size + x] == 0
            integral[(y + 1) * stride + x + 1] = (
                integral[y * stride + x + 1] + row_sum
            )
    result = bytearray(size * size)
    for y in range(size):
        y0, y1 = max(0, y - radius), min(size - 1, y + radius)
        for x in range(size):
            point = y * size + x
            if pixels[point] != 255:
                continue
            x0, x1 = max(0, x - radius), min(size - 1, x + radius)
            occupied = (
                integral[(y1 + 1) * stride + x1 + 1]
                - integral[y0 * stride + x1 + 1]
                - integral[(y1 + 1) * stride + x0]
                + integral[y0 * stride + x0]
            )
            if occupied == 0:
                result[point] = 255
    return bytes(result)


def _largest_component(mask: bytes, size: int) -> tuple[int, ...]:
    seen = bytearray(size * size)
    components: list[list[int]] = []
    for root, value in enumerate(mask):
        if value != 255 or seen[root]:
            continue
        seen[root] = 1
        queue = [root]
        component: list[int] = []
        cursor = 0
        while cursor < len(queue):
            point = queue[cursor]
            cursor += 1
            component.append(point)
            x, y = point % size, point // size
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < size and 0 <= ny < size:
                    neighbour = ny * size + nx
                    if mask[neighbour] == 255 and not seen[neighbour]:
                        seen[neighbour] = 1
                        queue.append(neighbour)
        components.append(component)
    if not components:
        raise MissionCompileError("calibration map has no navigable component")
    largest = max(components, key=lambda component: (len(component), -component[0]))
    return tuple(largest)


def _bresenham_points(start: int, goal: int, size: int) -> tuple[int, ...]:
    x0, y0 = start % size, start // size
    x1, y1 = goal % size, goal // size
    dx, sx = abs(x1 - x0), 1 if x0 < x1 else -1
    dy, sy = -abs(y1 - y0), 1 if y0 < y1 else -1
    error = dx + dy
    result: list[int] = []
    while True:
        result.append(y0 * size + x0)
        if x0 == x1 and y0 == y1:
            return tuple(result)
        twice = 2 * error
        if twice >= dy:
            error += dy
            x0 += sx
        if twice <= dx:
            error += dx
            y0 += sy


def _line_is_free(mask: bytes, size: int, start: int, goal: int) -> bool:
    return all(mask[point] == 255 for point in _bresenham_points(start, goal, size))


def _pixel_xy(point: int, size: int = MAP_SIZE_PX) -> tuple[int, int]:
    return point % size, point // size


def _pixel_to_world(point: int, size: int = MAP_SIZE_PX) -> list[float]:
    x, y = _pixel_xy(point, size)
    return [
        round(((x + 0.5) / size - 0.5) * WORLD_SIZE_M, 6),
        round(((y + 0.5) / size - 0.5) * WORLD_SIZE_M, 6),
    ]


def _world_for_pixel_xy(value: Sequence[int]) -> list[float]:
    return _pixel_to_world(value[1] * MAP_SIZE_PX + value[0])


def _endpoint_has_world_boundary_clearance(point: int) -> bool:
    x, y = _pixel_xy(point)
    distance_px = min(x + 0.5, y + 0.5, MAP_SIZE_PX - x - 0.5, MAP_SIZE_PX - y - 0.5)
    return distance_px * PIXEL_SIZE_M >= MINIMUM_ENDPOINT_OBSTACLE_CLEARANCE_M


def _endpoint_is_separated(point: int, endpoints: Sequence[int]) -> bool:
    x, y = _pixel_xy(point)
    for other in endpoints:
        ox, oy = _pixel_xy(other)
        if Fraction((x - ox) ** 2 + (y - oy) ** 2, 1) < _MIN_ENDPOINT_DISTANCE_PX_SQUARED:
            return False
    return True


def _route_offsets() -> tuple[tuple[int, int], ...]:
    maximum = math.ceil(MAXIMUM_ROUTE_LENGTH_M / PIXEL_SIZE_M)
    offsets = [
        (dx, dy)
        for dy in range(-maximum, maximum + 1)
        for dx in range(-maximum, maximum + 1)
        if _MIN_ROUTE_LENGTH_PX_SQUARED
        <= dx * dx + dy * dy
        <= _MAX_ROUTE_LENGTH_PX_SQUARED
    ]
    offsets.sort(key=lambda offset: (offset[0] ** 2 + offset[1] ** 2, offset[1], offset[0]))
    if not offsets:
        raise MissionCompileError("route offset inventory is empty")
    return tuple(offsets)


ROUTE_OFFSETS = _route_offsets()


def _orientation(a: tuple[int, int], b: tuple[int, int], c: tuple[int, int]) -> int:
    cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return (cross > 0) - (cross < 0)


def _on_segment(a: tuple[int, int], b: tuple[int, int], point: tuple[int, int]) -> bool:
    return (
        _orientation(a, b, point) == 0
        and min(a[0], b[0]) <= point[0] <= max(a[0], b[0])
        and min(a[1], b[1]) <= point[1] <= max(a[1], b[1])
    )


def _segments_intersect(
    a: tuple[int, int],
    b: tuple[int, int],
    c: tuple[int, int],
    d: tuple[int, int],
) -> bool:
    o1, o2 = _orientation(a, b, c), _orientation(a, b, d)
    o3, o4 = _orientation(c, d, a), _orientation(c, d, b)
    if o1 != o2 and o3 != o4:
        return True
    return (
        (o1 == 0 and _on_segment(a, b, c))
        or (o2 == 0 and _on_segment(a, b, d))
        or (o3 == 0 and _on_segment(c, d, a))
        or (o4 == 0 and _on_segment(c, d, b))
    )


def _point_segment_distance_squared(
    point: tuple[int, int], start: tuple[int, int], goal: tuple[int, int]
) -> Fraction:
    vx, vy = goal[0] - start[0], goal[1] - start[1]
    wx, wy = point[0] - start[0], point[1] - start[1]
    length_squared = vx * vx + vy * vy
    projection = wx * vx + wy * vy
    if projection <= 0:
        return Fraction(wx * wx + wy * wy, 1)
    if projection >= length_squared:
        dx, dy = point[0] - goal[0], point[1] - goal[1]
        return Fraction(dx * dx + dy * dy, 1)
    cross = vx * wy - vy * wx
    return Fraction(cross * cross, length_squared)


def _segment_distance_squared(
    first: tuple[int, int],
    second: tuple[int, int],
    other_first: tuple[int, int],
    other_second: tuple[int, int],
) -> Fraction:
    if _segments_intersect(first, second, other_first, other_second):
        return Fraction(0, 1)
    return min(
        _point_segment_distance_squared(first, other_first, other_second),
        _point_segment_distance_squared(second, other_first, other_second),
        _point_segment_distance_squared(other_first, first, second),
        _point_segment_distance_squared(other_second, first, second),
    )


def _static_route_diagnostic(robots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    below = 0
    minimum: Fraction | None = None
    threshold_px_sq = (Fraction(6, 1) / _PIXEL_SIZE_M_EXACT) ** 2
    for left_index, left in enumerate(robots):
        left_start = tuple(left["start_pixel"])
        left_goal = tuple(left["goal_pixel"])
        for right in robots[left_index + 1 :]:
            distance = _segment_distance_squared(
                left_start,
                left_goal,
                tuple(right["start_pixel"]),
                tuple(right["goal_pixel"]),
            )
            minimum = distance if minimum is None else min(minimum, distance)
            if distance < threshold_px_sq:
                below += 1
    minimum_m = None if minimum is None else round(math.sqrt(float(minimum)) * PIXEL_SIZE_M, 8)
    return {
        "diagnostic_only": True,
        "observed_minimum_center_distance_m": minimum_m,
        "route_pair_count_below_threshold": below,
        "threshold_m": STATIC_ROUTE_DISTANCE_DIAGNOSTIC_M,
    }


def _geometry_policy() -> dict[str, Any]:
    return {
        "clearance_mask_radius_px": CLEARANCE_RADIUS_PX,
        "controller_timestep_seconds": CONTROLLER_TIMESTEP_SECONDS,
        "fixed_work_timesteps": FIXED_WORK_TIMESTEPS,
        "horizon_distance_m": HORIZON_DISTANCE_M,
        "horizon_feasibility_rule": (
            "max(0, route_length_m - robot_radius_m) <= horizon_distance_m"
        ),
        "maximum_linear_speed_m_per_second": MAXIMUM_LINEAR_SPEED_M_PER_SECOND,
        "maximum_center_to_center_route_length_m": MAXIMUM_ROUTE_LENGTH_M,
        "maximum_goal_reach_travel_distance_m": (
            MAXIMUM_GOAL_REACH_TRAVEL_DISTANCE_M
        ),
        "minimum_endpoint_obstacle_clearance_m": MINIMUM_ENDPOINT_OBSTACLE_CLEARANCE_M,
        "minimum_endpoint_pair_center_distance_m": MINIMUM_ENDPOINT_PAIR_CENTER_DISTANCE_M,
        "minimum_center_to_center_route_length_m": MINIMUM_ROUTE_LENGTH_M,
        "minimum_goal_reach_travel_distance_m": (
            MINIMUM_GOAL_REACH_TRAVEL_DISTANCE_M
        ),
        "robot_radius_m": ROBOT_RADIUS_M,
        "route_map_clearance_classification": (
            "static-raster-preflight-only-not-dynamic-obstacle-proof"
        ),
        "static_inter_route_distance_gate": False,
        "static_inter_route_distance_threshold_m": STATIC_ROUTE_DISTANCE_DIAGNOSTIC_M,
        "straight_line_routes": True,
        "world_size_m": WORLD_SIZE_M,
    }


def _generate_robots(
    registered_map: AuthenticatedMap, seed: int, maximum_count: int
) -> tuple[int, list[dict[str, Any]]]:
    safe = clearance_mask(registered_map.pixels, MAP_SIZE_PX, CLEARANCE_RADIUS_PX)
    candidates = tuple(
        point
        for point in _largest_component(safe, MAP_SIZE_PX)
        if _endpoint_has_world_boundary_clearance(point)
    )
    if not candidates:
        raise MissionCompileError(f"{registered_map.family} has no safe endpoints")
    generator_seed = generator_seed_uint64(
        registered_map.family, seed, registered_map.source_sha256
    )
    stream = _HashStream(generator_seed)
    endpoints: list[int] = []
    robots: list[dict[str, Any]] = []
    for robot_id in range(maximum_count):
        accepted: tuple[int, int] | None = None
        for _attempt in range(MAXIMUM_ATTEMPTS_PER_ROBOT):
            start = candidates[stream.randbelow(len(candidates))]
            dx, dy = ROUTE_OFFSETS[stream.randbelow(len(ROUTE_OFFSETS))]
            start_x, start_y = _pixel_xy(start)
            goal_x, goal_y = start_x + dx, start_y + dy
            if not (0 <= goal_x < MAP_SIZE_PX and 0 <= goal_y < MAP_SIZE_PX):
                continue
            goal = goal_y * MAP_SIZE_PX + goal_x
            if safe[goal] != 255 or not _endpoint_has_world_boundary_clearance(goal):
                continue
            if not _endpoint_is_separated(start, endpoints):
                continue
            if not _endpoint_is_separated(goal, (*endpoints, start)):
                continue
            if not _line_is_free(safe, MAP_SIZE_PX, start, goal):
                continue
            accepted = (start, goal)
            break
        if accepted is None:
            raise MissionCompileError(
                f"could not place robot {robot_id} for "
                f"{registered_map.family}/seed-{seed} after the fixed attempt bound"
            )
        start, goal = accepted
        endpoints.extend((start, goal))
        start_x, start_y = _pixel_xy(start)
        goal_x, goal_y = _pixel_xy(goal)
        length_m = round(
            math.hypot(goal_x - start_x, goal_y - start_y) * PIXEL_SIZE_M,
            8,
        )
        required_travel = round(max(0.0, length_m - ROBOT_RADIUS_M), 8)
        if required_travel > HORIZON_DISTANCE_M:
            raise MissionCompileError("generated route exceeds the fixed-work horizon")
        if not (
            MINIMUM_GOAL_REACH_TRAVEL_DISTANCE_M
            <= required_travel
            <= MAXIMUM_GOAL_REACH_TRAVEL_DISTANCE_M
        ):
            raise MissionCompileError(
                "generated route differs from the goal-reach travel window"
            )
        robots.append(
            {
                "decision": stream.randbelow(10),
                "goal_pixel": [goal_x, goal_y],
                "required_travel_distance_m": required_travel,
                "robot_id": robot_id,
                "route_length_m": length_m,
                "start_pixel": [start_x, start_y],
                "waypoints_m": [_pixel_to_world(start), _pixel_to_world(goal)],
            }
        )
    return generator_seed, robots


def _bank_semantic_payload(bank_without_id: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value for key, value in bank_without_id.items() if key != "bank_id"
    }


def _build_bank(
    registered_map: AuthenticatedMap, seed: int, counts: Sequence[int]
) -> dict[str, Any]:
    generator_seed, robots = _generate_robots(registered_map, seed, counts[-1])
    bank_without_id: dict[str, Any] = {
        "count_grid": list(counts),
        "family": registered_map.family,
        "generator_seed_uint64": generator_seed,
        "geometry_policy": _geometry_policy(),
        "map": {
            "bytes": len(registered_map.source_bytes),
            "path": registered_map.relative_path.as_posix(),
            "sha256": registered_map.source_sha256,
            "size_px": MAP_SIZE_PX,
        },
        "max_robot_count": counts[-1],
        "phase": PHASE,
        "robots": robots,
        "sampling_structure": _sampling_structure(),
        "schema_version": BANK_SCHEMA,
        "seed": seed,
        "seed_roles": _seed_roles(),
        "static_inter_route_distance": _static_route_diagnostic(robots),
    }
    bank_id = sha256_bytes(canonical_json_bytes(_bank_semantic_payload(bank_without_id)))
    return {"bank_id": bank_id, **bank_without_id}


def _scenario_from_bank(bank: Mapping[str, Any], count: int) -> dict[str, Any]:
    robots = bank["robots"][:count]
    return {
        "decisions": {str(robot["robot_id"]): robot["decision"] for robot in robots},
        "metadata": {
            "clearance_radius_px": CLEARANCE_RADIUS_PX,
            "environment_id": bank["family"],
            "fixed_work_timesteps": FIXED_WORK_TIMESTEPS,
            "generator_seed_uint64": bank["generator_seed_uint64"],
            "map_sha256": bank["map"]["sha256"],
            "maximum_linear_speed_m_per_second": MAXIMUM_LINEAR_SPEED_M_PER_SECOND,
            "mission_bank_id": bank["bank_id"],
            "mission_seed": bank["seed"],
            "phase": PHASE,
            "prefix_robot_count": count,
            "robot_radius_m": ROBOT_RADIUS_M,
            "scenario_kind": SCENARIO_KIND,
            "timestep_seconds": CONTROLLER_TIMESTEP_SECONDS,
            "world_size_m": WORLD_SIZE_M,
        },
        "num_robots": count,
        "seed_robots": [],
        "waypoints": {
            str(robot["robot_id"]): robot["waypoints_m"] for robot in robots
        },
    }


def effective_scenario_sha256(scenario: Mapping[str, Any]) -> str:
    """Hash only fields that affect the file-scenario execution."""

    scenario_map = _require_exact_keys(
        scenario,
        {"decisions", "metadata", "num_robots", "seed_robots", "waypoints"},
        "scenario",
    )
    effective = {
        "decisions": scenario_map["decisions"],
        "num_robots": scenario_map["num_robots"],
        "seed_robots": scenario_map["seed_robots"],
        "waypoints": scenario_map["waypoints"],
    }
    return sha256_bytes(canonical_json_bytes(effective))


def _file_identity(path: PurePosixPath, data: bytes) -> dict[str, Any]:
    return {"bytes": len(data), "path": path.as_posix(), "sha256": sha256_bytes(data)}


def _bank_path(family: str, seed: int) -> PurePosixPath:
    return PurePosixPath("missions") / family / f"seed-{seed:04d}" / "bank.json"


def _scenario_path(family: str, seed: int, count: int) -> PurePosixPath:
    return (
        PurePosixPath("missions")
        / family
        / f"seed-{seed:04d}"
        / f"n-{count}"
        / "scenario.json"
    )


def compile_mission_banks(
    repository_root: Path, design: Mapping[str, Any]
) -> MissionCompilation:
    """Compile a caller-selected calibration inventory entirely in memory."""

    normalized_design = normalize_design(design)
    design_bytes = canonical_json_bytes(normalized_design)
    design_sha = sha256_bytes(design_bytes)
    requested_families = {
        request["family"] for request in normalized_design["requests"]
    }
    repo_fd = _open_directory_path(repository_root, "repository root")
    try:
        environment_manifest, environment_manifest_bytes = (
            _load_environment_manifest_at(repo_fd)
        )
        maps = tuple(
            _load_calibration_map_at(repo_fd, environment_manifest, family)
            for family in FAMILIES
            if family in requested_families
        )
    except BaseException as primary:
        _close_fd_preserving(repo_fd, "repository root", primary)
        raise
    _close_fd(repo_fd, "repository root")
    map_by_family = {registered.family: registered for registered in maps}
    compiled_files: list[CompiledFile] = [
        CompiledFile(
            relative_path=PurePosixPath("design.json"),
            data=design_bytes,
            sha256=design_sha,
            kind="design",
        )
    ]
    records: list[dict[str, Any]] = []
    effective_by_family_count: dict[tuple[str, int], set[str]] = {}
    for request in normalized_design["requests"]:
        family, seed, counts = request["family"], request["seed"], request["counts"]
        bank = _build_bank(map_by_family[family], seed, counts)
        bank_data = canonical_json_bytes(bank)
        bank_path = _bank_path(family, seed)
        compiled_files.append(
            CompiledFile(
                relative_path=bank_path,
                data=bank_data,
                sha256=sha256_bytes(bank_data),
                kind="bank",
            )
        )
        scenario_records: list[dict[str, Any]] = []
        prior_scenario_data: bytes | None = None
        for count in counts:
            scenario = _scenario_from_bank(bank, count)
            scenario_data = canonical_json_bytes(scenario)
            if scenario_data == prior_scenario_data:
                raise MissionCompileError("different count prefixes reused scenario bytes")
            prior_scenario_data = scenario_data
            effective_sha = effective_scenario_sha256(scenario)
            key = (family, count)
            if effective_sha in effective_by_family_count.setdefault(key, set()):
                raise MissionCompileError(
                    f"{family}/n-{count} reuses an effective scenario across seeds"
                )
            effective_by_family_count[key].add(effective_sha)
            scenario_path = _scenario_path(family, seed, count)
            compiled_files.append(
                CompiledFile(
                    relative_path=scenario_path,
                    data=scenario_data,
                    sha256=sha256_bytes(scenario_data),
                    kind="scenario",
                )
            )
            scenario_records.append(
                {
                    "effective_scenario_sha256": effective_sha,
                    "robot_count": count,
                    "scenario": _file_identity(scenario_path, scenario_data),
                }
            )
        records.append(
            {
                "bank": _file_identity(bank_path, bank_data),
                "bank_id": bank["bank_id"],
                "counts": scenario_records,
                "family": family,
                "generator_seed_uint64": bank["generator_seed_uint64"],
                "map": dict(bank["map"]),
                "max_robot_count": counts[-1],
                "phase": PHASE,
                "sampling_structure": _sampling_structure(),
                "seed": seed,
                "seed_roles": _seed_roles(),
                "static_inter_route_distance": dict(bank["static_inter_route_distance"]),
            }
        )
    manifest: dict[str, Any] = {
        "artifacts": records,
        "classification": {
            "algorithm_execution_during_compilation": False,
            "authorizes_scientific_execution": False,
            "network_access": False,
            "plan_freeze": False,
            "result_evidence": False,
            "validation_artifacts_read": False,
        },
        "design": _file_identity(PurePosixPath("design.json"), design_bytes),
        "environment_manifest": _file_identity(
            ENVIRONMENT_MANIFEST_PATH, environment_manifest_bytes
        ),
        "schema_version": MANIFEST_SCHEMA,
    }
    manifest_bytes = canonical_json_bytes(manifest)
    manifest_sha = sha256_bytes(manifest_bytes)
    compiled_files.append(
        CompiledFile(
            relative_path=PurePosixPath("manifest.json"),
            data=manifest_bytes,
            sha256=manifest_sha,
            kind="manifest",
        )
    )
    compilation = MissionCompilation(
        design=normalized_design,
        design_bytes=design_bytes,
        design_sha256=design_sha,
        environment_manifest_bytes=environment_manifest_bytes,
        environment_manifest_sha256=ENVIRONMENT_MANIFEST_SHA256,
        maps=maps,
        files=tuple(compiled_files),
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        manifest_sha256=manifest_sha,
    )
    validate_compilation(compilation)
    return compilation


def _validate_robot(
    robot: object,
    expected_id: int,
    safe: bytes,
    prior_endpoints: list[int],
) -> tuple[int, int]:
    record = _require_exact_keys(
        robot,
        {
            "decision",
            "goal_pixel",
            "required_travel_distance_m",
            "robot_id",
            "route_length_m",
            "start_pixel",
            "waypoints_m",
        },
        f"bank robot {expected_id}",
    )
    if (
        not _is_int(record["robot_id"])
        or record["robot_id"] != expected_id
        or not _is_int(record["decision"])
        or not 0 <= record["decision"] <= 9
    ):
        raise MissionCompileError("bank robot identity or decision differs")
    pixel_values: list[tuple[int, int]] = []
    for label in ("start_pixel", "goal_pixel"):
        point = record[label]
        if (
            type(point) is not list
            or len(point) != 2
            or any(not _is_int(coordinate) or not 0 <= coordinate < MAP_SIZE_PX for coordinate in point)
        ):
            raise MissionCompileError(f"bank robot {label} differs")
        pixel_values.append((point[0], point[1]))
    start_xy, goal_xy = pixel_values
    start = start_xy[1] * MAP_SIZE_PX + start_xy[0]
    goal = goal_xy[1] * MAP_SIZE_PX + goal_xy[0]
    if (
        safe[start] != 255
        or safe[goal] != 255
        or not _endpoint_has_world_boundary_clearance(start)
        or not _endpoint_has_world_boundary_clearance(goal)
        or not _line_is_free(safe, MAP_SIZE_PX, start, goal)
    ):
        raise MissionCompileError("bank route violates authenticated map clearance")
    if not _endpoint_is_separated(start, prior_endpoints) or not _endpoint_is_separated(goal, (*prior_endpoints, start)):
        raise MissionCompileError("bank endpoints violate pair-centre separation")
    dx, dy = goal_xy[0] - start_xy[0], goal_xy[1] - start_xy[1]
    length_m = round(math.hypot(dx, dy) * PIXEL_SIZE_M, 8)
    if (
        type(record["route_length_m"]) is not float
        or not math.isfinite(record["route_length_m"])
        or type(record["required_travel_distance_m"]) is not float
        or not math.isfinite(record["required_travel_distance_m"])
        or not MINIMUM_ROUTE_LENGTH_M <= length_m <= MAXIMUM_ROUTE_LENGTH_M
        or record["route_length_m"] != length_m
        or record["required_travel_distance_m"]
        != round(max(0.0, length_m - ROBOT_RADIUS_M), 8)
        or not (
            MINIMUM_GOAL_REACH_TRAVEL_DISTANCE_M
            <= record["required_travel_distance_m"]
            <= MAXIMUM_GOAL_REACH_TRAVEL_DISTANCE_M
        )
        or record["required_travel_distance_m"] > HORIZON_DISTANCE_M
    ):
        raise MissionCompileError("bank route length or horizon feasibility differs")
    if not _json_exact_equal(
        record["waypoints_m"],
        [_world_for_pixel_xy(start_xy), _world_for_pixel_xy(goal_xy)],
    ):
        raise MissionCompileError("bank route world coordinates differ")
    prior_endpoints.extend((start, goal))
    return start, goal


def _validate_bank(bank: object, registered_map: AuthenticatedMap, request: Mapping[str, Any]) -> dict[str, Any]:
    record = _require_exact_keys(
        bank,
        {
            "bank_id",
            "count_grid",
            "family",
            "generator_seed_uint64",
            "geometry_policy",
            "map",
            "max_robot_count",
            "phase",
            "robots",
            "sampling_structure",
            "schema_version",
            "seed",
            "seed_roles",
            "static_inter_route_distance",
        },
        "mission bank",
    )
    if (
        record["schema_version"] != BANK_SCHEMA
        or record["phase"] != PHASE
        or record["family"] != request["family"]
        or not _is_int(record["seed"])
        or record["seed"] != request["seed"]
        or not _json_exact_equal(record["count_grid"], request["counts"])
        or not _is_int(record["max_robot_count"])
        or record["max_robot_count"] != request["counts"][-1]
        or not _json_exact_equal(record["geometry_policy"], _geometry_policy())
        or not _json_exact_equal(record["seed_roles"], _seed_roles())
        or not _json_exact_equal(
            record["sampling_structure"], _sampling_structure()
        )
        or not _is_int(record["generator_seed_uint64"])
        or record["generator_seed_uint64"]
        != generator_seed_uint64(record["family"], record["seed"], registered_map.source_sha256)
    ):
        raise MissionCompileError("mission bank identity or policy differs")
    expected_map = {
        "bytes": len(registered_map.source_bytes),
        "path": registered_map.relative_path.as_posix(),
        "sha256": registered_map.source_sha256,
        "size_px": MAP_SIZE_PX,
    }
    if not _json_exact_equal(record["map"], expected_map):
        raise MissionCompileError("mission bank map identity differs")
    robots = record["robots"]
    if type(robots) is not list or len(robots) != record["max_robot_count"]:
        raise MissionCompileError("mission bank robot inventory differs")
    safe = clearance_mask(registered_map.pixels, MAP_SIZE_PX, CLEARANCE_RADIUS_PX)
    endpoints: list[int] = []
    for robot_id, robot in enumerate(robots):
        _validate_robot(robot, robot_id, safe, endpoints)
    if not _json_exact_equal(
        record["static_inter_route_distance"],
        _static_route_diagnostic(robots),
    ):
        raise MissionCompileError("mission bank static-route diagnostic differs")
    expected_bank_id = sha256_bytes(canonical_json_bytes(_bank_semantic_payload(record)))
    if (
        _validate_sha256(record["bank_id"], "mission bank id")
        != expected_bank_id
    ):
        raise MissionCompileError("mission bank semantic identity differs")
    return dict(record)


def _validate_scenario(scenario: object, bank: Mapping[str, Any], count: int) -> dict[str, Any]:
    record = _require_exact_keys(
        scenario,
        {"decisions", "metadata", "num_robots", "seed_robots", "waypoints"},
        "prefix scenario",
    )
    metadata = _require_exact_keys(
        record["metadata"],
        {
            "clearance_radius_px",
            "environment_id",
            "fixed_work_timesteps",
            "generator_seed_uint64",
            "map_sha256",
            "maximum_linear_speed_m_per_second",
            "mission_bank_id",
            "mission_seed",
            "phase",
            "prefix_robot_count",
            "robot_radius_m",
            "scenario_kind",
            "timestep_seconds",
            "world_size_m",
        },
        "prefix scenario metadata",
    )
    expected_ids = [str(index) for index in range(count)]
    if (
        not _is_int(record["num_robots"])
        or record["num_robots"] != count
        or type(record["seed_robots"]) is not list
        or record["seed_robots"] != []
        or type(record["decisions"]) is not dict
        or set(record["decisions"]) != set(expected_ids)
        or type(record["waypoints"]) is not dict
        or set(record["waypoints"]) != set(expected_ids)
    ):
        raise MissionCompileError("prefix scenario exact-N identity differs")
    expected_metadata = {
        "clearance_radius_px": CLEARANCE_RADIUS_PX,
        "environment_id": bank["family"],
        "fixed_work_timesteps": FIXED_WORK_TIMESTEPS,
        "generator_seed_uint64": bank["generator_seed_uint64"],
        "map_sha256": bank["map"]["sha256"],
        "maximum_linear_speed_m_per_second": MAXIMUM_LINEAR_SPEED_M_PER_SECOND,
        "mission_bank_id": bank["bank_id"],
        "mission_seed": bank["seed"],
        "phase": PHASE,
        "prefix_robot_count": count,
        "robot_radius_m": ROBOT_RADIUS_M,
        "scenario_kind": SCENARIO_KIND,
        "timestep_seconds": CONTROLLER_TIMESTEP_SECONDS,
        "world_size_m": WORLD_SIZE_M,
    }
    if not _json_exact_equal(dict(metadata), expected_metadata):
        raise MissionCompileError("prefix scenario metadata differs")
    robots = bank["robots"][:count]
    if not _json_exact_equal(
        record["decisions"],
        {str(robot["robot_id"]): robot["decision"] for robot in robots},
    ):
        raise MissionCompileError("prefix scenario decisions differ from bank")
    if not _json_exact_equal(
        record["waypoints"],
        {str(robot["robot_id"]): robot["waypoints_m"] for robot in robots},
    ):
        raise MissionCompileError("prefix scenario routes differ from bank")
    return dict(record)


def validate_compilation(compilation: MissionCompilation) -> None:
    """Recheck canonical bytes, schemas, geometry, prefixes, and seed diversity."""

    if type(compilation) is not MissionCompilation:
        raise MissionCompileError("compilation object type differs")
    if (
        type(compilation.design) is not dict
        or type(compilation.design_bytes) is not bytes
        or type(compilation.environment_manifest_bytes) is not bytes
        or type(compilation.maps) is not tuple
        or type(compilation.files) is not tuple
        or type(compilation.manifest) is not dict
        or type(compilation.manifest_bytes) is not bytes
    ):
        raise MissionCompileError("compilation top-level field type differs")
    normalized = normalize_design(compilation.design)
    if (
        not _json_exact_equal(normalized, compilation.design)
        or compilation.design_bytes != canonical_json_bytes(normalized)
        or compilation.design_sha256 != sha256_bytes(compilation.design_bytes)
        or compilation.environment_manifest_sha256 != ENVIRONMENT_MANIFEST_SHA256
        or sha256_bytes(compilation.environment_manifest_bytes) != ENVIRONMENT_MANIFEST_SHA256
        or compilation.manifest_bytes != canonical_json_bytes(compilation.manifest)
        or compilation.manifest_sha256 != sha256_bytes(compilation.manifest_bytes)
    ):
        raise MissionCompileError("compilation top-level identity differs")
    paths: set[PurePosixPath] = set()
    by_path: dict[PurePosixPath, CompiledFile] = {}
    for artifact in compilation.files:
        if (
            type(artifact) is not CompiledFile
            or type(artifact.relative_path) is not PurePosixPath
            or type(artifact.data) is not bytes
            or type(artifact.sha256) is not str
            or type(artifact.kind) is not str
        ):
            raise MissionCompileError("compiled output record type differs")
        _safe_relative(artifact.relative_path.as_posix(), "compiled output path")
        if artifact.relative_path in paths:
            raise MissionCompileError("compiled output path repeats")
        paths.add(artifact.relative_path)
        if artifact.sha256 != sha256_bytes(artifact.data):
            raise MissionCompileError("compiled output hash differs")
        parsed = strict_json_bytes(artifact.data, artifact.relative_path.as_posix())
        if artifact.data != canonical_json_bytes(parsed):
            raise MissionCompileError("compiled output is not canonical JSON")
        by_path[artifact.relative_path] = artifact
    if by_path.get(PurePosixPath("design.json"), None) is None or by_path[PurePosixPath("design.json")].data != compilation.design_bytes:
        raise MissionCompileError("compiled design file differs")
    if by_path.get(PurePosixPath("manifest.json"), None) is None or by_path[PurePosixPath("manifest.json")].data != compilation.manifest_bytes:
        raise MissionCompileError("compiled manifest file differs")
    manifest = _require_exact_keys(
        compilation.manifest,
        {"artifacts", "classification", "design", "environment_manifest", "schema_version"},
        "compiled manifest",
    )
    expected_classification = {
        "algorithm_execution_during_compilation": False,
        "authorizes_scientific_execution": False,
        "network_access": False,
        "plan_freeze": False,
        "result_evidence": False,
        "validation_artifacts_read": False,
    }
    if not _json_exact_equal(
        manifest["schema_version"], MANIFEST_SCHEMA
    ) or not _json_exact_equal(manifest["classification"], expected_classification):
        raise MissionCompileError("compiled manifest classification differs")
    if not _json_exact_equal(
        manifest["design"],
        _file_identity(PurePosixPath("design.json"), compilation.design_bytes),
    ):
        raise MissionCompileError("compiled manifest design identity differs")
    if not _json_exact_equal(
        manifest["environment_manifest"],
        _file_identity(
            ENVIRONMENT_MANIFEST_PATH, compilation.environment_manifest_bytes
        ),
    ):
        raise MissionCompileError("compiled manifest environment identity differs")
    if type(manifest["artifacts"]) is not list or len(manifest["artifacts"]) != len(normalized["requests"]):
        raise MissionCompileError("compiled manifest bank inventory differs")
    map_by_family: dict[str, AuthenticatedMap] = {}
    for registered in compilation.maps:
        if (
            type(registered) is not AuthenticatedMap
            or type(registered.family) is not str
            or registered.family not in FAMILIES
            or type(registered.relative_path) is not PurePosixPath
            or type(registered.source_bytes) is not bytes
            or type(registered.source_sha256) is not str
            or type(registered.pixels) is not bytes
            or registered.relative_path
            != ENVIRONMENT_ROOT / registered.family / PHASE / "map.png"
            or registered.family in map_by_family
        ):
            raise MissionCompileError("compiled authenticated map record differs")
        map_by_family[registered.family] = registered
    if set(map_by_family) != {
        request["family"] for request in normalized["requests"]
    }:
        raise MissionCompileError("compiled authenticated map inventory differs")
    effective_seen: dict[tuple[str, int], set[str]] = {}
    prior_prefixes: dict[tuple[str, int], tuple[dict[str, Any], int]] = {}
    expected_file_paths = {PurePosixPath("design.json"), PurePosixPath("manifest.json")}
    for request, manifest_record in zip(normalized["requests"], manifest["artifacts"]):
        registered_map = map_by_family.get(request["family"])
        if registered_map is None:
            raise MissionCompileError("compiled map inventory is incomplete")
        if (
            sha256_bytes(registered_map.source_bytes) != registered_map.source_sha256
            or _decode_png(registered_map.source_bytes, "compiled authenticated map")[1]
            != registered_map.pixels
        ):
            raise MissionCompileError("compiled authenticated map bytes differ")
        bank_path = _bank_path(request["family"], request["seed"])
        expected_file_paths.add(bank_path)
        bank_file = by_path.get(bank_path)
        if bank_file is None or not _json_exact_equal(bank_file.kind, "bank"):
            raise MissionCompileError("compiled mission bank file is missing")
        bank_value = strict_json_bytes(bank_file.data, "compiled mission bank")
        bank = _validate_bank(bank_value, registered_map, request)
        expected_manifest_record_keys = {
            "bank",
            "bank_id",
            "counts",
            "family",
            "generator_seed_uint64",
            "map",
            "max_robot_count",
            "phase",
            "sampling_structure",
            "seed",
            "seed_roles",
            "static_inter_route_distance",
        }
        record = _require_exact_keys(manifest_record, expected_manifest_record_keys, "manifest bank record")
        if (
            not _json_exact_equal(
                record["bank"], _file_identity(bank_path, bank_file.data)
            )
            or not _json_exact_equal(record["bank_id"], bank["bank_id"])
            or not _json_exact_equal(record["family"], bank["family"])
            or not _json_exact_equal(record["seed"], bank["seed"])
            or not _json_exact_equal(record["phase"], PHASE)
            or not _json_exact_equal(
                record["generator_seed_uint64"], bank["generator_seed_uint64"]
            )
            or not _json_exact_equal(record["map"], bank["map"])
            or not _json_exact_equal(
                record["max_robot_count"], bank["max_robot_count"]
            )
            or not _json_exact_equal(
                record["seed_roles"], bank["seed_roles"]
            )
            or not _json_exact_equal(
                record["sampling_structure"], bank["sampling_structure"]
            )
            or not _json_exact_equal(
                record["static_inter_route_distance"],
                bank["static_inter_route_distance"],
            )
            or type(record["counts"]) is not list
            or len(record["counts"]) != len(request["counts"])
        ):
            raise MissionCompileError("manifest bank record differs")
        for count, count_record in zip(request["counts"], record["counts"]):
            count_row = _require_exact_keys(
                count_record,
                {"effective_scenario_sha256", "robot_count", "scenario"},
                "manifest scenario record",
            )
            scenario_path = _scenario_path(request["family"], request["seed"], count)
            expected_file_paths.add(scenario_path)
            scenario_file = by_path.get(scenario_path)
            if scenario_file is None or not _json_exact_equal(
                scenario_file.kind, "scenario"
            ):
                raise MissionCompileError("compiled prefix scenario is missing")
            scenario_value = strict_json_bytes(scenario_file.data, "compiled prefix scenario")
            scenario = _validate_scenario(scenario_value, bank, count)
            effective_sha = effective_scenario_sha256(scenario)
            if (
                not _json_exact_equal(count_row["robot_count"], count)
                or not _json_exact_equal(
                    count_row["scenario"],
                    _file_identity(scenario_path, scenario_file.data),
                )
                or not _json_exact_equal(
                    count_row["effective_scenario_sha256"], effective_sha
                )
            ):
                raise MissionCompileError("manifest scenario record differs")
            key = (request["family"], count)
            if effective_sha in effective_seen.setdefault(key, set()):
                raise MissionCompileError("effective scenario hash repeats across seeds")
            effective_seen[key].add(effective_sha)
            prefix_key = (request["family"], request["seed"])
            prior = prior_prefixes.get(prefix_key)
            if prior is not None:
                prior_scenario, prior_count = prior
                prior_ids = [str(index) for index in range(prior_count)]
                if (
                    not _json_exact_equal(
                        {
                            robot_id: scenario["decisions"][robot_id]
                            for robot_id in prior_ids
                        },
                        prior_scenario["decisions"],
                    )
                    or not _json_exact_equal(
                        {
                            robot_id: scenario["waypoints"][robot_id]
                            for robot_id in prior_ids
                        },
                        prior_scenario["waypoints"],
                    )
                ):
                    raise MissionCompileError("scenario count prefixes are not nested")
            prior_prefixes[prefix_key] = (scenario, count)
    if set(by_path) != expected_file_paths:
        raise MissionCompileError("compiled output inventory has extra or missing files")
    expected_kinds = {
        path: (
            "design"
            if path == PurePosixPath("design.json")
            else "manifest"
            if path == PurePosixPath("manifest.json")
            else "bank"
            if path.name == "bank.json"
            else "scenario"
        )
        for path in expected_file_paths
    }
    if any(
        not _json_exact_equal(by_path[path].kind, kind)
        for path, kind in expected_kinds.items()
    ):
        raise MissionCompileError("compiled output kinds differ")


def _open_or_create_relative_directory(
    root_fd: int,
    parts: Sequence[str],
    label: str,
) -> int:
    try:
        current = os.dup(root_fd)
    except OSError as error:
        raise MissionCompileError(f"{label} root descriptor cannot be duplicated") from error
    try:
        for part in parts:
            created = False
            try:
                os.mkdir(part, mode=0o755, dir_fd=current)
                created = True
            except FileExistsError:
                pass
            except OSError as error:
                raise MissionCompileError(
                    f"{label} directory cannot be created"
                ) from error
            if created:
                os.fsync(current)
            try:
                following = os.open(
                    part,
                    _DIRECTORY_OPEN_FLAGS,
                    dir_fd=current,
                )
            except OSError as error:
                raise MissionCompileError(
                    f"{label} directory is missing, replaced, or a symlink"
                ) from error
            try:
                _close_fd(current, f"{label} ancestor")
            except BaseException as primary:
                _close_fd_preserving(following, f"{label} child", primary)
                raise
            current = following
        return current
    except BaseException as primary:
        _close_fd_preserving(current, label, primary)
        raise


def _write_exclusive(
    root_fd: int,
    relative: PurePosixPath,
    data: bytes,
) -> None:
    safe = _safe_relative(relative.as_posix(), "published output path")
    if type(data) is not bytes:
        raise MissionCompileError("published output data must be bytes")
    parent_fd = _open_or_create_relative_directory(
        root_fd,
        safe.parts[:-1],
        f"published {safe.as_posix()} parent",
    )
    try:
        try:
            file_fd = os.open(
                safe.parts[-1],
                _EXCLUSIVE_WRITE_FLAGS,
                0o644,
                dir_fd=parent_fd,
            )
        except OSError as error:
            raise MissionCompileError(
                f"published {safe.as_posix()} cannot be created exclusively"
            ) from error
        try:
            offset = 0
            while offset < len(data):
                written = os.write(file_fd, data[offset:])
                if written <= 0:
                    raise OSError("exclusive publication write made no progress")
                offset += written
            os.fsync(file_fd)
        except BaseException as primary:
            _close_fd_preserving(
                file_fd,
                f"published {safe.as_posix()}",
                primary,
            )
            raise
        _close_fd(file_fd, f"published {safe.as_posix()}")
        os.fsync(parent_fd)
    except BaseException as primary:
        _close_fd_preserving(
            parent_fd,
            f"published {safe.as_posix()} parent",
            primary,
        )
        raise
    _close_fd(parent_fd, f"published {safe.as_posix()} parent")


def _remove_directory_contents(directory_fd: int, label: str) -> None:
    try:
        names = sorted(os.listdir(directory_fd))
    except OSError as error:
        raise MissionCompileError(f"{label} cannot be inventoried for rollback") from error
    for name in names:
        if type(name) is not str or name in ("", ".", "..") or os.sep in name:
            raise MissionCompileError(f"{label} contains an unsafe rollback entry")
        try:
            child_fd = os.open(
                name,
                _DIRECTORY_OPEN_FLAGS,
                dir_fd=directory_fd,
            )
        except OSError as open_error:
            if open_error.errno not in (errno.ELOOP, errno.ENOTDIR):
                raise MissionCompileError(
                    f"{label}/{name} cannot be classified safely for rollback"
                ) from open_error
            try:
                os.unlink(name, dir_fd=directory_fd)
            except OSError as error:
                raise MissionCompileError(
                    f"{label}/{name} cannot be removed during rollback"
                ) from error
            continue
        try:
            _remove_directory_contents(child_fd, f"{label}/{name}")
        except BaseException as primary:
            _close_fd_preserving(child_fd, f"{label}/{name}", primary)
            raise
        _close_fd(child_fd, f"{label}/{name}")
        try:
            os.rmdir(name, dir_fd=directory_fd)
        except OSError as error:
            raise MissionCompileError(
                f"{label}/{name} cannot be removed during rollback"
            ) from error
    os.fsync(directory_fd)


def _rollback_created_root(parent_fd: int, root_name: str) -> None:
    try:
        root_fd = os.open(
            root_name,
            _DIRECTORY_OPEN_FLAGS,
            dir_fd=parent_fd,
        )
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            try:
                os.unlink(root_name, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except OSError as unlink_error:
                raise MissionCompileError(
                    "replaced output-root entry cannot be removed safely"
                ) from unlink_error
            return
        raise MissionCompileError(
            "created output root cannot be reopened safely for rollback"
        ) from error
    try:
        _remove_directory_contents(root_fd, "created output root")
    except BaseException as primary:
        _close_fd_preserving(root_fd, "created output root", primary)
        raise
    _close_fd(root_fd, "created output root")
    try:
        os.rmdir(root_name, dir_fd=parent_fd)
        os.fsync(parent_fd)
    except OSError as error:
        raise MissionCompileError("created output root rollback failed") from error


def _directory_object_identity(fd: int) -> tuple[int, int]:
    metadata = os.fstat(fd)
    if not stat.S_ISDIR(metadata.st_mode):
        raise MissionCompileError("publication parent descriptor is not a directory")
    return metadata.st_dev, metadata.st_ino


def _usable_rollback_parent(
    parent_fd: int,
    parent_path: Path,
    expected_identity: tuple[int, int],
) -> tuple[int, bool]:
    try:
        if _directory_object_identity(parent_fd) == expected_identity:
            return parent_fd, False
    except (MissionCompileError, OSError):
        pass
    reopened = _open_directory_path(parent_path, "output-root rollback parent")
    try:
        if _directory_object_identity(reopened) != expected_identity:
            raise MissionCompileError(
                "output-root parent identity changed before rollback"
            )
    except BaseException as primary:
        _close_fd_preserving(reopened, "output-root rollback parent", primary)
        raise
    return reopened, True


def _protected_output_identities() -> set[tuple[int, int]]:
    repository_fd = _open_directory_path(
        _LIVE_REPOSITORY_ROOT,
        "live compiler repository root",
    )
    identities: set[tuple[int, int]] = set()
    try:
        for relative in _PROTECTED_OUTPUT_ROOTS:
            protected_fd = _open_relative_directory(
                repository_fd,
                relative.parts,
                f"protected output root {relative.as_posix()}",
            )
            try:
                identities.add(_directory_object_identity(protected_fd))
            except BaseException as primary:
                _close_fd_preserving(
                    protected_fd,
                    f"protected output root {relative.as_posix()}",
                    primary,
                )
                raise
            _close_fd(
                protected_fd,
                f"protected output root {relative.as_posix()}",
            )
    except BaseException as primary:
        _close_fd_preserving(
            repository_fd,
            "live compiler repository root",
            primary,
        )
        raise
    _close_fd(repository_fd, "live compiler repository root")
    if len(identities) != len(_PROTECTED_OUTPUT_ROOTS):
        raise MissionCompileError("protected output roots are not distinct")
    return identities


def _reject_protected_output_parent(parent_fd: int) -> None:
    protected = _protected_output_identities()
    try:
        current = os.dup(parent_fd)
    except OSError as error:
        raise MissionCompileError(
            "output-root parent descriptor cannot be duplicated"
        ) from error
    try:
        while True:
            identity = _directory_object_identity(current)
            if identity in protected:
                raise MissionCompileError(
                    "output root is inside a protected plan/result namespace"
                )
            try:
                ancestor = os.open("..", _DIRECTORY_OPEN_FLAGS, dir_fd=current)
            except OSError as error:
                raise MissionCompileError(
                    "output-root ancestry cannot be authenticated"
                ) from error
            ancestor_identity = _directory_object_identity(ancestor)
            if ancestor_identity == identity:
                _close_fd(ancestor, "output-root filesystem root")
                break
            try:
                _close_fd(current, "output-root ancestry")
            except BaseException as primary:
                _close_fd_preserving(
                    ancestor,
                    "output-root ancestor",
                    primary,
                )
                raise
            current = ancestor
    except BaseException as primary:
        _close_fd_preserving(current, "output-root ancestry", primary)
        raise
    _close_fd(current, "output-root ancestry")


def preflight_output_destination(output_root: Path) -> None:
    """Read-only rejection for existing or authority-protected destinations."""

    parent_fd, root_name, _parent_path = _open_parent_directory(
        output_root,
        "output root",
    )
    try:
        _reject_protected_output_parent(parent_fd)
        try:
            os.stat(root_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise MissionCompileError(
                "output-root destination cannot be authenticated"
            ) from error
        else:
            raise MissionCompileError("output root already exists")
    except BaseException as primary:
        _close_fd_preserving(parent_fd, "output-root parent", primary)
        raise
    _close_fd(parent_fd, "output-root parent")


def publish_compilation_create_exclusive(
    compilation: MissionCompilation, output_root: Path
) -> str:
    """Reject every mission-bank publication at this checkpoint."""

    raise MissionCompileError(
        "mission-bank publication is categorically disabled at this nonauthorizing checkpoint"
    )

    validate_compilation(compilation)
    parent_fd, root_name, parent_path = _open_parent_directory(
        output_root,
        "output root",
    )
    try:
        _reject_protected_output_parent(parent_fd)
        parent_identity = _directory_object_identity(parent_fd)
    except BaseException as primary:
        _close_fd_preserving(parent_fd, "output-root parent", primary)
        raise
    created = False
    root_fd: int | None = None
    try:
        try:
            os.mkdir(root_name, mode=0o755, dir_fd=parent_fd)
        except FileExistsError as error:
            raise MissionCompileError("output root already exists") from error
        except OSError as error:
            raise MissionCompileError("output root cannot be created") from error
        created = True
        os.fsync(parent_fd)
        try:
            root_fd = os.open(
                root_name,
                _DIRECTORY_OPEN_FLAGS,
                dir_fd=parent_fd,
            )
        except OSError as error:
            raise MissionCompileError(
                "created output root cannot be authenticated"
            ) from error
        for artifact in sorted(
            compilation.files,
            key=lambda item: item.relative_path.as_posix(),
        ):
            _write_exclusive(root_fd, artifact.relative_path, artifact.data)
        for artifact in compilation.files:
            if (
                _read_regular_at(
                    root_fd,
                    artifact.relative_path,
                    f"published {artifact.relative_path.as_posix()}",
                )
                != artifact.data
            ):
                raise MissionCompileError("published output verification differs")
        os.fsync(root_fd)
        _close_fd(root_fd, "created output root")
        root_fd = None
        os.fsync(parent_fd)
        _close_fd(parent_fd, "output-root parent")
        parent_fd = -1
    except BaseException as primary:
        secondary_failures: list[tuple[str, BaseException, bool]] = []
        if root_fd is not None:
            try:
                os.close(root_fd)
            except BaseException as close_error:
                secondary_failures.append(
                    ("created output root close also failed", close_error, False)
                )
        if created:
            try:
                rollback_parent, reopened = _usable_rollback_parent(
                    parent_fd,
                    parent_path,
                    parent_identity,
                )
                try:
                    _rollback_created_root(rollback_parent, root_name)
                finally:
                    if reopened:
                        _close_fd(rollback_parent, "output-root rollback parent")
            except BaseException as rollback_error:
                secondary_failures.append(
                    (
                        "publication rollback failed; retained output may remain",
                        rollback_error,
                        True,
                    )
                )
        if parent_fd >= 0:
            try:
                os.close(parent_fd)
            except BaseException as close_error:
                secondary_failures.append(
                    ("output-root parent close also failed", close_error, False)
                )
        if secondary_failures:
            for detail, secondary, annotate in secondary_failures[:-1]:
                _record_secondary_failure(
                    primary,
                    secondary,
                    detail,
                    annotate_message=annotate,
                )
            detail, secondary, annotate = secondary_failures[-1]
            _raise_primary_with_secondary(
                primary,
                secondary,
                detail,
                annotate_message=annotate,
            )
        raise
    return compilation.manifest_sha256


# Intentionally explicit aliases for callers that use compiler/publication terms.
compile_design = compile_mission_banks
publish_mission_banks = publish_compilation_create_exclusive


def default_repository_root() -> Path:
    return Path(os.path.abspath(__file__)).parents[2]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument(
        "--repository-root", type=Path, default=default_repository_root()
    )
    args = parser.parse_args(argv)
    _ = args
    print(
        "mission-bank compiler remains nonauthorizing; no design read or output is permitted",
        file=sys.stderr,
    )
    return 1

    try:
        preflight_output_destination(args.output_root)
        design = load_canonical_design(args.design)
        compilation = compile_mission_banks(args.repository_root, design)
        publish_compilation_create_exclusive(compilation, args.output_root)
    except (MissionCompileError, OSError) as error:
        print(f"mission compilation failed: {error}", file=sys.stderr)
        return 1
    receipt = {
        "authorizes_scientific_execution": False,
        "manifest_sha256": compilation.manifest_sha256,
        "output_root": str(args.output_root),
        "published_files": len(compilation.files),
        "schema_version": "dancers-headless-mission-publication-receipt-v1",
        "solver_processes_launched": 0,
        "validation_artifacts_read": False,
    }
    sys.stdout.buffer.write(canonical_json_bytes(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
