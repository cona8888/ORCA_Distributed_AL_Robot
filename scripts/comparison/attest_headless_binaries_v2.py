#!/usr/bin/env python3
"""Produce an in-memory v2 attestation for a sealed runtime-root build.

The attester is static and read-only: it delegates to the static build
verifier, consumes its already verified in-memory artifact snapshot, and emits canonical JSON to
stdout.  It never executes a binary and never publishes a plan, result, or
attestation file.  All scientific and execution-authority flags remain false.
"""

from __future__ import annotations

import sys
sys.dont_write_bytecode = True
if (
    sys.flags.isolated != 1
    or sys.flags.no_site != 1
    or sys.flags.dont_write_bytecode != 1
):
    raise SystemExit("headless-binary-attestation-v2: reject: public checkpoint invocation requires python -I -S -B")

import argparse
import hashlib
import os
from pathlib import Path
import stat
from typing import Any, Optional, Sequence

SCHEMA_VERSION = "dancers-headless-binary-attestation-v2"
TRACK_ID = "simulator-demo-v1-headless-epoch-1"
VERIFIER_PATH = "scripts/comparison/verify_headless_runtime_build_v1.py"
EXPECTED_VERIFIER_BYTES = 56_953
EXPECTED_VERIFIER_SHA256 = "55eacab49479963a828d1091dd2153336d5042376f0dd88f838aac8bcfb91430"
ATTESTER_PATH = "scripts/comparison/attest_headless_binaries_v2.py"
_MAX_IMPLEMENTATION_BYTES = 2 * 1024 * 1024


class _BootstrapError(RuntimeError):
    """An implementation source could not be captured before execution."""


def _source_fingerprint(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_uid,
        info.st_gid,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _finish_source_fd_custody(
    owned_fds: list[int], primary: Optional[BaseException], label: str
) -> None:
    """Clear each numeric ownership token before its one close attempt."""
    cleanup_error: Optional[BaseException] = None
    while owned_fds:
        owned_fd = owned_fds.pop()
        try:
            os.close(owned_fd)
        except BaseException as exc:
            if cleanup_error is None:
                cleanup_error = exc
    if primary is not None:
        if isinstance(primary, _BootstrapError):
            if cleanup_error is not None:
                raise primary from cleanup_error
            raise primary
        if isinstance(primary, OSError):
            wrapped = _BootstrapError(f"{label} source capture failed: {primary}")
            if cleanup_error is not None:
                raise wrapped from primary
            raise wrapped from primary
        if cleanup_error is not None:
            raise primary from cleanup_error
        raise primary
    if cleanup_error is not None:
        if isinstance(cleanup_error, OSError):
            raise _BootstrapError(
                f"{label} source descriptor cleanup failed: {cleanup_error}"
            ) from cleanup_error
        raise cleanup_error


def _capture_authenticated_source(
    repository_root: Path,
    relative: str,
    expected_bytes: int,
    expected_sha256: str,
    label: str,
) -> tuple[Path, bytes]:
    """Capture exact source through retained FDs without consulting a pyc."""
    lexical_root = repository_root.absolute()
    try:
        canonical_root = repository_root.resolve(strict=True)
    except OSError as exc:
        raise _BootstrapError(f"{label} repository root is unavailable: {exc}") from exc
    if lexical_root != canonical_root:
        raise _BootstrapError(f"{label} repository root is not canonical")
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(part in ("", ".", "..") for part in parts):
        raise _BootstrapError(f"{label} relative path is invalid")
    owned_fds: list[int] = []
    primary: Optional[BaseException] = None
    try:
        root_fd = os.open(
            canonical_root,
            os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
        )
        owned_fds.append(root_fd)
        root_path_info = os.stat(canonical_root, follow_symlinks=False)
        if _source_fingerprint(os.fstat(root_fd)) != _source_fingerprint(root_path_info):
            raise _BootstrapError(f"{label} repository root changed during capture")
        current_fd = root_fd
        for component in parts[:-1]:
            next_fd = os.open(
                component,
                os.O_RDONLY | os.O_CLOEXEC | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            owned_fds.append(next_fd)
            current_fd = next_fd
        name = parts[-1]
        path_before = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(path_before.st_mode)
            or path_before.st_nlink != 1
            or path_before.st_size != expected_bytes
            or path_before.st_size > _MAX_IMPLEMENTATION_BYTES
        ):
            raise _BootstrapError(f"{label} implementation identity differs")
        source_fd = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=current_fd,
        )
        owned_fds.append(source_fd)
        fd_before = os.fstat(source_fd)
        if _source_fingerprint(fd_before) != _source_fingerprint(path_before):
            raise _BootstrapError(f"{label} source changed before capture")
        chunks: list[bytes] = []
        remaining = expected_bytes + 1
        while remaining:
            try:
                chunk = os.read(source_fd, min(remaining, 128 * 1024))
            except InterruptedError:
                continue
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        fd_after = os.fstat(source_fd)
        path_after = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
        if not (
            _source_fingerprint(fd_before)
            == _source_fingerprint(fd_after)
            == _source_fingerprint(path_after)
        ):
            raise _BootstrapError(f"{label} source changed during capture")
    except BaseException as exc:
        primary = exc
    _finish_source_fd_custody(owned_fds, primary, label)
    if len(raw) != expected_bytes or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise _BootstrapError(f"{label} implementation identity differs")
    return canonical_root.joinpath(*parts), raw


