from __future__ import annotations

import hashlib
import json
import os
import signal
import stat
import subprocess
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from scripts.comparison import analyze_results_v2 as ANALYZER
from scripts.comparison import run_paired_v2 as RUNNER
from scripts.comparison.tests.test_analyze_results_v2 import (
    host_snapshot as analyzer_host_snapshot,
)
from scripts.comparison.tests.test_analyze_results_v2 import (
    timing_run_record,
)


def battery_output(
    *,
    percent: int = 85,
    status: str = "charging",
    source: str = "AC Power",
    present: str = "true",
    trailing: str | None = None,
    newline: str = "\n",
) -> str:
    final = (
        trailing
        if trailing is not None
        else f"0:45 remaining present: {present}"
    )
    return newline.join(
        [
            f"Now drawing from '{source}'",
            (
                " -InternalBattery-0 (id=23658595)\t"
                f"{percent}%; {status}; {final}"
            ),
            "",
        ]
    )


class DarwinBatteryParserTests(unittest.TestCase):
    def test_accepts_only_registered_statuses_and_normalizes_ascii_space(
        self,
    ) -> None:
        for status in ("charging", "charged", "finishing \t  charge"):
            with self.subTest(status=status):
                record = RUNNER.parse_darwin_battery_probe(
                    returncode=0,
                    stdout=battery_output(status=status, newline="\r\n"),
                    stderr="",
                )
                self.assertTrue(record["valid"], record)
                self.assertEqual(record["source"], "AC Power")
                self.assertEqual(
                    record["battery_record"]["status"],
                    "finishing charge"
                    if status.startswith("finishing")
                    else status,
                )
                self.assertTrue(record["battery_record"]["present"])

    def test_accepts_exact_live_pmset_no_estimate_suffix(self) -> None:
        template = (
            "Now drawing from 'AC Power'\n"
            " -InternalBattery-0 (id=23658595)\t"
            "96%; {status}; (no estimate) present: true\n"
        )
        for raw_status, expected_status in (
            ("charging", "charging"),
            ("charged", "charged"),
            ("finishing \t charge", "finishing charge"),
        ):
            with self.subTest(status=raw_status):
                stdout = template.format(status=raw_status)
                record = RUNNER.parse_darwin_battery_probe(
                    returncode=0,
                    stdout=stdout,
                    stderr="",
                )
                self.assertTrue(record["valid"], record)
                self.assertEqual(
                    record["battery_record"],
                    {
                        "name": "InternalBattery-0",
                        "id": 23658595,
                        "percent": 96,
                        "status": expected_status,
                        "present": True,
                    },
                )

    def test_rejects_malformed_ambiguous_and_policy_invalid_output(self) -> None:
        valid = battery_output()
        cases = {
            "nonzero": {
                "returncode": 1,
                "stdout": valid,
            },
            "low-battery": {
                "returncode": 0,
                "stdout": battery_output(percent=79),
            },
            "discharging": {
                "returncode": 0,
                "stdout": battery_output(status="discharging"),
            },
            "not-charging": {
                "returncode": 0,
                "stdout": battery_output(status="not charging"),
            },
            "not-present": {
                "returncode": 0,
                "stdout": battery_output(present="false"),
            },
            "wrong-source": {
                "returncode": 0,
                "stdout": battery_output(source="Battery Power"),
            },
            "extra-line": {
                "returncode": 0,
                "stdout": valid + "unexpected\n",
            },
            "duplicate-battery": {
                "returncode": 0,
                "stdout": valid
                + (
                    " -InternalBattery-1 (id=2)\t85%; charged; "
                    "0:00 remaining present: true\n"
                ),
            },
            "arbitrary-trailing": {
                "returncode": 0,
                "stdout": battery_output(
                    trailing="arbitrary text present: true"
                ),
            },
            "trailing-field": {
                "returncode": 0,
                "stdout": battery_output(
                    trailing="0:45 remaining present: true extra"
                ),
            },
            "legacy-no-estimate-remaining": {
                "returncode": 0,
                "stdout": battery_output(
                    trailing="(no estimate) remaining present: true"
                ),
            },
            "no-estimate-double-space": {
                "returncode": 0,
                "stdout": battery_output(
                    trailing="(no estimate)  present: true"
                ),
            },
            "no-estimate-missing-space": {
                "returncode": 0,
                "stdout": battery_output(
                    trailing="(no estimate) present:true"
                ),
            },
            "timed-estimate-missing-remaining": {
                "returncode": 0,
                "stdout": battery_output(
                    trailing="0:45 present: true"
                ),
            },
            "timed-estimate-double-space": {
                "returncode": 0,
                "stdout": battery_output(
                    trailing="0:45 remaining  present: true"
                ),
            },
            "trailing-leading-ascii-space": {
                "returncode": 0,
                "stdout": battery_output(
                    trailing=" 0:45 remaining present: true"
                ),
            },
            "trailing-ending-ascii-space": {
                "returncode": 0,
                "stdout": battery_output(
                    trailing="0:45 remaining present: true "
                ),
            },
            "trailing-unicode-space": {
                "returncode": 0,
                "stdout": battery_output(
                    trailing="\u00a00:45 remaining present: true"
                ),
            },
            "unicode-status-space": {
                "returncode": 0,
                "stdout": battery_output(status="finishing\u00a0charge"),
            },
        }
        for name, case in cases.items():
            with self.subTest(name=name):
                record = RUNNER.parse_darwin_battery_probe(
                    stderr="",
                    **case,
                )
                self.assertFalse(record["valid"], record)
                self.assertIsNotNone(record["parse_error"])

    def test_drop_rules_and_absolute_floor_fail_closed(self) -> None:
        cooldown = RUNNER._power_policy_evaluation(
            stage="cooldown-complete",
            current_percent=84,
            cooldown_baseline_percent=None,
            comparison_percent=85,
        )
        self.assertTrue(cooldown["valid"], cooldown)
        failed_cooldown = RUNNER._power_policy_evaluation(
            stage="cooldown-complete",
            current_percent=83,
            cooldown_baseline_percent=None,
            comparison_percent=85,
        )
        self.assertIn(
            "cooldown-drop-exceeds-1", failed_cooldown["violations"]
        )
        failed_attempt = RUNNER._power_policy_evaluation(
            stage="after-attempt",
            current_percent=81,
            cooldown_baseline_percent=84,
            comparison_percent=83,
        )
        self.assertIn(
            "attempt-drop-exceeds-1", failed_attempt["violations"]
        )
        self.assertIn(
            "session-drop-exceeds-2", failed_attempt["violations"]
        )
        below_floor = RUNNER._power_policy_evaluation(
            stage="session-complete",
            current_percent=79,
            cooldown_baseline_percent=80,
            comparison_percent=None,
        )
        self.assertFalse(below_floor["valid"])
        self.assertIn(
            "battery-percent-outside-80-100",
            below_floor["violations"],
        )

    def test_runner_and_analyzer_battery_grammars_are_differentially_exact(
        self,
    ) -> None:
        valid = battery_output()
        cases = (
            valid,
            valid.replace("; 0:45", ";0:45"),
            valid.replace("; 0:45", ";  0:45"),
            valid.replace("present: true\n", "present: true \n"),
        )
        for stdout in cases:
            with self.subTest(stdout=stdout):
                runner = RUNNER.parse_darwin_battery_probe(
                    returncode=0,
                    stdout=stdout,
                    stderr="",
                )
                analyzer = ANALYZER._reparse_darwin_battery_probe(
                    returncode=0,
                    stdout=stdout,
                    stderr="",
                )
                self.assertEqual(analyzer, runner)


