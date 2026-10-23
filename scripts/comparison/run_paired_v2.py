#!/usr/bin/env python3
"""Versioned fail-closed runner for the amended DANCeRS comparison.

This module deliberately leaves ``run_paired.py`` byte-identical.  It reuses
the historical scheduling and evidence code only after verifying the frozen
v1 runner and its transitive Python dependencies as explicit v2 plan
artifacts.  V2 adds strict laptop-power evidence at every registered boundary
and a process-control state machine that cannot finalize evidence before the
direct child is reaped and its process group is empty.
"""

from __future__ import annotations

import argparse
import csv
import errno
import hashlib
import io
import json
import math
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, BinaryIO, Iterator, Sequence

try:
    from scripts.comparison import run_paired as _v1
except ModuleNotFoundError:  # Direct execution from scripts/comparison.
    import run_paired as _v1


PLAN_SCHEMA = "dancers-laptop-comparison-plan-v2"
RESULT_SCHEMA = "dancers-laptop-comparison-attempt-v2"
PAIR_SCHEMA = "dancers-laptop-comparison-pair-v2"
SESSION_SCHEMA = "dancers-laptop-comparison-session-v2"
PROCESS_CONTROL_SCHEMA = "dancers-process-control-v1"
ORCHESTRATION_ABORT_SCHEMA = "dancers-orchestration-abort-v2"
POWER_POLICY_ID = "darwin-ac-battery-stable-v1"
BASE_RUNNER_SHA256 = (
    "28c185189b756498c9a1030f80b092504d3e4a19f5c09fdfadc2860cb4a69fb2"
)
BASE_RUNNER_PATH = Path(_v1.__file__).resolve()
POST_OUTCOME_AMENDMENT_SHA256 = (
    "cb9dcd355648102cac35e06d0e2e6575f5ae6a60c9319690c2373efffc3ce7cb"
)
ALLOWED_POWER_STATUSES = {
    "charging",
    "charged",
    "finishing charge",
}
MINIMUM_BATTERY_PERCENT = 80
MAXIMUM_BATTERY_PERCENT = 100
MAXIMUM_COOLDOWN_DROP = 1
MAXIMUM_ATTEMPT_DROP = 1
MAXIMUM_SESSION_DROP = 2

RunnerError = _v1.RunnerError
Artifact = _v1.Artifact
Method = _v1.Method
Pair = _v1.Pair
CapacitySearch = _v1.CapacitySearch
CapacityEvaluation = _v1.CapacityEvaluation
canonical_json_bytes = _v1.canonical_json_bytes
sha256_file = _v1.sha256_file
utc_now = _v1.utc_now


class UncontainedProcessError(BaseException):
    """Fatal process-control failure that must bypass v1's Exception handler."""

    def __init__(
        self,
        message: str,
        process_control: dict[str, Any],
        *,
        status: int | None = None,
        usage: Any = None,
    ) -> None:
        super().__init__(message)
        self.process_control = process_control
        self.status = status
        self.usage = usage


class PowerStabilityAbort(BaseException):
    """Power-policy breach requiring an unsealed session abort."""


class UnsafeEvidencePurgeError(BaseException):
    """A partial zero-run tree could not be purged without path risk."""


@dataclass(frozen=True)
class Plan(_v1.Plan):
    amendment_id: str
    cohort_id: str
    result_root: Path
    design_role: str
    power_policy: dict[str, Any]
    process_control_policy: dict[str, Any]
    serial_execution: bool
    analyzer_sha256: str
    planned_method_attempt_slots: int
    planned_pair_blocks: int
    amendment: dict[str, Any]


@dataclass(frozen=True)
class ProcessMeasurement(_v1.ProcessMeasurement):
    process_control: dict[str, Any]


@dataclass
class _ExecutionState:
    plan: Plan
    output_path: Path
    preflight_snapshot: dict[str, Any] | None = None
    preflight_record: dict[str, Any] | None = None
    cooldown_record: dict[str, Any] | None = None
    cooldown_baseline_percent: int | None = None
    current_attempt_id: str | None = None
    current_attempt_before_percent: int | None = None
    process_controls: dict[str, dict[str, Any]] | None = None
    process_measurements: dict[str, ProcessMeasurement] | None = None
    process_started_attempts: set[str] | None = None
    pending_power_abort: str | None = None
    pending_boundary_abort: dict[str, Any] | None = None
    boundary_after_block: str | None = None
    pending_block_boundary_snapshot: dict[str, Any] | None = None
    block_boundary_before: dict[str, dict[str, Any]] | None = None
    block_boundary_after: dict[str, dict[str, Any]] | None = None
    host_abort: dict[str, Any] | None = None
    last_probe_error: str | None = None
    completed_runs: list[dict[str, Any]] | None = None
    completed_pairs: list[dict[str, Any]] | None = None
    output_identity: tuple[int, int] | None = None
    frozen_plan_bytes: bytes | None = None

    def __post_init__(self) -> None:
        if self.process_controls is None:
            self.process_controls = {}
        if self.process_measurements is None:
            self.process_measurements = {}
        if self.process_started_attempts is None:
            self.process_started_attempts = set()
        if self.completed_runs is None:
            self.completed_runs = []
        if self.completed_pairs is None:
            self.completed_pairs = []
        if self.block_boundary_before is None:
            self.block_boundary_before = {}
        if self.block_boundary_after is None:
            self.block_boundary_after = {}


_ACTIVE_STATE: _ExecutionState | None = None
_PROBE_STAGE = "block-boundary"
_PROBE_COMPARISON_PERCENT: int | None = None
_CONCRETE_PATH = type(Path())


