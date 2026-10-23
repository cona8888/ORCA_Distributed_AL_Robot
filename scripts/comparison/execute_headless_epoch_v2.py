#!/usr/bin/env python3
"""Authenticate and execute the one registered Track-B calibration plan."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[2]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from scripts.comparison import run_headless_epoch_v1 as runner


PLAN_PATH = ROOT.joinpath(*runner.PLAN_ROOT.parts) / "plan.json"


class ExecutionDriverError(RuntimeError):
    pass


def _git(root: Path, *arguments: str) -> bytes:
    environment = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": "/nonexistent",
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": "/usr/bin:/bin",
    }
    result = subprocess.run(
        ["/usr/bin/git", *arguments],
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        raise ExecutionDriverError(
            f"git {' '.join(arguments)} rejected the registered inputs: "
            + result.stderr.decode("utf-8", "replace")
        )
    return result.stdout


def require_committed_inputs(plan_path: Path, repository_root: Path) -> None:
    if repository_root != ROOT or plan_path != PLAN_PATH:
        raise ExecutionDriverError("registered plan pathname differs")
    relative = plan_path.relative_to(repository_root).as_posix()
    _git(repository_root, "cat-file", "-e", f"HEAD:{relative}")
    if _git(repository_root, "status", "--porcelain", "--untracked-files=no"):
        raise ExecutionDriverError("tracked repository inputs are not clean at HEAD")
    live = plan_path.read_bytes()
    committed = _git(repository_root, "show", f"HEAD:{relative}")
    if live != committed:
        raise ExecutionDriverError("registered plan bytes differ from HEAD")


def preflight() -> dict[str, object]:
    authenticated = runner.load_authenticated_plan(
        PLAN_PATH,
        ROOT,
        production=False,
    )
    require_committed_inputs(PLAN_PATH, ROOT)
    return {
        "plan_bytes": len(authenticated.plan_bytes),
        "plan_sha256": authenticated.plan_sha256,
        "process_count": len(authenticated.schedule),
        "result_namespace": authenticated.result_namespace.as_posix(),
        "schema_version": authenticated.plan["schema_version"],
        "status": "pass-authenticated-committed-preflight",
    }


def execute(session_name: str) -> dict[str, object]:
    if (
        not session_name
        or session_name in (".", "..", ".epoch-lease")
        or "/" in session_name
        or "\\" in session_name
    ):
        raise ExecutionDriverError("session name differs")
    preflight_plan = runner.load_authenticated_plan(PLAN_PATH, ROOT, production=False)
    require_committed_inputs(PLAN_PATH, ROOT)
    result_root = ROOT.joinpath(*preflight_plan.result_namespace.parts)
    result_root.mkdir(parents=True, exist_ok=True)
    authenticated = runner.load_authenticated_plan(
        PLAN_PATH,
        ROOT,
        production=True,
        committed_input_checker=require_committed_inputs,
    )
    session_path = result_root / session_name
    return runner.execute_custodied_schedule(authenticated, session_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight-only", action="store_true")
    mode.add_argument("--execute-calibration", action="store_true")
    parser.add_argument("--session-name", default="calibration-v2-session-001")
    arguments = parser.parse_args(argv)
    result = preflight() if arguments.preflight_only else execute(arguments.session_name)
    print(json.dumps(result, allow_nan=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
