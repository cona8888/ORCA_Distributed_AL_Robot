#!/usr/bin/env python3
"""Authenticate the frozen ISO qualifier and confirm its hard live guard.

This is a non-materializing locked-driver checkpoint.  It reads exact loose
Git objects and live files through retained descriptors, executes the exact
authenticated verifier and then the authenticated qualifier in process, and
requires the qualifier to stop at its frozen Phase-A-only guard.  It has no
image, mount, build, network, simulator, receipt, or official-output capability.
"""

from __future__ import annotations

import errno
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import signal
import stat
import sys
import threading
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple
import unicodedata
import zlib


CHECKPOINT_COMMIT = "a06be111354b0547d035d2bc48a2dbae33ecfba4"
CHECKPOINT_TREE = "c624c7a5179370e2af760b7e968284f10ea0fb80"
CONTRACT_COMMIT = "df6721d45732e4dbc9a5764c84729c33f3e54e74"
CONTRACT_TREE = "577b9dba177f5f87918421e68f34ca2ac7e2113c"
CONTRACT_SHA256 = "62e00380bda281eaeaf4bacf717494964306ce4a840138b62e0707caf476e5e4"
BINDING_SCHEMA = "dancers-planar-physics-iso-source-capsule-locked-implementation-binding-v1"
BINDING_PATH = "docs/simulator/physics_iso_source_capsule_locked_implementation_binding_v1.json"
BINDING_BYTES = 715
BINDING_SHA256 = "cd8570a0de55c88463a3f122c5ae3e84e26d6d51aab4d00beaa8d8a2c4f08463"
QUALIFIER_PATH = "scripts/comparison/qualify_physics_iso_source_capsule_v1.py"
VERIFIER_PATH = "scripts/comparison/verify_physics_iso_source_capsule_qualification_v1.py"
QUALIFIER_BYTES = 155_073
QUALIFIER_SHA256 = "858d1e25da2541faec436a67c4d5c3782f06654f50635f3ef7a2d98af832d638"
VERIFIER_BYTES = 113_107
VERIFIER_SHA256 = "42d0763df1db68808d41b327d049446f810e432bc5860bdc50fce552e19c2c59"
EXPECTED_GUARD = (
    "qualification live phases are not implemented; Phase A plan "
    "4c34568494cc8bc20a66617586ff274f8b2bb617594cdb8f120be05e9242d9e8 "
    "is nonmaterializing"
)
CONTROLLED_SIGNAL_NAMES = ("SIGHUP", "SIGINT", "SIGQUIT", "SIGTERM")
FAILURE_PREFIX = "physics ISO source-capsule qualification failed: "
RUNTIME_FAILURE = FAILURE_PREFIX + "locked nonexecuting checkpoint rejected\n"
ARGUMENT_FAILURE = FAILURE_PREFIX + "this driver accepts no command-line arguments\n"
SUCCESS_VERDICT = (
    b'{"binding_file_bytes":715,'
    b'"binding_file_sha256":"cd8570a0de55c88463a3f122c5ae3e84e26d6d51aab4d00beaa8d8a2c4f08463",'
    b'"checkpoint_commit":"a06be111354b0547d035d2bc48a2dbae33ecfba4",'
    b'"checkpoint_tree":"c624c7a5179370e2af760b7e968284f10ea0fb80",'
    b'"controlled_signal_names":["SIGHUP","SIGINT","SIGQUIT","SIGTERM"],'
    b'"handlers_installed":true,"handlers_restored":true,'
    b'"image_attached":false,"image_created":false,"mount_created":false,'
    b'"native_thread_count_at_entry":1,"phase_a_authenticated":true,'
    b'"qualification_receipt_emitted":false,"qualifier_live_guard_observed":true,'
    b'"schema_version":"dancers-planar-physics-iso-source-capsule-locked-driver-nonexecution-verdict-v1",'
    b'"verifier_checkpoint_authenticated":true}\n'
)
READ_CHUNK = 65_536
MAX_OBJECT_BYTES = 4_194_304
MAX_COMPRESSED_OBJECT_BYTES = 4_194_304
PROC_PIDLISTTHREADS = 6


class DriverError(RuntimeError):
    """The exact nonexecuting driver contract was not satisfied."""


class DriverCancellation(BaseException):
    """A controlled signal cancelled the locked driver."""


def _repository_root() -> Path:
    return Path(os.path.abspath(__file__)).parents[2]


def _controlled_signals() -> Tuple[int, ...]:
    values = []  # type: List[int]
    for name in CONTROLLED_SIGNAL_NAMES:
        value = getattr(signal, name, None)
        if type(value) is not signal.Signals:
            raise DriverError("a controlled signal is unavailable")
        values.append(int(value))
    if len(set(values)) != len(values):
        raise DriverError("controlled signals are not distinct")
    return tuple(values)


def _mask(operation: int, values: Set[int]) -> Set[int]:
    function = getattr(signal, "pthread_sigmask", None)
    if not callable(function):
        raise DriverError("signal masking is unavailable")
    try:
        return {int(item) for item in function(operation, values)}
    except OSError as exc:
        raise DriverError("controlled-signal masking failed") from exc


def _query_mask() -> Set[int]:
    return _mask(signal.SIG_BLOCK, set())


