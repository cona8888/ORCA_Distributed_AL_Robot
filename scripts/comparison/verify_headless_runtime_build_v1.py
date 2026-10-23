#!/usr/bin/env python3
"""Statically verify a sealed host-bound headless runtime build.

No command, binary, simulator, plan freezer, or result analyzer is executed.
The verifier only reads byte-bounded regular files and checks the sealed
build-only inventory against the committed design.
"""

from __future__ import annotations

import sys
sys.dont_write_bytecode = True
if (
    sys.flags.isolated != 1
    or sys.flags.no_site != 1
    or sys.flags.dont_write_bytecode != 1
):
    raise SystemExit("headless-runtime-build-verifier-v1: reject: public checkpoint invocation requires python -I -S -B")

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Optional, Sequence

BUILDER_PATH = "scripts/comparison/build_headless_runtime_artifacts_v1.py"
EXPECTED_BUILDER_BYTES = 226_623
EXPECTED_BUILDER_SHA256 = "a4761875df1d35dd048ad0f04328515450fd68f1658f990f769cde8f1c21c6be"
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
    """Open by retained directory FDs, authenticate, and return captured bytes.

    No import machinery participates.  The bytes returned here are the exact
    bytes passed to ``compile`` below; a matching or malicious ``.pyc`` is
    therefore neither discovered nor executed.
    """
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
    raise _BootstrapError("verifier must be loaded from a canonical physical path")
ROOT = _SCRIPT_PATH.parents[2]
_BUILDER_SOURCE_PATH, _BOOTSTRAP_BUILDER_RAW = _capture_authenticated_source(
    ROOT,
    BUILDER_PATH,
    EXPECTED_BUILDER_BYTES,
    EXPECTED_BUILDER_SHA256,
    "runtime builder bootstrap",
)
B = _exec_authenticated_source(
    "_dancers_authenticated_headless_runtime_builder_v1",
    _BUILDER_SOURCE_PATH,
    _BOOTSTRAP_BUILDER_RAW,
)


MACHO_MAGIC = B.MACHO_MAGIC
MACHO_CPU_ARM64 = B.MACHO_CPU_ARM64
MACHO_EXECUTE = B.MACHO_EXECUTE
LEGACY_OBSERVER_ENV_GUARD_MARKERS = (
    b"DANCERS_REQUIRE_PROVENANCE\x00",
    b"DANCERS_RUN_METADATA\x00",
    b"DANCERS_SUMMARY_OUT\x00",
    b"DANCERS_TRACE_DETAIL\x00",
    b"DANCERS_TRACE_OUT\x00",
)
OBSERVER_ONLY_MARKERS = (
    b"dancers-comparison-trace-v3\x00",
)
EXPECTED_TREE_PROOFS = {
    "reference": (521, "05d4a1546364ff64b1bcfdacc314cc20a7661267190e30e20b1fb8ac1d533bf3"),
    "optimized": (523, "1a5c99d30bf367065c210f20adbb30b8bd373795eea5ff31bb65d40ec548fbd1"),
}
EXPECTED_OVERLAY_PROOFS = {
    "reference": (526, "283c6a46bbdf3651d131f9ba65429d2649020d04f30ecc97a1b753190d8e40ba"),
    "optimized": (528, "708385d18764e1ed7716ae24b9fd9144da89a9deba7441dee1b788cd9c6c38a8"),
}


class VerificationError(ValueError):
    """The sealed engineering build does not match the v1 design."""


@dataclass(frozen=True)
class VerifiedBuild:
    verdict: dict[str, Any]
    artifact_raw_by_path: Mapping[str, bytes]
    manifest: dict[str, Any]
    manifest_raw: bytes
    seal_raw: bytes


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise VerificationError(f"{label} is not an object")
    return value


def _sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise VerificationError(f"{label} is not an array")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise VerificationError(f"{label} keys differ")


def _canonical_object(raw: bytes, label: str) -> dict[str, Any]:
    try:
        value = B._parse_json(raw, label)
    except B.BuildError as exc:
        raise VerificationError(str(exc)) from exc
    value = _mapping(value, label)
    if raw != B._canonical_json(value):
        raise VerificationError(f"{label} is not canonical JSON")
    return value


def _identity_tuple(value: Any, label: str, *, with_mode: bool = False) -> tuple[Any, ...]:
    record = _mapping(value, label)
    expected = {"bytes", "path", "sha256"} | ({"mode"} if with_mode else set())
    _exact_keys(record, expected, label)
    if type(record["bytes"]) is not int or not 0 <= record["bytes"] <= B.MAX_BINARY:
        raise VerificationError(f"{label} bytes are invalid")
    try:
        B._safe_relative(record["path"], f"{label}.path")
    except B.BuildError as exc:
        raise VerificationError(str(exc)) from exc
    if not isinstance(record["sha256"], str) or not B.HEX64.fullmatch(record["sha256"]):
        raise VerificationError(f"{label} SHA-256 is invalid")
    if with_mode:
        if type(record["mode"]) is not int or record["mode"] not in (0o444, 0o555):
            raise VerificationError(f"{label} mode is invalid")
        return record["path"], record["bytes"], record["sha256"], record["mode"]
    return record["path"], record["bytes"], record["sha256"]


def _read_published(build_root: Path, identity: Mapping[str, Any], label: str) -> bytes:
    relative, expected_bytes, expected_sha, expected_mode = _identity_tuple(identity, label, with_mode=True)
    path = build_root / relative
    try:
        B._assert_no_symlink_ancestry(path, build_root)
        raw = B._read_regular(path, B.MAX_BINARY, label)
    except B.BuildError as exc:
        raise VerificationError(str(exc)) from exc
    if len(raw) != expected_bytes or B._sha256(raw) != expected_sha:
        raise VerificationError(f"{label} identity differs")
    info = path.lstat()
    if info.st_nlink != 1:
        raise VerificationError(f"{label} link count differs")
    if stat.S_IMODE(info.st_mode) != expected_mode:
        raise VerificationError(f"{label} publication mode differs")
    return raw


def _post_artifact_semantic_hook(
    build_root: Path,
    artifact_by_path: Mapping[str, tuple[dict[str, Any], bytes]],
) -> None:
    """No-op fault-injection seam immediately before final artifact custody."""
    del build_root, artifact_by_path


