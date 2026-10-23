#!/usr/bin/env python3
"""Prospective, fail-closed runner for the frozen DANCeRS--ORCA study.

The public command surface is intentionally non-authorizing.  ``check`` and
``plan`` are read-only.  ``execute`` always refuses before creating a path or
spawning a process.  A future, separately frozen launcher may call
``execute_authorized`` only after authenticating an external authorization and
constructing an :class:`ExecutionPermit` for the exact plan.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import os
import pathlib
import re
import resource
import shutil
import signal
import stat
import struct
import subprocess
import sys
import time
from collections import Counter
from typing import Any, Callable, Mapping, Sequence


REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.replication import prepare_orca_build_capsule as orca_capsule
from scripts.comparison import trace_compare


STUDY_ID = "dancers-orca-v1"
PLAN_SCHEMA = "dancers-orca-execution-plan-v1"
ATTEMPT_SCHEMA = "dancers-orca-attempt-v1"
SESSION_SCHEMA = "dancers-orca-session-v1"
INPUT_RELATIVE = pathlib.PurePosixPath("comparison/publication/dancers-orca-v1")
DEFAULT_INPUT_ROOT = REPO.joinpath(*INPUT_RELATIVE.parts)
EXPECTED_PROCESSES = 363
INPUT_MANIFEST_SHA256 = (
    "3fc8a7572ed6c89eecfd8cdc1f0db2c9b552e63f60117e83bacf0ad24cd153f7"
)
SCHEDULE_SHA256 = (
    "244d268f37a976bf7af8f11ce0bb02430bd72c32372a4bb040e2b586181328c4"
)
ORCA_BUILD_CAPSULE_SHA256 = (
    "a0fa89094f55df970bef6c63a33b4d11791972a0c303b923fa3180769616a428"
)
ORCA_SEQUENCE_SHA256 = (
    "08e7782cab3e2ec7abfb7ac4304a09adee090d94e71c75381debfe8dc4416559"
)
DANCERS_BUILD_MANIFEST_SHA256 = (
    "3c9c4305dae5e5154963ec76bb52e0bb826d6fb5b44a89503235057ad449b3cd"
)
DANCERS_BUILD_MANIFEST_RELATIVE = (
    "results/publication/dancers-multimap-v1-host-1/build_manifest.json"
)
ORCA_BUILD_CAPSULE_RELATIVE = (
    "comparison/publication/dancers-orca-v1-build-capsule/manifest.json"
)
NO_MAP_SHA256 = hashlib.sha256(b"").hexdigest()
PIXI_LOCK_SHA256 = (
    "636ecf1cf31431ffc19f453fef39299c3243718477e321fcea8814225bfe68be"
)
COMMON_PATCH_SHA256 = (
    "2ab81c9a9621c89de5854bbd8496cb2124845733140a913855ca61677185a95e"
)
ALGORITHM_REVISIONS = {
    "reference": "986f47deec5b079663c7d7f6120bfc20f0467901",
    "optimized": "99364a41f1535a4bb0eddbe5a10030127ea77086",
}
SOURCE_ENDPOINTS = {
    "optimized": {
        "algorithm_revision": "99364a41f1535a4bb0eddbe5a10030127ea77086",
        "applied_head": "26c8c6f9b262a2114ac28203419cb03caf8b0d88",
        "source_archive_sha256": "c33937d249dd8ba1d69621aeb146bd1236e2c8c3743cc143f33a925c62bbee2b",
        "tree": "b6f713f47ea3aa57c824cf2c481f3647ac7c9408",
    },
    "reference": {
        "algorithm_revision": "986f47deec5b079663c7d7f6120bfc20f0467901",
        "applied_head": "de0f60d1ad4b944c2b3264b8e3ccd348708b8b8a",
        "source_archive_sha256": "e657fe2064f404762c016ffedc882cfc395ab58f4d8b9bc9a1f0bb11de067ced",
        "tree": "cb93386e28f3824ea7ffc17605b441b92f13f87d",
    },
}
ORCA_SOURCE = {
    "bound_input_manifest_sha256": INPUT_MANIFEST_SHA256,
    "implementation": "snape/RVO2",
    "rvo2_commit": "1c25b27258d191e26c2df83ff6e31abc8efbdaed",
    "rvo2_tree": "754114c13a345b1d228871be7e85683e31b3af5d",
    "wrapper": "dancers-orca-wrapper",
}
PROTOCOL_SHA256 = {
    "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_V1.md": (
        "04bab9f2da0de290346104e5db3d8881df85a2429d1f9964c4e86943ac7350ff"
    ),
    "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_AMENDMENT_1_V1.md": (
        "88b7465b61fa565827939a9ffc1b5c9c3c0a681e28af985c91b2b5076c4215b8"
    ),
    "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_AMENDMENT_2_V1.md": (
        "f5192e4a304028355a449e8d10b2d6a9c5adb863daa72ea0776ee58a1f77d6b6"
    ),
    "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_AMENDMENT_3_V1.md": (
        "dd90ffd163e34b83696be1bc3c31f416fd7901eccd5a3c9364c02709aac6e4d7"
    ),
    "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_AMENDMENT_7_V1.md": (
        "77ec5a6db56731e5f90b44a95aea6426f1c11a80a2ae2bc5f89f366d770654d3"
    ),
    "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_AMENDMENT_8_V1.md": (
        "6b5b9e222e72aad9d9b8e17768e28ab3d24e84c17c75dcd4986f801b6a6f86bd"
    ),
}
EXPECTED_BINARIES = {
    "orca": {
        "bytes": 297240,
        "sha256": "155fc6729a85f8595be4cddecd0cb17f4e58c7a0688ccc789fcabbb3c4f59a9e",
    },
    "optimized_fidelity": {
        "bytes": 2403032,
        "sha256": "97c5f869a17049d87351728829cbba30ecf120d2b3b8cda27872dfdcf545ef72",
    },
    "optimized_timing": {
        "bytes": 2182568,
        "sha256": "4b904a0a61d7b3c46dcb4c824b49429e457b9831c4bc9198601ee99cc75733ee",
    },
    "reference_fidelity": {
        "bytes": 2419320,
        "sha256": "26afb1f7e6dd915381333fa47b9f0d8bd7ac2a1b97d4446e9e5788e64fb85a1e",
    },
    "reference_timing": {
        "bytes": 2182408,
        "sha256": "03fd120158382014201d4b3f19db536c65eab7d88a5d5edd1ecd5c13312e5472",
    },
}
DEFAULT_RUNTIME_PATHS = {
    "orca": REPO / "build/orca-wrapper-publication-v1-pass-1/dancers-orca-wrapper",
    "optimized_fidelity": REPO / "build/publication/dancers-multimap-v1-host-1/optimized/build-fidelity/dancers",
    "optimized_timing": REPO / "build/publication/dancers-multimap-v1-host-1/optimized/build-timing/dancers",
    "reference_fidelity": REPO / "build/publication/dancers-multimap-v1-host-1/reference/build-fidelity/dancers",
    "reference_timing": REPO / "build/publication/dancers-multimap-v1-host-1/reference/build-timing/dancers",
}
DEFAULT_SOURCE_ROOTS = {
    "optimized": REPO / "build/publication/dancers-multimap-v1-host-1/optimized/source",
    "reference": REPO / "build/publication/dancers-multimap-v1-host-1/reference/source",
}
DEFAULT_LIBOMP = (REPO / ".pixi/envs/default/lib/libomp.dylib").resolve()
EXPECTED_LIBOMP = {
    "bytes": 825_088,
    "sha256": "40618282b8a203af8759f46ef431cb58490d3ab9709edc421d0dc2b9059c98aa",
}
PUBLIC_EXECUTION_REFUSAL = (
    "execution authorization is deliberately absent; the public runner cannot "
    "launch a publication process"
)
TIMESTEP_RE = re.compile(rb"(?m)^timesteps:\s*(\d+)\b")
ROBOT_COUNT_RE = re.compile(rb"Final robot count:\s*\[(\d+)\]")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
DANCERS_SUMMARY_PROJECTION_FIELDS = {
    "all_goals_reached",
    "final_robot_count",
    "final_tick",
    "first_all_goals_tick",
    "goals_reached",
    "goals_total",
    "invalid_geometry_evaluations",
    "invalid_robot_frames",
    "iterations",
    "maximum_robot_count",
    "mean_path_length_m_per_robot",
    "minimum_obstacle_clearance_m",
    "minimum_pair_clearance_m",
    "nonfinite_numeric_observations",
    "nonfinite_state_observations",
    "obstacle_collision_events",
    "obstacle_collision_exposure_seconds",
    "pair_collision_events",
    "pair_collision_exposure_seconds",
    "paper_metrics",
    "per_robot_goal_tick",
    "per_robot_path_length_m",
    "run_valid",
    "termination_reason",
    "total_path_length_m",
}


class RunnerError(RuntimeError):
    """A fail-closed study-runner error."""


class _DuplicateJsonKey(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class RuntimeBindings:
    """Host paths whose bytes must match the five frozen executables."""

    orca_binary: pathlib.Path
    optimized_fidelity_binary: pathlib.Path
    optimized_timing_binary: pathlib.Path
    reference_fidelity_binary: pathlib.Path
    reference_timing_binary: pathlib.Path
    optimized_source_root: pathlib.Path
    reference_source_root: pathlib.Path
    libomp: pathlib.Path


@dataclasses.dataclass(frozen=True)
class ExecutionPermit:
    """Token constructed only by a future external-authorization launcher."""

    execution_authorized: bool
    study_id: str
    plan_sha256: str
    input_manifest_sha256: str
    schedule_sha256: str
    authorization_manifest_sha256: str
    admission_manifest_sha256: str
    output_root: pathlib.Path
    expected_processes: int = EXPECTED_PROCESSES
    selective_retry_allowed: bool = False


@dataclasses.dataclass(frozen=True)
class ChildResult:
    returncode: int
    signal: int | None
    timed_out: bool
    wall_seconds: float
    user_cpu_seconds: float
    system_cpu_seconds: float
    peak_rss_raw: int


ProcessRunner = Callable[..., ChildResult]


def canonical_json_bytes(value: object, *, pretty: bool = False) -> bytes:
    options: dict[str, Any] = {
        "allow_nan": False,
        "ensure_ascii": True,
        "sort_keys": True,
    }
    if pretty:
        options["indent"] = 2
    else:
        options["separators"] = (",", ":")
    return (json.dumps(value, **options) + "\n").encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _reject_nonfinite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


def _no_duplicate_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def file_identity(path: pathlib.Path, *, relative_to: pathlib.Path | None = None) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise RunnerError(f"artifact is not a regular file: {path}")
    result: dict[str, Any] = {
        "bytes": path.stat().st_size,
        "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}",
        "sha256": sha256_file(path),
    }
    result["path"] = (
        path.relative_to(relative_to).as_posix() if relative_to is not None else str(path)
    )
    return result


def _write_exclusive(path: pathlib.Path, raw: bytes) -> None:
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise RunnerError(f"exclusive-output parent is absent or unsafe: {path.parent}")
    with path.open("xb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _require_real_absolute(path: pathlib.Path, label: str, *, directory: bool) -> pathlib.Path:
    if not path.is_absolute() or path != path.resolve() or path.is_symlink():
        raise RunnerError(f"{label} must be a real absolute non-symlink path")
    if directory and not path.is_dir():
        raise RunnerError(f"{label} is not a directory: {path}")
    if not directory and not path.is_file():
        raise RunnerError(f"{label} is not a file: {path}")
    return path


def default_runtime_bindings() -> RuntimeBindings:
    return RuntimeBindings(
        orca_binary=DEFAULT_RUNTIME_PATHS["orca"],
        optimized_fidelity_binary=DEFAULT_RUNTIME_PATHS["optimized_fidelity"],
        optimized_timing_binary=DEFAULT_RUNTIME_PATHS["optimized_timing"],
        reference_fidelity_binary=DEFAULT_RUNTIME_PATHS["reference_fidelity"],
        reference_timing_binary=DEFAULT_RUNTIME_PATHS["reference_timing"],
        optimized_source_root=DEFAULT_SOURCE_ROOTS["optimized"],
        reference_source_root=DEFAULT_SOURCE_ROOTS["reference"],
        libomp=DEFAULT_LIBOMP,
    )


def _runtime_binary(runtime: RuntimeBindings, method: str, role: str) -> tuple[str, pathlib.Path]:
    if method == "orca":
        return "orca", runtime.orca_binary
    if method not in ("reference", "optimized") or role not in ("fidelity", "timing"):
        raise RunnerError("schedule method/role is outside the frozen command firewall")
    key = f"{method}_{role}"
    return key, getattr(runtime, f"{key}_binary")


def _source_root(runtime: RuntimeBindings, method: str) -> pathlib.Path:
    if method == "reference":
        return runtime.reference_source_root
    if method == "optimized":
        return runtime.optimized_source_root
    raise RunnerError("ORCA has no DANCeRS source root")


def _require_binary(path: pathlib.Path, key: str) -> dict[str, Any]:
    path = _require_real_absolute(path, f"{key} binary", directory=False)
    expected = EXPECTED_BINARIES[key]
    actual = file_identity(path)
    if (
        actual["bytes"] != expected["bytes"]
        or actual["sha256"] != expected["sha256"]
        or actual["mode"] != "0755"
    ):
        raise RunnerError(f"{key} binary identity differs")
    if not os.access(path, os.X_OK):
        raise RunnerError(f"{key} binary is not executable")
    return actual


def _require_libomp(path: pathlib.Path) -> dict[str, Any]:
    path = _require_real_absolute(path, "libomp", directory=False)
    if path != DEFAULT_LIBOMP:
        raise RunnerError("libomp path differs from the frozen runtime path")
    actual = file_identity(path)
    if (
        actual["bytes"] != EXPECTED_LIBOMP["bytes"]
        or actual["sha256"] != EXPECTED_LIBOMP["sha256"]
        or actual["mode"] != "0755"
    ):
        raise RunnerError("libomp identity differs")
    return actual


def _validate_runtime(runtime: RuntimeBindings, snapshot: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    build_manifest = REPO / DANCERS_BUILD_MANIFEST_RELATIVE
    if (
        build_manifest.is_symlink()
        or not build_manifest.is_file()
        or sha256_file(build_manifest) != DANCERS_BUILD_MANIFEST_SHA256
    ):
        raise RunnerError("DANCeRS build manifest differs from the trust root")
    binaries = {
        key: _require_binary(path, key)
        for key, path in (
            ("orca", runtime.orca_binary),
            ("optimized_fidelity", runtime.optimized_fidelity_binary),
            ("optimized_timing", runtime.optimized_timing_binary),
            ("reference_fidelity", runtime.reference_fidelity_binary),
            ("reference_timing", runtime.reference_timing_binary),
        )
    }
    libomp = _require_libomp(runtime.libomp)
    roots: dict[str, str] = {}
    for method in ("reference", "optimized"):
        source = _require_real_absolute(
            _source_root(runtime, method), f"{method} source root", directory=True
        )
        roots[method] = str(source)
    build_manifest_identity = file_identity(build_manifest, relative_to=REPO)
    if build_manifest_identity["mode"] != "0644":
        raise RunnerError("DANCeRS build manifest mode differs")
    return {
        "binaries": binaries,
        "build_manifest": build_manifest_identity,
        "libomp": libomp,
        "source_endpoints": SOURCE_ENDPOINTS,
        "source_roots": roots,
    }


def validate_runtime_bindings(
    runtime: RuntimeBindings,
    input_root: pathlib.Path = DEFAULT_INPUT_ROOT,
) -> dict[str, Any]:
    """Read-only validation of all five binaries and two endpoint roots."""

    _manifest, _attempts, snapshot = load_frozen_plan(input_root)
    return _validate_runtime(runtime, snapshot)


def load_frozen_plan(
    input_root: pathlib.Path = DEFAULT_INPUT_ROOT,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Authenticate and return the exact frozen input, schedule, and snapshot."""

    input_root = _require_real_absolute(input_root, "input root", directory=True)
    try:
        manifest, attempts, snapshot = orca_capsule.verify_input(input_root)
    except (orca_capsule.CapsuleError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        raise RunnerError(f"frozen input validation failed: {error}") from error
    if sha256_file(input_root / "manifest.json") != INPUT_MANIFEST_SHA256:
        raise RunnerError("input manifest differs from the external trust root")
    if sha256_file(input_root / "schedule.json") != SCHEDULE_SHA256:
        raise RunnerError("schedule differs from the external trust root")
    if len(attempts) != EXPECTED_PROCESSES or [row.get("sequence") for row in attempts] != list(range(EXPECTED_PROCESSES)):
        raise RunnerError("attempt inventory is not exactly sequences 0..362")
    counts = Counter(row.get("method") for row in attempts)
    if counts != Counter({"reference": 119, "optimized": 119, "orca": 125}):
        raise RunnerError(f"method inventory differs: {dict(counts)}")
    for row in attempts:
        _validate_schedule_row(row)
        if row["method"] != "orca":
            config_raw = (input_root / row["config_relpath"]).read_bytes()
            if b"ASSETS_DIR" in config_raw or b"OBSTACLE_FILE" in config_raw:
                raise RunnerError(
                    f"DANCeRS sequence {row['sequence']} config contains an obstacle path"
                )
            resolved = row.get("resolved_parameters")
            common = resolved.get("common") if isinstance(resolved, Mapping) else None
            if not isinstance(common, Mapping) or common.get("obstacles_present") is not False:
                raise RunnerError(
                    f"DANCeRS sequence {row['sequence']} is not statically obstacle-free"
                )
    return manifest, attempts, snapshot


def _validate_schedule_row(row: Mapping[str, Any]) -> None:
    method = row.get("method")
    phase = row.get("phase")
    run_class = row.get("run_class")
    trace_detail = row.get("trace_detail")
    if method not in ("reference", "optimized", "orca"):
        raise RunnerError("schedule method differs")
    if phase not in ("warmup", "measured", "sensitivity"):
        raise RunnerError("schedule phase differs")
    if run_class not in ("digest_fidelity", "positions_fidelity", "timing", "cadence_sensitivity"):
        raise RunnerError("schedule run class differs")
    if type(row.get("timeout_seconds")) not in (int, float) or float(row["timeout_seconds"]) != 300.0:
        raise RunnerError("schedule timeout differs")
    if type(row.get("steps")) is not int or row["steps"] not in (100, 600, 1200):
        raise RunnerError("schedule step count differs")
    if method != "orca" and (phase == "sensitivity" or run_class == "cadence_sensitivity"):
        raise RunnerError("DANCeRS cannot enter the ORCA sensitivity cadence")
    if method != "orca" and trace_detail not in (None, "digest", "positions"):
        raise RunnerError("DANCeRS trace detail differs")
    if method == "orca" and trace_detail not in (None, "positions"):
        raise RunnerError("ORCA trace detail differs")
    for key in ("config_relpath", "scenario_relpath"):
        relative = pathlib.PurePosixPath(str(row.get(key, "")))
        if relative.is_absolute() or not relative.parts or any(part in ("", ".", "..") for part in relative.parts):
            raise RunnerError(f"unsafe schedule path: {key}")


def _repository_identity(relative: str) -> dict[str, Any] | None:
    path = REPO / relative
    if not path.is_file() or path.is_symlink():
        return None
    return file_identity(path, relative_to=REPO)


def _trusted_repository_identity(
    relative: str, expected_sha256: str | None
) -> dict[str, Any]:
    identity = _repository_identity(relative)
    if identity is None or (
        expected_sha256 is not None and identity["sha256"] != expected_sha256
    ):
        raise RunnerError(f"trusted repository artifact differs: {relative}")
    return identity


def _environment_template() -> dict[str, str]:
    env_root = "${REPO}/.pixi/envs/default"
    result = {
        "CMAKE_PREFIX_PATH": env_root,
        "CONDA_PREFIX": env_root,
        "LANG": "C",
        "LC_ALL": "C",
        "OMP_DYNAMIC": "FALSE",
        "OMP_NUM_THREADS": "1",
        "OMP_WAIT_POLICY": "passive",
        "PATH": f"{env_root}/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "TMPDIR": "/private/tmp" if sys.platform == "darwin" else "/tmp",
    }
    if sys.platform == "darwin":
        result["DYLD_LIBRARY_PATH"] = f"{env_root}/lib"
    elif sys.platform.startswith("linux"):
        result["LD_LIBRARY_PATH"] = f"{env_root}/lib"
    else:
        raise RunnerError(f"unsupported command-template platform: {sys.platform}")
    return result


def command_template(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return the pure, serializable command/output contract for one row."""

    _validate_schedule_row(row)
    method = str(row["method"])
    role = "timing" if row["run_class"] == "timing" else "fidelity"
    expects_summary, expects_trace = _expected_artifacts(row)
    environment = _environment_template()
    if method == "orca":
        argv = [
            "${BINARY:orca}",
            "run",
            "--input-root",
            "${INPUT_ROOT}",
            "--attempt-sequence",
            str(row["sequence"]),
            "--summary-out",
            "${ATTEMPT_STAGE}/summary.json",
        ]
        if expects_trace:
            argv.extend(["--trace-out", "${ATTEMPT_STAGE}/trace.ndjson"])
    else:
        argv = [
            f"${{BINARY:{method}_{role}}}",
            "--cfg",
            INPUT_RELATIVE.joinpath(
                *pathlib.PurePosixPath(row["config_relpath"]).parts
            ).as_posix(),
        ]
        if role == "fidelity":
            environment.update(
                {
                    "DANCERS_REQUIRE_PROVENANCE": "1",
                    "DANCERS_RUN_METADATA": "${ATTEMPT_STAGE}/provenance.json",
                    "DANCERS_SUMMARY_OUT": "${ATTEMPT_STAGE}/summary.json",
                    "DANCERS_TRACE_DETAIL": str(row["trace_detail"]),
                    "DANCERS_TRACE_OUT": "${ATTEMPT_STAGE}/trace.ndjson",
                }
            )
    expected_artifacts = ["stderr.txt", "stdout.txt"]
    if method != "orca":
        expected_artifacts.insert(0, "provenance.json")
    if expects_summary:
        expected_artifacts.append("summary.json")
    if expects_trace:
        expected_artifacts.append("trace.ndjson")
    return {
        "binary_role": "orca" if method == "orca" else f"{method}_{role}",
        "command_kind": "orca-wrapper-run" if method == "orca" else "dancers-config-run",
        "cwd": "${ATTEMPT_STAGE}/cwd",
        "environment": environment,
        "expected_artifacts_on_success": expected_artifacts,
        "expects_summary_on_success": expects_summary,
        "expects_trace_on_success": expects_trace,
        "method": method,
        "phase": row["phase"],
        "run_class": row["run_class"],
        "sequence": row["sequence"],
        "argv": argv,
        "steps": row["steps"],
        "timeout_seconds": 300.0,
    }


def result_contract() -> dict[str, Any]:
    """Pure publication/result contract hashed by the execution capsule."""

    return {
        "attempt": {
            "directory": "attempts/{sequence:03d}-{method}",
            "required_files": ["record.json", "stderr.txt", "stdout.txt"],
            "schema_version": ATTEMPT_SCHEMA,
            "staging_directory": "attempts/.staging-{sequence:03d}-{method}",
            "transaction": "record.json written and fsynced last, then same-parent atomic rename",
            "validation_error_rule": "present and nonempty only for artifact_failure",
        },
        "method_outputs_on_success": {
            "dancers_fidelity": ["provenance.json", "summary.json", "trace.ndjson"],
            "dancers_timing": ["provenance.json"],
            "orca_positions": ["summary.json", "trace.ndjson"],
            "orca_timing": ["summary.json"],
        },
        "root_files": [
            "input_manifest.json",
            "plan.json",
            "records.ndjson",
            "schedule.json",
            "session.json",
        ],
        "seal": {
            "create_last": "session.json",
            "records": 363,
            "records_rule": "records.ndjson equals canonical record.json objects in sequence order",
            "resume_allowed": False,
            "selective_retry_allowed": False,
        },
        "session_schema_version": SESSION_SCHEMA,
        "terminal_outcomes": [
            "artifact_failure",
            "completed",
            "nonzero_exit",
            "timeout",
        ],
    }


def _plan_document_from_frozen(
    input_root: pathlib.Path,
    manifest: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Materialize one plan from an already authenticated frozen snapshot."""

    runner = _repository_identity("scripts/replication/run_orca_comparison.py")
    analyzer = _repository_identity("scripts/replication/analyze_orca_comparison.py")
    protocols = {
        relative: _trusted_repository_identity(relative, expected)
        for relative, expected in PROTOCOL_SHA256.items()
    }
    return {
        "admission": {
            "admission_manifest_sha256": None,
            "authorization_manifest_sha256": None,
            "execution_authorized": False,
            "rule": "both digests must be supplied and authenticated by a separate future launcher",
        },
        "artifacts": {
            "analyzer": analyzer,
            "common_patch_sha256": COMMON_PATCH_SHA256,
            "dancers_build_manifest": _trusted_repository_identity(
                DANCERS_BUILD_MANIFEST_RELATIVE,
                DANCERS_BUILD_MANIFEST_SHA256,
            ),
            "expected_binaries": EXPECTED_BINARIES,
            "expected_libomp": EXPECTED_LIBOMP,
            "input_manifest_sha256": INPUT_MANIFEST_SHA256,
            "orca_build_capsule_manifest": _trusted_repository_identity(
                ORCA_BUILD_CAPSULE_RELATIVE,
                ORCA_BUILD_CAPSULE_SHA256,
            ),
            "orca_build_capsule_manifest_sha256": ORCA_BUILD_CAPSULE_SHA256,
            "orca_sequence_sha256": ORCA_SEQUENCE_SHA256,
            "pixi_lock_sha256": PIXI_LOCK_SHA256,
            "protocols": protocols,
            "runner": runner,
            "schedule_sha256": SCHEDULE_SHA256,
            "source_endpoints": SOURCE_ENDPOINTS,
        },
        "attempts": [command_template(row) for row in attempts],
        "classification": {
            "execution_authorized": False,
            "publication_processes_executed": 0,
            "publication_simulation_steps": 0,
            "read_only_plan": True,
        },
        "input": {
            "manifest_bytes": (input_root / "manifest.json").stat().st_size,
            "manifest_sha256": INPUT_MANIFEST_SHA256,
            "root_repository_relative": manifest["root_repository_relative"],
            "schedule_bytes": (input_root / "schedule.json").stat().st_size,
            "schedule_sha256": SCHEDULE_SHA256,
        },
        "inventory": {
            "methods": {"optimized": 119, "orca": 125, "reference": 119},
            "processes": EXPECTED_PROCESSES,
            "sequences": [0, EXPECTED_PROCESSES - 1],
        },
        "policy": {
            "natural_nonzero_retained": True,
            "selective_retry_allowed": False,
            "serial_nonoverlapping": True,
            "timeout_retained": True,
            "timeout_seconds": 300.0,
        },
        "result_contract": {
            "sha256": sha256_bytes(canonical_json_bytes(result_contract())),
            "value": result_contract(),
        },
        "schema_version": PLAN_SCHEMA,
        "study_id": STUDY_ID,
    }


def plan_document(input_root: pathlib.Path = DEFAULT_INPUT_ROOT) -> dict[str, Any]:
    input_root = _require_real_absolute(input_root, "input root", directory=True)
    manifest, attempts, _snapshot = load_frozen_plan(input_root)
    return _plan_document_from_frozen(input_root, manifest, attempts)


def plan_sha256(input_root: pathlib.Path = DEFAULT_INPUT_ROOT) -> str:
    return sha256_bytes(canonical_json_bytes(plan_document(input_root)))


def check_frozen_inputs(input_root: pathlib.Path = DEFAULT_INPUT_ROOT) -> dict[str, Any]:
    _manifest, attempts, snapshot = load_frozen_plan(input_root)
    return {
        "execution_authorized": False,
        "files": len(snapshot),
        "input_manifest_sha256": INPUT_MANIFEST_SHA256,
        "processes": len(attempts),
        "schedule_sha256": SCHEDULE_SHA256,
        "status": "accepted_without_execution",
        "study_id": STUDY_ID,
    }


def _require_snapshot(root: pathlib.Path, expected: Sequence[Mapping[str, Any]], label: str) -> None:
    try:
        actual = orca_capsule.snapshot_tree(root)
        directories = orca_capsule.directory_inventory(root)
        expected_directories = orca_capsule.parent_directories(
            {str(item["path"]) for item in expected}
        )
    except (orca_capsule.CapsuleError, OSError, ValueError, KeyError) as error:
        raise RunnerError(f"{label} cannot be inspected: {error}") from error
    if actual != list(expected) or directories != expected_directories:
        raise RunnerError(f"{label} differs from its exact registered snapshot")


def _copy_input_tree(source: pathlib.Path, destination: pathlib.Path, snapshot: Sequence[Mapping[str, Any]]) -> None:
    if destination.exists() or destination.is_symlink():
        raise RunnerError(f"attempt input destination already exists: {destination}")
    shutil.copytree(source, destination, symlinks=False, copy_function=shutil.copy2)
    _require_snapshot(destination, snapshot, "isolated attempt input copy")


def sanitized_environment() -> dict[str, str]:
    env_root = REPO / ".pixi/envs/default"
    result = {
        "CMAKE_PREFIX_PATH": str(env_root),
        "CONDA_PREFIX": str(env_root),
        "LANG": "C",
        "LC_ALL": "C",
        "OMP_DYNAMIC": "FALSE",
        "OMP_NUM_THREADS": "1",
        "OMP_WAIT_POLICY": "passive",
        "PATH": f"{env_root / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
        "TMPDIR": "/private/tmp" if sys.platform == "darwin" else "/tmp",
    }
    library = str(env_root / "lib")
    if sys.platform == "darwin":
        result["DYLD_LIBRARY_PATH"] = library
    elif sys.platform.startswith("linux"):
        result["LD_LIBRARY_PATH"] = library
    return result


def _attempt_name(row: Mapping[str, Any]) -> str:
    return f"{int(row['sequence']):03d}-{row['method']}"


def _expected_artifacts(row: Mapping[str, Any]) -> tuple[bool, bool]:
    method = row["method"]
    role = "timing" if row["run_class"] == "timing" else "fidelity"
    return method == "orca" or role == "fidelity", row.get("trace_detail") in ("digest", "positions")


def _metadata(row: Mapping[str, Any], binary_sha256: str) -> dict[str, Any]:
    method = str(row["method"])
    if method not in ALGORITHM_REVISIONS:
        raise RunnerError("ORCA does not use DANCeRS provenance metadata")
    return {
        "algorithm_revision": ALGORITHM_REVISIONS[method],
        "binary_sha256": binary_sha256,
        "common_patch_sha256": COMMON_PATCH_SHA256,
        "config_sha256": row["config_sha256"],
        "implementation_id": method,
        "map_sha256": NO_MAP_SHA256,
        "map_status": "absent; SHA-256 is the empty-byte sentinel",
        "omp_threads": 1,
        "pixi_lock_sha256": PIXI_LOCK_SHA256,
        "run_id": f"{STUDY_ID}-{int(row['sequence']):03d}",
        "scenario_sha256": row["scenario_sha256"],
        "scene_id": row["scene_id"],
    }


def derive_command(
    row: Mapping[str, Any],
    runtime: RuntimeBindings,
    attempt_stage: pathlib.Path,
    input_root: pathlib.Path,
    binary_identity: Mapping[str, Any],
) -> tuple[list[str], pathlib.Path, dict[str, str]]:
    """Derive the sole allowed argv/cwd/environment for one authenticated row."""

    _validate_schedule_row(row)
    method = str(row["method"])
    role = "timing" if row["run_class"] == "timing" else "fidelity"
    key, binary = _runtime_binary(runtime, method, role)
    expected = EXPECTED_BINARIES[key]
    if binary_identity.get("sha256") != expected["sha256"] or binary_identity.get("bytes") != expected["bytes"]:
        raise RunnerError("command binary identity is not the frozen identity")
    cwd = attempt_stage / "cwd"
    if not cwd.is_dir() or cwd.is_symlink():
        raise RunnerError("isolated attempt cwd is absent or unsafe")
    environment = sanitized_environment()
    if method == "orca":
        argv = [
            str(binary),
            "run",
            "--input-root",
            str(input_root),
            "--attempt-sequence",
            str(row["sequence"]),
            "--summary-out",
            str(attempt_stage / "summary.json"),
        ]
        if row.get("trace_detail") == "positions":
            argv.extend(["--trace-out", str(attempt_stage / "trace.ndjson")])
        return argv, cwd, environment

    metadata_path = attempt_stage / "provenance.json"
    if role == "fidelity":
        environment.update(
            {
                "DANCERS_RUN_METADATA": str(metadata_path),
                "DANCERS_REQUIRE_PROVENANCE": "1",
                "DANCERS_SUMMARY_OUT": str(attempt_stage / "summary.json"),
                "DANCERS_TRACE_DETAIL": str(row["trace_detail"]),
                "DANCERS_TRACE_OUT": str(attempt_stage / "trace.ndjson"),
            }
        )
    config = INPUT_RELATIVE.joinpath(*pathlib.PurePosixPath(row["config_relpath"]).parts)
    argv = [str(binary), "--cfg", config.as_posix()]
    return argv, cwd, environment


def _rss_to_mib(raw: int) -> tuple[str, float]:
    if type(raw) is not int or raw < 0:
        raise RunnerError("ru_maxrss is not a non-negative integer")
    if sys.platform == "darwin":
        return "bytes", raw / (1024.0 * 1024.0)
    if sys.platform.startswith("linux"):
        return "kibibytes", raw / 1024.0
    raise RunnerError(f"unsupported RSS platform: {sys.platform}")


def _wait_child(pid: int, timeout_seconds: float) -> tuple[int, resource.struct_rusage, bool]:
    deadline = time.monotonic() + timeout_seconds
    while True:
        waited, status, usage = os.wait4(pid, os.WNOHANG)
        if waited == pid:
            return status, usage, False
        if time.monotonic() >= deadline:
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            grace = time.monotonic() + 5.0
            while time.monotonic() < grace:
                waited, status, usage = os.wait4(pid, os.WNOHANG)
                if waited == pid:
                    return status, usage, True
                time.sleep(0.01)
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            _waited, status, usage = os.wait4(pid, 0)
            return status, usage, True
        time.sleep(0.005)


def _terminate_and_reap_child(pid: int) -> None:
    """Best-effort containment for an exception while a direct child is live."""

    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            waited, _status, _usage = os.wait4(pid, os.WNOHANG)
        except ChildProcessError:
            return
        if waited == pid:
            return
        time.sleep(0.01)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        os.wait4(pid, 0)
    except ChildProcessError:
        pass


def run_process(
    *,
    argv: Sequence[str],
    cwd: pathlib.Path,
    environment: Mapping[str, str],
    stdout_path: pathlib.Path,
    stderr_path: pathlib.Path,
    timeout_seconds: float,
) -> ChildResult:
    """Spawn one argv-only process and collect wait4 process-group resources."""

    started = time.monotonic_ns()
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        process = subprocess.Popen(
            list(argv),
            cwd=str(cwd),
            env=dict(environment),
            executable=None,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
            start_new_session=True,
        )
        try:
            status, usage, timed_out = _wait_child(process.pid, timeout_seconds)
        except BaseException:
            _terminate_and_reap_child(process.pid)
            raise
    returncode = os.waitstatus_to_exitcode(status)
    child_signal = os.WTERMSIG(status) if os.WIFSIGNALED(status) else None
    return ChildResult(
        peak_rss_raw=int(usage.ru_maxrss),
        returncode=returncode,
        signal=child_signal,
        system_cpu_seconds=float(usage.ru_stime),
        timed_out=timed_out,
        user_cpu_seconds=float(usage.ru_utime),
        wall_seconds=(time.monotonic_ns() - started) / 1e9,
    )


def _read_json(
    path: pathlib.Path,
    label: str,
    *,
    pretty: bool,
    maximum_bytes: int = 64 * 1024 * 1024,
) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum_bytes:
            raise RunnerError(f"{label} is absent, unsafe, or exceeds its byte bound")
        raw = path.read_bytes()
        value = json.loads(
            raw,
            object_pairs_hook=_no_duplicate_json_object,
            parse_constant=_reject_nonfinite_json,
        )
    except (OSError, RecursionError, ValueError, json.JSONDecodeError) as error:
        raise RunnerError(f"{label} is invalid: {error}") from error
    if not isinstance(value, dict):
        raise RunnerError(f"{label} is not a JSON object")
    if raw != canonical_json_bytes(value, pretty=pretty):
        raise RunnerError(f"{label} does not use the frozen canonical JSON framing")
    return value


def _finite_number(value: object) -> bool:
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, TypeError, ValueError):
        return False


def _f32(value: float) -> float:
    return struct.unpack(">f", struct.pack(">f", float(value)))[0]


def _expected_orca_final_time(row: Mapping[str, Any]) -> tuple[float, str]:
    timestep = _f32(float(row["timestep_seconds"]))
    value = _f32(0.0)
    for _ in range(int(row["steps"])):
        value = _f32(value + timestep)
    return value, struct.pack(">f", value).hex()


def _validate_float_record(
    record: object,
    *,
    bits: int,
    dimensions: int,
) -> bool:
    if not isinstance(record, dict) or set(record) != {"hex", "value"}:
        return False
    values = record["value"] if dimensions > 1 else [record["value"]]
    hex_values = record["hex"] if dimensions > 1 else [record["hex"]]
    if (
        not isinstance(values, list)
        or not isinstance(hex_values, list)
        or len(values) != dimensions
        or len(hex_values) != dimensions
    ):
        return False
    code = ">f" if bits == 32 else ">d"
    width = 8 if bits == 32 else 16
    for value, hexadecimal in zip(values, hex_values):
        if not _finite_number(value) or not isinstance(hexadecimal, str):
            return False
        if re.fullmatch(rf"[0-9a-f]{{{width}}}", hexadecimal) is None:
            return False
        try:
            packed = struct.pack(code, float(value)).hex()
        except (OverflowError, TypeError, ValueError, struct.error):
            return False
        if packed != hexadecimal:
            return False
    return True


def _validate_orca_trace_header(
    header: Mapping[str, Any], row: Mapping[str, Any]
) -> None:
    expected_input = {
        "cadence": row["cadence"],
        "input_manifest_sha256": INPUT_MANIFEST_SHA256,
        "scenario_sha256": row["scenario_sha256"],
        "scene_id": row["scene_id"],
        "scene_set": row["scene_set"],
        "schedule_sha256": SCHEDULE_SHA256,
        "study_id": STUDY_ID,
    }
    audit = header.get("parameter_audit")
    accepted = audit.get("accepted") if isinstance(audit, Mapping) else None
    if (
        set(header)
        != {
            "input",
            "numeric_identity",
            "observation_rule",
            "parameter_audit",
            "schema_version",
            "source",
            "type",
        }
        or header.get("type") != "header"
        or header.get("schema_version") != "dancers-orca-trace-v1"
        or header.get("numeric_identity")
        != "native IEEE-754 binary32 values plus 8-digit hex bits"
        or header.get("observation_rule")
        != "initial state plus post-doStep states; integer common ticks define time"
        or header.get("input") != expected_input
        or header.get("source") != ORCA_SOURCE
        or not isinstance(audit, Mapping)
        or audit.get("verified_through_public_getters") is not True
        or not isinstance(accepted, Mapping)
        or accepted.get("num_agents") != row["robot_count"]
        or accepted.get("num_obstacle_vertices") != 0
    ):
        raise RunnerError(f"ORCA trace header differs at sequence {row['sequence']}")


def _validate_orca_trace_frame(
    frame: Mapping[str, Any], row: Mapping[str, Any], common_tick: int
) -> None:
    expected_native_step = common_tick * int(row["native_sample_stride"])
    robots = frame.get("robots")
    if (
        set(frame) != {"common_tick", "native_step", "nominal_time_seconds", "robots", "type"}
        or frame.get("type") != "frame"
        or frame.get("common_tick") != common_tick
        or frame.get("native_step") != expected_native_step
        or frame.get("nominal_time_seconds") != common_tick / 10.0
        or not isinstance(robots, list)
        or len(robots) != row["robot_count"]
    ):
        raise RunnerError(f"ORCA trace frame differs at sequence {row['sequence']} tick {common_tick}")
    expected_robot_keys = {
        "executed_velocity",
        "first_goal_native_step",
        "goal",
        "goal_distance_m",
        "goal_latched",
        "position",
        "preferred_velocity",
        "radius",
        "rid",
    }
    for rid, robot in enumerate(robots):
        if (
            not isinstance(robot, Mapping)
            or set(robot) != expected_robot_keys
            or robot.get("rid") != rid
            or not _validate_float_record(robot.get("position"), bits=32, dimensions=2)
            or not _validate_float_record(
                robot.get("executed_velocity"), bits=32, dimensions=2
            )
            or not _validate_float_record(robot.get("goal"), bits=32, dimensions=2)
            or not _validate_float_record(
                robot.get("preferred_velocity"), bits=32, dimensions=2
            )
            or not _validate_float_record(robot.get("radius"), bits=32, dimensions=1)
            or not _finite_number(robot.get("goal_distance_m"))
            or not isinstance(robot.get("goal_latched"), bool)
            or (
                robot.get("first_goal_native_step") is not None
                and (
                    type(robot.get("first_goal_native_step")) is not int
                    or not 0 <= robot["first_goal_native_step"] <= expected_native_step
                )
            )
        ):
            raise RunnerError(
                f"ORCA robot trace differs at sequence {row['sequence']} tick {common_tick} rid {rid}"
            )


def _validate_dancers_trace(path: pathlib.Path, row: Mapping[str, Any]) -> None:
    limits = trace_compare.TraceLimits(
        max_file_bytes=1024 * 1024 * 1024,
        max_line_bytes=64 * 1024 * 1024,
        max_frames=2 * int(row["steps"]) + 1,
        max_robots_per_frame=int(row["robot_count"]),
        max_json_depth=128,
    )
    try:
        with trace_compare.TraceReader(path, limits) as reader:
            resolved = reader.header.canonical.get("resolved_config", {})
            provenance = _read_json(
                path.parent / "provenance.json",
                "DANCeRS provenance",
                pretty=False,
            )
            if (
                reader.header.schema_version != trace_compare.TRACE_SCHEMA_V3
                or reader.header.trace_detail != row["trace_detail"]
                or reader.header.provenance != provenance
                or not isinstance(resolved, Mapping)
                or resolved.get("config_file")
                != INPUT_RELATIVE.joinpath(*pathlib.PurePosixPath(row["config_relpath"]).parts).as_posix()
                or resolved.get("scenario_file")
                != INPUT_RELATIVE.joinpath(*pathlib.PurePosixPath(row["scenario_relpath"]).parts).as_posix()
                or resolved.get("obstacle_file") != ""
                or resolved.get("obstacles_present") is not False
                or resolved.get("configured_robot_count") != row["robot_count"]
                or resolved.get("max_timesteps") != row["steps"]
            ):
                raise RunnerError(f"DANCeRS trace header differs at sequence {row['sequence']}")
            frame_count = 0
            for frame in reader.frames():
                frame_count += 1
                robots = frame.get("robots")
                if (
                    not isinstance(robots, list)
                    or len(robots) != row["robot_count"]
                    or [robot.get("rid") for robot in robots if isinstance(robot, Mapping)]
                    != list(range(int(row["robot_count"])))
                ):
                    raise RunnerError(
                        f"DANCeRS robot inventory differs at sequence {row['sequence']}"
                    )
                if row["trace_detail"] == "positions":
                    for robot in robots:
                        if (
                            not isinstance(robot, Mapping)
                            or not _validate_float_record(
                                {"value": robot.get("state"), "hex": robot.get("state_hex")},
                                bits=64,
                                dimensions=6,
                            )
                            or not _finite_number(robot.get("bearing"))
                            or not _finite_number(robot.get("radius_m"))
                            or not isinstance(robot.get("goal_reached"), bool)
                            or not isinstance(robot.get("current_waypoint"), list)
                            or len(robot["current_waypoint"]) != 6
                            or not all(_finite_number(value) for value in robot["current_waypoint"])
                            or not isinstance(robot.get("final_goal"), list)
                            or len(robot["final_goal"]) != 2
                            or not all(_finite_number(value) for value in robot["final_goal"])
                        ):
                            raise RunnerError(
                                f"DANCeRS position record differs at sequence {row['sequence']}"
                            )
            if frame_count != 2 * int(row["steps"]):
                raise RunnerError(f"DANCeRS trace frame count differs at sequence {row['sequence']}")
    except (
        trace_compare.TraceFormatError,
        OSError,
        RecursionError,
        ValueError,
        TypeError,
    ) as error:
        raise RunnerError(f"DANCeRS trace is malformed at sequence {row['sequence']}: {error}") from error


def _validate_trace_structure(row: Mapping[str, Any], path: pathlib.Path) -> None:
    method = row["method"]
    expected_schema = (
        "dancers-orca-trace-v1"
        if method == "orca"
        else "dancers-comparison-trace-v3"
    )
    expected_frames = (
        int(row["common_sample_count"])
        if method == "orca"
        else 2 * int(row["steps"])
    )
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size > 1024 * 1024 * 1024
    ):
        raise RunnerError(f"trace is absent, unsafe, or too large at sequence {row['sequence']}")
    count = 0
    total_bytes = 0
    with path.open("rb") as handle:
        for line_number, line in enumerate(handle, 1):
            total_bytes += len(line)
            if total_bytes > 1024 * 1024 * 1024:
                raise RunnerError(f"trace byte bound exceeded at sequence {row['sequence']}")
            if not line.endswith(b"\n") or len(line) > 64 * 1024 * 1024:
                raise RunnerError(
                    f"trace framing differs at sequence {row['sequence']} line {line_number}"
                )
            try:
                value = trace_compare.parse_portable_trace_json_line(line)
            except (RecursionError, ValueError) as error:
                raise RunnerError(
                    f"trace JSON is invalid at sequence {row['sequence']} line {line_number}: {error}"
                ) from error
            if not isinstance(value, dict):
                raise RunnerError(
                    f"trace row is not an object at sequence {row['sequence']} line {line_number}"
                )
            if line_number == 1 and (
                value.get("type") != "header"
                or value.get("schema_version") != expected_schema
            ):
                raise RunnerError(f"trace header differs at sequence {row['sequence']}")
            if method == "orca":
                if line_number == 1:
                    _validate_orca_trace_header(value, row)
                else:
                    _validate_orca_trace_frame(value, row, line_number - 2)
            count += 1
    if count != expected_frames + 1:
        raise RunnerError(
            f"trace frame count differs at sequence {row['sequence']}: "
            f"{count - 1} != {expected_frames}"
        )
    if method != "orca":
        _validate_dancers_trace(path, row)


