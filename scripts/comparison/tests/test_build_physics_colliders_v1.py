from __future__ import annotations

import binascii
import copy
import hashlib
import io
import json
import os
import stat
import struct
import tempfile
import unittest
import zlib
from dataclasses import replace
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest import mock

from scripts.comparison import build_physics_colliders_v1 as COLLIDERS


REPO_ROOT = Path(__file__).resolve().parents[3]


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = binascii.crc32(kind)
    crc = binascii.crc32(payload, crc) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", crc)
    )


def _png_bytes(
    width: int,
    height: int,
    pixels: bytes,
    *,
    color_type: int = 0,
) -> bytes:
    channels = {0: 1, 6: 4}[color_type]
    if len(pixels) != width * height * channels:
        raise ValueError("fixture pixel buffer differs")

    rows = b"".join(
        b"\x00" + pixels[row * width * channels : (row + 1) * width * channels]
        for row in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(rows))
        + _png_chunk(b"IEND", b"")
    )


def _source_digest(label: str) -> str:
    return hashlib.sha256(label.encode("ascii")).hexdigest()


def _tree_snapshot(path: Path) -> tuple[tuple[object, ...], ...]:
    """Capture an exact no-follow inventory that also supports published trees."""

    if not os.path.lexists(path):
        return ((".", "absent"),)
    records: list[tuple[object, ...]] = []

    def visit(current: Path, relative: str) -> None:
        metadata = current.lstat()
        kind = stat.S_IFMT(metadata.st_mode)
        common: tuple[object, ...] = (
            relative,
            kind,
            stat.S_IMODE(metadata.st_mode),
            metadata.st_size,
            metadata.st_mtime_ns,
        )
        if stat.S_ISLNK(metadata.st_mode):
            records.append((*common, os.readlink(current)))
        elif stat.S_ISREG(metadata.st_mode):
            records.append((*common, hashlib.sha256(current.read_bytes()).hexdigest()))
        elif stat.S_ISDIR(metadata.st_mode):
            records.append(common)
            for child in sorted(current.iterdir(), key=lambda item: item.name):
                child_relative = child.name if relative == "." else f"{relative}/{child.name}"
                visit(child, child_relative)
        else:
            records.append(common)

    visit(path, ".")
    return tuple(records)


def _expected_atomic_edges(
    occupancy: bytes,
    width: int,
    height: int,
    *,
    pixel_size_mm: int,
    origin_mm: tuple[int, int],
) -> set[tuple[str, int, int, int, int, int]]:
    origin_x, origin_y = origin_mm
    edges: set[tuple[str, int, int, int, int, int]] = set()
    for row in range(height):
        for column in range(width):
            index = row * width + column
            if occupancy[index] != COLLIDERS.FREE:
                continue
            x0 = origin_x + column * pixel_size_mm
            x1 = x0 + pixel_size_mm
            y0 = origin_y + row * pixel_size_mm
            y1 = y0 + pixel_size_mm
            if column == 0 or occupancy[index - 1] == COLLIDERS.OCCUPIED:
                edges.add(("vertical", x0, y0, y1, 1, 0))
            if column + 1 == width or occupancy[index + 1] == COLLIDERS.OCCUPIED:
                edges.add(("vertical", x1, y0, y1, -1, 0))
            if row == 0 or occupancy[index - width] == COLLIDERS.OCCUPIED:
                edges.add(("horizontal", y0, x0, x1, 0, 1))
            if row + 1 == height or occupancy[index + width] == COLLIDERS.OCCUPIED:
                edges.add(("horizontal", y1, x0, x1, 0, -1))
    return edges


def _expanded_segment_edges(
    segments: tuple[COLLIDERS.ColliderSegment, ...],
    pixel_size_mm: int,
) -> set[tuple[str, int, int, int, int, int]]:
    edges: set[tuple[str, int, int, int, int, int]] = set()
    for segment in segments:
        length = segment.second_coordinate_mm - segment.first_coordinate_mm
        if length % pixel_size_mm:
            raise AssertionError("merged segment is not cell aligned")
        for first in range(
            segment.first_coordinate_mm,
            segment.second_coordinate_mm,
            pixel_size_mm,
        ):
            edges.add(
                (
                    segment.axis,
                    segment.fixed_coordinate_mm,
                    first,
                    first + pixel_size_mm,
                    segment.normal_x,
                    segment.normal_y,
                )
            )
    return edges