def _descriptor_safe_create_result_root(plan: Plan, target: Path) -> None:
    """Create the registered root through no-follow directory descriptors."""

    expected = plan.run_cwd / _registered_result_relative(plan.mode)
    if Path(target) != expected:
        raise RunnerError("guarded mkdir target is not the registered result root")
    required_flags = ("O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(os, name) for name in required_flags):
        raise RunnerError("host lacks descriptor-safe no-follow directory flags")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptors: list[int] = []
    try:
        current_fd = os.open(plan.run_cwd, flags)
        descriptors.append(current_fd)
        repository_stat = os.fstat(current_fd)
        lexical_repository_stat = os.stat(
            plan.run_cwd, follow_symlinks=False
        )
        if (
            repository_stat.st_dev,
            repository_stat.st_ino,
        ) != (
            lexical_repository_stat.st_dev,
            lexical_repository_stat.st_ino,
        ):
            raise RunnerError("run_cwd identity changed before output mkdir")
        components = _registered_result_relative(plan.mode).parts
        for index, component in enumerate(components):
            final = index == len(components) - 1
            try:
                entry = os.stat(
                    component,
                    dir_fd=current_fd,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                os.mkdir(component, mode=0o777, dir_fd=current_fd)
                entry = os.stat(
                    component,
                    dir_fd=current_fd,
                    follow_symlinks=False,
                )
            else:
                if final:
                    raise FileExistsError(
                        f"output path already exists: {expected}"
                    )
            if not stat.S_ISDIR(entry.st_mode):
                raise RunnerError(
                    "registered output component is not a real directory: "
                    f"{component}"
                )
            next_fd = os.open(component, flags, dir_fd=current_fd)
            descriptors.append(next_fd)
            current_fd = next_fd
        final_stat = os.fstat(current_fd)
        lexical_final = os.stat(expected, follow_symlinks=False)
        if (final_stat.st_dev, final_stat.st_ino) != (
            lexical_final.st_dev,
            lexical_final.st_ino,
        ):
            raise RunnerError(
                "registered result_root identity changed during mkdir"
            )
        if _ACTIVE_STATE is not None:
            _ACTIVE_STATE.output_identity = (
                final_stat.st_dev,
                final_stat.st_ino,
            )
    except OSError as exc:
        raise RunnerError(
            f"descriptor-safe result_root creation failed: {exc}"
        ) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


class _GuardedPath(_CONCRETE_PATH):
    """Path used only inside v1 execution to guard its result-root mkdir."""

    def mkdir(
        self,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        state = _ACTIVE_STATE
        if state is not None and Path(self) == state.output_path:
            if mode != 0o777 or not parents or exist_ok:
                raise RunnerError("registered result_root mkdir flags changed")
            _descriptor_safe_create_result_root(state.plan, Path(self))
            return
        super().mkdir(mode=mode, parents=parents, exist_ok=exist_ok)


def _require_output_identity(state: _ExecutionState) -> None:
    if state.output_identity is None:
        return
    try:
        observed = state.output_path.lstat()
    except OSError as exc:
        raise RunnerError(
            f"result_root identity cannot be inspected: {exc}"
        ) from exc
    if (
        stat.S_ISLNK(observed.st_mode)
        or (observed.st_dev, observed.st_ino) != state.output_identity
    ):
        raise RunnerError(
            "result_root identity changed after exclusive creation"
        )


_ORIGINAL_PREFLIGHT = _v1.preflight
_ORIGINAL_EXECUTE = _v1.execute
_ORIGINAL_RUN_ATTEMPT = _v1._run_attempt
_ORIGINAL_PAIR_RESULT = _v1._pair_result
_ORIGINAL_HOST_SNAPSHOT = _v1._host_condition_snapshot
_ORIGINAL_FREEZE_TREE = _v1._freeze_tree
_ORIGINAL_TERMINAL_CATEGORY = _v1._terminal_category
_ORIGINAL_VALIDATE_EXPERIMENT_CONTROLS = _v1._validate_experiment_controls

_cooldown_sleep = time.sleep
_cooldown_clock = time.monotonic


def _strict_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    raw, data = _v1._load_json(path, label)
    return _v1._require_mapping(raw, label), data


def _artifact_from_record(
    record: Any, base: Path, label: str
) -> Artifact:
    return Artifact.parse(record, base, label)


def _verify_dependency_path(
    artifact: Artifact, expected: Path, label: str
) -> None:
    if artifact.path != expected.resolve():
        raise RunnerError(
            f"{label} path must be {expected.resolve()}, observed "
            f"{artifact.path}"
        )
    artifact.verify()


def _verify_base_runner() -> None:
    observed = sha256_file(BASE_RUNNER_PATH)
    if observed != BASE_RUNNER_SHA256:
        raise RunnerError(
            "historical v1 runner bytes changed; refusing the v2 wrapper"
        )


def _registered_result_relative(mode: str) -> Path:
    if mode == "fidelity":
        leaf = "fractal-high-lower-count-fidelity"
    elif mode == "timing":
        leaf = "fractal-high-lower-count-timing"
    else:
        raise RunnerError("v2 mode must be timing or fidelity")
    return (
        Path("results/comparison/calibration/trace-v3-amended-v1") / leaf
    )


def _validate_registered_result_root(
    run_cwd: Path,
    candidate: Path | str,
    mode: str,
    *,
    observed_parent: Path | None = None,
) -> Path:
    """Validate the registered output lexically without following it.

    Every existing component below the repository is checked with ``lstat``
    so even a dangling final symlink is rejected.  The resolved parent check
    is repeated immediately after cooldown, just before the historical
    runner creates the directory.
    """

    try:
        repository = run_cwd.resolve(strict=True)
    except OSError as exc:
        raise RunnerError(f"run_cwd cannot be resolved: {exc}") from exc
    if not repository.is_dir():
        raise RunnerError("run_cwd must be an existing directory")

    relative = _registered_result_relative(mode)
    expected = repository / relative
    try:
        candidate_text = os.fspath(candidate)
    except TypeError as exc:
        raise RunnerError("result_root must be an absolute path string") from exc
    if not isinstance(candidate_text, str):
        raise RunnerError("result_root must be an absolute path string")
    if not candidate_text or not Path(candidate_text).is_absolute():
        raise RunnerError("result_root must be absolute")
    if candidate_text != str(expected):
        raise RunnerError("result_root is not the registered lexical path")

    current = repository
    for index, component in enumerate(relative.parts):
        current = current / component
        try:
            mode_bits = current.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RunnerError(
                f"cannot inspect result_root component {current}: {exc}"
            ) from exc
        if stat.S_ISLNK(mode_bits):
            raise RunnerError(
                f"result_root component must not be a symbolic link: "
                f"{current}"
            )
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(mode_bits):
            raise RunnerError(
                f"result_root parent component is not a directory: "
                f"{current}"
            )

    try:
        resolved_parent = expected.parent.resolve(strict=False)
        resolved_parent.relative_to(repository)
    except (OSError, ValueError) as exc:
        raise RunnerError(
            "resolved result_root parent does not remain inside run_cwd"
        ) from exc
    if observed_parent is not None:
        try:
            observed = observed_parent.resolve(strict=False)
        except OSError as exc:
            raise RunnerError(
                f"cannot resolve the output parent selected for mkdir: {exc}"
            ) from exc
        if observed != resolved_parent:
            raise RunnerError(
                "output parent changed after result_root registration"
            )
    return expected


def _require_zero_git_remotes(plan: Plan) -> None:
    repositories = [
        ("run_cwd", plan.run_cwd),
        *[
            (f"methods.{method_id}.source_dir", plan.methods[method_id].source_dir)
            for method_id in ("reference", "optimized")
        ],
    ]
    for label, repository in repositories:
        remotes = [
            line
            for line in _v1._git(repository, "remote").splitlines()
            if line.strip()
        ]
        if remotes:
            raise RunnerError(
                f"{label} must have zero Git remotes at execution preflight"
            )


def _require_real_directory_path(path: Path, label: str) -> Path:
    if not path.is_absolute():
        raise RunnerError(f"{label} must be absolute")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        try:
            mode_bits = current.lstat().st_mode
        except OSError as exc:
            raise RunnerError(f"cannot inspect {label} component: {exc}") from exc
        if stat.S_ISLNK(mode_bits):
            raise RunnerError(
                f"{label} component must not be a symbolic link: {current}"
            )
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise RunnerError(f"{label} cannot be resolved: {exc}") from exc
    if resolved != path or not stat.S_ISDIR(path.stat().st_mode):
        raise RunnerError(f"{label} must remain an exact real directory")
    return resolved


def _post_cooldown_execution_integrity(
    plan: Plan,
    *,
    observed_output_parent: Path,
) -> None:
    """Repeat mutable execution checks immediately before output creation."""

    repository = _require_real_directory_path(plan.run_cwd, "run_cwd")
    top = Path(
        _v1._git(repository, "rev-parse", "--show-toplevel")
    ).resolve(strict=True)
    if top != repository:
        raise RunnerError("run_cwd is no longer the Git worktree root")
    _require_zero_git_remotes(plan)
    _validate_registered_result_root(
        repository,
        plan.result_root,
        plan.mode,
        observed_parent=observed_output_parent,
    )
    if sha256_file(plan.path) != plan.plan_sha256:
        raise RunnerError("plan bytes changed during cooldown")
    _verify_base_runner()
    for artifact in plan.artifacts.values():
        artifact.verify()
    for method_id in ("reference", "optimized"):
        method = plan.methods[method_id]
        _require_real_directory_path(
            method.source_dir,
            f"methods.{method_id}.source_dir",
        )
        _v1._verify_source(method, plan.artifacts["pixi_lock"])
    for pair in plan.pairs:
        pair.map_artifact.verify()
        pair.scenario.verify()
    # Keep the last mutable check as close as possible to mkdir.
    _require_zero_git_remotes(plan)


def _validate_policy_binding(
    record: dict[str, Any], amendment: dict[str, Any]
) -> None:
    power = record["power_policy"]
    process = record["process_control_policy"]
    if power != amendment.get("power_policy"):
        raise RunnerError(
            "plan power_policy is not byte-semantically equal to the amendment"
        )
    if process != amendment.get("process_control_policy"):
        raise RunnerError(
            "plan process_control_policy disagrees with the amendment"
        )
    required_power = {
        "policy_id": POWER_POLICY_ID,
        "minimum_battery_percent": MINIMUM_BATTERY_PERCENT,
        "maximum_battery_percent": MAXIMUM_BATTERY_PERCENT,
        "maximum_cooldown_drop_percentage_points": MAXIMUM_COOLDOWN_DROP,
        "maximum_attempt_boundary_drop_percentage_points": (
            MAXIMUM_ATTEMPT_DROP
        ),
        "maximum_session_drop_percentage_points": MAXIMUM_SESSION_DROP,
        "power_source": "AC Power",
        "present_required": True,
        "probe_during_timed_process": False,
        "session_drop_baseline": "cooldown-complete",
        "unknown_or_ambiguous_fails_closed": True,
    }
    for key, expected in required_power.items():
        if power.get(key) != expected:
            raise RunnerError(f"power_policy.{key} must equal {expected!r}")
    if set(power.get("allowed_statuses", [])) != ALLOWED_POWER_STATUSES:
        raise RunnerError("power_policy.allowed_statuses is not exact")
    expected_points = [
        "preflight",
        "cooldown-complete",
        "before-each-method-attempt",
        "after-each-method-attempt",
        "session-complete",
    ]
    if power.get("probe_points") != expected_points:
        raise RunnerError("power_policy.probe_points is not exact")
    required_process = {
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
    for key, expected in required_process.items():
        if process.get(key) != expected:
            raise RunnerError(
                f"process_control_policy.{key} must equal {expected!r}"
            )


def load_plan(path: Path | str) -> Plan:
    """Load an exact v2 plan while reusing the fully audited v1 parser."""

    _verify_base_runner()
    plan_path = Path(path).resolve()
    base = plan_path.parent
    record, original_bytes = _strict_json(plan_path, "comparison plan v2")
    v1_required = {
        "schema_version",
        "phase",
        "mode",
        "fixed_work_timesteps",
        "timestep_seconds",
        "idle_cooldown_seconds",
        "timeout_seconds",
        "repetitions",
        "warmups_per_binary_map",
        "omp_threads",
        "order_seed",
        "minimum_free_disk_bytes",
        "maximum_peak_rss_mib",
        "power_mode",
        "thermal_policy",
        "task_validity_predicate_id",
        "runner_sha256",
        "run_cwd",
        "artifacts",
        "methods",
        "pairs",
    }
    additions = {
        "amendment_id",
        "cohort_id",
        "result_root",
        "design_role",
        "power_policy",
        "process_control_policy",
        "serial_execution",
        "analyzer_sha256",
        "planned_method_attempt_slots",
        "planned_pair_blocks",
    }
    optional = {
        "termination_grace_seconds",
        "trace_detail",
        "capacity_search",
        "capacity_evaluation",
    }
    _v1._require_exact_keys(
        record,
        v1_required | additions,
        optional,
        "comparison plan v2",
    )
    if record["schema_version"] != PLAN_SCHEMA:
        raise RunnerError(
            f"unsupported v2 plan schema {record['schema_version']!r}"
        )
    if record["phase"] != "calibration":
        raise RunnerError("the amended v2 runner currently permits calibration only")
    if record["power_mode"] != "AC Power":
        raise RunnerError("v2 power_mode must remain exactly 'AC Power'")
    if record["serial_execution"] is not True:
        raise RunnerError("serial_execution must be true")
    expected_cohort = (
        "fractal-high-lower-count-fidelity-amended-v1"
        if record["mode"] == "fidelity"
        else "fractal-high-lower-count-timing-amended-v1"
    )
    if record["cohort_id"] != expected_cohort:
        raise RunnerError("cohort_id is not the registered amended cohort")
    run_cwd = _v1._resolve_path(record["run_cwd"], base, "run_cwd")
    if not isinstance(record["result_root"], str):
        raise RunnerError("result_root must be an absolute path string")
    expected_result_root = _validate_registered_result_root(
        run_cwd,
        record["result_root"],
        str(record["mode"]),
    )

    artifacts_raw = _v1._require_mapping(record["artifacts"], "artifacts")
    common_artifact_keys = {
        "pixi_lock",
        "build_manifest",
        "environment_manifest",
        "config_template",
        "protocol",
        "experiment_manifest",
        "analysis_revision",
        "runner",
        "runner_v1_base",
        "analyzer_v1_base",
        "plan_freezer",
        "trace_compare",
        "freeze_capacity_decision",
        "trigger_analysis",
        "trigger_frozen_plan",
        "trigger_session",
        "trigger_runs",
        "trigger_pairs",
        "environment_manifest_sidecar",
        "capacity_materializer",
        "environment_generator",
        "fractal_high_calibration_map",
        "fractal_high_calibration_bank",
        "amendment_protocol",
        "post_outcome_amendment",
    }
    optional_artifacts = {"capacity_decision"}
    if record["mode"] == "timing":
        common_artifact_keys.update(
            {
                "fidelity_gate_analysis",
                "fidelity_frozen_plan",
                "fidelity_run_manifest",
                "fidelity_preflight",
                "fidelity_session",
                "fidelity_runs",
                "fidelity_pairs",
            }
        )
    else:
        optional_artifacts.add("fidelity_gate_analysis")
    _v1._require_exact_keys(
        artifacts_raw,
        common_artifact_keys,
        optional_artifacts - common_artifact_keys,
        "artifacts",
    )
    if (
        record["mode"] == "fidelity"
        and "fidelity_gate_analysis" in artifacts_raw
    ):
        raise RunnerError(
            "fidelity plans must not contain fidelity_gate_analysis"
        )
    extra_artifacts = {
        key: _artifact_from_record(value, base, f"artifacts.{key}")
        for key, value in artifacts_raw.items()
        if key
        not in {
            "pixi_lock",
            "build_manifest",
            "environment_manifest",
            "config_template",
            "protocol",
            "experiment_manifest",
            "analysis_revision",
            "capacity_decision",
        }
    }
    expected_dependency_paths = {
        "runner": Path(__file__).resolve(),
        "runner_v1_base": Path(_v1.__file__).resolve(),
        "analyzer_v1_base": Path(__file__).with_name("analyze_results.py"),
        "plan_freezer": Path(__file__).with_name("freeze_plan_v2.py"),
        "trace_compare": Path(__file__).with_name("trace_compare.py"),
        "freeze_capacity_decision": Path(__file__).with_name(
            "freeze_capacity_decision.py"
        ),
        "trigger_analysis": (
            run_cwd
            / "results/comparison/calibration/trace-v3"
            / "fractal-high-full-grid-fidelity-analysis.json"
        ),
        "trigger_frozen_plan": (
            run_cwd
            / "results/comparison/calibration/trace-v3"
            / "fractal-high-full-grid-fidelity/frozen_plan.json"
        ),
        "trigger_session": (
            run_cwd
            / "results/comparison/calibration/trace-v3"
            / "fractal-high-full-grid-fidelity/session.json"
        ),
        "trigger_runs": (
            run_cwd
            / "results/comparison/calibration/trace-v3"
            / "fractal-high-full-grid-fidelity/runs.jsonl"
        ),
        "trigger_pairs": (
            run_cwd
            / "results/comparison/calibration/trace-v3"
            / "fractal-high-full-grid-fidelity/pairs.jsonl"
        ),
        "environment_manifest_sidecar": (
            run_cwd / "comparison/environments/manifest.sha256"
        ),
        "capacity_materializer": (
            run_cwd
            / "scripts/comparison/materialize_capacity_scenario.py"
        ),
        "environment_generator": (
            run_cwd / "scripts/comparison/generate_environments.py"
        ),
        "fractal_high_calibration_map": (
            run_cwd
            / "comparison/environments/fractal_high/calibration/map.png"
        ),
        "fractal_high_calibration_bank": (
            run_cwd
            / "comparison/environments/fractal_high/calibration"
            / "capacity/bank.json"
        ),
        "post_outcome_amendment": (
            run_cwd
            / "docs/comparison/fractal_high_lower_count_amendment_v1.json"
        ),
        "amendment_protocol": (
            run_cwd
            / "docs/comparison"
            / "EXPERIMENT_PROTOCOL_AMENDMENT_FH_LOWER_V1.md"
        ),
    }
    if record["mode"] == "timing":
        fidelity_root = (
            run_cwd
            / "results/comparison/calibration/trace-v3-amended-v1"
            / "fractal-high-lower-count-fidelity"
        )
        expected_dependency_paths.update(
            {
                "fidelity_gate_analysis": (
                    run_cwd
                    / "results/comparison/calibration"
                    / "trace-v3-amended-v1/fidelity-gate-receipt.json"
                ),
                "fidelity_frozen_plan": (
                    fidelity_root / "frozen_plan.json"
                ),
                "fidelity_run_manifest": (
                    fidelity_root / "run_manifest.json"
                ),
                "fidelity_preflight": fidelity_root / "preflight.json",
                "fidelity_session": fidelity_root / "session.json",
                "fidelity_runs": fidelity_root / "runs.jsonl",
                "fidelity_pairs": fidelity_root / "pairs.jsonl",
            }
        )
    for key, expected_path in expected_dependency_paths.items():
        _verify_dependency_path(extra_artifacts[key], expected_path, key)
    analysis_revision = _artifact_from_record(
        artifacts_raw["analysis_revision"],
        base,
        "artifacts.analysis_revision",
    )
    _verify_dependency_path(
        analysis_revision,
        Path(__file__).with_name("analyze_results_v2.py"),
        "analysis_revision",
    )
    registered_base_paths = {
        "pixi_lock": run_cwd / "pixi.lock",
        "build_manifest": run_cwd / "comparison/build_manifest.json",
        "environment_manifest": (
            run_cwd / "comparison/environments/manifest.json"
        ),
        "config_template": (
            run_cwd / "config/comparison/laptop_benchmark.yaml"
        ),
        "protocol": run_cwd / "docs/comparison/EXPERIMENT_PROTOCOL.md",
        "experiment_manifest": (
            run_cwd / "docs/comparison/experiment_manifest.json"
        ),
    }
    for key, expected_path in registered_base_paths.items():
        artifact = _artifact_from_record(
            artifacts_raw[key], base, f"artifacts.{key}"
        )
        _verify_dependency_path(artifact, expected_path, key)
    if extra_artifacts["runner_v1_base"].sha256 != BASE_RUNNER_SHA256:
        raise RunnerError("runner_v1_base is not the approved historical hash")
    if record["runner_sha256"] != extra_artifacts["runner"].sha256:
        raise RunnerError("runner_sha256 disagrees with artifacts.runner")
    if record["analyzer_sha256"] != artifacts_raw["analysis_revision"].get(
        "sha256"
    ):
        raise RunnerError(
            "analyzer_sha256 disagrees with artifacts.analysis_revision"
        )

    amendment_artifact = extra_artifacts["post_outcome_amendment"]
    if amendment_artifact.sha256 != POST_OUTCOME_AMENDMENT_SHA256:
        raise RunnerError("post_outcome_amendment is not the approved hash")
    amendment_artifact.verify()
    amendment, _ = _strict_json(
        amendment_artifact.path, "post-outcome amendment"
    )
    if record["amendment_id"] != amendment.get("amendment_id"):
        raise RunnerError("plan amendment_id disagrees with amendment")
    base_artifact_bindings = _v1._require_mapping(
        amendment.get("base_artifacts"),
        "post-outcome amendment.base_artifacts",
    )
    if set(base_artifact_bindings) != {
        "environment_manifest",
        "experiment_manifest",
        "protocol",
    }:
        raise RunnerError("amendment base_artifacts keys are not exact")
    for key, binding_value in base_artifact_bindings.items():
        binding = _v1._require_mapping(
            binding_value,
            f"post-outcome amendment.base_artifacts.{key}",
        )
        declared = _artifact_from_record(
            artifacts_raw[key], base, f"artifacts.{key}"
        )
        if (
            declared.path != (run_cwd / str(binding.get("path"))).resolve()
            or declared.sha256 != binding.get("sha256")
        ):
            raise RunnerError(f"amendment {key} artifact binding is invalid")
    trigger = _v1._require_mapping(
        amendment.get("trigger"), "post-outcome amendment.trigger"
    )
    trigger_analysis = _v1._require_mapping(
        trigger.get("analysis"),
        "post-outcome amendment.trigger.analysis",
    )
    if (
        extra_artifacts["trigger_analysis"].sha256
        != trigger_analysis.get("sha256")
    ):
        raise RunnerError("trigger_analysis amendment binding is invalid")
    trigger_hashes = _v1._require_mapping(
        trigger.get("hashes"), "post-outcome amendment.trigger.hashes"
    )
    trigger_hash_bindings = {
        "trigger_frozen_plan": "frozen_plan_json_sha256",
        "trigger_session": "session_json_sha256",
        "trigger_runs": "runs_jsonl_sha256",
        "trigger_pairs": "pairs_jsonl_sha256",
    }
    for artifact_name, hash_field in trigger_hash_bindings.items():
        if (
            extra_artifacts[artifact_name].sha256
            != trigger_hashes.get(hash_field)
        ):
            raise RunnerError(
                f"{artifact_name} amendment binding is invalid"
            )
    amendment_protocol = _v1._require_mapping(
        amendment.get("amendment_protocol"),
        "post-outcome amendment.amendment_protocol",
    )
    if (
        extra_artifacts["amendment_protocol"].path
        != Path(amendment_protocol["path"]).resolve()
        or extra_artifacts["amendment_protocol"].sha256
        != amendment_protocol["sha256"]
    ):
        raise RunnerError("amendment_protocol artifact binding is invalid")
    extra_artifacts["amendment_protocol"].verify()
    _validate_policy_binding(record, amendment)

    if record["mode"] == "fidelity":
        design = _v1._require_mapping(
            amendment.get("followup_fidelity_design"),
            "amendment.followup_fidelity_design",
        )
        expected_role = design.get("design_role")
        expected_warmups = design.get("warmup_attempts_per_binary_map")
    elif record["mode"] == "timing":
        design = _v1._require_mapping(
            amendment.get("timing_if_fidelity_passes"),
            "amendment.timing_if_fidelity_passes",
        )
        expected_role = design.get("claim_eligibility")
        expected_warmups = design.get("warmups_per_binary_map")
        extra_artifacts["fidelity_gate_analysis"].verify()
    else:
        raise RunnerError("v2 mode must be timing or fidelity")
    if record["design_role"] != expected_role:
        raise RunnerError("design_role disagrees with the amendment")
    if record["warmups_per_binary_map"] != expected_warmups:
        raise RunnerError("warmups_per_binary_map disagrees with amendment")
    for key in (
        "planned_method_attempt_slots",
        "planned_pair_blocks",
        "repetitions",
        "timeout_seconds",
        "termination_grace_seconds",
        "fixed_work_timesteps",
        "timestep_seconds",
        "minimum_free_disk_bytes",
        "maximum_peak_rss_mib",
        "omp_threads",
    ):
        if record.get(key) != design.get(key):
            raise RunnerError(f"plan {key} disagrees with amendment design")

    pairs_raw = record["pairs"]
    if not isinstance(pairs_raw, list) or not pairs_raw:
        raise RunnerError("pairs must be a non-empty array")
    expected_pairs = len(pairs_raw) * int(record["repetitions"])
    expected_attempts = expected_pairs * 2
    if record["planned_pair_blocks"] != expected_pairs:
        raise RunnerError("planned_pair_blocks does not match pair scheduling")
    if record["planned_method_attempt_slots"] != expected_attempts:
        raise RunnerError(
            "planned_method_attempt_slots does not match pair scheduling"
        )

    transformed = json.loads(json.dumps(record))
    transformed["schema_version"] = _v1.PLAN_SCHEMA
    for key in additions:
        transformed.pop(key)
    transformed_artifacts = transformed["artifacts"]
    allowed_v1_artifacts = {
        "pixi_lock",
        "build_manifest",
        "environment_manifest",
        "config_template",
        "protocol",
        "experiment_manifest",
        "analysis_revision",
        "capacity_decision",
    }
    transformed["artifacts"] = {
        key: value
        for key, value in transformed_artifacts.items()
        if key in allowed_v1_artifacts
    }
    original_pair_bytes: list[tuple[int, int]] = []
    for index, (raw_pair, transformed_pair) in enumerate(
        zip(pairs_raw, transformed["pairs"])
    ):
        raw_pair = _v1._require_mapping(raw_pair, f"pairs[{index}]")
        map_record = _v1._require_mapping(
            raw_pair.get("map"), f"pairs[{index}].map"
        )
        scenario_record = _v1._require_mapping(
            raw_pair.get("scenario"), f"pairs[{index}].scenario"
        )
        _v1._require_exact_keys(
            map_record, {"path", "sha256", "bytes"}, set(), f"pairs[{index}].map"
        )
        _v1._require_exact_keys(
            scenario_record,
            {"path", "sha256", "bytes"},
            set(),
            f"pairs[{index}].scenario",
        )
        map_path = _v1._resolve_path(
            map_record["path"], base, f"pairs[{index}].map.path"
        )
        scenario_path = _v1._resolve_path(
            scenario_record["path"], base, f"pairs[{index}].scenario.path"
        )
        observed_map_bytes = map_path.stat().st_size
        observed_scenario_bytes = scenario_path.stat().st_size
        if map_record["bytes"] != observed_map_bytes:
            raise RunnerError(f"pairs[{index}].map.bytes mismatch")
        if scenario_record["bytes"] != observed_scenario_bytes:
            raise RunnerError(f"pairs[{index}].scenario.bytes mismatch")
        original_pair_bytes.append(
            (observed_map_bytes, observed_scenario_bytes)
        )
        transformed_pair["map"].pop("bytes")
        transformed_pair["scenario"].pop("bytes")
    if record["mode"] == "fidelity":
        # V1's parser froze this unused field at one. V1 execution already
        # creates no fidelity warmups; restore the amended zero afterwards.
        transformed["warmups_per_binary_map"] = 1

    original_loader = _v1._load_json

    def transformed_loader(
        candidate: Path, label: str
    ) -> tuple[Any, bytes]:
        if Path(candidate).resolve() == plan_path:
            data = canonical_json_bytes(transformed)
            return transformed, data
        return original_loader(candidate, label)

    _v1._load_json = transformed_loader
    try:
        parsed = _v1.load_plan(plan_path)
    finally:
        _v1._load_json = original_loader

    all_artifacts = dict(parsed.artifacts)
    all_artifacts.update(extra_artifacts)
    for artifact in all_artifacts.values():
        artifact.verify()
    for method in parsed.methods.values():
        method.binary.verify()
    for pair in parsed.pairs:
        pair.map_artifact.verify()
        pair.scenario.verify()
    base_values = {
        field.name: getattr(parsed, field.name) for field in fields(_v1.Plan)
    }
    base_values.update(
        {
            "path": plan_path,
            "plan_sha256": hashlib.sha256(original_bytes).hexdigest(),
            "warmups_per_binary_map": int(record["warmups_per_binary_map"]),
            "artifacts": all_artifacts,
        }
    )
    return Plan(
        **base_values,
        amendment_id=str(record["amendment_id"]),
        cohort_id=str(record["cohort_id"]),
        result_root=expected_result_root,
        design_role=str(record["design_role"]),
        power_policy=dict(record["power_policy"]),
        process_control_policy=dict(record["process_control_policy"]),
        serial_execution=True,
        analyzer_sha256=str(record["analyzer_sha256"]),
        planned_method_attempt_slots=int(
            record["planned_method_attempt_slots"]
        ),
        planned_pair_blocks=int(record["planned_pair_blocks"]),
        amendment=amendment,
    )


_SOURCE_RE = re.compile(r"^Now drawing from '([^']+)'$")
_BATTERY_RE = re.compile(
    r"^[ \t]*-(?P<name>InternalBattery[A-Za-z0-9_-]*)[ \t]+"
    r"\(id=(?P<id>[0-9]+)\)[ \t]+"
    r"(?P<percent>[0-9]{1,3})%[ \t]*;[ \t]*"
    r"(?P<status>[^;\r\n]+?)[ \t]*; "
    r"(?P<trailing>[^;\r\n]+?)$"
)
_PRESENT_RE = re.compile(
    r"^(?:[0-9]+:[0-9]{2} remaining|\(no estimate\)) "
    r"present: (true|false)$"
)


def parse_darwin_battery_probe(
    *,
    returncode: int | None,
    stdout: str,
    stderr: str,
) -> dict[str, Any]:
    """Parse ``pmset -g batt`` with the amendment's anchored grammar."""

    record: dict[str, Any] = {
        "command": ["pmset", "-g", "batt"],
        "returncode": returncode,
        "stdout": _v1._bounded_probe_output(stdout),
        "stderr": _v1._bounded_probe_output(stderr),
        "source": None,
        "battery_record": None,
        "parse_error": None,
        "valid": False,
    }
    if returncode != 0:
        record["parse_error"] = "pmset-returncode-not-zero"
        return record
    normalized = stdout.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if len(lines) != 2 or any(line == "" for line in lines):
        record["parse_error"] = "expected-exactly-two-complete-lines"
        return record
    source_matches = [
        _SOURCE_RE.fullmatch(line) for line in lines
        if _SOURCE_RE.fullmatch(line) is not None
    ]
    battery_matches = [
        _BATTERY_RE.fullmatch(line) for line in lines
        if _BATTERY_RE.fullmatch(line) is not None
    ]
    if len(source_matches) != 1:
        record["parse_error"] = "expected-exactly-one-source-line"
        return record
    if len(battery_matches) != 1:
        record["parse_error"] = "expected-exactly-one-battery-line"
        return record
    source = source_matches[0].group(1)
    battery = battery_matches[0]
    raw_status = battery.group("status")
    if any(character.isspace() and character not in " \t" for character in raw_status):
        record["parse_error"] = "status-contains-non-ascii-whitespace"
        return record
    status = re.sub(r"[ \t]+", " ", raw_status.strip(" \t")).lower()
    trailing = battery.group("trailing")
    if trailing != trailing.strip(" \t"):
        record["parse_error"] = "trailing-field-has-surrounding-whitespace"
        return record
    if any(
        character.isspace() and character not in " \t"
        for character in trailing
    ):
        record["parse_error"] = "trailing-field-has-non-ascii-whitespace"
        return record
    present_match = _PRESENT_RE.fullmatch(trailing)
    if present_match is None:
        record["parse_error"] = "malformed-present-trailing-field"
        return record
    percent = int(battery.group("percent"))
    present = present_match.group(1) == "true"
    record["source"] = source
    record["battery_record"] = {
        "name": battery.group("name"),
        "id": int(battery.group("id")),
        "percent": percent,
        "status": status,
        "present": present,
    }
    violations: list[str] = []
    if source != "AC Power":
        violations.append("power-source-not-ac")
    if not (MINIMUM_BATTERY_PERCENT <= percent <= MAXIMUM_BATTERY_PERCENT):
        violations.append("battery-percent-outside-80-100")
    if status not in ALLOWED_POWER_STATUSES:
        violations.append("battery-status-not-allowed")
    if not present:
        violations.append("battery-not-present")
    if violations:
        record["parse_error"] = ",".join(violations)
        return record
    record["valid"] = True
    return record


def _battery_percent(snapshot: dict[str, Any] | None) -> int | None:
    if not snapshot:
        return None
    battery = snapshot.get("power_probe", {}).get("battery_record")
    if not isinstance(battery, dict):
        return None
    value = battery.get("percent")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _power_policy_evaluation(
    *,
    stage: str,
    current_percent: int | None,
    cooldown_baseline_percent: int | None,
    comparison_percent: int | None,
) -> dict[str, Any]:
    violations: list[str] = []
    baseline_drop = (
        None
        if current_percent is None or cooldown_baseline_percent is None
        else cooldown_baseline_percent - current_percent
    )
    boundary_drop = (
        None
        if current_percent is None or comparison_percent is None
        else comparison_percent - current_percent
    )
    if current_percent is None:
        violations.append("battery-percent-unavailable")
    elif not (
        MINIMUM_BATTERY_PERCENT
        <= current_percent
        <= MAXIMUM_BATTERY_PERCENT
    ):
        violations.append("battery-percent-outside-80-100")
    if stage == "cooldown-complete":
        if boundary_drop is None:
            violations.append("cooldown-comparison-unavailable")
        elif boundary_drop > MAXIMUM_COOLDOWN_DROP:
            violations.append("cooldown-drop-exceeds-1")
    if stage == "after-attempt":
        if boundary_drop is None:
            violations.append("attempt-comparison-unavailable")
        elif boundary_drop > MAXIMUM_ATTEMPT_DROP:
            violations.append("attempt-drop-exceeds-1")
    if stage in {
        "before-attempt",
        "after-attempt",
        "block-boundary",
        "session-complete",
    }:
        if baseline_drop is None:
            violations.append("cooldown-baseline-unavailable")
        elif baseline_drop > MAXIMUM_SESSION_DROP:
            violations.append("session-drop-exceeds-2")
    return {
        "stage": stage,
        "cooldown_baseline_percent": cooldown_baseline_percent,
        "attempt_boundary_before_percent": comparison_percent,
        "drop_from_cooldown_baseline_percentage_points": baseline_drop,
        "drop_from_attempt_before_percentage_points": boundary_drop,
        "valid": not violations,
        "violations": violations,
    }


def _process_event(
    *,
    stage: str,
    operation: str,
    signal_name: str | None,
    outcome: str,
    error: BaseException | str | None = None,
) -> dict[str, Any]:
    if isinstance(error, BaseException):
        detail = f"{type(error).__name__}: {error}"
    else:
        detail = error
    return {
        "stage": stage,
        "operation": operation,
        "signal": signal_name,
        "outcome": outcome,
        "error": detail,
    }


def _new_process_control() -> dict[str, Any]:
    return {
        "schema_version": PROCESS_CONTROL_SCHEMA,
        "process_started": True,
        "timed_out": None,
        "child_reaped": False,
        "process_group_empty_after_reap": None,
        "wait_interrupted_count": 0,
        "anomaly": False,
        "events": [],
        "valid": False,
    }


def _no_process_control(error: str | None) -> dict[str, Any]:
    return {
        "schema_version": PROCESS_CONTROL_SCHEMA,
        "process_started": False,
        "timed_out": None,
        "child_reaped": False,
        "process_group_empty_after_reap": None,
        "wait_interrupted_count": 0,
        "anomaly": True,
        "events": [],
        "valid": False,
    }


def _wait4_retry(
    pid: int,
    options: int,
    control: dict[str, Any],
    *,
    stage: str,
) -> tuple[int, int, Any]:
    while True:
        try:
            waited_pid, status, usage = os.wait4(pid, options)
        except InterruptedError as exc:
            control["wait_interrupted_count"] += 1
            control["events"].append(
                _process_event(
                    stage=stage,
                    operation="wait4",
                    signal_name=None,
                    outcome="interrupted",
                    error=exc,
                )
            )
            continue
        if waited_pid == pid:
            control["child_reaped"] = True
            control["events"].append(
                _process_event(
                    stage=stage,
                    operation="wait4",
                    signal_name=None,
                    outcome="reaped",
                )
            )
        elif waited_pid == 0:
            control["events"].append(
                _process_event(
                    stage=stage,
                    operation="wait4",
                    signal_name=None,
                    outcome="not-ready",
                )
            )
        else:
            control["anomaly"] = True
            control["events"].append(
                _process_event(
                    stage=stage,
                    operation="wait4",
                    signal_name=None,
                    outcome="error",
                    error=f"unexpected waited pid {waited_pid}",
                )
            )
            raise UncontainedProcessError(
                "wait4 returned an unexpected child identifier",
                control,
                status=status,
                usage=usage,
            )
        return waited_pid, status, usage


def _signal_group_or_child(
    pid: int,
    sig: signal.Signals,
    control: dict[str, Any],
    *,
    stage: str,
) -> None:
    signal_name = sig.name
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        control["events"].append(
            _process_event(
                stage=stage,
                operation="killpg",
                signal_name=signal_name,
                outcome="not-found",
            )
        )
        return
    except PermissionError as exc:
        control["anomaly"] = True
        control["events"].append(
            _process_event(
                stage=stage,
                operation="killpg",
                signal_name=signal_name,
                outcome="eperm",
                error=exc,
            )
        )
    except OSError as exc:
        control["anomaly"] = True
        control["events"].append(
            _process_event(
                stage=stage,
                operation="killpg",
                signal_name=signal_name,
                outcome="error",
                error=exc,
            )
        )
    else:
        control["events"].append(
            _process_event(
                stage=stage,
                operation="killpg",
                signal_name=signal_name,
                outcome="sent",
            )
        )
        return

    # Do not use Popen.terminate()/kill(): their implicit poll may reap with
    # waitpid and permanently discard the wait4 resource record.
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        control["events"].append(
            _process_event(
                stage=stage,
                operation="kill",
                signal_name=signal_name,
                outcome="not-found",
            )
        )
    except OSError as exc:
        control["anomaly"] = True
        control["events"].append(
            _process_event(
                stage=stage,
                operation="kill",
                signal_name=signal_name,
                outcome=(
                    "eperm"
                    if isinstance(exc, PermissionError)
                    or exc.errno == errno.EPERM
                    else "error"
                ),
                error=exc,
            )
        )
    else:
        control["events"].append(
            _process_event(
                stage=stage,
                operation="kill",
                signal_name=signal_name,
                outcome="sent",
            )
        )


def _confirm_process_group_empty(
    pid: int,
    control: dict[str, Any],
    *,
    status: int,
    usage: Any,
) -> None:
    while True:
        try:
            os.killpg(pid, 0)
        except InterruptedError as exc:
            control["events"].append(
                _process_event(
                    stage="group-empty-check",
                    operation="killpg",
                    signal_name=None,
                    outcome="interrupted",
                    error=exc,
                )
            )
            continue
        except ProcessLookupError:
            control["process_group_empty_after_reap"] = True
            control["events"].append(
                _process_event(
                    stage="group-empty-check",
                    operation="killpg",
                    signal_name=None,
                    outcome="empty",
                )
            )
            control["valid"] = bool(
                control["child_reaped"] and not control["anomaly"]
            )
            return
        except PermissionError as exc:
            control["anomaly"] = True
            control["process_group_empty_after_reap"] = False
            control["events"].append(
                _process_event(
                    stage="group-empty-check",
                    operation="killpg",
                    signal_name=None,
                    outcome="eperm",
                    error=exc,
                )
            )
            raise UncontainedProcessError(
                "cannot establish that the process group is empty after reap",
                control,
                status=status,
                usage=usage,
            ) from exc
        except OSError as exc:
            control["anomaly"] = True
            control["process_group_empty_after_reap"] = False
            control["events"].append(
                _process_event(
                    stage="group-empty-check",
                    operation="killpg",
                    signal_name=None,
                    outcome="error",
                    error=exc,
                )
            )
            raise UncontainedProcessError(
                "process-group containment check failed after reap",
                control,
                status=status,
                usage=usage,
            ) from exc
        else:
            control["anomaly"] = True
            control["process_group_empty_after_reap"] = False
            control["events"].append(
                _process_event(
                    stage="group-empty-check",
                    operation="killpg",
                    signal_name=None,
                    outcome="members-remain",
                )
            )
            raise UncontainedProcessError(
                "process group still contains a member after direct-child reap",
                control,
                status=status,
                usage=usage,
            )


def _finish_reap(
    pid: int,
    control: dict[str, Any],
    *,
    stage: str,
) -> tuple[int, Any]:
    waited_pid, status, usage = _wait4_retry(
        pid, os.WNOHANG, control, stage=stage
    )
    if waited_pid != pid:
        raise UncontainedProcessError(
            "direct child did not exit after final SIGKILL containment grace",
            control,
        )
    _confirm_process_group_empty(
        pid, control, status=status, usage=usage
    )
    return status, usage


def _wait_for_pid(
    pid: int, timeout: float, grace: float
) -> tuple[int, Any, bool, dict[str, Any]]:
    """Wait, terminate, reap, and prove containment before returning."""

    control = _new_process_control()
    deadline = time.monotonic() + timeout
    while True:
        waited_pid, status, usage = _wait4_retry(
            pid,
            os.WNOHANG,
            control,
            stage="wait-before-timeout",
        )
        if waited_pid == pid:
            control["timed_out"] = False
            _confirm_process_group_empty(
                pid, control, status=status, usage=usage
            )
            _validate_process_control_event_sequence(control)
            return status, usage, False, control
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.01, remaining))

    control["timed_out"] = True
    _signal_group_or_child(
        pid, signal.SIGTERM, control, stage="sigterm"
    )
    grace_deadline = time.monotonic() + grace
    while True:
        waited_pid, status, usage = _wait4_retry(
            pid,
            os.WNOHANG,
            control,
            stage="wait-after-sigterm",
        )
        if waited_pid == pid:
            _confirm_process_group_empty(
                pid, control, status=status, usage=usage
            )
            _validate_process_control_event_sequence(control)
            return status, usage, True, control
        remaining = grace_deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.01, remaining))

    _signal_group_or_child(
        pid, signal.SIGKILL, control, stage="sigkill"
    )
    kill_deadline = time.monotonic() + max(grace, 0.5)
    while True:
        waited_pid, status, usage = _wait4_retry(
            pid,
            os.WNOHANG,
            control,
            stage="wait-after-sigkill",
        )
        if waited_pid == pid:
            _confirm_process_group_empty(
                pid, control, status=status, usage=usage
            )
            _validate_process_control_event_sequence(control)
            return status, usage, True, control
        remaining = kill_deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(0.01, remaining))
    raise UncontainedProcessError(
        "direct child could not be reaped after timeout termination",
        control,
    )