def _expected_artifact_paths() -> list[str]:
    paths: list[str] = []
    for pass_number in (1, 2):
        for product_id, _, _, _ in B.MATRIX:
            for suffix in (
                "build.log", "configure.log", "descriptor-nm-undefined.log",
                "nm-undefined.log", "otool-load.log", "otool-rpath.log",
            ):
                paths.append(f"logs/pass-{pass_number}/{product_id}.{suffix}")
    for product_id, _, _, _ in B.MATRIX:
        paths.append(f"products/{product_id}/dancers")
        paths.append(f"products/{product_id}/{B.DESCRIPTOR_TEST_TARGET}")
    for pass_number in (1, 2):
        paths.extend((
            f"logs/pass-{pass_number}/{B.SHIM_PRODUCT_ID}.build.log",
            f"logs/pass-{pass_number}/{B.SHIM_PRODUCT_ID}.nm-undefined.log",
        ))
    paths.append(f"products/{B.SHIM_PRODUCT_ID}/{B.SHIM_BINARY_NAME}")
    return sorted(paths, key=lambda item: item.encode("utf-8"))


def _inventory_paths(build_root: Path) -> list[str]:
    paths: list[str] = []
    for directory, dirnames, filenames in os.walk(build_root, topdown=True, followlinks=False):
        if stat.S_IMODE(Path(directory).lstat().st_mode) != 0o555:
            raise VerificationError("published tree contains a writable directory")
        for name in dirnames:
            child = Path(directory) / name
            if child.is_symlink():
                raise VerificationError("published tree contains a symlink directory")
            if stat.S_IMODE(child.lstat().st_mode) != 0o555:
                raise VerificationError("published tree contains a writable directory")
        for name in filenames:
            path = Path(directory) / name
            info = path.lstat()
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
            ):
                raise VerificationError(
                    "published tree contains a non-regular, symlinked, or multiply linked file"
                )
            paths.append(path.relative_to(build_root).as_posix())
    return sorted(paths, key=lambda item: item.encode("utf-8"))


def _verify_source_proof(value: Any, method: str) -> None:
    proof = _mapping(value, "source proof")
    _exact_keys(
        proof,
        {
            "algorithm_revision", "applied_head", "archive_entry_count",
            "archive_tree_digest", "descriptor_post_identities", "endpoint_tree",
            "method", "overlay_paths", "post_overlay_cmake_sha256",
            "post_overlay_file_count", "post_overlay_main_sha256",
            "post_overlay_tree_sha256", "single_thread_post_identity",
        },
        "source proof",
    )
    endpoint = B.ENDPOINTS[method]
    expected_count, expected_digest = EXPECTED_TREE_PROOFS[method]
    overlay_count, overlay_digest = EXPECTED_OVERLAY_PROOFS[method]
    expected = {
        "algorithm_revision": endpoint["algorithm_revision"],
        "applied_head": endpoint["applied_head"],
        "archive_entry_count": expected_count,
        "archive_tree_digest": expected_digest,
        "descriptor_post_identities": B._descriptor_post_identity_list(method),
        "endpoint_tree": endpoint["tree"],
        "method": method,
        "overlay_paths": sorted({
            "CMakeLists.txt", "src/main.cpp",
            *[item[1] for item in B.ARCHIVE_OVERLAYS],
            *B.EXPECTED_DESCRIPTOR_PATHS,
        }),
        "post_overlay_cmake_sha256": B.EXPECTED_SINGLE_THREAD_CMAKE_POST[1],
        "post_overlay_file_count": overlay_count,
        "post_overlay_main_sha256": B.EXPECTED_DESCRIPTOR_POST[method]["src/main.cpp"][1],
        "post_overlay_tree_sha256": overlay_digest,
        "single_thread_post_identity": {
            "bytes": B.EXPECTED_SINGLE_THREAD_CMAKE_POST[0],
            "path": "CMakeLists.txt",
            "sha256": B.EXPECTED_SINGLE_THREAD_CMAKE_POST[1],
        },
    }
    if proof != expected:
        raise VerificationError(f"{method} source proof differs")


