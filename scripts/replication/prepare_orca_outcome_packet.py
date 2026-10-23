#!/usr/bin/env python3
"""Create or authenticate the sealed DANCeRS--ORCA session-002 outcome packet.

``create`` reruns the frozen read-only analyzer, requires its already observed
canonical identity, and writes the full analysis plus a compact mechanical
projection. ``check`` authenticates an existing packet from an externally
supplied manifest SHA-256. With ``--recompute-analysis``, it also reruns the
frozen analyzer and requires byte-identical analysis output.

This reporting tool has no execution, retry, resume, or result-writing surface.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import shutil
import stat
import sys
import tempfile
from typing import Any, Mapping, Sequence


REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.replication import analyze_orca_comparison as analyzer
from scripts.replication import prepare_orca_execution_capsule as capsule


SCHEMA = "dancers-orca-outcome-packet-v1"
OUTCOME_SCHEMA = "dancers-orca-confirmatory-outcome-v1"
PACKET_ID = "dancers-orca-v1-session-002-outcome"
STUDY_ID = "dancers-orca-v1"
SESSION_ID = "dancers-orca-v1-session-002"
SESSION_ROOT = (REPO / "results/publication/dancers-orca-v1-session-002").resolve()
INPUT_ROOT = (REPO / "comparison/publication/dancers-orca-v1").resolve()
DEFAULT_PACKET_ROOT = (
    REPO / "comparison/publication/dancers-orca-v1-session-002-outcome-packet"
).resolve()
ANALYZER_SOURCE = (REPO / "scripts/replication/analyze_orca_comparison.py").resolve()
GENERATOR_SOURCE = pathlib.Path(__file__).resolve()
DECISION_PACKET_MANIFEST_SHA256 = (
    "cab4350b1f1bbfffe9485303e674c2e60f24dcd8ab6f111831bccee3c9c3d354"
)
CAPSULE_MANIFEST_SHA256 = (
    "aa1d4229c115e3742c07328d09034d5e62f83be70b97c0d71284a01315cd0c91"
)
INPUT_MANIFEST_SHA256 = (
    "3fc8a7572ed6c89eecfd8cdc1f0db2c9b552e63f60117e83bacf0ad24cd153f7"
)
SCHEDULE_SHA256 = (
    "244d268f37a976bf7af8f11ce0bb02430bd72c32372a4bb040e2b586181328c4"
)
PLAN_SHA256 = "8cc08dac76f74898b4c2593fabe99cb3d5ed797330ca37aceaf693538ba6230f"
AUTHORIZATION_SHA256 = (
    "ab9b002849782a16871ef9f7ad4192e211c07866e670fe85b73e400a54f5a3a7"
)
ADMISSION_SHA256 = (
    "7c7d902f0ecca45a697f0d6b3c1f8e7953def622f689106ef8374b6eedfaf415"
)
EXPECTED_ANALYSIS_SHA256 = (
    "ee7a0f445b5ef0efa55ccc0b06cd2a7a12afdf294dd0d4b8b22dd70cc134be51"
)
EXPECTED_ANALYSIS_BYTES = 2_990_128
SESSION_HASHES = {
    "input_manifest.json": INPUT_MANIFEST_SHA256,
    "plan.json": PLAN_SHA256,
    "records.ndjson": "8f27dacd554bc7cb905862c0eed7546cf9c156d00288a1e420a2c8efa35e6896",
    "schedule.json": SCHEDULE_SHA256,
    "session.json": "072afe57dda6de3adc73deb8963657c709ee36113107f6c505d8e35c860bb92d",
}
PAYLOADS = ("analysis.json", "outcome.json")
FILE_MODE = "0644"
EXPECTED_PROCESSES = 363
EXACT_CREATE_CONFIRMATION = "CREATE-SEALED-OUTCOME-PACKET"
SHA256_RE = re.compile(r"[0-9a-f]{64}")
AUTHENTICATION_RULE = (
    "check requires an externally supplied exact packet manifest SHA-256; "
    "the sidecar is consistency evidence only"
)


class OutcomePacketError(RuntimeError):
    """A fail-closed sealed-outcome packet error."""


def canonical_json_bytes(value: object, *, pretty: bool = False) -> bytes:
    return analyzer.canonical_json_bytes(value, pretty=pretty)


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _identity(path: pathlib.Path, display_path: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise OutcomePacketError(f"required regular file is absent or unsafe: {display_path}")
    raw = path.read_bytes()
    return {
        "bytes": len(raw),
        "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}",
        "path": display_path,
        "sha256": sha256_bytes(raw),
    }


def _source_identity(path: pathlib.Path) -> dict[str, Any]:
    return _identity(path, str(path.relative_to(REPO)))


def _payload_identity(relative: str, raw: bytes) -> dict[str, Any]:
    return {
        "bytes": len(raw),
        "mode": FILE_MODE,
        "path": relative,
        "sha256": sha256_bytes(raw),
    }


def _strict_json(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = capsule.parse_strict_json(raw, label)
    except (capsule.CapsuleError, ValueError) as error:
        raise OutcomePacketError(f"{label} is not strict JSON: {error}") from error
    if not isinstance(value, dict):
        raise OutcomePacketError(f"{label} is not a JSON object")
    return value


def _validate_live_session() -> list[dict[str, Any]]:
    if SESSION_ROOT.is_symlink() or not SESSION_ROOT.is_dir():
        raise OutcomePacketError("sealed session root is absent or unsafe")
    expected_entries = {"attempts", *SESSION_HASHES}
    if {item.name for item in SESSION_ROOT.iterdir()} != expected_entries:
        raise OutcomePacketError("sealed session root inventory differs")
    identities: list[dict[str, Any]] = []
    for name, expected_digest in SESSION_HASHES.items():
        identity = _identity(
            SESSION_ROOT / name,
            str((SESSION_ROOT / name).relative_to(REPO)),
        )
        if identity["sha256"] != expected_digest:
            raise OutcomePacketError(f"sealed session identity differs: {name}")
        identities.append(identity)
    session_raw = (SESSION_ROOT / "session.json").read_bytes()
    session = _strict_json(session_raw, "sealed session")
    if session_raw != canonical_json_bytes(session):
        raise OutcomePacketError("sealed session is not canonical JSON")
    if (
        session.get("execution_complete") is not True
        or session.get("completed_processes") != EXPECTED_PROCESSES
        or session.get("expected_processes") != EXPECTED_PROCESSES
        or session.get("outcome_counts") != {"completed": EXPECTED_PROCESSES}
        or session.get("plan", {}).get("sha256") != PLAN_SHA256
        or session.get("records", {}).get("sha256") != SESSION_HASHES["records.ndjson"]
    ):
        raise OutcomePacketError("sealed session terminal contract differs")
    attempts = SESSION_ROOT / "attempts"
    if attempts.is_symlink() or not attempts.is_dir():
        raise OutcomePacketError("sealed attempt root is absent or unsafe")
    entries = list(attempts.iterdir())
    if any(item.name.startswith(".staging-") for item in entries):
        raise OutcomePacketError("sealed attempt root contains staging state")
    directories = {item.name for item in entries if item.is_dir() and not item.is_symlink()}
    expected_directories = {
        pathlib.Path(item["path"]).parent.name for item in session["attempt_records"]
    }
    if directories != expected_directories or len(directories) != EXPECTED_PROCESSES:
        raise OutcomePacketError("sealed attempt directory inventory differs")
    return sorted(identities, key=lambda item: item["path"])


def build_analysis() -> dict[str, Any]:
    return analyzer.analyze_session(
        INPUT_ROOT,
        SESSION_ROOT,
        expected_input_manifest_sha256=INPUT_MANIFEST_SHA256,
        expected_schedule_sha256=SCHEDULE_SHA256,
        expected_plan_sha256=PLAN_SHA256,
        expected_authorization_manifest_sha256=AUTHORIZATION_SHA256,
        expected_admission_manifest_sha256=ADMISSION_SHA256,
    )


def _behavior_projection(summary: Mapping[str, Any]) -> dict[str, Any]:
    if summary.get("estimable") is not True:
        return {
            "estimable": False,
            "status": summary.get("status"),
        }
    interval = summary["mean_difference_interval"]
    return {
        "difference_direction": summary["difference_direction"],
        "estimable": True,
        "lower_95": interval["lower_95"],
        "mean_difference": summary["mean_difference"],
        "median_difference": summary["median_difference"],
        "upper_95": interval["upper_95"],
    }


def _resource_projection(summary: Mapping[str, Any]) -> dict[str, Any]:
    if summary.get("estimable") is not True:
        return {
            "estimable": False,
            "status": summary.get("status"),
        }
    return {
        "estimable": True,
        "geometric_mean_ratio": summary["geometric_mean_ratio"],
        "lower_95": summary["lower_95"],
        "registered_gate_passed": summary["registered_gate_passed"],
        "upper_95": summary["upper_95"],
    }


def outcome_document(analysis: Mapping[str, Any]) -> dict[str, Any]:
    integrity = analysis.get("integrity")
    expected_integrity = {
        "admission_manifest_sha256": ADMISSION_SHA256,
        "authorization_manifest_sha256": AUTHORIZATION_SHA256,
        "input_manifest_sha256": INPUT_MANIFEST_SHA256,
        "plan_sha256": PLAN_SHA256,
        "schedule_sha256": SCHEDULE_SHA256,
        "sealed_session_valid": True,
    }
    if integrity != expected_integrity:
        raise OutcomePacketError("analysis integrity binding differs")
    behavior_metrics = analysis["behavior"]["open"]["metric_summaries"]
    resource_metrics = analysis["resources"]["open"]["metric_summaries"]
    if set(behavior_metrics) != set(analyzer.BEHAVIOR_DIFFERENCE_METRICS):
        raise OutcomePacketError("registered behavior metric inventory differs")
    if set(resource_metrics) != set(analyzer.RESOURCE_RATIO_METRICS):
        raise OutcomePacketError("registered resource metric inventory differs")
    fidelity = analysis["dancers_fidelity"]
    repeatability = analysis["orca_repeatability"]
    gates = {
        "all_23_dancers_scenes_passed": fidelity["all_scenes_passed"],
        "all_23_scene_pairs_estimable": (
            len(analysis["behavior"]["scene_pairs"]) == 23
            and all(row["estimable"] for row in analysis["behavior"]["scene_pairs"])
        ),
        "all_26_orca_repeatability_cohorts_passed": (
            repeatability["all_main_passed"]
            and repeatability["all_sensitivity_passed"]
            and len(repeatability["rows"]) == 26
        ),
        "all_3_registered_resource_gates_passed": all(
            item.get("registered_gate_passed") is True
            for item in resource_metrics.values()
        ),
    }
    if not all(gates.values()):
        raise OutcomePacketError("one or more confirmatory gates did not pass")
    return {
        "behavior_open_fixed_world": {
            metric: _behavior_projection(behavior_metrics[metric])
            for metric in analyzer.BEHAVIOR_DIFFERENCE_METRICS
        },
        "claim_boundary": {
            "blanket_dancers_superiority_to_orca_supported": False,
            "fixed_input_fixed_open_world_only": True,
            "implementation_claim": (
                "optimized DANCeRS preserves registered reference behavior exactly "
                "and improves registered wall and CPU performance"
            ),
            "orca_comparison_claim": (
                "DANCeRS and ORCA show registered behavioral trade-offs; no universal "
                "planner-dominance claim is supported"
            ),
            "population_generalization_supported": False,
            "real_world_generalization_supported": False,
        },
        "confirmatory_gates": gates,
        "integrity": dict(expected_integrity),
        "resource_open_fixed_world": {
            metric: _resource_projection(resource_metrics[metric])
            for metric in analyzer.RESOURCE_RATIO_METRICS
        },
        "schema_version": OUTCOME_SCHEMA,
        "session_id": SESSION_ID,
        "study_id": STUDY_ID,
    }


def build_packet() -> tuple[dict[str, bytes], dict[str, Any]]:
    session_files = _validate_live_session()
    analysis = build_analysis()
    analysis_raw = canonical_json_bytes(analysis, pretty=True)
    if (
        len(analysis_raw) != EXPECTED_ANALYSIS_BYTES
        or sha256_bytes(analysis_raw) != EXPECTED_ANALYSIS_SHA256
    ):
        raise OutcomePacketError("recomputed analysis differs from its external identity")
    outcome_raw = canonical_json_bytes(outcome_document(analysis), pretty=True)
    payloads = {
        "analysis.json": analysis_raw,
        "outcome.json": outcome_raw,
    }
    manifest = {
        "analysis": {
            "bytes": EXPECTED_ANALYSIS_BYTES,
            "sha256": EXPECTED_ANALYSIS_SHA256,
        },
        "authentication": {"rule": AUTHENTICATION_RULE},
        "decision_packet_manifest_sha256": DECISION_PACKET_MANIFEST_SHA256,
        "execution_capsule_manifest_sha256": CAPSULE_MANIFEST_SHA256,
        "files": [_payload_identity(name, payloads[name]) for name in PAYLOADS],
        "packet_id": PACKET_ID,
        "schema_version": SCHEMA,
        "session_files": session_files,
        "session_id": SESSION_ID,
        "sources": {
            "analyzer": _source_identity(ANALYZER_SOURCE),
            "outcome_packet_generator": _source_identity(GENERATOR_SOURCE),
        },
        "study_id": STUDY_ID,
    }
    return payloads, manifest


def _write_exclusive(path: pathlib.Path, raw: bytes) -> None:
    with path.open("xb") as handle:
        os.fchmod(handle.fileno(), int(FILE_MODE, 8))
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def publish_packet(
    root: pathlib.Path,
    payloads: Mapping[str, bytes],
    manifest: Mapping[str, Any],
) -> str:
    if not root.is_absolute() or root != root.resolve() or root.is_symlink():
        raise OutcomePacketError("packet root must be absolute and normalized")
    if root.exists():
        raise OutcomePacketError("packet root already exists; replacement is forbidden")
    if root.parent.is_symlink() or not root.parent.is_dir():
        raise OutcomePacketError("packet parent is absent or unsafe")
    if tuple(sorted(payloads)) != PAYLOADS:
        raise OutcomePacketError("packet payload inventory differs")
    stage = pathlib.Path(tempfile.mkdtemp(prefix=f".{root.name}.stage-", dir=root.parent))
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


def _read_payload(path: pathlib.Path, label: str, *, pretty: bool) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise OutcomePacketError(f"{label} is absent or unsafe")
    raw = path.read_bytes()
    value = _strict_json(raw, label)
    if raw != canonical_json_bytes(value, pretty=pretty):
        raise OutcomePacketError(f"{label} is not canonical JSON")
    return value, raw


def check_packet(
    root: pathlib.Path,
    expected_manifest_sha256: str,
    *,
    recompute_analysis: bool = False,
) -> dict[str, Any]:
    if SHA256_RE.fullmatch(expected_manifest_sha256) is None:
        raise OutcomePacketError("external packet manifest SHA-256 is invalid")
    if not root.is_absolute() or root != root.resolve() or root.is_symlink() or not root.is_dir():
        raise OutcomePacketError("packet root must be a real absolute directory")
    expected_names = {*PAYLOADS, "manifest.json", "manifest.sha256"}
    entries = list(root.iterdir())
    if {item.name for item in entries} != expected_names:
        raise OutcomePacketError("packet file inventory differs")
    if any(item.is_symlink() or not item.is_file() for item in entries):
        raise OutcomePacketError("packet contains an unsafe entry")
    manifest, manifest_raw = _read_payload(root / "manifest.json", "packet manifest", pretty=False)
    if sha256_bytes(manifest_raw) != expected_manifest_sha256:
        raise OutcomePacketError("packet differs from the external trust root")
    if (root / "manifest.sha256").read_bytes() != (
        f"{expected_manifest_sha256}  manifest.json\n".encode("ascii")
    ):
        raise OutcomePacketError("packet sidecar differs")
    if (
        manifest.get("schema_version") != SCHEMA
        or manifest.get("packet_id") != PACKET_ID
        or manifest.get("study_id") != STUDY_ID
        or manifest.get("session_id") != SESSION_ID
        or manifest.get("authentication") != {"rule": AUTHENTICATION_RULE}
        or manifest.get("analysis")
        != {"bytes": EXPECTED_ANALYSIS_BYTES, "sha256": EXPECTED_ANALYSIS_SHA256}
        or manifest.get("decision_packet_manifest_sha256")
        != DECISION_PACKET_MANIFEST_SHA256
        or manifest.get("execution_capsule_manifest_sha256")
        != CAPSULE_MANIFEST_SHA256
        or manifest.get("session_files") != _validate_live_session()
        or manifest.get("sources")
        != {
            "analyzer": _source_identity(ANALYZER_SOURCE),
            "outcome_packet_generator": _source_identity(GENERATOR_SOURCE),
        }
    ):
        raise OutcomePacketError("packet fixed contract differs")
    records = manifest.get("files")
    if not isinstance(records, list) or tuple(item.get("path") for item in records) != PAYLOADS:
        raise OutcomePacketError("packet payload records differ")
    payload_values: dict[str, dict[str, Any]] = {}
    payload_raw: dict[str, bytes] = {}
    for item in records:
        if not isinstance(item, dict) or set(item) != {"bytes", "mode", "path", "sha256"}:
            raise OutcomePacketError("packet payload identity schema differs")
        name = item["path"]
        value, raw = _read_payload(root / name, name, pretty=True)
        actual = _payload_identity(name, raw)
        actual["mode"] = f"{stat.S_IMODE((root / name).stat().st_mode):04o}"
        if actual != item:
            raise OutcomePacketError(f"packet payload identity differs: {name}")
        payload_values[name] = value
        payload_raw[name] = raw
    analysis = payload_values["analysis.json"]
    if outcome_document(analysis) != payload_values["outcome.json"]:
        raise OutcomePacketError("compact outcome projection differs from full analysis")
    if recompute_analysis:
        recomputed = canonical_json_bytes(build_analysis(), pretty=True)
        if recomputed != payload_raw["analysis.json"]:
            raise OutcomePacketError("recomputed frozen analysis differs")
    return manifest


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    commands = value.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="create the sealed outcome packet")
    create.add_argument("--packet-root", type=pathlib.Path, default=DEFAULT_PACKET_ROOT)
    create.add_argument("--confirmation", required=True)
    check = commands.add_parser("check", help="authenticate the sealed outcome packet")
    check.add_argument("--packet-root", type=pathlib.Path, default=DEFAULT_PACKET_ROOT)
    check.add_argument("--expected-manifest-sha256", required=True)
    check.add_argument("--recompute-analysis", action="store_true")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "create":
            if args.confirmation != EXACT_CREATE_CONFIRMATION:
                raise OutcomePacketError("exact non-execution confirmation is absent")
            payloads, manifest = build_packet()
            digest = publish_packet(args.packet_root.resolve(), payloads, manifest)
            print(f"created sealed outcome packet: {args.packet_root.resolve()}")
            print(f"packet manifest sha256: {digest}")
        else:
            check_packet(
                args.packet_root.resolve(),
                args.expected_manifest_sha256,
                recompute_analysis=args.recompute_analysis,
            )
            print(f"sealed outcome packet is valid: {args.packet_root.resolve()}")
        return 0
    except (
        OutcomePacketError,
        analyzer.AnalysisError,
        capsule.CapsuleError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        print(f"outcome packet failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
