#!/usr/bin/env python3
"""Build, execute, and analyze the prospective DANCeRS multi-map study."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import pathlib
import platform
import random
import re
import resource
import shutil
import signal
import statistics
import subprocess
import sys
import tarfile
import time
from typing import Any, Mapping, Sequence


REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.comparison import headless_geometry_v1 as geometry
from scripts.comparison import trace_compare
from scripts.replication import prepare_multimap_inputs as inputs_module


SCHEMA = "dancers-multimap-study-v1"
BUILD_SCHEMA = "dancers-multimap-build-manifest-v1"
INPUT_SCHEMA = inputs_module.SCHEMA
STUDY_ID = inputs_module.STUDY_ID
LOCK_REL = "pixi.lock"
LOCK_SHA256 = "636ecf1cf31431ffc19f453fef39299c3243718477e321fcea8814225bfe68be"
MANIF_REL = "thirdparty/dist/manif-0.0.5-build-source.tar.gz"
MANIF_SHA256 = "3d3a13e93384348946c689ebd4e818bbb9f72cbcc3cdafecb7b531f7d5191715"
RAYLIB_REL = "thirdparty/dist/raylib-5.5-build-source.tar.gz"
RAYLIB_SHA256 = "9758adff979fe36aa846897ecfd14e6d88ea13df87e9862b770d36b854717709"
COMMON_PATCH_SHA256 = "2ab81c9a9621c89de5854bbd8496cb2124845733140a913855ca61677185a95e"
ENDPOINTS = {
    "reference": {
        "algorithm_revision": "986f47deec5b079663c7d7f6120bfc20f0467901",
        "applied_head": "de0f60d1ad4b944c2b3264b8e3ccd348708b8b8a",
        "tree": "cb93386e28f3824ea7ffc17605b441b92f13f87d",
    },
    "optimized": {
        "algorithm_revision": "99364a41f1535a4bb0eddbe5a10030127ea77086",
        "applied_head": "26c8c6f9b262a2114ac28203419cb03caf8b0d88",
        "tree": "b6f713f47ea3aa57c824cf2c481f3647ac7c9408",
    },
}
METHODS = ("reference", "optimized")
RUN_CLASSES = ("digest_fidelity", "positions_fidelity", "timing")
REPETITIONS = {"digest_fidelity": 1, "positions_fidelity": 1, "timing": 3}
EXPECTED_SCENES = 200
EXPECTED_MAPS = 41
EXPECTED_WARMUPS = EXPECTED_MAPS * len(METHODS)
EXPECTED_MEASURED = EXPECTED_SCENES * 2 * sum(REPETITIONS.values())
EXPECTED_PROCESSES = EXPECTED_WARMUPS + EXPECTED_MEASURED
TIMEOUT_SECONDS = 120.0
BOOTSTRAP_DRAWS = 100_000
BOOTSTRAP_SEED = 20_260_824
OBSERVER_ENV = {
    "DANCERS_TRACE_OUT",
    "DANCERS_SUMMARY_OUT",
    "DANCERS_RUN_METADATA",
    "DANCERS_REQUIRE_PROVENANCE",
    "DANCERS_TRACE_DETAIL",
}
SUMMARY_PARITY_FIELDS = (
    "termination_reason",
    "all_goals_reached",
    "goals_reached",
    "goals_total",
    "first_all_goals_tick",
    "iterations",
    "total_path_length_m",
    "mean_path_length_m_per_robot",
    "per_robot_goal_tick",
    "per_robot_path_length_m",
    "obstacle_collision_events",
    "obstacle_collision_exposure_seconds",
    "pair_collision_events",
    "pair_collision_exposure_seconds",
    "minimum_obstacle_clearance_m",
    "minimum_pair_clearance_m",
    "invalid_geometry_evaluations",
    "invalid_robot_frames",
    "nonfinite_numeric_observations",
    "nonfinite_state_observations",
    "paper_metrics",
    "final_tick",
    "final_robot_count",
    "maximum_robot_count",
    "run_valid",
)
TIMESTEP_RE = re.compile(rb"(?m)^timesteps:\s*(\d+)\b")
ROBOT_COUNT_RE = re.compile(rb"Final robot count:\s*\[(\d+)\]")


class StudyError(RuntimeError):
    pass


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
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


def file_identity(path: pathlib.Path, *, relative_to: pathlib.Path) -> dict[str, Any]:
    return {
        "bytes": path.stat().st_size,
        "path": path.relative_to(relative_to).as_posix(),
        "sha256": sha256_file(path),
    }


def require_identity(path: pathlib.Path, identity: Mapping[str, Any], label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise StudyError(f"{label} is not a regular file: {path}")
    if path.stat().st_size != identity.get("bytes") or sha256_file(path) != identity.get("sha256"):
        raise StudyError(f"{label} identity differs: {path}")


def git_text(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if completed.returncode:
        raise StudyError(completed.stderr.decode("utf-8", "replace"))
    return completed.stdout.decode("utf-8").strip()


def validate_repository_inputs(input_root: pathlib.Path) -> dict[str, Any]:
    manifest = inputs_module.load_and_validate(input_root)
    if manifest.get("schema_version") != INPUT_SCHEMA:
        raise StudyError("input manifest schema differs")
    for relative, expected in (
        (LOCK_REL, LOCK_SHA256),
        (MANIF_REL, MANIF_SHA256),
        (RAYLIB_REL, RAYLIB_SHA256),
    ):
        path = REPO / relative
        if not path.is_file() or sha256_file(path) != expected:
            raise StudyError(f"frozen repository input differs: {relative}")
    for endpoint, record in ENDPOINTS.items():
        if git_text("rev-parse", f"{record['applied_head']}^{{commit}}") != record["applied_head"]:
            raise StudyError(f"{endpoint} applied head differs")
        if git_text("rev-parse", f"{record['applied_head']}^{{tree}}") != record["tree"]:
            raise StudyError(f"{endpoint} tree differs")
    return manifest


def safe_extract(raw: bytes, destination: pathlib.Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    root = destination.resolve()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
        for member in archive.getmembers():
            candidate = (destination / member.name).resolve()
            if candidate != root and root not in candidate.parents:
                raise StudyError(f"unsafe archive member: {member.name}")
            if member.issym() or member.islnk() or member.isdev():
                raise StudyError(f"unsupported archive member: {member.name}")
        archive.extractall(destination)


def extract_single_root(path: pathlib.Path, destination: pathlib.Path) -> pathlib.Path:
    safe_extract(path.read_bytes(), destination)
    children = list(destination.iterdir())
    if len(children) != 1 or not children[0].is_dir():
        raise StudyError(f"dependency archive lacks one root: {path}")
    return children[0]


def tool_paths() -> dict[str, pathlib.Path]:
    env_root = REPO / ".pixi" / "envs" / "default"
    paths = {
        "cmake": env_root / "bin" / "cmake",
        "ninja": env_root / "bin" / "ninja",
        "clang": env_root / "bin" / "clang",
        "clangxx": env_root / "bin" / "clang++",
        "libomp": env_root / "lib" / "libomp.dylib",
    }
    for name, path in paths.items():
        if not path.is_file():
            raise StudyError(f"toolchain member missing ({name}): {path}")
    return paths


def command_env() -> dict[str, str]:
    env_root = REPO / ".pixi" / "envs" / "default"
    result = {
        "PATH": f"{env_root / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
        "CONDA_PREFIX": str(env_root),
        "CMAKE_PREFIX_PATH": str(env_root),
        "OMP_NUM_THREADS": "1",
        "OMP_DYNAMIC": "FALSE",
        "OMP_WAIT_POLICY": "passive",
        "LC_ALL": "C",
        "LANG": "C",
        "TMPDIR": "/private/tmp" if sys.platform == "darwin" else "/tmp",
    }
    library = str(env_root / "lib")
    if sys.platform == "darwin":
        result["DYLD_LIBRARY_PATH"] = library
    elif sys.platform.startswith("linux"):
        result["LD_LIBRARY_PATH"] = library
    return result


def _run_logged(argv: list[str], *, cwd: pathlib.Path, log: pathlib.Path) -> None:
    started = time.monotonic_ns()
    completed = subprocess.run(
        argv,
        cwd=cwd,
        env=command_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    header = canonical_json_bytes(
        {
            "argv": argv,
            "cwd": str(cwd),
            "elapsed_ns": time.monotonic_ns() - started,
            "returncode": completed.returncode,
        }
    )
    log.write_bytes(header + completed.stdout)
    if completed.returncode:
        raise StudyError(f"build command failed; see {log}")


def build_products(input_root: pathlib.Path, build_root: pathlib.Path) -> dict[str, Any]:
    input_root = input_root.resolve()
    build_root = build_root.resolve()
    input_manifest = validate_repository_inputs(input_root)
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise StudyError("v1 build is registered only for macOS arm64")
    if build_root.exists() or build_root.is_symlink():
        raise StudyError(f"build root already exists: {build_root}")
    build_root.mkdir(parents=True, exist_ok=False)
    (build_root / "logs").mkdir()
    deps = build_root / "deps"
    deps.mkdir()
    manif = extract_single_root(REPO / MANIF_REL, deps / "manif")
    raylib = extract_single_root(REPO / RAYLIB_REL, deps / "raylib")
    tools = tool_paths()
    products: dict[str, dict[str, Any]] = {}
    input_destination = pathlib.Path(*inputs_module.OUTPUT_RELATIVE.parts)
    for method in METHODS:
        endpoint = ENDPOINTS[method]
        method_root = build_root / method
        source = method_root / "source"
        method_root.mkdir()
        archived = subprocess.run(
            ["git", "archive", "--format=tar", endpoint["applied_head"]],
            cwd=REPO,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if archived.returncode:
            raise StudyError(archived.stderr.decode("utf-8", "replace"))
        safe_extract(archived.stdout, source)
        mirror = source / input_destination
        shutil.copytree(input_root, mirror)
        if sha256_file(mirror / "manifest.json") != sha256_file(input_root / "manifest.json"):
            raise StudyError("mirrored input manifest differs")
        products[method] = {}
        for role, observer in (("timing", False), ("fidelity", True)):
            build = method_root / f"build-{role}"
            argv = [
                str(tools["cmake"]),
                "-S",
                str(source),
                "-B",
                str(build),
                "-G",
                "Ninja",
                "-DCMAKE_BUILD_TYPE=Release",
                f"-DCMAKE_MAKE_PROGRAM={tools['ninja']}",
                f"-DCMAKE_C_COMPILER={tools['clang']}",
                f"-DCMAKE_CXX_COMPILER={tools['clangxx']}",
                f"-DCMAKE_PREFIX_PATH={REPO / '.pixi' / 'envs' / 'default'}",
                f"-DOpenMP_omp_LIBRARY={tools['libomp']}",
                f"-DFETCHCONTENT_SOURCE_DIR_MANIF={manif}",
                f"-DFETCHCONTENT_SOURCE_DIR_RAYLIB={raylib}",
                "-DDANCERS_BUILD_TESTS=OFF",
                f"-DDANCERS_ENABLE_COMPARISON_OBSERVER={'ON' if observer else 'OFF'}",
            ]
            _run_logged(
                argv,
                cwd=method_root,
                log=build_root / "logs" / f"{method}-{role}-configure.log",
            )
            _run_logged(
                [str(tools["cmake"]), "--build", str(build), "--target", "dancers", "--parallel", "8"],
                cwd=method_root,
                log=build_root / "logs" / f"{method}-{role}-build.log",
            )
            binary = build / "dancers"
            if not binary.is_file() or not os.access(binary, os.X_OK):
                raise StudyError(f"built binary missing: {method}/{role}")
            cache = build / "CMakeCache.txt"
            expected_cache = f"DANCERS_ENABLE_COMPARISON_OBSERVER:BOOL={'ON' if observer else 'OFF'}"
            if expected_cache not in cache.read_text(encoding="utf-8", errors="strict"):
                raise StudyError(f"observer cache binding differs: {method}/{role}")
            products[method][role] = {
                "binary": file_identity(binary, relative_to=REPO),
                "observer": observer,
            }
        products[method]["algorithm_revision"] = endpoint["algorithm_revision"]
        products[method]["applied_head"] = endpoint["applied_head"]
        products[method]["tree"] = endpoint["tree"]
        products[method]["source_archive_sha256"] = sha256_bytes(archived.stdout)
        products[method]["source_root"] = source.relative_to(REPO).as_posix()
    manifest = {
        "binaries": products,
        "common_patch_sha256": COMMON_PATCH_SHA256,
        "dependencies": {MANIF_REL: MANIF_SHA256, RAYLIB_REL: RAYLIB_SHA256},
        "host": host_observation(require_power=False),
        "input_manifest": file_identity(input_root / "manifest.json", relative_to=REPO),
        "lock_sha256": LOCK_SHA256,
        "protocol": {
            "generator": file_identity(
                REPO / "scripts/replication/prepare_multimap_inputs.py",
                relative_to=REPO,
            ),
            "preregistration": file_identity(
                REPO / "docs/publication/DANCERS_MULTIMAP_REPLICATION_STUDY_V1.md",
                relative_to=REPO,
            ),
            "repository_head": git_text("rev-parse", "HEAD"),
            "runner": file_identity(pathlib.Path(__file__), relative_to=REPO),
        },
        "schema_version": BUILD_SCHEMA,
        "study_id": STUDY_ID,
        "tools": {
            name: file_identity(path, relative_to=REPO) for name, path in tools.items()
        },
    }
    (build_root / "build_manifest.json").write_bytes(canonical_json_bytes(manifest))
    return manifest


def _probe(argv: list[str]) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=30,
        )
        return {
            "argv": argv,
            "output": completed.stdout.decode("utf-8", "replace")[:16_384],
            "returncode": completed.returncode,
        }
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"argv": argv, "error": f"{type(error).__name__}: {error}", "returncode": None}


def host_observation(*, require_power: bool) -> dict[str, Any]:
    if sys.platform == "darwin":
        power = _probe(["/usr/bin/pmset", "-g", "batt"])
        if require_power and (
            power["returncode"] != 0 or "AC Power" not in power.get("output", "")
        ):
            raise StudyError("measured execution requires AC power")
        os_probe = _probe(["/usr/bin/sw_vers"])
        thermal = _probe(["/usr/bin/pmset", "-g", "therm"])
        hardware = _probe(
            [
                "/usr/sbin/system_profiler",
                "SPHardwareDataType",
                "-detailLevel",
                "mini",
            ]
        )
    else:
        power = {"not_available": True}
        if require_power:
            raise StudyError("v1 measured host gate supports Darwin AC checks only")
        os_probe = _probe(["/usr/bin/uname", "-a"])
        thermal = {"not_available": True}
        hardware = {"not_available": True}
    return {
        "architecture": platform.machine(),
        "free_bytes": shutil.disk_usage(REPO).free,
        "hardware": hardware,
        "host_id_sha256": sha256_bytes(platform.node().encode("utf-8")),
        "os": os_probe,
        "platform": sys.platform,
        "power": power,
        "python": sys.version,
        "thermal": thermal,
        "time_unix_ns": time.time_ns(),
    }


def load_build_manifest(build_root: pathlib.Path, input_root: pathlib.Path) -> dict[str, Any]:
    path = build_root / "build_manifest.json"
    raw = path.read_bytes()
    manifest = json.loads(raw)
    if raw != canonical_json_bytes(manifest):
        raise StudyError("build manifest is not canonical JSON")
    if manifest.get("schema_version") != BUILD_SCHEMA or manifest.get("study_id") != STUDY_ID:
        raise StudyError("build manifest schema/study differs")
    if manifest.get("lock_sha256") != LOCK_SHA256 or manifest.get("common_patch_sha256") != COMMON_PATCH_SHA256:
        raise StudyError("build manifest frozen identity differs")
    protocol = manifest.get("protocol")
    if not isinstance(protocol, dict):
        raise StudyError("build protocol identities are missing")
    for key in ("generator", "preregistration", "runner"):
        identity = protocol.get(key)
        if not isinstance(identity, dict):
            raise StudyError(f"build protocol identity missing: {key}")
        require_identity(REPO / identity["path"], identity, f"protocol {key}")
    input_identity = manifest.get("input_manifest")
    require_identity(input_root / "manifest.json", input_identity, "input manifest")
    for method in METHODS:
        product = manifest["binaries"][method]
        endpoint = ENDPOINTS[method]
        for key in ("algorithm_revision", "applied_head", "tree"):
            if product.get(key) != endpoint[key]:
                raise StudyError(f"{method} source identity differs")
        source = REPO / product["source_root"]
        if source.is_symlink() or not source.is_dir():
            raise StudyError(f"{method} source root differs")
        mirror = source.joinpath(*inputs_module.OUTPUT_RELATIVE.parts)
        if sha256_file(mirror / "manifest.json") != input_identity["sha256"]:
            raise StudyError(f"{method} input mirror differs")
        for role, expected_observer in (("timing", False), ("fidelity", True)):
            record = product[role]
            if record.get("observer") is not expected_observer:
                raise StudyError(f"{method}/{role} observer binding differs")
            binary = REPO / record["binary"]["path"]
            require_identity(binary, record["binary"], f"{method}/{role} binary")
    return manifest


def _pair_order(scene_id: str, run_class: str, repetition: int) -> tuple[str, str]:
    digest = hashlib.sha256(
        f"dancers-multimap-order-v1\0{scene_id}\0{run_class}\0{repetition}".encode("ascii")
    ).digest()
    return METHODS if digest[0] % 2 == 0 else tuple(reversed(METHODS))


def schedule_records(input_manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    scenes = list(input_manifest["scenes"])
    if len(scenes) != EXPECTED_SCENES:
        raise StudyError("input scene count differs before scheduling")
    records: list[dict[str, Any]] = []
    sequence = 0
    map_groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for scene in scenes:
        map_groups.setdefault((scene["family"], scene["map_id"]), []).append(scene)
    if len(map_groups) != EXPECTED_MAPS:
        raise StudyError("input map count differs before scheduling")
    for map_block, key in enumerate(sorted(map_groups)):
        representative = sorted(
            map_groups[key], key=lambda item: (item["mission_label"], item["robot_count"])
        )[0]
        order = _pair_order(representative["scene_id"], "warmup", 0)
        for position, method in enumerate(order):
            records.append(
                {
                    "map_block": map_block,
                    "method": method,
                    "pair_id": f"warmup-{representative['family']}-{representative['map_id']}",
                    "pair_position": position,
                    "phase": "warmup",
                    "repetition": 0,
                    "run_class": "timing",
                    "sequence": sequence,
                    **representative,
                }
            )
            sequence += 1
    for scene_index, scene in enumerate(scenes):
        for run_class in RUN_CLASSES:
            for repetition in range(1, REPETITIONS[run_class] + 1):
                pair_id = f"{scene['scene_id']}-{run_class}-r{repetition}"
                for position, method in enumerate(_pair_order(scene["scene_id"], run_class, repetition)):
                    records.append(
                        {
                            "map_block": None,
                            "method": method,
                            "pair_id": pair_id,
                            "pair_position": position,
                            "phase": "measured",
                            "repetition": repetition,
                            "run_class": run_class,
                            "scene_index": scene_index,
                            "sequence": sequence,
                            **scene,
                        }
                    )
                    sequence += 1
    if len(records) != EXPECTED_PROCESSES:
        raise StudyError(f"schedule size differs: {len(records)}")
    if [record["sequence"] for record in records] != list(range(EXPECTED_PROCESSES)):
        raise StudyError("schedule sequence differs")
    return records


def rss_to_mib(raw: int, system: str | None = None) -> tuple[str, float]:
    platform_name = sys.platform if system is None else system
    if type(raw) is not int or raw < 0:
        raise StudyError("ru_maxrss must be a non-negative integer")
    if platform_name == "darwin":
        return "bytes", raw / (1024.0 * 1024.0)
    if platform_name.startswith("linux"):
        return "kibibytes", raw / 1024.0
    raise StudyError(f"unsupported ru_maxrss platform: {platform_name}")


def _wait_child(pid: int, timeout: float) -> tuple[int, resource.struct_rusage, bool]:
    deadline = time.monotonic() + timeout
    while True:
        waited, status, usage = os.wait4(pid, os.WNOHANG)
        if waited == pid:
            return status, usage, False
        if time.monotonic() >= deadline:
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            grace = time.monotonic() + 5.0
            while time.monotonic() < grace:
                waited, status, usage = os.wait4(pid, os.WNOHANG)
                if waited == pid:
                    return status, usage, True
                time.sleep(0.01)
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            _waited, status, usage = os.wait4(pid, 0)
            return status, usage, True
        time.sleep(0.005)


def _artifact_record(path: pathlib.Path, output_root: pathlib.Path) -> dict[str, Any]:
    return file_identity(path, relative_to=output_root)


def _validate_stdout(raw: bytes, count: int) -> None:
    ticks = [int(match.group(1)) for match in TIMESTEP_RE.finditer(raw)]
    counts = [int(match.group(1)) for match in ROBOT_COUNT_RE.finditer(raw)]
    if not ticks or ticks[-1] != 100 or any(tick > 100 for tick in ticks):
        raise StudyError("simulator stdout fixed-work timestep gate failed")
    if not counts or counts[-1] != count:
        raise StudyError("simulator stdout robot-count gate failed")


def run_one(
    *,
    record: Mapping[str, Any],
    build_manifest: Mapping[str, Any],
    input_root: pathlib.Path,
    output_root: pathlib.Path,
) -> dict[str, Any]:
    method = record["method"]
    role = "timing" if record["run_class"] == "timing" else "fidelity"
    product = build_manifest["binaries"][method]
    binary_record = product[role]["binary"]
    binary = REPO / binary_record["path"]
    require_identity(binary, binary_record, f"{method}/{role} binary before run")
    source = REPO / product["source_root"]
    config_original = input_root / record["config_relpath"]
    scenario_original = input_root / record["scenario_relpath"]
    map_original = input_root / record["map_relpath"]
    for path, expected, label in (
        (config_original, record["config_sha256"], "config"),
        (scenario_original, record["scenario_sha256"], "scenario"),
        (map_original, record["map_sha256"], "map"),
    ):
        if not path.is_file() or sha256_file(path) != expected:
            raise StudyError(f"{label} identity differs before sequence {record['sequence']}")
    mirror_root = source.joinpath(*inputs_module.OUTPUT_RELATIVE.parts)
    config = mirror_root / record["config_relpath"]
    scenario = mirror_root / record["scenario_relpath"]
    map_path = mirror_root / record["map_relpath"]
    for original, mirrored in ((config_original, config), (scenario_original, scenario), (map_original, map_path)):
        if not mirrored.is_file() or sha256_file(mirrored) != sha256_file(original):
            raise StudyError("source-tree input mirror differs")
    attempt = output_root / "attempts" / f"{int(record['sequence']):04d}-{record['phase']}-{record['run_class']}-{method}"
    attempt.mkdir(parents=True, exist_ok=False)
    stdout_path = attempt / "stdout.txt"
    stderr_path = attempt / "stderr.txt"
    metadata_path = attempt / "metadata.json"
    metadata = {
        "algorithm_revision": product["algorithm_revision"],
        "binary_sha256": binary_record["sha256"],
        "common_patch_sha256": COMMON_PATCH_SHA256,
        "config_sha256": record["config_sha256"],
        "implementation_id": method,
        "map_sha256": record["map_sha256"],
        "omp_threads": 1,
        "pixi_lock_sha256": LOCK_SHA256,
        "run_id": f"{STUDY_ID}-{record['sequence']:04d}",
        "scenario_sha256": record["scenario_sha256"],
        "scene_id": record["scene_id"],
    }
    metadata_path.write_bytes(canonical_json_bytes(metadata))
    environment = {key: value for key, value in command_env().items() if key not in OBSERVER_ENV}
    trace_path = attempt / "trace.ndjson"
    summary_path = attempt / "summary.json"
    if role == "fidelity":
        environment.update(
            {
                "DANCERS_TRACE_OUT": str(trace_path),
                "DANCERS_SUMMARY_OUT": str(summary_path),
                "DANCERS_RUN_METADATA": str(metadata_path),
                "DANCERS_REQUIRE_PROVENANCE": "1",
                "DANCERS_TRACE_DETAIL": "digest" if record["run_class"] == "digest_fidelity" else "positions",
            }
        )
    config_argument = os.path.relpath(config, source)
    if any(character.isspace() for character in config_argument):
        raise StudyError("relative config argument contains whitespace")
    started_wall = time.monotonic_ns()
    started_unix = time.time_ns()
    with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
        process = subprocess.Popen(
            [str(binary), "--cfg", config_argument],
            cwd=source,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
            close_fds=True,
        )
        status, usage, timed_out = _wait_child(process.pid, TIMEOUT_SECONDS)
        process.returncode = os.waitstatus_to_exitcode(status)
    wall = (time.monotonic_ns() - started_wall) / 1e9
    stdout_raw = stdout_path.read_bytes()
    _validate_stdout(stdout_raw, int(record["robot_count"]))
    unit, rss_mib = rss_to_mib(int(usage.ru_maxrss))
    if process.returncode != 0 or timed_out:
        raise StudyError(f"simulator failed at sequence {record['sequence']}: rc={process.returncode} timeout={timed_out}")
    if role == "fidelity" and (not trace_path.is_file() or not summary_path.is_file()):
        raise StudyError(f"fidelity artifacts missing at sequence {record['sequence']}")
    if role == "timing" and (trace_path.exists() or summary_path.exists()):
        raise StudyError(f"timing observer artifacts appeared at sequence {record['sequence']}")
    result: dict[str, Any] = {
        **record,
        "binary_sha256": binary_record["sha256"],
        "measurement": {
            "peak_rss_mib": rss_mib,
            "peak_rss_raw": int(usage.ru_maxrss),
            "peak_rss_raw_unit": unit,
            "returncode": process.returncode,
            "system_cpu_seconds": float(usage.ru_stime),
            "timed_out": timed_out,
            "total_cpu_seconds": float(usage.ru_utime + usage.ru_stime),
            "user_cpu_seconds": float(usage.ru_utime),
            "wall_seconds": wall,
        },
        "metadata": _artifact_record(metadata_path, output_root),
        "started_unix_ns": started_unix,
        "stderr": _artifact_record(stderr_path, output_root),
        "stdout": _artifact_record(stdout_path, output_root),
    }
    if role == "fidelity":
        result["trace"] = _artifact_record(trace_path, output_root)
        result["summary"] = _artifact_record(summary_path, output_root)
    for metric in ("wall_seconds", "user_cpu_seconds", "total_cpu_seconds"):
        value = result["measurement"][metric]
        if not math.isfinite(value) or value <= 0:
            raise StudyError(f"invalid {metric} at sequence {record['sequence']}")
    require_identity(binary, binary_record, f"{method}/{role} binary after run")
    return result


def execute(input_root: pathlib.Path, build_root: pathlib.Path, output_root: pathlib.Path) -> None:
    input_root = input_root.resolve()
    build_root = build_root.resolve()
    output_root = output_root.resolve()
    input_manifest = validate_repository_inputs(input_root)
    build_manifest = load_build_manifest(build_root, input_root)
    if output_root.exists() or output_root.is_symlink():
        raise StudyError(f"output root already exists: {output_root}")
    if shutil.disk_usage(REPO).free < 12 * 1024**3:
        raise StudyError("at least 12 GiB free disk is required")
    output_root.mkdir(parents=True, exist_ok=False)
    (output_root / "attempts").mkdir()
    shutil.copyfile(build_root / "build_manifest.json", output_root / "build_manifest.json")
    shutil.copyfile(input_root / "manifest.json", output_root / "input_manifest.json")
    schedule = schedule_records(input_manifest)
    (output_root / "schedule.json").write_bytes(
        canonical_json_bytes(
            {
                "expected_processes": EXPECTED_PROCESSES,
                "records": schedule,
                "schema_version": SCHEMA,
                "study_id": STUDY_ID,
            }
        )
    )
    (output_root / "host_initial.json").write_bytes(
        canonical_json_bytes(host_observation(require_power=True))
    )
    ledger_path = output_root / "ledger.ndjson"
    current_block: tuple[str, str] | None = None
    with ledger_path.open("xb") as ledger:
        for record in schedule:
            block = (record["family"], record["map_id"])
            if block != current_block:
                observation = host_observation(require_power=True)
                with (output_root / "host_blocks.ndjson").open("ab") as host_ledger:
                    host_ledger.write(canonical_json_bytes({"block": block, "observation": observation}))
                current_block = block
            result = run_one(
                record=record,
                build_manifest=build_manifest,
                input_root=input_root,
                output_root=output_root,
            )
            ledger.write(canonical_json_bytes(result))
            ledger.flush()
            os.fsync(ledger.fileno())
    (output_root / "host_final.json").write_bytes(
        canonical_json_bytes(host_observation(require_power=True))
    )


def _read_canonical_json(path: pathlib.Path, label: str) -> Any:
    raw = path.read_bytes()
    value = json.loads(raw)
    if raw != canonical_json_bytes(value):
        raise StudyError(f"{label} is not canonical JSON")
    return value


def read_session(output_root: pathlib.Path) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    schedule_doc = _read_canonical_json(output_root / "schedule.json", "schedule")
    if (
        schedule_doc.get("schema_version") != SCHEMA
        or schedule_doc.get("study_id") != STUDY_ID
        or schedule_doc.get("expected_processes") != EXPECTED_PROCESSES
    ):
        raise StudyError("session schedule header differs")
    schedule = schedule_doc.get("records")
    if not isinstance(schedule, list) or len(schedule) != EXPECTED_PROCESSES:
        raise StudyError("session schedule inventory differs")
    ledger_lines = (output_root / "ledger.ndjson").read_bytes().splitlines(keepends=True)
    records: list[dict[str, Any]] = []
    for index, raw in enumerate(ledger_lines):
        value = json.loads(raw)
        if raw != canonical_json_bytes(value):
            raise StudyError(f"ledger record {index} is not canonical JSON")
        records.append(value)
    if len(records) != len(schedule):
        raise StudyError(f"ledger inventory differs: {len(records)} != {len(schedule)}")
    join_keys = (
        "sequence",
        "phase",
        "run_class",
        "method",
        "pair_id",
        "pair_position",
        "repetition",
        "scene_id",
        "family",
        "map_id",
        "mission_id",
        "mission_label",
        "robot_count",
        "cell_id",
        "config_sha256",
        "map_sha256",
        "scenario_sha256",
    )
    for planned, actual in zip(schedule, records):
        if any(actual.get(key) != planned.get(key) for key in join_keys):
            raise StudyError(f"ledger differs from schedule at sequence {planned['sequence']}")
        measurement = actual.get("measurement")
        if not isinstance(measurement, dict):
            raise StudyError(f"measurement missing at sequence {planned['sequence']}")
        required = (
            "wall_seconds",
            "user_cpu_seconds",
            "system_cpu_seconds",
            "total_cpu_seconds",
            "peak_rss_raw",
            "peak_rss_raw_unit",
            "peak_rss_mib",
        )
        if any(key not in measurement for key in required):
            raise StudyError(f"resource field missing at sequence {planned['sequence']}")
        if measurement.get("returncode") != 0 or measurement.get("timed_out") is not False:
            raise StudyError(f"process did not complete at sequence {planned['sequence']}")
        if not math.isclose(
            measurement["total_cpu_seconds"],
            measurement["user_cpu_seconds"] + measurement["system_cpu_seconds"],
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise StudyError(f"total CPU identity differs at sequence {planned['sequence']}")
        unit, expected_mib = rss_to_mib(measurement["peak_rss_raw"], sys.platform)
        if unit != measurement["peak_rss_raw_unit"] or expected_mib != measurement["peak_rss_mib"]:
            raise StudyError(f"RSS conversion differs at sequence {planned['sequence']}")
        for artifact_name in ("stdout", "stderr", "metadata", "trace", "summary"):
            if artifact_name not in actual:
                if artifact_name in ("trace", "summary") and actual["run_class"] == "timing":
                    continue
                raise StudyError(f"{artifact_name} identity missing at sequence {planned['sequence']}")
            identity = actual[artifact_name]
            require_identity(output_root / identity["path"], identity, f"sequence {planned['sequence']} {artifact_name}")
    input_raw = (output_root / "input_manifest.json").read_bytes()
    input_manifest = json.loads(input_raw)
    if input_raw != inputs_module.canonical_json_bytes(input_manifest):
        raise StudyError("session input manifest is not generator-canonical JSON")
    return input_manifest, schedule, records


def _summary_semantics(path: pathlib.Path, record: Mapping[str, Any]) -> dict[str, Any]:
    raw = json.loads(path.read_bytes())
    if raw.get("schema_version") != "dancers-comparison-summary-v1":
        raise StudyError(f"summary schema differs: {path}")
    if raw.get("trace_detail") not in ("digest", "positions"):
        raise StudyError(f"summary trace detail differs: {path}")
    if raw.get("final_tick") != 100 or raw.get("final_robot_count") != record["robot_count"]:
        raise StudyError(f"summary fixed-work fields differ: {path}")
    if raw.get("run_valid") is not True:
        raise StudyError(f"observer reports invalid run: {path}")
    missing = [field for field in SUMMARY_PARITY_FIELDS if field not in raw]
    if missing:
        raise StudyError(f"summary parity fields missing ({missing}): {path}")
    return {field: raw[field] for field in SUMMARY_PARITY_FIELDS}


def _positions_sweeps(path: pathlib.Path) -> tuple[list[dict[str, Any]], float]:
    sweeps: list[dict[str, Any]] = []
    with trace_compare.TraceReader(path) as reader:
        if reader.header.trace_detail != "positions":
            raise StudyError("geometry trace is not positions detail")
        timestep = reader.header.canonical["resolved_config"]["timestep_seconds"]
        frames = list(reader.frames())
    if len(frames) != 200:
        raise StudyError(f"positions trace frame count differs: {len(frames)}")
    for index in range(100):
        first, last = frames[2 * index : 2 * index + 2]
        if first["phase"] != "pre_update" or last["phase"] != "post_update" or first["tick"] != index or last["tick"] != index:
            raise StudyError(f"positions frame pair differs at tick {index}")
        start = [
            {
                "position": [robot["state"][0], robot["state"][1]],
                "radius_m": robot["radius_m"],
                "rid": robot["rid"],
            }
            for robot in first["robots"]
        ]
        end = [
            {
                "position": [robot["state"][0], robot["state"][1]],
                "radius_m": robot["radius_m"],
                "rid": robot["rid"],
            }
            for robot in last["robots"]
        ]
        sweeps.append({"end": end, "start": start, "tick": index})
    return sweeps, float(timestep)


def analyze_fidelity(
    output_root: pathlib.Path,
    input_root: pathlib.Path,
    records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    input_manifest = inputs_module.load_and_validate(input_root)
    raster_hashes = {
        (item["family"], item["map_id"]): item["effective_raster_sha256"]
        for item in input_manifest["maps"]
    }
    measured = [record for record in records if record["phase"] == "measured"]
    rows: list[dict[str, Any]] = []
    task_rows: list[dict[str, Any]] = []
    for run_class in ("digest_fidelity", "positions_fidelity"):
        class_records = [record for record in measured if record["run_class"] == run_class]
        by_pair: dict[str, list[Mapping[str, Any]]] = {}
        for record in class_records:
            by_pair.setdefault(record["pair_id"], []).append(record)
        if len(by_pair) != EXPECTED_SCENES:
            raise StudyError(f"{run_class} pair inventory differs")
        for pair_id in sorted(by_pair):
            pair = by_pair[pair_id]
            if len(pair) != 2 or {item["method"] for item in pair} != set(METHODS):
                raise StudyError(f"fidelity pair differs: {pair_id}")
            by_method = {item["method"]: item for item in pair}
            reference = by_method["reference"]
            optimized = by_method["optimized"]
            ref_trace = output_root / reference["trace"]["path"]
            opt_trace = output_root / optimized["trace"]["path"]
            comparison = trace_compare.compare_traces(ref_trace, opt_trace)
            if not comparison.equal or comparison.frames_compared != 200:
                mismatch = None if comparison.mismatch is None else comparison.mismatch.format()
                raise StudyError(f"semantic trace mismatch in {pair_id}: {mismatch}")
            ref_summary = _summary_semantics(output_root / reference["summary"]["path"], reference)
            opt_summary = _summary_semantics(output_root / optimized["summary"]["path"], optimized)
            if ref_summary != opt_summary:
                raise StudyError(f"semantic summary mismatch in {pair_id}")
            row = {
                "family": reference["family"],
                "frames_compared": comparison.frames_compared,
                "map_id": reference["map_id"],
                "mission_id": reference["mission_id"],
                "robot_count": reference["robot_count"],
                "run_class": run_class,
                "scene_id": reference["scene_id"],
                "semantic_equal": True,
                "summary_equal": True,
            }
            rows.append(row)
            if run_class == "positions_fidelity":
                sweeps, timestep = _positions_sweeps(ref_trace)
                map_path = input_root / reference["map_relpath"]
                raster = geometry.effective_raster_from_png_bytes(map_path.read_bytes())
                if raster.sha256 != raster_hashes[(reference["family"], reference["map_id"])]:
                    raise StudyError(f"effective raster identity differs in {pair_id}")
                report = geometry.analyze_sweeps(sweeps, raster, timestep_seconds=timestep)
                task_rows.append(
                    {
                        "family": reference["family"],
                        "geometry_contact_free": report["geometry_contact_free"],
                        "geometry_failure_reasons": report["geometry_failure_reasons"],
                        "map_id": reference["map_id"],
                        "minimum_obstacle_clearance_lower_bound_m": report[
                            "minimum_obstacle_clearance_lower_bound_m"
                        ],
                        "minimum_pair_clearance_m": report["minimum_pair_clearance_m"],
                        "mission_id": reference["mission_id"],
                        "obstacle_contact_exposure_seconds": report[
                            "obstacle_contact_exposure_seconds"
                        ],
                        "pair_contact_exposure_seconds": report[
                            "pair_contact_exposure_seconds"
                        ],
                        "robot_count": reference["robot_count"],
                        "scene_id": reference["scene_id"],
                        "task_valid": report["task_valid"],
                    }
                )
    return {
        "digest_pair_count": sum(row["run_class"] == "digest_fidelity" for row in rows),
        "all_semantically_equal": all(row["semantic_equal"] and row["summary_equal"] for row in rows),
        "pair_rows": rows,
        "positions_pair_count": sum(row["run_class"] == "positions_fidelity" for row in rows),
        "task_invalid_scene_count": sum(not row["task_valid"] for row in task_rows),
        "task_rows": task_rows,
        "task_valid_scene_count": sum(row["task_valid"] for row in task_rows),
    }


def geometric_mean(values: Sequence[float]) -> float:
    if not values or any(not math.isfinite(value) or value <= 0 for value in values):
        raise StudyError("geometric mean requires positive finite values")
    return math.exp(math.fsum(math.log(value) for value in values) / len(values))


def percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (position - lower) * (ordered[upper] - ordered[lower])


def bootstrap_interval(
    scene_ratios: Mapping[tuple[str, str], float],
    *,
    family: str,
    metric: str,
    salt: int,
    draws: int | None = None,
) -> dict[str, Any]:
    if draws is None:
        draws = BOOTSTRAP_DRAWS
    if type(draws) is not int or draws < 1:
        raise StudyError("bootstrap draw count must be positive")
    if family == "open":
        values = [math.log(value) for _key, value in sorted(scene_ratios.items())]
        if len(values) != 20:
            raise StudyError("open bootstrap requires 20 missions")
        grouped = None
    else:
        grouped = {}
        for (map_id, mission_id), ratio in scene_ratios.items():
            grouped.setdefault(map_id, []).append(math.log(ratio))
        if len(grouped) != 10 or any(len(values) != 2 for values in grouped.values()):
            raise StudyError(f"{family} bootstrap requires ten maps x two missions")
        values = []
    rng = random.Random(BOOTSTRAP_SEED + salt)
    samples: list[float] = []
    if grouped is None:
        for _ in range(draws):
            samples.append(math.exp(math.fsum(values[rng.randrange(20)] for _position in range(20)) / 20))
        point_logs = values
        unit = "mission-within-one-fixed-open-map"
    else:
        map_ids = sorted(grouped)
        for _ in range(draws):
            selected_logs = []
            for _position in range(10):
                mission_logs = grouped[map_ids[rng.randrange(10)]]
                selected_logs.append(
                    math.fsum(mission_logs[rng.randrange(2)] for _inner in range(2)) / 2
                )
            samples.append(math.exp(math.fsum(selected_logs) / 10))
        point_logs = [math.fsum(grouped[map_id]) / 2 for map_id in map_ids]
        unit = "map-outer-and-mission-within-map"
    return {
        "draws": draws,
        "estimate": math.exp(math.fsum(point_logs) / len(point_logs)),
        "lower_95": percentile(samples, 0.025),
        "metric": metric,
        "resampling_unit": unit,
        "seed": BOOTSTRAP_SEED + salt,
        "upper_95": percentile(samples, 0.975),
    }


def analyze_resources(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    timing = [
        record
        for record in records
        if record["phase"] == "measured" and record["run_class"] == "timing"
    ]
    if len(timing) != EXPECTED_SCENES * 2 * 3:
        raise StudyError("timing measurement inventory differs")
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for record in timing:
        grouped.setdefault((record["scene_id"], record["method"]), []).append(record)
    if len(grouped) != EXPECTED_SCENES * 2 or any(len(value) != 3 for value in grouped.values()):
        raise StudyError("timing scene/method repetitions differ")
    scene_rows: list[dict[str, Any]] = []
    scene_template = {record["scene_id"]: record for record in timing}
    metrics = ("wall_seconds", "total_cpu_seconds", "peak_rss_mib")
    for scene_id in sorted(scene_template):
        template = scene_template[scene_id]
        medians: dict[str, dict[str, float]] = {}
        for method in METHODS:
            medians[method] = {
                metric: statistics.median(
                    item["measurement"][metric] for item in grouped[(scene_id, method)]
                )
                for metric in metrics
            }
        scene_rows.append(
            {
                "family": template["family"],
                "map_id": template["map_id"],
                "mission_id": template["mission_id"],
                "optimized_medians": medians["optimized"],
                "ratios": {
                    "cpu_speedup": medians["reference"]["total_cpu_seconds"] / medians["optimized"]["total_cpu_seconds"],
                    "rss_optimized_over_reference": medians["optimized"]["peak_rss_mib"] / medians["reference"]["peak_rss_mib"],
                    "rss_reference_over_optimized": medians["reference"]["peak_rss_mib"] / medians["optimized"]["peak_rss_mib"],
                    "wall_speedup": medians["reference"]["wall_seconds"] / medians["optimized"]["wall_seconds"],
                },
                "reference_medians": medians["reference"],
                "robot_count": template["robot_count"],
                "scene_id": scene_id,
            }
        )
    cells: list[dict[str, Any]] = []
    families = inputs_module.FAMILIES
    metric_names = ("wall_speedup", "cpu_speedup", "rss_optimized_over_reference")
    salt = 0
    for family in families:
        for count in inputs_module.COUNTS[family]:
            rows = [row for row in scene_rows if row["family"] == family and row["robot_count"] == count]
            if len(rows) != 20:
                raise StudyError(f"timing cell differs: {family}/N={count}")
            intervals = {}
            for metric in metric_names:
                ratios = {(row["map_id"], row["mission_id"]): row["ratios"][metric] for row in rows}
                intervals[metric] = bootstrap_interval(
                    ratios, family=family, metric=metric, salt=salt
                )
                salt += 1
            wall = intervals["wall_speedup"]
            cpu = intervals["cpu_speedup"]
            rss = intervals["rss_optimized_over_reference"]
            cells.append(
                {
                    "cpu_improvement_gate": cpu["estimate"] > 1.05 and cpu["lower_95"] > 1.0,
                    "family": family,
                    "intervals": intervals,
                    "memory_no_increase_above_5pct_gate": rss["upper_95"] <= 1.05,
                    "robot_count": count,
                    "scene_count": len(rows),
                    "wall_improvement_gate": wall["estimate"] > 1.05 and wall["lower_95"] > 1.0,
                }
            )
    return {
        "all_cpu_cells_improve": all(row["cpu_improvement_gate"] for row in cells),
        "all_memory_cells_within_5pct": all(row["memory_no_increase_above_5pct_gate"] for row in cells),
        "all_wall_cells_improve": all(row["wall_improvement_gate"] for row in cells),
        "cell_rows": cells,
        "scene_rows": scene_rows,
        "timing_process_count": len(timing),
    }


def write_outputs(output_root: pathlib.Path, analysis: Mapping[str, Any]) -> None:
    (output_root / "analysis.json").write_bytes(canonical_json_bytes(analysis))
    with (output_root / "analysis.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(
            [
                "family",
                "robot_count",
                "scene_count",
                "wall_speedup",
                "wall_lower_95",
                "wall_upper_95",
                "cpu_speedup",
                "cpu_lower_95",
                "cpu_upper_95",
                "rss_optimized_over_reference",
                "rss_lower_95",
                "rss_upper_95",
                "wall_gate",
                "cpu_gate",
                "rss_within_5pct_gate",
            ]
        )
        for row in analysis["resources"]["cell_rows"]:
            wall = row["intervals"]["wall_speedup"]
            cpu = row["intervals"]["cpu_speedup"]
            rss = row["intervals"]["rss_optimized_over_reference"]
            writer.writerow(
                [
                    row["family"],
                    row["robot_count"],
                    row["scene_count"],
                    wall["estimate"],
                    wall["lower_95"],
                    wall["upper_95"],
                    cpu["estimate"],
                    cpu["lower_95"],
                    cpu["upper_95"],
                    rss["estimate"],
                    rss["lower_95"],
                    rss["upper_95"],
                    row["wall_improvement_gate"],
                    row["cpu_improvement_gate"],
                    row["memory_no_increase_above_5pct_gate"],
                ]
            )
    lines = [
        "# DANCeRS prospective multi-map study v1 findings",
        "",
        f"Host ID: `{analysis['host_id_sha256'][:16]}` (pseudonymous).",
        "",
        f"All {analysis['inventory']['processes']} scheduled simulator processes completed and were retained.",
        f"Exact semantic parity passed for {analysis['fidelity']['digest_pair_count']} digest pairs and {analysis['fidelity']['positions_pair_count']} position pairs.",
        f"Independent swept-geometry analysis classified {analysis['fidelity']['task_valid_scene_count']}/200 scenes as task-valid; invalid scenes remain in the resource analysis.",
        "",
        "## Resource results",
        "",
        "| family | N | wall speedup (95% interval) | CPU speedup (95% interval) | optimized/reference RSS (95% interval) |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in analysis["resources"]["cell_rows"]:
        wall = row["intervals"]["wall_speedup"]
        cpu = row["intervals"]["cpu_speedup"]
        rss = row["intervals"]["rss_optimized_over_reference"]
        lines.append(
            f"| {row['family']} | {row['robot_count']} | {wall['estimate']:.4f}x "
            f"[{wall['lower_95']:.4f}, {wall['upper_95']:.4f}] | "
            f"{cpu['estimate']:.4f}x [{cpu['lower_95']:.4f}, {cpu['upper_95']:.4f}] | "
            f"{rss['estimate']:.4f} [{rss['lower_95']:.4f}, {rss['upper_95']:.4f}] |"
        )
    lines.extend(
        [
            "",
            "Wall and CPU speedups are reference/optimized; values above one favour the optimized implementation.",
            "RSS is optimized/reference; the preregistered secondary gate is an upper 95% bound no greater than 1.05, not a memory-reduction claim.",
            "Stochastic-family intervals use a two-stage map-then-mission bootstrap. Open is one deterministic map and its interval resamples missions only; it does not support open-map population generalization.",
            "No task-invalid scene, timing repetition, or unfavourable resource value was removed.",
            "",
        ]
    )
    (output_root / "FINDINGS.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def analyze_session(output_root: pathlib.Path, input_root: pathlib.Path) -> dict[str, Any]:
    output_root = output_root.resolve()
    input_root = input_root.resolve()
    registered = inputs_module.load_and_validate(input_root)
    load_build_manifest(output_root, input_root)
    session_inputs, schedule, records = read_session(output_root)
    if canonical_json_bytes(session_inputs) != canonical_json_bytes(registered):
        raise StudyError("session input manifest differs from registered inputs")
    fidelity = analyze_fidelity(output_root, input_root, records)
    if not fidelity["all_semantically_equal"]:
        raise StudyError("semantic fidelity gate failed")
    resources = analyze_resources(records)
    initial = _read_canonical_json(output_root / "host_initial.json", "initial host")
    analysis = {
        "classification": {
            "cross_machine_replication": False,
            "fixed_work_resource_evidence": True,
            "open_map_population_generalization": False,
            "result_preserving_on_tested_scenes": fidelity["all_semantically_equal"],
            "stochastic_map_population_sample": True,
        },
        "fidelity": fidelity,
        "host_id_sha256": initial["host_id_sha256"],
        "inventory": {
            "maps": EXPECTED_MAPS,
            "measured": EXPECTED_MEASURED,
            "processes": len(records),
            "scenes": EXPECTED_SCENES,
            "warmups": EXPECTED_WARMUPS,
        },
        "resources": resources,
        "schema_version": SCHEMA,
        "study_id": STUDY_ID,
    }
    write_outputs(output_root, analysis)
    return analysis


def create_capsule(destination: pathlib.Path) -> pathlib.Path:
    destination = destination.resolve()
    if destination.exists() or destination.is_symlink():
        raise StudyError(f"capsule destination already exists: {destination}")
    staging = destination.with_name(destination.name + ".staging")
    if staging.exists() or staging.is_symlink():
        raise StudyError(f"capsule staging path already exists: {staging}")
    staging.mkdir(parents=True, exist_ok=False)
    try:
        bundle = staging / "Beyond-Dancers.bundle"
        completed = subprocess.run(
            ["git", "bundle", "create", str(bundle), "--all"],
            cwd=REPO,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode:
            raise StudyError(completed.stderr.decode("utf-8", "replace"))
        head = git_text("rev-parse", "HEAD")
        readme = f"""# DANCeRS second-host replication capsule

