#!/usr/bin/env python3
"""Generate deterministic laptop-sized DANCeRS comparison environments.

The output is intentionally dependency-free and byte reproducible.  Maps are
8-bit grayscale PNGs (0 = black obstacle, 255 = white free space), scenarios
use the file-scenario schema consumed by PathPlanningScenarios.cpp, and every
artifact is pinned by SHA-256 in a canonical manifest.

Boundary box-counting dimension is reported only as a measured raster
descriptor.  It is not a theoretical fractal-dimension guarantee.
"""

from __future__ import annotations

import argparse
import binascii
import hashlib
import json
import math
import random
import struct
import sys
import tempfile
import zlib
from collections import deque
from pathlib import Path
from typing import Iterable, Sequence


MAP_SIZE = 256
RUNTIME_MAP_SIZE = 1000
WORLD_SIZE_M = 100.0
BOX_SIZES = (2, 4, 8, 16, 32, 64)
CLEARANCE_RADIUS_PX = 4
ROBOT_RADIUS_M = 1.0
MIN_INTERROBOT_CENTER_DISTANCE_M = 2.0 * ROBOT_RADIUS_M
MIN_INTERROBOT_CENTER_DISTANCE_PX = (
    MIN_INTERROBOT_CENTER_DISTANCE_M / (WORLD_SIZE_M / MAP_SIZE)
)
CAPACITY_TARGET_ROBOTS = 3_000
CAPACITY_COUNT_LADDER = (
    8,
    12,
    16,
    24,
    32,
    48,
    64,
    96,
    128,
    192,
    256,
    384,
    500,
    768,
    1_000,
    1_500,
    2_000,
    2_500,
    2_900,
    3_000,
)
RUNTIME_RESIZE_RULE = (
    "nearest-neighbour; sx=floor(x*source_size/1000), "
    "sy=floor(y*source_size/1000)"
)
RAW_OCCUPANCY_ENCODING = (
    "row-major uint8 occupancy; 0=obstacle, 255=free; no row padding"
)
DESCRIPTOR_NOTE = (
    "Measured box-counting descriptor of the obstacle-side raster boundary; "
    "not a theoretical fractal-dimension guarantee."
)
CAPACITY_POLICY_NOTE = (
    "Each exact-N scenario is a byte-frozen prefix of one deterministic "
    "collision-free mission bank. Geometry generation and structural checks "
    "do not execute either comparison algorithm or expose validation outcomes."
)
CAPACITY_MATERIALIZER_RELATIVE_PATH = (
    "scripts/comparison/materialize_capacity_scenario.py"
)

SPLITS = {
    "calibration": {
        "map_seed": 104_729,
        "mission_seed": 130_363,
        "robot_count": 8,
    },
    "validation": {
        "map_seed": 155_921,
        "mission_seed": 196_613,
        "robot_count": 12,
    },
}

FAMILIES = (
    "open",
    "perfect_maze",
    "fractal_low",
    "fractal_medium",
    "fractal_high",
)

FRACTAL_PERSISTENCE = {
    "fractal_low": 0.28,
    "fractal_medium": 0.52,
    "fractal_high": 0.78,
}


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def derive_seed(root_seed: int, family: str, purpose: str) -> int:
    material = f"dancers-env-v1:{root_seed}:{family}:{purpose}".encode("ascii")
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big")


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = binascii.crc32(kind)
    crc = binascii.crc32(payload, crc) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", crc)
    )


def _stored_zlib_stream(data: bytes) -> bytes:
    """Return a deterministic zlib stream made only of stored DEFLATE blocks."""
    out = bytearray(b"\x78\x01")
    offset = 0
    while offset < len(data):
        block = data[offset : offset + 65_535]
        offset += len(block)
        out.append(0x01 if offset == len(data) else 0x00)
        length = len(block)
        out.extend(struct.pack("<HH", length, 0xFFFF - length))
        out.extend(block)
    out.extend(struct.pack(">I", zlib.adler32(data) & 0xFFFFFFFF))
    return bytes(out)


def grayscale_png_bytes(pixels: Sequence[int], size: int) -> bytes:
    if len(pixels) != size * size:
        raise ValueError("pixel buffer has the wrong size")
    if any(pixel not in (0, 255) for pixel in pixels):
        raise ValueError("comparison maps must be binary black/white")
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # PNG filter: None
        raw.extend(pixels[y * size : (y + 1) * size])
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", _stored_zlib_stream(bytes(raw)))
        + _png_chunk(b"IEND", b"")
    )


def read_grayscale_png(path: Path) -> tuple[int, bytearray]:
    """Read the deliberately small PNG subset emitted above, validating CRCs."""
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError(f"{path} is not a PNG")
    cursor = 8
    width = height = None
    compressed = bytearray()
    while cursor < len(data):
        length = struct.unpack(">I", data[cursor : cursor + 4])[0]
        kind = data[cursor + 4 : cursor + 8]
        payload = data[cursor + 8 : cursor + 8 + length]
        expected_crc = struct.unpack(
            ">I", data[cursor + 8 + length : cursor + 12 + length]
        )[0]
        actual_crc = binascii.crc32(kind)
        actual_crc = binascii.crc32(payload, actual_crc) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise ValueError(f"{path} has a bad {kind!r} CRC")
        cursor += 12 + length
        if kind == b"IHDR":
            width, height, depth, color, compression, filtering, interlace = (
                struct.unpack(">IIBBBBB", payload)
            )
            if (
                width != height
                or depth != 8
                or color != 0
                or compression
                or filtering
                or interlace
            ):
                raise ValueError(f"{path} is outside the supported PNG subset")
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            break
    if width is None or height is None:
        raise ValueError(f"{path} has no IHDR")
    raw = zlib.decompress(bytes(compressed))
    stride = width + 1
    if len(raw) != stride * height:
        raise ValueError(f"{path} has an unexpected payload size")
    pixels = bytearray()
    for y in range(height):
        row = raw[y * stride : (y + 1) * stride]
        if row[0] != 0:
            raise ValueError(f"{path} uses an unsupported PNG filter")
        pixels.extend(row[1:])
    return width, pixels


