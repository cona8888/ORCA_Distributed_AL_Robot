#!/usr/bin/env python3
"""Validate the nonauthorizing ISO/Rock-Ridge qualification contract.

This module is deliberately static.  It authenticates contract, source-input,
host, and top-level tool bytes, but it cannot create an image, invoke a tool,
attach a device, mount a filesystem, or authorize a build.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import stat
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple


SCHEMA_VERSION = "dancers-planar-physics-iso-source-capsule-qualification-contract-v1"
VERDICT_SCHEMA_VERSION = SCHEMA_VERSION + "-verdict-v1"
EPOCH_ID = "simulator-demo-v1-physics-stress-test-epoch-1"
CONTRACT_RELATIVE_PATH = Path(
    "docs/simulator/physics_iso_source_capsule_qualification_contract_v1.json"
)
EXPECTED_CONTRACT_BYTES = 55_127
EXPECTED_CONTRACT_SHA256 = (
    "62e00380bda281eaeaf4bacf717494964306ce4a840138b62e0707caf476e5e4"
)

CONTRACT_BYTES_MAX = 1 << 20
JSON_DEPTH_MAX = 32
JSON_NODES_MAX = 200_000
STRING_BYTES_MAX = 65_536
SIGNED64_MIN = -(1 << 63)
SIGNED64_MAX = (1 << 63) - 1
READ_CHUNK = 64 * 1024

HOST_PROFILE_DOMAIN = b"dancers-planar-physics-iso-source-capsule-host-profile-v1\0"
TOOL_PROFILE_DOMAIN = b"dancers-planar-physics-iso-source-capsule-tool-profile-v1\0"
RECEIPT_DOMAIN = (
    b"dancers-planar-physics-iso-source-capsule-host-qualification-receipt-v1\0"
)

EXPECTED_TOP_KEYS = {
    "canonical_json",
    "classification",
    "current_blockers",
    "current_unqualified_receipt",
    "epoch_id",
    "exact_bindings",
    "hard_caps",
    "hash_domains",
    "hash_formulas",
    "hash_test_vectors",
    "host_profile",
    "image_profile",
    "qualification_protocol",
    "receipt_contract",
    "schema_version",
    "schemas",
    "scope",
    "tool_profile",
}

EXPECTED_SECTION_SHA256 = {
    "canonical_json": "bf88955334674acff3c0189c837c388289127143b682b1dec67c307b4ef77f22",
    "classification": "807322d7fa6cc96067d5e9a4ecfda1e2fd67eff2666a5031b3fa990e7935df63",
    "current_blockers": "014b7097abd434bad1e3f9f521d3882eed2178970f5405af8c5090d65bb3c27c",
    "current_unqualified_receipt": "05d9a501a3012937a0279c0be0bdc90bb989be9206ac1d59cf2f136a53458de1",
    "epoch_id": "69449d13e7e77cf083640385867219f06fe4d5869e8477634f8ffc97bc243ae9",
    "exact_bindings": "849e6435728cf87a8166416da5489df56026719d5f18be315040153608a855a4",
    "hard_caps": "c9e6f9b801208c124b060e16ee5969ba330dab9c0692e310b1a1516ff6bcef2e",
    "hash_domains": "71141a342f3229d45728e7fc358172c71bcfa48b76879f97a7d518f232461d82",
    "hash_formulas": "f97add2cb11cdad44af48fac8a01a795fbf7f9ffe3915cfb15809ea323b11e7a",
    "hash_test_vectors": "92d3e1e4c4d5c6500791e8d2d3500646345e78f6a09d095122480da3b530a371",
    "host_profile": "61ec655a785466df1369c5a33e3a8cf5dc7f83eb13b947cc947ca339748cd2b3",
    "image_profile": "56868f56a6976d8d8fc6f2aa08fa1d53cfa2317dfe66cb064caff512e0bd2c5d",
    "qualification_protocol": "f126ae4e1c6a3fed669eb916829df5f8c742aec6cb8fbe959c39bd76b5e05ded",
    "receipt_contract": "f0bb315fbf46b8a610b5693df6e7b7e19e6afbb74a88ba3c892afb6271f3bc48",
    "schema_version": "d5a4531d85117ccbf7a9365c95c64b97bf42273fca3155f0d4026e481cd5f8b6",
    "schemas": "11198cfe70eed6f141a2a2b5d81cc457487f275bf9edda810b5d79c058ecd2ff",
    "scope": "ce66fc50e706e6a54a53103d8420600402a146bb83e7074fbc554459ae213ec4",
    "tool_profile": "a492cf5093b582324a75d893d69b7a012edf9a1705ae1c697bdb6a1332cbc0a4",
}

EMPIRICAL_BLOCKER_IDS = [
    "p0-qualification-implementation-absent",
    "p0-atomic-noreplace-publication-flags-unproven",
    "p0-rock-ridge-name-mode-content-unqualified",
    "p0-cd9660-readonly-view-unqualified",
    "p0-backing-unlink-reread-unqualified",
    "p0-detach-and-residue-free-cleanup-unqualified",
]
PRODUCTION_BLOCKER_IDS = [
    "p0-source-materializer-and-git-reader-runtime-unfrozen",
    "p0-helper-framework-daemon-kernel-closure-unfrozen",
    "p0-raw-iso-rock-ridge-metadata-parser-absent",
    "p0-same-euid-backing-custody-unproven",
    "p0-independent-single-writer-custody-unproven",
    "p0-production-mount-identity-formula-unfrozen",
    "p0-production-source-root-verifier-absent",
    "p0-protected-capture-trust-root-and-grant-absent",
]

MAKEHYBRID_ARGV = [
    "/usr/bin/hdiutil",
    "makehybrid",
    "-o",
    "<P>/image.work.iso",
    "<P>/stage.work",
    "-iso",
    "-default-volume-name",
    "PHYSICS_ISO_QUAL_V1",
]
ATTACH_ARGV = [
    "/usr/bin/hdiutil",
    "attach",
    "-readonly",
    "-nobrowse",
    "-noautoopen",
    "-noautofsck",
    "-owners",
    "on",
    "-mountpoint",
    "<P>/mount",
    "-plist",
    "<P>/source.iso",
]
DETACH_ARGV = ["/usr/bin/hdiutil", "detach", "<WHOLE_DEVICE>"]
DISKUTIL_WHOLE_ARGV = [
    "/usr/sbin/diskutil",
    "info",
    "-plist",
    "<WHOLE_DEVICE>",
]
DISKUTIL_VOLUME_ARGV = [
    "/usr/sbin/diskutil",
    "info",
    "-plist",
    "<VOLUME_DEVICE>",
]
HDIUTIL_INFO_ARGV = ["/usr/bin/hdiutil", "info", "-plist"]

FALSE_RECEIPT_CLASSIFICATION = {
    "build_authorized": False,
    "build_capture_complete": False,
    "build_executed": False,
    "capture_complete": False,
    "execution_authorized": False,
    "official_namespace_materialized": False,
    "production_backend_eligible": False,
    "production_source_roots_materialized": False,
    "promotion_eligible": False,
    "provenance_complete": False,
    "registered_scientific_run": False,
    "scientific_evidence": False,
    "scientific_evidence_eligible": False,
    "scientific_execution_authorized": False,
    "single_writer_enforced": False,
}


class ValidationError(ValueError):
    """Raised when any frozen contract invariant fails."""


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _strict_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_float(_: str) -> Any:
    raise ValidationError("floating-point JSON is forbidden")


def _reject_constant(_: str) -> Any:
    raise ValidationError("non-finite JSON is forbidden")


def _walk_json(value: Any, depth: int = 0, counter: List[int] | None = None) -> None:
    if counter is None:
        counter = [0]
    if depth > JSON_DEPTH_MAX:
        raise ValidationError("JSON depth cap exceeded")
    counter[0] += 1
    if counter[0] > JSON_NODES_MAX:
        raise ValidationError("JSON node cap exceeded")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if not SIGNED64_MIN <= value <= SIGNED64_MAX:
            raise ValidationError("integer outside signed 64-bit range")
        return
    if type(value) is str:
        if unicodedata.normalize("NFC", value) != value:
            raise ValidationError("string is not NFC")
        if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
            raise ValidationError("surrogate code point is forbidden")
        if len(value.encode("utf-8")) > STRING_BYTES_MAX:
            raise ValidationError("string cap exceeded")
        return
    if type(value) is list:
        for item in value:
            _walk_json(item, depth + 1, counter)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValidationError("object key must be a string")
            _walk_json(key, depth + 1, counter)
            _walk_json(item, depth + 1, counter)
        return
    raise ValidationError("unsupported JSON value type")


def canonical_json_bytes(value: Any, maximum_bytes: int = CONTRACT_BYTES_MAX) -> bytes:
    if type(maximum_bytes) is not int or not 1 <= maximum_bytes <= CONTRACT_BYTES_MAX:
        raise ValidationError("invalid or raised canonical JSON cap")
    _walk_json(value)
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(raw) > maximum_bytes:
        raise ValidationError("canonical JSON cap exceeded")
    return raw


def parse_canonical_json(raw: bytes, maximum_bytes: int = CONTRACT_BYTES_MAX) -> Any:
    if type(maximum_bytes) is not int or not 1 <= maximum_bytes <= CONTRACT_BYTES_MAX:
        raise ValidationError("invalid or raised parse cap")
    if len(raw) > maximum_bytes:
        raise ValidationError("JSON input cap exceeded")
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise ValidationError("canonical file must end in exactly one LF")
    try:
        value = json.loads(
            raw[:-1].decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("invalid JSON bytes") from exc
    _walk_json(value)
    if canonical_json_bytes(value, maximum_bytes - 1) + b"\n" != raw:
        raise ValidationError("JSON file is not canonical")
    return value


def _read_fd_all(fd: int, expected_size: int, maximum_bytes: int) -> bytes:
    if expected_size > maximum_bytes:
        raise ValidationError("file cap exceeded before read")
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = os.read(fd, min(READ_CHUNK, maximum_bytes + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum_bytes:
            raise ValidationError("file cap exceeded during read")
    raw = b"".join(chunks)
    if len(raw) != expected_size:
        raise ValidationError("file size changed during read")
    return raw


def read_authenticated_file(path: Path, maximum_bytes: int) -> bytes:
    before = os.lstat(path)
    if not stat.S_ISREG(before.st_mode):
        raise ValidationError("authenticated path is not a regular file")
    if before.st_nlink != 1:
        raise ValidationError("authenticated file must be single-link")
    if before.st_uid not in (0, os.geteuid()):
        raise ValidationError("authenticated file owner is not trusted")
    if stat.S_IMODE(before.st_mode) & 0o022:
        raise ValidationError("authenticated file is group/world writable")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        identity = (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mode,
            opened.st_uid,
            opened.st_gid,
            opened.st_nlink,
        )
        if identity != (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mode,
            before.st_uid,
            before.st_gid,
            before.st_nlink,
        ):
            raise ValidationError("path changed before authenticated open")
        raw = _read_fd_all(fd, opened.st_size, maximum_bytes)
        after_fd = os.fstat(fd)
        if (
            after_fd.st_dev,
            after_fd.st_ino,
            after_fd.st_size,
            after_fd.st_mode,
            after_fd.st_uid,
            after_fd.st_gid,
            after_fd.st_nlink,
        ) != identity:
            raise ValidationError("authenticated descriptor changed during read")
    finally:
        os.close(fd)
    after_name = os.lstat(path)
    if (
        after_name.st_dev,
        after_name.st_ino,
        after_name.st_size,
        after_name.st_mode,
        after_name.st_uid,
        after_name.st_gid,
        after_name.st_nlink,
    ) != identity:
        raise ValidationError("authenticated file name changed after read")
    return raw


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _section_sha(value: Any) -> str:
    return _sha256(canonical_json_bytes(value))


def _u64be(value: int) -> bytes:
    if type(value) is not int or not 0 <= value < (1 << 64):
        raise ValidationError("invalid unsigned 64-bit length")
    return value.to_bytes(8, "big")


def _framed(raw: bytes) -> bytes:
    return _u64be(len(raw)) + raw


def _framed_json(value: Any) -> bytes:
    return _framed(canonical_json_bytes(value))


def _domain_bytes(value: Any) -> bytes:
    if type(value) is not str or not value.endswith("\\0") or "\\0" in value[:-2]:
        raise ValidationError("invalid textual hash domain")
    return value[:-2].encode("utf-8") + b"\0"


def _raw32(value: Any) -> bytes:
    if (
        type(value) is not str
        or len(value) != 64
        or any(ch not in "0123456789abcdef" for ch in value)
    ):
        raise ValidationError("raw32 input is not lowercase SHA-256 hexadecimal")
    return bytes.fromhex(value)


def _validate_hash_test_vectors(contract: Mapping[str, Any]) -> None:
    domains = contract["hash_domains"]
    vectors = contract["hash_test_vectors"]

    device = vectors["device_association_ledger"]
    device_entries = device["entries"]
    if device_entries != sorted(
        device_entries,
        key=lambda entry: (
            str(entry["image_path_sha256"]).encode("ascii"),
            str(entry["whole_device"]).encode("utf-8"),
        ),
    ):
        raise ValidationError("device-association vector order differs")
    actual = _sha256(
        _domain_bytes(domains["device_association_ledger"])
        + b"".join(_framed_json(entry) for entry in device_entries)
    )
    if actual != device["sha256"]:
        raise ValidationError("device-association hash vector differs")

    mount = vectors["mount_ledger"]
    mount_entries = mount["entries"]
    if mount_entries != sorted(
        mount_entries,
        key=lambda entry: (
            str(entry["mountpoint_sha256"]).encode("ascii"),
            str(entry["mounted_from_sha256"]).encode("ascii"),
        ),
    ):
        raise ValidationError("mount-ledger vector order differs")
    actual = _sha256(
        _domain_bytes(domains["mount_ledger"])
        + b"".join(_framed_json(entry) for entry in mount_entries)
    )
    if actual != mount["sha256"]:
        raise ValidationError("mount-ledger hash vector differs")

    diagnostic = vectors["diagnostic_mount_identity"]
    actual = _sha256(
        _domain_bytes(domains["diagnostic_mount_identity"])
        + canonical_json_bytes(diagnostic["body"])
    )
    if actual != diagnostic["sha256"]:
        raise ValidationError("diagnostic-mount hash vector differs")

    mounted_root = vectors["mounted_root_tree"]
    root_id = str(mounted_root["root_id"]).encode("utf-8")
    actual = _sha256(
        _domain_bytes(domains["mounted_root_tree"])
        + _framed(root_id)
        + _framed_json(mounted_root["entry"])
    )
    if actual != mounted_root["sha256"]:
        raise ValidationError("mounted-root-tree hash vector differs")

    mounted = vectors["mounted_tree"]
    actual = _sha256(
        _domain_bytes(domains["mounted_tree"])
        + b"".join(
            _framed(str(item["root_id"]).encode("utf-8"))
            + _raw32(item["sha256"])
            for item in mounted["root_digests"]
        )
    )
    if actual != mounted["sha256"]:
        raise ValidationError("mounted-tree hash vector differs")

    staged = vectors["staged_tree"]
    actual = _sha256(
        _domain_bytes(domains["staged_tree"])
        + b"".join(_framed_json(entry) for entry in staged["entries"])
    )
    if actual != staged["sha256"]:
        raise ValidationError("staged-tree hash vector differs")


def _check_exact_keys(value: Mapping[str, Any], expected: Iterable[str], label: str) -> None:
    if type(value) is not dict or set(value) != set(expected):
        raise ValidationError(label + " has wrong exact keys")


def _profile_digest(profile: Mapping[str, Any], domain: bytes) -> str:
    body = dict(profile)
    digest = body.pop("profile_sha256", None)
    if type(digest) is not str:
        raise ValidationError("profile digest missing")
    return _sha256(domain + canonical_json_bytes(body))


def _receipt_digest(receipt: Mapping[str, Any]) -> str:
    body = dict(receipt)
    digest = body.pop("receipt_sha256", None)
    if type(digest) is not str:
        raise ValidationError("receipt digest missing")
    return _sha256(RECEIPT_DOMAIN + canonical_json_bytes(body))


def _validate_live_binding(repository: Path, binding: Mapping[str, Any]) -> None:
    path = repository / str(binding["path"])
    maximum = max(int(binding["bytes"]), 1)
    raw = read_authenticated_file(path, maximum)
    if len(raw) != binding["bytes"] or _sha256(raw) != binding["sha256"]:
        raise ValidationError("bound repository artifact identity differs")


def _validate_live_tool(tool: Mapping[str, Any]) -> None:
    invocation = Path(str(tool["invocation_path"]))
    if tool["invocation_path_kind"] == "symlink":
        if not invocation.is_symlink() or os.readlink(invocation) != tool["symlink_target"]:
            raise ValidationError("tool invocation symlink differs")
    elif tool["invocation_path_kind"] == "regular":
        if invocation.is_symlink():
            raise ValidationError("regular tool invocation became symlink")
    else:
        raise ValidationError("unknown tool invocation path kind")
    resolved = Path(str(tool["resolved_path"]))
    raw = read_authenticated_file(resolved, 4 << 20)
    st = os.stat(resolved, follow_symlinks=False)
    if (
        len(raw) != tool["bytes"]
        or _sha256(raw) != tool["sha256"]
        or stat.S_IMODE(st.st_mode) != tool["resolved_mode"]
        or st.st_uid != tool["owner_uid"]
        or st.st_gid != tool["owner_gid"]
    ):
        raise ValidationError("live tool identity differs")


def _validate_live_host(host: Mapping[str, Any]) -> None:
    system_plist = Path("/System/Library/CoreServices/SystemVersion.plist")
    raw = read_authenticated_file(system_plist, 1 << 20)
    info = plistlib.loads(raw)
    uname = os.uname()
    if (
        info.get("ProductName") != host["os_product_name"]
        or info.get("ProductVersion") != host["os_product_version"]
        or info.get("ProductBuildVersion") != host["os_build"]
        or uname.release != host["darwin_release"]
        or uname.machine != host["hardware_machine"]
        or uname.version != host["darwin_kernel_build"]
    ):
        raise ValidationError("live host profile differs")


def validate_contract_value(contract: Any, repository: Path | None = None) -> Dict[str, Any]:
    if type(contract) is not dict:
        raise ValidationError("contract must be an object")
    _check_exact_keys(contract, EXPECTED_TOP_KEYS, "contract")
    for key, expected in EXPECTED_SECTION_SHA256.items():
        if _section_sha(contract[key]) != expected:
            raise ValidationError("contract section differs: " + key)

    if contract["schema_version"] != SCHEMA_VERSION or contract["epoch_id"] != EPOCH_ID:
        raise ValidationError("schema or epoch differs")
    classification = contract["classification"]
    for key, value in classification.items():
        if key in {"contract_only", "schema_surface_frozen"}:
            if value is not True:
                raise ValidationError("required contract classification is not true")
        elif value is not False:
            raise ValidationError("contract authorization or execution flag is not false")

    blockers = contract["current_blockers"]
    empirical_ids = [x["blocker_id"] for x in blockers["empirical_qualification_blockers"]]
    production_ids = [x["blocker_id"] for x in blockers["permanent_production_blockers"]]
    if empirical_ids != EMPIRICAL_BLOCKER_IDS or production_ids != PRODUCTION_BLOCKER_IDS:
        raise ValidationError("blocker order or identity differs")
    if any(x["priority"] != "P0" for group in blockers.values() for x in group):
        raise ValidationError("blocker priority differs")

    receipt = contract["current_unqualified_receipt"]
    _check_exact_keys(
        receipt,
        contract["schemas"]["qualification_receipt"]["exact_keys"],
        "current receipt",
    )
    if receipt["receipt_sha256"] != _receipt_digest(receipt):
        raise ValidationError("current receipt digest differs")
    if receipt["classification"] != FALSE_RECEIPT_CLASSIFICATION:
        raise ValidationError("current receipt classification differs")
    if receipt["empirical_blockers"] != EMPIRICAL_BLOCKER_IDS:
        raise ValidationError("current receipt empirical blockers differ")
    if receipt["production_blockers"] != PRODUCTION_BLOCKER_IDS:
        raise ValidationError("current receipt production blockers differ")
    if receipt["empirical_qualification_passed"] is not False:
        raise ValidationError("current receipt claims empirical pass")
    if receipt["production_backend_eligible"] is not False:
        raise ValidationError("current receipt claims production eligibility")
    for key in ("attachment", "backing_file", "cleanup", "image", "metadata_view"):
        if receipt[key] is not None:
            raise ValidationError("current receipt contains empirical evidence")
    if (
        receipt["inventories"] != []
        or receipt["mutation_probes"] != []
        or receipt["implementation_bindings"] != []
    ):
        raise ValidationError("current receipt contains empirical arrays")

    host = contract["host_profile"]
    tools = contract["tool_profile"]
    if host["profile_sha256"] != _profile_digest(host, HOST_PROFILE_DOMAIN):
        raise ValidationError("host profile digest differs")
    if tools["profile_sha256"] != _profile_digest(tools, TOOL_PROFILE_DOMAIN):
        raise ValidationError("tool profile digest differs")
    if receipt["host_profile_sha256"] != host["profile_sha256"]:
        raise ValidationError("receipt host profile binding differs")
    if receipt["tool_profile_sha256"] != tools["profile_sha256"]:
        raise ValidationError("receipt tool profile binding differs")
    if tools["dynamic_runtime_closure_authenticated"] is not False:
        raise ValidationError("tool profile overclaims dynamic closure")

    commands = contract["qualification_protocol"]["command_templates"]
    if commands != {
        "attach": ATTACH_ARGV,
        "detach": DETACH_ARGV,
        "diskutil_info_volume": DISKUTIL_VOLUME_ARGV,
        "diskutil_info_whole": DISKUTIL_WHOLE_ARGV,
        "hdiutil_info": HDIUTIL_INFO_ARGV,
        "makehybrid": MAKEHYBRID_ARGV,
    }:
        raise ValidationError("command template differs")
    flattened = [token for argv in commands.values() for token in argv]
    if any(token in flattened for token in contract["qualification_protocol"]["forbidden_argv_tokens"]):
        raise ValidationError("forbidden command token is reachable")
    if any("shell" in token.lower() for token in flattened):
        raise ValidationError("shell command surface is forbidden")

    roots = contract["image_profile"]["roots"]
    if [root["root_id"] for root in roots] != [
        "common-source",
        "optimized-source",
        "reference-source",
    ]:
        raise ValidationError("root order differs")
    if sum(root["tree_entry_count"] for root in roots) != 548:
        raise ValidationError("typed-entry count differs")
    if sum(root["regular_file_count"] for root in roots) != 477:
        raise ValidationError("regular-file count differs")
    if sum(root["total_regular_bytes"] for root in roots) != 14_595_556:
        raise ValidationError("projected payload differs")
    image_profile = contract["image_profile"]
    if (
        image_profile["enabled_filesystems"] != ["iso9660"]
        or image_profile["iso_level"] != 2
        or image_profile["rock_ridge_required"] is not True
        or image_profile["joliet_enabled"] is not False
        or image_profile["udf_enabled"] is not False
        or image_profile["mounted_empty_or_enotsup_xattrs_are_production_absence_proof"] is not False
    ):
        raise ValidationError("ISO-only or metadata boundary differs")

    domains = contract["hash_domains"]
    if len(domains) != len(set(domains.values())) or not all(
        type(value) is str and value.endswith("\\0") for value in domains.values()
    ):
        raise ValidationError("hash domains are not unique terminated literals")
    formulas = contract["hash_formulas"]
    if "length(raw_image_bytes)" not in formulas["image_id"]:
        raise ValidationError("image identity length prefix differs")
    if "mount_identity_body" not in formulas["diagnostic_mount_identity_sha256"]:
        raise ValidationError("diagnostic mount formula differs")
    _validate_hash_test_vectors(contract)

    if contract["hard_caps"]["mount_ledger_entries_max"] != 256:
        raise ValidationError("mount-ledger cap differs")
    failure_transport = contract["receipt_contract"]["failure_transport"]
    if failure_transport != {
        "argument_error_exit_code": 2,
        "failure_exit_code": 1,
        "failure_stderr_prefix": "physics ISO source-capsule qualification failed: ",
        "failure_stdout_bytes": 0,
        "success_emission": (
            "buffer one complete canonical receipt and emit it exactly once only after "
            "cleanup.complete is true"
        ),
        "success_exit_code": 0,
        "success_stderr_bytes": 0,
    }:
        raise ValidationError("failure transport or success emission differs")

    scope = contract["scope"]
    if scope["future_temp_qualification_protocol_frozen"] is not True:
        raise ValidationError("future temp protocol is not frozen")
    for key in (
        "image_or_mount_created",
        "production_backend_eligible",
        "production_manifest_emitted",
        "production_materialization_permitted",
        "registered_build_or_simulator_permitted",
        "v2_handoff_consumer_permitted",
        "v2_root_manifest_compatible",
    ):
        if scope[key] is not False:
            raise ValidationError("scope overclaims or permits production")

    if repository is not None:
        bindings = contract["exact_bindings"]
        _validate_live_binding(repository, bindings["protected_source_contract"])
        for binding in bindings["source_index_checkpoint"]["files"]:
            _validate_live_binding(repository, binding)
        for binding in bindings["host_probe_checkpoint"]["files"]:
            _validate_live_binding(repository, binding)
        for binding in bindings["source_implementations"]:
            _validate_live_binding(repository, binding)
        _validate_live_host(host)
        for tool in tools["tools"]:
            _validate_live_tool(tool)

    return {
        "contract_sha256": EXPECTED_CONTRACT_SHA256,
        "empirical_qualification_passed": False,
        "production_backend_eligible": False,
        "production_blocker_count": len(PRODUCTION_BLOCKER_IDS),
    }


def verify_contract(path: Path | None = None) -> Dict[str, Any]:
    repository = _repository_root()
    target = path if path is not None else repository / CONTRACT_RELATIVE_PATH
    raw = read_authenticated_file(target, CONTRACT_BYTES_MAX)
    if len(raw) != EXPECTED_CONTRACT_BYTES or _sha256(raw) != EXPECTED_CONTRACT_SHA256:
        raise ValidationError("contract file identity differs")
    contract = parse_canonical_json(raw)
    result = validate_contract_value(contract, repository if path is None else None)
    return result


def _verdict(result: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "classification": FALSE_RECEIPT_CLASSIFICATION,
        "contract_bytes": EXPECTED_CONTRACT_BYTES,
        "contract_sha256": result["contract_sha256"],
        "empirical_qualification_passed": False,
        "production_backend_eligible": False,
        "schema_version": VERDICT_SCHEMA_VERSION,
        "valid": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print("ISO source-capsule qualification contract validator accepts no arguments", file=sys.stderr)
        return 2
    try:
        result = verify_contract()
    except (OSError, ValidationError, ValueError, KeyError, plistlib.InvalidFileException) as exc:
        print("ISO source-capsule qualification contract validation failed: " + str(exc), file=sys.stderr)
        return 1
    sys.stdout.buffer.write(canonical_json_bytes(_verdict(result)) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
