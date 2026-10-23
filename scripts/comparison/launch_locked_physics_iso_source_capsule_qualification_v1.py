#!/usr/bin/env python3
"""External nonauthorizing, nonspawning ISO qualification scaffold.

This checkpoint authenticates a committed locked driver, validates the
provisional transport constants, and then categorically rejects child setup.
It cannot create an image, attach or mount a device, build or run a simulator,
use a network, emit a qualification receipt, or write any official artifact.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import resource
import selectors
import signal
import stat
import struct
import sys
import threading
import time
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple
import unicodedata
import zlib


CHECKPOINT_COMMIT = "08dce107cf8982a156e8ed1081311c5cb3a5469d"
CHECKPOINT_TREE = "7612befcdc80c05be45f0ac3d0558b4e0951c56e"
CHECKPOINT_PARENT = "a06be111354b0547d035d2bc48a2dbae33ecfba4"
CHECKPOINT_PARENT_TREE = "c624c7a5179370e2af760b7e968284f10ea0fb80"
CHECKPOINT_COMMIT_BYTES = 239
CHECKPOINT_COMMIT_SHA256 = "4c2f117b8b3e4a84095eb56b2e82519c3a43b7e3dd47664e9bdd1ea800ff5687"
INVOCATION_BINDING_PATH = "docs/simulator/physics_iso_source_capsule_external_launcher_lock_v1.json"
INVOCATION_BINDING_BYTES = 1_663
INVOCATION_BINDING_SHA256 = "09e38f00fb277d2286829551646edc5c4b30db937d051d5bdc6eeb6aeb99647c"
INVOCATION_BINDING_SCHEMA = "dancers-planar-physics-iso-source-capsule-external-launcher-lock-v1"
LOCKED_DRIVER_PATH = "scripts/comparison/run_locked_physics_iso_source_capsule_qualification_v1.py"
LOCKED_DRIVER_BYTES = 47_588
LOCKED_DRIVER_SHA256 = "a15637b595902ba412e4cf0d2128fbc6840a92f1e5865b93d76cfd16c3a5ac4c"
LOCKED_DRIVER_GIT_BLOB = "a61e8b5ece347cea7d2c71a16c017a792696375a"
LOCKED_BINDING_PATH = "docs/simulator/physics_iso_source_capsule_locked_implementation_binding_v1.json"
LOCKED_BINDING_BYTES = 715
LOCKED_BINDING_SHA256 = "cd8570a0de55c88463a3f122c5ae3e84e26d6d51aab4d00beaa8d8a2c4f08463"
LOCKED_BINDING_GIT_BLOB = "3c7e273e7ac68b5f9703a1fd3d04deb4c8f40854"
TREE_OBJECTS = (
    ("", CHECKPOINT_TREE, 675, "8a55609b2fe9987782147c8b381b291cebc1875e8a500b13acddf9632159b8f4"),
    ("docs", "806c8e22774a91419a570c466aea5961d522455d", 240,
     "aa3039884441191d32add637f6d3ab5100fc332463265291cf530c33550b9ea3"),
    ("docs/simulator", "8032c92872c131058faf6455166db463013ef808", 1_914,
     "44833a8440b6d51b738dabbf3e1cbf32d600884d35d65bf5a04dc08634a1de2e"),
    ("scripts", "eec0efc60eda531287cbc757dd7108ef634ae763", 75,
     "17214a5d89db4eb3fa997b21ca587e4ff79bb0bdb524505ec97fcf4ba602b780"),
    ("scripts/comparison", "7e15993efd39c8b131dd52c8bab471d949c03867", 2_681,
     "0af4a4910b662709a3be8802082b0bf75416c9e231df28ea7b30256150f926f7"),
)
EXPECTED_DRIVER_VERDICT_BYTES = 701
EXPECTED_DRIVER_VERDICT_SHA256 = "98ca685ce20eeff7ffc4701193510af56e29f42ad1d6df4b6190fb387a59bea3"
EXPECTED_DRIVER_VERDICT = (
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
CONTROLLED_SIGNAL_NAMES = ("SIGHUP", "SIGINT", "SIGQUIT", "SIGTERM")
FAILURE_PREFIX = "physics ISO source-capsule qualification failed: "
RUNTIME_FAILURE = FAILURE_PREFIX + "external launcher remains nonauthorizing\n"
ARGUMENT_FAILURE = FAILURE_PREFIX + "this external launcher accepts no command-line arguments\n"
READ_CHUNK = 65_536
MAX_OBJECT_BYTES = 4_194_304
PROC_PIDLISTTHREADS = 6
CHILD_TIMEOUT_SECONDS = 45.0
CHILD_TERMINATE_SECONDS = 5.0
CHILD_STATUS_MAX = 135
CHILD_SUCCESS_FRAME = bytes.fromhex(
    "44414e434552535f4c4f434b45445f4452495645525f4348494c445f535543434553535f563100"
    "09e38f00fb277d2286829551646edc5c4b30db937d051d5bdc6eeb6aeb99647c"
    "a15637b595902ba412e4cf0d2128fbc6840a92f1e5865b93d76cfd16c3a5ac4c"
    "98ca685ce20eeff7ffc4701193510af56e29f42ad1d6df4b6190fb387a59bea3"
)
CHILD_SUCCESS_FRAME_SHA256 = "55519363f8096abe31c60cd45f6a8ed6f445ea8759d4a5d51835e98e7640e988"
GIT_CUSTODY_FRAME = struct.Struct(">4sBBHIIIIQQ32s32s")
GIT_CUSTODY_FRAME_BYTES = 104
GIT_CUSTODY_CALLS = 874
GIT_CUSTODY_MAX = 181_792
GIT_CUSTODY_FINAL_SHA256 = "243b93a2b81f57594049ab60edcc266a21c81b6af59a5c26170e9658b87836da"
GIT_CUSTODY_PREFIX = (
    ("commit", "a06be111354b0547d035d2bc48a2dbae33ecfba4"),
    ("commit", "df6721d45732e4dbc9a5764c84729c33f3e54e74"),
    ("tree", "c624c7a5179370e2af760b7e968284f10ea0fb80"),
    ("tree", "45124dd5514cb5d8f5295c8625746b4558d2a5c0"),
    ("tree", "e1e86af8de39621dad4d9c2f5af3b06b7277b8ad"),
    ("blob", "7ef8140bb7424db543859355b13611dcba66929f"),
    ("blob", "1b52312d7ba848b6cfadff747a46581ac7a9104d"),
)
GIT_CUSTODY_GENERATOR_PREFIX = (
    ("remote",), ("replace", "-l"),
    ("rev-parse", "--is-shallow-repository"),
    ("rev-parse", "--show-object-format"),
    ("config", "--local", "--name-only", "--null", "--list"),
    ("rev-parse", "--git-path", "objects"),
    ("rev-parse", "--git-path", "info/grafts"),
    ("rev-parse", "b484595e007cc9b13d4c41624dfb9b8cd8e91c6e^{tree}"),
    ("rev-parse", "a138ebfb13fe9ba33d723a5ed4f7f0a931eb3999^{tree}"),
    ("rev-parse", "5383edc2cd17e72f88c6629f0c3f25125a3bd5a5^{tree}"),
    ("rev-parse", "d1a9cd3bd3bf7ede17ed9f4014b84e64289a09af^{tree}"),
    ("rev-list", "--parents", "-n", "1", "b484595e007cc9b13d4c41624dfb9b8cd8e91c6e"),
    ("rev-list", "--parents", "-n", "1", "d1a9cd3bd3bf7ede17ed9f4014b84e64289a09af"),
    ("ls-tree", "-rz", "--full-tree", "-r", "d0f2a23cc6649887d326321ff3b3df721a3b4afc"),
    ("ls-tree", "-rz", "--full-tree", "-r", "e548979ec8c43716bd32da25793e60d2b3f45e94"),
)
GIT_ALLOWED_PATHS = (
    b"/Applications/Xcode.app/Contents/Developer/usr/bin/git",
)
CHILD_ALLOWED_PATHS = (
    b"/Applications/Xcode.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python",
)
CHILD_ENROLLMENT_PATHS = (b"/usr/bin/python3",) + CHILD_ALLOWED_PATHS
CHILD_ENV = {
    "HOME": "/nonexistent",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
}

EXTERNAL_VERDICT = (
    b'{"build_started":false,"child_stderr_emitted":false,"child_stdout_emitted":false,'
    b'"driver_cli_invoked":false,"external_launcher_authenticated":false,'
    b'"external_runtime_authenticated":false,"image_attached":false,"image_created":false,'
    b'"lock_file_bytes":1663,'
    b'"lock_file_sha256":"09e38f00fb277d2286829551646edc5c4b30db937d051d5bdc6eeb6aeb99647c",'
    b'"locked_driver_checkpoint_commit":"08dce107cf8982a156e8ed1081311c5cb3a5469d",'
    b'"locked_driver_checkpoint_tree":"7612befcdc80c05be45f0ac3d0558b4e0951c56e",'
    b'"mount_created":false,"network_accessed":false,"official_output_emitted":false,'
    b'"qualification_authorized":false,"qualification_receipt_emitted":false,'
    b'"schema_version":"dancers-planar-physics-iso-source-capsule-external-launcher-nonexecution-verdict-v1",'
    b'"simulator_started":false}\n'
)
EXTERNAL_VERDICT_BYTES = 773
EXTERNAL_VERDICT_SHA256 = "6cf722aa28311057e453bd3320e884d4fe0e11673c918d54191dbe000243dd7b"


CHILD_BOOTSTRAP = r'''import ctypes
import errno
import fcntl
import hashlib
import os
import resource
import signal
import struct
import subprocess
import sys
import threading
import time

DRIVER_BYTES = 47588
DRIVER_SHA256 = "a15637b595902ba412e4cf0d2128fbc6840a92f1e5865b93d76cfd16c3a5ac4c"
DRIVER_PATH = "scripts/comparison/run_locked_physics_iso_source_capsule_qualification_v1.py"
VERDICT = b'{"binding_file_bytes":715,"binding_file_sha256":"cd8570a0de55c88463a3f122c5ae3e84e26d6d51aab4d00beaa8d8a2c4f08463","checkpoint_commit":"a06be111354b0547d035d2bc48a2dbae33ecfba4","checkpoint_tree":"c624c7a5179370e2af760b7e968284f10ea0fb80","controlled_signal_names":["SIGHUP","SIGINT","SIGQUIT","SIGTERM"],"handlers_installed":true,"handlers_restored":true,"image_attached":false,"image_created":false,"mount_created":false,"native_thread_count_at_entry":1,"phase_a_authenticated":true,"qualification_receipt_emitted":false,"qualifier_live_guard_observed":true,"schema_version":"dancers-planar-physics-iso-source-capsule-locked-driver-nonexecution-verdict-v1","verifier_checkpoint_authenticated":true}\n'
FRAME = bytes.fromhex("44414e434552535f4c4f434b45445f4452495645525f4348494c445f535543434553535f56310009e38f00fb277d2286829551646edc5c4b30db937d051d5bdc6eeb6aeb99647ca15637b595902ba412e4cf0d2128fbc6840a92f1e5865b93d76cfd16c3a5ac4c98ca685ce20eeff7ffc4701193510af56e29f42ad1d6df4b6190fb387a59bea3")
CONTROLLED = (signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGTERM)
F_GETPATH = 50
PROC_PIDLISTTHREADS = 6
PROC_PIDTBSDINFO = 3
GIT_FRAME = struct.Struct(">4sBBHIIIIQQ32s32s")
GIT_PREFIX = (
    ("commit", "a06be111354b0547d035d2bc48a2dbae33ecfba4"),
    ("commit", "df6721d45732e4dbc9a5764c84729c33f3e54e74"),
    ("tree", "c624c7a5179370e2af760b7e968284f10ea0fb80"),
    ("tree", "45124dd5514cb5d8f5295c8625746b4558d2a5c0"),
    ("tree", "e1e86af8de39621dad4d9c2f5af3b06b7277b8ad"),
    ("blob", "7ef8140bb7424db543859355b13611dcba66929f"),
    ("blob", "1b52312d7ba848b6cfadff747a46581ac7a9104d"),
)
GIT_GENERATOR_PREFIX = (
    ("remote",),
    ("replace", "-l"),
    ("rev-parse", "--is-shallow-repository"),
    ("rev-parse", "--show-object-format"),
    ("config", "--local", "--name-only", "--null", "--list"),
    ("rev-parse", "--git-path", "objects"),
    ("rev-parse", "--git-path", "info/grafts"),
    ("rev-parse", "b484595e007cc9b13d4c41624dfb9b8cd8e91c6e^{tree}"),
    ("rev-parse", "a138ebfb13fe9ba33d723a5ed4f7f0a931eb3999^{tree}"),
    ("rev-parse", "5383edc2cd17e72f88c6629f0c3f25125a3bd5a5^{tree}"),
    ("rev-parse", "d1a9cd3bd3bf7ede17ed9f4014b84e64289a09af^{tree}"),
    ("rev-list", "--parents", "-n", "1", "b484595e007cc9b13d4c41624dfb9b8cd8e91c6e"),
    ("rev-list", "--parents", "-n", "1", "d1a9cd3bd3bf7ede17ed9f4014b84e64289a09af"),
    ("ls-tree", "-rz", "--full-tree", "-r", "d0f2a23cc6649887d326321ff3b3df721a3b4afc"),
    ("ls-tree", "-rz", "--full-tree", "-r", "e548979ec8c43716bd32da25793e60d2b3f45e94"),
)
GIT_CALLS = 874
GIT_FINAL_SHA256 = "243b93a2b81f57594049ab60edcc266a21c81b6af59a5c26170e9658b87836da"
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0", "GIT_PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0", "HOME": "/nonexistent", "LANG": "C",
    "LC_ALL": "C", "PATH": "/usr/bin:/bin",
}
GIT_PATHS = (
    b"/Applications/Xcode.app/Contents/Developer/usr/bin/git",
)
ORIGINAL_POPEN = subprocess.Popen
git_sequence = 0
git_unresolved = False
git_digests = []
suppressed_child_cancellation = None
suppress_child_cancellation = False

class ChildCancellation(BaseException):
    pass

def cancel(number, _frame):
    global suppressed_child_cancellation
    cancellation = ChildCancellation("controlled signal %d" % number)
    if suppress_child_cancellation:
        if suppressed_child_cancellation is None:
            suppressed_child_cancellation = cancellation
        return
    raise cancellation

class ProcBsdInfo(ctypes.Structure):
    _fields_ = [
        ("head", ctypes.c_uint32 * 12),
        ("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32),
        ("nfiles", ctypes.c_uint32), ("pgid", ctypes.c_uint32),
        ("pjobc", ctypes.c_uint32), ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32), ("nice", ctypes.c_int32),
        ("start_sec", ctypes.c_uint64), ("start_usec", ctypes.c_uint64),
    ]

def process_identity_once(pid, expected_parent):
    if ctypes.sizeof(ProcBsdInfo) != 136:
        raise RuntimeError("Git process identity layout differs")
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    proc_pidinfo = libproc.proc_pidinfo
    proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    proc_pidinfo.restype = ctypes.c_int
    proc_pidpath = libproc.proc_pidpath
    proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    proc_pidpath.restype = ctypes.c_int
    info = ProcBsdInfo()
    returned = proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, ctypes.byref(info), ctypes.sizeof(info))
    if returned != 136:
        raise RuntimeError("Git process identity is unavailable")
    fields = list(info.head)
    observed_pid, ppid, uid, gid = fields[3], fields[4], fields[5], fields[6]
    if observed_pid != pid or ppid != expected_parent or uid != os.geteuid() or gid != os.getegid():
        raise RuntimeError("Git process ownership differs")
    if info.pgid != pid or os.getsid(pid) != pid or info.start_usec >= 1000000:
        raise RuntimeError("Git process session identity differs")
    buffer = ctypes.create_string_buffer(4096)
    path_bytes = proc_pidpath(pid, buffer, 4096)
    if path_bytes < 1 or path_bytes > 4095:
        raise RuntimeError("Git process path is unavailable")
    raw_path = bytes(buffer.raw[:path_bytes])
    text_path = raw_path.decode("utf-8", "strict")
    if raw_path not in (b"/usr/bin/git",) + GIT_PATHS or not text_path.startswith("/") or os.path.normpath(text_path) != text_path:
        raise RuntimeError("Git process path differs")
    return (pid, ppid, int(info.pgid), uid, gid, int(info.start_sec), int(info.start_usec), hashlib.sha256(raw_path).digest())

def process_identity(pid, expected_parent):
    deadline = time.monotonic() + 0.25
    anchor = None
    candidate = None
    matches = 0
    final_digests = {hashlib.sha256(path).digest() for path in GIT_PATHS}
    while time.monotonic() < deadline:
        observed = process_identity_once(pid, expected_parent)
        immutable = observed[:7]
        if anchor is None:
            anchor = immutable
        elif immutable != anchor:
            raise RuntimeError("Git process changed during exec stabilization")
        if observed[7] in final_digests:
            if observed == candidate:
                matches += 1
            else:
                candidate = observed
                matches = 1
            if matches == 2:
                return observed
        time.sleep(0.002)
    raise RuntimeError("Git executable path did not stabilize")

def frame_bytes(kind, sequence, argv_digest, identity=None):
    if identity is None:
        pid = ppid = pgid = uid = start_sec = start_usec = 0
        path_digest = b"\0" * 32
    else:
        pid, ppid, pgid, uid, _gid, start_sec, start_usec, path_digest = identity
    return GIT_FRAME.pack(b"DGIT", 1, kind, sequence, pid, ppid, pgid, uid, start_sec, start_usec, argv_digest, path_digest)

def write_git_frame(frame):
    if type(frame) is not bytes or len(frame) != 104 or os.write(4, frame) != 104:
        raise RuntimeError("Git custody frame write differed")

def git_group_absent(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return True
    return False

def git_group_absent_twice(pid):
    stable = 0
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if git_group_absent(pid):
            stable += 1
            if stable == 2:
                return True
        else:
            stable = 0
        time.sleep(0.005)
    return False

def no_direct_children_twice(pid=None):
    if pid is None:
        pid = os.getpid()
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    function = libproc.proc_listchildpids
    function.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    function.restype = ctypes.c_int
    for _ in range(2):
        values = (ctypes.c_int * 2)()
        if function(pid, ctypes.byref(values), ctypes.sizeof(values)) != 0:
            return False
    return True

def owned_git_stop_snapshot(pid):
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    proc_pidinfo = libproc.proc_pidinfo
    proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    proc_pidinfo.restype = ctypes.c_int
    info = ProcBsdInfo()
    if proc_pidinfo(pid, PROC_PIDTBSDINFO, 0, ctypes.byref(info), ctypes.sizeof(info)) != 136:
        raise RuntimeError("owned Git stop identity is unavailable")
    fields = list(info.head)
    if (
        fields[1] != 4 or fields[2] != signal.SIGSTOP or fields[3] != pid
        or fields[4] != os.getpid() or fields[5] != os.geteuid()
        or fields[6] != os.getegid() or info.pgid != pid
        or os.getsid(pid) != pid or info.start_usec >= 1000000
    ):
        raise RuntimeError("owned Git stop identity differs")
    return (
        pid, fields[4], int(info.pgid), fields[5], fields[6],
        int(info.start_sec), int(info.start_usec), fields[1], fields[2],
    )

def stop_owned_git(process, expected_identity=None):
    pid = process.pid
    if process.poll() is not None:
        process.wait(timeout=0)
        if not git_group_absent_twice(pid):
            retain_git_custody_forever()
        return False
    if expected_identity is not None and process_identity(pid, os.getpid()) != expected_identity:
        raise RuntimeError("Git identity changed before stop")
    os.killpg(pid, signal.SIGSTOP)
    deadline = time.monotonic() + 5
    candidate = None
    matches = 0
    while time.monotonic() < deadline:
        try:
            observed = owned_git_stop_snapshot(pid)
        except RuntimeError:
            time.sleep(0.002)
            continue
        if observed == candidate:
            matches += 1
        else:
            candidate = observed
            matches = 1
        if matches == 2:
            break
        time.sleep(0.002)
    if matches != 2 or not no_direct_children_twice(pid):
        raise RuntimeError("owned stopped Git custody differs")
    if owned_git_stop_snapshot(pid) != candidate:
        raise RuntimeError("owned Git changed at its stopped kill gate")
    os.killpg(pid, signal.SIGKILL)
    process.wait(timeout=5)
    if not git_group_absent_twice(pid):
        raise RuntimeError("owned Git group remained after cleanup")
    return True

def retain_git_custody_forever():
    global suppress_child_cancellation
    suppress_child_cancellation = True
    while True:
        try:
            signal.pthread_sigmask(signal.SIG_BLOCK, set(CONTROLLED))
        except BaseException:
            pass
        try:
            time.sleep(3600)
        except BaseException:
            pass

def block_git_cleanup_persistently():
    first = None
    while True:
        try:
            signal.pthread_sigmask(signal.SIG_BLOCK, set(CONTROLLED))
        except BaseException as exc:
            if first is None:
                first = exc
        try:
            if set(CONTROLLED).issubset(signal.pthread_sigmask(signal.SIG_BLOCK, set())):
                return first
        except BaseException as exc:
            if first is None:
                first = exc

def restore_git_mask_persistently(prior_mask):
    first = None
    while True:
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, prior_mask)
        except BaseException as exc:
            if first is None:
                first = exc
        try:
            if signal.pthread_sigmask(signal.SIG_BLOCK, set()) == prior_mask:
                return first
        except BaseException as exc:
            if first is None:
                first = exc

def cleanup_exact_git(process, identity):
    pid = process.pid
    stop_owned_git(process, identity)
    if not git_group_absent_twice(pid):
        raise RuntimeError("Git process group remained after cleanup")

def cleanup_owned_git_without_identity(process):
    pid = process.pid
    if type(pid) is not int or pid <= 1:
        raise RuntimeError("owned Git pid differs")
    stop_owned_git(process)
    if git_group_absent_twice(pid):
        return
    # Once an identity-less leader has been reaped, signalling its numeric
    # process group could target a reused id.  Retain the Python owner and its
    # unresolved BEGIN instead of abandoning custody.
    retain_git_custody_forever()

def validated_git_argv(sequence, repository, observed):
    if type(observed) is not list or any(type(token) is not str for token in observed):
        raise RuntimeError("Git argv type differs")
    common = ["/usr/bin/git", "--no-pager", "-C", repository]
    if observed[:4] != common:
        raise RuntimeError("Git argv prefix differs")
    tail = tuple(observed[4:])
    if sequence < len(GIT_PREFIX):
        kind, oid = GIT_PREFIX[sequence]
        expected = ("cat-file", kind, oid)
    elif sequence < 22:
        expected = GIT_GENERATOR_PREFIX[sequence - 7]
    elif sequence < 448:
        expected = tail
        if len(tail) != 3 or tail[:2] != ("cat-file", "-s"):
            raise RuntimeError("Git size-query grammar differs")
        oid = tail[2]
        if len(oid) != 40 or any(character not in "0123456789abcdef" for character in oid):
            raise RuntimeError("Git size-query object id differs")
    elif sequence < GIT_CALLS:
        expected = tail
        if len(tail) != 3 or tail[:2] != ("cat-file", "blob"):
            raise RuntimeError("Git blob-query grammar differs")
        oid = tail[2]
        if len(oid) != 40 or any(character not in "0123456789abcdef" for character in oid):
            raise RuntimeError("Git blob-query object id differs")
    else:
        raise RuntimeError("Git custody sequence exceeds its bound")
    if tail != expected:
        raise RuntimeError("Git argv phase differs")
    normalized = common[:]
    normalized[3] = "<REPOSITORY>"
    normalized.extend(tail)
    return normalized

def install_git_wrapper(repository):
    global git_sequence, git_unresolved

    def tracked_popen(*args, **kwargs):
        global git_sequence, git_unresolved
        global suppress_child_cancellation, suppressed_child_cancellation
        if git_sequence >= GIT_CALLS or git_unresolved:
            raise RuntimeError("Git custody sequence differs")
        if len(args) != 1:
            raise RuntimeError("Git argv differs")
        normalized_argv = validated_git_argv(git_sequence, repository, args[0])
        if set(kwargs) != {"stdin", "stdout", "stderr", "env", "start_new_session", "close_fds"}:
            raise RuntimeError("Git keyword surface differs")
        if not (
            kwargs["stdin"] == subprocess.DEVNULL
            and kwargs["stdout"] == subprocess.PIPE
            and kwargs["stderr"] == subprocess.PIPE
            and type(kwargs["env"]) is dict and kwargs["env"] == GIT_ENV
            and kwargs["start_new_session"] is True
            and kwargs["close_fds"] is True
        ):
            raise RuntimeError("Git process options differ")
        encoded = [token.encode("utf-8", "strict") for token in normalized_argv]
        argv_digest = hashlib.sha256(b"\0".join(encoded) + b"\0").digest()
        prior_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set(CONTROLLED))
        if prior_mask not in (set(), set(CONTROLLED)):
            signal.pthread_sigmask(signal.SIG_SETMASK, prior_mask)
            raise RuntimeError("Git wrapper prior signal mask differs")
        process = None
        identity = None
        sequence = git_sequence
        primary = None
        cleanup_error = None
        begin_written = False
        resolution_write_started = False
        terminal_written = False
        suppressed_child_cancellation = None

        def retire_process():
            nonlocal identity
            if process is None:
                return
            if identity is None and process.poll() is None:
                try:
                    identity = process_identity(process.pid, os.getpid())
                except BaseException:
                    identity = None
            if identity is None:
                cleanup_owned_git_without_identity(process)
            else:
                cleanup_exact_git(process, identity)

        def write_cleaned_terminal():
            nonlocal terminal_written
            global git_sequence, git_unresolved
            if not git_unresolved:
                return
            if not begin_written:
                retain_git_custody_forever()
            if process is None:
                if not no_direct_children_twice():
                    retain_git_custody_forever()
                cleaned_identity = None
            else:
                retire_process()
                cleaned_identity = (
                    process.pid, 0, 0, 0, 0, 0, 0, b"\0" * 32,
                )
            # A resolution write may have put any prefix (or the whole frame)
            # into fd4.  The owned Git is now retired, but appending kind 4
            # could misalign the stream or create a third frame, so retain the
            # resolved/ambiguous owner instead.
            if resolution_write_started:
                retain_git_custody_forever()
            try:
                write_git_frame(
                    frame_bytes(4, sequence, argv_digest, cleaned_identity),
                )
            except BaseException:
                retain_git_custody_forever()
            terminal_written = True
            git_sequence += 1
            git_unresolved = False

        try:
            git_unresolved = True
            write_git_frame(frame_bytes(1, sequence, argv_digest))
            begin_written = True
            git_digests.append(argv_digest)
            process = ORIGINAL_POPEN(*args, **kwargs)
            if type(process) is not ORIGINAL_POPEN or type(process.pid) is not int or process.pid <= 1:
                raise RuntimeError("Git Popen result differs")
            try:
                identity = process_identity(process.pid, os.getpid())
            except BaseException:
                if process.poll() is None:
                    raise
                process.wait(timeout=0)
                if not git_group_absent_twice(process.pid):
                    raise RuntimeError("fast Git process group remained")
                fast_identity = (process.pid, 0, 0, 0, 0, 0, 0, b"\0" * 32)
                resolution_write_started = True
                write_git_frame(frame_bytes(3, sequence, argv_digest, fast_identity))
            else:
                resolution_write_started = True
                write_git_frame(frame_bytes(2, sequence, argv_digest, identity))
            terminal_written = True
            git_sequence += 1
            git_unresolved = False
        except BaseException as operation_error:
            primary = operation_error
        if primary is not None and git_unresolved:
            try:
                write_cleaned_terminal()
            except BaseException as exc:
                cleanup_error = exc
        if cleanup_error is not None and git_unresolved:
            retain_git_custody_forever()
        # Suppress repeated handler escapes while restoring: pending controlled
        # signals are recorded, the Git process is retired under a re-established
        # block, and the exact prior mask is always the last mutable operation.
        suppress_child_cancellation = True
        restoration_error = restore_git_mask_persistently(prior_mask)
        pending_cancellation = suppressed_child_cancellation
        if restoration_error is not None or pending_cancellation is not None:
            block_error = block_git_cleanup_persistently()
            if cleanup_error is None and block_error is not None:
                cleanup_error = block_error
            if process is not None:
                try:
                    retire_process()
                except BaseException as retire_error:
                    if cleanup_error is None:
                        cleanup_error = retire_error
            if cleanup_error is not None:
                retain_git_custody_forever()
            retry_error = restore_git_mask_persistently(prior_mask)
            if restoration_error is None:
                restoration_error = retry_error
            elif retry_error is not None:
                try:
                    restoration_error.git_mask_retry_error = retry_error
                except Exception:
                    pass
            if suppressed_child_cancellation is not None:
                pending_cancellation = suppressed_child_cancellation
        suppress_child_cancellation = False
        suppressed_child_cancellation = None
        if pending_cancellation is not None:
            if primary is None or isinstance(primary, Exception):
                if primary is not None:
                    try:
                        pending_cancellation.git_operation_error = primary
                    except Exception:
                        pass
                primary = pending_cancellation
            else:
                try:
                    primary.git_cancellation_error = pending_cancellation
                except Exception:
                    pass
        if restoration_error is not None and primary is None:
            primary = restoration_error
        if primary is not None:
            if cleanup_error is not None:
                try:
                    primary.git_cleanup_error = cleanup_error
                except Exception:
                    pass
            if restoration_error is not None and restoration_error is not primary:
                try:
                    primary.git_mask_restoration_error = restoration_error
                except Exception:
                    pass
            raise primary
        if not terminal_written:
            raise RuntimeError("Git custody terminal frame was not written")
        return process

    subprocess.Popen = tracked_popen
    if subprocess.Popen is not tracked_popen:
        raise RuntimeError("Git wrapper installation differs")
    return tracked_popen

def native_thread_proof():
    if sys.platform != "darwin":
        raise RuntimeError("isolated native platform differs")
    system = ctypes.CDLL(None, use_errno=True)
    pthread_main_np = system.pthread_main_np
    pthread_main_np.argtypes = []
    pthread_main_np.restype = ctypes.c_int
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    proc_pidinfo = libproc.proc_pidinfo
    proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    proc_pidinfo.restype = ctypes.c_int
    if pthread_main_np() != 1:
        raise RuntimeError("isolated native main thread differs")
    identities = []
    for _ in range(2):
        values = (ctypes.c_uint64 * 2)()
        returned = proc_pidinfo(os.getpid(), PROC_PIDLISTTHREADS, 0, ctypes.byref(values), ctypes.sizeof(values))
        if returned != 8 or values[0] == 0 or values[1] != 0:
            raise RuntimeError("isolated native thread census differs")
        identities.append(int(values[0]))
    if identities[0] != identities[1]:
        raise RuntimeError("isolated native thread identity changed")

def descriptor_snapshot():
    descriptor_limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    if type(descriptor_limit) is not int or descriptor_limit < 4 or descriptor_limit > 1048576:
        raise RuntimeError("isolated descriptor limit differs")
    result = []
    for descriptor in range(descriptor_limit):
        try:
            info = os.fstat(descriptor)
        except OSError as exc:
            if exc.errno == errno.EBADF:
                continue
            raise
        result.append((descriptor, info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_uid, info.st_gid, info.st_rdev, os.get_inheritable(descriptor), fcntl.fcntl(descriptor, fcntl.F_GETFD), fcntl.fcntl(descriptor, fcntl.F_GETFL)))
    numbers = [item[0] for item in result]
    if numbers[:5] != [0, 1, 2, 3, 4] or len(numbers) not in (5, 6):
        raise RuntimeError("isolated child descriptor set differs")
    if len(numbers) == 6:
        extra = numbers[5]
        path = fcntl.fcntl(extra, F_GETPATH, b"\0" * 1024).split(b"\0", 1)[0]
        if path != b"/dev/urandom" or os.get_inheritable(extra):
            raise RuntimeError("isolated runtime descriptor differs")
    pipe_identities = set()
    for item in result[:5]:
        descriptor, device, inode, mode = item[:4]
        access = item[-1] & os.O_ACCMODE
        expected_access = os.O_RDONLY if descriptor == 0 else os.O_WRONLY
        if not (mode & 0o170000) == 0o010000 or access != expected_access or item[-2] != 0:
            raise RuntimeError("isolated pipe descriptor differs")
        pipe_identities.add((device, inode))
    if len(pipe_identities) != 5:
        raise RuntimeError("isolated pipes are not distinct")
    return tuple(result)

def state_proof(expected_descriptors):
    if sys.gettrace() is not None or sys.getprofile() is not None or sys.argv != ["-c", path]:
        raise RuntimeError("isolated hooks or argv changed")
    if threading.current_thread() is not threading.main_thread() or threading.enumerate() != [threading.main_thread()] or threading.active_count() != 1:
        raise RuntimeError("isolated Python thread state differs")
    if os.getpid() != os.getpgrp() or os.getsid(0) != os.getpid():
        raise RuntimeError("isolated process ownership differs")
    if signal.pthread_sigmask(signal.SIG_BLOCK, set()) != set():
        raise RuntimeError("isolated signal mask differs")
    if any(signal.getsignal(number) is not cancel for number in CONTROLLED):
        raise RuntimeError("isolated cancellation handler differs")
    native_thread_proof()
    observed = descriptor_snapshot()
    if expected_descriptors is not None and observed != expected_descriptors:
        raise RuntimeError("isolated descriptor identity changed")
    return observed

try:
    if signal.pthread_sigmask(signal.SIG_BLOCK, set()) != set():
        raise RuntimeError("isolated startup signal mask differs")
    startup_handlers = tuple(signal.getsignal(number) for number in CONTROLLED)
    if startup_handlers != (signal.SIG_DFL, signal.default_int_handler, signal.SIG_DFL, signal.SIG_DFL):
        raise RuntimeError("isolated startup signal dispositions differ")
    for number, prior in zip(CONTROLLED, startup_handlers):
        if signal.signal(number, cancel) is not prior or signal.getsignal(number) is not cancel:
            raise RuntimeError("isolated cancellation installation differs")
    flags = sys.flags
    required = {
        "debug": 0, "inspect": 0, "interactive": 0, "optimize": 0,
        "dont_write_bytecode": 1, "no_user_site": 1, "no_site": 1,
        "ignore_environment": 1, "verbose": 0, "bytes_warning": 0,
        "quiet": 0, "hash_randomization": 1, "isolated": 1,
        "dev_mode": False, "utf8_mode": 1,
    }
    if any(getattr(flags, key, None) != value for key, value in required.items()):
        raise RuntimeError("isolated interpreter flags differ")
    if sys.gettrace() is not None or sys.getprofile() is not None or len(sys.argv) != 2:
        raise RuntimeError("isolated interpreter hooks or argv differ")
    path = sys.argv[1]
    if not path.startswith("/") or os.path.normpath(path) != path or not path.endswith("/" + DRIVER_PATH):
        raise RuntimeError("isolated driver path differs")
    repository = path[:-len("/" + DRIVER_PATH)]
    if not repository.startswith("/") or os.path.normpath(repository) != repository:
        raise RuntimeError("isolated repository path differs")
    launch_environment = {"HOME": "/nonexistent", "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"}
    if any(os.environ.get(key) != value for key, value in launch_environment.items()):
        raise RuntimeError("isolated launch environment differs")
    if any(key.startswith(("PYTHON", "DYLD", "GIT")) for key in os.environ):
        raise RuntimeError("isolated effective environment contains a forbidden key")
    tracked_popen = install_git_wrapper(repository)
    if subprocess.Popen is not tracked_popen:
        raise RuntimeError("Git wrapper installation changed")
    os.urandom(1)
    initial_descriptors = state_proof(None)
    raw = sys.stdin.buffer.read(DRIVER_BYTES + 1)
    if len(raw) != DRIVER_BYTES or hashlib.sha256(raw).hexdigest() != DRIVER_SHA256:
        raise RuntimeError("isolated child driver identity differs")
    namespace = {
        "__file__": path,
        "__name__": "dancers_isolated_locked_iso_driver_v1",
        "__package__": None,
    }
    code = compile(raw.decode("utf-8", "strict"), path, "exec", dont_inherit=True, optimize=0)
    exec(code, namespace)
    entry = namespace.get("run_nonexecuting_checkpoint")
    if not callable(entry) or namespace.get("SUCCESS_VERDICT") != VERDICT:
        raise RuntimeError("isolated locked driver API differs")
    observed = entry()
    if type(observed) is not bytes or observed != VERDICT:
        raise RuntimeError("isolated locked driver verdict differs")
    if git_sequence != GIT_CALLS or git_unresolved or len(git_digests) != GIT_CALLS:
        raise RuntimeError("Git custody transcript is incomplete")
    transcript = hashlib.sha256(
        b"DANCERS_GIT_CUSTODY_SEQUENCE_V1\0" + b"".join(git_digests),
    ).hexdigest()
    if transcript != GIT_FINAL_SHA256:
        raise RuntimeError("Git custody transcript digest differs")
    if subprocess.Popen is not tracked_popen:
        raise RuntimeError("Git wrapper changed during driver execution")
    subprocess.Popen = ORIGINAL_POPEN
    if subprocess.Popen is not ORIGINAL_POPEN:
        raise RuntimeError("Git wrapper restoration differs")
    sys.stdout.flush()
    sys.stderr.flush()
    state_proof(initial_descriptors)
    offset = 0
    while offset < len(FRAME):
        written = os.write(3, FRAME[offset:])
        if written < 1:
            raise RuntimeError("isolated child frame write stopped")
        offset += written
    os.close(3)
except BaseException:
    os._exit(71)
os._exit(0)
'''


class LauncherError(RuntimeError):
    """The pinned outer-launch invariant failed."""


class LauncherCancellation(BaseException):
    """A controlled signal cancelled the isolated child transaction."""


_ACTIVE_SIGNAL_ENVELOPE = None  # type: Optional[Any]


def _controlled_signal_handler(number: int, _frame: Any) -> None:
    cancellation = LauncherCancellation(
        "controlled signal %d cancelled the launcher" % number,
    )
    envelope = _ACTIVE_SIGNAL_ENVELOPE
    if envelope is not None:
        if envelope.cancellation is None:
            envelope.cancellation = cancellation
        if (
            envelope.cleanup_mode or envelope.cancellation_raised
            or sys.exc_info()[0] is not None
        ):
            return
        envelope.cancellation_raised = True
        raise envelope.cancellation
    raise cancellation


def _repository_root() -> Path:
    return Path(os.path.abspath(__file__)).parents[2]


def _merge_failure(primary: Optional[BaseException], later: BaseException,
                   label: str) -> BaseException:
    if primary is None:
        return later
    if isinstance(primary, Exception) and not isinstance(later, Exception):
        try:
            later.pinned_launcher_displaced_ordinary_error = (label, primary)  # type: ignore[attr-defined]
        except Exception:
            pass
        return later
    try:
        accumulated = getattr(primary, "pinned_launcher_later_errors", None)
        if type(accumulated) is not list:
            accumulated = []
            primary.pinned_launcher_later_errors = accumulated  # type: ignore[attr-defined]
        accumulated.append((label, later))
    except Exception:
        pass
    return primary


def _merge_effect_failure(primary: Optional[BaseException], later: BaseException,
                          label: str) -> BaseException:
    """Give a controlled cancellation priority over an ordinary operation error."""

    if isinstance(later, LauncherCancellation) and (
        primary is None or isinstance(primary, Exception)
    ):
        if primary is not None:
            _merge_failure(later, primary, label)
        return later
    return _merge_failure(primary, later, label)


def _run_transport_effect(effect: Any) -> Any:
    """Reconcile one irreversible transport effect before signals can dispatch."""

    controlled = set(_controlled_signals())
    previous = _query_mask()
    primary = None  # type: Optional[BaseException]
    result = None
    blocked = False
    try:
        observed = _mask(signal.SIG_BLOCK, controlled)
        blocked = controlled.issubset(_query_mask())
        if observed != previous or not blocked:
            raise LauncherError("transport effect signal block differs")
        result = effect()
    except BaseException as exc:
        primary = exc
    restoration = None  # type: Optional[BaseException]
    if blocked:
        try:
            _mask(signal.SIG_SETMASK, previous)
            if _query_mask() != previous:
                raise LauncherError("transport effect signal restoration differs")
        except BaseException as exc:
            restoration = exc
    else:
        restoration = LauncherError("transport effect signals were not blocked")
    if restoration is not None:
        primary = _merge_effect_failure(
            primary, restoration, "transport effect mask restoration failed",
        )
    if primary is not None:
        raise primary
    return result


def _controlled_signals() -> Tuple[int, ...]:
    result = []  # type: List[int]
    for name in CONTROLLED_SIGNAL_NAMES:
        value = getattr(signal, name, None)
        if type(value) is not signal.Signals:
            raise LauncherError("a controlled signal is unavailable")
        result.append(int(value))
    if len(set(result)) != len(result):
        raise LauncherError("controlled signals are not distinct")
    return tuple(result)


def _mask(operation: int, values: Set[int]) -> Set[int]:
    function = getattr(signal, "pthread_sigmask", None)
    if not callable(function):
        raise LauncherError("signal masking is unavailable")
    try:
        return {int(value) for value in function(operation, values)}
    except OSError as exc:
        raise LauncherError("controlled-signal masking failed") from exc


def _query_mask() -> Set[int]:
    return _mask(signal.SIG_BLOCK, set())


def _require_default_sigchld() -> None:
    number = getattr(signal, "SIGCHLD", None)
    if type(number) is not signal.Signals or signal.getsignal(number) is not signal.SIG_DFL:
        raise LauncherError("SIGCHLD disposition differs")


def _native_thread_census_blocked(controlled: Set[int]) -> None:
    if not controlled.issubset(_query_mask()) or sys.platform != "darwin":
        raise LauncherError("native Darwin thread census precondition differs")
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
        raise LauncherError("native Darwin thread APIs are unavailable") from exc
    if pthread_main_np() != 1:
        raise LauncherError("pinned launcher is not on the native main thread")
    prior = None  # type: Optional[int]
    for _ in range(2):
        values = (ctypes.c_uint64 * 2)()
        returned = proc_pidinfo(
            os.getpid(), PROC_PIDLISTTHREADS, 0,
            ctypes.byref(values), ctypes.sizeof(values),
        )
        if returned != 8 or values[0] == 0 or values[1] != 0:
            raise LauncherError("native process does not have exactly one thread")
        if prior is not None and prior != int(values[0]):
            raise LauncherError("native main-thread identity changed")
        prior = int(values[0])


def _prove_single_main_thread() -> None:
    _require_default_sigchld()
    current = threading.current_thread()
    main = threading.main_thread()
    if current is not main or threading.enumerate() != [main] or threading.active_count() != 1:
        raise LauncherError("pinned launcher is not the sole Python main thread")
    controlled = set(_controlled_signals())
    previous = _query_mask()
    primary = None  # type: Optional[BaseException]
    try:
        observed = _mask(signal.SIG_BLOCK, controlled)
        if observed != previous or controlled & previous:
            raise LauncherError("controlled signal mask differs at launcher entry")
        _native_thread_census_blocked(controlled)
    except BaseException as exc:
        primary = exc
    restoration = None  # type: Optional[BaseException]
    try:
        _mask(signal.SIG_SETMASK, previous)
    except BaseException as exc:
        restoration = exc
    if restoration is not None:
        raise _merge_failure(
            primary, restoration, "launcher census mask restoration failed",
        )
    if primary is not None:
        raise primary


class _SignalEnvelope:
    """Own controlled parent signals across one child transaction."""

    def __init__(self) -> None:
        self.signals = _controlled_signals()
        self.previous_mask = set()  # type: Set[int]
        self.previous_handlers = {}  # type: Dict[int, Any]
        self.installed = []  # type: List[int]
        self.started = False
        self.blocked = False
        self.active = False
        self.restored = False
        self.cleanup_mode = False
        self.cancellation_raised = False
        self.cancellation = None  # type: Optional[LauncherCancellation]

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

    def install_blocked(self) -> None:
        global _ACTIVE_SIGNAL_ENVELOPE
        controlled = set(self.signals)
        self.previous_mask = _query_mask()
        self.started = True
        primary = None  # type: Optional[BaseException]
        try:
            observed = _mask(signal.SIG_BLOCK, controlled)
            self.blocked = True
            if observed != self.previous_mask or controlled & self.previous_mask:
                raise LauncherError("controlled signal state differs at child setup")
            _native_thread_census_blocked(controlled)
            for number in self.signals:
                self.previous_handlers[number] = signal.getsignal(number)
            if _ACTIVE_SIGNAL_ENVELOPE is not None:
                raise LauncherError("another controlled signal envelope is active")
            _ACTIVE_SIGNAL_ENVELOPE = self
            for number in self.signals:
                self.installed.append(number)
                prior = signal.signal(number, _controlled_signal_handler)
                if (
                    not self._handler_equal(prior, self.previous_handlers[number])
                    or signal.getsignal(number) is not _controlled_signal_handler
                ):
                    raise LauncherError("controlled handler installation differs")
            return
        except BaseException as exc:
            primary = exc
        cleanup = []  # type: List[BaseException]
        for _ in range(2):
            try:
                _mask(signal.SIG_BLOCK, controlled)
                if controlled.issubset(_query_mask()):
                    break
            except BaseException as exc:
                cleanup.append(exc)
        for number in reversed(self.installed):
            try:
                signal.signal(number, self.previous_handlers[number])
            except BaseException as exc:
                cleanup.append(exc)
        try:
            _mask(signal.SIG_SETMASK, self.previous_mask)
        except BaseException as exc:
            cleanup.append(exc)
        try:
            self.restored = all(
                self._handler_equal(signal.getsignal(number), self.previous_handlers[number])
                for number in self.installed
            ) and _query_mask() == self.previous_mask
        except BaseException as exc:
            cleanup.append(exc)
            self.restored = False
        self.blocked = not self.restored
        if self.restored and _ACTIVE_SIGNAL_ENVELOPE is self:
            _ACTIVE_SIGNAL_ENVELOPE = None
        for error in cleanup:
            primary = _merge_failure(primary, error, "signal installation rollback failed")
        assert primary is not None
        raise primary

    def activate(self) -> None:
        if not self.blocked or self.active or self.restored:
            raise LauncherError("controlled signal envelope activation state differs")
        for number in self.signals:
            if signal.getsignal(number) is not _controlled_signal_handler:
                raise LauncherError("controlled handler changed before activation")
        try:
            observed = _mask(signal.SIG_SETMASK, self.previous_mask)
            if not set(self.signals).issubset(observed) or _query_mask() != self.previous_mask:
                raise LauncherError("controlled signal activation mask differs")
            self.blocked = False
            self.active = True
        except BaseException:
            try:
                if _query_mask() == self.previous_mask:
                    self.blocked = False
                    self.active = True
            except BaseException:
                pass
            raise

    def block_for_cleanup(self) -> None:
        if self.restored:
            raise LauncherError("controlled signal envelope is already restored")
        controlled = set(self.signals)
        errors = []  # type: List[BaseException]
        exact = False
        for _ in range(2):
            try:
                _mask(signal.SIG_BLOCK, controlled)
            except BaseException as exc:
                errors.append(exc)
            try:
                observed = _query_mask()
                exact = observed == self.previous_mask | controlled
            except BaseException as exc:
                errors.append(exc)
                exact = False
            if exact:
                break
        self.blocked = exact
        self.active = not exact
        if not exact:
            errors.append(LauncherError("controlled signals were not blocked for cleanup"))
        if errors:
            combined = None  # type: Optional[BaseException]
            for error in errors:
                combined = _merge_failure(combined, error, "cleanup signal blocking failed")
            assert combined is not None
            raise combined

    def enter_cleanup_mode(self) -> None:
        if self.restored or not self.started:
            raise LauncherError("controlled cleanup mode state differs")
        self.cleanup_mode = True

    def restore(self) -> None:
        global _ACTIVE_SIGNAL_ENVELOPE
        if self.restored:
            raise LauncherError("controlled signal envelope was restored twice")
        errors = []  # type: List[BaseException]
        controlled = set(self.signals)
        blocked = False
        for _ in range(2):
            try:
                _mask(signal.SIG_BLOCK, controlled)
                blocked = controlled.issubset(_query_mask())
            except BaseException as exc:
                errors.append(exc)
            if blocked:
                break
        if not blocked:
            errors.append(LauncherError("controlled signals could not be blocked at teardown"))
        for number in reversed(self.installed):
            try:
                signal.signal(number, self.previous_handlers[number])
            except BaseException as exc:
                errors.append(exc)
        for number in self.installed:
            try:
                if not self._handler_equal(signal.getsignal(number), self.previous_handlers[number]):
                    errors.append(LauncherError("controlled handler restoration differs"))
            except BaseException as exc:
                errors.append(exc)
        try:
            _mask(signal.SIG_SETMASK, self.previous_mask)
        except BaseException as exc:
            errors.append(exc)
        exact = False
        try:
            exact = (
                _query_mask() == self.previous_mask
                and all(
                    self._handler_equal(signal.getsignal(number), self.previous_handlers[number])
                    for number in self.installed
                )
            )
        except BaseException as exc:
            errors.append(exc)
        self.restored = exact
        self.blocked = False if exact else blocked
        self.active = not exact
        if exact and _ACTIVE_SIGNAL_ENVELOPE is self:
            _ACTIVE_SIGNAL_ENVELOPE = None
        if not exact:
            errors.append(LauncherError("controlled signal restoration remained incomplete"))
        if errors:
            combined = None  # type: Optional[BaseException]
            for error in errors:
                combined = _merge_failure(combined, error, "controlled signal restoration failed")
            assert combined is not None
            raise combined


def _restore_signal_envelope_persistently(envelope: _SignalEnvelope) -> None:
    first = None  # type: Optional[BaseException]
    while not envelope.restored:
        try:
            envelope.restore()
        except BaseException as exc:
            first = _remember_cleanup_failure(
                first, exc, "signal restoration failures accumulated",
            )
        if not envelope.restored:
            first = _cleanup_pause(
                first, "signal restoration retry pause failed",
            )
    if first is not None:
        raise first


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
        stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o022
        and (
            info.st_uid == 0
            or (info.st_uid == os.geteuid() and info.st_gid == os.getegid())
        )
    )


def _trusted_regular(info: os.stat_result) -> bool:
    return (
        stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
        and info.st_gid == os.getegid() and info.st_nlink == 1
        and not info.st_mode & 0o022
    )


def _required_flag(name: str) -> int:
    value = getattr(os, name, None)
    if type(value) is not int or value == 0:
        raise LauncherError("required descriptor flag is unavailable")
    return value


class _DescriptorSession:
    def __init__(self, controlled: Sequence[int]) -> None:
        self.controlled = {int(value) for value in controlled}
        self.fds = []  # type: List[int]
        self.directories = []  # type: List[Tuple[int, str, int, Tuple[int, ...], Tuple[int, ...]]]
        self.files = []  # type: List[Tuple[int, str, int, Tuple[int, ...]]]
        self.cleanup_mask = _query_mask()
        self.pending_fd = -1
        self.closed = False

    def _open_tracked(self, name: str, flags: int,
                      parent: Optional[int] = None) -> int:
        previous = _query_mask()
        primary = None  # type: Optional[BaseException]
        cleanup = []  # type: List[BaseException]
        descriptor = -1
        slot = len(self.fds)
        try:
            observed = _mask(signal.SIG_BLOCK, self.controlled)
            if observed != previous:
                raise LauncherError("descriptor acquisition mask changed")
            self.fds.append(-1)
            descriptor = (
                os.open(name, flags) if parent is None
                else os.open(name, flags, dir_fd=parent)
            )
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
                        cleanup.append(close_error)
                    try:
                        os.fstat(self.pending_fd)
                    except OSError as state_error:
                        if state_error.errno == errno.EBADF:
                            closed = True
                            break
                        cleanup.append(state_error)
                if closed:
                    self.pending_fd = -1
                    if len(self.fds) > slot and self.fds[slot] == descriptor:
                        self.fds.pop()
                else:
                    cleanup.append(LauncherError("unregistered descriptor remained open"))
            if len(self.fds) > slot and self.fds[slot] == -1:
                self.fds.pop()
        restoration = None  # type: Optional[BaseException]
        try:
            _mask(signal.SIG_SETMASK, previous)
        except BaseException as exc:
            restoration = exc
        combined = primary
        for error in cleanup:
            combined = _merge_failure(combined, error, "descriptor registration cleanup failed")
        if restoration is not None:
            combined = _merge_failure(combined, restoration, "descriptor mask restoration failed")
        if combined is not None:
            raise combined
        return descriptor

    def _open_directory(self, parent: int, name: str) -> int:
        if (
            not name or name in {".", ".."} or "/" in name or "\x00" in name
            or unicodedata.normalize("NFC", name) != name
            or len(name.encode("utf-8")) > 255
        ):
            raise LauncherError("descriptor path component is invalid")
        flags = (
            os.O_RDONLY | _required_flag("O_CLOEXEC")
            | _required_flag("O_DIRECTORY") | _required_flag("O_NOFOLLOW")
        )
        parent_before = os.fstat(parent)
        child = self._open_tracked(name, flags, parent)
        child_info = os.fstat(child)
        named = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (
            not _trusted_directory(child_info)
            or _directory_identity(child_info) != _directory_identity(named)
            or _directory_identity(parent_before) != _directory_identity(os.fstat(parent))
        ):
            raise LauncherError("descriptor directory identity differs")
        self.directories.append((
            parent, name, child, _directory_identity(parent_before),
            _directory_identity(child_info),
        ))
        return child

    def open_repository(self, repository: Path) -> int:
        text = os.path.abspath(os.fspath(repository))
        pure = PurePosixPath(text)
        if not pure.is_absolute() or pure.as_posix() != text:
            raise LauncherError("repository path is not canonical absolute POSIX")
        flags = (
            os.O_RDONLY | _required_flag("O_CLOEXEC")
            | _required_flag("O_DIRECTORY") | _required_flag("O_NOFOLLOW")
        )
        current = self._open_tracked("/", flags)
        root = os.fstat(current)
        if not _trusted_directory(root) or root.st_uid != 0:
            raise LauncherError("filesystem root is not trusted")
        for component in pure.parts[1:]:
            current = self._open_directory(current, component)
        return current

    @staticmethod
    def _parts(path: str) -> Tuple[str, ...]:
        if (
            type(path) is not str or not path or path.startswith("/")
            or "\\" in path or "\x00" in path
            or unicodedata.normalize("NFC", path) != path
        ):
            raise LauncherError("repository-relative path is invalid")
        pure = PurePosixPath(path)
        if pure.as_posix() != path or any(part in {"", ".", ".."} for part in pure.parts):
            raise LauncherError("repository-relative path is not canonical")
        return tuple(pure.parts)

    def read_regular(self, base: int, path: str, maximum: int) -> bytes:
        if type(maximum) is not int or maximum < 1 or maximum > MAX_OBJECT_BYTES:
            raise LauncherError("descriptor read cap is invalid")
        parts = self._parts(path)
        parent = base
        for component in parts[:-1]:
            parent = self._open_directory(parent, component)
        flags = os.O_RDONLY | _required_flag("O_CLOEXEC") | _required_flag("O_NOFOLLOW")
        parent_before = os.fstat(parent)
        descriptor = self._open_tracked(parts[-1], flags, parent)
        before = os.fstat(descriptor)
        named = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if (
            not _trusted_regular(before)
            or _file_identity(before) != _file_identity(named)
            or before.st_size > maximum
            or _directory_identity(parent_before) != _directory_identity(os.fstat(parent))
        ):
            raise LauncherError("descriptor file identity differs")
        chunks = []  # type: List[bytes]
        total = 0
        while True:
            chunk = os.read(descriptor, min(READ_CHUNK, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise LauncherError("descriptor file exceeds its cap")
        after = os.fstat(descriptor)
        named_after = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if (
            _file_identity(before) != _file_identity(after)
            or _file_identity(before) != _file_identity(named_after)
            or total != before.st_size
            or _directory_identity(parent_before) != _directory_identity(os.fstat(parent))
        ):
            raise LauncherError("descriptor file changed while read")
        self.files.append((parent, parts[-1], descriptor, _file_identity(before)))
        return b"".join(chunks)

    def reauthenticate(self) -> None:
        for parent, name, child, parent_id, child_id in self.directories:
            if (
                _directory_identity(os.fstat(parent)) != parent_id
                or _directory_identity(os.fstat(child)) != child_id
                or _directory_identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != child_id
            ):
                raise LauncherError("descriptor ancestry changed")
        for parent, name, descriptor, identity in self.files:
            if (
                _file_identity(os.fstat(descriptor)) != identity
                or _file_identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) != identity
            ):
                raise LauncherError("authenticated file name changed")

    def close(self) -> None:
        if self.closed:
            raise LauncherError("descriptor session was closed twice")
        errors = []  # type: List[BaseException]
        try:
            _mask(signal.SIG_BLOCK, self.controlled)
        except BaseException as exc:
            errors.append(exc)
        targets = list(self.fds)
        if self.pending_fd >= 0:
            targets.append(self.pending_fd)
        remaining = []  # type: List[int]
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
            if closed and descriptor == self.pending_fd:
                self.pending_fd = -1
            elif not closed:
                errors.append(LauncherError("tracked descriptor remained open"))
                if descriptor != self.pending_fd:
                    remaining.append(descriptor)
        self.fds = list(reversed(remaining))
        mask_restored = False
        try:
            _mask(signal.SIG_SETMASK, self.cleanup_mask)
            mask_restored = _query_mask() == self.cleanup_mask
        except BaseException as exc:
            errors.append(exc)
        if not mask_restored:
            errors.append(LauncherError("descriptor cleanup mask differs"))
        self.closed = not self.fds and self.pending_fd < 0 and mask_restored
        if errors:
            combined = None  # type: Optional[BaseException]
            for error in errors:
                combined = _merge_failure(combined, error, "descriptor cleanup failed")
            assert combined is not None
            raise combined


def _close_session_bounded(session: _DescriptorSession) -> None:
    first = None  # type: Optional[BaseException]
    for _ in range(2):
        if session.closed:
            break
        try:
            session.close()
        except BaseException as exc:
            first = _merge_failure(first, exc, "descriptor cleanup failures accumulated")
    if not session.closed:
        first = _merge_failure(
            first, LauncherError("descriptor session did not close"),
            "descriptor cleanup remained incomplete",
        )
    if first is not None:
        raise first


def _strict_invocation_binding(raw: bytes) -> Mapping[str, Any]:
    if not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise LauncherError("invocation binding termination differs")

    def pairs(items: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
        result = {}  # type: Dict[str, Any]
        for key, value in items:
            if key in result:
                raise LauncherError("invocation binding contains a duplicate key")
            result[key] = value
        return result

    def reject_constant(_value: str) -> Any:
        raise LauncherError("invocation binding contains a non-finite number")

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
        raise LauncherError("invocation binding is not canonical JSON") from exc
    expected = {
        "checkpoint_commit": {
            "bytes": CHECKPOINT_COMMIT_BYTES,
            "oid": CHECKPOINT_COMMIT,
            "parent": CHECKPOINT_PARENT,
            "sha256": CHECKPOINT_COMMIT_SHA256,
        },
        "locked_files": [
            {
                "bytes": LOCKED_BINDING_BYTES,
                "git_blob": LOCKED_BINDING_GIT_BLOB,
                "path": LOCKED_BINDING_PATH,
                "role": "locked implementation binding",
                "sha256": LOCKED_BINDING_SHA256,
            },
            {
                "bytes": LOCKED_DRIVER_BYTES,
                "git_blob": LOCKED_DRIVER_GIT_BLOB,
                "path": LOCKED_DRIVER_PATH,
                "role": "locked nonexecuting driver",
                "sha256": LOCKED_DRIVER_SHA256,
            },
        ],
        "schema_version": INVOCATION_BINDING_SCHEMA,
        "tree_objects": [
            {"bytes": size, "oid": oid, "path": path, "sha256": digest}
            for path, oid, size, digest in TREE_OBJECTS
        ],
    }
    if canonical != raw or type(value) is not dict or value != expected:
        raise LauncherError("invocation binding values differ")
    return value


def _parse_commit(payload: bytes) -> Tuple[str, Tuple[str, ...]]:
    if not payload or b"\x00" in payload or b"\n\n" not in payload:
        raise LauncherError("commit payload is invalid")
    try:
        header = payload.split(b"\n\n", 1)[0].decode("ascii", "strict").splitlines()
    except UnicodeDecodeError as exc:
        raise LauncherError("commit header is not ASCII") from exc
    trees = [line[5:] for line in header if line.startswith("tree ")]
    parents = tuple(line[7:] for line in header if line.startswith("parent "))
    values = tuple(trees) + parents
    if (
        len(trees) != 1 or any(len(value) != 40 for value in values)
        or any(any(character not in "0123456789abcdef" for character in value) for value in values)
    ):
        raise LauncherError("commit topology is malformed")
    return trees[0], parents


def _parse_tree(payload: bytes) -> Mapping[str, Tuple[str, str]]:
    result = {}  # type: Dict[str, Tuple[str, str]]
    position = 0
    prior = None  # type: Optional[bytes]
    while position < len(payload):
        space = payload.find(b" ", position)
        nul = payload.find(b"\x00", space + 1 if space >= 0 else position)
        if space < 0 or nul < 0 or nul + 21 > len(payload):
            raise LauncherError("tree payload is truncated")
        try:
            mode = payload[position:space].decode("ascii", "strict")
            name = payload[space + 1:nul].decode("utf-8", "strict")
        except UnicodeDecodeError as exc:
            raise LauncherError("tree entry text is invalid") from exc
        if (
            mode not in {"40000", "100644", "100755"}
            or not name or name in {".", ".."} or "/" in name or "\x00" in name
            or unicodedata.normalize("NFC", name) != name or name in result
        ):
            raise LauncherError("tree entry is invalid")
        sort_key = name.encode("utf-8") + (b"/" if mode == "40000" else b"")
        if prior is not None and sort_key <= prior:
            raise LauncherError("tree ordering differs")
        prior = sort_key
        result[name] = (mode, payload[nul + 1:nul + 21].hex())
        position = nul + 21
    return result


def _read_loose_object(session: _DescriptorSession, objects: int,
                       oid: str, kind: str) -> bytes:
    if (
        type(oid) is not str or len(oid) != 40
        or any(character not in "0123456789abcdef" for character in oid)
        or kind not in {"blob", "commit", "tree"}
    ):
        raise LauncherError("loose-object request is invalid")
    compressed = session.read_regular(
        objects, oid[:2] + "/" + oid[2:], MAX_OBJECT_BYTES,
    )
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(compressed, MAX_OBJECT_BYTES + 1)
        raw += decoder.flush(MAX_OBJECT_BYTES + 1 - min(len(raw), MAX_OBJECT_BYTES + 1))
    except zlib.error as exc:
        raise LauncherError("loose Git object compression is invalid") from exc
    if (
        len(raw) > MAX_OBJECT_BYTES or not decoder.eof
        or decoder.unused_data or decoder.unconsumed_tail
        or hashlib.sha1(raw).hexdigest() != oid
    ):
        raise LauncherError("loose Git object identity differs")
    nul = raw.find(b"\x00")
    if nul < 1:
        raise LauncherError("loose Git object header is absent")
    try:
        header = raw[:nul].decode("ascii", "strict")
    except UnicodeDecodeError as exc:
        raise LauncherError("loose Git object header is invalid") from exc
    fields = header.split(" ")
    if (
        len(fields) != 2 or fields[0] != kind or not fields[1].isdigit()
        or fields[1] != str(int(fields[1]))
    ):
        raise LauncherError("loose Git object header differs")
    payload = raw[nul + 1:]
    if int(fields[1]) != len(payload):
        raise LauncherError("loose Git object length differs")
    return payload


def _resolve_blob(session: _DescriptorSession, objects: int,
                  root_tree: str, path: str) -> bytes:
    current = root_tree
    parts = path.split("/")
    for index, component in enumerate(parts):
        entries = _parse_tree(_read_loose_object(session, objects, current, "tree"))
        if component not in entries:
            raise LauncherError("checkpoint tree omits a pinned input")
        mode, oid = entries[component]
        if index < len(parts) - 1:
            if mode != "40000":
                raise LauncherError("checkpoint path crosses a blob")
            current = oid
        else:
            if mode != "100644":
                raise LauncherError("checkpoint pinned input mode differs")
            return _read_loose_object(session, objects, oid, "blob")
    raise LauncherError("checkpoint pinned path is empty")


def _authenticate_launcher_inputs(
    session: _DescriptorSession, repository_fd: int,
) -> Tuple[Mapping[str, Any], bytes]:
    invocation_raw = session.read_regular(
        repository_fd, INVOCATION_BINDING_PATH, INVOCATION_BINDING_BYTES,
    )
    if (
        len(invocation_raw) != INVOCATION_BINDING_BYTES
        or hashlib.sha256(invocation_raw).hexdigest() != INVOCATION_BINDING_SHA256
    ):
        raise LauncherError("invocation binding identity differs")
    invocation = _strict_invocation_binding(invocation_raw)
    objects = repository_fd
    for component in (".git", "objects"):
        objects = session._open_directory(objects, component)
    commit = _read_loose_object(session, objects, CHECKPOINT_COMMIT, "commit")
    if (
        len(commit) != CHECKPOINT_COMMIT_BYTES
        or hashlib.sha256(commit).hexdigest() != CHECKPOINT_COMMIT_SHA256
    ):
        raise LauncherError("launcher checkpoint commit payload differs")
    tree, parents = _parse_commit(commit)
    if tree != CHECKPOINT_TREE or parents != (CHECKPOINT_PARENT,):
        raise LauncherError("launcher checkpoint topology differs")
    parent = _read_loose_object(session, objects, CHECKPOINT_PARENT, "commit")
    parent_tree, _ = _parse_commit(parent)
    if parent_tree != CHECKPOINT_PARENT_TREE:
        raise LauncherError("launcher parent tree differs")
    tree_entries = {}  # type: Dict[str, Mapping[str, Tuple[str, str]]]
    for path, oid, size, digest in TREE_OBJECTS:
        payload = _read_loose_object(session, objects, oid, "tree")
        if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
            raise LauncherError("locked raw tree payload differs")
        tree_entries[path] = _parse_tree(payload)
    required_links = (
        ("", "docs", "40000", TREE_OBJECTS[1][1]),
        ("docs", "simulator", "40000", TREE_OBJECTS[2][1]),
        ("", "scripts", "40000", TREE_OBJECTS[3][1]),
        ("scripts", "comparison", "40000", TREE_OBJECTS[4][1]),
        ("docs/simulator", LOCKED_BINDING_PATH.rsplit("/", 1)[1], "100644", LOCKED_BINDING_GIT_BLOB),
        ("scripts/comparison", LOCKED_DRIVER_PATH.rsplit("/", 1)[1], "100644", LOCKED_DRIVER_GIT_BLOB),
    )
    for parent_path, name, mode, oid in required_links:
        if tree_entries[parent_path].get(name) != (mode, oid):
            raise LauncherError("locked tree path proof differs")
    locked_binding = _read_loose_object(
        session, objects, LOCKED_BINDING_GIT_BLOB, "blob",
    )
    driver = _read_loose_object(
        session, objects, LOCKED_DRIVER_GIT_BLOB, "blob",
    )
    if (
        len(driver) != LOCKED_DRIVER_BYTES
        or hashlib.sha256(driver).hexdigest() != LOCKED_DRIVER_SHA256
        or len(locked_binding) != LOCKED_BINDING_BYTES
        or hashlib.sha256(locked_binding).hexdigest() != LOCKED_BINDING_SHA256
    ):
        raise LauncherError("checkpoint pinned blob identity differs")
    live_driver = session.read_regular(
        repository_fd, LOCKED_DRIVER_PATH, LOCKED_DRIVER_BYTES,
    )
    live_binding = session.read_regular(
        repository_fd, LOCKED_BINDING_PATH, LOCKED_BINDING_BYTES,
    )
    if live_driver != driver or live_binding != locked_binding:
        raise LauncherError("live pinned input differs from checkpoint")
    try:
        compile(
            driver.decode("utf-8", "strict"), LOCKED_DRIVER_PATH, "exec",
            dont_inherit=True, optimize=0,
        )
    except (UnicodeDecodeError, SyntaxError) as exc:
        raise LauncherError("locked driver is not valid Python") from exc
    session.reauthenticate()
    return invocation, driver


class _ProcBsdInfo(ctypes.Structure):
    _fields_ = [
        ("head", ctypes.c_uint32 * 12),
        ("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32),
        ("nfiles", ctypes.c_uint32), ("pgid", ctypes.c_uint32),
        ("pjobc", ctypes.c_uint32), ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32), ("nice", ctypes.c_int32),
        ("start_sec", ctypes.c_uint64), ("start_usec", ctypes.c_uint64),
    ]


def _darwin_process_identity(
    pid: int, allowed_paths: Sequence[bytes] = GIT_ALLOWED_PATHS,
) -> Optional[Tuple[Any, ...]]:
    if (
        sys.platform != "darwin" or type(pid) is not int or pid <= 1
        or ctypes.sizeof(_ProcBsdInfo) != 136
    ):
        raise LauncherError("Darwin process identity request differs")
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    proc_pidinfo = libproc.proc_pidinfo
    proc_pidinfo.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
        ctypes.c_void_p, ctypes.c_int,
    ]
    proc_pidinfo.restype = ctypes.c_int
    proc_pidpath = libproc.proc_pidpath
    proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    proc_pidpath.restype = ctypes.c_int
    info = _ProcBsdInfo()
    returned = proc_pidinfo(
        pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info),
    )
    if returned == 0:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        raise LauncherError("live process identity could not be read")
    if returned != 136:
        raise LauncherError("Darwin process identity length differs")
    fields = tuple(int(value) for value in info.head)
    observed_pid, ppid, uid, gid = fields[3], fields[4], fields[5], fields[6]
    if observed_pid != pid or info.start_usec >= 1_000_000:
        raise LauncherError("Darwin process identity values differ")
    buffer = ctypes.create_string_buffer(4096)
    path_bytes = proc_pidpath(pid, buffer, 4096)
    if path_bytes < 1 or path_bytes > 4095:
        raise LauncherError("Darwin process executable path is unavailable")
    raw_path = bytes(buffer.raw[:path_bytes])
    try:
        path_text = raw_path.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise LauncherError("Darwin process path is not UTF-8") from exc
    if (
        raw_path not in tuple(allowed_paths) or not path_text.startswith("/")
        or os.path.normpath(path_text) != path_text
    ):
        raise LauncherError("Darwin process executable path differs")
    try:
        sid = os.getsid(pid)
    except ProcessLookupError:
        return None
    return (
        pid, ppid, int(info.pgid), uid, gid, sid,
        int(info.start_sec), int(info.start_usec),
        hashlib.sha256(raw_path).digest(),
    )


def _darwin_process_status(pid: int) -> Optional[Tuple[int, ...]]:
    """Read the stable identity fields plus Darwin status/xstatus."""

    if sys.platform != "darwin" or type(pid) is not int or pid <= 1:
        raise LauncherError("Darwin process status request differs")
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    proc_pidinfo = libproc.proc_pidinfo
    proc_pidinfo.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_uint64,
        ctypes.c_void_p, ctypes.c_int,
    ]
    proc_pidinfo.restype = ctypes.c_int
    info = _ProcBsdInfo()
    returned = proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
    if returned == 0:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        raise LauncherError("live Darwin process status could not be read")
    if returned != 136:
        raise LauncherError("Darwin process status length differs")
    fields = tuple(int(value) for value in info.head)
    if fields[3] != pid or info.start_usec >= 1_000_000:
        raise LauncherError("Darwin process status identity differs")
    return (
        pid, fields[4], int(info.pgid), fields[5], fields[6],
        int(info.start_sec), int(info.start_usec), fields[1], fields[2],
    )


def _git_has_no_children(pid: int) -> bool:
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    function = libproc.proc_listchildpids
    function.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
    function.restype = ctypes.c_int
    for _ in range(2):
        values = (ctypes.c_int * 2)()
        returned = function(pid, ctypes.byref(values), ctypes.sizeof(values))
        if returned != 0:
            return False
    return True


def _stabilize_child_identity(pid: int) -> Tuple[Any, ...]:
    deadline = time.monotonic() + 1.0
    anchor = None  # type: Optional[Tuple[Any, ...]]
    candidate = None  # type: Optional[Tuple[Any, ...]]
    matches = 0
    final_digests = {
        hashlib.sha256(path).digest() for path in CHILD_ALLOWED_PATHS
    }
    while time.monotonic() < deadline:
        observed = _darwin_process_identity(pid, CHILD_ENROLLMENT_PATHS)
        if observed is None:
            raise LauncherError("isolated child exited during enrollment")
        immutable = observed[:8]
        if anchor is None:
            anchor = immutable
        elif immutable != anchor:
            raise LauncherError("isolated child changed during exec stabilization")
        if observed[8] in final_digests:
            if observed == candidate:
                matches += 1
            else:
                candidate = observed
                matches = 1
            if matches == 2:
                return observed
        time.sleep(0.002)
    raise LauncherError("isolated child executable path did not stabilize")


def _expected_git_argv_digest(repository: str, sequence: int) -> Optional[bytes]:
    if sequence < 0 or sequence >= GIT_CUSTODY_CALLS:
        raise LauncherError("Git custody sequence is outside its bound")
    if sequence < len(GIT_CUSTODY_PREFIX):
        kind, oid = GIT_CUSTODY_PREFIX[sequence]
        tail = ("cat-file", kind, oid)
    elif sequence < 22:
        tail = GIT_CUSTODY_GENERATOR_PREFIX[sequence - 7]
    else:
        return None
    values = ("/usr/bin/git", "--no-pager", "-C", "<REPOSITORY>", *tail)
    return hashlib.sha256(
        b"\0".join(value.encode("utf-8", "strict") for value in values) + b"\0",
    ).digest()


def _parse_custody_frames(state: "_TransportState", repository: str) -> None:
    while len(state.custody) - state.custody_offset >= GIT_CUSTODY_FRAME_BYTES:
        frame = bytes(
            state.custody[
                state.custody_offset:
                state.custody_offset + GIT_CUSTODY_FRAME_BYTES
            ]
        )
        state.custody_offset += GIT_CUSTODY_FRAME_BYTES
        values = GIT_CUSTODY_FRAME.unpack(frame)
        (
            magic, version, kind, sequence, pid, ppid, pgid, uid,
            start_sec, start_usec, argv_digest, path_digest,
        ) = values
        if magic != b"DGIT" or version != 1 or sequence >= GIT_CUSTODY_CALLS:
            raise LauncherError("Git custody frame header differs")
        expected_digest = _expected_git_argv_digest(repository, sequence)
        if expected_digest is not None and argv_digest != expected_digest:
            raise LauncherError("Git custody argv digest differs")
        if kind == 1:
            if (
                sequence != state.custody_next
                or state.custody_unresolved is not None
                or any((pid, ppid, pgid, uid, start_sec, start_usec))
                or path_digest != b"\0" * 32
            ):
                raise LauncherError("Git custody BEGIN frame differs")
            state.custody_unresolved = sequence
            state.custody_unresolved_digest = argv_digest
            state.custody_digests.append(argv_digest)
        elif kind == 2:
            if (
                state.custody_unresolved != sequence or state.pid is None
                or state.custody_unresolved_digest != argv_digest
                or pid <= 1 or ppid != state.pid or pgid != pid
                or uid != os.geteuid() or start_usec >= 1_000_000
                or path_digest not in {
                    hashlib.sha256(path).digest() for path in GIT_ALLOWED_PATHS
                }
            ):
                raise LauncherError("Git custody SPAWNED frame differs")
            enrolled = (
                pid, ppid, pgid, uid, os.getegid(), pid,
                start_sec, start_usec, path_digest,
            )
            observed = _darwin_process_identity(pid)
            if observed is not None:
                if not _same_enrolled_git(
                    enrolled, observed, state.pid,
                    state.wait_status is not None,
                ):
                    raise LauncherError("enrolled Git process identity differs")
                state.git_identities[sequence] = enrolled
            elif not _bounded_group_absent(pid):
                raise LauncherError("Git leader vanished but its group remained")
            state.custody_unresolved = None
            state.custody_unresolved_digest = None
            state.custody_next += 1
        elif kind == 3:
            if (
                state.custody_unresolved != sequence or pid <= 1
                or state.custody_unresolved_digest != argv_digest
                or any((ppid, pgid, uid, start_sec, start_usec))
                or path_digest != b"\0" * 32
                or _darwin_process_identity(pid) is not None
                or not _process_group_absent(pid)
            ):
                raise LauncherError("Git custody FAST_EXIT frame differs")
            state.custody_unresolved = None
            state.custody_unresolved_digest = None
            state.custody_next += 1
        elif kind == 4:
            if (
                state.custody_unresolved != sequence
                or state.custody_unresolved_digest != argv_digest
                or path_digest != b"\0" * 32
            ):
                raise LauncherError("Git custody CLEANED frame differs")
            if pid == 0:
                if any((ppid, pgid, uid, start_sec, start_usec)):
                    raise LauncherError("Git custody no-child terminal differs")
                if (
                    state.wait_status is None and state.pid is not None
                    and not _git_has_no_children(state.pid)
                ):
                    raise LauncherError("Git custody no-child proof differs")
            elif (
                pid <= 1 or any((ppid, pgid, uid, start_sec, start_usec))
            ):
                raise LauncherError("Git custody cleaned-process terminal differs")
            # The pid in a CLEANED terminal is historical and may already be
            # reusable.  It is syntax-checked only and is never queried or
            # signalled by the parent.
            state.custody_failure_terminal = True
            state.custody_unresolved = None
            state.custody_unresolved_digest = None
            state.custody_next += 1
        else:
            raise LauncherError("Git custody frame type differs")
        state.custody_frames += 1


def _ingest_custody_frames(state: "_TransportState", repository: str) -> None:
    try:
        _parse_custody_frames(state, repository)
    except BaseException:
        state.custody_ambiguous = True
        raise


class _TransportState:
    """Tracked raw descriptors, selector, child pid, and observed streams."""

    def __init__(self) -> None:
        self.fds = {}  # type: Dict[int, str]
        self.pending_fds = []  # type: List[Tuple[int, str]]
        self.selector = None
        self.selector_fd = -1
        self.pid = None  # type: Optional[int]
        self.child_identity = None  # type: Optional[Tuple[Any, ...]]
        self.child_stopped = False
        self.term_attempted = False
        self.preentry_stop_attempted = False
        self.preentry_kill_attempted = False
        self.repository = ""
        self.wait_status = None  # type: Optional[int]
        self.input_attempted = False
        self.input_offset = 0
        self.preinput_handshake = False
        self.stdout = bytearray()
        self.stderr = bytearray()
        self.status_frame = bytearray()
        self.custody = bytearray()
        self.custody_offset = 0
        self.custody_frames = 0
        self.custody_next = 0
        self.custody_unresolved = None  # type: Optional[int]
        self.custody_unresolved_digest = None  # type: Optional[bytes]
        self.custody_digests = []  # type: List[bytes]
        self.git_identities = {}  # type: Dict[int, Tuple[Any, ...]]
        self.custody_eof = False
        self.custody_ambiguous = False
        self.custody_failure_terminal = False
        self.transport_failure = False
        self.overflow_names = set()  # type: Set[str]
        self.open_names = set()  # type: Set[str]

    def _register_fd(self, descriptor: int, label: str) -> int:
        if (
            type(descriptor) is not int or descriptor < 0
            or descriptor in self.fds
        ):
            raise LauncherError("transport descriptor registration differs")
        os.fstat(descriptor)
        self.fds[descriptor] = label
        return descriptor

    def duplicate(self, source: int, label: str) -> int:
        command = getattr(fcntl, "F_DUPFD_CLOEXEC", None)
        if type(command) is not int:
            raise LauncherError("close-on-exec duplication is unavailable")
        descriptor = fcntl.fcntl(source, command, 3)
        self.pending_fds.append((descriptor, label))
        self._register_fd(descriptor, label)
        self.pending_fds.remove((descriptor, label))
        if os.get_inheritable(descriptor):
            raise LauncherError("transport duplicate is inheritable")
        return descriptor

    def pipe(self, label: str) -> Tuple[int, int]:
        pair = os.pipe()
        if (
            type(pair) is not tuple or len(pair) != 2
            or any(type(value) is not int or value < 0 for value in pair)
            or pair[0] == pair[1]
        ):
            raise LauncherError("transport pipe creation differs")
        pending = [(pair[0], label + " read"), (pair[1], label + " write")]
        self.pending_fds.extend(pending)
        for descriptor, endpoint in pending:
            self._register_fd(descriptor, endpoint)
            if os.get_inheritable(descriptor):
                raise LauncherError("transport pipe endpoint is inheritable")
        for item in pending:
            self.pending_fds.remove(item)
        return pair

    def create_selector(self) -> Any:
        selector = selectors.DefaultSelector()
        self.selector = selector
        descriptor = selector.fileno()
        self.selector_fd = descriptor
        self._register_fd(descriptor, "selector")
        if os.get_inheritable(descriptor):
            raise LauncherError("transport selector is inheritable")
        return selector

    def retire_fd(self, descriptor: int) -> None:
        if descriptor not in self.fds:
            raise LauncherError("transport descriptor was not tracked")
        errors = []  # type: List[BaseException]
        closed = False
        for _ in range(2):
            try:
                os.close(descriptor)
            except BaseException as exc:
                errors.append(exc)
            try:
                os.fstat(descriptor)
            except OSError as exc:
                if exc.errno == errno.EBADF:
                    closed = True
                else:
                    errors.append(exc)
            except BaseException as exc:
                errors.append(exc)
            if closed:
                break
        if closed:
            self.fds.pop(descriptor, None)
            self.pending_fds = [item for item in self.pending_fds if item[0] != descriptor]
            if descriptor == self.selector_fd:
                self.selector_fd = -1
        else:
            errors.append(LauncherError("transport descriptor remained open"))
        if errors:
            combined = None  # type: Optional[BaseException]
            for error in errors:
                combined = _merge_failure(combined, error, "transport descriptor close failed")
            assert combined is not None
            raise combined

    def retire_selector(self) -> None:
        selector = self.selector
        descriptor = self.selector_fd
        if selector is None and descriptor < 0:
            return
        errors = []  # type: List[BaseException]
        absent = False
        if selector is not None:
            for _ in range(2):
                try:
                    selector.close()
                except BaseException as exc:
                    errors.append(exc)
                if descriptor < 0:
                    absent = True
                else:
                    try:
                        os.fstat(descriptor)
                    except OSError as exc:
                        if exc.errno == errno.EBADF:
                            absent = True
                        else:
                            errors.append(exc)
                    except BaseException as exc:
                        errors.append(exc)
                if absent:
                    break
        if not absent and descriptor >= 0:
            try:
                self.retire_fd(descriptor)
            except BaseException as exc:
                errors.append(exc)
            absent = descriptor not in self.fds
        if absent:
            self.fds.pop(descriptor, None)
            self.selector = None
            self.selector_fd = -1
        else:
            errors.append(LauncherError("transport selector remained open"))
        if errors:
            combined = None  # type: Optional[BaseException]
            for error in errors:
                combined = _merge_failure(combined, error, "transport selector close failed")
            assert combined is not None
            raise combined

    def close_all(self) -> None:
        errors = []  # type: List[BaseException]
        try:
            self.retire_selector()
        except BaseException as exc:
            errors.append(exc)
        targets = list(self.fds)
        targets.extend(item[0] for item in self.pending_fds if item[0] not in targets)
        for descriptor in reversed(targets):
            if descriptor not in self.fds:
                absent = False
                try:
                    os.fstat(descriptor)
                except OSError as exc:
                    if exc.errno == errno.EBADF:
                        absent = True
                    else:
                        errors.append(exc)
                except BaseException as exc:
                    errors.append(exc)
                if absent:
                    self.pending_fds = [
                        item for item in self.pending_fds if item[0] != descriptor
                    ]
                    continue
                self.fds[descriptor] = "pending transport descriptor"
            try:
                self.retire_fd(descriptor)
            except BaseException as exc:
                errors.append(exc)
        if self.fds or self.pending_fds or self.selector is not None or self.selector_fd >= 0:
            errors.append(LauncherError("transport resources remained open"))
        if errors:
            combined = None  # type: Optional[BaseException]
            for error in errors:
                combined = _merge_failure(combined, error, "transport resource cleanup failed")
            assert combined is not None
            raise combined


def _child_wait_nonblocking(state: _TransportState) -> None:
    if state.pid is None or state.wait_status is not None:
        return

    def effect() -> None:
        waited_pid, status_value = os.waitpid(state.pid, os.WNOHANG)
        if waited_pid == state.pid:
            state.wait_status = status_value
        elif waited_pid != 0:
            raise LauncherError("isolated child wait identity differs")

    _run_transport_effect(effect)


def _read_child_channel(state: _TransportState, descriptor: int,
                        name: str) -> None:
    def effect() -> None:
        target = (
            state.stdout if name == "stdout" else
            state.stderr if name == "stderr" else
            state.status_frame if name == "status" else state.custody
        )
        maximum = (
            CHILD_STATUS_MAX if name == "status" else
            GIT_CUSTODY_MAX if name == "custody" else 0
        )
        overflowed = name in state.overflow_names
        requested = READ_CHUNK if overflowed else maximum + 1 - len(target)
        if requested < 1:
            state.transport_failure = True
            state.overflow_names.add(name)
            if name == "custody":
                state.custody_ambiguous = True
            requested = READ_CHUNK
            overflowed = True
        try:
            chunk = os.read(descriptor, requested)
        except BlockingIOError:
            return
        if chunk:
            if overflowed:
                return
            target.extend(chunk)
            if len(target) > maximum:
                state.transport_failure = True
                state.overflow_names.add(name)
                if name == "custody":
                    state.custody_ambiguous = True
                raise LauncherError("isolated child output exceeded its cap")
            if name == "custody":
                _ingest_custody_frames(state, state.repository)
            return
        if name == "custody":
            state.custody_eof = True
            if state.custody_offset != len(state.custody):
                state.custody_ambiguous = True
        if state.selector is not None:
            state.selector.unregister(descriptor)
        state.retire_fd(descriptor)
        state.open_names.discard(name)
        if name == "custody" and state.custody_ambiguous:
            raise LauncherError("Git custody frame is truncated")

    _run_transport_effect(effect)


def _write_driver_input(state: _TransportState, descriptor: int,
                        raw: bytes) -> None:
    def effect() -> None:
        remaining = len(raw) - state.input_offset
        if remaining <= 0:
            raise LauncherError("isolated child input offset differs")
        # Pessimistically relinquish all hard-kill authority before the first
        # kernel write.  A post-effect exception can never route cleanup back
        # through the pre-entry path.
        state.input_attempted = True
        try:
            written = os.write(
                descriptor,
                raw[state.input_offset:state.input_offset + READ_CHUNK],
            )
        except BlockingIOError:
            return
        if written < 1:
            raise LauncherError("isolated child input transport stopped")
        state.input_offset += written
        if state.input_offset == len(raw):
            if state.selector is not None:
                state.selector.unregister(descriptor)
            state.retire_fd(descriptor)
            state.open_names.discard("stdin")

    _run_transport_effect(effect)


def _drain_child_channels(state: _TransportState) -> None:
    for descriptor, name in tuple(state.fds.items()):
        if name not in {"stdout read", "stderr read", "status read", "custody read"}:
            continue
        _read_child_channel(state, descriptor, name.split(" ", 1)[0])


def _process_group_absent(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return True
    return False


def _bounded_group_absent(pgid: int, seconds: float = 0.25) -> bool:
    deadline = time.monotonic() + seconds
    stable = 0
    while time.monotonic() < deadline:
        if _process_group_absent(pgid):
            stable += 1
            if stable == 2:
                return True
        else:
            stable = 0
        time.sleep(0.005)
    return False


def _same_enrolled_git(enrolled: Tuple[Any, ...], observed: Tuple[Any, ...],
                       child_pid: int, child_reaped: bool) -> bool:
    immutable = (0, 2, 3, 4, 5, 6, 7, 8)
    allowed_parent = 1 if child_reaped else child_pid
    return (
        all(enrolled[index] == observed[index] for index in immutable)
        and observed[1] == allowed_parent
        and observed[2] == observed[0] and observed[5] == observed[0]
        and observed[3] == os.geteuid() and observed[4] == os.getegid()
    )


def _same_enrolled_child(enrolled: Tuple[Any, ...],
                         observed: Tuple[Any, ...]) -> bool:
    return (
        enrolled == observed and observed[0] == observed[2] == observed[5]
        and observed[1] == os.getpid() and observed[3] == os.geteuid()
        and observed[4] == os.getegid()
    )


def _signal_cooperative_term(state: _TransportState) -> bool:
    if (
        state.pid is None or state.child_identity is None
        or not state.input_attempted or not state.term_attempted
    ):
        raise LauncherError("isolated child identity was not enrolled")
    _child_wait_nonblocking(state)
    if state.wait_status is not None:
        return False
    observed = _darwin_process_identity(state.pid, CHILD_ALLOWED_PATHS)
    if observed is None or not _same_enrolled_child(state.child_identity, observed):
        raise LauncherError("isolated child identity changed before signal")
    os.kill(state.pid, signal.SIGTERM)
    return True


def _remember_cleanup_failure(
    first: Optional[BaseException], later: BaseException, label: str,
) -> BaseException:
    """Retain one primary failure and only a bounded count of later faults."""

    if first is None:
        try:
            later.pinned_launcher_repeated_cleanup_errors = 0  # type: ignore[attr-defined]
        except Exception:
            pass
        return later
    if isinstance(first, Exception) and not isinstance(later, Exception):
        try:
            later.pinned_launcher_displaced_ordinary_error = (label, first)  # type: ignore[attr-defined]
            later.pinned_launcher_repeated_cleanup_errors = 0  # type: ignore[attr-defined]
        except Exception:
            pass
        return later
    try:
        count = getattr(first, "pinned_launcher_repeated_cleanup_errors", 0)
        if type(count) is not int:
            count = 0
        first.pinned_launcher_repeated_cleanup_errors = min(count + 1, 1_000_000)  # type: ignore[attr-defined]
        if not hasattr(first, "pinned_launcher_first_later_cleanup_error"):
            first.pinned_launcher_first_later_cleanup_error = (label, later)  # type: ignore[attr-defined]
    except Exception:
        pass
    return first


def _cleanup_pause(first: Optional[BaseException],
                   label: str) -> Optional[BaseException]:
    try:
        time.sleep(0.005)
    except BaseException as exc:
        return _remember_cleanup_failure(first, exc, label)
    return first


def _retain_parent_custody_forever(state: _TransportState) -> None:
    """Deliberately retain descriptors/mask/owner when absence is unprovable."""

    controlled = set(_controlled_signals())
    while True:
        blocked = False
        while not blocked:
            try:
                _mask(signal.SIG_BLOCK, controlled)
                blocked = controlled.issubset(_query_mask())
            except BaseException:
                blocked = False
        try:
            _drain_child_channels(state)
        except BaseException:
            pass
        try:
            _child_wait_nonblocking(state)
        except BaseException:
            pass
        try:
            time.sleep(3600)
        except BaseException:
            pass


def _close_parent_input(state: _TransportState) -> Optional[BaseException]:
    first = None  # type: Optional[BaseException]
    for descriptor, label in tuple(state.fds.items()):
        if label != "stdin write":
            continue
        if state.selector is not None:
            try:
                state.selector.unregister(descriptor)
            except KeyError:
                pass
            except BaseException as exc:
                first = _remember_cleanup_failure(
                    first, exc, "stdin selector retirement failed",
                )
        try:
            state.retire_fd(descriptor)
        except BaseException as exc:
            first = _remember_cleanup_failure(
                first, exc, "stdin descriptor retirement failed",
            )
        if descriptor not in state.fds:
            state.open_names.discard("stdin")
    return first


def _wait_preentry_stop(state: _TransportState) -> Optional[BaseException]:
    first = None  # type: Optional[BaseException]
    if state.pid is None or state.wait_status is not None:
        return first
    deadline = time.monotonic() + CHILD_TERMINATE_SECONDS
    while (
        state.wait_status is None and not state.child_stopped
        and time.monotonic() < deadline
    ):
        def effect() -> None:
            waited_pid, status_value = os.waitpid(
                state.pid, os.WNOHANG | os.WUNTRACED,
            )
            if waited_pid == 0:
                return
            if waited_pid != state.pid:
                raise LauncherError("pre-entry stop wait identity differs")
            if (
                os.WIFSTOPPED(status_value)
                and os.WSTOPSIG(status_value) == signal.SIGSTOP
            ):
                state.child_stopped = True
                return
            if os.WIFEXITED(status_value) or os.WIFSIGNALED(status_value):
                state.wait_status = status_value
                return
            raise LauncherError("pre-entry stop wait status differs")

        try:
            _run_transport_effect(effect)
        except BaseException as exc:
            first = _remember_cleanup_failure(
                first, exc, "pre-entry stop observation failed",
            )
        if state.wait_status is None and not state.child_stopped:
            first = _cleanup_pause(first, "pre-entry stop wait pause failed")
    return first


def _stable_stopped_child_snapshot(
    state: _TransportState,
) -> Tuple[Tuple[Any, ...], Tuple[int, ...]]:
    if state.pid is None or not state.child_stopped or state.wait_status is not None:
        raise LauncherError("pre-entry stopped-child state differs")
    candidate = None  # type: Optional[Tuple[Tuple[Any, ...], Tuple[int, ...]]]
    matches = 0
    deadline = time.monotonic() + CHILD_TERMINATE_SECONDS
    while time.monotonic() < deadline:
        identity = _darwin_process_identity(state.pid, CHILD_ENROLLMENT_PATHS)
        status = _darwin_process_status(state.pid)
        observed = (identity, status)
        if (
            identity is None or status is None
            or identity[0] != identity[2] or identity[0] != identity[5]
            or identity[1] != os.getpid()
            or identity[3] != os.geteuid() or identity[4] != os.getegid()
            or status[:7] != (
                identity[0], identity[1], identity[2], identity[3],
                identity[4], identity[6], identity[7],
            )
            or status[7:] != (4, int(signal.SIGSTOP))
        ):
            raise LauncherError("pre-entry stopped-child snapshot differs")
        if observed == candidate:
            matches += 1
        else:
            candidate = observed
            matches = 1
        if matches == 2:
            return observed
        time.sleep(0.002)
    raise LauncherError("pre-entry stopped-child identity did not stabilize")


def _preentry_stop_kill_and_reap(state: _TransportState) -> None:
    if (
        state.pid is None or state.input_attempted
        or state.wait_status is not None or "stdin" not in state.open_names
        or state.stdout or state.stderr or state.status_frame
        or state.custody or state.custody_offset or state.custody_frames
        or state.custody_unresolved is not None or state.git_identities
        or state.custody_ambiguous or state.custody_failure_terminal
    ):
        raise LauncherError("pre-entry hard-kill authority is unavailable")
    if state.preentry_stop_attempted:
        raise LauncherError("pre-entry stop was already attempted")
    state.preentry_stop_attempted = True
    stop_error = None  # type: Optional[BaseException]
    try:
        os.kill(state.pid, signal.SIGSTOP)
    except BaseException as exc:
        stop_error = exc
    observed_error = _wait_preentry_stop(state)
    if stop_error is not None:
        observed_error = _remember_cleanup_failure(
            stop_error, observed_error, "pre-entry stop effect differed",
        ) if observed_error is not None else stop_error
    if state.wait_status is not None:
        input_error = _close_parent_input(state)
        if input_error is not None:
            observed_error = _remember_cleanup_failure(
                observed_error, input_error, "pre-entry input close failed",
            )
        if observed_error is not None:
            raise observed_error
        return
    if not state.child_stopped:
        if observed_error is not None:
            raise observed_error
        raise LauncherError("pre-entry child did not stop")

    first_snapshot = _stable_stopped_child_snapshot(state)
    for _ in range(2):
        _drain_child_channels(state)
        if (
            state.custody or state.custody_offset or state.custody_frames
            or state.stdout or state.stderr or state.status_frame
            or state.custody_unresolved is not None or state.git_identities
            or state.custody_ambiguous or state.custody_failure_terminal
        ):
            raise LauncherError("pre-entry stopped child emitted custody data")
    if not _git_has_no_children(state.pid):
        raise LauncherError("pre-entry stopped child has descendants")
    if _stable_stopped_child_snapshot(state) != first_snapshot:
        raise LauncherError("pre-entry stopped child changed before kill")
    if state.preentry_kill_attempted:
        raise LauncherError("pre-entry kill was already attempted")
    state.preentry_kill_attempted = True
    kill_error = None  # type: Optional[BaseException]
    try:
        os.kill(state.pid, signal.SIGKILL)
    except BaseException as exc:
        kill_error = exc
    deadline = time.monotonic() + CHILD_TERMINATE_SECONDS
    while state.wait_status is None and time.monotonic() < deadline:
        try:
            _child_wait_nonblocking(state)
        except BaseException as exc:
            kill_error = _remember_cleanup_failure(
                kill_error, exc, "pre-entry kill wait failed",
            )
        if state.wait_status is None:
            kill_error = _cleanup_pause(
                kill_error, "pre-entry kill wait pause failed",
            )
    if state.wait_status is None:
        if kill_error is not None:
            raise kill_error
        raise LauncherError("pre-entry killed child was not reaped")
    state.child_stopped = False
    input_error = _close_parent_input(state)
    if input_error is not None:
        kill_error = _remember_cleanup_failure(
            kill_error, input_error, "post-reap input close failed",
        )
    if not _bounded_group_absent(state.pid, CHILD_TERMINATE_SECONDS):
        group_error = LauncherError("pre-entry child group absence was not proved")
        kill_error = _remember_cleanup_failure(
            kill_error, group_error, "pre-entry group proof failed",
        )
    if kill_error is not None:
        raise kill_error


def _resume_failed_preentry_child(state: _TransportState) -> Optional[BaseException]:
    first = _close_parent_input(state)
    if state.child_stopped and state.pid is not None and state.wait_status is None:
        # The direct unreaped relation still pins this exact numeric PID.  Resume
        # only so that the closed stdin can drive the bootstrap to a natural exit.
        state.child_stopped = False
        try:
            os.kill(state.pid, signal.SIGCONT)
        except BaseException as exc:
            first = _remember_cleanup_failure(
                first, exc, "pre-entry child resume failed",
            )
    return first


def _send_cooperative_term_once(state: _TransportState) -> None:
    if state.term_attempted:
        return
    if (
        not state.input_attempted or state.pid is None
        or state.wait_status is not None
    ):
        raise LauncherError("cooperative TERM authority differs")
    state.term_attempted = True
    _signal_cooperative_term(state)


def _observe_owner_until_reaped(
    state: _TransportState, first: Optional[BaseException],
) -> Optional[BaseException]:
    while state.wait_status is None:
        try:
            _drain_child_channels(state)
        except BaseException as exc:
            first = _remember_cleanup_failure(
                first, exc, "cooperative child drain failed",
            )
        try:
            _child_wait_nonblocking(state)
        except BaseException as exc:
            first = _remember_cleanup_failure(
                first, exc, "cooperative child wait failed",
            )
        if state.wait_status is None:
            first = _cleanup_pause(first, "cooperative child pause failed")
    return first


def _drain_reaped_channels_to_eof(
    state: _TransportState, first: Optional[BaseException],
) -> Optional[BaseException]:
    input_error = _close_parent_input(state)
    if input_error is not None:
        first = _remember_cleanup_failure(
            first, input_error, "reaped child input close failed",
        )
    output_names = {"stdout", "stderr", "status", "custody"}
    while output_names & state.open_names:
        try:
            _drain_child_channels(state)
        except BaseException as exc:
            first = _remember_cleanup_failure(
                first, exc, "post-reap channel drain failed",
            )
        if output_names & state.open_names:
            first = _cleanup_pause(first, "post-reap channel pause failed")
    if not state.custody_eof:
        _retain_parent_custody_forever(state)
    if (
        state.custody_unresolved is not None or state.custody_ambiguous
        or len(state.custody) != state.custody_offset
    ):
        _retain_parent_custody_forever(state)
    return first


def _observe_git_absence_after_owner_reap(
    state: _TransportState, first: Optional[BaseException],
) -> Optional[BaseException]:
    if state.pid is None or state.wait_status is None:
        raise LauncherError("post-owner Git observation state differs")
    while state.git_identities:
        progressed = False
        for sequence in sorted(tuple(state.git_identities), reverse=True):
            enrolled = state.git_identities[sequence]
            pid = int(enrolled[0])
            try:
                observed = _darwin_process_identity(pid)
            except BaseException as exc:
                first = _remember_cleanup_failure(
                    first, exc, "post-owner Git identity read failed",
                )
                continue
            if observed is None:
                if _bounded_group_absent(pid):
                    state.git_identities.pop(sequence, None)
                    progressed = True
                continue
            if not _same_enrolled_git(enrolled, observed, state.pid, True):
                state.custody_ambiguous = True
                _retain_parent_custody_forever(state)
        if state.git_identities:
            first = _cleanup_pause(first, "post-owner Git absence pause failed")
        elif progressed:
            break
    return first


def _prove_owner_group_absent_or_retain(
    state: _TransportState, first: Optional[BaseException],
) -> Optional[BaseException]:
    if state.pid is None or state.wait_status is None:
        raise LauncherError("owner group absence state differs")
    while True:
        try:
            if _bounded_group_absent(state.pid):
                return first
        except BaseException as exc:
            first = _remember_cleanup_failure(
                first, exc, "owner group absence probe failed",
            )
        first = _cleanup_pause(first, "owner group absence pause failed")


def _terminate_reap_and_prove_group(state: _TransportState,
                                    force: bool) -> None:
    if state.pid is None:
        return
    first = None  # type: Optional[BaseException]
    try:
        _child_wait_nonblocking(state)
    except BaseException as exc:
        first = _remember_cleanup_failure(
            first, exc, "initial child wait failed",
        )

    if state.wait_status is None and not state.input_attempted:
        try:
            _preentry_stop_kill_and_reap(state)
        except BaseException as exc:
            first = _remember_cleanup_failure(
                first, exc, "pre-entry hard cleanup failed",
            )
            resume_error = _resume_failed_preentry_child(state)
            if resume_error is not None:
                first = _remember_cleanup_failure(
                    first, resume_error, "pre-entry fallback failed",
                )

    if (
        state.wait_status is None and state.input_attempted
        and force and not state.term_attempted
    ):
        try:
            _send_cooperative_term_once(state)
        except BaseException as exc:
            first = _remember_cleanup_failure(
                first, exc, "cooperative TERM failed",
            )

    # After any input attempt there is no STOP/KILL authority.  Likewise, a
    # failed pre-entry proof falls back to closed-stdin observation only.
    first = _observe_owner_until_reaped(state, first)
    first = _drain_reaped_channels_to_eof(state, first)
    first = _observe_git_absence_after_owner_reap(state, first)
    first = _prove_owner_group_absent_or_retain(state, first)
    if first is not None:
        raise first


def _cleanup_terminal_safe(state: _TransportState) -> bool:
    if state.pid is None:
        # No child ownership was ever registered; tracked local acquisitions
        # are closed only after this transport-safety gate returns.
        return True
    return (
        state.wait_status is not None
        and not state.open_names
        and state.custody_eof
        and state.custody_unresolved is None
        and not state.custody_ambiguous
        and len(state.custody) == state.custody_offset
        and not state.git_identities
        and _bounded_group_absent(state.pid)
    )


def _execute_locked_driver(raw: bytes, repository: Path) -> bytes:
    if (
        len(raw) != LOCKED_DRIVER_BYTES
        or hashlib.sha256(raw).hexdigest() != LOCKED_DRIVER_SHA256
    ):
        raise LauncherError("isolated child input driver identity differs")
    repository_text = os.path.abspath(os.fspath(repository))
    driver_path = os.path.join(repository_text, LOCKED_DRIVER_PATH)
    if (
        PurePosixPath(repository_text).as_posix() != repository_text
        or PurePosixPath(driver_path).as_posix() != driver_path
        or os.path.normpath(driver_path) != driver_path
    ):
        raise LauncherError("isolated child driver path is not canonical")
    if (
        len(CHILD_SUCCESS_FRAME) != CHILD_STATUS_MAX
        or hashlib.sha256(CHILD_SUCCESS_FRAME).hexdigest()
        != CHILD_SUCCESS_FRAME_SHA256
    ):
        raise LauncherError("isolated child success frame identity differs")
    raise LauncherError(
        "external launcher child transport is categorically disabled",
    )

    envelope = _SignalEnvelope()
    state = _TransportState()
    state.repository = repository_text
    primary = None  # type: Optional[BaseException]
    setup_complete = False
    try:
        envelope.install_blocked()
        _require_default_sigchld()
        soft_limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        if type(soft_limit) is not int or soft_limit < 4 or soft_limit > 1_048_576:
            raise LauncherError("parent descriptor limit differs")
        for descriptor in range(3):
            os.fstat(descriptor)
        for descriptor in range(soft_limit):
            try:
                inheritable = os.get_inheritable(descriptor)
            except OSError as exc:
                if exc.errno == errno.EBADF:
                    continue
                raise
            if descriptor > 2 and inheritable:
                raise LauncherError("an unrelated inheritable descriptor is open")

        reservation3 = state.duplicate(0, "fd3 reservation")
        reservation4 = state.duplicate(0, "fd4 reservation")
        input_read, input_write = state.pipe("stdin")
        stdout_read, stdout_write = state.pipe("stdout")
        stderr_read, stderr_write = state.pipe("stderr")
        status_read, status_write = state.pipe("status")
        custody_read, custody_write = state.pipe("custody")
        sources = (
            input_read, stdout_write, stderr_write, status_write, custody_write,
        )
        if (
            reservation3 < 3 or reservation4 <= reservation3
            or any(descriptor <= 4 for descriptor in sources)
        ):
            raise LauncherError("private descriptor reservation differs")

        selector = state.create_selector()
        endpoints = {
            input_write: "stdin", stdout_read: "stdout",
            stderr_read: "stderr", status_read: "status",
            custody_read: "custody",
        }
        for descriptor, name in endpoints.items():
            os.set_blocking(descriptor, False)
            event = selectors.EVENT_WRITE if name == "stdin" else selectors.EVENT_READ
            selector.register(descriptor, event, name)
            state.open_names.add(name)

        file_actions = [
            (os.POSIX_SPAWN_DUP2, input_read, 0),
            (os.POSIX_SPAWN_DUP2, stdout_write, 1),
            (os.POSIX_SPAWN_DUP2, stderr_write, 2),
            (os.POSIX_SPAWN_DUP2, status_write, 3),
            (os.POSIX_SPAWN_DUP2, custody_write, 4),
        ]
        for descriptor in sorted(state.fds):
            if descriptor > 4:
                file_actions.append((os.POSIX_SPAWN_CLOSE, descriptor))
        argv = [
            "/usr/bin/python3", "-I", "-S", "-B", "-c",
            CHILD_BOOTSTRAP, driver_path,
        ]
        observed_pid = os.posix_spawn(
            "/usr/bin/python3", argv, dict(CHILD_ENV),
            file_actions=file_actions, setsid=True, setsigmask=(),
            setsigdef=tuple(_controlled_signals()),
        )
        if type(observed_pid) is not int or observed_pid <= 1:
            raise LauncherError("isolated child pid differs")
        state.pid = observed_pid
        child_identity = _stabilize_child_identity(observed_pid)
        if (
            child_identity is None
            or child_identity[0] != child_identity[2]
            or child_identity[0] != child_identity[5]
            or child_identity[1] != os.getpid()
            or child_identity[3] != os.geteuid()
            or child_identity[4] != os.getegid()
        ):
            raise LauncherError("isolated child enrollment identity differs")
        state.child_identity = child_identity
        if not _git_has_no_children(observed_pid):
            raise LauncherError("isolated child has a descendant before input")
        state.preinput_handshake = True
        for descriptor in (
            reservation3, reservation4, input_read, stdout_write,
            stderr_write, status_write, custody_write,
        ):
            state.retire_fd(descriptor)
        setup_complete = True
        envelope.activate()

        deadline = time.monotonic() + CHILD_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise LauncherError("isolated child timed out")
            for key, mask_value in selector.select(min(0.25, remaining)):
                descriptor = int(key.fileobj)
                name = key.data
                if name == "stdin" and mask_value & selectors.EVENT_WRITE:
                    if state.input_offset == 0:
                        if not state.preinput_handshake or not _git_has_no_children(state.pid):
                            raise LauncherError("isolated child pre-input handshake differs")
                        immediate_child = _darwin_process_identity(
                            state.pid, CHILD_ALLOWED_PATHS,
                        )
                        if (
                            immediate_child is None
                            or not _same_enrolled_child(
                                state.child_identity, immediate_child,
                            )
                        ):
                            raise LauncherError("isolated child changed before input")
                    _write_driver_input(state, descriptor, raw)
                elif name != "stdin" and mask_value & selectors.EVENT_READ:
                    _read_child_channel(state, descriptor, name)
            _child_wait_nonblocking(state)
            if envelope.cancellation is not None:
                raise envelope.cancellation
            if state.wait_status is not None and not state.open_names:
                break
            if state.wait_status is not None and "stdin" in state.open_names:
                raise LauncherError("isolated child exited before input completed")
        custody_digest = hashlib.sha256(
            b"DANCERS_GIT_CUSTODY_SEQUENCE_V1\0"
            + b"".join(state.custody_digests),
        ).hexdigest()
        if (
            state.wait_status is None
            or not os.WIFEXITED(state.wait_status)
            or os.WEXITSTATUS(state.wait_status) != 0
            or state.stdout or state.stderr
            or bytes(state.status_frame) != CHILD_SUCCESS_FRAME
            or not state.custody_eof
            or len(state.custody) != GIT_CUSTODY_MAX
            or state.custody_frames != 2 * GIT_CUSTODY_CALLS
            or state.custody_next != GIT_CUSTODY_CALLS
            or state.custody_unresolved is not None
            or state.custody_ambiguous
            or state.custody_failure_terminal
            or state.transport_failure
            or len(state.custody_digests) != GIT_CUSTODY_CALLS
            or custody_digest != GIT_CUSTODY_FINAL_SHA256
        ):
            raise LauncherError("isolated child success transport differs")
        envelope.cleanup_mode = True
    except BaseException as exc:
        if envelope.started:
            envelope.cleanup_mode = True
        primary = exc

    cleanup_error = None  # type: Optional[BaseException]
    if envelope.started and not envelope.restored:
        # Switch the installed handler to record-only before any cleanup
        # syscall.  A second controlled signal cannot escape this loop.
        envelope.cleanup_mode = True
        while not envelope.blocked:
            try:
                envelope.block_for_cleanup()
            except BaseException as exc:
                cleanup_error = _remember_cleanup_failure(
                    cleanup_error, exc, "cleanup signal block failed",
                )
            if not envelope.blocked:
                cleanup_error = _cleanup_pause(
                    cleanup_error, "cleanup signal block pause failed",
                )
    while True:
        try:
            _terminate_reap_and_prove_group(
                state, force=primary is not None or not setup_complete,
            )
        except BaseException as exc:
            cleanup_error = _remember_cleanup_failure(
                cleanup_error, exc, "cooperative child cleanup failed",
            )
        try:
            terminal_safe = _cleanup_terminal_safe(state)
        except BaseException as exc:
            terminal_safe = False
            cleanup_error = _remember_cleanup_failure(
                cleanup_error, exc, "cleanup terminal proof failed",
            )
        if terminal_safe:
            break
        cleanup_error = _cleanup_pause(
            cleanup_error, "cooperative cleanup retry pause failed",
        )
    while (
        state.fds or state.pending_fds or state.selector is not None
        or state.selector_fd >= 0
    ):
        try:
            state.close_all()
        except BaseException as exc:
            cleanup_error = _remember_cleanup_failure(
                cleanup_error, exc, "transport resource cleanup failed",
            )
        if (
            state.fds or state.pending_fds or state.selector is not None
            or state.selector_fd >= 0
        ):
            cleanup_error = _cleanup_pause(
                cleanup_error, "transport resource cleanup retry pause failed",
            )
    if envelope.started and not envelope.restored:
        try:
            _restore_signal_envelope_persistently(envelope)
        except BaseException as exc:
            cleanup_error = _remember_cleanup_failure(
                cleanup_error, exc, "signal envelope cleanup failed",
            )
    if cleanup_error is not None:
        primary = _merge_failure(
            primary, cleanup_error, "isolated child cleanup failed",
        )
    if envelope.cancellation is not None:
        primary = _merge_effect_failure(
            primary, envelope.cancellation,
            "controlled cancellation observed during cleanup",
        )
    if primary is not None:
        raise primary
    return EXPECTED_DRIVER_VERDICT


def _reauthenticate_live_inputs(repository: Path, expected_driver: bytes) -> None:
    """Fresh descriptor walk proving the live lock and driver still match."""

    _prove_single_main_thread()
    session = None  # type: Optional[_DescriptorSession]
    primary = None  # type: Optional[BaseException]
    try:
        session = _DescriptorSession(_controlled_signals())
        repository_fd = session.open_repository(repository)
        lock_raw = session.read_regular(
            repository_fd, INVOCATION_BINDING_PATH, INVOCATION_BINDING_BYTES,
        )
        driver_raw = session.read_regular(
            repository_fd, LOCKED_DRIVER_PATH, LOCKED_DRIVER_BYTES,
        )
        if (
            len(lock_raw) != INVOCATION_BINDING_BYTES
            or hashlib.sha256(lock_raw).hexdigest() != INVOCATION_BINDING_SHA256
        ):
            raise LauncherError("post-child live lock identity differs")
        _strict_invocation_binding(lock_raw)
        if (
            driver_raw != expected_driver
            or len(driver_raw) != LOCKED_DRIVER_BYTES
            or hashlib.sha256(driver_raw).hexdigest() != LOCKED_DRIVER_SHA256
        ):
            raise LauncherError("post-child live driver identity differs")
        session.reauthenticate()
    except BaseException as exc:
        primary = exc
    if session is not None:
        try:
            _close_session_bounded(session)
        except BaseException as exc:
            primary = _merge_failure(
                primary, exc, "post-child descriptor cleanup failed",
            )
    if primary is not None:
        raise primary
    _prove_single_main_thread()


def run_pinned_nonexecuting_checkpoint() -> bytes:
    """Authenticate the committed inputs, then hit the nonspawning guard."""

    _prove_single_main_thread()
    repository = _repository_root()
    session = None  # type: Optional[_DescriptorSession]
    primary = None  # type: Optional[BaseException]
    invocation = None  # type: Optional[Mapping[str, Any]]
    driver = None  # type: Optional[bytes]
    try:
        session = _DescriptorSession(_controlled_signals())
        repository_fd = session.open_repository(repository)
        invocation, driver = _authenticate_launcher_inputs(session, repository_fd)
    except BaseException as exc:
        primary = exc
    if session is not None:
        try:
            _close_session_bounded(session)
        except BaseException as exc:
            primary = _merge_failure(
                primary, exc, "pinned launcher authentication cleanup failed",
            )
    if primary is not None:
        raise primary
    assert invocation is not None and driver is not None
    if invocation["locked_files"][1]["sha256"] != LOCKED_DRIVER_SHA256:
        raise LauncherError("invocation driver correlation differs")
    _prove_single_main_thread()
    observed = _execute_locked_driver(driver, repository)
    if observed != EXPECTED_DRIVER_VERDICT:
        raise LauncherError("isolated locked driver verdict differs")
    _reauthenticate_live_inputs(repository, driver)
    if (
        len(EXTERNAL_VERDICT) != EXTERNAL_VERDICT_BYTES
        or hashlib.sha256(EXTERNAL_VERDICT).hexdigest() != EXTERNAL_VERDICT_SHA256
    ):
        raise LauncherError("external nonexecution verdict identity differs")
    return EXTERNAL_VERDICT


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        sys.stderr.write(ARGUMENT_FAILURE)
        return 2
    try:
        verdict = run_pinned_nonexecuting_checkpoint()
    except BaseException:
        sys.stderr.write(RUNTIME_FAILURE)
        return 1
    if verdict != EXTERNAL_VERDICT:
        sys.stderr.write(RUNTIME_FAILURE)
        return 1
    sys.stderr.write(RUNTIME_FAILURE)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
