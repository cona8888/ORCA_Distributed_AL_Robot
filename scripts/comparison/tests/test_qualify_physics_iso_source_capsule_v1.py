#!/usr/bin/env python3
"""Synthetic Phase A/Phase B tests for the temp-only ISO qualifier."""

from __future__ import annotations

import ast
import contextlib
import errno
import hashlib
import io
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import types
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[3]
QUALIFIER_PATH = REPOSITORY / "scripts/comparison/qualify_physics_iso_source_capsule_v1.py"

# Keep the reviewed implementation identity isolated for a one-line rebind.
QUALIFIER_BYTES = 155_073
QUALIFIER_SHA256 = "858d1e25da2541faec436a67c4d5c3782f06654f50635f3ef7a2d98af832d638"
PHASE_B_TEST_EXECUTION_POLICY = "serial-only"

REAL_STAGED_TREE_SHA256 = "4c34568494cc8bc20a66617586ff274f8b2bb617594cdb8f120be05e9242d9e8"
REAL_MOUNTED_TREE_SHA256 = "25a127cdf0b48094bb45367ca7386fb3d65a943276cc3f64fa199a7b21ff031a"
REAL_ROOT_DIGESTS = [
    {"root_id": "common-source", "sha256": "f022171964dc4fc0b5b7a849afe96d2a086053b454a5197491d246442dd3fc5a"},
    {"root_id": "optimized-source", "sha256": "d5f80214308714b9bbd357c8e055f6d17ccb7ad7088836f380a1abad3acbf66c"},
    {"root_id": "reference-source", "sha256": "4e798d040657412a225e919fcb11a6281493737f220a305043c205d9c74bfcfb"},
]

TINY_STAGED_TREE_SHA256 = "bea10b118d5f2c56ae803d23f5951c9bd9f7b19b26f760479501189f880f77c9"
TINY_MOUNTED_TREE_SHA256 = "f428f21eb5f1598b3705fe69a210abade9b805e0f8a6fadecb9301dea4af570e"
TINY_ROOT_DIGESTS = [
    {"root_id": "common-source", "sha256": "f9c9f5a9f17c3363df913fa6bcb3b7ba791bf1c0aa19b0e73237801e884518f6"},
]


def load_authenticated_qualifier():
    raw = QUALIFIER_PATH.read_bytes()
    if len(raw) != QUALIFIER_BYTES or hashlib.sha256(raw).hexdigest() != QUALIFIER_SHA256:
        raise AssertionError("qualifier source identity differs before compile")
    module = types.ModuleType("iso_source_capsule_qualifier_v1_tests")
    module.__file__ = str(QUALIFIER_PATH)
    module.__package__ = ""
    exec(compile(raw, str(QUALIFIER_PATH), "exec"), module.__dict__)
    return module, raw


qualifier, QUALIFIER_RAW = load_authenticated_qualifier()


def capsule_residue():
    return sorted(
        path.name
        for path in Path("/private/tmp").glob("dancers-physics-iso-qual-v1-*")
    )


def targeted_bytecode():
    paths = []
    for directory in (
        REPOSITORY / "scripts/comparison/__pycache__",
        REPOSITORY / "scripts/comparison/tests/__pycache__",
    ):
        if directory.is_dir():
            paths.extend(directory.glob("*qualify_physics_iso_source_capsule_v1*.pyc"))
    return sorted(str(path) for path in paths)


def open_fd_count():
    return len(os.listdir("/dev/fd"))


def authenticated_bootstrap():
    verifier_path = REPOSITORY / qualifier.RECEIPT_VERIFIER_PATH
    verifier_raw = verifier_path.read_bytes()
    return {
        "checkpoint_commit": "1" * 40,
        "checkpoint_tree": "2" * 40,
        "qualifier": {
            "bytes": QUALIFIER_BYTES,
            "path": qualifier.QUALIFIER_PATH,
            "role": "ISO qualifier",
            "sha256": QUALIFIER_SHA256,
        },
        "receipt_verifier": {
            "bytes": len(verifier_raw),
            "path": qualifier.RECEIPT_VERIFIER_PATH,
            "role": "ISO receipt verifier",
            "sha256": hashlib.sha256(verifier_raw).hexdigest(),
        },
        "driver_authenticated_qualifier": True,
        "driver_authenticated_receipt_verifier": True,
    }


