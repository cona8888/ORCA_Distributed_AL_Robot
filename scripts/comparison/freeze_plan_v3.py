#!/usr/bin/env python3
"""Freeze the immutable DANCeRS rerun-1 fidelity or gated timing plan.

The failed v2 epoch remains sealed evidence.  This successor authenticates
that incident, preserves its scientific design byte-for-byte where required,
and creates only the registered absent v3 destinations.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import errno
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


PLAN_SCHEMA_V3 = "dancers-laptop-comparison-plan-v3"
AMENDMENT_SCHEMA = "dancers-post-outcome-amendment-v1"
AMENDMENT_ID = "fh-lower-count-fidelity-2026-07-28-v1"
EXECUTION_EPOCH_ID = "fh-lower-count-fidelity-2026-07-28-rerun-1"
FIDELITY_ORDER_SEED = 8089505177631375378
AMENDMENT_SHA256 = (
    "cb9dcd355648102cac35e06d0e2e6575f5ae6a60c9319690c2373efffc3ce7cb"
)
RERUN_REGISTRY_SCHEMA = "dancers-amended-fidelity-rerun-v1"
RERUN_ADDENDUM_SHA256 = (
    "8d55b9f5cfdd1a2bddd11c35a8f6e317d3bd38b58a12bbb5561bcc3a827f77ce"
)
RERUN_REGISTRY_SHA256 = (
    "8828999bc6bd367c6d5c7c5964e501b7ffe97e37615b494768b0018f1a5d236d"
)
FIDELITY_GATE_SCHEMA = "dancers-amended-fidelity-gate-v2"
BUILD_MANIFEST_SCHEMA = "dancers-comparison-build-manifest-v1"
REFERENCE_PIN = "986f47deec5b079663c7d7f6120bfc20f0467901"
OPTIMIZED_PIN = "99364a41f1535a4bb0eddbe5a10030127ea77086"
AMENDMENT_PATH = Path(
    "docs/comparison/fractal_high_lower_count_amendment_v1.json"
)
RERUN_ADDENDUM_PATH = Path(
    "docs/comparison/EXPERIMENT_PROTOCOL_AMENDMENT_FH_LOWER_RERUN_1.md"
)
RERUN_REGISTRY_PATH = Path(
    "docs/comparison/fractal_high_lower_count_fidelity_rerun_1.json"
)
PLAN_FREEZER_PATH = Path("scripts/comparison/freeze_plan_v3.py")
RUNNER_PATH = Path("scripts/comparison/run_paired_v3.py")
ANALYZER_PATH = Path("scripts/comparison/analyze_results_v3.py")
RUNNER_V2_BASE_PATH = Path("scripts/comparison/run_paired_v2.py")
ANALYZER_V2_BASE_PATH = Path("scripts/comparison/analyze_results_v2.py")
FREEZER_V2_BASE_PATH = Path("scripts/comparison/freeze_plan_v2.py")
RUNNER_V1_BASE_PATH = Path("scripts/comparison/run_paired.py")
ANALYZER_V1_BASE_PATH = Path("scripts/comparison/analyze_results.py")
TRACE_COMPARE_PATH = Path("scripts/comparison/trace_compare.py")
CAPACITY_DECISION_PATH = Path(
    "scripts/comparison/freeze_capacity_decision.py"
)
MATERIALIZER_PATH = Path(
    "scripts/comparison/materialize_capacity_scenario.py"
)
ENVIRONMENT_GENERATOR_PATH = Path(
    "scripts/comparison/generate_environments.py"
)
BUILD_MANIFEST_PATH = Path("comparison/build_manifest.json")
CONFIG_TEMPLATE_PATH = Path("config/comparison/laptop_benchmark.yaml")
PIXI_LOCK_PATH = Path("pixi.lock")
ENVIRONMENT_MANIFEST_SIDECAR_PATH = Path(
    "comparison/environments/manifest.sha256"
)
FRACTAL_HIGH_CALIBRATION_BANK_PATH = Path(
    "comparison/environments/fractal_high/calibration/capacity/bank.json"
)
FRACTAL_HIGH_CALIBRATION_MAP_PATH = Path(
    "comparison/environments/fractal_high/calibration/map.png"
)
PLAN_ROOT = Path("comparison/plans/calibration/trace-v3-amended-v1")
RESULT_ROOT = Path("results/comparison/calibration/trace-v3-amended-v1")
ORIGINAL_TRIGGER_SESSION_ROOT = Path(
    "results/comparison/calibration/trace-v3/"
    "fractal-high-full-grid-fidelity"
)
ORIGINAL_TRIGGER_ANALYSIS_PATH = Path(
    "results/comparison/calibration/trace-v3/"
    "fractal-high-full-grid-fidelity-analysis.json"
)
PRIOR_FAILED_PLAN_PATH = (
    PLAN_ROOT / "fractal-high-lower-count-fidelity" / "plan.json"
)
PRIOR_FAILED_RESULT_ROOT = (
    RESULT_ROOT / "fractal-high-lower-count-fidelity"
)
PRIOR_FAILED_ANALYSIS_PATH = (
    RESULT_ROOT / "fractal-high-lower-count-fidelity-analysis.json"
)
FIDELITY_OUTPUT_NAME = "fractal-high-lower-count-fidelity-rerun-1"
TIMING_OUTPUT_NAME = "fractal-high-lower-count-timing-rerun-1"
FIDELITY_RECEIPT_NAME = f"{FIDELITY_OUTPUT_NAME}-gate-receipt.json"
FIDELITY_COHORT_ID = (
    "fractal-high-lower-count-fidelity-amended-v1-rerun-1"
)
TIMING_COHORT_ID = (
    "fractal-high-lower-count-timing-amended-v1-rerun-1"
)
IDLE_COOLDOWN_SECONDS = 300
POWER_MODE = "AC Power"
THERMAL_POLICY = (
    "ProcessInfo.thermalState rawValue must be 0 before every block; "
    "fail closed on probe error/change; five-minute idle cooldown before "
    "a measured session"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
TIMESTEP_CONFIG_RE = re.compile(
    r"(?m)^[ \t]*TIMESTEP[ \t]*:[ \t]*"
    r"([0-9]+(?:\.[0-9]+)?)[ \t]*(?:#.*)?$"
)
SELECTION_DISCLOSURE = (
    "Counts are the adjacent pre-existing registered exact-N prefixes "
    "immediately below N=384 and were selected after observing the "
    "original-grid failures."
)
RECEIPT_LEDGER_FIELDS = {
    "frozen_plan_json_sha256": (
        "frozen_plan.json",
        "fidelity_frozen_plan",
    ),
    "run_manifest_json_sha256": (
        "run_manifest.json",
        "fidelity_run_manifest",
    ),
    "preflight_json_sha256": (
        "preflight.json",
        "fidelity_preflight",
    ),
    "session_json_sha256": ("session.json", "fidelity_session"),
    "runs_jsonl_sha256": ("runs.jsonl", "fidelity_runs"),
    "runs_csv_sha256": ("runs.csv", "fidelity_runs_csv"),
    "pairs_jsonl_sha256": ("pairs.jsonl", "fidelity_pairs"),
}
PRIOR_BINDING_KEY_MAP = {
    "analyzer_v2_base": "analyzer_v2_base",
    "freezer_v2_base": "freezer_v2_base",
    "prior_failed_analysis": "analysis",
    "prior_failed_frozen_plan": "frozen_plan",
    "prior_failed_orchestration_abort": "orchestration_abort",
    "prior_failed_pairs": "pairs",
    "prior_failed_plan": "source_plan",
    "prior_failed_preflight": "preflight",
    "prior_failed_run_manifest": "run_manifest",
    "prior_failed_runs": "runs",
    "prior_failed_runs_csv": "runs_csv",
    "prior_failed_session": "session",
    "runner_v2_base": "runner_v2_base",
}
PRIOR_REGISTRY_ARTIFACT_KEYS = frozenset(
    {
        "analysis",
        "analyzer_v2_base",
        "freezer_v2_base",
        "frozen_plan",
        "orchestration_abort",
        "pairs",
        "parent_amendment_protocol",
        "parent_amendment_registry",
        "preflight",
        "run_manifest",
        "runner_v2_base",
        "runs",
        "runs_csv",
        "scenario_192",
        "scenario_256",
        "session",
        "source_plan",
    }
)
MANDATORY_ARTIFACT_KEYS = frozenset(
    {
        "rerun_addendum",
        "rerun_registry",
        "prior_failed_plan",
        "prior_failed_frozen_plan",
        "prior_failed_run_manifest",
        "prior_failed_preflight",
        "prior_failed_session",
        "prior_failed_runs",
        "prior_failed_runs_csv",
        "prior_failed_pairs",
        "prior_failed_analysis",
        "prior_failed_orchestration_abort",
        "runner_v2_base",
        "analyzer_v2_base",
        "freezer_v2_base",
        "runner",
        "analysis_revision",
        "plan_freezer",
    }
)
COMMON_V2_ARTIFACT_KEYS = frozenset(
    {
        "amendment_protocol",
        "analyzer_v1_base",
        "build_manifest",
        "capacity_materializer",
        "config_template",
        "environment_generator",
        "environment_manifest",
        "environment_manifest_sidecar",
        "experiment_manifest",
        "fractal_high_calibration_bank",
        "fractal_high_calibration_map",
        "freeze_capacity_decision",
        "pixi_lock",
        "post_outcome_amendment",
        "protocol",
        "runner_v1_base",
        "trace_compare",
        "trigger_analysis",
        "trigger_frozen_plan",
        "trigger_pairs",
        "trigger_runs",
        "trigger_session",
    }
)
TIMING_ONLY_ARTIFACT_KEYS = frozenset(
    {"fidelity_gate_analysis"}
    | {artifact_name for _, artifact_name in RECEIPT_LEDGER_FIELDS.values()}
)
AUTHENTICATED_MATERIALIZER_BOOTSTRAP = r"""
import base64
import hashlib
import json
import os
import sys
import tempfile
import types
from pathlib import Path

payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
materializer_source = base64.b64decode(
    payload["materializer_source_base64"], validate=True
)
generator_source = base64.b64decode(
    payload["generator_source_base64"], validate=True
)
if (
    hashlib.sha256(materializer_source).hexdigest()
    != payload["materializer_sha256"]
    or hashlib.sha256(generator_source).hexdigest()
    != payload["generator_sha256"]
):
    raise SystemExit("authenticated source transfer hash mismatch")

with tempfile.TemporaryDirectory(
    prefix="dancers-authenticated-materializer-"
) as temporary:
    root = Path(temporary)
    materializer_path = root / "materialize_capacity_scenario.py"
    descriptor = os.open(
        materializer_path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0),
        0o400,
    )
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(materializer_source)
        stream.flush()
        os.fsync(stream.fileno())

    generator = types.ModuleType("generate_environments")
    generator.__file__ = "<authenticated-generate-environments>"
    generator.__package__ = ""
    exec(
        compile(
            generator_source,
            generator.__file__,
            "exec",
            dont_inherit=True,
        ),
        generator.__dict__,
    )
    sys.modules["generate_environments"] = generator

    materializer = types.ModuleType("materialize_capacity_scenario")
    materializer.__file__ = str(materializer_path)
    materializer.__package__ = ""
    exec(
        compile(
            materializer_source,
            materializer.__file__,
            "exec",
            dont_inherit=True,
        ),
        materializer.__dict__,
    )
    output = root / "scenario.json"
    reported_sha256 = materializer.materialize_registered_prefix(
        Path(payload["environment_root"]),
        payload["family"],
        payload["split"],
        payload["robot_count"],
        output,
    )
    data = output.read_bytes()
    result = {
        "bytes": len(data),
        "data_base64": base64.b64encode(data).decode("ascii"),
        "sha256": reported_sha256,
    }
    sys.stdout.write(
        json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
    )
"""
AUTHENTICATED_ANALYZER_BOOTSTRAP = r"""
import base64
import hashlib
import json
import os
import sys
import tempfile
import types
from pathlib import Path as RealPath

payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
snapshot_root = RealPath(payload["snapshot_root"])
logical_cwd = RealPath(payload["logical_cwd"])
analysis_temp_root = snapshot_root / ".analysis-private"
analysis_temp_root.mkdir(mode=0o700)
tempfile.tempdir = str(analysis_temp_root)

class SnapshotPath:
    __slots__ = ("_logical",)

    def __init__(self, *parts):
        normalized = [
            part._logical if isinstance(part, SnapshotPath) else part
            for part in parts
        ]
        candidate = RealPath(*normalized)
        if candidate.is_absolute():
            try:
                candidate = RealPath("/") / candidate.relative_to(
                    snapshot_root
                )
            except ValueError:
                pass
        self._logical = candidate

    def _absolute_logical(self):
        logical = self._logical
        if not logical.is_absolute():
            logical = logical_cwd / logical
        return RealPath(os.path.normpath(str(logical)))

    def _physical(self):
        logical = self._absolute_logical()
        return snapshot_root.joinpath(*logical.parts[1:])

    def __str__(self):
        return str(self._logical)

    def __repr__(self):
        return f"SnapshotPath({str(self._logical)!r})"

    def __fspath__(self):
        return str(self._physical())

    def __hash__(self):
        return hash(self._logical)

    def __eq__(self, other):
        if isinstance(other, SnapshotPath):
            return self._logical == other._logical
        if isinstance(other, RealPath):
            return self._logical == other
        return False

    def __truediv__(self, other):
        logical_other = (
            other._logical if isinstance(other, SnapshotPath) else other
        )
        return SnapshotPath(self._logical / logical_other)

    def __rtruediv__(self, other):
        return SnapshotPath(other) / self

    @property
    def parent(self):
        return SnapshotPath(self._logical.parent)

    @property
    def parents(self):
        return tuple(SnapshotPath(item) for item in self._logical.parents)

    @property
    def name(self):
        return self._logical.name

    @property
    def parts(self):
        return self._logical.parts

    def with_name(self, name):
        return SnapshotPath(self._logical.with_name(name))

    def expanduser(self):
        return SnapshotPath(self._logical.expanduser())

    def is_absolute(self):
        return self._logical.is_absolute()

    def as_posix(self):
        return self._logical.as_posix()

    def relative_to(self, other):
        logical_other = (
            other._logical if isinstance(other, SnapshotPath)
            else RealPath(other)
        )
        return SnapshotPath(self._logical.relative_to(logical_other))

    def resolve(self, strict=False):
        physical = self._physical()
        if strict and not physical.exists():
            raise FileNotFoundError(str(self._logical))
        return SnapshotPath(self._absolute_logical())

    def exists(self):
        return self._physical().exists()

    def is_symlink(self):
        return self._physical().is_symlink()

    def is_file(self):
        return self._physical().is_file()

    def is_dir(self):
        return self._physical().is_dir()

    def stat(self):
        return self._physical().stat()

    def open(self, *args, **kwargs):
        return self._physical().open(*args, **kwargs)

    def read_bytes(self):
        return self._physical().read_bytes()

    def read_text(self, *args, **kwargs):
        return self._physical().read_text(*args, **kwargs)

    def chmod(self, mode):
        return self._physical().chmod(mode)

def decoded_source(name):
    source = base64.b64decode(
        payload["sources"][name]["base64"], validate=True
    )
    if (
        hashlib.sha256(source).hexdigest()
        != payload["sources"][name]["sha256"]
    ):
        raise SystemExit(f"{name} authenticated source transfer mismatch")
    return source

def load_module(name, source, logical_file):
    module = types.ModuleType(name)
    module.__file__ = logical_file
    module.__package__ = ""
    sys.modules[name] = module
    exec(
        compile(source, logical_file, "exec", dont_inherit=True),
        module.__dict__,
    )
    return module

