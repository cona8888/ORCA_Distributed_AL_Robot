#!/usr/bin/env python3
"""Build a bounded, verifier-derived bundle for the offline physics viewer.

This module is deliberately a data projection, not a runner.  It imports the
strict evidence verifier and comparator, rejects digest projections, and never
starts either simulator product.  Exact binary64 words remain beside decoded
viewer values so signed zero and every projected physics leaf survive JSON
transport.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import json
import math
import os
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

try:
    from compare_physics_evidence_v1 import (
        EvidencePairError,
        compare_verified_traces,
    )
    from physics_evidence_v1 import (
        ENCODING,
        EvidenceFormatError,
        EvidenceTrace,
        f64_from_bits,
        parse_physics_evidence_v1,
        read_verified_event,
    )
except ImportError:  # pragma: no cover - package-style import convenience
    from .compare_physics_evidence_v1 import (
        EvidencePairError,
        compare_verified_traces,
    )
    from .physics_evidence_v1 import (
        ENCODING,
        EvidenceFormatError,
        EvidenceTrace,
        f64_from_bits,
        parse_physics_evidence_v1,
        read_verified_event,
    )


BUNDLE_SCHEMA = "dancers-planar-physics-replay-bundle-v1"
PACKED_OCCUPANCY_ENCODING = "row-major-msb-packed-occupied-1-v1"
HARD_MAX_FRAMES = 100_001
HARD_MAX_BUNDLE_BYTES = 64 * 1024 * 1024
MAX_IDENTITY_FILE_BYTES = 512 * 1024 * 1024
EXPECTED_OCCUPANCY_BYTES = 1_000_000
EXPECTED_PACKED_OCCUPANCY_BYTES = EXPECTED_OCCUPANCY_BYTES // 8
OFFICIAL_RESULT_RELATIVE = Path(
    "results/simulator-demo/v1/physics-stress-test/epoch-1"
)


class ReplayBundleError(ValueError):
    """Verified evidence cannot be safely projected or published."""


class _DuplicateKey(ValueError):
    pass


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(f"duplicate object key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is forbidden: {value!r}")


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=False,
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ReplayBundleError(f"viewer bundle is not canonical finite JSON: {error}") from error


def _bounded_positive(value: int, *, name: str, hard_maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= hard_maximum
    ):
        raise ReplayBundleError(f"{name} must be in 1..{hard_maximum}")
    return value


def _display_label(value: str, *, name: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > 80
    ):
        raise ReplayBundleError(
            f"{name} must be a trimmed nonempty display string of at most 80 characters"
        )
    if any(ord(character) < 0x20 for character in value):
        raise ReplayBundleError(f"{name} contains a control character")
    return value


def _decoded(words: Sequence[str]) -> list[float]:
    return [f64_from_bits(word) for word in words]


def _decoded_settings(settings: Mapping[str, Any]) -> dict[str, float | int]:
    result: dict[str, float | int] = {}
    for key, value in settings.items():
        result[key] = f64_from_bits(value) if isinstance(value, str) else value
    return result


def _segment_payload(segment: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": segment["id"],
        "a_bits": list(segment["a"]),
        "a": _decoded(segment["a"]),
        "b_bits": list(segment["b"]),
        "b": _decoded(segment["b"]),
        "free_normal_bits": list(segment["free_normal"]),
        "free_normal": _decoded(segment["free_normal"]),
    }


def _mission_payload(mission: Mapping[str, Any]) -> dict[str, Any]:
    initial_bits = list(mission["authenticated_initial_r6_before_zero_normalization"])
    return {
        "id": mission["id"],
        "route_upper_length_mm": mission["route_upper_length_mm"],
        "authenticated_initial_r6_bits": initial_bits,
        "authenticated_initial_r6": _decoded(initial_bits),
        "waypoints": [
            {"xy_bits": list(point), "xy": _decoded(point)}
            for point in mission["waypoints"]
        ],
    }


def _body_payload(frame: Mapping[str, Any], sha256: str) -> dict[str, Any]:
    return {
        "sha256": sha256,
        "bodies": [
            {
                "id": body["id"],
                "r6_bits": list(body["r6"]),
                "r6": _decoded(body["r6"]),
            }
            for body in frame["bodies"]
        ],
    }


def _command_robot_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    planner_raw_bits = list(record["planner_raw_requested_pose"])
    planner_effective_bits = list(record["planner_effective_requested_pose"])
    requested_bits = list(record["adapter_requested_pose"])
    raw_bits = list(record["adapter_raw_velocity_command"])
    clamped_bits = list(record["adapter_clamped_velocity_command"])
    return {
        "id": record["id"],
        "planner_raw_requested_pose_bits": planner_raw_bits,
        "planner_raw_requested_pose": _decoded(planner_raw_bits),
        "planner_effective_requested_pose_bits": planner_effective_bits,
        "planner_effective_requested_pose": _decoded(planner_effective_bits),
        "requested_pose_bits": requested_bits,
        "requested_pose": _decoded(requested_bits),
        "raw_velocity_bits": raw_bits,
        "raw_velocity": _decoded(raw_bits),
        "clamped_velocity_bits": clamped_bits,
        "clamped_velocity": _decoded(clamped_bits),
        "waypoint_advanced": record["waypoint_advanced"],
        "final_goal_reached": record["final_goal_reached"],
        "remaining_waypoint_count": record["remaining_waypoint_count"],
    }


def _command_payload(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "tick": event["tick"],
        "time_ms": event["simulated_time_ms"],
        "sha256": event["command_frame_sha256"],
        "robots": [_command_robot_payload(record) for record in event["records"]],
    }


def _contact_payload(contact: Mapping[str, Any]) -> dict[str, Any]:
    normal_bits = list(contact["normal_applied_to_first"])
    point_bits = list(contact["point"])
    scalar_names = (
        "signed_clearance_before_m",
        "penetration_before_m",
        "residual_penetration_m",
        "final_normal_impulse_n_s",
        "final_tangent_impulse_n_s",
    )
    result: dict[str, Any] = {
        "kind": contact["kind"],
        "first": contact["first"],
        "second": contact["second"],
        "normal_applied_to_first_bits": normal_bits,
        "normal_applied_to_first": _decoded(normal_bits),
        "point_bits": point_bits,
        "point": _decoded(point_bits),
    }
    for name in scalar_names:
        bits = contact[name]
        result[f"{name}_bits"] = bits
        result[name] = f64_from_bits(bits)
    return result


def _contacts_payload(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "available": True,
        "sha256": event["contact_frame_sha256"],
        "contacts": [
            _contact_payload(contact)
            for contact in event["contact_frame"]["contacts"]
        ],
    }


def _empty_contacts_payload() -> dict[str, Any]:
    return {"available": False, "sha256": None, "contacts": []}


def _clearance_payload(frame: Mapping[str, Any], sha256: str) -> dict[str, Any]:
    raw = frame["clearance"]
    result: dict[str, Any] = {"sha256": sha256}
    for name in (
        "minimum_pair_clearance_m",
        "minimum_obstacle_clearance_m",
    ):
        bits = raw[name]
        result[f"{name}_bits"] = bits
        result[name] = None if bits is None else f64_from_bits(bits)
    return result


def _route_robot_payload(robot: Mapping[str, Any]) -> dict[str, Any]:
    result = {
        "id": robot["id"],
        "current_route_index": robot["current_route_index"],
        "remaining_waypoint_count": robot["remaining_waypoint_count"],
        "waypoint_advanced": robot["waypoint_advanced"],
        "route_cursor_sha256": robot["route_cursor_sha256"],
        "observed_goal_controller_tick": robot["observed_goal_controller_tick"],
        "strict_goal_before_horizon": robot["strict_goal_before_horizon"],
    }
    for source_name, output_name in (
        ("next_waypoint", "next_waypoint"),
        ("final_goal", "final_goal"),
    ):
        point = robot[source_name]
        result[f"{output_name}_bits"] = None if point is None else list(point)
        result[output_name] = None if point is None else _decoded(point)
    return result


def _boundary_payload(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "boundary": event["boundary"],
        "time_ms": event["simulated_time_ms"],
        "mission_sha256": event["mission_frame_sha256"],
        "robots": [
            _route_robot_payload(robot) for robot in event["mission"]["robots"]
        ],
    }


class _ByteBudget:
    def __init__(self, maximum: int) -> None:
        self.maximum = maximum
        self.projected = 0

    def add(self, value: Any, *, label: str) -> None:
        self.projected += len(_canonical_bytes(value))
        if self.projected > self.maximum:
            raise ReplayBundleError(
                f"viewer bundle exceeds the {self.maximum}-byte laptop ceiling while adding {label}; no frames were downsampled"
            )


def _trace_verification(trace: EvidenceTrace) -> dict[str, Any]:
    return {
        "detail": trace.detail,
        "verification_level": trace.verification_level,
        "source_file_sha256": trace.file_sha256,
        "source_bytes": trace.byte_count,
        "initialization_semantic_sha256": trace.initialization_semantic_sha256,
        "common_scene_semantic_sha256": trace.common_scene_semantic_sha256,
        "mission_terminal_frame_sha256": trace.mission_terminal_frame_sha256,
        "domain_digests": dict(trace.domain_digests),
        "scientific_evidence": False,
        "execution_plan_bound": False,
        "official_result_namespace": False,
        "method_identity_authenticated": False,
    }


def _run_payload(
    trace: EvidenceTrace,
    *,
    display_label: str,
    budget: _ByteBudget,
) -> dict[str, Any]:
    commands: list[dict[str, Any]] = []
    commands_by_tick: dict[int, dict[str, Any]] = {}
    boundaries: list[dict[str, Any]] = []
    frames: list[dict[str, Any]] = []

    for event_index in range(len(trace.event_locators)):
        event = read_verified_event(trace, event_index)
        kind = event["event"]
        if kind == "commands":
            command = _command_payload(event)
            commands.append(command)
            commands_by_tick[event["tick"]] = command
            budget.add(command, label=f"{display_label} command tick {event['tick']}")
            continue
        if kind == "boundary":
            boundary = _boundary_payload(event)
            boundaries.append(boundary)
            budget.add(boundary, label=f"{display_label} boundary {event['boundary']}")
            if event["boundary"] == 0:
                frame = {
                    "frame_index": 0,
                    "time_ms": 0,
                    "tick": None,
                    "substep": None,
                    "body_state": _body_payload(
                        event["body_state_frame"], event["body_frame_sha256"]
                    ),
                    "held_command_tick": None,
                    "contacts": _empty_contacts_payload(),
                    "clearance": _clearance_payload(
                        event["clearance_frame"], event["clearance_frame_sha256"]
                    ),
                    "route_boundary_index": 0,
                }
                frames.append(frame)
                budget.add(frame, label=f"{display_label} frame 0")
            continue
        if kind != "substep":
            raise ReplayBundleError(f"verified locator has unsupported event {kind!r}")
        tick = event["tick"]
        if tick not in commands_by_tick:
            raise ReplayBundleError("verified substep has no preceding held command")
        frame = {
            "frame_index": event["engine_step"],
            "time_ms": event["simulated_time_ms"],
            "tick": tick,
            "substep": event["substep"],
            "body_state": _body_payload(
                event["body_state_frame"], event["body_frame_sha256"]
            ),
            # The exact command frame is stored once in command_frames.  This
            # tick reference expresses the ten-substep zero-order hold without
            # duplicating O(10 * H * N) command leaves in a laptop bundle.
            "held_command_tick": tick,
            "contacts": _contacts_payload(event),
            "clearance": _clearance_payload(
                event["clearance_frame"], event["clearance_frame_sha256"]
            ),
            "route_boundary_index": event["engine_step"] // 10,
        }
        frames.append(frame)
        budget.add(frame, label=f"{display_label} frame {event['engine_step']}")

    if len(frames) != trace.physics_steps + 1:
        raise ReplayBundleError("verified replay did not project exactly J+1 body frames")
    if len(commands) != trace.controller_ticks:
        raise ReplayBundleError("verified replay did not project exactly H command frames")
    if len(boundaries) != trace.controller_ticks + 1:
        raise ReplayBundleError("verified replay did not project exactly H+1 mission boundaries")
    for index, frame in enumerate(frames):
        if frame["frame_index"] != index or frame["time_ms"] != 10 * index:
            raise ReplayBundleError("projected frame cadence differs from exact 10 ms lattice")
    for index, boundary in enumerate(boundaries):
        if boundary["boundary"] != index or boundary["time_ms"] != 100 * index:
            raise ReplayBundleError("projected route boundary differs from exact 100 ms lattice")

    terminal = {
        "mission_terminal_frame_sha256": trace.mission_terminal_frame_sha256,
        "summary": copy.deepcopy(trace.terminal["summary"]),
        "domain_digests": dict(trace.domain_digests),
    }
    budget.add(terminal, label=f"{display_label} terminal")
    return {
        "display_only_label": display_label,
        "verification": _trace_verification(trace),
        "command_frames": commands,
        "frames": frames,
        "boundaries": boundaries,
        "terminal": terminal,
    }


def _safe_relative(value: Any, *, label: str) -> PurePosixPath:
    if not isinstance(value, str):
        raise ReplayBundleError(f"{label} path must be a string")
    path = PurePosixPath(value)
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or "\0" in value
        or any(part in ("", ".", "..") for part in value.split("/"))
        or path.as_posix() != value
    ):
        raise ReplayBundleError(f"{label} path is not normalized repository-relative POSIX")
    return path


def _repository(path: os.PathLike[str] | str) -> Path:
    supplied = Path(path).expanduser()
    if supplied.is_symlink():
        raise ReplayBundleError("repository root must not be a symbolic link")
    try:
        root = supplied.resolve(strict=True)
    except OSError as error:
        raise ReplayBundleError(f"repository root is unavailable: {error}") from error
    if not root.is_dir():
        raise ReplayBundleError("repository root must be a directory")
    return root


def _open_beneath(root: Path, relative: PurePosixPath) -> int:
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        directory_flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    current = os.open(root, directory_flags)
    try:
        parts = relative.parts
        for part in parts[:-1]:
            next_descriptor = os.open(part, directory_flags, dir_fd=current)
            os.close(current)
            current = next_descriptor
        file_flags = os.O_RDONLY
        if hasattr(os, "O_NONBLOCK"):
            # Prevent a substituted FIFO from blocking before fstat rejects it.
            # The flag is inert for accepted regular files.
            file_flags |= os.O_NONBLOCK
        if hasattr(os, "O_CLOEXEC"):
            file_flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        descriptor = os.open(parts[-1], file_flags, dir_fd=current)
        os.close(current)
        return descriptor
    except Exception:
        os.close(current)
        raise


def _verified_identity_file(
    repository: Path,
    identity: Mapping[str, Any],
    *,
    label: str,
    retain: bool,
) -> bytes | None:
    relative = _safe_relative(identity.get("path"), label=label)
    expected_bytes = identity.get("bytes")
    expected_sha = identity.get("sha256")
    if (
        not isinstance(expected_bytes, int)
        or isinstance(expected_bytes, bool)
        or not 0 <= expected_bytes <= MAX_IDENTITY_FILE_BYTES
    ):
        raise ReplayBundleError(
            f"{label} byte count exceeds the {MAX_IDENTITY_FILE_BYTES}-byte identity bound"
        )
    if (
        not isinstance(expected_sha, str)
        or len(expected_sha) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha)
    ):
        raise ReplayBundleError(f"{label} SHA-256 identity is malformed")
    try:
        descriptor = _open_beneath(repository, relative)
    except OSError as error:
        raise ReplayBundleError(f"cannot securely open {label}: {error}") from error
    chunks: list[bytes] = []
    digest = hashlib.sha256()
    total = 0
    try:
        initial = os.fstat(descriptor)
        if not stat.S_ISREG(initial.st_mode):
            raise ReplayBundleError(f"{label} must identify a regular file")
        if initial.st_size != expected_bytes:
            raise ReplayBundleError(f"{label} byte identity differs")
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, expected_bytes - total + 1))
            if not chunk:
                break
            total += len(chunk)
            if total > expected_bytes:
                raise ReplayBundleError(f"{label} grew beyond its byte identity")
            digest.update(chunk)
            if retain:
                chunks.append(chunk)
        final = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if total != expected_bytes or digest.hexdigest() != expected_sha:
        raise ReplayBundleError(f"{label} bytes or SHA-256 differ from authenticated identity")
    if (
        initial.st_dev != final.st_dev
        or initial.st_ino != final.st_ino
        or initial.st_size != final.st_size
        or initial.st_mtime_ns != final.st_mtime_ns
        or initial.st_ctime_ns != final.st_ctime_ns
    ):
        raise ReplayBundleError(f"{label} changed while its identity was checked")
    return b"".join(chunks) if retain else None


def _unpack_occupancy_msb(packed: bytes) -> bytes:
    if len(packed) != EXPECTED_PACKED_OCCUPANCY_BYTES:
        raise ReplayBundleError(
            f"packed occupancy must contain exactly {EXPECTED_PACKED_OCCUPANCY_BYTES} bytes"
        )
    raw = bytearray(EXPECTED_OCCUPANCY_BYTES)
    output_index = 0
    for value in packed:
        for shift in range(7, -1, -1):
            raw[output_index] = 0 if value & (1 << shift) else 255
            output_index += 1
    return bytes(raw)


def _manifest_json(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, ValueError, _DuplicateKey) as error:
        raise ReplayBundleError(f"environment manifest is not strict JSON: {error}") from error
    if not isinstance(value, dict):
        raise ReplayBundleError("environment manifest root must be an object")

    def require_finite(item: Any) -> None:
        if isinstance(item, float) and not math.isfinite(item):
            raise ReplayBundleError("environment manifest contains a nonfinite number")
        if isinstance(item, dict):
            for child in item.values():
                require_finite(child)
        elif isinstance(item, list):
            for child in item:
                require_finite(child)

    require_finite(value)
    return value


def _manifest_map_path(environment_path: str, map_path: str) -> str:
    map_relative = _safe_relative(map_path, label="environment manifest map")
    parent = _safe_relative(environment_path, label="environment manifest").parent
    return (parent / map_relative).as_posix()


def _environment_payload(
    manifest_raw: bytes,
    inputs: Mapping[str, Any],
) -> dict[str, Any]:
    manifest = _manifest_json(manifest_raw)
    cases = manifest.get("environments")
    if not isinstance(cases, list):
        raise ReplayBundleError("environment manifest environments must be an array")
    matches = [
        case
        for case in cases
        if isinstance(case, dict)
        and case.get("family") == inputs["family"]
        and case.get("split") == inputs["map_split"]
    ]
    if len(matches) != 1:
        raise ReplayBundleError("environment manifest does not select one exact family/split row")
    case = matches[0]
    identities = inputs["identities"]
    map_record = case.get("map")
    if not isinstance(map_record, dict):
        raise ReplayBundleError("selected environment row lacks a map identity")
    expected_map_path = _manifest_map_path(
        identities["environment_manifest"]["path"], map_record.get("path")
    )
    evidence_map = identities["map"]
    if (
        expected_map_path != evidence_map["path"]
        or map_record.get("bytes") != evidence_map["bytes"]
        or map_record.get("sha256") != evidence_map["sha256"]
    ):
        raise ReplayBundleError("selected environment map identity differs from common scene")
    runtime = case.get("effective_runtime_1000")
    if not isinstance(runtime, dict):
        raise ReplayBundleError("selected environment row lacks effective_runtime_1000")
    resolution = runtime.get("resolution")
    if resolution != {"height_px": 1000, "width_px": 1000}:
        raise ReplayBundleError("selected effective environment is not exactly 1000 x 1000")
    if (
        runtime.get("raw_occupancy_bytes") != inputs["effective_occupancy_bytes"]
        or runtime.get("raw_occupancy_sha256") != inputs["effective_occupancy_sha256"]
    ):
        raise ReplayBundleError("selected effective occupancy identity differs from common scene")
    metrics = runtime.get("map_metrics")
    if not isinstance(metrics, dict):
        raise ReplayBundleError("selected effective environment lacks map_metrics")
    policy = manifest.get("boundary_dimension_policy")
    if not isinstance(policy, str) or not policy:
        raise ReplayBundleError("environment boundary descriptor policy is unavailable")
    return {
        "available": True,
        "source": "effective_runtime_1000.map_metrics",
        "policy_note": policy,
        "map_metrics": copy.deepcopy(metrics),
    }


def _external_scene_assets(
    repository: Path,
    inputs: Mapping[str, Any],
    *,
    allow_analytic_fallback: bool,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    identities: Mapping[str, Mapping[str, Any]] = inputs["identities"]
    verified: dict[str, dict[str, Any]] = {}
    retained: dict[str, bytes] = {}
    for name, identity in identities.items():
        keep = name in ("environment_manifest", "occupancy_bitset")
        data = _verified_identity_file(
            repository,
            identity,
            label=f"scene identity {name}",
            retain=keep,
        )
        verified[name] = {
            "verified": True,
            "bytes": identity["bytes"],
            "path": identity["path"],
            "sha256": identity["sha256"],
        }
        if data is not None:
            retained[name] = data
    packed = retained["occupancy_bitset"]
    raw = _unpack_occupancy_msb(packed)
    raw_sha = hashlib.sha256(raw).hexdigest()
    if len(raw) != inputs["effective_occupancy_bytes"] or raw_sha != inputs["effective_occupancy_sha256"]:
        raise ReplayBundleError(
            "MSB-first packed occupancy does not reproduce the authenticated effective raster"
        )
    raster = {
        "available": True,
        "width_px": 1000,
        "height_px": 1000,
        "encoding": PACKED_OCCUPANCY_ENCODING,
        "occupied_bitset_base64": base64.b64encode(packed).decode("ascii"),
        "effective_occupancy_sha256": raw_sha,
        "source_identity": copy.deepcopy(identities["occupancy_bitset"]),
        "rendering": {
            "occupied_bit": 1,
            "occupied_uint8": 0,
            "free_bit": 0,
            "free_uint8": 255,
            "bit_order": "most-significant first",
            "row_order": "increasing y, top to bottom",
            "vertical_flip": False,
        },
    }
    environment = _environment_payload(retained["environment_manifest"], inputs)
    return (
        {"available": True, "all_declared_identity_files_verified": True, "records": verified},
        raster,
        environment,
    )


def _analytic_scene_assets(
    inputs: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    declared = {
        name: {
            "verified": False,
            "bytes": identity["bytes"],
            "path": identity["path"],
            "sha256": identity["sha256"],
        }
        for name, identity in inputs["identities"].items()
    }
    return (
        {
            "available": False,
            "all_declared_identity_files_verified": False,
            "reason": "analytic-origin common scene has no authenticated runtime-input claim",
            "records": declared,
        },
        {
            "available": False,
            "reason": "analytic fixture identity is declarative only; static segments remain available",
        },
        {
            "available": False,
            "source": None,
            "policy_note": "measured raster descriptors are unavailable for this analytic fixture",
            "map_metrics": None,
        },
    )


def _pair_payload(comparison: Any) -> dict[str, Any]:
    mismatch = comparison.first_discordance
    return {
        "valid_pair": True,
        "equal": comparison.equal,
        "events_compared": comparison.events_compared,
        "common_scene_semantic_sha256": comparison.left.common_scene_semantic_sha256,
        "left_domain_digests": dict(comparison.left.domain_digests),
        "right_domain_digests": dict(comparison.right.domain_digests),
        "first_discordance": None if mismatch is None else mismatch.as_dict(),
        "scientific_pair_eligible": False,
        "method_pair_identity_authenticated": False,
    }


def build_bundle(
    left_path: os.PathLike[str] | str,
    right_path: os.PathLike[str] | str,
    *,
    repository_root: os.PathLike[str] | str,
    left_label: str = "Run A",
    right_label: str = "Run B",
    allow_analytic_fallback: bool = False,
    max_frames: int = HARD_MAX_FRAMES,
    max_bundle_bytes: int = HARD_MAX_BUNDLE_BYTES,
) -> dict[str, Any]:
    """Fully verify two replay artifacts and return one bounded viewer bundle.

    No sampling or truncation is performed.  Inputs that exceed either caller
    bound fail with a clear error.
    """
    left_label = _display_label(left_label, name="left_label")
    right_label = _display_label(right_label, name="right_label")
    max_frames = _bounded_positive(max_frames, name="max_frames", hard_maximum=HARD_MAX_FRAMES)
    max_bundle_bytes = _bounded_positive(
        max_bundle_bytes,
        name="max_bundle_bytes",
        hard_maximum=HARD_MAX_BUNDLE_BYTES,
    )
    repository = _repository(repository_root)

    # Both complete artifacts are parsed before any projection decision.  This
    # prevents an early visualization error from hiding a malformed suffix.
    left = parse_physics_evidence_v1(left_path)
    right = parse_physics_evidence_v1(right_path)
    if left.detail != "replay" or right.detail != "replay":
        raise ReplayBundleError(
            "offline visualization requires two verified replay-detail artifacts; digest-only evidence omits authenticated leaves"
        )
    comparison = compare_verified_traces(left, right)
    frame_count = left.physics_steps + 1
    if frame_count != right.physics_steps + 1:
        raise ReplayBundleError("paired verified traces have different frame counts")
    if frame_count > max_frames:
        raise ReplayBundleError(
            f"viewer requires {frame_count} exact frames per run but max_frames is {max_frames}; no frames were downsampled"
        )

    inputs = left.header["initialization"]["common_scene"]["inputs"]
    authenticated_runtime = left.header["initialization"]["classification"][
        "authenticated_runtime_input"
    ]
    if authenticated_runtime:
        identity_files, raster, environment = _external_scene_assets(
            repository,
            inputs,
            allow_analytic_fallback=allow_analytic_fallback,
        )
    else:
        if not allow_analytic_fallback:
            raise ReplayBundleError(
                "analytic-origin replay has no externally authenticated scene files; pass allow_analytic_fallback=True only for private analytic fixtures"
            )
        identity_files, raster, environment = _analytic_scene_assets(inputs)

    decoded_settings = _decoded_settings(inputs["settings"])
    scene = {
        "common_scene_semantic_sha256": left.common_scene_semantic_sha256,
        "engine_id": inputs["engine_id"],
        "phase": inputs["phase"],
        "family": inputs["family"],
        "map_split": inputs["map_split"],
        "mission_id": inputs["mission_id"],
        "mission_semantic_sha256": inputs["mission_semantic_sha256"],
        "seed": inputs["seed"],
        "robot_count": inputs["robot_count"],
        "controller_ticks": inputs["controller_ticks"],
        "physics_steps": inputs["physics_steps"],
        "simulated_time_ms": inputs["simulated_time_ms"],
        "radius_m": decoded_settings["radius_m"],
        "settings_bits": copy.deepcopy(inputs["settings"]),
        "settings": decoded_settings,
        "identities": copy.deepcopy(inputs["identities"]),
        "identity_files_verified": identity_files,
        "static_segments": [
            _segment_payload(segment) for segment in inputs["static_segments"]
        ],
        "missions": [_mission_payload(mission) for mission in inputs["missions"]],
        "raster": raster,
        "environment_descriptors": environment,
    }
    budget = _ByteBudget(max_bundle_bytes)
    budget.add(scene, label="shared scene")
    left_payload = _run_payload(left, display_label=left_label, budget=budget)
    right_payload = _run_payload(right, display_label=right_label, budget=budget)
    bundle = {
        "schema": BUNDLE_SCHEMA,
        "encoding": ENCODING,
        "coordinate_system": {
            "frame": "centered-100m-raster-world",
            "world_bounds_m": {
                "x_min": -50.0,
                "x_max": 50.0,
                "y_min": -50.0,
                "y_max": 50.0,
            },
            "x_direction": "increasing raster column, right",
            "y_direction": "increasing raster row, down",
            "heading_positive_direction": "from +x toward +y",
            "displayed_positive_rotation": "clockwise",
            "vertical_flip": False,
        },
        "bounds": {
            "hard_max_frames": HARD_MAX_FRAMES,
            "requested_max_frames": max_frames,
            "actual_frames_per_run": frame_count,
            "hard_max_bundle_bytes": HARD_MAX_BUNDLE_BYTES,
            "requested_max_bundle_bytes": max_bundle_bytes,
            "silent_downsampling": False,
        },
        "classification": {
            "role": "offline-engineering-diagnostic-only",
            "registered_scientific_run": False,
            "scientific_evidence": False,
            "scientific_evidence_eligible": False,
            "paper_metrics_applicable": False,
            "timing_eligible": False,
            "execution_plan_bound": False,
            "official_result_namespace": False,
            "method_identity_authenticated": False,
            "display_labels_authenticate_methods": False,
        },
        "display_only_labels": {"left": left_label, "right": right_label},
        "verification": {
            "strict_replay_leaves_and_formulas": True,
            "common_scene_pair_verified": True,
            "identity_files_externally_verified": authenticated_runtime,
            "raster_available": raster["available"],
            "solver_launched_by_exporter": False,
        },
        "pair_comparison": _pair_payload(comparison),
        "scene": scene,
        "left": left_payload,
        "right": right_payload,
    }
    data = _canonical_bytes(bundle) + b"\n"
    if len(data) > max_bundle_bytes:
        raise ReplayBundleError(
            f"viewer bundle is {len(data)} bytes, above the {max_bundle_bytes}-byte laptop ceiling; no frames were downsampled"
        )
    bundle["bounds"]["actual_bundle_bytes_when_canonical"] = len(data)
    # Adding the byte-count field changes the exact size.  Iterate to its stable
    # decimal width and report the final canonical publication length.
    for _ in range(4):
        data = _canonical_bytes(bundle) + b"\n"
        if bundle["bounds"]["actual_bundle_bytes_when_canonical"] == len(data):
            break
        bundle["bounds"]["actual_bundle_bytes_when_canonical"] = len(data)
    data = _canonical_bytes(bundle) + b"\n"
    if len(data) > max_bundle_bytes:
        raise ReplayBundleError(
            f"viewer bundle is {len(data)} bytes, above the {max_bundle_bytes}-byte laptop ceiling; no frames were downsampled"
        )
    return bundle


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _secure_output_parent(path: Path, repository: Path) -> tuple[int, str]:
    if not path.is_absolute() or Path(os.path.normpath(path)) != path:
        raise ReplayBundleError("output path must be normalized and absolute")
    if len(os.fspath(path).encode("utf-8")) > 4096:
        raise ReplayBundleError("output path exceeds 4096 UTF-8 bytes")
    if not path.name or path.name in (".", ".."):
        raise ReplayBundleError("output path lacks a file name")
    if len(path.name.encode("utf-8")) > 255:
        raise ReplayBundleError("output file name exceeds 255 UTF-8 bytes")
    official = repository / OFFICIAL_RESULT_RELATIVE
    if _is_within(path, official):
        raise ReplayBundleError("viewer bundles cannot use the official result namespace")

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    current = os.open("/", flags)
    repository_status = os.stat(repository, follow_symlinks=False)
    try:
        for component in path.parent.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=current)
            os.close(current)
            current = next_descriptor
            status = os.fstat(current)
            if (
                status.st_dev == repository_status.st_dev
                and status.st_ino == repository_status.st_ino
            ):
                raise ReplayBundleError("viewer bundles must be written outside the repository")
        parent_status = os.fstat(current)
        if parent_status.st_uid != os.geteuid():
            raise ReplayBundleError("output parent must be owned by the effective user")
        if stat.S_IMODE(parent_status.st_mode) & 0o022:
            raise ReplayBundleError("output parent must not be group/world writable")
        return current, path.name
    except Exception:
        os.close(current)
        raise


def _validate_publishable_bundle(bundle: Mapping[str, Any]) -> None:
    if not isinstance(bundle, Mapping) or bundle.get("schema") != BUNDLE_SCHEMA:
        raise ReplayBundleError("publication requires a physics replay bundle v1 mapping")
    classification = bundle.get("classification")
    verification = bundle.get("verification")
    bounds = bundle.get("bounds")
    if not isinstance(classification, Mapping) or any(
        classification.get(key) is not False
        for key in (
            "registered_scientific_run",
            "scientific_evidence",
            "scientific_evidence_eligible",
            "paper_metrics_applicable",
            "timing_eligible",
            "execution_plan_bound",
            "official_result_namespace",
            "method_identity_authenticated",
            "display_labels_authenticate_methods",
        )
    ):
        raise ReplayBundleError("viewer bundle classification must remain engineering-only and unbound")
    if (
        not isinstance(verification, Mapping)
        or verification.get("strict_replay_leaves_and_formulas") is not True
        or verification.get("common_scene_pair_verified") is not True
        or verification.get("solver_launched_by_exporter") is not False
    ):
        raise ReplayBundleError("viewer bundle verification envelope is incomplete")
    if (
        not isinstance(bounds, Mapping)
        or bounds.get("hard_max_bundle_bytes") != HARD_MAX_BUNDLE_BYTES
        or bounds.get("silent_downsampling") is not False
    ):
        raise ReplayBundleError("viewer bundle bounds do not preserve the hard no-downsampling contract")


def _write_all(descriptor: int, data: bytes) -> None:
    offset = 0
    while offset < len(data):
        written = os.write(descriptor, data[offset:])
        if written <= 0:
            raise ReplayBundleError("could not complete create-exclusive viewer bundle write")
        offset += written


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def publish_bundle(
    bundle: Mapping[str, Any],
    output_path: os.PathLike[str] | str,
    *,
    repository_root: os.PathLike[str] | str,
) -> dict[str, Any]:
    """Create one mode-0600 bundle outside the repository without overwrite."""
    repository = _repository(repository_root)
    _validate_publishable_bundle(bundle)
    requested = Path(output_path).expanduser()
    if not requested.is_absolute() or Path(os.path.normpath(requested)) != requested:
        raise ReplayBundleError("output path must be normalized and absolute")
    output = requested
    data = _canonical_bytes(bundle) + b"\n"
    if len(data) > HARD_MAX_BUNDLE_BYTES:
        raise ReplayBundleError("viewer bundle exceeds the hard 64 MiB publication ceiling")
    bounds = bundle["bounds"]
    requested_maximum = bounds.get("requested_max_bundle_bytes")
    if (
        not isinstance(requested_maximum, int)
        or isinstance(requested_maximum, bool)
        or not 1 <= requested_maximum <= HARD_MAX_BUNDLE_BYTES
        or len(data) > requested_maximum
        or bounds.get("actual_bundle_bytes_when_canonical") != len(data)
    ):
        raise ReplayBundleError(
            "viewer bundle bytes differ from the declared canonical/requested publication bound"
        )
    parent_fd, basename = _secure_output_parent(output, repository)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    created: os.stat_result | None = None
    try:
        try:
            descriptor = os.open(basename, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError as error:
            raise ReplayBundleError("output already exists; viewer export is create-exclusive") from error
        created = os.fstat(descriptor)
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, data)
        os.fsync(descriptor)
        status = os.fstat(descriptor)
        if (
            not stat.S_ISREG(status.st_mode)
            or status.st_uid != os.geteuid()
            or stat.S_IMODE(status.st_mode) != 0o600
            or status.st_size != len(data)
        ):
            raise ReplayBundleError("published viewer bundle failed owner/mode/size checks")
        if descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
        os.fsync(parent_fd)
    except Exception as error:
        if descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
        if created is not None:
            try:
                current = os.stat(basename, dir_fd=parent_fd, follow_symlinks=False)
                if _same_inode(created, current):
                    os.unlink(basename, dir_fd=parent_fd)
                    try:
                        os.fsync(parent_fd)
                    except OSError:
                        # The created name is already gone.  Preserve the
                        # original publication error if directory fsync also
                        # fails during rollback.
                        pass
            except FileNotFoundError:
                pass
        if isinstance(error, ReplayBundleError):
            raise
        raise ReplayBundleError(f"could not publish viewer bundle: {error}") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent_fd)
    return {
        "path": str(output),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "mode": "0600",
        "create_exclusive": True,
        "official_result_namespace": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a bounded offline viewer bundle from two verified physics replays"
    )
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--left-label", default="Run A")
    parser.add_argument("--right-label", default="Run B")
    parser.add_argument("--allow-analytic-fallback", action="store_true")
    parser.add_argument("--max-frames", type=int, default=HARD_MAX_FRAMES)
    parser.add_argument("--max-bundle-bytes", type=int, default=HARD_MAX_BUNDLE_BYTES)
    parser.add_argument("--pretty", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        bundle = build_bundle(
            args.left,
            args.right,
            repository_root=args.repository_root,
            left_label=args.left_label,
            right_label=args.right_label,
            allow_analytic_fallback=args.allow_analytic_fallback,
            max_frames=args.max_frames,
            max_bundle_bytes=args.max_bundle_bytes,
        )
        receipt = publish_bundle(
            bundle,
            args.output,
            repository_root=args.repository_root,
        )
    except (EvidenceFormatError, EvidencePairError, ReplayBundleError, OSError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, separators=(",", ":")), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {"ok": True, "schema": BUNDLE_SCHEMA, "output": receipt},
            indent=2 if args.pretty else None,
            separators=None if args.pretty else (",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
