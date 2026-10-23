#!/usr/bin/env python3
"""Strict streaming verifier for planar-physics evidence v1.

The verifier deliberately has no dependency on the historical kinematic trace
reader.  Physics evidence has a different trust boundary: exact JSON text is
the semantic encoding, binary64 values are represented by their 16 hexadecimal
bits, and four independently framed SHA-256 streams close at the terminal.

Version 1 is an engineering-evidence format.  It has no authenticated method,
source, binary, controller, configuration, or execution-plan binding and this
module never promotes it to a scientific/reference/optimized result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import stat
import struct
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Iterable, Iterator, Mapping, Sequence


SCHEMA = "dancers-planar-physics-evidence-v1"
ENCODING = "binary64-bits-lower-hex-v1"
PAYLOAD_DOMAIN = "dancers-planar-physics-evidence-payload-v1"
EVENT_DOMAIN = "dancers-planar-physics-evidence-event-v1"
INITIALIZATION_DOMAIN = "dancers-planar-physics-initialization-v1"
COMMON_SCENE_DOMAIN = "dancers-planar-physics-common-scene-v1"
TERMINAL_DOMAIN = "dancers-planar-physics-terminal-v1"
COMMAND_DOMAIN = "dancers-planar-physics-command-stream-v1"
BODY_DOMAIN = "dancers-planar-physics-body-stream-v1"
CONTACT_DOMAIN = "dancers-planar-physics-contact-and-clearance-stream-v1"
MISSION_DOMAIN = "dancers-planar-physics-mission-summary-v1"

MAX_FILE_BYTES = 8 * 1024 * 1024 * 1024
MAX_LINE_BYTES = 256 * 1024 * 1024
MAX_ROBOTS = 4096
MAX_SEGMENTS = 8192
MAX_WAYPOINTS = 131072
U64_MAX = (1 << 64) - 1
U32_MAX = (1 << 32) - 1
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
F64_RE = re.compile(r"^[0-9a-f]{16}$")

DOMAIN_KEYS = (
    "canonical_command_sha256",
    "canonical_body_state_sha256",
    "canonical_contact_and_clearance_sha256",
    "canonical_mission_summary_sha256",
)

SETTINGS_KEYS = (
    "physics_timestep_s",
    "radius_m",
    "mass_kg",
    "inertia_kg_m2",
    "linear_damping_s_inverse",
    "angular_damping_s_inverse",
    "robot_robot_friction",
    "robot_obstacle_friction",
    "restitution",
    "maximum_requested_linear_speed_m_s",
    "maximum_requested_angular_speed_rad_s",
    "linear_servo_time_constant_s",
    "angular_servo_time_constant_s",
    "maximum_linear_acceleration_m_s2",
    "maximum_angular_acceleration_rad_s2",
    "broadphase_cell_width_m",
    "velocity_iterations",
    "position_iterations",
    "baumgarte_factor",
    "penetration_slop_m",
    "maximum_position_correction_per_iteration_m",
)

FROZEN_SETTINGS: dict[str, str | int] = {
    "physics_timestep_s": "3f847ae147ae147b",
    "radius_m": "3ff0000000000000",
    "mass_kg": "4024000000000000",
    "inertia_kg_m2": "4014000000000000",
    "linear_damping_s_inverse": "3fd0000000000000",
    "angular_damping_s_inverse": "3fe0000000000000",
    "robot_robot_friction": "3fe0000000000000",
    "robot_obstacle_friction": "3fe0000000000000",
    "restitution": "0000000000000000",
    "maximum_requested_linear_speed_m_s": "4000000000000000",
    "maximum_requested_angular_speed_rad_s": "4000000000000000",
    "linear_servo_time_constant_s": "3fd0000000000000",
    "angular_servo_time_constant_s": "3fd0000000000000",
    "maximum_linear_acceleration_m_s2": "4010000000000000",
    "maximum_angular_acceleration_rad_s2": "4020000000000000",
    "broadphase_cell_width_m": "4000000000000000",
    "velocity_iterations": 8,
    "position_iterations": 3,
    "baumgarte_factor": "3fc999999999999a",
    "penetration_slop_m": "3f50624dd2f1a9fc",
    "maximum_position_correction_per_iteration_m": "3fa999999999999a",
}

METRIC_INTEGER_KEYS = (
    "physics_steps",
    "broadphase_candidates",
    "accepted_dynamic_contacts",
    "accepted_static_contacts",
    "pair_contact_events",
    "obstacle_contact_events",
    "pair_active_contact_substeps",
    "obstacle_active_contact_substeps",
    "velocity_solver_iterations",
    "position_solver_iterations",
    "velocity_contact_visits",
    "position_contact_visits",
    "linear_saturation_body_steps",
    "angular_saturation_body_steps",
    "invalid_geometry_count",
    "solver_failure_count",
    "motion_sample_count",
)
METRIC_F64_KEYS = (
    "pair_contact_exposure_s",
    "obstacle_contact_exposure_s",
    "maximum_pre_solve_penetration_m",
    "maximum_residual_penetration_m",
    "maximum_pair_normal_impulse_n_s",
    "total_pair_normal_impulse_n_s",
    "maximum_obstacle_normal_impulse_n_s",
    "total_obstacle_normal_impulse_n_s",
    "maximum_linear_speed_m_s",
    "maximum_angular_speed_rad_s",
    "maximum_linear_acceleration_m_s2",
    "maximum_angular_acceleration_rad_s2",
    "maximum_force_n",
    "total_force_magnitude_n_s",
    "maximum_torque_n_m",
    "total_torque_magnitude_n_m_s",
    "maximum_linear_command_tracking_error_m_s",
    "maximum_angular_command_tracking_error_rad_s",
    "linear_speed_squared_sum",
    "angular_speed_squared_sum",
)
METRIC_KEYS = METRIC_INTEGER_KEYS + METRIC_F64_KEYS

METRIC_ADDITIVE_F64_KEYS = (
    "pair_contact_exposure_s",
    "obstacle_contact_exposure_s",
    "total_pair_normal_impulse_n_s",
    "total_obstacle_normal_impulse_n_s",
    "total_force_magnitude_n_s",
    "total_torque_magnitude_n_m_s",
    "linear_speed_squared_sum",
    "angular_speed_squared_sum",
)
METRIC_MAXIMUM_F64_KEYS = tuple(
    key for key in METRIC_F64_KEYS if key not in METRIC_ADDITIVE_F64_KEYS
)

SEMANTIC_GAPS = (
    "engine saturation fields are body-substep counts, not controller robot-ticks",
    "engine tracking fields are maxima; RMS is derived in canonical step-then-rid order",
    "engine contact exposure uses repeated accumulation; canonical exposure is active-substeps times fixed dt once",
    "clearance is brute-force post-position-solve at state 0 and every returned substep",
    "digest projection locates a discordant frame; exact robot/contact leaf requires replay",
)


class EvidenceFormatError(ValueError):
    """An evidence artifact failed syntax, structure, lattice, or hash checks."""

    def __init__(
        self,
        source: Path,
        message: str,
        *,
        line: int | None = None,
        path: str | None = None,
    ) -> None:
        self.source = source
        self.line = line
        self.path = path
        location = str(source)
        if line is not None:
            location += f":{line}"
        if path is not None:
            message += f" (path={path})"
        super().__init__(f"{location}: {message}")


@dataclass(frozen=True)
class EventLocator:
    line_number: int
    byte_offset: int
    byte_length: int
    event: str
    boundary: int | None
    tick: int | None
    substep: int | None
    engine_step: int | None
    simulated_time_ms: int
    semantic_sha256: str
    component_sha256: tuple[tuple[str, str], ...]
    counts: tuple[tuple[str, int], ...]

    @property
    def components(self) -> dict[str, str]:
        return dict(self.component_sha256)


@dataclass(frozen=True)
class EvidenceTrace:
    source: Path
    detail: str
    file_sha256: str
    byte_count: int
    device: int
    inode: int
    mtime_ns: int
    ctime_ns: int
    initialization_semantic_sha256: str
    common_scene_semantic_sha256: str
    mission_terminal_frame_sha256: str
    robot_count: int
    controller_ticks: int
    physics_steps: int
    simulated_time_ms: int
    run_class: str
    engine_id: str
    header: dict[str, Any]
    terminal: dict[str, Any]
    event_locators: tuple[EventLocator, ...]
    domain_digests: dict[str, str]
    censoring: dict[str, Any]
    verification_level: str
    scientific_evidence: bool = False
    execution_plan_bound: bool = False
    official_result_namespace: bool = False
    authenticated_pair_claim: bool = False

    def public_report(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "source": str(self.source),
            "valid": True,
            "detail": self.detail,
            "verification_level": self.verification_level,
            "file_sha256": self.file_sha256,
            "bytes": self.byte_count,
            "robot_count": self.robot_count,
            "controller_ticks": self.controller_ticks,
            "physics_steps": self.physics_steps,
            "simulated_time_ms": self.simulated_time_ms,
            "run_class": self.run_class,
            "engine_id": self.engine_id,
            "initialization_semantic_sha256": self.initialization_semantic_sha256,
            "common_scene_semantic_sha256": self.common_scene_semantic_sha256,
            "mission_terminal_frame_sha256": self.mission_terminal_frame_sha256,
            "domain_digests": dict(self.domain_digests),
            "censoring": self.censoring,
            "classification": {
                "scientific_evidence": False,
                "execution_plan_bound": False,
                "official_result_namespace": False,
                "authenticated_pair_claim": False,
                "paper_metrics_applicable": False,
                "role": "engineering-only",
            },
        }


class _DuplicateKey(ValueError):
    pass


def _pairs_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(f"duplicate object key {key!r}")
        result[key] = value
    return result


def _reject_float(value: str) -> None:
    raise ValueError(f"bare JSON floating-point number is forbidden: {value!r}")


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant is forbidden: {value!r}")


def canonical_bytes(value: Any) -> bytes:
    """Return the one frozen compact UTF-8 JSON representation."""
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=False,
        )
        return text.encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError(f"value cannot be represented canonically: {error}") from error


def semantic_sha256(domain: str, semantic: bytes | Mapping[str, Any]) -> str:
    payload = semantic if isinstance(semantic, bytes) else canonical_bytes(semantic)
    digest = hashlib.sha256()
    digest.update(domain.encode("ascii"))
    digest.update(b"\0")
    digest.update(payload)
    digest.update(b"\n")
    return digest.hexdigest()


def _stream_digest(domain: str) -> "hashlib._Hash":
    digest = hashlib.sha256()
    digest.update(domain.encode("ascii"))
    digest.update(b"\0")
    return digest


def _stream_update(digest: "hashlib._Hash", record: Mapping[str, Any]) -> None:
    digest.update(canonical_bytes(record))
    digest.update(b"\n")


def _without_last(value: Mapping[str, Any], expected_key: str) -> dict[str, Any]:
    keys = list(value)
    if not keys or keys[-1] != expected_key:
        raise ValueError(f"last key is not {expected_key!r}")
    return {key: value[key] for key in keys[:-1]}


def f64_from_bits(value: str) -> float:
    if not isinstance(value, str) or F64_RE.fullmatch(value) is None:
        raise ValueError("binary64 value must be exactly 16 lower-case hex digits")
    bits = int(value, 16)
    if ((bits >> 52) & 0x7FF) == 0x7FF:
        raise ValueError("NaN and infinity are forbidden binary64 values")
    return struct.unpack(">d", bytes.fromhex(value))[0]


def f64_bits(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("cannot encode a nonfinite binary64 value")
    return struct.pack(">d", value).hex()


def _reconstruct_adapter_command(
    requested_pose_words: Sequence[str],
    measured_r6_words: Sequence[str],
    final_goal_reached: bool,
) -> tuple[list[str], list[str]]:
    """Reproduce PhysicsAdapter::submitRequestedPose in engine order."""
    canonical_zero = ["0000000000000000"] * 3
    if final_goal_reached:
        return canonical_zero, canonical_zero
    requested = [f64_from_bits(word) for word in requested_pose_words]
    measured = [f64_from_bits(word) for word in measured_r6_words]
    raw_linear_x = (requested[0] - measured[0]) / 0.1
    raw_linear_y = (requested[1] - measured[1]) / 0.1
    heading_delta = requested[2] - measured[2]
    raw_angular = (
        math.atan2(math.sin(heading_delta), math.cos(heading_delta)) / 0.1
    )
    raw_values = [raw_linear_x, raw_linear_y, raw_angular]
    raw_words = [f64_bits(value) for value in raw_values]
    magnitude = math.hypot(raw_linear_x, raw_linear_y)
    if magnitude <= 2.0 or magnitude == 0.0:
        clamped_linear_x = raw_linear_x
        clamped_linear_y = raw_linear_y
    else:
        scale = 2.0 / magnitude
        clamped_linear_x = raw_linear_x * scale
        clamped_linear_y = raw_linear_y * scale
    if raw_angular < -2.0:
        clamped_angular = -2.0
    elif raw_angular > 2.0:
        clamped_angular = 2.0
    else:
        clamped_angular = raw_angular
    return raw_words, [
        f64_bits(clamped_linear_x),
        f64_bits(clamped_linear_y),
        f64_bits(clamped_angular),
    ]


def _ordered_keys(
    source: Path,
    line: int,
    value: Any,
    expected: Sequence[str],
    path: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvidenceFormatError(source, "must be an object", line=line, path=path)
    actual = tuple(value)
    if actual != tuple(expected):
        raise EvidenceFormatError(
            source,
            f"object keys/order differ; expected {tuple(expected)!r}, got {actual!r}",
            line=line,
            path=path,
        )
    return value


def _u64(source: Path, line: int, value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= U64_MAX:
        raise EvidenceFormatError(
            source, "must be an unsigned 64-bit integer", line=line, path=path
        )
    return value


def _boolean(source: Path, line: int, value: Any, path: str) -> bool:
    if not isinstance(value, bool):
        raise EvidenceFormatError(source, "must be a boolean", line=line, path=path)
    return value


def _string(source: Path, line: int, value: Any, path: str) -> str:
    if not isinstance(value, str):
        raise EvidenceFormatError(source, "must be a string", line=line, path=path)
    return value


def _sha(source: Path, line: int, value: Any, path: str) -> str:
    if not isinstance(value, str) or SHA_RE.fullmatch(value) is None:
        raise EvidenceFormatError(
            source, "must be exactly 64 lower-case hex digits", line=line, path=path
        )
    return value


def _f64(source: Path, line: int, value: Any, path: str) -> float:
    try:
        return f64_from_bits(value)
    except ValueError as error:
        raise EvidenceFormatError(source, str(error), line=line, path=path) from error


def _nullable_f64(
    source: Path, line: int, value: Any, path: str
) -> float | None:
    return None if value is None else _f64(source, line, value, path)


def _canonical_nonnegative_f64(
    source: Path, line: int, value: Any, path: str
) -> float:
    decoded = _f64(source, line, value, path)
    _expect(source, line, decoded >= 0.0, "must be physically nonnegative", path)
    _expect(
        source,
        line,
        value != "8000000000000000",
        "zero value must use canonical +0, never negative zero",
        path,
    )
    return decoded


def _vec(
    source: Path, line: int, value: Any, length: int, path: str
) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) != length:
        raise EvidenceFormatError(
            source, f"must be an array of length {length}", line=line, path=path
        )
    return tuple(_f64(source, line, item, f"{path}[{index}]") for index, item in enumerate(value))


def _expect(source: Path, line: int, condition: bool, message: str, path: str) -> None:
    if not condition:
        raise EvidenceFormatError(source, message, line=line, path=path)


def _safe_relative_path(source: Path, line: int, value: Any, path: str) -> str:
    text = _string(source, line, value, path)
    pure = PurePosixPath(text)
    if (
        not text
        or text.startswith("/")
        or "\\" in text
        or "\0" in text
        or any(component in ("", ".", "..") for component in text.split("/"))
        or pure.as_posix() != text
    ):
        raise EvidenceFormatError(
            source,
            "must be an exact normalized safe repository-relative POSIX path",
            line=line,
            path=path,
        )
    return text


def _read_json_line(
    source: Path,
    raw: bytes,
    line_number: int,
) -> dict[str, Any]:
    if not raw:
        raise EvidenceFormatError(source, "unexpected end of file", line=line_number)
    if len(raw) > MAX_LINE_BYTES + 1:
        raise EvidenceFormatError(source, "semantic line exceeds 256 MiB", line=line_number)
    if not raw.endswith(b"\n"):
        raise EvidenceFormatError(source, "line/final record lacks LF terminator", line=line_number)
    if raw.endswith(b"\r\n"):
        raise EvidenceFormatError(source, "CRLF is forbidden; exact LF is required", line=line_number)
    payload = raw[:-1]
    if not payload:
        raise EvidenceFormatError(source, "blank JSONL record is forbidden", line=line_number)
    try:
        text = payload.decode("utf-8", "strict")
        value = json.loads(
            text,
            object_pairs_hook=_pairs_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise EvidenceFormatError(
            source, f"invalid strict JSON: {error}", line=line_number
        ) from error
    if not isinstance(value, dict):
        raise EvidenceFormatError(source, "record must be an object", line=line_number)
    try:
        rendered = canonical_bytes(value)
    except ValueError as error:
        raise EvidenceFormatError(source, str(error), line=line_number) from error
    if rendered != payload:
        raise EvidenceFormatError(
            source,
            "record bytes are not the frozen compact canonical JSON representation",
            line=line_number,
        )
    return value


def _validate_settings(source: Path, line: int, value: Any, path: str) -> dict[str, Any]:
    settings = _ordered_keys(source, line, value, SETTINGS_KEYS, path)
    for key in SETTINGS_KEYS:
        if key in ("velocity_iterations", "position_iterations"):
            iteration_count = _u64(source, line, settings[key], f"{path}.{key}")
            _expect(source, line, iteration_count <= U32_MAX, "iteration count exceeds uint32", f"{path}.{key}")
        else:
            _f64(source, line, settings[key], f"{path}.{key}")
    for key, expected in FROZEN_SETTINGS.items():
        _expect(
            source,
            line,
            settings[key] == expected and type(settings[key]) is type(expected),
            "setting differs from the frozen dancers-planar-disc-engine-v1 value",
            f"{path}.{key}",
        )
    return settings


def _validate_metrics(source: Path, line: int, value: Any, path: str) -> dict[str, Any]:
    metrics = _ordered_keys(source, line, value, METRIC_KEYS, path)
    for key in METRIC_INTEGER_KEYS:
        _u64(source, line, metrics[key], f"{path}.{key}")
    for key in METRIC_F64_KEYS:
        _canonical_nonnegative_f64(source, line, metrics[key], f"{path}.{key}")
    return metrics


def _validate_clearance(
    source: Path, line: int, value: Any, path: str
) -> tuple[float | None, float | None]:
    clearance = _ordered_keys(
        source,
        line,
        value,
        ("minimum_pair_clearance_m", "minimum_obstacle_clearance_m"),
        path,
    )
    return (
        _nullable_f64(source, line, clearance["minimum_pair_clearance_m"], f"{path}.minimum_pair_clearance_m"),
        _nullable_f64(source, line, clearance["minimum_obstacle_clearance_m"], f"{path}.minimum_obstacle_clearance_m"),
    )


def _validate_header(source: Path, line: int, header: dict[str, Any]) -> dict[str, Any]:
    _ordered_keys(
        source,
        line,
        header,
        ("event", "schema", "encoding", "detail", "initialization", "semantic_sha256"),
        "$",
    )
    _expect(source, line, header["event"] == "header", "first event must be header", "$.event")
    _expect(source, line, header["schema"] == SCHEMA, "unsupported evidence schema", "$.schema")
    _expect(source, line, header["encoding"] == ENCODING, "unsupported evidence encoding", "$.encoding")
    _expect(source, line, header["detail"] in ("digest", "replay"), "detail must be digest or replay", "$.detail")
    initialization = _ordered_keys(
        source,
        line,
        header["initialization"],
        ("classification", "external_identity_binding", "paper_metrics_applicable", "common_scene"),
        "$.initialization",
    )
    classification = _ordered_keys(
        source,
        line,
        initialization["classification"],
        (
            "classification",
            "authenticated_runtime_input",
            "registered_scientific_run",
            "scientific_evidence",
            "scientific_evidence_eligible",
            "execution_locked",
            "execution_plan_bound",
            "official_result_namespace",
            "role",
        ),
        "$.initialization.classification",
    )
    expected_classification: dict[str, Any] = {
        "classification": "non-scientific-engineering",
        "registered_scientific_run": False,
        "scientific_evidence": False,
        "scientific_evidence_eligible": False,
        "execution_locked": True,
        "execution_plan_bound": False,
        "official_result_namespace": False,
        "role": "engineering-only",
    }
    _boolean(source, line, classification["authenticated_runtime_input"], "$.initialization.classification.authenticated_runtime_input")
    for key, expected in expected_classification.items():
        _expect(
            source,
            line,
            classification[key] == expected and type(classification[key]) is type(expected),
            f"v1 classification field must be {expected!r}",
            f"$.initialization.classification.{key}",
        )
    bindings = _ordered_keys(
        source,
        line,
        initialization["external_identity_binding"],
        ("method", "source", "binary", "controller", "configuration", "plan"),
        "$.initialization.external_identity_binding",
    )
    for key, value in bindings.items():
        _expect(
            source,
            line,
            value is None,
            "v1 external binding must remain null/unbound",
            f"$.initialization.external_identity_binding.{key}",
        )
    _expect(
        source,
        line,
        initialization["paper_metrics_applicable"] is False,
        "paper metrics must remain explicitly inapplicable",
        "$.initialization.paper_metrics_applicable",
    )
    common_scene = _ordered_keys(source, line, initialization["common_scene"], ("inputs",), "$.initialization.common_scene")
    inputs_keys = (
        "schema_version",
        "engine_id",
        "phase",
        "family",
        "map_split",
        "mission_id",
        "mission_semantic_sha256",
        "seed",
        "robot_count",
        "generator_seed_uint64",
        "run_class",
        "controller_ticks",
        "physics_steps",
        "simulated_time_ms",
        "settings",
        "effective_occupancy_bytes",
        "effective_occupancy_sha256",
        "identities",
        "static_segments",
        "missions",
    )
    inputs = _ordered_keys(source, line, common_scene["inputs"], inputs_keys, "$.initialization.common_scene.inputs")
    for key in ("schema_version", "engine_id", "phase", "family", "map_split", "mission_id"):
        _string(source, line, inputs[key], f"$.initialization.common_scene.inputs.{key}")
    _expect(source, line, inputs["schema_version"] == "dancers-planar-physics-run-input-v1", "run-input schema ID differs", "$.initialization.common_scene.inputs.schema_version")
    _expect(source, line, inputs["engine_id"] == "dancers-planar-disc-engine-v1", "physics engine ID differs", "$.initialization.common_scene.inputs.engine_id")
    _sha(source, line, inputs["mission_semantic_sha256"], "$.initialization.common_scene.inputs.mission_semantic_sha256")
    _u64(source, line, inputs["seed"], "$.initialization.common_scene.inputs.seed")
    robots = _u64(source, line, inputs["robot_count"], "$.initialization.common_scene.inputs.robot_count")
    _expect(source, line, 1 <= robots <= MAX_ROBOTS, "robot_count must be in 1..4096", "$.initialization.common_scene.inputs.robot_count")
    _u64(source, line, inputs["generator_seed_uint64"], "$.initialization.common_scene.inputs.generator_seed_uint64")
    run_class = _string(source, line, inputs["run_class"], "$.initialization.common_scene.inputs.run_class")
    _expect(source, line, run_class in ("physics-fixed-work-v1", "physics-route-mission-v1"), "unknown run class", "$.initialization.common_scene.inputs.run_class")
    horizon = _u64(source, line, inputs["controller_ticks"], "$.initialization.common_scene.inputs.controller_ticks")
    steps = _u64(source, line, inputs["physics_steps"], "$.initialization.common_scene.inputs.physics_steps")
    duration = _u64(source, line, inputs["simulated_time_ms"], "$.initialization.common_scene.inputs.simulated_time_ms")
    _expect(source, line, horizon > 0 and steps == 10 * horizon and duration == 100 * horizon, "controller/physics/time lattice must be H/10H/100H", "$.initialization.common_scene.inputs.controller_ticks")
    if run_class == "physics-fixed-work-v1":
        _expect(source, line, (horizon, steps, duration) == (100, 1000, 10000), "fixed-work v1 must be exactly H=100", "$.initialization.common_scene.inputs.run_class")
    _validate_settings(source, line, inputs["settings"], "$.initialization.common_scene.inputs.settings")
    _expect(source, line, inputs["effective_occupancy_bytes"] == 1_000_000, "effective occupancy must contain 1,000,000 retained bytes", "$.initialization.common_scene.inputs.effective_occupancy_bytes")
    _sha(source, line, inputs["effective_occupancy_sha256"], "$.initialization.common_scene.inputs.effective_occupancy_sha256")
    identity_names = (
        "run_input",
        "physics_registry",
        "environment_manifest",
        "collider_manifest",
        "mission_manifest",
        "map",
        "collider",
        "occupancy_bitset",
        "mission",
    )
    identities = _ordered_keys(source, line, inputs["identities"], identity_names, "$.initialization.common_scene.inputs.identities")
    for name in identity_names:
        item_path = f"$.initialization.common_scene.inputs.identities.{name}"
        identity = _ordered_keys(source, line, identities[name], ("bytes", "path", "sha256"), item_path)
        _u64(source, line, identity["bytes"], f"{item_path}.bytes")
        _safe_relative_path(source, line, identity["path"], f"{item_path}.path")
        _sha(source, line, identity["sha256"], f"{item_path}.sha256")
    segments = inputs["static_segments"]
    if not isinstance(segments, list) or len(segments) > MAX_SEGMENTS:
        raise EvidenceFormatError(source, "static_segments must be an array of at most 8192", line=line, path="$.initialization.common_scene.inputs.static_segments")
    prior_segment = -1
    for index, raw_segment in enumerate(segments):
        item_path = f"$.initialization.common_scene.inputs.static_segments[{index}]"
        segment = _ordered_keys(source, line, raw_segment, ("id", "a", "b", "free_normal"), item_path)
        segment_id = _u64(source, line, segment["id"], f"{item_path}.id")
        _expect(source, line, segment_id <= U32_MAX, "segment ID exceeds uint32", f"{item_path}.id")
        _expect(source, line, segment_id > prior_segment, "segment IDs must be strict ordered unique", f"{item_path}.id")
        prior_segment = segment_id
        for key in ("a", "b", "free_normal"):
            _vec(source, line, segment[key], 2, f"{item_path}.{key}")
    missions = inputs["missions"]
    if not isinstance(missions, list) or len(missions) != robots:
        raise EvidenceFormatError(source, "missions must contain exactly N ordered robots", line=line, path="$.initialization.common_scene.inputs.missions")
    waypoint_total = 0
    for rid, raw_mission in enumerate(missions):
        item_path = f"$.initialization.common_scene.inputs.missions[{rid}]"
        mission = _ordered_keys(
            source,
            line,
            raw_mission,
            ("id", "route_upper_length_mm", "authenticated_initial_r6_before_zero_normalization", "waypoints"),
            item_path,
        )
        _expect(source, line, mission["id"] == rid and type(mission["id"]) is int, "mission IDs must be exact ordered 0..N-1", f"{item_path}.id")
        _u64(source, line, mission["route_upper_length_mm"], f"{item_path}.route_upper_length_mm")
        initial_r6 = mission["authenticated_initial_r6_before_zero_normalization"]
        _vec(source, line, initial_r6, 6, f"{item_path}.authenticated_initial_r6_before_zero_normalization")
        _expect(
            source,
            line,
            all(word in ("0000000000000000", "8000000000000000") for word in initial_r6[3:]),
            "authenticated initial velocity words must be signed zeros only",
            f"{item_path}.authenticated_initial_r6_before_zero_normalization",
        )
        waypoints = mission["waypoints"]
        if not isinstance(waypoints, list) or not waypoints:
            raise EvidenceFormatError(source, "mission waypoints must be a nonempty array", line=line, path=f"{item_path}.waypoints")
        waypoint_total += len(waypoints)
        if waypoint_total > MAX_WAYPOINTS:
            raise EvidenceFormatError(source, "total waypoint count exceeds 131072", line=line, path="$.initialization.common_scene.inputs.missions")
        for index, waypoint in enumerate(waypoints):
            _vec(source, line, waypoint, 2, f"{item_path}.waypoints[{index}]")
    initialization_sha = _sha(source, line, header["semantic_sha256"], "$.semantic_sha256")
    expected_sha = semantic_sha256(INITIALIZATION_DOMAIN, initialization)
    _expect(source, line, initialization_sha == expected_sha, "initialization semantic SHA-256 mismatch", "$.semantic_sha256")
    return inputs


def _body_frame(
    source: Path,
    line: int,
    value: Any,
    *,
    frame_index: int,
    robot_count: int,
    path: str,
) -> list[list[str]]:
    frame = _ordered_keys(source, line, value, ("record", "frame_index", "body_count", "bodies"), path)
    _expect(source, line, frame["record"] == "body_state", "body frame record tag differs", f"{path}.record")
    _expect(source, line, frame["frame_index"] == frame_index and type(frame["frame_index"]) is int, "body frame index differs", f"{path}.frame_index")
    _expect(source, line, frame["body_count"] == robot_count and type(frame["body_count"]) is int, "body frame count differs", f"{path}.body_count")
    bodies = frame["bodies"]
    if not isinstance(bodies, list) or len(bodies) != robot_count:
        raise EvidenceFormatError(source, "body inventory differs from N", line=line, path=f"{path}.bodies")
    result: list[list[str]] = []
    for rid, raw_body in enumerate(bodies):
        item_path = f"{path}.bodies[{rid}]"
        body = _ordered_keys(source, line, raw_body, ("id", "r6"), item_path)
        _expect(source, line, body["id"] == rid and type(body["id"]) is int, "body IDs must be exact ordered 0..N-1", f"{item_path}.id")
        _vec(source, line, body["r6"], 6, f"{item_path}.r6")
        result.append(list(body["r6"]))
    return result


def _clearance_frame(
    source: Path,
    line: int,
    value: Any,
    *,
    sample_index: int,
    path: str,
) -> tuple[float | None, float | None]:
    frame = _ordered_keys(source, line, value, ("record", "sample_index", "clearance"), path)
    _expect(source, line, frame["record"] == "post_solve_clearance", "clearance record tag differs", f"{path}.record")
    _expect(source, line, frame["sample_index"] == sample_index and type(frame["sample_index"]) is int, "clearance sample index differs", f"{path}.sample_index")
    return _validate_clearance(source, line, frame["clearance"], f"{path}.clearance")


def _route_identity(mission: Mapping[str, Any]) -> str:
    record = {
        "robot_id": mission["id"],
        "route_upper_length_mm": mission["route_upper_length_mm"],
        "waypoint_count": len(mission["waypoints"]),
        "waypoints": mission["waypoints"],
    }
    return semantic_sha256(EVENT_DOMAIN, record)


def _mission_frame(
    source: Path,
    line: int,
    value: Any,
    *,
    boundary: int,
    horizon: int,
    missions: Sequence[Mapping[str, Any]],
    prior_remaining: list[int] | None,
    prior_goals: list[int | None] | None,
    path: str,
) -> tuple[list[int], list[int | None], list[bool]]:
    frame = _ordered_keys(source, line, value, ("record", "boundary", "simulated_time_ms", "robot_count", "robots"), path)
    robot_count = len(missions)
    _expect(source, line, frame["record"] == "mission_boundary", "mission record tag differs", f"{path}.record")
    _expect(source, line, frame["boundary"] == boundary and type(frame["boundary"]) is int, "mission boundary differs", f"{path}.boundary")
    _expect(source, line, frame["simulated_time_ms"] == 100 * boundary and type(frame["simulated_time_ms"]) is int, "mission simulated time differs", f"{path}.simulated_time_ms")
    _expect(source, line, frame["robot_count"] == robot_count and type(frame["robot_count"]) is int, "mission robot count differs", f"{path}.robot_count")
    robots = frame["robots"]
    if not isinstance(robots, list) or len(robots) != robot_count:
        raise EvidenceFormatError(source, "mission frame must contain N robots", line=line, path=f"{path}.robots")
    remaining_now: list[int] = []
    goals_now: list[int | None] = []
    advanced_now: list[bool] = []
    for rid, raw_robot in enumerate(robots):
        item_path = f"{path}.robots[{rid}]"
        robot = _ordered_keys(
            source,
            line,
            raw_robot,
            (
                "id",
                "current_route_index",
                "remaining_waypoint_count",
                "waypoint_advanced",
                "route_cursor_sha256",
                "next_waypoint",
                "final_goal",
                "observed_goal_controller_tick",
                "strict_goal_before_horizon",
            ),
            item_path,
        )
        _expect(source, line, robot["id"] == rid and type(robot["id"]) is int, "mission robot IDs must be exact ordered 0..N-1", f"{item_path}.id")
        total = len(missions[rid]["waypoints"])
        current = _u64(source, line, robot["current_route_index"], f"{item_path}.current_route_index")
        remaining = _u64(source, line, robot["remaining_waypoint_count"], f"{item_path}.remaining_waypoint_count")
        _expect(source, line, current + remaining == total, "route index/count do not partition authenticated route", f"{item_path}.current_route_index")
        advanced = _boolean(source, line, robot["waypoint_advanced"], f"{item_path}.waypoint_advanced")
        if prior_remaining is None:
            _expect(source, line, current in (0, 1) and advanced == (current == 1), "boundary-0 route may advance by at most one waypoint", f"{item_path}.waypoint_advanced")
        else:
            delta = prior_remaining[rid] - remaining
            _expect(source, line, delta in (0, 1) and advanced == (delta == 1), "route may advance by exactly zero or one waypoint per boundary", f"{item_path}.waypoint_advanced")
        route_identity = _route_identity(missions[rid])
        suffix = {
            "route_identity_sha256": route_identity,
            "current_route_index": current,
            "remaining_waypoint_count": remaining,
        }
        _expect(source, line, _sha(source, line, robot["route_cursor_sha256"], f"{item_path}.route_cursor_sha256") == semantic_sha256(EVENT_DOMAIN, suffix), "route cursor SHA-256 mismatch", f"{item_path}.route_cursor_sha256")
        expected_next = None if remaining == 0 else missions[rid]["waypoints"][current]
        _expect(source, line, robot["next_waypoint"] == expected_next, "next waypoint differs bitwise from authenticated suffix", f"{item_path}.next_waypoint")
        if robot["next_waypoint"] is not None:
            _vec(source, line, robot["next_waypoint"], 2, f"{item_path}.next_waypoint")
        _vec(source, line, robot["final_goal"], 2, f"{item_path}.final_goal")
        _expect(source, line, robot["final_goal"] == missions[rid]["waypoints"][-1], "final goal differs from authenticated route", f"{item_path}.final_goal")
        goal_raw = robot["observed_goal_controller_tick"]
        goal = None if goal_raw is None else _u64(source, line, goal_raw, f"{item_path}.observed_goal_controller_tick")
        _expect(source, line, goal is None or goal <= boundary, "goal tick lies after observation boundary", f"{item_path}.observed_goal_controller_tick")
        _expect(source, line, (goal is not None) == (remaining == 0), "goal tick must exist iff route is exhausted", f"{item_path}.observed_goal_controller_tick")
        if prior_goals is not None:
            prior = prior_goals[rid]
            _expect(source, line, prior is None or goal == prior, "goal tick changed after first publication", f"{item_path}.observed_goal_controller_tick")
            _expect(source, line, prior is not None or goal is None or goal == boundary, "new goal tick must equal its publication boundary", f"{item_path}.observed_goal_controller_tick")
        timing = _boolean(source, line, robot["strict_goal_before_horizon"], f"{item_path}.strict_goal_before_horizon")
        _expect(source, line, timing == (goal is not None and goal < horizon), "strict_goal_before_horizon is a pure goal-timing predicate", f"{item_path}.strict_goal_before_horizon")
        remaining_now.append(remaining)
        goals_now.append(goal)
        advanced_now.append(advanced)
    return remaining_now, goals_now, advanced_now


def _contact_frame(
    source: Path,
    line: int,
    value: Any,
    *,
    tick: int,
    substep: int,
    engine_step: int,
    robot_count: int,
    segment_ids: set[int],
    path: str,
) -> tuple[int, int, set[tuple[str, int, int]]]:
    frame = _ordered_keys(source, line, value, ("record", "tick", "substep", "engine_step", "contact_count", "contacts"), path)
    _expect(source, line, frame["record"] == "contacts", "contact record tag differs", f"{path}.record")
    for key, expected in (("tick", tick), ("substep", substep), ("engine_step", engine_step)):
        _expect(source, line, frame[key] == expected and type(frame[key]) is int, f"contact {key} differs", f"{path}.{key}")
    count = _u64(source, line, frame["contact_count"], f"{path}.contact_count")
    contacts = frame["contacts"]
    if not isinstance(contacts, list) or len(contacts) != count:
        raise EvidenceFormatError(source, "contact_count differs from contacts array", line=line, path=f"{path}.contacts")
    prior_key: tuple[int, int, int] | None = None
    dynamic = 0
    static_count = 0
    keys: set[tuple[str, int, int]] = set()
    for index, raw_contact in enumerate(contacts):
        item_path = f"{path}.contacts[{index}]"
        contact = _ordered_keys(
            source,
            line,
            raw_contact,
            (
                "kind",
                "first",
                "second",
                "normal_applied_to_first",
                "point",
                "signed_clearance_before_m",
                "penetration_before_m",
                "residual_penetration_m",
                "final_normal_impulse_n_s",
                "final_tangent_impulse_n_s",
            ),
            item_path,
        )
        kind = _string(source, line, contact["kind"], f"{item_path}.kind")
        first = _u64(source, line, contact["first"], f"{item_path}.first")
        second = _u64(source, line, contact["second"], f"{item_path}.second")
        if kind == "dynamic_pair":
            _expect(source, line, first < second < robot_count, "dynamic contact IDs must satisfy first < second < N", f"{item_path}.first")
            order_key = (0, first, second)
            dynamic += 1
        elif kind == "static_segment":
            _expect(source, line, first < robot_count and second in segment_ids, "static contact body/segment ID is outside inventory", f"{item_path}.first")
            order_key = (1, first, second)
            static_count += 1
        else:
            raise EvidenceFormatError(source, "unknown contact kind", line=line, path=f"{item_path}.kind")
        _expect(source, line, prior_key is None or prior_key < order_key, "contact keys must be strict canonical ordered unique", item_path)
        prior_key = order_key
        keys.add((kind, first, second))
        _vec(source, line, contact["normal_applied_to_first"], 2, f"{item_path}.normal_applied_to_first")
        _vec(source, line, contact["point"], 2, f"{item_path}.point")
        _f64(
            source,
            line,
            contact["signed_clearance_before_m"],
            f"{item_path}.signed_clearance_before_m",
        )
        for key in (
            "penetration_before_m",
            "residual_penetration_m",
            "final_normal_impulse_n_s",
        ):
            _canonical_nonnegative_f64(
                source, line, contact[key], f"{item_path}.{key}"
            )
        _f64(
            source,
            line,
            contact["final_tangent_impulse_n_s"],
            f"{item_path}.final_tangent_impulse_n_s",
        )
    return dynamic, static_count, keys


def _float_close_bits(recorded: str, computed: float, *, ulps: int = 16) -> bool:
    """Compare a recomputed formula while allowing only cross-libm last-bit drift."""
    try:
        actual = f64_from_bits(recorded)
    except ValueError:
        return False
    if actual == 0.0 and computed == 0.0:
        return recorded == f64_bits(computed)
    if actual == computed:
        return True
    if not (math.isfinite(actual) and math.isfinite(computed)):
        return False
    tolerance = max(math.ulp(actual), math.ulp(computed)) * ulps
    return abs(actual - computed) <= tolerance


def _secure_open(source: Path) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NONBLOCK"):
        # A FIFO opened read-only without this flag blocks before fstat can
        # reject it. O_NONBLOCK is inert for accepted regular files.
        flags |= os.O_NONBLOCK
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(source, flags)
    except OSError as error:
        raise EvidenceFormatError(source, f"cannot securely open evidence: {error}") from error
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode):
            raise EvidenceFormatError(source, "evidence source must be a regular file")
        if status.st_size > MAX_FILE_BYTES:
            raise EvidenceFormatError(source, "evidence exceeds the 8 GiB bound")
        return descriptor, status
    except Exception:
        os.close(descriptor)
        raise


class _Verifier:
    def __init__(self, source: Path) -> None:
        self.source = source
        self.line = 0
        self.offset = 0
        self.file_hash = hashlib.sha256()
        self.payload_hash = _stream_digest(PAYLOAD_DOMAIN)
        self.payload_bytes = 0
        self.command_hash = _stream_digest(COMMAND_DOMAIN)
        self.body_hash = _stream_digest(BODY_DOMAIN)
        self.contact_hash = _stream_digest(CONTACT_DOMAIN)
        self.mission_hash = _stream_digest(MISSION_DOMAIN)
        self.locators: list[EventLocator] = []
        self.previous_bodies: list[list[str]] | None = None
        self.last_body_sha: str | None = None
        self.last_clearance_sha: str | None = None
        self.prior_remaining: list[int] | None = None
        self.prior_goals: list[int | None] | None = None
        self.boundary_advanced: list[bool] | None = None
        self.held_commands: list[list[str]] | None = None
        self.path_lengths: list[float] | None = None
        self.linear_error_squared_sum = 0.0
        self.angular_error_squared_sum = 0.0
        self.max_linear_error = 0.0
        self.max_angular_error = 0.0
        self.tracking_samples = 0
        self.controller_linear_saturation = 0
        self.controller_angular_saturation = 0
        self.min_pair_clearance: tuple[str, float] | None = None
        self.min_obstacle_clearance: tuple[str, float] | None = None
        self.dynamic_contacts = 0
        self.static_contacts = 0
        self.contact_items = 0
        self.pair_events = 0
        self.obstacle_events = 0
        self.prior_contact_keys: set[tuple[str, int, int]] = set()
        self.final_cumulative_metrics: dict[str, Any] | None = None

    def _next(self, stream: BinaryIO, *, preterminal: bool) -> tuple[dict[str, Any], int, int]:
        start = self.offset
        raw = stream.readline(MAX_LINE_BYTES + 2)
        self.line += 1
        value = _read_json_line(self.source, raw, self.line)
        self.offset += len(raw)
        self.file_hash.update(raw)
        if preterminal:
            self.payload_hash.update(raw)
            self.payload_bytes += len(raw)
        return value, start, len(raw)

    def _locator(
        self,
        value: Mapping[str, Any],
        start: int,
        length: int,
        components: Sequence[tuple[str, str]],
        counts: Sequence[tuple[str, int]],
    ) -> None:
        self.locators.append(
            EventLocator(
                line_number=self.line,
                byte_offset=start,
                byte_length=length,
                event=value["event"],
                boundary=value.get("boundary"),
                tick=value.get("tick"),
                substep=value.get("substep"),
                engine_step=value.get("engine_step"),
                simulated_time_ms=value["simulated_time_ms"],
                semantic_sha256=value["semantic_sha256"],
                component_sha256=tuple(components),
                counts=tuple(counts),
            )
        )

    def parse(self) -> EvidenceTrace:
        descriptor, initial_status = _secure_open(self.source)
        try:
            with os.fdopen(descriptor, "rb", closefd=True) as stream:
                header, _, _ = self._next(stream, preterminal=True)
                inputs = _validate_header(self.source, self.line, header)
                self.detail = header["detail"]
                self.header = header
                self.inputs = inputs
                self.robot_count = inputs["robot_count"]
                self.horizon = inputs["controller_ticks"]
                self.steps = inputs["physics_steps"]
                self.run_class = inputs["run_class"]
                self.missions: list[Mapping[str, Any]] = inputs["missions"]
                self.segment_ids = {item["id"] for item in inputs["static_segments"]}
                self.path_lengths = [0.0] * self.robot_count
                self.held_commands = [["0000000000000000"] * 3 for _ in range(self.robot_count)]

                self._parse_boundary(stream, 0)
                for tick in range(self.horizon):
                    self._parse_commands(stream, tick)
                    for substep in range(10):
                        self._parse_substep(stream, tick, substep)
                    self._parse_boundary(stream, tick + 1)
                terminal, _, _ = self._next(stream, preterminal=False)
                self._parse_terminal(terminal)
                trailing = stream.read(1)
                if trailing:
                    raise EvidenceFormatError(self.source, "trailing bytes/records after terminal", line=self.line + 1)
                final_status = os.fstat(stream.fileno())
            if (
                initial_status.st_dev != final_status.st_dev
                or initial_status.st_ino != final_status.st_ino
                or initial_status.st_size != final_status.st_size
                or initial_status.st_mtime_ns != final_status.st_mtime_ns
                or initial_status.st_ctime_ns != final_status.st_ctime_ns
                or self.offset != initial_status.st_size
            ):
                raise EvidenceFormatError(self.source, "evidence inode changed while being verified")
        except Exception:
            # os.fdopen owns descriptor after successful construction.
            raise
        goals = [robot["observed_goal_controller_tick"] for robot in terminal["summary"]["robots"]]
        strict_events = [goal is not None and goal < self.horizon for goal in goals]
        per_robot_censor = [
            {
                "id": rid,
                "event": bool(strict_events[rid]),
                "time_ms": 100 * goals[rid] if strict_events[rid] else 100 * self.horizon,
                "endpoint_h_goal_diagnostic": goals[rid] == self.horizon,
            }
            for rid in range(self.robot_count)
        ]
        scene_event = all(strict_events)
        censoring = {
            "rule": "event only for observed goal tick < H; tick H and never-observed are right-censored at 100H ms",
            "per_robot": per_robot_censor,
            "scene": {
                "event": scene_event,
                "time_ms": 100 * max(goals) if scene_event else 100 * self.horizon,
            },
        }
        return EvidenceTrace(
            source=self.source,
            detail=self.detail,
            file_sha256=self.file_hash.hexdigest(),
            byte_count=initial_status.st_size,
            device=initial_status.st_dev,
            inode=initial_status.st_ino,
            mtime_ns=initial_status.st_mtime_ns,
            ctime_ns=initial_status.st_ctime_ns,
            initialization_semantic_sha256=header["semantic_sha256"],
            common_scene_semantic_sha256=semantic_sha256(COMMON_SCENE_DOMAIN, header["initialization"]["common_scene"]),
            mission_terminal_frame_sha256=terminal["mission_terminal_frame_sha256"],
            robot_count=self.robot_count,
            controller_ticks=self.horizon,
            physics_steps=self.steps,
            simulated_time_ms=inputs["simulated_time_ms"],
            run_class=self.run_class,
            engine_id=inputs["engine_id"],
            header=header,
            terminal=terminal,
            event_locators=tuple(self.locators),
            domain_digests=dict(terminal["domain_digests"]),
            censoring=censoring,
            verification_level=("replay-full-leaf-and-formula" if self.detail == "replay" else "digest-frame-reference-and-terminal"),
        )

    def _parse_boundary(self, stream: BinaryIO, boundary: int) -> None:
        value, start, length = self._next(stream, preterminal=True)
        digest_keys = (
            "event", "boundary", "simulated_time_ms", "state_frame_index",
            "body_frame_sha256", "body_count", "mission_robot_count",
            "clearance_sample_index", "clearance_frame_sha256",
            "mission_frame_sha256", "semantic_sha256",
        )
        replay_keys = (
            "event", "boundary", "simulated_time_ms", "state_frame_index",
            "body_frame_sha256", "body_count", "body_state_frame",
            "clearance_sample_index", "clearance_frame_sha256", "clearance_frame",
            "mission_frame_sha256", "mission", "semantic_sha256",
        )
        _ordered_keys(self.source, self.line, value, digest_keys if self.detail == "digest" else replay_keys, "$")
        _expect(self.source, self.line, value["event"] == "boundary", "expected boundary event", "$.event")
        _expect(self.source, self.line, value["boundary"] == boundary and type(value["boundary"]) is int, "boundary index differs from exact lattice", "$.boundary")
        expected_frame = 10 * boundary
        expected_time = 100 * boundary
        _expect(self.source, self.line, value["simulated_time_ms"] == expected_time and type(value["simulated_time_ms"]) is int, "boundary simulated time differs", "$.simulated_time_ms")
        _expect(self.source, self.line, value["state_frame_index"] == expected_frame and type(value["state_frame_index"]) is int, "boundary state frame index differs", "$.state_frame_index")
        body_sha = _sha(self.source, self.line, value["body_frame_sha256"], "$.body_frame_sha256")
        _expect(self.source, self.line, value["body_count"] == self.robot_count and type(value["body_count"]) is int, "boundary body_count differs from N", "$.body_count")
        _expect(self.source, self.line, value["clearance_sample_index"] == expected_frame and type(value["clearance_sample_index"]) is int, "boundary clearance index differs", "$.clearance_sample_index")
        clearance_sha = _sha(self.source, self.line, value["clearance_frame_sha256"], "$.clearance_frame_sha256")
        mission_sha = _sha(self.source, self.line, value["mission_frame_sha256"], "$.mission_frame_sha256")
        if boundary > 0:
            _expect(self.source, self.line, body_sha == self.last_body_sha, "boundary body hash differs from endpoint substep", "$.body_frame_sha256")
            _expect(self.source, self.line, clearance_sha == self.last_clearance_sha, "boundary clearance hash differs from endpoint substep", "$.clearance_frame_sha256")
        if self.detail == "digest":
            _expect(self.source, self.line, value["mission_robot_count"] == self.robot_count and type(value["mission_robot_count"]) is int, "mission_robot_count differs from N", "$.mission_robot_count")
        else:
            if boundary == 0:
                bodies = _body_frame(self.source, self.line, value["body_state_frame"], frame_index=0, robot_count=self.robot_count, path="$.body_state_frame")
                _expect(self.source, self.line, semantic_sha256(EVENT_DOMAIN, value["body_state_frame"]) == body_sha, "boundary-0 body frame SHA-256 mismatch", "$.body_frame_sha256")
                for rid, body in enumerate(bodies):
                    initial = self.missions[rid]["authenticated_initial_r6_before_zero_normalization"]
                    _expect(self.source, self.line, body[:3] == initial[:3] and body[3:] == ["0000000000000000"] * 3, "boundary-0 measured body must preserve pose and normalize all velocities to +0", f"$.body_state_frame.bodies[{rid}].r6")
                self.previous_bodies = bodies
                clearance = _clearance_frame(self.source, self.line, value["clearance_frame"], sample_index=0, path="$.clearance_frame")
                _expect(self.source, self.line, semantic_sha256(EVENT_DOMAIN, value["clearance_frame"]) == clearance_sha, "boundary-0 clearance frame SHA-256 mismatch", "$.clearance_frame_sha256")
                self._update_min_clearance(value["clearance_frame"]["clearance"], clearance)
            else:
                _expect(self.source, self.line, value["body_state_frame"] is None, "nonzero boundary must reference, not duplicate, endpoint body state", "$.body_state_frame")
                _expect(self.source, self.line, value["clearance_frame"] is None, "nonzero boundary must reference, not duplicate, endpoint clearance", "$.clearance_frame")
            remaining, goals, advanced = _mission_frame(
                self.source,
                self.line,
                value["mission"],
                boundary=boundary,
                horizon=self.horizon,
                missions=self.missions,
                prior_remaining=self.prior_remaining,
                prior_goals=self.prior_goals,
                path="$.mission",
            )
            _expect(self.source, self.line, semantic_sha256(EVENT_DOMAIN, value["mission"]) == mission_sha, "mission frame SHA-256 mismatch", "$.mission_frame_sha256")
            semantic = _without_last(value, "semantic_sha256")
            _expect(self.source, self.line, semantic_sha256(EVENT_DOMAIN, semantic) == value["semantic_sha256"], "boundary event semantic SHA-256 mismatch", "$.semantic_sha256")
            self.prior_remaining, self.prior_goals, self.boundary_advanced = remaining, goals, advanced
        if boundary == 0:
            _stream_update(self.body_hash, {"record": "body_frame_ref", "frame_index": 0, "simulated_time_ms": 0, "item_count": self.robot_count, "frame_sha256": body_sha})
            _stream_update(self.contact_hash, {"record": "clearance_frame_ref", "sample_index": 0, "simulated_time_ms": 0, "frame_sha256": clearance_sha})
        _stream_update(self.mission_hash, {"record": "mission_boundary_frame_ref", "boundary": boundary, "simulated_time_ms": expected_time, "item_count": self.robot_count, "frame_sha256": mission_sha})
        _sha(self.source, self.line, value["semantic_sha256"], "$.semantic_sha256")
        self._locator(
            value,
            start,
            length,
            (("body", body_sha), ("contact_and_clearance", clearance_sha), ("mission", mission_sha)),
            (("body_count", self.robot_count), ("mission_robot_count", self.robot_count)),
        )

    def _parse_commands(self, stream: BinaryIO, tick: int) -> None:
        value, start, length = self._next(stream, preterminal=True)
        digest_keys = ("event", "tick", "simulated_time_ms", "record_count", "command_frame_sha256", "semantic_sha256")
        replay_keys = ("event", "tick", "simulated_time_ms", "record_count", "records", "command_frame_sha256", "semantic_sha256")
        _ordered_keys(self.source, self.line, value, digest_keys if self.detail == "digest" else replay_keys, "$")
        _expect(self.source, self.line, value["event"] == "commands", "expected commands event", "$.event")
        _expect(self.source, self.line, value["tick"] == tick and type(value["tick"]) is int, "command tick differs from exact lattice", "$.tick")
        _expect(self.source, self.line, value["simulated_time_ms"] == 100 * tick and type(value["simulated_time_ms"]) is int, "command simulated time differs", "$.simulated_time_ms")
        _expect(self.source, self.line, value["record_count"] == self.robot_count and type(value["record_count"]) is int, "command record_count differs from N", "$.record_count")
        frame_sha = _sha(self.source, self.line, value["command_frame_sha256"], "$.command_frame_sha256")
        if self.detail == "replay":
            records = value["records"]
            if not isinstance(records, list) or len(records) != self.robot_count:
                raise EvidenceFormatError(self.source, "commands records must contain N robots", line=self.line, path="$.records")
            for rid, raw_record in enumerate(records):
                path = f"$.records[{rid}]"
                record = _ordered_keys(
                    self.source,
                    self.line,
                    raw_record,
                    (
                        "id", "planner_raw_requested_pose", "planner_effective_requested_pose",
                        "adapter_requested_pose", "adapter_raw_velocity_command",
                        "adapter_clamped_velocity_command", "waypoint_advanced",
                        "final_goal_reached", "remaining_waypoint_count",
                    ),
                    path,
                )
                _expect(self.source, self.line, record["id"] == rid and type(record["id"]) is int, "command robot IDs must be exact ordered 0..N-1", f"{path}.id")
                for key in (
                    "planner_raw_requested_pose", "planner_effective_requested_pose",
                    "adapter_requested_pose", "adapter_raw_velocity_command",
                    "adapter_clamped_velocity_command",
                ):
                    _vec(self.source, self.line, record[key], 3, f"{path}.{key}")
                _expect(self.source, self.line, record["planner_effective_requested_pose"] == record["adapter_requested_pose"], "planner effective pose must equal adapter requested pose bitwise", f"{path}.adapter_requested_pose")
                advanced = _boolean(self.source, self.line, record["waypoint_advanced"], f"{path}.waypoint_advanced")
                reached = _boolean(self.source, self.line, record["final_goal_reached"], f"{path}.final_goal_reached")
                remaining = _u64(self.source, self.line, record["remaining_waypoint_count"], f"{path}.remaining_waypoint_count")
                assert self.prior_remaining is not None and self.prior_goals is not None and self.boundary_advanced is not None
                _expect(self.source, self.line, advanced == self.boundary_advanced[rid] and remaining == self.prior_remaining[rid] and reached == (self.prior_goals[rid] is not None), "command route/goal flags differ from latest boundary", path)
                raw = record["adapter_raw_velocity_command"]
                clamped = record["adapter_clamped_velocity_command"]
                if reached:
                    canonical_zero_command = ["0000000000000000"] * 3
                    _expect(
                        self.source,
                        self.line,
                        raw == canonical_zero_command and clamped == canonical_zero_command,
                        "final-goal command must be canonical +0 raw and clamped velocity",
                        f"{path}.adapter_raw_velocity_command",
                    )
                assert self.previous_bodies is not None
                try:
                    expected_raw, expected_clamped = _reconstruct_adapter_command(
                        record["adapter_requested_pose"],
                        self.previous_bodies[rid],
                        reached,
                    )
                except (ValueError, OverflowError) as error:
                    raise EvidenceFormatError(
                        self.source,
                        f"adapter reconstruction is nonfinite: {error}",
                        line=self.line,
                        path=f"{path}.adapter_requested_pose",
                    ) from error
                _expect(
                    self.source,
                    self.line,
                    raw == expected_raw,
                    "adapter raw velocity command differs bitwise from frozen pose-difference conversion",
                    f"{path}.adapter_raw_velocity_command",
                )
                _expect(
                    self.source,
                    self.line,
                    clamped == expected_clamped,
                    "adapter clamped velocity command differs bitwise from frozen norm/absolute clamp",
                    f"{path}.adapter_clamped_velocity_command",
                )
                if raw[0] != clamped[0] or raw[1] != clamped[1]:
                    self.controller_linear_saturation += 1
                if raw[2] != clamped[2]:
                    self.controller_angular_saturation += 1
                assert self.held_commands is not None
                self.held_commands[rid] = list(clamped)
            frame_semantic = {key: value[key] for key in ("event", "tick", "simulated_time_ms", "record_count", "records")}
            _expect(self.source, self.line, semantic_sha256(EVENT_DOMAIN, frame_semantic) == frame_sha, "command frame SHA-256 mismatch", "$.command_frame_sha256")
            _expect(self.source, self.line, semantic_sha256(EVENT_DOMAIN, _without_last(value, "semantic_sha256")) == value["semantic_sha256"], "command event semantic SHA-256 mismatch", "$.semantic_sha256")
        _sha(self.source, self.line, value["semantic_sha256"], "$.semantic_sha256")
        _stream_update(self.command_hash, {"record": "command_frame_ref", "tick": tick, "simulated_time_ms": 100 * tick, "item_count": self.robot_count, "frame_sha256": frame_sha})
        self._locator(value, start, length, (("command", frame_sha),), (("record_count", self.robot_count),))

    def _parse_substep(self, stream: BinaryIO, tick: int, substep: int) -> None:
        value, start, length = self._next(stream, preterminal=True)
        digest_keys = (
            "event", "tick", "substep", "engine_step", "simulated_time_ms",
            "body_count", "body_frame_sha256", "contact_count", "contact_frame_sha256",
            "clearance_frame_sha256", "semantic_sha256",
        )
        replay_keys = (
            "event", "tick", "substep", "engine_step", "simulated_time_ms",
            "body_count", "body_frame_sha256", "body_state_frame", "contact_count",
            "contact_frame_sha256", "contact_frame", "clearance_frame_sha256",
            "clearance_frame", "step_engine_metrics", "cumulative_engine_metrics",
            "semantic_sha256",
        )
        _ordered_keys(self.source, self.line, value, digest_keys if self.detail == "digest" else replay_keys, "$")
        engine_step = 10 * tick + substep + 1
        time_ms = 10 * engine_step
        _expect(self.source, self.line, value["event"] == "substep", "expected substep event", "$.event")
        for key, expected in (("tick", tick), ("substep", substep), ("engine_step", engine_step), ("simulated_time_ms", time_ms)):
            _expect(self.source, self.line, value[key] == expected and type(value[key]) is int, f"substep {key} differs from exact lattice", f"$.{key}")
        _expect(self.source, self.line, value["body_count"] == self.robot_count and type(value["body_count"]) is int, "substep body_count differs from N", "$.body_count")
        contact_count = _u64(self.source, self.line, value["contact_count"], "$.contact_count")
        body_sha = _sha(self.source, self.line, value["body_frame_sha256"], "$.body_frame_sha256")
        contact_sha = _sha(self.source, self.line, value["contact_frame_sha256"], "$.contact_frame_sha256")
        clearance_sha = _sha(self.source, self.line, value["clearance_frame_sha256"], "$.clearance_frame_sha256")
        if self.detail == "replay":
            bodies = _body_frame(self.source, self.line, value["body_state_frame"], frame_index=engine_step, robot_count=self.robot_count, path="$.body_state_frame")
            _expect(self.source, self.line, semantic_sha256(EVENT_DOMAIN, value["body_state_frame"]) == body_sha, "body frame SHA-256 mismatch", "$.body_frame_sha256")
            dynamic, static_count, contact_keys = _contact_frame(
                self.source,
                self.line,
                value["contact_frame"],
                tick=tick,
                substep=substep,
                engine_step=engine_step,
                robot_count=self.robot_count,
                segment_ids=self.segment_ids,
                path="$.contact_frame",
            )
            _expect(self.source, self.line, dynamic + static_count == contact_count, "contact_count differs from decoded contacts", "$.contact_count")
            _expect(self.source, self.line, semantic_sha256(EVENT_DOMAIN, value["contact_frame"]) == contact_sha, "contact frame SHA-256 mismatch", "$.contact_frame_sha256")
            clearance = _clearance_frame(self.source, self.line, value["clearance_frame"], sample_index=engine_step, path="$.clearance_frame")
            _expect(self.source, self.line, semantic_sha256(EVENT_DOMAIN, value["clearance_frame"]) == clearance_sha, "clearance frame SHA-256 mismatch", "$.clearance_frame_sha256")
            step_metrics = _validate_metrics(self.source, self.line, value["step_engine_metrics"], "$.step_engine_metrics")
            cumulative = _validate_metrics(self.source, self.line, value["cumulative_engine_metrics"], "$.cumulative_engine_metrics")
            expected_step_counts = {
                "physics_steps": 1,
                "accepted_dynamic_contacts": dynamic,
                "accepted_static_contacts": static_count,
                "pair_active_contact_substeps": dynamic,
                "obstacle_active_contact_substeps": static_count,
                "motion_sample_count": self.robot_count,
            }
            for key, expected in expected_step_counts.items():
                _expect(self.source, self.line, step_metrics[key] == expected, "step engine/contact accounting differs", f"$.step_engine_metrics.{key}")
            for key in METRIC_INTEGER_KEYS:
                if self.final_cumulative_metrics is None:
                    expected = step_metrics[key]
                else:
                    expected = self.final_cumulative_metrics[key] + step_metrics[key]
                _expect(self.source, self.line, cumulative[key] == expected, "cumulative integer engine metric is not prior + step", f"$.cumulative_engine_metrics.{key}")
            for key in METRIC_ADDITIVE_F64_KEYS:
                prior_value = (
                    0.0
                    if self.final_cumulative_metrics is None
                    else f64_from_bits(self.final_cumulative_metrics[key])
                )
                expected_value = prior_value + f64_from_bits(step_metrics[key])
                _expect(
                    self.source,
                    self.line,
                    cumulative[key] == f64_bits(expected_value),
                    "cumulative floating engine total is not prior + step",
                    f"$.cumulative_engine_metrics.{key}",
                )
            for key in METRIC_MAXIMUM_F64_KEYS:
                prior_word = (
                    "0000000000000000"
                    if self.final_cumulative_metrics is None
                    else self.final_cumulative_metrics[key]
                )
                step_word = step_metrics[key]
                expected_word = (
                    step_word
                    if f64_from_bits(prior_word) < f64_from_bits(step_word)
                    else prior_word
                )
                _expect(
                    self.source,
                    self.line,
                    cumulative[key] == expected_word,
                    "cumulative floating engine maximum differs from std::max(prior, step)",
                    f"$.cumulative_engine_metrics.{key}",
                )
            new_pair = sum(1 for key in contact_keys if key[0] == "dynamic_pair" and key not in self.prior_contact_keys)
            new_obstacle = sum(1 for key in contact_keys if key[0] == "static_segment" and key not in self.prior_contact_keys)
            _expect(self.source, self.line, step_metrics["pair_contact_events"] == new_pair and step_metrics["obstacle_contact_events"] == new_obstacle, "contact transition event counts differ", "$.step_engine_metrics.pair_contact_events")
            self.prior_contact_keys = contact_keys
            self.pair_events += new_pair
            self.obstacle_events += new_obstacle
            self.dynamic_contacts += dynamic
            self.static_contacts += static_count
            self.contact_items += contact_count
            self.final_cumulative_metrics = cumulative
            assert self.previous_bodies is not None and self.path_lengths is not None and self.held_commands is not None
            for rid, body in enumerate(bodies):
                previous = self.previous_bodies[rid]
                dx = f64_from_bits(body[0]) - f64_from_bits(previous[0])
                dy = f64_from_bits(body[1]) - f64_from_bits(previous[1])
                self.path_lengths[rid] += math.hypot(dx, dy)
                desired = self.held_commands[rid]
                linear_error = math.hypot(
                    f64_from_bits(desired[0]) - f64_from_bits(body[3]),
                    f64_from_bits(desired[1]) - f64_from_bits(body[4]),
                )
                angular_error = abs(f64_from_bits(desired[2]) - f64_from_bits(body[5]))
                self.linear_error_squared_sum += linear_error * linear_error
                self.angular_error_squared_sum += angular_error * angular_error
                self.max_linear_error = max(self.max_linear_error, linear_error)
                self.max_angular_error = max(self.max_angular_error, angular_error)
                self.tracking_samples += 1
            self.previous_bodies = bodies
            self._update_min_clearance(value["clearance_frame"]["clearance"], clearance)
            _expect(self.source, self.line, semantic_sha256(EVENT_DOMAIN, _without_last(value, "semantic_sha256")) == value["semantic_sha256"], "substep event semantic SHA-256 mismatch", "$.semantic_sha256")
        _sha(self.source, self.line, value["semantic_sha256"], "$.semantic_sha256")
        _stream_update(self.body_hash, {"record": "body_frame_ref", "frame_index": engine_step, "simulated_time_ms": time_ms, "item_count": self.robot_count, "frame_sha256": body_sha})
        _stream_update(self.contact_hash, {"record": "contact_frame_ref", "engine_step": engine_step, "simulated_time_ms": time_ms, "item_count": contact_count, "frame_sha256": contact_sha})
        _stream_update(self.contact_hash, {"record": "clearance_frame_ref", "sample_index": engine_step, "simulated_time_ms": time_ms, "frame_sha256": clearance_sha})
        self.last_body_sha = body_sha
        self.last_clearance_sha = clearance_sha
        self._locator(
            value,
            start,
            length,
            (("body", body_sha), ("contact", contact_sha), ("clearance", clearance_sha)),
            (("body_count", self.robot_count), ("contact_count", contact_count)),
        )

    def _update_min_clearance(
        self,
        raw: Mapping[str, Any],
        decoded: tuple[float | None, float | None],
    ) -> None:
        _expect(
            self.source,
            self.line,
            (decoded[0] is None) == (self.robot_count < 2),
            "pair clearance is null iff N < 2",
            "$.clearance.minimum_pair_clearance_m",
        )
        _expect(
            self.source,
            self.line,
            (decoded[1] is None) == (len(self.segment_ids) == 0),
            "obstacle clearance is null iff the static-segment inventory is empty",
            "$.clearance.minimum_obstacle_clearance_m",
        )
        for index, attribute in enumerate(("min_pair_clearance", "min_obstacle_clearance")):
            numeric = decoded[index]
            if numeric is None:
                continue
            bits = raw[("minimum_pair_clearance_m", "minimum_obstacle_clearance_m")[index]]
            current = getattr(self, attribute)
            if current is None or numeric < current[1]:
                setattr(self, attribute, (bits, numeric))

    def _parse_terminal(self, terminal: dict[str, Any]) -> None:
        _ordered_keys(
            self.source,
            self.line,
            terminal,
            (
                "event", "schema", "detail", "initialization_semantic_sha256",
                "mission_terminal_frame_sha256", "summary", "domain_digests",
                "preterminal_payload_domain", "preterminal_payload_bytes",
                "preterminal_payload_sha256", "terminal_semantic_sha256",
            ),
            "$",
        )
        _expect(self.source, self.line, terminal["event"] == "terminal", "expected terminal event", "$.event")
        _expect(self.source, self.line, terminal["schema"] == SCHEMA and terminal["detail"] == self.detail, "terminal schema/detail differs from header", "$.detail")
        _expect(self.source, self.line, terminal["initialization_semantic_sha256"] == self.header["semantic_sha256"], "terminal initialization SHA differs from header", "$.initialization_semantic_sha256")
        summary = self._validate_summary(terminal["summary"])
        mission_terminal_sha = _sha(self.source, self.line, terminal["mission_terminal_frame_sha256"], "$.mission_terminal_frame_sha256")
        _expect(self.source, self.line, mission_terminal_sha == semantic_sha256(EVENT_DOMAIN, summary), "mission terminal frame SHA-256 mismatch", "$.mission_terminal_frame_sha256")
        _stream_update(self.command_hash, {"record": "commands_end", "frame_count": self.horizon, "item_count": self.robot_count * self.horizon})
        command_sha = self.command_hash.hexdigest()
        _stream_update(self.body_hash, {"record": "body_states_end", "frame_count": self.steps + 1, "item_count": self.robot_count * (self.steps + 1)})
        body_sha = self.body_hash.hexdigest()
        _stream_update(self.contact_hash, {"record": "contacts_and_clearance_end", "contact_frame_count": self.steps, "contact_item_count": summary["event_counts"]["contact_items"], "clearance_frame_count": self.steps + 1})
        contact_sha = self.contact_hash.hexdigest()
        _stream_update(self.mission_hash, {"record": "mission_terminal_frame_ref", "frame_sha256": mission_terminal_sha})
        _stream_update(self.mission_hash, {"record": "mission_end", "boundary_frame_count": self.horizon + 1, "terminal_frame_count": 1})
        mission_sha = self.mission_hash.hexdigest()
        digests = _ordered_keys(self.source, self.line, terminal["domain_digests"], DOMAIN_KEYS, "$.domain_digests")
        expected = dict(zip(DOMAIN_KEYS, (command_sha, body_sha, contact_sha, mission_sha)))
        for key in DOMAIN_KEYS:
            _sha(self.source, self.line, digests[key], f"$.domain_digests.{key}")
            _expect(self.source, self.line, digests[key] == expected[key], "recomputed canonical stream digest mismatch", f"$.domain_digests.{key}")
        stream = summary["stream_digests"]
        for key in DOMAIN_KEYS[:3]:
            _expect(self.source, self.line, stream[key] == digests[key], "summary stream digest differs from terminal domain digest", f"$.summary.stream_digests.{key}")
        _expect(self.source, self.line, terminal["preterminal_payload_domain"] == PAYLOAD_DOMAIN, "preterminal payload domain differs", "$.preterminal_payload_domain")
        _expect(self.source, self.line, terminal["preterminal_payload_bytes"] == self.payload_bytes and type(terminal["preterminal_payload_bytes"]) is int, "preterminal payload byte count mismatch", "$.preterminal_payload_bytes")
        _expect(self.source, self.line, _sha(self.source, self.line, terminal["preterminal_payload_sha256"], "$.preterminal_payload_sha256") == self.payload_hash.hexdigest(), "preterminal payload SHA-256 mismatch", "$.preterminal_payload_sha256")
        terminal_sha = _sha(self.source, self.line, terminal["terminal_semantic_sha256"], "$.terminal_semantic_sha256")
        _expect(self.source, self.line, terminal_sha == semantic_sha256(TERMINAL_DOMAIN, _without_last(terminal, "terminal_semantic_sha256")), "terminal semantic SHA-256 mismatch", "$.terminal_semantic_sha256")
        self.terminal = terminal

    def _validate_summary(self, value: Any) -> dict[str, Any]:
        keys = (
            "record", "end", "engine_id", "common_scene_semantic_sha256",
            "mission_semantic_sha256", "run_class", "robot_count",
            "controller_ticks_planned", "controller_ticks_completed",
            "physics_steps_planned", "physics_steps_completed",
            "measured_boundary_publications", "event_counts", "stream_digests",
            "robots", "all_strict_mission_success",
            "total_full_horizon_physical_path_length_m", "total_route_upper_length_mm",
            "scene_route_efficiency_ratio", "derived_metrics_v1", "engine_metrics",
            "semantic_gaps",
        )
        summary = _ordered_keys(self.source, self.line, value, keys, "$.summary")
        _expect(self.source, self.line, summary["record"] == "mission_terminal" and summary["end"] is True, "mission terminal record/end differs", "$.summary.record")
        _expect(self.source, self.line, summary["engine_id"] == self.inputs["engine_id"], "summary engine_id differs from common scene", "$.summary.engine_id")
        common_sha = semantic_sha256(COMMON_SCENE_DOMAIN, self.header["initialization"]["common_scene"])
        _expect(self.source, self.line, _sha(self.source, self.line, summary["common_scene_semantic_sha256"], "$.summary.common_scene_semantic_sha256") == common_sha, "common-scene semantic SHA-256 mismatch", "$.summary.common_scene_semantic_sha256")
        _expect(self.source, self.line, summary["mission_semantic_sha256"] == self.inputs["mission_semantic_sha256"], "summary mission SHA differs from common scene", "$.summary.mission_semantic_sha256")
        expected_scalars = {
            "run_class": self.run_class,
            "robot_count": self.robot_count,
            "controller_ticks_planned": self.horizon,
            "controller_ticks_completed": self.horizon,
            "physics_steps_planned": self.steps,
            "physics_steps_completed": self.steps,
            "measured_boundary_publications": self.horizon + 1,
        }
        for key, expected in expected_scalars.items():
            _expect(self.source, self.line, summary[key] == expected and type(summary[key]) is type(expected), "summary exact-H accounting differs", f"$.summary.{key}")
        count_keys = (
            "boundary_frames", "command_frames", "command_items", "substep_frames",
            "body_frames", "body_items", "contact_frames", "contact_items",
            "clearance_samples",
        )
        counts = _ordered_keys(self.source, self.line, summary["event_counts"], count_keys, "$.summary.event_counts")
        expected_counts = {
            "boundary_frames": self.horizon + 1,
            "command_frames": self.horizon,
            "command_items": self.robot_count * self.horizon,
            "substep_frames": self.steps,
            "body_frames": self.steps + 1,
            "body_items": self.robot_count * (self.steps + 1),
            "contact_frames": self.steps,
            "clearance_samples": self.steps + 1,
        }
        for key, expected in expected_counts.items():
            _expect(self.source, self.line, counts[key] == expected and type(counts[key]) is int, "terminal event count differs", f"$.summary.event_counts.{key}")
        contact_items = _u64(self.source, self.line, counts["contact_items"], "$.summary.event_counts.contact_items")
        locator_contacts = sum(dict(locator.counts).get("contact_count", 0) for locator in self.locators)
        _expect(self.source, self.line, contact_items == locator_contacts, "terminal contact item count differs from substep locators", "$.summary.event_counts.contact_items")
        stream = _ordered_keys(self.source, self.line, summary["stream_digests"], DOMAIN_KEYS[:3], "$.summary.stream_digests")
        for key in DOMAIN_KEYS[:3]:
            _sha(self.source, self.line, stream[key], f"$.summary.stream_digests.{key}")
        robots = summary["robots"]
        if not isinstance(robots, list) or len(robots) != self.robot_count:
            raise EvidenceFormatError(self.source, "terminal robots must contain N entries", line=self.line, path="$.summary.robots")
        all_strict = self.run_class == "physics-route-mission-v1"
        total_path = 0.0
        total_route = 0
        for rid, raw_robot in enumerate(robots):
            path = f"$.summary.robots[{rid}]"
            robot = _ordered_keys(
                self.source,
                self.line,
                raw_robot,
                (
                    "id", "observed_goal_controller_tick", "strict_mission_success",
                    "endpoint_h_goal_censored", "fixed_work_goal_diagnostic_only",
                    "route_upper_length_mm", "full_horizon_physical_path_length_m",
                    "route_efficiency_ratio",
                ),
                path,
            )
            _expect(self.source, self.line, robot["id"] == rid and type(robot["id"]) is int, "terminal robot IDs must be exact ordered 0..N-1", f"{path}.id")
            goal = None if robot["observed_goal_controller_tick"] is None else _u64(self.source, self.line, robot["observed_goal_controller_tick"], f"{path}.observed_goal_controller_tick")
            _expect(self.source, self.line, goal is None or goal <= self.horizon, "terminal goal tick exceeds H", f"{path}.observed_goal_controller_tick")
            if self.detail == "replay":
                assert self.prior_goals is not None
                _expect(self.source, self.line, goal == self.prior_goals[rid], "terminal goal tick differs from boundary-H", f"{path}.observed_goal_controller_tick")
            strict = self.run_class == "physics-route-mission-v1" and goal is not None and goal < self.horizon
            all_strict = all_strict and strict
            _expect(self.source, self.line, robot["strict_mission_success"] is strict, "strict scientific success requires route class and goal tick < H", f"{path}.strict_mission_success")
            _expect(self.source, self.line, robot["endpoint_h_goal_censored"] is (goal == self.horizon), "endpoint-H diagnostic must mean observed exactly at H", f"{path}.endpoint_h_goal_censored")
            _expect(self.source, self.line, robot["fixed_work_goal_diagnostic_only"] is (self.run_class == "physics-fixed-work-v1"), "fixed-work goals must remain diagnostic only", f"{path}.fixed_work_goal_diagnostic_only")
            route = self.missions[rid]["route_upper_length_mm"]
            _expect(self.source, self.line, robot["route_upper_length_mm"] == route and type(robot["route_upper_length_mm"]) is int, "route upper length differs from common scene", f"{path}.route_upper_length_mm")
            recorded_path = _canonical_nonnegative_f64(self.source, self.line, robot["full_horizon_physical_path_length_m"], f"{path}.full_horizon_physical_path_length_m")
            if self.detail == "replay":
                assert self.path_lengths is not None
                _expect(self.source, self.line, _float_close_bits(robot["full_horizon_physical_path_length_m"], self.path_lengths[rid]), "full-H path length formula mismatch", f"{path}.full_horizon_physical_path_length_m")
            expected_ratio = recorded_path / (route / 1000.0) if strict and route != 0 else None
            if expected_ratio is None:
                _expect(self.source, self.line, robot["route_efficiency_ratio"] is None, "route efficiency must be null unless strict success and nonzero route", f"{path}.route_efficiency_ratio")
            else:
                _canonical_nonnegative_f64(self.source, self.line, robot["route_efficiency_ratio"], f"{path}.route_efficiency_ratio")
                _expect(self.source, self.line, robot["route_efficiency_ratio"] == f64_bits(expected_ratio), "per-robot route efficiency must be exact full-H path / route upper length", f"{path}.route_efficiency_ratio")
            total_path += recorded_path
            total_route += route
        _expect(self.source, self.line, summary["all_strict_mission_success"] is all_strict, "scene strict success requires route class and every robot goal tick < H", "$.summary.all_strict_mission_success")
        _canonical_nonnegative_f64(self.source, self.line, summary["total_full_horizon_physical_path_length_m"], "$.summary.total_full_horizon_physical_path_length_m")
        _expect(self.source, self.line, summary["total_full_horizon_physical_path_length_m"] == f64_bits(total_path), "scene path total must be exact ordered sum of per-robot full-H paths", "$.summary.total_full_horizon_physical_path_length_m")
        _expect(self.source, self.line, summary["total_route_upper_length_mm"] == total_route and type(summary["total_route_upper_length_mm"]) is int, "scene route total differs", "$.summary.total_route_upper_length_mm")
        scene_ratio = total_path / (total_route / 1000.0) if all_strict and total_route != 0 else None
        if scene_ratio is None:
            _expect(self.source, self.line, summary["scene_route_efficiency_ratio"] is None, "scene route efficiency must be null unless all strict and total route nonzero", "$.summary.scene_route_efficiency_ratio")
        else:
            _canonical_nonnegative_f64(self.source, self.line, summary["scene_route_efficiency_ratio"], "$.summary.scene_route_efficiency_ratio")
            _expect(self.source, self.line, summary["scene_route_efficiency_ratio"] == f64_bits(scene_ratio), "scene route efficiency must be exact ratio of sums, never mean of ratios", "$.summary.scene_route_efficiency_ratio")
        derived_keys = (
            "controller_linear_saturation_robot_ticks",
            "controller_angular_saturation_robot_ticks",
            "tracking_sample_count",
            "linear_command_tracking_error_rms_m_s",
            "angular_command_tracking_error_rms_rad_s",
            "maximum_linear_command_tracking_error_m_s",
            "maximum_angular_command_tracking_error_rad_s",
            "minimum_post_solve_pair_clearance_m",
            "minimum_post_solve_obstacle_clearance_m",
            "pair_contact_exposure_from_active_substeps_s",
            "obstacle_contact_exposure_from_active_substeps_s",
        )
        derived = _ordered_keys(self.source, self.line, summary["derived_metrics_v1"], derived_keys, "$.summary.derived_metrics_v1")
        for key in derived_keys[:3]:
            _u64(self.source, self.line, derived[key], f"$.summary.derived_metrics_v1.{key}")
        for key in derived_keys[3:7] + derived_keys[9:]:
            _canonical_nonnegative_f64(self.source, self.line, derived[key], f"$.summary.derived_metrics_v1.{key}")
        terminal_pair_clearance = _nullable_f64(self.source, self.line, derived["minimum_post_solve_pair_clearance_m"], "$.summary.derived_metrics_v1.minimum_post_solve_pair_clearance_m")
        terminal_obstacle_clearance = _nullable_f64(self.source, self.line, derived["minimum_post_solve_obstacle_clearance_m"], "$.summary.derived_metrics_v1.minimum_post_solve_obstacle_clearance_m")
        _expect(self.source, self.line, (terminal_pair_clearance is None) == (self.robot_count < 2), "terminal pair clearance is null iff N < 2", "$.summary.derived_metrics_v1.minimum_post_solve_pair_clearance_m")
        _expect(self.source, self.line, (terminal_obstacle_clearance is None) == (len(self.segment_ids) == 0), "terminal obstacle clearance is null iff no static segments exist", "$.summary.derived_metrics_v1.minimum_post_solve_obstacle_clearance_m")
        _expect(self.source, self.line, derived["tracking_sample_count"] == self.robot_count * self.steps, "tracking samples must be N*10H", "$.summary.derived_metrics_v1.tracking_sample_count")
        _expect(self.source, self.line, derived["controller_linear_saturation_robot_ticks"] <= self.robot_count * self.horizon and derived["controller_angular_saturation_robot_ticks"] <= self.robot_count * self.horizon, "controller saturation robot-tick count exceeds N*H", "$.summary.derived_metrics_v1.controller_linear_saturation_robot_ticks")
        engine_metrics = _validate_metrics(self.source, self.line, summary["engine_metrics"], "$.summary.engine_metrics")
        _expect(self.source, self.line, engine_metrics["physics_steps"] == self.steps and engine_metrics["motion_sample_count"] == self.robot_count * self.steps, "terminal engine step/motion counts differ", "$.summary.engine_metrics.physics_steps")
        _expect(self.source, self.line, engine_metrics["accepted_dynamic_contacts"] + engine_metrics["accepted_static_contacts"] == contact_items and engine_metrics["pair_active_contact_substeps"] == engine_metrics["accepted_dynamic_contacts"] and engine_metrics["obstacle_active_contact_substeps"] == engine_metrics["accepted_static_contacts"], "terminal engine contact totals differ from canonical contact item accounting", "$.summary.engine_metrics.accepted_dynamic_contacts")
        _expect(self.source, self.line, engine_metrics["maximum_linear_command_tracking_error_m_s"] == derived["maximum_linear_command_tracking_error_m_s"] and engine_metrics["maximum_angular_command_tracking_error_rad_s"] == derived["maximum_angular_command_tracking_error_rad_s"], "engine tracking maxima must equal canonical derived maxima bitwise", "$.summary.engine_metrics.maximum_linear_command_tracking_error_m_s")
        dt = f64_from_bits(self.inputs["settings"]["physics_timestep_s"])
        _expect(self.source, self.line, derived["pair_contact_exposure_from_active_substeps_s"] == f64_bits(engine_metrics["pair_active_contact_substeps"] * dt), "canonical pair exposure must be exact active count * fixed dt once", "$.summary.derived_metrics_v1.pair_contact_exposure_from_active_substeps_s")
        _expect(self.source, self.line, derived["obstacle_contact_exposure_from_active_substeps_s"] == f64_bits(engine_metrics["obstacle_active_contact_substeps"] * dt), "canonical obstacle exposure must be exact active count * fixed dt once", "$.summary.derived_metrics_v1.obstacle_contact_exposure_from_active_substeps_s")
        if self.detail == "replay":
            _expect(self.source, self.line, derived["controller_linear_saturation_robot_ticks"] == self.controller_linear_saturation and derived["controller_angular_saturation_robot_ticks"] == self.controller_angular_saturation, "controller saturation must count raw-vs-clamped bit changes at robot ticks", "$.summary.derived_metrics_v1.controller_linear_saturation_robot_ticks")
            _expect(self.source, self.line, self.tracking_samples == self.robot_count * self.steps, "internal replay tracking sample count differs", "$.summary.derived_metrics_v1.tracking_sample_count")
            linear_rms = math.sqrt(self.linear_error_squared_sum / self.tracking_samples)
            angular_rms = math.sqrt(self.angular_error_squared_sum / self.tracking_samples)
            for key, computed in (
                ("linear_command_tracking_error_rms_m_s", linear_rms),
                ("angular_command_tracking_error_rms_rad_s", angular_rms),
                ("maximum_linear_command_tracking_error_m_s", self.max_linear_error),
                ("maximum_angular_command_tracking_error_rad_s", self.max_angular_error),
            ):
                _expect(self.source, self.line, _float_close_bits(derived[key], computed), "replayed command-tracking formula mismatch", f"$.summary.derived_metrics_v1.{key}")
            expected_pair = None if self.min_pair_clearance is None else self.min_pair_clearance[0]
            expected_obstacle = None if self.min_obstacle_clearance is None else self.min_obstacle_clearance[0]
            _expect(self.source, self.line, derived["minimum_post_solve_pair_clearance_m"] == expected_pair and derived["minimum_post_solve_obstacle_clearance_m"] == expected_obstacle, "terminal minima must include clearance sample 0 and every post-substep sample", "$.summary.derived_metrics_v1.minimum_post_solve_pair_clearance_m")
            _expect(self.source, self.line, engine_metrics["accepted_dynamic_contacts"] == self.dynamic_contacts and engine_metrics["accepted_static_contacts"] == self.static_contacts and engine_metrics["pair_active_contact_substeps"] == self.dynamic_contacts and engine_metrics["obstacle_active_contact_substeps"] == self.static_contacts, "terminal engine contact totals differ from replay leaves", "$.summary.engine_metrics.accepted_dynamic_contacts")
            _expect(self.source, self.line, engine_metrics["pair_contact_events"] == self.pair_events and engine_metrics["obstacle_contact_events"] == self.obstacle_events, "terminal contact transition totals differ from replay leaves", "$.summary.engine_metrics.pair_contact_events")
            _expect(self.source, self.line, self.final_cumulative_metrics == engine_metrics, "terminal engine metrics differ bitwise from final cumulative substep observer", "$.summary.engine_metrics")
        gaps = summary["semantic_gaps"]
        _expect(self.source, self.line, isinstance(gaps, list) and tuple(gaps) == SEMANTIC_GAPS, "semantic_gaps vocabulary/order differs", "$.summary.semantic_gaps")
        return summary


def parse_physics_evidence_v1(path: os.PathLike[str] | str) -> EvidenceTrace:
    """Verify one complete artifact and return bounded comparison metadata."""
    # Make the spelling absolute without resolving a final symlink; O_NOFOLLOW
    # must observe and reject that final component rather than being bypassed by
    # Path.resolve().
    expanded = Path(path).expanduser()
    source = Path(os.path.abspath(expanded))
    return _Verifier(source).parse()


def read_verified_event(trace: EvidenceTrace, event_index: int) -> dict[str, Any]:
    """Reopen one already-verified event for replay leaf localization.

    The compact trace object stores only offsets and hashes.  This bounded second
    pass avoids retaining a potentially multi-gigabyte replay in memory.
    """
    if not 0 <= event_index < len(trace.event_locators):
        raise IndexError("event index is outside verified locator inventory")
    locator = trace.event_locators[event_index]
    descriptor, status = _secure_open(trace.source)
    try:
        if (
            status.st_dev != trace.device
            or status.st_ino != trace.inode
            or status.st_size != trace.byte_count
            or status.st_mtime_ns != trace.mtime_ns
            or status.st_ctime_ns != trace.ctime_ns
        ):
            raise EvidenceFormatError(trace.source, "artifact changed since verification")
        os.lseek(descriptor, locator.byte_offset, os.SEEK_SET)
        raw = b""
        remaining = locator.byte_length
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            raw += chunk
            remaining -= len(chunk)
    finally:
        os.close(descriptor)
    if len(raw) != locator.byte_length:
        raise EvidenceFormatError(trace.source, "could not reread verified event bytes", line=locator.line_number)
    value = _read_json_line(trace.source, raw, locator.line_number)
    _expect(trace.source, locator.line_number, value.get("semantic_sha256") == locator.semantic_sha256, "reread event differs from verified semantic locator", "$.semantic_sha256")
    if trace.detail != "replay":
        raise EvidenceFormatError(
            trace.source,
            "compact digest events cannot authenticate omitted leaves on reread",
            line=locator.line_number,
        )
    _expect(
        trace.source,
        locator.line_number,
        semantic_sha256(EVENT_DOMAIN, _without_last(value, "semantic_sha256"))
        == locator.semantic_sha256,
        "reread replay event semantic SHA-256 mismatch",
        "$.semantic_sha256",
    )
    event = value.get("event")
    if event == "commands":
        frame = {
            key: value[key]
            for key in ("event", "tick", "simulated_time_ms", "record_count", "records")
        }
        _expect(
            trace.source,
            locator.line_number,
            semantic_sha256(EVENT_DOMAIN, frame) == value["command_frame_sha256"],
            "reread command frame SHA-256 mismatch",
            "$.command_frame_sha256",
        )
    elif event == "boundary":
        for field, hash_field in (
            ("body_state_frame", "body_frame_sha256"),
            ("clearance_frame", "clearance_frame_sha256"),
            ("mission", "mission_frame_sha256"),
        ):
            if value[field] is not None:
                _expect(
                    trace.source,
                    locator.line_number,
                    semantic_sha256(EVENT_DOMAIN, value[field]) == value[hash_field],
                    f"reread {field} SHA-256 mismatch",
                    f"$.{hash_field}",
                )
    elif event == "substep":
        for field, hash_field in (
            ("body_state_frame", "body_frame_sha256"),
            ("contact_frame", "contact_frame_sha256"),
            ("clearance_frame", "clearance_frame_sha256"),
        ):
            _expect(
                trace.source,
                locator.line_number,
                semantic_sha256(EVENT_DOMAIN, value[field]) == value[hash_field],
                f"reread {field} SHA-256 mismatch",
                f"$.{hash_field}",
            )
    else:
        raise EvidenceFormatError(
            trace.source,
            "reread locator has an unknown replay event type",
            line=locator.line_number,
        )
    return value


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Strictly verify planar physics evidence v1")
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--pretty", action="store_true", help="pretty-print the JSON verification report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_cli().parse_args(argv)
    try:
        trace = parse_physics_evidence_v1(args.artifact)
    except (EvidenceFormatError, OSError) as error:
        print(json.dumps({"valid": False, "error": str(error)}, separators=(",", ":")), file=sys.stderr)
        return 2
    print(json.dumps(trace.public_report(), indent=2 if args.pretty else None, separators=None if args.pretty else (",", ":"), sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
