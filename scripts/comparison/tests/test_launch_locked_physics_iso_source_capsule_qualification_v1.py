#!/usr/bin/env python3
"""Isolated adversarial tests for the external locked-driver launcher.

The tests in this file never enable the descendant-custody gate and never
spawn the isolated child.  Transport and teardown paths are exercised only
with synthetic state and patched process effects.
"""

from __future__ import annotations

import ast
from contextlib import redirect_stderr, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[3]
LAUNCHER = (
    ROOT
    / "scripts/comparison/launch_locked_physics_iso_source_capsule_qualification_v1.py"
)
LOCK = (
    ROOT
    / "docs/simulator/physics_iso_source_capsule_external_launcher_lock_v1.json"
)
DRIVER = (
    ROOT
    / "scripts/comparison/run_locked_physics_iso_source_capsule_qualification_v1.py"
)

# Provisional source-audit identity.  This pin must move with any audited
# launcher edit and becomes final only when the source-level review says GO.
LAUNCHER_BYTES = 134_719
LAUNCHER_SHA256 = "ac826eed059477023bafdfa8d9c737433a77bf5d8299f95234c914e9d3af4b63"
LOCK_BYTES = 1_663
LOCK_SHA256 = "09e38f00fb277d2286829551646edc5c4b30db937d051d5bdc6eeb6aeb99647c"
DRIVER_BYTES = 47_588
DRIVER_SHA256 = "a15637b595902ba412e4cf0d2128fbc6840a92f1e5865b93d76cfd16c3a5ac4c"


def _load_launcher():
    """Compile exact source bytes directly, without an import cache."""

    raw = LAUNCHER.read_bytes()
    if len(raw) != LAUNCHER_BYTES:
        raise AssertionError("provisional launcher byte count differs")
    if hashlib.sha256(raw).hexdigest() != LAUNCHER_SHA256:
        raise AssertionError("provisional launcher digest differs")
    module = types.ModuleType("external_locked_iso_launcher_under_test")
    module.__file__ = str(LAUNCHER)
    module.__package__ = None
    code = compile(
        raw.decode("utf-8", "strict"), str(LAUNCHER), "exec",
        dont_inherit=True, optimize=0,
    )
    exec(code, module.__dict__)
    return module


class FatalProbe(BaseException):
    """A non-Exception fault used to exercise BaseException precedence."""


class RetainedCustody(BaseException):
    """Sentinel replacing the launcher's intentional infinite retention."""


class ExternalLockedIsoLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = _load_launcher()

    def _state(self):
        state = self.module._TransportState()
        state.repository = str(ROOT)
        return state

    def _custody_frame(
        self, kind: int, sequence: int, digest: bytes, *, pid: int = 0,
        ppid: int = 0, pgid: int = 0, uid: int = 0,
        start_sec: int = 0, start_usec: int = 0,
        path_digest: bytes = b"\0" * 32,
    ) -> bytes:
        return self.module.GIT_CUSTODY_FRAME.pack(
            b"DGIT", 1, kind, sequence, pid, ppid, pgid, uid,
            start_sec, start_usec, digest, path_digest,
        )

    @staticmethod
    def _canonical_json(value) -> bytes:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8") + b"\n"

    def test_exact_lock_and_locked_driver_are_loaded_from_raw_bytes(self) -> None:
        lock_raw = LOCK.read_bytes()
        driver_raw = DRIVER.read_bytes()
        self.assertEqual(len(lock_raw), LOCK_BYTES)
        self.assertEqual(hashlib.sha256(lock_raw).hexdigest(), LOCK_SHA256)
        self.assertEqual(len(driver_raw), DRIVER_BYTES)
        self.assertEqual(hashlib.sha256(driver_raw).hexdigest(), DRIVER_SHA256)

        value = json.loads(lock_raw)
        self.assertEqual(lock_raw, self._canonical_json(value))
        self.assertEqual(self.module._strict_invocation_binding(lock_raw), value)
        self.assertEqual(value["checkpoint_commit"]["oid"], self.module.CHECKPOINT_COMMIT)
        self.assertEqual(
            [item["path"] for item in value["locked_files"]],
            [self.module.LOCKED_BINDING_PATH, self.module.LOCKED_DRIVER_PATH],
        )

    def test_exact_descriptor_loader_returns_the_committed_raw_driver(self) -> None:
        session = self.module._DescriptorSession(self.module._controlled_signals())
        primary = None
        invocation = None
        observed = None
        try:
            repository_fd = session.open_repository(ROOT)
            invocation, observed = self.module._authenticate_launcher_inputs(
                session, repository_fd,
            )
        except BaseException as exc:
            primary = exc
        try:
            self.module._close_session_bounded(session)
        except BaseException as exc:
            primary = self.module._merge_failure(
                primary, exc, "test descriptor cleanup failed",
            )
        if primary is not None:
            raise primary
        self.assertIsNotNone(invocation)
        self.assertEqual(observed, DRIVER.read_bytes())
        self.assertEqual(invocation["locked_files"][1]["sha256"], DRIVER_SHA256)

    def test_lock_noncanonical_and_semantic_mutations_are_rejected(self) -> None:
        original = json.loads(LOCK.read_bytes())
        mutations = []

        wrong_commit = json.loads(json.dumps(original))
        wrong_commit["checkpoint_commit"]["oid"] = "0" * 40
        mutations.append(self._canonical_json(wrong_commit))

        reversed_files = json.loads(json.dumps(original))
        reversed_files["locked_files"].reverse()
        mutations.append(self._canonical_json(reversed_files))

        wrong_role = json.loads(json.dumps(original))
        wrong_role["locked_files"][1]["role"] = "executable driver"
        mutations.append(self._canonical_json(wrong_role))

        mutations.extend((LOCK.read_bytes().rstrip(b"\n"), b"{}\n"))
        for raw in mutations:
            with self.subTest(digest=hashlib.sha256(raw).hexdigest()):
                with self.assertRaises(self.module.LauncherError):
                    self.module._strict_invocation_binding(raw)

    def test_categorical_disable_precedes_every_child_capability(self) -> None:
        self.assertFalse(hasattr(self.module, "DESCENDANT_CUSTODY_PROVED"))
        raw = DRIVER.read_bytes()
        envelope = mock.Mock(side_effect=AssertionError("envelope capability reached"))
        state = mock.Mock(side_effect=AssertionError("transport capability reached"))
        spawn = mock.Mock(side_effect=AssertionError("spawn capability reached"))
        pipe = mock.Mock(side_effect=AssertionError("pipe capability reached"))
        selector = mock.Mock(side_effect=AssertionError("selector capability reached"))
        # Even injecting the former mutable attribute cannot authorize this
        # exact source: the executable path contains no reference to it.
        setattr(self.module, "DESCENDANT_CUSTODY_PROVED", True)
        try:
            with (
                mock.patch.object(self.module, "_SignalEnvelope", envelope),
                mock.patch.object(self.module, "_TransportState", state),
                mock.patch.object(self.module.os, "posix_spawn", spawn),
                mock.patch.object(self.module.os, "pipe", pipe),
                mock.patch.object(self.module.selectors, "DefaultSelector", selector),
            ):
                with self.assertRaises(self.module.LauncherError) as caught:
                    self.module._execute_locked_driver(raw, ROOT)
        finally:
            delattr(self.module, "DESCENDANT_CUSTODY_PROVED")
        self.assertEqual(
            str(caught.exception),
            "external launcher child transport is categorically disabled",
        )
        self.assertFalse(hasattr(self.module, "DESCENDANT_CUSTODY_PROVED"))
        envelope.assert_not_called()
        state.assert_not_called()
        spawn.assert_not_called()
        pipe.assert_not_called()
        selector.assert_not_called()

    def test_unconditional_raise_structurally_dominates_transport_capabilities(self) -> None:
        source = LAUNCHER.read_text(encoding="utf-8")
        self.assertNotIn("DESCENDANT_CUSTODY_PROVED", source)
        tree = ast.parse(source)
        execute = None
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "_execute_locked_driver":
                execute = node
        self.assertIsNotNone(execute)

        categorical_raises = [
            statement for statement in execute.body
            if isinstance(statement, ast.Raise)
            and any(
                isinstance(item, ast.Constant)
                and item.value
                == "external launcher child transport is categorically disabled"
                for item in ast.walk(statement)
            )
        ]
        self.assertEqual(len(categorical_raises), 1)
        categorical_raise = categorical_raises[0]
        capability_lines = {}
        for node in ast.walk(execute):
            if not isinstance(node, ast.Call):
                continue
            if (
                isinstance(node.func, ast.Name)
                and node.func.id in {"_SignalEnvelope", "_TransportState"}
            ):
                capability_lines.setdefault(node.func.id, []).append(node.lineno)
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in {"pipe", "create_selector", "posix_spawn"}
            ):
                capability_lines.setdefault(node.func.attr, []).append(node.lineno)
        self.assertEqual(set(capability_lines), {
            "_SignalEnvelope", "_TransportState", "pipe", "create_selector",
            "posix_spawn",
        })
        for label, lines in capability_lines.items():
            with self.subTest(capability=label):
                self.assertLess(categorical_raise.lineno, min(lines))

        transport_state = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "_TransportState"
        )
        methods = {
            node.name: node for node in transport_state.body
            if isinstance(node, ast.FunctionDef)
        }
        pipe_calls = {
            node.func.attr for node in ast.walk(methods["pipe"])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        selector_calls = {
            node.func.attr for node in ast.walk(methods["create_selector"])
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertIn("pipe", pipe_calls)
        self.assertIn("DefaultSelector", selector_calls)

    def test_source_omits_materialization_network_and_simulator_commands(self) -> None:
        source = LAUNCHER.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = set()
        prohibited_mutations = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
                and node.func.attr in {
                    "chmod", "chown", "link", "makedirs", "mkdir", "remove",
                    "rename", "replace", "rmdir", "symlink", "unlink",
                }
            ):
                prohibited_mutations.append(node.func.attr)
        self.assertTrue(imports.isdisjoint({"http", "socket", "urllib"}))
        self.assertEqual(prohibited_mutations, [])
        for command in (
            "/usr/bin/hdiutil", "/usr/sbin/diskutil", "mount_cd9660",
            "xcrun simctl", "Simulator.app", "http://", "https://",
        ):
            self.assertNotIn(command, source)

    def test_kind4_is_a_terminal_failure_not_a_success_terminal(self) -> None:
        state = self._state()
        state.pid = 44_001
        digest = self.module._expected_git_argv_digest(state.repository, 0)
        self.assertIsNotNone(digest)
        state.custody.extend(self._custody_frame(1, 0, digest))
        state.custody.extend(self._custody_frame(4, 0, digest))
        with mock.patch.object(self.module, "_git_has_no_children", return_value=True):
            self.module._ingest_custody_frames(state, state.repository)
        self.assertEqual(state.custody_offset, 2 * self.module.GIT_CUSTODY_FRAME_BYTES)
        self.assertEqual(state.custody_frames, 2)
        self.assertEqual(state.custody_next, 1)
        self.assertIsNone(state.custody_unresolved)
        self.assertIsNone(state.custody_unresolved_digest)
        self.assertTrue(state.custody_failure_terminal)
        self.assertFalse(state.custody_ambiguous)

        tree = ast.parse(LAUNCHER.read_text(encoding="utf-8"))
        execute = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_execute_locked_driver"
        )
        success_attributes = {
            node.attr for node in ast.walk(execute)
            if isinstance(node, ast.Attribute)
        }
        self.assertIn("custody_failure_terminal", success_attributes)

    def test_partial_custody_frame_streams_but_blocks_terminal_and_hard_kill(self) -> None:
        for size in (1, self.module.GIT_CUSTODY_FRAME_BYTES - 1):
            with self.subTest(size=size):
                state = self._state()
                state.pid = 44_101
                state.open_names.add("stdin")
                digest = self.module._expected_git_argv_digest(state.repository, 0)
                complete = self._custody_frame(1, 0, digest)
                state.custody.extend(complete[:size])
                self.module._ingest_custody_frames(state, state.repository)
                # Pipe reads may split one atomic writer frame.  Before EOF the
                # prefix is a valid streaming state, not permanent ambiguity.
                self.assertFalse(state.custody_ambiguous)
                self.assertEqual(state.custody_offset, 0)
                with mock.patch.object(
                    self.module, "_bounded_group_absent", return_value=True,
                ):
                    self.assertFalse(self.module._cleanup_terminal_safe(state))
                with mock.patch.object(self.module.os, "kill") as kill:
                    with self.assertRaises(self.module.LauncherError):
                        self.module._preentry_stop_kill_and_reap(state)
                kill.assert_not_called()

                state.custody.extend(complete[size:])
                self.module._ingest_custody_frames(state, state.repository)
                self.assertFalse(state.custody_ambiguous)
                self.assertEqual(
                    state.custody_offset, self.module.GIT_CUSTODY_FRAME_BYTES,
                )
                self.assertEqual(state.custody_unresolved, 0)
                self.assertEqual(state.custody_unresolved_digest, digest)

    def test_invalid_custody_frame_is_sticky_and_forbids_hard_kill(self) -> None:
        state = self._state()
        state.pid = 44_201
        state.open_names.add("stdin")
        digest = self.module._expected_git_argv_digest(state.repository, 0)
        begin = self._custody_frame(1, 0, digest)
        invalid = bytearray(self._custody_frame(4, 0, digest))
        invalid[0:4] = b"FAIL"
        state.custody.extend(begin + invalid)
        with self.assertRaises(self.module.LauncherError):
            self.module._ingest_custody_frames(state, state.repository)
        self.assertTrue(state.custody_ambiguous)
        # Retaining the last-valid offset is useful fail-stop hardening, but
        # safety rests on the permanent ambiguity plus unresolved BEGIN.  Both
        # a last-valid and a consumed-invalid frame boundary are acceptable.
        self.assertIn(state.custody_offset, {
            self.module.GIT_CUSTODY_FRAME_BYTES,
            2 * self.module.GIT_CUSTODY_FRAME_BYTES,
        })
        self.assertEqual(state.custody_frames, 1)
        self.assertEqual(state.custody_unresolved, 0)
        self.assertEqual(state.custody_unresolved_digest, digest)
        self.assertFalse(state.custody_failure_terminal)
        with mock.patch.object(self.module.os, "kill") as kill:
            with self.assertRaises(self.module.LauncherError):
                self.module._preentry_stop_kill_and_reap(state)
        kill.assert_not_called()

    def test_input_attempted_is_set_before_a_write_post_effect_fault(self) -> None:
        state = self._state()
        fault = FatalProbe("write post-effect")

        def direct(effect):
            return effect()

        with (
            mock.patch.object(self.module, "_run_transport_effect", side_effect=direct),
            mock.patch.object(self.module.os, "write", side_effect=fault),
        ):
            with self.assertRaises(FatalProbe) as caught:
                self.module._write_driver_input(state, 91, b"locked bytes")
        self.assertIs(caught.exception, fault)
        self.assertTrue(state.input_attempted)
        self.assertEqual(state.input_offset, 0)

    def test_read_write_and_waitpid_are_all_masked_transport_effects(self) -> None:
        effects = []

        def masked(effect):
            effects.append(effect.__name__)
            return effect()

        wait_state = self._state()
        wait_state.pid = 90_001
        write_state = self._state()
        read_state = self._state()
        with (
            mock.patch.object(self.module, "_run_transport_effect", side_effect=masked),
            mock.patch.object(self.module.os, "waitpid", return_value=(0, 0)) as waitpid,
            mock.patch.object(self.module.os, "write", return_value=1) as write,
            mock.patch.object(
                self.module.os, "read", side_effect=BlockingIOError(),
            ) as read,
        ):
            self.module._child_wait_nonblocking(wait_state)
            self.module._write_driver_input(write_state, 92, b"ab")
            self.module._read_child_channel(read_state, 93, "status")
        self.assertEqual(effects, ["effect", "effect", "effect"])
        waitpid.assert_called_once_with(wait_state.pid, os.WNOHANG)
        write.assert_called_once()
        read.assert_called_once()
        self.assertTrue(write_state.input_attempted)
        self.assertEqual(write_state.input_offset, 1)

    def test_transport_effect_preserves_earliest_base_over_restore_fault(self) -> None:
        controlled = {int(signal.SIGTERM)}
        operation_fault = FatalProbe("operation")
        restoration_fault = FatalProbe("restoration")

        def mask_effect(operation, _values):
            if operation == signal.SIG_BLOCK:
                return set()
            raise restoration_fault

        with (
            mock.patch.object(
                self.module, "_controlled_signals", return_value=tuple(controlled),
            ),
            mock.patch.object(
                self.module, "_query_mask", side_effect=[set(), controlled],
            ),
            mock.patch.object(self.module, "_mask", side_effect=mask_effect),
        ):
            with self.assertRaises(FatalProbe) as caught:
                self.module._run_transport_effect(
                    lambda: (_ for _ in ()).throw(operation_fault),
                )
        self.assertIs(caught.exception, operation_fault)
        later = getattr(operation_fault, "pinned_launcher_later_errors", [])
        self.assertEqual(len(later), 1)
        self.assertIs(later[0][1], restoration_fault)

    def test_cleanup_base_displaces_an_ordinary_operation_error(self) -> None:
        ordinary = RuntimeError("ordinary operation")
        fatal = FatalProbe("cleanup base")
        observed = self.module._remember_cleanup_failure(
            ordinary, fatal, "cleanup",
        )
        self.assertIs(observed, fatal)
        self.assertEqual(
            getattr(fatal, "pinned_launcher_displaced_ordinary_error"),
            ("cleanup", ordinary),
        )

    def test_post_input_signal_authority_is_direct_cooperative_term_only(self) -> None:
        state = self._state()
        state.pid = 91_001
        state.input_attempted = True
        state.term_attempted = True
        path_digest = hashlib.sha256(self.module.CHILD_ALLOWED_PATHS[0]).digest()
        state.child_identity = (
            state.pid, os.getpid(), state.pid, os.geteuid(), os.getegid(),
            state.pid, 123, 456, path_digest,
        )
        calls = []

        def record_kill(pid, number):
            calls.append((pid, number))

        with (
            mock.patch.object(self.module, "_child_wait_nonblocking", return_value=None),
            mock.patch.object(
                self.module, "_darwin_process_identity",
                return_value=state.child_identity,
            ),
            mock.patch.object(self.module.os, "kill", side_effect=record_kill),
            mock.patch.object(
                self.module.os, "killpg",
                side_effect=AssertionError("group signal attempted"),
            ),
        ):
            self.assertTrue(self.module._signal_cooperative_term(state))
        self.assertEqual(calls, [(state.pid, signal.SIGTERM)])

    def test_post_input_cleanup_never_enters_preentry_stop_kill(self) -> None:
        state = self._state()
        state.pid = 92_001
        state.input_attempted = True
        events = []

        def reap_owner(observed, first):
            events.append("observe-owner")
            observed.wait_status = 0
            return first

        with (
            mock.patch.object(
                self.module, "_child_wait_nonblocking",
                side_effect=lambda _state: events.append("initial-wait"),
            ),
            mock.patch.object(
                self.module, "_send_cooperative_term_once",
                side_effect=lambda _state: events.append("term"),
            ),
            mock.patch.object(
                self.module, "_preentry_stop_kill_and_reap",
                side_effect=AssertionError("pre-entry hard kill reached"),
            ),
            mock.patch.object(
                self.module, "_observe_owner_until_reaped", side_effect=reap_owner,
            ),
            mock.patch.object(
                self.module, "_drain_reaped_channels_to_eof",
                side_effect=lambda _state, first: events.append("drain-eof") or first,
            ),
            mock.patch.object(
                self.module, "_observe_git_absence_after_owner_reap",
                side_effect=lambda _state, first: events.append("git-absence") or first,
            ),
            mock.patch.object(
                self.module, "_prove_owner_group_absent_or_retain",
                side_effect=lambda _state, first: events.append("group-absence") or first,
            ),
        ):
            self.module._terminate_reap_and_prove_group(state, force=True)
        self.assertEqual(events, [
            "initial-wait", "term", "observe-owner", "drain-eof",
            "git-absence", "group-absence",
        ])

    def test_preentry_hard_kill_gate_rejects_every_effect_or_custody_hint(self) -> None:
        mutations = {
            "input attempted": lambda state: setattr(state, "input_attempted", True),
            "stdin absent": lambda state: state.open_names.clear(),
            "waited": lambda state: setattr(state, "wait_status", 0),
            "stdout": lambda state: state.stdout.extend(b"x"),
            "stderr": lambda state: state.stderr.extend(b"x"),
            "status": lambda state: state.status_frame.extend(b"x"),
            "partial custody": lambda state: state.custody.extend(b"x"),
            "custody offset": lambda state: setattr(state, "custody_offset", 1),
            "custody frames": lambda state: setattr(state, "custody_frames", 1),
            "unresolved": lambda state: setattr(state, "custody_unresolved", 0),
            "enrolled Git": lambda state: state.git_identities.update({0: (1,)}),
            "ambiguous": lambda state: setattr(state, "custody_ambiguous", True),
            "failure terminal": lambda state: setattr(
                state, "custody_failure_terminal", True,
            ),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                state = self._state()
                state.pid = 93_001
                state.open_names.add("stdin")
                mutate(state)
                with mock.patch.object(self.module.os, "kill") as kill:
                    with self.assertRaises(self.module.LauncherError):
                        self.module._preentry_stop_kill_and_reap(state)
                kill.assert_not_called()

    def test_clean_preentry_path_signals_stop_then_kill_only(self) -> None:
        state = self._state()
        state.pid = 94_001
        state.open_names.add("stdin")
        calls = []
        snapshot = (("identity",), ("status",))

        def stopped(observed):
            observed.child_stopped = True
            return None

        def reaped(observed):
            observed.wait_status = int(signal.SIGKILL)

        with (
            mock.patch.object(
                self.module.os, "kill",
                side_effect=lambda pid, number: calls.append((pid, number)),
            ),
            mock.patch.object(self.module, "_wait_preentry_stop", side_effect=stopped),
            mock.patch.object(
                self.module, "_stable_stopped_child_snapshot", return_value=snapshot,
            ),
            mock.patch.object(self.module, "_drain_child_channels", return_value=None),
            mock.patch.object(self.module, "_git_has_no_children", return_value=True),
            mock.patch.object(
                self.module, "_child_wait_nonblocking", side_effect=reaped,
            ),
            mock.patch.object(self.module, "_close_parent_input", return_value=None),
            mock.patch.object(self.module, "_bounded_group_absent", return_value=True),
        ):
            self.module._preentry_stop_kill_and_reap(state)
        self.assertEqual(calls, [
            (state.pid, signal.SIGSTOP), (state.pid, signal.SIGKILL),
        ])
        self.assertTrue(state.preentry_stop_attempted)
        self.assertTrue(state.preentry_kill_attempted)
        self.assertFalse(state.child_stopped)
        self.assertIsNotNone(state.wait_status)

    def test_custody_eof_with_partial_frame_is_ambiguous_before_close(self) -> None:
        state = self._state()
        descriptor = 95
        state.custody.extend(b"partial")
        state.fds[descriptor] = "custody read"
        state.open_names.add("custody")

        def retire(observed_descriptor):
            self.assertEqual(observed_descriptor, descriptor)
            state.fds.pop(observed_descriptor)

        state.retire_fd = retire
        with (
            mock.patch.object(
                self.module, "_run_transport_effect", side_effect=lambda effect: effect(),
            ),
            mock.patch.object(self.module.os, "read", return_value=b""),
        ):
            with self.assertRaises(self.module.LauncherError):
                self.module._read_child_channel(state, descriptor, "custody")
        self.assertTrue(state.custody_eof)
        self.assertTrue(state.custody_ambiguous)
        self.assertNotIn("custody", state.open_names)
        self.assertNotIn(descriptor, state.fds)

    def test_missing_eof_or_uncertain_custody_enters_permanent_retention(self) -> None:
        def missing_eof(state):
            state.custody_eof = False

        def unresolved(state):
            state.custody_eof = True
            state.custody_unresolved = 0

        def ambiguous(state):
            state.custody_eof = True
            state.custody_ambiguous = True

        def trailing(state):
            state.custody_eof = True
            state.custody.extend(b"x")

        for label, mutate in (
            ("missing eof", missing_eof),
            ("unresolved", unresolved),
            ("ambiguous", ambiguous),
            ("trailing", trailing),
        ):
            with self.subTest(label=label):
                state = self._state()
                state.wait_status = 0
                mutate(state)
                with (
                    mock.patch.object(
                        self.module, "_close_parent_input", return_value=None,
                    ),
                    mock.patch.object(
                        self.module, "_retain_parent_custody_forever",
                        side_effect=RetainedCustody(label),
                    ) as retain,
                ):
                    with self.assertRaises(RetainedCustody):
                        self.module._drain_reaped_channels_to_eof(state, None)
                retain.assert_called_once_with(state)

    def test_complete_custody_eof_returns_without_retention(self) -> None:
        state = self._state()
        state.wait_status = 0
        state.custody_eof = True
        primary = FatalProbe("prior cleanup fault")
        with (
            mock.patch.object(self.module, "_close_parent_input", return_value=None),
            mock.patch.object(
                self.module, "_retain_parent_custody_forever",
                side_effect=AssertionError("unexpected retention"),
            ) as retain,
        ):
            observed = self.module._drain_reaped_channels_to_eof(state, primary)
        self.assertIs(observed, primary)
        retain.assert_not_called()

    def test_outer_orchestration_reauthenticates_after_fake_isolation(self) -> None:
        events = []
        driver = DRIVER.read_bytes()
        invocation = {
            "locked_files": [
                {"sha256": self.module.LOCKED_BINDING_SHA256},
                {"sha256": self.module.LOCKED_DRIVER_SHA256},
            ],
        }

        class FakeSession:
            def __init__(self, _signals):
                events.append("session")

            def open_repository(self, repository):
                self_test.assertEqual(repository, ROOT)
                events.append("open")
                return 17

        self_test = self

        def authenticate(_session, descriptor):
            self.assertEqual(descriptor, 17)
            events.append("authenticate")
            return invocation, driver

        def isolate(raw, repository):
            self.assertEqual(raw, driver)
            self.assertEqual(repository, ROOT)
            events.append("isolate")
            return self.module.EXPECTED_DRIVER_VERDICT

        def reauthenticate(repository, raw):
            self.assertEqual((repository, raw), (ROOT, driver))
            events.append("reauthenticate")

        with (
            mock.patch.object(
                self.module, "_prove_single_main_thread",
                side_effect=lambda: events.append("thread-proof"),
            ),
            mock.patch.object(self.module, "_DescriptorSession", FakeSession),
            mock.patch.object(
                self.module, "_authenticate_launcher_inputs",
                side_effect=authenticate,
            ),
            mock.patch.object(
                self.module, "_close_session_bounded",
                side_effect=lambda _session: events.append("close"),
            ),
            mock.patch.object(
                self.module, "_execute_locked_driver", side_effect=isolate,
            ),
            mock.patch.object(
                self.module, "_reauthenticate_live_inputs",
                side_effect=reauthenticate,
            ),
        ):
            observed = self.module.run_pinned_nonexecuting_checkpoint()
        self.assertEqual(observed, self.module.EXTERNAL_VERDICT)
        self.assertEqual(events, [
            "thread-proof", "session", "open", "authenticate", "close",
            "thread-proof", "isolate", "reauthenticate",
        ])

    def test_exact_internal_and_outer_verdict_identities(self) -> None:
        for raw, expected_size, expected_digest in (
            (
                self.module.EXPECTED_DRIVER_VERDICT,
                self.module.EXPECTED_DRIVER_VERDICT_BYTES,
                self.module.EXPECTED_DRIVER_VERDICT_SHA256,
            ),
            (
                self.module.EXTERNAL_VERDICT,
                self.module.EXTERNAL_VERDICT_BYTES,
                self.module.EXTERNAL_VERDICT_SHA256,
            ),
            (
                self.module.CHILD_SUCCESS_FRAME,
                self.module.CHILD_STATUS_MAX,
                self.module.CHILD_SUCCESS_FRAME_SHA256,
            ),
        ):
            with self.subTest(expected_digest=expected_digest):
                self.assertEqual(len(raw), expected_size)
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected_digest)

        value = json.loads(self.module.EXTERNAL_VERDICT)
        self.assertEqual(self.module.EXTERNAL_VERDICT, self._canonical_json(value))
        self.assertEqual(set(value), {
            "build_started", "child_stderr_emitted", "child_stdout_emitted",
            "driver_cli_invoked", "external_launcher_authenticated",
            "external_runtime_authenticated", "image_attached", "image_created",
            "lock_file_bytes", "lock_file_sha256",
            "locked_driver_checkpoint_commit", "locked_driver_checkpoint_tree",
            "mount_created", "network_accessed", "official_output_emitted",
            "qualification_authorized", "qualification_receipt_emitted",
            "schema_version", "simulator_started",
        })
        for key, item in value.items():
            if isinstance(item, bool):
                self.assertIs(item, False, key)

    def test_cli_is_always_zero_stdout_and_nonzero_without_running_a_child(self) -> None:
        outcomes = (
            self.module.EXTERNAL_VERDICT,
            b"wrong verdict\n",
            FatalProbe("fake isolation failure"),
        )
        for outcome in outcomes:
            with self.subTest(outcome=type(outcome).__name__):
                stdout = io.StringIO()
                stderr = io.StringIO()
                patcher = (
                    mock.patch.object(
                        self.module, "run_pinned_nonexecuting_checkpoint",
                        return_value=outcome,
                    )
                    if isinstance(outcome, bytes)
                    else mock.patch.object(
                        self.module, "run_pinned_nonexecuting_checkpoint",
                        side_effect=outcome,
                    )
                )
                with patcher, redirect_stdout(stdout), redirect_stderr(stderr):
                    self.assertEqual(self.module.main([]), 1)
                self.assertEqual(stdout.getvalue(), "")
                self.assertEqual(stderr.getvalue(), self.module.RUNTIME_FAILURE)

        stdout = io.StringIO()
        stderr = io.StringIO()
        run = mock.Mock(side_effect=AssertionError("argument path ran checkpoint"))
        with (
            mock.patch.object(
                self.module, "run_pinned_nonexecuting_checkpoint", run,
            ),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            self.assertEqual(self.module.main(["unexpected"]), 2)
        run.assert_not_called()
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), self.module.ARGUMENT_FAILURE)


if __name__ == "__main__":
    unittest.main()