def _controlled_signal_handler(signum: int, _frame: Any) -> None:
    try:
        name = signal.Signals(signum).name
    except ValueError:
        name = "UNKNOWN"
    raise DriverCancellation("controlled signal " + name)


def _prove_python_main_thread() -> None:
    current = threading.current_thread()
    main = threading.main_thread()
    active = threading.enumerate()
    if current is not main or active != [main] or threading.active_count() != 1:
        raise DriverError("locked driver is not the sole main thread")


def _native_thread_census_blocked(controlled: Set[int]) -> int:
    if not controlled or not controlled.issubset(_query_mask()):
        raise DriverError("native thread census signals are not blocked")
    if sys.platform != "darwin":
        raise DriverError("native Darwin thread census is unavailable")
    try:
        system = ctypes.CDLL(None, use_errno=True)
        pthread_main_np = system.pthread_main_np
        pthread_main_np.argtypes = []
        pthread_main_np.restype = ctypes.c_int
        libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        proc_pidinfo = libproc.proc_pidinfo
        proc_pidinfo.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
            ctypes.c_void_p, ctypes.c_int,
        ]
        proc_pidinfo.restype = ctypes.c_int
    except (AttributeError, OSError) as exc:
        raise DriverError("native Darwin thread APIs are unavailable") from exc
    if pthread_main_np() != 1:
        raise DriverError("locked driver is not on the native main thread")
    observed = []  # type: List[int]
    for _ in range(2):
        threads = (ctypes.c_uint64 * 2)()
        ctypes.set_errno(0)
        returned = proc_pidinfo(
            os.getpid(), PROC_PIDLISTTHREADS, 0,
            ctypes.byref(threads), ctypes.sizeof(threads),
        )
        if returned != 8 or threads[0] == 0 or threads[1] != 0:
            raise DriverError("native process does not have exactly one thread")
        observed.append(int(threads[0]))
    if observed[0] != observed[1]:
        raise DriverError("native main-thread identity changed during census")
    return 1


def _prove_single_main_thread() -> None:
    _prove_python_main_thread()
    controlled = set(_controlled_signals())
    previous = _query_mask()
    primary = None  # type: Optional[BaseException]
    try:
        observed = _mask(signal.SIG_BLOCK, controlled)
        if observed != previous:
            raise DriverError("pre-install signal-mask snapshot changed")
        if controlled & previous:
            raise DriverError("a controlled signal was already blocked")
        else:
            _native_thread_census_blocked(controlled)
            _prove_python_main_thread()
    except BaseException as exc:
        primary = exc
    restoration = None  # type: Optional[BaseException]
    try:
        _mask(signal.SIG_SETMASK, previous)
    except BaseException as exc:
        restoration = exc
    if restoration is not None:
        if primary is not None:
            raise _merge_failure(
                primary, restoration, "pre-install signal-mask restoration failed",
            )
        raise restoration
    if primary is not None:
        raise primary