class PhysicsColliderBuilderV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.bundle = COLLIDERS.build_registered_colliders(REPO_ROOT)

    def _build(
        self,
        occupancy: bytes,
        width: int,
        height: int,
        *,
        pixel_size_mm: int = 100,
        origin_mm: tuple[int, int] = (0, 0),
    ) -> COLLIDERS.ColliderArtifact:
        return COLLIDERS.build_collider_from_occupancy(
            occupancy,
            width=width,
            height=height,
            source_png_sha256=_source_digest("analytic-fixture"),
            pixel_size_mm=pixel_size_mm,
            origin_mm=origin_mm,
        )

    def test_msb_first_occupancy_packing_and_floor_resize(self) -> None:
        occupancy = bytes(
            [
                COLLIDERS.OCCUPIED,
                COLLIDERS.FREE,
                COLLIDERS.OCCUPIED,
                COLLIDERS.OCCUPIED,
                COLLIDERS.FREE,
                COLLIDERS.FREE,
                COLLIDERS.FREE,
                COLLIDERS.OCCUPIED,
                COLLIDERS.OCCUPIED,
                COLLIDERS.FREE,
            ]
        )
        self.assertEqual(
            COLLIDERS.pack_occupancy_msb_first(occupancy, 10, 1),
            bytes([0b10110001, 0b10000000]),
        )
        source = bytes(
            [
                COLLIDERS.OCCUPIED,
                COLLIDERS.FREE,
                COLLIDERS.FREE,
                COLLIDERS.OCCUPIED,
            ]
        )
        self.assertEqual(
            COLLIDERS.expand_nearest_neighbour_occupancy(
                source, 2, 2, 3, 3
            ),
            bytes(
                [
                    COLLIDERS.OCCUPIED,
                    COLLIDERS.OCCUPIED,
                    COLLIDERS.FREE,
                    COLLIDERS.OCCUPIED,
                    COLLIDERS.OCCUPIED,
                    COLLIDERS.FREE,
                    COLLIDERS.FREE,
                    COLLIDERS.FREE,
                    COLLIDERS.OCCUPIED,
                ]
            ),
        )

    def test_analytic_free_occupied_wall_and_positive_down_maps(self) -> None:
        free = self._build(bytes([COLLIDERS.FREE]), 1, 1)
        self.assertEqual(len(free.segments), 4)
        self.assertEqual(
            [
                (
                    segment.axis,
                    segment.fixed_coordinate_mm,
                    segment.first_endpoint_mm,
                    segment.second_endpoint_mm,
                    segment.normal,
                )
                for segment in free.segments
            ],
            [
                ("horizontal", 0, (0, 0), (100, 0), (0, 1)),
                ("horizontal", 100, (0, 100), (100, 100), (0, -1)),
                ("vertical", 0, (0, 0), (0, 100), (1, 0)),
                ("vertical", 100, (100, 0), (100, 100), (-1, 0)),
            ],
        )
        occupied = self._build(bytes([COLLIDERS.OCCUPIED]), 1, 1)
        self.assertEqual(occupied.segments, ())

        open_rectangle = self._build(bytes([COLLIDERS.FREE] * 6), 3, 2)
        self.assertEqual(len(open_rectangle.segments), 4)
        self.assertEqual(
            open_rectangle.segments[0].second_coordinate_mm
            - open_rectangle.segments[0].first_coordinate_mm,
            300,
        )

        top_free = self._build(
            bytes([COLLIDERS.FREE, COLLIDERS.OCCUPIED]), 1, 2
        )
        internal = [
            segment
            for segment in top_free.segments
            if segment.axis == "horizontal"
            and segment.fixed_coordinate_mm == 100
        ]
        self.assertEqual(len(internal), 1)
        self.assertEqual(internal[0].normal, (0, -1))

        bottom_free = self._build(
            bytes([COLLIDERS.OCCUPIED, COLLIDERS.FREE]), 1, 2
        )
        internal = [
            segment
            for segment in bottom_free.segments
            if segment.axis == "horizontal"
            and segment.fixed_coordinate_mm == 100
        ]
        self.assertEqual(len(internal), 1)
        self.assertEqual(internal[0].normal, (0, 1))

    def test_boundary_equivalence_for_edges_corners_and_corridor(self) -> None:
        fixtures = {
            "free-square": (2, 2, bytes([COLLIDERS.FREE] * 4)),
            "centre-obstacle": (
                3,
                3,
                bytes(
                    [
                        COLLIDERS.FREE,
                        COLLIDERS.FREE,
                        COLLIDERS.FREE,
                        COLLIDERS.FREE,
                        COLLIDERS.OCCUPIED,
                        COLLIDERS.FREE,
                        COLLIDERS.FREE,
                        COLLIDERS.FREE,
                        COLLIDERS.FREE,
                    ]
                ),
            ),
            "corner-free": (
                2,
                2,
                bytes(
                    [
                        COLLIDERS.FREE,
                        COLLIDERS.OCCUPIED,
                        COLLIDERS.OCCUPIED,
                        COLLIDERS.OCCUPIED,
                    ]
                ),
            ),
            "one-cell-corridor": (
                5,
                3,
                bytes(
                    [
                        COLLIDERS.OCCUPIED,
                        COLLIDERS.OCCUPIED,
                        COLLIDERS.FREE,
                        COLLIDERS.OCCUPIED,
                        COLLIDERS.OCCUPIED,
                    ]
                    * 3
                ),
            ),
        }
        pixel_size = 25
        origin = (-50, -75)
        for name, (width, height, occupancy) in fixtures.items():
            with self.subTest(name=name):
                collider = self._build(
                    occupancy,
                    width,
                    height,
                    pixel_size_mm=pixel_size,
                    origin_mm=origin,
                )
                self.assertEqual(
                    _expanded_segment_edges(collider.segments, pixel_size),
                    _expected_atomic_edges(
                        occupancy,
                        width,
                        height,
                        pixel_size_mm=pixel_size,
                        origin_mm=origin,
                    ),
                )
                self.assertEqual(
                    [segment.segment_id for segment in collider.segments],
                    list(range(len(collider.segments))),
                )

        corner = self._build(bytes([COLLIDERS.FREE]), 1, 1)
        touching = [
            segment
            for segment in corner.segments
            if COLLIDERS.point_segment_distance_squared_mm2(
                (0, 0), segment
            )
            == 0
        ]
        self.assertEqual(len(touching), 2)
        self.assertEqual(
            [segment.segment_id for segment in touching],
            sorted(segment.segment_id for segment in touching),
        )
        self.assertEqual(
            COLLIDERS.minimum_point_segment_distance_squared_mm2(
                (50, 50), corner.segments
            ),
            2_500,
        )

    def test_artifact_is_compact_deterministic_and_hashes_without_self_reference(
        self,
    ) -> None:
        occupancy = bytes(
            [
                COLLIDERS.FREE,
                COLLIDERS.FREE,
                COLLIDERS.OCCUPIED,
                COLLIDERS.FREE,
                COLLIDERS.OCCUPIED,
                COLLIDERS.FREE,
            ]
        )
        first = self._build(occupancy, 3, 2)
        second = self._build(occupancy, 3, 2)
        self.assertEqual(first.artifact_bytes, second.artifact_bytes)
        self.assertEqual(first.artifact_sha256, second.artifact_sha256)
        self.assertEqual(first.occupancy_bitset, second.occupancy_bitset)
        self.assertEqual(first.segments, second.segments)
        self.assertTrue(first.artifact_bytes.endswith(b"\n"))
        self.assertNotIn(b"\n ", first.artifact_bytes)
        self.assertNotIn(b'": ', first.artifact_bytes)
        self.assertNotIn("artifact_sha256", first.artifact)

        payload = copy.deepcopy(first.artifact)
        embedded = payload.pop("ordered_segment_payload_sha256")
        self.assertEqual(
            embedded,
            COLLIDERS.sha256_bytes(COLLIDERS.canonical_json_bytes(payload)),
        )
        self.assertEqual(
            first.artifact_sha256,
            COLLIDERS.sha256_bytes(first.artifact_bytes),
        )
        self.assertEqual(
            COLLIDERS.validate_collider_artifact_bytes(first.artifact_bytes),
            first.artifact,
        )

    def test_png_decoder_accepts_exact_rgba_and_rejects_corruption(self) -> None:
        grayscale = _png_bytes(
            2,
            1,
            bytes([COLLIDERS.OCCUPIED, COLLIDERS.FREE]),
        )
        self.assertEqual(
            COLLIDERS.decode_png_occupancy(grayscale),
            (2, 1, bytes([COLLIDERS.OCCUPIED, COLLIDERS.FREE])),
        )
        rgba = _png_bytes(
            2,
            1,
            bytes([0, 0, 0, 255, 255, 255, 255, 255]),
            color_type=6,
        )
        self.assertEqual(
            COLLIDERS.decode_png_occupancy(rgba),
            (2, 1, bytes([COLLIDERS.OCCUPIED, COLLIDERS.FREE])),
        )
        invalid_pixel = _png_bytes(1, 1, bytes([127]))
        with self.assertRaisesRegex(
            COLLIDERS.ColliderBuildError, "non-opaque-binary"
        ):
            COLLIDERS.decode_png_occupancy(invalid_pixel)
        bad_crc = bytearray(grayscale)
        bad_crc[-1] ^= 1
        with self.assertRaisesRegex(COLLIDERS.ColliderBuildError, "CRC"):
            COLLIDERS.decode_png_occupancy(bytes(bad_crc))

        ihdr_end = 8 + 12 + 13
        transparent = (
            grayscale[:ihdr_end]
            + _png_chunk(b"tRNS", b"\x00\x00")
            + grayscale[ihdr_end:]
        )
        with self.assertRaisesRegex(
            COLLIDERS.ColliderBuildError, "forbidden transparency"
        ):
            COLLIDERS.decode_png_occupancy(transparent)

        before_header = (
            grayscale[:8]
            + _png_chunk(b"tEXt", b"note\x00before-header")
            + grayscale[8:]
        )
        with self.assertRaisesRegex(
            COLLIDERS.ColliderBuildError, "IHDR is not the first chunk"
        ):
            COLLIDERS.decode_png_occupancy(before_header)

    def test_descriptor_relative_reads_reject_symlink_components(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            real = root / "real"
            real.mkdir()
            (real / "input.bin").write_bytes(b"authenticated")
            self.assertEqual(
                COLLIDERS._read_regular_relative(
                    root,
                    PurePosixPath("real/input.bin"),
                    "fixture",
                ),
                b"authenticated",
            )
            (root / "alias").symlink_to(real, target_is_directory=True)
            with self.assertRaisesRegex(
                COLLIDERS.ColliderBuildError, "symlink"
            ):
                COLLIDERS._read_regular_relative(
                    root,
                    PurePosixPath("alias/input.bin"),
                    "fixture",
                )
            (root / "linked.bin").symlink_to(real / "input.bin")
            with self.assertRaisesRegex(
                COLLIDERS.ColliderBuildError, "symlink"
            ):
                COLLIDERS._read_regular_relative(
                    root,
                    PurePosixPath("linked.bin"),
                    "fixture",
                )

    def test_artifact_and_registered_source_corruption_fail_closed(self) -> None:
        artifact = self._build(bytes([COLLIDERS.FREE]), 1, 1)
        changed = copy.deepcopy(artifact.artifact)
        changed["segments"][0]["normal"] = [0, -1]
        with self.assertRaisesRegex(
            COLLIDERS.ColliderBuildError, "payload SHA-256"
        ):
            COLLIDERS.validate_collider_artifact_bytes(
                COLLIDERS.canonical_json_bytes(changed)
            )
        changed = copy.deepcopy(artifact.artifact)
        changed["artifact_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            COLLIDERS.ColliderBuildError, "keys differ"
        ):
            COLLIDERS.validate_collider_artifact_bytes(
                COLLIDERS.canonical_json_bytes(changed)
            )

        original_reader = COLLIDERS._read_regular_relative
        target = (
            COLLIDERS.ENVIRONMENT_ROOT
            / "open/calibration/map.png"
        )

        def corrupted_reader(
            root: Path, relative: object, label: str
        ) -> bytes:
            data = original_reader(root, relative, label)
            if relative == target:
                return data[:-1] + bytes([data[-1] ^ 1])
            return data

        with mock.patch.object(
            COLLIDERS,
            "_read_regular_relative",
            side_effect=corrupted_reader,
        ):
            with self.assertRaisesRegex(
                COLLIDERS.ColliderBuildError, "source PNG SHA-256 differs"
            ):
                COLLIDERS.build_registered_colliders(REPO_ROOT)

        changed_manifest = copy.deepcopy(
            COLLIDERS.load_authenticated_environment_manifest(REPO_ROOT)
        )
        changed_manifest["environments"][0]["effective_runtime_1000"][
            "raw_occupancy_sha256"
        ] = "0" * 64
        with mock.patch.object(
            COLLIDERS,
            "load_authenticated_environment_manifest",
            return_value=changed_manifest,
        ):
            with self.assertRaisesRegex(
                COLLIDERS.ColliderBuildError,
                "effective occupancy SHA-256 differs",
            ):
                COLLIDERS.build_registered_colliders(REPO_ROOT)

    def test_all_ten_registered_maps_build_and_remain_in_memory(self) -> None:
        expected_identities = [
            (family, split)
            for family in COLLIDERS.FAMILIES
            for split in COLLIDERS.SPLITS
        ]
        self.assertEqual(
            [
                (artifact.family, artifact.split)
                for artifact in self.bundle.artifacts
            ],
            expected_identities,
        )
        self.assertEqual(len(self.bundle.by_identity()), 10)
        for registered in self.bundle.artifacts:
            with self.subTest(
                family=registered.family, split=registered.split
            ):
                collider = registered.collider
                self.assertIsInstance(collider.effective_occupancy, bytes)
                self.assertEqual(
                    len(collider.effective_occupancy),
                    COLLIDERS.RASTER_WIDTH * COLLIDERS.RASTER_HEIGHT,
                )
                self.assertEqual(
                    COLLIDERS.sha256_bytes(collider.effective_occupancy),
                    registered.effective_occupancy_sha256,
                )
                self.assertEqual(len(collider.occupancy_bitset), 125_000)
                self.assertEqual(
                    COLLIDERS.sha256_bytes(collider.occupancy_bitset),
                    collider.artifact[
                        "row_major_occupancy_bitset_sha256"
                    ],
                )
                self.assertGreater(len(collider.segments), 0)
                self.assertNotIn("artifact_sha256", collider.artifact)
                COLLIDERS.validate_collider_artifact_bytes(
                    collider.artifact_bytes
                )
                raster = collider.artifact["raster"]
                self.assertEqual(raster["origin_mm"], [-50_000, -50_000])
                self.assertEqual(raster["x_extent_mm"], [-50_000, 50_000])
                self.assertEqual(raster["y_extent_mm"], [-50_000, 50_000])
                self.assertEqual(raster["row_y_direction"], "positive-down")

        by_identity = self.bundle.by_identity()
        self.assertEqual(
            by_identity[("open", "calibration")].collider.artifact_bytes,
            by_identity[("open", "validation")].collider.artifact_bytes,
        )

    def test_collider_manifest_is_canonical_ordered_and_non_authorizing(self) -> None:
        manifest = COLLIDERS.build_collider_manifest(REPO_ROOT, self.bundle)
        self.assertEqual(
            COLLIDERS.validate_collider_manifest_bytes(manifest.manifest_bytes),
            manifest.manifest,
        )
        expected = [
            (family, split)
            for family in COLLIDERS.FAMILIES
            for split in COLLIDERS.SPLITS
        ]
        self.assertEqual(
            [
                (row["family"], row["split"])
                for row in manifest.manifest["artifacts"]
            ],
            expected,
        )
        for row in manifest.manifest["artifacts"]:
            parent = (
                COLLIDERS.COLLIDER_NAMESPACE
                / row["family"]
                / row["split"]
            )
            self.assertEqual(
                row["artifact"]["path"],
                (parent / "collider.json").as_posix(),
            )
            self.assertEqual(
                row["occupancy_bitset"]["path"],
                (parent / "occupancy-msb.bin").as_posix(),
            )
            self.assertEqual(row["occupancy_bitset"]["bytes"], 125_000)
        self.assertNotIn("count_grid_frozen", manifest.manifest)
        self.assertNotIn("complete_scientific_grid", manifest.manifest)
        self.assertNotIn("scientific_execution_authorized", manifest.manifest)

    def test_manifest_reconstructs_geometry_before_accepting_coherent_hashes(self) -> None:
        registered = self.bundle.by_identity()[("open", "calibration")]
        collider = registered.collider
        changed_segments = list(collider.segments)
        changed_segments[0] = replace(
            changed_segments[0],
            second_coordinate_mm=(
                changed_segments[0].second_coordinate_mm - 100
            ),
        )
        changed_artifact = copy.deepcopy(collider.artifact)
        changed_artifact["segments"] = [
            segment.as_record() for segment in changed_segments
        ]
        payload = dict(changed_artifact)
        del payload["ordered_segment_payload_sha256"]
        changed_artifact["ordered_segment_payload_sha256"] = (
            COLLIDERS.sha256_bytes(COLLIDERS.canonical_json_bytes(payload))
        )
        changed_bytes = COLLIDERS.canonical_json_bytes(changed_artifact)
        forged_collider = replace(
            collider,
            artifact=changed_artifact,
            artifact_bytes=changed_bytes,
            artifact_sha256=COLLIDERS.sha256_bytes(changed_bytes),
            segments=tuple(changed_segments),
        )
        forged_registered = replace(registered, collider=forged_collider)
        forged_bundle = replace(
            self.bundle,
            artifacts=tuple(
                forged_registered if item is registered else item
                for item in self.bundle.artifacts
            ),
        )
        with self.assertRaisesRegex(
            COLLIDERS.ColliderBuildError, "detached"
        ):
            COLLIDERS.build_collider_manifest(REPO_ROOT, forged_bundle)

    def test_real_check_only_cli_writes_nothing(self) -> None:
        future_namespace = REPO_ROOT.joinpath(
            *self.bundle.artifacts[0].artifact_path.parents[2].parts
        )
        before = _tree_snapshot(future_namespace)
        stdout = SimpleNamespace(buffer=io.BytesIO())
        stderr = io.StringIO()
        with (
            mock.patch.object(Path, "write_bytes") as write_bytes,
            mock.patch.object(Path, "write_text") as write_text,
            mock.patch.object(Path, "mkdir") as mkdir,
            mock.patch.object(COLLIDERS.sys, "stdout", stdout),
            mock.patch.object(COLLIDERS.sys, "stderr", stderr),
        ):
            return_code = COLLIDERS.main(
                ["--check-only", "--repository-root", str(REPO_ROOT)]
            )
        self.assertEqual(return_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        write_bytes.assert_not_called()
        write_text.assert_not_called()
        mkdir.assert_not_called()
        self.assertEqual(_tree_snapshot(future_namespace), before)
        receipt_bytes = stdout.buffer.getvalue()
        receipt = COLLIDERS.strict_json_bytes(receipt_bytes, "check receipt")
        self.assertEqual(receipt_bytes, COLLIDERS.canonical_json_bytes(receipt))
        self.assertTrue(receipt["check_only"])
        self.assertEqual(receipt["artifact_count"], 10)
        self.assertFalse(receipt["count_grid_frozen"])
        self.assertFalse(receipt["complete_scientific_grid"])
        self.assertFalse(receipt["scientific_execution_authorized"])
        self.assertEqual(receipt["written_files"], 0)

    def test_non_check_invocation_refuses_without_building(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch.object(COLLIDERS.sys, "stderr", stderr),
            mock.patch.object(
                COLLIDERS, "build_registered_colliders"
            ) as build,
        ):
            return_code = COLLIDERS.main(
                ["--repository-root", str(REPO_ROOT)]
            )
        self.assertEqual(return_code, 2)
        self.assertIn("--check-only is required", stderr.getvalue())
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
