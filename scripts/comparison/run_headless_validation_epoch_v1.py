#!/usr/bin/env python3
"""Epoch-5 validation runner adapter; public execution remains disabled."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import sys
import types
from typing import Any, Callable, Mapping, Sequence


sys.dont_write_bytecode = True

EPOCH_SCHEMA = "dancers-headless-validation-epoch-plan-v1"
TRACK_ID = "simulator-demo-v1-headless-epoch-1"
EPOCH_ID = "simulator-demo-v1-headless-epoch-5-validation"
PHASE = "validation"
RUN_CLASSES = ("digest_fidelity", "positions_replay", "headless_timing")
PLAN_ROOT = PurePosixPath("comparison/plans/simulator-demo/v1/headless-epoch-5-validation")
RESULT_ROOT = PurePosixPath("results/simulator-demo/v1/headless-scientific/epoch-5-validation")
SOURCE_PATH = PurePosixPath("scripts/comparison/run_headless_validation_epoch_v1.py")
FREEZER_PATH = PurePosixPath("scripts/comparison/freeze_headless_validation_epoch_v1.py")
BASE = {
    "bytes": 13257,
    "path": "scripts/comparison/run_headless_task_calibration_epoch_v1.py",
    "sha256": "6fa4c6bd5a75662f9c07e49a8043e68b5242f063a923434eb001518052a76cff",
}
RUNNER_RECORD_SCHEMA = "dancers-headless-task-runner-record-v1"
RUNNER_CONTRACT = {
    "execution_authorized": False,
    "run_classes": list(RUN_CLASSES),
    "supported_plan_schema": EPOCH_SCHEMA,
}
EXPECTED = {"digest_fidelity": 1200, "positions_replay": 400, "headless_timing": 1200}
_CACHE: dict[str, types.ModuleType] = {}


class ValidationRunnerError(ValueError):
    """The exact validation runner binding differs."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _flatten_schedule(plan: Mapping[str, Any]) -> list[tuple[Mapping[str, Any], int]]:
    schedule = plan.get("schedule")
    if type(schedule) is not dict or set(schedule) != {
        "assignment_algorithm", "balance", "execution_order", "inventories",
        "timing_gate", "warmups",
    }:
        raise ValidationRunnerError("validation schedule schema differs")
    if schedule["execution_order"] != ["warmups", *RUN_CLASSES]:
        raise ValidationRunnerError("validation execution order differs")
    if schedule["timing_gate"] != {
        "all_prior_digest_and_positions_required": True,
        "on_prior_failure": "do-not-start-timing",
        "timing_inventory_present": True,
    }:
        raise ValidationRunnerError("validation timing gate differs")
    warmups = schedule["warmups"]
    if warmups.get("process_count") != 10 or len(warmups.get("slots", ())) != 10:
        raise ValidationRunnerError("validation warmup denominator differs")
    flattened = [(slot, index) for index, slot in enumerate(warmups["slots"])]
    inventories = schedule.get("inventories")
    if type(inventories) is not dict or set(inventories) != set(RUN_CLASSES):
        raise ValidationRunnerError("validation inventory set differs")
    for run_class in RUN_CLASSES:
        inventory = inventories[run_class]
        measured = inventory.get("measured")
        if (
            type(measured) is not list
            or inventory.get("run_class") != run_class
            or inventory.get("measured_process_count") != EXPECTED[run_class]
            or len(measured) != EXPECTED[run_class]
            or inventory.get("paired_scene_repetition_count") * 2 != len(measured)
            or inventory.get("timed") is not (run_class == "headless_timing")
        ):
            raise ValidationRunnerError("validation measured denominator differs")
        flattened.extend((slot, index) for index, slot in enumerate(measured))
    if len(flattened) != 2810:
        raise ValidationRunnerError("validation process denominator differs")
    return flattened


