#!/usr/bin/env python3
"""Verify the bounded, non-authorizing physics source-lift feasibility record."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from typing import Any, Sequence
import unicodedata


SCHEMA = "dancers-planar-physics-source-lift-feasibility-v1"
EPOCH = "simulator-demo-v1-physics-stress-test-epoch-1"
DEFAULT_MANIFEST = "docs/simulator/physics_source_lift_epoch_1_v1.json"
EXPECTED_MANIFEST_SHA256 = "52ab7a20d01978107b1122ce70c8fe2a3719f34719e5c61cff5a960581d46bee"
GIT_EXECUTABLE = "/usr/bin/git"
GIT_ENVIRONMENT = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_NO_LAZY_FETCH": "1",
    "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0",
    "GIT_PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0",
    "HOME": "/nonexistent",
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
}

R_BASE = "986f47deec5b079663c7d7f6120bfc20f0467901"
O_BASE = "99364a41f1535a4bb0eddbe5a10030127ea77086"
R_TRACE = "de0f60d1ad4b944c2b3264b8e3ccd348708b8b8a"
O_TRACE = "26c8c6f9b262a2114ac28203419cb03caf8b0d88"
O_PRE = "c515c3c0e7f1d3eecc84f75e12da508a95437805"
SCAFFOLD = "e14f7b4448fb0cb031c88143f6806c1f246e3bf9"

EXPECTED_TREES = {
    R_BASE: "b564979ee493293a4208116ee7c1c05bd69307f4",
    O_BASE: "c48983070e3227ab2591417f3e7eb2ce61cc2cc8",
    R_TRACE: "cb93386e28f3824ea7ffc17605b441b92f13f87d",
    O_TRACE: "b6f713f47ea3aa57c824cf2c481f3647ac7c9408",
    O_PRE: "4758928d20b90a628101d06ce2392c9577400a7f",
    SCAFFOLD: "22b39deda77019874811225308bfee687b32ac4d",
}
R_PRE_TREE = "4fc208b05aaad81d93ef3bb6973ea17eeb853806"
SCAFFOLD_PARENT = "80ca2738505d59cabe7f5b77ec7735046663dc59"

ALLOWED_PATHS = [
    "docs/performance/SCALING_BASELINE.md",
    "docs/performance/SCALING_RESULTS.md",
    "inc/Simulator.h",
    "inc/gbp/FactorGraph.h",
    "inc/gbp/FactorGraphLayer.h",
    "inc/gbp/GBPCore.h",
    "inc/gbp/LieGroups.h",
    "inc/gbp/Variable.h",
    "src/Graphics.cpp",
    "src/Simulator.cpp",
    "src/gbp/Factor.cpp",
    "src/gbp/Variable.cpp",
]
EXPECTED_STATUSES = ["A", "A"] + ["M"] * 10
ALLOWED_LIST_SHA256 = "e9439411a8b16f8cb459f4345091d44d945543eedaf0470ca391386aa44637c8"
TOUCHED_ALLOWED = [
    "inc/Simulator.h",
    "inc/gbp/LieGroups.h",
    "inc/gbp/Variable.h",
    "src/Graphics.cpp",
    "src/Simulator.cpp",
    "src/gbp/Variable.cpp",
]
AUTOMATIC_PATHS = [path for path in ALLOWED_PATHS if path not in {
    "inc/Simulator.h", "inc/gbp/Variable.h", "src/Simulator.cpp"
}]
CONFLICT_PATCHES = {
    "inc/Simulator.h": (3572, "ca7696d475dbc25f064e88d7e75f7733fcb2e60c60385b3b9f3b1c10a4284326"),
    "inc/gbp/Variable.h": (687, "4bd6cad392383efd89a3c85b58e8f3f8af234b4c809817afca8f57dc6b6ddf35"),
    "src/Simulator.cpp": (5718, "c7827b63d69831372f06edb65b47f614d4630f066b35d454b62e18f4e00ed1b1"),
}
HISTORICAL_PATCH = (45756, "7fc6e8271b02898068adbed0999b00956420f8109d4e68de81640107f45d0bf9", "5eebbb6186f443c42d722f4b96078e0d506814dd")
PHYSICS_PATCH = (8804631, "a86c03a91b49d774195c76523b621b26b420be1a8dc18830ed6fda8bec402bbc", "f43895e0893d7df590f9969d3018028bfb043734")
HISTORICAL_PATCH_RECORD = {
    "bytes": HISTORICAL_PATCH[0],
    "format": "git diff --binary --full-index --no-renames --no-ext-diff --no-textconv <optimized> <reference> -- <ordered paths>",
    "from_commit": O_TRACE,
    "patch_id_stable": HISTORICAL_PATCH[2],
    "sha256": HISTORICAL_PATCH[1],
    "to_commit": R_TRACE,
}
SCAFFOLD_PATCH_RECORD = {
    "bytes": PHYSICS_PATCH[0],
    "commit_count": 16,
    "from_commit": O_PRE,
    "path_count": 97,
    "patch_id_stable": PHYSICS_PATCH[2],
    "sha256": PHYSICS_PATCH[1],
    "to_commit": SCAFFOLD,
}
PREPHYSICS_DERIVATION = "replace or delete the twelve optimized prephysics entries with the authenticated reference trace entries, then hash Git trees in memory"
REVERSE_APPLY_ALGORITHM = "exact ordered unified-diff context application with three context lines and no fuzz"

CLASSIFICATION = {
    "build_capture_complete": False,
    "build_provenance_complete": False,
    "final_source_endpoints_frozen": False,
    "future_post_port_superbuild_source_lift_record_required": True,
    "historical_source_lift_feasibility_verified": True,
    "physics_source_lift_frozen": False,
    "promotion_eligible": False,
    "registered_scientific_run": False,
    "scientific_evidence": False,
    "scientific_execution_authorized": False,
}
COMMUTATIVE_RULES = [
    "materialized reference prephysics tree must equal the derived reference_prephysics_tree",
    "optimized and reference physics routes must produce the same declared reference final tree",
    "outside the exact allowed path list, reference and optimized final entries must be byte-and-mode identical",
    "inside the allowed list, nine paths must be mechanically lifted and exactly three paths must use authenticated resolutions",
    "the final reference-to-optimized name-status list must be two additions followed by ten modifications and no other entry",
]
RESOLUTION_REQUIREMENTS = [
    "bind optimized-prephysics, reference-prephysics, scaffold-endpoint, and resolved-reference blobs and modes",
    "bind the failed historical patch bytes and the canonical resolution diff",
    "record a rationale and independent reviewer identities",
    "permit no manually resolved path outside the exact conflict list",
]
REPOSITORY_CONTRACT = {
    "forbidden_git_operations": [
        "archive", "checkout", "commit-tree", "hash-object -w",
        "merge-tree --write-tree", "read-tree", "update-index", "worktree",
    ],
    "git_environment": GIT_ENVIRONMENT,
    "object_format": "sha1",
    "required_git_executable": {
        "executable": True,
        "group_or_world_writable": False,
        "owner_uid": 0,
        "path": GIT_EXECUTABLE,
        "regular_file": True,
    },
    "required_no_alternate_object_store": True,
    "required_no_partial_clone": True,
    "required_no_replace_objects": True,
    "required_non_shallow": True,
    "required_zero_remotes": True,
}

MAX_MANIFEST_BYTES = 262_144
MAX_GIT_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_GIT_STDERR_BYTES = 1 * 1024 * 1024
MAX_SMALL_GIT_OUTPUT_BYTES = 1 * 1024 * 1024
MAX_TREE_ENTRIES = 25_000
MAX_PATH_BYTES = 1_024
MAX_BLOB_BYTES = 32 * 1024 * 1024
GIT_TIMEOUT_SECONDS = 30.0
STREAM_CHUNK_BYTES = 64 * 1024
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class SourceLiftError(ValueError):
    """The feasibility checkpoint or its Git object evidence is invalid."""


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _strict_json(raw: bytes, label: str) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise SourceLiftError(f"{label} has duplicate key {key!r}")
            result[key] = value
        return result
    try:
        return json.loads(raw.decode("utf-8", "strict"), object_pairs_hook=pairs,
                          parse_constant=lambda value: (_ for _ in ()).throw(SourceLiftError(f"non-finite {value}")))
    except (UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        if isinstance(exc, SourceLiftError):
            raise
        raise SourceLiftError(f"{label} is not strict JSON: {exc}") from exc


def _exact_mapping(value: Any, keys: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise SourceLiftError(f"{label} has the wrong exact key set")
    return value


def _read_manifest(path: Path) -> tuple[dict[str, Any], bytes]:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    canonical = Path(os.path.abspath(candidate))
    if os.path.realpath(canonical) != os.fspath(canonical):
        raise SourceLiftError("manifest path must have no symlink ancestors")
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(canonical, flags)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_MANIFEST_BYTES:
            raise SourceLiftError("manifest must be a bounded single-link regular file")
        raw = os.read(fd, MAX_MANIFEST_BYTES + 1)
        after = os.fstat(fd)
        if len(raw) != before.st_size or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise SourceLiftError("manifest changed while read")
    finally:
        os.close(fd)
    value = _strict_json(raw, "source-lift manifest")
    if type(value) is not dict:
        raise SourceLiftError("manifest must be an object")
    return value, raw


class GitReader:
    """A fixed, read-only Git command surface with streaming byte ceilings."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise SourceLiftError("repository root is not a directory")
        self.git = GIT_EXECUTABLE
        self._git_identity = self._validate_git_executable()
        self.commands: list[tuple[str, ...]] = []

    @staticmethod
    def _validate_git_executable() -> tuple[int, ...]:
        path = Path(GIT_EXECUTABLE)
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise SourceLiftError("fixed Git executable is unavailable") from exc
        if os.path.realpath(path) != os.fspath(path):
            raise SourceLiftError("fixed Git executable must not be a symlink")
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
                or metadata.st_mode & 0o022 or not metadata.st_mode & stat.S_IXUSR
                or metadata.st_mode & (stat.S_ISUID | stat.S_ISGID)
                or not os.access(path, os.X_OK)):
            raise SourceLiftError("fixed Git executable owner, mode, or type is unsafe")
        return (
            metadata.st_dev, metadata.st_ino, metadata.st_size,
            metadata.st_mtime_ns, metadata.st_mode, metadata.st_uid,
            metadata.st_gid,
        )

    @staticmethod
    def _diff_commands() -> set[tuple[str, ...]]:
        prefix = ("diff", "--no-ext-diff", "--no-textconv", "--no-renames")
        binary = prefix + ("--binary", "--full-index")
        unified = prefix + ("--unified=3",)
        commands = {
            binary + (O_TRACE, R_TRACE, "--", *ALLOWED_PATHS),
            binary + (O_PRE, SCAFFOLD),
        }
        commands.update(
            unified + (O_TRACE, R_TRACE, "--", path)
            for path in ALLOWED_PATHS
        )
        commands.update(
            binary + (O_TRACE, R_TRACE, "--", path)
            for path in CONFLICT_PATCHES
        )
        return commands

    @classmethod
    def _command_cap(cls, args: tuple[str, ...], input_bytes: bytes | None) -> int:
        if not args or any(type(item) is not str or "\0" in item for item in args):
            raise SourceLiftError("Git arguments are malformed")
        small_commands = {
            ("config", "--local", "--name-only", "--null", "--list"),
            ("merge-base", R_TRACE, O_TRACE),
            ("merge-base", O_PRE, SCAFFOLD),
            ("remote",),
            ("replace", "-l"),
            ("rev-list", "--count", f"{O_PRE}..{SCAFFOLD}"),
            ("rev-list", "--merges", f"{O_PRE}..{SCAFFOLD}"),
            ("rev-list", "--parents", "-n", "1", SCAFFOLD),
            ("rev-parse", "--git-path", "objects"),
            ("rev-parse", "--is-shallow-repository"),
            ("rev-parse", "--show-object-format"),
        }
        small_commands.update(
            ("rev-parse", f"{commit}^{{tree}}") for commit in EXPECTED_TREES
        )
        if args in small_commands:
            if input_bytes is not None:
                raise SourceLiftError("Git input is forbidden for this command")
            return MAX_SMALL_GIT_OUTPUT_BYTES
        if (len(args) == 5 and args[:4] == ("ls-tree", "-rz", "--full-tree", "-r")
                and args[4] in {R_TRACE, O_TRACE, O_PRE, SCAFFOLD}):
            if input_bytes is not None:
                raise SourceLiftError("Git input is forbidden for ls-tree")
            return MAX_GIT_OUTPUT_BYTES
        if (len(args) == 3 and args[:2] in {("cat-file", "-s"), ("cat-file", "blob")}
                and HEX40.fullmatch(args[2])):
            if input_bytes is not None:
                raise SourceLiftError("Git input is forbidden for cat-file")
            return 64 if args[1] == "-s" else MAX_BLOB_BYTES
        if args in cls._diff_commands():
            if input_bytes is not None:
                raise SourceLiftError("Git input is forbidden for diff")
            return MAX_GIT_OUTPUT_BYTES
        if args == ("patch-id", "--stable"):
            if type(input_bytes) is not bytes or not input_bytes or len(input_bytes) > MAX_GIT_OUTPUT_BYTES:
                raise SourceLiftError("patch-id input exceeds its fixed byte ceiling")
            return 1024
        raise SourceLiftError("Git arguments are outside the exact read-only templates")

    @staticmethod
    def _stop_process(process: subprocess.Popen[bytes]) -> None:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                process.kill()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def _stream(self, args: tuple[str, ...], input_bytes: bytes | None,
                stdout_cap: int) -> tuple[int, bytes, bytes]:
        if self._validate_git_executable() != self._git_identity:
            raise SourceLiftError("fixed Git executable identity changed")
        command = [self.git, "--no-pager", "-C", os.fspath(self.root), *args]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                env=dict(GIT_ENVIRONMENT),
                start_new_session=True,
            )
        except OSError as exc:
            raise SourceLiftError(f"bounded Git read failed to start: {exc}") from exc
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        stdout = bytearray()
        stderr = bytearray()
        streams = {
            process.stdout.fileno(): (process.stdout, stdout, stdout_cap),
            process.stderr.fileno(): (process.stderr, stderr, MAX_GIT_STDERR_BYTES),
        }
        for descriptor, (stream, _, _) in streams.items():
            os.set_blocking(descriptor, False)
            selector.register(stream, selectors.EVENT_READ, descriptor)
        input_offset = 0
        if input_bytes is not None:
            assert process.stdin is not None
            os.set_blocking(process.stdin.fileno(), False)
            selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
        deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SourceLiftError("Git read timed out")
                for key, _ in selector.select(min(remaining, 0.1)):
                    if key.data == "stdin":
                        assert input_bytes is not None and process.stdin is not None
                        try:
                            written = os.write(
                                process.stdin.fileno(),
                                input_bytes[input_offset:input_offset + STREAM_CHUNK_BYTES],
                            )
                            input_offset += written
                        except (BrokenPipeError, OSError):
                            input_offset = len(input_bytes)
                        if input_offset == len(input_bytes):
                            selector.unregister(process.stdin)
                            process.stdin.close()
                        continue
                    stream, destination, limit = streams[key.data]
                    chunk = os.read(key.data, min(STREAM_CHUNK_BYTES, limit - len(destination) + 1))
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    destination.extend(chunk)
                    if len(destination) > limit:
                        raise SourceLiftError("Git read exceeded its fixed output bound")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SourceLiftError("Git read timed out")
            returncode = process.wait(timeout=remaining)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self._stop_process(process)
            raise SourceLiftError(f"bounded Git read failed: {exc}") from exc
        except SourceLiftError:
            self._stop_process(process)
            raise
        finally:
            selector.close()
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
        return returncode, bytes(stdout), bytes(stderr)

    def run(self, args: Sequence[str], *, input_bytes: bytes | None = None) -> bytes:
        exact = tuple(args)
        stdout_cap = self._command_cap(exact, input_bytes)
        self.commands.append(exact)
        returncode, stdout, stderr = self._stream(exact, input_bytes, stdout_cap)
        if returncode != 0:
            detail = stderr.decode("utf-8", "replace").strip()
            raise SourceLiftError(f"Git read rejected ({args[0]}): {detail}")
        return stdout


