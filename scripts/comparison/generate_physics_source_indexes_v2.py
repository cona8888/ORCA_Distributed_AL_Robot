#!/usr/bin/env python3
"""Reconstruct the frozen, nonauthorizing physics source-index artifacts.

This program reads only fixed local Git objects and authenticated committed
records.  It writes one selected canonical artifact to stdout.  It never
checks out or materializes a source root, invokes a build, opens a network
connection, or runs the simulator.
"""

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
import types
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple
import unicodedata


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

CHECKPOINT_COMMIT = "b484595e007cc9b13d4c41624dfb9b8cd8e91c6e"
CHECKPOINT_TREE = "5c090dcf993e2893b2f9e5ffa7ae081bd8a27157"
CHECKPOINT_PARENT = "a138ebfb13fe9ba33d723a5ed4f7f0a931eb3999"
INTEGRATION_COMMIT = CHECKPOINT_PARENT
INTEGRATION_TREE = "f2ba2ff2faef26b917992745110e9306ad773fd2"
OPTIMIZED_COMMIT = "5383edc2cd17e72f88c6629f0c3f25125a3bd5a5"
OPTIMIZED_TREE = "d0f2a23cc6649887d326321ff3b3df721a3b4afc"
REFERENCE_COMMIT = "d1a9cd3bd3bf7ede17ed9f4014b84e64289a09af"
REFERENCE_TREE = "e548979ec8c43716bd32da25793e60d2b3f45e94"

CONTRACT_BYTES = 19_728
CONTRACT_SHA256 = "21fb7fde7dc8b44eba46d4223e9c441f38888b6dc427ff1a7b3993eab92675a6"
CONTRACT_VALIDATOR_BYTES = 46_056
CONTRACT_VALIDATOR_SHA256 = "927cfdb7a845822499c36791fcf0bb85fafb62df5e9cb8a143bc92da828a700a"
SOURCE_RECORD_BYTES = 27_198
SOURCE_RECORD_SHA256 = "1e24fa3b95f182573a0dcfbd2bf7dc0c3520ed525cfeff9240f86603cbfbcec9"
INTEGRATION_RECORD_BYTES = 13_587
INTEGRATION_RECORD_SHA256 = "31b7b9a8b024d41c060df72e2791a0ff640a3ebd0d792649fb2f7551aa315010"

MAX_SMALL_GIT_OUTPUT_BYTES = 65_536
MAX_GIT_OUTPUT_BYTES = 4 << 20
MAX_GIT_STDERR_BYTES = 65_536
MAX_BLOB_BYTES = 4 << 20
MAX_TREE_ENTRIES = 4_096
MAX_PATH_BYTES = 1_024
GIT_TIMEOUT_SECONDS = 30.0
STREAM_CHUNK_BYTES = 65_536

HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")

COMMON_PROJECT_PATHS = (
    "inc/DistanceField.h",
    "inc/ObstacleField.h",
    "inc/PhysicsArtifactLoader.h",
    "inc/Sha256.h",
    "inc/SimulatorControllerPortV1.h",
    "inc/SimulatorPhysicsBridge.h",
    "inc/physics/PhysicsAdapter.h",
    "inc/physics/PlanarPhysics.h",
    "src/PhysicsArtifactLoader.cpp",
    "src/Sha256.cpp",
    "src/SimulatorPhysicsBridge.cpp",
    "src/physics/PhysicsAdapter.cpp",
    "src/physics/PlanarPhysics.cpp",
)

CONTROLLER_SOURCES = (
    "src/main.cpp",
    "src/Globals.cpp",
    "src/ObstacleField.cpp",
    "src/Robot.cpp",
    "src/CustomFactorGraphLayers/Consensus.cpp",
    "src/CustomFactorGraphLayers/ExampleLayer.cpp",
    "src/CustomFactorGraphLayers/Pathplanning.cpp",
    "src/ShapeFormation.cpp",
    "src/Simulator.cpp",
    "src/PathPlanningScenarios.cpp",
    "src/Utils.cpp",
    "src/Graphics.cpp",
    "src/gbp/Variable.cpp",
    "src/gbp/Factor.cpp",
    "src/ComparisonHooksDispatch.cpp",
    "src/PhysicsEvidenceHooksDispatch.cpp",
)

