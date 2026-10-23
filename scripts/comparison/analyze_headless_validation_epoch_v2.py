#!/usr/bin/env python3
"""Analyze the frozen epoch-5 held-out validation cohort in memory.

The public CLI is deliberately nonauthorizing.  A separately committed
activation wrapper must bind the completed session and output identities.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import statistics
import struct
import sys
import types
from typing import Any, Mapping, Sequence


sys.dont_write_bytecode = True

SCHEMA = "dancers-headless-validation-analysis-v1"
PLAN_SCHEMA = "dancers-headless-validation-epoch-plan-v1"
TRACK_ID = "simulator-demo-v1-headless-epoch-1"
EPOCH_ID = "simulator-demo-v1-headless-epoch-5-validation"
PHASE = "validation"
RUN_CLASSES = ("digest_fidelity", "positions_replay", "headless_timing")
PLAN_ROOT = PurePosixPath(
    "comparison/plans/simulator-demo/v1/headless-epoch-5-validation"
)
RESULT_ROOT = PurePosixPath(
    "results/simulator-demo/v1/headless-scientific/epoch-5-validation"
)
PLAN = {
    "bytes": 4618054,
    "path": (PLAN_ROOT / "plan.json").as_posix(),
    "sha256": "592c8ded608f4fbfcfb128d30877d5809d7be5b5b9e6a6504e78ff3cfa9552b5",
}
CORE = {
    "bytes": 219654,
    "path": "scripts/comparison/analyze_headless_epoch_v1.py",
    "sha256": "d25ca18aee5167a2c796606774acc5406bcdfdda83a85e914e9526fd179f64c4",
}
RUNNER = {
    "bytes": 8605,
    "path": "scripts/comparison/run_headless_validation_epoch_v1.py",
    "sha256": "86752cd303a7175b5af4bd47288ef4251e790b6d4a58538bf83f90a25d88ee36",
}
FAMILIES = ("open", "perfect_maze", "fractal_low", "fractal_medium", "fractal_high")
GRID = {
    "open": (8,),
    "perfect_maze": (2, 4, 6),
    "fractal_low": (2, 4),
    "fractal_medium": (2, 4),
    "fractal_high": (2, 4),
}
MISSION_SEEDS = tuple(range(11, 31))
BOOTSTRAP_DOMAIN = b"dancers-headless-validation-bootstrap-v1\0"
BOOTSTRAP_DRAWS = 100_000
POINT_FLOOR = 1.05
LOWER_FLOOR = 1.0


class ValidationAnalysisError(ValueError):
    """The held-out evidence or its exact analysis contract differs."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (RecursionError, TypeError, UnicodeError, ValueError) as error:
        raise ValidationAnalysisError(f"value is not canonical JSON: {error}") from error


def _read(root: Path, identity: Mapping[str, Any], label: str) -> bytes:
    try:
        raw = root.joinpath(*PurePosixPath(identity["path"]).parts).read_bytes()
    except (KeyError, OSError, TypeError) as error:
        raise ValidationAnalysisError(f"cannot read {label}: {error}") from error
    if len(raw) != identity.get("bytes") or _sha(raw) != identity.get("sha256"):
        raise ValidationAnalysisError(f"{label} identity differs")
    return raw


