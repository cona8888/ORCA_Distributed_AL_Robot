#!/usr/bin/env python3
"""Cross-language authenticated runtime-input regressions (temporary files only)."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any

REPOSITORY_IMPORT_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_IMPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_IMPORT_ROOT))

from scripts.comparison import build_physics_colliders_v1 as COLLIDERS
from scripts.comparison import build_physics_missions_v1 as MISSIONS


def canonical(value: Any) -> bytes:
    return COLLIDERS.canonical_json_bytes(value)


def identity(path: str, data: bytes) -> dict[str, Any]:
    return {
        "bytes": len(data),
        "path": path,
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def write(root: Path, relative: str, data: bytes) -> None:
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def tree_snapshot(root: Path) -> dict[str, tuple[Any, ...]]:
    snapshot: dict[str, tuple[Any, ...]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        if path.is_symlink():
            snapshot[relative] = (
                "symlink",
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
                os.readlink(path),
            )
        elif path.is_dir():
            snapshot[relative] = (
                "directory",
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_mode,
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
            )
        elif path.is_file():
            data = path.read_bytes()
            snapshot[relative] = (
                "file",
                metadata.st_mode,
                metadata.st_dev,
                metadata.st_ino,
                len(data),
                metadata.st_mtime_ns,
                metadata.st_ctime_ns,
                hashlib.sha256(data).hexdigest(),
            )
        else:
            snapshot[relative] = ("special", metadata.st_mode)
    return snapshot


class Fixture:
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root
        self.collider_bundle = COLLIDERS.build_registered_colliders(repository_root)
        self.collider_manifest = COLLIDERS.build_collider_manifest(
            repository_root, self.collider_bundle
        )
        bundles = []
        for family in COLLIDERS.FAMILIES:
            for phase, seed in (("calibration", 1), ("validation", 11)):
                bundles.append(
                    MISSIONS.build_mission_bank(
                        repository_root,
                        phase=phase,
                        family=family,
                        seed=seed,
                        counts=(1,),
                        collider_bundle=self.collider_bundle,
                    )
                )
        bundles.append(
            MISSIONS.build_mission_bank(
                repository_root,
                phase="audit",
                family="open",
                seed=31,
                counts=(1,),
                collider_bundle=self.collider_bundle,
            )
        )
        bundles.append(
            MISSIONS.build_mission_bank(
                repository_root,
                phase="calibration",
                family="open",
                seed=2,
                counts=(1,),
                collider_bundle=self.collider_bundle,
            )
        )
        self.mission_manifest = MISSIONS.build_mission_manifest(
            repository_root,
            reversed(bundles),
            self.collider_bundle,
            self.collider_manifest,
        )
        self.bundles = {
            (bundle.phase, bundle.family, bundle.seed): bundle
            for bundle in bundles
        }
        self.missions_by_path = {
            artifact.artifact_path.as_posix(): artifact
            for bundle in bundles
            for artifact in bundle.artifacts
            if artifact.artifact_path is not None
        }
        self.colliders_by_path = {
            registered.artifact_path.as_posix(): registered
            for registered in self.collider_bundle.artifacts
        }
        self.selected_bundle = self.bundles[("calibration", "open", 1)]
        self.selected_collider = self.collider_bundle.by_identity()[
            ("open", "calibration")
        ]
        self.selected_mission = self.selected_bundle.by_count()[1]

    def runtime(
        self,
        run_class: str,
        *,
        phase: str = "calibration",
        family: str = "open",
        seed: int = 1,
    ) -> dict[str, Any]:
        return copy.deepcopy(
            MISSIONS.build_runtime_input(
                repo_root=self.repository_root,
                collider_manifest=self.collider_manifest,
                mission_manifest=self.mission_manifest,
                phase=phase,
                family=family,
                seed=seed,
                robot_count=1,
                run_class=run_class,
            ).run_input
        )

    def materialize(
        self,
        root: Path,
        run: dict[str, Any],
        *,
        collider_manifest: dict[str, Any] | None = None,
        mission_manifest: dict[str, Any] | None = None,
        collider_bytes: bytes | None = None,
        mission_bytes: bytes | None = None,
        occupancy_bytes: bytes | None = None,
        run_path: str = "inputs/run.json",
    ) -> tuple[str, bytes]:
        collider_manifest = copy.deepcopy(
            collider_manifest or self.collider_manifest.manifest
        )
        mission_manifest = copy.deepcopy(
            mission_manifest or self.mission_manifest.manifest
        )
        selected_collider = self.colliders_by_path[
            run["artifacts"]["collider"]["path"]
        ]
        selected_mission = self.missions_by_path[
            run["artifacts"]["mission"]["path"]
        ]
        collider_bytes = collider_bytes or selected_collider.collider.artifact_bytes
        mission_bytes = mission_bytes or selected_mission.mission_bytes
        occupancy_bytes = occupancy_bytes or selected_collider.collider.occupancy_bitset

        registry = (self.repository_root / COLLIDERS.PHYSICS_REGISTRY_PATH).read_bytes()
        environment = (
            self.repository_root / COLLIDERS.ENVIRONMENT_MANIFEST_PATH
        ).read_bytes()
        map_bytes = (self.repository_root / selected_collider.source_path).read_bytes()
        collider_manifest_bytes = canonical(collider_manifest)
        mission_manifest_bytes = canonical(mission_manifest)
        run = copy.deepcopy(run)
        run["artifacts"]["physics_registry"] = identity(
            COLLIDERS.PHYSICS_REGISTRY_PATH.as_posix(), registry
        )
        run["artifacts"]["environment_manifest"] = identity(
            COLLIDERS.ENVIRONMENT_MANIFEST_PATH.as_posix(), environment
        )
        run["artifacts"]["collider_manifest"] = identity(
            COLLIDERS.COLLIDER_MANIFEST_PATH.as_posix(), collider_manifest_bytes
        )
        run["artifacts"]["mission_manifest"] = identity(
            MISSIONS.MISSION_MANIFEST_PATH.as_posix(), mission_manifest_bytes
        )

        write(root, COLLIDERS.PHYSICS_REGISTRY_PATH.as_posix(), registry)
        write(root, COLLIDERS.ENVIRONMENT_MANIFEST_PATH.as_posix(), environment)
        write(root, selected_collider.source_path.as_posix(), map_bytes)
        write(root, COLLIDERS.COLLIDER_MANIFEST_PATH.as_posix(), collider_manifest_bytes)
        write(root, MISSIONS.MISSION_MANIFEST_PATH.as_posix(), mission_manifest_bytes)
        write(root, run["artifacts"]["collider"]["path"], collider_bytes)
        write(root, run["artifacts"]["occupancy_bitset"]["path"], occupancy_bytes)
        write(root, run["artifacts"]["mission"]["path"], mission_bytes)
        run_bytes = canonical(run)
        write(root, run_path, run_bytes)
        return run_path, run_bytes


def invoke(
    loader: Path,
    root: Path,
    run_path: str,
    run_bytes: bytes,
    run_class: str,
    *,
    byte_count: int | None = None,
    sha256: str | None = None,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(loader),
            "--load",
            str(root),
            run_path,
            str(len(run_bytes) if byte_count is None else byte_count),
            hashlib.sha256(run_bytes).hexdigest() if sha256 is None else sha256,
            run_class,
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )


def require_failed(completed: subprocess.CompletedProcess[str], label: str) -> None:
    if completed.returncode == 0:
        raise AssertionError(f"{label} unexpectedly passed: {completed.stdout}")
    if completed.returncode < 0:
        raise AssertionError(f"{label} crashed with signal {-completed.returncode}")
    if "physics input authentication failed:" not in completed.stderr:
        raise AssertionError(
            f"{label} did not use the normalized error surface: {completed.stderr}"
        )


def rebind_selected(
    run: dict[str, Any],
    collider_manifest: dict[str, Any],
    mission_manifest: dict[str, Any],
    collider_bytes: bytes,
    mission_bytes: bytes,
) -> None:
    collider_row = next(
        row
        for row in collider_manifest["artifacts"]
        if (row["family"], row["split"]) == ("open", "calibration")
    )
    mission_row = next(
        row
        for row in mission_manifest["artifacts"]
        if (row["phase"], row["family"], row["seed"], row["robot_count"])
        == ("calibration", "open", 1, 1)
    )
    collider_row["artifact"] = identity(
        collider_row["artifact"]["path"], collider_bytes
    )
    mission_row["artifact"] = identity(mission_row["artifact"]["path"], mission_bytes)
    run["artifacts"]["collider"] = copy.deepcopy(collider_row["artifact"])
    run["artifacts"]["mission"] = copy.deepcopy(mission_row["artifact"])
    run["mission_semantic_sha256"] = mission_row["mission_semantic_sha256"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--loader", required=True, type=Path)
    parser.add_argument("--repository-root", required=True, type=Path)
    arguments = parser.parse_args()
    fixture = Fixture(arguments.repository_root.resolve())
    temporary_root = Path(tempfile.gettempdir()).resolve()
    temporary = Path(
        tempfile.mkdtemp(prefix="dancers-loader-", dir=temporary_root)
    ).resolve()
    try:
        fixed = fixture.runtime(MISSIONS.FIXED_WORK_RUN_CLASS)
        run_path, run_bytes = fixture.materialize(temporary, fixed)
        before_load = tree_snapshot(temporary)
        completed = invoke(
            arguments.loader,
            temporary,
            run_path,
            run_bytes,
            MISSIONS.FIXED_WORK_RUN_CLASS,
        )
        if completed.returncode != 0:
            raise AssertionError(f"valid fixed-work input failed: {completed.stderr}")
        if tree_snapshot(temporary) != before_load:
            raise AssertionError("loader changed the input tree")
        receipt = json.loads(completed.stdout)
        if receipt["controller_ticks"] != 100 or receipt["physics_steps"] != 1000:
            raise AssertionError("fixed-work execution was not exactly 100/1000")

        route = fixture.runtime(MISSIONS.ROUTE_MISSION_RUN_CLASS)
        route_path, route_bytes = fixture.materialize(
            temporary, route, run_path="inputs/route.json"
        )
        completed = invoke(
            arguments.loader,
            temporary,
            route_path,
            route_bytes,
            MISSIONS.ROUTE_MISSION_RUN_CLASS,
        )
        if completed.returncode != 0:
            raise AssertionError(f"valid route input failed: {completed.stderr}")
        route_receipt = json.loads(completed.stdout)
        horizon = fixture.selected_mission.mission["route_horizon"]
        if (
            route_receipt["controller_ticks"]
            != horizon["mission_controller_ticks"]
            or route_receipt["physics_steps"] != horizon["physics_steps"]
        ):
            raise AssertionError("route execution differs from mission horizon")

        for family in COLLIDERS.FAMILIES:
            for phase, seed, expected_split in (
                ("calibration", 1, "calibration"),
                ("validation", 11, "validation"),
            ):
                positive = fixture.runtime(
                    MISSIONS.FIXED_WORK_RUN_CLASS,
                    phase=phase,
                    family=family,
                    seed=seed,
                )
                positive_path, positive_bytes = fixture.materialize(
                    temporary,
                    positive,
                    run_path=f"inputs/positive-{phase}-{family}.json",
                )
                completed = invoke(
                    arguments.loader,
                    temporary,
                    positive_path,
                    positive_bytes,
                    MISSIONS.FIXED_WORK_RUN_CLASS,
                )
                if completed.returncode != 0:
                    raise AssertionError(
                        f"valid {phase}/{family} input failed: {completed.stderr}"
                    )
                positive_receipt = json.loads(completed.stdout)
                if (
                    positive_receipt["phase"] != phase
                    or positive_receipt["family"] != family
                    or positive_receipt["map_split"] != expected_split
                ):
                    raise AssertionError("positive family/split selection differs")

        audit = fixture.runtime(
            MISSIONS.FIXED_WORK_RUN_CLASS,
            phase="audit",
            family="open",
            seed=31,
        )
        audit_path, audit_bytes = fixture.materialize(
            temporary, audit, run_path="inputs/positive-audit-open.json"
        )
        completed = invoke(
            arguments.loader,
            temporary,
            audit_path,
            audit_bytes,
            MISSIONS.FIXED_WORK_RUN_CLASS,
        )
        if completed.returncode != 0:
            raise AssertionError(f"valid audit input failed: {completed.stderr}")
        audit_receipt = json.loads(completed.stdout)
        if (
            audit_receipt["phase"] != "audit"
            or audit_receipt["map_split"] != "validation"
        ):
            raise AssertionError("audit did not bind the validation map split")

        require_failed(
            invoke(
                arguments.loader,
                temporary,
                run_path,
                run_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
                byte_count=len(run_bytes) + 1,
            ),
            "wrong external run byte count",
        )
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                run_path,
                run_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
                sha256="0" * 64,
            ),
            "wrong external run hash",
        )

        duplicate_bytes = (b'{"seed":1,' + run_bytes[1:])
        write(temporary, "inputs/duplicate.json", duplicate_bytes)
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                "inputs/duplicate.json",
                duplicate_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
            ),
            "duplicate run key",
        )
        pretty_bytes = (json.dumps(fixed, indent=2) + "\n").encode()
        write(temporary, "inputs/pretty.json", pretty_bytes)
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                "inputs/pretty.json",
                pretty_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
            ),
            "noncanonical run JSON",
        )
        nested_duplicate = run_bytes.replace(
            b'"execution":{"controller_ticks":100,',
            b'"execution":{"controller_ticks":100,"controller_ticks":100,',
            1,
        )
        write(temporary, "inputs/nested-duplicate.json", nested_duplicate)
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                "inputs/nested-duplicate.json",
                nested_duplicate,
                MISSIONS.FIXED_WORK_RUN_CLASS,
            ),
            "nested duplicate run key",
        )
        for label, malformed in (
            ("BOM", b"\xef\xbb\xbf" + run_bytes),
            ("CRLF", run_bytes[:-1] + b"\r\n"),
        ):
            malformed_path = f"inputs/{label.lower()}.json"
            write(temporary, malformed_path, malformed)
            require_failed(
                invoke(
                    arguments.loader,
                    temporary,
                    malformed_path,
                    malformed,
                    MISSIONS.FIXED_WORK_RUN_CLASS,
                ),
                f"{label} run JSON",
            )
        too_deep = (b'{"a":' + b"[" * 129 + b"0" + b"]" * 129 + b"}\n")
        write(temporary, "inputs/too-deep.json", too_deep)
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                "inputs/too-deep.json",
                too_deep,
                MISSIONS.FIXED_WORK_RUN_CLASS,
            ),
            "over-depth run JSON",
        )
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                run_path,
                run_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
                byte_count=(1 << 20) + 1,
            ),
            "over-cap run identity",
        )
        over_manifest_run = copy.deepcopy(fixed)
        over_manifest_run["artifacts"]["collider_manifest"]["bytes"] = (
            (16 << 20) + 1
        )
        over_manifest_bytes = canonical(over_manifest_run)
        write(temporary, "inputs/over-cap-manifest.json", over_manifest_bytes)
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                "inputs/over-cap-manifest.json",
                over_manifest_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
            ),
            "over-cap collider manifest identity",
        )

        over_mission_run = fixture.runtime(MISSIONS.FIXED_WORK_RUN_CLASS)
        over_mission_manifest = copy.deepcopy(fixture.mission_manifest.manifest)
        over_mission_row = next(
            row
            for row in over_mission_manifest["artifacts"]
            if (row["phase"], row["family"], row["seed"], row["robot_count"])
            == ("calibration", "open", 1, 1)
        )
        over_mission_row["artifact"]["bytes"] = (8 << 20) + 1
        over_mission_row["artifact"]["sha256"] = "0" * 64
        over_mission_run["artifacts"]["mission"] = copy.deepcopy(
            over_mission_row["artifact"]
        )
        over_mission_path, over_mission_bytes = fixture.materialize(
            temporary,
            over_mission_run,
            mission_manifest=over_mission_manifest,
            run_path="inputs/over-cap-mission.json",
        )
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                over_mission_path,
                over_mission_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
            ),
            "over-cap selected mission identity",
        )

        duplicate_path_run = copy.deepcopy(fixed)
        duplicate_path_run["artifacts"]["environment_manifest"]["path"] = (
            duplicate_path_run["artifacts"]["physics_registry"]["path"]
        )
        duplicate_path_bytes = canonical(duplicate_path_run)
        write(temporary, "inputs/duplicate-artifact-path.json", duplicate_path_bytes)
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                "inputs/duplicate-artifact-path.json",
                duplicate_path_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
            ),
            "duplicate run artifact path",
        )
        for field, wrong_path in (
            ("collider_manifest", "wrong/collider-manifest.json"),
            ("mission_manifest", "wrong/mission-manifest.json"),
        ):
            wrong_manifest_run = copy.deepcopy(fixed)
            wrong_manifest_run["artifacts"][field]["path"] = wrong_path
            wrong_manifest_bytes = canonical(wrong_manifest_run)
            path = f"inputs/wrong-{field}-path.json"
            write(temporary, path, wrong_manifest_bytes)
            require_failed(
                invoke(
                    arguments.loader,
                    temporary,
                    path,
                    wrong_manifest_bytes,
                    MISSIONS.FIXED_WORK_RUN_CLASS,
                ),
                f"wrong canonical {field} path",
            )
        direct_mismatch = copy.deepcopy(fixed)
        direct_mismatch["artifacts"]["map"]["sha256"] = "0" * 64
        direct_mismatch_bytes = canonical(direct_mismatch)
        write(temporary, "inputs/direct-mismatch.json", direct_mismatch_bytes)
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                "inputs/direct-mismatch.json",
                direct_mismatch_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
            ),
            "direct selected identity mismatch",
        )

        packed_path = temporary / fixed["artifacts"]["occupancy_bitset"]["path"]
        original_packed = packed_path.read_bytes()
        changed_packed = bytearray(original_packed)
        changed_packed[0] ^= 0x80
        packed_path.write_bytes(changed_packed)
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                run_path,
                run_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
            ),
            "tampered occupancy sidecar",
        )
        packed_path.write_bytes(original_packed)

        horizon_run = fixture.runtime(MISSIONS.ROUTE_MISSION_RUN_CLASS)
        horizon_mission = copy.deepcopy(fixture.selected_mission.mission)
        horizon_mission["route_horizon"]["physics_steps"] += 10
        horizon_mission_bytes = canonical(horizon_mission)
        horizon_manifest = copy.deepcopy(fixture.mission_manifest.manifest)
        horizon_row = horizon_manifest["artifacts"][0]
        horizon_row["artifact"] = identity(
            horizon_row["artifact"]["path"], horizon_mission_bytes
        )
        horizon_row["route_horizon"] = copy.deepcopy(
            horizon_mission["route_horizon"]
        )
        horizon_run["artifacts"]["mission"] = copy.deepcopy(horizon_row["artifact"])
        horizon_path, horizon_bytes = fixture.materialize(
            temporary,
            horizon_run,
            mission_manifest=horizon_manifest,
            mission_bytes=horizon_mission_bytes,
            run_path="inputs/bad-horizon.json",
        )
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                horizon_path,
                horizon_bytes,
                MISSIONS.ROUTE_MISSION_RUN_CLASS,
            ),
            "self-rehashed forged horizon",
        )

        seed_run = fixture.runtime(MISSIONS.FIXED_WORK_RUN_CLASS)
        seed_mission = copy.deepcopy(fixture.selected_mission.mission)
        seed_mission["generator_seed_uint64"] += 1
        seed_mission_bytes = canonical(seed_mission)
        seed_manifest = copy.deepcopy(fixture.mission_manifest.manifest)
        seed_row = next(
            row
            for row in seed_manifest["artifacts"]
            if (row["phase"], row["family"], row["seed"], row["robot_count"])
            == ("calibration", "open", 1, 1)
        )
        seed_row["artifact"] = identity(
            seed_row["artifact"]["path"], seed_mission_bytes
        )
        seed_run["artifacts"]["mission"] = copy.deepcopy(seed_row["artifact"])
        seed_path, seed_bytes = fixture.materialize(
            temporary,
            seed_run,
            mission_manifest=seed_manifest,
            mission_bytes=seed_mission_bytes,
            run_path="inputs/bad-generator-seed.json",
        )
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                seed_path,
                seed_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
            ),
            "self-rehashed forged generator seed",
        )

        semantic_run = fixture.runtime(MISSIONS.FIXED_WORK_RUN_CLASS)
        semantic_mission = copy.deepcopy(fixture.selected_mission.mission)
        semantic_mission["mission_semantic_sha256"] = "0" * 64
        semantic_mission_bytes = canonical(semantic_mission)
        semantic_manifest = copy.deepcopy(fixture.mission_manifest.manifest)
        semantic_row = next(
            row
            for row in semantic_manifest["artifacts"]
            if (row["phase"], row["family"], row["seed"], row["robot_count"])
            == ("calibration", "open", 1, 1)
        )
        semantic_row["artifact"] = identity(
            semantic_row["artifact"]["path"], semantic_mission_bytes
        )
        semantic_row["mission_semantic_sha256"] = "0" * 64
        semantic_run["artifacts"]["mission"] = copy.deepcopy(
            semantic_row["artifact"]
        )
        semantic_run["mission_semantic_sha256"] = "0" * 64
        semantic_path, semantic_bytes = fixture.materialize(
            temporary,
            semantic_run,
            mission_manifest=semantic_manifest,
            mission_bytes=semantic_mission_bytes,
            run_path="inputs/bad-semantic.json",
        )
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                semantic_path,
                semantic_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
            ),
            "self-rehashed forged mission semantic",
        )

        geometry_run = fixture.runtime(MISSIONS.FIXED_WORK_RUN_CLASS)
        geometry_collider = copy.deepcopy(fixture.selected_collider.collider.artifact)
        first_segment = geometry_collider["segments"][0]
        if first_segment["axis"] == "horizontal":
            first_segment["second_endpoint_mm"][0] -= 100
        else:
            first_segment["second_endpoint_mm"][1] -= 100
        geometry_payload = dict(geometry_collider)
        del geometry_payload["ordered_segment_payload_sha256"]
        geometry_collider["ordered_segment_payload_sha256"] = hashlib.sha256(
            canonical(geometry_payload)
        ).hexdigest()
        geometry_collider_bytes = canonical(geometry_collider)
        geometry_collider_sha = hashlib.sha256(geometry_collider_bytes).hexdigest()
        geometry_manifest = copy.deepcopy(fixture.collider_manifest.manifest)
        geometry_collider_row = next(
            row
            for row in geometry_manifest["artifacts"]
            if (row["family"], row["split"]) == ("open", "calibration")
        )
        geometry_collider_row["artifact"] = identity(
            geometry_collider_row["artifact"]["path"], geometry_collider_bytes
        )
        geometry_collider_row["ordered_segment_payload_sha256"] = (
            geometry_collider["ordered_segment_payload_sha256"]
        )
        geometry_manifest_bytes = canonical(geometry_manifest)
        geometry_mission = copy.deepcopy(fixture.selected_mission.mission)
        geometry_mission["collider_sha256"] = geometry_collider_sha
        geometry_mission["mission_semantic_sha256"] = (
            MISSIONS.mission_semantic_sha256(geometry_mission)
        )
        geometry_mission_bytes = canonical(geometry_mission)
        geometry_mission_manifest = copy.deepcopy(fixture.mission_manifest.manifest)
        geometry_mission_manifest["collider_manifest_sha256"] = hashlib.sha256(
            geometry_manifest_bytes
        ).hexdigest()
        for row in geometry_mission_manifest["artifacts"]:
            if (row["family"], row["map_split"]) == ("open", "calibration"):
                row["collider_sha256"] = geometry_collider_sha
            if (
                row["phase"], row["family"], row["seed"], row["robot_count"]
            ) == ("calibration", "open", 1, 1):
                row["artifact"] = identity(row["artifact"]["path"], geometry_mission_bytes)
                row["mission_semantic_sha256"] = geometry_mission[
                    "mission_semantic_sha256"
                ]
        geometry_run["artifacts"]["collider"] = copy.deepcopy(
            geometry_collider_row["artifact"]
        )
        selected_geometry_mission_row = next(
            row
            for row in geometry_mission_manifest["artifacts"]
            if (
                row["phase"], row["family"], row["seed"], row["robot_count"]
            ) == ("calibration", "open", 1, 1)
        )
        geometry_run["artifacts"]["mission"] = copy.deepcopy(
            selected_geometry_mission_row["artifact"]
        )
        geometry_run["mission_semantic_sha256"] = geometry_mission[
            "mission_semantic_sha256"
        ]
        geometry_path, geometry_bytes = fixture.materialize(
            temporary,
            geometry_run,
            collider_manifest=geometry_manifest,
            mission_manifest=geometry_mission_manifest,
            collider_bytes=geometry_collider_bytes,
            mission_bytes=geometry_mission_bytes,
            run_path="inputs/bad-collider-geometry.json",
        )
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                geometry_path,
                geometry_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
            ),
            "self-rehashed collider geometry detached from sidecar",
        )

        detached_missions = copy.deepcopy(fixture.mission_manifest.manifest)
        detached_missions["artifacts"][1]["collider_sha256"] = "0" * 64
        detached_run = fixture.runtime(MISSIONS.FIXED_WORK_RUN_CLASS)
        detached_path, detached_bytes = fixture.materialize(
            temporary,
            detached_run,
            mission_manifest=detached_missions,
            run_path="inputs/detached-nonselected-mission.json",
        )
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                detached_path,
                detached_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
            ),
            "detached nonselected mission row",
        )

        detached_colliders = copy.deepcopy(fixture.collider_manifest.manifest)
        detached_colliders["artifacts"][-1]["effective_occupancy_sha256"] = "0" * 64
        detached_collider_bytes = canonical(detached_colliders)
        detached_collider_missions = copy.deepcopy(fixture.mission_manifest.manifest)
        detached_collider_missions["collider_manifest_sha256"] = hashlib.sha256(
            detached_collider_bytes
        ).hexdigest()
        detached_collider_run = fixture.runtime(MISSIONS.FIXED_WORK_RUN_CLASS)
        detached_collider_path, detached_collider_run_bytes = fixture.materialize(
            temporary,
            detached_collider_run,
            collider_manifest=detached_colliders,
            mission_manifest=detached_collider_missions,
            run_path="inputs/detached-nonselected-collider.json",
        )
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                detached_collider_path,
                detached_collider_run_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
            ),
            "detached nonselected collider row",
        )

        shuffled_colliders = copy.deepcopy(fixture.collider_manifest.manifest)
        shuffled_colliders["artifacts"][0], shuffled_colliders["artifacts"][1] = (
            shuffled_colliders["artifacts"][1],
            shuffled_colliders["artifacts"][0],
        )
        shuffled_collider_manifest_bytes = canonical(shuffled_colliders)
        shuffled_collider_missions = copy.deepcopy(fixture.mission_manifest.manifest)
        shuffled_collider_missions["collider_manifest_sha256"] = hashlib.sha256(
            shuffled_collider_manifest_bytes
        ).hexdigest()
        shuffled_collider_run = fixture.runtime(MISSIONS.FIXED_WORK_RUN_CLASS)
        shuffled_collider_path, shuffled_collider_bytes = fixture.materialize(
            temporary,
            shuffled_collider_run,
            collider_manifest=shuffled_colliders,
            mission_manifest=shuffled_collider_missions,
            run_path="inputs/shuffled-collider-manifest.json",
        )
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                shuffled_collider_path,
                shuffled_collider_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
            ),
            "shuffled collider manifest",
        )

        shuffled_missions = copy.deepcopy(fixture.mission_manifest.manifest)
        shuffled_missions["artifacts"][0], shuffled_missions["artifacts"][1] = (
            shuffled_missions["artifacts"][1],
            shuffled_missions["artifacts"][0],
        )
        shuffled_mission_run = fixture.runtime(MISSIONS.FIXED_WORK_RUN_CLASS)
        shuffled_mission_path, shuffled_mission_bytes = fixture.materialize(
            temporary,
            shuffled_mission_run,
            mission_manifest=shuffled_missions,
            run_path="inputs/shuffled-mission-manifest.json",
        )
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                shuffled_mission_path,
                shuffled_mission_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
            ),
            "shuffled mission manifest",
        )

        symlink_target = temporary / "real-run.json"
        symlink_target.write_bytes(run_bytes)
        symlink_path = temporary / "inputs/symlink-run.json"
        symlink_path.unlink(missing_ok=True)
        symlink_path.symlink_to(symlink_target)
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                "inputs/symlink-run.json",
                run_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
            ),
            "symlink run input",
        )
        parent_alias = temporary / "linked-parent"
        parent_alias.unlink(missing_ok=True)
        parent_alias.symlink_to(temporary / "inputs", target_is_directory=True)
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                "linked-parent/run.json",
                run_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
            ),
            "symlink input parent",
        )
        directory_input = temporary / "inputs/directory-run.json"
        directory_input.mkdir(exist_ok=True)
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                "inputs/directory-run.json",
                run_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
            ),
            "directory run input",
        )

        fifo_path = temporary / "inputs/fifo-run.json"
        fifo_path.unlink(missing_ok=True)
        os.mkfifo(fifo_path)
        require_failed(
            invoke(
                arguments.loader,
                temporary,
                "inputs/fifo-run.json",
                run_bytes,
                MISSIONS.FIXED_WORK_RUN_CLASS,
                timeout=5.0,
            ),
            "FIFO run input",
        )

        alias = temporary.parent / f"{temporary.name}-alias"
        alias.unlink(missing_ok=True)
        alias.symlink_to(temporary, target_is_directory=True)
        try:
            require_failed(
                invoke(
                    arguments.loader,
                    alias,
                    run_path,
                    run_bytes,
                    MISSIONS.FIXED_WORK_RUN_CLASS,
                ),
                "symlink repository root",
            )
        finally:
            alias.unlink(missing_ok=True)

        print("physics artifact loader interop regressions passed")
        return 0
    finally:
        shutil.rmtree(temporary)


if __name__ == "__main__":
    raise SystemExit(main())
