#!/usr/bin/env python3
"""Run the one permitted retrospective analysis repair for headless epoch 1."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
from typing import Any, Mapping, Sequence


sys.dont_write_bytecode = True

SCHEMA = "dancers-headless-analysis-remediation-v1"
ADDENDUM_SCHEMA = "dancers-headless-analysis-remediation-addendum-v1"
ROOT = Path(__file__).resolve().parents[2]
V1_RELATIVE = PurePosixPath("scripts/comparison/analyze_headless_epoch_v1.py")
ADDENDUM_RELATIVE = PurePosixPath(
    "docs/comparison/headless_epoch_1_analysis_remediation_v1.json"
)
PLAN_RELATIVE = PurePosixPath(
    "comparison/plans/simulator-demo/v1/headless-epoch-1/plan.json"
)
SESSION_RELATIVE = PurePosixPath(
    "results/simulator-demo/v1/headless-scientific/epoch-1/"
    "calibration-v2-session-002"
)
OUTPUT_RELATIVE = PurePosixPath(
    "results/simulator-demo/v1/headless-scientific/"
    "epoch-1-calibration-v2-session-002-analysis-remediation-1.json"
)
LOCKED_FALSE = {
    "benchmark_claim_ready": False,
    "capacity_claim_ready": False,
    "confirmatory_claim_ready": False,
    "execution_authorized": False,
    "scientific_evidence": False,
    "timing_claim_ready": False,
    "timing_unlock": False,
    "validation_unlock": False,
}


class AnalysisRemediationError(RuntimeError):
    pass


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    if not raw or not raw.endswith(b"\n"):
        raise AnalysisRemediationError(f"{label} byte envelope differs")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in items:
            if key in value:
                raise AnalysisRemediationError(f"{label} has a duplicate key")
            value[key] = item
        return value

    try:
        value = json.loads(raw, object_pairs_hook=pairs)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise AnalysisRemediationError(f"{label} is not strict JSON") from error
    if type(value) is not dict or _canonical(value) != raw:
        raise AnalysisRemediationError(f"{label} is not a canonical object")
    return value


def _identity(root: Path, relative: PurePosixPath, label: str) -> dict[str, Any]:
    path = root.joinpath(*relative.parts)
    if path.is_symlink():
        raise AnalysisRemediationError(f"{label} is a symlink")
    raw = path.read_bytes()
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise AnalysisRemediationError(f"{label} is not regular")
    return {"bytes": len(raw), "path": relative.as_posix(), "sha256": _sha256(raw)}


def _exact(value: Any, expected: Any, label: str) -> None:
    if type(value) is not type(expected) or value != expected:
        raise AnalysisRemediationError(f"{label} differs")


def load_addendum(root: Path) -> dict[str, Any]:
    raw = root.joinpath(*ADDENDUM_RELATIVE.parts).read_bytes()
    value = _strict_json(raw, "analysis remediation addendum")
    required = {
        "analysis_identity",
        "classification",
        "evidence",
        "forbidden_changes",
        "output_contract",
        "permitted_repair",
        "schema_version",
        "status",
        "tooling",
    }
    if set(value) != required or value["schema_version"] != ADDENDUM_SCHEMA:
        raise AnalysisRemediationError("analysis remediation addendum schema differs")
    _exact(
        value["classification"],
        {
            "analysis_only": True,
            "outcome_known_at_freeze": True,
            "preregistered": False,
            "simulator_execution_authorized": False,
            "timing_authorized": False,
        },
        "analysis remediation classification",
    )
    _exact(value["output_contract"]["path"], OUTPUT_RELATIVE.as_posix(), "output path")
    _exact(value["output_contract"]["exclusive_create"], True, "output exclusivity")
    _exact(value["status"]["source_evidence_immutable"], True, "evidence immutability")
    return value


def _load_original_analyzer(root: Path, addendum: Mapping[str, Any]) -> dict[str, Any]:
    source_path = root.joinpath(*V1_RELATIVE.parts)
    raw = source_path.read_bytes()
    _exact(
        {"bytes": len(raw), "path": V1_RELATIVE.as_posix(), "sha256": _sha256(raw)},
        addendum["tooling"]["original_analyzer"],
        "original analyzer identity",
    )
    namespace: dict[str, Any] = {
        "__builtins__": __builtins__,
        "__file__": V1_RELATIVE.as_posix(),
        "__name__": "_dancers_headless_analysis_remediation_v1_base",
    }
    try:
        exec(compile(raw, V1_RELATIVE.as_posix(), "exec", dont_inherit=True), namespace)
    except BaseException as error:
        raise AnalysisRemediationError("original analyzer cannot be raw-executed") from error
    required = {"Path", "analyze_headless_epoch", "validate_analysis_report"}
    if not required <= set(namespace):
        raise AnalysisRemediationError("original analyzer API differs")
    # Exact permitted repair: pathlib.Path is a factory while concrete absolute
    # paths are PosixPath instances.  Rebinding this one global makes the frozen
    # `type(session_root) is Path` predicate accept its genuine platform type.
    namespace["Path"] = type(Path())
    return namespace


def _verify_bindings(root: Path, addendum: Mapping[str, Any]) -> None:
    evidence = addendum["evidence"]
    _exact(_identity(root, PLAN_RELATIVE, "registered plan"), evidence["plan"], "plan identity")
    _exact(
        _identity(root, SESSION_RELATIVE / "session.json", "sealed session record"),
        evidence["session"],
        "session identity",
    )
    _exact(
        _identity(root, SESSION_RELATIVE / "ledger.ndjson", "sealed session ledger"),
        evidence["ledger"],
        "ledger identity",
    )
    _exact(
        _identity(root, PurePosixPath(__file__).relative_to(root), "successor analyzer"),
        addendum["tooling"]["analysis_revision"],
        "successor analyzer identity",
    )


def run_analysis(root: Path, *, bootstrap_draws: int = 10_000) -> dict[str, Any]:
    canonical_root = Path(os.path.realpath(root))
    if not canonical_root.is_dir() or canonical_root != ROOT:
        raise AnalysisRemediationError("repository root differs")
    addendum = load_addendum(canonical_root)
    _verify_bindings(canonical_root, addendum)
    namespace = _load_original_analyzer(canonical_root, addendum)
    report = namespace["analyze_headless_epoch"](
        repository_root=canonical_root,
        plan_path=canonical_root.joinpath(*PLAN_RELATIVE.parts),
        session_path=canonical_root.joinpath(*SESSION_RELATIVE.parts),
        bootstrap_draws=bootstrap_draws,
    )
    namespace["validate_analysis_report"](report)
    return {
        "analysis": report,
        "analysis_identity": addendum["analysis_identity"],
        "classification": dict(LOCKED_FALSE),
        "evidence": addendum["evidence"],
        "permitted_repair": addendum["permitted_repair"],
        "schema_version": SCHEMA,
        "status": {
            "analysis_completed": True,
            "benchmark_claim_ready": False,
            "capacity_claim_ready": False,
            "execution_unlock": False,
            "timing_unlock": False,
            "validation_unlock": False,
        },
        "tooling": addendum["tooling"],
    }


def publish_create_exclusive(root: Path, value: Mapping[str, Any]) -> dict[str, Any]:
    raw = _canonical(dict(value))
    target = root.joinpath(*OUTPUT_RELATIVE.parts)
    parent = target.parent
    if parent.is_symlink() or not parent.is_dir() or Path(os.path.realpath(parent)) != parent:
        raise AnalysisRemediationError("analysis output parent differs")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(target, flags, 0o444)
    try:
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise AnalysisRemediationError("analysis output write made no progress")
            offset += written
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    parent_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
    observed = target.read_bytes()
    if observed != raw:
        raise AnalysisRemediationError("published analysis bytes differ")
    return {"bytes": len(raw), "path": OUTPUT_RELATIVE.as_posix(), "sha256": _sha256(raw)}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-analysis-remediation", action="store_true")
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args(argv)
    if args.execute_analysis_remediation == args.preflight_only:
        parser.error("choose exactly one explicit mode")
    addendum = load_addendum(ROOT)
    _verify_bindings(ROOT, addendum)
    if args.preflight_only:
        print(_canonical({"status": "pass-remediation-preflight", "write_performed": False}).decode(), end="")
        return 0
    result = publish_create_exclusive(ROOT, run_analysis(ROOT))
    print(_canonical(result).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
