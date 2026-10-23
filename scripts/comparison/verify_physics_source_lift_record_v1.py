#!/usr/bin/env python3
"""Verify the frozen, nonauthorizing post-port physics source-lift record."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import types
from typing import Any, Sequence


TRUSTED_FEASIBILITY_VERIFIER_BYTES = 43773
TRUSTED_FEASIBILITY_VERIFIER_SHA256 = "4ee06d153a4cad245e0ce227ca8ab384ba8137e347ad1e9b73a3fd5808955be2"


def _load_trusted_feasibility_module(candidate_path: Path | None = None) -> types.ModuleType:
    """Execute only the independently pinned predecessor-verifier bytes."""

    path = candidate_path or Path(__file__).with_name("verify_physics_source_lift_v1.py")
    canonical = Path(os.path.abspath(path))
    if os.path.realpath(canonical) != os.fspath(canonical):
        raise RuntimeError("trusted predecessor verifier has a symlink ancestor")
    fd = os.open(canonical, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size != TRUSTED_FEASIBILITY_VERIFIER_BYTES):
            raise RuntimeError("trusted predecessor verifier type or size differs")
        raw = os.read(fd, TRUSTED_FEASIBILITY_VERIFIER_BYTES + 1)
        after = os.fstat(fd)
        if (len(raw) != before.st_size or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise RuntimeError("trusted predecessor verifier changed while read")
    finally:
        os.close(fd)
    if hashlib.sha256(raw).hexdigest() != TRUSTED_FEASIBILITY_VERIFIER_SHA256:
        raise RuntimeError("trusted predecessor verifier SHA-256 differs")
    module_name = "dancers_trusted_verify_physics_source_lift_v1"
    module = types.ModuleType(module_name)
    module.__file__ = os.fspath(canonical)
    module.__package__ = ""
    sys.modules[module_name] = module
    exec(compile(raw, os.fspath(canonical), "exec"), module.__dict__)
    return module


feasibility = _load_trusted_feasibility_module()


SCHEMA = "dancers-planar-physics-source-lift-record-v1"
EPOCH = "simulator-demo-v1-physics-stress-test-epoch-1"
DEFAULT_MANIFEST = "docs/simulator/physics_source_lift_record_epoch_1_v1.json"
EXPECTED_MANIFEST_SHA256 = "1e24fa3b95f182573a0dcfbd2bf7dc0c3520ed525cfeff9240f86603cbfbcec9"

O_TRACE = "26c8c6f9b262a2114ac28203419cb03caf8b0d88"
R_TRACE = "de0f60d1ad4b944c2b3264b8e3ccd348708b8b8a"
O_PRE = "c515c3c0e7f1d3eecc84f75e12da508a95437805"
O_FINAL = "5383edc2cd17e72f88c6629f0c3f25125a3bd5a5"
R_FINAL = "d1a9cd3bd3bf7ede17ed9f4014b84e64289a09af"
O_PARENT = "31ce2597bacac72c21eddbc89cd986a4b2e03050"
O_PRE_TREE = "4758928d20b90a628101d06ce2392c9577400a7f"
R_PRE_TREE = "4fc208b05aaad81d93ef3bb6973ea17eeb853806"
O_FINAL_TREE = "d0f2a23cc6649887d326321ff3b3df721a3b4afc"
R_FINAL_TREE = "e548979ec8c43716bd32da25793e60d2b3f45e94"
POST_PORT_CHANGED_PATHS_SHA256 = "906e43a37feb38b594fe41d45873e4525d9221646215722b239835a860deb550"
POST_PORT_ALLOWED_OVERLAP = [
    "inc/Simulator.h", "inc/gbp/LieGroups.h", "inc/gbp/Variable.h",
    "src/Graphics.cpp", "src/Simulator.cpp", "src/gbp/Variable.cpp",
]

ALLOWED_PATHS = [
    "docs/performance/SCALING_BASELINE.md",
    "docs/performance/SCALING_RESULTS.md",
    "inc/Simulator.h",
    "inc/gbp/FactorGraph.h",
    "inc/gbp/FactorGraphLayer.h",
    "inc/gbp/GBPCore.h",
    "inc/gbp/LieGroups.h",
    "inc/gbp/Variable.h",
    "src/Graphics.cpp",
    "src/Simulator.cpp",
    "src/gbp/Factor.cpp",
    "src/gbp/Variable.cpp",
]
STATUSES = ["A", "A"] + ["M"] * 10
RESOLUTION_PATHS = ["inc/Simulator.h", "inc/gbp/Variable.h", "src/Simulator.cpp"]
AUTOMATIC_PATHS = [path for path in ALLOWED_PATHS if path not in RESOLUTION_PATHS]
PATH_LIST_SHA256 = "e9439411a8b16f8cb459f4345091d44d945543eedaf0470ca391386aa44637c8"
RESOLUTION_DOMAIN = b"dancers-planar-physics-source-lift-resolution-bundle-v1\0"
RESOLUTION_BUNDLE_SHA256 = "35814118d8792955fbae426117b4417b3e9d6b3aeb742f7083a8659d7a94f050"

CLASSIFICATION = {
    "build_authorized": False,
    "build_capture_complete": False,
    "build_provenance_complete": False,
    "capture_complete": False,
    "execution_authorized": False,
    "final_source_endpoints_frozen": True,
    "independently_retained_signed_review_receipt_present": False,
    "physics_source_lift_frozen": True,
    "promotion_eligible": False,
    "protected_external_source_roots_materialized": False,
    "record_only": True,
    "registered_scientific_run": False,
    "scientific_evidence": False,
    "scientific_evidence_eligible": False,
    "scientific_execution_authorized": False,
    "source_lift_verified": True,
}
CURRENT_BLOCKERS = [
    "independently-retained-signed-review-receipt-absent",
    "protected-external-source-root-materialization-absent",
    "protected-four-product-build-capture-absent",
    "build-test-attestation-absent",
    "external-execution-identity-manifest-absent",
    "execution-design-and-freezer-v2-rebind-absent",
]
REVIEWERS = [
    {"identifier": "codex/final-source-lift-derivation", "role": "source-lift-derivation", "signed": False},
    {"identifier": "codex/final-superbuild-audit", "role": "independent-in-process-audit", "signed": False},
]
RATIONALes = {
    "inc/Simulator.h": "Preserve the registered reference map-backed neighbour method while retaining the post-port null initialization required by failed-initialization cleanup and the const DistanceField declaration required by the equal outside-allowlist definition.",
    "inc/gbp/Variable.h": "Preserve removal of the optimized updateBeliefR1 method while retaining the post-port measured-boundary reset API; the frozen exact-context patch rejects, but the full three-way content resolution is unambiguous.",
    "src/Simulator.cpp": "Preserve the complete common opaque-port and physics integration, transactionally populate the reference map-backed robot-position store during bootstrap, and use the registered reference void neighbour pass with unconditional interrobot-factor updates in both legacy and physics flows.",
}
REJECTED = {
    "inc/Simulator.h": {
        "bytes": 12468,
        "failure": "uninitialized treeOfRobots_ permits invalid cleanup and the non-const declaration disagrees with an equal outside-allowlist definition",
        "oid": "094eb96dca07f547eaa42c3c73a01c20884d8738",
        "sha256": "8dbf91cf97d8b9cdca538eb0f23997d7a8a95a4b4952d5d4b45f5b303c1a7b6f",
    },
    "inc/gbp/Variable.h": None,
    "src/Simulator.cpp": {
        "bytes": 59424,
        "failure": "optimized bool neighbour flow cannot match the reference void interface and bootstrap leaves reference robot-position keys absent",
        "oid": "55e0ac5aa8e291405cc36adece7a7ae1d5318868",
        "sha256": "ca4def8e779ed871e953551f32a7be29d93339a3fabaa4ba941bd5a8346c72e0",
    },
}

PROJECT_PATHS = [
    "inc/DistanceField.h", "inc/ObstacleField.h", "inc/PhysicsArtifactLoader.h",
    "inc/Sha256.h", "inc/SimulatorControllerPortV1.h", "inc/SimulatorPhysicsBridge.h",
    "inc/physics/PhysicsAdapter.h", "inc/physics/PlanarPhysics.h",
    "src/PhysicsArtifactLoader.cpp", "src/Sha256.cpp", "src/SimulatorPhysicsBridge.cpp",
    "src/physics/PhysicsAdapter.cpp", "src/physics/PlanarPhysics.cpp",
]
METHOD_SOURCES = [
    "src/main.cpp", "src/Globals.cpp", "src/ObstacleField.cpp", "src/Robot.cpp",
    "src/CustomFactorGraphLayers/Consensus.cpp", "src/CustomFactorGraphLayers/ExampleLayer.cpp",
    "src/CustomFactorGraphLayers/Pathplanning.cpp", "src/ShapeFormation.cpp",
    "src/Simulator.cpp", "src/PathPlanningScenarios.cpp", "src/Utils.cpp",
    "src/Graphics.cpp", "src/gbp/Variable.cpp", "src/gbp/Factor.cpp",
    "src/ComparisonHooksDispatch.cpp", "src/PhysicsEvidenceHooksDispatch.cpp",
]
FIDELITY_SOURCES = [
    "src/ComparisonHooksFidelity.cpp", "src/CollisionMetrics.cpp",
    "src/ComparisonRecorder.cpp", "src/PhysicsEvidenceHooksFidelity.cpp",
    "src/PhysicsEvidenceRecorder.cpp",
]
INTERFACE_PATHS = [
    "inc/CollisionMetrics.h", "inc/ComparisonHooks.h", "inc/ComparisonHooksDispatchV1.h",
    "inc/ComparisonRecorder.h", "inc/PhysicsEvidenceHooks.h",
    "inc/PhysicsEvidenceHooksDispatchV1.h", "inc/PhysicsEvidenceRecorder.h",
    "inc/SimulatorControllerPortV1.h",
]

COMMON_PROJECTION = {
    "endpoint_equality": True,
    "entry_count": 371,
    "enumeration_rule": "the exact thirteen project_paths plus every recursive Git blob entry whose path starts with thirdparty/, with no other entry",
    "implicit_directory_count": 54,
    "manifest_bytes": 62339,
    "manifest_encoding": "for each UTF-8 bytewise-sorted entry: mode SP type SP git_oid SP decimal_blob_bytes SP lowercase_sha256 TAB relative_path LF",
    "manifest_sha256": "d570fbe72e5c47125666292e938ee43faf3b920070157204fdd5e6e54f4ce3c8",
    "mode_counts": {"100644": 362, "100755": 9},
    "path_list_encoding": "UTF-8 bytewise-sorted relative paths, one per line, terminal LF",
    "path_list_sha256": "a9c0ca8926fe5a68d4646bb0337527fb6fdec33294dddf713e9de9678d81e03d",
    "payload_bytes": 13288895,
    "project_entry_count": 13,
    "project_manifest_sha256": "2bdf2dc382b6e4984dfb279acd6b2e70dd97fe0f736a21387436457aa07d126d",
    "project_paths": PROJECT_PATHS,
    "projected_git_tree": "47f49279f69191b7a7835b8e4e8aa6ae51a2c1fb",
    "symlink_or_submodule_count": 0,
    "thirdparty_entry_count": 358,
    "thirdparty_manifest_sha256": "84942cc689103f9f81bf3f8bc40c9a2d56e1e9ea1ea7bd2ea0ea93d03fff45c9",
    "thirdparty_prefix": "thirdparty/",
}
METHOD_INVENTORY = {
    "controller_aggregate_count": 16,
    "controller_aggregate_path_list_sha256": "c330fcddc622b4df3591bc016744236625b896df9fe95ad6a0af3cfcbc79ea28",
    "controller_aggregate_sources": METHOD_SOURCES,
    "fidelity_observer_count": 5,
    "fidelity_observer_path_list_sha256": "62ae9a01c6c705ff43aaa66f7a752a139e99630eaabc7a31231f6b50e8fbc2c3",
    "fidelity_observer_sources": FIDELITY_SOURCES,
    "ordered_combined_path_list_sha256": "95523db615614bf0bd7f3797a5bdb54e89707834dd01593c22dd4c8567357fb3",
    "path_list_encoding": "listed UTF-8 paths, one per line, terminal LF",
}
INTERFACE_EQUALITY = {
    "endpoint_equality": True,
    "entry_count": 8,
    "manifest_bytes": 1211,
    "manifest_encoding": "for each listed path in order: mode SP type SP git_oid SP decimal_blob_bytes SP lowercase_sha256 TAB relative_path LF",
    "manifest_sha256": "e49a80c702ffdeb5ee2f896c1fa1ee35561c6d1b434ef366437970ed9dae4ac8",
    "paths": INTERFACE_PATHS,
    "payload_bytes": 16597,
}

SourceLiftRecordError = feasibility.SourceLiftError
HEX40 = re.compile(r"^[0-9a-f]{40}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
MAX_RECORD_BYTES = 512 * 1024


class RecordGitReader(feasibility.GitReader):
    """Extend the predecessor's fixed read-only Git surface for final endpoints."""

    @staticmethod
    def _diff_commands() -> set[tuple[str, ...]]:
        commands = set(feasibility.GitReader._diff_commands())
        binary = ("diff", "--no-ext-diff", "--no-textconv", "--no-renames", "--binary", "--full-index")
        commands.add(binary + (O_FINAL, R_FINAL, "--", *ALLOWED_PATHS))
        commands.update(binary + (O_FINAL, R_FINAL, "--", path) for path in ALLOWED_PATHS)
        commands.update(binary + (O_TRACE, R_TRACE, "--", path) for path in ALLOWED_PATHS)
        return commands

    @classmethod
    def _command_cap(cls, args: tuple[str, ...], input_bytes: bytes | None) -> int:
        small = {
            ("rev-parse", f"{O_FINAL}^{{tree}}"),
            ("rev-parse", f"{R_FINAL}^{{tree}}"),
            ("rev-list", "--parents", "-n", "1", O_FINAL),
            ("rev-list", "--parents", "-n", "1", R_FINAL),
        }
        if args in small:
            if input_bytes is not None:
                raise SourceLiftRecordError("Git input is forbidden for this command")
            return feasibility.MAX_SMALL_GIT_OUTPUT_BYTES
        if (len(args) == 5 and args[:4] == ("ls-tree", "-rz", "--full-tree", "-r")
                and args[4] in {O_FINAL, R_FINAL}):
            if input_bytes is not None:
                raise SourceLiftRecordError("Git input is forbidden for ls-tree")
            return feasibility.MAX_GIT_OUTPUT_BYTES
        return super()._command_cap(args, input_bytes)