trace_source = decoded_source("trace")
capacity_source = decoded_source("capacity")
runner_v1_source = decoded_source("runner_v1")
v1_source = decoded_source("analyzer_v1")
v2_source = decoded_source("analyzer_v2")
analyzer_source = decoded_source("analyzer")
trace = load_module(
    "trace_compare",
    trace_source,
    payload["sources"]["trace"]["logical_path"],
)
capacity = load_module(
    "freeze_capacity_decision",
    capacity_source,
    payload["sources"]["capacity"]["logical_path"],
)
runner_v1 = load_module(
    "run_paired",
    runner_v1_source,
    payload["sources"]["runner_v1"]["logical_path"],
)
v1 = load_module(
    "analyze_results",
    v1_source,
    payload["sources"]["analyzer_v1"]["logical_path"],
)
v2 = load_module(
    "analyze_results_v2",
    v2_source,
    payload["sources"]["analyzer_v2"]["logical_path"],
)
analyzer = load_module(
    "analyze_results_v3",
    analyzer_source,
    payload["sources"]["analyzer"]["logical_path"],
)
runner_v1.Path = SnapshotPath
v1.Path = SnapshotPath
trace.Path = SnapshotPath
capacity.Path = SnapshotPath
v2.Path = SnapshotPath
analyzer.Path = SnapshotPath

if payload.get("evidence_guard_probe") is True:
    probe_path = SnapshotPath(payload["evidence_guard_probe_path"])
    guard = analyzer._EvidenceGuard()
    try:
        snapshot = guard.capture(probe_path, "bootstrap evidence probe")
        guard.reverify()
        result = {
            "private_mode": snapshot.private_path.stat().st_mode & 0o777,
            "sha256": snapshot.sha256,
            "source_path": str(snapshot.source_path),
        }
    finally:
        guard.close()
    sys.stdout.write(
        json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
    )
    raise SystemExit(0)

validated = analyzer.validate_session(
    SnapshotPath(payload["session_root"])
)
combined = analyzer.analyze_validated_sessions([validated])
if combined["mode"] != "fidelity":
    raise SystemExit("authenticated snapshot is not a fidelity session")
gate = combined["fidelity_gate"]
if gate is None or not gate["gate_pass"]:
    raise SystemExit("authenticated snapshot does not pass the fidelity gate")
