#!/usr/bin/env python3
"""Analyze the one exact completed epoch-2 task-calibration session."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import sys
import types
from typing import Any, Mapping


sys.dont_write_bytecode = True

AUTH_PATH = PurePosixPath(
    "docs/simulator/headless_task_calibration_epoch_2_analysis_v1.json"
)
EXECUTION_RECEIPT = {
    "bytes": 1133,
    "path": "docs/simulator/headless_task_calibration_epoch_2_execution_receipt_v1.json",
    "sha256": "7cb5b09685c2428a31f8b3a342dfad9f7223fd3dc69e515b5901c7f0613e62b0",
}
ANALYZER = {
    "bytes": 10612,
    "path": "scripts/comparison/analyze_headless_task_calibration_epoch_v2.py",
    "sha256": "90184be7b38413745d0629433943aafaa78ce8c6abc8ffbfd087b2b7fdf06afe",
}
PLAN = {
    "bytes": 2795335,
    "path": "comparison/plans/simulator-demo/v1/headless-epoch-2-task-calibration/plan.json",
    "sha256": "f8579bbb29c2b6abece90ad86da6478edfee11dc36f7494881d1479ecd8b16a0",
}
SESSION = {
    "bytes": 1597553,
    "path": "results/simulator-demo/v1/headless-scientific/epoch-2-task-calibration/task-calibration-session-001/session.json",
    "sha256": "e0c67e6bf43826b9cb68c2c27e2cd9e82a831d434209759ab2c4c3a3d19c18c1",
}
LEDGER = {
    "bytes": 775410,
    "path": "results/simulator-demo/v1/headless-scientific/epoch-2-task-calibration/task-calibration-session-001/ledger.ndjson",
    "sha256": "ab98b443880f78dde553400221b40cd39b4f51b20fc62bd9c84431bdd79ffb60",
}
OUTPUT = PurePosixPath(
    "results/simulator-demo/v1/headless-scientific/epoch-2-task-calibration/"
    "task-calibration-session-001-analysis.json"
)
SESSION_DIR = PurePosixPath(SESSION["path"]).parent


class AnalysisActivationError(ValueError):
    """The completed-session analysis authority or evidence differs."""


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise AnalysisActivationError(f"duplicate authorization key: {key}")
        value[key] = item
    return value


def _canonical(value: object) -> bytes:
    try:
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
    except (RecursionError, TypeError, UnicodeError, ValueError) as error:
        raise AnalysisActivationError(f"value is not canonical JSON: {error}") from error


def _read_identity(root: Path, identity: Mapping[str, Any], label: str) -> bytes:
    path = root.joinpath(*PurePosixPath(identity["path"]).parts)
    try:
        before = path.lstat()
        raw = path.read_bytes()
        after = path.lstat()
    except (KeyError, OSError, TypeError) as error:
        raise AnalysisActivationError(f"cannot read {label}: {error}") from error
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_ino != after.st_ino
        or before.st_dev != after.st_dev
        or before.st_size != after.st_size
        or len(raw) != identity.get("bytes")
        or _sha(raw) != identity.get("sha256")
    ):
        raise AnalysisActivationError(f"{label} identity differs")
    return raw


def _load_authorization(root: Path) -> tuple[dict[str, Any], bytes]:
    raw = root.joinpath(*AUTH_PATH.parts).read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except (RecursionError, UnicodeError, ValueError) as error:
        raise AnalysisActivationError(f"analysis authorization is not strict JSON: {error}") from error
    if raw != _canonical(value):
        raise AnalysisActivationError("analysis authorization is not canonical JSON")
    if (
        value.get("schema_version")
        != "dancers-headless-task-calibration-analysis-authorization-v1"
        or value.get("one_use") is not True
        or value.get("operation") != "analyze-exact-completed-task-only-session"
        or value.get("analysis") != ANALYZER
        or value.get("execution_receipt") != EXECUTION_RECEIPT
        or value.get("plan") != PLAN
        or value.get("session") != SESSION
        or value.get("ledger") != LEDGER
        or value.get("output") != OUTPUT.as_posix()
        or value.get("scientific_evidence_authorized") is not False
        or value.get("timing_execution_authorized") is not False
        or value.get("timing_claim_authorized") is not False
        or value.get("validation_execution_authorized") is not False
        or value.get("validation_claim_authorized") is not False
        or value.get("benchmark_claim_authorized") is not False
    ):
        raise AnalysisActivationError("analysis authorization differs")
    return value, raw


def _git(root: Path, *arguments: str) -> bytes:
    result = subprocess.run(
        ["/usr/bin/git", *arguments],
        cwd=root,
        env={
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "HOME": "/nonexistent",
            "LANG": "C",
            "LC_ALL": "C",
            "PATH": "/usr/bin:/bin",
        },
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=60,
    )
    if result.returncode != 0:
        raise AnalysisActivationError(f"git {' '.join(arguments)} failed")
    return result.stdout


def _require_committed(root: Path) -> None:
    if _git(root, "status", "--porcelain", "--untracked-files=no"):
        raise AnalysisActivationError("tracked repository state is not clean")
    paths = (
        AUTH_PATH.as_posix(),
        EXECUTION_RECEIPT["path"],
        ANALYZER["path"],
        PLAN["path"],
        Path(__file__).resolve().relative_to(root).as_posix(),
    )
    for relative in paths:
        if _git(root, "show", f"HEAD:{relative}") != (root / relative).read_bytes():
            raise AnalysisActivationError(f"{relative} is not exact committed HEAD content")


def _load_analyzer(root: Path) -> types.ModuleType:
    raw = _read_identity(root, ANALYZER, "authorized prospective analyzer")
    module = types.ModuleType("_dancers_authorized_task_analyzer")
    module.__file__ = ANALYZER["path"]
    sys.modules[module.__name__] = module
    try:
        exec(compile(raw, module.__file__, "exec", dont_inherit=True, optimize=0), module.__dict__)
    except BaseException:
        sys.modules.pop(module.__name__, None)
        raise
    return module


def _publish_exclusive(root: Path, raw: bytes) -> None:
    absolute = root.joinpath(*OUTPUT.parts)
    if absolute.exists() or absolute.is_symlink():
        raise AnalysisActivationError("authorized analysis output already exists")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    created = False
    try:
        descriptor = os.open(absolute, flags, 0o444)
        created = True
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise AnalysisActivationError("analysis output write made no progress")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if absolute.read_bytes() != raw:
            raise AnalysisActivationError("published analysis bytes differ")
    except BaseException:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except BaseException:
                pass
        if created:
            try:
                absolute.unlink()
            except BaseException:
                pass
        raise


def execute_authorized_analysis(repository_root: Path) -> dict[str, Any]:
    root = Path(os.path.abspath(repository_root))
    if Path(os.path.realpath(root)) != root:
        raise AnalysisActivationError("repository root is not canonical")
    authorization, authorization_raw = _load_authorization(root)
    _require_committed(root)
    _read_identity(root, EXECUTION_RECEIPT, "execution receipt")
    _read_identity(root, SESSION, "sealed session")
    _read_identity(root, LEDGER, "sealed ledger")
    if not root.joinpath(*SESSION_DIR.parts).is_dir():
        raise AnalysisActivationError("sealed session directory is absent")
    analyzer = _load_analyzer(root)
    report = analyzer.analyze_task_session(
        root,
        root.joinpath(*SESSION_DIR.parts),
    )
    report_raw = analyzer.canonical_json_bytes(report)
    _publish_exclusive(root, report_raw)
    return {
        "analysis_authorization_sha256": _sha(authorization_raw),
        "benchmark_claim_ready": False,
        "operation": authorization["operation"],
        "output": {
            "bytes": len(report_raw),
            "path": OUTPUT.as_posix(),
            "sha256": _sha(report_raw),
        },
        "schema_version": "dancers-headless-task-calibration-analysis-receipt-v1",
        "scientific_evidence": False,
        "timing_claim_ready": False,
        "validation_claim_ready": False,
    }


def main() -> int:
    try:
        receipt = execute_authorized_analysis(Path(__file__).resolve().parents[2])
    except BaseException as error:
        print(
            f"task-calibration analysis rejected: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1
    sys.stdout.buffer.write(_canonical(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