class ProcessControlTests(unittest.TestCase):
    def test_interrupted_wait_is_retried_and_child_is_accounted(self) -> None:
        usage = SimpleNamespace(ru_utime=0.1, ru_stime=0.2, ru_maxrss=10)
        with mock.patch.object(
            RUNNER.os,
            "wait4",
            side_effect=[
                InterruptedError("signal"),
                (1234, 0, usage),
            ],
        ), mock.patch.object(
            RUNNER.os,
            "killpg",
            side_effect=ProcessLookupError(),
        ):
            status, observed_usage, timed_out, control = (
                RUNNER._wait_for_pid(1234, 1.0, 0.1)
            )
        self.assertEqual(status, 0)
        self.assertIs(observed_usage, usage)
        self.assertFalse(timed_out)
        self.assertEqual(control["wait_interrupted_count"], 1)
        self.assertTrue(control["child_reaped"])
        self.assertTrue(control["process_group_empty_after_reap"])
        self.assertTrue(control["valid"])
        self.assertEqual(
            [
                (event["stage"], event["operation"], event["outcome"])
                for event in control["events"]
            ],
            [
                ("wait-before-timeout", "wait4", "interrupted"),
                ("wait-before-timeout", "wait4", "reaped"),
                ("group-empty-check", "killpg", "empty"),
            ],
        )

    def test_process_event_validator_rejects_out_of_order_fallback(
        self,
    ) -> None:
        control = RUNNER._new_process_control()
        control.update(
            {
                "timed_out": True,
                "child_reaped": True,
                "process_group_empty_after_reap": True,
            }
        )
        control["events"] = [
            RUNNER._process_event(
                stage="wait-before-timeout",
                operation="wait4",
                signal_name=None,
                outcome="not-ready",
            ),
            RUNNER._process_event(
                stage="sigterm",
                operation="kill",
                signal_name="SIGTERM",
                outcome="sent",
            ),
            RUNNER._process_event(
                stage="wait-after-sigterm",
                operation="wait4",
                signal_name=None,
                outcome="reaped",
            ),
            RUNNER._process_event(
                stage="group-empty-check",
                operation="killpg",
                signal_name=None,
                outcome="empty",
            ),
        ]
        with self.assertRaisesRegex(
            RUNNER.RunnerError, "fallback is not justified"
        ):
            RUNNER._validate_process_control_event_sequence(control)

    def test_no_process_control_has_no_fictitious_wait_event(self) -> None:
        control = RUNNER._no_process_control("launch failed")
        self.assertEqual(control["events"], [])
        RUNNER._validate_process_control_event_sequence(control)

    def test_group_eperm_falls_back_to_child_and_reaps_before_return(
        self,
    ) -> None:
        process = subprocess.Popen(
            ["/bin/sleep", "30"],
            start_new_session=True,
        )
        original_killpg = os.killpg

        def deny_group_signal(pid: int, sig: int) -> None:
            if sig == 0:
                original_killpg(pid, sig)
                return
            raise PermissionError(1, "Operation not permitted")

        reaped = False
        try:
            with mock.patch.object(
                RUNNER.os, "killpg", side_effect=deny_group_signal
            ):
                status, _, timed_out, control = RUNNER._wait_for_pid(
                    process.pid, 0.01, 0.2
                )
            process.returncode = os.waitstatus_to_exitcode(status)
            reaped = True
            self.assertTrue(timed_out)
            self.assertTrue(control["child_reaped"])
            self.assertTrue(control["process_group_empty_after_reap"])
            self.assertTrue(control["anomaly"])
            self.assertFalse(control["valid"])
            self.assertTrue(
                any(
                    event["operation"] == "killpg"
                    and event["outcome"] == "eperm"
                    and event["error"] is not None
                    for event in control["events"]
                ),
                control,
            )
            self.assertTrue(
                any(
                    event["operation"] == "kill"
                    and event["outcome"] == "sent"
                    for event in control["events"]
                ),
                control,
            )
            with self.assertRaises(ChildProcessError):
                os.waitpid(process.pid, os.WNOHANG)
        finally:
            if not reaped:
                try:
                    os.kill(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                try:
                    os.waitpid(process.pid, 0)
                except ChildProcessError:
                    pass

    def test_remaining_group_member_aborts_without_a_valid_control(self) -> None:
        usage = SimpleNamespace(ru_utime=0.1, ru_stime=0.2, ru_maxrss=10)
        with mock.patch.object(
            RUNNER.os,
            "wait4",
            return_value=(4321, 0, usage),
        ), mock.patch.object(
            RUNNER.os,
            "killpg",
            return_value=None,
        ):
            with self.assertRaises(
                RUNNER.UncontainedProcessError
            ) as raised:
                RUNNER._wait_for_pid(4321, 1.0, 0.1)
        control = raised.exception.process_control
        self.assertTrue(control["child_reaped"])
        self.assertFalse(control["process_group_empty_after_reap"])
        self.assertTrue(control["anomaly"])
        self.assertFalse(control["valid"])


class ResultContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.before = {"valid": True}
        self.after = {"valid": True}

    def test_control_anomaly_retains_result_but_forces_wrapper_failure(
        self,
    ) -> None:
        control = RUNNER._new_process_control()
        control.update(
            {
                "timed_out": True,
                "child_reaped": True,
                "process_group_empty_after_reap": True,
                "anomaly": True,
                "valid": False,
            }
        )
        control["events"].append(
            RUNNER._process_event(
                stage="sigterm",
                operation="killpg",
                signal_name="SIGTERM",
                outcome="eperm",
                error=PermissionError(1, "Operation not permitted"),
            )
        )
        result = {
            "schema_version": "old",
            "terminal_category": "runner-timeout",
            "orchestration_error": None,
            "resource": {
                "wall_seconds": 300.01,
                "total_cpu_seconds": 298.0,
                "peak_rss_mib": 512.0,
            },
        }
        decorated = RUNNER._decorate_attempt_result(
            result,
            before=self.before,
            after=self.after,
            control=control,
        )
        self.assertEqual(
            decorated["terminal_category"],
            "measurement-wrapper-failure",
        )
        self.assertEqual(decorated["resource"]["wall_seconds"], 300.01)
        self.assertIn("PermissionError", decorated["orchestration_error"])
        self.assertTrue(decorated["host_attempt_valid"])

    def test_post_measurement_artifact_error_cannot_remain_completed(
        self,
    ) -> None:
        control = RUNNER._new_process_control()
        control.update(
            {
                "timed_out": False,
                "child_reaped": True,
                "process_group_empty_after_reap": True,
                "valid": True,
            }
        )
        result = {
            "schema_version": "old",
            "terminal_category": "completed",
            "orchestration_error": "PermissionError: artifact changed",
            "resource": {"wall_seconds": 1.0},
        }
        decorated = RUNNER._decorate_attempt_result(
            result,
            before=self.before,
            after=self.after,
            control=control,
        )
        self.assertEqual(
            decorated["terminal_category"],
            "measurement-wrapper-failure",
        )

    def test_fidelity_wrapper_failure_clears_completed_only_metrics(
        self,
    ) -> None:
        for mutation in ("decorate", "force"):
            with self.subTest(mutation=mutation):
                result = timing_run_record()
                result["mode"] = "fidelity"
                result["provenance"]["mode"] = "fidelity"
                result["task_valid"] = True
                result["fidelity_metrics"] = {
                    "task_validity_predicate_id": (
                        "fixed-horizon-safe-navigation-v1"
                    ),
                    "task_valid": True,
                    "task_fields": {},
                    "paper_metrics": {},
                    "summary_parity_fields": {},
                }
                if mutation == "decorate":
                    result["orchestration_error"] = (
                        "OSError: post-process artifact failure"
                    )
                    RUNNER._decorate_attempt_result(
                        result,
                        before=result[
                            "host_conditions_before_attempt"
                        ],
                        after=result[
                            "host_conditions_after_attempt"
                        ],
                        control=result["process_control"],
                    )
                else:
                    RUNNER._force_wrapper_failure(
                        result,
                        OSError("post-result seal failed"),
                    )
                self.assertEqual(
                    result["terminal_category"],
                    "measurement-wrapper-failure",
                )
                self.assertIsNone(result["task_valid"])
                self.assertIsNone(result["fidelity_metrics"])
                ANALYZER.validate_run_record(result)

    def test_historical_runner_dependency_is_still_exact(self) -> None:
        self.assertEqual(
            RUNNER.sha256_file(Path(RUNNER._v1.__file__).resolve()),
            RUNNER.BASE_RUNNER_SHA256,
        )


class SealedPowerAbortTests(unittest.TestCase):
    def _plan(self, root: Path) -> RUNNER.Plan:
        plan_path = root / "plan.json"
        plan_bytes = b"{}\n"
        plan_path.write_bytes(plan_bytes)
        map_artifact = RUNNER.Artifact(
            root / "map.png",
            "a" * 64,
            "test map",
        )
        scenario = RUNNER.Artifact(
            root / "scenario.json",
            "b" * 64,
            "test scenario",
        )
        pair = RUNNER.Pair(
            pair_id="pair-001",
            environment="fractal_high",
            seed=1,
            robot_count=192,
            map_artifact=map_artifact,
            scenario=scenario,
            config_overrides={},
        )
        result_root = (
            root
            / "results/comparison/calibration/trace-v3-amended-v1"
            / "fractal-high-lower-count-fidelity"
        ).resolve()
        return RUNNER.Plan(
            path=plan_path,
            plan_sha256=hashlib.sha256(plan_bytes).hexdigest(),
            phase="calibration",
            mode="fidelity",
            fixed_work_timesteps=100,
            timestep_seconds=0.1,
            idle_cooldown_seconds=300,
            timeout_seconds=300.0,
            termination_grace_seconds=0.5,
            repetitions=1,
            warmups_per_binary_map=0,
            omp_threads=1,
            order_seed=1,
            minimum_free_disk_bytes=21_474_836_480,
            maximum_peak_rss_mib=49_152.0,
            power_mode="AC Power",
            thermal_policy="test",
            task_validity_predicate_id="fixed-horizon-safe-navigation-v1",
            trace_detail="digest",
            run_cwd=root,
            artifacts={},
            methods={},
            pairs=(pair,),
            capacity=None,
            capacity_evaluation=None,
            runner_sha256="0" * 64,
            build_manifest={},
            environment_manifest={},
            amendment_id="fh-lower-count-fidelity-2026-07-28-v1",
            cohort_id="fractal-high-lower-count-fidelity-amended-v1",
            result_root=result_root,
            design_role="outcome-informed-exploratory-fidelity",
            power_policy={"policy_id": RUNNER.POWER_POLICY_ID},
            process_control_policy={"test": True},
            serial_execution=True,
            analyzer_sha256="1" * 64,
            planned_method_attempt_slots=40,
            planned_pair_blocks=20,
            amendment={},
        )

    @staticmethod
    def _valid_host_snapshot() -> dict[str, object]:
        return {
            "valid": True,
            "power_probe": {
                "source": "AC Power",
                "battery_record": {"percent": 85},
            },
            "thermal_probe": {"state": 0},
        }

    def _install_completed_preflight(
        self,
        state: RUNNER._ExecutionState,
    ) -> None:
        snapshot = self._valid_host_snapshot()
        cooldown = {
            "required_seconds": 300,
            "eligible": True,
            "performed": True,
            "elapsed_seconds": 300.0,
            "before": snapshot,
            "after": snapshot,
            "valid": True,
        }
        state.preflight_snapshot = snapshot
        state.cooldown_record = cooldown
        state.cooldown_baseline_percent = 85
        state.preflight_record = {
            "host_conditions": snapshot,
            "idle_cooldown": cooldown,
        }

    def _assert_frozen_tree(self, output: Path) -> None:
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o555)
        for path in output.rglob("*"):
            expected = 0o555 if path.is_dir() else 0o444
            self.assertEqual(
                stat.S_IMODE(path.stat().st_mode),
                expected,
                path,
            )

    def test_mid_session_power_abort_writes_and_freezes_partial_session(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            plan = self._plan(root)
            output = plan.result_root
            valid_snapshot = {"valid": True}

            def aborting_execute(
                _plan: RUNNER.Plan, output_path: Path
            ) -> None:
                output_path.mkdir(parents=True)
                (output_path / "run_manifest.json").write_text(
                    "{}\n", encoding="utf-8"
                )
                (output_path / "runs.jsonl").write_text("", encoding="utf-8")
                (output_path / "pairs.jsonl").write_text("", encoding="utf-8")
                state = RUNNER._ACTIVE_STATE
                assert state is not None
                state.preflight_snapshot = valid_snapshot
                state.cooldown_record = {
                    "valid": True,
                    "before": valid_snapshot,
                    "after": valid_snapshot,
                }
                state.cooldown_baseline_percent = 85
                state.pending_power_abort = "before-attempt policy breach"
                raise RUNNER.PowerStabilityAbort(
                    "before-attempt policy breach"
                )

            with mock.patch.object(
                RUNNER, "_ORIGINAL_EXECUTE", side_effect=aborting_execute
            ), mock.patch.object(
                RUNNER, "_runtime_patches", return_value=nullcontext()
            ), mock.patch.object(
                RUNNER,
                "_host_condition_snapshot",
                return_value=valid_snapshot,
            ), mock.patch.object(
                RUNNER, "_write_runs_csv_if_missing"
            ), mock.patch.object(
                RUNNER, "_ORIGINAL_FREEZE_TREE"
            ) as freeze:
                session = RUNNER.execute(plan, output)

            self.assertFalse(session["session_power_valid"])
            self.assertFalse(session["all_attempts_completed"])
            self.assertFalse(session["all_pairs_valid"])
            self.assertTrue(session["stopped_early"])
            self.assertEqual(session["executed_method_attempt_slots"], 0)
            self.assertEqual(session["planned_method_attempt_slots"], 40)
            self.assertTrue((output / "session.json").is_file())
            freeze.assert_called_once_with(output)

    def test_result_root_rejects_inserted_parent_and_dangling_symlinks(
        self,
    ) -> None:
        for symlink_kind in ("parent", "dangling-final"):
            with self.subTest(symlink_kind=symlink_kind), (
                tempfile.TemporaryDirectory()
            ) as tmp, tempfile.TemporaryDirectory() as escape_tmp:
                root = Path(tmp)
                plan = self._plan(root)
                escape = Path(escape_tmp)
                if symlink_kind == "parent":
                    (root / "results").mkdir()
                    (root / "results" / "comparison").symlink_to(
                        escape, target_is_directory=True
                    )
                else:
                    plan.result_root.parent.mkdir(parents=True)
                    plan.result_root.symlink_to(
                        escape / "missing", target_is_directory=True
                    )

                with mock.patch.object(
                    RUNNER, "_ORIGINAL_EXECUTE"
                ) as original_execute:
                    with self.assertRaises(RUNNER.RunnerError):
                        RUNNER.execute(plan, plan.result_root)
                original_execute.assert_not_called()

    def test_execution_preflight_rejects_a_remote_in_any_worktree(
        self,
    ) -> None:
        plan = SimpleNamespace(
            run_cwd=Path("/repository"),
            methods={
                "reference": SimpleNamespace(
                    source_dir=Path("/reference")
                ),
                "optimized": SimpleNamespace(
                    source_dir=Path("/optimized")
                ),
            },
        )

        def git_output(source: Path, operation: str) -> str:
            self.assertEqual(operation, "remote")
            return "origin" if source == Path("/optimized") else ""

        with mock.patch.object(
            RUNNER._v1, "_git", side_effect=git_output
        ):
            with self.assertRaisesRegex(
                RUNNER.RunnerError, "zero Git remotes"
            ):
                RUNNER._require_zero_git_remotes(plan)

    def test_cooldown_rechecks_integrity_immediately_before_mkdir(
        self,
    ) -> None:
        snapshot = {
            "valid": True,
            "power_probe": {
                "source": "AC Power",
                "battery_record": {"percent": 85},
            },
            "thermal_probe": {"state": 0},
        }
        plan = SimpleNamespace(idle_cooldown_seconds=300)
        with mock.patch.object(
            RUNNER,
            "_cooldown_clock",
            side_effect=[0.0, 300.0, 300.0],
        ), mock.patch.object(
            RUNNER, "_host_condition_snapshot", return_value=snapshot
        ), mock.patch.object(
            RUNNER, "_post_cooldown_execution_integrity"
        ) as integrity:
            record = RUNNER._enforce_idle_cooldown(
                plan,
                snapshot,
                Path("/registered/output/parent"),
            )
        self.assertTrue(record["valid"])
        integrity.assert_called_once_with(
            plan,
            observed_output_parent=Path("/registered/output/parent"),
        )

    def test_descriptor_safe_result_root_creation_rejects_symlink_swap(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            plan = self._plan(root)
            RUNNER._descriptor_safe_create_result_root(
                plan,
                plan.result_root,
            )
            self.assertTrue(plan.result_root.is_dir())

        with tempfile.TemporaryDirectory() as tmp, (
            tempfile.TemporaryDirectory()
        ) as escape_tmp:
            root = Path(tmp).resolve()
            plan = self._plan(root)
            (root / "results").mkdir()
            (root / "results" / "comparison").symlink_to(
                Path(escape_tmp),
                target_is_directory=True,
            )
            with self.assertRaises(RUNNER.RunnerError):
                RUNNER._descriptor_safe_create_result_root(
                    plan,
                    plan.result_root,
                )
            self.assertFalse(plan.result_root.exists())

    def test_generic_contained_execution_failure_dispatches_to_sealer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            plan = self._plan(root)
            output = plan.result_root
            sealed = {"schema_version": RUNNER.SESSION_SCHEMA}

            def contained_failure(
                _plan: RUNNER.Plan, output_path: Path
            ) -> None:
                output_path.mkdir(parents=True)
                observed = output_path.lstat()
                state = RUNNER._ACTIVE_STATE
                assert state is not None
                state.output_identity = (
                    observed.st_dev,
                    observed.st_ino,
                )
                (output_path / "run_manifest.json").write_text(
                    "{}\n", encoding="utf-8"
                )
                assert state.completed_runs is not None
                state.completed_runs.append({"attempt_id": "attempt-001"})
                raise OSError("injected post-containment failure")

            with mock.patch.object(
                RUNNER, "_ORIGINAL_EXECUTE", side_effect=contained_failure
            ), mock.patch.object(
                RUNNER, "_runtime_patches", return_value=nullcontext()
            ), mock.patch.object(
                RUNNER,
                "_seal_generic_execution_abort",
                return_value=sealed,
            ) as sealer:
                observed_session = RUNNER.execute(plan, output)

            self.assertIs(observed_session, sealed)
            self.assertEqual(sealer.call_count, 1)
            self.assertIs(sealer.call_args.args[0], plan)
            self.assertEqual(sealer.call_args.args[1], output)
            self.assertIsInstance(sealer.call_args.args[3], OSError)

    def test_zero_attempt_abort_repairs_missing_or_corrupt_manifest(
        self,
    ) -> None:
        for manifest_state in ("missing", "corrupt", "partial"):
            with self.subTest(manifest_state=manifest_state), (
                tempfile.TemporaryDirectory()
            ) as tmp:
                root = Path(tmp).resolve()
                plan = self._plan(root)
                output = plan.result_root
                snapshot = self._valid_host_snapshot()

                def fail_after_mkdir(
                    _plan: RUNNER.Plan, output_path: Path
                ) -> None:
                    RUNNER._descriptor_safe_create_result_root(
                        _plan,
                        output_path,
                    )
                    state = RUNNER._ACTIVE_STATE
                    assert state is not None
                    self._install_completed_preflight(state)
                    if manifest_state == "corrupt":
                        (output_path / "run_manifest.json").write_text(
                            "{\n",
                            encoding="utf-8",
                        )
                    elif manifest_state == "partial":
                        (output_path / "run_manifest.json").write_text(
                            '{"schema_version":"partial"}\n',
                            encoding="utf-8",
                        )
                    raise OSError(
                        f"injected {manifest_state} manifest failure"
                    )

                with mock.patch.object(
                    RUNNER,
                    "_ORIGINAL_EXECUTE",
                    side_effect=fail_after_mkdir,
                ), mock.patch.object(
                    RUNNER,
                    "_host_condition_snapshot",
                    return_value=snapshot,
                ):
                    session = RUNNER.execute(plan, output)

                self.assertEqual(
                    session["host_abort"]["stage"],
                    "orchestration",
                )
                self.assertEqual(
                    session["host_abort"]["attempt_id"],
                    "000001__pair-001__rep-001__reference",
                )
                self.assertEqual(
                    (output / "runs.jsonl").read_bytes(),
                    b"",
                )
                self.assertEqual(
                    (output / "pairs.jsonl").read_bytes(),
                    b"",
                )
                manifest = json.loads(
                    (output / "run_manifest.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(manifest["schema_version"], RUNNER.PLAN_SCHEMA)
                ANALYZER.validate_host_abort(session["host_abort"])
                diagnostic = json.loads(
                    (output / "orchestration_abort.json").read_text(
                        encoding="utf-8"
                    )
                )
                ANALYZER.validate_orchestration_abort(
                    diagnostic,
                    session["host_abort"],
                )
                self._assert_frozen_tree(output)

    def test_zero_attempt_abort_purges_all_partial_inventory_safely(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp, (
            tempfile.TemporaryDirectory()
        ) as outside_tmp:
            root = Path(tmp).resolve()
            outside = Path(outside_tmp).resolve()
            sentinel = outside / "must-survive.txt"
            sentinel.write_text("outside\n", encoding="utf-8")
            plan = self._plan(root)
            output = plan.result_root
            snapshot = self._valid_host_snapshot()

            def fail_with_partial_inventory(
                _plan: RUNNER.Plan, output_path: Path
            ) -> None:
                RUNNER._descriptor_safe_create_result_root(
                    _plan,
                    output_path,
                )
                state = RUNNER._ACTIVE_STATE
                assert state is not None
                self._install_completed_preflight(state)
                partial_attempt = (
                    output_path / "attempts" / "000001__partial"
                )
                partial_attempt.mkdir(parents=True)
                (partial_attempt / "runner_metadata.json").write_text(
                    "{}\n",
                    encoding="utf-8",
                )
                (partial_attempt / "outside-link").symlink_to(outside)
                partial_block = output_path / "blocks" / "partial-block"
                partial_block.mkdir(parents=True)
                (partial_block / "config.yaml").write_text(
                    "partial: true\n",
                    encoding="utf-8",
                )
                (output_path / ".partial-root-write").write_text(
                    "partial\n",
                    encoding="utf-8",
                )
                (output_path / "run_manifest.json").write_text(
                    "{\n",
                    encoding="utf-8",
                )
                raise OSError("injected partial attempt directory")

            with mock.patch.object(
                RUNNER,
                "_ORIGINAL_EXECUTE",
                side_effect=fail_with_partial_inventory,
            ), mock.patch.object(
                RUNNER,
                "_host_condition_snapshot",
                return_value=snapshot,
            ):
                session = RUNNER.execute(plan, output)

            self.assertTrue(sentinel.is_file())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "outside\n")
            self.assertEqual(list((output / "attempts").iterdir()), [])
            self.assertEqual(list((output / "blocks").iterdir()), [])
            self.assertFalse((output / ".partial-root-write").exists())
            manifest = json.loads(
                (output / "run_manifest.json").read_text(encoding="utf-8")
            )
            guard = ANALYZER._EvidenceGuard()
            try:
                ANALYZER._validate_runs_csv(output, [], guard)
                ANALYZER._validate_session_inventory(
                    output,
                    runs=[],
                    pairs=[],
                    manifest=manifest,
                    host_abort=session["host_abort"],
                    orchestration_abort_present=True,
                    guard=guard,
                )
            finally:
                guard.close()
            self._assert_frozen_tree(output)

    def test_zero_attempt_purge_rejects_unknown_root_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            plan = self._plan(root)
            output = plan.result_root
            output.mkdir(parents=True)
            marker = output / "partial"
            marker.write_text("retain\n", encoding="utf-8")
            state = RUNNER._ExecutionState(plan, output)
            observed = output.stat()
            state.output_identity = (observed.st_dev, observed.st_ino + 1)
            with self.assertRaises(
                RUNNER.UnsafeEvidencePurgeError
            ):
                RUNNER._purge_zero_attempt_result_root(state)
            self.assertEqual(marker.read_text(encoding="utf-8"), "retain\n")

    def test_real_scheduler_pre_attempt_failure_seals_and_fails_gate(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            plan = self._plan(root)
            output = plan.result_root
            snapshot = self._valid_host_snapshot()

            def preflight_record(
                _plan: RUNNER.Plan,
                _filesystem_path: Path | None = None,
            ) -> dict[str, object]:
                state = RUNNER._ACTIVE_STATE
                assert state is not None
                state.preflight_snapshot = snapshot
                return {"host_conditions": snapshot}

            def completed_cooldown(
                _plan: RUNNER.Plan,
                _before: dict[str, object],
                _filesystem_path: Path,
            ) -> dict[str, object]:
                state = RUNNER._ACTIVE_STATE
                assert state is not None
                cooldown = {
                    "required_seconds": 300,
                    "eligible": True,
                    "performed": True,
                    "elapsed_seconds": 300.0,
                    "before": snapshot,
                    "after": snapshot,
                    "valid": True,
                }
                state.cooldown_record = cooldown
                state.cooldown_baseline_percent = 85
                return cooldown

            with mock.patch.object(
                RUNNER,
                "_ORIGINAL_PREFLIGHT",
                side_effect=preflight_record,
            ), mock.patch.object(
                RUNNER, "_require_zero_git_remotes"
            ), mock.patch.object(
                RUNNER,
                "_enforce_idle_cooldown",
                side_effect=completed_cooldown,
            ), mock.patch.object(
                RUNNER,
                "_host_condition_snapshot",
                return_value=snapshot,
            ), mock.patch.object(
                RUNNER._v1,
                "render_config",
                side_effect=OSError(
                    "injected after manifest before first attempt"
                ),
            ), mock.patch.object(
                RUNNER,
                "_ORIGINAL_FREEZE_TREE",
                side_effect=OSError("injected primary freeze failure"),
            ):
                session = RUNNER.execute(plan, output)

            manifest = json.loads(
                (output / "run_manifest.json").read_text(encoding="utf-8")
            )
            ANALYZER.validate_host_abort_block_prefix(
                session["host_abort"],
                manifest["blocks"][0],
                [],
                ANALYZER.METHODS,
                total_run_count=0,
            )
            gate = ANALYZER.registered_fidelity_gate([], [])
            self.assertFalse(gate["gate_pass"])
            self.assertFalse(session["all_attempts_completed"])
            self.assertFalse(session["all_pairs_valid"])
            self.assertFalse(session["session_power_valid"])
            self._assert_frozen_tree(output)

    def test_result_root_inode_swap_cannot_enter_generic_sealer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            plan = self._plan(root)
            output = plan.result_root

            def swap_then_fail(
                _plan: RUNNER.Plan, output_path: Path
            ) -> None:
                output_path.mkdir(parents=True)
                observed = output_path.lstat()
                state = RUNNER._ACTIVE_STATE
                assert state is not None
                state.output_identity = (
                    observed.st_dev,
                    observed.st_ino,
                )
                displaced = output_path.with_name(
                    f"{output_path.name}-displaced"
                )
                output_path.rename(displaced)
                output_path.mkdir()
                (output_path / "run_manifest.json").write_text(
                    "{}\n", encoding="utf-8"
                )
                raise OSError("injected after result-root swap")

            with mock.patch.object(
                RUNNER, "_ORIGINAL_EXECUTE", side_effect=swap_then_fail
            ), mock.patch.object(
                RUNNER, "_runtime_patches", return_value=nullcontext()
            ), mock.patch.object(
                RUNNER, "_seal_generic_execution_abort"
            ) as sealer:
                with self.assertRaisesRegex(
                    RUNNER.RunnerError,
                    "result_root identity changed",
                ):
                    RUNNER.execute(plan, output)
            sealer.assert_not_called()

    def test_unidentified_result_root_cannot_enter_generic_sealer(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            plan = self._plan(root)
            output = plan.result_root

            def unguarded_mkdir_then_fail(
                _plan: RUNNER.Plan, output_path: Path
            ) -> None:
                output_path.mkdir(parents=True)
                raise OSError("injected unguarded output creation")

            with mock.patch.object(
                RUNNER,
                "_ORIGINAL_EXECUTE",
                side_effect=unguarded_mkdir_then_fail,
            ), mock.patch.object(
                RUNNER, "_runtime_patches", return_value=nullcontext()
            ), mock.patch.object(
                RUNNER, "_seal_generic_execution_abort"
            ) as sealer:
                with self.assertRaisesRegex(
                    OSError,
                    "unguarded output creation",
                ):
                    RUNNER.execute(plan, output)
            sealer.assert_not_called()

    def test_after_probe_failure_on_second_method_returns_for_pair_sealing(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self._plan(root)
            output = plan.result_root
            output.mkdir(parents=True)
            attempts_root = output / "attempts"
            attempts_root.mkdir()
            attempt_id = "000002__block-001__optimized"
            run_dir = attempts_root / attempt_id
            run_dir.mkdir()
            (run_dir / "result.json").write_text(
                "{}\n", encoding="utf-8"
            )
            before = {
                "valid": True,
                "power_probe": {"battery_record": {"percent": 85}},
            }
            after = {
                "valid": False,
                "power_probe": {"battery_record": {"percent": 83}},
            }
            control = RUNNER._new_process_control()
            control.update(
                {
                    "timed_out": False,
                    "child_reaped": True,
                    "process_group_empty_after_reap": True,
                    "valid": True,
                }
            )
            state = RUNNER._ExecutionState(plan, output)
            RUNNER._ACTIVE_STATE = state

            def measured_result(*_args: object, **_kwargs: object) -> dict:
                assert state.current_attempt_id is not None
                assert state.process_controls is not None
                state.process_controls[state.current_attempt_id] = control
                return {
                    "schema_version": "old",
                    "terminal_category": "completed",
                    "orchestration_error": None,
                }

            try:
                with mock.patch.object(
                    RUNNER,
                    "_host_condition_snapshot",
                    side_effect=[before, after],
                ), mock.patch.object(
                    RUNNER,
                    "_ORIGINAL_RUN_ATTEMPT",
                    side_effect=measured_result,
                ), mock.patch.object(
                    RUNNER, "_ORIGINAL_FREEZE_TREE"
                ):
                    result = RUNNER._run_attempt(
                        plan,
                        SimpleNamespace(),
                        "block-001",
                        1,
                        "AB",
                        2,
                        2,
                        SimpleNamespace(method_id="optimized"),
                        SimpleNamespace(),
                        attempts_root,
                    )
            finally:
                RUNNER._ACTIVE_STATE = None

            self.assertFalse(result["host_attempt_valid"])
            self.assertEqual(state.host_abort["stage"], "after-attempt")
            self.assertEqual(state.host_abort["order_position"], 2)
            self.assertEqual(
                state.host_abort["attempt_id"],
                attempt_id,
            )
            self.assertIs(
                state.host_abort["failed_snapshot"],
                after,
            )
            self.assertIsNone(state.host_abort["probe_error"])
            self.assertIsNotNone(state.pending_power_abort)

    def test_post_containment_replace_failure_is_sealed_as_wrapper(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self._plan(root)
            output = plan.result_root
            output.mkdir(parents=True)
            attempts_root = output / "attempts"
            attempts_root.mkdir()
            attempt_id = "000001__block-001__reference"
            run_dir = attempts_root / attempt_id
            run_dir.mkdir()
            (run_dir / "result.json").write_text(
                "{}\n", encoding="utf-8"
            )
            snapshot = {
                "valid": True,
                "power_probe": {"battery_record": {"percent": 85}},
            }
            control = RUNNER._new_process_control()
            control.update(
                {
                    "timed_out": False,
                    "child_reaped": True,
                    "process_group_empty_after_reap": True,
                    "valid": True,
                }
            )
            state = RUNNER._ExecutionState(plan, output)
            RUNNER._ACTIVE_STATE = state

            def measured_result(*_args: object, **_kwargs: object) -> dict:
                assert state.current_attempt_id is not None
                assert state.process_controls is not None
                state.process_controls[state.current_attempt_id] = control
                return {
                    "schema_version": "old",
                    "terminal_category": "completed",
                    "orchestration_error": None,
                }

            try:
                with mock.patch.object(
                    RUNNER,
                    "_host_condition_snapshot",
                    side_effect=[snapshot, snapshot],
                ), mock.patch.object(
                    RUNNER,
                    "_ORIGINAL_RUN_ATTEMPT",
                    side_effect=measured_result,
                ), mock.patch.object(
                    RUNNER,
                    "_replace_json",
                    side_effect=OSError("injected replacement failure"),
                ), mock.patch.object(
                    RUNNER, "_ORIGINAL_FREEZE_TREE"
                ):
                    result = RUNNER._run_attempt(
                        plan,
                        SimpleNamespace(),
                        "block-001",
                        1,
                        "AB",
                        1,
                        1,
                        SimpleNamespace(method_id="reference"),
                        SimpleNamespace(),
                        attempts_root,
                    )
            finally:
                RUNNER._ACTIVE_STATE = None

            self.assertEqual(
                result["terminal_category"],
                "measurement-wrapper-failure",
            )
            self.assertIn(
                "injected replacement failure",
                result["orchestration_error"],
            )
            persisted = json.loads(
                (run_dir / "result.json").read_text(encoding="utf-8")
            )
            self.assertEqual(persisted, result)
            self.assertEqual(state.completed_runs, [result])

    def test_two_attempt_aggregate_failure_synthesizes_terminal_record(
        self,
    ) -> None:
        for attempt_kind in ("measured", "warmup"):
            with self.subTest(attempt_kind=attempt_kind), (
                tempfile.TemporaryDirectory()
            ) as tmp:
                root = Path(tmp).resolve()
                plan = self._plan(root)
                output = plan.result_root
                output.mkdir(parents=True)
                (output / "attempts").mkdir()
                (output / "blocks").mkdir()
                state = RUNNER._ExecutionState(plan, output)
                self._install_completed_preflight(state)
                manifest = RUNNER._canonical_zero_attempt_manifest(
                    plan,
                    state,
                )
                block = manifest["blocks"][0]
                if attempt_kind == "warmup":
                    block = {
                        "block_id": "warmup-001-aaaaaaaaaaaa",
                        "pair_id": "pair-001",
                        "repetition": 0,
                        "order": "warmup",
                        "environment": "fractal_high",
                        "seed": 1,
                        "robot_count": 192,
                        "map_sha256": "a" * 64,
                    }
                    manifest["warmup_blocks"] = [block]
                RUNNER._emergency_replace_json(
                    output / "run_manifest.json",
                    manifest,
                )
                runs = []
                method_order = (
                    ("reference", "optimized")
                    if block["order"] in {"AB", "warmup"}
                    else ("optimized", "reference")
                )
                for sequence, method in enumerate(
                    method_order,
                    start=1,
                ):
                    run = timing_run_record()
                    run.update(
                        {
                            "sequence": sequence,
                            "attempt_id": (
                                f"{sequence:06d}__"
                                f"{block['block_id']}__{method}"
                            ),
                            "block_id": block["block_id"],
                            "pair_id": block["pair_id"],
                            "repetition": block["repetition"],
                            "attempt_kind": attempt_kind,
                            "order": block["order"],
                            "order_position": sequence,
                            "mode": "fidelity",
                            "method": method,
                        }
                    )
                    run["provenance"].update(
                        {
                            "run_id": run["attempt_id"],
                            "implementation_id": method,
                            "mode": "fidelity",
                            "repetition": block["repetition"],
                            "order": block["order"],
                            "attempt_kind": attempt_kind,
                        }
                    )
                    run_dir = (
                        output / "attempts" / str(run["attempt_id"])
                    )
                    run_dir.mkdir()
                    RUNNER._emergency_replace_json(
                        run_dir / "result.json",
                        run,
                    )
                    runs.append(run)
                block_dir = output / "blocks" / str(block["block_id"])
                block_dir.mkdir()
                before = analyzer_host_snapshot(stage="block-boundary")
                after = analyzer_host_snapshot(stage="block-boundary")
                assert state.block_boundary_before is not None
                assert state.block_boundary_after is not None
                state.block_boundary_before[str(block["block_id"])] = before
                state.block_boundary_after[str(block["block_id"])] = after
                state.completed_runs = runs
                state.process_started_attempts = {
                    str(run["attempt_id"]) for run in runs
                }
                RUNNER._ACTIVE_STATE = state
                completion = analyzer_host_snapshot(
                    stage="session-complete"
                )
                try:
                    with mock.patch.object(
                        RUNNER,
                        "_host_condition_snapshot",
                        return_value=completion,
                    ):
                        session = RUNNER._seal_generic_execution_abort(
                            plan,
                            output,
                            state,
                            OSError("injected aggregate write failure"),
                        )
                finally:
                    RUNNER._ACTIVE_STATE = None

                self.assertFalse(session["all_pairs_valid"])
                self.assertEqual(
                    runs[-1]["terminal_category"],
                    "measurement-wrapper-failure",
                )
                self.assertIsNone(runs[-1]["task_valid"])
                self.assertIsNone(runs[-1]["fidelity_metrics"])
                if attempt_kind == "measured":
                    pair = json.loads(
                        (
                            block_dir / "pair.json"
                        ).read_text(encoding="utf-8")
                    )
                    self.assertFalse(pair["pair_valid"])
                    self.assertEqual(
                        pair["terminal_categories"][method_order[-1]],
                        "measurement-wrapper-failure",
                    )
                    ANALYZER.validate_pair_record(pair)
                else:
                    warmup = json.loads(
                        (
                            block_dir / "warmup.json"
                        ).read_text(encoding="utf-8")
                    )
                    self.assertFalse(warmup["warmup_valid"])
                    self.assertEqual(
                        warmup["terminal_categories"][method_order[-1]],
                        "measurement-wrapper-failure",
                    )
                self._assert_frozen_tree(output)

    def test_invalid_block_boundary_seals_before_next_attempt_launch(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self._plan(root)
            output = plan.result_root
            valid_snapshot = {"valid": True}
            attempted_launch = mock.Mock()

            def boundary_then_attempt(
                _plan: RUNNER.Plan, output_path: Path
            ) -> None:
                output_path.mkdir(parents=True)
                (output_path / "attempts").mkdir()
                (output_path / "run_manifest.json").write_text(
                    "{}\n", encoding="utf-8"
                )
                (output_path / "runs.jsonl").write_text(
                    "", encoding="utf-8"
                )
                (output_path / "pairs.jsonl").write_text(
                    "", encoding="utf-8"
                )
                state = RUNNER._ACTIVE_STATE
                assert state is not None
                state.preflight_snapshot = valid_snapshot
                state.cooldown_record = {
                    "valid": True,
                    "before": valid_snapshot,
                    "after": valid_snapshot,
                }
                state.cooldown_baseline_percent = 85
                with RUNNER._probe_context("block-boundary"):
                    failed = RUNNER._structured_invalid_host_snapshot(
                        _plan,
                        output_path,
                        "injected invalid boundary",
                    )
                RUNNER._register_block_boundary_sample(
                    state,
                    failed,
                    None,
                )
                RUNNER._run_attempt(
                    _plan,
                    SimpleNamespace(),
                    "block-001",
                    1,
                    "AB",
                    1,
                    1,
                    SimpleNamespace(method_id="reference"),
                    SimpleNamespace(),
                    output_path / "attempts",
                )

            with mock.patch.object(
                RUNNER, "_ORIGINAL_EXECUTE", side_effect=boundary_then_attempt
            ), mock.patch.object(
                RUNNER, "_runtime_patches", return_value=nullcontext()
            ), mock.patch.object(
                RUNNER,
                "_ORIGINAL_RUN_ATTEMPT",
                attempted_launch,
            ), mock.patch.object(
                RUNNER,
                "_host_condition_snapshot",
                return_value=valid_snapshot,
            ), mock.patch.object(
                RUNNER, "_write_runs_csv_if_missing"
            ), mock.patch.object(
                RUNNER, "_ORIGINAL_FREEZE_TREE"
            ) as freeze:
                session = RUNNER.execute(plan, output)

            attempted_launch.assert_not_called()
            abort = session["host_abort"]
            self.assertEqual(abort["stage"], "block-boundary")
            self.assertEqual(abort["attempt_id"], "000001__block-001__reference")
            self.assertEqual(abort["block_id"], "block-001")
            self.assertEqual(abort["method"], "reference")
            self.assertEqual(abort["order_position"], 1)
            self.assertIsNone(abort["probe_error"])
            self.assertEqual(
                abort["failed_snapshot"]["power_policy_evaluation"]["stage"],
                "block-boundary",
            )
            self.assertEqual(session["executed_method_attempt_slots"], 0)
            self.assertTrue((output / "session.json").is_file())
            freeze.assert_called_once_with(output)

    def test_probe_exception_after_output_creation_seals_partial_evidence(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = self._plan(root)
            output = plan.result_root
            valid_snapshot = {"valid": True}

            def probe_then_abort(
                _plan: RUNNER.Plan, output_path: Path
            ) -> None:
                output_path.mkdir(parents=True)
                (output_path / "attempts").mkdir()
                (output_path / "run_manifest.json").write_text(
                    "{}\n", encoding="utf-8"
                )
                (output_path / "runs.jsonl").write_text(
                    "", encoding="utf-8"
                )
                (output_path / "pairs.jsonl").write_text(
                    "", encoding="utf-8"
                )
                state = RUNNER._ACTIVE_STATE
                assert state is not None
                state.preflight_snapshot = valid_snapshot
                state.cooldown_record = {
                    "valid": True,
                    "before": valid_snapshot,
                    "after": valid_snapshot,
                }
                state.cooldown_baseline_percent = 85
                RUNNER._run_attempt(
                    _plan,
                    SimpleNamespace(),
                    "block-001",
                    1,
                    "AB",
                    1,
                    1,
                    SimpleNamespace(method_id="reference"),
                    SimpleNamespace(),
                    output_path / "attempts",
                )

            with mock.patch.object(
                RUNNER, "_ORIGINAL_EXECUTE", side_effect=probe_then_abort
            ), mock.patch.object(
                RUNNER, "_runtime_patches", return_value=nullcontext()
            ), mock.patch.object(
                RUNNER,
                "_ORIGINAL_HOST_SNAPSHOT",
                side_effect=OSError("disk probe failed"),
            ), mock.patch.object(
                RUNNER, "_write_runs_csv_if_missing"
            ), mock.patch.object(
                RUNNER, "_ORIGINAL_FREEZE_TREE"
            ) as freeze:
                session = RUNNER.execute(plan, output)

            abort = session["host_abort"]
            self.assertEqual(abort["stage"], "before-attempt")
            self.assertEqual(abort["attempt_id"], "000001__block-001__reference")
            self.assertEqual(abort["block_id"], "block-001")
            self.assertEqual(abort["method"], "reference")
            self.assertEqual(abort["order_position"], 1)
            self.assertIsNone(abort["failed_snapshot"])
            self.assertIn("OSError: disk probe failed", abort["probe_error"])
            self.assertFalse(
                session["session_completion_host_conditions"]["valid"]
            )
            self.assertFalse(session["session_power_valid"])
            self.assertTrue((output / "session.json").is_file())
            freeze.assert_called_once_with(output)


if __name__ == "__main__":
    unittest.main()