def _validate_repository_storage(reader: GitReader) -> None:
    if reader.run(["remote"]).strip():
        raise SourceLiftError("repository must have zero configured remotes")
    raw_keys = reader.run(["config", "--local", "--name-only", "--null", "--list"])
    try:
        keys = [item for item in raw_keys.decode("ascii", "strict").split("\0") if item]
    except UnicodeError as exc:
        raise SourceLiftError("local Git configuration keys are malformed") from exc
    for key in keys:
        lowered = key.casefold()
        if (lowered == "extensions.partialclone"
                or lowered.startswith("remote.") and lowered.endswith((".promisor", ".partialclonefilter"))):
            raise SourceLiftError("repository has partial-clone configuration")

    raw_path = reader.run(["rev-parse", "--git-path", "objects"])
    try:
        value = raw_path.decode("utf-8", "strict").removesuffix("\n")
    except UnicodeError as exc:
        raise SourceLiftError("Git object-directory path is malformed") from exc
    if not value or "\n" in value or "\0" in value:
        raise SourceLiftError("Git object-directory path is malformed")
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = reader.root / candidate
    canonical = Path(os.path.abspath(candidate))
    if os.path.realpath(canonical) != os.fspath(canonical):
        raise SourceLiftError("Git object directory must have no symlink ancestor")
    try:
        metadata = canonical.lstat()
    except OSError as exc:
        raise SourceLiftError("Git object directory is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise SourceLiftError("Git object directory is not a directory")
    for name in ("alternates", "http-alternates"):
        try:
            (canonical / "info" / name).lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SourceLiftError("cannot authenticate alternate object-store metadata") from exc
        raise SourceLiftError("repository has an alternate object store")
    pack = canonical / "pack"
    try:
        entries = list(os.scandir(pack))
    except FileNotFoundError:
        entries = []
    except OSError as exc:
        raise SourceLiftError("cannot inspect Git pack directory") from exc
    if len(entries) > MAX_TREE_ENTRIES:
        raise SourceLiftError("Git pack directory exceeds its entry bound")
    if any(entry.name.endswith(".promisor") for entry in entries):
        raise SourceLiftError("repository contains a partial-clone promisor pack")


def _path(value: str) -> str:
    if type(value) is not str or not value or "\\" in value or "\x00" in value:
        raise SourceLiftError("invalid Git path")
    if unicodedata.normalize("NFC", value) != value or len(value.encode()) > MAX_PATH_BYTES:
        raise SourceLiftError("noncanonical or oversized Git path")
    pure = PurePosixPath(value)
    if pure.is_absolute() or pure.as_posix() != value or any(part in ("", ".", "..") for part in pure.parts):
        raise SourceLiftError("Git path is not canonical relative POSIX")
    return value


def _tree(reader: GitReader, rev: str) -> dict[str, dict[str, str]]:
    raw = reader.run(["ls-tree", "-rz", "--full-tree", "-r", rev])
    result: dict[str, dict[str, str]] = {}
    folded: set[str] = set()
    for record in raw.split(b"\0"):
        if not record:
            continue
        if len(result) >= MAX_TREE_ENTRIES:
            raise SourceLiftError("Git tree exceeds entry bound")
        try:
            meta, raw_path = record.split(b"\t", 1)
            mode, kind, oid = meta.decode("ascii").split(" ")
            path = _path(raw_path.decode("utf-8", "strict"))
        except (ValueError, UnicodeError) as exc:
            raise SourceLiftError("malformed ls-tree record") from exc
        if path in result or path.casefold() in folded or not HEX40.fullmatch(oid):
            raise SourceLiftError("ambiguous or invalid Git tree entry")
        if kind not in {"blob", "commit"} or not re.fullmatch(r"[0-7]{6}", mode):
            raise SourceLiftError("unsupported Git tree entry")
        result[path] = {"mode": mode, "type": kind, "oid": oid}
        folded.add(path.casefold())
    return result


def _git_tree_oid(entries: dict[str, dict[str, str]]) -> str:
    root: dict[str, Any] = {}
    for path, identity in entries.items():
        node = root
        parts = path.split("/")
        for part in parts[:-1]:
            item = node.setdefault(part, {"children": {}})
            if "children" not in item:
                raise SourceLiftError("file/directory collision in derived tree")
            node = item["children"]
        if parts[-1] in node:
            raise SourceLiftError("duplicate derived tree entry")
        node[parts[-1]] = identity

    def emit(node: dict[str, Any]) -> str:
        values: list[tuple[bytes, bool, bytes, bytes]] = []
        for name, identity in node.items():
            encoded = name.encode("utf-8")
            if "children" in identity:
                oid = emit(identity["children"])
                values.append((encoded, True, b"40000", bytes.fromhex(oid)))
            else:
                values.append((encoded, False, identity["mode"].encode("ascii"), bytes.fromhex(identity["oid"])))
        values.sort(key=lambda item: item[0] + (b"/" if item[1] else b""))
        body = b"".join(mode + b" " + name + b"\0" + oid for name, _, mode, oid in values)
        return hashlib.sha1(b"tree " + str(len(body)).encode("ascii") + b"\0" + body).hexdigest()
    return emit(root)


def _blob(reader: GitReader, identity: dict[str, Any], label: str) -> bytes:
    if set(identity) != {"bytes", "mode", "oid", "sha256", "type"} or identity["type"] != "blob":
        raise SourceLiftError(f"{label} identity shape differs")
    if type(identity["oid"]) is not str or type(identity["sha256"]) is not str:
        raise SourceLiftError(f"{label} digest is invalid")
    if not HEX40.fullmatch(identity["oid"]) or not HEX64.fullmatch(identity["sha256"]):
        raise SourceLiftError(f"{label} digest is invalid")
    raw = _read_blob_oid(reader, identity["oid"], label)
    if type(identity["bytes"]) is not int or identity["bytes"] != len(raw):
        raise SourceLiftError(f"{label} byte count differs")
    if hashlib.sha256(raw).hexdigest() != identity["sha256"]:
        raise SourceLiftError(f"{label} SHA-256 differs")
    oid = hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()
    if oid != identity["oid"]:
        raise SourceLiftError(f"{label} Git blob identity differs")
    return raw


def _read_blob_oid(reader: GitReader, oid: str, label: str) -> bytes:
    size_raw = reader.run(["cat-file", "-s", oid])
    try:
        size = int(size_raw)
    except ValueError as exc:
        raise SourceLiftError(f"{label} blob size is malformed") from exc
    if size < 0 or size > MAX_BLOB_BYTES:
        raise SourceLiftError(f"{label} blob exceeds its byte bound")
    raw = reader.run(["cat-file", "blob", oid])
    if len(raw) != size:
        raise SourceLiftError(f"{label} blob size changed while read")
    return raw


def _diff(reader: GitReader, left: str, right: str, paths: Sequence[str] | None = None,
          *, unified: bool = False) -> bytes:
    args = ["diff", "--no-ext-diff", "--no-textconv", "--no-renames"]
    args += ["--unified=3"] if unified else ["--binary", "--full-index"]
    args += [left, right]
    if paths is not None:
        args += ["--", *paths]
    return reader.run(args)


def _patch_id(reader: GitReader, patch: bytes) -> str:
    raw = reader.run(["patch-id", "--stable"], input_bytes=patch)
    fields = raw.decode("ascii", "strict").split()
    if len(fields) != 2 or not HEX40.fullmatch(fields[0]):
        raise SourceLiftError("Git patch-id output is malformed")
    return fields[0]


def _unified_hunks(patch: bytes) -> list[tuple[list[bytes], list[bytes]]]:
    lines = patch.splitlines(keepends=True)
    result: list[tuple[list[bytes], list[bytes]]] = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith(b"@@ "):
            index += 1
            continue
        index += 1
        old: list[bytes] = []
        new: list[bytes] = []
        prior: bytes | None = None
        while index < len(lines) and not lines[index].startswith((b"@@ ", b"diff --git ")):
            line = lines[index]
            if line.startswith(b"\\ No newline at end of file"):
                if prior in (b" ", b"-") and old:
                    old[-1] = old[-1].removesuffix(b"\n")
                if prior in (b" ", b"+") and new:
                    new[-1] = new[-1].removesuffix(b"\n")
                index += 1
                continue
            prior = line[:1]
            if prior in (b" ", b"-"):
                old.append(line[1:])
            if prior in (b" ", b"+"):
                new.append(line[1:])
            index += 1
        result.append((old, new))
    return result


def _apply_exact(data: bytes, hunks: Sequence[tuple[list[bytes], list[bytes]]]) -> bytes | None:
    lines = data.splitlines(keepends=True)
    cursor = 0
    for old, new in hunks:
        matches = [index for index in range(cursor, len(lines) - len(old) + 1) if lines[index:index + len(old)] == old]
        if len(matches) != 1:
            return None
        index = matches[0]
        lines[index:index + len(old)] = new
        cursor = index + len(new)
    return b"".join(lines)


def _validate_manifest_shape(manifest: dict[str, Any]) -> None:
    expected_top = {"allowed_method_delta", "classification", "commutative_proof_requirements", "epoch_id", "historical_lineage", "prephysics_lift", "repository_contract", "reverse_apply_probe", "scaffold_endpoint_probe", "schema_version"}
    if set(manifest) != expected_top or manifest["schema_version"] != SCHEMA or manifest["epoch_id"] != EPOCH:
        raise SourceLiftError("manifest top-level contract differs")
    if manifest["classification"] != CLASSIFICATION:
        raise SourceLiftError("manifest classification is not the frozen feasibility-only state")
    if manifest["repository_contract"] != REPOSITORY_CONTRACT:
        raise SourceLiftError("repository read-only contract differs")
    allowed = _exact_mapping(
        manifest["allowed_method_delta"],
        {"historical_reverse_patch", "path_list_encoding", "path_list_sha256", "paths", "reference_to_optimized_statuses"},
        "allowed_method_delta",
    )
    if allowed != {
        "historical_reverse_patch": HISTORICAL_PATCH_RECORD,
        "path_list_encoding": "UTF-8 bytewise-sorted paths, one per line, with a terminal newline",
        "path_list_sha256": ALLOWED_LIST_SHA256,
        "paths": ALLOWED_PATHS,
        "reference_to_optimized_statuses": EXPECTED_STATUSES,
    }:
        raise SourceLiftError("allowed method path/status list differs")
    if hashlib.sha256(("\n".join(ALLOWED_PATHS) + "\n").encode()).hexdigest() != ALLOWED_LIST_SHA256:
        raise SourceLiftError("allowed path-list identity differs")
    proof = _exact_mapping(
        manifest["commutative_proof_requirements"],
        {"final_optimized_source_tree", "final_reference_source_tree", "future_record_schema", "outside_allowlist_equal", "required_rules", "resolution_bundle_sha256", "status"},
        "commutative_proof_requirements",
    )
    if proof != {
        "final_optimized_source_tree": None,
        "final_reference_source_tree": None,
        "future_record_schema": "dancers-planar-physics-source-lift-record-v1",
        "outside_allowlist_equal": False,
        "required_rules": COMMUTATIVE_RULES,
        "resolution_bundle_sha256": None,
        "status": "future-post-port-record-required",
    }:
        raise SourceLiftError("future commutative proof is being represented as complete")
    expected_lineage = {
        "optimized_performance_base": {"commit": O_BASE, "tree": EXPECTED_TREES[O_BASE]},
        "optimized_trace_endpoint": {"commit": O_TRACE, "tree": EXPECTED_TREES[O_TRACE]},
        "reference_method_base": {"commit": R_BASE, "tree": EXPECTED_TREES[R_BASE]},
        "reference_trace_endpoint": {"commit": R_TRACE, "tree": EXPECTED_TREES[R_TRACE]},
        "trace_merge_base": {"commit": R_BASE, "tree": EXPECTED_TREES[R_BASE]},
    }
    if manifest["historical_lineage"] != expected_lineage:
        raise SourceLiftError("historical lineage shape or identity differs")
    lift = _exact_mapping(
        manifest["prephysics_lift"],
        {"corner_entries", "optimized", "optimized_trace_to_prephysics_allowed_path_changes", "reference"},
        "prephysics_lift",
    )
    if lift["optimized"] != {"commit": O_PRE, "tree": EXPECTED_TREES[O_PRE]}:
        raise SourceLiftError("optimized prephysics identity differs")
    if lift["reference"] != {
        "commit": None,
        "derivation": PREPHYSICS_DERIVATION,
        "materialized": False,
        "tree": R_PRE_TREE,
    } or lift["optimized_trace_to_prephysics_allowed_path_changes"] != 0:
        raise SourceLiftError("reference prephysics derivation differs")
    corners = lift["corner_entries"]
    if type(corners) is not list or len(corners) != len(ALLOWED_PATHS):
        raise SourceLiftError("prephysics corner list shape differs")
    for index, item in enumerate(corners):
        record = _exact_mapping(item, {"optimized", "path", "reference"}, f"corner_entries[{index}]")
        if record["path"] != ALLOWED_PATHS[index]:
            raise SourceLiftError("prephysics corner path order differs")
        _exact_mapping(record["optimized"], {"bytes", "mode", "oid", "sha256", "type"}, f"corner_entries[{index}].optimized")
        if record["reference"] is not None:
            _exact_mapping(record["reference"], {"bytes", "mode", "oid", "sha256", "type"}, f"corner_entries[{index}].reference")
    expected_conflicts = [
        {
            "path": path,
            "resolution_required": True,
            "resolved_blob": None,
            "reverse_patch_bytes": values[0],
            "reverse_patch_sha256": values[1],
        }
        for path, values in CONFLICT_PATCHES.items()
    ]
    if manifest["reverse_apply_probe"] != {
        "algorithm": REVERSE_APPLY_ALGORITHM,
        "automatic_paths": AUTOMATIC_PATHS,
        "conflicts": expected_conflicts,
        "resolution_requirements": RESOLUTION_REQUIREMENTS,
        "target_commit": SCAFFOLD,
    }:
        raise SourceLiftError("reverse-apply probe shape or identity differs")
    if manifest["scaffold_endpoint_probe"] != {
        "commit": SCAFFOLD,
        "is_final_optimized_source_endpoint": False,
        "parent": SCAFFOLD_PARENT,
        "prephysics_to_scaffold_patch": SCAFFOLD_PATCH_RECORD,
        "touched_allowed_paths": TOUCHED_ALLOWED,
        "tree": EXPECTED_TREES[SCAFFOLD],
    }:
        raise SourceLiftError("scaffold endpoint probe shape or identity differs")


def _verify_repository(repository_root: Path, manifest_path: Path | None = None,
                       expected_manifest_sha256: str | None = None) -> dict[str, Any]:
    root = Path(repository_root).resolve(strict=True)
    path = manifest_path or root / DEFAULT_MANIFEST
    manifest, manifest_raw = _read_manifest(path)
    if expected_manifest_sha256 is not None and expected_manifest_sha256 != EXPECTED_MANIFEST_SHA256:
        raise SourceLiftError("caller-supplied manifest identity is not the frozen identity")
    if hashlib.sha256(manifest_raw).hexdigest() != EXPECTED_MANIFEST_SHA256:
        raise SourceLiftError("manifest SHA-256 differs from the independent expectation")
    _validate_manifest_shape(manifest)
    reader = GitReader(root)
    if reader.run(["rev-parse", "--show-object-format"]).strip() != b"sha1":
        raise SourceLiftError("repository object format differs")
    if reader.run(["rev-parse", "--is-shallow-repository"]).strip() != b"false":
        raise SourceLiftError("repository is shallow")
    if reader.run(["replace", "-l"]).strip():
        raise SourceLiftError("repository has replacement objects")
    _validate_repository_storage(reader)

    lineage = manifest["historical_lineage"]
    named = {
        "reference_method_base": R_BASE, "optimized_performance_base": O_BASE,
        "reference_trace_endpoint": R_TRACE, "optimized_trace_endpoint": O_TRACE,
        "trace_merge_base": R_BASE,
    }
    for key, commit in named.items():
        if lineage[key] != {"commit": commit, "tree": EXPECTED_TREES[commit]}:
            raise SourceLiftError(f"historical lineage record {key} differs")
    if reader.run(["merge-base", R_TRACE, O_TRACE]).decode().strip() != R_BASE:
        raise SourceLiftError("trace merge base differs")
    if reader.run(["merge-base", O_PRE, SCAFFOLD]).decode().strip() != O_PRE:
        raise SourceLiftError("prephysics boundary is not the scaffold ancestor")
    for commit, tree in EXPECTED_TREES.items():
        if reader.run(["rev-parse", f"{commit}^{{tree}}"]).decode().strip() != tree:
            raise SourceLiftError(f"commit tree differs for {commit}")

    r_tree, o_tree, pre_tree, scaffold_tree = (_tree(reader, rev) for rev in (R_TRACE, O_TRACE, O_PRE, SCAFFOLD))
    differences: list[tuple[str, str]] = []
    for path in sorted(set(r_tree) | set(o_tree), key=lambda item: item.encode()):
        left, right = r_tree.get(path), o_tree.get(path)
        if left == right:
            continue
        differences.append((path, "A" if left is None else "D" if right is None else "M"))
    if differences != list(zip(ALLOWED_PATHS, EXPECTED_STATUSES)):
        raise SourceLiftError("historical trace cross-tree difference is not exact")

    lift = manifest["prephysics_lift"]
    if lift["optimized"] != {"commit": O_PRE, "tree": EXPECTED_TREES[O_PRE]} or lift["reference"]["tree"] != R_PRE_TREE or lift["reference"]["commit"] is not None or lift["reference"]["materialized"] is not False or lift["optimized_trace_to_prephysics_allowed_path_changes"] != 0:
        raise SourceLiftError("prephysics lift identity/classification differs")
    corners = lift["corner_entries"]
    if type(corners) is not list or [item.get("path") for item in corners] != ALLOWED_PATHS:
        raise SourceLiftError("prephysics corner list differs")
    derived = dict(pre_tree)
    for item in corners:
        path = item["path"]
        optimized = item["optimized"]
        reference = item["reference"]
        _blob(reader, optimized, f"{path} optimized")
        if {key: optimized[key] for key in ("mode", "type", "oid")} != o_tree.get(path):
            raise SourceLiftError(f"optimized corner entry metadata differs for {path}")
        if pre_tree.get(path) != o_tree.get(path):
            raise SourceLiftError(f"allowed blob changed before physics for {path}")
        if reference is None:
            if path in r_tree:
                raise SourceLiftError(f"reference absence differs for {path}")
            derived.pop(path, None)
        else:
            _blob(reader, reference, f"{path} reference")
            if {key: reference[key] for key in ("mode", "type", "oid")} != r_tree.get(path):
                raise SourceLiftError(f"reference corner entry differs for {path}")
            derived[path] = {key: reference[key] for key in ("mode", "type", "oid")}
    if _git_tree_oid(pre_tree) != EXPECTED_TREES[O_PRE] or _git_tree_oid(derived) != R_PRE_TREE:
        raise SourceLiftError("in-memory prephysics Git tree derivation differs")

    historical = _diff(reader, O_TRACE, R_TRACE, ALLOWED_PATHS)
    h_bytes, h_sha, h_pid = HISTORICAL_PATCH
    h_record = manifest["allowed_method_delta"]["historical_reverse_patch"]
    if len(historical) != h_bytes or hashlib.sha256(historical).hexdigest() != h_sha or _patch_id(reader, historical) != h_pid or h_record["bytes"] != h_bytes or h_record["sha256"] != h_sha or h_record["patch_id_stable"] != h_pid:
        raise SourceLiftError("historical reverse patch identity differs")

    scaffold = manifest["scaffold_endpoint_probe"]
    if scaffold["commit"] != SCAFFOLD or scaffold["tree"] != EXPECTED_TREES[SCAFFOLD] or scaffold["parent"] != SCAFFOLD_PARENT or scaffold["is_final_optimized_source_endpoint"] is not False:
        raise SourceLiftError("scaffold endpoint is represented incorrectly")
    scaffold_line = reader.run(["rev-list", "--parents", "-n", "1", SCAFFOLD]).decode("ascii", "strict").split()
    if scaffold_line != [SCAFFOLD, SCAFFOLD_PARENT]:
        raise SourceLiftError("scaffold commit parent differs")
    physics = _diff(reader, O_PRE, SCAFFOLD)
    p_bytes, p_sha, p_pid = PHYSICS_PATCH
    p_record = scaffold["prephysics_to_scaffold_patch"]
    if len(physics) != p_bytes or hashlib.sha256(physics).hexdigest() != p_sha or _patch_id(reader, physics) != p_pid:
        raise SourceLiftError("prephysics-to-scaffold patch identity differs")
    if (p_record["bytes"], p_record["sha256"], p_record["patch_id_stable"], p_record["commit_count"], p_record["path_count"]) != (p_bytes, p_sha, p_pid, 16, 97):
        raise SourceLiftError("scaffold patch record differs")
    changed = [path for path in sorted(set(pre_tree) | set(scaffold_tree), key=lambda item: item.encode()) if pre_tree.get(path) != scaffold_tree.get(path)]
    if len(changed) != 97 or [path for path in ALLOWED_PATHS if path in changed] != TOUCHED_ALLOWED or scaffold["touched_allowed_paths"] != TOUCHED_ALLOWED:
        raise SourceLiftError("scaffold path/allowlist overlap differs")
    if int(reader.run(["rev-list", "--count", f"{O_PRE}..{SCAFFOLD}"]).strip()) != 16 or reader.run(["rev-list", "--merges", f"{O_PRE}..{SCAFFOLD}"]).strip():
        raise SourceLiftError("scaffold history is not the expected linear 16-commit tail")

    automatic: list[str] = []
    conflicts: list[str] = []
    for path in ALLOWED_PATHS:
        reference = r_tree.get(path)
        target = scaffold_tree.get(path)
        if reference is None:
            (automatic if target == o_tree.get(path) else conflicts).append(path)
            continue
        patch = _diff(reader, O_TRACE, R_TRACE, [path], unified=True)
        data = _read_blob_oid(reader, target["oid"], f"{path} scaffold target")
        result = _apply_exact(data, _unified_hunks(patch))
        (automatic if result is not None else conflicts).append(path)
    probe = manifest["reverse_apply_probe"]
    if (probe["algorithm"] != REVERSE_APPLY_ALGORITHM
            or probe["target_commit"] != SCAFFOLD
            or probe["resolution_requirements"] != RESOLUTION_REQUIREMENTS):
        raise SourceLiftError("reverse-apply algorithm or resolution requirements differ")
    if automatic != AUTOMATIC_PATHS or conflicts != list(CONFLICT_PATCHES) or probe["automatic_paths"] != AUTOMATIC_PATHS:
        raise SourceLiftError("reverse-apply feasibility conflict partition differs")
    records = probe["conflicts"]
    if [item["path"] for item in records] != list(CONFLICT_PATCHES):
        raise SourceLiftError("resolution-required path list differs")
    for item in records:
        expected_bytes, expected_sha = CONFLICT_PATCHES[item["path"]]
        patch = _diff(reader, O_TRACE, R_TRACE, [item["path"]])
        if len(patch) != expected_bytes or hashlib.sha256(patch).hexdigest() != expected_sha or item != {"path": item["path"], "resolution_required": True, "resolved_blob": None, "reverse_patch_bytes": expected_bytes, "reverse_patch_sha256": expected_sha}:
            raise SourceLiftError(f"conflict patch/placeholder differs for {item['path']}")

    return {
        "build_capture_complete": False,
        "final_source_endpoints_frozen": False,
        "future_post_port_superbuild_source_lift_record_required": True,
        "historical_source_lift_feasibility_verified": True,
        "manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
        "promotion_eligible": False,
        "reference_prephysics_tree": R_PRE_TREE,
        "resolution_required_paths": conflicts,
        "scaffold_endpoint_is_final": False,
        "scientific_evidence": False,
        "scientific_execution_authorized": False,
    }


def verify_repository(repository_root: Path, manifest_path: Path | None = None,
                      expected_manifest_sha256: str | None = None) -> dict[str, Any]:
    try:
        return _verify_repository(repository_root, manifest_path, expected_manifest_sha256)
    except SourceLiftError:
        raise
    except (AttributeError, IndexError, KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise SourceLiftError("manifest or Git evidence has an invalid nested shape") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--expected-manifest-sha256", default=EXPECTED_MANIFEST_SHA256)
    args = parser.parse_args(argv)
    try:
        result = verify_repository(args.repository_root, args.manifest, args.expected_manifest_sha256)
    except (OSError, SourceLiftError) as exc:
        print(f"physics-source-lift-v1: reject: {exc}", file=sys.stderr)
        return 1
    print(canonical_json_bytes(result).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
