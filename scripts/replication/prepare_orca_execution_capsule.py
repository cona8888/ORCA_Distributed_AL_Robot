#!/usr/bin/env python3
"""Create or check the non-authorizing DANCeRS--ORCA execution capsule.

This tool freezes commands and analysis contracts only.  It has no interface
that can launch a simulator, the ORCA wrapper, or a publication process.  Its
only direct child-process surface is a read-only, argument-firewalled Git query
used to anchor source files.  The delegated ORCA build-capsule checker may also
run its frozen read-only Git, tool-version, and Ninja-query probes; that checker
never invokes the wrapper's ``validate``, ``smoke``, or ``run`` command.

``create`` publishes a new capsule through a same-parent staging directory.
``check`` is read-only and requires the exact manifest SHA-256 from outside the
capsule; ``manifest.sha256`` is checked for consistency but is not a trust root.
Neither command creates a publication-result namespace.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import hashlib
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Iterable, Mapping, Sequence


REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.replication import analyze_orca_comparison as analyzer
from scripts.replication import prepare_orca_build_capsule as orca_build_capsule
from scripts.replication import run_orca_comparison as runner


SCHEMA = "dancers-orca-execution-capsule-v1"
CAPSULE_ID = "dancers-orca-v1-pre-execution-capsule"
COMMAND_TEMPLATES_SCHEMA = "dancers-orca-command-templates-v1"

INPUT_MANIFEST_SHA256 = (
    "3fc8a7572ed6c89eecfd8cdc1f0db2c9b552e63f60117e83bacf0ad24cd153f7"
)
SCHEDULE_SHA256 = (
    "244d268f37a976bf7af8f11ce0bb02430bd72c32372a4bb040e2b586181328c4"
)
ORCA_BUILD_CAPSULE_SHA256 = (
    "a0fa89094f55df970bef6c63a33b4d11791972a0c303b923fa3180769616a428"
)
ORCA_BINARY_SHA256 = (
    "155fc6729a85f8595be4cddecd0cb17f4e58c7a0688ccc789fcabbb3c4f59a9e"
)
DANCERS_BUILD_MANIFEST_SHA256 = (
    "3c9c4305dae5e5154963ec76bb52e0bb826d6fb5b44a89503235057ad449b3cd"
)
LIBOMP_SHA256 = (
    "40618282b8a203af8759f46ef431cb58490d3ab9709edc421d0dc2b9059c98aa"
)
LIBOMP_BYTES = 825_088
EXPECTED_PROCESSES = 363

COMMITTED_DANCERS_MANIFEST_RELATIVE = (
    "results/publication/dancers-multimap-v1-host-1/build_manifest.json"
)
COMMITTED_DANCERS_MANIFEST = (
    REPO / COMMITTED_DANCERS_MANIFEST_RELATIVE
).resolve()
RUNTIME_DANCERS_MANIFEST = (
    REPO / runner.DANCERS_BUILD_MANIFEST_RELATIVE
).resolve()
DEFAULT_ORCA_BUILD_CAPSULE_ROOT = (
    REPO / "comparison/publication/dancers-orca-v1-build-capsule"
).resolve()
DEFAULT_LIBOMP = (REPO / ".pixi/envs/default/lib/libomp.dylib").resolve()
GIT = (REPO / ".pixi/envs/default/bin/git").resolve()
GIT_BYTES = 3_721_648
GIT_MODE = "0755"
GIT_SHA256 = (
    "06067a9ff2f3191a5f37c1b8e924f11810603665dfc957877f01328152f08a23"
)
CHILD_TIMEOUT_SECONDS = 30.0
PAYLOAD_MODE = "0644"
SOURCE_ROOTS_ROLE = (
    "informational host locations; immutable source_endpoints are authoritative"
)

# Constructed from scratch.  In particular, no caller-controlled Git object,
# loader-injection, or trace-output redirection survives into a child.
MINIMAL_CHILD_ENVIRONMENT = {
    "LANG": "C",
    "LC_ALL": "C",
    "PATH": "/usr/bin:/bin",
    "TMPDIR": "/private/tmp/",
    "TZ": "UTC",
}
_CONTROLLED_GIT_ENVIRONMENT = {
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_OPTIONAL_LOCKS": "0",
}

PAYLOAD_PATHS = (
    "analysis_contract.json",
    "command_templates.json",
    "execution_plan.json",
    "result_contract.json",
)

# Every file here must be tracked at the single repository anchor.  Runtime
# artifacts are authenticated separately because build products are not source.
SOURCE_PATHS = (
    "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_V1.md",
    "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_AMENDMENT_1_V1.md",
    "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_AMENDMENT_2_V1.md",
    "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_AMENDMENT_3_V1.md",
    "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_AMENDMENT_7_V1.md",
    "docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_AMENDMENT_8_V1.md",
    "pixi.lock",
    COMMITTED_DANCERS_MANIFEST_RELATIVE,
    "scripts/comparison/trace_compare.py",
    "scripts/replication/analyze_orca_comparison.py",
    "scripts/replication/prepare_orca_build_capsule.py",
    "scripts/replication/prepare_orca_execution_capsule.py",
    "scripts/replication/prepare_orca_inputs.py",
    "scripts/replication/run_orca_comparison.py",
)

AUTHENTICATION_RULE = (
    "check requires an externally supplied exact manifest SHA-256; "
    "manifest.sha256 is consistency evidence, not a trust root"
)
CLASSIFICATION = {
    "execution_authorized": False,
    "publication_processes_executed": 0,
    "publication_simulation_steps": 0,
    "result_namespace": "absent",
    "scope": "prospective commands and result-analysis contracts only",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


class CapsuleError(RuntimeError):
    """A fail-closed execution-capsule error."""


class _DuplicateKey(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class CapsuleInputs:
    input_root: pathlib.Path
    orca_build_capsule_root: pathlib.Path
    dancers_build_manifest: pathlib.Path
    libomp: pathlib.Path
    runtime: runner.RuntimeBindings


def default_inputs() -> CapsuleInputs:
    return CapsuleInputs(
        input_root=runner.DEFAULT_INPUT_ROOT.resolve(),
        orca_build_capsule_root=DEFAULT_ORCA_BUILD_CAPSULE_ROOT,
        dancers_build_manifest=COMMITTED_DANCERS_MANIFEST,
        libomp=DEFAULT_LIBOMP,
        runtime=runner.default_runtime_bindings(),
    )


def canonical_json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value!r}")


def parse_strict_json(raw: bytes, label: str) -> Any:
    try:
        return json.loads(
            raw,
            object_pairs_hook=_no_duplicate_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise CapsuleError(f"{label} is not strict JSON: {error}") from error


def file_identity(
    path: pathlib.Path, *, recorded_path: str | None = None
) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise CapsuleError(f"artifact is not a regular non-symlink file: {path}")
    status = path.stat()
    return {
        "bytes": status.st_size,
        "mode": f"{stat.S_IMODE(status.st_mode):04o}",
        "path": str(path) if recorded_path is None else recorded_path,
        "sha256": sha256_file(path),
    }


def payload_identity(relative: str, raw: bytes) -> dict[str, Any]:
    return {
        "bytes": len(raw),
        "mode": PAYLOAD_MODE,
        "path": relative,
        "sha256": sha256_bytes(raw),
    }


def require_real_absolute(
    path: pathlib.Path, label: str, *, directory: bool
) -> pathlib.Path:
    if not path.is_absolute():
        raise CapsuleError(f"{label} must be absolute")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise CapsuleError(f"{label} does not exist: {error}") from error
    if path != resolved or path.is_symlink():
        raise CapsuleError(f"{label} must be canonical and contain no symlink")
    if directory and not path.is_dir():
        raise CapsuleError(f"{label} is not a directory")
    if not directory and not path.is_file():
        raise CapsuleError(f"{label} is not a regular file")
    return path


def require_absent_absolute(path: pathlib.Path, label: str) -> pathlib.Path:
    if not path.is_absolute():
        raise CapsuleError(f"{label} must be absolute")
    require_real_absolute(path.parent, f"{label} parent", directory=True)
    if path.exists() or path.is_symlink():
        raise CapsuleError(f"{label} already exists")
    return path


def _is_relative_to(path: pathlib.Path, parent: pathlib.Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def require_disjoint(named: Mapping[str, pathlib.Path]) -> None:
    items = list(named.items())
    for index, (left_name, left) in enumerate(items):
        for right_name, right in items[index + 1 :]:
            if _is_relative_to(left, right) or _is_relative_to(right, left):
                raise CapsuleError(f"{left_name} and {right_name} overlap")


def checked_payload_path(root: pathlib.Path, relative: str) -> pathlib.Path:
    parsed = pathlib.PurePosixPath(relative)
    if (
        not relative
        or parsed.is_absolute()
        or parsed.as_posix() != relative
        or any(part in ("", ".", "..") for part in parsed.parts)
    ):
        raise CapsuleError(f"unsafe capsule path: {relative!r}")
    return root.joinpath(*parsed.parts)


def snapshot_tree(root: pathlib.Path) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        current_path = pathlib.Path(current)
        for name in sorted(directories):
            path = current_path / name
            if path.is_symlink():
                raise CapsuleError(f"capsule contains a symlink directory: {path}")
        for name in sorted(filenames):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink() or not path.is_file():
                raise CapsuleError(f"capsule contains a non-regular file: {relative}")
            status = path.stat()
            result.append(
                {
                    "bytes": status.st_size,
                    "mode": f"{stat.S_IMODE(status.st_mode):04o}",
                    "path": relative,
                    "sha256": sha256_file(path),
                }
            )
    return sorted(result, key=lambda item: item["path"])


def directory_inventory(root: pathlib.Path) -> set[str]:
    result: set[str] = set()
    for current, directories, _filenames in os.walk(root, followlinks=False):
        current_path = pathlib.Path(current)
        for name in directories:
            path = current_path / name
            if path.is_symlink():
                raise CapsuleError(f"capsule contains a symlink directory: {path}")
            result.add(path.relative_to(root).as_posix())
    return result


def parent_directories(paths: Iterable[str]) -> set[str]:
    result: set[str] = set()
    for value in paths:
        relative = pathlib.PurePosixPath(value)
        for parent in relative.parents:
            if parent != pathlib.PurePosixPath("."):
                result.add(parent.as_posix())
    return result


def _git_args_allowed(args: Sequence[str]) -> bool:
    if tuple(args) == ("rev-parse", "HEAD"):
        return True
    if (
        len(args) == 2
        and args[0] == "rev-parse"
        and args[1].endswith("^{tree}")
        and GIT_OBJECT_RE.fullmatch(args[1][:-7]) is not None
    ):
        return True
    if len(args) == 2 and args[0] == "show" and ":" in args[1]:
        commit, relative = args[1].split(":", 1)
        parsed = pathlib.PurePosixPath(relative)
        return (
            GIT_OBJECT_RE.fullmatch(commit) is not None
            and relative in SOURCE_PATHS
            and not parsed.is_absolute()
            and all(part not in ("", ".", "..") for part in parsed.parts)
        )
    return False


def authenticate_git_executable() -> dict[str, Any]:
    """Authenticate the sole direct executable before any child is created."""

    path = require_real_absolute(GIT, "Git executable", directory=False)
    identity = file_identity(path)
    if identity != {
        "bytes": GIT_BYTES,
        "mode": GIT_MODE,
        "path": str(GIT),
        "sha256": GIT_SHA256,
    } or not os.access(path, os.X_OK):
        raise CapsuleError("Git executable differs from the exact trust root")
    return identity


def _clean_child_environment(
    requested: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return a fixed child environment, accepting only defensive Git flags."""

    environment = dict(MINIMAL_CHILD_ENVIRONMENT)
    if requested is None:
        return environment
    requested = dict(requested)
    allowed = {**MINIMAL_CHILD_ENVIRONMENT, **_CONTROLLED_GIT_ENVIRONMENT}
    if any(
        key not in allowed or requested[key] != allowed[key]
        for key in requested
    ):
        raise CapsuleError("delegated probe requested a non-clean child environment")
    environment.update(
        {
            key: value
            for key, value in _CONTROLLED_GIT_ENVIRONMENT.items()
            if requested.get(key) == value
        }
    )
    return environment


