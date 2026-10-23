#!/usr/bin/env python3
"""Authenticate the bounded, offline CMake source inputs for the physics build.

This verifier is deliberately read-only.  It does not configure or compile the
project, extract an archive, contact a network, run tests, or launch a simulator.
The manifest describes the presently reviewed inputs, including an explicit
incomplete-provenance blocker for the vendored Eigen tree's absent license.
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
import unicodedata
from typing import Any, Iterable, Sequence
import zlib


SCHEMA_VERSION = "dancers-offline-build-inputs-v1"
DEFAULT_MANIFEST = "thirdparty/dist/offline_build_inputs_v1.json"
# Callers may instead supply the same digest through an independently
# authenticated execution-identity manifest to verify_repository().
EXPECTED_MANIFEST_SHA256 = (
    "a33b7ca07461e019cb8b178b0fb7496a8a40ba5af54aa4991e57fcf39f7252fa"
)

TREE_DOMAIN = b"dancers-offline-build-input-tree-v1\0"
TREE_ENCODING = (
    "sha256(domain_utf8_nul || for each UTF-8 bytewise-sorted regular file: "
    "'file\\0' || uint64be(path_bytes) || path || uint64be(file_bytes) || "
    "sha256(file_bytes))"
)

MAX_MANIFEST_BYTES = 262_144
MAX_ARCHIVE_BYTES = 4_194_304
MAX_ARCHIVE_MEMBERS = 1_024
MAX_ARCHIVE_MEMBER_BYTES = 8_388_608
MAX_ARCHIVE_REGULAR_BYTES = 33_554_432
MAX_ARCHIVE_TAR_STREAM_BYTES = 16_777_216
MAX_TREE_FILES = 4_096
MAX_TREE_FILE_BYTES = 8_388_608
MAX_TREE_REGULAR_BYTES = 16_777_216
MAX_PATH_BYTES = 512
MAX_CMAKE_BYTES = 1_048_576

LIMITS = {
    "archive_bytes_max": MAX_ARCHIVE_BYTES,
    "archive_member_bytes_max": MAX_ARCHIVE_MEMBER_BYTES,
    "archive_members_max": MAX_ARCHIVE_MEMBERS,
    "archive_regular_bytes_max": MAX_ARCHIVE_REGULAR_BYTES,
    "archive_tar_stream_bytes_max": MAX_ARCHIVE_TAR_STREAM_BYTES,
    "cmake_bytes_max": MAX_CMAKE_BYTES,
    "manifest_bytes_max": MAX_MANIFEST_BYTES,
    "path_bytes_max": MAX_PATH_BYTES,
    "tree_file_bytes_max": MAX_TREE_FILE_BYTES,
    "tree_files_max": MAX_TREE_FILES,
    "tree_regular_bytes_max": MAX_TREE_REGULAR_BYTES,
}

TOP_KEYS = {
    "archives",
    "classification",
    "cmake",
    "limits",
    "schema_version",
    "tree_digest",
    "vendored_eigen",
}
FILE_IDENTITY_KEYS = {"bytes", "mode", "path", "sha256"}


class VerificationError(ValueError):
    """An authenticated-input contract violation."""


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


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _reject_constant(value: str) -> None:
    raise VerificationError(f"non-standard JSON constant {value!r}")


def _object_pairs(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VerificationError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _parse_json(raw: bytes, label: str) -> Any:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VerificationError(f"{label} is not UTF-8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_constant,
        )
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise VerificationError(f"{label} is not strict JSON: {exc}") from exc
    if canonical_json_bytes(value) != raw:
        raise VerificationError(f"{label} is not canonical JSON")
    return value


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


def _validate_relative_path(value: Any, label: str) -> str:
    path = _string(value, label)
    if not path or "\\" in path or "\x00" in path:
        raise VerificationError(f"{label} is not a portable relative path")
    if unicodedata.normalize("NFC", path) != path:
        raise VerificationError(f"{label} is not NFC-normalized")
    try:
        encoded = path.encode("utf-8", "strict")
    except UnicodeEncodeError as exc:
        raise VerificationError(f"{label} is not strict UTF-8") from exc
    if len(encoded) > MAX_PATH_BYTES:
        raise VerificationError(f"{label} exceeds the path-byte bound")
    pure = PurePosixPath(path)
    if (
        not pure.parts
        or pure.is_absolute()
        or any(part in ("", ".", "..") for part in pure.parts)
    ):
        raise VerificationError(f"{label} is not a normalized relative path")
    if pure.as_posix() != path:
        raise VerificationError(f"{label} is not a canonical POSIX path")
    return path


def _private_stat(info: os.stat_result, label: str, expected_type: str) -> None:
    if info.st_uid != os.geteuid():
        raise VerificationError(f"{label} is not owned by the effective user")
    if info.st_mode & 0o022:
        raise VerificationError(f"{label} is group- or world-writable")
    if expected_type == "directory" and not stat.S_ISDIR(info.st_mode):
        raise VerificationError(f"{label} is not a directory")
    if expected_type == "regular" and not stat.S_ISREG(info.st_mode):
        raise VerificationError(f"{label} is not a regular file")
    if expected_type == "regular" and info.st_nlink != 1:
        raise VerificationError(f"{label} has more than one hard link")


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _open_child_reauthenticated(
    parent_fd: int,
    name: str,
    expected_type: str,
    label: str,
) -> tuple[int, os.stat_result]:
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as exc:
        raise VerificationError(f"cannot stat {label!r} safely: {exc}") from exc
    _private_stat(before, label, expected_type)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    if expected_type == "directory":
        flags |= getattr(os, "O_DIRECTORY", 0)
    try:
        child = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise VerificationError(f"cannot open {label!r} safely: {exc}") from exc
    try:
        opened = os.fstat(child)
        _private_stat(opened, label, expected_type)
        after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        _private_stat(after, label, expected_type)
        if not (_same_inode(before, opened) and _same_inode(opened, after)):
            raise VerificationError(f"{label} changed identity while it was opened")
        return child, opened
    except Exception:
        os.close(child)
        raise


def _open_root(root: Path) -> int:
    if not root.is_absolute():
        raise VerificationError("repository root must be absolute")
    root_text = os.fspath(root)
    if (
        os.path.normpath(root_text) != root_text
        or os.path.realpath(root_text) != root_text
        or unicodedata.normalize("NFC", root_text) != root_text
    ):
        raise VerificationError("repository root must be one canonical no-follow path")
    try:
        before = os.lstat(root_text)
    except OSError as exc:
        raise VerificationError(f"cannot stat repository root safely: {exc}") from exc
    _private_stat(before, "repository root", "directory")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(os.fspath(root), flags)
    except OSError as exc:
        raise VerificationError(f"cannot open repository root safely: {exc}") from exc
    try:
        opened = os.fstat(fd)
        _private_stat(opened, "repository root", "directory")
        after = os.lstat(root_text)
        _private_stat(after, "repository root", "directory")
        if not (_same_inode(before, opened) and _same_inode(opened, after)):
            raise VerificationError("repository root changed identity while it was opened")
    except Exception:
        os.close(fd)
        raise
    return fd


def _open_relative(root_fd: int, relative: str, expected_type: str) -> int:
    relative = _validate_relative_path(relative, "repository-relative path")
    parts = relative.split("/")
    current = os.dup(root_fd)
    try:
        for index, part in enumerate(parts):
            final = index == len(parts) - 1
            wanted = expected_type if final else "directory"
            child, _ = _open_child_reauthenticated(
                current,
                part,
                wanted,
                f"{relative} component {part}",
            )
            os.close(current)
            current = child
        return current
    except Exception:
        os.close(current)
        raise


def _read_fd(fd: int, maximum: int, label: str) -> tuple[bytes, os.stat_result]:
    before = os.fstat(fd)
    _private_stat(before, label, "regular")
    if before.st_size > maximum:
        raise VerificationError(f"{label} exceeds its byte bound")
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining:
        chunk = os.read(fd, min(1 << 20, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    raw = b"".join(chunks)
    if len(raw) > maximum:
        raise VerificationError(f"{label} exceeds its byte bound")
    after = os.fstat(fd)
    stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, field) != getattr(after, field) for field in stable_fields):
        raise VerificationError(f"{label} changed while it was read")
    if len(raw) != before.st_size:
        raise VerificationError(f"{label} produced a short read")
    return raw, before


def _secure_read(root_fd: int, relative: str, maximum: int) -> tuple[bytes, os.stat_result]:
    fd = _open_relative(root_fd, relative, "regular")
    try:
        return _read_fd(fd, maximum, relative)
    finally:
        os.close(fd)


def _validate_file_identity(
    root_fd: int,
    value: Any,
    label: str,
    maximum: int,
) -> tuple[dict[str, Any], bytes]:
    identity = _mapping(value, label)
    _exact_keys(identity, FILE_IDENTITY_KEYS, label)
    path = _validate_relative_path(identity["path"], f"{label}.path")
    expected_bytes = _integer(identity["bytes"], f"{label}.bytes")
    expected_mode = _integer(identity["mode"], f"{label}.mode")
    expected_sha = _sha256(identity["sha256"], f"{label}.sha256")
    if expected_mode > 0o7777:
        raise VerificationError(f"{label}.mode exceeds POSIX permission bits")
    raw, info = _secure_read(root_fd, path, maximum)
    if len(raw) != expected_bytes:
        raise VerificationError(f"{label} byte count mismatch")
    if stat.S_IMODE(info.st_mode) != expected_mode:
        raise VerificationError(f"{label} mode mismatch")
    if sha256_bytes(raw) != expected_sha:
        raise VerificationError(f"{label} SHA-256 mismatch")
    return identity, raw


def regular_tree_digest(entries: Iterable[tuple[str, bytes]]) -> str:
    ordered = sorted(entries, key=lambda item: item[0].encode("utf-8"))
    _validate_path_kinds(
        [(path, "file") for path, _ in ordered], "regular-file tree"
    )
    digest = hashlib.sha256()
    digest.update(TREE_DOMAIN)
    previous: str | None = None
    for path, payload in ordered:
        path = _validate_relative_path(path, "tree path")
        if path == previous:
            raise VerificationError(f"duplicate tree path {path!r}")
        previous = path
        encoded = path.encode("utf-8")
        digest.update(b"file\0")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(hashlib.sha256(payload).digest())
    return digest.hexdigest()


def _validate_path_kinds(
    entries: Sequence[tuple[str, str]],
    label: str,
) -> None:
    """Reject ambiguous case-insensitive names and file/descendant conflicts."""

    exact: dict[str, str] = {}
    folded: dict[str, str] = {}
    for raw_path, kind in entries:
        path = _validate_relative_path(raw_path, f"{label} path")
        if kind not in ("directory", "file"):
            raise VerificationError(f"{label} has invalid path kind {kind!r}")
        if path in exact:
            raise VerificationError(f"{label} contains duplicate path {path!r}")
        folded_path = path.casefold()
        prior = folded.get(folded_path)
        if prior is not None and prior != path:
            raise VerificationError(
                f"{label} contains casefold-colliding paths {prior!r} and {path!r}"
            )
        exact[path] = kind
        folded[folded_path] = path
    for path in exact:
        parts = path.split("/")
        for length in range(1, len(parts)):
            ancestor = "/".join(parts[:length])
            matched = folded.get(ancestor.casefold())
            if matched is None:
                continue
            if matched != ancestor:
                raise VerificationError(
                    f"{label} has case-inconsistent ancestor {matched!r} for {path!r}"
                )
            if exact[matched] == "file":
                raise VerificationError(
                    f"{label} uses file {matched!r} as an ancestor of {path!r}"
                )


def _archive_path(name: str, label: str) -> str:
    if name.endswith("/"):
        raise VerificationError(f"{label} has a trailing slash")
    return _validate_relative_path(name, label)


def _covered(path: str, selected: Sequence[str]) -> bool:
    return any(path == item or path.startswith(item + "/") for item in selected)


def _bounded_single_gzip_member(raw: bytes, label: str) -> bytes:
    """Return one complete bounded gzip member, rejecting every trailing byte."""

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
                    raise VerificationError(f"{label} gzip decompressor made no progress")
                pending = next_pending
        if not decompressor.eof:
            raise VerificationError(f"{label} gzip member has no authenticated EOF")
        trailing = decompressor.unused_data + raw[cursor:]
        if trailing:
            raise VerificationError(
                f"{label} has trailing bytes or a concatenated gzip member"
            )
        flushed = decompressor.flush(MAX_ARCHIVE_TAR_STREAM_BYTES - len(output) + 1)
        output.extend(flushed)
    except zlib.error as exc:
        raise VerificationError(f"{label} gzip checksum or framing is invalid") from exc
    if len(output) > MAX_ARCHIVE_TAR_STREAM_BYTES:
        raise VerificationError(f"{label} exceeds the tar-stream byte bound")
    return bytes(output)


def _validate_archive(
    root_fd: int,
    value: Any,
    index: int,
) -> dict[str, Any]:
    label = f"archives[{index}]"
    record = _mapping(value, label)
    _exact_keys(
        record,
        {
            "archive",
            "directory_count",
            "license_path",
            "member_count",
            "name",
            "regular_file_count",
            "root_directory",
            "selected_paths",
            "source",
            "tar_stream_bytes",
            "tree_sha256",
            "uncompressed_regular_bytes",
            "version",
        },
        label,
    )
    name = _string(record["name"], f"{label}.name")
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
        raise VerificationError(f"{label}.name is invalid")
    version = _string(record["version"], f"{label}.version")
    root_directory = _validate_relative_path(
        record["root_directory"], f"{label}.root_directory"
    )
    if "/" in root_directory:
        raise VerificationError(f"{label}.root_directory must have one component")
    source = _mapping(record["source"], f"{label}.source")
    _exact_keys(source, {"repository", "tag", "upstream_commit"}, f"{label}.source")
    _string(source["repository"], f"{label}.source.repository")
    tag = _string(source["tag"], f"{label}.source.tag")
    commit = _string(source["upstream_commit"], f"{label}.source.upstream_commit")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise VerificationError(f"{label}.source.upstream_commit is not a full Git SHA")
    if tag != version:
        raise VerificationError(f"{label} tag and version differ")

    selected_values = _sequence(record["selected_paths"], f"{label}.selected_paths")
    selected = [
        _validate_relative_path(item, f"{label}.selected_paths[{number}]")
        for number, item in enumerate(selected_values)
    ]
    if not selected or len(set(selected)) != len(selected):
        raise VerificationError(f"{label}.selected_paths must be nonempty and unique")
    _validate_path_kinds(
        [(path, "directory") for path in selected],
        f"{label}.selected_paths",
    )
    license_path = _validate_relative_path(record["license_path"], f"{label}.license_path")
    if license_path not in selected:
        raise VerificationError(f"{label}.license_path must be an explicit selected path")
    if not PurePosixPath(license_path).name.lower().startswith(("license", "copying")):
        raise VerificationError(f"{label}.license_path is not a license filename")

    _, raw = _validate_file_identity(
        root_fd, record["archive"], f"{label}.archive", MAX_ARCHIVE_BYTES
    )
    tar_stream = _bounded_single_gzip_member(raw, f"{label}.archive")
    if _integer(record["tar_stream_bytes"], f"{label}.tar_stream_bytes") != len(
        tar_stream
    ):
        raise VerificationError(f"{label}.tar_stream_bytes mismatch")
    try:
        archive = tarfile.open(
            fileobj=io.BytesIO(tar_stream),
            mode="r:",
            encoding="utf-8",
            errors="strict",
        )
    except (tarfile.TarError, OSError, EOFError) as exc:
        raise VerificationError(f"{label} is not a valid gzip tar archive") from exc
    member_count = 0
    seen: set[str] = set()
    file_entries: list[tuple[str, bytes]] = []
    regular_count = 0
    directory_count = 0
    regular_bytes = 0
    root_seen = False
    selected_seen: set[str] = set()
    license_seen = False
    member_kinds: list[tuple[str, str]] = []
    with archive:
        try:
            if archive.pax_headers != {"comment": commit}:
                raise VerificationError(f"{label} global PAX commit comment mismatch")
            for member_number, member in enumerate(archive):
                member_count += 1
                if member_count > MAX_ARCHIVE_MEMBERS:
                    raise VerificationError(f"{label} exceeds the member-count bound")
                member_label = f"{label}.member[{member_number}]"
                path = _archive_path(member.name, f"{member_label}.name")
                if path in seen:
                    raise VerificationError(f"{label} contains duplicate member {path!r}")
                seen.add(path)
                if member.pax_headers != {"comment": commit}:
                    raise VerificationError(f"{member_label} PAX commit comment mismatch")
                if member.sparse:
                    raise VerificationError(f"{member_label} is sparse")
                parts = path.split("/")
                if parts[0] != root_directory:
                    raise VerificationError(f"{member_label} escapes the single archive root")
                relative = "/".join(parts[1:])
                if not relative:
                    if root_seen or not member.isdir() or member.size != 0:
                        raise VerificationError(f"{label} has an invalid root member")
                    root_seen = True
                    directory_count += 1
                    member_kinds.append((path, "directory"))
                    continue
                if not _covered(relative, selected):
                    raise VerificationError(
                        f"{member_label} is outside the selected source paths"
                    )
                for selected_path in selected:
                    if relative == selected_path or relative.startswith(
                        selected_path + "/"
                    ):
                        selected_seen.add(selected_path)
                if member.isdir():
                    if member.size != 0:
                        raise VerificationError(f"{member_label} directory has payload bytes")
                    directory_count += 1
                    member_kinds.append((path, "directory"))
                    continue
                if not member.isfile():
                    raise VerificationError(
                        f"{member_label} is not a regular file or directory"
                    )
                regular_count += 1
                member_kinds.append((path, "file"))
                if member.size < 0 or member.size > MAX_ARCHIVE_MEMBER_BYTES:
                    raise VerificationError(
                        f"{member_label} exceeds the member-byte bound"
                    )
                regular_bytes += member.size
                if regular_bytes > MAX_ARCHIVE_REGULAR_BYTES:
                    raise VerificationError(f"{label} exceeds the decompressed-byte bound")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise VerificationError(f"{member_label} could not be read")
                payload = extracted.read(MAX_ARCHIVE_MEMBER_BYTES + 1)
                if len(payload) != member.size:
                    raise VerificationError(
                        f"{member_label} size differs from its tar header"
                    )
                file_entries.append((relative, payload))
                if relative == license_path:
                    license_seen = True
        except (tarfile.TarError, OSError, EOFError) as exc:
            raise VerificationError(f"{label} tar stream is invalid") from exc

        tar_end = archive.offset

    if (
        tar_end + 1_024 > len(tar_stream)
        or tar_stream[tar_end : tar_end + 1_024] != bytes(1_024)
        or any(tar_stream[tar_end + 1_024 :])
    ):
        raise VerificationError(
            f"{label} lacks a canonical tar EOF or has nonzero trailing tar data"
        )

    if not root_seen:
        raise VerificationError(f"{label} has no explicit root directory member")
    _validate_path_kinds(member_kinds, f"{label} members")
    if selected_seen != set(selected):
        raise VerificationError(
            f"{label} does not contain every selected path: "
            f"{sorted(set(selected) - selected_seen)}"
        )
    if not license_seen:
        raise VerificationError(f"{label} has no regular selected license file")
    exact_numbers = {
        "member_count": member_count,
        "regular_file_count": regular_count,
        "directory_count": directory_count,
        "uncompressed_regular_bytes": regular_bytes,
    }
    for key, actual in exact_numbers.items():
        expected = _integer(record[key], f"{label}.{key}")
        if expected != actual:
            raise VerificationError(f"{label}.{key} mismatch")
    expected_tree = _sha256(record["tree_sha256"], f"{label}.tree_sha256")
    if regular_tree_digest(file_entries) != expected_tree:
        raise VerificationError(f"{label}.tree_sha256 mismatch")
    return {
        "archive_sha256": _mapping(record["archive"], f"{label}.archive")["sha256"],
        "name": name,
        "regular_file_count": regular_count,
        "tree_sha256": expected_tree,
    }


def _read_tree(
    root_fd: int,
    relative_root: str,
) -> tuple[list[tuple[str, bytes]], int]:
    relative_root = _validate_relative_path(relative_root, "tree root")
    directory_fd = _open_relative(root_fd, relative_root, "directory")
    entries: list[tuple[str, bytes]] = []
    total_bytes = 0

    def visit(fd: int, prefix: str) -> None:
        nonlocal total_bytes
        try:
            names = sorted(os.listdir(fd), key=lambda item: item.encode("utf-8"))
        except OSError as exc:
            raise VerificationError(f"cannot enumerate tree {relative_root!r}: {exc}") from exc
        for name in names:
            _validate_relative_path(name, "tree entry name")
            path = name if not prefix else f"{prefix}/{name}"
            try:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            except OSError as exc:
                raise VerificationError(f"cannot stat tree entry {path!r}: {exc}") from exc
            if stat.S_ISDIR(info.st_mode):
                child, _ = _open_child_reauthenticated(
                    fd, name, "directory", f"tree directory {path}"
                )
                try:
                    visit(child, path)
                finally:
                    os.close(child)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise VerificationError(f"tree entry {path!r} is not a regular file")
            if len(entries) >= MAX_TREE_FILES:
                raise VerificationError("tree exceeds the regular-file-count bound")
            child, opened = _open_child_reauthenticated(
                fd, name, "regular", f"tree file {path}"
            )
            try:
                payload, stable = _read_fd(child, MAX_TREE_FILE_BYTES, path)
            finally:
                os.close(child)
            if not _same_inode(opened, stable):
                raise VerificationError(f"tree file {path!r} was substituted")
            total_bytes += len(payload)
            if total_bytes > MAX_TREE_REGULAR_BYTES:
                raise VerificationError("tree exceeds the regular-byte bound")
            entries.append((path, payload))

    try:
        visit(directory_fd, "")
    finally:
        os.close(directory_fd)
    return entries, total_bytes


def _strip_cmake_comments(text: str) -> str:
    output: list[str] = []
    quoted = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if quoted:
            output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            index += 1
            continue
        if char == '"':
            quoted = True
            output.append(char)
            index += 1
            continue
        if char == "#":
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        output.append(char)
        index += 1
    if quoted:
        raise VerificationError("CMakeLists.txt has an unterminated quote")
    return "".join(output)


def _cmake_tokens(body: str) -> list[str]:
    tokens: list[str] = []
    current: list[str] = []
    quoted = False
    escaped = False
    for char in body:
        if quoted:
            if escaped:
                current.append(char)
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            else:
                current.append(char)
            continue
        if char == '"':
            quoted = True
        elif char.isspace():
            if current:
                tokens.append("".join(current))
                current = []
        else:
            current.append(char)
    if quoted or escaped:
        raise VerificationError("CMake call has an unterminated escape or quote")
    if current:
        tokens.append("".join(current))
    return tokens


def _cmake_calls(text: str) -> list[tuple[str, list[str]]]:
    text = _strip_cmake_comments(text)
    calls: list[tuple[str, list[str]]] = []
    pattern = re.compile(r"(?i)(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*\(")
    cursor = 0
    while True:
        match = pattern.search(text, cursor)
        if match is None:
            break
        depth = 1
        quoted = False
        escaped = False
        index = match.end()
        body_start = index
        while index < len(text) and depth:
            char = text[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            index += 1
        if depth:
            raise VerificationError(f"unterminated CMake call {match.group(1)!r}")
        calls.append((match.group(1).lower(), _cmake_tokens(text[body_start : index - 1])))
        cursor = index
    return calls


def _one_set(calls: Sequence[tuple[str, list[str]]], variable: str) -> list[str]:
    matches = [tokens for name, tokens in calls if name == "set" and tokens and tokens[0] == variable]
    if len(matches) != 1:
        raise VerificationError(f"CMake must set {variable} exactly once")
    return matches[0]


def _validate_cmake(root_fd: int, value: Any, archives: Sequence[dict[str, Any]]) -> None:
    record = _mapping(value, "cmake")
    _exact_keys(
        record,
        {
            "direct_source_override_guards",
            "fetchcontent_declarations",
            "file",
            "forbidden_tokens",
            "make_available",
            "runtime_artifact_root",
        },
        "cmake",
    )
    _, raw = _validate_file_identity(root_fd, record["file"], "cmake.file", MAX_CMAKE_BYTES)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VerificationError("CMakeLists.txt is not UTF-8") from exc
    uncommented = _strip_cmake_comments(text)
    forbidden = _sequence(record["forbidden_tokens"], "cmake.forbidden_tokens")
    if forbidden != ["GIT_REPOSITORY", "http://", "https://"]:
        raise VerificationError("cmake.forbidden_tokens differs from the v1 policy")
    if re.search(r"(?i)https?://", uncommented):
        raise VerificationError("CMakeLists.txt contains a network URL")
    if re.search(r"(?i)(?<![A-Za-z0-9_])GIT_REPOSITORY(?![A-Za-z0-9_])", uncommented):
        raise VerificationError("CMakeLists.txt contains GIT_REPOSITORY")
    calls = _cmake_calls(text)
    declarations = [tokens for name, tokens in calls if name == "fetchcontent_declare"]
    make_available = [
        tokens for name, tokens in calls if name == "fetchcontent_makeavailable"
    ]
    expected_make_available = _sequence(record["make_available"], "cmake.make_available")
    if expected_make_available != ["manif", "raylib"]:
        raise VerificationError("cmake.make_available differs from the v1 policy")
    if make_available != [["manif"], ["raylib"]]:
        raise VerificationError(
            "CMake must make exactly manif and raylib available in registered order"
        )
    override_guards = _sequence(
        record["direct_source_override_guards"],
        "cmake.direct_source_override_guards",
    )
    if override_guards != ["MANIF", "RAYLIB"]:
        raise VerificationError(
            "cmake.direct_source_override_guards differs from the v1 policy"
        )
    guard_pattern = re.compile(
        r"foreach\s*\(\s*DANCERS_DEPENDENCY\s+IN\s+ITEMS\s+MANIF\s+RAYLIB\s*\)"
        r"\s*if\s*\(\s*DEFINED\s+FETCHCONTENT_SOURCE_DIR_\$\{DANCERS_DEPENDENCY\}"
        r"\s+AND\s+NOT\s+\"\$\{FETCHCONTENT_SOURCE_DIR_\$\{DANCERS_DEPENDENCY\}\}\""
        r"\s+STREQUAL\s+\"\"\s*\)"
        r"\s*message\s*\(\s*FATAL_ERROR\s+"
        r"\"FETCHCONTENT_SOURCE_DIR_\$\{DANCERS_DEPENDENCY\} overrides are forbidden\"\s*\)"
        r"\s*endif\s*\(\s*\)\s*endforeach\s*\(\s*\)",
        re.IGNORECASE,
    )
    if len(guard_pattern.findall(uncommented)) != 1:
        raise VerificationError(
            "CMake lacks the exact MANIF/RAYLIB direct-source override guards"
        )
    expected_values = _sequence(
        record["fetchcontent_declarations"], "cmake.fetchcontent_declarations"
    )
    if len(expected_values) != len(archives) or len(declarations) != len(archives):
        raise VerificationError("CMake FetchContent declaration count mismatch")
    by_name = {archive["name"]: archive for archive in archives}
    seen: set[str] = set()
    for index, raw_expected in enumerate(expected_values):
        expected = _mapping(raw_expected, f"cmake.fetchcontent_declarations[{index}]")
        _exact_keys(
            expected,
            {"archive_variable", "local_path", "name", "sha256"},
            f"cmake.fetchcontent_declarations[{index}]",
        )
        name = _string(expected["name"], f"cmake.fetchcontent_declarations[{index}].name")
        variable = _string(
            expected["archive_variable"],
            f"cmake.fetchcontent_declarations[{index}].archive_variable",
        )
        local_path = _validate_relative_path(
            expected["local_path"], f"cmake.fetchcontent_declarations[{index}].local_path"
        )
        digest = _sha256(
            expected["sha256"], f"cmake.fetchcontent_declarations[{index}].sha256"
        )
        if name not in by_name or name in seen:
            raise VerificationError("CMake FetchContent names differ from archive names")
        seen.add(name)
        archive_identity = _mapping(by_name[name]["archive"], f"archive {name}")
        if archive_identity["path"] != local_path or archive_identity["sha256"] != digest:
            raise VerificationError(f"CMake identity for {name} differs from its archive")
        set_tokens = _one_set(calls, variable)
        expected_url = f"${{CMAKE_CURRENT_SOURCE_DIR}}/{local_path}"
        if set_tokens != [variable, expected_url]:
            raise VerificationError(f"CMake {variable} is not the exact local archive path")
        matching = [tokens for tokens in declarations if tokens and tokens[0].lower() == name]
        exact_declaration = [
            name,
            "URL",
            f"${{{variable}}}",
            "URL_HASH",
            f"SHA256={digest}",
            "DOWNLOAD_EXTRACT_TIMESTAMP",
            "TRUE",
        ]
        if matching != [exact_declaration]:
            raise VerificationError(f"CMake FetchContent_Declare({name}) is not exact")
    runtime = _mapping(record["runtime_artifact_root"], "cmake.runtime_artifact_root")
    _exact_keys(runtime, {"cache_type", "default", "variable"}, "cmake.runtime_artifact_root")
    runtime_variable = _string(runtime["variable"], "cmake.runtime_artifact_root.variable")
    runtime_default = _string(runtime["default"], "cmake.runtime_artifact_root.default")
    runtime_type = _string(runtime["cache_type"], "cmake.runtime_artifact_root.cache_type")
    runtime_tokens = _one_set(calls, runtime_variable)
    if len(runtime_tokens) != 5 or runtime_tokens[:4] != [
        runtime_variable,
        runtime_default,
        "CACHE",
        runtime_type,
    ]:
        raise VerificationError("CMake runtime artifact root is not an explicit cache PATH")
    realpath_call = [
        tokens
        for name, tokens in calls
        if name == "get_filename_component" and tokens and tokens[0] == "GBP_PROJECT_ROOT"
    ]
    if realpath_call != [["GBP_PROJECT_ROOT", f"${{{runtime_variable}}}", "REALPATH"]]:
        raise VerificationError("CMake does not canonicalize the runtime artifact root exactly")


def _validate_eigen(root_fd: int, value: Any) -> dict[str, Any]:
    record = _mapping(value, "vendored_eigen")
    _exact_keys(
        record,
        {
            "config",
            "license_file_present",
            "license_path",
            "provenance_complete",
            "provenance_blocker",
            "root",
            "tree_regular_bytes",
            "tree_regular_file_count",
            "tree_sha256",
            "version",
        },
        "vendored_eigen",
    )
    root = _validate_relative_path(record["root"], "vendored_eigen.root")
    if _string(record["version"], "vendored_eigen.version") != "3.4.0":
        raise VerificationError("vendored_eigen.version must be 3.4.0")
    if _boolean(record["license_file_present"], "vendored_eigen.license_file_present"):
        raise VerificationError("vendored_eigen.license_file_present must remain false in v1")
    if record["license_path"] is not None:
        raise VerificationError("vendored_eigen.license_path must be null while absent")
    if _boolean(record["provenance_complete"], "vendored_eigen.provenance_complete"):
        raise VerificationError("vendored_eigen.provenance_complete must remain false in v1")
    blocker = _string(record["provenance_blocker"], "vendored_eigen.provenance_blocker")
    if not blocker.strip() or blocker != blocker.strip():
        raise VerificationError("vendored_eigen.provenance_blocker is invalid")
    _validate_file_identity(root_fd, record["config"], "vendored_eigen.config", MAX_CMAKE_BYTES)
    entries, total_bytes = _read_tree(root_fd, root)
    if _integer(record["tree_regular_file_count"], "vendored_eigen.tree_regular_file_count") != len(entries):
        raise VerificationError("vendored_eigen.tree_regular_file_count mismatch")
    if _integer(record["tree_regular_bytes"], "vendored_eigen.tree_regular_bytes") != total_bytes:
        raise VerificationError("vendored_eigen.tree_regular_bytes mismatch")
    expected_tree = _sha256(record["tree_sha256"], "vendored_eigen.tree_sha256")
    if regular_tree_digest(entries) != expected_tree:
        raise VerificationError("vendored_eigen.tree_sha256 mismatch")
    license_names = [
        path
        for path, _ in entries
        if PurePosixPath(path).name.lower().startswith(("license", "copying"))
    ]
    if license_names:
        raise VerificationError(
            "vendored Eigen now contains a license candidate but the v1 manifest says absent"
        )
    return {
        "license_file_present": False,
        "provenance_complete": False,
        "tree_sha256": expected_tree,
    }


def verify_repository(
    repository_root: Path,
    manifest_relative_path: str = DEFAULT_MANIFEST,
    expected_manifest_sha256: str | None = None,
) -> dict[str, Any]:
    """Verify offline build inputs without executing any build or test process."""

    expected = expected_manifest_sha256 or EXPECTED_MANIFEST_SHA256
    _sha256(expected, "expected manifest SHA-256")
    root_fd = _open_root(repository_root)
    try:
        manifest_path = _validate_relative_path(manifest_relative_path, "manifest path")
        raw, _ = _secure_read(root_fd, manifest_path, MAX_MANIFEST_BYTES)
        manifest_sha = sha256_bytes(raw)
        if manifest_sha != expected:
            raise VerificationError("manifest SHA-256 differs from the independent expectation")
        manifest = _mapping(_parse_json(raw, "offline input manifest"), "manifest")
        _exact_keys(manifest, TOP_KEYS, "manifest")
        if manifest["schema_version"] != SCHEMA_VERSION:
            raise VerificationError("manifest schema_version mismatch")
        limits = _mapping(manifest["limits"], "limits")
        if limits != LIMITS:
            raise VerificationError("manifest limits differ from verifier constants")
        tree_digest = _mapping(manifest["tree_digest"], "tree_digest")
        _exact_keys(tree_digest, {"algorithm", "domain", "entry_encoding"}, "tree_digest")
        if tree_digest != {
            "algorithm": "sha256",
            "domain": TREE_DOMAIN[:-1].decode("ascii"),
            "entry_encoding": TREE_ENCODING,
        }:
            raise VerificationError("manifest tree-digest contract mismatch")
        classification = _mapping(manifest["classification"], "classification")
        _exact_keys(
            classification,
            {
                "execution_eligible",
                "offline_build_inputs_authenticated",
                "provenance_complete",
                "scientific_execution_authorized",
            },
            "classification",
        )
        if classification != {
            "execution_eligible": False,
            "offline_build_inputs_authenticated": True,
            "provenance_complete": False,
            "scientific_execution_authorized": False,
        }:
            raise VerificationError("manifest classification is not the locked v1 state")
        archive_values = _sequence(manifest["archives"], "archives")
        if len(archive_values) != 2:
            raise VerificationError("manifest must contain exactly two archives")
        archive_results = [
            _validate_archive(root_fd, value, index)
            for index, value in enumerate(archive_values)
        ]
        names = [result["name"] for result in archive_results]
        if names != ["manif", "raylib"]:
            raise VerificationError("archive order must be exactly manif, raylib")
        _validate_cmake(root_fd, manifest["cmake"], archive_values)
        eigen = _validate_eigen(root_fd, manifest["vendored_eigen"])
        return {
            "execution_eligible": False,
            "manifest_sha256": manifest_sha,
            "offline_build_inputs_authenticated": True,
            "provenance_complete": False,
            "vendored_eigen_license_file_present": eigen["license_file_present"],
        }
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
    parser.add_argument(
        "--manifest",
        default=DEFAULT_MANIFEST,
        help="repository-relative canonical manifest path",
    )
    parser.add_argument(
        "--expected-manifest-sha256",
        default=EXPECTED_MANIFEST_SHA256,
        help="independently authenticated expected manifest SHA-256",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = verify_repository(
            args.repository_root,
            args.manifest,
            args.expected_manifest_sha256,
        )
    except (OSError, VerificationError) as exc:
        print(f"offline-build-inputs-v1: reject: {exc}", file=sys.stderr)
        return 1
    print(canonical_json_bytes(result).decode("utf-8"), end="")
    # Authentication succeeded, but v1 deliberately records an unresolved Eigen
    # license/provenance blocker and therefore must not be used to unlock execution.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
