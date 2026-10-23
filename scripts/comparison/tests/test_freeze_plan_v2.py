from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from collections.abc import Callable
from pathlib import Path
from unittest import mock

from scripts.comparison import freeze_plan_v2 as FREEZER



def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        text=True,
        capture_output=True,
    )
    return completed.stdout.strip()


ANALYZER_STUB = b"""#!/usr/bin/env python3
import argparse
import hashlib
import json
from pathlib import Path

def canonical_json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\\n").encode()

def sha256_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def validate_session(session):
    session = session.resolve()
    fields = {
        "frozen_plan_json_sha256": "frozen_plan.json",
        "run_manifest_json_sha256": "run_manifest.json",
        "preflight_json_sha256": "preflight.json",
        "session_json_sha256": "session.json",
        "runs_jsonl_sha256": "runs.jsonl",
        "pairs_jsonl_sha256": "pairs.jsonl",
    }
    input_session = {"path": str(session)}
    for field, filename in fields.items():
        input_session[field] = sha256_file(session / filename)
    return {"input_session": input_session}

def analyze_validated_sessions(sessions):
    return {
        "mode": "fidelity",
        "design_role": "outcome-informed-exploratory-fidelity",
        "input_sessions": [item["input_session"] for item in sessions],
        "fidelity_gate": {
            "planned_method_attempt_slots": 40,
            "executed_attempt_count": 40,
            "planned_pair_blocks": 20,
            "completed_attempt_count": 40,
            "valid_pair_count": 20,
            "host_valid_pair_count": 20,
            "frames_compared_per_pair": 200,
            "gate_pass": True,
        },
        "timing_screen": None,
    }

def fidelity_gate_receipt(*, analyzer_sha256, input_sessions, gate):
    return {
        "schema_version": "dancers-amended-fidelity-gate-v1",
        "amendment_id": "fh-lower-count-fidelity-2026-07-28-v1",
        "design_role": "outcome-informed-exploratory-fidelity",
        "analyzer_sha256": analyzer_sha256,
        "input_sessions": list(input_sessions),
        "planned_method_attempt_slots": gate["planned_method_attempt_slots"],
        "executed_attempt_count": gate["executed_attempt_count"],
        "planned_pair_blocks": gate["planned_pair_blocks"],
        "completed_attempt_count": gate["completed_attempt_count"],
        "valid_pair_count": gate["valid_pair_count"],
        "host_valid_pair_count": gate["host_valid_pair_count"],
        "frames_compared_per_pair": gate["frames_compared_per_pair"],
        "gate_pass": gate["gate_pass"],
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--fidelity-gate-receipt-output", required=True, type=Path
    )
    args = parser.parse_args()
    validated = validate_session(args.session)
    combined = analyze_validated_sessions([validated])
    receipt = fidelity_gate_receipt(
        analyzer_sha256=sha256_file(Path(__file__).resolve()),
        input_sessions=combined["input_sessions"],
        gate=combined["fidelity_gate"],
    )
    args.output.write_bytes(canonical_json_bytes({"fixture_analysis": True}))
    args.fidelity_gate_receipt_output.write_bytes(
        canonical_json_bytes(receipt)
    )

if __name__ == "__main__":
    main()
"""