def _validate_process_control_event_sequence(
    control: dict[str, Any],
) -> None:
    events = control["events"]
    if control["process_started"] is False:
        if events:
            raise RunnerError("a non-started process cannot have control events")
        return
    if not events:
        raise RunnerError("a started process must have control events")
    stage_order = {
        "wait-before-timeout": 0,
        "sigterm": 1,
        "wait-after-sigterm": 2,
        "sigkill": 3,
        "wait-after-sigkill": 4,
        "group-empty-check": 5,
    }
    prior_rank = -1
    reaped: list[dict[str, Any]] = []
    wait_interruptions = 0
    for index, event in enumerate(events):
        if event["outcome"] in {"interrupted", "eperm", "error"}:
            if not isinstance(event["error"], str) or not event["error"]:
                raise RunnerError("failed process-control event lacks an error")
        elif event["error"] is not None:
            raise RunnerError("successful process-control event has an error")
        stage = event["stage"]
        if stage not in stage_order:
            raise RunnerError(f"unknown process-control stage {stage!r}")
        rank = stage_order[stage]
        if rank < prior_rank:
            raise RunnerError("process-control events are out of order")
        prior_rank = rank
        operation = event["operation"]
        outcome = event["outcome"]
        if stage.startswith("wait-"):
            if (
                operation != "wait4"
                or event["signal"] is not None
                or outcome not in {"interrupted", "not-ready", "reaped"}
            ):
                raise RunnerError("illegal wait4 process-control event")
            if outcome == "interrupted":
                wait_interruptions += 1
            elif outcome == "reaped":
                reaped.append(event)
        elif stage in {"sigterm", "sigkill"}:
            if operation not in {"killpg", "kill"}:
                raise RunnerError("illegal signal process-control operation")
            expected_signal = "SIGTERM" if stage == "sigterm" else "SIGKILL"
            if event["signal"] != expected_signal:
                raise RunnerError("process-control signal disagrees with stage")
            if outcome not in {"sent", "not-found", "eperm", "error"}:
                raise RunnerError("illegal process-control signal outcome")
            if operation == "kill":
                if index == 0:
                    raise RunnerError("direct-child fallback lacks group event")
                prior = events[index - 1]
                if (
                    prior["stage"] != stage
                    or prior["operation"] != "killpg"
                    or prior["outcome"] not in {"eperm", "error"}
                ):
                    raise RunnerError(
                        "direct-child fallback is not justified by group failure"
                    )
        else:
            if (
                operation != "killpg"
                or event["signal"] is not None
                or outcome
                not in {
                    "interrupted",
                    "empty",
                    "eperm",
                    "error",
                    "members-remain",
                }
            ):
                raise RunnerError("illegal group-empty process-control event")
    if wait_interruptions != control["wait_interrupted_count"]:
        raise RunnerError("wait interruption count disagrees with events")
    if len(reaped) != (1 if control["child_reaped"] else 0):
        raise RunnerError("child-reap state disagrees with process events")
    if control["child_reaped"]:
        terminal_group = events[-1]
        expected_empty = control["process_group_empty_after_reap"] is True
        if (
            terminal_group["stage"] != "group-empty-check"
            or (terminal_group["outcome"] == "empty") != expected_empty
        ):
            raise RunnerError("group-empty state lacks a matching final event")
    if control["timed_out"] is False:
        if any(
            event["stage"]
            not in {"wait-before-timeout", "group-empty-check"}
            for event in events
        ):
            raise RunnerError("non-timeout process has timeout-control events")
        if reaped and reaped[0]["stage"] != "wait-before-timeout":
            raise RunnerError("non-timeout process reaped in an illegal stage")
    elif control["timed_out"] is True:
        if not any(event["stage"] == "sigterm" for event in events):
            raise RunnerError("timeout process lacks SIGTERM control evidence")
        if reaped and reaped[0]["stage"] == "wait-before-timeout":
            raise RunnerError("timeout process was reaped before timeout")


