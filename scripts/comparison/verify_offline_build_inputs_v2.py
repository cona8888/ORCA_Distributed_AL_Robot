#!/usr/bin/env python3
"""Verify the versioned, build-input-only Eigen provenance extension.

V2 does not reinterpret the deliberately incomplete v1 checkpoint.  It binds
that exact reviewed predecessor, revalidates its two offline source archives
and the current CMake dependency declarations, and additionally proves that
the vendored Eigen headers are byte-for-byte the selected ``Eigen/`` subtree
of a committed upstream 3.4.0 source archive.  It also authenticates all seven
upstream license files and the separate repository-local CMake package file.

This program never extracts an archive, configures or builds the project,
contacts a network, runs a simulator, or authorizes scientific execution.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tarfile
import types
from typing import Any, Sequence
import zlib


SCHEMA_VERSION = "dancers-offline-build-inputs-v2"
DEFAULT_MANIFEST = "thirdparty/dist/offline_build_inputs_v2.json"
EXPECTED_MANIFEST_SHA256 = (
    "6ed518dbf1b8d1d1ff4085dc4763cd717cfddbf04cf98258b90b4d77858a1aff"
)

V1_SCHEMA_VERSION = "dancers-offline-build-inputs-v1"
V1_MANIFEST_PATH = "thirdparty/dist/offline_build_inputs_v1.json"
V1_MANIFEST_SHA256 = (
    "a33b7ca07461e019cb8b178b0fb7496a8a40ba5af54aa4991e57fcf39f7252fa"
)
V1_VERIFIER_PATH = "scripts/comparison/verify_offline_build_inputs_v1.py"
V1_VERIFIER_SHA256 = (
    "e625afb316423ed9f89ef488ad1ebc8fc718de877bd8fa8904be82d78a36639b"
)

EIGEN_VERSION = "3.4.0"
EIGEN_TAG = "3.4.0"
EIGEN_COMMIT = "3147391d946bb4b6c68edd901f2add6ac1f31f8c"
EIGEN_REPOSITORY = "gitlab.com/libeigen/eigen"
EIGEN_ROOT = f"eigen-{EIGEN_COMMIT}"
EIGEN_LICENSES = [
    "COPYING.APACHE",
    "COPYING.BSD",
    "COPYING.GPL",
    "COPYING.LGPL",
    "COPYING.MINPACK",
    "COPYING.MPL2",
    "COPYING.README",
]

MAX_MANIFEST_BYTES = 262_144
MAX_ARCHIVE_BYTES = 4_194_304
MAX_ARCHIVE_MEMBERS = 4_096
MAX_ARCHIVE_MEMBER_BYTES = 2_097_152
MAX_ARCHIVE_REGULAR_BYTES = 33_554_432
MAX_ARCHIVE_TAR_STREAM_BYTES = 25_165_824
MAX_LICENSE_BYTES = 1_048_576
MAX_PREDECESSOR_BYTES = 1_048_576
MAX_CMAKE_BYTES = 1_048_576
MAX_PATH_BYTES = 512
MAX_TREE_FILES = 4_096
MAX_TREE_FILE_BYTES = 8_388_608
MAX_TREE_REGULAR_BYTES = 16_777_216

LIMITS = {
    "archive_bytes_max": MAX_ARCHIVE_BYTES,
    "archive_member_bytes_max": MAX_ARCHIVE_MEMBER_BYTES,
    "archive_members_max": MAX_ARCHIVE_MEMBERS,
    "archive_regular_bytes_max": MAX_ARCHIVE_REGULAR_BYTES,
    "archive_tar_stream_bytes_max": MAX_ARCHIVE_TAR_STREAM_BYTES,
    "cmake_bytes_max": MAX_CMAKE_BYTES,
    "license_bytes_max": MAX_LICENSE_BYTES,
    "manifest_bytes_max": MAX_MANIFEST_BYTES,
    "path_bytes_max": MAX_PATH_BYTES,
    "predecessor_bytes_max": MAX_PREDECESSOR_BYTES,
    "tree_file_bytes_max": MAX_TREE_FILE_BYTES,
    "tree_files_max": MAX_TREE_FILES,
    "tree_regular_bytes_max": MAX_TREE_REGULAR_BYTES,
}

CLASSIFICATION = {
    "eligible_as_build_input": True,
    "execution_eligible": False,
    "offline_build_inputs_authenticated": True,
    "provenance_complete": True,
    "scientific_execution_authorized": False,
}

TOP_KEYS = {
    "classification",
    "cmake",
    "limits",
    "predecessor",
    "schema_version",
    "tree_digest",
    "vendored_eigen",
}


class VerificationError(ValueError):
    """A v2 input-provenance contract violation."""


def _module_from_authenticated_source(raw: bytes, path: Path, name: str) -> Any:
    """Execute exactly ``raw``; never consult a pathname loader or bytecode cache."""

    module = types.ModuleType(name)
    module.__file__ = os.fspath(path)
    module.__package__ = ""
    sys.modules[name] = module
    try:
        code = compile(raw, os.fspath(path), "exec", dont_inherit=True)
        exec(code, module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _load_reviewed_v1() -> Any:
    """Load only the exact reviewed v1 implementation used by this verifier."""

    path = Path(__file__).resolve().with_name("verify_offline_build_inputs_v1.py")
    try:
        info = path.stat()
        raw = path.read_bytes()
        stable = path.stat()
    except OSError as exc:
        raise RuntimeError(f"cannot authenticate the v1 verifier: {exc}") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RuntimeError("the v1 verifier is not a single-link regular file")
    if info.st_uid != os.geteuid() or info.st_mode & 0o022:
        raise RuntimeError("the v1 verifier is not private to the effective user")
    if (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) != (
        stable.st_dev,
        stable.st_ino,
        stable.st_size,
        stable.st_mtime_ns,
    ):
        raise RuntimeError("the v1 verifier changed while it was authenticated")
    if hashlib.sha256(raw).hexdigest() != V1_VERIFIER_SHA256:
        raise RuntimeError("the v1 verifier differs from the reviewed implementation")
    return _module_from_authenticated_source(raw, path, "dancers_offline_inputs_v1")


V1 = _load_reviewed_v1()
if (
    V1.MAX_PATH_BYTES,
    V1.MAX_TREE_FILES,
    V1.MAX_TREE_FILE_BYTES,
    V1.MAX_TREE_REGULAR_BYTES,
) != (
    MAX_PATH_BYTES,
    MAX_TREE_FILES,
    MAX_TREE_FILE_BYTES,
    MAX_TREE_REGULAR_BYTES,
):
    raise RuntimeError("v1 helper bounds differ from the v2 manifest contract")


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise VerificationError(f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> list[Any]:
    if type(value) is not list:
        raise VerificationError(f"{label} must be an array")
    return value


def _string(value: Any, label: str) -> str:
    if type(value) is not str:
        raise VerificationError(f"{label} must be a string")
    return value


def _integer(value: Any, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise VerificationError(f"{label} must be an integer >= {minimum}")
    return value


def _boolean(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise VerificationError(f"{label} must be a boolean")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(value)
    if actual != expected:
        raise VerificationError(
            f"{label} keys differ: missing={sorted(expected - actual)}, "
            f"extra={sorted(actual - expected)}"
        )


def _sha256(value: Any, label: str) -> str:
    digest = _string(value, label)
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise VerificationError(f"{label} must be lowercase SHA-256 hex")
    return digest


def _bounded_single_gzip_member(raw: bytes, label: str) -> bytes:
    if len(raw) < 18 or raw[:3] != b"\x1f\x8b\x08":
        raise VerificationError(f"{label} is not a gzip deflate stream")
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    output = bytearray()
    cursor = 0
    try:
        while cursor < len(raw) and not decompressor.eof:
            pending = raw[cursor : cursor + 65_536]
            cursor += len(pending)
            while pending and not decompressor.eof:
                remaining = MAX_ARCHIVE_TAR_STREAM_BYTES - len(output)
                chunk = decompressor.decompress(pending, remaining + 1)
                output.extend(chunk)
                if len(output) > MAX_ARCHIVE_TAR_STREAM_BYTES:
                    raise VerificationError(f"{label} exceeds the tar-stream byte bound")
                next_pending = decompressor.unconsumed_tail
                if next_pending == pending and not chunk:
                    raise VerificationError(f"{label} decompressor made no progress")
                pending = next_pending
        if not decompressor.eof:
            raise VerificationError(f"{label} has no authenticated gzip EOF")
        if decompressor.unused_data or raw[cursor:]:
            raise VerificationError(f"{label} has trailing bytes or another gzip member")
        output.extend(
            decompressor.flush(MAX_ARCHIVE_TAR_STREAM_BYTES - len(output) + 1)
        )
    except zlib.error as exc:
        raise VerificationError(f"{label} gzip framing or checksum is invalid") from exc
    if len(output) > MAX_ARCHIVE_TAR_STREAM_BYTES:
        raise VerificationError(f"{label} exceeds the tar-stream byte bound")
    return bytes(output)


def _validate_predecessor(root_fd: int, value: Any) -> dict[str, Any]:
    record = _mapping(value, "predecessor")
    _exact_keys(record, {"manifest", "schema_version", "verifier"}, "predecessor")
    if record["schema_version"] != V1_SCHEMA_VERSION:
        raise VerificationError("predecessor schema_version mismatch")
    manifest, manifest_raw = V1._validate_file_identity(
        root_fd, record["manifest"], "predecessor.manifest", MAX_PREDECESSOR_BYTES
    )
    verifier, verifier_raw = V1._validate_file_identity(
        root_fd, record["verifier"], "predecessor.verifier", MAX_PREDECESSOR_BYTES
    )
    if manifest["path"] != V1_MANIFEST_PATH or manifest["sha256"] != V1_MANIFEST_SHA256:
        raise VerificationError("predecessor manifest is not the exact reviewed v1 manifest")
    if verifier["path"] != V1_VERIFIER_PATH or verifier["sha256"] != V1_VERIFIER_SHA256:
        raise VerificationError("predecessor verifier is not the exact reviewed v1 verifier")
    if hashlib.sha256(verifier_raw).hexdigest() != V1_VERIFIER_SHA256:
        raise VerificationError("predecessor verifier bytes mismatch")
    parsed = V1._mapping(V1._parse_json(manifest_raw, "v1 manifest"), "v1 manifest")
    V1._exact_keys(parsed, V1.TOP_KEYS, "v1 manifest")
    if parsed["schema_version"] != V1_SCHEMA_VERSION:
        raise VerificationError("predecessor manifest schema mismatch")
    return parsed


def _validate_archive(root_fd: int, value: Any) -> dict[str, Any]:
    record = _mapping(value, "vendored_eigen.upstream_archive")
    _exact_keys(
        record,
        {
            "archive",
            "directory_count",
            "format",
            "full_tree",
            "member_count",
            "regular_file_count",
            "root_directory",
            "source",
            "tar_stream_bytes",
            "uncompressed_regular_bytes",
        },
        "vendored_eigen.upstream_archive",
    )
    source = _mapping(record["source"], "vendored_eigen.upstream_archive.source")
    _exact_keys(source, {"repository", "tag", "upstream_commit"}, "upstream source")
    if source != {
        "repository": EIGEN_REPOSITORY,
        "tag": EIGEN_TAG,
        "upstream_commit": EIGEN_COMMIT,
    }:
        raise VerificationError("Eigen upstream source identity mismatch")
    if record["root_directory"] != EIGEN_ROOT:
        raise VerificationError("Eigen archive root mismatch")
    archive_format = _mapping(record["format"], "vendored_eigen.upstream_archive.format")
    expected_format = {
        "directory_mode": 0o775,
        "file_modes": [0o664, 0o775],
        "gid": 0,
        "gname": "root",
        "mtime": 1_629_319_318,
        "pax_comment": EIGEN_COMMIT,
        "uid": 0,
        "uname": "root",
    }
    if archive_format != expected_format:
        raise VerificationError("Eigen archive format contract mismatch")
    _, raw = V1._validate_file_identity(
        root_fd,
        record["archive"],
        "vendored_eigen.upstream_archive.archive",
        MAX_ARCHIVE_BYTES,
    )
    tar_stream = _bounded_single_gzip_member(raw, "Eigen upstream archive")
    if _integer(record["tar_stream_bytes"], "Eigen tar_stream_bytes") != len(tar_stream):
        raise VerificationError("Eigen tar-stream byte count mismatch")
    try:
        archive = tarfile.open(
            fileobj=io.BytesIO(tar_stream), mode="r:", encoding="utf-8", errors="strict"
        )
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise VerificationError("Eigen archive is not a valid tar stream") from exc

    entries: list[tuple[str, bytes]] = []
    selected: list[tuple[str, bytes]] = []
    selected_modes: dict[str, int] = {}
    licenses: dict[str, bytes] = {}
    kinds: list[tuple[str, str]] = []
    seen: set[str] = set()
    member_count = directory_count = regular_count = regular_bytes = 0
    root_seen = False
    with archive:
        try:
            if archive.pax_headers != {"comment": EIGEN_COMMIT}:
                raise VerificationError("Eigen global PAX identity mismatch")
            for number, member in enumerate(archive):
                member_count += 1
                if member_count > MAX_ARCHIVE_MEMBERS:
                    raise VerificationError("Eigen archive exceeds the member-count bound")
                label = f"Eigen member[{number}]"
                path = V1._archive_path(member.name, f"{label}.name")
                if path in seen:
                    raise VerificationError(f"duplicate Eigen archive member {path!r}")
                seen.add(path)
                if member.pax_headers != {"comment": EIGEN_COMMIT}:
                    raise VerificationError(f"{label} PAX identity mismatch")
                if member.sparse:
                    raise VerificationError(f"{label} is sparse")
                if (member.uid, member.gid, member.uname, member.gname, member.mtime) != (
                    0,
                    0,
                    "root",
                    "root",
                    1_629_319_318,
                ):
                    raise VerificationError(f"{label} metadata mismatch")
                parts = path.split("/")
                if parts[0] != EIGEN_ROOT:
                    raise VerificationError(f"{label} escapes the single archive root")
                relative = "/".join(parts[1:])
                if not relative:
                    if (
                        root_seen
                        or not member.isdir()
                        or member.mode != 0o775
                        or member.size != 0
                    ):
                        raise VerificationError("Eigen archive root member is invalid")
                    root_seen = True
                    directory_count += 1
                    kinds.append((path, "directory"))
                    continue
                if member.isdir():
                    if member.mode != 0o775 or member.size != 0:
                        raise VerificationError(f"{label} directory metadata mismatch")
                    directory_count += 1
                    kinds.append((path, "directory"))
                    continue
                if not member.isfile() or member.mode not in (0o664, 0o775):
                    raise VerificationError(f"{label} is not a canonical regular file")
                if member.size < 0 or member.size > MAX_ARCHIVE_MEMBER_BYTES:
                    raise VerificationError(f"{label} exceeds the member-byte bound")
                regular_count += 1
                regular_bytes += member.size
                if regular_bytes > MAX_ARCHIVE_REGULAR_BYTES:
                    raise VerificationError("Eigen archive exceeds the regular-byte bound")
                kinds.append((path, "file"))
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise VerificationError(f"{label} cannot be read")
                payload = extracted.read(MAX_ARCHIVE_MEMBER_BYTES + 1)
                if len(payload) != member.size:
                    raise VerificationError(f"{label} payload size mismatch")
                entries.append((relative, payload))
                if relative.startswith("Eigen/"):
                    selected_path = relative[len("Eigen/") :]
                    selected.append((selected_path, payload))
                    selected_modes[selected_path] = member.mode & ~0o022
                if relative in EIGEN_LICENSES:
                    licenses[relative] = payload
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise VerificationError("Eigen tar stream is invalid") from exc
        tar_end = archive.offset

    if (
        tar_end + 1_024 > len(tar_stream)
        or tar_stream[tar_end : tar_end + 1_024] != bytes(1_024)
        or any(tar_stream[tar_end + 1_024 :])
    ):
        raise VerificationError("Eigen archive has noncanonical tar EOF data")
    if not root_seen:
        raise VerificationError("Eigen archive has no explicit root member")
    V1._validate_path_kinds(kinds, "Eigen archive members")
    if set(licenses) != set(EIGEN_LICENSES):
        raise VerificationError("Eigen archive does not contain the exact license set")

    numbers = {
        "directory_count": directory_count,
        "member_count": member_count,
        "regular_file_count": regular_count,
        "uncompressed_regular_bytes": regular_bytes,
    }
    for key, actual in numbers.items():
        if _integer(record[key], f"Eigen {key}") != actual:
            raise VerificationError(f"Eigen archive {key} mismatch")
    full_tree = _mapping(record["full_tree"], "vendored_eigen.upstream_archive.full_tree")
    _exact_keys(full_tree, {"regular_bytes", "regular_file_count", "sha256"}, "full_tree")
    if _integer(full_tree["regular_file_count"], "full_tree.regular_file_count") != len(entries):
        raise VerificationError("Eigen full-tree file count mismatch")
    if _integer(full_tree["regular_bytes"], "full_tree.regular_bytes") != regular_bytes:
        raise VerificationError("Eigen full-tree byte count mismatch")
    full_sha = _sha256(full_tree["sha256"], "full_tree.sha256")
    if V1.regular_tree_digest(entries) != full_sha:
        raise VerificationError("Eigen full-tree SHA-256 mismatch")
    return {
        "full_tree_sha256": full_sha,
        "licenses": licenses,
        "selected": selected,
        "selected_bytes": sum(len(payload) for _, payload in selected),
        "selected_modes": selected_modes,
    }


def _read_repository_tree(
    root_fd: int,
    relative_root: str,
    expected_file_modes: dict[str, int],
) -> tuple[list[tuple[str, bytes]], int]:
    """Read bytes and modes in one descriptor-relative authenticated traversal."""

    directory_fd = V1._open_relative(root_fd, relative_root, "directory")
    seen_files: set[str] = set()
    entries: list[tuple[str, bytes]] = []
    total_bytes = 0
    try:
        root_info = os.fstat(directory_fd)
        if stat.S_IMODE(root_info.st_mode) != 0o755:
            raise VerificationError("vendored Eigen root mode is not 0755")

        def visit(fd: int, prefix: str) -> None:
            nonlocal total_bytes
            try:
                names = sorted(os.listdir(fd), key=lambda item: item.encode("utf-8"))
            except OSError as exc:
                raise VerificationError(f"cannot enumerate vendored Eigen modes: {exc}") from exc
            for name in names:
                V1._validate_relative_path(name, "vendored Eigen entry name")
                path = name if not prefix else f"{prefix}/{name}"
                try:
                    before = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except OSError as exc:
                    raise VerificationError(f"cannot stat vendored Eigen entry {path!r}") from exc
                if before.st_uid != os.geteuid() or before.st_mode & 0o022:
                    raise VerificationError(f"vendored Eigen entry {path!r} is not private")
                if stat.S_ISDIR(before.st_mode):
                    if stat.S_IMODE(before.st_mode) != 0o755:
                        raise VerificationError(f"vendored Eigen directory {path!r} is not 0755")
                    child, opened = V1._open_child_reauthenticated(
                        fd, name, "directory", f"vendored Eigen directory {path}"
                    )
                    try:
                        visit(child, path)
                    finally:
                        os.close(child)
                    try:
                        after = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    except OSError as exc:
                        raise VerificationError(
                            f"vendored Eigen directory {path!r} disappeared"
                        ) from exc
                    if not V1._same_inode(opened, after):
                        raise VerificationError(
                            f"vendored Eigen directory {path!r} changed identity"
                        )
                    continue
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                    raise VerificationError(
                        f"vendored Eigen entry {path!r} is not a single-link regular file"
                    )
                expected_mode = expected_file_modes.get(path)
                if expected_mode is None:
                    raise VerificationError(
                        f"vendored Eigen file {path!r} is absent from the archive mode map"
                    )
                if stat.S_IMODE(before.st_mode) != expected_mode:
                    raise VerificationError(
                        f"vendored Eigen file {path!r} has a noncanonical normalized mode"
                    )
                if len(entries) >= MAX_TREE_FILES:
                    raise VerificationError("vendored Eigen exceeds the file-count bound")
                child, opened = V1._open_child_reauthenticated(
                    fd, name, "regular", f"vendored Eigen file {path}"
                )
                try:
                    payload, stable = V1._read_fd(child, MAX_TREE_FILE_BYTES, path)
                finally:
                    os.close(child)
                try:
                    named = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except OSError as exc:
                    raise VerificationError(
                        f"vendored Eigen file {path!r} disappeared"
                    ) from exc
                if not (
                    V1._same_inode(opened, stable)
                    and V1._same_inode(opened, named)
                ):
                    raise VerificationError(
                        f"vendored Eigen file {path!r} changed identity"
                    )
                total_bytes += len(payload)
                if total_bytes > MAX_TREE_REGULAR_BYTES:
                    raise VerificationError("vendored Eigen exceeds the byte bound")
                entries.append((path, payload))
                seen_files.add(path)

        visit(directory_fd, "")
        if seen_files != set(expected_file_modes):
            raise VerificationError("vendored Eigen mode map and live file set differ")
    finally:
        os.close(directory_fd)
    return entries, total_bytes


def _validate_eigen(root_fd: int, value: Any) -> dict[str, Any]:
    record = _mapping(value, "vendored_eigen")
    _exact_keys(
        record,
        {
            "config",
            "config_version",
            "licenses",
            "normalized_repository_modes",
            "provenance_complete",
            "root",
            "selected_tree",
            "upstream_archive",
            "version",
        },
        "vendored_eigen",
    )
    if record["version"] != EIGEN_VERSION or record["root"] != "thirdparty/Eigen":
        raise VerificationError("vendored Eigen version or root mismatch")
    if _boolean(record["provenance_complete"], "vendored_eigen.provenance_complete") is not True:
        raise VerificationError("vendored Eigen provenance must be complete in v2")
    if record["normalized_repository_modes"] != {
        "directory": 0o755,
        "regular": [0o644, 0o755],
    }:
        raise VerificationError("vendored Eigen repository-mode contract mismatch")
    archive = _validate_archive(root_fd, record["upstream_archive"])

    entries, total_bytes = _read_repository_tree(
        root_fd, "thirdparty/Eigen", archive["selected_modes"]
    )
    selected_tree = _mapping(record["selected_tree"], "vendored_eigen.selected_tree")
    _exact_keys(
        selected_tree,
        {"archive_path", "regular_bytes", "regular_file_count", "sha256"},
        "vendored_eigen.selected_tree",
    )
    if selected_tree["archive_path"] != "Eigen":
        raise VerificationError("vendored Eigen selected archive path mismatch")
    expected_count = _integer(selected_tree["regular_file_count"], "selected_tree count")
    expected_bytes = _integer(selected_tree["regular_bytes"], "selected_tree bytes")
    expected_sha = _sha256(selected_tree["sha256"], "selected_tree.sha256")
    if (len(entries), total_bytes) != (expected_count, expected_bytes):
        raise VerificationError("vendored Eigen tree size mismatch")
    if V1.regular_tree_digest(entries) != expected_sha:
        raise VerificationError("vendored Eigen tree digest mismatch")
    if (
        len(archive["selected"]) != expected_count
        or archive["selected_bytes"] != expected_bytes
        or V1.regular_tree_digest(archive["selected"]) != expected_sha
    ):
        raise VerificationError("vendored Eigen tree differs from the upstream archive")

    config, _ = V1._validate_file_identity(
        root_fd, record["config"], "vendored_eigen.config", MAX_CMAKE_BYTES
    )
    if config["path"] != "thirdparty/Eigen3Config.cmake":
        raise VerificationError("vendored Eigen config path mismatch")
    config_version, _ = V1._validate_file_identity(
        root_fd,
        record["config_version"],
        "vendored_eigen.config_version",
        MAX_CMAKE_BYTES,
    )
    if config_version["path"] != "thirdparty/Eigen3ConfigVersion.cmake":
        raise VerificationError("vendored Eigen config-version path mismatch")
    license_values = _sequence(record["licenses"], "vendored_eigen.licenses")
    if len(license_values) != len(EIGEN_LICENSES):
        raise VerificationError("vendored Eigen license count mismatch")
    for index, expected_name in enumerate(EIGEN_LICENSES):
        item = _mapping(license_values[index], f"vendored_eigen.licenses[{index}]")
        _exact_keys(item, {"archive_path", "file"}, f"vendored_eigen.licenses[{index}]")
        if item["archive_path"] != expected_name:
            raise VerificationError("vendored Eigen license order/path mismatch")
        identity, payload = V1._validate_file_identity(
            root_fd,
            item["file"],
            f"vendored_eigen.licenses[{index}].file",
            MAX_LICENSE_BYTES,
        )
        if identity["path"] != f"thirdparty/{expected_name}":
            raise VerificationError("vendored Eigen repository license path mismatch")
        if payload != archive["licenses"][expected_name]:
            raise VerificationError("vendored Eigen license differs from the upstream archive")
    return {
        "archive_tree_sha256": archive["full_tree_sha256"],
        "selected_tree_sha256": expected_sha,
    }


def verify_repository(
    repository_root: Path,
    manifest_relative_path: str = DEFAULT_MANIFEST,
    expected_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Authenticate v2 inputs without running a build, test, or simulator."""

    expected = expected_manifest_sha256 or EXPECTED_MANIFEST_SHA256
    _sha256(expected, "expected manifest SHA-256")
    root_fd = V1._open_root(repository_root)
    try:
        manifest_path = V1._validate_relative_path(manifest_relative_path, "manifest path")
        raw, _ = V1._secure_read(root_fd, manifest_path, MAX_MANIFEST_BYTES)
        manifest_sha = hashlib.sha256(raw).hexdigest()
        if manifest_sha != expected:
            raise VerificationError("manifest SHA-256 differs from the independent expectation")
        manifest = _mapping(V1._parse_json(raw, "offline v2 manifest"), "manifest")
        _exact_keys(manifest, TOP_KEYS, "manifest")
        if manifest["schema_version"] != SCHEMA_VERSION:
            raise VerificationError("manifest schema_version mismatch")
        if manifest["classification"] != CLASSIFICATION:
            raise VerificationError("manifest classification mismatch")
        if manifest["limits"] != LIMITS:
            raise VerificationError("manifest limits differ from verifier constants")
        tree_digest = _mapping(manifest["tree_digest"], "tree_digest")
        if tree_digest != {
            "algorithm": "sha256",
            "domain": V1.TREE_DOMAIN[:-1].decode("ascii"),
            "entry_encoding": V1.TREE_ENCODING,
        }:
            raise VerificationError("manifest tree-digest contract mismatch")

        predecessor = _validate_predecessor(root_fd, manifest["predecessor"])
        archives = _sequence(predecessor["archives"], "v1 archives")
        if len(archives) != 2:
            raise VerificationError("v1 predecessor archive count mismatch")
        archive_results = [
            V1._validate_archive(root_fd, value, index)
            for index, value in enumerate(archives)
        ]
        if [item["name"] for item in archive_results] != ["manif", "raylib"]:
            raise VerificationError("v1 predecessor archive order mismatch")
        V1._validate_cmake(root_fd, manifest["cmake"], archives)
        eigen = _validate_eigen(root_fd, manifest["vendored_eigen"])
        return {
            "eligible_as_build_input": True,
            "execution_eligible": False,
            "manifest_sha256": manifest_sha,
            "offline_build_inputs_authenticated": True,
            "provenance_complete": True,
            "scientific_execution_authorized": False,
            "vendored_eigen_archive_tree_sha256": eigen["archive_tree_sha256"],
            "vendored_eigen_tree_sha256": eigen["selected_tree_sha256"],
        }
    except V1.VerificationError as exc:
        raise VerificationError(str(exc)) from exc
    finally:
        os.close(root_fd)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="absolute repository root (default: inferred from this script)",
    )
    parser.add_argument("--manifest", default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--expected-manifest-sha256",
        default=EXPECTED_MANIFEST_SHA256,
        help="independently authenticated expected v2 manifest SHA-256",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = verify_repository(
            args.repository_root, args.manifest, args.expected_manifest_sha256
        )
    except (OSError, VerificationError) as exc:
        print(f"offline-build-inputs-v2: reject: {exc}", file=sys.stderr)
        return 1
    print(canonical_json_bytes(result).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