def _exec_authenticated_source(module_name: str, path: Path, raw: bytes) -> Any:
    module = type(sys)(module_name)
    module.__file__ = str(path)
    module.__package__ = ""
    module.__loader__ = None
    module.__spec__ = None
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        code = compile(raw, str(path), "exec", dont_inherit=True, optimize=0)
        exec(code, module.__dict__)
    except BaseException:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
        raise
    return module


_SCRIPT_PATH = Path(__file__).absolute()
if _SCRIPT_PATH != _SCRIPT_PATH.resolve(strict=True):
    raise _BootstrapError("attester must be loaded from a canonical physical path")
ROOT = _SCRIPT_PATH.parents[2]
_VERIFIER_SOURCE_PATH, _BOOTSTRAP_VERIFIER_RAW = _capture_authenticated_source(
    ROOT,
    VERIFIER_PATH,
    EXPECTED_VERIFIER_BYTES,
    EXPECTED_VERIFIER_SHA256,
    "runtime verifier bootstrap",
)
V = _exec_authenticated_source(
    "_dancers_authenticated_headless_runtime_verifier_v1",
    _VERIFIER_SOURCE_PATH,
    _BOOTSTRAP_VERIFIER_RAW,
)
B = V.B


class AttestationError(ValueError):
    """A sealed quartet cannot be represented by the v2 attestation."""


def _post_verified_build_hook(
    verified: Any, repository_root: Path, build_root: Path
) -> None:
    """No-op fault-injection seam after the verifier returns its snapshot."""
    del verified, repository_root, build_root