def run_measured_process(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    stdout: BinaryIO,
    stderr: BinaryIO,
    timeout: float,
    grace: float,
) -> ProcessMeasurement:
    started = time.monotonic()
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
        close_fds=True,
    )
    if _ACTIVE_STATE is not None and _ACTIVE_STATE.current_attempt_id:
        assert _ACTIVE_STATE.process_started_attempts is not None
        _ACTIVE_STATE.process_started_attempts.add(
            _ACTIVE_STATE.current_attempt_id
        )
    try:
        status, usage, timed_out, control = _wait_for_pid(
            process.pid, timeout, grace
        )
    except UncontainedProcessError as exc:
        if exc.status is not None:
            process.returncode = os.waitstatus_to_exitcode(exc.status)
        raise
    if _ACTIVE_STATE is not None and _ACTIVE_STATE.current_attempt_id:
        assert _ACTIVE_STATE.process_controls is not None
        _ACTIVE_STATE.process_controls[
            _ACTIVE_STATE.current_attempt_id
        ] = control
    wall = time.monotonic() - started
    returncode = os.waitstatus_to_exitcode(status)
    process.returncode = returncode
    unit, rss_mib = _v1._rss_to_mib(usage.ru_maxrss)
    measurement = ProcessMeasurement(
        returncode=returncode,
        timed_out=timed_out,
        wall_seconds=wall,
        user_cpu_seconds=float(usage.ru_utime),
        system_cpu_seconds=float(usage.ru_stime),
        peak_rss_raw=int(usage.ru_maxrss),
        peak_rss_raw_unit=unit,
        peak_rss_mib=rss_mib,
        process_control=control,
    )
    if _ACTIVE_STATE is not None and _ACTIVE_STATE.current_attempt_id:
        assert _ACTIVE_STATE.process_measurements is not None
        _ACTIVE_STATE.process_measurements[
            _ACTIVE_STATE.current_attempt_id
        ] = measurement
    return measurement


def _terminal_category(measurement: _v1.ProcessMeasurement) -> str:
    control = getattr(measurement, "process_control", None)
    if isinstance(control, dict) and control.get("anomaly") is True:
        return "measurement-wrapper-failure"
    return _ORIGINAL_TERMINAL_CATEGORY(measurement)


