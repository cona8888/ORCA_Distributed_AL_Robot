#!/usr/bin/env python3
"""Freeze one deterministic, runner-compatible DANCeRS comparison plan.

The freezer is setup-only.  It reads registered manifests and source/build
identities, materializes byte-registered exact-N scenario prefixes, and writes
one immutable JSON plan.  It never launches a simulator and never reads result
directories or validation outcomes.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    # The registered materializer currently uses a direct sibling import.
    sys.path.insert(0, str(SCRIPT_DIR))

from materialize_capacity_scenario import materialize_registered_prefix
from freeze_capacity_decision import (
    AUDIT_SEEDS as DECISION_AUDIT_SEEDS,
    DecisionError,
    VALIDATION_SEEDS as DECISION_VALIDATION_SEEDS,
    load_capacity_decision,
)


PLAN_SCHEMA = "dancers-laptop-comparison-plan-v1"
BUILD_MANIFEST_SCHEMA = "dancers-comparison-build-manifest-v1"
REFERENCE_PIN = "986f47deec5b079663c7d7f6120bfc20f0467901"
OPTIMIZED_PIN = "99364a41f1535a4bb0eddbe5a10030127ea77086"
MINIMUM_FREE_DISK_BYTES = 21_474_836_480
MAXIMUM_PEAK_RSS_MIB = 49_152.0
POWER_MODE = "AC Power"
TASK_VALIDITY_PREDICATE_ID = "fixed-horizon-safe-navigation-v1"
TIMESTEP_SECONDS = 0.1
IDLE_COOLDOWN_SECONDS = 300
THERMAL_POLICY = (
    "ProcessInfo.thermalState rawValue must be 0 before every block; "
    "fail closed on probe error/change; five-minute idle cooldown before "
    "a measured session"
)
PHASE_SEEDS = {
    "calibration": tuple(range(1, 11)),
    "validation": tuple(range(11, 31)),
    "audit": tuple(range(31, 51)),
}
PHASE_SPLIT = {
    "calibration": "calibration",
    "validation": "validation",
    # The independent audit reuses the frozen held-out geometry, never its
    # outcomes.  The runner labels attempts by phase while registry matching
    # uses this underlying map/scenario split.
    "audit": "validation",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
TIMESTEP_CONFIG_RE = re.compile(
    r"(?m)^[ \t]*TIMESTEP[ \t]*:[ \t]*"
    r"([0-9]+(?:\.[0-9]+)?)[ \t]*(?:#.*)?$"
)


class FreezeError(RuntimeError):
    """The proposed frozen plan is invalid or cannot be created safely."""


class _DuplicateKey(ValueError):
    pass


@dataclass(frozen=True)
class FrozenPlan:
    """Paths and identities created by :func:`freeze_plan`."""

    plan_path: Path
    plan_sha256: str
    scenario_paths: tuple[Path, ...]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
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


def _regular_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if path.is_symlink() or resolved.is_symlink():
        raise FreezeError(f"{label} must not be a symbolic link: {path}")
    if not resolved.is_file():
        raise FreezeError(f"{label} is not a regular file: {resolved}")
    return resolved


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    resolved = _regular_file(path, label)
    data = resolved.read_bytes()
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
        raise FreezeError(f"{label} is not strict JSON: {error}") from error
    if not isinstance(value, dict):
        raise FreezeError(f"{label} must contain one JSON object")
    return value, data


def _artifact(path: Path, label: str) -> dict[str, str]:
    resolved = _regular_file(path, label)
    return {"path": str(resolved), "sha256": sha256_file(resolved)}


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise FreezeError(
            f"{label} must be 64 lower-case hexadecimal digits"
        )
    return value


def _git_object(value: Any, label: str) -> str:
    if not isinstance(value, str) or GIT_OBJECT_RE.fullmatch(value) is None:
        raise FreezeError(f"{label} must be a full Git object identifier")
    return value


def _positive_int(value: Any, label: str, maximum: int) -> int:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < 1
        or value > maximum
    ):
        raise FreezeError(f"{label} must be an integer in [1, {maximum}]")
    return value


def _positive_float(value: Any, label: str, maximum: float) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not 0 < float(value) <= maximum
    ):
        raise FreezeError(f"{label} must be in (0, {maximum}]")
    return float(value)


def _bounded_text(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 240
        or "\n" in value
        or "\r" in value
    ):
        raise FreezeError(
            f"{label} must be a non-empty string of at most 240 characters"
        )
    return value


def _strictly_increasing(
    values: Sequence[int], label: str, maximum: int
) -> tuple[int, ...]:
    checked = tuple(
        _positive_int(value, f"{label}[{index}]", maximum)
        for index, value in enumerate(values)
    )
    if not checked:
        raise FreezeError(f"{label} must not be empty")
    if tuple(sorted(set(checked))) != checked:
        raise FreezeError(f"{label} must be strictly increasing and unique")
    return checked


def _verified_timestep_seconds(config_template: Path) -> float:
    try:
        text = config_template.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise FreezeError("config template is not UTF-8") from error
    matches = TIMESTEP_CONFIG_RE.findall(text)
    if len(matches) != 1:
        raise FreezeError(
            "config template must contain exactly one scalar TIMESTEP"
        )
    timestep = _positive_float(
        float(matches[0]), "config template TIMESTEP", 86_400.0
    )
    if timestep != TIMESTEP_SECONDS:
        raise FreezeError(
            f"config template TIMESTEP must equal {TIMESTEP_SECONDS}"
        )
    return timestep


def _calibration_design(
    experiment_manifest: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    design = experiment_manifest.get("calibration_design")
    if not isinstance(design, dict):
        raise FreezeError("experiment manifest has no calibration design")
    if (
        design.get("status") != "frozen-before-calibration"
        or design.get("full_grid_execution") is not True
        or design.get("timeout_seconds") != 300.0
        or design.get("idle_cooldown_seconds") != IDLE_COOLDOWN_SECONDS
    ):
        raise FreezeError("experiment calibration design is not frozen")
    environments = design.get("environments")
    if not isinstance(environments, dict):
        raise FreezeError("calibration design has no environment grids")
    manifest_families = {
        "open",
        "perfect_maze",
        "fractal_low",
        "fractal_medium",
        "fractal_high",
    }
    if set(environments) != manifest_families:
        raise FreezeError(
            "calibration design must register exactly five environments"
        )
    for family, raw_record in environments.items():
        if not isinstance(raw_record, dict):
            raise FreezeError(f"calibration design {family} is malformed")
        grid = _strictly_increasing(
            raw_record.get("grid", ()),
            f"calibration design {family}.grid",
            100_000,
        )
        if raw_record.get("reference_anchor_n") != grid[0]:
            raise FreezeError(
                f"calibration design {family} anchor is not grid[0]"
            )
        if raw_record.get("timing_counts") != list(grid):
            raise FreezeError(
                f"calibration design {family} timing counts changed"
            )
        if raw_record.get("fidelity_counts") != list(grid):
            raise FreezeError(
                f"calibration design {family} fidelity counts changed"
            )
    return design, environments


def parse_integer_set(specification: str) -> tuple[int, ...]:
    """Parse ``1,3-5`` into an ordered, duplicate-free integer tuple."""

    if not isinstance(specification, str) or not specification.strip():
        raise argparse.ArgumentTypeError("integer set must not be empty")
    values: list[int] = []
    for raw_token in specification.split(","):
        token = raw_token.strip()
        if not token:
            raise argparse.ArgumentTypeError("integer set contains an empty item")
        if "-" in token:
            parts = token.split("-")
            if (
                len(parts) != 2
                or not parts[0].isdigit()
                or not parts[1].isdigit()
            ):
                raise argparse.ArgumentTypeError(
                    f"invalid inclusive integer range {token!r}"
                )
            start, stop = (int(item) for item in parts)
            if start > stop:
                raise argparse.ArgumentTypeError(
                    f"descending range is not allowed: {token!r}"
                )
            values.extend(range(start, stop + 1))
        elif token.isdigit():
            values.append(int(token))
        else:
            raise argparse.ArgumentTypeError(f"invalid integer {token!r}")
    if tuple(sorted(set(values))) != tuple(values):
        raise argparse.ArgumentTypeError(
            "integers must be strictly increasing and unique"
        )
    return tuple(values)


def _git(source_dir: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(source_dir), *arguments],
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise FreezeError(
            f"git {' '.join(arguments)} failed in {source_dir}: {detail}"
        )
    return completed.stdout.strip()


def _inside(root: Path, path_value: Any, label: str) -> Path:
    if not isinstance(path_value, str) or not path_value:
        raise FreezeError(f"{label} must be a non-empty path string")
    candidate = (root / path_value).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise FreezeError(f"{label} escapes its registered root") from error
    return candidate


def _registered_source_directory(
    repository_root: Path,
    path_value: Any,
    label: str,
) -> Path:
    """Resolve one manifest-owned, repository-relative source worktree."""

    if not isinstance(path_value, str) or not path_value:
        raise FreezeError(
            f"{label} must be a non-empty repository-relative path string"
        )
    relative = Path(path_value)
    if relative.is_absolute() or ".." in relative.parts:
        raise FreezeError(
            f"{label} must be a repository-relative path without '..'"
        )

    source_dir = _inside(repository_root, path_value, label)
    candidate = repository_root.resolve()
    for part in relative.parts:
        candidate /= part
        if candidate.is_symlink():
            raise FreezeError(
                f"{label} must not contain a symbolic link: {candidate}"
            )
    if not source_dir.is_dir():
        raise FreezeError(f"{label} is missing: {source_dir}")
    return source_dir


def _verified_environment_manifest(
    environment_root: Path,
) -> tuple[dict[str, Any], str]:
    root = environment_root.expanduser().resolve()
    manifest_path = root / "manifest.json"
    sidecar_path = _regular_file(root / "manifest.sha256", "manifest sidecar")
    manifest, data = _load_json(manifest_path, "environment manifest")
    sidecar_parts = sidecar_path.read_text(encoding="ascii").split()
    if not sidecar_parts:
        raise FreezeError("environment manifest sidecar is empty")
    expected = _sha256(sidecar_parts[0], "environment manifest sidecar hash")
    actual = sha256_bytes(data)
    if actual != expected:
        raise FreezeError(
            f"environment manifest SHA-256 mismatch: {actual} != {expected}"
        )
    capacity = manifest.get("capacity_suite")
    if not isinstance(capacity, dict):
        raise FreezeError("environment manifest has no capacity suite")
    if capacity.get("algorithm_execution_during_generation") is not False:
        raise FreezeError("capacity suite is not registered as setup-only")
    if capacity.get("validation_outcomes_generated_or_inspected") is not False:
        raise FreezeError(
            "capacity suite does not preserve held-out outcome isolation"
        )
    materializer = capacity.get("prefix_materializer")
    if not isinstance(materializer, dict):
        raise FreezeError("capacity prefix materializer is not registered")
    if materializer.get("exclusive_create_no_overwrite") is not True:
        raise FreezeError("registered materializer is not exclusive-create")
    materializer_path = _inside(
        root.parent.parent,
        materializer.get("path"),
        "capacity prefix materializer",
    )
    registered_materializer_hash = _sha256(
        materializer.get("sha256"), "capacity prefix materializer hash"
    )
    if sha256_file(_regular_file(materializer_path, "capacity materializer")) != (
        registered_materializer_hash
    ):
        raise FreezeError("capacity prefix materializer SHA-256 mismatch")
    return manifest, actual


def _environment_case(
    manifest: dict[str, Any],
    family: str,
    split: str,
) -> dict[str, Any]:
    families = manifest.get("families")
    if not isinstance(families, list) or family not in families:
        raise FreezeError(f"unknown registered environment family {family!r}")
    cases = manifest.get("environments")
    if not isinstance(cases, list):
        raise FreezeError("environment manifest has no environment records")
    matches = [
        item
        for item in cases
        if isinstance(item, dict)
        and item.get("family") == family
        and item.get("split") == split
    ]
    if len(matches) != 1:
        raise FreezeError(
            f"expected one environment record for {family}/{split}; "
            f"found {len(matches)}"
        )
    return matches[0]


def _validate_request(
    *,
    manifest: dict[str, Any],
    experiment_manifest: dict[str, Any],
    phase: str,
    mode: str,
    family: str,
    seeds: Sequence[int],
    counts: Sequence[int],
    repetitions: int,
    capacity: bool,
    reference_anchor_n: int | None,
) -> tuple[dict[str, Any], tuple[int, ...], tuple[int, ...], int | None]:
    if phase not in PHASE_SPLIT:
        raise FreezeError(f"phase must be one of {sorted(PHASE_SPLIT)}")
    split = PHASE_SPLIT[phase]
    checked_seeds = _strictly_increasing(seeds, "seeds", 2**31 - 1)
    allowed_seeds = PHASE_SEEDS[phase]
    if checked_seeds != allowed_seeds:
        raise FreezeError(
            f"{phase} frozen plans must use the complete seed set "
            f"{allowed_seeds[0]}-{allowed_seeds[-1]}"
        )

    randomization = experiment_manifest.get("randomization")
    if not isinstance(randomization, dict):
        raise FreezeError("experiment manifest has no randomization policy")
    registered_phase_seeds = randomization.get(f"{phase}_seeds")
    if registered_phase_seeds != list(allowed_seeds):
        raise FreezeError(
            f"experiment manifest {phase} seed policy disagrees with runner"
        )

    checked_counts = _strictly_increasing(counts, "counts", 100_000)
    case = _environment_case(manifest, family, split)
    capacity_record = case.get("capacity")
    if not isinstance(capacity_record, dict):
        raise FreezeError(f"{family}/{split} has no registered capacity data")
    ceiling = _positive_int(
        capacity_record.get("family_frozen_max_robot_count"),
        f"{family} frozen ceiling",
        100_000,
    )
    global_ceilings = manifest["capacity_suite"].get(
        "family_frozen_max_robot_count"
    )
    if (
        not isinstance(global_ceilings, dict)
        or global_ceilings.get(family) != ceiling
    ):
        raise FreezeError(f"{family} ceiling registration is inconsistent")
    prefix_records = capacity_record.get("prefix_scenarios")
    if not isinstance(prefix_records, list) or not prefix_records:
        raise FreezeError(f"{family}/{split} has no prefix registrations")
    registered_counts: dict[int, dict[str, Any]] = {}
    for record in prefix_records:
        if not isinstance(record, dict):
            raise FreezeError("capacity prefix registration is malformed")
        count = _positive_int(
            record.get("robot_count"), "registered robot_count", 100_000
        )
        if count in registered_counts:
            raise FreezeError(f"duplicate registered count N={count}")
        if count > ceiling:
            raise FreezeError(f"registered N={count} exceeds ceiling N={ceiling}")
        _sha256(record.get("sha256"), f"registered N={count} SHA-256")
        _positive_int(record.get("bytes"), f"registered N={count} bytes", 10**9)
        registered_counts[count] = record
    for count in checked_counts:
        if count > ceiling:
            raise FreezeError(
                f"requested N={count} exceeds frozen {family} ceiling N={ceiling}"
            )
        if count not in registered_counts:
            allowed = ", ".join(str(item) for item in sorted(registered_counts))
            raise FreezeError(
                f"requested N={count} is not a registered exact-N prefix; "
                f"allowed: {allowed}"
            )

    checked_repetitions = _positive_int(repetitions, "repetitions", 100)
    controls = experiment_manifest.get("control_variables")
    if not isinstance(controls, dict):
        raise FreezeError("experiment manifest has no control variables")
    if controls.get("fixed_work_timesteps") != 100:
        raise FreezeError("experiment manifest fixed-work horizon is not 100")
    if controls.get("runner_jobs") != 1 or controls.get("omp_threads") != 1:
        raise FreezeError("experiment manifest is not single-process/thread")
    if controls.get("warmups_per_binary_map") != 1:
        raise FreezeError("experiment manifest warm-up policy is not one")
    if mode == "timing" and (
        checked_repetitions != 3
        or controls.get("measured_repetitions") != 3
    ):
        raise FreezeError(
            "timing plans must use the experiment manifest's three "
            "measured repetitions"
        )
    if mode == "fidelity" and checked_repetitions != 1:
        raise FreezeError(
            "fidelity plans must use one deterministic trace repetition"
        )

    if phase == "calibration":
        design, design_environments = _calibration_design(
            experiment_manifest
        )
        family_design = design_environments.get(family)
        if not isinstance(family_design, dict):
            raise FreezeError(
                f"{family} is absent from the calibration design"
            )
        initial = design.get("initial_fidelity")
        if not isinstance(initial, dict):
            raise FreezeError(
                "calibration design has no initial fidelity gate"
            )
        initial_condition = (
            mode == "fidelity"
            and family == initial.get("environment")
            and list(checked_counts) == initial.get("counts")
        )
        if initial_condition:
            if (
                list(checked_seeds) != list(PHASE_SEEDS["calibration"])
                or initial.get("seeds")
                != "complete registered calibration seed set 1-10"
                or checked_repetitions != initial.get("repetitions")
            ):
                raise FreezeError(
                    "initial calibration fidelity request disagrees with "
                    "the preregistered design"
                )
        elif mode == "timing":
            if not capacity:
                raise FreezeError(
                    "calibration timing requests must be full capacity grids"
                )
            if list(checked_counts) != family_design.get("timing_counts"):
                raise FreezeError(
                    f"{family} timing request must use the complete "
                    "preregistered calibration grid"
                )
        elif list(checked_counts) != family_design.get("fidelity_counts"):
            raise FreezeError(
                f"{family} fidelity request must use the complete "
                "preregistered calibration grid"
            )

    anchor: int | None = None
    if capacity:
        if reference_anchor_n is None:
            anchor = checked_counts[0]
        else:
            anchor = _positive_int(
                reference_anchor_n, "reference_anchor_n", 100_000
            )
        if anchor != checked_counts[0]:
            raise FreezeError(
                "capacity reference anchor must be the first grid count"
            )
        if phase == "calibration":
            _, design_environments = _calibration_design(
                experiment_manifest
            )
            if anchor != design_environments[family].get(
                "reference_anchor_n"
            ):
                raise FreezeError(
                    f"{family} capacity anchor disagrees with the "
                    "preregistered calibration design"
                )
    elif reference_anchor_n is not None:
        raise FreezeError("reference anchor is valid only for a capacity grid")
    return case, checked_seeds, checked_counts, anchor


def _registered_artifact_sha(value: Any, label: str) -> str:
    if isinstance(value, str):
        return _sha256(value, label)
    if isinstance(value, dict):
        return _sha256(value.get("sha256"), label)
    raise FreezeError(f"{label} is not frozen in the experiment manifest")


def _validate_experiment_links(
    *,
    experiment_manifest: dict[str, Any],
    environment_manifest_sha256: str,
    analysis_revision_sha256: str,
    pixi_lock_sha256: str,
    power_mode: str,
    thermal_policy: str,
    build_manifest_path: Path,
    repository_root: Path,
    build_manifest: dict[str, Any],
) -> None:
    software = experiment_manifest.get("software_versions")
    environment = experiment_manifest.get("environment_and_map")
    if not isinstance(software, dict) or not isinstance(environment, dict):
        raise FreezeError(
            "experiment manifest is missing software/environment registration"
        )
    registered_analysis = _registered_artifact_sha(
        software.get("analysis_revision"),
        "experiment analysis revision SHA-256",
    )
    if registered_analysis != analysis_revision_sha256:
        raise FreezeError(
            "analysis revision disagrees with the experiment manifest"
        )
    registered_environment = _sha256(
        environment.get("map_manifest_sha256"),
        "experiment environment manifest SHA-256",
    )
    if registered_environment != environment_manifest_sha256:
        raise FreezeError(
            "environment manifest disagrees with the experiment manifest"
        )
    if software.get("pixi_lock_sha256") != pixi_lock_sha256:
        raise FreezeError("pixi.lock disagrees with the experiment manifest")
    hardware = experiment_manifest.get("hardware_versions")
    if not isinstance(hardware, dict):
        raise FreezeError("experiment manifest has no hardware policy")
    if hardware.get("power_requirement") != power_mode:
        raise FreezeError("power mode disagrees with the experiment manifest")
    if hardware.get("thermal_policy") != thermal_policy:
        raise FreezeError(
            "thermal policy disagrees with the experiment manifest"
        )

    repository = build_manifest.get("repository")
    if not isinstance(repository, dict):
        raise FreezeError("build manifest repository registration is missing")
    if (
        software.get("reference_source_commit")
        != repository.get("reference_pin")
        or software.get("optimized_source_commit")
        != repository.get("optimized_pin")
    ):
        raise FreezeError(
            "source pins disagree between experiment and build manifests"
        )
    registered_build_path = software.get("build_manifest_path")
    if not isinstance(registered_build_path, str) or not registered_build_path:
        raise FreezeError(
            "experiment build manifest path is not registered"
        )
    resolved_registered_build = (
        repository_root / registered_build_path
    ).resolve()
    if resolved_registered_build != build_manifest_path.expanduser().resolve():
        raise FreezeError(
            "build manifest path disagrees with the experiment manifest"
        )


def _verified_build_methods(
    *,
    repository_root: Path,
    build_manifest_path: Path,
    phase: str,
    mode: str,
    pixi_lock_path: Path,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], bytes]:
    manifest, manifest_bytes = _load_json(
        build_manifest_path, "build manifest"
    )
    if manifest.get("schema_version") != BUILD_MANIFEST_SCHEMA:
        raise FreezeError(
            f"build manifest schema must be {BUILD_MANIFEST_SCHEMA}"
        )
    repository = manifest.get("repository")
    builds = manifest.get("builds")
    gates = manifest.get("gates")
    toolchain = manifest.get("toolchain")
    if not all(
        isinstance(item, dict)
        for item in (repository, builds, gates, toolchain)
    ):
        raise FreezeError("build manifest is missing required sections")
    assert isinstance(repository, dict)
    assert isinstance(builds, dict)
    assert isinstance(gates, dict)
    assert isinstance(toolchain, dict)

    required_gates = (
        "dual_clean_builds",
        "common_registered_tests_all_four_builds",
        "smoke_semantic_trace_identity",
    )
    for gate in required_gates:
        if gates.get(gate) != "pass":
            raise FreezeError(f"build gate {gate} has not passed")
    if mode == "timing" and gates.get(
        "timing_observer_compile_absence"
    ) != "pass":
        raise FreezeError("timing observer-absence gate has not passed")
    phase_gate = f"{phase}_permitted"
    if gates.get(phase_gate) is not True:
        raise FreezeError(
            f"build manifest gate {phase_gate} does not permit plan setup"
        )

    lock = _regular_file(pixi_lock_path, "pixi.lock")
    lock_hash = sha256_file(lock)
    if toolchain.get("pixi_lock_sha256") != lock_hash:
        raise FreezeError("build manifest pixi.lock SHA-256 mismatch")

    expected_pins = {
        "reference": REFERENCE_PIN,
        "optimized": OPTIMIZED_PIN,
    }
    methods: dict[str, dict[str, Any]] = {}
    for method_id, required_pin in expected_pins.items():
        base_revision = _git_object(
            repository.get(f"{method_id}_pin"),
            f"{method_id} base revision",
        )
        if base_revision != required_pin:
            raise FreezeError(
                f"{method_id} base revision is not the approved pin"
            )
        head_revision = _git_object(
            repository.get(f"{method_id}_applied_head"),
            f"{method_id} applied HEAD",
        )
        tree_hash = _git_object(
            repository.get(f"{method_id}_tree"),
            f"{method_id} tree",
        )
        build_key = f"{method_id}_{mode}"
        build = builds.get(build_key)
        if not isinstance(build, dict):
            raise FreezeError(f"build manifest has no {build_key} record")
        if build.get("algorithm_revision") != base_revision:
            raise FreezeError(f"{build_key} algorithm revision mismatch")
        if build.get("applied_head") != head_revision:
            raise FreezeError(f"{build_key} applied HEAD mismatch")
        if build.get("observer_compiled") is not (mode == "fidelity"):
            raise FreezeError(f"{build_key} observer flavor mismatch")

        source_dir = _registered_source_directory(
            repository_root,
            build.get("source_dir"),
            f"{build_key} source_dir",
        )
        actual_head = _git(source_dir, "rev-parse", "HEAD")
        actual_tree = _git(source_dir, "rev-parse", "HEAD^{tree}")
        if actual_head != head_revision or actual_tree != tree_hash:
            raise FreezeError(
                f"{method_id} source worktree identity disagrees with manifest"
            )
        if _git(
            source_dir, "status", "--porcelain", "--untracked-files=all"
        ):
            raise FreezeError(f"{method_id} source worktree is dirty")

        binary_path = _inside(
            repository_root,
            build.get("binary_path"),
            f"{build_key} binary path",
        )
        binary = _regular_file(binary_path, f"{build_key} binary")
        registered_binary_hash = _sha256(
            build.get("binary_sha256"), f"{build_key} binary SHA-256"
        )
        if sha256_file(binary) != registered_binary_hash:
            raise FreezeError(f"{build_key} binary SHA-256 mismatch")
        if not os.access(binary, os.X_OK):
            raise FreezeError(f"{build_key} binary is not executable")
        if build.get("registered_tests") != "3/3 passed":
            raise FreezeError(f"{build_key} registered tests did not pass")
        for filename, hash_field in (
            ("CMakeCache.txt", "cmake_cache_sha256"),
            ("compile_commands.json", "compile_commands_sha256"),
            ("build.log", "clean_build_log_sha256"),
        ):
            auxiliary = _regular_file(
                binary.parent / filename,
                f"{build_key} {filename}",
            )
            registered_auxiliary_hash = _sha256(
                build.get(hash_field), f"{build_key}.{hash_field}"
            )
            if sha256_file(auxiliary) != registered_auxiliary_hash:
                raise FreezeError(
                    f"{build_key} {filename} SHA-256 mismatch"
                )
        methods[method_id] = {
            "source_dir": str(source_dir),
            "base_revision": base_revision,
            "head_revision": head_revision,
            "tree_hash": tree_hash,
            "binary": {
                "path": str(binary),
                "sha256": registered_binary_hash,
            },
        }
    return manifest, methods, manifest_bytes


def _exclusive_write(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o444)
    except OSError as error:
        if error.errno == errno.EEXIST:
            raise FileExistsError(
                f"refusing to overwrite existing output: {path}"
            ) from error
        raise
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path, 0o444)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _order_seed(
    *,
    phase: str,
    mode: str,
    family: str,
    seeds: tuple[int, ...],
    counts: tuple[int, ...],
    artifact_hashes: dict[str, str],
) -> int:
    basis = canonical_json_bytes(
        {
            "phase": phase,
            "mode": mode,
            "family": family,
            "seeds": list(seeds),
            "counts": list(counts),
            "artifact_hashes": artifact_hashes,
        }
    )
    value = int.from_bytes(hashlib.sha256(basis).digest()[:8], "big")
    return value % ((1 << 63) - 1) or 1


def _capacity_evaluation_from_decision(
    *,
    decision_path: Path,
    phase: str,
    mode: str,
    family: str,
    seeds: tuple[int, ...],
    counts: tuple[int, ...],
    timeout_seconds: float,
    artifacts: dict[str, dict[str, str]],
) -> tuple[dict[str, Any], dict[str, str]]:
    try:
        decision, decision_artifact = load_capacity_decision(decision_path)
    except DecisionError as error:
        raise FreezeError(f"capacity decision is invalid: {error}") from error
    provenance = decision["provenance"]
    bindings = {
        "analyzer": "analysis_revision",
        "experiment_manifest": "experiment_manifest",
        "environment_manifest": "environment_manifest",
    }
    for decision_name, plan_name in bindings.items():
        if (
            provenance[decision_name]["sha256"]
            != artifacts[plan_name]["sha256"]
        ):
            raise FreezeError(
                f"capacity decision {decision_name} disagrees with the plan"
            )
    expected_seeds = (
        DECISION_VALIDATION_SEEDS
        if phase == "validation"
        else DECISION_AUDIT_SEEDS
    )
    if tuple(decision["seeds"][phase]) != expected_seeds:
        raise FreezeError(f"capacity decision {phase} seed set changed")
    if seeds != expected_seeds:
        raise FreezeError(
            f"{phase} plan seeds do not match the capacity decision"
        )
    matches = [
        record
        for record in decision["environments"]
        if record["environment"] == family
    ]
    if len(matches) != 1:
        raise FreezeError(
            f"capacity decision has no unique {family} environment"
        )
    record = matches[0]
    grid = tuple(record["grid"])
    expected_count_field = (
        "validation_timing_counts"
        if mode == "timing"
        else "validation_fidelity_counts"
    )
    if tuple(record[expected_count_field]) != grid or counts != grid:
        raise FreezeError(
            f"{family} {mode} counts must equal the decision's full grid"
        )
    if float(record["validation_timeout_seconds"]) != timeout_seconds:
        raise FreezeError(
            f"{family} timeout must equal the frozen capacity decision"
        )
    return (
        {
            "environment": family,
            "role": (
                "timing-grid" if mode == "timing" else "fidelity-grid"
            ),
            "reference_anchor_n": record["reference_anchor_n"],
            "candidate_n_star": record["candidate_n_star"],
            "next_grid_point": record["next_grid_point"],
            "grid": list(grid),
        },
        decision_artifact,
    )


def freeze_plan(
    *,
    phase: str,
    mode: str,
    family: str,
    seeds: Sequence[int],
    counts: Sequence[int],
    repetitions: int,
    timeout_seconds: float,
    minimum_free_disk_bytes: int,
    maximum_peak_rss_mib: float,
    power_mode: str,
    thermal_policy: str,
    output_root: Path,
    config_template: Path,
    analysis_revision: Path,
    capacity: bool = False,
    reference_anchor_n: int | None = None,
    capacity_decision: Path | None = None,
    trace_detail: str | None = None,
    repository_root: Path | None = None,
    environment_root: Path | None = None,
    build_manifest_path: Path | None = None,
    protocol_path: Path | None = None,
    experiment_manifest_path: Path | None = None,
    runner_path: Path | None = None,
) -> FrozenPlan:
    """Validate all inputs, materialize exact-N scenarios, and freeze a plan."""

    if mode not in {"fidelity", "timing"}:
        raise FreezeError("mode must be fidelity or timing")
    if capacity and mode != "timing":
        raise FreezeError("capacity grids are permitted only in timing mode")
    if capacity and phase != "calibration":
        raise FreezeError(
            "capacity-search plans are calibration-only; validation consumes "
            "the N0/grid/N* decisions frozen after calibration"
        )
    if phase == "calibration" and capacity_decision is not None:
        raise FreezeError(
            "calibration plans must not consume a post-calibration decision"
        )
    checked_timeout = _positive_float(
        timeout_seconds, "timeout_seconds", 86_400
    )
    checked_minimum_free_disk = _positive_int(
        minimum_free_disk_bytes, "minimum_free_disk_bytes", 2**63 - 1
    )
    checked_maximum_rss = _positive_float(
        maximum_peak_rss_mib, "maximum_peak_rss_mib", 10**9
    )
    checked_power_mode = _bounded_text(power_mode, "power_mode")
    checked_thermal_policy = _bounded_text(
        thermal_policy, "thermal_policy"
    )
    if checked_minimum_free_disk != MINIMUM_FREE_DISK_BYTES:
        raise FreezeError(
            "minimum_free_disk_bytes must equal the frozen 20 GiB safeguard"
        )
    if checked_maximum_rss != MAXIMUM_PEAK_RSS_MIB:
        raise FreezeError(
            "maximum_peak_rss_mib must equal the frozen 49152 MiB safeguard"
        )
    if checked_power_mode != POWER_MODE:
        raise FreezeError("power_mode must equal the frozen AC Power policy")
    if checked_thermal_policy != THERMAL_POLICY:
        raise FreezeError("thermal_policy does not equal the frozen policy")
    if trace_detail is None:
        trace_detail = "digest"
    if trace_detail not in {"digest", "positions", "full"}:
        raise FreezeError("trace_detail must be digest, positions, or full")
    if trace_detail != "digest":
        raise FreezeError(
            f"{mode} plans must use digest trace detail; "
            f"{trace_detail} traces are not permitted"
        )

    repo = (
        repository_root.expanduser().resolve()
        if repository_root is not None
        else Path(__file__).resolve().parents[2]
    )
    if repo.is_symlink() or not repo.is_dir():
        raise FreezeError(f"repository root is not a real directory: {repo}")
    env_root = (
        environment_root.expanduser().resolve()
        if environment_root is not None
        else repo / "comparison" / "environments"
    )
    build_path = (
        build_manifest_path
        if build_manifest_path is not None
        else repo / "comparison" / "build_manifest.json"
    )
    protocol = (
        protocol_path
        if protocol_path is not None
        else repo / "docs" / "comparison" / "EXPERIMENT_PROTOCOL.md"
    )
    experiment_path = (
        experiment_manifest_path
        if experiment_manifest_path is not None
        else repo / "docs" / "comparison" / "experiment_manifest.json"
    )
    runner = (
        runner_path
        if runner_path is not None
        else repo / "scripts" / "comparison" / "run_paired.py"
    )
    expected_config_template = (
        repo / "config" / "comparison" / "laptop_benchmark.yaml"
    ).resolve()
    supplied_config_template = _regular_file(
        config_template, "config template"
    )
    if supplied_config_template != expected_config_template:
        raise FreezeError(
            "config template must be the frozen "
            "config/comparison/laptop_benchmark.yaml artifact"
        )
    checked_timestep = _verified_timestep_seconds(supplied_config_template)

    env_manifest, env_manifest_hash = _verified_environment_manifest(env_root)
    experiment, _ = _load_json(experiment_path, "experiment manifest")
    robot_configuration = experiment.get("robot_configuration")
    if (
        not isinstance(robot_configuration, dict)
        or robot_configuration.get("timestep_seconds") != checked_timestep
        or robot_configuration.get("fixed_work_timesteps") != 100
    ):
        raise FreezeError(
            "experiment robot timing disagrees with the protected config"
        )
    design, _ = _calibration_design(experiment)
    if phase == "calibration":
        if checked_timeout != float(design["timeout_seconds"]):
            raise FreezeError(
                "calibration timeout must equal the preregistered 300 seconds"
            )
        initial = design["initial_fidelity"]
        if (
            mode == "fidelity"
            and family == initial.get("environment")
            and list(counts) == initial.get("counts")
            and trace_detail != initial.get("trace_detail")
        ):
            raise FreezeError(
                "initial calibration fidelity trace detail disagrees with "
                "the preregistered design"
            )
    (
        case,
        checked_seeds,
        checked_counts,
        anchor,
    ) = _validate_request(
        manifest=env_manifest,
        experiment_manifest=experiment,
        phase=phase,
        mode=mode,
        family=family,
        seeds=seeds,
        counts=counts,
        repetitions=repetitions,
        capacity=capacity,
        reference_anchor_n=reference_anchor_n,
    )
    if phase in {"validation", "audit"} and capacity_decision is None:
        raise FreezeError(
            f"{phase} plans require a frozen --capacity-decision artifact"
        )
    build_manifest, methods, build_manifest_bytes = _verified_build_methods(
        repository_root=repo,
        build_manifest_path=build_path,
        phase=phase,
        mode=mode,
        pixi_lock_path=repo / "pixi.lock",
    )

    map_record = case.get("map")
    if not isinstance(map_record, dict):
        raise FreezeError(f"{family}/{phase} map registration is missing")
    map_path = _inside(env_root, map_record.get("path"), "environment map")
    map_path = _regular_file(map_path, "environment map")
    map_hash = _sha256(map_record.get("sha256"), "environment map SHA-256")
    if sha256_file(map_path) != map_hash:
        raise FreezeError("environment map SHA-256 mismatch")
    if map_path.stat().st_size != map_record.get("bytes"):
        raise FreezeError("environment map byte count mismatch")

    artifacts = {
        "pixi_lock": _artifact(repo / "pixi.lock", "pixi.lock"),
        "build_manifest": {
            "path": str(_regular_file(build_path, "build manifest")),
            "sha256": sha256_bytes(build_manifest_bytes),
        },
        "environment_manifest": {
            "path": str(
                _regular_file(
                    env_root / "manifest.json", "environment manifest"
                )
            ),
            "sha256": env_manifest_hash,
        },
        "config_template": _artifact(
            supplied_config_template, "config template"
        ),
        "protocol": _artifact(protocol, "comparison protocol"),
        "experiment_manifest": _artifact(
            experiment_path, "experiment manifest"
        ),
        "analysis_revision": _artifact(
            analysis_revision, "analysis revision"
        ),
    }
    _validate_experiment_links(
        experiment_manifest=experiment,
        environment_manifest_sha256=env_manifest_hash,
        analysis_revision_sha256=artifacts["analysis_revision"]["sha256"],
        pixi_lock_sha256=artifacts["pixi_lock"]["sha256"],
        power_mode=checked_power_mode,
        thermal_policy=checked_thermal_policy,
        build_manifest_path=_regular_file(build_path, "build manifest"),
        repository_root=repo,
        build_manifest=build_manifest,
    )
    capacity_evaluation: dict[str, Any] | None = None
    if phase in {"validation", "audit"}:
        assert capacity_decision is not None
        (
            capacity_evaluation,
            decision_artifact,
        ) = _capacity_evaluation_from_decision(
            decision_path=capacity_decision,
            phase=phase,
            mode=mode,
            family=family,
            seeds=checked_seeds,
            counts=checked_counts,
            timeout_seconds=checked_timeout,
            artifacts=artifacts,
        )
        artifacts["capacity_decision"] = decision_artifact
    runner_artifact = _artifact(runner, "paired runner")
    artifact_hashes = {
        key: value["sha256"] for key, value in artifacts.items()
    }
    artifact_hashes["runner"] = runner_artifact["sha256"]
    order_seed = _order_seed(
        phase=phase,
        mode=mode,
        family=family,
        seeds=checked_seeds,
        counts=checked_counts,
        artifact_hashes=artifact_hashes,
    )

    output = output_root.expanduser().resolve()
    if output.exists() or output.is_symlink():
        raise FileExistsError(
            f"refusing to overwrite existing setup directory: {output}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.mkdir()
    except FileExistsError as error:
        raise FileExistsError(
            f"refusing to overwrite existing setup directory: {output}"
        ) from error

    created: list[Path] = []
    scenario_paths: list[Path] = []
    try:
        scenario_directory = output / "scenarios"
        scenario_directory.mkdir()
        registered_prefixes = {
            item["robot_count"]: item
            for item in case["capacity"]["prefix_scenarios"]
        }
        scenario_by_count: dict[int, dict[str, str]] = {}
        for count in checked_counts:
            scenario_path = scenario_directory / f"n-{count}.json"
            digest = materialize_registered_prefix(
                env_root,
                family,
                PHASE_SPLIT[phase],
                count,
                scenario_path,
            )
            created.append(scenario_path)
            scenario_paths.append(scenario_path)
            if digest != registered_prefixes[count]["sha256"]:
                raise FreezeError(
                    f"materialized N={count} scenario hash changed"
                )
            scenario_by_count[count] = {
                "path": str(scenario_path.resolve()),
                "sha256": digest,
            }

        pairs: list[dict[str, Any]] = []
        for count in checked_counts:
            for seed in checked_seeds:
                pairs.append(
                    {
                        "pair_id": (
                            f"{family}-{phase}-seed-{seed}-n-{count}"
                        ),
                        "environment": family,
                        "seed": seed,
                        "robot_count": count,
                        "map": {
                            "path": str(map_path),
                            "sha256": map_hash,
                        },
                        "scenario": scenario_by_count[count],
                    }
                )

        plan: dict[str, Any] = {
            "schema_version": PLAN_SCHEMA,
            "phase": phase,
            "mode": mode,
            "fixed_work_timesteps": 100,
            "timestep_seconds": checked_timestep,
            "idle_cooldown_seconds": IDLE_COOLDOWN_SECONDS,
            "timeout_seconds": checked_timeout,
            "termination_grace_seconds": 0.5,
            "minimum_free_disk_bytes": checked_minimum_free_disk,
            "maximum_peak_rss_mib": checked_maximum_rss,
            "power_mode": checked_power_mode,
            "thermal_policy": checked_thermal_policy,
            "task_validity_predicate_id": TASK_VALIDITY_PREDICATE_ID,
            "repetitions": repetitions,
            "warmups_per_binary_map": 1,
            "omp_threads": 1,
            "order_seed": order_seed,
            "runner_sha256": runner_artifact["sha256"],
            "trace_detail": trace_detail,
            "run_cwd": str(repo),
            "artifacts": artifacts,
            "methods": methods,
            "pairs": pairs,
        }
        if capacity:
            assert anchor is not None
            plan["capacity_search"] = {
                "environment": family,
                "reference_anchor_n": anchor,
                "grid": list(checked_counts),
                "max_points": len(checked_counts),
            }
        if capacity_evaluation is not None:
            plan["capacity_evaluation"] = capacity_evaluation
        plan_data = canonical_json_bytes(plan)
        plan_path = output / "plan.json"
        _exclusive_write(plan_path, plan_data)
        created.append(plan_path)
        return FrozenPlan(
            plan_path=plan_path,
            plan_sha256=sha256_bytes(plan_data),
            scenario_paths=tuple(scenario_paths),
        )
    except BaseException:
        for path in reversed(created):
            path.unlink(missing_ok=True)
        for directory in (output / "scenarios", output):
            try:
                directory.rmdir()
            except OSError:
                pass
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        required=True,
        choices=tuple(PHASE_SPLIT),
    )
    parser.add_argument(
        "--mode", required=True, choices=("fidelity", "timing")
    )
    parser.add_argument("--family", required=True)
    parser.add_argument(
        "--seeds",
        required=True,
        type=parse_integer_set,
        help="strictly increasing comma/range list, for example 1-10",
    )
    count_group = parser.add_mutually_exclusive_group(required=True)
    count_group.add_argument(
        "--counts",
        type=parse_integer_set,
        help="equal-N count list",
    )
    count_group.add_argument(
        "--grid",
        type=parse_integer_set,
        help="capacity grid; timing mode only",
    )
    parser.add_argument("--reference-anchor", type=int)
    parser.add_argument(
        "--capacity-decision",
        type=Path,
        help="required immutable post-calibration decision for validation/audit",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        help="defaults to 1 for fidelity and 3 for timing",
    )
    parser.add_argument("--timeout-seconds", type=float, required=True)
    parser.add_argument(
        "--minimum-free-disk-bytes",
        type=int,
        default=MINIMUM_FREE_DISK_BYTES,
    )
    parser.add_argument(
        "--maximum-peak-rss-mib",
        type=float,
        default=MAXIMUM_PEAK_RSS_MIB,
    )
    parser.add_argument("--power-mode", default=POWER_MODE)
    parser.add_argument("--thermal-policy", default=THERMAL_POLICY)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument(
        "--config-template",
        type=Path,
        help="defaults to config/comparison/laptop_benchmark.yaml",
    )
    parser.add_argument("--analysis-revision", type=Path, required=True)
    parser.add_argument("--trace-detail", choices=("digest",))
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    args = parser.parse_args(argv)
    capacity = args.grid is not None
    selected_counts = args.grid if capacity else args.counts
    assert selected_counts is not None
    try:
        repetitions = (
            args.repetitions
            if args.repetitions is not None
            else (1 if args.mode == "fidelity" else 3)
        )
        config_template = (
            args.config_template
            if args.config_template is not None
            else (
                args.repository_root
                / "config"
                / "comparison"
                / "laptop_benchmark.yaml"
            )
        )
        frozen = freeze_plan(
            phase=args.phase,
            mode=args.mode,
            family=args.family,
            seeds=args.seeds,
            counts=selected_counts,
            repetitions=repetitions,
            timeout_seconds=args.timeout_seconds,
            minimum_free_disk_bytes=args.minimum_free_disk_bytes,
            maximum_peak_rss_mib=args.maximum_peak_rss_mib,
            power_mode=args.power_mode,
            thermal_policy=args.thermal_policy,
            output_root=args.output_root,
            config_template=config_template,
            analysis_revision=args.analysis_revision,
            capacity=capacity,
            reference_anchor_n=args.reference_anchor,
            capacity_decision=args.capacity_decision,
            trace_detail=args.trace_detail,
            repository_root=args.repository_root,
        )
    except (
        FreezeError,
        FileExistsError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        print(f"comparison plan freeze failed: {error}", file=sys.stderr)
        return 1
    print(f"{frozen.plan_sha256}  {frozen.plan_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
