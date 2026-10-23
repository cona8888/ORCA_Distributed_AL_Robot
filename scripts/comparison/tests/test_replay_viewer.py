from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "scripts" / "comparison" / "build_replay_viewer.py"
MAP_PATH = (
    REPO_ROOT
    / "comparison"
    / "environments"
    / "open"
    / "calibration"
    / "map.png"
)
SPEC = importlib.util.spec_from_file_location("build_replay_viewer", MODULE_PATH)
assert SPEC and SPEC.loader
VIEWER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VIEWER
SPEC.loader.exec_module(VIEWER)

TRACE_SCHEMA_V2 = "dancers-comparison-trace-v2"
TRACE_SCHEMA_V3 = "dancers-comparison-trace-v3"
V2_DIGEST_ALGORITHM = (
    "SHA-256 over canonical nlohmann JSON serialization of each "
    "implementation-neutral robot frame"
)
V3_DIGEST_ALGORITHM = (
    "SHA-256 over robot-semantic-digest-v2 canonical typed binary encoding"
)


def header(
    detail: str,
    implementation: str,
    map_sha256: str,
    *,
    schema_version: str = TRACE_SCHEMA_V3,
) -> dict:
    observer = {
        "trace_detail": detail,
        "timing_eligible": False,
        "digest_algorithm": (
            V2_DIGEST_ALGORITHM
            if schema_version == TRACE_SCHEMA_V2
            else V3_DIGEST_ALGORITHM
        ),
    }
    if schema_version == TRACE_SCHEMA_V3:
        observer.update(
            {
                "future_state_scope": "common future-affecting state",
                "message_checksum_scope": "common live-message state",
            }
        )
    return {
        "type": "header",
        "schema_version": schema_version,
        "metadata": {
            "implementation_id": implementation,
            "algorithm_revision": f"{implementation}-revision",
            "binary_sha256": ("1" if implementation == "reference" else "2")
            * 64,
            "config_sha256": "3" * 64,
            "map_sha256": map_sha256,
            "pair_id": "open-n2-seed01",
            "robot_count": 2,
            "scenario_sha256": "4" * 64,
            "seed": 1,
            "timestep_seconds": 0.1,
            "timesteps": 2,
        },
        "resolved_config": {
            "config_file": (
                f"/tmp/{implementation}/config/comparison/laptop_smoke.yaml"
            ),
            "scenario_file": (
                f"/tmp/{implementation}/comparison/environments/"
                "open/calibration/scenario.json"
            ),
            "obstacle_file": (
                f"/tmp/{implementation}/comparison/environments/"
                "open/calibration/map.png"
            ),
            "world_size_m": 100,
            "rng_seed": 1,
        },
        "observer": observer,
    }


def robot(rid: int, x: float, y: float, *, full: bool = False) -> dict:
    record = {
        "rid": rid,
        "state": [x, y, 0.25, 0.0, 0.0, 0.0],
        "state_hex": ["0" * 16] * 6,
        "bearing": 0.25,
        "radius_m": 1.0,
        "current_waypoint": [10.0, 10.0, 0.0],
        "final_goal": [20.0, 20.0],
        "goal_reached": False,
    }
    if full:
        record.update(
            {
                "decision": rid,
                "layers": [{"id": 0, "variables": [], "factors": []}],
                "remaining_waypoints": [],
            }
        )
    return record


def frame(tick: int, phase: str, *, x_offset: float = 0.0, full: bool = False) -> dict:
    return {
        "type": "frame",
        "tick": tick,
        "phase": phase,
        "simulated_time_seconds": tick * 0.1,
        "robots": [
            robot(0, -10.0 + tick + x_offset, -5.0, full=full),
            robot(1, 10.0, 5.0 + tick, full=full),
        ],
        "metrics": {
            "robot_count": 2,
            "updated_this_frame": phase == "post_update",
            "frame_min_pair_clearance_m": 12.5,
            "frame_min_obstacle_clearance_m": 7.25,
            "pair_collisions": 0,
            "obstacle_collisions": 0,
            "goals_reached": 0,
            "all_goals_reached": False,
            "cumulative_pair_collision_exposure_seconds": 0.0,
            "cumulative_obstacle_collision_exposure_seconds": 0.0,
        },
        "rng_state": {"normal_mt19937": "same"},
        "simulator_state": {"iterations": tick},
    }