def _verify_toolchain(value: Any, repository_root: Path) -> None:
    toolchain = _mapping(value, "toolchain")
    _exact_keys(
        toolchain,
        {
            "compiler_driver_entries", "deployment_target", "driver_linker_entry",
            "openmp_policy", "openmp_version_from_lock", "platform",
            "probe_outputs", "provenance_boundary", "tools",
        },
        "toolchain",
    )
    if (
        toolchain["deployment_target"] != B.DEPLOYMENT_TARGET
        or toolchain["openmp_version_from_lock"] != "19.1.7"
        or toolchain["platform"] != "osx-arm64"
    ):
        raise VerificationError("toolchain platform profile differs")
    if toolchain["openmp_policy"] != {
        "compile_activation_forbidden": True,
        "logical_threads": 1,
        "runtime_library_loaded": False,
        "source_layer": B.INPUT_IDENTITIES["single_thread_patch"][0],
        "symbol_representations_scanned": [
            "LC_SYMTAB nlist defined and undefined names",
            "LC_DYLD_INFO bind weak-bind and lazy-bind opcode names",
            "LC_DYLD_CHAINED_FIXUPS import table and complete symbol pool",
            "LC_DYLD_INFO export trie and LC_DYLD_EXPORTS_TRIE names",
        ],
        "symbol_prefixes_forbidden": list(
            B.FORBIDDEN_OPENMP_SYMBOL_PREFIXES
        ),
    }:
        raise VerificationError("single-thread no-OpenMP toolchain policy differs")
    if toolchain["provenance_boundary"] != {
        "entrypoint_bytes_do_not_authenticate_installed_transitive_closure": True,
        "external_host_toolchain_assumptions": [
            "all unrecorded transitive dynamic libraries, resource directories, configuration files, and helper inputs used by the recorded Pixi, CMake, Ninja, Git, clang, ld, nm, ar, ranlib, otool, xcrun, xcodebuild, and uname entrypoints",
            "Xcode SDK 26.5 contents and the relevant macOS system platform closure",
            "recorded lexical tool entrypoints do not daemonize or escape their fresh owned process group",
        ],
        "git_no_lazy_fetch": "1",
        "non_daemonizing_owned_process_group_required": True,
        "quiescent_exclusive_build_required": True,
        "sealed_binary_qualification_independent": True,
        "tool_entrypoint_bytes_lock_flags_and_versions_recorded": True,
        "tool_transitive_closure_exact_provenance": False,
        "use_bound_toolchain_provenance_claim": False,
    }:
        raise VerificationError("host toolchain provenance boundary differs")
    outputs = _mapping(toolchain["probe_outputs"], "toolchain probe outputs")
    _exact_keys(outputs, {"architecture", "cmake", "compiler_c", "compiler_c_linker", "compiler_cxx", "compiler_cxx_linker", "git", "ninja", "sdk_build", "sdk_path", "sdk_version", "xcode"}, "toolchain probe outputs")
    if not all(isinstance(item, str) for item in outputs.values()):
        raise VerificationError("toolchain probe output is not textual")
    if (
        outputs["architecture"] != "arm64"
        or "cmake version 4.4.0" not in outputs["cmake"]
        or "clang version 19.1.7" not in outputs["compiler_c"]
        or "clang version 19.1.7" not in outputs["compiler_cxx"]
        or outputs["git"] != "git version 2.55.0"
        or outputs["ninja"] != "1.13.2"
        or outputs["sdk_build"] != "25F70"
        or outputs["sdk_path"] != "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX26.5.sdk"
        or outputs["sdk_version"] != "26.5"
        or outputs["xcode"] != "Xcode 26.6\nBuild version 17F113"
    ):
        raise VerificationError("toolchain probe output differs")
    tools = _mapping(toolchain["tools"], "tool identities")
    _exact_keys(
        tools,
        {
            "ar", "clang", "clang++", "cmake", "git", "ld", "ninja",
            "nm", "otool", "ranlib", "uname", "xcodebuild", "xcrun",
        },
        "tool identities",
    )
    for name, value in tools.items():
        record = _mapping(value, f"tool {name}")
        _exact_keys(record, {"bytes", "path", "sha256"}, f"tool {name}")
        if type(record["bytes"]) is not int or not 1 <= record["bytes"] <= 256 * 1024 * 1024:
            raise VerificationError(f"tool {name} byte count is invalid")
        if not isinstance(record["path"], str) or not Path(record["path"]).is_absolute():
            raise VerificationError(f"tool {name} path is not absolute")
        if not isinstance(record["sha256"], str) or not B.HEX64.fullmatch(record["sha256"]):
            raise VerificationError(f"tool {name} SHA-256 is invalid")
    compiler_entries = _mapping(
        toolchain["compiler_driver_entries"], "compiler driver entries"
    )
    _exact_keys(compiler_entries, {"clang", "clang++"}, "compiler driver entries")
    for name, value in compiler_entries.items():
        entry = _mapping(value, f"{name} compiler driver entry")
        _exact_keys(
            entry, {"path", "resolved_path", "target"},
            f"{name} compiler driver entry",
        )
        if entry != {
            "path": str(repository_root / ".pixi/envs/default/bin" / name),
            "resolved_path": tools[name]["path"],
            "target": "clang-19",
        }:
            raise VerificationError(f"{name} compiler driver entry identity differs")
    driver_linker = _mapping(toolchain["driver_linker_entry"], "driver linker entry")
    _exact_keys(
        driver_linker, {"path", "resolved_path", "target"}, "driver linker entry"
    )
    if driver_linker != {
        "path": str(repository_root / ".pixi/envs/default/bin/ld"),
        "resolved_path": tools["ld"]["path"],
        "target": "arm64-apple-darwin20.0.0-ld",
    }:
        raise VerificationError("compiler driver linker entry identity differs")
    if (
        outputs["compiler_c_linker"] != driver_linker["path"]
        or outputs["compiler_cxx_linker"] != driver_linker["path"]
    ):
        raise VerificationError("compiler driver linker resolution differs")