def _structured_invalid_host_snapshot(
    plan: Plan,
    filesystem_path: Path,
    error: str,
) -> dict[str, Any]:
    """Represent an exceptional boundary probe using the strict v2 shape."""

    evaluation = _power_policy_evaluation(
        stage=_PROBE_STAGE,
        current_percent=None,
        cooldown_baseline_percent=(
            _ACTIVE_STATE.cooldown_baseline_percent
            if _ACTIVE_STATE is not None
            else None
        ),
        comparison_percent=_PROBE_COMPARISON_PERCENT,
    )
    bounded_error = _v1._bounded_probe_output(error)
    cache = (
        Path(tempfile.gettempdir())
        / f"dancers-swift-module-cache-{plan.runner_sha256[:16]}"
    )
    return {
        "checked_utc": utc_now(),
        "filesystem_path": str(filesystem_path),
        "free_disk_bytes": 0,
        "minimum_free_disk_bytes": plan.minimum_free_disk_bytes,
        "disk_valid": 0 >= plan.minimum_free_disk_bytes,
        "power_mode": plan.power_mode,
        "power_probe": {
            "command": ["pmset", "-g", "batt"],
            "returncode": None,
            "stdout": "",
            "stderr": bounded_error,
            "source": None,
            "battery_record": None,
            "parse_error": "host-snapshot-exception",
            "valid": False,
        },
        "thermal_policy": plan.thermal_policy,
        "thermal_probe": {
            "command": [
                "swift",
                "-e",
                "import Foundation; print("
                "ProcessInfo.processInfo.thermalState.rawValue)",
            ],
            "module_cache_path": str(cache),
            "returncode": None,
            "stdout": "",
            "stderr": bounded_error,
            "state": None,
            "valid": False,
        },
        "power_policy_id": POWER_POLICY_ID,
        "power_policy_evaluation": evaluation,
        "valid": False,
    }


def _register_block_boundary_sample(
    state: _ExecutionState,
    snapshot: dict[str, Any],
    probe_error: str | None,
) -> None:
    if state.boundary_after_block is not None:
        assert state.block_boundary_after is not None
        state.block_boundary_after[state.boundary_after_block] = snapshot
        state.boundary_after_block = None
        return
    state.pending_block_boundary_snapshot = snapshot
    if snapshot["valid"] is True:
        return
    state.pending_boundary_abort = {
        "failed_snapshot": None if probe_error is not None else snapshot,
        "probe_error": probe_error,
    }


def _host_condition_snapshot(
    plan: Plan, filesystem_path: Path
) -> dict[str, Any]:
    """Return the v1 disk/thermal evidence with a strict power interpretation."""

    state = _ACTIVE_STATE
    if state is not None:
        state.last_probe_error = None
        _require_output_identity(state)
    try:
        snapshot = _ORIGINAL_HOST_SNAPSHOT(plan, filesystem_path)
        loose_power = snapshot["power_probe"]
        strict_power = parse_darwin_battery_probe(
            returncode=loose_power.get("returncode"),
            stdout=loose_power.get("stdout", ""),
            stderr=loose_power.get("stderr", ""),
        )
        baseline = (
            state.cooldown_baseline_percent if state is not None else None
        )
        current = (
            strict_power["battery_record"]["percent"]
            if strict_power.get("battery_record") is not None
            else None
        )
        evaluation = _power_policy_evaluation(
            stage=_PROBE_STAGE,
            current_percent=current,
            cooldown_baseline_percent=baseline,
            comparison_percent=_PROBE_COMPARISON_PERCENT,
        )
        snapshot["power_policy_id"] = POWER_POLICY_ID
        snapshot["power_probe"] = strict_power
        snapshot["power_policy_evaluation"] = evaluation
        snapshot["valid"] = bool(
            snapshot["disk_valid"]
            and snapshot["thermal_probe"]["valid"]
            and strict_power["valid"]
            and evaluation["valid"]
        )
        if state is not None and _PROBE_STAGE == "preflight":
            state.preflight_snapshot = snapshot
        if state is not None and _PROBE_STAGE == "block-boundary":
            _register_block_boundary_sample(state, snapshot, None)
        return snapshot
    except Exception as exc:
        can_seal = bool(
            state is not None
            and _PROBE_STAGE
            in {
                "block-boundary",
                "before-attempt",
                "after-attempt",
                "session-complete",
            }
            and state.output_path.is_dir()
            and not state.output_path.is_symlink()
        )
        if not can_seal:
            raise
        error = f"{type(exc).__name__}: {exc}"
        state.last_probe_error = error
        snapshot = _structured_invalid_host_snapshot(
            plan, filesystem_path, error
        )
        if _PROBE_STAGE == "block-boundary":
            _register_block_boundary_sample(state, snapshot, error)
        return snapshot


@contextmanager
def _probe_context(
    stage: str, comparison_percent: int | None = None
) -> Iterator[None]:
    global _PROBE_STAGE, _PROBE_COMPARISON_PERCENT
    old_stage = _PROBE_STAGE
    old_comparison = _PROBE_COMPARISON_PERCENT
    _PROBE_STAGE = stage
    _PROBE_COMPARISON_PERCENT = comparison_percent
    try:
        yield
    finally:
        _PROBE_STAGE = old_stage
        _PROBE_COMPARISON_PERCENT = old_comparison


def _enforce_idle_cooldown(
    plan: Plan,
    before: dict[str, Any],
    filesystem_path: Path,
) -> dict[str, Any]:
    started = _cooldown_clock()
    deadline = started + plan.idle_cooldown_seconds
    while True:
        remaining = deadline - _cooldown_clock()
        if remaining <= 0.0:
            break
        _cooldown_sleep(remaining)
    finished = _cooldown_clock()
    before_percent = _battery_percent(before)
    with _probe_context("cooldown-complete", before_percent):
        after = _host_condition_snapshot(plan, filesystem_path)
    elapsed = finished - started
    valid = bool(
        elapsed >= plan.idle_cooldown_seconds
        and _v1._host_block_valid(before, after)
    )
    record = {
        "required_seconds": plan.idle_cooldown_seconds,
        "eligible": bool(before["valid"]),
        "performed": True,
        "elapsed_seconds": elapsed,
        "before": before,
        "after": after,
        "valid": valid,
    }
    if _ACTIVE_STATE is not None:
        _ACTIVE_STATE.cooldown_record = record
        if valid:
            _ACTIVE_STATE.cooldown_baseline_percent = _battery_percent(after)
    if not valid:
        raise RunnerError(
            "idle cooldown violated darwin-ac-battery-stable-v1"
        )
    _post_cooldown_execution_integrity(
        plan,
        observed_output_parent=filesystem_path,
    )
    return record


def _validate_experiment_controls(
    plan: Plan, experiment: dict[str, Any]
) -> None:
    """Retain v1 invariants without reimposing the superseded count grid."""

    if experiment.get("schema_version") != "1.0":
        raise RunnerError("experiment manifest schema must be '1.0'")
    controls = _v1._require_mapping(
        experiment.get("control_variables"),
        "experiment manifest.control_variables",
    )
    expected_controls = {
        "fixed_work_timesteps": plan.fixed_work_timesteps,
        "runner_jobs": 1,
        "omp_threads": 1,
        "minimum_free_disk_bytes": plan.minimum_free_disk_bytes,
        "maximum_peak_rss_mib": plan.maximum_peak_rss_mib,
        "task_validity_predicate_id": plan.task_validity_predicate_id,
    }
    for field, expected in expected_controls.items():
        if controls.get(field) != expected:
            raise RunnerError(
                f"experiment control {field} disagrees with v2 plan"
            )
    robot = _v1._require_mapping(
        experiment.get("robot_configuration"),
        "experiment manifest.robot_configuration",
    )
    if (
        robot.get("fixed_work_timesteps") != plan.fixed_work_timesteps
        or robot.get("timestep_seconds") != plan.timestep_seconds
    ):
        raise RunnerError("experiment robot timing disagrees with v2 plan")
    if (
        plan.phase != "calibration"
        or plan.timeout_seconds != 300.0
        or plan.idle_cooldown_seconds != 300
        or plan.omp_threads != 1
    ):
        raise RunnerError("v2 amended calibration controls are not exact")
    if {pair.environment for pair in plan.pairs} != {"fractal_high"}:
        raise RunnerError("v2 amendment is restricted to fractal_high")
    expected_counts = set(
        plan.amendment[
            "followup_fidelity_design"
            if plan.mode == "fidelity"
            else "timing_if_fidelity_passes"
        ]["counts"]
    )
    if {pair.robot_count for pair in plan.pairs} != expected_counts:
        raise RunnerError("v2 plan counts disagree with amendment")
    expected_conditions = {
        (seed, count)
        for seed in _v1.PHASE_SEEDS["calibration"]
        for count in expected_counts
    }
    observed_conditions = [
        (pair.seed, pair.robot_count) for pair in plan.pairs
    ]
    if (
        len(observed_conditions) != len(expected_conditions)
        or set(observed_conditions) != expected_conditions
    ):
        raise RunnerError(
            "v2 plan must contain the complete amended seed/count product"
        )


def _freeze_tree(path: Path) -> None:
    state = _ACTIVE_STATE
    if state is not None:
        resolved = path.resolve()
        if resolved == state.output_path:
            return
        if resolved.parent == state.output_path / "attempts":
            return
    _ORIGINAL_FREEZE_TREE(path)


