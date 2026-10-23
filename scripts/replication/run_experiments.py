#!/usr/bin/env python3
"""Run and aggregate the DANCeRS experiments reported in arXiv:2508.18153v1."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT = ROOT / "replication/metrics/paper_contract.json"
CONTINUOUS_TEMPLATE = ROOT / "replication/configs/paper_continuous.yaml.in"
DISCRETE_TEMPLATE = ROOT / "replication/configs/paper_discrete.yaml.in"
DEFAULT_BINARY = ROOT / "build/pixi/dancers"

REPORTED_SOURCE_COMMIT = "c5a9767583b7bb53c29bc833104b3ee1fd2b9b74"

FINAL_ITER_RE = re.compile(r"Final iterations:\s*\[(\d+)\]")
ROBOT_COUNT_RE = re.compile(r"Final robot count:\s*\[(\d+)\]")
CONNECTIVITY_RE = re.compile(r"Mean connectivity:\s*\[([-+0-9.eE]+)")
HEADING_RE = re.compile(r"error_heading:\s*\[([-+0-9.eE]+)")
POSITION_RE = re.compile(r"error_pos:\s*\[([-+0-9.eE]+)")
DECISION_RE = re.compile(r"Final decision:\s*\[(-?\d+)\]")
CONSENSUS_RE = re.compile(r"(?m)^Consensus reached\.")


@dataclass(frozen=True)
class RunSpec:
    experiment: str
    trial: int
    seed: int
    parameters: dict[str, Any]
    template: str

    @property
    def run_id(self) -> str:
        pieces = [self.experiment]
        abbreviations = {
            "num_robots": "n",
            "communication_radius_m": "rc",
            "window_timesteps": "w",
            "num_decisions": "nd",
            "consensus_sigma": "sigma",
            "seed_proportion": "zeta",
        }
        for key in sorted(self.parameters):
            if key in {"max_iterations", "seed_prior_sigma"}:
                continue
            pieces.append(f"{abbreviations.get(key, key)}-{slug_value(self.parameters[key])}")
        pieces.append(f"seed-{self.seed:03d}")
        return "__".join(pieces)


def slug_value(value: Any) -> str:
    if isinstance(value, float):
        text = format(value, ".12g")
    else:
        text = str(value)
    return text.replace("-", "m").replace(".", "p").replace("+", "")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_contract(path: Path = DEFAULT_CONTRACT) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _continuous_specs(contract: dict[str, Any]) -> list[tuple[str, dict[str, Any], str]]:
    exp = contract["experiments"]["continuous_convergence"]
    return [
        (
            "continuous_convergence",
            {
                "num_robots": n,
                "communication_radius_m": rc,
                "window_timesteps": 3,
                "max_iterations": 5000,
            },
            "continuous",
        )
        for n in exp["robot_counts"]
        for rc in exp["communication_radii_m"]
    ]


def _sliding_specs(contract: dict[str, Any]) -> list[tuple[str, dict[str, Any], str]]:
    exp = contract["experiments"]["sliding_window"]
    return [
        (
            "sliding_window",
            {
                "num_robots": exp["robot_count"],
                "communication_radius_m": rc,
                "window_timesteps": window,
                "max_iterations": 5000,
            },
            "continuous",
        )
        for rc in exp["communication_radii_m"]
        for window in exp["window_timesteps"]
    ]


def _discrete_specs(contract: dict[str, Any]) -> list[tuple[str, dict[str, Any], str]]:
    exp = contract["experiments"]["discrete_convergence"]
    sigma = 0.5 / exp["num_decisions"]
    return [
        (
            "discrete_convergence",
            {
                "num_robots": n,
                "communication_radius_m": rc,
                "num_decisions": exp["num_decisions"],
                "consensus_sigma": sigma,
                "seed_proportion": 0.0,
                "seed_decision": 0,
                "seed_prior_sigma": 1e-8,
                "max_iterations": exp["max_iterations"],
            },
            "discrete",
        )
        for rc in exp["communication_radii_m"]
        for n in exp["robot_counts"]
    ]


def _sigma_specs(contract: dict[str, Any]) -> list[tuple[str, dict[str, Any], str]]:
    exp = contract["experiments"]["sigma_sweep"]
    assumptions = exp["replication_assumptions"]
    return [
        (
            "sigma_sweep",
            {
                "num_robots": assumptions["robot_count"],
                "communication_radius_m": assumptions["communication_radius_m"],
                "num_decisions": nd,
                "consensus_sigma": sigma,
                "seed_proportion": 0.0,
                "seed_decision": 0,
                "seed_prior_sigma": 1e-8,
                "max_iterations": exp["max_iterations"],
            },
            "discrete",
        )
        for nd in exp["num_decisions"]
        for sigma in exp["consensus_sigmas"]
    ]


def _informed_specs(contract: dict[str, Any]) -> list[tuple[str, dict[str, Any], str]]:
    exp = contract["experiments"]["informed_robots"]
    return [
        (
            "informed_robots",
            {
                "num_robots": exp["robot_count"],
                "communication_radius_m": exp["communication_radius_m"],
                "num_decisions": exp["num_decisions"],
                "consensus_sigma": 0.5 / exp["num_decisions"],
                "seed_proportion": zeta,
                "seed_decision": exp["seed_decision"],
                "seed_prior_sigma": exp["seed_prior_sigma"],
                "max_iterations": exp["max_iterations"],
            },
            "discrete",
        )
        for zeta in exp["seed_proportions"]
    ]


def parameter_sets(contract: dict[str, Any], suite: str) -> list[tuple[str, dict[str, Any], str]]:
    by_suite = {
        "continuous": _continuous_specs(contract),
        "sliding": _sliding_specs(contract),
        "discrete": _discrete_specs(contract),
        "sigma": _sigma_specs(contract),
        "informed": _informed_specs(contract),
    }
    if suite == "all":
        return [item for name in ("continuous", "sliding", "discrete", "sigma", "informed") for item in by_suite[name]]
    if suite == "quick":
        # One paper-parameterised point from each quantitative experiment. The
        # CLI defaults quick runs to one trial; this is a plumbing check, not a
        # statistical paper comparison.
        return [
            next(item for item in by_suite["continuous"] if item[1]["num_robots"] == 20 and item[1]["communication_radius_m"] == 100),
            next(item for item in by_suite["sliding"] if item[1]["communication_radius_m"] == 50 and item[1]["window_timesteps"] == 8),
            next(item for item in by_suite["discrete"] if item[1]["num_robots"] == 20 and item[1]["communication_radius_m"] == 80),
            next(item for item in by_suite["sigma"] if item[1]["num_decisions"] == 10 and item[1]["consensus_sigma"] == 0.032),
            next(item for item in by_suite["informed"] if item[1]["seed_proportion"] == 0.2),
        ]
    return by_suite[suite]


def build_plan(contract: dict[str, Any], suite: str, trials: int) -> list[RunSpec]:
    if trials < 1:
        raise ValueError("trials must be positive")
    plan: list[RunSpec] = []
    for experiment, params, template in parameter_sets(contract, suite):
        for trial in range(1, trials + 1):
            plan.append(RunSpec(experiment, trial, trial, params, template))
    return plan


def render_config(spec: RunSpec) -> str:
    if spec.template == "continuous":
        context = {
            "SIMULATION_NAME": spec.run_id,
            "NUM_ROBOTS": spec.parameters["num_robots"],
            "COMMUNICATION_RADIUS": spec.parameters["communication_radius_m"],
            "RNG_SEED": spec.seed,
            "MAX_TIME": spec.parameters["max_iterations"],
            "WINDOW_TIMESTEPS": spec.parameters["window_timesteps"],
        }
        source = CONTINUOUS_TEMPLATE
    elif spec.template == "discrete":
        context = {
            "SIMULATION_NAME": spec.run_id,
            "NUM_ROBOTS": spec.parameters["num_robots"],
            "COMMUNICATION_RADIUS": spec.parameters["communication_radius_m"],
            "RNG_SEED": spec.seed,
            "SIGMA_POSE_FIXED": format(spec.parameters["seed_prior_sigma"], ".12g"),
            "NUM_DECISIONS": spec.parameters["num_decisions"],
            "SEED_PROPORTION": format(spec.parameters["seed_proportion"], ".12g"),
            "SEED_DECISION": str(spec.parameters["seed_decision"]),
            "CONSENSUS_SIGMA": format(spec.parameters["consensus_sigma"], ".12g"),
        }
        source = DISCRETE_TEMPLATE
    else:
        raise ValueError(f"unknown template: {spec.template}")
    return Template(source.read_text(encoding="utf-8")).substitute(context)


def _match_number(regex: re.Pattern[str], output: str, cast: type = float) -> Any | None:
    match = regex.search(output)
    return cast(match.group(1)) if match else None


def parse_simulator_output(output: str) -> dict[str, Any]:
    return {
        "converged": CONSENSUS_RE.search(output) is not None,
        "max_iterations_elapsed": "MAX TIME ELASPED!" in output,
        "iterations": _match_number(FINAL_ITER_RE, output, int),
        "robot_count": _match_number(ROBOT_COUNT_RE, output, int),
        "mean_connectivity": _match_number(CONNECTIVITY_RE, output),
        "final_error_heading_rad": _match_number(HEADING_RE, output),
        "final_error_position_m": _match_number(POSITION_RE, output),
        "final_decision": _match_number(DECISION_RE, output, int),
    }


def git_value(*args: str) -> str | None:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
    )
    return completed.stdout.strip() if completed.returncode == 0 else None


def execute_run(
    spec: RunSpec,
    output_dir: Path,
    binary: Path,
    timeout_s: int,
    omp_threads: int,
) -> dict[str, Any]:
    config_dir = output_dir / "configs"
    log_dir = output_dir / "logs"
    config_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / f"{spec.run_id}.yaml"
    log_path = log_dir / f"{spec.run_id}.log"
    rendered = render_config(spec)
    config_path.write_text(rendered, encoding="utf-8")
    config_sha256 = hashlib.sha256(rendered.encode("utf-8")).hexdigest()

    # The released DArgs parser tokenises string values with stringstream, so an
    # absolute path containing the workspace's space ("Beyond Dancers") is
    # truncated. A cwd-relative path preserves the exact source while avoiding
    # that upstream parser limitation.
    config_argument = os.path.relpath(config_path, ROOT)
    command = [str(binary), "--cfg", config_argument]
    env = os.environ.copy()
    env.update(
        {
            "OMP_NUM_THREADS": str(omp_threads),
            "OMP_DYNAMIC": "FALSE",
            "OMP_WAIT_POLICY": "passive",
        }
    )
    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout_s,
            check=False,
        )
        output = completed.stdout + completed.stderr
        returncode: int | None = completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout.decode() if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode() if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        output = stdout + stderr + f"\nRUNNER TIMEOUT after {timeout_s}s\n"
        returncode = None
    duration = time.monotonic() - started
    log_path.write_text(output, encoding="utf-8")
    metrics = parse_simulator_output(output)
    error = None
    if timed_out:
        error = "runner_timeout"
    elif returncode != 0:
        error = f"simulator_exit_{returncode}"
    elif metrics["iterations"] is None:
        error = "missing_final_metrics"

    return {
        "schema_version": 1,
        "run_id": spec.run_id,
        "experiment": spec.experiment,
        "algorithm": "DANCeRS",
        "trial": spec.trial,
        "seed": spec.seed,
        "parameters": spec.parameters,
        "metrics": metrics,
        "error": error,
        "returncode": returncode,
        "runner_timed_out": timed_out,
        "duration_seconds": duration,
        "omp_threads": omp_threads,
        "config": str(config_path.relative_to(output_dir)),
        "config_sha256": config_sha256,
        "log": str(log_path.relative_to(output_dir)),
        "completed_utc": utc_now(),
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")
        handle.flush()


def aggregate_results(results: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for result in results:
        key = (result["experiment"], json.dumps(result["parameters"], sort_keys=True))
        grouped.setdefault(key, []).append(result)

    aggregates = []
    for (experiment, params_json), rows in sorted(grouped.items()):
        successful_runs = [row for row in rows if row["error"] is None]
        converged = [row for row in successful_runs if row["metrics"]["converged"]]
        censored = [
            row
            for row in successful_runs
            if not row["metrics"]["converged"]
            and row["metrics"].get("max_iterations_elapsed") is True
        ]
        iterations = [row["metrics"]["iterations"] for row in converged]
        all_valid_iterations = [
            row["metrics"]["iterations"]
            for row in successful_runs
            if row["metrics"].get("iterations") is not None
        ]
        aggregate = {
            "experiment": experiment,
            "parameters": json.loads(params_json),
            "trials_requested_or_resumed": len(rows),
            "valid_runs": len(successful_runs),
            "failed_runs": len(rows) - len(successful_runs),
            "converged_runs": len(converged),
            "censored_runs": len(censored),
            "success_pct": (100.0 * len(converged) / len(successful_runs)) if successful_runs else None,
            "mean_iterations_converged": statistics.fmean(iterations) if iterations else None,
            "stdev_iterations_converged": statistics.stdev(iterations) if len(iterations) > 1 else (0.0 if iterations else None),
            "median_iterations_converged": statistics.median(iterations) if iterations else None,
            "min_iterations_converged": min(iterations) if iterations else None,
            "max_iterations_converged": max(iterations) if iterations else None,
            # Continuous experiments have no paper-reported cutoff. Keep a
            # separate, explicitly censored 50-trial statistic so a safety
            # ceiling is never mistaken for time-to-convergence.
            "mean_iterations_all_valid_including_censoring": (
                statistics.fmean(all_valid_iterations) if all_valid_iterations else None
            ),
        }
        aggregates.append(aggregate)
    return aggregates


def wilson_interval_95(successes: int, total: int) -> tuple[float, float]:
    if total <= 0:
        raise ValueError("total must be positive")
    z = 1.959963984540054
    proportion = successes / total
    z2 = z * z
    denominator = 1.0 + z2 / total
    centre = (proportion + z2 / (2.0 * total)) / denominator
    margin = z * math.sqrt(
        proportion * (1.0 - proportion) / total + z2 / (4.0 * total * total)
    ) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def compare_with_paper(
    contract: dict[str, Any], aggregates: list[dict[str, Any]], trials: int
) -> dict[str, Any]:
    exp = contract["experiments"]["informed_robots"]
    expected_by_zeta = dict(zip(exp["seed_proportions"], exp["reported_success_pct"]["DANCeRS"]))
    table_rows = []
    for aggregate in aggregates:
        if aggregate["experiment"] != "informed_robots":
            continue
        zeta = aggregate["parameters"]["seed_proportion"]
        expected = expected_by_zeta[zeta]
        observed = aggregate["success_pct"]
        comparable = trials == contract["shared_protocol"]["trials"] and aggregate["valid_runs"] == trials
        exact_match = (observed == expected) if comparable and observed is not None else None
        interval = (
            wilson_interval_95(aggregate["converged_runs"], aggregate["valid_runs"])
            if comparable
            else None
        )
        statistically_compatible = (
            interval[0] <= expected / 100.0 <= interval[1]
            if interval is not None
            else None
        )
        table_rows.append(
            {
                "seed_proportion": zeta,
                "reported_success_pct": expected,
                "observed_success_pct": observed,
                "difference_percentage_points": (observed - expected) if observed is not None else None,
                "comparable": comparable,
                "exact_match": exact_match,
                "observed_wilson_95pct": (
                    [100.0 * interval[0], 100.0 * interval[1]] if interval is not None else None
                ),
                "statistically_compatible_95pct": statistically_compatible,
            }
        )
    table_rows.sort(key=lambda row: row["seed_proportion"])
    complete_table = len(table_rows) == len(expected_by_zeta)
    exact_table_match = complete_table and all(row["exact_match"] is True for row in table_rows)
    compatible_table_match = complete_table and all(
        row["statistically_compatible_95pct"] is True for row in table_rows
    )
    return {
        "schema_version": 1,
        "paper_trial_count": contract["shared_protocol"]["trials"],
        "run_trial_count": trials,
        "numeric_reference_availability": {
            "continuous_convergence": "plot_only",
            "sliding_window": "plot_only",
            "discrete_convergence": "plot_only",
            "sigma_sweep": "plot_only",
            "informed_robots": "exact_table",
            "shape_formation": "qualitative_only"
        },
        "informed_robots_table": table_rows,
        "informed_robots_exact_match": exact_table_match if complete_table and trials == 50 else None,
        "informed_robots_statistically_compatible_95pct": (
            compatible_table_match if complete_table and trials == 50 else None
        ),
        "notes": [
            "Plot-only paper results are aggregated with the same metric definitions but cannot be asserted numerically because the paper source contains no raw values.",
            "Continuous runs that reach the replication safety ceiling are reported as censored; their ceiling value is available only in the explicitly named all-valid statistic and is never treated as convergence.",
            "The paper does not report trial seeds; this runner uses deterministic seeds 1..N and records them in every result.",
            "For stochastic percentages, compatibility means the paper value lies inside the two-sided 95% Wilson interval of the observed binomial proportion; exact equality is reported separately."
        ]
    }


def write_aggregates(output_dir: Path, aggregates: list[dict[str, Any]]) -> None:
    (output_dir / "summary.json").write_text(
        json.dumps(aggregates, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    fieldnames = [
        "experiment",
        "parameters_json",
        "trials_requested_or_resumed",
        "valid_runs",
        "failed_runs",
        "converged_runs",
        "censored_runs",
        "success_pct",
        "mean_iterations_converged",
        "stdev_iterations_converged",
        "median_iterations_converged",
        "min_iterations_converged",
        "max_iterations_converged",
        "mean_iterations_all_valid_including_censoring",
    ]
    with (output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in aggregates:
            csv_row = dict(row)
            csv_row["parameters_json"] = json.dumps(csv_row.pop("parameters"), sort_keys=True)
            writer.writerow(csv_row)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        choices=("all", "continuous", "sliding", "discrete", "sigma", "informed", "quick"),
        default="quick",
    )
    parser.add_argument("--trials", type=int, default=None, help="default: paper's 50, or 1 for quick")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--omp-threads", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=1800, help="per-run timeout in seconds")
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--require-paper-match", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.jobs < 1 or args.omp_threads < 1:
        raise SystemExit("--jobs and --omp-threads must be positive")
    contract = load_contract(args.contract)
    trials = args.trials if args.trials is not None else (1 if args.suite == "quick" else contract["shared_protocol"]["trials"])
    plan = build_plan(contract, args.suite, trials)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = (args.output or (ROOT / "results/replication" / stamp)).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    results_path = output_dir / "runs.jsonl"
    previous_results = load_jsonl(results_path)
    completed_ids = {row["run_id"] for row in previous_results}
    pending = [spec for spec in plan if spec.run_id not in completed_ids]

    manifest = {
        "schema_version": 1,
        "created_utc": utc_now(),
        "suite": args.suite,
        "trials": trials,
        "jobs": args.jobs,
        "omp_threads_per_run": args.omp_threads,
        "timeout_seconds_per_run": args.timeout,
        "source_commit_reported": REPORTED_SOURCE_COMMIT,
        "local_head": git_value("rev-parse", "HEAD"),
        "configured_remotes": (git_value("remote", "-v") or "").splitlines(),
        "binary": str(args.binary.resolve()),
        "contract": str(args.contract.resolve()),
        "total_planned_runs": len(plan),
        "resumed_runs": len(plan) - len(pending),
        "pending_runs": len(pending),
        "runs": [asdict(spec) | {"run_id": spec.run_id} for spec in plan],
    }
    (output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Output: {output_dir}")
    print(f"Plan: {len(plan)} runs ({len(pending)} pending, {len(plan) - len(pending)} resumed)")
    if args.dry_run:
        return 0
    if not args.binary.exists():
        print(f"Missing simulator binary: {args.binary}", file=sys.stderr)
        print("Run `pixi run build` first.", file=sys.stderr)
        return 2

    new_results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.jobs) as executor:
        futures = {
            executor.submit(execute_run, spec, output_dir, args.binary.resolve(), args.timeout, args.omp_threads): spec
            for spec in pending
        }
        complete = len(plan) - len(pending)
        for future in as_completed(futures):
            spec = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # preserve progress and make orchestration failures visible
                result = {
                    "schema_version": 1,
                    "run_id": spec.run_id,
                    "experiment": spec.experiment,
                    "trial": spec.trial,
                    "seed": spec.seed,
                    "parameters": spec.parameters,
                    "metrics": {"converged": False, "iterations": None},
                    "error": f"runner_exception: {exc}",
                    "completed_utc": utc_now(),
                }
            append_jsonl(results_path, result)
            new_results.append(result)
            complete += 1
            status = "ok" if result["error"] is None else result["error"]
            print(f"[{complete}/{len(plan)}] {spec.run_id}: {status}", flush=True)

    all_results = previous_results + new_results
    selected_ids = {spec.run_id for spec in plan}
    selected_results = [row for row in all_results if row["run_id"] in selected_ids]
    aggregates = aggregate_results(selected_results)
    write_aggregates(output_dir, aggregates)
    comparison = compare_with_paper(contract, aggregates, trials)
    (output_dir / "comparison.json").write_text(
        json.dumps(comparison, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    failures = [row for row in selected_results if row.get("error")]
    print(f"Completed with {len(failures)} failed runs. Summary: {output_dir / 'summary.csv'}")
    if failures:
        return 1
    if args.require_paper_match and comparison["informed_robots_exact_match"] is not True:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