def _verify_fixed_work(row: Mapping[str, Any], attempt: pathlib.Path) -> bool:
    stdout = (attempt / "stdout.txt").read_bytes()
    method = row["method"]
    expected_steps = int(row["steps"])
    expected_count = int(row["robot_count"])
    if method == "orca":
        summary = _read_json(
            attempt / "summary.json", "ORCA summary", pretty=True
        )
        input_record = summary.get("input", {})
        parameter_audit = summary.get("parameter_audit", {})
        trace_expected = row.get("trace_detail") == "positions"
        accepted = (
            parameter_audit.get("accepted", {})
            if isinstance(parameter_audit, dict)
            else {}
        )
        source = summary.get("source", {})
        goal_latches = summary.get("goal_latches")
        final_time = summary.get("final_native_global_time", {})
        expected_final_value, expected_final_hex = _expected_orca_final_time(row)
        trace_path = attempt / "trace.ndjson"
        trace_raw = trace_path.read_bytes() if trace_expected else b""
        return bool(
            summary.get("schema_version") == "dancers-orca-summary-v1"
            and summary.get("completed_native_steps") == expected_steps
            and summary.get("common_states_evaluated") == row["common_sample_count"]
            and summary.get("trace_frames_written")
            == (row["common_sample_count"] if trace_expected else 0)
            and summary.get("finite_state") is True
            and summary.get("termination") == "fixed_ceiling"
            and isinstance(goal_latches, list)
            and len(goal_latches) == expected_count
            and isinstance(summary.get("all_goals_latched"), bool)
            and isinstance(final_time, dict)
            and final_time.get("value") == expected_final_value
            and final_time.get("hex") == expected_final_hex
            and isinstance(input_record, dict)
            and input_record.get("attempt_sequence") == row["sequence"]
            and input_record.get("repetition") == row["repetition"]
            and input_record.get("scene_id") == row["scene_id"]
            and input_record.get("scenario_sha256") == row["scenario_sha256"]
            and input_record.get("input_manifest_sha256") == INPUT_MANIFEST_SHA256
            and input_record.get("schedule_sha256") == SCHEDULE_SHA256
            and isinstance(parameter_audit, dict)
            and parameter_audit.get("verified_through_public_getters") is True
            and isinstance(accepted, dict)
            and accepted.get("num_agents") == expected_count
            and accepted.get("num_obstacle_vertices") == 0
            and isinstance(source, dict)
            and source.get("bound_input_manifest_sha256") == INPUT_MANIFEST_SHA256
            and source.get("rvo2_commit") == "1c25b27258d191e26c2df83ff6e31abc8efbdaed"
            and source.get("rvo2_tree") == "754114c13a345b1d228871be7e85683e31b3af5d"
            and summary.get("trace_bytes") == (len(trace_raw) if trace_expected else None)
            and summary.get("trace_sha256")
            == (sha256_bytes(trace_raw) if trace_expected else None)
        )
    ticks = [int(match.group(1)) for match in TIMESTEP_RE.finditer(stdout)]
    counts = [int(match.group(1)) for match in ROBOT_COUNT_RE.finditer(stdout)]
    stdout_fixed = bool(
        ticks
        and ticks[-1] == expected_steps
        and all(tick <= expected_steps for tick in ticks)
        and counts
        and counts[-1] == expected_count
    )
    if row["run_class"] == "timing":
        return stdout_fixed
    summary = _read_json(
        attempt / "summary.json", "DANCeRS summary", pretty=True
    )
    provenance = _read_json(
        attempt / "provenance.json", "DANCeRS provenance", pretty=False
    )
    resolved = summary.get("resolved_config", {})
    path_lengths = summary.get("per_robot_path_length_m")
    goal_ticks = summary.get("per_robot_goal_tick")
    return bool(
        stdout_fixed
        and DANCERS_SUMMARY_PROJECTION_FIELDS.issubset(summary)
        and summary.get("schema_version") == "dancers-comparison-summary-v1"
        and summary.get("metadata") == provenance
        and summary.get("trace_detail") == row["trace_detail"]
        and summary.get("frames_recorded") == 2 * expected_steps
        and summary.get("final_tick") == expected_steps
        and summary.get("final_robot_count") == expected_count
        and summary.get("maximum_robot_count") == expected_count
        and summary.get("iterations") == 2 * expected_steps
        and summary.get("termination_reason") == "maximum_time"
        and summary.get("run_valid") is True
        and summary.get("invalid_geometry_evaluations") == 0
        and summary.get("invalid_robot_frames") == 0
        and summary.get("nonfinite_numeric_observations") == 0
        and summary.get("nonfinite_state_observations") == 0
        and summary.get("goals_total") == expected_count
        and type(summary.get("goals_reached")) is int
        and 0 <= summary["goals_reached"] <= expected_count
        and isinstance(summary.get("all_goals_reached"), bool)
        and isinstance(path_lengths, dict)
        and len(path_lengths) == expected_count
        and all(_finite_number(value) for value in path_lengths.values())
        and isinstance(goal_ticks, dict)
        and len(goal_ticks) == summary["goals_reached"]
        and all(type(value) is int and 0 <= value <= expected_steps for value in goal_ticks.values())
        and _finite_number(summary.get("total_path_length_m"))
        and _finite_number(summary.get("mean_path_length_m_per_robot"))
        and _finite_number(summary.get("minimum_pair_clearance_m"))
        and _finite_number(summary.get("pair_collision_exposure_seconds"))
        and _finite_number(summary.get("obstacle_collision_exposure_seconds"))
        and isinstance(resolved, dict)
        and resolved.get("max_timesteps") == expected_steps
        and resolved.get("configured_robot_count") == expected_count
        and resolved.get("obstacle_file") == ""
        and resolved.get("obstacles_present") is False
    )