def _attest_build_bound(repository_root: Path, build_root: Path) -> dict[str, Any]:
    B._require_isolated_python()
    lexical_root = repository_root.absolute()
    repository_root = repository_root.resolve(strict=True)
    if lexical_root != repository_root:
        raise AttestationError("repository root must be supplied in canonical physical form")
    implementation: dict[str, dict[str, Any]] = {}
    for key, relative, expected_bytes, expected_sha in (
        ("builder", V.BUILDER_PATH, V.EXPECTED_BUILDER_BYTES, V.EXPECTED_BUILDER_SHA256),
        ("verifier", VERIFIER_PATH, EXPECTED_VERIFIER_BYTES, EXPECTED_VERIFIER_SHA256),
    ):
        try:
            B._assert_no_symlink_ancestry(repository_root / relative, repository_root)
            raw = B._read_regular(repository_root / relative, 2 * 1024 * 1024, key)
        except B.BuildError as exc:
            raise AttestationError(str(exc)) from exc
        if len(raw) != expected_bytes or B._sha256(raw) != expected_sha:
            raise AttestationError(f"{key} implementation identity differs")
        implementation[key] = B._identity(relative, raw)
    try:
        verified = V.verify_build_custody(repository_root, build_root)
    except (V.VerificationError, B.BuildError, OSError) as exc:
        raise AttestationError(str(exc)) from exc
    _post_verified_build_hook(verified, repository_root, build_root)
    verdict = verified.verdict
    build_root = Path(verdict["build_root"])
    try:
        B._assert_no_symlink_ancestry(repository_root / ATTESTER_PATH, repository_root)
        attester_raw = B._read_regular(
            repository_root / ATTESTER_PATH, 2 * 1024 * 1024, "v2 attester implementation"
        )
    except B.BuildError as exc:
        raise AttestationError(str(exc)) from exc
    manifest = verified.manifest
    manifest_raw = verified.manifest_raw
    seal_raw = verified.seal_raw
    implementation["attester"] = B._identity(ATTESTER_PATH, attester_raw)
    artifact_by_path = {
        item["path"]: item for item in manifest["artifacts"] if isinstance(item, dict)
    }

    def verified_artifact(path: str) -> dict[str, Any]:
        record = artifact_by_path.get(path)
        raw = verified.artifact_raw_by_path.get(path)
        if not isinstance(record, dict) or not isinstance(raw, bytes):
            raise AttestationError("verified artifact snapshot is incomplete")
        expected = {
            **B._identity(path, raw),
            "mode": record.get("mode"),
        }
        if record != expected:
            raise AttestationError("verified artifact snapshot identity differs")
        return dict(record)
    pass_two = {
        item["product_id"]: item
        for item in manifest["passes"]
        if isinstance(item, dict) and item.get("pass") == 2
    }
    products: dict[str, dict[str, Any]] = {}
    for product_id, method, role, observer in B.MATRIX:
        endpoint = B.ENDPOINTS[method]
        binary_path = f"products/{product_id}/dancers"
        descriptor_path = f"products/{product_id}/{B.DESCRIPTOR_TEST_TARGET}"
        binary = verified_artifact(binary_path)
        descriptor_binary = verified_artifact(descriptor_path)
        record = pass_two.get(product_id)
        if (
            not isinstance(binary, dict)
            or not isinstance(descriptor_binary, dict)
            or not isinstance(record, dict)
        ):
            raise AttestationError("sealed product inventory is incomplete")
        products[product_id] = {
            "algorithm_revision": endpoint["algorithm_revision"],
            "applied_head": endpoint["applied_head"],
            "binary": binary,
            "build_role": role,
            "code_signature": record["macho"]["code_signature"],
            "expected_cdhash": record["macho"]["code_signature"]["cdhash"],
            "expected_lc_uuid": record["macho"]["uuid"],
            "descriptor_test_binary": descriptor_binary,
            "method": method,
            "observer_compiled": observer,
            "source_tree": endpoint["tree"],
            "two_pass_binary_sha256": [
                item["binary_sha256"]
                for item in manifest["passes"]
                if item["product_id"] == product_id
            ],
            "two_pass_descriptor_test_sha256": [
                item["descriptor_test_binary_sha256"]
                for item in manifest["passes"]
                if item["product_id"] == product_id
            ],
        }
        if products[product_id]["two_pass_binary_sha256"] != [
            record["binary_sha256"], record["binary_sha256"]
        ]:
            raise AttestationError("two-pass product identity differs")
        if products[product_id]["two_pass_descriptor_test_sha256"] != [
            record["descriptor_test_binary_sha256"],
            record["descriptor_test_binary_sha256"],
        ]:
            raise AttestationError("two-pass descriptor test identity differs")
    shim_path = f"products/{B.SHIM_PRODUCT_ID}/{B.SHIM_BINARY_NAME}"
    shim_binary = verified_artifact(shim_path)
    shim_passes = manifest.get("shim_passes")
    if not isinstance(shim_binary, dict) or not isinstance(shim_passes, list):
        raise AttestationError("sealed shim inventory is incomplete")
    shim_hashes = [
        item.get("binary_sha256") for item in shim_passes if isinstance(item, dict)
    ]
    if (
        len(shim_passes) != 2
        or len(shim_hashes) != 2
        or shim_hashes[0] != shim_hashes[1]
        or shim_binary.get("sha256") != shim_hashes[1]
    ):
        raise AttestationError("two-pass shim identity differs")
    spawn_shim = {
        "binary": shim_binary,
        "code_signature": shim_passes[1]["macho"]["code_signature"],
        "expected_cdhash": shim_passes[1]["macho"]["code_signature"]["cdhash"],
        "expected_lc_uuid": shim_passes[1]["macho"]["uuid"],
        "product_id": B.SHIM_PRODUCT_ID,
        "runtime_descriptor_contract": {
            "descriptor_fds": B.SHIM_DESCRIPTOR_FDS,
            "launch": B.SHIM_LAUNCH_CONTRACT,
        },
        "source_identities": shim_passes[1]["source_identities"],
        "two_pass_binary_sha256": shim_hashes,
    }
    attestation = {
        "build": {
            "build_root": str(build_root),
            "host_bound": True,
            "manifest": B._identity("build-manifest-v1.json", manifest_raw),
            "seal": B._identity("BUILD_COMPLETE.seal.json", seal_raw),
            "static_verifier_manifest_sha256": verdict["manifest_sha256"],
        },
        "classification": {
            **B.SCIENCE_FLAGS,
            "attestation_published": False,
            "build_complete": True,
            "build_only_engineering_artifact": True,
            "host_bound": True,
            "identity_attestation_only": True,
            "portable": False,
        },
        "design": manifest["design"],
        "evidence_boundary": [
            "fresh byte identities for the sealed four-product, two-pass engineering build",
            "one exact canonical main-repository runtime artifact root is shared by all products",
            "no runtime, descriptor-test, or shim product was executed by the builder; runtime-root and descriptor activation smoke is pending the separately qualified suspended-image/CDHash launcher",
            "the distinct canonical-path Darwin spawn shim and simulator products are byte-bound and two-pass reproducible; /dev/fd execution is forbidden; parent FD247 reaches the initial shim under custody and is CLOEXEC before the same-PID simulator SETEXEC boundary",
            "each simulator and spawn-shim Mach-O CodeDirectory is parsed from sealed bytes and contributes its exact 20-byte CDHash",
            "each simulator and spawn-shim retains one dyld-required LC_UUID whose value is exact and stable across both clean passes",
            "the runtime and descriptor products have no OpenMP compile/link action, no OpenMP-family name in the parsed nlist, dyld bind/weak/lazy streams, chained-fixups imports/symbol pool, or export tries, no OpenMP dylib load, and no non-platform load or rpath; their source pragma loops are serial under the logical one-thread contract",
            "this attester performs static reads only and does not execute any binary",
            "no plan, result, calibration, validation, timing, capacity, or benchmark claim is authorized",
        ],
        "implementation": implementation,
        "operations": {
            "attestation_written": False,
            "binary_executed": False,
            "network_accessed": False,
            "plan_written": False,
            "result_written": False,
            "simulator_invoked": False,
            "static_reads_only": True,
        },
        "pixi_lock": manifest["inputs"]["pixi_lock"],
        "products": products,
        "repository": manifest["repository"],
        "runtime_artifact_root": manifest["runtime_artifact_root"],
        "schema_version": SCHEMA_VERSION,
        "source_identity": {
            "common_patch": {
                "commit": "26c8c6f9b262a2114ac28203419cb03caf8b0d88",
                "format_email_sha256": "2ab81c9a9621c89de5854bbd8496cb2124845733140a913855ca61677185a95e",
                "full_index_diff_sha256": "5641cb37493f2c62618c43301459d7ce5e33c95101d344ba3157a9ae478de3f8",
                "history": [
                    {
                        "format_email_sha256": "feba83f12d25f22ba04857b82dee514e75e8136cff8416f872e83cf0fb41359b",
                        "full_index_diff_sha256": "0eb4722ce67646d75c85fcd20b66bb7f62b019601db613af052e25774248edfc",
                        "optimized_commit": "a992bee99ce64ae6548698ddcc48811e50fa47e7",
                        "purpose": "common comparison correctness and observer layer",
                    },
                    {
                        "format_email_sha256": "629e50f9dada3e2f95808a29aada750c53bc7fb21c7f85f99bbc08aac7e56df1",
                        "full_index_diff_sha256": "12d1d18f6e6a923c74604ef5cbc54fb3e69cb08907aab80721ef8585959d1c2a",
                        "optimized_commit": "91f5f05735ba249c657bb8789597635f0755d74f",
                        "purpose": "planning-only result logging bounds correction",
                    },
                    {
                        "format_email_sha256": "2ab81c9a9621c89de5854bbd8496cb2124845733140a913855ca61677185a95e",
                        "full_index_diff_sha256": "5641cb37493f2c62618c43301459d7ce5e33c95101d344ba3157a9ae478de3f8",
                        "optimized_commit": "26c8c6f9b262a2114ac28203419cb03caf8b0d88",
                        "purpose": "compact trace-v3 semantic observer and storage correction",
                    },
                ],
            },
            "optimized": {
                "applied_head": B.ENDPOINTS["optimized"]["applied_head"],
                "source_pin": B.ENDPOINTS["optimized"]["algorithm_revision"],
                "tree": B.ENDPOINTS["optimized"]["tree"],
            },
            "reference": {
                "applied_head": B.ENDPOINTS["reference"]["applied_head"],
                "source_pin": B.ENDPOINTS["reference"]["algorithm_revision"],
                "tree": B.ENDPOINTS["reference"]["tree"],
            },
            "runtime_rebuild_layers": {
                key: manifest["inputs"][key]
                for key in (
                    "cmake_patch", "probe_patch", "descriptor_patch",
                    "single_thread_patch", "sentinel",
                )
            },
        },
        "spawn_shim": spawn_shim,
        "status": "pass-build-identity-only-activation-pending-nonauthorizing",
        "toolchain": manifest["toolchain"],
        "track_id": TRACK_ID,
    }
    return attestation