FIDELITY_OBSERVER_SOURCES = (
    "src/ComparisonHooksFidelity.cpp",
    "src/CollisionMetrics.cpp",
    "src/ComparisonRecorder.cpp",
    "src/PhysicsEvidenceHooksFidelity.cpp",
    "src/PhysicsEvidenceRecorder.cpp",
)

ARTIFACT_IDENTITIES = {
    "common-source-v2.json": (100_897, "1270fc7975691abf302627a96e6b027da0b8306790e7090f89219877eed76f19"),
    "optimized-source-v2.json": (14_237, "f27bc056aa6face5fe0c72be3e2adf6a59d46f775214c60b7031a006a9957a33"),
    "reference-source-v2.json": (14_236, "f786fbc410a904f3f71bb014dc018969073b7aa13a1d5472d299172d2e1da910"),
    "source-input-handoff-v2.json": (1_226, "a3f9b676198fba7e471f2a6500cbb94cf2e8f8c804b560b520bbf2c4d0112b4f"),
}

INDEX_NAMES = {
    "common-source": "common-source-v2.json",
    "optimized-source": "optimized-source-v2.json",
    "reference-source": "reference-source-v2.json",
}
HANDOFF_NAME = "source-input-handoff-v2.json"


class SourceIndexGenerationError(ValueError):
    """A trusted input or fixed local Git read differs from the checkpoint."""


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _canonical_root(path: Path) -> Path:
    value = Path(os.path.abspath(os.fspath(path)))
    if os.path.realpath(os.fspath(value)) != os.fspath(value):
        raise SourceIndexGenerationError("repository has a symlink ancestor")
    try:
        info = value.lstat()
    except OSError as error:
        raise SourceIndexGenerationError("repository is unavailable") from error
    if not stat.S_ISDIR(info.st_mode):
        raise SourceIndexGenerationError("repository is not a directory")
    return value


def _read_exact(path: Path, expected_bytes: int, expected_sha256: str, label: str) -> bytes:
    canonical = Path(os.path.abspath(os.fspath(path)))
    if os.path.realpath(os.fspath(canonical)) != os.fspath(canonical):
        raise SourceIndexGenerationError("%s has a symlink ancestor" % label)
    flags = os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(os.fspath(canonical), flags)
    except OSError as error:
        raise SourceIndexGenerationError("cannot open %s" % label) from error
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.geteuid() or before.st_mode & 0o022
                or before.st_size != expected_bytes):
            raise SourceIndexGenerationError("%s owner, mode, type, links, or size differ" % label)
        data = bytearray()
        while len(data) < before.st_size:
            chunk = os.read(descriptor, min(STREAM_CHUNK_BYTES, before.st_size - len(data)))
            if not chunk:
                raise SourceIndexGenerationError("%s ended early" % label)
            data.extend(chunk)
        if os.read(descriptor, 1):
            raise SourceIndexGenerationError("%s grew while read" % label)
        after = os.fstat(descriptor)
        named = os.stat(os.fspath(canonical), follow_symlinks=False)
        identity = lambda item: (
            item.st_dev, item.st_ino, item.st_mode, item.st_nlink, item.st_uid,
            item.st_size, item.st_mtime_ns, item.st_ctime_ns,
        )
        if identity(before) != identity(after) or identity(after) != identity(named):
            raise SourceIndexGenerationError("%s changed while read" % label)
    finally:
        os.close(descriptor)
    raw = bytes(data)
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise SourceIndexGenerationError("%s SHA-256 differs" % label)
    return raw


