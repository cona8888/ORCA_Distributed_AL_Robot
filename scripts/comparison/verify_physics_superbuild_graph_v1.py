#!/usr/bin/env python3
"""Strict, data-only verifier for the prospective physics superbuild graph v1."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any


SCHEMA_VERSION = "dancers-planar-physics-superbuild-source-graph-v1"
DEFAULT_GRAPH = "comparison/physics-superbuild-v1/source_graph_v1.json"
MAX_GRAPH_BYTES = 262_144
EXPECTED_GRAPH_BYTES = 14_455
EXPECTED_GRAPH_SHA256 = "3df23a2292833277e707d46d961d640928653317664206171787e590491e7c8d"
EXPECTED_TOP_LEVEL_KEYS = {
    "analytic_static_tests",
    "blockers",
    "classification",
    "contract_id",
    "historical_method_boundary",
    "literal_common_objects",
    "method_controller_aggregate",
    "observer_partition",
    "parity_contract",
    "products",
    "schema_version",
    "simulator_controller_port",
    "source_lift_feasibility_binding",
    "source_roots",
}
EXPECTED_CLASSIFICATION = {
    "actual_build_captured": False,
    "build_authorized": False,
    "build_executed": False,
    "capture_complete": False,
    "execution_authorized": False,
    "official_namespace_materialized": False,
    "promotion_eligible": False,
    "registered_scientific_run": False,
    "scientific_evidence": False,
    "structural_design_only": True,
}
EXPECTED_COMMON = [
    ("physics-engine", "src/physics/PlanarPhysics.cpp"),
    ("controller-adapter", "src/physics/PhysicsAdapter.cpp"),
    ("artifact-loader", "src/PhysicsArtifactLoader.cpp"),
    ("simulator-bridge", "src/SimulatorPhysicsBridge.cpp"),
    ("sha256", "src/Sha256.cpp"),
]
EXPECTED_METHOD_BOUNDARY = [
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
EXPECTED_CORE = [
    "src/main.cpp",
    "src/Globals.cpp",
    "src/ObstacleField.cpp",
    "src/Robot.cpp",
    "src/CustomFactorGraphLayers/Consensus.cpp",
    "src/CustomFactorGraphLayers/ExampleLayer.cpp",
    "src/CustomFactorGraphLayers/Pathplanning.cpp",
    "src/ShapeFormation.cpp",
    "src/Simulator.cpp",
    "src/PathPlanningScenarios.cpp",
    "src/Utils.cpp",
    "src/Graphics.cpp",
    "src/gbp/Variable.cpp",
    "src/gbp/Factor.cpp",
]
EXPECTED_DISPATCH = [
    "src/ComparisonHooksDispatch.cpp",
    "src/PhysicsEvidenceHooksDispatch.cpp",
]
EXPECTED_OBSERVERS = [
    "src/ComparisonHooksFidelity.cpp",
    "src/CollisionMetrics.cpp",
    "src/ComparisonRecorder.cpp",
    "src/PhysicsEvidenceHooksFidelity.cpp",
    "src/PhysicsEvidenceRecorder.cpp",
]
EXPECTED_PRODUCT_ORDER = [
    "optimized-fidelity",
    "optimized-timing",
    "reference-fidelity",
    "reference-timing",
]
EXPECTED_BLOCKERS = [
    "simulator-controller-port-v1-not-implemented",
    "observer-dispatch-translation-units-not-implemented",
    "standalone-four-product-superbuild-not-implemented",
    "protected-causal-build-capture-not-implemented",
    "source-pinned-four-product-build-not-performed",
    "literal-object-product-and-parity-attestation-absent",
    "final-post-port-commutative-source-lift-record-absent",
    "final-reference-and-optimized-source-endpoints-not-frozen",
]
EXPECTED_SOURCE_LIFT_BINDING = {
    "bytes": 12_123,
    "final_source_endpoints_frozen": False,
    "future_post_port_superbuild_source_lift_record_required": True,
    "path": "docs/simulator/physics_source_lift_epoch_1_v1.json",
    "schema_version": "dancers-planar-physics-source-lift-feasibility-v1",
    "sha256": "52ab7a20d01978107b1122ce70c8fe2a3719f34719e5c61cff5a960581d46bee",
}
EXPECTED_SOURCE_ROOTS = {
    "common_source": {
        "root_id": "common-source",
        "rule": "one clean authenticated physics-support snapshot supplies only literal common sources and port declarations",
    },
    "configure_count": 1,
    "graph_count": 1,
    "optimized_source": {
        "historical_pin": "99364a41f1535a4bb0eddbe5a10030127ea77086",
        "root_id": "optimized-source",
        "rule": "one clean authenticated optimized controller snapshot with the reviewed common physics integration",
    },
    "reference_source": {
        "historical_pin": "986f47deec5b079663c7d7f6120bfc20f0467901",
        "root_id": "reference-source",
        "rule": "one clean authenticated reference controller snapshot with the same reviewed common physics integration",
    },
    "standalone_superbuild_path": "comparison/physics-superbuild-v1/CMakeLists.txt",
}
EXPECTED_ORDER_RESOLUTION = {
    "selected_profile": "v2-optimized-first",
    "v1_contract_declared_order": [
        "reference-fidelity", "reference-timing",
        "optimized-fidelity", "optimized-timing",
    ],
    "v1_execution_authorization_permitted": False,
    "v1_structural_verifier_enforced_order": EXPECTED_PRODUCT_ORDER,
    "resolution": "the prospective v2 graph explicitly selects optimized-first; the contradictory v1 contract and verifier remain non-authorizing historical scaffolds and cannot authorize a build capture or execution",
}
EXPECTED_OUTPUT_NAMES = {
    "optimized-fidelity": "dancers-optimized-fidelity",
    "optimized-timing": "dancers-optimized-timing",
    "reference-fidelity": "dancers-reference-fidelity",
    "reference-timing": "dancers-reference-timing",
}
EXPECTED_PARITY = {
    "common_dependency_targets": {
        "configured_once_in_graph": True,
        "exact_instances_reused": True,
        "roles": ["Eigen3::Eigen", "manif", "OpenMP::OpenMP_CXX", "raylib"],
    },
    "compile": {
        "common_actions": "one action per common source, never one action per method",
        "method_action_difference_allowlist": [
            "canonical-method-source-root-prefix",
            "canonical-method-output-path",
            "canonical-method-dependency-file-path",
            "discovered-method-source-file-identities",
        ],
        "normalized_argv_environment_and_nonmethod_dependency_identities_equal": True,
        "required_common_flags": [
            "cxx-standard-20", "Release", "EIGEN_DONT_PARALLELIZE",
            "authenticated-common-runtime-root",
            "authenticated-architecture-sdk-and-deployment-target",
        ],
    },
    "link": {
        "difference_allowlist": [
            "output-product-path", "selected-method-aggregate-object",
            "fidelity-observer-object-suffix",
        ],
        "flags_equal_after_allowlist_normalization": True,
        "ordered_library_identity_sequence_equal_for_all_products": True,
        "resolved_non_system_dynamic_input_sequence_equal_for_all_products": True,
    },
    "runtime_artifact_root": {
        "baked_definition_count_per_compile": 1,
        "canonical_absolute_identity_equal_for_all_actions_and_products": True,
        "method_source_root_forbidden": True,
    },
}
EXPECTED_PORT_TABLE_FIELDS = [
    "abi_version", "struct_bytes", "context", "controller_tick",
    "bootstrap_controller_world", "validate_configuration",
    "validate_inventory", "validate_topology", "publish_measured_boundary",
    "validate_published_boundary", "arm_request_slots",
    "collect_request_slots", "consume_request_slots", "evidence_requested",
    "evidence_initialize", "evidence_boundary", "evidence_commands",
    "evidence_substep", "evidence_finish", "evidence_shutdown", "fail_runtime",
]
EXPECTED_MOVED_DEFINITIONS = [
    "SimulatorPhysicsBridge::prepareMeasuredBoundaryForSimulator",
    "SimulatorPhysicsBridge::validatePublishedBoundaryForSimulator",
    "SimulatorPhysicsBridge::publishMeasuredBoundaryForSimulator",
    "SimulatorPhysicsBridge::initializeSimulator",
    "SimulatorPhysicsBridge::beginControllerBoundary",
    "SimulatorPhysicsBridge::armRequestSlots",
    "SimulatorPhysicsBridge::completeControllerBoundary",
]
EXPECTED_PORT_RULE = "the common bridge may hold and invoke only the versioned opaque port and common value types; it must not include or derive layout from Simulator Robot Graphics PlanningLayer or gbp headers"
PROTECTED_REPOSITORY_ROOTS = (
    "comparison/plans/simulator-demo/v1/physics-stress-test/epoch-1",
    "results/simulator-demo/v1/physics-stress-test/epoch-1",
    "docs/simulator/physics_execution_identities_epoch_1_v1.json",
)


class VerificationError(ValueError):
    """The graph is not the exact frozen prospective design."""


def _reject_constant(value: str) -> None:
    raise VerificationError(f"non-finite JSON constant is forbidden: {value}")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VerificationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True,
                   separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise VerificationError(f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise VerificationError(f"{label} must be an array")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise VerificationError(f"{label} keys differ")


def _all_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _all_strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _all_strings(item)


def _reject_protected_repository_paths(graph: dict[str, Any]) -> None:
    for value in _all_strings(graph):
        for root in PROTECTED_REPOSITORY_ROOTS:
            if value == root or value.startswith(root + "/"):
                raise VerificationError(
                    "current protected plan/result/execution-identity path is forbidden"
                )


def _validate_semantics(graph: dict[str, Any]) -> None:
    _exact_keys(graph, EXPECTED_TOP_LEVEL_KEYS, "top level")
    _reject_protected_repository_paths(graph)
    if graph["schema_version"] != SCHEMA_VERSION:
        raise VerificationError("schema version differs")
    if graph["contract_id"] != "dancers-physics-superbuild-source-graph-v1":
        raise VerificationError("contract ID differs")
    if graph["classification"] != EXPECTED_CLASSIFICATION:
        raise VerificationError("classification is not exactly locked")
    if graph["source_lift_feasibility_binding"] != EXPECTED_SOURCE_LIFT_BINDING:
        raise VerificationError("source-lift feasibility identity binding differs")
    if graph["source_roots"] != EXPECTED_SOURCE_ROOTS:
        raise VerificationError("source-root and standalone-graph contract differs")
    if graph["parity_contract"] != EXPECTED_PARITY:
        raise VerificationError("compile/link/dependency/runtime parity contract differs")

    boundary = _mapping(graph["historical_method_boundary"], "method boundary")
    if boundary.get("path_count") != 12 or boundary.get("paths") != EXPECTED_METHOD_BOUNDARY:
        raise VerificationError("historical twelve-path boundary differs")

    common = _mapping(graph["literal_common_objects"], "common objects")
    roles = _sequence(common.get("roles"), "common roles")
    actual_common = [
        (record.get("role"), record.get("source"))
        for record in (_mapping(item, "common role") for item in roles)
    ]
    if actual_common != EXPECTED_COMMON or common.get("compile_once") is not True:
        raise VerificationError("literal common role/source inventory differs")
    if common.get("link_order") != [role for role, _ in EXPECTED_COMMON]:
        raise VerificationError("common link order differs")

    aggregate = _mapping(graph["method_controller_aggregate"], "aggregate")
    if aggregate.get("controller_core_count") != 14 or aggregate.get(
        "controller_core_sources"
    ) != EXPECTED_CORE:
        raise VerificationError("fourteen-source controller core differs")
    if aggregate.get("dispatch_count") != 2 or aggregate.get(
        "dispatch_sources"
    ) != EXPECTED_DISPATCH:
        raise VerificationError("two-source observer dispatch differs")
    if aggregate.get("ordered_contributor_count") != 16 or aggregate.get(
        "ordered_contributor_sources"
    ) != EXPECTED_CORE + EXPECTED_DISPATCH:
        raise VerificationError("ordered ld -r contributor closure differs")
    operation = _mapping(aggregate.get("aggregate_operation"), "aggregate operation")
    if operation.get("operation") != "relocatable-link" or operation.get(
        "tool_role"
    ) != "linker" or operation.get("private_contributors_linked_into_products") is not False:
        raise VerificationError("deterministic relocatable aggregate policy differs")
    dispatch = _mapping(aggregate.get("dispatch_policy"), "dispatch policy")
    if dispatch.get("timing_stub_objects") is not False:
        raise VerificationError("timing-stub objects are forbidden")

    observers = _mapping(graph["observer_partition"], "observer partition")
    if observers.get("fidelity_observer_count_per_method") != 5 or observers.get(
        "fidelity_sources_per_method"
    ) != EXPECTED_OBSERVERS:
        raise VerificationError("fidelity observer inventory differs")
    if observers.get("timing_observer_count") != 0 or observers.get(
        "timing_observer_sources"
    ) != []:
        raise VerificationError("timing observer inventory is not empty")

    products = _mapping(graph["products"], "products")
    records = _sequence(products.get("records"), "product records")
    _exact_keys(products, {"order", "order_resolution", "records"}, "products")
    if products.get("order") != EXPECTED_PRODUCT_ORDER or [
        record.get("key") for record in records if isinstance(record, dict)
    ] != EXPECTED_PRODUCT_ORDER or len(records) != 4:
        raise VerificationError("canonical four-product order differs")
    if products.get("order_resolution") != EXPECTED_ORDER_RESOLUTION:
        raise VerificationError("v1/v2 product-order resolution differs")
    common_terms = [f"common:{role}" for role, _ in EXPECTED_COMMON]
    for record in records:
        product = _mapping(record, "product")
        method = product.get("method")
        role = product.get("role")
        expected = common_terms + [f"aggregate:{method}"]
        if role == "fidelity":
            expected += [
                f"observer:{method}:{name}"
                for name in (
                    "comparison-hooks-fidelity",
                    "collision-metrics",
                    "comparison-recorder",
                    "physics-evidence-hooks-fidelity",
                    "physics-evidence-recorder",
                )
            ]
            if product.get("observer_mode") != "present":
                raise VerificationError("fidelity observer mode differs")
        elif role == "timing":
            if product.get("observer_mode") != "absent":
                raise VerificationError("timing observer mode differs")
        else:
            raise VerificationError("product role differs")
        if product.get("object_equation") != expected:
            raise VerificationError("product object equation differs")
        if product.get("output_name") != EXPECTED_OUTPUT_NAMES.get(product.get("key")):
            raise VerificationError("product output name differs")

    port = _mapping(graph["simulator_controller_port"], "controller port")
    _exact_keys(
        port,
        {
            "abi", "bridge_common_ownership", "controller_method_callback_ownership",
            "definitions_moved_to_common_bridge", "method_implementation_location",
            "rule",
        },
        "controller port",
    )
    abi = _mapping(port.get("abi"), "controller port ABI")
    _exact_keys(
        abi,
        {
            "context", "factory", "header", "header_dependency_allowlist", "name",
            "table_fields", "version",
        },
        "controller port ABI",
    )
    if (
        abi.get("name") != "SimulatorControllerPortV1"
        or abi.get("version") != 1
        or abi.get("factory") != "makeSimulatorControllerPortV1(Simulator&) noexcept"
        or abi.get("header") != "inc/SimulatorControllerPortV1.h"
        or abi.get("context") != "one opaque nonnull pointer owned by the method Simulator"
        or abi.get("header_dependency_allowlist") != [
            "fixed-width-standard-types", "span", "common-planar-physics-value-types",
            "forward-declared-Simulator",
        ]
        or abi.get("table_fields") != EXPECTED_PORT_TABLE_FIELDS
    ):
        raise VerificationError("controller port version differs")
    if (
        port.get("method_implementation_location") != "src/Simulator.cpp"
        or _sequence(
            port.get("definitions_moved_to_common_bridge"), "moved definitions"
        ) != EXPECTED_MOVED_DEFINITIONS
        or port.get("rule") != EXPECTED_PORT_RULE
    ):
        raise VerificationError("bridge lifecycle ownership differs")

    blockers = _sequence(graph["blockers"], "blockers")
    if [item.get("blocker_id") for item in blockers if isinstance(item, dict)] != EXPECTED_BLOCKERS:
        raise VerificationError("machine blocker inventory differs")
    if any(not isinstance(item, dict) or item.get("cleared") is not False for item in blockers):
        raise VerificationError("a machine blocker was cleared")

def _read_graph(path: Path) -> bytes:
    path = Path(path)
    rendered = os.fspath(path)
    if not path.is_absolute() or os.path.normpath(rendered) != rendered:
        raise VerificationError("graph path must be canonical and absolute")
    if os.path.realpath(rendered) != rendered:
        raise VerificationError("graph path and ancestors must contain no symlink")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(rendered, flags)
    except OSError as error:
        raise VerificationError(f"cannot open graph without following links: {error}") from error
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise VerificationError("graph must be a regular file")
        if before.st_nlink != 1:
            raise VerificationError("graph must have exactly one hard link")
        if before.st_uid != os.geteuid():
            raise VerificationError("graph must be owned by the effective user")
        if before.st_mode & 0o022:
            raise VerificationError("graph must not be group/world writable")
        if before.st_size <= 0 or before.st_size > MAX_GRAPH_BYTES:
            raise VerificationError("graph byte count is outside the frozen bound")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                raise VerificationError("graph was truncated during bounded read")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise VerificationError("graph grew during bounded read")
        after = os.fstat(descriptor)
    except OSError as error:
        raise VerificationError(f"cannot read graph: {error}") from error
    finally:
        os.close(descriptor)
    try:
        named = os.stat(rendered, follow_symlinks=False)
    except OSError as error:
        raise VerificationError("graph name disappeared after read") from error
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_mode,
        item.st_uid, item.st_nlink,
    )
    if identity(before) != identity(after) or identity(after) != identity(named):
        raise VerificationError("graph descriptor/name identity changed during read")
    return b"".join(chunks)


def verify_graph(path: Path) -> dict[str, Any]:
    raw = _read_graph(path)
    try:
        graph = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_pairs,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"invalid UTF-8 JSON: {error}") from error
    graph = _mapping(graph, "graph")
    if raw != canonical_bytes(graph):
        raise VerificationError("graph is not strict canonical JSON with one newline")
    observed_sha256 = hashlib.sha256(raw).hexdigest()
    if observed_sha256 != EXPECTED_GRAPH_SHA256:
        raise VerificationError("graph SHA-256 differs from the frozen identity")
    _validate_semantics(graph)
    return {
        "actual_build_captured": False,
        "build_authorized": False,
        "execution_authorized": False,
        "graph_bytes": len(raw),
        "graph_sha256": observed_sha256,
        "promotion_eligible": False,
        "scientific_evidence": False,
        "status": "pass-structural-design-only",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_graph = Path(__file__).resolve().parents[2] / DEFAULT_GRAPH
    parser.add_argument("--graph", type=Path, default=default_graph)
    arguments = parser.parse_args(argv)
    try:
        verdict = verify_graph(arguments.graph)
    except VerificationError as error:
        print(f"physics-superbuild-graph-v1: reject: {error}", file=sys.stderr)
        return 2
    print(json.dumps(verdict, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
