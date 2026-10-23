#!/usr/bin/env python3
"""Probe whether this host can satisfy the frozen source-capsule metadata policy.

The probe is deliberately smaller than a source-root materializer.  It reads
the exact committed source-policy artifacts, creates only one private
directory and one small file below a caller-supplied temporary parent,
measures their metadata, and removes both before returning.  It never creates
common/reference/optimized source roots or a root manifest, and it never
configures, builds, tests, opens a network connection, or runs the simulator.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import sys
import tempfile
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
import unicodedata


SCHEMA_VERSION = "dancers-planar-physics-source-capsule-host-verdict-v2"
EPOCH_ID = "simulator-demo-v1-physics-stress-test-epoch-1"
PARENT_PREFIX = "dancers-physics-diagnostic-parent-v2-"
PROBE_PREFIX = ".dancers-physics-source-capability-v2-"
PROBE_FILE = "probe.bin"
PROBE_BYTES = b"dancers planar physics source metadata probe v2\n"

MAX_PATH_BYTES = 1_024
MAX_TRUSTED_FILE_BYTES = 2 << 20
MAX_JSON_BYTES = 2 << 20
MAX_JSON_DEPTH = 24
MAX_JSON_NODES = 20_000
MAX_STRING_BYTES = 65_536
MAX_PARENT_ENTRIES = 4
MAX_XATTR_LIST_BYTES = 65_536
MAX_XATTR_COUNT = 256
MAX_XATTR_VALUE_BYTES = 65_536
MAX_XATTR_TOTAL_BYTES = 1 << 20
READ_CHUNK_BYTES = 65_536

CLASSIFICATION = {
    "build_authorized": False,
    "build_capture_complete": False,
    "build_executed": False,
    "capture_complete": False,
    "execution_authorized": False,
    "official_namespace_materialized": False,
    "production_source_roots_materialized": False,
    "promotion_eligible": False,
    "provenance_complete": False,
    "registered_scientific_run": False,
    "scientific_evidence": False,
    "scientific_evidence_eligible": False,
    "scientific_execution_authorized": False,
}

TRUSTED_FILES = {
    "docs/simulator/physics_protected_source_roots_contract_v2.json": {
        "role": "protected-source-roots-contract",
        "bytes": 19_728,
        "sha256": "21fb7fde7dc8b44eba46d4223e9c441f38888b6dc427ff1a7b3993eab92675a6",
    },
    "scripts/comparison/generate_physics_source_indexes_v2.py": {
        "role": "source-index-generator",
        "bytes": 33_990,
        "sha256": "16edd827f0ba93d2c6e9699cfd4e4df8466229221172b9a55fa161a87485b77c",
    },
    "scripts/comparison/verify_physics_source_indexes_v2.py": {
        "role": "source-index-verifier",
        "bytes": 18_446,
        "sha256": "09cef6043ecf6b72e2fb2e373f8826581a9f784e1509cacfb5fcdd3359bf7546",
    },
    "comparison/physics-superbuild-v1/source-indexes/common-source-v2.json": {
        "role": "common-source-index",
        "bytes": 100_897,
        "sha256": "1270fc7975691abf302627a96e6b027da0b8306790e7090f89219877eed76f19",
    },
    "comparison/physics-superbuild-v1/source-indexes/optimized-source-v2.json": {
        "role": "optimized-source-index",
        "bytes": 14_237,
        "sha256": "f27bc056aa6face5fe0c72be3e2adf6a59d46f775214c60b7031a006a9957a33",
    },
    "comparison/physics-superbuild-v1/source-indexes/reference-source-v2.json": {
        "role": "reference-source-index",
        "bytes": 14_236,
        "sha256": "f786fbc410a904f3f71bb014dc018969073b7aa13a1d5472d299172d2e1da910",
    },
    "comparison/physics-superbuild-v1/source-indexes/source-input-handoff-v2.json": {
        "role": "source-input-handoff",
        "bytes": 1_226,
        "sha256": "a3f9b676198fba7e471f2a6500cbb94cf2e8f8c804b560b520bbf2c4d0112b4f",
    },
}


class HostProbeError(ValueError):
    """The requested host probe or one of its trusted inputs is invalid."""


class HostProbeCleanupError(HostProbeError):
    """The probe could not prove that every created name was removed."""


def _required_open_flags(directory: bool) -> int:
    for name in ("O_CLOEXEC", "O_NOFOLLOW"):
        if not hasattr(os, name):
            raise HostProbeError("required no-follow descriptor flags are unavailable")
    if directory and not hasattr(os, "O_DIRECTORY"):
        raise HostProbeError("required directory descriptor flag is unavailable")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    if directory:
        flags |= os.O_DIRECTORY
    return flags


def _node_identity(info: os.stat_result) -> Tuple[int, int]:
    return info.st_dev, info.st_ino


def _stable_directory_identity(info: os.stat_result) -> Tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_uid,
        info.st_gid,
        getattr(info, "st_flags", 0),
    )


def _stable_regular_identity(info: os.stat_result) -> Tuple[int, ...]:
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
        getattr(info, "st_flags", 0),
    )


def _canonical_absolute(path: Path, label: str) -> Path:
    raw = os.fspath(path)
    if type(raw) is not str or not raw or not os.path.isabs(raw):
        raise HostProbeError("%s must be an absolute path" % label)
    if (len(raw.encode("utf-8")) > MAX_PATH_BYTES or raw.startswith("//")
            or "\0" in raw or "\\" in raw
            or unicodedata.normalize("NFC", raw) != raw):
        raise HostProbeError("%s is not a bounded NFC path" % label)
    pure = PurePosixPath(raw)
    if pure.as_posix() != raw or os.path.normpath(raw) != raw:
        raise HostProbeError("%s is not a canonical absolute path" % label)
    if any(part in {"", ".", ".."} for part in pure.parts[1:]):
        raise HostProbeError("%s contains a forbidden path component" % label)
    return Path(raw)


class _OpenedAbsolute:
    def __init__(
        self,
        path: Path,
        descriptor: int,
        ancestry: List[Tuple[int, str, Tuple[int, int]]],
    ) -> None:
        self.path = path
        self.descriptor = descriptor
        self.ancestry = ancestry

    def reauthenticate(self) -> None:
        for parent_fd, component, expected in self.ancestry:
            try:
                named = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            except OSError as error:
                raise HostProbeError("absolute path ancestry disappeared") from error
            if _node_identity(named) != expected:
                raise HostProbeError("absolute path ancestry changed")
        try:
            named_final = os.stat(os.fspath(self.path), follow_symlinks=False)
        except OSError as error:
            raise HostProbeError("absolute final name disappeared") from error
        if _node_identity(named_final) != _node_identity(os.fstat(self.descriptor)):
            raise HostProbeError("absolute final name changed")

    def close(self) -> None:
        for parent_fd, _, _ in self.ancestry:
            os.close(parent_fd)
        self.ancestry = []
        if self.descriptor >= 0:
            os.close(self.descriptor)
            self.descriptor = -1


def _open_absolute(path: Path, label: str, directory: bool) -> _OpenedAbsolute:
    canonical = _canonical_absolute(path, label)
    components = canonical.parts[1:]
    current = os.open("/", _required_open_flags(True))
    ancestry = []  # type: List[Tuple[int, str, Tuple[int, int]]]
    try:
        for index, component in enumerate(components):
            final = index == len(components) - 1
            before = os.stat(component, dir_fd=current, follow_symlinks=False)
            flags = _required_open_flags(directory if final else True)
            if final and not directory:
                flags |= getattr(os, "O_NONBLOCK", 0)
            child = os.open(component, flags, dir_fd=current)
            opened = os.fstat(child)
            after = os.stat(component, dir_fd=current, follow_symlinks=False)
            if (_node_identity(before) != _node_identity(opened)
                    or _node_identity(opened) != _node_identity(after)):
                os.close(child)
                raise HostProbeError("%s path component changed while opened" % label)
            if (not final or directory) and not stat.S_ISDIR(opened.st_mode):
                os.close(child)
                raise HostProbeError("%s path component is not a directory" % label)
            ancestry.append((os.dup(current), component, _node_identity(opened)))
            os.close(current)
            current = child
        opened_path = _OpenedAbsolute(canonical, current, ancestry)
        opened_path.reauthenticate()
        return opened_path
    except Exception:
        for parent_fd, _, _ in ancestry:
            os.close(parent_fd)
        os.close(current)
        raise


def _bounded_names(descriptor: int, maximum: int, label: str) -> List[str]:
    names = []  # type: List[str]
    try:
        iterator = os.scandir(descriptor)
        try:
            for item in iterator:
                if len(names) >= maximum:
                    raise HostProbeError("%s inventory exceeds its fixed bound" % label)
                name = item.name
                if (not name or name in {".", ".."} or "/" in name or "\0" in name
                        or unicodedata.normalize("NFC", name) != name):
                    raise HostProbeError("%s contains a noncanonical name" % label)
                names.append(name)
        finally:
            iterator.close()
    except OSError as error:
        raise HostProbeError("cannot enumerate %s" % label) from error
    if len(names) != len(set(names)) or len({item.casefold() for item in names}) != len(names):
        raise HostProbeError("%s contains duplicate or case-fold-colliding names" % label)
    return sorted(names, key=lambda value: value.encode("utf-8"))


def _read_exact_file(path: Path, expected_bytes: int, expected_sha256: str, label: str) -> None:
    if (type(expected_bytes) is not int or not 0 <= expected_bytes <= MAX_TRUSTED_FILE_BYTES
            or type(expected_sha256) is not str or len(expected_sha256) != 64):
        raise HostProbeError("%s expected identity is invalid" % label)
    opened = _open_absolute(path, label, directory=False)
    try:
        before = os.fstat(opened.descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid() or before.st_mode & 0o022
                or before.st_size != expected_bytes):
            raise HostProbeError("%s type, owner, links, mode, or size differs" % label)
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            chunk = os.read(opened.descriptor, min(remaining, READ_CHUNK_BYTES))
            if not chunk:
                raise HostProbeError("%s ended early" % label)
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(opened.descriptor, 1):
            raise HostProbeError("%s grew while read" % label)
        after = os.fstat(opened.descriptor)
        opened.reauthenticate()
        if _stable_regular_identity(before) != _stable_regular_identity(after):
            raise HostProbeError("%s changed while read" % label)
        if digest.hexdigest() != expected_sha256:
            raise HostProbeError("%s SHA-256 differs" % label)
    finally:
        opened.close()


def _authenticate_policy_files(repository: Path) -> Dict[str, Any]:
    records = []  # type: List[Dict[str, Any]]
    for relative_path in sorted(TRUSTED_FILES, key=lambda value: value.encode("utf-8")):
        identity = TRUSTED_FILES[relative_path]
        _read_exact_file(
            repository / relative_path,
            identity["bytes"],
            identity["sha256"],
            identity["role"],
        )
        records.append({
            "path": relative_path,
            "role": identity["role"],
            "bytes": identity["bytes"],
            "sha256": identity["sha256"],
        })
    return {
        "protected_source_policy_authenticated": True,
        "source_index_artifacts_authenticated": True,
        "source_input_handoff_sha256": (
            "fdc48721d71547ba0455ec44b950444583b3b178836a0658714d88c0aa82630b"
        ),
        "files": records,
    }


_LIBC = None  # type: Optional[ctypes.CDLL]


def _libc() -> ctypes.CDLL:
    global _LIBC
    if _LIBC is None:
        name = ctypes.util.find_library("c")
        if not name:
            raise HostProbeError("platform C library is unavailable")
        _LIBC = ctypes.CDLL(name, use_errno=True)
    return _LIBC


def _xattrs(descriptor: int, label: str) -> List[Dict[str, Any]]:
    library = _libc()
    if not hasattr(library, "flistxattr") or not hasattr(library, "fgetxattr"):
        raise HostProbeError("descriptor xattr APIs are unavailable")
    list_function = library.flistxattr
    get_function = library.fgetxattr
    list_function.restype = ctypes.c_ssize_t
    get_function.restype = ctypes.c_ssize_t
    if sys.platform == "darwin":
        list_function.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
        get_function.argtypes = [
            ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t,
            ctypes.c_uint32, ctypes.c_int,
        ]
        list_arguments = lambda buffer, size: (descriptor, buffer, size, 0)
        get_arguments = lambda name, buffer, size: (descriptor, name, buffer, size, 0, 0)
    else:
        list_function.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]
        get_function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t]
        list_arguments = lambda buffer, size: (descriptor, buffer, size)
        get_arguments = lambda name, buffer, size: (descriptor, name, buffer, size)

    ctypes.set_errno(0)
    name_bytes = list_function(*list_arguments(None, 0))
    if name_bytes < 0:
        raise HostProbeError("cannot enumerate %s xattrs: errno %d" % (label, ctypes.get_errno()))
    if name_bytes > MAX_XATTR_LIST_BYTES:
        raise HostProbeError("%s xattr list exceeds its fixed bound" % label)
    if name_bytes == 0:
        return []
    name_buffer = ctypes.create_string_buffer(name_bytes)
    ctypes.set_errno(0)
    copied = list_function(*list_arguments(name_buffer, name_bytes))
    if copied != name_bytes:
        raise HostProbeError("%s xattr list changed while read" % label)
    raw_names = [item for item in name_buffer.raw[:copied].split(b"\0") if item]
    if not raw_names or len(raw_names) > MAX_XATTR_COUNT or len(raw_names) != len(set(raw_names)):
        raise HostProbeError("%s xattr-name inventory is invalid" % label)

    records = []  # type: List[Dict[str, Any]]
    aggregate = 0
    for raw_name in sorted(raw_names):
        try:
            name = raw_name.decode("utf-8")
        except UnicodeDecodeError as error:
            raise HostProbeError("%s has a non-UTF-8 xattr name" % label) from error
        if (not name or unicodedata.normalize("NFC", name) != name
                or len(raw_name) > MAX_STRING_BYTES):
            raise HostProbeError("%s has a noncanonical xattr name" % label)
        ctypes.set_errno(0)
        value_bytes = get_function(*get_arguments(raw_name, None, 0))
        if value_bytes < 0:
            raise HostProbeError("cannot size %s xattr %s" % (label, name))
        if value_bytes > MAX_XATTR_VALUE_BYTES:
            raise HostProbeError("%s xattr value exceeds its fixed bound" % label)
        aggregate += value_bytes
        if aggregate > MAX_XATTR_TOTAL_BYTES:
            raise HostProbeError("%s xattr values exceed their aggregate bound" % label)
        value_buffer = ctypes.create_string_buffer(value_bytes) if value_bytes else None
        ctypes.set_errno(0)
        copied_value = get_function(*get_arguments(raw_name, value_buffer, value_bytes))
        if copied_value != value_bytes:
            raise HostProbeError("%s xattr value changed while read" % label)
        raw_value = b"" if value_buffer is None else value_buffer.raw[:copied_value]
        records.append({
            "name": name,
            "bytes": len(raw_value),
            "sha256": hashlib.sha256(raw_value).hexdigest(),
        })
    ctypes.set_errno(0)
    final_name_bytes = list_function(*list_arguments(None, 0))
    if final_name_bytes != name_bytes:
        raise HostProbeError("%s xattr inventory changed while read" % label)
    final_buffer = ctypes.create_string_buffer(final_name_bytes)
    ctypes.set_errno(0)
    final_copied = list_function(*list_arguments(final_buffer, final_name_bytes))
    final_names = [item for item in final_buffer.raw[:final_copied].split(b"\0") if item]
    if final_copied != final_name_bytes or final_names != raw_names:
        raise HostProbeError("%s xattr inventory changed while read" % label)
    return records


def _acl_present(descriptor: int, label: str) -> bool:
    if sys.platform != "darwin":
        # On Linux, POSIX ACLs are represented by system.posix_acl_* xattrs;
        # the mandatory xattr inventory therefore covers the local surface.
        return False
    library = _libc()
    for name in ("acl_get_fd_np", "acl_get_entry", "acl_free"):
        if not hasattr(library, name):
            raise HostProbeError("descriptor ACL API is unavailable")
    get_acl = library.acl_get_fd_np
    get_acl.argtypes = [ctypes.c_int, ctypes.c_int]
    get_acl.restype = ctypes.c_void_p
    get_entry = library.acl_get_entry
    get_entry.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
    get_entry.restype = ctypes.c_int
    free_acl = library.acl_free
    free_acl.argtypes = [ctypes.c_void_p]
    free_acl.restype = ctypes.c_int
    ctypes.set_errno(0)
    acl = get_acl(descriptor, 0x00000100)  # ACL_TYPE_EXTENDED
    if not acl:
        error = ctypes.get_errno()
        if error == errno.ENOENT:
            return False
        raise HostProbeError("cannot inspect %s ACL: errno %d" % (label, error))
    try:
        entry = ctypes.c_void_p()
        ctypes.set_errno(0)
        result = get_entry(acl, 0, ctypes.byref(entry))  # ACL_FIRST_ENTRY
        if result == 0:
            return True
        raise HostProbeError("cannot enumerate %s ACL: errno %d" % (label, ctypes.get_errno()))
    finally:
        free_acl(acl)


def _observe_metadata(descriptor: int, node_type: str) -> Dict[str, Any]:
    before = os.fstat(descriptor)
    observation = {
        "node_type": node_type,
        "mode": stat.S_IMODE(before.st_mode),
        "owner_uid": before.st_uid,
        "xattrs": _xattrs(descriptor, node_type),
        "acl_present": _acl_present(descriptor, node_type),
        "bsd_flags": getattr(before, "st_flags", 0),
    }
    after = os.fstat(descriptor)
    before_identity = (
        before.st_dev, before.st_ino, before.st_mode, before.st_nlink,
        before.st_uid, before.st_gid, before.st_size, before.st_mtime_ns,
        before.st_ctime_ns, getattr(before, "st_flags", 0),
    )
    after_identity = (
        after.st_dev, after.st_ino, after.st_mode, after.st_nlink,
        after.st_uid, after.st_gid, after.st_size, after.st_mtime_ns,
        after.st_ctime_ns, getattr(after, "st_flags", 0),
    )
    if before_identity != after_identity:
        raise HostProbeError("%s changed while metadata was inspected" % node_type)
    return observation


def _open_child_directory(parent_fd: int, name: str, expected: Tuple[int, int]) -> int:
    descriptor = os.open(name, _required_open_flags(True), dir_fd=parent_fd)
    opened = os.fstat(descriptor)
    named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if (not stat.S_ISDIR(opened.st_mode) or _node_identity(opened) != expected
            or _node_identity(named) != expected):
        os.close(descriptor)
        raise HostProbeError("created probe directory identity differs")
    return descriptor


def _cleanup_created_probe(
    parent_fd: int,
    probe_name: str,
    probe_identity: Optional[Tuple[int, int]],
    probe_fd: Optional[int],
    leaf_identity: Optional[Tuple[int, int]],
) -> None:
    residual = probe_name
    descriptor = probe_fd
    try:
        if probe_identity is None:
            try:
                recovered = os.stat(probe_name, dir_fd=parent_fd, follow_symlinks=False)
            except OSError as error:
                raise HostProbeCleanupError("cleanup residual: %s" % residual) from error
            if (not stat.S_ISDIR(recovered.st_mode) or recovered.st_uid != os.geteuid()
                    or stat.S_IMODE(recovered.st_mode) != 0o700):
                raise HostProbeCleanupError("cleanup residual: %s" % residual)
            probe_identity = _node_identity(recovered)
        if descriptor is None:
            descriptor = _open_child_directory(parent_fd, probe_name, probe_identity)
        if leaf_identity is not None:
            try:
                named_leaf = os.stat(PROBE_FILE, dir_fd=descriptor, follow_symlinks=False)
            except OSError as error:
                raise HostProbeCleanupError("cleanup residual: %s" % residual) from error
            if _node_identity(named_leaf) != leaf_identity or not stat.S_ISREG(named_leaf.st_mode):
                raise HostProbeCleanupError("cleanup residual: %s" % residual)
            os.unlink(PROBE_FILE, dir_fd=descriptor)
            os.fsync(descriptor)
        if _bounded_names(descriptor, 1, "probe cleanup directory"):
            raise HostProbeCleanupError("cleanup residual: %s" % residual)
        named_probe = os.stat(probe_name, dir_fd=parent_fd, follow_symlinks=False)
        if _node_identity(named_probe) != probe_identity:
            raise HostProbeCleanupError("cleanup residual: %s" % residual)
        os.close(descriptor)
        descriptor = None
        os.rmdir(probe_name, dir_fd=parent_fd)
        os.fsync(parent_fd)
        try:
            os.stat(probe_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise HostProbeCleanupError("cleanup residual: %s" % residual)
    except HostProbeCleanupError:
        raise
    except Exception as error:
        raise HostProbeCleanupError("cleanup residual: %s" % residual) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _run_metadata_probe(parent_fd: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    probe_name = ""
    probe_identity = None  # type: Optional[Tuple[int, int]]
    probe_fd = None  # type: Optional[int]
    leaf_identity = None  # type: Optional[Tuple[int, int]]
    observations = []  # type: List[Dict[str, Any]]
    original_error = None  # type: Optional[BaseException]
    try:
        for _ in range(32):
            candidate = PROBE_PREFIX + secrets.token_hex(16)
            try:
                os.mkdir(candidate, 0o700, dir_fd=parent_fd)
            except FileExistsError:
                continue
            probe_name = candidate
            named_probe = os.stat(candidate, dir_fd=parent_fd, follow_symlinks=False)
            probe_identity = _node_identity(named_probe)
            break
        if not probe_name or probe_identity is None:
            raise HostProbeError("cannot allocate an exclusive probe directory")
        probe_fd = _open_child_directory(parent_fd, probe_name, probe_identity)
        probe_info = os.fstat(probe_fd)
        if probe_info.st_uid != os.geteuid() or stat.S_IMODE(probe_info.st_mode) != 0o700:
            raise HostProbeError("created probe directory owner or mode differs")

        write_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
        leaf_fd = os.open(PROBE_FILE, write_flags, 0o600, dir_fd=probe_fd)
        try:
            leaf_before = os.fstat(leaf_fd)
            leaf_identity = _node_identity(leaf_before)
            if (not stat.S_ISREG(leaf_before.st_mode) or leaf_before.st_nlink != 1
                    or leaf_before.st_uid != os.geteuid()
                    or stat.S_IMODE(leaf_before.st_mode) != 0o600):
                raise HostProbeError("created probe file identity differs")
            offset = 0
            while offset < len(PROBE_BYTES):
                written = os.write(leaf_fd, PROBE_BYTES[offset:])
                if written <= 0:
                    raise HostProbeError("probe file write did not make progress")
                offset += written
            os.fsync(leaf_fd)
        finally:
            os.close(leaf_fd)

        read_fd = os.open(PROBE_FILE, _required_open_flags(False), dir_fd=probe_fd)
        try:
            read_info = os.fstat(read_fd)
            named_leaf = os.stat(PROBE_FILE, dir_fd=probe_fd, follow_symlinks=False)
            if (_node_identity(read_info) != leaf_identity
                    or _node_identity(named_leaf) != leaf_identity
                    or read_info.st_size != len(PROBE_BYTES)):
                raise HostProbeError("probe file changed before readback")
            raw = os.read(read_fd, len(PROBE_BYTES) + 1)
            if raw != PROBE_BYTES:
                raise HostProbeError("probe file readback differs")
            observations.append(_observe_metadata(probe_fd, "probe-directory"))
            observations.append(_observe_metadata(read_fd, "probe-file"))
        finally:
            os.close(read_fd)
    except BaseException as error:
        original_error = error
    finally:
        if probe_name:
            try:
                _cleanup_created_probe(
                    parent_fd, probe_name, probe_identity, probe_fd, leaf_identity
                )
                probe_fd = None
            except BaseException as cleanup_error:
                if original_error is not None:
                    raise HostProbeCleanupError(str(cleanup_error)) from original_error
                raise
    if original_error is not None:
        raise original_error
    return observations, {
        "complete": True,
        "residual_paths": [],
        "parent_inventory_after": _bounded_names(parent_fd, MAX_PARENT_ENTRIES, "diagnostic parent"),
    }


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _paths_overlap(first: Path, second: Path) -> bool:
    try:
        common = Path(os.path.commonpath((os.fspath(first), os.fspath(second))))
    except ValueError:
        return False
    return common == first or common == second


def probe_host(private_parent: Path) -> Dict[str, Any]:
    repository = _canonical_absolute(_repository_root(), "repository")
    policy_binding = _authenticate_policy_files(repository)
    temporary_root = _canonical_absolute(
        Path(os.path.realpath(tempfile.gettempdir())), "system temporary directory"
    )
    parent_path = _canonical_absolute(private_parent, "diagnostic parent")
    if parent_path.parent != temporary_root or not parent_path.name.startswith(PARENT_PREFIX):
        raise HostProbeError(
            "diagnostic parent must be a fixed-prefix direct child of the system temporary directory"
        )
    if _paths_overlap(parent_path, repository):
        raise HostProbeError("diagnostic parent overlaps the repository")

    opened_parent = _open_absolute(parent_path, "diagnostic parent", directory=True)
    try:
        parent_before = os.fstat(opened_parent.descriptor)
        if (parent_before.st_uid != os.geteuid()
                or stat.S_IMODE(parent_before.st_mode) != 0o700):
            raise HostProbeError("diagnostic parent must be euid-owned mode 0700")
        if _bounded_names(opened_parent.descriptor, MAX_PARENT_ENTRIES, "diagnostic parent"):
            raise HostProbeError("diagnostic parent must be fresh and empty")
        parent_observation = _observe_metadata(opened_parent.descriptor, "diagnostic-parent")
        if parent_observation["acl_present"] or parent_observation["bsd_flags"] != 0:
            raise HostProbeError("diagnostic parent has unsafe ACLs or BSD flags")

        observations, cleanup = _run_metadata_probe(opened_parent.descriptor)
        if cleanup["parent_inventory_after"]:
            raise HostProbeCleanupError("cleanup residual: diagnostic parent")
        parent_after = os.fstat(opened_parent.descriptor)
        opened_parent.reauthenticate()
        if _stable_directory_identity(parent_before) != _stable_directory_identity(parent_after):
            raise HostProbeError("diagnostic parent stable identity changed")

        blockers = []  # type: List[str]
        if any(item["xattrs"] for item in observations):
            blockers.append("xattrs-absent-unavailable")
        if any(item["acl_present"] for item in observations):
            blockers.append("acls-absent-unavailable")
        if any(item["bsd_flags"] != 0 for item in observations):
            blockers.append("bsd-flags-absent-unavailable")
        eligible = not blockers
        verdict = {
            "schema_version": SCHEMA_VERSION,
            "epoch_id": EPOCH_ID,
            "eligible": eligible,
            "outcome": (
                "host-metadata-profile-eligible"
                if eligible else "host-metadata-profile-ineligible"
            ),
            "classification": dict(CLASSIFICATION),
            "policy_binding": policy_binding,
            "parent": {
                "canonical_absolute_path": os.fspath(parent_path),
                "device": parent_after.st_dev,
                "inode": parent_after.st_ino,
                "owner_uid": parent_after.st_uid,
                "mode": stat.S_IMODE(parent_after.st_mode),
                "xattrs": parent_observation["xattrs"],
                "acl_present": parent_observation["acl_present"],
                "bsd_flags": parent_observation["bsd_flags"],
            },
            "observations": observations,
            "cleanup": cleanup,
            "scope": {
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
            },
            "blockers": blockers,
        }
        return verdict
    finally:
        opened_parent.close()


def _walk_json(value: Any, depth: int = 0, counter: Optional[List[int]] = None) -> None:
    if counter is None:
        counter = [0]
    counter[0] += 1
    if depth > MAX_JSON_DEPTH or counter[0] > MAX_JSON_NODES:
        raise HostProbeError("host verdict exceeds its structural bound")
    if value is None or type(value) is bool:
        return
    if type(value) is int:
        if not -(1 << 63) <= value < (1 << 63):
            raise HostProbeError("host verdict integer exceeds signed 64-bit range")
        return
    if type(value) is str:
        if (len(value.encode("utf-8")) > MAX_STRING_BYTES
                or unicodedata.normalize("NFC", value) != value
                or any(0xD800 <= ord(character) <= 0xDFFF for character in value)):
            raise HostProbeError("host verdict string is invalid")
        return
    if type(value) is list:
        for item in value:
            _walk_json(item, depth + 1, counter)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or len(key.encode("utf-8")) > MAX_STRING_BYTES:
                raise HostProbeError("host verdict object key is invalid")
            _walk_json(key, depth + 1, counter)
            _walk_json(item, depth + 1, counter)
        return
    raise HostProbeError("host verdict contains a forbidden JSON value")


def canonical_verdict_bytes(value: Mapping[str, Any]) -> bytes:
    _walk_json(value)
    raw = (json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ) + "\n").encode("utf-8")
    if len(raw) > MAX_JSON_BYTES:
        raise HostProbeError("host verdict exceeds its fixed byte ceiling")
    return raw


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("private_temporary_parent", type=Path)
    arguments = parser.parse_args(argv)
    try:
        verdict = probe_host(arguments.private_temporary_parent)
        sys.stdout.buffer.write(canonical_verdict_bytes(verdict))
    except Exception as error:
        print("physics source-capsule host probe failed: %s" % error, file=sys.stderr)
        return 1
    return 0 if verdict["eligible"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
