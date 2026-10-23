#!/usr/bin/env python3
"""Execute the one committed epoch-2 task-calibration session."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import types
from typing import Any


sys.dont_write_bytecode = True

AUTH_PATH = "docs/simulator/headless_task_calibration_epoch_2_execution_v1.json"
RUNNER_PATH = "scripts/comparison/run_headless_task_calibration_epoch_v1.py"
RUNNER_IDENTITY = {
    "bytes": 13257,
    "path": RUNNER_PATH,
    "sha256": "6fa4c6bd5a75662f9c07e49a8043e68b5242f063a923434eb001518052a76cff",
}
ANALYZER_IDENTITY = {
    "bytes": 10612,
    "path": "scripts/comparison/analyze_headless_task_calibration_epoch_v2.py",
    "sha256": "90184be7b38413745d0629433943aafaa78ce8c6abc8ffbfd087b2b7fdf06afe",
}
PLAN = {
    "bytes": 2795335,
    "path": "comparison/plans/simulator-demo/v1/headless-epoch-2-task-calibration/plan.json",
    "sha256": "f8579bbb29c2b6abece90ad86da6478edfee11dc36f7494881d1479ecd8b16a0",
}
RESULT_ROOT = PurePosixPath(
    "results/simulator-demo/v1/headless-scientific/epoch-2-task-calibration"
)
SESSION_NAME = "task-calibration-session-001"
EXPECTED_PROCESSES = 1450


class ExecutionActivationError(ValueError):
    """The execution authority or exact plan/session binding differs."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ExecutionActivationError(f"duplicate authorization key: {key}")
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
        raise ExecutionActivationError(f"execution authorization is not strict JSON: {error}") from error
    if raw != _canonical(value):
        raise ExecutionActivationError("execution authorization is not canonical JSON")
    if (
        value.get("schema_version")
        != "dancers-headless-task-calibration-execution-authorization-v1"
        or value.get("one_use") is not True
        or value.get("operation") != "execute-exact-task-only-session"
        or value.get("plan") != PLAN
        or value.get("runner") != RUNNER_IDENTITY
        or value.get("analyzer") != ANALYZER_IDENTITY
        or value.get("session_name") != SESSION_NAME
        or value.get("expected_process_count") != EXPECTED_PROCESSES
        or value.get("timing_execution_authorized") is not False
        or value.get("validation_execution_authorized") is not False
        or value.get("benchmark_execution_authorized") is not False
    ):
        raise ExecutionActivationError("execution authorization differs")
    return value


def _load_runner(root: Path) -> types.ModuleType:
    raw = (root / RUNNER_PATH).read_bytes()
    if len(raw) != RUNNER_IDENTITY["bytes"] or _sha(raw) != RUNNER_IDENTITY["sha256"]:
        raise ExecutionActivationError("authorized runner identity differs")
    module = types.ModuleType("_dancers_authorized_task_runner")
    module.__file__ = RUNNER_PATH
    sys.modules[module.__name__] = module
    try:
        exec(compile(raw, RUNNER_PATH, "exec", dont_inherit=True, optimize=0), module.__dict__)
    except BaseException:
        sys.modules.pop(module.__name__, None)
        raise
    return module


def _git(root: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={"LC_ALL": "C", "PATH": os.environ.get("PATH", "")},
    )
    if completed.returncode != 0:
        raise ExecutionActivationError(f"git {' '.join(args)} failed")
    return completed.stdout


def _require_clean_committed(root: Path) -> None:
    if _git(root, "status", "--porcelain", "--untracked-files=no") != b"":
        raise ExecutionActivationError("tracked repository state is not clean")
    for path in (AUTH_PATH, RUNNER_PATH, ANALYZER_IDENTITY["path"], PLAN["path"], __file__):
        candidate = Path(path)
        relative = candidate.relative_to(root).as_posix() if candidate.is_absolute() else candidate.as_posix()
        if _git(root, "show", f"HEAD:{relative}") != (root / relative).read_bytes():
            raise ExecutionActivationError(f"{relative} is not exact committed HEAD content")


def _committed_checker(plan_path: Path, root: Path) -> None:
    relative = plan_path.relative_to(root).as_posix()
    if _git(root, "show", f"HEAD:{relative}") != plan_path.read_bytes():
        raise ExecutionActivationError("registered plan is not exact committed HEAD content")


def execute_one_use_session(repository_root: Path) -> dict[str, Any]:
    root = Path(os.path.abspath(repository_root))
    if Path(os.path.realpath(root)) != root:
        raise ExecutionActivationError("repository root is not canonical")
    authorization = _load_authorization(root)
    _require_clean_committed(root)
    analyzer_raw = (root / ANALYZER_IDENTITY["path"]).read_bytes()
    if len(analyzer_raw) != ANALYZER_IDENTITY["bytes"] or _sha(analyzer_raw) != ANALYZER_IDENTITY["sha256"]:
        raise ExecutionActivationError("prospective analyzer identity differs")
    runner = _load_runner(root)
    result_root = root.joinpath(*RESULT_ROOT.parts)
    if result_root.exists() or result_root.is_symlink():
        raise ExecutionActivationError("task-calibration result root already exists")
    result_root.mkdir(mode=0o755, parents=False, exist_ok=False)
    plan_path = root / PLAN["path"]
    try:
        authenticated = runner.load_authenticated_plan(
            plan_path,
            root,
            production=True,
            committed_input_checker=_committed_checker,
        )
        if (
            authenticated.plan_sha256 != PLAN["sha256"]
            or len(authenticated.schedule) != EXPECTED_PROCESSES
            or any(item.run_class not in runner.RUN_CLASSES for item in authenticated.schedule)
            or any(item.raw["timed"] is not False for item in authenticated.schedule)
        ):
            raise ExecutionActivationError("authenticated execution schedule differs")
        session_path = result_root / SESSION_NAME
        session = runner.execute_custodied_schedule(authenticated, session_path)
    except BaseException:
        if result_root.exists() and not any(result_root.iterdir()):
            result_root.rmdir()
        raise
    return {
        "authorization_sha256": _sha(_canonical(authorization)),
        "classification": {
            "benchmark_execution_performed": False,
            "timing_execution_performed": False,
            "validation_execution_performed": False,
        },
        "expected_process_count": EXPECTED_PROCESSES,
        "observed_attempt_count": session["attempt_count"],
        "operation": "execute-exact-task-only-session",
        "plan_sha256": authenticated.plan_sha256,
        "schema_version": "dancers-headless-task-calibration-execution-receipt-v1",
        "session": (RESULT_ROOT / SESSION_NAME).as_posix(),
        "session_aborted": session["aborted"],
    }


def main() -> int:
    try:
        receipt = execute_one_use_session(Path(__file__).resolve().parents[2])
    except BaseException as error:
        print(f"task-calibration execution rejected: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(_canonical(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
