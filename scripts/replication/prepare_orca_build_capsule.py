#!/usr/bin/env python3
"""Create or check the pre-execution DANCeRS--ORCA build capsule.

The prepare command admits two already-built wrapper artifacts, exercises only
the wrapper's pre-step ``validate`` path and non-confirmatory ``smoke`` path,
and publishes a create-only evidence capsule.  It never invokes the publication
``run`` command.  The check command is read-only and never launches the wrapper.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import platform
import re
import shutil
import shlex
import stat
import struct
import subprocess
import sys
import tempfile
from typing import Any, Callable, Iterable, Mapping, Sequence


REPO = pathlib.Path(__file__).resolve().parents[2]
SCHEMA = "dancers-orca-build-capsule-v1"
CAPSULE_ID = "dancers-orca-v1-build-capsule"
INPUT_MANIFEST_SHA256 = (
    "3fc8a7572ed6c89eecfd8cdc1f0db2c9b552e63f60117e83bacf0ad24cd153f7"
)
SCHEDULE_SHA256 = (
    "244d268f37a976bf7af8f11ce0bb02430bd72c32372a4bb040e2b586181328c4"
)
SEQUENCE_SHA256 = (
    "08e7782cab3e2ec7abfb7ac4304a09adee090d94e71c75381debfe8dc4416559"
)
RVO2_COMMIT = "1c25b27258d191e26c2df83ff6e31abc8efbdaed"
RVO2_TREE = "754114c13a345b1d228871be7e85683e31b3af5d"
EXPECTED_SEQUENCES = (
    2, 4, 6, 10, 16, 17, 18, 23, 25, 31, 32, 35, 37, 39, 46, 47, 49,
    51, 56, 61, 62, 63, 68, 70, 76, 77, 80, 82, 84, 91, 92, 93, 98,
    100, 106, 107, 109, 111, 116, 121, 122, 124, 126, 131, 136, 137,
    139, 141, 146, 151, 152, 155, 157, 159, 166, 167, 170, 172, 174,
    181, 182, 183, 188, 190, 196, 197, 200, 202, 204, 211, 212, 213,
    218, 220, 226, 227, 228, 233, 235, 241, 242, 244, 246, 251, 256,
    257, 260, 262, 264, 271, 272, 274, 276, 281, 286, 287, 289, 291,
    296, 301, 302, 305, 307, 309, 316, 317, 319, 321, 326, 331, 332,
    335, 337, 339, 346, 347, 349, 351, 356, 357, 358, 359, 360, 361,
    362,
)

SOURCE_IDENTITIES = {
    "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_V1.md":
        "04bab9f2da0de290346104e5db3d8881df85a2429d1f9964c4e86943ac7350ff",
    "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_AMENDMENT_1_V1.md":
        "88b7465b61fa565827939a9ffc1b5c9c3c0a681e28af985c91b2b5076c4215b8",
    "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_AMENDMENT_2_V1.md":
        "f5192e4a304028355a449e8d10b2d6a9c5adb863daa72ea0776ee58a1f77d6b6",
    "docs/publication/task4_external_baseline/PREFREEZE_WRAPPER_DIAGNOSTIC_V1.md":
        "7fe1b473717daf1d44ca769486a247e7c4aacfb3f215eae099dfc0a5b8c1f272",
    "inc/Sha256.h":
        "93806917c5df2a467381a82af82ba97fb17d7b8857963e1387d8f22c685ac602",
    "pixi.lock":
        "636ecf1cf31431ffc19f453fef39299c3243718477e321fcea8814225bfe68be",
    "pixi.toml":
        "eeef85f1df2445d02a6e632432e67678ef8600de2b5902209fc9a8003fac74aa",
    "src/Sha256.cpp":
        "d3b2c18e745663c72258f217b03fbf12373accfa2b5a434e56dea43b24016e35",
    "thirdparty/json.hpp":
        "d2eeb25d2e95bffeb08ebb7704cdffd2e8fca7113eba9a0b38d60a5c391ea09a",
    "tools/orca_baseline/CMakeLists.txt":
        "af38d6209edbcef6ac4179528bdac76539b69d2a864c7f159948d54f4dde1777",
    "tools/orca_baseline/rvo2_runner.cpp":
        "75713b15bdf79e4d85a4438ff6a92a2ea82d291be6169d6f070b1e6557883b17",
    "tools/orca_baseline/tests/smoke_crossing.json":
        "9590da85e93292f44799f7648645baf1989086c5941fbd41407c2ff996da0d2a",
    "tools/orca_baseline/tests/smoke_goal_latch.json":
        "61c5e659d3d9cb2369d724aaf1aeb3fef2bf8ebbe03a03f9f144715dbad9dd7e",
}

TOOLCHAIN_IDENTITIES = {
    ".local/pixi/bin/pixi": (
        "01e9421df661b45ede5939423ff7df0097afbbaea55a015b31980629f5f7b8ea",
        "pixi 0.72.2",
    ),
    ".pixi/envs/default/bin/clang++": (
        "1748d6247e63b468f8706ab85790d940e43d8e223785386170c5695c3262774e",
        "clang version 19.1.7",
    ),
    ".pixi/envs/default/bin/cmake": (
        "8a0ce7b083c3496b61e5d1a64bff1377768035debda36506fc458b05cac411f5",
        "cmake version 4.4.0",
    ),
    ".pixi/envs/default/bin/git": (
        "06067a9ff2f3191a5f37c1b8e924f11810603665dfc957877f01328152f08a23",
        "git version 2.55.0",
    ),
    ".pixi/envs/default/bin/ninja": (
        "a255887f34142041de8f9159b8bf57f221d656d79fe6a2177ffdda4a1577c8b5",
        "1.13.2",
    ),
}
AUXILIARY_BUILD_TOOLS = {
    ".pixi/envs/default/bin/ar": (
        "c91a233473e2f5c682aaa8a3f2eeca19b78b195de44a00aa43d1a009432bc27c",
        "arm64-apple-darwin20.0.0-ar",
    ),
    ".pixi/envs/default/bin/llvm-ranlib": (
        "db41f847cb10540b41aac31a68c6a4286efb6aac9c490a5cdfdf060678d15b1e",
        "llvm-ranlib-19",
    ),
    ".pixi/envs/default/bin/ld": (
        "7644a6084894cc3e1f8ab4fe38f453c88757b65bfdd269c238a0ff7f68e35221",
        "arm64-apple-darwin20.0.0-ld",
    ),
}

EXPECTED_COMPILE_UNITS = {
    "Agent.cc", "KdTree.cc", "Line.cc", "Obstacle.cc", "RVOSimulator.cc",
    "Sha256.cpp", "Vector2.cc", "rvo2_runner.cpp",
}
EXPECTED_ARCHIVE_MEMBERS = {
    "Agent.cc.o", "KdTree.cc.o", "Line.cc.o", "Obstacle.cc.o",
    "RVOSimulator.cc.o", "Vector2.cc.o",
}
SOURCE_RECORD = {
    "bound_input_manifest_sha256": INPUT_MANIFEST_SHA256,
    "implementation": "snape/RVO2",
    "rvo2_commit": RVO2_COMMIT,
    "rvo2_tree": RVO2_TREE,
    "wrapper": "dancers-orca-wrapper",
}
NEGATIVE_LABELS = (
    "missing-attempt", "duplicate-attempt", "forbidden-summary",
    "forbidden-trace", "unknown-option", "negative-attempt",
    "signed-attempt", "decimal-attempt", "overflow-attempt",
    "out-of-range", "non-orca", "relative-root", "symlink-root",
    "tampered-manifest", "tampered-sidecar", "tampered-schedule",
    "tampered-scenario", "tampered-scenario-symlink",
)
PAYLOAD_PATHS = (
    "compile_commands.normalized.json",
    "smoke/crossing-run-1.summary.json",
    "smoke/crossing-run-1.trace.ndjson",
    "smoke/crossing-run-2.summary.json",
    "smoke/crossing-run-2.trace.ndjson",
    "smoke/goal-latch.summary.json",
    "smoke/goal-latch.trace.ndjson",
    "validation.ndjson",
)


class CapsuleError(RuntimeError):
    pass


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value, allow_nan=False, ensure_ascii=True, indent=2, sort_keys=True
        )
        + "\n"
    ).encode("utf-8")


def compact_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value, allow_nan=False, ensure_ascii=True,
            separators=(",", ":"), sort_keys=False,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def identity(path: pathlib.Path, label: str | None = None) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise CapsuleError(f"{label or path} is not a regular non-symlink file")
    item = {
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if label is not None:
        item["path"] = label
    return item


def require_real_absolute(path: pathlib.Path, label: str, *, directory: bool) -> pathlib.Path:
    if not path.is_absolute():
        raise CapsuleError(f"{label} must be absolute")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise CapsuleError(f"{label} does not exist: {error}") from error
    if resolved != path:
        raise CapsuleError(f"{label} contains a symlink or non-canonical component")
    if directory and not path.is_dir():
        raise CapsuleError(f"{label} must be a directory")
    if not directory and not path.is_file():
        raise CapsuleError(f"{label} must be a file")
    return path


def require_absent_absolute(path: pathlib.Path, label: str) -> pathlib.Path:
    if not path.is_absolute():
        raise CapsuleError(f"{label} must be absolute")
    parent = path.parent
    require_real_absolute(parent, f"{label} parent", directory=True)
    if path.exists() or path.is_symlink():
        raise CapsuleError(f"{label} already exists")
    return path


def is_relative_to(path: pathlib.Path, parent: pathlib.Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def require_disjoint(named: Mapping[str, pathlib.Path]) -> None:
    items = list(named.items())
    for index, (left_name, left) in enumerate(items):
        for right_name, right in items[index + 1:]:
            if is_relative_to(left, right) or is_relative_to(right, left):
                raise CapsuleError(f"{left_name} and {right_name} overlap")


def snapshot_tree(root: pathlib.Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = pathlib.Path(current)
        for name in sorted(directories):
            path = current_path / name
            if path.is_symlink():
                raise CapsuleError(f"tree contains a symlink directory: {path}")
        for name in sorted(filenames):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.is_file():
                raise CapsuleError(f"tree contains a non-regular file: {relative}")
            status = path.stat()
            result.append({
                "bytes": status.st_size,
                "mode": f"{stat.S_IMODE(status.st_mode):04o}",
                "path": relative,
                "sha256": sha256_file(path),
                "type": "regular",
            })
    return sorted(result, key=lambda item: item["path"])


def directory_inventory(root: pathlib.Path) -> set[str]:
    result: set[str] = set()
    for current, directories, _ in os.walk(root, followlinks=False):
        current_path = pathlib.Path(current)
        for name in directories:
            path = current_path / name
            if path.is_symlink():
                raise CapsuleError(f"tree contains a symlink directory: {path}")
            result.add(path.relative_to(root).as_posix())
    return result


def parent_directories(paths: Iterable[str]) -> set[str]:
    result: set[str] = set()
    for value in paths:
        relative = pathlib.PurePosixPath(value)
        for parent in relative.parents:
            if parent != pathlib.PurePosixPath("."):
                result.add(parent.as_posix())
    return result


def read_json(path: pathlib.Path, label: str) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapsuleError(f"{label} is not valid JSON: {error}") from error


def verify_source_identities() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for relative, expected in sorted(SOURCE_IDENTITIES.items()):
        record = identity(REPO / relative, relative)
        if record["sha256"] != expected:
            raise CapsuleError(f"source identity differs: {relative}")
        records.append(record)
    return records


def run_probe(argv: Sequence[str], *, cwd: pathlib.Path | None = None,
              allowed: Iterable[int] = (0,),
              env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
    if not argv or any(not isinstance(item, str) for item in argv):
        raise CapsuleError("probe argv is invalid")
    completed = subprocess.run(
        list(argv), cwd=cwd, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
        env=None if env is None else dict(env),
    )
    if completed.returncode not in set(allowed):
        raise CapsuleError(
            f"probe failed ({completed.returncode}): {argv!r}: "
            f"{completed.stderr.decode('utf-8', 'replace')}"
        )
    return completed


def safe_git(root: pathlib.Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    git = str(REPO / ".pixi/envs/default/bin/git")
    environment = dict(os.environ)
    environment.update({
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    })
    return run_probe([
        git, "--no-optional-locks",
        "-c", "core.fsmonitor=false",
        "-c", "core.hooksPath=/dev/null",
        "-C", str(root), *args,
    ], env=environment)


def git_text(root: pathlib.Path, *args: str) -> str:
    return safe_git(root, *args).stdout.decode("utf-8").strip()


def verify_repository_anchor() -> dict[str, Any]:
    relative = pathlib.Path(__file__).resolve().relative_to(REPO).as_posix()
    if git_text(REPO, "status", "--porcelain=v1", "--", relative):
        raise CapsuleError("capsule tool has uncommitted changes")
    commit = git_text(REPO, "rev-parse", "HEAD")
    tracked = safe_git(REPO, "show", f"{commit}:{relative}").stdout
    current = pathlib.Path(__file__).read_bytes()
    if tracked != current:
        raise CapsuleError("capsule tool bytes differ from the repository anchor")
    return {
        "commit": commit,
        "tool": {"path": relative, "bytes": len(current),
                 "sha256": sha256_bytes(current)},
    }


def verify_repository_record(record: Mapping[str, Any]) -> None:
    if set(record) != {"commit", "tool"} or not re.fullmatch(
        r"[0-9a-f]{40}", str(record.get("commit", ""))
    ):
        raise CapsuleError("repository anchor record differs")
    tool = record["tool"]
    relative = pathlib.Path(__file__).resolve().relative_to(REPO).as_posix()
    if not isinstance(tool, dict) or tool.get("path") != relative:
        raise CapsuleError("repository tool anchor path differs")
    tracked = safe_git(REPO, "show", f"{record['commit']}:{relative}").stdout
    current = pathlib.Path(__file__).read_bytes()
    expected = {
        "path": relative, "bytes": len(tracked), "sha256": sha256_bytes(tracked)
    }
    if tool != expected or tracked != current:
        raise CapsuleError("repository-anchored capsule tool bytes differ")
    if git_text(REPO, "status", "--porcelain=v1", "--", relative):
        raise CapsuleError("capsule tool currently has uncommitted changes")


def verify_toolchain() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    version_args = {
        "pixi": ["--version"], "clang++": ["--version"],
        "cmake": ["--version"], "git": ["--version"],
        "ninja": ["--version"],
    }
    for relative, (expected_sha, expected_first) in sorted(TOOLCHAIN_IDENTITIES.items()):
        path = REPO / relative
        if path.is_symlink():
            target = os.readlink(path)
            if relative != ".pixi/envs/default/bin/clang++" or target != "clang-19":
                raise CapsuleError(f"toolchain symlink differs: {relative}")
            target_path = path.parent / target
            record = identity(target_path, relative)
            record["symlink_target"] = target
        else:
            record = identity(path, relative)
        if record["sha256"] != expected_sha:
            raise CapsuleError(f"toolchain identity differs: {relative}")
        name = pathlib.Path(relative).name
        completed = run_probe([str(path), *version_args[name]])
        first = completed.stdout.decode("utf-8", "replace").splitlines()[0]
        if first != expected_first:
            raise CapsuleError(f"toolchain version differs: {relative}: {first}")
        record["version"] = first
        result.append(record)
    return result


def verify_auxiliary_build_tools() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for relative, (expected_sha, expected_target) in sorted(AUXILIARY_BUILD_TOOLS.items()):
        path = REPO / relative
        if not path.is_symlink() or os.readlink(path) != expected_target:
            raise CapsuleError(f"auxiliary build-tool symlink differs: {relative}")
        target = path.parent / expected_target
        record = identity(target, relative)
        record["symlink_target"] = expected_target
        if record["sha256"] != expected_sha:
            raise CapsuleError(f"auxiliary build-tool identity differs: {relative}")
        records.append(record)
    return records


def verify_rvo2(root: pathlib.Path) -> dict[str, Any]:
    require_real_absolute(root, "RVO2 source", directory=True)
    commit = git_text(root, "rev-parse", "HEAD")
    tree = git_text(root, "rev-parse", "HEAD^{tree}")
    status_text = git_text(root, "status", "--porcelain=v1", "--untracked-files=all")
    if commit != RVO2_COMMIT or tree != RVO2_TREE or status_text:
        raise CapsuleError("RVO2 commit/tree/cleanliness differs")
    licence = identity(root / "LICENSE", "LICENSE")
    if licence["sha256"] != "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30":
        raise CapsuleError("RVO2 licence identity differs")
    return {
        "commit": commit, "tree": tree, "clean": True,
        "licence": "Apache-2.0", "licence_file": licence,
        "path": str(root),
    }


def verify_input(root: pathlib.Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    require_real_absolute(root, "input root", directory=True)
    snapshot = snapshot_tree(root)
    if len(snapshot) != 50:
        raise CapsuleError(f"input tree must contain exactly 50 files, found {len(snapshot)}")
    manifest_path = root / "manifest.json"
    sidecar_path = root / "manifest.sha256"
    if sha256_file(manifest_path) != INPUT_MANIFEST_SHA256:
        raise CapsuleError("input manifest identity differs")
    if sidecar_path.read_bytes() != (INPUT_MANIFEST_SHA256 + "  manifest.json\n").encode():
        raise CapsuleError("input manifest sidecar differs")
    manifest = read_json(manifest_path, "input manifest")
    if manifest.get("schema_version") != "dancers-orca-input-manifest-v1" or manifest.get("study_id") != "dancers-orca-v1":
        raise CapsuleError("input manifest schema/study differs")
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != 48:
        raise CapsuleError("input manifest payload inventory differs")
    expected_paths = {"manifest.json", "manifest.sha256"}
    for item in files:
        if set(item) != {"bytes", "path", "sha256"}:
            raise CapsuleError("input manifest file identity shape differs")
        relative = item["path"]
        if relative in expected_paths:
            raise CapsuleError("duplicate input manifest path")
        expected_paths.add(relative)
        path = root / pathlib.PurePosixPath(relative)
        actual = identity(path)
        if actual != {"bytes": item["bytes"], "sha256": item["sha256"]}:
            raise CapsuleError(f"input payload differs: {relative}")
    if {item["path"] for item in snapshot} != expected_paths:
        raise CapsuleError("input tree has missing or extra files")
    if directory_inventory(root) != parent_directories(expected_paths):
        raise CapsuleError("input tree has missing or extra directories")
    schedule_identity = manifest.get("schedule")
    if not isinstance(schedule_identity, dict):
        raise CapsuleError("input schedule identity is absent")
    schedule_path = root / schedule_identity.get("path", "")
    if sha256_file(schedule_path) != SCHEDULE_SHA256:
        raise CapsuleError("schedule identity differs")
    schedule = read_json(schedule_path, "schedule")
    attempts = schedule.get("attempts")
    if schedule.get("schema_version") != "dancers-orca-schedule-v1" or schedule.get("study_id") != "dancers-orca-v1" or not isinstance(attempts, list) or len(attempts) != 363:
        raise CapsuleError("schedule schema/study/length differs")
    if [row.get("sequence") for row in attempts] != list(range(363)):
        raise CapsuleError("schedule sequences are not exactly 0..362")
    sequences = tuple(row["sequence"] for row in attempts if row.get("method") == "orca")
    if sequences != EXPECTED_SEQUENCES or len(set(sequences)) != 125:
        raise CapsuleError("ORCA schedule sequence inventory differs")
    if sha256_bytes(compact_json_bytes(list(sequences))) != SEQUENCE_SHA256:
        raise CapsuleError("ORCA sequence digest differs")
    return manifest, attempts, snapshot


def parse_cache(path: pathlib.Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith(("#", "//")) or "=" not in line or ":" not in line.split("=", 1)[0]:
            continue
        left, value = line.split("=", 1)
        key = left.split(":", 1)[0]
        values[key] = value
    return values


def replace_roots(value: str, build_root: pathlib.Path, rvo2: pathlib.Path) -> str:
    replacements = (
        (str(build_root), "${BUILD_ROOT}"),
        (str(rvo2), "${RVO2_SOURCE}"),
        (str(REPO), "${REPO}"),
    )
    result = value
    for source, token in replacements:
        result = result.replace(source, token)
    return result


def normalize_compile_commands(build_root: pathlib.Path, rvo2: pathlib.Path) -> list[dict[str, Any]]:
    database = build_root / "compile_commands.json"
    identity(database)
    value = read_json(database, "compile commands")
    if not isinstance(value, list):
        raise CapsuleError("compile commands must be an array")
    normalized: list[dict[str, Any]] = []
    units: list[str] = []
    actual_sources: set[str] = set()
    expected_sources = {
        str(REPO / "tools/orca_baseline/rvo2_runner.cpp"),
        str(REPO / "src/Sha256.cpp"),
        *(str(rvo2 / "src" / name) for name in (
            "Agent.cc", "KdTree.cc", "Line.cc", "Obstacle.cc",
            "RVOSimulator.cc", "Vector2.cc",
        )),
    }
    compiler = str(REPO / ".pixi/envs/default/bin/clang++")
    generated_include = str(build_root / "rvo2/src")
    source_include = str(rvo2 / "src")
    for item in value:
        if not isinstance(item, dict) or "file" not in item or "directory" not in item:
            raise CapsuleError("compile command entry differs")
        command_keys = {key for key in ("arguments", "command") if key in item}
        if len(command_keys) != 1:
            raise CapsuleError("compile command must have exactly one command representation")
        source = str(item["file"])
        if source not in expected_sources or pathlib.Path(source).resolve(strict=True) != pathlib.Path(source):
            raise CapsuleError(f"compile source path differs: {source}")
        if str(item["directory"]) != str(build_root):
            raise CapsuleError("compile working directory differs")
        if "command" in item:
            tokens = shlex.split(item["command"])
        else:
            tokens = list(item["arguments"])
        if not tokens or tokens[0] != compiler or tokens[-1] != source:
            raise CapsuleError(f"compile argv/compiler/source differs: {source}")
        include_tokens = [token[2:] for token in tokens if token.startswith("-I") and len(token) > 2]
        if generated_include not in include_tokens or source_include not in include_tokens or include_tokens.index(generated_include) > include_tokens.index(source_include):
            raise CapsuleError(f"generated/source RVO include order differs: {source}")
        entry: dict[str, Any] = {}
        for key, raw in item.items():
            if isinstance(raw, str):
                entry[key] = replace_roots(raw, build_root, rvo2)
            elif key == "arguments" and isinstance(raw, list) and all(isinstance(token, str) for token in raw):
                entry[key] = [replace_roots(token, build_root, rvo2) for token in raw]
            else:
                entry[key] = raw
        normalized.append(entry)
        units.append(pathlib.Path(str(item["file"])).name)
        actual_sources.add(source)
        flattened = json.dumps(entry, sort_keys=True).lower()
        if "openmp" in flattened or "-fopenmp" in flattened or "-lomp" in flattened:
            raise CapsuleError("OpenMP appears in a compile command")
    if len(normalized) != 8 or set(units) != EXPECTED_COMPILE_UNITS or len(units) != len(set(units)) or actual_sources != expected_sources:
        raise CapsuleError(f"compile unit inventory differs: {sorted(units)}")
    for name in ("rvo2_runner.cpp", "Sha256.cpp"):
        unit = next(entry for entry in normalized if str(entry["file"]).endswith(name))
        flattened = json.dumps(unit, sort_keys=True)
        for required in (INPUT_MANIFEST_SHA256, RVO2_COMMIT, RVO2_TREE, "DANCERS_SHA256_FORCE_PORTABLE=1"):
            if required not in flattened:
                raise CapsuleError(f"wrapper-target compile definition is absent: {name}: {required}")
    return normalized


def normalized_link_command(build_root: pathlib.Path, rvo2: pathlib.Path) -> str:
    ninja = str(REPO / ".pixi/envs/default/bin/ninja")
    output = run_probe([ninja, "-C", str(build_root), "-t", "commands", "dancers-orca-wrapper"]).stdout.decode("utf-8")
    candidates = [line for line in output.splitlines() if " -o dancers-orca-wrapper " in f" {line} "]
    if len(candidates) != 1:
        raise CapsuleError("could not identify the unique wrapper link command")
    normalized = replace_roots(candidates[0], build_root, rvo2)
    lowered = normalized.lower()
    if "rvo2/src/librvo.a" not in lowered or "openmp" in lowered or "-lomp" in lowered or "-fopenmp" in lowered:
        raise CapsuleError("normalized link edge is not static-RVO/OpenMP-free")
    return normalized


def probe_text(argv: Sequence[str], allowed: Iterable[int] = (0,)) -> str:
    completed = run_probe(argv, allowed=allowed)
    return (completed.stdout + completed.stderr).decode("utf-8", "replace")


def audit_build(root: pathlib.Path, rvo2: pathlib.Path) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    require_real_absolute(root, "build root", directory=True)
    binary = require_real_absolute(root / "dancers-orca-wrapper", "wrapper binary", directory=False)
    cache_path = root / "CMakeCache.txt"
    cache_identity = identity(cache_path)
    cache = parse_cache(cache_path)
    expected_cache = {
        "BUILD_SHARED_LIBS": "OFF", "CMAKE_BUILD_TYPE": "Release",
        "CMAKE_AR": str(REPO / ".pixi/envs/default/bin/ar"),
        "CMAKE_COMMAND": str(REPO / ".pixi/envs/default/bin/cmake"),
        "CMAKE_CXX_COMPILER": str(REPO / ".pixi/envs/default/bin/clang++"),
        "CMAKE_CXX_FLAGS": "", "CMAKE_CXX_FLAGS_RELEASE": "-O3 -DNDEBUG",
        "CMAKE_EXE_LINKER_FLAGS": "", "CMAKE_EXE_LINKER_FLAGS_RELEASE": "",
        "CMAKE_EXPORT_COMPILE_COMMANDS": "ON",
        "CMAKE_GENERATOR": "Ninja",
        "CMAKE_HOME_DIRECTORY": str(REPO / "tools/orca_baseline"),
        "CMAKE_MAKE_PROGRAM": str(REPO / ".pixi/envs/default/bin/ninja"),
        "CMAKE_LINKER": str(REPO / ".pixi/envs/default/bin/ld"),
        "CMAKE_OSX_ARCHITECTURES": "", "CMAKE_OSX_DEPLOYMENT_TARGET": "",
        "CMAKE_OSX_SYSROOT": "",
        "CMAKE_PROJECT_NAME": "dancers_orca_wrapper",
        "CMAKE_RANLIB": str(REPO / ".pixi/envs/default/bin/llvm-ranlib"),
        "ENABLE_OPENMP": "OFF", "ORCA_INPUT_MANIFEST_SHA256": INPUT_MANIFEST_SHA256,
        "ORCA_WRAPPER_ENABLE_TESTS": "OFF", "RVO2_SOURCE_DIR": str(rvo2),
    }
    for key, expected in expected_cache.items():
        if cache.get(key) != expected:
            raise CapsuleError(f"effective CMake cache differs for {key}: {cache.get(key)!r}")
    compile_commands = normalize_compile_commands(root, rvo2)
    link_command = normalized_link_command(root, rvo2)
    build_ninja = root / "build.ninja"
    ninja_raw = build_ninja.read_bytes()
    if b"rvo2/src/libRVO.a" not in ninja_raw or b"ENABLE_OPENMP:BOOL=ON" in ninja_raw:
        raise CapsuleError("Ninja graph does not prove static RVO/OpenMP exclusion")
    archives = list(root.glob("**/libRVO.a"))
    if len(archives) != 1:
        raise CapsuleError("build must contain exactly one libRVO.a")
    archive = archives[0]
    members = probe_text(["/usr/bin/ar", "-t", str(archive)]).splitlines()
    object_members = [member for member in members if member != "__.SYMDEF"]
    if (
        members.count("__.SYMDEF") != 1
        or set(object_members) != EXPECTED_ARCHIVE_MEMBERS
        or len(object_members) != 6
    ):
        raise CapsuleError(f"RVO archive members differ: {members}")
    generated = root / "rvo2/src/Export.h"
    generated_identity = identity(generated, "rvo2/src/Export.h")
    deps = probe_text(["/usr/bin/otool", "-L", str(binary)])
    lowered_deps = deps.lower()
    if "libomp" in lowered_deps or "librvo" in lowered_deps:
        raise CapsuleError("wrapper has an OpenMP or RVO dynamic dependency")
    dependency_lines = [line.strip() for line in deps.splitlines()[1:] if line.strip()]
    if len(dependency_lines) != 2 or not any("libc++.1.dylib" in line for line in dependency_lines) or not any("libSystem.B.dylib" in line for line in dependency_lines):
        raise CapsuleError(f"wrapper dynamic dependency inventory differs: {dependency_lines}")
    nm_text = probe_text(["/usr/bin/nm", str(binary)])
    forbidden = [line for line in nm_text.splitlines() if re.search(r"(?:omp_|GOMP|__kmpc)", line, re.IGNORECASE)]
    if forbidden:
        raise CapsuleError(f"wrapper contains OpenMP symbols: {forbidden[:5]}")
    rvo_symbols = [line for line in nm_text.splitlines() if "RVOSimulator" in line and " U " not in f" {line} "]
    if not any("doStep" in line for line in rvo_symbols) or not any("addAgent" in line for line in rvo_symbols):
        raise CapsuleError("defined RVO simulator symbols are absent")
    raw = binary.read_bytes()
    for required in (INPUT_MANIFEST_SHA256, RVO2_COMMIT, RVO2_TREE):
        if required.encode() not in raw:
            raise CapsuleError(f"binary binding string is absent: {required}")
    if b"smoke-only-unbound" in raw:
        raise CapsuleError("publication binary contains the smoke-only binding")
    file_text = probe_text(["/usr/bin/file", str(binary)]).strip()
    if "Mach-O 64-bit executable arm64" not in file_text:
        raise CapsuleError(f"wrapper architecture differs: {file_text}")
    uuid_text = probe_text(["/usr/bin/dwarfdump", "--uuid", str(binary)]).strip()
    codesign_text = probe_text(["/usr/bin/codesign", "-d", "--verbose=6", str(binary)], allowed=(0, 1))
    if "adhoc" not in codesign_text.lower():
        raise CapsuleError("wrapper is not ad-hoc code signed")
    mode = stat.S_IMODE(binary.stat().st_mode)
    if not mode & stat.S_IXUSR:
        raise CapsuleError("wrapper is not executable")
    record = {
        "archive": {**identity(archive), "members": members,
                    "path": str(archive)},
        "archive_tools": verify_auxiliary_build_tools(),
        "binary": {**identity(binary), "mode": f"{mode:04o}",
                   "path": str(binary)},
        "build_ninja": {**identity(build_ninja), "static_rvo_link_edge": True},
        "cmake_cache": cache_identity,
        "codesign": codesign_text.splitlines(),
        "compile_commands": identity(root / "compile_commands.json"),
        "dynamic_dependencies": dependency_lines,
        "effective_cache": expected_cache,
        "file": file_text,
        "generated_export_h": generated_identity,
        "link_command_normalized": link_command,
        "nm_sha256": sha256_bytes(nm_text.encode()),
        "openmp_forbidden_symbol_matches": [],
        "rvo_defined_symbol_sample": rvo_symbols[:12],
        "uuid": uuid_text,
    }
    return record, compile_commands, link_command


def f32_record(value: float) -> dict[str, Any]:
    raw = struct.pack("=f", float(value))
    converted = struct.unpack("=f", raw)[0]
    bits = struct.unpack("=I", raw)[0]
    return {"hex": f"{bits:08x}", "value": converted}


def point_record(point: Sequence[float]) -> dict[str, Any]:
    records = [f32_record(float(value)) for value in point]
    return {"hex": [item["hex"] for item in records],
            "value": [item["value"] for item in records]}


def expected_input(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "attempt_sequence": row["sequence"], "cadence": row["cadence"],
        "cohort_id": row["cohort_id"], "input_manifest_sha256": INPUT_MANIFEST_SHA256,
        "phase": row["phase"], "repetition": row["repetition"],
        "run_class": row["run_class"], "scenario_sha256": row["scenario_sha256"],
        "scene_id": row["scene_id"], "scene_set": row["scene_set"],
        "schedule_sha256": SCHEDULE_SHA256, "study_id": "dancers-orca-v1",
    }


def expected_requested(row: Mapping[str, Any]) -> dict[str, Any]:
    resolved = row["resolved_parameters"]
    return {
        "common_sample_count": row["common_sample_count"],
        "goal_latch": "permanent after first native boundary at distance <= 1 m",
        "goal_tolerance_m": 1.0, "initial_velocity_mps": [0.0, 0.0],
        "maximum_speed_mps": 2.0,
        "max_neighbors": row["robot_count"] - 1,
        "native_sample_stride": row["native_sample_stride"],
        "native_steps": row["steps"],
        "native_timestep_seconds": row["timestep_seconds"],
        "neighbor_distance_m": 20.0, "obstacle_count": 0,
        "robot_radius_m": 1.0,
        "stopping": "fixed ceiling; no early stop or agent deletion",
        "time_horizon_obstacles_seconds": 1.5, "time_horizon_seconds": 1.5,
    }


def verify_validation_record(record: Any, row: Mapping[str, Any], input_root: pathlib.Path) -> None:
    if not isinstance(record, dict) or set(record) != {"input", "parameter_audit", "schema_version", "source", "status"}:
        raise CapsuleError("validation record shape differs")
    if record["schema_version"] != "dancers-orca-validation-v1" or record["status"] != "accepted_without_simulation" or record["source"] != SOURCE_RECORD or record["input"] != expected_input(row):
        raise CapsuleError(f"validation metadata differs for sequence {row['sequence']}")
    audit = record["parameter_audit"]
    if set(audit) != {"accepted", "requested", "verified_through_public_getters"} or audit["verified_through_public_getters"] is not True or audit["requested"] != expected_requested(row):
        raise CapsuleError(f"validation requested-parameter audit differs for {row['sequence']}")
    accepted = audit["accepted"]
    if accepted.get("num_agents") != row["robot_count"] or accepted.get("num_obstacle_vertices") != 0 or accepted.get("time_step") != f32_record(row["timestep_seconds"]):
        raise CapsuleError(f"validation simulator audit differs for {row['sequence']}")
    scenario = read_json(input_root / row["scenario_relpath"], "validation scenario")
    agents = accepted.get("agents")
    if not isinstance(agents, list) or len(agents) != row["robot_count"]:
        raise CapsuleError(f"validation agent count differs for {row['sequence']}")
    for rid, agent in enumerate(agents):
        expected = {
            "initial_position": point_record(scenario["waypoints"][str(rid)][0]),
            "initial_velocity": point_record([0.0, 0.0]),
            "max_neighbors": row["robot_count"] - 1,
            "maximum_speed": f32_record(2.0),
            "neighbor_distance": f32_record(20.0),
            "radius": f32_record(1.0), "rid": rid,
            "time_horizon": f32_record(1.5),
            "time_horizon_obstacles": f32_record(1.5),
        }
        if agent != expected:
            raise CapsuleError(f"validation agent audit differs at sequence {row['sequence']} rid {rid}")


def empty_process_environment(root: pathlib.Path) -> tuple[dict[str, str], pathlib.Path, list[pathlib.Path]]:
    root.mkdir(exist_ok=True)
    work, home, temporary = (root / name for name in ("work", "home", "tmp"))
    for path in (work, home, temporary):
        path.mkdir()
    environment = {
        "HOME": str(home), "TMPDIR": str(temporary), "LANG": "C",
        "LC_ALL": "C", "PATH": "/usr/bin:/bin", "TZ": "UTC",
    }
    return environment, work, [work, home, temporary]


def assert_empty(paths: Sequence[pathlib.Path], label: str) -> None:
    for path in paths:
        if any(path.iterdir()):
            raise CapsuleError(f"{label} created an unexpected file under {path}")


def invoke_wrapper(binary: pathlib.Path, subcommand: str, arguments: Sequence[str],
                   *, cwd: pathlib.Path, env: Mapping[str, str],
                   expected_returncode: int) -> subprocess.CompletedProcess[bytes]:
    if subcommand not in {"validate", "smoke"}:
        raise CapsuleError(f"wrapper command is forbidden: {subcommand}")
    argv = [str(binary), subcommand, *arguments]
    completed = subprocess.run(
        argv, cwd=cwd, env=dict(env), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
    )
    if completed.returncode != expected_returncode:
        raise CapsuleError(
            f"wrapper {subcommand} returned {completed.returncode}, expected "
            f"{expected_returncode}: {completed.stderr.decode('utf-8', 'replace')}"
        )
    return completed


def one_json_line(raw: bytes, label: str) -> Any:
    if not raw.endswith(b"\n") or raw.count(b"\n") != 1 or raw.startswith(b"\n"):
        raise CapsuleError(f"{label} is not exactly one JSON line plus LF")
    try:
        return json.loads(raw[:-1])
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CapsuleError(f"{label} JSON differs: {error}") from error


def run_validations(binary: pathlib.Path, input_root: pathlib.Path,
                    attempts: Sequence[Mapping[str, Any]], ledger: list[str]) -> bytes:
    by_sequence = {row["sequence"]: row for row in attempts}
    rows: list[bytes] = []
    with tempfile.TemporaryDirectory(prefix="orca-validation-") as temporary:
        root = pathlib.Path(temporary)
        for sequence in EXPECTED_SEQUENCES:
            process_root = root / f"sequence-{sequence:03d}"
            process_root.mkdir()
            env, cwd, empties = empty_process_environment(process_root)
            completed = invoke_wrapper(
                binary, "validate",
                ["--input-root", str(input_root), "--attempt-sequence", str(sequence)],
                cwd=cwd, env=env, expected_returncode=0,
            )
            ledger.append("validate")
            if completed.stderr != b"":
                raise CapsuleError(f"validation emitted stderr for sequence {sequence}")
            record = one_json_line(completed.stdout, f"validation {sequence}")
            verify_validation_record(record, by_sequence[sequence], input_root)
            assert_empty(empties, f"validation {sequence}")
            rows.append(completed.stdout)
    if len(rows) != 125:
        raise CapsuleError("validation output count differs")
    return b"".join(rows)


def negative_call(binary: pathlib.Path, root: pathlib.Path, arguments: Sequence[str],
                  ledger: list[str], label: str) -> dict[str, Any]:
    env, cwd, empties = empty_process_environment(root)
    completed = invoke_wrapper(binary, "validate", arguments, cwd=cwd, env=env, expected_returncode=2)
    ledger.append("validate")
    if completed.stdout != b"" or not completed.stderr.startswith(b"DANCeRS--ORCA wrapper failed: ") or not completed.stderr.endswith(b"\n"):
        raise CapsuleError(f"negative validation output differs: {label}")
    assert_empty(empties, f"negative validation {label}")
    return {"label": label, "returncode": 2,
            "stderr_sha256": sha256_bytes(completed.stderr)}


def run_negative_matrix(binary: pathlib.Path, input_root: pathlib.Path,
                        ledger: list[str]) -> list[dict[str, Any]]:
    valid = str(EXPECTED_SEQUENCES[0])
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="orca-negative-") as temporary:
        root = pathlib.Path(temporary)
        cases = {
            "missing-attempt": ["--input-root", str(input_root)],
            "duplicate-attempt": ["--input-root", str(input_root), "--attempt-sequence", valid, "--attempt-sequence", valid],
            "forbidden-summary": ["--input-root", str(input_root), "--attempt-sequence", valid, "--summary-out", str(root / "forbidden.json")],
            "forbidden-trace": ["--input-root", str(input_root), "--attempt-sequence", valid, "--trace-out", str(root / "forbidden.ndjson")],
            "unknown-option": ["--input-root", str(input_root), "--attempt-sequence", valid, "--unknown", "x"],
            "negative-attempt": ["--input-root", str(input_root), "--attempt-sequence", "-1"],
            "signed-attempt": ["--input-root", str(input_root), "--attempt-sequence", "+2"],
            "decimal-attempt": ["--input-root", str(input_root), "--attempt-sequence", "2.0"],
            "overflow-attempt": ["--input-root", str(input_root), "--attempt-sequence", "184467440737095516160"],
            "out-of-range": ["--input-root", str(input_root), "--attempt-sequence", "363"],
            "non-orca": ["--input-root", str(input_root), "--attempt-sequence", "0"],
            "relative-root": ["--input-root", "comparison/publication/dancers-orca-v1", "--attempt-sequence", valid],
        }
        for index, (label, arguments) in enumerate(cases.items()):
            case_root = root / f"case-{index:02d}"
            case_root.mkdir()
            results.append(negative_call(binary, case_root, arguments, ledger, label))
        alias = root / "input-alias"
        alias.symlink_to(input_root, target_is_directory=True)
        case_root = root / "case-symlink-root"
        case_root.mkdir()
        results.append(negative_call(binary, case_root, ["--input-root", str(alias), "--attempt-sequence", valid], ledger, "symlink-root"))
        mutations = ("manifest", "sidecar", "schedule", "scenario", "scenario-symlink")
        for index, mutation in enumerate(mutations):
            copy = root / f"copy-{mutation}"
            shutil.copytree(input_root, copy)
            if mutation == "manifest":
                (copy / "manifest.json").write_bytes((copy / "manifest.json").read_bytes() + b" ")
            elif mutation == "sidecar":
                (copy / "manifest.sha256").write_bytes((INPUT_MANIFEST_SHA256 + " manifest.json\n").encode())
            elif mutation == "schedule":
                (copy / "schedule.json").write_bytes((copy / "schedule.json").read_bytes() + b" ")
            else:
                scenario = copy / "scenarios/open/mission-31/n-8/scenario.json"
                if mutation == "scenario":
                    scenario.write_bytes(scenario.read_bytes() + b" ")
                else:
                    replacement = copy / "scenario-target.json"
                    replacement.write_bytes(scenario.read_bytes())
                    scenario.unlink()
                    scenario.symlink_to(replacement)
            before = snapshot_tree(copy) if mutation != "scenario-symlink" else None
            case_root = root / f"case-copy-{index}"
            case_root.mkdir()
            results.append(negative_call(binary, case_root, ["--input-root", str(copy), "--attempt-sequence", valid], ledger, f"tampered-{mutation}"))
            if before is not None and snapshot_tree(copy) != before:
                raise CapsuleError(f"negative validation changed tampered copy: {mutation}")
        if (root / "forbidden.json").exists() or (root / "forbidden.ndjson").exists():
            raise CapsuleError("negative validation created a forbidden output")
    return results


def run_smoke_once(binary: pathlib.Path, scenario: pathlib.Path, scene_id: str,
                   output: pathlib.Path, ledger: list[str]) -> tuple[bytes, bytes]:
    output.mkdir()
    env, cwd, empties = empty_process_environment(output / "process")
    summary = output / "summary.json"
    trace = output / "trace.ndjson"
    completed = invoke_wrapper(
        binary, "smoke", [
            "--scenario", str(scenario), "--scenario-sha256", sha256_file(scenario),
            "--scene-id", scene_id, "--cadence", "smoke-main",
            "--observer", "positions", "--summary-out", str(summary),
            "--trace-out", str(trace),
        ], cwd=cwd, env=env, expected_returncode=0,
    )
    ledger.append("smoke")
    if completed.stderr != b"":
        raise CapsuleError(f"smoke emitted stderr: {scene_id}")
    stdout = one_json_line(completed.stdout, f"smoke stdout {scene_id}")
    summary_raw = summary.read_bytes()
    summary_value = read_json(summary, f"smoke summary {scene_id}")
    if stdout != summary_value or summary_value.get("source") != SOURCE_RECORD:
        raise CapsuleError(f"smoke summary/source differs: {scene_id}")
    input_record = summary_value.get("input", {})
    if input_record.get("study_id") != "dancers-orca-nonconfirmatory-smoke-v1" or input_record.get("input_manifest_sha256") is not None or input_record.get("schedule_sha256") is not None or input_record.get("phase") != "smoke" or input_record.get("run_class") != "smoke":
        raise CapsuleError(f"smoke classification differs: {scene_id}")
    trace_raw = trace.read_bytes()
    if summary_value.get("trace_sha256") != sha256_bytes(trace_raw) or summary_value.get("trace_bytes") != len(trace_raw) or summary_value.get("trace_frames_written") != 21 or summary_value.get("completed_native_steps") != 20:
        raise CapsuleError(f"smoke trace identity/ceiling differs: {scene_id}")
    assert_empty(empties, f"smoke {scene_id}")
    return summary_raw, trace_raw


def verify_goal_latch(summary_raw: bytes, trace_raw: bytes) -> None:
    summary = json.loads(summary_raw)
    input_record = summary.get("input", {})
    if (
        summary.get("source") != SOURCE_RECORD
        or summary.get("termination") != "fixed_ceiling"
        or summary.get("completed_native_steps") != 20
        or summary.get("trace_sha256") != sha256_bytes(trace_raw)
        or summary.get("trace_bytes") != len(trace_raw)
        or summary.get("trace_frames_written") != 21
        or input_record.get("study_id") != "dancers-orca-nonconfirmatory-smoke-v1"
        or input_record.get("phase") != "smoke"
        or input_record.get("run_class") != "smoke"
        or input_record.get("input_manifest_sha256") is not None
        or input_record.get("schedule_sha256") is not None
    ):
        raise CapsuleError("goal-latch smoke did not retain the fixed ceiling")
    rows = [json.loads(line) for line in trace_raw.splitlines()]
    frames = [row for row in rows if row.get("type") == "frame"]
    if (
        len(rows) != 22
        or rows[0].get("type") != "header"
        or len(frames) != 21
        or [frame.get("native_step") for frame in frames] != list(range(21))
    ):
        raise CapsuleError("goal-latch smoke frame count differs")
    robots = [frame["robots"][0] for frame in frames]
    if not all(robot["goal_latched"] is True and robot["first_goal_native_step"] == 0 and robot["preferred_velocity"]["hex"] == ["00000000", "00000000"] for robot in robots):
        raise CapsuleError("goal latch is not sticky from native step zero")
    if max(robot["goal_distance_m"] for robot in robots) <= 1.0:
        raise CapsuleError("goal-latch smoke lacks displacement beyond tolerance")


def run_smokes(binary: pathlib.Path, ledger: list[str]) -> dict[str, bytes]:
    crossing = REPO / "tools/orca_baseline/tests/smoke_crossing.json"
    latch = REPO / "tools/orca_baseline/tests/smoke_goal_latch.json"
    payloads: dict[str, bytes] = {}
    with tempfile.TemporaryDirectory(prefix="orca-smoke-") as temporary:
        root = pathlib.Path(temporary)
        first_summary, first_trace = run_smoke_once(binary, crossing, "smoke-crossing", root / "crossing-1", ledger)
        second_summary, second_trace = run_smoke_once(binary, crossing, "smoke-crossing", root / "crossing-2", ledger)
        if first_summary != second_summary or first_trace != second_trace:
            raise CapsuleError("crossing smoke outputs are not byte deterministic")
        latch_summary, latch_trace = run_smoke_once(binary, latch, "smoke-goal-latch", root / "goal-latch", ledger)
        verify_goal_latch(latch_summary, latch_trace)
        payloads = {
            "smoke/crossing-run-1.summary.json": first_summary,
            "smoke/crossing-run-1.trace.ndjson": first_trace,
            "smoke/crossing-run-2.summary.json": second_summary,
            "smoke/crossing-run-2.trace.ndjson": second_trace,
            "smoke/goal-latch.summary.json": latch_summary,
            "smoke/goal-latch.trace.ndjson": latch_trace,
        }
    return payloads


def payload_identity(relative: str, raw: bytes) -> dict[str, Any]:
    return {"bytes": len(raw), "path": relative, "sha256": sha256_bytes(raw)}


def checked_payload_path(root: pathlib.Path, relative: str) -> pathlib.Path:
    parsed = pathlib.PurePosixPath(relative)
    if (
        not relative
        or parsed.is_absolute()
        or parsed.as_posix() != relative
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise CapsuleError(f"capsule payload path is unsafe: {relative!r}")
    return root.joinpath(*parsed.parts)


def write_exclusive(path: pathlib.Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)


def publish_capsule(
    output: pathlib.Path,
    payloads: Mapping[str, bytes],
    manifest: dict[str, Any],
    *,
    validator: Callable[[pathlib.Path, str], object],
) -> str:
    require_absent_absolute(output, "output root")
    if len(payloads) != len(set(payloads)):
        raise CapsuleError("capsule payload paths are duplicated")
    stage = pathlib.Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent))
    try:
        for relative, raw in sorted(payloads.items()):
            write_exclusive(checked_payload_path(stage, relative), raw)
        manifest_raw = canonical_json_bytes(manifest)
        digest = sha256_bytes(manifest_raw)
        write_exclusive(stage / "manifest.json", manifest_raw)
        write_exclusive(stage / "manifest.sha256", (digest + "  manifest.json\n").encode())
        validator(stage, digest)
        output.mkdir(mode=0o755, exist_ok=False)
        moved: list[pathlib.Path] = []
        try:
            children = sorted(
                (child for child in stage.iterdir() if child.name != "manifest.sha256"),
                key=lambda child: child.name,
            )
            children.append(stage / "manifest.sha256")
            for child in children:
                destination = output / child.name
                child.rename(destination)
                moved.append(destination)
            stage.rmdir()
        except Exception:
            for destination in reversed(moved):
                if destination.is_dir() and not destination.is_symlink():
                    shutil.rmtree(destination)
                elif destination.exists() or destination.is_symlink():
                    destination.unlink()
            output.rmdir()
            raise
        return digest
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def check_payload_inventory(root: pathlib.Path, manifest: Mapping[str, Any]) -> None:
    files = manifest.get("files")
    if not isinstance(files, list) or files != sorted(files, key=lambda item: item.get("path", "")):
        raise CapsuleError("capsule payload inventory is absent or unsorted")
    paths: set[str] = {"manifest.json", "manifest.sha256"}
    if tuple(item.get("path") for item in files) != PAYLOAD_PATHS:
        raise CapsuleError("capsule payload path inventory differs")
    for item in files:
        if set(item) != {"bytes", "path", "sha256"} or item["path"] in paths:
            raise CapsuleError("capsule payload identity differs or is duplicated")
        paths.add(item["path"])
        actual = identity(checked_payload_path(root, item["path"]))
        if actual != {"bytes": item["bytes"], "sha256": item["sha256"]}:
            raise CapsuleError(f"capsule payload differs: {item['path']}")
    if {item["path"] for item in snapshot_tree(root)} != paths:
        raise CapsuleError("capsule tree has missing or extra files")
    if directory_inventory(root) != parent_directories(paths):
        raise CapsuleError("capsule tree has missing or extra directories")


def check_validation_payload(raw: bytes, attempts: Sequence[Mapping[str, Any]], input_root: pathlib.Path) -> None:
    lines = raw.splitlines(keepends=True)
    if len(lines) != 125 or not all(line.endswith(b"\n") and line.count(b"\n") == 1 for line in lines):
        raise CapsuleError("stored validation NDJSON framing differs")
    by_sequence = {row["sequence"]: row for row in attempts}
    seen: list[int] = []
    for line in lines:
        record = one_json_line(line, "stored validation")
        sequence = record.get("input", {}).get("attempt_sequence")
        if sequence not in by_sequence:
            raise CapsuleError("stored validation sequence is unknown")
        verify_validation_record(record, by_sequence[sequence], input_root)
        seen.append(sequence)
    if tuple(seen) != EXPECTED_SEQUENCES:
        raise CapsuleError("stored validation sequence order/set differs")


def check_smoke_payloads(root: pathlib.Path) -> None:
    summaries = [root / f"smoke/crossing-run-{index}.summary.json" for index in (1, 2)]
    traces = [root / f"smoke/crossing-run-{index}.trace.ndjson" for index in (1, 2)]
    if summaries[0].read_bytes() != summaries[1].read_bytes() or traces[0].read_bytes() != traces[1].read_bytes():
        raise CapsuleError("stored crossing smoke outputs differ")
    for summary, trace in zip(summaries, traces):
        value = read_json(summary, "stored crossing summary")
        raw = trace.read_bytes()
        input_record = value.get("input", {})
        rows = [json.loads(line) for line in raw.splitlines()]
        frames = [row for row in rows if row.get("type") == "frame"]
        if (
            value.get("source") != SOURCE_RECORD
            or value.get("trace_sha256") != sha256_bytes(raw)
            or value.get("trace_bytes") != len(raw)
            or value.get("trace_frames_written") != 21
            or value.get("completed_native_steps") != 20
            or value.get("termination") != "fixed_ceiling"
            or input_record.get("study_id") != "dancers-orca-nonconfirmatory-smoke-v1"
            or input_record.get("phase") != "smoke"
            or input_record.get("run_class") != "smoke"
            or input_record.get("input_manifest_sha256") is not None
            or input_record.get("schedule_sha256") is not None
            or len(rows) != 22
            or rows[0].get("type") != "header"
            or len(frames) != 21
            or [frame.get("native_step") for frame in frames] != list(range(21))
        ):
            raise CapsuleError("stored crossing smoke semantics differ")
    latch_summary = (root / "smoke/goal-latch.summary.json").read_bytes()
    latch_trace = (root / "smoke/goal-latch.trace.ndjson").read_bytes()
    verify_goal_latch(latch_summary, latch_trace)


def check_capsule(root: pathlib.Path, expected_digest: str) -> dict[str, Any]:
    require_real_absolute(root, "capsule root", directory=True)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise CapsuleError("expected capsule manifest SHA-256 is invalid")
    manifest_path = root / "manifest.json"
    raw = manifest_path.read_bytes()
    if sha256_bytes(raw) != expected_digest:
        raise CapsuleError("capsule manifest differs from the external digest")
    if (root / "manifest.sha256").read_bytes() != (expected_digest + "  manifest.json\n").encode():
        raise CapsuleError("capsule manifest sidecar differs")
    manifest = read_json(manifest_path, "capsule manifest")
    if raw != canonical_json_bytes(manifest):
        raise CapsuleError("capsule manifest is not canonical JSON")
    if manifest.get("schema_version") != SCHEMA or manifest.get("capsule_id") != CAPSULE_ID:
        raise CapsuleError("capsule schema/id differs")
    expected_top_level = {
        "authentication", "builds", "byte_identical_final_executables",
        "capsule_id", "classification", "files", "input",
        "negative_validation_matrix", "normalized_link_command",
        "orca_sequences", "platform", "repository", "reproduction_recipe",
        "rvo2", "schema_version", "source_files", "toolchain",
        "validation", "wrapper_command_ledger",
    }
    if set(manifest) != expected_top_level:
        raise CapsuleError("capsule top-level field inventory differs")
    if manifest.get("authentication") != {
        "rule": "check requires an externally supplied exact manifest SHA-256; the sidecar is not a trust root"
    }:
        raise CapsuleError("capsule authentication rule differs")
    classification = manifest.get("classification", {})
    if classification != {
        "accepted_validation_invocations": 125,
        "execution_authorized": False,
        "negative_validation_invocations": len(NEGATIVE_LABELS),
        "publication_run_invocations": 0,
        "publication_simulation_steps": 0,
        "smoke_invocations": 3,
        "smoke_scope": "non-confirmatory wrapper fixtures only",
        "total_validation_invocations": 125 + len(NEGATIVE_LABELS),
        "validation_scope": "input-derived initialization and public-getter audit before any doStep",
    }:
        raise CapsuleError("capsule execution classification differs")
    check_payload_inventory(root, manifest)
    source_records = verify_source_identities()
    toolchain = verify_toolchain()
    if manifest.get("source_files") != source_records or manifest.get("toolchain") != toolchain:
        raise CapsuleError("capsule source/toolchain identities differ")
    verify_repository_record(manifest.get("repository", {}))
    input_root = pathlib.Path(manifest["input"]["path"])
    _, attempts, input_snapshot = verify_input(input_root)
    if (
        manifest["input"].get("file_count") != 50
        or manifest["input"].get("manifest_sha256") != INPUT_MANIFEST_SHA256
        or manifest["input"].get("schedule_sha256") != SCHEDULE_SHA256
        or input_snapshot != manifest["input"]["snapshot"]
    ):
        raise CapsuleError("current frozen input differs from the capsule snapshot")
    rvo2 = pathlib.Path(manifest["rvo2"]["path"])
    if verify_rvo2(rvo2) != manifest["rvo2"]:
        raise CapsuleError("current RVO2 identity differs from the capsule")
    builds = manifest["builds"]
    if not isinstance(builds, list) or len(builds) != 2:
        raise CapsuleError("capsule build inventory differs")
    build_a, compile_a, link_a = audit_build(pathlib.Path(builds[0]["root"]), rvo2)
    build_b, compile_b, link_b = audit_build(pathlib.Path(builds[1]["root"]), rvo2)
    if build_a != builds[0]["audit"] or build_b != builds[1]["audit"]:
        raise CapsuleError("current build audit differs from the capsule")
    normalized_raw = canonical_json_bytes(compile_a)
    if (
        compile_a != compile_b
        or link_a != link_b
        or manifest.get("normalized_link_command") != link_a
        or (root / "compile_commands.normalized.json").read_bytes() != normalized_raw
    ):
        raise CapsuleError("current normalized build commands differ")
    binary_a = pathlib.Path(builds[0]["audit"]["binary"]["path"])
    binary_b = pathlib.Path(builds[1]["audit"]["binary"]["path"])
    if binary_a.stat().st_ino == binary_b.stat().st_ino and binary_a.stat().st_dev == binary_b.stat().st_dev:
        raise CapsuleError("designated binaries are the same inode")
    if binary_a.read_bytes() != binary_b.read_bytes():
        raise CapsuleError("designated binaries are no longer byte identical")
    if manifest.get("byte_identical_final_executables") is not True:
        raise CapsuleError("capsule executable-identity gate differs")
    sequences = manifest.get("orca_sequences", {})
    if sequences != {
        "count": 125, "sha256": SEQUENCE_SHA256,
        "values": list(EXPECTED_SEQUENCES),
    }:
        raise CapsuleError("capsule ORCA sequence record differs")
    negatives = manifest.get("negative_validation_matrix")
    if (
        not isinstance(negatives, list)
        or tuple(item.get("label") for item in negatives) != NEGATIVE_LABELS
        or any(
            item.get("returncode") != 2
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("stderr_sha256", "")))
            for item in negatives
        )
    ):
        raise CapsuleError("capsule negative-validation matrix differs")
    ledger = manifest.get("wrapper_command_ledger", {})
    if ledger != {
        "accepted_validation_invocations": 125,
        "allowed_commands": ["smoke", "validate"],
        "negative_validation_invocations": len(NEGATIVE_LABELS),
        "publication_run_invocations": 0,
        "smoke_invocations": 3,
        "total_validation_invocations": 125 + len(NEGATIVE_LABELS),
    }:
        raise CapsuleError("capsule wrapper-command ledger differs")
    validation_raw = (root / "validation.ndjson").read_bytes()
    if manifest.get("validation") != {
        "accepted_sequences_sha256": SEQUENCE_SHA256,
        "ndjson_sha256": sha256_bytes(validation_raw),
        "records": 125,
    }:
        raise CapsuleError("capsule validation identity differs")
    pixi = str(REPO / ".local/pixi/bin/pixi")
    cmake = "cmake"
    expected_recipe = {
        "classification": "prospective command recipe reconstructed from and checked against the effective CMake/Ninja state; not an observed shell-history record",
        "build_argv": [pixi, "run", "--locked", "--frozen", "--no-config", "--", cmake, "--build", "${BUILD_ROOT}"],
        "configure_argv": [
            pixi, "run", "--locked", "--frozen", "--no-config", "--", cmake,
            "-S", str(REPO / "tools/orca_baseline"), "-B", "${BUILD_ROOT}",
            "-G", "Ninja", f"-DRVO2_SOURCE_DIR={rvo2}",
            f"-DORCA_INPUT_MANIFEST_SHA256={INPUT_MANIFEST_SHA256}",
            "-DORCA_WRAPPER_ENABLE_TESTS=OFF", "-DCMAKE_BUILD_TYPE=Release",
            "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
        ],
        "pixi_cache_dir": "/private/tmp/dancers-pixi-cache",
    }
    if manifest.get("reproduction_recipe") != expected_recipe:
        raise CapsuleError("capsule reproduction recipe differs")
    platform_record = manifest.get("platform")
    if not isinstance(platform_record, dict) or set(platform_record) != {
        "machine", "mac_ver", "platform", "system"
    } or any(not isinstance(value, str) or not value for value in platform_record.values()):
        raise CapsuleError("capsule platform record differs")
    check_validation_payload(validation_raw, attempts, input_root)
    check_smoke_payloads(root)
    return manifest


def prepare(args: argparse.Namespace) -> str:
    input_root = require_real_absolute(args.input_root, "input root", directory=True)
    build_a = require_real_absolute(args.build_root_a, "build root A", directory=True)
    build_b = require_real_absolute(args.build_root_b, "build root B", directory=True)
    rvo2 = require_real_absolute(args.rvo2_source, "RVO2 source", directory=True)
    output = require_absent_absolute(args.output_root, "output root")
    require_disjoint({"input": input_root, "build A": build_a, "build B": build_b, "RVO2": rvo2, "output": output})
    if build_a == build_b:
        raise CapsuleError("the two build roots must differ")
    source_records = verify_source_identities()
    repository = verify_repository_anchor()
    toolchain = verify_toolchain()
    rvo_record = verify_rvo2(rvo2)
    _, attempts, before = verify_input(input_root)
    audit_a, compile_a, link_a = audit_build(build_a, rvo2)
    audit_b, compile_b, link_b = audit_build(build_b, rvo2)
    binary_a = pathlib.Path(audit_a["binary"]["path"])
    binary_b = pathlib.Path(audit_b["binary"]["path"])
    if (binary_a.stat().st_dev, binary_a.stat().st_ino) == (binary_b.stat().st_dev, binary_b.stat().st_ino):
        raise CapsuleError("the two binaries are the same inode")
    if binary_a.read_bytes() != binary_b.read_bytes() or audit_a["binary"]["sha256"] != audit_b["binary"]["sha256"]:
        raise CapsuleError("the two final executables are not byte identical")
    if compile_a != compile_b or link_a != link_b:
        raise CapsuleError("normalized compile/link commands differ between builds")
    if audit_a["generated_export_h"] != audit_b["generated_export_h"]:
        raise CapsuleError("generated RVO Export.h differs between builds")
    ledger: list[str] = []
    validation_raw = run_validations(binary_a, input_root, attempts, ledger)
    negatives = run_negative_matrix(binary_a, input_root, ledger)
    smoke_payloads = run_smokes(binary_a, ledger)
    _, _, after = verify_input(input_root)
    if after != before:
        raise CapsuleError("frozen input tree changed during admission")
    if ledger.count("smoke") != 3 or ledger.count("validate") != 125 + len(negatives) or any(item not in {"validate", "smoke"} for item in ledger):
        raise CapsuleError("wrapper command ledger differs")
    payloads: dict[str, bytes] = {
        "compile_commands.normalized.json": canonical_json_bytes(compile_a),
        "validation.ndjson": validation_raw,
        **smoke_payloads,
    }
    files = [payload_identity(relative, raw) for relative, raw in sorted(payloads.items())]
    pixi = str(REPO / ".local/pixi/bin/pixi")
    cmake = "cmake"
    common_configure = [
        pixi, "run", "--locked", "--frozen", "--no-config", "--", cmake,
        "-S", str(REPO / "tools/orca_baseline"), "-B", "${BUILD_ROOT}",
        "-G", "Ninja", f"-DRVO2_SOURCE_DIR={rvo2}",
        f"-DORCA_INPUT_MANIFEST_SHA256={INPUT_MANIFEST_SHA256}",
        "-DORCA_WRAPPER_ENABLE_TESTS=OFF", "-DCMAKE_BUILD_TYPE=Release",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
    ]
    manifest: dict[str, Any] = {
        "authentication": {
            "rule": "check requires an externally supplied exact manifest SHA-256; the sidecar is not a trust root",
        },
        "builds": [
            {"audit": audit_a, "root": str(build_a)},
            {"audit": audit_b, "root": str(build_b)},
        ],
        "byte_identical_final_executables": True,
        "capsule_id": CAPSULE_ID,
        "classification": {
            "accepted_validation_invocations": 125,
            "execution_authorized": False,
            "negative_validation_invocations": len(negatives),
            "publication_run_invocations": 0,
            "publication_simulation_steps": 0,
            "smoke_invocations": 3,
            "smoke_scope": "non-confirmatory wrapper fixtures only",
            "total_validation_invocations": 125 + len(negatives),
            "validation_scope": "input-derived initialization and public-getter audit before any doStep",
        },
        "files": files,
        "input": {
            "file_count": 50, "manifest_sha256": INPUT_MANIFEST_SHA256,
            "path": str(input_root), "schedule_sha256": SCHEDULE_SHA256,
            "snapshot": before,
        },
        "negative_validation_matrix": negatives,
        "normalized_link_command": link_a,
        "orca_sequences": {
            "count": 125, "sha256": SEQUENCE_SHA256,
            "values": list(EXPECTED_SEQUENCES),
        },
        "platform": {
            "machine": platform.machine(), "mac_ver": platform.mac_ver()[0],
            "platform": platform.platform(), "system": platform.system(),
        },
        "repository": repository,
        "reproduction_recipe": {
            "classification": "prospective command recipe reconstructed from and checked against the effective CMake/Ninja state; not an observed shell-history record",
            "build_argv": [pixi, "run", "--locked", "--frozen", "--no-config", "--", cmake, "--build", "${BUILD_ROOT}"],
            "configure_argv": common_configure,
            "pixi_cache_dir": "/private/tmp/dancers-pixi-cache",
        },
        "rvo2": rvo_record,
        "schema_version": SCHEMA,
        "source_files": source_records,
        "toolchain": toolchain,
        "validation": {
            "accepted_sequences_sha256": SEQUENCE_SHA256,
            "ndjson_sha256": sha256_bytes(validation_raw), "records": 125,
        },
        "wrapper_command_ledger": {
            "accepted_validation_invocations": 125,
            "allowed_commands": ["smoke", "validate"],
            "negative_validation_invocations": len(negatives),
            "publication_run_invocations": 0,
            "smoke_invocations": ledger.count("smoke"),
            "total_validation_invocations": ledger.count("validate"),
        },
    }
    digest = publish_capsule(output, payloads, manifest, validator=check_capsule)
    return digest


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    commands = value.add_subparsers(dest="command", required=True)
    prepare_parser = commands.add_parser("prepare", help="create a new evidence capsule")
    prepare_parser.add_argument("--input-root", type=pathlib.Path, required=True)
    prepare_parser.add_argument("--build-root-a", type=pathlib.Path, required=True)
    prepare_parser.add_argument("--build-root-b", type=pathlib.Path, required=True)
    prepare_parser.add_argument("--rvo2-source", type=pathlib.Path, required=True)
    prepare_parser.add_argument("--output-root", type=pathlib.Path, required=True)
    check_parser = commands.add_parser("check", help="read-only capsule verification")
    check_parser.add_argument("--capsule-root", type=pathlib.Path, required=True)
    check_parser.add_argument("--expected-manifest-sha256", required=True)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "prepare":
            digest = prepare(args)
            print(f"created ORCA build capsule: {args.output_root}")
            print(f"manifest sha256: {digest}")
        else:
            check_capsule(args.capsule_root, args.expected_manifest_sha256)
            print(f"ORCA build capsule is valid: {args.capsule_root}")
        return 0
    except (CapsuleError, OSError, KeyError, TypeError, ValueError) as error:
        print(f"ORCA build capsule failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
