#!/usr/bin/env python3
"""Verify the current laptop's fail-closed physics source-capsule host probe."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tempfile
import types
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
import unicodedata


PROBE_BYTES = 32_995
PROBE_SHA256 = "99ca3dab645cffce0bd8618306151a0c3310590b5dacb94195fb4bb666d68db0"
PROBE_RELATIVE = "scripts/comparison/probe_physics_source_capsule_host_v2.py"
VERDICT_SCHEMA = "dancers-planar-physics-source-capsule-host-verification-v2"
MAX_PROBE_BYTES = 1 << 20
MAX_PATH_BYTES = 1_024
READ_CHUNK_BYTES = 65_536

TOP_KEYS = {
    "schema_version", "epoch_id", "eligible", "outcome", "classification",
    "policy_binding", "parent", "observations", "cleanup", "scope", "blockers",
}
PARENT_KEYS = {
    "canonical_absolute_path", "device", "inode", "owner_uid", "mode",
    "xattrs", "acl_present", "bsd_flags",
}
OBSERVATION_KEYS = {
    "node_type", "mode", "owner_uid", "xattrs", "acl_present", "bsd_flags",
}
XATTR_KEYS = {"name", "bytes", "sha256"}
POLICY_KEYS = {
    "protected_source_policy_authenticated", "source_index_artifacts_authenticated",
    "source_input_handoff_sha256", "files",
}
POLICY_FILE_KEYS = {"path", "role", "bytes", "sha256"}
CLEANUP_KEYS = {"complete", "residual_paths", "parent_inventory_after"}
SCOPE_KEYS = {
    "source_roots_materialized", "root_manifests_issued",
    "production_materializer_implemented", "production_verifier_invoked",
    "configure_build_test_or_simulator_invoked", "network_used",
    "official_namespace_materialized", "source_policy_bytes_authenticated",
    "probe_nodes_created", "probe_nodes_removed",
}


class HostProbeVerificationError(ValueError):
    """The probe implementation or current host verdict differs."""


def _node_identity(info: os.stat_result) -> Tuple[int, int]:
    return info.st_dev, info.st_ino


def _canonical_absolute(path: Path, label: str) -> Path:
    raw = os.fspath(path)
    if (type(raw) is not str or not raw or not os.path.isabs(raw)
            or raw.startswith("//")
            or len(raw.encode("utf-8")) > MAX_PATH_BYTES or "\0" in raw
            or "\\" in raw or unicodedata.normalize("NFC", raw) != raw):
        raise HostProbeVerificationError("%s path is not bounded canonical NFC" % label)
    pure = PurePosixPath(raw)
    if pure.as_posix() != raw or os.path.normpath(raw) != raw:
        raise HostProbeVerificationError("%s path is not canonical" % label)
    return Path(raw)


def _required_flags(directory: bool) -> int:
    for name in ("O_CLOEXEC", "O_NOFOLLOW"):
        if not hasattr(os, name):
            raise HostProbeVerificationError("required descriptor flag is unavailable")
    if directory and not hasattr(os, "O_DIRECTORY"):
        raise HostProbeVerificationError("required directory flag is unavailable")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    if directory:
        flags |= os.O_DIRECTORY
    return flags


def _read_probe_source(repository: Path) -> bytes:
    path = _canonical_absolute(repository / PROBE_RELATIVE, "probe source")
    current = os.open("/", _required_flags(True))
    ancestry = []  # type: List[Tuple[int, str, Tuple[int, int]]]
    try:
        for index, component in enumerate(path.parts[1:]):
            final = index == len(path.parts[1:]) - 1
            before = os.stat(component, dir_fd=current, follow_symlinks=False)
            flags = _required_flags(not final)
            if final:
                flags |= getattr(os, "O_NONBLOCK", 0)
            child = os.open(component, flags, dir_fd=current)
            opened = os.fstat(child)
            after = os.stat(component, dir_fd=current, follow_symlinks=False)
            if (_node_identity(before) != _node_identity(opened)
                    or _node_identity(opened) != _node_identity(after)):
                os.close(child)
                raise HostProbeVerificationError("probe path changed while opened")
            if not final and not stat.S_ISDIR(opened.st_mode):
                os.close(child)
                raise HostProbeVerificationError("probe ancestor is not a directory")
            ancestry.append((os.dup(current), component, _node_identity(opened)))
            os.close(current)
            current = child
        before = os.fstat(current)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid() or before.st_mode & 0o022
                or before.st_size != PROBE_BYTES or before.st_size > MAX_PROBE_BYTES):
            raise HostProbeVerificationError("probe source identity differs")
        data = bytearray()
        remaining = before.st_size
        while remaining:
            chunk = os.read(current, min(remaining, READ_CHUNK_BYTES))
            if not chunk:
                raise HostProbeVerificationError("probe source ended early")
            data.extend(chunk)
            remaining -= len(chunk)
        if os.read(current, 1):
            raise HostProbeVerificationError("probe source grew while read")
        after = os.fstat(current)
        for parent_fd, component, expected in ancestry:
            named = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            if _node_identity(named) != expected:
                raise HostProbeVerificationError("probe ancestry changed while read")
        named_final = os.stat(os.fspath(path), follow_symlinks=False)
        if (_node_identity(after) != _node_identity(named_final)
                or before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns):
            raise HostProbeVerificationError("probe source changed while read")
    finally:
        for parent_fd, _, _ in ancestry:
            os.close(parent_fd)
        os.close(current)
    raw = bytes(data)
    if hashlib.sha256(raw).hexdigest() != PROBE_SHA256:
        raise HostProbeVerificationError("probe source SHA-256 differs")
    return raw


def _load_probe(repository: Path) -> types.ModuleType:
    path = repository / PROBE_RELATIVE
    raw = _read_probe_source(repository)
    module = types.ModuleType("dancers_authenticated_source_capsule_host_probe_v2")
    module.__file__ = os.fspath(path)
    module.__package__ = ""
    sys.modules[module.__name__] = module
    try:
        exec(compile(raw, os.fspath(path), "exec"), module.__dict__)
    except Exception:
        sys.modules.pop(module.__name__, None)
        raise
    return module


def _exact(value: Any, keys: set, label: str) -> Mapping[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise HostProbeVerificationError("%s exact key set differs" % label)
    return value


def _integer(value: Any, label: str) -> int:
    if type(value) is not int or value < 0 or value >= 1 << 63:
        raise HostProbeVerificationError("%s is not a bounded nonnegative integer" % label)
    return value


def _hex64(value: Any, label: str) -> str:
    if (type(value) is not str or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)):
        raise HostProbeVerificationError("%s is not lowercase SHA-256" % label)
    return value


def _validate_xattrs(value: Any, label: str) -> List[Mapping[str, Any]]:
    if type(value) is not list or len(value) > 256:
        raise HostProbeVerificationError("%s xattr list differs" % label)
    records = []  # type: List[Mapping[str, Any]]
    names = []  # type: List[str]
    aggregate = 0
    for item in value:
        record = _exact(item, XATTR_KEYS, "%s xattr" % label)
        name = record["name"]
        if (type(name) is not str or not name or len(name.encode("utf-8")) > 65_536
                or unicodedata.normalize("NFC", name) != name):
            raise HostProbeVerificationError("%s xattr name differs" % label)
        size = _integer(record["bytes"], "%s xattr bytes" % label)
        if size > 65_536:
            raise HostProbeVerificationError("%s xattr value exceeds its fixed bound" % label)
        aggregate += size
        if aggregate > 1 << 20:
            raise HostProbeVerificationError("%s xattr values exceed their aggregate bound" % label)
        _hex64(record["sha256"], "%s xattr SHA-256" % label)
        names.append(name)
        records.append(record)
    if names != sorted(names, key=lambda item: item.encode("utf-8")) or len(names) != len(set(names)):
        raise HostProbeVerificationError("%s xattr ordering differs" % label)
    return records


def validate_host_verdict(
    value: Any,
    probe: types.ModuleType,
    require_current_host_blocker: bool = True,
) -> Dict[str, Any]:
    verdict = dict(_exact(value, TOP_KEYS, "host verdict"))
    if verdict["schema_version"] != probe.SCHEMA_VERSION or verdict["epoch_id"] != probe.EPOCH_ID:
        raise HostProbeVerificationError("host verdict schema or epoch differs")
    if verdict["classification"] != probe.CLASSIFICATION:
        raise HostProbeVerificationError("host verdict classification is authorizing or malformed")

    policy = _exact(verdict["policy_binding"], POLICY_KEYS, "policy binding")
    if (policy["protected_source_policy_authenticated"] is not True
            or policy["source_index_artifacts_authenticated"] is not True
            or policy["source_input_handoff_sha256"]
            != "fdc48721d71547ba0455ec44b950444583b3b178836a0658714d88c0aa82630b"):
        raise HostProbeVerificationError("policy binding differs")
    expected_files = []
    for path in sorted(probe.TRUSTED_FILES, key=lambda item: item.encode("utf-8")):
        identity = probe.TRUSTED_FILES[path]
        expected_files.append({
            "path": path,
            "role": identity["role"],
            "bytes": identity["bytes"],
            "sha256": identity["sha256"],
        })
    if policy["files"] != expected_files:
        raise HostProbeVerificationError("policy file inventory differs")
    for item in policy["files"]:
        _exact(item, POLICY_FILE_KEYS, "policy file")

    parent = _exact(verdict["parent"], PARENT_KEYS, "parent")
    path = _canonical_absolute(Path(parent["canonical_absolute_path"]), "parent")
    if (path.parent != Path(os.path.realpath(tempfile.gettempdir()))
            or not path.name.startswith(probe.PARENT_PREFIX)):
        raise HostProbeVerificationError("parent is outside the canonical temporary directory")
    for key in ("device", "inode", "owner_uid", "mode", "bsd_flags"):
        _integer(parent[key], "parent %s" % key)
    if parent["mode"] != 0o700 or type(parent["acl_present"]) is not bool:
        raise HostProbeVerificationError("parent protection differs")
    parent_xattrs = _validate_xattrs(parent["xattrs"], "parent")
    opened_parent = probe._open_absolute(path, "verified diagnostic parent", directory=True)
    try:
        live_parent = os.fstat(opened_parent.descriptor)
        if (live_parent.st_dev != parent["device"] or live_parent.st_ino != parent["inode"]
                or live_parent.st_uid != parent["owner_uid"]
                or stat.S_IMODE(live_parent.st_mode) != parent["mode"]
                or getattr(live_parent, "st_flags", 0) != parent["bsd_flags"]
                or probe._xattrs(opened_parent.descriptor, "verified diagnostic parent")
                != parent_xattrs
                or probe._acl_present(opened_parent.descriptor, "verified diagnostic parent")
                is not parent["acl_present"]):
            raise HostProbeVerificationError("parent live identity or metadata differs")
        opened_parent.reauthenticate()
    finally:
        opened_parent.close()

    observations = verdict["observations"]
    if type(observations) is not list or len(observations) != 2:
        raise HostProbeVerificationError("probe observation count differs")
    expected_nodes = (("probe-directory", 0o700), ("probe-file", 0o600))
    derived_blockers = []  # type: List[str]
    observed_names = []  # type: List[str]
    for item, (node_type, mode) in zip(observations, expected_nodes):
        record = _exact(item, OBSERVATION_KEYS, "probe observation")
        if record["node_type"] != node_type or record["mode"] != mode:
            raise HostProbeVerificationError("probe observation type or mode differs")
        _integer(record["owner_uid"], "probe owner")
        if record["owner_uid"] != os.geteuid() or type(record["acl_present"]) is not bool:
            raise HostProbeVerificationError("probe observation owner or ACL type differs")
        _integer(record["bsd_flags"], "probe BSD flags")
        xattrs = _validate_xattrs(record["xattrs"], node_type)
        observed_names.extend(item["name"] for item in xattrs)
    if any(item["xattrs"] for item in observations):
        derived_blockers.append("xattrs-absent-unavailable")
    if any(item["acl_present"] for item in observations):
        derived_blockers.append("acls-absent-unavailable")
    if any(item["bsd_flags"] != 0 for item in observations):
        derived_blockers.append("bsd-flags-absent-unavailable")

    cleanup = _exact(verdict["cleanup"], CLEANUP_KEYS, "cleanup")
    if cleanup != {"complete": True, "residual_paths": [], "parent_inventory_after": []}:
        raise HostProbeVerificationError("probe cleanup is incomplete")
    scope = _exact(verdict["scope"], SCOPE_KEYS, "scope")
    expected_scope = {
        "source_roots_materialized": False,
        "root_manifests_issued": False,
        "production_materializer_implemented": False,
        "production_verifier_invoked": False,
        "configure_build_test_or_simulator_invoked": False,
        "network_used": False,
        "official_namespace_materialized": False,
        "source_policy_bytes_authenticated": True,
        "probe_nodes_created": 2,
        "probe_nodes_removed": 2,
    }
    if scope != expected_scope:
        raise HostProbeVerificationError("probe scope differs")
    if verdict["blockers"] != derived_blockers:
        raise HostProbeVerificationError("probe blocker derivation differs")
    eligible = not derived_blockers
    if type(verdict["eligible"]) is not bool or verdict["eligible"] is not eligible:
        raise HostProbeVerificationError("probe eligibility differs")
    expected_outcome = (
        "host-metadata-profile-eligible" if eligible else "host-metadata-profile-ineligible"
    )
    if verdict["outcome"] != expected_outcome:
        raise HostProbeVerificationError("probe outcome differs")
    if require_current_host_blocker:
        provenance_records = [parent_xattrs] + [item["xattrs"] for item in observations]
        if (sys.platform != "darwin" or verdict["eligible"] is not False
                or verdict["blockers"] != ["xattrs-absent-unavailable"]
                or observed_names != ["com.apple.provenance", "com.apple.provenance"]
                or any(len(items) != 1 or items[0]["name"] != "com.apple.provenance"
                       for items in provenance_records)
                or any(items != provenance_records[0] for items in provenance_records[1:])):
            raise HostProbeVerificationError("current Darwin provenance blocker differs")
    return verdict


def verify_current_host() -> Dict[str, Any]:
    repository = Path(__file__).resolve().parents[2]
    probe = _load_probe(repository)
    with tempfile.TemporaryDirectory(prefix=probe.PARENT_PREFIX) as raw_parent:
        os.chmod(raw_parent, 0o700)
        parent = Path(os.path.realpath(raw_parent))
        verdict = probe.probe_host(parent)
        validate_host_verdict(verdict, probe, require_current_host_blocker=True)
        opened_parent = probe._open_absolute(parent, "terminal diagnostic parent", directory=True)
        try:
            if probe._bounded_names(
                    opened_parent.descriptor, probe.MAX_PARENT_ENTRIES,
                    "terminal diagnostic parent"):
                raise HostProbeVerificationError("probe left a residual name")
            opened_parent.reauthenticate()
        finally:
            opened_parent.close()
    return {
        "schema_version": VERDICT_SCHEMA,
        "valid": True,
        "host_metadata_profile_eligible": False,
        "outcome": "host-metadata-profile-ineligible",
        "blockers": ["xattrs-absent-unavailable"],
        "classification": dict(probe.CLASSIFICATION),
        "scope": {
            "source_policy_bytes_authenticated": True,
            "probe_implementation_authenticated_before_execution": True,
            "probe_cleanup_verified": True,
            "source_roots_materialized": False,
            "root_manifests_issued": False,
            "configure_build_test_or_simulator_invoked": False,
            "network_used": False,
            "official_namespace_materialized": False,
        },
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    if argv is not None and list(argv):
        print("source-capsule host verification accepts no arguments", file=sys.stderr)
        return 2
    try:
        verdict = verify_current_host()
    except Exception as error:
        print("source-capsule host verification failed: %s" % error, file=sys.stderr)
        return 1
    sys.stdout.write(json.dumps(verdict, sort_keys=True, separators=(",", ":")) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
