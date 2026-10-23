#!/usr/bin/env python3
"""Verify the frozen, nonauthorizing physics source indexes v2.

The verifier authenticates source text before executing either local helper.
The committed contract validator supplies the exact v2 schemas and hash
formulas.  The separately pinned generator reconstructs the selected Git blob
closure through its fixed, local, read-only Git surface.  This module itself
does not configure, build, materialize roots, use a network, or run a simulator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import types
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


SCHEMA = "dancers-planar-physics-source-indexes-verdict-v2"
EPOCH_ID = "simulator-demo-v1-physics-stress-test-epoch-1"
CHECKPOINT_COMMIT = "b484595e007cc9b13d4c41624dfb9b8cd8e91c6e"
CHECKPOINT_TREE = "5c090dcf993e2893b2f9e5ffa7ae081bd8a27157"
INTEGRATION_COMMIT = "a138ebfb13fe9ba33d723a5ed4f7f0a931eb3999"
INTEGRATION_TREE = "f2ba2ff2faef26b917992745110e9306ad773fd2"
CONTRACT_BYTES = 19_728
CONTRACT_SHA256 = "21fb7fde7dc8b44eba46d4223e9c441f38888b6dc427ff1a7b3993eab92675a6"
CONTRACT_VALIDATOR_BYTES = 46_056
CONTRACT_VALIDATOR_SHA256 = "927cfdb7a845822499c36791fcf0bb85fafb62df5e9cb8a143bc92da828a700a"

SOURCE_GENERATOR_BYTES = 33_990
SOURCE_GENERATOR_SHA256 = "16edd827f0ba93d2c6e9699cfd4e4df8466229221172b9a55fa161a87485b77c"

ARTIFACT_IDENTITIES = {
    "common-source-v2.json": {
        "bytes": 100_897,
        "sha256": "1270fc7975691abf302627a96e6b027da0b8306790e7090f89219877eed76f19",
        "root_id": "common-source",
        "source_index_id": "bf3a8cac7c44ea03c78b63f4720446b8d44b89a3db2e4a7fb76577d04f49263b",
    },
    "optimized-source-v2.json": {
        "bytes": 14_237,
        "sha256": "f27bc056aa6face5fe0c72be3e2adf6a59d46f775214c60b7031a006a9957a33",
        "root_id": "optimized-source",
        "source_index_id": "8ee7f230fdff4d92050e8b521753d0a6c2bb8429dfabfb3f81a273db29518b8f",
    },
    "reference-source-v2.json": {
        "bytes": 14_236,
        "sha256": "f786fbc410a904f3f71bb014dc018969073b7aa13a1d5472d299172d2e1da910",
        "root_id": "reference-source",
        "source_index_id": "d26394cd945e495e8734b4fab4df42b5ebe02e82600c2f8d5842158b23f57150",
    },
    "source-input-handoff-v2.json": {
        "bytes": 1_226,
        "sha256": "a3f9b676198fba7e471f2a6500cbb94cf2e8f8c804b560b520bbf2c4d0112b4f",
        "source_input_handoff_sha256": "fdc48721d71547ba0455ec44b950444583b3b178836a0658714d88c0aa82630b",
    },
}

INDEX_ORDER = (
    "common-source-v2.json",
    "optimized-source-v2.json",
    "reference-source-v2.json",
)
HANDOFF_NAME = "source-input-handoff-v2.json"
MAX_ARTIFACT_BYTES = 2 << 20

CLASSIFICATION = {
    "build_authorized": False,
    "build_capture_complete": False,
    "build_executed": False,
    "capture_complete": False,
    "execution_authorized": False,
    "production_source_roots_materialized": False,
    "promotion_eligible": False,
    "provenance_complete": False,
    "registered_scientific_run": False,
    "scientific_evidence": False,
    "scientific_evidence_eligible": False,
    "scientific_execution_authorized": False,
}


class SourceIndexVerificationError(ValueError):
    """The source-index checkpoint or one of its trusted inputs is invalid."""


def _stat_identity(info: os.stat_result) -> Tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_uid,
        info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _read_exact_descriptor(descriptor: int, size: int, label: str) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = os.read(descriptor, min(65_536, size - len(data)))
        if not chunk:
            raise SourceIndexVerificationError("%s ended early" % label)
        data.extend(chunk)
    if os.read(descriptor, 1):
        raise SourceIndexVerificationError("%s grew while read" % label)
    return bytes(data)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _canonical_nonsymlink_path(path: Path, label: str) -> Path:
    canonical = Path(os.path.abspath(os.fspath(path)))
    if os.path.realpath(os.fspath(canonical)) != os.fspath(canonical):
        raise SourceIndexVerificationError("%s has a symlink ancestor" % label)
    return canonical


def _read_regular(path: Path, maximum: int, label: str) -> bytes:
    canonical = _canonical_nonsymlink_path(path, label)
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(os.fspath(canonical), flags)
    except OSError as error:
        raise SourceIndexVerificationError("cannot open %s: %s" % (label, error)) from error
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid() or before.st_mode & 0o022
                or before.st_size < 1 or before.st_size > maximum):
            raise SourceIndexVerificationError(
                "%s type, owner, mode, link count, or size is invalid" % label
            )
        raw = _read_exact_descriptor(descriptor, before.st_size, label)
        after = os.fstat(descriptor)
        if _stat_identity(before) != _stat_identity(after):
            raise SourceIndexVerificationError("%s changed while read" % label)
        try:
            named = os.stat(os.fspath(canonical), follow_symlinks=False)
        except OSError as error:
            raise SourceIndexVerificationError("cannot reauthenticate %s: %s" % (label, error)) from error
        if _stat_identity(after) != _stat_identity(named):
            raise SourceIndexVerificationError("%s final named identity differs" % label)
        return raw
    finally:
        os.close(descriptor)


def _load_authenticated_module(
    path: Path,
    expected_bytes: int,
    expected_sha256: str,
    module_name: str,
) -> types.ModuleType:
    if expected_bytes <= 0 or expected_sha256 == "0" * 64:
        raise SourceIndexVerificationError("%s identity has not been frozen" % module_name)
    raw = _read_regular(path, expected_bytes, module_name)
    if len(raw) != expected_bytes or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise SourceIndexVerificationError("%s exact-byte identity differs" % module_name)
    module = types.ModuleType(module_name)
    module.__file__ = os.fspath(path)
    module.__package__ = ""
    sys.modules[module_name] = module
    try:
        exec(compile(raw, os.fspath(path), "exec"), module.__dict__)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _trusted_modules(repository: Path) -> Tuple[types.ModuleType, types.ModuleType]:
    scripts = repository / "scripts/comparison"
    validator = _load_authenticated_module(
        scripts / "validate_physics_protected_source_roots_contract_v2.py",
        CONTRACT_VALIDATOR_BYTES,
        CONTRACT_VALIDATOR_SHA256,
        "dancers_trusted_protected_source_roots_contract_v2",
    )
    generator = _load_authenticated_module(
        scripts / "generate_physics_source_indexes_v2.py",
        SOURCE_GENERATOR_BYTES,
        SOURCE_GENERATOR_SHA256,
        "dancers_trusted_generate_physics_source_indexes_v2",
    )
    return validator, generator


def _read_artifact_directory(directory: Path) -> Dict[str, bytes]:
    canonical = _canonical_nonsymlink_path(directory, "source-index directory")
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(os.fspath(canonical), flags)
    except OSError as error:
        raise SourceIndexVerificationError("cannot open source-index directory: %s" % error) from error
    try:
        before = os.fstat(directory_fd)
        if not stat.S_ISDIR(before.st_mode):
            raise SourceIndexVerificationError("source-index path is not a directory")
        names = os.listdir(directory_fd)
        if set(names) != set(ARTIFACT_IDENTITIES) or len(names) != len(ARTIFACT_IDENTITIES):
            raise SourceIndexVerificationError("source-index directory inventory differs")
        result = {}
        identities = {}
        for name in sorted(names):
            flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(name, flags, dir_fd=directory_fd)
            except OSError as error:
                raise SourceIndexVerificationError("cannot open artifact %s: %s" % (name, error)) from error
            try:
                info = os.fstat(descriptor)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_size < 1 or info.st_size > MAX_ARTIFACT_BYTES):
                    raise SourceIndexVerificationError("artifact %s type, links, or size differ" % name)
                raw = _read_exact_descriptor(descriptor, info.st_size, "artifact %s" % name)
                after = os.fstat(descriptor)
                if _stat_identity(info) != _stat_identity(after):
                    raise SourceIndexVerificationError("artifact %s changed while read" % name)
                named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if _stat_identity(after) != _stat_identity(named):
                    raise SourceIndexVerificationError("artifact %s final named identity differs" % name)
                result[name] = raw
                identities[name] = (_stat_identity(after), hashlib.sha256(raw).hexdigest())
            finally:
                os.close(descriptor)
        after_directory = os.fstat(directory_fd)
        if _stat_identity(before) != _stat_identity(after_directory):
            raise SourceIndexVerificationError("source-index directory changed while read")
        named_directory = os.stat(os.fspath(canonical), follow_symlinks=False)
        if _stat_identity(after_directory) != _stat_identity(named_directory):
            raise SourceIndexVerificationError("source-index directory final named identity differs")
        if set(os.listdir(directory_fd)) != set(ARTIFACT_IDENTITIES):
            raise SourceIndexVerificationError("source-index directory changed while read")
        for name in sorted(ARTIFACT_IDENTITIES):
            flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
            try:
                descriptor = os.open(name, flags, dir_fd=directory_fd)
            except OSError as error:
                raise SourceIndexVerificationError(
                    "cannot terminally reopen artifact %s: %s" % (name, error)
                ) from error
            try:
                info = os.fstat(descriptor)
                expected_info, expected_sha = identities[name]
                if _stat_identity(info) != expected_info:
                    raise SourceIndexVerificationError("artifact %s terminal identity differs" % name)
                raw = _read_exact_descriptor(descriptor, info.st_size, "artifact %s terminal read" % name)
                after = os.fstat(descriptor)
                named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if (_stat_identity(after) != expected_info
                        or _stat_identity(named) != expected_info
                        or hashlib.sha256(raw).hexdigest() != expected_sha
                        or raw != result[name]):
                    raise SourceIndexVerificationError("artifact %s terminal reauthentication differs" % name)
            finally:
                os.close(descriptor)
        terminal_directory = os.fstat(directory_fd)
        terminal_named_directory = os.stat(os.fspath(canonical), follow_symlinks=False)
        if (_stat_identity(terminal_directory) != _stat_identity(before)
                or _stat_identity(terminal_named_directory) != _stat_identity(before)
                or set(os.listdir(directory_fd)) != set(ARTIFACT_IDENTITIES)):
            raise SourceIndexVerificationError(
                "source-index directory terminal named reauthentication differs"
            )
        return result
    finally:
        os.close(directory_fd)


def _validate_live_bindings(repository: Path, validator: types.ModuleType) -> Mapping[str, Any]:
    contract_path = repository / "docs/simulator/physics_protected_source_roots_contract_v2.json"
    raw = _read_regular(contract_path, CONTRACT_BYTES, "protected-source-roots contract")
    if len(raw) != CONTRACT_BYTES or hashlib.sha256(raw).hexdigest() != CONTRACT_SHA256:
        raise SourceIndexVerificationError("protected-source-roots contract identity differs")
    verdict = validator.validate_contract_file(contract_path)
    if not verdict.get("valid"):
        raise SourceIndexVerificationError("protected-source-roots contract did not validate")
    value = validator.parse_canonical_json(raw)
    integration = value.get("design_bindings", {}).get("integration_anchor", {})
    if (integration.get("commit") != INTEGRATION_COMMIT
            or integration.get("tree") != INTEGRATION_TREE):
        raise SourceIndexVerificationError("integration anchor differs")
    if any(value.get("classification", {}).get(key) is not False for key in CLASSIFICATION):
        raise SourceIndexVerificationError("contract authorization classification differs")
    return value


def verify_source_indexes(directory: Optional[Path] = None) -> Dict[str, Any]:
    repository = _repository_root()
    validator, generator = _trusted_modules(repository)
    _validate_live_bindings(repository, validator)
    source_directory = directory or repository / "comparison/physics-superbuild-v1/source-indexes"
    actual = _read_artifact_directory(source_directory)

    receipt = {}
    for name, identity in ARTIFACT_IDENTITIES.items():
        raw = actual[name]
        if (len(raw) != identity["bytes"]
                or hashlib.sha256(raw).hexdigest() != identity["sha256"]):
            raise SourceIndexVerificationError("artifact %s exact identity differs" % name)
        receipt[name] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}

    generated = generator.generate_source_index_artifacts(repository)
    generator_bindings = {
        "CHECKPOINT_COMMIT": CHECKPOINT_COMMIT,
        "CHECKPOINT_TREE": CHECKPOINT_TREE,
        "INTEGRATION_COMMIT": INTEGRATION_COMMIT,
        "INTEGRATION_TREE": INTEGRATION_TREE,
    }
    for attribute, expected in generator_bindings.items():
        if getattr(generator, attribute, None) != expected:
            raise SourceIndexVerificationError("trusted generator binding %s differs" % attribute)
    if type(generated) is not dict or set(generated) != set(ARTIFACT_IDENTITIES):
        raise SourceIndexVerificationError("trusted generator returned the wrong artifact set")

    documents = {}
    for name, identity in ARTIFACT_IDENTITIES.items():
        raw = actual[name]
        if generated[name] != raw:
            raise SourceIndexVerificationError("artifact %s differs from Git reconstruction" % name)
        document = validator.parse_canonical_json(raw, validator.SOURCE_INDEX_BYTES_MAX)
        documents[name] = document

    indexes = [validator.validate_source_index(documents[name]) for name in INDEX_ORDER]
    for name, index in zip(INDEX_ORDER, indexes):
        identity = ARTIFACT_IDENTITIES[name]
        if (index["root_id"] != identity["root_id"]
                or index["source_index_id"] != identity["source_index_id"]):
            raise SourceIndexVerificationError("artifact %s index identity differs" % name)
        receipt[name]["root_id"] = index["root_id"]
        receipt[name]["source_index_id"] = index["source_index_id"]

    handoff = validator.validate_source_input_handoff(documents[HANDOFF_NAME], indexes)
    handoff_identity = ARTIFACT_IDENTITIES[HANDOFF_NAME]
    if handoff["source_input_handoff_sha256"] != handoff_identity["source_input_handoff_sha256"]:
        raise SourceIndexVerificationError("source-input handoff identity differs")
    if any(value is not False for value in handoff["classification"].values()):
        raise SourceIndexVerificationError("source-input handoff is not categorically nonauthorizing")
    receipt[HANDOFF_NAME]["source_input_handoff_sha256"] = handoff["source_input_handoff_sha256"]

    return {
        "schema_version": SCHEMA,
        "epoch_id": EPOCH_ID,
        "valid": True,
        "classification": dict(CLASSIFICATION),
        "bindings": {
            "checkpoint_commit": CHECKPOINT_COMMIT,
            "checkpoint_tree": CHECKPOINT_TREE,
            "integration_commit": INTEGRATION_COMMIT,
            "integration_tree": INTEGRATION_TREE,
            "contract_bytes": CONTRACT_BYTES,
            "contract_sha256": CONTRACT_SHA256,
            "contract_validator_bytes": CONTRACT_VALIDATOR_BYTES,
            "contract_validator_sha256": CONTRACT_VALIDATOR_SHA256,
            "source_generator_bytes": SOURCE_GENERATOR_BYTES,
            "source_generator_sha256": SOURCE_GENERATOR_SHA256,
        },
        "artifacts": receipt,
        "scope": {
            "action_plan_created_by_verifier": False,
            "build_input_manifest_created_by_verifier": False,
            "capture_grant_created_by_verifier": False,
            "capture_trust_root_created_by_verifier": False,
            "git_closure_recomputed_read_only": True,
            "official_namespace_created_by_verifier": False,
            "production_source_roots_created_by_verifier": False,
            "source_indexes_verified": True,
            "verifier_invoked_configure_build_or_simulator": False,
        },
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source_index_directory",
        nargs="?",
        type=Path,
        help="alternate exact four-file directory, used by temp-only mutation tests",
    )
    arguments = parser.parse_args(argv)
    try:
        verdict = verify_source_indexes(arguments.source_index_directory)
    except Exception as error:
        print("source-index verification failed: %s" % error, file=sys.stderr)
        return 1
    print(json.dumps(verdict, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
