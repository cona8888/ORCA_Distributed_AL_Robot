#!/usr/bin/env python3
"""Publish the prospective DANCeRS multi-map study inputs.

This is an input compiler only.  It never launches the simulator and never
reads a result directory.  The output is byte deterministic and create-only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import shutil
import sys
from typing import Any, Sequence

_REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from scripts.comparison import generate_environments as environments
from scripts.comparison import generate_headless_mission_banks_v1 as missions


SCHEMA = "dancers-multimap-input-manifest-v1"
STUDY_ID = "dancers-multimap-v1"
OUTPUT_RELATIVE = pathlib.PurePosixPath(
    "comparison/publication/dancers-multimap-v1"
)
MAP_SEEDS = (
    1124288754611687508,
    5143576881131483611,
    364023683178661252,
    5756175215563340043,
    7628420574272008056,
    4761703837298867603,
    4492167233608332407,
    6254029921437523734,
    7378728135909974893,
    1424198907603555106,
)
FAMILIES = (
    "open",
    "perfect_maze",
    "fractal_low",
    "fractal_medium",
    "fractal_high",
)
COUNTS = {
    "open": (8,),
    "perfect_maze": (2, 4, 6),
    "fractal_low": (2, 4),
    "fractal_medium": (2, 4),
    "fractal_high": (2, 4),
}
MISSION_LABELS = tuple(range(31, 51))
MISSION_DOMAIN = b"dancers-publication-multimap-mission-v1\0"
FIXED_TIMESTEPS = 100
RUNTIME_SEED_RULE = "1 + ((mission_label - 31) mod 10)"


class InputError(RuntimeError):
    pass


def repository_root() -> pathlib.Path:
    return _REPOSITORY_ROOT


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            indent=2,
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


def mission_generator_seed(
    family: str, map_id: str, map_sha256: str, mission_label: int
) -> int:
    if family not in FAMILIES:
        raise InputError("unknown family in mission seed")
    if not map_id or not 31 <= mission_label <= 50:
        raise InputError("invalid map or mission label")
    material = b"\0".join(
        (
            MISSION_DOMAIN,
            family.encode("ascii"),
            map_id.encode("ascii"),
            map_sha256.encode("ascii"),
            str(mission_label).encode("ascii"),
        )
    )
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def runtime_seed(mission_label: int) -> int:
    if mission_label not in MISSION_LABELS:
        raise InputError("mission label is outside 31..50")
    return 1 + ((mission_label - 31) % 10)


def map_specs() -> list[dict[str, Any]]:
    specs = [
        {
            "family": "open",
            "map_id": "canonical",
            "map_index": 0,
            "map_seed": MAP_SEEDS[0],
            "mission_labels": MISSION_LABELS,
        }
    ]
    for family in FAMILIES[1:]:
        for index, seed in enumerate(MAP_SEEDS, start=1):
            start = 31 + 2 * (index - 1)
            specs.append(
                {
                    "family": family,
                    "map_id": f"map-{index:02d}",
                    "map_index": index,
                    "map_seed": seed,
                    "mission_labels": (start, start + 1),
                }
            )
    return specs


def _generate_robots(
    *,
    family: str,
    map_id: str,
    map_sha256: str,
    pixels: bytes,
    mission_label: int,
    maximum_count: int,
) -> tuple[int, list[dict[str, Any]]]:
    """Use the frozen headless mission geometry with a new seed domain."""

    safe = missions.clearance_mask(
        pixels, missions.MAP_SIZE_PX, missions.CLEARANCE_RADIUS_PX
    )
    candidates = tuple(
        point
        for point in missions._largest_component(safe, missions.MAP_SIZE_PX)
        if missions._endpoint_has_world_boundary_clearance(point)
    )
    if not candidates:
        raise InputError(f"{family}/{map_id} has no safe mission endpoints")
    seed = mission_generator_seed(family, map_id, map_sha256, mission_label)
    stream = missions._HashStream(seed)
    endpoints: list[int] = []
    robots: list[dict[str, Any]] = []
    for robot_id in range(maximum_count):
        accepted: tuple[int, int] | None = None
        for _attempt in range(missions.MAXIMUM_ATTEMPTS_PER_ROBOT):
            start = candidates[stream.randbelow(len(candidates))]
            dx, dy = missions.ROUTE_OFFSETS[
                stream.randbelow(len(missions.ROUTE_OFFSETS))
            ]
            start_x, start_y = missions._pixel_xy(start)
            goal_x, goal_y = start_x + dx, start_y + dy
            if not (
                0 <= goal_x < missions.MAP_SIZE_PX
                and 0 <= goal_y < missions.MAP_SIZE_PX
            ):
                continue
            goal = goal_y * missions.MAP_SIZE_PX + goal_x
            if safe[goal] != 255:
                continue
            if not missions._endpoint_has_world_boundary_clearance(goal):
                continue
            if not missions._endpoint_is_separated(start, endpoints):
                continue
            if not missions._endpoint_is_separated(goal, (*endpoints, start)):
                continue
            if not missions._line_is_free(
                safe, missions.MAP_SIZE_PX, start, goal
            ):
                continue
            accepted = (start, goal)
            break
        if accepted is None:
            raise InputError(
                f"could not place robot {robot_id} for "
                f"{family}/{map_id}/mission-{mission_label}"
            )
        start, goal = accepted
        endpoints.extend((start, goal))
        start_x, start_y = missions._pixel_xy(start)
        goal_x, goal_y = missions._pixel_xy(goal)
        route_length = round(
            math.hypot(goal_x - start_x, goal_y - start_y)
            * missions.PIXEL_SIZE_M,
            8,
        )
        required_travel = round(
            max(0.0, route_length - missions.ROBOT_RADIUS_M), 8
        )
        if not (
            missions.MINIMUM_GOAL_REACH_TRAVEL_DISTANCE_M
            <= required_travel
            <= missions.MAXIMUM_GOAL_REACH_TRAVEL_DISTANCE_M
        ):
            raise InputError("mission route differs from the frozen travel window")
        robots.append(
            {
                "decision": stream.randbelow(10),
                "goal_pixel": [goal_x, goal_y],
                "required_travel_distance_m": required_travel,
                "robot_id": robot_id,
                "route_length_m": route_length,
                "start_pixel": [start_x, start_y],
                "waypoints_m": [
                    missions._pixel_to_world(start),
                    missions._pixel_to_world(goal),
                ],
            }
        )
    return seed, robots


def build_bank(
    *,
    family: str,
    map_id: str,
    map_path: pathlib.PurePosixPath,
    map_bytes: bytes,
    pixels: bytes,
    mission_label: int,
) -> dict[str, Any]:
    counts = COUNTS[family]
    generator_seed, robots = _generate_robots(
        family=family,
        map_id=map_id,
        map_sha256=sha256_bytes(map_bytes),
        pixels=pixels,
        mission_label=mission_label,
        maximum_count=counts[-1],
    )
    without_id: dict[str, Any] = {
        "count_grid": list(counts),
        "family": family,
        "generator_seed_uint64": generator_seed,
        "geometry_policy": missions._geometry_policy(),
        "map": {
            "bytes": len(map_bytes),
            "map_id": map_id,
            "path": map_path.as_posix(),
            "sha256": sha256_bytes(map_bytes),
            "size_px": missions.MAP_SIZE_PX,
        },
        "max_robot_count": counts[-1],
        "mission_label": mission_label,
        "phase": "prospective-multimap",
        "robots": robots,
        "sampling_structure": {
            "clustering_unit": "family-by-map",
            "count_relationship_within_bank": "nested-prefix",
            "independent_scene_assumption": False,
            "mission_relationship_within_map": "within-map-replicate",
        },
        "schema_version": "dancers-multimap-mission-bank-v1",
        "static_inter_route_distance": missions._static_route_diagnostic(
            robots
        ),
    }
    bank_id = sha256_bytes(canonical_json_bytes(without_id))
    return {"bank_id": bank_id, **without_id}


def scenario_from_bank(bank: dict[str, Any], count: int) -> dict[str, Any]:
    if count not in bank["count_grid"]:
        raise InputError("scenario count is outside its bank grid")
    robots = bank["robots"][:count]
    return {
        "decisions": {
            str(robot["robot_id"]): robot["decision"] for robot in robots
        },
        "metadata": {
            "clearance_radius_px": missions.CLEARANCE_RADIUS_PX,
            "environment_id": bank["family"],
            "fixed_work_timesteps": FIXED_TIMESTEPS,
            "generator_seed_uint64": bank["generator_seed_uint64"],
            "map_id": bank["map"]["map_id"],
            "map_sha256": bank["map"]["sha256"],
            "maximum_linear_speed_m_per_second": (
                missions.MAXIMUM_LINEAR_SPEED_M_PER_SECOND
            ),
            "mission_bank_id": bank["bank_id"],
            "mission_label": bank["mission_label"],
            "phase": "prospective-multimap",
            "prefix_robot_count": count,
            "robot_radius_m": missions.ROBOT_RADIUS_M,
            "scenario_kind": "dancers-multimap-prefix-v1",
            "timestep_seconds": missions.CONTROLLER_TIMESTEP_SECONDS,
            "world_size_m": missions.WORLD_SIZE_M,
        },
        "num_robots": count,
        "seed_robots": [],
        "waypoints": {
            str(robot["robot_id"]): robot["waypoints_m"] for robot in robots
        },
    }


def render_config(
    *,
    scene_id: str,
    family: str,
    map_relpath: pathlib.PurePosixPath,
    scenario_relpath: pathlib.PurePosixPath,
    count: int,
    mission_label: int,
) -> bytes:
    assets = map_relpath.parent.as_posix()
    text = f'''SIMULATION_NAME: "{scene_id}"
LOG_RESULTS: true
ASSETS_DIR: {assets}
OBSTACLE_FILE: map.png
SCENARIO: file
SCENARIO_FILE: {scenario_relpath.as_posix()}
NUM_ROBOTS: {count}
COMMUNICATION_RADIUS: 20
RNG_SEED: {runtime_seed(mission_label)}
COMMS_FAILURE_RATE: 0.0

WORLD_SZ: 100
TIMESTEP: 0.1
MAX_TIME: 100
SIM_MODE: 1
ROBOT_MODEL: sphere

DISPLAY: 0
SCREEN_SZ: 800
DRAW_INTERROBOT: 0
DRAW_PATH: 0
DRAW_TRAJ: 0
MAX_TRAJ_LEN: 100
DRAW_WAYPOINTS: 0

FACTORGRAPH_LAYERS:
  - name: Planning
    type: planning
    params:
      T_HORIZON: 1.5
      ROBOT_RADIUS: 1.0
      MAX_SPEED: 2.0
      TURNING_RADIUS: 1.0
      SIGMA_VARIABLE_HORIZON: 0.01
      SIGMA_FACTOR_DYNAMICS: 0.1
      SIGMA_FACTOR_UNICYCLE: 0.001
      SIGMA_FACTOR_INTERROBOT: 0.01
      SIGMA_FACTOR_OBSTACLE: 0.01

NUM_ITERS: 2
DAMPING: 0.0
SYMMETRIC_INTERROBOT_FACTORS: false
'''
    return text.encode("utf-8")


def _identity(relative: pathlib.PurePosixPath, raw: bytes) -> dict[str, Any]:
    return {
        "bytes": len(raw),
        "path": relative.as_posix(),
        "sha256": sha256_bytes(raw),
    }


def _write(root: pathlib.Path, relative: pathlib.PurePosixPath, raw: bytes) -> None:
    path = root.joinpath(*relative.parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(raw)


def compile_inputs(output_root: pathlib.Path) -> dict[str, Any]:
    root = output_root.resolve()
    if root.exists() or root.is_symlink():
        raise InputError(f"output already exists: {root}")
    root.mkdir(parents=True, exist_ok=False)
    files: list[dict[str, Any]] = []
    maps: list[dict[str, Any]] = []
    scenes: list[dict[str, Any]] = []
    try:
        for spec in map_specs():
            family = spec["family"]
            map_id = spec["map_id"]
            pixels_mutable, generation = environments.generate_map(
                family, spec["map_seed"]
            )
            pixels = bytes(pixels_mutable)
            map_raw = environments.grayscale_png_bytes(
                pixels, environments.MAP_SIZE
            )
            map_rel = pathlib.PurePosixPath(
                "maps", family, map_id, "map.png"
            )
            _write(root, map_rel, map_raw)
            map_file = _identity(map_rel, map_raw)
            files.append(map_file)
            source_metrics = environments.measure_map(
                pixels, environments.MAP_SIZE
            )
            effective = environments.expand_nearest_neighbour(
                pixels,
                environments.MAP_SIZE,
                environments.RUNTIME_MAP_SIZE,
            )
            map_record = {
                "effective_raster_sha256": sha256_bytes(bytes(effective)),
                "family": family,
                "generation": generation,
                "map_id": map_id,
                "map_index": spec["map_index"],
                "map_seed": spec["map_seed"],
                "metrics": source_metrics,
                "png": map_file,
            }
            maps.append(map_record)
            for mission_label in spec["mission_labels"]:
                bank = build_bank(
                    family=family,
                    map_id=map_id,
                    map_path=OUTPUT_RELATIVE / map_rel,
                    map_bytes=map_raw,
                    pixels=pixels,
                    mission_label=mission_label,
                )
                bank_rel = pathlib.PurePosixPath(
                    "missions",
                    family,
                    map_id,
                    f"mission-{mission_label:02d}",
                    "bank.json",
                )
                bank_raw = canonical_json_bytes(bank)
                _write(root, bank_rel, bank_raw)
                files.append(_identity(bank_rel, bank_raw))
                for count in COUNTS[family]:
                    scenario = scenario_from_bank(bank, count)
                    scenario_rel = pathlib.PurePosixPath(
                        "scenarios",
                        family,
                        map_id,
                        f"mission-{mission_label:02d}",
                        f"n-{count}",
                        "scenario.json",
                    )
                    scenario_raw = canonical_json_bytes(scenario)
                    _write(root, scenario_rel, scenario_raw)
                    files.append(_identity(scenario_rel, scenario_raw))
                    scene_id = (
                        f"{family}-{map_id}-m{mission_label:02d}-n{count}"
                    )
                    config_rel = pathlib.PurePosixPath(
                        "configs",
                        family,
                        map_id,
                        f"mission-{mission_label:02d}",
                        f"n-{count}",
                        "config.yaml",
                    )
                    config_raw = render_config(
                        scene_id=scene_id,
                        family=family,
                        map_relpath=OUTPUT_RELATIVE / map_rel,
                        scenario_relpath=OUTPUT_RELATIVE / scenario_rel,
                        count=count,
                        mission_label=mission_label,
                    )
                    _write(root, config_rel, config_raw)
                    files.append(_identity(config_rel, config_raw))
                    scenes.append(
                        {
                            "cell_id": f"{family}-n{count}",
                            "config_relpath": config_rel.as_posix(),
                            "config_sha256": sha256_bytes(config_raw),
                            "family": family,
                            "map_id": map_id,
                            "map_relpath": map_rel.as_posix(),
                            "map_sha256": sha256_bytes(map_raw),
                            "mission_id": f"mission-{mission_label:02d}",
                            "mission_label": mission_label,
                            "robot_count": count,
                            "runtime_rng_seed": runtime_seed(mission_label),
                            "scenario_relpath": scenario_rel.as_posix(),
                            "scenario_sha256": sha256_bytes(scenario_raw),
                            "scene_id": scene_id,
                        }
                    )
        manifest: dict[str, Any] = {
            "classification": {
                "algorithm_execution_during_generation": False,
                "open_map_inference": "fixed-map mission control",
                "planner_outcomes_read": False,
                "stochastic_family_inference_unit": "map",
            },
            "design": {
                "counts": {key: list(value) for key, value in COUNTS.items()},
                "fixed_timesteps": FIXED_TIMESTEPS,
                "map_seeds": list(MAP_SEEDS),
                "mission_domain_hex": MISSION_DOMAIN.hex(),
                "mission_labels": list(MISSION_LABELS),
                "runtime_seed_rule": RUNTIME_SEED_RULE,
                "timestep_seconds": 0.1,
            },
            "files": sorted(files, key=lambda item: item["path"]),
            "generator": {
                "path": "scripts/replication/prepare_multimap_inputs.py",
                "sha256": sha256_file(pathlib.Path(__file__)),
            },
            "maps": maps,
            "root_repository_relative": OUTPUT_RELATIVE.as_posix(),
            "scenes": scenes,
            "schema_version": SCHEMA,
            "study_id": STUDY_ID,
        }
        validate_manifest(root, manifest)
        manifest_raw = canonical_json_bytes(manifest)
        _write(root, pathlib.PurePosixPath("manifest.json"), manifest_raw)
        _write(
            root,
            pathlib.PurePosixPath("manifest.sha256"),
            f"{sha256_bytes(manifest_raw)}  manifest.json\n".encode("ascii"),
        )
        return manifest
    except BaseException:
        shutil.rmtree(root, ignore_errors=True)
        raise


def validate_manifest(root: pathlib.Path, manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != SCHEMA:
        raise InputError("input manifest schema differs")
    generator = manifest.get("generator")
    if (
        not isinstance(generator, dict)
        or generator.get("path") != "scripts/replication/prepare_multimap_inputs.py"
        or generator.get("sha256") != sha256_file(pathlib.Path(__file__))
    ):
        raise InputError("input generator identity differs")
    scenes = manifest.get("scenes")
    maps = manifest.get("maps")
    files = manifest.get("files")
    if not isinstance(scenes, list) or len(scenes) != 200:
        raise InputError("input scene inventory must contain 200 scenes")
    if not isinstance(maps, list) or len(maps) != 41:
        raise InputError("map inventory must contain 41 maps")
    if not isinstance(files, list):
        raise InputError("file inventory is missing")
    paths = [item["path"] for item in files]
    if len(paths) != len(set(paths)):
        raise InputError("file inventory contains duplicates")
    for item in files:
        path = root.joinpath(*pathlib.PurePosixPath(item["path"]).parts)
        if (
            not path.is_file()
            or path.is_symlink()
            or path.stat().st_size != item["bytes"]
            or sha256_file(path) != item["sha256"]
        ):
            raise InputError(f"input file identity differs: {item['path']}")
    scene_ids = [scene["scene_id"] for scene in scenes]
    if len(scene_ids) != len(set(scene_ids)):
        raise InputError("scene identifiers are not unique")
    expected_cells = {
        (family, count): (20 if family == "open" else 20)
        for family in FAMILIES
        for count in COUNTS[family]
    }
    observed_cells = {
        key: sum(
            scene["family"] == key[0] and scene["robot_count"] == key[1]
            for scene in scenes
        )
        for key in expected_cells
    }
    if observed_cells != expected_cells:
        raise InputError("family/count scene grid differs")
    stochastic_hashes = [
        item["png"]["sha256"] for item in maps if item["family"] != "open"
    ]
    if len(stochastic_hashes) != 40 or len(set(stochastic_hashes)) != 40:
        raise InputError("stochastic map hashes are not all distinct")
    if len({item["effective_raster_sha256"] for item in maps}) != 41:
        raise InputError("effective raster identities are not all distinct")
    if any(
        item["metrics"]["free_space"]["component_count_4_connected"] != 1
        for item in maps
    ):
        raise InputError("a generated map has disconnected free space")
    by_key = {(item["family"], item["map_index"]): item for item in maps}
    for index in range(1, 11):
        densities = [
            by_key[(family, index)]["metrics"]["boundary_density"]
            for family in ("fractal_low", "fractal_medium", "fractal_high")
        ]
        if not densities[0] < densities[1] < densities[2]:
            raise InputError(
                f"matched fractal density ordering differs at map {index}"
            )
    for family in FAMILIES:
        for map_id in {scene["map_id"] for scene in scenes if scene["family"] == family}:
            relevant = [
                scene
                for scene in scenes
                if scene["family"] == family and scene["map_id"] == map_id
            ]
            for mission_id in {scene["mission_id"] for scene in relevant}:
                mission_scenes = sorted(
                    (scene for scene in relevant if scene["mission_id"] == mission_id),
                    key=lambda item: item["robot_count"],
                )
                prior: dict[str, Any] | None = None
                for scene in mission_scenes:
                    raw = root.joinpath(
                        *pathlib.PurePosixPath(scene["scenario_relpath"]).parts
                    ).read_bytes()
                    scenario = json.loads(raw)
                    count = scene["robot_count"]
                    expected = {str(index) for index in range(count)}
                    if (
                        scenario["num_robots"] != count
                        or set(scenario["decisions"]) != expected
                        or set(scenario["waypoints"]) != expected
                    ):
                        raise InputError("scenario prefix keys differ")
                    if prior is not None:
                        for field in ("decisions", "waypoints"):
                            if any(
                                scenario[field][key] != value
                                for key, value in prior[field].items()
                            ):
                                raise InputError("nested scenario prefix differs")
                    prior = scenario


def load_and_validate(root: pathlib.Path) -> dict[str, Any]:
    manifest_path = root / "manifest.json"
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    if raw != canonical_json_bytes(manifest):
        raise InputError("manifest is not canonical JSON")
    validate_manifest(root, manifest)
    sidecar = (root / "manifest.sha256").read_text(encoding="ascii").split()
    if sidecar != [sha256_bytes(raw), "manifest.json"]:
        raise InputError("manifest sidecar differs")
    return manifest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--output-root", type=pathlib.Path, required=True)
    check = sub.add_parser("check")
    check.add_argument("--output-root", type=pathlib.Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.command == "prepare":
            manifest = compile_inputs(args.output_root)
        else:
            manifest = load_and_validate(args.output_root.resolve())
        print(
            f"validated {len(manifest['maps'])} maps and "
            f"{len(manifest['scenes'])} scenes"
        )
        return 0
    except (InputError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"multi-map input preparation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
