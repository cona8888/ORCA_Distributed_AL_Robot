#!/usr/bin/env python3
"""Independent swept-geometry primitives for the future Track-B analyzer.

This module is deliberately incapable of launching a simulator or publishing a
result.  It verifies continuous linear sweeps of closed robot discs against
each other and against the closed occupied-square union of an effective raster.
Contact (clearance exactly zero) is unsafe.
"""

from __future__ import annotations

from dataclasses import dataclass
import binascii
import hashlib
import math
import os
from pathlib import Path
import stat
import struct
from typing import Iterable, Mapping, Sequence
import zlib


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DEFAULT_WORLD_SIZE_M = 100.0
DEFAULT_EFFECTIVE_SIZE = 1000
MAX_MAP_BYTES = 16 * 1024 * 1024
MAX_OCCUPIED_CELLS = DEFAULT_EFFECTIVE_SIZE * DEFAULT_EFFECTIVE_SIZE
SAFETY_EPSILON_M = 1e-12


class GeometryError(RuntimeError):
    """Raised when geometry or its authenticated input is malformed."""


def _finite(value: object, label: str) -> float:
    if type(value) not in (int, float):
        raise GeometryError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as error:
        raise GeometryError(f"{label} must be a finite number") from error
    if not math.isfinite(result):
        raise GeometryError(f"{label} must be a finite number")
    return result


def _positive(value: object, label: str) -> float:
    result = _finite(value, label)
    if result <= 0.0:
        raise GeometryError(f"{label} must be strictly positive")
    return result


