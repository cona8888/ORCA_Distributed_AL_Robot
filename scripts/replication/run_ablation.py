#!/usr/bin/env python3
"""Build, run, and analyze the prospective DANCeRS cumulative ablation v1."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import os
import pathlib
import random
import resource
import shutil
import statistics
import subprocess
import sys
import tarfile
import time
from typing import Optional


SCHEMA = "dancers-cumulative-ablation-v1"
CONFIG_REL = "config/ablation/sparse_consensus_seed1.yaml"
CONFIG_SHA256 = "c48107956401bf912d24a587d94a6cd256c18256c2edf82007c103b987e33d2e"
LOCK_SHA256 = "636ecf1cf31431ffc19f453fef39299c3243718477e321fcea8814225bfe68be"
MANIF_REL = "thirdparty/dist/manif-0.0.5-build-source.tar.gz"
MANIF_SHA256 = "3d3a13e93384348946c689ebd4e818bbb9f72cbcc3cdafecb7b531f7d5191715"
RAYLIB_REL = "thirdparty/dist/raylib-5.5-build-source.tar.gz"
RAYLIB_SHA256 = "9758adff979fe36aa846897ecfd14e6d88ea13df87e9862b770d36b854717709"
COUNTS = (500, 1000)
SEEDS = tuple(range(1, 11))
REPETITIONS = 3
MEASURED_BLOCKS = len(SEEDS) * REPETITIONS
BOOTSTRAP_SAMPLES = 50_000
BOOTSTRAP_SEED = 20_260_824
COOLDOWN_SECONDS = 2.0

STAGES = (
    {
        "id": "S0",
        "commit": "986f47deec5b079663c7d7f6120bfc20f0467901",
        "tree": "b564979ee493293a4208116ee7c1c05bd69307f4",
        "label": "reference",
    },
    {
        "id": "S1",
        "commit": "4c653c1fb2aa1317b6ead76f2fab5064f84c1e8b",
        "tree": "9f1310ca94c7f9b9a76d098d57d813be21c9eb63",
        "label": "contiguous-kd-layout",
    },
    {
        "id": "S2",
        "commit": "aa58cf8d3a5cb9437843d04b61aa4f8c2f962c52",
        "tree": "37e24e1ee446d84a225278b0696133604015e36c",
        "label": "topology-reuse",
    },
    {
        "id": "S3",
        "commit": "ba036208812b840617557abf7a3621838a76ef74",
        "tree": "2a5bec3c58787de9e632531e5e9005d92f0107e8",
        "label": "gbp-inner-loop-reuse",
    },
    {
        "id": "S4",
        "commit": "51d7ef6b6c10f60bbab60c035bd39ca7b7400b2a",
        "tree": "eade13828cc54526851c1c4cb31af2f8792ba413",
        "label": "scalar-r1-specialization",
    },
    {
        "id": "S5",
        "commit": "870898dafbadde0e8763e95a18a061d7f3e7f701",
        "tree": "fc6efbe4f2e6c2e58b805a45e570932db74de9ae",
        "label": "single-owner-external-moves",
    },
)


class AblationError(RuntimeError):
    pass


def repository_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def canonical_json_bytes(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def require_file(path: pathlib.Path, expected_sha256: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise AblationError(f"required regular file missing: {path}")
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise AblationError(f"identity mismatch for {path}: {actual}")


def run_checked(argv: list[str], *, cwd: pathlib.Path, env: dict[str, str], log: pathlib.Path) -> None:
    started = time.monotonic_ns()
    completed = subprocess.run(argv, cwd=str(cwd), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    elapsed = time.monotonic_ns() - started
    header = canonical_json_bytes({"argv": argv, "cwd": str(cwd), "elapsed_ns": elapsed, "returncode": completed.returncode})
    log.write_bytes(header + completed.stdout)
    if completed.returncode != 0:
        raise AblationError(f"command failed ({completed.returncode}); see {log}")


def safe_extract(raw: bytes, destination: pathlib.Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    root = destination.resolve()
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
        members = archive.getmembers()
        for member in members:
            candidate = (destination / member.name).resolve()
            if candidate != root and root not in candidate.parents:
                raise AblationError(f"unsafe archive member: {member.name}")
            if member.issym() or member.islnk() or member.isdev():
                raise AblationError(f"unsupported archive member: {member.name}")
        archive.extractall(destination)


def extract_single_root(archive_path: pathlib.Path, destination: pathlib.Path) -> pathlib.Path:
    safe_extract(archive_path.read_bytes(), destination)
    children = sorted(destination.iterdir())
    if len(children) != 1 or not children[0].is_dir():
        raise AblationError(f"dependency archive lacks one root: {archive_path}")
    return children[0]


def tool_paths(repo: pathlib.Path) -> dict[str, pathlib.Path]:
    env_root = repo / ".pixi" / "envs" / "default"
    paths = {
        "cmake": env_root / "bin" / "cmake",
        "ninja": env_root / "bin" / "ninja",
        "clang": env_root / "bin" / "clang",
        "clangxx": env_root / "bin" / "clang++",
        "libomp": env_root / "lib" / "libomp.dylib",
    }
    for path in paths.values():
        if not path.is_file():
            raise AblationError(f"toolchain member missing: {path}")
    return paths


def build_environment(repo: pathlib.Path, tools: dict[str, pathlib.Path]) -> dict[str, str]:
    env_root = repo / ".pixi" / "envs" / "default"
    return {
        "PATH": f"{env_root / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
        "CC": str(tools["clang"]),
        "CXX": str(tools["clangxx"]),
        "CONDA_PREFIX": str(env_root),
        "CMAKE_PREFIX_PATH": str(env_root),
        "OMP_WAIT_POLICY": "passive",
        "LC_ALL": "C",
        "LANG": "C",
        "TMPDIR": "/private/tmp",
    }


def git_text(repo: pathlib.Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode != 0:
        raise AblationError(completed.stderr.decode("utf-8", "replace"))
    return completed.stdout.decode("utf-8").strip()


def validate_frozen_inputs(repo: pathlib.Path) -> dict[str, pathlib.Path]:
    require_file(repo / CONFIG_REL, CONFIG_SHA256)
    require_file(repo / "pixi.lock", LOCK_SHA256)
    require_file(repo / MANIF_REL, MANIF_SHA256)
    require_file(repo / RAYLIB_REL, RAYLIB_SHA256)
    for stage in STAGES:
        commit = git_text(repo, "rev-parse", f"{stage['commit']}^{{commit}}")
        tree = git_text(repo, "rev-parse", f"{stage['commit']}^{{tree}}")
        if commit != stage["commit"] or tree != stage["tree"]:
            raise AblationError(f"Git identity mismatch for {stage['id']}")
        lock = subprocess.run(
            ["git", "show", f"{stage['commit']}:pixi.lock"], cwd=str(repo), stdout=subprocess.PIPE, check=True
        ).stdout
        if sha256_bytes(lock) != LOCK_SHA256:
            raise AblationError(f"lock mismatch in {stage['id']}")
    return tool_paths(repo)


def build_all(build_root: pathlib.Path) -> None:
    repo = repository_root()
    tools = validate_frozen_inputs(repo)
    if build_root.exists() or build_root.is_symlink():
        raise AblationError(f"build root already exists: {build_root}")
    build_root.mkdir(parents=True, exist_ok=False)
    logs = build_root / "logs"
    logs.mkdir()
    deps = build_root / "deps"
    deps.mkdir()
    manif = extract_single_root(repo / MANIF_REL, deps / "manif")
    raylib = extract_single_root(repo / RAYLIB_REL, deps / "raylib")
    env = build_environment(repo, tools)
    tool_receipts = {}
    for name, path in tools.items():
        tool_receipts[name] = {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}

    built = []
    for stage in STAGES:
        stage_root = build_root / stage["id"]
        source = stage_root / "source"
        build = stage_root / "build"
        stage_root.mkdir()
        archived = subprocess.run(
            ["git", "archive", "--format=tar", stage["commit"]], cwd=str(repo), stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        if archived.returncode != 0:
            raise AblationError(archived.stderr.decode("utf-8", "replace"))
        safe_extract(archived.stdout, source)
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
            f"-DCMAKE_PREFIX_PATH={repo / '.pixi' / 'envs' / 'default'}",
            f"-DOpenMP_omp_LIBRARY={tools['libomp']}",
            f"-DFETCHCONTENT_SOURCE_DIR_MANIF={manif}",
            f"-DFETCHCONTENT_SOURCE_DIR_RAYLIB={raylib}",
            "-DBUILD_TESTING=OFF",
        ]
        run_checked(argv, cwd=stage_root, env=env, log=logs / f"{stage['id']}-configure.log")
        run_checked(
            [str(tools["cmake"]), "--build", str(build), "--target", "dancers", "--parallel", "8"],
            cwd=stage_root,
            env=env,
            log=logs / f"{stage['id']}-build.log",
        )
        binary = build / "dancers"
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise AblationError(f"binary missing for {stage['id']}")
        built.append(
            {
                **stage,
                "source_archive_sha256": sha256_bytes(archived.stdout),
                "binary": str(binary),
                "binary_bytes": binary.stat().st_size,
                "binary_sha256": sha256_file(binary),
            }
        )
    manifest = {
        "schema_version": SCHEMA,
        "kind": "build-manifest",
        "repository": str(repo),
        "config": {"path": CONFIG_REL, "sha256": CONFIG_SHA256},
        "lock_sha256": LOCK_SHA256,
        "dependencies": {
            MANIF_REL: MANIF_SHA256,
            RAYLIB_REL: RAYLIB_SHA256,
        },
        "tools": tool_receipts,
        "stages": built,
    }
    (build_root / "build_manifest.json").write_bytes(canonical_json_bytes(manifest))


def schedule_records() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    sequence = 0
    for stage in STAGES:
        for count in COUNTS:
            records.append(
                {
                    "sequence": sequence,
                    "phase": "warmup",
                    "block": None,
                    "stage": stage["id"],
                    "count": count,
                    "seed": 1,
                    "repetition": None,
                }
            )
            sequence += 1
    stage_ids = [stage["id"] for stage in STAGES]
    block = 0
    for seed in SEEDS:
        for repetition in range(REPETITIONS):
            offset = block % len(stage_ids)
            order = stage_ids[offset:] + stage_ids[:offset]
            counts = list(COUNTS if block % 2 == 0 else tuple(reversed(COUNTS)))
            for stage_id in order:
                for count in counts:
                    records.append(
                        {
                            "sequence": sequence,
                            "phase": "measured",
                            "block": block,
                            "stage": stage_id,
                            "count": count,
                            "seed": seed,
                            "repetition": repetition,
                        }
                    )
                    sequence += 1
            block += 1
    return records


def config_argument(repo: pathlib.Path, source: pathlib.Path) -> str:
    """Return the same authenticated config through the legacy whitespace-safe CLI."""
    argument = os.path.relpath(repo / CONFIG_REL, source)
    if any(character.isspace() for character in argument):
        raise AblationError("legacy config argument contains whitespace")
    if (source / argument).resolve() != (repo / CONFIG_REL).resolve():
        raise AblationError("legacy config argument resolves to the wrong file")
    return argument


def validate_fixed_work_stdout(stdout: bytes, count: int) -> None:
    required = (
        b"timesteps:  100 ",
        b"Final iterations: [100]",
        f"Final robot count: [{count}]".encode(),
        b"Test complete",
    )
    if any(fragment not in stdout for fragment in required):
        raise AblationError("fixed-work log gate failed")


def host_observation() -> dict[str, object]:
    def command(argv: list[str]) -> dict[str, object]:
        completed = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return {"argv": argv, "returncode": completed.returncode, "output": completed.stdout.decode("utf-8", "replace")}

    return {
        "time_unix_ns": time.time_ns(),
        "sw_vers": command(["/usr/bin/sw_vers"]),
        "pmset_battery": command(["/usr/bin/pmset", "-g", "batt"]),
        "pmset_thermal": command(["/usr/bin/pmset", "-g", "therm"]),
        "uname": command(["/usr/bin/uname", "-a"]),
        "free_bytes": shutil.disk_usage(repository_root()).free,
    }


def require_ac(observation: dict[str, object]) -> None:
    battery = observation["pmset_battery"]
    if not isinstance(battery, dict) or battery.get("returncode") != 0 or "AC Power" not in str(battery.get("output", "")):
        raise AblationError("measured execution requires AC power")


def run_one(
    *,
    repo: pathlib.Path,
    build_manifest: dict[str, object],
    output_root: pathlib.Path,
    record: dict[str, object],
) -> dict[str, object]:
    stage_by_id = {stage["id"]: stage for stage in build_manifest["stages"]}
    stage = stage_by_id[record["stage"]]
    binary = pathlib.Path(stage["binary"])
    if sha256_file(binary) != stage["binary_sha256"]:
        raise AblationError(f"binary changed before {record['sequence']}")
    attempt = output_root / "attempts" / f"{int(record['sequence']):03d}-{record['phase']}-{record['stage']}-n{record['count']}"
    attempt.mkdir(parents=True, exist_ok=False)
    stdout_path = attempt / "stdout.txt"
    stderr_path = attempt / "stderr.txt"
    source = pathlib.Path(stage["binary"]).parents[1] / "source"
    argv = [
        str(binary),
        "--cfg",
        config_argument(repo, source),
        "--set",
        f"NUM_ROBOTS={record['count']};MAX_TIME=100;RNG_SEED={record['seed']}",
    ]
    env_root = repo / ".pixi" / "envs" / "default"
    env = {
        "PATH": f"{env_root / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
        "DYLD_LIBRARY_PATH": str(env_root / "lib"),
        "OMP_NUM_THREADS": "1",
        "OMP_WAIT_POLICY": "passive",
        "LC_ALL": "C",
        "LANG": "C",
        "TMPDIR": "/private/tmp",
    }
    started_wall = time.monotonic_ns()
    started_unix = time.time_ns()
    with stdout_path.open("wb") as stdout_handle, stderr_path.open("wb") as stderr_handle:
        process = subprocess.Popen(argv, cwd=str(source), env=env, stdout=stdout_handle, stderr=stderr_handle)
        pid, status, usage = os.wait4(process.pid, 0)
        if pid != process.pid:
            raise AblationError("wait4 returned the wrong child")
        process.returncode = os.waitstatus_to_exitcode(status)
    ended_wall = time.monotonic_ns()
    stdout = stdout_path.read_bytes()
    stderr = stderr_path.read_bytes()
    try:
        validate_fixed_work_stdout(stdout, int(record["count"]))
    except AblationError as exc:
        raise AblationError(f"{exc} for sequence {record['sequence']}") from exc
    if process.returncode != 0:
        raise AblationError(f"simulator failed for sequence {record['sequence']}: {process.returncode}")
    result = {
        **record,
        "argv": argv,
        "started_unix_ns": started_unix,
        "wall_seconds": (ended_wall - started_wall) / 1e9,
        "user_cpu_seconds": usage.ru_utime,
        "system_cpu_seconds": usage.ru_stime,
        "total_cpu_seconds": usage.ru_utime + usage.ru_stime,
        "max_rss_raw": usage.ru_maxrss,
        "returncode": process.returncode,
        "stdout": {
            "path": str(stdout_path.relative_to(output_root)),
            "bytes": len(stdout),
            "sha256": sha256_bytes(stdout),
        },
        "stderr": {
            "path": str(stderr_path.relative_to(output_root)),
            "bytes": len(stderr),
            "sha256": sha256_bytes(stderr),
        },
        "binary_sha256": stage["binary_sha256"],
        "commit": stage["commit"],
        "tree": stage["tree"],
    }
    for key in ("wall_seconds", "user_cpu_seconds", "total_cpu_seconds"):
        value = result[key]
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise AblationError(f"invalid {key} for sequence {record['sequence']}")
    return result


def execute(build_root: pathlib.Path, output_root: pathlib.Path) -> None:
    repo = repository_root()
    validate_frozen_inputs(repo)
    build_manifest_path = build_root / "build_manifest.json"
    if not build_manifest_path.is_file():
        raise AblationError(f"build manifest missing: {build_manifest_path}")
    build_manifest = json.loads(build_manifest_path.read_text())
    if build_manifest.get("schema_version") != SCHEMA or build_manifest.get("kind") != "build-manifest":
        raise AblationError("build manifest schema mismatch")
    if output_root.exists() or output_root.is_symlink():
        raise AblationError(f"output root already exists: {output_root}")
    output_root.mkdir(parents=True, exist_ok=False)
    (output_root / "attempts").mkdir()
    shutil.copyfile(build_manifest_path, output_root / "build_manifest.json")
    schedule = schedule_records()
    (output_root / "schedule.json").write_bytes(
        canonical_json_bytes(
            {
                "schema_version": SCHEMA,
                "records": schedule,
                "measured_blocks": MEASURED_BLOCKS,
                "cooldown_seconds": COOLDOWN_SECONDS,
            }
        )
    )
    initial_host = host_observation()
    require_ac(initial_host)
    (output_root / "host_initial.json").write_bytes(canonical_json_bytes(initial_host))
    runs_path = output_root / "runs.jsonl"
    with runs_path.open("xb") as ledger:
        previous_block = None
        for record in schedule:
            if record["phase"] == "measured" and record["block"] != previous_block:
                if previous_block is not None:
                    time.sleep(COOLDOWN_SECONDS)
                observation = host_observation()
                require_ac(observation)
                with (output_root / "host_blocks.jsonl").open("ab") as host_ledger:
                    host_ledger.write(canonical_json_bytes({"block": record["block"], "observation": observation}))
                previous_block = record["block"]
            result = run_one(repo=repo, build_manifest=build_manifest, output_root=output_root, record=record)
            ledger.write(canonical_json_bytes(result))
            ledger.flush()
            os.fsync(ledger.fileno())
    final = host_observation()
    (output_root / "host_final.json").write_bytes(canonical_json_bytes(final))


def read_runs(output_root: pathlib.Path) -> list[dict[str, object]]:
    path = output_root / "runs.jsonl"
    raw_lines = path.read_bytes().splitlines(keepends=True)
    records = []
    for index, raw in enumerate(raw_lines):
        value = json.loads(raw)
        if raw != canonical_json_bytes(value):
            raise AblationError(f"noncanonical run record {index}")
        records.append(value)
    expected = schedule_records()
    if len(records) != len(expected):
        raise AblationError(f"run inventory differs: {len(records)} != {len(expected)}")
    for actual, planned in zip(records, expected):
        for key in ("sequence", "phase", "block", "stage", "count", "seed", "repetition"):
            if actual.get(key) != planned.get(key):
                raise AblationError(f"run {actual.get('sequence')} differs from schedule")
        for stream in ("stdout", "stderr"):
            identity = actual[stream]
            path = output_root / identity["path"]
            if path.stat().st_size != identity["bytes"] or sha256_file(path) != identity["sha256"]:
                raise AblationError(f"{stream} identity mismatch for {actual['sequence']}")
    return records


def geometric_mean(values: list[float]) -> float:
    if not values or any((not math.isfinite(value) or value <= 0) for value in values):
        raise AblationError("geometric mean requires positive finite values")
    # ``sum`` changed its float summation algorithm between supported Python
    # runtimes.  ``fsum`` keeps the serialized analysis byte-identical while
    # also reducing round-off in the aggregate.
    return math.exp(math.fsum(math.log(value) for value in values) / len(values))


def percentile(sorted_values: list[float], probability: float) -> float:
    index = (len(sorted_values) - 1) * probability
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return sorted_values[lower]
    fraction = index - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def bootstrap_interval(ratios: list[float], *, salt: int) -> dict[str, float]:
    rng = random.Random(BOOTSTRAP_SEED + salt)
    samples = []
    for _ in range(BOOTSTRAP_SAMPLES):
        resample = [ratios[rng.randrange(len(ratios))] for _ in ratios]
        samples.append(geometric_mean(resample))
    samples.sort()
    return {
        "estimate": geometric_mean(ratios),
        "lower_95": percentile(samples, 0.025),
        "upper_95": percentile(samples, 0.975),
    }


def analyze(output_root: pathlib.Path) -> dict[str, object]:
    records = read_runs(output_root)
    measured = [record for record in records if record["phase"] == "measured"]
    for count in COUNTS:
        for seed in SEEDS:
            hashes = {
                record["stdout"]["sha256"]
                for record in records
                if record["count"] == count and record["seed"] == seed
            }
            if len(hashes) != 1:
                raise AblationError(
                    f"stdout fidelity differs across stages/runs at N={count}, seed={seed}: {sorted(hashes)}"
                )
    stage_summaries = []
    for count in COUNTS:
        for stage in STAGES:
            cell = [record for record in measured if record["count"] == count and record["stage"] == stage["id"]]
            if len(cell) != len(SEEDS) * REPETITIONS:
                raise AblationError(f"incomplete cell {stage['id']} N={count}")
            seed_medians = {}
            for seed in SEEDS:
                seed_cell = [record for record in cell if record["seed"] == seed]
                if len(seed_cell) != REPETITIONS:
                    raise AblationError(f"incomplete seed cell {stage['id']} N={count} seed={seed}")
                seed_medians[seed] = {
                    metric: statistics.median(record[metric] for record in seed_cell)
                    for metric in ("wall_seconds", "user_cpu_seconds", "total_cpu_seconds", "max_rss_raw")
                }
            stage_summaries.append(
                {
                    "count": count,
                    "stage": stage["id"],
                    "label": stage["label"],
                    "n_seeds": len(SEEDS),
                    "repetitions_per_seed": REPETITIONS,
                    "wall_geomean_seconds": geometric_mean([seed_medians[seed]["wall_seconds"] for seed in SEEDS]),
                    "user_cpu_geomean_seconds": geometric_mean([seed_medians[seed]["user_cpu_seconds"] for seed in SEEDS]),
                    "total_cpu_geomean_seconds": geometric_mean([seed_medians[seed]["total_cpu_seconds"] for seed in SEEDS]),
                    "max_rss_geomean_raw": geometric_mean([seed_medians[seed]["max_rss_raw"] for seed in SEEDS]),
                    "max_rss_geomean_mib": geometric_mean(
                        [seed_medians[seed]["max_rss_raw"] for seed in SEEDS]
                    )
                    / (1024.0 * 1024.0),
                    "seed_medians": seed_medians,
                    "wall_values_seconds": [record["wall_seconds"] for record in cell],
                    "total_cpu_values_seconds": [record["total_cpu_seconds"] for record in cell],
                    "max_rss_values_raw": [record["max_rss_raw"] for record in cell],
                }
            )
    comparisons = []
    pairs = [(STAGES[index - 1], STAGES[index]) for index in range(1, len(STAGES))]
    pairs.append((STAGES[0], STAGES[-1]))
    for count_index, count in enumerate(COUNTS):
        by_key = {
            (record["seed"], record["repetition"], record["stage"]): record
            for record in measured
            if record["count"] == count
        }
        for pair_index, (before, after) in enumerate(pairs):
            comparison: dict[str, object] = {
                "count": count,
                "before": before["id"],
                "after": after["id"],
                "kind": "cumulative" if before["id"] == "S0" and after["id"] == "S5" else "adjacent",
            }
            for metric_index, metric in enumerate(("wall_seconds", "user_cpu_seconds", "total_cpu_seconds", "max_rss_raw")):
                ratios = []
                for seed in SEEDS:
                    before_median = statistics.median(
                        by_key[(seed, repetition, before["id"])][metric] for repetition in range(REPETITIONS)
                    )
                    after_median = statistics.median(
                        by_key[(seed, repetition, after["id"])][metric] for repetition in range(REPETITIONS)
                    )
                    ratios.append(before_median / after_median)
                comparison[metric.replace("_seconds", "") + "_ratio"] = {
                    **bootstrap_interval(ratios, salt=count_index * 100 + pair_index * 10 + metric_index),
                    "seed_ratios": ratios,
                }
            comparisons.append(comparison)
    analysis = {
        "schema_version": SCHEMA,
        "kind": "analysis",
        "classification": {
            "cumulative_order_dependent_ablation": True,
            "order_independent_effect_claim": False,
            "modern_obstacle_planning_ablation": False,
            "max_rss_raw_unit": "bytes-on-darwin",
        },
        "inventory": {
            "warmups": len(records) - len(measured),
            "measured": len(measured),
            "blocks": MEASURED_BLOCKS,
            "seeds": list(SEEDS),
            "repetitions_per_seed": REPETITIONS,
            "stages": len(STAGES),
            "counts": list(COUNTS),
        },
        "stage_summaries": stage_summaries,
        "comparisons": comparisons,
    }
    (output_root / "analysis.json").write_bytes(canonical_json_bytes(analysis))
    with (output_root / "analysis.csv").open("w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["count", "stage", "label", "n_seeds", "repetitions_per_seed", "wall_geomean_seconds", "user_cpu_geomean_seconds", "total_cpu_geomean_seconds", "max_rss_geomean_raw", "max_rss_geomean_mib"])
        for row in stage_summaries:
            writer.writerow([row[key] for key in ("count", "stage", "label", "n_seeds", "repetitions_per_seed", "wall_geomean_seconds", "user_cpu_geomean_seconds", "total_cpu_geomean_seconds", "max_rss_geomean_raw", "max_rss_geomean_mib")])
    write_findings(output_root, analysis)
    return analysis


def write_findings(output_root: pathlib.Path, analysis: dict[str, object]) -> None:
    lines = [
        "# DANCeRS cumulative optimization ablation v1 findings",
        "",
        "This is a cumulative, order-dependent ablation of the exact historical sparse-workload source chain.",
        "It is not an ablation of the modern obstacle-planning runtime and does not assign order-independent causal effects.",
        "",
        "## Stage timing",
        "",
        "| N | stage | optimization state | wall geomean (s) | total CPU geomean (s) | max RSS (MiB) |",
        "|---:|---|---|---:|---:|---:|",
    ]
    for row in analysis["stage_summaries"]:
        lines.append(
            f"| {row['count']} | {row['stage']} | {row['label']} | {row['wall_geomean_seconds']:.6f} | "
            f"{row['total_cpu_geomean_seconds']:.6f} | {row['max_rss_geomean_mib']:.2f} |"
        )
    lines.extend(["", "## Paired speedups", "", "| N | comparison | kind | wall speedup | 95% interval | total CPU speedup |", "|---:|---|---|---:|---:|---:|"])
    for row in analysis["comparisons"]:
        wall = row["wall_ratio"]
        cpu = row["total_cpu_ratio"]
        lines.append(
            f"| {row['count']} | {row['before']} -> {row['after']} | {row['kind']} | {wall['estimate']:.4f} | "
            f"[{wall['lower_95']:.4f}, {wall['upper_95']:.4f}] | {cpu['estimate']:.4f} |"
        )
    lines.extend(
        [
            "",
            "All raw runs, schedule records, build identities, stdout/stderr bytes, and seed-level ratios are retained beside this document.",
            "A ratio above 1 favours the later stage; a ratio below 1 is a measured regression and is not removed.",
            "Wall and CPU timings cover the complete simulator process lifetime, including initialization; they are not solver-kernel-only timings.",
            "Exact stdout equality establishes equality of the deterministic printed summary, not equality of unobserved internal state.",
            "",
        ]
    )
    (output_root / "FINDINGS.md").write_text("\n".join(lines))


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--build-root", type=pathlib.Path, required=True)
    run = sub.add_parser("run")
    run.add_argument("--build-root", type=pathlib.Path, required=True)
    run.add_argument("--output-root", type=pathlib.Path, required=True)
    analyze_parser = sub.add_parser("analyze")
    analyze_parser.add_argument("--output-root", type=pathlib.Path, required=True)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.command == "prepare":
            build_all(args.build_root.resolve())
        elif args.command == "run":
            execute(args.build_root.resolve(), args.output_root.resolve())
        else:
            analyze(args.output_root.resolve())
    except (AblationError, OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
        print(f"ablation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
