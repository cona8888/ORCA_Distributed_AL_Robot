#!/usr/bin/env python3
"""Compile the prospective epoch-5 held-out validation missions in memory."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import sys
import types
from typing import Any


sys.dont_write_bytecode = True

SOURCE = {
    "bytes": 17294,
    "path": "scripts/comparison/generate_headless_task_calibration_missions_v1.py",
    "sha256": "b24ecc3abd9f936b133f821ea164207c4792e72ecd7ae725c23429529a9f5b91",
}
DESIGN = {
    "bytes": 2201,
    "path": "docs/simulator/headless_validation_epoch_5_design.json",
    "sha256": "e710fdfb5fde916bf73db6ce604ba7859ae486cbef9b2b6aa36e4f3863db1be3",
}
PROTOCOL = {
    "bytes": 1481,
    "path": "docs/simulator/HEADLESS_VALIDATION_EPOCH_5_ADDENDUM.md",
    "sha256": "3ce1df5d8b731ec9d65974f16c895179af336547caf23d4962d9919dad945e6c",
}
PRIOR_PLANS = (
    {
        "bytes": 5768480,
        "path": "comparison/plans/simulator-demo/v1/headless-epoch-1/plan.json",
        "sha256": "dbf1f06f1fcb3247181e630b5cdd030967ce749fd7b96d9655142a6826376e95",
    },
    {
        "bytes": 2795335,
        "path": "comparison/plans/simulator-demo/v1/headless-epoch-2-task-calibration/plan.json",
        "sha256": "f8579bbb29c2b6abece90ad86da6478edfee11dc36f7494881d1479ecd8b16a0",
    },
    {
        "bytes": 349797,
        "path": "comparison/plans/simulator-demo/v1/headless-epoch-3-fractal-low-task-calibration/plan.json",
        "sha256": "2eb98865b61d185d7df92b79445e978b4ccab574113437cc7f1bc3bdbd6f0636",
    },
)
EPOCH_ID = "simulator-demo-v1-headless-epoch-5-validation"
PHASE = "validation"
SCHEMA = "dancers-headless-validation-mission-manifest-v1"
ROOT_DOMAIN = b"dancers-headless-mission-bank-root-validation-v1\0"
STREAM_DOMAIN = b"dancers-headless-mission-bank-stream-validation-v1\0"
FAMILIES = ("open", "perfect_maze", "fractal_low", "fractal_medium", "fractal_high")
COUNTS = {
    "open": (8,),
    "perfect_maze": (2, 4, 6),
    "fractal_low": (2, 4),
    "fractal_medium": (2, 4),
    "fractal_high": (2, 4),
}
SEEDS = tuple(range(11, 31))


class ValidationMissionError(ValueError):
    """The frozen validation design or an authenticated input differs."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read(root: Path, identity: dict[str, Any], label: str) -> bytes:
    try:
        raw = root.joinpath(*PurePosixPath(identity["path"]).parts).read_bytes()
    except (OSError, ValueError) as error:
        raise ValidationMissionError(f"cannot read {label}: {error}") from error
    if len(raw) != identity["bytes"] or _sha(raw) != identity["sha256"]:
        raise ValidationMissionError(f"{label} identity differs")
    return raw