def _load_contract_validator(repository: Path) -> types.ModuleType:
    path = repository / "scripts/comparison/validate_physics_protected_source_roots_contract_v2.py"
    raw = _read_exact(
        path,
        CONTRACT_VALIDATOR_BYTES,
        CONTRACT_VALIDATOR_SHA256,
        "protected-source contract validator",
    )
    module = types.ModuleType("dancers_source_index_generator_contract_v2")
    module.__file__ = os.fspath(path)
    module.__package__ = ""
    sys.modules[module.__name__] = module
    try:
        exec(compile(raw, os.fspath(path), "exec"), module.__dict__)
    except Exception:
        sys.modules.pop(module.__name__, None)
        raise
    return module


class GitReader:
    """A fixed read-only Git command surface with streaming byte ceilings."""

    def __init__(self, root: Path):
        self.root = _canonical_root(root)
        self.git_identity = self._validate_git_executable()
        self.commands = []  # type: List[Tuple[str, ...]]

    @staticmethod
    def _validate_git_executable() -> Tuple[int, ...]:
        path = Path(GIT_EXECUTABLE)
        try:
            info = path.lstat()
        except OSError as error:
            raise SourceIndexGenerationError("fixed Git executable is unavailable") from error
        if os.path.realpath(os.fspath(path)) != os.fspath(path):
            raise SourceIndexGenerationError("fixed Git executable must not be a symlink")
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
                or not info.st_mode & stat.S_IXUSR or info.st_mode & (stat.S_ISUID | stat.S_ISGID)
                or not os.access(os.fspath(path), os.X_OK)):
            raise SourceIndexGenerationError("fixed Git executable identity is unsafe")
        return (
            info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_mode, info.st_uid, info.st_gid,
        )

    @classmethod
    def _command_cap(cls, arguments: Tuple[str, ...]) -> int:
        if not arguments or any(type(item) is not str or "\0" in item for item in arguments):
            raise SourceIndexGenerationError("Git arguments are malformed")
        small = {
            ("config", "--local", "--name-only", "--null", "--list"),
            ("remote",),
            ("replace", "-l"),
            ("rev-parse", "--git-path", "objects"),
            ("rev-parse", "--git-path", "info/grafts"),
            ("rev-parse", "--is-shallow-repository"),
            ("rev-parse", "--show-object-format"),
            ("rev-list", "--parents", "-n", "1", CHECKPOINT_COMMIT),
            ("rev-list", "--parents", "-n", "1", REFERENCE_COMMIT),
        }
        small.update(
            ("rev-parse", "%s^{tree}" % commit)
            for commit in (
                CHECKPOINT_COMMIT,
                INTEGRATION_COMMIT,
                OPTIMIZED_COMMIT,
                REFERENCE_COMMIT,
            )
        )
        if arguments in small:
            return MAX_SMALL_GIT_OUTPUT_BYTES
        if (len(arguments) == 5
                and arguments[:4] == ("ls-tree", "-rz", "--full-tree", "-r")
                and arguments[4] in {OPTIMIZED_TREE, REFERENCE_TREE}):
            return MAX_GIT_OUTPUT_BYTES
        if (len(arguments) == 3 and arguments[:2] in {
                ("cat-file", "-s"), ("cat-file", "blob")
        } and HEX40.fullmatch(arguments[2])):
            return 64 if arguments[1] == "-s" else MAX_BLOB_BYTES
        raise SourceIndexGenerationError("Git arguments are outside the exact read-only templates")

    @staticmethod
    def _stop(process: subprocess.Popen) -> None:
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

    def _stream(self, arguments: Tuple[str, ...], stdout_cap: int) -> Tuple[int, bytes, bytes]:
        if self._validate_git_executable() != self.git_identity:
            raise SourceIndexGenerationError("fixed Git executable changed")
        command = [GIT_EXECUTABLE, "--no-pager", "-C", os.fspath(self.root), *arguments]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                env=dict(GIT_ENVIRONMENT),
                start_new_session=True,
            )
        except OSError as error:
            raise SourceIndexGenerationError("bounded Git read failed to start") from error
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
        deadline = time.monotonic() + GIT_TIMEOUT_SECONDS
        try:
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SourceIndexGenerationError("Git read timed out")
                for key, _ in selector.select(min(remaining, 0.1)):
                    stream, destination, limit = streams[key.data]
                    chunk = os.read(key.data, min(STREAM_CHUNK_BYTES, limit - len(destination) + 1))
                    if not chunk:
                        selector.unregister(stream)
                        stream.close()
                        continue
                    destination.extend(chunk)
                    if len(destination) > limit:
                        raise SourceIndexGenerationError("Git read exceeded its fixed output bound")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SourceIndexGenerationError("Git read timed out")
            returncode = process.wait(timeout=remaining)
        except (OSError, subprocess.TimeoutExpired) as error:
            self._stop(process)
            raise SourceIndexGenerationError("bounded Git read failed") from error
        except SourceIndexGenerationError:
            self._stop(process)
            raise
        finally:
            selector.close()
            for stream in (process.stdout, process.stderr):
                if stream is not None and not stream.closed:
                    stream.close()
        return returncode, bytes(stdout), bytes(stderr)

    def run(self, arguments: Sequence[str]) -> bytes:
        exact = tuple(arguments)
        cap = self._command_cap(exact)
        self.commands.append(exact)
        returncode, stdout, stderr = self._stream(exact, cap)
        if returncode != 0:
            detail = stderr.decode("utf-8", "replace").strip()
            raise SourceIndexGenerationError("Git read rejected: %s" % detail)
        return stdout


