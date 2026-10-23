from __future__ import annotations

import errno
import hashlib
import importlib.util
import os
from pathlib import Path, PurePosixPath
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

SOURCE_ROOT = Path(__file__).resolve().parents[3]
RUNNER_SOURCE = SOURCE_ROOT / "scripts/comparison/run_headless_epoch_v1.py"
RUNNER_MODULE_NAME = "dancers_test_run_headless_epoch_v1"
RUNNER_SPEC = importlib.util.spec_from_file_location(RUNNER_MODULE_NAME, RUNNER_SOURCE)
if RUNNER_SPEC is None or RUNNER_SPEC.loader is None:
    raise RuntimeError("cannot load the runner source")
runner = importlib.util.module_from_spec(RUNNER_SPEC)
sys.modules[RUNNER_MODULE_NAME] = runner
RUNNER_SPEC.loader.exec_module(runner)


SHIM_SOURCE = SOURCE_ROOT / "scripts/comparison/headless_darwin_spawn_shim_v1.c"
TARGET_SOURCE = (
    SOURCE_ROOT
    / "scripts/comparison/tests/headless_darwin_spawn_shim_target_v1.c"
)


def identity(relative: PurePosixPath, raw: bytes) -> runner.FileIdentity:
    return runner.FileIdentity(
        relative_path=relative,
        byte_count=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
    )


class FakeNativeFunction:
    def __init__(self, effect):
        self.effect = effect

    def __call__(self, *arguments):
        return self.effect(*arguments)


class DescriptorCustodyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.temporary.name))
        self.repository_path = self.root / "repository"
        self.result_path = self.root / "results"
        self.repository_path.mkdir()
        self.result_path.mkdir()
        self.inputs = {
            "config": (PurePosixPath("inputs/config.yaml"), b"config\n"),
            "map": (PurePosixPath("inputs/map.bin"), b"map\n"),
            "scenario": (PurePosixPath("inputs/scenario.json"), b"{}\n"),
        }
        for relative, raw in self.inputs.values():
            path = self.repository_path.joinpath(*relative.parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def acquire_attempt(
        self, observer_mode: str = "none"
    ) -> tuple[
        runner.RepositoryDescriptorCustody,
        runner.DescriptorSessionCustody,
        runner.DescriptorDirectoryCustody,
        runner.PreparedDescriptorAttempt,
        runner.DescriptorRuntime,
    ]:
        repository = runner.RepositoryDescriptorCustody(self.repository_path)
        session = runner.DescriptorSessionCustody.acquire(
            self.result_path, "a" * 64, "session-a"
        )
        attempt_directory = session.create_attempt("attempt-0001")
        runtime = runner.DescriptorRuntime(
            controller_timesteps=100,
            num_robots=2,
            observer_mode=observer_mode,
            omp_threads=1,
            rng_seed=1,
            timestep_milliseconds=100,
        )
        metadata = None
        if observer_mode != "none":
            metadata = (
                b'{"config_sha256":"' + hashlib.sha256(b"config\n").hexdigest().encode()
                + b'","map_sha256":"' + hashlib.sha256(b"map\n").hexdigest().encode()
                + b'","omp_threads":1,"scenario_sha256":"'
                + hashlib.sha256(b"{}\n").hexdigest().encode() + b'"}\n'
            )
        prepared = runner.prepare_descriptor_attempt(
            repository,
            attempt_directory,
            config=identity(*self.inputs["config"]),
            map_artifact=identity(*self.inputs["map"]),
            scenario=identity(*self.inputs["scenario"]),
            runtime=runtime,
            metadata_bytes=metadata,
        )
        return repository, session, attempt_directory, prepared, runtime

    def close_attempt(
        self,
        repository: runner.RepositoryDescriptorCustody,
        session: runner.DescriptorSessionCustody,
        prepared: runner.PreparedDescriptorAttempt,
    ) -> None:
        prepared.close()
        repository.close()
        session.close()

    def test_shim_path_buffer_requires_an_in_bounds_nul_terminator(self) -> None:
        source = SHIM_SOURCE.read_text(encoding="utf-8")
        self.assertIn("memchr(path, '\\0', capacity) == NULL", source)
        self.assertNotIn("one fd-exec", source)

    def test_openat_post_effect_base_is_enrolled_closed_and_reusable(self) -> None:
        real_invoke = runner._invoke_native_effect
        effected: list[int] = []

        def post_effect(function, arguments):
            value = real_invoke(function, arguments)
            if getattr(function, "__name__", "") == "openat" and not effected:
                effected.append(int(value))
                raise KeyboardInterrupt("injected after openat effect")
            return value

        with (
            mock.patch.object(runner, "_invoke_native_effect", side_effect=post_effect),
            self.assertRaisesRegex(KeyboardInterrupt, "openat effect"),
        ):
            runner.DescriptorDirectoryCustody.open_absolute(
                self.result_path,
                "post-effect absolute directory",
            )
        self.assertEqual(len(effected), 1)
        with self.assertRaises(OSError) as missing:
            runner.fcntl.fcntl(effected[0], runner.fcntl.F_GETFD)
        self.assertEqual(missing.exception.errno, errno.EBADF)
        replacement = os.open("/dev/null", os.O_RDONLY)
        try:
            self.assertEqual(replacement, effected[0])
        finally:
            os.close(replacement)

    def test_openat_helper_return_gap_uses_caller_owned_cell(self) -> None:
        real_openat = runner._native_openat_effect
        effected: list[int] = []

        def post_helper_return(*arguments):
            value = real_openat(*arguments)
            if not effected:
                effected.append(value[0])
                raise KeyboardInterrupt("injected after openat helper return")
            return value

        with (
            mock.patch.object(
                runner,
                "_native_openat_effect",
                side_effect=post_helper_return,
            ),
            self.assertRaisesRegex(KeyboardInterrupt, "helper return"),
        ):
            runner.DescriptorDirectoryCustody.open_absolute(
                self.result_path,
                "caller-cell absolute directory",
            )
        self.assertEqual(len(effected), 1)
        with self.assertRaises(OSError) as missing:
            runner.fcntl.fcntl(effected[0], runner.fcntl.F_GETFD)
        self.assertEqual(missing.exception.errno, errno.EBADF)

    def test_openat_cleanup_close_effect_reuse_preserves_primary_and_replacement(self) -> None:
        real_invoke = runner._invoke_native_effect
        real_close = os.close
        effected: list[int] = []
        replacement: list[int] = []

        def post_effect(function, arguments):
            value = real_invoke(function, arguments)
            if getattr(function, "__name__", "") == "openat" and not effected:
                effected.append(int(value))
                raise KeyboardInterrupt("primary openat effect")
            return value

        def close_effect(descriptor: int) -> None:
            if effected and descriptor == effected[0] and not replacement:
                real_close(descriptor)
                replacement.append(os.open("/dev/null", os.O_RDONLY))
                raise OSError(errno.EIO, "cleanup close effect")
            real_close(descriptor)

        try:
            with (
                mock.patch.object(runner, "_invoke_native_effect", side_effect=post_effect),
                mock.patch.object(runner.os, "close", side_effect=close_effect),
                self.assertRaisesRegex(KeyboardInterrupt, "primary openat effect") as raised,
            ):
                runner.DescriptorDirectoryCustody.open_absolute(
                    self.result_path,
                    "post-effect close reuse",
                )
            self.assertEqual(replacement, effected)
            self.assertEqual(
                str(raised.exception.custody_cleanup_errors[0]),
                "[Errno 5] cleanup close effect",
            )
            self.assertGreaterEqual(
                runner.fcntl.fcntl(replacement[0], runner.fcntl.F_GETFD),
                0,
            )
        finally:
            if replacement:
                real_close(replacement[0])

    def test_mkdir_post_effect_base_cleans_exact_entry_or_retains_on_cleanup_failure(self) -> None:
        parent = runner.DescriptorDirectoryCustody.open_absolute(
            self.result_path,
            "mkdir parent",
        )
        real_invoke = runner._invoke_native_effect
        real_rmdir = os.rmdir

        def post_mkdir(function, arguments):
            value = real_invoke(function, arguments)
            if getattr(function, "__name__", "") == "mkdirat":
                raise KeyboardInterrupt("injected after mkdir effect")
            return value

        def fail_cleanup(_name: str, *, dir_fd: int) -> None:
            self.assertEqual(dir_fd, parent.descriptor)
            raise OSError(errno.EIO, "injected retained entry")

        try:
            with (
                mock.patch.object(runner, "_invoke_native_effect", side_effect=post_mkdir),
                mock.patch.object(runner.os, "rmdir", side_effect=fail_cleanup),
                self.assertRaisesRegex(KeyboardInterrupt, "mkdir effect") as raised,
            ):
                parent.create_directory("retained")
            retained = raised.exception.custody_retained_objects[0]
            self.assertTrue(retained.closed)
            self.assertEqual(retained.entry_name, "retained")
            self.assertTrue(
                os.path.isdir(os.path.join(self.result_path, "retained"))
            )
            real_rmdir("retained", dir_fd=parent.descriptor)
        finally:
            parent.close()

    def test_mkdir_success_then_child_open_failure_reconciles_exact_entry(self) -> None:
        parent = runner.DescriptorDirectoryCustody.open_absolute(
            self.result_path,
            "mkdir-open parent",
        )
        real_openat = runner._native_openat_effect
        child_open_calls = 0

        def fail_first_child_open(*arguments):
            nonlocal child_open_calls
            if os.fsdecode(arguments[1]) == "reconciled":
                child_open_calls += 1
                if child_open_calls == 1:
                    raise OSError(errno.EIO, "injected child open failure")
            return real_openat(*arguments)

        try:
            with (
                mock.patch.object(
                    runner,
                    "_native_openat_effect",
                    side_effect=fail_first_child_open,
                ),
                self.assertRaisesRegex(runner.StorageAbort, "child open failure"),
            ):
                parent.create_directory("reconciled")
            self.assertEqual(child_open_calls, 2)
            self.assertFalse((self.result_path / "reconciled").exists())
        finally:
            parent.close()

    def test_mkdir_created_state_is_explicitly_retained_when_enrollment_fails(self) -> None:
        parent = runner.DescriptorDirectoryCustody.open_absolute(
            self.result_path,
            "mkdir-retained parent",
        )

        def fail_child_open(*_arguments):
            raise OSError(errno.EMFILE, "injected persistent child open failure")

        try:
            with (
                mock.patch.object(
                    runner,
                    "_native_openat_effect",
                    side_effect=fail_child_open,
                ),
                self.assertRaisesRegex(runner.StorageAbort, "child open failure") as raised,
            ):
                parent.create_directory("retained-state")
            retained = raised.exception.custody_retained_entry_cells[0]
            self.assertTrue(retained.created)
            self.assertEqual(retained.parent_descriptor, parent.descriptor)
            self.assertEqual(retained.entry_name, "retained-state")
            os.rmdir("retained-state", dir_fd=parent.descriptor)
            retained.relinquish()
        finally:
            parent.close()

    def test_created_file_and_materialized_reopen_post_effect_base_roll_back(self) -> None:
        parent = runner.DescriptorDirectoryCustody.open_absolute(
            self.result_path,
            "file effect parent",
        )
        real_invoke = runner._invoke_native_effect
        create_seen = False

        def post_create(function, arguments):
            nonlocal create_seen
            value = real_invoke(function, arguments)
            if (
                getattr(function, "__name__", "") == "openat"
                and int(arguments[2]) & os.O_CREAT
                and not create_seen
            ):
                create_seen = True
                raise KeyboardInterrupt("injected after create effect")
            return value

        try:
            with (
                mock.patch.object(runner, "_invoke_native_effect", side_effect=post_create),
                self.assertRaisesRegex(KeyboardInterrupt, "create effect"),
            ):
                parent.create_output("created", "logical/created", "created")
            self.assertFalse((self.result_path / "created").exists())

            open_count = 0

            def post_reopen(function, arguments):
                nonlocal open_count
                value = real_invoke(function, arguments)
                if getattr(function, "__name__", "") == "openat":
                    open_count += 1
                    if open_count == 2:
                        raise KeyboardInterrupt("injected after reopen effect")
                return value

            with (
                mock.patch.object(runner, "_invoke_native_effect", side_effect=post_reopen),
                self.assertRaisesRegex(KeyboardInterrupt, "reopen effect"),
            ):
                parent.materialize_input(
                    "input",
                    b"input\n",
                    "logical/input",
                    "input",
                )
            self.assertFalse((self.result_path / "input").exists())
        finally:
            parent.close()

    def test_created_file_identity_base_is_reconciled_without_entry_or_fd_leak(self) -> None:
        parent = runner.DescriptorDirectoryCustody.open_absolute(
            self.result_path,
            "identity-effect parent",
        )
        real_identity = runner._open_file_identity
        calls = 0

        def fail_first_identity(descriptor: int, label: str):
            nonlocal calls
            if label == "identity-effect":
                calls += 1
                if calls == 1:
                    raise KeyboardInterrupt("injected identity effect")
            return real_identity(descriptor, label)

        try:
            with (
                mock.patch.object(
                    runner,
                    "_open_file_identity",
                    side_effect=fail_first_identity,
                ),
                self.assertRaisesRegex(KeyboardInterrupt, "identity effect"),
            ):
                parent.create_output(
                    "identity-effect",
                    "logical/identity-effect",
                    "identity-effect",
                )
            self.assertGreaterEqual(calls, 2)
            self.assertFalse((self.result_path / "identity-effect").exists())
        finally:
            parent.close()

    def test_created_file_identity_persistent_failure_retains_published_fd(self) -> None:
        parent = runner.DescriptorDirectoryCustody.open_absolute(
            self.result_path,
            "identity-retained parent",
        )

        def fail_identity(_descriptor: int, label: str):
            if label == "identity-retained":
                raise KeyboardInterrupt("injected persistent identity failure")
            raise AssertionError("unexpected identity request")

        try:
            with (
                mock.patch.object(
                    runner,
                    "_open_file_identity",
                    side_effect=fail_identity,
                ),
                self.assertRaisesRegex(KeyboardInterrupt, "identity failure") as raised,
            ):
                parent.create_output(
                    "identity-retained",
                    "logical/identity-retained",
                    "identity-retained",
                )
            retained = raised.exception.custody_retained_descriptor_cells[0]
            descriptor = retained.require_owned("identity-retained")
            self.assertGreaterEqual(runner.fcntl.fcntl(descriptor, runner.fcntl.F_GETFD), 0)
            self.assertTrue((self.result_path / "identity-retained").is_file())
            os.unlink("identity-retained", dir_fd=parent.descriptor)
            os.close(retained.relinquish("identity-retained"))
        finally:
            parent.close()

    def test_repository_dup_helper_return_gap_closes_exact_duplicate(self) -> None:
        repository = runner.RepositoryDescriptorCustody(self.repository_path)
        real_dup = runner._native_dupfd_effect
        effected: list[int] = []

        def post_helper_return(*arguments):
            value = real_dup(*arguments)
            if not effected:
                effected.append(value[0])
                raise KeyboardInterrupt("injected after duplicate helper return")
            return value

        try:
            with (
                mock.patch.object(
                    runner,
                    "_native_dupfd_effect",
                    side_effect=post_helper_return,
                ),
                self.assertRaisesRegex(KeyboardInterrupt, "duplicate helper return"),
            ):
                repository.open_identity(
                    identity(*self.inputs["config"]),
                    "duplicate-gap config",
                )
            self.assertEqual(len(effected), 1)
            with self.assertRaises(OSError) as missing:
                runner.fcntl.fcntl(effected[0], runner.fcntl.F_GETFD)
            self.assertEqual(missing.exception.errno, errno.EBADF)
        finally:
            repository.close()

    def test_timing_manifest_is_exact_fixed_fd_abi(self) -> None:
        repository, session, _directory, prepared, runtime = self.acquire_attempt()
        try:
            raw = runner.build_descriptor_manifest_bytes(prepared.roles, runtime)
            value = runner.strict_json_bytes(raw, "descriptor manifest")
            self.assertEqual(list(value), ["roles", "runtime", "schema_version"])
            self.assertEqual(
                [(record["role"], record["fd"]) for record in value["roles"]],
                [("config", 241), ("map", 242), ("scenario", 243)],
            )
            self.assertTrue(all(record["mode"] == 0o444 for record in value["roles"]))
            self.assertEqual(raw, prepared.directory.read_file(prepared.control, 65_536))
        finally:
            self.close_attempt(repository, session, prepared)

    def test_fidelity_manifest_requires_metadata_input_and_two_outputs(self) -> None:
        repository, session, _directory, prepared, runtime = self.acquire_attempt("digest")
        try:
            value = runner.strict_json_bytes(
                runner.build_descriptor_manifest_bytes(prepared.roles, runtime),
                "descriptor manifest",
            )
            records = {record["role"]: record for record in value["roles"]}
            self.assertEqual(records["metadata"]["mode"], 0o444)
            self.assertIsInstance(records["metadata"]["sha256"], str)
            for role in ("trace", "summary"):
                self.assertEqual(records[role]["mode"], 0o600)
                self.assertIsNone(records[role]["sha256"])
        finally:
            self.close_attempt(repository, session, prepared)

    def test_symlinked_repository_component_is_rejected(self) -> None:
        real = self.root / "real"
        real.mkdir()
        linked = self.root / "linked"
        linked.symlink_to(real, target_is_directory=True)
        with self.assertRaises(runner.StorageAbort):
            runner.RepositoryDescriptorCustody(linked)

    def test_session_and_lease_are_exclusive_and_released_by_identity(self) -> None:
        session = runner.DescriptorSessionCustody.acquire(
            self.result_path, "b" * 64, "session-b"
        )
        with self.assertRaises(runner.StorageAbort):
            runner.DescriptorSessionCustody.acquire(
                self.result_path, "b" * 64, "session-c"
            )
        self.assertTrue((self.result_path / ".epoch-lease").is_file())
        session.close()
        self.assertFalse((self.result_path / ".epoch-lease").exists())

    def test_acquisition_attempts_rmdir_post_effect_preserves_original_and_continues(self) -> None:
        real_fsync = os.fsync
        real_rmdir = os.rmdir
        sync_calls = 0
        rmdir_calls: list[str] = []

        def fail_after_directories(descriptor: int) -> None:
            nonlocal sync_calls
            sync_calls += 1
            if sync_calls == 2:
                raise runner.StorageAbort("original acquisition failure")
            real_fsync(descriptor)

        def post_effect(name: str, *, dir_fd: int) -> None:
            rmdir_calls.append(name)
            real_rmdir(name, dir_fd=dir_fd)
            if name == "attempts":
                raise KeyboardInterrupt("attempts rmdir effect")

        with (
            mock.patch.object(runner.os, "fsync", side_effect=fail_after_directories),
            mock.patch.object(runner.os, "rmdir", side_effect=post_effect),
            self.assertRaisesRegex(
                runner.StorageAbort,
                "original acquisition failure",
            ) as raised,
        ):
            runner.DescriptorSessionCustody.acquire(
                self.result_path,
                "1" * 64,
                "attempts-post-effect",
            )
        self.assertEqual(rmdir_calls, ["attempts", "attempts-post-effect"])
        self.assertTrue(
            any(
                isinstance(error, KeyboardInterrupt)
                for error in raised.exception.custody_cleanup_errors
            )
        )
        self.assertFalse((self.result_path / ".epoch-lease").exists())
        self.assertFalse((self.result_path / "attempts-post-effect").exists())

    def test_acquisition_session_rmdir_post_effect_preserves_original_and_continues(self) -> None:
        real_fsync = os.fsync
        real_rmdir = os.rmdir
        sync_calls = 0
        rmdir_calls: list[str] = []

        def fail_after_directories(descriptor: int) -> None:
            nonlocal sync_calls
            sync_calls += 1
            if sync_calls == 2:
                raise runner.StorageAbort("original session cleanup failure")
            real_fsync(descriptor)

        def post_effect(name: str, *, dir_fd: int) -> None:
            rmdir_calls.append(name)
            real_rmdir(name, dir_fd=dir_fd)
            if name == "session-post-effect":
                raise KeyboardInterrupt("session rmdir effect")

        with (
            mock.patch.object(runner.os, "fsync", side_effect=fail_after_directories),
            mock.patch.object(runner.os, "rmdir", side_effect=post_effect),
            self.assertRaisesRegex(
                runner.StorageAbort,
                "original session cleanup failure",
            ) as raised,
        ):
            runner.DescriptorSessionCustody.acquire(
                self.result_path,
                "2" * 64,
                "session-post-effect",
            )
        self.assertEqual(rmdir_calls, ["attempts", "session-post-effect"])
        self.assertTrue(
            any(
                isinstance(error, KeyboardInterrupt)
                for error in raised.exception.custody_cleanup_errors
            )
        )
        self.assertFalse((self.result_path / ".epoch-lease").exists())
        self.assertFalse((self.result_path / "session-post-effect").exists())

    def test_acquisition_pre_effect_rmdir_failure_surfaces_closed_retained_entries(self) -> None:
        real_fsync = os.fsync
        sync_calls = 0
        rmdir_calls: list[str] = []

        def fail_after_directories(descriptor: int) -> None:
            nonlocal sync_calls
            sync_calls += 1
            if sync_calls == 2:
                raise runner.StorageAbort("original retained acquisition failure")
            real_fsync(descriptor)

        def fail_attempts(name: str, *, dir_fd: int) -> None:
            del dir_fd
            rmdir_calls.append(name)
            if name == "attempts":
                raise OSError(errno.EIO, "attempts rmdir pre-effect")
            raise AssertionError("nonempty session must not reach rmdir")

        with (
            mock.patch.object(runner.os, "fsync", side_effect=fail_after_directories),
            mock.patch.object(runner.os, "rmdir", side_effect=fail_attempts),
            self.assertRaisesRegex(
                runner.StorageAbort,
                "original retained acquisition failure",
            ) as raised,
        ):
            runner.DescriptorSessionCustody.acquire(
                self.result_path,
                "3" * 64,
                "retained-acquisition",
            )
        retained = raised.exception.custody_retained_objects
        retained_entries = {
            item.entry_name
            for item in retained
            if isinstance(item, runner.DescriptorDirectoryCustody)
            and item.entry_name is not None
        }
        self.assertEqual(rmdir_calls, ["attempts"])
        self.assertEqual(retained_entries, {"attempts", "retained-acquisition"})
        self.assertTrue(
            all(
                item.closed
                for item in retained
                if isinstance(item, runner.DescriptorDirectoryCustody)
            )
        )
        self.assertFalse((self.result_path / ".epoch-lease").exists())
        self.assertTrue(
            (self.result_path / "retained-acquisition/attempts").is_dir()
        )
        os.rmdir(self.result_path / "retained-acquisition/attempts")
        os.rmdir(self.result_path / "retained-acquisition")

    def test_acquire_open_absolute_helper_return_base_closes_published_root(self) -> None:
        real_open = runner.DescriptorDirectoryCustody.open_absolute
        captured: list[runner.DescriptorDirectoryCustody] = []

        def post_helper(*arguments, **keywords):
            value = real_open(*arguments, **keywords)
            captured.append(value)
            raise KeyboardInterrupt("open-absolute helper return")

        with (
            mock.patch.object(
                runner.DescriptorDirectoryCustody,
                "open_absolute",
                side_effect=post_helper,
            ),
            self.assertRaisesRegex(KeyboardInterrupt, "open-absolute helper return"),
        ):
            runner.DescriptorSessionCustody.acquire(
                self.result_path,
                "4" * 64,
                "never-created",
            )
        self.assertEqual(len(captured), 1)
        self.assertTrue(captured[0].closed)
        self.assertEqual(captured[0].descriptor, -1)
        self.assertEqual(os.listdir(self.result_path), [])

    def test_acquire_from_repository_path_helper_return_base_closes_full_chain(self) -> None:
        repository = runner.RepositoryDescriptorCustody(self.repository_path)
        real_open = runner.RepositoryDescriptorCustody.open_directory_path
        captured: list[
            tuple[
                runner.DescriptorDirectoryCustody,
                tuple[runner.DescriptorDirectoryCustody, ...],
            ]
        ] = []

        def post_helper(*arguments, **keywords):
            value = real_open(*arguments, **keywords)
            captured.append(value)
            raise KeyboardInterrupt("directory-path helper return")

        try:
            with (
                mock.patch.object(
                    runner.RepositoryDescriptorCustody,
                    "open_directory_path",
                    autospec=True,
                    side_effect=post_helper,
                ),
                self.assertRaisesRegex(KeyboardInterrupt, "directory-path helper return"),
            ):
                runner.DescriptorSessionCustody.acquire_from_repository(
                    repository,
                    PurePosixPath("inputs"),
                    "5" * 64,
                    "never-created",
                )
            self.assertEqual(len(captured), 1)
            result, ancestors = captured[0]
            self.assertTrue(all(item.closed for item in (result, *ancestors)))
            self.assertFalse((self.repository_path / "inputs/.epoch-lease").exists())
        finally:
            repository.close()

    def test_acquisition_lease_helper_return_base_unlinks_and_closes_published_file(self) -> None:
        real_create = runner.DescriptorDirectoryCustody.create_output
        captured: list[runner.CustodiedFile] = []

        def post_helper(directory, name, logical_path, role, **keywords):
            value = real_create(
                directory,
                name,
                logical_path,
                role,
                **keywords,
            )
            if role == "descriptor epoch lease":
                captured.append(value)
                raise KeyboardInterrupt("lease helper return")
            return value

        with (
            mock.patch.object(
                runner.DescriptorDirectoryCustody,
                "create_output",
                autospec=True,
                side_effect=post_helper,
            ),
            self.assertRaisesRegex(KeyboardInterrupt, "lease helper return"),
        ):
            runner.DescriptorSessionCustody.acquire(
                self.result_path,
                "6" * 64,
                "lease-helper",
            )
        self.assertEqual(len(captured), 1)
        self.assertTrue(captured[0].closed)
        self.assertIsNone(captured[0].entry_name)
        self.assertEqual(os.listdir(self.result_path), [])

    def test_acquisition_session_helper_return_base_removes_published_directory(self) -> None:
        real_create = runner.DescriptorDirectoryCustody.create_directory
        captured: list[runner.DescriptorDirectoryCustody] = []

        def post_helper(directory, name, *arguments, **keywords):
            value = real_create(directory, name, *arguments, **keywords)
            if name == "session-helper":
                captured.append(value)
                raise KeyboardInterrupt("session helper return")
            return value

        with (
            mock.patch.object(
                runner.DescriptorDirectoryCustody,
                "create_directory",
                autospec=True,
                side_effect=post_helper,
            ),
            self.assertRaisesRegex(KeyboardInterrupt, "session helper return"),
        ):
            runner.DescriptorSessionCustody.acquire(
                self.result_path,
                "7" * 64,
                "session-helper",
            )
        self.assertEqual(len(captured), 1)
        self.assertTrue(captured[0].closed)
        self.assertIsNone(captured[0].entry_name)
        self.assertEqual(os.listdir(self.result_path), [])

    def test_acquisition_attempts_helper_return_base_removes_both_directories(self) -> None:
        real_create = runner.DescriptorDirectoryCustody.create_directory
        captured: list[runner.DescriptorDirectoryCustody] = []

        def post_helper(directory, name, *arguments, **keywords):
            value = real_create(directory, name, *arguments, **keywords)
            if name == "attempts":
                captured.append(value)
                raise KeyboardInterrupt("attempts helper return")
            return value

        with (
            mock.patch.object(
                runner.DescriptorDirectoryCustody,
                "create_directory",
                autospec=True,
                side_effect=post_helper,
            ),
            self.assertRaisesRegex(KeyboardInterrupt, "attempts helper return"),
        ):
            runner.DescriptorSessionCustody.acquire(
                self.result_path,
                "8" * 64,
                "attempts-helper",
            )
        self.assertEqual(len(captured), 1)
        self.assertTrue(captured[0].closed)
        self.assertIsNone(captured[0].entry_name)
        self.assertEqual(os.listdir(self.result_path), [])

    def test_acquisition_final_helper_return_base_rolls_back_empty_session(self) -> None:
        real_acquire = runner.DescriptorSessionCustody._acquire_held_result
        captured: list[runner.DescriptorSessionCustody] = []

        def post_helper(*arguments, **keywords):
            value = real_acquire(*arguments, **keywords)
            captured.append(value)
            raise KeyboardInterrupt("held-result helper return")

        with (
            mock.patch.object(
                runner.DescriptorSessionCustody,
                "_acquire_held_result",
                side_effect=post_helper,
            ),
            self.assertRaisesRegex(KeyboardInterrupt, "held-result helper return"),
        ):
            runner.DescriptorSessionCustody.acquire(
                self.result_path,
                "9" * 64,
                "final-helper",
            )
        self.assertEqual(len(captured), 1)
        self.assertTrue(captured[0].closed)
        self.assertEqual(os.listdir(self.result_path), [])

    def test_attempt_child_helper_return_base_rolls_back_entry_and_publications(self) -> None:
        session = runner.DescriptorSessionCustody.acquire(
            self.result_path,
            "a" * 64,
            "attempt-helper-session",
        )
        real_create = runner.DescriptorDirectoryCustody.create_directory
        captured: list[runner.DescriptorDirectoryCustody] = []

        def post_helper(directory, name, *arguments, **keywords):
            value = real_create(directory, name, *arguments, **keywords)
            if name == "orphan":
                captured.append(value)
                raise KeyboardInterrupt("attempt child helper return")
            return value

        try:
            with (
                mock.patch.object(
                    runner.DescriptorDirectoryCustody,
                    "create_directory",
                    autospec=True,
                    side_effect=post_helper,
                ),
                self.assertRaisesRegex(KeyboardInterrupt, "attempt child helper return"),
            ):
                session.create_attempt("orphan")
            self.assertEqual(len(captured), 1)
            self.assertTrue(captured[0].closed)
            self.assertIsNone(captured[0].entry_name)
            self.assertEqual(set(session.directory_index), {PurePosixPath(".")})
            self.assertEqual(session.attempt_directories, [])
            self.assertFalse(
                (self.result_path / "attempt-helper-session/attempts/orphan").exists()
            )
        finally:
            session.close()

    def test_attempt_index_and_list_effect_failures_are_transactionally_rolled_back(self) -> None:
        class EffectDict(dict):
            def __setitem__(self, key, value):
                super().__setitem__(key, value)
                raise KeyboardInterrupt("index publication effect")

        class EffectList(list):
            def append(self, value):
                super().append(value)
                raise KeyboardInterrupt("list publication effect")

        for kind in ("index", "list"):
            with self.subTest(kind=kind):
                session = runner.DescriptorSessionCustody.acquire(
                    self.result_path,
                    ("b" if kind == "index" else "c") * 64,
                    f"publication-{kind}",
                )
                captured: list[runner.DescriptorDirectoryCustody] = []
                real_create = runner.DescriptorDirectoryCustody.create_directory

                def remember(directory, name, *arguments, **keywords):
                    value = real_create(directory, name, *arguments, **keywords)
                    if name == "attempt":
                        captured.append(value)
                    return value

                if kind == "index":
                    session.directory_index = EffectDict(session.directory_index)
                else:
                    session.attempt_directories = EffectList(
                        session.attempt_directories
                    )
                try:
                    with (
                        mock.patch.object(
                            runner.DescriptorDirectoryCustody,
                            "create_directory",
                            autospec=True,
                            side_effect=remember,
                        ),
                        self.assertRaisesRegex(
                            KeyboardInterrupt,
                            f"{kind} publication effect",
                        ),
                    ):
                        session.create_attempt("attempt")
                    self.assertEqual(len(captured), 1)
                    self.assertTrue(captured[0].closed)
                    self.assertIsNone(captured[0].entry_name)
                    self.assertEqual(
                        set(session.directory_index),
                        {PurePosixPath(".")},
                    )
                    self.assertEqual(session.attempt_directories, [])
                    self.assertFalse(
                        (
                            self.result_path
                            / f"publication-{kind}/attempts/attempt"
                        ).exists()
                    )
                finally:
                    session.close()

    def test_attempt_reparent_is_detected_before_further_output(self) -> None:
        session = runner.DescriptorSessionCustody.acquire(
            self.result_path, "c" * 64, "session-c"
        )
        attempt = session.create_attempt("attempt-a")
        os.rename(
            "attempt-a",
            "attempt-moved",
            src_dir_fd=session.attempts.descriptor,
            dst_dir_fd=session.attempts.descriptor,
        )
        with self.assertRaises(runner.StorageAbort):
            attempt.verify_entry()
        os.rename(
            "attempt-moved",
            "attempt-a",
            src_dir_fd=session.attempts.descriptor,
            dst_dir_fd=session.attempts.descriptor,
        )
        session.close()

    def test_pre_effect_rmdir_failure_preserves_primary_without_retry(self) -> None:
        session = runner.DescriptorSessionCustody.acquire(
            self.result_path, "2" * 64, "rmdir-pre-effect"
        )
        child = session.create_attempt("attempt-a")
        calls = 0

        def fail_rmdir(_name: str, *, dir_fd: int) -> None:
            nonlocal calls
            self.assertEqual(dir_fd, session.attempts.descriptor)
            calls += 1
            raise OSError(errno.EIO, "injected pre-effect rmdir")

        with mock.patch.object(runner.os, "rmdir", side_effect=fail_rmdir):
            with self.assertRaisesRegex(OSError, "pre-effect rmdir"):
                session.attempts.remove_empty_directory(child)
        self.assertEqual(calls, 1)
        self.assertTrue(child.closed)
        self.assertEqual(child.entry_name, "attempt-a")
        self.assertTrue((self.result_path / "rmdir-pre-effect/attempts/attempt-a").is_dir())
        session.close()

    def test_output_path_replacement_is_detected_without_following_it(self) -> None:
        session = runner.DescriptorSessionCustody.acquire(
            self.result_path, "d" * 64, "session-d"
        )
        attempt = session.create_attempt("attempt-a")
        output = attempt.create_output("out", "attempt/out", "output")
        os.rename(
            "out", "held", src_dir_fd=attempt.descriptor, dst_dir_fd=attempt.descriptor
        )
        os.symlink("held", "out", dir_fd=attempt.descriptor)
        with self.assertRaises(runner.StorageAbort):
            attempt.verify_file_entry(output)
        os.unlink("out", dir_fd=attempt.descriptor)
        os.rename(
            "held", "out", src_dir_fd=attempt.descriptor, dst_dir_fd=attempt.descriptor
        )
        runner.close_custodied_file(output)
        session.close()

    def test_prepare_failure_rolls_back_every_enrolled_file(self) -> None:
        repository = runner.RepositoryDescriptorCustody(self.repository_path)
        session = runner.DescriptorSessionCustody.acquire(
            self.result_path, "e" * 64, "session-e"
        )
        attempt = session.create_attempt("attempt-a")
        runtime = runner.DescriptorRuntime(100, 2, "none", 1, 1, 100)
        original = runner.materialize_descriptor_control
        with mock.patch.object(
            runner,
            "materialize_descriptor_control",
            side_effect=runner.StorageAbort("injected control failure"),
        ):
            with self.assertRaises(runner.StorageAbort):
                runner.prepare_descriptor_attempt(
                    repository,
                    attempt,
                    config=identity(*self.inputs["config"]),
                    map_artifact=identity(*self.inputs["map"]),
                    scenario=identity(*self.inputs["scenario"]),
                    runtime=runtime,
                )
        self.assertIsNotNone(original)
        self.assertEqual(os.listdir(attempt.descriptor), [])
        repository.close()
        session.close()

    def test_fixed_fd_reservations_survive_more_than_three_hundred_attempt_fds(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("Darwin fixed descriptor reservation")
        reservations = runner.FixedDescriptorReservations.acquire(
            ambiguity_terminal=[].append
        )
        session = runner.DescriptorSessionCustody.acquire(
            self.result_path, "7" * 64, "many-attempt-fds"
        )
        try:
            directories = [
                session.create_attempt(f"attempt-{index:04d}")
                for index in range(350)
            ]
            self.assertTrue(
                all(
                    not 240 <= directory.descriptor <= 250
                    for directory in directories
                )
            )
            reservations.verify()
        finally:
            session.close()
            reservations.close(ambiguity_terminal=[].append)
        for descriptor in range(240, 251):
            with self.assertRaises(OSError) as missing:
                runner.fcntl.fcntl(descriptor, runner.fcntl.F_GETFD)
            self.assertEqual(missing.exception.errno, errno.EBADF)
        read_descriptor, write_descriptor = os.pipe()
        duplicate = runner.fcntl.fcntl(
            read_descriptor,
            runner.fcntl.F_DUPFD_CLOEXEC,
            240,
        )
        try:
            self.assertEqual(duplicate, 240)
        finally:
            os.close(duplicate)
            os.close(read_descriptor)
            os.close(write_descriptor)

    def test_fixed_reservation_enrols_duplicate_before_fstat_baseexception(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("Darwin fixed descriptor reservation")
        real_fstat = os.fstat
        labels: list[str] = []

        def fail_first_reservation(descriptor: int):
            if descriptor == 240:
                raise KeyboardInterrupt("injected reservation fstat")
            return real_fstat(descriptor)

        with mock.patch.object(runner.os, "fstat", side_effect=fail_first_reservation):
            with self.assertRaisesRegex(KeyboardInterrupt, "reservation fstat"):
                runner.FixedDescriptorReservations.acquire(
                    ambiguity_terminal=labels.append
                )
        self.assertEqual(labels, [])
        for descriptor in range(240, 251):
            with self.assertRaises(OSError) as missing:
                runner.fcntl.fcntl(descriptor, runner.fcntl.F_GETFD)
            self.assertEqual(missing.exception.errno, errno.EBADF)

    def test_fixed_reservation_identity_drift_never_closes_a_reused_number(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("Darwin fixed descriptor reservation")
        reservations = runner.FixedDescriptorReservations.acquire(
            ambiguity_terminal=[].append
        )
        labels: list[str] = []
        os.close(240)
        anchor = os.open("/dev/null", os.O_RDONLY)
        replacement = runner.fcntl.fcntl(
            anchor,
            runner.fcntl.F_DUPFD_CLOEXEC,
            240,
        )
        self.assertEqual(replacement, 240)
        try:
            with self.assertRaises(runner.CustodyAmbiguity):
                reservations.close(ambiguity_terminal=labels.append)
            self.assertEqual(
                labels,
                ["fixed descriptor reservation identity changed before release"],
            )
            self.assertGreaterEqual(runner.fcntl.fcntl(replacement, runner.fcntl.F_GETFD), 0)
        finally:
            enrolled = tuple(reservations.descriptors)
            reservations.descriptors.clear()
            reservations.closed = True
            for descriptor in enrolled:
                try:
                    os.close(descriptor)
                except OSError as error:
                    if error.errno != errno.EBADF:
                        raise
            os.close(anchor)

    def test_spawn_effect_then_exception_retains_pid_and_proof(self) -> None:
        owner = runner._ChildOwnership(pid_cell=runner.ctypes.c_int(0))

        def effect(cell):
            cell.value = 12345
            raise KeyboardInterrupt("injected after effect")

        def proof(observed):
            observed.start_seconds = 1
            observed.start_microseconds = 2
            observed.suspended_proved = True
            observed.signal_authority = True

        with self.assertRaises(KeyboardInterrupt):
            runner._publish_spawn_ownership(owner, effect, proof)
        self.assertEqual(owner.pid, 12345)
        self.assertTrue(owner.signal_authority)

    def test_posix_file_actions_init_post_effect_base_is_destroyed_once(self) -> None:
        destroyed: list[int] = []

        def publish(pointer, value: int) -> None:
            runner.ctypes.cast(
                pointer,
                runner.ctypes.POINTER(runner.ctypes.c_void_p),
            )[0] = value

        def init_actions(pointer):
            publish(pointer, 0x1111)
            raise KeyboardInterrupt("file-actions init effect")

        def destroy_actions(pointer):
            destroyed.append(
                int(
                    runner.ctypes.cast(
                        pointer,
                        runner.ctypes.POINTER(runner.ctypes.c_void_p),
                    )[0]
                )
            )
            return 0

        zero = FakeNativeFunction(lambda *_arguments: 0)
        system = SimpleNamespace(
            posix_spawn_file_actions_init=FakeNativeFunction(init_actions),
            posix_spawn_file_actions_adddup2=zero,
            posix_spawn_file_actions_addinherit_np=zero,
            posix_spawnattr_init=zero,
            posix_spawnattr_setflags=zero,
            posix_spawnattr_setsigdefault=zero,
            posix_spawnattr_setsigmask=zero,
            posix_spawn=zero,
            posix_spawnattr_destroy=zero,
            posix_spawn_file_actions_destroy=FakeNativeFunction(destroy_actions),
        )
        spawner = runner._RawDarwinPosixSpawner.__new__(runner._RawDarwinPosixSpawner)
        spawner.system = system
        with self.assertRaisesRegex(KeyboardInterrupt, "file-actions init effect"):
            spawner.spawn(
                runner.ctypes.c_int(0),
                10,
                "/private/synthetic-shim",
                ((10, runner.SHIM_EXECUTABLE_FD),),
                {"LANG": "C"},
            )
        self.assertEqual(destroyed, [0x1111])

    def test_posix_attribute_init_and_destroy_effect_bases_preserve_primary(self) -> None:
        destroyed_actions: list[int] = []
        destroyed_attributes: list[int] = []

        def publish(pointer, value: int) -> None:
            runner.ctypes.cast(
                pointer,
                runner.ctypes.POINTER(runner.ctypes.c_void_p),
            )[0] = value

        def init_actions(pointer):
            publish(pointer, 0x2222)
            return 0

        def init_attributes(pointer):
            publish(pointer, 0x3333)
            raise KeyboardInterrupt("attribute init effect")

        def destroy_attributes(pointer):
            destroyed_attributes.append(
                int(
                    runner.ctypes.cast(
                        pointer,
                        runner.ctypes.POINTER(runner.ctypes.c_void_p),
                    )[0]
                )
            )
            raise OSError(errno.EIO, "attribute destroy effect")

        def destroy_actions(pointer):
            destroyed_actions.append(
                int(
                    runner.ctypes.cast(
                        pointer,
                        runner.ctypes.POINTER(runner.ctypes.c_void_p),
                    )[0]
                )
            )
            return 0

        zero = FakeNativeFunction(lambda *_arguments: 0)
        system = SimpleNamespace(
            posix_spawn_file_actions_init=FakeNativeFunction(init_actions),
            posix_spawn_file_actions_adddup2=zero,
            posix_spawn_file_actions_addinherit_np=zero,
            posix_spawnattr_init=FakeNativeFunction(init_attributes),
            posix_spawnattr_setflags=zero,
            posix_spawnattr_setsigdefault=zero,
            posix_spawnattr_setsigmask=zero,
            posix_spawn=zero,
            posix_spawnattr_destroy=FakeNativeFunction(destroy_attributes),
            posix_spawn_file_actions_destroy=FakeNativeFunction(destroy_actions),
        )
        spawner = runner._RawDarwinPosixSpawner.__new__(runner._RawDarwinPosixSpawner)
        spawner.system = system
        with self.assertRaisesRegex(KeyboardInterrupt, "attribute init effect") as raised:
            spawner.spawn(
                runner.ctypes.c_int(0),
                10,
                "/private/synthetic-shim",
                ((10, runner.SHIM_EXECUTABLE_FD),),
                {"LANG": "C"},
            )
        self.assertEqual(destroyed_attributes, [0x3333])
        self.assertEqual(destroyed_actions, [0x2222])
        self.assertEqual(len(raised.exception.custody_cleanup_errors), 1)
        self.assertIn(
            "attribute destroy effect",
            str(raised.exception.custody_cleanup_errors[0]),
        )

    def test_spawn_proof_failure_uses_no_return_terminal(self) -> None:
        owner = runner._ChildOwnership(pid_cell=runner.ctypes.c_int(0))
        labels: list[str] = []

        def effect(cell):
            cell.value = 12345
            return 0

        with self.assertRaises(runner.CustodyAmbiguity):
            runner._publish_spawn_ownership(
                owner,
                effect,
                lambda _owner: (_ for _ in ()).throw(RuntimeError("proof")),
                ambiguity_terminal=labels.append,
            )
        self.assertEqual(len(labels), 1)

    def test_final_reap_ambiguity_revokes_all_signal_authority(self) -> None:
        owner = runner._ChildOwnership(
            pid_cell=runner.ctypes.c_int(12345),
            spawn_effect_started=True,
            pid=12345,
            start_seconds=1,
            start_microseconds=2,
            suspended_proved=True,
            signal_authority=True,
            released=True,
            exit_observed_unreaped=True,
        )
        labels: list[str] = []
        with self.assertRaises(runner.CustodyAmbiguity):
            runner._final_reap_once(
                owner,
                lambda _pid: (_ for _ in ()).throw(KeyboardInterrupt("reap")),
                ambiguity_terminal=labels.append,
            )
        self.assertFalse(owner.signal_authority)
        self.assertTrue(owner.reap_started)
        self.assertTrue(owner.reap_ambiguous)
        with mock.patch.object(runner.os, "kill") as kill:
            with self.assertRaises(runner.ContainmentAbort):
                runner._signal_exact_unreaped(owner, signal.SIGTERM)
            kill.assert_not_called()

    def test_pid_start_identity_change_is_never_signalled(self) -> None:
        owner = runner._ChildOwnership(
            pid_cell=runner.ctypes.c_int(12345),
            spawn_effect_started=True,
            pid=12345,
            start_seconds=11,
            start_microseconds=22,
            suspended_proved=True,
            signal_authority=True,
            released=True,
        )
        information = runner._DarwinProcBsdInfo()
        information.head[1] = runner.DARWIN_SSTOP
        information.head[3] = owner.pid
        information.head[4] = os.getpid()
        information.head[5] = os.getuid()
        information.head[7] = os.getuid()
        information.start_sec = 12
        information.start_usec = 22
        with (
            mock.patch.object(runner, "_darwin_process_info", return_value=information),
            mock.patch.object(runner.os, "kill") as kill,
        ):
            with self.assertRaises(runner.ContainmentAbort):
                runner._signal_exact_unreaped(owner, signal.SIGTERM)
            kill.assert_not_called()

    def test_close_effect_then_reuse_never_closes_replacement_number(self) -> None:
        read_descriptor, write_descriptor = os.pipe()
        numbers = {"read": read_descriptor}
        labels: list[str] = []
        real_close = os.close
        replacement: list[int] = []

        def effect_then_reuse(descriptor: int) -> None:
            if descriptor != read_descriptor or replacement:
                real_close(descriptor)
                return
            real_close(descriptor)
            replacement.append(os.open("/dev/null", os.O_RDONLY))
            raise KeyboardInterrupt("injected close effect")

        try:
            with mock.patch.object(runner.os, "close", side_effect=effect_then_reuse):
                with self.assertRaises(runner.CustodyAmbiguity):
                    runner._close_tracked_number(
                        numbers,
                        "read",
                        ambiguity_terminal=labels.append,
                    )
            self.assertEqual(replacement, [read_descriptor])
            self.assertEqual(numbers, {})
            self.assertGreaterEqual(runner.fcntl.fcntl(read_descriptor, runner.fcntl.F_GETFD), 0)
            self.assertEqual(len(labels), 1)
        finally:
            real_close(write_descriptor)
            if replacement:
                real_close(replacement[0])

    def test_close_effect_without_reuse_relinquishes_before_terminal(self) -> None:
        read_descriptor, write_descriptor = os.pipe()
        numbers = {"read": read_descriptor}
        real_close = os.close
        labels: list[str] = []

        def effect_then_raise(descriptor: int) -> None:
            real_close(descriptor)
            raise KeyboardInterrupt("injected close effect")

        try:
            with mock.patch.object(runner.os, "close", side_effect=effect_then_raise):
                with self.assertRaises(runner.CustodyAmbiguity):
                    runner._close_tracked_number(
                        numbers,
                        "read",
                        ambiguity_terminal=labels.append,
                    )
            self.assertEqual(numbers, {})
            self.assertEqual(len(labels), 1)
        finally:
            real_close(write_descriptor)

    def test_custodied_file_close_effect_reuse_is_not_reclosed_by_outer_cleanup(self) -> None:
        session = runner.DescriptorSessionCustody.acquire(
            self.result_path, "8" * 64, "close-reuse-file"
        )
        attempt = session.create_attempt("attempt-a")
        output = attempt.create_output("out", "attempt/out", "output")
        original_number = output.descriptor
        real_close = os.close
        replacement: list[int] = []

        def effect_then_reuse(descriptor: int) -> None:
            if descriptor == original_number and not replacement:
                real_close(descriptor)
                replacement.append(
                    os.open("out", os.O_RDONLY, dir_fd=attempt.descriptor)
                )
                raise KeyboardInterrupt("injected close effect")
            real_close(descriptor)

        try:
            with mock.patch.object(runner.os, "close", side_effect=effect_then_reuse):
                with self.assertRaisesRegex(KeyboardInterrupt, "injected close effect"):
                    runner.close_custodied_file(output)
            self.assertTrue(output.closed)
            self.assertEqual(output.descriptor, -1)
            self.assertEqual(replacement, [original_number])
            self.assertGreaterEqual(
                runner.fcntl.fcntl(replacement[0], runner.fcntl.F_GETFD), 0
            )
            # An outer close observes relinquished ownership and cannot touch the reuse.
            runner.close_custodied_file(output)
            self.assertGreaterEqual(
                runner.fcntl.fcntl(replacement[0], runner.fcntl.F_GETFD), 0
            )
        finally:
            if replacement:
                real_close(replacement[0])
            session.close()

    def test_directory_close_effect_reuse_is_not_reclosed_by_session_cleanup(self) -> None:
        session = runner.DescriptorSessionCustody.acquire(
            self.result_path, "9" * 64, "close-reuse-directory"
        )
        attempt = session.create_attempt("attempt-a")
        original_number = attempt.descriptor
        real_close = os.close
        replacement: list[int] = []

        def effect_then_reuse(descriptor: int) -> None:
            if descriptor == original_number and not replacement:
                real_close(descriptor)
                replacement.append(os.open("/dev/null", os.O_RDONLY))
                raise KeyboardInterrupt("injected directory close effect")
            real_close(descriptor)

        try:
            with mock.patch.object(runner.os, "close", side_effect=effect_then_reuse):
                with self.assertRaisesRegex(
                    KeyboardInterrupt, "injected directory close effect"
                ):
                    attempt.close()
            self.assertTrue(attempt.closed)
            self.assertEqual(attempt.descriptor, -1)
            session.close()
            self.assertGreaterEqual(
                runner.fcntl.fcntl(replacement[0], runner.fcntl.F_GETFD), 0
            )
        finally:
            if replacement:
                real_close(replacement[0])

    def test_pre_effect_file_close_failure_is_relinquished_and_never_retried(self) -> None:
        session = runner.DescriptorSessionCustody.acquire(
            self.result_path, "a" * 64, "close-pre-effect"
        )
        attempt = session.create_attempt("attempt-a")
        output = attempt.create_output("out", "attempt/out", "output")
        original_number = output.descriptor
        real_close = os.close
        calls = 0

        def fail_before_effect(descriptor: int) -> None:
            nonlocal calls
            if descriptor == original_number:
                calls += 1
                raise OSError(errno.EIO, "injected pre-effect close failure")
            real_close(descriptor)

        try:
            with mock.patch.object(runner.os, "close", side_effect=fail_before_effect):
                with self.assertRaises(OSError):
                    runner.close_custodied_file(output)
                runner.close_custodied_file(output)
            self.assertEqual(calls, 1)
            self.assertTrue(output.closed)
            self.assertEqual(output.descriptor, -1)
            self.assertGreaterEqual(
                runner.fcntl.fcntl(original_number, runner.fcntl.F_GETFD), 0
            )
        finally:
            real_close(original_number)
            session.close()

    def test_stateless_select_rejects_fdset_overflow_before_use(self) -> None:
        self.assertEqual(runner.DARWIN_FD_SETSIZE, 1024)
        with self.assertRaisesRegex(runner.ContainmentAbort, "FD_SETSIZE"):
            runner._select_pipe_names(
                {"stdout_read": runner.DARWIN_FD_SETSIZE},
                {"stdout"},
                0.0,
            )

    def test_set_blocking_base_leaves_both_pipe_numbers_enrolled_for_cleanup(self) -> None:
        numbers: dict[str, int] = {}
        labels: list[str] = []
        with (
            mock.patch.object(
                runner.os,
                "set_blocking",
                side_effect=KeyboardInterrupt("injected set_blocking failure"),
            ),
            self.assertRaisesRegex(KeyboardInterrupt, "set_blocking failure"),
        ):
            runner._enroll_tracked_pipe(
                numbers,
                "stdout",
                nonblocking_read=True,
                ambiguity_terminal=labels.append,
            )
        enrolled = (numbers["stdout_read"], numbers["stdout_write"])
        self.assertEqual(labels, [])
        for name in tuple(numbers):
            runner._close_tracked_number(
                numbers,
                name,
                ambiguity_terminal=labels.append,
            )
        self.assertEqual(numbers, {})
        for descriptor in enrolled:
            with self.assertRaises(OSError) as missing:
                runner.fcntl.fcntl(descriptor, runner.fcntl.F_GETFD)
            self.assertEqual(missing.exception.errno, errno.EBADF)
        replacement = os.pipe()
        try:
            self.assertEqual(replacement, enrolled)
        finally:
            os.close(replacement[0])
            os.close(replacement[1])

    def test_launch_window_begins_before_spawn_effect_and_ends_at_terminal(self) -> None:
        ticks = iter((10.0, 17.25))
        window = runner._LaunchWindow()
        effect_observations: list[float | None] = []

        def effect() -> object:
            effect_observations.append(window.launch_started)
            return "spawned"

        self.assertEqual(window.invoke_spawn(effect, lambda: next(ticks)), "spawned")
        self.assertEqual(effect_observations, [10.0])
        self.assertEqual(window.deadline(5.0), 15.0)
        window.observe_terminal(lambda: next(ticks))
        self.assertEqual(window.elapsed(), 7.25)
        with self.assertRaisesRegex(runner.ContainmentAbort, "already started"):
            window.invoke_spawn(effect, lambda: 20.0)

    def test_macho_parser_rejects_platform_prefixed_openmp_runtime(self) -> None:
        encoded = b"/usr/lib/libomp.dylib\0"
        size = 24 + len(encoded)
        size += (-size) % 4
        command = (
            struct.pack("<IIIIII", runner.MACHO_LC_LOAD_DYLIB, size, 24, 0, 0, 0)
            + encoded
            + b"\0" * (size - 24 - len(encoded))
        )
        header = struct.pack(
            "<IiiIIIII",
            runner.MACHO_MAGIC_64,
            runner.MACHO_CPU_TYPE_ARM64,
            0,
            runner.MACHO_FILETYPE_EXECUTE,
            1,
            len(command),
            0,
            0,
        )
        with self.assertRaisesRegex(runner.ContainmentAbort, "OpenMP"):
            runner._macho_linker_signed_cdhash(
                header + command,
                "synthetic OpenMP executable",
            )

    def test_macho_parser_rejects_noncanonical_platform_dependency_paths(self) -> None:
        for dependency in (
            b"/usr/lib/../private/libescape.dylib",
            b"/System/Library/../private/libescape.dylib",
            b"/usr/lib//libSystem.B.dylib",
            b"//usr/lib/libSystem.B.dylib",
        ):
            with self.subTest(dependency=dependency):
                encoded = dependency + b"\0"
                size = 24 + len(encoded)
                size += (-size) % 4
                command = (
                    struct.pack(
                        "<IIIIII",
                        runner.MACHO_LC_LOAD_DYLIB,
                        size,
                        24,
                        0,
                        0,
                        0,
                    )
                    + encoded
                    + b"\0" * (size - 24 - len(encoded))
                )
                header = struct.pack(
                    "<IiiIIIII",
                    runner.MACHO_MAGIC_64,
                    runner.MACHO_CPU_TYPE_ARM64,
                    0,
                    runner.MACHO_FILETYPE_EXECUTE,
                    1,
                    len(command),
                    0,
                    0,
                )
                with self.assertRaisesRegex(
                    runner.ContainmentAbort,
                    "canonical platform-absolute",
                ):
                    runner._macho_linker_signed_cdhash(
                        header + command,
                        "synthetic noncanonical executable",
                    )

    def test_output_seal_effect_then_base_is_reconciled_without_close_retry(self) -> None:
        session = runner.DescriptorSessionCustody.acquire(
            self.result_path, "b" * 64, "seal-effect-file"
        )
        attempt = session.create_attempt("attempt-a")
        output = attempt.create_output("out", "attempt/out", "output")
        runner._write_all(output.descriptor, b"sealed\n", "output")
        real_fchmod = os.fchmod
        calls = 0

        def effect_then_raise(descriptor: int, mode: int) -> None:
            nonlocal calls
            calls += 1
            real_fchmod(descriptor, mode)
            raise KeyboardInterrupt("injected post-effect fchmod")

        with mock.patch.object(runner.os, "fchmod", side_effect=effect_then_raise):
            with self.assertRaisesRegex(KeyboardInterrupt, "post-effect fchmod"):
                runner._seal_descriptor_output(attempt, output, 1024)
        self.assertEqual(calls, 1)
        self.assertTrue(output.closed)
        self.assertEqual(output.descriptor, -1)
        self.assertEqual(
            stat.S_IMODE(os.stat("out", dir_fd=attempt.descriptor).st_mode), 0o444
        )
        session.close()

    def test_directory_seal_effect_then_base_is_reconciled_as_read_only(self) -> None:
        session = runner.DescriptorSessionCustody.acquire(
            self.result_path, "c" * 64, "seal-effect-directory"
        )
        attempt = session.create_attempt("attempt-a")
        real_fchmod = os.fchmod

        def effect_then_raise(descriptor: int, mode: int) -> None:
            real_fchmod(descriptor, mode)
            raise KeyboardInterrupt("injected post-effect directory fchmod")

        with mock.patch.object(runner.os, "fchmod", side_effect=effect_then_raise):
            with self.assertRaisesRegex(KeyboardInterrupt, "directory fchmod"):
                runner._seal_descriptor_directory(attempt)
        self.assertEqual(
            stat.S_IMODE(os.fstat(attempt.descriptor).st_mode), 0o555
        )
        session.close()

    def test_repeated_controlled_signals_are_latched_and_mask_restored(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("Darwin signal envelope")
        before = runner._query_signal_mask()
        envelope = runner._CustodySignalEnvelope([].append)
        envelope.install_blocked()
        try:
            os.kill(os.getpid(), signal.SIGINT)
            os.kill(os.getpid(), signal.SIGTERM)
            envelope.activate()
            for _index in range(100):
                if {signal.SIGINT, signal.SIGTERM} <= envelope.cancelled_signals:
                    break
                time.sleep(0.001)
            self.assertTrue({signal.SIGINT, signal.SIGTERM} <= envelope.cancelled_signals)
            envelope.block_cleanup()
        finally:
            envelope.restore()
        self.assertEqual(runner._query_signal_mask(), before)

    def test_in_process_host_probe_uses_retained_result_descriptor(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("Darwin host probe")
        result = runner.DescriptorDirectoryCustody.open_absolute(
            self.result_path,
            "host-probe result root",
        )
        try:
            observed = runner._real_host_probe(result.descriptor)
            self.assertEqual(
                set(observed),
                {"ac_power", "free_bytes", "free_bytes_ok", "thermal_nominal"},
            )
            self.assertIs(type(observed["ac_power"]), bool)
            self.assertIs(type(observed["thermal_nominal"]), bool)
            self.assertIs(type(observed["free_bytes"]), int)
            self.assertEqual(
                observed["free_bytes_ok"],
                observed["free_bytes"] >= runner.MIN_HOST_FREE_BYTES,
            )
        finally:
            result.close()

    def test_cf_allocation_post_effect_base_releases_once_with_primary_precedence(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("Darwin CoreFoundation custody")
        real_invoke = runner._invoke_native_effect
        copy_calls = 0
        release_calls = 0

        def post_copy(function, arguments):
            nonlocal copy_calls, release_calls
            value = real_invoke(function, arguments)
            name = getattr(function, "__name__", "")
            if name == "IOPSCopyPowerSourcesInfo":
                copy_calls += 1
                raise KeyboardInterrupt("injected CF allocation effect")
            if name == "CFRelease":
                release_calls += 1
            return value

        with mock.patch.object(
            runner,
            "_invoke_native_effect",
            side_effect=post_copy,
        ):
            with self.assertRaises(runner.InfrastructureAbort) as raised:
                runner._darwin_ac_power()
        self.assertEqual(copy_calls, 1)
        self.assertEqual(release_calls, 1)
        self.assertIsInstance(raised.exception.__cause__, KeyboardInterrupt)

    def test_cf_allocation_helper_return_gap_keeps_pointer_custody(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("Darwin CoreFoundation custody")
        real_pointer_effect = runner._native_pointer_effect
        real_invoke = runner._invoke_native_effect
        release_calls = 0
        injected = False

        def post_helper_return(*arguments):
            nonlocal injected
            value = real_pointer_effect(*arguments)
            if not injected:
                injected = True
                raise KeyboardInterrupt("injected after pointer helper return")
            return value

        def count_release(function, arguments):
            nonlocal release_calls
            value = real_invoke(function, arguments)
            if getattr(function, "__name__", "") == "CFRelease":
                release_calls += 1
            return value

        with (
            mock.patch.object(
                runner,
                "_native_pointer_effect",
                side_effect=post_helper_return,
            ),
            mock.patch.object(
                runner,
                "_invoke_native_effect",
                side_effect=count_release,
            ),
            self.assertRaises(runner.InfrastructureAbort) as raised,
        ):
            runner._darwin_ac_power()
        self.assertTrue(injected)
        self.assertEqual(release_calls, 1)
        self.assertIsInstance(raised.exception.__cause__, KeyboardInterrupt)

    def test_cf_release_post_effect_base_is_never_released_twice(self) -> None:
        if sys.platform != "darwin":
            self.skipTest("Darwin CoreFoundation custody")
        real_invoke = runner._invoke_native_effect
        release_calls = 0

        def post_release(function, arguments):
            nonlocal release_calls
            value = real_invoke(function, arguments)
            if getattr(function, "__name__", "") == "CFRelease":
                release_calls += 1
                raise KeyboardInterrupt("injected CF release effect")
            return value

        with mock.patch.object(
            runner,
            "_invoke_native_effect",
            side_effect=post_release,
        ):
            with self.assertRaises(runner.InfrastructureAbort) as raised:
                runner._darwin_ac_power()
        self.assertEqual(release_calls, 1)
        self.assertIsInstance(raised.exception.__cause__, KeyboardInterrupt)

    def test_host_probe_baseexception_is_wrapped_fail_closed(self) -> None:
        with self.assertRaisesRegex(runner.InfrastructureAbort, "failed closed"):
            runner._require_host(
                lambda: (_ for _ in ()).throw(KeyboardInterrupt("probe effect"))
            )


@unittest.skipUnless(sys.platform == "darwin", "Darwin-only native shim")
class NativeShimIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(os.path.realpath(cls.temporary.name))
        cls.repository_path = cls.root / "repository"
        cls.result_path = cls.root / "results"
        cls.repository_path.mkdir()
        cls.result_path.mkdir()
        cls.shim_relative = PurePosixPath("products/headless-darwin-spawn-shim-v1")
        cls.target_relative = PurePosixPath("products/test-target-v1")
        for relative, source in (
            (cls.shim_relative, SHIM_SOURCE),
            (cls.target_relative, TARGET_SOURCE),
        ):
            destination = cls.repository_path.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            completed = subprocess.run(
                [
                    "xcrun",
                    "clang",
                    "-std=c17",
                    "-Os",
                    "-Wall",
                    "-Wextra",
                    "-Werror",
                    "-pedantic",
                    "-fno-common",
                    "-fstack-protector-strong",
                    "-Wl,-dead_strip",
                    str(source),
                    "-o",
                    str(destination),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if completed.returncode != 0:
                raise AssertionError(completed.stderr.decode("utf-8", "replace"))
            destination.chmod(0o555)
        cls.inputs = {
            "config": (PurePosixPath("inputs/config.yaml"), b"config\n"),
            "map": (PurePosixPath("inputs/map.bin"), b"map\n"),
            "scenario": (PurePosixPath("inputs/scenario.json"), b"{}\n"),
        }
        for relative, raw in cls.inputs.values():
            destination = cls.repository_path.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temporary.cleanup()

    def test_native_shim_enforces_no_fork_and_exact_fd_execution(self) -> None:
        reservations = runner.FixedDescriptorReservations.acquire(
            ambiguity_terminal=[].append
        )
        repository = runner.RepositoryDescriptorCustody(self.repository_path)
        session = runner.DescriptorSessionCustody.acquire(
            self.result_path, "f" * 64, "native-session"
        )
        attempt_directory = session.create_attempt("attempt-0001")
        runtime = runner.DescriptorRuntime(100, 2, "none", 1, 1, 100)
        prepared = runner.prepare_descriptor_attempt(
            repository,
            attempt_directory,
            config=identity(*self.inputs["config"]),
            map_artifact=identity(*self.inputs["map"]),
            scenario=identity(*self.inputs["scenario"]),
            runtime=runtime,
        )
        shim_path = self.repository_path.joinpath(*self.shim_relative.parts)
        target_path = self.repository_path.joinpath(*self.target_relative.parts)
        shim_raw = shim_path.read_bytes()
        target_raw = target_path.read_bytes()
        shim = repository.open_identity(
            identity(self.shim_relative, shim_raw), "shim"
        )
        target = repository.open_identity(
            identity(self.target_relative, target_raw), "target"
        )
        try:
            request = prepared.process_request(
                shim=shim,
                target=target,
                repository_descriptor=repository.descriptor,
                reservations=reservations,
                runtime=runtime,
                timeout_seconds=5.0,
                termination_grace_seconds=0.5,
            )
            ambiguous: list[str] = []
            try:
                result = runner.run_custodied_process(
                    request, ambiguity_terminal=ambiguous.append
                )
            except BaseException as error:
                self.fail(
                    f"native custody failed: {error}; wait_status="
                    f"{getattr(error, 'custody_child_wait_status', None)}; cleanup="
                    f"{getattr(error, 'custody_cleanup_errors', None)}"
                )
            self.assertEqual(ambiguous, [])
            self.assertEqual(result.returncode, 0)
            self.assertFalse(result.timed_out)
            self.assertEqual(
                attempt_directory.read_file(prepared.stdout, 1024),
                b"shim-no-fork-ok\n",
            )
            self.assertEqual(attempt_directory.read_file(prepared.stderr, 1024), b"")
        finally:
            runner.close_custodied_file(target)
            runner.close_custodied_file(shim)
            prepared.close()
            repository.close()
            session.close()
            reservations.close(ambiguity_terminal=[].append)

    def test_native_shim_without_fixed_manifest_fds_fails_closed(self) -> None:
        shim_path = self.repository_path.joinpath(*self.shim_relative.parts)
        completed = subprocess.run(
            [str(shim_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"DANCERS_HEADLESS_DESCRIPTOR_IO_V1": "1"},
            check=False,
        )
        self.assertEqual(completed.returncode, 127)
        self.assertEqual(completed.stdout, b"")
        self.assertEqual(completed.stderr, b"")

    def test_wrong_target_cdhash_is_never_released_to_user_code(self) -> None:
        reservations = runner.FixedDescriptorReservations.acquire(
            ambiguity_terminal=[].append
        )
        repository = runner.RepositoryDescriptorCustody(self.repository_path)
        session = runner.DescriptorSessionCustody.acquire(
            self.result_path, "1" * 64, "wrong-target-session"
        )
        attempt_directory = session.create_attempt("attempt-0001")
        runtime = runner.DescriptorRuntime(100, 2, "none", 1, 1, 100)
        prepared = runner.prepare_descriptor_attempt(
            repository,
            attempt_directory,
            config=identity(*self.inputs["config"]),
            map_artifact=identity(*self.inputs["map"]),
            scenario=identity(*self.inputs["scenario"]),
            runtime=runtime,
        )
        shim_path = self.repository_path.joinpath(*self.shim_relative.parts)
        target_path = self.repository_path.joinpath(*self.target_relative.parts)
        shim = repository.open_identity(
            identity(self.shim_relative, shim_path.read_bytes()), "shim"
        )
        target = repository.open_identity(
            identity(self.target_relative, target_path.read_bytes()), "target"
        )
        original = runner._macho_linker_signed_cdhash
        calls = 0

        def wrong_second_digest(data: bytes, label: str) -> bytes:
            nonlocal calls
            calls += 1
            value = original(data, label)
            return value if calls == 1 else bytes([value[0] ^ 1]) + value[1:]

        try:
            request = prepared.process_request(
                shim=shim,
                target=target,
                repository_descriptor=repository.descriptor,
                reservations=reservations,
                runtime=runtime,
                timeout_seconds=5.0,
                termination_grace_seconds=0.5,
            )
            ambiguous: list[str] = []
            with (
                mock.patch.object(
                    runner,
                    "_macho_linker_signed_cdhash",
                    side_effect=wrong_second_digest,
                ),
                self.assertRaises(runner.ContainmentAbort),
            ):
                runner.run_custodied_process(
                    request,
                    ambiguity_terminal=ambiguous.append,
                )
            self.assertEqual(ambiguous, [])
            self.assertEqual(attempt_directory.read_file(prepared.stdout, 1024), b"")
            self.assertEqual(attempt_directory.read_file(prepared.stderr, 1024), b"")
        finally:
            runner.close_custodied_file(target)
            runner.close_custodied_file(shim)
            prepared.close()
            repository.close()
            session.close()
            reservations.close(ambiguity_terminal=[].append)


if __name__ == "__main__":
    unittest.main()
