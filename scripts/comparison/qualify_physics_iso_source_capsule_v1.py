#!/usr/bin/env python3
"""Run the bounded, temp-only ISO source-capsule host qualification v1.

This module is deliberately not a standalone executor.  A separately pinned
locked driver must authenticate these exact source bytes, then call
``run_from_authenticated_driver`` with its independently frozen checkpoint and
implementation bindings.  Ordinary CLI invocation cannot create an image,
attach a device, materialize a source root, or emit a qualification receipt.

The successful receipt is diagnostic and permanently nonauthorizing: it never
authorizes a build, capture, simulator execution, promotion, or scientific use.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import signal
import stat
import sys
import types
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple
import unicodedata


CONTRACT_PATH = "docs/simulator/physics_iso_source_capsule_qualification_contract_v1.json"
CONTRACT_BYTES = 55_127
CONTRACT_SHA256 = "62e00380bda281eaeaf4bacf717494964306ce4a840138b62e0707caf476e5e4"
CONTRACT_VALIDATOR_PATH = "scripts/comparison/validate_physics_iso_source_capsule_qualification_contract_v1.py"
CONTRACT_VALIDATOR_BYTES = 28_604
CONTRACT_VALIDATOR_SHA256 = "c7098c9f84256531376963f030c2017af40c8e034af45ef63bd7536eb3524066"
PROTECTED_SOURCE_CONTRACT_PATH = "docs/simulator/physics_protected_source_roots_contract_v2.json"
PROTECTED_SOURCE_CONTRACT_BYTES = 19_728
PROTECTED_SOURCE_CONTRACT_SHA256 = "21fb7fde7dc8b44eba46d4223e9c441f38888b6dc427ff1a7b3993eab92675a6"

SOURCE_BINDINGS = (
    ("scripts/comparison/validate_physics_protected_source_roots_contract_v2.py", 46_056,
     "927cfdb7a845822499c36791fcf0bb85fafb62df5e9cb8a143bc92da828a700a"),
    ("scripts/comparison/generate_physics_source_indexes_v2.py", 33_990,
     "16edd827f0ba93d2c6e9699cfd4e4df8466229221172b9a55fa161a87485b77c"),
    ("scripts/comparison/verify_physics_source_indexes_v2.py", 18_446,
     "09cef6043ecf6b72e2fb2e373f8826581a9f784e1509cacfb5fcdd3359bf7546"),
)
INDEX_BINDINGS = (
    ("common-source", "comparison/physics-superbuild-v1/source-indexes/common-source-v2.json",
     100_897, "1270fc7975691abf302627a96e6b027da0b8306790e7090f89219877eed76f19"),
    ("optimized-source", "comparison/physics-superbuild-v1/source-indexes/optimized-source-v2.json",
     14_237, "f27bc056aa6face5fe0c72be3e2adf6a59d46f775214c60b7031a006a9957a33"),
    ("reference-source", "comparison/physics-superbuild-v1/source-indexes/reference-source-v2.json",
     14_236, "f786fbc410a904f3f71bb014dc018969073b7aa13a1d5472d299172d2e1da910"),
)
HANDOFF_BINDING = (
    "comparison/physics-superbuild-v1/source-indexes/source-input-handoff-v2.json",
    1_226,
    "a3f9b676198fba7e471f2a6500cbb94cf2e8f8c804b560b520bbf2c4d0112b4f",
)

QUALIFIER_PATH = "scripts/comparison/qualify_physics_iso_source_capsule_v1.py"
RECEIPT_VERIFIER_PATH = "scripts/comparison/verify_physics_iso_source_capsule_qualification_v1.py"
EXPECTED_ROOT_ORDER = ("common-source", "optimized-source", "reference-source")
PRIVATE_TMP = "/private/tmp"
PHASE_B_LIVE_SIGNAL_PRECONDITION = (
    "before Phase B is reachable, the independently locked driver must prove "
    "single-threaded execution and install controlled SIGHUP/SIGINT/SIGQUIT/"
    "SIGTERM handlers that raise a cleanup-caught cancellation; it must restore "
    "the prior handlers after residue-free cleanup; SIGKILL is not recoverable"
)
FAILURE_PREFIX = "physics ISO source-capsule qualification failed: "
FAILURE_EXIT = 1
ARGUMENT_EXIT = 2
SUCCESS_EXIT = 0
RENAME_FLAGS = 0x00000034
RENAME_EXCL = 0x00000004
RENAME_NOFOLLOW_ANY = 0x00000010
RENAME_RESOLVE_BENEATH = 0x00000020
MNT_RDONLY = 0x00000001
READ_CHUNK = 65_536
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
DEVICE_RE = re.compile(r"^/dev/disk[0-9]+(?:s[0-9]+)?$")


class QualificationError(RuntimeError):
    """A frozen qualification invariant failed."""


class CleanupError(QualificationError):
    """The bounded cleanup proof failed and no receipt may be emitted."""


class PhaseBInjectedFailure(QualificationError):
    """A synthetic failure hook stopped private staging."""


class _PhaseBCapsule:
    """Descriptor-held state for one private staging capsule."""

    def __init__(self) -> None:
        self.private_tmp_fds = []  # type: List[int]
        self.private_tmp_records = []  # type: List[Tuple[int, str, int, Tuple[int, ...]]]
        self.private_tmp_fd = -1
        self.name = ""
        self.path = ""
        self.p_fd = -1
        self.p_inode = None  # type: Optional[Tuple[int, ...]]
        self.p_cleanup_progress = {}  # type: Dict[str, bool]
        self.child_fds = {}  # type: Dict[str, int]
        self.child_inodes = {}  # type: Dict[str, Tuple[int, ...]]
        self.child_cleanup_progress = {}  # type: Dict[str, Dict[str, bool]]
        self.stage_directories = {}  # type: Dict[Tuple[str, str], Dict[str, Any]]
        self.stage_files = []  # type: List[Dict[str, Any]]
        self.unbound_created_directories = []  # type: List[Dict[str, Any]]
        self.cleaned = False


def _repository_root() -> Path:
    return Path(os.path.abspath(__file__)).parents[2]


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _u64be(value: int) -> bytes:
    if type(value) is not int or value < 0 or value >= 1 << 64:
        raise QualificationError("invalid framed length")
    return value.to_bytes(8, "big")


def _framed(raw: bytes) -> bytes:
    return _u64be(len(raw)) + raw


def _domain(value: str) -> bytes:
    if type(value) is not str or not value.endswith("\\0") or "\\0" in value[:-2]:
        raise QualificationError("invalid hash domain")
    return value[:-2].encode("utf-8") + b"\0"


def _stat_identity(info: os.stat_result) -> Tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_uid,
        info.st_gid, info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _read_fd_exact(fd: int, size: int, maximum: int, label: str) -> bytes:
    if size < 0 or size > maximum:
        raise QualificationError("%s exceeds its byte cap" % label)
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []  # type: List[bytes]
    total = 0
    while total < size:
        chunk = os.read(fd, min(READ_CHUNK, size - total))
        if not chunk:
            raise QualificationError("%s ended early" % label)
        chunks.append(chunk)
        total += len(chunk)
    if os.read(fd, 1):
        raise QualificationError("%s grew while read" % label)
    return b"".join(chunks)


def _strict_relative_path(value: Any, label: str) -> Tuple[str, ...]:
    if (type(value) is not str or not value or "\\" in value or "\0" in value
            or unicodedata.normalize("NFC", value) != value
            or len(value.encode("utf-8")) > 1_024):
        raise QualificationError("%s is not canonical repository-relative UTF-8" % label)
    pure = PurePosixPath(value)
    parts = pure.parts
    if (pure.is_absolute() or pure.as_posix() != value or not parts
            or any(part in {"", ".", ".."} for part in parts)
            or any(len(part.encode("utf-8")) > 255 for part in parts)):
        raise QualificationError("%s has an absolute, empty, or dot component" % label)
    return tuple(parts)


def _directory_identity(info: os.stat_result) -> Tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
        info.st_uid, info.st_gid, info.st_ctime_ns,
    )


def _required_open_flag(name: str) -> int:
    value = getattr(os, name, None)
    if type(value) is not int or value == 0:
        raise QualificationError("required open flag %s is unavailable" % name)
    return value


def _reauthenticate_directory_chain(
        records: Sequence[Tuple[int, str, int, Tuple[int, ...], Tuple[int, ...]]],
        label: str) -> None:
    for parent_fd, component, child_fd, child_identity, parent_identity in records:
        parent = os.fstat(parent_fd)
        child = os.fstat(child_fd)
        named = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
        if (_directory_identity(parent) != parent_identity
                or _directory_identity(child) != child_identity
                or _directory_identity(named) != child_identity):
            raise QualificationError("%s ancestry changed during terminal reauthentication" % label)


def _open_absolute_directory_chain(
        path: Path, label: str,
) -> Tuple[List[int], List[Tuple[int, str, int, Tuple[int, ...], Tuple[int, ...]]]]:
    """Retain and bind every descriptor in one absolute directory walk."""

    absolute = Path(os.path.abspath(os.fspath(path)))
    if not absolute.is_absolute():
        raise QualificationError("%s is not absolute" % label)
    parts = absolute.parts[1:]
    flags = os.O_RDONLY | _required_open_flag("O_CLOEXEC") | _required_open_flag("O_DIRECTORY") | _required_open_flag("O_NOFOLLOW")
    descriptors = []  # type: List[int]
    records = []  # type: List[Tuple[int, str, int, Tuple[int, ...], Tuple[int, ...]]]
    previous_signals = _block_tracking_signals()
    primary_error = None  # type: Optional[BaseException]
    try:
        try:
            root_fd = os.open("/", flags)
        except OSError as error:
            raise QualificationError(
                "cannot open filesystem root for %s" % label
            ) from error
        descriptors.append(root_fd)
        root_info = os.fstat(root_fd)
        if not stat.S_ISDIR(root_info.st_mode):
            raise QualificationError("filesystem root is not a directory")
        for component in parts:
            if component in {"", ".", ".."} or "/" in component or "\0" in component:
                raise QualificationError("%s has an invalid ancestor component" % label)
            parent_fd = descriptors[-1]
            parent_before = os.fstat(parent_fd)
            try:
                child_fd = os.open(component, flags, dir_fd=parent_fd)
            except OSError as error:
                raise QualificationError("cannot descriptor-walk %s" % label) from error
            descriptors.append(child_fd)
            child_info = os.fstat(child_fd)
            named = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            parent_after = os.fstat(parent_fd)
            if (not stat.S_ISDIR(child_info.st_mode)
                    or _directory_identity(child_info) != _directory_identity(named)
                    or _directory_identity(parent_before) != _directory_identity(parent_after)):
                raise QualificationError("%s ancestor changed during descriptor walk" % label)
            if child_info.st_uid not in (0, os.geteuid()) or stat.S_IMODE(child_info.st_mode) & 0o022:
                raise QualificationError("%s has an untrusted writable ancestor" % label)
            records.append((
                parent_fd, component, child_fd,
                _directory_identity(child_info), _directory_identity(parent_before),
            ))
        final = os.fstat(descriptors[-1])
        named_final = os.stat(os.fspath(absolute), follow_symlinks=False)
        if _directory_identity(final) != _directory_identity(named_final):
            raise QualificationError("%s final directory name differs" % label)
        _reauthenticate_directory_chain(records, label)
    except BaseException as error:
        primary_error = error
    if primary_error is None:
        try:
            _restore_tracking_signals(previous_signals)
        except BaseException as error:
            primary_error = error
        else:
            return descriptors, records
    _cleanup_owned_descriptors_and_raise(
        descriptors, previous_signals, primary_error,
        "absolute directory chain for " + label,
    )


def _stable_directory_inode(info: os.stat_result) -> Tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode),
        info.st_uid, info.st_gid,
    )


def _open_private_tmp(capsule: _PhaseBCapsule) -> None:
    """Open exact /private/tmp and retain its complete no-symlink ancestry."""

    flags = os.O_RDONLY | _required_open_flag("O_CLOEXEC") | _required_open_flag("O_DIRECTORY") | _required_open_flag("O_NOFOLLOW")
    descriptors = []  # type: List[int]
    records = []  # type: List[Tuple[int, str, int, Tuple[int, ...]]]
    capsule.private_tmp_fds = descriptors
    capsule.private_tmp_records = records
    capsule.private_tmp_fd = -1
    previous_signals = _block_tracking_signals()
    primary_error = None  # type: Optional[BaseException]
    try:
        root_fd = os.open("/", flags)
        descriptors.append(root_fd)
        root = os.fstat(root_fd)
        if (not stat.S_ISDIR(root.st_mode) or root.st_uid != 0 or root.st_gid != 0
                or stat.S_IMODE(root.st_mode) != 0o755):
            raise QualificationError("filesystem root policy differs")
        for component, expected_mode in (("private", 0o755), ("tmp", 0o1777)):
            parent_fd = descriptors[-1]
            try:
                child_fd = os.open(component, flags, dir_fd=parent_fd)
            except OSError as error:
                raise QualificationError("cannot open exact /private/tmp ancestry") from error
            descriptors.append(child_fd)
            child = os.fstat(child_fd)
            named = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
            if (not stat.S_ISDIR(child.st_mode)
                    or child.st_uid != 0 or child.st_gid != 0
                    or stat.S_IMODE(child.st_mode) != expected_mode
                    or _stable_directory_inode(child) != _stable_directory_inode(named)):
                raise QualificationError(
                    "exact /private/tmp ownership or sticky policy differs"
                )
            records.append((parent_fd, component, child_fd, _stable_directory_inode(child)))
            capsule.private_tmp_fd = child_fd
        for parent_fd, component, child_fd, identity in records:
            if (_stable_directory_inode(os.fstat(child_fd)) != identity
                    or _stable_directory_inode(os.stat(
                        component, dir_fd=parent_fd, follow_symlinks=False,
                    )) != identity):
                raise QualificationError("/private/tmp ancestry changed during open")
        capsule.private_tmp_fd = descriptors[-1]
    except BaseException as error:
        primary_error = error
    if primary_error is None:
        try:
            _restore_tracking_signals(previous_signals)
        except BaseException as error:
            primary_error = error
        else:
            return
    def retire_closed_ancestry() -> None:
        capsule.private_tmp_records = []
        capsule.private_tmp_fd = -1

    _cleanup_owned_descriptors_and_raise(
        descriptors, previous_signals, primary_error,
        "exact /private/tmp ancestry",
        retire_closed_ancestry,
    )


def _reauthenticate_private_tmp(capsule: _PhaseBCapsule) -> None:
    if capsule.private_tmp_fd < 0 or not capsule.private_tmp_records:
        raise QualificationError("private tmp ancestry is not retained")
    for parent_fd, component, child_fd, identity in capsule.private_tmp_records:
        try:
            opened = os.fstat(child_fd)
            named = os.stat(component, dir_fd=parent_fd, follow_symlinks=False)
        except OSError as error:
            raise QualificationError("private tmp ancestry disappeared") from error
        if (_stable_directory_inode(opened) != identity
                or _stable_directory_inode(named) != identity):
            raise QualificationError("private tmp ancestry identity differs")
    tmp = os.fstat(capsule.private_tmp_fd)
    if (stat.S_IMODE(tmp.st_mode) != 0o1777 or tmp.st_uid != 0 or tmp.st_gid != 0):
        raise QualificationError("private tmp sticky policy changed")


def _directory_names(fd: int, maximum: int, label: str) -> List[str]:
    if type(maximum) is not int or maximum < 0:
        raise QualificationError("%s has an invalid entry cap" % label)
    names = []  # type: List[str]
    previous_signals = _block_tracking_signals()
    primary_error = None  # type: Optional[BaseException]
    iterator = None  # type: Any
    try:
        iterator = os.scandir(fd)
        for entry in iterator:
            names.append(entry.name)
            if len(names) > maximum:
                raise QualificationError("%s exceeds its entry cap" % label)
    except OSError as error:
        primary_error = QualificationError("cannot enumerate %s" % label)
        primary_error.__cause__ = error
    except BaseException as error:
        primary_error = error
    close_report = {
        "complete": iterator is None,
        "ordinary_errors": [],
        "deferred_base_exceptions": [],
        "attempt_count": 0,
    }  # type: Dict[str, Any]
    if iterator is not None:
        for _ in range(2):
            close_report["attempt_count"] += 1
            try:
                iterator.close()
            except Exception as error:
                close_report["ordinary_errors"].append(
                    type(error).__name__ + ": " + str(error)
                )
            except BaseException as error:
                close_report["deferred_base_exceptions"].append(error)
            else:
                close_report["complete"] = True
                break
    try:
        _restore_tracking_signals(previous_signals)
    except BaseException as error:
        if isinstance(error, Exception):
            close_report["ordinary_errors"].append(
                "signal restoration " + type(error).__name__ + ": "
                + str(error)
            )
            close_report["complete"] = False
        else:
            close_report["deferred_base_exceptions"].append(error)
    if (primary_error is not None or close_report["complete"] is not True
            or close_report["ordinary_errors"]
            or close_report["deferred_base_exceptions"]):
        if primary_error is None:
            primary_error = CleanupError(
                "directory iterator finalization failed for %s" % label
            )
        _raise_after_owned_descriptor_cleanup(
            primary_error, close_report,
            "directory enumeration iterator " + label,
        )
    if len(names) != len(set(names)):
        raise QualificationError("%s contains duplicate names" % label)
    for name in names:
        if (type(name) is not str or not name or name in {".", ".."}
                or "/" in name or "\0" in name
                or unicodedata.normalize("NFC", name) != name
                or len(name.encode("utf-8")) > 255):
            raise QualificationError("%s contains a noncanonical name" % label)
    return names


def _open_bound_directory_at(parent_fd: int, name: str, expected_mode: int,
                             label: str, normalize_mode: bool = False,
                             expected_inode: Optional[Tuple[int, ...]] = None) -> Tuple[int, Tuple[int, ...]]:
    flags = os.O_RDONLY | _required_open_flag("O_CLOEXEC") | _required_open_flag("O_DIRECTORY") | _required_open_flag("O_NOFOLLOW")
    previous_signals = _block_tracking_signals()
    owned = []  # type: List[int]
    primary_error = None  # type: Optional[BaseException]
    try:
        try:
            fd = os.open(name, flags, dir_fd=parent_fd)
        except OSError as error:
            raise QualificationError("cannot open %s" % label) from error
        owned.append(fd)
        opened = os.fstat(fd)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        identity = _stable_directory_inode(opened)
        if (not stat.S_ISDIR(opened.st_mode) or opened.st_uid != os.geteuid()
                or identity != _stable_directory_inode(named)):
            raise QualificationError("%s directory identity differs" % label)
        if expected_inode is not None and identity != expected_inode:
            raise QualificationError("%s created inode differs" % label)
        if normalize_mode:
            os.fchmod(fd, expected_mode)
            opened = os.fstat(fd)
            named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (stat.S_IMODE(opened.st_mode) != expected_mode
                or _stable_directory_inode(opened) != identity
                or _stable_directory_inode(named) != identity):
            raise QualificationError("%s directory mode differs" % label)
    except BaseException as error:
        primary_error = error
    if primary_error is None:
        try:
            _restore_tracking_signals(previous_signals)
        except BaseException as error:
            primary_error = error
        else:
            return fd, identity
    _cleanup_owned_descriptors_and_raise(
        owned, previous_signals, primary_error, label,
    )


def _phase_b_fault(fault_injector: Optional[Callable[[str], None]], event: str) -> None:
    if fault_injector is not None:
        fault_injector(event)


def _block_tracking_signals() -> Any:
    """Delay cancellation across create→bind and remove→progress gaps."""

    mask = getattr(signal, "pthread_sigmask", None)
    block = getattr(signal, "SIG_BLOCK", None)
    if not callable(mask) or block is None:
        raise QualificationError("per-thread signal masking is unavailable")
    names = ("SIGHUP", "SIGINT", "SIGQUIT", "SIGTERM")
    blocked = set()  # type: Set[Any]
    for name in names:
        value = getattr(signal, name, None)
        if value is None:
            raise QualificationError("required tracking signal %s is unavailable" % name)
        blocked.add(value)
    try:
        return mask(block, blocked)
    except OSError as error:
        raise QualificationError("cannot block create-to-tracking signals") from error


def _restore_tracking_signals(previous: Any) -> None:
    mask = getattr(signal, "pthread_sigmask", None)
    setmask = getattr(signal, "SIG_SETMASK", None)
    if not callable(mask) or setmask is None:
        raise QualificationError("per-thread signal restoration is unavailable")
    try:
        mask(setmask, previous)
    except OSError as error:
        raise QualificationError("cannot restore create-to-tracking signals") from error


def _close_owned_descriptor_list(
        descriptors: List[int], label: str) -> Dict[str, Any]:
    """Boundedly retire every locally owned fd while retaining failures.

    The caller must already hold the tracking-signal mask.  The list is the
    ownership registry: a descriptor is removed only after close success or an
    EBADF reconciliation performed by ``_close_descriptor_with_retry``.
    """

    ordinary_errors = []  # type: List[str]
    deferred_base_exceptions = []  # type: List[BaseException]
    unclosed = []  # type: List[int]
    seen = set()  # type: Set[int]
    for descriptor in reversed(list(descriptors)):
        if descriptor in seen:
            unclosed.append(descriptor)
            ordinary_errors.append(
                "%s has duplicate descriptor %d" % (label, descriptor)
            )
            continue
        seen.add(descriptor)
        result = _close_descriptor_with_retry(
            descriptor, "%s descriptor %d" % (label, descriptor),
        )
        ordinary_errors.extend(result["ordinary_errors"])
        deferred_base_exceptions.extend(
            result["deferred_base_exceptions"]
        )
        if result["closed"] is True:
            descriptors.remove(descriptor)
        else:
            unclosed.append(descriptor)
    return {
        "complete": not unclosed and not descriptors,
        "ordinary_errors": ordinary_errors,
        "deferred_base_exceptions": deferred_base_exceptions,
        "unclosed": unclosed,
    }


def _raise_after_owned_descriptor_cleanup(
        primary_error: BaseException, close_report: Mapping[str, Any],
        label: str) -> None:
    """Preserve cancellation precedence after bounded local-fd retirement."""

    def attach(error: BaseException) -> None:
        try:
            existing = getattr(error, "descriptor_close_report", None)
            if existing is None:
                error.descriptor_close_report = close_report  # type: ignore[attr-defined]
            elif existing is not close_report:
                additional = list(getattr(
                    error, "additional_descriptor_close_reports", (),
                ))
                additional.append(close_report)
                error.additional_descriptor_close_reports = additional  # type: ignore[attr-defined]
        except Exception:
            pass

    bases = []  # type: List[BaseException]
    if not isinstance(primary_error, Exception):
        bases.append(primary_error)
    bases.extend(close_report.get("deferred_base_exceptions", ()))
    if close_report.get("complete") is not True:
        failure = CleanupError(
            "%s descriptor cleanup remained incomplete" % label
        )
        failure.primary_error = primary_error  # type: ignore[attr-defined]
        failure.descriptor_close_report = close_report  # type: ignore[attr-defined]
        if bases:
            chosen = bases[0]
            try:
                chosen.resource_cleanup_failure = failure  # type: ignore[attr-defined]
            except Exception:
                pass
            attach(chosen)
            raise chosen from failure
        raise failure from primary_error
    if bases:
        chosen = bases[0]
        try:
            chosen.prior_resource_error = primary_error  # type: ignore[attr-defined]
        except Exception:
            pass
        attach(chosen)
        raise chosen
    attach(primary_error)
    raise primary_error


def _cleanup_owned_descriptors_and_raise(
        descriptors: List[int], previous_signals: Any,
        primary_error: BaseException, label: str,
        on_complete: Optional[Callable[[], None]] = None) -> None:
    """Re-block, retire an acquisition registry, restore, then propagate."""

    preclose_ordinary = []  # type: List[str]
    preclose_bases = []  # type: List[BaseException]
    try:
        _block_tracking_signals()
    except BaseException as error:
        if isinstance(error, Exception):
            preclose_ordinary.append(
                "signal reblock " + type(error).__name__ + ": " + str(error)
            )
        else:
            preclose_bases.append(error)
    close_report = _close_owned_descriptor_list(descriptors, label)
    if close_report["complete"] is True and on_complete is not None:
        try:
            on_complete()
        except BaseException as error:
            if isinstance(error, Exception):
                close_report["ordinary_errors"].append(
                    "registry retirement " + type(error).__name__ + ": "
                    + str(error)
                )
                close_report["complete"] = False
            else:
                close_report["deferred_base_exceptions"].append(error)
    close_report["ordinary_errors"] = (
        preclose_ordinary + close_report["ordinary_errors"]
    )
    close_report["deferred_base_exceptions"] = (
        preclose_bases + close_report["deferred_base_exceptions"]
    )
    try:
        _restore_tracking_signals(previous_signals)
    except BaseException as error:
        if isinstance(error, Exception):
            close_report["ordinary_errors"].append(
                "signal restoration " + type(error).__name__ + ": "
                + str(error)
            )
            if close_report["complete"] is True:
                close_report["complete"] = False
        else:
            close_report["deferred_base_exceptions"].append(error)
    _raise_after_owned_descriptor_cleanup(primary_error, close_report, label)


def _operate_on_ephemeral_fd(
        open_fd: Callable[[], int], operation: Callable[[int], Any],
        label: str) -> Any:
    """Open, use, and close one fd without an interruptible ownership gap."""

    previous_signals = _block_tracking_signals()
    owned = []  # type: List[int]
    primary_error = None  # type: Optional[BaseException]
    result = None  # type: Any
    try:
        descriptor = open_fd()
        owned.append(descriptor)
        result = operation(descriptor)
    except BaseException as error:
        primary_error = error
    close_report = _close_owned_descriptor_list(owned, label)
    try:
        _restore_tracking_signals(previous_signals)
    except BaseException as error:
        if isinstance(error, Exception):
            close_report["ordinary_errors"].append(
                "signal restoration " + type(error).__name__ + ": "
                + str(error)
            )
            close_report["complete"] = False
        else:
            close_report["deferred_base_exceptions"].append(error)
    if (primary_error is None and close_report["complete"] is True
            and not close_report["ordinary_errors"]
            and not close_report["deferred_base_exceptions"]):
        return result
    if primary_error is None:
        primary_error = CleanupError(
            "%s descriptor finalization failed" % label
        )
    _raise_after_owned_descriptor_cleanup(
        primary_error, close_report, label,
    )


def _prebind_mkdir_fault(
        fault_injector: Optional[Callable[[str], None]], event: str,
        recovery_capture: Callable[[], None]) -> None:
    """Inject at mkdir→bind while first capturing recovery state on failure.

    A real failure of both the first bind and recovery bind is a controlled
    residual and can never produce cleanup-complete evidence.  The injected
    boundary is recoverable because this helper performs the first inode bind
    before propagating the synthetic exception.
    """

    try:
        _phase_b_fault(fault_injector, event)
    except BaseException:
        recovery_capture()
        raise


def _record_unbound_created_directory(
        capsule: _PhaseBCapsule, parent_fd: int, name: str, scope: str) -> Dict[str, Any]:
    record = {"parent_fd": parent_fd, "name": name, "scope": scope}
    capsule.unbound_created_directories.append(record)
    return record


def _clear_unbound_created_directory(
        capsule: _PhaseBCapsule, record: Dict[str, Any]) -> None:
    try:
        capsule.unbound_created_directories.remove(record)
    except ValueError as error:
        raise QualificationError("created-directory prebind record disappeared") from error


def _create_private_capsule(
        fault_injector: Optional[Callable[[str], None]] = None) -> _PhaseBCapsule:
    capsule = _PhaseBCapsule()
    try:
        _open_private_tmp(capsule)
        _phase_b_fault(fault_injector, "after-private-tmp-open")
        p_unbound = None  # type: Optional[Dict[str, Any]]

        def capture_p() -> None:
            p_named = os.stat(
                capsule.name, dir_fd=capsule.private_tmp_fd,
                follow_symlinks=False,
            )
            if not stat.S_ISDIR(p_named.st_mode) or p_named.st_uid != os.geteuid():
                raise QualificationError("new private capsule named inode differs")
            capsule.p_inode = _stable_directory_inode(p_named)
            if (p_unbound is not None
                    and p_unbound in capsule.unbound_created_directories):
                _clear_unbound_created_directory(capsule, p_unbound)

        for _ in range(32):
            name = "dancers-physics-iso-qual-v1-" + secrets.token_hex(16)
            created = False
            previous_signals = _block_tracking_signals()
            try:
                try:
                    os.mkdir(name, 0o700, dir_fd=capsule.private_tmp_fd)
                except FileExistsError:
                    pass
                except OSError as error:
                    raise QualificationError(
                        "cannot create private qualification capsule"
                    ) from error
                else:
                    created = True
                    capsule.name = name
                    capsule.path = PRIVATE_TMP + "/" + capsule.name
                    p_unbound = _record_unbound_created_directory(
                        capsule, capsule.private_tmp_fd, capsule.name,
                        "private-P",
                    )
                    capture_p()
            finally:
                _restore_tracking_signals(previous_signals)
            if created:
                break
        if not capsule.name:
            raise QualificationError("cannot allocate an exclusive capsule name")
        _prebind_mkdir_fault(
            fault_injector, "after-p-mkdir-before-bind", capture_p,
        )
        capture_p()
        _phase_b_fault(fault_injector, "after-p-mkdir")
        previous_signals = _block_tracking_signals()
        try:
            capsule.p_fd, capsule.p_inode = _open_bound_directory_at(
                capsule.private_tmp_fd, capsule.name, 0o700,
                "private capsule", normalize_mode=True,
                expected_inode=capsule.p_inode,
            )
        finally:
            _restore_tracking_signals(previous_signals)
        _phase_b_fault(fault_injector, "after-p-open")
        os.fsync(capsule.private_tmp_fd)
        for child in ("stage.work", "home", "tmp", "mount"):
            child_unbound = None  # type: Optional[Dict[str, Any]]

            def capture_child(child_name: str = child) -> None:
                child_named = os.stat(
                    child_name, dir_fd=capsule.p_fd,
                    follow_symlinks=False,
                )
                if (not stat.S_ISDIR(child_named.st_mode)
                        or child_named.st_uid != os.geteuid()):
                    raise QualificationError(
                        "new capsule child named inode differs"
                    )
                capsule.child_inodes[child_name] = _stable_directory_inode(
                    child_named
                )
                if (child_unbound is not None
                        and child_unbound in capsule.unbound_created_directories):
                    _clear_unbound_created_directory(capsule, child_unbound)

            previous_signals = _block_tracking_signals()
            try:
                try:
                    os.mkdir(child, 0o700, dir_fd=capsule.p_fd)
                except OSError as error:
                    raise QualificationError(
                        "cannot create exact capsule child %s" % child
                    ) from error
                child_unbound = _record_unbound_created_directory(
                    capsule, capsule.p_fd, child,
                    "capsule-child-" + child,
                )
                capture_child()
            finally:
                _restore_tracking_signals(previous_signals)

            _prebind_mkdir_fault(
                fault_injector, "after-%s-mkdir-before-bind" % child,
                capture_child,
            )
            capture_child()
            identity = capsule.child_inodes[child]
            _phase_b_fault(fault_injector, "after-%s-mkdir" % child)
            previous_signals = _block_tracking_signals()
            try:
                child_fd, identity = _open_bound_directory_at(
                    capsule.p_fd, child, 0o700, "capsule child " + child,
                    normalize_mode=True, expected_inode=identity,
                )
                capsule.child_fds[child] = child_fd
                capsule.child_inodes[child] = identity
            finally:
                _restore_tracking_signals(previous_signals)
            _phase_b_fault(fault_injector, "after-%s-open" % child)
        os.fsync(capsule.p_fd)
        _phase_b_fault(fault_injector, "after-p-fsync")
        if set(_directory_names(capsule.p_fd, 5, "private capsule")) != {
                "stage.work", "home", "tmp", "mount"}:
            raise QualificationError("private capsule child inventory differs")
        for child, child_fd in capsule.child_fds.items():
            if _directory_names(child_fd, 1, "capsule child " + child):
                raise QualificationError("new capsule child is not empty")
        _reauthenticate_private_tmp(capsule)
        return capsule
    except BaseException as primary_error:
        no_owned_state = (
            not capsule.private_tmp_fds
            and not capsule.private_tmp_records
            and capsule.private_tmp_fd < 0
            and not capsule.name
            and not capsule.path
            and capsule.p_fd < 0
            and capsule.p_inode is None
            and not capsule.child_fds
            and not capsule.child_inodes
            and not capsule.stage_files
            and not capsule.stage_directories
            and not capsule.unbound_created_directories
        )
        if no_owned_state:
            capsule.cleaned = True
            evidence = {
                "complete": True,
                "private_parent_absent": True,
                "stage_files_removed": 0,
                "stage_directories_removed": 0,
                "capsule_directories_removed": 0,
                "synthetic_injected_cleanup_faults": [],
                "capsule_creation_not_reached": True,
            }
        else:
            try:
                evidence = _cleanup_phase_b_capsule(
                    capsule, require_exact_counts=False,
                )
            except BaseException as cleanup_error:
                reported_evidence = getattr(
                    cleanup_error, "physical_cleanup_evidence", None,
                )
                cleanup_completed = (
                    type(reported_evidence) is dict
                    and reported_evidence.get("complete") is True
                    and capsule.cleaned is True
                )
                cleanup_report = {
                    "cleanup_error_type": type(cleanup_error).__name__,
                    "cleanup_error_message": str(cleanup_error),
                    "cleanup_completed": cleanup_completed,
                }  # type: Dict[str, Any]
                for attribute in (
                        "descriptor_close_report", "descriptor_close_state",
                        "resumed_errors", "cleanup_complete"):
                    value = getattr(cleanup_error, attribute, None)
                    if value is not None:
                        cleanup_report[attribute] = value
                residual_state = {
                    "capsule_cleaned": capsule.cleaned,
                    "private_tmp_descriptor_count": len(
                        capsule.private_tmp_fds
                    ),
                    "private_tmp_terminal_registered": (
                        capsule.private_tmp_fd >= 0
                    ),
                    "capsule_name_registered": bool(capsule.name),
                    "private_capsule_descriptor_registered": (
                        capsule.p_fd >= 0
                    ),
                    "private_capsule_inode_registered": (
                        capsule.p_inode is not None
                    ),
                    "child_descriptor_names": sorted(capsule.child_fds),
                    "child_inode_names": sorted(capsule.child_inodes),
                    "stage_file_record_count": len(capsule.stage_files),
                    "stage_file_live_descriptor_count": sum(
                        1 for record in capsule.stage_files
                        if record.get("fd", -1) >= 0
                    ),
                    "stage_directory_record_count": len(
                        capsule.stage_directories
                    ),
                    "stage_directory_live_descriptor_count": sum(
                        1 for record in capsule.stage_directories.values()
                        if record.get("fd", -1) >= 0
                    ),
                    "unbound_created_directory_count": len(
                        capsule.unbound_created_directories
                    ),
                }
                if cleanup_completed:
                    failure = CleanupError(
                        "private capsule creation failed and cleanup completed "
                        "with a terminal error: %s" % cleanup_error
                    )
                    failure.primary_error = primary_error  # type: ignore[attr-defined]
                    failure.cleanup_error = cleanup_error  # type: ignore[attr-defined]
                    failure.cleanup_report = cleanup_report  # type: ignore[attr-defined]
                    failure.physical_cleanup_evidence = reported_evidence  # type: ignore[attr-defined]
                    if not isinstance(primary_error, Exception):
                        try:
                            primary_error.cleanup_terminal_failure = failure  # type: ignore[attr-defined]
                            primary_error.cleanup_terminal_error = cleanup_error  # type: ignore[attr-defined]
                            primary_error.cleanup_report = cleanup_report  # type: ignore[attr-defined]
                            primary_error.physical_cleanup_evidence = reported_evidence  # type: ignore[attr-defined]
                        except Exception:
                            pass
                        raise primary_error from cleanup_error
                    if not isinstance(cleanup_error, Exception):
                        try:
                            cleanup_error.prior_resource_error = primary_error  # type: ignore[attr-defined]
                            cleanup_error.cleanup_terminal_failure = failure  # type: ignore[attr-defined]
                            cleanup_error.cleanup_report = cleanup_report  # type: ignore[attr-defined]
                        except Exception:
                            pass
                        raise cleanup_error from primary_error
                    raise failure from cleanup_error

                failure = CleanupError(
                    "private capsule creation failed and cleanup was incomplete: %s"
                    % cleanup_error
                )
                failure.primary_error = primary_error  # type: ignore[attr-defined]
                failure.cleanup_error = cleanup_error  # type: ignore[attr-defined]
                failure.cleanup_incomplete = True  # type: ignore[attr-defined]
                failure.cleanup_report = cleanup_report  # type: ignore[attr-defined]
                failure.cleanup_residual_state = residual_state  # type: ignore[attr-defined]

                def attach_incomplete(chosen: BaseException) -> None:
                    try:
                        chosen.cleanup_incomplete_failure = failure  # type: ignore[attr-defined]
                        chosen.cleanup_incomplete_report = cleanup_report  # type: ignore[attr-defined]
                        chosen.cleanup_residual_state = residual_state  # type: ignore[attr-defined]
                    except Exception:
                        pass

                if not isinstance(primary_error, Exception):
                    attach_incomplete(primary_error)
                    raise primary_error from failure
                if not isinstance(cleanup_error, Exception):
                    try:
                        cleanup_error.prior_resource_error = primary_error  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    attach_incomplete(cleanup_error)
                    raise cleanup_error from failure
                raise failure from cleanup_error
        try:
            primary_error.physical_cleanup_evidence = evidence  # type: ignore[attr-defined]
        except Exception:
            pass
        raise


def _name_absent(parent_fd: int, name: str) -> bool:
    try:
        os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    except OSError as error:
        raise CleanupError("cannot prove name absence") from error
    return False


def _new_directory_removal_progress() -> Dict[str, bool]:
    return {
        "removed": False,
        "parent_fsynced": False,
        "absence_proved": False,
        "cleanup_retired": False,
        "descriptor_closed": False,
        "registry_retired": False,
    }


def _remove_empty_bound_directory(parent_fd: int, name: str, fd: int,
                                  identity: Tuple[int, ...], label: str,
                                  fault_observer: Optional[Callable[[str], None]] = None,
                                  event_id: Optional[str] = None,
                                  progress: Optional[Dict[str, bool]] = None) -> None:
    state = progress if progress is not None else _new_directory_removal_progress()
    event = event_id or label
    if not state["removed"]:
        opened = os.fstat(fd)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (_stable_directory_inode(opened) != identity
                or _stable_directory_inode(named) != identity
                or _directory_names(fd, 0, label)):
            raise CleanupError("%s exact-inode or empty-directory guard failed" % label)
        if fault_observer is not None:
            fault_observer("before-cleanup-rmdir:" + event)
        previous_signals = _block_tracking_signals()
        try:
            try:
                os.rmdir(name, dir_fd=parent_fd)
            except BaseException:
                if _name_absent(parent_fd, name):
                    state["removed"] = True
                raise
            state["removed"] = True
        finally:
            _restore_tracking_signals(previous_signals)
        if fault_observer is not None:
            fault_observer("after-cleanup-rmdir:" + event)
    if not state["parent_fsynced"]:
        if fault_observer is not None:
            fault_observer("before-cleanup-parent-fsync:" + event)
        os.fsync(parent_fd)
        state["parent_fsynced"] = True
        if fault_observer is not None:
            fault_observer("after-cleanup-parent-fsync:" + event)
    if not state["absence_proved"]:
        if fault_observer is not None:
            fault_observer("before-cleanup-absence-proof:" + event)
        absent = _name_absent(parent_fd, name)
        if not absent:
            raise CleanupError("%s name remains after removal" % label)
        state["absence_proved"] = True
        if fault_observer is not None:
            fault_observer("after-cleanup-absence-proof:" + event)
    state["cleanup_retired"] = True


def _descriptor_is_closed(fd: int) -> bool:
    try:
        os.fstat(fd)
    except OSError as error:
        if error.errno == errno.EBADF:
            return True
        raise CleanupError("cannot reconcile directory descriptor close") from error
    return False


def _close_tracked_record_fd(
        record: Dict[str, Any], state_key: str, label: str,
        fault_observer: Optional[Callable[[str], None]] = None,
        event: Optional[str] = None) -> None:
    if type(state_key) is not str or not state_key:
        raise CleanupError("tracked descriptor close state is invalid")
    record.setdefault(state_key, False)
    fd = record.get("fd", -1)
    if record[state_key]:
        if fd != -1:
            raise CleanupError("%s closed-descriptor record differs" % label)
        return
    if type(fd) is not int or fd < 0:
        raise CleanupError("%s retained descriptor is absent" % label)
    event_name = event or label
    previous_signals = _block_tracking_signals()
    try:
        if fault_observer is not None:
            fault_observer("before-cleanup-tracked-close:" + event_name)
        try:
            os.close(fd)
        except BaseException:
            if _descriptor_is_closed(fd):
                record["fd"] = -1
                record[state_key] = True
            raise
        record["fd"] = -1
        record[state_key] = True
        if fault_observer is not None:
            fault_observer("after-cleanup-tracked-close:" + event_name)
    finally:
        _restore_tracking_signals(previous_signals)


def _retire_removed_capsule_directory(
        capsule: _PhaseBCapsule, name: str, fd: int,
        identity: Tuple[int, ...], event: str,
        progress: Dict[str, bool], private_parent: bool,
        fault_observer: Optional[Callable[[str], None]]) -> None:
    if not progress.get("cleanup_retired"):
        raise CleanupError("capsule directory retired before removal proof")
    progress.setdefault("descriptor_closed", False)
    progress.setdefault("registry_retired", False)
    if progress["descriptor_closed"] and progress["registry_retired"]:
        return
    previous_signals = _block_tracking_signals()
    try:
        if not progress["descriptor_closed"]:
            if fault_observer is not None:
                fault_observer("before-cleanup-descriptor-close:" + event)
            try:
                os.close(fd)
            except BaseException:
                if _descriptor_is_closed(fd):
                    progress["descriptor_closed"] = True
                raise
            progress["descriptor_closed"] = True
            if fault_observer is not None:
                fault_observer("after-cleanup-descriptor-close:" + event)
        if not progress["registry_retired"]:
            if private_parent:
                if capsule.p_fd not in (-1, fd):
                    raise CleanupError("private capsule descriptor registry differs")
                capsule.p_fd = -1
            else:
                registered_fd = capsule.child_fds.get(name)
                registered_inode = capsule.child_inodes.get(name)
                if registered_fd not in (None, fd) or registered_inode not in (None, identity):
                    raise CleanupError("capsule child descriptor registry differs")
                capsule.child_fds.pop(name, None)
                capsule.child_inodes.pop(name, None)
            progress["registry_retired"] = True
    finally:
        _restore_tracking_signals(previous_signals)


def _cleanup_bound_capsule_directory(
        capsule: _PhaseBCapsule, parent_fd: int, name: str, fd: int,
        identity: Tuple[int, ...], label: str, event: str,
        progress: Dict[str, bool], private_parent: bool,
        fault_observer: Optional[Callable[[str], None]]) -> None:
    _remove_empty_bound_directory(
        parent_fd, name, fd, identity, label, fault_observer, event, progress,
    )
    _retire_removed_capsule_directory(
        capsule, name, fd, identity, event, progress, private_parent,
        fault_observer,
    )


def _close_descriptor_with_retry(fd: int, label: str) -> Dict[str, Any]:
    ordinary_errors = []  # type: List[str]
    deferred_base_exceptions = []  # type: List[BaseException]
    for _ in range(2):
        try:
            os.close(fd)
            return {
                "closed": True,
                "ordinary_errors": ordinary_errors,
                "deferred_base_exceptions": deferred_base_exceptions,
            }
        except Exception as error:
            ordinary_errors.append(type(error).__name__ + ": " + str(error))
        except BaseException as error:
            deferred_base_exceptions.append(error)
        try:
            reconciled_closed = _descriptor_is_closed(fd)
        except Exception as error:
            ordinary_errors.append(
                "descriptor reconciliation " + type(error).__name__ + ": "
                + str(error)
            )
            reconciled_closed = False
        except BaseException as error:
            deferred_base_exceptions.append(error)
            reconciled_closed = False
        if reconciled_closed:
            return {
                "closed": True,
                "ordinary_errors": ordinary_errors,
                "deferred_base_exceptions": deferred_base_exceptions,
            }
    return {
        "closed": False,
        "ordinary_errors": ordinary_errors,
        "deferred_base_exceptions": deferred_base_exceptions,
        "label": label,
    }


def _close_phase_b_descriptors(capsule: _PhaseBCapsule) -> Dict[str, Any]:
    """Close every retained descriptor without erasing an unclosed registry."""

    ordinary_errors = []  # type: List[str]
    deferred_base_exceptions = []  # type: List[BaseException]
    unclosed = []  # type: List[str]
    unexpected_live_nonprivate = []  # type: List[str]
    seen = set()  # type: Set[int]

    def close_one(fd: int, label: str) -> bool:
        if fd in seen:
            unclosed.append(label + ":duplicate-descriptor")
            return False
        seen.add(fd)
        result = _close_descriptor_with_retry(fd, label)
        ordinary_errors.extend(result["ordinary_errors"])
        deferred_base_exceptions.extend(result["deferred_base_exceptions"])
        if result["closed"] is not True:
            unclosed.append(label)
            return False
        return True

    for index, record in enumerate(capsule.stage_files):
        fd = record.get("fd", -1)
        if type(fd) is int and fd >= 0:
            label = "stage-file-%d" % index
            unexpected_live_nonprivate.append(label)
            if close_one(fd, label):
                record["fd"] = -1
    for key, record in capsule.stage_directories.items():
        fd = record.get("fd", -1)
        if type(fd) is int and fd >= 0:
            label = "stage-directory-%s/%s" % key
            unexpected_live_nonprivate.append(label)
            if close_one(fd, label):
                record["fd"] = -1
    for name, fd in list(capsule.child_fds.items()):
        if fd >= 0:
            label = "capsule-child-" + name
            unexpected_live_nonprivate.append(label)
            if close_one(fd, label):
                capsule.child_fds.pop(name, None)
    if capsule.p_fd >= 0:
        unexpected_live_nonprivate.append("private-P")
        if close_one(capsule.p_fd, "private-P"):
            capsule.p_fd = -1

    for fd in reversed(list(capsule.private_tmp_fds)):
        label = "private-tmp-ancestry-fd-%d" % fd
        if close_one(fd, label):
            capsule.private_tmp_fds.remove(fd)
            if capsule.private_tmp_fd == fd:
                capsule.private_tmp_fd = -1
    if not capsule.private_tmp_fds:
        capsule.private_tmp_records = []
        capsule.private_tmp_fd = -1
    complete = (
        not unclosed
        and not capsule.private_tmp_fds
        and capsule.private_tmp_fd < 0
        and all(record.get("fd", -1) < 0 for record in capsule.stage_files)
        and all(record.get("fd", -1) < 0
                for record in capsule.stage_directories.values())
        and not capsule.child_fds
        and capsule.p_fd < 0
    )
    return {
        "complete": complete,
        "ordinary_errors": ordinary_errors,
        "deferred_base_exceptions": deferred_base_exceptions,
        "unexpected_live_nonprivate": unexpected_live_nonprivate,
        "unclosed": unclosed,
    }


def _cleanup_phase_b_capsule(capsule: _PhaseBCapsule,
                             require_exact_counts: bool = True,
                             fault_injector: Optional[Callable[[str], None]] = None) -> Dict[str, Any]:
    """Remove only state whose exact created inode is still authenticated."""

    if capsule.cleaned:
        raise CleanupError("private capsule cleanup was requested twice")
    removed_files = 0
    removed_stage_directories = 0
    injected_faults = []  # type: List[str]
    resumed_real_errors = []  # type: List[str]
    deferred_base_exceptions = []  # type: List[BaseException]
    terminal_evidence = None  # type: Optional[Dict[str, Any]]
    descriptor_close_complete = False
    descriptor_close_state = {
        "complete": False,
        "attempt_count": 0,
        "reports": [],
        "ordinary_errors": [],
        "deferred_base_exceptions": [],
        "direct_failures": [],
        "unexpected_live_nonprivate": [],
    }  # type: Dict[str, Any]

    def resume_once(operation: Callable[[Optional[Callable[[str], None]]], Any]) -> Any:
        try:
            return operation(fault_injector)
        except PhaseBInjectedFailure as error:
            injected_faults.append(str(error))
            return operation(None)
        except Exception as error:
            resumed_real_errors.append(type(error).__name__ + ": " + str(error))
            return operation(None)
        except BaseException as error:
            # Complete the same idempotent operation without callbacks, then
            # preserve cancellation only after the complete capsule teardown.
            deferred_base_exceptions.append(error)
            return operation(None)

    def close_descriptors_bounded(attempts: int = 2) -> bool:
        nonlocal descriptor_close_complete
        if descriptor_close_complete:
            return True
        for _ in range(attempts):
            descriptor_close_state["attempt_count"] += 1
            try:
                report = _close_phase_b_descriptors(capsule)
            except Exception as error:
                text = type(error).__name__ + ": " + str(error)
                descriptor_close_state["ordinary_errors"].append(text)
                descriptor_close_state["direct_failures"].append(text)
                continue
            except BaseException as error:
                descriptor_close_state["deferred_base_exceptions"].append(
                    error
                )
                descriptor_close_state["direct_failures"].append(
                    type(error).__name__ + ": " + str(error)
                )
                continue
            descriptor_close_state["reports"].append(report)
            descriptor_close_state["ordinary_errors"].extend(
                report["ordinary_errors"]
            )
            descriptor_close_state["deferred_base_exceptions"].extend(
                report["deferred_base_exceptions"]
            )
            descriptor_close_state["unexpected_live_nonprivate"].extend(
                report["unexpected_live_nonprivate"]
            )
            if report["complete"] is True:
                descriptor_close_complete = True
                descriptor_close_state["complete"] = True
                return True
        return False

    cleanup_previous_signals = _block_tracking_signals()
    try:
        if capsule.unbound_created_directories:
            raise CleanupError(
                "controlled residual: a successful mkdir could not be inode-bound; "
                "automatic deletion is forbidden"
            )
        if capsule.p_fd < 0 and capsule.p_inode is not None and capsule.name:
            capsule.p_fd, reopened_identity = resume_once(
                lambda injector: _open_bound_directory_at(
                    capsule.private_tmp_fd, capsule.name, 0o700,
                    "partial private capsule", expected_inode=capsule.p_inode,
                )
            )
            if reopened_identity != capsule.p_inode:
                raise CleanupError("partial private capsule inode differs")
        # File and nested-directory teardown is added by the materializer below.
        if capsule.stage_files or capsule.stage_directories:
            removed_files, removed_stage_directories = resume_once(
                lambda injector: _cleanup_materialized_stage(capsule, injector),
            )
        if require_exact_counts and (removed_files != 477 or removed_stage_directories != 71):
            raise CleanupError("exact staged cleanup counts differ")
        if capsule.p_fd >= 0:
            expected_children = set(capsule.child_inodes)
            observed_children = resume_once(
                lambda injector: set(_directory_names(
                    capsule.p_fd, 5, "private capsule cleanup",
                ))
            )
            if observed_children != expected_children:
                raise CleanupError("private capsule has unexpected cleanup residue")
            for child in ("stage.work", "mount", "home", "tmp"):
                fd = capsule.child_fds.get(child)
                identity = capsule.child_inodes.get(child)
                if identity is None:
                    continue
                if fd is None:
                    fd, reopened_identity = resume_once(
                        lambda injector, child_name=child,
                        child_identity=identity: _open_bound_directory_at(
                            capsule.p_fd, child_name, 0o700,
                            "partial capsule child " + child_name,
                            expected_inode=child_identity,
                        )
                    )
                    capsule.child_fds[child] = fd
                    if reopened_identity != identity:
                        raise CleanupError("partial capsule child inode differs")
                progress = capsule.child_cleanup_progress.setdefault(
                    child, _new_directory_removal_progress(),
                )
                resume_once(
                    lambda injector, child_name=child, child_fd=fd,
                    child_identity=identity, child_progress=progress:
                    _cleanup_bound_capsule_directory(
                        capsule,
                        capsule.p_fd, child_name, child_fd, child_identity,
                        "capsule child " + child_name,
                        "capsule-child-" + child_name, child_progress, False,
                        injector,
                    )
                )
            remaining_children = resume_once(
                lambda injector: _directory_names(
                    capsule.p_fd, 0, "empty private capsule",
                )
            )
            if remaining_children:
                raise CleanupError("private capsule remains nonempty")
            if capsule.private_tmp_fd < 0 or capsule.p_inode is None or not capsule.name:
                raise CleanupError("private capsule parent binding is absent")
            if not capsule.p_cleanup_progress:
                capsule.p_cleanup_progress = _new_directory_removal_progress()
            resume_once(
                lambda injector: _cleanup_bound_capsule_directory(
                    capsule,
                    capsule.private_tmp_fd, capsule.name, capsule.p_fd,
                    capsule.p_inode, "private capsule", "private-P",
                    capsule.p_cleanup_progress, True, injector,
                )
            )
        resume_once(lambda injector: _reauthenticate_private_tmp(capsule))
        private_parent_absent = (
            not capsule.name
            or resume_once(lambda injector: _name_absent(
                capsule.private_tmp_fd, capsule.name,
            ))
        )
        if not private_parent_absent:
            raise CleanupError("private capsule name was not removed")
        if not close_descriptors_bounded():
            error = CleanupError(
                "one or more private cleanup descriptors remain unclosed"
            )
            error.descriptor_close_state = descriptor_close_state  # type: ignore[attr-defined]
            raise error
        resumed_real_errors.extend(
            "descriptor-close: " + value
            for value in descriptor_close_state["ordinary_errors"]
        )
        deferred_base_exceptions.extend(
            descriptor_close_state["deferred_base_exceptions"]
        )
        if descriptor_close_state["unexpected_live_nonprivate"]:
            resumed_real_errors.append(
                "unexpected live non-private descriptors reached terminal close: "
                + ",".join(
                    descriptor_close_state["unexpected_live_nonprivate"]
                )
            )
        evidence = {
            "complete": True,
            "private_parent_absent": True,
            "stage_files_removed": removed_files,
            "stage_directories_removed": removed_stage_directories,
            "capsule_directories_removed": 4,
            "synthetic_injected_cleanup_faults": injected_faults,
        }
        terminal_evidence = evidence
        capsule.cleaned = True
        if deferred_base_exceptions:
            deferred = deferred_base_exceptions[0]
            try:
                deferred.physical_cleanup_evidence = evidence  # type: ignore[attr-defined]
                deferred.deferred_base_exception_count = len(  # type: ignore[attr-defined]
                    deferred_base_exceptions
                )
            except Exception:
                pass
            raise deferred
        if resumed_real_errors:
            error = CleanupError(
                "cleanup encountered an actual operation failure before a residue-free retry"
            )
            error.physical_cleanup_evidence = evidence  # type: ignore[attr-defined]
            error.resumed_errors = resumed_real_errors  # type: ignore[attr-defined]
            raise error
        return evidence
    finally:
        primary_error = sys.exc_info()[1]
        try:
            if not descriptor_close_complete:
                close_descriptors_bounded()
            if not descriptor_close_complete:
                pending_base = None  # type: Optional[BaseException]
                if (primary_error is not None
                        and not isinstance(primary_error, Exception)):
                    pending_base = primary_error
                elif descriptor_close_state["deferred_base_exceptions"]:
                    pending_base = descriptor_close_state[
                        "deferred_base_exceptions"
                    ][0]
                if pending_base is not None:
                    try:
                        if pending_base is not primary_error:
                            pending_base.prior_cleanup_error = primary_error  # type: ignore[attr-defined]
                        pending_base.cleanup_complete = False  # type: ignore[attr-defined]
                        pending_base.descriptor_close_state = descriptor_close_state  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    raise pending_base
                error = CleanupError(
                    "descriptor cleanup remained incomplete after bounded retries"
                )
                error.primary_cleanup_error = primary_error  # type: ignore[attr-defined]
                error.descriptor_close_state = descriptor_close_state  # type: ignore[attr-defined]
                raise error
            close_cancellations = descriptor_close_state[
                "deferred_base_exceptions"
            ]
            if close_cancellations:
                if (primary_error is None
                        or isinstance(primary_error, Exception)):
                    cancellation = close_cancellations[0]
                    try:
                        cancellation.prior_cleanup_error = primary_error  # type: ignore[attr-defined]
                        cancellation.descriptor_close_state = descriptor_close_state  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    raise cancellation
                try:
                    primary_error.descriptor_close_state = descriptor_close_state  # type: ignore[attr-defined]
                except Exception:
                    pass
            elif primary_error is not None and (
                    descriptor_close_state["ordinary_errors"]
                    or descriptor_close_state["direct_failures"]
                    or descriptor_close_state["unexpected_live_nonprivate"]):
                try:
                    primary_error.descriptor_close_state = descriptor_close_state  # type: ignore[attr-defined]
                except Exception:
                    pass
        finally:
            transport_error = sys.exc_info()[1]
            try:
                _restore_tracking_signals(cleanup_previous_signals)
            except BaseException as restoration_error:
                chosen = restoration_error
                if (transport_error is not None
                        and not isinstance(transport_error, Exception)):
                    chosen = transport_error
                elif (isinstance(restoration_error, Exception)
                        and transport_error is not None):
                    chosen = transport_error
                try:
                    if chosen is not transport_error:
                        chosen.prior_cleanup_error = transport_error  # type: ignore[attr-defined]
                    if chosen is not restoration_error:
                        chosen.signal_restoration_error = restoration_error  # type: ignore[attr-defined]
                    chosen.descriptor_close_state = descriptor_close_state  # type: ignore[attr-defined]
                    if terminal_evidence is not None:
                        chosen.physical_cleanup_evidence = terminal_evidence  # type: ignore[attr-defined]
                except Exception:
                    pass
                raise chosen


def _stable_file_inode(info: os.stat_result) -> Tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode),
        info.st_uid, info.st_gid,
    )


def _git_blob_oid(raw: bytes) -> str:
    return hashlib.sha1(
        b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw
    ).hexdigest()


def _write_all(fd: int, raw: bytes, label: str) -> None:
    offset = 0
    while offset < len(raw):
        try:
            written = os.write(fd, raw[offset:offset + READ_CHUNK])
        except OSError as error:
            raise QualificationError("cannot write %s" % label) from error
        if written <= 0:
            raise QualificationError("%s write made no progress" % label)
        offset += written


def _stage_directory_depth(root_id: str, relative_path: str) -> int:
    return 1 if not relative_path else 1 + len(relative_path.split("/"))


def _stage_event_name(root_id: str, relative_path: str) -> str:
    return root_id if not relative_path else root_id + "/" + relative_path


def _create_stage_directory(
        capsule: _PhaseBCapsule, root_id: str, relative_path: str,
        parent_fd: int, name: str,
        fault_injector: Optional[Callable[[str], None]]) -> Dict[str, Any]:
    key = (root_id, relative_path)
    if key in capsule.stage_directories:
        raise QualificationError("stage directory was requested twice")
    record = {
        "root_id": root_id,
        "relative_path": relative_path,
        "parent_fd": parent_fd,
        "name": name,
        "inode": None,
        "fd": -1,
        "removed": False,
        "parent_fsynced_after_remove": False,
        "absence_proved_after_remove": False,
        "cleanup_retired": False,
        "descriptor_closed_after_remove": False,
    }
    event_name = _stage_event_name(root_id, relative_path)
    unbound = None  # type: Optional[Dict[str, Any]]

    def capture_stage_directory() -> None:
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(named.st_mode) or named.st_uid != os.geteuid():
            raise QualificationError("new staged directory named inode differs")
        record["inode"] = _stable_directory_inode(named)
        if (unbound is not None
                and unbound in capsule.unbound_created_directories):
            _clear_unbound_created_directory(capsule, unbound)

    previous_signals = _block_tracking_signals()
    try:
        try:
            os.mkdir(name, 0o700, dir_fd=parent_fd)
        except OSError as error:
            raise QualificationError(
                "cannot create staged directory %s"
                % (relative_path or root_id)
            ) from error
        unbound = _record_unbound_created_directory(
            capsule, parent_fd, name,
            "stage-directory-" + event_name,
        )
        capsule.stage_directories[key] = record
        capture_stage_directory()
    finally:
        _restore_tracking_signals(previous_signals)
    _prebind_mkdir_fault(
        fault_injector,
        "after-stage-directory-mkdir-before-bind:" + event_name,
        capture_stage_directory,
    )
    capture_stage_directory()
    _phase_b_fault(fault_injector, "after-stage-directory-mkdir:" + event_name)
    previous_signals = _block_tracking_signals()
    try:
        fd, identity = _open_bound_directory_at(
            parent_fd, name, 0o700, "staged directory " + event_name,
            normalize_mode=True, expected_inode=record["inode"],
        )
        record["fd"] = fd
        record["inode"] = identity
    finally:
        _restore_tracking_signals(previous_signals)
    _phase_b_fault(fault_injector, "after-stage-directory-open:" + event_name)
    return record


def _validated_blob(entry: Mapping[str, Any],
                    blob_reader: Callable[[Mapping[str, Any]], bytes]) -> bytes:
    try:
        raw = blob_reader(entry)
    except Exception as error:
        raise QualificationError("authenticated Git blob reader failed") from error
    if type(raw) is not bytes:
        raise QualificationError("authenticated Git blob reader returned non-bytes")
    if (len(raw) != entry["bytes"] or len(raw) > 4 << 20
            or _sha256(raw) != entry["sha256"]
            or _git_blob_oid(raw) != entry["git_oid"]):
        raise QualificationError("Git blob size, SHA-1, or SHA-256 differs")
    return raw


def _materialize_stage_file(
        capsule: _PhaseBCapsule, entry: Mapping[str, Any], parent_fd: int,
        raw: bytes, fault_injector: Optional[Callable[[str], None]]) -> None:
    path_label = entry["root_id"] + "/" + entry["relative_path"]
    flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | _required_open_flag("O_CLOEXEC") | _required_open_flag("O_NOFOLLOW")
    )
    fd = -1
    record = None  # type: Optional[Dict[str, Any]]
    previous_signals = _block_tracking_signals()
    try:
        try:
            try:
                fd = os.open(
                    entry["basename"], flags, 0o600, dir_fd=parent_fd,
                )
            except OSError as error:
                raise QualificationError(
                    "cannot create exact staged file " + path_label
                ) from error
            record = {
                "root_id": entry["root_id"],
                "relative_path": entry["relative_path"],
                "parent_fd": parent_fd,
                "name": entry["basename"],
                "inode": None,
                "bytes": entry["bytes"],
                "sha256": entry["sha256"],
                "mode": entry["materialized_mode"],
                "fd": fd,
                "unlinked": False,
                "parent_fsynced_after_unlink": False,
                "absence_proved_after_unlink": False,
                "cleanup_retired": False,
                "descriptor_closed_after_materialize": False,
                "descriptor_closed_after_recovery": False,
            }
            capsule.stage_files.append(record)

            def capture_file_inode() -> None:
                opened = os.fstat(fd)
                named = os.stat(
                    entry["basename"], dir_fd=parent_fd,
                    follow_symlinks=False,
                )
                identity = _stable_file_inode(opened)
                if (not stat.S_ISREG(opened.st_mode)
                        or opened.st_uid != os.geteuid()
                        or opened.st_nlink != 1
                        or _stable_file_inode(named) != identity):
                    raise QualificationError(
                        "new staged file identity differs"
                    )
                record["inode"] = identity

            _prebind_mkdir_fault(
                fault_injector,
                "after-stage-file-open-before-fstat:" + path_label,
                capture_file_inode,
            )
            try:
                capture_file_inode()
            except OSError as error:
                raise QualificationError(
                    "staged file first descriptor binding failed"
                ) from error
        finally:
            _restore_tracking_signals(previous_signals)
        _phase_b_fault(fault_injector, "after-stage-file-create:" + path_label)
        _write_all(fd, raw, "staged file " + path_label)
        _phase_b_fault(fault_injector, "after-stage-file-write:" + path_label)
        os.fchmod(fd, entry["materialized_mode"])
        os.fsync(fd)
        _phase_b_fault(fault_injector, "after-stage-file-fsync:" + path_label)
        final = os.fstat(fd)
        named_final = os.stat(entry["basename"], dir_fd=parent_fd, follow_symlinks=False)
        if (not stat.S_ISREG(final.st_mode) or final.st_nlink != 1
                or final.st_uid != os.geteuid()
                or stat.S_IMODE(final.st_mode) != entry["materialized_mode"]
                or final.st_size != entry["bytes"]
                or _stable_file_inode(final) != record["inode"]
                or _stable_file_inode(named_final) != record["inode"]):
            raise QualificationError("staged file terminal identity differs")
    finally:
        if record is not None and record["inode"] is not None and fd >= 0:
            _close_tracked_record_fd(
                record, "descriptor_closed_after_materialize",
                "materialized staged file " + path_label,
            )


def _reauthenticate_staged_file(record: Mapping[str, Any]) -> None:
    flags = os.O_RDONLY | _required_open_flag("O_CLOEXEC") | _required_open_flag("O_NOFOLLOW")
    def open_file() -> int:
        try:
            return os.open(
                record["name"], flags, dir_fd=record["parent_fd"],
            )
        except OSError as error:
            raise QualificationError("cannot reopen staged file") from error

    def authenticate(fd: int) -> None:
        before = os.fstat(fd)
        named_before = os.stat(record["name"], dir_fd=record["parent_fd"], follow_symlinks=False)
        if (_stable_file_inode(before) != record["inode"]
                or _stable_file_inode(named_before) != record["inode"]
                or not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != record["mode"]
                or before.st_size != record["bytes"]):
            raise QualificationError("staged file identity changed before readback")
        raw = _read_fd_exact(fd, before.st_size, 4 << 20, "staged file readback")
        after = os.fstat(fd)
        named_after = os.stat(record["name"], dir_fd=record["parent_fd"], follow_symlinks=False)
        if (_stat_identity(before) != _stat_identity(after)
                or _stat_identity(after) != _stat_identity(named_after)
                or _sha256(raw) != record["sha256"]):
            raise QualificationError("staged file content or identity changed during readback")

    _operate_on_ephemeral_fd(
        open_file, authenticate, "staged file reauthentication",
    )


def _phase_b_plan_entries(
        contract: Mapping[str, Any], plan: Mapping[str, Any],
) -> Tuple[List[Mapping[str, Any]], Dict[Tuple[str, str], Mapping[str, Any]]]:
    roots = plan.get("roots")
    materialization = plan.get("materialization_entries")
    if type(roots) is not list or not roots or type(materialization) is not list or not materialization:
        raise QualificationError("Phase B plan arrays are absent")
    root_ids = [root.get("root_id") for root in roots]
    if root_ids != list(EXPECTED_ROOT_ORDER[:len(root_ids)]) or len(set(root_ids)) != len(root_ids):
        raise QualificationError("Phase B plan root order differs")
    typed = {}  # type: Dict[Tuple[str, str], Mapping[str, Any]]
    for root in roots:
        entries = root.get("typed_entries")
        if type(entries) is not list or not entries:
            raise QualificationError("Phase B root typed entries are absent")
        derive_mounted_root_tree_sha256(
            contract, root["root_id"], entries,
        )
        for entry in entries:
            key = (entry["root_id"], entry["relative_path"])
            if key in typed:
                raise QualificationError("Phase B typed entry is duplicated")
            typed[key] = entry
    seen_files = set()  # type: Set[Tuple[str, str]]
    for entry in materialization:
        required = {
            "basename", "bytes", "git_mode", "git_oid", "materialized_mode",
            "parent_relative_path", "path_components", "relative_path",
            "root_id", "sha256",
        }
        _exact_keys(entry, required, "Phase B materialization entry")
        key = (entry["root_id"], entry["relative_path"])
        typed_entry = typed.get(key)
        if (key in seen_files or typed_entry is None or typed_entry.get("entry_type") != "regular"):
            raise QualificationError("Phase B materialization entry differs from typed plan")
        seen_files.add(key)
        parts = _source_path(entry["relative_path"])
        if (entry["path_components"] != list(parts)
                or entry["basename"] != parts[-1]
                or entry["parent_relative_path"] != "/".join(parts[:-1])
                or typed_entry["bytes"] != entry["bytes"]
                or typed_entry["sha256"] != entry["sha256"]
                or typed_entry["mode"] != entry["materialized_mode"]):
            raise QualificationError("Phase B materialization path or content binding differs")
    typed_files = {key for key, entry in typed.items() if entry["entry_type"] == "regular"}
    if seen_files != typed_files:
        raise QualificationError("Phase B materialization file closure differs")
    return materialization, typed


def _materialize_phase_b_stage(
        capsule: _PhaseBCapsule, contract: Mapping[str, Any], plan: Mapping[str, Any],
        blob_reader: Callable[[Mapping[str, Any]], bytes],
        fault_injector: Optional[Callable[[str], None]] = None,
        failure_after_files: Optional[int] = None) -> Dict[str, Any]:
    if not callable(blob_reader):
        raise QualificationError("Phase B blob reader is not callable")
    if failure_after_files is not None and (type(failure_after_files) is not int or failure_after_files < 0):
        raise QualificationError("Phase B file failure threshold is invalid")
    materialization, typed = _phase_b_plan_entries(contract, plan)
    stage_fd = capsule.child_fds.get("stage.work", -1)
    if stage_fd < 0 or _directory_names(stage_fd, 1, "new stage.work"):
        raise QualificationError("stage.work is unavailable or nonempty")

    roots = plan["roots"]
    for root in roots:
        root_id = root["root_id"]
        _create_stage_directory(
            capsule, root_id, "", stage_fd, root_id, fault_injector,
        )
        directory_entries = [
            entry for entry in root["typed_entries"] if entry["entry_type"] == "directory"
        ]
        directory_entries.sort(
            key=lambda item: (
                len(item["relative_path"].split("/")),
                item["relative_path"].encode("utf-8"),
            )
        )
        for entry in directory_entries:
            relative_path = entry["relative_path"]
            parts = relative_path.split("/")
            parent_path = "/".join(parts[:-1])
            parent = capsule.stage_directories.get((root_id, parent_path))
            if parent is None or parent["fd"] < 0:
                raise QualificationError("staged directory parent binding is absent")
            _create_stage_directory(
                capsule, root_id, relative_path, parent["fd"], parts[-1],
                fault_injector,
            )

    completed_files = 0
    if failure_after_files == 0:
        raise PhaseBInjectedFailure("injected before first staged file")
    for entry in materialization:
        parent = capsule.stage_directories.get((
            entry["root_id"], entry["parent_relative_path"],
        ))
        if parent is None or parent["fd"] < 0:
            raise QualificationError("staged file parent binding is absent")
        raw = _validated_blob(entry, blob_reader)
        _phase_b_fault(
            fault_injector,
            "after-blob-verify:" + entry["root_id"] + "/" + entry["relative_path"],
        )
        _materialize_stage_file(
            capsule, entry, parent["fd"], raw, fault_injector,
        )
        completed_files += 1
        if failure_after_files is not None and completed_files >= failure_after_files:
            raise PhaseBInjectedFailure("injected after %d staged files" % completed_files)

    for record in capsule.stage_files:
        _reauthenticate_staged_file(record)
    directories = sorted(
        capsule.stage_directories.values(),
        key=lambda record: (
            -_stage_directory_depth(record["root_id"], record["relative_path"]),
            record["root_id"].encode("utf-8"),
            record["relative_path"].encode("utf-8"),
        ),
    )
    for record in directories:
        fd = record["fd"]
        if fd < 0:
            raise QualificationError("staged directory descriptor is absent before fsync")
        os.fsync(fd)
        os.fchmod(fd, 0o555)
        os.fsync(fd)
        _phase_b_fault(
            fault_injector,
            "after-stage-directory-fsync:" + _stage_event_name(
                record["root_id"], record["relative_path"],
            ),
        )
        opened = os.fstat(fd)
        named = os.stat(record["name"], dir_fd=record["parent_fd"], follow_symlinks=False)
        if (stat.S_IMODE(opened.st_mode) != 0o555
                or _stable_directory_inode(opened) != record["inode"]
                or _stable_directory_inode(named) != record["inode"]):
            raise QualificationError("staged directory final mode or inode differs")
    os.fsync(stage_fd)
    _phase_b_fault(fault_injector, "after-stage-work-fsync")
    return {
        "regular_file_count": completed_files,
        "stage_directory_count": len(capsule.stage_directories),
        "typed_entry_count": len(typed),
    }


def _scan_staged_regular(
        parent_fd: int, name: str, expected: Mapping[str, Any],
        inode_set: Set[Tuple[int, int]], expected_device: int) -> Dict[str, Any]:
    flags = os.O_RDONLY | _required_open_flag("O_CLOEXEC") | _required_open_flag("O_NOFOLLOW")
    def open_file() -> int:
        try:
            return os.open(name, flags, dir_fd=parent_fd)
        except OSError as error:
            raise QualificationError("cannot scan staged regular file") from error

    def scan(fd: int) -> Dict[str, Any]:
        before = os.fstat(fd)
        named_before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        inode_key = (before.st_dev, before.st_ino)
        if (not stat.S_ISREG(before.st_mode) or before.st_dev != expected_device
                or before.st_uid != os.geteuid() or before.st_nlink != 1
                or inode_key in inode_set
                or stat.S_IMODE(before.st_mode) != expected["mode"]
                or before.st_size != expected["bytes"]
                or _stable_file_inode(before) != _stable_file_inode(named_before)):
            raise QualificationError("staged regular file type, link, owner, mode, size, or inode differs")
        if getattr(before, "st_flags", 0) != 0:
            raise QualificationError("staged regular file has BSD flags")
        inode_set.add(inode_key)
        raw = _read_fd_exact(fd, before.st_size, 4 << 20, "staged inventory regular")
        after = os.fstat(fd)
        named_after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if (_stat_identity(before) != _stat_identity(after)
                or _stat_identity(after) != _stat_identity(named_after)
                or _sha256(raw) != expected["sha256"]):
            raise QualificationError("staged regular file changed during inventory")
        return {
            "root_id": expected["root_id"],
            "relative_path": expected["relative_path"],
            "entry_type": "regular",
            "mode": stat.S_IMODE(after.st_mode),
            "bytes": len(raw),
            "sha256": _sha256(raw),
        }

    return _operate_on_ephemeral_fd(
        open_file, scan, "staged regular-file scan",
    )


def _scan_staged_root(
        capsule: _PhaseBCapsule, root: Mapping[str, Any],
        inode_set: Set[Tuple[int, int]], expected_device: int) -> List[Dict[str, Any]]:
    root_id = root["root_id"]
    expected_entries = root["typed_entries"]
    expected_by_path = {entry["relative_path"]: entry for entry in expected_entries}
    child_registry = {}  # type: Dict[str, Dict[str, Mapping[str, Any]]]
    for entry in expected_entries[1:]:
        parts = entry["relative_path"].split("/")
        parent_path = "/".join(parts[:-1])
        child_registry.setdefault(parent_path, {})[parts[-1]] = entry
    root_record = capsule.stage_directories.get((root_id, ""))
    if root_record is None:
        raise QualificationError("staged root descriptor record is absent")
    observed = []  # type: List[Dict[str, Any]]
    folded = set()  # type: Set[str]

    def walk(relative_path: str, directory_fd: int, expected: Mapping[str, Any]) -> None:
        info = os.fstat(directory_fd)
        inode_key = (info.st_dev, info.st_ino)
        if (not stat.S_ISDIR(info.st_mode) or info.st_dev != expected_device
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o555
                or inode_key in inode_set or getattr(info, "st_flags", 0) != 0):
            raise QualificationError("staged directory type, owner, mode, flags, or inode differs")
        inode_set.add(inode_key)
        expected_children = child_registry.get(relative_path, {})
        names = _directory_names(
            directory_fd, len(expected_children),
            "staged inventory directory " + root_id + "/" + relative_path,
        )
        if set(names) != set(expected_children) or not names:
            raise QualificationError("staged directory child inventory differs or is empty")
        if len({name.casefold() for name in names}) != len(names):
            raise QualificationError("staged directory contains a casefold collision")
        observed.append({
            "root_id": root_id,
            "relative_path": relative_path,
            "entry_type": "root" if relative_path == "" else "directory",
            "mode": 0o555,
            "bytes": 0,
            "sha256": None,
        })
        for name in sorted(names, key=lambda value: value.encode("utf-8")):
            child_expected = expected_children[name]
            child_path = child_expected["relative_path"]
            if child_path.casefold() in folded:
                raise QualificationError("staged root contains a casefold path collision")
            folded.add(child_path.casefold())
            named = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if child_expected["entry_type"] == "directory":
                if not stat.S_ISDIR(named.st_mode):
                    raise QualificationError("staged expected directory is special or linked")
                flags = os.O_RDONLY | _required_open_flag("O_CLOEXEC") | _required_open_flag("O_DIRECTORY") | _required_open_flag("O_NOFOLLOW")
                def open_child() -> int:
                    return os.open(name, flags, dir_fd=directory_fd)

                def scan_child(child_fd: int) -> None:
                    child_info = os.fstat(child_fd)
                    if _stable_directory_inode(child_info) != _stable_directory_inode(named):
                        raise QualificationError("staged directory changed before scan")
                    walk(child_path, child_fd, child_expected)
                    named_after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if _stable_directory_inode(os.fstat(child_fd)) != _stable_directory_inode(named_after):
                        raise QualificationError("staged directory changed during scan")

                _operate_on_ephemeral_fd(
                    open_child, scan_child,
                    "staged inventory directory " + root_id + "/" + child_path,
                )
            elif child_expected["entry_type"] == "regular":
                if not stat.S_ISREG(named.st_mode):
                    raise QualificationError("staged expected regular is special or linked")
                observed.append(_scan_staged_regular(
                    directory_fd, name, child_expected, inode_set, expected_device,
                ))
            else:
                raise QualificationError("staged child entry type differs")

    def open_root() -> int:
        root_fd, _ = _open_bound_directory_at(
            capsule.child_fds["stage.work"], root_id, 0o555,
            "staged inventory root " + root_id,
            expected_inode=root_record["inode"],
        )
        return root_fd

    def scan_root(root_fd: int) -> List[Dict[str, Any]]:
        walk("", root_fd, expected_by_path[""])
        ordered = [observed[0]] + sorted(
            observed[1:],
            key=lambda item: item["relative_path"].encode("utf-8"),
        )
        if ordered != expected_entries:
            raise QualificationError(
                "staged root typed inventory differs from exact plan"
            )
        return ordered

    return _operate_on_ephemeral_fd(
        open_root, scan_root, "staged inventory root " + root_id,
    )


def _scan_phase_b_stage(
        capsule: _PhaseBCapsule, contract: Mapping[str, Any],
        plan: Mapping[str, Any], require_exact_counts: bool = True) -> Dict[str, Any]:
    _phase_b_plan_entries(contract, plan)
    stage_fd = capsule.child_fds.get("stage.work", -1)
    if stage_fd < 0:
        raise QualificationError("stage.work descriptor is absent")
    stage_info = os.fstat(stage_fd)
    if (not stat.S_ISDIR(stage_info.st_mode) or stage_info.st_uid != os.geteuid()
            or stat.S_IMODE(stage_info.st_mode) != 0o700):
        raise QualificationError("stage.work identity differs")
    roots = plan["roots"]
    root_ids = [root["root_id"] for root in roots]
    if set(_directory_names(stage_fd, len(root_ids), "stage.work inventory")) != set(root_ids):
        raise QualificationError("stage.work root inventory differs")
    inode_set = {(stage_info.st_dev, stage_info.st_ino)}
    staged_entries = []  # type: List[Dict[str, Any]]
    root_digests = []  # type: List[Dict[str, Any]]
    for root in roots:
        entries = _scan_staged_root(
            capsule, root, inode_set, stage_info.st_dev,
        )
        root_digest = derive_mounted_root_tree_sha256(
            contract, root["root_id"], entries,
        )
        if root_digest != root["mounted_root_tree_sha256"]:
            raise QualificationError("staged root mounted-domain digest differs")
        root_digests.append({"root_id": root["root_id"], "sha256": root_digest})
        staged_entries.extend(entries)
    staged_sha = derive_staged_tree_sha256(contract, staged_entries)
    mounted_sha = derive_mounted_tree_sha256(contract, root_digests)
    if (staged_sha != plan["staged_tree_sha256"]
            or mounted_sha != plan["mounted_tree_sha256"]
            or len(staged_entries) != plan["typed_entry_count"]):
        raise QualificationError("complete staged inventory digest or count differs")
    if require_exact_counts and (
            len(capsule.stage_files) != 477
            or len(capsule.stage_directories) != 71
            or len(staged_entries) != 548):
        raise QualificationError("production Phase B exact counts differ")
    for record in capsule.stage_directories.values():
        fd = record["fd"]
        named = os.stat(record["name"], dir_fd=record["parent_fd"], follow_symlinks=False)
        if (fd < 0 or _stable_directory_inode(os.fstat(fd)) != record["inode"]
                or _stable_directory_inode(named) != record["inode"]):
            raise QualificationError("retained staged directory identity differs after scan")
    return {
        "complete": True,
        "regular_file_count": len(capsule.stage_files),
        "stage_directory_count": len(capsule.stage_directories),
        "typed_entry_count": len(staged_entries),
        "staged_tree_sha256": staged_sha,
        "mounted_tree_sha256": mounted_sha,
        "mounted_root_digests": root_digests,
    }


def _reopen_stage_directory_record(record: Dict[str, Any]) -> int:
    fd = record.get("fd", -1)
    if type(fd) is int and fd >= 0:
        if _stable_directory_inode(os.fstat(fd)) != record["inode"]:
            raise CleanupError("retained staged directory inode differs")
        return fd
    flags = os.O_RDONLY | _required_open_flag("O_CLOEXEC") | _required_open_flag("O_DIRECTORY") | _required_open_flag("O_NOFOLLOW")
    previous_signals = _block_tracking_signals()
    owned = []  # type: List[int]
    primary_error = None  # type: Optional[BaseException]
    try:
        try:
            fd = os.open(
                record["name"], flags, dir_fd=record["parent_fd"],
            )
        except OSError as error:
            raise CleanupError(
                "cannot reopen partial staged directory"
            ) from error
        owned.append(fd)
        opened = os.fstat(fd)
        named = os.stat(
            record["name"], dir_fd=record["parent_fd"], follow_symlinks=False,
        )
        if (_stable_directory_inode(opened) != record["inode"]
                or _stable_directory_inode(named) != record["inode"]):
            raise CleanupError("partial staged directory inode differs")
        record["fd"] = fd
    except BaseException as error:
        primary_error = error
    if primary_error is None:
        try:
            _restore_tracking_signals(previous_signals)
        except BaseException as error:
            primary_error = error
        else:
            return fd

    def retire_reopened_descriptor() -> None:
        if record.get("fd") == fd:
            record["fd"] = -1

    _cleanup_owned_descriptors_and_raise(
        owned, previous_signals, primary_error,
        "partial staged directory", retire_reopened_descriptor,
    )


def _cleanup_materialized_stage(
        capsule: _PhaseBCapsule,
        fault_observer: Optional[Callable[[str], None]] = None,
) -> Tuple[int, int]:
    directories_top_down = sorted(
        capsule.stage_directories.values(),
        key=lambda record: (
            _stage_directory_depth(record["root_id"], record["relative_path"]),
            record["root_id"].encode("utf-8"),
            record["relative_path"].encode("utf-8"),
        ),
    )
    for record in directories_top_down:
        if record["removed"]:
            continue
        fd = _reopen_stage_directory_record(record)
        event = _stage_event_name(record["root_id"], record["relative_path"])
        if fault_observer is not None:
            fault_observer("before-cleanup-directory-fchmod:" + event)
        os.fchmod(fd, 0o700)
        if fault_observer is not None:
            fault_observer("after-cleanup-directory-fchmod:" + event)
            fault_observer("before-cleanup-directory-fsync:" + event)
        os.fsync(fd)
        if fault_observer is not None:
            fault_observer("after-cleanup-directory-fsync:" + event)
        named = os.stat(record["name"], dir_fd=record["parent_fd"], follow_symlinks=False)
        if (_stable_directory_inode(os.fstat(fd)) != record["inode"]
                or _stable_directory_inode(named) != record["inode"]):
            raise CleanupError("staged directory changed before cleanup")

    for record in reversed(capsule.stage_files):
        if record["cleanup_retired"]:
            continue
        event = record["root_id"] + "/" + record["relative_path"]
        if record["inode"] is None:
            retained_fd = record.get("fd", -1)
            if type(retained_fd) is not int or retained_fd < 0:
                raise CleanupError("unbound staged file has no retained descriptor")
            opened = os.fstat(retained_fd)
            named = os.stat(record["name"], dir_fd=record["parent_fd"], follow_symlinks=False)
            identity = _stable_file_inode(opened)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                    or opened.st_uid != os.geteuid()
                    or _stable_file_inode(named) != identity):
                raise CleanupError("unbound staged file recovery identity differs")
            record["inode"] = identity
        if record.get("fd", -1) >= 0:
            _close_tracked_record_fd(
                record, "descriptor_closed_after_recovery",
                "recovered staged file " + event, fault_observer, event,
            )
        if not record["unlinked"]:
            named = os.stat(record["name"], dir_fd=record["parent_fd"], follow_symlinks=False)
            if (not stat.S_ISREG(named.st_mode) or named.st_nlink != 1
                    or _stable_file_inode(named) != record["inode"]):
                raise CleanupError("staged file exact-inode cleanup guard failed")
            flags = os.O_RDONLY | _required_open_flag("O_CLOEXEC") | _required_open_flag("O_NOFOLLOW")
            def open_guard() -> int:
                return os.open(
                    record["name"], flags, dir_fd=record["parent_fd"],
                )

            def verify_guard(fd: int) -> None:
                if _stable_file_inode(os.fstat(fd)) != record["inode"]:
                    raise CleanupError("staged file changed before unlink")

            _operate_on_ephemeral_fd(
                open_guard, verify_guard, "staged file unlink guard",
            )
            if fault_observer is not None:
                fault_observer("before-cleanup-file-unlink:" + event)
            previous_signals = _block_tracking_signals()
            try:
                try:
                    os.unlink(record["name"], dir_fd=record["parent_fd"])
                except BaseException:
                    if _name_absent(record["parent_fd"], record["name"]):
                        record["unlinked"] = True
                    raise
                record["unlinked"] = True
            finally:
                _restore_tracking_signals(previous_signals)
            if fault_observer is not None:
                fault_observer("after-cleanup-file-unlink:" + event)
        if not record["parent_fsynced_after_unlink"]:
            if fault_observer is not None:
                fault_observer("before-cleanup-file-parent-fsync:" + event)
            os.fsync(record["parent_fd"])
            record["parent_fsynced_after_unlink"] = True
            if fault_observer is not None:
                fault_observer("after-cleanup-file-parent-fsync:" + event)
        if not record["absence_proved_after_unlink"]:
            if fault_observer is not None:
                fault_observer("before-cleanup-file-absence-proof:" + event)
            absent = _name_absent(record["parent_fd"], record["name"])
            if not absent:
                raise CleanupError("staged file name remains after unlink")
            record["absence_proved_after_unlink"] = True
            if fault_observer is not None:
                fault_observer("after-cleanup-file-absence-proof:" + event)
        record["cleanup_retired"] = True

    directories_bottom_up = list(reversed(directories_top_down))
    for record in directories_bottom_up:
        if record["cleanup_retired"]:
            continue
        event = _stage_event_name(record["root_id"], record["relative_path"])
        if not record["removed"]:
            fd = _reopen_stage_directory_record(record)
            if _directory_names(fd, 0, "empty staged directory cleanup"):
                raise CleanupError("staged directory remains nonempty")
            named = os.stat(record["name"], dir_fd=record["parent_fd"], follow_symlinks=False)
            if (_stable_directory_inode(os.fstat(fd)) != record["inode"]
                    or _stable_directory_inode(named) != record["inode"]):
                raise CleanupError("staged directory exact-inode rmdir guard failed")
            if fault_observer is not None:
                fault_observer("before-cleanup-stage-rmdir:" + event)
            previous_signals = _block_tracking_signals()
            try:
                try:
                    os.rmdir(record["name"], dir_fd=record["parent_fd"])
                except BaseException:
                    if _name_absent(record["parent_fd"], record["name"]):
                        record["removed"] = True
                    raise
                record["removed"] = True
            finally:
                _restore_tracking_signals(previous_signals)
        if not record.get("descriptor_closed_after_remove", False):
            _close_tracked_record_fd(
                record, "descriptor_closed_after_remove",
                "removed staged directory " + event, fault_observer, event,
            )
        if fault_observer is not None:
            fault_observer("after-cleanup-stage-rmdir:" + event)
        if not record["parent_fsynced_after_remove"]:
            if fault_observer is not None:
                fault_observer("before-cleanup-stage-parent-fsync:" + event)
            os.fsync(record["parent_fd"])
            record["parent_fsynced_after_remove"] = True
            if fault_observer is not None:
                fault_observer("after-cleanup-stage-parent-fsync:" + event)
        if not record["absence_proved_after_remove"]:
            if fault_observer is not None:
                fault_observer("before-cleanup-stage-absence-proof:" + event)
            absent = _name_absent(record["parent_fd"], record["name"])
            if not absent:
                raise CleanupError("staged directory name remains after rmdir")
            record["absence_proved_after_remove"] = True
            if fault_observer is not None:
                fault_observer("after-cleanup-stage-absence-proof:" + event)
        record["cleanup_retired"] = True
    stage_fd = capsule.child_fds.get("stage.work", -1)
    if stage_fd < 0 or _directory_names(stage_fd, 0, "empty stage.work cleanup"):
        raise CleanupError("stage.work remains nonempty")
    removed_files = sum(1 for record in capsule.stage_files if record["cleanup_retired"])
    removed_directories = sum(
        1 for record in capsule.stage_directories.values() if record["cleanup_retired"]
    )
    return removed_files, removed_directories


def _synthetic_phase_b_plan(
        contract: Mapping[str, Any], raw: bytes,
) -> Tuple[Dict[str, Any], Callable[[Mapping[str, Any]], bytes]]:
    root_id = "common-source"
    relative_path = "fixture/hello.txt"
    digest = _sha256(raw)
    oid = _git_blob_oid(raw)
    typed = [
        {
            "root_id": root_id, "relative_path": "", "entry_type": "root",
            "mode": 0o555, "bytes": 0, "sha256": None,
        },
        {
            "root_id": root_id, "relative_path": "fixture", "entry_type": "directory",
            "mode": 0o555, "bytes": 0, "sha256": None,
        },
        {
            "root_id": root_id, "relative_path": relative_path, "entry_type": "regular",
            "mode": 0o444, "bytes": len(raw), "sha256": digest,
        },
    ]
    root_digest = derive_mounted_root_tree_sha256(contract, root_id, typed)
    root_digests = [{"root_id": root_id, "sha256": root_digest}]
    materialization = [{
        "root_id": root_id,
        "relative_path": relative_path,
        "path_components": ["fixture", "hello.txt"],
        "parent_relative_path": "fixture",
        "basename": "hello.txt",
        "git_oid": oid,
        "git_mode": "100644",
        "materialized_mode": 0o444,
        "bytes": len(raw),
        "sha256": digest,
    }]
    plan = {
        "schema_version": "dancers-planar-physics-iso-source-capsule-synthetic-phase-b-plan-v1",
        "root_order": [root_id],
        "regular_file_count": 1,
        "implicit_directory_count": 1,
        "root_record_count": 1,
        "typed_entry_count": 3,
        "total_regular_bytes": len(raw),
        "staged_tree_sha256": derive_staged_tree_sha256(contract, typed),
        "mounted_tree_sha256": derive_mounted_tree_sha256(contract, root_digests),
        "mounted_root_digests": root_digests,
        "roots": [{
            "root_id": root_id,
            "mounted_root_tree_sha256": root_digest,
            "regular_file_count": 1,
            "implicit_directory_count": 1,
            "tree_entry_count": 3,
            "total_regular_bytes": len(raw),
            "typed_entries": typed,
        }],
        "materialization_entries": materialization,
    }

    def reader(entry: Mapping[str, Any]) -> bytes:
        if entry.get("git_oid") != oid or entry.get("sha256") != digest:
            raise QualificationError("synthetic blob reader received another identity")
        return raw

    return plan, reader


def exercise_synthetic_phase_b(
        failure_event: Optional[str] = None,
        failure_after_files: Optional[int] = None) -> Dict[str, Any]:
    """Exercise Phase B on one tiny injected blob and always reclaim its P.

    This API never reads a real Git blob, invokes a subprocess, creates an
    image, or reaches a mount/device interface.  Its result is test evidence,
    never a qualification receipt.
    """

    if failure_event is not None and (type(failure_event) is not str or not failure_event):
        raise QualificationError("synthetic Phase B failure event is invalid")
    repository = _repository_root()
    contract_raw = _read_authenticated_repository_file(
        repository, CONTRACT_PATH, CONTRACT_BYTES, CONTRACT_SHA256,
        "ISO qualification contract",
    )
    contract = _parse_canonical_json(contract_raw, CONTRACT_BYTES)
    _self_check_hash_vectors(contract)
    raw = b"bounded synthetic Phase B blob\n"
    plan, reader = _synthetic_phase_b_plan(contract, raw)
    seen = []  # type: List[str]

    def fault(event: str) -> None:
        seen.append(event)
        if failure_event == event:
            raise PhaseBInjectedFailure("synthetic fault at " + event)

    capsule = None  # type: Optional[_PhaseBCapsule]
    failed = None  # type: Optional[str]
    inventory = None  # type: Optional[Dict[str, Any]]
    cleanup = None  # type: Optional[Dict[str, Any]]
    creation_cleanup_confirmed = False
    try:
        previous_signals = _block_tracking_signals()
        try:
            capsule = _create_private_capsule(fault)
        finally:
            _restore_tracking_signals(previous_signals)
        _materialize_phase_b_stage(
            capsule, contract, plan, reader, fault,
            failure_after_files=failure_after_files,
        )
        inventory = _scan_phase_b_stage(
            capsule, contract, plan, require_exact_counts=False,
        )
    except QualificationError as error:
        failed = type(error).__name__ + ": " + str(error)
        if capsule is None and not isinstance(error, CleanupError):
            # _create_private_capsule propagates its primary error only after
            # its own exact cleanup succeeds; cleanup failure is retyped.
            creation_cleanup_confirmed = True
    finally:
        if capsule is not None and not capsule.cleaned:
            cleanup = _cleanup_phase_b_capsule(
                capsule, require_exact_counts=False, fault_injector=fault,
            )
    if creation_cleanup_confirmed:
        cleanup = {
            "complete": True,
            "private_parent_absent": True,
            "stage_files_removed": 0,
            "stage_directories_removed": 0,
            "capsule_directories_removed": None,
            "synthetic_injected_cleanup_faults": [],
        }
    if (cleanup is not None
            and cleanup.get("synthetic_injected_cleanup_faults")
            and failed is None):
        failed = "PhaseBInjectedFailure: synthetic cleanup boundary fault"
    if failure_event is not None and failure_event not in seen:
        raise QualificationError("requested synthetic Phase B fault event was not reached")
    if failure_after_files is None and failure_event is None and failed is not None:
        raise QualificationError("synthetic Phase B success path failed: " + failed)
    if cleanup is None or cleanup.get("complete") is not True:
        raise CleanupError("synthetic Phase B did not complete cleanup")
    return {
        "schema_version": "dancers-planar-physics-iso-source-capsule-synthetic-phase-b-result-v1",
        "completed": failed is None,
        "failure": failed,
        "events": seen,
        "inventory": inventory,
        "cleanup": cleanup,
        "image_created": False,
        "subprocess_invoked": False,
    }


def _read_authenticated_repository_file(repository: Path, relative_path: str,
                                        expected_bytes: int, expected_sha256: str,
                                        label: str) -> bytes:
    parts = _strict_relative_path(relative_path, label + " path")
    absolute_fds = []  # type: List[int]
    absolute_records = []  # type: List[Tuple[int, str, int, Tuple[int, ...], Tuple[int, ...]]]
    opened_directories = []  # type: List[int]
    owned_descriptors = []  # type: List[int]
    relative_records = []  # type: List[Tuple[int, str, int, Tuple[int, ...], Tuple[int, ...]]]
    flags_dir = os.O_RDONLY | _required_open_flag("O_CLOEXEC") | _required_open_flag("O_DIRECTORY") | _required_open_flag("O_NOFOLLOW")
    flags_file = os.O_RDONLY | _required_open_flag("O_CLOEXEC") | _required_open_flag("O_NOFOLLOW")
    previous_signals = _block_tracking_signals()
    primary_error = None  # type: Optional[BaseException]
    raw = b""
    try:
        absolute_fds, absolute_records = _open_absolute_directory_chain(
            repository, "repository",
        )
        owned_descriptors.extend(absolute_fds)
        root_fd = absolute_fds[-1]
        directory_fd = root_fd
        for component in parts[:-1]:
            before = os.fstat(directory_fd)
            try:
                child_fd = os.open(component, flags_dir, dir_fd=directory_fd)
            except OSError as error:
                raise QualificationError("cannot open %s ancestor" % label) from error
            opened_directories.append(child_fd)
            owned_descriptors.append(child_fd)
            child = os.fstat(child_fd)
            named_child = os.stat(
                component, dir_fd=directory_fd, follow_symlinks=False,
            )
            after = os.fstat(directory_fd)
            if (not stat.S_ISDIR(child.st_mode)
                    or child.st_uid not in (0, os.geteuid())
                    or stat.S_IMODE(child.st_mode) & 0o022
                    or _directory_identity(child) != _directory_identity(named_child)
                    or _directory_identity(before) != _directory_identity(after)):
                raise QualificationError(
                    "%s ancestor identity differs" % label
                )
            relative_records.append((
                directory_fd, component, child_fd,
                _directory_identity(child), _directory_identity(before),
            ))
            directory_fd = child_fd
        parent_before = os.fstat(directory_fd)

        def open_leaf() -> int:
            try:
                return os.open(
                    parts[-1], flags_file, dir_fd=directory_fd,
                )
            except OSError as error:
                raise QualificationError("cannot open %s" % label) from error

        def read_leaf(fd: int) -> bytes:
            before = os.fstat(fd)
            named_before = os.stat(parts[-1], dir_fd=directory_fd, follow_symlinks=False)
            if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                    or before.st_uid != os.geteuid() or before.st_mode & 0o022
                    or before.st_size != expected_bytes
                    or _stat_identity(before) != _stat_identity(named_before)):
                raise QualificationError("%s type, links, owner, mode, or size differs" % label)
            raw = _read_fd_exact(fd, before.st_size, expected_bytes, label)
            after = os.fstat(fd)
            named_after = os.stat(parts[-1], dir_fd=directory_fd, follow_symlinks=False)
            parent_after = os.fstat(directory_fd)
            if (_stat_identity(before) != _stat_identity(after)
                    or _stat_identity(after) != _stat_identity(named_after)
                    or _directory_identity(parent_before) != _directory_identity(parent_after)):
                raise QualificationError("%s changed during authenticated read" % label)
            _reauthenticate_directory_chain(relative_records, label)
            _reauthenticate_directory_chain(absolute_records, label)
            return raw

        raw = _operate_on_ephemeral_fd(
            open_leaf, read_leaf, "authenticated repository file " + label,
        )
    except BaseException as error:
        primary_error = error
    close_report = _close_owned_descriptor_list(
        owned_descriptors, "authenticated repository ancestry " + label,
    )
    try:
        _restore_tracking_signals(previous_signals)
    except BaseException as error:
        if isinstance(error, Exception):
            close_report["ordinary_errors"].append(
                "signal restoration " + type(error).__name__ + ": "
                + str(error)
            )
            close_report["complete"] = False
        else:
            close_report["deferred_base_exceptions"].append(error)
    if (primary_error is not None or close_report["complete"] is not True
            or close_report["ordinary_errors"]
            or close_report["deferred_base_exceptions"]):
        if primary_error is None:
            primary_error = CleanupError(
                "authenticated repository ancestry cleanup failed"
            )
        _raise_after_owned_descriptor_cleanup(
            primary_error, close_report,
            "authenticated repository ancestry " + label,
        )
    if _sha256(raw) != expected_sha256:
        raise QualificationError("%s SHA-256 differs" % label)
    return raw


def _read_authenticated_file(path: Path, expected_bytes: int, expected_sha256: str,
                             label: str) -> bytes:
    """Compatibility wrapper restricted to files below the repository root."""

    repository = _repository_root()
    absolute = Path(os.path.abspath(os.fspath(path)))
    try:
        relative = absolute.relative_to(repository).as_posix()
    except ValueError as error:
        raise QualificationError("%s is outside the authenticated repository" % label) from error
    return _read_authenticated_repository_file(
        repository, relative, expected_bytes, expected_sha256, label,
    )


def _load_authenticated_module(path: Path, raw: bytes, module_name: str) -> types.ModuleType:
    module = types.ModuleType(module_name)
    module.__file__ = os.fspath(path)
    module.__package__ = ""
    sys.modules[module_name] = module
    try:
        exec(compile(raw, os.fspath(path), "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def _strict_json_object(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result = {}  # type: Dict[str, Any]
    for key, value in pairs:
        if key in result:
            raise QualificationError("duplicate JSON object member")
        result[key] = value
    return result


def _reject_json_float(_: str) -> Any:
    raise QualificationError("floating-point JSON is forbidden")


def _reject_json_constant(_: str) -> Any:
    raise QualificationError("nonfinite JSON is forbidden")


def _validate_json_value(value: Any, maximum_nodes: int = 200_000,
                         maximum_depth: int = 32) -> None:
    count = [0]

    def visit(item: Any, depth: int) -> None:
        count[0] += 1
        if count[0] > maximum_nodes or depth > maximum_depth:
            raise QualificationError("JSON node or depth cap exceeded")
        if item is None or type(item) is bool:
            return
        if type(item) is int:
            if item < -(1 << 63) or item >= 1 << 63:
                raise QualificationError("JSON integer exceeds signed 64-bit range")
            return
        if type(item) is str:
            if unicodedata.normalize("NFC", item) != item or any(0xD800 <= ord(ch) <= 0xDFFF for ch in item):
                raise QualificationError("JSON string is not canonical NFC Unicode")
            return
        if type(item) is list:
            for child in item:
                visit(child, depth + 1)
            return
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise QualificationError("JSON object key is not a string")
                visit(key, depth + 1)
                visit(child, depth + 1)
            return
        raise QualificationError("JSON contains a forbidden value type")

    visit(value, 0)


def _parse_canonical_json(raw: bytes, maximum_bytes: int) -> Any:
    if not raw or len(raw) > maximum_bytes or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise QualificationError("canonical JSON file framing differs")
    try:
        value = json.loads(
            raw[:-1].decode("utf-8", "strict"),
            object_pairs_hook=_strict_json_object,
            parse_float=_reject_json_float,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        raise QualificationError("canonical JSON parsing failed") from error
    _validate_json_value(value)
    if _canonical_json(value) + b"\n" != raw:
        raise QualificationError("JSON input is not canonical")
    return value


def _raw32(value: Any) -> bytes:
    if type(value) is not str or HEX64.fullmatch(value) is None:
        raise QualificationError("raw32 value is not lowercase SHA-256 hexadecimal")
    return bytes.fromhex(value)


def _hash_json_sequence(domain_text: str, entries: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256(_domain(domain_text))
    for entry in entries:
        _validate_json_value(entry)
        fragment = _canonical_json(entry)
        digest.update(_framed(fragment))
    return digest.hexdigest()


def _exact_keys(value: Any, keys: Set[str], label: str) -> Mapping[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise QualificationError("%s exact keys differ" % label)
    return value


def _validate_device_ledger_entry(entry: Mapping[str, Any]) -> None:
    _exact_keys(entry, {
        "entities", "image_path_sha256", "is_target_image",
        "target_image_id", "whole_device",
    }, "device-association entry")
    if (HEX64.fullmatch(str(entry["image_path_sha256"])) is None
            or DEVICE_RE.fullmatch(str(entry["whole_device"])) is None
            or type(entry["is_target_image"]) is not bool):
        raise QualificationError("device-association entry identity differs")
    if ((entry["is_target_image"] and (type(entry["target_image_id"]) is not str
                                      or HEX64.fullmatch(entry["target_image_id"]) is None))
            or (not entry["is_target_image"] and entry["target_image_id"] is not None)):
        raise QualificationError("device-association target identity differs")
    entities = entry["entities"]
    if type(entities) is not list or not entities or len(entities) > 4:
        raise QualificationError("device-association entity count differs")
    for entity in entities:
        _exact_keys(entity, {"device", "is_whole", "mountpoint_sha256"}, "device entity")
        mount_hash = entity["mountpoint_sha256"]
        if (DEVICE_RE.fullmatch(str(entity["device"])) is None
                or type(entity["is_whole"]) is not bool
                or (mount_hash is not None
                    and (type(mount_hash) is not str or HEX64.fullmatch(mount_hash) is None))):
            raise QualificationError("device entity identity differs")
    ordered = sorted(
        entities,
        key=lambda item: (
            item["device"].encode("utf-8"),
            b"" if item["mountpoint_sha256"] is None else item["mountpoint_sha256"].encode("ascii"),
        ),
    )
    if entities != ordered or len({_canonical_json(item) for item in entities}) != len(entities):
        raise QualificationError("device entity order or uniqueness differs")


def derive_device_association_ledger_sha256(
        contract: Mapping[str, Any], entries: Sequence[Mapping[str, Any]]) -> str:
    if type(entries) not in (list, tuple):
        raise QualificationError("device-association ledger is not an array")
    for entry in entries:
        _validate_device_ledger_entry(entry)
    ordered = sorted(
        entries,
        key=lambda item: (
            str(item.get("image_path_sha256", "")).encode("ascii"),
            str(item.get("whole_device", "")).encode("utf-8"),
        ),
    )
    if (list(entries) != ordered
            or len({_canonical_json(item) for item in entries}) != len(entries)
            or len(entries) > int(contract["hard_caps"]["device_association_entries_max"])):
        raise QualificationError("device-association ledger order or cap differs")
    return _hash_json_sequence(contract["hash_domains"]["device_association_ledger"], entries)


def derive_mount_ledger_sha256(
        contract: Mapping[str, Any], entries: Sequence[Mapping[str, Any]]) -> str:
    if type(entries) not in (list, tuple):
        raise QualificationError("mount ledger is not an array")
    expected_keys = {
        "fs_type", "is_target", "mount_flags", "mounted_from_sha256",
        "mountpoint_sha256", "statfs_fsid_words", "target_mounted_from",
        "target_mountpoint",
    }
    for entry in entries:
        _exact_keys(entry, expected_keys, "mount-ledger entry")
        words = entry["statfs_fsid_words"]
        if (type(entry["fs_type"]) is not str or not entry["fs_type"]
                or unicodedata.normalize("NFC", entry["fs_type"]) != entry["fs_type"]
                or type(entry["is_target"]) is not bool
                or type(entry["mount_flags"]) is not int or entry["mount_flags"] < 0
                or type(entry["mounted_from_sha256"]) is not str
                or HEX64.fullmatch(entry["mounted_from_sha256"]) is None
                or type(entry["mountpoint_sha256"]) is not str
                or HEX64.fullmatch(entry["mountpoint_sha256"]) is None
                or type(words) is not list or len(words) != 2
                or any(type(word) is not int or word < -(1 << 31) or word >= 1 << 31 for word in words)):
            raise QualificationError("mount-ledger entry identity differs")
        if entry["is_target"]:
            if (DEVICE_RE.fullmatch(str(entry["target_mounted_from"])) is None
                    or type(entry["target_mountpoint"]) is not str
                    or not entry["target_mountpoint"].startswith(PRIVATE_TMP + "/")):
                raise QualificationError("target mount-ledger fields differ")
        elif entry["target_mounted_from"] is not None or entry["target_mountpoint"] is not None:
            raise QualificationError("unrelated mount-ledger entry leaks a raw path")
    ordered = sorted(
        entries,
        key=lambda item: (
            str(item.get("mountpoint_sha256", "")).encode("ascii"),
            str(item.get("mounted_from_sha256", "")).encode("ascii"),
        ),
    )
    if (list(entries) != ordered
            or len({_canonical_json(item) for item in entries}) != len(entries)
            or len(entries) > int(contract["hard_caps"]["mount_ledger_entries_max"])):
        raise QualificationError("mount ledger order or cap differs")
    return _hash_json_sequence(contract["hash_domains"]["mount_ledger"], entries)


def derive_diagnostic_mount_identity_sha256(
        contract: Mapping[str, Any], body: Mapping[str, Any]) -> str:
    _exact_keys(body, {
        "fs_type", "image_id", "media_read_only", "mount_flags",
        "mount_root_device", "mount_root_inode", "mount_root_mode",
        "mount_root_owner_uid", "mountpoint", "statfs_fsid_words",
        "volume_device", "volume_read_only", "whole_device",
    }, "diagnostic mount identity")
    _validate_json_value(body)
    return _sha256(
        _domain(contract["hash_domains"]["diagnostic_mount_identity"])
        + _canonical_json(body)
    )


def derive_mounted_root_tree_sha256(
        contract: Mapping[str, Any], root_id: str,
        entries: Sequence[Mapping[str, Any]]) -> str:
    if root_id not in EXPECTED_ROOT_ORDER or not entries:
        raise QualificationError("mounted-root digest root differs")
    for entry in entries:
        _exact_keys(entry, {
            "bytes", "entry_type", "mode", "relative_path", "root_id", "sha256",
        }, "mounted typed entry")
        if entry["root_id"] != root_id:
            raise QualificationError("mounted typed entry names another root")
    expected_order = [entries[0]] + sorted(
        entries[1:], key=lambda item: str(item.get("relative_path", "")).encode("utf-8")
    )
    if list(entries) != expected_order:
        raise QualificationError("mounted-root typed-entry order differs")
    root_bytes = root_id.encode("utf-8")
    digest = hashlib.sha256(_domain(contract["hash_domains"]["mounted_root_tree"]))
    digest.update(_framed(root_bytes))
    for entry in entries:
        _validate_json_value(entry)
        digest.update(_framed(_canonical_json(entry)))
    return digest.hexdigest()


def derive_mounted_tree_sha256(
        contract: Mapping[str, Any], root_digests: Sequence[Mapping[str, Any]]) -> str:
    root_ids = [item.get("root_id") for item in root_digests]
    if root_ids != list(EXPECTED_ROOT_ORDER[:len(root_ids)]) or not root_ids:
        raise QualificationError("mounted-tree root order differs")
    digest = hashlib.sha256(_domain(contract["hash_domains"]["mounted_tree"]))
    for item in root_digests:
        root_id = str(item["root_id"]).encode("utf-8")
        digest.update(_framed(root_id))
        digest.update(_raw32(item["sha256"]))
    return digest.hexdigest()


def derive_staged_tree_sha256(
        contract: Mapping[str, Any], entries: Sequence[Mapping[str, Any]]) -> str:
    if not entries:
        raise QualificationError("staged tree is empty")
    groups = []  # type: List[Tuple[str, List[Mapping[str, Any]]]]
    for entry in entries:
        root_id = entry.get("root_id")
        if root_id not in EXPECTED_ROOT_ORDER:
            raise QualificationError("staged typed entry root differs")
        if not groups or groups[-1][0] != root_id:
            groups.append((root_id, []))
        groups[-1][1].append(entry)
    group_ids = [item[0] for item in groups]
    if group_ids != list(EXPECTED_ROOT_ORDER[:len(group_ids)]) or len(set(group_ids)) != len(group_ids):
        raise QualificationError("staged tree root order differs")
    for root_id, root_entries in groups:
        # This also freezes exact keys and root-first/path-byte ordering.
        derive_mounted_root_tree_sha256(contract, root_id, root_entries)
    return _hash_json_sequence(contract["hash_domains"]["staged_tree"], entries)


def derive_image_id(contract: Mapping[str, Any], raw_image: bytes) -> str:
    if len(raw_image) < int(contract["hard_caps"]["image_bytes_min"]) or len(raw_image) > int(contract["hard_caps"]["image_bytes_max"]):
        raise QualificationError("image byte count is outside the frozen cap")
    return _sha256(
        _domain(contract["hash_domains"]["image"])
        + _u64be(len(raw_image)) + raw_image
    )


def derive_profile_sha256(contract: Mapping[str, Any], profile: Mapping[str, Any],
                          profile_kind: str) -> str:
    if profile_kind not in {"host_profile", "tool_profile"}:
        raise QualificationError("profile hash kind differs")
    body = dict(profile)
    body.pop("profile_sha256", None)
    _validate_json_value(body)
    return _sha256(_domain(contract["hash_domains"][profile_kind]) + _canonical_json(body))


def derive_qualification_receipt_sha256(
        contract: Mapping[str, Any], receipt: Mapping[str, Any]) -> str:
    body = dict(receipt)
    body.pop("receipt_sha256", None)
    _validate_json_value(body)
    return _sha256(
        _domain(contract["hash_domains"]["qualification_receipt"])
        + _canonical_json(body)
    )


def _self_check_hash_vectors(contract: Mapping[str, Any]) -> None:
    vectors = contract.get("hash_test_vectors")
    if type(vectors) is not dict or set(vectors) != {
            "device_association_ledger", "diagnostic_mount_identity", "mount_ledger",
            "mounted_root_tree", "mounted_tree", "staged_tree"}:
        raise QualificationError("hash test-vector registry differs")
    device = vectors["device_association_ledger"]
    if derive_device_association_ledger_sha256(contract, device["entries"]) != device["sha256"]:
        raise QualificationError("device-association hash vector failed")
    mount = vectors["mount_ledger"]
    if derive_mount_ledger_sha256(contract, mount["entries"]) != mount["sha256"]:
        raise QualificationError("mount-ledger hash vector failed")
    diagnostic = vectors["diagnostic_mount_identity"]
    if derive_diagnostic_mount_identity_sha256(contract, diagnostic["body"]) != diagnostic["sha256"]:
        raise QualificationError("diagnostic-mount hash vector failed")
    root = vectors["mounted_root_tree"]
    if derive_mounted_root_tree_sha256(contract, root["root_id"], [root["entry"]]) != root["sha256"]:
        raise QualificationError("mounted-root hash vector failed")
    mounted = vectors["mounted_tree"]
    if derive_mounted_tree_sha256(contract, mounted["root_digests"]) != mounted["sha256"]:
        raise QualificationError("mounted-tree hash vector failed")
    staged = vectors["staged_tree"]
    if derive_staged_tree_sha256(contract, staged["entries"]) != staged["sha256"]:
        raise QualificationError("staged-tree hash vector failed")


def _source_path(value: Any) -> Tuple[str, ...]:
    parts = _strict_relative_path(value, "source-index relative path")
    if len(parts) > 8:
        raise QualificationError("source-index path depth exceeds the frozen cap")
    return parts


def _typed_entries(root_id: str, index: Mapping[str, Any]) -> Tuple[List[Dict[str, Any]], Set[str]]:
    files = index.get("entries")
    if type(files) is not list or not files:
        raise QualificationError("source index entries are absent")
    paths = []  # type: List[str]
    directories = set()  # type: Set[str]
    regular_entries = []  # type: List[Dict[str, Any]]
    folded = set()  # type: Set[str]
    for item in files:
        expected_keys = {
            "bytes", "git_mode", "git_oid", "git_type", "materialized_mode",
            "relative_path", "sha256",
        }
        if type(item) is not dict or set(item) != expected_keys:
            raise QualificationError("source-index entry keys differ")
        parts = _source_path(item["relative_path"])
        path = item["relative_path"]
        if path.casefold() in folded:
            raise QualificationError("source-index path has a casefold collision")
        folded.add(path.casefold())
        paths.append(path)
        for end in range(1, len(parts)):
            directories.add("/".join(parts[:end]))
        if (item["git_type"] != "blob" or item["git_mode"] not in {"100644", "100755"}
                or HEX40.fullmatch(str(item["git_oid"])) is None
                or HEX64.fullmatch(str(item["sha256"])) is None
                or type(item["bytes"]) is not int or not 0 <= item["bytes"] <= 4 << 20
                or item["materialized_mode"] != (0o444 if item["git_mode"] == "100644" else 0o555)):
            raise QualificationError("source-index blob identity differs")
        regular_entries.append({
            "root_id": root_id,
            "relative_path": path,
            "entry_type": "regular",
            "mode": item["materialized_mode"],
            "bytes": item["bytes"],
            "sha256": item["sha256"],
        })
    if paths != sorted(paths, key=lambda value: value.encode("utf-8")) or len(paths) != len(set(paths)):
        raise QualificationError("source-index entry order or uniqueness differs")
    if any(path in directories for path in paths):
        raise QualificationError("source-index file is an ancestor conflict")
    typed = [{
        "root_id": root_id, "relative_path": "", "entry_type": "root",
        "mode": 0o555, "bytes": 0, "sha256": None,
    }]
    typed.extend({
        "root_id": root_id, "relative_path": path, "entry_type": "directory",
        "mode": 0o555, "bytes": 0, "sha256": None,
    } for path in directories)
    typed.extend(regular_entries)
    typed = [typed[0]] + sorted(typed[1:], key=lambda item: item["relative_path"].encode("utf-8"))
    return typed, directories


def _build_materialization_plan(
        contract: Mapping[str, Any], indexes: Sequence[Mapping[str, Any]],
        handoff: Mapping[str, Any]) -> Dict[str, Any]:
    profile = contract.get("image_profile")
    caps = contract.get("hard_caps")
    if type(profile) is not dict or type(caps) is not dict:
        raise QualificationError("contract image profile or caps are absent")
    if profile.get("root_order") != list(EXPECTED_ROOT_ORDER):
        raise QualificationError("contract root order differs")
    if type(indexes) is not list or [item.get("root_id") for item in indexes] != list(EXPECTED_ROOT_ORDER):
        raise QualificationError("source-index root order differs")
    if (type(handoff) is not dict or handoff.get("root_order") != list(EXPECTED_ROOT_ORDER)
            or handoff.get("source_input_handoff_sha256")
            != contract["exact_bindings"]["source_index_checkpoint"]["source_input_handoff_sha256"]):
        raise QualificationError("source-input handoff differs")
    expected_roots = {item["root_id"]: item for item in profile["roots"]}
    handoff_sources = {item["root_id"]: item for item in handoff.get("sources", [])}
    plan_roots = []  # type: List[Dict[str, Any]]
    staged_entries = []  # type: List[Dict[str, Any]]
    materialization = []  # type: List[Dict[str, Any]]
    mounted_root_digests = []  # type: List[Dict[str, Any]]
    long_components = []  # type: List[str]
    total_files = 0
    total_directories = 0
    total_bytes = 0
    for index in indexes:
        root_id = index["root_id"]
        expectation = expected_roots.get(root_id)
        source_ref = handoff_sources.get(root_id)
        if type(expectation) is not dict or type(source_ref) is not dict:
            raise QualificationError("root expectation or handoff reference is absent")
        if (index.get("source_index_id") != expectation["source_index_id"]
                or index.get("root_tree_sha256") != expectation["root_tree_sha256"]
                or source_ref.get("source_index_id") != index.get("source_index_id")
                or source_ref.get("root_tree_sha256") != index.get("root_tree_sha256")):
            raise QualificationError("source root identity differs from contract or handoff")
        typed, directories = _typed_entries(root_id, index)
        files = index["entries"]
        root_bytes = sum(item["bytes"] for item in files)
        if (index.get("entry_count") != len(files)
                or index.get("implicit_directory_count") != len(directories)
                or index.get("total_regular_bytes") != root_bytes
                or len(files) != expectation["regular_file_count"]
                or len(directories) != expectation["implicit_directory_count"]
                or root_bytes != expectation["total_regular_bytes"]
                or len(typed) != expectation["tree_entry_count"]):
            raise QualificationError("source root counts differ")
        for item in files:
            parts = item["relative_path"].split("/")
            for component in parts:
                if len(component.encode("utf-8")) > 31:
                    long_components.append(component)
            materialization.append({
                "root_id": root_id,
                "relative_path": item["relative_path"],
                "path_components": parts,
                "parent_relative_path": "/".join(parts[:-1]),
                "basename": parts[-1],
                "git_oid": item["git_oid"],
                "git_mode": item["git_mode"],
                "materialized_mode": item["materialized_mode"],
                "bytes": item["bytes"],
                "sha256": item["sha256"],
            })
        root_mounted_sha = derive_mounted_root_tree_sha256(contract, root_id, typed)
        mounted_root_digests.append({"root_id": root_id, "sha256": root_mounted_sha})
        staged_entries.extend(typed)
        plan_roots.append({
            "root_id": root_id,
            "source_index_id": index["source_index_id"],
            "portable_root_tree_sha256": index["root_tree_sha256"],
            "mounted_root_tree_sha256": root_mounted_sha,
            "regular_file_count": len(files),
            "implicit_directory_count": len(directories),
            "tree_entry_count": len(typed),
            "total_regular_bytes": root_bytes,
            "typed_entries": typed,
        })
        total_files += len(files)
        total_directories += len(directories)
        total_bytes += root_bytes
    if (total_files != int(caps["mounted_tree_regular_files_exact"])
            or total_directories != int(caps["mounted_tree_directory_records_exact"])
            or len(staged_entries) != int(caps["mounted_tree_typed_records_exact"])
            or total_bytes != int(caps["projected_payload_bytes_exact"])
            or len(materialization) != 477):
        raise QualificationError("aggregate materialization-plan counts differ")
    path_lengths = [len(item["relative_path"].encode("utf-8")) for item in materialization]
    depths = [len(item["path_components"]) for item in materialization]
    component_lengths = [len(component.encode("utf-8")) for item in materialization for component in item["path_components"]]
    if (len(long_components) != int(caps["long_component_occurrences_exact"])
            or len(set(long_components)) != int(caps["long_components_unique_exact"])
            or max(component_lengths) != int(caps["indexed_component_bytes_max"])
            or max(path_lengths) != int(caps["indexed_relative_path_bytes_max"])
            or max(depths) != int(caps["indexed_relative_path_depth_max"])):
        raise QualificationError("long-name witness closure differs")
    staged_sha = derive_staged_tree_sha256(contract, staged_entries)
    mounted_sha = derive_mounted_tree_sha256(contract, mounted_root_digests)
    plan = {
        "schema_version": "dancers-planar-physics-iso-source-capsule-materialization-plan-v1-internal",
        "root_order": list(EXPECTED_ROOT_ORDER),
        "source_input_handoff_sha256": handoff["source_input_handoff_sha256"],
        "regular_file_count": total_files,
        "implicit_directory_count": total_directories,
        "root_record_count": len(plan_roots),
        "typed_entry_count": len(staged_entries),
        "total_regular_bytes": total_bytes,
        "long_component_occurrences": len(long_components),
        "long_components_unique": len(set(long_components)),
        "max_component_bytes": max(component_lengths),
        "max_relative_path_bytes": max(path_lengths),
        "max_relative_path_depth": max(depths),
        "staged_tree_sha256": staged_sha,
        "mounted_tree_sha256": mounted_sha,
        "mounted_root_digests": mounted_root_digests,
        "roots": plan_roots,
        "materialization_entries": materialization,
    }
    _validate_json_value(plan)
    return plan


def _read_phase_a_inputs(repository: Path) -> Dict[str, bytes]:
    bindings = [
        (CONTRACT_PATH, CONTRACT_BYTES, CONTRACT_SHA256, "ISO qualification contract"),
        (CONTRACT_VALIDATOR_PATH, CONTRACT_VALIDATOR_BYTES, CONTRACT_VALIDATOR_SHA256,
         "ISO qualification contract validator"),
        (PROTECTED_SOURCE_CONTRACT_PATH, PROTECTED_SOURCE_CONTRACT_BYTES,
         PROTECTED_SOURCE_CONTRACT_SHA256, "protected-source-roots contract"),
    ]
    for path, size, digest in SOURCE_BINDINGS:
        bindings.append((path, size, digest, "source dependency %s" % path))
    for root_id, path, size, digest in INDEX_BINDINGS:
        bindings.append((path, size, digest, "%s source index" % root_id))
    bindings.append((HANDOFF_BINDING[0], HANDOFF_BINDING[1], HANDOFF_BINDING[2], "source-input handoff"))
    result = {}  # type: Dict[str, bytes]
    for path, size, digest, label in bindings:
        result[path] = _read_authenticated_repository_file(
            repository, path, size, digest, label,
        )
    if len(result) != 10:
        raise QualificationError("Phase A authenticated input inventory differs")
    return result


def _prepare_authenticated_phase_a(repository: Path) -> Dict[str, Any]:
    # Every source byte dependency is authenticated before the first exec.
    raw = _read_phase_a_inputs(repository)
    contract = _parse_canonical_json(raw[CONTRACT_PATH], CONTRACT_BYTES)
    _self_check_hash_vectors(contract)

    iso_validator = _load_authenticated_module(
        repository / CONTRACT_VALIDATOR_PATH, raw[CONTRACT_VALIDATOR_PATH],
        "dancers_iso_qualification_contract_v1_authenticated",
    )
    protected_path, _, _ = SOURCE_BINDINGS[0]
    generator_path, _, _ = SOURCE_BINDINGS[1]
    verifier_path, _, _ = SOURCE_BINDINGS[2]
    protected_validator = _load_authenticated_module(
        repository / protected_path, raw[protected_path],
        "dancers_protected_source_contract_v2_authenticated_for_iso",
    )
    generator = _load_authenticated_module(
        repository / generator_path, raw[generator_path],
        "dancers_source_index_generator_v2_authenticated_for_iso",
    )
    source_verifier = _load_authenticated_module(
        repository / verifier_path, raw[verifier_path],
        "dancers_source_index_verifier_v2_authenticated_for_iso",
    )

    protected_contract = _parse_canonical_json(
        raw[PROTECTED_SOURCE_CONTRACT_PATH], PROTECTED_SOURCE_CONTRACT_BYTES,
    )
    protected_validator.validate_contract_value(protected_contract)
    contract_verdict = iso_validator.verify_contract()
    if (type(contract_verdict) is not dict
            or contract_verdict.get("contract_sha256") != CONTRACT_SHA256
            or contract_verdict.get("empirical_qualification_passed") is not False
            or contract_verdict.get("production_backend_eligible") is not False
            or contract_verdict.get("production_blocker_count") != 8):
        raise QualificationError("ISO qualification contract did not validate")
    source_verdict = source_verifier.verify_source_indexes(
        repository / "comparison/physics-superbuild-v1/source-indexes"
    )
    if type(source_verdict) is not dict or source_verdict.get("valid") is not True:
        raise QualificationError("source-index verifier did not regenerate the exact closure")
    expected_generator_artifacts = {
        path.rsplit("/", 1)[-1]: (size, digest)
        for _, path, size, digest in INDEX_BINDINGS
    }
    expected_generator_artifacts[HANDOFF_BINDING[0].rsplit("/", 1)[-1]] = (
        HANDOFF_BINDING[1], HANDOFF_BINDING[2],
    )
    if (getattr(generator, "ARTIFACT_IDENTITIES", None) != expected_generator_artifacts
            or not callable(getattr(generator, "generate_source_index_artifacts", None))):
        raise QualificationError("authenticated source generator API differs")

    indexes = []  # type: List[Mapping[str, Any]]
    for root_id, path, size, _ in INDEX_BINDINGS:
        value = _parse_canonical_json(raw[path], size)
        validated = protected_validator.validate_source_index(value)
        if validated.get("root_id") != root_id:
            raise QualificationError("validated source-index root differs")
        indexes.append(validated)
    handoff = _parse_canonical_json(raw[HANDOFF_BINDING[0]], HANDOFF_BINDING[1])
    handoff = protected_validator.validate_source_input_handoff(handoff, indexes)
    plan = _build_materialization_plan(contract, indexes, handoff)
    return {
        "contract": contract,
        "contract_verdict": contract_verdict,
        "source_verdict": source_verdict,
        "materialization_plan": plan,
    }


def run_pure_phase_a_self_checks() -> Dict[str, Any]:
    """Authenticate committed data and exercise only parsers/hash/plan helpers.

    This intentionally does not exec a module, invoke Git, create a node, or
    reach the locked-driver live API.  It is safe for syntax/static test runs.
    """

    repository = _repository_root()
    raw_inputs = _read_phase_a_inputs(repository)
    if len(raw_inputs) != 10:
        raise QualificationError("pure-check Phase A input cardinality differs")
    contract_raw = raw_inputs[CONTRACT_PATH]
    contract = _parse_canonical_json(contract_raw, CONTRACT_BYTES)
    _self_check_hash_vectors(contract)
    indexes = []  # type: List[Mapping[str, Any]]
    for root_id, path, size, digest in INDEX_BINDINGS:
        raw = raw_inputs[path]
        value = _parse_canonical_json(raw, size)
        if value.get("root_id") != root_id:
            raise QualificationError("pure-check source-index root differs")
        indexes.append(value)
    handoff_raw = raw_inputs[HANDOFF_BINDING[0]]
    handoff = _parse_canonical_json(handoff_raw, HANDOFF_BINDING[1])
    plan = _build_materialization_plan(contract, indexes, handoff)
    return {
        "valid": True,
        "regular_file_count": plan["regular_file_count"],
        "typed_entry_count": plan["typed_entry_count"],
        "total_regular_bytes": plan["total_regular_bytes"],
        "staged_tree_sha256": plan["staged_tree_sha256"],
        "mounted_tree_sha256": plan["mounted_tree_sha256"],
    }


def _validate_bootstrap(value: Any, repository: Path) -> Dict[str, Any]:
    expected = {
        "checkpoint_commit", "checkpoint_tree", "qualifier", "receipt_verifier",
        "driver_authenticated_qualifier", "driver_authenticated_receipt_verifier",
    }
    if type(value) is not dict or set(value) != expected:
        raise QualificationError("authenticated-driver bootstrap keys differ")
    if (type(value["checkpoint_commit"]) is not str
            or type(value["checkpoint_tree"]) is not str
            or HEX40.fullmatch(value["checkpoint_commit"]) is None
            or HEX40.fullmatch(value["checkpoint_tree"]) is None):
        raise QualificationError("authenticated-driver checkpoint binding is malformed")
    if value["driver_authenticated_qualifier"] is not True or value["driver_authenticated_receipt_verifier"] is not True:
        raise QualificationError("authenticated-driver execution attestations are absent")
    result = dict(value)
    for key, role in (("qualifier", "ISO qualifier"), ("receipt_verifier", "ISO receipt verifier")):
        binding = value[key]
        keys = {"bytes", "path", "role", "sha256"}
        if type(binding) is not dict or set(binding) != keys or binding.get("role") != role:
            raise QualificationError("%s bootstrap binding differs" % role)
        if (type(binding.get("bytes")) is not int or binding["bytes"] <= 0
                or type(binding.get("sha256")) is not str
                or HEX64.fullmatch(binding["sha256"]) is None):
            raise QualificationError("%s bootstrap identity is malformed" % role)
        path_text = binding.get("path")
        _strict_relative_path(path_text, role + " bootstrap path")
        expected_path = QUALIFIER_PATH if key == "qualifier" else RECEIPT_VERIFIER_PATH
        if path_text != expected_path:
            raise QualificationError("%s bootstrap path differs from the frozen path" % role)
        raw = _read_authenticated_file(
            repository / path_text, binding["bytes"], binding["sha256"], role,
        )
        if key == "qualifier" and Path(os.path.abspath(__file__)) != repository / path_text:
            raise QualificationError("executing qualifier path differs from bootstrap")
        result[key] = dict(binding)
        result[key]["raw"] = raw
    return result


def run_from_authenticated_driver(bootstrap: Mapping[str, Any]) -> bytes:
    """Return one canonical success receipt to an independently locked driver.

    This call buffers the receipt; it never writes stdout or stderr.  Any
    exception means the driver must emit only the contract failure transport.
    """

    repository = _repository_root()
    trusted = _validate_bootstrap(bootstrap, repository)
    # Protocol implementation is filled below; retaining this guard makes an
    # incomplete or selectively copied checkpoint categorically non-executable.
    return _execute_qualification(repository, trusted)


def prepare_authenticated_materialization_plan(
        bootstrap: Mapping[str, Any]) -> Dict[str, Any]:
    """Return the authenticated, regenerated, read-only Phase A result.

    The supplied correlation bindings are meaningful only because the future
    locked driver independently pins and validates them.  This module does not
    claim that same-euid Python code can cryptographically authenticate its
    in-process caller.
    """

    repository = _repository_root()
    _validate_bootstrap(bootstrap, repository)
    return _prepare_authenticated_phase_a(repository)


def _execute_qualification(repository: Path, bootstrap: Mapping[str, Any]) -> bytes:
    phase_a = _prepare_authenticated_phase_a(repository)
    plan = phase_a["materialization_plan"]
    if (plan.get("regular_file_count") != 477
            or plan.get("typed_entry_count") != 548
            or plan.get("total_regular_bytes") != 14_595_556):
        raise QualificationError("authenticated Phase A plan terminal counts differ")
    raise QualificationError(
        "qualification live phases are not implemented; Phase A plan %s is nonmaterializing"
        % plan["staged_tree_sha256"]
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        print(FAILURE_PREFIX + "this module accepts no command-line arguments", file=sys.stderr)
        return ARGUMENT_EXIT
    print(
        FAILURE_PREFIX
        + "live execution requires an independently authenticated locked-driver bootstrap",
        file=sys.stderr,
    )
    return FAILURE_EXIT


if __name__ == "__main__":
    raise SystemExit(main())