class IsoSourceCapsuleQualifierV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        contract_raw = qualifier._read_authenticated_repository_file(
            REPOSITORY, qualifier.CONTRACT_PATH, qualifier.CONTRACT_BYTES,
            qualifier.CONTRACT_SHA256, "test ISO qualification contract",
        )
        cls.contract = qualifier._parse_canonical_json(
            contract_raw, qualifier.CONTRACT_BYTES,
        )

    def setUp(self) -> None:
        self.assertEqual(capsule_residue(), [])

        def forbidden_external_execution(*args, **kwargs):
            raise AssertionError("qualifier test attempted external execution")

        self.external_patches = [
            mock.patch.object(subprocess, "Popen", side_effect=forbidden_external_execution),
            mock.patch.object(os, "system", side_effect=forbidden_external_execution),
            mock.patch.object(os, "popen", side_effect=forbidden_external_execution),
        ]
        for name in (
            "execl", "execle", "execlp", "execlpe", "execv", "execve",
            "execvp", "execvpe", "spawnl", "spawnle", "spawnlp", "spawnlpe",
            "spawnv", "spawnve", "spawnvp", "spawnvpe",
        ):
            if hasattr(os, name):
                self.external_patches.append(
                    mock.patch.object(os, name, side_effect=forbidden_external_execution)
                )
        for patcher in self.external_patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def tearDown(self) -> None:
        self.assertEqual(capsule_residue(), [])
        self.assertEqual(targeted_bytecode(), [])

    def assert_failed_synthetic_result(self, result, expected_text) -> None:
        self.assertFalse(result["completed"])
        self.assertIn(expected_text, result["failure"])
        self.assertTrue(result["cleanup"]["complete"])
        self.assertTrue(result["cleanup"]["private_parent_absent"])
        self.assertFalse(result["image_created"])
        self.assertFalse(result["subprocess_invoked"])

    def materialized_tiny_capsule(self):
        raw = b"bounded synthetic Phase B blob\n"
        plan, reader = qualifier._synthetic_phase_b_plan(self.contract, raw)
        capsule = qualifier._create_private_capsule()
        try:
            qualifier._materialize_phase_b_stage(
                capsule, self.contract, plan, reader,
            )
            inventory = qualifier._scan_phase_b_stage(
                capsule, self.contract, plan, require_exact_counts=False,
            )
            self.assertEqual(inventory["staged_tree_sha256"], TINY_STAGED_TREE_SHA256)
            return capsule
        except BaseException:
            if not capsule.cleaned:
                qualifier._cleanup_phase_b_capsule(
                    capsule, require_exact_counts=False,
                )
            raise

    def partial_pre_fstat_capsule(self):
        raw = b"bounded synthetic Phase B blob\n"
        plan, reader = qualifier._synthetic_phase_b_plan(self.contract, raw)
        capsule = qualifier._create_private_capsule()
        real_open = qualifier.os.open
        real_fstat = qualifier.os.fstat
        staged_fd = [None]
        injected = [False]

        def track_stage_open(path, flags, *args, **kwargs):
            descriptor = real_open(path, flags, *args, **kwargs)
            if path == "hello.txt" and flags & os.O_CREAT:
                staged_fd[0] = descriptor
            return descriptor

        def fail_first_stage_fstat(descriptor):
            if descriptor == staged_fd[0] and not injected[0]:
                injected[0] = True
                raise OSError(errno.EIO, "synthetic first fstat failure")
            return real_fstat(descriptor)

        try:
            with mock.patch.object(qualifier.os, "open", side_effect=track_stage_open):
                with mock.patch.object(
                    qualifier.os, "fstat", side_effect=fail_first_stage_fstat,
                ):
                    with self.assertRaisesRegex(
                        qualifier.QualificationError, "first descriptor binding failed",
                    ):
                        qualifier._materialize_phase_b_stage(
                            capsule, self.contract, plan, reader,
                        )
            self.assertTrue(injected[0])
            self.assertEqual(len(capsule.stage_files), 1)
            self.assertEqual(capsule.stage_files[0]["fd"], staged_fd[0])
            self.assertIsNone(capsule.stage_files[0]["inode"])
            return capsule, staged_fd[0]
        except BaseException:
            if not capsule.cleaned:
                qualifier._cleanup_phase_b_capsule(
                    capsule, require_exact_counts=False,
                )
            raise

    def assert_deferred_cleanup(
            self, caught, capsule, baseline_fds, expected_count=1) -> None:
        cancellation = caught.exception
        evidence = cancellation.physical_cleanup_evidence
        self.assertTrue(capsule.cleaned)
        self.assertTrue(evidence["complete"])
        self.assertTrue(evidence["private_parent_absent"])
        self.assertEqual(
            cancellation.deferred_base_exception_count, expected_count,
        )
        self.assertEqual(capsule_residue(), [])
        self.assertEqual(open_fd_count(), baseline_fds)
        self.assertEqual(targeted_bytecode(), [])

    @contextlib.contextmanager
    def pending_sigint_harness(self):
        self.assertIs(threading.current_thread(), threading.main_thread())
        self.assertTrue(callable(getattr(signal, "pthread_sigmask", None)))
        prior_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        unblocked = set(prior_mask)
        unblocked.discard(signal.SIGINT)
        signal.pthread_sigmask(signal.SIG_SETMASK, unblocked)
        active = [""]
        deliveries = []

        def deliver_interrupt(signum, frame):
            error = KeyboardInterrupt(
                "pending acquisition cancellation: " + active[0]
            )
            deliveries.append(error)
            raise error

        prior_handler = signal.signal(signal.SIGINT, deliver_interrupt)
        try:
            yield active, deliveries
        finally:
            signal.signal(signal.SIGINT, prior_handler)
            signal.pthread_sigmask(signal.SIG_SETMASK, prior_mask)

    def assert_signal_case_clean(
            self, caught, deliveries, before_deliveries, baseline_fds) -> None:
        self.assertEqual(len(deliveries), before_deliveries + 1)
        self.assertIs(caught.exception, deliveries[-1])
        self.assertEqual(capsule_residue(), [])
        self.assertEqual(open_fd_count(), baseline_fds)
        self.assertEqual(targeted_bytecode(), [])

    def test_01_raw_identity_cli_and_static_nonexecution(self) -> None:
        self.assertEqual(PHASE_B_TEST_EXECUTION_POLICY, "serial-only")
        self.assertEqual(len(QUALIFIER_RAW), QUALIFIER_BYTES)
        self.assertEqual(hashlib.sha256(QUALIFIER_RAW).hexdigest(), QUALIFIER_SHA256)
        tree = ast.parse(QUALIFIER_RAW.decode("utf-8"), str(QUALIFIER_PATH))
        imported = {
            alias.name.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module.split(".", 1)[0]
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        self.assertNotIn("subprocess", imported)
        forbidden_attributes = {
            "Popen", "system", "popen", "execv", "execve", "execvp",
            "spawnv", "spawnve", "spawnvp",
        }
        self.assertFalse(any(
            isinstance(node, ast.Attribute) and node.attr in forbidden_attributes
            for node in ast.walk(tree)
        ))

        with mock.patch.object(
            qualifier, "_execute_qualification",
            side_effect=AssertionError("CLI reached live execution"),
        ):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(qualifier.main([]), qualifier.FAILURE_EXIT)
                self.assertEqual(qualifier.main(["unexpected"]), qualifier.ARGUMENT_EXIT)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue().count(qualifier.FAILURE_PREFIX), 2)
        self.assertIn("locked-driver bootstrap", stderr.getvalue())
        self.assertIn("accepts no command-line arguments", stderr.getvalue())

    def test_02_phase_a_exact_plan_digests_and_counts_without_materialization(self) -> None:
        pure = qualifier.run_pure_phase_a_self_checks()
        self.assertEqual(pure, {
            "valid": True,
            "regular_file_count": 477,
            "typed_entry_count": 548,
            "total_regular_bytes": 14_595_556,
            "staged_tree_sha256": REAL_STAGED_TREE_SHA256,
            "mounted_tree_sha256": REAL_MOUNTED_TREE_SHA256,
        })
        raw_inputs = qualifier._read_phase_a_inputs(REPOSITORY)
        indexes = [
            qualifier._parse_canonical_json(raw_inputs[path], size)
            for _, path, size, _ in qualifier.INDEX_BINDINGS
        ]
        handoff = qualifier._parse_canonical_json(
            raw_inputs[qualifier.HANDOFF_BINDING[0]], qualifier.HANDOFF_BINDING[1],
        )
        with mock.patch.object(
            qualifier, "_create_private_capsule",
            side_effect=AssertionError("Phase A attempted materialization"),
        ):
            plan = qualifier._build_materialization_plan(
                self.contract, indexes, handoff,
            )
        self.assertEqual(plan["regular_file_count"], 477)
        self.assertEqual(plan["implicit_directory_count"], 68)
        self.assertEqual(plan["root_record_count"], 3)
        self.assertEqual(plan["typed_entry_count"], 548)
        self.assertEqual(plan["total_regular_bytes"], 14_595_556)
        self.assertEqual(plan["staged_tree_sha256"], REAL_STAGED_TREE_SHA256)
        self.assertEqual(plan["mounted_tree_sha256"], REAL_MOUNTED_TREE_SHA256)
        self.assertEqual(plan["mounted_root_digests"], REAL_ROOT_DIGESTS)
        self.assertEqual(len(plan["materialization_entries"]), 477)

    def test_03_authenticated_live_guard_stops_before_phase_b(self) -> None:
        phase_a = {"materialization_plan": {
            "regular_file_count": 477,
            "typed_entry_count": 548,
            "total_regular_bytes": 14_595_556,
            "staged_tree_sha256": REAL_STAGED_TREE_SHA256,
        }}
        with mock.patch.object(
            qualifier, "_create_private_capsule",
            side_effect=AssertionError("live guard reached capsule creation"),
        ), mock.patch.object(
            qualifier, "_materialize_phase_b_stage",
            side_effect=AssertionError("live guard reached stage materialization"),
        ), mock.patch.object(
            qualifier, "_prepare_authenticated_phase_a", return_value=phase_a,
        ):
            with self.assertRaisesRegex(
                qualifier.QualificationError, "live phases are not implemented",
            ):
                qualifier.run_from_authenticated_driver(authenticated_bootstrap())

    def test_04_tiny_phase_b_success_exact_inventory_and_cleanup(self) -> None:
        result = qualifier.exercise_synthetic_phase_b()
        self.assertTrue(result["completed"])
        self.assertIsNone(result["failure"])
        self.assertFalse(result["image_created"])
        self.assertFalse(result["subprocess_invoked"])
        self.assertEqual(len(result["events"]), 101)
        self.assertEqual(result["inventory"], {
            "complete": True,
            "mounted_root_digests": TINY_ROOT_DIGESTS,
            "mounted_tree_sha256": TINY_MOUNTED_TREE_SHA256,
            "regular_file_count": 1,
            "stage_directory_count": 2,
            "staged_tree_sha256": TINY_STAGED_TREE_SHA256,
            "typed_entry_count": 3,
        })
        self.assertEqual(result["cleanup"], {
            "capsule_directories_removed": 4,
            "complete": True,
            "private_parent_absent": True,
            "stage_directories_removed": 2,
            "stage_files_removed": 1,
            "synthetic_injected_cleanup_faults": [],
        })

    def test_05_creation_boundary_faults_reclaim_every_capsule(self) -> None:
        events = (
            "after-private-tmp-open",
            "after-p-mkdir-before-bind",
            "after-p-mkdir",
            "after-p-open",
            "after-stage.work-mkdir-before-bind",
            "after-home-mkdir",
            "after-mount-open",
            "after-p-fsync",
        )
        for event in events:
            with self.subTest(event=event):
                result = qualifier.exercise_synthetic_phase_b(failure_event=event)
                self.assert_failed_synthetic_result(result, event)
                self.assertEqual(capsule_residue(), [])

    def test_06_stage_boundary_faults_and_zero_one_thresholds_reclaim(self) -> None:
        events = (
            "after-stage-directory-mkdir-before-bind:common-source",
            "after-stage-directory-open:common-source/fixture",
            "after-blob-verify:common-source/fixture/hello.txt",
            "after-stage-file-open-before-fstat:common-source/fixture/hello.txt",
            "after-stage-file-create:common-source/fixture/hello.txt",
            "after-stage-file-write:common-source/fixture/hello.txt",
            "after-stage-file-fsync:common-source/fixture/hello.txt",
            "after-stage-directory-fsync:common-source/fixture",
            "after-stage-work-fsync",
        )
        for event in events:
            with self.subTest(event=event):
                result = qualifier.exercise_synthetic_phase_b(failure_event=event)
                self.assert_failed_synthetic_result(result, event)
                self.assertEqual(capsule_residue(), [])
        for threshold, removed_files in ((0, 0), (1, 1)):
            with self.subTest(failure_after_files=threshold):
                result = qualifier.exercise_synthetic_phase_b(
                    failure_after_files=threshold,
                )
                self.assert_failed_synthetic_result(result, "injected")
                self.assertEqual(result["cleanup"]["stage_files_removed"], removed_files)
                self.assertEqual(result["cleanup"]["stage_directories_removed"], 2)
                self.assertEqual(capsule_residue(), [])

    def test_07_cleanup_after_boundary_faults_do_not_repeat_destructive_steps(self) -> None:
        cases = (
            ("after-cleanup-file-unlink:common-source/fixture/hello.txt", "unlink"),
            ("after-cleanup-file-parent-fsync:common-source/fixture/hello.txt", "fsync"),
            ("after-cleanup-file-absence-proof:common-source/fixture/hello.txt", "absence"),
            ("after-cleanup-stage-rmdir:common-source/fixture", "rmdir"),
        )
        for event, label in cases:
            with self.subTest(operation=label):
                result = qualifier.exercise_synthetic_phase_b(failure_event=event)
                self.assert_failed_synthetic_result(result, "cleanup boundary fault")
                self.assertEqual(result["events"].count(event), 1)
                self.assertEqual(
                    len(result["cleanup"]["synthetic_injected_cleanup_faults"]), 1,
                )
                self.assertEqual(capsule_residue(), [])

    def test_08_actual_one_shot_cleanup_failures_restart_but_forbid_success(self) -> None:
        for operation in ("unlink", "rmdir", "fsync", "absence"):
            with self.subTest(operation=operation):
                capsule = self.materialized_tiny_capsule()
                calls = [0]
                if operation == "absence":
                    owner = qualifier
                    attribute = "_name_absent"
                else:
                    owner = qualifier.os
                    attribute = operation
                original = getattr(owner, attribute)

                def fail_once(*args, **kwargs):
                    calls[0] += 1
                    if calls[0] == 1:
                        raise OSError(errno.EIO, "synthetic one-shot cleanup failure")
                    return original(*args, **kwargs)

                with mock.patch.object(owner, attribute, side_effect=fail_once):
                    with self.assertRaisesRegex(
                        qualifier.CleanupError, "actual operation failure",
                    ) as caught:
                        qualifier._cleanup_phase_b_capsule(
                            capsule, require_exact_counts=False,
                        )
                evidence = caught.exception.physical_cleanup_evidence
                self.assertTrue(capsule.cleaned)
                self.assertTrue(evidence["complete"])
                self.assertTrue(evidence["private_parent_absent"])
                self.assertEqual(evidence["stage_files_removed"], 1)
                self.assertEqual(evidence["stage_directories_removed"], 2)
                self.assertTrue(caught.exception.resumed_errors)
                self.assertGreaterEqual(calls[0], 2)
                self.assertEqual(capsule_residue(), [])

    def test_09_real_first_fstat_failure_recovers_unbound_staged_file(self) -> None:
        real_open = qualifier.os.open
        real_fstat = qualifier.os.fstat
        staged_fd = [None]
        injected = [False]

        def track_stage_open(path, flags, *args, **kwargs):
            descriptor = real_open(path, flags, *args, **kwargs)
            if path == "hello.txt" and flags & os.O_CREAT:
                staged_fd[0] = descriptor
            return descriptor

        def fail_first_stage_fstat(descriptor):
            if descriptor == staged_fd[0] and not injected[0]:
                injected[0] = True
                raise OSError(errno.EIO, "synthetic first fstat failure")
            return real_fstat(descriptor)

        with mock.patch.object(qualifier.os, "open", side_effect=track_stage_open):
            with mock.patch.object(qualifier.os, "fstat", side_effect=fail_first_stage_fstat):
                with self.assertRaisesRegex(
                    qualifier.QualificationError, "first descriptor binding failed",
                ):
                    qualifier.exercise_synthetic_phase_b()
        self.assertTrue(injected[0])

    def test_10_cleanup_is_single_use_after_proven_absence(self) -> None:
        capsule = qualifier._create_private_capsule()
        evidence = qualifier._cleanup_phase_b_capsule(
            capsule, require_exact_counts=False,
        )
        self.assertTrue(evidence["complete"])
        self.assertTrue(evidence["private_parent_absent"])
        with self.assertRaisesRegex(qualifier.CleanupError, "requested twice"):
            qualifier._cleanup_phase_b_capsule(
                capsule, require_exact_counts=False,
            )

    def test_11_pending_sigint_is_delivered_only_after_terminal_cleanup(self) -> None:
        self.assertIs(threading.current_thread(), threading.main_thread())
        self.assertEqual(PHASE_B_TEST_EXECUTION_POLICY, "serial-only")
        self.assertTrue(callable(getattr(signal, "pthread_sigmask", None)))
        previous_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        test_mask = set(previous_mask)
        test_mask.discard(signal.SIGINT)
        signal.pthread_sigmask(signal.SIG_SETMASK, test_mask)
        capsule = qualifier._create_private_capsule()
        deliveries = []
        sent = [False]

        def handle_sigint(signum, frame):
            deliveries.append({
                "cleaned": capsule.cleaned,
                "residue": capsule_residue(),
                "signal": signum,
            })
            raise KeyboardInterrupt("synthetic deferred SIGINT")

        previous_handler = signal.signal(signal.SIGINT, handle_sigint)

        def send_during_cleanup(event):
            if event == "before-cleanup-rmdir:capsule-child-stage.work" and not sent[0]:
                sent[0] = True
                os.kill(os.getpid(), signal.SIGINT)
                self.assertEqual(deliveries, [])
                if callable(getattr(signal, "sigpending", None)):
                    self.assertIn(signal.SIGINT, signal.sigpending())

        try:
            with self.assertRaises(KeyboardInterrupt) as caught:
                qualifier._cleanup_phase_b_capsule(
                    capsule, require_exact_counts=False,
                    fault_injector=send_during_cleanup,
                )
            self.assertTrue(sent[0])
            self.assertEqual(deliveries, [{
                "cleaned": True,
                "residue": [],
                "signal": signal.SIGINT,
            }])
            evidence = caught.exception.physical_cleanup_evidence
            self.assertTrue(evidence["complete"])
            self.assertTrue(evidence["private_parent_absent"])
        finally:
            signal.signal(signal.SIGINT, lambda signum, frame: None)
            signal.pthread_sigmask(signal.SIG_SETMASK, previous_mask)
            signal.signal(signal.SIGINT, previous_handler)

    def test_12_post_effect_stage_file_unlink_defers_cancellation(self) -> None:
        baseline_fds = open_fd_count()
        capsule = self.materialized_tiny_capsule()
        real_unlink = qualifier.os.unlink
        triggered = [0]

        def unlink_then_interrupt(path, *args, **kwargs):
            result = real_unlink(path, *args, **kwargs)
            if path == "hello.txt" and triggered[0] == 0:
                triggered[0] += 1
                raise KeyboardInterrupt("post-effect staged-file unlink")
            return result

        with mock.patch.object(qualifier.os, "unlink", side_effect=unlink_then_interrupt):
            with self.assertRaises(KeyboardInterrupt) as caught:
                qualifier._cleanup_phase_b_capsule(
                    capsule, require_exact_counts=False,
                )
        self.assertEqual(triggered[0], 1)
        self.assert_deferred_cleanup(caught, capsule, baseline_fds)
        self.assertEqual(caught.exception.physical_cleanup_evidence["stage_files_removed"], 1)

    def test_13_post_effect_rmdir_matrix_defers_cancellation(self) -> None:
        for site in ("staged-directory", "capsule-child", "private-P"):
            with self.subTest(site=site):
                baseline_fds = open_fd_count()
                if site == "staged-directory":
                    capsule = self.materialized_tiny_capsule()
                    target = "fixture"
                else:
                    capsule = qualifier._create_private_capsule()
                    target = "stage.work" if site == "capsule-child" else capsule.name
                real_rmdir = qualifier.os.rmdir
                triggered = [0]

                def rmdir_then_interrupt(path, *args, **kwargs):
                    result = real_rmdir(path, *args, **kwargs)
                    if path == target and triggered[0] == 0:
                        triggered[0] += 1
                        raise KeyboardInterrupt("post-effect rmdir at " + site)
                    return result

                with mock.patch.object(
                    qualifier.os, "rmdir", side_effect=rmdir_then_interrupt,
                ):
                    with self.assertRaises(KeyboardInterrupt) as caught:
                        qualifier._cleanup_phase_b_capsule(
                            capsule, require_exact_counts=False,
                        )
                self.assertEqual(triggered[0], 1)
                self.assert_deferred_cleanup(caught, capsule, baseline_fds)

    def test_14_post_effect_tracked_close_matrix_reconciles_ebadf(self) -> None:
        for site in (
            "recovered-stage-file", "staged-directory",
            "capsule-child", "private-P",
        ):
            with self.subTest(site=site):
                baseline_fds = open_fd_count()
                if site == "recovered-stage-file":
                    capsule, target_fd = self.partial_pre_fstat_capsule()
                elif site == "staged-directory":
                    capsule = self.materialized_tiny_capsule()
                    target_fd = capsule.stage_directories[("common-source", "fixture")]["fd"]
                else:
                    capsule = qualifier._create_private_capsule()
                    target_fd = (
                        capsule.child_fds["stage.work"]
                        if site == "capsule-child" else capsule.p_fd
                    )
                real_close = qualifier.os.close
                triggered = [0]

                def close_then_interrupt(descriptor):
                    result = real_close(descriptor)
                    if descriptor == target_fd and triggered[0] == 0:
                        triggered[0] += 1
                        raise KeyboardInterrupt("post-effect tracked close at " + site)
                    return result

                with mock.patch.object(
                    qualifier.os, "close", side_effect=close_then_interrupt,
                ):
                    with self.assertRaises(KeyboardInterrupt) as caught:
                        qualifier._cleanup_phase_b_capsule(
                            capsule, require_exact_counts=False,
                        )
                self.assertEqual(triggered[0], 1)
                self.assert_deferred_cleanup(caught, capsule, baseline_fds)

    def test_15_private_ancestry_close_one_shot_error_matrix(self) -> None:
        cases = (
            ("pre-effect-ordinary", OSError(errno.EIO, "ancestry pre ordinary")),
            ("post-effect-ordinary", OSError(errno.EIO, "ancestry post ordinary")),
            ("pre-effect-base", KeyboardInterrupt("ancestry pre cancellation")),
            ("post-effect-base", KeyboardInterrupt("ancestry post cancellation")),
        )
        for site, injected_error in cases:
            with self.subTest(site=site):
                baseline_fds = open_fd_count()
                capsule = qualifier._create_private_capsule()
                target_fd = capsule.private_tmp_fd
                self.assertEqual(target_fd, capsule.private_tmp_fds[-1])
                real_close = qualifier.os.close
                triggered = [0]

                def close_with_one_shot(descriptor):
                    if descriptor == target_fd and triggered[0] == 0:
                        triggered[0] += 1
                        if site.startswith("post-effect"):
                            real_close(descriptor)
                        raise injected_error
                    return real_close(descriptor)

                with mock.patch.object(
                    qualifier.os, "close", side_effect=close_with_one_shot,
                ):
                    if isinstance(injected_error, Exception):
                        with self.assertRaisesRegex(
                            qualifier.CleanupError, "actual operation failure",
                        ) as caught:
                            qualifier._cleanup_phase_b_capsule(
                                capsule, require_exact_counts=False,
                            )
                        self.assertIn(
                            "ancestry " + ("pre" if "pre-effect" in site else "post")
                            + " ordinary",
                            "\n".join(caught.exception.resumed_errors),
                        )
                    else:
                        with self.assertRaises(KeyboardInterrupt) as caught:
                            qualifier._cleanup_phase_b_capsule(
                                capsule, require_exact_counts=False,
                            )
                        self.assertIs(caught.exception, injected_error)
                        self.assert_deferred_cleanup(
                            caught, capsule, baseline_fds,
                        )
                self.assertEqual(triggered[0], 1)
                self.assertTrue(capsule.cleaned)
                self.assertEqual(capsule.private_tmp_fds, [])
                self.assertEqual(capsule.private_tmp_records, [])
                self.assertEqual(capsule.private_tmp_fd, -1)
                self.assertEqual(capsule_residue(), [])
                self.assertEqual(open_fd_count(), baseline_fds)
                self.assertEqual(targeted_bytecode(), [])

    def test_16_persistent_ancestry_close_failure_retains_registry_then_recovers(self) -> None:
        baseline_fds = open_fd_count()
        capsule = qualifier._create_private_capsule()
        target_fd = capsule.private_tmp_fd
        original_records = list(capsule.private_tmp_records)
        real_close = qualifier.os.close
        attempts = [0]

        def fail_target_close(descriptor):
            if descriptor == target_fd:
                attempts[0] += 1
                raise OSError(errno.EIO, "persistent ancestry close")
            return real_close(descriptor)

        with mock.patch.object(
            qualifier.os, "close", side_effect=fail_target_close,
        ):
            with self.assertRaisesRegex(
                qualifier.CleanupError,
                "descriptor cleanup remained incomplete",
            ) as caught:
                qualifier._cleanup_phase_b_capsule(
                    capsule, require_exact_counts=False,
                )
        state = caught.exception.descriptor_close_state
        self.assertFalse(state["complete"])
        self.assertEqual(state["attempt_count"], 4)
        self.assertEqual(len(state["reports"]), 4)
        self.assertTrue(all(not report["complete"] for report in state["reports"]))
        self.assertEqual(attempts[0], 8)
        self.assertFalse(capsule.cleaned)
        self.assertEqual(capsule.private_tmp_fds, [target_fd])
        self.assertEqual(capsule.private_tmp_fd, target_fd)
        self.assertEqual(capsule.private_tmp_records, original_records)
        self.assertEqual(capsule_residue(), [])
        self.assertEqual(open_fd_count(), baseline_fds + 1)

        recovery = qualifier._close_phase_b_descriptors(capsule)
        self.assertEqual(recovery, {
            "complete": True,
            "ordinary_errors": [],
            "deferred_base_exceptions": [],
            "unexpected_live_nonprivate": [],
            "unclosed": [],
        })
        self.assertEqual(capsule.private_tmp_fds, [])
        self.assertEqual(capsule.private_tmp_records, [])
        self.assertEqual(capsule.private_tmp_fd, -1)
        self.assertEqual(open_fd_count(), baseline_fds)
        self.assertEqual(capsule_residue(), [])
        self.assertEqual(targeted_bytecode(), [])

    def test_17_prior_ordinary_error_and_reported_base_preserve_precedence(self) -> None:
        baseline_fds = open_fd_count()
        capsule = qualifier._create_private_capsule()
        target_fd = capsule.private_tmp_fd
        real_close = qualifier.os.close
        cancellation = KeyboardInterrupt("reported ancestry cancellation")
        reauth_attempts = [0]
        close_attempts = [0]

        def fail_reauthentication(_capsule):
            reauth_attempts[0] += 1
            raise OSError(errno.EIO, "prior private-tmp reauthentication error")

        def close_then_report_base(descriptor):
            result = real_close(descriptor)
            if descriptor == target_fd and close_attempts[0] == 0:
                close_attempts[0] += 1
                raise cancellation
            return result

        with mock.patch.object(
            qualifier, "_reauthenticate_private_tmp",
            side_effect=fail_reauthentication,
        ), mock.patch.object(
            qualifier.os, "close", side_effect=close_then_report_base,
        ):
            with self.assertRaises(KeyboardInterrupt) as caught:
                qualifier._cleanup_phase_b_capsule(
                    capsule, require_exact_counts=False,
                )
        self.assertIs(caught.exception, cancellation)
        self.assertEqual(reauth_attempts[0], 2)
        self.assertEqual(close_attempts[0], 1)
        self.assertIsInstance(cancellation.prior_cleanup_error, OSError)
        self.assertIn(
            "prior private-tmp reauthentication error",
            str(cancellation.prior_cleanup_error),
        )
        state = cancellation.descriptor_close_state
        self.assertTrue(state["complete"])
        self.assertEqual(state["attempt_count"], 1)
        self.assertEqual(state["direct_failures"], [])
        self.assertEqual(len(state["reports"]), 1)
        self.assertTrue(state["reports"][0]["complete"])
        self.assertEqual(
            state["reports"][0]["deferred_base_exceptions"],
            [cancellation],
        )
        self.assertFalse(capsule.cleaned)
        self.assertEqual(capsule.private_tmp_fds, [])
        self.assertEqual(capsule_residue(), [])
        self.assertEqual(open_fd_count(), baseline_fds)
        self.assertEqual(targeted_bytecode(), [])

    def test_18_prior_ordinary_error_and_direct_base_preserve_precedence(self) -> None:
        baseline_fds = open_fd_count()
        capsule = qualifier._create_private_capsule()
        real_close_phase = qualifier._close_phase_b_descriptors
        cancellation = KeyboardInterrupt("direct descriptor-close cancellation")
        reauth_attempts = [0]
        close_phase_attempts = [0]

        def fail_reauthentication(_capsule):
            reauth_attempts[0] += 1
            raise OSError(errno.EIO, "prior direct-close reauthentication error")

        def interrupt_close_phase_once(target_capsule):
            close_phase_attempts[0] += 1
            if close_phase_attempts[0] == 1:
                raise cancellation
            return real_close_phase(target_capsule)

        with mock.patch.object(
            qualifier, "_reauthenticate_private_tmp",
            side_effect=fail_reauthentication,
        ), mock.patch.object(
            qualifier, "_close_phase_b_descriptors",
            side_effect=interrupt_close_phase_once,
        ):
            with self.assertRaises(KeyboardInterrupt) as caught:
                qualifier._cleanup_phase_b_capsule(
                    capsule, require_exact_counts=False,
                )
        self.assertIs(caught.exception, cancellation)
        self.assertEqual(reauth_attempts[0], 2)
        self.assertEqual(close_phase_attempts[0], 2)
        self.assertIsInstance(cancellation.prior_cleanup_error, OSError)
        self.assertIn(
            "prior direct-close reauthentication error",
            str(cancellation.prior_cleanup_error),
        )
        state = cancellation.descriptor_close_state
        self.assertTrue(state["complete"])
        self.assertEqual(state["attempt_count"], 2)
        self.assertEqual(len(state["reports"]), 1)
        self.assertTrue(state["reports"][0]["complete"])
        self.assertEqual(
            state["direct_failures"],
            ["KeyboardInterrupt: direct descriptor-close cancellation"],
        )
        self.assertEqual(state["deferred_base_exceptions"], [cancellation])
        self.assertFalse(capsule.cleaned)
        self.assertEqual(capsule.private_tmp_fds, [])
        self.assertEqual(capsule_residue(), [])
        self.assertEqual(open_fd_count(), baseline_fds)
        self.assertEqual(targeted_bytecode(), [])

    def test_19_initial_incomplete_base_report_recovers_before_delivery(self) -> None:
        baseline_fds = open_fd_count()
        capsule = qualifier._create_private_capsule()
        target_fd = capsule.private_tmp_fd
        real_close = qualifier.os.close
        cancellation = KeyboardInterrupt("recoverable ancestry cancellation")
        close_attempts = [0]

        def fail_first_bounded_close(descriptor):
            if descriptor == target_fd and close_attempts[0] < 2:
                close_attempts[0] += 1
                raise cancellation
            return real_close(descriptor)

        with mock.patch.object(
            qualifier.os, "close", side_effect=fail_first_bounded_close,
        ):
            with self.assertRaises(KeyboardInterrupt) as caught:
                qualifier._cleanup_phase_b_capsule(
                    capsule, require_exact_counts=False,
                )
        self.assertIs(caught.exception, cancellation)
        self.assertEqual(close_attempts[0], 2)
        state = cancellation.descriptor_close_state
        self.assertTrue(state["complete"])
        self.assertEqual(state["attempt_count"], 2)
        self.assertEqual(len(state["reports"]), 2)
        self.assertFalse(state["reports"][0]["complete"])
        self.assertTrue(state["reports"][1]["complete"])
        self.assertEqual(len(state["deferred_base_exceptions"]), 2)
        self.assert_deferred_cleanup(
            caught, capsule, baseline_fds, expected_count=2,
        )
        self.assertEqual(capsule.private_tmp_fds, [])
        self.assertEqual(capsule.private_tmp_records, [])

    def test_20_persistent_incomplete_base_retains_registry_then_recovers(self) -> None:
        baseline_fds = open_fd_count()
        capsule = qualifier._create_private_capsule()
        target_fd = capsule.private_tmp_fd
        original_records = list(capsule.private_tmp_records)
        real_close = qualifier.os.close
        cancellations = []

        def cancel_target_close(descriptor):
            if descriptor == target_fd:
                cancellation = KeyboardInterrupt(
                    "persistent ancestry cancellation %d" % len(cancellations)
                )
                cancellations.append(cancellation)
                raise cancellation
            return real_close(descriptor)

        with mock.patch.object(
            qualifier.os, "close", side_effect=cancel_target_close,
        ):
            with self.assertRaises(KeyboardInterrupt) as caught:
                qualifier._cleanup_phase_b_capsule(
                    capsule, require_exact_counts=False,
                )
        self.assertIs(caught.exception, cancellations[0])
        self.assertEqual(len(cancellations), 8)
        self.assertFalse(caught.exception.cleanup_complete)
        self.assertIsInstance(
            caught.exception.prior_cleanup_error, qualifier.CleanupError,
        )
        self.assertIn(
            "one or more private cleanup descriptors remain unclosed",
            str(caught.exception.prior_cleanup_error),
        )
        state = caught.exception.descriptor_close_state
        self.assertFalse(state["complete"])
        self.assertEqual(state["attempt_count"], 4)
        self.assertEqual(len(state["reports"]), 4)
        self.assertEqual(len(state["deferred_base_exceptions"]), 8)
        self.assertTrue(all(not report["complete"] for report in state["reports"]))
        self.assertFalse(capsule.cleaned)
        self.assertEqual(capsule.private_tmp_fds, [target_fd])
        self.assertEqual(capsule.private_tmp_fd, target_fd)
        self.assertEqual(capsule.private_tmp_records, original_records)
        self.assertEqual(capsule_residue(), [])
        self.assertEqual(open_fd_count(), baseline_fds + 1)

        recovery = qualifier._close_phase_b_descriptors(capsule)
        self.assertTrue(recovery["complete"])
        self.assertEqual(recovery["ordinary_errors"], [])
        self.assertEqual(recovery["deferred_base_exceptions"], [])
        self.assertEqual(capsule.private_tmp_fds, [])
        self.assertEqual(capsule.private_tmp_records, [])
        self.assertEqual(capsule.private_tmp_fd, -1)
        self.assertEqual(capsule_residue(), [])
        self.assertEqual(open_fd_count(), baseline_fds)
        self.assertEqual(targeted_bytecode(), [])

    def test_21_ordinary_mask_restore_failure_preserves_prior_base(self) -> None:
        self.assertTrue(callable(getattr(signal, "pthread_sigmask", None)))
        baseline_fds = open_fd_count()
        prior_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        capsule = qualifier._create_private_capsule()
        target_fd = capsule.private_tmp_fd
        real_close = qualifier.os.close
        real_restore = qualifier._restore_tracking_signals
        cancellation = KeyboardInterrupt("prior cleanup cancellation")
        close_attempts = [0]
        restore_failures = [0]

        def close_then_interrupt(descriptor):
            result = real_close(descriptor)
            if descriptor == target_fd and close_attempts[0] == 0:
                close_attempts[0] += 1
                raise cancellation
            return result

        def restore_then_fail(previous):
            real_restore(previous)
            if capsule.cleaned and restore_failures[0] == 0:
                restore_failures[0] += 1
                raise OSError(errno.EIO, "ordinary terminal mask restore failure")

        try:
            with mock.patch.object(
                qualifier.os, "close", side_effect=close_then_interrupt,
            ), mock.patch.object(
                qualifier, "_restore_tracking_signals",
                side_effect=restore_then_fail,
            ):
                with self.assertRaises(KeyboardInterrupt) as caught:
                    qualifier._cleanup_phase_b_capsule(
                        capsule, require_exact_counts=False,
                    )
            self.assertIs(caught.exception, cancellation)
            self.assertEqual(close_attempts[0], 1)
            self.assertEqual(restore_failures[0], 1)
            self.assertIsInstance(cancellation.signal_restoration_error, OSError)
            self.assertIn(
                "ordinary terminal mask restore failure",
                str(cancellation.signal_restoration_error),
            )
            self.assertTrue(cancellation.descriptor_close_state["complete"])
            self.assert_deferred_cleanup(caught, capsule, baseline_fds)
            self.assertEqual(
                signal.pthread_sigmask(signal.SIG_BLOCK, set()), prior_mask,
            )
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, prior_mask)
        self.assertEqual(capsule_residue(), [])
        self.assertEqual(open_fd_count(), baseline_fds)
        self.assertEqual(targeted_bytecode(), [])

    def test_22_private_tmp_raw_acquisition_and_helper_transfer(self) -> None:
        with self.pending_sigint_harness() as (active, deliveries):
            for target in ("root", "private-child"):
                with self.subTest(target=target):
                    baseline_fds = open_fd_count()
                    capsule = qualifier._PhaseBCapsule()
                    real_open = qualifier.os.open
                    sent = [False]
                    active[0] = "private-tmp-" + target
                    before = len(deliveries)

                    def open_then_signal(path, flags, *args, **kwargs):
                        descriptor = real_open(path, flags, *args, **kwargs)
                        selected = (
                            target == "root" and path == "/"
                        ) or (
                            target == "private-child" and path == "private"
                        )
                        if selected and not sent[0]:
                            sent[0] = True
                            os.kill(os.getpid(), signal.SIGINT)
                        return descriptor

                    with mock.patch.object(
                        qualifier.os, "open", side_effect=open_then_signal,
                    ):
                        with self.assertRaises(KeyboardInterrupt) as caught:
                            qualifier._open_private_tmp(capsule)
                    self.assertTrue(sent[0])
                    self.assertEqual(capsule.private_tmp_fds, [])
                    self.assertEqual(capsule.private_tmp_records, [])
                    self.assertEqual(capsule.private_tmp_fd, -1)
                    self.assert_signal_case_clean(
                        caught, deliveries, before, baseline_fds,
                    )

            baseline_fds = open_fd_count()
            captured = []
            real_private_tmp = qualifier._open_private_tmp
            active[0] = "private-tmp-helper-transfer"
            before = len(deliveries)

            def private_tmp_then_signal(capsule):
                captured.append(capsule)
                result = real_private_tmp(capsule)
                os.kill(os.getpid(), signal.SIGINT)
                return result

            with mock.patch.object(
                qualifier, "_open_private_tmp",
                side_effect=private_tmp_then_signal,
            ):
                with self.assertRaises(KeyboardInterrupt) as caught:
                    qualifier._create_private_capsule()
            self.assertEqual(len(captured), 1)
            capsule = captured[0]
            self.assertTrue(capsule.cleaned)
            self.assertEqual(capsule.private_tmp_fds, [])
            self.assertEqual(capsule.private_tmp_records, [])
            self.assertEqual(capsule.private_tmp_fd, -1)
            evidence = caught.exception.physical_cleanup_evidence
            self.assertTrue(evidence["complete"])
            self.assertTrue(evidence["private_parent_absent"])
            self.assert_signal_case_clean(
                caught, deliveries, before, baseline_fds,
            )

    def test_23_bound_directory_raw_and_private_registry_transfers(self) -> None:
        with self.pending_sigint_harness() as (active, deliveries):
            baseline_fds = open_fd_count()
            with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
                root = Path(temporary)
                child = root / "child"
                child.mkdir(mode=0o700)
                flags = (
                    os.O_RDONLY | qualifier._required_open_flag("O_CLOEXEC")
                    | qualifier._required_open_flag("O_DIRECTORY")
                    | qualifier._required_open_flag("O_NOFOLLOW")
                )
                parent_fd = os.open(str(root), flags)
                with_parent = open_fd_count()
                real_open = qualifier.os.open
                sent = [False]
                active[0] = "bound-directory-raw-open"
                before = len(deliveries)

                def bound_open_then_signal(path, open_flags, *args, **kwargs):
                    descriptor = real_open(path, open_flags, *args, **kwargs)
                    if path == "child" and not sent[0]:
                        sent[0] = True
                        os.kill(os.getpid(), signal.SIGINT)
                    return descriptor

                try:
                    with mock.patch.object(
                        qualifier.os, "open", side_effect=bound_open_then_signal,
                    ):
                        with self.assertRaises(KeyboardInterrupt) as caught:
                            qualifier._open_bound_directory_at(
                                parent_fd, "child", 0o700,
                                "synthetic bound child",
                            )
                    self.assertTrue(sent[0])
                    self.assertEqual(open_fd_count(), with_parent)
                    self.assertEqual(len(deliveries), before + 1)
                    self.assertIs(caught.exception, deliveries[-1])
                finally:
                    os.close(parent_fd)
            self.assertEqual(open_fd_count(), baseline_fds)
            self.assertEqual(capsule_residue(), [])

            for target_label in (
                "private capsule", "capsule child stage.work",
            ):
                with self.subTest(target_label=target_label):
                    baseline_fds = open_fd_count()
                    active[0] = "bound-helper-transfer-" + target_label
                    before = len(deliveries)
                    captured = []
                    cleanup_entries = []
                    real_private_tmp = qualifier._open_private_tmp
                    real_bound = qualifier._open_bound_directory_at
                    real_cleanup = qualifier._cleanup_phase_b_capsule
                    sent = [False]

                    def capture_private_tmp(capsule):
                        captured.append(capsule)
                        return real_private_tmp(capsule)

                    def bound_then_signal(*args, **kwargs):
                        result = real_bound(*args, **kwargs)
                        label = args[3] if len(args) > 3 else kwargs.get("label")
                        if label == target_label and not sent[0]:
                            sent[0] = True
                            os.kill(os.getpid(), signal.SIGINT)
                        return result

                    def observe_cleanup(capsule, *args, **kwargs):
                        cleanup_entries.append({
                            "p_fd": capsule.p_fd,
                            "children": set(capsule.child_fds),
                        })
                        return real_cleanup(capsule, *args, **kwargs)

                    with mock.patch.object(
                        qualifier, "_open_private_tmp",
                        side_effect=capture_private_tmp,
                    ), mock.patch.object(
                        qualifier, "_open_bound_directory_at",
                        side_effect=bound_then_signal,
                    ), mock.patch.object(
                        qualifier, "_cleanup_phase_b_capsule",
                        side_effect=observe_cleanup,
                    ):
                        with self.assertRaises(KeyboardInterrupt) as caught:
                            qualifier._create_private_capsule()
                    self.assertTrue(sent[0])
                    self.assertEqual(len(captured), 1)
                    self.assertEqual(len(cleanup_entries), 1)
                    self.assertGreaterEqual(cleanup_entries[0]["p_fd"], 0)
                    if target_label == "capsule child stage.work":
                        self.assertIn(
                            "stage.work", cleanup_entries[0]["children"],
                        )
                    else:
                        self.assertEqual(cleanup_entries[0]["children"], set())
                    capsule = captured[0]
                    self.assertTrue(capsule.cleaned)
                    self.assertEqual(capsule.child_fds, {})
                    self.assertEqual(capsule.p_fd, -1)
                    self.assertTrue(
                        caught.exception.physical_cleanup_evidence["complete"],
                    )
                    self.assert_signal_case_clean(
                        caught, deliveries, before, baseline_fds,
                    )

    def test_24_stage_directory_file_and_reopen_acquisition_registries(self) -> None:
        raw = b"bounded synthetic Phase B blob\n"
        plan, reader = qualifier._synthetic_phase_b_plan(self.contract, raw)
        with self.pending_sigint_harness() as (active, deliveries):
            baseline_fds = open_fd_count()
            capsule = qualifier._create_private_capsule()
            real_bound = qualifier._open_bound_directory_at
            sent = [False]
            active[0] = "stage-directory-helper-transfer"
            before = len(deliveries)

            def stage_bound_then_signal(*args, **kwargs):
                result = real_bound(*args, **kwargs)
                label = args[3] if len(args) > 3 else kwargs.get("label")
                if label == "staged directory common-source" and not sent[0]:
                    sent[0] = True
                    os.kill(os.getpid(), signal.SIGINT)
                return result

            try:
                with mock.patch.object(
                    qualifier, "_open_bound_directory_at",
                    side_effect=stage_bound_then_signal,
                ):
                    with self.assertRaises(KeyboardInterrupt) as caught:
                        qualifier._materialize_phase_b_stage(
                            capsule, self.contract, plan, reader,
                        )
                self.assertTrue(sent[0])
                root_record = capsule.stage_directories[("common-source", "")]
                self.assertGreaterEqual(root_record["fd"], 0)
                self.assertIsNotNone(root_record["inode"])
                self.assertEqual(len(deliveries), before + 1)
                self.assertIs(caught.exception, deliveries[-1])
            finally:
                if not capsule.cleaned:
                    qualifier._cleanup_phase_b_capsule(
                        capsule, require_exact_counts=False,
                    )
            self.assertEqual(open_fd_count(), baseline_fds)
            self.assertEqual(capsule_residue(), [])

            baseline_fds = open_fd_count()
            capsule = qualifier._create_private_capsule()
            real_open = qualifier.os.open
            sent = [False]
            active[0] = "stage-file-exclusive-open"
            before = len(deliveries)

            def stage_file_open_then_signal(path, flags, *args, **kwargs):
                descriptor = real_open(path, flags, *args, **kwargs)
                if (
                    path == "hello.txt" and flags & os.O_CREAT
                    and not sent[0]
                ):
                    sent[0] = True
                    os.kill(os.getpid(), signal.SIGINT)
                return descriptor

            try:
                with mock.patch.object(
                    qualifier.os, "open", side_effect=stage_file_open_then_signal,
                ):
                    with self.assertRaises(KeyboardInterrupt) as caught:
                        qualifier._materialize_phase_b_stage(
                            capsule, self.contract, plan, reader,
                        )
                self.assertTrue(sent[0])
                self.assertEqual(len(capsule.stage_files), 1)
                file_record = capsule.stage_files[0]
                self.assertEqual(file_record["name"], "hello.txt")
                self.assertIsNotNone(file_record["inode"])
                self.assertEqual(file_record["fd"], -1)
                self.assertTrue(
                    file_record["descriptor_closed_after_materialize"],
                )
                self.assertEqual(len(deliveries), before + 1)
                self.assertIs(caught.exception, deliveries[-1])
            finally:
                if not capsule.cleaned:
                    qualifier._cleanup_phase_b_capsule(
                        capsule, require_exact_counts=False,
                    )
            self.assertEqual(open_fd_count(), baseline_fds)
            self.assertEqual(capsule_residue(), [])

            baseline_fds = open_fd_count()
            capsule = self.materialized_tiny_capsule()
            record = capsule.stage_directories[("common-source", "fixture")]
            os.close(record["fd"])
            record["fd"] = -1
            real_open = qualifier.os.open
            sent = [False]
            active[0] = "stage-directory-reopen-record"
            before = len(deliveries)

            def reopen_then_signal(path, flags, *args, **kwargs):
                descriptor = real_open(path, flags, *args, **kwargs)
                if (
                    path == record["name"]
                    and kwargs.get("dir_fd") == record["parent_fd"]
                    and not sent[0]
                ):
                    sent[0] = True
                    os.kill(os.getpid(), signal.SIGINT)
                return descriptor

            try:
                with mock.patch.object(
                    qualifier.os, "open", side_effect=reopen_then_signal,
                ):
                    with self.assertRaises(KeyboardInterrupt) as caught:
                        qualifier._reopen_stage_directory_record(record)
                self.assertTrue(sent[0])
                self.assertEqual(record["fd"], -1)
                self.assertEqual(len(deliveries), before + 1)
                self.assertIs(caught.exception, deliveries[-1])
            finally:
                if not capsule.cleaned:
                    qualifier._cleanup_phase_b_capsule(
                        capsule, require_exact_counts=False,
                    )
            self.assertEqual(open_fd_count(), baseline_fds)
            self.assertEqual(capsule_residue(), [])
            self.assertEqual(targeted_bytecode(), [])

    def test_25_ephemeral_fd_transfer_for_reauth_scan_and_unlink_guard(self) -> None:
        raw = b"bounded synthetic Phase B blob\n"
        plan, _ = qualifier._synthetic_phase_b_plan(self.contract, raw)
        expected_regular = next(
            entry for entry in plan["roots"][0]["typed_entries"]
            if entry["entry_type"] == "regular"
        )
        with self.pending_sigint_harness() as (active, deliveries):
            for use in ("staged-reauth", "staged-scan"):
                with self.subTest(use=use):
                    baseline_fds = open_fd_count()
                    capsule = self.materialized_tiny_capsule()
                    record = capsule.stage_files[0]
                    real_open = qualifier.os.open
                    sent = [False]
                    active[0] = "ephemeral-" + use
                    before = len(deliveries)

                    def open_then_signal(path, flags, *args, **kwargs):
                        descriptor = real_open(path, flags, *args, **kwargs)
                        if (
                            path == "hello.txt" and not flags & os.O_CREAT
                            and not sent[0]
                        ):
                            sent[0] = True
                            os.kill(os.getpid(), signal.SIGINT)
                        return descriptor

                    try:
                        with mock.patch.object(
                            qualifier.os, "open", side_effect=open_then_signal,
                        ):
                            with self.assertRaises(KeyboardInterrupt) as caught:
                                if use == "staged-reauth":
                                    qualifier._reauthenticate_staged_file(record)
                                else:
                                    qualifier._scan_staged_regular(
                                        record["parent_fd"], record["name"],
                                        expected_regular, set(),
                                        os.fstat(record["parent_fd"]).st_dev,
                                    )
                        self.assertTrue(sent[0])
                    finally:
                        if not capsule.cleaned:
                            qualifier._cleanup_phase_b_capsule(
                                capsule, require_exact_counts=False,
                            )
                    self.assert_signal_case_clean(
                        caught, deliveries, before, baseline_fds,
                    )

            baseline_fds = open_fd_count()
            capsule = self.materialized_tiny_capsule()
            real_open = qualifier.os.open
            sent = [False]
            active[0] = "ephemeral-unlink-guard"
            before = len(deliveries)

            def guard_open_then_signal(path, flags, *args, **kwargs):
                descriptor = real_open(path, flags, *args, **kwargs)
                if (
                    path == "hello.txt" and not flags & os.O_CREAT
                    and not sent[0]
                ):
                    sent[0] = True
                    os.kill(os.getpid(), signal.SIGINT)
                return descriptor

            with mock.patch.object(
                qualifier.os, "open", side_effect=guard_open_then_signal,
            ):
                with self.assertRaises(KeyboardInterrupt) as caught:
                    qualifier._cleanup_phase_b_capsule(
                        capsule, require_exact_counts=False,
                    )
            self.assertTrue(sent[0])
            self.assertTrue(capsule.cleaned)
            self.assertTrue(
                caught.exception.physical_cleanup_evidence["complete"],
            )
            self.assert_signal_case_clean(
                caught, deliveries, before, baseline_fds,
            )

    def test_26_authenticated_reader_acquisition_and_helper_transfers(self) -> None:
        repository_parts = Path(os.path.abspath(str(REPOSITORY))).parts
        first_repository_component = repository_parts[1]
        leaf = Path(qualifier.CONTRACT_PATH).name

        def authenticated_read():
            return qualifier._read_authenticated_repository_file(
                REPOSITORY, qualifier.CONTRACT_PATH,
                qualifier.CONTRACT_BYTES, qualifier.CONTRACT_SHA256,
                "synthetic acquisition reader",
            )

        cases = (
            ("absolute-root", lambda path, flags, kwargs: path == "/"),
            (
                "absolute-child",
                lambda path, flags, kwargs: path == first_repository_component,
            ),
            ("relative-directory", lambda path, flags, kwargs: path == "docs"),
            ("final-leaf", lambda path, flags, kwargs: path == leaf),
        )
        with self.pending_sigint_harness() as (active, deliveries):
            for case, predicate in cases:
                with self.subTest(case=case):
                    baseline_fds = open_fd_count()
                    real_open = qualifier.os.open
                    sent = [False]
                    active[0] = "authenticated-reader-" + case
                    before = len(deliveries)

                    def open_then_signal(path, flags, *args, **kwargs):
                        descriptor = real_open(path, flags, *args, **kwargs)
                        if predicate(path, flags, kwargs) and not sent[0]:
                            sent[0] = True
                            os.kill(os.getpid(), signal.SIGINT)
                        return descriptor

                    with mock.patch.object(
                        qualifier.os, "open", side_effect=open_then_signal,
                    ):
                        with self.assertRaises(KeyboardInterrupt) as caught:
                            authenticated_read()
                    self.assertTrue(sent[0])
                    self.assert_signal_case_clean(
                        caught, deliveries, before, baseline_fds,
                    )

            baseline_fds = open_fd_count()
            real_chain = qualifier._open_absolute_directory_chain
            sent = [False]
            active[0] = "authenticated-reader-chain-helper-transfer"
            before = len(deliveries)

            def chain_then_signal(*args, **kwargs):
                result = real_chain(*args, **kwargs)
                if not sent[0]:
                    sent[0] = True
                    os.kill(os.getpid(), signal.SIGINT)
                return result

            with mock.patch.object(
                qualifier, "_open_absolute_directory_chain",
                side_effect=chain_then_signal,
            ):
                with self.assertRaises(KeyboardInterrupt) as caught:
                    authenticated_read()
            self.assertTrue(sent[0])
            self.assert_signal_case_clean(
                caught, deliveries, before, baseline_fds,
            )

    def test_27_directory_iterator_pending_and_exception_precedence(self) -> None:
        directory_flags = (
            os.O_RDONLY | qualifier._required_open_flag("O_CLOEXEC")
            | qualifier._required_open_flag("O_DIRECTORY")
            | qualifier._required_open_flag("O_NOFOLLOW")
        )
        with self.pending_sigint_harness() as (active, deliveries):
            baseline_fds = open_fd_count()
            with tempfile.TemporaryDirectory(dir="/private/tmp") as temporary:
                directory_fd = os.open(temporary, directory_flags)
                real_scandir = qualifier.os.scandir
                sent = [False]
                active[0] = "directory-iterator-return"
                before = len(deliveries)

                def scandir_then_signal(*args, **kwargs):
                    iterator = real_scandir(*args, **kwargs)
                    if not sent[0]:
                        sent[0] = True
                        os.kill(os.getpid(), signal.SIGINT)
                    return iterator

                try:
                    with mock.patch.object(
                        qualifier.os, "scandir",
                        side_effect=scandir_then_signal,
                    ):
                        with self.assertRaises(KeyboardInterrupt) as caught:
                            qualifier._directory_names(
                                directory_fd, 0,
                                "pending directory iterator",
                            )
                    self.assertTrue(sent[0])
                    self.assertEqual(len(deliveries), before + 1)
                    self.assertIs(caught.exception, deliveries[-1])
                    self.assertEqual(open_fd_count(), baseline_fds + 1)
                finally:
                    os.close(directory_fd)
            self.assertEqual(open_fd_count(), baseline_fds)
            self.assertEqual(capsule_residue(), [])

        class FaultingIterator:
            def __init__(self, primary, close_error, close_timing):
                self.primary = primary
                self.close_error = close_error
                self.close_timing = close_timing
                self.close_calls = 0
                self.fd = os.open("/dev/null", os.O_RDONLY)
                self.closed = False

            def __iter__(self):
                return self

            def __next__(self):
                if self.primary is None:
                    raise StopIteration
                raise self.primary

            def close(self):
                self.close_calls += 1
                if self.close_calls == 1 and self.close_error is not None:
                    if self.close_timing == "post":
                        os.close(self.fd)
                        self.closed = True
                    raise self.close_error
                if not self.closed:
                    os.close(self.fd)
                    self.closed = True

        cases = (
            ("ordinary-primary-ordinary-close", "ordinary", "ordinary", "pre"),
            ("ordinary-primary-base-close", "ordinary", "base", "post"),
            ("base-primary-ordinary-close", "base", "ordinary", "post"),
            ("base-primary-base-close", "base", "base", "pre"),
            ("close-only-ordinary", None, "ordinary", "post"),
        )
        for case, primary_kind, close_kind, close_timing in cases:
            with self.subTest(case=case):
                baseline_fds = open_fd_count()
                primary = None
                if primary_kind == "ordinary":
                    primary = OSError(errno.EIO, case + " iteration")
                elif primary_kind == "base":
                    primary = KeyboardInterrupt(case + " iteration")
                if close_kind == "ordinary":
                    close_error = OSError(errno.EIO, case + " close")
                else:
                    close_error = KeyboardInterrupt(case + " close")
                iterator = FaultingIterator(primary, close_error, close_timing)
                directory_fd = os.open("/private/tmp", directory_flags)
                try:
                    with mock.patch.object(
                        qualifier.os, "scandir", return_value=iterator,
                    ):
                        with self.assertRaises(BaseException) as caught:
                            qualifier._directory_names(
                                directory_fd, 0,
                                "faulting synthetic iterator " + case,
                            )
                    if primary_kind == "base":
                        self.assertIs(caught.exception, primary)
                    elif close_kind == "base":
                        self.assertIs(caught.exception, close_error)
                    elif primary_kind == "ordinary":
                        self.assertIsInstance(
                            caught.exception, qualifier.QualificationError,
                        )
                    else:
                        self.assertIsInstance(
                            caught.exception, qualifier.CleanupError,
                        )
                    report = caught.exception.descriptor_close_report
                    self.assertTrue(report["complete"])
                    self.assertEqual(report["attempt_count"], 2)
                    self.assertTrue(iterator.closed)
                    self.assertEqual(iterator.close_calls, 2)
                    self.assertEqual(open_fd_count(), baseline_fds + 1)
                finally:
                    if not iterator.closed:
                        os.close(iterator.fd)
                    os.close(directory_fd)
                self.assertEqual(open_fd_count(), baseline_fds)
                self.assertEqual(capsule_residue(), [])
        self.assertEqual(targeted_bytecode(), [])

    def test_28_descriptor_and_signal_base_exception_chronology(self) -> None:
        for primary_kind in ("success", "ordinary", "base"):
            with self.subTest(ephemeral_primary=primary_kind):
                baseline_fds = open_fd_count()
                target = [None]
                real_close = qualifier.os.close
                real_restore = qualifier._restore_tracking_signals
                operation_base = KeyboardInterrupt(
                    "ephemeral operation BaseException",
                )
                close_base = KeyboardInterrupt(
                    "ephemeral close BaseException",
                )
                restore_base = KeyboardInterrupt(
                    "ephemeral restoration BaseException",
                )
                close_sent = [False]
                restore_sent = [False]

                def open_target():
                    target[0] = os.open("/dev/null", os.O_RDONLY)
                    return target[0]

                def operate(descriptor):
                    self.assertEqual(descriptor, target[0])
                    if primary_kind == "ordinary":
                        raise OSError(errno.EIO, "ephemeral ordinary primary")
                    if primary_kind == "base":
                        raise operation_base
                    return "unpublishable-success"

                def close_then_base(descriptor):
                    result = real_close(descriptor)
                    if descriptor == target[0] and not close_sent[0]:
                        close_sent[0] = True
                        raise close_base
                    return result

                def restore_then_base(previous):
                    real_restore(previous)
                    if not restore_sent[0]:
                        restore_sent[0] = True
                        raise restore_base

                with mock.patch.object(
                    qualifier.os, "close", side_effect=close_then_base,
                ), mock.patch.object(
                    qualifier, "_restore_tracking_signals",
                    side_effect=restore_then_base,
                ):
                    with self.assertRaises(KeyboardInterrupt) as caught:
                        qualifier._operate_on_ephemeral_fd(
                            open_target, operate,
                            "chronological ephemeral descriptor",
                        )
                expected = operation_base if primary_kind == "base" else close_base
                self.assertIs(caught.exception, expected)
                self.assertTrue(close_sent[0])
                self.assertTrue(restore_sent[0])
                report = caught.exception.descriptor_close_report
                self.assertTrue(report["complete"])
                self.assertEqual(
                    report["deferred_base_exceptions"],
                    [close_base, restore_base],
                )
                self.assertEqual(open_fd_count(), baseline_fds)

        baseline_fds = open_fd_count()
        target = [None]
        real_close = qualifier.os.close
        close_sent = [False]

        def open_for_ordinary_close():
            target[0] = os.open("/dev/null", os.O_RDONLY)
            return target[0]

        def ordinary_close_after_effect(descriptor):
            result = real_close(descriptor)
            if descriptor == target[0] and not close_sent[0]:
                close_sent[0] = True
                raise OSError(errno.EIO, "post-effect ephemeral close")
            return result

        with mock.patch.object(
            qualifier.os, "close", side_effect=ordinary_close_after_effect,
        ):
            with self.assertRaises(qualifier.CleanupError):
                qualifier._operate_on_ephemeral_fd(
                    open_for_ordinary_close, lambda descriptor: True,
                    "post-effect ordinary ephemeral close",
                )
        self.assertTrue(close_sent[0])
        self.assertEqual(open_fd_count(), baseline_fds)

        baseline_fds = open_fd_count()
        target_fd = os.open("/dev/null", os.O_RDONLY)
        descriptors = [target_fd]
        prior_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        previous_signals = qualifier._block_tracking_signals()
        real_close = qualifier.os.close
        real_restore = qualifier._restore_tracking_signals
        reblock_base = KeyboardInterrupt("cleanup signal-reblock BaseException")
        close_base = KeyboardInterrupt("cleanup close BaseException")
        restore_base = KeyboardInterrupt("cleanup restoration BaseException")
        close_sent = [False]
        restore_sent = [False]

        def close_after_reblock(descriptor):
            result = real_close(descriptor)
            if descriptor == target_fd and not close_sent[0]:
                close_sent[0] = True
                raise close_base
            return result

        def restore_after_reblock(previous):
            real_restore(previous)
            if not restore_sent[0]:
                restore_sent[0] = True
                raise restore_base

        try:
            with mock.patch.object(
                qualifier, "_block_tracking_signals",
                side_effect=reblock_base,
            ), mock.patch.object(
                qualifier.os, "close", side_effect=close_after_reblock,
            ), mock.patch.object(
                qualifier, "_restore_tracking_signals",
                side_effect=restore_after_reblock,
            ):
                with self.assertRaises(KeyboardInterrupt) as caught:
                    qualifier._cleanup_owned_descriptors_and_raise(
                        descriptors, previous_signals,
                        OSError(errno.EIO, "ordinary acquisition primary"),
                        "chronological acquisition cleanup",
                    )
            self.assertIs(caught.exception, reblock_base)
            self.assertEqual(descriptors, [])
            self.assertTrue(close_sent[0])
            self.assertTrue(restore_sent[0])
            report = caught.exception.descriptor_close_report
            self.assertTrue(report["complete"])
            self.assertEqual(
                report["deferred_base_exceptions"],
                [reblock_base, close_base, restore_base],
            )
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, prior_mask)
        self.assertEqual(open_fd_count(), baseline_fds)

        class ChronologicalIterator:
            def __init__(self, primary, close_error):
                self.primary = primary
                self.close_error = close_error
                self.fd = os.open("/dev/null", os.O_RDONLY)
                self.close_calls = 0
                self.closed = False

            def __iter__(self):
                return self

            def __next__(self):
                raise self.primary

            def close(self):
                self.close_calls += 1
                if not self.closed:
                    os.close(self.fd)
                    self.closed = True
                    raise self.close_error

        baseline_fds = open_fd_count()
        directory_flags = (
            os.O_RDONLY | qualifier._required_open_flag("O_CLOEXEC")
            | qualifier._required_open_flag("O_DIRECTORY")
            | qualifier._required_open_flag("O_NOFOLLOW")
        )
        directory_fd = os.open("/private/tmp", directory_flags)
        iteration_base = KeyboardInterrupt("iterator operation BaseException")
        iterator_close_base = KeyboardInterrupt("iterator close BaseException")
        iterator_restore_base = KeyboardInterrupt(
            "iterator restoration BaseException",
        )
        iterator = ChronologicalIterator(
            iteration_base, iterator_close_base,
        )
        real_restore = qualifier._restore_tracking_signals
        restore_sent = [False]

        def iterator_restore_then_base(previous):
            real_restore(previous)
            if not restore_sent[0]:
                restore_sent[0] = True
                raise iterator_restore_base

        try:
            with mock.patch.object(
                qualifier.os, "scandir", return_value=iterator,
            ), mock.patch.object(
                qualifier, "_restore_tracking_signals",
                side_effect=iterator_restore_then_base,
            ):
                with self.assertRaises(KeyboardInterrupt) as caught:
                    qualifier._directory_names(
                        directory_fd, 0,
                        "chronological iterator",
                    )
            self.assertIs(caught.exception, iteration_base)
            self.assertTrue(iterator.closed)
            self.assertEqual(iterator.close_calls, 2)
            self.assertTrue(restore_sent[0])
            self.assertEqual(
                caught.exception.descriptor_close_report[
                    "deferred_base_exceptions"
                ],
                [iterator_close_base, iterator_restore_base],
            )
        finally:
            if not iterator.closed:
                os.close(iterator.fd)
            os.close(directory_fd)
        self.assertEqual(open_fd_count(), baseline_fds)

        def authenticated_read():
            return qualifier._read_authenticated_repository_file(
                REPOSITORY, qualifier.CONTRACT_PATH,
                qualifier.CONTRACT_BYTES, qualifier.CONTRACT_SHA256,
                "chronological authenticated reader",
            )

        baseline_fds = open_fd_count()
        real_close = qualifier.os.close
        close_calls = [0]
        ordinary_sent = [False]

        def ancestry_close_then_ordinary(descriptor):
            result = real_close(descriptor)
            close_calls[0] += 1
            if close_calls[0] == 2 and not ordinary_sent[0]:
                ordinary_sent[0] = True
                raise OSError(errno.EIO, "post-effect ancestry close")
            return result

        with mock.patch.object(
            qualifier.os, "close", side_effect=ancestry_close_then_ordinary,
        ):
            with self.assertRaises(qualifier.CleanupError):
                authenticated_read()
        self.assertTrue(ordinary_sent[0])
        self.assertEqual(open_fd_count(), baseline_fds)

        baseline_fds = open_fd_count()
        operation_base = KeyboardInterrupt(
            "authenticated read operation BaseException",
        )
        reader_close_base = KeyboardInterrupt(
            "authenticated read close BaseException",
        )
        reader_restore_base = KeyboardInterrupt(
            "authenticated read restoration BaseException",
        )
        real_close = qualifier.os.close
        real_restore = qualifier._restore_tracking_signals
        operation_active = [False]
        close_sent = [False]
        restore_sent = [False]

        def read_then_base(*args, **kwargs):
            operation_active[0] = True
            raise operation_base

        def reader_close_then_base(descriptor):
            result = real_close(descriptor)
            if operation_active[0] and not close_sent[0]:
                close_sent[0] = True
                raise reader_close_base
            return result

        def reader_restore_then_base(previous):
            real_restore(previous)
            if operation_active[0] and not restore_sent[0]:
                restore_sent[0] = True
                raise reader_restore_base

        with mock.patch.object(
            qualifier, "_read_fd_exact", side_effect=read_then_base,
        ), mock.patch.object(
            qualifier.os, "close", side_effect=reader_close_then_base,
        ), mock.patch.object(
            qualifier, "_restore_tracking_signals",
            side_effect=reader_restore_then_base,
        ):
            with self.assertRaises(KeyboardInterrupt) as caught:
                authenticated_read()
        self.assertIs(caught.exception, operation_base)
        self.assertTrue(close_sent[0])
        self.assertTrue(restore_sent[0])
        self.assertEqual(open_fd_count(), baseline_fds)
        self.assertEqual(capsule_residue(), [])
        self.assertEqual(targeted_bytecode(), [])

    def test_29_creation_primary_and_cleanup_failure_precedence(self) -> None:
        real_cleanup = qualifier._cleanup_phase_b_capsule
        primary_kinds = ("ordinary", "keyboard", "system-exit")
        cleanup_kinds = ("ordinary", "base")
        cleanup_states = ("proved-complete", "incomplete")

        for primary_kind in primary_kinds:
            for cleanup_kind in cleanup_kinds:
                for cleanup_state in cleanup_states:
                    case = "%s-primary-%s-cleanup-%s" % (
                        primary_kind, cleanup_kind, cleanup_state,
                    )
                    with self.subTest(case=case):
                        baseline_fds = open_fd_count()
                        if primary_kind == "ordinary":
                            primary = qualifier.QualificationError(
                                case + " creation primary",
                            )
                        elif primary_kind == "keyboard":
                            primary = KeyboardInterrupt(
                                case + " creation primary",
                            )
                        else:
                            primary = SystemExit(
                                case + " creation primary",
                            )
                        if cleanup_kind == "ordinary":
                            cleanup_error = qualifier.CleanupError(
                                case + " cleanup terminal error",
                            )
                        else:
                            cleanup_error = KeyboardInterrupt(
                                case + " cleanup BaseException",
                            )
                        captured = []
                        cleanup_evidence = []
                        injected = [False]

                        def fail_after_private_tmp(event):
                            if event == "after-private-tmp-open":
                                injected[0] = True
                                raise primary

                        def cleanup_then_fail(capsule, *args, **kwargs):
                            captured.append(capsule)
                            if cleanup_state == "proved-complete":
                                evidence = real_cleanup(
                                    capsule, require_exact_counts=False,
                                )
                            else:
                                evidence = {
                                    "complete": False,
                                    "private_parent_absent": True,
                                    "stage_files_removed": 0,
                                    "stage_directories_removed": 0,
                                    "capsule_directories_removed": 0,
                                    "synthetic_injected_cleanup_faults": [],
                                }
                            cleanup_evidence.append(evidence)
                            cleanup_error.physical_cleanup_evidence = evidence
                            raise cleanup_error

                        with mock.patch.object(
                            qualifier, "_cleanup_phase_b_capsule",
                            side_effect=cleanup_then_fail,
                        ):
                            with self.assertRaises(BaseException) as caught:
                                qualifier._create_private_capsule(
                                    fault_injector=fail_after_private_tmp,
                                )
                        self.assertTrue(injected[0])
                        self.assertEqual(len(captured), 1)
                        self.assertEqual(len(cleanup_evidence), 1)
                        capsule = captured[0]

                        if not isinstance(primary, Exception):
                            self.assertIs(caught.exception, primary)
                        elif not isinstance(cleanup_error, Exception):
                            self.assertIs(caught.exception, cleanup_error)
                        else:
                            self.assertIsInstance(
                                caught.exception, qualifier.CleanupError,
                            )
                            self.assertIsNot(caught.exception, cleanup_error)

                        if cleanup_state == "proved-complete":
                            evidence = caught.exception.physical_cleanup_evidence
                            self.assertIs(evidence, cleanup_evidence[0])
                            self.assertTrue(evidence["complete"])
                            self.assertTrue(capsule.cleaned)
                            self.assertTrue(
                                caught.exception.cleanup_report[
                                    "cleanup_completed"
                                ],
                            )
                            self.assertFalse(hasattr(
                                caught.exception, "cleanup_incomplete_failure",
                            ))
                            if (
                                isinstance(primary, Exception)
                                and isinstance(cleanup_error, Exception)
                            ):
                                terminal = caught.exception
                            else:
                                terminal = (
                                    caught.exception.cleanup_terminal_failure
                                )
                            self.assertIs(terminal.primary_error, primary)
                            self.assertIs(terminal.cleanup_error, cleanup_error)
                            self.assertFalse(hasattr(
                                terminal, "cleanup_incomplete",
                            ))
                            self.assertEqual(open_fd_count(), baseline_fds)
                        else:
                            selected_evidence = getattr(
                                caught.exception,
                                "physical_cleanup_evidence", None,
                            )
                            self.assertFalse(
                                type(selected_evidence) is dict
                                and selected_evidence.get("complete") is True
                            )
                            self.assertFalse(capsule.cleaned)
                            if (
                                isinstance(primary, Exception)
                                and isinstance(cleanup_error, Exception)
                            ):
                                incomplete = caught.exception
                            else:
                                incomplete = (
                                    caught.exception.cleanup_incomplete_failure
                                )
                            self.assertTrue(incomplete.cleanup_incomplete)
                            self.assertIs(incomplete.primary_error, primary)
                            self.assertIs(
                                incomplete.cleanup_error, cleanup_error,
                            )
                            self.assertFalse(
                                incomplete.cleanup_report["cleanup_completed"],
                            )
                            residual = incomplete.cleanup_residual_state
                            self.assertFalse(residual["capsule_cleaned"])
                            self.assertEqual(
                                residual["private_tmp_descriptor_count"], 3,
                            )
                            self.assertTrue(
                                residual[
                                    "private_tmp_terminal_registered"
                                ],
                            )
                            self.assertFalse(
                                residual["capsule_name_registered"],
                            )
                            self.assertFalse(
                                residual[
                                    "private_capsule_descriptor_registered"
                                ],
                            )
                            self.assertEqual(
                                residual["child_descriptor_names"], [],
                            )
                            self.assertEqual(
                                open_fd_count(), baseline_fds + 3,
                            )
                            recovered = real_cleanup(
                                capsule, require_exact_counts=False,
                            )
                            self.assertTrue(recovered["complete"])
                            self.assertTrue(capsule.cleaned)
                            self.assertEqual(open_fd_count(), baseline_fds)
                        self.assertEqual(capsule_residue(), [])
                        self.assertEqual(targeted_bytecode(), [])


if __name__ == "__main__":
    unittest.main()