def _validate_success_outputs(
    row: Mapping[str, Any],
    attempt: pathlib.Path,
    *,
    input_root: pathlib.Path,
    output_root: pathlib.Path,
    binary_identity: Mapping[str, Any],
) -> None:
    for name in ("stdout.txt", "stderr.txt"):
        path = attempt / name
        if path.is_symlink() or not path.is_file():
            raise RunnerError(f"successful sequence {row['sequence']} is missing {name}")
    expects_summary, expects_trace = _expected_artifacts(row)
    for name, expected in (("summary.json", expects_summary), ("trace.ndjson", expects_trace)):
        path = attempt / name
        if expected and (path.is_symlink() or not path.is_file()):
            raise RunnerError(f"successful sequence {row['sequence']} is missing {name}")
        if not expected and path.exists():
            raise RunnerError(f"sequence {row['sequence']} produced forbidden {name}")
    if expects_trace:
        _validate_trace_structure(row, attempt / "trace.ndjson")
    if not _verify_fixed_work(row, attempt):
        raise RunnerError(f"fixed-work verification failed at sequence {row['sequence']}")

    # A zero-exit artifact may be called completed only if the exact analyzer
    # that is frozen into the plan can consume it.  This keeps the runner's
    # terminal classifier and the later read-only analysis on one contract.
    from scripts.replication import analyze_orca_comparison as analyzer

    analysis_record = {
        "artifacts": _attempt_artifacts(
            attempt, output_root, logical_attempt=attempt
        ),
        "binary": dict(binary_identity),
        "schedule": dict(row),
    }
    try:
        if row["method"] == "orca":
            summary, _semantic = analyzer._orca_summary(
                input_root, output_root, analysis_record
            )
            if expects_trace:
                trajectory = analyzer.normalize_orca_trace(
                    attempt / "trace.ndjson",
                    row,
                    input_root,
                    expected_parameter_audit=summary["parameter_audit"],
                    expected_goal_latches=summary["goal_latches"],
                )
                analyzer.analyze_trajectory(trajectory)
        elif expects_summary:
            provenance = analyzer._dancers_provenance(
                output_root, analysis_record
            )
            analyzer._dancers_summary(output_root, analysis_record)
            trace_path = attempt / "trace.ndjson"
            header, _frames, _raw = analyzer._trace_records(
                trace_path, f"DANCeRS trace for sequence {row['sequence']}"
            )
            analyzer._validate_dancers_header(
                header,
                row,
                provenance,
                label=f"DANCeRS trace for sequence {row['sequence']}",
            )
            if row.get("trace_detail") == "positions":
                trajectory = analyzer.normalize_dancers_trace(
                    trace_path, row, input_root, provenance=provenance
                )
                analyzer.analyze_trajectory(trajectory)
    except (
        analyzer.AnalysisError,
        ArithmeticError,
        OSError,
        RecursionError,
        ValueError,
        TypeError,
    ) as error:
        raise RunnerError(
            f"analysis-contract validation failed at sequence {row['sequence']}: {error}"
        ) from error