This capsule transfers the complete Git object graph.  It does not transfer
the first host's binaries: a valid second-host result must rebuild them.

Target for v1: a physically distinct Apple-silicon Mac on AC power.

1. `git clone Beyond-Dancers.bundle Beyond-Dancers`
2. `cd Beyond-Dancers`
3. `git checkout {head}`
4. Install/activate Pixi and run `pixi install --locked`.
5. Run the documented `prepare`, `build`, `run`, and `analyze` commands in
   `docs/publication/DANCERS_MULTIMAP_REPLICATION_STUDY_V1.md`.
6. Return the compact result files plus the checksummed raw session.  Do not
   pool absolute times across machines; analyze each host separately.
"""
        (staging / "README.md").write_text(readme, encoding="utf-8", newline="\n")
        manifest = {
            "bundle": file_identity(bundle, relative_to=staging),
            "created_unix_ns": time.time_ns(),
            "head": head,
            "readme": file_identity(staging / "README.md", relative_to=staging),
            "schema_version": "dancers-second-host-capsule-v1",
            "study_id": STUDY_ID,
        }
        (staging / "capsule_manifest.json").write_bytes(canonical_json_bytes(manifest))
        staging.rename(destination)
        return destination
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--input-root", type=pathlib.Path, required=True)
    build.add_argument("--build-root", type=pathlib.Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--input-root", type=pathlib.Path, required=True)
    run.add_argument("--build-root", type=pathlib.Path, required=True)
    run.add_argument("--output-root", type=pathlib.Path, required=True)
    analyze = sub.add_parser("analyze")
    analyze.add_argument("--input-root", type=pathlib.Path, required=True)
    analyze.add_argument("--output-root", type=pathlib.Path, required=True)
    capsule = sub.add_parser("capsule")
    capsule.add_argument("--destination", type=pathlib.Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "build":
            build_products(args.input_root, args.build_root)
        elif args.command == "run":
            execute(args.input_root, args.build_root, args.output_root)
        elif args.command == "analyze":
            analyze_session(args.output_root, args.input_root)
        else:
            create_capsule(args.destination)
        return 0
    except (
        StudyError,
        inputs_module.InputError,
        geometry.GeometryError,
        trace_compare.TraceFormatError,
        OSError,
        ValueError,
        KeyError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as error:
        print(f"multi-map study failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
