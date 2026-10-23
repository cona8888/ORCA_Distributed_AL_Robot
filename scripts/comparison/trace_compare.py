#!/usr/bin/env python3
"""Bounded canonicalizer/comparator for DANCeRS comparison trace JSONL.

Only provenance metadata and the three resolved worktree-dependent paths are
normalized.  Every frame remains semantic and is compared recursively with
strict JSON type/value identity.  Trace v2 remains a strict historical read
path; new common-observer traces use v3.  Mixed-version pairs are rejected.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Sequence


TRACE_SCHEMA_V2 = "dancers-comparison-trace-v2"
TRACE_SCHEMA_V3 = "dancers-comparison-trace-v3"
SCHEMA_VERSION = TRACE_SCHEMA_V3
SUPPORTED_SCHEMA_VERSIONS = (TRACE_SCHEMA_V2, TRACE_SCHEMA_V3)
DIGEST_CONTRACTS = {
    TRACE_SCHEMA_V2: {
        "state_schema": "robot-frame-v1",
        "digest_algorithm": (
            "SHA-256 over canonical nlohmann JSON serialization of each "
            "implementation-neutral robot frame"
        ),
    },
    TRACE_SCHEMA_V3: {
        "state_schema": "robot-semantic-digest-v2",
        "digest_algorithm": (
            "SHA-256 over robot-semantic-digest-v2 canonical typed binary "
            "encoding"
        ),
    },
}
V3_REQUIRED_SCOPE_FIELDS = (
    "future_state_scope",
    "message_checksum_scope",
)
DIGEST_ROBOT_KEYS = frozenset(("rid", "state_schema", "state_sha256"))
ALLOWED_PHASES = ("pre_update", "post_update")
ALLOWED_TRACE_DETAILS = ("digest", "full", "positions")
PATH_FIELDS = ("config_file", "scenario_file", "obstacle_file")
PATH_ANCHORS = {
    "config_file": ("config",),
    "scenario_file": ("comparison", "config", "assets"),
    "obstacle_file": ("comparison", "assets", "config"),
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
JSON_NUMBER_TOKEN_RE = re.compile(
    rb"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"
)


@dataclass(frozen=True)
class TraceLimits:
    max_file_bytes: int = 512 * 1024 * 1024
    max_line_bytes: int = 64 * 1024 * 1024
    max_frames: int = 1_000_000
    max_robots_per_frame: int = 100_000
    max_json_depth: int = 128

    def __post_init__(self) -> None:
        for name, value in self.__dict__.items():
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")


DEFAULT_LIMITS = TraceLimits()


class TraceFormatError(ValueError):
    def __init__(
        self,
        source: Path,
        message: str,
        *,
        line: int | None = None,
        tick: int | None = None,
        phase: str | None = None,
        path: str | None = None,
    ) -> None:
        self.source = source
        self.line = line
        self.tick = tick
        self.phase = phase
        self.path = path
        location = str(source)
        if line is not None:
            location += f":{line}"
        context = []
        if tick is not None:
            context.append(f"tick={tick}")
        if phase is not None:
            context.append(f"phase={phase}")
        if path is not None:
            context.append(f"path={path}")
        suffix = f" ({', '.join(context)})" if context else ""
        super().__init__(f"{location}: {message}{suffix}")


@dataclass(frozen=True)
class TraceHeader:
    schema_version: str
    provenance: dict[str, Any]
    canonical: dict[str, Any]
    trace_detail: str


@dataclass(frozen=True)
class Mismatch:
    tick: int | None
    phase: str
    path: str
    reason: str
    left: Any
    right: Any

    def format(self) -> str:
        tick = "header" if self.tick is None else str(self.tick)
        return (
            f"first mismatch: tick={tick} phase={self.phase} "
            f"path={self.path}: {self.reason}; "
            f"left={_bounded_repr(self.left)} "
            f"right={_bounded_repr(self.right)}"
        )


@dataclass(frozen=True)
class ComparisonResult:
    equal: bool
    frames_compared: int
    left_provenance: dict[str, Any]
    right_provenance: dict[str, Any]
    mismatch: Mismatch | None = None


@dataclass(frozen=True)
class CanonicalizationResult:
    frame_count: int
    semantic_sha256: str
    provenance: dict[str, Any]


class _DuplicateKey(ValueError):
    pass


class PortableTraceJsonError(ValueError):
    """A trace record violates the language-independent lexical contract."""


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(f"duplicate object key {key!r}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value!r}")


def _canonical_ascii_json_line(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _mask_json_number_tokens(raw: bytes) -> bytes:
    """Replace JSON number lexemes outside strings with one neutral token."""

    masked = bytearray()
    index = 0
    in_string = False
    escaped = False
    while index < len(raw):
        byte = raw[index]
        if in_string:
            masked.append(byte)
            if escaped:
                escaped = False
            elif byte == ord("\\"):
                escaped = True
            elif byte == ord('"'):
                in_string = False
            index += 1
            continue
        if byte == ord('"'):
            in_string = True
            masked.append(byte)
            index += 1
            continue
        if byte == ord("-") or ord("0") <= byte <= ord("9"):
            match = JSON_NUMBER_TOKEN_RE.match(raw, index)
            if match is None:
                raise PortableTraceJsonError("invalid JSON number token")
            masked.extend(b"0")
            index = match.end()
            continue
        masked.append(byte)
        index += 1
    if in_string or escaped:
        raise PortableTraceJsonError("unterminated JSON string")
    return bytes(masked)


def parse_portable_trace_json_line(raw: bytes) -> Any:
    """Parse one compact trace line while ignoring finite decimal spelling.

    Object-key order, strings, punctuation, literals, UTF-8/ASCII escaping and
    LF framing remain canonical. Valid finite JSON number tokens may use any
    spelling because independently implemented serializers can choose different
    shortest round-trip decimals for the same IEEE-754 value.
    """

    if not isinstance(raw, bytes) or not raw or not raw.endswith(b"\n"):
        raise PortableTraceJsonError("trace JSON must be a nonempty LF-terminated line")
    try:
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_no_duplicate_object,
            parse_constant=_reject_json_constant,
        )
        # JSON permits arbitrarily large exponents, while Python materializes
        # some of them as infinity. The portable trace contract remains finite.
        _canonical_ascii_json_line(value)
        masked = _mask_json_number_tokens(raw)
        masked_value = json.loads(
            masked.decode("utf-8"),
            object_pairs_hook=_no_duplicate_object,
            parse_constant=_reject_json_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateKey,
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
    ) as error:
        raise PortableTraceJsonError(f"invalid strict finite JSON: {error}") from error
    if masked != _canonical_ascii_json_line(masked_value):
        raise PortableTraceJsonError(
            "trace JSON is not compact with sorted keys and canonical nonnumeric tokens"
        )
    return value


def _json_depth(value: Any) -> int:
    if isinstance(value, dict):
        return 1 + max((_json_depth(item) for item in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_json_depth(item) for item in value), default=0)
    return 1


def _is_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _path_component(path: str, key: str) -> str:
    if IDENTIFIER_RE.fullmatch(key):
        return f"{path}.{key}"
    return f"{path}[{json.dumps(key, ensure_ascii=True)}]"


def normalize_worktree_path(value: str, field: str) -> str:
    """Normalize a repo path while retaining its environment-identifying suffix."""
    if not isinstance(value, str):
        raise TypeError(f"resolved_config.{field} must be a string")
    if not value:
        return value
    text = value.replace("\\", "/")
    drive = ""
    if len(text) >= 2 and text[1] == ":":
        drive, text = text[:2], text[2:]
    absolute = text.startswith("/") or bool(drive)
    stack: list[str] = []
    for component in text.split("/"):
        if component in ("", "."):
            continue
        if component == "..":
            if stack and stack[-1] != "..":
                stack.pop()
            elif not absolute:
                stack.append(component)
        else:
            stack.append(component)

    anchor_indexes = [
        index
        for index, component in enumerate(stack)
        if component in PATH_ANCHORS[field]
    ]
    if anchor_indexes:
        stack = stack[max(anchor_indexes) :]
        return "/".join(stack)
    if absolute:
        # No repository anchor means the prefix cannot safely be identified as
        # worktree-only. Preserve it so unrelated absolute inputs do not compare
        # equal merely because their basenames match.
        prefix = f"{drive}/" if drive else "/"
        return prefix + "/".join(stack)
    return "/".join(stack)


def canonicalize_header(header: dict[str, Any], source: Path) -> TraceHeader:
    if not isinstance(header, dict):
        raise TraceFormatError(source, "header record must be a JSON object", line=1)
    if header.get("type") != "header":
        raise TraceFormatError(
            source, "first record must have type='header'", line=1, path="$.type"
        )
    schema_version = header.get("schema_version")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise TraceFormatError(
            source,
            "unsupported schema_version; expected one of "
            f"{SUPPORTED_SCHEMA_VERSIONS!r}",
            line=1,
            path="$.schema_version",
        )
    assert isinstance(schema_version, str)
    metadata = header.get("metadata")
    if not isinstance(metadata, dict):
        raise TraceFormatError(
            source, "header metadata must be an object", line=1, path="$.metadata"
        )
    resolved = header.get("resolved_config")
    if not isinstance(resolved, dict):
        raise TraceFormatError(
            source,
            "header resolved_config must be an object",
            line=1,
            path="$.resolved_config",
        )
    observer = header.get("observer")
    if not isinstance(observer, dict):
        raise TraceFormatError(
            source, "header observer must be an object", line=1, path="$.observer"
        )
    trace_detail = observer.get("trace_detail")
    if trace_detail not in ALLOWED_TRACE_DETAILS:
        raise TraceFormatError(
            source,
            f"observer.trace_detail must be one of {ALLOWED_TRACE_DETAILS}",
            line=1,
            path="$.observer.trace_detail",
        )
    digest_contract = DIGEST_CONTRACTS[schema_version]
    digest_algorithm = observer.get("digest_algorithm")
    if digest_algorithm != digest_contract["digest_algorithm"]:
        raise TraceFormatError(
            source,
            "observer.digest_algorithm does not match the exact "
            f"{schema_version} contract",
            line=1,
            path="$.observer.digest_algorithm",
        )
    if schema_version == TRACE_SCHEMA_V3:
        for field in V3_REQUIRED_SCOPE_FIELDS:
            value = observer.get(field)
            if not isinstance(value, str) or not value.strip():
                raise TraceFormatError(
                    source,
                    f"observer.{field} must be a non-empty string for "
                    f"{TRACE_SCHEMA_V3}",
                    line=1,
                    path=f"$.observer.{field}",
                )

    canonical = copy.deepcopy(header)
    canonical["metadata"] = {}
    canonical_resolved = canonical["resolved_config"]
    for field in PATH_FIELDS:
        if field not in canonical_resolved:
            raise TraceFormatError(
                source,
                f"resolved_config is missing {field!r}",
                line=1,
                path=f"$.resolved_config.{field}",
            )
        try:
            canonical_resolved[field] = normalize_worktree_path(
                canonical_resolved[field], field
            )
        except TypeError as error:
            raise TraceFormatError(
                source,
                str(error),
                line=1,
                path=f"$.resolved_config.{field}",
            ) from error
    return TraceHeader(
        schema_version=schema_version,
        provenance=copy.deepcopy(metadata),
        canonical=canonical,
        trace_detail=trace_detail,
    )


class TraceReader:
    """Streaming v2/v3 JSONL reader with explicit file/line/frame bounds."""

    def __init__(
        self, source: Path | str, limits: TraceLimits = DEFAULT_LIMITS
    ) -> None:
        self.source = Path(source)
        self.limits = limits
        try:
            file_size = self.source.stat().st_size
        except OSError as error:
            raise TraceFormatError(self.source, f"cannot stat trace: {error}") from error
        if file_size > limits.max_file_bytes:
            raise TraceFormatError(
                self.source,
                f"trace is {file_size} bytes; limit is {limits.max_file_bytes}",
            )
        try:
            self._stream: BinaryIO = self.source.open("rb")
        except OSError as error:
            raise TraceFormatError(self.source, f"cannot open trace: {error}") from error
        self._line_number = 0
        self._bytes_read = 0
        self._frames_read = 0
        self._closed = False
        self._completed = False
        self._expected_phase = "pre_update"
        self._expected_tick = 0
        self._phase_keys: set[tuple[int, str]] = set()
        try:
            raw_header = self._read_record()
            if raw_header is None:
                raise TraceFormatError(self.source, "trace is empty")
            self.header = canonicalize_header(raw_header, self.source)
        except Exception:
            self.close()
            raise

    def __enter__(self) -> "TraceReader":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._stream.close()
            self._closed = True

    def _read_record(self) -> dict[str, Any] | None:
        raw = self._stream.readline(self.limits.max_line_bytes + 1)
        if not raw:
            return None
        self._line_number += 1
        self._bytes_read += len(raw)
        if self._bytes_read > self.limits.max_file_bytes:
            raise TraceFormatError(
                self.source,
                f"trace exceeds {self.limits.max_file_bytes}-byte limit",
                line=self._line_number,
            )
        if len(raw) > self.limits.max_line_bytes:
            raise TraceFormatError(
                self.source,
                f"JSONL record exceeds {self.limits.max_line_bytes}-byte limit",
                line=self._line_number,
            )
        if not raw.endswith(b"\n"):
            extra = self._stream.read(1)
            if extra:
                raise TraceFormatError(
                    self.source,
                    f"JSONL record exceeds {self.limits.max_line_bytes}-byte limit",
                    line=self._line_number,
                )
        stripped = raw.strip()
        if not stripped:
            raise TraceFormatError(
                self.source, "blank JSONL record", line=self._line_number
            )
        try:
            value = parse_portable_trace_json_line(raw)
        except (PortableTraceJsonError, RecursionError) as error:
            raise TraceFormatError(
                self.source,
                f"malformed JSON: {error}",
                line=self._line_number,
            ) from error
        if not isinstance(value, dict):
            raise TraceFormatError(
                self.source,
                "each JSONL record must be an object",
                line=self._line_number,
            )
        try:
            depth = _json_depth(value)
        except RecursionError as error:
            raise TraceFormatError(
                self.source,
                "record exceeds JSON nesting supported by the reader",
                line=self._line_number,
            ) from error
        if depth > self.limits.max_json_depth:
            raise TraceFormatError(
                self.source,
                f"record exceeds JSON depth limit {self.limits.max_json_depth}",
                line=self._line_number,
            )
        return value

    def _validate_digest_robots(
        self, robots: list[Any], tick: int, phase: str
    ) -> None:
        seen: set[int] = set()
        for index, robot in enumerate(robots):
            path = f"$.robots[{index}]"
            if not isinstance(robot, dict):
                raise TraceFormatError(
                    self.source,
                    "robot record must be an object",
                    line=self._line_number,
                    tick=tick,
                    phase=phase,
                    path=path,
                )
            rid = robot.get("rid")
            if not _is_integer(rid) or rid < 0:
                raise TraceFormatError(
                    self.source,
                    "robot rid must be a non-negative integer",
                    line=self._line_number,
                    tick=tick,
                    phase=phase,
                    path=f"{path}.rid",
                )
            if rid in seen:
                raise TraceFormatError(
                    self.source,
                    f"duplicate robot rid {rid}",
                    line=self._line_number,
                    tick=tick,
                    phase=phase,
                    path=f"{path}.rid",
                )
            seen.add(rid)
            if self.header.trace_detail == "digest":
                if set(robot) != DIGEST_ROBOT_KEYS:
                    missing = sorted(DIGEST_ROBOT_KEYS - set(robot))
                    extra = sorted(set(robot) - DIGEST_ROBOT_KEYS)
                    raise TraceFormatError(
                        self.source,
                        "digest robot keys do not match the exact contract; "
                        f"missing={missing} extra={extra}",
                        line=self._line_number,
                        tick=tick,
                        phase=phase,
                        path=path,
                    )
                expected_state_schema = DIGEST_CONTRACTS[
                    self.header.schema_version
                ]["state_schema"]
                if robot.get("state_schema") != expected_state_schema:
                    raise TraceFormatError(
                        self.source,
                        "digest robot state_schema must be "
                        f"{expected_state_schema!r} for "
                        f"{self.header.schema_version}",
                        line=self._line_number,
                        tick=tick,
                        phase=phase,
                        path=f"{path}.state_schema",
                    )
                digest = robot.get("state_sha256")
                if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
                    raise TraceFormatError(
                        self.source,
                        "digest robot state_sha256 must be 64 lower-case hex digits",
                        line=self._line_number,
                        tick=tick,
                        phase=phase,
                        path=f"{path}.state_sha256",
                    )

    def _validate_frame(self, frame: dict[str, Any]) -> None:
        if frame.get("type") != "frame":
            raise TraceFormatError(
                self.source,
                "records after the header must have type='frame'",
                line=self._line_number,
                path="$.type",
            )
        tick = frame.get("tick")
        phase = frame.get("phase")
        if not _is_integer(tick) or tick < 0:
            raise TraceFormatError(
                self.source,
                "frame tick must be a non-negative integer",
                line=self._line_number,
                path="$.tick",
            )
        if phase not in ALLOWED_PHASES:
            raise TraceFormatError(
                self.source,
                f"frame phase must be one of {ALLOWED_PHASES}",
                line=self._line_number,
                tick=tick,
                path="$.phase",
            )
        phase_key = (tick, phase)
        if phase_key in self._phase_keys:
            raise TraceFormatError(
                self.source,
                "duplicate tick/phase frame",
                line=self._line_number,
                tick=tick,
                phase=phase,
                path="$.phase",
            )
        self._phase_keys.add(phase_key)
        if phase != self._expected_phase or tick != self._expected_tick:
            raise TraceFormatError(
                self.source,
                f"missing or out-of-order phase; expected "
                f"tick={self._expected_tick} phase={self._expected_phase}",
                line=self._line_number,
                tick=tick,
                phase=phase,
                path="$.phase",
            )
        if phase == "pre_update":
            self._expected_phase = "post_update"
            self._expected_tick = tick + 1
        else:
            self._expected_phase = "pre_update"
            self._expected_tick = tick

        robots = frame.get("robots")
        if not isinstance(robots, list):
            raise TraceFormatError(
                self.source,
                "frame robots must be an array",
                line=self._line_number,
                tick=tick,
                phase=phase,
                path="$.robots",
            )
        if len(robots) > self.limits.max_robots_per_frame:
            raise TraceFormatError(
                self.source,
                f"frame has {len(robots)} robots; limit is "
                f"{self.limits.max_robots_per_frame}",
                line=self._line_number,
                tick=tick,
                phase=phase,
                path="$.robots",
            )
        self._validate_digest_robots(robots, tick, phase)

    def frames(self) -> Iterator[dict[str, Any]]:
        if self._completed:
            raise RuntimeError("trace frame iterator may only be consumed once")
        try:
            while True:
                frame = self._read_record()
                if frame is None:
                    break
                self._frames_read += 1
                if self._frames_read > self.limits.max_frames:
                    raise TraceFormatError(
                        self.source,
                        f"trace exceeds {self.limits.max_frames}-frame limit",
                        line=self._line_number,
                    )
                self._validate_frame(frame)
                yield frame
            if self._frames_read == 0:
                raise TraceFormatError(self.source, "trace contains no frames")
            if self._expected_phase == "post_update":
                raise TraceFormatError(
                    self.source,
                    f"missing terminal phase; expected "
                    f"tick={self._expected_tick} phase=post_update",
                    line=self._line_number,
                    tick=self._expected_tick,
                    phase="post_update",
                    path="$.phase",
                )
            self._completed = True
        finally:
            self.close()


def _same_json_scalar(left: Any, right: Any) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, float):
        if not math.isfinite(left) or not math.isfinite(right):
            return False
        if left == 0.0 and right == 0.0:
            return math.copysign(1.0, left) == math.copysign(1.0, right)
    return left == right


def first_json_difference(
    left: Any, right: Any, path: str = "$"
) -> tuple[str, str, Any, Any] | None:
    if type(left) is not type(right):
        return path, "JSON types differ", left, right
    if isinstance(left, dict):
        left_keys = set(left)
        right_keys = set(right)
        for key in sorted(left_keys | right_keys):
            child_path = _path_component(path, key)
            if key not in left:
                return child_path, "key is missing on the left", None, right[key]
            if key not in right:
                return child_path, "key is missing on the right", left[key], None
            difference = first_json_difference(
                left[key], right[key], child_path
            )
            if difference is not None:
                return difference
        return None
    if isinstance(left, list):
        common = min(len(left), len(right))
        for index in range(common):
            difference = first_json_difference(
                left[index], right[index], f"{path}[{index}]"
            )
            if difference is not None:
                return difference
        if len(left) != len(right):
            index = common
            return (
                f"{path}[{index}]",
                "array lengths differ",
                None if index >= len(left) else left[index],
                None if index >= len(right) else right[index],
            )
        return None
    if not _same_json_scalar(left, right):
        return path, "values differ", left, right
    return None


_MISSING_FRAME = object()


def compare_traces(
    left_path: Path | str,
    right_path: Path | str,
    limits: TraceLimits = DEFAULT_LIMITS,
) -> ComparisonResult:
    with TraceReader(left_path, limits) as left, TraceReader(
        right_path, limits
    ) as right:
        if left.header.schema_version != right.header.schema_version:
            raise TraceFormatError(
                right.source,
                "mixed trace schema versions are not comparable: "
                f"left={left.header.schema_version!r}, "
                f"right={right.header.schema_version!r}",
                line=1,
                path="$.schema_version",
            )
        header_difference = first_json_difference(
            left.header.canonical, right.header.canonical, "$.header"
        )
        first_mismatch: Mismatch | None = None
        if header_difference is not None:
            path, reason, left_value, right_value = header_difference
            first_mismatch = Mismatch(
                tick=None,
                phase="header",
                path=path,
                reason=reason,
                left=left_value,
                right=right_value,
            )

        frames_compared = 0
        for left_frame, right_frame in zip_longest(
            left.frames(), right.frames(), fillvalue=_MISSING_FRAME
        ):
            # Continue consuming both iterators after finding a mismatch. This
            # ensures malformed or incomplete trailing phase sequences are
            # rejected instead of being hidden behind an earlier difference.
            if first_mismatch is not None:
                continue
            if (
                left_frame is _MISSING_FRAME
                or right_frame is _MISSING_FRAME
            ):
                present = (
                    right_frame
                    if left_frame is _MISSING_FRAME
                    else left_frame
                )
                tick = present.get("tick") if isinstance(present, dict) else None
                phase = (
                    present.get("phase", "unknown")
                    if isinstance(present, dict)
                    else "unknown"
                )
                first_mismatch = Mismatch(
                    tick=tick if _is_integer(tick) else None,
                    phase=phase if isinstance(phase, str) else "unknown",
                    path="$",
                    reason="frame is missing from one trace",
                    left=(
                        "<missing>"
                        if left_frame is _MISSING_FRAME
                        else "<frame present>"
                    ),
                    right=(
                        "<missing>"
                        if right_frame is _MISSING_FRAME
                        else "<frame present>"
                    ),
                )
                continue
            difference = first_json_difference(left_frame, right_frame)
            if difference is not None:
                path, reason, left_value, right_value = difference
                first_mismatch = Mismatch(
                    tick=left_frame["tick"],
                    phase=left_frame["phase"],
                    path=path,
                    reason=reason,
                    left=left_value,
                    right=right_value,
                )
            else:
                frames_compared += 1

        return ComparisonResult(
            equal=first_mismatch is None,
            frames_compared=frames_compared,
            left_provenance=left.header.provenance,
            right_provenance=right.header.provenance,
            mismatch=first_mismatch,
        )


def _canonical_line(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def canonicalize_trace(
    source_path: Path | str,
    destination_path: Path | str,
    provenance_path: Path | str | None = None,
    limits: TraceLimits = DEFAULT_LIMITS,
) -> CanonicalizationResult:
    source = Path(source_path)
    destination = Path(destination_path)
    provenance_destination = (
        Path(provenance_path)
        if provenance_path is not None
        else destination.with_name(destination.name + ".provenance.json")
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    provenance_destination.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.sha256()
    frame_count = 0
    with TraceReader(source, limits) as reader, destination.open("wb") as output:
        header_line = _canonical_line(reader.header.canonical)
        output.write(header_line)
        hasher.update(header_line)
        for frame in reader.frames():
            line = _canonical_line(frame)
            output.write(line)
            hasher.update(line)
            frame_count += 1
        provenance_destination.write_bytes(
            _canonical_line(reader.header.provenance)
        )
        provenance = reader.header.provenance
    return CanonicalizationResult(
        frame_count=frame_count,
        semantic_sha256=hasher.hexdigest(),
        provenance=provenance,
    )


def _bounded_repr(value: Any, limit: int = 240) -> str:
    try:
        text = json.dumps(
            value, sort_keys=True, ensure_ascii=True, allow_nan=False
        )
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _comparison_json(result: ComparisonResult) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "equal": result.equal,
        "frames_compared": result.frames_compared,
        "left_provenance": result.left_provenance,
        "right_provenance": result.right_provenance,
    }
    if result.mismatch is not None:
        payload["mismatch"] = {
            "tick": result.mismatch.tick,
            "phase": result.mismatch.phase,
            "path": result.mismatch.path,
            "reason": result.mismatch.reason,
            "left": result.mismatch.left,
            "right": result.mismatch.right,
        }
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    compare_parser = subparsers.add_parser(
        "compare", help="compare two same-version trace-v2/v3 JSONL artifacts"
    )
    compare_parser.add_argument("left", type=Path)
    compare_parser.add_argument("right", type=Path)
    compare_parser.add_argument(
        "--json", action="store_true", help="emit a machine-readable result"
    )

    canonical_parser = subparsers.add_parser(
        "canonicalize",
        help="write semantic JSONL and provenance as separate artifacts",
    )
    canonical_parser.add_argument("source", type=Path)
    canonical_parser.add_argument("destination", type=Path)
    canonical_parser.add_argument("--provenance-out", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "compare":
            result = compare_traces(args.left, args.right)
            if args.json:
                print(json.dumps(_comparison_json(result), sort_keys=True))
            elif result.equal:
                print(
                    f"semantic traces match exactly "
                    f"({result.frames_compared} frames)"
                )
            else:
                assert result.mismatch is not None
                print(result.mismatch.format())
            return 0 if result.equal else 1

        result = canonicalize_trace(
            args.source,
            args.destination,
            provenance_path=args.provenance_out,
        )
        print(
            f"canonicalized {result.frame_count} frames; "
            f"semantic_sha256={result.semantic_sha256}"
        )
        return 0
    except TraceFormatError as error:
        print(f"invalid trace: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"I/O error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