def _path(value: str) -> str:
    if (type(value) is not str or not value or "\\" in value or "\0" in value
            or unicodedata.normalize("NFC", value) != value
            or len(value.encode("utf-8")) > MAX_PATH_BYTES):
        raise SourceIndexGenerationError("Git path is invalid")
    pure = PurePosixPath(value)
    if pure.is_absolute() or pure.as_posix() != value or any(
            part in {"", ".", ".."} for part in pure.parts):
        raise SourceIndexGenerationError("Git path is not canonical relative POSIX")
    return value


def _tree(reader: GitReader, tree: str) -> Dict[str, Dict[str, str]]:
    raw = reader.run(["ls-tree", "-rz", "--full-tree", "-r", tree])
    result = {}  # type: Dict[str, Dict[str, str]]
    folded = set()  # type: Set[str]
    for record in raw.split(b"\0"):
        if not record:
            continue
        if len(result) >= MAX_TREE_ENTRIES:
            raise SourceIndexGenerationError("Git tree exceeds its fixed entry bound")
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, kind, oid = metadata.decode("ascii").split(" ")
            path = _path(raw_path.decode("utf-8", "strict"))
        except (ValueError, UnicodeError) as error:
            raise SourceIndexGenerationError("Git tree record is malformed") from error
        if path in result or path.casefold() in folded or HEX40.fullmatch(oid) is None:
            raise SourceIndexGenerationError("Git tree entry is ambiguous")
        if kind not in {"blob", "commit"} or re.fullmatch(r"[0-7]{6}", mode) is None:
            raise SourceIndexGenerationError("Git tree entry type or mode is invalid")
        result[path] = {"git_mode": mode, "git_type": kind, "git_oid": oid}
        folded.add(path.casefold())
    return result


def _blob_size(reader: GitReader, oid: str, cache: Dict[str, int]) -> int:
    if oid in cache:
        return cache[oid]
    size_raw = reader.run(["cat-file", "-s", oid])
    try:
        size_text = size_raw.decode("ascii", "strict").strip()
        if not size_text or not size_text.isdigit():
            raise ValueError
        size = int(size_text)
    except (UnicodeError, ValueError) as error:
        raise SourceIndexGenerationError("Git blob size is malformed") from error
    if size < 0 or size > MAX_BLOB_BYTES:
        raise SourceIndexGenerationError("Git blob exceeds its fixed byte ceiling")
    cache[oid] = size
    return size