def expand_nearest_neighbour(
    pixels: Sequence[int], source_size: int, target_size: int
) -> bytearray:
    """Expand a square occupancy raster with an explicit floor-index rule."""
    if len(pixels) != source_size * source_size:
        raise ValueError("source occupancy buffer has the wrong size")
    if source_size <= 0 or target_size <= 0:
        raise ValueError("occupancy dimensions must be positive")
    x_sources = [
        (x * source_size) // target_size for x in range(target_size)
    ]
    expanded_source_rows = []
    for source_y in range(source_size):
        row_offset = source_y * source_size
        expanded_source_rows.append(
            bytes(pixels[row_offset + source_x] for source_x in x_sources)
        )
    result = bytearray()
    for y in range(target_size):
        source_y = (y * source_size) // target_size
        result.extend(expanded_source_rows[source_y])
    return result


def _index(x: int, y: int, size: int) -> int:
    return y * size + x


def _fill_rect(
    pixels: bytearray,
    size: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
    value: int,
) -> None:
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(size, x1), min(size, y1)
    for y in range(y0, y1):
        start = _index(x0, y, size)
        pixels[start : start + (x1 - x0)] = bytes([value]) * (x1 - x0)


def make_open_map(size: int = MAP_SIZE) -> tuple[bytearray, dict]:
    border = 4
    pixels = bytearray([255]) * (size * size)
    _fill_rect(pixels, size, 0, 0, size, border, 0)
    _fill_rect(pixels, size, 0, size - border, size, size, 0)
    _fill_rect(pixels, size, 0, 0, border, size, 0)
    _fill_rect(pixels, size, size - border, 0, size, size, 0)
    return pixels, {"border_width_px": border}


def make_perfect_maze(
    seed: int, size: int = MAP_SIZE
) -> tuple[bytearray, dict]:
    cells_x = cells_y = 15
    pitch = 16
    corridor = 10
    margin = (size - cells_x * pitch) // 2
    rng = random.Random(seed)

    start = (rng.randrange(cells_x), rng.randrange(cells_y))
    visited = {start}
    stack = [start]
    edges: list[tuple[tuple[int, int], tuple[int, int]]] = []
    while stack:
        x, y = stack[-1]
        candidates = [
            (nx, ny)
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
            if 0 <= nx < cells_x
            and 0 <= ny < cells_y
            and (nx, ny) not in visited
        ]
        if not candidates:
            stack.pop()
            continue
        nxt = rng.choice(candidates)
        visited.add(nxt)
        edges.append(((x, y), nxt))
        stack.append(nxt)

    pixels = bytearray(size * size)

    def center(cell: tuple[int, int]) -> tuple[int, int]:
        return (
            margin + cell[0] * pitch + pitch // 2,
            margin + cell[1] * pitch + pitch // 2,
        )

    half = corridor // 2
    for cell in visited:
        cx, cy = center(cell)
        _fill_rect(pixels, size, cx - half, cy - half, cx + half, cy + half, 255)
    for left, right in edges:
        x0, y0 = center(left)
        x1, y1 = center(right)
        _fill_rect(
            pixels,
            size,
            min(x0, x1) - half,
            min(y0, y1) - half,
            max(x0, x1) + half,
            max(y0, y1) + half,
            255,
        )

    topology = {
        "cell_columns": cells_x,
        "cell_rows": cells_y,
        "connected_cells": len(visited),
        "corridor_width_px": corridor,
        "passage_edges": len(edges),
        "perfect_tree": len(visited) == cells_x * cells_y
        and len(edges) == len(visited) - 1,
    }
    return pixels, topology


def _smoothstep(value: float) -> float:
    return value * value * (3.0 - 2.0 * value)


