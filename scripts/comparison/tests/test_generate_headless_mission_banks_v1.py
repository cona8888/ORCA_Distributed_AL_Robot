from __future__ import annotations

import ast
import copy
import hashlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path, PurePosixPath
from unittest import mock

from scripts.comparison import generate_headless_mission_banks_v1 as MISSIONS


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
SOURCE_PATH = (
    REPOSITORY_ROOT
    / "scripts"
    / "comparison"
    / "generate_headless_mission_banks_v1.py"
)


def design_for(
    families: tuple[str, ...] = MISSIONS.FAMILIES,
    seeds: tuple[int, ...] = (1, 2),
    counts: tuple[int, ...] = (8, 12, 16, 24, 32),
) -> dict[str, object]:
    return {
        "phase": "calibration",
        "requests": [
            {"counts": list(counts), "family": family, "seed": seed}
            for family in families
            for seed in seeds
        ],
        "schema_version": MISSIONS.DESIGN_SCHEMA,
    }


def parsed_file(
    compilation: MISSIONS.MissionCompilation, path: PurePosixPath
) -> dict[str, object]:
    return json.loads(compilation.by_path()[path].data)


def with_mutated_manifest(
    compilation: MISSIONS.MissionCompilation,
    mutate: object,
) -> MISSIONS.MissionCompilation:
    manifest = copy.deepcopy(compilation.manifest)
    mutate(manifest)
    data = MISSIONS.canonical_json_bytes(manifest)
    manifest_path = PurePosixPath("manifest.json")
    old_file = compilation.by_path()[manifest_path]
    new_file = replace(
        old_file,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
    )
    return replace(
        compilation,
        files=tuple(
            new_file if item.relative_path == manifest_path else item
            for item in compilation.files
        ),
        manifest=manifest,
        manifest_bytes=data,
        manifest_sha256=hashlib.sha256(data).hexdigest(),
    )