def _verify_shim_passes(
    value: Any,
    *,
    artifact_by_path: Mapping[str, tuple[dict[str, Any], bytes]],
    build_root: Path,
    expected_inputs: Mapping[str, Mapping[str, Any]],
    toolchain: Mapping[str, Any],
) -> None:
    passes = _sequence(value, "shim passes")
    if len(passes) != 2:
        raise VerificationError("shim pass count differs")
    hashes: list[str] = []
    cdhashes: list[str] = []
    uuids: list[str] = []
    macho_receipts: list[dict[str, Any]] = []
    pass_two: Optional[dict[str, Any]] = None
    tools = _mapping(toolchain["tools"], "shim tool identities")
    compiler_entries = _mapping(
        toolchain["compiler_driver_entries"], "shim compiler driver entries"
    )
    outputs = _mapping(toolchain["probe_outputs"], "shim toolchain probes")
    sdk = Path(outputs["sdk_path"])
    for index, value_record in enumerate(passes, 1):
        record = _mapping(value_record, f"shim pass[{index - 1}]")
        _exact_keys(
            record,
            {
                "binary_bytes", "binary_sha256", "compile_argv", "environment",
                "macho", "nm_undefined_symbols_sha256", "pass", "product_id",
                "quiescent_drift_detection", "runtime_descriptor_contract",
                "sdk", "source_identities", "tool_identities",
            },
            f"shim pass[{index - 1}]",
        )
        if record["pass"] != index or record["product_id"] != B.SHIM_PRODUCT_ID:
            raise VerificationError("shim pass order or product ID differs")
        if type(record["binary_bytes"]) is not int or not 32 <= record["binary_bytes"] <= B.MAX_BINARY:
            raise VerificationError("shim pass binary byte count is invalid")
        if not isinstance(record["binary_sha256"], str) or not B.HEX64.fullmatch(record["binary_sha256"]):
            raise VerificationError("shim pass binary SHA-256 is invalid")
        hashes.append(record["binary_sha256"])
        macho_receipts.append(_mapping(record["macho"], "shim Mach-O receipt"))
        expected_nm_digest = B._sha256(
            b"\0".join(
                item.encode("utf-8")
                for item in macho_receipts[-1].get("undefined_symbols", [])
            )
        )
        if record["nm_undefined_symbols_sha256"] != expected_nm_digest:
            raise VerificationError("shim nm symbol receipt differs")
        code_signature = _mapping(
            macho_receipts[-1].get("code_signature"), "shim CodeDirectory receipt"
        )
        cdhash = code_signature.get("cdhash")
        if not isinstance(cdhash, str) or not B.HEX40.fullmatch(cdhash):
            raise VerificationError("shim CDHash is not an exact 20-byte digest")
        cdhashes.append(cdhash)
        uuid = macho_receipts[-1].get("uuid")
        if not isinstance(uuid, str) or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            uuid,
        ) is None:
            raise VerificationError("shim LC_UUID receipt differs")
        uuids.append(uuid)
        argv = _sequence(record["compile_argv"], "shim compile argv")
        if not all(isinstance(item, str) and item and "\x00" not in item for item in argv):
            raise VerificationError("shim compile argv is malformed")
        if len(argv) < 8:
            raise VerificationError("shim compile argv is truncated")
        try:
            source_index = argv.index(str(Path(B.SHIM_SOURCE_PATH).name))
        except ValueError:
            source_index = -1
        source_candidates = [
            Path(item) for item in argv
            if Path(item).name == Path(B.SHIM_SOURCE_PATH).name
        ]
        if len(source_candidates) != 1:
            raise VerificationError("shim compile source path differs")
        source = source_candidates[0]
        output = Path(argv[-1])
        if source_index != -1 or not source.is_absolute() or not output.is_absolute():
            raise VerificationError("shim compile paths are not exact absolute paths")
        staging_root = source.parents[3] if len(source.parents) > 3 else Path("/")
        if (
            staging_root.parent != build_root.parent
            or re.fullmatch(r"\.staging-[1-9][0-9]*-[0-9a-f]{16}", staging_root.name) is None
            or source != staging_root / "work" / f"shim-pass-{index}" / "source" / Path(B.SHIM_SOURCE_PATH).name
            or output != staging_root / "work" / f"shim-pass-{index}" / "output" / B.SHIM_BINARY_NAME
        ):
            raise VerificationError("shim compile private-work path binding differs")
        expected_argv = B._shim_compile_argv(
            Path(compiler_entries["clang"]["path"]), Path(tools["ld"]["path"]),
            sdk, source, output,
        )
        if argv != expected_argv:
            raise VerificationError("shim compile argv differs")
        expected_env = B._shim_build_env(
            build_root.parents[3],
            staging_root / "work" / f"shim-pass-{index}" / "home",
            Path(compiler_entries["clang"]["path"]),
            Path(tools["ld"]["path"]),
        )
        if record["environment"] != expected_env:
            raise VerificationError("shim compile environment differs")
        if record["source_identities"] != [
            expected_inputs["shim_source"], expected_inputs["shim_header"]
        ]:
            raise VerificationError("shim source identities differ")
        if record["tool_identities"] != {
            "clang": tools["clang"],
            "clang_driver_entry": compiler_entries["clang"],
            "driver_linker_entry": toolchain["driver_linker_entry"],
            "ld": tools["ld"],
            "nm": tools["nm"],
        }:
            raise VerificationError("shim compiler/linker identities differ")
        if record["sdk"] != {
            "build": outputs["sdk_build"],
            "deployment_target": toolchain["deployment_target"],
            "path": outputs["sdk_path"],
            "version": outputs["sdk_version"],
        }:
            raise VerificationError("shim SDK binding differs")
        if record["runtime_descriptor_contract"] != {
            "descriptor_fds": B.SHIM_DESCRIPTOR_FDS,
            "launch": B.SHIM_LAUNCH_CONTRACT,
        }:
            raise VerificationError("shim runtime descriptor contract differs")
        if record["quiescent_drift_detection"] != {
            "active_aba_provenance_claim": False,
            "binary_path_pre_post_drift_checked": True,
            "quiescent_exclusive_build_required": True,
            "source_path_pre_post_drift_checked": True,
            "tool_path_pre_post_drift_checked": True,
            "use_bound_provenance_claim": False,
        }:
            raise VerificationError("shim quiescent drift-detection record differs")
        if index == 2:
            pass_two = record
    if len(hashes) != 2 or hashes[0] != hashes[1] or pass_two is None:
        raise VerificationError("two-pass shim binary identity differs")
    if len(macho_receipts) != 2 or macho_receipts[0] != macho_receipts[1]:
        raise VerificationError("two-pass shim Mach-O receipts differ")
    if len(cdhashes) != 2 or cdhashes[0] != cdhashes[1]:
        raise VerificationError("two-pass shim CDHash differs")
    if len(uuids) != 2 or uuids[0] != uuids[1]:
        raise VerificationError("two-pass shim LC_UUID differs")
    path = f"products/{B.SHIM_PRODUCT_ID}/{B.SHIM_BINARY_NAME}"
    binary_record, binary_raw = artifact_by_path[path]
    if (
        binary_record["bytes"] != pass_two["binary_bytes"]
        or binary_record["sha256"] != pass_two["binary_sha256"]
    ):
        raise VerificationError("published shim differs from pass two")
    if (build_root / path).lstat().st_nlink != 1:
        raise VerificationError("published shim link count differs")
    try:
        actual_macho = B._macho_system_executable_receipt(
            binary_raw, "spawn shim binary", B.SHIM_EXEC_SEGMENT_LIMIT_BASIS,
            B.SHIM_BINARY_NAME, 0x00200085,
            require_shim_exec_segment_profile=True,
        )
    except B.BuildError as exc:
        raise VerificationError(str(exc)) from exc
    if pass_two["macho"] != actual_macho:
        raise VerificationError("shim Mach-O receipt differs from published bytes")


