#!/usr/bin/env python3
"""Retry the zero-process epoch-3 execution after one exact gate repair."""

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

BASE = {
    "bytes": 2945,
    "path": "scripts/comparison/activate_headless_task_calibration_execution_v2.py",
    "sha256": "2a4abd71987dba63313df615ba7199f8414120a33f966699a625c4183522c94c",
}
REPAIR_PATH = PurePosixPath(
    "docs/simulator/headless_task_calibration_epoch_3_execution_repair_v1.json"
)


class ExecutionRepairError(ValueError):
    """The zero-effect retry authority or repaired gate differs."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return (json.dumps(value, allow_nan=False, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def _load_base(root: Path) -> types.ModuleType:
    raw = root.joinpath(*PurePosixPath(BASE["path"]).parts).read_bytes()
    if len(raw) != BASE["bytes"] or _sha(raw) != BASE["sha256"]:
        raise ExecutionRepairError("original activation identity differs")
    name = "_dancers_epoch3_repaired_execution_base"
    module = types.ModuleType(name)
    module.__file__ = BASE["path"]
    sys.modules[name] = module
    exec(compile(raw, module.__file__, "exec", dont_inherit=True, optimize=0), module.__dict__)
    return module


def _load_repair(root: Path) -> dict[str, Any]:
    raw = root.joinpath(*REPAIR_PATH.parts).read_bytes()
    value = json.loads(raw.decode("utf-8"))
    if raw != _canonical(value):
        raise ExecutionRepairError("repair authorization is not canonical JSON")
    self_raw = Path(__file__).resolve().read_bytes()
    if (
        value.get("schema_version") != "dancers-headless-task-calibration-execution-repair-v1"
        or value.get("one_use_retry") is not True
        or value.get("prior_attempt").get("simulator_process_count") != 0
        or value.get("prior_attempt").get("result_namespace_state") != "absent"
        or value.get("repair_scope") != "separate-registration-from-one-use-execution-authority"
        or value.get("activation") != {
            "bytes": len(self_raw),
            "path": Path(__file__).resolve().relative_to(root).as_posix(),
            "sha256": _sha(self_raw),
        }
        or value.get("timing_execution_authorized") is not False
        or value.get("validation_execution_authorized") is not False
        or value.get("benchmark_execution_authorized") is not False
    ):
        raise ExecutionRepairError("repair authorization differs")
    return value


def _require_committed(root: Path) -> None:
    environment = {"LC_ALL": "C", "PATH": os.environ.get("PATH", "")}
    for relative in (REPAIR_PATH.as_posix(), Path(__file__).resolve().relative_to(root).as_posix()):
        completed = subprocess.run(
            ["git", "show", f"HEAD:{relative}"], cwd=root, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0 or completed.stdout != (root / relative).read_bytes():
            raise ExecutionRepairError(f"{relative} is not exact committed HEAD content")


def execute_repaired_one_use_session(repository_root: Path) -> dict[str, Any]:
    root = Path(repository_root).resolve()
    repair = _load_repair(root)
    _require_committed(root)
    wrapper = _load_base(root)
    core = wrapper._core(root)
    authorization = core._load_authorization(root)
    core._require_clean_committed(root)
    analyzer_raw = (root / core.ANALYZER_IDENTITY["path"]).read_bytes()
    if len(analyzer_raw) != core.ANALYZER_IDENTITY["bytes"] or _sha(analyzer_raw) != core.ANALYZER_IDENTITY["sha256"]:
        raise ExecutionRepairError("prospective analyzer identity differs")
    runner = core._load_runner(root)
    original_load = runner.load_authenticated_plan

    def authorized_load(plan_path: Path, passed_root: Path, *, production: bool, committed_input_checker: Any = None) -> Any:
        authenticated = original_load(plan_path, passed_root, production=False)
        if production:
            classification = authenticated.plan["classification"]
            status = authenticated.plan["status"]
            if (
                classification.get("plan_registered") is not True
                or classification.get("execution_authorized") is not False
                or classification.get("calibration_execution_authorized") is not False
                or status.get("registration_state") != "registered-committed"
                or committed_input_checker is None
            ):
                raise ExecutionRepairError("registered nonauthorizing plan state differs")
            committed_input_checker(Path(plan_path), Path(passed_root))
        return authenticated

    runner.load_authenticated_plan = authorized_load
    result_root = root.joinpath(*core.RESULT_ROOT.parts)
    if result_root.exists() or result_root.is_symlink():
        raise ExecutionRepairError("epoch-3 result root already exists")
    result_root.mkdir(mode=0o755, parents=False, exist_ok=False)
    try:
        authenticated = runner.load_authenticated_plan(
            root / core.PLAN["path"], root, production=True,
            committed_input_checker=core._committed_checker,
        )
        if authenticated.plan_sha256 != core.PLAN["sha256"] or len(authenticated.schedule) != core.EXPECTED_PROCESSES:
            raise ExecutionRepairError("authorized epoch-3 schedule differs")
        session = runner.execute_custodied_schedule(authenticated, result_root / core.SESSION_NAME)
    except BaseException:
        if result_root.exists() and not any(result_root.iterdir()):
            result_root.rmdir()
        raise
    return {
        "authorization_sha256": _sha(_canonical(authorization)),
        "benchmark_execution_performed": False,
        "expected_process_count": core.EXPECTED_PROCESSES,
        "observed_attempt_count": session["attempt_count"],
        "operation": "execute-repaired-exact-task-only-session",
        "repair_authorization_sha256": _sha(_canonical(repair)),
        "schema_version": "dancers-headless-task-calibration-execution-receipt-v2",
        "session": (core.RESULT_ROOT / core.SESSION_NAME).as_posix(),
        "session_aborted": session["aborted"],
        "timing_execution_performed": False,
        "validation_execution_performed": False,
    }


def main() -> int:
    try:
        receipt = execute_repaired_one_use_session(Path(__file__).resolve().parents[2])
    except BaseException as error:
        print(f"epoch-3 repaired execution rejected: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(_canonical(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