def _run_delegated_probe(
    argv: Sequence[str],
    *,
    cwd: pathlib.Path | None = None,
    allowed: Iterable[int] = (0,),
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    """Run one frozen build-checker probe through this capsule's boundary."""

    if not argv or any(not isinstance(item, str) for item in argv):
        raise orca_build_capsule.CapsuleError("probe argv is invalid")
    git_before = authenticate_git_executable()
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=_clean_child_environment(env),
            check=False,
            timeout=CHILD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise orca_build_capsule.CapsuleError(
            f"probe timed out after {CHILD_TIMEOUT_SECONDS:g} seconds: {list(argv)!r}"
        ) from error
    if authenticate_git_executable() != git_before:
        raise orca_build_capsule.CapsuleError(
            "Git executable changed while a delegated probe was running"
        )
    if completed.returncode not in set(allowed):
        raise orca_build_capsule.CapsuleError(
            f"probe failed ({completed.returncode}): {list(argv)!r}: "
            f"{completed.stderr.decode('utf-8', 'replace')}"
        )
    return completed


@contextlib.contextmanager
def _delegated_build_checker_boundary() -> Iterable[None]:
    """Make every delegated read-only probe inherit the same clean boundary."""

    previous_environment = dict(os.environ)
    previous_probe = orca_build_capsule.run_probe
    os.environ.clear()
    os.environ.update(MINIMAL_CHILD_ENVIRONMENT)
    orca_build_capsule.run_probe = _run_delegated_probe
    try:
        yield
    finally:
        orca_build_capsule.run_probe = previous_probe
        os.environ.clear()
        os.environ.update(previous_environment)


def _run_git(args: Sequence[str]) -> bytes:
    """Run one of three read-only Git queries; no caller chooses an executable."""

    if not _git_args_allowed(args):
        raise CapsuleError(f"Git argument firewall rejected: {list(args)!r}")
    before = authenticate_git_executable()
    argv = [
        str(GIT),
        "--no-pager",
        "--no-replace-objects",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=/dev/null",
        "-C",
        str(REPO),
        *args,
    ]
    try:
        completed = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            env=_clean_child_environment(),
            check=False,
            timeout=CHILD_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as error:
        raise CapsuleError(
            f"read-only Git query timed out after {CHILD_TIMEOUT_SECONDS:g} seconds"
        ) from error
    if authenticate_git_executable() != before:
        raise CapsuleError("Git executable changed while a query was running")
    if completed.returncode != 0:
        raise CapsuleError(
            "read-only Git query failed: "
            + completed.stderr.decode("utf-8", "replace").strip()
        )
    return completed.stdout


def _git_text(args: Sequence[str]) -> str:
    return _run_git(args).decode("ascii").strip()


def _source_record(relative: str, raw: bytes) -> dict[str, Any]:
    path = REPO.joinpath(*pathlib.PurePosixPath(relative).parts)
    actual = file_identity(path, recorded_path=relative)
    if path.read_bytes() != raw:
        raise CapsuleError(f"source differs from repository anchor: {relative}")
    header = f"blob {len(raw)}\0".encode("ascii")
    return {
        **actual,
        "git_blob_sha1": hashlib.sha1(header + raw).hexdigest(),
    }


def verify_repository_anchor() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    commit = _git_text(("rev-parse", "HEAD"))
    if GIT_OBJECT_RE.fullmatch(commit) is None:
        raise CapsuleError("repository HEAD is not a Git object id")
    tree = _git_text(("rev-parse", f"{commit}^{{tree}}"))
    if GIT_OBJECT_RE.fullmatch(tree) is None:
        raise CapsuleError("repository tree is not a Git object id")
    source_files = [
        _source_record(relative, _run_git(("show", f"{commit}:{relative}")))
        for relative in SOURCE_PATHS
    ]
    return {
        "commit": commit,
        "source_policy": "all bound source bytes equal the named commit",
        "tree": tree,
    }, source_files


def verify_repository_record(
    repository: Mapping[str, Any], expected_sources: Any
) -> list[dict[str, Any]]:
    if set(repository) != {"commit", "source_policy", "tree"}:
        raise CapsuleError("repository anchor field inventory differs")
    commit = repository.get("commit")
    tree = repository.get("tree")
    if (
        not isinstance(commit, str)
        or GIT_OBJECT_RE.fullmatch(commit) is None
        or not isinstance(tree, str)
        or GIT_OBJECT_RE.fullmatch(tree) is None
        or repository.get("source_policy")
        != "all bound source bytes equal the named commit"
    ):
        raise CapsuleError("repository anchor differs")
    if _git_text(("rev-parse", f"{commit}^{{tree}}")) != tree:
        raise CapsuleError("repository tree differs from the named commit")
    actual = [
        _source_record(relative, _run_git(("show", f"{commit}:{relative}")))
        for relative in SOURCE_PATHS
    ]
    if expected_sources != actual:
        raise CapsuleError("source-file identities differ from the repository anchor")
    return actual


def _strict_canonical_file(path: pathlib.Path, label: str) -> Any:
    raw = path.read_bytes()
    value = parse_strict_json(raw, label)
    if raw != canonical_json_bytes(value):
        raise CapsuleError(f"{label} is not canonical JSON")
    return value


def _manifest_binary_path(
    manifest: Mapping[str, Any], method: str, role: str
) -> tuple[pathlib.Path, Mapping[str, Any]]:
    try:
        record = manifest["binaries"][method][role]["binary"]
        path = REPO.joinpath(
            *pathlib.PurePosixPath(str(record["path"])).parts
        ).resolve()
    except (KeyError, TypeError, ValueError) as error:
        raise CapsuleError("DANCeRS build-manifest binary record is malformed") from error
    return path, record


def verify_dancers_build(
    manifest_path: pathlib.Path,
    libomp: pathlib.Path,
    runtime: runner.RuntimeBindings,
) -> dict[str, Any]:
    manifest_path = require_real_absolute(
        manifest_path, "committed DANCeRS build manifest", directory=False
    )
    if manifest_path != COMMITTED_DANCERS_MANIFEST:
        raise CapsuleError("DANCeRS build manifest is not the committed trust-root path")
    if sha256_file(manifest_path) != DANCERS_BUILD_MANIFEST_SHA256:
        raise CapsuleError("DANCeRS build manifest differs from the exact trust root")
    manifest = _strict_canonical_file(manifest_path, "DANCeRS build manifest")
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "dancers-multimap-build-manifest-v1"
        or manifest.get("study_id") != "dancers-multimap-v1"
        or manifest.get("common_patch_sha256") != runner.COMMON_PATCH_SHA256
        or manifest.get("lock_sha256") != runner.PIXI_LOCK_SHA256
        or set(manifest.get("binaries", {})) != {"optimized", "reference"}
    ):
        raise CapsuleError("DANCeRS build manifest top-level contract differs")

    runtime_paths = {
        "optimized_fidelity": runtime.optimized_fidelity_binary,
        "optimized_timing": runtime.optimized_timing_binary,
        "reference_fidelity": runtime.reference_fidelity_binary,
        "reference_timing": runtime.reference_timing_binary,
    }
    binaries: dict[str, dict[str, Any]] = {}
    for method in ("optimized", "reference"):
        method_record = manifest["binaries"][method]
        endpoint = runner.SOURCE_ENDPOINTS[method]
        for key in (
            "algorithm_revision",
            "applied_head",
            "source_archive_sha256",
            "tree",
        ):
            if method_record.get(key) != endpoint[key]:
                raise CapsuleError(f"DANCeRS {method} endpoint differs: {key}")
        expected_source = REPO.joinpath(
            *pathlib.PurePosixPath(str(method_record.get("source_root", ""))).parts
        ).resolve()
        actual_source = (
            runtime.optimized_source_root
            if method == "optimized"
            else runtime.reference_source_root
        )
        if expected_source != actual_source:
            raise CapsuleError(f"DANCeRS {method} source-root binding differs")
        for role in ("fidelity", "timing"):
            key = f"{method}_{role}"
            expected_path, manifest_identity = _manifest_binary_path(
                manifest, method, role
            )
            if expected_path != runtime_paths[key]:
                raise CapsuleError(f"DANCeRS {key} runtime path differs")
            current = file_identity(require_real_absolute(
                runtime_paths[key], f"{key} binary", directory=False
            ))
            expected = runner.EXPECTED_BINARIES[key]
            if (
                current["bytes"] != expected["bytes"]
                or current["sha256"] != expected["sha256"]
                or manifest_identity.get("bytes") != expected["bytes"]
                or manifest_identity.get("sha256") != expected["sha256"]
                or manifest["binaries"][method][role].get("observer")
                is not (role == "fidelity")
            ):
                raise CapsuleError(f"DANCeRS {key} identity differs")
            binaries[key] = current

    libomp = require_real_absolute(libomp, "libomp", directory=False)
    expected_libomp = manifest.get("tools", {}).get("libomp", {})
    declared_libomp = REPO.joinpath(
        *pathlib.PurePosixPath(str(expected_libomp.get("path", ""))).parts
    ).resolve()
    libomp_identity = file_identity(libomp)
    if (
        libomp != DEFAULT_LIBOMP
        or declared_libomp != libomp
        or expected_libomp.get("bytes") != LIBOMP_BYTES
        or expected_libomp.get("sha256") != LIBOMP_SHA256
        or libomp_identity["bytes"] != LIBOMP_BYTES
        or libomp_identity["sha256"] != LIBOMP_SHA256
    ):
        raise CapsuleError("libomp identity differs from the committed build manifest")

    runtime_manifest = require_real_absolute(
        RUNTIME_DANCERS_MANIFEST, "runtime DANCeRS build manifest", directory=False
    )
    if (
        sha256_file(runtime_manifest) != DANCERS_BUILD_MANIFEST_SHA256
        or runtime_manifest.read_bytes() != manifest_path.read_bytes()
    ):
        raise CapsuleError("runtime DANCeRS build manifest differs from committed copy")
    return {
        "binaries": binaries,
        "committed_build_manifest": file_identity(manifest_path),
        "libomp": libomp_identity,
        "runtime_build_manifest": file_identity(runtime_manifest),
        "source_endpoints": runner.SOURCE_ENDPOINTS,
    }