class _SignalEnvelope:
    def __init__(self) -> None:
        self.signals = _controlled_signals()
        self.previous_mask = set()  # type: Set[int]
        self.previous_handlers = {}  # type: Dict[int, Any]
        self.installed = []  # type: List[int]
        self.active = False
        self.restoration_complete = False

    @staticmethod
    def _handler_equal(left: Any, right: Any) -> bool:
        if callable(left) or callable(right):
            return left is right
        handler_type = type(signal.SIG_DFL)
        return (
            type(left) is handler_type and type(right) is handler_type
            and int(left) in {int(signal.SIG_DFL), int(signal.SIG_IGN)}
            and int(left) == int(right)
        )

    def install(self) -> None:
        controlled = set(self.signals)
        self.previous_mask = _query_mask()
        primary = None  # type: Optional[BaseException]
        try:
            observed_mask = _mask(signal.SIG_BLOCK, controlled)
            if observed_mask != self.previous_mask:
                raise DriverError("signal-mask snapshot changed during installation")
            if controlled & self.previous_mask:
                raise DriverError("a controlled signal was already blocked")
            for number in self.signals:
                self.previous_handlers[number] = signal.getsignal(number)
            _native_thread_census_blocked(controlled)
            _prove_python_main_thread()
            for number in self.signals:
                # Record the target before the call so a synthetic or real
                # post-effect BaseException cannot hide a changed handler.
                self.installed.append(number)
                observed = signal.signal(number, _controlled_signal_handler)
                if (
                    not self._handler_equal(observed, self.previous_handlers[number])
                    or signal.getsignal(number) is not _controlled_signal_handler
                ):
                    raise DriverError("controlled-signal handler installation differed")
            _mask(signal.SIG_SETMASK, self.previous_mask)
            self.active = True
            return
        except BaseException as exc:
            primary = exc
        cleanup_errors = []  # type: List[BaseException]
        rollback_blocked = False
        for _ in range(2):
            try:
                _mask(signal.SIG_BLOCK, controlled)
            except BaseException as exc:
                cleanup_errors.append(exc)
            try:
                rollback_blocked = controlled.issubset(_query_mask())
            except BaseException as exc:
                cleanup_errors.append(exc)
                rollback_blocked = False
            if rollback_blocked:
                break
        if not rollback_blocked:
            cleanup_errors.append(
                DriverError("installation rollback signals could not be blocked"),
            )
        for number in reversed(self.installed):
            try:
                signal.signal(number, self.previous_handlers[number])
            except BaseException as exc:
                cleanup_errors.append(exc)
        for number in self.installed:
            try:
                if not self._handler_equal(
                    signal.getsignal(number), self.previous_handlers[number],
                ):
                    cleanup_errors.append(DriverError("installation rollback handler differs"))
            except BaseException as exc:
                cleanup_errors.append(exc)
        try:
            _mask(signal.SIG_SETMASK, self.previous_mask)
        except BaseException as exc:
            cleanup_errors.append(exc)
        else:
            try:
                if _query_mask() != self.previous_mask:
                    cleanup_errors.append(DriverError("installation rollback mask differs"))
            except BaseException as exc:
                cleanup_errors.append(exc)
        exact_handlers = True
        for number in self.installed:
            try:
                exact_handlers = exact_handlers and self._handler_equal(
                    signal.getsignal(number), self.previous_handlers[number],
                )
            except BaseException as exc:
                cleanup_errors.append(exc)
                exact_handlers = False
        exact_mask = False
        try:
            exact_mask = _query_mask() == self.previous_mask
        except BaseException as exc:
            cleanup_errors.append(exc)
        if exact_handlers and exact_mask:
            self.restoration_complete = True
        else:
            self.active = True
        if cleanup_errors:
            combined = primary
            for error in cleanup_errors:
                combined = _merge_failure(
                    combined, error,
                    "controlled-signal installation rollback failed",
                )
            assert combined is not None
            raise combined
        assert primary is not None
        raise primary

    def pre_entry_census(self) -> int:
        if not self.active or self.restoration_complete:
            raise DriverError("controlled-signal envelope is not active")
        controlled = set(self.signals)
        observed_mask = _mask(signal.SIG_BLOCK, controlled)
        primary = None  # type: Optional[BaseException]
        count = 0
        try:
            if observed_mask != self.previous_mask:
                raise DriverError("signal mask changed before qualifier entry")
            count = _native_thread_census_blocked(controlled)
            _prove_python_main_thread()
            for number in self.signals:
                if signal.getsignal(number) is not _controlled_signal_handler:
                    raise DriverError("controlled handler changed before qualifier entry")
        except BaseException as exc:
            primary = exc
        restoration = None  # type: Optional[BaseException]
        try:
            _mask(signal.SIG_SETMASK, self.previous_mask)
        except BaseException as exc:
            restoration = exc
        if restoration is not None:
            if primary is not None:
                raise _merge_failure(
                    primary, restoration, "pre-entry signal-mask restoration failed",
                )
            raise restoration
        if primary is not None:
            raise primary
        return count

    def restore(self) -> None:
        if not self.active or self.restoration_complete:
            raise DriverError("controlled-signal envelope was not active")
        controlled = set(self.signals)
        observed_mask = _query_mask()
        mismatch = observed_mask != self.previous_mask
        errors = []  # type: List[BaseException]
        teardown_blocked = False
        for _ in range(2):
            try:
                prior = _mask(signal.SIG_BLOCK, controlled)
                if prior != observed_mask:
                    mismatch = True
            except BaseException as exc:
                errors.append(exc)
            try:
                teardown_blocked = controlled.issubset(_query_mask())
            except BaseException as exc:
                errors.append(exc)
                teardown_blocked = False
            if teardown_blocked:
                break
        if not teardown_blocked:
            errors.append(DriverError("teardown signals could not be blocked"))
        try:
            _native_thread_census_blocked(controlled)
            _prove_python_main_thread()
        except BaseException as exc:
            errors.append(exc)
        for number in reversed(self.installed):
            try:
                signal.signal(number, self.previous_handlers[number])
            except BaseException as exc:
                errors.append(exc)
        for number in self.installed:
            try:
                if not self._handler_equal(
                    signal.getsignal(number), self.previous_handlers[number],
                ):
                    mismatch = True
            except BaseException as exc:
                errors.append(exc)
        try:
            _mask(signal.SIG_SETMASK, self.previous_mask)
        except BaseException as exc:
            errors.append(exc)
        else:
            try:
                if _query_mask() != self.previous_mask:
                    mismatch = True
            except BaseException as exc:
                errors.append(exc)
        exact_handlers = True
        for number in self.installed:
            try:
                exact_handlers = exact_handlers and self._handler_equal(
                    signal.getsignal(number), self.previous_handlers[number],
                )
            except BaseException as exc:
                errors.append(exc)
                exact_handlers = False
        exact_mask = False
        try:
            exact_mask = _query_mask() == self.previous_mask
        except BaseException as exc:
            errors.append(exc)
        if exact_handlers and exact_mask:
            self.active = False
            self.restoration_complete = True
        if errors or mismatch:
            if errors:
                combined = None  # type: Optional[BaseException]
                for error in errors:
                    combined = _merge_failure(
                        combined, error, "controlled-signal restoration failed",
                    )
                if mismatch:
                    combined = _merge_failure(
                        combined, DriverError("controlled-signal state changed inside the driver"),
                        "controlled-signal restoration failed",
                    )
                assert combined is not None
                raise combined
            raise DriverError("controlled-signal state changed inside the driver")