class HeadlessMissionBankCompilerV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.design = design_for()
        cls.compilation = MISSIONS.compile_mission_banks(
            REPOSITORY_ROOT, cls.design
        )

    def test_complete_live_calibration_family_seed_count_matrix(self) -> None:
        records = self.compilation.manifest["artifacts"]
        self.assertEqual(len(records), len(MISSIONS.FAMILIES) * 2)
        self.assertEqual(
            [(record["family"], record["seed"]) for record in records],
            [
                (family, seed)
                for family in MISSIONS.FAMILIES
                for seed in (1, 2)
            ],
        )
        for record in records:
            self.assertEqual(
                [entry["robot_count"] for entry in record["counts"]],
                [8, 12, 16, 24, 32],
            )
            self.assertEqual(record["max_robot_count"], 32)
            self.assertEqual(record["phase"], "calibration")
            self.assertEqual(
                record["static_inter_route_distance"]["diagnostic_only"],
                True,
            )
            self.assertEqual(record["seed_roles"], MISSIONS.SEED_ROLES)
            self.assertEqual(
                record["sampling_structure"], MISSIONS.SAMPLING_STRUCTURE
            )
        MISSIONS.validate_compilation(self.compilation)

    def test_byte_determinism_and_canonical_json(self) -> None:
        repeated = MISSIONS.compile_mission_banks(
            REPOSITORY_ROOT, copy.deepcopy(self.design)
        )
        self.assertEqual(repeated.design_bytes, self.compilation.design_bytes)
        self.assertEqual(
            repeated.manifest_bytes, self.compilation.manifest_bytes
        )
        self.assertEqual(
            [(item.relative_path, item.data) for item in repeated.files],
            [
                (item.relative_path, item.data)
                for item in self.compilation.files
            ],
        )
        for artifact in self.compilation.files:
            value = MISSIONS.strict_json_bytes(
                artifact.data, artifact.relative_path.as_posix()
            )
            self.assertEqual(
                artifact.data, MISSIONS.canonical_json_bytes(value)
            )
            self.assertEqual(
                artifact.sha256,
                hashlib.sha256(artifact.data).hexdigest(),
            )

    def test_seeds_have_distinct_effective_scenarios(self) -> None:
        by_family_count: dict[tuple[str, int], set[str]] = {}
        for record in self.compilation.manifest["artifacts"]:
            for count in record["counts"]:
                by_family_count.setdefault(
                    (record["family"], count["robot_count"]), set()
                ).add(count["effective_scenario_sha256"])
        self.assertEqual(
            set(by_family_count),
            {
                (family, count)
                for family in MISSIONS.FAMILIES
                for count in (8, 12, 16, 24, 32)
            },
        )
        self.assertTrue(all(len(hashes) == 2 for hashes in by_family_count.values()))

    def test_each_family_seed_is_one_nested_bank(self) -> None:
        for request in self.compilation.design["requests"]:
            family, seed = request["family"], request["seed"]
            bank = parsed_file(
                self.compilation, MISSIONS._bank_path(family, seed)
            )
            self.assertEqual(bank["seed_roles"], MISSIONS.SEED_ROLES)
            self.assertEqual(
                bank["sampling_structure"], MISSIONS.SAMPLING_STRUCTURE
            )
            self.assertEqual(
                bank["seed_roles"]["mission_selection"]["active_via"],
                "distinct-effective-scenario-bytes",
            )
            self.assertIs(
                bank["seed_roles"]["runtime_rng"]["activity_assumed"],
                False,
            )
            self.assertEqual(
                bank["sampling_structure"][
                    "count_relationship_within_bank"
                ],
                "nested-prefix",
            )
            self.assertIs(
                bank["sampling_structure"]["independent_scene_assumption"],
                False,
            )
            prior_count = 0
            prior_decisions: dict[str, int] = {}
            prior_waypoints: dict[str, list[list[float]]] = {}
            scenario_bytes: set[bytes] = set()
            for count in request["counts"]:
                scenario_path = MISSIONS._scenario_path(family, seed, count)
                scenario_artifact = self.compilation.by_path()[scenario_path]
                scenario = json.loads(scenario_artifact.data)
                scenario_bytes.add(scenario_artifact.data)
                self.assertEqual(scenario["num_robots"], count)
                self.assertEqual(
                    scenario["metadata"]["generator_seed_uint64"],
                    bank["generator_seed_uint64"],
                )
                self.assertEqual(
                    scenario["metadata"]["mission_bank_id"], bank["bank_id"]
                )
                ids = [str(index) for index in range(prior_count)]
                self.assertEqual(
                    {robot_id: scenario["decisions"][robot_id] for robot_id in ids},
                    prior_decisions,
                )
                self.assertEqual(
                    {robot_id: scenario["waypoints"][robot_id] for robot_id in ids},
                    prior_waypoints,
                )
                prior_count = count
                current_ids = [str(index) for index in range(count)]
                prior_decisions = {
                    robot_id: scenario["decisions"][robot_id]
                    for robot_id in current_ids
                }
                prior_waypoints = {
                    robot_id: scenario["waypoints"][robot_id]
                    for robot_id in current_ids
                }
            self.assertEqual(len(scenario_bytes), len(request["counts"]))

    def test_routes_obey_map_clearance_endpoint_spacing_and_horizon(self) -> None:
        map_by_family = {
            registered.family: registered
            for registered in self.compilation.maps
        }
        for request in self.compilation.design["requests"]:
            bank = parsed_file(
                self.compilation,
                MISSIONS._bank_path(request["family"], request["seed"]),
            )
            registered = map_by_family[request["family"]]
            safe = MISSIONS.clearance_mask(
                registered.pixels,
                MISSIONS.MAP_SIZE_PX,
                MISSIONS.CLEARANCE_RADIUS_PX,
            )
            endpoints: list[int] = []
            for robot in bank["robots"]:
                start_x, start_y = robot["start_pixel"]
                goal_x, goal_y = robot["goal_pixel"]
                start = start_y * MISSIONS.MAP_SIZE_PX + start_x
                goal = goal_y * MISSIONS.MAP_SIZE_PX + goal_x
                self.assertEqual(safe[start], 255)
                self.assertEqual(safe[goal], 255)
                self.assertTrue(
                    MISSIONS._line_is_free(
                        safe, MISSIONS.MAP_SIZE_PX, start, goal
                    )
                )
                self.assertTrue(
                    MISSIONS._endpoint_is_separated(start, endpoints)
                )
                self.assertTrue(
                    MISSIONS._endpoint_is_separated(goal, (*endpoints, start))
                )
                endpoints.extend((start, goal))
                self.assertGreaterEqual(
                    robot["route_length_m"], MISSIONS.MINIMUM_ROUTE_LENGTH_M
                )
                self.assertLessEqual(
                    robot["route_length_m"], MISSIONS.MAXIMUM_ROUTE_LENGTH_M
                )
                self.assertEqual(
                    robot["required_travel_distance_m"],
                    round(
                        max(
                            0.0,
                            robot["route_length_m"]
                            - MISSIONS.ROBOT_RADIUS_M,
                        ),
                        8,
                    ),
                )
                self.assertLessEqual(
                    robot["required_travel_distance_m"],
                    MISSIONS.HORIZON_DISTANCE_M,
                )
                self.assertGreaterEqual(
                    robot["required_travel_distance_m"],
                    MISSIONS.MINIMUM_GOAL_REACH_TRAVEL_DISTANCE_M,
                )
                self.assertLessEqual(
                    robot["required_travel_distance_m"],
                    MISSIONS.MAXIMUM_GOAL_REACH_TRAVEL_DISTANCE_M,
                )
            self.assertEqual(
                bank["geometry_policy"]["robot_radius_m"], 1.0
            )
            self.assertEqual(
                bank["geometry_policy"][
                    "minimum_endpoint_obstacle_clearance_m"
                ],
                1.25,
            )
            self.assertEqual(
                bank["geometry_policy"][
                    "minimum_endpoint_pair_center_distance_m"
                ],
                2.25,
            )
            self.assertEqual(
                bank["geometry_policy"][
                    "minimum_center_to_center_route_length_m"
                ],
                9.0,
            )
            self.assertEqual(
                bank["geometry_policy"][
                    "maximum_center_to_center_route_length_m"
                ],
                13.0,
            )
            self.assertEqual(
                bank["geometry_policy"][
                    "minimum_goal_reach_travel_distance_m"
                ],
                8.0,
            )
            self.assertEqual(
                bank["geometry_policy"][
                    "maximum_goal_reach_travel_distance_m"
                ],
                12.0,
            )
            self.assertEqual(
                bank["geometry_policy"][
                    "route_map_clearance_classification"
                ],
                "static-raster-preflight-only-not-dynamic-obstacle-proof",
            )

    def test_seed_and_count_mutations_change_only_their_declared_axes(self) -> None:
        base = MISSIONS.compile_mission_banks(
            REPOSITORY_ROOT, design_for(("open",), (1,), (2, 4))
        )
        larger = MISSIONS.compile_mission_banks(
            REPOSITORY_ROOT, design_for(("open",), (1,), (2, 4, 8))
        )
        other_seed = MISSIONS.compile_mission_banks(
            REPOSITORY_ROOT, design_for(("open",), (2,), (2, 4))
        )
        base_bank = parsed_file(base, MISSIONS._bank_path("open", 1))
        larger_bank = parsed_file(larger, MISSIONS._bank_path("open", 1))
        seed_bank = parsed_file(other_seed, MISSIONS._bank_path("open", 2))
        self.assertEqual(
            base_bank["generator_seed_uint64"],
            larger_bank["generator_seed_uint64"],
        )
        self.assertEqual(base_bank["robots"], larger_bank["robots"][:4])
        self.assertNotEqual(
            base_bank["generator_seed_uint64"], seed_bank["generator_seed_uint64"]
        )
        base_n2 = parsed_file(base, MISSIONS._scenario_path("open", 1, 2))
        larger_n2 = parsed_file(larger, MISSIONS._scenario_path("open", 1, 2))
        other_n2 = parsed_file(other_seed, MISSIONS._scenario_path("open", 2, 2))
        self.assertEqual(
            MISSIONS.effective_scenario_sha256(base_n2),
            MISSIONS.effective_scenario_sha256(larger_n2),
        )
        self.assertNotEqual(
            MISSIONS.effective_scenario_sha256(base_n2),
            MISSIONS.effective_scenario_sha256(other_n2),
        )
        base_n4 = parsed_file(base, MISSIONS._scenario_path("open", 1, 4))
        self.assertNotEqual(
            MISSIONS.effective_scenario_sha256(base_n2),
            MISSIONS.effective_scenario_sha256(base_n4),
        )

    def test_authenticated_map_byte_mutation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT) as temporary:
            root = Path(temporary) / "repository"
            environment_root = root / "comparison" / "environments"
            map_root = environment_root / "open" / "calibration"
            map_root.mkdir(parents=True)
            for relative in (
                PurePosixPath("comparison/environments/manifest.json"),
                PurePosixPath("comparison/environments/manifest.sha256"),
                PurePosixPath("comparison/environments/open/calibration/map.png"),
            ):
                source = REPOSITORY_ROOT.joinpath(*relative.parts)
                target = root.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
            map_path = map_root / "map.png"
            mutated = bytearray(map_path.read_bytes())
            mutated[-1] ^= 1
            map_path.write_bytes(mutated)
            with self.assertRaisesRegex(
                MISSIONS.MissionCompileError,
                "authenticated calibration map.*differs",
            ):
                MISSIONS.compile_mission_banks(
                    root, design_for(("open",), (1,), (2,))
                )

    def test_validation_and_audit_are_rejected_before_environment_read(self) -> None:
        for phase in ("validation", "audit"):
            design = design_for(("open",), (1,), (2,))
            design["phase"] = phase
            with mock.patch.object(
                MISSIONS,
                "_open_directory_path",
                side_effect=AssertionError("environment must not be read"),
            ) as loader:
                with self.assertRaisesRegex(
                    MISSIONS.MissionCompileError, "prohibited"
                ):
                    MISSIONS.compile_mission_banks(REPOSITORY_ROOT, design)
                loader.assert_not_called()

    def test_noncalibration_seed_is_rejected_before_environment_read(self) -> None:
        for seed in (0, 11, 31):
            design = design_for(("open",), (1,), (2,))
            design["requests"][0]["seed"] = seed
            with mock.patch.object(
                MISSIONS,
                "_open_directory_path",
                side_effect=AssertionError("environment must not be read"),
            ) as loader:
                with self.assertRaisesRegex(
                    MISSIONS.MissionCompileError, "seed must be in 1..10"
                ):
                    MISSIONS.compile_mission_banks(REPOSITORY_ROOT, design)
                loader.assert_not_called()

    def test_closed_design_and_artifact_schemas_reject_mutations(self) -> None:
        invalid_designs = []
        extra = design_for(("open",), (1,), (2,))
        extra["unexpected"] = False
        invalid_designs.append(extra)
        bool_count = design_for(("open",), (1,), (2,))
        bool_count["requests"][0]["counts"] = [True]
        invalid_designs.append(bool_count)
        float_seed = design_for(("open",), (1,), (2,))
        float_seed["requests"][0]["seed"] = 1.0
        invalid_designs.append(float_seed)
        duplicate = design_for(("open",), (1,), (2,))
        duplicate["requests"].append(copy.deepcopy(duplicate["requests"][0]))
        invalid_designs.append(duplicate)
        for invalid in invalid_designs:
            with self.assertRaises(MISSIONS.MissionCompileError):
                MISSIONS.compile_mission_banks(REPOSITORY_ROOT, invalid)

        small = MISSIONS.compile_mission_banks(
            REPOSITORY_ROOT, design_for(("open",), (1,), (2,))
        )
        scenario_path = MISSIONS._scenario_path("open", 1, 2)
        scenario_file = small.by_path()[scenario_path]
        scenario = json.loads(scenario_file.data)
        scenario["unexpected"] = None
        mutated_data = MISSIONS.canonical_json_bytes(scenario)
        mutated_file = replace(
            scenario_file,
            data=mutated_data,
            sha256=hashlib.sha256(mutated_data).hexdigest(),
        )
        mutated_files = tuple(
            mutated_file if item.relative_path == scenario_path else item
            for item in small.files
        )
        with self.assertRaises(MISSIONS.MissionCompileError):
            MISSIONS.validate_compilation(replace(small, files=mutated_files))

        coherent_type_confusions = (
            lambda manifest: manifest["classification"].__setitem__(
                "network_access", 0
            ),
            lambda manifest: manifest["design"].__setitem__(
                "bytes", float(manifest["design"]["bytes"])
            ),
            lambda manifest: manifest["environment_manifest"].__setitem__(
                "bytes", float(manifest["environment_manifest"]["bytes"])
            ),
            lambda manifest: manifest["artifacts"][0].__setitem__("seed", True),
            lambda manifest: manifest["artifacts"][0]["counts"][0].__setitem__(
                "robot_count", 2.0
            ),
            lambda manifest: manifest["artifacts"][0]["seed_roles"][
                "runtime_rng"
            ].__setitem__("activity_assumed", 0),
            lambda manifest: manifest["artifacts"][0]["sampling_structure"].__setitem__(
                "independent_scene_assumption", 0
            ),
        )
        for mutate in coherent_type_confusions:
            with self.subTest(mutate=mutate):
                with self.assertRaises(MISSIONS.MissionCompileError):
                    MISSIONS.validate_compilation(
                        with_mutated_manifest(small, mutate)
                    )

    def test_publication_and_cli_are_categorically_disabled_before_effects(self) -> None:
        small = MISSIONS.compile_mission_banks(
            REPOSITORY_ROOT, design_for(("open",), (1,), (2,))
        )
        protected_outputs = (
            REPOSITORY_ROOT
            / "comparison"
            / "plans"
            / "compiler-must-not-create-v1",
            REPOSITORY_ROOT / "results" / "compiler-must-not-create-v1",
            REPOSITORY_ROOT
            / "comparison"
            / "PLANS"
            / "compiler-must-not-create-v1",
        )
        for output in protected_outputs:
            with self.subTest(output=output):
                self.assertFalse(output.exists())
                with mock.patch.object(
                    MISSIONS.os,
                    "mkdir",
                    side_effect=AssertionError("write capability reached"),
                ) as mkdir, mock.patch.object(
                    MISSIONS,
                    "validate_compilation",
                    side_effect=AssertionError("validation capability reached"),
                ) as validate:
                    with self.assertRaisesRegex(
                        MISSIONS.MissionCompileError, "categorically disabled"
                    ):
                        MISSIONS.publish_compilation_create_exclusive(
                            small, output
                        )
                mkdir.assert_not_called()
                validate.assert_not_called()
                self.assertFalse(output.exists())

        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT) as temporary:
            parent = Path(temporary)
            design_path = parent / "design.json"
            design_path.write_bytes(small.design_bytes)
            stderr = io.StringIO()
            with mock.patch.object(
                MISSIONS,
                "load_canonical_design",
                side_effect=AssertionError("design read reached"),
            ) as loader, mock.patch.object(
                MISSIONS,
                "preflight_output_destination",
                side_effect=AssertionError("preflight reached"),
            ) as preflight, mock.patch.object(
                MISSIONS,
                "publish_compilation_create_exclusive",
                side_effect=AssertionError("publisher reached"),
            ) as publisher, mock.patch("sys.stderr", stderr):
                result = MISSIONS.main(
                    (
                        "--design",
                        str(design_path),
                        "--output-root",
                        str(parent / "output"),
                        "--repository-root",
                        str(REPOSITORY_ROOT),
                    )
                )
            self.assertEqual(result, 1)
            loader.assert_not_called()
            preflight.assert_not_called()
            publisher.assert_not_called()
            self.assertIn("nonauthorizing", stderr.getvalue())
            self.assertFalse((parent / "output").exists())

    def test_canonical_design_loader_rejects_noncanonical_bytes(self) -> None:
        design = MISSIONS.normalize_design(
            design_for(("open",), (1,), (2,))
        )
        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT) as temporary:
            canonical_path = Path(temporary) / "canonical.json"
            canonical_path.write_bytes(MISSIONS.canonical_json_bytes(design))
            self.assertEqual(
                MISSIONS.load_canonical_design(canonical_path), design
            )
            noncanonical_path = Path(temporary) / "noncanonical.json"
            noncanonical_path.write_text(
                json.dumps(design, sort_keys=True), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                MISSIONS.MissionCompileError, "canonical"
            ):
                MISSIONS.load_canonical_design(noncanonical_path)

    def test_descriptor_reads_reject_symlinks_and_path_races(self) -> None:
        design = MISSIONS.normalize_design(
            design_for(("open",), (1,), (2,))
        )
        raw = MISSIONS.canonical_json_bytes(design)
        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT) as temporary:
            parent = Path(temporary)
            target = parent / "target.json"
            target.write_bytes(raw)
            final_link = parent / "final-link.json"
            final_link.symlink_to(target.name)
            with self.assertRaisesRegex(
                MISSIONS.MissionCompileError, "non-symlink"
            ):
                MISSIONS.load_canonical_design(final_link)

            real_directory = parent / "real-directory"
            real_directory.mkdir()
            (real_directory / "design.json").write_bytes(raw)
            directory_link = parent / "directory-link"
            directory_link.symlink_to(
                real_directory.name, target_is_directory=True
            )
            with self.assertRaisesRegex(
                MISSIONS.MissionCompileError, "symlink component"
            ):
                MISSIONS.load_canonical_design(
                    directory_link / "design.json"
                )

            raced = parent / "raced.json"
            raced.write_bytes(raw)
            raced_original = parent / "raced-original.json"
            real_open = MISSIONS.os.open
            swapped = False

            def swap_before_final_open(
                path: object,
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal swapped
                if path == raced.name and not swapped:
                    swapped = True
                    raced.rename(raced_original)
                    raced.symlink_to(raced_original.name)
                return real_open(path, flags, mode, dir_fd=dir_fd)

            with mock.patch.object(
                MISSIONS.os, "open", side_effect=swap_before_final_open
            ):
                with self.assertRaises(MISSIONS.MissionCompileError):
                    MISSIONS.load_canonical_design(raced)
            self.assertTrue(swapped)

            repository_link = parent / "repository-link"
            repository_link.symlink_to(
                REPOSITORY_ROOT, target_is_directory=True
            )
            with self.assertRaisesRegex(
                MISSIONS.MissionCompileError, "symlink component"
            ):
                MISSIONS.compile_mission_banks(repository_link, design)

    def test_descriptor_read_detects_in_place_change_and_close_failure(self) -> None:
        design = MISSIONS.normalize_design(
            design_for(("open",), (1,), (2,))
        )
        raw = MISSIONS.canonical_json_bytes(design)
        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT) as temporary:
            parent = Path(temporary)
            changed = parent / "changed.json"
            changed.write_bytes(raw)
            real_read = MISSIONS.os.read
            mutated = False

            def mutate_after_read(fd: int, count: int) -> bytes:
                nonlocal mutated
                data = real_read(fd, count)
                if data and not mutated:
                    mutated = True
                    altered = bytearray(raw)
                    altered[0] = ord("[")
                    with changed.open("r+b") as stream:
                        stream.write(altered)
                        stream.flush()
                        os.fsync(stream.fileno())
                return data

            with mock.patch.object(
                MISSIONS.os, "read", side_effect=mutate_after_read
            ):
                with self.assertRaisesRegex(
                    MISSIONS.MissionCompileError, "changed while being read"
                ):
                    MISSIONS.load_canonical_design(changed)
            self.assertTrue(mutated)

            close_path = parent / "close.json"
            close_path.write_bytes(raw)
            real_close = MISSIONS.os.close
            close_fired = False

            def close_regular_then_error(fd: int) -> None:
                nonlocal close_fired
                if stat.S_ISREG(os.fstat(fd).st_mode) and not close_fired:
                    close_fired = True
                    real_close(fd)
                    raise OSError("injected stable-read close failure")
                real_close(fd)

            with mock.patch.object(
                MISSIONS.os,
                "close",
                side_effect=close_regular_then_error,
            ):
                with self.assertRaisesRegex(
                    MISSIONS.MissionCompileError, "close failed"
                ):
                    MISSIONS.load_canonical_design(close_path)
            self.assertTrue(close_fired)

    def test_repository_map_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT) as temporary:
            root = Path(temporary) / "repository"
            relatives = (
                PurePosixPath("comparison/environments/manifest.json"),
                PurePosixPath("comparison/environments/manifest.sha256"),
                PurePosixPath(
                    "comparison/environments/open/calibration/map.png"
                ),
            )
            for relative in relatives:
                source = REPOSITORY_ROOT.joinpath(*relative.parts)
                target = root.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
            map_path = root / relatives[-1]
            map_copy = map_path.with_name("map-copy.png")
            map_path.rename(map_copy)
            map_path.symlink_to(map_copy.name)
            with self.assertRaisesRegex(
                MISSIONS.MissionCompileError, "non-symlink"
            ):
                MISSIONS.compile_mission_banks(
                    root, design_for(("open",), (1,), (2,))
                )

    def test_deep_json_failures_are_closed_and_cli_safe(self) -> None:
        depth = max(100_000, sys.getrecursionlimit() + 100)
        deep_raw = b"[" * depth + b"0" + b"]" * depth
        with self.assertRaises(MISSIONS.MissionCompileError):
            MISSIONS.strict_json_bytes(deep_raw, "deep JSON")
        deep_value: object = 0
        for _index in range(depth):
            deep_value = [deep_value]
        with self.assertRaises(MISSIONS.MissionCompileError):
            MISSIONS.canonical_json_bytes(deep_value)

        with tempfile.TemporaryDirectory(dir=REPOSITORY_ROOT) as temporary:
            parent = Path(temporary)
            design_path = parent / "deep.json"
            output = parent / "must-not-exist"
            design_path.write_bytes(deep_raw)
            stderr = io.StringIO()
            with mock.patch("sys.stderr", stderr):
                result = MISSIONS.main(
                    (
                        "--design",
                        str(design_path),
                        "--output-root",
                        str(output),
                        "--repository-root",
                        str(REPOSITORY_ROOT),
                    )
                )
            self.assertEqual(result, 1)
            self.assertIn("nonauthorizing", stderr.getvalue())
            self.assertFalse(output.exists())

    def test_compiler_has_no_subprocess_or_network_import(self) -> None:
        tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.split(".", 1)[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
        self.assertTrue(
            imported_roots.isdisjoint(
                {
                    "http",
                    "requests",
                    "socket",
                    "subprocess",
                    "urllib",
                }
            )
        )
        source = SOURCE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("comparison/plans/simulator-demo", source)
        self.assertNotIn("results/simulator-demo", source)


if __name__ == "__main__":
    unittest.main()
