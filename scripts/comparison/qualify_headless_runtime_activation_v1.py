#!/usr/bin/env python3
"""Run a nonauthorizing suspended-image runtime-root activation qualification."""

from __future__ import annotations

import sys


sys.dont_write_bytecode = True
if not (
    sys.flags.isolated
    and sys.flags.no_site
    and sys.flags.dont_write_bytecode
    and sys.path
    and sys.path[0] != ""
):
    raise SystemExit(
        "headless-runtime-activation-v1: reject: public invocation requires python -I -S -B"
    )

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
import types
from typing import Any, Optional, Sequence


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "dancers-headless-runtime-activation-receipt-v1"
TRACK_ID = "simulator-demo-v1-headless-epoch-1"
RUNNER_PATH = "scripts/comparison/run_headless_epoch_v1.py"
RUNNER_BYTES = 310_335
RUNNER_SHA256 = "c0724cc6cc87dd1613fe9606aef4d3512a243393985ee4a012ecbb5b068cc18e"
ATTESTER_PATH = "scripts/comparison/attest_headless_binaries_v2.py"
ATTESTER_BYTES = 21_944
ATTESTER_SHA256 = "01219df2d2dc611509ba45a467322bb4aa0099f6ebf844bcae1a6ceedc6020d1"
DESIGN_PATH = "comparison/headless-runtime-rebuild-v1/design_v1.json"
DESIGN_BYTES = 24_971
DESIGN_SHA256 = "826835e100dfbbae4fc558f83e85aa07019ca366ca5df417e3ea5d81a31d02b3"
SENTINEL_PATH = "comparison/headless-runtime-rebuild-v1/runtime-root-sentinel-v1.txt"
SENTINEL_BYTES = 130
SENTINEL_SHA256 = "4c4b2b55f37ccaac958f72c0621281ae3771966d0111dfc28c70601d9ecf5e1e"
PROBE_PATCH_PATH = "comparison/headless-runtime-rebuild-v1/runtime-root-probe.patch"
PROBE_PATCH_BYTES = 1_384
PROBE_PATCH_SHA256 = "f77b1c8ff7d88cfdb14106174b817a8d26f042696d6611c1e80d84bb86fefbaf"
PRODUCT_IDS = (
    "reference-timing",
    "reference-fidelity",
    "optimized-timing",
    "optimized-fidelity",
)
MAX_SOURCE_BYTES = 2 * 1024 * 1024


class ActivationError(RuntimeError):
    pass


def _canonical(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_exact_source(root: Path, relative: str, expected_bytes: int, expected_sha: str) -> tuple[Path, bytes]:
    parts = PurePosixPath(relative).parts
    if not parts or any(part in ("", ".", "..") for part in parts):
        raise ActivationError("authenticated source path differs")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    directory_flags = flags | os.O_DIRECTORY
    owned: list[int] = []
    try:
        descriptor = os.open(root, directory_flags)
        owned.append(descriptor)
        for component in parts[:-1]:
            descriptor = os.open(component, directory_flags, dir_fd=descriptor)
            owned.append(descriptor)
        file_descriptor = os.open(parts[-1], flags, dir_fd=descriptor)
        owned.append(file_descriptor)
        before = os.fstat(file_descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size != expected_bytes
            or before.st_size <= 0
            or before.st_size > MAX_SOURCE_BYTES
        ):
            raise ActivationError(f"authenticated source identity differs: {relative}")
        chunks: list[bytes] = []
        remaining = before.st_size
        while remaining:
            chunk = os.read(file_descriptor, min(remaining, 1024 * 1024))
            if not chunk:
                raise ActivationError(f"authenticated source truncated: {relative}")
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(file_descriptor)
        raw = b"".join(chunks)
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            or _sha(raw) != expected_sha
        ):
            raise ActivationError(f"authenticated source changed or differs: {relative}")
        return root.joinpath(*parts), raw
    finally:
        primary = sys.exc_info()[1]
        errors: list[BaseException] = []
        while owned:
            descriptor = owned.pop()
            try:
                os.close(descriptor)
            except BaseException as error:
                errors.append(error)
        if primary is None and errors:
            raise errors[0]


