from __future__ import annotations

import copy
import hashlib
import io
import itertools
import json
import os
import stat
import subprocess
import sys
import unittest
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest import mock

from scripts.comparison import build_physics_colliders_v1 as COLLIDERS
from scripts.comparison import build_physics_missions_v1 as MISSIONS


REPO_ROOT = Path(__file__).resolve().parents[3]


class PhysicsMissionBuilderV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = COLLIDERS.load_authenticated_registry(REPO_ROOT)
        width = 80
        height = 80
        occupancy = bytes([COLLIDERS.FREE]) * (width * height)
        collider = COLLIDERS.build_collider_from_occupancy(
            occupancy,
            width=width,
            height=height,
            source_png_sha256="a" * 64,
            pixel_size_mm=100,
            origin_mm=(-4_000, -4_000),
        )
        cls.analytic = COLLIDERS.RegisteredCollider(
            family="open",
            split="calibration",
            source_path=PurePosixPath("analytic/open.png"),
            artifact_path=PurePosixPath("analytic/open-collider.json"),
            effective_occupancy_sha256=COLLIDERS.sha256_bytes(occupancy),
            collider=collider,
        )
        cls.prefix_two = MISSIONS.build_mission_bank_from_registered_collider(
            cls.registry,
            cls.analytic,
            phase="calibration",
            family="open",
            seed=1,
            counts=(2,),
        )
        cls.prefix_four = MISSIONS.build_mission_bank_from_registered_collider(
            cls.registry,
            cls.analytic,
            phase="calibration",
            family="open",
            seed=1,
            counts=(2, 4),
        )

    @staticmethod
    def _tree_snapshot(paths: list[Path]) -> dict[str, tuple[object, ...]]:
        records: dict[str, tuple[object, ...]] = {}

        def visit(path: Path, key: str) -> None:
            try:
                metadata = path.lstat()
            except FileNotFoundError:
                records[key] = ("absent",)
                return
            mode = stat.S_IMODE(metadata.st_mode)
            if stat.S_ISLNK(metadata.st_mode):
                records[key] = (
                    "symlink",
                    mode,
                    metadata.st_mtime_ns,
                    metadata.st_ctime_ns,
                    metadata.st_ino,
                    os.readlink(path),
                )
            elif stat.S_ISREG(metadata.st_mode):
                records[key] = (
                    "file",
                    mode,
                    metadata.st_size,
                    metadata.st_mtime_ns,
                    metadata.st_ctime_ns,
                    metadata.st_ino,
                )
            elif stat.S_ISDIR(metadata.st_mode):
                records[key] = (
                    "directory",
                    mode,
                    metadata.st_mtime_ns,
                    metadata.st_ctime_ns,
                    metadata.st_ino,
                )
                with os.scandir(path) as entries:
                    children = sorted(entries, key=lambda entry: entry.name)
                for child in children:
                    visit(Path(child.path), f"{key}/{child.name}")
            else:
                records[key] = ("special", metadata.st_mode)

        for path in paths:
            visit(path, path.relative_to(REPO_ROOT).as_posix())
        return records

    def test_seed_derivation_is_exact_and_excludes_robot_count(self) -> None:
        material = b"\0".join(
            (b"dancers-planar-physics-v1", b"calibration", b"open", b"1")
        )
        expected = int.from_bytes(hashlib.sha256(material).digest()[:8], "big")
        self.assertEqual(
            MISSIONS.generator_seed_uint64("calibration", "open", 1),
            expected,
        )
        self.assertEqual(
            self.prefix_two.generator_seed_uint64,
            self.prefix_four.generator_seed_uint64,
        )
        with self.assertRaisesRegex(MISSIONS.MissionBuildError, "registered"):
            MISSIONS.generator_seed_uint64("calibration", "open", 11)
        with self.assertRaises(MISSIONS.MissionBuildError):
            MISSIONS.generator_seed_uint64("calibration", "open", True)

    def test_nested_prefix_is_byte_identical_and_geometry_is_valid(self) -> None:
        from_two = self.prefix_two.by_count()[2]
        from_four = self.prefix_four.by_count()[2]
        self.assertEqual(from_two.mission_bytes, from_four.mission_bytes)
        self.assertEqual(from_two.mission_sha256, from_four.mission_sha256)

        large = self.prefix_four.by_count()[4]
        parsed = MISSIONS.validate_mission_bytes(
            large.mission_bytes,
            self.analytic,
            registry=self.registry,
        )
        robots = parsed["robots"]
        self.assertEqual(
            [robot["robot_id"] for robot in robots], list(range(4))
        )
        prefix_records = from_two.mission["robots"]
        self.assertEqual(robots[:2], prefix_records)
        self.assertTrue(all(robot["waypoints_mm"] for robot in robots))

        starts = [tuple(robot["start_pose"]["position_mm"]) for robot in robots]
        goals = [tuple(robot["waypoints_mm"][-1]) for robot in robots]
        minimum_squared = MISSIONS.MINIMUM_CENTER_SEPARATION_MM**2
        for collection in (starts, goals):
            for left, right in itertools.combinations(collection, 2):
                self.assertGreaterEqual(
                    (left[0] - right[0]) ** 2 + (left[1] - right[1]) ** 2,
                    minimum_squared,
                )
        for start, goal in zip(starts, goals):
            self.assertGreaterEqual(
                (start[0] - goal[0]) ** 2 + (start[1] - goal[1]) ** 2,
                minimum_squared,
            )
        for robot in robots:
            self.assertIsInstance(
                robot["start_pose"]["heading_microradians"], int
            )
            for point in [
                robot["start_pose"]["position_mm"],
                *robot["waypoints_mm"],
            ]:
                self.assertTrue(all(isinstance(value, int) for value in point))

    def test_exact_integer_route_horizon_matches_frozen_example(self) -> None:
        robot = {
            "start_pose": {
                "heading_microradians": 0,
                "position_mm": [0, 0],
            },
            "waypoints_mm": [[30_000, 0], [30_000, 40_000]],
        }
        self.assertEqual(MISSIONS.route_upper_length_mm(robot), 70_000)
        self.assertEqual(
            MISSIONS.compute_route_horizon([robot]),
            {
                "longest_robot_route_upper_mm": 70_000,
                "mission_controller_ticks": 800,
                "mission_horizon_ms": 80_000,
                "nominal_min_travel_ms": 35_000,
                "physics_steps": 8_000,
                "raw_budget_ms": 80_000,
            },
        )
        self.assertEqual(MISSIONS.ceil_sqrt(2), 2)
        self.assertEqual(MISSIONS.ceil_sqrt(9), 3)
        self.assertEqual(MISSIONS.ceil_div(101, 100), 2)

    def test_determinism_real_geometry_and_semantic_uniqueness(self) -> None:
        repeated = MISSIONS.build_mission_bank_from_registered_collider(
            self.registry,
            self.analytic,
            phase="calibration",
            family="open",
            seed=1,
            counts=(2, 4),
        )
        self.assertEqual(
            [artifact.mission_bytes for artifact in repeated.artifacts],
            [artifact.mission_bytes for artifact in self.prefix_four.artifacts],
        )
        different_seed = MISSIONS.build_mission_bank_from_registered_collider(
            self.registry,
            self.analytic,
            phase="calibration",
            family="open",
            seed=2,
            counts=(2,),
        )
        left = self.prefix_two.by_count()[2]
        right = different_seed.by_count()[2]
        self.assertNotEqual(left.mission["robots"], right.mission["robots"])
        self.assertNotEqual(
            left.mission_semantic_sha256, right.mission_semantic_sha256
        )
        MISSIONS.validate_distinct_semantic_hashes(
            [self.prefix_four, different_seed]
        )

    def test_semantic_hash_excludes_labels_but_duplicates_fail_closed(self) -> None:
        artifact = self.prefix_two.by_count()[2]
        relabelled = copy.deepcopy(artifact.mission)
        relabelled["phase"] = "audit"
        relabelled["seed"] = 31
        relabelled["map_split"] = "validation"
        relabelled["mission_id"] = "changed-label-only"
        relabelled["generator_seed_uint64"] = 0
        self.assertEqual(
            MISSIONS.mission_semantic_sha256(relabelled),
            artifact.mission_semantic_sha256,
        )
        duplicate_mission = copy.deepcopy(artifact.mission)
        duplicate_mission.update(
            {
                "phase": "audit",
                "seed": 31,
                "map_split": "validation",
                "mission_id": "physics-v1:audit:open:n-2:seed-31",
                "generator_seed_uint64": MISSIONS.generator_seed_uint64(
                    "audit", "open", 31
                ),
            }
        )
        duplicate_bytes = MISSIONS.canonical_json_bytes(duplicate_mission)
        duplicate_artifact = replace(
            artifact,
            mission=duplicate_mission,
            mission_bytes=duplicate_bytes,
            mission_sha256=MISSIONS.sha256_bytes(duplicate_bytes),
        )
        duplicate_identity = replace(
            self.prefix_two,
            phase="audit",
            map_split="validation",
            seed=31,
            generator_seed_uint64=MISSIONS.generator_seed_uint64(
                "audit", "open", 31
            ),
            artifacts=(duplicate_artifact,),
        )
        with self.assertRaisesRegex(
            MISSIONS.MissionBuildError, "reuse semantic geometry"
        ):
            MISSIONS.validate_distinct_semantic_hashes(
                [self.prefix_two, duplicate_identity]
            )
        forged_artifact = replace(
            duplicate_artifact, mission_semantic_sha256="f" * 64
        )
        forged_bundle = replace(
            duplicate_identity, artifacts=(forged_artifact,)
        )
        with self.assertRaisesRegex(
            MISSIONS.MissionBuildError, "artifact semantic identity differs"
        ):
            MISSIONS.validate_distinct_semantic_hashes(
                [self.prefix_two, forged_bundle]
            )

    def test_registry_unchecked_field_tampering_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.registry)
        tampered["frozen_date"] = "2099-01-01"
        with self.assertRaisesRegex(
            MISSIONS.MissionBuildError, "registry SHA-256 differs"
        ):
            MISSIONS.build_mission_bank_from_registered_collider(
                tampered,
                self.analytic,
                phase="calibration",
                family="open",
                seed=1,
                counts=(1,),
            )

    def test_corrupt_missions_fail_closed(self) -> None:
        base = self.prefix_two.by_count()[2].mission

        def encoded(mutator: object) -> bytes:
            value = copy.deepcopy(base)
            mutator(value)  # type: ignore[operator]
            return MISSIONS.canonical_json_bytes(value)

        corruptions = {
            "robot IDs": lambda value: value["robots"][0].__setitem__(
                "robot_id", 7
            ),
            "route": lambda value: value["robots"][0]["waypoints_mm"].__setitem__(
                0, value["robots"][0]["start_pose"]["position_mm"]
            ),
            "horizon": lambda value: value["route_horizon"].__setitem__(
                "physics_steps", value["route_horizon"]["physics_steps"] + 1
            ),
            "semantic": lambda value: value.__setitem__(
                "mission_semantic_sha256", "0" * 64
            ),
            "seed": lambda value: value.__setitem__(
                "generator_seed_uint64", value["generator_seed_uint64"] + 1
            ),
            "clearance": lambda value: value["robots"][0][
                "waypoints_mm"
            ].__setitem__(0, [-3_950, -3_950]),
        }
        for label, mutator in corruptions.items():
            with self.subTest(corruption=label):
                with self.assertRaises(MISSIONS.MissionBuildError):
                    MISSIONS.validate_mission_bytes(
                        encoded(mutator),
                        self.analytic,
                        registry=self.registry,
                    )

        noncanonical = json.dumps(base, indent=2).encode("utf-8")
        with self.assertRaisesRegex(
            MISSIONS.MissionBuildError, "not compact canonical"
        ):
            MISSIONS.validate_mission_bytes(noncanonical, self.analytic)

    def test_effective_occupancy_tampering_is_rejected(self) -> None:
        occupancy = bytearray(self.analytic.collider.effective_occupancy)
        occupancy[len(occupancy) // 2] = COLLIDERS.OCCUPIED
        detached_collider = replace(
            self.analytic.collider, effective_occupancy=bytes(occupancy)
        )
        detached = replace(self.analytic, collider=detached_collider)
        with self.assertRaisesRegex(
            MISSIONS.MissionBuildError, "detached"
        ):
            MISSIONS.build_mission_bank_from_registered_collider(
                self.registry,
                detached,
                phase="calibration",
                family="open",
                seed=1,
                counts=(1,),
            )
        wrong_digest = replace(
            self.analytic, effective_occupancy_sha256="b" * 64
        )
        with self.assertRaisesRegex(
            MISSIONS.MissionBuildError, "detached"
        ):
            MISSIONS.build_mission_bank_from_registered_collider(
                self.registry,
                wrong_digest,
                phase="calibration",
                family="open",
                seed=1,
                counts=(1,),
            )

    def test_counts_are_caller_supplied_not_a_frozen_grid(self) -> None:
        for invalid in ((), (0,), (True,), (2, 1), (1, 1)):
            with self.subTest(counts=invalid):
                with self.assertRaises(MISSIONS.MissionBuildError):
                    MISSIONS.build_mission_bank_from_registered_collider(
                        self.registry,
                        self.analytic,
                        phase="calibration",
                        family="open",
                        seed=1,
                        counts=invalid,
                    )
        receipt = MISSIONS.check_receipt(self.prefix_four)
        self.assertFalse(receipt["count_grid_frozen"])
        self.assertFalse(receipt["complete_scientific_grid"])
        self.assertFalse(receipt["scientific_execution_authorized"])
        self.assertEqual(receipt["solver_processes_launched"], 0)
        self.assertEqual(receipt["written_files"], 0)

    def test_manifest_exact_sha_integer_and_runtime_count_schema(self) -> None:
        artifact = self.prefix_two.by_count()[2]
        manifest_sha = "a" * 64
        record = {
            "artifact": COLLIDERS.file_identity(
                artifact.artifact_path, artifact.mission_bytes
            ),
            "collider_sha256": self.prefix_two.collider_sha256,
            "family": self.prefix_two.family,
            "map_sha256": self.prefix_two.map_sha256,
            "map_split": self.prefix_two.map_split,
            "mission_id": artifact.mission["mission_id"],
            "mission_semantic_sha256": artifact.mission_semantic_sha256,
            "phase": self.prefix_two.phase,
            "robot_count": artifact.robot_count,
            "route_horizon": copy.deepcopy(
                artifact.mission["route_horizon"]
            ),
            "seed": self.prefix_two.seed,
        }
        manifest = {
            "artifacts": [record],
            "collider_manifest_sha256": manifest_sha,
            "environment_manifest_sha256": (
                COLLIDERS.ENVIRONMENT_MANIFEST_SHA256
            ),
            "physics_registry_sha256": COLLIDERS.PHYSICS_REGISTRY_SHA256,
            "schema_version": MISSIONS.MANIFEST_SCHEMA,
        }
        self.assertEqual(
            MISSIONS.validate_mission_manifest_bytes(
                MISSIONS.canonical_json_bytes(manifest),
                collider_manifest_sha256=manifest_sha,
            ),
            manifest,
        )
        malformed_sha = copy.deepcopy(manifest)
        malformed_sha["collider_manifest_sha256"] = "x"
        with self.assertRaises(MISSIONS.MissionBuildError):
            MISSIONS.validate_mission_manifest_bytes(
                MISSIONS.canonical_json_bytes(malformed_sha),
                collider_manifest_sha256="x",
            )
        float_horizon = copy.deepcopy(manifest)
        float_horizon["artifacts"][0]["route_horizon"][
            "physics_steps"
        ] = float(
            float_horizon["artifacts"][0]["route_horizon"]["physics_steps"]
        )
        with self.assertRaises(MISSIONS.MissionBuildError):
            MISSIONS.validate_mission_manifest_bytes(
                MISSIONS.canonical_json_bytes(float_horizon),
                collider_manifest_sha256=manifest_sha,
            )
        oversized = copy.deepcopy(manifest)
        oversized["artifacts"][0]["robot_count"] = 4_097
        oversized["artifacts"][0]["mission_id"] = (
            "physics-v1:calibration:open:n-4097:seed-01"
        )
        oversized["artifacts"][0]["artifact"]["path"] = (
            MISSIONS.MISSION_NAMESPACE
            / "calibration/open/n-4097/seed-01/mission.json"
        ).as_posix()
        with self.assertRaisesRegex(MISSIONS.MissionBuildError, "identity"):
            MISSIONS.validate_mission_manifest_bytes(
                MISSIONS.canonical_json_bytes(oversized),
                collider_manifest_sha256=manifest_sha,
            )

    def test_registered_map_small_count_and_bundle_tamper(self) -> None:
        colliders = COLLIDERS.build_registered_colliders(REPO_ROOT)
        all_family_bundles = []
        for family in COLLIDERS.FAMILIES:
            for split, phase, seed in (
                ("calibration", "calibration", 1),
                ("validation", "validation", 11),
            ):
                with self.subTest(family=family, split=split):
                    generated = (
                        MISSIONS.build_mission_bank_from_registered_collider(
                            self.registry,
                            colliders.by_identity()[(family, split)],
                            phase=phase,
                            family=family,
                            seed=seed,
                            counts=(1,),
                        )
                    )
                    self.assertEqual(
                        generated.by_count()[1].mission["robot_count"], 1
                    )
                    all_family_bundles.append(generated)
        MISSIONS.validate_distinct_semantic_hashes(all_family_bundles)
        audit = MISSIONS.build_mission_bank(
            REPO_ROOT,
            phase="audit",
            family="open",
            seed=31,
            counts=(1,),
            collider_bundle=colliders,
        )
        self.assertEqual(audit.map_split, "validation")
        collider_manifest = COLLIDERS.build_collider_manifest(
            REPO_ROOT, colliders
        )
        inventory = MISSIONS.build_mission_manifest(
            REPO_ROOT,
            [audit, *reversed(all_family_bundles)],
            colliders,
            collider_manifest,
        )
        self.assertEqual(
            MISSIONS.validate_mission_manifest_bytes(
                inventory.manifest_bytes,
                collider_manifest_sha256=collider_manifest.manifest_sha256,
            ),
            inventory.manifest,
        )
        records = inventory.manifest["artifacts"]
        phase_rank = {
            phase: index for index, phase in enumerate(MISSIONS.PHASE_ORDER)
        }
        family_rank = {
            family: index for index, family in enumerate(COLLIDERS.FAMILIES)
        }
        keys = [
            (
                phase_rank[row["phase"]],
                family_rank[row["family"]],
                row["seed"],
                row["robot_count"],
            )
            for row in records
        ]
        self.assertEqual(keys, sorted(keys))
        audit_row = next(row for row in records if row["phase"] == "audit")
        self.assertEqual(audit_row["map_split"], "validation")
        self.assertEqual(
            audit_row["artifact"]["path"],
            (
                MISSIONS.MISSION_NAMESPACE
                / "audit/open/n-1/seed-31/mission.json"
            ).as_posix(),
        )
        self.assertNotIn("count_grid_frozen", inventory.manifest)
        self.assertNotIn("complete_scientific_grid", inventory.manifest)
        self.assertNotIn("scientific_execution_authorized", inventory.manifest)
        runtime = MISSIONS.build_runtime_input(
            repo_root=REPO_ROOT,
            collider_manifest=collider_manifest,
            mission_manifest=inventory,
            phase="audit",
            family="open",
            seed=31,
            robot_count=1,
            run_class=MISSIONS.FIXED_WORK_RUN_CLASS,
        )
        self.assertEqual(runtime.run_input["map_split"], "validation")
        self.assertEqual(
            runtime.run_input["artifacts"]["collider_manifest"]["path"],
            COLLIDERS.COLLIDER_MANIFEST_PATH.as_posix(),
        )
        self.assertEqual(
            runtime.run_input["artifacts"]["mission_manifest"]["path"],
            MISSIONS.MISSION_MANIFEST_PATH.as_posix(),
        )
        detached_inventory_value = copy.deepcopy(inventory.manifest)
        detached_inventory_value["artifacts"][-1]["collider_sha256"] = (
            "0" * 64
        )
        detached_inventory_bytes = MISSIONS.canonical_json_bytes(
            detached_inventory_value
        )
        detached_inventory = replace(
            inventory,
            manifest=detached_inventory_value,
            manifest_bytes=detached_inventory_bytes,
            manifest_sha256=MISSIONS.sha256_bytes(detached_inventory_bytes),
        )
        with self.assertRaisesRegex(
            MISSIONS.MissionBuildError, "detached row"
        ):
            MISSIONS.build_runtime_input(
                repo_root=REPO_ROOT,
                collider_manifest=collider_manifest,
                mission_manifest=detached_inventory,
                phase="audit",
                family="open",
                seed=31,
                robot_count=1,
                run_class=MISSIONS.FIXED_WORK_RUN_CLASS,
            )
        detached_collider_value = copy.deepcopy(collider_manifest.manifest)
        detached_collider_value["artifacts"][-1][
            "effective_occupancy_sha256"
        ] = "0" * 64
        detached_collider_bytes = COLLIDERS.canonical_json_bytes(
            detached_collider_value
        )
        detached_collider_manifest = replace(
            collider_manifest,
            manifest=detached_collider_value,
            manifest_bytes=detached_collider_bytes,
            manifest_sha256=COLLIDERS.sha256_bytes(detached_collider_bytes),
        )
        rebound_inventory_value = copy.deepcopy(inventory.manifest)
        rebound_inventory_value["collider_manifest_sha256"] = (
            detached_collider_manifest.manifest_sha256
        )
        rebound_inventory_bytes = MISSIONS.canonical_json_bytes(
            rebound_inventory_value
        )
        rebound_inventory = replace(
            inventory,
            manifest=rebound_inventory_value,
            manifest_bytes=rebound_inventory_bytes,
            manifest_sha256=MISSIONS.sha256_bytes(rebound_inventory_bytes),
        )
        with self.assertRaisesRegex(
            MISSIONS.MissionBuildError, "detached row"
        ):
            MISSIONS.build_runtime_input(
                repo_root=REPO_ROOT,
                collider_manifest=detached_collider_manifest,
                mission_manifest=rebound_inventory,
                phase="audit",
                family="open",
                seed=31,
                robot_count=1,
                run_class=MISSIONS.FIXED_WORK_RUN_CLASS,
            )
        seed_two = MISSIONS.build_mission_bank(
            REPO_ROOT,
            phase="calibration",
            family="open",
            seed=2,
            counts=(1,),
            collider_bundle=colliders,
        )
        seed_two_artifact = seed_two.by_count()[1]
        relabelled_mission = copy.deepcopy(seed_two_artifact.mission)
        relabelled_mission["seed"] = 1
        relabelled_mission["generator_seed_uint64"] = (
            MISSIONS.generator_seed_uint64("calibration", "open", 1)
        )
        relabelled_mission["mission_id"] = (
            "physics-v1:calibration:open:n-1:seed-01"
        )
        relabelled_mission["mission_semantic_sha256"] = (
            MISSIONS.mission_semantic_sha256(relabelled_mission)
        )
        relabelled_bytes = MISSIONS.canonical_json_bytes(relabelled_mission)
        relabelled_artifact = replace(
            seed_two_artifact,
            mission=relabelled_mission,
            mission_bytes=relabelled_bytes,
            mission_sha256=MISSIONS.sha256_bytes(relabelled_bytes),
            mission_semantic_sha256=relabelled_mission[
                "mission_semantic_sha256"
            ],
            artifact_path=MISSIONS.mission_artifact_path(relabelled_mission),
        )
        relabelled_bundle = replace(
            seed_two,
            seed=1,
            generator_seed_uint64=MISSIONS.generator_seed_uint64(
                "calibration", "open", 1
            ),
            artifacts=(relabelled_artifact,),
        )
        with self.assertRaisesRegex(
            MISSIONS.MissionBuildError, "deterministic regeneration"
        ):
            MISSIONS.build_mission_manifest(
                REPO_ROOT,
                [relabelled_bundle],
                colliders,
                collider_manifest,
            )

        first = MISSIONS.build_mission_bank(
            REPO_ROOT,
            phase="calibration",
            family="open",
            seed=1,
            counts=(1, 2),
            collider_bundle=colliders,
        )
        second = MISSIONS.build_mission_bank(
            REPO_ROOT,
            phase="calibration",
            family="open",
            seed=1,
            counts=(2,),
            collider_bundle=colliders,
        )
        self.assertEqual(
            first.by_count()[2].mission_bytes,
            second.by_count()[2].mission_bytes,
        )
        registered = colliders.by_identity()[("open", "calibration")]
        MISSIONS.validate_mission_bytes(
            first.by_count()[2].mission_bytes,
            registered,
            registry=self.registry,
        )

        detached = replace(
            registered, source_path=PurePosixPath("detached/map.png")
        )
        tampered_artifacts = tuple(
            detached if item is registered else item
            for item in colliders.artifacts
        )
        tampered = replace(colliders, artifacts=tampered_artifacts)
        with self.assertRaisesRegex(
            MISSIONS.MissionBuildError, "authenticated environment"
        ):
            MISSIONS.build_mission_bank(
                REPO_ROOT,
                phase="calibration",
                family="open",
                seed=1,
                counts=(1,),
                collider_bundle=tampered,
            )
        bad_identity = replace(
            colliders, environment_manifest_sha256="0" * 64
        )
        with self.assertRaisesRegex(
            MISSIONS.MissionBuildError, "pinned identities"
        ):
            MISSIONS.build_mission_bank(
                REPO_ROOT,
                phase="calibration",
                family="open",
                seed=1,
                counts=(1,),
                collider_bundle=bad_identity,
            )

    def test_direct_check_only_cli_is_real_and_writes_nothing(self) -> None:
        namespaces = self.registry["namespaces"]
        guarded = [
            REPO_ROOT.joinpath(*PurePosixPath(namespaces[key]).parts)
            for key in ("plan_root", "result_root")
        ]
        before = self._tree_snapshot(guarded)
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        command = [
            sys.executable,
            "scripts/comparison/build_physics_missions_v1.py",
            "--check-only",
            "--phase",
            "calibration",
            "--family",
            "open",
            "--seed",
            "1",
            "--counts",
            "1",
            "--repository-root",
            str(REPO_ROOT),
        ]
        completed = subprocess.run(
            command,
            cwd=REPO_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        receipt = json.loads(completed.stdout)
        self.assertTrue(receipt["check_only"])
        self.assertEqual(receipt["caller_supplied_counts"], [1])
        self.assertEqual(receipt["written_files"], 0)
        self.assertEqual(receipt["solver_processes_launched"], 0)
        after = self._tree_snapshot(guarded)
        self.assertEqual(after, before)

        stdout = SimpleNamespace(buffer=io.BytesIO())
        stderr = io.StringIO()
        with (
            mock.patch.object(Path, "write_bytes") as write_bytes,
            mock.patch.object(Path, "write_text") as write_text,
            mock.patch.object(Path, "mkdir") as mkdir,
            mock.patch.object(MISSIONS.sys, "stdout", stdout),
            mock.patch.object(MISSIONS.sys, "stderr", stderr),
        ):
            return_code = MISSIONS.main(command[2:])
        self.assertEqual(return_code, 0, stderr.getvalue())
        self.assertEqual(stderr.getvalue(), "")
        write_bytes.assert_not_called()
        write_text.assert_not_called()
        mkdir.assert_not_called()
        direct_receipt = json.loads(stdout.buffer.getvalue())
        self.assertEqual(direct_receipt, receipt)

        refused = subprocess.run(
            [item for item in command if item != "--check-only"],
            cwd=REPO_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(refused.returncode, 2)
        self.assertIn("no publication or execution mode", refused.stderr)
        self.assertEqual(self._tree_snapshot(guarded), before)


if __name__ == "__main__":
    unittest.main()