def _verify_build_custody_bound(
    repository_root: Path, build_root: Path, builder_raw: bytes
) -> VerifiedBuild:
    B._require_isolated_python()
    expected_parent = (repository_root / B.BUILD_PREFIX).resolve(strict=True)
    lexical = build_root.absolute()
    try:
        B._assert_no_symlink_ancestry(lexical, repository_root)
    except B.BuildError as exc:
        raise VerificationError(str(exc)) from exc
    build_root = lexical.resolve(strict=True)
    if build_root.parent != expected_parent or build_root.name != B.DESIGN_SHA256:
        raise VerificationError("build root is not the exact design-digest destination")
    if not build_root.is_dir() or stat.S_IMODE(build_root.lstat().st_mode) != 0o555:
        raise VerificationError("build root is not a read-only directory")
    try:
        _, design_raw = B.load_design(repository_root)
        expected_inputs = B.authenticate_repository_inputs(repository_root)
        manifest_raw = B._read_regular(build_root / "build-manifest-v1.json", 4 * 1024 * 1024, "build manifest")
        seal_raw = B._read_regular(build_root / "BUILD_COMPLETE.seal.json", 1 * 1024 * 1024, "build seal")
    except B.BuildError as exc:
        raise VerificationError(str(exc)) from exc
    manifest_info = (build_root / "build-manifest-v1.json").lstat()
    seal_info = (build_root / "BUILD_COMPLETE.seal.json").lstat()
    if manifest_info.st_nlink != 1 or seal_info.st_nlink != 1:
        raise VerificationError("build manifest or seal link count differs")
    if stat.S_IMODE(manifest_info.st_mode) != 0o444:
        raise VerificationError("build manifest is not read-only")
    if stat.S_IMODE(seal_info.st_mode) != 0o444:
        raise VerificationError("build seal is not read-only")
    manifest = _canonical_object(manifest_raw, "build manifest")
    seal = _canonical_object(seal_raw, "build seal")
    _exact_keys(
        manifest,
        {"artifacts", "build", "classification", "design", "inputs", "operations", "passes", "repository", "runtime_artifact_root", "schema_version", "shim_passes", "toolchain"},
        "build manifest",
    )
    if manifest["schema_version"] != B.MANIFEST_SCHEMA:
        raise VerificationError("build manifest schema differs")
    if manifest["design"] != B._identity(B.DESIGN_PATH, design_raw):
        raise VerificationError("build manifest design identity differs")
    if manifest["inputs"] != expected_inputs:
        raise VerificationError("build manifest input bindings differ")
    expected_classification = {
        **B.SCIENCE_FLAGS,
        "build_complete": True,
        "build_only_engineering_artifact": True,
        "host_bound": True,
        "portable": False,
    }
    if manifest["classification"] != expected_classification:
        raise VerificationError("build classification differs")
    if manifest["operations"] != {
        **B.OPERATIONS,
        "build_invoked": True,
        "configure_invoked": True,
        "ctest_invoked": False,
        "descriptor_test_product_invoked": False,
        "runtime_product_invoked": False,
        "runtime_root_activation_smoke": "pending-qualified-suspended-image-cdhash-launcher",
        "shim_compile_invoked": True,
        "shim_product_invoked": False,
    }:
        raise VerificationError("build operation record differs")
    if manifest["build"] != {
        "configure_count": 8,
        "descriptor_test_target": B.DESCRIPTOR_TEST_TARGET,
        "direct_compile_count": 2,
        "matrix_order": [item[0] for item in B.MATRIX],
        "pass_count": 2,
        "published_product_count": 5,
        "runtime_product_count": 4,
        "shim_product_id": B.SHIM_PRODUCT_ID,
        "two_pass_binary_identity": True,
    }:
        raise VerificationError("build matrix record differs")
    _verify_toolchain(manifest["toolchain"], repository_root)
    repository = _mapping(manifest["repository"], "repository record")
    _exact_keys(repository, {"design_base_commit", "design_base_tree", "head", "immutable_design_receipts", "implementation_ancestor", "implementation_ancestor_parent", "implementation_ancestor_tree", "tree"}, "repository record")
    if repository["design_base_commit"] != B.BASE_HEAD or repository["design_base_tree"] != B.BASE_TREE:
        raise VerificationError("design base identity differs")
    if (
        repository["implementation_ancestor"] != B.IMPLEMENTATION_ANCESTOR
        or repository["implementation_ancestor_parent"] != B.IMPLEMENTATION_ANCESTOR_PARENT
        or repository["implementation_ancestor_tree"] != B.IMPLEMENTATION_ANCESTOR_TREE
    ):
        raise VerificationError("implementation checkpoint identity differs")
    if not isinstance(repository["head"], str) or not B.HEX40.fullmatch(repository["head"]):
        raise VerificationError("repository HEAD is malformed")
    if not isinstance(repository["tree"], str) or not B.HEX40.fullmatch(repository["tree"]):
        raise VerificationError("repository tree is malformed")
    if repository["immutable_design_receipts"] != {
        "cmake_patch_commit": "80ca2738505d59cabe7f5b77ec7735046663dc59",
        "cmake_patch_parent": "f77437d47fbe3251317f7405cc2a2fd32f5575f4",
        "cross_method_path_count": 12,
        "cross_method_path_list_sha256": "e9439411a8b16f8cb459f4345091d44d945543eedaf0470ca391386aa44637c8",
        "descriptor_patch_path_count": 12,
        "descriptor_patch_path_list_sha256": "251154ccd86b4489614af8607da030b42735c12fb2216aede83b2755b6b31099",
        "design_base_tree_verified": True,
        "endpoint_cmake_and_main_bytes_identical": True,
    }:
        raise VerificationError("immutable Git design receipts differ")

    runtime = _mapping(manifest["runtime_artifact_root"], "runtime root")
    if runtime != {
        "canonical_absolute_path": str(repository_root),
        "identical_for_all_products": True,
        "sentinel": B._identity(B.SENTINEL_PATH, B._read_bound_input(repository_root, "sentinel")),
        "source_copy_roots_forbidden": True,
    }:
        raise VerificationError("runtime artifact root binding differs")

    artifacts = _sequence(manifest["artifacts"], "artifacts")
    if [item.get("path") if isinstance(item, dict) else None for item in artifacts] != _expected_artifact_paths():
        raise VerificationError("published artifact path inventory differs")
    artifact_by_path: dict[str, tuple[dict[str, Any], bytes]] = {}
    for index, value in enumerate(artifacts):
        record = _mapping(value, f"artifact[{index}]")
        path = record.get("path")
        if path in artifact_by_path:
            raise VerificationError("duplicate published artifact path")
        artifact_by_path[path] = (record, _read_published(build_root, record, f"artifact {path}"))

    _verify_shim_passes(
        manifest["shim_passes"],
        artifact_by_path=artifact_by_path,
        build_root=build_root,
        expected_inputs=expected_inputs,
        toolchain=manifest["toolchain"],
    )

    passes = _sequence(manifest["passes"], "passes")
    if len(passes) != 8:
        raise VerificationError("pass record count differs")
    pass_hashes: dict[str, list[str]] = {item[0]: [] for item in B.MATRIX}
    pass_cdhashes: dict[str, list[str]] = {item[0]: [] for item in B.MATRIX}
    pass_uuids: dict[str, list[str]] = {item[0]: [] for item in B.MATRIX}
    descriptor_hashes: dict[str, list[str]] = {item[0]: [] for item in B.MATRIX}
    descriptor_macho_receipts: dict[str, list[dict[str, Any]]] = {
        item[0]: [] for item in B.MATRIX
    }
    macho_closure: Optional[dict[str, Any]] = None
    expected_order = [(pass_number, *item) for pass_number in (1, 2) for item in B.MATRIX]
    for index, (value, expected) in enumerate(zip(passes, expected_order)):
        pass_number, product_id, method, role, observer = expected
        record = _mapping(value, f"pass[{index}]")
        _exact_keys(
            record,
            {
                "binary_bytes", "binary_sha256", "configure_receipt",
                "dependency_sources", "descriptor_test_binary_bytes",
                "descriptor_test_binary_sha256", "descriptor_test_macho",
                "descriptor_test_nm_undefined_symbols_sha256", "macho",
                "method", "nm_undefined_symbols_sha256", "observer_compiled",
                "pass", "product_id", "quiescent_drift_detection", "role",
                "source_proof",
            },
            f"pass[{index}]",
        )
        if record["pass"] != pass_number or record["product_id"] != product_id or record["method"] != method or record["role"] != role or record["observer_compiled"] is not observer:
            raise VerificationError("pass matrix order or semantics differ")
        if type(record["binary_bytes"]) is not int or not 32 <= record["binary_bytes"] <= B.MAX_BINARY:
            raise VerificationError("pass binary byte count is invalid")
        if not isinstance(record["binary_sha256"], str) or not B.HEX64.fullmatch(record["binary_sha256"]):
            raise VerificationError("pass binary SHA-256 is invalid")
        if type(record["descriptor_test_binary_bytes"]) is not int or not 32 <= record["descriptor_test_binary_bytes"] <= B.MAX_BINARY:
            raise VerificationError("descriptor test binary byte count is invalid")
        if not isinstance(record["descriptor_test_binary_sha256"], str) or not B.HEX64.fullmatch(record["descriptor_test_binary_sha256"]):
            raise VerificationError("descriptor test binary SHA-256 is invalid")
        configure_receipt = _mapping(record["configure_receipt"], "configure receipt")
        _exact_keys(
            configure_receipt,
            {
                "cmake_cache_bytes", "cmake_cache_sha256", "compile_commands_bytes",
                "compile_commands_sha256", "compiler_driver_flag_receipt",
                "configured_auxiliary_compile_action_count",
                "configured_project_compile_action_count",
                "configured_test_compile_action_count",
                "descriptor_test_compile_action_count", "gbp_compile_action_count",
                "ninja_build_graph_bytes", "ninja_build_graph_sha256",
                "openmp_compile_or_link_input_present",
                "product_compile_action_count", "project_compile_action_count",
            },
            "configure receipt",
        )
        expected_product_actions = len(
            B.GBP_PROJECT_SOURCES | B.DANCERS_TIMING_SOURCES
            | (B.OBSERVER_PROJECT_SOURCES if observer else set())
        )
        expected_test_actions = sum(
            len(sources) for sources in B.CONFIGURED_TEST_SOURCES.values()
        )
        expected_auxiliary_actions = sum(
            len(sources) for sources in B.CONFIGURED_AUXILIARY_SOURCES.values()
        )
        expected_total_actions = (
            expected_product_actions + expected_test_actions
            + expected_auxiliary_actions
        )
        driver_receipt = _mapping(
            configure_receipt["compiler_driver_flag_receipt"],
            "compiler driver flag receipt",
        )
        _exact_keys(
            driver_receipt,
            {
                "authenticated_compile_action_count", "delivery",
                "exact_cardinality_per_action", "flag_count", "flag_list_sha256",
                "unused_argument_suppression",
            },
            "compiler driver flag receipt",
        )
        if (
            type(driver_receipt["authenticated_compile_action_count"]) is not int
            or driver_receipt["authenticated_compile_action_count"]
            < expected_total_actions
            or driver_receipt["delivery"]
            != "explicit-cmake-language-flags-not-ambient-environment"
            or type(driver_receipt["exact_cardinality_per_action"]) is not int
            or driver_receipt["exact_cardinality_per_action"] != 1
            or type(driver_receipt["flag_count"]) is not int
            or driver_receipt["flag_count"] != 5
            or not isinstance(driver_receipt["flag_list_sha256"], str)
            or not B.HEX64.fullmatch(driver_receipt["flag_list_sha256"])
            or driver_receipt["unused_argument_suppression"]
            != B.DRIVER_UNUSED_ARGUMENT_SUPPRESSION
        ):
            raise VerificationError("compiler driver flag receipt differs")
        if configure_receipt["product_compile_action_count"] != expected_product_actions:
            raise VerificationError("configure compile action count differs")
        if configure_receipt["descriptor_test_compile_action_count"] != len(B.DESCRIPTOR_TEST_SOURCES):
            raise VerificationError("descriptor test compile action count differs")
        if (
            configure_receipt["configured_auxiliary_compile_action_count"]
            != expected_auxiliary_actions
            or configure_receipt["configured_project_compile_action_count"]
            != expected_total_actions
            or configure_receipt["configured_test_compile_action_count"]
            != expected_test_actions
            or configure_receipt["gbp_compile_action_count"]
            != len(B.GBP_PROJECT_SOURCES)
            or configure_receipt["project_compile_action_count"]
            != expected_total_actions
            or configure_receipt["openmp_compile_or_link_input_present"] is not False
        ):
            raise VerificationError("total compile action count differs")
        for prefix in ("cmake_cache", "compile_commands", "ninja_build_graph"):
            maximum = 4 * 1024 * 1024 if prefix == "cmake_cache" else 32 * 1024 * 1024
            if type(configure_receipt[f"{prefix}_bytes"]) is not int or not 1 <= configure_receipt[f"{prefix}_bytes"] <= maximum:
                raise VerificationError("configure receipt byte count is invalid")
            if not isinstance(configure_receipt[f"{prefix}_sha256"], str) or not B.HEX64.fullmatch(configure_receipt[f"{prefix}_sha256"]):
                raise VerificationError("configure receipt SHA-256 is invalid")
        dependency_sources = _mapping(record["dependency_sources"], "dependency sources")
        if set(dependency_sources) != set(B.DEPENDENCY_SOURCE_PROOFS):
            raise VerificationError("dependency source set differs")
        for dependency, expected_dependency in B.DEPENDENCY_SOURCE_PROOFS.items():
            expected_receipt = {
                key: expected_dependency[key]
                for key in ("file_count", "regular_bytes", "tree_sha256")
            }
            if dependency_sources[dependency] != expected_receipt:
                raise VerificationError("dependency extracted source proof differs")
        macho = _mapping(record["macho"], "Mach-O receipt")
        code_signature = _mapping(
            macho.get("code_signature"), "runtime CodeDirectory receipt"
        )
        cdhash = code_signature.get("cdhash")
        if not isinstance(cdhash, str) or not B.HEX40.fullmatch(cdhash):
            raise VerificationError("runtime CDHash is not an exact 20-byte digest")
        expected_nm_digest = B._sha256(
            b"\0".join(
                item.encode("utf-8") for item in macho.get("undefined_symbols", [])
            )
        )
        descriptor_macho = _mapping(
            record["descriptor_test_macho"], "descriptor test Mach-O receipt"
        )
        descriptor_macho_receipts[product_id].append(descriptor_macho)
        expected_descriptor_nm_digest = B._sha256(
            b"\0".join(
                item.encode("utf-8")
                for item in descriptor_macho.get("undefined_symbols", [])
            )
        )
        if (
            record["nm_undefined_symbols_sha256"] != expected_nm_digest
            or record["descriptor_test_nm_undefined_symbols_sha256"]
            != expected_descriptor_nm_digest
        ):
            raise VerificationError("nm symbol receipt differs")
        closure = {
            "dynamic_loads": macho.get("dynamic_loads"),
            "rpaths": macho.get("rpaths"),
        }
        if macho_closure is None:
            macho_closure = closure
        elif closure != macho_closure:
            raise VerificationError("Mach-O closure differs across products or passes")
        if record["quiescent_drift_detection"] != {
            "active_aba_provenance_claim": False,
            "binary_path_pre_post_drift_checked": True,
            "quiescent_exclusive_build_required": True,
            "source_path_pre_post_drift_checked": True,
            "tool_path_pre_post_drift_checked": True,
            "use_bound_provenance_claim": False,
        }:
            raise VerificationError("pass quiescent drift-detection record differs")
        _verify_source_proof(record["source_proof"], method)
        pass_hashes[product_id].append(record["binary_sha256"])
        pass_cdhashes[product_id].append(cdhash)
        uuid = macho.get("uuid")
        if not isinstance(uuid, str) or re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            uuid,
        ) is None:
            raise VerificationError("runtime LC_UUID receipt differs")
        pass_uuids[product_id].append(uuid)
        descriptor_hashes[product_id].append(record["descriptor_test_binary_sha256"])

    for product_id, method, role, observer in B.MATRIX:
        digests = pass_hashes[product_id]
        if len(digests) != 2 or digests[0] != digests[1]:
            raise VerificationError("two-pass binary identity differs")
        cdhashes = pass_cdhashes[product_id]
        if len(cdhashes) != 2 or cdhashes[0] != cdhashes[1]:
            raise VerificationError("two-pass runtime CDHash differs")
        uuids = pass_uuids[product_id]
        if len(uuids) != 2 or uuids[0] != uuids[1]:
            raise VerificationError("two-pass runtime LC_UUID differs")
        descriptor_digests = descriptor_hashes[product_id]
        if len(descriptor_digests) != 2 or descriptor_digests[0] != descriptor_digests[1]:
            raise VerificationError("two-pass descriptor test identity differs")
        descriptor_receipts = descriptor_macho_receipts[product_id]
        if len(descriptor_receipts) != 2 or descriptor_receipts[0] != descriptor_receipts[1]:
            raise VerificationError("two-pass descriptor test Mach-O receipt differs")
        binary_record, binary_raw = artifact_by_path[f"products/{product_id}/dancers"]
        second = next(item for item in passes if item["pass"] == 2 and item["product_id"] == product_id)
        if binary_record["sha256"] != second["binary_sha256"] or binary_record["bytes"] != second["binary_bytes"]:
            raise VerificationError("published binary differs from pass two")
        try:
            actual_macho = B._macho_runtime_receipt(binary_raw, repository_root)
        except B.BuildError as exc:
            raise VerificationError(str(exc)) from exc
        if second["macho"] != actual_macho:
            raise VerificationError("sealed Mach-O receipt differs from binary load commands")
        descriptor_record, descriptor_raw = artifact_by_path[
            f"products/{product_id}/{B.DESCRIPTOR_TEST_TARGET}"
        ]
        if (
            descriptor_record["sha256"] != second["descriptor_test_binary_sha256"]
            or descriptor_record["bytes"] != second["descriptor_test_binary_bytes"]
        ):
            raise VerificationError("published descriptor test differs from pass two")
        try:
            actual_descriptor_macho = B._macho_system_executable_receipt(
                descriptor_raw, f"{product_id} descriptor test",
                "__TEXT-file-bytes", B.DESCRIPTOR_TEST_TARGET, 0x00218085,
                require_anchored_empty_data_in_code=True,
            )
        except B.BuildError as exc:
            raise VerificationError(str(exc)) from exc
        if second["descriptor_test_macho"] != actual_descriptor_macho:
            raise VerificationError("descriptor test Mach-O receipt differs from bytes")
        required_runtime_tokens = (
            str(repository_root).encode("utf-8"),
            B.PROBE_ENV_NAME.encode("ascii"),
            B.PROBE_ENV_VALUE.encode("ascii"),
            B.SENTINEL_PATH.encode("ascii"),
            B.SENTINEL_SHA256.encode("ascii"),
        )
        if any(token not in binary_raw for token in required_runtime_tokens):
            raise VerificationError("binary lacks a static common-runtime-root probe binding")
        if any(
            token in binary_raw
            for token in (
                b"build/comparison/trace-v3/reference-src",
                b"build/comparison/trace-v3/optimized-src",
                b"/.staging-",
                b"/work/pass-",
            )
        ):
            raise VerificationError("binary retains a forbidden source-copy or staging root")
        if not all(
            marker in binary_raw for marker in LEGACY_OBSERVER_ENV_GUARD_MARKERS
        ):
            raise VerificationError(
                "binary lacks descriptor-mode legacy observer environment guards"
            )
        observer_markers_present = all(
            marker in binary_raw for marker in OBSERVER_ONLY_MARKERS
        )
        observer_markers_absent = all(
            marker not in binary_raw for marker in OBSERVER_ONLY_MARKERS
        )
        if observer and not observer_markers_present:
            raise VerificationError("fidelity binary lacks observer markers")
        if not observer and not observer_markers_absent:
            raise VerificationError("timing binary contains observer markers")

    actual_paths = _inventory_paths(build_root)
    expected_paths = sorted(
        [*_expected_artifact_paths(), "BUILD_COMPLETE.seal.json", "build-manifest-v1.json"],
        key=lambda item: item.encode("utf-8"),
    )
    if actual_paths != expected_paths:
        raise VerificationError("sealed filesystem inventory differs")

    # Re-read every byte-bound artifact after all semantic use.  The attester
    # consumes this verified in-memory snapshot and never reopens artifact
    # pathnames, while this final identity pass rejects a persistent swap made
    # after the first read.
    _post_artifact_semantic_hook(build_root, artifact_by_path)
    for path, (record, original_raw) in artifact_by_path.items():
        final_raw = _read_published(
            build_root, record, f"artifact {path} final custody"
        )
        if final_raw != original_raw:
            raise VerificationError("published artifact changed across verification")

    _exact_keys(seal, {"artifact_count", "artifact_inventory_sha256", "build_complete", "design_sha256", "manifest", "operations", "schema_version", "science_flags"}, "build seal")
    digest = hashlib.sha256(b"dancers-headless-runtime-artifacts-v1\0")
    for artifact in artifacts:
        digest.update(B._canonical_json(artifact))
    expected_seal = {
        "artifact_count": len(artifacts),
        "artifact_inventory_sha256": digest.hexdigest(),
        "build_complete": True,
        "design_sha256": B.DESIGN_SHA256,
        "manifest": B._identity("build-manifest-v1.json", manifest_raw),
        "operations": B.OPERATIONS,
        "schema_version": B.SEAL_SCHEMA,
        "science_flags": B.SCIENCE_FLAGS,
    }
    if seal != expected_seal:
        raise VerificationError("build seal differs")
    verdict = {
        "artifact_count": len(artifacts),
        "build_complete": True,
        "build_root": str(build_root),
        "design_sha256": B.DESIGN_SHA256,
        "host_bound": True,
        "implementation": B._identity(BUILDER_PATH, builder_raw),
        "manifest_sha256": B._sha256(manifest_raw),
        "schema_version": B.MANIFEST_SCHEMA,
        "science_flags": B.SCIENCE_FLAGS,
        "static_read_only_verification": True,
    }
    try:
        manifest_post = B._read_regular(
            build_root / "build-manifest-v1.json", 4 * 1024 * 1024,
            "build manifest post-verification",
        )
        seal_post = B._read_regular(
            build_root / "BUILD_COMPLETE.seal.json", 1 * 1024 * 1024,
            "build seal post-verification",
        )
    except B.BuildError as exc:
        raise VerificationError(str(exc)) from exc
    if manifest_post != manifest_raw or seal_post != seal_raw:
        raise VerificationError("manifest or seal changed across static verification")
    return VerifiedBuild(
        verdict=verdict,
        artifact_raw_by_path={
            path: raw for path, (_, raw) in artifact_by_path.items()
        },
        manifest=manifest,
        manifest_raw=manifest_raw,
        seal_raw=seal_raw,
    )