def _directory_identity(info: os.stat_result) -> Tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
        info.st_uid, info.st_gid, info.st_ctime_ns,
    )


def _file_identity(info: os.stat_result) -> Tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
        info.st_uid, info.st_gid, info.st_size, info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _trusted_directory(info: os.stat_result) -> bool:
    return (
        stat.S_ISDIR(info.st_mode)
        and not info.st_mode & 0o022
        and (
            info.st_uid == 0
            or (info.st_uid == os.geteuid() and info.st_gid == os.getegid())
        )
    )


def _trusted_regular(info: os.stat_result) -> bool:
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.geteuid()
        and info.st_gid == os.getegid()
        and info.st_nlink == 1
        and not info.st_mode & 0o022
    )


def _required_open_flag(name: str) -> int:
    value = getattr(os, name, None)
    if type(value) is not int or value == 0:
        raise DriverError("required descriptor flag is unavailable")
    return value


class _DescriptorSession:
    def __init__(self, controlled: Sequence[int]) -> None:
        self.controlled = {int(item) for item in controlled}
        self.fds = []  # type: List[int]
        self.directories = []  # type: List[Tuple[int, str, int, Tuple[int, ...], Tuple[int, ...]]]
        self.files = []  # type: List[Tuple[int, str, int, Tuple[int, ...]]]
        self.root_fd = -1
        self.closed = False
        self.cleanup_mask_target = _query_mask()
        self.pending_fd = -1

    def _open_tracked(self, path: str, flags: int, dir_fd: Optional[int] = None) -> int:
        prior = _query_mask()
        descriptor = -1
        primary = None  # type: Optional[BaseException]
        cleanup_errors = []  # type: List[BaseException]
        slot = len(self.fds)
        try:
            observed = _mask(signal.SIG_BLOCK, self.controlled)
            if observed != prior:
                raise DriverError("descriptor acquisition mask snapshot changed")
            self.fds.append(-1)
            if dir_fd is None:
                descriptor = os.open(path, flags)
            else:
                descriptor = os.open(path, flags, dir_fd=dir_fd)
            self.pending_fd = descriptor
            self.fds[slot] = descriptor
            self.pending_fd = -1
        except BaseException as exc:
            primary = exc
            if self.pending_fd >= 0:
                closed = False
                for _ in range(2):
                    try:
                        os.close(self.pending_fd)
                        closed = True
                        break
                    except BaseException as close_error:
                        cleanup_errors.append(close_error)
                    try:
                        os.fstat(self.pending_fd)
                    except OSError as state_error:
                        if state_error.errno == errno.EBADF:
                            closed = True
                            break
                        cleanup_errors.append(state_error)
                    except BaseException as state_error:
                        cleanup_errors.append(state_error)
                if closed:
                    self.pending_fd = -1
                    if len(self.fds) > slot and self.fds[slot] == descriptor:
                        self.fds.pop()
                else:
                    cleanup_errors.append(
                        DriverError("unregistered descriptor remained open"),
                    )
            if len(self.fds) > slot and self.fds[slot] == -1:
                self.fds.pop()
        restoration = None  # type: Optional[BaseException]
        try:
            _mask(signal.SIG_SETMASK, prior)
        except BaseException as exc:
            restoration = exc
        combined = primary
        for error in cleanup_errors:
            combined = _merge_failure(
                combined, error, "unregistered descriptor cleanup failed",
            )
        if restoration is not None:
            combined = _merge_failure(
                combined, restoration,
                "descriptor acquisition signal restoration failed",
            )
        if combined is not None:
            raise combined
        return descriptor

    def _open_child_directory(self, parent_fd: int, name: str) -> int:
        if (
            not name or name in {".", ".."} or "/" in name or "\x00" in name
            or unicodedata.normalize("NFC", name) != name
            or len(name.encode("utf-8")) > 255
        ):
            raise DriverError("descriptor path component is invalid")
        flags = (
            os.O_RDONLY | _required_open_flag("O_CLOEXEC")
            | _required_open_flag("O_DIRECTORY") | _required_open_flag("O_NOFOLLOW")
        )
        parent_before = os.fstat(parent_fd)
        child_fd = self._open_tracked(name, flags, parent_fd)
        child = os.fstat(child_fd)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        parent_after = os.fstat(parent_fd)
        if (
            not _trusted_directory(child)
            or _directory_identity(child) != _directory_identity(named)
            or _directory_identity(parent_before) != _directory_identity(parent_after)
        ):
            raise DriverError("descriptor-walk directory identity differs")
        self.directories.append((
            parent_fd, name, child_fd,
            _directory_identity(parent_before), _directory_identity(child),
        ))
        return child_fd

    def open_repository(self, repository: Path) -> int:
        text = os.path.abspath(os.fspath(repository))
        pure = PurePosixPath(text)
        if not pure.is_absolute() or pure.as_posix() != text:
            raise DriverError("repository path is not canonical absolute POSIX")
        flags = (
            os.O_RDONLY | _required_open_flag("O_CLOEXEC")
            | _required_open_flag("O_DIRECTORY") | _required_open_flag("O_NOFOLLOW")
        )
        root = self._open_tracked("/", flags)
        root_info = os.fstat(root)
        if not _trusted_directory(root_info) or root_info.st_uid != 0:
            raise DriverError("filesystem root is not trusted")
        current = root
        for component in pure.parts[1:]:
            current = self._open_child_directory(current, component)
        self.root_fd = current
        return current

    def _relative_components(self, path: str) -> Tuple[str, ...]:
        if (
            type(path) is not str or not path or path.startswith("/")
            or "\\" in path or "\x00" in path
            or unicodedata.normalize("NFC", path) != path
            or len(path.encode("utf-8")) > 2048
        ):
            raise DriverError("repository-relative path is invalid")
        pure = PurePosixPath(path)
        if pure.as_posix() != path or any(part in {"", ".", ".."} for part in pure.parts):
            raise DriverError("repository-relative path is not canonical")
        return tuple(pure.parts)

    def read_regular(self, base_fd: int, path: str, maximum: int) -> bytes:
        if type(maximum) is not int or maximum < 1 or maximum > MAX_OBJECT_BYTES:
            raise DriverError("descriptor read cap is invalid")
        parts = self._relative_components(path)
        parent = base_fd
        for component in parts[:-1]:
            parent = self._open_child_directory(parent, component)
        flags = os.O_RDONLY | _required_open_flag("O_CLOEXEC") | _required_open_flag("O_NOFOLLOW")
        parent_before = os.fstat(parent)
        descriptor = self._open_tracked(parts[-1], flags, parent)
        before = os.fstat(descriptor)
        named = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if (
            not _trusted_regular(before)
            or _file_identity(before) != _file_identity(named)
            or _directory_identity(parent_before) != _directory_identity(os.fstat(parent))
            or before.st_size > maximum
        ):
            raise DriverError("descriptor-read file identity differs")
        chunks = []  # type: List[bytes]
        total = 0
        while True:
            chunk = os.read(descriptor, min(READ_CHUNK, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise DriverError("descriptor-read file exceeds its cap")
        after = os.fstat(descriptor)
        named_after = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if (
            _file_identity(before) != _file_identity(after)
            or _file_identity(before) != _file_identity(named_after)
            or _directory_identity(parent_before) != _directory_identity(os.fstat(parent))
            or total != before.st_size
        ):
            raise DriverError("descriptor-read file changed while read")
        self.files.append((parent, parts[-1], descriptor, _file_identity(before)))
        return b"".join(chunks)

    def reauthenticate(self) -> None:
        for parent, name, child, parent_identity, child_identity in self.directories:
            if (
                _directory_identity(os.fstat(parent)) != parent_identity
                or _directory_identity(os.fstat(child)) != child_identity
                or _directory_identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != child_identity
            ):
                raise DriverError("descriptor-walk ancestry changed")
        for parent, name, descriptor, identity in self.files:
            if (
                _file_identity(os.fstat(descriptor)) != identity
                or _file_identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != identity
            ):
                raise DriverError("authenticated file name changed")

    def close(self) -> None:
        if self.closed:
            raise DriverError("descriptor session was closed twice")
        errors = []  # type: List[BaseException]
        try:
            _mask(signal.SIG_BLOCK, self.controlled)
        except BaseException as exc:
            # Descriptor retirement remains mandatory even if masking itself
            # fails.  The exact construction-time mask is still restored.
            errors.append(exc)
        remaining = []  # type: List[int]
        targets = list(self.fds)
        if self.pending_fd >= 0:
            targets.append(self.pending_fd)
        for descriptor in reversed(targets):
            if descriptor < 0:
                continue
            closed = False
            for _ in range(2):
                try:
                    os.close(descriptor)
                    closed = True
                    break
                except BaseException as exc:
                    errors.append(exc)
                try:
                    os.fstat(descriptor)
                except OSError as exc:
                    if exc.errno == errno.EBADF:
                        closed = True
                        break
                    errors.append(exc)
                except BaseException as exc:
                    errors.append(exc)
            if not closed:
                errors.append(DriverError("a tracked descriptor remained open"))
                if descriptor != self.pending_fd:
                    remaining.append(descriptor)
            elif descriptor == self.pending_fd:
                self.pending_fd = -1
        self.fds = list(reversed(remaining))
        mask_restored = False
        try:
            _mask(signal.SIG_SETMASK, self.cleanup_mask_target)
        except BaseException as exc:
            errors.append(exc)
        try:
            mask_restored = _query_mask() == self.cleanup_mask_target
        except BaseException as exc:
            errors.append(exc)
        if not mask_restored:
            errors.append(DriverError("descriptor-session signal mask remained changed"))
        self.closed = not self.fds and self.pending_fd < 0 and mask_restored
        if errors:
            combined = None  # type: Optional[BaseException]
            for error in errors:
                combined = _merge_failure(
                    combined, error, "descriptor-session cleanup was not clean",
                )
            assert combined is not None
            raise combined


def _parse_commit(payload: bytes) -> Tuple[str, Tuple[str, ...]]:
    if not payload or b"\x00" in payload or b"\n\n" not in payload:
        raise DriverError("commit payload is invalid")
    try:
        header = payload.split(b"\n\n", 1)[0].decode("ascii", "strict").splitlines()
    except UnicodeDecodeError as exc:
        raise DriverError("commit header is not ASCII") from exc
    trees = [line[5:] for line in header if line.startswith("tree ")]
    parents = tuple(line[7:] for line in header if line.startswith("parent "))
    if len(trees) != 1 or any(len(value) != 40 for value in (trees[0], *parents)):
        raise DriverError("commit topology is malformed")
    if any(any(character not in "0123456789abcdef" for character in value) for value in (trees[0], *parents)):
        raise DriverError("commit topology identity is malformed")
    return trees[0], parents


def _parse_tree(payload: bytes) -> Mapping[str, Tuple[str, str]]:
    result = {}  # type: Dict[str, Tuple[str, str]]
    position = 0
    prior = None  # type: Optional[bytes]
    while position < len(payload):
        space = payload.find(b" ", position)
        nul = payload.find(b"\x00", space + 1 if space >= 0 else position)
        if space < 0 or nul < 0 or nul + 21 > len(payload):
            raise DriverError("tree payload is truncated")
        try:
            mode = payload[position:space].decode("ascii", "strict")
            name = payload[space + 1:nul].decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise DriverError("tree entry text is invalid") from exc
        if (
            mode not in {"40000", "100644", "100755"}
            or not name or name in {".", ".."} or "/" in name or "\x00" in name
            or unicodedata.normalize("NFC", name) != name or name in result
        ):
            raise DriverError("tree entry is invalid")
        key = name.encode("utf-8") + (b"/" if mode == "40000" else b"")
        if prior is not None and key <= prior:
            raise DriverError("tree entry ordering differs")
        prior = key
        result[name] = (mode, payload[nul + 1:nul + 21].hex())
        position = nul + 21
    return result


def _read_loose_object(session: _DescriptorSession, objects_fd: int,
                       oid: str, expected_kind: str) -> bytes:
    if (
        type(oid) is not str or len(oid) != 40
        or any(character not in "0123456789abcdef" for character in oid)
        or expected_kind not in {"blob", "commit", "tree"}
    ):
        raise DriverError("loose-object request is invalid")
    compressed = session.read_regular(
        objects_fd, oid[:2] + "/" + oid[2:], MAX_COMPRESSED_OBJECT_BYTES,
    )
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(compressed, MAX_OBJECT_BYTES + 1)
        raw += decoder.flush(MAX_OBJECT_BYTES + 1 - min(len(raw), MAX_OBJECT_BYTES + 1))
    except zlib.error as exc:
        raise DriverError("loose Git object compression is invalid") from exc
    if (
        len(raw) > MAX_OBJECT_BYTES or not decoder.eof
        or decoder.unused_data or decoder.unconsumed_tail
        or hashlib.sha1(raw).hexdigest() != oid
    ):
        raise DriverError("loose Git object identity differs")
    nul = raw.find(b"\x00")
    if nul < 1:
        raise DriverError("loose Git object header is absent")
    try:
        header = raw[:nul].decode("ascii", "strict")
    except UnicodeDecodeError as exc:
        raise DriverError("loose Git object header is invalid") from exc
    fields = header.split(" ")
    if len(fields) != 2 or fields[0] != expected_kind or not fields[1].isdigit():
        raise DriverError("loose Git object type differs")
    if fields[1] != str(int(fields[1])):
        raise DriverError("loose Git object length is not canonical")
    payload = raw[nul + 1:]
    if int(fields[1]) != len(payload):
        raise DriverError("loose Git object length differs")
    return payload


def _strict_binding(raw: bytes) -> Mapping[str, Any]:
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise DriverError("implementation binding termination differs")

    def pairs(items: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
        result = {}  # type: Dict[str, Any]
        for key, value in items:
            if key in result:
                raise DriverError("implementation binding contains a duplicate key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> Any:
        raise DriverError("implementation binding contains a non-finite number")

    try:
        value = json.loads(
            raw.decode("utf-8", "strict"), object_pairs_hook=pairs,
            parse_constant=reject_constant,
        )
        canonical = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8") + b"\n"
    except (UnicodeDecodeError, ValueError, TypeError) as exc:
        raise DriverError("implementation binding is not canonical JSON") from exc
    if canonical != raw or type(value) is not dict:
        raise DriverError("implementation binding is not canonical JSON")
    expected = {
        "checkpoint_commit": CHECKPOINT_COMMIT,
        "checkpoint_tree": CHECKPOINT_TREE,
        "contract_sha256": CONTRACT_SHA256,
        "implementations": [
            {"bytes": QUALIFIER_BYTES, "path": QUALIFIER_PATH, "role": "ISO qualifier", "sha256": QUALIFIER_SHA256},
            {"bytes": VERIFIER_BYTES, "path": VERIFIER_PATH, "role": "ISO receipt verifier", "sha256": VERIFIER_SHA256},
        ],
        "schema_version": BINDING_SCHEMA,
    }
    if value != expected:
        raise DriverError("implementation binding values differ")
    return value


def _resolve_blob(session: _DescriptorSession, objects_fd: int,
                  root_oid: str, path: str) -> Tuple[str, bytes]:
    current = root_oid
    parts = path.split("/")
    for index, component in enumerate(parts):
        entries = _parse_tree(_read_loose_object(session, objects_fd, current, "tree"))
        if component not in entries:
            raise DriverError("checkpoint tree omits an implementation")
        mode, oid = entries[component]
        if index < len(parts) - 1:
            if mode != "40000":
                raise DriverError("checkpoint implementation path crosses a blob")
            current = oid
        else:
            if mode != "100644":
                raise DriverError("checkpoint implementation mode differs")
            return mode, _read_loose_object(session, objects_fd, oid, "blob")
    raise DriverError("checkpoint implementation path is empty")


def _authenticate_checkpoint(session: _DescriptorSession, repository_fd: int,
                             repository: Path) -> Tuple[Mapping[str, Any], bytes, bytes]:
    binding_raw = session.read_regular(repository_fd, BINDING_PATH, BINDING_BYTES)
    if len(binding_raw) != BINDING_BYTES or hashlib.sha256(binding_raw).hexdigest() != BINDING_SHA256:
        raise DriverError("implementation binding identity differs")
    binding = _strict_binding(binding_raw)
    objects_fd = repository_fd
    for component in (".git", "objects"):
        objects_fd = session._open_child_directory(objects_fd, component)

    commit = _read_loose_object(session, objects_fd, CHECKPOINT_COMMIT, "commit")
    tree, parents = _parse_commit(commit)
    if tree != CHECKPOINT_TREE or parents != (CONTRACT_COMMIT,):
        raise DriverError("checkpoint commit topology differs")
    contract_commit = _read_loose_object(session, objects_fd, CONTRACT_COMMIT, "commit")
    contract_tree, _ = _parse_commit(contract_commit)
    if contract_tree != CONTRACT_TREE:
        raise DriverError("contract anchor tree differs")

    _, qualifier = _resolve_blob(session, objects_fd, CHECKPOINT_TREE, QUALIFIER_PATH)
    _, verifier = _resolve_blob(session, objects_fd, CHECKPOINT_TREE, VERIFIER_PATH)
    identities = (
        (QUALIFIER_PATH, QUALIFIER_BYTES, QUALIFIER_SHA256, qualifier),
        (VERIFIER_PATH, VERIFIER_BYTES, VERIFIER_SHA256, verifier),
    )
    for path, size, digest, blob in identities:
        if len(blob) != size or hashlib.sha256(blob).hexdigest() != digest:
            raise DriverError("checkpoint implementation blob differs")
        live = session.read_regular(repository_fd, path, size)
        if live != blob:
            raise DriverError("live implementation differs from checkpoint")
        try:
            compile(blob.decode("utf-8", "strict"), path, "exec", dont_inherit=True, optimize=0)
        except (UnicodeDecodeError, SyntaxError) as exc:
            raise DriverError("authenticated implementation is not valid Python") from exc
    session.reauthenticate()
    if repository != _repository_root():
        raise DriverError("repository identity changed")
    return binding, qualifier, verifier


def _execute_authenticated_verifier(
    raw: bytes,
    repository: Path,
    expected_binding: Mapping[str, Any],
    expected_qualifier: bytes,
    expected_verifier: bytes,
) -> None:
    """Run only exact checkpoint verifier bytes, then compare both raw blobs."""

    try:
        source = raw.decode("utf-8", "strict")
        code = compile(source, VERIFIER_PATH, "exec", dont_inherit=True, optimize=0)
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise DriverError("authenticated verifier is not valid Python") from exc
    namespace = {
        "__file__": str(repository / VERIFIER_PATH),
        "__name__": "dancers_authenticated_iso_receipt_verifier_v1",
        "__package__": None,
    }  # type: Dict[str, Any]
    exec(code, namespace)
    load = namespace.get("load_authenticated_expected_binding")
    authenticate = namespace.get("authenticate_implementation_checkpoint")
    if (
        not callable(load) or not callable(authenticate)
        or namespace.get("REPOSITORY") != repository
    ):
        raise DriverError("authenticated verifier API differs")
    loaded = load(repository / BINDING_PATH, BINDING_BYTES, BINDING_SHA256)
    if loaded != expected_binding:
        raise DriverError("verifier-loaded implementation binding differs")
    observed_binding, observed_raws = authenticate(loaded)
    if (
        observed_binding != expected_binding
        or type(observed_raws) is not tuple or len(observed_raws) != 2
        or observed_raws[0] != expected_qualifier
        or observed_raws[1] != expected_verifier
    ):
        raise DriverError("verifier checkpoint authentication differs")


def _bootstrap(binding: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "checkpoint_commit": binding["checkpoint_commit"],
        "checkpoint_tree": binding["checkpoint_tree"],
        "driver_authenticated_qualifier": True,
        "driver_authenticated_receipt_verifier": True,
        "qualifier": dict(binding["implementations"][0]),
        "receipt_verifier": dict(binding["implementations"][1]),
    }


def _execute_authenticated_qualifier(raw: bytes, repository: Path,
                                     bootstrap: Mapping[str, Any]) -> None:
    source = raw.decode("utf-8", "strict")
    code = compile(source, QUALIFIER_PATH, "exec", dont_inherit=True, optimize=0)
    namespace = {
        "__file__": str(repository / QUALIFIER_PATH),
        "__name__": "dancers_authenticated_iso_qualifier_v1",
        "__package__": None,
    }  # type: Dict[str, Any]
    exec(code, namespace)
    entry = namespace.get("run_from_authenticated_driver")
    error_type = namespace.get("QualificationError")
    if not callable(entry) or type(error_type) is not type or not issubclass(error_type, RuntimeError):
        raise DriverError("authenticated qualifier API differs")
    try:
        result = entry(bootstrap)
    except BaseException as exc:
        if type(exc) is error_type and str(exc) == EXPECTED_GUARD:
            return
        raise
    if result is not None:
        raise DriverError("authenticated qualifier unexpectedly returned a receipt")
    raise DriverError("authenticated qualifier did not reach its hard guard")


def _merge_failure(primary: Optional[BaseException],
                   later: BaseException, label: str) -> BaseException:
    if primary is None:
        return later
    if not isinstance(primary, Exception):
        try:
            primary.locked_driver_cleanup_error = later  # type: ignore[attr-defined]
        except Exception:
            pass
        return primary
    if not isinstance(later, Exception):
        try:
            later.locked_driver_prior_error = primary  # type: ignore[attr-defined]
        except Exception:
            pass
        return later
    failure = DriverError(label)
    failure.primary_error = primary  # type: ignore[attr-defined]
    failure.later_error = later  # type: ignore[attr-defined]
    failure.__cause__ = later
    return failure


def _close_session_bounded(session: _DescriptorSession) -> None:
    first = None  # type: Optional[BaseException]
    for _ in range(2):
        if session.closed:
            break
        try:
            session.close()
        except BaseException as exc:
            first = _merge_failure(
                first, exc, "descriptor-session cleanup failures accumulated",
            )
    if not session.closed:
        first = _merge_failure(
            first, DriverError("descriptor session did not close"),
            "descriptor-session cleanup remained incomplete",
        )
    if first is not None:
        raise first


def _restore_envelope_bounded(envelope: _SignalEnvelope) -> None:
    first = None  # type: Optional[BaseException]
    for _ in range(2):
        if envelope.restoration_complete:
            break
        if not envelope.active:
            first = _merge_failure(
                first, DriverError("signal envelope cannot be retried"),
                "controlled-signal restoration remained incomplete",
            )
            break
        try:
            envelope.restore()
        except BaseException as exc:
            first = _merge_failure(
                first, exc, "controlled-signal restoration failures accumulated",
            )
    if not envelope.restoration_complete:
        first = _merge_failure(
            first, DriverError("controlled-signal state was not restored"),
            "controlled-signal restoration remained incomplete",
        )
    if first is not None:
        raise first


def run_nonexecuting_checkpoint() -> bytes:
    """Return the internal nonexecution verdict after exact restoration."""

    _prove_single_main_thread()
    repository = _repository_root()
    primary = None  # type: Optional[BaseException]
    binding = None  # type: Optional[Mapping[str, Any]]
    qualifier = None  # type: Optional[bytes]
    verifier = None  # type: Optional[bytes]
    session = None  # type: Optional[_DescriptorSession]
    try:
        session = _DescriptorSession(_controlled_signals())
        repository_fd = session.open_repository(repository)
        binding, qualifier, verifier = _authenticate_checkpoint(
            session, repository_fd, repository,
        )
    except BaseException as exc:
        primary = exc
    if session is not None:
        try:
            _close_session_bounded(session)
        except BaseException as exc:
            primary = _merge_failure(
                primary, exc, "pre-signal authentication cleanup failed",
            )
    if primary is not None:
        raise primary
    assert binding is not None and qualifier is not None and verifier is not None

    # The exact verifier, authenticated above without executing either frozen
    # implementation, now independently authenticates the binding, commit,
    # tree, blobs, and live files.  Both returned raw programs must match.
    _execute_authenticated_verifier(
        verifier, repository, binding, qualifier, verifier,
    )
    _prove_single_main_thread()

    envelope = _SignalEnvelope()
    try:
        envelope.install()
    except BaseException as exc:
        primary = exc
    if primary is None:
        try:
            if envelope.pre_entry_census() != 1:
                raise DriverError("native pre-entry thread count differs")
            _execute_authenticated_qualifier(
                qualifier, repository, _bootstrap(binding),
            )
        except BaseException as exc:
            primary = exc
    if envelope.active:
        try:
            _restore_envelope_bounded(envelope)
        except BaseException as exc:
            primary = _merge_failure(
                primary, exc, "qualifier signal-envelope cleanup failed",
            )
    if primary is not None:
        raise primary
    if not envelope.restoration_complete:
        raise DriverError("signal envelope did not reach its restored state")
    return SUCCESS_VERDICT


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        sys.stderr.write(ARGUMENT_FAILURE)
        return 2
    try:
        verdict = run_nonexecuting_checkpoint()
    except BaseException:
        sys.stderr.write(RUNTIME_FAILURE)
        return 1
    if verdict != SUCCESS_VERDICT:
        sys.stderr.write(RUNTIME_FAILURE)
        return 1
    # Observing the hard guard is intentionally not a successful qualification
    # transport.  The internal all-false verdict is never written by the CLI.
    sys.stderr.write(RUNTIME_FAILURE)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