def _read_blob(
    reader: GitReader,
    oid: str,
    size_cache: Dict[str, int],
    cache: Dict[str, bytes],
) -> bytes:
    if oid in cache:
        return cache[oid]
    size = _blob_size(reader, oid, size_cache)
    raw = reader.run(["cat-file", "blob", oid])
    if len(raw) != size:
        raise SourceIndexGenerationError("Git blob byte count differs")
    actual_oid = hashlib.sha1(
        b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw
    ).hexdigest()
    if actual_oid != oid:
        raise SourceIndexGenerationError("Git blob object identity differs")
    cache[oid] = raw
    return raw


def _validate_repository(reader: GitReader) -> None:
    if reader.run(["remote"]).strip() or reader.run(["replace", "-l"]).strip():
        raise SourceIndexGenerationError("repository has remotes or replace objects")
    if reader.run(["rev-parse", "--is-shallow-repository"]).strip() != b"false":
        raise SourceIndexGenerationError("repository is shallow")
    if reader.run(["rev-parse", "--show-object-format"]).strip() != b"sha1":
        raise SourceIndexGenerationError("repository object format differs")
    keys_raw = reader.run(["config", "--local", "--name-only", "--null", "--list"])
    try:
        keys = [item for item in keys_raw.decode("ascii", "strict").split("\0") if item]
    except UnicodeError as error:
        raise SourceIndexGenerationError("Git configuration keys are malformed") from error
    for key in keys:
        lowered = key.casefold()
        if (lowered == "extensions.partialclone"
                or lowered.startswith("remote.")
                and lowered.endswith((".promisor", ".partialclonefilter"))):
            raise SourceIndexGenerationError("repository has partial-clone configuration")

    objects_raw = reader.run(["rev-parse", "--git-path", "objects"])
    try:
        objects_text = objects_raw.decode("utf-8", "strict").strip()
    except UnicodeError as error:
        raise SourceIndexGenerationError("Git object path is malformed") from error
    if not objects_text or "\n" in objects_text or "\0" in objects_text:
        raise SourceIndexGenerationError("Git object path is malformed")
    objects = Path(objects_text)
    if not objects.is_absolute():
        objects = reader.root / objects
    objects = Path(os.path.abspath(os.fspath(objects)))
    if os.path.realpath(os.fspath(objects)) != os.fspath(objects):
        raise SourceIndexGenerationError("Git object directory has a symlink ancestor")
    try:
        info = objects.lstat()
    except OSError as error:
        raise SourceIndexGenerationError("Git object directory is unavailable") from error
    if not stat.S_ISDIR(info.st_mode):
        raise SourceIndexGenerationError("Git object directory is not a directory")
    for name in ("alternates", "http-alternates"):
        try:
            (objects / "info" / name).lstat()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise SourceIndexGenerationError("cannot inspect alternate object metadata") from error
        else:
            raise SourceIndexGenerationError("repository has an alternate object store")
    try:
        pack_entries = list(os.scandir(objects / "pack"))
    except FileNotFoundError:
        pack_entries = []
    except OSError as error:
        raise SourceIndexGenerationError("cannot inspect Git pack directory") from error
    if len(pack_entries) > MAX_TREE_ENTRIES or any(
            entry.name.endswith(".promisor") for entry in pack_entries):
        raise SourceIndexGenerationError("repository pack closure is invalid")

    graft_raw = reader.run(["rev-parse", "--git-path", "info/grafts"])
    try:
        graft_text = graft_raw.decode("utf-8", "strict").strip()
    except UnicodeError as error:
        raise SourceIndexGenerationError("Git graft path is malformed") from error
    graft = Path(graft_text)
    if not graft.is_absolute():
        graft = reader.root / graft
    try:
        graft.lstat()
    except FileNotFoundError:
        pass
    except OSError as error:
        raise SourceIndexGenerationError("cannot inspect Git graft metadata") from error
    else:
        raise SourceIndexGenerationError("repository has graft metadata")

    expected_trees = {
        CHECKPOINT_COMMIT: CHECKPOINT_TREE,
        INTEGRATION_COMMIT: INTEGRATION_TREE,
        OPTIMIZED_COMMIT: OPTIMIZED_TREE,
        REFERENCE_COMMIT: REFERENCE_TREE,
    }
    for commit, expected_tree in expected_trees.items():
        raw = reader.run(["rev-parse", "%s^{tree}" % commit])
        if raw.decode("ascii", "strict").strip() != expected_tree:
            raise SourceIndexGenerationError("fixed commit tree differs")
    checkpoint_line = reader.run(
        ["rev-list", "--parents", "-n", "1", CHECKPOINT_COMMIT]
    ).decode("ascii", "strict").strip()
    if checkpoint_line != "%s %s" % (CHECKPOINT_COMMIT, CHECKPOINT_PARENT):
        raise SourceIndexGenerationError("checkpoint parent differs")
    reference_line = reader.run(
        ["rev-list", "--parents", "-n", "1", REFERENCE_COMMIT]
    ).decode("ascii", "strict").strip()
    if reference_line != "%s %s" % (REFERENCE_COMMIT, OPTIMIZED_COMMIT):
        raise SourceIndexGenerationError("reference endpoint parent differs")