def verify_build_custody(repository_root: Path, build_root: Path) -> VerifiedBuild:
    """Verify with a builder module compiled from the supplied exact bytes."""
    global B
    B._require_isolated_python()
    lexical_repository_root = repository_root.absolute()
    try:
        canonical_repository_root = repository_root.resolve(strict=True)
    except OSError as exc:
        raise VerificationError(f"repository root is unavailable: {exc}") from exc
    if lexical_repository_root != canonical_repository_root:
        raise VerificationError("repository root must be supplied in canonical physical form")
    try:
        builder_path, builder_raw = _capture_authenticated_source(
            canonical_repository_root,
            BUILDER_PATH,
            EXPECTED_BUILDER_BYTES,
            EXPECTED_BUILDER_SHA256,
            "runtime builder",
        )
        repository_builder = _exec_authenticated_source(
            "_dancers_repository_bound_headless_runtime_builder_v1",
            builder_path,
            builder_raw,
        )
    except _BootstrapError as exc:
        raise VerificationError(str(exc)) from exc
    bootstrap_builder = B
    B = repository_builder
    try:
        return _verify_build_custody_bound(
            canonical_repository_root, build_root, builder_raw
        )
    finally:
        B = bootstrap_builder


def verify_build(repository_root: Path, build_root: Path) -> dict[str, Any]:
    return verify_build_custody(repository_root, build_root).verdict


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", default=str(ROOT))
    parser.add_argument("--build-root", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        B._require_isolated_python()
    except B.BuildError as exc:
        print(f"headless-runtime-build-verifier-v1: reject: {exc}", file=sys.stderr)
        return 1
    args = _parser().parse_args(argv)
    try:
        verdict = verify_build(Path(args.repository_root), Path(args.build_root))
    except (VerificationError, B.BuildError, OSError) as exc:
        print(f"headless-runtime-build-verifier-v1: reject: {exc}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(B._canonical_json(verdict))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
