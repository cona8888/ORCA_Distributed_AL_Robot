#!/usr/bin/env python3
"""Authenticate DANCeRS--ORCA execution decisions without executing them.

The public surface is deliberately non-authorizing: ``describe`` emits the
exact host/session/output binding that two external decision files must use,
and ``verify`` authenticates those files against separately supplied SHA-256
trust roots.  Neither command writes a file, constructs an ExecutionPermit,
or calls the publication runner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import stat
import sys
from typing import Any, Mapping, Sequence


REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.replication import prepare_orca_execution_capsule as capsule


STUDY_ID = "dancers-orca-v1"
AUTHORIZATION_SCHEMA = "dancers-orca-execution-authorization-v1"
ADMISSION_SCHEMA = "dancers-orca-execution-admission-v1"
CONTRACT_SCHEMA = "dancers-orca-execution-admission-contract-v1"
CAPSULE_MANIFEST_SHA256 = (
    "aa1d4229c115e3742c07328d09034d5e62f83be70b97c0d71284a01315cd0c91"
)
DEFAULT_CAPSULE_ROOT = (
    REPO / "comparison/publication/dancers-orca-v1-session-002-execution-capsule"
).resolve()
EXPECTED_PROCESSES = 363
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SESSION_ID_RE = re.compile(r"[a-z0-9][a-z0-9._-]{7,63}")
LAUNCHER_SOURCE = REPO / "scripts/replication/launch_orca_comparison.py"
AMENDMENT_4 = (
    REPO
    / "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_AMENDMENT_4_V1.md"
)
AMENDMENT_5 = (
    REPO
    / "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_AMENDMENT_5_V1.md"
)
AMENDMENT_6 = (
    REPO
    / "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_AMENDMENT_6_V1.md"
)
AMENDMENT_7 = (
    REPO
    / "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_AMENDMENT_7_V1.md"
)
AMENDMENT_8 = (
    REPO
    / "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_AMENDMENT_8_V1.md"
)
AMENDMENT_9 = (
    REPO
    / "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_AMENDMENT_9_V1.md"
)


class AdmissionError(RuntimeError):
    """A fail-closed authorization/admission contract error."""


def canonical_json_bytes(value: object) -> bytes:
    return capsule.canonical_json_bytes(value)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _file_identity(path: pathlib.Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise AdmissionError(f"required regular file is absent or unsafe: {path}")
    raw = path.read_bytes()
    return {
        "bytes": len(raw),
        "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}",
        "path": str(path),
        "sha256": sha256_bytes(raw),
    }


def _require_identity(expected: Mapping[str, Any], label: str) -> None:
    if set(expected) != {"bytes", "mode", "path", "sha256"}:
        raise AdmissionError(f"{label} identity schema differs")
    actual = _file_identity(pathlib.Path(str(expected["path"])))
    if actual != dict(expected):
        raise AdmissionError(f"{label} identity differs")


def _strict_json_file(path: pathlib.Path, expected_digest: str, label: str) -> dict[str, Any]:
    if SHA256_RE.fullmatch(expected_digest) is None:
        raise AdmissionError(f"external {label} SHA-256 is invalid")
    if not path.is_absolute() or path != path.resolve():
        raise AdmissionError(f"{label} path must be absolute and normalized")
    if path.is_symlink() or not path.is_file():
        raise AdmissionError(f"{label} is absent or unsafe")
    raw = path.read_bytes()
    if sha256_bytes(raw) != expected_digest:
        raise AdmissionError(f"{label} differs from its external trust root")
    try:
        value = capsule.parse_strict_json(raw, label)
    except (capsule.CapsuleError, ValueError, json.JSONDecodeError) as error:
        raise AdmissionError(f"{label} is not strict JSON: {error}") from error
    if not isinstance(value, dict) or raw != canonical_json_bytes(value):
        raise AdmissionError(f"{label} is not a canonical JSON object")
    return value


def _normalized_absent_output(path: pathlib.Path) -> pathlib.Path:
    if not path.is_absolute() or path != path.resolve() or path.is_symlink():
        raise AdmissionError("output root must be absolute, normalized, and non-symlink")
    if path.exists():
        raise AdmissionError("output root must be absent; resume and overwrite are forbidden")
    parent = path.parent
    if parent.is_symlink() or not parent.is_dir() or parent != parent.resolve():
        raise AdmissionError("output parent must be a real normalized directory")
    if not os.access(parent, os.W_OK | os.X_OK):
        raise AdmissionError("output parent is not writable and searchable")
    return path


def _overlaps(left: pathlib.Path, right: pathlib.Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _host_contract(manifest: Mapping[str, Any]) -> dict[str, Any]:
    build_identity = manifest["dependencies"]["dancers"]["committed_build_manifest"]
    _require_identity(build_identity, "DANCeRS build manifest")
    build_path = pathlib.Path(str(build_identity["path"]))
    build_raw = build_path.read_bytes()
    try:
        build = capsule.parse_strict_json(build_raw, "DANCeRS build manifest")
        host = build["host"]
    except (
        capsule.CapsuleError,
        KeyError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
    ) as error:
        raise AdmissionError("DANCeRS build host record is malformed") from error
    current = {
        "architecture": os.uname().machine,
        "host_id_sha256": sha256_bytes(os.uname().nodename.encode("utf-8")),
        "platform": sys.platform,
    }
    expected = {
        "architecture": host.get("architecture"),
        "host_id_sha256": host.get("host_id_sha256"),
        "platform": host.get("platform"),
    }
    if current != expected:
        raise AdmissionError("current host differs from the frozen build host")
    return {
        **current,
        "dancers_build_manifest_sha256": build_identity["sha256"],
        "schema_version": "dancers-orca-host-contract-v1",
    }


def build_binding(
    capsule_root: pathlib.Path,
    expected_capsule_sha256: str,
    output_root: pathlib.Path,
    session_id: str,
) -> dict[str, Any]:
    """Read and authenticate every byte needed to bind external decisions."""

    if expected_capsule_sha256 != CAPSULE_MANIFEST_SHA256:
        raise AdmissionError("execution capsule digest differs from the frozen trust root")
    if SESSION_ID_RE.fullmatch(session_id) is None:
        raise AdmissionError("session id must be 8-64 lowercase safe characters")
    output_root = _normalized_absent_output(output_root)
    try:
        manifest, payloads = capsule.load_capsule_files(
            capsule_root, expected_capsule_sha256
        )
    except (capsule.CapsuleError, OSError, KeyError, TypeError, ValueError) as error:
        raise AdmissionError(f"execution capsule authentication failed: {error}") from error
    if manifest.get("study_id") != STUDY_ID:
        raise AdmissionError("execution capsule study differs")
    classification = manifest.get("classification", {})
    if (
        classification.get("execution_authorized") is not False
        or classification.get("publication_processes_executed") != 0
        or classification.get("publication_simulation_steps") != 0
    ):
        raise AdmissionError("execution capsule is not the prospective zero-result capsule")

    file_records = {item["path"]: item for item in manifest["files"]}
    plan_identity = file_records.get("execution_plan.json")
    if (
        plan_identity is None
        or sha256_bytes(payloads["execution_plan.json"])
        != plan_identity["sha256"]
    ):
        raise AdmissionError("execution plan identity differs")
    plan = capsule.parse_strict_json(payloads["execution_plan.json"], "execution plan")
    if (
        plan.get("study_id") != STUDY_ID
        or plan.get("inventory", {}).get("processes") != EXPECTED_PROCESSES
        or plan.get("classification", {}).get("execution_authorized") is not False
    ):
        raise AdmissionError("execution plan contract differs")

    runtime = manifest["runtime"]
    for name, identity in runtime["binaries"].items():
        _require_identity(identity, f"{name} binary")
    _require_identity(runtime["libomp"], "libomp")
    for label in ("manifest", "schedule"):
        _require_identity(manifest["input"][label], f"input {label}")
    for method, raw_path in runtime["source_roots"].items():
        path = pathlib.Path(raw_path)
        if path.is_symlink() or not path.is_dir() or path != path.resolve():
            raise AdmissionError(f"{method} source root is absent or unsafe")

    protected = [
        capsule_root,
        pathlib.Path(manifest["input"]["root"]),
        *[pathlib.Path(item["path"]).parent for item in runtime["binaries"].values()],
        pathlib.Path(runtime["libomp"]["path"]).parent,
        *[pathlib.Path(item) for item in runtime["source_roots"].values()],
    ]
    if any(_overlaps(output_root, path) for path in protected):
        raise AdmissionError("output root overlaps a protected capsule/input/runtime root")

    host = _host_contract(manifest)
    source_contract = {
        "admission_gate": _file_identity(pathlib.Path(__file__).resolve()),
        "amendment_4": _file_identity(AMENDMENT_4.resolve()),
        "amendment_5": _file_identity(AMENDMENT_5.resolve()),
        "amendment_6": _file_identity(AMENDMENT_6.resolve()),
        "amendment_7": _file_identity(AMENDMENT_7.resolve()),
        "amendment_8": _file_identity(AMENDMENT_8.resolve()),
        "amendment_9": _file_identity(AMENDMENT_9.resolve()),
        "execution_launcher": _file_identity(LAUNCHER_SOURCE.resolve()),
        "schema_version": "dancers-orca-execution-launcher-source-contract-v1",
    }
    binding = {
        "capsule": {
            "manifest_sha256": expected_capsule_sha256,
            "root": str(capsule_root),
        },
        "execution_plan_sha256": plan_identity["sha256"],
        "expected_processes": EXPECTED_PROCESSES,
        "host_contract": host,
        "host_contract_sha256": sha256_bytes(canonical_json_bytes(host)),
        "input_manifest_sha256": manifest["input"]["manifest"]["sha256"],
        "output_root": str(output_root),
        "runtime_contract_sha256": sha256_bytes(canonical_json_bytes(runtime)),
        "schedule_sha256": manifest["input"]["schedule"]["sha256"],
        "session_id": session_id,
        "source_contract": source_contract,
        "source_contract_sha256": sha256_bytes(
            canonical_json_bytes(source_contract)
        ),
    }
    return binding


def authorization_document(binding: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "binding": dict(binding),
        "decision": "authorize_one_complete_registered_session",
        "execution_authorized": True,
        "expected_processes": EXPECTED_PROCESSES,
        "schema_version": AUTHORIZATION_SCHEMA,
        "selective_retry_allowed": False,
        "study_id": STUDY_ID,
    }


def admission_document(
    binding: Mapping[str, Any], authorization_manifest_sha256: str
) -> dict[str, Any]:
    if SHA256_RE.fullmatch(authorization_manifest_sha256) is None:
        raise AdmissionError("authorization manifest SHA-256 is invalid")
    return {
        "admission_granted": True,
        "authorization_manifest_sha256": authorization_manifest_sha256,
        "binding": dict(binding),
        "binding_sha256": sha256_bytes(canonical_json_bytes(binding)),
        "decision": "admit_authenticated_host_and_absent_output",
        "expected_processes": EXPECTED_PROCESSES,
        "output_root_state": "absent",
        "schema_version": ADMISSION_SCHEMA,
        "selective_retry_allowed": False,
        "study_id": STUDY_ID,
    }


def describe_contract(binding: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "admission_schema": ADMISSION_SCHEMA,
        "authorization_schema": AUTHORIZATION_SCHEMA,
        "binding": dict(binding),
        "binding_sha256": sha256_bytes(canonical_json_bytes(binding)),
        "classification": {
            "execution_authorized": False,
            "publication_processes_executed": 0,
            "publication_simulation_steps": 0,
            "read_only": True,
        },
        "next_required_artifacts": [
            "externally authenticated canonical authorization manifest",
            "externally authenticated canonical admission manifest",
            "exact explicit complete-schedule launch confirmation",
        ],
        "schema_version": CONTRACT_SCHEMA,
        "study_id": STUDY_ID,
    }


def verify_decisions(
    binding: Mapping[str, Any],
    authorization_path: pathlib.Path,
    expected_authorization_sha256: str,
    admission_path: pathlib.Path,
    expected_admission_sha256: str,
) -> dict[str, Any]:
    authorization = _strict_json_file(
        authorization_path, expected_authorization_sha256, "authorization manifest"
    )
    if authorization != authorization_document(binding):
        raise AdmissionError("authorization manifest differs from the exact contract")
    admission = _strict_json_file(
        admission_path, expected_admission_sha256, "admission manifest"
    )
    if admission != admission_document(binding, expected_authorization_sha256):
        raise AdmissionError("admission manifest differs from the exact contract")
    return {
        "admission_manifest_sha256": expected_admission_sha256,
        "authorization_manifest_sha256": expected_authorization_sha256,
        "binding_sha256": sha256_bytes(canonical_json_bytes(binding)),
        "decisions_authenticated": True,
        "execution_authorized": False,
        "publication_processes_executed": 0,
        "publication_simulation_steps": 0,
        "status": "admitted_without_execution",
        "study_id": STUDY_ID,
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    commands = value.add_subparsers(dest="command", required=True)
    for name in ("describe", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--capsule-root", type=pathlib.Path, default=DEFAULT_CAPSULE_ROOT)
        command.add_argument(
            "--expected-capsule-sha256", default=CAPSULE_MANIFEST_SHA256
        )
        command.add_argument("--output-root", type=pathlib.Path, required=True)
        command.add_argument("--session-id", required=True)
        if name == "verify":
            command.add_argument("--authorization-manifest", type=pathlib.Path, required=True)
            command.add_argument("--expected-authorization-sha256", required=True)
            command.add_argument("--admission-manifest", type=pathlib.Path, required=True)
            command.add_argument("--expected-admission-sha256", required=True)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        binding = build_binding(
            args.capsule_root,
            args.expected_capsule_sha256,
            args.output_root,
            args.session_id,
        )
        if args.command == "describe":
            result = describe_contract(binding)
        else:
            result = verify_decisions(
                binding,
                args.authorization_manifest,
                args.expected_authorization_sha256,
                args.admission_manifest,
                args.expected_admission_sha256,
            )
        sys.stdout.buffer.write(canonical_json_bytes(result))
        return 0
    except (AdmissionError, OSError, KeyError, TypeError, ValueError) as error:
        print(f"execution admission failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
