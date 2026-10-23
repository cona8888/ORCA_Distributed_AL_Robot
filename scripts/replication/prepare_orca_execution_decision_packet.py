#!/usr/bin/env python3
"""Create or check an affirmative, non-executing ORCA decision packet.

``create`` writes canonical authorization and admission manifests for one exact
session binding.  ``check`` authenticates the packet from an external manifest
SHA-256 and rechecks the live binding.  Neither command imports or invokes the
execution launcher or runner.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import shutil
import stat
import sys
import tempfile
from typing import Any, Mapping, Sequence


REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.replication import prepare_orca_execution_admission as admission
from scripts.replication import prepare_orca_execution_capsule as capsule


SCHEMA = "dancers-orca-execution-decision-packet-v1"
PACKET_ID = "dancers-orca-v1-session-002-decisions"
SESSION_ID = "dancers-orca-v1-session-002"
RESULT_ROOT = (
    REPO / "results/publication/dancers-orca-v1-session-002"
).resolve()
DEFAULT_PACKET_ROOT = (
    REPO / "comparison/publication/dancers-orca-v1-session-002-decision-packet"
).resolve()
EXACT_CREATE_CONFIRMATION = "CREATE-AFFIRMATIVE-DECISIONS-NO-EXECUTION"
PAYLOADS = ("admission.json", "authorization.json")
FILE_MODE = "0644"
AUTHENTICATION_RULE = (
    "check requires an externally supplied exact packet manifest SHA-256; "
    "the sidecar is consistency evidence only"
)
CLASSIFICATION = {
    "admission_granted": True,
    "execution_authorized": True,
    "launcher_invoked": False,
    "publication_processes_executed": 0,
    "publication_simulation_steps": 0,
    "result_namespace": "absent",
}


class DecisionPacketError(RuntimeError):
    """A fail-closed decision-packet error."""


def canonical_json_bytes(value: object) -> bytes:
    return admission.canonical_json_bytes(value)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def payload_identity(relative: str, raw: bytes) -> dict[str, Any]:
    return {
        "bytes": len(raw),
        "mode": FILE_MODE,
        "path": relative,
        "sha256": sha256_bytes(raw),
    }


def build_packet() -> tuple[dict[str, bytes], dict[str, Any]]:
    binding = admission.build_binding(
        admission.DEFAULT_CAPSULE_ROOT,
        admission.CAPSULE_MANIFEST_SHA256,
        RESULT_ROOT,
        SESSION_ID,
    )
    authorization_raw = canonical_json_bytes(
        admission.authorization_document(binding)
    )
    authorization_sha256 = sha256_bytes(authorization_raw)
    admission_raw = canonical_json_bytes(
        admission.admission_document(binding, authorization_sha256)
    )
    payloads = {
        "admission.json": admission_raw,
        "authorization.json": authorization_raw,
    }
    manifest = {
        "authentication": {
            "rule": AUTHENTICATION_RULE
        },
        "binding_sha256": sha256_bytes(canonical_json_bytes(binding)),
        "capsule_manifest_sha256": admission.CAPSULE_MANIFEST_SHA256,
        "classification": CLASSIFICATION,
        "files": [payload_identity(name, payloads[name]) for name in PAYLOADS],
        "packet_id": PACKET_ID,
        "result_root": str(RESULT_ROOT),
        "schema_version": SCHEMA,
        "session_id": SESSION_ID,
        "study_id": admission.STUDY_ID,
    }
    return payloads, manifest


def _write_exclusive(path: pathlib.Path, raw: bytes) -> None:
    with path.open("xb") as handle:
        os.fchmod(handle.fileno(), int(FILE_MODE, 8))
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def _require_absent_absolute(path: pathlib.Path) -> pathlib.Path:
    if not path.is_absolute() or path != path.resolve() or path.is_symlink():
        raise DecisionPacketError("packet root must be absolute and normalized")
    if path.exists():
        raise DecisionPacketError("packet root already exists; replacement is forbidden")
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise DecisionPacketError("packet parent is absent or unsafe")
    return path


def publish_packet(
    root: pathlib.Path,
    payloads: Mapping[str, bytes],
    manifest: Mapping[str, Any],
) -> str:
    root = _require_absent_absolute(root)
    if tuple(sorted(payloads)) != PAYLOADS:
        raise DecisionPacketError("packet payload inventory differs")
    stage = pathlib.Path(
        tempfile.mkdtemp(prefix=f".{root.name}.stage-", dir=root.parent)
    )
    try:
        for relative in PAYLOADS:
            _write_exclusive(stage / relative, payloads[relative])
        manifest_raw = canonical_json_bytes(manifest)
        digest = sha256_bytes(manifest_raw)
        _write_exclusive(stage / "manifest.json", manifest_raw)
        _write_exclusive(
            stage / "manifest.sha256",
            f"{digest}  manifest.json\n".encode("ascii"),
        )
        check_packet(stage, digest)
        root.mkdir(mode=0o755, exist_ok=False)
        moved: list[pathlib.Path] = []
        try:
            for name in (*PAYLOADS, "manifest.json", "manifest.sha256"):
                destination = root / name
                (stage / name).rename(destination)
                moved.append(destination)
            stage.rmdir()
        except BaseException:
            for destination in reversed(moved):
                if destination.exists() or destination.is_symlink():
                    destination.unlink()
            if root.exists():
                root.rmdir()
            raise
        return digest
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def _read_canonical(path: pathlib.Path, label: str) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise DecisionPacketError(f"{label} is absent or unsafe")
    raw = path.read_bytes()
    try:
        value = capsule.parse_strict_json(raw, label)
    except (capsule.CapsuleError, ValueError) as error:
        raise DecisionPacketError(f"{label} is not strict JSON: {error}") from error
    if not isinstance(value, dict) or raw != canonical_json_bytes(value):
        raise DecisionPacketError(f"{label} is not a canonical JSON object")
    return value, raw


def check_packet(root: pathlib.Path, expected_manifest_sha256: str) -> dict[str, Any]:
    if admission.SHA256_RE.fullmatch(expected_manifest_sha256) is None:
        raise DecisionPacketError("external packet manifest SHA-256 is invalid")
    if not root.is_absolute() or root != root.resolve() or root.is_symlink():
        raise DecisionPacketError("packet root must be a real absolute directory")
    if not root.is_dir():
        raise DecisionPacketError("packet root is absent")
    expected_names = {*PAYLOADS, "manifest.json", "manifest.sha256"}
    entries = list(root.iterdir())
    if {item.name for item in entries} != expected_names:
        raise DecisionPacketError("packet file inventory differs")
    if any(item.is_symlink() or not item.is_file() for item in entries):
        raise DecisionPacketError("packet contains an unsafe entry")

    manifest, manifest_raw = _read_canonical(root / "manifest.json", "packet manifest")
    if sha256_bytes(manifest_raw) != expected_manifest_sha256:
        raise DecisionPacketError("packet differs from the external trust root")
    if (root / "manifest.sha256").read_bytes() != (
        f"{expected_manifest_sha256}  manifest.json\n".encode("ascii")
    ):
        raise DecisionPacketError("packet sidecar differs")
    expected_keys = {
        "authentication",
        "binding_sha256",
        "capsule_manifest_sha256",
        "classification",
        "files",
        "packet_id",
        "result_root",
        "schema_version",
        "session_id",
        "study_id",
    }
    if set(manifest) != expected_keys:
        raise DecisionPacketError("packet manifest schema differs")
    if (
        manifest["schema_version"] != SCHEMA
        or manifest["packet_id"] != PACKET_ID
        or manifest["authentication"] != {"rule": AUTHENTICATION_RULE}
        or manifest["study_id"] != admission.STUDY_ID
        or manifest["session_id"] != SESSION_ID
        or manifest["result_root"] != str(RESULT_ROOT)
        or manifest["capsule_manifest_sha256"]
        != admission.CAPSULE_MANIFEST_SHA256
        or manifest["classification"] != CLASSIFICATION
    ):
        raise DecisionPacketError("packet fixed contract differs")
    records = manifest["files"]
    if (
        not isinstance(records, list)
        or tuple(item.get("path") for item in records if isinstance(item, dict))
        != PAYLOADS
    ):
        raise DecisionPacketError("packet payload records differ")
    identities: dict[str, dict[str, Any]] = {}
    for item in records:
        if not isinstance(item, dict) or set(item) != {"bytes", "mode", "path", "sha256"}:
            raise DecisionPacketError("packet payload identity schema differs")
        relative = item["path"]
        payload_path = root / relative
        _value, raw = _read_canonical(payload_path, relative)
        actual = payload_identity(relative, raw)
        actual["mode"] = f"{stat.S_IMODE(payload_path.stat().st_mode):04o}"
        if actual != item:
            raise DecisionPacketError(f"packet payload identity differs: {relative}")
        identities[relative] = item

    binding = admission.build_binding(
        admission.DEFAULT_CAPSULE_ROOT,
        admission.CAPSULE_MANIFEST_SHA256,
        RESULT_ROOT,
        SESSION_ID,
    )
    if sha256_bytes(canonical_json_bytes(binding)) != manifest["binding_sha256"]:
        raise DecisionPacketError("live binding differs from the packet")
    admission.verify_decisions(
        binding,
        (root / "authorization.json").resolve(),
        identities["authorization.json"]["sha256"],
        (root / "admission.json").resolve(),
        identities["admission.json"]["sha256"],
    )
    return manifest


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    commands = value.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create affirmative decisions only")
    create.add_argument("--packet-root", type=pathlib.Path, default=DEFAULT_PACKET_ROOT)
    create.add_argument("--confirmation", required=True)
    check = commands.add_parser("check", help="read-only packet authentication")
    check.add_argument("--packet-root", type=pathlib.Path, default=DEFAULT_PACKET_ROOT)
    check.add_argument("--expected-manifest-sha256", required=True)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "create":
            if args.confirmation != EXACT_CREATE_CONFIRMATION:
                raise DecisionPacketError("exact non-execution confirmation is absent")
            payloads, manifest = build_packet()
            digest = publish_packet(args.packet_root, payloads, manifest)
            print(f"created affirmative non-executing decision packet: {args.packet_root}")
            print(f"packet manifest sha256: {digest}")
        else:
            check_packet(args.packet_root, args.expected_manifest_sha256)
            print(f"decision packet is valid without execution: {args.packet_root}")
        return 0
    except (
        DecisionPacketError,
        admission.AdmissionError,
        capsule.CapsuleError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        print(f"decision packet failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
