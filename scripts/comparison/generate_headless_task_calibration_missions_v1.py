#!/usr/bin/env python3
"""Compile fresh epoch-2 task-calibration missions entirely in memory.

The audited epoch-1 compiler remains the geometry implementation.  This
successor authenticates and executes its exact source bytes, replaces only the
declared count-independent root/counter domains, and wraps the result in an
epoch-2 receipt.  It has no publication or simulator surface.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import types
from typing import Any, Mapping


sys.dont_write_bytecode = True

SCHEMA = "dancers-headless-task-calibration-mission-manifest-v1"
DESIGN_SCHEMA = "dancers-headless-task-calibration-design-v1"
EPOCH_ID = "simulator-demo-v1-headless-epoch-2-task-calibration"
PHASE = "calibration"
ROOT_DOMAIN = b"dancers-headless-mission-bank-root-v2"
STREAM_DOMAIN = b"dancers-headless-mission-bank-stream-v2\0"
DESIGN_PATH = PurePosixPath(
    "docs/simulator/headless_task_calibration_epoch_2_design.json"
)
PROTOCOL_PATH = PurePosixPath(
    "docs/simulator/HEADLESS_TASK_CALIBRATION_EPOCH_2_ADDENDUM.md"
)
BASE_COMPILER_PATH = PurePosixPath(
    "scripts/comparison/generate_headless_mission_banks_v1.py"
)
EPOCH1_PLAN_PATH = PurePosixPath(
    "comparison/plans/simulator-demo/v1/headless-epoch-1/plan.json"
)
DESIGN_IDENTITY = {
    "bytes": 3284,
    "path": DESIGN_PATH.as_posix(),
    "sha256": "c26e247713740b7279470f8ee4fa0be31a3ccaa25781077adac6dbf879053ab9",
}
PROTOCOL_IDENTITY = {
    "bytes": 5287,
    "path": PROTOCOL_PATH.as_posix(),
    "sha256": "1b25625be91f55b7f394dabbe4f28e1b88cc78c129cf1aac6c7c1d3d95d8917c",
}
BASE_COMPILER_IDENTITY = {
    "bytes": 88887,
    "path": BASE_COMPILER_PATH.as_posix(),
    "sha256": "4c3db3a38db71f170e016543c2235b4c621146f8769e560c3311ede3e04f998e",
}
EPOCH1_PLAN_IDENTITY = {
    "bytes": 5768480,
    "path": EPOCH1_PLAN_PATH.as_posix(),
    "sha256": "dbf1f06f1fcb3247181e630b5cdd030967ce749fd7b96d9655142a6826376e95",
}
EXPECTED_FAMILIES = (
    "open",
    "perfect_maze",
    "fractal_low",
    "fractal_medium",
    "fractal_high",
)
EXPECTED_COUNTS = {
    "open": (8, 10, 12),
    "perfect_maze": (2, 4, 6, 8),
    "fractal_low": (6, 8, 10),
    "fractal_medium": (2, 4, 6, 8),
    "fractal_high": (2, 4, 6, 8),
}
EXPECTED_SEEDS = tuple(range(1, 11))
EXPECTED_BANKS = 50
EXPECTED_SCENES = 180
EXPECTED_OVERLAPS = 60
MAX_READ_BYTES = 16 * 1024 * 1024
HEX = frozenset("0123456789abcdef")


class TaskMissionError(ValueError):
    """The prospective design or an authenticated input differs."""


@dataclass(frozen=True)
class CompiledFile:
    relative_path: PurePosixPath
    data: bytes
    sha256: str
    kind: str


@dataclass(frozen=True)
class TaskMissionCompilation:
    design: dict[str, Any]
    files: tuple[CompiledFile, ...]
    manifest: dict[str, Any]
    manifest_bytes: bytes
    manifest_sha256: str
    base_compilation: Any

    def by_path(self) -> dict[PurePosixPath, CompiledFile]:
        return {item.relative_path: item for item in self.files}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical(value: object) -> bytes:
    try:
        return (
            json.dumps(
                value,
                allow_nan=False,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
    except (RecursionError, TypeError, UnicodeError, ValueError) as error:
        raise TaskMissionError(f"value is not canonical JSON: {error}") from error


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise TaskMissionError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _parse(raw: bytes, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
        value = json.loads(
            text,
            object_pairs_hook=_pairs,
            parse_constant=lambda token: (_ for _ in ()).throw(
                TaskMissionError(f"{label} contains {token}")
            ),
        )
    except TaskMissionError:
        raise
    except (RecursionError, UnicodeError, ValueError) as error:
        raise TaskMissionError(f"{label} is not strict JSON: {error}") from error
    return value


def _read_regular(root: Path, relative: PurePosixPath, identity: Mapping[str, Any]) -> bytes:
    if relative.is_absolute() or ".." in relative.parts or not relative.parts:
        raise TaskMissionError("authenticated path is not repository-relative")
    directory_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY
    file_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
    try:
        parent = os.open(root, directory_flags)
    except OSError as error:
        raise TaskMissionError(f"cannot open repository root: {error}") from error
    try:
        for component in relative.parts[:-1]:
            child = os.open(component, directory_flags, dir_fd=parent)
            os.close(parent)
            parent = child
        descriptor = os.open(relative.parts[-1], file_flags, dir_fd=parent)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise TaskMissionError(f"{relative} is not a single-link regular file")
            if before.st_size != identity["bytes"] or before.st_size > MAX_READ_BYTES:
                raise TaskMissionError(f"{relative} byte count differs")
            chunks: list[bytes] = []
            remaining = before.st_size
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise TaskMissionError(f"{relative} ended early")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise TaskMissionError(f"{relative} grew during read")
            after = os.fstat(descriptor)
            if (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise TaskMissionError(f"{relative} changed during read")
            raw = b"".join(chunks)
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)
    if _sha256(raw) != identity["sha256"]:
        raise TaskMissionError(f"{relative} SHA-256 differs")
    return raw


def _load_base(root: Path) -> types.ModuleType:
    raw = _read_regular(root, BASE_COMPILER_PATH, BASE_COMPILER_IDENTITY)
    name = "_dancers_epoch2_authenticated_base_mission_compiler"
    module = types.ModuleType(name)
    module.__file__ = BASE_COMPILER_PATH.as_posix()
    sys.modules[name] = module
    try:
        code = compile(raw, module.__file__, "exec", dont_inherit=True, optimize=0)
        exec(code, module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    if (
        getattr(module, "DESIGN_SCHEMA", None) != "dancers-headless-mission-design-v1"
        or not callable(getattr(module, "compile_mission_banks", None))
        or not callable(getattr(module, "validate_compilation", None))
    ):
        raise TaskMissionError("authenticated base compiler contract differs")
    return module


def _validate_design(value: Any) -> dict[str, Any]:
    if type(value) is not dict or value.get("schema_version") != DESIGN_SCHEMA:
        raise TaskMissionError("epoch-2 design schema differs")
    if value.get("epoch_id") != EPOCH_ID or value.get("phase") != PHASE:
        raise TaskMissionError("epoch-2 design identity differs")
    if type(value.get("authority")) is not dict or any(
        item is not False for item in value["authority"].values()
    ):
        raise TaskMissionError("epoch-2 design grants authority")
    requests = value.get("requests")
    if type(requests) is not list or len(requests) != len(EXPECTED_FAMILIES):
        raise TaskMissionError("epoch-2 request inventory differs")
    for request, family in zip(requests, EXPECTED_FAMILIES):
        if type(request) is not dict or set(request) != {"counts", "family", "seeds"}:
            raise TaskMissionError("epoch-2 request shape differs")
        if request["family"] != family:
            raise TaskMissionError("epoch-2 family order differs")
        if request["counts"] != list(EXPECTED_COUNTS[family]):
            raise TaskMissionError(f"epoch-2 {family} count ladder differs")
        if request["seeds"] != list(EXPECTED_SEEDS):
            raise TaskMissionError(f"epoch-2 {family} seed inventory differs")
    return value


def _fresh_generator_seed(family: str, seed: int, map_sha256: str) -> int:
    if family not in EXPECTED_FAMILIES or type(seed) is not int or seed not in EXPECTED_SEEDS:
        raise TaskMissionError("fresh generator seed inputs differ")
    if (
        type(map_sha256) is not str
        or len(map_sha256) != 64
        or any(character not in HEX for character in map_sha256)
    ):
        raise TaskMissionError("fresh generator map SHA-256 differs")
    material = b"\0".join(
        (
            ROOT_DOMAIN,
            EPOCH_ID.encode("ascii"),
            PHASE.encode("ascii"),
            family.encode("ascii"),
            str(seed).encode("ascii"),
            map_sha256.encode("ascii"),
        )
    )
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def _identity(path: PurePosixPath, data: bytes) -> dict[str, Any]:
    return {"bytes": len(data), "path": path.as_posix(), "sha256": _sha256(data)}


def compile_task_missions(repository_root: Path) -> TaskMissionCompilation:
    """Return the complete fresh mission inventory without writing a file."""

    root = Path(os.path.abspath(repository_root))
    if Path(os.path.realpath(root)) != root:
        raise TaskMissionError("repository root is not canonical")
    design_raw = _read_regular(root, DESIGN_PATH, DESIGN_IDENTITY)
    design = _validate_design(_parse(design_raw, "epoch-2 design"))
    if design_raw != json.dumps(
        design, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8") + b"\n":
        raise TaskMissionError("epoch-2 design is not canonical JSON")
    protocol_raw = _read_regular(root, PROTOCOL_PATH, PROTOCOL_IDENTITY)
    epoch1_raw = _read_regular(root, EPOCH1_PLAN_PATH, EPOCH1_PLAN_IDENTITY)
    epoch1 = _parse(epoch1_raw, "epoch-1 plan")
    if type(epoch1) is not dict or type(epoch1.get("scenes")) is not list:
        raise TaskMissionError("epoch-1 plan scene inventory differs")

    base = _load_base(root)
    base.SEED_DOMAIN = STREAM_DOMAIN + EPOCH_ID.encode("ascii") + b"\0"
    base.generator_seed_uint64 = _fresh_generator_seed
    translated = {
        "phase": PHASE,
        "requests": [
            {"counts": list(EXPECTED_COUNTS[family]), "family": family, "seed": seed}
            for family in EXPECTED_FAMILIES
            for seed in EXPECTED_SEEDS
        ],
        "schema_version": base.DESIGN_SCHEMA,
    }
    try:
        base_compilation = base.compile_mission_banks(root, translated)
        base.validate_compilation(base_compilation)
    except Exception as error:
        raise TaskMissionError(f"authenticated base compilation failed: {error}") from error

    old_effective = {
        (scene["family"], scene["seed"], scene["robot_count"]): scene[
            "effective_scenario_sha256"
        ]
        for scene in epoch1["scenes"]
    }
    overlap_rows: list[dict[str, Any]] = []
    bank_count = 0
    scene_count = 0
    for artifact in base_compilation.manifest["artifacts"]:
        bank_count += 1
        for row in artifact["counts"]:
            scene_count += 1
            key = (artifact["family"], artifact["seed"], row["robot_count"])
            if key in old_effective:
                fresh = row["effective_scenario_sha256"]
                if fresh == old_effective[key]:
                    raise TaskMissionError("epoch-2 scenario reuses epoch-1 effective input")
                overlap_rows.append(
                    {
                        "epoch_1_effective_scenario_sha256": old_effective[key],
                        "epoch_2_effective_scenario_sha256": fresh,
                        "family": key[0],
                        "robot_count": key[2],
                        "seed": key[1],
                    }
                )
    if (bank_count, scene_count, len(overlap_rows)) != (
        EXPECTED_BANKS,
        EXPECTED_SCENES,
        EXPECTED_OVERLAPS,
    ):
        raise TaskMissionError("compiled inventory counts differ")

    output_files: list[CompiledFile] = [
        CompiledFile(
            PurePosixPath("design.json"),
            design_raw,
            _sha256(design_raw),
            "design",
        )
    ]
    artifact_rows: list[dict[str, Any]] = []
    for item in base_compilation.files:
        if item.kind in {"design", "manifest"}:
            continue
        compiled = CompiledFile(item.relative_path, item.data, item.sha256, item.kind)
        output_files.append(compiled)
        artifact_rows.append(
            {
                "bytes": len(item.data),
                "kind": item.kind,
                "path": item.relative_path.as_posix(),
                "sha256": item.sha256,
            }
        )
    manifest: dict[str, Any] = {
        "artifacts": artifact_rows,
        "base_compilation": {
            "compiler": dict(BASE_COMPILER_IDENTITY),
            "design_sha256": base_compilation.design_sha256,
            "manifest_sha256": base_compilation.manifest_sha256,
        },
        "classification": {
            "algorithm_execution_during_compilation": False,
            "authorizes_scientific_execution": False,
            "mission_publication_authorized": False,
            "network_accessed": False,
            "plan_freeze": False,
            "result_evidence": False,
        },
        "derivation": {
            "count_enters_root": False,
            "epoch_id": EPOCH_ID,
            "root_domain_hex": ROOT_DOMAIN.hex(),
            "stream_domain_hex": STREAM_DOMAIN.hex(),
        },
        "design": _identity(PurePosixPath("design.json"), design_raw),
        "epoch_1_overlap_nonidentity": {
            "epoch_1_plan": dict(EPOCH1_PLAN_IDENTITY),
            "overlap_count": len(overlap_rows),
            "rows": overlap_rows,
        },
        "inventory": {
            "bank_count": bank_count,
            "file_count_including_manifest": 2 + bank_count + scene_count,
            "scene_count": scene_count,
        },
        "protocol": _identity(PROTOCOL_PATH, protocol_raw),
        "schema_version": SCHEMA,
    }
    manifest_bytes = _canonical(manifest)
    output_files.append(
        CompiledFile(PurePosixPath("manifest.json"), manifest_bytes, _sha256(manifest_bytes), "manifest")
    )
    result = TaskMissionCompilation(
        design=design,
        files=tuple(output_files),
        manifest=manifest,
        manifest_bytes=manifest_bytes,
        manifest_sha256=_sha256(manifest_bytes),
        base_compilation=base_compilation,
    )
    validate_compilation(result)
    return result


def validate_compilation(compilation: TaskMissionCompilation) -> None:
    if type(compilation) is not TaskMissionCompilation:
        raise TaskMissionError("compilation type differs")
    if compilation.manifest_bytes != _canonical(compilation.manifest):
        raise TaskMissionError("manifest bytes differ")
    if compilation.manifest_sha256 != _sha256(compilation.manifest_bytes):
        raise TaskMissionError("manifest SHA-256 differs")
    if any(compilation.manifest["classification"].values()):
        raise TaskMissionError("compilation classification grants authority")
    if compilation.manifest["inventory"] != {
        "bank_count": EXPECTED_BANKS,
        "file_count_including_manifest": 2 + EXPECTED_BANKS + EXPECTED_SCENES,
        "scene_count": EXPECTED_SCENES,
    }:
        raise TaskMissionError("manifest inventory differs")
    if len(compilation.manifest["epoch_1_overlap_nonidentity"]["rows"]) != EXPECTED_OVERLAPS:
        raise TaskMissionError("overlap proof inventory differs")
    paths: set[PurePosixPath] = set()
    for item in compilation.files:
        if item.relative_path in paths:
            raise TaskMissionError("compiled path is duplicated")
        paths.add(item.relative_path)
        if item.sha256 != _sha256(item.data):
            raise TaskMissionError("compiled file SHA-256 differs")
    if len(compilation.files) != 2 + EXPECTED_BANKS + EXPECTED_SCENES:
        raise TaskMissionError("compiled file count differs")


def main() -> int:
    raise TaskMissionError(
        "epoch-2 mission publication is categorically disabled at this checkpoint"
    )


if __name__ == "__main__":
    try:
        main()
    except TaskMissionError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
