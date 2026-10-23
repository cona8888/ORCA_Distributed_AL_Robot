#!/usr/bin/env python3
"""Synthetic, fault, and capability tests for the runtime rebuild builder."""

from __future__ import annotations

from contextlib import redirect_stderr
import hashlib
import importlib.util
import io
import os
from pathlib import Path
import shutil
import subprocess
import struct
import sys
import tarfile
import tempfile
import time
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/comparison/build_headless_runtime_artifacts_v1.py"
SPEC = importlib.util.spec_from_file_location("build_headless_runtime_artifacts_v1", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
B = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = B
SPEC.loader.exec_module(B)

FIXTURE_PATH = Path(__file__).with_name("_headless_runtime_build_v1_fixture.py")
FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "headless_runtime_build_v1_fixture_for_builder", FIXTURE_PATH
)
assert FIXTURE_SPEC is not None and FIXTURE_SPEC.loader is not None
F = importlib.util.module_from_spec(FIXTURE_SPEC)
FIXTURE_SPEC.loader.exec_module(F)


def git_bytes(*args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=str(ROOT), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    ).stdout


class RuntimeBuildBuilderTests(unittest.TestCase):
    def test_01_design_and_input_receipts_are_exact_and_nonauthorizing(self) -> None:
        design, raw = B.load_design(ROOT)
        self.assertEqual(len(raw), B.DESIGN_BYTES)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), B.DESIGN_SHA256)
        self.assertEqual(design["schema_version"], B.DESIGN_SCHEMA)
        self.assertTrue(design["classification"]["host_bound_engineering_design_only"])
        self.assertEqual(design["build"]["data_in_code_policy"], B.DATA_IN_CODE_POLICY)
        self.assertEqual(
            design["build"]["chained_fixups_policy"], B.CHAINED_FIXUPS_POLICY
        )
        self.assertEqual(
            design["build"]["shim_exec_segment_policy"],
            B.SHIM_EXEC_SEGMENT_POLICY,
        )
        self.assertEqual(
            design["build"]["compiler_driver_diagnostic_policy"],
            "pass exactly one -Qunused-arguments to every configured C/C++ compile "
            "action and the direct shim compile/link action; retain -Werror and "
            "suppress only driver-level unused-argument diagnostics caused by the "
            "authenticated global --no-default-config and --ld-path inputs",
        )
        self.assertEqual(design["build"]["cmake_arguments"][-2:], [
            "-DCMAKE_C_FLAGS=--no-default-config -Qunused-arguments "
            "--ld-path=<recorded-Pixi-ld-entry> "
            "-ffile-prefix-map=<private-work>=/dancers/<method>-source "
            "-fdebug-prefix-map=<private-work>=/dancers/<method>-source",
            "-DCMAKE_CXX_FLAGS=--no-default-config -Qunused-arguments "
            "--ld-path=<recorded-Pixi-ld-entry> "
            "-ffile-prefix-map=<private-work>=/dancers/<method>-source "
            "-fdebug-prefix-map=<private-work>=/dancers/<method>-source",
        ])
        for key, expected in B.SCIENCE_FLAGS.items():
            self.assertIs(design["classification"][key], expected)
        self.assertEqual(design["operations"], {
            "build_invoked": False, "configure_invoked": False,
            "ctest_invoked": False,
            "descriptor_test_product_invoked": False,
            "runtime_product_invoked": False,
            "shim_compile_invoked": False,
            "shim_product_invoked": False,
            **B.OPERATIONS,
        })
        identities = B.authenticate_repository_inputs(ROOT)
        self.assertEqual(set(identities), set(B.INPUT_IDENTITIES))

    def test_02_selected_cmake_patch_is_the_exact_three_hunk_receipt(self) -> None:
        actual = git_bytes(
            "diff", "--no-ext-diff", "--binary", "80ca273^", "80ca273", "--", "CMakeLists.txt"
        ).split(b"@@ -468", 1)[0]
        patch = (ROOT / B.INPUT_IDENTITIES["cmake_patch"][0]).read_bytes()
        self.assertEqual(len(actual), 4_933)
        self.assertEqual(hashlib.sha256(actual).hexdigest(), B.INPUT_IDENTITIES["cmake_patch"][2])
        self.assertEqual(patch, actual)
        self.assertEqual(sum(line.startswith(b"@@ ") for line in patch.splitlines()), 3)

    def test_03_both_endpoints_accept_identical_overlays(self) -> None:
        cmake_patch = (ROOT / B.INPUT_IDENTITIES["cmake_patch"][0]).read_bytes()
        probe_patch = (ROOT / B.INPUT_IDENTITIES["probe_patch"][0]).read_bytes()
        descriptor_patch = (ROOT / B.INPUT_IDENTITIES["descriptor_patch"][0]).read_bytes()
        for endpoint in B.ENDPOINTS.values():
            with self.subTest(endpoint=endpoint["applied_head"]):
                cmake = git_bytes("show", f'{endpoint["applied_head"]}:CMakeLists.txt')
                main = git_bytes("show", f'{endpoint["applied_head"]}:src/main.cpp')
                self.assertEqual((len(cmake), B._sha256(cmake)), B.EXPECTED_CMAKE_PRE)
                self.assertEqual((len(main), B._sha256(main)), B.EXPECTED_MAIN_PRE)
                self.assertEqual(
                    (len(B.apply_context_patch(cmake, cmake_patch, "test")), B._sha256(B.apply_context_patch(cmake, cmake_patch, "test"))),
                    B.EXPECTED_CMAKE_POST,
                )
                self.assertEqual(
                    (len(B.apply_probe_patch(main, probe_patch)), B._sha256(B.apply_probe_patch(main, probe_patch))),
                    B.EXPECTED_MAIN_POST,
                )
        patched = B.apply_probe_patch(
            git_bytes("show", f'{B.ENDPOINTS["reference"]["applied_head"]}:src/main.cpp'),
            probe_patch,
        )
        probe_offset = patched.index(b"DANCERS_RUNTIME_ROOT_PROBE")
        self.assertLess(probe_offset, patched.index(b"OMP_WAIT_POLICY"))
        self.assertLess(probe_offset, patched.index(b"DArgs::DArgs"))
        self.assertLess(probe_offset, patched.index(b"Simulator* sim = nullptr"))
        records = B.parse_authenticated_git_patch(descriptor_patch, "test descriptor patch")
        self.assertEqual([item["path"] for item in records], list(B.EXPECTED_DESCRIPTOR_PATHS))
        self.assertEqual(len(descriptor_patch), 108_053)
        self.assertEqual(
            B._sha256(descriptor_patch),
            "1c3ce61e0c5b4753d0bb7382209a328fa252bb63c2ae38d2251e1432ff313323",
        )

    def test_04_git_tree_proofs_bind_exact_heads_and_trees(self) -> None:
        expected = {
            "reference": (521, "05d4a1546364ff64b1bcfdacc314cc20a7661267190e30e20b1fb8ac1d533bf3"),
            "optimized": (523, "1a5c99d30bf367065c210f20adbb30b8bd373795eea5ff31bb65d40ec548fbd1"),
        }
        for method, endpoint in B.ENDPOINTS.items():
            tree = git_bytes("rev-parse", f'{endpoint["applied_head"]}^{{tree}}').decode().strip()
            entries = B._parse_ls_tree(git_bytes("ls-tree", "-rz", "-r", "--full-tree", endpoint["applied_head"]))
            self.assertEqual(tree, endpoint["tree"])
            self.assertEqual((len(entries), B._tree_digest(entries)), expected[method])
        design, _ = B.load_design(ROOT)
        inputs = {key: B._read_bound_input(ROOT, key) for key in B.INPUT_IDENTITIES}
        receipts = B.authenticate_git_design_receipts(ROOT, design, inputs)
        self.assertTrue(receipts["design_base_tree_verified"])
        self.assertTrue(receipts["endpoint_cmake_and_main_bytes_identical"])
        self.assertEqual(receipts["cross_method_path_count"], 12)

    def test_05_archive_extractor_rejects_traversal_symlink_and_blob_faults(self) -> None:
        payload = b"payload\n"
        good_entry = [{"mode": "100644", "oid": B._git_blob_oid(payload), "path": "safe.txt"}]

        def archive(name: str, *, kind: str = "file", raw: bytes = payload) -> bytes:
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode="w") as value:
                info = tarfile.TarInfo(name)
                if kind == "symlink":
                    info.type = tarfile.SYMTYPE
                    info.linkname = "safe.txt"
                    value.addfile(info)
                else:
                    info.size = len(raw)
                    value.addfile(info, io.BytesIO(raw))
            return stream.getvalue()

        with tempfile.TemporaryDirectory(prefix="runtime-archive-fault-") as name:
            base = Path(name)
            for label, raw, entries in (
                ("traversal", archive("../escape"), good_entry),
                ("symlink", archive("safe.txt", kind="symlink"), good_entry),
                ("blob", archive("safe.txt", raw=b"wrong\n"), good_entry),
            ):
                destination = base / label
                destination.mkdir()
                with self.subTest(label=label), self.assertRaises(B.BuildError):
                    B._safe_extract_git_archive(raw, destination, entries)

    def test_06_public_surface_requires_explicit_gate_and_never_spawns_without_it(self) -> None:
        stderr = io.StringIO()
        with mock.patch.object(B, "build_repository") as build, redirect_stderr(stderr):
            self.assertEqual(B.main([]), 2)
        build.assert_not_called()
        self.assertIn("--execute-engineering-build", stderr.getvalue())

    def test_07_plan_and_result_paths_are_categorically_rejected(self) -> None:
        for value in (
            "comparison/plans/simulator-demo/v1/headless-epoch-1",
            "results/simulator-demo/v1/headless-scientific/epoch-1",
            "safe/results/file",
        ):
            with self.subTest(path=value), self.assertRaises(B.BuildError):
                B._safe_relative(value, "test")
        self.assertEqual(B.BUILD_PREFIX.as_posix(), "build/comparison/headless-runtime-v1")

    def test_08_failure_cleanup_removes_even_read_only_private_seal(self) -> None:
        with tempfile.TemporaryDirectory(prefix="runtime-cleanup-") as name:
            staging = Path(name) / ".staging"
            nested = staging / "nested"
            nested.mkdir(parents=True)
            seal = nested / "BUILD_COMPLETE.seal.json"
            seal.write_text("sentinel\n", encoding="utf-8")
            os.chmod(seal, 0o444)
            os.chmod(nested, 0o555)
            os.chmod(staging, 0o555)
            B._remove_private_tree(staging)
            self.assertFalse(staging.exists())

    def test_09_builder_source_has_no_shell_or_official_namespace_default(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)
        self.assertNotIn("os.system", source)
        self.assertNotIn("comparison/plans/", source)
        self.assertNotIn("comparison/results/", source)

    def test_10_offline_archives_prepopulate_exact_fully_disconnected_sources(self) -> None:
        for dependency, expected in B.DEPENDENCY_SOURCE_PROOFS.items():
            with self.subTest(dependency=dependency), tempfile.TemporaryDirectory(
                prefix=f"runtime-{dependency}-source-"
            ) as name:
                receipt = B.prepopulate_dependency_archive(
                    B._read_bound_input(ROOT, expected["archive_key"]),
                    expected["archive_root"],
                    Path(name) / "source",
                )
                self.assertEqual(receipt, {
                    key: expected[key]
                    for key in ("file_count", "regular_bytes", "tree_sha256")
                })

    def test_11_real_git_archives_reconstruct_both_exact_overlay_trees_without_building(self) -> None:
        inputs = {key: B._read_bound_input(ROOT, key) for key in B.INPUT_IDENTITIES}
        with tempfile.TemporaryDirectory(prefix="runtime-source-proof-") as name:
            for method, endpoint in B.ENDPOINTS.items():
                with self.subTest(method=method):
                    receipt = B.materialize_source_snapshot(
                        ROOT, Path(name) / method, method, inputs
                    )
                    self.assertEqual(receipt["endpoint_tree"], endpoint["tree"])
                    self.assertEqual(
                        receipt["overlay_paths"],
                        sorted({
                            "CMakeLists.txt", "src/main.cpp",
                            *[item[1] for item in B.ARCHIVE_OVERLAYS],
                            *B.EXPECTED_DESCRIPTOR_PATHS,
                        }),
                    )
                    self.assertEqual(
                        receipt["descriptor_post_identities"],
                        B._descriptor_post_identity_list(method),
                    )
                    self.assertEqual(
                        (
                            receipt["post_overlay_file_count"],
                            receipt["post_overlay_tree_sha256"],
                        ),
                        B.EXPECTED_FINAL_OVERLAY_PROOFS[method],
                    )

    def test_12_dynamic_closure_parser_rejects_non_system_inputs_and_wrong_rpath(self) -> None:
        binary = Path("/private/build/dancers")
        valid = (
            f"{binary}:\n"
            "\t/System/Library/Frameworks/Cocoa.framework/Versions/A/Cocoa (compatibility version 1.0.0, current version 1.0.0)\n"
            "\t/usr/lib/libSystem.B.dylib (compatibility version 1.0.0, current version 1.0.0)\n"
        ).encode("utf-8")
        self.assertEqual(len(B._parse_otool_dependencies(valid, binary)), 2)
        with self.assertRaisesRegex(B.BuildError, "non-system"):
            B._parse_otool_dependencies(
                valid.replace(b"/usr/lib/libSystem.B.dylib", b"/private/untrusted.dylib"),
                binary,
            )
        root = Path("/canonical/repository/.pixi/envs/default/lib")
        load = (
            f"{binary}:\nLoad command 1\n          cmd LC_RPATH\n"
            f"      cmdsize 88\n         path {root} (offset 12)\n"
        ).encode("utf-8")
        self.assertEqual(B._parse_otool_rpaths(load, binary, root), [str(root)])
        with self.assertRaisesRegex(B.BuildError, "runtime search path"):
            B._parse_otool_rpaths(load, binary, None)

    def test_13_ambient_overrides_are_rejected_and_never_inherited(self) -> None:
        for key in (
            "CPATH", "LIBRARY_PATH", "DYLD_INSERT_LIBRARIES", "GIT_DIR",
            "GIT_CONFIG_GLOBAL", "GIT_CONFIG_KEY_0", "CMAKE_TOOLCHAIN_FILE",
            "SCCACHE_DIR", "CLANG_CONFIG_FILE", "XCRUN_TOOLCHAIN_NAME",
        ):
            with self.subTest(key=key), self.assertRaisesRegex(
                B.BuildError, "ambient compiler/linker/loader/Git override"
            ):
                B._reject_ambient_overrides({key: "/poison"})
        with mock.patch.dict(os.environ, {"HEADLESS_RUNTIME_POISON": "present"}):
            minimal = B._git_env(ROOT)
        self.assertNotIn("HEADLESS_RUNTIME_POISON", minimal)
        self.assertEqual(
            set(minimal),
            {
                "GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_SYSTEM",
                "GIT_NO_LAZY_FETCH", "GIT_OPTIONAL_LOCKS", "GIT_PAGER",
                "GIT_TERMINAL_PROMPT", "HOME",
                "LANG", "LC_ALL", "LOGNAME", "PATH",
                "PIXI_PROJECT_ROOT", "TMPDIR", "USER",
            },
        )
        self.assertEqual(minimal["GIT_NO_LAZY_FETCH"], "1")

    def test_14_quiescent_process_and_source_drift_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="runtime-use-custody-") as name:
            base = Path(name)
            executable = base / "tool"
            executable.write_bytes(b"#!/bin/sh\nprintf 'ok\\n'\n")
            os.chmod(executable, 0o700)
            guard = B._capture_file_guard(executable, B.MAX_BINARY, "synthetic tool")
            mutated = False

            def mutate_tool(process: subprocess.Popen[bytes]) -> None:
                nonlocal mutated
                if not mutated:
                    executable.write_bytes(b"mutated-tool")
                    mutated = True

            with mock.patch.object(B, "_supervisor_tick_hook", side_effect=mutate_tool):
                with self.assertRaisesRegex(B.BuildError, "custody"):
                    B._run_process(
                        [str(executable), "ok"], cwd=base, env={}, timeout=1,
                        executable_guard=guard,
                    )

            source = base / "source"
            source.mkdir()
            tracked = source / "input.cpp"
            tracked.write_bytes(b"original\n")
            inventory = B._filesystem_inventory(source)
            tracked.write_bytes(b"mutated\n")
            with self.assertRaisesRegex(B.BuildError, "changed across a build use boundary"):
                B._assert_inventory(source, inventory, "synthetic source")

    def test_15_no_replace_publication_rolls_back_post_rename_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="runtime-publication-") as name:
            parent = Path(name)
            staging_name = ".staging-test"
            final_name = "final"
            staging = parent / staging_name
            staging.mkdir()
            (staging / "BUILD_COMPLETE.seal.json").write_bytes(b"sealed\n")
            fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                expected = B._entry_fingerprint(fd, staging_name)
                self.assertIsNotNone(expected)
                with mock.patch.object(
                    B, "_fsync_directory_fd",
                    side_effect=[OSError("post-rename fsync fault"), None, None],
                ):
                    with self.assertRaises(OSError):
                        B._publish_custodied_tree(
                            fd, parent, staging_name, final_name, expected
                        )
                self.assertFalse((parent / final_name).exists())
                self.assertFalse(staging.exists())
            finally:
                os.close(fd)

    def test_16_rename_effect_is_reconciled_even_when_wrapper_raises(self) -> None:
        with tempfile.TemporaryDirectory(prefix="runtime-rename-effect-") as name:
            parent = Path(name)
            staging_name = ".staging-test"
            final_name = "final"
            staging = parent / staging_name
            staging.mkdir()
            (staging / "BUILD_COMPLETE.seal.json").write_bytes(b"sealed\n")
            fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            real_rename = B._rename_no_replace

            def move_then_interrupt(parent_fd: int, source: str, destination: str) -> None:
                real_rename(parent_fd, source, destination)
                raise KeyboardInterrupt("after rename effect")

            try:
                expected = B._entry_fingerprint(fd, staging_name)
                self.assertIsNotNone(expected)
                with mock.patch.object(B, "_rename_no_replace", side_effect=move_then_interrupt):
                    with self.assertRaises(B.BuildError):
                        B._publish_custodied_tree(
                            fd, parent, staging_name, final_name, expected
                        )
                self.assertFalse((parent / final_name).exists())
                self.assertFalse(staging.exists())
            finally:
                os.close(fd)

    def test_17_no_replace_publication_preserves_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory(prefix="runtime-no-replace-") as name:
            parent = Path(name)
            (parent / "staging").mkdir()
            (parent / "final").mkdir()
            marker = parent / "final/owner.txt"
            marker.write_bytes(b"existing winner\n")
            fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                with self.assertRaises(OSError):
                    B._rename_no_replace(fd, "staging", "final")
                self.assertEqual(marker.read_bytes(), b"existing winner\n")
                self.assertTrue((parent / "staging").is_dir())
            finally:
                os.close(fd)

    def test_18_descriptor_layer_supports_authenticated_new_file_hunks(self) -> None:
        old = b"old\n"
        changed = b"changed\n"
        added = b"added\n"
        patch = (
            b"diff --git a/existing.txt b/existing.txt\n"
            + f"index {B._git_blob_oid(old)}..{B._git_blob_oid(changed)} 100644\n".encode()
            + b"--- a/existing.txt\n+++ b/existing.txt\n"
            + b"@@ -1 +1 @@\n-old\n+changed\n"
            + b"diff --git a/new.txt b/new.txt\nnew file mode 100644\n"
            + f"index {'0' * 40}..{B._git_blob_oid(added)}\n".encode()
            + b"--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+added\n"
        )
        with tempfile.TemporaryDirectory(prefix="descriptor-layer-") as name:
            destination = Path(name)
            (destination / "existing.txt").write_bytes(old)
            receipt = B.apply_descriptor_patch_layer(
                destination, "reference", patch, {}, b"unused"
            )
            self.assertEqual(receipt["path_count"], 2)
            self.assertEqual((destination / "existing.txt").read_bytes(), changed)
            self.assertEqual((destination / "new.txt").read_bytes(), added)
        traversal = patch.replace(b"a/existing.txt", b"a/../escape", 1)
        with self.assertRaises(B.BuildError):
            B.parse_authenticated_git_patch(traversal, "fault patch")

    def test_19_final_descriptor_overlays_preserve_exact_method_boundary(self) -> None:
        inputs = {key: B._read_bound_input(ROOT, key) for key in B.INPUT_IDENTITIES}
        with tempfile.TemporaryDirectory(prefix="descriptor-boundary-") as name:
            inventories = {}
            for method in B.ENDPOINTS:
                destination = Path(name) / method
                B.materialize_source_snapshot(ROOT, destination, method, inputs)
                inventories[method] = B._filesystem_inventory(destination)
            changed = sorted(
                path
                for path in set(inventories["reference"]) | set(inventories["optimized"])
                if inventories["reference"].get(path) != inventories["optimized"].get(path)
            )
        self.assertEqual(changed, sorted(B.HISTORICAL_DIFFERENCE_PATHS))

    def test_20_shim_compile_argv_environment_and_fd_contract_are_exact(self) -> None:
        root = Path("/private/repository")
        home = Path("/private/build/work/home")
        clang = Path("/private/pixi/bin/clang")
        linker = Path("/private/pixi/bin/ld")
        sdk = Path("/Applications/Xcode.app/SDK")
        source = Path("/private/build/work/shim-pass-1/source/headless_darwin_spawn_shim_v1.c")
        output = Path("/private/build/work/shim-pass-1/output/headless-darwin-spawn-shim-v1")
        self.assertEqual(B._shim_compile_argv(clang, linker, sdk, source, output), [
            str(clang), f"--ld-path={linker}", "-isysroot", str(sdk),
            "--no-default-config", "-Qunused-arguments", "-arch", "arm64",
            "-mmacosx-version-min=26.0", "-std=c17", "-Os", "-Wall",
            "-Wextra", "-Werror", "-pedantic", "-fno-common",
            "-fstack-protector-strong", str(source), "-Wl,-dead_strip",
            "-o", str(output),
        ])
        with mock.patch.dict(os.environ, {"CPATH": "/poison"}):
            env = B._shim_build_env(root, home, clang, linker)
        self.assertNotIn("CPATH", env)
        self.assertEqual(set(env), {
            "CC", "CLANG_NO_DEFAULT_CONFIG", "HOME", "LANG", "LC_ALL", "LD", "LOGNAME",
            "MACOSX_DEPLOYMENT_TARGET", "PATH", "PIXI_PROJECT_ROOT",
            "SOURCE_DATE_EPOCH", "TMPDIR", "USER", "ZERO_AR_DATE",
        })
        self.assertNotIn("target_simulator", B.SHIM_DESCRIPTOR_FDS)
        self.assertNotIn("shim_executable", B.SHIM_DESCRIPTOR_FDS)
        self.assertEqual(B.PARENT_SIMULATOR_CUSTODY_FD, 247)
        self.assertTrue(B.SHIM_LAUNCH_CONTRACT["dev_fd_execution_forbidden"])
        self.assertTrue(
            B.SHIM_LAUNCH_CONTRACT["parent_simulator_custody_fd_reaches_shim"]
        )
        self.assertFalse(B.SHIM_LAUNCH_CONTRACT[
            "parent_simulator_custody_fd_reaches_simulator_after_setexec"
        ])
        build_env = B._build_env(
            root, home, clang, Path("/private/pixi/bin/clang++"),
            Path("/private/pixi/bin/ar"), Path("/private/pixi/bin/ranlib"), linker,
        )
        self.assertEqual(build_env["CLANG_NO_DEFAULT_CONFIG"], "1")
        self.assertNotIn("CFLAGS", build_env)
        self.assertNotIn("CXXFLAGS", build_env)

    def test_21_host_toolchain_and_runtime_library_capability_is_exact(self) -> None:
        with mock.patch.dict(os.environ, {"PIXI_PROJECT_ROOT": str(ROOT)}):
            receipt = B.authenticate_toolchain(ROOT)
        self.assertEqual(receipt["platform"], "osx-arm64")
        self.assertEqual(receipt["deployment_target"], B.DEPLOYMENT_TARGET)
        self.assertEqual(receipt["probe_outputs"]["architecture"], "arm64")
        self.assertEqual(receipt["probe_outputs"]["sdk_version"], "26.5")
        self.assertEqual(receipt["probe_outputs"]["sdk_build"], "25F70")
        self.assertEqual(
            receipt["probe_outputs"]["compiler_c_linker"],
            receipt["driver_linker_entry"]["path"],
        )
        self.assertEqual(
            receipt["driver_linker_entry"]["resolved_path"],
            receipt["tools"]["ld"]["path"],
        )
        self.assertEqual(set(receipt["tools"]), {
            "ar", "clang", "clang++", "cmake", "git", "ld", "ninja", "nm",
            "otool", "ranlib", "uname", "xcodebuild", "xcrun",
        })
        self.assertEqual(receipt["openmp_policy"], {
            "compile_activation_forbidden": True,
            "logical_threads": 1,
            "runtime_library_loaded": False,
            "source_layer": B.INPUT_IDENTITIES["single_thread_patch"][0],
            "symbol_representations_scanned": [
                "LC_SYMTAB nlist defined and undefined names",
                "LC_DYLD_INFO bind weak-bind and lazy-bind opcode names",
                "LC_DYLD_CHAINED_FIXUPS import table and complete symbol pool",
                "LC_DYLD_INFO export trie and LC_DYLD_EXPORTS_TRIE names",
            ],
            "symbol_prefixes_forbidden": list(
                B.FORBIDDEN_OPENMP_SYMBOL_PREFIXES
            ),
        })
        self.assertEqual(set(receipt["compiler_driver_entries"]), {"clang", "clang++"})
        self.assertEqual(
            receipt["compiler_driver_entries"]["clang++"]["path"],
            str(ROOT / ".pixi/envs/default/bin/clang++"),
        )

    def test_22_code_directory_cdhash_and_page_hashes_are_parsed_from_bytes(self) -> None:
        raw = F._binary(
            B, B, ROOT, "synthetic-runtime", False, False, False
        )
        receipt = B._macho_runtime_receipt(raw, ROOT)
        signature = receipt["code_signature"]
        self.assertEqual(len(signature["cdhash"]), 40)
        self.assertEqual(signature["flags"] & B.CS_ADHOC, B.CS_ADHOC)
        self.assertEqual(signature["hash_type"], 2)
        self.assertRegex(
            receipt["uuid"],
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
        )
        corrupted = bytearray(raw)
        corrupted[signature["data_offset"] - 1] ^= 1
        with self.assertRaisesRegex(B.BuildError, "code-slot hash differs"):
            B._macho_runtime_receipt(bytes(corrupted), ROOT)

    def test_23_compiler_driver_linker_symlink_swap_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="driver-linker-custody-") as name:
            base = Path(name)
            first = base / "first-ld"
            second = base / "second-ld"
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            entry = base / "ld"
            entry.symlink_to(first.name)
            guard = B._capture_symlink_guard(entry, "synthetic driver linker")
            entry.unlink()
            entry.symlink_to(second.name)
            with self.assertRaisesRegex(B.BuildError, "authenticated identity"):
                B._check_symlink_guard(guard)

    def test_24_cmake_prefix_map_flags_preserve_space_as_one_compiler_argument(self) -> None:
        work = Path("/private/work/Beyond Dancers/pass-1/reference-timing")
        atoms = B._prefix_map_flag_atoms(work, "/dancers/reference-source")
        rendered = B._cmake_flags_string(atoms)
        self.assertEqual(B.shlex.split(rendered, posix=True), list(atoms))
        self.assertIn("Beyond Dancers", atoms[0])
        self.assertNotEqual(rendered, " ".join(atoms))

    def test_25_staging_mkdir_effect_then_baseexception_is_reconciled(self) -> None:
        with tempfile.TemporaryDirectory(prefix="runtime-mkdir-effect-") as name:
            parent = Path(name)
            parent_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            state: dict[str, tuple[int, ...]] = {}
            real_mkdir = B.os.mkdir

            def mkdir_then_interrupt(
                path: object, mode: int = 0o777, *, dir_fd: object = None
            ) -> None:
                real_mkdir(path, mode=mode, dir_fd=dir_fd)
                raise KeyboardInterrupt("after mkdir effect")

            try:
                with mock.patch.object(B.os, "mkdir", side_effect=mkdir_then_interrupt):
                    with self.assertRaises(KeyboardInterrupt):
                        B._create_custodied_staging(
                            parent_fd, parent, ".staging-effect", state
                        )
                self.assertEqual(state, {})
                self.assertFalse((parent / ".staging-effect").exists())
            finally:
                os.close(parent_fd)

    def test_26_macho_rejects_path_traversal_dyld_environment_and_extra_slots(self) -> None:
        self.assertFalse(B._canonical_system_dylib_path("/usr/lib/../../private/x.dylib"))
        self.assertFalse(B._canonical_system_dylib_path("/System/Library/Frameworks/../x"))
        commands = [
            F._load_string_command(B.LC_LOAD_DYLINKER, "/usr/lib/dyld", 12),
            F._load_string_command(
                B.LC_DYLD_ENVIRONMENT, "DYLD_LIBRARY_PATH=/private", 12
            ),
            F._load_string_command(0x0000000C, "/usr/lib/libSystem.B.dylib", 24),
        ]
        with self.assertRaisesRegex(B.BuildError, "LC_DYLD_ENVIRONMENT"):
            B._parse_macho(
                F._signed_macho(B, B, commands, b"payload", "dyld-env"),
                "dyld environment fixture", B.MACHO_EXECUTE,
            )
        valid_commands = [
            F._load_string_command(0x0000000C, "/usr/lib/libSystem.B.dylib", 24),
            F._load_string_command(B.LC_LOAD_DYLINKER, "/usr/lib/dyld", 12),
        ]
        cms = struct.pack(">II", B.CSMAGIC_BLOBWRAPPER, 8)
        with self.assertRaisesRegex(B.BuildError, "slot inventory"):
            B._parse_macho(
                F._signed_macho(
                    B, B, valid_commands, b"payload", "extra-slot",
                    auxiliary_blobs=((0x10000, cms),),
                ),
                "extra slot fixture", B.MACHO_EXECUTE,
            )

    def test_27_lexical_compiler_entry_is_bound_to_target_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="compiler-entry-custody-") as name:
            base = Path(name).resolve()
            target = base / "clang-19"
            target.write_bytes(b"#!/bin/sh\nprintf 'ok\\n'\n")
            os.chmod(target, 0o700)
            entry = base / "clang++"
            entry.symlink_to(target.name)
            target_guard = B._capture_file_guard(
                target, B.MAX_BINARY, "synthetic compiler"
            )
            entry_guard = B._capture_symlink_guard(entry, "synthetic clang++ entry")
            code, stdout, stderr = B._run_process(
                [str(entry), "ok"], cwd=base, env={}, timeout=1,
                executable_guard=target_guard,
                executable_symlink_guard=entry_guard,
            )
            self.assertEqual((code, stdout, stderr), (0, b"ok\n", b""))

    def test_28_timeout_reaps_owned_child_and_grandchild_before_return(self) -> None:
        with tempfile.TemporaryDirectory(prefix="process-group-reap-") as name:
            base = Path(name)
            marker = base / "writer.log"
            child_code = (
                "import pathlib,sys,time\n"
                "path=pathlib.Path(sys.argv[1])\n"
                "for _ in range(500):\n"
                " path.open('ab').write(b'x')\n"
                " time.sleep(0.01)\n"
            )
            parent_code = (
                "import subprocess,sys,time\n"
                f"subprocess.Popen([sys.executable,'-c',{child_code!r},sys.argv[1]])\n"
                "time.sleep(30)\n"
            )
            python = Path("/usr/bin/python3")
            guard = B._capture_file_guard(
                python, B.MAX_BINARY, "synthetic process-group interpreter"
            )
            with self.assertRaisesRegex(B.BuildError, "timed out"):
                B._run_process(
                    [str(python), "-c", parent_code, str(marker)],
                    cwd=base, env={"LC_ALL": "C"}, timeout=1.0,
                    executable_guard=guard,
                )
            size = marker.stat().st_size
            self.assertGreater(size, 0)
            time.sleep(0.2)
            self.assertEqual(marker.stat().st_size, size)

    def test_29_spawn_effect_then_baseexception_adopts_and_reaps_pid_cell(self) -> None:
        with tempfile.TemporaryDirectory(prefix="spawn-effect-custody-") as name:
            base = Path(name)
            marker = base / "writer.log"
            child_code = (
                "import pathlib,sys,time\n"
                "path=pathlib.Path(sys.argv[1])\n"
                "while True:\n"
                " path.open('ab').write(b'x')\n"
                " time.sleep(0.01)\n"
            )
            parent_code = (
                "import subprocess,sys,time\n"
                f"subprocess.Popen([sys.executable,'-c',{child_code!r},sys.argv[1]])\n"
                "time.sleep(30)\n"
            )
            with mock.patch.object(
                B, "_spawn_effect_hook",
                side_effect=KeyboardInterrupt("after raw spawn effect"),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    B._run_process(
                        ["/usr/bin/python3", "-c", parent_code, str(marker)],
                        cwd=base, env={"LC_ALL": "C"}, timeout=5,
                    )
            size = marker.stat().st_size if marker.exists() else 0
            time.sleep(0.2)
            self.assertEqual(marker.stat().st_size if marker.exists() else 0, size)

    def test_30_effect_then_error_pipe_close_never_retries_reused_fd(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pipe-close-effect-") as name:
            base = Path(name)
            script = base / "quiet"
            script.write_bytes(b"#!/bin/sh\nexit 0\n")
            os.chmod(script, 0o700)
            real_pipe = B.os.pipe
            real_close = B.os.close
            stdout_read: list[int] = []
            replacement: list[int] = []

            def pipe_capture() -> tuple[int, int]:
                pair = real_pipe()
                if not stdout_read:
                    stdout_read.append(pair[0])
                return pair

            def close_effect_then_error(descriptor: int) -> None:
                if stdout_read and descriptor == stdout_read[0] and not replacement:
                    real_close(descriptor)
                    while True:
                        candidate = os.open("/dev/null", os.O_RDONLY)
                        if candidate == descriptor:
                            replacement.append(candidate)
                            break
                    raise OSError("after close effect")
                real_close(descriptor)

            try:
                with mock.patch.object(B.os, "pipe", side_effect=pipe_capture), mock.patch.object(
                    B.os, "close", side_effect=close_effect_then_error
                ):
                    with self.assertRaises(B.BuildError):
                        B._run_process(
                            [str(script)], cwd=base, env={}, timeout=2
                        )
                self.assertEqual(len(replacement), 1)
                os.fstat(replacement[0])
            finally:
                for descriptor in replacement:
                    real_close(descriptor)

    def test_31_native_process_group_census_counts_one_and_two_members(self) -> None:
        with tempfile.TemporaryDirectory(prefix="native-pgrp-census-") as name:
            base = Path(name)
            for grandchild in (False, True):
                state: dict[str, object] = {}
                code = (
                    "import subprocess,time\n"
                    + ("subprocess.Popen(['/bin/sleep','30'])\n" if grandchild else "")
                    + "time.sleep(30)\n"
                )
                process = B._spawn_owned_process(
                    ["/usr/bin/python3", "-c", code], base,
                    {"LC_ALL": "C"}, state,
                )
                try:
                    deadline = time.monotonic() + 2
                    members: set[int] = set()
                    while time.monotonic() < deadline:
                        members = B._darwin_process_group_pids(process.pid)
                        if len(members) == (2 if grandchild else 1):
                            break
                        time.sleep(0.01)
                    self.assertEqual(len(members), 2 if grandchild else 1)
                    self.assertIn(process.pid, members)
                finally:
                    B._quiesce_group_before_reap(
                        process, process.owned_fds,
                        {"stdout": bytearray(), "stderr": bytearray()}, B.MAX_TEXT,
                    )

    def test_32_macho_rejects_encryption_overlap_entry_and_openmp_closure(self) -> None:
        base_commands = [
            F._load_string_command(B.LC_LOAD_DYLINKER, "/usr/lib/dyld", 12),
            F._load_string_command(0x0C, "/usr/lib/libSystem.B.dylib", 24),
        ]
        encrypted = struct.pack(
            "<IIIIII", B.LC_ENCRYPTION_INFO_64, 24, 0, 0, 0, 0
        )
        with self.assertRaisesRegex(B.BuildError, "encrypted-image"):
            B._parse_macho(
                F._signed_macho(
                    B, B, [*base_commands, encrypted], b"payload",
                    "encrypted-fixture",
                ),
                "encrypted fixture", B.MACHO_EXECUTE,
            )

        original = bytearray(
            F._signed_macho(B, B, base_commands, b"payload", "overlap-fixture")
        )
        cursor = 32
        segment_offsets: list[int] = []
        main_offset = signature_data_offset = None
        command_count = struct.unpack_from("<I", original, 16)[0]
        for _ in range(command_count):
            command, command_bytes = struct.unpack_from("<II", original, cursor)
            if command == B.LC_SEGMENT_64:
                segment_offsets.append(cursor)
            elif command == B.LC_MAIN:
                main_offset = cursor
            elif command == B.LC_CODE_SIGNATURE:
                signature_data_offset = struct.unpack_from("<I", original, cursor + 8)[0]
            cursor += command_bytes
        self.assertEqual(len(segment_offsets), 2)
        protected_text = bytearray(original)
        struct.pack_into(
            "<I", protected_text, segment_offsets[0] + 68, 0x00000008
        )
        with self.assertRaisesRegex(B.BuildError, "segment layout differs"):
            B._parse_macho(
                bytes(protected_text), "SG_PROTECTED __TEXT fixture",
                B.MACHO_EXECUTE,
            )
        overlap = bytearray(original)
        struct.pack_into("<Q", overlap, segment_offsets[1] + 40, 0)
        with self.assertRaisesRegex(B.BuildError, "segment file ranges overlap"):
            B._parse_macho(bytes(overlap), "overlap fixture", B.MACHO_EXECUTE)
        self.assertIsNotNone(main_offset)
        self.assertIsNotNone(signature_data_offset)
        entry = bytearray(original)
        struct.pack_into("<Q", entry, int(main_offset) + 8, int(signature_data_offset))
        with self.assertRaisesRegex(B.BuildError, "LC_MAIN entry point differs"):
            B._parse_macho(bytes(entry), "entry fixture", B.MACHO_EXECUTE)

        openmp_symbol = F._signed_macho(
            B, B, base_commands, b"payload", "openmp-symbol-fixture",
            undefined_symbol="___kmpc_fork_call",
        )
        with self.assertRaisesRegex(B.BuildError, "forbidden OpenMP"):
            B._parse_macho(
                openmp_symbol, "OpenMP symbol fixture", B.MACHO_EXECUTE
            )
        for representation, arguments in (
            ("dyld bind", {"dyld_bind_symbol": "___kmpc_fork_call"}),
            ("chained import", {"chained_import_symbol": "___kmpc_fork_call"}),
            ("chained addend import", {
                "chained_import_symbol": "___kmpc_fork_call",
                "chained_import_format": 2,
            }),
            ("chained addend64 import", {
                "chained_import_symbol": "___kmpc_fork_call",
                "chained_import_format": 3,
            }),
            ("unreferenced chained pool", {
                "chained_import_symbol": "_exit",
                "chained_pool_extra_symbols": ("___kmpc_fork_call",),
            }),
            ("export trie", {"export_symbol": "_omp_get_num_threads"}),
        ):
            with self.subTest(representation=representation), self.assertRaisesRegex(
                B.BuildError, "forbidden OpenMP"
            ):
                B._parse_macho(
                    F._signed_macho(
                        B, B, base_commands, b"payload",
                        f"{representation.replace(' ', '-')}-fixture",
                        **arguments,
                    ),
                    f"hidden OpenMP {representation} fixture",
                    B.MACHO_EXECUTE,
                )
        reconciled = B._parse_macho(
            F._signed_macho(
                B, B, base_commands, b"payload", "dyld-union-fixture",
                dyld_bind_symbol="_malloc",
                chained_import_symbol="_exit",
                export_symbol="_main",
            ),
            "dyld union fixture",
            B.MACHO_EXECUTE,
        )
        self.assertEqual(reconciled["dyld_import_symbol_count"], 2)
        self.assertEqual(reconciled["export_symbol_count"], 1)
        self.assertEqual(
            reconciled["symbol_inventory_scope"],
            "LC_SYMTAB nlist plus all present dyld bind, chained-import, "
            "complete chained symbol-pool, and export-trie names",
        )
        with self.assertRaisesRegex(B.BuildError, "unsupported Mach-O load command"):
            B._parse_macho(
                F._signed_macho(
                    B, B, [*base_commands, struct.pack("<II", 0x06, 8)],
                    b"payload", "legacy-loader-command-fixture",
                ),
                "legacy loader command fixture",
                B.MACHO_EXECUTE,
            )
        lazy_names = b"@rpath/libomp.dylib\0___kmpc_fork_call\0"
        lazy_fixture = bytearray(F._signed_macho(
            B, B,
            [
                *base_commands,
                struct.pack(
                    "<IIII", B.LC_LAZY_LOAD_DYLIB_INFO, 16, 0, 0
                ),
            ],
            lazy_names, "lazy-dylib-info-fixture",
        ))
        lazy_names_offset = lazy_fixture.find(lazy_names)
        self.assertGreater(lazy_names_offset, 0)
        lazy_command_offset = None
        command_cursor = 32
        for _ in range(struct.unpack_from("<I", lazy_fixture, 16)[0]):
            command_kind, command_bytes = struct.unpack_from(
                "<II", lazy_fixture, command_cursor
            )
            if command_kind == B.LC_LAZY_LOAD_DYLIB_INFO:
                lazy_command_offset = command_cursor
                break
            command_cursor += command_bytes
        self.assertIsNotNone(lazy_command_offset)
        struct.pack_into(
            "<II", lazy_fixture, int(lazy_command_offset) + 8,
            lazy_names_offset, len(lazy_names),
        )
        with self.assertRaisesRegex(B.BuildError, "forbidden LC_LAZY_LOAD_DYLIB_INFO"):
            B._parse_macho(
                bytes(lazy_fixture),
                "hidden libomp/OpenMP LC_LAZY_LOAD_DYLIB_INFO fixture",
                B.MACHO_EXECUTE,
            )
        libomp_commands = [
            F._load_string_command(B.LC_LOAD_DYLINKER, "/usr/lib/dyld", 12),
            F._load_string_command(0x0C, "/usr/lib/libomp5.dylib", 24),
        ]
        with self.assertRaisesRegex(B.BuildError, "dynamic closure"):
            B._macho_runtime_receipt(
                F._signed_macho(
                    B, B, libomp_commands, b"payload", "dancers"
                ),
                ROOT,
            )

    def test_33_public_builder_rejects_nonisolated_invocation_before_gate(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--execute-engineering-build"],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"requires python -I -S -B", result.stderr)

    def test_34_nonisolated_builder_rejects_before_shadow_stdlib_import(self) -> None:
        with tempfile.TemporaryDirectory(prefix="headless-builder-shadow-") as name:
            directory = Path(name).resolve()
            script = directory / SCRIPT.name
            shutil.copyfile(SCRIPT, script)
            marker = directory / "SHADOW_ARGPARSE_EXECUTED"
            (directory / "argparse.py").write_text(
                f"open({str(marker)!r}, 'wb').write(b'executed')\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [sys.executable, "-S", "-B", str(script), "--help"],
                cwd=directory,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                timeout=20,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn(b"requires python -I -S -B", result.stderr)
            self.assertFalse(marker.exists())

    def test_35_every_git_wrapper_disables_lazy_promisor_fetch(self) -> None:
        fake_guard = {"path": Path("/usr/bin/git")}
        with mock.patch.object(
            B, "_fixed_tool_guard", return_value=fake_guard
        ), mock.patch.object(
            B, "_run_process", return_value=(0, b"synthetic\n", b"")
        ) as run:
            self.assertEqual(
                B._git(ROOT, ["cat-file", "-p", "deadbeef"]),
                b"synthetic\n",
            )
        env = run.call_args.kwargs["env"]
        self.assertEqual(env["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(env["GIT_TERMINAL_PROMPT"], "0")

    def test_36_all_chained_import_formats_and_threaded_bind_names_are_scanned(self) -> None:
        name = b"_synthetic_import\0"
        for import_format, entry in (
            (1, struct.pack("<I", 1 | (1 << 9))),
            (2, struct.pack("<Ii", 1 | (1 << 9), -7)),
            (3, struct.pack("<QQ", 1 | (1 << 32), 9)),
        ):
            starts_offset = 32
            imports_offset = 40 if import_format == 3 else 36
            symbols_offset = imports_offset + len(entry)
            blob = (
                struct.pack(
                    "<7I", 0, starts_offset, imports_offset, symbols_offset,
                    1, import_format, 0,
                )
                + b"\0" * 4
                + struct.pack("<I", 0)
                + b"\0" * (imports_offset - starts_offset - 4)
                + entry
                + b"\0" + name
            )
            blob += b"\0" * ((-len(blob)) % 8)
            receipt = B._parse_chained_fixups(
                blob, f"synthetic chained format {import_format}"
            )
            self.assertEqual(receipt["import_symbols"], ["_synthetic_import"])
            self.assertEqual(receipt["symbol_pool_symbols"], ["_synthetic_import"])
            self.assertEqual(receipt["starts_offset"], 32)
            self.assertEqual(receipt["imports_offset"], imports_offset)
            self.assertEqual(receipt["symbols_offset"], symbols_offset)
            self.assertEqual(receipt["import_entry_bytes"], len(entry))
            self.assertEqual(receipt["import_table_bytes"], len(entry))
            self.assertEqual(receipt["symbol_pool_sentinel_bytes"], 1)
            self.assertEqual(receipt["symbol_pool_entry_count"], 1)
            self.assertEqual(receipt["symbol_pool_referenced_entry_count"], 1)
            self.assertEqual(receipt["symbol_pool_unreferenced_entry_count"], 0)
            self.assertEqual(receipt["symbol_pool_used_bytes"], 1 + len(name))
            self.assertEqual(len(blob) % 8, 0)
        threaded = (
            b"\x40_synthetic_threaded\0"
            b"\xd0\x01\x90\xd1\0"
        )
        self.assertEqual(
            B._parse_dyld_bind_stream(threaded, "synthetic threaded bind"),
            ["_synthetic_threaded"],
        )

    def test_37_host_sdk26_chained_and_export_payloads_are_parser_compatible(self) -> None:
        universal = Path("/usr/bin/true").read_bytes()
        self.assertEqual(universal[:4], b"\xca\xfe\xba\xbe")
        architecture_count = struct.unpack_from(">I", universal, 4)[0]
        thin = None
        for index in range(architecture_count):
            cpu_type, _, offset, size, _ = struct.unpack_from(
                ">iiIII", universal, 8 + index * 20
            )
            if cpu_type == B.MACHO_CPU_ARM64:
                thin = universal[offset : offset + size]
                break
        self.assertIsNotNone(thin)
        assert thin is not None
        command_count = struct.unpack_from("<I", thin, 16)[0]
        cursor = 32
        chained = exports = None
        for _ in range(command_count):
            command, command_bytes = struct.unpack_from("<II", thin, cursor)
            if command in {B.LC_DYLD_CHAINED_FIXUPS, B.LC_DYLD_EXPORTS_TRIE}:
                data_offset, data_bytes = struct.unpack_from("<II", thin, cursor + 8)
                payload = thin[data_offset : data_offset + data_bytes]
                if command == B.LC_DYLD_CHAINED_FIXUPS:
                    chained = B._parse_chained_fixups(payload, "host /usr/bin/true")
                else:
                    exports = B._parse_export_trie(payload, "host /usr/bin/true")
            cursor += command_bytes
        self.assertIsNotNone(chained)
        self.assertIsNotNone(exports)
        self.assertIn("__mh_execute_header", exports)

    def test_38_werror_compile_only_target_has_one_exact_driver_suppression(self) -> None:
        cc = Path("/private/pixi/bin/clang")
        cxx = Path("/private/pixi/bin/clang++")
        work = Path("/private/build/work/pass-1/reference-timing")
        expected_flags = (
            "--no-default-config",
            B.DRIVER_UNUSED_ARGUMENT_SUPPRESSION,
            "--ld-path=/private/pixi/bin/ld",
            *B._prefix_map_flag_atoms(work, "/dancers/reference-source"),
        )
        source = work / "source/tests/headless_descriptor_io_v1_test.cpp"
        object_path = (
            work / "build/CMakeFiles/dancers-headless-descriptor-io-v1-test.dir/"
            "tests/headless_descriptor_io_v1_test.cpp.o"
        )
        command = [
            str(cxx), *expected_flags,
            "-Wall", "-Wextra", "-Wpedantic", "-Werror", "-std=gnu++20",
            "-o", str(object_path), "-c", str(source),
        ]
        B._authenticate_compile_command_driver_flags(
            command, expected_flags, cc, cxx
        )
        self.assertIn("-Werror", command)
        self.assertIn("-c", command)
        self.assertEqual(
            command.count(B.DRIVER_UNUSED_ARGUMENT_SUPPRESSION), 1
        )
        for label, fault in (
            (
                "missing",
                [
                    item for item in command
                    if item != B.DRIVER_UNUSED_ARGUMENT_SUPPRESSION
                ],
            ),
            (
                "duplicate",
                [*command[:2], B.DRIVER_UNUSED_ARGUMENT_SUPPRESSION, *command[2:]],
            ),
            (
                "alternate",
                [*command, "-Wno-unused-command-line-argument"],
            ),
        ):
            with self.subTest(label=label), self.assertRaises(B.BuildError):
                B._authenticate_compile_command_driver_flags(
                    fault, expected_flags, cc, cxx
                )

    def test_39_descriptor_accepts_only_boundary_anchored_empty_data_in_code(self) -> None:
        commands = [
            F._load_string_command(
                0x0000000C, "/usr/lib/libSystem.B.dylib", 24
            ),
            F._load_string_command(B.LC_LOAD_DYLINKER, "/usr/lib/dyld", 12),
        ]

        def binary(profile: str) -> bytes:
            return F._signed_macho(
                B, B, commands, b"descriptor-empty-data-in-code\0",
                B.DESCRIPTOR_TEST_TARGET,
                data_in_code_empty_profile=profile,
            )

        receipt = B._macho_system_executable_receipt(
            binary("boundary"), "realistic descriptor test fixture",
            "__TEXT-file-bytes", B.DESCRIPTOR_TEST_TARGET, 0x00218085,
            require_anchored_empty_data_in_code=True,
        )
        data_in_code = receipt["linkedit_metadata"]["LC_DATA_IN_CODE"]
        self.assertEqual(data_in_code, {
            "bytes": 0,
            "data_offset": data_in_code["data_offset"],
            "empty_anchor_basis": B.DATA_IN_CODE_DESCRIPTOR_ANCHOR_BASIS,
            "empty_encoding": B.DATA_IN_CODE_EMPTY_ENCODING,
            "entry_count": 0,
            "sha256": B._sha256(b""),
        })
        self.assertGreater(data_in_code["data_offset"], 0)
        function_starts = receipt["linkedit_metadata"]["LC_FUNCTION_STARTS"]
        self.assertEqual(
            function_starts["data_offset"] + function_starts["bytes"],
            data_in_code["data_offset"],
        )
        with self.assertRaisesRegex(B.BuildError, "role profile"):
            B._macho_system_executable_receipt(
                binary("zero"), "zero-offset descriptor fixture",
                "__TEXT-file-bytes", B.DESCRIPTOR_TEST_TARGET, 0x00218085,
                require_anchored_empty_data_in_code=True,
            )
        with self.assertRaisesRegex(B.BuildError, "signed __LINKEDIT boundary"):
            B._parse_macho(
                binary("interior"), "interior empty-anchor fixture",
                B.MACHO_EXECUTE,
            )

    def test_40_nonempty_data_in_code_entries_are_semantically_bounded(self) -> None:
        commands = [
            F._load_string_command(
                0x0000000C, "/usr/lib/libSystem.B.dylib", 24
            ),
            F._load_string_command(B.LC_LOAD_DYLINKER, "/usr/lib/dyld", 12),
        ]
        code_payload = b"nonempty-data-in-code-fixture" + b"x" * 64

        def binary(
            entries: tuple[tuple[int, int, int], ...],
            trailing: bytes = b"",
        ) -> bytes:
            return F._signed_macho(
                B, B, commands, code_payload, "data-in-code-entry-fixture",
                data_in_code_entries=entries,
                data_in_code_trailing_bytes=trailing,
            )

        receipt = B._parse_macho(
            binary(((8, 4, 1),)), "valid data-in-code entry fixture",
            B.MACHO_EXECUTE,
        )["linkedit_metadata"]["LC_DATA_IN_CODE"]
        self.assertEqual(receipt["bytes"], 8)
        self.assertEqual(receipt["entry_count"], 1)
        self.assertIsNone(receipt["empty_encoding"])
        for label, entries, trailing, pattern in (
            ("partial", ((8, 4, 1),), b"x", "entry bytes differ"),
            ("zero-length", ((8, 0, 1),), b"", "entry differs"),
            ("unknown-kind", ((8, 4, 6),), b"", "entry differs"),
            (
                "overlap",
                ((8, 8, 1), (12, 2, 1)),
                b"",
                "entry differs",
            ),
            (
                "outside-instructions",
                ((len(code_payload), 1, 1),),
                b"",
                "entry differs",
            ),
        ):
            with self.subTest(label=label), self.assertRaisesRegex(
                B.BuildError, pattern
            ):
                B._parse_macho(
                    binary(entries, trailing), f"{label} data-in-code fixture",
                    B.MACHO_EXECUTE,
                )

    def test_41_chained_symbol_pool_sentinel_padding_and_offsets_are_exact(self) -> None:
        raw = F._chained_fixups_payload(
            "_synthetic_import", 1, ("_unreferenced_pool_name",)
        )
        receipt = B._parse_chained_fixups(raw, "realistic chained pool")
        self.assertEqual(receipt["version"], 0)
        self.assertEqual(receipt["symbols_format"], 0)
        self.assertEqual(receipt["import_format"], 1)
        self.assertEqual(receipt["import_symbols"], ["_synthetic_import"])
        self.assertEqual(receipt["symbol_pool_symbols"], [
            "_synthetic_import", "_unreferenced_pool_name",
        ])
        self.assertEqual(
            receipt["symbol_pool_encoding"], B.CHAINED_SYMBOL_POOL_ENCODING
        )
        self.assertEqual(receipt["symbol_pool_sentinel_bytes"], 1)
        self.assertEqual(receipt["symbol_pool_entry_count"], 2)
        self.assertEqual(receipt["symbol_pool_referenced_entry_count"], 1)
        self.assertEqual(receipt["symbol_pool_unreferenced_entry_count"], 1)
        self.assertEqual(receipt["symbol_pool_padding_bytes"],
                         (-receipt["symbol_pool_used_bytes"]
                          - receipt["symbols_offset"]) % 8)
        self.assertLessEqual(receipt["symbol_pool_padding_bytes"], 7)
        self.assertEqual(len(raw) % 8, 0)

        _, _, imports_offset, symbols_offset, _, _, _ = struct.unpack_from(
            "<7I", raw, 0
        )
        mutants: list[tuple[str, bytes, str]] = []

        no_sentinel = bytearray(raw)
        no_sentinel[symbols_offset] = ord("X")
        mutants.append(("sentinel", bytes(no_sentinel), "sentinel differs"))

        compressed = bytearray(raw)
        struct.pack_into("<I", compressed, 24, 1)
        mutants.append(("compressed", bytes(compressed), "header differs"))

        interior_import = bytearray(raw)
        import_word = struct.unpack_from("<I", interior_import, imports_offset)[0]
        struct.pack_into(
            "<I", interior_import, imports_offset,
            (import_word & 0x1FF) | (2 << 9),
        )
        mutants.append((
            "interior import offset", bytes(interior_import),
            "import name offset differs",
        ))

        hidden_gap = bytearray(raw)
        gap_offset = raw.index(b"_unreferenced_pool_name", symbols_offset)
        hidden_gap[gap_offset] = 0
        mutants.append(("interior gap", bytes(hidden_gap), "padding differs"))

        nonzero_padding = bytearray(raw)
        nonzero_padding[-1] = ord("X")
        mutants.append((
            "nonzero tail", bytes(nonzero_padding), "padding differs",
        ))
        mutants.append((
            "overlong zero tail", raw + b"\0" * 8, "padding differs",
        ))

        invalid_utf8 = bytearray(raw)
        invalid_utf8[gap_offset] = 0xFF
        mutants.append(("invalid UTF-8", bytes(invalid_utf8), "not UTF-8"))

        format3 = bytearray(F._chained_fixups_payload("_format3_import", 3))
        format3_imports = struct.unpack_from("<I", format3, 8)[0]
        format3_word = struct.unpack_from("<Q", format3, format3_imports)[0]
        struct.pack_into("<Q", format3, format3_imports, format3_word | (1 << 17))
        mutants.append((
            "format-3 reserved bits", bytes(format3), "reserved bits differ",
        ))

        for mutant_label, mutant, pattern in mutants:
            with self.subTest(mutant=mutant_label), self.assertRaisesRegex(
                B.BuildError, pattern
            ):
                B._parse_chained_fixups(mutant, f"{mutant_label} chained pool")

        commands = [
            F._load_string_command(
                0x0000000C, "/usr/lib/libSystem.B.dylib", 24
            ),
            F._load_string_command(B.LC_LOAD_DYLINKER, "/usr/lib/dyld", 12),
        ]
        aligned_macho = F._signed_macho(
            B, B, commands, b"aligned-chained-payload",
            "aligned-chained-offset-fixture",
            chained_import_symbol="_aligned_import",
        )
        aligned_receipt = B._parse_macho(
            aligned_macho, "aligned chained offset fixture", B.MACHO_EXECUTE
        )["dyld_chained_fixups"]
        self.assertEqual(aligned_receipt["data_offset"] % 8, 0)
        with self.assertRaisesRegex(B.BuildError, "data offset differs"):
            B._parse_macho(
                F._signed_macho(
                    B, B, commands, b"unaligned-chained-payload",
                    "unaligned-chained-offset-fixture",
                    chained_import_symbol="_unaligned_import",
                    chained_unaligned_data_offset=True,
                ),
                "unaligned chained offset fixture", B.MACHO_EXECUTE,
            )

    def test_42_spawn_shim_requires_file_backed_text_codesign_profile(self) -> None:
        commands = [
            F._load_string_command(
                0x0000000C, "/usr/lib/libSystem.B.dylib", 24
            ),
            F._load_string_command(B.LC_LOAD_DYLINKER, "/usr/lib/dyld", 12),
        ]
        payload = b"realistic-target-26-spawn-shim-profile\0" + b"x" * 96
        accepted = F._signed_macho(
            B, B, commands, payload, B.SHIM_BINARY_NAME,
            section_sized_exec_limit=False,
            header_flags=0x00200085,
        )
        receipt = B._macho_system_executable_receipt(
            accepted, "realistic target-26 spawn shim",
            B.SHIM_EXEC_SEGMENT_LIMIT_BASIS,
            B.SHIM_BINARY_NAME, 0x00200085,
            require_shim_exec_segment_profile=True,
        )
        text_segment = next(
            item for item in receipt["segments"] if item["name"] == "__TEXT"
        )
        text_section = next(
            item for item in text_segment["sections"] if item["name"] == "__text"
        )
        self.assertEqual(receipt["code_signature"]["exec_segment_base"], 0)
        self.assertEqual(
            receipt["code_signature"]["exec_segment_limit"],
            text_segment["file_bytes"],
        )
        self.assertEqual(receipt["entry_point_offset"], text_section["offset"])
        self.assertLessEqual(
            text_section["offset"] + text_section["bytes"],
            receipt["code_signature"]["exec_segment_limit"],
        )
        legacy_section_sized = F._signed_macho(
            B, B, commands, payload, B.SHIM_BINARY_NAME,
            section_sized_exec_limit=True,
            header_flags=0x00200085,
        )
        with self.assertRaisesRegex(
            B.BuildError, "CodeDirectory executable-segment profile differs"
        ):
            B._macho_system_executable_receipt(
                legacy_section_sized, "legacy section-sized spawn shim",
                B.SHIM_EXEC_SEGMENT_LIMIT_BASIS,
                B.SHIM_BINARY_NAME, 0x00200085,
                require_shim_exec_segment_profile=True,
            )


if __name__ == "__main__":
    unittest.main()
