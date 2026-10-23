from __future__ import annotations

import base64
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

from scripts.comparison import freeze_plan_v3 as FREEZER



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
import tempfile
from pathlib import Path

def canonical_json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\\n").encode()

def sha256_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def validate_session(session):
    with tempfile.TemporaryDirectory(
        prefix="fixture-analyzer-evidence-"
    ) as temporary:
        private = Path(temporary) / "000000.evidence"
        with private.open("xb") as handle:
            handle.write(b"fixture private evidence\\n")
        private.chmod(0o400)
        if private.read_bytes() != b"fixture private evidence\\n":
            raise RuntimeError("private evidence copy changed")
    session = session.resolve()
    fields = {
        "frozen_plan_json_sha256": "frozen_plan.json",
        "run_manifest_json_sha256": "run_manifest.json",
        "preflight_json_sha256": "preflight.json",
        "session_json_sha256": "session.json",
        "runs_jsonl_sha256": "runs.jsonl",
        "runs_csv_sha256": "runs.csv",
        "pairs_jsonl_sha256": "pairs.jsonl",
    }
    input_session = {"path": str(session)}
    for field, filename in fields.items():
        input_session[field] = sha256_file(session / filename)
    plan = json.loads((session / "frozen_plan.json").read_text())
    artifacts = plan["artifacts"]
    prior = {
        "analysis_json_sha256": artifacts["prior_failed_analysis"]["sha256"],
        "cohort_id": "fractal-high-lower-count-fidelity-amended-v1",
        "executed_method_attempt_slots": 0,
        "executed_pair_blocks": 0,
        "frozen_plan_json_sha256": artifacts["prior_failed_frozen_plan"]["sha256"],
        "gate_pass": False,
        "missing_method_attempt_slots": 40,
        "missing_pair_blocks": 20,
        "orchestration_abort_json_sha256": artifacts["prior_failed_orchestration_abort"]["sha256"],
        "pairs_jsonl_sha256": artifacts["prior_failed_pairs"]["sha256"],
        "path": artifacts["prior_failed_session"]["path"].rsplit("/", 1)[0],
        "planned_method_attempt_slots": 40,
        "planned_pair_blocks": 20,
        "preflight_json_sha256": artifacts["prior_failed_preflight"]["sha256"],
        "process_started": False,
        "run_manifest_json_sha256": artifacts["prior_failed_run_manifest"]["sha256"],
        "runs_csv_sha256": artifacts["prior_failed_runs_csv"]["sha256"],
        "runs_jsonl_sha256": artifacts["prior_failed_runs"]["sha256"],
        "session_json_sha256": artifacts["prior_failed_session"]["sha256"],
        "source_plan_json_sha256": artifacts["prior_failed_plan"]["sha256"],
        "valid_cooldown": True,
        "valid_pair_blocks": 0,
    }
    return {
        "input_session": input_session,
        "execution_epoch_id": plan["execution_epoch_id"],
        "cohort_id": plan["cohort_id"],
        "prior_failed_cohort": prior,
    }