def verify_orca_build(
    capsule_root: pathlib.Path, orca_binary: pathlib.Path
) -> dict[str, Any]:
    capsule_root = require_real_absolute(
        capsule_root, "ORCA build capsule root", directory=True
    )
    git_before = authenticate_git_executable()
    try:
        with _delegated_build_checker_boundary():
            manifest = orca_build_capsule.check_capsule(
                capsule_root, ORCA_BUILD_CAPSULE_SHA256
            )
    except (orca_build_capsule.CapsuleError, OSError, ValueError, KeyError) as error:
        raise CapsuleError(f"ORCA build capsule authentication failed: {error}") from error
    if authenticate_git_executable() != git_before:
        raise CapsuleError("Git executable changed during ORCA build-capsule checking")
    orca_binary = require_real_absolute(orca_binary, "ORCA binary", directory=False)
    binary_identity = file_identity(orca_binary)
    if binary_identity["sha256"] != ORCA_BINARY_SHA256:
        raise CapsuleError("ORCA binary differs from the exact trust root")
    matching_builds: list[int] = []
    for index, build in enumerate(manifest.get("builds", [])):
        try:
            declared = pathlib.Path(build["audit"]["binary"]["path"])
        except (KeyError, TypeError) as error:
            raise CapsuleError("ORCA build-capsule binary record is malformed") from error
        if declared == orca_binary:
            matching_builds.append(index)
            record = build["audit"]["binary"]
            if (
                record.get("bytes") != binary_identity["bytes"]
                or record.get("sha256") != binary_identity["sha256"]
            ):
                raise CapsuleError("selected ORCA binary record differs")
    if len(matching_builds) != 1:
        raise CapsuleError("selected ORCA binary is not one designated capsule build")
    return {
        "binary": binary_identity,
        "build_capsule_manifest": file_identity(capsule_root / "manifest.json"),
        "build_capsule_root": str(capsule_root),
        "external_manifest_sha256": ORCA_BUILD_CAPSULE_SHA256,
        "selected_build_index": matching_builds[0],
    }