def _point(value: Sequence[object], label: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise GeometryError(f"{label} must contain exactly x and y")
    return (_finite(value[0], f"{label}.x"), _finite(value[1], f"{label}.y"))


@dataclass(frozen=True)
class Raster:
    """A binary effective occupancy raster in the simulator world frame."""

    size: int
    world_size_m: float
    pixels: bytes
    sha256: str

    def __post_init__(self) -> None:
        if type(self.size) is not int or not (1 <= self.size <= 4096):
            raise GeometryError("raster size is invalid")
        world = _positive(self.world_size_m, "raster world_size_m")
        if len(self.pixels) != self.size * self.size:
            raise GeometryError("raster occupancy byte count differs")
        if any(value not in (0, 255) for value in self.pixels):
            raise GeometryError("raster occupancy must use 0/255 bytes")
        expected = hashlib.sha256(self.pixels).hexdigest()
        if self.sha256 != expected:
            raise GeometryError("raster occupancy SHA-256 differs")
        object.__setattr__(self, "world_size_m", world)

    @property
    def cell_m(self) -> float:
        return self.world_size_m / self.size

    @property
    def lower_m(self) -> float:
        return -self.world_size_m / 2.0

    @property
    def upper_m(self) -> float:
        return self.world_size_m / 2.0


@dataclass(frozen=True)
class PairSweep:
    minimum_clearance_m: float
    minimum_u: float
    contact_fraction: float
    entry_u: float | None
    exit_u: float | None


@dataclass(frozen=True)
class ObstacleSweep:
    minimum_clearance_lower_bound_m: float
    minimum_clearance_exact: bool
    minimum_u: float | None
    contact_fraction: float
    contact_intervals: tuple[tuple[float, float], ...]
    positive_clearance_proved: bool
    cells_examined: int


def _read_regular(path: Path, maximum: int, label: str) -> bytes:
    candidate = Path(os.path.abspath(os.fspath(path)))
    if os.path.realpath(candidate) != os.fspath(candidate):
        raise GeometryError(f"{label} has a symlink ancestor or leaf")
    try:
        metadata = candidate.lstat()
    except OSError as error:
        raise GeometryError(f"cannot stat {label}: {error}") from error
    if not stat.S_ISREG(metadata.st_mode):
        raise GeometryError(f"{label} must be a regular non-symlink file")
    if metadata.st_size <= 0 or metadata.st_size > maximum:
        raise GeometryError(f"{label} byte count is outside its bound")
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(candidate, flags)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino)
            or opened.st_size != metadata.st_size
        ):
            raise GeometryError(f"{label} identity changed before reading")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        ended = os.fstat(descriptor)
        if (
            (ended.st_dev, ended.st_ino, ended.st_size, ended.st_mtime_ns)
            != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        ):
            raise GeometryError(f"{label} changed while it was read")
    except OSError as error:
        raise GeometryError(f"cannot read {label}: {error}") from error
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError as error:
                raise GeometryError(f"cannot close {label}: {error}") from error
    try:
        after = candidate.lstat()
    except OSError as error:
        raise GeometryError(f"cannot restat {label}: {error}") from error
    before_id = (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
    after_id = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    if before_id != after_id or len(data) != metadata.st_size:
        raise GeometryError(f"{label} changed while it was read")
    return data


def decode_binary_grayscale_png(data: bytes) -> tuple[int, bytes]:
    """Decode the exact binary grayscale PNG subset used by the map freezer."""

    if not isinstance(data, bytes) or not (0 < len(data) <= MAX_MAP_BYTES):
        raise GeometryError("map PNG bytes are invalid")
    if not data.startswith(PNG_SIGNATURE):
        raise GeometryError("map does not have the PNG signature")
    cursor = len(PNG_SIGNATURE)
    width: int | None = None
    height: int | None = None
    compressed = bytearray()
    seen_ihdr = False
    seen_iend = False
    while cursor < len(data):
        if cursor + 12 > len(data):
            raise GeometryError("map PNG chunk is truncated")
        length = struct.unpack(">I", data[cursor : cursor + 4])[0]
        end = cursor + 12 + length
        if length > MAX_MAP_BYTES or end > len(data):
            raise GeometryError("map PNG chunk length is invalid")
        kind = data[cursor + 4 : cursor + 8]
        payload = data[cursor + 8 : cursor + 8 + length]
        expected_crc = struct.unpack(">I", data[cursor + 8 + length : end])[0]
        observed_crc = binascii.crc32(payload, binascii.crc32(kind)) & 0xFFFFFFFF
        if observed_crc != expected_crc:
            raise GeometryError("map PNG chunk CRC differs")
        cursor = end
        if kind == b"IHDR":
            if seen_ihdr or length != 13:
                raise GeometryError("map PNG IHDR is invalid")
            seen_ihdr = True
            width, height, depth, color, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", payload
            )
            if (
                width != height
                or not (1 <= width <= 4096)
                or depth != 8
                or color != 0
                or compression != 0
                or filtering != 0
                or interlace != 0
            ):
                raise GeometryError("map PNG format is outside the frozen subset")
        elif kind == b"IDAT":
            if not seen_ihdr or seen_iend:
                raise GeometryError("map PNG IDAT order is invalid")
            compressed.extend(payload)
            if len(compressed) > MAX_MAP_BYTES:
                raise GeometryError("map PNG compressed payload is too large")
        elif kind == b"IEND":
            if length != 0 or seen_iend:
                raise GeometryError("map PNG IEND is invalid")
            seen_iend = True
            break
        elif kind[0] & 0x20 == 0:
            raise GeometryError("map PNG contains an unknown critical chunk")
    if not seen_ihdr or not seen_iend or cursor != len(data):
        raise GeometryError("map PNG structure is incomplete")
    assert width is not None and height is not None
    try:
        raw = zlib.decompress(bytes(compressed))
    except zlib.error as error:
        raise GeometryError("map PNG payload cannot be decompressed") from error
    stride = width + 1
    if len(raw) != stride * height:
        raise GeometryError("map PNG decoded byte count differs")
    pixels = bytearray()
    for y in range(height):
        row = raw[y * stride : (y + 1) * stride]
        if row[0] != 0:
            raise GeometryError("map PNG uses a non-frozen row filter")
        if any(value not in (0, 255) for value in row[1:]):
            raise GeometryError("map PNG is not binary occupancy")
        pixels.extend(row[1:])
    return width, bytes(pixels)