class V2Fixture:
    def __init__(self, directory: Path) -> None:
        self.repository = (directory / "repository").resolve()
        self.repository.mkdir()
        git(self.repository, "init", "-q")
        self.tracked: list[Path] = []

        self.base_protocol = self.write(
            Path("docs/comparison/EXPERIMENT_PROTOCOL.md"),
            b"test base protocol\n",
        )
        self.experiment = self.write_json(
            Path("docs/comparison/experiment_manifest.json"),
            {"schema_version": "test-base"},
        )
        self.amendment_protocol = self.write(
            Path(
                "docs/comparison/"
                "EXPERIMENT_PROTOCOL_AMENDMENT_FH_LOWER_V1.md"
            ),
            b"test amendment protocol\n",
        )
        self.plan_freezer = self.write(
            FREEZER.PLAN_FREEZER_PATH,
            b"#!/usr/bin/env python3\n# test plan freezer v2\n",
            executable=True,
        )
        self.runner = self.write(
            FREEZER.RUNNER_PATH,
            b"#!/usr/bin/env python3\n# test runner v2\n",
            executable=True,
        )
        self.analyzer = self.write(
            FREEZER.ANALYZER_PATH,
            ANALYZER_STUB,
            executable=True,
        )
        self.runner_v1_base = self.write(
            FREEZER.RUNNER_V1_BASE_PATH,
            b"#!/usr/bin/env python3\n# test runner v1 base\n",
        )
        self.analyzer_v1_base = self.write(
            FREEZER.ANALYZER_V1_BASE_PATH,
            b"#!/usr/bin/env python3\n# test analyzer v1 base\n",
        )
        self.trace_compare = self.write(
            FREEZER.TRACE_COMPARE_PATH,
            b"#!/usr/bin/env python3\n# test trace comparison\n",
        )
        self.capacity_decision = self.write(
            FREEZER.CAPACITY_DECISION_PATH,
            b"#!/usr/bin/env python3\n# test capacity decision\n",
        )
        self.pixi_lock = self.write(
            FREEZER.PIXI_LOCK_PATH, b"test pixi lock\n"
        )
        self.config = self.write(
            FREEZER.CONFIG_TEMPLATE_PATH,
            b"TIMESTEP: 0.1\n",
        )
        self.materializer = self.write(
            FREEZER.MATERIALIZER_PATH,
            b"#!/usr/bin/env python3\n# test capacity materializer\n",
        )
        self.environment_generator = self.write(
            FREEZER.ENVIRONMENT_GENERATOR_PATH,
            b"#!/usr/bin/env python3\n# test environment generator\n",
        )

        self.map_bytes = b"test fractal-high calibration map\n"
        self.map_path = self.write(
            Path(
                "comparison/environments/"
                "fractal_high/calibration/map.png"
            ),
            self.map_bytes,
        )
        self.scenario_bytes = {
            192: b'{"num_robots":192,"fixture":true}\n',
            256: b'{"num_robots":256,"fixture":true}\n',
        }
        self.bank = self.write(
            FREEZER.FRACTAL_HIGH_CALIBRATION_BANK_PATH,
            b'{"fixture_capacity_bank":true}\n',
        )
        prefixes = [
            {
                "robot_count": count,
                "bytes": len(data),
                "sha256": digest_bytes(data),
            }
            for count, data in self.scenario_bytes.items()
        ]
        self.environment = self.write_json(
            Path("comparison/environments/manifest.json"),
            {
                "capacity_suite": {
                    "prefix_materializer": {
                        "exclusive_create_no_overwrite": True,
                        "path": FREEZER.MATERIALIZER_PATH.as_posix(),
                        "sha256": digest(self.materializer),
                    }
                },
                "environments": [
                    {
                        "family": "fractal_high",
                        "split": "calibration",
                        "map": {
                            "path": (
                                "fractal_high/calibration/map.png"
                            ),
                            "bytes": len(self.map_bytes),
                            "sha256": digest_bytes(self.map_bytes),
                        },
                        "capacity": {
                            "bank": {
                                "bytes": self.bank.stat().st_size,
                                "path": (
                                    "fractal_high/calibration/capacity/"
                                    "bank.json"
                                ),
                                "sha256": digest(self.bank),
                            },
                            "prefix_scenarios": prefixes,
                        },
                    }
                ],
                "generator": {
                    "path": FREEZER.ENVIRONMENT_GENERATOR_PATH.as_posix(),
                    "sha256": digest(self.environment_generator),
                    "stdlib_only": True,
                },
            },
        )
        self.environment_sidecar = self.write(
            FREEZER.ENVIRONMENT_MANIFEST_SIDECAR_PATH,
            f"{digest(self.environment)}  manifest.json\n".encode("ascii"),
        )

        self.trigger_analysis = self.write(
            FREEZER.ORIGINAL_TRIGGER_ANALYSIS_PATH,
            b'{"fixture_trigger_analysis":true}\n',
        )
        self.trigger_session_root = (
            self.repository / FREEZER.ORIGINAL_TRIGGER_SESSION_ROOT
        )
        self.trigger_files: dict[str, Path] = {}
        for filename in (
            "frozen_plan.json",
            "session.json",
            "runs.jsonl",
            "pairs.jsonl",
        ):
            self.trigger_files[filename] = self.write(
                FREEZER.ORIGINAL_TRIGGER_SESSION_ROOT / filename,
                f"{filename} historical fixture\n".encode("utf-8"),
            )

        self.commit("fixture base artifacts")

        identities: dict[str, tuple[str, str]] = {}
        builds: dict[str, dict] = {}
        for method in ("reference", "optimized"):
            source = (
                self.repository
                / "build"
                / "comparison"
                / "trace-v3"
                / f"{method}-src"
            )
            source.parent.mkdir(parents=True, exist_ok=True)
            git(
                self.repository,
                "worktree",
                "add",
                "-q",
                "-b",
                f"fixture-{method}",
                str(source),
                "HEAD",
            )
            (source / "identity.txt").write_text(
                f"{method}\n", encoding="utf-8"
            )
            git(source, "add", "identity.txt")
            git(
                source,
                "-c",
                "user.name=V2 Freezer Test",
                "-c",
                "user.email=v2-freezer@example.invalid",
                "commit",
                "-q",
                "-m",
                "identity",
            )
            identities[method] = (
                git(source, "rev-parse", "HEAD"),
                git(source, "rev-parse", "HEAD^{tree}"),
            )
            for mode in ("fidelity", "timing"):
                binary = (
                    self.repository
                    / "build"
                    / "comparison"
                    / "trace-v3"
                    / "build"
                    / f"{method}-{mode}"
                    / "dancers"
                )
                binary.parent.mkdir(parents=True)
                binary.write_bytes(
                    f"{method}-{mode}-binary\n".encode("ascii")
                )
                binary.chmod(0o755)
                builds[f"{method}_{mode}"] = {
                    "algorithm_revision": (
                        FREEZER.REFERENCE_PIN
                        if method == "reference"
                        else FREEZER.OPTIMIZED_PIN
                    ),
                    "applied_head": identities[method][0],
                    "observer_compiled": mode == "fidelity",
                    "source_dir": str(
                        source.relative_to(self.repository)
                    ),
                    "binary_path": str(
                        binary.relative_to(self.repository)
                    ),
                    "binary_sha256": digest(binary),
                    "registered_tests": "3/3 passed",
                }

        self.build_manifest = self.write_json(
            FREEZER.BUILD_MANIFEST_PATH,
            {
                "schema_version": FREEZER.BUILD_MANIFEST_SCHEMA,
                "repository": {
                    "remote_count": 0,
                    "origin_present": False,
                    "reference_pin": FREEZER.REFERENCE_PIN,
                    "optimized_pin": FREEZER.OPTIMIZED_PIN,
                    "reference_applied_head": identities["reference"][0],
                    "reference_tree": identities["reference"][1],
                    "optimized_applied_head": identities["optimized"][0],
                    "optimized_tree": identities["optimized"][1],
                },
                "toolchain": {
                    "pixi_lock_sha256": digest(self.pixi_lock),
                },
                "builds": builds,
                "gates": {
                    "dual_clean_builds": "pass",
                    "common_registered_tests_all_four_builds": "pass",
                    "timing_observer_compile_absence": "pass",
                    "smoke_semantic_trace_identity": "pass",
                    "calibration_permitted": True,
                },
            },
        )

        self.amendment = self.write_json(
            FREEZER.AMENDMENT_PATH,
            self.amendment_value(),
        )
        self.commit("fixture artifacts")
        self.amendment_sha256 = digest(self.amendment)

    def write(
        self,
        relative: Path,
        data: bytes,
        *,
        executable: bool = False,
    ) -> Path:
        path = self.repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if executable:
            path.chmod(0o755)
        self.tracked.append(relative)
        return path

    def write_json(self, relative: Path, value: dict) -> Path:
        return self.write(
            relative,
            (
                json.dumps(value, sort_keys=True, allow_nan=False) + "\n"
            ).encode("utf-8"),
        )

    def commit(self, message: str, *relative_paths: Path) -> None:
        selected = list(relative_paths) if relative_paths else self.tracked
        git(self.repository, "add", "--", *(str(item) for item in selected))
        git(
            self.repository,
            "-c",
            "user.name=V2 Freezer Test",
            "-c",
            "user.email=v2-freezer@example.invalid",
            "commit",
            "-q",
            "-m",
            message,
        )

    def amendment_value(self) -> dict:
        fidelity = {
            "capacity_search": False,
            "counts": [192, 256],
            "design_role": "outcome-informed-exploratory-fidelity",
            "environment": "fractal_high",
            "maximum_peak_rss_mib": 49152.0,
            "minimum_free_disk_bytes": 21_474_836_480,
            "omp_threads": 1,
            "planned_method_attempt_slots": 40,
            "planned_pair_blocks": 20,
            "fixed_work_timesteps": 100,
            "map_sha256": digest(self.map_path),
            "mode": "fidelity",
            "phase": "calibration",
            "plan_root": FREEZER.PLAN_ROOT.as_posix(),
            "registered_scenario_prefixes": {
                str(count): {
                    "bytes": len(data),
                    "sha256": digest_bytes(data),
                }
                for count, data in self.scenario_bytes.items()
            },
            "repetitions": 1,
            "result_root": FREEZER.RESULT_ROOT.as_posix(),
            "seeds": list(range(1, 11)),
            "selection_disclosure": FREEZER.SELECTION_DISCLOSURE,
            "serial_execution": True,
            "split": "calibration",
            "task_validity_predicate_id": (
                "fixed-horizon-safe-navigation-v1"
            ),
            "termination_grace_seconds": 0.5,
            "timeout_seconds": 300.0,
            "timestep_seconds": 0.1,
            "trace_detail": "digest",
            "trace_schema": "dancers-comparison-trace-v3",
            "warmup_attempts_per_binary_map": 0,
        }
        timing = {
            "capacity_search": False,
            "claim_eligibility": "exploratory-screening-only",
            "counts": [192, 256],
            "fixed_work_timesteps": 100,
            "maximum_peak_rss_mib": 49152.0,
            "minimum_free_disk_bytes": 21_474_836_480,
            "mode": "timing",
            "observer_compiled": False,
            "omp_threads": 1,
            "phase": "calibration",
            "planned_method_attempt_slots": 120,
            "planned_pair_blocks": 60,
            "power_policy_id": "darwin-ac-battery-stable-v1",
            "repetitions": 3,
            "seeds": list(range(1, 11)),
            "serial_execution": True,
            "task_validity_predicate_id": (
                "fixed-horizon-safe-navigation-v1"
            ),
            "termination_grace_seconds": 0.5,
            "timeout_seconds": 300.0,
            "timestep_seconds": 0.1,
            "warmups_per_binary_map": 1,
        }
        return {
            "amendment_id": FREEZER.AMENDMENT_ID,
            "amendment_protocol": {
                "path": str(
                    self.amendment_protocol.relative_to(self.repository)
                ),
                "sha256": digest(self.amendment_protocol),
            },
            "base_artifacts": {
                "protocol": {
                    "path": str(
                        self.base_protocol.relative_to(self.repository)
                    ),
                    "sha256": digest(self.base_protocol),
                },
                "experiment_manifest": {
                    "path": str(
                        self.experiment.relative_to(self.repository)
                    ),
                    "sha256": digest(self.experiment),
                },
                "environment_manifest": {
                    "path": str(
                        self.environment.relative_to(self.repository)
                    ),
                    "sha256": digest(self.environment),
                },
            },
            "fidelity_gate": FREEZER.EXPECTED_FIDELITY_GATE,
            "followup_fidelity_design": fidelity,
            "frozen_before_any_amended_attempt": True,
            "original_cohort_policy": {
                "all_attempt_denominator": True,
                "confirmatory_status": (
                    "no-valid-pair-under-original-300-second-design"
                ),
                "pool_with_followup_for_confirmatory_estimation": False,
                "replace_or_withdraw": False,
            },
            "power_policy": FREEZER.EXPECTED_POWER_POLICY,
            "process_control_policy": (
                FREEZER.EXPECTED_PROCESS_CONTROL_POLICY
            ),
            "prospective_capacity_unlock": {},
            "reporting": {},
            "schema_version": FREEZER.AMENDMENT_SCHEMA,
            "scientific_role": (
                "exploratory-feasibility-and-fidelity-gate-only"
            ),
            "status": "frozen-before-any-amended-attempt",
            "timing_if_fidelity_passes": timing,
            "trigger": {
                "analysis": {
                    "path": FREEZER.ORIGINAL_TRIGGER_ANALYSIS_PATH.as_posix(),
                    "sha256": digest(self.trigger_analysis),
                },
                "completed_utc": "2026-07-28T15:10:10.991023Z",
                "hashes": {
                    "frozen_plan_json_sha256": digest(
                        self.trigger_files["frozen_plan.json"]
                    ),
                    "pairs_jsonl_sha256": digest(
                        self.trigger_files["pairs.jsonl"]
                    ),
                    "runs_jsonl_sha256": digest(
                        self.trigger_files["runs.jsonl"]
                    ),
                    "session_json_sha256": digest(
                        self.trigger_files["session.json"]
                    ),
                },
                "original_grid": [384, 471],
                "original_executed_attempts": 40,
                "original_planned_attempt_slots": 40,
                "original_planned_pair_blocks": 20,
                "original_reference_anchor_n": 384,
                "session_path": (
                    FREEZER.ORIGINAL_TRIGGER_SESSION_ROOT.as_posix()
                ),
                "terminal_counts": {
                    "measurement-wrapper-failure": 1,
                    "runner-timeout": 39,
                },
                "valid_pair_count": 0,
            },
            "validation_data_accessed_at_freeze": False,
        }

    def output(self, mode: str) -> Path:
        name = (
            FREEZER.FIDELITY_OUTPUT_NAME
            if mode == "fidelity"
            else FREEZER.TIMING_OUTPUT_NAME
        )
        return self.repository / FREEZER.PLAN_ROOT / name

    def result(self, mode: str) -> Path:
        name = (
            FREEZER.FIDELITY_OUTPUT_NAME
            if mode == "fidelity"
            else FREEZER.TIMING_OUTPUT_NAME
        )
        return self.repository / FREEZER.RESULT_ROOT / name

    def materialize_authenticated(
        self,
        authenticated: object,
        environment_root: Path,
        family: str,
        split: str,
        count: int,
    ) -> bytes:
        self.assertIsAuthenticatedMaterializer(authenticated)
        self.assert_materializer_call(
            environment_root, family, split, count
        )
        return self.scenario_bytes[count]

    @staticmethod
    def assertIsAuthenticatedMaterializer(authenticated: object) -> None:
        if not isinstance(
            authenticated, FREEZER._AuthenticatedMaterializer
        ):
            raise AssertionError("missing authenticated source snapshot")

    def assert_materializer_call(
        self,
        environment_root: Path,
        family: str,
        split: str,
        count: int,
    ) -> None:
        if environment_root.resolve() != self.environment.parent.resolve():
            raise AssertionError("wrong environment root")
        if family != "fractal_high" or split != "calibration":
            raise AssertionError("wrong environment case")
        if count not in self.scenario_bytes:
            raise AssertionError("unregistered count")

    def freeze(
        self,
        mode: str,
        *,
        receipt: Path | None = None,
        output_root: Path | None = None,
        pin_current_amendment: bool = False,
        materializer: (
            Callable[[object, Path, str, str, int], bytes] | None
        ) = None,
    ) -> FREEZER.FrozenPlanV2:
        amendment_sha = (
            digest(self.amendment)
            if pin_current_amendment
            else self.amendment_sha256
        )
        with mock.patch.object(
            FREEZER,
            "_materialize_authenticated_scenario",
            side_effect=(
                self.materialize_authenticated
                if materializer is None
                else materializer
            ),
        ), mock.patch.object(
            FREEZER,
            "__file__",
            str(self.plan_freezer),
        ), mock.patch.object(
            FREEZER,
            "AMENDMENT_SHA256",
            amendment_sha,
        ):
            return FREEZER.freeze_plan_v2(
                repository_root=self.repository,
                mode=mode,
                output_root=(
                    self.output(mode) if output_root is None else output_root
                ),
                runner_path=self.runner,
                analyzer_path=self.analyzer,
                fidelity_gate_analysis=receipt,
            )

    def create_receipt(self) -> Path:
        plan_path = self.output("fidelity") / "plan.json"
        if not plan_path.exists():
            frozen = self.freeze("fidelity")
            plan_path = frozen.plan_path
            plan_files = [
                plan_path,
                *frozen.scenario_paths,
            ]
            for item in plan_files:
                relative = item.relative_to(self.repository)
                self.tracked.append(relative)
            self.commit(
                "frozen fidelity plan",
                *(
                    item.relative_to(self.repository)
                    for item in plan_files
                ),
            )
        session = self.result("fidelity")
        session.mkdir(parents=True)
        session_files: dict[str, Path] = {}
        for filename in (
            "frozen_plan.json",
            "run_manifest.json",
            "preflight.json",
            "session.json",
            "runs.jsonl",
            "pairs.jsonl",
        ):
            path = session / filename
            if filename == "frozen_plan.json":
                path.write_bytes(plan_path.read_bytes())
            else:
                path.write_text(
                    f"{filename} fixture\n", encoding="utf-8"
                )
            relative = path.relative_to(self.repository)
            self.tracked.append(relative)
            session_files[filename] = path
        path = (
            self.repository
            / FREEZER.RESULT_ROOT
            / "fidelity-gate-receipt.json"
        )
        with tempfile.TemporaryDirectory() as temporary:
            analysis = Path(temporary) / "analysis.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    "-E",
                    "-s",
                    "-S",
                    "-B",
                    str(self.analyzer),
                    "--session",
                    str(session),
                    "--output",
                    str(analysis),
                    "--fidelity-gate-receipt-output",
                    str(path),
                ],
                check=False,
                text=True,
                capture_output=True,
                env={
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr or completed.stdout)
        relative = path.relative_to(self.repository)
        self.tracked.append(relative)
        self.commit(
            "fidelity gate receipt",
            relative,
            *(
                item.relative_to(self.repository)
                for item in session_files.values()
            ),
        )
        return path