def attest_build(repository_root: Path, build_root: Path) -> dict[str, Any]:
    """Attest through verifier and builder modules compiled from supplied bytes."""
    global B, V
    B._require_isolated_python()
    lexical_root = repository_root.absolute()
    try:
        canonical_root = repository_root.resolve(strict=True)
    except OSError as exc:
        raise AttestationError(f"repository root is unavailable: {exc}") from exc
    if lexical_root != canonical_root:
        raise AttestationError("repository root must be supplied in canonical physical form")
    try:
        verifier_path, verifier_raw = _capture_authenticated_source(
            canonical_root,
            VERIFIER_PATH,
            EXPECTED_VERIFIER_BYTES,
            EXPECTED_VERIFIER_SHA256,
            "runtime verifier",
        )
        repository_verifier = _exec_authenticated_source(
            "_dancers_repository_bound_headless_runtime_verifier_v1",
            verifier_path,
            verifier_raw,
        )
    except _BootstrapError as exc:
        raise AttestationError(str(exc)) from exc
    bootstrap_verifier = V
    bootstrap_builder = B
    V = repository_verifier
    B = repository_verifier.B
    try:
        return _attest_build_bound(canonical_root, build_root)
    finally:
        B = bootstrap_builder
        V = bootstrap_verifier


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", default=str(ROOT))
    parser.add_argument("--build-root", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        B._require_isolated_python()
    except B.BuildError as exc:
        print(f"headless-binary-attestation-v2: reject: {exc}", file=sys.stderr)
        return 1
    args = _parser().parse_args(argv)
    try:
        value = attest_build(Path(args.repository_root), Path(args.build_root))
    except (AttestationError, OSError) as exc:
        print(f"headless-binary-attestation-v2: reject: {exc}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(B._canonical_json(value))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