def _read_regular(path: Path, maximum: int) -> bytes:
    candidate = Path(os.path.abspath(path))
    if os.path.realpath(candidate) != os.fspath(candidate):
        raise SourceLiftRecordError("bound file path has a symlink ancestor")
    fd = os.open(candidate, os.O_RDONLY | os.O_CLOEXEC | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size < 0 or before.st_size > maximum):
            raise SourceLiftRecordError("bound file is not a bounded single-link regular file")
        raw = os.read(fd, maximum + 1)
        after = os.fstat(fd)
        if (len(raw) != before.st_size or
                (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise SourceLiftRecordError("bound file changed while read")
        return raw
    finally:
        os.close(fd)


def _read_record(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _read_regular(path, MAX_RECORD_BYTES)
    value = feasibility._strict_json(raw, "source-lift record")
    if type(value) is not dict:
        raise SourceLiftRecordError("source-lift record must be one object")
    if raw != feasibility.canonical_json_bytes(value):
        raise SourceLiftRecordError("source-lift record is not canonical JSON with one terminal LF")
    return value, raw


def _digest_lines(paths: Sequence[str]) -> str:
    return hashlib.sha256(("\n".join(paths) + "\n").encode("utf-8")).hexdigest()


def _identity_shape(value: Any, label: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if type(value) is not dict or set(value) != {"bytes", "mode", "oid", "sha256", "type"}:
        raise SourceLiftRecordError(f"{label} identity shape differs")
    if (type(value["bytes"]) is not int or value["bytes"] < 0
            or value["mode"] not in {"100644", "100755"} or value["type"] != "blob"
            or type(value["oid"]) is not str or not HEX40.fullmatch(value["oid"])
            or type(value["sha256"]) is not str or not HEX64.fullmatch(value["sha256"])):
        raise SourceLiftRecordError(f"{label} identity value differs")


def _patch_shape(value: Any, label: str) -> None:
    if type(value) is not dict or set(value) != {"bytes", "patch_id_stable", "sha256"}:
        raise SourceLiftRecordError(f"{label} patch shape differs")
    if (type(value["bytes"]) is not int or value["bytes"] <= 0
            or type(value["patch_id_stable"]) is not str or not HEX40.fullmatch(value["patch_id_stable"])
            or type(value["sha256"]) is not str or not HEX64.fullmatch(value["sha256"])):
        raise SourceLiftRecordError(f"{label} patch value differs")


def _resolution_expected(path: str) -> dict[str, Any]:
    return {
        "failed_exact_apply": True,
        "independently_retained_signed_review_receipt": None,
        "rationale": RATIONALes[path],
        "rejected_naive_candidate": REJECTED[path],
        "review_basis": "unsigned in-process independent reconstruction; not an external trust root",
        "reviewers": REVIEWERS,
    }


def _validate_shape(record: dict[str, Any]) -> None:
    expected_top = {
        "allowed_method_delta", "classification", "common_source_projection",
        "commutative_proof", "current_blockers", "endpoint_bindings", "epoch_id",
        "interface_equality", "method_inventory", "repository_contract",
        "review_disclosure", "schema_version", "upstream_bindings",
    }
    if set(record) != expected_top or record.get("schema_version") != SCHEMA or record.get("epoch_id") != EPOCH:
        raise SourceLiftRecordError("record top-level exact schema differs")
    if record["classification"] != CLASSIFICATION or record["current_blockers"] != CURRENT_BLOCKERS:
        raise SourceLiftRecordError("record classification or blocker lock differs")
    if record["repository_contract"] != feasibility.REPOSITORY_CONTRACT:
        raise SourceLiftRecordError("record read-only repository contract differs")
    if record["endpoint_bindings"] != {
        "optimized_source": {"commit": O_FINAL, "materialized_in_repository": True, "parent": O_PARENT, "role": "optimized-source", "tree": O_FINAL_TREE},
        "reference_source": {"commit": R_FINAL, "materialized_in_repository": True, "parent": O_FINAL, "role": "reference-source", "tree": R_FINAL_TREE},
    }:
        raise SourceLiftRecordError("final endpoint binding differs")
    if record["common_source_projection"] != COMMON_PROJECTION:
        raise SourceLiftRecordError("common-source projection contract differs")
    if record["method_inventory"] != METHOD_INVENTORY or record["interface_equality"] != INTERFACE_EQUALITY:
        raise SourceLiftRecordError("method or shared-interface inventory differs")
    if record["review_disclosure"] != {
        "independent_retained_signed_review_receipt": None,
        "in_process_review_is_external_authority": False,
        "protected_external_source_roots_materialized": False,
        "repository_commits_are_protected_external_roots": False,
        "statement": "The endpoint trees and resolutions were independently reconstructed in-process, but no independently retained signed review receipt or protected external source-root materialization exists at this checkpoint.",
    }:
        raise SourceLiftRecordError("review/trust disclosure differs")
    expected_upstream = {
        "feasibility_manifest": {"bytes": 12123, "path": "docs/simulator/physics_source_lift_epoch_1_v1.json", "schema_version": feasibility.SCHEMA, "sha256": feasibility.EXPECTED_MANIFEST_SHA256},
        "feasibility_verifier": {"bytes": 43773, "path": "scripts/comparison/verify_physics_source_lift_v1.py", "role": "trusted-read-only-git-and-historical-lift-implementation", "sha256": "4ee06d153a4cad245e0ce227ca8ab384ba8137e347ad1e9b73a3fd5808955be2"},
        "superbuild_graph": {"bytes": 14455, "path": "comparison/physics-superbuild-v1/source_graph_v1.json", "schema_version": "dancers-planar-physics-superbuild-source-graph-v1", "sha256": "3df23a2292833277e707d46d961d640928653317664206171787e590491e7c8d"},
    }
    if record["upstream_bindings"] != expected_upstream:
        raise SourceLiftRecordError("predecessor design binding differs")
    proof = record["commutative_proof"]
    expected_status = [{"path": path, "status": status} for path, status in zip(ALLOWED_PATHS, STATUSES)]
    if proof != {
        "final_name_status": expected_status,
        "optimized_final_tree": O_FINAL_TREE,
        "optimized_prephysics_tree": O_PRE_TREE,
        "optimized_route_result_tree": R_FINAL_TREE,
        "outside_allowlist": {"encoding": "raw git ls-tree -r lines for every path outside the exact allowlist, retained byte-for-byte in Git order", "entry_count": 1796, "equality": True, "manifest_bytes": 288269, "sha256": "a8537f5dfdf01230bc2af6456ffcbe1819079a44e2880b289fd42402f3377c1c"},
        "post_port_changed_path_surface": {"allowed_overlap": POST_PORT_ALLOWED_OVERLAP, "count": 118, "encoding": "UTF-8 bytewise-sorted relative paths, one per line, terminal LF", "manifest_bytes": 4766, "optimized_and_reference_equal": True, "sha256": POST_PORT_CHANGED_PATHS_SHA256},
        "reference_final_tree": R_FINAL_TREE,
        "reference_prephysics_route_result_tree": R_FINAL_TREE,
        "reference_prephysics_tree": R_PRE_TREE,
        "routes_equal": True,
    }:
        raise SourceLiftRecordError("commutative proof declaration differs")
    allowed = record["allowed_method_delta"]
    expected_allowed_keys = {
        "automatic_paths", "exact_apply_algorithm", "final_full_patch", "final_patch_format",
        "historical_full_patch", "historical_patch_format", "path_list_encoding",
        "path_list_sha256", "path_records", "paths", "reference_to_optimized_statuses",
        "resolution_bundle", "resolution_paths",
    }
    if type(allowed) is not dict or set(allowed) != expected_allowed_keys:
        raise SourceLiftRecordError("allowed-method exact key set differs")
    if (allowed["paths"] != ALLOWED_PATHS or allowed["automatic_paths"] != AUTOMATIC_PATHS
            or allowed["resolution_paths"] != RESOLUTION_PATHS
            or allowed["reference_to_optimized_statuses"] != STATUSES
            or allowed["path_list_sha256"] != PATH_LIST_SHA256
            or allowed["path_list_encoding"] != "UTF-8 bytewise-sorted paths, one per line, with a terminal newline"
            or _digest_lines(ALLOWED_PATHS) != PATH_LIST_SHA256):
        raise SourceLiftRecordError("allowed path/status partition differs")
    if allowed["exact_apply_algorithm"] != "exact ordered unified-diff context application with three context lines, one unique match per hunk, and no fuzz":
        raise SourceLiftRecordError("exact-application algorithm differs")
    if allowed["final_patch_format"] != "git diff --no-ext-diff --no-textconv --no-renames --binary --full-index <optimized-final> <reference-final> -- <ordered paths>" or allowed["historical_patch_format"] != "git diff --no-ext-diff --no-textconv --no-renames --binary --full-index <optimized-trace> <reference-trace> -- <ordered paths>":
        raise SourceLiftRecordError("patch command format differs")
    _patch_shape(allowed["final_full_patch"], "final_full_patch")
    _patch_shape(allowed["historical_full_patch"], "historical_full_patch")
    bundle = allowed["resolution_bundle"]
    if bundle != {
        "canonical_json_termination": "no trailing byte",
        "domain": "dancers-planar-physics-source-lift-resolution-bundle-v1\\0",
        "formula": "sha256(utf8(domain with final two characters interpreted as one NUL byte) || canonical_json(three complete resolution path_records in resolution_paths order, excluding resolution_bundle))",
        "sha256": RESOLUTION_BUNDLE_SHA256,
    }:
        raise SourceLiftRecordError("resolution-bundle formula or identity differs")
    records = allowed["path_records"]
    if type(records) is not list or len(records) != len(ALLOWED_PATHS):
        raise SourceLiftRecordError("path-record cardinality differs")
    keys = {"application", "final_patch", "historical_patch", "optimized_final", "optimized_prephysics", "path", "reference_final", "reference_prephysics", "reference_to_optimized_status", "resolution"}
    for index, item in enumerate(records):
        if type(item) is not dict or set(item) != keys or item["path"] != ALLOWED_PATHS[index] or item["reference_to_optimized_status"] != STATUSES[index]:
            raise SourceLiftRecordError("path-record order, keys, or status differs")
        expected_application = "authenticated-resolution" if item["path"] in RESOLUTION_PATHS else "automatic-exact-context"
        if item["application"] != expected_application:
            raise SourceLiftRecordError("path application class differs")
        for field in ("optimized_final", "optimized_prephysics"):
            _identity_shape(item[field], f"{item['path']} {field}")
        for field in ("reference_final", "reference_prephysics"):
            _identity_shape(item[field], f"{item['path']} {field}", nullable=True)
        _patch_shape(item["historical_patch"], f"{item['path']} historical_patch")
        _patch_shape(item["final_patch"], f"{item['path']} final_patch")
        expected_resolution = _resolution_expected(item["path"]) if item["path"] in RESOLUTION_PATHS else None
        if item["resolution"] != expected_resolution:
            raise SourceLiftRecordError("resolution rationale, reviewer identity, or rejected candidate differs")
    fragment = json.dumps(
        [item for item in records if item["path"] in RESOLUTION_PATHS],
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode("utf-8")
    if hashlib.sha256(RESOLUTION_DOMAIN + fragment).hexdigest() != RESOLUTION_BUNDLE_SHA256:
        raise SourceLiftRecordError("resolution-bundle digest does not reconstruct")


def _entry_identity(reader: RecordGitReader, identity: dict[str, Any] | None,
                    tree_entry: dict[str, str] | None, label: str) -> bytes | None:
    if identity is None:
        if tree_entry is not None:
            raise SourceLiftRecordError(f"{label} unexpectedly exists")
        return None
    raw = feasibility._blob(reader, identity, label)
    if {key: identity[key] for key in ("mode", "type", "oid")} != tree_entry:
        raise SourceLiftRecordError(f"{label} tree metadata differs")
    return raw


def _patch_identity(reader: RecordGitReader, raw: bytes, expected: dict[str, Any], label: str) -> None:
    if (len(raw) != expected["bytes"] or hashlib.sha256(raw).hexdigest() != expected["sha256"]
            or feasibility._patch_id(reader, raw) != expected["patch_id_stable"]):
        raise SourceLiftRecordError(f"{label} identity differs")


def _raw_tree(reader: RecordGitReader, rev: str) -> list[tuple[bytes, str]]:
    raw = reader.run(["ls-tree", "-rz", "--full-tree", "-r", rev])
    result: list[tuple[bytes, str]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path_raw = record.split(b"\t", 1)
            path = path_raw.decode("utf-8", "strict")
        except (UnicodeError, ValueError) as exc:
            raise SourceLiftRecordError("final tree raw record is malformed") from exc
        result.append((metadata, path))
    return result


def _projection_manifest(reader: RecordGitReader, tree: dict[str, dict[str, str]],
                         paths: Sequence[str]) -> tuple[bytes, int]:
    lines: list[bytes] = []
    total = 0
    for path in paths:
        entry = tree.get(path)
        if entry is None or entry["type"] != "blob":
            raise SourceLiftRecordError(f"projected blob is absent: {path}")
        raw = feasibility._read_blob_oid(reader, entry["oid"], f"projection {path}")
        total += len(raw)
        lines.append(
            f"{entry['mode']} {entry['type']} {entry['oid']} {len(raw)} {hashlib.sha256(raw).hexdigest()}\t{path}\n".encode("utf-8")
        )
    return b"".join(lines), total


def _verify_repository(repository_root: Path, manifest_path: Path | None = None,
                       expected_manifest_sha256: str | None = None) -> dict[str, Any]:
    root = Path(repository_root).resolve(strict=True)
    path = manifest_path or root / DEFAULT_MANIFEST
    record, raw = _read_record(path)
    if expected_manifest_sha256 is not None and expected_manifest_sha256 != EXPECTED_MANIFEST_SHA256:
        raise SourceLiftRecordError("caller-supplied record identity differs from the pinned expectation")
    observed = hashlib.sha256(raw).hexdigest()
    if observed != EXPECTED_MANIFEST_SHA256:
        raise SourceLiftRecordError("source-lift record SHA-256 differs from the pinned expectation")
    _validate_shape(record)

    for binding in record["upstream_bindings"].values():
        bound = _read_regular(root / binding["path"], 1024 * 1024)
        if len(bound) != binding["bytes"] or hashlib.sha256(bound).hexdigest() != binding["sha256"]:
            raise SourceLiftRecordError("predecessor design file bytes changed")

    reader = RecordGitReader(root)
    if reader.run(["rev-parse", "--show-object-format"]).strip() != b"sha1":
        raise SourceLiftRecordError("repository object format differs")
    if reader.run(["rev-parse", "--is-shallow-repository"]).strip() != b"false":
        raise SourceLiftRecordError("repository is shallow")
    if reader.run(["replace", "-l"]).strip():
        raise SourceLiftRecordError("repository has replacement objects")
    feasibility._validate_repository_storage(reader)
    if reader.run(["rev-parse", f"{O_FINAL}^{{tree}}"]).decode().strip() != O_FINAL_TREE or reader.run(["rev-parse", f"{R_FINAL}^{{tree}}"]).decode().strip() != R_FINAL_TREE:
        raise SourceLiftRecordError("materialized endpoint tree differs")
    if reader.run(["rev-list", "--parents", "-n", "1", O_FINAL]).decode().split() != [O_FINAL, O_PARENT] or reader.run(["rev-list", "--parents", "-n", "1", R_FINAL]).decode().split() != [R_FINAL, O_FINAL]:
        raise SourceLiftRecordError("materialized endpoint parent relation differs")

    o_trace = feasibility._tree(reader, O_TRACE)
    r_trace = feasibility._tree(reader, R_TRACE)
    o_pre = feasibility._tree(reader, O_PRE)
    o_final = feasibility._tree(reader, O_FINAL)
    r_final = feasibility._tree(reader, R_FINAL)
    if feasibility._git_tree_oid(o_pre) != O_PRE_TREE or feasibility._git_tree_oid(o_final) != O_FINAL_TREE or feasibility._git_tree_oid(r_final) != R_FINAL_TREE:
        raise SourceLiftRecordError("reconstructed endpoint tree differs")

    derived_pre = dict(o_pre)
    for path_name in ALLOWED_PATHS:
        if path_name in r_trace:
            derived_pre[path_name] = r_trace[path_name]
        else:
            derived_pre.pop(path_name, None)
    if feasibility._git_tree_oid(derived_pre) != R_PRE_TREE:
        raise SourceLiftRecordError("reference-prephysics tree does not reconstruct")

    optimized_changed = sorted(
        (path_name for path_name in set(o_pre) | set(o_final)
         if o_pre.get(path_name) != o_final.get(path_name)),
        key=lambda item: item.encode("utf-8"),
    )
    reference_changed = sorted(
        (path_name for path_name in set(derived_pre) | set(r_final)
         if derived_pre.get(path_name) != r_final.get(path_name)),
        key=lambda item: item.encode("utf-8"),
    )
    changed_manifest = ("\n".join(optimized_changed) + "\n").encode("utf-8")
    surface = record["commutative_proof"]["post_port_changed_path_surface"]
    if (optimized_changed != reference_changed
            or len(optimized_changed) != surface["count"]
            or len(changed_manifest) != surface["manifest_bytes"]
            or hashlib.sha256(changed_manifest).hexdigest() != surface["sha256"]
            or [path_name for path_name in ALLOWED_PATHS if path_name in optimized_changed]
                != surface["allowed_overlap"]):
        raise SourceLiftRecordError("optimized/reference post-port changed-path surface differs")
    reference_route = dict(derived_pre)
    for path_name in optimized_changed:
        if path_name not in ALLOWED_PATHS and (
                o_pre.get(path_name) != derived_pre.get(path_name)
                or o_final.get(path_name) != r_final.get(path_name)):
            raise SourceLiftRecordError("shared post-port route differs outside the method boundary")
        if path_name in r_final:
            reference_route[path_name] = r_final[path_name]
        else:
            reference_route.pop(path_name, None)
    if reference_route != r_final or feasibility._git_tree_oid(reference_route) != R_FINAL_TREE:
        raise SourceLiftRecordError("reference-prephysics route does not reconstruct reference-final")

    differences: list[dict[str, str]] = []
    for path_name in sorted(set(r_final) | set(o_final), key=lambda item: item.encode("utf-8")):
        left, right = r_final.get(path_name), o_final.get(path_name)
        if left != right:
            differences.append({"path": path_name, "status": "A" if left is None else "D" if right is None else "M"})
        elif path_name not in ALLOWED_PATHS and left != right:
            raise SourceLiftRecordError("outside-allowlist entry differs")
    if differences != record["commutative_proof"]["final_name_status"]:
        raise SourceLiftRecordError("final name-status surface differs")
    for path_name in set(o_final) | set(r_final):
        if path_name not in ALLOWED_PATHS and o_final.get(path_name) != r_final.get(path_name):
            raise SourceLiftRecordError("outside-allowlist entry is not byte-and-mode equal")

    outside = b"".join(
        metadata + b"\t" + path_name.encode("utf-8") + b"\n"
        for metadata, path_name in _raw_tree(reader, O_FINAL)
        if path_name not in ALLOWED_PATHS
    )
    outside_contract = record["commutative_proof"]["outside_allowlist"]
    if (outside.count(b"\n") != outside_contract["entry_count"]
            or len(outside) != outside_contract["manifest_bytes"]
            or hashlib.sha256(outside).hexdigest() != outside_contract["sha256"]):
        raise SourceLiftRecordError("outside-allowlist projection identity differs")

    allowed = record["allowed_method_delta"]
    final_full = feasibility._diff(reader, O_FINAL, R_FINAL, ALLOWED_PATHS)
    historical_full = feasibility._diff(reader, O_TRACE, R_TRACE, ALLOWED_PATHS)
    _patch_identity(reader, final_full, allowed["final_full_patch"], "final full patch")
    _patch_identity(reader, historical_full, allowed["historical_full_patch"], "historical full patch")

    reconstructed = dict(o_final)
    for item in allowed["path_records"]:
        path_name = item["path"]
        o_pre_raw = _entry_identity(reader, item["optimized_prephysics"], o_pre.get(path_name), f"{path_name} optimized prephysics")
        r_pre_raw = _entry_identity(reader, item["reference_prephysics"], r_trace.get(path_name), f"{path_name} reference prephysics")
        o_raw = _entry_identity(reader, item["optimized_final"], o_final.get(path_name), f"{path_name} optimized final")
        r_raw = _entry_identity(reader, item["reference_final"], r_final.get(path_name), f"{path_name} reference final")
        historic = feasibility._diff(reader, O_TRACE, R_TRACE, [path_name])
        final = feasibility._diff(reader, O_FINAL, R_FINAL, [path_name])
        _patch_identity(reader, historic, item["historical_patch"], f"{path_name} historical patch")
        _patch_identity(reader, final, item["final_patch"], f"{path_name} final patch")
        if o_pre_raw != feasibility._read_blob_oid(reader, o_trace[path_name]["oid"], f"{path_name} optimized trace"):
            raise SourceLiftRecordError(f"optimized prephysics corner differs for {path_name}")
        if path_name in AUTOMATIC_PATHS:
            if r_pre_raw is None:
                if r_raw is not None or o_raw != o_pre_raw:
                    raise SourceLiftRecordError(f"automatic deletion differs for {path_name}")
            else:
                unified = feasibility._diff(reader, O_TRACE, R_TRACE, [path_name], unified=True)
                lifted = feasibility._apply_exact(o_raw or b"", feasibility._unified_hunks(unified))
                if lifted != r_raw:
                    raise SourceLiftRecordError(f"automatic exact lift differs for {path_name}")
        else:
            unified = feasibility._diff(reader, O_TRACE, R_TRACE, [path_name], unified=True)
            if feasibility._apply_exact(o_raw or b"", feasibility._unified_hunks(unified)) is not None:
                raise SourceLiftRecordError(f"declared conflict applies automatically for {path_name}")
        if r_raw is None:
            reconstructed.pop(path_name, None)
        else:
            assert item["reference_final"] is not None
            reconstructed[path_name] = {key: item["reference_final"][key] for key in ("mode", "type", "oid")}
    if feasibility._git_tree_oid(reconstructed) != R_FINAL_TREE or reconstructed != r_final:
        raise SourceLiftRecordError("optimized route does not reconstruct the reference-final tree")

    header = feasibility._read_blob_oid(reader, r_final["inc/Simulator.h"]["oid"], "resolved Simulator header")
    simulator = feasibility._read_blob_oid(reader, r_final["src/Simulator.cpp"]["oid"], "resolved Simulator source")
    variable = feasibility._read_blob_oid(reader, r_final["inc/gbp/Variable.h"]["oid"], "resolved Variable header")
    if (b"KDTreeMapOfVectorsAdaptor<std::map<int,Eigen::VectorXd>>" not in header
            or b"KDTree* treeOfRobots_ = nullptr;" not in header
            or b"const DistanceField* p_obsImg" not in header
            or b"KDTreeVectorOfVectorsAdaptor" in header):
        raise SourceLiftRecordError("resolved reference-map header invariants differ")
    if (b"std::map<int, Eigen::VectorXd> robot_position_candidates;" not in simulator
            or b"active.robot_positions_.swap(robot_position_candidates);" not in simulator
            or b"const bool neighbour_topology_changed = calculateRobotNeighbours(robots_);" in simulator
            or simulator.count(b"for (auto [rid, robot] : robots_) robot->updateInterrobotFactors();") != 2):
        raise SourceLiftRecordError("resolved reference neighbour-flow invariants differ")
    if b"updateBeliefR1" in variable or b"resetMeasuredPriorBeliefAndOutboxes" not in variable:
        raise SourceLiftRecordError("resolved reference Variable interface differs")

    selected = sorted(
        set(PROJECT_PATHS) | {path_name for path_name in o_final if path_name.startswith("thirdparty/")},
        key=lambda item: item.encode("utf-8"),
    )
    if len(selected) != COMMON_PROJECTION["entry_count"] or any(o_final.get(path_name) != r_final.get(path_name) for path_name in selected):
        raise SourceLiftRecordError("common-source endpoint projection differs")
    if _digest_lines(selected) != COMMON_PROJECTION["path_list_sha256"]:
        raise SourceLiftRecordError("common-source projected path list differs")
    manifest, payload = _projection_manifest(reader, o_final, selected)
    project_manifest, _ = _projection_manifest(reader, o_final, PROJECT_PATHS)
    thirdparty_paths = [path_name for path_name in selected if path_name.startswith("thirdparty/")]
    thirdparty_manifest, _ = _projection_manifest(reader, o_final, thirdparty_paths)
    mode_counts = {mode: sum(o_final[path_name]["mode"] == mode for path_name in selected) for mode in ("100644", "100755")}
    directories = {"/".join(path_name.split("/")[:index]) for path_name in selected for index in range(1, len(path_name.split("/")))}
    projected = {path_name: o_final[path_name] for path_name in selected}
    if (len(manifest) != COMMON_PROJECTION["manifest_bytes"] or payload != COMMON_PROJECTION["payload_bytes"]
            or hashlib.sha256(manifest).hexdigest() != COMMON_PROJECTION["manifest_sha256"]
            or hashlib.sha256(project_manifest).hexdigest() != COMMON_PROJECTION["project_manifest_sha256"]
            or hashlib.sha256(thirdparty_manifest).hexdigest() != COMMON_PROJECTION["thirdparty_manifest_sha256"]
            or len(thirdparty_paths) != COMMON_PROJECTION["thirdparty_entry_count"]
            or mode_counts != COMMON_PROJECTION["mode_counts"]
            or len(directories) != COMMON_PROJECTION["implicit_directory_count"]
            or feasibility._git_tree_oid(projected) != COMMON_PROJECTION["projected_git_tree"]):
        raise SourceLiftRecordError("common-source manifest/tree identity differs")

    for path_name in METHOD_SOURCES + FIDELITY_SOURCES:
        if o_final.get(path_name, {}).get("type") != "blob" or r_final.get(path_name, {}).get("type") != "blob":
            raise SourceLiftRecordError(f"method inventory source is absent: {path_name}")
    if (_digest_lines(METHOD_SOURCES) != METHOD_INVENTORY["controller_aggregate_path_list_sha256"]
            or _digest_lines(FIDELITY_SOURCES) != METHOD_INVENTORY["fidelity_observer_path_list_sha256"]
            or _digest_lines(METHOD_SOURCES + FIDELITY_SOURCES) != METHOD_INVENTORY["ordered_combined_path_list_sha256"]):
        raise SourceLiftRecordError("method inventory digest differs")
    if any(o_final.get(path_name) != r_final.get(path_name) for path_name in INTERFACE_PATHS):
        raise SourceLiftRecordError("shared interface entry differs between endpoints")
    interface_manifest, interface_payload = _projection_manifest(reader, o_final, INTERFACE_PATHS)
    if (len(interface_manifest) != INTERFACE_EQUALITY["manifest_bytes"]
            or interface_payload != INTERFACE_EQUALITY["payload_bytes"]
            or hashlib.sha256(interface_manifest).hexdigest() != INTERFACE_EQUALITY["manifest_sha256"]):
        raise SourceLiftRecordError("shared interface manifest identity differs")

    return {
        "build_capture_complete": False,
        "common_source_entry_count": 371,
        "final_source_endpoints_frozen": True,
        "manifest_sha256": observed,
        "optimized_source_tree": O_FINAL_TREE,
        "promotion_eligible": False,
        "protected_external_source_roots_materialized": False,
        "reference_source_tree": R_FINAL_TREE,
        "resolution_bundle_sha256": RESOLUTION_BUNDLE_SHA256,
        "scientific_evidence": False,
        "scientific_execution_authorized": False,
        "source_lift_verified": True,
    }


def verify_repository(repository_root: Path, manifest_path: Path | None = None,
                      expected_manifest_sha256: str | None = None) -> dict[str, Any]:
    try:
        return _verify_repository(repository_root, manifest_path, expected_manifest_sha256)
    except feasibility.SourceLiftError:
        raise
    except (AttributeError, IndexError, KeyError, TypeError, UnicodeError, ValueError) as exc:
        raise SourceLiftRecordError("record or Git evidence has an invalid nested shape") from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--expected-manifest-sha256", default=EXPECTED_MANIFEST_SHA256)
    args = parser.parse_args(argv)
    try:
        result = verify_repository(args.repository_root, args.manifest, args.expected_manifest_sha256)
    except (OSError, feasibility.SourceLiftError) as exc:
        print(f"physics-source-lift-record-v1: reject: {exc}", file=sys.stderr)
        return 1
    print(feasibility.canonical_json_bytes(result).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
