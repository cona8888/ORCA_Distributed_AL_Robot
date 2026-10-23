#!/usr/bin/env python3
"""Build a self-contained, solver-free planar-physics replay viewer.

The evidence exporter is the only input projection layer.  It verifies both
JSONL artifacts before this module receives any scene or frame data.  This
builder never imports, invokes, or supervises a simulator executable.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import html
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


SCRIPT_DIR = Path(__file__).resolve().parent
REPOSITORY_ROOT = SCRIPT_DIR.parents[1]
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import export_physics_replay_bundle_v1 as replay_export  # noqa: E402


BUNDLE_SCHEMA = "dancers-planar-physics-replay-bundle-v1"
DEFAULT_MAX_FRAMES = 100_001
DEFAULT_MAX_BUNDLE_BYTES = 47 * 1024 * 1024
DEFAULT_MAX_HTML_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_ROBOT_SAMPLES = 2_000_000
DEFAULT_MAX_CONTACT_ITEMS = 2_000_000
MAX_TITLE_CHARS = 160
class PhysicsReplayViewerError(ValueError):
    """A verified bundle cannot safely or usefully be rendered."""


@dataclass(frozen=True)
class PhysicsReplayBuildResult:
    frame_count: int
    robot_samples: int
    contact_items: int
    bundle_sha256: str
    output_bytes: int
    html_bytes: bytes


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PhysicsReplayViewerError(message)


def _canonical_bundle_bytes(bundle: Mapping[str, Any]) -> bytes:
    try:
        return json.dumps(
            bundle,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise PhysicsReplayViewerError(
            f"replay bundle is not finite canonical JSON data: {error}"
        ) from error


def _validate_resource_limits(
    *,
    max_frames: int,
    max_bundle_bytes: int,
    max_html_bytes: int,
    max_robot_samples: int,
    max_contact_items: int,
) -> None:
    limits = (
        ("max_frames", max_frames, DEFAULT_MAX_FRAMES),
        ("max_bundle_bytes", max_bundle_bytes, DEFAULT_MAX_BUNDLE_BYTES),
        ("max_html_bytes", max_html_bytes, DEFAULT_MAX_HTML_BYTES),
        ("max_robot_samples", max_robot_samples, DEFAULT_MAX_ROBOT_SAMPLES),
        ("max_contact_items", max_contact_items, DEFAULT_MAX_CONTACT_ITEMS),
    )
    for name, value, maximum in limits:
        _require(
            type(value) is int and 1 <= value <= maximum,
            f"{name} must be an integer in 1..{maximum}",
        )


def _bundle_inventory(
    bundle: Mapping[str, Any],
    *,
    max_frames: int,
    max_bundle_bytes: int,
    max_robot_samples: int,
    max_contact_items: int,
    allow_analytic_fallback: bool,
) -> tuple[int, int, int]:
    _require(type(bundle) is dict, "replay bundle must be an object")
    _require(bundle.get("schema") == BUNDLE_SCHEMA, "unsupported replay bundle schema")
    _require(
        bundle.get("encoding") == "binary64-bits-lower-hex-v1",
        "replay bundle has an unsupported numeric encoding",
    )

    coordinate = bundle.get("coordinate_system")
    _require(type(coordinate) is dict, "bundle.coordinate_system must be an object")
    _require(
        coordinate.get("frame") == "centered-100m-raster-world"
        and coordinate.get("world_bounds_m")
        == {"x_min": -50.0, "x_max": 50.0, "y_min": -50.0, "y_max": 50.0}
        and coordinate.get("x_direction") == "increasing raster column, right"
        and coordinate.get("y_direction") == "increasing raster row, down"
        and coordinate.get("heading_positive_direction") == "from +x toward +y"
        and coordinate.get("displayed_positive_rotation") == "clockwise"
        and coordinate.get("vertical_flip") is False,
        "bundle coordinate system is not the frozen x-right/y-down 100 m world",
    )

    classification = bundle.get("classification")
    expected_classification = {
        "role": "offline-engineering-diagnostic-only",
        "registered_scientific_run": False,
        "scientific_evidence": False,
        "scientific_evidence_eligible": False,
        "paper_metrics_applicable": False,
        "timing_eligible": False,
        "execution_plan_bound": False,
        "official_result_namespace": False,
        "method_identity_authenticated": False,
        "display_labels_authenticate_methods": False,
    }
    _require(
        classification == expected_classification,
        "bundle classification/science/method-binding flags differ from the engineering-only contract",
    )
    verification = bundle.get("verification")
    asset_authenticated = (
        type(verification) is dict
        and verification.get("identity_files_externally_verified") is True
        and verification.get("raster_available") is True
    )
    analytic_assets_unavailable = (
        type(verification) is dict
        and verification.get("identity_files_externally_verified") is False
        and verification.get("raster_available") is False
    )
    _require(
        asset_authenticated or analytic_assets_unavailable,
        "identity-file and raster verification flags must agree",
    )
    _require(
        asset_authenticated or allow_analytic_fallback,
        "analytic-origin replay requires explicit allow_analytic_fallback=True",
    )
    expected_verification = {
        "strict_replay_leaves_and_formulas": True,
        "common_scene_pair_verified": True,
        "identity_files_externally_verified": asset_authenticated,
        "raster_available": asset_authenticated,
        "solver_launched_by_exporter": False,
    }
    _require(
        verification == expected_verification,
        "bundle was not produced by the strict solver-free replay verifier path",
    )
    pair = bundle.get("pair_comparison")
    _require(
        type(pair) is dict
        and pair.get("valid_pair") is True
        and type(pair.get("equal")) is bool
        and type(pair.get("events_compared")) is int
        and pair.get("events_compared") >= 0
        and (pair.get("first_discordance") is None) == pair.get("equal")
        and pair.get("scientific_pair_eligible") is False
        and pair.get("method_pair_identity_authenticated") is False,
        "bundle pair classification or common-scene verification differs",
    )

    scene = bundle.get("scene")
    _require(type(scene) is dict, "bundle.scene must be an object")
    robot_count = scene.get("robot_count")
    horizon = scene.get("controller_ticks")
    physics_steps = scene.get("physics_steps")
    simulated_time_ms = scene.get("simulated_time_ms")
    _require(type(robot_count) is int and 1 <= robot_count <= 4096, "scene robot_count is outside 1..4096")
    _require(
        type(horizon) is int
        and horizon > 0
        and type(physics_steps) is int
        and physics_steps == 10 * horizon
        and simulated_time_ms == 10 * physics_steps,
        "scene does not satisfy the exact H/10H/100H physics lattice",
    )
    raster = scene.get("raster")
    identity_files = scene.get("identity_files_verified")
    descriptors = scene.get("environment_descriptors")
    _require(type(raster) is dict and type(identity_files) is dict and type(descriptors) is dict, "scene verification assets are incomplete")
    if not asset_authenticated:
        _require(
            verification.get("identity_files_externally_verified") is False
            and verification.get("raster_available") is False
            and raster.get("available") is False
            and identity_files.get("available") is False
            and identity_files.get("all_declared_identity_files_verified") is False
            and descriptors.get("available") is False,
            "analytic fallback must remain explicitly sidecar/raster/descriptor unavailable",
        )
    else:
        _require(
            verification.get("identity_files_externally_verified") is True
            and verification.get("raster_available") is True
            and raster.get("available") is True
            and identity_files.get("available") is True
            and identity_files.get("all_declared_identity_files_verified") is True
            and descriptors.get("available") is True,
            "production replay requires externally verified identities, occupancy raster, and descriptors",
        )
        _require(
            raster.get("width_px") == 1000
            and raster.get("height_px") == 1000
            and raster.get("encoding") == "row-major-msb-packed-occupied-1-v1",
            "authenticated occupancy raster geometry/encoding differs",
        )
        encoded = raster.get("occupied_bitset_base64")
        _require(type(encoded) is str, "authenticated occupancy raster payload is missing")
        try:
            packed = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as error:
            raise PhysicsReplayViewerError("occupancy raster is not canonical base64") from error
        _require(len(packed) == 125_000, "occupancy raster must contain exactly 1,000,000 packed bits")
        raw = bytearray(1_000_000)
        output_index = 0
        for value in packed:
            for shift in range(7, -1, -1):
                raw[output_index] = 0 if value & (1 << shift) else 255
                output_index += 1
        _require(
            hashlib.sha256(raw).hexdigest() == raster.get("effective_occupancy_sha256"),
            "occupancy raster does not reproduce its authenticated effective SHA-256",
        )
        _require(
            raster.get("rendering")
            == {
                "occupied_bit": 1,
                "occupied_uint8": 0,
                "free_bit": 0,
                "free_uint8": 255,
                "bit_order": "most-significant first",
                "row_order": "increasing y, top to bottom",
                "vertical_flip": False,
            },
            "occupancy raster rendering policy differs from nearest-neighbour y-down v1",
        )

    bounds = bundle.get("bounds")
    _require(type(bounds) is dict, "bundle bounds must be an object")
    _require(
        bounds.get("hard_max_frames") == 100_001
        and type(bounds.get("requested_max_frames")) is int
        and bounds["requested_max_frames"] <= max_frames
        and bounds.get("actual_frames_per_run") == physics_steps + 1
        and bounds.get("hard_max_bundle_bytes") == 64 * 1024 * 1024
        and bounds.get("silent_downsampling") is False,
        "bundle bounds/downsampling declaration differs from the frozen viewer policy",
    )

    labels = bundle.get("display_only_labels")
    _require(type(labels) is dict, "display-only label declaration is missing")
    sides: list[list[Any]] = []
    robot_samples = 0
    contact_items = 0
    for side_name in ("left", "right"):
        side = bundle.get(side_name)
        _require(type(side) is dict, f"bundle.{side_name} must be an object")
        expected_label = labels.get(side_name)
        _require(
            type(expected_label) is str
            and expected_label
            and expected_label.strip() == expected_label
            and len(expected_label) <= 80
            and not any(ord(character) < 0x20 for character in expected_label)
            and side.get("display_only_label") == expected_label,
            f"bundle.{side_name} label is not explicitly display-only",
        )
        side_verification = side.get("verification")
        _require(
            type(side_verification) is dict
            and side_verification.get("detail") == "replay"
            and side_verification.get("verification_level") == "replay-full-leaf-and-formula"
            and side_verification.get("scientific_evidence") is False
            and side_verification.get("execution_plan_bound") is False
            and side_verification.get("official_result_namespace") is False
            and side_verification.get("method_identity_authenticated") is False,
            f"bundle.{side_name} verification/classification differs",
        )
        frames = side.get("frames")
        commands = side.get("command_frames")
        boundaries = side.get("boundaries")
        _require(type(frames) is list and len(frames) == physics_steps + 1, f"bundle.{side_name}.frames must contain exactly J+1 samples")
        _require(type(commands) is list and len(commands) == horizon, f"bundle.{side_name}.command_frames must contain exactly H frames")
        _require(type(boundaries) is list and len(boundaries) == horizon + 1, f"bundle.{side_name}.boundaries must contain exactly H+1 frames")
        _require(
            len(frames) <= max_frames,
            f"bundle frame count exceeds laptop limit {max_frames}",
        )
        for position, frame in enumerate(frames):
            _require(type(frame) is dict, f"bundle.{side_name}.frames[{position}] must be an object")
            frame_index = frame.get("frame_index")
            time_ms = frame.get("time_ms")
            _require(frame_index == position and type(frame_index) is int, f"bundle.{side_name} frame indices are not exact 0..J")
            _require(time_ms == 10 * position and type(time_ms) is int, f"bundle.{side_name} frame times are not exact 10 ms samples")
            if position == 0:
                _require(
                    frame.get("tick") is None
                    and frame.get("substep") is None
                    and frame.get("held_command_tick") is None
                    and frame.get("route_boundary_index") == 0,
                    f"bundle.{side_name} initial frame mapping differs",
                )
            else:
                expected_tick = (position - 1) // 10
                expected_substep = (position - 1) % 10
                _require(
                    frame.get("tick") == expected_tick
                    and frame.get("substep") == expected_substep
                    and frame.get("route_boundary_index") == position // 10
                    and frame.get("held_command_tick") == expected_tick,
                    f"bundle.{side_name} held-command/substep/boundary mapping differs at frame {position}",
                )
            body_state = frame.get("body_state")
            contacts = frame.get("contacts")
            _require(type(body_state) is dict, f"bundle.{side_name} frame body state is missing")
            _require(type(contacts) is dict, f"bundle.{side_name} frame contacts are missing")
            bodies = body_state.get("bodies")
            items = contacts.get("contacts")
            _require(type(bodies) is list and len(bodies) == robot_count, f"bundle.{side_name} frame bodies must contain exactly N robots")
            _require(type(items) is list, f"bundle.{side_name} frame contacts must be an array")
            if position == 0:
                _require(
                    contacts.get("available") is False
                    and contacts.get("sha256") is None
                    and not items,
                    f"bundle.{side_name} initial contact frame must be explicitly unavailable/empty",
                )
            else:
                _require(
                    contacts.get("available") is True
                    and isinstance(contacts.get("sha256"), str),
                    f"bundle.{side_name} post-step contact frame must be available",
                )
            _require(
                all(type(body) is dict and body.get("id") == rid for rid, body in enumerate(bodies)),
                f"bundle.{side_name} body IDs must be exact ordered 0..N-1",
            )
            robot_samples += len(bodies)
            contact_items += len(items)
            _require(
                robot_samples <= max_robot_samples,
                f"bundle robot-sample count exceeds laptop limit {max_robot_samples}",
            )
            _require(
                contact_items <= max_contact_items,
                f"bundle contact-item count exceeds laptop limit {max_contact_items}",
            )
        sides.append(frames)
        for command_tick, command in enumerate(commands):
            _require(
                type(command) is dict
                and command.get("tick") == command_tick
                and command.get("time_ms") == 100 * command_tick
                and type(command.get("robots")) is list
                and len(command["robots"]) == robot_count
                and all(
                    type(robot) is dict and robot.get("id") == rid
                    for rid, robot in enumerate(command["robots"])
                ),
                f"bundle.{side_name} command lattice/inventory differs at tick {command_tick}",
            )
        for boundary_index, boundary in enumerate(boundaries):
            _require(
                type(boundary) is dict
                and boundary.get("boundary") == boundary_index
                and boundary.get("time_ms") == 100 * boundary_index
                and type(boundary.get("robots")) is list
                and len(boundary["robots"]) == robot_count,
                f"bundle.{side_name} boundary lattice differs at {boundary_index}",
            )
            _require(
                all(
                    type(robot) is dict and robot.get("id") == rid
                    for rid, robot in enumerate(boundary["robots"])
                ),
                f"bundle.{side_name} boundary robot IDs differ at {boundary_index}",
            )

    _require(
        len(sides[0]) == len(sides[1]),
        "paired replay frame counts differ after verified projection",
    )
    for index, (left, right) in enumerate(zip(sides[0], sides[1])):
        _require(
            left.get("frame_index") == right.get("frame_index")
            and left.get("time_ms") == right.get("time_ms"),
            f"paired replay lattice differs at frame {index}",
        )
    _require(pair.get("common_scene_semantic_sha256") == scene.get("common_scene_semantic_sha256"), "pair and scene semantic identities differ")
    return len(sides[0]), robot_samples, contact_items


def _sha256_csp_token(text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return "sha256-" + base64.b64encode(digest).decode("ascii")


def render_physics_replay_bundle(
    bundle: Mapping[str, Any],
    *,
    title: str = "Synchronized planar-physics replay",
    max_frames: int = DEFAULT_MAX_FRAMES,
    max_bundle_bytes: int = DEFAULT_MAX_BUNDLE_BYTES,
    max_html_bytes: int = DEFAULT_MAX_HTML_BYTES,
    max_robot_samples: int = DEFAULT_MAX_ROBOT_SAMPLES,
    max_contact_items: int = DEFAULT_MAX_CONTACT_ITEMS,
    allow_analytic_fallback: bool = False,
) -> PhysicsReplayBuildResult:
    """Render one already verified pair bundle into deterministic HTML bytes."""
    _require(type(title) is str and title.strip() == title and title, "title must be nonempty and trimmed")
    _require(len(title) <= MAX_TITLE_CHARS, f"title exceeds {MAX_TITLE_CHARS} characters")
    _validate_resource_limits(
        max_frames=max_frames,
        max_bundle_bytes=max_bundle_bytes,
        max_html_bytes=max_html_bytes,
        max_robot_samples=max_robot_samples,
        max_contact_items=max_contact_items,
    )
    frame_count, robot_samples, contact_items = _bundle_inventory(
        bundle,
        max_frames=max_frames,
        max_bundle_bytes=max_bundle_bytes,
        max_robot_samples=max_robot_samples,
        max_contact_items=max_contact_items,
        allow_analytic_fallback=allow_analytic_fallback,
    )
    bundle_bytes = _canonical_bundle_bytes(bundle)
    _require(
        len(bundle_bytes) <= max_bundle_bytes,
        f"verified replay bundle exceeds laptop limit {max_bundle_bytes} bytes",
    )
    _require(
        bundle.get("bounds", {}).get("actual_bundle_bytes_when_canonical")
        == len(bundle_bytes) + 1,
        "bundle canonical byte declaration differs",
    )
    bundle_sha256 = hashlib.sha256(bundle_bytes).hexdigest()
    payload_base64 = base64.b64encode(bundle_bytes).decode("ascii")
    style_token = _sha256_csp_token(STYLE_TEXT)
    script_token = _sha256_csp_token(SCRIPT_TEXT)
    csp = (
        "default-src 'none'; base-uri 'none'; connect-src 'none'; "
        "font-src 'none'; form-action 'none'; frame-src 'none'; img-src 'none'; "
        "media-src 'none'; object-src 'none'; worker-src 'none'; "
        f"style-src '{style_token}'; script-src '{script_token}'"
    )
    replacements = {
        "__TITLE__": html.escape(title, quote=True),
        "__CSP__": html.escape(csp, quote=True),
        "__STYLE__": STYLE_TEXT,
        "__SCRIPT__": SCRIPT_TEXT,
        "__PAYLOAD_BASE64__": payload_base64,
        "__BUNDLE_SHA256__": bundle_sha256,
    }
    rendered = re.sub(
        r"__(?:TITLE|CSP|STYLE|SCRIPT|PAYLOAD_BASE64|BUNDLE_SHA256)__",
        lambda match: replacements[match.group(0)],
        HTML_TEMPLATE,
    ).encode("utf-8")
    _require(
        len(rendered) <= max_html_bytes,
        f"self-contained replay viewer exceeds laptop limit {max_html_bytes} bytes",
    )
    return PhysicsReplayBuildResult(
        frame_count=frame_count,
        robot_samples=robot_samples,
        contact_items=contact_items,
        bundle_sha256=bundle_sha256,
        output_bytes=len(rendered),
        html_bytes=rendered,
    )


def build_physics_replay_viewer(
    left_evidence: Path | str,
    right_evidence: Path | str,
    *,
    title: str = "Synchronized planar-physics replay",
    left_label: str = "Run A",
    right_label: str = "Run B",
    repository_root: Path | str = REPOSITORY_ROOT,
    max_frames: int = DEFAULT_MAX_FRAMES,
    max_bundle_bytes: int = DEFAULT_MAX_BUNDLE_BYTES,
    max_html_bytes: int = DEFAULT_MAX_HTML_BYTES,
    max_robot_samples: int = DEFAULT_MAX_ROBOT_SAMPLES,
    max_contact_items: int = DEFAULT_MAX_CONTACT_ITEMS,
    allow_analytic_fallback: bool = False,
) -> PhysicsReplayBuildResult:
    """Verify, project, and render two replay-detail engineering artifacts."""
    _validate_resource_limits(
        max_frames=max_frames,
        max_bundle_bytes=max_bundle_bytes,
        max_html_bytes=max_html_bytes,
        max_robot_samples=max_robot_samples,
        max_contact_items=max_contact_items,
    )
    for name, value in (("left_label", left_label), ("right_label", right_label)):
        _require(type(value) is str and value.strip() == value and value, f"{name} must be nonempty and trimmed")
        _require(len(value) <= 80, f"{name} exceeds 80 characters")
    bundle = replay_export.build_bundle(
        left_evidence,
        right_evidence,
        repository_root=Path(repository_root),
        left_label=left_label,
        right_label=right_label,
        allow_analytic_fallback=allow_analytic_fallback,
        max_frames=max_frames,
        max_bundle_bytes=max_bundle_bytes,
    )
    return render_physics_replay_bundle(
        bundle,
        title=title,
        max_frames=max_frames,
        max_bundle_bytes=max_bundle_bytes,
        max_html_bytes=max_html_bytes,
        max_robot_samples=max_robot_samples,
        max_contact_items=max_contact_items,
        allow_analytic_fallback=allow_analytic_fallback,
    )


def _secure_output_parent(raw: Path, repository_root: Path) -> tuple[Path, int, str]:
    _require(raw.is_absolute(), "output path must be absolute")
    _require(Path(os.path.normpath(raw)) == raw, "output path must be normalized")
    _require(raw.name not in ("", ".", ".."), "output path must name a file")
    _require(raw.suffix.lower() == ".html", "output path must end in .html")
    _require(len(os.fspath(raw).encode("utf-8")) <= 4096, "output path exceeds 4096 UTF-8 bytes")
    _require(len(raw.name.encode("utf-8")) <= 255, "output file name exceeds 255 UTF-8 bytes")
    _require(not repository_root.is_symlink(), "repository root must not be a symbolic link")
    repository = repository_root.resolve(strict=True)
    repository_status = os.stat(repository, follow_symlinks=False)
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    current = os.open("/", flags)
    try:
        for component in raw.parent.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=current)
            os.close(current)
            current = next_descriptor
            status = os.fstat(current)
            if (
                status.st_dev == repository_status.st_dev
                and status.st_ino == repository_status.st_ino
            ):
                raise PhysicsReplayViewerError(
                    "replay viewer HTML must be written outside the repository and official result namespaces"
                )
        parent_status = os.fstat(current)
        _require(parent_status.st_uid == os.geteuid(), "output parent must be owned by the effective user")
        _require(
            stat.S_IMODE(parent_status.st_mode) & 0o022 == 0,
            "output parent must not be group/world writable",
        )
        return raw, current, raw.name
    except BaseException:
        os.close(current)
        raise


def _write_exclusive(parent_descriptor: int, basename: str, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    created = False
    try:
        descriptor = os.open(basename, flags, 0o600, dir_fd=parent_descriptor)
        created = True
        os.fchmod(descriptor, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write while publishing replay viewer")
            view = view[written:]
        os.fsync(descriptor)
        status = os.fstat(descriptor)
        _require(
            stat.S_ISREG(status.st_mode)
            and status.st_uid == os.geteuid()
            and stat.S_IMODE(status.st_mode) == 0o600
            and status.st_size == len(data),
            "published replay viewer failed regular-file/owner/mode/size checks",
        )
        os.fsync(parent_descriptor)
    except FileExistsError as error:
        raise PhysicsReplayViewerError(
            "output already exists; replay viewer publication is create-exclusive"
        ) from error
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
            descriptor = -1
        if created:
            try:
                os.unlink(basename, dir_fd=parent_descriptor)
            except OSError:
                pass
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a synchronized offline viewer from two verified physics replay artifacts"
    )
    parser.add_argument("left_evidence", type=Path)
    parser.add_argument("right_evidence", type=Path)
    parser.add_argument("output", type=Path, help="new normalized absolute .html path in a private parent outside the repository")
    parser.add_argument("--title", default="Synchronized planar-physics replay")
    parser.add_argument("--left-label", default="Run A")
    parser.add_argument("--right-label", default="Run B")
    parser.add_argument(
        "--analytic-fixture-only",
        action="store_true",
        help="TEST ONLY: allow the private analytic fixture without authenticated sidecar files",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_cli().parse_args(argv)
    try:
        result = build_physics_replay_viewer(
            args.left_evidence,
            args.right_evidence,
            title=args.title,
            left_label=args.left_label,
            right_label=args.right_label,
            allow_analytic_fallback=args.analytic_fixture_only,
        )
        output, parent_descriptor, basename = _secure_output_parent(
            args.output,
            REPOSITORY_ROOT,
        )
        try:
            _write_exclusive(parent_descriptor, basename, result.html_bytes)
        finally:
            os.close(parent_descriptor)
    except (PhysicsReplayViewerError, replay_export.ReplayBundleError, OSError) as error:
        print(f"physics replay viewer: {error}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "schema": "dancers-planar-physics-replay-viewer-v1",
                "classification": "non-scientific-engineering-offline-replay",
                "output": str(output),
                "frames": result.frame_count,
                "robot_samples": result.robot_samples,
                "contact_items": result.contact_items,
                "bundle_sha256": result.bundle_sha256,
                "bytes": result.output_bytes,
            },
            separators=(",", ":"),
        )
    )
    return 0


STYLE_TEXT = r"""
:root {
  color-scheme: light dark;
  --page: #f3f5f8;
  --surface: #ffffff;
  --surface-2: #eef2f6;
  --field: #fbfcfe;
  --text: #18212d;
  --muted: #58677a;
  --line: #ccd5df;
  --grid: rgba(28,47,69,.09);
  --run-a: #0868ac;
  --run-b: #c45100;
  --contact: #a20d45;
  --goal: #087c62;
  --waypoint: #865a00;
  --wall: #33475e;
  --warning-bg: #fff3d6;
  --warning-text: #6c4800;
  --focus: #005fcc;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--page);
  color: var(--text);
  font: 14px/1.4 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
button, select, input { font: inherit; }
button, select {
  min-height: 34px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: var(--surface-2);
  color: var(--text);
  padding: 5px 10px;
}
button:hover { border-color: var(--focus); }
button[aria-pressed="true"] { background: var(--text); color: var(--page); }
button:focus-visible, select:focus-visible, input:focus-visible {
  outline: 2px solid var(--focus);
  outline-offset: 2px;
}
.app { max-width: 1440px; margin: 0 auto; padding: 16px; }
.topline { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
h1 { margin: 0; font-size: 21px; font-weight: 500; }
.subtitle { color: var(--muted); margin-top: 2px; }
.badges { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
.badge {
  display: inline-flex;
  align-items: center;
  min-height: 26px;
  padding: 3px 8px;
  border: 1px solid var(--line);
  border-radius: 999px;
  background: var(--surface);
  font-size: 12px;
  white-space: nowrap;
}
.badge.warning { background: var(--warning-bg); color: var(--warning-text); border-color: currentColor; }
.controls {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  padding: 10px;
  border: 1px solid var(--line);
  border-radius: 10px;
  background: var(--surface);
}
.button-group { display: inline-flex; gap: 4px; }
.timeline-group { display: flex; flex: 1 1 280px; align-items: center; gap: 8px; min-width: 220px; }
#timeline { width: 100%; accent-color: var(--run-a); }
.readout { min-width: 118px; color: var(--muted); font-variant-numeric: tabular-nums; text-align: right; }
.toggle { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
.toggle input { accent-color: var(--run-a); }
.arena-grid { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 10px; margin-top: 10px; }
.arena-grid[data-mode="overlay"] { grid-template-columns: minmax(0,1fr); }
.arena-grid[data-mode="overlay"] .panel.right { display: none; }
.panel { min-width: 0; border: 1px solid var(--line); border-radius: 10px; overflow: hidden; background: var(--surface); }
.panel-head { display: flex; align-items: center; justify-content: space-between; gap: 10px; padding: 8px 10px; border-bottom: 1px solid var(--line); }
.method-label { display: inline-flex; align-items: center; gap: 7px; font-weight: 500; }
.swatch { width: 12px; height: 12px; border-radius: 50%; background: var(--run-a); }
.right .swatch { background: var(--run-b); }
.swatch.run-b { background: var(--run-b); }
.panel-meta { color: var(--muted); font-size: 12px; text-align: right; }
.canvas-wrap { position: relative; aspect-ratio: 1 / 1; min-height: 280px; background: var(--field); }
canvas { display: block; width: 100%; height: 100%; }
.hud {
  position: absolute;
  top: 8px;
  left: 8px;
  display: grid;
  grid-template-columns: repeat(2, minmax(100px, auto));
  gap: 4px 10px;
  max-width: calc(100% - 16px);
  padding: 6px 8px;
  border: 1px solid var(--line);
  border-radius: 7px;
  background: color-mix(in srgb, var(--surface) 88%, transparent);
  pointer-events: none;
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}
.hud span:nth-child(odd) { color: var(--muted); }
.legend { display: flex; flex-wrap: wrap; gap: 6px 14px; margin-top: 10px; color: var(--muted); font-size: 12px; }
.legend-item { display: inline-flex; align-items: center; gap: 5px; }
.legend-line { width: 20px; height: 2px; background: var(--wall); }
.legend-line.velocity { background: var(--goal); }
.legend-line.command { background: var(--waypoint); border-top: 1px dashed var(--field); }
.legend-line.contact { background: var(--contact); }
.metrics { display: grid; grid-template-columns: repeat(4,minmax(0,1fr)); gap: 8px; margin-top: 10px; }
.metric { padding: 9px 10px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); min-width: 0; }
.metric-label { color: var(--muted); font-size: 12px; }
.metric-value { margin-top: 2px; font-size: 16px; font-variant-numeric: tabular-nums; overflow-wrap: anywhere; }
.provenance { margin-top: 10px; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }
@media (max-width: 900px) {
  .topline { display: block; }
  .badges { justify-content: flex-start; margin-top: 8px; }
  .arena-grid { grid-template-columns: minmax(0,1fr); }
  .metrics { grid-template-columns: repeat(2,minmax(0,1fr)); }
}
@media (max-width: 520px) {
  .app { padding: 10px; }
  .controls { align-items: stretch; }
  .timeline-group { order: 2; flex-basis: 100%; }
  .metrics { grid-template-columns: minmax(0,1fr); }
  .canvas-wrap { min-height: 260px; }
  .hud { grid-template-columns: minmax(92px,auto) minmax(0,1fr); font-size: 11px; }
  .hud span { min-width: 0; overflow-wrap: anywhere; }
}
@media (prefers-reduced-motion: reduce) {
  * { scroll-behavior: auto !important; }
}
@media (prefers-color-scheme: dark) {
  :root {
    --page: #0a0f16;
    --surface: #111923;
    --surface-2: #182330;
    --field: #0d141d;
    --text: #edf3fb;
    --muted: #9eafc3;
    --line: #2b3a4c;
    --grid: rgba(214,229,246,.08);
    --run-a: #50b6ff;
    --run-b: #ff9d54;
    --contact: #ff5f91;
    --goal: #4ed4ad;
    --waypoint: #ffd166;
    --wall: #b6c6d9;
    --warning-bg: #382b13;
    --warning-text: #ffe2a0;
    --focus: #82c4ff;
  }
}
"""


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="__CSP__">
<title>__TITLE__</title>
<style>__STYLE__</style>
</head>
<body>
<div class="app" id="physicsReplayApp" data-bundle-sha256="__BUNDLE_SHA256__">
  <div class="topline">
    <div>
      <h1>__TITLE__</h1>
      <div class="subtitle" id="sceneReadout">Loading verified offline replay…</div>
    </div>
    <div class="badges" aria-label="Evidence status">
      <span class="badge">OFFLINE PHYSICS REPLAY</span>
      <span class="badge warning">NON-SCIENTIFIC ENGINEERING</span>
      <span class="badge warning">NOT LIVE SOLVER TIMING</span>
      <span class="badge warning">RENDERING EXCLUDED FROM COMPUTE</span>
      <span class="badge warning">MODEL DIAGNOSTIC — NOT PHYSICAL SAFETY</span>
      <span class="badge">VIEWER NEVER LAUNCHES SOLVER</span>
      <span class="badge" id="comparisonBadge">PAIR CHECK LOADING</span>
    </div>
  </div>

  <section class="controls" aria-label="Synchronized replay controls">
    <button id="stepBack" type="button" aria-label="Previous physics sample">◀ Step</button>
    <button id="playPause" type="button" aria-label="Play synchronized replay">▶ Play</button>
    <button id="stepForward" type="button" aria-label="Next physics sample">Step ▶</button>
    <label for="speed" class="sr-only">Playback speed</label>
    <select id="speed" aria-label="Playback speed">
      <option value="0.25">0.25×</option>
      <option value="0.5">0.5×</option>
      <option value="1" selected>1×</option>
      <option value="2">2×</option>
      <option value="4">4×</option>
      <option value="8">8×</option>
    </select>
    <div class="button-group" role="group" aria-label="Replay layout">
      <button id="splitMode" type="button" aria-pressed="true">Split</button>
      <button id="overlayMode" type="button" aria-pressed="false">Overlay</button>
    </div>
    <div class="timeline-group">
      <input id="timeline" type="range" min="0" max="0" step="1" value="0" aria-label="Physics sample">
      <output id="timeReadout" class="readout">0.000 s</output>
    </div>
    <label class="toggle"><input id="showRoutes" type="checkbox" checked> Routes &amp; goals</label>
    <label class="toggle"><input id="showTrails" type="checkbox" checked> Trails</label>
    <label class="toggle"><input id="showVectors" type="checkbox" checked> Motion vectors</label>
    <label class="toggle"><input id="showContacts" type="checkbox" checked> Contacts</label>
    <label class="toggle"><input id="showIds" type="checkbox"> Robot IDs</label>
  </section>

  <section id="arenaGrid" class="arena-grid" data-mode="split" aria-label="Synchronized planar world">
    <article class="panel left">
      <div class="panel-head">
        <span class="method-label"><span class="swatch" aria-hidden="true"></span><span id="leftLabel">Run A</span></span>
        <span class="panel-meta">display label · identity unbound</span>
      </div>
      <div class="canvas-wrap">
        <canvas id="leftCanvas" role="img" aria-label="Run A physics replay"></canvas>
        <div class="hud" id="leftHud"></div>
      </div>
    </article>
    <article class="panel right">
      <div class="panel-head">
        <span class="method-label"><span class="swatch" aria-hidden="true"></span><span id="rightLabel">Run B</span></span>
        <span class="panel-meta">display label · identity unbound</span>
      </div>
      <div class="canvas-wrap">
        <canvas id="rightCanvas" role="img" aria-label="Run B physics replay"></canvas>
        <div class="hud" id="rightHud"></div>
      </div>
    </article>
  </section>

  <div class="legend" aria-label="Replay legend">
    <span class="legend-item"><span class="swatch" aria-hidden="true"></span><span id="leftLegendLabel">Run A</span> robot/trail</span>
    <span class="legend-item"><span class="swatch run-b" aria-hidden="true"></span><span id="rightLegendLabel">Run B</span> robot/trail</span>
    <span class="legend-item"><span class="legend-line velocity" aria-hidden="true"></span>velocity (1 s)</span>
    <span class="legend-item"><span class="legend-line command" aria-hidden="true"></span>held command (1 s)</span>
    <span class="legend-item"><span class="legend-line contact" aria-hidden="true"></span>contact normal</span>
    <span class="legend-item"><span class="legend-line" aria-hidden="true"></span>authenticated static segment</span>
  </div>

  <section class="metrics" aria-label="Replay metrics">
    <div class="metric"><div class="metric-label">Current contacts A / B</div><div class="metric-value" id="contactMetric">—</div></div>
    <div class="metric"><div class="metric-label">Pair clearance A / B</div><div class="metric-value" id="pairMetric">—</div></div>
    <div class="metric"><div class="metric-label">Obstacle clearance A / B</div><div class="metric-value" id="obstacleMetric">—</div></div>
    <div class="metric"><div class="metric-label">Goals observed A / B</div><div class="metric-value" id="goalMetric">—</div></div>
  </section>
  <div class="provenance" id="renderHealth">Exact step and scrub preserve every embedded 10 ms sample. High-speed playback may coalesce displayed frames; embedded data is unchanged.</div>
  <div class="provenance" id="verificationReadout"></div>
  <output id="accessibleFrame" class="sr-only" aria-live="polite"></output>
</div>
<template id="replayPayload">__PAYLOAD_BASE64__</template>
<script>__SCRIPT__</script>
</body>
</html>
"""


SCRIPT_TEXT = r'''"use strict";
const payloadBytes = Uint8Array.from(
  atob(document.getElementById("replayPayload").content.textContent.trim()),
  character => character.charCodeAt(0)
);
const bundle = JSON.parse(new TextDecoder("utf-8", {fatal: true}).decode(payloadBytes));
if (bundle.schema !== "dancers-planar-physics-replay-bundle-v1") {
  throw new Error("Unsupported physics replay bundle");
}
const app = document.getElementById("physicsReplayApp");
const arenaGrid = document.getElementById("arenaGrid");
const leftCanvas = document.getElementById("leftCanvas");
const rightCanvas = document.getElementById("rightCanvas");
const timeline = document.getElementById("timeline");
const playPause = document.getElementById("playPause");
const speed = document.getElementById("speed");
const splitMode = document.getElementById("splitMode");
const overlayMode = document.getElementById("overlayMode");
const showRoutes = document.getElementById("showRoutes");
const showTrails = document.getElementById("showTrails");
const showVectors = document.getElementById("showVectors");
const showContacts = document.getElementById("showContacts");
const showIds = document.getElementById("showIds");
const frameCount = bundle.left.frames.length;
let frameIndex = 0;
let playing = false;
let lastAnimationTime = null;
let playheadMs = 0;
let mode = "split";
let rasterCanvas = null;
let coalescedDisplayFrames = 0;
let leftTrailData = null;
let rightTrailData = null;

function numberValue(value, fallback = null) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && /^[0-9a-f]{16}$/.test(value)) {
    const bytes = new ArrayBuffer(8);
    const view = new DataView(bytes);
    view.setUint32(0, Number.parseInt(value.slice(0, 8), 16), false);
    view.setUint32(4, Number.parseInt(value.slice(8), 16), false);
    const decoded = view.getFloat64(0, false);
    return Number.isFinite(decoded) ? decoded : fallback;
  }
  return fallback;
}

function finite(value, fallback = 0) {
  const decoded = numberValue(value, fallback);
  return typeof decoded === "number" && Number.isFinite(decoded) ? decoded : fallback;
}

function vector(value, fallback = [0, 0]) {
  if (!Array.isArray(value) || value.length < 2) return fallback;
  const decoded = value.map(item => numberValue(item));
  return decoded.every(Number.isFinite) ? decoded : fallback;
}

function firstValue(object, names, fallback = null) {
  for (const name of names) {
    if (object && Object.prototype.hasOwnProperty.call(object, name)) return object[name];
  }
  return fallback;
}

function readBounds() {
  const raw = (bundle.coordinate_system && bundle.coordinate_system.world_bounds_m) || {};
  return {
    minX: finite(raw.x_min, -50),
    minY: finite(raw.y_min, -50),
    maxX: finite(raw.x_max, 50),
    maxY: finite(raw.y_max, 50),
  };
}
const bounds = readBounds();
const worldWidth = bounds.maxX - bounds.minX;
const worldHeight = bounds.maxY - bounds.minY;
if (!(worldWidth > 0 && worldHeight > 0)) throw new Error("Invalid replay world bounds");

function cssColor(name) {
  return getComputedStyle(app).getPropertyValue(name).trim();
}

function fitCanvas(canvas) {
  const rectangle = canvas.getBoundingClientRect();
  const cssWidth = Math.max(260, Math.floor(rectangle.width));
  const cssHeight = Math.max(260, Math.floor(rectangle.height));
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.floor(cssWidth * ratio);
  const height = Math.floor(cssHeight * ratio);
  if (canvas.width !== width || canvas.height !== height) {
    canvas.width = width;
    canvas.height = height;
  }
  const context = canvas.getContext("2d");
  context.setTransform(ratio, 0, 0, ratio, 0, 0);
  return {context, width: cssWidth, height: cssHeight};
}

function worldPoint(point, width, height) {
  const xy = vector(point);
  return [
    (xy[0] - bounds.minX) / worldWidth * width,
    (xy[1] - bounds.minY) / worldHeight * height,
  ];
}

function worldRadius(radiusM, width, height) {
  return radiusM * Math.min(width / worldWidth, height / worldHeight);
}

function decodedSegmentPoint(segment, key) {
  const direct = segment[key];
  const decoded = vector(direct, null);
  if (decoded) return decoded;
  return vector(firstValue(segment, [`${key}_decoded`, `${key}_m`, `${key}_bits`], null));
}

function decodedMissionPoint(value) {
  const direct = vector(value, null);
  if (direct) return direct;
  if (value && Array.isArray(value.xy)) return value.xy;
  return null;
}

function bodyPose(body) {
  const state = firstValue(body, ["r6", "state"], []);
  const decoded = vector(state, null);
  return decoded && decoded.length === 6 ? decoded : [0, 0, 0, 0, 0, 0];
}

function commandVector(command) {
  if (!command) return null;
  return vector(firstValue(command, ["clamped_velocity", "clamped_velocity_command", "adapter_clamped_velocity_command", "clamped_velocity_bits"], null), null);
}

function currentBoundary(side, frame) {
  const index = frame.route_boundary_index;
  return Number.isInteger(index) && side.boundaries[index] ? side.boundaries[index] : null;
}

function boundaryRobots(boundary) {
  if (!boundary) return [];
  if (Array.isArray(boundary.robots)) return boundary.robots;
  if (boundary.mission && Array.isArray(boundary.mission.robots)) return boundary.mission.robots;
  return [];
}

function sceneRadius() {
  const scene = bundle.scene || {};
  if (numberValue(scene.radius_m) !== null) return numberValue(scene.radius_m);
  const settings = scene.settings || {};
  const radius = firstValue(settings, ["radius_m_decoded", "radius_m_value", "radius_m"], 1);
  return finite(radius, 1);
}
const robotRadiusM = sceneRadius();

function prepareRaster() {
  const raster = bundle.scene && bundle.scene.raster;
  if (!raster || !raster.available || !raster.occupied_bitset_base64) return null;
  const width = raster.width_px;
  const height = raster.height_px;
  if (!Number.isInteger(width) || !Number.isInteger(height) || width <= 0 || height <= 0) return null;
  const packed = Uint8Array.from(atob(raster.occupied_bitset_base64), value => value.charCodeAt(0));
  if (packed.length !== Math.ceil(width * height / 8)) throw new Error("Raster bitset length differs");
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext("2d");
  const image = context.createImageData(width, height);
  const wall = cssColor("--wall");
  const probe = document.createElement("canvas").getContext("2d");
  probe.fillStyle = wall;
  const normalized = probe.fillStyle;
  probe.clearRect(0, 0, 1, 1);
  probe.fillStyle = normalized;
  probe.fillRect(0, 0, 1, 1);
  const pixel = probe.getImageData(0, 0, 1, 1).data;
  for (let index = 0; index < width * height; index++) {
    if ((packed[index >> 3] & (1 << (7 - (index & 7)))) === 0) continue;
    const offset = 4 * index;
    image.data[offset] = pixel[0];
    image.data[offset + 1] = pixel[1];
    image.data[offset + 2] = pixel[2];
    image.data[offset + 3] = 105;
  }
  context.putImageData(image, 0, 0);
  return canvas;
}

function drawWorld(context, width, height) {
  context.clearRect(0, 0, width, height);
  context.fillStyle = cssColor("--field");
  context.fillRect(0, 0, width, height);
  context.strokeStyle = cssColor("--grid");
  context.lineWidth = 1;
  const gridStep = 10;
  for (let x = Math.ceil(bounds.minX / gridStep) * gridStep; x <= bounds.maxX; x += gridStep) {
    const [px] = worldPoint([x, 0], width, height);
    context.beginPath(); context.moveTo(px, 0); context.lineTo(px, height); context.stroke();
  }
  for (let y = Math.ceil(bounds.minY / gridStep) * gridStep; y <= bounds.maxY; y += gridStep) {
    const [, py] = worldPoint([0, y], width, height);
    context.beginPath(); context.moveTo(0, py); context.lineTo(width, py); context.stroke();
  }
  if (rasterCanvas) {
    context.save();
    context.imageSmoothingEnabled = false;
    context.drawImage(rasterCanvas, 0, 0, width, height);
    context.restore();
  }
  context.strokeStyle = cssColor("--wall");
  context.lineWidth = Math.max(1, Math.min(width / worldWidth, height / worldHeight) * 0.12);
  for (const segment of bundle.scene.static_segments || []) {
    const a = worldPoint(decodedSegmentPoint(segment, "a"), width, height);
    const b = worldPoint(decodedSegmentPoint(segment, "b"), width, height);
    context.beginPath(); context.moveTo(a[0], a[1]); context.lineTo(b[0], b[1]); context.stroke();
  }
  context.strokeStyle = cssColor("--line");
  context.lineWidth = 1;
  context.strokeRect(.5, .5, width - 1, height - 1);
}

function drawArrow(context, origin, vectorValue, width, height, color, dashed) {
  if (!Array.isArray(vectorValue) || vectorValue.length < 2) return;
  const vx = finite(vectorValue[0]);
  const vy = finite(vectorValue[1]);
  const start = worldPoint(origin, width, height);
  const end = worldPoint([origin[0] + vx, origin[1] + vy], width, height);
  const dx = end[0] - start[0];
  const dy = end[1] - start[1];
  const length = Math.hypot(dx, dy);
  if (length < .6) return;
  context.save();
  context.strokeStyle = color;
  context.fillStyle = color;
  context.lineWidth = 1.4;
  context.setLineDash(dashed ? [4, 3] : []);
  context.beginPath(); context.moveTo(start[0], start[1]); context.lineTo(end[0], end[1]); context.stroke();
  context.setLineDash([]);
  const angle = Math.atan2(dy, dx);
  context.beginPath();
  context.moveTo(end[0], end[1]);
  context.lineTo(end[0] - 6 * Math.cos(angle - .45), end[1] - 6 * Math.sin(angle - .45));
  context.lineTo(end[0] - 6 * Math.cos(angle + .45), end[1] - 6 * Math.sin(angle + .45));
  context.closePath(); context.fill();
  context.restore();
}

function drawRoutesLayer(context, side, frame, width, height, methodColor) {
  if (!showRoutes.checked) return;
  const boundary = currentBoundary(side, frame);
  const states = boundaryRobots(boundary);
  const missions = bundle.scene.missions || [];
  for (const mission of missions) {
    const rid = mission.id;
    const waypoints = mission.waypoints || [];
    if (!waypoints.length) continue;
    const initial = Array.isArray(mission.authenticated_initial_r6)
      ? mission.authenticated_initial_r6.slice(0, 2) : null;
    const route = initial ? [initial, ...waypoints] : waypoints;
    context.save();
    context.strokeStyle = methodColor;
    context.globalAlpha = .34;
    context.lineWidth = 1.25;
    context.setLineDash([4, 4]);
    context.beginPath();
    route.forEach((waypoint, index) => {
      const point = worldPoint(decodedMissionPoint(waypoint), width, height);
      if (index === 0) context.moveTo(point[0], point[1]); else context.lineTo(point[0], point[1]);
    });
    context.stroke();
    context.restore();
    const state = states[rid] || {};
    const next = decodedMissionPoint(firstValue(state, ["next_waypoint", "next_waypoint_m"], null));
    const goal = decodedMissionPoint(firstValue(state, ["final_goal", "final_goal_m"], waypoints[waypoints.length - 1]));
    if (next) drawWaypoint(context, next, width, height, false);
    if (goal) drawWaypoint(context, goal, width, height, true);
  }
}

function drawWaypoint(context, point, width, height, finalGoal) {
  const [x, y] = worldPoint(point, width, height);
  context.save();
  context.strokeStyle = cssColor(finalGoal ? "--goal" : "--waypoint");
  context.lineWidth = 1.8;
  context.beginPath();
  if (finalGoal) {
    context.arc(x, y, 6, 0, Math.PI * 2);
    context.moveTo(x - 8, y); context.lineTo(x + 8, y);
    context.moveTo(x, y - 8); context.lineTo(x, y + 8);
  } else {
    context.moveTo(x, y - 6); context.lineTo(x + 6, y); context.lineTo(x, y + 6); context.lineTo(x - 6, y); context.closePath();
  }
  context.stroke(); context.restore();
}

function prepareTrailData(side) {
  const robots = Math.max(1, bundle.scene.robot_count || side.frames[0].body_state.bodies.length);
  const stride = Math.max(1, Math.ceil(side.frames.length * robots / 40000));
  const tracks = new Map();
  for (let index = 0; index < side.frames.length; index += stride) {
    for (const body of side.frames[index].body_state.bodies) {
      if (!tracks.has(body.id)) tracks.set(body.id, []);
      const state = bodyPose(body);
      tracks.get(body.id).push([index, state[0], state[1]]);
    }
  }
  const lastIndex = side.frames.length - 1;
  if (lastIndex % stride !== 0) {
    for (const body of side.frames[lastIndex].body_state.bodies) {
      const state = bodyPose(body);
      tracks.get(body.id).push([lastIndex, state[0], state[1]]);
    }
  }
  return {stride, tracks};
}

function drawTrailsLayer(context, trailData, index, width, height, color) {
  if (!showTrails.checked) return;
  context.save(); context.strokeStyle = color; context.globalAlpha = .42; context.lineWidth = 1.2;
  for (const samples of trailData.tracks.values()) {
    if (samples.length < 2 || samples[0][0] > index) continue;
    let count = 0;
    context.beginPath();
    for (const sample of samples) {
      if (sample[0] > index) break;
      const point = worldPoint([sample[1], sample[2]], width, height);
      if (count === 0) context.moveTo(point[0], point[1]); else context.lineTo(point[0], point[1]);
      count += 1;
    }
    if (count < 2) continue;
    context.stroke();
  }
  context.restore();
}

function drawContactsLayer(context, frame, width, height) {
  if (!showContacts.checked) return;
  const contacts = frame.contacts.contacts || [];
  for (const contact of contacts) {
    const pointValue = vector(firstValue(contact, ["point", "point_m", "point_decoded", "point_bits"], null), null);
    const normalValue = vector(firstValue(contact, ["normal_applied_to_first", "normal", "normal_decoded", "normal_applied_to_first_bits"], null), null);
    if (!pointValue || !normalValue) continue;
    const point = worldPoint(pointValue, width, height);
    const normalEnd = worldPoint([pointValue[0] + normalValue[0], pointValue[1] + normalValue[1]], width, height);
    context.save();
    context.strokeStyle = cssColor("--contact"); context.fillStyle = cssColor("--contact"); context.lineWidth = 1.7;
    context.beginPath(); context.arc(point[0], point[1], 3.2, 0, Math.PI * 2); context.fill();
    context.beginPath(); context.moveTo(point[0], point[1]); context.lineTo(normalEnd[0], normalEnd[1]); context.stroke();
    context.restore();
  }
}

function drawBodiesLayer(context, side, frame, width, height, color, overlayStyle) {
  const held = Number.isInteger(frame.held_command_tick)
    ? side.command_frames[frame.held_command_tick] : null;
  const commands = held && Array.isArray(held.robots)
    ? new Map(held.robots.map(command => [command.id, command])) : new Map();
  for (const body of frame.body_state.bodies) {
    const state = bodyPose(body);
    const point = worldPoint([state[0], state[1]], width, height);
    const radius = worldRadius(robotRadiusM, width, height);
    context.save();
    context.strokeStyle = color;
    context.fillStyle = color;
    context.lineWidth = overlayStyle ? 2 : 1.5;
    context.globalAlpha = overlayStyle ? .72 : .86;
    context.beginPath(); context.arc(point[0], point[1], radius, 0, Math.PI * 2);
    if (!overlayStyle) context.fill();
    context.stroke();
    context.globalAlpha = 1;
    const headingLength = Math.max(radius, 5);
    context.beginPath(); context.moveTo(point[0], point[1]);
    context.lineTo(point[0] + Math.cos(state[2]) * headingLength, point[1] + Math.sin(state[2]) * headingLength);
    context.stroke();
    if (showIds.checked) {
      context.fillStyle = cssColor("--text");
      context.font = "11px ui-monospace, SFMono-Regular, Menlo, monospace";
      context.textAlign = "center"; context.textBaseline = "bottom";
      context.fillText(String(body.id), point[0], point[1] - radius - 2);
    }
    context.restore();
    if (showVectors.checked) {
      drawArrow(context, [state[0], state[1]], [state[3], state[4]], width, height, cssColor("--goal"), false);
      drawArrow(context, [state[0], state[1]], commandVector(commands.get(body.id)), width, height, cssColor("--waypoint"), true);
    }
  }
}

function goalsObserved(side, frame) {
  const boundary = currentBoundary(side, frame);
  return boundaryRobots(boundary).filter(robot => firstValue(robot, ["observed_goal_controller_tick"], null) !== null).length;
}

function formatDistance(value) {
  const decoded = numberValue(value);
  return decoded !== null ? `${decoded.toFixed(3)} m` : "—";
}

function clearanceValue(frame, kind) {
  const clearance = frame.clearance || {};
  return numberValue(firstValue(clearance, kind === "pair"
    ? ["minimum_pair_clearance_m", "minimum_pair_clearance", "minimum_pair_clearance_decoded", "minimum_pair_clearance_bits"]
    : ["minimum_obstacle_clearance_m", "minimum_obstacle_clearance", "minimum_obstacle_clearance_decoded", "minimum_obstacle_clearance_bits"], null));
}

function contactDiagnostics(frame) {
  let maxImpulse = 0;
  let maxPenetration = 0;
  const contacts = frame.contacts.contacts || [];
  for (const contact of contacts) {
    maxImpulse = Math.max(maxImpulse, finite(firstValue(contact, ["final_normal_impulse_n_s", "final_normal_impulse_bits"], 0), 0));
    maxPenetration = Math.max(maxPenetration, finite(firstValue(contact, ["penetration_before_m", "penetration_before_bits"], 0), 0));
  }
  const visibleKeys = contacts.slice(0, 4).map(contact => `${contact.kind}:${contact.first}/${contact.second}`);
  const keySummary = visibleKeys.length
    ? `${visibleKeys.join(", ")}${contacts.length > visibleKeys.length ? ` +${contacts.length - visibleKeys.length} more` : ""}`
    : "none";
  return {count: contacts.length, maxImpulse, maxPenetration, keySummary};
}

function updateHud(element, side, frame) {
  const contacts = contactDiagnostics(frame);
  const goals = goalsObserved(side, frame);
  element.replaceChildren();
  for (const [label, value] of [
    ["contacts", String(contacts.count)],
    ["max normal impulse", `${contacts.maxImpulse.toFixed(3)} N·s`],
    ["max pre-solve penetration", `${contacts.maxPenetration.toFixed(3)} m`],
    ["contact keys", contacts.keySummary],
    ["goals", `${goals}/${bundle.scene.robot_count}`],
    ["pair clearance", formatDistance(clearanceValue(frame, "pair"))],
    ["obstacle clearance", formatDistance(clearanceValue(frame, "obstacle"))],
  ]) {
    const key = document.createElement("span"); key.textContent = label;
    const item = document.createElement("span"); item.textContent = value;
    element.append(key, item);
  }
}

function drawSide(canvas, hud, side, index, color, overlaySide = null) {
  const fitted = fitCanvas(canvas);
  const {context, width, height} = fitted;
  drawWorld(context, width, height);
  const frame = side.frames[index];
  drawRoutesLayer(context, side, frame, width, height, color);
  drawTrailsLayer(context, side === bundle.left ? leftTrailData : rightTrailData, index, width, height, color);
  drawBodiesLayer(context, side, frame, width, height, color, false);
  drawContactsLayer(context, frame, width, height);
  if (overlaySide) {
    const otherFrame = overlaySide.frames[index];
    drawRoutesLayer(context, overlaySide, otherFrame, width, height, cssColor("--run-b"));
    drawTrailsLayer(context, rightTrailData, index, width, height, cssColor("--run-b"));
    drawBodiesLayer(context, overlaySide, otherFrame, width, height, cssColor("--run-b"), true);
    drawContactsLayer(context, otherFrame, width, height);
  }
  updateHud(hud, side, frame);
}

function updateMetrics(leftFrame, rightFrame) {
  document.getElementById("contactMetric").textContent = `${leftFrame.contacts.contacts.length} / ${rightFrame.contacts.contacts.length}`;
  document.getElementById("pairMetric").textContent = `${formatDistance(clearanceValue(leftFrame, "pair"))} / ${formatDistance(clearanceValue(rightFrame, "pair"))}`;
  document.getElementById("obstacleMetric").textContent = `${formatDistance(clearanceValue(leftFrame, "obstacle"))} / ${formatDistance(clearanceValue(rightFrame, "obstacle"))}`;
  document.getElementById("goalMetric").textContent = `${goalsObserved(bundle.left, leftFrame)} / ${goalsObserved(bundle.right, rightFrame)}`;
}

function framePhase(frame) {
  if (frame.frame_index === 0) return "initial boundary 0";
  if (frame.frame_index % 10 === 0) return `controller boundary ${frame.frame_index / 10}`;
  return `tick ${frame.tick}, substep ${frame.substep} result`;
}

function draw() {
  frameIndex = Math.max(0, Math.min(frameCount - 1, frameIndex));
  const leftFrame = bundle.left.frames[frameIndex];
  const rightFrame = bundle.right.frames[frameIndex];
  timeline.value = String(frameIndex);
  document.getElementById("timeReadout").textContent = `${(leftFrame.time_ms / 1000).toFixed(3)} s · ${frameIndex}/${frameCount - 1} · ${framePhase(leftFrame)}`;
  drawSide(leftCanvas, document.getElementById("leftHud"), bundle.left, frameIndex, cssColor("--run-a"), mode === "overlay" ? bundle.right : null);
  if (mode === "split") drawSide(rightCanvas, document.getElementById("rightHud"), bundle.right, frameIndex, cssColor("--run-b"));
  updateMetrics(leftFrame, rightFrame);
  const leftContact = contactDiagnostics(leftFrame);
  const rightContact = contactDiagnostics(rightFrame);
  document.getElementById("accessibleFrame").textContent = `Physics sample ${frameIndex} of ${frameCount - 1}, ${(leftFrame.time_ms / 1000).toFixed(3)} seconds, ${framePhase(leftFrame)}. ${bundle.left.display_only_label} and ${bundle.right.display_only_label} contacts ${leftContact.count} and ${rightContact.count}; contact keys for ${bundle.left.display_only_label}: ${leftContact.keySummary}; contact keys for ${bundle.right.display_only_label}: ${rightContact.keySummary}; maximum normal impulses ${leftContact.maxImpulse.toFixed(3)} and ${rightContact.maxImpulse.toFixed(3)} newton seconds; maximum pre-solve penetrations ${leftContact.maxPenetration.toFixed(3)} and ${rightContact.maxPenetration.toFixed(3)} metres.`;
}

function setMode(nextMode) {
  mode = nextMode;
  arenaGrid.dataset.mode = mode;
  splitMode.setAttribute("aria-pressed", String(mode === "split"));
  overlayMode.setAttribute("aria-pressed", String(mode === "overlay"));
  leftCanvas.setAttribute("aria-label", mode === "overlay"
    ? `${bundle.left.display_only_label} and ${bundle.right.display_only_label} overlaid physics replay`
    : `${bundle.left.display_only_label} physics replay`);
  draw();
}

function setPlaying(next) {
  playing = next;
  playPause.textContent = playing ? "❚❚ Pause" : "▶ Play";
  playPause.setAttribute("aria-label", playing ? "Pause synchronized replay" : "Play synchronized replay");
  lastAnimationTime = null;
}

function step(delta) {
  setPlaying(false);
  frameIndex = Math.max(0, Math.min(frameCount - 1, frameIndex + delta));
  playheadMs = bundle.left.frames[frameIndex].time_ms;
  draw();
}

function scheduledFrame(timeMs) {
  let low = 0;
  let high = frameCount;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (bundle.left.frames[middle].time_ms <= timeMs) low = middle + 1;
    else high = middle;
  }
  return Math.max(0, low - 1);
}

function animate(timestamp) {
  if (!playing) return;
  if (lastAnimationTime === null) lastAnimationTime = timestamp;
  const elapsed = Math.max(0, timestamp - lastAnimationTime);
  lastAnimationTime = timestamp;
  const finalTime = bundle.left.frames[frameCount - 1].time_ms;
  playheadMs = Math.min(finalTime, playheadMs + elapsed * Number(speed.value));
  const nextFrame = scheduledFrame(playheadMs);
  if (nextFrame !== frameIndex) {
    if (nextFrame > frameIndex + 1) coalescedDisplayFrames += nextFrame - frameIndex - 1;
    frameIndex = nextFrame;
    draw();
    document.getElementById("renderHealth").textContent = `${coalescedDisplayFrames} displayed frame transition(s) coalesced during playback; embedded data is unchanged.${trailPolicy}`;
  }
  if (playheadMs >= finalTime) {
    frameIndex = frameCount - 1; draw(); setPlaying(false); return;
  }
  requestAnimationFrame(animate);
}

timeline.max = String(frameCount - 1);
const robotCount = bundle.scene.robot_count;
showTrails.checked = robotCount <= 128;
showIds.checked = robotCount <= 128;
showTrails.disabled = robotCount > 512;
showIds.disabled = robotCount > 512;
leftTrailData = robotCount > 512 ? {stride: 0, tracks: new Map()} : prepareTrailData(bundle.left);
rightTrailData = robotCount > 512 ? {stride: 0, tracks: new Map()} : prepareTrailData(bundle.right);
document.getElementById("leftLabel").textContent = bundle.left.display_only_label;
document.getElementById("rightLabel").textContent = bundle.right.display_only_label;
document.getElementById("leftLegendLabel").textContent = bundle.left.display_only_label;
document.getElementById("rightLegendLabel").textContent = bundle.right.display_only_label;
leftCanvas.setAttribute("aria-label", `${bundle.left.display_only_label} physics replay`);
rightCanvas.setAttribute("aria-label", `${bundle.right.display_only_label} physics replay`);
const descriptor = bundle.scene.environment_descriptors || {available: false};
const sceneParts = [bundle.scene.family, bundle.scene.map_split].filter(value => typeof value === "string" && value);
sceneParts.push(`${bundle.scene.robot_count} robots`, `${bundle.scene.controller_ticks} controller ticks`, `${(bundle.scene.simulated_time_ms / 1000).toFixed(3)} s`);
if (descriptor.available) {
  const metrics = descriptor.map_metrics || {};
  const box = metrics.boundary_box_counting || {};
  sceneParts.push(Number.isFinite(box.dimension) ? `measured boundary box descriptor D ${box.dimension.toFixed(4)}` : "runtime environment descriptors verified");
} else {
  sceneParts.push("analytic fixture · raster descriptors unavailable");
  const analyticBadge = document.createElement("span");
  analyticBadge.className = "badge warning";
  analyticBadge.textContent = "ANALYTIC FIXTURE — SIDECARS UNAVAILABLE";
  document.querySelector(".badges").append(analyticBadge);
}
document.getElementById("sceneReadout").textContent = sceneParts.join(" · ");
const comparison = bundle.pair_comparison;
const comparisonBadge = document.getElementById("comparisonBadge");
let comparisonText;
if (comparison.equal) {
  comparisonBadge.textContent = "VERIFIED STREAMS EQUAL";
  comparisonText = `Pair comparator found no discordance across ${comparison.events_compared} verified events.`;
} else {
  const mismatch = comparison.first_discordance || {};
  comparisonBadge.textContent = `FIRST DISCORDANCE · ${mismatch.event || "terminal"}`;
  comparisonBadge.classList.add("warning");
  comparisonText = `First verified discordance: ${mismatch.event || "terminal"}, ${mismatch.simulated_time_ms === null || mismatch.simulated_time_ms === undefined ? "endpoint" : `${mismatch.simulated_time_ms} ms`}, ${mismatch.primary_domain || "mission summary"}${mismatch.leaf_path ? ` at ${mismatch.leaf_path}` : ""}.`;
}
const trailStrideDisplay = Math.max(leftTrailData.stride, rightTrailData.stride);
const trailPolicy = robotCount > 512
  ? " Trails and IDs are disabled above 512 robots for display performance; all exact samples remain embedded and step/scrubbable."
  : (trailStrideDisplay > 1
    ? ` Trail display uses every ${trailStrideDisplay}th recorded sample (visible LOD only).`
    : " Trails use every recorded sample.");
document.getElementById("renderHealth").textContent = `Exact step and scrub preserve every embedded 10 ms sample. High-speed playback may coalesce displayed frames; embedded data is unchanged.${trailPolicy}`;
document.getElementById("verificationReadout").textContent = `Both inputs passed full replay-leaf and formula verification. ${comparisonText} Common scene ${bundle.scene.common_scene_semantic_sha256}. Display labels are not authenticated method identities. Bundle SHA-256 ${app.dataset.bundleSha256}. Rendering, playback, and this interface are excluded from solver timing and scientific evidence.`;
rasterCanvas = prepareRaster();

playPause.addEventListener("click", () => {
  if (!playing && frameIndex === frameCount - 1) { frameIndex = 0; playheadMs = 0; }
  setPlaying(!playing); draw(); if (playing) requestAnimationFrame(animate);
});
document.getElementById("stepBack").addEventListener("click", () => step(-1));
document.getElementById("stepForward").addEventListener("click", () => step(1));
timeline.addEventListener("input", () => { setPlaying(false); frameIndex = Number(timeline.value); playheadMs = bundle.left.frames[frameIndex].time_ms; draw(); });
splitMode.addEventListener("click", () => setMode("split"));
overlayMode.addEventListener("click", () => setMode("overlay"));
for (const control of [showRoutes, showTrails, showVectors, showContacts, showIds]) control.addEventListener("change", draw);
window.addEventListener("resize", draw);
window.addEventListener("keydown", event => {
  if (event.target.matches("input,select,button")) return;
  if (event.code === "Space") { event.preventDefault(); playPause.click(); }
  if (event.code === "ArrowLeft") { event.preventDefault(); step(-1); }
  if (event.code === "ArrowRight") { event.preventDefault(); step(1); }
});
const colorScheme = window.matchMedia("(prefers-color-scheme: dark)");
if (typeof colorScheme.addEventListener === "function") colorScheme.addEventListener("change", () => { rasterCanvas = prepareRaster(); draw(); });
draw();
'''


if __name__ == "__main__":
    raise SystemExit(main())