def _load_records(repository: Path, validator: types.ModuleType) -> None:
    contract_path = repository / "docs/simulator/physics_protected_source_roots_contract_v2.json"
    _read_exact(contract_path, CONTRACT_BYTES, CONTRACT_SHA256, "protected-source contract")
    verdict = validator.validate_contract_file(contract_path)
    if not verdict.get("valid"):
        raise SourceIndexGenerationError("protected-source contract is invalid")

    source_raw = _read_exact(
        repository / "docs/simulator/physics_source_lift_record_epoch_1_v1.json",
        SOURCE_RECORD_BYTES,
        SOURCE_RECORD_SHA256,
        "source-lift record",
    )
    integration_raw = _read_exact(
        repository / "docs/simulator/physics_superbuild_integration_record_epoch_1_v1.json",
        INTEGRATION_RECORD_BYTES,
        INTEGRATION_RECORD_SHA256,
        "superbuild integration record",
    )
    source = validator.parse_canonical_json(source_raw)
    integration = validator.parse_canonical_json(integration_raw)
    if tuple(source.get("common_source_projection", {}).get("project_paths", ())) != COMMON_PROJECT_PATHS:
        raise SourceIndexGenerationError("common source selection differs")
    inventory = source.get("method_inventory", {})
    if tuple(inventory.get("controller_aggregate_sources", ())) != CONTROLLER_SOURCES:
        raise SourceIndexGenerationError("controller source selection differs")
    if tuple(inventory.get("fidelity_observer_sources", ())) != FIDELITY_OBSERVER_SOURCES:
        raise SourceIndexGenerationError("observer source selection differs")
    projected = integration.get("projected_source_roots", {})
    if type(projected) is not dict or set(projected) != {
            "common-source", "optimized-source", "reference-source"}:
        raise SourceIndexGenerationError("integration projected-root registry differs")
    for spec in validator.ROOT_SPECS:
        record = projected.get(spec["root_id"], {})
        for key in (
            "entry_count", "implicit_directory_count", "manifest_bytes",
            "manifest_sha256", "path_list_sha256", "payload_bytes",
            "projected_git_tree", "selection",
        ):
            expected = spec[key]
            # The predecessor integration record truthfully names the
            # source-lift record as the origin of the thirteen common paths;
            # the successor contract calls the same byte-exact set frozen by
            # the integration record.  Bind both reviewed phrasings without
            # pretending the prose strings are identical.
            if spec["root_id"] == "common-source" and key == "selection":
                expected = (
                    "the exact thirteen common project paths frozen by the "
                    "source-lift record plus every recursive Git blob below "
                    "thirdparty/, and no other entry"
                )
            if record.get(key) != expected:
                raise SourceIndexGenerationError("integration projection identity differs")