def _core(root: Path) -> types.ModuleType:
    canonical = Path(os.path.abspath(root))
    key = os.fspath(canonical)
    if key in _CACHE:
        return _CACHE[key]
    raw = canonical.joinpath(*PurePosixPath(BASE["path"]).parts).read_bytes()
    if len(raw) != BASE["bytes"] or _sha(raw) != BASE["sha256"]:
        raise ValidationRunnerError("task runner identity differs")
    module = types.ModuleType("_dancers_validation_runner_core")
    module.__file__ = BASE["path"]
    sys.modules[module.__name__] = module
    exec(compile(raw, module.__file__, "exec", dont_inherit=True, optimize=0), module.__dict__)
    module.EPOCH_SCHEMA = EPOCH_SCHEMA
    module.EPOCH_ID = EPOCH_ID
    module.PHASE = PHASE
    module.RUN_CLASSES = RUN_CLASSES
    module.PLAN_ROOT = PLAN_ROOT
    module.RESULT_ROOT = RESULT_ROOT
    module.SOURCE_PATH = SOURCE_PATH
    module.FREEZER_PATH = FREEZER_PATH
    module.RUNNER_CONTRACT = dict(RUNNER_CONTRACT)
    module._flatten_schedule = _flatten_schedule
    def load_base(passed_root: Path) -> types.ModuleType:
        cache_key = os.fspath(Path(os.path.abspath(passed_root)))
        cached = module._BASE_CACHE.get(cache_key)
        if cached is not None:
            return cached
        base_raw = module._read_exact(Path(cache_key), module.BASE_RUNNER, "base runner")
        old = b"not 1 <= seed <= 10"
        new = b"not 11 <= seed <= 30"
        resolved_old = b'resolved.get("rng_seed") != attempt.raw["seed"]'
        resolved_new = b'resolved.get("rng_seed") != 1 + ((int(attempt.raw["seed"]) - 11) % 10)'
        if base_raw.count(old) != 1 or base_raw.count(resolved_old) != 1:
            raise ValidationRunnerError("base runner seed-range transformation anchor differs")
        base = types.ModuleType("_dancers_validation_base_runner")
        base.__file__ = module.BASE_RUNNER_PATH.as_posix()
        sys.modules[base.__name__] = base
        transformed = base_raw.replace(old, new).replace(resolved_old, resolved_new)
        exec(compile(transformed, base.__file__, "exec", dont_inherit=True, optimize=0), base.__dict__)
        base.EPOCH_SCHEMA = EPOCH_SCHEMA
        base.TRACK_ID = TRACK_ID
        base.PHASE = PHASE
        base.RUN_CLASSES = RUN_CLASSES
        base.PLAN_ROOT = PLAN_ROOT
        base.RESULT_ROOT = RESULT_ROOT
        base._flatten_schedule = _flatten_schedule
        base._production_status_ready = lambda value: (
            value.get("status", {}).get("registration_state") == "registered-committed"
            and value.get("status", {}).get("execution_ready") is True
            and value.get("classification", {}).get("execution_authorized") is True
            and value.get("classification", {}).get("validation_execution_authorized") is True
            and value.get("classification", {}).get("calibration_execution_authorized") is False
        )
        base._descriptor_runtime_for_attempt = lambda attempt: base.DescriptorRuntime(
            controller_timesteps=100,
            num_robots=int(attempt.raw["robot_count"]),
            observer_mode={"headless_timing": "none", "digest_fidelity": "digest", "positions_replay": "positions"}[attempt.run_class],
            omp_threads=1,
            rng_seed=1 + ((int(attempt.raw["seed"]) - 11) % 10),
            timestep_milliseconds=100,
        )
        original_verify_config = base._verify_fixed_config
        base._verify_fixed_config = lambda raw, *, family, robot_count, seed, map_path, scenario_path: original_verify_config(
            raw, family=family, robot_count=robot_count,
            seed=1 + ((seed - 11) % 10), map_path=map_path,
            scenario_path=scenario_path,
        )
        module._BASE_CACHE[cache_key] = base
        return base

    module._load_base = load_base
    module._BASE_CACHE.clear()
    _CACHE[key] = module
    return module


def build_runner_record(repository_root: Path) -> dict[str, Any]:
    return _core(repository_root).build_runner_record(repository_root)


def load_authenticated_plan(
    plan_path: Path,
    repository_root: Path,
    *,
    production: bool,
    committed_input_checker: Callable[[Path, Path], None] | None = None,
) -> Any:
    return _core(repository_root).load_authenticated_plan(
        plan_path,
        repository_root,
        production=production,
        committed_input_checker=committed_input_checker,
    )


def execute_custodied_schedule(*args: Any, **kwargs: Any) -> dict[str, Any]:
    if not args:
        raise ValidationRunnerError("authenticated plan is required")
    return _core(args[0].repository_root).execute_custodied_schedule(*args, **kwargs)


def main(argv: Sequence[str] | None = None) -> int:
    _ = argv
    print(
        "validation runner remains nonauthorizing; no plan read, output, host probe, or process is permitted",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