def _load(root: Path, identity: Mapping[str, Any], name: str) -> types.ModuleType:
    raw = _read(root, identity, name)
    module = types.ModuleType(name)
    module.__file__ = str(identity["path"])
    sys.modules[name] = module
    try:
        exec(compile(raw, module.__file__, "exec", dont_inherit=True, optimize=0), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _runtime_seed(mission_seed: int) -> int:
    if type(mission_seed) is not int or not 11 <= mission_seed <= 30:
        raise ValidationAnalysisError("validation mission seed differs")
    return 1 + ((mission_seed - 11) % 10)


def load_authenticated_plan(repository_root: Path) -> dict[str, Any]:
    root = Path(os.path.abspath(repository_root))
    if Path(os.path.realpath(root)) != root:
        raise ValidationAnalysisError("repository root is not canonical")
    plan_raw = _read(root, PLAN, "registered validation plan")
    core = _load(root, CORE, "_dancers_validation_analysis_core")
    runner = _load(root, RUNNER, "_dancers_validation_analysis_runner")
    core.PLAN_SCHEMA = PLAN_SCHEMA
    core.TRACK_ID = TRACK_ID
    core.PHASE = PHASE
    core.PLAN_ROOT = PLAN_ROOT
    core.RESULT_ROOT = RESULT_ROOT
    core.RUN_CLASSES = RUN_CLASSES
    core.Path = type(Path())
    core.flatten_plan_slots = lambda plan: [
        dict(slot) for slot, _index in runner._flatten_schedule(plan)
    ]
    original_expected = core._expected_runtime_resolved_config
    core._expected_runtime_resolved_config = lambda **kwargs: original_expected(
        **{**kwargs, "mission_seed": _runtime_seed(kwargs["mission_seed"])}
    )
    original_summary = core.validate_summary_bytes
    core.validate_summary_bytes = lambda raw, **kwargs: original_summary(
        raw, **{**kwargs, "mission_seed": _runtime_seed(kwargs["mission_seed"])}
    )
    authenticated = runner.load_authenticated_plan(
        root.joinpath(*PurePosixPath(PLAN["path"]).parts),
        root,
        production=False,
    )
    if authenticated.plan_bytes != plan_raw or len(authenticated.schedule) != 2810:
        raise ValidationAnalysisError("runner-authenticated validation plan differs")
    try:
        geometry_identity = authenticated.plan["input_records"]["geometry"]["content"]["identity"]
    except (KeyError, TypeError) as error:
        raise ValidationAnalysisError("validation geometry identity is absent") from error
    geometry_raw = _read(root, geometry_identity, "validation geometry source")
    geometry = core.load_bound_geometry_source(geometry_raw, identity=geometry_identity)
    return {
        "core": core,
        "file_authentication": {"all_declared_files_authenticated": True},
        "geometry_namespace": geometry,
        "plan": authenticated.plan,
        "plan_identity": dict(PLAN),
        "repository_root": root,
        "runner_plan": authenticated,
    }


def _draw_index(cell_label: str, replicate: int, position: int, population: int) -> int:
    if population <= 0:
        raise ValidationAnalysisError("bootstrap population is empty")
    limit = (1 << 256) - ((1 << 256) % population)
    label = cell_label.encode("utf-8")
    for counter in range(10_000):
        digest = hashlib.sha256(
            BOOTSTRAP_DOMAIN
            + label
            + b"\0"
            + struct.pack(">QQQ", replicate, position, counter)
        ).digest()
        integer = int.from_bytes(digest, "big")
        if integer < limit:
            return integer % population
    raise ValidationAnalysisError("bootstrap rejection stream exhausted")


def _type7(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValidationAnalysisError("quantile population is empty")
    ordered = sorted(values)
    h = (len(ordered) - 1) * probability
    lower = int(math.floor(h))
    upper = int(math.ceil(h))
    return ordered[lower] + (h - lower) * (ordered[upper] - ordered[lower])


def cluster_bootstrap(
    cluster_log_means: Mapping[int, float], *, family: str, robot_count: int,
    draws: int = BOOTSTRAP_DRAWS,
) -> dict[str, Any]:
    if tuple(sorted(cluster_log_means)) != MISSION_SEEDS:
        raise ValidationAnalysisError("validation bootstrap cluster inventory differs")
    if type(draws) is not int or draws < 1:
        raise ValidationAnalysisError("validation bootstrap draw count differs")
    logs = [float(cluster_log_means[seed]) for seed in MISSION_SEEDS]
    if any(not math.isfinite(value) for value in logs):
        raise ValidationAnalysisError("validation bootstrap input is nonfinite")
    label = f"{family}:n-{robot_count}"
    samples = []
    for replicate in range(draws):
        sampled = [logs[_draw_index(label, replicate, position, len(logs))] for position in range(len(logs))]
        samples.append(math.exp(statistics.fmean(sampled)))
    point = math.exp(statistics.fmean(logs))
    return {
        "cluster_count": len(logs),
        "confidence_level": 0.95,
        "domain": BOOTSTRAP_DOMAIN.decode("utf-8"),
        "draw_count": draws,
        "geometric_mean_wall_speedup": point,
        "interval": "two-sided-percentile-linear-type-7",
        "lower_95": _type7(samples, 0.025),
        "prng": "sha256-counter-rejection-v1",
        "upper_95": _type7(samples, 0.975),
    }


def _timing_analysis(
    authenticated: Mapping[str, Any], session: Mapping[str, Any], fidelity: Mapping[str, Any],
    *, draws: int,
) -> dict[str, Any]:
    plan = authenticated["plan"]
    slots = session["slots"]
    evidence = session["evidence"]
    sequence_by_id = {slot["slot_id"]: sequence for sequence, slot in enumerate(slots, start=1)}
    scenes = {scene["scene_id"]: scene for scene in plan["scenes"]}
    measured = plan["schedule"]["inventories"]["headless_timing"]["measured"]
    if len(measured) != 1200 or len(measured) % 2:
        raise ValidationAnalysisError("validation timing denominator differs")
    task_valid = set(fidelity["valid_scene_ids"])
    pair_rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for offset in range(0, len(measured), 2):
        left, right = measured[offset : offset + 2]
        if (
            {left["method"], right["method"]} != {"reference", "optimized"}
            or left["scene_id"] != right["scene_id"]
            or left["input_id"] != right["input_id"]
            or left["repetition"] != right["repetition"]
        ):
            raise ValidationAnalysisError("validation timing planned pair differs")
        by_method = {left["method"]: left, right["method"]: right}
        measurements: dict[str, Mapping[str, Any]] = {}
        reasons: list[str] = []
        for method, slot in by_method.items():
            item = evidence.get(sequence_by_id[slot["slot_id"]])
            if item is None or item["attempt"]["category"] != "completed":
                reasons.append(f"{method}-attempt-not-completed")
                continue
            measurement = item["attempt"]["measurement"]
            if (
                measurement is None
                or measurement["returncode"] != 0
                or measurement["timed_out"] is not False
                or measurement["process_group_contained"] is not True
                or measurement["stdout_truncated"] is not False
                or measurement["stderr_truncated"] is not False
                or measurement["wall_seconds"] <= 0.0
            ):
                reasons.append(f"{method}-measurement-invalid")
                continue
            if (slot["input_id"], method, slot["repetition"]) not in fidelity["digest_success"]:
                reasons.append(f"{method}-digest-join-invalid")
            if (slot["input_id"], method) not in fidelity["positions_success"]:
                reasons.append(f"{method}-positions-join-invalid")
            measurements[method] = measurement
        scene = scenes[left["scene_id"]]
        if left["scene_id"] not in task_valid:
            reasons.append("scene-task-invalid")
        row = {
            "cluster_id": scene["sampling_unit"]["clustering_unit_id"],
            "failure_reasons": sorted(set(reasons)),
            "family": scene["family"],
            "input_id": left["input_id"],
            "mission_seed": scene["seed"],
            "repetition": left["repetition"],
            "robot_count": scene["robot_count"],
            "scene_id": left["scene_id"],
            "timing_pair_valid": not reasons and set(measurements) == {"reference", "optimized"},
        }
        if row["timing_pair_valid"]:
            reference = measurements["reference"]
            optimized = measurements["optimized"]
            row.update({
                "optimized_wall_seconds": optimized["wall_seconds"],
                "reference_wall_seconds": reference["wall_seconds"],
                "wall_speedup": reference["wall_seconds"] / optimized["wall_seconds"],
            })
        pair_rows.append(row)
        failures.extend(f"{left['scene_id']}:rep-{left['repetition']}:{reason}" for reason in reasons)

    cell_rows: list[dict[str, Any]] = []
    for family in FAMILIES:
        for count in GRID[family]:
            rows = [row for row in pair_rows if row["family"] == family and row["robot_count"] == count]
            logs_by_seed: dict[int, list[float]] = {seed: [] for seed in MISSION_SEEDS}
            for row in rows:
                if row["timing_pair_valid"]:
                    logs_by_seed[row["mission_seed"]].append(math.log(row["wall_speedup"]))
            complete = len(rows) == 60 and all(len(logs_by_seed[seed]) == 3 for seed in MISSION_SEEDS)
            bootstrap = (
                cluster_bootstrap(
                    {seed: statistics.fmean(logs_by_seed[seed]) for seed in MISSION_SEEDS},
                    family=family,
                    robot_count=count,
                    draws=draws,
                )
                if complete
                else None
            )
            task_count = sum(
                row["task_valid"]
                for row in fidelity["task_report"]["scene_rows"]
                if row["family"] == family and row["robot_count"] == count
            )
            task_gate = task_count == 20
            point_gate = bootstrap is not None and bootstrap["geometric_mean_wall_speedup"] > POINT_FLOOR
            lower_gate = bootstrap is not None and bootstrap["lower_95"] > LOWER_FLOOR
            cell_rows.append({
                "bootstrap": bootstrap,
                "cell_pass": bool(task_gate and point_gate and lower_gate),
                "family": family,
                "performance_lower_bound_strictly_above_1_0": bool(lower_gate),
                "performance_point_strictly_above_1_05": bool(point_gate),
                "robot_count": count,
                "task_valid_scene_count": task_count,
                "task_validity_all_20": task_gate,
                "timing_pair_count": len(rows),
                "timing_pairs_all_valid": complete,
            })
    all_cells_pass = len(cell_rows) == 10 and all(row["cell_pass"] for row in cell_rows)
    return {
        "all_cells_pass": all_cells_pass,
        "cell_count": len(cell_rows),
        "cell_rows": cell_rows,
        "failure_reasons": sorted(set(failures)),
        "no_outcome_filtering": True,
        "pair_count": len(pair_rows),
        "pair_rows": pair_rows,
    }


def analyze_validation_session(
    repository_root: Path, session_path: Path, *, draws: int = BOOTSTRAP_DRAWS,
) -> dict[str, Any]:
    authenticated = load_authenticated_plan(repository_root)
    core = authenticated["core"]
    try:
        session = core.audit_session(authenticated, session_path)
        fidelity = core.analyze_fidelity_and_task(authenticated, session)
    except Exception as error:
        raise ValidationAnalysisError(f"validation task analysis failed: {error}") from error
    timing = _timing_analysis(authenticated, session, fidelity, draws=draws)
    positive = bool(
        session["report"]["complete"]
        and session["report"]["all_attempts_completed"]
        and fidelity["task_report"]["all_scenes_valid"]
        and timing["all_cells_pass"]
    )
    report = {
        "analysis": {
            "fidelity": fidelity["fidelity_report"],
            "inventory": session["report"],
            "positions_replay": fidelity["positions_report"],
            "task_validity": fidelity["task_report"],
            "timing_validation": timing,
        },
        "classification": {
            "benchmark_claim_ready": False,
            "capacity_claim_ready": False,
            "fixed_suite_validation_evidence": positive,
            "population_generalization_ready": False,
            "scientific_evidence": positive,
            "validation_claim_ready": positive,
        },
        "epoch_id": EPOCH_ID,
        "plan": dict(PLAN),
        "schema_version": SCHEMA,
        "status": {
            "analysis_completed": True,
            "benchmark_unlock": False,
            "validation_outcome": "positive" if positive else "negative",
        },
    }
    validate_report(report, expected_draws=draws)
    return report


def validate_report(value: Any, *, expected_draws: int = BOOTSTRAP_DRAWS) -> dict[str, Any]:
    if type(value) is not dict or set(value) != {
        "analysis", "classification", "epoch_id", "plan", "schema_version", "status",
    }:
        raise ValidationAnalysisError("validation report schema differs")
    if value["schema_version"] != SCHEMA or value["epoch_id"] != EPOCH_ID or value["plan"] != PLAN:
        raise ValidationAnalysisError("validation report identity differs")
    analysis = value["analysis"]
    timing = analysis["timing_validation"]
    if (
        analysis["inventory"]["expected_attempt_count"] != 2810
        or analysis["fidelity"]["pair_count"] != 600
        or analysis["positions_replay"]["pair_count"] != 200
        or analysis["task_validity"]["scene_count"] != 200
        or timing["pair_count"] != 600
        or timing["cell_count"] != 10
        or any(
            row["bootstrap"] is not None and row["bootstrap"]["draw_count"] != expected_draws
            for row in timing["cell_rows"]
        )
    ):
        raise ValidationAnalysisError("validation report denominator differs")
    positive = timing["all_cells_pass"] and analysis["task_validity"]["all_scenes_valid"]
    if value["classification"] != {
        "benchmark_claim_ready": False,
        "capacity_claim_ready": False,
        "fixed_suite_validation_evidence": bool(positive),
        "population_generalization_ready": False,
        "scientific_evidence": bool(positive),
        "validation_claim_ready": bool(positive),
    } or value["status"] != {
        "analysis_completed": True,
        "benchmark_unlock": False,
        "validation_outcome": "positive" if positive else "negative",
    }:
        raise ValidationAnalysisError("validation report classification differs")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    _ = argv
    print(
        "validation analysis remains nonauthorizing pending an immutable completed-session authorization",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
