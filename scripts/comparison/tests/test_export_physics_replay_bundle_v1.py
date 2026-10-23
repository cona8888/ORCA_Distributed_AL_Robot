from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
COMPARISON_DIR = REPO_ROOT / "scripts" / "comparison"
if str(COMPARISON_DIR) not in sys.path:
    sys.path.insert(0, str(COMPARISON_DIR))

MODULE_PATH = COMPARISON_DIR / "export_physics_replay_bundle_v1.py"
SPEC = importlib.util.spec_from_file_location(
    "export_physics_replay_bundle_v1", MODULE_PATH
)
assert SPEC and SPEC.loader
EXPORTER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = EXPORTER
SPEC.loader.exec_module(EXPORTER)


def analytic_binary() -> Path | None:
    configured = os.environ.get("DANCERS_PHYSICS_EVIDENCE_ANALYTIC_BINARY")
    candidates = (
        Path(configured) if configured else None,
        REPO_ROOT / "build" / "pixi" / "dancers-physics-evidence-analytic-test",
        REPO_ROOT / "build" / "dancers-physics-evidence-analytic-test",
    )
    for candidate in candidates:
        if candidate is not None and candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def external_scene_fixture(
    root: Path,
) -> tuple[dict[str, object], bytes, str, dict[str, object], dict[str, Path]]:
    packed = bytes([0b10000001]) + bytes(
        EXPORTER.EXPECTED_PACKED_OCCUPANCY_BYTES - 1
    )
    raw_sha = "09040680c7818049e654c938da8d5fc38b412ce7da8da13d9e34459662b517b0"
    metrics: dict[str, object] = {
        "boundary_box_counting": {
            "dimension": 1.25,
            "r_squared": 0.99,
        },
        "boundary_density": 0.1,
        "free_space": {
            "component_count_4_connected": 1,
            "reachable_fraction": 1.0,
        },
        "obstacle_occupancy": 0.2,
    }
    payloads: dict[str, tuple[str, bytes]] = {
        "run_input": ("inputs/run.json", b"run-input\n"),
        "physics_registry": ("docs/physics-registry.json", b"registry\n"),
        "collider_manifest": (
            "comparison/plans/collider-manifest.json",
            b"collider-manifest\n",
        ),
        "mission_manifest": (
            "comparison/plans/mission-manifest.json",
            b"mission-manifest\n",
        ),
        "map": ("comparison/environments/open/calibration/map.png", b"map-bytes"),
        "collider": ("comparison/colliders/open.json", b"collider\n"),
        "occupancy_bitset": (
            "comparison/colliders/open.occupancy-msb.bin",
            packed,
        ),
        "mission": ("comparison/missions/open.json", b"mission\n"),
    }
    map_bytes = payloads["map"][1]
    environment = {
        "boundary_dimension_policy": (
            "Measured raster boundary descriptor; not a theoretical fractal-dimension guarantee."
        ),
        "environments": [
            {
                "family": "open",
                "split": "calibration",
                "map": {
                    "path": "open/calibration/map.png",
                    "bytes": len(map_bytes),
                    "sha256": hashlib.sha256(map_bytes).hexdigest(),
                },
                "effective_runtime_1000": {
                    "resolution": {"height_px": 1000, "width_px": 1000},
                    "raw_occupancy_bytes": 1_000_000,
                    "raw_occupancy_sha256": raw_sha,
                    "map_metrics": metrics,
                },
            }
        ],
    }
    environment_bytes = json.dumps(
        environment, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    payloads["environment_manifest"] = (
        "comparison/environments/manifest.json",
        environment_bytes,
    )

    identities: dict[str, dict[str, object]] = {}
    paths: dict[str, Path] = {}
    for name in (
        "run_input",
        "physics_registry",
        "environment_manifest",
        "collider_manifest",
        "mission_manifest",
        "map",
        "collider",
        "occupancy_bitset",
        "mission",
    ):
        relative, data = payloads[name]
        path = root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        paths[name] = path
        identities[name] = {
            "bytes": len(data),
            "path": relative,
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    inputs: dict[str, object] = {
        "family": "open",
        "map_split": "calibration",
        "effective_occupancy_bytes": 1_000_000,
        "effective_occupancy_sha256": raw_sha,
        "identities": identities,
    }
    return inputs, packed, raw_sha, metrics, paths


class PhysicsReplayBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        binary = analytic_binary()
        if binary is None:
            raise unittest.SkipTest("analytic physics-evidence test binary is not built")
        cls._temporary = tempfile.TemporaryDirectory(
            prefix="physics-replay-bundle-", dir=REPO_ROOT.parent
        )
        cls.directory = Path(cls._temporary.name)
        cls.left = cls.directory / "run-a.jsonl"
        cls.right = cls.directory / "run-b.jsonl"
        cls.digest = cls.directory / "digest.jsonl"
        for output, detail in (
            (cls.left, "replay"),
            (cls.right, "replay"),
            (cls.digest, "digest"),
        ):
            environment = os.environ.copy()
            environment.update(
                {"OMP_DYNAMIC": "FALSE", "OMP_NUM_THREADS": "1"}
            )
            subprocess.run(
                [str(binary), str(output), detail, "success"],
                cwd=REPO_ROOT,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=environment,
            )
        cls.bundle = EXPORTER.build_bundle(
            cls.left,
            cls.right,
            repository_root=REPO_ROOT,
            allow_analytic_fallback=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        if hasattr(cls, "_temporary"):
            cls._temporary.cleanup()

    def test_exact_replay_projection_preserves_lattice_hold_and_contacts(self) -> None:
        bundle = self.bundle
        self.assertEqual(bundle["schema"], EXPORTER.BUNDLE_SCHEMA)
        self.assertEqual(bundle["display_only_labels"], {"left": "Run A", "right": "Run B"})
        for field in (
            "registered_scientific_run",
            "scientific_evidence",
            "scientific_evidence_eligible",
            "paper_metrics_applicable",
            "timing_eligible",
            "execution_plan_bound",
            "official_result_namespace",
            "method_identity_authenticated",
            "display_labels_authenticate_methods",
        ):
            self.assertIs(bundle["classification"][field], False)
        self.assertTrue(bundle["verification"]["strict_replay_leaves_and_formulas"])
        self.assertFalse(bundle["verification"]["solver_launched_by_exporter"])
        self.assertTrue(bundle["pair_comparison"]["equal"])

        side = bundle["left"]
        self.assertEqual(len(side["frames"]), 21)
        self.assertEqual(len(side["command_frames"]), 2)
        self.assertEqual(len(side["boundaries"]), 3)
        for index, frame in enumerate(side["frames"]):
            self.assertEqual(frame["frame_index"], index)
            self.assertEqual(frame["time_ms"], 10 * index)
            expected_tick = None if index == 0 else (index - 1) // 10
            self.assertEqual(frame["held_command_tick"], expected_tick)
            self.assertNotIn("held_command", frame)
        self.assertEqual(side["frames"][10]["route_boundary_index"], 1)
        self.assertEqual(side["frames"][20]["route_boundary_index"], 2)
        self.assertEqual(side["boundaries"][1]["time_ms"], 100)
        command_robot = side["command_frames"][0]["robots"][0]
        self.assertEqual(
            command_robot["planner_raw_requested_pose_bits"],
            [
                "bfe80001895834ab",
                "bf6542860ca22909",
                "3ff921faff2bdc5a",
            ],
        )
        self.assertEqual(
            command_robot["planner_effective_requested_pose_bits"],
            command_robot["requested_pose_bits"],
        )
        self.assertTrue(side["frames"][1]["contacts"]["available"])
        kinds = {
            contact["kind"]
            for contact in side["frames"][1]["contacts"]["contacts"]
        }
        self.assertEqual(kinds, {"dynamic_pair", "static_segment"})

    def test_y_down_geometry_numeric_companions_and_signed_zero_survive(self) -> None:
        bundle = self.bundle
        coordinates = bundle["coordinate_system"]
        self.assertEqual(coordinates["world_bounds_m"], {
            "x_min": -50.0,
            "x_max": 50.0,
            "y_min": -50.0,
            "y_max": 50.0,
        })
        self.assertIn("down", coordinates["y_direction"])
        self.assertFalse(coordinates["vertical_flip"])
        self.assertEqual(bundle["scene"]["radius_m"], 1.0)
        segment = bundle["scene"]["static_segments"][0]
        self.assertEqual(segment["a_bits"], ["c000000000000000", "4014000000000000"])
        self.assertEqual(segment["a"], [-2.0, 5.0])
        mission = bundle["scene"]["missions"][2]
        self.assertEqual(mission["authenticated_initial_r6_bits"][-2:], [
            "8000000000000000",
            "8000000000000000",
        ])
        self.assertEqual(mission["authenticated_initial_r6"][-2:], [-0.0, -0.0])
        self.assertTrue(
            all(
                math_sign < 0
                for math_sign in (
                    __import__("math").copysign(1.0, value)
                    for value in mission["authenticated_initial_r6"][-2:]
                )
            )
        )
        contact = bundle["left"]["frames"][1]["contacts"]["contacts"][0]
        self.assertIn("normal_applied_to_first_bits", contact)
        self.assertIn("normal_applied_to_first", contact)
        self.assertIn("point_bits", contact)
        self.assertIn("point", contact)

    def test_analytic_assets_are_explicitly_unavailable_and_require_opt_in(self) -> None:
        scene = self.bundle["scene"]
        self.assertFalse(scene["identity_files_verified"]["available"])
        self.assertFalse(scene["raster"]["available"])
        self.assertFalse(scene["environment_descriptors"]["available"])
        self.assertTrue(scene["static_segments"])
        with self.assertRaises(EXPORTER.ReplayBundleError) as raised:
            EXPORTER.build_bundle(
                self.left,
                self.right,
                repository_root=REPO_ROOT,
            )
        self.assertIn("analytic-origin", str(raised.exception))

    def test_digest_is_rejected_and_frame_or_byte_bounds_never_downsample(self) -> None:
        with self.assertRaises(EXPORTER.ReplayBundleError) as raised:
            EXPORTER.build_bundle(
                self.digest,
                self.right,
                repository_root=REPO_ROOT,
                allow_analytic_fallback=True,
            )
        self.assertIn("digest-only", str(raised.exception))

        with self.assertRaises(EXPORTER.ReplayBundleError) as raised:
            EXPORTER.build_bundle(
                self.left,
                self.right,
                repository_root=REPO_ROOT,
                allow_analytic_fallback=True,
                max_frames=20,
            )
        self.assertIn("no frames were downsampled", str(raised.exception))

        with self.assertRaises(EXPORTER.ReplayBundleError) as raised:
            EXPORTER.build_bundle(
                self.left,
                self.right,
                repository_root=REPO_ROOT,
                allow_analytic_fallback=True,
                max_bundle_bytes=1024,
            )
        self.assertIn("no frames were downsampled", str(raised.exception))

    def test_packed_occupancy_is_msb_first_black_occupied_and_hashable(self) -> None:
        packed = bytes([0b10000001]) + bytes(
            EXPORTER.EXPECTED_PACKED_OCCUPANCY_BYTES - 1
        )
        raw = EXPORTER._unpack_occupancy_msb(packed)
        self.assertEqual(len(raw), 1_000_000)
        self.assertEqual(list(raw[:9]), [0, 255, 255, 255, 255, 255, 255, 0, 255])
        self.assertEqual(
            hashlib.sha256(raw).hexdigest(),
            "09040680c7818049e654c938da8d5fc38b412ce7da8da13d9e34459662b517b0",
        )

    def test_external_scene_assets_verify_every_identity_and_effective_raster(self) -> None:
        root = self.directory / "synthetic-external-repository"
        root.mkdir(mode=0o700)
        inputs, packed, raw_sha, metrics, paths = external_scene_fixture(root)
        identity_files, raster, environment = EXPORTER._external_scene_assets(
            root,
            inputs,
            allow_analytic_fallback=False,
        )
        self.assertTrue(identity_files["all_declared_identity_files_verified"])
        self.assertTrue(
            all(record["verified"] for record in identity_files["records"].values())
        )
        self.assertTrue(raster["available"])
        self.assertEqual(raster["effective_occupancy_sha256"], raw_sha)
        self.assertEqual(
            __import__("base64").b64decode(raster["occupied_bitset_base64"]),
            packed,
        )
        self.assertEqual(raster["rendering"]["bit_order"], "most-significant first")
        self.assertFalse(raster["rendering"]["vertical_flip"])
        self.assertEqual(environment["map_metrics"], metrics)
        self.assertIn("not a theoretical", environment["policy_note"])

        paths["occupancy_bitset"].write_bytes(bytes([0]) + packed[1:])
        with self.assertRaises(EXPORTER.ReplayBundleError) as raised:
            EXPORTER._external_scene_assets(
                root,
                inputs,
                allow_analytic_fallback=False,
            )
        self.assertIn("SHA-256", str(raised.exception))
        paths["occupancy_bitset"].write_bytes(packed)

        changed_inputs = json.loads(json.dumps(inputs))
        changed_inputs["effective_occupancy_sha256"] = "0" * 64
        with self.assertRaises(EXPORTER.ReplayBundleError) as raised:
            EXPORTER._external_scene_assets(
                root,
                changed_inputs,
                allow_analytic_fallback=False,
            )
        self.assertIn("effective raster", str(raised.exception))

        mission_bytes = paths["mission"].read_bytes()
        paths["mission"].unlink()
        paths["mission"].symlink_to(paths["run_input"])
        with self.assertRaises(EXPORTER.ReplayBundleError) as raised:
            EXPORTER._external_scene_assets(
                root,
                inputs,
                allow_analytic_fallback=False,
            )
        self.assertIn("securely open", str(raised.exception))
        paths["mission"].unlink()
        paths["mission"].write_bytes(mission_bytes)

        if hasattr(os, "mkfifo"):
            collider_bytes = paths["collider"].read_bytes()
            paths["collider"].unlink()
            os.mkfifo(paths["collider"])
            with self.assertRaises(EXPORTER.ReplayBundleError) as raised:
                EXPORTER._external_scene_assets(
                    root,
                    inputs,
                    allow_analytic_fallback=False,
                )
            self.assertIn("regular file", str(raised.exception))
            paths["collider"].unlink()
            paths["collider"].write_bytes(collider_bytes)

    def test_selected_environment_descriptor_must_match_map_and_occupancy(self) -> None:
        occupancy_sha = "a" * 64
        inputs = {
            "family": "open",
            "map_split": "calibration",
            "effective_occupancy_bytes": 1_000_000,
            "effective_occupancy_sha256": occupancy_sha,
            "identities": {
                "environment_manifest": {
                    "path": "comparison/environments/manifest.json",
                },
                "map": {
                    "path": "comparison/environments/open/calibration/map.png",
                    "bytes": 7,
                    "sha256": "b" * 64,
                },
            },
        }
        metrics = {
            "boundary_box_counting": {
                "dimension": 1.25,
                "r_squared": 0.99,
            },
            "boundary_density": 0.1,
            "free_space": {
                "component_count_4_connected": 1,
                "reachable_fraction": 1.0,
            },
            "obstacle_occupancy": 0.2,
        }
        manifest = {
            "boundary_dimension_policy": "Measured raster descriptor; not theoretical fractal dimension.",
            "environments": [
                {
                    "family": "open",
                    "split": "calibration",
                    "map": {
                        "path": "open/calibration/map.png",
                        "bytes": 7,
                        "sha256": "b" * 64,
                    },
                    "effective_runtime_1000": {
                        "resolution": {"height_px": 1000, "width_px": 1000},
                        "raw_occupancy_bytes": 1_000_000,
                        "raw_occupancy_sha256": occupancy_sha,
                        "map_metrics": metrics,
                    },
                }
            ],
        }
        payload = EXPORTER._environment_payload(
            json.dumps(manifest, separators=(",", ":")).encode(), inputs
        )
        self.assertTrue(payload["available"])
        self.assertEqual(payload["map_metrics"], metrics)
        self.assertIn("not theoretical", payload["policy_note"])

        changed = json.loads(json.dumps(manifest))
        changed["environments"][0]["map"]["sha256"] = "c" * 64
        with self.assertRaises(EXPORTER.ReplayBundleError):
            EXPORTER._environment_payload(
                json.dumps(changed, separators=(",", ":")).encode(), inputs
            )

    def test_create_exclusive_private_publication_and_namespace_rejection(self) -> None:
        output = self.directory / "viewer-bundle.json"
        receipt = EXPORTER.publish_bundle(
            self.bundle,
            output,
            repository_root=REPO_ROOT,
        )
        self.assertEqual(receipt["path"], str(output))
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertLessEqual(output.stat().st_size, EXPORTER.HARD_MAX_BUNDLE_BYTES)
        with self.assertRaises(EXPORTER.ReplayBundleError) as raised:
            EXPORTER.publish_bundle(
                self.bundle,
                output,
                repository_root=REPO_ROOT,
            )
        self.assertIn("create-exclusive", str(raised.exception))

        with self.assertRaises(EXPORTER.ReplayBundleError):
            EXPORTER.publish_bundle(
                self.bundle,
                Path("relative-viewer-bundle.json"),
                repository_root=REPO_ROOT,
            )
        with self.assertRaises(EXPORTER.ReplayBundleError) as raised:
            EXPORTER.publish_bundle(
                self.bundle,
                REPO_ROOT / "must-not-create-viewer-bundle.json",
                repository_root=REPO_ROOT,
            )
        self.assertIn("outside the repository", str(raised.exception))

    def test_publication_failure_removes_only_its_created_final(self) -> None:
        output = self.directory / "injected-write-failure.json"
        with mock.patch.object(
            EXPORTER,
            "_write_all",
            side_effect=OSError("injected write failure"),
        ):
            with self.assertRaises(EXPORTER.ReplayBundleError) as raised:
                EXPORTER.publish_bundle(
                    self.bundle,
                    output,
                    repository_root=REPO_ROOT,
                )
        self.assertIn("injected write failure", str(raised.exception))
        self.assertFalse(output.exists())

        fsync_output = self.directory / "injected-fsync-failure.json"
        with mock.patch.object(
            EXPORTER.os,
            "fsync",
            side_effect=OSError("injected fsync failure"),
        ):
            with self.assertRaises(EXPORTER.ReplayBundleError) as raised:
                EXPORTER.publish_bundle(
                    self.bundle,
                    fsync_output,
                    repository_root=REPO_ROOT,
                )
        self.assertIn("injected fsync failure", str(raised.exception))
        self.assertFalse(fsync_output.exists())

        for field in (
            "registered_scientific_run",
            "scientific_evidence_eligible",
            "timing_eligible",
            "official_result_namespace",
        ):
            invalid = dict(self.bundle)
            invalid["classification"] = dict(self.bundle["classification"])
            invalid["classification"][field] = True
            with self.subTest(classification_field=field):
                with self.assertRaises(EXPORTER.ReplayBundleError) as raised:
                    EXPORTER.publish_bundle(
                        invalid,
                        self.directory / f"invalid-{field}.json",
                        repository_root=REPO_ROOT,
                    )
                self.assertIn("engineering-only", str(raised.exception))

        bad_schema = dict(self.bundle)
        bad_schema["schema"] = "not-a-replay-bundle"
        with self.assertRaises(EXPORTER.ReplayBundleError) as raised:
            EXPORTER.publish_bundle(
                bad_schema,
                self.directory / "invalid-schema.json",
                repository_root=REPO_ROOT,
            )
        self.assertIn("replay bundle v1", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
