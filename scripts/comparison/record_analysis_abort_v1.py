#!/usr/bin/env python3
"""Exclusively record the sealed rerun-1 v3 analysis failure."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


SCHEMA_VERSION = "dancers-analysis-abort-v1"
AMENDMENT_ID = "fh-lower-count-fidelity-2026-07-28-v1"
EXECUTION_EPOCH_ID = "fh-lower-count-fidelity-2026-07-28-rerun-1"
COHORT_ID = "fractal-high-lower-count-fidelity-amended-v1-rerun-1"
EXPECTED_EVIDENCE_COMMIT = "a1a83693a16b74449aad6e5d2fc9ec7708fff461"

SESSION_RELATIVE = Path(
    "results/comparison/calibration/trace-v3-amended-v1/"
    "fractal-high-lower-count-fidelity-rerun-1"
)
PRIOR_FAILED_RELATIVE = Path(
    "results/comparison/calibration/trace-v3-amended-v1/"
    "fractal-high-lower-count-fidelity"
)
ANALYZER_RELATIVE = Path("scripts/comparison/analyze_results_v3.py")
ANALYSIS_RELATIVE = SESSION_RELATIVE.with_name(
    "fractal-high-lower-count-fidelity-rerun-1-analysis.json"
)
RECEIPT_RELATIVE = SESSION_RELATIVE.with_name(
    "fractal-high-lower-count-fidelity-rerun-1-gate-receipt.json"
)
ABORT_RELATIVE = SESSION_RELATIVE.with_name(
    "fractal-high-lower-count-fidelity-rerun-1-analysis-abort.json"
)

EXPECTED_FILE_COUNT = 327
EXPECTED_ATTEMPT_COUNT = 40
EXPECTED_BLOCK_COUNT = 20
EXPECTED_INVENTORY_SHA256 = (
    "e6f5bc297391cca2ceafb7b5aa04d86614271cadbe6c15687c301932bebc4560"
)
INVENTORY_DIGEST_ENCODING = (
    "SHA-256 of canonical JSON for the relative-path-sorted array of "
    "{path,bytes,sha256} records"
)
EMPTY_SHA256 = (
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
)
EXPECTED_ANALYZER_SHA256 = (
    "61a6bf537c6376ab4780df6fdb15f01b9385417abdffa85d190dee6f4ae19e97"
)
EXPECTED_ANALYZER_BYTES = 267_172
EXACT_ANALYSIS_ERROR = (
    "preflight.json.artifacts.prior_failed_pairs.bytes must be >= 1"
)

EXPECTED_CORE_ARTIFACTS: dict[str, dict[str, Any]] = {
    "frozen_plan.json": {
        "bytes": 24_807,
        "sha256": (
            "46197dd373abfc12da72409929c59cb4f53491f8a679d3a1c9e81ab2fb9701fb"
        ),
    },
    "run_manifest.json": {
        "bytes": 11_704,
        "sha256": (
            "8bbf56f8c08206414448b0c798806c6fcaa22d1b3c10b01293d78572a94d463b"
        ),
    },
    "preflight.json": {
        "bytes": 41_018,
        "sha256": (
            "0461914d7708a50ed08d566b6d353b0a326cdff23e0a2c34ea6cce9c45b1ccbc"
        ),
    },
    "session.json": {
        "bytes": 7_436,
        "sha256": (
            "c3812c6b082ce41b3d0a865c58e94142d782fd67f0870f0b22812781a4299677"
        ),
    },
    "runs.jsonl": {
        "bytes": 50_300_960,
        "sha256": (
            "158e6befd23d2abc8815a4d4bc69099d107ec94d9477f0aaff09f46d9c3145b2"
        ),
    },
    "runs.csv": {
        "bytes": 34_615,
        "sha256": (
            "7ea89fa1ef119d0f33c19ac6d623ffba4c42d69e017d885ddc99f2ca571dca0f"
        ),
    },
    "pairs.jsonl": {
        "bytes": 79_350,
        "sha256": (
            "613a169cfbc399de27fe0dfb1f9000a00588dc33665894c66a8e35244818fdf3"
        ),
    },
}
ATTEMPT_FILENAMES = frozenset(
    {
        "observer_metadata.json",
        "result.json",
        "runner_metadata.json",
        "stderr.log",
        "stdout.log",
        "summary.json",
        "trace.jsonl",
    }
)
BLOCK_FILENAMES = frozenset({"config.yaml", "pair.json"})


class AnalysisAbortError(RuntimeError):
    """The exact sealed-evidence contract was not satisfied."""


@dataclass(frozen=True)
class RecordedAnalysisAbort:
    path: Path
    sha256: str
    inventory_sha256: str


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _duplicate_guard(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _strict_json(path: Path, label: str) -> dict[str, Any]:
    data = path.read_bytes()
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_duplicate_guard,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-finite JSON constant {value!r}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise AnalysisAbortError(f"{label} is not strict JSON") from error
    if not isinstance(value, dict) or data != canonical_json_bytes(value):
        raise AnalysisAbortError(f"{label} is not a canonical JSON object")
    return value


def _sha256_file(path: Path, label: str) -> tuple[str, int]:
    if path.is_symlink():
        raise AnalysisAbortError(f"{label} must not be a symbolic link")
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise AnalysisAbortError(f"{label} must be a regular file")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    identity = lambda item: (
        item.st_dev,
        item.st_ino,
        item.st_size,
        item.st_mtime_ns,
        item.st_ctime_ns,
    )
    if identity(before) != identity(after):
        raise AnalysisAbortError(f"{label} changed while being read")
    return digest.hexdigest(), int(after.st_size)


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise AnalysisAbortError(
            f"git {' '.join(arguments)} failed: {detail}"
        )
    return completed.stdout.strip()


def _repository(repository_root: Path) -> Path:
    candidate = repository_root.expanduser()
    if candidate.is_symlink():
        raise AnalysisAbortError("repository root must not be a symbolic link")
    repository = candidate.resolve(strict=True)
    if not repository.is_dir():
        raise AnalysisAbortError("repository root must be a directory")
    if Path(_git(repository, "rev-parse", "--show-toplevel")).resolve() != repository:
        raise AnalysisAbortError("repository root must be the Git worktree root")
    if _git(repository, "rev-parse", "HEAD") != EXPECTED_EVIDENCE_COMMIT:
        raise AnalysisAbortError(
            "HEAD must equal the sealed evidence commit "
            f"{EXPECTED_EVIDENCE_COMMIT}"
        )
    return repository


def _require_real_directory(path: Path, label: str) -> None:
    if path.is_symlink() or path.resolve(strict=True) != path or not path.is_dir():
        raise AnalysisAbortError(f"{label} must be an exact real directory")


def _require_absent(path: Path, label: str) -> None:
    if os.path.lexists(path):
        raise FileExistsError(f"{label} must be absent: {path}")


def _inventory(session_root: Path) -> tuple[list[dict[str, Any]], str]:
    _require_real_directory(session_root, "sealed session")
    entries: list[dict[str, Any]] = []
    pending = [session_root]
    while pending:
        directory = pending.pop()
        for child in sorted(os.scandir(directory), key=lambda item: item.name):
            path = Path(child.path)
            relative = path.relative_to(session_root).as_posix()
            if child.is_symlink():
                raise AnalysisAbortError(
                    f"sealed evidence contains a symbolic link: {relative}"
                )
            if child.is_dir(follow_symlinks=False):
                pending.append(path)
            elif child.is_file(follow_symlinks=False):
                digest, size = _sha256_file(
                    path, f"sealed evidence file {relative}"
                )
                entries.append(
                    {"path": relative, "bytes": size, "sha256": digest}
                )
            else:
                raise AnalysisAbortError(
                    f"sealed evidence contains a special file: {relative}"
                )
    entries.sort(key=lambda item: item["path"])
    if len(entries) != EXPECTED_FILE_COUNT:
        raise AnalysisAbortError(
            f"sealed evidence must contain {EXPECTED_FILE_COUNT} files, "
            f"observed {len(entries)}"
        )
    digest = hashlib.sha256(canonical_json_bytes(entries)).hexdigest()
    if digest != EXPECTED_INVENTORY_SHA256:
        raise AnalysisAbortError(
            "sealed evidence inventory SHA-256 changed: "
            f"expected {EXPECTED_INVENTORY_SHA256}, observed {digest}"
        )
    return entries, digest


def _child_directories(path: Path, label: str) -> list[Path]:
    _require_real_directory(path, label)
    result: list[Path] = []
    for child in sorted(os.scandir(path), key=lambda item: item.name):
        if child.is_symlink() or not child.is_dir(follow_symlinks=False):
            raise AnalysisAbortError(f"{label} contains a non-directory entry")
        result.append(Path(child.path))
    return result


def _require_file_set(path: Path, expected: frozenset[str], label: str) -> None:
    children = list(os.scandir(path))
    if any(
        child.is_symlink() or not child.is_file(follow_symlinks=False)
        for child in children
    ) or {child.name for child in children} != set(expected):
        raise AnalysisAbortError(f"{label} file set changed")


def _validate_layout(session_root: Path) -> None:
    attempts = _child_directories(
        session_root / "attempts", "sealed attempts"
    )
    if len(attempts) != EXPECTED_ATTEMPT_COUNT:
        raise AnalysisAbortError(
            f"expected 40 attempt directories, observed {len(attempts)}"
        )
    expected_conditions = {
        f"fractal_high-calibration-seed-{seed}-n-{count}"
        f"__rep-001__{method}"
        for count in (192, 256)
        for seed in range(1, 11)
        for method in ("reference", "optimized")
    }
    observed_conditions: set[str] = set()
    for sequence, attempt in enumerate(attempts, start=1):
        prefix = f"{sequence:06d}__"
        if not attempt.name.startswith(prefix):
            raise AnalysisAbortError("attempt sequence must be 000001..000040")
        observed_conditions.add(attempt.name[len(prefix) :])
        _require_file_set(attempt, ATTEMPT_FILENAMES, attempt.name)
    if observed_conditions != expected_conditions:
        raise AnalysisAbortError("sealed attempt condition grid changed")

    blocks = _child_directories(session_root / "blocks", "sealed blocks")
    expected_blocks = {
        f"fractal_high-calibration-seed-{seed}-n-{count}__rep-001"
        for count in (192, 256)
        for seed in range(1, 11)
    }
    if len(blocks) != EXPECTED_BLOCK_COUNT or {
        block.name for block in blocks
    } != expected_blocks:
        raise AnalysisAbortError("sealed 20-block condition grid changed")
    for block in blocks:
        _require_file_set(block, BLOCK_FILENAMES, block.name)


def _line_count(path: Path, label: str) -> int:
    count = 0
    with path.open("rb") as handle:
        for line in handle:
            if line == b"\n" or not line.endswith(b"\n"):
                raise AnalysisAbortError(f"{label} is malformed")
            count += 1
    return count


def _expect_scalars(
    record: dict[str, Any],
    expected: dict[str, Any],
    label: str,
) -> None:
    for field, value in expected.items():
        if type(record.get(field)) is not type(value) or record.get(field) != value:
            raise AnalysisAbortError(f"{label}.{field} is not registered")


def _validate_core(
    repository: Path,
    session_root: Path,
    entries: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    by_path = {str(entry["path"]): entry for entry in entries}
    core: dict[str, dict[str, Any]] = {}
    for filename, expected in EXPECTED_CORE_ARTIFACTS.items():
        observed = by_path.get(filename)
        if observed is None or any(
            observed[field] != expected[field] for field in ("bytes", "sha256")
        ):
            raise AnalysisAbortError(f"sealed core artifact changed: {filename}")
        core[filename] = {
            "path": (SESSION_RELATIVE / filename).as_posix(),
            "bytes": observed["bytes"],
            "sha256": observed["sha256"],
        }

    plan = _strict_json(session_root / "frozen_plan.json", "frozen_plan.json")
    preflight = _strict_json(session_root / "preflight.json", "preflight.json")
    session = _strict_json(session_root / "session.json", "session.json")
    _expect_scalars(
        plan,
        {
            "schema_version": "dancers-laptop-comparison-plan-v3",
            "amendment_id": AMENDMENT_ID,
            "execution_epoch_id": EXECUTION_EPOCH_ID,
            "cohort_id": COHORT_ID,
            "mode": "fidelity",
            "result_root": str(session_root),
            "analyzer_sha256": EXPECTED_ANALYZER_SHA256,
        },
        "frozen_plan.json",
    )
    _expect_scalars(
        preflight,
        {
            "schema_version": "dancers-laptop-comparison-plan-v3",
            "amendment_id": AMENDMENT_ID,
            "execution_epoch_id": EXECUTION_EPOCH_ID,
            "mode": "fidelity",
            "plan_sha256": EXPECTED_CORE_ARTIFACTS["frozen_plan.json"][
                "sha256"
            ],
        },
        "preflight.json",
    )
    _expect_scalars(
        session,
        {
            "schema_version": "dancers-laptop-comparison-session-v3",
            "execution_epoch_id": EXECUTION_EPOCH_ID,
            "cohort_id": COHORT_ID,
            "result_root": str(session_root),
            "attempt_count": 40,
            "pair_count": 20,
            "planned_method_attempt_slots": 40,
            "executed_method_attempt_slots": 40,
            "completed_method_attempt_slots": 40,
            "valid_method_attempt_slots": 40,
            "planned_pair_blocks": 20,
            "executed_pair_blocks": 20,
            "valid_pair_blocks": 0,
            "all_attempts_completed": True,
            "all_pairs_valid": False,
            "stopped_early": False,
        },
        "session.json",
    )
    if _line_count(session_root / "runs.jsonl", "runs.jsonl") != 40:
        raise AnalysisAbortError("runs.jsonl must contain 40 attempts")
    if _line_count(session_root / "pairs.jsonl", "pairs.jsonl") != 20:
        raise AnalysisAbortError("pairs.jsonl must contain 20 blocks")

    analyzer = {
        "path": str(repository / ANALYZER_RELATIVE),
        "sha256": EXPECTED_ANALYZER_SHA256,
    }
    if plan.get("artifacts", {}).get("analysis_revision") != analyzer:
        raise AnalysisAbortError("frozen plan analyzer binding changed")
    if preflight.get("artifacts", {}).get("analysis_revision") != {
        **analyzer,
        "bytes": EXPECTED_ANALYZER_BYTES,
    }:
        raise AnalysisAbortError("preflight analyzer attestation changed")
    return core


def _validate_analyzer(repository: Path) -> dict[str, Any]:
    path = repository / ANALYZER_RELATIVE
    if path.is_symlink() or path.resolve(strict=True) != path:
        raise AnalysisAbortError("v3 analyzer path changed")
    digest, size = _sha256_file(path, "v3 analyzer")
    if digest != EXPECTED_ANALYZER_SHA256 or size != EXPECTED_ANALYZER_BYTES:
        raise AnalysisAbortError("v3 analyzer bytes changed")
    return {
        "path": ANALYZER_RELATIVE.as_posix(),
        "bytes": size,
        "sha256": digest,
    }


def _validate_prior_ledgers(
    repository: Path,
    session_root: Path,
) -> dict[str, dict[str, Any]]:
    plan = _strict_json(session_root / "frozen_plan.json", "frozen_plan.json")
    preflight = _strict_json(session_root / "preflight.json", "preflight.json")
    result: dict[str, dict[str, Any]] = {}
    for name, filename in (
        ("prior_failed_pairs", "pairs.jsonl"),
        ("prior_failed_runs", "runs.jsonl"),
    ):
        path = repository / PRIOR_FAILED_RELATIVE / filename
        if path.is_symlink() or path.resolve(strict=True) != path:
            raise AnalysisAbortError(f"{name} path changed")
        digest, size = _sha256_file(path, name)
        planned = {"path": str(path), "sha256": EMPTY_SHA256}
        if size != 0 or digest != EMPTY_SHA256:
            raise AnalysisAbortError(
                f"{name} must remain the registered empty ledger"
            )
        if plan.get("artifacts", {}).get(name) != planned:
            raise AnalysisAbortError(f"frozen plan {name} binding changed")
        if preflight.get("artifacts", {}).get(name) != {
            **planned,
            "bytes": 0,
        }:
            raise AnalysisAbortError(f"preflight {name} attestation changed")
        result[name] = {
            "path": (PRIOR_FAILED_RELATIVE / filename).as_posix(),
            "bytes": 0,
            "sha256": EMPTY_SHA256,
        }
    return result


def _output_paths(repository: Path) -> tuple[Path, Path, Path]:
    parent = repository / SESSION_RELATIVE.parent
    _require_real_directory(parent, "analysis output directory")
    analysis = repository / ANALYSIS_RELATIVE
    receipt = repository / RECEIPT_RELATIVE
    abort = repository / ABORT_RELATIVE
    _require_absent(analysis, "planned analysis output")
    _require_absent(receipt, "planned fidelity gate receipt")
    _require_absent(abort, "analysis abort output")
    return analysis, receipt, abort


def build_analysis_abort_record(
    repository_root: Path,
) -> tuple[dict[str, Any], Path]:
    """Read and authenticate evidence; return the deterministic record."""

    repository = _repository(repository_root)
    analysis, receipt, abort = _output_paths(repository)
    analyzer = _validate_analyzer(repository)
    session_root = repository / SESSION_RELATIVE
    prior = _validate_prior_ledgers(repository, session_root)
    entries, inventory_sha256 = _inventory(session_root)
    _validate_layout(session_root)
    core = _validate_core(repository, session_root, entries)
    record = {
        "schema_version": SCHEMA_VERSION,
        "amendment_id": AMENDMENT_ID,
        "execution_epoch_id": EXECUTION_EPOCH_ID,
        "cohort_id": COHORT_ID,
        "evidence_commit": EXPECTED_EVIDENCE_COMMIT,
        "sealed_session": {
            "path": SESSION_RELATIVE.as_posix(),
            "file_count": len(entries),
            "attempt_count": EXPECTED_ATTEMPT_COUNT,
            "block_count": EXPECTED_BLOCK_COUNT,
            "symlink_count": 0,
            "inventory_digest_encoding": INVENTORY_DIGEST_ENCODING,
            "inventory_sha256": inventory_sha256,
        },
        "core_artifacts": core,
        "analyzer": analyzer,
        "analysis_failure": {
            "stage": "preflight-artifact-attestation",
            "exception_type": "AnalysisError",
            "message": EXACT_ANALYSIS_ERROR,
            "cli_stderr_line": f"analysis error: {EXACT_ANALYSIS_ERROR}",
            "cause": (
                "the generic nonempty-artifact validator rejected the "
                "registered zero-byte prior failed pairs ledger"
            ),
        },
        "prior_empty_ledgers": prior,
        "planned_outputs": {
            "analysis": {
                "path": analysis.relative_to(repository).as_posix(),
                "absent": True,
            },
            "fidelity_gate_receipt": {
                "path": receipt.relative_to(repository).as_posix(),
                "absent": True,
            },
        },
        "analysis_abort_output": {
            "path": abort.relative_to(repository).as_posix(),
            "exclusive_create_no_overwrite": True,
        },
        "receipt_issued": False,
        "timing_locked": True,
    }
    return record, abort


def _exclusive_write(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o444)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(data)
            handle.flush()
            os.fchmod(handle.fileno(), 0o444)
            os.fsync(handle.fileno())
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            path.unlink()
        except OSError:
            pass
        raise


def record_analysis_abort(repository_root: Path) -> RecordedAnalysisAbort:
    """Exclusively create the exact rerun-1 analysis-abort record."""

    record, output = build_analysis_abort_record(repository_root)
    data = canonical_json_bytes(record)
    _require_absent(
        output.parent / ANALYSIS_RELATIVE.name, "planned analysis output"
    )
    _require_absent(
        output.parent / RECEIPT_RELATIVE.name,
        "planned fidelity gate receipt",
    )
    _exclusive_write(output, data)
    digest, size = _sha256_file(output, "analysis abort output")
    if size != len(data) or digest != hashlib.sha256(data).hexdigest():
        raise AnalysisAbortError("analysis abort changed after creation")
    return RecordedAnalysisAbort(
        output,
        digest,
        str(record["sealed_session"]["inventory_sha256"]),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repository-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    args = parser.parse_args(argv)
    try:
        result = record_analysis_abort(args.repository_root)
    except (AnalysisAbortError, FileExistsError, OSError) as error:
        print(f"analysis abort error: {error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(
        canonical_json_bytes(
            {
                "analysis_abort_path": str(result.path),
                "analysis_abort_sha256": result.sha256,
                "inventory_sha256": result.inventory_sha256,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