def normalize_inputs(inputs: CapsuleInputs) -> CapsuleInputs:
    runtime = inputs.runtime
    runtime_libomp = require_real_absolute(
        runtime.libomp, "runtime libomp", directory=False
    )
    declared_libomp = require_real_absolute(inputs.libomp, "libomp", directory=False)
    if runtime_libomp != declared_libomp:
        raise CapsuleError("runtime and dependency libomp paths differ")
    return CapsuleInputs(
        input_root=require_real_absolute(
            inputs.input_root, "frozen input root", directory=True
        ),
        orca_build_capsule_root=require_real_absolute(
            inputs.orca_build_capsule_root,
            "ORCA build capsule root",
            directory=True,
        ),
        dancers_build_manifest=require_real_absolute(
            inputs.dancers_build_manifest,
            "committed DANCeRS build manifest",
            directory=False,
        ),
        libomp=declared_libomp,
        runtime=runner.RuntimeBindings(
            orca_binary=require_real_absolute(
                runtime.orca_binary, "ORCA binary", directory=False
            ),
            optimized_fidelity_binary=require_real_absolute(
                runtime.optimized_fidelity_binary,
                "optimized fidelity binary",
                directory=False,
            ),
            optimized_timing_binary=require_real_absolute(
                runtime.optimized_timing_binary,
                "optimized timing binary",
                directory=False,
            ),
            reference_fidelity_binary=require_real_absolute(
                runtime.reference_fidelity_binary,
                "reference fidelity binary",
                directory=False,
            ),
            reference_timing_binary=require_real_absolute(
                runtime.reference_timing_binary,
                "reference timing binary",
                directory=False,
            ),
            optimized_source_root=require_real_absolute(
                runtime.optimized_source_root,
                "optimized source root",
                directory=True,
            ),
            reference_source_root=require_real_absolute(
                runtime.reference_source_root,
                "reference source root",
                directory=True,
            ),
            libomp=runtime_libomp,
        ),
    )


def verify_pixi_lock() -> dict[str, Any]:
    path = require_real_absolute(
        REPO / "pixi.lock", "live pixi.lock", directory=False
    )
    identity = file_identity(path, recorded_path="pixi.lock")
    if (
        identity["sha256"] != runner.PIXI_LOCK_SHA256
        or identity["sha256"] != analyzer.FROZEN_PIXI_LOCK_SHA256
    ):
        raise CapsuleError("live pixi.lock differs from the runner/analyzer trust root")
    return identity


def _require_contract_equal(label: str, expected: Any, *actual: Any) -> None:
    if any(value != expected for value in actual):
        raise CapsuleError(f"runner/analyzer cross-contract differs: {label}")