def _attempt_artifacts(
    attempt: pathlib.Path,
    output_root: pathlib.Path,
    *,
    logical_attempt: pathlib.Path,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in ("provenance.json", "stderr.txt", "stdout.txt", "summary.json", "trace.ndjson"):
        path = attempt / name
        if path.is_file() and not path.is_symlink():
            identity = file_identity(path, relative_to=output_root)
            identity["path"] = (logical_attempt / name).relative_to(output_root).as_posix()
            result[name] = identity
    return result


def _validate_child_result(result: object, sequence: int) -> ChildResult:
    if not isinstance(result, ChildResult):
        raise RunnerError(f"process receipt type differs at sequence {sequence}")
    if type(result.returncode) is not int or type(result.timed_out) is not bool:
        raise RunnerError(f"process status type differs at sequence {sequence}")
    if result.signal is not None and (
        type(result.signal) is not int or result.signal < 1
    ):
        raise RunnerError(f"process signal differs at sequence {sequence}")
    if result.returncode < 0:
        if result.signal != -result.returncode:
            raise RunnerError(f"process signal/returncode identity differs at sequence {sequence}")
    elif result.signal is not None:
        raise RunnerError(f"non-signalled process records a signal at sequence {sequence}")
    if type(result.peak_rss_raw) is not int or result.peak_rss_raw < 0:
        raise RunnerError(f"process RSS differs at sequence {sequence}")
    for label, value in (
        ("wall", result.wall_seconds),
        ("user CPU", result.user_cpu_seconds),
        ("system CPU", result.system_cpu_seconds),
    ):
        if type(value) not in (int, float) or not math.isfinite(float(value)) or value < 0:
            raise RunnerError(f"process {label} differs at sequence {sequence}")
    if result.wall_seconds <= 0:
        raise RunnerError(f"process wall receipt is non-positive at sequence {sequence}")
    return result


def _close_attempt_cwd(
    cwd: pathlib.Path,
    row: Mapping[str, Any],
    snapshot: Sequence[Mapping[str, Any]],
) -> None:
    expected: list[dict[str, Any]] = []
    if row["method"] != "orca":
        for item in snapshot:
            record = dict(item)
            record["path"] = INPUT_RELATIVE.joinpath(
                *pathlib.PurePosixPath(str(item["path"])).parts
            ).as_posix()
            expected.append(record)
    _require_snapshot(
        cwd,
        expected,
        f"owned cwd after sequence {row['sequence']}",
    )
    shutil.rmtree(cwd)
    if cwd.exists() or cwd.is_symlink():
        raise RunnerError(f"owned cwd could not be closed at sequence {row['sequence']}")


def _require_stage_inventory(
    stage: pathlib.Path,
    row: Mapping[str, Any],
    provenance_raw: bytes | None,
) -> None:
    allowed = {"stderr.txt", "stdout.txt", "summary.json", "trace.ndjson"}
    required = {"stderr.txt", "stdout.txt"}
    if row["method"] != "orca":
        allowed.add("provenance.json")
        required.add("provenance.json")
    entries = list(stage.iterdir())
    names = {entry.name for entry in entries}
    if len(names) != len(entries) or not required.issubset(names) or not names.issubset(allowed):
        raise RunnerError(f"attempt stage inventory differs at sequence {row['sequence']}")
    for entry in entries:
        if entry.is_symlink() or not entry.is_file():
            raise RunnerError(
                f"attempt stage contains a non-regular artifact at sequence {row['sequence']}"
            )
        limit = 1024 * 1024 * 1024
        if entry.name == "provenance.json":
            limit = 64 * 1024
        elif entry.name == "summary.json":
            limit = 64 * 1024 * 1024
        if entry.stat().st_size > limit:
            raise RunnerError(
                f"attempt artifact exceeds its byte bound at sequence {row['sequence']}"
            )
    if provenance_raw is not None and (stage / "provenance.json").read_bytes() != provenance_raw:
        raise RunnerError(f"DANCeRS provenance changed at sequence {row['sequence']}")


def _run_one(
    *,
    row: Mapping[str, Any],
    runtime: RuntimeBindings,
    runtime_record: Mapping[str, Any],
    input_root: pathlib.Path,
    snapshot: Sequence[Mapping[str, Any]],
    output_root: pathlib.Path,
    process_runner: ProcessRunner,
    analyzer_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    name = _attempt_name(row)
    attempts_root = output_root / "attempts"
    final = attempts_root / name
    stage = attempts_root / f".staging-{name}"
    if final.exists() or final.is_symlink() or stage.exists() or stage.is_symlink():
        raise RunnerError(f"attempt path already exists at sequence {row['sequence']}; retry is forbidden")
    stage.mkdir(parents=False, exist_ok=False)
    cwd = stage / "cwd"
    cwd.mkdir()
    isolated: pathlib.Path | None = None
    if row["method"] != "orca":
        isolated = cwd.joinpath(*INPUT_RELATIVE.parts)
        _copy_input_tree(input_root, isolated, snapshot)
    config = input_root / row["config_relpath"]
    scenario = input_root / row["scenario_relpath"]
    if sha256_file(config) != row["config_sha256"] or sha256_file(scenario) != row["scenario_sha256"]:
        raise RunnerError(f"row input identity differs before sequence {row['sequence']}")
    if analyzer_identity is not None and _repository_identity(
        "scripts/replication/analyze_orca_comparison.py"
    ) != dict(analyzer_identity):
        raise RunnerError(f"analyzer changed before sequence {row['sequence']}")
    role = "timing" if row["run_class"] == "timing" else "fidelity"
    binary_key, binary_path = _runtime_binary(runtime, str(row["method"]), role)
    before_binary = _require_binary(binary_path, binary_key)
    before_libomp = (
        _require_libomp(runtime.libomp) if row["method"] != "orca" else None
    )
    provenance_raw: bytes | None = None
    if row["method"] != "orca":
        provenance_raw = canonical_json_bytes(_metadata(row, before_binary["sha256"]))
        _write_exclusive(stage / "provenance.json", provenance_raw)
    argv, command_cwd, environment = derive_command(
        row, runtime, stage, input_root, before_binary
    )
    started_unix_ns = time.time_ns()
    result = _validate_child_result(process_runner(
        argv=argv,
        cwd=command_cwd,
        environment=environment,
        stdout_path=stage / "stdout.txt",
        stderr_path=stage / "stderr.txt",
        timeout_seconds=300.0,
    ), int(row["sequence"]))
    after_binary = _require_binary(binary_path, binary_key)
    if after_binary != before_binary:
        raise RunnerError(f"binary changed across sequence {row['sequence']}")
    if before_libomp is not None and _require_libomp(runtime.libomp) != before_libomp:
        raise RunnerError(f"libomp changed across sequence {row['sequence']}")
    if analyzer_identity is not None and _repository_identity(
        "scripts/replication/analyze_orca_comparison.py"
    ) != dict(analyzer_identity):
        raise RunnerError(f"analyzer changed across sequence {row['sequence']}")
    if sha256_file(config) != row["config_sha256"] or sha256_file(scenario) != row["scenario_sha256"]:
        raise RunnerError(f"row input identity changed across sequence {row['sequence']}")
    _require_snapshot(input_root, snapshot, f"frozen input after sequence {row['sequence']}")
    if isolated is not None:
        _require_snapshot(
            isolated,
            snapshot,
            f"isolated {row['method']} input after sequence {row['sequence']}",
        )
    _close_attempt_cwd(cwd, row, snapshot)
    _require_stage_inventory(stage, row, provenance_raw)
    validation_error: str | None = None
    if result.timed_out:
        outcome = "timeout"
        fixed_work_verified = False
    elif result.returncode != 0:
        outcome = "nonzero_exit"
        fixed_work_verified = False
    else:
        try:
            _validate_success_outputs(
                row,
                stage,
                input_root=input_root,
                output_root=output_root,
                binary_identity=before_binary,
            )
        except RunnerError as error:
            outcome = "artifact_failure"
            fixed_work_verified = False
            validation_error = str(error)
        else:
            outcome = "completed"
            fixed_work_verified = True
    if analyzer_identity is not None and _repository_identity(
        "scripts/replication/analyze_orca_comparison.py"
    ) != dict(analyzer_identity):
        raise RunnerError(f"analyzer changed while classifying sequence {row['sequence']}")
    unit, rss_mib = _rss_to_mib(result.peak_rss_raw)
    for metric in (result.wall_seconds, result.user_cpu_seconds, result.system_cpu_seconds):
        if not math.isfinite(metric) or metric < 0:
            raise RunnerError(f"non-finite process resource at sequence {row['sequence']}")
    record: dict[str, Any] = {
        "artifacts": _attempt_artifacts(
            stage, output_root, logical_attempt=final
        ),
        "binary": before_binary,
        "command": {
            "argv": argv,
            "cwd": str(command_cwd),
            "environment": environment,
            "shell": False,
        },
        "fixed_work_verified": fixed_work_verified,
        "measurement": {
            "peak_rss_mib": rss_mib,
            "peak_rss_raw": result.peak_rss_raw,
            "peak_rss_raw_unit": unit,
            "returncode": result.returncode,
            "signal": result.signal,
            "system_cpu_seconds": result.system_cpu_seconds,
            "timed_out": result.timed_out,
            "total_cpu_seconds": result.user_cpu_seconds + result.system_cpu_seconds,
            "user_cpu_seconds": result.user_cpu_seconds,
            "wall_seconds": result.wall_seconds,
        },
        "outcome": outcome,
        "runtime_binding": runtime_record,
        "schedule": dict(row),
        "schema_version": ATTEMPT_SCHEMA,
        "started_unix_ns": started_unix_ns,
        "study_id": STUDY_ID,
    }
    if validation_error is not None:
        record["validation_error"] = validation_error
    record_path = stage / "record.json"
    _write_exclusive(record_path, canonical_json_bytes(record))
    record["artifacts"] = _attempt_artifacts(
        stage, output_root, logical_attempt=final
    )
    # Include record.json itself only in the session-level identity to avoid a
    # recursive self-hash.  The record bytes are the authoritative attempt row.
    if canonical_json_bytes(record) != record_path.read_bytes():
        raise RunnerError("attempt record changed after create-last publication")
    if final.exists() or final.is_symlink():
        raise RunnerError(
            f"attempt final path appeared before sequence {row['sequence']} publication"
        )
    stage.rename(final)
    return record


def _validate_permit(
    permit: ExecutionPermit,
    *,
    output_root: pathlib.Path,
) -> None:
    if permit.execution_authorized is not True:
        raise RunnerError("external execution permit is not affirmative")
    if permit.study_id != STUDY_ID or permit.expected_processes != EXPECTED_PROCESSES:
        raise RunnerError("execution permit study/inventory differs")
    if permit.selective_retry_allowed is not False:
        raise RunnerError("execution permit attempts to authorize selective retry")
    if permit.input_manifest_sha256 != INPUT_MANIFEST_SHA256 or permit.schedule_sha256 != SCHEDULE_SHA256:
        raise RunnerError("execution permit input binding differs")
    if permit.output_root != output_root:
        raise RunnerError("execution permit output root differs")
    for label, digest in (
        ("plan", permit.plan_sha256),
        ("authorization", permit.authorization_manifest_sha256),
        ("admission", permit.admission_manifest_sha256),
    ):
        if not SHA256_RE.fullmatch(digest):
            raise RunnerError(f"execution permit {label} digest is invalid")


def _require_result_tree(
    output_root: pathlib.Path,
    attempts: Sequence[Mapping[str, Any]],
    records: Sequence[Mapping[str, Any]],
    *,
    root_payloads: Mapping[str, bytes],
    sealed: bool,
) -> None:
    if len(attempts) != EXPECTED_PROCESSES or len(records) != EXPECTED_PROCESSES:
        raise RunnerError("result-tree receipt count differs from the exact schedule")
    root_files = {"input_manifest.json", "plan.json", "records.ndjson", "schedule.json"}
    if sealed:
        root_files.add("session.json")
    if set(root_payloads) != root_files or any(
        not isinstance(raw, bytes) for raw in root_payloads.values()
    ):
        raise RunnerError("expected result-root payload inventory differs")
    root_entries = list(output_root.iterdir())
    if {entry.name for entry in root_entries} != root_files | {"attempts"}:
        raise RunnerError("result-root inventory differs from the exact contract")
    for entry in root_entries:
        if entry.name == "attempts":
            if entry.is_symlink() or not entry.is_dir():
                raise RunnerError("attempts root is unsafe")
        elif entry.is_symlink() or not entry.is_file():
            raise RunnerError(f"result-root artifact is unsafe: {entry.name}")
    for name, expected_raw in root_payloads.items():
        if (output_root / name).read_bytes() != expected_raw:
            raise RunnerError(f"result-root artifact bytes differ: {name}")
    attempts_root = output_root / "attempts"
    expected_directories = {_attempt_name(row) for row in attempts}
    actual_directories = list(attempts_root.iterdir())
    if (
        {entry.name for entry in actual_directories} != expected_directories
        or len(actual_directories) != EXPECTED_PROCESSES
    ):
        raise RunnerError("attempt-directory inventory differs from the exact schedule")
    for row, record in zip(attempts, records):
        directory = attempts_root / _attempt_name(row)
        if directory.is_symlink() or not directory.is_dir():
            raise RunnerError(f"attempt directory is unsafe at sequence {row['sequence']}")
        artifacts = record.get("artifacts")
        if not isinstance(artifacts, Mapping):
            raise RunnerError(f"attempt artifact record differs at sequence {row['sequence']}")
        expected_files = {"record.json", *artifacts.keys()}
        entries = list(directory.iterdir())
        if {entry.name for entry in entries} != expected_files:
            raise RunnerError(f"attempt file inventory differs at sequence {row['sequence']}")
        if any(entry.is_symlink() or not entry.is_file() for entry in entries):
            raise RunnerError(f"attempt contains an unsafe artifact at sequence {row['sequence']}")
        record_path = directory / "record.json"
        if record_path.read_bytes() != canonical_json_bytes(record):
            raise RunnerError(
                f"attempt record bytes differ at sequence {row['sequence']}"
            )
        for name, expected_identity in artifacts.items():
            if not isinstance(name, str) or not isinstance(expected_identity, Mapping):
                raise RunnerError(
                    f"attempt artifact identity differs at sequence {row['sequence']}"
                )
            actual_identity = file_identity(
                directory / name, relative_to=output_root
            )
            if actual_identity != dict(expected_identity):
                raise RunnerError(
                    f"attempt artifact identity differs at sequence {row['sequence']}: {name}"
                )


def execute_authorized(
    *,
    input_root: pathlib.Path,
    output_root: pathlib.Path,
    runtime: RuntimeBindings,
    permit: ExecutionPermit,
    process_runner: ProcessRunner = run_process,
) -> dict[str, Any]:
    """Execute all 363 rows once after a separate launcher authorizes the plan.

    This function has no resume, sequence-selection, or retry surface.  Natural
    child failure and timeout are terminal recorded outcomes and do not alter
    the remaining schedule.  Admission or integrity failures stop the session.
    """

    if not output_root.is_absolute() or output_root != output_root.resolve() or output_root.is_symlink():
        raise RunnerError("output root must be an absolute normalized non-symlink path")
    _validate_permit(permit, output_root=output_root)
    if process_runner is not run_process:
        raise RunnerError("authorized execution forbids a process-runner override")
    if output_root.exists():
        raise RunnerError("output root already exists; resume and retry are forbidden")
    manifest, attempts, snapshot = load_frozen_plan(input_root)
    if output_root == input_root or output_root.is_relative_to(input_root):
        raise RunnerError("output root must be disjoint from the frozen input root")
    plan = _plan_document_from_frozen(input_root, manifest, attempts)
    plan_raw = canonical_json_bytes(plan)
    if sha256_bytes(plan_raw) != permit.plan_sha256:
        raise RunnerError("execution permit plan digest differs")
    runtime_record = _validate_runtime(runtime, snapshot)
    protected_roots = (
        runtime.optimized_source_root,
        runtime.reference_source_root,
        runtime.orca_binary.parent,
        runtime.optimized_fidelity_binary.parent,
        runtime.optimized_timing_binary.parent,
        runtime.reference_fidelity_binary.parent,
        runtime.reference_timing_binary.parent,
        runtime.libomp.parent,
    )
    if any(
        output_root == protected or output_root.is_relative_to(protected)
        for protected in protected_roots
    ):
        raise RunnerError("output root overlaps a protected runtime root")
    if output_root.exists() or output_root.is_symlink():
        raise RunnerError("output root appeared during admission")
    _require_real_absolute(output_root.parent, "output parent", directory=True)
    output_root.mkdir(parents=False, exist_ok=False)
    (output_root / "attempts").mkdir()
    input_manifest_raw = (input_root / "manifest.json").read_bytes()
    schedule_raw = (input_root / "schedule.json").read_bytes()
    _write_exclusive(output_root / "input_manifest.json", input_manifest_raw)
    _write_exclusive(output_root / "schedule.json", schedule_raw)
    _write_exclusive(output_root / "plan.json", plan_raw)
    records: list[dict[str, Any]] = []
    for row in attempts:
        records.append(
            _run_one(
                row=row,
                runtime=runtime,
                runtime_record=runtime_record,
                input_root=input_root,
                snapshot=snapshot,
                output_root=output_root,
                process_runner=process_runner,
                analyzer_identity=plan["artifacts"]["analyzer"],
            )
        )
    records_raw = b"".join(canonical_json_bytes(record) for record in records)
    _write_exclusive(output_root / "records.ndjson", records_raw)
    root_payloads = {
        "input_manifest.json": input_manifest_raw,
        "plan.json": plan_raw,
        "records.ndjson": records_raw,
        "schedule.json": schedule_raw,
    }
    _require_result_tree(
        output_root,
        attempts,
        records,
        root_payloads=root_payloads,
        sealed=False,
    )
    attempt_records = [
        file_identity(
            output_root / "attempts" / _attempt_name(row) / "record.json",
            relative_to=output_root,
        )
        for row in attempts
    ]
    session = {
        "admission_manifest_sha256": permit.admission_manifest_sha256,
        "attempt_records": attempt_records,
        "authorization_manifest_sha256": permit.authorization_manifest_sha256,
        "completed_processes": len(records),
        "execution_complete": True,
        "expected_processes": EXPECTED_PROCESSES,
        "input_manifest": file_identity(output_root / "input_manifest.json", relative_to=output_root),
        "outcome_counts": dict(sorted(Counter(record["outcome"] for record in records).items())),
        "plan": file_identity(output_root / "plan.json", relative_to=output_root),
        "records": file_identity(output_root / "records.ndjson", relative_to=output_root),
        "runtime_binding": runtime_record,
        "schedule": file_identity(output_root / "schedule.json", relative_to=output_root),
        "schema_version": SESSION_SCHEMA,
        "study_id": STUDY_ID,
    }
    session_raw = canonical_json_bytes(session)
    _write_exclusive(output_root / "session.json", session_raw)
    _require_result_tree(
        output_root,
        attempts,
        records,
        root_payloads={**root_payloads, "session.json": session_raw},
        sealed=True,
    )
    return session


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="read-only frozen-input check")
    check.add_argument("--input-root", type=pathlib.Path, default=DEFAULT_INPUT_ROOT)
    plan = commands.add_parser("plan", help="print the non-authorizing exact plan")
    plan.add_argument("--input-root", type=pathlib.Path, default=DEFAULT_INPUT_ROOT)
    execute = commands.add_parser("execute", help="always refuses; authorization is external")
    execute.add_argument("--input-root", type=pathlib.Path, default=DEFAULT_INPUT_ROOT)
    execute.add_argument("--output-root", type=pathlib.Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "execute":
            # This branch must remain before any filesystem resolution,
            # validation, output creation, or process-spawn path.
            raise RunnerError(PUBLIC_EXECUTION_REFUSAL)
        value = (
            check_frozen_inputs(args.input_root)
            if args.command == "check"
            else plan_document(args.input_root)
        )
        sys.stdout.buffer.write(canonical_json_bytes(value))
        return 0
    except (RunnerError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"DANCeRS--ORCA runner failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