def records(
    implementation: str,
    map_sha256: str,
    *,
    detail: str = "positions",
    mismatch: bool = False,
    extra_pair: bool = False,
    schema_version: str = TRACE_SCHEMA_V3,
) -> list[dict]:
    full = detail == "full"
    output = [
        header(
            detail,
            implementation,
            map_sha256,
            schema_version=schema_version,
        ),
        frame(0, "pre_update", full=full),
        frame(
            1,
            "post_update",
            x_offset=0.5 if mismatch else 0.0,
            full=full,
        ),
        frame(1, "pre_update", full=full),
        frame(2, "post_update", full=full),
    ]
    if extra_pair:
        output.extend(
            [
                frame(2, "pre_update", full=full),
                frame(3, "post_update", full=full),
            ]
        )
    return output


def write_trace(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(
            json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def set_simulated_times(
    rows: list[dict], simulated_times: list[float]
) -> list[dict]:
    updated = copy.deepcopy(rows)
    assert len(updated) - 1 == len(simulated_times)
    for record, simulated_time in zip(updated[1:], simulated_times):
        record["simulated_time_seconds"] = simulated_time
    return updated


def set_header_metadata(rows: list[dict], **updates: object) -> list[dict]:
    changed = copy.deepcopy(rows)
    changed[0]["metadata"].update(updates)
    return changed


def set_robot_count(rows: list[dict], robot_count: int) -> list[dict]:
    updated = copy.deepcopy(rows)
    for frame_record in updated[1:]:
        tick = frame_record["tick"]
        frame_record["robots"] = [
            robot(
                rid,
                -40.0 + float(rid % 80),
                -40.0 + float((rid // 80 + tick) % 80),
            )
            for rid in range(robot_count)
        ]
        frame_record["metrics"]["robot_count"] = robot_count
    return updated


def remove_routes(rows: list[dict]) -> list[dict]:
    updated = copy.deepcopy(rows)
    for frame_record in updated[1:]:
        for robot_record in frame_record["robots"]:
            robot_record["current_waypoint"] = None
            robot_record["final_goal"] = None
    return updated


def embedded_payload(html_text: str) -> dict:
    match = re.search(
        r'<script id="replayPayload" type="application/json">(.*?)</script>',
        html_text,
        flags=re.DOTALL,
    )
    assert match
    return json.loads(match.group(1))


def build_to_file(
    reference: Path,
    optimized: Path,
    map_path: Path,
    output: Path,
    **kwargs: object,
) -> object:
    result = VIEWER.build_replay_viewer(
        reference,
        optimized,
        map_path,
        **kwargs,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write(result.html_bytes)
    return result


class ReplayViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.map_sha256 = hashlib.sha256(MAP_PATH.read_bytes()).hexdigest()

    def test_builds_self_contained_synchronized_position_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            reference = directory / "reference.jsonl"
            optimized = directory / "optimized.jsonl"
            output = directory / "replay.html"
            write_trace(
                reference, records("reference", self.map_sha256)
            )
            write_trace(
                optimized, records("optimized", self.map_sha256)
            )

            result = build_to_file(
                reference,
                optimized,
                MAP_PATH,
                output,
                title="Laptop replay",
                environment_family="open",
                environment_descriptors={
                    "obstacle_occupancy": 0.061039,
                    "boundary_density": 0.003876,
                    "boundary_box_counting": {
                        "dimension": 1.0035231,
                        "r_squared": 0.99998818,
                    },
                    "free_space": {
                        "component_count_4_connected": 1,
                        "reachable_fraction": 1.0,
                    },
                },
            )
            self.assertTrue(result.exact_semantic_match)
            self.assertEqual(result.frame_count, 4)
            self.assertEqual(result.robot_samples, 16)
            text = output.read_text(encoding="utf-8")
            for control in (
                'id="playPause"',
                'id="speed"',
                'id="stepBack"',
                'id="stepForward"',
                'id="timeline"',
                'id="showTrails"',
                'id="showIds"',
                'id="showRoutes"',
                'id="timeReadout"',
                'id="diagnosticBadge"',
                'id="renderHealth"',
                'id="environmentInfo"',
                'id="lodInfo"',
                'id="mismatchMarker"',
                'id="referenceSummary"',
                'id="optimizedSummary"',
            ):
                self.assertIn(control, text)
            self.assertIsNone(re.search(r"(?:https?|wss?)://", text))
            self.assertIn("connect-src 'none'", text)
            for required_badge in (
                "DIAGNOSTIC REPLAY",
                "NOT LIVE SOLVER TIMING",
                "RENDERING EXCLUDED FROM COMPUTE",
            ):
                self.assertIn(required_badge, text)
            self.assertIn("Replay renderer coalesced", text)
            self.assertIn("trace data is unchanged", text)
            self.assertNotIn("BASE_FRAMES_PER_SECOND", text)
            payload = embedded_payload(text)
            self.assertEqual(
                payload["schema_version"], "dancers-replay-viewer-v2"
            )
            self.assertTrue(
                payload["map"]["data_uri"].startswith(
                    "data:image/png;base64,"
                )
            )
            self.assertTrue(payload["map"]["provenance_verified"])
            self.assertEqual(payload["frame_count"], 4)
            self.assertIsNone(payload["comparison"]["mismatch"])
            self.assertEqual(
                payload["playback"]["frame_schedule_seconds"],
                [0.0, 0.1, 0.1, 0.2],
            )
            self.assertEqual(payload["playback"]["duration_seconds"], 0.2)
            self.assertEqual(
                payload["playback"]["equal_timestamp_transitions"], 1
            )
            self.assertTrue(
                payload["playback"]["paired_schedule_verified"]
            )
            self.assertTrue(
                payload["comparison"][
                    "paired_input_provenance_verified"
                ]
            )
            self.assertTrue(
                payload["comparison"]["paired_robot_id_sets_verified"]
            )
            self.assertTrue(payload["environment"]["available"])
            self.assertEqual(
                payload["environment"]["descriptor_source"],
                "effective_runtime_1000.map_metrics",
            )
            self.assertEqual(
                payload["environment"]["map_metrics"][
                    "boundary_box_counting"
                ]["dimension"],
                1.0035231,
            )
            self.assertIn(
                "measured boundary box descriptor D",
                text,
            )
            self.assertTrue(payload["rendering"]["diagnostic_only"])
            self.assertFalse(
                payload["rendering"]["included_in_compute_claims"]
            )
            self.assertTrue(payload["rendering"]["routes_available"])
            self.assertTrue(
                payload["rendering"]["initial_controls"]["show_routes"]
            )
            self.assertEqual(
                payload["reference"]["frames"][1]["robots"][0]["xy"],
                [-9.0, -5.0],
            )

    def test_historical_v2_positions_trace_remains_replayable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            reference = directory / "reference-v2.jsonl"
            optimized = directory / "optimized-v2.jsonl"
            output = directory / "replay-v2.html"
            write_trace(
                reference,
                records(
                    "reference",
                    self.map_sha256,
                    schema_version=TRACE_SCHEMA_V2,
                ),
            )
            write_trace(
                optimized,
                records(
                    "optimized",
                    self.map_sha256,
                    schema_version=TRACE_SCHEMA_V2,
                ),
            )

            result = build_to_file(
                reference,
                optimized,
                MAP_PATH,
                output,
            )
            self.assertTrue(result.exact_semantic_match)
            payload = embedded_payload(
                output.read_text(encoding="utf-8")
            )
            self.assertEqual(
                payload["reference"]["trace_detail"],
                "positions",
            )
            self.assertEqual(payload["frame_count"], 4)

    def test_semantic_mutation_builds_visible_first_mismatch_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            reference = directory / "reference.jsonl"
            optimized = directory / "optimized.jsonl"
            output = directory / "replay.html"
            write_trace(
                reference, records("reference", self.map_sha256)
            )
            write_trace(
                optimized,
                records("optimized", self.map_sha256, mismatch=True),
            )

            result = build_to_file(
                reference, optimized, MAP_PATH, output
            )
            self.assertFalse(result.exact_semantic_match)
            self.assertEqual(result.mismatch_tick, 1)
            payload = embedded_payload(output.read_text(encoding="utf-8"))
            mismatch = payload["comparison"]["mismatch"]
            self.assertEqual(mismatch["tick"], 1)
            self.assertEqual(mismatch["phase"], "post_update")
            self.assertEqual(mismatch["frame_index"], 1)
            self.assertEqual(
                mismatch["path"], "$.robots[0].state[0]"
            )

    def test_full_trace_is_projected_without_embedding_graph_internals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            reference = directory / "reference.jsonl"
            optimized = directory / "optimized.jsonl"
            output = directory / "replay.html"
            write_trace(
                reference,
                records("reference", self.map_sha256, detail="full"),
            )
            write_trace(
                optimized,
                records("optimized", self.map_sha256, detail="full"),
            )
            build_to_file(
                reference, optimized, MAP_PATH, output
            )
            payload = embedded_payload(output.read_text(encoding="utf-8"))
            projected = payload["reference"]["frames"][0]["robots"][0]
            self.assertEqual(payload["reference"]["trace_detail"], "full")
            self.assertNotIn("layers", projected)
            self.assertIn("xy", projected)
            self.assertIn("decision", projected)

    def test_digest_malformed_and_unaligned_traces_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            reference = directory / "reference.jsonl"
            optimized = directory / "optimized.jsonl"
            output = directory / "replay.html"

            digest_rows = records(
                "reference", self.map_sha256, detail="positions"
            )
            digest_rows[0]["observer"]["trace_detail"] = "digest"
            for row in digest_rows[1:]:
                row["robots"] = [
                    {
                        "rid": 0,
                        "state_schema": "robot-semantic-digest-v2",
                        "state_sha256": "a" * 64,
                    }
                ]
            write_trace(reference, digest_rows)
            write_trace(
                optimized, records("optimized", self.map_sha256)
            )
            with self.assertRaises(VIEWER.ViewerInputError):
                build_to_file(
                    reference, optimized, MAP_PATH, output
                )

            malformed = records("reference", self.map_sha256)
            malformed.pop()
            write_trace(reference, malformed)
            with self.assertRaises(VIEWER.trace_tools.TraceFormatError):
                build_to_file(
                    reference, optimized, MAP_PATH, output
                )

            write_trace(
                reference, records("reference", self.map_sha256)
            )
            write_trace(
                optimized,
                records(
                    "optimized",
                    self.map_sha256,
                    extra_pair=True,
                ),
            )
            with self.assertRaises(VIEWER.ViewerInputError):
                build_to_file(
                    reference, optimized, MAP_PATH, output
                )

    def test_playback_schedule_uses_recorded_time_and_accepts_equal_times(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            reference = directory / "reference.jsonl"
            optimized = directory / "optimized.jsonl"
            output = directory / "replay.html"
            simulated_times = [8.0, 8.25, 8.25, 9.0]
            write_trace(
                reference,
                set_simulated_times(
                    records("reference", self.map_sha256),
                    simulated_times,
                ),
            )
            write_trace(
                optimized,
                set_simulated_times(
                    records("optimized", self.map_sha256),
                    simulated_times,
                ),
            )

            build_to_file(
                reference, optimized, MAP_PATH, output
            )
            text = output.read_text(encoding="utf-8")
            payload = embedded_payload(text)
            playback = payload["playback"]
            self.assertEqual(
                playback["clock"], "simulated_time_seconds"
            )
            self.assertEqual(
                playback["frame_schedule_seconds"],
                [0.0, 0.25, 0.25, 1.0],
            )
            self.assertEqual(
                playback["start_simulated_time_seconds"], 8.0
            )
            self.assertEqual(
                playback["end_simulated_time_seconds"], 9.0
            )
            self.assertEqual(playback["duration_seconds"], 1.0)
            self.assertEqual(playback["equal_timestamp_transitions"], 1)
            self.assertIn(
                "playheadSeconds + elapsed / 1000 * Number(speed.value)",
                text,
            )
            self.assertIn(
                "simulated-time progress",
                text,
            )

    def test_decreasing_simulated_time_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            reference = directory / "reference.jsonl"
            optimized = directory / "optimized.jsonl"
            output = directory / "replay.html"
            decreasing = [0.0, 0.2, 0.1, 0.3]
            write_trace(
                reference,
                set_simulated_times(
                    records("reference", self.map_sha256),
                    decreasing,
                ),
            )
            write_trace(
                optimized,
                set_simulated_times(
                    records("optimized", self.map_sha256),
                    decreasing,
                ),
            )

            with self.assertRaisesRegex(
                VIEWER.ViewerInputError,
                "simulated_time_seconds must be nondecreasing",
            ):
                build_to_file(
                    reference, optimized, MAP_PATH, output
                )
            self.assertFalse(output.exists())

    def test_pair_provenance_mismatch_is_rejected(self) -> None:
        mutations = {
            "pair_id": "another-pair",
            "config_sha256": "a" * 64,
            "scenario_sha256": "b" * 64,
            "seed": 9,
            "robot_count": 99,
        }
        for field, changed_value in mutations.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                directory = Path(tmp)
                reference = directory / "reference.jsonl"
                optimized = directory / "optimized.jsonl"
                output = directory / "replay.html"
                write_trace(
                    reference,
                    records("reference", self.map_sha256),
                )
                write_trace(
                    optimized,
                    set_header_metadata(
                        records("optimized", self.map_sha256),
                        **{field: changed_value},
                    ),
                )

                with self.assertRaisesRegex(
                    VIEWER.ViewerInputError,
                    "paired trace provenance differs",
                ):
                    build_to_file(
                        reference, optimized, MAP_PATH, output
                    )
                self.assertFalse(output.exists())

    def test_pair_simulated_time_schedule_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            reference = directory / "reference.jsonl"
            optimized = directory / "optimized.jsonl"
            output = directory / "replay.html"
            write_trace(
                reference,
                set_simulated_times(
                    records("reference", self.map_sha256),
                    [0.0, 0.1, 0.1, 0.2],
                ),
            )
            write_trace(
                optimized,
                set_simulated_times(
                    records("optimized", self.map_sha256),
                    [0.0, 0.15, 0.15, 0.3],
                ),
            )

            with self.assertRaisesRegex(
                VIEWER.ViewerInputError,
                "paired simulated-time schedules differ",
            ):
                build_to_file(
                    reference, optimized, MAP_PATH, output
                )
            self.assertFalse(output.exists())

    def test_pair_robot_id_set_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            reference = directory / "reference.jsonl"
            optimized = directory / "optimized.jsonl"
            output = directory / "replay.html"
            reference_rows = records("reference", self.map_sha256)
            optimized_rows = records("optimized", self.map_sha256)
            optimized_rows[2]["robots"][0]["rid"] = 2
            write_trace(reference, reference_rows)
            write_trace(optimized, optimized_rows)

            with self.assertRaisesRegex(
                VIEWER.ViewerInputError,
                "paired robot-ID sets differ at frame 1",
            ):
                build_to_file(
                    reference, optimized, MAP_PATH, output
                )
            self.assertFalse(output.exists())

    def test_pair_trace_detail_mismatch_is_rejected(self) -> None:
        for reference_detail, optimized_detail in (
            ("positions", "full"),
            ("full", "positions"),
        ):
            with (
                self.subTest(
                    reference_detail=reference_detail,
                    optimized_detail=optimized_detail,
                ),
                tempfile.TemporaryDirectory() as tmp,
            ):
                directory = Path(tmp)
                reference = directory / "reference.jsonl"
                optimized = directory / "optimized.jsonl"
                output = directory / "replay.html"
                write_trace(
                    reference,
                    records(
                        "reference",
                        self.map_sha256,
                        detail=reference_detail,
                    ),
                )
                write_trace(
                    optimized,
                    records(
                        "optimized",
                        self.map_sha256,
                        detail=optimized_detail,
                    ),
                )

                with self.assertRaisesRegex(
                    VIEWER.ViewerInputError,
                    "paired traces use different trace detail",
                ):
                    build_to_file(
                        reference, optimized, MAP_PATH, output
                    )
                self.assertFalse(output.exists())

    def test_title_placeholder_text_is_not_reexpanded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            reference = directory / "reference.jsonl"
            optimized = directory / "optimized.jsonl"
            output = directory / "replay.html"
            write_trace(
                reference, records("reference", self.map_sha256)
            )
            write_trace(
                optimized, records("optimized", self.map_sha256)
            )
            title = "__PAYLOAD__ <literal>"

            build_to_file(
                reference,
                optimized,
                MAP_PATH,
                output,
                title=title,
            )
            text = output.read_text(encoding="utf-8")
            self.assertIn(
                "<title>__PAYLOAD__ &lt;literal&gt;</title>",
                text,
            )
            self.assertEqual(embedded_payload(text)["title"], title)

    def test_large_swarm_lod_defaults_and_hard_limits_are_embedded(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for robot_count, forced_off in ((129, False), (513, True)):
                reference = directory / f"reference-{robot_count}.jsonl"
                optimized = directory / f"optimized-{robot_count}.jsonl"
                output = directory / f"replay-{robot_count}.html"
                write_trace(
                    reference,
                    set_robot_count(
                        records("reference", self.map_sha256),
                        robot_count,
                    ),
                )
                write_trace(
                    optimized,
                    set_robot_count(
                        records("optimized", self.map_sha256),
                        robot_count,
                    ),
                )

                build_to_file(
                    reference, optimized, MAP_PATH, output
                )
                text = output.read_text(encoding="utf-8")
                rendering = embedded_payload(text)["rendering"]
                controls = rendering["initial_controls"]
                lod = rendering["lod"]
                self.assertEqual(
                    rendering["max_robot_count"], robot_count
                )
                self.assertFalse(controls["show_ids"])
                self.assertFalse(controls["show_trails"])
                self.assertEqual(
                    controls["ids_forced_off"], forced_off
                )
                self.assertEqual(
                    controls["trails_forced_off"], forced_off
                )
                self.assertEqual(
                    lod["ids_default_off_above_robot_count"], 128
                )
                self.assertEqual(
                    lod["trails_default_off_above_robot_count"], 128
                )
                self.assertEqual(
                    lod["ids_forced_off_above_robot_count"], 512
                )
                self.assertEqual(
                    lod["trails_forced_off_above_robot_count"], 512
                )
                self.assertEqual(lod["trail_frame_limit"], 500)
                self.assertEqual(
                    lod["trail_robot_sample_limit_per_panel"], 50_000
                )
                self.assertIn(
                    "lod.trail_robot_sample_limit_per_panel / "
                    "Math.max(1, maxRobotCount)",
                    text,
                )

    def test_route_control_disables_when_trace_has_no_route_fields(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            reference = directory / "reference.jsonl"
            optimized = directory / "optimized.jsonl"
            output = directory / "replay.html"
            write_trace(
                reference,
                remove_routes(records("reference", self.map_sha256)),
            )
            write_trace(
                optimized,
                remove_routes(records("optimized", self.map_sha256)),
            )

            build_to_file(
                reference, optimized, MAP_PATH, output
            )
            text = output.read_text(encoding="utf-8")
            rendering = embedded_payload(text)["rendering"]
            self.assertFalse(rendering["routes_available"])
            self.assertFalse(
                rendering["initial_controls"]["show_routes"]
            )
            self.assertIn(
                "showRoutes.disabled = !rendering.routes_available",
                text,
            )
            self.assertIn(
                "robot.current_waypoint",
                text,
            )
            self.assertIn("robot.final_goal", text)

    def test_output_is_deterministic_and_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            reference = directory / "reference.jsonl"
            optimized = directory / "optimized.jsonl"
            first_output = directory / "replay-1.html"
            second_output = directory / "replay-2.html"
            protected_output = directory / "protected.html"
            write_trace(
                reference, records("reference", self.map_sha256)
            )
            write_trace(
                optimized, records("optimized", self.map_sha256)
            )

            build_to_file(
                reference, optimized, MAP_PATH, first_output
            )
            build_to_file(
                reference, optimized, MAP_PATH, second_output
            )
            self.assertEqual(
                first_output.read_bytes(), second_output.read_bytes()
            )

            protected_output.write_bytes(b"preserve this artifact")
            with self.assertRaises(FileExistsError):
                build_to_file(
                    reference, optimized, MAP_PATH, protected_output
                )
            self.assertEqual(
                protected_output.read_bytes(), b"preserve this artifact"
            )

    def test_renderer_has_no_cli_or_filesystem_write_surface(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_roots: set[str] = set()
        call_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                imported_roots.add((node.module or "").split(".", 1)[0])
            elif isinstance(node, ast.Call):
                function = node.func
                if isinstance(function, ast.Name):
                    call_names.add(function.id)
                elif isinstance(function, ast.Attribute):
                    call_names.add(function.attr)
        self.assertFalse(
            imported_roots
            & {
                "argparse",
                "shutil",
                "subprocess",
                "tempfile",
            }
        )
        self.assertFalse(
            call_names
            & {
                "mkdir",
                "open",
                "rename",
                "touch",
                "unlink",
                "write",
                "write_bytes",
                "write_text",
                "writelines",
            }
        )
        self.assertFalse(hasattr(VIEWER, "main"))

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            reference = directory / "reference.jsonl"
            optimized = directory / "optimized.jsonl"
            forbidden_output = (
                directory / "results" / "comparison" / "replay.html"
            )
            write_trace(
                reference, records("reference", self.map_sha256)
            )
            write_trace(
                optimized, records("optimized", self.map_sha256)
            )
            with self.assertRaises(TypeError):
                VIEWER.build_replay_viewer(
                    reference,
                    optimized,
                    MAP_PATH,
                    forbidden_output,
                )
            self.assertFalse(forbidden_output.exists())

    def test_wrong_map_and_viewer_bounds_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            reference = directory / "reference.jsonl"
            optimized = directory / "optimized.jsonl"
            output = directory / "replay.html"
            write_trace(
                reference, records("reference", "0" * 64)
            )
            write_trace(
                optimized, records("optimized", "0" * 64)
            )
            with self.assertRaises(VIEWER.ViewerInputError):
                build_to_file(
                    reference, optimized, MAP_PATH, output
                )

            write_trace(
                reference, records("reference", self.map_sha256)
            )
            write_trace(
                optimized, records("optimized", self.map_sha256)
            )
            with self.assertRaises(VIEWER.ViewerInputError):
                build_to_file(
                    reference,
                    optimized,
                    MAP_PATH,
                    output,
                    max_frames=2,
                )
            with self.assertRaises(VIEWER.ViewerInputError):
                build_to_file(
                    reference,
                    optimized,
                    MAP_PATH,
                    output,
                    max_robot_samples=4,
                )


if __name__ == "__main__":
    unittest.main()
