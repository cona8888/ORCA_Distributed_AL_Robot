#!/usr/bin/env python3
"""Pure static checks for the structural-only four-product CMake scaffold."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
GRAPH_PATH = REPO_ROOT / "comparison/physics-superbuild-v1/source_graph_v1.json"
CMAKE_PATH = REPO_ROOT / "comparison/physics-superbuild-v1/CMakeLists.txt"
EXPECTED_GRAPH_BYTES = 14_455
EXPECTED_GRAPH_SHA256 = "3df23a2292833277e707d46d961d640928653317664206171787e590491e7c8d"
EXPECTED_CMAKE_SHA256 = "185bcb21b34e27952f7395c8b8aaeb32c83493380b395b7e10226b9cf5988a91"


class StaticContractError(RuntimeError):
    """Raised when the scaffold text diverges from the committed graph."""


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def quoted_set(text: str, name: str) -> list:
    pattern = r"(?ms)^set\(" + re.escape(name) + r"\s*\n(.*?)^\)"
    match = re.search(pattern, text)
    if match is None:
        raise StaticContractError("missing multiline set: " + name)
    body = match.group(1)
    values = re.findall(r'^\s*"([^"]*)"\s*$', body, flags=re.MULTILINE)
    if not values or re.sub(r'^\s*"[^"]*"\s*$', "", body, flags=re.MULTILINE).strip():
        raise StaticContractError("nonliteral list body: " + name)
    return values


def normalized(value: str) -> str:
    return " ".join(value.split())


def require_fragment(text: str, fragment: str, label: str) -> None:
    if normalized(fragment) not in normalized(text):
        raise StaticContractError("missing exact " + label)


def calls(text: str, command: str) -> list:
    found = []
    start_pattern = re.compile(r"(?m)^\s*" + re.escape(command) + r"\s*\(")
    offset = 0
    while True:
        match = start_pattern.search(text, offset)
        if match is None:
            break
        depth = 0
        quote = False
        escape = False
        end = None
        for index in range(match.end() - 1, len(text)):
            character = text[index]
            if escape:
                escape = False
                continue
            if character == "\\" and quote:
                escape = True
                continue
            if character == '"':
                quote = not quote
                continue
            if quote:
                continue
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    end = index
                    break
        if end is None:
            raise StaticContractError("unterminated command call: " + command)
        found.append(text[match.end():end])
        offset = end + 1
    return found


def expected_products(graph: dict) -> list:
    records = {record["key"]: record for record in graph["products"]["records"]}
    common_roles = [
        "common:" + item["role"]
        for item in graph["literal_common_objects"]["roles"]
    ]
    observer_roles = graph["observer_partition"]["observer_object_order"]
    result = []
    for key in graph["products"]["order"]:
        record = records[key]
        method = record["method"]
        role = record["role"]
        equation = common_roles + ["aggregate:" + method]
        if role == "fidelity":
            equation.extend(
                "observer:" + method + ":" + observer_role
                for observer_role in observer_roles
            )
        if record["object_equation"] != equation:
            raise StaticContractError("graph product equation is internally inconsistent: " + key)
        observers = (
            "${DANCERS_" + method + "_FIDELITY_OBJECTS}"
            if role == "fidelity"
            else ""
        )
        result.append([
            key,
            method,
            role,
            record["output_name"],
            "${DANCERS_" + method + "_AGGREGATE}",
            observers,
        ])
    return result


def validate_scaffold(text: str, graph: dict) -> None:
    common = graph["literal_common_objects"]
    method = graph["method_controller_aggregate"]
    observers = graph["observer_partition"]

    if quoted_set(text, "DANCERS_GRAPH_COMMON_ROLES") != [
        item["role"] for item in common["roles"]
    ]:
        raise StaticContractError("common role order differs from graph")
    if quoted_set(text, "DANCERS_GRAPH_COMMON_SOURCES") != [
        item["source"] for item in common["roles"]
    ]:
        raise StaticContractError("common source order differs from graph")
    if quoted_set(text, "DANCERS_GRAPH_COMMON_OBJECT_NAMES") != [
        item["object_name"] for item in common["roles"]
    ]:
        raise StaticContractError("common object-name order differs from graph")
    if quoted_set(text, "DANCERS_GRAPH_METHOD_SOURCES") != method[
        "ordered_contributor_sources"
    ]:
        raise StaticContractError("14+2 method contributor order differs from graph")
    method_sources = quoted_set(text, "DANCERS_GRAPH_METHOD_SOURCES")
    core_count = method["controller_core_count"]
    if method_sources[:core_count] != method["controller_core_sources"]:
        raise StaticContractError("14 method core sources differ from graph")
    if method_sources[core_count:] != method["dispatch_sources"]:
        raise StaticContractError("two method dispatch sources differ from graph")
    if quoted_set(text, "DANCERS_GRAPH_FIDELITY_SOURCES") != observers[
        "fidelity_sources_per_method"
    ]:
        raise StaticContractError("fidelity source order differs from graph")
    if quoted_set(text, "DANCERS_GRAPH_FIDELITY_ROLES") != observers[
        "observer_object_order"
    ]:
        raise StaticContractError("fidelity role order differs from graph")
    if len(quoted_set(text, "DANCERS_GRAPH_FIDELITY_SOURCES")) != observers[
        "fidelity_observer_count_per_method"
    ]:
        raise StaticContractError("fidelity observer count differs from graph")
    if observers["timing_observer_count"] != 0 or observers["timing_observer_sources"]:
        raise StaticContractError("graph timing observer partition is not empty")
    if quoted_set(text, "DANCERS_GRAPH_PRODUCT_ORDER") != graph["products"]["order"]:
        raise StaticContractError("product order differs from graph")

    required_scalar_fragments = (
        "set(DANCERS_GRAPH_BYTES 14455)",
        "set(DANCERS_GRAPH_SHA256 \"" + EXPECTED_GRAPH_SHA256 + "\")",
        "set(DANCERS_SUPERBUILD_STRUCTURAL_DESIGN_ONLY TRUE)",
        "set(DANCERS_SUPERBUILD_BUILD_AUTHORIZED FALSE)",
        "set(DANCERS_SUPERBUILD_EXECUTION_AUTHORIZED FALSE)",
        "set(DANCERS_SUPERBUILD_SCIENTIFIC_EVIDENCE FALSE)",
    )
    for fragment in required_scalar_fragments:
        require_fragment(text, fragment, "identity/classification scalar")

    forbidden_fragments = (
        "file(GLOB",
        "GLOB_RECURSE",
        "aux_source_directory",
        "execute_process(",
        "try_run(",
        "ExternalProject",
        "GIT_REPOSITORY",
        "string(FORMAT",
        "http://",
        "https://",
        "DANCERS_SUPERBUILD_BUILD_AUTHORIZED TRUE",
        "DANCERS_SUPERBUILD_EXECUTION_AUTHORIZED TRUE",
        "DANCERS_SUPERBUILD_SCIENTIFIC_EVIDENCE TRUE",
    )
    for fragment in forbidden_fragments:
        if fragment.lower() in text.lower():
            raise StaticContractError("forbidden scaffold surface: " + fragment)

    for root_name, digest_name, label in (
        (
            "DANCERS_AUTHENTICATED_COMMON_SOURCE_ROOT",
            "DANCERS_AUTHENTICATED_COMMON_SOURCE_TREE_SHA256",
            "common-source root",
        ),
        (
            "DANCERS_AUTHENTICATED_OPTIMIZED_SOURCE_ROOT",
            "DANCERS_AUTHENTICATED_OPTIMIZED_SOURCE_TREE_SHA256",
            "optimized-source root",
        ),
        (
            "DANCERS_AUTHENTICATED_REFERENCE_SOURCE_ROOT",
            "DANCERS_AUTHENTICATED_REFERENCE_SOURCE_TREE_SHA256",
            "reference-source root",
        ),
        (
            "DANCERS_AUTHENTICATED_RUNTIME_ARTIFACT_ROOT",
            "DANCERS_AUTHENTICATED_RUNTIME_ARTIFACT_TREE_SHA256",
            "runtime-artifact root",
        ),
    ):
        require_fragment(
            text,
            "dancers_require_authenticated_root(\n"
            "    " + root_name + "\n"
            "    " + digest_name + "\n"
            "    \"" + label + "\")",
            label + " authentication",
        )
    require_fragment(
        text,
        "get_filename_component(canonical_root \"${${root_variable}}\" REALPATH)",
        "canonical root resolution",
    )
    require_fragment(
        text,
        "if(NOT canonical_root STREQUAL \"${${root_variable}}\")",
        "root alias rejection",
    )
    require_fragment(
        text,
        "runtime-artifact root must not be a method source root",
        "runtime root separation",
    )

    require_fragment(
        text,
        "set(DANCERS_AUTHENTICATED_RELOCATABLE_LINKER \"\" CACHE FILEPATH",
        "authenticated linker input",
    )
    require_fragment(
        text,
        "set(DANCERS_AUTHENTICATED_RELOCATABLE_LINKER_SHA256 \"\" CACHE STRING",
        "authenticated linker digest input",
    )
    require_fragment(
        text,
        "file(SHA256 \"${DANCERS_CANONICAL_RELOCATABLE_LINKER}\" "
        "DANCERS_OBSERVED_RELOCATABLE_LINKER_SHA256)",
        "linker byte authentication",
    )
    require_fragment(
        text,
        "COMMAND \"${DANCERS_CANONICAL_RELOCATABLE_LINKER}\" "
        "-r -o \"${aggregate}\" ${ordered_objects}",
        "ordered relocatable-link argv",
    )
    require_fragment(text, "COMMAND_EXPAND_LISTS\n        VERBATIM", "literal argv handling")
    require_fragment(
        text,
        "if(NOT index EQUAL 16)",
        "exact 14+2 method object count",
    )
    require_fragment(
        text,
        "set(ordinal \"${index}\")",
        "ordered method object ordinal",
    )
    require_fragment(
        text,
        "if(NOT index EQUAL 5)",
        "exact fidelity object count",
    )
    require_fragment(
        text,
        "set(product_objects ${DANCERS_COMMON_OBJECTS} \"${aggregate}\")",
        "common-plus-aggregate product base",
    )
    require_fragment(
        text,
        "add_library(${target} OBJECT \"${source_root}/${relative_source}\")",
        "one-source object target",
    )
    require_fragment(
        text,
        "set_property(TARGET ${target} PROPERTY DANCERS_GRAPH_OBJECT_NAME "
        "\"${graph_object_name}\")",
        "exact common object-name binding",
    )
    require_fragment(
        text,
        "if(role STREQUAL \"timing\" AND NOT \"${observer_objects}\" STREQUAL \"\")",
        "timing observer rejection",
    )
    require_fragment(
        text,
        "list(APPEND product_objects ${observer_objects})",
        "fidelity observer append",
    )
    if text.count("PROJECT_ROOT=") != 1:
        raise StaticContractError("runtime artifact root must be defined once")

    exact_dependency_order = """
        target_link_libraries(dancers_superbuild_link_policy INTERFACE
            Eigen3::Eigen
            dancers_superbuild_manif_dependency
            OpenMP::OpenMP_CXX
            dancers_superbuild_raylib_dependency
        )
    """
    require_fragment(text, exact_dependency_order, "four-product dependency order")
    require_fragment(
        text,
        "foreach(dependency IN ITEMS DANCERS_SUPERBUILD_MANIF DANCERS_SUPERBUILD_RAYLIB)",
        "dependency source-override rejection",
    )
    for required_flag in (
        "-pthread",
        "-fno-fast-math",
        "-ffp-contract=off",
        "-frounding-math",
    ):
        if text.count(required_flag) != 1:
            raise StaticContractError("compile flag is not centralized once: " + required_flag)

    product_calls = calls(text, "dancers_add_product")
    observed_products = [re.findall(r'"([^"]*)"', call) for call in product_calls]
    if observed_products != expected_products(graph):
        raise StaticContractError("product names/order/equations differ from graph")

    require_fragment(
        text,
        "dancers_require_graph_source(\n"
        "    \"${DANCERS_AUTHENTICATED_COMMON_SOURCE_ROOT}\"\n"
        "    \"inc/SimulatorControllerPortV1.h\"",
        "opaque port blocker",
    )
    for dispatch_source in method["dispatch_sources"]:
        if dispatch_source not in quoted_set(text, "DANCERS_GRAPH_METHOD_SOURCES"):
            raise StaticContractError("missing method-resident dispatch source")
    if "Noop.cpp" in text or "timing_stub" in text:
        raise StaticContractError("timing stub source is forbidden")


class PhysicsSuperbuildCMakeV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.graph_raw = GRAPH_PATH.read_bytes()
        cls.graph = json.loads(cls.graph_raw)
        cls.cmake_raw = CMAKE_PATH.read_bytes()
        cls.cmake = cls.cmake_raw.decode("utf-8")

    def test_committed_graph_and_scaffold_identities(self) -> None:
        self.assertEqual(len(self.graph_raw), EXPECTED_GRAPH_BYTES)
        self.assertEqual(sha256_bytes(self.graph_raw), EXPECTED_GRAPH_SHA256)
        self.assertEqual(sha256_bytes(self.cmake_raw), EXPECTED_CMAKE_SHA256)

    def test_exact_graph_inventory_and_product_equations(self) -> None:
        validate_scaffold(self.cmake, self.graph)

    def test_common_method_observer_and_parity_structure(self) -> None:
        self.assertEqual(self.cmake.count("dancers_add_method_partition(\n"), 2)
        self.assertEqual(self.cmake.count("dancers_add_fidelity_partition(\n"), 2)
        self.assertEqual(self.cmake.count("add_executable(${output_name}"), 1)
        self.assertEqual(self.cmake.count("add_custom_command(\n"), 1)
        self.assertIn("private_contributors_linked_into_products", json.dumps(self.graph))
        self.assertNotIn("${ordered_objects}", "".join(calls(self.cmake, "dancers_add_product")))

    def test_non_authorizing_and_no_active_capability_surface(self) -> None:
        validate_scaffold(self.cmake, self.graph)
        self.assertNotIn("enable_testing", self.cmake.lower())
        self.assertNotIn("add_test(", self.cmake.lower())
        self.assertNotIn("install(", self.cmake.lower())
        self.assertNotIn("ctest", self.cmake.lower())

    def test_direct_semantic_mutations_reject(self) -> None:
        mutations = (
            (
                "src/physics/PlanarPhysics.cpp",
                "src/physics/PlanarPhysicsAlternate.cpp",
            ),
            ("src/gbp/Variable.cpp", "src/gbp/VariableMoved.cpp"),
            ("src/ComparisonHooksDispatch.cpp", "src/ComparisonHooksNoop.cpp"),
            ("src/CollisionMetrics.cpp", "src/CollisionMetricsAlternate.cpp"),
            ("optimized-fidelity\"\n    \"optimized-timing", "optimized-timing\"\n    \"optimized-fidelity"),
            ("dancers-reference-timing", "dancers-reference-timing-v2"),
            ("-r -o \"${aggregate}\"", "-o \"${aggregate}\" -r"),
            ("DANCERS_SUPERBUILD_BUILD_AUTHORIZED FALSE", "DANCERS_SUPERBUILD_BUILD_AUTHORIZED TRUE"),
            ("inc/SimulatorControllerPortV1.h", "inc/Simulator.h"),
            ("${DANCERS_reference_FIDELITY_OBJECTS}", ""),
        )
        for old, new in mutations:
            with self.subTest(old=old, new=new):
                self.assertIn(old, self.cmake)
                mutated = self.cmake.replace(old, new, 1)
                with self.assertRaises(StaticContractError):
                    validate_scaffold(mutated, self.graph)


if __name__ == "__main__":
    unittest.main()