def verify_runner_analyzer_cross_contract(
    plan: Mapping[str, Any],
    templates: Sequence[Mapping[str, Any]],
    result: Mapping[str, Any],
    analysis: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail closed unless every shared runner/analyzer contract agrees."""

    plan_keys = {
        "admission",
        "artifacts",
        "attempts",
        "classification",
        "input",
        "inventory",
        "policy",
        "result_contract",
        "schema_version",
        "study_id",
    }
    template_keys = {
        "argv",
        "binary_role",
        "command_kind",
        "cwd",
        "environment",
        "expected_artifacts_on_success",
        "expects_summary_on_success",
        "expects_trace_on_success",
        "method",
        "phase",
        "run_class",
        "sequence",
        "steps",
        "timeout_seconds",
    }
    result_keys = {
        "attempt",
        "method_outputs_on_success",
        "root_files",
        "seal",
        "session_schema_version",
        "terminal_outcomes",
    }
    analysis_keys = {
        "analysis_schema_version",
        "behavior",
        "bootstrap",
        "common_grid",
        "failure_policy",
        "geometry",
        "integrity",
        "resource",
        "result_contract",
        "schema_version",
        "study_id",
    }
    if (
        set(plan) != plan_keys
        or set(result) != result_keys
        or set(analysis) != analysis_keys
        or any(set(item) != template_keys for item in templates)
    ):
        raise CapsuleError("runner/analyzer cross-contract inventory differs")

    artifacts = plan.get("artifacts")
    plan_input = plan.get("input")
    inventory = plan.get("inventory")
    integrity = analysis.get("integrity")
    analysis_result = analysis.get("result_contract")
    if not all(
        isinstance(value, Mapping)
        for value in (artifacts, plan_input, inventory, integrity, analysis_result)
    ):
        raise CapsuleError("runner/analyzer cross-contract object is malformed")
    expected_plan_artifact_keys = {
        "analyzer",
        "common_patch_sha256",
        "dancers_build_manifest",
        "expected_binaries",
        "expected_libomp",
        "input_manifest_sha256",
        "orca_build_capsule_manifest",
        "orca_build_capsule_manifest_sha256",
        "orca_sequence_sha256",
        "pixi_lock_sha256",
        "protocols",
        "runner",
        "schedule_sha256",
        "source_endpoints",
    }
    if (
        set(artifacts) != expected_plan_artifact_keys
        or set(plan_input)
        != {
            "manifest_bytes",
            "manifest_sha256",
            "root_repository_relative",
            "schedule_bytes",
            "schedule_sha256",
        }
        or set(inventory) != {"methods", "processes", "sequences"}
        or not isinstance(plan.get("result_contract"), Mapping)
        or set(plan["result_contract"]) != {"sha256", "value"}
        or not isinstance(artifacts.get("orca_build_capsule_manifest"), Mapping)
        or not isinstance(artifacts.get("dancers_build_manifest"), Mapping)
        or not isinstance(artifacts.get("expected_libomp"), Mapping)
    ):
        raise CapsuleError("runner plan subfield inventory differs")
    if (
        plan.get("classification")
        != {
            "execution_authorized": False,
            "publication_processes_executed": 0,
            "publication_simulation_steps": 0,
            "read_only_plan": True,
        }
        or plan.get("admission")
        != {
            "admission_manifest_sha256": None,
            "authorization_manifest_sha256": None,
            "execution_authorized": False,
            "rule": (
                "both digests must be supplied and authenticated by a separate "
                "future launcher"
            ),
        }
        or plan.get("policy")
        != {
            "natural_nonzero_retained": True,
            "selective_retry_allowed": False,
            "serial_nonoverlapping": True,
            "timeout_retained": True,
            "timeout_seconds": 300.0,
        }
    ):
        raise CapsuleError("runner prospective execution policy differs")

    trust_roots = {
        "common_patch_sha256": runner.COMMON_PATCH_SHA256,
        "dancers_build_manifest_sha256": DANCERS_BUILD_MANIFEST_SHA256,
        "input_manifest_sha256": INPUT_MANIFEST_SHA256,
        "libomp_sha256": LIBOMP_SHA256,
        "orca_binary_sha256": ORCA_BINARY_SHA256,
        "orca_build_capsule_sha256": ORCA_BUILD_CAPSULE_SHA256,
        "pixi_lock_sha256": runner.PIXI_LOCK_SHA256,
        "schedule_sha256": SCHEDULE_SHA256,
    }
    _require_contract_equal(
        "input manifest trust root",
        trust_roots["input_manifest_sha256"],
        runner.INPUT_MANIFEST_SHA256,
        analyzer.FROZEN_INPUT_MANIFEST_SHA256,
        artifacts.get("input_manifest_sha256"),
        plan_input.get("manifest_sha256"),
        integrity.get("input_manifest_sha256"),
    )
    _require_contract_equal(
        "schedule trust root",
        trust_roots["schedule_sha256"],
        runner.SCHEDULE_SHA256,
        analyzer.FROZEN_SCHEDULE_SHA256,
        artifacts.get("schedule_sha256"),
        plan_input.get("schedule_sha256"),
        integrity.get("schedule_sha256"),
    )
    _require_contract_equal(
        "ORCA build-capsule trust root",
        trust_roots["orca_build_capsule_sha256"],
        runner.ORCA_BUILD_CAPSULE_SHA256,
        analyzer.FROZEN_BUILD_CAPSULE_SHA256,
        artifacts.get("orca_build_capsule_manifest_sha256"),
        artifacts["orca_build_capsule_manifest"].get("sha256"),
    )
    _require_contract_equal(
        "DANCeRS build-manifest trust root",
        trust_roots["dancers_build_manifest_sha256"],
        runner.DANCERS_BUILD_MANIFEST_SHA256,
        analyzer.FROZEN_DANCERS_BUILD_MANIFEST_SHA256,
        artifacts["dancers_build_manifest"].get("sha256"),
    )
    _require_contract_equal(
        "common patch trust root",
        trust_roots["common_patch_sha256"],
        analyzer.FROZEN_COMMON_PATCH_SHA256,
        artifacts.get("common_patch_sha256"),
    )
    _require_contract_equal(
        "pixi.lock trust root",
        trust_roots["pixi_lock_sha256"],
        analyzer.FROZEN_PIXI_LOCK_SHA256,
        artifacts.get("pixi_lock_sha256"),
    )
    _require_contract_equal(
        "ORCA binary trust root",
        trust_roots["orca_binary_sha256"],
        runner.EXPECTED_BINARIES.get("orca", {}).get("sha256"),
        analyzer.FROZEN_ORCA_BINARY_SHA256,
        analyzer.EXPECTED_BINARIES.get("orca", {}).get("sha256"),
        integrity.get("orca_binary_sha256"),
    )
    _require_contract_equal(
        "libomp trust root",
        trust_roots["libomp_sha256"],
        runner.EXPECTED_LIBOMP.get("sha256"),
        analyzer.FROZEN_LIBOMP_SHA256,
        analyzer.EXPECTED_LIBOMP.get("sha256"),
        artifacts["expected_libomp"].get("sha256"),
    )
    _require_contract_equal("libomp identity", runner.EXPECTED_LIBOMP, analyzer.EXPECTED_LIBOMP, artifacts.get("expected_libomp"))
    _require_contract_equal("libomp byte length", LIBOMP_BYTES, runner.EXPECTED_LIBOMP.get("bytes"))

    _require_contract_equal(
        "all executable identities",
        runner.EXPECTED_BINARIES,
        analyzer.EXPECTED_BINARIES,
        artifacts.get("expected_binaries"),
    )
    _require_contract_equal(
        "source endpoints",
        runner.SOURCE_ENDPOINTS,
        analyzer.SOURCE_ENDPOINTS,
        artifacts.get("source_endpoints"),
    )
    _require_contract_equal(
        "ORCA source endpoint", runner.ORCA_SOURCE, analyzer.ORCA_SOURCE
    )
    _require_contract_equal(
        "algorithm revisions",
        runner.ALGORITHM_REVISIONS,
        analyzer.ALGORITHM_REVISIONS,
    )
    _require_contract_equal(
        "empty-map sentinel",
        runner.NO_MAP_SHA256,
        analyzer.EMPTY_SHA256,
        integrity.get("dancers_no_map_sha256"),
    )
    _require_contract_equal(
        "DANCeRS summary projection",
        list(analyzer.SUMMARY_PARITY_FIELDS),
        integrity.get("dancers_summary_projection"),
    )
    _require_contract_equal(
        "DANCeRS summary projection fields",
        set(runner.DANCERS_SUMMARY_PROJECTION_FIELDS),
        set(analyzer.SUMMARY_PARITY_FIELDS),
    )

    schemas = {
        "analysis": analyzer.ANALYSIS_SCHEMA,
        "analysis_contract": "dancers-orca-analysis-contract-v1",
        "attempt": runner.ATTEMPT_SCHEMA,
        "command_templates": COMMAND_TEMPLATES_SCHEMA,
        "execution_plan": runner.PLAN_SCHEMA,
        "session": runner.SESSION_SCHEMA,
    }
    _require_contract_equal("study id", runner.STUDY_ID, analyzer.STUDY_ID, plan.get("study_id"), analysis.get("study_id"))
    _require_contract_equal("plan schema", schemas["execution_plan"], analyzer.PLAN_SCHEMA, plan.get("schema_version"), analysis_result.get("plan_schema_version"))
    _require_contract_equal("attempt schema", schemas["attempt"], analyzer.ATTEMPT_SCHEMA, result.get("attempt", {}).get("schema_version"), analysis_result.get("attempt_schema_version"))
    _require_contract_equal("session schema", schemas["session"], analyzer.SESSION_SCHEMA, result.get("session_schema_version"), analysis_result.get("session_schema_version"))
    _require_contract_equal("analysis schema", schemas["analysis"], analysis.get("analysis_schema_version"))
    _require_contract_equal("analysis-contract schema", schemas["analysis_contract"], analysis.get("schema_version"))

    expected_methods = {"optimized": 119, "orca": 125, "reference": 119}
    actual_methods = {key: 0 for key in expected_methods}
    for item in templates:
        method = item.get("method")
        if method not in actual_methods:
            raise CapsuleError("runner/analyzer cross-contract method inventory differs")
        actual_methods[method] += 1
    process_inventory = {
        "methods": expected_methods,
        "processes": EXPECTED_PROCESSES,
        "sequences": [0, EXPECTED_PROCESSES - 1],
    }
    _require_contract_equal("process count", EXPECTED_PROCESSES, runner.EXPECTED_PROCESSES, analyzer.EXPECTED_PROCESSES, len(templates), integrity.get("process_inventory"), inventory.get("processes"), result.get("seal", {}).get("records"))
    _require_contract_equal("method counts", expected_methods, actual_methods, inventory.get("methods"))
    _require_contract_equal("sequence bounds", process_inventory["sequences"], inventory.get("sequences"))
    _require_contract_equal("sequence inventory", list(range(EXPECTED_PROCESSES)), [item.get("sequence") for item in templates])

    expected_result_keys = {
        "artifact_filename_keys",
        "artifact_identity_exact_keys",
        "attempt_schema_version",
        "command_exact_keys",
        "measurement_exact_keys",
        "plan_schema_version",
        "record_artifact_failure_extra_key",
        "record_base_exact_keys",
        "runtime_binding_exact_keys",
        "session_exact_keys",
        "session_schema_version",
        "terminal_outcomes",
    }
    if set(analysis_result) != expected_result_keys:
        raise CapsuleError("analyzer result-contract field inventory differs")
    attempt_contract = result.get("attempt")
    seal_contract = result.get("seal")
    if (
        not isinstance(attempt_contract, Mapping)
        or set(attempt_contract)
        != {
            "directory",
            "required_files",
            "schema_version",
            "staging_directory",
            "transaction",
            "validation_error_rule",
        }
        or not isinstance(seal_contract, Mapping)
        or set(seal_contract)
        != {
            "create_last",
            "records",
            "records_rule",
            "resume_allowed",
            "selective_retry_allowed",
        }
    ):
        raise CapsuleError("runner result-contract subfield inventory differs")
    _require_contract_equal(
        "result root-file inventory",
        [
            "input_manifest.json",
            "plan.json",
            "records.ndjson",
            "schedule.json",
            "session.json",
        ],
        result.get("root_files"),
    )
    _require_contract_equal(
        "attempt required-file inventory",
        ["record.json", "stderr.txt", "stdout.txt"],
        attempt_contract.get("required_files"),
    )
    if (
        seal_contract.get("create_last") != "session.json"
        or seal_contract.get("resume_allowed") is not False
        or seal_contract.get("selective_retry_allowed") is not False
    ):
        raise CapsuleError("runner result sealing policy differs")
    required_files = result.get("attempt", {}).get("required_files")
    method_outputs = result.get("method_outputs_on_success")
    expected_method_outputs = {
        "dancers_fidelity": ["provenance.json", "summary.json", "trace.ndjson"],
        "dancers_timing": ["provenance.json"],
        "orca_positions": ["summary.json", "trace.ndjson"],
        "orca_timing": ["summary.json"],
    }
    if (
        not isinstance(required_files, list)
        or not isinstance(method_outputs, Mapping)
        or method_outputs != expected_method_outputs
    ):
        raise CapsuleError("runner result artifact inventory differs")
    artifact_names = sorted(
        (
            set(required_files)
            | {
                name
                for names in method_outputs.values()
                if isinstance(names, list)
                for name in names
            }
        )
        - {"record.json"}
    )
    template_artifacts = sorted(
        {
            name
            for item in templates
            for name in item.get("expected_artifacts_on_success", [])
        }
    )
    _require_contract_equal("artifact filename inventory", artifact_names, template_artifacts, analysis_result.get("artifact_filename_keys"))
    _require_contract_equal("artifact identity keys", ["bytes", "mode", "path", "sha256"], analysis_result.get("artifact_identity_exact_keys"))
    _require_contract_equal("command keys", ["argv", "cwd", "environment", "shell"], analysis_result.get("command_exact_keys"))
    _require_contract_equal(
        "measurement keys",
        [
            "peak_rss_mib",
            "peak_rss_raw",
            "peak_rss_raw_unit",
            "returncode",
            "signal",
            "system_cpu_seconds",
            "timed_out",
            "total_cpu_seconds",
            "user_cpu_seconds",
            "wall_seconds",
        ],
        analysis_result.get("measurement_exact_keys"),
    )
    _require_contract_equal(
        "record base keys",
        [
            "artifacts",
            "binary",
            "command",
            "fixed_work_verified",
            "measurement",
            "outcome",
            "runtime_binding",
            "schedule",
            "schema_version",
            "started_unix_ns",
            "study_id",
        ],
        analysis_result.get("record_base_exact_keys"),
    )
    _require_contract_equal(
        "artifact-failure record key",
        "validation_error",
        analysis_result.get("record_artifact_failure_extra_key"),
    )
    _require_contract_equal("runtime-binding keys", ["binaries", "build_manifest", "libomp", "source_endpoints", "source_roots"], analysis_result.get("runtime_binding_exact_keys"))
    _require_contract_equal(
        "session keys",
        [
            "admission_manifest_sha256",
            "attempt_records",
            "authorization_manifest_sha256",
            "completed_processes",
            "execution_complete",
            "expected_processes",
            "input_manifest",
            "outcome_counts",
            "plan",
            "records",
            "runtime_binding",
            "schedule",
            "schema_version",
            "study_id",
        ],
        analysis_result.get("session_exact_keys"),
    )
    terminal_outcomes = ["artifact_failure", "completed", "nonzero_exit", "timeout"]
    _require_contract_equal("terminal outcomes", terminal_outcomes, result.get("terminal_outcomes"), analysis_result.get("terminal_outcomes"))
    _require_contract_equal("embedded result contract", result, plan.get("result_contract", {}).get("value"))
    _require_contract_equal(
        "embedded result-contract digest",
        sha256_bytes(canonical_json_bytes(result)),
        plan.get("result_contract", {}).get("sha256"),
    )

    return {
        "artifact_filename_inventory": artifact_names,
        "binaries": runner.EXPECTED_BINARIES,
        "process_inventory": process_inventory,
        "result_contract_sha256": sha256_bytes(canonical_json_bytes(result)),
        "runtime_binding_keys": analysis_result["runtime_binding_exact_keys"],
        "schemas": schemas,
        "source_endpoints": runner.SOURCE_ENDPOINTS,
        "source_roots_role": SOURCE_ROOTS_ROLE,
        "terminal_outcomes": terminal_outcomes,
        "trust_roots": trust_roots,
    }


def build_payloads(input_root: pathlib.Path) -> tuple[dict[str, bytes], dict[str, Any]]:
    """Build the four pure JSON payloads without launching any child process."""

    _manifest, attempts, _snapshot = runner.load_frozen_plan(input_root)
    plan = runner.plan_document(input_root)
    templates = [runner.command_template(row) for row in attempts]
    result = runner.result_contract()
    analysis = analyzer.analysis_contract()
    cross_contract = verify_runner_analyzer_cross_contract(
        plan, templates, result, analysis
    )
    if (
        len(templates) != EXPECTED_PROCESSES
        or [item.get("sequence") for item in templates]
        != list(range(EXPECTED_PROCESSES))
        or plan.get("attempts") != templates
        or plan.get("result_contract", {}).get("value") != result
        or plan.get("result_contract", {}).get("sha256")
        != sha256_bytes(canonical_json_bytes(result))
        or plan.get("classification", {}).get("execution_authorized") is not False
        or analysis.get("integrity", {}).get("input_manifest_sha256")
        != INPUT_MANIFEST_SHA256
        or analysis.get("integrity", {}).get("schedule_sha256")
        != SCHEDULE_SHA256
        or analysis.get("integrity", {}).get("orca_binary_sha256")
        != ORCA_BINARY_SHA256
        or analysis.get("result_contract", {}).get("session_schema_version")
        != result.get("session_schema_version")
        or not cross_contract
    ):
        raise CapsuleError("runner/analyzer pure contract APIs disagree")
    command_document = {
        "count": EXPECTED_PROCESSES,
        "schema_version": COMMAND_TEMPLATES_SCHEMA,
        "study_id": runner.STUDY_ID,
        "templates": templates,
    }
    values = {
        "analysis_contract.json": analysis,
        "command_templates.json": command_document,
        "execution_plan.json": plan,
        "result_contract.json": result,
    }
    payloads = {
        relative: canonical_json_bytes(values[relative])
        for relative in PAYLOAD_PATHS
    }
    return payloads, values


def collect_current(
    inputs: CapsuleInputs,
    repository: Mapping[str, Any],
    source_files: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, bytes], dict[str, Any]]:
    inputs = normalize_inputs(inputs)
    if inputs.dancers_build_manifest != COMMITTED_DANCERS_MANIFEST:
        raise CapsuleError("committed DANCeRS manifest path differs")
    if runner.INPUT_MANIFEST_SHA256 != INPUT_MANIFEST_SHA256:
        raise CapsuleError("runner input trust root differs")
    if runner.SCHEDULE_SHA256 != SCHEDULE_SHA256:
        raise CapsuleError("runner schedule trust root differs")
    if runner.ORCA_BUILD_CAPSULE_SHA256 != ORCA_BUILD_CAPSULE_SHA256:
        raise CapsuleError("runner ORCA-capsule trust root differs")
    if runner.DANCERS_BUILD_MANIFEST_SHA256 != DANCERS_BUILD_MANIFEST_SHA256:
        raise CapsuleError("runner DANCeRS-manifest trust root differs")
    pixi_lock_record = verify_pixi_lock()
    git_record = authenticate_git_executable()

    try:
        _input_manifest, attempts, input_snapshot = runner.load_frozen_plan(
            inputs.input_root
        )
        runtime_record = runner.validate_runtime_bindings(
            inputs.runtime, inputs.input_root
        )
    except (runner.RunnerError, OSError, ValueError, KeyError) as error:
        raise CapsuleError(f"runner dependency authentication failed: {error}") from error
    if len(attempts) != EXPECTED_PROCESSES:
        raise CapsuleError("frozen attempt count differs")

    dancers_record = verify_dancers_build(
        inputs.dancers_build_manifest, inputs.libomp, inputs.runtime
    )
    orca_record = verify_orca_build(
        inputs.orca_build_capsule_root, inputs.runtime.orca_binary
    )
    if runtime_record.get("source_endpoints") != runner.SOURCE_ENDPOINTS:
        raise CapsuleError("runner source endpoints differ")
    if runtime_record.get("binaries", {}).get("orca", {}).get("sha256") != ORCA_BINARY_SHA256:
        raise CapsuleError("runner ORCA binary record differs")

    payloads, values = build_payloads(inputs.input_root)
    cross_contract = verify_runner_analyzer_cross_contract(
        values["execution_plan.json"],
        values["command_templates.json"]["templates"],
        values["result_contract.json"],
        values["analysis_contract.json"],
    )
    files = [payload_identity(relative, payloads[relative]) for relative in PAYLOAD_PATHS]
    input_record = {
        "file_count": len(input_snapshot),
        "manifest": file_identity(inputs.input_root / "manifest.json"),
        "root": str(inputs.input_root),
        "schedule": file_identity(inputs.input_root / "schedule.json"),
        "snapshot": input_snapshot,
    }
    if (
        input_record["manifest"]["sha256"] != INPUT_MANIFEST_SHA256
        or input_record["schedule"]["sha256"] != SCHEDULE_SHA256
    ):
        raise CapsuleError("frozen input hashes differ")

    manifest = {
        "authentication": {"rule": AUTHENTICATION_RULE},
        "capsule_id": CAPSULE_ID,
        "classification": CLASSIFICATION,
        "contracts": {
            "analysis_schema_version": values["analysis_contract.json"].get(
                "schema_version"
            ),
            "command_templates": EXPECTED_PROCESSES,
            "cross_contract": cross_contract,
            "cross_contract_sha256": sha256_bytes(
                canonical_json_bytes(cross_contract)
            ),
            "execution_plan_schema_version": values["execution_plan.json"].get(
                "schema_version"
            ),
            "result_session_schema_version": values["result_contract.json"].get(
                "session_schema_version"
            ),
        },
        "dependencies": {
            "dancers": dancers_record,
            "git": git_record,
            "orca": orca_record,
            "pixi_lock": pixi_lock_record,
        },
        "files": files,
        "input": input_record,
        "repository": dict(repository),
        "runtime": {
            "binaries": runtime_record["binaries"],
            "libomp": runtime_record["libomp"],
            "source_endpoints": runtime_record["source_endpoints"],
            "source_roots": runtime_record["source_roots"],
            "source_roots_role": cross_contract["source_roots_role"],
        },
        "schema_version": SCHEMA,
        "source_files": list(source_files),
        "study_id": runner.STUDY_ID,
    }
    return payloads, manifest


def _write_exclusive(path: pathlib.Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        os.fchmod(handle.fileno(), int(PAYLOAD_MODE, 8))
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())


def publish_capsule(
    output: pathlib.Path,
    payloads: Mapping[str, bytes],
    manifest: Mapping[str, Any],
    *,
    validator: Any,
) -> str:
    """Publish create-exclusively; the trust-sidecar child moves last."""

    output = require_absent_absolute(output, "output root")
    if tuple(sorted(payloads)) != PAYLOAD_PATHS:
        raise CapsuleError("payload path inventory differs")
    stage = pathlib.Path(
        tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent)
    )
    try:
        for relative in PAYLOAD_PATHS:
            _write_exclusive(checked_payload_path(stage, relative), payloads[relative])
        manifest_raw = canonical_json_bytes(manifest)
        digest = sha256_bytes(manifest_raw)
        _write_exclusive(stage / "manifest.json", manifest_raw)
        # Written after every other staged byte and moved to the final tree last.
        _write_exclusive(
            stage / "manifest.sha256",
            f"{digest}  manifest.json\n".encode("ascii"),
        )
        validator(stage, digest)
        output.mkdir(mode=0o755, exist_ok=False)
        moved: list[pathlib.Path] = []
        try:
            children = sorted(
                (
                    child
                    for child in stage.iterdir()
                    if child.name != "manifest.sha256"
                ),
                key=lambda child: child.name,
            )
            children.append(stage / "manifest.sha256")
            for child in children:
                destination = output / child.name
                child.rename(destination)
                moved.append(destination)
            stage.rmdir()
        except BaseException:
            for destination in reversed(moved):
                if destination.is_dir() and not destination.is_symlink():
                    shutil.rmtree(destination)
                elif destination.exists() or destination.is_symlink():
                    destination.unlink()
            if output.exists():
                output.rmdir()
            raise
        return digest
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def load_capsule_files(
    root: pathlib.Path, expected_digest: str
) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Authenticate the immutable file tree before interpreting embedded paths."""

    root = require_real_absolute(root, "execution capsule root", directory=True)
    if SHA256_RE.fullmatch(expected_digest) is None:
        raise CapsuleError("external manifest SHA-256 is invalid")
    manifest_path = root / "manifest.json"
    sidecar_path = root / "manifest.sha256"
    if (
        manifest_path.is_symlink()
        or not manifest_path.is_file()
        or sidecar_path.is_symlink()
        or not sidecar_path.is_file()
    ):
        raise CapsuleError("capsule manifest or sidecar is not a regular file")
    raw = manifest_path.read_bytes()
    if sha256_bytes(raw) != expected_digest:
        raise CapsuleError("capsule manifest differs from the external trust root")
    if sidecar_path.read_bytes() != f"{expected_digest}  manifest.json\n".encode(
        "ascii"
    ):
        raise CapsuleError("capsule sidecar differs")
    manifest = parse_strict_json(raw, "execution capsule manifest")
    if not isinstance(manifest, dict) or raw != canonical_json_bytes(manifest):
        raise CapsuleError("execution capsule manifest is not canonical JSON")
    if manifest.get("schema_version") != SCHEMA or manifest.get("capsule_id") != CAPSULE_ID:
        raise CapsuleError("execution capsule schema or id differs")
    files = manifest.get("files")
    if (
        not isinstance(files, list)
        or tuple(item.get("path") for item in files if isinstance(item, dict))
        != PAYLOAD_PATHS
        or any(
            not isinstance(item, dict)
            or set(item) != {"bytes", "mode", "path", "sha256"}
            for item in files
        )
    ):
        raise CapsuleError("capsule payload inventory differs")
    payloads: dict[str, bytes] = {}
    expected_paths = {"manifest.json", "manifest.sha256", *PAYLOAD_PATHS}
    for item in files:
        relative = item["path"]
        path = checked_payload_path(root, relative)
        if path.is_symlink() or not path.is_file():
            raise CapsuleError(f"capsule payload is absent or unsafe: {relative}")
        payload_raw = path.read_bytes()
        actual_mode = f"{stat.S_IMODE(path.stat().st_mode):04o}"
        if payload_identity(relative, payload_raw) != item or actual_mode != PAYLOAD_MODE:
            raise CapsuleError(f"capsule payload differs: {relative}")
        value = parse_strict_json(payload_raw, f"capsule payload {relative}")
        if payload_raw != canonical_json_bytes(value):
            raise CapsuleError(f"capsule payload is not canonical JSON: {relative}")
        payloads[relative] = payload_raw
    actual_paths = {item["path"] for item in snapshot_tree(root)}
    if actual_paths != expected_paths:
        raise CapsuleError("capsule tree has missing or extra files")
    if directory_inventory(root) != parent_directories(expected_paths):
        raise CapsuleError("capsule tree has missing or extra directories")
    return manifest, payloads


def _inputs_from_manifest(manifest: Mapping[str, Any]) -> CapsuleInputs:
    try:
        runtime = manifest["runtime"]
        binaries = runtime["binaries"]
        roots = runtime["source_roots"]
        dependencies = manifest["dependencies"]
        if (
            set(runtime)
            != {
                "binaries",
                "libomp",
                "source_endpoints",
                "source_roots",
                "source_roots_role",
            }
            or runtime["source_endpoints"] != runner.SOURCE_ENDPOINTS
            or runtime["source_roots_role"] != SOURCE_ROOTS_ROLE
            or runtime["libomp"] != dependencies["dancers"]["libomp"]
        ):
            raise CapsuleError("embedded runtime contract differs")
        return CapsuleInputs(
            input_root=pathlib.Path(manifest["input"]["root"]),
            orca_build_capsule_root=pathlib.Path(
                dependencies["orca"]["build_capsule_root"]
            ),
            dancers_build_manifest=pathlib.Path(
                dependencies["dancers"]["committed_build_manifest"]["path"]
            ),
            libomp=pathlib.Path(dependencies["dancers"]["libomp"]["path"]),
            runtime=runner.RuntimeBindings(
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
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise CapsuleError("embedded runtime paths are malformed") from error


def check_capsule(root: pathlib.Path, expected_digest: str) -> dict[str, Any]:
    """Read-only, externally authenticated verification of the whole capsule."""

    root = require_real_absolute(root, "execution capsule root", directory=True)
    sealed_before = snapshot_tree(root)
    manifest, payloads = load_capsule_files(root, expected_digest)
    expected_top_level = {
        "authentication",
        "capsule_id",
        "classification",
        "contracts",
        "dependencies",
        "files",
        "input",
        "repository",
        "runtime",
        "schema_version",
        "source_files",
        "study_id",
    }
    if set(manifest) != expected_top_level:
        raise CapsuleError("execution capsule top-level inventory differs")
    if manifest.get("authentication") != {"rule": AUTHENTICATION_RULE}:
        raise CapsuleError("execution capsule authentication rule differs")
    if manifest.get("classification") != CLASSIFICATION:
        raise CapsuleError("execution capsule classification differs")
    if manifest.get("study_id") != runner.STUDY_ID:
        raise CapsuleError("execution capsule study id differs")
    verify_repository_record(
        manifest.get("repository", {}), manifest.get("source_files")
    )
    inputs = _inputs_from_manifest(manifest)
    current_payloads, current_manifest = collect_current(
        inputs,
        manifest["repository"],
        manifest["source_files"],
    )
    if manifest != current_manifest:
        raise CapsuleError("current authenticated dependencies differ from the capsule")
    if payloads != current_payloads:
        raise CapsuleError("current prospective contracts differ from the capsule")
    if snapshot_tree(root) != sealed_before:
        raise CapsuleError("execution capsule mutated while it was being checked")
    return manifest


def create_capsule(inputs: CapsuleInputs, output: pathlib.Path) -> str:
    inputs = normalize_inputs(inputs)
    output = require_absent_absolute(output, "output root")
    require_disjoint(
        {
            "output": output,
            "input": inputs.input_root,
            "ORCA build capsule": inputs.orca_build_capsule_root,
            "optimized source": inputs.runtime.optimized_source_root,
            "reference source": inputs.runtime.reference_source_root,
        }
    )
    repository, source_files = verify_repository_anchor()
    payloads, manifest = collect_current(inputs, repository, source_files)
    return publish_capsule(
        output, payloads, manifest, validator=check_capsule
    )


def _runtime_from_args(args: argparse.Namespace) -> runner.RuntimeBindings:
    return runner.RuntimeBindings(
        orca_binary=args.orca_binary,
        optimized_fidelity_binary=args.optimized_fidelity_binary,
        optimized_timing_binary=args.optimized_timing_binary,
        reference_fidelity_binary=args.reference_fidelity_binary,
        reference_timing_binary=args.reference_timing_binary,
        optimized_source_root=args.optimized_source_root,
        reference_source_root=args.reference_source_root,
        libomp=args.libomp,
    )


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    commands = value.add_subparsers(dest="command", required=True)
    create = commands.add_parser(
        "create", help="create a prospective non-authorizing capsule"
    )
    defaults = default_inputs()
    create.add_argument("--input-root", type=pathlib.Path, default=defaults.input_root)
    create.add_argument(
        "--orca-build-capsule-root",
        type=pathlib.Path,
        default=defaults.orca_build_capsule_root,
    )
    create.add_argument(
        "--dancers-build-manifest",
        type=pathlib.Path,
        default=defaults.dancers_build_manifest,
    )
    create.add_argument("--libomp", type=pathlib.Path, default=defaults.libomp)
    create.add_argument(
        "--orca-binary", type=pathlib.Path, default=defaults.runtime.orca_binary
    )
    for method in ("optimized", "reference"):
        for role in ("fidelity", "timing"):
            key = f"{method}_{role}"
            create.add_argument(
                f"--{method}-{role}-binary",
                type=pathlib.Path,
                default=getattr(defaults.runtime, f"{key}_binary"),
            )
        create.add_argument(
            f"--{method}-source-root",
            type=pathlib.Path,
            default=getattr(defaults.runtime, f"{method}_source_root"),
        )
    create.add_argument("--output-root", type=pathlib.Path, required=True)
    check = commands.add_parser("check", help="read-only capsule verification")
    check.add_argument("--capsule-root", type=pathlib.Path, required=True)
    check.add_argument("--expected-manifest-sha256", required=True)
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "create":
            inputs = CapsuleInputs(
                input_root=args.input_root,
                orca_build_capsule_root=args.orca_build_capsule_root,
                dancers_build_manifest=args.dancers_build_manifest,
                libomp=args.libomp,
                runtime=_runtime_from_args(args),
            )
            digest = create_capsule(inputs, args.output_root)
            print(f"created non-authorizing execution capsule: {args.output_root}")
            print(f"manifest sha256: {digest}")
        else:
            check_capsule(args.capsule_root, args.expected_manifest_sha256)
            print(f"execution capsule is valid: {args.capsule_root}")
        return 0
    except (
        CapsuleError,
        analyzer.AnalysisError,
        orca_build_capsule.CapsuleError,
        runner.RunnerError,
        OSError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        print(f"execution capsule failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