receipt = analyzer.fidelity_gate_receipt(
    analyzer_sha256=payload["sources"]["analyzer"]["sha256"],
    execution_epoch_id=combined["execution_epoch_id"],
    cohort_id=combined["cohort_id"],
    prior_failed_cohort=combined["prior_failed_cohort"],
    input_sessions=combined["input_sessions"],
    gate=gate,
)
sys.stdout.buffer.write(analyzer.canonical_json_bytes(receipt))
"""

EXPECTED_POWER_POLICY: dict[str, Any] = {
    "allowed_statuses": [
        "charging",
        "charged",
        "finishing charge",
    ],
    "battery_record_count": 1,
    "comparison_rules": {
        "absolute_floor_overrides_drop_tolerance": True,
        "attempt_boundary": "after_level >= before_level - 1",
        "cooldown": "after_level >= before_level - 1",
        "session": "level >= post_cooldown_baseline - 2",
    },
    "line_parsing": {
        "additional_battery_lines_rejected": True,
        "additional_source_lines_rejected": True,
        "battery_line_count": 1,
        "battery_record_name_prefix": "InternalBattery",
        "line_endings_normalized": True,
        "malformed_trailing_fields_rejected": True,
        "source_line_count": 1,
        "status_normalization": (
            "trim, collapse ASCII whitespace, lowercase"
        ),
        "unknown_required_fields_rejected": True,
    },
    "maximum_attempt_boundary_drop_percentage_points": 1,
    "maximum_battery_percent": 100,
    "maximum_cooldown_drop_percentage_points": 1,
    "maximum_session_drop_percentage_points": 2,
    "minimum_battery_percent": 80,
    "policy_id": "darwin-ac-battery-stable-v1",
    "power_source": POWER_MODE,
    "present_required": True,
    "probe_during_timed_process": False,
    "probe_points": [
        "preflight",
        "cooldown-complete",
        "before-each-method-attempt",
        "after-each-method-attempt",
        "session-complete",
    ],
    "session_drop_baseline": "cooldown-complete",
    "unknown_or_ambiguous_fails_closed": True,
}

EXPECTED_PROCESS_CONTROL_POLICY: dict[str, Any] = {
    "already_exited_process_lookup_is_anomaly": False,
    "anomaly_terminal": "measurement-wrapper-failure",
    "group_eperm_direct_child_fallback": True,
    "initial_group_eperm_remains_anomaly": True,
    "post_measurement_artifact_failure_terminal": (
        "measurement-wrapper-failure"
    ),
    "process_group_empty_before_finalize": True,
    "reap_before_result_or_hash": True,
    "retain_resource_when_wait4_succeeds": True,
    "retry_interrupted_waits": True,
    "unrecoverable_control_action": "abort-session-unsealed",
}

EXPECTED_FIDELITY_GATE: dict[str, Any] = {
    "planned_method_attempt_slots": 40,
    "planned_pair_blocks": 20,
    "required_completed_attempts": 40,
    "expected_frames_compared_per_pair": 200,
    "expected_host_valid_pairs": 20,
    "expected_valid_pairs": 20,
    "failure_rule": (
        "any planned attempt or pair failing any requirement fails the "
        "complete gate"
    ),
    "partial_trace_progress_admissible": False,
    "required_pair_fields": {
        "config_sha256_equal": True,
        "pair_valid": True,
        "paper_metrics_equal": True,
        "semantic_trace_equal": True,
        "summary_parity_fields_equal": True,
        "task_valid": True,
    },
    "required_terminal_category": "completed",
    "require_complete_well_formed_trace_and_summary": True,
}


class FreezeV3Error(RuntimeError):
    """The amended plan cannot be frozen without ambiguity."""


class _DuplicateKey(ValueError):
    pass


@dataclass(frozen=True)
class FrozenPlanV3:
    """The immutable artifacts created by :func:`freeze_plan_v3`."""

    plan_path: Path
    plan_sha256: str
    scenario_paths: tuple[Path, ...]


@dataclass(frozen=True)
class _AuthenticatedMaterializer:
    materializer_source: bytes
    generator_source: bytes


@dataclass(frozen=True)
class _AuthenticatedAnalyzer:
    analyzer_source: bytes
    analyzer_v2_source: bytes
    analyzer_v1_source: bytes
    runner_v1_source: bytes
    trace_source: bytes
    capacity_source: bytes


@dataclass(frozen=True)
class _DirectoryHandle:
    fd: int
    parent_fd: int | None
    name: str | None
    identity: tuple[int, int]
    created: bool


def _reject_duplicate_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value!r}")


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _strict_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise FreezeV3Error(f"{label} is not a regular non-symlink file")
    data = path.read_bytes()
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateKey,
        ValueError,
    ) as error:
        raise FreezeV3Error(f"{label} is not strict JSON: {error}") from error
    if type(value) is not dict:
        raise FreezeV3Error(f"{label} must contain one JSON object")
    return value, data


def _exact_keys(
    value: dict[str, Any],
    required: set[str],
    label: str,
) -> None:
    observed = set(value)
    if observed != required:
        missing = sorted(required - observed)
        extra = sorted(observed - required)
        raise FreezeV3Error(
            f"{label} keys are ambiguous; missing={missing}, extra={extra}"
        )


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise FreezeV3Error(f"{label} must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if type(value) is not list:
        raise FreezeV3Error(f"{label} must be an array")
    return value


def _strict_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise FreezeV3Error(f"{label} must be a JSON boolean")
    return value


def _strict_str(value: Any, label: str) -> str:
    if type(value) is not str:
        raise FreezeV3Error(f"{label} must be a JSON string")
    return value


def _strict_int(
    value: Any,
    label: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if type(value) is not int:
        raise FreezeV3Error(f"{label} must be a JSON integer")
    if minimum is not None and value < minimum:
        raise FreezeV3Error(f"{label} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise FreezeV3Error(f"{label} must be at most {maximum}")
    return value


def _strict_float(value: Any, label: str) -> float:
    if type(value) is not float:
        raise FreezeV3Error(f"{label} must be a JSON floating-point number")
    return value


def _validate_json_type_shape(
    value: Any,
    expected: Any,
    label: str,
) -> None:
    if type(expected) is bool:
        _strict_bool(value, label)
        return
    if type(expected) is int:
        _strict_int(value, label)
        return
    if type(expected) is float:
        _strict_float(value, label)
        return
    if type(expected) is str:
        _strict_str(value, label)
        return
    if expected is None:
        if value is not None:
            raise FreezeV3Error(f"{label} must be JSON null")
        return
    if type(value) is not type(expected):
        raise FreezeV3Error(
            f"{label} has JSON type {type(value).__name__}; "
            f"expected {type(expected).__name__}"
        )
    if isinstance(expected, dict):
        for key in expected.keys() & value.keys():
            _validate_json_type_shape(
                value[key], expected[key], f"{label}.{key}"
            )
    elif isinstance(expected, list):
        for index, (observed_item, expected_item) in enumerate(
            zip(value, expected, strict=False)
        ):
            _validate_json_type_shape(
                observed_item,
                expected_item,
                f"{label}[{index}]",
            )


def _exact_json_value(value: Any, expected: Any, label: str) -> None:
    _validate_json_type_shape(value, expected, label)
    try:
        observed_bytes = canonical_json_bytes(value)
        expected_bytes = canonical_json_bytes(expected)
    except (TypeError, ValueError) as error:
        raise FreezeV3Error(f"{label} is not strict JSON: {error}") from error
    if observed_bytes != expected_bytes:
        raise FreezeV3Error(f"{label} changed from the exact frozen value")


def _sha256(value: Any, label: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None:
        raise FreezeV3Error(
            f"{label} must be 64 lower-case hexadecimal digits"
        )
    return value


def _git_object(value: Any, label: str) -> str:
    if type(value) is not str or GIT_OBJECT_RE.fullmatch(value) is None:
        raise FreezeV3Error(f"{label} must be a full Git object identifier")
    return value


def _git(
    repository: Path,
    *arguments: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        text=True,
        capture_output=True,
    )
    if check and completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise FreezeV3Error(
            f"git {' '.join(arguments)} failed in {repository}: {detail}"
        )
    return completed


def _verified_repository(path: Path) -> Path:
    repository = path.expanduser().resolve()
    if path.is_symlink() or not repository.is_dir():
        raise FreezeV3Error("repository root is not a real directory")
    top = Path(
        _git(repository, "rev-parse", "--show-toplevel").stdout.strip()
    ).resolve()
    if top != repository:
        raise FreezeV3Error("repository_root is not the Git toplevel")
    remotes = [
        item
        for item in _git(repository, "remote").stdout.splitlines()
        if item.strip()
    ]
    if remotes:
        raise FreezeV3Error(
            "amended plan freezing requires a local repository with no remotes"
        )
    return repository


def _relative_path(value: Any, label: str) -> Path:
    if type(value) is not str or not value:
        raise FreezeV3Error(f"{label} must be a non-empty relative path")
    relative = Path(value)
    if (
        relative.is_absolute()
        or relative == Path(".")
        or ".." in relative.parts
        or relative.as_posix() != value
    ):
        raise FreezeV3Error(
            f"{label} must be one canonical repository-relative path"
        )
    return relative


def _inside_repository(
    repository: Path,
    relative: Path,
    label: str,
    *,
    directory: bool = False,
) -> Path:
    candidate = repository
    for part in relative.parts:
        candidate /= part
        if candidate.is_symlink():
            raise FreezeV3Error(
                f"{label} contains a symbolic link: {candidate}"
            )
    resolved = candidate.resolve()
    try:
        resolved.relative_to(repository)
    except ValueError as error:
        raise FreezeV3Error(f"{label} escapes the repository") from error
    exists = resolved.is_dir() if directory else resolved.is_file()
    if not exists:
        kind = "directory" if directory else "regular file"
        raise FreezeV3Error(f"{label} is not a {kind}: {resolved}")
    return resolved


def _future_path_inside_repository(
    repository: Path,
    relative: Path,
    label: str,
) -> Path:
    """Return a lexical future path after rejecting every symlink component."""

    if relative.is_absolute() or ".." in relative.parts:
        raise FreezeV3Error(
            f"{label} must be a canonical repository-relative path"
        )
    candidate = repository
    for part in relative.parts:
        candidate /= part
        if candidate.is_symlink():
            raise FreezeV3Error(
                f"{label} contains a symbolic link: {candidate}"
            )
    try:
        candidate.relative_to(repository)
    except ValueError as error:
        raise FreezeV3Error(f"{label} escapes the repository") from error
    return candidate


def _assert_committed_clean(
    repository: Path,
    path: Path,
    label: str,
) -> None:
    relative = path.relative_to(repository)
    _git(
        repository,
        "ls-files",
        "--error-unmatch",
        "--",
        str(relative),
    )
    for arguments in (
        ("diff", "--quiet", "HEAD", "--", str(relative)),
        ("diff", "--cached", "--quiet", "--", str(relative)),
    ):
        completed = _git(repository, *arguments, check=False)
        if completed.returncode not in {0, 1}:
            detail = (completed.stderr or completed.stdout).strip()
            raise FreezeV3Error(
                f"cannot verify {label} Git state: {detail}"
            )
        if completed.returncode == 1:
            raise FreezeV3Error(
                f"{label} has mutable uncommitted or staged bytes"
            )


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _stable_file_snapshot(
    path: Path,
    label: str,
) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise FreezeV3Error(
            f"cannot open {label} without following links: {error}"
        ) from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise FreezeV3Error(f"{label} is not a regular file")
        blocks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            blocks.append(block)
        after = os.fstat(descriptor)
        data = b"".join(blocks)
        if (
            _identity(before) != _identity(after)
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or len(data) != after.st_size
        ):
            raise FreezeV3Error(f"{label} changed while being authenticated")
        try:
            current = os.stat(path, follow_symlinks=False)
        except OSError as error:
            raise FreezeV3Error(
                f"{label} disappeared while being authenticated"
            ) from error
        if (
            _identity(current) != _identity(after)
            or not stat.S_ISREG(current.st_mode)
        ):
            raise FreezeV3Error(
                f"{label} path changed while being authenticated"
            )
        return data, after
    finally:
        os.close(descriptor)


def _committed_artifact_snapshot(
    repository: Path,
    relative: Path,
    label: str,
) -> tuple[dict[str, Any], bytes]:
    path = _inside_repository(repository, relative, label)
    _assert_committed_clean(repository, path, label)
    data, metadata = _stable_file_snapshot(path, label)
    _assert_committed_clean(repository, path, label)
    try:
        current = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise FreezeV3Error(
            f"{label} disappeared after Git authentication"
        ) from error
    if _identity(current) != _identity(metadata):
        raise FreezeV3Error(
            f"{label} path changed after Git authentication"
        )
    return (
        {
            "path": str(path),
            "sha256": sha256_bytes(data),
            "bytes": len(data),
        },
        data,
    )


def _committed_artifact(
    repository: Path,
    relative: Path,
    label: str,
) -> dict[str, Any]:
    artifact, _ = _committed_artifact_snapshot(
        repository, relative, label
    )
    return artifact


def _committed_artifact_at_path(
    repository: Path,
    path: Path,
    label: str,
) -> dict[str, Any]:
    try:
        relative = path.relative_to(repository)
    except ValueError as error:
        raise FreezeV3Error(f"{label} escapes the repository") from error
    return _committed_artifact(repository, relative, label)


def _registered_artifact(
    repository: Path,
    value: Any,
    label: str,
) -> dict[str, Any]:
    record = _mapping(value, label)
    _exact_keys(record, {"path", "sha256"}, label)
    relative = _relative_path(record["path"], f"{label}.path")
    artifact = _committed_artifact(repository, relative, label)
    expected = _sha256(record["sha256"], f"{label}.sha256")
    if artifact["sha256"] != expected:
        raise FreezeV3Error(f"{label} SHA-256 disagrees with registration")
    return artifact


def _without_bytes(record: dict[str, Any]) -> dict[str, str]:
    return {
        "path": str(record["path"]),
        "sha256": str(record["sha256"]),
    }


def _verified_config_template(
    repository: Path,
) -> dict[str, Any]:
    artifact = _committed_artifact(
        repository, CONFIG_TEMPLATE_PATH, "config template"
    )
    path = Path(str(artifact["path"]))
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise FreezeV3Error("config template is not UTF-8") from error
    matches = TIMESTEP_CONFIG_RE.findall(text)
    if len(matches) != 1 or float(matches[0]) != 0.1:
        raise FreezeV3Error(
            "config template must contain exactly one TIMESTEP: 0.1"
        )
    return artifact


def _verified_plan_freezer(repository: Path) -> dict[str, Any]:
    expected = (repository / PLAN_FREEZER_PATH).resolve()
    executing = Path(__file__).resolve()
    if executing != expected:
        raise FreezeV3Error(
            f"executing plan freezer must be the repository artifact {expected}"
        )
    return _committed_artifact(
        repository, PLAN_FREEZER_PATH, "v3 plan freezer"
    )


def _verified_trigger_artifacts(
    repository: Path,
    amendment: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    trigger = _mapping(amendment.get("trigger"), "trigger")
    _exact_keys(
        trigger,
        {
            "analysis",
            "completed_utc",
            "hashes",
            "original_grid",
            "original_executed_attempts",
            "original_planned_attempt_slots",
            "original_planned_pair_blocks",
            "original_reference_anchor_n",
            "session_path",
            "terminal_counts",
            "valid_pair_count",
        },
        "trigger",
    )
    _exact_json_value(
        {
            "completed_utc": trigger.get("completed_utc"),
            "original_grid": trigger.get("original_grid"),
            "original_executed_attempts": trigger.get(
                "original_executed_attempts"
            ),
            "original_planned_attempt_slots": trigger.get(
                "original_planned_attempt_slots"
            ),
            "original_planned_pair_blocks": trigger.get(
                "original_planned_pair_blocks"
            ),
            "original_reference_anchor_n": trigger.get(
                "original_reference_anchor_n"
            ),
            "session_path": trigger.get("session_path"),
            "terminal_counts": trigger.get("terminal_counts"),
            "valid_pair_count": trigger.get("valid_pair_count"),
        },
        {
            "completed_utc": "2026-07-28T15:10:10.991023Z",
            "original_grid": [384, 471],
            "original_executed_attempts": 40,
            "original_planned_attempt_slots": 40,
            "original_planned_pair_blocks": 20,
            "original_reference_anchor_n": 384,
            "session_path": ORIGINAL_TRIGGER_SESSION_ROOT.as_posix(),
            "terminal_counts": {
                "measurement-wrapper-failure": 1,
                "runner-timeout": 39,
            },
            "valid_pair_count": 0,
        },
        "trigger historical outcome",
    )

    analysis = _mapping(trigger.get("analysis"), "trigger.analysis")
    _exact_keys(analysis, {"path", "sha256"}, "trigger.analysis")
    if analysis.get("path") != ORIGINAL_TRIGGER_ANALYSIS_PATH.as_posix():
        raise FreezeV3Error("trigger analysis path changed")
    artifacts = {
        "trigger_analysis": _registered_artifact(
            repository, analysis, "trigger analysis"
        )
    }

    session_relative = _relative_path(
        trigger.get("session_path"), "trigger.session_path"
    )
    if session_relative != ORIGINAL_TRIGGER_SESSION_ROOT:
        raise FreezeV3Error("trigger session path changed")
    session_root = _inside_repository(
        repository,
        session_relative,
        "trigger session",
        directory=True,
    )
    hashes = _mapping(trigger.get("hashes"), "trigger.hashes")
    file_records = {
        "frozen_plan_json_sha256": (
            "frozen_plan.json",
            "trigger_frozen_plan",
        ),
        "session_json_sha256": ("session.json", "trigger_session"),
        "runs_jsonl_sha256": ("runs.jsonl", "trigger_runs"),
        "pairs_jsonl_sha256": ("pairs.jsonl", "trigger_pairs"),
    }
    _exact_keys(hashes, set(file_records), "trigger.hashes")
    for field, (filename, artifact_name) in file_records.items():
        expected_sha = _sha256(hashes[field], f"trigger.hashes.{field}")
        artifact = _committed_artifact_at_path(
            repository,
            session_root / filename,
            f"trigger {filename}",
        )
        if artifact["sha256"] != expected_sha:
            raise FreezeV3Error(f"trigger {filename} SHA-256 changed")
        artifacts[artifact_name] = artifact
    return artifacts


def _validate_amendment(
    repository: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    amendment_path = _inside_repository(
        repository, AMENDMENT_PATH, "post-outcome amendment"
    )
    _assert_committed_clean(
        repository, amendment_path, "post-outcome amendment"
    )
    amendment, amendment_bytes = _strict_json(
        amendment_path, "post-outcome amendment"
    )
    amendment_sha = sha256_bytes(amendment_bytes)
    if amendment_sha != AMENDMENT_SHA256:
        raise FreezeV3Error(
            "post-outcome amendment SHA-256 changed: "
            f"{amendment_sha} != {AMENDMENT_SHA256}"
        )
    required_keys = {
        "amendment_id",
        "amendment_protocol",
        "base_artifacts",
        "fidelity_gate",
        "followup_fidelity_design",
        "frozen_before_any_amended_attempt",
        "original_cohort_policy",
        "power_policy",
        "process_control_policy",
        "prospective_capacity_unlock",
        "reporting",
        "schema_version",
        "scientific_role",
        "status",
        "timing_if_fidelity_passes",
        "trigger",
        "validation_data_accessed_at_freeze",
    }
    _exact_keys(amendment, required_keys, "post-outcome amendment")
    if amendment.get("schema_version") != AMENDMENT_SCHEMA:
        raise FreezeV3Error("post-outcome amendment schema changed")
    if amendment.get("amendment_id") != AMENDMENT_ID:
        raise FreezeV3Error("post-outcome amendment ID changed")
    if (
        amendment.get("status") != "frozen-before-any-amended-attempt"
        or amendment.get("frozen_before_any_amended_attempt") is not True
        or amendment.get("validation_data_accessed_at_freeze") is not False
    ):
        raise FreezeV3Error(
            "post-outcome amendment is not prospectively frozen"
        )
    if (
        amendment.get("scientific_role")
        != "exploratory-feasibility-and-fidelity-gate-only"
    ):
        raise FreezeV3Error("post-outcome scientific role changed")
    _exact_json_value(
        amendment.get("power_policy"),
        EXPECTED_POWER_POLICY,
        "power policy",
    )
    _exact_json_value(
        amendment.get("process_control_policy"),
        EXPECTED_PROCESS_CONTROL_POLICY,
        "process-control policy",
    )
    _exact_json_value(
        amendment.get("fidelity_gate"),
        EXPECTED_FIDELITY_GATE,
        "fidelity gate",
    )

    original = _mapping(
        amendment.get("original_cohort_policy"),
        "original_cohort_policy",
    )
    _exact_keys(
        original,
        {
            "all_attempt_denominator",
            "confirmatory_status",
            "pool_with_followup_for_confirmatory_estimation",
            "replace_or_withdraw",
        },
        "original_cohort_policy",
    )
    _exact_json_value(
        original,
        {
            "all_attempt_denominator": True,
            "confirmatory_status": (
                "no-valid-pair-under-original-300-second-design"
            ),
            "pool_with_followup_for_confirmatory_estimation": False,
            "replace_or_withdraw": False,
        },
        "historical AC-only cohort distinction",
    )

    artifacts: dict[str, dict[str, Any]] = {
        "post_outcome_amendment": {
            "path": str(amendment_path),
            "sha256": sha256_bytes(amendment_bytes),
            "bytes": len(amendment_bytes),
        }
    }
    base = _mapping(amendment.get("base_artifacts"), "base_artifacts")
    _exact_keys(
        base,
        {"protocol", "experiment_manifest", "environment_manifest"},
        "base_artifacts",
    )
    artifacts["protocol"] = _registered_artifact(
        repository, base["protocol"], "base protocol"
    )
    artifacts["experiment_manifest"] = _registered_artifact(
        repository,
        base["experiment_manifest"],
        "base experiment manifest",
    )
    artifacts["environment_manifest"] = _registered_artifact(
        repository,
        base["environment_manifest"],
        "environment manifest",
    )
    artifacts["amendment_protocol"] = _registered_artifact(
        repository,
        amendment.get("amendment_protocol"),
        "amendment protocol",
    )
    artifacts.update(_verified_trigger_artifacts(repository, amendment))
    return amendment, artifacts


def _canonical_committed_json(
    repository: Path,
    path: Path,
    label: str,
) -> tuple[dict[str, Any], bytes]:
    artifact, data = _committed_artifact_snapshot(
        repository, path, label
    )
    value, parsed_data = _strict_json(Path(artifact["path"]), label)
    if parsed_data != data or data != canonical_json_bytes(value):
        raise FreezeV3Error(f"{label} must be canonical JSON")
    return value, data


def _validate_prior_failure_evidence(
    repository: Path,
    registry: dict[str, Any],
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    source_plan, source_data = _canonical_committed_json(
        repository, PRIOR_FAILED_PLAN_PATH, "prior failed source plan"
    )
    frozen_plan, frozen_data = _canonical_committed_json(
        repository,
        PRIOR_FAILED_RESULT_ROOT / "frozen_plan.json",
        "prior failed frozen plan",
    )
    if source_data != frozen_data or source_plan != frozen_plan:
        raise FreezeV3Error(
            "prior failed source plan and frozen plan differ"
        )
    expected_plan_identity = {
        "schema_version": "dancers-laptop-comparison-plan-v2",
        "amendment_id": AMENDMENT_ID,
        "cohort_id": "fractal-high-lower-count-fidelity-amended-v1",
        "design_role": "outcome-informed-exploratory-fidelity",
        "mode": "fidelity",
        "planned_method_attempt_slots": 40,
        "planned_pair_blocks": 20,
        "order_seed": 8089505177631375378,
    }
    for field, expected in expected_plan_identity.items():
        _exact_json_value(
            source_plan.get(field),
            expected,
            f"prior failed plan.{field}",
        )

    session, _ = _canonical_committed_json(
        repository,
        PRIOR_FAILED_RESULT_ROOT / "session.json",
        "prior failed session",
    )
    for field, expected in {
        "schema_version": "dancers-laptop-comparison-session-v2",
        "cohort_id": "fractal-high-lower-count-fidelity-amended-v1",
        "result_root": str(repository / PRIOR_FAILED_RESULT_ROOT),
        "attempt_count": 0,
        "pair_count": 0,
        "planned_method_attempt_slots": 40,
        "planned_pair_blocks": 20,
        "executed_method_attempt_slots": 0,
        "completed_method_attempt_slots": 0,
        "valid_method_attempt_slots": 0,
        "executed_pair_blocks": 0,
        "valid_pair_blocks": 0,
        "all_attempts_completed": False,
        "all_pairs_valid": False,
        "stopped_early": True,
        "session_power_valid": False,
    }.items():
        _exact_json_value(
            session.get(field), expected, f"prior failed session.{field}"
        )
    cooldown = _mapping(
        session.get("idle_cooldown"), "prior failed session.idle_cooldown"
    )
    for field, expected in {
        "performed": True,
        "eligible": True,
        "valid": True,
        "required_seconds": 300,
    }.items():
        _exact_json_value(
            cooldown.get(field),
            expected,
            f"prior failed session.idle_cooldown.{field}",
        )

    abort, _ = _canonical_committed_json(
        repository,
        PRIOR_FAILED_RESULT_ROOT / "orchestration_abort.json",
        "prior failed orchestration abort",
    )
    for field, expected in {
        "schema_version": "dancers-orchestration-abort-v2",
        "stage": "pre-first-attempt",
        "process_started": False,
    }.items():
        _exact_json_value(
            abort.get(field),
            expected,
            f"prior failed orchestration abort.{field}",
        )
    if "registered result_root mkdir flags changed" not in _strict_str(
        abort.get("error"), "prior failed orchestration abort.error"
    ):
        raise FreezeV3Error("prior failure reason changed")

    analysis, _ = _canonical_committed_json(
        repository,
        PRIOR_FAILED_ANALYSIS_PATH,
        "prior failed analysis",
    )
    gate = _mapping(
        analysis.get("fidelity_gate"), "prior failed analysis.fidelity_gate"
    )
    for field, expected in {
        "planned_method_attempt_slots": 40,
        "executed_attempt_count": 0,
        "planned_pair_blocks": 20,
        "completed_attempt_count": 0,
        "valid_pair_count": 0,
        "host_valid_pair_count": 0,
        "gate_pass": False,
    }.items():
        _exact_json_value(
            gate.get(field),
            expected,
            f"prior failed analysis.fidelity_gate.{field}",
        )
    for filename in ("runs.jsonl", "pairs.jsonl"):
        path = repository / PRIOR_FAILED_RESULT_ROOT / filename
        if path.read_bytes() != b"":
            raise FreezeV3Error(
                f"prior failed {filename} must remain empty"
            )

    prior = _mapping(
        registry.get("prior_failed_cohort"),
        "rerun registry.prior_failed_cohort",
    )
    for field, expected in {
        "cohort_id": "fractal-high-lower-count-fidelity-amended-v1",
        "path": PRIOR_FAILED_RESULT_ROOT.as_posix(),
        "planned_method_attempt_slots": 40,
        "executed_method_attempt_slots": 0,
        "missing_method_attempt_slots": 40,
        "planned_pair_blocks": 20,
        "executed_pair_blocks": 0,
        "missing_pair_blocks": 20,
    }.items():
        _exact_json_value(
            prior.get(field),
            expected,
            f"rerun registry.prior_failed_cohort.{field}",
        )
    disposition = _mapping(
        prior.get("disposition"),
        "rerun registry.prior_failed_cohort.disposition",
    )
    if (
        disposition.get("gate_pass") is not False
        or any(
            disposition.get(field) is not True
            for field in (
                "not_overwritten",
                "not_pooled",
                "not_replaced",
                "not_resumed",
                "not_withdrawn",
            )
        )
    ):
        raise FreezeV3Error("prior failed cohort disposition changed")
    failure = _mapping(
        prior.get("failure"),
        "rerun registry.prior_failed_cohort.failure",
    )
    if (
        failure.get("infrastructure_only") is not True
        or failure.get("process_started") is not False
        or failure.get("stage") != "pre-first-attempt"
        or "registered result_root mkdir flags changed"
        not in str(failure.get("error"))
    ):
        raise FreezeV3Error("prior failed cohort rationale changed")
    if prior.get("source_head_commit") != (
        "bd3c72d77aabe147017b456bb883ed2c48e533ca"
    ):
        raise FreezeV3Error("prior failed source commit changed")

    for plan_key in PRIOR_BINDING_KEY_MAP:
        if plan_key not in artifacts:
            raise FreezeV3Error(
                f"prior failure artifact {plan_key} is missing"
            )
    return source_plan


def _validate_rerun_registry(
    repository: Path,
) -> tuple[
    dict[str, Any],
    dict[str, dict[str, Any]],
    dict[str, Any],
]:
    registry_artifact, registry_data = _committed_artifact_snapshot(
        repository, RERUN_REGISTRY_PATH, "rerun registry"
    )
    if registry_artifact["sha256"] != RERUN_REGISTRY_SHA256:
        raise FreezeV3Error("rerun registry is not the approved frozen hash")
    registry, parsed_data = _strict_json(
        Path(registry_artifact["path"]), "rerun registry"
    )
    if parsed_data != registry_data:
        raise FreezeV3Error("rerun registry changed while being read")
    required_registry_keys = {
        "addendum_protocol",
        "amendment_id",
        "execution_epoch_id",
        "fidelity_gate",
        "freeze_policy",
        "new_cohort",
        "prior_failed_cohort",
        "reporting",
        "required_v3_plan_artifact_contract",
        "rerun_justification",
        "schema_version",
        "scientific_invariance",
        "source_head_commit",
        "status",
        "successor_tooling",
        "timing_unlock",
    }
    _exact_keys(registry, required_registry_keys, "rerun registry")
    for field, expected in {
        "schema_version": RERUN_REGISTRY_SCHEMA,
        "amendment_id": AMENDMENT_ID,
        "execution_epoch_id": EXECUTION_EPOCH_ID,
        "source_head_commit": (
            "bd3c72d77aabe147017b456bb883ed2c48e533ca"
        ),
        "status": "frozen-before-any-rerun-attempt",
    }.items():
        _exact_json_value(
            registry.get(field), expected, f"rerun registry.{field}"
        )

    addendum = _registered_artifact(
        repository,
        registry.get("addendum_protocol"),
        "rerun addendum",
    )
    if (
        Path(addendum["path"]) != (repository / RERUN_ADDENDUM_PATH)
        or addendum["sha256"] != RERUN_ADDENDUM_SHA256
    ):
        raise FreezeV3Error("rerun addendum is not the approved frozen file")

    contract = _mapping(
        registry.get("required_v3_plan_artifact_contract"),
        "rerun registry.required_v3_plan_artifact_contract",
    )
    _exact_keys(
        contract,
        {
            "common_v2_binding_rule",
            "mandatory_artifact_keys",
            "mandatory_common_v2_artifact_keys",
            "prior_binding_key_map",
        },
        "rerun registry artifact contract",
    )
    mandatory = _list(
        contract.get("mandatory_artifact_keys"),
        "rerun registry mandatory_artifact_keys",
    )
    common = _list(
        contract.get("mandatory_common_v2_artifact_keys"),
        "rerun registry mandatory_common_v2_artifact_keys",
    )
    if (
        any(type(item) is not str for item in mandatory)
        or len(mandatory) != len(set(mandatory))
        or set(mandatory) != MANDATORY_ARTIFACT_KEYS
    ):
        raise FreezeV3Error("rerun mandatory artifact contract changed")
    if (
        any(type(item) is not str for item in common)
        or len(common) != len(set(common))
        or set(common) != COMMON_V2_ARTIFACT_KEYS
    ):
        raise FreezeV3Error("rerun common v2 artifact contract changed")
    _exact_json_value(
        contract.get("prior_binding_key_map"),
        PRIOR_BINDING_KEY_MAP,
        "rerun prior binding key map",
    )

    prior = _mapping(
        registry.get("prior_failed_cohort"),
        "rerun registry.prior_failed_cohort",
    )
    registered_prior = _mapping(
        prior.get("artifacts"),
        "rerun registry.prior_failed_cohort.artifacts",
    )
    _exact_keys(
        registered_prior,
        set(PRIOR_REGISTRY_ARTIFACT_KEYS),
        "rerun registry.prior_failed_cohort.artifacts",
    )
    registered_prior_artifacts = {
        key: _registered_artifact(
            repository,
            registered_prior[key],
            f"rerun prior artifact {key}",
        )
        for key in sorted(PRIOR_REGISTRY_ARTIFACT_KEYS)
    }
    artifacts: dict[str, dict[str, Any]] = {
        "rerun_addendum": addendum,
        "rerun_registry": registry_artifact,
    }
    for plan_key, registry_key in PRIOR_BINDING_KEY_MAP.items():
        artifacts[plan_key] = registered_prior_artifacts[registry_key]
    expected_paths = {
        "prior_failed_plan": PRIOR_FAILED_PLAN_PATH,
        "prior_failed_frozen_plan": (
            PRIOR_FAILED_RESULT_ROOT / "frozen_plan.json"
        ),
        "prior_failed_run_manifest": (
            PRIOR_FAILED_RESULT_ROOT / "run_manifest.json"
        ),
        "prior_failed_preflight": (
            PRIOR_FAILED_RESULT_ROOT / "preflight.json"
        ),
        "prior_failed_session": PRIOR_FAILED_RESULT_ROOT / "session.json",
        "prior_failed_runs": PRIOR_FAILED_RESULT_ROOT / "runs.jsonl",
        "prior_failed_runs_csv": PRIOR_FAILED_RESULT_ROOT / "runs.csv",
        "prior_failed_pairs": PRIOR_FAILED_RESULT_ROOT / "pairs.jsonl",
        "prior_failed_analysis": PRIOR_FAILED_ANALYSIS_PATH,
        "prior_failed_orchestration_abort": (
            PRIOR_FAILED_RESULT_ROOT / "orchestration_abort.json"
        ),
        "runner_v2_base": RUNNER_V2_BASE_PATH,
        "analyzer_v2_base": ANALYZER_V2_BASE_PATH,
        "freezer_v2_base": FREEZER_V2_BASE_PATH,
        "parent_amendment_protocol": Path(
            "docs/comparison/EXPERIMENT_PROTOCOL_AMENDMENT_FH_LOWER_V1.md"
        ),
        "parent_amendment_registry": AMENDMENT_PATH,
        "scenario_192": (
            PLAN_ROOT
            / "fractal-high-lower-count-fidelity"
            / "scenarios"
            / "n-192.json"
        ),
        "scenario_256": (
            PLAN_ROOT
            / "fractal-high-lower-count-fidelity"
            / "scenarios"
            / "n-256.json"
        ),
    }
    for key, relative in expected_paths.items():
        record = (
            artifacts[key]
            if key in artifacts
            else registered_prior_artifacts[key]
        )
        if Path(record["path"]) != repository / relative:
            raise FreezeV3Error(
                f"{key} path is not the registered prior artifact"
            )

    new_cohort = _mapping(
        registry.get("new_cohort"), "rerun registry.new_cohort"
    )
    for field, expected in {
        "cohort_id": FIDELITY_COHORT_ID,
        "design_role": "outcome-informed-exploratory-fidelity",
        "mode": "fidelity",
        "phase": "calibration",
        "planned_method_attempt_slots": 40,
        "planned_pair_blocks": 20,
        "repetitions": 1,
        "separate_denominator": True,
    }.items():
        _exact_json_value(
            new_cohort.get(field),
            expected,
            f"rerun registry.new_cohort.{field}",
        )
    new_plan = _mapping(
        new_cohort.get("plan"), "rerun registry.new_cohort.plan"
    )
    _exact_json_value(
        new_plan,
        {
            "basename": FIDELITY_OUTPUT_NAME,
            "path": (
                PLAN_ROOT / FIDELITY_OUTPUT_NAME / "plan.json"
            ).as_posix(),
            "root": PLAN_ROOT.as_posix(),
            "scenario_paths": {
                str(count): (
                    PLAN_ROOT
                    / FIDELITY_OUTPUT_NAME
                    / "scenarios"
                    / f"n-{count}.json"
                ).as_posix()
                for count in (192, 256)
            },
            "schema_version": PLAN_SCHEMA_V3,
        },
        "rerun registry fidelity plan",
    )
    new_result = _mapping(
        new_cohort.get("result"), "rerun registry.new_cohort.result"
    )
    _exact_json_value(
        new_result,
        {
            "analysis_path": (
                RESULT_ROOT / f"{FIDELITY_OUTPUT_NAME}-analysis.json"
            ).as_posix(),
            "basename": FIDELITY_OUTPUT_NAME,
            "receipt_path": (
                RESULT_ROOT / FIDELITY_RECEIPT_NAME
            ).as_posix(),
            "root": (
                RESULT_ROOT / FIDELITY_OUTPUT_NAME
            ).as_posix(),
        },
        "rerun registry fidelity result",
    )

    unlock = _mapping(
        registry.get("timing_unlock"), "rerun registry.timing_unlock"
    )
    for field, expected in {
        "fidelity_cohort_id": FIDELITY_COHORT_ID,
        "fidelity_execution_epoch_id": EXECUTION_EPOCH_ID,
        "passing_receipt_required": True,
        "prior_failure_binding_required": True,
        "receipt_schema_version": FIDELITY_GATE_SCHEMA,
        "v2_failed_gate_cannot_unlock": True,
    }.items():
        _exact_json_value(
            unlock.get(field),
            expected,
            f"rerun registry.timing_unlock.{field}",
        )
    timing = _mapping(
        unlock.get("timing_cohort"),
        "rerun registry.timing_unlock.timing_cohort",
    )
    for field, expected in {
        "cohort_id": TIMING_COHORT_ID,
        "mode": "timing",
        "planned_method_attempt_slots": 120,
        "planned_pair_blocks": 60,
        "repetitions": 3,
        "warmups_per_binary_map": 1,
    }.items():
        _exact_json_value(
            timing.get(field),
            expected,
            f"rerun registry timing cohort.{field}",
        )
    timing_plan = _mapping(
        timing.get("plan"), "rerun registry timing cohort.plan"
    )
    _exact_json_value(
        timing_plan,
        {
            "basename": TIMING_OUTPUT_NAME,
            "path": (
                PLAN_ROOT / TIMING_OUTPUT_NAME / "plan.json"
            ).as_posix(),
            "schema_version": PLAN_SCHEMA_V3,
        },
        "rerun registry timing plan",
    )
    timing_result = _mapping(
        timing.get("result"), "rerun registry timing cohort.result"
    )
    _exact_json_value(
        timing_result,
        {
            "basename": TIMING_OUTPUT_NAME,
            "root": (RESULT_ROOT / TIMING_OUTPUT_NAME).as_posix(),
        },
        "rerun registry timing result",
    )

    freeze_policy = _mapping(
        registry.get("freeze_policy"), "rerun registry.freeze_policy"
    )
    _exact_json_value(
        freeze_policy,
        {
            "all_dependencies_committed_and_clean": True,
            "frozen_before_any_rerun_attempt": True,
            "new_epoch_required_after_any_abort": True,
            "no_overwrite": True,
            "no_resume": True,
            "plan_and_result_destinations_must_be_absent": True,
            "plan_schema_version": PLAN_SCHEMA_V3,
            "v3_tool_hashes_frozen_in_plan_before_first_attempt": True,
        },
        "rerun registry freeze policy",
    )
    scientific = _mapping(
        registry.get("scientific_invariance"),
        "rerun registry.scientific_invariance",
    )
    order = _mapping(
        scientific.get("order"),
        "rerun registry.scientific_invariance.order",
    )
    _exact_json_value(
        order.get("order_seed"),
        FIDELITY_ORDER_SEED,
        "rerun registry fidelity order seed",
    )

    source_plan = _validate_prior_failure_evidence(
        repository, registry, artifacts
    )
    return registry, artifacts, source_plan


def _validate_common_v2_artifacts(
    artifacts: dict[str, dict[str, Any]],
    prior_plan: dict[str, Any],
) -> None:
    prior_artifacts = _mapping(
        prior_plan.get("artifacts"), "prior failed plan.artifacts"
    )
    for key in COMMON_V2_ARTIFACT_KEYS:
        if key not in artifacts:
            raise FreezeV3Error(f"common v2 artifact {key} is missing")
        current = _without_bytes(artifacts[key])
        previous = _mapping(
            prior_artifacts.get(key),
            f"prior failed plan.artifacts.{key}",
        )
        _exact_keys(
            previous, {"path", "sha256"}, f"prior plan artifact {key}"
        )
        _exact_json_value(
            current,
            previous,
            f"common v2 artifact {key} exact prior-plan binding",
        )


def _validate_exact_artifact_contract(
    artifacts: dict[str, dict[str, Any]],
    mode: str,
) -> None:
    expected = set(MANDATORY_ARTIFACT_KEYS | COMMON_V2_ARTIFACT_KEYS)
    if mode == "timing":
        expected.update(TIMING_ONLY_ARTIFACT_KEYS)
    observed = set(artifacts)
    if observed != expected:
        raise FreezeV3Error(
            "v3 plan artifact contract is not exact; "
            f"missing={sorted(expected - observed)}, "
            f"extra={sorted(observed - expected)}"
        )
    expected_count = 40 if mode == "fidelity" else 48
    if len(artifacts) != expected_count:
        raise FreezeV3Error(
            f"{mode} artifact count changed from {expected_count}"
        )


def _scientific_pair(value: Any, label: str) -> dict[str, Any]:
    pair = dict(_mapping(value, label))
    _exact_keys(
        pair,
        {
            "pair_id",
            "environment",
            "seed",
            "robot_count",
            "map",
            "scenario",
        },
        label,
    )
    for artifact_name in ("map", "scenario"):
        artifact = dict(
            _mapping(
                pair[artifact_name],
                f"{label}.{artifact_name}",
            )
        )
        _exact_keys(
            artifact,
            {"path", "sha256", "bytes"},
            f"{label}.{artifact_name}",
        )
        if artifact_name == "scenario":
            artifact.pop("path")
        pair[artifact_name] = artifact
    return pair


def _validate_fidelity_scientific_binding(
    plan: dict[str, Any],
    prior_plan: dict[str, Any],
) -> None:
    fields = {
        "fixed_work_timesteps",
        "timestep_seconds",
        "idle_cooldown_seconds",
        "timeout_seconds",
        "termination_grace_seconds",
        "minimum_free_disk_bytes",
        "maximum_peak_rss_mib",
        "power_mode",
        "power_policy",
        "process_control_policy",
        "thermal_policy",
        "task_validity_predicate_id",
        "repetitions",
        "warmups_per_binary_map",
        "omp_threads",
        "serial_execution",
        "order_seed",
        "trace_detail",
        "planned_method_attempt_slots",
        "planned_pair_blocks",
        "methods",
    }
    for field in sorted(fields):
        _exact_json_value(
            plan.get(field),
            prior_plan.get(field),
            f"fidelity {field} exact prior-plan binding",
        )
    current_pairs = [
        _scientific_pair(item, f"fidelity pairs[{index}]")
        for index, item in enumerate(
            _list(plan.get("pairs"), "fidelity pairs")
        )
    ]
    prior_pairs = [
        _scientific_pair(item, f"prior fidelity pairs[{index}]")
        for index, item in enumerate(
            _list(prior_plan.get("pairs"), "prior fidelity pairs")
        )
    ]
    _exact_json_value(
        current_pairs,
        prior_pairs,
        "fidelity pair order and registered scenario bytes",
    )


def _expected_design(
    amendment: dict[str, Any],
    mode: str,
) -> tuple[dict[str, Any], str]:
    fidelity = _mapping(
        amendment.get("followup_fidelity_design"),
        "followup_fidelity_design",
    )
    required_fidelity = {
        "capacity_search",
        "counts",
        "design_role",
        "environment",
        "maximum_peak_rss_mib",
        "minimum_free_disk_bytes",
        "omp_threads",
        "planned_method_attempt_slots",
        "planned_pair_blocks",
        "fixed_work_timesteps",
        "map_sha256",
        "mode",
        "phase",
        "plan_root",
        "registered_scenario_prefixes",
        "repetitions",
        "result_root",
        "seeds",
        "selection_disclosure",
        "serial_execution",
        "split",
        "task_validity_predicate_id",
        "termination_grace_seconds",
        "timeout_seconds",
        "timestep_seconds",
        "trace_detail",
        "trace_schema",
        "warmup_attempts_per_binary_map",
    }
    _exact_keys(fidelity, required_fidelity, "followup_fidelity_design")
    expected_common = {
        "capacity_search": False,
        "counts": [192, 256],
        "environment": "fractal_high",
        "fixed_work_timesteps": 100,
        "maximum_peak_rss_mib": 49152.0,
        "minimum_free_disk_bytes": 21_474_836_480,
        "omp_threads": 1,
        "phase": "calibration",
        "seeds": list(range(1, 11)),
        "serial_execution": True,
        "task_validity_predicate_id": "fixed-horizon-safe-navigation-v1",
        "termination_grace_seconds": 0.5,
        "timeout_seconds": 300.0,
        "timestep_seconds": 0.1,
    }
    for key, expected in expected_common.items():
        _exact_json_value(
            fidelity.get(key), expected, f"fidelity design {key}"
        )
    expected_fidelity_only = {
        "mode": "fidelity",
        "split": "calibration",
        "trace_schema": "dancers-comparison-trace-v3",
        "trace_detail": "digest",
        "repetitions": 1,
        "warmup_attempts_per_binary_map": 0,
        "planned_method_attempt_slots": 40,
        "planned_pair_blocks": 20,
        "design_role": "outcome-informed-exploratory-fidelity",
        "plan_root": PLAN_ROOT.as_posix(),
        "result_root": RESULT_ROOT.as_posix(),
        "selection_disclosure": SELECTION_DISCLOSURE,
    }
    for key, expected in expected_fidelity_only.items():
        _exact_json_value(
            fidelity.get(key), expected, f"fidelity design {key}"
        )
    _sha256(fidelity.get("map_sha256"), "fidelity design map_sha256")

    if mode == "fidelity":
        return fidelity, str(fidelity["design_role"])

    timing = _mapping(
        amendment.get("timing_if_fidelity_passes"),
        "timing_if_fidelity_passes",
    )
    required_timing = {
        "capacity_search",
        "claim_eligibility",
        "counts",
        "fixed_work_timesteps",
        "maximum_peak_rss_mib",
        "minimum_free_disk_bytes",
        "mode",
        "observer_compiled",
        "omp_threads",
        "phase",
        "planned_method_attempt_slots",
        "planned_pair_blocks",
        "power_policy_id",
        "repetitions",
        "seeds",
        "serial_execution",
        "task_validity_predicate_id",
        "termination_grace_seconds",
        "timeout_seconds",
        "timestep_seconds",
        "warmups_per_binary_map",
    }
    _exact_keys(timing, required_timing, "timing_if_fidelity_passes")
    for key, expected in expected_common.items():
        if key == "environment":
            continue
        _exact_json_value(
            timing.get(key), expected, f"timing design {key}"
        )
    expected_timing_only = {
        "mode": "timing",
        "observer_compiled": False,
        "power_policy_id": EXPECTED_POWER_POLICY["policy_id"],
        "repetitions": 3,
        "warmups_per_binary_map": 1,
        "planned_method_attempt_slots": 120,
        "planned_pair_blocks": 60,
        "claim_eligibility": "exploratory-screening-only",
    }
    for key, expected in expected_timing_only.items():
        _exact_json_value(
            timing.get(key), expected, f"timing design {key}"
        )
    return timing, str(timing["claim_eligibility"])


def _validate_denominators(design: dict[str, Any]) -> None:
    counts = _list(design.get("counts"), "design.counts")
    seeds = _list(design.get("seeds"), "design.seeds")
    repetitions = _strict_int(
        design.get("repetitions"), "design.repetitions", minimum=1
    )
    pair_blocks = len(counts) * len(seeds) * repetitions
    attempt_slots = pair_blocks * 2
    planned_pairs = _strict_int(
        design.get("planned_pair_blocks"),
        "design.planned_pair_blocks",
        minimum=1,
    )
    planned_attempts = _strict_int(
        design.get("planned_method_attempt_slots"),
        "design.planned_method_attempt_slots",
        minimum=1,
    )
    if planned_pairs != pair_blocks:
        raise FreezeV3Error("planned pair-block denominator is inconsistent")
    if planned_attempts != attempt_slots:
        raise FreezeV3Error(
            "planned method-attempt denominator is inconsistent"
        )


def _verified_environment_dependencies(
    repository: Path,
    environment_artifact: dict[str, Any],
) -> tuple[
    dict[str, dict[str, Any]],
    _AuthenticatedMaterializer,
]:
    manifest_path = Path(str(environment_artifact["path"]))
    manifest, _ = _strict_json(manifest_path, "environment manifest")
    dependencies: dict[str, dict[str, Any]] = {}

    sidecar = _committed_artifact(
        repository,
        ENVIRONMENT_MANIFEST_SIDECAR_PATH,
        "environment manifest sidecar",
    )
    expected_sidecar = (
        f"{environment_artifact['sha256']}  manifest.json\n"
    ).encode("ascii")
    if Path(str(sidecar["path"])).read_bytes() != expected_sidecar:
        raise FreezeV3Error(
            "environment manifest sidecar is not the exact canonical binding"
        )
    dependencies["environment_manifest_sidecar"] = sidecar

    capacity_suite = _mapping(
        manifest.get("capacity_suite"), "environment capacity_suite"
    )
    materializer_record = _mapping(
        capacity_suite.get("prefix_materializer"),
        "environment capacity_suite.prefix_materializer",
    )
    _exact_keys(
        materializer_record,
        {"exclusive_create_no_overwrite", "path", "sha256"},
        "environment capacity_suite.prefix_materializer",
    )
    _exact_json_value(
        materializer_record.get("exclusive_create_no_overwrite"),
        True,
        "capacity materializer exclusive-create policy",
    )
    if materializer_record.get("path") != MATERIALIZER_PATH.as_posix():
        raise FreezeV3Error("capacity materializer path changed")
    materializer, materializer_source = _committed_artifact_snapshot(
        repository, MATERIALIZER_PATH, "capacity materializer"
    )
    if materializer["sha256"] != _sha256(
        materializer_record.get("sha256"), "capacity materializer SHA-256"
    ):
        raise FreezeV3Error("capacity materializer SHA-256 changed")
    dependencies["capacity_materializer"] = materializer

    generator_record = _mapping(
        manifest.get("generator"), "environment generator"
    )
    _exact_keys(
        generator_record,
        {"path", "sha256", "stdlib_only"},
        "environment generator",
    )
    _exact_json_value(
        generator_record.get("stdlib_only"),
        True,
        "environment generator stdlib_only",
    )
    if generator_record.get("path") != ENVIRONMENT_GENERATOR_PATH.as_posix():
        raise FreezeV3Error("environment generator path changed")
    generator, generator_source = _committed_artifact_snapshot(
        repository, ENVIRONMENT_GENERATOR_PATH, "environment generator"
    )
    if generator["sha256"] != _sha256(
        generator_record.get("sha256"), "environment generator SHA-256"
    ):
        raise FreezeV3Error("environment generator SHA-256 changed")
    dependencies["environment_generator"] = generator

    environments = _list(
        manifest.get("environments"), "environment manifest.environments"
    )
    matches = [
        item
        for item in environments
        if type(item) is dict
        and item.get("family") == "fractal_high"
        and item.get("split") == "calibration"
    ]
    if len(matches) != 1:
        raise FreezeV3Error(
            "environment manifest must have one fractal_high/calibration case"
        )
    capacity = _mapping(
        matches[0].get("capacity"), "fractal-high calibration capacity"
    )
    map_record = _mapping(
        matches[0].get("map"), "fractal-high calibration map"
    )
    _exact_keys(
        map_record,
        {"bytes", "path", "sha256"},
        "fractal-high calibration map",
    )
    expected_map_relative = FRACTAL_HIGH_CALIBRATION_MAP_PATH.relative_to(
        Path("comparison/environments")
    ).as_posix()
    if map_record.get("path") != expected_map_relative:
        raise FreezeV3Error("fractal-high calibration map path changed")
    map_artifact = _committed_artifact(
        repository,
        FRACTAL_HIGH_CALIBRATION_MAP_PATH,
        "fractal-high calibration map",
    )
    if (
        map_artifact["bytes"]
        != _strict_int(
            map_record.get("bytes"),
            "fractal-high calibration map bytes",
            minimum=1,
        )
        or map_artifact["sha256"]
        != _sha256(
            map_record.get("sha256"),
            "fractal-high calibration map SHA-256",
        )
    ):
        raise FreezeV3Error("fractal-high calibration map bytes changed")
    dependencies["fractal_high_calibration_map"] = map_artifact

    bank_record = _mapping(
        capacity.get("bank"), "fractal-high calibration capacity bank"
    )
    _exact_keys(
        bank_record,
        {"bytes", "path", "sha256"},
        "fractal-high calibration capacity bank",
    )
    expected_bank_relative = FRACTAL_HIGH_CALIBRATION_BANK_PATH.relative_to(
        Path("comparison/environments")
    ).as_posix()
    if bank_record.get("path") != expected_bank_relative:
        raise FreezeV3Error("fractal-high calibration bank path changed")
    bank = _committed_artifact(
        repository,
        FRACTAL_HIGH_CALIBRATION_BANK_PATH,
        "fractal-high calibration bank",
    )
    expected_bank_bytes = _strict_int(
        bank_record.get("bytes"),
        "fractal-high calibration bank bytes",
        minimum=1,
    )
    expected_bank_sha = _sha256(
        bank_record.get("sha256"),
        "fractal-high calibration bank SHA-256",
    )
    if (
        bank["bytes"] != expected_bank_bytes
        or bank["sha256"] != expected_bank_sha
    ):
        raise FreezeV3Error("fractal-high calibration bank bytes changed")
    dependencies["fractal_high_calibration_bank"] = bank
    return (
        dependencies,
        _AuthenticatedMaterializer(
            materializer_source=materializer_source,
            generator_source=generator_source,
        ),
    )


def _validate_environment(
    artifact: dict[str, Any],
    design: dict[str, Any],
    fidelity_design: dict[str, Any],
) -> tuple[Path, dict[str, Any], dict[int, dict[str, Any]]]:
    manifest_path = Path(str(artifact["path"]))
    manifest, _ = _strict_json(manifest_path, "environment manifest")
    environments = _list(
        manifest.get("environments"), "environment manifest.environments"
    )
    matches = [
        item
        for item in environments
        if type(item) is dict
        and item.get("family") == "fractal_high"
        and item.get("split") == "calibration"
    ]
    if len(matches) != 1:
        raise FreezeV3Error(
            "environment manifest must have one fractal_high/calibration case"
        )
    case = matches[0]
    map_record = _mapping(case.get("map"), "environment map")
    _exact_keys(map_record, {"path", "sha256", "bytes"}, "environment map")
    map_sha = _sha256(map_record["sha256"], "environment map SHA-256")
    if map_sha != fidelity_design.get("map_sha256"):
        raise FreezeV3Error("amendment map SHA-256 changed")
    environment_root = manifest_path.parent
    map_relative = _relative_path(map_record["path"], "environment map path")
    map_path = _inside_repository(
        environment_root, map_relative, "environment map"
    )
    if (
        sha256_file(map_path) != map_sha
        or map_path.stat().st_size
        != _strict_int(
            map_record["bytes"], "environment map bytes", minimum=1
        )
    ):
        raise FreezeV3Error("environment map bytes changed")

    capacity = _mapping(case.get("capacity"), "environment capacity")
    records = _list(
        capacity.get("prefix_scenarios"),
        "environment capacity prefix_scenarios",
    )
    by_count: dict[int, dict[str, Any]] = {}
    for raw in records:
        record = _mapping(raw, "capacity prefix")
        count = record.get("robot_count")
        if type(count) is not int:
            raise FreezeV3Error("capacity prefix robot_count is invalid")
        if count in by_count:
            raise FreezeV3Error("duplicate capacity prefix robot_count")
        by_count[count] = record
    amended = _mapping(
        fidelity_design.get("registered_scenario_prefixes"),
        "registered_scenario_prefixes",
    )
    _exact_keys(amended, {"192", "256"}, "registered_scenario_prefixes")
    selected: dict[int, dict[str, Any]] = {}
    for count in design["counts"]:
        record = by_count.get(count)
        if record is None:
            raise FreezeV3Error(f"N={count} is not environment-registered")
        registered = _mapping(
            amended.get(str(count)), f"amended N={count} scenario"
        )
        _exact_keys(
            registered, {"bytes", "sha256"}, f"amended N={count} scenario"
        )
        registered_bytes = _strict_int(
            registered.get("bytes"),
            f"amended N={count} scenario bytes",
            minimum=1,
        )
        registered_sha = _sha256(
            registered.get("sha256"),
            f"amended N={count} scenario SHA-256",
        )
        if (
            record.get("bytes") != registered_bytes
            or record.get("sha256") != registered_sha
        ):
            raise FreezeV3Error(
                f"N={count} scenario registration is inconsistent"
            )
        _strict_int(
            record.get("bytes"), f"N={count} scenario bytes", minimum=1
        )
        _sha256(record.get("sha256"), f"N={count} scenario SHA-256")
        selected[count] = {
            "bytes": record["bytes"],
            "sha256": record["sha256"],
        }
    return map_path, map_record, selected


def _materialize_authenticated_scenario(
    authenticated: _AuthenticatedMaterializer,
    environment_root: Path,
    family: str,
    split: str,
    robot_count: int,
) -> bytes:
    materializer_sha = sha256_bytes(authenticated.materializer_source)
    generator_sha = sha256_bytes(authenticated.generator_source)
    payload = canonical_json_bytes(
        {
            "environment_root": str(environment_root),
            "family": _strict_str(family, "materializer family"),
            "generator_sha256": generator_sha,
            "generator_source_base64": base64.b64encode(
                authenticated.generator_source
            ).decode("ascii"),
            "materializer_sha256": materializer_sha,
            "materializer_source_base64": base64.b64encode(
                authenticated.materializer_source
            ).decode("ascii"),
            "robot_count": _strict_int(
                robot_count, "materializer robot_count", minimum=1
            ),
            "split": _strict_str(split, "materializer split"),
        }
    )
    command = [
        sys.executable,
        "-I",
        "-S",
        "-B",
        "-c",
        AUTHENTICATED_MATERIALIZER_BOOTSTRAP,
    ]
    try:
        completed = subprocess.run(
            command,
            input=payload,
            cwd=Path(tempfile.gettempdir()).resolve(),
            env={
                "LANG": "C",
                "LC_ALL": "C",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            check=False,
            capture_output=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired as error:
        raise FreezeV3Error(
            f"authenticated materializer timed out for N={robot_count}"
        ) from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).decode(
            "utf-8", errors="replace"
        ).strip()
        raise FreezeV3Error(
            f"authenticated materializer failed for N={robot_count}: "
            f"{detail}"
        )
    try:
        value = json.loads(
            completed.stdout.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateKey,
        ValueError,
    ) as error:
        raise FreezeV3Error(
            "authenticated materializer emitted invalid JSON"
        ) from error
    result = _mapping(value, "authenticated materializer result")
    _exact_keys(
        result,
        {"bytes", "data_base64", "sha256"},
        "authenticated materializer result",
    )
    if completed.stdout != canonical_json_bytes(result):
        raise FreezeV3Error(
            "authenticated materializer emitted noncanonical JSON"
        )
    expected_bytes = _strict_int(
        result.get("bytes"),
        "authenticated materializer result.bytes",
        minimum=1,
    )
    reported_sha = _sha256(
        result.get("sha256"),
        "authenticated materializer result.sha256",
    )
    encoded = _strict_str(
        result.get("data_base64"),
        "authenticated materializer result.data_base64",
    )
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise FreezeV3Error(
            "authenticated materializer emitted invalid base64"
        ) from error
    observed_sha = sha256_bytes(data)
    if len(data) != expected_bytes or observed_sha != reported_sha:
        raise FreezeV3Error(
            "authenticated materializer result self-check failed"
        )
    return data


def _verified_methods(
    repository: Path,
    mode: str,
    pixi_lock: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    manifest_artifact = _committed_artifact(
        repository, BUILD_MANIFEST_PATH, "build manifest"
    )
    manifest, _ = _strict_json(
        Path(str(manifest_artifact["path"])), "build manifest"
    )
    if manifest.get("schema_version") != BUILD_MANIFEST_SCHEMA:
        raise FreezeV3Error("build manifest schema changed")
    repository_record = _mapping(
        manifest.get("repository"), "build manifest.repository"
    )
    builds = _mapping(manifest.get("builds"), "build manifest.builds")
    gates = _mapping(manifest.get("gates"), "build manifest.gates")
    toolchain = _mapping(
        manifest.get("toolchain"), "build manifest.toolchain"
    )
    if (
        gates.get("dual_clean_builds") != "pass"
        or gates.get("common_registered_tests_all_four_builds") != "pass"
        or gates.get("smoke_semantic_trace_identity") != "pass"
        or gates.get("calibration_permitted") is not True
    ):
        raise FreezeV3Error("build manifest does not permit calibration")
    if (
        mode == "timing"
        and gates.get("timing_observer_compile_absence") != "pass"
    ):
        raise FreezeV3Error("timing observer-absence gate has not passed")
    if toolchain.get("pixi_lock_sha256") != pixi_lock["sha256"]:
        raise FreezeV3Error("build manifest pixi.lock hash changed")
    _exact_json_value(
        repository_record.get("remote_count"),
        0,
        "build manifest repository.remote_count",
    )
    _exact_json_value(
        repository_record.get("origin_present"),
        False,
        "build manifest repository.origin_present",
    )

    pins = {
        "reference": REFERENCE_PIN,
        "optimized": OPTIMIZED_PIN,
    }
    methods: dict[str, dict[str, Any]] = {}
    for method, expected_pin in pins.items():
        base_revision = _git_object(
            repository_record.get(f"{method}_pin"),
            f"{method} base revision",
        )
        if base_revision != expected_pin:
            raise FreezeV3Error(f"{method} base revision changed")
        head = _git_object(
            repository_record.get(f"{method}_applied_head"),
            f"{method} applied HEAD",
        )
        tree = _git_object(
            repository_record.get(f"{method}_tree"),
            f"{method} tree",
        )
        key = f"{method}_{mode}"
        build = _mapping(builds.get(key), f"builds.{key}")
        if (
            build.get("algorithm_revision") != base_revision
            or build.get("applied_head") != head
            or build.get("observer_compiled") is not (mode == "fidelity")
            or build.get("registered_tests") != "3/3 passed"
        ):
            raise FreezeV3Error(f"{key} build identity changed")

        source_relative = _relative_path(
            build.get("source_dir"), f"{key}.source_dir"
        )
        source = _inside_repository(
            repository,
            source_relative,
            f"{key} source_dir",
            directory=True,
        )
        source_top = Path(
            _git(source, "rev-parse", "--show-toplevel").stdout.strip()
        ).resolve()
        if source_top != source:
            raise FreezeV3Error(
                f"{key} source_dir is not its Git worktree toplevel"
            )
        if (
            _git(source, "rev-parse", "--is-inside-work-tree")
            .stdout.strip()
            != "true"
        ):
            raise FreezeV3Error(f"{key} source_dir is not a Git worktree")
        common_text = _git(
            source, "rev-parse", "--git-common-dir"
        ).stdout.strip()
        common_path = Path(common_text)
        if not common_path.is_absolute():
            common_path = source / common_path
        if common_path.resolve() != (repository / ".git").resolve():
            raise FreezeV3Error(
                f"{key} source worktree does not share the local repository"
            )
        source_remotes = [
            item
            for item in _git(source, "remote").stdout.splitlines()
            if item.strip()
        ]
        if source_remotes:
            raise FreezeV3Error(
                f"{key} source worktree must not have Git remotes"
            )
        actual_head = _git(source, "rev-parse", "HEAD").stdout.strip()
        actual_tree = _git(source, "rev-parse", "HEAD^{tree}").stdout.strip()
        if actual_head != head or actual_tree != tree:
            raise FreezeV3Error(f"{key} source identity changed")
        if _git(
            source,
            "status",
            "--porcelain",
            "--untracked-files=all",
        ).stdout:
            raise FreezeV3Error(f"{key} source worktree is mutable")

        binary_relative = _relative_path(
            build.get("binary_path"), f"{key}.binary_path"
        )
        binary = _inside_repository(
            repository, binary_relative, f"{key} binary"
        )
        binary_sha = _sha256(
            build.get("binary_sha256"), f"{key}.binary_sha256"
        )
        if sha256_file(binary) != binary_sha:
            raise FreezeV3Error(f"{key} binary bytes changed")
        if not os.access(binary, os.X_OK):
            raise FreezeV3Error(f"{key} binary is not executable")
        methods[method] = {
            "source_dir": str(source),
            "base_revision": base_revision,
            "head_revision": head,
            "tree_hash": tree,
            "binary": {
                "path": str(binary),
                "sha256": binary_sha,
            },
        }
    return methods, manifest_artifact


def _git_head_bytes(repository: Path, relative: Path) -> bytes:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "show",
            f"HEAD:{relative.as_posix()}",
        ],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).decode(
            "utf-8", errors="replace"
        ).strip()
        raise FreezeV3Error(
            f"cannot read committed bytes for {relative}: {detail}"
        )
    return completed.stdout


def _tracked_files_under(
    repository: Path,
    relative_root: Path,
) -> set[Path]:
    completed = subprocess.run(
        [
            "git",
            "-C",
            str(repository),
            "ls-files",
            "-z",
            "--",
            relative_root.as_posix(),
        ],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).decode(
            "utf-8", errors="replace"
        ).strip()
        raise FreezeV3Error(
            f"cannot enumerate committed fidelity session: {detail}"
        )
    return {
        Path(item.decode("utf-8"))
        for item in completed.stdout.split(b"\0")
        if item
    }


def _path_is_tracked(repository: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(repository)
    except ValueError:
        return False
    completed = _git(
        repository,
        "ls-files",
        "--error-unmatch",
        "--",
        relative.as_posix(),
        check=False,
    )
    return completed.returncode == 0


def _observed_session_files(session_root: Path) -> set[Path]:
    observed: set[Path] = set()
    for current_text, directory_names, file_names in os.walk(
        session_root,
        topdown=True,
        followlinks=False,
    ):
        current = Path(current_text)
        for name in directory_names:
            path = current / name
            metadata = os.stat(path, follow_symlinks=False)
            if not stat.S_ISDIR(metadata.st_mode):
                raise FreezeV3Error(
                    f"fidelity session contains a non-directory or link: "
                    f"{path}"
                )
        for name in file_names:
            path = current / name
            metadata = os.stat(path, follow_symlinks=False)
            if not stat.S_ISREG(metadata.st_mode):
                raise FreezeV3Error(
                    f"fidelity session contains a non-regular file or link: "
                    f"{path}"
                )
            observed.add(path)
    return observed


def _snapshot_physical_path(
    snapshot_root: Path,
    logical_path: Path,
) -> Path:
    if not logical_path.is_absolute() or ".." in logical_path.parts:
        raise FreezeV3Error(
            f"snapshot path must be absolute and canonical: {logical_path}"
        )
    return snapshot_root.joinpath(*logical_path.parts[1:])


def _write_snapshot_file(
    snapshot_root: Path,
    logical_path: Path,
    data: bytes,
) -> None:
    physical = _snapshot_physical_path(snapshot_root, logical_path)
    physical.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(physical, flags, 0o400)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.fchmod(descriptor, 0o400)
    except BaseException:
        physical.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)


def _json_values_from_snapshot_file(
    path: Path,
    data: bytes,
) -> list[Any]:
    if path.suffix == ".json":
        try:
            return [
                json.loads(
                    data.decode("utf-8"),
                    object_pairs_hook=_reject_duplicate_keys,
                    parse_constant=_reject_constant,
                )
            ]
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            _DuplicateKey,
            ValueError,
        ):
            return []
    if path.suffix == ".jsonl":
        values: list[Any] = []
        for line in data.splitlines():
            if not line.strip():
                continue
            try:
                values.append(
                    json.loads(
                        line.decode("utf-8"),
                        object_pairs_hook=_reject_duplicate_keys,
                        parse_constant=_reject_constant,
                    )
                )
            except (
                UnicodeDecodeError,
                json.JSONDecodeError,
                _DuplicateKey,
                ValueError,
            ):
                continue
        return values
    return []


def _sha_bound_paths(value: Any) -> list[tuple[Path, str]]:
    found: list[tuple[Path, str]] = []
    if type(value) is dict:
        raw_path = value.get("path")
        raw_sha = value.get("sha256")
        if (
            type(raw_path) is str
            and Path(raw_path).is_absolute()
            and type(raw_sha) is str
            and SHA256_RE.fullmatch(raw_sha) is not None
        ):
            found.append((Path(raw_path), raw_sha))
        for child in value.values():
            found.extend(_sha_bound_paths(child))
    elif type(value) is list:
        for child in value:
            found.extend(_sha_bound_paths(child))
    return found


def _add_snapshot_file(
    *,
    repository: Path,
    snapshot_root: Path,
    files: dict[Path, bytes],
    logical_path: Path,
    expected_sha256: str,
    label: str,
    require_head_match: bool,
) -> None:
    try:
        relative = logical_path.relative_to(repository)
    except ValueError as error:
        raise FreezeV3Error(
            f"{label} escapes the registered repository"
        ) from error
    data, _ = _stable_file_snapshot(logical_path, label)
    observed_sha = sha256_bytes(data)
    if observed_sha != expected_sha256:
        raise FreezeV3Error(
            f"{label} SHA-256 changed: {observed_sha} != {expected_sha256}"
        )
    if require_head_match:
        _assert_committed_clean(repository, logical_path, label)
        if _git_head_bytes(repository, relative) != data:
            raise FreezeV3Error(
                f"{label} differs from its committed HEAD bytes"
            )
    prior = files.get(logical_path)
    if prior is not None:
        if prior != data:
            raise FreezeV3Error(
                f"{label} has contradictory authenticated snapshots"
            )
        return
    _write_snapshot_file(snapshot_root, logical_path, data)
    files[logical_path] = data


def _build_authenticated_fidelity_snapshot(
    *,
    repository: Path,
    session_root: Path,
    snapshot_root: Path,
    authenticated_analyzer: _AuthenticatedAnalyzer,
) -> None:
    head_before = _git(repository, "rev-parse", "HEAD").stdout.strip()
    relative_session = session_root.relative_to(repository)
    tracked = _tracked_files_under(repository, relative_session)
    observed_paths = _observed_session_files(session_root)
    observed = {
        path.relative_to(repository) for path in observed_paths
    }
    if observed != tracked:
        raise FreezeV3Error(
            "fidelity session tree must contain exactly its committed files; "
            f"missing={sorted(str(item) for item in tracked - observed)}, "
            f"extra={sorted(str(item) for item in observed - tracked)}"
        )

    files: dict[Path, bytes] = {}
    for relative in sorted(tracked, key=lambda item: item.as_posix()):
        path = repository / relative
        artifact, data = _committed_artifact_snapshot(
            repository, relative, f"fidelity snapshot {relative}"
        )
        if _git_head_bytes(repository, relative) != data:
            raise FreezeV3Error(
                f"fidelity snapshot {relative} differs from HEAD"
            )
        _write_snapshot_file(snapshot_root, path, data)
        files[path] = data
        if artifact["sha256"] != sha256_bytes(data):
            raise FreezeV3Error(
                f"fidelity snapshot {relative} authentication failed"
            )

    frozen_plan_path = session_root / "frozen_plan.json"
    frozen_plan_data = files.get(frozen_plan_path)
    if frozen_plan_data is None:
        raise FreezeV3Error(
            "authenticated fidelity snapshot lacks frozen_plan.json"
        )
    try:
        frozen_plan = json.loads(
            frozen_plan_data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateKey,
        ValueError,
    ) as error:
        raise FreezeV3Error(
            "authenticated fidelity frozen plan is invalid JSON"
        ) from error
    plan = _mapping(frozen_plan, "authenticated fidelity frozen plan")
    methods = _mapping(
        plan.get("methods"), "authenticated fidelity methods"
    )
    for method_name, raw_method in methods.items():
        method = _mapping(
            raw_method,
            f"authenticated fidelity method {method_name}",
        )
        binary = _mapping(
            method.get("binary"),
            f"authenticated fidelity method {method_name}.binary",
        )
        _strict_str(
            binary.get("path"),
            f"authenticated fidelity method {method_name}.binary.path",
        )

    discovered: dict[Path, str] = {}
    for data_path, data in list(files.items()):
        for value in _json_values_from_snapshot_file(data_path, data):
            for path, expected_sha in _sha_bound_paths(value):
                prior = discovered.setdefault(path, expected_sha)
                if prior != expected_sha:
                    raise FreezeV3Error(
                        f"snapshot records contradict the hash of {path}"
                    )
    for path, expected_sha in sorted(
        discovered.items(), key=lambda item: str(item[0])
    ):
        if path in files:
            if sha256_bytes(files[path]) != expected_sha:
                raise FreezeV3Error(
                    f"session record hash disagrees for {path}"
                )
            continue
        _add_snapshot_file(
            repository=repository,
            snapshot_root=snapshot_root,
            files=files,
            logical_path=path,
            expected_sha256=expected_sha,
            label=f"fidelity snapshot dependency {path}",
            require_head_match=_path_is_tracked(repository, path),
        )

    source_plan = (
        repository
        / PLAN_ROOT
        / FIDELITY_OUTPUT_NAME
        / "plan.json"
    )
    _add_snapshot_file(
        repository=repository,
        snapshot_root=snapshot_root,
        files=files,
        logical_path=source_plan,
        expected_sha256=sha256_bytes(frozen_plan_data),
        label="fidelity source plan",
        require_head_match=True,
    )
    if files[source_plan] != frozen_plan_data:
        raise FreezeV3Error(
            "fidelity source plan differs from frozen_plan.json"
        )

    for method_name, raw_method in methods.items():
        method = _mapping(
            raw_method,
            f"authenticated fidelity method {method_name}",
        )
        source_dir = Path(
            _strict_str(
                method.get("source_dir"),
                f"authenticated fidelity method {method_name}.source_dir",
            )
        )
        try:
            source_dir.relative_to(repository)
        except ValueError as error:
            raise FreezeV3Error(
                f"authenticated source directory escapes repository: "
                f"{source_dir}"
            ) from error
        _snapshot_physical_path(snapshot_root, source_dir).mkdir(
            parents=True, exist_ok=True
        )

    analyzer_path = repository / ANALYZER_PATH
    analyzer_v2_path = repository / ANALYZER_V2_BASE_PATH
    analyzer_v1_path = repository / ANALYZER_V1_BASE_PATH
    runner_v1_path = repository / RUNNER_V1_BASE_PATH
    trace_path = repository / TRACE_COMPARE_PATH
    capacity_path = repository / CAPACITY_DECISION_PATH
    for logical_path, source, label in (
        (
            analyzer_path,
            authenticated_analyzer.analyzer_source,
            "authenticated v3 analyzer",
        ),
        (
            analyzer_v2_path,
            authenticated_analyzer.analyzer_v2_source,
            "authenticated v2 analyzer",
        ),
        (
            analyzer_v1_path,
            authenticated_analyzer.analyzer_v1_source,
            "authenticated v1 analyzer",
        ),
        (
            runner_v1_path,
            authenticated_analyzer.runner_v1_source,
            "authenticated v1 runner",
        ),
        (
            trace_path,
            authenticated_analyzer.trace_source,
            "authenticated trace reader",
        ),
        (
            capacity_path,
            authenticated_analyzer.capacity_source,
            "authenticated capacity-decision dependency",
        ),
    ):
        if files.get(logical_path) != source:
            raise FreezeV3Error(
                f"{label} snapshot differs from the executed source"
            )

    head_after = _git(repository, "rev-parse", "HEAD").stdout.strip()
    if head_after != head_before:
        raise FreezeV3Error(
            "repository HEAD changed while snapshotting fidelity evidence"
        )


def _execute_authenticated_analyzer_snapshot(
    *,
    repository: Path,
    session_root: Path,
    snapshot_root: Path,
    authenticated_analyzer: _AuthenticatedAnalyzer,
) -> bytes:
    sources = {
        "analyzer": (
            ANALYZER_PATH,
            authenticated_analyzer.analyzer_source,
        ),
        "analyzer_v2": (
            ANALYZER_V2_BASE_PATH,
            authenticated_analyzer.analyzer_v2_source,
        ),
        "analyzer_v1": (
            ANALYZER_V1_BASE_PATH,
            authenticated_analyzer.analyzer_v1_source,
        ),
        "runner_v1": (
            RUNNER_V1_BASE_PATH,
            authenticated_analyzer.runner_v1_source,
        ),
        "trace": (
            TRACE_COMPARE_PATH,
            authenticated_analyzer.trace_source,
        ),
        "capacity": (
            CAPACITY_DECISION_PATH,
            authenticated_analyzer.capacity_source,
        ),
    }
    payload = canonical_json_bytes(
        {
            "logical_cwd": str(repository),
            "session_root": str(session_root),
            "snapshot_root": str(snapshot_root),
            "sources": {
                name: {
                    "base64": base64.b64encode(source).decode("ascii"),
                    "logical_path": str(repository / relative),
                    "sha256": sha256_bytes(source),
                }
                for name, (relative, source) in sources.items()
            },
        }
    )
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-B",
                "-c",
                AUTHENTICATED_ANALYZER_BOOTSTRAP,
            ],
            input=payload,
            cwd=Path(tempfile.gettempdir()).resolve(),
            env={
                "LANG": "C",
                "LC_ALL": "C",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
            check=False,
            capture_output=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired as error:
        raise FreezeV3Error(
            "authenticated fidelity snapshot analysis timed out"
        ) from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).decode(
            "utf-8", errors="replace"
        ).strip()
        raise FreezeV3Error(
            "authenticated fidelity snapshot analysis failed: "
            f"{detail}"
        )
    return completed.stdout


def _regenerate_fidelity_receipt_from_snapshot(
    *,
    repository: Path,
    session_root: Path,
    authenticated_analyzer: _AuthenticatedAnalyzer,
) -> bytes:
    with tempfile.TemporaryDirectory(
        prefix="dancers-fidelity-snapshot-"
    ) as temporary:
        snapshot_root = Path(temporary).resolve()
        _build_authenticated_fidelity_snapshot(
            repository=repository,
            session_root=session_root,
            snapshot_root=snapshot_root,
            authenticated_analyzer=authenticated_analyzer,
        )
        return _execute_authenticated_analyzer_snapshot(
            repository=repository,
            session_root=session_root,
            snapshot_root=snapshot_root,
            authenticated_analyzer=authenticated_analyzer,
        )


def _expected_prior_failed_receipt(
    repository: Path,
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    return {
        "analysis_json_sha256": artifacts["prior_failed_analysis"][
            "sha256"
        ],
        "cohort_id": "fractal-high-lower-count-fidelity-amended-v1",
        "executed_method_attempt_slots": 0,
        "executed_pair_blocks": 0,
        "frozen_plan_json_sha256": artifacts[
            "prior_failed_frozen_plan"
        ]["sha256"],
        "gate_pass": False,
        "missing_method_attempt_slots": 40,
        "missing_pair_blocks": 20,
        "orchestration_abort_json_sha256": artifacts[
            "prior_failed_orchestration_abort"
        ]["sha256"],
        "pairs_jsonl_sha256": artifacts["prior_failed_pairs"]["sha256"],
        "path": str(repository / PRIOR_FAILED_RESULT_ROOT),
        "planned_method_attempt_slots": 40,
        "planned_pair_blocks": 20,
        "preflight_json_sha256": artifacts["prior_failed_preflight"][
            "sha256"
        ],
        "process_started": False,
        "run_manifest_json_sha256": artifacts[
            "prior_failed_run_manifest"
        ]["sha256"],
        "runs_csv_sha256": artifacts["prior_failed_runs_csv"]["sha256"],
        "runs_jsonl_sha256": artifacts["prior_failed_runs"]["sha256"],
        "session_json_sha256": artifacts["prior_failed_session"]["sha256"],
        "source_plan_json_sha256": artifacts["prior_failed_plan"]["sha256"],
        "valid_cooldown": True,
        "valid_pair_blocks": 0,
    }


def _verified_fidelity_receipt(
    repository: Path,
    receipt_path: Path | None,
    analyzer: dict[str, Any],
    authenticated_analyzer: _AuthenticatedAnalyzer,
    artifacts: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    if receipt_path is None:
        raise FreezeV3Error(
            "timing plan requires a passed --fidelity-gate-analysis receipt"
        )
    expected_receipt = _future_path_inside_repository(
        repository,
        RESULT_ROOT / FIDELITY_RECEIPT_NAME,
        "fidelity gate receipt",
    )
    supplied_receipt = Path(os.path.expanduser(str(receipt_path)))
    if (
        not supplied_receipt.is_absolute()
        or ".." in supplied_receipt.parts
    ):
        raise FreezeV3Error(
            "fidelity gate receipt must be an absolute canonical path "
            "without '..'"
        )
    if supplied_receipt != expected_receipt:
        raise FreezeV3Error(
            f"fidelity gate receipt must be {expected_receipt}"
        )
    artifact = _committed_artifact(
        repository,
        expected_receipt.relative_to(repository),
        "fidelity gate analysis",
    )
    receipt, receipt_data = _strict_json(
        expected_receipt, "fidelity gate analysis"
    )
    if receipt_data != canonical_json_bytes(receipt):
        raise FreezeV3Error(
            "fidelity gate analysis must be canonical JSON"
        )
    required = {
        "schema_version",
        "amendment_id",
        "execution_epoch_id",
        "cohort_id",
        "design_role",
        "analyzer_sha256",
        "input_sessions",
        "prior_failed_cohort",
        "planned_method_attempt_slots",
        "executed_attempt_count",
        "planned_pair_blocks",
        "completed_attempt_count",
        "valid_pair_count",
        "host_valid_pair_count",
        "frames_compared_per_pair",
        "gate_pass",
    }
    _exact_keys(receipt, required, "fidelity gate analysis")
    expected = {
        "schema_version": FIDELITY_GATE_SCHEMA,
        "amendment_id": AMENDMENT_ID,
        "execution_epoch_id": EXECUTION_EPOCH_ID,
        "cohort_id": FIDELITY_COHORT_ID,
        "design_role": "outcome-informed-exploratory-fidelity",
        "analyzer_sha256": analyzer["sha256"],
        "planned_method_attempt_slots": 40,
        "executed_attempt_count": 40,
        "planned_pair_blocks": 20,
        "completed_attempt_count": 40,
        "valid_pair_count": 20,
        "host_valid_pair_count": 20,
        "frames_compared_per_pair": 200,
        "gate_pass": True,
    }
    for key, value in expected.items():
        try:
            _exact_json_value(
                receipt.get(key),
                value,
                f"fidelity gate analysis {key}",
            )
        except FreezeV3Error as error:
            raise FreezeV3Error(
                f"fidelity gate analysis {key} is not a complete pass"
            ) from error
    prior = _mapping(
        receipt.get("prior_failed_cohort"),
        "fidelity gate analysis.prior_failed_cohort",
    )
    _exact_json_value(
        prior,
        _expected_prior_failed_receipt(repository, artifacts),
        "fidelity gate analysis prior failed cohort",
    )
    sessions = _list(
        receipt.get("input_sessions"),
        "fidelity gate analysis.input_sessions",
    )
    if len(sessions) != 1:
        raise FreezeV3Error(
            "fidelity gate analysis must bind exactly one immutable session; "
            "pooling or resumption is prohibited"
        )
    session = _mapping(
        sessions[0], "fidelity gate analysis.input_sessions[0]"
    )
    _exact_keys(
        session,
        {"path", *RECEIPT_LEDGER_FIELDS},
        "fidelity gate analysis.input_sessions[0]",
    )
    expected_session_root = _future_path_inside_repository(
        repository,
        RESULT_ROOT / FIDELITY_OUTPUT_NAME,
        "registered fidelity result root",
    )
    path_value = session["path"]
    if (
        type(path_value) is not str
        or not Path(path_value).is_absolute()
        or Path(path_value) != expected_session_root
    ):
        raise FreezeV3Error(
            "fidelity receipt session path is not the exact registered "
            f"result root {expected_session_root}"
        )
    session_root = _inside_repository(
        repository,
        expected_session_root.relative_to(repository),
        "fidelity receipt session",
        directory=True,
    )

    evidence: dict[str, dict[str, Any]] = {
        "fidelity_gate_analysis": artifact
    }
    for field, (filename, artifact_name) in RECEIPT_LEDGER_FIELDS.items():
        expected_sha = _sha256(session[field], f"receipt {field}")
        ledger = _committed_artifact_at_path(
            repository,
            session_root / filename,
            f"fidelity receipt {filename}",
        )
        if ledger["sha256"] != expected_sha:
            raise FreezeV3Error(
                f"fidelity receipt {filename} hash changed"
            )
        evidence[artifact_name] = ledger

    regenerated_data = _regenerate_fidelity_receipt_from_snapshot(
        repository=repository,
        session_root=session_root,
        authenticated_analyzer=authenticated_analyzer,
    )
    try:
        regenerated = json.loads(
            regenerated_data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        _DuplicateKey,
        ValueError,
    ) as error:
        raise FreezeV3Error(
            "authenticated analyzer emitted an invalid fidelity receipt"
        ) from error
    regenerated_receipt = _mapping(
        regenerated, "regenerated fidelity gate analysis"
    )
    if regenerated_data != canonical_json_bytes(regenerated_receipt):
        raise FreezeV3Error(
            "authenticated analyzer emitted a noncanonical receipt"
        )
    if regenerated_data != receipt_data:
        raise FreezeV3Error(
            "committed fidelity receipt is not byte-identical to fresh "
            "analysis of the exact authenticated snapshot"
        )
    return evidence


def _order_seed(
    *,
    mode: str,
    counts: list[int],
    seeds: list[int],
    artifacts: dict[str, dict[str, Any]],
    power_policy: dict[str, Any],
    process_policy: dict[str, Any],
) -> int:
    if mode == "fidelity":
        return FIDELITY_ORDER_SEED
    basis = canonical_json_bytes(
        {
            "schema_version": PLAN_SCHEMA_V3,
            "amendment_id": AMENDMENT_ID,
            "phase": "calibration",
            "mode": mode,
            "family": "fractal_high",
            "counts": counts,
            "seeds": seeds,
            "artifact_hashes": {
                key: value["sha256"]
                for key, value in sorted(artifacts.items())
            },
            "power_policy": power_policy,
            "process_control_policy": process_policy,
        }
    )
    value = int.from_bytes(hashlib.sha256(basis).digest()[:8], "big")
    return value % ((1 << 63) - 1) or 1


def _directory_flags() -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise FreezeV3Error(
            "descriptor-safe plan creation requires O_DIRECTORY and "
            "O_NOFOLLOW"
        )
    return (
        os.O_RDONLY
        | os.O_DIRECTORY
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )


def _open_directory_at(
    parent_fd: int,
    name: str,
    label: str,
) -> tuple[int, tuple[int, int]]:
    if not name or "/" in name or name in {".", ".."}:
        raise FreezeV3Error(f"{label} has an invalid path component")
    try:
        descriptor = os.open(
            name,
            _directory_flags(),
            dir_fd=parent_fd,
        )
    except OSError as error:
        raise FreezeV3Error(
            f"cannot open {label} without following links: {error}"
        ) from error
    try:
        opened = os.fstat(descriptor)
        current = os.stat(
            name,
            dir_fd=parent_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or _identity(opened) != _identity(current)
        ):
            raise FreezeV3Error(
                f"{label} changed at the descriptor-open boundary"
            )
        return descriptor, _identity(opened)
    except BaseException:
        os.close(descriptor)
        raise


def _entry_matches_handle(handle: _DirectoryHandle) -> bool:
    if handle.parent_fd is None:
        if handle.name is None:
            return False
        try:
            current = os.stat(handle.name, follow_symlinks=False)
            opened = os.fstat(handle.fd)
        except OSError:
            return False
        return (
            stat.S_ISDIR(current.st_mode)
            and stat.S_ISDIR(opened.st_mode)
            and _identity(current) == handle.identity
            and _identity(opened) == handle.identity
        )
    if handle.name is None:
        return False
    try:
        current = os.stat(
            handle.name,
            dir_fd=handle.parent_fd,
            follow_symlinks=False,
        )
        opened = os.fstat(handle.fd)
    except OSError:
        return False
    return (
        stat.S_ISDIR(current.st_mode)
        and stat.S_ISDIR(opened.st_mode)
        and _identity(current) == handle.identity
        and _identity(opened) == handle.identity
    )


def _safe_rmdir_handle(handle: _DirectoryHandle) -> None:
    if (
        not handle.created
        or handle.parent_fd is None
        or handle.name is None
    ):
        return
    try:
        os.fchmod(handle.fd, 0o755)
    except OSError:
        pass
    if not _entry_matches_handle(handle):
        return
    try:
        os.rmdir(handle.name, dir_fd=handle.parent_fd)
    except OSError:
        pass


def _close_directory_handles(handles: list[_DirectoryHandle]) -> None:
    for handle in reversed(handles):
        try:
            os.close(handle.fd)
        except OSError:
            pass


def _cleanup_directory_handles(handles: list[_DirectoryHandle]) -> None:
    for handle in reversed(handles):
        _safe_rmdir_handle(handle)


def _create_plan_directory_tree(
    repository: Path,
    relative_output: Path,
) -> list[_DirectoryHandle]:
    if relative_output.is_absolute() or ".." in relative_output.parts:
        raise FreezeV3Error(
            "registered plan output must be repository-relative"
        )
    flags = _directory_flags()
    root_fd = os.open(repository, flags)
    root_metadata = os.fstat(root_fd)
    repository_metadata = os.stat(repository, follow_symlinks=False)
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or _identity(root_metadata) != _identity(repository_metadata)
    ):
        os.close(root_fd)
        raise FreezeV3Error(
            "repository changed at the descriptor-open boundary"
        )
    handles = [
        _DirectoryHandle(
            fd=root_fd,
            parent_fd=None,
            name=str(repository),
            identity=_identity(root_metadata),
            created=False,
        )
    ]
    try:
        parts = relative_output.parts
        for index, part in enumerate(parts):
            parent = handles[-1]
            created = False
            final = index == len(parts) - 1
            if final:
                try:
                    os.mkdir(part, 0o755, dir_fd=parent.fd)
                    created = True
                except OSError as error:
                    if error.errno == errno.EEXIST:
                        raise FileExistsError(
                            "refusing to overwrite existing output: "
                            f"{repository / relative_output}"
                        ) from error
                    raise
            else:
                try:
                    child_fd, child_identity = _open_directory_at(
                        parent.fd,
                        part,
                        f"registered plan ancestor {part}",
                    )
                except FreezeV3Error as open_error:
                    cause = open_error.__cause__
                    if not (
                        isinstance(cause, OSError)
                        and cause.errno == errno.ENOENT
                    ):
                        raise
                    try:
                        os.mkdir(part, 0o755, dir_fd=parent.fd)
                        created = True
                    except OSError as mkdir_error:
                        if mkdir_error.errno != errno.EEXIST:
                            raise
                    child_fd, child_identity = _open_directory_at(
                        parent.fd,
                        part,
                        f"registered plan ancestor {part}",
                    )
                if created:
                    os.fchmod(child_fd, 0o755)
                handles.append(
                    _DirectoryHandle(
                        fd=child_fd,
                        parent_fd=parent.fd,
                        name=part,
                        identity=child_identity,
                        created=created,
                    )
                )
                continue

            child_fd, child_identity = _open_directory_at(
                parent.fd,
                part,
                "registered plan output",
            )
            os.fchmod(child_fd, 0o755)
            handles.append(
                _DirectoryHandle(
                    fd=child_fd,
                    parent_fd=parent.fd,
                    name=part,
                    identity=child_identity,
                    created=created,
                )
            )

        output = handles[-1]
        try:
            os.mkdir("scenarios", 0o755, dir_fd=output.fd)
        except OSError as error:
            if error.errno == errno.EEXIST:
                raise FreezeV3Error(
                    "new registered plan output unexpectedly contains "
                    "scenarios"
                ) from error
            raise
        scenario_fd, scenario_identity = _open_directory_at(
            output.fd,
            "scenarios",
            "registered scenario directory",
        )
        os.fchmod(scenario_fd, 0o755)
        handles.append(
            _DirectoryHandle(
                fd=scenario_fd,
                parent_fd=output.fd,
                name="scenarios",
                identity=scenario_identity,
                created=True,
            )
        )
        return handles
    except BaseException:
        _cleanup_directory_handles(handles)
        _close_directory_handles(handles)
        raise


def _entry_matches_identity(
    directory_fd: int,
    name: str,
    identity: tuple[int, int],
    *,
    regular: bool,
) -> bool:
    try:
        current = os.stat(
            name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
    except OSError:
        return False
    expected_kind = (
        stat.S_ISREG(current.st_mode)
        if regular
        else stat.S_ISDIR(current.st_mode)
    )
    return expected_kind and _identity(current) == identity


def _safe_unlink_identity_at(
    directory_fd: int,
    name: str,
    identity: tuple[int, int],
) -> None:
    if not _entry_matches_identity(
        directory_fd, name, identity, regular=True
    ):
        return
    try:
        os.unlink(name, dir_fd=directory_fd)
    except OSError:
        pass


def _exclusive_write_at(
    directory_fd: int,
    name: str,
    data: bytes,
) -> tuple[int, int]:
    if not name or "/" in name or name in {".", ".."}:
        raise FreezeV3Error("output filename is not one path component")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | os.O_NOFOLLOW
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(
            name,
            flags,
            0o444,
            dir_fd=directory_fd,
        )
    except OSError as error:
        if error.errno == errno.EEXIST:
            raise FileExistsError(
                f"refusing to overwrite existing output: {name}"
            ) from error
        raise
    metadata = os.fstat(descriptor)
    identity = _identity(metadata)
    try:
        if not stat.S_ISREG(metadata.st_mode):
            raise FreezeV3Error(
                f"exclusive output {name} is not a regular file"
            )
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.fchmod(descriptor, 0o444)
        after = os.fstat(descriptor)
        if (
            _identity(after) != identity
            or after.st_size != len(data)
            or not _entry_matches_identity(
                directory_fd, name, identity, regular=True
            )
        ):
            raise FreezeV3Error(
                f"exclusive output {name} changed while being written"
            )
        return identity
    except BaseException:
        _safe_unlink_identity_at(directory_fd, name, identity)
        raise
    finally:
        os.close(descriptor)


def _verify_published_output(
    handles: list[_DirectoryHandle],
    files: list[tuple[int, str, tuple[int, int]]],
) -> None:
    for handle in handles:
        if not _entry_matches_handle(handle):
            raise FreezeV3Error(
                "registered plan directory identity changed before "
                "publication"
            )
    for directory_fd, name, identity in files:
        if not _entry_matches_identity(
            directory_fd, name, identity, regular=True
        ):
            raise FreezeV3Error(
                f"registered plan file {name} changed before publication"
            )


def _expected_output(
    repository: Path,
    plan_root: Any,
    mode: str,
    output_root: Path,
) -> Path:
    relative_root = _relative_path(plan_root, "amended plan_root")
    if relative_root != PLAN_ROOT:
        raise FreezeV3Error(
            f"amended plan_root must be {PLAN_ROOT.as_posix()}"
        )
    expected_name = (
        FIDELITY_OUTPUT_NAME if mode == "fidelity" else TIMING_OUTPUT_NAME
    )
    expected = _future_path_inside_repository(
        repository,
        relative_root / expected_name,
        "registered plan output",
    )
    supplied = Path(os.path.expanduser(str(output_root)))
    if not supplied.is_absolute() or ".." in supplied.parts:
        raise FreezeV3Error(
            "output_root must be an absolute canonical path without '..'"
        )
    if supplied != expected:
        raise FreezeV3Error(
            f"output_root must be the registered path {expected}"
        )
    if supplied.exists() or supplied.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite existing output: {supplied}"
        )
    return supplied


def _expected_result_root(repository: Path, mode: str) -> tuple[Path, str]:
    output_name = (
        FIDELITY_OUTPUT_NAME if mode == "fidelity" else TIMING_OUTPUT_NAME
    )
    cohort_id = (
        FIDELITY_COHORT_ID if mode == "fidelity" else TIMING_COHORT_ID
    )
    result = _future_path_inside_repository(
        repository,
        RESULT_ROOT / output_name,
        "registered result root",
    )
    if result.exists() or result.is_symlink():
        raise FileExistsError(
            f"refusing to freeze a plan for existing result root: {result}"
        )
    if mode == "fidelity":
        for relative, label in (
            (
                RESULT_ROOT / f"{FIDELITY_OUTPUT_NAME}-analysis.json",
                "fidelity analysis",
            ),
            (
                RESULT_ROOT / FIDELITY_RECEIPT_NAME,
                "fidelity gate receipt",
            ),
        ):
            path = _future_path_inside_repository(
                repository,
                relative,
                f"registered {label}",
            )
            if path.exists() or path.is_symlink():
                raise FileExistsError(
                    f"refusing to freeze a plan for existing {label}: {path}"
                )
    return result, cohort_id


def freeze_plan_v3(
    *,
    repository_root: Path,
    mode: str,
    output_root: Path,
    runner_path: Path,
    analyzer_path: Path,
    fidelity_gate_analysis: Path | None = None,
) -> FrozenPlanV3:
    """Freeze one amendment-bound fidelity or conditional timing plan."""

    mode = _strict_str(mode, "mode")
    if mode not in {"fidelity", "timing"}:
        raise FreezeV3Error("mode must be fidelity or timing")
    repository = _verified_repository(repository_root)
    expected_runner = (repository / RUNNER_PATH).resolve()
    expected_analyzer = (repository / ANALYZER_PATH).resolve()
    if runner_path.expanduser().resolve() != expected_runner:
        raise FreezeV3Error(f"runner must be {expected_runner}")
    if analyzer_path.expanduser().resolve() != expected_analyzer:
        raise FreezeV3Error(f"analyzer must be {expected_analyzer}")

    amendment, artifacts = _validate_amendment(repository)
    _, rerun_artifacts, prior_plan = _validate_rerun_registry(repository)
    overlap = set(artifacts) & set(rerun_artifacts)
    if overlap:
        raise FreezeV3Error(
            f"rerun artifact namespaces overlap: {sorted(overlap)}"
        )
    artifacts.update(rerun_artifacts)
    artifacts["plan_freezer"] = _verified_plan_freezer(repository)
    fidelity_design = _mapping(
        amendment["followup_fidelity_design"],
        "followup_fidelity_design",
    )
    design, design_role = _expected_design(amendment, mode)
    _validate_denominators(design)

    runner = _committed_artifact(repository, RUNNER_PATH, "v3 runner")
    analyzer, analyzer_source = _committed_artifact_snapshot(
        repository, ANALYZER_PATH, "v3 analyzer"
    )
    artifacts["runner"] = runner
    artifacts["analysis_revision"] = analyzer
    runner_v1, runner_v1_source = _committed_artifact_snapshot(
        repository, RUNNER_V1_BASE_PATH, "historical v1 runner dependency"
    )
    artifacts["runner_v1_base"] = runner_v1
    analyzer_v1, analyzer_v1_source = _committed_artifact_snapshot(
        repository, ANALYZER_V1_BASE_PATH, "historical v1 analyzer dependency"
    )
    artifacts["analyzer_v1_base"] = analyzer_v1
    analyzer_v2, analyzer_v2_source = _committed_artifact_snapshot(
        repository, ANALYZER_V2_BASE_PATH, "sealed v2 analyzer dependency"
    )
    _exact_json_value(
        _without_bytes(analyzer_v2),
        _without_bytes(artifacts["analyzer_v2_base"]),
        "sealed v2 analyzer registry binding",
    )
    trace_compare, trace_source = _committed_artifact_snapshot(
        repository, TRACE_COMPARE_PATH, "trace comparison dependency"
    )
    artifacts["trace_compare"] = trace_compare
    (
        capacity_decision,
        capacity_source,
    ) = _committed_artifact_snapshot(
        repository,
        CAPACITY_DECISION_PATH,
        "capacity-decision dependency",
    )
    artifacts["freeze_capacity_decision"] = capacity_decision
    authenticated_analyzer = _AuthenticatedAnalyzer(
        analyzer_source=analyzer_source,
        analyzer_v2_source=analyzer_v2_source,
        analyzer_v1_source=analyzer_v1_source,
        runner_v1_source=runner_v1_source,
        trace_source=trace_source,
        capacity_source=capacity_source,
    )
    artifacts["pixi_lock"] = _committed_artifact(
        repository, PIXI_LOCK_PATH, "pixi.lock"
    )
    artifacts["config_template"] = _verified_config_template(repository)
    (
        environment_dependencies,
        authenticated_materializer,
    ) = _verified_environment_dependencies(
        repository, artifacts["environment_manifest"]
    )
    artifacts.update(environment_dependencies)
    methods, build_manifest = _verified_methods(
        repository, mode, artifacts["pixi_lock"]
    )
    artifacts["build_manifest"] = build_manifest
    _validate_common_v2_artifacts(artifacts, prior_plan)
    if mode == "timing":
        artifacts.update(
            _verified_fidelity_receipt(
                repository,
                fidelity_gate_analysis,
                analyzer,
                authenticated_analyzer,
                artifacts,
            )
        )
    elif fidelity_gate_analysis is not None:
        raise FreezeV3Error(
            "fidelity plans must not consume a prior fidelity gate receipt"
        )
    _validate_exact_artifact_contract(artifacts, mode)

    map_path, map_record, scenario_records = _validate_environment(
        artifacts["environment_manifest"],
        design,
        fidelity_design,
    )
    output = _expected_output(
        repository,
        fidelity_design.get("plan_root"),
        mode,
        output_root,
    )
    result_root, cohort_id = _expected_result_root(repository, mode)

    directory_handles: list[_DirectoryHandle] = []
    created_files: list[tuple[int, str, tuple[int, int]]] = []
    scenario_paths: list[Path] = []
    try:
        relative_output = output.relative_to(repository)
        _future_path_inside_repository(
            repository,
            relative_output,
            "registered plan output",
        )
        directory_handles = _create_plan_directory_tree(
            repository, relative_output
        )
        output_handle = directory_handles[-2]
        scenario_handle = directory_handles[-1]
        scenario_directory = output / "scenarios"
        materialized: dict[int, dict[str, Any]] = {}
        environment_root = Path(
            str(artifacts["environment_manifest"]["path"])
        ).parent
        for count in design["counts"]:
            scenario_name = f"n-{count}.json"
            scenario_path = scenario_directory / scenario_name
            scenario_data = _materialize_authenticated_scenario(
                authenticated_materializer,
                environment_root,
                "fractal_high",
                "calibration",
                count,
            )
            expected = scenario_records[count]
            if (
                len(scenario_data) != expected["bytes"]
                or sha256_bytes(scenario_data) != expected["sha256"]
            ):
                raise FreezeV3Error(
                    f"materialized N={count} scenario bytes changed"
                )
            scenario_identity = _exclusive_write_at(
                scenario_handle.fd,
                scenario_name,
                scenario_data,
            )
            created_files.append(
                (
                    scenario_handle.fd,
                    scenario_name,
                    scenario_identity,
                )
            )
            scenario_paths.append(scenario_path)
            materialized[count] = {
                "path": str(scenario_path),
                "sha256": expected["sha256"],
                "bytes": expected["bytes"],
            }

        pairs = [
            {
                "pair_id": (
                    f"fractal_high-calibration-seed-{seed}-n-{count}"
                ),
                "environment": "fractal_high",
                "seed": seed,
                "robot_count": count,
                "map": {
                    "path": str(map_path),
                    "sha256": map_record["sha256"],
                    "bytes": map_record["bytes"],
                },
                "scenario": materialized[count],
            }
            for count in design["counts"]
            for seed in design["seeds"]
        ]
        if len(pairs) * design["repetitions"] != design[
            "planned_pair_blocks"
        ]:
            raise FreezeV3Error("materialized pair denominator changed")

        order_seed = _order_seed(
            mode=mode,
            counts=list(design["counts"]),
            seeds=list(design["seeds"]),
            artifacts=artifacts,
            power_policy=EXPECTED_POWER_POLICY,
            process_policy=EXPECTED_PROCESS_CONTROL_POLICY,
        )
        plan: dict[str, Any] = {
            "schema_version": PLAN_SCHEMA_V3,
            "amendment_id": AMENDMENT_ID,
            "execution_epoch_id": EXECUTION_EPOCH_ID,
            "cohort_id": cohort_id,
            "design_role": design_role,
            "phase": "calibration",
            "mode": mode,
            "fixed_work_timesteps": design["fixed_work_timesteps"],
            "timestep_seconds": design["timestep_seconds"],
            "idle_cooldown_seconds": IDLE_COOLDOWN_SECONDS,
            "timeout_seconds": design["timeout_seconds"],
            "termination_grace_seconds": design[
                "termination_grace_seconds"
            ],
            "minimum_free_disk_bytes": design[
                "minimum_free_disk_bytes"
            ],
            "maximum_peak_rss_mib": design["maximum_peak_rss_mib"],
            "power_mode": POWER_MODE,
            "power_policy": EXPECTED_POWER_POLICY,
            "process_control_policy": EXPECTED_PROCESS_CONTROL_POLICY,
            "thermal_policy": THERMAL_POLICY,
            "task_validity_predicate_id": design[
                "task_validity_predicate_id"
            ],
            "repetitions": design["repetitions"],
            "warmups_per_binary_map": (
                design["warmup_attempts_per_binary_map"]
                if mode == "fidelity"
                else design["warmups_per_binary_map"]
            ),
            "omp_threads": design["omp_threads"],
            "serial_execution": design["serial_execution"],
            "order_seed": order_seed,
            "runner_sha256": runner["sha256"],
            "analyzer_sha256": analyzer["sha256"],
            "trace_detail": "digest",
            "run_cwd": str(repository),
            "result_root": str(result_root),
            "planned_method_attempt_slots": design[
                "planned_method_attempt_slots"
            ],
            "planned_pair_blocks": design["planned_pair_blocks"],
            "artifacts": {
                key: _without_bytes(value)
                for key, value in sorted(artifacts.items())
            },
            "methods": methods,
            "pairs": pairs,
        }
        if mode == "fidelity":
            _validate_fidelity_scientific_binding(plan, prior_plan)
        plan_data = canonical_json_bytes(plan)
        plan_path = output / "plan.json"
        plan_identity = _exclusive_write_at(
            output_handle.fd,
            "plan.json",
            plan_data,
        )
        created_files.append(
            (output_handle.fd, "plan.json", plan_identity)
        )
        os.fchmod(scenario_handle.fd, 0o555)
        os.fchmod(output_handle.fd, 0o555)
        for descriptor in (scenario_handle.fd, output_handle.fd):
            try:
                os.fsync(descriptor)
            except OSError as error:
                if error.errno not in {errno.EINVAL, errno.ENOTSUP}:
                    raise
        _verify_published_output(directory_handles, created_files)
        return FrozenPlanV3(
            plan_path=plan_path,
            plan_sha256=sha256_bytes(plan_data),
            scenario_paths=tuple(scenario_paths),
        )
    except BaseException:
        for handle in directory_handles:
            try:
                os.fchmod(handle.fd, 0o755)
            except OSError:
                pass
        for directory_fd, name, identity in reversed(created_files):
            _safe_unlink_identity_at(
                directory_fd, name, identity
            )
        _cleanup_directory_handles(directory_handles)
        raise
    finally:
        _close_directory_handles(directory_handles)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", required=True, choices=("fidelity", "timing"))
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--runner", required=True, type=Path)
    parser.add_argument("--analyzer", required=True, type=Path)
    parser.add_argument("--fidelity-gate-analysis", type=Path)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    args = parser.parse_args(argv)
    try:
        frozen = freeze_plan_v3(
            repository_root=args.repository_root,
            mode=args.mode,
            output_root=args.output_root,
            runner_path=args.runner,
            analyzer_path=args.analyzer,
            fidelity_gate_analysis=args.fidelity_gate_analysis,
        )
    except (
        FreezeV3Error,
        FileExistsError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        print(f"amended comparison plan freeze failed: {error}", file=sys.stderr)
        return 1
    print(f"{frozen.plan_sha256}  {frozen.plan_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
