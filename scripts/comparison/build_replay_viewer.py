#!/usr/bin/env python3
"""Build a self-contained synchronized DANCeRS trace replay HTML artifact."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import math
import re
import struct
import sys
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import trace_compare as trace_tools  # noqa: E402


DEFAULT_MAX_FRAMES = 20_000
DEFAULT_MAX_ROBOT_SAMPLES = 2_000_000
MAX_MAP_BYTES = 32 * 1024 * 1024
IDS_DEFAULT_OFF_ABOVE_ROBOTS = 128
IDS_FORCED_OFF_ABOVE_ROBOTS = 512
TRAILS_DEFAULT_OFF_ABOVE_ROBOTS = 128
TRAILS_FORCED_OFF_ABOVE_ROBOTS = 512
TRAIL_FRAME_LIMIT = 500
TRAIL_ROBOT_SAMPLE_LIMIT_PER_PANEL = 50_000
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
METHOD_SPECIFIC_PROVENANCE_FIELDS = frozenset(
    {
        "algorithm_revision",
        "binary_sha256",
        "implementation_id",
        "run_id",
        "source_head_revision",
        "source_tree_hash",
    }
)
V3_REQUIRED_PAIRED_PROVENANCE_FIELDS = (
    "config_sha256",
    "map_sha256",
    "scenario_sha256",
)


class ViewerInputError(ValueError):
    pass


@dataclass(frozen=True)
class VisualTrace:
    source: Path
    label: str
    schema_version: str
    trace_detail: str
    provenance: dict[str, Any]
    resolved_config: dict[str, Any]
    world_size_m: float
    frames: list[dict[str, Any]]
    phase_keys: list[tuple[int, str]]


@dataclass(frozen=True)
class ViewerBuildResult:
    frame_count: int
    robot_samples: int
    exact_semantic_match: bool
    mismatch_tick: int | None
    output_bytes: int
    html_bytes: bytes


def _finite_number(value: Any, path: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
    ):
        raise ViewerInputError(f"{path} must be a finite JSON number")
    return float(value)


def _optional_xy(value: Any, path: str) -> list[float] | None:
    if value is None:
        return None
    if not isinstance(value, list) or len(value) < 2:
        raise ViewerInputError(f"{path} must be null or an array with x/y")
    return [
        _finite_number(value[0], f"{path}[0]"),
        _finite_number(value[1], f"{path}[1]"),
    ]


def _project_metrics(metrics: Any, path: str) -> dict[str, Any]:
    if not isinstance(metrics, dict):
        raise ViewerInputError(f"{path} must be an object")
    fields = (
        "robot_count",
        "updated_this_frame",
        "frame_min_pair_clearance_m",
        "frame_min_obstacle_clearance_m",
        "pair_collisions",
        "obstacle_collisions",
        "cumulative_pair_collision_exposure_seconds",
        "cumulative_obstacle_collision_exposure_seconds",
        "goals_reached",
        "all_goals_reached",
        "cumulative_invalid_geometry_evaluations",
    )
    return {field: metrics.get(field) for field in fields if field in metrics}


def _project_robot(robot: Any, frame_path: str) -> dict[str, Any]:
    if not isinstance(robot, dict):
        raise ViewerInputError(f"{frame_path} must be an object")
    rid = robot.get("rid")
    if not isinstance(rid, int) or isinstance(rid, bool) or rid < 0:
        raise ViewerInputError(f"{frame_path}.rid must be a non-negative integer")
    state = robot.get("state")
    if not isinstance(state, list) or len(state) < 2:
        raise ViewerInputError(f"{frame_path}.state must contain x/y")
    radius = _finite_number(robot.get("radius_m"), f"{frame_path}.radius_m")
    if radius <= 0:
        raise ViewerInputError(f"{frame_path}.radius_m must be positive")
    bearing = _finite_number(robot.get("bearing"), f"{frame_path}.bearing")
    goal_reached = robot.get("goal_reached")
    if not isinstance(goal_reached, bool):
        raise ViewerInputError(f"{frame_path}.goal_reached must be boolean")
    projected = {
        "rid": rid,
        "xy": [
            _finite_number(state[0], f"{frame_path}.state[0]"),
            _finite_number(state[1], f"{frame_path}.state[1]"),
        ],
        "bearing": bearing,
        "radius_m": radius,
        "current_waypoint": _optional_xy(
            robot.get("current_waypoint"), f"{frame_path}.current_waypoint"
        ),
        "final_goal": _optional_xy(
            robot.get("final_goal"), f"{frame_path}.final_goal"
        ),
        "goal_reached": goal_reached,
    }
    if "decision" in robot:
        decision = robot["decision"]
        if not isinstance(decision, int) or isinstance(decision, bool):
            raise ViewerInputError(f"{frame_path}.decision must be an integer")
        projected["decision"] = decision
    return projected


def load_visual_trace(
    source: Path | str,
    *,
    max_frames: int = DEFAULT_MAX_FRAMES,
    max_robot_samples: int = DEFAULT_MAX_ROBOT_SAMPLES,
) -> VisualTrace:
    path = Path(source)
    if max_frames <= 0 or max_robot_samples <= 0:
        raise ValueError("viewer bounds must be positive")
    frames: list[dict[str, Any]] = []
    phase_keys: list[tuple[int, str]] = []
    robot_samples = 0
    with trace_tools.TraceReader(path) as reader:
        if reader.header.trace_detail == "digest":
            raise ViewerInputError(
                f"{path}: digest traces have no positions; rerun with "
                "DANCERS_TRACE_DETAIL=positions or full"
            )
        resolved = reader.header.canonical["resolved_config"]
        world_size = _finite_number(
            resolved.get("world_size_m"),
            f"{path}: $.header.resolved_config.world_size_m",
        )
        if world_size <= 0:
            raise ViewerInputError(f"{path}: world_size_m must be positive")
        for frame_index, frame in enumerate(reader.frames()):
            if frame_index >= max_frames:
                raise ViewerInputError(
                    f"{path}: viewer frame limit {max_frames} exceeded"
                )
            robots = frame["robots"]
            robot_samples += len(robots)
            if robot_samples > max_robot_samples:
                raise ViewerInputError(
                    f"{path}: viewer robot-sample limit "
                    f"{max_robot_samples} exceeded"
                )
            tick = frame["tick"]
            phase = frame["phase"]
            simulated_time = _finite_number(
                frame.get("simulated_time_seconds"),
                f"{path}: frame[{frame_index}].simulated_time_seconds",
            )
            projected_robots = [
                _project_robot(
                    robot,
                    f"{path}: frame[{frame_index}].robots[{robot_index}]",
                )
                for robot_index, robot in enumerate(robots)
            ]
            frames.append(
                {
                    "tick": tick,
                    "phase": phase,
                    "simulated_time_seconds": simulated_time,
                    "robots": projected_robots,
                    "metrics": _project_metrics(
                        frame.get("metrics"),
                        f"{path}: frame[{frame_index}].metrics",
                    ),
                }
            )
            phase_keys.append((tick, phase))

        provenance = reader.header.provenance
        label = provenance.get("implementation_id")
        if not isinstance(label, str) or not label:
            label = path.stem
        return VisualTrace(
            source=path,
            label=label,
            schema_version=reader.header.schema_version,
            trace_detail=reader.header.trace_detail,
            provenance=provenance,
            resolved_config=resolved,
            world_size_m=world_size,
            frames=frames,
            phase_keys=phase_keys,
        )


def _read_png(path: Path) -> tuple[bytes, int, int, str]:
    data = path.read_bytes()
    if len(data) > MAX_MAP_BYTES:
        raise ViewerInputError(
            f"{path}: map exceeds {MAX_MAP_BYTES}-byte viewer limit"
        )
    if len(data) < 33 or not data.startswith(PNG_SIGNATURE):
        raise ViewerInputError(f"{path}: map must be a valid PNG")
    ihdr_length = struct.unpack(">I", data[8:12])[0]
    if data[12:16] != b"IHDR" or ihdr_length != 13:
        raise ViewerInputError(f"{path}: PNG must begin with a standard IHDR")
    width, height = struct.unpack(">II", data[16:24])
    if width <= 0 or height <= 0:
        raise ViewerInputError(f"{path}: PNG dimensions must be positive")
    return data, width, height, hashlib.sha256(data).hexdigest()


def _validate_map_provenance(
    trace: VisualTrace, map_sha256: str
) -> bool:
    declared = trace.provenance.get("map_sha256")
    if declared is None:
        raise ViewerInputError(
            f"{trace.source}: metadata.map_sha256 is required before replay"
        )
    if not isinstance(declared, str) or not trace_tools.SHA256_RE.fullmatch(
        declared
    ):
        raise ViewerInputError(
            f"{trace.source}: metadata.map_sha256 is not lower-case SHA-256"
        )
    if declared != map_sha256:
        raise ViewerInputError(
            f"{trace.source}: selected map SHA-256 {map_sha256} does not "
            f"match trace provenance {declared}"
        )
    return True


def _validate_pair_input_provenance(
    reference: VisualTrace, optimized: VisualTrace
) -> None:
    if reference.schema_version != optimized.schema_version:
        raise ViewerInputError(
            "paired traces use different trace schema versions"
        )
    if reference.trace_detail != optimized.trace_detail:
        raise ViewerInputError(
            "paired traces use different trace detail: "
            f"reference={reference.trace_detail!r}, "
            f"optimized={optimized.trace_detail!r}"
        )
    if reference.schema_version == trace_tools.TRACE_SCHEMA_V3:
        for trace in (reference, optimized):
            for field in V3_REQUIRED_PAIRED_PROVENANCE_FIELDS:
                value = trace.provenance.get(field)
                if (
                    not isinstance(value, str)
                    or trace_tools.SHA256_RE.fullmatch(value) is None
                ):
                    raise ViewerInputError(
                        f"{trace.source}: metadata.{field} must be a "
                        "lower-case SHA-256 for trace-v3 replay"
                    )

    paired_reference = {
        key: value
        for key, value in reference.provenance.items()
        if key not in METHOD_SPECIFIC_PROVENANCE_FIELDS
    }
    paired_optimized = {
        key: value
        for key, value in optimized.provenance.items()
        if key not in METHOD_SPECIFIC_PROVENANCE_FIELDS
    }
    provenance_difference = trace_tools.first_json_difference(
        paired_reference,
        paired_optimized,
        "$.metadata.paired_inputs",
    )
    if provenance_difference is not None:
        path, reason, _, _ = provenance_difference
        raise ViewerInputError(
            f"paired trace provenance differs at {path}: {reason}"
        )

    resolved_difference = trace_tools.first_json_difference(
        reference.resolved_config,
        optimized.resolved_config,
        "$.resolved_config",
    )
    if resolved_difference is not None:
        path, reason, _, _ = resolved_difference
        raise ViewerInputError(
            f"paired resolved configuration differs at {path}: {reason}"
        )


def _alignment_error(
    left_keys: list[tuple[int, str]],
    right_keys: list[tuple[int, str]],
) -> str | None:
    sentinel = object()
    for index, (left, right) in enumerate(
        zip_longest(left_keys, right_keys, fillvalue=sentinel)
    ):
        if left != right:
            return (
                f"trace phase alignment differs at frame {index}: "
                f"reference={None if left is sentinel else left}, "
                f"optimized={None if right is sentinel else right}"
            )
    return None


def _validate_paired_robot_id_sets(
    reference: VisualTrace, optimized: VisualTrace
) -> None:
    # _alignment_error has already proved equal, positionally identical phase
    # schedules.  Plain zip keeps this viewer usable with the repository's
    # supported system Python 3.9 as well as newer runtimes.
    for frame_index, (reference_frame, optimized_frame) in enumerate(
        zip(reference.frames, optimized.frames)
    ):
        reference_ids = {
            robot["rid"] for robot in reference_frame["robots"]
        }
        optimized_ids = {
            robot["rid"] for robot in optimized_frame["robots"]
        }
        if reference_ids != optimized_ids:
            reference_only = sorted(reference_ids - optimized_ids)
            optimized_only = sorted(optimized_ids - reference_ids)
            tick = reference_frame["tick"]
            phase = reference_frame["phase"]
            raise ViewerInputError(
                "paired robot-ID sets differ at "
                f"frame {frame_index} ({tick=}, {phase=}): "
                f"reference_only={reference_only}, "
                f"optimized_only={optimized_only}"
            )


def _mismatch_payload(
    comparison: trace_tools.ComparisonResult,
    phase_keys: list[tuple[int, str]],
) -> dict[str, Any] | None:
    mismatch = comparison.mismatch
    if mismatch is None:
        return None
    if mismatch.tick is None:
        frame_index = 0
    else:
        try:
            frame_index = phase_keys.index((mismatch.tick, mismatch.phase))
        except ValueError:
            frame_index = 0
    return {
        "tick": mismatch.tick,
        "phase": mismatch.phase,
        "path": mismatch.path,
        "reason": mismatch.reason,
        "frame_index": frame_index,
    }


def _trace_payload(trace: VisualTrace) -> dict[str, Any]:
    revision = trace.provenance.get("algorithm_revision")
    return {
        "label": trace.label,
        "trace_detail": trace.trace_detail,
        "world_size_m": trace.world_size_m,
        "revision": revision if isinstance(revision, str) else "",
        "binary_sha256": trace.provenance.get("binary_sha256", ""),
        "frames": trace.frames,
    }


def _playback_payload(frames: list[dict[str, Any]]) -> dict[str, Any]:
    if not frames:
        return {
            "clock": "simulated_time_seconds",
            "frame_schedule_seconds": [],
            "start_simulated_time_seconds": 0.0,
            "end_simulated_time_seconds": 0.0,
            "duration_seconds": 0.0,
            "equal_timestamp_transitions": 0,
        }

    simulated_times = [
        _finite_number(
            frame.get("simulated_time_seconds"),
            f"frame[{index}].simulated_time_seconds",
        )
        for index, frame in enumerate(frames)
    ]
    equal_timestamp_transitions = 0
    for index, (previous, current) in enumerate(
        zip(simulated_times, simulated_times[1:]), start=1
    ):
        if current < previous:
            raise ViewerInputError(
                "simulated_time_seconds must be nondecreasing: "
                f"frame[{index - 1}]={previous}, frame[{index}]={current}"
            )
        if current == previous:
            equal_timestamp_transitions += 1

    start = simulated_times[0]
    schedule = [
        0.0 if index == 0 else simulated_time - start
        for index, simulated_time in enumerate(simulated_times)
    ]
    duration = 0.0 if len(schedule) == 1 else schedule[-1]
    return {
        "clock": "simulated_time_seconds",
        "frame_schedule_seconds": schedule,
        "start_simulated_time_seconds": start,
        "end_simulated_time_seconds": simulated_times[-1],
        "duration_seconds": duration,
        "equal_timestamp_transitions": equal_timestamp_transitions,
    }


def _paired_playback_payload(
    reference: VisualTrace, optimized: VisualTrace
) -> dict[str, Any]:
    reference_playback = _playback_payload(reference.frames)
    optimized_playback = _playback_payload(optimized.frames)
    difference = trace_tools.first_json_difference(
        reference_playback,
        optimized_playback,
        "$.playback",
    )
    if difference is not None:
        path, reason, left, right = difference
        raise ViewerInputError(
            f"paired simulated-time schedules differ at {path}: {reason}; "
            f"reference={left!r}, optimized={right!r}"
        )
    return {
        **reference_playback,
        "paired_schedule_verified": True,
    }


def _environment_payload(
    family: str | None,
    descriptors: dict[str, Any] | None,
) -> dict[str, Any]:
    if family is None and descriptors is None:
        return {
            "available": False,
            "family": "",
            "descriptor_source": "",
            "map_metrics": None,
        }
    if (
        not isinstance(family, str)
        or not family
        or len(family) > 128
        or not isinstance(descriptors, dict)
    ):
        raise ViewerInputError(
            "environment family and descriptors must be supplied together"
        )
    expected_top = {
        "obstacle_occupancy",
        "boundary_density",
        "boundary_box_counting",
        "free_space",
    }
    if set(descriptors) != expected_top:
        raise ViewerInputError(
            "environment descriptors have the wrong exact fields"
        )
    box = descriptors["boundary_box_counting"]
    free = descriptors["free_space"]
    if (
        not isinstance(box, dict)
        or set(box) != {"dimension", "r_squared"}
        or not isinstance(free, dict)
        or set(free)
        != {
            "component_count_4_connected",
            "reachable_fraction",
        }
    ):
        raise ViewerInputError(
            "environment descriptor nested fields differ"
        )
    occupancy = _finite_number(
        descriptors["obstacle_occupancy"],
        "environment.obstacle_occupancy",
    )
    boundary_density = _finite_number(
        descriptors["boundary_density"],
        "environment.boundary_density",
    )
    dimension = _finite_number(
        box["dimension"],
        "environment.boundary_box_counting.dimension",
    )
    r_squared = _finite_number(
        box["r_squared"],
        "environment.boundary_box_counting.r_squared",
    )
    reachable = _finite_number(
        free["reachable_fraction"],
        "environment.free_space.reachable_fraction",
    )
    component_count = free["component_count_4_connected"]
    if (
        not isinstance(component_count, int)
        or isinstance(component_count, bool)
        or component_count < 0
    ):
        raise ViewerInputError(
            "environment component count must be a non-negative integer"
        )
    for name, value in (
        ("obstacle_occupancy", occupancy),
        ("boundary_density", boundary_density),
        ("boundary_box_counting.r_squared", r_squared),
        ("free_space.reachable_fraction", reachable),
    ):
        if not 0.0 <= value <= 1.0:
            raise ViewerInputError(
                f"environment {name} must be within [0, 1]"
            )
    if dimension <= 0.0:
        raise ViewerInputError(
            "environment boundary box-counting descriptor must be positive"
        )
    return {
        "available": True,
        "family": family,
        "descriptor_source": "effective_runtime_1000.map_metrics",
        "map_metrics": {
            "obstacle_occupancy": occupancy,
            "boundary_density": boundary_density,
            "boundary_box_counting": {
                "dimension": dimension,
                "r_squared": r_squared,
            },
            "free_space": {
                "component_count_4_connected": component_count,
                "reachable_fraction": reachable,
            },
        },
    }


def _rendering_payload(
    reference: VisualTrace, optimized: VisualTrace
) -> dict[str, Any]:
    all_frames = reference.frames + optimized.frames
    max_robot_count = max(
        (len(frame["robots"]) for frame in all_frames), default=0
    )
    routes_available = any(
        robot["current_waypoint"] is not None
        or robot["final_goal"] is not None
        for frame in all_frames
        for robot in frame["robots"]
    )
    ids_forced_off = max_robot_count > IDS_FORCED_OFF_ABOVE_ROBOTS
    trails_forced_off = (
        max_robot_count > TRAILS_FORCED_OFF_ABOVE_ROBOTS
    )
    return {
        "diagnostic_only": True,
        "included_in_compute_claims": False,
        "max_robot_count": max_robot_count,
        "routes_available": routes_available,
        "lod": {
            "ids_default_off_above_robot_count": (
                IDS_DEFAULT_OFF_ABOVE_ROBOTS
            ),
            "ids_forced_off_above_robot_count": (
                IDS_FORCED_OFF_ABOVE_ROBOTS
            ),
            "trails_default_off_above_robot_count": (
                TRAILS_DEFAULT_OFF_ABOVE_ROBOTS
            ),
            "trails_forced_off_above_robot_count": (
                TRAILS_FORCED_OFF_ABOVE_ROBOTS
            ),
            "trail_frame_limit": TRAIL_FRAME_LIMIT,
            "trail_robot_sample_limit_per_panel": (
                TRAIL_ROBOT_SAMPLE_LIMIT_PER_PANEL
            ),
        },
        "initial_controls": {
            "show_ids": (
                max_robot_count <= IDS_DEFAULT_OFF_ABOVE_ROBOTS
                and not ids_forced_off
            ),
            "ids_forced_off": ids_forced_off,
            "show_trails": (
                max_robot_count <= TRAILS_DEFAULT_OFF_ABOVE_ROBOTS
                and not trails_forced_off
            ),
            "trails_forced_off": trails_forced_off,
            "show_routes": routes_available,
        },
    }


def _safe_script_json(payload: dict[str, Any]) -> str:
    return (
        json.dumps(
            payload,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def build_replay_viewer(
    reference_trace: Path | str,
    optimized_trace: Path | str,
    map_path: Path | str,
    *,
    title: str = "DANCeRS synchronized replay",
    environment_family: str | None = None,
    environment_descriptors: dict[str, Any] | None = None,
    max_frames: int = DEFAULT_MAX_FRAMES,
    max_robot_samples: int = DEFAULT_MAX_ROBOT_SAMPLES,
) -> ViewerBuildResult:
    reference_path = Path(reference_trace)
    optimized_path = Path(optimized_trace)
    map_file = Path(map_path)

    # Exact comparison consumes both traces fully and rejects malformed trailing
    # phases. A semantic mismatch is visualized; malformed input is never built.
    comparison = trace_tools.compare_traces(reference_path, optimized_path)
    reference = load_visual_trace(
        reference_path,
        max_frames=max_frames,
        max_robot_samples=max_robot_samples,
    )
    optimized = load_visual_trace(
        optimized_path,
        max_frames=max_frames,
        max_robot_samples=max_robot_samples,
    )
    alignment_error = _alignment_error(
        reference.phase_keys, optimized.phase_keys
    )
    if alignment_error is not None:
        raise ViewerInputError(alignment_error)
    _validate_pair_input_provenance(reference, optimized)
    _validate_paired_robot_id_sets(reference, optimized)

    map_data, map_width, map_height, map_sha256 = _read_png(map_file)
    reference_map_verified = _validate_map_provenance(
        reference, map_sha256
    )
    optimized_map_verified = _validate_map_provenance(
        optimized, map_sha256
    )
    mismatch = _mismatch_payload(comparison, reference.phase_keys)
    playback = _paired_playback_payload(reference, optimized)
    payload = {
        "schema_version": "dancers-replay-viewer-v2",
        "title": title,
        "frame_count": len(reference.frames),
        "playback": playback,
        "environment": _environment_payload(
            environment_family, environment_descriptors
        ),
        "rendering": _rendering_payload(reference, optimized),
        "comparison": {
            "exact_semantic_match": comparison.equal,
            "paired_input_provenance_verified": True,
            "paired_robot_id_sets_verified": True,
            "matching_prefix_frames": comparison.frames_compared,
            "mismatch": mismatch,
        },
        "map": {
            "name": map_file.name,
            "width_px": map_width,
            "height_px": map_height,
            "sha256": map_sha256,
            "provenance_verified": (
                reference_map_verified and optimized_map_verified
            ),
            "data_uri": (
                "data:image/png;base64,"
                + base64.b64encode(map_data).decode("ascii")
            ),
        },
        "reference": _trace_payload(reference),
        "optimized": _trace_payload(optimized),
    }
    replacements = {
        "__TITLE__": html.escape(title, quote=True),
        "__PAYLOAD__": _safe_script_json(payload),
    }
    rendered = re.sub(
        r"__TITLE__|__PAYLOAD__",
        lambda match: replacements[match.group(0)],
        HTML_TEMPLATE,
    ).encode(
        "utf-8"
    )
    return ViewerBuildResult(
        frame_count=len(reference.frames),
        robot_samples=sum(
            len(frame["robots"]) for frame in reference.frames
        )
        + sum(len(frame["robots"]) for frame in optimized.frames),
        exact_semantic_match=comparison.equal,
        mismatch_tick=None if mismatch is None else mismatch["tick"],
        output_bytes=len(rendered),
        html_bytes=rendered,
    )


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<title>__TITLE__</title>
<style>
:root {
  color-scheme: dark;
  --bg: #0b1119;
  --panel: #121c29;
  --panel-2: #182536;
  --line: #2b3b50;
  --text: #e8eef7;
  --muted: #9db0c8;
  --accent: #49b9ff;
  --good: #48d597;
  --bad: #ff5b7f;
  --warn: #f4bd4f;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  min-width: 720px;
  background: radial-gradient(circle at 50% -20%, #20334a 0, var(--bg) 42%);
  color: var(--text);
  font: 14px/1.35 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 20px;
  padding: 18px 22px 10px;
}
h1 { margin: 0; font-size: 21px; letter-spacing: .01em; }
.subtle { color: var(--muted); font-size: 12px; }
.header-badges {
  display: grid;
  justify-items: end;
  gap: 6px;
}
#statusBadge, #diagnosticBadge {
  max-width: 52vw;
  padding: 7px 11px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: rgba(0,0,0,.25);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
#statusBadge.good { color: var(--good); border-color: rgba(72,213,151,.55); }
#statusBadge.bad { color: var(--bad); border-color: rgba(255,91,127,.6); }
#diagnosticBadge {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 4px 10px;
  color: var(--warn);
  border-color: rgba(244,189,79,.55);
  font-size: 12px;
  white-space: normal;
}
#diagnosticBadge span { white-space: nowrap; }
main { padding: 0 18px 22px; }
.controls {
  display: grid;
  grid-template-columns: auto auto auto auto minmax(230px, 1fr) auto auto auto;
  align-items: center;
  gap: 8px;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: rgba(18,28,41,.96);
  box-shadow: 0 10px 40px rgba(0,0,0,.22);
}
button, select {
  min-height: 34px;
  border: 1px solid #3a506a;
  border-radius: 7px;
  background: var(--panel-2);
  color: var(--text);
  padding: 5px 10px;
  font: inherit;
}
button:hover, select:hover { border-color: var(--accent); }
button:focus-visible, select:focus-visible, input:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
.check { display: flex; align-items: center; gap: 5px; white-space: nowrap; }
.check.disabled { color: var(--muted); }
.time-readout {
  grid-column: 1 / -1;
  color: var(--muted);
  font: 12px/1.3 ui-monospace, SFMono-Regular, Menlo, monospace;
  text-align: right;
}
.timeline-wrap { position: relative; height: 28px; display: flex; align-items: center; }
#timeline { width: 100%; accent-color: var(--accent); }
#mismatchMarker {
  position: absolute;
  top: 1px;
  bottom: 1px;
  width: 2px;
  background: var(--bad);
  box-shadow: 0 0 8px var(--bad);
  pointer-events: none;
  display: none;
}
.panels {
  display: grid;
  grid-template-columns: minmax(0,1fr) minmax(0,1fr);
  gap: 12px;
  margin-top: 12px;
}
.panel {
  min-width: 0;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 12px;
  background: var(--panel);
  box-shadow: 0 12px 36px rgba(0,0,0,.24);
}
.panel.mismatch { border-color: var(--bad); box-shadow: 0 0 0 1px rgba(255,91,127,.25), 0 12px 36px rgba(0,0,0,.28); }
.panel-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 9px 12px;
  border-bottom: 1px solid var(--line);
}
.panel-title { font-weight: 700; }
.revision {
  max-width: 52%;
  color: var(--muted);
  font: 11px/1.4 ui-monospace, SFMono-Regular, Menlo, monospace;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.canvas-wrap { position: relative; width: 100%; aspect-ratio: 1; background: white; }
canvas { display: block; width: 100%; height: 100%; }
.summary {
  position: absolute;
  left: 9px;
  top: 9px;
  max-width: calc(100% - 18px);
  padding: 7px 9px;
  border: 1px solid rgba(255,255,255,.25);
  border-radius: 7px;
  background: rgba(7,12,19,.78);
  color: #f4f8fc;
  font: 11px/1.45 ui-monospace, SFMono-Regular, Menlo, monospace;
  white-space: pre;
  pointer-events: none;
}
.details {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 10px;
  margin-top: 12px;
}
.card {
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: rgba(18,28,41,.9);
}
.card strong { display: block; margin-bottom: 4px; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; overflow-wrap: anywhere; }
.bad-text { color: var(--bad); }
.good-text { color: var(--good); }
.warn-text { color: var(--warn); }
@media (max-width: 920px) {
  .controls { grid-template-columns: repeat(4,auto); }
  .timeline-wrap { grid-column: 1 / -1; grid-row: 2; }
  .time-readout { grid-row: 3; }
  .panels { grid-template-columns: 1fr; }
  .details { grid-template-columns: 1fr; }
}
</style>
</head>
<body>
<header>
  <div>
    <h1 id="title">__TITLE__</h1>
    <div class="subtle" id="frameReadout">Loading embedded replay…</div>
  </div>
  <div class="header-badges">
    <div id="statusBadge">Validating replay payload…</div>
    <div id="diagnosticBadge" aria-label="Diagnostic replay limitations">
      <span>DIAGNOSTIC REPLAY</span>
      <span>NOT LIVE SOLVER TIMING</span>
      <span>RENDERING EXCLUDED FROM COMPUTE</span>
    </div>
  </div>
</header>
<main>
  <section class="controls" aria-label="Replay controls">
    <button id="stepBack" type="button" title="Previous frame (left arrow)">◀ Step</button>
    <button id="playPause" type="button" title="Play/pause (space)">▶ Play</button>
    <button id="stepForward" type="button" title="Next frame (right arrow)">Step ▶</button>
    <select id="speed" aria-label="Playback speed">
      <option value="0.25">0.25×</option>
      <option value="0.5">0.5×</option>
      <option value="1" selected>1×</option>
      <option value="2">2×</option>
      <option value="4">4×</option>
      <option value="8">8×</option>
    </select>
    <div class="timeline-wrap">
      <input id="timeline" type="range" min="0" max="0" value="0" step="1" aria-label="Replay timeline">
      <span id="mismatchMarker" title="First exact semantic mismatch"></span>
    </div>
    <label class="check" id="trailsLabel"><input id="showTrails" type="checkbox"> Trails</label>
    <label class="check" id="idsLabel"><input id="showIds" type="checkbox"> Robot IDs</label>
    <label class="check" id="routesLabel"><input id="showRoutes" type="checkbox"> Waypoint/goal routes</label>
    <output class="time-readout" id="timeReadout">Simulated time loading…</output>
  </section>

  <section class="panels">
    <article class="panel" id="referencePanel">
      <div class="panel-head">
        <span class="panel-title" id="referenceTitle">Reference</span>
        <span class="revision" id="referenceRevision"></span>
      </div>
      <div class="canvas-wrap">
        <canvas id="referenceCanvas" aria-label="Reference swarm replay"></canvas>
        <div class="summary" id="referenceSummary"></div>
      </div>
    </article>
    <article class="panel" id="optimizedPanel">
      <div class="panel-head">
        <span class="panel-title" id="optimizedTitle">Optimized</span>
        <span class="revision" id="optimizedRevision"></span>
      </div>
      <div class="canvas-wrap">
        <canvas id="optimizedCanvas" aria-label="Optimized swarm replay"></canvas>
        <div class="summary" id="optimizedSummary"></div>
      </div>
    </article>
  </section>

  <section class="details">
    <div class="card"><strong>Map</strong><span id="mapInfo" class="mono"></span></div>
    <div class="card"><strong>Obstacle environment</strong><span id="environmentInfo"></span></div>
    <div class="card"><strong>Synchronization</strong><span id="syncInfo"></span></div>
    <div class="card"><strong>Exact comparison</strong><span id="mismatchInfo"></span></div>
    <div class="card"><strong>Rendering detail</strong><span id="lodInfo"></span></div>
    <div class="card"><strong>Replay health</strong><span id="renderHealth">No render frames coalesced.</span></div>
  </section>
</main>

<script id="replayPayload" type="application/json">__PAYLOAD__</script>
<script>
"use strict";
const payload = JSON.parse(document.getElementById("replayPayload").textContent);
const frameCount = payload.frame_count;
const reference = payload.reference;
const optimized = payload.optimized;
const mismatch = payload.comparison.mismatch;
const playback = payload.playback;
const rendering = payload.rendering;
const frameScheduleSeconds = playback.frame_schedule_seconds;
const playbackDurationSeconds = playback.duration_seconds;
const maxRobotCount = rendering.max_robot_count;
const lod = rendering.lod;
const timeline = document.getElementById("timeline");
const playPause = document.getElementById("playPause");
const speed = document.getElementById("speed");
const showTrails = document.getElementById("showTrails");
const showIds = document.getElementById("showIds");
const showRoutes = document.getElementById("showRoutes");
const timeReadout = document.getElementById("timeReadout");
const statusBadge = document.getElementById("statusBadge");
const renderHealth = document.getElementById("renderHealth");
const mismatchMarker = document.getElementById("mismatchMarker");
const referenceCanvas = document.getElementById("referenceCanvas");
const optimizedCanvas = document.getElementById("optimizedCanvas");
const mapImage = new Image();
let frameIndex = 0;
let playing = false;
let lastAnimationTime = null;
let playheadSeconds = 0;
let coalescedRenderFrames = 0;

showIds.checked = rendering.initial_controls.show_ids;
showIds.disabled = rendering.initial_controls.ids_forced_off;
showTrails.checked = rendering.initial_controls.show_trails;
showTrails.disabled = rendering.initial_controls.trails_forced_off;
showRoutes.checked = rendering.initial_controls.show_routes;
showRoutes.disabled = !rendering.routes_available;
document.getElementById("idsLabel").classList.toggle("disabled", showIds.disabled);
document.getElementById("trailsLabel").classList.toggle("disabled", showTrails.disabled);
document.getElementById("routesLabel").classList.toggle("disabled", showRoutes.disabled);

document.getElementById("referenceTitle").textContent = reference.label;
document.getElementById("optimizedTitle").textContent = optimized.label;
document.getElementById("referenceRevision").textContent = reference.revision || "revision unavailable";
document.getElementById("optimizedRevision").textContent = optimized.revision || "revision unavailable";
document.getElementById("mapInfo").textContent =
  `${payload.map.name} · ${payload.map.width_px}×${payload.map.height_px} · sha256 ${payload.map.sha256.slice(0,16)}…` +
  (payload.map.provenance_verified ? " · provenance verified" : " · hash not declared in both traces");
const environmentInfo = document.getElementById("environmentInfo");
if (payload.environment.available) {
  const metrics = payload.environment.map_metrics;
  const box = metrics.boundary_box_counting;
  const free = metrics.free_space;
  environmentInfo.textContent =
    `${payload.environment.family} · occupancy ${(100 * metrics.obstacle_occupancy).toFixed(2)}% · ` +
    `boundary density ${(100 * metrics.boundary_density).toFixed(2)}% · ` +
    `measured boundary box descriptor D ${box.dimension.toFixed(4)} ` +
    `(R² ${box.r_squared.toFixed(4)}) · ` +
    `${free.component_count_4_connected} connected component(s) · ` +
    `reachable free space ${(100 * free.reachable_fraction).toFixed(2)}%`;
} else {
  environmentInfo.textContent =
    "Registered obstacle descriptors were not supplied for this historical replay.";
}
document.getElementById("syncInfo").textContent =
  `${frameCount} aligned logical frames · ${formatSeconds(playbackDurationSeconds)} at 1× · ` +
  `recorded simulated-time clock` +
  (playback.equal_timestamp_transitions
    ? ` · ${playback.equal_timestamp_transitions} zero-delay frame transition(s)`
    : "");
document.getElementById("lodInfo").textContent =
  `maximum ${maxRobotCount} robots · IDs default/forced off above ` +
  `${lod.ids_default_off_above_robot_count}/${lod.ids_forced_off_above_robot_count}; ` +
  `trails default/forced off above ` +
  `${lod.trails_default_off_above_robot_count}/${lod.trails_forced_off_above_robot_count} · ` +
  `trails inspect at most ${lod.trail_robot_sample_limit_per_panel.toLocaleString()} robot samples per panel`;

if (payload.comparison.exact_semantic_match) {
  statusBadge.textContent = `Exact semantic match · ${frameCount} frames`;
  statusBadge.className = "good";
  document.getElementById("mismatchInfo").innerHTML =
    '<span class="good-text">No semantic mismatch.</span>';
} else {
  const where = mismatch.tick === null ? "header" : `tick ${mismatch.tick} / ${mismatch.phase}`;
  statusBadge.textContent = `Mismatch at ${where} · ${mismatch.path}`;
  statusBadge.className = "bad";
  document.getElementById("mismatchInfo").innerHTML =
    `<span class="bad-text">${escapeHtml(where)} · ${escapeHtml(mismatch.path)}</span><br>` +
    `<span class="subtle">${escapeHtml(mismatch.reason)}</span>`;
  mismatchMarker.style.display = "block";
  const percentage = frameCount <= 1 ? 0 : (mismatch.frame_index / (frameCount - 1)) * 100;
  mismatchMarker.style.left = `calc(${percentage}% - 1px)`;
}

timeline.max = String(Math.max(0, frameCount - 1));
mapImage.src = payload.map.data_uri;
mapImage.addEventListener("load", draw);

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[character]);
}

function colorForRobot(rid, alpha = 1) {
  const hue = (rid * 137.508 + 205) % 360;
  return `hsla(${hue},78%,47%,${alpha})`;
}

function worldPoint(xy, worldSize, width, height) {
  return [
    (xy[0] / worldSize + 0.5) * width,
    (xy[1] / worldSize + 0.5) * height
  ];
}

function fitCanvas(canvas) {
  const cssWidth = Math.max(320, Math.floor(canvas.getBoundingClientRect().width));
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const pixels = Math.floor(cssWidth * ratio);
  if (canvas.width !== pixels || canvas.height !== pixels) {
    canvas.width = pixels;
    canvas.height = pixels;
  }
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return [context, cssWidth, cssWidth];
}

function positionDifferences(index) {
  const left = new Map(reference.frames[index].robots.map(robot => [robot.rid, robot.xy]));
  const right = new Map(optimized.frames[index].robots.map(robot => [robot.rid, robot.xy]));
  const differences = new Set();
  for (const rid of new Set([...left.keys(), ...right.keys()])) {
    const a = left.get(rid);
    const b = right.get(rid);
    if (!a || !b || a[0] !== b[0] || a[1] !== b[1]) differences.add(rid);
  }
  return differences;
}

function drawTrails(context, trace, width, height, index) {
  if (!showTrails.checked) return;
  const frameBudget = Math.max(
    1,
    Math.min(
      lod.trail_frame_limit,
      Math.floor(
        lod.trail_robot_sample_limit_per_panel / Math.max(1, maxRobotCount)
      )
    )
  );
  const start = Math.max(0, index - frameBudget + 1);
  const tracks = new Map();
  for (let i = start; i <= index; i++) {
    const frame = trace.frames[i];
    if (frame.phase !== "post_update" && i !== start && i !== index) continue;
    for (const robot of frame.robots) {
      if (!tracks.has(robot.rid)) tracks.set(robot.rid, []);
      tracks.get(robot.rid).push(worldPoint(robot.xy, trace.world_size_m, width, height));
    }
  }
  context.lineWidth = 1.25;
  for (const [rid, points] of tracks) {
    if (points.length < 2) continue;
    context.beginPath();
    context.moveTo(points[0][0], points[0][1]);
    for (let i = 1; i < points.length; i++) context.lineTo(points[i][0], points[i][1]);
    context.strokeStyle = colorForRobot(rid, 0.38);
    context.stroke();
  }
}

function drawMarker(context, xy, trace, width, height, reached, waypoint) {
  if (!xy) return;
  const [x, y] = worldPoint(xy, trace.world_size_m, width, height);
  context.save();
  context.strokeStyle = reached ? "#3ee59a" : (waypoint ? "#f4bd4f" : "#2c6fdf");
  context.lineWidth = 1.4;
  context.beginPath();
  if (waypoint) {
    context.moveTo(x, y - 4);
    context.lineTo(x + 4, y);
    context.lineTo(x, y + 4);
    context.lineTo(x - 4, y);
    context.closePath();
  } else {
    context.moveTo(x - 5, y); context.lineTo(x + 5, y);
    context.moveTo(x, y - 5); context.lineTo(x, y + 5);
  }
  context.stroke();
  context.restore();
}

function drawRoutes(context, frame, trace, width, height) {
  if (!showRoutes.checked) return;
  context.save();
  context.lineWidth = 1;
  for (const robot of frame.robots) {
    const route = [robot.xy];
    if (robot.current_waypoint) route.push(robot.current_waypoint);
    if (robot.final_goal) route.push(robot.final_goal);
    if (route.length > 1) {
      const first = worldPoint(route[0], trace.world_size_m, width, height);
      context.setLineDash([4, 4]);
      context.beginPath();
      context.moveTo(first[0], first[1]);
      for (let index = 1; index < route.length; index++) {
        const point = worldPoint(route[index], trace.world_size_m, width, height);
        context.lineTo(point[0], point[1]);
      }
      context.strokeStyle = colorForRobot(robot.rid, 0.48);
      context.stroke();
    }
    context.setLineDash([]);
    drawMarker(
      context, robot.current_waypoint, trace, width, height,
      robot.goal_reached, true
    );
    drawMarker(
      context, robot.final_goal, trace, width, height,
      robot.goal_reached, false
    );
  }
  context.restore();
}

function drawPanel(canvas, trace, summaryElement, index, differingIds) {
  const [context, width, height] = fitCanvas(canvas);
  context.clearRect(0, 0, width, height);
  if (mapImage.complete && mapImage.naturalWidth) {
    context.imageSmoothingEnabled = false;
    context.drawImage(mapImage, 0, 0, width, height);
  } else {
    context.fillStyle = "#fff";
    context.fillRect(0, 0, width, height);
  }
  context.strokeStyle = "rgba(10,20,30,.35)";
  context.strokeRect(.5, .5, width - 1, height - 1);
  drawTrails(context, trace, width, height, index);

  const frame = trace.frames[index];
  drawRoutes(context, frame, trace, width, height);
  for (const robot of frame.robots) {
    const [x, y] = worldPoint(robot.xy, trace.world_size_m, width, height);
    const radius = Math.max(3.2, robot.radius_m / trace.world_size_m * width);
    context.beginPath();
    context.arc(x, y, radius, 0, Math.PI * 2);
    context.fillStyle = robot.goal_reached ? "#38bb7f" : colorForRobot(robot.rid, .92);
    context.fill();
    context.strokeStyle = "#08111c";
    context.lineWidth = 1;
    context.stroke();

    const direction = Math.max(radius + 4, 8);
    context.beginPath();
    context.moveTo(x, y);
    context.lineTo(
      x + Math.cos(robot.bearing) * direction,
      y + Math.sin(robot.bearing) * direction
    );
    context.strokeStyle = "#07101a";
    context.lineWidth = 1.4;
    context.stroke();

    if (differingIds.has(robot.rid)) {
      context.beginPath();
      context.arc(x, y, radius + 4, 0, Math.PI * 2);
      context.strokeStyle = "#ff2f76";
      context.lineWidth = 2.2;
      context.stroke();
    }
    if (showIds.checked) {
      context.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
      context.textAlign = "center";
      context.textBaseline = "bottom";
      context.lineWidth = 3;
      context.strokeStyle = "rgba(255,255,255,.9)";
      context.strokeText(String(robot.rid), x, y - radius - 2);
      context.fillStyle = "#07101a";
      context.fillText(String(robot.rid), x, y - radius - 2);
    }
  }
  const metrics = frame.metrics;
  const pairClearance = formatMetric(metrics.frame_min_pair_clearance_m, "m");
  const obstacleClearance = formatMetric(metrics.frame_min_obstacle_clearance_m, "m");
  summaryElement.textContent =
    `robots ${metrics.robot_count ?? frame.robots.length}  goals ${metrics.goals_reached ?? "—"}\n` +
    `collisions pair ${metrics.pair_collisions ?? "—"}  obstacle ${metrics.obstacle_collisions ?? "—"}\n` +
    `clearance pair ${pairClearance}  obstacle ${obstacleClearance}`;
}

function formatMetric(value, unit = "") {
  return typeof value === "number" && Number.isFinite(value)
    ? `${value.toFixed(3)}${unit}`
    : "—";
}

function formatSeconds(value) {
  return `${Number(value).toFixed(3)} s`;
}

function updateTimeReadout() {
  const absoluteSimulatedTime =
    playback.start_simulated_time_seconds + playheadSeconds;
  timeReadout.textContent =
    `simulated-time progress ${formatSeconds(playheadSeconds)} / ` +
    `${formatSeconds(playbackDurationSeconds)} · trace clock ` +
    `${formatSeconds(absoluteSimulatedTime)} · speed ${speed.value}×`;
}

function draw() {
  if (!frameCount) return;
  frameIndex = Math.max(0, Math.min(frameIndex, frameCount - 1));
  timeline.value = String(frameIndex);
  const leftFrame = reference.frames[frameIndex];
  const differences = positionDifferences(frameIndex);
  drawPanel(
    referenceCanvas, reference,
    document.getElementById("referenceSummary"), frameIndex, differences
  );
  drawPanel(
    optimizedCanvas, optimized,
    document.getElementById("optimizedSummary"), frameIndex, differences
  );
  document.getElementById("frameReadout").textContent =
    `frame ${frameIndex + 1}/${frameCount} · tick ${leftFrame.tick} · ${leftFrame.phase} · ` +
    `${leftFrame.simulated_time_seconds.toFixed(3)} s · positional differences ${differences.size}`;
  updateTimeReadout();
  const mismatchActive = mismatch && frameIndex >= mismatch.frame_index;
  document.getElementById("referencePanel").classList.toggle("mismatch", Boolean(mismatchActive));
  document.getElementById("optimizedPanel").classList.toggle("mismatch", Boolean(mismatchActive));
}

function setPlaying(value) {
  playing = value;
  playPause.textContent = playing ? "❚❚ Pause" : "▶ Play";
  lastAnimationTime = playing ? performance.now() : null;
}

function step(delta) {
  setPlaying(false);
  frameIndex = Math.max(0, Math.min(frameCount - 1, frameIndex + delta));
  playheadSeconds = frameScheduleSeconds[frameIndex] || 0;
  draw();
}

function scheduledFrameAt(playhead) {
  let lower = 0;
  let upper = frameScheduleSeconds.length;
  while (lower < upper) {
    const middle = Math.floor((lower + upper) / 2);
    if (frameScheduleSeconds[middle] <= playhead) {
      lower = middle + 1;
    } else {
      upper = middle;
    }
  }
  return Math.max(0, lower - 1);
}

function animate(timestamp) {
  if (!playing) return;
  if (lastAnimationTime === null) lastAnimationTime = timestamp;
  const elapsed = Math.max(0, timestamp - lastAnimationTime);
  lastAnimationTime = timestamp;
  playheadSeconds = Math.min(
    playbackDurationSeconds,
    playheadSeconds + elapsed / 1000 * Number(speed.value)
  );
  const scheduledFrame = scheduledFrameAt(playheadSeconds);
  if (scheduledFrame !== frameIndex) {
    if (scheduledFrame > frameIndex + 1) {
      coalescedRenderFrames += scheduledFrame - frameIndex - 1;
      renderHealth.textContent =
        `Replay renderer coalesced ${coalescedRenderFrames} recorded frame transition(s) ` +
        `to keep pace with simulated time; trace data is unchanged.`;
      renderHealth.className = "warn-text";
    }
    frameIndex = scheduledFrame;
    draw();
  } else {
    updateTimeReadout();
  }
  if (playheadSeconds >= playbackDurationSeconds) {
    frameIndex = Math.max(0, frameCount - 1);
    playheadSeconds = playbackDurationSeconds;
    draw();
    setPlaying(false);
  }
  if (playing) requestAnimationFrame(animate);
}

playPause.addEventListener("click", () => {
  if (frameIndex >= frameCount - 1 && !playing) {
    frameIndex = 0;
    playheadSeconds = 0;
    coalescedRenderFrames = 0;
    renderHealth.textContent = "No render frames coalesced.";
    renderHealth.className = "";
  }
  setPlaying(!playing);
  draw();
  if (playing) requestAnimationFrame(animate);
});
document.getElementById("stepBack").addEventListener("click", () => step(-1));
document.getElementById("stepForward").addEventListener("click", () => step(1));
timeline.addEventListener("input", () => {
  setPlaying(false);
  frameIndex = Number(timeline.value);
  playheadSeconds = frameScheduleSeconds[frameIndex] || 0;
  draw();
});
showTrails.addEventListener("change", draw);
showIds.addEventListener("change", draw);
showRoutes.addEventListener("change", draw);
speed.addEventListener("change", () => {
  if (playing) lastAnimationTime = performance.now();
  updateTimeReadout();
});
window.addEventListener("resize", draw);
window.addEventListener("keydown", event => {
  if (event.target.matches("input,select,button")) return;
  if (event.code === "Space") {
    event.preventDefault();
    playPause.click();
  } else if (event.code === "ArrowLeft") {
    event.preventDefault();
    step(-1);
  } else if (event.code === "ArrowRight") {
    event.preventDefault();
    step(1);
  }
});
draw();
</script>
</body>
</html>
"""