def _value_noise(size: int, spacing: int, seed: int) -> list[float]:
    nodes = math.ceil((size - 1) / spacing) + 1
    rng = random.Random(seed)
    lattice = [[rng.random() for _ in range(nodes)] for _ in range(nodes)]
    result = [0.0] * (size * size)
    for y in range(size):
        gy = min(y // spacing, nodes - 2)
        fy = _smoothstep((y - gy * spacing) / spacing)
        for x in range(size):
            gx = min(x // spacing, nodes - 2)
            fx = _smoothstep((x - gx * spacing) / spacing)
            top = lattice[gy][gx] * (1.0 - fx) + lattice[gy][gx + 1] * fx
            bottom = (
                lattice[gy + 1][gx] * (1.0 - fx)
                + lattice[gy + 1][gx + 1] * fx
            )
            result[_index(x, y, size)] = top * (1.0 - fy) + bottom * fy
    return result


def _free_components(
    pixels: Sequence[int], size: int
) -> list[list[int]]:
    seen = bytearray(size * size)
    components: list[list[int]] = []
    for origin, value in enumerate(pixels):
        if value != 255 or seen[origin]:
            continue
        seen[origin] = 1
        queue = deque([origin])
        component: list[int] = []
        while queue:
            current = queue.popleft()
            component.append(current)
            x, y = current % size, current // size
            for nx, ny in (
                (x + 1, y),
                (x - 1, y),
                (x, y + 1),
                (x, y - 1),
            ):
                if not (0 <= nx < size and 0 <= ny < size):
                    continue
                neighbour = _index(nx, ny, size)
                if pixels[neighbour] == 255 and not seen[neighbour]:
                    seen[neighbour] = 1
                    queue.append(neighbour)
        components.append(component)
    components.sort(key=len, reverse=True)
    return components


def _retain_largest_free_component(
    pixels: bytearray, size: int
) -> bytearray:
    components = _free_components(pixels, size)
    if not components:
        raise ValueError("generated map has no free space")
    keep = bytearray(size * size)
    for point in components[0]:
        keep[point] = 1
    for point, value in enumerate(pixels):
        if value == 255 and not keep[point]:
            pixels[point] = 0
    return pixels


def make_fractal_map(
    seed: int, persistence: float, size: int = MAP_SIZE
) -> tuple[bytearray, dict]:
    spacings = (64, 32, 16, 8, 4, 2)
    field = [0.0] * (size * size)
    weight_sum = 0.0
    weights = []
    for level, spacing in enumerate(spacings):
        weight = persistence**level
        weights.append(weight)
        layer_seed = derive_seed(seed, f"spacing-{spacing}", "noise")
        layer = _value_noise(size, spacing, layer_seed)
        for i, value in enumerate(layer):
            field[i] += weight * value
        weight_sum += weight
    field = [value / weight_sum for value in field]

    border = 4
    target_occupancy = 0.25
    interior = [
        field[_index(x, y, size)]
        for y in range(border, size - border)
        for x in range(border, size - border)
    ]
    obstacle_target = max(1, int(target_occupancy * size * size))
    border_count = size * size - (size - 2 * border) ** 2
    interior_target = max(1, obstacle_target - border_count)
    cutoff = sorted(interior)[min(interior_target, len(interior)) - 1]

    pixels = bytearray([255]) * (size * size)
    for y in range(size):
        for x in range(size):
            if (
                x < border
                or x >= size - border
                or y < border
                or y >= size - border
                or field[_index(x, y, size)] <= cutoff
            ):
                pixels[_index(x, y, size)] = 0
    _retain_largest_free_component(pixels, size)
    return pixels, {
        "border_width_px": border,
        "noise_spacings_px": list(spacings),
        "persistence": persistence,
        "threshold_target_obstacle_occupancy": target_occupancy,
        "weights": [round(value, 8) for value in weights],
    }


def boundary_pixels(pixels: Sequence[int], size: int) -> list[int]:
    boundary = []
    for y in range(size):
        for x in range(size):
            point = _index(x, y, size)
            if pixels[point] != 0:
                continue
            for nx, ny in (
                (x + 1, y),
                (x - 1, y),
                (x, y + 1),
                (x, y - 1),
            ):
                if (
                    0 <= nx < size
                    and 0 <= ny < size
                    and pixels[_index(nx, ny, size)] == 255
                ):
                    boundary.append(point)
                    break
    return boundary


def box_counting_descriptor(
    pixels: Sequence[int],
    size: int,
    box_sizes: Sequence[int] = BOX_SIZES,
) -> dict:
    boundary = boundary_pixels(pixels, size)
    counts = []
    for box_size in box_sizes:
        boxes = {
            ((point % size) // box_size, (point // size) // box_size)
            for point in boundary
        }
        counts.append(len(boxes))
    valid = [
        (math.log(size / box_size), math.log(count))
        for box_size, count in zip(box_sizes, counts)
        if count > 1
    ]
    if len(valid) < 2:
        dimension = r_squared = None
    else:
        xs, ys = zip(*valid)
        x_mean, y_mean = sum(xs) / len(xs), sum(ys) / len(ys)
        ss_xx = sum((x - x_mean) ** 2 for x in xs)
        ss_xy = sum((x - x_mean) * (y - y_mean) for x, y in valid)
        dimension = ss_xy / ss_xx
        intercept = y_mean - dimension * x_mean
        ss_res = sum(
            (y - (intercept + dimension * x)) ** 2 for x, y in valid
        )
        ss_tot = sum((y - y_mean) ** 2 for y in ys)
        r_squared = 1.0 if ss_tot == 0.0 else 1.0 - ss_res / ss_tot
    return {
        "box_counts": counts,
        "box_sizes_px": list(box_sizes),
        "dimension": None if dimension is None else round(dimension, 8),
        "descriptor_note": DESCRIPTOR_NOTE,
        "r_squared": None if r_squared is None else round(r_squared, 8),
    }


def measure_map(pixels: Sequence[int], size: int) -> dict:
    obstacle_count = sum(value == 0 for value in pixels)
    free_count = size * size - obstacle_count
    boundary_count = len(boundary_pixels(pixels, size))
    components = _free_components(pixels, size)
    largest = len(components[0]) if components else 0
    return {
        "boundary_box_counting": box_counting_descriptor(pixels, size),
        "boundary_density": round(boundary_count / (size * size), 8),
        "boundary_pixel_count": boundary_count,
        "free_space": {
            "component_count_4_connected": len(components),
            "free_pixel_count": free_count,
            "largest_component_fraction": (
                0.0 if free_count == 0 else round(largest / free_count, 8)
            ),
            "reachable_fraction": (
                0.0 if free_count == 0 else round(largest / free_count, 8)
            ),
        },
        "obstacle_occupancy": round(obstacle_count / (size * size), 8),
        "obstacle_pixel_count": obstacle_count,
    }


def clearance_mask(
    pixels: Sequence[int], size: int, radius: int
) -> bytearray:
    """Return free pixels whose square radius contains no obstacle."""
    stride = size + 1
    integral = [0] * (stride * stride)
    for y in range(size):
        row_sum = 0
        for x in range(size):
            row_sum += pixels[_index(x, y, size)] == 0
            integral[(y + 1) * stride + x + 1] = (
                integral[y * stride + x + 1] + row_sum
            )
    result = bytearray(size * size)
    for y in range(size):
        y0, y1 = max(0, y - radius), min(size - 1, y + radius)
        for x in range(size):
            if pixels[_index(x, y, size)] != 255:
                continue
            x0, x1 = max(0, x - radius), min(size - 1, x + radius)
            obstacle_count = (
                integral[(y1 + 1) * stride + x1 + 1]
                - integral[y0 * stride + x1 + 1]
                - integral[(y1 + 1) * stride + x0]
                + integral[y0 * stride + x0]
            )
            if obstacle_count == 0:
                result[_index(x, y, size)] = 255
    return result


def _packing_order(
    candidates: Sequence[int],
    size: int,
    seed: int,
    variant: int,
) -> list[int]:
    """Return one deterministic spatial traversal for constructive packing."""
    phase_rng = random.Random(derive_seed(seed, f"packing-{variant}", "phase"))
    phase_x = phase_rng.randrange(size)
    phase_y = phase_rng.randrange(size)

    def transformed(point: int) -> tuple[int, int, int]:
        x, y = point % size, point // size
        if variant % 4 == 0:
            primary, secondary = y, x
        elif variant % 4 == 1:
            primary, secondary = x, y
        elif variant % 4 == 2:
            primary, secondary = y, size - 1 - x
        else:
            primary, secondary = x, size - 1 - y
        if variant >= 4:
            primary = size - 1 - primary
        return (
            (primary - phase_y) % size,
            (secondary - phase_x) % size,
            point,
        )

    return sorted(candidates, key=transformed)


def _greedy_collision_free_pack(
    ordered_candidates: Sequence[int],
    size: int,
    minimum_distance_px: float,
    target: int,
) -> list[int]:
    """Construct an independent set using a spatial hash."""
    bucket_width = max(1, math.ceil(minimum_distance_px))
    minimum_distance_sq = minimum_distance_px * minimum_distance_px
    buckets: dict[tuple[int, int], list[int]] = {}
    selected: list[int] = []
    for point in ordered_candidates:
        x, y = point % size, point // size
        bx, by = x // bucket_width, y // bucket_width
        collision = False
        for neighbour_by in range(by - 1, by + 2):
            for neighbour_bx in range(bx - 1, bx + 2):
                for other in buckets.get((neighbour_bx, neighbour_by), ()):
                    ox, oy = other % size, other // size
                    if (
                        (x - ox) * (x - ox) + (y - oy) * (y - oy)
                        < minimum_distance_sq
                    ):
                        collision = True
                        break
                if collision:
                    break
            if collision:
                break
        if collision:
            continue
        selected.append(point)
        buckets.setdefault((bx, by), []).append(point)
        if len(selected) == target:
            break
    return selected


def pack_capacity_points(
    navigable: Sequence[int],
    size: int,
    seed: int,
    target: int = CAPACITY_TARGET_ROBOTS,
) -> tuple[list[int], dict]:
    """Choose a dense deterministic set of mutually collision-free centres."""
    components = _free_components(navigable, size)
    if not components:
        raise ValueError("capacity map has no navigable component")
    candidates = components[0]
    trials: list[tuple[list[int], int]] = []
    for variant in range(8):
        packed = _greedy_collision_free_pack(
            _packing_order(candidates, size, seed, variant),
            size,
            MIN_INTERROBOT_CENTER_DISTANCE_PX,
            target,
        )
        trials.append((packed, variant))
        if len(packed) == target:
            break
    packed, variant = max(
        trials,
        key=lambda trial: (
            len(trial[0]),
            -trial[1],
            tuple(-point for point in trial[0][:16]),
        ),
    )
    identity_rng = random.Random(derive_seed(seed, "capacity", "robot-order"))
    identity_rng.shuffle(packed)
    minimum_distance_sq = minimum_pairwise_distance_sq(packed, size)
    return packed, {
        "candidate_pixels": len(candidates),
        "constructive_packing_count": len(packed),
        "minimum_pairwise_center_distance_m": round(
            math.sqrt(minimum_distance_sq) * WORLD_SIZE_M / size, 8
        ),
        "packing_policy": "best of eight deterministic spatial-hash greedy traversals",
        "selected_traversal": variant,
        "target_robot_count": target,
    }


def minimum_pairwise_distance_sq(
    points: Sequence[int], size: int
) -> float:
    if len(points) < 2:
        return math.inf
    bucket_width = max(1, math.ceil(MIN_INTERROBOT_CENTER_DISTANCE_PX))
    buckets: dict[tuple[int, int], list[int]] = {}
    minimum = math.inf
    for point in points:
        x, y = point % size, point // size
        bx, by = x // bucket_width, y // bucket_width
        search_radius = max(size // bucket_width + 1, 1)
        if minimum < math.inf:
            search_radius = max(
                1, math.ceil(math.sqrt(minimum) / bucket_width)
            )
        for neighbour_by in range(by - search_radius, by + search_radius + 1):
            for neighbour_bx in range(
                bx - search_radius, bx + search_radius + 1
            ):
                for other in buckets.get((neighbour_bx, neighbour_by), ()):
                    ox, oy = other % size, other // size
                    distance_sq = (x - ox) ** 2 + (y - oy) ** 2
                    if distance_sq < minimum:
                        minimum = distance_sq
        buckets.setdefault((bx, by), []).append(point)
    return minimum


def _bfs_tree(
    free_mask: Sequence[int], size: int, root: int
) -> tuple[list[int], list[int]]:
    parents = [-2] * (size * size)
    depths = [-1] * (size * size)
    parents[root] = -1
    depths[root] = 0
    queue = deque([root])
    while queue:
        current = queue.popleft()
        x, y = current % size, current // size
        for nx, ny in (
            (x + 1, y),
            (x - 1, y),
            (x, y + 1),
            (x, y - 1),
        ):
            if not (0 <= nx < size and 0 <= ny < size):
                continue
            neighbour = _index(nx, ny, size)
            if free_mask[neighbour] == 255 and parents[neighbour] == -2:
                parents[neighbour] = current
                depths[neighbour] = depths[current] + 1
                queue.append(neighbour)
    return parents, depths


def _tree_path(
    start: int,
    goal: int,
    parents: Sequence[int],
    depths: Sequence[int],
) -> list[int]:
    if depths[start] < 0 or depths[goal] < 0:
        raise ValueError("mission endpoint is outside the routing tree")
    left, right = [], []
    a, b = start, goal
    while depths[a] > depths[b]:
        left.append(a)
        a = parents[a]
    while depths[b] > depths[a]:
        right.append(b)
        b = parents[b]
    while a != b:
        left.append(a)
        right.append(b)
        a = parents[a]
        b = parents[b]
    left.append(a)
    return left + list(reversed(right))


def _capacity_goal_mapping(
    points: Sequence[int], size: int
) -> tuple[dict[int, int], float]:
    """Create a bijective, nontrivial endpoint assignment with long routes."""
    if len(points) < 2:
        raise ValueError("capacity missions require at least two points")
    projections = (
        lambda x, y: (x, y),
        lambda x, y: (y, x),
        lambda x, y: (x + y, x, y),
        lambda x, y: (x - y, x, y),
        lambda x, y: (2 * x + y, x, y),
        lambda x, y: (x + 2 * y, x, y),
    )
    best_mapping: dict[int, int] | None = None
    best_minimum = -1
    best_signature: tuple[int, ...] | None = None
    count = len(points)
    shifts = sorted(
        {
            max(1, min(count - 1, int(count * fraction)))
            for fraction in (0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65)
        }
    )
    for projection in projections:
        ordered = sorted(
            points,
            key=lambda point: projection(point % size, point // size),
        )
        for shift in shifts:
            mapping = {
                point: ordered[(index + shift) % count]
                for index, point in enumerate(ordered)
            }
            minimum = min(
                (point % size - goal % size) ** 2
                + (point // size - goal // size) ** 2
                for point, goal in mapping.items()
            )
            signature = tuple(mapping[point] for point in points[:16])
            if (
                minimum > best_minimum
                or (
                    minimum == best_minimum
                    and (best_signature is None or signature < best_signature)
                )
            ):
                best_mapping = mapping
                best_minimum = minimum
                best_signature = signature
    assert best_mapping is not None
    return (
        best_mapping,
        math.sqrt(best_minimum) * WORLD_SIZE_M / size,
    )


def _bfs_path(
    free_mask: Sequence[int], size: int, start: int, goal: int
) -> list[int]:
    parents = [-2] * (size * size)
    parents[start] = -1
    queue = deque([start])
    while queue and parents[goal] == -2:
        current = queue.popleft()
        x, y = current % size, current // size
        for nx, ny in (
            (x + 1, y),
            (x - 1, y),
            (x, y + 1),
            (x, y - 1),
        ):
            if not (0 <= nx < size and 0 <= ny < size):
                continue
            neighbour = _index(nx, ny, size)
            if free_mask[neighbour] == 255 and parents[neighbour] == -2:
                parents[neighbour] = current
                queue.append(neighbour)
    if parents[goal] == -2:
        raise ValueError("mission endpoints are not connected")
    path = []
    cursor = goal
    while cursor != -1:
        path.append(cursor)
        cursor = parents[cursor]
    path.reverse()
    return path


def _bresenham_points(start: int, goal: int, size: int) -> Iterable[int]:
    x0, y0 = start % size, start // size
    x1, y1 = goal % size, goal // size
    dx, sx = abs(x1 - x0), 1 if x0 < x1 else -1
    dy, sy = -abs(y1 - y0), 1 if y0 < y1 else -1
    error = dx + dy
    while True:
        yield _index(x0, y0, size)
        if x0 == x1 and y0 == y1:
            break
        twice = 2 * error
        if twice >= dy:
            error += dy
            x0 += sx
        if twice <= dx:
            error += dx
            y0 += sy


def _line_is_free(
    free_mask: Sequence[int], size: int, start: int, goal: int
) -> bool:
    return all(free_mask[point] == 255 for point in _bresenham_points(start, goal, size))


def _simplify_grid_path(
    path: Sequence[int], free_mask: Sequence[int], size: int
) -> list[int]:
    if len(path) <= 2:
        return list(path)
    turns = [path[0]]
    last_direction = None
    for i in range(1, len(path)):
        previous, current = path[i - 1], path[i]
        direction = (
            (current % size) - (previous % size),
            (current // size) - (previous // size),
        )
        if last_direction is not None and direction != last_direction:
            turns.append(previous)
        last_direction = direction
    turns.append(path[-1])

    simplified = [turns[0]]
    anchor = 0
    while anchor < len(turns) - 1:
        candidate = anchor + 1
        while (
            candidate + 1 < len(turns)
            and _line_is_free(
                free_mask, size, turns[anchor], turns[candidate + 1]
            )
        ):
            candidate += 1
        simplified.append(turns[candidate])
        anchor = candidate
    return simplified


def _compress_cardinal_grid_path(
    path: Sequence[int], size: int
) -> list[int]:
    """Compress a four-connected path without adding diagonal shortcuts."""
    if len(path) <= 2:
        return list(path)
    turns = [path[0]]
    last_direction = None
    for previous, current in zip(path, path[1:]):
        direction = (
            (current % size) - (previous % size),
            (current // size) - (previous // size),
        )
        if last_direction is not None and direction != last_direction:
            turns.append(previous)
        last_direction = direction
    turns.append(path[-1])
    return turns


def _pixel_to_world(point: int, size: int) -> list[float]:
    x, y = point % size, point // size
    return [
        round(((x + 0.5) / size - 0.5) * WORLD_SIZE_M, 6),
        round(((y + 0.5) / size - 0.5) * WORLD_SIZE_M, 6),
    ]


def _choose_point(
    rng: random.Random,
    candidates: Sequence[int],
    size: int,
    separated_from: Sequence[int],
    min_distance_px: float,
    far_from: int | None = None,
    min_goal_distance_px: float = 0.0,
) -> int:
    min_sep_sq = min_distance_px * min_distance_px
    min_goal_sq = min_goal_distance_px * min_goal_distance_px
    for _ in range(20_000):
        point = rng.choice(candidates)
        x, y = point % size, point // size
        if any(
            (x - other % size) ** 2 + (y - other // size) ** 2 < min_sep_sq
            for other in separated_from
        ):
            continue
        if far_from is not None:
            fx, fy = far_from % size, far_from // size
            if (x - fx) ** 2 + (y - fy) ** 2 < min_goal_sq:
                continue
        return point
    raise ValueError("could not choose well-separated mission points")


def make_scenario(
    pixels: Sequence[int],
    size: int,
    environment_id: str,
    split: str,
    seed: int,
    robot_count: int,
    map_sha256: str,
) -> tuple[dict, dict]:
    navigable = clearance_mask(pixels, size, CLEARANCE_RADIUS_PX)
    components = _free_components(navigable, size)
    if not components:
        raise ValueError(f"{environment_id}/{split} has no navigable free space")
    candidates = components[0]
    rng = random.Random(seed)
    starts: list[int] = []
    goals: list[int] = []
    routes: dict[str, list[list[float]]] = {}
    decisions: dict[str, int] = {}
    total_legs = reachable_legs = 0
    total_route_pixels = 0

    for robot in range(robot_count):
        start = _choose_point(rng, candidates, size, starts, 10.0)
        goal = _choose_point(
            rng,
            candidates,
            size,
            goals,
            10.0,
            far_from=start,
            min_goal_distance_px=size * 0.25,
        )
        path = _bfs_path(navigable, size, start, goal)
        route = _simplify_grid_path(path, navigable, size)
        starts.append(start)
        goals.append(goal)
        routes[str(robot)] = [_pixel_to_world(point, size) for point in route]
        decisions[str(robot)] = rng.randrange(10)
        total_route_pixels += len(path) - 1
        for left, right in zip(route, route[1:]):
            total_legs += 1
            if _line_is_free(navigable, size, left, right):
                reachable_legs += 1

    scenario = {
        "decisions": decisions,
        "metadata": {
            "clearance_radius_px": CLEARANCE_RADIUS_PX,
            "environment_id": environment_id,
            "map_sha256": map_sha256,
            "mission_seed": seed,
            "split": split,
            "world_size_m": WORLD_SIZE_M,
        },
        "num_robots": robot_count,
        "seed_robots": [],
        "waypoints": routes,
    }
    metrics = {
        "largest_navigable_component_pixels": len(candidates),
        "mean_grid_route_length_px": round(
            total_route_pixels / robot_count, 8
        ),
        "mission_reachability": {
            "fraction": (
                1.0 if total_legs == 0 else round(reachable_legs / total_legs, 8)
            ),
            "reachable_legs": reachable_legs,
            "total_legs": total_legs,
        },
        "robot_count": robot_count,
        "waypoint_count": sum(len(route) for route in routes.values()),
    }
    return scenario, metrics


def make_capacity_bank(
    pixels: Sequence[int],
    size: int,
    environment_id: str,
    split: str,
    seed: int,
    robot_count: int,
    map_sha256: str,
    packed_points: Sequence[int],
    packing_metrics: dict,
) -> tuple[dict, dict, list[int]]:
    """Build one fixed mission bank from which every exact-N prefix is cut."""
    if not 2 <= robot_count <= len(packed_points):
        raise ValueError("capacity robot count exceeds collision-free packing")
    navigable = clearance_mask(pixels, size, CLEARANCE_RADIUS_PX)
    points = list(packed_points[:robot_count])
    goal_for_start, minimum_mission_distance_m = _capacity_goal_mapping(
        points, size
    )
    parents, depths = _bfs_tree(navigable, size, points[0])
    decisions_rng = random.Random(
        derive_seed(seed, environment_id, "capacity-decisions")
    )
    routes: dict[str, list[list[float]]] = {}
    decisions: dict[str, int] = {}
    route_lengths: list[int] = []
    route_legs: list[int] = []
    for robot, start in enumerate(points):
        goal = goal_for_start[start]
        path = _tree_path(start, goal, parents, depths)
        route = _simplify_grid_path(path, navigable, size)
        if start == goal or len(route) < 2:
            raise ValueError("capacity mission has a trivial route")
        if not all(
            _line_is_free(navigable, size, left, right)
            for left, right in zip(route, route[1:])
        ):
            raise ValueError("capacity mission simplification crossed an obstacle")
        routes[str(robot)] = [
            _pixel_to_world(point, size) for point in route
        ]
        decisions[str(robot)] = decisions_rng.randrange(10)
        route_lengths.append(len(path) - 1)
        route_legs.append(len(route) - 1)

    bank_id = sha256_bytes(
        (
            f"dancers-capacity-bank-v1:{environment_id}:{split}:{seed}:"
            f"{robot_count}:{map_sha256}"
        ).encode("ascii")
    )
    scenario = {
        "decisions": decisions,
        "metadata": {
            "capacity_bank_id": bank_id,
            "capacity_bank_robot_count": robot_count,
            "clearance_radius_px": CLEARANCE_RADIUS_PX,
            "environment_id": environment_id,
            "map_sha256": map_sha256,
            "minimum_interrobot_center_distance_m": (
                MIN_INTERROBOT_CENTER_DISTANCE_M
            ),
            "mission_seed": seed,
            "robot_radius_m": ROBOT_RADIUS_M,
            "scenario_kind": "capacity-prefix",
            "split": split,
            "world_size_m": WORLD_SIZE_M,
        },
        "num_robots": robot_count,
        "seed_robots": [],
        "waypoints": routes,
    }
    minimum_bank_spacing_sq = minimum_pairwise_distance_sq(points, size)
    metrics = {
        "bank_id": bank_id,
        "largest_navigable_component_pixels": (
            packing_metrics["candidate_pixels"]
        ),
        "minimum_mission_endpoint_distance_m": round(
            minimum_mission_distance_m, 8
        ),
        "minimum_pairwise_goal_distance_m": (
            round(
                math.sqrt(minimum_bank_spacing_sq) * WORLD_SIZE_M / size,
                8,
            )
        ),
        "minimum_pairwise_start_distance_m": (
            round(
                math.sqrt(minimum_bank_spacing_sq) * WORLD_SIZE_M / size,
                8,
            )
        ),
        "robot_count": robot_count,
        "route_lengths_px": route_lengths,
        "route_legs": route_legs,
    }
    return scenario, metrics, points


def capacity_scenario_prefix(bank: dict, robot_count: int) -> dict:
    """Return the exact first-N mission; no robot is renumbered or regenerated."""
    bank_count = int(bank["metadata"]["capacity_bank_robot_count"])
    if not 1 <= robot_count <= bank_count:
        raise ValueError(
            f"capacity count {robot_count} is outside [1, {bank_count}]"
        )
    expected_ids = [str(index) for index in range(robot_count)]
    prefix = {
        "decisions": {
            robot_id: bank["decisions"][robot_id]
            for robot_id in expected_ids
        },
        "metadata": {
            **bank["metadata"],
            "prefix_robot_count": robot_count,
        },
        "num_robots": robot_count,
        "seed_robots": [
            robot_id
            for robot_id in bank.get("seed_robots", [])
            if int(robot_id) < robot_count
        ],
        "waypoints": {
            robot_id: bank["waypoints"][robot_id]
            for robot_id in expected_ids
        },
    }
    validate_capacity_scenario(prefix, robot_count)
    return prefix


def validate_capacity_scenario(scenario: dict, robot_count: int) -> None:
    """Reject a scenario whose declared N and exact robot key set disagree."""
    expected = {str(index) for index in range(robot_count)}
    if scenario.get("num_robots") != robot_count:
        raise ValueError("capacity scenario num_robots does not equal requested N")
    if scenario.get("metadata", {}).get("prefix_robot_count") != robot_count:
        raise ValueError("capacity scenario metadata does not equal requested N")
    for field in ("decisions", "waypoints"):
        if set(scenario.get(field, {})) != expected:
            raise ValueError(
                f"capacity scenario {field} keys are not exactly 0..N-1"
            )
    if any(
        int(robot_id) >= robot_count
        for robot_id in scenario.get("seed_robots", [])
    ):
        raise ValueError("capacity scenario has an out-of-prefix seed robot")
    if any(len(route) < 2 for route in scenario["waypoints"].values()):
        raise ValueError("capacity scenario contains a trivial route")


def capacity_counts(maximum: int) -> list[int]:
    counts = [count for count in CAPACITY_COUNT_LADDER if count <= maximum]
    if maximum not in counts:
        counts.append(maximum)
    return sorted(set(counts))


def _capacity_prefix_metrics(
    bank_metrics: dict, robot_count: int
) -> dict:
    route_lengths = bank_metrics["route_lengths_px"][:robot_count]
    route_legs = bank_metrics["route_legs"][:robot_count]
    return {
        "mean_grid_route_length_px": round(
            sum(route_lengths) / robot_count, 8
        ),
        "mission_reachability": {
            "fraction": 1.0,
            "reachable_legs": sum(route_legs),
            "total_legs": sum(route_legs),
        },
        "robot_count": robot_count,
        "waypoint_count": sum(legs + 1 for legs in route_legs),
    }


def generate_map(
    family: str, seed: int, size: int = MAP_SIZE
) -> tuple[bytearray, dict]:
    if family == "open":
        return make_open_map(size)
    if family == "perfect_maze":
        return make_perfect_maze(seed, size)
    if family in FRACTAL_PERSISTENCE:
        return make_fractal_map(seed, FRACTAL_PERSISTENCE[family], size)
    raise ValueError(f"unknown environment family: {family}")


def _artifact_record(relative_path: Path, data: bytes) -> dict:
    return {
        "bytes": len(data),
        "path": relative_path.as_posix(),
        "sha256": sha256_bytes(data),
    }


def generate_environment_tree(output_root: Path) -> dict:
    output_root.mkdir(parents=True, exist_ok=True)
    script_sha = sha256_bytes(Path(__file__).read_bytes())
    materializer_path = (
        Path(__file__).resolve().parents[2]
        / CAPACITY_MATERIALIZER_RELATIVE_PATH
    )
    materializer_sha = sha256_bytes(materializer_path.read_bytes())
    manifest: dict = {
        "artifact_hash": "SHA-256",
        "boundary_dimension_policy": DESCRIPTOR_NOTE,
        "capacity_suite": {
            "algorithm_execution_during_generation": False,
            "capacity_target_robot_count": CAPACITY_TARGET_ROBOTS,
            "count_ladder": list(CAPACITY_COUNT_LADDER),
            "exact_n_policy": (
                "num_robots, decisions keys, and waypoint keys must be exactly "
                "N and 0..N-1; silent truncation is prohibited"
            ),
            "frozen_ceiling_interpretation": (
                "Constructively demonstrated collision-free endpoint capacity "
                "shared by calibration and validation. It is not a mathematical "
                "packing maximum and is not an algorithm-performance result."
            ),
            "minimum_interrobot_center_distance_m": (
                MIN_INTERROBOT_CENTER_DISTANCE_M
            ),
            "prefix_materializer": {
                "exclusive_create_no_overwrite": True,
                "path": CAPACITY_MATERIALIZER_RELATIVE_PATH,
                "sha256": materializer_sha,
            },
            "policy_note": CAPACITY_POLICY_NOTE,
            "robot_radius_m": ROBOT_RADIUS_M,
            "validation_outcomes_generated_or_inspected": False,
        },
        "families": list(FAMILIES),
        "generator": {
            "path": "scripts/comparison/generate_environments.py",
            "sha256": script_sha,
            "stdlib_only": True,
        },
        "map_encoding": {
            "bit_depth": 8,
            "color_type": "grayscale",
            "free_value": 255,
            "height_px": MAP_SIZE,
            "obstacle_value": 0,
            "width_px": MAP_SIZE,
        },
        "schema_version": 1,
        "splits": SPLITS,
        "world_size_m": WORLD_SIZE_M,
        "environments": [],
    }

    prepared_cases: list[dict] = []
    for family in FAMILIES:
        for split, split_spec in SPLITS.items():
            map_seed = derive_seed(split_spec["map_seed"], family, "map")
            mission_seed = derive_seed(
                split_spec["mission_seed"], family, "mission"
            )
            pixels, generation = generate_map(family, map_seed)
            png_data = grayscale_png_bytes(pixels, MAP_SIZE)
            case_dir = Path(family) / split
            map_relative = case_dir / "map.png"
            map_record = _artifact_record(map_relative, png_data)
            navigable = clearance_mask(
                pixels, MAP_SIZE, CLEARANCE_RADIUS_PX
            )
            packed_points, packing_metrics = pack_capacity_points(
                navigable,
                MAP_SIZE,
                mission_seed,
            )
            free_area_m2 = (
                sum(value == 255 for value in pixels)
                * (WORLD_SIZE_M / MAP_SIZE) ** 2
            )
            packing_metrics["free_area_disk_upper_bound"] = math.floor(
                free_area_m2 / (math.pi * ROBOT_RADIUS_M**2)
            )
            packing_metrics["free_area_m2"] = round(free_area_m2, 8)
            prepared_cases.append(
                {
                    "case_dir": case_dir,
                    "family": family,
                    "generation": generation,
                    "map_record": map_record,
                    "map_seed": map_seed,
                    "mission_seed": mission_seed,
                    "packing_metrics": packing_metrics,
                    "packed_points": packed_points,
                    "pixels": pixels,
                    "png_data": png_data,
                    "split": split,
                    "split_spec": split_spec,
                }
            )

    family_maxima = {
        family: min(
            case["packing_metrics"]["constructive_packing_count"]
            for case in prepared_cases
            if case["family"] == family
        )
        for family in FAMILIES
    }
    if any(maximum < max(spec["robot_count"] for spec in SPLITS.values())
           for maximum in family_maxima.values()):
        raise ValueError("a capacity geometry cannot support the smoke-test N")
    manifest["capacity_suite"]["family_frozen_max_robot_count"] = (
        family_maxima
    )

    for case in prepared_cases:
        family = case["family"]
        split = case["split"]
        pixels = case["pixels"]
        map_record = case["map_record"]
        case_dir = case["case_dir"]
        map_path = output_root / map_record["path"]
        map_path.parent.mkdir(parents=True, exist_ok=True)
        map_path.write_bytes(case["png_data"])

        frozen_maximum = family_maxima[family]
        bank, bank_metrics, _ = make_capacity_bank(
            pixels,
            MAP_SIZE,
            family,
            split,
            case["mission_seed"],
            frozen_maximum,
            map_record["sha256"],
            case["packed_points"],
            case["packing_metrics"],
        )
        count_records = []
        for robot_count in capacity_counts(frozen_maximum):
            scenario = capacity_scenario_prefix(bank, robot_count)
            scenario_data = canonical_json_bytes(scenario)
            count_records.append(
                {
                    "bytes": len(scenario_data),
                    "sha256": sha256_bytes(scenario_data),
                    "scenario_metrics": _capacity_prefix_metrics(
                        bank_metrics, robot_count
                    ),
                    "robot_count": robot_count,
                }
            )

        bank_scenario = capacity_scenario_prefix(bank, frozen_maximum)
        bank_data = canonical_json_bytes(bank_scenario)
        bank_relative = case_dir / "capacity" / "bank.json"
        bank_path = output_root / bank_relative
        bank_path.parent.mkdir(parents=True, exist_ok=True)
        bank_path.write_bytes(bank_data)

        smoke_count = case["split_spec"]["robot_count"]
        smoke_scenario, scenario_metrics = make_scenario(
            pixels,
            MAP_SIZE,
            family,
            split,
            case["mission_seed"],
            smoke_count,
            map_record["sha256"],
        )
        scenario_data = canonical_json_bytes(smoke_scenario)
        scenario_relative = case_dir / "scenario.json"
        (output_root / scenario_relative).write_bytes(scenario_data)
        runtime_pixels = expand_nearest_neighbour(
            pixels, MAP_SIZE, RUNTIME_MAP_SIZE
        )
        endpoint_material = canonical_json_bytes(
            {
                "goals": [
                    bank["waypoints"][str(index)][-1]
                    for index in range(frozen_maximum)
                ],
                "starts": [
                    bank["waypoints"][str(index)][0]
                    for index in range(frozen_maximum)
                ],
            }
        )
        manifest["environments"].append(
            {
                "capacity": {
                    "bank": _artifact_record(bank_relative, bank_data),
                    "bank_id": bank_metrics["bank_id"],
                    "prefix_scenarios": count_records,
                    "endpoint_pool_sha256": sha256_bytes(
                        endpoint_material
                    ),
                    "family_frozen_max_robot_count": frozen_maximum,
                    "generation_only_no_algorithm_execution": True,
                    "packing": case["packing_metrics"],
                    "prefix_policy": (
                        "For M>N, robot ids 0..N-1 have byte-identical "
                        "decisions and waypoint arrays."
                    ),
                    "supported_counts": capacity_counts(frozen_maximum),
                },
                "effective_runtime_1000": {
                    "map_metrics": measure_map(
                        runtime_pixels, RUNTIME_MAP_SIZE
                    ),
                    "raw_occupancy_bytes": len(runtime_pixels),
                    "raw_occupancy_encoding": RAW_OCCUPANCY_ENCODING,
                    "raw_occupancy_sha256": sha256_bytes(runtime_pixels),
                    "resize_rule": RUNTIME_RESIZE_RULE,
                    "resolution": {
                        "height_px": RUNTIME_MAP_SIZE,
                        "width_px": RUNTIME_MAP_SIZE,
                    },
                    "source_resolution": {
                        "height_px": MAP_SIZE,
                        "width_px": MAP_SIZE,
                    },
                },
                "family": family,
                "generation": case["generation"],
                "map": map_record,
                "map_metrics": measure_map(pixels, MAP_SIZE),
                "map_seed": case["map_seed"],
                "mission_seed": case["mission_seed"],
                "scenario": _artifact_record(
                    scenario_relative, scenario_data
                ),
                "scenario_metrics": scenario_metrics,
                "split": split,
            }
        )

    manifest_data = canonical_json_bytes(manifest)
    (output_root / "manifest.json").write_bytes(manifest_data)
    manifest_hash = sha256_bytes(manifest_data)
    (output_root / "manifest.sha256").write_text(
        f"{manifest_hash}  manifest.json\n", encoding="ascii", newline="\n"
    )
    return manifest


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def check_environment_tree(output_root: Path) -> tuple[bool, list[str]]:
    if not output_root.is_dir():
        return False, [f"missing environment directory: {output_root}"]
    with tempfile.TemporaryDirectory(prefix="dancers-environments-") as tmp:
        candidate = Path(tmp) / "environments"
        generate_environment_tree(candidate)
        expected = _tree_bytes(candidate)
        actual = _tree_bytes(output_root)
    errors = []
    for relative in sorted(set(expected) | set(actual)):
        if relative not in actual:
            errors.append(f"missing frozen artifact: {relative}")
        elif relative not in expected:
            errors.append(f"unexpected frozen artifact: {relative}")
        elif actual[relative] != expected[relative]:
            errors.append(f"content mismatch: {relative}")
    return not errors, errors


def default_output_root() -> Path:
    return Path(__file__).resolve().parents[2] / "comparison" / "environments"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=default_output_root(),
        help="output directory (default: comparison/environments)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate in a temporary directory and compare byte-for-byte",
    )
    args = parser.parse_args(argv)
    if args.check:
        valid, errors = check_environment_tree(args.output)
        if not valid:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        print(f"Frozen environment tree is deterministic: {args.output}")
        return 0

    manifest = generate_environment_tree(args.output)
    print(
        f"Generated {len(manifest['environments'])} frozen cases in "
        f"{args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
