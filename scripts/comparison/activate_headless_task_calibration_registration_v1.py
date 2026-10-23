#!/usr/bin/env python3
"""Execute the one authorized epoch-2 plan publication and nothing else."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import types
from typing import Any


sys.dont_write_bytecode = True

AUTH_PATH = "docs/simulator/headless_task_calibration_epoch_2_registration_v1.json"
REGISTRAR_PATH = "scripts/comparison/register_headless_task_calibration_epoch_v1.py"
REGISTRAR_IDENTITY = {
    "bytes": 7629,
    "path": REGISTRAR_PATH,
    "sha256": "2877d089117411de7aee2018213e03cd1cbccedbcb4f7f7fc47217e1497132d5",
}
EXPECTED_PLAN = {
    "file_count": 423,
    "plan_bytes": 2795335,
    "plan_sha256": "f8579bbb29c2b6abece90ad86da6478edfee11dc36f7494881d1479ecd8b16a0",
    "total_bytes": 3704486,
}


class ActivationError(ValueError):
    """The one-use registration authority or exact output differs."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ActivationError(f"duplicate authorization key: {key}")
        value[key] = item
    return value


def _canonical(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _load_authorization(root: Path) -> dict[str, Any]:
    raw = (root / AUTH_PATH).read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except (UnicodeError, ValueError) as error:
        raise ActivationError(f"authorization is not strict JSON: {error}") from error
    if raw != _canonical(value):
        raise ActivationError("authorization is not canonical JSON")
    if (
        type(value) is not dict
        or value.get("schema_version")
        != "dancers-headless-task-calibration-registration-authorization-v1"
        or value.get("one_use") is not True
        or value.get("operation") != "publish-exact-registered-plan-only"
        or value.get("simulator_execution_authorized") is not False
        or value.get("result_write_authorized") is not False
        or value.get("plan") != EXPECTED_PLAN
        or value.get("registrar") != REGISTRAR_IDENTITY
    ):
        raise ActivationError("one-use registration authorization differs")
    return value


def _load_registrar(root: Path) -> types.ModuleType:
    raw = (root / REGISTRAR_PATH).read_bytes()
    if len(raw) != REGISTRAR_IDENTITY["bytes"] or _sha(raw) != REGISTRAR_IDENTITY["sha256"]:
        raise ActivationError("authorized registrar identity differs")
    module = types.ModuleType("_dancers_authorized_task_registrar")
    module.__file__ = REGISTRAR_PATH
    sys.modules[module.__name__] = module
    try:
        exec(compile(raw, REGISTRAR_PATH, "exec", dont_inherit=True, optimize=0), module.__dict__)
    except BaseException:
        sys.modules.pop(module.__name__, None)
        raise
    return module


def _require_clean_committed(root: Path) -> None:
    completed = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={"LC_ALL": "C", "PATH": os.environ.get("PATH", "")},
    )
    if completed.returncode != 0 or completed.stdout != b"":
        raise ActivationError("tracked repository state is not clean")
    for relative in (AUTH_PATH, REGISTRAR_PATH, __file__):
        path = Path(relative)
        if path.is_absolute():
            relative = path.relative_to(root).as_posix()
        committed = subprocess.run(
            ["git", "show", f"HEAD:{relative}"],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            env={"LC_ALL": "C", "PATH": os.environ.get("PATH", "")},
        )
        if committed.returncode != 0 or committed.stdout != (root / relative).read_bytes():
            raise ActivationError(f"{relative} is not exact committed HEAD content")


def execute_one_use_registration(repository_root: Path) -> dict[str, Any]:
    root = Path(os.path.abspath(repository_root))
    if Path(os.path.realpath(root)) != root:
        raise ActivationError("repository root is not canonical")
    authorization = _load_authorization(root)
    _require_clean_committed(root)
    registrar = _load_registrar(root)
    compilation = registrar.prepare_registered_compilation(root)
    observed = {
        "file_count": len(compilation.files),
        "plan_bytes": len(compilation.data),
        "plan_sha256": compilation.sha256,
        "total_bytes": compilation.total_bytes,
    }
    if observed != EXPECTED_PLAN:
        raise ActivationError("prepared registered plan differs from authorization")
    receipt = registrar.publish_registered_create_exclusive(root, compilation)
    if any(receipt[key] != EXPECTED_PLAN[key] for key in EXPECTED_PLAN):
        raise ActivationError("published registered plan differs from authorization")
    return {
        "authorization_sha256": _sha(_canonical(authorization)),
        "classification": {
            "result_write_performed": False,
            "scientific_process_spawned": False,
            "simulator_invoked": False,
        },
        "operation": "publish-exact-registered-plan-only",
        "plan": dict(EXPECTED_PLAN),
        "schema_version": "dancers-headless-task-calibration-registration-activation-v1",
    }


def main() -> int:
    try:
        receipt = execute_one_use_registration(Path(__file__).resolve().parents[2])
    except (ActivationError, OSError, ValueError) as error:
        print(f"task-calibration registration rejected: {error}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(_canonical(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
