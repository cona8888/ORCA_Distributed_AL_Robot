from __future__ import annotations

import contextlib
import copy
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import py_compile
import shutil
import stat
import sys
import tarfile
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "scripts/comparison/verify_offline_build_inputs_v2.py"
SPEC = importlib.util.spec_from_file_location("verify_offline_build_inputs_v2", MODULE_PATH)
assert SPEC and SPEC.loader
VERIFY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VERIFY
SPEC.loader.exec_module(VERIFY)


def _write(path: Path, payload: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.chmod(mode)


def _copy(relative: str, destination: Path) -> None:
    source = REPO_ROOT / relative
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(stat.S_IMODE(source.stat().st_mode))


def _identity(root: Path, relative: str) -> dict[str, object]:
    path = root / relative
    payload = path.read_bytes()
    return {
        "bytes": len(payload),
        "mode": stat.S_IMODE(path.stat().st_mode),
        "path": relative,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _tar_member(name: str, kind: str, payload: bytes = b"", mode: int = 0o664) -> tuple[tarfile.TarInfo, io.BytesIO | None]:
    info = tarfile.TarInfo(name)
    info.uid = 0
    info.gid = 0
    info.uname = "root"
    info.gname = "root"
    info.mtime = 1_629_319_318
    info.mode = mode
    if kind == "directory":
        info.type = tarfile.DIRTYPE
        info.size = 0
        return info, None
    if kind == "file":
        info.type = tarfile.REGTYPE
        info.size = len(payload)
        return info, io.BytesIO(payload)
    if kind == "symlink":
        info.type = tarfile.SYMTYPE
        info.linkname = "../outside"
        info.size = 0
        return info, None
    raise AssertionError(kind)


def _make_eigen_archive(
    path: Path,
    *,
    selected: dict[str, bytes],
    licenses: dict[str, bytes],
    extra: list[tuple[str, str, bytes, int]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    members: list[tuple[str, str, bytes, int]] = [
        (VERIFY.EIGEN_ROOT, "directory", b"", 0o775),
        (f"{VERIFY.EIGEN_ROOT}/Eigen", "directory", b"", 0o775),
    ]
    directories: set[str] = set()
    for relative in selected:
        parent = PurePosixPath(relative).parent
        while parent.as_posix() not in ("", "."):
            directories.add(parent.as_posix())
            parent = parent.parent
    for directory in sorted(directories, key=lambda value: (value.count("/"), value)):
        members.append((f"{VERIFY.EIGEN_ROOT}/Eigen/{directory}", "directory", b"", 0o775))
    members.extend(
        (
            f"{VERIFY.EIGEN_ROOT}/Eigen/{relative}",
            "file",
            payload,
            0o775 if relative == "src/helper.h" else 0o664,
        )
        for relative, payload in sorted(selected.items())
    )
    members.extend(
        (f"{VERIFY.EIGEN_ROOT}/{name}", "file", payload, 0o664)
        for name, payload in licenses.items()
    )
    members.extend(extra or [])
    with tarfile.open(
        path,
        "w:gz",
        format=tarfile.PAX_FORMAT,
        pax_headers={"comment": VERIFY.EIGEN_COMMIT},
    ) as archive:
        for name, kind, payload, mode in members:
            info, stream = _tar_member(name, kind, payload, mode)
            archive.addfile(info, stream)


def _archive_facts(path: Path) -> dict[str, object]:
    entries: list[tuple[str, bytes]] = []
    with tarfile.open(path, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            if member.isfile():
                stream = archive.extractfile(member)
                assert stream is not None
                entries.append(
                    (
                        str(PurePosixPath(member.name).relative_to(VERIFY.EIGEN_ROOT)),
                        stream.read(),
                    )
                )
    return {
        "directory_count": sum(item.isdir() for item in members),
        "entries": entries,
        "member_count": len(members),
        "regular_file_count": sum(item.isfile() for item in members),
        "regular_bytes": sum(len(payload) for _, payload in entries),
        "tar_stream_bytes": len(gzip.decompress(path.read_bytes())),
    }


def _refresh_eigen_archive(root: Path, manifest: dict[str, object]) -> None:
    record = manifest["vendored_eigen"]["upstream_archive"]  # type: ignore[index]
    relative = record["archive"]["path"]  # type: ignore[index]
    facts = _archive_facts(root / relative)
    record["archive"] = _identity(root, relative)  # type: ignore[index]
    record["directory_count"] = facts["directory_count"]  # type: ignore[index]
    record["member_count"] = facts["member_count"]  # type: ignore[index]
    record["regular_file_count"] = facts["regular_file_count"]  # type: ignore[index]
    record["tar_stream_bytes"] = facts["tar_stream_bytes"]  # type: ignore[index]
    record["uncompressed_regular_bytes"] = facts["regular_bytes"]  # type: ignore[index]
    record["full_tree"] = {  # type: ignore[index]
        "regular_bytes": facts["regular_bytes"],
        "regular_file_count": facts["regular_file_count"],
        "sha256": VERIFY.V1.regular_tree_digest(facts["entries"]),
    }


def _save_manifest(root: Path, manifest: dict[str, object]) -> str:
    raw = VERIFY.canonical_json_bytes(manifest)
    _write(root / VERIFY.DEFAULT_MANIFEST, raw)
    return hashlib.sha256(raw).hexdigest()


def _fixture(root: Path) -> tuple[dict[str, object], str]:
    for relative in (
        VERIFY.V1_MANIFEST_PATH,
        VERIFY.V1_VERIFIER_PATH,
        "thirdparty/dist/manif-0.0.5-build-source.tar.gz",
        "thirdparty/dist/raylib-5.5-build-source.tar.gz",
        "CMakeLists.txt",
    ):
        _copy(relative, root)

    selected = {
        "Core": b"synthetic Eigen core\n",
        "src/helper.h": b"synthetic Eigen helper\n",
    }
    licenses = {name: f"license {name}\n".encode("ascii") for name in VERIFY.EIGEN_LICENSES}
    archive_relative = "thirdparty/dist/eigen-synthetic.tar.gz"
    _make_eigen_archive(
        root / archive_relative,
        selected=selected,
        licenses=licenses,
    )
    for relative, payload in selected.items():
        _write(
            root / "thirdparty/Eigen" / relative,
            payload,
            0o755 if relative == "src/helper.h" else 0o644,
        )
    for name, payload in licenses.items():
        _write(root / "thirdparty" / name, payload)
    _write(root / "thirdparty/Eigen3Config.cmake", b"set(Eigen3_FOUND TRUE)\n")
    _write(
        root / "thirdparty/Eigen3ConfigVersion.cmake",
        b'set(PACKAGE_VERSION "3.4.0")\n',
    )

    manifest = json.loads((REPO_ROOT / VERIFY.DEFAULT_MANIFEST).read_text(encoding="utf-8"))
    manifest["cmake"]["file"] = _identity(root, "CMakeLists.txt")  # type: ignore[index]
    facts = _archive_facts(root / archive_relative)
    selected_entries = sorted(selected.items())
    manifest["vendored_eigen"] = {  # type: ignore[index]
        "config": _identity(root, "thirdparty/Eigen3Config.cmake"),
        "config_version": _identity(root, "thirdparty/Eigen3ConfigVersion.cmake"),
        "licenses": [
            {
                "archive_path": name,
                "file": _identity(root, f"thirdparty/{name}"),
            }
            for name in VERIFY.EIGEN_LICENSES
        ],
        "normalized_repository_modes": {
            "directory": 0o755,
            "regular": [0o644, 0o755],
        },
        "provenance_complete": True,
        "root": "thirdparty/Eigen",
        "selected_tree": {
            "archive_path": "Eigen",
            "regular_bytes": sum(len(payload) for payload in selected.values()),
            "regular_file_count": len(selected),
            "sha256": VERIFY.V1.regular_tree_digest(selected_entries),
        },
        "upstream_archive": {
            "archive": _identity(root, archive_relative),
            "directory_count": facts["directory_count"],
            "format": {
                "directory_mode": 0o775,
                "file_modes": [0o664, 0o775],
                "gid": 0,
                "gname": "root",
                "mtime": 1_629_319_318,
                "pax_comment": VERIFY.EIGEN_COMMIT,
                "uid": 0,
                "uname": "root",
            },
            "full_tree": {
                "regular_bytes": facts["regular_bytes"],
                "regular_file_count": facts["regular_file_count"],
                "sha256": VERIFY.V1.regular_tree_digest(facts["entries"]),
            },
            "member_count": facts["member_count"],
            "regular_file_count": facts["regular_file_count"],
            "root_directory": VERIFY.EIGEN_ROOT,
            "source": {
                "repository": VERIFY.EIGEN_REPOSITORY,
                "tag": VERIFY.EIGEN_TAG,
                "upstream_commit": VERIFY.EIGEN_COMMIT,
            },
            "tar_stream_bytes": facts["tar_stream_bytes"],
            "uncompressed_regular_bytes": facts["regular_bytes"],
        },
        "version": VERIFY.EIGEN_VERSION,
    }
    return manifest, _save_manifest(root, manifest)


class OfflineBuildInputsV2Tests(unittest.TestCase):
    def test_authenticated_source_loader_ignores_unchecked_malicious_bytecode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "fixture_module.py"
            _write(source, b'MARKER = "bytecode"\n')
            py_compile.compile(
                str(source),
                invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
                doraise=True,
            )
            benign = b'MARKER = "authenticated-source"\n'
            _write(source, benign)
            name = "dancers_authenticated_source_fixture"
            try:
                module = VERIFY._module_from_authenticated_source(benign, source, name)
                self.assertEqual(module.MARKER, "authenticated-source")
            finally:
                sys.modules.pop(name, None)

    def test_v1_predecessor_remains_byte_exact_and_categorically_incomplete(self) -> None:
        v1_manifest_raw = (REPO_ROOT / VERIFY.V1_MANIFEST_PATH).read_bytes()
        v1_verifier_raw = (REPO_ROOT / VERIFY.V1_VERIFIER_PATH).read_bytes()
        self.assertEqual(hashlib.sha256(v1_manifest_raw).hexdigest(), VERIFY.V1_MANIFEST_SHA256)
        self.assertEqual(hashlib.sha256(v1_verifier_raw).hexdigest(), VERIFY.V1_VERIFIER_SHA256)
        predecessor = json.loads(v1_manifest_raw)
        self.assertFalse(predecessor["classification"]["execution_eligible"])
        self.assertFalse(predecessor["classification"]["provenance_complete"])
        self.assertFalse(predecessor["vendored_eigen"]["license_file_present"])

    def test_valid_fixture_is_complete_build_input_but_never_execution_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _, digest = _fixture(root)
            result = VERIFY.verify_repository(root, expected_manifest_sha256=digest)
        self.assertTrue(result["eligible_as_build_input"])
        self.assertTrue(result["provenance_complete"])
        self.assertFalse(result["execution_eligible"])
        self.assertFalse(result["scientific_execution_authorized"])

    def test_live_repository_proves_exact_upstream_and_vendored_tree(self) -> None:
        result = VERIFY.verify_repository(REPO_ROOT)
        raw = (REPO_ROOT / VERIFY.DEFAULT_MANIFEST).read_bytes()
        live_manifest = json.loads(raw)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), VERIFY.EXPECTED_MANIFEST_SHA256)
        self.assertEqual(
            live_manifest["cmake"]["file"],
            {
                "bytes": 30_275,
                "mode": 0o644,
                "path": "CMakeLists.txt",
                "sha256": "1018842af50afc106ea42594841d38a9229c5f0fb64545aa7c1c51f9bb0f17fa",
            },
        )
        self.assertEqual(
            result["vendored_eigen_tree_sha256"],
            "b72914b7742b23fab2f99d6b10eb50897a0d399bab052da8299a434e651f2abb",
        )
        self.assertFalse(result["execution_eligible"])

    def test_manifest_canonical_shape_hash_and_classification_are_strict(self) -> None:
        for mutation in ("expected-hash", "pretty", "extra", "eligibility", "science"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                manifest, digest = _fixture(root)
                if mutation == "expected-hash":
                    expected = "0" * 64
                else:
                    expected = digest
                    if mutation == "pretty":
                        payload = (json.dumps(manifest, indent=2) + "\n").encode()
                        _write(root / VERIFY.DEFAULT_MANIFEST, payload)
                        expected = hashlib.sha256(payload).hexdigest()
                    elif mutation == "extra":
                        manifest["extra"] = True
                        expected = _save_manifest(root, manifest)
                    elif mutation == "eligibility":
                        manifest["classification"]["eligible_as_build_input"] = False  # type: ignore[index]
                        expected = _save_manifest(root, manifest)
                    else:
                        manifest["classification"]["scientific_execution_authorized"] = True  # type: ignore[index]
                        expected = _save_manifest(root, manifest)
                with self.assertRaises(VERIFY.VerificationError):
                    VERIFY.verify_repository(root, expected_manifest_sha256=expected)

    def test_predecessor_manifest_verifier_and_current_cmake_are_reauthenticated(self) -> None:
        for mutation in ("manifest", "verifier", "cmake"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                _, digest = _fixture(root)
                relative = {
                    "manifest": VERIFY.V1_MANIFEST_PATH,
                    "verifier": VERIFY.V1_VERIFIER_PATH,
                    "cmake": "CMakeLists.txt",
                }[mutation]
                with (root / relative).open("ab") as stream:
                    stream.write(b"tamper\n")
                with self.assertRaises(VERIFY.VerificationError):
                    VERIFY.verify_repository(root, expected_manifest_sha256=digest)

    def test_archive_hash_trailing_member_type_and_source_substitution_rejected(self) -> None:
        for mutation in (
            "bytes",
            "trailing",
            "symlink-member",
            "root-payload",
            "source",
            "format",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                manifest, _ = _fixture(root)
                archive_record = manifest["vendored_eigen"]["upstream_archive"]  # type: ignore[index]
                archive_path = root / archive_record["archive"]["path"]  # type: ignore[index]
                if mutation == "bytes":
                    payload = bytearray(archive_path.read_bytes())
                    payload[-1] ^= 1
                    archive_path.write_bytes(payload)
                elif mutation == "trailing":
                    archive_path.write_bytes(archive_path.read_bytes() + b"trailing")
                    archive_record["archive"] = _identity(  # type: ignore[index]
                        root, archive_record["archive"]["path"]  # type: ignore[index]
                    )
                elif mutation == "symlink-member":
                    selected = {"Core": b"synthetic Eigen core\n", "src/helper.h": b"synthetic Eigen helper\n"}
                    licenses = {name: f"license {name}\n".encode() for name in VERIFY.EIGEN_LICENSES}
                    _make_eigen_archive(
                        archive_path,
                        selected=selected,
                        licenses=licenses,
                        extra=[
                            (
                                f"{VERIFY.EIGEN_ROOT}/forbidden-link",
                                "symlink",
                                b"",
                                0o775,
                            )
                        ],
                    )
                    _refresh_eigen_archive(root, manifest)
                elif mutation == "root-payload":
                    with tarfile.open(
                        archive_path,
                        "w:gz",
                        format=tarfile.PAX_FORMAT,
                        pax_headers={"comment": VERIFY.EIGEN_COMMIT},
                    ) as archive:
                        info, _ = _tar_member(
                            VERIFY.EIGEN_ROOT, "directory", b"", 0o775
                        )
                        info.size = 1
                        archive.addfile(info, io.BytesIO(b"x"))
                    _refresh_eigen_archive(root, manifest)
                elif mutation == "source":
                    archive_record["source"]["upstream_commit"] = "0" * 40  # type: ignore[index]
                else:
                    archive_record["format"]["mtime"] += 1  # type: ignore[index,operator]
                digest = _save_manifest(root, manifest)
                if mutation == "root-payload":
                    with self.assertRaisesRegex(
                        VERIFY.VerificationError, "archive root member is invalid"
                    ):
                        VERIFY.verify_repository(root, expected_manifest_sha256=digest)
                else:
                    with self.assertRaises(VERIFY.VerificationError):
                        VERIFY.verify_repository(root, expected_manifest_sha256=digest)

    def test_selected_tree_config_and_upstream_tree_digest_rejected(self) -> None:
        for mutation in (
            "tree-bytes",
            "tree-extra",
            "config",
            "config-version",
            "selected-sha",
            "full-sha",
        ):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                manifest, _ = _fixture(root)
                if mutation == "tree-bytes":
                    _write(root / "thirdparty/Eigen/Core", b"changed\n")
                elif mutation == "tree-extra":
                    _write(root / "thirdparty/Eigen/extra.h", b"extra\n")
                elif mutation == "config":
                    _write(root / "thirdparty/Eigen3Config.cmake", b"changed\n")
                elif mutation == "config-version":
                    _write(root / "thirdparty/Eigen3ConfigVersion.cmake", b"changed\n")
                elif mutation == "selected-sha":
                    manifest["vendored_eigen"]["selected_tree"]["sha256"] = "0" * 64  # type: ignore[index]
                else:
                    manifest["vendored_eigen"]["upstream_archive"]["full_tree"]["sha256"] = "0" * 64  # type: ignore[index]
                digest = _save_manifest(root, manifest)
                with self.assertRaises(VERIFY.VerificationError):
                    VERIFY.verify_repository(root, expected_manifest_sha256=digest)

    def test_exact_seven_license_order_paths_hashes_and_payloads_are_required(self) -> None:
        for mutation in ("missing", "order", "path", "bytes", "identity"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                manifest, _ = _fixture(root)
                licenses = manifest["vendored_eigen"]["licenses"]  # type: ignore[index]
                if mutation == "missing":
                    licenses.pop()
                elif mutation == "order":
                    licenses[0], licenses[1] = licenses[1], licenses[0]
                elif mutation == "path":
                    licenses[0]["archive_path"] = "COPYING.BSD"
                elif mutation == "bytes":
                    _write(root / "thirdparty/COPYING.APACHE", b"rewritten license\n")
                    licenses[0]["file"] = _identity(root, "thirdparty/COPYING.APACHE")
                else:
                    licenses[0]["file"]["sha256"] = "0" * 64
                digest = _save_manifest(root, manifest)
                with self.assertRaises(VERIFY.VerificationError):
                    VERIFY.verify_repository(root, expected_manifest_sha256=digest)

    def test_symlink_writable_and_noncanonical_modes_fail_closed(self) -> None:
        for mutation in ("symlink", "writable", "tree-file-mode", "tree-directory-mode"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                _, digest = _fixture(root)
                path = root / "thirdparty/COPYING.APACHE"
                if mutation == "symlink":
                    saved = path.with_name("saved-license")
                    path.rename(saved)
                    path.symlink_to(saved.name)
                elif mutation == "writable":
                    path.chmod(0o666)
                elif mutation == "tree-file-mode":
                    (root / "thirdparty/Eigen/Core").chmod(0o600)
                else:
                    (root / "thirdparty/Eigen/src").chmod(0o700)
                with self.assertRaises(VERIFY.VerificationError):
                    VERIFY.verify_repository(root, expected_manifest_sha256=digest)

    def test_gzip_truncation_concatenation_and_stream_cap_fail_closed(self) -> None:
        for mutation in ("truncated", "concatenated", "cap-plus-one"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                manifest, _ = _fixture(root)
                record = manifest["vendored_eigen"]["upstream_archive"]  # type: ignore[index]
                relative = record["archive"]["path"]  # type: ignore[index]
                path = root / relative
                raw = path.read_bytes()
                if mutation == "truncated":
                    changed = raw[:-1]
                elif mutation == "concatenated":
                    changed = raw + gzip.compress(b"second", mtime=0)
                else:
                    changed = gzip.compress(bytes(VERIFY.MAX_ARCHIVE_TAR_STREAM_BYTES + 1), mtime=0)
                    record["tar_stream_bytes"] = VERIFY.MAX_ARCHIVE_TAR_STREAM_BYTES + 1  # type: ignore[index]
                path.write_bytes(changed)
                record["archive"] = _identity(root, relative)  # type: ignore[index]
                digest = _save_manifest(root, manifest)
                with self.assertRaises(VERIFY.VerificationError):
                    VERIFY.verify_repository(root, expected_manifest_sha256=digest)

    def test_cli_emits_only_non_authorizing_result(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            _, digest = _fixture(root)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = VERIFY.main(
                    [
                        "--repository-root",
                        str(root),
                        "--expected-manifest-sha256",
                        digest,
                    ]
                )
        self.assertEqual(exit_code, 0)
        result = json.loads(output.getvalue())
        self.assertTrue(result["eligible_as_build_input"])
        self.assertFalse(result["execution_eligible"])
        self.assertFalse(result["scientific_execution_authorized"])


if __name__ == "__main__":
    unittest.main()