def _load_exact(name: str, path: Path, raw: bytes) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__file__ = str(path)
    module.__package__ = "scripts.comparison"
    sys.modules[name] = module
    try:
        exec(compile(raw, str(path), "exec", dont_inherit=True), module.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def _file_identity(runner: Any, relative: str, byte_count: int, digest: str) -> Any:
    return runner.FileIdentity(PurePosixPath(relative), byte_count, digest)


def qualify(repository_root: Path, build_root: Path) -> dict[str, Any]:
    lexical_root = repository_root.absolute()
    canonical_root = repository_root.resolve(strict=True)
    if lexical_root != canonical_root:
        raise ActivationError("repository root must be canonical physical path")
    runner_path, runner_raw = _read_exact_source(
        canonical_root, RUNNER_PATH, RUNNER_BYTES, RUNNER_SHA256
    )
    attester_path, attester_raw = _read_exact_source(
        canonical_root, ATTESTER_PATH, ATTESTER_BYTES, ATTESTER_SHA256
    )
    runner = _load_exact("_dancers_activation_runner_v1", runner_path, runner_raw)
    attester = _load_exact("_dancers_activation_attester_v2", attester_path, attester_raw)
    attestation = attester.attest_build(canonical_root, build_root)
    if (
        attestation.get("schema_version") != runner.RUNTIME_ATTESTATION_SCHEMA
        or attestation.get("status") != runner.RUNTIME_ATTESTATION_STATUS
        or attestation.get("track_id") != TRACK_ID
    ):
        raise ActivationError("v2 build attestation boundary differs")
    attestation_raw = _canonical(attestation)
    build_relative = PurePosixPath(
        Path(attestation["build"]["build_root"]).relative_to(canonical_root).as_posix()
    )
    repository = runner.RepositoryDescriptorCustody(canonical_root)
    reservations = None
    session = None
    shim = None
    results: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="dancers-activation-v1-", dir="/private/tmp") as temporary:
        result_root = Path(temporary) / "activation"
        result_root.mkdir(mode=0o700)
        try:
            reservations = runner.FixedDescriptorReservations.acquire(
                ambiguity_terminal=None
            )
            session = runner.DescriptorSessionCustody.acquire(
                result_root, _sha(attestation_raw), "runtime-root-probe"
            )
            shim_record = attestation["spawn_shim"]["binary"]
            shim_identity = _file_identity(
                runner,
                (build_relative / PurePosixPath(shim_record["path"])).as_posix(),
                shim_record["bytes"],
                shim_record["sha256"],
            )
            shim = repository.open_identity(shim_identity, "shim")
            expected_stdout_raw = (
                f"{runner.RUNTIME_ROOT_PROBE_ENV_VALUE}\n"
                f"runtime_artifact_root={canonical_root}\n"
                f"sentinel_sha256={SENTINEL_SHA256}\n"
                "dancers-headless-runtime-root-sentinel-v1\n"
                "This file binds the rebuilt quartet to one authenticated main-repository artifact root.\n"
            ).encode("utf-8")
            input_config = _file_identity(runner, DESIGN_PATH, DESIGN_BYTES, DESIGN_SHA256)
            input_map = _file_identity(runner, SENTINEL_PATH, SENTINEL_BYTES, SENTINEL_SHA256)
            input_scenario = _file_identity(
                runner, PROBE_PATCH_PATH, PROBE_PATCH_BYTES, PROBE_PATCH_SHA256
            )
            for product_id in PRODUCT_IDS:
                record = attestation["products"][product_id]
                binary = record["binary"]
                target_identity = _file_identity(
                    runner,
                    (build_relative / PurePosixPath(binary["path"])).as_posix(),
                    binary["bytes"],
                    binary["sha256"],
                )
                target = repository.open_identity(target_identity, "target")
                prepared = None
                attempt = session.create_attempt(product_id)
                observer_mode = "digest" if record["observer_compiled"] else "none"
                runtime = runner.DescriptorRuntime(100, 1, observer_mode, 1, 1, 100)
                metadata = (
                    _canonical({"purpose": "runtime-root-activation-only", "product_id": product_id})
                    if observer_mode != "none"
                    else None
                )
                try:
                    prepared = runner.prepare_descriptor_attempt(
                        repository,
                        attempt,
                        config=input_config,
                        map_artifact=input_map,
                        scenario=input_scenario,
                        runtime=runtime,
                        metadata_bytes=metadata,
                    )
                    request = prepared.process_request(
                        shim=shim,
                        target=target,
                        repository_descriptor=repository.descriptor,
                        reservations=reservations,
                        runtime=runtime,
                        timeout_seconds=15.0,
                        termination_grace_seconds=1.0,
                        execution_mode="runtime-root-probe",
                    )
                    measurement = runner.run_custodied_process(request)
                    stdout = attempt.read_file(prepared.stdout, 64 * 1024)
                    stderr = attempt.read_file(prepared.stderr, 64 * 1024)
                    if (
                        measurement.returncode != 0
                        or measurement.timed_out
                        or stdout != expected_stdout_raw
                        or stderr != b""
                    ):
                        raise ActivationError(f"runtime-root activation failed: {product_id}")
                    for output_role in ("trace", "summary"):
                        if output_role in prepared.roles:
                            output = prepared.roles[output_role]
                            if attempt.read_file(output, 1) != b"":
                                raise ActivationError(
                                    f"activation unexpectedly wrote {output_role}: {product_id}"
                                )
                    results.append(
                        {
                            "expected_cdhash": record["expected_cdhash"],
                            "expected_lc_uuid": record["expected_lc_uuid"],
                            "observer_compiled": record["observer_compiled"],
                            "product_id": product_id,
                            "returncode": measurement.returncode,
                            "stderr_bytes": 0,
                            "stdout_bytes": len(stdout),
                            "stdout_sha256": _sha(stdout),
                            "timed_out": measurement.timed_out,
                            "two_stage_suspended_cdhash_proof": True,
                        }
                    )
                finally:
                    runner.close_custodied_file(target)
                    if prepared is not None:
                        prepared.close()
        finally:
            if shim is not None:
                runner.close_custodied_file(shim)
            if session is not None:
                session.close()
            repository.close()
            if reservations is not None:
                reservations.close(ambiguity_terminal=None)
    return {
        "attestation": {
            "bytes": len(attestation_raw),
            "schema_version": attestation["schema_version"],
            "sha256": _sha(attestation_raw),
            "status": attestation["status"],
        },
        "classification": {
            "activation_qualified": True,
            "calibration_execution_authorized": False,
            "host_bound": True,
            "plan_frozen": False,
            "scientific_evidence": False,
            "scientific_evidence_eligible": False,
            "validation_execution_authorized": False,
        },
        "operations": {
            "binary_executed": True,
            "descriptor_test_product_invoked": False,
            "ephemeral_activation_outputs_written": True,
            "ephemeral_activation_tree_retained": False,
            "network_accessed": False,
            "plan_written": False,
            "scientific_result_namespace_written": False,
            "runtime_product_invoked": True,
            "shim_product_invoked": True,
            "simulator_constructed": False,
        },
        "products": results,
        "runtime_artifact_root": str(canonical_root),
        "schema_version": SCHEMA,
        "status": "pass-host-bound-runtime-root-activation-nonauthorizing",
        "track_id": TRACK_ID,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", default=str(ROOT))
    parser.add_argument("--build-root", required=True)
    parser.add_argument("--execute-engineering-activation-smoke", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if not args.execute_engineering_activation_smoke:
        print(
            "headless-runtime-activation-v1: reject: explicit engineering activation gate is required",
            file=sys.stderr,
        )
        return 2
    try:
        receipt = qualify(Path(args.repository_root), Path(args.build_root))
    except (ActivationError, OSError, ValueError) as error:
        print(f"headless-runtime-activation-v1: reject: {error}", file=sys.stderr)
        return 1
    sys.stdout.buffer.write(_canonical(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