def _selected(tree: Mapping[str, Mapping[str, str]], root_id: str) -> Dict[str, Dict[str, str]]:
    method_paths = set(CONTROLLER_SOURCES + FIDELITY_OBSERVER_SOURCES)
    common_paths = set(COMMON_PROJECT_PATHS)
    result = {}  # type: Dict[str, Dict[str, str]]
    for path, identity in tree.items():
        include = (
            path in common_paths or path.startswith("thirdparty/")
            if root_id == "common-source"
            else path.startswith("inc/") or path in method_paths
        )
        if not include:
            continue
        if identity["git_type"] != "blob" or identity["git_mode"] not in {"100644", "100755"}:
            raise SourceIndexGenerationError("selected source is not a regular Git blob")
        result[path] = dict(identity)
    return result


def _index(
    root_id: str,
    selected: Mapping[str, Mapping[str, str]],
    validator: types.ModuleType,
    reader: GitReader,
    size_cache: Dict[str, int],
    cache: Dict[str, bytes],
) -> Dict[str, Any]:
    spec = next(item for item in validator.ROOT_SPECS if item["root_id"] == root_id)
    entries = []  # type: List[Dict[str, Any]]
    for path in sorted(selected, key=lambda value: value.encode("utf-8")):
        identity = selected[path]
        raw = _read_blob(reader, identity["git_oid"], size_cache, cache)
        entries.append({
            "relative_path": path,
            "git_mode": identity["git_mode"],
            "git_type": identity["git_type"],
            "git_oid": identity["git_oid"],
            "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "materialized_mode": 0o444 if identity["git_mode"] == "100644" else 0o555,
        })
    path_list = b"".join(
        (item["relative_path"] + "\n").encode("utf-8") for item in entries
    )
    compact_manifest = b"".join(
        (
            "%s %s %s %d %s\t%s\n"
            % (
                item["git_mode"],
                item["git_type"],
                item["git_oid"],
                item["bytes"],
                item["sha256"],
                item["relative_path"],
            )
        ).encode("utf-8")
        for item in entries
    )
    if hashlib.sha256(path_list).hexdigest() != spec["path_list_sha256"]:
        raise SourceIndexGenerationError("projection path-list SHA-256 differs")
    if (len(compact_manifest) != spec["manifest_bytes"]
            or hashlib.sha256(compact_manifest).hexdigest() != spec["manifest_sha256"]):
        raise SourceIndexGenerationError("projection compact-manifest identity differs")
    index = {
        "source_index_id": "0" * 64,
        "schema_version": validator.SOURCE_INDEX_SCHEMA,
        "epoch_id": validator.EPOCH_ID,
        "root_id": root_id,
        "role": root_id,
        "endpoint_anchors": json.loads(json.dumps(spec["endpoint_anchors"])),
        "projection": {
            "selection": spec["selection"],
            "path_list_sha256": spec["path_list_sha256"],
            "manifest_bytes": spec["manifest_bytes"],
            "manifest_sha256": spec["manifest_sha256"],
        },
        "entries": entries,
        "entry_count": len(entries),
        "implicit_directory_count": spec["implicit_directory_count"],
        "total_regular_bytes": sum(item["bytes"] for item in entries),
        "projected_git_tree": spec["projected_git_tree"],
        "root_tree_sha256": spec["root_tree_sha256"],
    }
    index["source_index_id"] = validator.derive_source_index_id(index, validate=False)
    return validator.validate_source_index(index)