class FreezePlanV2Tests(unittest.TestCase):
    def test_fidelity_plan_binds_amendment_tools_and_denominators(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            frozen = fixture.freeze("fidelity")
            plan = json.loads(
                frozen.plan_path.read_text(encoding="utf-8")
            )

            self.assertEqual(plan["schema_version"], FREEZER.PLAN_SCHEMA_V2)
            self.assertEqual(plan["amendment_id"], FREEZER.AMENDMENT_ID)
            self.assertEqual(
                plan["design_role"],
                "outcome-informed-exploratory-fidelity",
            )
            self.assertEqual(
                plan["cohort_id"], FREEZER.FIDELITY_COHORT_ID
            )
            self.assertEqual(
                plan["result_root"], str(fixture.result("fidelity"))
            )
            self.assertEqual(plan["power_policy"], FREEZER.EXPECTED_POWER_POLICY)
            self.assertEqual(
                plan["process_control_policy"],
                FREEZER.EXPECTED_PROCESS_CONTROL_POLICY,
            )
            self.assertEqual(plan["runner_sha256"], digest(fixture.runner))
            self.assertEqual(
                plan["analyzer_sha256"], digest(fixture.analyzer)
            )
            self.assertEqual(plan["planned_method_attempt_slots"], 40)
            self.assertEqual(plan["planned_pair_blocks"], 20)
            self.assertEqual(plan["repetitions"], 1)
            self.assertEqual(plan["warmups_per_binary_map"], 0)
            self.assertEqual(len(plan["pairs"]), 20)
            self.assertEqual(
                sorted({item["robot_count"] for item in plan["pairs"]}),
                [192, 256],
            )
            self.assertEqual(
                sorted({item["seed"] for item in plan["pairs"]}),
                list(range(1, 11)),
            )
            self.assertEqual(
                set(plan["artifacts"]),
                {
                    "analysis_revision",
                    "analyzer_v1_base",
                    "amendment_protocol",
                    "build_manifest",
                    "capacity_materializer",
                    "config_template",
                    "environment_generator",
                    "environment_manifest",
                    "environment_manifest_sidecar",
                    "experiment_manifest",
                    "fractal_high_calibration_bank",
                    "fractal_high_calibration_map",
                    "freeze_capacity_decision",
                    "pixi_lock",
                    "plan_freezer",
                    "post_outcome_amendment",
                    "protocol",
                    "runner",
                    "runner_v1_base",
                    "trace_compare",
                    "trigger_analysis",
                    "trigger_frozen_plan",
                    "trigger_pairs",
                    "trigger_runs",
                    "trigger_session",
                },
            )
            for artifact in plan["artifacts"].values():
                self.assertTrue(Path(artifact["path"]).is_absolute())
                self.assertEqual(digest(Path(artifact["path"])), artifact["sha256"])
            for pair in plan["pairs"]:
                self.assertEqual(
                    pair["scenario"]["bytes"],
                    len(fixture.scenario_bytes[pair["robot_count"]]),
                )
                self.assertEqual(
                    digest(Path(pair["scenario"]["path"])),
                    pair["scenario"]["sha256"],
                )
            self.assertEqual(
                frozen.plan_sha256, digest(frozen.plan_path)
            )
            self.assertEqual(
                frozen.plan_path.read_bytes(),
                FREEZER.canonical_json_bytes(plan),
            )
            self.assertEqual(len(frozen.scenario_paths), 2)
            self.assertEqual(
                stat.S_IMODE(frozen.plan_path.stat().st_mode), 0o444
            )
            self.assertEqual(
                stat.S_IMODE(frozen.plan_path.parent.stat().st_mode),
                0o555,
            )
            self.assertEqual(
                stat.S_IMODE(
                    (frozen.plan_path.parent / "scenarios").stat().st_mode
                ),
                0o555,
            )
            for path in frozen.scenario_paths:
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o444)

    def test_timing_requires_and_binds_complete_fidelity_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            with self.assertRaisesRegex(
                FREEZER.FreezeV2Error, "requires a passed"
            ):
                fixture.freeze("timing")
            self.assertFalse(fixture.output("timing").exists())

            receipt = fixture.create_receipt()
            frozen = fixture.freeze("timing", receipt=receipt)
            plan = json.loads(
                frozen.plan_path.read_text(encoding="utf-8")
            )
            self.assertEqual(plan["design_role"], "exploratory-screening-only")
            self.assertEqual(
                plan["cohort_id"], FREEZER.TIMING_COHORT_ID
            )
            self.assertEqual(
                plan["result_root"], str(fixture.result("timing"))
            )
            self.assertEqual(plan["planned_method_attempt_slots"], 120)
            self.assertEqual(plan["planned_pair_blocks"], 60)
            self.assertEqual(plan["repetitions"], 3)
            self.assertEqual(plan["warmups_per_binary_map"], 1)
            self.assertEqual(
                plan["artifacts"]["fidelity_gate_analysis"],
                {
                    "path": str(receipt.resolve()),
                    "sha256": digest(receipt),
                },
            )
            for artifact_name, filename in (
                ("fidelity_frozen_plan", "frozen_plan.json"),
                ("fidelity_run_manifest", "run_manifest.json"),
                ("fidelity_preflight", "preflight.json"),
                ("fidelity_session", "session.json"),
                ("fidelity_runs", "runs.jsonl"),
                ("fidelity_pairs", "pairs.jsonl"),
            ):
                artifact = plan["artifacts"][artifact_name]
                ledger = fixture.result("fidelity") / filename
                self.assertEqual(artifact["path"], str(ledger))
                self.assertEqual(artifact["sha256"], digest(ledger))

    def test_mutable_tool_and_wrong_output_fail_before_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            fixture.runner.write_text(
                "#!/usr/bin/env python3\n# changed\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                FREEZER.FreezeV2Error, "mutable"
            ):
                fixture.freeze("fidelity")
            self.assertFalse(fixture.output("fidelity").exists())

        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            wrong = fixture.output("fidelity").with_name("ambiguous")
            with self.assertRaisesRegex(
                FREEZER.FreezeV2Error,
                "output_root must be the registered path",
            ):
                fixture.freeze("fidelity", output_root=wrong)
            self.assertFalse(wrong.exists())

    def test_symlinked_tool_is_rejected_even_when_git_committed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            fixture.analyzer.unlink()
            fixture.analyzer.symlink_to(
                fixture.runner.relative_to(fixture.analyzer.parent)
            )
            fixture.commit(
                "symlink analyzer",
                FREEZER.ANALYZER_PATH,
            )
            with self.assertRaisesRegex(
                FREEZER.FreezeV2Error, "symbolic link"
            ):
                fixture.freeze("fidelity")
            self.assertFalse(fixture.output("fidelity").exists())

    def test_mutable_transitive_tool_dependency_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            fixture.trace_compare.write_text(
                "#!/usr/bin/env python3\n# mutated trace reader\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                FREEZER.FreezeV2Error, "mutable"
            ):
                fixture.freeze("fidelity")
            self.assertFalse(fixture.output("fidelity").exists())

    def test_policy_or_denominator_drift_fails_closed(self) -> None:
        for field, value, error in (
            ("power_policy", {}, "power policy"),
            (
                "planned_pair_blocks",
                19,
                "fidelity design planned_pair_blocks",
            ),
        ):
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = V2Fixture(Path(temporary))
                    amendment = json.loads(
                        fixture.amendment.read_text(encoding="utf-8")
                    )
                    if field == "power_policy":
                        amendment[field] = value
                    else:
                        amendment["followup_fidelity_design"][field] = value
                    fixture.amendment.write_text(
                        json.dumps(amendment, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    fixture.commit(
                        f"drift {field}",
                        FREEZER.AMENDMENT_PATH,
                    )
                    with self.assertRaisesRegex(
                        FREEZER.FreezeV2Error, error
                    ):
                        fixture.freeze(
                            "fidelity", pin_current_amendment=True
                        )
                    self.assertFalse(
                        fixture.output("fidelity").exists()
                    )

    def test_nonpassing_or_mutable_timing_receipt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            receipt = fixture.create_receipt()
            raw = json.loads(receipt.read_text(encoding="utf-8"))
            raw["gate_pass"] = False
            receipt.write_text(
                FREEZER.canonical_json_bytes(raw).decode("utf-8"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                FREEZER.FreezeV2Error, "mutable"
            ):
                fixture.freeze("timing", receipt=receipt)
            self.assertFalse(fixture.output("timing").exists())

            fixture.commit(
                "record failed receipt",
                receipt.relative_to(fixture.repository),
            )
            with self.assertRaisesRegex(
                FREEZER.FreezeV2Error, "gate_pass"
            ):
                fixture.freeze("timing", receipt=receipt)
            self.assertFalse(fixture.output("timing").exists())

    def test_exact_amendment_sha_and_trigger_bytes_are_enforced(self) -> None:
        expected_sha = (
            "cb9dcd355648102cac35e06d0e2e6575f5ae6a60c9319690c2373efffc3ce7cb"
        )
        self.assertEqual(FREEZER.AMENDMENT_SHA256, expected_sha)
        repository = Path(__file__).resolve().parents[3]
        self.assertEqual(
            digest(repository / FREEZER.AMENDMENT_PATH), expected_sha
        )

        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            fixture.amendment.write_bytes(
                fixture.amendment.read_bytes() + b" "
            )
            fixture.commit("changed amendment bytes", FREEZER.AMENDMENT_PATH)
            with self.assertRaisesRegex(
                FREEZER.FreezeV2Error,
                "post-outcome amendment SHA-256 changed",
            ):
                fixture.freeze("fidelity")
            self.assertFalse(fixture.output("fidelity").exists())

        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            trigger = fixture.trigger_files["runs.jsonl"]
            trigger.write_bytes(b"forged historical runs\n")
            fixture.commit(
                "changed historical trigger",
                trigger.relative_to(fixture.repository),
            )
            with self.assertRaisesRegex(
                FREEZER.FreezeV2Error,
                "trigger runs.jsonl SHA-256 changed",
            ):
                fixture.freeze("fidelity")
            self.assertFalse(fixture.output("fidelity").exists())

    def test_bound_freezer_and_environment_inputs_reject_mutability(
        self,
    ) -> None:
        for attribute in (
            "plan_freezer",
            "materializer",
            "environment_generator",
            "environment_sidecar",
            "bank",
        ):
            with self.subTest(attribute=attribute):
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = V2Fixture(Path(temporary))
                    path = getattr(fixture, attribute)
                    path.write_bytes(path.read_bytes() + b"mutable\n")
                    with self.assertRaisesRegex(
                        FREEZER.FreezeV2Error, "mutable"
                    ):
                        fixture.freeze("fidelity")
                    self.assertFalse(
                        fixture.output("fidelity").exists()
                    )

    def test_dirty_materializer_import_code_never_runs_in_fresh_process(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            freezer_source = Path(FREEZER.__file__).read_bytes()
            freezer_source = freezer_source.replace(
                FREEZER.AMENDMENT_SHA256.encode("ascii"),
                fixture.amendment_sha256.encode("ascii"),
                1,
            )
            self.assertIn(
                fixture.amendment_sha256.encode("ascii"),
                freezer_source,
            )
            fixture.plan_freezer.write_bytes(freezer_source)
            fixture.plan_freezer.chmod(0o755)
            fixture.commit(
                "executable freezer under test",
                FREEZER.PLAN_FREEZER_PATH,
            )

            marker = fixture.repository.parent / "malicious-import-ran"
            fixture.materializer.write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text("
                "'executed\\n', encoding='utf-8')\n",
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-E",
                    "-s",
                    "-S",
                    "-B",
                    str(fixture.plan_freezer),
                    "--repository-root",
                    str(fixture.repository),
                    "--mode",
                    "fidelity",
                    "--output-root",
                    str(fixture.output("fidelity")),
                    "--runner",
                    str(fixture.runner),
                    "--analyzer",
                    str(fixture.analyzer),
                ],
                check=False,
                text=True,
                capture_output=True,
                env={
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
            self.assertEqual(completed.returncode, 1)
            self.assertIn("capacity materializer has mutable", completed.stderr)
            self.assertFalse(marker.exists())
            self.assertFalse(fixture.output("fidelity").exists())

    def test_committed_environment_dependency_drift_is_rejected(self) -> None:
        cases = (
            ("materializer", "capacity materializer SHA-256 changed"),
            ("environment_generator", "environment generator SHA-256 changed"),
            (
                "environment_sidecar",
                "sidecar is not the exact canonical binding",
            ),
            ("bank", "calibration bank bytes changed"),
        )
        for attribute, error in cases:
            with self.subTest(attribute=attribute):
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = V2Fixture(Path(temporary))
                    path = getattr(fixture, attribute)
                    path.write_bytes(path.read_bytes() + b"committed drift\n")
                    fixture.commit(
                        f"drift {attribute}",
                        path.relative_to(fixture.repository),
                    )
                    with self.assertRaisesRegex(
                        FREEZER.FreezeV2Error, error
                    ):
                        fixture.freeze("fidelity")
                    self.assertFalse(
                        fixture.output("fidelity").exists()
                    )

    def test_boolean_integer_and_float_type_confusion_fails_closed(
        self,
    ) -> None:
        cases = (
            ("serial_execution", 1),
            ("maximum_peak_rss_mib", 49152),
        )
        for field, replacement in cases:
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = V2Fixture(Path(temporary))
                    amendment = json.loads(
                        fixture.amendment.read_text(encoding="utf-8")
                    )
                    amendment["followup_fidelity_design"][field] = replacement
                    fixture.amendment.write_bytes(
                        FREEZER.canonical_json_bytes(amendment)
                    )
                    fixture.commit(
                        f"type confusion {field}",
                        FREEZER.AMENDMENT_PATH,
                    )
                    with self.assertRaisesRegex(
                        FREEZER.FreezeV2Error, field
                    ):
                        fixture.freeze(
                            "fidelity", pin_current_amendment=True
                        )
                    self.assertFalse(
                        fixture.output("fidelity").exists()
                    )

        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            manifest = json.loads(
                fixture.build_manifest.read_text(encoding="utf-8")
            )
            manifest["repository"]["remote_count"] = False
            fixture.build_manifest.write_bytes(
                FREEZER.canonical_json_bytes(manifest)
            )
            fixture.commit(
                "type confusion remote count",
                FREEZER.BUILD_MANIFEST_PATH,
            )
            with self.assertRaisesRegex(
                FREEZER.FreezeV2Error, "remote_count"
            ):
                fixture.freeze("fidelity")
            self.assertFalse(fixture.output("fidelity").exists())

    def test_output_path_rejects_dotdot_and_symlink_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            alias = (
                fixture.repository
                / "unused"
                / ".."
                / FREEZER.PLAN_ROOT
                / FREEZER.FIDELITY_OUTPUT_NAME
            )
            with self.assertRaisesRegex(
                FREEZER.FreezeV2Error,
                "absolute canonical path without",
            ):
                fixture.freeze("fidelity", output_root=alias)
            self.assertFalse(fixture.output("fidelity").exists())

        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            output = fixture.output("fidelity")
            output.parent.mkdir(parents=True)
            outside = fixture.repository.parent / "dangling-output-target"
            output.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(
                FREEZER.FreezeV2Error, "symbolic link"
            ):
                fixture.freeze("fidelity")
            self.assertTrue(output.is_symlink())
            self.assertFalse(outside.exists())

        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            outside = fixture.repository.parent / "outside-plans"
            outside.mkdir()
            plans = fixture.repository / "comparison" / "plans"
            plans.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(
                FREEZER.FreezeV2Error, "symbolic link"
            ):
                fixture.freeze("fidelity")
            self.assertEqual(list(outside.iterdir()), [])

    def test_descriptor_creation_rejects_component_swap_in_fresh_process(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repository = root / "repository"
            (repository / "comparison").mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            plans = repository / "comparison" / "plans"
            freezer_path = Path(FREEZER.__file__).resolve()
            program = f"""
import importlib.util
import sys
from pathlib import Path

freezer_path = Path({str(freezer_path)!r})
spec = importlib.util.spec_from_file_location(
    "descriptor_freezer_under_test", freezer_path
)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
repository = Path({str(repository)!r})
outside = Path({str(outside)!r})
plans = Path({str(plans)!r})
original = module._open_directory_at
swapped = False

def adversarial_open(parent_fd, name, label):
    global swapped
    if name == "plans" and plans.is_dir() and not plans.is_symlink():
        plans.rmdir()
        plans.symlink_to(outside, target_is_directory=True)
        swapped = True
    return original(parent_fd, name, label)

module._open_directory_at = adversarial_open
try:
    module._create_plan_directory_tree(
        repository,
        module.PLAN_ROOT / module.FIDELITY_OUTPUT_NAME,
    )
except module.FreezeV2Error as error:
    if not swapped:
        raise SystemExit("adversarial swap did not run")
    print(f"rejected: {{error}}")
else:
    raise SystemExit("component swap was accepted")
"""
            completed = subprocess.run(
                [
                    sys.executable,
                    "-E",
                    "-s",
                    "-S",
                    "-B",
                    "-c",
                    program,
                ],
                check=False,
                text=True,
                capture_output=True,
                env={
                    "LANG": "C",
                    "LC_ALL": "C",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stderr or completed.stdout,
            )
            self.assertIn("rejected:", completed.stdout)
            self.assertTrue(plans.is_symlink())
            self.assertEqual(list(outside.iterdir()), [])

    def test_publication_inode_check_rejects_output_swap(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            output = fixture.output("fidelity")
            displaced = output.with_name(output.name + "-displaced")
            outside = fixture.repository.parent / "publication-target"
            outside.mkdir()
            original_verify = FREEZER._verify_published_output
            swapped = False

            def adversarial_verify(
                handles: list[FREEZER._DirectoryHandle],
                files: list[tuple[int, str, tuple[int, int]]],
            ) -> None:
                nonlocal swapped
                output.rename(displaced)
                output.symlink_to(outside, target_is_directory=True)
                swapped = True
                original_verify(handles, files)

            with mock.patch.object(
                FREEZER,
                "_verify_published_output",
                side_effect=adversarial_verify,
            ):
                with self.assertRaisesRegex(
                    FREEZER.FreezeV2Error,
                    "directory identity changed before publication",
                ):
                    fixture.freeze("fidelity")
            self.assertTrue(swapped)
            self.assertTrue(output.is_symlink())
            self.assertEqual(list(outside.iterdir()), [])
            self.assertTrue(displaced.is_dir())
            self.assertEqual(list(displaced.iterdir()), [])

    def test_source_worktrees_must_share_git_identity_and_have_no_remotes(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            original_git = FREEZER._git

            def remote_in_source(
                repository: Path,
                *arguments: str,
                check: bool = True,
            ) -> subprocess.CompletedProcess[str]:
                if (
                    repository != fixture.repository
                    and arguments == ("remote",)
                ):
                    return subprocess.CompletedProcess(
                        ["git"], 0, "forged-origin\n", ""
                    )
                return original_git(
                    repository, *arguments, check=check
                )

            with mock.patch.object(
                FREEZER, "_git", side_effect=remote_in_source
            ):
                with self.assertRaisesRegex(
                    FREEZER.FreezeV2Error,
                    "source worktree must not have Git remotes",
                ):
                    fixture.freeze("fidelity")
            self.assertFalse(fixture.output("fidelity").exists())

        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            original_git = FREEZER._git

            def foreign_common_git(
                repository: Path,
                *arguments: str,
                check: bool = True,
            ) -> subprocess.CompletedProcess[str]:
                if (
                    repository != fixture.repository
                    and arguments
                    == ("rev-parse", "--git-common-dir")
                ):
                    return subprocess.CompletedProcess(
                        ["git"],
                        0,
                        str(fixture.repository / "foreign.git") + "\n",
                        "",
                    )
                return original_git(
                    repository, *arguments, check=check
                )

            with mock.patch.object(
                FREEZER, "_git", side_effect=foreign_common_git
            ):
                with self.assertRaisesRegex(
                    FREEZER.FreezeV2Error,
                    "does not share the local repository",
                ):
                    fixture.freeze("fidelity")
            self.assertFalse(fixture.output("fidelity").exists())

    def test_freeze_is_no_overwrite_and_cleans_partial_materialization(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            fixture.freeze("fidelity")
            plan_data = (
                fixture.output("fidelity") / "plan.json"
            ).read_bytes()
            with self.assertRaisesRegex(
                FileExistsError, "refusing to overwrite"
            ):
                fixture.freeze("fidelity")
            self.assertEqual(
                (fixture.output("fidelity") / "plan.json").read_bytes(),
                plan_data,
            )

        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))

            def partial_materializer(
                authenticated: object,
                environment_root: Path,
                family: str,
                split: str,
                count: int,
            ) -> bytes:
                fixture.assertIsAuthenticatedMaterializer(authenticated)
                fixture.assert_materializer_call(
                    environment_root, family, split, count
                )
                raise RuntimeError("injected materializer failure")

            with self.assertRaisesRegex(
                RuntimeError, "injected materializer failure"
            ):
                fixture.freeze(
                    "fidelity", materializer=partial_materializer
                )
            self.assertFalse(fixture.output("fidelity").exists())

    def test_receipt_rejects_forged_session_claims_and_ledgers(self) -> None:
        for mutation, error in (
            ("self_asserted_count", "valid_pair_count"),
            ("pooled_sessions", "exactly one immutable session"),
            ("wrong_session_path", "exact registered result root"),
        ):
            with self.subTest(mutation=mutation):
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = V2Fixture(Path(temporary))
                    receipt = fixture.create_receipt()
                    value = json.loads(
                        receipt.read_text(encoding="utf-8")
                    )
                    if mutation == "self_asserted_count":
                        value["valid_pair_count"] = 19
                    elif mutation == "pooled_sessions":
                        value["input_sessions"].append(
                            dict(value["input_sessions"][0])
                        )
                    else:
                        value["input_sessions"][0]["path"] = str(
                            fixture.result("fidelity").with_name(
                                "forged-session"
                            )
                        )
                    receipt.write_bytes(
                        FREEZER.canonical_json_bytes(value)
                    )
                    fixture.commit(
                        f"forged receipt {mutation}",
                        receipt.relative_to(fixture.repository),
                    )
                    with self.assertRaisesRegex(
                        FREEZER.FreezeV2Error, error
                    ):
                        fixture.freeze("timing", receipt=receipt)
                    self.assertFalse(
                        fixture.output("timing").exists()
                    )

        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            receipt = fixture.create_receipt()
            value = json.loads(receipt.read_text(encoding="utf-8"))
            receipt.write_text(
                json.dumps(value, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            fixture.commit(
                "noncanonical receipt",
                receipt.relative_to(fixture.repository),
            )
            with self.assertRaisesRegex(
                FREEZER.FreezeV2Error, "canonical JSON"
            ):
                fixture.freeze("timing", receipt=receipt)
            self.assertFalse(fixture.output("timing").exists())

        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            receipt = fixture.create_receipt()
            ledger = fixture.result("fidelity") / "pairs.jsonl"
            ledger.write_bytes(b"committed forged pair ledger\n")
            fixture.commit(
                "forged pair ledger",
                ledger.relative_to(fixture.repository),
            )
            with self.assertRaisesRegex(
                FREEZER.FreezeV2Error, "pairs.jsonl hash changed"
            ):
                fixture.freeze("timing", receipt=receipt)
            self.assertFalse(fixture.output("timing").exists())

    def test_receipt_must_match_fresh_exact_analyzer_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            marker_expression = (
                b'Path(__file__).with_name("freshness.marker").exists()'
            )
            analyzer_data = ANALYZER_STUB.replace(
                b'"gate_pass": True',
                b'"gate_pass": ' + marker_expression,
            )
            self.assertNotEqual(analyzer_data, ANALYZER_STUB)
            fixture.analyzer.write_bytes(analyzer_data)
            fixture.commit(
                "freshness-sensitive analyzer",
                FREEZER.ANALYZER_PATH,
            )
            marker = fixture.analyzer.with_name("freshness.marker")
            marker.write_text("initial generation only\n", encoding="utf-8")
            receipt = fixture.create_receipt()
            marker.unlink()

            with self.assertRaisesRegex(
                FREEZER.FreezeV2Error,
                "does not pass the fidelity gate",
            ):
                fixture.freeze("timing", receipt=receipt)
            self.assertFalse(fixture.output("timing").exists())

    def test_receipt_regeneration_ignores_concurrent_live_path_swaps(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V2Fixture(Path(temporary))
            receipt = fixture.create_receipt()
            live_pairs = fixture.result("fidelity") / "pairs.jsonl"
            analyzer_bytes = fixture.analyzer.read_bytes()
            pairs_bytes = live_pairs.read_bytes()
            marker = fixture.repository.parent / "live-analyzer-executed"
            snapshot_ready = threading.Event()
            paths_swapped = threading.Event()
            analysis_finished = threading.Event()
            attacker_errors: list[BaseException] = []
            original_execute = (
                FREEZER._execute_authenticated_analyzer_snapshot
            )

            def attacker() -> None:
                try:
                    if not snapshot_ready.wait(timeout=20):
                        raise AssertionError("snapshot execution never started")
                    fixture.analyzer.write_text(
                        "from pathlib import Path\n"
                        f"Path({str(marker)!r}).write_text("
                        "'executed\\n', encoding='utf-8')\n"
                        "raise SystemExit('malicious live analyzer')\n",
                        encoding="utf-8",
                    )
                    live_pairs.write_bytes(b"forged live pair ledger\n")
                    paths_swapped.set()
                    if not analysis_finished.wait(timeout=20):
                        raise AssertionError(
                            "snapshot analysis did not finish"
                        )
                except BaseException as error:
                    attacker_errors.append(error)
                finally:
                    fixture.analyzer.write_bytes(analyzer_bytes)
                    live_pairs.write_bytes(pairs_bytes)
                    paths_swapped.set()

            def execute_after_swap(**kwargs: object) -> bytes:
                snapshot_ready.set()
                if not paths_swapped.wait(timeout=20):
                    raise AssertionError("live paths were not swapped")
                try:
                    return original_execute(**kwargs)
                finally:
                    analysis_finished.set()

            thread = threading.Thread(target=attacker, daemon=True)
            thread.start()
            try:
                with mock.patch.object(
                    FREEZER,
                    "_execute_authenticated_analyzer_snapshot",
                    side_effect=execute_after_swap,
                ):
                    frozen = fixture.freeze("timing", receipt=receipt)
            finally:
                analysis_finished.set()
                snapshot_ready.set()
                thread.join(timeout=20)
            self.assertFalse(thread.is_alive())
            self.assertEqual(attacker_errors, [])
            self.assertFalse(marker.exists())
            self.assertEqual(fixture.analyzer.read_bytes(), analyzer_bytes)
            self.assertEqual(live_pairs.read_bytes(), pairs_bytes)
            self.assertTrue(frozen.plan_path.is_file())

    def test_real_registered_scenarios_materialize_exact_bytes(self) -> None:
        repository = Path(__file__).resolve().parents[3]
        environment_root = repository / "comparison" / "environments"
        expected = {
            192: (
                97_660,
                "add7ddc2b610b0dcd583f97854cf6c918e7640788b6489c3df114634adfd1d22",
            ),
            256: (
                128_223,
                "a0a68737ab6c52445ca0bfe5f64c328cee223752c90c8930d339201bbd165023",
            ),
        }
        self.assertEqual(
            digest(
                environment_root
                / "fractal_high"
                / "calibration"
                / "map.png"
            ),
            "529cdc9862a98beb3394c5ed5916ec6ccc982742f6aca1475cdc473deb169dd8",
        )
        environment_artifact = FREEZER._committed_artifact(
            repository,
            Path("comparison/environments/manifest.json"),
            "production environment manifest",
        )
        dependencies, authenticated = (
            FREEZER._verified_environment_dependencies(
                repository, environment_artifact
            )
        )
        self.assertEqual(
            dependencies["capacity_materializer"]["sha256"],
            "5e61a2e86c316369b1877c99ee370ff23b8beaa83e90cecfa0ff38c583b6d44b",
        )
        for count, (expected_bytes, expected_sha) in expected.items():
            data = FREEZER._materialize_authenticated_scenario(
                authenticated,
                environment_root,
                "fractal_high",
                "calibration",
                count,
            )
            self.assertEqual(len(data), expected_bytes)
            self.assertEqual(digest_bytes(data), expected_sha)


if __name__ == "__main__":
    unittest.main()
