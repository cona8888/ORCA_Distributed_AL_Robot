#!/usr/bin/env python3
"""Launch one fully authenticated DANCeRS--ORCA publication session.

This is the registered public bridge from the external
authorization/admission documents to the runner's internal ExecutionPermit.
It has one all-or-nothing ``launch`` command and no resume, retry, or
sequence-selection surface.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Any, Mapping, Sequence


REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.replication import prepare_orca_execution_admission as admission
from scripts.replication import prepare_orca_execution_capsule as capsule
from scripts.replication import run_orca_comparison as runner


EXACT_CONFIRMATION = "RUN-ALL-363-ONCE"


class LauncherError(RuntimeError):
    """A fail-closed execution-launcher error."""


def _runtime_from_manifest(manifest: Mapping[str, Any]) -> runner.RuntimeBindings:
    try:
        runtime = manifest["runtime"]
        binaries = runtime["binaries"]
        roots = runtime["source_roots"]
        return runner.RuntimeBindings(
            orca_binary=pathlib.Path(binaries["orca"]["path"]),
            optimized_fidelity_binary=pathlib.Path(
                binaries["optimized_fidelity"]["path"]
            ),
            optimized_timing_binary=pathlib.Path(
                binaries["optimized_timing"]["path"]
            ),
            reference_fidelity_binary=pathlib.Path(
                binaries["reference_fidelity"]["path"]
            ),
            reference_timing_binary=pathlib.Path(
                binaries["reference_timing"]["path"]
            ),
            optimized_source_root=pathlib.Path(roots["optimized"]),
            reference_source_root=pathlib.Path(roots["reference"]),
            libomp=pathlib.Path(runtime["libomp"]["path"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise LauncherError("execution capsule runtime paths are malformed") from error


def _authenticated_context(
    *,
    capsule_root: pathlib.Path,
    expected_capsule_sha256: str,
    output_root: pathlib.Path,
    session_id: str,
    authorization_manifest: pathlib.Path,
    expected_authorization_sha256: str,
    admission_manifest: pathlib.Path,
    expected_admission_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], pathlib.Path, runner.RuntimeBindings]:
    binding = admission.build_binding(
        capsule_root,
        expected_capsule_sha256,
        output_root,
        session_id,
    )
    decision = admission.verify_decisions(
        binding,
        authorization_manifest,
        expected_authorization_sha256,
        admission_manifest,
        expected_admission_sha256,
    )
    if (
        decision.get("status") != "admitted_without_execution"
        or decision.get("decisions_authenticated") is not True
        or decision.get("execution_authorized") is not False
        or decision.get("publication_processes_executed") != 0
        or decision.get("publication_simulation_steps") != 0
    ):
        raise LauncherError("admission decision receipt differs")
    try:
        manifest, _payloads = capsule.load_capsule_files(
            capsule_root, expected_capsule_sha256
        )
        input_root = pathlib.Path(manifest["input"]["root"])
        runtime = _runtime_from_manifest(manifest)
    except (capsule.CapsuleError, OSError, KeyError, TypeError, ValueError) as error:
        raise LauncherError(f"execution context reconstruction failed: {error}") from error
    if (
        admission.sha256_bytes(admission.canonical_json_bytes(manifest["runtime"]))
        != binding["runtime_contract_sha256"]
    ):
        raise LauncherError("reconstructed runtime contract differs")
    return binding, decision, input_root, runtime


def launch_authenticated(
    *,
    capsule_root: pathlib.Path,
    expected_capsule_sha256: str,
    output_root: pathlib.Path,
    session_id: str,
    authorization_manifest: pathlib.Path,
    expected_authorization_sha256: str,
    admission_manifest: pathlib.Path,
    expected_admission_sha256: str,
    confirmation: str,
) -> dict[str, Any]:
    """Authenticate twice, then invoke the exact runner entry point once."""

    if confirmation != EXACT_CONFIRMATION:
        raise LauncherError("exact complete-schedule confirmation is absent")
    first = _authenticated_context(
        capsule_root=capsule_root,
        expected_capsule_sha256=expected_capsule_sha256,
        output_root=output_root,
        session_id=session_id,
        authorization_manifest=authorization_manifest,
        expected_authorization_sha256=expected_authorization_sha256,
        admission_manifest=admission_manifest,
        expected_admission_sha256=expected_admission_sha256,
    )
    second = _authenticated_context(
        capsule_root=capsule_root,
        expected_capsule_sha256=expected_capsule_sha256,
        output_root=output_root,
        session_id=session_id,
        authorization_manifest=authorization_manifest,
        expected_authorization_sha256=expected_authorization_sha256,
        admission_manifest=admission_manifest,
        expected_admission_sha256=expected_admission_sha256,
    )
    first_binding, _first_decision, _first_input, _first_runtime = first
    binding, _decision, input_root, runtime = second
    if first_binding != binding:
        raise LauncherError("authenticated binding changed during final admission")

    permit = runner.ExecutionPermit(
        execution_authorized=True,
        study_id=admission.STUDY_ID,
        plan_sha256=binding["execution_plan_sha256"],
        input_manifest_sha256=binding["input_manifest_sha256"],
        schedule_sha256=binding["schedule_sha256"],
        authorization_manifest_sha256=expected_authorization_sha256,
        admission_manifest_sha256=expected_admission_sha256,
        output_root=output_root,
        expected_processes=admission.EXPECTED_PROCESSES,
        selective_retry_allowed=False,
    )
    return runner.execute_authorized(
        input_root=input_root,
        output_root=output_root,
        runtime=runtime,
        permit=permit,
    )


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    command = value.add_subparsers(dest="command", required=True).add_parser(
        "launch", help="run the complete authenticated 363-process schedule once"
    )
    command.add_argument(
        "--capsule-root", type=pathlib.Path, default=admission.DEFAULT_CAPSULE_ROOT
    )
    command.add_argument(
        "--expected-capsule-sha256",
        default=admission.CAPSULE_MANIFEST_SHA256,
    )
    command.add_argument("--output-root", type=pathlib.Path, required=True)
    command.add_argument("--session-id", required=True)
    command.add_argument(
        "--authorization-manifest", type=pathlib.Path, required=True
    )
    command.add_argument("--expected-authorization-sha256", required=True)
    command.add_argument("--admission-manifest", type=pathlib.Path, required=True)
    command.add_argument("--expected-admission-sha256", required=True)
    command.add_argument("--confirmation", required=True)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        session = launch_authenticated(
            capsule_root=args.capsule_root,
            expected_capsule_sha256=args.expected_capsule_sha256,
            output_root=args.output_root,
            session_id=args.session_id,
            authorization_manifest=args.authorization_manifest,
            expected_authorization_sha256=args.expected_authorization_sha256,
            admission_manifest=args.admission_manifest,
            expected_admission_sha256=args.expected_admission_sha256,
            confirmation=args.confirmation,
        )
        sys.stdout.buffer.write(runner.canonical_json_bytes(session))
        return 0
    except (
        LauncherError,
        admission.AdmissionError,
        capsule.CapsuleError,
        runner.RunnerError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        print(f"execution launch failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
