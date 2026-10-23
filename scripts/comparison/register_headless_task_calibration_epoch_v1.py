#!/usr/bin/env python3
"""Prepare and atomically publish the registered epoch-2 task-only plan.

The public entry point is disabled.  A later committed one-use authorization
may call the private publisher after authenticating these exact source bytes.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sys
import types
from typing import Any, Mapping


sys.dont_write_bytecode = True

PLAN_ROOT = PurePosixPath(
    "comparison/plans/simulator-demo/v1/headless-epoch-2-task-calibration"
)
FREEZER_PATH = PurePosixPath(
    "scripts/comparison/freeze_headless_task_calibration_epoch_v1.py"
)
FREEZER_IDENTITY = {
    "bytes": 29960,
    "path": FREEZER_PATH.as_posix(),
    "sha256": "a05d9907ab0422e096fa9e0bba92642c0a18af303feccc85c8880c21789c14c5",
}
REGISTRATION_SCHEMA = "dancers-headless-task-calibration-registration-receipt-v1"
MAX_PLAN_FILES = 1000
MAX_PLAN_BYTES = 64 * 1024 * 1024


class RegistrationError(ValueError):
    """The plan compilation, destination, or publication differs."""


@dataclass(frozen=True)
class RegisteredCompilation:
    value: dict[str, Any]
    data: bytes
    sha256: str
    files: tuple[Any, ...]
    total_bytes: int


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
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


def _load_freezer(root: Path) -> types.ModuleType:
    path = root.joinpath(*FREEZER_PATH.parts)
    raw = path.read_bytes()
    if len(raw) != FREEZER_IDENTITY["bytes"] or _sha(raw) != FREEZER_IDENTITY["sha256"]:
        raise RegistrationError("task-only freezer identity differs")
    name = "_dancers_task_registration_freezer"
    module = types.ModuleType(name)
    module.__file__ = FREEZER_PATH.as_posix()
    sys.modules[name] = module
    try:
        exec(compile(raw, module.__file__, "exec", dont_inherit=True, optimize=0), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def prepare_registered_compilation(repository_root: Path) -> RegisteredCompilation:
    root = Path(os.path.abspath(repository_root))
    if Path(os.path.realpath(root)) != root:
        raise RegistrationError("repository root is not canonical")
    freezer = _load_freezer(root)
    candidate = freezer.compile_epoch(root)
    value = json.loads(freezer.canonical_json_bytes(candidate.value))
    value["classification"] = dict(freezer.REGISTERED_CLASSIFICATION)
    value["status"] = dict(freezer.REGISTERED_STATUS)
    value["operations"]["plan_written"] = True
    data = freezer.canonical_json_bytes(value)
    freezer.validate_epoch_bytes(data)
    files = tuple(
        replace(item, data=data, sha256=_sha(data))
        if item.relative_path == PurePosixPath("plan.json")
        else item
        for item in candidate.files
    )
    if len(files) > MAX_PLAN_FILES:
        raise RegistrationError("registered plan file count exceeds the fixed bound")
    paths: set[PurePosixPath] = set()
    total = 0
    for item in files:
        path = item.relative_path
        if (
            type(path) is not PurePosixPath
            or path.is_absolute()
            or not path.parts
            or ".." in path.parts
            or path in paths
            or item.sha256 != _sha(item.data)
        ):
            raise RegistrationError("registered plan file inventory differs")
        paths.add(path)
        total += len(item.data)
    if total > MAX_PLAN_BYTES:
        raise RegistrationError("registered plan exceeds the fixed byte bound")
    return RegisteredCompilation(value, data, _sha(data), files, total)


def _write_file(root: Path, relative: PurePosixPath, data: bytes) -> None:
    path = root.joinpath(*relative.parts)
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    if path.is_symlink():
        raise RegistrationError("plan file destination is a symlink")
    try:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise RegistrationError("plan file destination already exists") from error


def _verify_tree(root: Path, compilation: RegisteredCompilation) -> None:
    expected = {item.relative_path.as_posix(): item for item in compilation.files}
    observed = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
    }
    if observed != set(expected):
        raise RegistrationError("published plan inventory differs")
    for path, item in expected.items():
        raw = root.joinpath(*PurePosixPath(path).parts).read_bytes()
        if raw != item.data or _sha(raw) != item.sha256:
            raise RegistrationError("published plan bytes differ")


def publish_registered_create_exclusive(
    repository_root: Path,
    compilation: RegisteredCompilation,
) -> dict[str, Any]:
    """Create the one exact plan tree, rolling it back on every ordinary fault."""

    root = Path(os.path.abspath(repository_root))
    expected = root.joinpath(*PLAN_ROOT.parts)
    if expected.exists() or expected.is_symlink():
        raise RegistrationError("registered plan root already exists")
    created = False
    try:
        expected.mkdir(mode=0o755, parents=False, exist_ok=False)
        created = True
        for item in sorted(compilation.files, key=lambda row: row.relative_path.as_posix()):
            _write_file(expected, item.relative_path, item.data)
        _verify_tree(expected, compilation)
        for directory, _names, files in os.walk(expected, topdown=False):
            for name in files:
                os.chmod(Path(directory) / name, 0o444, follow_symlinks=False)
            os.chmod(directory, 0o555, follow_symlinks=False)
        _verify_tree(expected, compilation)
    except BaseException as primary:
        if created:
            try:
                for directory, names, _files in os.walk(expected):
                    os.chmod(directory, 0o755, follow_symlinks=False)
                    for name in names:
                        path = Path(directory) / name
                        if path.is_dir() and not path.is_symlink():
                            os.chmod(path, 0o755, follow_symlinks=False)
                shutil.rmtree(expected)
            except BaseException as cleanup:
                try:
                    primary.add_note(f"registration rollback also failed: {cleanup}")
                except Exception:
                    pass
        raise
    return {
        "classification": {
            "execution_authorized": False,
            "scientific_evidence": False,
            "simulator_invoked": False,
        },
        "file_count": len(compilation.files),
        "plan_bytes": len(compilation.data),
        "plan_path": (PLAN_ROOT / "plan.json").as_posix(),
        "plan_sha256": compilation.sha256,
        "schema_version": REGISTRATION_SCHEMA,
        "total_bytes": compilation.total_bytes,
    }


def main() -> int:
    raise RegistrationError(
        "task-calibration plan registration is disabled pending a committed one-use authorization"
    )


if __name__ == "__main__":
    try:
        main()
    except RegistrationError as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
