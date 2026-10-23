#!/usr/bin/env python3
"""Compile a nonauthorizing Track-B calibration epoch candidate in memory.

The compiler accepts already-supplied records.  It never reads a result,
launches a process, builds software, accesses a network, or writes a file.  The
candidate-publication surface is categorically disabled at this implementation
checkpoint; a later registration checkpoint must supply a newly qualified
publisher.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from typing import Any, Mapping


DESIGN_SCHEMA = "dancers-headless-epoch-design-v1"
MISSION_DESIGN_SCHEMA = "dancers-headless-mission-design-v1"
MISSION_RECEIPT_SCHEMA = "dancers-headless-mission-manifest-v1"
BINARY_ATTESTATION_SCHEMA = "dancers-headless-binary-attestation-v1"
PROTOCOL_RECORD_SCHEMA = "dancers-headless-protocol-record-v1"
RUNNER_RECORD_SCHEMA = "dancers-headless-runner-record-v1"
ANALYZER_RECORD_SCHEMA = "dancers-headless-analyzer-record-v1"
GEOMETRY_RECORD_SCHEMA = "dancers-headless-geometry-record-v1"
FREEZER_RECORD_SCHEMA = "dancers-headless-freezer-record-v1"
MISSION_COMPILER_RECORD_SCHEMA = "dancers-headless-mission-compiler-record-v1"
BINARY_ATTESTER_RECORD_SCHEMA = "dancers-headless-binary-attester-record-v1"
EPOCH_SCHEMA = "dancers-headless-calibration-epoch-candidate-v1"
TRACK_ID = "simulator-demo-v1-headless-epoch-1"
EPOCH_ID = "simulator-demo-v1-headless-epoch-1-calibration-candidate"
FUTURE_PLAN_ROOT = "comparison/plans/simulator-demo/v1/headless-epoch-1"
PROTOCOL_SOURCE_PATH = "docs/simulator/HEADLESS_SCIENTIFIC_EPOCH_1_ADDENDUM.md"
MODULE_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PHASE = "calibration"
FAMILIES = (
    "open",
    "perfect_maze",
    "fractal_low",
    "fractal_medium",
    "fractal_high",
)
SEEDS = tuple(range(1, 11))
ROBOT_COUNTS = (8, 12, 16, 24, 32)
METHODS = ("reference", "optimized")
RUN_CLASSES = ("digest_fidelity", "positions_replay", "headless_timing")
PATTERNS = {
    "ABA": (("reference", "optimized"), ("optimized", "reference"), ("reference", "optimized")),
    "BAB": (("optimized", "reference"), ("reference", "optimized"), ("optimized", "reference")),
}
FIXED_TIMESTEPS = 100
TIMESTEP_SECONDS = 0.1
SIMULATED_SECONDS = 10.0
WARMUPS_PER_BINARY_MAP = 1
MEASURED_REPETITIONS = 3
POSITIONS_REPETITIONS = 1
REPETITIONS_BY_RUN_CLASS = {
    "digest_fidelity": MEASURED_REPETITIONS,
    "headless_timing": MEASURED_REPETITIONS,
    "positions_replay": POSITIONS_REPETITIONS,
}
MAX_TIMEOUT_SECONDS = 3_600
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_PATH_BYTES = 1_024
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
UTC_SECONDS = re.compile(
    r"^(?:19|20)[0-9]{2}-(?:0[1-9]|1[0-2])-"
    r"(?:0[1-9]|[12][0-9]|3[01])T(?:[01][0-9]|2[0-3]):"
    r"[0-5][0-9]:[0-5][0-9]Z$"
)
ENVIRONMENT_MANIFEST_SHA256 = (
    "2785458ea32028f8a59498650677d1aa4c9dfb749b5f1a80754577e1eb703589"
)
EFFECTIVE_RUNTIME_RASTERS = {
    "open": "c77b708cec16c2c0403d3c137b5ed4d134ff1955e7c76dcd5ff45303c5591a83",
    "perfect_maze": "84172817edac6e8bf8b7ce57a3b1ed773ac6d2aa5781b5139117dd4c42303d92",
    "fractal_low": "bc28e318234a1a10886085c2208d5a1137b520301edbd9e4801d97df2c0b7670",
    "fractal_medium": "7162bb8186ad7775d677171cd9fcf617e01fd3b8dc03c5f5414f1dbf6e067dcc",
    "fractal_high": "05d657d25d630a2ec061342817d7450bb10662f1066007cc5961e6778db080b3",
}
EFFECTIVE_RUNTIME_RASTER_ENCODING = (
    "row-major uint8 occupancy; 0=obstacle, 255=free; no row padding"
)
OBSERVER_MARKER_NAMES = (
    "DANCERS_REQUIRE_PROVENANCE",
    "DANCERS_RUN_METADATA",
    "DANCERS_SUMMARY_OUT",
    "DANCERS_TRACE_DETAIL",
    "DANCERS_TRACE_OUT",
    "dancers-comparison-trace-v3",
)

ATTESTATION_EVIDENCE_BOUNDARY = (
    "identity-only attestation of the byte-pinned manifest, Pixi lock, and existing Mach-O binaries",
    "source pins, applied heads, trees, and common-patch identities are authenticated manifest assertions, not a fresh live Git checkout proof",
    "headless display and drawing off are mandatory runtime constraints and are not proven by this binary-only attestation",
    "no build, simulator, scientific process, network access, plan write, or result write occurred",
)

ATTESTED_BUILD_MANIFEST = {
    "bytes": 9_718,
    "path": "comparison/build_manifest.json",
    "schema_version": "dancers-comparison-build-manifest-v1",
    "scope": "local-only source-pinned Release trace-v3 builds; no calibration or validation result",
    "sha256": "57a154c7a366583ff4a6cd742d1e586575899baef823544feef2b606aad142d0",
    "status": "dual-build-parity-passed-smoke-fidelity-only",
}

ATTESTED_PIXI_LOCK = {
    "bytes": 31_051,
    "path": "pixi.lock",
    "sha256": "636ecf1cf31431ffc19f453fef39299c3243718477e321fcea8814225bfe68be",
}

ATTESTED_SOURCE_IDENTITY = {
    "common_patch": {
        "commit": "26c8c6f9b262a2114ac28203419cb03caf8b0d88",
        "format_email_sha256": "2ab81c9a9621c89de5854bbd8496cb2124845733140a913855ca61677185a95e",
        "full_index_diff_sha256": "5641cb37493f2c62618c43301459d7ce5e33c95101d344ba3157a9ae478de3f8",
        "history": [
            {
                "format_email_sha256": "feba83f12d25f22ba04857b82dee514e75e8136cff8416f872e83cf0fb41359b",
                "full_index_diff_sha256": "0eb4722ce67646d75c85fcd20b66bb7f62b019601db613af052e25774248edfc",
                "optimized_commit": "a992bee99ce64ae6548698ddcc48811e50fa47e7",
                "purpose": "common comparison correctness and observer layer",
            },
            {
                "format_email_sha256": "629e50f9dada3e2f95808a29aada750c53bc7fb21c7f85f99bbc08aac7e56df1",
                "full_index_diff_sha256": "12d1d18f6e6a923c74604ef5cbc54fb3e69cb08907aab80721ef8585959d1c2a",
                "optimized_commit": "91f5f05735ba249c657bb8789597635f0755d74f",
                "purpose": "planning-only result logging bounds correction",
            },
            {
                "format_email_sha256": "2ab81c9a9621c89de5854bbd8496cb2124845733140a913855ca61677185a95e",
                "full_index_diff_sha256": "5641cb37493f2c62618c43301459d7ce5e33c95101d344ba3157a9ae478de3f8",
                "optimized_commit": "26c8c6f9b262a2114ac28203419cb03caf8b0d88",
                "purpose": "compact trace-v3 semantic observer and storage correction",
            },
        ],
    },
    "optimized": {
        "applied_head": "26c8c6f9b262a2114ac28203419cb03caf8b0d88",
        "source_pin": "99364a41f1535a4bb0eddbe5a10030127ea77086",
        "tree": "b6f713f47ea3aa57c824cf2c481f3647ac7c9408",
    },
    "reference": {
        "applied_head": "de0f60d1ad4b944c2b3264b8e3ccd348708b8b8a",
        "source_pin": "986f47deec5b079663c7d7f6120bfc20f0467901",
        "tree": "cb93386e28f3824ea7ffc17605b441b92f13f87d",
    },
}

ATTESTED_BINARIES = {
    "reference_fidelity": {
        "algorithm_revision": "986f47deec5b079663c7d7f6120bfc20f0467901",
        "applied_head": "de0f60d1ad4b944c2b3264b8e3ccd348708b8b8a",
        "build_flavor": "fidelity",
        "bytes": 2_419_320,
        "method": "reference",
        "path": "build/comparison/trace-v3/build/reference-fidelity/dancers",
        "run_classes": ["digest_fidelity", "positions_replay"],
        "sha256": "45d894ae4ef5212672347b9e8351e366e47cc1eb809f8cb48900263624a03f6e",
        "source_dir": "build/comparison/trace-v3/reference-src",
    },
    "reference_timing": {
        "algorithm_revision": "986f47deec5b079663c7d7f6120bfc20f0467901",
        "applied_head": "de0f60d1ad4b944c2b3264b8e3ccd348708b8b8a",
        "build_flavor": "timing",
        "bytes": 2_182_408,
        "method": "reference",
        "path": "build/comparison/trace-v3/build/reference-timing/dancers",
        "run_classes": ["headless_timing"],
        "sha256": "a84e8fb74933f945cba131ebb11438e6666321fd0121ad09eef4a954e006ea68",
        "source_dir": "build/comparison/trace-v3/reference-src",
    },
    "optimized_fidelity": {
        "algorithm_revision": "99364a41f1535a4bb0eddbe5a10030127ea77086",
        "applied_head": "26c8c6f9b262a2114ac28203419cb03caf8b0d88",
        "build_flavor": "fidelity",
        "bytes": 2_403_032,
        "method": "optimized",
        "path": "build/comparison/trace-v3/build/optimized-fidelity/dancers",
        "run_classes": ["digest_fidelity", "positions_replay"],
        "sha256": "3886d9df72356d0dd71e94a95cea6998fc62b60401ed8701496e7f280bfaf081",
        "source_dir": "build/comparison/trace-v3/optimized-src",
    },
    "optimized_timing": {
        "algorithm_revision": "99364a41f1535a4bb0eddbe5a10030127ea77086",
        "applied_head": "26c8c6f9b262a2114ac28203419cb03caf8b0d88",
        "build_flavor": "timing",
        "bytes": 2_182_568,
        "method": "optimized",
        "path": "build/comparison/trace-v3/build/optimized-timing/dancers",
        "run_classes": ["headless_timing"],
        "sha256": "c060992bc411aab7b05ebbb9da30af34bc96f597bb8c6585024acf9892c6df30",
        "source_dir": "build/comparison/trace-v3/optimized-src",
    },
}

MISSION_CLASSIFICATION = {
    "algorithm_execution_during_compilation": False,
    "authorizes_scientific_execution": False,
    "network_access": False,
    "plan_freeze": False,
    "result_evidence": False,
    "validation_artifacts_read": False,
}

NONAUTHORIZING_CLASSIFICATION = {
    "benchmark_claim_ready": False,
    "benchmark_execution_authorized": False,
    "calibration_execution_authorized": False,
    "capacity_claim_ready": False,
    "execution_authorized": False,
    "fidelity_claim_ready": False,
    "plan_registered": False,
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
    "result_read": False,
    "result_written": False,
    "simulator_invoked": False,
    "subprocess_spawned": False,
}

BINARY_CLASSIFICATION = {
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

BINARY_OPERATIONS = {
    "build_invoked": False,
    "network_accessed": False,
    "plan_written": False,
    "result_written": False,
    "simulator_invoked": False,
}

FIXED_WORK = {
    "controller_timesteps": FIXED_TIMESTEPS,
    "simulated_seconds": SIMULATED_SECONDS,
    "timestep_seconds": TIMESTEP_SECONDS,
}

NORMALIZED_CONFIG = {
    "communications": {"failure_rate": 0.0, "radius_m": 20.0},
    "controller": {
        "damping": 0.0,
        "gbp_iterations_per_timestep": 2,
        "planning_horizon_seconds": 1.5,
        "sigma_factor_dynamics": 0.1,
        "sigma_factor_interrobot": 0.01,
        "sigma_factor_obstacle": 0.01,
        "sigma_factor_unicycle": 0.001,
        "sigma_variable_horizon": 0.01,
        "symmetric_interrobot_factors": False,
    },
    "motion": {
        "maximum_speed_m_per_second": 2.0,
        "robot_radius_m": 1.0,
        "turning_radius_m": 1.0,
    },
    "path_policy": {
        "map_cli_path": "repository-relative-canonical-no-whitespace",
        "scenario_cli_path": "repository-relative-canonical-no-whitespace",
    },
    "rendering": {
        "display": False,
        "draw_interrobot": False,
        "draw_path": False,
        "draw_trajectory": False,
        "draw_waypoints": False,
        "maximum_trajectory_length": 100,
        "screen_size_px": 800,
    },
    "schema_version": "dancers-headless-normalized-config-v1",
    "simulation": {
        "controller_timesteps": FIXED_TIMESTEPS,
        "log_results": True,
        "maximum_time_config_value": 100,
        "robot_model": "sphere",
        "scenario_mode": "file",
        "simulation_mode": 1,
        "timestep_seconds": TIMESTEP_SECONDS,
        "world_size_m": 100.0,
    },
}

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

CONTROLS = {
    "fresh_process_per_session": True,
    "measured_repetitions_per_method_scene": MEASURED_REPETITIONS,
    "method_order": "precomputed-balanced-ABA-BAB",
    "omp_dynamic": False,
    "omp_threads": 1,
    "omp_wait_policy": "passive",
    "power": "AC Power",
    "positions_repetitions_per_method_scene": POSITIONS_REPETITIONS,
    "runner_jobs": 1,
    "runtime_method_randomization": False,
    "serial_execution": True,
    "thermal_state": "nominal",
    "warmups_per_binary_map": WARMUPS_PER_BINARY_MAP,
}

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

FAILURE_POLICY = {
    "adaptive_stopping": False,
    "failed_attempts_retained": True,
    "full_finite_grid_required": True,
    "legacy_evidence_permitted": False,
    "missing_attempt_is_failure": True,
    "outlier_deletion": False,
    "runtime_rerandomization": False,
    "validation_or_audit_input_permitted": False,
}

OUTPUT_BASENAMES = {
    "digest_fidelity": (
        "attempt.json",
        "provenance.json",
        "summary.json",
        "trace.ndjson",
        "stdout.txt",
        "stderr.txt",
    ),
    "headless_timing": (
        "attempt.json",
        "provenance.json",
        "resource.json",
        "stdout.txt",
        "stderr.txt",
    ),
    "positions_replay": (
        "attempt.json",
        "provenance.json",
        "summary.json",
        "trace.ndjson",
        "stdout.txt",
        "stderr.txt",
    ),
    "warmup": ("attempt.json", "stdout.txt", "stderr.txt"),
}

PROTOCOL_CONTRACT = {
    "controls": CONTROLS,
    "failure_policy": FAILURE_POLICY,
    "fixed_work": FIXED_WORK,
    "run_classes": RUN_CLASS_CONSTRAINTS,
}

RUNNER_CONTRACT = {
    "accepted_phase": PHASE,
    "adaptive_stopping": False,
    "closed_output_inventory_required": True,
    "exclusive_output_create": True,
    "failed_attempts_retained": True,
    "fresh_process_per_session": True,
    "legacy_evidence_permitted": False,
    "precomputed_schedule_only": True,
    "runtime_method_randomization": False,
    "serial_execution": True,
    "supported_plan_schema": EPOCH_SCHEMA,
    "timing_requires_complete_digest_and_positions": True,
    "validation_or_audit_permitted": False,
}

ANALYZER_CONTRACT = {
    "adaptive_denominator": False,
    "digest_timing_separation_required": True,
    "exact_input_join_required": True,
    "full_grid_denominator_required": True,
    "legacy_evidence_permitted": False,
    "missing_attempt_is_failure": True,
    "outlier_deletion": False,
    "supported_plan_schema": EPOCH_SCHEMA,
    "timing_from_observer_disabled_only": True,
    "validation_or_audit_permitted": False,
}

GEOMETRY_CONTRACT = {
    "contact_is_unsafe": True,
    "continuous_swept_obstacle_union": True,
    "continuous_swept_pair_discs": True,
    "independent_from_controller_summary": True,
    "positions_trace_required": True,
    "supported_plan_schema": EPOCH_SCHEMA,
    "validation_or_audit_permitted": False,
}

FREEZER_CONTRACT = {
    "accepted_phase": PHASE,
    "candidate_publication_permitted": False,
    "canonical_in_memory_compile": True,
    "registered_plan_write_permitted": False,
    "result_read_permitted": False,
    "supported_output_schema": EPOCH_SCHEMA,
}

MISSION_COMPILER_CONTRACT = {
    "accepted_phase": PHASE,
    "algorithm_execution_during_compilation": False,
    "nested_prefix_mission_banks": True,
    "supported_manifest_schema": MISSION_RECEIPT_SCHEMA,
    "validation_or_audit_permitted": False,
}

BINARY_ATTESTER_CONTRACT = {
    "build_permitted": False,
    "live_byte_authentication": True,
    "simulator_launch_permitted": False,
    "supported_attestation_schema": BINARY_ATTESTATION_SCHEMA,
    "validation_or_audit_permitted": False,
}

COMPONENT_CLASSIFICATION = {
    "execution_authorized": False,
    "registered_scientific_run": False,
    "scientific_evidence": False,
    "timing_claim_ready": False,
    "validation_permitted": False,
}


class EpochFreezeError(ValueError):
    """A supplied record or compiled candidate violates the closed contract."""


@dataclass(frozen=True)
class EpochCompilation:
    value: dict[str, Any]
    data: bytes
    sha256: str


@dataclass(frozen=True)
class PublishedCandidate:
    path: Path
    bytes: int
    sha256: str

    def as_json(self) -> dict[str, Any]:
        return {"bytes": self.bytes, "path": str(self.path), "sha256": self.sha256}


class _DuplicateKey(ValueError):
    pass


def _reject_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value!r} is forbidden")


def canonical_json_bytes(value: Any) -> bytes:
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
        raise EpochFreezeError("value is not finite canonical JSON") from exc
    if len(raw) > MAX_OUTPUT_BYTES:
        raise EpochFreezeError("canonical epoch candidate exceeds the byte bound")
    return raw


def strict_json_bytes(raw: bytes, label: str) -> Any:
    if type(raw) is not bytes:
        raise EpochFreezeError(f"{label} must be bytes")
    try:
        return json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_reject_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError) as exc:
        raise EpochFreezeError(f"{label} is not strict UTF-8 JSON") from exc


def _exact_object(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise EpochFreezeError(f"{label} schema differs")
    return value


def _json_exact(value: Any, expected: Any) -> bool:
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


def _sha256(value: Any, label: str) -> str:
    if type(value) is not str or HEX64.fullmatch(value) is None:
        raise EpochFreezeError(f"{label} is not lowercase SHA-256")
    return value


def _safe_relative(value: Any, label: str) -> str:
    if type(value) is not str or not value or "\\" in value or "\x00" in value:
        raise EpochFreezeError(f"{label} is not a canonical relative path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in ("", ".", "..") for part in path.parts)
        or len(value.encode("utf-8")) > MAX_PATH_BYTES
    ):
        raise EpochFreezeError(f"{label} is not a canonical relative path")
    return value


def _identity(value: Any, label: str, *, keys: set[str] | None = None) -> dict[str, Any]:
    accepted = {"bytes", "path", "sha256"} if keys is None else keys
    record = _exact_object(value, accepted, label)
    if type(record["bytes"]) is not int or record["bytes"] <= 0:
        raise EpochFreezeError(f"{label}.bytes must be a positive integer")
    _safe_relative(record["path"], f"{label}.path")
    _sha256(record["sha256"], f"{label}.sha256")
    return record


def _record_identity(name: str, record: Mapping[str, Any]) -> dict[str, Any]:
    raw = canonical_json_bytes(record)
    return {
        "bytes": len(raw),
        "content": record,
        "path": f"{FUTURE_PLAN_ROOT}/records/{name}.json",
        "schema_version": record["schema_version"],
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _exact_value(value: Any, expected: Any, label: str) -> None:
    if not _json_exact(value, expected):
        raise EpochFreezeError(f"{label} differs")


def _finite_number(value: Any, label: str) -> float:
    if type(value) not in (int, float):
        raise EpochFreezeError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise EpochFreezeError(f"{label} must be a finite number") from exc
    if result != result or result in (float("inf"), float("-inf")):
        raise EpochFreezeError(f"{label} must be a finite number")
    return result


def normalize_design(design: Mapping[str, Any]) -> dict[str, Any]:
    value = _exact_object(
        design,
        {
            "family_grids",
            "normalized_config",
            "order_seed",
            "phase",
            "sampling_structure",
            "schema_version",
            "seed_roles",
            "timeout_seconds",
            "track_id",
        },
        "epoch design",
    )
    _exact_value(value["schema_version"], DESIGN_SCHEMA, "epoch design schema")
    _exact_value(value["track_id"], TRACK_ID, "epoch design track")
    if value["phase"] in ("validation", "audit"):
        raise EpochFreezeError("validation and audit epoch compilation is prohibited")
    _exact_value(value["phase"], PHASE, "epoch design phase")
    order_seed = _sha256(value["order_seed"], "epoch design order_seed")
    _exact_value(
        value["normalized_config"], NORMALIZED_CONFIG, "epoch normalized config"
    )
    _exact_value(value["seed_roles"], SEED_ROLES, "epoch seed roles")
    _exact_value(
        value["sampling_structure"],
        SAMPLING_STRUCTURE,
        "epoch sampling structure",
    )
    timeout = value["timeout_seconds"]
    if (
        type(timeout) is not int
        or timeout < 1
        or timeout > MAX_TIMEOUT_SECONDS
    ):
        raise EpochFreezeError("timeout_seconds must be a fixed bounded integer")
    grids = value["family_grids"]
    if type(grids) is not list or len(grids) != len(FAMILIES):
        raise EpochFreezeError("epoch design must contain exactly five family grids")
    normalized_by_family: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(grids):
        grid = _exact_object(
            item, {"counts", "family", "n0"}, f"family grid {index}"
        )
        family = grid["family"]
        if type(family) is not str or family not in FAMILIES:
            raise EpochFreezeError(f"family grid {index} family differs")
        if family in normalized_by_family:
            raise EpochFreezeError("family grid repeats a family")
        counts = grid["counts"]
        if (
            type(counts) is not list
            or not _json_exact(counts, list(ROBOT_COUNTS))
        ):
            raise EpochFreezeError(
                f"{family} count grid must equal the frozen protocol grid"
            )
        n0 = grid["n0"]
        if type(n0) is not int or n0 != ROBOT_COUNTS[0]:
            raise EpochFreezeError(f"{family} N0 must equal the frozen protocol N0")
        normalized_by_family[family] = {
            "counts": list(counts),
            "family": family,
            "n0": n0,
        }
    if set(normalized_by_family) != set(FAMILIES):
        raise EpochFreezeError("family grid inventory is incomplete")
    return {
        "family_grids": [normalized_by_family[family] for family in FAMILIES],
        "normalized_config": json.loads(json.dumps(NORMALIZED_CONFIG)),
        "order_seed": order_seed,
        "phase": PHASE,
        "sampling_structure": json.loads(json.dumps(SAMPLING_STRUCTURE)),
        "schema_version": DESIGN_SCHEMA,
        "seed_roles": json.loads(json.dumps(SEED_ROLES)),
        "timeout_seconds": timeout,
        "track_id": TRACK_ID,
    }


def _validate_static_diagnostic(value: Any, label: str) -> None:
    record = _exact_object(
        value,
        {
            "diagnostic_only",
            "observed_minimum_center_distance_m",
            "route_pair_count_below_threshold",
            "threshold_m",
        },
        label,
    )
    _exact_value(record["diagnostic_only"], True, f"{label}.diagnostic_only")
    _finite_number(
        record["observed_minimum_center_distance_m"],
        f"{label}.observed_minimum_center_distance_m",
    )
    if (
        type(record["route_pair_count_below_threshold"]) is not int
        or record["route_pair_count_below_threshold"] < 0
    ):
        raise EpochFreezeError(f"{label}.route_pair_count_below_threshold differs")
    if _finite_number(record["threshold_m"], f"{label}.threshold_m") <= 0.0:
        raise EpochFreezeError(f"{label}.threshold_m must be positive")


def _expected_mission_design_identity(
    design: Mapping[str, Any],
) -> dict[str, Any]:
    grids = {item["family"]: item for item in design["family_grids"]}
    mission_design = {
        "phase": PHASE,
        "requests": [
            {
                "counts": list(grids[family]["counts"]),
                "family": family,
                "seed": seed,
            }
            for family in FAMILIES
            for seed in SEEDS
        ],
        "schema_version": MISSION_DESIGN_SCHEMA,
    }
    try:
        raw = (
            json.dumps(
                mission_design,
                allow_nan=False,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8", "strict")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise EpochFreezeError("mission design is not canonical JSON") from exc
    return {
        "bytes": len(raw),
        "path": "design.json",
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _validate_mission_receipt(
    receipt: Mapping[str, Any], design: Mapping[str, Any]
) -> dict[tuple[str, int, int], dict[str, Any]]:
    value = _exact_object(
        receipt,
        {
            "artifacts",
            "classification",
            "design",
            "environment_manifest",
            "schema_version",
        },
        "mission receipt",
    )
    _exact_value(value["schema_version"], MISSION_RECEIPT_SCHEMA, "mission receipt schema")
    _exact_value(
        value["classification"], MISSION_CLASSIFICATION, "mission receipt classification"
    )
    mission_design = _identity(value["design"], "mission receipt design")
    _exact_value(
        mission_design,
        _expected_mission_design_identity(design),
        "mission receipt design identity",
    )
    environment = _identity(value["environment_manifest"], "mission environment manifest")
    _exact_value(
        environment["path"],
        "comparison/environments/manifest.json",
        "mission environment manifest path",
    )
    _exact_value(
        environment["sha256"],
        ENVIRONMENT_MANIFEST_SHA256,
        "mission environment manifest SHA-256",
    )
    artifacts = value["artifacts"]
    expected_pairs = [(family, seed) for family in FAMILIES for seed in SEEDS]
    if type(artifacts) is not list or len(artifacts) != len(expected_pairs):
        raise EpochFreezeError("mission receipt must contain all five families and ten seeds")
    grids = {item["family"]: item for item in design["family_grids"]}
    scenarios: dict[tuple[str, int, int], dict[str, Any]] = {}
    effective_seen: dict[tuple[str, int], set[str]] = {}
    maps_by_family: dict[str, dict[str, Any]] = {}
    bank_ids: set[str] = set()
    for index, (artifact, expected_pair) in enumerate(zip(artifacts, expected_pairs)):
        record = _exact_object(
            artifact,
            {
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
            },
            f"mission artifact {index}",
        )
        family, seed = expected_pair
        _exact_value(record["family"], family, f"mission artifact {index}.family")
        _exact_value(record["seed"], seed, f"mission artifact {index}.seed")
        _exact_value(record["phase"], PHASE, f"mission artifact {index}.phase")
        _exact_value(
            record["seed_roles"],
            SEED_ROLES,
            f"mission artifact {index}.seed_roles",
        )
        _exact_value(
            record["sampling_structure"],
            SAMPLING_STRUCTURE,
            f"mission artifact {index}.sampling_structure",
        )
        bank_id = _sha256(record["bank_id"], f"mission artifact {index}.bank_id")
        if bank_id in bank_ids:
            raise EpochFreezeError("mission bank ID repeats across family/seed units")
        bank_ids.add(bank_id)
        _identity(record["bank"], f"mission artifact {index}.bank")
        expected_bank_path = f"missions/{family}/seed-{seed:04d}/bank.json"
        _exact_value(
            record["bank"]["path"], expected_bank_path, f"mission artifact {index}.bank.path"
        )
        generator_seed = record["generator_seed_uint64"]
        if type(generator_seed) is not int or not 0 <= generator_seed < 1 << 64:
            raise EpochFreezeError(f"mission artifact {index} generator seed differs")
        registered_map = _identity(
            record["map"],
            f"mission artifact {index}.map",
            keys={"bytes", "path", "sha256", "size_px"},
        )
        _exact_value(registered_map["size_px"], 256, f"mission artifact {index}.map.size_px")
        _exact_value(
            registered_map["path"],
            f"comparison/environments/{family}/calibration/map.png",
            f"mission artifact {index}.map.path",
        )
        prior_map = maps_by_family.setdefault(family, dict(registered_map))
        if not _json_exact(prior_map, registered_map):
            raise EpochFreezeError(f"{family} map identity differs across seeds")
        _validate_static_diagnostic(
            record["static_inter_route_distance"],
            f"mission artifact {index}.static_inter_route_distance",
        )
        counts = grids[family]["counts"]
        _exact_value(record["max_robot_count"], counts[-1], f"mission artifact {index}.max_robot_count")
        rows = record["counts"]
        if type(rows) is not list or len(rows) != len(counts):
            raise EpochFreezeError(f"mission artifact {index} count inventory is incomplete")
        for count, row in zip(counts, rows):
            count_record = _exact_object(
                row,
                {"effective_scenario_sha256", "robot_count", "scenario"},
                f"mission artifact {index} count {count}",
            )
            _exact_value(
                count_record["robot_count"], count, f"mission artifact {index} robot_count"
            )
            effective = _sha256(
                count_record["effective_scenario_sha256"],
                f"mission artifact {index} effective scenario",
            )
            if effective in effective_seen.setdefault((family, count), set()):
                raise EpochFreezeError(
                    f"{family}/n-{count} reuses an effective scenario across seeds"
                )
            effective_seen[(family, count)].add(effective)
            scenario = _identity(
                count_record["scenario"], f"mission artifact {index} scenario n-{count}"
            )
            _exact_value(
                scenario["path"],
                f"missions/{family}/seed-{seed:04d}/n-{count}/scenario.json",
                f"mission artifact {index} scenario path",
            )
            scenarios[(family, count, seed)] = {
                "bank_id": bank_id,
                "effective_scenario_sha256": effective,
                "generator_seed_uint64": generator_seed,
                "map": dict(registered_map),
                "mission_seed": seed,
                "scenario": dict(scenario),
            }
    expected_scenarios = {
        (grid["family"], count, seed)
        for grid in design["family_grids"]
        for count in grid["counts"]
        for seed in SEEDS
    }
    if set(scenarios) != expected_scenarios:
        raise EpochFreezeError("mission receipt scenario matrix is incomplete")
    return scenarios


def _validate_binary_attestation(
    attestation: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    value = _exact_object(
        attestation,
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
    _exact_value(value["schema_version"], BINARY_ATTESTATION_SCHEMA, "binary attestation schema")
    _exact_value(value["track_id"], TRACK_ID, "binary attestation track")
    _exact_value(
        value["status"], "pass-identity-only-nonauthorizing", "binary attestation status"
    )
    _exact_value(value["classification"], BINARY_CLASSIFICATION, "binary classification")
    _exact_value(value["operations"], BINARY_OPERATIONS, "binary operations")
    _exact_value(
        value["run_class_constraints"], RUN_CLASS_CONSTRAINTS, "binary run classes"
    )
    observed_utc = value["observed_utc"]
    if type(observed_utc) is not str or UTC_SECONDS.fullmatch(observed_utc) is None:
        raise EpochFreezeError("binary attestation observed_utc differs")
    try:
        datetime.strptime(observed_utc, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise EpochFreezeError("binary attestation observed_utc is not real") from exc
    _exact_value(
        value["evidence_boundary"],
        list(ATTESTATION_EVIDENCE_BOUNDARY),
        "binary attestation evidence boundary",
    )
    _exact_value(
        value["build_manifest"],
        ATTESTED_BUILD_MANIFEST,
        "binary build manifest",
    )
    _exact_value(value["pixi_lock"], ATTESTED_PIXI_LOCK, "binary Pixi lock")
    _exact_value(
        value["source_identity"],
        ATTESTED_SOURCE_IDENTITY,
        "binary source identity",
    )
    expected_keys = {
        "reference_fidelity": (
            "reference",
            "fidelity",
            ["digest_fidelity", "positions_replay"],
            True,
        ),
        "reference_timing": (
            "reference",
            "timing",
            ["headless_timing"],
            False,
        ),
        "optimized_fidelity": (
            "optimized",
            "fidelity",
            ["digest_fidelity", "positions_replay"],
            True,
        ),
        "optimized_timing": (
            "optimized",
            "timing",
            ["headless_timing"],
            False,
        ),
    }
    binaries = _exact_object(value["binaries"], set(expected_keys), "binary inventory")
    result: dict[str, dict[str, Any]] = {}
    paths: set[str] = set()
    for key, expected in expected_keys.items():
        record = _exact_object(
            binaries[key],
            {
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
            },
            f"binary {key}",
        )
        method, flavor, run_classes, observer = expected
        for field, expected_value in {
            **ATTESTED_BINARIES[key],
            "build_flavor": flavor,
            "build_manifest_key": key,
            "method": method,
            "observer_compiled": observer,
            "run_classes": run_classes,
        }.items():
            _exact_value(record[field], expected_value, f"binary {key}.{field}")
        path = _safe_relative(record["path"], f"binary {key}.path")
        _safe_relative(record["source_dir"], f"binary {key}.source_dir")
        if path in paths:
            raise EpochFreezeError("binary paths are not distinct")
        paths.add(path)
        _exact_value(
            record["mach_o"],
            {
                "architecture": "arm64",
                "file_type": "executable",
                "format": "mach-o-64-little-endian",
            },
            f"binary {key}.mach_o",
        )
        markers = _exact_object(
            record["observer_marker_counts"],
            set(OBSERVER_MARKER_NAMES),
            f"binary {key}.observer_marker_counts",
        )
        marker_count = 1 if observer else 0
        if any(type(markers[name]) is not int or markers[name] != marker_count for name in OBSERVER_MARKER_NAMES):
            raise EpochFreezeError(f"binary {key} observer marker classification differs")
        result[key] = dict(record)
    return result


def _validate_component_record(
    record: Mapping[str, Any],
    *,
    schema: str,
    contract: Mapping[str, Any],
    source_path: str,
    label: str,
) -> None:
    phase_key = {"phase"} if schema == PROTOCOL_RECORD_SCHEMA else set()
    value = _exact_object(
        record,
        {"classification", "contract", "identity", "schema_version", "track_id"}
        | phase_key,
        label,
    )
    _exact_value(value["schema_version"], schema, f"{label} schema")
    _exact_value(value["track_id"], TRACK_ID, f"{label} track")
    if phase_key:
        if value["phase"] in ("validation", "audit"):
            raise EpochFreezeError(f"{label} validation/audit phase is prohibited")
        _exact_value(value["phase"], PHASE, f"{label} phase")
    identity = _identity(value["identity"], f"{label} identity")
    _exact_value(identity["path"], source_path, f"{label} source path")
    _exact_value(value["classification"], COMPONENT_CLASSIFICATION, f"{label} classification")
    _exact_value(value["contract"], contract, f"{label} contract")


def _assignment_score(order_seed: str, family: str, count: int, seed: int) -> bytes:
    material = "\0".join(
        ("dancers-headless-aba-bab-assignment-v1", order_seed, family, str(count), str(seed))
    ).encode("ascii")
    return hashlib.sha256(material).digest()


def _block_score(order_seed: str, family: str, count: int, seed: int) -> bytes:
    material = "\0".join(
        ("dancers-headless-block-order-v1", order_seed, family, str(count), str(seed))
    ).encode("ascii")
    return hashlib.sha256(material).digest()


def _semantic_id(domain: str, value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        domain.encode("ascii") + b"\0" + canonical_json_bytes(value)
    ).hexdigest()


def _render_config_bytes(
    *, scene_id: str, family: str, count: int, seed: int,
    map_path: str, scenario_path: str
) -> bytes:
    map_parent = PurePosixPath(map_path).parent.as_posix()
    map_name = PurePosixPath(map_path).name
    lines = [
        f"SIMULATION_NAME: {json.dumps(scene_id)}",
        "LOG_RESULTS: true",
        f"ASSETS_DIR: {map_parent}",
        f"OBSTACLE_FILE: {map_name}",
        "SCENARIO: file",
        f"SCENARIO_FILE: {scenario_path}",
        f"NUM_ROBOTS: {count}",
        "COMMUNICATION_RADIUS: 20",
        f"RNG_SEED: {seed}",
        "COMMS_FAILURE_RATE: 0.0",
        "",
        "WORLD_SZ: 100",
        "TIMESTEP: 0.1",
        "MAX_TIME: 100",
        "SIM_MODE: 1",
        "ROBOT_MODEL: sphere",
        "",
        "DISPLAY: 0",
        "SCREEN_SZ: 800",
        "DRAW_INTERROBOT: 0",
        "DRAW_PATH: 0",
        "DRAW_TRAJ: 0",
        "MAX_TRAJ_LEN: 100",
        "DRAW_WAYPOINTS: 0",
        "",
        "FACTORGRAPH_LAYERS:",
        "  - name: Planning",
        "    type: planning",
        "    params:",
        "      T_HORIZON: 1.5",
        "      ROBOT_RADIUS: 1.0",
        "      MAX_SPEED: 2.0",
        "      TURNING_RADIUS: 1.0",
        "      SIGMA_VARIABLE_HORIZON: 0.01",
        "      SIGMA_FACTOR_DYNAMICS: 0.1",
        "      SIGMA_FACTOR_UNICYCLE: 0.001",
        "      SIGMA_FACTOR_INTERROBOT: 0.01",
        "      SIGMA_FACTOR_OBSTACLE: 0.01",
        "",
        "NUM_ITERS: 2",
        "DAMPING: 0.0",
        "SYMMETRIC_INTERROBOT_FACTORS: false",
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _destination(
    *, run_class: str, family: str, count: int, seed: int, repetition: int,
    pair_position: int, method: str, warmup: bool
) -> str:
    run_directory = run_class
    if warmup:
        return f"calibration/{run_directory}/{family}/warmup/{method}"
    return (
        f"calibration/{run_directory}/{family}/n-{count}/seed-{seed:02d}/"
        f"rep-{repetition}/{pair_position}-{method}"
    )


def _binary_key_for(method: str, run_class: str) -> str:
    flavor = "timing" if run_class == "headless_timing" else "fidelity"
    return f"{method}_{flavor}"


def _scene_seed_roles(
    source: Mapping[str, Any], *, runtime_rng_seed: int
) -> dict[str, Any]:
    return {
        "mission_selection": {
            "active_via_distinct_scenario_bytes": True,
            "effective_scenario_sha256": source["effective_scenario_sha256"],
            "generator_seed_uint64": source["generator_seed_uint64"],
            "mission_seed": source["mission_seed"],
        },
        "runtime_rng": {
            "activity_assumed": False,
            "config_key": "RNG_SEED",
            "runtime_rng_seed": runtime_rng_seed,
        },
    }


def _scene_sampling_unit(
    *, family: str, count: int, mission_bank_id: str
) -> dict[str, Any]:
    clustering_unit_id = _semantic_id(
        "dancers-headless-family-mission-bank-cluster-v1",
        {"family": family, "mission_bank_id": mission_bank_id},
    )
    return {
        "clustering_unit_id": clustering_unit_id,
        "count_relationship": "nested-prefix",
        "family": family,
        "mission_bank_id": mission_bank_id,
        "prefix_robot_count": count,
    }


def _effective_runtime_raster(family: str) -> dict[str, Any]:
    return {
        "bytes": 1_000_000,
        "encoding": EFFECTIVE_RUNTIME_RASTER_ENCODING,
        "height_px": 1_000,
        "sha256": EFFECTIVE_RUNTIME_RASTERS[family],
        "source_height_px": 256,
        "source_width_px": 256,
        "width_px": 1_000,
    }


def _method_bindings(
    binaries: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        method: {
            "digest_fidelity": {
                "binary_key": f"{method}_fidelity",
                "bytes": binaries[f"{method}_fidelity"]["bytes"],
                "observer_compiled": True,
                "path": binaries[f"{method}_fidelity"]["path"],
                "sha256": binaries[f"{method}_fidelity"]["sha256"],
            },
            "positions_replay": {
                "binary_key": f"{method}_fidelity",
                "bytes": binaries[f"{method}_fidelity"]["bytes"],
                "observer_compiled": True,
                "path": binaries[f"{method}_fidelity"]["path"],
                "sha256": binaries[f"{method}_fidelity"]["sha256"],
                "trace_detail": "positions",
            },
            "headless_timing": {
                "binary_key": f"{method}_timing",
                "bytes": binaries[f"{method}_timing"]["bytes"],
                "display": False,
                "drawing": False,
                "observer_compiled": False,
                "path": binaries[f"{method}_timing"]["path"],
                "sha256": binaries[f"{method}_timing"]["sha256"],
            },
        }
        for method in METHODS
    }


def _slot(
    *, order_index: int, kind: str, run_class: str, family: str,
    count: int, seed: int, repetition: int, pattern: str,
    pair_position: int, method: str, scene: Mapping[str, Any],
    binary_key: str, binary: Mapping[str, Any], timeout: int
) -> dict[str, Any]:
    warmup = kind == "warmup"
    timed = run_class == "headless_timing" and not warmup
    trace_detail: str | None
    if warmup:
        trace_detail = None
    elif run_class == "digest_fidelity":
        trace_detail = "digest"
    elif run_class == "positions_replay":
        trace_detail = "positions"
    else:
        trace_detail = None
    destination = _destination(
        run_class=run_class,
        family=family,
        count=count,
        seed=seed,
        repetition=repetition,
        pair_position=pair_position,
        method=method,
        warmup=warmup,
    )
    core = {
        "binary_key": binary_key,
        "binary_sha256": binary["sha256"],
        "config": dict(scene["config"]),
        "controller_timesteps": FIXED_TIMESTEPS,
        "destination": destination,
        "effective_runtime_raster_sha256": scene["effective_runtime_raster"][
            "sha256"
        ],
        "family": family,
        "input_id": scene["input_id"],
        "kind": kind,
        "map_sha256": scene["map"]["sha256"],
        "method": method,
        "normalized_config_sha256": scene["normalized_config_sha256"],
        "outputs": list(OUTPUT_BASENAMES["warmup" if warmup else run_class]),
        "pair_position": pair_position,
        "pattern": pattern,
        "repetition": repetition,
        "robot_count": count,
        "run_class": run_class,
        "scene_id": scene["scene_id"],
        "seed": seed,
        "timed": timed,
        "timeout_seconds": timeout,
        "timestep_seconds": TIMESTEP_SECONDS,
        "trace_detail": trace_detail,
    }
    slot_id = _semantic_id("dancers-headless-slot-v1", core)
    return {"order_index": order_index, "slot_id": slot_id, **core}


def compile_epoch(
    *,
    design: Mapping[str, Any],
    mission_receipt: Mapping[str, Any],
    binary_attestation: Mapping[str, Any],
    protocol_record: Mapping[str, Any],
    runner_record: Mapping[str, Any],
    analyzer_record: Mapping[str, Any],
    geometry_record: Mapping[str, Any],
    freezer_record: Mapping[str, Any],
    mission_compiler_record: Mapping[str, Any],
    binary_attester_record: Mapping[str, Any],
) -> EpochCompilation:
    """Compile all literal slots and inventories without executing anything."""

    normalized = normalize_design(design)
    scenarios = _validate_mission_receipt(mission_receipt, normalized)
    binaries = _validate_binary_attestation(binary_attestation)
    _validate_component_record(
        protocol_record,
        schema=PROTOCOL_RECORD_SCHEMA,
        contract=PROTOCOL_CONTRACT,
        source_path=PROTOCOL_SOURCE_PATH,
        label="protocol record",
    )
    _validate_component_record(
        runner_record,
        schema=RUNNER_RECORD_SCHEMA,
        contract=RUNNER_CONTRACT,
        source_path="scripts/comparison/run_headless_epoch_v1.py",
        label="runner record",
    )
    _validate_component_record(
        analyzer_record,
        schema=ANALYZER_RECORD_SCHEMA,
        contract=ANALYZER_CONTRACT,
        source_path="scripts/comparison/analyze_headless_epoch_v1.py",
        label="analyzer record",
    )
    _validate_component_record(
        geometry_record,
        schema=GEOMETRY_RECORD_SCHEMA,
        contract=GEOMETRY_CONTRACT,
        source_path="scripts/comparison/headless_geometry_v1.py",
        label="geometry record",
    )
    _validate_component_record(
        freezer_record,
        schema=FREEZER_RECORD_SCHEMA,
        contract=FREEZER_CONTRACT,
        source_path="scripts/comparison/freeze_headless_epoch_v1.py",
        label="freezer record",
    )
    _validate_component_record(
        mission_compiler_record,
        schema=MISSION_COMPILER_RECORD_SCHEMA,
        contract=MISSION_COMPILER_CONTRACT,
        source_path="scripts/comparison/generate_headless_mission_banks_v1.py",
        label="mission compiler record",
    )
    _validate_component_record(
        binary_attester_record,
        schema=BINARY_ATTESTER_RECORD_SCHEMA,
        contract=BINARY_ATTESTER_CONTRACT,
        source_path="scripts/comparison/attest_headless_binaries_v1.py",
        label="binary attester record",
    )

    grids = {item["family"]: item for item in normalized["family_grids"]}
    scene_rows: list[dict[str, Any]] = []
    config_artifacts: list[dict[str, Any]] = []
    scene_by_key: dict[tuple[str, int, int], dict[str, Any]] = {}
    assignments: dict[tuple[str, int, int], str] = {}
    balance_rows: list[dict[str, Any]] = []
    for family in FAMILIES:
        grid = grids[family]
        for count in grid["counts"]:
            ranked = sorted(
                SEEDS,
                key=lambda seed: (
                    _assignment_score(normalized["order_seed"], family, count, seed),
                    seed,
                ),
            )
            for seed in ranked[: len(SEEDS) // 2]:
                assignments[(family, count, seed)] = "ABA"
            for seed in ranked[len(SEEDS) // 2 :]:
                assignments[(family, count, seed)] = "BAB"
            balance_rows.append(
                {
                    "aba_scene_count": len(SEEDS) // 2,
                    "bab_scene_count": len(SEEDS) // 2,
                    "family": family,
                    "measured_method_pair_counts": {
                        run_class: len(SEEDS) * repetitions
                        for run_class, repetitions in REPETITIONS_BY_RUN_CLASS.items()
                    },
                    "robot_count": count,
                }
            )
            for seed in SEEDS:
                source = scenarios[(family, count, seed)]
                scenario_identity = dict(source["scenario"])
                scenario_identity["path"] = (
                    f"{FUTURE_PLAN_ROOT}/{source['scenario']['path']}"
                )
                input_payload = {
                    "controller_timesteps": FIXED_TIMESTEPS,
                    "effective_runtime_raster_sha256": EFFECTIVE_RUNTIME_RASTERS[
                        family
                    ],
                    "effective_scenario_sha256": source[
                        "effective_scenario_sha256"
                    ],
                    "family": family,
                    "map_sha256": source["map"]["sha256"],
                    "mission_bank_id": source["bank_id"],
                    "mission_generator_seed_uint64": source[
                        "generator_seed_uint64"
                    ],
                    "mission_selection_seed": source["mission_seed"],
                    "phase": PHASE,
                    "robot_count": count,
                    "runtime_rng_seed": seed,
                    "scenario_sha256": scenario_identity["sha256"],
                    "timestep_seconds": TIMESTEP_SECONDS,
                }
                config_binding = {
                    "map_cli_path": source["map"]["path"],
                    "normalized_config": normalized["normalized_config"],
                    "num_robots": count,
                    "rng_seed": seed,
                    "scenario_cli_path": scenario_identity["path"],
                    "simulation_name": f"calibration-{family}-n-{count}-seed-{seed:02d}",
                }
                normalized_config_sha256 = _semantic_id(
                    "dancers-headless-normalized-scene-config-v1", config_binding
                )
                scene_id = f"calibration-{family}-n-{count}-seed-{seed:02d}"
                config_bytes = _render_config_bytes(
                    scene_id=scene_id,
                    family=family,
                    count=count,
                    seed=seed,
                    map_path=source["map"]["path"],
                    scenario_path=scenario_identity["path"],
                )
                config_identity = {
                    "bytes": len(config_bytes),
                    "path": (
                        f"{FUTURE_PLAN_ROOT}/configs/{family}/n-{count}/"
                        f"seed-{seed:02d}/config.yaml"
                    ),
                    "sha256": hashlib.sha256(config_bytes).hexdigest(),
                }
                config_artifacts.append(
                    {**config_identity, "content_utf8": config_bytes.decode("utf-8")}
                )
                input_payload["normalized_config_sha256"] = normalized_config_sha256
                input_payload["rendered_config_sha256"] = config_identity["sha256"]
                seed_roles = _scene_seed_roles(source, runtime_rng_seed=seed)
                sampling_unit = _scene_sampling_unit(
                    family=family,
                    count=count,
                    mission_bank_id=source["bank_id"],
                )
                row = {
                    "config": config_identity,
                    "config_binding": config_binding,
                    "effective_runtime_raster": _effective_runtime_raster(family),
                    "effective_scenario_sha256": source[
                        "effective_scenario_sha256"
                    ],
                    "family": family,
                    "input_id": _semantic_id(
                        "dancers-headless-exact-input-v1", input_payload
                    ),
                    "is_n0": count == grid["n0"],
                    "map": dict(source["map"]),
                    "normalized_config_sha256": normalized_config_sha256,
                    "pattern": assignments[(family, count, seed)],
                    "robot_count": count,
                    "sampling_unit": sampling_unit,
                    "scenario": scenario_identity,
                    "scene_id": scene_id,
                    "seed": seed,
                    "seed_roles": seed_roles,
                }
                scene_rows.append(row)
                scene_by_key[(family, count, seed)] = row

    method_bindings = _method_bindings(binaries)

    inventories: dict[str, Any] = {}
    measured_join_sets: dict[str, set[tuple[Any, ...]]] = {}
    timeout = normalized["timeout_seconds"]
    global_warmups: list[dict[str, Any]] = []
    warmup_order = 0
    for family in FAMILIES:
        grid = grids[family]
        scene = scene_by_key[(family, grid["n0"], SEEDS[0])]
        for binary_key in (
            "reference_fidelity",
            "reference_timing",
            "optimized_fidelity",
            "optimized_timing",
        ):
            method = binaries[binary_key]["method"]
            run_class = (
                "headless_timing" if binary_key.endswith("_timing") else "digest_fidelity"
            )
            global_warmups.append(
                _slot(
                    order_index=warmup_order,
                    kind="warmup",
                    run_class=run_class,
                    family=family,
                    count=grid["n0"],
                    seed=SEEDS[0],
                    repetition=0,
                    pattern="warmup",
                    pair_position=0,
                    method=method,
                    scene=scene,
                    binary_key=binary_key,
                    binary=binaries[binary_key],
                    timeout=timeout,
                )
            )
            warmup_order += 1
    for run_class in RUN_CLASSES:
        measured: list[dict[str, Any]] = []
        order_index = 0
        joins: set[tuple[Any, ...]] = set()
        for family in FAMILIES:
            for count in grids[family]["counts"]:
                ordered_seeds = sorted(
                    SEEDS,
                    key=lambda seed: (
                        _block_score(
                            normalized["order_seed"], family, count, seed
                        ),
                        seed,
                    ),
                )
                for seed in ordered_seeds:
                    pattern = assignments[(family, count, seed)]
                    scene = scene_by_key[(family, count, seed)]
                    repetition_orders = PATTERNS[pattern][
                        : REPETITIONS_BY_RUN_CLASS[run_class]
                    ]
                    for repetition, method_order in enumerate(repetition_orders, start=1):
                        for pair_position, method in enumerate(method_order):
                            binary_key = _binary_key_for(method, run_class)
                            slot = _slot(
                                order_index=order_index,
                                kind="measured",
                                run_class=run_class,
                                family=family,
                                count=count,
                                seed=seed,
                                repetition=repetition,
                                pattern=pattern,
                                pair_position=pair_position,
                                method=method,
                                scene=scene,
                                binary_key=binary_key,
                                binary=binaries[binary_key],
                                timeout=timeout,
                            )
                            measured.append(slot)
                            joins.add(
                                (
                                    scene["scene_id"],
                                    scene["input_id"],
                                    repetition,
                                    pattern,
                                    pair_position,
                                    method,
                                )
                            )
                            order_index += 1
        inventories[run_class] = {
            "measured": measured,
            "measured_process_count": len(measured),
            "paired_scene_repetition_count": len(measured) // 2,
            "run_class": run_class,
            "timed": run_class == "headless_timing",
        }
        measured_join_sets[run_class] = joins
    if measured_join_sets["digest_fidelity"] != measured_join_sets["headless_timing"]:
        raise EpochFreezeError("digest and timing measured inventories do not exact-input join")
    expected_position_joins = {
        join
        for join in measured_join_sets["digest_fidelity"]
        if join[2] == 1
    }
    if measured_join_sets["positions_replay"] != expected_position_joins:
        raise EpochFreezeError("positions replay does not cover every exact input once")

    global_aba = sum(row["aba_scene_count"] for row in balance_rows)
    global_bab = sum(row["bab_scene_count"] for row in balance_rows)
    if global_aba != global_bab:
        raise EpochFreezeError("global ABA/BAB assignment is not balanced")
    value = {
        "classification": dict(NONAUTHORIZING_CLASSIFICATION),
        "config_artifacts": config_artifacts,
        "controls": {
            **CONTROLS,
            "fixed_work": dict(FIXED_WORK),
            "timeout_seconds": timeout,
        },
        "design": normalized,
        "epoch_id": EPOCH_ID,
        "failure_policy": dict(FAILURE_POLICY),
        "input_records": {
            "analyzer": _record_identity("analyzer", analyzer_record),
            "binary_attester": _record_identity(
                "binary-attester", binary_attester_record
            ),
            "binary_attestation": _record_identity(
                "binary-attestation", binary_attestation
            ),
            "freezer": _record_identity("freezer", freezer_record),
            "geometry": _record_identity("geometry", geometry_record),
            "mission_compiler": _record_identity(
                "mission-compiler", mission_compiler_record
            ),
            "mission_receipt": _record_identity(
                "mission-receipt", mission_receipt
            ),
            "protocol": _record_identity("protocol", protocol_record),
            "runner": _record_identity("runner", runner_record),
        },
        "method_bindings": method_bindings,
        "normalized_config": normalized["normalized_config"],
        "operations": dict(OPERATIONS),
        "output_grammar": {
            "append_permitted": False,
            "closed_inventory": True,
            "create_exclusive": True,
            "destination_root": "explicit-unregistered-candidate-output-root",
            "destination_templates": {
                "measured": "calibration/{run_class}/{family}/n-{robot_count}/seed-{seed:02d}/rep-{repetition}/{pair_position}-{method}",
                "warmup": "calibration/{run_class}/{family}/warmup/{method}",
            },
            "failed_attempts_retained": True,
            "files_by_kind": {
                key: list(items) for key, items in OUTPUT_BASENAMES.items()
            },
            "unlisted_outputs_permitted": False,
        },
        "phase": PHASE,
        "scenes": scene_rows,
        "schedule": {
            "assignment_algorithm": {
                "assignment_domain": "dancers-headless-aba-bab-assignment-v1",
                "block_order_domain": "dancers-headless-block-order-v1",
                "literal_schedule": True,
                "order_seed": normalized["order_seed"],
                "reconstruction_at_runtime_permitted": False,
                "runtime_randomization": False,
            },
            "balance": {
                "global": {
                    "aba_scene_count": global_aba,
                    "bab_scene_count": global_bab,
                    "status": "exact",
                },
                "per_family_count": balance_rows,
            },
            "execution_order": [
                "warmups",
                "digest_fidelity",
                "positions_replay",
                "headless_timing",
            ],
            "inventories": inventories,
            "timing_gate": {
                "on_prior_failure": "do-not-start-timing",
                "required_prior_inventories": [
                    "digest_fidelity",
                    "positions_replay",
                ],
                "requires_authenticated_complete_attempts": True,
                "runtime_override_permitted": False,
            },
            "warmups": {
                "process_count": len(global_warmups),
                "slots": global_warmups,
                "uniqueness_key": ["binary_key", "map_sha256"],
            },
        },
        "schema_version": EPOCH_SCHEMA,
        "status": "compiled-candidate-only-not-registered",
        "track_id": TRACK_ID,
    }
    data = canonical_json_bytes(value)
    compilation = EpochCompilation(
        value=value, data=data, sha256=hashlib.sha256(data).hexdigest()
    )
    validate_compilation(compilation)
    return compilation


SLOT_KEYS = {
    "binary_key",
    "binary_sha256",
    "config",
    "controller_timesteps",
    "destination",
    "effective_runtime_raster_sha256",
    "family",
    "input_id",
    "kind",
    "map_sha256",
    "method",
    "normalized_config_sha256",
    "order_index",
    "outputs",
    "pair_position",
    "pattern",
    "repetition",
    "robot_count",
    "run_class",
    "scene_id",
    "seed",
    "slot_id",
    "timed",
    "timeout_seconds",
    "timestep_seconds",
    "trace_detail",
}


def _validate_slot(
    slot: Any,
    *,
    expected_order: int,
    run_class: str,
    timeout: int,
    scenes: Mapping[str, Mapping[str, Any]],
    method_bindings: Mapping[str, Any],
) -> dict[str, Any]:
    value = _exact_object(slot, SLOT_KEYS, f"{run_class} slot {expected_order}")
    _exact_value(value["order_index"], expected_order, "slot order_index")
    _exact_value(value["run_class"], run_class, "slot run_class")
    _exact_value(value["timeout_seconds"], timeout, "slot timeout")
    _exact_value(value["controller_timesteps"], FIXED_TIMESTEPS, "slot timesteps")
    _exact_value(value["timestep_seconds"], TIMESTEP_SECONDS, "slot timestep")
    if value["kind"] not in ("warmup", "measured"):
        raise EpochFreezeError("slot kind differs")
    if value["method"] not in METHODS or value["family"] not in FAMILIES:
        raise EpochFreezeError("slot method or family differs")
    for field in ("robot_count", "seed", "repetition", "pair_position"):
        if type(value[field]) is not int:
            raise EpochFreezeError(f"slot {field} type differs")
    scene = scenes.get(value["scene_id"])
    if scene is None or scene["input_id"] != value["input_id"]:
        raise EpochFreezeError("slot scene/input binding differs")
    _exact_value(value["config"], scene["config"], "slot config identity")
    _exact_value(
        value["effective_runtime_raster_sha256"],
        scene["effective_runtime_raster"]["sha256"],
        "slot effective runtime raster SHA-256",
    )
    _exact_value(value["map_sha256"], scene["map"]["sha256"], "slot map SHA-256")
    _exact_value(
        value["normalized_config_sha256"],
        scene["normalized_config_sha256"],
        "slot normalized config SHA-256",
    )
    if (
        scene["family"] != value["family"]
        or scene["robot_count"] != value["robot_count"]
        or scene["seed"] != value["seed"]
    ):
        raise EpochFreezeError("slot scene fields differ")
    binding = method_bindings[value["method"]][run_class]
    if (
        value["binary_key"] != binding["binary_key"]
        or value["binary_sha256"] != binding["sha256"]
    ):
        raise EpochFreezeError("slot binary binding differs")
    warmup = value["kind"] == "warmup"
    expected_timed = run_class == "headless_timing" and not warmup
    if warmup or run_class == "headless_timing":
        expected_trace = None
    elif run_class == "digest_fidelity":
        expected_trace = "digest"
    else:
        expected_trace = "positions"
    _exact_value(value["timed"], expected_timed, "slot timed classification")
    _exact_value(value["trace_detail"], expected_trace, "slot trace detail")
    expected_outputs = list(OUTPUT_BASENAMES["warmup" if warmup else run_class])
    _exact_value(value["outputs"], expected_outputs, "slot output inventory")
    expected_destination = _destination(
        run_class=run_class,
        family=value["family"],
        count=value["robot_count"],
        seed=value["seed"],
        repetition=value["repetition"],
        pair_position=value["pair_position"],
        method=value["method"],
        warmup=warmup,
    )
    _exact_value(value["destination"], expected_destination, "slot destination")
    core = {key: item for key, item in value.items() if key not in {"order_index", "slot_id"}}
    _exact_value(
        value["slot_id"], _semantic_id("dancers-headless-slot-v1", core), "slot ID"
    )
    return value


def _validate_epoch_value(epoch: Any) -> dict[str, Any]:
    value = _exact_object(
        epoch,
        {
            "classification",
            "config_artifacts",
            "controls",
            "design",
            "epoch_id",
            "failure_policy",
            "input_records",
            "method_bindings",
            "normalized_config",
            "operations",
            "output_grammar",
            "phase",
            "scenes",
            "schedule",
            "schema_version",
            "status",
            "track_id",
        },
        "epoch candidate",
    )
    _exact_value(value["schema_version"], EPOCH_SCHEMA, "epoch schema")
    _exact_value(value["epoch_id"], EPOCH_ID, "epoch ID")
    _exact_value(value["track_id"], TRACK_ID, "epoch track")
    _exact_value(value["phase"], PHASE, "epoch phase")
    _exact_value(
        value["status"], "compiled-candidate-only-not-registered", "epoch status"
    )
    _exact_value(
        value["classification"], NONAUTHORIZING_CLASSIFICATION, "epoch classification"
    )
    _exact_value(value["operations"], OPERATIONS, "epoch operations")
    _exact_value(value["failure_policy"], FAILURE_POLICY, "epoch failure policy")
    design = normalize_design(value["design"])
    _exact_value(value["design"], design, "epoch normalized design")
    _exact_value(value["normalized_config"], NORMALIZED_CONFIG, "epoch normalized config")
    timeout = design["timeout_seconds"]
    expected_controls = {
        **CONTROLS,
        "fixed_work": dict(FIXED_WORK),
        "timeout_seconds": timeout,
    }
    _exact_value(value["controls"], expected_controls, "epoch controls")

    record_schemas = {
        "analyzer": ANALYZER_RECORD_SCHEMA,
        "binary_attester": BINARY_ATTESTER_RECORD_SCHEMA,
        "binary_attestation": BINARY_ATTESTATION_SCHEMA,
        "freezer": FREEZER_RECORD_SCHEMA,
        "geometry": GEOMETRY_RECORD_SCHEMA,
        "mission_compiler": MISSION_COMPILER_RECORD_SCHEMA,
        "mission_receipt": MISSION_RECEIPT_SCHEMA,
        "protocol": PROTOCOL_RECORD_SCHEMA,
        "runner": RUNNER_RECORD_SCHEMA,
    }
    record_paths = {
        "analyzer": "analyzer",
        "binary_attester": "binary-attester",
        "binary_attestation": "binary-attestation",
        "freezer": "freezer",
        "geometry": "geometry",
        "mission_compiler": "mission-compiler",
        "mission_receipt": "mission-receipt",
        "protocol": "protocol",
        "runner": "runner",
    }
    records = _exact_object(
        value["input_records"], set(record_schemas), "epoch input records"
    )
    record_contents: dict[str, dict[str, Any]] = {}
    for name, schema in record_schemas.items():
        record = _exact_object(
            records[name],
            {"bytes", "content", "path", "schema_version", "sha256"},
            f"input record {name}",
        )
        content = record["content"]
        if type(content) is not dict:
            raise EpochFreezeError(f"input record {name}.content type differs")
        content_bytes = canonical_json_bytes(content)
        expected_path = (
            f"{FUTURE_PLAN_ROOT}/records/{record_paths[name]}.json"
        )
        _exact_value(record["bytes"], len(content_bytes), f"input record {name}.bytes")
        _exact_value(record["path"], expected_path, f"input record {name}.path")
        _exact_value(record["schema_version"], schema, f"input record {name}.schema")
        _exact_value(
            record["sha256"],
            hashlib.sha256(content_bytes).hexdigest(),
            f"input record {name}.sha256",
        )
        _exact_value(content.get("schema_version"), schema, f"input record {name}.content schema")
        record_contents[name] = content

    mission_scenarios = _validate_mission_receipt(
        record_contents["mission_receipt"], design
    )
    binaries = _validate_binary_attestation(record_contents["binary_attestation"])
    for name, schema, contract, source_path, label in (
        (
            "protocol",
            PROTOCOL_RECORD_SCHEMA,
            PROTOCOL_CONTRACT,
            PROTOCOL_SOURCE_PATH,
            "protocol record",
        ),
        (
            "runner",
            RUNNER_RECORD_SCHEMA,
            RUNNER_CONTRACT,
            "scripts/comparison/run_headless_epoch_v1.py",
            "runner record",
        ),
        (
            "analyzer",
            ANALYZER_RECORD_SCHEMA,
            ANALYZER_CONTRACT,
            "scripts/comparison/analyze_headless_epoch_v1.py",
            "analyzer record",
        ),
        (
            "geometry",
            GEOMETRY_RECORD_SCHEMA,
            GEOMETRY_CONTRACT,
            "scripts/comparison/headless_geometry_v1.py",
            "geometry record",
        ),
        (
            "freezer",
            FREEZER_RECORD_SCHEMA,
            FREEZER_CONTRACT,
            "scripts/comparison/freeze_headless_epoch_v1.py",
            "freezer record",
        ),
        (
            "mission_compiler",
            MISSION_COMPILER_RECORD_SCHEMA,
            MISSION_COMPILER_CONTRACT,
            "scripts/comparison/generate_headless_mission_banks_v1.py",
            "mission compiler record",
        ),
        (
            "binary_attester",
            BINARY_ATTESTER_RECORD_SCHEMA,
            BINARY_ATTESTER_CONTRACT,
            "scripts/comparison/attest_headless_binaries_v1.py",
            "binary attester record",
        ),
    ):
        _validate_component_record(
            record_contents[name],
            schema=schema,
            contract=contract,
            source_path=source_path,
            label=label,
        )

    bindings = _exact_object(
        value["method_bindings"], set(METHODS), "epoch method bindings"
    )
    _exact_value(bindings, _method_bindings(binaries), "epoch method bindings")

    grids = {item["family"]: item for item in design["family_grids"]}
    expected_scene_count = sum(len(item["counts"]) for item in design["family_grids"]) * len(SEEDS)
    scene_rows = value["scenes"]
    config_rows = value["config_artifacts"]
    if type(scene_rows) is not list or len(scene_rows) != expected_scene_count:
        raise EpochFreezeError("epoch scene inventory is incomplete")
    if type(config_rows) is not list or len(config_rows) != expected_scene_count:
        raise EpochFreezeError("rendered config inventory is incomplete")
    scenes: dict[str, dict[str, Any]] = {}
    expected_scene_order = [
        (family, count, seed)
        for family in FAMILIES
        for count in grids[family]["counts"]
        for seed in SEEDS
    ]
    pattern_counts: dict[tuple[str, int], dict[str, int]] = {}
    expected_assignments: dict[tuple[str, int, int], str] = {}
    for family in FAMILIES:
        for count in grids[family]["counts"]:
            ranked = sorted(
                SEEDS,
                key=lambda seed: (
                    _assignment_score(design["order_seed"], family, count, seed),
                    seed,
                ),
            )
            for seed in ranked[: len(SEEDS) // 2]:
                expected_assignments[(family, count, seed)] = "ABA"
            for seed in ranked[len(SEEDS) // 2 :]:
                expected_assignments[(family, count, seed)] = "BAB"
    config_paths: set[str] = set()
    for index, (row, config_row, expected_key) in enumerate(
        zip(scene_rows, config_rows, expected_scene_order)
    ):
        scene = _exact_object(
            row,
            {
                "config",
                "config_binding",
                "effective_runtime_raster",
                "effective_scenario_sha256",
                "family",
                "input_id",
                "is_n0",
                "map",
                "normalized_config_sha256",
                "pattern",
                "robot_count",
                "sampling_unit",
                "scenario",
                "scene_id",
                "seed",
                "seed_roles",
            },
            f"scene {index}",
        )
        family, count, seed = expected_key
        for field, expected in {
            "family": family,
            "robot_count": count,
            "seed": seed,
            "is_n0": count == grids[family]["n0"],
        }.items():
            _exact_value(scene[field], expected, f"scene {index}.{field}")
        _exact_value(
            scene["pattern"],
            expected_assignments[(family, count, seed)],
            f"scene {index}.pattern",
        )
        pattern_counts.setdefault((family, count), {"ABA": 0, "BAB": 0})[
            scene["pattern"]
        ] += 1
        _sha256(scene["effective_scenario_sha256"], f"scene {index} effective SHA")
        _sha256(
            scene["normalized_config_sha256"],
            f"scene {index} normalized config SHA",
        )
        _sha256(scene["input_id"], f"scene {index} input ID")
        map_record = _identity(
            scene["map"],
            f"scene {index}.map",
            keys={"bytes", "path", "sha256", "size_px"},
        )
        _exact_value(map_record["size_px"], 256, f"scene {index}.map.size_px")
        _exact_value(
            scene["effective_runtime_raster"],
            _effective_runtime_raster(family),
            f"scene {index}.effective runtime raster",
        )
        source = mission_scenarios[(family, count, seed)]
        _exact_value(map_record, source["map"], f"scene {index}.map source binding")
        _exact_value(
            scene["effective_scenario_sha256"],
            source["effective_scenario_sha256"],
            f"scene {index}.effective scenario source binding",
        )
        scenario = _identity(scene["scenario"], f"scene {index}.scenario")
        expected_scenario = dict(source["scenario"])
        expected_scenario["path"] = f"{FUTURE_PLAN_ROOT}/{source['scenario']['path']}"
        _exact_value(scenario, expected_scenario, f"scene {index}.scenario source binding")
        _exact_value(
            scenario["path"],
            (
                f"{FUTURE_PLAN_ROOT}/missions/{family}/seed-{seed:04d}/"
                f"n-{count}/scenario.json"
            ),
            f"scene {index}.scenario.path",
        )
        config_identity = _identity(scene["config"], f"scene {index}.config")
        expected_config_path = (
            f"{FUTURE_PLAN_ROOT}/configs/{family}/n-{count}/"
            f"seed-{seed:02d}/config.yaml"
        )
        _exact_value(
            config_identity["path"], expected_config_path, f"scene {index}.config.path"
        )
        config_artifact = _exact_object(
            config_row,
            {"bytes", "content_utf8", "path", "sha256"},
            f"rendered config {index}",
        )
        if type(config_artifact["content_utf8"]) is not str:
            raise EpochFreezeError(f"rendered config {index} content type differs")
        rendered = config_artifact["content_utf8"].encode("utf-8", "strict")
        expected_rendered = _render_config_bytes(
            scene_id=f"calibration-{family}-n-{count}-seed-{seed:02d}",
            family=family,
            count=count,
            seed=seed,
            map_path=map_record["path"],
            scenario_path=scenario["path"],
        )
        expected_config_identity = {
            "bytes": len(expected_rendered),
            "path": expected_config_path,
            "sha256": hashlib.sha256(expected_rendered).hexdigest(),
        }
        if (
            rendered != expected_rendered
            or not _json_exact(
                {
                    key: config_artifact[key]
                    for key in ("bytes", "path", "sha256")
                },
                expected_config_identity,
            )
            or not _json_exact(config_identity, expected_config_identity)
        ):
            raise EpochFreezeError(f"rendered config {index} bytes/identity differ")
        if expected_config_path in config_paths:
            raise EpochFreezeError("rendered config path repeats")
        config_paths.add(expected_config_path)
        expected_config_binding = {
            "map_cli_path": map_record["path"],
            "normalized_config": NORMALIZED_CONFIG,
            "num_robots": count,
            "rng_seed": seed,
            "scenario_cli_path": scenario["path"],
            "simulation_name": f"calibration-{family}-n-{count}-seed-{seed:02d}",
        }
        _exact_value(
            scene["config_binding"], expected_config_binding, f"scene {index} config binding"
        )
        _exact_value(
            scene["seed_roles"],
            _scene_seed_roles(source, runtime_rng_seed=seed),
            f"scene {index} seed roles",
        )
        _exact_value(
            scene["sampling_unit"],
            _scene_sampling_unit(
                family=family,
                count=count,
                mission_bank_id=source["bank_id"],
            ),
            f"scene {index} sampling unit",
        )
        _exact_value(
            scene["normalized_config_sha256"],
            _semantic_id(
                "dancers-headless-normalized-scene-config-v1",
                expected_config_binding,
            ),
            f"scene {index} normalized config SHA",
        )
        input_payload = {
            "controller_timesteps": FIXED_TIMESTEPS,
            "effective_runtime_raster_sha256": scene[
                "effective_runtime_raster"
            ]["sha256"],
            "effective_scenario_sha256": scene["effective_scenario_sha256"],
            "family": family,
            "map_sha256": map_record["sha256"],
            "mission_bank_id": source["bank_id"],
            "mission_generator_seed_uint64": source["generator_seed_uint64"],
            "mission_selection_seed": source["mission_seed"],
            "normalized_config_sha256": scene["normalized_config_sha256"],
            "rendered_config_sha256": scene["config"]["sha256"],
            "phase": PHASE,
            "robot_count": count,
            "runtime_rng_seed": seed,
            "scenario_sha256": scenario["sha256"],
            "timestep_seconds": TIMESTEP_SECONDS,
        }
        _exact_value(
            scene["input_id"],
            _semantic_id("dancers-headless-exact-input-v1", input_payload),
            f"scene {index} input ID",
        )
        _exact_value(
            scene["scene_id"],
            f"calibration-{family}-n-{count}-seed-{seed:02d}",
            f"scene {index} scene ID",
        )
        if scene["scene_id"] in scenes:
            raise EpochFreezeError("scene ID repeats")
        scenes[scene["scene_id"]] = scene
    if any(counts != {"ABA": 5, "BAB": 5} for counts in pattern_counts.values()):
        raise EpochFreezeError("ABA/BAB scene assignment is not balanced per family/count")

    schedule = _exact_object(
        value["schedule"],
        {
            "assignment_algorithm",
            "balance",
            "execution_order",
            "inventories",
            "timing_gate",
            "warmups",
        },
        "epoch schedule",
    )
    expected_assignment = {
        "assignment_domain": "dancers-headless-aba-bab-assignment-v1",
        "block_order_domain": "dancers-headless-block-order-v1",
        "literal_schedule": True,
        "order_seed": design["order_seed"],
        "reconstruction_at_runtime_permitted": False,
        "runtime_randomization": False,
    }
    _exact_value(schedule["assignment_algorithm"], expected_assignment, "assignment algorithm")
    expected_balance_rows = [
        {
            "aba_scene_count": 5,
            "bab_scene_count": 5,
            "family": family,
            "measured_method_pair_counts": {
                run_class: len(SEEDS) * repetitions
                for run_class, repetitions in REPETITIONS_BY_RUN_CLASS.items()
            },
            "robot_count": count,
        }
        for family in FAMILIES
        for count in grids[family]["counts"]
    ]
    balance = _exact_object(
        schedule["balance"], {"global", "per_family_count"}, "schedule balance"
    )
    _exact_value(balance["per_family_count"], expected_balance_rows, "per-stratum balance")
    half = len(expected_balance_rows) * 5
    _exact_value(
        balance["global"],
        {"aba_scene_count": half, "bab_scene_count": half, "status": "exact"},
        "global balance",
    )
    _exact_value(
        schedule["execution_order"],
        ["warmups", "digest_fidelity", "positions_replay", "headless_timing"],
        "schedule execution order",
    )
    _exact_value(
        schedule["timing_gate"],
        {
            "on_prior_failure": "do-not-start-timing",
            "required_prior_inventories": [
                "digest_fidelity",
                "positions_replay",
            ],
            "requires_authenticated_complete_attempts": True,
            "runtime_override_permitted": False,
        },
        "schedule timing gate",
    )

    warmup_inventory = _exact_object(
        schedule["warmups"],
        {"process_count", "slots", "uniqueness_key"},
        "schedule warmups",
    )
    _exact_value(
        warmup_inventory["uniqueness_key"],
        ["binary_key", "map_sha256"],
        "warmup uniqueness key",
    )
    expected_warmup_count = len(FAMILIES) * 4
    _exact_value(
        warmup_inventory["process_count"],
        expected_warmup_count,
        "warmup process count",
    )
    warmup_slots = warmup_inventory["slots"]
    if type(warmup_slots) is not list or len(warmup_slots) != expected_warmup_count:
        raise EpochFreezeError("global warmup inventory is incomplete")
    warmup_keys: set[tuple[str, str]] = set()
    warmup_order = 0
    for family in FAMILIES:
        scene = scenes[
            f"calibration-{family}-n-{grids[family]['n0']}-seed-{SEEDS[0]:02d}"
        ]
        for binary_key in (
            "reference_fidelity",
            "reference_timing",
            "optimized_fidelity",
            "optimized_timing",
        ):
            method = binaries[binary_key]["method"]
            run_class = (
                "headless_timing"
                if binary_key.endswith("_timing")
                else "digest_fidelity"
            )
            checked = _validate_slot(
                warmup_slots[warmup_order],
                expected_order=warmup_order,
                run_class=run_class,
                timeout=timeout,
                scenes=scenes,
                method_bindings=bindings,
            )
            if (
                checked["kind"] != "warmup"
                or checked["binary_key"] != binary_key
                or checked["repetition"] != 0
                or checked["pattern"] != "warmup"
                or checked["pair_position"] != 0
                or checked["scene_id"] != scene["scene_id"]
                or checked["seed"] != SEEDS[0]
                or checked["robot_count"] != grids[family]["n0"]
            ):
                raise EpochFreezeError("warmup slot semantics differ")
            warmup_key = (checked["binary_key"], checked["map_sha256"])
            if warmup_key in warmup_keys:
                raise EpochFreezeError("warmup binary/map pair repeats")
            warmup_keys.add(warmup_key)
            warmup_order += 1
    expected_warmup_keys = {
        (binary_key, mission_scenarios[(family, grids[family]["n0"], SEEDS[0])]["map"]["sha256"])
        for family in FAMILIES
        for binary_key in (
            "reference_fidelity",
            "reference_timing",
            "optimized_fidelity",
            "optimized_timing",
        )
    }
    if warmup_keys != expected_warmup_keys:
        raise EpochFreezeError("warmup binary/map inventory differs")

    inventories = _exact_object(
        schedule["inventories"], set(RUN_CLASSES), "schedule inventories"
    )
    join_sets: dict[str, set[tuple[Any, ...]]] = {}
    for run_class in RUN_CLASSES:
        repetitions = REPETITIONS_BY_RUN_CLASS[run_class]
        expected_measured = expected_scene_count * repetitions * len(METHODS)
        inventory = _exact_object(
            inventories[run_class],
            {
                "measured",
                "measured_process_count",
                "paired_scene_repetition_count",
                "run_class",
                "timed",
            },
            f"{run_class} inventory",
        )
        _exact_value(inventory["run_class"], run_class, "inventory run class")
        _exact_value(inventory["timed"], run_class == "headless_timing", "inventory timed")
        _exact_value(inventory["measured_process_count"], expected_measured, "measured denominator")
        _exact_value(
            inventory["paired_scene_repetition_count"],
            expected_measured // 2,
            "paired denominator",
        )
        if type(inventory["measured"]) is not list or len(inventory["measured"]) != expected_measured:
            raise EpochFreezeError("measured inventory is incomplete")
        joins: set[tuple[Any, ...]] = set()
        measured = inventory["measured"]
        expected_pairs: list[tuple[dict[str, Any], int, tuple[str, str]]] = []
        for family in FAMILIES:
            for count in grids[family]["counts"]:
                ordered_seeds = sorted(
                    SEEDS,
                    key=lambda seed: (
                        _block_score(design["order_seed"], family, count, seed),
                        seed,
                    ),
                )
                for seed in ordered_seeds:
                    scene = scenes[
                        f"calibration-{family}-n-{count}-seed-{seed:02d}"
                    ]
                    for repetition, method_order in enumerate(
                        PATTERNS[scene["pattern"]][:repetitions], start=1
                    ):
                        expected_pairs.append((scene, repetition, method_order))
        for pair_index, (scene, repetition, method_order) in enumerate(expected_pairs):
            offset = pair_index * 2
            left = _validate_slot(
                measured[offset],
                expected_order=offset,
                run_class=run_class,
                timeout=timeout,
                scenes=scenes,
                method_bindings=bindings,
            )
            right = _validate_slot(
                measured[offset + 1],
                expected_order=offset + 1,
                run_class=run_class,
                timeout=timeout,
                scenes=scenes,
                method_bindings=bindings,
            )
            if left["kind"] != "measured" or right["kind"] != "measured":
                raise EpochFreezeError("measured inventory contains a non-measured slot")
            same_fields = (
                "family",
                "input_id",
                "pattern",
                "repetition",
                "robot_count",
                "scene_id",
                "seed",
            )
            if any(left[field] != right[field] for field in same_fields):
                raise EpochFreezeError("measured method pair is not adjacent and identical-input")
            if left["scene_id"] != scene["scene_id"]:
                raise EpochFreezeError("measured literal block order differs")
            if left["pair_position"] != 0 or right["pair_position"] != 1:
                raise EpochFreezeError("measured pair positions differ")
            pattern = left["pattern"]
            if (
                pattern != scene["pattern"]
                or left["repetition"] != repetition
                or not 1 <= repetition <= repetitions
            ):
                raise EpochFreezeError("measured pattern/repetition differs")
            if (left["method"], right["method"]) != method_order:
                raise EpochFreezeError("measured literal ABA/BAB order differs")
            for slot in (left, right):
                join = (
                    slot["scene_id"],
                    slot["input_id"],
                    slot["repetition"],
                    slot["pattern"],
                    slot["pair_position"],
                    slot["method"],
                )
                if join in joins:
                    raise EpochFreezeError("measured slot repeats")
                joins.add(join)
        expected_joins = {
            (
                scene["scene_id"],
                scene["input_id"],
                repetition,
                scene["pattern"],
                pair_position,
                method,
            )
            for scene in scene_rows
            for repetition, order in enumerate(
                PATTERNS[scene["pattern"]][:repetitions], start=1
            )
            for pair_position, method in enumerate(order)
        }
        if joins != expected_joins:
            raise EpochFreezeError("measured full-grid denominator is incomplete")
        join_sets[run_class] = joins
    if join_sets["digest_fidelity"] != join_sets["headless_timing"]:
        raise EpochFreezeError("digest/timing exact-input join differs")
    expected_positions = {
        join for join in join_sets["digest_fidelity"] if join[2] == 1
    }
    if join_sets["positions_replay"] != expected_positions:
        raise EpochFreezeError("positions exact-input projection differs")

    expected_output_grammar = {
        "append_permitted": False,
        "closed_inventory": True,
        "create_exclusive": True,
        "destination_root": "explicit-unregistered-candidate-output-root",
        "destination_templates": {
            "measured": "calibration/{run_class}/{family}/n-{robot_count}/seed-{seed:02d}/rep-{repetition}/{pair_position}-{method}",
            "warmup": "calibration/{run_class}/{family}/warmup/{method}",
        },
        "failed_attempts_retained": True,
        "files_by_kind": {
            key: list(items) for key, items in OUTPUT_BASENAMES.items()
        },
        "unlisted_outputs_permitted": False,
    }
    _exact_value(value["output_grammar"], expected_output_grammar, "output grammar")
    return value


def validate_epoch_bytes(raw: bytes) -> dict[str, Any]:
    """Authenticate canonical candidate bytes before a runner interprets slots."""

    if type(raw) is not bytes or not 0 < len(raw) <= MAX_OUTPUT_BYTES:
        raise EpochFreezeError("epoch candidate byte count is outside the bound")
    value = strict_json_bytes(raw, "epoch candidate")
    if raw != canonical_json_bytes(value):
        raise EpochFreezeError("epoch candidate is not canonical JSON")
    return _validate_epoch_value(value)


def validate_compilation(compilation: EpochCompilation) -> None:
    if type(compilation) is not EpochCompilation:
        raise EpochFreezeError("epoch compilation object type differs")
    value = validate_epoch_bytes(compilation.data)
    if (
        not _json_exact(value, compilation.value)
        or compilation.sha256 != hashlib.sha256(compilation.data).hexdigest()
    ):
        raise EpochFreezeError("epoch compilation identity differs")


def _required_flag(name: str) -> int:
    value = getattr(os, name, None)
    if type(value) is not int or value == 0:
        raise EpochFreezeError(f"required safe-open flag {name} is unavailable")
    return value


def _directory_open_flags() -> int:
    return (
        os.O_RDONLY
        | _required_flag("O_DIRECTORY")
        | _required_flag("O_NOFOLLOW")
        | _required_flag("O_CLOEXEC")
    )


def _close_descriptor_exact(descriptor: int, label: str) -> None:
    """Close an owned descriptor, reconciling an effect-then-error close."""

    try:
        os.close(descriptor)
        return
    except OSError as close_error:
        try:
            os.fstat(descriptor)
        except OSError as probe_error:
            if probe_error.errno == errno.EBADF:
                return
        raise EpochFreezeError(f"cannot close {label}") from close_error


def _open_directory_path_exact(path: Path, label: str) -> int:
    """Open every absolute path component without following a symlink."""

    absolute = Path(os.path.abspath(os.fspath(path)))
    flags = _directory_open_flags()
    try:
        current = os.open(os.sep, flags)
    except OSError as error:
        raise EpochFreezeError(f"cannot open filesystem root for {label}") from error
    try:
        for part in absolute.parts[1:]:
            try:
                child = os.open(part, flags, dir_fd=current)
            except OSError as error:
                raise EpochFreezeError(
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
            raise EpochFreezeError(f"{label} is not a directory")
        return current
    except BaseException as primary:
        try:
            _close_descriptor_exact(current, label)
        except BaseException:
            pass
        raise primary


def _directory_identity(descriptor: int, label: str) -> tuple[int, int]:
    try:
        metadata = os.fstat(descriptor)
    except OSError as error:
        raise EpochFreezeError(f"cannot authenticate {label}") from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise EpochFreezeError(f"{label} is not a directory")
    return metadata.st_dev, metadata.st_ino


def _protected_output_identities(repository_root: Path) -> set[tuple[int, int]]:
    identities: set[tuple[int, int]] = set()
    for relative in (Path("results"), Path("comparison/plans")):
        descriptor = _open_directory_path_exact(
            repository_root / relative,
            f"protected output root {relative.as_posix()}",
        )
        try:
            identities.add(
                _directory_identity(
                    descriptor,
                    f"protected output root {relative.as_posix()}",
                )
            )
        finally:
            _close_descriptor_exact(
                descriptor,
                f"protected output root {relative.as_posix()}",
            )
    if len(identities) != 2:
        raise EpochFreezeError("protected output roots are not distinct")
    return identities


def _reject_protected_output_parent(
    parent_descriptor: int, repository_root: Path
) -> None:
    protected = _protected_output_identities(repository_root)
    try:
        current = os.dup(parent_descriptor)
    except OSError as error:
        raise EpochFreezeError("cannot duplicate candidate output parent") from error
    try:
        while True:
            identity = _directory_identity(current, "candidate output ancestry")
            if identity in protected:
                raise EpochFreezeError(
                    "candidate compiler must never write a registered plan or result namespace"
                )
            try:
                ancestor = os.open("..", _directory_open_flags(), dir_fd=current)
            except OSError as error:
                raise EpochFreezeError(
                    "cannot authenticate candidate output ancestry"
                ) from error
            ancestor_identity = _directory_identity(
                ancestor, "candidate output ancestor"
            )
            if ancestor_identity == identity:
                _close_descriptor_exact(ancestor, "filesystem root")
                break
            _close_descriptor_exact(current, "candidate output ancestry")
            current = ancestor
    finally:
        _close_descriptor_exact(current, "candidate output ancestry")


def _same_existing_path(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def _has_existing_ancestor(path: Path, protected: Path) -> bool:
    current = path
    while True:
        if _same_existing_path(current, protected):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _publication_path(
    output_path: Path, repository_root: Path
) -> tuple[Path, Path]:
    root = Path(os.path.abspath(os.fspath(repository_root)))
    module_root = Path(os.path.abspath(os.fspath(MODULE_REPOSITORY_ROOT)))
    if (
        os.path.realpath(root) != os.fspath(root)
        or not root.is_dir()
        or not _same_existing_path(root, module_root)
    ):
        raise EpochFreezeError(
            "repository root must be the canonical live module repository"
        )
    output = Path(os.path.abspath(os.fspath(output_path)))
    if len(os.fspath(output).encode("utf-8")) > 4096:
        raise EpochFreezeError("candidate output path exceeds the path bound")
    if os.path.realpath(output.parent) != os.fspath(output.parent):
        raise EpochFreezeError("candidate output parent contains a symlink")
    protected = (root / "results", root / "comparison/plans")
    if any(_has_existing_ancestor(output.parent, item) for item in protected):
        raise EpochFreezeError(
            "candidate compiler must never write a registered plan or result namespace"
        )
    try:
        relative = output.relative_to(root)
    except ValueError:
        return output, root
    if relative.parts[:1] == ("results",) or relative.parts[:2] == ("comparison", "plans"):
        raise EpochFreezeError(
            "candidate compiler must never write a registered plan or result namespace"
        )
    return output, root


def publish_candidate_create_exclusive(
    compilation: EpochCompilation,
    *,
    output_path: Path,
    repository_root: Path,
) -> PublishedCandidate:
    """Reject every candidate publication at the code-only checkpoint."""

    raise EpochFreezeError(
        "candidate publication is categorically disabled at this nonauthorizing checkpoint"
    )

    validate_compilation(compilation)
    output, _ = _publication_path(output_path, repository_root)
    parent_fd: int | None = None
    try:
        parent_fd = _open_directory_path_exact(
            output.parent, "candidate output parent"
        )
        _reject_protected_output_parent(parent_fd, repository_root)
    except (OSError, EpochFreezeError) as exc:
        if parent_fd is not None:
            try:
                _close_descriptor_exact(parent_fd, "candidate output parent")
            except BaseException:
                pass
        raise EpochFreezeError("cannot open candidate output parent safely") from exc
    created_fd: int | None = None
    verify_fd: int | None = None
    created_identity: tuple[int, int] | None = None
    try:
        assert parent_fd is not None
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | _required_flag("O_NOFOLLOW")
            | _required_flag("O_CLOEXEC")
        )
        try:
            created_fd = os.open(output.name, flags, 0o600, dir_fd=parent_fd)
        except FileExistsError as exc:
            raise EpochFreezeError(
                "candidate output already exists; publication is create-exclusive"
            ) from exc
        except OSError as exc:
            raise EpochFreezeError("cannot create candidate output safely") from exc
        metadata = os.fstat(created_fd)
        created_identity = (metadata.st_dev, metadata.st_ino)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise EpochFreezeError("created candidate output identity is unsafe")
        offset = 0
        while offset < len(compilation.data):
            try:
                written = os.write(created_fd, compilation.data[offset:])
            except OSError as exc:
                raise EpochFreezeError("cannot write candidate output") from exc
            if written <= 0:
                raise EpochFreezeError("candidate output write made no progress")
            offset += written
        try:
            os.fsync(created_fd)
        except OSError as exc:
            raise EpochFreezeError("cannot synchronize candidate output") from exc
        final = os.fstat(created_fd)
        if final.st_size != len(compilation.data):
            raise EpochFreezeError("candidate output size differs")
        _close_descriptor_exact(created_fd, "candidate output")
        created_fd = None
        named = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
        if (
            (named.st_dev, named.st_ino) != created_identity
            or named.st_size != len(compilation.data)
            or stat.S_IMODE(named.st_mode) != 0o600
        ):
            raise EpochFreezeError("candidate output name identity differs")
        try:
            verify_fd = os.open(
                output.name,
                os.O_RDONLY
                | _required_flag("O_NOFOLLOW")
                | _required_flag("O_CLOEXEC"),
                dir_fd=parent_fd,
            )
        except OSError as exc:
            raise EpochFreezeError("cannot reopen candidate output safely") from exc
        before_read = os.fstat(verify_fd)
        observed_parts: list[bytes] = []
        observed_size = 0
        while observed_size <= MAX_OUTPUT_BYTES:
            chunk = os.read(
                verify_fd,
                min(64 * 1024, MAX_OUTPUT_BYTES + 1 - observed_size),
            )
            if not chunk:
                break
            observed_parts.append(chunk)
            observed_size += len(chunk)
        after_read = os.fstat(verify_fd)
        _close_descriptor_exact(verify_fd, "candidate verification descriptor")
        verify_fd = None
        if (
            (before_read.st_dev, before_read.st_ino) != created_identity
            or (after_read.st_dev, after_read.st_ino) != created_identity
            or before_read.st_size != after_read.st_size
            or before_read.st_mtime_ns != after_read.st_mtime_ns
            or b"".join(observed_parts) != compilation.data
        ):
            raise EpochFreezeError("candidate output verification differs")
        try:
            os.fsync(parent_fd)
        except OSError as exc:
            raise EpochFreezeError("cannot synchronize candidate output parent") from exc
        _close_descriptor_exact(parent_fd, "candidate output parent")
        parent_fd = None
    except BaseException as primary:
        cleanup_errors: list[str] = []
        if created_fd is not None:
            try:
                _close_descriptor_exact(created_fd, "failed candidate output")
            except BaseException as cleanup_error:
                cleanup_errors.append(str(cleanup_error))
        if verify_fd is not None:
            try:
                _close_descriptor_exact(
                    verify_fd, "failed candidate verification descriptor"
                )
            except BaseException as cleanup_error:
                cleanup_errors.append(str(cleanup_error))
        if created_identity is not None and parent_fd is not None:
            try:
                named = os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
                if (named.st_dev, named.st_ino) == created_identity:
                    os.unlink(output.name, dir_fd=parent_fd)
                    os.fsync(parent_fd)
                else:
                    cleanup_errors.append(
                        "candidate output name no longer identifies the created file"
                    )
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                cleanup_errors.append(f"cannot roll back candidate output: {cleanup_error}")
            try:
                os.stat(output.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                cleanup_errors.append(
                    f"cannot prove candidate rollback absence: {cleanup_error}"
                )
            else:
                cleanup_errors.append("candidate output remains after rollback")
        elif created_identity is not None:
            cleanup_errors.append(
                "candidate output parent closed before rollback could be proved"
            )
        if parent_fd is not None:
            try:
                _close_descriptor_exact(parent_fd, "failed candidate output parent")
            except BaseException as cleanup_error:
                cleanup_errors.append(str(cleanup_error))
        if cleanup_errors:
            details = tuple(cleanup_errors)
            try:
                setattr(primary, "publication_cleanup_errors", details)
            except BaseException:
                pass
            if isinstance(primary, Exception):
                raise EpochFreezeError(
                    "candidate publication failed and rollback was not fully proved: "
                    + "; ".join(details)
                ) from primary
        raise
    return PublishedCandidate(
        path=output,
        bytes=len(compilation.data),
        sha256=compilation.sha256,
    )


freeze_headless_epoch = compile_epoch
publish_candidate = publish_candidate_create_exclusive