def effective_raster_from_png_bytes(
    data: bytes,
    *,
    target_size: int = DEFAULT_EFFECTIVE_SIZE,
    world_size_m: float = DEFAULT_WORLD_SIZE_M,
) -> Raster:
    if type(target_size) is not int or not (1 <= target_size <= 4096):
        raise GeometryError("target_size is invalid")
    source_size, pixels = decode_binary_grayscale_png(data)
    x_sources = [(x * source_size) // target_size for x in range(target_size)]
    expanded = bytearray()
    for y in range(target_size):
        source_y = (y * source_size) // target_size
        row = source_y * source_size
        expanded.extend(pixels[row + x] for x in x_sources)
    raw = bytes(expanded)
    return Raster(target_size, world_size_m, raw, hashlib.sha256(raw).hexdigest())


def effective_raster_from_png(
    path: Path,
    *,
    expected_png_sha256: str | None = None,
    target_size: int = DEFAULT_EFFECTIVE_SIZE,
    world_size_m: float = DEFAULT_WORLD_SIZE_M,
) -> Raster:
    data = _read_regular(path, MAX_MAP_BYTES, "map PNG")
    if expected_png_sha256 is not None:
        if hashlib.sha256(data).hexdigest() != expected_png_sha256:
            raise GeometryError("map PNG SHA-256 differs")
    return effective_raster_from_png_bytes(
        data, target_size=target_size, world_size_m=world_size_m
    )


def swept_disc_pair(
    left_start: Sequence[object],
    left_end: Sequence[object],
    right_start: Sequence[object],
    right_end: Sequence[object],
    left_radius_m: object,
    right_radius_m: object,
) -> PairSweep:
    """Return exact linear-sweep separation and closed-disc contact duration."""

    ls = _point(left_start, "left_start")
    le = _point(left_end, "left_end")
    rs = _point(right_start, "right_start")
    re = _point(right_end, "right_end")
    radius = _positive(left_radius_m, "left_radius_m") + _positive(
        right_radius_m, "right_radius_m"
    )
    start = (ls[0] - rs[0], ls[1] - rs[1])
    delta = ((le[0] - ls[0]) - (re[0] - rs[0]), (le[1] - ls[1]) - (re[1] - rs[1]))
    scale = max(abs(start[0]), abs(start[1]), abs(delta[0]), abs(delta[1]), radius)
    sx, sy = start[0] / scale, start[1] / scale
    dx, dy = delta[0] / scale, delta[1] / scale
    rr = radius / scale
    quadratic = dx * dx + dy * dy
    dot = sx * dx + sy * dy
    minimum_u = min(1.0, max(0.0, -dot / quadratic)) if quadratic > 0.0 else 0.0
    minimum_distance = scale * math.hypot(sx + minimum_u * dx, sy + minimum_u * dy)
    clearance = minimum_distance - radius
    constant = sx * sx + sy * sy - rr * rr
    if quadratic == 0.0:
        if constant <= 0.0:
            return PairSweep(clearance, minimum_u, 1.0, 0.0, 1.0)
        return PairSweep(clearance, minimum_u, 0.0, None, None)
    linear = 2.0 * dot
    discriminant = linear * linear - 4.0 * quadratic * constant
    if discriminant < 0.0:
        return PairSweep(clearance, minimum_u, 0.0, None, None)
    root = math.sqrt(max(0.0, discriminant))
    enter = max(0.0, (-linear - root) / (2.0 * quadratic))
    leave = min(1.0, (-linear + root) / (2.0 * quadratic))
    if enter > leave:
        return PairSweep(clearance, minimum_u, 0.0, None, None)
    return PairSweep(clearance, minimum_u, max(0.0, leave - enter), enter, leave)


def _segment_point_distance_sq(
    start: tuple[float, float], end: tuple[float, float], point: tuple[float, float]
) -> tuple[float, float]:
    dx, dy = end[0] - start[0], end[1] - start[1]
    denominator = dx * dx + dy * dy
    u = 0.0 if denominator == 0.0 else min(
        1.0,
        max(0.0, ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / denominator),
    )
    qx, qy = start[0] + u * dx, start[1] + u * dy
    return (point[0] - qx) ** 2 + (point[1] - qy) ** 2, u


def _segment_intersection(
    start: tuple[float, float],
    end: tuple[float, float],
    low_x: float,
    low_y: float,
    high_x: float,
    high_y: float,
) -> tuple[float, float] | None:
    """Liang-Barsky interval in which a segment lies in a closed box."""

    enter, leave = 0.0, 1.0
    dx, dy = end[0] - start[0], end[1] - start[1]
    for origin, delta, low, high in (
        (start[0], dx, low_x, high_x),
        (start[1], dy, low_y, high_y),
    ):
        if delta == 0.0:
            if origin < low or origin > high:
                return None
            continue
        first, second = (low - origin) / delta, (high - origin) / delta
        if first > second:
            first, second = second, first
        enter = max(enter, first)
        leave = min(leave, second)
        if enter > leave:
            return None
    return (max(0.0, enter), min(1.0, leave))


def _segment_circle_interval(
    start: tuple[float, float],
    end: tuple[float, float],
    center: tuple[float, float],
    radius: float,
) -> tuple[float, float] | None:
    """Return the closed parameter interval inside a closed circle."""

    dx, dy = end[0] - start[0], end[1] - start[1]
    sx, sy = start[0] - center[0], start[1] - center[1]
    quadratic = dx * dx + dy * dy
    constant = sx * sx + sy * sy - radius * radius
    if quadratic == 0.0:
        return (0.0, 1.0) if constant <= 0.0 else None
    linear = 2.0 * (sx * dx + sy * dy)
    discriminant = linear * linear - 4.0 * quadratic * constant
    if discriminant < 0.0:
        return None
    root = math.sqrt(max(0.0, discriminant))
    enter = max(0.0, (-linear - root) / (2.0 * quadratic))
    leave = min(1.0, (-linear + root) / (2.0 * quadratic))
    return (enter, leave) if enter <= leave else None


def _segment_box_distance_sq(
    start: tuple[float, float],
    end: tuple[float, float],
    low_x: float,
    low_y: float,
    high_x: float,
    high_y: float,
) -> tuple[float, float]:
    if _segment_intersection(start, end, low_x, low_y, high_x, high_y) is not None:
        return 0.0, 0.0
    candidates: list[tuple[float, float]] = []
    for point in ((low_x, low_y), (low_x, high_y), (high_x, low_y), (high_x, high_y)):
        candidates.append(_segment_point_distance_sq(start, end, point))
    for u, point in ((0.0, start), (1.0, end)):
        qx = min(high_x, max(low_x, point[0]))
        qy = min(high_y, max(low_y, point[1]))
        candidates.append(((point[0] - qx) ** 2 + (point[1] - qy) ** 2, u))
    return min(candidates, key=lambda item: (item[0], item[1]))


def _merge_intervals(intervals: Iterable[tuple[float, float]]) -> tuple[tuple[float, float], ...]:
    ordered = sorted((max(0.0, a), min(1.0, b)) for a, b in intervals if a <= b)
    merged: list[list[float]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return tuple((item[0], item[1]) for item in merged)


def swept_disc_obstacles(
    start: Sequence[object],
    end: Sequence[object],
    radius_m: object,
    raster: Raster,
) -> ObstacleSweep:
    """Prove closed-disc contact/noncontact against raster cells and exterior.

    Only cells capable of touching the swept disc are enumerated.  If none do,
    the function proves strictly positive clearance but intentionally does not
    claim the exact distance to a farther obstacle.
    """

    first = _point(start, "start")
    last = _point(end, "end")
    radius = _positive(radius_m, "radius_m")
    cell = raster.cell_m
    lower, upper = raster.lower_m, raster.upper_m
    intervals: list[tuple[float, float]] = []
    minimum: tuple[float, float] | None = None
    cells_examined = 0

    safe = _segment_intersection(
        first, last, lower + radius, lower + radius, upper - radius, upper - radius
    )
    if safe is None:
        intervals.append((0.0, 1.0))
    else:
        if safe[0] > 0.0:
            intervals.append((0.0, safe[0]))
        if safe[1] < 1.0:
            intervals.append((safe[1], 1.0))
    boundary_candidates = (
        (first[0] - lower - radius, 0.0),
        (upper - first[0] - radius, 0.0),
        (first[1] - lower - radius, 0.0),
        (upper - first[1] - radius, 0.0),
        (last[0] - lower - radius, 1.0),
        (upper - last[0] - radius, 1.0),
        (last[1] - lower - radius, 1.0),
        (upper - last[1] - radius, 1.0),
    )
    minimum = min(boundary_candidates, key=lambda item: (item[0], item[1]))

    # One cell diagonal beyond the contact radius creates a finite positive
    # lower bound for every unenumerated square.  If an enumerated square (or
    # the exterior) is closer than that bound, its value is the exact global
    # minimum; otherwise only the conservative bound is reported.
    search_margin = cell * math.sqrt(2.0)
    search_radius = radius + search_margin
    x0 = max(0, math.floor((min(first[0], last[0]) - search_radius - lower) / cell))
    x1 = min(raster.size - 1, math.floor((max(first[0], last[0]) + search_radius - lower) / cell))
    y0 = max(0, math.floor((min(first[1], last[1]) - search_radius - lower) / cell))
    y1 = min(raster.size - 1, math.floor((max(first[1], last[1]) + search_radius - lower) / cell))
    for y in range(y0, y1 + 1):
        row = y * raster.size
        low_y = lower + y * cell
        high_y = low_y + cell
        for x in range(x0, x1 + 1):
            if raster.pixels[row + x] != 0:
                continue
            cells_examined += 1
            if cells_examined > MAX_OCCUPIED_CELLS:
                raise GeometryError("obstacle sweep cell bound exceeded")
            low_x = lower + x * cell
            high_x = low_x + cell
            distance_sq, minimum_u = _segment_box_distance_sq(
                first, last, low_x, low_y, high_x, high_y
            )
            clearance = math.sqrt(max(0.0, distance_sq)) - radius
            candidate = (clearance, minimum_u)
            if minimum is None or candidate < minimum:
                minimum = candidate
            # The exact Minkowski sum of a closed square and a closed disc is
            # two expanded strips plus four quarter-circles.  Intersecting
            # the segment with the full circles is safe because the portions
            # outside the relevant quadrant are already inside a strip.
            contact_parts = (
                _segment_intersection(
                    first,
                    last,
                    low_x - radius,
                    low_y,
                    high_x + radius,
                    high_y,
                ),
                _segment_intersection(
                    first,
                    last,
                    low_x,
                    low_y - radius,
                    high_x,
                    high_y + radius,
                ),
                *(
                    _segment_circle_interval(first, last, corner, radius)
                    for corner in (
                        (low_x, low_y),
                        (low_x, high_y),
                        (high_x, low_y),
                        (high_x, high_y),
                    )
                ),
            )
            intervals.extend(part for part in contact_parts if part is not None)
    merged = _merge_intervals(intervals)
    fraction = sum(end_u - start_u for start_u, end_u in merged)
    assert minimum is not None
    conservative_minimum = min(minimum[0], search_margin)
    proved = not merged and conservative_minimum > SAFETY_EPSILON_M
    return ObstacleSweep(
        minimum_clearance_lower_bound_m=conservative_minimum,
        minimum_clearance_exact=minimum[0] <= search_margin,
        minimum_u=minimum[1],
        contact_fraction=fraction,
        contact_intervals=merged,
        positive_clearance_proved=proved,
        cells_examined=cells_examined,
    )


def analyze_sweeps(
    sweeps: Sequence[Mapping[str, object]],
    raster: Raster,
    *,
    timestep_seconds: object = 0.1,
) -> dict[str, object]:
    """Analyze a closed, ordered list of projected pre/post robot sweeps."""

    dt = _positive(timestep_seconds, "timestep_seconds")
    if not isinstance(sweeps, (list, tuple)) or not sweeps:
        raise GeometryError("sweeps must be a nonempty sequence")
    minimum_pair = math.inf
    minimum_obstacle = math.inf
    pair_contact = 0.0
    obstacle_contact = 0.0
    pair_nonpositive = 0
    obstacle_nonpositive = 0
    pair_sweeps = 0
    obstacle_sweeps = 0
    all_obstacle_positive_proved = True
    expected_ids: tuple[int, ...] | None = None
    prior_end: dict[int, tuple[tuple[float, float], float]] | None = None
    for sweep_index, sweep in enumerate(sweeps):
        if not isinstance(sweep, Mapping) or set(sweep) != {"tick", "start", "end"}:
            raise GeometryError(f"sweep {sweep_index} schema differs")
        tick = sweep["tick"]
        if type(tick) is not int or tick != sweep_index:
            raise GeometryError(f"sweep {sweep_index} tick differs")
        start = sweep["start"]
        end = sweep["end"]
        if not isinstance(start, Sequence) or isinstance(start, (str, bytes)):
            raise GeometryError(f"sweep {sweep_index} start robots differ")
        if not isinstance(end, Sequence) or isinstance(end, (str, bytes)):
            raise GeometryError(f"sweep {sweep_index} end robots differ")
        if len(start) != len(end) or not start:
            raise GeometryError(f"sweep {sweep_index} robot counts differ")
        parsed_start: dict[int, tuple[tuple[float, float], float]] = {}
        parsed_end: dict[int, tuple[tuple[float, float], float]] = {}
        for label, values, target in (("start", start, parsed_start), ("end", end, parsed_end)):
            for index, robot in enumerate(values):
                if not isinstance(robot, Mapping) or set(robot) != {"rid", "position", "radius_m"}:
                    raise GeometryError(f"sweep {sweep_index} {label} robot schema differs")
                rid = robot["rid"]
                if type(rid) is not int or rid < 0 or rid in target:
                    raise GeometryError(f"sweep {sweep_index} {label} robot id differs")
                target[rid] = (
                    _point(robot["position"], f"sweep {sweep_index} {label} robot {index}"),
                    _positive(robot["radius_m"], f"sweep {sweep_index} {label} radius"),
                )
        ids = tuple(sorted(parsed_start))
        if tuple(sorted(parsed_end)) != ids:
            raise GeometryError(f"sweep {sweep_index} robot identities differ")
        if expected_ids is None:
            expected_ids = ids
        elif ids != expected_ids:
            raise GeometryError(f"sweep {sweep_index} robot identities changed")
        if prior_end is not None and parsed_start != prior_end:
            raise GeometryError(
                f"sweep {sweep_index} starts after an unverified state gap"
            )
        for rid in ids:
            if parsed_start[rid][1] != parsed_end[rid][1]:
                raise GeometryError(f"sweep {sweep_index} robot radius changed")
        for left_index, left_id in enumerate(ids):
            left_start, left_radius = parsed_start[left_id]
            left_end, _ = parsed_end[left_id]
            obstacle = swept_disc_obstacles(left_start, left_end, left_radius, raster)
            obstacle_sweeps += 1
            all_obstacle_positive_proved &= obstacle.positive_clearance_proved
            minimum_obstacle = min(
                minimum_obstacle, obstacle.minimum_clearance_lower_bound_m
            )
            if obstacle.minimum_clearance_lower_bound_m <= 0.0:
                obstacle_nonpositive += 1
            obstacle_contact += obstacle.contact_fraction * dt
            for right_id in ids[left_index + 1 :]:
                right_start, right_radius = parsed_start[right_id]
                right_end, _ = parsed_end[right_id]
                pair = swept_disc_pair(
                    left_start, left_end, right_start, right_end, left_radius, right_radius
                )
                pair_sweeps += 1
                minimum_pair = min(minimum_pair, pair.minimum_clearance_m)
                if pair.minimum_clearance_m <= 0.0:
                    pair_nonpositive += 1
                pair_contact += pair.contact_fraction * dt
        prior_end = parsed_end
    geometry_contact_free = (
        minimum_pair > SAFETY_EPSILON_M
        and minimum_obstacle > SAFETY_EPSILON_M
        and pair_nonpositive == 0
        and obstacle_nonpositive == 0
        and pair_contact == 0.0
        and obstacle_contact == 0.0
        and all_obstacle_positive_proved
    )
    reasons: list[str] = []
    if (
        minimum_pair <= SAFETY_EPSILON_M
        or pair_nonpositive
        or pair_contact > 0.0
    ):
        reasons.append("pair-contact-or-clearance-not-above-safety-epsilon")
    if (
        minimum_obstacle <= SAFETY_EPSILON_M
        or obstacle_nonpositive
        or obstacle_contact > 0.0
        or not all_obstacle_positive_proved
    ):
        reasons.append("obstacle-contact-or-clearance-not-proved-above-safety-epsilon")
    return {
        "schema_version": "dancers-headless-swept-geometry-v1",
        "model": "linear-swept-closed-discs-plus-closed-effective-raster-squares-v1",
        "raster_sha256": raster.sha256,
        "timestep_seconds": dt,
        "sweep_count": len(sweeps),
        "robot_count": len(expected_ids or ()),
        "pair_sweeps_verified": pair_sweeps,
        "obstacle_sweeps_verified": obstacle_sweeps,
        "minimum_pair_clearance_m": minimum_pair,
        "minimum_obstacle_clearance_lower_bound_m": minimum_obstacle,
        "pair_nonpositive_sweep_count": pair_nonpositive,
        "obstacle_nonpositive_sweep_count": obstacle_nonpositive,
        "pair_contact_exposure_seconds": pair_contact,
        "obstacle_contact_exposure_seconds": obstacle_contact,
        "obstacle_positive_clearance_proved_for_every_sweep": all_obstacle_positive_proved,
        "clearance_safety_epsilon_m": SAFETY_EPSILON_M,
        "geometry_contact_free": geometry_contact_free,
        "geometry_failure_reasons": reasons,
        "task_valid": False,
        "task_validity_determined": False,
        "scientific_execution_authorized": False,
        "timing_unlock": False,
        "validation_unlock": False,
        "benchmark_claim_ready": False,
    }