def _load_source(root: Path) -> types.ModuleType:
    raw = _read(root, SOURCE, "task mission compiler")
    name = "_dancers_validation_mission_core"
    module = types.ModuleType(name)
    module.__file__ = SOURCE["path"]
    sys.modules[name] = module
    try:
        exec(compile(raw, module.__file__, "exec", dont_inherit=True, optimize=0), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _validation_normalizer(base: types.ModuleType, design: Any) -> dict[str, Any]:
    value = base._require_exact_keys(design, {"phase", "requests", "schema_version"}, "mission design")
    if value["phase"] != PHASE or value["schema_version"] != base.DESIGN_SCHEMA:
        raise base.MissionCompileError("validation mission design identity differs")
    requests = value["requests"]
    if type(requests) is not list or not requests:
        raise base.MissionCompileError("validation requests differ")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for request in requests:
        row = base._require_exact_keys(request, {"counts", "family", "seed"}, "validation request")
        family, seed, counts = row["family"], row["seed"], row["counts"]
        if family not in FAMILIES or type(seed) is not int or seed not in SEEDS:
            raise base.MissionCompileError("validation family/seed differs")
        if counts != list(COUNTS[family]):
            raise base.MissionCompileError("validation count grid differs")
        if (family, seed) in seen:
            raise base.MissionCompileError("validation bank is duplicated")
        seen.add((family, seed))
        normalized.append({"counts": list(counts), "family": family, "seed": seed})
    rank = {family: index for index, family in enumerate(FAMILIES)}
    normalized.sort(key=lambda item: (rank[item["family"]], item["seed"]))
    if len(normalized) != 100:
        raise base.MissionCompileError("validation bank inventory differs")
    return {"phase": PHASE, "requests": normalized, "schema_version": base.DESIGN_SCHEMA}


def compile_validation_missions(repository_root: Path) -> Any:
    root = Path(os.path.abspath(repository_root))
    if Path(os.path.realpath(root)) != root:
        raise ValidationMissionError("repository root is not canonical")
    _read(root, DESIGN, "validation design")
    _read(root, PROTOCOL, "validation protocol")
    core = _load_source(root)
    original_load_base = core._load_base

    def load_base(validation_root: Path) -> types.ModuleType:
        base = original_load_base(validation_root)
        base.PHASE = PHASE
        base.normalize_design = lambda value: _validation_normalizer(base, value)
        return base

    core._load_base = load_base
    core.SCHEMA = SCHEMA
    core.DESIGN_SCHEMA = "dancers-headless-validation-epoch-5-design-v1"
    core.EPOCH_ID = EPOCH_ID
    core.PHASE = PHASE
    core.ROOT_DOMAIN = ROOT_DOMAIN
    core.STREAM_DOMAIN = STREAM_DOMAIN
    core.DESIGN_PATH = PurePosixPath(DESIGN["path"])
    core.PROTOCOL_PATH = PurePosixPath(PROTOCOL["path"])
    core.DESIGN_IDENTITY = dict(DESIGN)
    core.PROTOCOL_IDENTITY = dict(PROTOCOL)
    core.EXPECTED_FAMILIES = FAMILIES
    core.EXPECTED_COUNTS = COUNTS
    core.EXPECTED_SEEDS = SEEDS
    core.EXPECTED_BANKS = 100
    core.EXPECTED_SCENES = 200
    core.EXPECTED_OVERLAPS = 0
    compilation = core.compile_task_missions(root)
    core.validate_compilation(compilation)

    prior_hashes: set[str] = set()
    for identity in PRIOR_PLANS:
        plan = core._parse(_read(root, identity, "prior calibration plan"), "prior calibration plan")
        if type(plan) is not dict or type(plan.get("scenes")) is not list:
            raise ValidationMissionError("prior calibration scene inventory differs")
        prior_hashes.update(scene["effective_scenario_sha256"] for scene in plan["scenes"])
    fresh_hashes = {
        row["effective_scenario_sha256"]
        for artifact in compilation.base_compilation.manifest["artifacts"]
        for row in artifact["counts"]
    }
    if len(fresh_hashes) != 200 or fresh_hashes & prior_hashes:
        raise ValidationMissionError("validation effective input is not fresh")
    if compilation.manifest["inventory"] != {
        "bank_count": 100,
        "file_count_including_manifest": 302,
        "scene_count": 200,
    }:
        raise ValidationMissionError("validation inventory differs")
    if compilation.manifest["derivation"] != {
        "count_enters_root": False,
        "epoch_id": EPOCH_ID,
        "root_domain_hex": ROOT_DOMAIN.hex(),
        "stream_domain_hex": STREAM_DOMAIN.hex(),
    }:
        raise ValidationMissionError("validation derivation differs")
    return compilation


def main() -> int:
    raise ValidationMissionError("validation mission publication is categorically disabled")


if __name__ == "__main__":
    try:
        main()
    except ValidationMissionError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
