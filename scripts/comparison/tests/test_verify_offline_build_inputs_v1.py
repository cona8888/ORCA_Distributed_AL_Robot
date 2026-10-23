from __future__ import annotations

import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import sys
import tarfile
import tempfile
import unittest
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = REPO_ROOT / "scripts" / "comparison" / "verify_offline_build_inputs_v1.py"
SPEC = importlib.util.spec_from_file_location("verify_offline_build_inputs_v1", MODULE_PATH)
assert SPEC and SPEC.loader
VERIFY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = VERIFY
SPEC.loader.exec_module(VERIFY)


MANIF_COMMIT = "1" * 40
RAYLIB_COMMIT = "2" * 40


def _write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _file_identity(root: Path, relative: str) -> dict[str, object]:
    path = root / relative
    payload = path.read_bytes()
    return {
        "bytes": len(payload),
        "mode": stat.S_IMODE(path.stat().st_mode),
        "path": relative,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _add_directory(archive: tarfile.TarFile, name: str) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.DIRTYPE
    info.mode = 0o755
    info.mtime = 0
    archive.addfile(info)


def _add_file(archive: tarfile.TarFile, name: str, payload: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.type = tarfile.REGTYPE
    info.mode = 0o644
    info.mtime = 0
    info.size = len(payload)
    archive.addfile(info, io.BytesIO(payload))


def _make_tar(
    path: Path,
    root: str,
    files: dict[str, bytes],
    commit: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    directories = {""}
    for relative in files:
        parent = PurePosixPath(relative).parent
        while parent.as_posix() not in ("", "."):
            directories.add(parent.as_posix())
            parent = parent.parent
    with tarfile.open(
        path,
        "w:gz",
        format=tarfile.PAX_FORMAT,
        pax_headers={"comment": commit},
    ) as archive:
        for relative in sorted(directories, key=lambda item: (item.count("/"), item)):
            _add_directory(archive, root if not relative else f"{root}/{relative}")
        for relative, payload in sorted(files.items()):
            _add_file(archive, f"{root}/{relative}", payload)


def _archive_record(
    repository: Path,
    *,
    name: str,
    version: str,
    commit: str,
    root: str,
    relative_archive: str,
    selected: list[str],
) -> dict[str, object]:
    entries: list[tuple[str, bytes]] = []
    with tarfile.open(repository / relative_archive, "r:gz") as archive:
        members = archive.getmembers()
        for member in members:
            if member.isfile():
                extracted = archive.extractfile(member)
                assert extracted is not None
                entries.append(
                    (
                        str(PurePosixPath(member.name).relative_to(root)),
                        extracted.read(),
                    )
                )
    return {
        "archive": _file_identity(repository, relative_archive),
        "directory_count": sum(member.isdir() for member in members),
        "license_path": "LICENSE",
        "member_count": len(members),
        "name": name,
        "regular_file_count": sum(member.isfile() for member in members),
        "root_directory": root,
        "selected_paths": selected,
        "source": {
            "repository": f"example.invalid/{name}",
            "tag": version,
            "upstream_commit": commit,
        },
        "tar_stream_bytes": len(gzip.decompress((repository / relative_archive).read_bytes())),
        "tree_sha256": VERIFY.regular_tree_digest(entries),
        "uncompressed_regular_bytes": sum(len(payload) for _, payload in entries),
        "version": version,
    }


def _cmake_text(manif_sha: str, raylib_sha: str) -> str:
    return f'''cmake_minimum_required(VERSION 3.15)
include(FetchContent)
foreach(DANCERS_DEPENDENCY IN ITEMS MANIF RAYLIB)
    if(DEFINED FETCHCONTENT_SOURCE_DIR_${{DANCERS_DEPENDENCY}}
       AND NOT "${{FETCHCONTENT_SOURCE_DIR_${{DANCERS_DEPENDENCY}}}}" STREQUAL "")
        message(FATAL_ERROR
            "FETCHCONTENT_SOURCE_DIR_${{DANCERS_DEPENDENCY}} overrides are forbidden")
    endif()
endforeach()
set(DANCERS_MANIF_ARCHIVE
    "${{CMAKE_CURRENT_SOURCE_DIR}}/thirdparty/dist/manif-test.tar.gz")
FetchContent_Declare(
    manif
    URL "${{DANCERS_MANIF_ARCHIVE}}"
    URL_HASH SHA256={manif_sha}
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)
FetchContent_MakeAvailable(manif)
set(DANCERS_RAYLIB_ARCHIVE
    "${{CMAKE_CURRENT_SOURCE_DIR}}/thirdparty/dist/raylib-test.tar.gz")
FetchContent_Declare(
    raylib
    URL "${{DANCERS_RAYLIB_ARCHIVE}}"
    URL_HASH SHA256={raylib_sha}
    DOWNLOAD_EXTRACT_TIMESTAMP TRUE
)
FetchContent_MakeAvailable(raylib)
set(DANCERS_RUNTIME_ARTIFACT_ROOT "${{CMAKE_SOURCE_DIR}}" CACHE PATH
    "Authenticated runtime root")
get_filename_component(GBP_PROJECT_ROOT
    "${{DANCERS_RUNTIME_ARTIFACT_ROOT}}" REALPATH)
'''


def _tree_entries(root: Path) -> list[tuple[str, bytes]]:
    return [
        (path.relative_to(root).as_posix(), path.read_bytes())
        for path in sorted(root.rglob("*"))
        if path.is_file() and not path.is_symlink()
    ]


def _make_fixture(repository: Path) -> tuple[dict[str, object], str]:
    manif_files = {
        "CMakeLists.txt": b"manif cmake\n",
        "LICENSE": b"manif license\n",
        "cmake/module.cmake": b"module\n",
        "external/tl/optional.hpp": b"optional\n",
        "include/manif/manif.h": b"header\n",
    }
    raylib_files = {
        "CMakeLists.txt": b"raylib cmake\n",
        "CMakeOptions.txt": b"options\n",
        "LICENSE": b"raylib license\n",
        "README.md": b"readme\n",
        "cmake/module.cmake": b"module\n",
        "raylib.pc.in": b"pc\n",
        "src/raylib.c": b"source\n",
    }
    _make_tar(
        repository / "thirdparty/dist/manif-test.tar.gz",
        "manif-test",
        manif_files,
        MANIF_COMMIT,
    )
    _make_tar(
        repository / "thirdparty/dist/raylib-test.tar.gz",
        "raylib-test",
        raylib_files,
        RAYLIB_COMMIT,
    )
    manif = _archive_record(
        repository,
        name="manif",
        version="0.0.5",
        commit=MANIF_COMMIT,
        root="manif-test",
        relative_archive="thirdparty/dist/manif-test.tar.gz",
        selected=["CMakeLists.txt", "cmake", "include", "external", "LICENSE"],
    )
    raylib = _archive_record(
        repository,
        name="raylib",
        version="5.5",
        commit=RAYLIB_COMMIT,
        root="raylib-test",
        relative_archive="thirdparty/dist/raylib-test.tar.gz",
        selected=[
            "CMakeLists.txt",
            "CMakeOptions.txt",
            "README.md",
            "raylib.pc.in",
            "cmake",
            "src",
            "LICENSE",
        ],
    )
    _write(
        repository / "CMakeLists.txt",
        _cmake_text(
            str(manif["archive"]["sha256"]),  # type: ignore[index]
            str(raylib["archive"]["sha256"]),  # type: ignore[index]
        ).encode("utf-8"),
    )
    _write(repository / "thirdparty/Eigen/Core/header.h", b"eigen header\n")
    _write(repository / "thirdparty/Eigen/src/helper.h", b"eigen helper\n")
    _write(repository / "thirdparty/Eigen3Config.cmake", b"set(Eigen3_FOUND TRUE)\n")
    eigen_entries = _tree_entries(repository / "thirdparty/Eigen")
    manifest: dict[str, object] = {
        "archives": [manif, raylib],
        "classification": {
            "execution_eligible": False,
            "offline_build_inputs_authenticated": True,
            "provenance_complete": False,
            "scientific_execution_authorized": False,
        },
        "cmake": {
            "direct_source_override_guards": ["MANIF", "RAYLIB"],
            "fetchcontent_declarations": [
                {
                    "archive_variable": "DANCERS_MANIF_ARCHIVE",
                    "local_path": "thirdparty/dist/manif-test.tar.gz",
                    "name": "manif",
                    "sha256": manif["archive"]["sha256"],  # type: ignore[index]
                },
                {
                    "archive_variable": "DANCERS_RAYLIB_ARCHIVE",
                    "local_path": "thirdparty/dist/raylib-test.tar.gz",
                    "name": "raylib",
                    "sha256": raylib["archive"]["sha256"],  # type: ignore[index]
                },
            ],
            "file": _file_identity(repository, "CMakeLists.txt"),
            "forbidden_tokens": ["GIT_REPOSITORY", "http://", "https://"],
            "make_available": ["manif", "raylib"],
            "runtime_artifact_root": {
                "cache_type": "PATH",
                "default": "${CMAKE_SOURCE_DIR}",
                "variable": "DANCERS_RUNTIME_ARTIFACT_ROOT",
            },
        },
        "limits": dict(VERIFY.LIMITS),
        "schema_version": VERIFY.SCHEMA_VERSION,
        "tree_digest": {
            "algorithm": "sha256",
            "domain": VERIFY.TREE_DOMAIN[:-1].decode("ascii"),
            "entry_encoding": VERIFY.TREE_ENCODING,
        },
        "vendored_eigen": {
            "config": _file_identity(repository, "thirdparty/Eigen3Config.cmake"),
            "license_file_present": False,
            "license_path": None,
            "provenance_blocker": "Synthetic Eigen tree has no authenticated license.",
            "provenance_complete": False,
            "root": "thirdparty/Eigen",
            "tree_regular_bytes": sum(len(payload) for _, payload in eigen_entries),
            "tree_regular_file_count": len(eigen_entries),
            "tree_sha256": VERIFY.regular_tree_digest(eigen_entries),
            "version": "3.4.0",
        },
    }
    return manifest, _save_manifest(repository, manifest)


def _save_manifest(repository: Path, manifest: dict[str, object]) -> str:
    payload = VERIFY.canonical_json_bytes(manifest)
    _write(repository / VERIFY.DEFAULT_MANIFEST, payload)
    return hashlib.sha256(payload).hexdigest()


def _refresh_cmake_identity(repository: Path, manifest: dict[str, object]) -> None:
    manifest["cmake"]["file"] = _file_identity(  # type: ignore[index]
        repository, "CMakeLists.txt"
    )


def _refresh_archive_identity(
    repository: Path,
    manifest: dict[str, object],
    index: int,
) -> None:
    archive = manifest["archives"][index]  # type: ignore[index]
    relative = archive["archive"]["path"]  # type: ignore[index]
    archive["archive"] = _file_identity(repository, relative)  # type: ignore[index]
    archive["tar_stream_bytes"] = len(  # type: ignore[index]
        gzip.decompress((repository / relative).read_bytes())
    )


def _refresh_eigen_tree(repository: Path, manifest: dict[str, object]) -> None:
    entries = _tree_entries(repository / "thirdparty/Eigen")
    eigen = manifest["vendored_eigen"]  # type: ignore[assignment]
    eigen["tree_regular_file_count"] = len(entries)  # type: ignore[index]
    eigen["tree_regular_bytes"] = sum(len(payload) for _, payload in entries)  # type: ignore[index]
    eigen["tree_sha256"] = VERIFY.regular_tree_digest(entries)  # type: ignore[index]


class OfflineBuildInputsV1Tests(unittest.TestCase):
    def _fixture(self) -> tuple[tempfile.TemporaryDirectory[str], Path, dict[str, object], str]:
        temporary = tempfile.TemporaryDirectory()
        repository = Path(temporary.name).resolve()
        manifest, digest = _make_fixture(repository)
        return temporary, repository, manifest, digest

    def test_valid_fixture_authenticates_but_remains_ineligible(self) -> None:
        temporary, repository, _, digest = self._fixture()
        with temporary:
            result = VERIFY.verify_repository(repository, expected_manifest_sha256=digest)
        self.assertTrue(result["offline_build_inputs_authenticated"])
        self.assertFalse(result["provenance_complete"])
        self.assertFalse(result["execution_eligible"])
        self.assertFalse(result["vendored_eigen_license_file_present"])

    def test_manifest_is_canonical_and_independently_authenticated(self) -> None:
        for mutation in ("pretty", "expected-hash"):
            with self.subTest(mutation=mutation):
                temporary, repository, manifest, digest = self._fixture()
                with temporary:
                    if mutation == "pretty":
                        (repository / VERIFY.DEFAULT_MANIFEST).write_text(
                            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
                        )
                        expected = hashlib.sha256(
                            (repository / VERIFY.DEFAULT_MANIFEST).read_bytes()
                        ).hexdigest()
                    else:
                        expected = "0" * 64
                    with self.assertRaises(VERIFY.VerificationError):
                        VERIFY.verify_repository(repository, expected_manifest_sha256=expected)

    def test_archive_tamper_and_nonregular_repository_entry_rejected(self) -> None:
        for mutation in ("bytes", "symlink", "writable"):
            with self.subTest(mutation=mutation):
                temporary, repository, _, digest = self._fixture()
                with temporary:
                    archive = repository / "thirdparty/dist/manif-test.tar.gz"
                    if mutation == "bytes":
                        payload = bytearray(archive.read_bytes())
                        payload[-1] ^= 1
                        archive.write_bytes(payload)
                    elif mutation == "symlink":
                        target = archive.with_name("saved.tar.gz")
                        archive.rename(target)
                        archive.symlink_to(target.name)
                    else:
                        archive.chmod(0o666)
                    with self.assertRaises(VERIFY.VerificationError):
                        VERIFY.verify_repository(repository, expected_manifest_sha256=digest)

    def test_traversal_and_link_tar_members_rejected(self) -> None:
        for mutation in ("traversal", "symlink"):
            with self.subTest(mutation=mutation):
                temporary, repository, manifest, _ = self._fixture()
                with temporary:
                    path = repository / "thirdparty/dist/manif-test.tar.gz"
                    with tarfile.open(
                        path,
                        "w:gz",
                        format=tarfile.PAX_FORMAT,
                        pax_headers={"comment": MANIF_COMMIT},
                    ) as archive:
                        _add_directory(archive, "manif-test")
                        if mutation == "traversal":
                            _add_file(archive, "manif-test/../escape", b"escape\n")
                        else:
                            info = tarfile.TarInfo("manif-test/LICENSE")
                            info.type = tarfile.SYMTYPE
                            info.linkname = "../outside"
                            info.mode = 0o777
                            archive.addfile(info)
                    _refresh_archive_identity(repository, manifest, 0)
                    digest = _save_manifest(repository, manifest)
                    with self.assertRaises(VERIFY.VerificationError):
                        VERIFY.verify_repository(repository, expected_manifest_sha256=digest)

    def test_archive_hash_count_tree_selection_and_license_rejected(self) -> None:
        for mutation in ("hash", "count", "tree", "selection", "license"):
            with self.subTest(mutation=mutation):
                temporary, repository, manifest, _ = self._fixture()
                with temporary:
                    archive = manifest["archives"][0]  # type: ignore[index]
                    if mutation == "hash":
                        archive["archive"]["sha256"] = "0" * 64  # type: ignore[index]
                    elif mutation == "count":
                        archive["member_count"] += 1  # type: ignore[operator]
                    elif mutation == "tree":
                        archive["tree_sha256"] = "0" * 64
                    elif mutation == "selection":
                        archive["selected_paths"].append("missing")  # type: ignore[union-attr]
                    else:
                        archive["license_path"] = "MISSING-LICENSE"
                        archive["selected_paths"].append("MISSING-LICENSE")  # type: ignore[union-attr]
                    digest = _save_manifest(repository, manifest)
                    with self.assertRaises(VERIFY.VerificationError):
                        VERIFY.verify_repository(repository, expected_manifest_sha256=digest)

    def test_archive_member_cap_plus_one_rejected(self) -> None:
        temporary, repository, manifest, _ = self._fixture()
        with temporary:
            path = repository / "thirdparty/dist/manif-test.tar.gz"
            with tarfile.open(
                path,
                "w:gz",
                format=tarfile.PAX_FORMAT,
                pax_headers={"comment": MANIF_COMMIT},
            ) as archive:
                _add_directory(archive, "manif-test")
                for index in range(VERIFY.MAX_ARCHIVE_MEMBERS):
                    _add_file(archive, f"manif-test/cmake/f-{index:04d}", b"")
            with tarfile.open(path, "r:gz") as archive:
                self.assertEqual(
                    len(archive.getmembers()),
                    VERIFY.MAX_ARCHIVE_MEMBERS + 1,
                )
            _refresh_archive_identity(repository, manifest, 0)
            digest = _save_manifest(repository, manifest)
            with self.assertRaisesRegex(VERIFY.VerificationError, "member-count bound"):
                VERIFY.verify_repository(repository, expected_manifest_sha256=digest)

    def test_gzip_member_eof_trailing_concatenation_and_stream_cap_rejected(self) -> None:
        for mutation in ("truncated", "trailing", "concatenated", "tar-trailing", "cap-plus-one"):
            with self.subTest(mutation=mutation):
                temporary, repository, manifest, _ = self._fixture()
                with temporary:
                    path = repository / "thirdparty/dist/manif-test.tar.gz"
                    original = path.read_bytes()
                    if mutation == "truncated":
                        changed = original[:-1]
                    elif mutation == "trailing":
                        changed = original + b"X"
                    elif mutation == "concatenated":
                        changed = original + gzip.compress(b"second member", mtime=0)
                    elif mutation == "tar-trailing":
                        tar_stream = gzip.decompress(original) + b"nonzero trailing tar data"
                        changed = gzip.compress(tar_stream, mtime=0)
                        manifest["archives"][0]["tar_stream_bytes"] = len(tar_stream)  # type: ignore[index]
                    else:
                        tar_stream = bytes(VERIFY.MAX_ARCHIVE_TAR_STREAM_BYTES + 1)
                        changed = gzip.compress(tar_stream, mtime=0)
                        manifest["archives"][0]["tar_stream_bytes"] = len(tar_stream)  # type: ignore[index]
                    path.write_bytes(changed)
                    manifest["archives"][0]["archive"] = _file_identity(  # type: ignore[index]
                        repository, "thirdparty/dist/manif-test.tar.gz"
                    )
                    digest = _save_manifest(repository, manifest)
                    with self.assertRaises(VERIFY.VerificationError):
                        VERIFY.verify_repository(repository, expected_manifest_sha256=digest)

    def test_archive_and_tree_casefold_and_ancestor_conflicts_rejected(self) -> None:
        archive_cases = {
            "casefold": [
                ("manif-test", "directory", b""),
                ("manif-test/include/Case.h", "file", b"upper\n"),
                ("manif-test/include/case.h", "file", b"lower\n"),
            ],
            "file-ancestor": [
                ("manif-test", "directory", b""),
                ("manif-test/include/conflict", "file", b"file\n"),
                ("manif-test/include/conflict/child.h", "file", b"child\n"),
            ],
        }
        for mutation, members in archive_cases.items():
            with self.subTest(scope="archive", mutation=mutation):
                temporary, repository, manifest, _ = self._fixture()
                with temporary:
                    path = repository / "thirdparty/dist/manif-test.tar.gz"
                    with tarfile.open(
                        path,
                        "w:gz",
                        format=tarfile.PAX_FORMAT,
                        pax_headers={"comment": MANIF_COMMIT},
                    ) as archive:
                        for name, kind, payload in members:
                            if kind == "directory":
                                _add_directory(archive, name)
                            else:
                                _add_file(archive, name, payload)
                    _refresh_archive_identity(repository, manifest, 0)
                    digest = _save_manifest(repository, manifest)
                    with self.assertRaises(VERIFY.VerificationError):
                        VERIFY.verify_repository(repository, expected_manifest_sha256=digest)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write(root / "one", b"one")
            _write(root / "two", b"two")
            with self.assertRaisesRegex(VERIFY.VerificationError, "casefold-colliding"):
                VERIFY.regular_tree_digest(
                    [("Case.h", (root / "one").read_bytes()), ("case.h", (root / "two").read_bytes())]
                )
            with self.assertRaisesRegex(VERIFY.VerificationError, "as an ancestor"):
                VERIFY.regular_tree_digest(
                    [("node", (root / "one").read_bytes()), ("node/child", (root / "two").read_bytes())]
                )

    def test_canonical_root_and_directory_name_reauthentication(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            outer = Path(temporary).resolve()
            repository = outer / "repository"
            repository.mkdir()
            _, digest = _make_fixture(repository)
            alias = outer / "alias"
            alias.symlink_to(repository, target_is_directory=True)
            with self.assertRaisesRegex(VERIFY.VerificationError, "canonical"):
                VERIFY.verify_repository(alias, expected_manifest_sha256=digest)
            lexical = Path(f"{repository}/thirdparty/..")
            with self.assertRaisesRegex(VERIFY.VerificationError, "canonical"):
                VERIFY.verify_repository(lexical, expected_manifest_sha256=digest)

            real_open = os.open
            raced = False

            def racing_open(path: object, flags: int, *args: object, **kwargs: object) -> int:
                nonlocal raced
                descriptor = real_open(path, flags, *args, **kwargs)
                if path == "thirdparty" and kwargs.get("dir_fd") is not None and not raced:
                    raced = True
                    parent_fd = kwargs["dir_fd"]
                    os.rename(
                        "thirdparty",
                        "thirdparty-before-race",
                        src_dir_fd=parent_fd,
                        dst_dir_fd=parent_fd,
                    )
                    os.mkdir("thirdparty", mode=0o700, dir_fd=parent_fd)
                return descriptor

            with mock.patch.object(VERIFY.os, "open", side_effect=racing_open):
                with self.assertRaisesRegex(VERIFY.VerificationError, "changed identity"):
                    VERIFY.verify_repository(repository, expected_manifest_sha256=digest)

    def test_cmake_network_git_hash_and_runtime_mutations_rejected(self) -> None:
        mutations = {
            "network": lambda text: text.replace(
                'URL "${DANCERS_MANIF_ARCHIVE}"',
                'URL "https://example.invalid/manif.tar.gz"',
            ),
            "git": lambda text: text.replace(
                "DOWNLOAD_EXTRACT_TIMESTAMP TRUE",
                "GIT_REPOSITORY local-repository\n    DOWNLOAD_EXTRACT_TIMESTAMP TRUE",
                1,
            ),
            "url-hash": lambda text: text.replace("SHA256=", "SHA256=0", 1),
            "runtime": lambda text: text.replace(" CACHE PATH\n", "\n", 1),
            "make-available": lambda text: text.replace(
                "FetchContent_MakeAvailable(manif)\n", "", 1
            ),
            "override-item": lambda text: text.replace(
                "ITEMS MANIF RAYLIB", "ITEMS RAYLIB", 1
            ),
            "override-condition": lambda text: text.replace("AND NOT", "AND", 1),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                temporary, repository, manifest, _ = self._fixture()
                with temporary:
                    cmake = repository / "CMakeLists.txt"
                    cmake.write_text(mutate(cmake.read_text(encoding="utf-8")), encoding="utf-8")
                    _refresh_cmake_identity(repository, manifest)
                    digest = _save_manifest(repository, manifest)
                    with self.assertRaises(VERIFY.VerificationError):
                        VERIFY.verify_repository(repository, expected_manifest_sha256=digest)

    def test_eigen_tree_config_and_new_license_rejected(self) -> None:
        for mutation in ("tree", "config", "license"):
            with self.subTest(mutation=mutation):
                temporary, repository, manifest, _ = self._fixture()
                with temporary:
                    if mutation == "tree":
                        _write(repository / "thirdparty/Eigen/Core/header.h", b"changed\n")
                    elif mutation == "config":
                        _write(repository / "thirdparty/Eigen3Config.cmake", b"changed\n")
                    else:
                        _write(repository / "thirdparty/Eigen/LICENSE", b"license\n")
                        _refresh_eigen_tree(repository, manifest)
                    digest = _save_manifest(repository, manifest)
                    with self.assertRaises(VERIFY.VerificationError):
                        VERIFY.verify_repository(repository, expected_manifest_sha256=digest)

    def test_identity_path_escape_rejected(self) -> None:
        temporary, repository, manifest, _ = self._fixture()
        with temporary:
            manifest["archives"][0]["archive"]["path"] = "../outside.tar.gz"  # type: ignore[index]
            digest = _save_manifest(repository, manifest)
            with self.assertRaises(VERIFY.VerificationError):
                VERIFY.verify_repository(repository, expected_manifest_sha256=digest)

    def test_cli_returns_distinct_incomplete_status(self) -> None:
        temporary, repository, _, digest = self._fixture()
        with temporary, contextlib.redirect_stdout(io.StringIO()) as output:
            status = VERIFY.main(
                [
                    "--repository-root",
                    str(repository),
                    "--expected-manifest-sha256",
                    digest,
                ]
            )
        self.assertEqual(status, 2)
        result = json.loads(output.getvalue())
        self.assertTrue(result["offline_build_inputs_authenticated"])
        self.assertFalse(result["provenance_complete"])


if __name__ == "__main__":
    unittest.main()
