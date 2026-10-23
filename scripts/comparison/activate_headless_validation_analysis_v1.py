#!/usr/bin/env python3
"""Publish the one authorized epoch-5 held-out validation analysis."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import types


sys.dont_write_bytecode = True

AUTH = {
    "bytes": 1642,
    "path": "docs/simulator/headless_validation_epoch_5_analysis_authorization_v1.json",
    "sha256": "8da9d45222b0f09947504a9486bc0931a2eddd465e09d8629bbc1ac2cb1fe089",
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read(root: Path, identity: dict, label: str) -> bytes:
    raw = root.joinpath(*PurePosixPath(identity["path"]).parts).read_bytes()
    if len(raw) != identity["bytes"] or _sha(raw) != identity["sha256"]:
        raise ValueError(f"{label} identity differs")
    return raw


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    if Path(os.path.realpath(root)) != root:
        raise ValueError("repository root is not canonical")
    auth_raw = _read(root, AUTH, "analysis authorization")
    auth = json.loads(auth_raw)
    if auth["authority"] != {
        "analysis_authorized": True,
        "benchmark_claim_authorized": False,
        "capacity_claim_authorized": False,
        "execution_authorized": False,
        "result_publication_authorized": True,
    }:
        raise ValueError("analysis authority differs")
    _read(root, auth["execution_receipt"], "execution receipt")
    _read(root, auth["plan"], "registered plan")
    _read(root, auth["session"]["session_record"], "session record")
    _read(root, auth["session"]["ledger"], "session ledger")
    source_identity = {
        "bytes": auth["analysis"]["source"]["bytes"],
        "path": auth["analysis"]["path"],
        "sha256": auth["analysis"]["source"]["sha256"],
    }
    source = _read(root, source_identity, "validation analyzer")
    module = types.ModuleType("_authorized_validation_analysis")
    module.__file__ = source_identity["path"]
    sys.modules[module.__name__] = module
    exec(compile(source, module.__file__, "exec", dont_inherit=True, optimize=0), module.__dict__)
    report = module.analyze_validation_session(
        root,
        root.joinpath(*PurePosixPath(auth["session"]["path"]).parts),
        draws=auth["analysis"]["bootstrap_draw_count"],
    )
    raw = module.canonical_json_bytes(report)
    output = root.joinpath(*PurePosixPath(auth["output"]).parts)
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("analysis write made no progress")
            view = view[written:]
        os.fsync(descriptor)
    except BaseException:
        try:
            os.close(descriptor)
        finally:
            output.unlink(missing_ok=True)
        raise
    os.close(descriptor)
    receipt = {
        "analysis_sha256": _sha(raw),
        "analysis_bytes": len(raw),
        "operation": "publish-held-out-validation-analysis",
        "output": auth["output"],
        "schema_version": "dancers-headless-validation-analysis-publication-receipt-v1",
        "validation_outcome": report["status"]["validation_outcome"],
    }
    print(json.dumps(receipt, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