def analyze_validated_sessions(sessions):
    return {
        "mode": "fidelity",
        "design_role": "outcome-informed-exploratory-fidelity",
        "execution_epoch_id": sessions[0]["execution_epoch_id"],
        "cohort_id": sessions[0]["cohort_id"],
        "prior_failed_cohort": sessions[0]["prior_failed_cohort"],
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

def fidelity_gate_receipt(
    *,
    analyzer_sha256,
    execution_epoch_id,
    cohort_id,
    prior_failed_cohort,
    input_sessions,
    gate,
):
    return {
        "schema_version": "dancers-amended-fidelity-gate-v2",
        "amendment_id": "fh-lower-count-fidelity-2026-07-28-v1",
        "execution_epoch_id": execution_epoch_id,
        "cohort_id": cohort_id,
        "design_role": "outcome-informed-exploratory-fidelity",
        "analyzer_sha256": analyzer_sha256,
        "input_sessions": list(input_sessions),
        "prior_failed_cohort": dict(prior_failed_cohort),
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
        execution_epoch_id=combined["execution_epoch_id"],
        cohort_id=combined["cohort_id"],
        prior_failed_cohort=combined["prior_failed_cohort"],
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


class V3Fixture:
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
            b"#!/usr/bin/env python3\n# test plan freezer v3\n",
            executable=True,
        )
        self.runner = self.write(
            FREEZER.RUNNER_PATH,
            b"#!/usr/bin/env python3\n# test runner v3\n",
            executable=True,
        )
        self.analyzer = self.write(
            FREEZER.ANALYZER_PATH,
            ANALYZER_STUB,
            executable=True,
        )
        self.runner_v2_base = self.write(
            FREEZER.RUNNER_V2_BASE_PATH,
            b"#!/usr/bin/env python3\n# sealed test runner v2 base\n",
        )
        self.analyzer_v2_base = self.write(
            FREEZER.ANALYZER_V2_BASE_PATH,
            b"#!/usr/bin/env python3\n# sealed test analyzer v2 base\n",
        )
        self.freezer_v2_base = self.write(
            FREEZER.FREEZER_V2_BASE_PATH,
            b"#!/usr/bin/env python3\n# sealed test freezer v2 base\n",
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
                "user.name=V3 Freezer Test",
                "-c",
                "user.email=v3-freezer@example.invalid",
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
        self._create_rerun_contract(builds)

    @staticmethod
    def _record(path: Path) -> dict[str, str]:
        return {
            "path": str(path.resolve()),
            "sha256": digest(path),
        }

    def _common_v2_artifacts(self) -> dict[str, dict[str, str]]:
        return {
            "amendment_protocol": self._record(
                self.amendment_protocol
            ),
            "analyzer_v1_base": self._record(self.analyzer_v1_base),
            "build_manifest": self._record(self.build_manifest),
            "capacity_materializer": self._record(self.materializer),
            "config_template": self._record(self.config),
            "environment_generator": self._record(
                self.environment_generator
            ),
            "environment_manifest": self._record(self.environment),
            "environment_manifest_sidecar": self._record(
                self.environment_sidecar
            ),
            "experiment_manifest": self._record(self.experiment),
            "fractal_high_calibration_bank": self._record(self.bank),
            "fractal_high_calibration_map": self._record(self.map_path),
            "freeze_capacity_decision": self._record(
                self.capacity_decision
            ),
            "pixi_lock": self._record(self.pixi_lock),
            "post_outcome_amendment": self._record(self.amendment),
            "protocol": self._record(self.base_protocol),
            "runner_v1_base": self._record(self.runner_v1_base),
            "trace_compare": self._record(self.trace_compare),
            "trigger_analysis": self._record(self.trigger_analysis),
            "trigger_frozen_plan": self._record(
                self.trigger_files["frozen_plan.json"]
            ),
            "trigger_pairs": self._record(
                self.trigger_files["pairs.jsonl"]
            ),
            "trigger_runs": self._record(
                self.trigger_files["runs.jsonl"]
            ),
            "trigger_session": self._record(
                self.trigger_files["session.json"]
            ),
        }

    def _prior_methods(
        self,
        builds: dict[str, dict],
    ) -> dict[str, dict]:
        methods: dict[str, dict] = {}
        manifest = json.loads(
            self.build_manifest.read_text(encoding="utf-8")
        )
        repository = manifest["repository"]
        for method in ("reference", "optimized"):
            build = builds[f"{method}_fidelity"]
            methods[method] = {
                "source_dir": str(
                    (self.repository / build["source_dir"]).resolve()
                ),
                "base_revision": repository[f"{method}_pin"],
                "head_revision": repository[
                    f"{method}_applied_head"
                ],
                "tree_hash": repository[f"{method}_tree"],
                "binary": {
                    "path": str(
                        (
                            self.repository / build["binary_path"]
                        ).resolve()
                    ),
                    "sha256": build["binary_sha256"],
                },
            }
        return methods

    def _create_rerun_contract(
        self,
        builds: dict[str, dict],
    ) -> None:
        prior_scenarios: dict[int, Path] = {}
        for count, data in self.scenario_bytes.items():
            prior_scenarios[count] = self.write(
                FREEZER.PRIOR_FAILED_PLAN_PATH.parent
                / "scenarios"
                / f"n-{count}.json",
                data,
            )

        methods = self._prior_methods(builds)
        common = self._common_v2_artifacts()
        prior_artifacts = {
            **common,
            "runner": self._record(self.runner_v2_base),
            "analysis_revision": self._record(self.analyzer_v2_base),
            "plan_freezer": self._record(self.freezer_v2_base),
        }
        pairs = [
            {
                "pair_id": (
                    f"fractal_high-calibration-seed-{seed}-n-{count}"
                ),
                "environment": "fractal_high",
                "seed": seed,
                "robot_count": count,
                "map": {
                    "path": str(self.map_path.resolve()),
                    "sha256": digest(self.map_path),
                    "bytes": len(self.map_bytes),
                },
                "scenario": {
                    "path": str(prior_scenarios[count].resolve()),
                    "sha256": digest(prior_scenarios[count]),
                    "bytes": len(self.scenario_bytes[count]),
                },
            }
            for count in (192, 256)
            for seed in range(1, 11)
        ]
        prior_plan_value = {
            "schema_version": "dancers-laptop-comparison-plan-v2",
            "amendment_id": FREEZER.AMENDMENT_ID,
            "cohort_id": (
                "fractal-high-lower-count-fidelity-amended-v1"
            ),
            "design_role": "outcome-informed-exploratory-fidelity",
            "phase": "calibration",
            "mode": "fidelity",
            "fixed_work_timesteps": 100,
            "timestep_seconds": 0.1,
            "idle_cooldown_seconds": 300,
            "timeout_seconds": 300.0,
            "termination_grace_seconds": 0.5,
            "minimum_free_disk_bytes": 21_474_836_480,
            "maximum_peak_rss_mib": 49_152.0,
            "power_mode": "AC Power",
            "power_policy": FREEZER.EXPECTED_POWER_POLICY,
            "process_control_policy": (
                FREEZER.EXPECTED_PROCESS_CONTROL_POLICY
            ),
            "thermal_policy": FREEZER.THERMAL_POLICY,
            "task_validity_predicate_id": (
                "fixed-horizon-safe-navigation-v1"
            ),
            "repetitions": 1,
            "warmups_per_binary_map": 0,
            "omp_threads": 1,
            "serial_execution": True,
            "order_seed": FREEZER.FIDELITY_ORDER_SEED,
            "runner_sha256": digest(self.runner_v2_base),
            "analyzer_sha256": digest(self.analyzer_v2_base),
            "trace_detail": "digest",
            "run_cwd": str(self.repository),
            "result_root": str(
                self.repository / FREEZER.PRIOR_FAILED_RESULT_ROOT
            ),
            "planned_method_attempt_slots": 40,
            "planned_pair_blocks": 20,
            "artifacts": prior_artifacts,
            "methods": methods,
            "pairs": pairs,
        }
        self.prior_plan = self.write_json(
            FREEZER.PRIOR_FAILED_PLAN_PATH,
            prior_plan_value,
        )

        self.prior_frozen_plan = self.write(
            FREEZER.PRIOR_FAILED_RESULT_ROOT / "frozen_plan.json",
            self.prior_plan.read_bytes(),
        )
        self.prior_run_manifest = self.write_json(
            FREEZER.PRIOR_FAILED_RESULT_ROOT / "run_manifest.json",
            {"fixture": "sealed-zero-run-manifest"},
        )
        self.prior_preflight = self.write_json(
            FREEZER.PRIOR_FAILED_RESULT_ROOT / "preflight.json",
            {"fixture": "sealed-valid-preflight"},
        )
        self.prior_session = self.write_json(
            FREEZER.PRIOR_FAILED_RESULT_ROOT / "session.json",
            {
                "schema_version": (
                    "dancers-laptop-comparison-session-v2"
                ),
                "cohort_id": (
                    "fractal-high-lower-count-fidelity-amended-v1"
                ),
                "result_root": str(
                    self.repository / FREEZER.PRIOR_FAILED_RESULT_ROOT
                ),
                "attempt_count": 0,
                "pair_count": 0,
                "planned_method_attempt_slots": 40,
                "planned_pair_blocks": 20,
                "executed_method_attempt_slots": 0,
                "completed_method_attempt_slots": 0,
                "valid_method_attempt_slots": 0,
                "executed_pair_blocks": 0,
                "valid_pair_blocks": 0,
                "all_attempts_completed": False,
                "all_pairs_valid": False,
                "stopped_early": True,
                "session_power_valid": False,
                "idle_cooldown": {
                    "performed": True,
                    "eligible": True,
                    "valid": True,
                    "required_seconds": 300,
                },
            },
        )
        self.prior_runs = self.write(
            FREEZER.PRIOR_FAILED_RESULT_ROOT / "runs.jsonl",
            b"",
        )
        self.prior_runs_csv = self.write(
            FREEZER.PRIOR_FAILED_RESULT_ROOT / "runs.csv",
            b"fixture_header\n",
        )
        self.prior_pairs = self.write(
            FREEZER.PRIOR_FAILED_RESULT_ROOT / "pairs.jsonl",
            b"",
        )
        self.prior_abort = self.write_json(
            FREEZER.PRIOR_FAILED_RESULT_ROOT
            / "orchestration_abort.json",
            {
                "schema_version": "dancers-orchestration-abort-v2",
                "stage": "pre-first-attempt",
                "process_started": False,
                "error": (
                    "post-containment orchestration failure: RunnerError: "
                    "registered result_root mkdir flags changed"
                ),
            },
        )
        self.prior_analysis = self.write_json(
            FREEZER.PRIOR_FAILED_ANALYSIS_PATH,
            {
                "fidelity_gate": {
                    "planned_method_attempt_slots": 40,
                    "executed_attempt_count": 0,
                    "planned_pair_blocks": 20,
                    "completed_attempt_count": 0,
                    "valid_pair_count": 0,
                    "host_valid_pair_count": 0,
                    "gate_pass": False,
                }
            },
        )
        self.rerun_addendum = self.write(
            FREEZER.RERUN_ADDENDUM_PATH,
            b"# Fixture rerun-1 addendum\n",
        )

        production_root = Path(FREEZER.__file__).resolve().parents[2]
        registry = json.loads(
            (
                production_root / FREEZER.RERUN_REGISTRY_PATH
            ).read_text(encoding="utf-8")
        )
        registry["addendum_protocol"] = {
            "path": FREEZER.RERUN_ADDENDUM_PATH.as_posix(),
            "sha256": digest(self.rerun_addendum),
        }
        registered = registry["prior_failed_cohort"]["artifacts"]
        registered.update(
            {
                "analysis": {
                    "path": FREEZER.PRIOR_FAILED_ANALYSIS_PATH.as_posix(),
                    "sha256": digest(self.prior_analysis),
                },
                "analyzer_v2_base": {
                    "path": FREEZER.ANALYZER_V2_BASE_PATH.as_posix(),
                    "sha256": digest(self.analyzer_v2_base),
                },
                "freezer_v2_base": {
                    "path": FREEZER.FREEZER_V2_BASE_PATH.as_posix(),
                    "sha256": digest(self.freezer_v2_base),
                },
                "frozen_plan": {
                    "path": (
                        FREEZER.PRIOR_FAILED_RESULT_ROOT
                        / "frozen_plan.json"
                    ).as_posix(),
                    "sha256": digest(self.prior_frozen_plan),
                },
                "orchestration_abort": {
                    "path": (
                        FREEZER.PRIOR_FAILED_RESULT_ROOT
                        / "orchestration_abort.json"
                    ).as_posix(),
                    "sha256": digest(self.prior_abort),
                },
                "pairs": {
                    "path": (
                        FREEZER.PRIOR_FAILED_RESULT_ROOT / "pairs.jsonl"
                    ).as_posix(),
                    "sha256": digest(self.prior_pairs),
                },
                "parent_amendment_protocol": {
                    "path": (
                        self.amendment_protocol.relative_to(
                            self.repository
                        ).as_posix()
                    ),
                    "sha256": digest(self.amendment_protocol),
                },
                "parent_amendment_registry": {
                    "path": FREEZER.AMENDMENT_PATH.as_posix(),
                    "sha256": digest(self.amendment),
                },
                "preflight": {
                    "path": (
                        FREEZER.PRIOR_FAILED_RESULT_ROOT
                        / "preflight.json"
                    ).as_posix(),
                    "sha256": digest(self.prior_preflight),
                },
                "run_manifest": {
                    "path": (
                        FREEZER.PRIOR_FAILED_RESULT_ROOT
                        / "run_manifest.json"
                    ).as_posix(),
                    "sha256": digest(self.prior_run_manifest),
                },
                "runner_v2_base": {
                    "path": FREEZER.RUNNER_V2_BASE_PATH.as_posix(),
                    "sha256": digest(self.runner_v2_base),
                },
                "runs": {
                    "path": (
                        FREEZER.PRIOR_FAILED_RESULT_ROOT / "runs.jsonl"
                    ).as_posix(),
                    "sha256": digest(self.prior_runs),
                },
                "runs_csv": {
                    "path": (
                        FREEZER.PRIOR_FAILED_RESULT_ROOT / "runs.csv"
                    ).as_posix(),
                    "sha256": digest(self.prior_runs_csv),
                },
                "scenario_192": {
                    "path": prior_scenarios[192]
                    .relative_to(self.repository)
                    .as_posix(),
                    "sha256": digest(prior_scenarios[192]),
                },
                "scenario_256": {
                    "path": prior_scenarios[256]
                    .relative_to(self.repository)
                    .as_posix(),
                    "sha256": digest(prior_scenarios[256]),
                },
                "session": {
                    "path": (
                        FREEZER.PRIOR_FAILED_RESULT_ROOT / "session.json"
                    ).as_posix(),
                    "sha256": digest(self.prior_session),
                },
                "source_plan": {
                    "path": FREEZER.PRIOR_FAILED_PLAN_PATH.as_posix(),
                    "sha256": digest(self.prior_plan),
                },
            }
        )
        self.rerun_registry = self.write_json(
            FREEZER.RERUN_REGISTRY_PATH,
            registry,
        )
        self.commit("fixture sealed v2 incident and rerun registry")
        self.rerun_addendum_sha256 = digest(self.rerun_addendum)
        self.rerun_registry_sha256 = digest(self.rerun_registry)

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
        return self.write(relative, FREEZER.canonical_json_bytes(value))

    def commit(self, message: str, *relative_paths: Path) -> None:
        selected = list(relative_paths) if relative_paths else self.tracked
        git(self.repository, "add", "--", *(str(item) for item in selected))
        git(
            self.repository,
            "-c",
            "user.name=V3 Freezer Test",
            "-c",
            "user.email=v3-freezer@example.invalid",
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
    ) -> FREEZER.FrozenPlanV3:
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
        ), mock.patch.object(
            FREEZER,
            "RERUN_ADDENDUM_SHA256",
            self.rerun_addendum_sha256,
        ), mock.patch.object(
            FREEZER,
            "RERUN_REGISTRY_SHA256",
            self.rerun_registry_sha256,
        ):
            return FREEZER.freeze_plan_v3(
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
            "runs.csv",
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
            / FREEZER.FIDELITY_RECEIPT_NAME
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


class FreezePlanV3Tests(unittest.TestCase):
    def test_real_analyzer_evidence_guard_uses_snapshot_private_temp(
        self,
    ) -> None:
        repository = Path(__file__).resolve().parents[3]
        sources = {
            "analyzer": FREEZER.ANALYZER_PATH,
            "analyzer_v2": FREEZER.ANALYZER_V2_BASE_PATH,
            "analyzer_v1": FREEZER.ANALYZER_V1_BASE_PATH,
            "runner_v1": FREEZER.RUNNER_V1_BASE_PATH,
            "trace": FREEZER.TRACE_COMPARE_PATH,
            "capacity": FREEZER.CAPACITY_DECISION_PATH,
        }
        with tempfile.TemporaryDirectory() as temporary:
            snapshot_root = Path(temporary).resolve()
            logical_probe = (
                repository / "bootstrap-evidence-probe.bin"
            )
            physical_probe = snapshot_root.joinpath(
                *logical_probe.parts[1:]
            )
            physical_probe.parent.mkdir(parents=True)
            probe_data = b"real analyzer evidence guard probe\n"
            physical_probe.write_bytes(probe_data)
            payload = FREEZER.canonical_json_bytes(
                {
                    "logical_cwd": str(repository),
                    "snapshot_root": str(snapshot_root),
                    "evidence_guard_probe": True,
                    "evidence_guard_probe_path": str(logical_probe),
                    "sources": {
                        name: {
                            "base64": base64.b64encode(
                                (repository / relative).read_bytes()
                            ).decode("ascii"),
                            "logical_path": str(repository / relative),
                            "sha256": digest(repository / relative),
                        }
                        for name, relative in sources.items()
                    },
                }
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    "-c",
                    FREEZER.AUTHENTICATED_ANALYZER_BOOTSTRAP,
                ],
                input=payload,
                check=False,
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
                completed.stderr.decode("utf-8", errors="replace"),
            )
            result = json.loads(completed.stdout.decode("utf-8"))
            self.assertEqual(
                result,
                {
                    "private_mode": 0o400,
                    "sha256": digest_bytes(probe_data),
                    "source_path": str(logical_probe),
                },
            )

    def test_fidelity_plan_binds_amendment_tools_and_denominators(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
            frozen = fixture.freeze("fidelity")
            plan = json.loads(
                frozen.plan_path.read_text(encoding="utf-8")
            )

            self.assertEqual(plan["schema_version"], FREEZER.PLAN_SCHEMA_V3)
            self.assertNotIn("capacity_search", plan)
            self.assertNotIn("capacity_evaluation", plan)
            self.assertEqual(plan["amendment_id"], FREEZER.AMENDMENT_ID)
            self.assertEqual(
                plan["execution_epoch_id"],
                FREEZER.EXECUTION_EPOCH_ID,
            )
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
            self.assertEqual(
                plan["order_seed"], FREEZER.FIDELITY_ORDER_SEED
            )
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
                set(
                    FREEZER.MANDATORY_ARTIFACT_KEYS
                    | FREEZER.COMMON_V2_ARTIFACT_KEYS
                ),
            )
            self.assertEqual(len(plan["artifacts"]), 40)
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
            prior = json.loads(
                fixture.prior_plan.read_text(encoding="utf-8")
            )
            self.assertEqual(
                [
                    FREEZER._scientific_pair(
                        pair, f"new pair {index}"
                    )
                    for index, pair in enumerate(plan["pairs"])
                ],
                [
                    FREEZER._scientific_pair(
                        pair, f"prior pair {index}"
                    )
                    for index, pair in enumerate(prior["pairs"])
                ],
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
            fixture = V3Fixture(Path(temporary))
            with self.assertRaisesRegex(
                FREEZER.FreezeV3Error, "requires a passed"
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
                plan["execution_epoch_id"],
                FREEZER.EXECUTION_EPOCH_ID,
            )
            self.assertGreater(plan["order_seed"], 0)
            self.assertEqual(
                plan["order_seed"],
                FREEZER._order_seed(
                    mode="timing",
                    counts=[192, 256],
                    seeds=list(range(1, 11)),
                    artifacts=plan["artifacts"],
                    power_policy=FREEZER.EXPECTED_POWER_POLICY,
                    process_policy=(
                        FREEZER.EXPECTED_PROCESS_CONTROL_POLICY
                    ),
                ),
            )
            self.assertEqual(
                set(plan["artifacts"]),
                set(
                    FREEZER.MANDATORY_ARTIFACT_KEYS
                    | FREEZER.COMMON_V2_ARTIFACT_KEYS
                    | FREEZER.TIMING_ONLY_ARTIFACT_KEYS
                ),
            )
            self.assertEqual(len(plan["artifacts"]), 48)
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
                ("fidelity_runs_csv", "runs.csv"),
                ("fidelity_pairs", "pairs.jsonl"),
            ):
                artifact = plan["artifacts"][artifact_name]
                ledger = fixture.result("fidelity") / filename
                self.assertEqual(artifact["path"], str(ledger))
                self.assertEqual(artifact["sha256"], digest(ledger))

    def test_mutable_tool_and_wrong_output_fail_before_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
            fixture.runner.write_text(
                "#!/usr/bin/env python3\n# changed\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                FREEZER.FreezeV3Error, "mutable"
            ):
                fixture.freeze("fidelity")
            self.assertFalse(fixture.output("fidelity").exists())

        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
            wrong = fixture.output("fidelity").with_name("ambiguous")
            with self.assertRaisesRegex(
                FREEZER.FreezeV3Error,
                "output_root must be the registered path",
            ):
                fixture.freeze("fidelity", output_root=wrong)
            self.assertFalse(wrong.exists())

    def test_symlinked_tool_is_rejected_even_when_git_committed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
            fixture.analyzer.unlink()
            fixture.analyzer.symlink_to(
                fixture.runner.relative_to(fixture.analyzer.parent)
            )
            fixture.commit(
                "symlink analyzer",
                FREEZER.ANALYZER_PATH,
            )
            with self.assertRaisesRegex(
                FREEZER.FreezeV3Error, "symbolic link"
            ):
                fixture.freeze("fidelity")
            self.assertFalse(fixture.output("fidelity").exists())

    def test_mutable_transitive_tool_dependency_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
            fixture.trace_compare.write_text(
                "#!/usr/bin/env python3\n# mutated trace reader\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                FREEZER.FreezeV3Error, "mutable"
            ):
                fixture.freeze("fidelity")
            self.assertFalse(fixture.output("fidelity").exists())

    def test_policy_or_denominator_drift_fails_closed(self) -> None:
        for field, value, error in (
            ("power_policy", {}, "power policy"),
            (
                "planned_pair_blocks",
                19,
                "parent_amendment_registry",
            ),
        ):
            with self.subTest(field=field):
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = V3Fixture(Path(temporary))
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
                        FREEZER.FreezeV3Error, error
                    ):
                        fixture.freeze(
                            "fidelity", pin_current_amendment=True
                        )
                    self.assertFalse(
                        fixture.output("fidelity").exists()
                    )

    def test_nonpassing_or_mutable_timing_receipt_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
            receipt = fixture.create_receipt()
            raw = json.loads(receipt.read_text(encoding="utf-8"))
            raw["gate_pass"] = False
            receipt.write_text(
                FREEZER.canonical_json_bytes(raw).decode("utf-8"),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                FREEZER.FreezeV3Error, "mutable"
            ):
                fixture.freeze("timing", receipt=receipt)
            self.assertFalse(fixture.output("timing").exists())

            fixture.commit(
                "record failed receipt",
                receipt.relative_to(fixture.repository),
            )
            with self.assertRaisesRegex(
                FREEZER.FreezeV3Error, "gate_pass"
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
        self.assertEqual(
            digest(repository / FREEZER.RERUN_ADDENDUM_PATH),
            FREEZER.RERUN_ADDENDUM_SHA256,
        )
        self.assertEqual(
            digest(repository / FREEZER.RERUN_REGISTRY_PATH),
            FREEZER.RERUN_REGISTRY_SHA256,
        )

        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
            fixture.amendment.write_bytes(
                fixture.amendment.read_bytes() + b" "
            )
            fixture.commit("changed amendment bytes", FREEZER.AMENDMENT_PATH)
            with self.assertRaisesRegex(
                FREEZER.FreezeV3Error,
                "post-outcome amendment SHA-256 changed",
            ):
                fixture.freeze("fidelity")
            self.assertFalse(fixture.output("fidelity").exists())

        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
            trigger = fixture.trigger_files["runs.jsonl"]
            trigger.write_bytes(b"forged historical runs\n")
            fixture.commit(
                "changed historical trigger",
                trigger.relative_to(fixture.repository),
            )
            with self.assertRaisesRegex(
                FREEZER.FreezeV3Error,
                "trigger runs.jsonl SHA-256 changed",
            ):
                fixture.freeze("fidelity")
            self.assertFalse(fixture.output("fidelity").exists())

    def test_rerun_registry_and_prior_failure_tampering_fails_closed(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
            registry = json.loads(
                fixture.rerun_registry.read_text(encoding="utf-8")
            )
            registry["prior_failed_cohort"][
                "missing_method_attempt_slots"
            ] = 39
            fixture.rerun_registry.write_bytes(
                FREEZER.canonical_json_bytes(registry)
            )
            fixture.commit(
                "tamper registered prior denominator",
                FREEZER.RERUN_REGISTRY_PATH,
            )
            fixture.rerun_registry_sha256 = digest(
                fixture.rerun_registry
            )
            with self.assertRaisesRegex(
                FREEZER.FreezeV3Error,
                "missing_method_attempt_slots",
            ):
                fixture.freeze("fidelity")
            self.assertFalse(fixture.output("fidelity").exists())

        for attribute, error in (
            ("prior_abort", "orchestration_abort"),
            ("runner_v2_base", "runner_v2_base"),
        ):
            with self.subTest(attribute=attribute):
                with tempfile.TemporaryDirectory() as temporary:
                    fixture = V3Fixture(Path(temporary))
                    artifact = getattr(fixture, attribute)
                    artifact.write_bytes(
                        artifact.read_bytes() + b"committed tamper\n"
                    )
                    fixture.commit(
                        f"tamper {attribute}",
                        artifact.relative_to(fixture.repository),
                    )
                    with self.assertRaisesRegex(
                        FREEZER.FreezeV3Error,
                        error,
                    ):
                        fixture.freeze("fidelity")
                    self.assertFalse(
                        fixture.output("fidelity").exists()
                    )

    def test_registered_result_and_receipt_destinations_must_be_absent(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
            fixture.result("fidelity").mkdir(parents=True)
            with self.assertRaisesRegex(
                FileExistsError,
                "existing result root",
            ):
                fixture.freeze("fidelity")
            self.assertFalse(fixture.output("fidelity").exists())

        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
            receipt = (
                fixture.repository
                / FREEZER.RESULT_ROOT
                / FREEZER.FIDELITY_RECEIPT_NAME
            )
            receipt.parent.mkdir(parents=True, exist_ok=True)
            receipt.write_text("stale\n", encoding="utf-8")
            with self.assertRaisesRegex(
                FileExistsError,
                "existing fidelity gate receipt",
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
                    fixture = V3Fixture(Path(temporary))
                    path = getattr(fixture, attribute)
                    path.write_bytes(path.read_bytes() + b"mutable\n")
                    with self.assertRaisesRegex(
                        FREEZER.FreezeV3Error, "mutable"
                    ):
                        fixture.freeze("fidelity")
                    self.assertFalse(
                        fixture.output("fidelity").exists()
                    )

    def test_dirty_materializer_import_code_never_runs_in_fresh_process(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
            freezer_source = Path(FREEZER.__file__).read_bytes()
            freezer_source = freezer_source.replace(
                FREEZER.AMENDMENT_SHA256.encode("ascii"),
                fixture.amendment_sha256.encode("ascii"),
                1,
            )
            freezer_source = freezer_source.replace(
                FREEZER.RERUN_ADDENDUM_SHA256.encode("ascii"),
                fixture.rerun_addendum_sha256.encode("ascii"),
                1,
            )
            freezer_source = freezer_source.replace(
                FREEZER.RERUN_REGISTRY_SHA256.encode("ascii"),
                fixture.rerun_registry_sha256.encode("ascii"),
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
                    fixture = V3Fixture(Path(temporary))
                    path = getattr(fixture, attribute)
                    path.write_bytes(path.read_bytes() + b"committed drift\n")
                    fixture.commit(
                        f"drift {attribute}",
                        path.relative_to(fixture.repository),
                    )
                    with self.assertRaisesRegex(
                        FREEZER.FreezeV3Error, error
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
                    fixture = V3Fixture(Path(temporary))
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
                        FREEZER.FreezeV3Error,
                        "parent_amendment_registry",
                    ):
                        fixture.freeze(
                            "fidelity", pin_current_amendment=True
                        )
                    self.assertFalse(
                        fixture.output("fidelity").exists()
                    )

        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
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
                FREEZER.FreezeV3Error, "remote_count"
            ):
                fixture.freeze("fidelity")
            self.assertFalse(fixture.output("fidelity").exists())

    def test_output_path_rejects_dotdot_and_symlink_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
            alias = (
                fixture.repository
                / "unused"
                / ".."
                / FREEZER.PLAN_ROOT
                / FREEZER.FIDELITY_OUTPUT_NAME
            )
            with self.assertRaisesRegex(
                FREEZER.FreezeV3Error,
                "absolute canonical path without",
            ):
                fixture.freeze("fidelity", output_root=alias)
            self.assertFalse(fixture.output("fidelity").exists())

        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
            output = fixture.output("fidelity")
            output.parent.mkdir(parents=True, exist_ok=True)
            outside = fixture.repository.parent / "dangling-output-target"
            output.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(
                FREEZER.FreezeV3Error, "symbolic link"
            ):
                fixture.freeze("fidelity")
            self.assertTrue(output.is_symlink())
            self.assertFalse(outside.exists())

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
except module.FreezeV3Error as error:
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
            fixture = V3Fixture(Path(temporary))
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
                    FREEZER.FreezeV3Error,
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
            fixture = V3Fixture(Path(temporary))
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
                    FREEZER.FreezeV3Error,
                    "source worktree must not have Git remotes",
                ):
                    fixture.freeze("fidelity")
            self.assertFalse(fixture.output("fidelity").exists())

        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
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
                    FREEZER.FreezeV3Error,
                    "does not share the local repository",
                ):
                    fixture.freeze("fidelity")
            self.assertFalse(fixture.output("fidelity").exists())

    def test_freeze_is_no_overwrite_and_cleans_partial_materialization(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
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
            fixture = V3Fixture(Path(temporary))

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
                    fixture = V3Fixture(Path(temporary))
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
                        FREEZER.FreezeV3Error, error
                    ):
                        fixture.freeze("timing", receipt=receipt)
                    self.assertFalse(
                        fixture.output("timing").exists()
                    )

        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
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
                FREEZER.FreezeV3Error, "canonical JSON"
            ):
                fixture.freeze("timing", receipt=receipt)
            self.assertFalse(fixture.output("timing").exists())

        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
            receipt = fixture.create_receipt()
            ledger = fixture.result("fidelity") / "pairs.jsonl"
            ledger.write_bytes(b"committed forged pair ledger\n")
            fixture.commit(
                "forged pair ledger",
                ledger.relative_to(fixture.repository),
            )
            with self.assertRaisesRegex(
                FREEZER.FreezeV3Error, "pairs.jsonl hash changed"
            ):
                fixture.freeze("timing", receipt=receipt)
            self.assertFalse(fixture.output("timing").exists())

    def test_receipt_must_match_fresh_exact_analyzer_output(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
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
                FREEZER.FreezeV3Error,
                "does not pass the fidelity gate",
            ):
                fixture.freeze("timing", receipt=receipt)
            self.assertFalse(fixture.output("timing").exists())

    def test_receipt_regeneration_ignores_concurrent_live_path_swaps(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = V3Fixture(Path(temporary))
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