def _replace_json(path: Path, value: Any) -> None:
    temporary = path.with_name(f".{path.name}.v2-replacement")
    _v1._write_exclusive(temporary, canonical_json_bytes(value))
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _emergency_replace_json(path: Path, value: Any) -> None:
    """Independent atomic writer used only after the normal seal path fails."""

    temporary = path.with_name(f".{path.name}.emergency-replacement")
    if temporary.exists() or temporary.is_symlink():
        raise RunnerError(f"stale emergency replacement exists: {temporary}")
    _v1._write_exclusive(temporary, canonical_json_bytes(value))
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _emergency_replace_bytes(path: Path, data: bytes) -> None:
    temporary = path.with_name(f".{path.name}.emergency-replacement")
    if temporary.exists() or temporary.is_symlink():
        raise RunnerError(f"stale emergency replacement exists: {temporary}")
    _v1._write_exclusive(temporary, data)
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _emergency_freeze_tree(path: Path) -> None:
    for child in sorted(
        path.rglob("*"),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        if child.is_symlink():
            raise RunnerError(f"cannot freeze symbolic-link evidence: {child}")
        child.chmod(0o555 if child.is_dir() else 0o444)
    path.chmod(0o555)


def _control_proves_containment(control: dict[str, Any]) -> bool:
    if control["process_started"] is False:
        return True
    return bool(
        control["child_reaped"] is True
        and control["process_group_empty_after_reap"] is True
    )


def _post_containment_error_text(error: BaseException) -> str:
    return (
        "post-containment orchestration failure: "
        f"{type(error).__name__}: {error}"
    )


def _force_wrapper_failure(
    result: dict[str, Any],
    error: BaseException,
) -> None:
    detail = _post_containment_error_text(error)
    prior = result.get("orchestration_error")
    result["terminal_category"] = "measurement-wrapper-failure"
    result["task_valid"] = None
    result["fidelity_metrics"] = None
    result["orchestration_error"] = (
        detail if prior is None else f"{detail}; prior attempt error: {prior}"
    )


def _fallback_attempt_result(
    *,
    plan: Plan,
    pair: Pair,
    block_id: str,
    repetition: int,
    order: str,
    order_position: int,
    sequence: int,
    method: Method,
    config: Artifact,
    attempts_root: Path,
    attempt_kind: str,
    control: dict[str, Any],
    measurement: ProcessMeasurement | None,
    error: BaseException,
) -> dict[str, Any]:
    attempt_id = f"{sequence:06d}__{block_id}__{method.method_id}"
    run_dir = attempts_root / attempt_id
    existing = run_dir / "result.json"
    if existing.is_file() and not existing.is_symlink():
        try:
            loaded = json.loads(existing.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            loaded = None
        if isinstance(loaded, dict):
            _force_wrapper_failure(loaded, error)
            return loaded

    metadata_path = run_dir / "runner_metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    stdout_path = run_dir / "stdout.log"
    stderr_path = run_dir / "stderr.log"
    if not stdout_path.exists():
        _v1._write_exclusive(stdout_path, b"")
    if not stderr_path.exists():
        _v1._write_exclusive(
            stderr_path,
            (_post_containment_error_text(error) + "\n").encode("utf-8"),
        )
    if measurement is None:
        timed_out = (
            control["timed_out"]
            if isinstance(control["timed_out"], bool)
            else False
        )
        resource: dict[str, Any] = {
            "returncode": None,
            "exit_code": None,
            "signal": None,
            "timed_out": timed_out,
            "wall_seconds": None,
            "user_cpu_seconds": None,
            "system_cpu_seconds": None,
            "total_cpu_seconds": None,
            "peak_rss_raw": None,
            "peak_rss_raw_unit": None,
            "peak_rss_mib": None,
        }
    else:
        resource = {
            "returncode": measurement.returncode,
            "exit_code": (
                measurement.returncode
                if measurement.returncode >= 0
                else None
            ),
            "signal": (
                -measurement.returncode
                if measurement.returncode < 0
                else None
            ),
            "timed_out": measurement.timed_out,
            "wall_seconds": measurement.wall_seconds,
            "user_cpu_seconds": measurement.user_cpu_seconds,
            "system_cpu_seconds": measurement.system_cpu_seconds,
            "total_cpu_seconds": (
                measurement.user_cpu_seconds
                + measurement.system_cpu_seconds
            ),
            "peak_rss_raw": measurement.peak_rss_raw,
            "peak_rss_raw_unit": measurement.peak_rss_raw_unit,
            "peak_rss_mib": measurement.peak_rss_mib,
        }
    resource["maximum_peak_rss_mib"] = plan.maximum_peak_rss_mib
    resource["peak_rss_within_limit"] = bool(
        resource["peak_rss_mib"] is not None
        and resource["peak_rss_mib"] <= plan.maximum_peak_rss_mib
    )
    trace_path = run_dir / "trace.jsonl"
    summary_path = run_dir / "summary.json"

    def artifact_record(path: Path) -> dict[str, str] | None:
        if not path.is_file() or path.is_symlink():
            return None
        return {"path": str(path), "sha256": sha256_file(path)}

    return {
        "schema_version": RESULT_SCHEMA,
        "sequence": sequence,
        "attempt_id": attempt_id,
        "block_id": block_id,
        "pair_id": pair.pair_id,
        "repetition": repetition,
        "attempt_kind": attempt_kind,
        "order": order,
        "order_position": order_position,
        "phase": plan.phase,
        "mode": plan.mode,
        "environment": pair.environment,
        "seed": pair.seed,
        "robot_count": pair.robot_count,
        "method": method.method_id,
        "terminal_category": "measurement-wrapper-failure",
        "orchestration_error": _post_containment_error_text(error),
        "task_valid": None,
        "fidelity_metrics": None,
        "resource": resource,
        "provenance": metadata,
        "artifacts": {
            "config": {"path": str(config.path), "sha256": config.sha256},
            "stdout": artifact_record(stdout_path),
            "stderr": artifact_record(stderr_path),
            "trace": artifact_record(trace_path),
            "summary": artifact_record(summary_path),
        },
        "completed_utc": utc_now(),
    }


def _control_error_message(control: dict[str, Any]) -> str:
    anomaly_events = [
        event for event in control["events"]
        if event["outcome"] in {"eperm", "error", "members-remain"}
    ]
    details = "; ".join(
        (
            f"{event['stage']} {event['operation']} "
            f"{event['signal'] or ''} {event['outcome']}"
            + (f" ({event['error']})" if event["error"] else "")
        ).strip()
        for event in anomaly_events
    )
    return "process-control anomaly" + (f": {details}" if details else "")


def _decorate_attempt_result(
    result: dict[str, Any],
    *,
    before: dict[str, Any],
    after: dict[str, Any],
    control: dict[str, Any],
) -> dict[str, Any]:
    host_valid = bool(before["valid"] and after["valid"])
    result["schema_version"] = RESULT_SCHEMA
    result["host_conditions_before_attempt"] = before
    result["host_conditions_after_attempt"] = after
    result["host_attempt_valid"] = host_valid
    result["process_control"] = control
    if control["anomaly"]:
        result["terminal_category"] = "measurement-wrapper-failure"
        result["orchestration_error"] = _control_error_message(control)
    elif (
        result["terminal_category"] == "completed"
        and result.get("orchestration_error") is not None
    ):
        result["terminal_category"] = "measurement-wrapper-failure"
    if result["terminal_category"] == "measurement-wrapper-failure":
        result["task_valid"] = None
        result["fidelity_metrics"] = None
    return result


def _record_host_abort(
    state: _ExecutionState,
    *,
    stage: str,
    attempt_id: str,
    block_id: str,
    method: str,
    order_position: int,
    snapshot: dict[str, Any],
    reason: str,
) -> None:
    probe_error = state.last_probe_error
    state.host_abort = {
        "stage": stage,
        "attempt_id": attempt_id,
        "block_id": block_id,
        "method": method,
        "order_position": order_position,
        "failed_snapshot": None if probe_error is not None else snapshot,
        "probe_error": probe_error,
        "reason": reason,
    }
    state.pending_power_abort = reason


def _consume_boundary_abort(
    state: _ExecutionState,
    *,
    attempt_id: str,
    block_id: str,
    method: str,
    order_position: int,
) -> None:
    failure = state.pending_boundary_abort
    if failure is None:
        return
    reason = (
        f"{attempt_id}: block-boundary host-power stability failed "
        "before launch"
    )
    state.host_abort = {
        "stage": "block-boundary",
        "attempt_id": attempt_id,
        "block_id": block_id,
        "method": method,
        "order_position": order_position,
        "failed_snapshot": failure["failed_snapshot"],
        "probe_error": failure["probe_error"],
        "reason": reason,
    }
    state.pending_boundary_abort = None
    state.pending_power_abort = reason
    raise PowerStabilityAbort(reason)


def _run_attempt(
    plan: Plan,
    pair: Pair,
    block_id: str,
    repetition: int,
    order: str,
    order_position: int,
    sequence: int,
    method: Method,
    config: Artifact,
    attempts_root: Path,
    attempt_kind: str = "measured",
) -> dict[str, Any]:
    state = _ACTIVE_STATE
    if state is None:
        raise RunnerError("v2 attempt executed without session state")
    attempt_id = f"{sequence:06d}__{block_id}__{method.method_id}"
    if order_position == 1 and state.pending_block_boundary_snapshot is not None:
        assert state.block_boundary_before is not None
        state.block_boundary_before[block_id] = (
            state.pending_block_boundary_snapshot
        )
        state.pending_block_boundary_snapshot = None
    _consume_boundary_abort(
        state,
        attempt_id=attempt_id,
        block_id=block_id,
        method=method.method_id,
        order_position=order_position,
    )
    if state.pending_power_abort is not None:
        raise PowerStabilityAbort(state.pending_power_abort)
    with _probe_context("before-attempt"):
        before = _host_condition_snapshot(plan, state.output_path)
    if not before["valid"]:
        reason = (
            f"{attempt_id}: before-attempt host-power stability failed"
        )
        _record_host_abort(
            state,
            stage="before-attempt",
            attempt_id=attempt_id,
            block_id=block_id,
            method=method.method_id,
            order_position=order_position,
            snapshot=before,
            reason=reason,
        )
        raise PowerStabilityAbort(reason)

    state.current_attempt_id = attempt_id
    state.current_attempt_before_percent = _battery_percent(before)
    try:
        result = _ORIGINAL_RUN_ATTEMPT(
            plan,
            pair,
            block_id,
            repetition,
            order,
            order_position,
            sequence,
            method,
            config,
            attempts_root,
            attempt_kind=attempt_kind,
        )
    except UncontainedProcessError as exc:
        run_dir = attempts_root / attempt_id
        if run_dir.is_dir():
            failure_path = run_dir / "process_control_failure.json"
            if not failure_path.exists():
                _v1._write_json_exclusive(
                    failure_path,
                    {
                        "schema_version": PROCESS_CONTROL_SCHEMA,
                        "error": f"{type(exc).__name__}: {exc}",
                        "process_control": exc.process_control,
                        "completed_utc": utc_now(),
                    },
                )
        raise
    except Exception as exc:
        assert state.process_started_attempts is not None
        assert state.process_controls is not None
        assert state.process_measurements is not None
        observed_control = state.process_controls.get(attempt_id)
        process_started = attempt_id in state.process_started_attempts
        if process_started and (
            observed_control is None
            or not _control_proves_containment(observed_control)
        ):
            raise UncontainedProcessError(
                "ordinary exception occurred before containment was proven",
                (
                    observed_control
                    if observed_control is not None
                    else {
                        **_no_process_control(str(exc)),
                        "process_started": True,
                    }
                ),
            ) from exc
        if observed_control is None:
            observed_control = _no_process_control(str(exc))
            state.process_controls[attempt_id] = observed_control
        result = _fallback_attempt_result(
            plan=plan,
            pair=pair,
            block_id=block_id,
            repetition=repetition,
            order=order,
            order_position=order_position,
            sequence=sequence,
            method=method,
            config=config,
            attempts_root=attempts_root,
            attempt_kind=attempt_kind,
            control=observed_control,
            measurement=state.process_measurements.get(attempt_id),
            error=exc,
        )
    finally:
        state.current_attempt_id = None

    with _probe_context(
        "after-attempt", state.current_attempt_before_percent
    ):
        after = _host_condition_snapshot(plan, state.output_path)
    state.current_attempt_before_percent = None
    control = state.process_controls.pop(attempt_id, None)
    if control is None:
        assert state.process_started_attempts is not None
        if attempt_id in state.process_started_attempts:
            unknown = _no_process_control(
                "process started but containment evidence is unavailable"
            )
            unknown["process_started"] = True
            raise UncontainedProcessError(
                "process containment is unknown after an ordinary exception",
                unknown,
            )
        control = _no_process_control(result.get("orchestration_error"))
    result = _decorate_attempt_result(
        result, before=before, after=after, control=control
    )
    host_valid = result["host_attempt_valid"]

    run_dir = attempts_root / attempt_id
    try:
        _replace_json(run_dir / "result.json", result)
        _ORIGINAL_FREEZE_TREE(run_dir)
    except Exception as exc:
        if not _control_proves_containment(control):
            raise UncontainedProcessError(
                "cannot seal a post-attempt failure without proven containment",
                control,
            ) from exc
        _force_wrapper_failure(result, exc)
        _emergency_replace_json(run_dir / "result.json", result)
        _emergency_freeze_tree(run_dir)
    if not host_valid:
        reason = (
            f"{attempt_id}: after-attempt host-power stability failed"
        )
        _record_host_abort(
            state,
            stage="after-attempt",
            attempt_id=attempt_id,
            block_id=block_id,
            method=method.method_id,
            order_position=order_position,
            snapshot=after,
            reason=reason,
        )
    assert state.completed_runs is not None
    state.completed_runs.append(result)
    if order_position == 2:
        state.boundary_after_block = block_id
    return result


def _pair_result(
    plan: Plan,
    block_id: str,
    pair: Pair,
    repetition: int,
    order: str,
    attempts: list[dict[str, Any]],
    host_conditions_before: dict[str, Any],
    host_conditions_after: dict[str, Any],
) -> dict[str, Any]:
    result = _ORIGINAL_PAIR_RESULT(
        plan,
        block_id,
        pair,
        repetition,
        order,
        attempts,
        host_conditions_before,
        host_conditions_after,
    )
    by_method = {attempt["method"]: attempt for attempt in attempts}
    attempt_validity = {
        method: bool(by_method[method]["host_attempt_valid"])
        for method in ("reference", "optimized")
    }
    result["schema_version"] = PAIR_SCHEMA
    result["attempt_host_validity"] = attempt_validity
    result["host_block_valid"] = bool(
        result["host_block_valid"] and all(attempt_validity.values())
    )
    result["pair_valid"] = bool(
        result["pair_valid"] and result["host_block_valid"]
    )
    result["block_terminal_category"] = (
        "completed"
        if result["host_block_valid"]
        else "host-power-stability-contamination"
    )
    if _ACTIVE_STATE is not None:
        assert _ACTIVE_STATE.completed_pairs is not None
        _ACTIVE_STATE.completed_pairs.append(result)
    return result


@contextmanager
def _runtime_patches() -> Iterator[None]:
    replacements = {
        "PLAN_SCHEMA": PLAN_SCHEMA,
        "RESULT_SCHEMA": RESULT_SCHEMA,
        "PAIR_SCHEMA": PAIR_SCHEMA,
        "__file__": str(Path(__file__).resolve()),
        "Path": _GuardedPath,
        "preflight": preflight,
        "_validate_experiment_controls": _validate_experiment_controls,
        "_host_condition_snapshot": _host_condition_snapshot,
        "_enforce_idle_cooldown": _enforce_idle_cooldown,
        "_run_attempt": _run_attempt,
        "_pair_result": _pair_result,
        "run_measured_process": run_measured_process,
        "_terminal_category": _terminal_category,
        "_freeze_tree": _freeze_tree,
    }
    previous = {key: getattr(_v1, key) for key in replacements}
    for key, value in replacements.items():
        setattr(_v1, key, value)
    try:
        yield
    finally:
        for key, value in previous.items():
            setattr(_v1, key, value)


def preflight(
    plan: Plan, filesystem_path: Path | None = None
) -> dict[str, Any]:
    if not isinstance(plan, Plan):
        raise RunnerError("v2 preflight requires a parsed v2 plan")
    _verify_base_runner()
    global _ACTIVE_STATE
    ephemeral = _ACTIVE_STATE is None
    if ephemeral:
        _ACTIVE_STATE = _ExecutionState(
            plan=plan,
            output_path=Path(filesystem_path or plan.run_cwd).resolve(),
        )
    try:
        _require_zero_git_remotes(plan)
        with _probe_context("preflight"), _runtime_patches():
            verification = _ORIGINAL_PREFLIGHT(plan, filesystem_path)
        _require_zero_git_remotes(plan)
        verification["schema_version"] = PLAN_SCHEMA
        verification["power_policy"] = plan.power_policy
        verification["process_control_policy"] = (
            plan.process_control_policy
        )
        verification["amendment_id"] = plan.amendment_id
        if _ACTIVE_STATE is not None:
            _ACTIVE_STATE.preflight_record = verification
        return verification
    finally:
        if ephemeral:
            _ACTIVE_STATE = None


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _manifest_updates(plan: Plan) -> dict[str, Any]:
    return {
        "schema_version": PLAN_SCHEMA,
        "amendment_id": plan.amendment_id,
        "cohort_id": plan.cohort_id,
        "result_root": str(plan.result_root),
        "design_role": plan.design_role,
        "power_policy": plan.power_policy,
        "process_control_policy": plan.process_control_policy,
        "planned_method_attempt_slots": plan.planned_method_attempt_slots,
        "planned_pair_blocks": plan.planned_pair_blocks,
        "analyzer_sha256": plan.analyzer_sha256,
    }


def _decorate_manifest(plan: Plan, output: Path) -> dict[str, Any]:
    path = output / "run_manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest.update(_manifest_updates(plan))
    _replace_json(path, manifest)
    return manifest


def _decorate_session(
    plan: Plan,
    output: Path,
    session: dict[str, Any],
    completion: dict[str, Any],
    state: _ExecutionState,
) -> dict[str, Any]:
    runs = _load_jsonl(output / "runs.jsonl")
    pairs = _load_jsonl(output / "pairs.jsonl")
    measured = [
        run for run in runs if run["attempt_kind"] == "measured"
    ]
    valid_measured = [
        run for run in measured
        if run["terminal_category"] == "completed"
        and run["host_attempt_valid"] is True
        and run["process_control"]["valid"] is True
    ]
    all_attempt_power_valid = all(
        run["host_attempt_valid"] is True for run in runs
    )
    preflight_valid = bool(
        state.preflight_snapshot
        and state.preflight_snapshot["valid"]
    )
    cooldown_valid = bool(
        state.cooldown_record and state.cooldown_record["valid"]
    )
    session_power_valid = bool(
        preflight_valid
        and cooldown_valid
        and all_attempt_power_valid
        and completion["valid"]
        and state.pending_power_abort is None
        and not session.get("host_contaminated_blocks")
    )
    session.update(
        {
            "schema_version": SESSION_SCHEMA,
            "cohort_id": plan.cohort_id,
            "result_root": str(plan.result_root),
            "power_policy": plan.power_policy,
            "process_control_policy": plan.process_control_policy,
            "session_completion_host_conditions": completion,
            "session_power_valid": session_power_valid,
            "host_abort": state.host_abort,
            "planned_method_attempt_slots": (
                plan.planned_method_attempt_slots
            ),
            "planned_pair_blocks": plan.planned_pair_blocks,
            "executed_method_attempt_slots": len(measured),
            "completed_method_attempt_slots": sum(
                run["terminal_category"] == "completed"
                for run in measured
            ),
            "valid_method_attempt_slots": len(valid_measured),
            "executed_pair_blocks": len(pairs),
            "valid_pair_blocks": sum(
                pair["pair_valid"] is True for pair in pairs
            ),
        }
    )
    if len(measured) < plan.planned_method_attempt_slots:
        session["all_attempts_completed"] = False
    session["all_pairs_valid"] = bool(
        session.get("all_pairs_valid") and session_power_valid
    )
    session["stopped_early"] = bool(
        len(pairs) < plan.planned_pair_blocks
    )
    _replace_json(output / "session.json", session)
    return session


def _write_runs_csv_if_missing(output: Path) -> None:
    path = output / "runs.csv"
    if path.exists():
        return
    runs = _load_jsonl(output / "runs.jsonl")
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_v1.CSV_FIELDS)
        writer.writeheader()
        for run in runs:
            writer.writerow(_v1._csv_row(run))
        handle.flush()
        os.fsync(handle.fileno())


def _partial_session(
    plan: Plan,
    output: Path,
    state: _ExecutionState,
) -> dict[str, Any]:
    runs = _load_jsonl(output / "runs.jsonl")
    pairs = _load_jsonl(output / "pairs.jsonl")
    warmups = [run for run in runs if run["attempt_kind"] == "warmup"]
    manifest = json.loads(
        (output / "run_manifest.json").read_text(encoding="utf-8")
    )
    complete_warmup_slots = len(manifest.get("warmup_blocks", [])) * 2
    warmups_valid = bool(
        len(warmups) == complete_warmup_slots
        and all(
            run["terminal_category"] == "completed"
            and run["host_attempt_valid"] is True
            and run["process_control"]["valid"] is True
            for run in warmups
        )
    )
    contaminated = [
        str(pair["block_id"])
        for pair in pairs
        if pair["host_block_valid"] is not True
    ]
    trailing_invalid = [
        str(run["block_id"])
        for run in runs
        if run["host_attempt_valid"] is not True
        and str(run["block_id"]) not in contaminated
    ]
    contaminated.extend(trailing_invalid)
    return {
        "schema_version": SESSION_SCHEMA,
        "completed_utc": utc_now(),
        "attempt_count": len(runs),
        "warmup_attempt_count": len(warmups),
        "warmups_valid": warmups_valid,
        "timestep_seconds": plan.timestep_seconds,
        "idle_cooldown_seconds": plan.idle_cooldown_seconds,
        "idle_cooldown": state.cooldown_record,
        "minimum_free_disk_bytes": plan.minimum_free_disk_bytes,
        "maximum_peak_rss_mib": plan.maximum_peak_rss_mib,
        "power_mode": plan.power_mode,
        "thermal_policy": plan.thermal_policy,
        "task_validity_predicate_id": plan.task_validity_predicate_id,
        "host_contaminated_blocks": contaminated,
        "pair_count": len(pairs),
        "all_attempts_completed": False,
        "all_pairs_valid": False,
        "stopped_early": True,
        "capacity": None,
        "capacity_evaluation": None,
    }


def _canonical_zero_attempt_manifest(
    plan: Plan,
    state: _ExecutionState,
) -> dict[str, Any]:
    verification = state.preflight_record
    if verification is None or state.cooldown_record is None:
        raise RunnerError(
            "zero-attempt seal lacks completed preflight/cooldown evidence"
        )
    if verification.get("idle_cooldown") != state.cooldown_record:
        raise RunnerError(
            "zero-attempt preflight/cooldown evidence is inconsistent"
        )
    ordered_pairs = _v1._ordered_pairs(plan)
    planned_blocks = _v1._planned_blocks(plan, ordered_pairs)
    randomizer = _v1.random.Random(plan.order_seed)
    extra_order = "AB" if randomizer.getrandbits(1) == 0 else "BA"
    order_counts = {
        "AB": len(planned_blocks) // 2,
        "BA": len(planned_blocks) // 2,
    }
    if len(planned_blocks) % 2:
        order_counts[extra_order] += 1
    orders = (
        ["AB"] * order_counts["AB"] + ["BA"] * order_counts["BA"]
    )
    randomizer.shuffle(orders)
    for block, order in zip(planned_blocks, orders):
        block["order"] = order

    warmup_blocks: list[dict[str, Any]] = []
    if plan.mode == "timing":
        seen_maps: set[str] = set()
        for pair in ordered_pairs:
            if pair.map_artifact.sha256 in seen_maps:
                continue
            seen_maps.add(pair.map_artifact.sha256)
            warmup_blocks.append(
                {
                    "block_id": (
                        f"warmup-{len(warmup_blocks) + 1:03d}-"
                        f"{pair.map_artifact.sha256[:12]}"
                    ),
                    "pair_id": pair.pair_id,
                    "repetition": 0,
                    "order": "warmup",
                    "environment": pair.environment,
                    "seed": pair.seed,
                    "robot_count": pair.robot_count,
                    "map_sha256": pair.map_artifact.sha256,
                }
            )
    manifest = {
        "schema_version": PLAN_SCHEMA,
        "created_utc": utc_now(),
        "plan_path": str(plan.path),
        "plan_sha256": plan.plan_sha256,
        "phase": plan.phase,
        "mode": plan.mode,
        "serial_execution": True,
        "order_policy": (
            "seeded balanced AB/BA assignment; measured blocks only"
        ),
        "count_order_policy": (
            "seeded base permutation alternated forward/reverse across "
            "seed-repetition units; all finite grid points execute "
            "nonadaptively"
            if _v1._execution_grid(plan) is not None
            else "frozen pair order repeated in complete rounds"
        ),
        "order_seed": plan.order_seed,
        "fixed_work_timesteps": plan.fixed_work_timesteps,
        "timestep_seconds": plan.timestep_seconds,
        "idle_cooldown_seconds": plan.idle_cooldown_seconds,
        "idle_cooldown": state.cooldown_record,
        "repetitions": plan.repetitions,
        "warmups_per_binary_map": plan.warmups_per_binary_map,
        "minimum_free_disk_bytes": plan.minimum_free_disk_bytes,
        "maximum_peak_rss_mib": plan.maximum_peak_rss_mib,
        "power_mode": plan.power_mode,
        "thermal_policy": plan.thermal_policy,
        "task_validity_predicate_id": (
            plan.task_validity_predicate_id
        ),
        "preflight_host_conditions": verification["host_conditions"],
        "warmup_blocks": warmup_blocks,
        "blocks": planned_blocks,
        "capacity_search": (
            {
                "environment": plan.capacity.environment,
                "reference_anchor_n": plan.capacity.reference_anchor_n,
                "grid": list(plan.capacity.grid),
                "max_points": plan.capacity.max_points,
            }
            if plan.capacity
            else None
        ),
        "capacity_evaluation": (
            plan.capacity_evaluation.record()
            if plan.capacity_evaluation is not None
            else None
        ),
    }
    manifest.update(_manifest_updates(plan))
    return manifest


def _first_scheduled_slot(
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    warmups = manifest["warmup_blocks"]
    if warmups:
        return warmups[0], "reference"
    blocks = manifest["blocks"]
    if not blocks:
        raise RunnerError("zero-attempt seal has no registered first slot")
    block = blocks[0]
    return block, "reference" if block["order"] == "AB" else "optimized"


def _ensure_real_mutable_directory(path: Path) -> None:
    if path.is_symlink():
        raise RunnerError(f"cannot seal through symbolic-link directory: {path}")
    if path.exists():
        if not path.is_dir():
            raise RunnerError(f"seal path is not a directory: {path}")
        path.chmod(0o755)
        return
    path.mkdir()


def _purge_directory_fd(
    directory_fd: int,
    *,
    root_device: int,
    flags: int,
) -> None:
    try:
        with os.scandir(directory_fd) as iterator:
            entries = [
                (
                    entry.name,
                    entry.stat(follow_symlinks=False),
                )
                for entry in iterator
            ]
    except OSError as exc:
        raise UnsafeEvidencePurgeError(
            f"cannot inventory partial zero-run evidence: {exc}"
        ) from exc
    for name, observed in entries:
        if stat.S_ISDIR(observed.st_mode):
            if observed.st_dev != root_device:
                raise UnsafeEvidencePurgeError(
                    "partial zero-run tree crosses a filesystem boundary"
                )
            try:
                child_fd = os.open(
                    name,
                    flags,
                    dir_fd=directory_fd,
                )
            except OSError as exc:
                raise UnsafeEvidencePurgeError(
                    f"cannot safely open partial directory {name!r}: {exc}"
                ) from exc
            try:
                opened = os.fstat(child_fd)
                if (
                    opened.st_dev,
                    opened.st_ino,
                ) != (
                    observed.st_dev,
                    observed.st_ino,
                ):
                    raise UnsafeEvidencePurgeError(
                        f"partial directory identity changed: {name!r}"
                    )
                os.fchmod(child_fd, 0o755)
                _purge_directory_fd(
                    child_fd,
                    root_device=root_device,
                    flags=flags,
                )
                os.fsync(child_fd)
            except OSError as exc:
                raise UnsafeEvidencePurgeError(
                    f"cannot purge partial directory {name!r}: {exc}"
                ) from exc
            finally:
                os.close(child_fd)
            try:
                os.rmdir(name, dir_fd=directory_fd)
            except OSError as exc:
                raise UnsafeEvidencePurgeError(
                    f"cannot remove partial directory {name!r}: {exc}"
                ) from exc
            continue
        try:
            os.unlink(name, dir_fd=directory_fd)
        except OSError as exc:
            raise UnsafeEvidencePurgeError(
                f"cannot remove partial entry {name!r}: {exc}"
            ) from exc


def _purge_zero_attempt_result_root(state: _ExecutionState) -> None:
    identity = state.output_identity
    if identity is None:
        raise UnsafeEvidencePurgeError(
            "zero-run result_root identity is unavailable"
        )
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise UnsafeEvidencePurgeError(
            "host lacks descriptor-safe no-follow directory flags"
        )
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        root_fd = os.open(state.output_path, flags)
    except OSError as exc:
        raise UnsafeEvidencePurgeError(
            f"cannot safely open zero-run result_root: {exc}"
        ) from exc
    try:
        observed = os.fstat(root_fd)
        if (observed.st_dev, observed.st_ino) != identity:
            raise UnsafeEvidencePurgeError(
                "zero-run result_root identity changed before purge"
            )
        os.fchmod(root_fd, 0o755)
        _purge_directory_fd(
            root_fd,
            root_device=observed.st_dev,
            flags=flags,
        )
        os.fsync(root_fd)
    except OSError as exc:
        raise UnsafeEvidencePurgeError(
            f"cannot purge zero-run result_root: {exc}"
        ) from exc
    finally:
        os.close(root_fd)
    _require_output_identity(state)


def _write_zero_attempt_foundation(
    plan: Plan,
    output: Path,
    state: _ExecutionState,
    manifest: dict[str, Any],
) -> None:
    plan_bytes = state.frozen_plan_bytes
    if (
        plan_bytes is None
        or hashlib.sha256(plan_bytes).hexdigest() != plan.plan_sha256
    ):
        raise RunnerError("zero-attempt seal lacks the exact frozen plan bytes")
    verification = state.preflight_record
    if verification is None:
        raise RunnerError("zero-attempt seal lacks preflight evidence")
    _purge_zero_attempt_result_root(state)
    output.chmod(0o755)
    _ensure_real_mutable_directory(output / "blocks")
    _ensure_real_mutable_directory(output / "attempts")
    _emergency_replace_bytes(output / "frozen_plan.json", plan_bytes)
    _emergency_replace_json(output / "preflight.json", verification)
    _emergency_replace_json(output / "run_manifest.json", manifest)
    _rewrite_execution_ledgers(output, [], [])


def _zero_attempt_orchestration_abort(
    manifest: dict[str, Any],
    error: Exception,
) -> tuple[dict[str, Any], dict[str, Any]]:
    block, method = _first_scheduled_slot(manifest)
    attempt_id = f"000001__{block['block_id']}__{method}"
    detail = _post_containment_error_text(error)
    host_abort = {
        "stage": "orchestration",
        "attempt_id": attempt_id,
        "block_id": block["block_id"],
        "method": method,
        "order_position": 1,
        "failed_snapshot": None,
        "probe_error": detail,
        "reason": detail,
    }
    diagnostic = {
        "schema_version": ORCHESTRATION_ABORT_SCHEMA,
        "stage": "pre-first-attempt",
        "error": detail,
        "process_started": False,
        "attempt_id": attempt_id,
        "block_id": block["block_id"],
        "method": method,
        "order_position": 1,
        "completed_utc": utc_now(),
    }
    return host_abort, diagnostic


def _seal_zero_attempt_execution_abort(
    plan: Plan,
    output: Path,
    state: _ExecutionState,
    error: Exception,
) -> dict[str, Any]:
    assert state.process_started_attempts is not None
    assert state.completed_pairs is not None
    if state.process_started_attempts or state.completed_pairs:
        raise RunnerError(
            "zero-attempt orchestration seal requires no process or pair"
        ) from error
    manifest = _canonical_zero_attempt_manifest(plan, state)
    _write_zero_attempt_foundation(plan, output, state, manifest)
    host_abort, diagnostic = _zero_attempt_orchestration_abort(
        manifest,
        error,
    )
    state.host_abort = host_abort
    state.pending_power_abort = host_abort["reason"]
    _emergency_replace_json(
        output / "orchestration_abort.json",
        diagnostic,
    )
    with _probe_context("session-complete"):
        completion = _host_condition_snapshot(plan, output)
    session = _partial_session(plan, output, state)
    try:
        session = _decorate_session(
            plan,
            output,
            session,
            completion,
            state,
        )
    except Exception:
        if (
            session.get("schema_version") != SESSION_SCHEMA
            or session.get("host_abort") != state.host_abort
        ):
            raise
    session["all_attempts_completed"] = False
    session["all_pairs_valid"] = False
    session["stopped_early"] = True
    _emergency_replace_json(output / "session.json", session)
    try:
        _ORIGINAL_FREEZE_TREE(output)
    except Exception:
        _emergency_freeze_tree(output)
    return session


def _rewrite_execution_ledgers(
    output: Path,
    runs: list[dict[str, Any]],
    pairs: list[dict[str, Any]],
) -> None:
    _emergency_replace_bytes(
        output / "runs.jsonl",
        b"".join(canonical_json_bytes(run) for run in runs),
    )
    _emergency_replace_bytes(
        output / "pairs.jsonl",
        b"".join(canonical_json_bytes(pair) for pair in pairs),
    )
    csv_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(csv_buffer, fieldnames=_v1.CSV_FIELDS)
    writer.writeheader()
    for run in runs:
        writer.writerow(_v1._csv_row(run))
    _emergency_replace_bytes(
        output / "runs.csv",
        csv_buffer.getvalue().encode("utf-8"),
    )


def _invalidate_pair_for_wrapper(
    pair: dict[str, Any],
    run: dict[str, Any],
) -> bool:
    if run["attempt_id"] not in pair["attempt_ids"].values():
        return False
    pair["terminal_categories"][run["method"]] = (
        "measurement-wrapper-failure"
    )
    for field in (
        "semantic_trace_equal",
        "frames_compared",
        "mismatch",
        "paper_metrics_equal",
        "summary_parity_fields_equal",
        "summary_mismatch",
        "task_valid",
    ):
        pair[field] = None
    pair["pair_valid"] = False
    return True


def _terminal_block_boundaries(
    plan: Plan,
    output: Path,
    state: _ExecutionState,
    block_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    assert state.block_boundary_before is not None
    assert state.block_boundary_after is not None
    before = state.block_boundary_before.get(block_id)
    if before is None:
        raise RunnerError(
            f"terminal block {block_id} lacks its before-boundary evidence"
        )
    after = state.block_boundary_after.get(block_id)
    if after is None:
        state.boundary_after_block = block_id
        with _probe_context("block-boundary"):
            after = _host_condition_snapshot(plan, output)
        after = state.block_boundary_after.get(block_id)
    if after is None:
        raise RunnerError(
            f"terminal block {block_id} lacks its after-boundary evidence"
        )
    return before, after


def _synthesized_warmup_result(
    plan: Plan,
    attempts: list[dict[str, Any]],
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    by_method = {item["method"]: item for item in attempts}
    first = attempts[0]
    config_equal = (
        by_method["reference"]["provenance"]["config_sha256"]
        == by_method["optimized"]["provenance"]["config_sha256"]
    )
    host_valid = _v1._host_block_valid(before, after)
    result = {
        "schema_version": "dancers-comparison-warmup-v1",
        "block_id": first["block_id"],
        "pair_id": first["pair_id"],
        "environment": first["environment"],
        "seed": first["seed"],
        "robot_count": first["robot_count"],
        "map_sha256": first["provenance"]["map_sha256"],
        "attempt_ids": {
            method: by_method[method]["attempt_id"]
            for method in ("reference", "optimized")
        },
        "config_sha256_equal": config_equal,
        "terminal_categories": {
            method: by_method[method]["terminal_category"]
            for method in ("reference", "optimized")
        },
        "host_conditions_before": before,
        "host_conditions_after": after,
        "host_block_valid": host_valid,
        "warmup_valid": bool(
            config_equal
            and host_valid
            and all(
                by_method[method]["terminal_category"] == "completed"
                for method in ("reference", "optimized")
            )
        ),
        "block_terminal_category": (
            "completed"
            if host_valid
            else "host-power-thermal-contamination"
        ),
    }
    return result


def _synthesize_terminal_aggregate(
    plan: Plan,
    output: Path,
    state: _ExecutionState,
    runs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    assert state.completed_pairs is not None
    last = runs[-1]
    block_id = str(last["block_id"])
    attempts = [
        run for run in runs if str(run["block_id"]) == block_id
    ]
    if len(attempts) != 2:
        return None
    attempts.sort(key=lambda item: int(item["order_position"]))
    if [int(item["order_position"]) for item in attempts] != [1, 2]:
        raise RunnerError(
            f"terminal block {block_id} has an invalid attempt prefix"
        )
    if attempts[-1] is not last:
        raise RunnerError(
            f"terminal block {block_id} is not the ledger suffix"
        )
    before, after = _terminal_block_boundaries(
        plan,
        output,
        state,
        block_id,
    )
    if last["attempt_kind"] == "warmup":
        return _synthesized_warmup_result(
            plan,
            attempts,
            before,
            after,
        )
    if any(
        str(pair["block_id"]) == block_id
        for pair in state.completed_pairs
    ):
        return None
    try:
        pair = next(
            item for item in plan.pairs
            if item.pair_id == last["pair_id"]
        )
    except StopIteration as exc:
        raise RunnerError(
            f"terminal block {block_id} has no registered pair"
        ) from exc
    _pair_result(
        plan,
        block_id,
        pair,
        int(last["repetition"]),
        str(last["order"]),
        attempts,
        before,
        after,
    )
    return None


def _seal_generic_execution_abort(
    plan: Plan,
    output: Path,
    state: _ExecutionState,
    error: Exception,
) -> dict[str, Any]:
    """Best-effort canonical seal after an ordinary contained failure."""

    assert state.completed_runs is not None
    assert state.completed_pairs is not None
    assert state.process_started_attempts is not None
    assert state.process_controls is not None
    runs = list(state.completed_runs)
    if not runs:
        if state.process_started_attempts:
            attempt_id = sorted(state.process_started_attempts)[0]
            control = state.process_controls.get(attempt_id)
            raise UncontainedProcessError(
                "zero-attempt abort observed an unreportable process start",
                (
                    control
                    if control is not None
                    else {
                        **_no_process_control(str(error)),
                        "process_started": True,
                    }
                ),
            ) from error
        return _seal_zero_attempt_execution_abort(
            plan,
            output,
            state,
            error,
        )
    completed_ids = {str(run["attempt_id"]) for run in runs}
    for attempt_id in state.process_started_attempts - completed_ids:
        control = state.process_controls.get(attempt_id)
        if control is None or not _control_proves_containment(control):
            raise UncontainedProcessError(
                "ordinary failure left process containment unproven",
                (
                    control
                    if control is not None
                    else {
                        **_no_process_control(str(error)),
                        "process_started": True,
                    }
                ),
            ) from error
    for run in runs:
        if not _control_proves_containment(run["process_control"]):
            raise UncontainedProcessError(
                "recorded attempt does not prove process containment",
                run["process_control"],
            ) from error

    last = runs[-1]
    _force_wrapper_failure(last, error)
    synthesized_warmup = _synthesize_terminal_aggregate(
        plan,
        output,
        state,
        runs,
    )
    run_dir = output / "attempts" / str(last["attempt_id"])
    run_dir.chmod(0o755)
    result_path = run_dir / "result.json"
    if result_path.exists():
        result_path.chmod(0o644)
    _emergency_replace_json(result_path, last)
    _emergency_freeze_tree(run_dir)

    pairs = list(state.completed_pairs)
    for pair in pairs:
        if not _invalidate_pair_for_wrapper(pair, last):
            continue
        block_dir = output / "blocks" / str(pair["block_id"])
        block_dir.chmod(0o755)
        pair_path = block_dir / "pair.json"
        if pair_path.exists():
            pair_path.chmod(0o644)
        _emergency_replace_json(pair_path, pair)
        _emergency_freeze_tree(block_dir)
    warmup_path = output / "blocks" / str(last["block_id"]) / "warmup.json"
    if last["attempt_kind"] == "warmup" and (
        synthesized_warmup is not None or warmup_path.is_file()
    ):
        warmup = (
            synthesized_warmup
            if synthesized_warmup is not None
            else json.loads(warmup_path.read_text(encoding="utf-8"))
        )
        warmup["terminal_categories"][last["method"]] = (
            "measurement-wrapper-failure"
        )
        warmup["warmup_valid"] = False
        warmup_path.parent.chmod(0o755)
        if warmup_path.exists():
            warmup_path.chmod(0o644)
        _emergency_replace_json(warmup_path, warmup)
        _emergency_freeze_tree(warmup_path.parent)

    _rewrite_execution_ledgers(output, runs, pairs)
    manifest_path = output / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(_manifest_updates(plan))
    _emergency_replace_json(manifest_path, manifest)
    with _probe_context("session-complete"):
        completion = _host_condition_snapshot(plan, output)
    session = _partial_session(plan, output, state)
    try:
        session = _decorate_session(
            plan,
            output,
            session,
            completion,
            state,
        )
    except Exception:
        if session.get("schema_version") != SESSION_SCHEMA:
            raise
        _emergency_replace_json(output / "session.json", session)
    session["all_attempts_completed"] = False
    session["all_pairs_valid"] = False
    _emergency_replace_json(output / "session.json", session)
    diagnostic = output / "orchestration_abort.json"
    if not diagnostic.exists():
        _v1._write_json_exclusive(
            diagnostic,
            {
                "schema_version": "dancers-orchestration-abort-v1",
                "error": _post_containment_error_text(error),
                "last_attempt_id": last["attempt_id"],
                "completed_utc": utc_now(),
            },
        )
    _emergency_freeze_tree(output)
    return session


def execute(plan: Plan, output: Path | str) -> dict[str, Any]:
    if not isinstance(plan, Plan):
        raise RunnerError("v2 execute requires a parsed v2 plan")
    global _ACTIVE_STATE
    if _ACTIVE_STATE is not None:
        raise RunnerError("nested v2 execution is not supported")
    _validate_registered_result_root(
        plan.run_cwd, plan.result_root, plan.mode
    )
    output_path = _validate_registered_result_root(
        plan.run_cwd, output, plan.mode
    )
    if output_path != plan.result_root:
        raise RunnerError(
            f"--output must exactly equal frozen result_root {plan.result_root}"
        )
    state = _ExecutionState(plan=plan, output_path=output_path)
    try:
        state.frozen_plan_bytes = plan.path.read_bytes()
    except OSError:
        state.frozen_plan_bytes = None
    _ACTIVE_STATE = state
    try:
        try:
            try:
                with _runtime_patches():
                    session = _ORIGINAL_EXECUTE(plan, output_path)
            except PowerStabilityAbort:
                if not output_path.is_dir():
                    raise
                with _probe_context("session-complete"):
                    completion = _host_condition_snapshot(plan, output_path)
                _decorate_manifest(plan, output_path)
                session = _partial_session(plan, output_path, state)
                session = _decorate_session(
                    plan, output_path, session, completion, state
                )
                session["all_attempts_completed"] = False
                session["all_pairs_valid"] = False
                session["stopped_early"] = True
                _replace_json(output_path / "session.json", session)
                _write_runs_csv_if_missing(output_path)
                _ORIGINAL_FREEZE_TREE(output_path)
                return session
            with _probe_context("session-complete"):
                completion = _host_condition_snapshot(plan, output_path)
            _decorate_manifest(plan, output_path)
            session = _decorate_session(
                plan, output_path, session, completion, state
            )
            if not session["session_power_valid"]:
                session["all_pairs_valid"] = False
                _replace_json(output_path / "session.json", session)
            _ORIGINAL_FREEZE_TREE(output_path)
            return session
        except Exception as exc:
            if (
                state.output_identity is None
                or not output_path.is_dir()
                or output_path.is_symlink()
            ):
                raise
            _require_output_identity(state)
            return _seal_generic_execution_abort(
                plan,
                output_path,
                state,
                exc,
            )
    finally:
        _ACTIVE_STATE = None


parse_args = _v1.parse_args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        plan = load_plan(args.plan)
        if args.phase != plan.phase:
            raise RunnerError(
                f"--phase {args.phase} does not match plan phase {plan.phase}"
            )
        if args.mode != plan.mode:
            raise RunnerError(
                f"--mode {args.mode} does not match plan mode {plan.mode}"
            )
        if plan.phase == "validation" and not args.unlock_validation:
            raise RunnerError(
                "validation remains locked; explicitly pass "
                "--phase validation --unlock-validation"
            )
        if args.dry_run:
            verification = preflight(plan)
            print(json.dumps(verification, sort_keys=True))
            return 0
        if args.output is None:
            raise RunnerError("--output is required unless --dry-run is used")
        result = execute(plan, args.output)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["all_pairs_valid"] else 1
    except RunnerError as exc:
        print(f"comparison runner rejected the request: {exc}", file=sys.stderr)
        return 2
    except (PowerStabilityAbort, UncontainedProcessError) as exc:
        print(
            "comparison runner aborted an unsealed session: "
            f"{type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