def generate_indexes(repository: Path) -> Dict[str, Dict[str, Any]]:
    """Return the three exact source-index objects in frozen root order."""

    root = _canonical_root(repository)
    validator = _load_contract_validator(root)
    _load_records(root, validator)
    reader = GitReader(root)
    _validate_repository(reader)
    optimized_tree = _tree(reader, OPTIMIZED_TREE)
    reference_tree = _tree(reader, REFERENCE_TREE)
    common_optimized = _selected(optimized_tree, "common-source")
    common_reference = _selected(reference_tree, "common-source")
    if common_optimized != common_reference:
        raise SourceIndexGenerationError("common projection differs between endpoints")
    selected = {
        "common-source": common_optimized,
        "optimized-source": _selected(optimized_tree, "optimized-source"),
        "reference-source": _selected(reference_tree, "reference-source"),
    }
    if sum(len(items) for items in selected.values()) != 477:
        raise SourceIndexGenerationError("selected source closure count differs")
    size_cache = {}  # type: Dict[str, int]
    projected_total = 0
    for spec in validator.ROOT_SPECS:
        root_id = spec["root_id"]
        items = selected[root_id]
        if len(items) != spec["entry_count"]:
            raise SourceIndexGenerationError("selected source count differs")
        root_total = sum(
            _blob_size(reader, identity["git_oid"], size_cache)
            for identity in items.values()
        )
        if root_total != spec["payload_bytes"]:
            raise SourceIndexGenerationError("selected source byte total differs")
        projected_total += root_total
    if projected_total != 14_595_556 or projected_total > 16 << 20:
        raise SourceIndexGenerationError("aggregate source closure byte total differs")
    if len(size_cache) > 477 or sum(size_cache.values()) > projected_total:
        raise SourceIndexGenerationError("unique blob closure exceeds its fixed bound")
    cache = {}  # type: Dict[str, bytes]
    indexes = {}  # type: Dict[str, Dict[str, Any]]
    for spec in validator.ROOT_SPECS:
        root_id = spec["root_id"]
        indexes[root_id] = _index(
            root_id, selected[root_id], validator, reader, size_cache, cache
        )
    return indexes


def generate_handoff(indexes: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    """Return the acyclic portable handoff for three already-derived indexes."""

    if type(indexes) is not dict or list(indexes) != [
            "common-source", "optimized-source", "reference-source"]:
        raise SourceIndexGenerationError("source index order differs")
    validator = _load_contract_validator(_repository_root())
    ordered = [validator.validate_source_index(dict(indexes[root_id])) for root_id in indexes]
    sources = [{
        "root_id": item["root_id"],
        "source_index_id": item["source_index_id"],
        "root_tree_sha256": item["root_tree_sha256"],
        "projected_git_tree": item["projected_git_tree"],
    } for item in ordered]
    handoff = {
        "source_input_handoff_sha256": "0" * 64,
        "schema_version": validator.HANDOFF_SCHEMA,
        "epoch_id": validator.EPOCH_ID,
        "classification": {
            "build_authorized": False,
            "capture_complete": False,
            "execution_authorized": False,
            "scientific_evidence": False,
        },
        "root_order": [item["root_id"] for item in sources],
        "sources": sources,
    }
    handoff["source_input_handoff_sha256"] = validator.derive_source_input_handoff_sha256(handoff)
    return validator.validate_source_input_handoff(handoff, ordered)


def generate_source_index_artifacts(repository: Path) -> Dict[str, bytes]:
    """Return exact canonical bytes for all four frozen artifacts."""

    root = _canonical_root(repository)
    validator = _load_contract_validator(root)
    indexes = generate_indexes(root)
    handoff = generate_handoff(indexes)
    artifacts = {}  # type: Dict[str, bytes]
    for root_id, index in indexes.items():
        artifacts[INDEX_NAMES[root_id]] = validator.canonical_json_bytes(
            index, validator.SOURCE_INDEX_BYTES_MAX
        )
    artifacts[HANDOFF_NAME] = validator.canonical_json_bytes(
        handoff, validator.SOURCE_INDEX_BYTES_MAX
    )
    if set(artifacts) != set(ARTIFACT_IDENTITIES):
        raise SourceIndexGenerationError("generated artifact inventory differs")
    for name, (expected_bytes, expected_sha256) in ARTIFACT_IDENTITIES.items():
        raw = artifacts[name]
        if len(raw) != expected_bytes or hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise SourceIndexGenerationError("generated artifact %s identity differs" % name)
    return artifacts


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", choices=tuple(sorted(ARTIFACT_IDENTITIES)))
    arguments = parser.parse_args(argv)
    try:
        artifacts = generate_source_index_artifacts(_repository_root())
    except Exception as error:
        print("source-index generation failed: %s" % error, file=sys.stderr)
        return 1
    sys.stdout.buffer.write(artifacts[arguments.artifact])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
