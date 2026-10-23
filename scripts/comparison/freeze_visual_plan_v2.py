#!/usr/bin/env python3
"""Freeze the immutable recovery-v2 laptop visual-diagnostic plan.

This utility never launches a DANCeRS binary, never opens a replay, and never
writes beneath ``results``.  It authenticates the parent contract, the
committed recovery registry, the complete sealed v1 failure, the committed v1
plan and scenario bytes, and the recovery-v2 tools.  It then republishes the
same 25 scientific scenario byte strings beneath a disjoint v2 plan tree,
enumerates 250 fresh paired scenes and 500 epoch-qualified process identities,
and publishes the plan once without overwriting.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import math
import os
import stat
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence


PLAN_SCHEMA = "dancers-simulator-visual-plan-v2"
PLAN_ID = "visual-diagnostic-calibration-v2"
TRACK_ID = "simulator-demo-v1-kinematic-visual-diagnostic"
EXECUTION_EPOCH_ID = "simulator-demo-v1-visual-calibration-v2-recovery-1"
RUN_ID_PREFIX = EXECUTION_EPOCH_ID
CONFIG_CLI_PATH_POLICY = "repository-relative-no-whitespace"
FAILURE_POLICY = "seal-stop-new-epoch-no-resume"
RESULT_SCHEMA = "dancers-simulator-visual-diagnostic-result-v2"
FAILURE_SCHEMA = "dancers-simulator-visual-diagnostic-failure-v2"
OUTPUT_SCHEMAS = {
    "success": RESULT_SCHEMA,
    "failure": FAILURE_SCHEMA,
}
CONTRACT_COMMIT = "b8ecf36b98ca1ec415110946864a8bb7711afa4c"
CONTRACT_PATH = PurePosixPath(
    "docs/simulator/simulator_contract_v1.json"
)
CONTRACT_SHA256 = (
    "6e4a74331b447adb1112bf19748f24295af1ecca1b267e081bcf1becca4403ee"
)
RECOVERY_COMMIT = "f8fd239d07d640ec536b721738c1e50e00f98f8c"
RECOVERY_REGISTRY_PATH = PurePosixPath(
    "docs/simulator/visual_diagnostic_recovery_v2.json"
)
RECOVERY_REGISTRY_SHA256 = (
    "8f39e926f67469d805da9c52946c889a89e70c4aa26d7d937ca747e590252e96"
)
RECOVERY_ADDENDUM_PATH = PurePosixPath(
    "docs/simulator/VISUAL_DIAGNOSTIC_RECOVERY_V2.md"
)
RECOVERY_ADDENDUM_SHA256 = (
    "0177bc9d69ed5a11b1527dee1a9af10367b8386d1723ab943e1a6d62f81681d9"
)
CONTROLLER_COMMIT = "cd138f94e8918c215dcab1591fe0834beaad91be"
CONTROLLER_SHA256 = (
    "195c69c34d636edc4465f32abae2e902280a85beeb38357997f0b7b4549b3f79"
)
CONTROLLER_TEST_SHA256 = (
    "3c0ccead35f45d2f358831d44c4ccf7c95a2c28887399c17d471a409090b95a8"
)
V1_PLAN_COMMIT = "49fab2bb2bb69cd96cfece9ee5492a2938aa6ccd"
V1_PLAN_SHA256 = (
    "a33d18ee8f9212973fc9f68c284924bbc3b939cc50f04066f579501df6dad189"
)
V1_PLAN_DIRECTORY = PurePosixPath(
    "comparison/plans/simulator-demo/v1/"
    "visual-diagnostic-calibration-v1"
)
V1_PLAN_PATH = V1_PLAN_DIRECTORY / "plan.json"
V1_FAILURE_COMMIT = "ff4c5d306c08cea5205e6da02775785f0fc50e00"
V1_FAILURE_ROOT = PurePosixPath(
    "results/simulator-demo/v1/visual-diagnostic/calibration"
)
V1_FAILURE_SCENE = V1_FAILURE_ROOT / "open/n-8/seed-01"
SIMULATOR_RESULT_ROOT = PurePosixPath("results/simulator-demo")
DARGS_HEADER_PATH = PurePosixPath("thirdparty/DArgs.h")
DARGS_HEADER_SHA256 = (
    "6bc53ed9d0bcb5e0d7fc5406c100fc2d4ee53ead549665f820ec02bfaf37a5aa"
)
PLAN_DIRECTORY = PurePosixPath(
    "comparison/plans/simulator-demo/v1/"
    "visual-diagnostic-calibration-v2"
)
PLAN_PATH = PLAN_DIRECTORY / "plan.json"
ALLOWED_OUTPUT_ROOT = PurePosixPath(
    "results/simulator-demo/v1/visual-diagnostic/calibration-v2"
)
EXECUTION_LEASE_PATH = PurePosixPath(
    "results/simulator-demo/v1/visual-diagnostic/"
    ".calibration-v2-execution.lease"
)
FAMILIES = (
    "open",
    "perfect_maze",
    "fractal_low",
    "fractal_medium",
    "fractal_high",
)
COUNTS = (8, 16, 32, 64, 128)
SEEDS = tuple(range(1, 11))
METHOD_ORDER = ("reference", "optimized")
EXPECTED_SCENE_COUNT = len(FAMILIES) * len(COUNTS) * len(SEEDS)
EXPECTED_ATTEMPT_COUNT = EXPECTED_SCENE_COUNT * len(METHOD_ORDER)
REFERENCE_REVISION = "986f47deec5b079663c7d7f6120bfc20f0467901"
OPTIMIZED_REVISION = "99364a41f1535a4bb0eddbe5a10030127ea77086"
ARTIFACT_PATHS = {
    "build_manifest": PurePosixPath("comparison/build_manifest.json"),
    "environment_manifest": PurePosixPath(
        "comparison/environments/manifest.json"
    ),
    "pixi_lock": PurePosixPath("pixi.lock"),
    "config_template": PurePosixPath(
        "config/comparison/laptop_benchmark.yaml"
    ),
    "trace_compare": PurePosixPath(
        "scripts/comparison/trace_compare.py"
    ),
    "viewer": PurePosixPath(
        "scripts/comparison/build_replay_viewer.py"
    ),
    "controller": PurePosixPath(
        "scripts/comparison/run_visual_diagnostic_v2.py"
    ),
    "plan_freezer": PurePosixPath(
        "scripts/comparison/freeze_visual_plan_v2.py"
    ),
    "capacity_materializer": PurePosixPath(
        "scripts/comparison/materialize_capacity_scenario.py"
    ),
}
V1_ARTIFACT_PATHS = {
    **ARTIFACT_PATHS,
    "controller": PurePosixPath(
        "scripts/comparison/run_visual_diagnostic.py"
    ),
    "plan_freezer": PurePosixPath(
        "scripts/comparison/freeze_visual_plan.py"
    ),
}
CONTROLLER_TEST_PATH = PurePosixPath(
    "scripts/comparison/tests/test_run_visual_diagnostic_v2.py"
)
FREEZER_TEST_PATH = PurePosixPath(
    "scripts/comparison/tests/test_freeze_visual_plan_v2.py"
)
RECOVERY_INPUTS = (
    RECOVERY_REGISTRY_PATH,
    RECOVERY_ADDENDUM_PATH,
    V1_PLAN_PATH,
    DARGS_HEADER_PATH,
    CONTROLLER_TEST_PATH,
    FREEZER_TEST_PATH,
)
TRACKED_INPUTS = tuple(
    dict.fromkeys(
        (CONTRACT_PATH, *ARTIFACT_PATHS.values(), *RECOVERY_INPUTS)
    )
)
COMMON_SCIENCE_ARTIFACT_KEYS = frozenset(
    set(ARTIFACT_PATHS) - {"controller", "plan_freezer"}
)
FORBIDDEN_METRIC_KEYS = frozenset(
    {"wall_seconds", "cpu_seconds", "peak_rss_mib"}
)
BASE_EXECUTION = {
    "families": list(FAMILIES),
    "counts": list(COUNTS),
    "seeds": list(SEEDS),
    "timesteps": 100,
    "timestep_seconds": 0.1,
    "simulated_duration_seconds": 10,
    "trace_detail": "positions",
    "run_class": "positions-replay",
    "serial_execution": True,
    "fresh_process_per_method": True,
    "method_order": list(METHOD_ORDER),
    "timeout_seconds": 300,
    "termination_grace_seconds": 5,
    "omp_threads": 1,
}


class PlanFreezeError(RuntimeError):
    """An identity, registry, plan, or publication failure."""


class _DuplicateKey(ValueError):
    pass


@dataclass(frozen=True)
class PlanBundle:
    plan: dict[str, Any]
    plan_bytes: bytes
    scenarios: dict[PurePosixPath, bytes]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise _DuplicateKey(f"duplicate JSON key {key!r}")
        output[key] = value
    return output


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


def canonical_scenario_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            indent=2,
            sort_keys=True,
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _strict_json_bytes(data: bytes, label: str) -> Any:
    try:
        return json.loads(
            data.decode("utf-8"),
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
        raise PlanFreezeError(f"{label} is not strict JSON: {error}") from error


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PlanFreezeError(f"{label} must be an object")
    return value


def _exact_keys(
    value: Mapping[str, Any], expected: Iterable[str], label: str
) -> None:
    expected_set = set(expected)
    missing = expected_set - value.keys()
    unknown = value.keys() - expected_set
    if missing or unknown:
        raise PlanFreezeError(
            f"{label} keys differ; missing={sorted(missing)}, "
            f"unknown={sorted(unknown)}"
        )


def _resolve(root: Path, relative: PurePosixPath, label: str) -> Path:
    if (
        relative.is_absolute()
        or any(part in ("", ".", "..") for part in relative.parts)
    ):
        raise PlanFreezeError(f"{label} is not repository-relative")
    candidate = root.joinpath(*relative.parts)
    current = root
    for component in relative.parts:
        current = current / component
        if current.is_symlink():
            raise PlanFreezeError(
                f"{label} contains a symlink component: {current}"
            )
    resolved = candidate.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise PlanFreezeError(f"{label} escapes the repository") from error
    return resolved


def _read_regular(
    root: Path, relative: PurePosixPath, label: str
) -> tuple[Path, bytes]:
    path = _resolve(root, relative, label)
    flags = os.O_RDONLY
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise PlanFreezeError(
            f"cannot open {label} without following links: {error}"
        ) from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise PlanFreezeError(f"{label} must be a regular file")
        blocks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            blocks.append(block)
        after = os.fstat(descriptor)
        try:
            current = path.lstat()
        except OSError as error:
            raise PlanFreezeError(
                f"{label} disappeared while being read"
            ) from error
        identity_changed = (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or after.st_dev != current.st_dev
            or after.st_ino != current.st_ino
            or not stat.S_ISREG(current.st_mode)
        )
        data = b"".join(blocks)
        if identity_changed or len(data) != after.st_size:
            raise PlanFreezeError(f"{label} changed while being read")
        return path, data
    except OSError as error:
        raise PlanFreezeError(f"cannot read {label}: {error}") from error
    finally:
        os.close(descriptor)


def _artifact(root: Path, relative: PurePosixPath, label: str) -> dict[str, str]:
    _, data = _read_regular(root, relative, label)
    return {"path": relative.as_posix(), "sha256": sha256_bytes(data)}


def _safe_config_cli_argument(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise PlanFreezeError(f"{label} must be a non-empty string")
    if "\\" in value:
        raise PlanFreezeError(f"{label} must not contain backslashes")
    if any(
        character.isspace()
        or unicodedata.category(character) in {"Cc", "Cf"}
        for character in value
    ):
        raise PlanFreezeError(
            f"{label} must not contain whitespace or control characters"
        )
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise PlanFreezeError(f"{label} must be a normalized POSIX path")
    relative = PurePosixPath(value)
    if relative.is_absolute() or relative.as_posix() != value:
        raise PlanFreezeError(
            f"{label} must be a normalized repository-relative POSIX path"
        )
    return relative


def _git(
    root: Path, arguments: Sequence[str], *, binary: bool = False
) -> bytes | str:
    completed = subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=False,
        capture_output=True,
        text=not binary,
    )
    if completed.returncode != 0:
        stderr = completed.stderr
        detail = (
            stderr.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes)
            else stderr
        ).strip()
        raise PlanFreezeError(
            f"git {' '.join(arguments)} failed: {detail}"
        )
    return completed.stdout


def _committed_bytes(
    root: Path,
    commit: str,
    relative: PurePosixPath,
    label: str,
) -> bytes:
    value = _git(
        root,
        ("show", f"{commit}:{relative.as_posix()}"),
        binary=True,
    )
    if not isinstance(value, bytes):
        raise PlanFreezeError(f"cannot read committed {label}")
    return value


def _verify_pinned_working_blob(
    root: Path,
    *,
    commit: str,
    relative: PurePosixPath,
    sha256: str,
    label: str,
) -> bytes:
    _, working = _read_regular(root, relative, label)
    if sha256_bytes(working) != sha256:
        raise PlanFreezeError(f"{label} SHA-256 differs from its pin")
    committed = _committed_bytes(root, commit, relative, label)
    if committed != working or sha256_bytes(committed) != sha256:
        raise PlanFreezeError(
            f"working {label} differs from the registered commit"
        )
    return working


def _verify_commit_chain(root: Path) -> None:
    commits = (
        CONTRACT_COMMIT,
        V1_PLAN_COMMIT,
        V1_FAILURE_COMMIT,
        RECOVERY_COMMIT,
        CONTROLLER_COMMIT,
    )
    for commit in commits:
        resolved = str(
            _git(
                root,
                ("rev-parse", "--verify", f"{commit}^{{commit}}"),
            )
        ).strip()
        if resolved != commit:
            raise PlanFreezeError(
                f"registered commit does not resolve exactly: {commit}"
            )
    for ancestor, descendant in zip(commits, commits[1:]):
        _git(
            root,
            ("merge-base", "--is-ancestor", ancestor, descendant),
        )
    head = str(_git(root, ("rev-parse", "--verify", "HEAD^{commit}"))).strip()
    _git(
        root,
        ("merge-base", "--is-ancestor", CONTROLLER_COMMIT, head),
    )


def _verify_git_state(root: Path) -> None:
    remotes = str(_git(root, ("remote",))).splitlines()
    if any(line.strip() for line in remotes):
        raise PlanFreezeError("repository must have zero Git remotes")
    _verify_commit_chain(root)
    for relative in TRACKED_INPUTS:
        path_text = relative.as_posix()
        _git(root, ("ls-files", "--error-unmatch", "--", path_text))
        status = str(
            _git(
                root,
                ("status", "--porcelain", "--untracked-files=all", "--", path_text),
            )
        )
        if status:
            raise PlanFreezeError(
                f"tracked plan input is not clean: {path_text}"
            )
    _verify_pinned_working_blob(
        root,
        commit=CONTROLLER_COMMIT,
        relative=ARTIFACT_PATHS["controller"],
        sha256=CONTROLLER_SHA256,
        label="controller v2",
    )
    _verify_pinned_working_blob(
        root,
        commit=CONTROLLER_COMMIT,
        relative=CONTROLLER_TEST_PATH,
        sha256=CONTROLLER_TEST_SHA256,
        label="controller v2 tests",
    )


def _committed_contract(root: Path, working_bytes: bytes) -> None:
    committed = _committed_bytes(
        root, CONTRACT_COMMIT, CONTRACT_PATH, "simulator contract"
    )
    if (
        committed != working_bytes
        or sha256_bytes(committed) != CONTRACT_SHA256
    ):
        raise PlanFreezeError(
            "working contract bytes differ from the registered commit"
        )


def _verify_contract(root: Path) -> tuple[dict[str, Any], bytes]:
    _, data = _read_regular(root, CONTRACT_PATH, "simulator contract")
    value = _strict_json_bytes(data, "simulator contract")
    contract = _mapping(value, "simulator contract")
    if data != canonical_json_bytes(contract):
        raise PlanFreezeError("simulator contract is not canonical JSON")
    if sha256_bytes(data) != CONTRACT_SHA256:
        raise PlanFreezeError("simulator contract SHA-256 differs")
    if contract.get("contract_id") != "dancers-simulator-demo-v1":
        raise PlanFreezeError("simulator contract ID differs")
    status = _mapping(contract.get("status"), "contract.status")
    if (
        status.get("classification") != "prospective-execution-contract"
        or status.get("execution_authorized_by_this_checkpoint") is not False
        or status.get("result_root_state_at_freeze") != "absent"
    ):
        raise PlanFreezeError("simulator contract status differs")
    visual = _mapping(
        contract.get("visual_diagnostic"), "contract.visual_diagnostic"
    )
    expected_visual = {
        "track_id": TRACK_ID,
        "initial_counts": list(COUNTS),
        "initial_seeds": list(SEEDS),
        "paired_scene_count": EXPECTED_SCENE_COUNT,
        "fresh_process_attempt_count": EXPECTED_ATTEMPT_COUNT,
        "timesteps": 100,
        "timestep_seconds": 0.1,
        "simulated_duration_seconds": 10,
        "trace_detail": "positions",
        "trace_schema": "dancers-comparison-trace-v3",
        "rendering_in_compute_measurement": False,
    }
    for key, expected in expected_visual.items():
        if visual.get(key) != expected:
            raise PlanFreezeError(
                f"contract visual_diagnostic.{key} differs"
            )
    process = _mapping(
        visual.get("process_policy"),
        "contract.visual_diagnostic.process_policy",
    )
    if (
        process.get("serial_execution") is not True
        or process.get("fresh_process_per_method") is not True
        or process.get("viewer_active_during_solver") is not False
    ):
        raise PlanFreezeError("contract visual process policy differs")
    return contract, data


def _scan_regular_tree(
    root: Path, label: str
) -> tuple[dict[str, os.stat_result], dict[str, os.stat_result]]:
    directories: dict[str, os.stat_result] = {}
    files: dict[str, os.stat_result] = {}

    def visit(directory: Path, relative: PurePosixPath | None) -> None:
        try:
            metadata = directory.lstat()
        except OSError as error:
            raise PlanFreezeError(f"cannot inspect {label}: {error}") from error
        key = "." if relative is None else relative.as_posix()
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            raise PlanFreezeError(
                f"{label} contains a non-directory or symlink: {key}"
            )
        directories[key] = metadata
        try:
            entries = sorted(os.scandir(directory), key=lambda item: item.name)
        except OSError as error:
            raise PlanFreezeError(f"cannot enumerate {label}: {error}") from error
        for entry in entries:
            child_relative = (
                PurePosixPath(entry.name)
                if relative is None
                else relative / entry.name
            )
            child_key = child_relative.as_posix()
            try:
                child_metadata = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise PlanFreezeError(
                    f"cannot inspect {label} entry {child_key}: {error}"
                ) from error
            if stat.S_ISLNK(child_metadata.st_mode):
                raise PlanFreezeError(
                    f"{label} contains a symlink: {child_key}"
                )
            if stat.S_ISDIR(child_metadata.st_mode):
                visit(Path(entry.path), child_relative)
            elif stat.S_ISREG(child_metadata.st_mode):
                files[child_key] = child_metadata
            else:
                raise PlanFreezeError(
                    f"{label} contains a special file: {child_key}"
                )

    visit(root, None)
    return directories, files


def _committed_tree_paths(
    root: Path,
    commit: str,
    relative: PurePosixPath,
    label: str,
) -> set[str]:
    output = str(
        _git(
            root,
            (
                "ls-tree",
                "-r",
                "--name-only",
                commit,
                "--",
                relative.as_posix(),
            ),
        )
    )
    prefix = relative.as_posix() + "/"
    paths: set[str] = set()
    for line in output.splitlines():
        if not line.startswith(prefix):
            raise PlanFreezeError(f"committed {label} path escapes its root")
        paths.add(line[len(prefix) :])
    return paths


def _artifact_record(value: Any, label: str) -> dict[str, str]:
    record = _mapping(value, label)
    _exact_keys(record, {"path", "sha256"}, label)
    path = record.get("path")
    digest = record.get("sha256")
    relative = PurePosixPath(path) if isinstance(path, str) else None
    if (
        not isinstance(path, str)
        or relative is None
        or relative.is_absolute()
        or any(part in ("", ".", "..") for part in relative.parts)
        or relative.as_posix() != path
    ):
        raise PlanFreezeError(f"{label}.path is not canonical")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise PlanFreezeError(f"{label}.sha256 is invalid")
    return {"path": path, "sha256": digest}


def _validate_v1_plan(
    value: Any,
) -> tuple[dict[str, Any], dict[tuple[str, int], dict[str, str]]]:
    plan = _mapping(value, "v1 visual plan")
    _exact_keys(
        plan,
        {
            "schema_version",
            "plan_id",
            "track_id",
            "phase",
            "status",
            "diagnostic_only",
            "compute_claim",
            "timing_unlock",
            "paper_replication_evidence",
            "contract",
            "artifacts",
            "methods",
            "environment_descriptors",
            "execution",
            "scenes",
            "expected_scene_count",
            "expected_attempt_count",
            "allowed_output_root",
        },
        "v1 visual plan",
    )
    exact_scalars = {
        "schema_version": "dancers-simulator-visual-plan-v1",
        "plan_id": "visual-diagnostic-calibration-v1",
        "track_id": TRACK_ID,
        "phase": "calibration",
        "status": "frozen",
        "diagnostic_only": True,
        "compute_claim": False,
        "timing_unlock": False,
        "paper_replication_evidence": False,
        "expected_scene_count": EXPECTED_SCENE_COUNT,
        "expected_attempt_count": EXPECTED_ATTEMPT_COUNT,
        "allowed_output_root": V1_FAILURE_ROOT.as_posix(),
    }
    for key, expected in exact_scalars.items():
        if plan.get(key) != expected:
            raise PlanFreezeError(f"v1 visual plan {key} differs")
    if plan.get("contract") != {
        "commit": CONTRACT_COMMIT,
        "path": CONTRACT_PATH.as_posix(),
        "sha256": CONTRACT_SHA256,
    }:
        raise PlanFreezeError("v1 visual plan contract differs")
    artifacts = _mapping(plan.get("artifacts"), "v1 visual plan artifacts")
    _exact_keys(artifacts, V1_ARTIFACT_PATHS, "v1 visual plan artifacts")
    for key, expected_path in V1_ARTIFACT_PATHS.items():
        record = _artifact_record(
            artifacts[key], f"v1 visual plan artifacts.{key}"
        )
        if record["path"] != expected_path.as_posix():
            raise PlanFreezeError(
                f"v1 visual plan artifacts.{key}.path differs"
            )
    execution = _mapping(
        plan.get("execution"), "v1 visual plan execution"
    )
    _exact_keys(execution, BASE_EXECUTION, "v1 visual plan execution")
    if execution != BASE_EXECUTION:
        raise PlanFreezeError("v1 visual plan execution differs")
    methods = _mapping(plan.get("methods"), "v1 visual plan methods")
    _exact_keys(methods, METHOD_ORDER, "v1 visual plan methods")
    descriptors = _mapping(
        plan.get("environment_descriptors"),
        "v1 visual plan environment_descriptors",
    )
    _exact_keys(
        descriptors, FAMILIES, "v1 visual plan environment_descriptors"
    )
    scenes = plan.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != EXPECTED_SCENE_COUNT:
        raise PlanFreezeError("v1 visual plan does not contain 250 scenes")
    expected_conditions = [
        (family, count, seed)
        for family in FAMILIES
        for count in COUNTS
        for seed in SEEDS
    ]
    scenario_records: dict[tuple[str, int], dict[str, str]] = {}
    seen_ids: set[str] = set()
    seen_outputs: set[str] = set()
    for index, (scene_value, expected) in enumerate(
        zip(scenes, expected_conditions)
    ):
        scene = _mapping(scene_value, f"v1 visual plan scene {index}")
        _exact_keys(
            scene,
            {
                "scene_id",
                "family",
                "robot_count",
                "seed",
                "map",
                "scenario",
                "output_relpath",
            },
            f"v1 visual plan scene {index}",
        )
        family, count, seed = expected
        scene_id = f"{family}-n{count}-seed{seed:02d}"
        output = f"{family}/n-{count}/seed-{seed:02d}"
        if (
            scene.get("scene_id") != scene_id
            or scene.get("family") != family
            or scene.get("robot_count") != count
            or scene.get("seed") != seed
            or scene.get("output_relpath") != output
        ):
            raise PlanFreezeError(
                f"v1 visual plan scene {index} crossing differs"
            )
        if scene_id in seen_ids or output in seen_outputs:
            raise PlanFreezeError(
                "v1 visual plan scene identifiers or outputs repeat"
            )
        seen_ids.add(scene_id)
        seen_outputs.add(output)
        _artifact_record(scene.get("map"), f"v1 scene {index}.map")
        scenario = _artifact_record(
            scene.get("scenario"), f"v1 scene {index}.scenario"
        )
        expected_path = (
            V1_PLAN_DIRECTORY
            / "scenarios"
            / family
            / f"n-{count}.json"
        ).as_posix()
        if scenario["path"] != expected_path:
            raise PlanFreezeError(
                f"v1 visual plan scene {index} scenario path differs"
            )
        key = (family, count)
        previous = scenario_records.setdefault(key, scenario)
        if previous != scenario:
            raise PlanFreezeError(
                f"v1 visual plan scenario identity repeats inconsistently: {key}"
            )
    if len(scenario_records) != len(FAMILIES) * len(COUNTS):
        raise PlanFreezeError("v1 visual plan lacks 25 scenario identities")
    return plan, scenario_records


def _load_v1_plan(
    root: Path,
) -> tuple[
    dict[str, Any],
    bytes,
    dict[tuple[str, int], dict[str, str]],
    dict[tuple[str, int], bytes],
]:
    data = _verify_pinned_working_blob(
        root,
        commit=V1_PLAN_COMMIT,
        relative=V1_PLAN_PATH,
        sha256=V1_PLAN_SHA256,
        label="v1 visual plan",
    )
    value = _strict_json_bytes(data, "v1 visual plan")
    if data != canonical_json_bytes(value):
        raise PlanFreezeError("v1 visual plan is not canonical JSON")
    plan, scenario_records = _validate_v1_plan(value)
    plan_root = _resolve(root, V1_PLAN_DIRECTORY, "v1 visual plan root")
    directories, files = _scan_regular_tree(
        plan_root, "sealed v1 visual plan"
    )
    expected_directories = {
        ".",
        "scenarios",
        *(f"scenarios/{family}" for family in FAMILIES),
    }
    expected_files = {
        "plan.json",
        *(
            f"scenarios/{family}/n-{count}.json"
            for family in FAMILIES
            for count in COUNTS
        ),
    }
    if set(directories) != expected_directories:
        raise PlanFreezeError(
            "sealed v1 visual plan contains an extra or missing directory"
        )
    if set(files) != expected_files:
        raise PlanFreezeError(
            "sealed v1 visual plan contains an extra or missing file"
        )
    if _committed_tree_paths(
        root, V1_PLAN_COMMIT, V1_PLAN_DIRECTORY, "v1 visual plan"
    ) != expected_files:
        raise PlanFreezeError("committed v1 visual plan inventory differs")
    for key, metadata in directories.items():
        if stat.S_IMODE(metadata.st_mode) != 0o555:
            raise PlanFreezeError(
                f"sealed v1 visual plan directory mode differs: {key}"
            )
    for key, metadata in files.items():
        if stat.S_IMODE(metadata.st_mode) != 0o444:
            raise PlanFreezeError(
                f"sealed v1 visual plan file mode differs: {key}"
            )
    scenario_bytes: dict[tuple[str, int], bytes] = {}
    for key, record in scenario_records.items():
        family, count = key
        relative = PurePosixPath(record["path"])
        _, current = _read_regular(
            root, relative, f"v1 {family} N={count} scenario"
        )
        committed = _committed_bytes(
            root,
            V1_PLAN_COMMIT,
            relative,
            f"v1 {family} N={count} scenario",
        )
        if current != committed or sha256_bytes(current) != record["sha256"]:
            raise PlanFreezeError(
                f"v1 {family} N={count} scenario bytes differ"
            )
        scenario_value = _strict_json_bytes(
            current, f"v1 {family} N={count} scenario"
        )
        if current != canonical_scenario_bytes(scenario_value):
            raise PlanFreezeError(
                f"v1 {family} N={count} scenario is not canonical"
            )
        scenario_bytes[key] = current
    return plan, data, scenario_records, scenario_bytes


def _verify_sealed_failure(
    root: Path,
    registry: Mapping[str, Any],
    v1_plan_bytes: bytes,
) -> dict[str, Any]:
    failure = _mapping(
        registry.get("failure_evidence"),
        "recovery registry.failure_evidence",
    )
    expected_keys = {
        "commit",
        "completed_method_attempts",
        "completed_pairs",
        "files",
        "inventory",
        "optimized_process_started",
        "path",
        "processes_started",
        "reference_exit_code",
        "replay_published",
        "scientific_admission",
        "trace_or_summary_produced",
    }
    _exact_keys(
        failure, expected_keys, "recovery registry.failure_evidence"
    )
    exact = {
        "commit": V1_FAILURE_COMMIT,
        "completed_method_attempts": 0,
        "completed_pairs": 0,
        "optimized_process_started": False,
        "path": V1_FAILURE_SCENE.as_posix(),
        "processes_started": 1,
        "reference_exit_code": -6,
        "replay_published": False,
        "scientific_admission": "orchestration-defect-only",
        "trace_or_summary_produced": False,
    }
    for key, expected in exact.items():
        if failure.get(key) != expected:
            raise PlanFreezeError(f"registered v1 failure {key} differs")
    inventory = _mapping(
        failure.get("inventory"),
        "recovery registry.failure_evidence.inventory",
    )
    expected_inventory = {
        "directory_count": 2,
        "directory_mode": "0555",
        "file_count": 7,
        "file_mode": "0444",
        "special_file_count": 0,
        "symlink_count": 0,
    }
    _exact_keys(
        inventory,
        expected_inventory,
        "recovery registry.failure_evidence.inventory",
    )
    if inventory != expected_inventory:
        raise PlanFreezeError("registered v1 failure inventory differs")
    records = failure.get("files")
    if not isinstance(records, list) or len(records) != 7:
        raise PlanFreezeError(
            "registered v1 failure must contain exactly seven files"
        )
    registered: dict[str, tuple[str, int]] = {}
    for index, value in enumerate(records):
        label = f"recovery registry failure file {index}"
        record = _mapping(value, label)
        _exact_keys(record, {"path", "sha256", "bytes"}, label)
        path_value = record.get("path")
        if not isinstance(path_value, str):
            raise PlanFreezeError(f"{label}.path must be a string")
        relative = PurePosixPath(path_value)
        if (
            relative.is_absolute()
            or relative.as_posix() != path_value
            or any(part in ("", ".", "..") for part in relative.parts)
        ):
            raise PlanFreezeError(f"{label}.path is invalid")
        if path_value in registered:
            raise PlanFreezeError("registered v1 failure paths repeat")
        byte_count = record.get("bytes")
        if (
            not isinstance(byte_count, int)
            or isinstance(byte_count, bool)
            or byte_count < 0
        ):
            raise PlanFreezeError(f"{label}.bytes is invalid")
        artifact = _artifact_record(
            {"path": path_value, "sha256": record.get("sha256")},
            label,
        )
        registered[path_value] = (artifact["sha256"], byte_count)

    result_root = _resolve(
        root, SIMULATOR_RESULT_ROOT, "simulator result root"
    )
    directories, files = _scan_regular_tree(
        result_root, "sealed simulator result evidence"
    )
    scene_relative = V1_FAILURE_SCENE.relative_to(
        SIMULATOR_RESULT_ROOT
    ).as_posix()
    expected_directories = {
        ".",
        "v1",
        "v1/visual-diagnostic",
        "v1/visual-diagnostic/calibration",
        "v1/visual-diagnostic/calibration/open",
        "v1/visual-diagnostic/calibration/open/n-8",
        scene_relative,
        f"{scene_relative}/reference",
    }
    expected_files = {
        f"{scene_relative}/{relative}" for relative in registered
    }
    if set(directories) != expected_directories:
        raise PlanFreezeError(
            "sealed v1 failure contains an extra or missing destination"
        )
    if set(files) != expected_files:
        raise PlanFreezeError(
            "sealed v1 failure contains an extra or missing file"
        )
    committed_expected = {
        (V1_FAILURE_SCENE / PurePosixPath(relative))
        .relative_to(V1_FAILURE_ROOT)
        .as_posix()
        for relative in registered
    }
    if _committed_tree_paths(
        root, V1_FAILURE_COMMIT, V1_FAILURE_ROOT, "v1 failure"
    ) != committed_expected:
        raise PlanFreezeError("committed v1 failure inventory differs")
    for key in (scene_relative, f"{scene_relative}/reference"):
        if stat.S_IMODE(directories[key].st_mode) != 0o555:
            raise PlanFreezeError(
                f"sealed v1 failure directory mode differs: {key}"
            )
    evidence: dict[str, dict[str, Any]] = {}
    for relative, (digest, byte_count) in registered.items():
        tree_relative = f"{scene_relative}/{relative}"
        metadata = files[tree_relative]
        if stat.S_IMODE(metadata.st_mode) != 0o444:
            raise PlanFreezeError(
                f"sealed v1 failure file mode differs: {relative}"
            )
        full_relative = V1_FAILURE_SCENE / PurePosixPath(relative)
        _, current = _read_regular(
            root, full_relative, f"sealed v1 failure {relative}"
        )
        committed = _committed_bytes(
            root,
            V1_FAILURE_COMMIT,
            full_relative,
            f"sealed v1 failure {relative}",
        )
        if (
            len(current) != byte_count
            or metadata.st_size != byte_count
            or sha256_bytes(current) != digest
            or current != committed
        ):
            raise PlanFreezeError(
                f"sealed v1 failure bytes differ: {relative}"
            )
        evidence[relative] = {
            "path": relative,
            "sha256": digest,
            "bytes": byte_count,
        }
    frozen_plan = _read_regular(
        root,
        V1_FAILURE_SCENE / "frozen_plan.json",
        "sealed v1 frozen plan",
    )[1]
    if frozen_plan != v1_plan_bytes:
        raise PlanFreezeError(
            "sealed v1 frozen plan differs from the committed v1 plan"
        )
    return {
        "commit": V1_FAILURE_COMMIT,
        "path": V1_FAILURE_SCENE.as_posix(),
        "files": [evidence[key] for key in sorted(evidence)],
    }


def _verify_recovery_registry(
    root: Path,
    v1_plan_bytes: bytes,
) -> tuple[dict[str, Any], dict[str, Any]]:
    registry_bytes = _verify_pinned_working_blob(
        root,
        commit=RECOVERY_COMMIT,
        relative=RECOVERY_REGISTRY_PATH,
        sha256=RECOVERY_REGISTRY_SHA256,
        label="recovery registry",
    )
    _verify_pinned_working_blob(
        root,
        commit=RECOVERY_COMMIT,
        relative=RECOVERY_ADDENDUM_PATH,
        sha256=RECOVERY_ADDENDUM_SHA256,
        label="recovery addendum",
    )
    _verify_pinned_working_blob(
        root,
        commit=RECOVERY_COMMIT,
        relative=DARGS_HEADER_PATH,
        sha256=DARGS_HEADER_SHA256,
        label="pinned DArgs header",
    )
    _verify_pinned_working_blob(
        root,
        commit=CONTROLLER_COMMIT,
        relative=ARTIFACT_PATHS["controller"],
        sha256=CONTROLLER_SHA256,
        label="controller v2",
    )
    _verify_pinned_working_blob(
        root,
        commit=CONTROLLER_COMMIT,
        relative=CONTROLLER_TEST_PATH,
        sha256=CONTROLLER_TEST_SHA256,
        label="controller v2 tests",
    )
    value = _strict_json_bytes(registry_bytes, "recovery registry")
    registry = _mapping(value, "recovery registry")
    if registry_bytes != canonical_json_bytes(registry):
        raise PlanFreezeError("recovery registry is not canonical JSON")
    _exact_keys(
        registry,
        {
            "admission_policy",
            "cause",
            "contract",
            "failure_evidence",
            "frozen_date",
            "recovery",
            "schema_version",
            "status",
            "v1_plan",
        },
        "recovery registry",
    )
    if (
        registry.get("schema_version")
        != "dancers-simulator-visual-recovery-v2"
        or registry.get("status")
        != "frozen-before-v2-tools-plan-and-execution"
        or registry.get("frozen_date") != "2026-07-29"
    ):
        raise PlanFreezeError("recovery registry identity differs")
    contract = _mapping(
        registry.get("contract"), "recovery registry.contract"
    )
    expected_contract = {
        "commit": CONTRACT_COMMIT,
        "contract_id": "dancers-simulator-demo-v1",
        "path": CONTRACT_PATH.as_posix(),
        "sha256": CONTRACT_SHA256,
        "track_id": TRACK_ID,
    }
    _exact_keys(contract, expected_contract, "recovery registry.contract")
    if contract != expected_contract:
        raise PlanFreezeError("recovery registry contract differs")
    recovery = _mapping(
        registry.get("recovery"), "recovery registry.recovery"
    )
    expected_recovery = {
        "config_cli_path_policy": CONFIG_CLI_PATH_POLICY,
        "controller_path": ARTIFACT_PATHS["controller"].as_posix(),
        "execution_epoch_id": EXECUTION_EPOCH_ID,
        "failure_schema": FAILURE_SCHEMA,
        "plan_freezer_path": ARTIFACT_PATHS["plan_freezer"].as_posix(),
        "plan_id": PLAN_ID,
        "plan_path": PLAN_PATH.as_posix(),
        "plan_schema": PLAN_SCHEMA,
        "process_count": EXPECTED_ATTEMPT_COUNT,
        "result_root": ALLOWED_OUTPUT_ROOT.as_posix(),
        "result_schema": RESULT_SCHEMA,
        "run_id_prefix": RUN_ID_PREFIX,
        "scenario_artifact_count": len(FAMILIES) * len(COUNTS),
        "scene_count": EXPECTED_SCENE_COUNT,
    }
    _exact_keys(recovery, expected_recovery, "recovery registry.recovery")
    if recovery != expected_recovery:
        raise PlanFreezeError("recovery registry execution identity differs")
    admission = _mapping(
        registry.get("admission_policy"),
        "recovery registry.admission_policy",
    )
    expected_admission = {
        "pinned_scientific_input_authentication_required": True,
        "v1_execution_evidence_admitted_to_v2": False,
        "v1_execution_or_result_reuse_permitted": False,
        "v1_mutation_permitted": False,
        "v2_fresh_process_count": EXPECTED_ATTEMPT_COUNT,
        "v2_fresh_scene_count": EXPECTED_SCENE_COUNT,
    }
    _exact_keys(
        admission, expected_admission, "recovery registry.admission_policy"
    )
    if admission != expected_admission:
        raise PlanFreezeError("recovery registry admission policy differs")
    cause = _mapping(registry.get("cause"), "recovery registry.cause")
    dargs = _mapping(
        cause.get("dargs_header"), "recovery registry.cause.dargs_header"
    )
    if dargs != {
        "path": DARGS_HEADER_PATH.as_posix(),
        "sha256": DARGS_HEADER_SHA256,
    }:
        raise PlanFreezeError("recovery registry DArgs identity differs")
    if (
        cause.get("classification")
        != "orchestration-config-cli-path-truncation"
        or cause.get("only_affected_current_argument") != "--cfg"
        or cause.get("remediation") != CONFIG_CLI_PATH_POLICY
        or cause.get("source_or_binary_change_permitted") is not False
    ):
        raise PlanFreezeError("recovery registry cause differs")
    v1_record = _mapping(
        registry.get("v1_plan"), "recovery registry.v1_plan"
    )
    expected_v1 = {
        "commit": V1_PLAN_COMMIT,
        "path": V1_PLAN_PATH.as_posix(),
        "sha256": V1_PLAN_SHA256,
        "status": "aborted",
    }
    _exact_keys(v1_record, expected_v1, "recovery registry.v1_plan")
    if v1_record != expected_v1:
        raise PlanFreezeError("recovery registry v1 plan differs")
    failure = _verify_sealed_failure(root, registry, v1_plan_bytes)
    return registry, failure


def _verify_registered_artifacts(
    root: Path, contract: dict[str, Any]
) -> dict[str, dict[str, str]]:
    artifacts = {
        key: _artifact(root, relative, f"artifact {key}")
        for key, relative in ARTIFACT_PATHS.items()
    }
    identities = _mapping(contract.get("identities"), "contract.identities")
    expected_contract_hashes = {
        "build_manifest": artifacts["build_manifest"]["sha256"],
        "environment_manifest": artifacts["environment_manifest"]["sha256"],
    }
    for key, digest in expected_contract_hashes.items():
        record = _mapping(identities.get(key), f"contract.identities.{key}")
        if (
            record.get("path") != artifacts[key]["path"]
            or record.get("sha256") != digest
        ):
            raise PlanFreezeError(f"contract {key} identity differs")
    if identities.get("pixi_lock_sha256") != artifacts["pixi_lock"]["sha256"]:
        raise PlanFreezeError("contract Pixi lock identity differs")
    binaries = _mapping(
        identities.get("initial_visual_binaries"),
        "contract.identities.initial_visual_binaries",
    )
    if set(binaries) != {
        "reference_fidelity_sha256",
        "optimized_fidelity_sha256",
    }:
        raise PlanFreezeError("contract visual binary identity keys differ")
    return artifacts


def _build_methods(
    root: Path,
    build_manifest: dict[str, Any],
    contract: dict[str, Any],
) -> dict[str, Any]:
    if (
        build_manifest.get("schema_version")
        != "dancers-comparison-build-manifest-v1"
    ):
        raise PlanFreezeError("unsupported build manifest")
    repository = _mapping(
        build_manifest.get("repository"), "build manifest.repository"
    )
    if (
        repository.get("remote_count") != 0
        or repository.get("origin_present") is not False
        or repository.get("reference_pin") != REFERENCE_REVISION
        or repository.get("optimized_pin") != OPTIMIZED_REVISION
    ):
        raise PlanFreezeError("build manifest local source identity differs")
    builds = _mapping(build_manifest.get("builds"), "build manifest.builds")
    contract_binaries = _mapping(
        _mapping(contract.get("identities"), "contract.identities").get(
            "initial_visual_binaries"
        ),
        "contract initial visual binaries",
    )
    methods: dict[str, Any] = {}
    revisions = {
        "reference": REFERENCE_REVISION,
        "optimized": OPTIMIZED_REVISION,
    }
    for method_id in METHOD_ORDER:
        build_key = f"{method_id}_fidelity"
        build = _mapping(builds.get(build_key), f"builds.{build_key}")
        binary_relative = PurePosixPath(str(build.get("binary_path")))
        binary = _artifact(
            root, binary_relative, f"{method_id} fidelity binary"
        )
        expected_digest = contract_binaries[
            f"{method_id}_fidelity_sha256"
        ]
        if (
            build.get("observer_compiled") is not True
            or build.get("algorithm_revision") != revisions[method_id]
            or build.get("binary_sha256") != binary["sha256"]
            or binary["sha256"] != expected_digest
        ):
            raise PlanFreezeError(f"{method_id} fidelity build differs")
        binary_path = _resolve(
            root, binary_relative, f"{method_id} fidelity binary"
        )
        if not os.access(binary_path, os.X_OK):
            raise PlanFreezeError(
                f"{method_id} fidelity binary is not executable"
            )
        methods[method_id] = {
            "build_manifest_key": build_key,
            "binary": binary,
            "source_revision": revisions[method_id],
        }
    if (
        methods["reference"]["binary"]["path"]
        == methods["optimized"]["binary"]["path"]
    ):
        raise PlanFreezeError("reference and optimized binaries are identical")
    return methods


def _environment_cases(
    root: Path, environment_manifest: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    if (
        environment_manifest.get("schema_version") != 1
        or environment_manifest.get("artifact_hash") != "SHA-256"
        or environment_manifest.get("families") != list(FAMILIES)
    ):
        raise PlanFreezeError("environment manifest identity differs")
    values = environment_manifest.get("environments")
    if not isinstance(values, list):
        raise PlanFreezeError("environment manifest cases are not an array")
    cases: dict[str, dict[str, Any]] = {}
    for index, value in enumerate(values):
        case = _mapping(value, f"environment case {index}")
        if case.get("split") != "calibration":
            continue
        family = case.get("family")
        if family not in FAMILIES or family in cases:
            raise PlanFreezeError(
                "calibration environment families are not unique"
            )
        cases[family] = case
    if tuple(cases) != FAMILIES:
        raise PlanFreezeError(
            "calibration environment family order differs"
        )
    return cases


def _environment_descriptor(
    case: dict[str, Any], family: str
) -> dict[str, Any]:
    runtime = _mapping(
        case.get("effective_runtime_1000"),
        f"{family}.effective_runtime_1000",
    )
    metrics = _mapping(
        runtime.get("map_metrics"),
        f"{family}.effective_runtime_1000.map_metrics",
    )
    box = _mapping(
        metrics.get("boundary_box_counting"),
        f"{family}.boundary_box_counting",
    )
    free = _mapping(metrics.get("free_space"), f"{family}.free_space")
    descriptor = {
        "obstacle_occupancy": metrics.get("obstacle_occupancy"),
        "boundary_density": metrics.get("boundary_density"),
        "boundary_box_counting": {
            "dimension": box.get("dimension"),
            "r_squared": box.get("r_squared"),
        },
        "free_space": {
            "component_count_4_connected": free.get(
                "component_count_4_connected"
            ),
            "reachable_fraction": free.get("reachable_fraction"),
        },
    }
    numbers = (
        ("obstacle_occupancy", descriptor["obstacle_occupancy"]),
        ("boundary_density", descriptor["boundary_density"]),
        (
            "boundary_box_counting.dimension",
            descriptor["boundary_box_counting"]["dimension"],
        ),
        (
            "boundary_box_counting.r_squared",
            descriptor["boundary_box_counting"]["r_squared"],
        ),
        (
            "free_space.reachable_fraction",
            descriptor["free_space"]["reachable_fraction"],
        ),
    )
    for name, value in numbers:
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not math.isfinite(float(value))
        ):
            raise PlanFreezeError(
                f"{family} descriptor {name} is not finite"
            )
    for name, value in (
        ("obstacle_occupancy", descriptor["obstacle_occupancy"]),
        ("boundary_density", descriptor["boundary_density"]),
        (
            "boundary_box_counting.r_squared",
            descriptor["boundary_box_counting"]["r_squared"],
        ),
        (
            "free_space.reachable_fraction",
            descriptor["free_space"]["reachable_fraction"],
        ),
    ):
        if not 0.0 <= float(value) <= 1.0:
            raise PlanFreezeError(
                f"{family} descriptor {name} is outside [0, 1]"
            )
    if float(descriptor["boundary_box_counting"]["dimension"]) <= 0.0:
        raise PlanFreezeError(
            f"{family} boundary box-counting descriptor is not positive"
        )
    component_count = descriptor["free_space"][
        "component_count_4_connected"
    ]
    if (
        not isinstance(component_count, int)
        or isinstance(component_count, bool)
        or component_count < 0
    ):
        raise PlanFreezeError(
            f"{family} connected-component count is invalid"
        )
    return descriptor


def _registered_file(
    root: Path,
    base: PurePosixPath,
    record: Any,
    label: str,
) -> tuple[PurePosixPath, bytes]:
    item = _mapping(record, label)
    relative_value = item.get("path")
    if not isinstance(relative_value, str):
        raise PlanFreezeError(f"{label}.path must be a string")
    relative = base / PurePosixPath(relative_value)
    _, data = _read_regular(root, relative, label)
    if (
        item.get("bytes") != len(data)
        or item.get("sha256") != sha256_bytes(data)
    ):
        raise PlanFreezeError(f"{label} hash or byte count differs")
    return relative, data


def _scenario_prefix(
    bank: dict[str, Any], family: str, count: int
) -> dict[str, Any]:
    metadata = _mapping(bank.get("metadata"), f"{family} capacity metadata")
    bank_count = metadata.get("capacity_bank_robot_count")
    if (
        not isinstance(bank_count, int)
        or isinstance(bank_count, bool)
        or count > bank_count
    ):
        raise PlanFreezeError(f"{family} capacity bank is too small")
    expected_ids = [str(index) for index in range(count)]
    decisions = _mapping(bank.get("decisions"), f"{family} bank decisions")
    waypoints = _mapping(bank.get("waypoints"), f"{family} bank waypoints")
    try:
        prefix = {
            "decisions": {
                robot_id: decisions[robot_id] for robot_id in expected_ids
            },
            "metadata": {**metadata, "prefix_robot_count": count},
            "num_robots": count,
            "seed_robots": [
                robot_id
                for robot_id in bank.get("seed_robots", [])
                if int(robot_id) < count
            ],
            "waypoints": {
                robot_id: waypoints[robot_id] for robot_id in expected_ids
            },
        }
    except (KeyError, TypeError, ValueError) as error:
        raise PlanFreezeError(
            f"{family} capacity bank cannot form N={count}"
        ) from error
    if (
        set(prefix["decisions"]) != set(expected_ids)
        or set(prefix["waypoints"]) != set(expected_ids)
    ):
        raise PlanFreezeError(f"{family} N={count} robot keys differ")
    return prefix


def _family_materials(
    root: Path,
    family: str,
    case: dict[str, Any],
    v1_scenario_records: Mapping[tuple[str, int], Mapping[str, str]],
    v1_scenario_bytes: Mapping[tuple[str, int], bytes],
) -> tuple[dict[str, str], dict[int, tuple[PurePosixPath, bytes]]]:
    map_relative, map_data = _registered_file(
        root,
        PurePosixPath("comparison/environments"),
        case.get("map"),
        f"{family} calibration map",
    )
    capacity = _mapping(case.get("capacity"), f"{family} capacity")
    bank_relative, bank_data = _registered_file(
        root,
        PurePosixPath("comparison/environments"),
        capacity.get("bank"),
        f"{family} capacity bank",
    )
    del bank_relative
    bank_value = _strict_json_bytes(bank_data, f"{family} capacity bank")
    bank = _mapping(bank_value, f"{family} capacity bank")
    metadata = _mapping(bank.get("metadata"), f"{family} capacity metadata")
    if (
        metadata.get("environment_id") != family
        or metadata.get("split") != "calibration"
        or metadata.get("map_sha256") != sha256_bytes(map_data)
        or metadata.get("capacity_bank_id") != capacity.get("bank_id")
    ):
        raise PlanFreezeError(f"{family} capacity bank metadata differs")
    prefixes = capacity.get("prefix_scenarios")
    if not isinstance(prefixes, list):
        raise PlanFreezeError(f"{family} prefix registry is not an array")
    registered = {
        item.get("robot_count"): item
        for item in prefixes
        if isinstance(item, dict) and item.get("robot_count") in COUNTS
    }
    if set(registered) != set(COUNTS):
        raise PlanFreezeError(
            f"{family} lacks all five registered exact-N prefixes"
        )
    scenario_materials: dict[int, tuple[PurePosixPath, bytes]] = {}
    for count in COUNTS:
        scenario = _scenario_prefix(bank, family, count)
        regenerated_data = canonical_scenario_bytes(scenario)
        record = _mapping(
            registered[count], f"{family} N={count} prefix registry"
        )
        if (
            record.get("bytes") != len(regenerated_data)
            or record.get("sha256") != sha256_bytes(regenerated_data)
        ):
            raise PlanFreezeError(
                f"{family} N={count} derived scenario differs"
            )
        v1_record = v1_scenario_records[(family, count)]
        data = v1_scenario_bytes[(family, count)]
        if (
            data != regenerated_data
            or sha256_bytes(data) != v1_record["sha256"]
        ):
            raise PlanFreezeError(
                f"{family} N={count} regenerated science differs from v1"
            )
        scenario_relative = (
            PLAN_DIRECTORY / "scenarios" / family / f"n-{count}.json"
        )
        scenario_materials[count] = (scenario_relative, data)
    return (
        {"path": map_relative.as_posix(), "sha256": sha256_bytes(map_data)},
        scenario_materials,
    )


def _reject_forbidden_metric_keys(value: Any, label: str = "visual plan") -> None:
    if isinstance(value, dict):
        forbidden = set(value) & FORBIDDEN_METRIC_KEYS
        if forbidden:
            raise PlanFreezeError(
                f"{label} contains forbidden compute metrics: "
                f"{sorted(forbidden)}"
            )
        for key, child in value.items():
            _reject_forbidden_metric_keys(child, f"{label}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_metric_keys(child, f"{label}[{index}]")


def validate_plan(plan: Any) -> dict[str, Any]:
    record = _mapping(plan, "visual plan")
    _exact_keys(
        record,
        {
            "schema_version",
            "plan_id",
            "track_id",
            "phase",
            "status",
            "diagnostic_only",
            "compute_claim",
            "timing_unlock",
            "paper_replication_evidence",
            "execution_epoch_id",
            "run_id_prefix",
            "config_cli_path_policy",
            "output_schemas",
            "recovery",
            "contract",
            "artifacts",
            "methods",
            "environment_descriptors",
            "execution",
            "scenes",
            "expected_scene_count",
            "expected_attempt_count",
            "allowed_output_root",
        },
        "visual plan",
    )
    expected_scalars = {
        "schema_version": PLAN_SCHEMA,
        "plan_id": PLAN_ID,
        "track_id": TRACK_ID,
        "phase": "calibration",
        "status": "frozen",
        "diagnostic_only": True,
        "compute_claim": False,
        "timing_unlock": False,
        "paper_replication_evidence": False,
        "execution_epoch_id": EXECUTION_EPOCH_ID,
        "run_id_prefix": RUN_ID_PREFIX,
        "config_cli_path_policy": CONFIG_CLI_PATH_POLICY,
        "expected_scene_count": EXPECTED_SCENE_COUNT,
        "expected_attempt_count": EXPECTED_ATTEMPT_COUNT,
        "allowed_output_root": ALLOWED_OUTPUT_ROOT.as_posix(),
    }
    for key, expected in expected_scalars.items():
        if record.get(key) != expected:
            raise PlanFreezeError(f"visual plan {key} differs")
    if record.get("output_schemas") != OUTPUT_SCHEMAS:
        raise PlanFreezeError("visual plan output_schemas differ")
    recovery = _mapping(record.get("recovery"), "visual plan recovery")
    expected_recovery = {
        "commit": RECOVERY_COMMIT,
        "registry": {
            "path": RECOVERY_REGISTRY_PATH.as_posix(),
            "sha256": RECOVERY_REGISTRY_SHA256,
        },
        "addendum": {
            "path": RECOVERY_ADDENDUM_PATH.as_posix(),
            "sha256": RECOVERY_ADDENDUM_SHA256,
        },
    }
    _exact_keys(recovery, expected_recovery, "visual plan recovery")
    if recovery != expected_recovery:
        raise PlanFreezeError("visual plan recovery binding differs")
    expected_contract = {
        "commit": CONTRACT_COMMIT,
        "path": CONTRACT_PATH.as_posix(),
        "sha256": CONTRACT_SHA256,
    }
    if record.get("contract") != expected_contract:
        raise PlanFreezeError("visual plan contract differs")
    artifacts = _mapping(record.get("artifacts"), "visual plan artifacts")
    _exact_keys(artifacts, ARTIFACT_PATHS, "visual plan artifacts")
    for key, expected_path in ARTIFACT_PATHS.items():
        artifact = _artifact_record(
            artifacts[key], f"visual plan artifacts.{key}"
        )
        if artifact["path"] != expected_path.as_posix():
            raise PlanFreezeError(
                f"visual plan artifacts.{key}.path differs"
            )
    methods = _mapping(record.get("methods"), "visual plan methods")
    _exact_keys(methods, METHOD_ORDER, "visual plan methods")
    for method_id in METHOD_ORDER:
        method = _mapping(methods[method_id], f"methods.{method_id}")
        _exact_keys(
            method,
            {"build_manifest_key", "binary", "source_revision"},
            f"methods.{method_id}",
        )
        expected_revision = (
            REFERENCE_REVISION
            if method_id == "reference"
            else OPTIMIZED_REVISION
        )
        if (
            method.get("build_manifest_key") != f"{method_id}_fidelity"
            or method.get("source_revision") != expected_revision
        ):
            raise PlanFreezeError(f"methods.{method_id} identity differs")
        _artifact_record(method.get("binary"), f"methods.{method_id}.binary")
    descriptors = _mapping(
        record.get("environment_descriptors"),
        "visual plan environment_descriptors",
    )
    _exact_keys(
        descriptors, FAMILIES, "visual plan environment_descriptors"
    )
    for family in FAMILIES:
        descriptor = _mapping(
            descriptors[family], f"environment_descriptors.{family}"
        )
        _exact_keys(
            descriptor,
            {
                "obstacle_occupancy",
                "boundary_density",
                "boundary_box_counting",
                "free_space",
            },
            f"environment_descriptors.{family}",
        )
        box = _mapping(
            descriptor.get("boundary_box_counting"),
            f"environment_descriptors.{family}.boundary_box_counting",
        )
        free = _mapping(
            descriptor.get("free_space"),
            f"environment_descriptors.{family}.free_space",
        )
        _exact_keys(
            box,
            {"dimension", "r_squared"},
            f"environment_descriptors.{family}.boundary_box_counting",
        )
        _exact_keys(
            free,
            {"component_count_4_connected", "reachable_fraction"},
            f"environment_descriptors.{family}.free_space",
        )
    execution = _mapping(record.get("execution"), "visual plan execution")
    expected_execution = {**BASE_EXECUTION, "failure_policy": FAILURE_POLICY}
    _exact_keys(execution, expected_execution, "visual plan execution")
    if execution != expected_execution:
        raise PlanFreezeError("visual plan execution differs")
    scenes = record.get("scenes")
    if not isinstance(scenes, list) or len(scenes) != EXPECTED_SCENE_COUNT:
        raise PlanFreezeError("visual plan does not contain 250 scenes")
    expected_conditions = [
        (family, count, seed)
        for family in FAMILIES
        for count in COUNTS
        for seed in SEEDS
    ]
    seen_ids: set[str] = set()
    seen_outputs: set[str] = set()
    seen_config_arguments: set[str] = set()
    seen_run_ids: set[str] = set()
    for index, (scene, expected) in enumerate(
        zip(scenes, expected_conditions)
    ):
        item = _mapping(scene, f"scene {index}")
        _exact_keys(
            item,
            {
                "scene_id",
                "family",
                "robot_count",
                "seed",
                "map",
                "scenario",
                "output_relpath",
                "config_cli_argument",
                "run_ids",
            },
            f"scene {index}",
        )
        family, count, seed = expected
        scene_id = f"{family}-n{count}-seed{seed:02d}"
        output = f"{family}/n-{count}/seed-{seed:02d}"
        if (
            item.get("scene_id") != scene_id
            or item.get("family") != family
            or item.get("robot_count") != count
            or item.get("seed") != seed
            or item.get("output_relpath") != output
        ):
            raise PlanFreezeError(f"scene {index} crossing differs")
        if scene_id in seen_ids or output in seen_outputs:
            raise PlanFreezeError("scene identifiers or outputs repeat")
        seen_ids.add(scene_id)
        seen_outputs.add(output)
        _artifact_record(item.get("map"), f"scene {index}.map")
        scenario = _artifact_record(
            item.get("scenario"), f"scene {index}.scenario"
        )
        expected_scenario = (
            PLAN_DIRECTORY / "scenarios" / family / f"n-{count}.json"
        ).as_posix()
        if scenario["path"] != expected_scenario:
            raise PlanFreezeError(f"scene {index} scenario path differs")
        config_argument = item.get("config_cli_argument")
        relative_config = _safe_config_cli_argument(
            config_argument, f"scene {index}.config_cli_argument"
        )
        expected_config = (
            ALLOWED_OUTPUT_ROOT / PurePosixPath(output) / "config.yaml"
        )
        if relative_config != expected_config:
            raise PlanFreezeError(
                f"scene {index} config_cli_argument differs"
            )
        if relative_config.as_posix() in seen_config_arguments:
            raise PlanFreezeError("scene config CLI arguments repeat")
        seen_config_arguments.add(relative_config.as_posix())
        run_ids = _mapping(item.get("run_ids"), f"scene {index}.run_ids")
        _exact_keys(run_ids, METHOD_ORDER, f"scene {index}.run_ids")
        for method_id in METHOD_ORDER:
            expected_run_id = (
                f"{RUN_ID_PREFIX}__{scene_id}__{method_id}"
            )
            if run_ids.get(method_id) != expected_run_id:
                raise PlanFreezeError(
                    f"scene {index} run_ids.{method_id} differs"
                )
            if expected_run_id in seen_run_ids:
                raise PlanFreezeError("scene run IDs repeat")
            seen_run_ids.add(expected_run_id)
    if len(seen_config_arguments) != EXPECTED_SCENE_COUNT:
        raise PlanFreezeError("visual plan lacks 250 unique config arguments")
    if len(seen_run_ids) != EXPECTED_ATTEMPT_COUNT:
        raise PlanFreezeError("visual plan lacks 500 unique run IDs")
    _reject_forbidden_metric_keys(record)
    return record


def _verify_scientific_projection(
    v1_plan: Mapping[str, Any],
    v2_plan: Mapping[str, Any],
) -> None:
    for key in (
        "track_id",
        "phase",
        "diagnostic_only",
        "compute_claim",
        "timing_unlock",
        "paper_replication_evidence",
        "contract",
        "methods",
        "environment_descriptors",
        "expected_scene_count",
        "expected_attempt_count",
    ):
        if v2_plan.get(key) != v1_plan.get(key):
            raise PlanFreezeError(
                f"v2 scientific projection differs from v1 at {key}"
            )
    v1_artifacts = _mapping(
        v1_plan.get("artifacts"), "v1 plan artifacts projection"
    )
    v2_artifacts = _mapping(
        v2_plan.get("artifacts"), "v2 plan artifacts projection"
    )
    for key in COMMON_SCIENCE_ARTIFACT_KEYS:
        if v2_artifacts.get(key) != v1_artifacts.get(key):
            raise PlanFreezeError(
                f"v2 common artifact differs from v1: {key}"
            )
    v2_execution = dict(
        _mapping(v2_plan.get("execution"), "v2 execution projection")
    )
    if v2_execution.pop("failure_policy", None) != FAILURE_POLICY:
        raise PlanFreezeError("v2 failure policy differs")
    if v2_execution != v1_plan.get("execution"):
        raise PlanFreezeError("v2 scientific execution differs from v1")
    v1_scenes = v1_plan.get("scenes")
    v2_scenes = v2_plan.get("scenes")
    if not isinstance(v1_scenes, list) or not isinstance(v2_scenes, list):
        raise PlanFreezeError("scientific scene projection is not an array")
    if len(v1_scenes) != len(v2_scenes):
        raise PlanFreezeError("scientific scene projection length differs")
    common_scene_fields = (
        "scene_id",
        "family",
        "robot_count",
        "seed",
        "map",
        "output_relpath",
    )
    for index, (v1_scene, v2_scene) in enumerate(zip(v1_scenes, v2_scenes)):
        left = _mapping(v1_scene, f"v1 projection scene {index}")
        right = _mapping(v2_scene, f"v2 projection scene {index}")
        for key in common_scene_fields:
            if right.get(key) != left.get(key):
                raise PlanFreezeError(
                    f"v2 scientific scene {index}.{key} differs from v1"
                )
        v1_scenario = _artifact_record(
            left.get("scenario"), f"v1 projection scene {index}.scenario"
        )
        v2_scenario = _artifact_record(
            right.get("scenario"), f"v2 projection scene {index}.scenario"
        )
        expected_v2_path = (
            PLAN_DIRECTORY
            / PurePosixPath(v1_scenario["path"]).relative_to(
                V1_PLAN_DIRECTORY
            )
        ).as_posix()
        if (
            v2_scenario["sha256"] != v1_scenario["sha256"]
            or v2_scenario["path"] != expected_v2_path
        ):
            raise PlanFreezeError(
                f"v2 scientific scenario {index} differs from v1"
            )


def build_bundle(
    root: Path, *, require_git_clean: bool = True
) -> PlanBundle:
    root = root.resolve()
    if require_git_clean:
        _verify_git_state(root)
    contract, contract_data = _verify_contract(root)
    _committed_contract(root, contract_data)
    (
        v1_plan,
        v1_plan_bytes,
        v1_scenario_records,
        v1_scenario_bytes,
    ) = _load_v1_plan(root)
    _verify_recovery_registry(root, v1_plan_bytes)
    artifacts = _verify_registered_artifacts(root, contract)
    build_value = _strict_json_bytes(
        _read_regular(
            root, ARTIFACT_PATHS["build_manifest"], "build manifest"
        )[1],
        "build manifest",
    )
    build_manifest = _mapping(build_value, "build manifest")
    methods = _build_methods(root, build_manifest, contract)
    environment_value = _strict_json_bytes(
        _read_regular(
            root,
            ARTIFACT_PATHS["environment_manifest"],
            "environment manifest",
        )[1],
        "environment manifest",
    )
    environment_manifest = _mapping(
        environment_value, "environment manifest"
    )
    cases = _environment_cases(root, environment_manifest)

    scenarios: dict[PurePosixPath, bytes] = {}
    family_maps: dict[str, dict[str, str]] = {}
    environment_descriptors = {
        family: _environment_descriptor(cases[family], family)
        for family in FAMILIES
    }
    family_scenarios: dict[str, dict[int, tuple[PurePosixPath, bytes]]] = {}
    for family in FAMILIES:
        map_record, materials = _family_materials(
            root,
            family,
            cases[family],
            v1_scenario_records,
            v1_scenario_bytes,
        )
        family_maps[family] = map_record
        family_scenarios[family] = materials
        for relative, data in materials.values():
            if relative in scenarios and scenarios[relative] != data:
                raise PlanFreezeError("scenario destination collision")
            scenarios[relative] = data

    scenes: list[dict[str, Any]] = []
    for family in FAMILIES:
        for count in COUNTS:
            scenario_relative, scenario_data = family_scenarios[family][count]
            for seed in SEEDS:
                scene_id = f"{family}-n{count}-seed{seed:02d}"
                output = f"{family}/n-{count}/seed-{seed:02d}"
                scenes.append(
                    {
                        "scene_id": scene_id,
                        "family": family,
                        "robot_count": count,
                        "seed": seed,
                        "map": family_maps[family],
                        "scenario": {
                            "path": scenario_relative.as_posix(),
                            "sha256": sha256_bytes(scenario_data),
                        },
                        "output_relpath": output,
                        "config_cli_argument": (
                            ALLOWED_OUTPUT_ROOT
                            / PurePosixPath(output)
                            / "config.yaml"
                        ).as_posix(),
                        "run_ids": {
                            method_id: (
                                f"{RUN_ID_PREFIX}__{scene_id}__{method_id}"
                            )
                            for method_id in METHOD_ORDER
                        },
                    }
                )

    plan = {
        "schema_version": PLAN_SCHEMA,
        "plan_id": PLAN_ID,
        "track_id": TRACK_ID,
        "phase": "calibration",
        "status": "frozen",
        "diagnostic_only": True,
        "compute_claim": False,
        "timing_unlock": False,
        "paper_replication_evidence": False,
        "execution_epoch_id": EXECUTION_EPOCH_ID,
        "run_id_prefix": RUN_ID_PREFIX,
        "config_cli_path_policy": CONFIG_CLI_PATH_POLICY,
        "output_schemas": dict(OUTPUT_SCHEMAS),
        "recovery": {
            "commit": RECOVERY_COMMIT,
            "registry": {
                "path": RECOVERY_REGISTRY_PATH.as_posix(),
                "sha256": RECOVERY_REGISTRY_SHA256,
            },
            "addendum": {
                "path": RECOVERY_ADDENDUM_PATH.as_posix(),
                "sha256": RECOVERY_ADDENDUM_SHA256,
            },
        },
        "contract": {
            "commit": CONTRACT_COMMIT,
            "path": CONTRACT_PATH.as_posix(),
            "sha256": CONTRACT_SHA256,
        },
        "artifacts": artifacts,
        "methods": methods,
        "environment_descriptors": environment_descriptors,
        "execution": {**BASE_EXECUTION, "failure_policy": FAILURE_POLICY},
        "scenes": scenes,
        "expected_scene_count": EXPECTED_SCENE_COUNT,
        "expected_attempt_count": EXPECTED_ATTEMPT_COUNT,
        "allowed_output_root": ALLOWED_OUTPUT_ROOT.as_posix(),
    }
    validate_plan(plan)
    _verify_scientific_projection(v1_plan, plan)
    return PlanBundle(
        plan=plan,
        plan_bytes=canonical_json_bytes(plan),
        scenarios=scenarios,
    )


def _directory_open_flags() -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    return flags


def _open_directory_at(parent_fd: int, name: str, label: str) -> int:
    try:
        descriptor = os.open(
            name, _directory_open_flags(), dir_fd=parent_fd
        )
    except OSError as error:
        raise PlanFreezeError(
            f"cannot open {label} as a non-symlink directory: {error}"
        ) from error
    metadata = os.fstat(descriptor)
    if not stat.S_ISDIR(metadata.st_mode):
        os.close(descriptor)
        raise PlanFreezeError(f"{label} is not a directory")
    return descriptor


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _assert_descriptor_binding(
    parent_fd: int,
    name: str,
    child_fd: int,
    label: str,
    *,
    require_directory: bool,
) -> None:
    try:
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        opened = os.fstat(child_fd)
    except OSError as error:
        raise PlanFreezeError(
            f"{label} binding is unavailable: {error}"
        ) from error
    expected_type = (
        stat.S_ISDIR(named.st_mode)
        if require_directory
        else stat.S_ISREG(named.st_mode)
    )
    if not expected_type or not _same_inode(named, opened):
        raise PlanFreezeError(f"{label} binding changed during publication")


def _read_descriptor_bytes(descriptor: int, label: str) -> bytes:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        blocks: list[bytes] = []
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            blocks.append(block)
        return b"".join(blocks)
    except OSError as error:
        raise PlanFreezeError(f"cannot reread {label}: {error}") from error


def _write_exclusive_at(
    parent_fd: int,
    name: str,
    data: bytes,
    label: str,
) -> int:
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=parent_fd)
    except OSError as error:
        if error.errno == errno.EEXIST:
            raise FileExistsError(
                f"refusing to overwrite existing output: {label}"
            ) from error
        raise PlanFreezeError(f"cannot create {label}: {error}") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise PlanFreezeError(f"{label} is not a regular file")
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise PlanFreezeError(f"short write while publishing {label}")
            offset += written
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o444)
        return descriptor
    except BaseException:
        try:
            _assert_descriptor_binding(
                parent_fd,
                name,
                descriptor,
                f"failed file {label}",
                require_directory=False,
            )
            os.unlink(name, dir_fd=parent_fd)
        except (OSError, PlanFreezeError):
            pass
        os.close(descriptor)
        raise


def _cleanup_publication_at(
    parent_fd: int,
    destination_name: str,
    destination_fd: int | None,
    directory_fds: Mapping[tuple[str, ...], int],
    file_fds: Mapping[tuple[str, ...], int],
) -> None:
    for descriptor in directory_fds.values():
        try:
            os.fchmod(descriptor, 0o755)
        except OSError:
            pass
    for key in sorted(file_fds, key=len, reverse=True):
        descriptor = file_fds[key]
        parent = directory_fds.get(key[:-1])
        if parent is None:
            continue
        try:
            _assert_descriptor_binding(
                parent,
                key[-1],
                descriptor,
                "failed publication file",
                require_directory=False,
            )
            os.unlink(key[-1], dir_fd=parent)
        except (OSError, PlanFreezeError):
            pass
    for key in sorted(
        (value for value in directory_fds if value),
        key=len,
        reverse=True,
    ):
        parent = directory_fds.get(key[:-1])
        descriptor = directory_fds[key]
        if parent is None:
            continue
        try:
            _assert_descriptor_binding(
                parent,
                key[-1],
                descriptor,
                "failed publication directory",
                require_directory=True,
            )
            os.rmdir(key[-1], dir_fd=parent)
        except (OSError, PlanFreezeError):
            pass
    if destination_fd is not None:
        try:
            _assert_descriptor_binding(
                parent_fd,
                destination_name,
                destination_fd,
                "failed publication root",
                require_directory=True,
            )
            os.rmdir(destination_name, dir_fd=parent_fd)
        except (OSError, PlanFreezeError):
            pass


def publish_bundle(bundle: PlanBundle, root: Path, destination: Path) -> Path:
    root = root.resolve()
    destination = Path(os.path.abspath(destination))
    try:
        relative_destination = destination.relative_to(root)
    except ValueError as error:
        raise PlanFreezeError("plan destination escapes repository") from error
    if not relative_destination.parts:
        raise PlanFreezeError("plan destination must be below repository root")

    all_directory_fds: list[int] = []
    file_fds: dict[tuple[str, ...], int] = {}
    tree_directory_fds: dict[tuple[str, ...], int] = {}
    path_bindings: list[tuple[int, str, int, str]] = []
    destination_fd: int | None = None
    destination_created = False
    parent_fd = -1
    root_fd = -1
    destination_name = relative_destination.parts[-1]
    try:
        root_fd = os.open(str(root), _directory_open_flags())
        all_directory_fds.append(root_fd)
        root_named = root.lstat()
        root_opened = os.fstat(root_fd)
        if (
            not stat.S_ISDIR(root_named.st_mode)
            or not _same_inode(root_named, root_opened)
        ):
            raise PlanFreezeError(
                "repository root binding changed during publication"
            )
        parent_fd = root_fd
        for index, component in enumerate(
            relative_destination.parts[:-1]
        ):
            label = "plan destination parent " + "/".join(
                relative_destination.parts[: index + 1]
            )
            try:
                child_fd = _open_directory_at(parent_fd, component, label)
            except PlanFreezeError as error:
                cause = error.__cause__
                if not isinstance(cause, OSError) or cause.errno != errno.ENOENT:
                    raise
                try:
                    os.mkdir(component, 0o755, dir_fd=parent_fd)
                except OSError as mkdir_error:
                    raise PlanFreezeError(
                        f"cannot create {label}: {mkdir_error}"
                    ) from mkdir_error
                child_fd = _open_directory_at(parent_fd, component, label)
            all_directory_fds.append(child_fd)
            path_bindings.append((parent_fd, component, child_fd, label))
            parent_fd = child_fd

        try:
            os.mkdir(destination_name, 0o755, dir_fd=parent_fd)
        except OSError as error:
            if error.errno == errno.EEXIST:
                raise FileExistsError(
                    "refusing to overwrite existing plan directory: "
                    f"{destination}"
                ) from error
            raise PlanFreezeError(
                f"cannot create plan destination: {error}"
            ) from error
        destination_created = True
        destination_fd = _open_directory_at(
            parent_fd, destination_name, "plan destination"
        )
        all_directory_fds.append(destination_fd)
        path_bindings.append(
            (
                parent_fd,
                destination_name,
                destination_fd,
                "plan destination",
            )
        )
        tree_directory_fds[()] = destination_fd

        for key in (
            ("scenarios",),
            *(("scenarios", family) for family in FAMILIES),
        ):
            directory_parent = tree_directory_fds[key[:-1]]
            try:
                os.mkdir(key[-1], 0o755, dir_fd=directory_parent)
            except OSError as error:
                raise PlanFreezeError(
                    f"cannot create plan directory {'/'.join(key)}: {error}"
                ) from error
            descriptor = _open_directory_at(
                directory_parent,
                key[-1],
                f"plan directory {'/'.join(key)}",
            )
            all_directory_fds.append(descriptor)
            tree_directory_fds[key] = descriptor
            path_bindings.append(
                (
                    directory_parent,
                    key[-1],
                    descriptor,
                    f"plan directory {'/'.join(key)}",
                )
            )

        file_bytes: dict[tuple[str, ...], bytes] = {
            ("plan.json",): bundle.plan_bytes
        }
        for relative, data in bundle.scenarios.items():
            within_plan = relative.relative_to(PLAN_DIRECTORY)
            key = tuple(within_plan.parts)
            if key in file_bytes:
                raise PlanFreezeError("scenario publication collision")
            file_bytes[key] = data
        expected_file_keys = {
            ("plan.json",),
            *(
                ("scenarios", family, f"n-{count}.json")
                for family in FAMILIES
                for count in COUNTS
            ),
        }
        if set(file_bytes) != expected_file_keys:
            raise PlanFreezeError("plan publication file set differs")
        for key in sorted(file_bytes):
            directory_parent = tree_directory_fds.get(key[:-1])
            if directory_parent is None:
                raise PlanFreezeError(
                    f"publication parent is not registered: {'/'.join(key)}"
                )
            descriptor = _write_exclusive_at(
                directory_parent,
                key[-1],
                file_bytes[key],
                "/".join(key),
            )
            file_fds[key] = descriptor

        for key in sorted(tree_directory_fds, key=len, reverse=True):
            descriptor = tree_directory_fds[key]
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o555)

        expected_children: dict[tuple[str, ...], set[str]] = {
            (): {"plan.json", "scenarios"},
            ("scenarios",): set(FAMILIES),
            **{
                ("scenarios", family): {
                    f"n-{count}.json" for count in COUNTS
                }
                for family in FAMILIES
            },
        }
        for key, descriptor in tree_directory_fds.items():
            observed = set(os.listdir(descriptor))
            if observed != expected_children[key]:
                raise PlanFreezeError(
                    f"published plan directory inventory differs: "
                    f"{'/'.join(key) or '.'}"
                )
            if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o555:
                raise PlanFreezeError(
                    f"published plan directory mode differs: "
                    f"{'/'.join(key) or '.'}"
                )
        for key, descriptor in file_fds.items():
            parent = tree_directory_fds[key[:-1]]
            _assert_descriptor_binding(
                parent,
                key[-1],
                descriptor,
                f"published file {'/'.join(key)}",
                require_directory=False,
            )
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_IMODE(metadata.st_mode) != 0o444
                or metadata.st_size != len(file_bytes[key])
                or _read_descriptor_bytes(
                    descriptor, f"published file {'/'.join(key)}"
                )
                != file_bytes[key]
            ):
                raise PlanFreezeError(
                    f"published file bytes or mode differ: {'/'.join(key)}"
                )
        for binding in path_bindings:
            _assert_descriptor_binding(
                binding[0],
                binding[1],
                binding[2],
                binding[3],
                require_directory=True,
            )
        root_named = root.lstat()
        root_opened = os.fstat(root_fd)
        if (
            not stat.S_ISDIR(root_named.st_mode)
            or not _same_inode(root_named, root_opened)
        ):
            raise PlanFreezeError(
                "repository root binding changed during publication"
            )
    except BaseException:
        if destination_created and parent_fd >= 0:
            _cleanup_publication_at(
                parent_fd,
                destination_name,
                destination_fd,
                tree_directory_fds,
                file_fds,
            )
        raise
    finally:
        for descriptor in file_fds.values():
            try:
                os.close(descriptor)
            except OSError:
                pass
        for descriptor in reversed(all_directory_fds):
            try:
                os.close(descriptor)
            except OSError:
                pass
    return destination / "plan.json"


def _require_future_paths_absent(root: Path) -> None:
    root = root.resolve()
    for relative, label in (
        (PLAN_DIRECTORY, "v2 visual plan root"),
        (ALLOWED_OUTPUT_ROOT, "v2 visual result root"),
        (EXECUTION_LEASE_PATH, "v2 execution lease"),
    ):
        path = _resolve(root, relative, label)
        if path.exists() or path.is_symlink():
            raise PlanFreezeError(f"{label} must remain absent before freeze")


def check_registered_plan(root: Path) -> PlanBundle:
    root = root.resolve()
    _require_future_paths_absent(root)
    return build_bundle(root, require_git_clean=True)


def freeze_registered_plan(root: Path) -> Path:
    root = root.resolve()
    bundle = check_registered_plan(root)
    _require_future_paths_absent(root)
    destination = _resolve(root, PLAN_DIRECTORY, "visual plan directory")
    published = publish_bundle(bundle, root, destination)
    if published.read_bytes() != bundle.plan_bytes:
        raise PlanFreezeError("published visual plan bytes changed")
    return published


def default_repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="verify and construct in memory without publishing",
    )
    args = parser.parse_args(argv)
    root = default_repository_root()
    try:
        if args.check_only:
            bundle = check_registered_plan(root)
            print(
                f"verified {len(bundle.plan['scenes'])} scenes and "
                f"{len(bundle.scenarios)} exact-N scenarios; no files written"
            )
            return 0
        output = freeze_registered_plan(root)
    except (
        FileExistsError,
        OSError,
        PlanFreezeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        print(f"visual plan freeze failed: {error}", file=sys.stderr)
        return 1
    print(
        f"wrote {output.relative_to(root)} "
        f"(sha256={sha256_file(output)}, "
        f"{EXPECTED_SCENE_COUNT} scenes, {EXPECTED_ATTEMPT_COUNT} processes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
