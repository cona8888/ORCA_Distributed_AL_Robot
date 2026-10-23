#!/usr/bin/env python3
"""Adversarial tests for the nonexecuting locked ISO qualifier driver."""

from __future__ import annotations

import ast
from contextlib import redirect_stderr, redirect_stdout
import ctypes
import errno
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
DRIVER = ROOT / "scripts/comparison/run_locked_physics_iso_source_capsule_qualification_v1.py"
BINDING = ROOT / "docs/simulator/physics_iso_source_capsule_locked_implementation_binding_v1.json"
DRIVER_BYTES = 47_588
DRIVER_SHA256 = "a15637b595902ba412e4cf0d2128fbc6840a92f1e5865b93d76cfd16c3a5ac4c"


def _load_driver():
    raw = DRIVER.read_bytes()
    if len(raw) != DRIVER_BYTES or hashlib.sha256(raw).hexdigest() != DRIVER_SHA256:
        raise AssertionError("locked driver exact identity differs")
    module = types.ModuleType("locked_iso_driver_under_test")
    module.__file__ = str(DRIVER)
    module.__package__ = None
    source = raw.decode("utf-8", "strict")
    code = compile(source, str(DRIVER), "exec", dont_inherit=True, optimize=0)
    exec(code, module.__dict__)
    return module


class LockedIsoDriverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_driver()

    def _handler_snapshot(self):
        return {
            number: signal.getsignal(number)
            for number in self.module._controlled_signals()
        }

    @staticmethod
    def _mask_snapshot():
        return {int(item) for item in signal.pthread_sigmask(signal.SIG_BLOCK, set())}

    @staticmethod
    def _fd_snapshot():
        result = {}
        for descriptor in range(4096):
            try:
                info = os.fstat(descriptor)
            except OSError:
                continue
            result[descriptor] = (
                info.st_dev, info.st_ino, info.st_mode, info.st_rdev,
            )
        return result

    @classmethod
    def _fd_count(cls) -> int:
        return len(cls._fd_snapshot())

    def test_binding_is_exact_canonical_parent_implementation_binding(self) -> None:
        raw = BINDING.read_bytes()
        self.assertEqual(len(raw), self.module.BINDING_BYTES)
        self.assertEqual(
            __import__("hashlib").sha256(raw).hexdigest(),
            self.module.BINDING_SHA256,
        )
        value = json.loads(raw)
        canonical = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8") + b"\n"
        self.assertEqual(raw, canonical)
        self.assertEqual(value["checkpoint_commit"], self.module.CHECKPOINT_COMMIT)
        self.assertEqual(value["checkpoint_tree"], self.module.CHECKPOINT_TREE)
        self.assertEqual(
            [item["path"] for item in value["implementations"]],
            [self.module.QUALIFIER_PATH, self.module.VERIFIER_PATH],
        )

    def test_binding_semantic_mutations_are_rejected(self) -> None:
        original = json.loads(BINDING.read_bytes())
        mutations = []
        changed_commit = json.loads(json.dumps(original))
        changed_commit["checkpoint_commit"] = "0" * 40
        mutations.append(changed_commit)
        changed_tree = json.loads(json.dumps(original))
        changed_tree["checkpoint_tree"] = "1" * 40
        mutations.append(changed_tree)
        changed_role = json.loads(json.dumps(original))
        changed_role["implementations"][0]["role"] = "other"
        mutations.append(changed_role)
        changed_bytes = json.loads(json.dumps(original))
        changed_bytes["implementations"][1]["bytes"] += 1
        mutations.append(changed_bytes)
        reversed_implementations = json.loads(json.dumps(original))
        reversed_implementations["implementations"].reverse()
        mutations.append(reversed_implementations)
        for value in mutations:
            raw = json.dumps(
                value, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8") + b"\n"
            with self.assertRaises(self.module.DriverError):
                self.module._strict_binding(raw)

    def test_descriptor_reads_reject_symlink_hardlink_mode_and_rename(self) -> None:
        def session_for(root: Path):
            session = self.module._DescriptorSession(self.module._controlled_signals())
            descriptor = session.open_repository(root)
            return session, descriptor

        with tempfile.TemporaryDirectory(dir=str(ROOT)) as temporary:
            root = Path(temporary)

            target = root / "target"
            target.write_bytes(b"target")
            (root / "link").symlink_to(target.name)
            session, descriptor = session_for(root)
            try:
                with self.assertRaises(BaseException):
                    session.read_regular(descriptor, "link", 64)
            finally:
                self.module._close_session_bounded(session)

            primary = root / "primary"
            primary.write_bytes(b"hard")
            os.link(primary, root / "hardlink")
            session, descriptor = session_for(root)
            try:
                with self.assertRaises(self.module.DriverError):
                    session.read_regular(descriptor, "primary", 64)
            finally:
                self.module._close_session_bounded(session)

            writable = root / "writable"
            writable.write_bytes(b"mode")
            writable.chmod(0o664)
            session, descriptor = session_for(root)
            try:
                with self.assertRaises(self.module.DriverError):
                    session.read_regular(descriptor, "writable", 64)
            finally:
                self.module._close_session_bounded(session)

            stable = root / "stable"
            stable.write_bytes(b"stable")
            session, descriptor = session_for(root)
            try:
                self.assertEqual(session.read_regular(descriptor, "stable", 64), b"stable")
                stable.rename(root / "renamed")
                with self.assertRaises(BaseException):
                    session.reauthenticate()
            finally:
                self.module._close_session_bounded(session)

    def test_source_has_no_materialization_or_external_execution_surface(self) -> None:
        source = DRIVER.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = set()
        forbidden_calls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "os"
                    and node.func.attr in {
                        "chmod", "chown", "link", "makedirs", "mkdir",
                        "remove", "rename", "replace", "rmdir", "symlink",
                        "unlink", "write",
                    }
                ):
                    forbidden_calls.append(node.func.attr)
        self.assertTrue(imports.isdisjoint({"socket", "subprocess", "urllib"}))
        self.assertEqual(forbidden_calls, [])
        self.assertNotIn("sys.stdout", source)
        self.assertNotIn("/usr/bin/hdiutil", source)
        self.assertNotIn("/usr/sbin/diskutil", source)
        self.assertNotIn("mount_cd9660", source)
        self.assertIn("load_authenticated_expected_binding", source)
        self.assertIn("authenticate_implementation_checkpoint", source)

    def test_internal_verdict_has_the_exact_nonexecution_schema(self) -> None:
        raw = self.module.SUCCESS_VERDICT
        value = json.loads(raw)
        self.assertEqual(
            raw,
            json.dumps(
                value, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), allow_nan=False,
            ).encode("utf-8") + b"\n",
        )
        self.assertEqual(set(value), {
            "binding_file_bytes", "binding_file_sha256", "checkpoint_commit",
            "checkpoint_tree", "controlled_signal_names", "handlers_installed",
            "handlers_restored", "image_attached", "image_created", "mount_created",
            "native_thread_count_at_entry", "phase_a_authenticated",
            "qualification_receipt_emitted", "qualifier_live_guard_observed",
            "schema_version", "verifier_checkpoint_authenticated",
        })
        self.assertEqual(value["native_thread_count_at_entry"], 1)
        self.assertTrue(value["handlers_installed"])
        self.assertTrue(value["handlers_restored"])
        self.assertTrue(value["phase_a_authenticated"])
        self.assertTrue(value["qualifier_live_guard_observed"])
        self.assertTrue(value["verifier_checkpoint_authenticated"])
        for key in (
            "image_attached", "image_created", "mount_created",
            "qualification_receipt_emitted",
        ):
            self.assertIs(value[key], False)

    def test_cli_never_emits_the_internal_verdict(self) -> None:
        for outcome in (self.module.SUCCESS_VERDICT, self.module.DriverError("no")):
            stdout = io.StringIO()
            stderr = io.StringIO()
            patcher = (
                mock.patch.object(self.module, "run_nonexecuting_checkpoint", return_value=outcome)
                if isinstance(outcome, bytes)
                else mock.patch.object(self.module, "run_nonexecuting_checkpoint", side_effect=outcome)
            )
            with patcher, redirect_stdout(stdout), redirect_stderr(stderr):
                self.assertEqual(self.module.main([]), 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), self.module.RUNTIME_FAILURE)
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(self.module.main(["unexpected"]), 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), self.module.ARGUMENT_FAILURE)

    def test_actual_cli_is_zero_stdout_and_nonzero_for_successful_guard_and_arguments(self) -> None:
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        guarded = subprocess.run(
            [sys.executable, "-B", str(DRIVER)], cwd=str(ROOT), env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            timeout=60, check=False,
        )
        self.assertEqual(guarded.returncode, 1)
        self.assertEqual(guarded.stdout, b"")
        self.assertEqual(guarded.stderr, self.module.RUNTIME_FAILURE.encode("utf-8"))
        arguments = subprocess.run(
            [sys.executable, "-B", str(DRIVER), "unexpected"], cwd=str(ROOT),
            env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=10, check=False,
        )
        self.assertEqual(arguments.returncode, 2)
        self.assertEqual(arguments.stdout, b"")
        self.assertEqual(arguments.stderr, self.module.ARGUMENT_FAILURE.encode("utf-8"))

    def test_bootstrap_has_the_exact_six_keys_and_parent_bindings(self) -> None:
        binding = json.loads(BINDING.read_bytes())
        observed = self.module._bootstrap(binding)
        self.assertEqual(set(observed), {
            "checkpoint_commit", "checkpoint_tree",
            "driver_authenticated_qualifier",
            "driver_authenticated_receipt_verifier", "qualifier",
            "receipt_verifier",
        })
        self.assertEqual(observed["checkpoint_commit"], self.module.CHECKPOINT_COMMIT)
        self.assertEqual(observed["checkpoint_tree"], self.module.CHECKPOINT_TREE)
        self.assertIs(observed["driver_authenticated_qualifier"], True)
        self.assertIs(observed["driver_authenticated_receipt_verifier"], True)
        self.assertEqual(observed["qualifier"], binding["implementations"][0])
        self.assertEqual(observed["receipt_verifier"], binding["implementations"][1])

    def test_orchestration_authenticates_verifier_before_qualifier_entry(self) -> None:
        events = []
        binding = json.loads(BINDING.read_bytes())
        qualifier = b"authenticated qualifier"
        verifier = b"authenticated verifier"

        class FakeSession:
            def __init__(self, _signals):
                events.append("session")

            def open_repository(self, _repository):
                events.append("open")
                return 17

        class FakeEnvelope:
            def __init__(self):
                self.active = False
                self.restoration_complete = False

            def install(self):
                events.append("install")
                self.active = True

            def pre_entry_census(self):
                events.append("pre-entry")
                return 1

        proof_count = {"value": 0}

        def proof():
            proof_count["value"] += 1
            events.append("proof-%d" % proof_count["value"])

        def authenticate(_session, descriptor, _repository):
            self.assertEqual(descriptor, 17)
            events.append("manual-auth")
            return binding, qualifier, verifier

        def close(_session):
            events.append("descriptor-close")

        def verify(raw, _repository, observed_binding, observed_q, observed_v):
            self.assertEqual((raw, observed_q, observed_v), (verifier, qualifier, verifier))
            self.assertEqual(observed_binding, binding)
            events.append("verifier")

        def qualify(raw, _repository, bootstrap):
            self.assertEqual(raw, qualifier)
            self.assertEqual(set(bootstrap), {
                "checkpoint_commit", "checkpoint_tree",
                "driver_authenticated_qualifier",
                "driver_authenticated_receipt_verifier", "qualifier",
                "receipt_verifier",
            })
            events.append("qualifier")

        def restore(envelope):
            events.append("restore")
            envelope.active = False
            envelope.restoration_complete = True

        with (
            mock.patch.object(self.module, "_prove_single_main_thread", side_effect=proof),
            mock.patch.object(self.module, "_DescriptorSession", FakeSession),
            mock.patch.object(self.module, "_authenticate_checkpoint", side_effect=authenticate),
            mock.patch.object(self.module, "_close_session_bounded", side_effect=close),
            mock.patch.object(self.module, "_execute_authenticated_verifier", side_effect=verify),
            mock.patch.object(self.module, "_SignalEnvelope", FakeEnvelope),
            mock.patch.object(self.module, "_execute_authenticated_qualifier", side_effect=qualify),
            mock.patch.object(self.module, "_restore_envelope_bounded", side_effect=restore),
        ):
            self.assertEqual(
                self.module.run_nonexecuting_checkpoint(), self.module.SUCCESS_VERDICT,
            )
        self.assertEqual(events, [
            "proof-1", "session", "open", "manual-auth", "descriptor-close",
            "verifier", "proof-2", "install", "pre-entry", "qualifier", "restore",
        ])

    def test_exact_verifier_api_rejects_any_returned_raw_mismatch(self) -> None:
        binding = json.loads(BINDING.read_bytes())
        qualifier = b"q"
        expected_verifier = b"v"

        def verifier_source(returned_verifier: bytes) -> bytes:
            return (
                "from pathlib import Path\n"
                "REPOSITORY = Path(%r)\n"
                "BINDING = %r\n"
                "def load_authenticated_expected_binding(path, size, digest):\n"
                "    assert path == REPOSITORY / %r\n"
                "    assert size == %r and digest == %r\n"
                "    return BINDING\n"
                "def authenticate_implementation_checkpoint(value):\n"
                "    assert value == BINDING\n"
                "    return value, (%r, %r)\n"
                % (
                    str(ROOT), binding, self.module.BINDING_PATH,
                    self.module.BINDING_BYTES, self.module.BINDING_SHA256,
                    qualifier, returned_verifier,
                )
            ).encode("utf-8")

        self.module._execute_authenticated_verifier(
            verifier_source(expected_verifier), ROOT, binding,
            qualifier, expected_verifier,
        )
        with self.assertRaises(self.module.DriverError):
            self.module._execute_authenticated_verifier(
                verifier_source(b"wrong"), ROOT, binding,
                qualifier, expected_verifier,
            )

    def test_callable_handler_comparison_is_identity_only(self) -> None:
        class AdversarialCallable:
            def __call__(self, *_args):
                return None

            def __eq__(self, _other):
                return True

        left = AdversarialCallable()
        right = AdversarialCallable()
        self.assertFalse(self.module._SignalEnvelope._handler_equal(left, right))
        self.assertTrue(self.module._SignalEnvelope._handler_equal(left, left))
        self.assertTrue(self.module._SignalEnvelope._handler_equal(signal.SIG_DFL, signal.SIG_DFL))
        self.assertFalse(self.module._SignalEnvelope._handler_equal(signal.SIG_DFL, signal.SIG_IGN))

    def test_native_census_rejects_zero_truncated_two_oversized_and_nonmain(self) -> None:
        controlled = set(self.module._controlled_signals())

        class Function:
            def __init__(self, implementation):
                self.implementation = implementation
                self.argtypes = None
                self.restype = None

            def __call__(self, *arguments):
                return self.implementation(*arguments)

        def libraries(returned: int, main: int = 1):
            def proc(_pid, _flavor, _arg, buffer, _size):
                values = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_uint64))
                values[0] = 41
                values[1] = 42 if returned >= 16 else 0
                return returned

            return (
                types.SimpleNamespace(pthread_main_np=Function(lambda: main)),
                types.SimpleNamespace(proc_pidinfo=Function(proc)),
            )

        for label, returned, main in (
            ("zero", 0, 1), ("truncated", 4, 1), ("two", 16, 1),
            ("oversized", 24, 1), ("not-main", 8, 0),
        ):
            with self.subTest(label=label):
                system, libproc = libraries(returned, main)
                with (
                    mock.patch.object(self.module.ctypes, "CDLL", side_effect=[system, libproc]),
                    mock.patch.object(self.module, "_query_mask", return_value=controlled),
                ):
                    with self.assertRaises(self.module.DriverError):
                        self.module._native_thread_census_blocked(controlled)

    def test_initially_blocked_controlled_signal_is_rejected_and_restored(self) -> None:
        baseline = self._mask_snapshot()
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        blocked = self._mask_snapshot()
        self.assertIn(int(signal.SIGTERM), blocked)
        try:
            with self.assertRaises(self.module.DriverError):
                self.module._prove_single_main_thread()
            self.assertEqual(self._mask_snapshot(), blocked)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, baseline)
        self.assertEqual(self._mask_snapshot(), baseline)

    def test_entry_thread_proof_rejects_a_python_worker(self) -> None:
        ready = threading.Event()
        release = threading.Event()

        def worker() -> None:
            ready.set()
            release.wait(5)

        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(ready.wait(5))
        try:
            with self.assertRaises(self.module.DriverError):
                self.module._prove_single_main_thread()
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())

    def test_initial_mask_post_effect_cancellation_is_restored(self) -> None:
        before_handlers = self._handler_snapshot()
        before_mask = self._mask_snapshot()
        real_mask = self.module._mask
        controlled = set(self.module._controlled_signals())
        fired = {"value": False}

        def injected(operation, values):
            result = real_mask(operation, values)
            if (
                not fired["value"] and operation == signal.SIG_BLOCK
                and set(values) == controlled
            ):
                fired["value"] = True
                raise self.module.DriverCancellation("after block")
            return result

        envelope = self.module._SignalEnvelope()
        with mock.patch.object(self.module, "_mask", side_effect=injected):
            with self.assertRaises(self.module.DriverCancellation):
                envelope.install()
        self.assertTrue(envelope.restoration_complete)
        self.assertFalse(envelope.active)
        self.assertEqual(self._handler_snapshot(), before_handlers)
        self.assertEqual(self._mask_snapshot(), before_mask)

    def test_proof_mask_post_effect_cancellation_is_restored(self) -> None:
        before = self._mask_snapshot()
        real_mask = self.module._mask
        controlled = set(self.module._controlled_signals())
        fired = {"value": False}

        def injected(operation, values):
            result = real_mask(operation, values)
            if (
                not fired["value"] and operation == signal.SIG_BLOCK
                and set(values) == controlled
            ):
                fired["value"] = True
                raise self.module.DriverCancellation("after proof block")
            return result

        with mock.patch.object(self.module, "_mask", side_effect=injected):
            with self.assertRaises(self.module.DriverCancellation):
                self.module._prove_single_main_thread()
        self.assertEqual(self._mask_snapshot(), before)

    def test_handler_install_post_effect_cancellation_rolls_back_all(self) -> None:
        before_handlers = self._handler_snapshot()
        before_mask = self._mask_snapshot()
        envelope = self.module._SignalEnvelope()
        target = envelope.signals[1]
        real_signal = self.module.signal.signal
        fired = {"value": False}

        def injected(number, handler):
            result = real_signal(number, handler)
            if (
                not fired["value"] and number == target
                and handler is self.module._controlled_signal_handler
            ):
                fired["value"] = True
                raise self.module.DriverCancellation("after handler install")
            return result

        with mock.patch.object(self.module.signal, "signal", side_effect=injected):
            with self.assertRaises(self.module.DriverCancellation):
                envelope.install()
        self.assertTrue(envelope.restoration_complete)
        self.assertFalse(envelope.active)
        self.assertEqual(self._handler_snapshot(), before_handlers)
        self.assertEqual(self._mask_snapshot(), before_mask)

    def test_install_rollback_reblocks_after_two_post_effect_cancellations(self) -> None:
        before_handlers = self._handler_snapshot()
        before_mask = self._mask_snapshot()
        envelope = self.module._SignalEnvelope()
        real_mask = self.module._mask
        controlled = set(envelope.signals)
        first = self.module.DriverCancellation("pending-one")
        second = self.module.DriverCancellation("pending-two")
        state = {"unmask": False, "reblock": False}

        def injected(operation, values):
            result = real_mask(operation, values)
            if (
                not state["unmask"] and operation == signal.SIG_SETMASK
                and set(values) == before_mask
            ):
                state["unmask"] = True
                raise first
            if (
                state["unmask"] and not state["reblock"]
                and operation == signal.SIG_BLOCK and set(values) == controlled
            ):
                state["reblock"] = True
                raise second
            return result

        with mock.patch.object(self.module, "_mask", side_effect=injected):
            with self.assertRaises(self.module.DriverCancellation) as caught:
                envelope.install()
        self.assertIs(caught.exception, first)
        self.assertIs(getattr(first, "locked_driver_cleanup_error", None), second)
        self.assertTrue(envelope.restoration_complete)
        self.assertFalse(envelope.active)
        self.assertEqual(self._handler_snapshot(), before_handlers)
        self.assertEqual(self._mask_snapshot(), before_mask)

    def test_handler_restore_post_effect_base_is_preserved_after_full_restore(self) -> None:
        before_handlers = self._handler_snapshot()
        before_mask = self._mask_snapshot()
        envelope = self.module._SignalEnvelope()
        envelope.install()
        target = envelope.signals[1]
        prior = envelope.previous_handlers[target]
        real_signal = self.module.signal.signal
        fired = {"value": False}

        def injected(number, handler):
            result = real_signal(number, handler)
            if not fired["value"] and number == target and handler is prior:
                fired["value"] = True
                raise self.module.DriverCancellation("after handler restore")
            return result

        try:
            with mock.patch.object(self.module.signal, "signal", side_effect=injected):
                with self.assertRaises(self.module.DriverCancellation):
                    envelope.restore()
        finally:
            if envelope.active:
                self.module._restore_envelope_bounded(envelope)
        self.assertTrue(envelope.restoration_complete)
        self.assertEqual(self._handler_snapshot(), before_handlers)
        self.assertEqual(self._mask_snapshot(), before_mask)

    def test_persistent_restoration_failure_suppresses_any_verdict(self) -> None:
        before_handlers = self._handler_snapshot()
        before_mask = self._mask_snapshot()
        envelope = self.module._SignalEnvelope()
        envelope.install()
        target = envelope.signals[0]
        prior = envelope.previous_handlers[target]
        real_signal = self.module.signal.signal

        def refuse(number, handler):
            if number == target and handler is prior:
                raise self.module.DriverCancellation("refuse restore")
            return real_signal(number, handler)

        try:
            with mock.patch.object(self.module.signal, "signal", side_effect=refuse):
                with self.assertRaises(self.module.DriverCancellation):
                    self.module._restore_envelope_bounded(envelope)
            self.assertFalse(envelope.restoration_complete)
        finally:
            signal.pthread_sigmask(signal.SIG_BLOCK, set(envelope.signals))
            for number in reversed(envelope.signals):
                real_signal(number, envelope.previous_handlers[number])
            signal.pthread_sigmask(signal.SIG_SETMASK, envelope.previous_mask)
            envelope.active = False
            envelope.restoration_complete = True
        self.assertEqual(self._handler_snapshot(), before_handlers)
        self.assertEqual(self._mask_snapshot(), before_mask)

    def test_descriptor_registration_post_effect_cancellation_leaks_no_fd(self) -> None:
        class PostEffectList(list):
            fired = False

            def __setitem__(self, key, value):
                super().__setitem__(key, value)
                if not self.fired:
                    self.fired = True
                    raise self_module.DriverCancellation("after fd registration")

        self_module = self.module
        before = self._fd_count()
        session = self.module._DescriptorSession(self.module._controlled_signals())
        session.fds = PostEffectList()
        with self.assertRaises(self.module.DriverCancellation):
            session._open_tracked(os.devnull, os.O_RDONLY)
        self.module._close_session_bounded(session)
        self.assertEqual(self._fd_count(), before)

    def test_descriptor_close_post_effect_base_attempts_every_fd(self) -> None:
        before = self._fd_count()
        read_fd, write_fd = os.pipe()
        session = self.module._DescriptorSession(self.module._controlled_signals())
        session.fds = [read_fd, write_fd]
        real_close = self.module.os.close
        fired = {"value": False}

        def injected(descriptor):
            result = real_close(descriptor)
            if not fired["value"] and descriptor == write_fd:
                fired["value"] = True
                raise self.module.DriverCancellation("after close")
            return result

        try:
            with mock.patch.object(self.module.os, "close", side_effect=injected):
                with self.assertRaises(self.module.DriverCancellation):
                    session.close()
            self.assertTrue(session.closed)
            for descriptor in (read_fd, write_fd):
                with self.assertRaises(OSError) as caught:
                    os.fstat(descriptor)
                self.assertEqual(caught.exception.errno, errno.EBADF)
        finally:
            for descriptor in (read_fd, write_fd):
                try:
                    real_close(descriptor)
                except OSError:
                    pass
        self.assertEqual(self._fd_count(), before)

    def test_qualifier_guard_must_be_the_exact_exception_and_message(self) -> None:
        expected_source = (
            "class QualificationError(RuntimeError):\n"
            "    pass\n"
            "def run_from_authenticated_driver(_bootstrap):\n"
            "    raise QualificationError(%r)\n" % self.module.EXPECTED_GUARD
        ).encode("utf-8")
        self.module._execute_authenticated_qualifier(expected_source, ROOT, {})
        receipt_source = (
            "class QualificationError(RuntimeError):\n"
            "    pass\n"
            "def run_from_authenticated_driver(_bootstrap):\n"
            "    return b'receipt'\n"
        ).encode("utf-8")
        with self.assertRaises(self.module.DriverError):
            self.module._execute_authenticated_qualifier(receipt_source, ROOT, {})
        wrong_source = (
            "class QualificationError(RuntimeError):\n"
            "    pass\n"
            "def run_from_authenticated_driver(_bootstrap):\n"
            "    raise QualificationError('wrong guard')\n"
        ).encode("utf-8")
        with self.assertRaises(Exception) as caught:
            self.module._execute_authenticated_qualifier(wrong_source, ROOT, {})
        self.assertEqual(type(caught.exception).__name__, "QualificationError")

    def test_z_full_checkpoint_returns_only_internal_nonexecution_verdict(self) -> None:
        before_handlers = self._handler_snapshot()
        before_mask = self._mask_snapshot()
        cold_fds = self._fd_snapshot()
        os.urandom(1)
        before_fds = self._fd_snapshot()
        warm_delta = set(before_fds) - set(cold_fds)
        self.assertLessEqual(len(warm_delta), 1)
        if warm_delta:
            descriptor = next(iter(warm_delta))
            info = os.fstat(descriptor)
            self.assertTrue(stat.S_ISCHR(info.st_mode))
            path = fcntl.fcntl(descriptor, 50, b"\x00" * 1024)
            self.assertEqual(path.split(b"\x00", 1)[0], b"/dev/urandom")
            self.assertTrue(fcntl.fcntl(descriptor, fcntl.F_GETFD) & fcntl.FD_CLOEXEC)
        before_residue = sorted(
            path.name for path in Path("/private/tmp").glob("dancers-physics-iso-qual-v1-*")
        )
        observed = self.module.run_nonexecuting_checkpoint()
        after_first_fds = self._fd_snapshot()
        self.assertEqual(after_first_fds, before_fds)
        # Older system Python lazily retains one CLOEXEC /dev/urandom runtime
        # descriptor; warm it before the baseline and never close it behind the
        # runtime.  A second complete pass must remain descriptor-stable.
        self.assertEqual(
            self.module.run_nonexecuting_checkpoint(), self.module.SUCCESS_VERDICT,
        )
        after_second_fds = self._fd_snapshot()
        after_residue = sorted(
            path.name for path in Path("/private/tmp").glob("dancers-physics-iso-qual-v1-*")
        )
        self.assertEqual(observed, self.module.SUCCESS_VERDICT)
        self.assertEqual(self._handler_snapshot(), before_handlers)
        self.assertEqual(self._mask_snapshot(), before_mask)
        self.assertEqual(after_second_fds, after_first_fds)
        self.assertEqual(after_residue, before_residue)


if __name__ == "__main__":
    unittest.main()
