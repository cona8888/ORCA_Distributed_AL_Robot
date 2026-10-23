from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


VERIFY = _load(
    "verify_physics_build_provenance_v1_tested",
    REPOSITORY_ROOT / "scripts/comparison/verify_physics_build_provenance_v1.py",
)
CAPTURE = _load(
    "capture_physics_build_provenance_v1_tested",
    REPOSITORY_ROOT / "scripts/comparison/capture_physics_build_provenance_v1.py",
)


def _canonical(value: object) -> bytes:
    return VERIFY.canonical_json_bytes(value)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write(path: Path, raw: bytes, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    path.chmod(mode)


def _macho(file_type: int, trailer: bytes = b"") -> bytes:
    return struct.pack(
        "<IIIIIIII",
        0xFEEDFACF,
        0x0100000C,
        0,
        file_type,
        0,
        0,
        0,
        0,
    ) + trailer


def _stream(text: str) -> dict[str, object]:
    raw = text.encode("utf-8")
    return {"bytes": len(raw), "sha256": _sha(raw), "utf8": text}


def _offline_digest(entries: list[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    digest.update(VERIFY.OFFLINE_TREE_DOMAIN)
    for path, raw in sorted(entries, key=lambda item: item[0].encode("utf-8")):
        encoded = path.encode("utf-8")
        digest.update(b"file\0")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(hashlib.sha256(raw).digest())
    return digest.hexdigest()


class SyntheticFixture:
    """A private, analytic-only provenance package; no command is launched."""

    TOOL_ROLES = sorted(VERIFY.TOOL_ROLES)
    COMMON_SOURCES = {
        "physics-engine": "src/physics.cpp",
        "controller-adapter": "src/adapter.cpp",
        "artifact-loader": "src/loader.cpp",
        "simulator-bridge": "src/bridge.cpp",
        "sha256": "src/sha.cpp",
    }

    def __init__(self, parent: Path) -> None:
        self.parent = parent
        self.paths = {
            "repository": parent / "repository",
            "runtime-artifact-root": parent / "repository",
            "reference-source": parent / "reference-source",
            "optimized-source": parent / "optimized-source",
            "reference-build": parent / "reference-build",
            "optimized-build": parent / "optimized-build",
            "toolchain-prefix": parent / "toolchain-prefix",
            "sdk-root": parent / "sdk-root",
            "private-home": parent / "private-home",
            "private-tmp": parent / "private-tmp",
        }
        for path in set(self.paths.values()):
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(0o700)
        self.repo = self.paths["repository"]
        self._prepare_repository_inputs()
        self._prepare_sources()
        self._prepare_toolchain_and_sdk()
        self._prepare_dependencies()
        self._prepare_actions_objects_and_products()
        self._prepare_trust_roots_and_manifest()

    def fi(self, root_id: str, relative: str) -> dict[str, object]:
        path = self.paths[root_id] / relative
        info = path.stat()
        raw = path.read_bytes()
        return {
            "root_id": root_id,
            "relative_path": relative,
            "file_type": "regular",
            "mode": stat.S_IMODE(info.st_mode),
            "bytes": len(raw),
            "sha256": _sha(raw),
        }

    @staticmethod
    def offline(identity: dict[str, object]) -> dict[str, object]:
        return {
            "bytes": identity["bytes"],
            "mode": identity["mode"],
            "path": identity["relative_path"],
            "sha256": identity["sha256"],
        }

    def entries(self, root_id: str) -> list[dict[str, object]]:
        root = self.paths[root_id]
        return [
            self.fi(root_id, path.relative_to(root).as_posix())
            for path in sorted(root.rglob("*"))
            if path.is_file() and not path.is_symlink()
        ]

    def _put_repo(self, relative: str, raw: bytes, mode: int = 0o644) -> None:
        _write(self.repo / relative, raw, mode)

    def _prepare_repository_inputs(self) -> None:
        contract = (
            REPOSITORY_ROOT / "docs/simulator/physics_build_provenance_contract_v1.json"
        ).read_bytes()
        self._put_repo("docs/simulator/physics_build_provenance_contract_v1.json", contract)
        placeholders = {
            "docs/simulator/physics_execution_design_epoch_1_v1.json": _canonical({"design": 1}),
            "docs/simulator/physics_stress_test_v1.json": _canonical({"registry": 1}),
            "CMakeLists.txt": b"synthetic cmake input\n",
            "pixi.toml": b"[project]\nname='synthetic'\n",
            "fixtures/kinematic.json": _canonical({"fixture": 1}),
            "fixtures/kinematic.digest": b"0" * 64 + b"\n",
            "docs/simulator/object_split.json": _canonical({"split": 1}),
            "tools/executor": b"synthetic executor\n",
            "tools/wrapper": b"synthetic wrapper\n",
            "tools/tree-hasher": b"synthetic tree hasher\n",
            "tools/macho-parser": b"synthetic macho parser\n",
            "tools/network-guard": b"synthetic network guard\n",
            "tools/network-policy": b"synthetic network policy\n",
            "tools/host-mechanism": b"synthetic host mechanism\n",
            "tools/canonical-policy": b"synthetic canonical policy\n",
            "host/identity.json": _canonical({"host": "synthetic"}),
            "scripts/comparison/verify_offline_build_inputs_v1.py": b"synthetic v1 verifier\n",
            "scripts/comparison/verify_offline_build_inputs_v2.py": b"synthetic v2 verifier\n",
        }
        for relative, raw in placeholders.items():
            self._put_repo(relative, raw, 0o755 if relative.startswith("tools/") else 0o644)
        self._put_repo(
            "scripts/comparison/verify_physics_build_provenance_v1.py",
            (REPOSITORY_ROOT / "scripts/comparison/verify_physics_build_provenance_v1.py").read_bytes(),
        )

    def _prepare_sources(self) -> None:
        eigen_paths = [f"thirdparty/Eigen/Eigen/file-{index:03d}.hpp" for index in range(337)]
        large = b"E" * 6_115_017
        for method in ("reference", "optimized"):
            root = self.paths[f"{method}-source"]
            for index, relative in enumerate(eigen_paths):
                _write(root / relative, large if index == 0 else b"")
            source_payloads = {
                "src/physics.cpp": b"physics\n",
                "src/adapter.cpp": b"adapter\n",
                "src/loader.cpp": b"loader\n",
                "src/bridge.cpp": b"bridge\n",
                "src/sha.cpp": b"sha\n",
                "src/controller.cpp": f"controller {method}\n".encode(),
                "src/observer.cpp": f"observer {method}\n".encode(),
                "include/common.hpp": b"header\n",
            }
            for relative, raw in source_payloads.items():
                _write(root / relative, raw)
            entries = self.entries(f"{method}-source")
            git_files: list[tuple[str, int, str]] = []
            source_files: list[dict[str, object]] = []
            for identity in entries:
                raw = (root / str(identity["relative_path"])).read_bytes()
                blob = VERIFY._git_blob_sha1(raw)
                git_files.append((str(identity["relative_path"]), int(identity["mode"]), blob))
                source_files.append({"file": identity, "git_blob_sha1": blob})
            tree = VERIFY._git_tree_sha1(git_files)
            commit_raw = (
                f"tree {tree}\nauthor Synthetic <none@example.invalid> 0 +0000\n"
                f"committer Synthetic <none@example.invalid> 0 +0000\n\n{method}\n"
            ).encode()
            commit = hashlib.sha1(
                b"commit " + str(len(commit_raw)).encode() + b"\0" + commit_raw
            ).hexdigest()
            commit_path = f"source-identities/{method}.commit"
            self._put_repo(commit_path, commit_raw)
            source_identity = {
                "schema_version": VERIFY.SOURCE_IDENTITY_SCHEMA,
                "method": method,
                "worktree_root": f"{method}-source",
                "git_commit": commit,
                "git_commit_object": self.fi("repository", commit_path),
                "git_tree": tree,
                "classification": {
                    "clean": True,
                    "remote_free": True,
                    "submodules_absent": True,
                    "alternates_absent": True,
                    "replace_refs_absent": True,
                    "untracked_files_absent": True,
                },
                "files": source_files,
                "source_tree_sha256": VERIFY.tree_sha256(entries),
            }
            self._put_repo(
                f"source-identities/{method}.json", _canonical(source_identity)
            )

    def _prepare_toolchain_and_sdk(self) -> None:
        prefix = self.paths["toolchain-prefix"]
        self.tools: dict[str, dict[str, object]] = {}
        for role in self.TOOL_ROLES:
            relative = f"bin/{role.replace(':', '-') }"
            _write(prefix / relative, _macho(2, role.encode()), 0o755)
            identity = self.fi("toolchain-prefix", relative)
            version_text = f"{role} synthetic 1\n"
            absolute = str(prefix / relative)
            self.tools[role] = {
                "role": role,
                "file": identity,
                "version_argv": [absolute, "--version"],
                "version_stdout_sha256": _sha(version_text.encode()),
                "macho": VERIFY._parse_macho((prefix / relative).read_bytes(), role),
            }
        extra = {
            "share/cmake/module.cmake": b"module\n",
            "lib/clang/resource.txt": b"resource\n",
            "include/c++/v1/vector": b"header\n",
            "lib/libc++.dylib": _macho(6),
            "lib/clang/libcompiler_rt.a": b"compiler rt\n",
            "lib/libomp.dylib": _macho(6),
        }
        for relative, raw in extra.items():
            _write(prefix / relative, raw)
        package_raw = b"synthetic conda package archive\n"
        self._put_repo("packages/toolchain.conda", package_raw)
        package_sha = _sha(package_raw)
        self.package_url = (
            "https://conda.anaconda.org/conda-forge/osx-arm64/"
            "synthetic-toolchain-1-0.conda"
        )
        pixi_lock = (
            "version: 7\npackages:\n"
            f"- conda: {self.package_url}\n  sha256: {package_sha}\n"
        ).encode()
        self._put_repo("pixi.lock", pixi_lock)

        sdk = self.paths["sdk-root"]
        _write(sdk / "SDKSettings.json", _canonical({"Version": "13.0"}))
        _write(sdk / "usr/include/stdio.h", b"header\n")
        _write(sdk / "usr/lib/libSystem.tbd", b"--- !tapi-tbd\n")

    # The remaining builders are defined below to keep each phase inspectable.
    def _prepare_dependencies(self) -> None:
        self._prepare_dependencies_impl()

    def _prepare_actions_objects_and_products(self) -> None:
        self._prepare_actions_impl()

    def _prepare_trust_roots_and_manifest(self) -> None:
        self._prepare_manifest_impl()


def _fixture_prepare_dependencies_impl(self: SyntheticFixture) -> None:
    manif_files = [("CMakeLists.txt", b"manif source\n")]
    raylib_files = [("CMakeLists.txt", b"raylib source\n")]
    archive_specs = {
        "manif": {
            "path": "thirdparty/dist/manif-0.0.5-build-source.tar.gz",
            "raw": b"synthetic manif archive\n",
            "version": "0.0.5",
            "revision": "c49fec8150e62821d9be7e2a3ac49db75bf7bb92",
            "files": manif_files,
        },
        "raylib": {
            "path": "thirdparty/dist/raylib-5.5-build-source.tar.gz",
            "raw": b"synthetic raylib archive\n",
            "version": "5.5",
            "revision": "c1ab645ca298a2801097931d1079b10ff7eb9df8",
            "files": raylib_files,
        },
    }
    predecessor_archives: list[dict[str, object]] = []
    for name in ("manif", "raylib"):
        spec = archive_specs[name]
        self._put_repo(str(spec["path"]), bytes(spec["raw"]))
        identity = self.fi("repository", str(spec["path"]))
        predecessor_archives.append(
            {
                "archive": self.offline(identity),
                "name": name,
                "source": {
                    "repository": f"example.invalid/{name}",
                    "tag": spec["version"],
                    "upstream_commit": spec["revision"],
                },
                "tree_sha256": _offline_digest(list(spec["files"])),
                "version": spec["version"],
            }
        )
    v1_manifest = {
        "archives": predecessor_archives,
        "classification": {
            "execution_eligible": False,
            "offline_build_inputs_authenticated": True,
            "provenance_complete": False,
            "scientific_execution_authorized": False,
        },
        "schema_version": "dancers-offline-build-inputs-v1",
    }
    self._put_repo("thirdparty/dist/offline_build_inputs_v1.json", _canonical(v1_manifest))

    eigen_archive_path = "thirdparty/dist/eigen-3.4.0-upstream-3147391d.tar.gz"
    self._put_repo(eigen_archive_path, b"synthetic authenticated Eigen archive\n")
    self._put_repo("thirdparty/Eigen3Config.cmake", b"set(Eigen3_FOUND TRUE)\n")
    self._put_repo("thirdparty/Eigen3ConfigVersion.cmake", b"set(PACKAGE_VERSION 3.4.0)\n")
    license_names = [
        "COPYING.APACHE",
        "COPYING.BSD",
        "COPYING.GPL",
        "COPYING.LGPL",
        "COPYING.MINPACK",
        "COPYING.MPL2",
        "COPYING.README",
    ]
    licenses: list[dict[str, object]] = []
    for name in license_names:
        self._put_repo(f"thirdparty/{name}", f"synthetic {name}\n".encode())
        licenses.append(
            {
                "archive_path": name,
                "file": self.offline(self.fi("repository", f"thirdparty/{name}")),
            }
        )
    eigen_entries_reference = [
        identity
        for identity in self.entries("reference-source")
        if str(identity["relative_path"]).startswith("thirdparty/Eigen/")
    ]
    eigen_sha = VERIFY._offline_tree_sha256(
        eigen_entries_reference, "thirdparty/Eigen"
    )
    v2_manifest = {
        "classification": {
            "eligible_as_build_input": True,
            "execution_eligible": False,
            "offline_build_inputs_authenticated": True,
            "provenance_complete": True,
            "scientific_execution_authorized": False,
        },
        "cmake": {},
        "limits": VERIFY.OFFLINE_V2_LIMITS,
        "predecessor": {
            "manifest": self.offline(
                self.fi("repository", "thirdparty/dist/offline_build_inputs_v1.json")
            ),
            "schema_version": "dancers-offline-build-inputs-v1",
            "verifier": self.offline(
                self.fi(
                    "repository",
                    "scripts/comparison/verify_offline_build_inputs_v1.py",
                )
            ),
        },
        "schema_version": "dancers-offline-build-inputs-v2",
        "tree_digest": {
            "algorithm": "sha256",
            "domain": "dancers-offline-build-input-tree-v1",
            "entry_encoding": (
                "sha256(domain_utf8_nul || for each UTF-8 bytewise-sorted regular file: "
                "'file\\0' || uint64be(path_bytes) || path || uint64be(file_bytes) || "
                "sha256(file_bytes))"
            ),
        },
        "vendored_eigen": {
            "config": self.offline(
                self.fi("repository", "thirdparty/Eigen3Config.cmake")
            ),
            "config_version": self.offline(
                self.fi("repository", "thirdparty/Eigen3ConfigVersion.cmake")
            ),
            "licenses": licenses,
            "normalized_repository_modes": {
                "directory": 0o755,
                "regular": [0o644, 0o755],
            },
            "provenance_complete": True,
            "root": "thirdparty/Eigen",
            "selected_tree": {
                "archive_path": "Eigen",
                "regular_bytes": 6_115_017,
                "regular_file_count": 337,
                "sha256": eigen_sha,
            },
            "upstream_archive": {
                "archive": self.offline(self.fi("repository", eigen_archive_path)),
                "directory_count": 1,
                "format": {
                    "directory_mode": 0o755,
                    "file_modes": [0o664, 0o755],
                    "gid": 0,
                    "gname": "root",
                    "mtime": 1_629_319_318,
                    "pax_comment": "3147391d946bb4b6c68edd901f2add6ac1f31f8c",
                    "uid": 0,
                    "uname": "root",
                },
                "full_tree": {
                    "regular_bytes": 15_452_179,
                    "regular_file_count": 1_784,
                    "sha256": "1" * 64,
                },
                "member_count": 1_921,
                "regular_file_count": 1_784,
                "root_directory": "eigen-3147391d946bb4b6c68edd901f2add6ac1f31f8c",
                "source": {
                    "repository": "gitlab.com/libeigen/eigen",
                    "tag": "3.4.0",
                    "upstream_commit": "3147391d946bb4b6c68edd901f2add6ac1f31f8c",
                },
                "tar_stream_bytes": 1,
                "uncompressed_regular_bytes": 15_452_179,
            },
            "version": "3.4.0",
        },
    }
    self._put_repo("thirdparty/dist/offline_build_inputs_v2.json", _canonical(v2_manifest))

    self.dependency_source_outputs: dict[str, list[dict[str, object]]] = {
        "optimized": [],
        "reference": [],
    }
    extracted_by_name: dict[str, list[dict[str, object]]] = {}
    for name in ("eigen", "manif", "raylib"):
        extracted: list[dict[str, object]] = []
        for method in ("optimized", "reference"):
            if name == "eigen":
                root_id = f"{method}-source"
                relative_root = "thirdparty/Eigen"
                entries = [
                    identity
                    for identity in self.entries(root_id)
                    if str(identity["relative_path"]).startswith(relative_root + "/")
                ]
                upstream_sha = eigen_sha
            else:
                root_id = f"{method}-build"
                relative_root = f"_deps/{name}-src"
                entries = []
                for relative, raw in archive_specs[name]["files"]:
                    target = f"{relative_root}/{relative}"
                    _write(self.paths[root_id] / target, raw)
                    identity = self.fi(root_id, target)
                    entries.append(identity)
                    self.dependency_source_outputs[method].append(identity)
                upstream_sha = _offline_digest(list(archive_specs[name]["files"]))
            entries.sort(
                key=lambda item: f"{item['root_id']}:{item['relative_path']}".encode()
            )
            extracted.append(
                {
                    "method": method,
                    "root_id": root_id,
                    "relative_path": relative_root,
                    "entries": entries,
                    "upstream_tree_sha256": upstream_sha,
                }
            )
        extracted_by_name[name] = extracted

    def source_roots(name: str) -> list[dict[str, object]]:
        return [
            {
                "method": record["method"],
                "root_id": record["root_id"],
                "relative_path": record["relative_path"],
            }
            for record in extracted_by_name[name]
        ]

    eigen_archive = self.fi("repository", eigen_archive_path)
    self.dependencies = {
        "offline_build_inputs_manifest": self.fi(
            "repository", "thirdparty/dist/offline_build_inputs_v2.json"
        ),
        "eigen_cmake_config": self.fi(
            "repository", "thirdparty/Eigen3Config.cmake"
        ),
        "eigen_cmake_config_version": self.fi(
            "repository", "thirdparty/Eigen3ConfigVersion.cmake"
        ),
        "eigen": {
            "source_kind": "vendored-tree",
            "version": "3.4.0",
            "immutable_revision": "3147391d946bb4b6c68edd901f2add6ac1f31f8c",
            "archive": eigen_archive,
            "extracted_tree": extracted_by_name["eigen"],
            "cmake_source_root": source_roots("eigen"),
            "nested_dependencies": [],
        },
    }
    for name in ("manif", "raylib"):
        spec = archive_specs[name]
        self.dependencies[name] = {
            "source_kind": "offline-archive",
            "version": spec["version"],
            "immutable_revision": spec["revision"],
            "archive": self.fi("repository", str(spec["path"])),
            "extracted_tree": extracted_by_name[name],
            "cmake_source_root": source_roots(name),
            "nested_dependencies": [],
        }

    prefix_entries = self.entries("toolchain-prefix")
    package_archive = self.fi("repository", "packages/toolchain.conda")
    trees = {
        "pixi-package-archives": [package_archive],
        "pixi-extracted-prefix": prefix_entries,
        "cmake-module-tree": [
            self.fi("toolchain-prefix", "share/cmake/module.cmake")
        ],
        "compiler-resource-tree": [
            self.fi("toolchain-prefix", "lib/clang/resource.txt")
        ],
        "libcxx-headers-and-libraries": [
            self.fi("toolchain-prefix", "include/c++/v1/vector"),
            self.fi("toolchain-prefix", "lib/libc++.dylib"),
        ],
        "compiler-rt": [
            self.fi("toolchain-prefix", "lib/clang/libcompiler_rt.a")
        ],
        "libomp": [self.fi("toolchain-prefix", "lib/libomp.dylib")],
    }
    self.toolchain = {
        "package_closure": {
            "pixi_lock": self.fi("repository", "pixi.lock"),
            "packages": [
                {
                    "locked_url": self.package_url,
                    "locked_sha256": package_archive["sha256"],
                    "archive": package_archive,
                    "extracted_entries": prefix_entries,
                    "extracted_tree_sha256": VERIFY.tree_sha256(prefix_entries),
                }
            ],
            "prefix_entries": prefix_entries,
            "prefix_tree_sha256": VERIFY.tree_sha256(prefix_entries),
        },
        "tools": [self.tools[role] for role in sorted(self.tools)],
        "trees": [
            {
                "role": role,
                "entries": sorted(
                    entries,
                    key=lambda item: f"{item['root_id']}:{item['relative_path']}".encode(),
                ),
                "tree_sha256": VERIFY.tree_sha256(
                    sorted(
                        entries,
                        key=lambda item: f"{item['root_id']}:{item['relative_path']}".encode(),
                    )
                ),
            }
            for role, entries in sorted(trees.items())
        ],
    }
    sdk_entries = [
        self.fi("sdk-root", "usr/include/stdio.h"),
        self.fi("sdk-root", "usr/lib/libSystem.tbd"),
    ]
    self.sdk = {
        "root": "sdk-root",
        "sdk_settings": self.fi("sdk-root", "SDKSettings.json"),
        "sysroot_argument": str(self.paths["sdk-root"]),
        "deployment_target": "13.0",
        "architectures": ["arm64"],
        "platform": "macos",
        "link_input_closure_manifest": {
            "entries": sdk_entries,
            "tree_sha256": VERIFY.tree_sha256(sdk_entries),
        },
    }
    variables = {
        "PATH": str(self.paths["toolchain-prefix"] / "bin"),
        "HOME": str(self.paths["private-home"]),
        "TMPDIR": str(self.paths["private-tmp"]),
        "LANG": "C",
        "LC_ALL": "C",
        "TZ": "UTC",
        "SOURCE_DATE_EPOCH": "0",
        "ZERO_AR_DATE": "1",
        "CC": str(
            self.paths["toolchain-prefix"]
            / str(self.tools["c-driver"]["file"]["relative_path"])
        ),
        "CXX": str(
            self.paths["toolchain-prefix"]
            / str(self.tools["cxx-driver"]["file"]["relative_path"])
        ),
        "SDKROOT": str(self.paths["sdk-root"]),
        "MACOSX_DEPLOYMENT_TARGET": "13.0",
        "OMP_NUM_THREADS": "1",
        "OMP_DYNAMIC": "FALSE",
        "OMP_WAIT_POLICY": "PASSIVE",
    }
    self.environment_id = VERIFY.environment_id(variables)
    self.environments = [
        {"environment_id": self.environment_id, "variables": variables}
    ]


SyntheticFixture._prepare_dependencies_impl = _fixture_prepare_dependencies_impl


def _fixture_prepare_actions_impl(self: SyntheticFixture) -> None:
    sdk_root = str(self.paths["sdk-root"])
    runtime_root = str(self.paths["runtime-artifact-root"])

    def ordered(identities: list[dict[str, object]]) -> list[dict[str, object]]:
        return sorted(
            identities,
            key=lambda item: f"{item['root_id']}:{item['relative_path']}".encode(),
        )

    self.actions: list[dict[str, object]] = []

    def action(
        *,
        kind: str,
        method: str,
        target: str,
        tool_role: str,
        cwd_root_id: str,
        argv: list[str],
        declared: list[dict[str, object]],
        discovered: list[dict[str, object]],
        outputs: list[dict[str, object]],
        stdout: str = "",
        response_files: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        record_without_id = {
            "sequence_index": len(self.actions),
            "kind": kind,
            "method": method,
            "target": target,
            "cwd_root_id": cwd_root_id,
            "cwd_relative_path": ".",
            "executable": self.tools[tool_role]["file"],
            "argv": argv,
            "environment_id": self.environment_id,
            "stdin": "closed",
            "response_files": response_files or [],
            "declared_inputs": ordered(declared),
            "discovered_inputs": ordered(discovered),
            "outputs": ordered(outputs),
            "exit_code": 0,
            "stdout": _stream(stdout),
            "stderr": _stream(""),
            "network_session_id": "synthetic-network-session",
        }
        complete = {
            **record_without_id,
            "action_id": VERIFY.action_id(record_without_id),
        }
        self.actions.append(complete)
        return complete

    for index, role in enumerate(self.TOOL_ROLES):
        method = "optimized" if index % 2 == 0 else "reference"
        relative = f"probes/{role}.txt"
        _write(self.paths[f"{method}-build"] / relative, f"probe {role}\n".encode())
        action(
            kind="compiler-probe",
            method=method,
            target=f"tool-version:{role}",
            tool_role=role,
            cwd_root_id=f"{method}-build",
            argv=list(self.tools[role]["version_argv"]),
            declared=[],
            discovered=[],
            outputs=[self.fi(f"{method}-build", relative)],
            stdout=f"{role} synthetic 1\n",
        )

    configure_inputs = [
        self.fi("repository", "CMakeLists.txt"),
        self.fi("repository", "pixi.toml"),
        self.fi("repository", "pixi.lock"),
        self.fi("repository", "thirdparty/dist/offline_build_inputs_v2.json"),
        self.fi("repository", "thirdparty/Eigen3Config.cmake"),
        self.fi("repository", "thirdparty/Eigen3ConfigVersion.cmake"),
    ]
    for method in ("optimized", "reference"):
        marker_relative = "configure/configured.txt"
        _write(self.paths[f"{method}-build"] / marker_relative, b"configured\n")
        outputs = [
            *self.dependency_source_outputs[method],
            self.fi(f"{method}-build", marker_relative),
        ]
        cmake_path = str(
            self.paths["toolchain-prefix"]
            / str(self.tools["cmake"]["file"]["relative_path"])
        )
        action(
            kind="configure",
            method=method,
            target=f"configure-{method}",
            tool_role="cmake",
            cwd_root_id=f"{method}-build",
            argv=[
                cmake_path,
                str(self.paths[f"{method}-source"]),
                "-DCMAKE_BUILD_TYPE=Release",
                "-DCMAKE_OSX_ARCHITECTURES=arm64",
                f"-DCMAKE_OSX_SYSROOT={sdk_root}",
                "-DCMAKE_OSX_DEPLOYMENT_TARGET=13.0",
                f"-DDANCERS_RUNTIME_ARTIFACT_ROOT={runtime_root}",
            ],
            declared=configure_inputs,
            discovered=[],
            outputs=outputs,
        )

    common_header_reference = self.fi("reference-source", "include/common.hpp")
    common_header_optimized = self.fi("optimized-source", "include/common.hpp")
    closure_records: dict[str, list[dict[str, object]]] = {}

    def closure(entries: list[dict[str, object]]) -> str:
        values = ordered(entries)
        digest = VERIFY.dependency_closure_sha256(values)
        closure_records[digest] = values
        return digest

    self.objects_by_label: dict[str, dict[str, object]] = {}

    def compile_object(
        *,
        label: str,
        role: str,
        method: str,
        sharing: str,
        source: dict[str, object],
        header: dict[str, object],
        output_root: str,
        output_relative: str,
        kind: str = "compile",
        extra_declared: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        _write(self.paths[output_root] / output_relative, _macho(1))
        output = self.fi(output_root, output_relative)
        tool_role = "linker" if kind == "aggregate-object" else "cxx-driver"
        executable_path = str(
            self.paths["toolchain-prefix"]
            / str(self.tools[tool_role]["file"]["relative_path"])
        )
        argv = [
            executable_path,
            "-arch",
            "arm64",
            "-isysroot",
            sdk_root,
            "-mmacosx-version-min=13.0",
        ]
        if kind == "compile":
            argv.append(f"-DDANCERS_RUNTIME_ARTIFACT_ROOT={runtime_root}")
        argv.extend(["-c", str(self.paths[str(source["root_id"])] / str(source["relative_path"])), "-o", str(self.paths[output_root] / output_relative)])
        producer = action(
            kind=kind,
            method=method,
            target=label,
            tool_role=tool_role,
            cwd_root_id=output_root,
            argv=argv,
            declared=[source, *(extra_declared or [])],
            discovered=[header],
            outputs=[output],
        )
        without_object_id = {
            "role": role,
            "method": method,
            "source": source,
            "producer_action_id": producer["action_id"],
            "compile_argv_sha256": _sha(_canonical(argv)),
            "environment_id": self.environment_id,
            "dependency_closure_sha256": closure([header]),
            "object": output,
            "sharing_scope": sharing,
        }
        object_record = {
            **without_object_id,
            "object_id": VERIFY.object_id(without_object_id),
        }
        self.objects_by_label[label] = object_record
        return object_record

    for role, relative in self.COMMON_SOURCES.items():
        compile_object(
            label=f"common-{role}",
            role=role,
            method="common",
            sharing="all-products",
            source=self.fi("reference-source", relative),
            header=common_header_reference,
            output_root="reference-build",
            output_relative=f"objects/common/{role}.o",
        )

    aggregate_records: list[dict[str, object]] = []
    for method, header in (
        ("optimized", common_header_optimized),
        ("reference", common_header_reference),
    ):
        source = self.fi(f"{method}-source", "src/controller.cpp")
        translation = compile_object(
            label=f"{method}-controller-tu",
            role="controller-translation-unit",
            method=method,
            sharing="private-intermediate",
            source=source,
            header=header,
            output_root=f"{method}-build",
            output_relative="objects/controller-tu.o",
        )
        aggregate = compile_object(
            label=f"{method}-controller-aggregate",
            role="controller-aggregate",
            method=method,
            sharing="method-products",
            source=source,
            header=header,
            output_root=f"{method}-build",
            output_relative="objects/controller-aggregate.o",
            kind="aggregate-object",
            extra_declared=[translation["object"]],
        )
        aggregate_records.append(
            {
                "method": method,
                "aggregate_object_id": aggregate["object_id"],
                "ordered_contributing_object_ids": [translation["object_id"]],
            }
        )
        compile_object(
            label=f"{method}-observer",
            role="fidelity-observer-evidence",
            method=method,
            sharing="fidelity-only",
            source=self.fi(f"{method}-source", "src/observer.cpp"),
            header=header,
            output_root=f"{method}-build",
            output_relative="objects/observer.o",
        )

    common_object_records = [
        self.objects_by_label[f"common-{role}"]
        for role in sorted(self.COMMON_SOURCES)
    ]
    library_relative = "lib/libcommon.a"
    _write(self.paths["reference-build"] / library_relative, b"synthetic archive\n")
    shared_library = self.fi("reference-build", library_relative)
    archiver_path = str(
        self.paths["toolchain-prefix"]
        / str(self.tools["archiver"]["file"]["relative_path"])
    )
    action(
        kind="archive",
        method="common",
        target="common-static-library",
        tool_role="archiver",
        cwd_root_id="reference-build",
        argv=[
            archiver_path,
            "rcs",
            str(self.paths["reference-build"] / library_relative),
            *[
                str(
                    self.paths[str(record["object"]["root_id"])]
                    / str(record["object"]["relative_path"])
                )
                for record in common_object_records
            ],
        ],
        declared=[record["object"] for record in common_object_records],
        discovered=[],
        outputs=[shared_library],
    )

    common_ids = [record["object_id"] for record in common_object_records]
    common_object_identities = [record["object"] for record in common_object_records]
    self.products: list[dict[str, object]] = []
    for method, product_role in (
        ("optimized", "fidelity"),
        ("optimized", "timing"),
        ("reference", "fidelity"),
        ("reference", "timing"),
    ):
        aggregate = self.objects_by_label[f"{method}-controller-aggregate"]
        observer = self.objects_by_label[f"{method}-observer"]
        object_records = [*common_object_records, aggregate]
        if product_role == "fidelity":
            object_records.append(observer)
        object_ids = [record["object_id"] for record in object_records]
        object_identities = [record["object"] for record in object_records]
        response_relative = f"responses/{method}-{product_role}.rsp"
        response_tokens = [
            *[
                str(
                    self.paths[str(identity["root_id"])]
                    / str(identity["relative_path"])
                )
                for identity in object_identities
            ],
            str(self.paths["reference-build"] / library_relative),
        ]
        self._put_repo(response_relative, (" ".join(response_tokens) + "\n").encode())
        response_identity = self.fi("repository", response_relative)
        binary_relative = f"products/{product_role}"
        binary_raw = _macho(2, runtime_root.encode())
        _write(self.paths[f"{method}-build"] / binary_relative, binary_raw, 0o755)
        binary = self.fi(f"{method}-build", binary_relative)
        cxx_path = str(
            self.paths["toolchain-prefix"]
            / str(self.tools["cxx-driver"]["file"]["relative_path"])
        )
        response_argument = "@" + str(self.repo / response_relative)
        link = action(
            kind="link",
            method=method,
            target=f"{method}-{product_role}",
            tool_role="cxx-driver",
            cwd_root_id=f"{method}-build",
            argv=[
                cxx_path,
                "-arch",
                "arm64",
                "-isysroot",
                sdk_root,
                "-mmacosx-version-min=13.0",
                response_argument,
            ],
            declared=[response_identity, *object_identities, shared_library],
            discovered=[self.fi("sdk-root", "usr/lib/libSystem.tbd")],
            outputs=[binary],
            response_files=[
                {"argument": response_argument, "file": response_identity}
            ],
        )
        self.products.append(
            {
                "method": method,
                "role": product_role,
                "binary": binary,
                "link_action_id": link["action_id"],
                "ordered_object_ids": object_ids,
                "ordered_library_inputs": [shared_library],
                "macho_load_commands": VERIFY._parse_macho(binary_raw, "product"),
                "resolved_non_system_dynamic_inputs": [],
                "observer_mode": "present" if product_role == "fidelity" else "absent",
                "runtime_artifact_root": "runtime-artifact-root",
            }
        )

    objects = sorted(self.objects_by_label.values(), key=lambda item: item["object_id"])
    self.object_graph = {
        "schema_version": VERIFY.OBJECT_GRAPH_SCHEMA,
        "dependency_closures": [
            {"dependency_closure_sha256": digest, "inputs": values}
            for digest, values in sorted(closure_records.items())
        ],
        "objects": objects,
        "aggregates": sorted(aggregate_records, key=lambda item: item["method"]),
    }
    self.common_objects = {
        "roles": {
            role: self.objects_by_label[f"common-{role}"]["object_id"]
            for role in self.COMMON_SOURCES
        },
        "controller_aggregates": {
            method: self.objects_by_label[f"{method}-controller-aggregate"]["object_id"]
            for method in ("optimized", "reference")
        },
        "registered_fidelity_observer_object_ids": {
            method: [self.objects_by_label[f"{method}-observer"]["object_id"]]
            for method in ("optimized", "reference")
        },
    }
    self.action_ledger = {
        "schema_version": VERIFY.ACTION_LEDGER_SCHEMA,
        "network_session_id": "synthetic-network-session",
        "wrapper": self.fi("repository", "tools/wrapper"),
        "actions": self.actions,
        "ledger_sha256": VERIFY.action_ledger_sha256(self.actions),
    }


SyntheticFixture._prepare_actions_impl = _fixture_prepare_actions_impl


def _fixture_prepare_manifest_impl(self: SyntheticFixture) -> None:
    identity_paths = {
        "build_provenance_contract": "docs/simulator/physics_build_provenance_contract_v1.json",
        "execution_design": "docs/simulator/physics_execution_design_epoch_1_v1.json",
        "physics_registry": "docs/simulator/physics_stress_test_v1.json",
        "cmake_lists": "CMakeLists.txt",
        "pixi_toml": "pixi.toml",
        "pixi_lock": "pixi.lock",
        "reference_source_identity": "source-identities/reference.json",
        "optimized_source_identity": "source-identities/optimized.json",
        "kinematic_fixture": "fixtures/kinematic.json",
        "kinematic_expected_golden_digest": "fixtures/kinematic.digest",
        "object_split_contract": "docs/simulator/object_split.json",
        "build_executor": "tools/executor",
        "action_wrapper": "tools/wrapper",
        "tree_hasher": "tools/tree-hasher",
        "macho_parser": "tools/macho-parser",
        "provenance_verifier": "scripts/comparison/verify_physics_build_provenance_v1.py",
        "offline_build_inputs_manifest": "thirdparty/dist/offline_build_inputs_v2.json",
        "offline_build_inputs_verifier": "scripts/comparison/verify_offline_build_inputs_v2.py",
    }
    trust_identity_paths = {
        "provenance_verifier": "scripts/comparison/verify_physics_build_provenance_v1.py",
        "hermetic_build_executor": "tools/executor",
        "action_wrapper": "tools/wrapper",
        "tree_and_package_closure_generator": "tools/tree-hasher",
        "macho_parser": "tools/macho-parser",
        "network_guard": "tools/network-guard",
        "network_policy": "tools/network-policy",
        "permitted_host_os_mechanism": "tools/host-mechanism",
        "canonicalization_and_size_policy": "tools/canonical-policy",
    }
    trust = {
        "schema_version": VERIFY.TRUST_ROOT_SCHEMA,
        "epoch_id": VERIFY.EPOCH_ID,
        "classification": {
            "execution_authorized": False,
            "outcome_independent": True,
            "prospective": True,
            "scientific_evidence": False,
            "scientific_evidence_eligible": False,
        },
        "limits": VERIFY.LIMITS,
        "root_paths": [
            {"root_id": root_id, "canonical_absolute_path": str(path)}
            for root_id, path in sorted(self.paths.items())
        ],
        "identities": [
            {"name": name, "file": self.fi("repository", relative)}
            for name, relative in sorted(trust_identity_paths.items())
        ],
        "network_backend": {
            "backend": "synthetic-seatbelt",
            "guard_identity": "network_guard",
            "policy_identity": "network_policy",
            "host_mechanism_identity": "permitted_host_os_mechanism",
        },
    }
    trust_relative = "trust/build-trust-root.json"
    self._put_repo(trust_relative, _canonical(trust))
    identity_paths["build_trust_root"] = trust_relative
    self.identity_bindings = {
        name: self.fi("repository", relative)
        for name, relative in identity_paths.items()
    }

    self.root_records: list[dict[str, object]] = []
    complete_roots = {
        "reference-source",
        "optimized-source",
        "reference-build",
        "optimized-build",
        "toolchain-prefix",
        "sdk-root",
        "private-home",
        "private-tmp",
    }
    for root_id, path in sorted(self.paths.items()):
        if root_id == "runtime-artifact-root":
            entries: list[dict[str, object]] = []
        else:
            entries = self.entries(root_id)
        complete = root_id in complete_roots
        info = path.stat()
        self.root_records.append(
            {
                "root_id": root_id,
                "canonical_absolute_path": str(path),
                "owner_uid": info.st_uid,
                "mode": stat.S_IMODE(info.st_mode),
                "tree_manifest": {
                    "schema_version": VERIFY.ROOT_TREE_SCHEMA,
                    "complete": complete,
                    "entries": entries,
                },
                "entry_count": len(entries),
                "total_regular_bytes": sum(int(item["bytes"]) for item in entries),
                "tree_sha256": VERIFY.tree_sha256(entries),
            }
        )
    roots_by_id = {record["root_id"]: record for record in self.root_records}

    ledger_sha = self.action_ledger["ledger_sha256"]
    root_process = {
        "pid": 900,
        "parent_pid": 1,
        "executable": self.fi("repository", "tools/executor"),
        "wrapper": self.fi("repository", "tools/wrapper"),
        "start_ns": 1_100,
        "end_ns": 999_000,
        "exit_code": 0,
    }
    processes: list[dict[str, object]] = []
    for index, build_action in enumerate(self.actions):
        wrapper_pid = 1_000 + 2 * index
        child_pid = wrapper_pid + 1
        wrapper_start = 2_000 + 100 * index
        processes.append(
            {
                "process_index": len(processes),
                "process_kind": "action-wrapper",
                "pid": wrapper_pid,
                "parent_pid": root_process["pid"],
                "action_id": build_action["action_id"],
                "executable": self.fi("repository", "tools/wrapper"),
                "start_ns": wrapper_start,
                "end_ns": wrapper_start + 80,
                "exit_code": 0,
            }
        )
        processes.append(
            {
                "process_index": len(processes),
                "process_kind": "action-child",
                "pid": child_pid,
                "parent_pid": wrapper_pid,
                "action_id": build_action["action_id"],
                "executable": build_action["executable"],
                "start_ns": wrapper_start + 10,
                "end_ns": wrapper_start + 60,
                "exit_code": 0,
            }
        )
    canaries = [
        {
            "address": "127.0.0.1",
            "errno": "EPERM",
            "family": "AF_INET",
            "operation": "connect",
            "outcome": "denied",
            "port": 9,
            "transport": "SOCK_STREAM",
        },
        {
            "address": "::1",
            "errno": "EPERM",
            "family": "AF_INET6",
            "operation": "connect",
            "outcome": "denied",
            "port": 9,
            "transport": "SOCK_STREAM",
        },
    ]
    network = {
        "schema_version": VERIFY.NETWORK_RECEIPT_SCHEMA,
        "epoch_id": VERIFY.EPOCH_ID,
        "session_id": "synthetic-network-session",
        "backend": "synthetic-seatbelt",
        "host_identity": self.fi("repository", "host/identity.json"),
        "executor": self.fi("repository", "tools/executor"),
        "guard": self.fi("repository", "tools/network-guard"),
        "policy": self.fi("repository", "tools/network-policy"),
        "root_process": root_process,
        "monotonic_interval": {"start_ns": 1_000, "end_ns": 1_000_000},
        "action_ledger_sha256": ledger_sha,
        "process_ledger": processes,
        "preflight_canaries": copy.deepcopy(canaries),
        "postflight_canaries": copy.deepcopy(canaries),
        "network_device_or_policy_state": {
            "interfaces_before_sha256": "2" * 64,
            "interfaces_after_sha256": "2" * 64,
            "policy_state_sha256": "3" * 64,
            "preflight_empty_build_roots": ["optimized-build", "reference-build"],
            "postflight_build_tree_sha256": {
                root_id: roots_by_id[root_id]["tree_sha256"]
                for root_id in ("optimized-build", "reference-build")
            },
        },
        "outcome": "enforced",
    }
    self.manifest = {
        "schema_version": VERIFY.SCHEMA_VERSION,
        "epoch_id": VERIFY.EPOCH_ID,
        "classification": copy.deepcopy(VERIFY.CLASSIFICATION),
        "identity_bindings": self.identity_bindings,
        "roots": self.root_records,
        "build_policy": copy.deepcopy(VERIFY.BUILD_POLICY),
        "environments": self.environments,
        "toolchain": self.toolchain,
        "sdk": self.sdk,
        "dependencies": self.dependencies,
        "action_ledger": self.action_ledger,
        "network_isolation": network,
        "object_graph": self.object_graph,
        "common_objects": self.common_objects,
        "products": self.products,
    }
    self.trust_path = self.repo / trust_relative
    self.provenance_path = self.parent / "synthetic-provenance.json"
    self.write_manifest(self.manifest)

    self.required_blockers = [
        "capture-profile-revision-required",
        "frozen-contract-offline-v2-profile-revision-required",
        "embedded-ledger-16mib-profile-limit",
        "normalized-no-symlink-staged-prefix-absent",
        "independent-protected-capture-absent",
        "offline-v2-semantic-verdict-not-bound-or-recomputed",
        "generation-aware-action-ledger-revision-required",
        "fork-exec-exit-event-ledger-revision-required",
        "complete-helper-action-surface-revision-required",
        "complete-network-canary-matrix-revision-required",
        "complete-link-line-resolution-profile-required",
        "complete-action-argv-grammar-profile-required",
        "recursive-dynamic-resolution-profile-required",
        "production-macho-semantic-profile-required",
        "bounded-authentication-cache-profile-required",
        "root-alias-and-overlap-profile-revision-required",
        "private-home-and-tmp-lifecycle-profile-required",
        "cross-method-environment-and-action-parity-profile-required",
        "prospective-input-identity-closure-not-trust-root-bound",
        "literal-common-object-superbuild-capture-absent",
        "test-attestation-absent",
        "external-execution-identity-manifest-absent",
        "execution-design-and-freezer-remain-locked",
    ]


def _fixture_write_manifest(
    self: SyntheticFixture, value: dict[str, object]
) -> tuple[int, str]:
    raw = _canonical(value)
    _write(self.provenance_path, raw, 0o600)
    return len(raw), _sha(raw)


def _fixture_verify(
    self: SyntheticFixture, value: dict[str, object] | None = None
) -> dict[str, object]:
    manifest = self.manifest if value is None else value
    size, digest = self.write_manifest(manifest)
    trust_raw = self.trust_path.read_bytes()
    return VERIFY.verify_build_provenance(
        self.provenance_path,
        expected_provenance_bytes=size,
        expected_provenance_sha256=digest,
        trust_root_path=self.trust_path,
        expected_trust_root_bytes=len(trust_raw),
        expected_trust_root_sha256=_sha(trust_raw),
    )


SyntheticFixture._prepare_manifest_impl = _fixture_prepare_manifest_impl
SyntheticFixture.write_manifest = _fixture_write_manifest
SyntheticFixture.verify = _fixture_verify


def _rehash_actions(manifest: dict[str, object]) -> None:
    ledger = manifest["action_ledger"]
    replacements: dict[str, str] = {}
    for action in ledger["actions"]:
        old = action["action_id"]
        action["action_id"] = VERIFY.action_id(
            {key: action[key] for key in VERIFY.ACTION_KEYS_WITHOUT_ID}
        )
        replacements[old] = action["action_id"]
    ledger["ledger_sha256"] = VERIFY.action_ledger_sha256(ledger["actions"])
    manifest["network_isolation"]["action_ledger_sha256"] = ledger["ledger_sha256"]
    for process in manifest["network_isolation"]["process_ledger"]:
        process["action_id"] = replacements.get(process["action_id"], process["action_id"])
    for record in manifest["object_graph"]["objects"]:
        record["producer_action_id"] = replacements.get(
            record["producer_action_id"], record["producer_action_id"]
        )
    for product in manifest["products"]:
        product["link_action_id"] = replacements.get(
            product["link_action_id"], product["link_action_id"]
        )
    object_replacements: dict[str, str] = {}
    for record in manifest["object_graph"]["objects"]:
        old = record["object_id"]
        record["object_id"] = VERIFY.object_id(
            {key: record[key] for key in VERIFY.OBJECT_KEYS if key != "object_id"}
        )
        object_replacements[old] = record["object_id"]
    manifest["object_graph"]["objects"].sort(key=lambda item: item["object_id"])
    for aggregate in manifest["object_graph"]["aggregates"]:
        aggregate["aggregate_object_id"] = object_replacements.get(
            aggregate["aggregate_object_id"], aggregate["aggregate_object_id"]
        )
        aggregate["ordered_contributing_object_ids"] = [
            object_replacements.get(value, value)
            for value in aggregate["ordered_contributing_object_ids"]
        ]
    common = manifest["common_objects"]
    common["roles"] = {
        role: object_replacements.get(value, value)
        for role, value in common["roles"].items()
    }
    common["controller_aggregates"] = {
        method: object_replacements.get(value, value)
        for method, value in common["controller_aggregates"].items()
    }
    common["registered_fidelity_observer_object_ids"] = {
        method: [object_replacements.get(value, value) for value in values]
        for method, values in common[
            "registered_fidelity_observer_object_ids"
        ].items()
    }
    for product in manifest["products"]:
        product["ordered_object_ids"] = [
            object_replacements.get(value, value)
            for value in product["ordered_object_ids"]
        ]


def _replace_file_identity(
    value: object,
    *,
    root_id: str,
    relative_path: str,
    replacement: dict[str, object],
) -> None:
    if type(value) is dict:
        if (
            value.get("root_id") == root_id
            and value.get("relative_path") == relative_path
            and set(VERIFY.FILE_IDENTITY_KEYS) <= set(value)
        ):
            value.clear()
            value.update(copy.deepcopy(replacement))
            return
        for child in list(value.values()):
            _replace_file_identity(
                child,
                root_id=root_id,
                relative_path=relative_path,
                replacement=replacement,
            )
    elif type(value) is list:
        for child in value:
            _replace_file_identity(
                child,
                root_id=root_id,
                relative_path=relative_path,
                replacement=replacement,
            )


def _refresh_root_record(manifest: dict[str, object], root_id: str) -> None:
    record = next(item for item in manifest["roots"] if item["root_id"] == root_id)
    entries = record["tree_manifest"]["entries"]
    record["entry_count"] = len(entries)
    record["total_regular_bytes"] = sum(item["bytes"] for item in entries)
    record["tree_sha256"] = VERIFY.tree_sha256(entries)


def _install_repository_bytes(
    fixture: SyntheticFixture,
    manifest: dict[str, object],
    relative_path: str,
    raw: bytes,
) -> None:
    path = fixture.repo / relative_path
    mode = stat.S_IMODE(path.stat().st_mode)
    _write(path, raw, mode)
    replacement = fixture.fi("repository", relative_path)
    _replace_file_identity(
        manifest,
        root_id="repository",
        relative_path=relative_path,
        replacement=replacement,
    )
    _refresh_root_record(manifest, "repository")
    _rehash_actions(manifest)


class PhysicsBuildProvenanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._temporary = tempfile.TemporaryDirectory()
        cls.fixture = SyntheticFixture(Path(cls._temporary.name).resolve())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._temporary.cleanup()

    def test_synthetic_data_consistent_fixture(self) -> None:
        verdict = self.fixture.verify()
        self.assertTrue(verdict["valid"])
        self.assertTrue(verdict["data_consistent"])
        self.assertFalse(verdict["causal_capture_verified"])
        self.assertFalse(verdict["provenance_complete"])
        self.assertFalse(verdict["promotion_eligible"])
        self.assertEqual(verdict["promotion_blockers"], self.fixture.required_blockers)

    def test_three_categorical_mutations_are_rejected(self) -> None:
        classification = copy.deepcopy(self.fixture.manifest)
        classification["classification"]["execution_authorized"] = True
        with self.assertRaisesRegex(VERIFY.VerificationError, "classification differs"):
            self.fixture.verify(classification)

        action = copy.deepcopy(self.fixture.manifest)
        action["action_ledger"]["actions"][0]["action_id"] = "0" * 64
        with self.assertRaisesRegex(VERIFY.VerificationError, "ID differs"):
            self.fixture.verify(action)

        network = copy.deepcopy(self.fixture.manifest)
        network["network_isolation"]["process_ledger"][1]["parent_pid"] = 999999
        with self.assertRaisesRegex(VERIFY.VerificationError, "parent"):
            self.fixture.verify(network)

    def test_cli_returns_two_for_authenticated_but_blocked_fixture(self) -> None:
        size, digest = self.fixture.write_manifest(self.fixture.manifest)
        trust_raw = self.fixture.trust_path.read_bytes()
        completed = subprocess.run(
            [
                sys.executable,
                str(REPOSITORY_ROOT / "scripts/comparison/verify_physics_build_provenance_v1.py"),
                "--provenance",
                str(self.fixture.provenance_path),
                "--expected-provenance-bytes",
                str(size),
                "--expected-provenance-sha256",
                digest,
                "--trust-root",
                str(self.fixture.trust_path),
                "--expected-trust-root-bytes",
                str(len(trust_raw)),
                "--expected-trust-root-sha256",
                _sha(trust_raw),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertEqual(completed.stderr, "")
        verdict = json.loads(completed.stdout)
        self.assertTrue(verdict["data_consistent"])
        self.assertFalse(verdict["causal_capture_verified"])
        self.assertFalse(verdict["provenance_complete"])
        self.assertFalse(verdict["promotion_eligible"])
        self.assertEqual(verdict["promotion_blockers"], self.fixture.required_blockers)

    def test_packager_is_deterministic_and_non_authorizing(self) -> None:
        draft = copy.deepcopy(self.fixture.manifest)
        for action in draft["action_ledger"]["actions"]:
            action.pop("action_id")
        draft["action_ledger"]["ledger_sha256"] = None
        draft["network_isolation"]["action_ledger_sha256"] = None
        first = CAPTURE.package_provenance(draft)
        second = CAPTURE.package_provenance(draft)
        self.assertEqual(CAPTURE.canonical_json_bytes(first), CAPTURE.canonical_json_bytes(second))
        self.assertEqual(
            first["action_ledger"]["ledger_sha256"],
            VERIFY.action_ledger_sha256(first["action_ledger"]["actions"]),
        )
        for action in first["action_ledger"]["actions"]:
            self.assertEqual(
                action["action_id"],
                VERIFY.action_id(
                    {key: action[key] for key in VERIFY.ACTION_KEYS_WITHOUT_ID}
                ),
            )
        self.assertFalse(first["classification"]["execution_authorized"])

        alias = copy.deepcopy(draft)
        alias["classification"]["execution_authorized"] = 0
        with self.assertRaisesRegex(CAPTURE.ProvenanceCaptureError, "classification"):
            CAPTURE.package_provenance(alias)

    def test_capture_publication_is_exclusive_and_rejects_official_roots(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary).resolve()
            parent.chmod(0o700)
            destination = parent / "capture.json"
            captured = CAPTURE.write_exclusive_canonical(destination, {"value": 1})
            self.assertEqual(captured.path, destination)
            self.assertEqual(stat.S_IMODE(destination.stat().st_mode), 0o400)
            self.assertFalse(
                any(path.name.startswith(".physics-build-provenance-stage-") for path in parent.iterdir())
            )
            with self.assertRaisesRegex(CAPTURE.ProvenanceCaptureError, "create-exclusive"):
                CAPTURE.write_exclusive_canonical(destination, {"value": 2})

            official_parent = (
                parent
                / "comparison/plans/simulator-demo/v1/physics-stress-test"
            )
            official_parent.mkdir(parents=True)
            official_parent.chmod(0o700)
            official_root = official_parent / "epoch-1"
            with self.assertRaisesRegex(CAPTURE.ProvenanceCaptureError, "official"):
                CAPTURE.write_exclusive_canonical(official_root, {"value": 1})
            official_root.mkdir()
            official_root.chmod(0o700)
            with self.assertRaisesRegex(CAPTURE.ProvenanceCaptureError, "official"):
                CAPTURE.write_exclusive_canonical(
                    official_root / "capture.json", {"value": 1}
                )
            self.assertFalse((official_root / "capture.json").exists())

            identity_parent = parent / "DOCS/SIMULATOR"
            identity_parent.mkdir(parents=True)
            identity_parent.chmod(0o700)
            identity_root = identity_parent / "PHYSICS_EXECUTION_IDENTITIES_EPOCH_1_V1.JSON"
            with self.assertRaisesRegex(CAPTURE.ProvenanceCaptureError, "official"):
                CAPTURE.write_exclusive_canonical(identity_root, {"value": 1})
            identity_root.mkdir()
            identity_root.chmod(0o700)
            with self.assertRaisesRegex(CAPTURE.ProvenanceCaptureError, "official"):
                CAPTURE.write_exclusive_canonical(identity_root / "child.json", {"value": 1})

    def test_capture_input_cap_plus_one_is_rejected(self) -> None:
        with self.assertRaisesRegex(CAPTURE.ProvenanceCaptureError, "input byte bound"):
            CAPTURE.parse_json_bytes(b" " * (CAPTURE.MAX_JSON_BYTES + 1))
        exact = CAPTURE.canonical_json_bytes("a" * 61, maximum_bytes=64)
        self.assertEqual(len(exact), 64)
        with self.assertRaisesRegex(CAPTURE.ProvenanceCaptureError, "64-byte bound"):
            CAPTURE.canonical_json_bytes("a" * 62, maximum_bytes=64)
        multibyte = CAPTURE.canonical_json_bytes("é" * 10, maximum_bytes=23)
        self.assertEqual(len(multibyte), 23)
        with self.assertRaisesRegex(CAPTURE.ProvenanceCaptureError, "22-byte bound"):
            CAPTURE.canonical_json_bytes("é" * 10, maximum_bytes=22)
        with self.assertRaisesRegex(CAPTURE.ProvenanceCaptureError, "oversized string token"):
            CAPTURE.canonical_json_bytes("x" * 65, maximum_bytes=64)
        for invalid_limit in (True, 64.0):
            with self.subTest(invalid_limit=invalid_limit):
                with self.assertRaisesRegex(CAPTURE.ProvenanceCaptureError, "positive integer"):
                    CAPTURE.canonical_json_bytes({}, maximum_bytes=invalid_limit)

        class IntSubclass(int):
            pass

        with self.assertRaisesRegex(CAPTURE.ProvenanceCaptureError, "positive integer"):
            CAPTURE.canonical_json_bytes({}, maximum_bytes=IntSubclass(64))

        identity = {
            "root_id": "repository",
            "relative_path": "identity.bin",
            "file_type": "regular",
            "mode": 0o400,
            "bytes": VERIFY.MAX_IDENTITY_FILE_BYTES,
            "sha256": "0" * 64,
        }
        self.assertEqual(
            VERIFY._validate_identity_file(identity, "identity")["bytes"],
            VERIFY.MAX_IDENTITY_FILE_BYTES,
        )
        identity["bytes"] += 1
        with self.assertRaisesRegex(VERIFY.VerificationError, "identity-file bound"):
            VERIFY._validate_identity_file(identity, "identity")

    def test_packager_recursively_rejects_later_stage_or_typed_authorization(self) -> None:
        numeric = copy.deepcopy(self.fixture.manifest)
        numeric["network_isolation"]["promotion_eligible"] = 1
        with self.assertRaisesRegex(CAPTURE.ProvenanceCaptureError, "boolean false"):
            CAPTURE.package_provenance(numeric)

        nested = copy.deepcopy(self.fixture.manifest)
        nested["network_isolation"]["nested"] = {
            "execution_identity_manifest": None
        }
        with self.assertRaisesRegex(CAPTURE.ProvenanceCaptureError, "later-stage"):
            CAPTURE.package_provenance(nested)

    def test_external_identities_and_canonical_manifest_are_fail_closed(self) -> None:
        size, digest = self.fixture.write_manifest(self.fixture.manifest)
        trust_raw = self.fixture.trust_path.read_bytes()
        kwargs = {
            "expected_provenance_bytes": size,
            "expected_provenance_sha256": digest,
            "trust_root_path": self.fixture.trust_path,
            "expected_trust_root_bytes": len(trust_raw),
            "expected_trust_root_sha256": _sha(trust_raw),
        }
        with self.assertRaisesRegex(VERIFY.VerificationError, "independent expected identity"):
            VERIFY.verify_build_provenance(
                self.fixture.provenance_path,
                **{**kwargs, "expected_provenance_sha256": "0" * 64},
            )
        with self.assertRaisesRegex(VERIFY.VerificationError, "trust-root bytes"):
            VERIFY.verify_build_provenance(
                self.fixture.provenance_path,
                **{**kwargs, "expected_trust_root_sha256": "0" * 64},
            )

        pretty = (json.dumps(self.fixture.manifest, indent=2, sort_keys=True) + "\n").encode()
        _write(self.fixture.provenance_path, pretty, 0o600)
        try:
            with self.assertRaisesRegex(VERIFY.VerificationError, "not canonical JSON"):
                VERIFY.verify_build_provenance(
                    self.fixture.provenance_path,
                    **{
                        **kwargs,
                        "expected_provenance_bytes": len(pretty),
                        "expected_provenance_sha256": _sha(pretty),
                    },
                )
        finally:
            self.fixture.write_manifest(self.fixture.manifest)

    def test_source_git_closure_mutation_is_rejected(self) -> None:
        relative = "source-identities/reference.json"
        path = self.fixture.repo / relative
        original = path.read_bytes()
        identity = json.loads(original)
        identity["git_commit"] = "0" * 40
        manifest = copy.deepcopy(self.fixture.manifest)
        try:
            _install_repository_bytes(
                self.fixture, manifest, relative, _canonical(identity)
            )
            with self.assertRaisesRegex(VERIFY.VerificationError, "commit object hash"):
                self.fixture.verify(manifest)
        finally:
            _write(path, original)

    def test_package_tool_and_sdk_mutations_are_rejected(self) -> None:
        package = copy.deepcopy(self.fixture.manifest)
        package["toolchain"]["package_closure"]["packages"][0][
            "locked_sha256"
        ] = "0" * 64
        with self.assertRaisesRegex(VERIFY.VerificationError, "pixi lock"):
            self.fixture.verify(package)

        tool = copy.deepcopy(self.fixture.manifest)
        tool["toolchain"]["tools"][0]["version_stdout_sha256"] = "0" * 64
        with self.assertRaisesRegex(VERIFY.VerificationError, "version probe"):
            self.fixture.verify(tool)

        sdk = copy.deepcopy(self.fixture.manifest)
        sdk["sdk"]["architectures"] = ["x86_64"]
        with self.assertRaisesRegex(VERIFY.VerificationError, "SDK platform"):
            self.fixture.verify(sdk)

        wrong_role = copy.deepcopy(self.fixture.manifest)
        configure = next(
            action
            for action in wrong_role["action_ledger"]["actions"]
            if action["kind"] == "configure"
        )
        old_action_id = configure["action_id"]
        replacement = self.fixture.tools["cxx-driver"]["file"]
        configure["executable"] = replacement
        configure["argv"][0] = str(
            self.fixture.paths[replacement["root_id"]] / replacement["relative_path"]
        )
        child = next(
            process
            for process in wrong_role["network_isolation"]["process_ledger"]
            if process["action_id"] == old_action_id
            and process["process_kind"] == "action-child"
        )
        child["executable"] = replacement
        _rehash_actions(wrong_role)
        with self.assertRaisesRegex(VERIFY.VerificationError, "wrong registered tool role"):
            self.fixture.verify(wrong_role)

    def test_action_input_output_and_unique_producer_mutations_are_rejected(self) -> None:
        wrong_archive = copy.deepcopy(self.fixture.manifest)
        archive = next(
            action
            for action in wrong_archive["action_ledger"]["actions"]
            if action["kind"] == "archive"
        )
        archive["method"] = "reference"
        _rehash_actions(wrong_archive)
        with self.assertRaisesRegex(VERIFY.VerificationError, "one common archive"):
            self.fixture.verify(wrong_archive)

        duplicate = copy.deepcopy(self.fixture.manifest)
        same_method = [
            action
            for action in duplicate["action_ledger"]["actions"]
            if action["kind"] == "compiler-probe" and action["method"] == "optimized"
        ]
        same_method[1]["outputs"] = copy.deepcopy(same_method[0]["outputs"])
        _rehash_actions(duplicate)
        with self.assertRaisesRegex(VERIFY.VerificationError, "multiple producers"):
            self.fixture.verify(duplicate)

        missing_input = copy.deepcopy(self.fixture.manifest)
        compile_action = next(
            action
            for action in missing_input["action_ledger"]["actions"]
            if action["kind"] == "compile"
        )
        compile_action["discovered_inputs"] = []
        _rehash_actions(missing_input)
        with self.assertRaisesRegex(VERIFY.VerificationError, "dependency closure|producer"):
            self.fixture.verify(missing_input)

        source_mismatch = copy.deepcopy(self.fixture.manifest)
        object_record = next(
            record
            for record in source_mismatch["object_graph"]["objects"]
            if record["role"] == "physics-engine"
        )
        producer = next(
            action
            for action in source_mismatch["action_ledger"]["actions"]
            if action["action_id"] == object_record["producer_action_id"]
        )
        old_source = object_record["source"]
        new_source = self.fixture.fi("reference-source", "src/adapter.cpp")
        object_record["source"] = new_source
        producer["declared_inputs"] = [
            new_source if identity == old_source else identity
            for identity in producer["declared_inputs"]
        ]
        producer["declared_inputs"].sort(
            key=lambda item: f"{item['root_id']}:{item['relative_path']}".encode("utf-8")
        )
        _rehash_actions(source_mismatch)
        with self.assertRaisesRegex(VERIFY.VerificationError, "exact source and output"):
            self.fixture.verify(source_mismatch)

    def test_network_session_interval_canary_and_helper_mutations_are_rejected(self) -> None:
        session = copy.deepcopy(self.fixture.manifest)
        session["network_isolation"]["session_id"] = "different-session"
        with self.assertRaisesRegex(VERIFY.VerificationError, "session differs"):
            self.fixture.verify(session)

        interval = copy.deepcopy(self.fixture.manifest)
        interval["network_isolation"]["process_ledger"][0]["end_ns"] = 1_000_001
        with self.assertRaisesRegex(VERIFY.VerificationError, "interval"):
            self.fixture.verify(interval)

        canary = copy.deepcopy(self.fixture.manifest)
        canary["network_isolation"]["preflight_canaries"][0]["port"] = 9.0
        with self.assertRaisesRegex(VERIFY.VerificationError, "canaries"):
            self.fixture.verify(canary)

        helper = copy.deepcopy(self.fixture.manifest)
        processes = helper["network_isolation"]["process_ledger"]
        processes[3]["process_kind"] = "registered-helper"
        processes[3]["parent_pid"] = processes[1]["pid"]
        processes[3]["executable"] = self.fixture.tools["linker"]["file"]
        with self.assertRaisesRegex(VERIFY.VerificationError, "crosses action ancestry"):
            self.fixture.verify(helper)

    def test_complete_build_roots_and_canonical_path_are_required(self) -> None:
        incomplete = copy.deepcopy(self.fixture.manifest)
        build_root = next(
            item for item in incomplete["roots"] if item["root_id"] == "optimized-build"
        )
        build_root["tree_manifest"]["complete"] = False
        with self.assertRaisesRegex(VERIFY.VerificationError, "complete final inventory"):
            self.fixture.verify(incomplete)

        escaped = copy.deepcopy(self.fixture.manifest)
        environment = escaped["environments"][0]
        environment["variables"]["PATH"] = (
            str(self.fixture.paths["toolchain-prefix"]) + "/../outside"
        )
        environment["environment_id"] = VERIFY.environment_id(environment["variables"])
        with self.assertRaisesRegex(VERIFY.VerificationError, "canonical absolute"):
            self.fixture.verify(escaped)

    def test_unregistered_physical_root_alias_is_rejected(self) -> None:
        trust_path = self.fixture.trust_path
        original = trust_path.read_bytes()
        manifest = copy.deepcopy(self.fixture.manifest)
        trust = json.loads(original)
        reference_path = str(self.fixture.paths["reference-build"])
        for item in trust["root_paths"]:
            if item["root_id"] == "optimized-build":
                item["canonical_absolute_path"] = reference_path

        reference = next(
            item for item in manifest["roots"] if item["root_id"] == "reference-build"
        )
        optimized = next(
            item for item in manifest["roots"] if item["root_id"] == "optimized-build"
        )
        optimized["canonical_absolute_path"] = reference_path
        optimized["owner_uid"] = reference["owner_uid"]
        optimized["mode"] = reference["mode"]
        optimized["tree_manifest"] = copy.deepcopy(reference["tree_manifest"])
        for identity in optimized["tree_manifest"]["entries"]:
            identity["root_id"] = "optimized-build"
        _refresh_root_record(manifest, "optimized-build")
        try:
            _install_repository_bytes(
                self.fixture,
                manifest,
                trust_path.relative_to(self.fixture.repo).as_posix(),
                _canonical(trust),
            )
            with self.assertRaisesRegex(VERIFY.VerificationError, "root alias"):
                self.fixture.verify(manifest)
        finally:
            _write(trust_path, original)

    def test_response_file_semantics_reject_network_and_conflicting_override(self) -> None:
        response_action = next(
            action
            for action in self.fixture.manifest["action_ledger"]["actions"]
            if action["response_files"]
        )
        relative = response_action["response_files"][0]["file"]["relative_path"]
        path = self.fixture.repo / relative
        original = path.read_bytes()
        try:
            network = copy.deepcopy(self.fixture.manifest)
            _install_repository_bytes(
                self.fixture, network, relative, original + b" https://example.invalid/source\n"
            )
            with self.assertRaisesRegex(VERIFY.VerificationError, "forbidden"):
                self.fixture.verify(network)

            _write(path, original)
            override = copy.deepcopy(self.fixture.manifest)
            _install_repository_bytes(
                self.fixture, override, relative, original + b" -arch x86_64\n"
            )
            with self.assertRaisesRegex(VERIFY.VerificationError, "exactly once"):
                self.fixture.verify(override)
        finally:
            _write(path, original)

        split = copy.deepcopy(self.fixture.manifest)
        configure = next(
            action
            for action in split["action_ledger"]["actions"]
            if action["kind"] == "configure"
        )
        configure["argv"].extend(["-D", "CMAKE_BUILD_TYPE=Debug"])
        _rehash_actions(split)
        with self.assertRaisesRegex(VERIFY.VerificationError, "exact frozen"):
            self.fixture.verify(split)

        relative_override = "responses/configure-override.rsp"
        override_path = self.fixture.repo / relative_override
        try:
            _write(override_path, b"-D CMAKE_BUILD_TYPE=Debug\n")
            response_override = copy.deepcopy(self.fixture.manifest)
            identity = self.fixture.fi("repository", relative_override)
            repository = next(
                item
                for item in response_override["roots"]
                if item["root_id"] == "repository"
            )
            repository["tree_manifest"]["entries"].append(identity)
            repository["tree_manifest"]["entries"].sort(
                key=lambda item: item["relative_path"].encode("utf-8")
            )
            _refresh_root_record(response_override, "repository")
            configure = next(
                action
                for action in response_override["action_ledger"]["actions"]
                if action["kind"] == "configure"
            )
            argument = "@" + str(override_path)
            configure["argv"].append(argument)
            configure["response_files"].append({"argument": argument, "file": identity})
            configure["declared_inputs"].append(identity)
            configure["declared_inputs"].sort(
                key=lambda item: f"{item['root_id']}:{item['relative_path']}".encode("utf-8")
            )
            _rehash_actions(response_override)
            with self.assertRaisesRegex(VERIFY.VerificationError, "exact frozen"):
                self.fixture.verify(response_override)
        finally:
            override_path.unlink(missing_ok=True)

    def test_bool_and_float_index_or_exit_aliases_are_rejected_after_rehash(self) -> None:
        false_index = copy.deepcopy(self.fixture.manifest)
        false_index["action_ledger"]["actions"][0]["sequence_index"] = False
        _rehash_actions(false_index)
        with self.assertRaisesRegex(VERIFY.VerificationError, "must be an integer"):
            self.fixture.verify(false_index)

        float_exit = copy.deepcopy(self.fixture.manifest)
        float_exit["action_ledger"]["actions"][0]["exit_code"] = 0.0
        _rehash_actions(float_exit)
        with self.assertRaisesRegex(VERIFY.VerificationError, "must be an integer"):
            self.fixture.verify(float_exit)

        false_process = copy.deepcopy(self.fixture.manifest)
        false_process["network_isolation"]["process_ledger"][0]["process_index"] = False
        with self.assertRaisesRegex(VERIFY.VerificationError, "must be an integer"):
            self.fixture.verify(false_process)

    def test_offline_v2_surface_is_exact_and_cross_bound(self) -> None:
        relative = "thirdparty/dist/offline_build_inputs_v2.json"
        path = self.fixture.repo / relative
        original = path.read_bytes()

        def rejected(mutator, message: str) -> None:
            document = json.loads(original)
            mutator(document)
            manifest = copy.deepcopy(self.fixture.manifest)
            _install_repository_bytes(self.fixture, manifest, relative, _canonical(document))
            with self.assertRaisesRegex(VERIFY.VerificationError, message):
                self.fixture.verify(manifest)
            _write(path, original)

        try:
            rejected(lambda value: value.__setitem__("extra", 1), "keys differ")
            rejected(
                lambda value: value["vendored_eigen"]["config_version"].__setitem__(
                    "sha256", "0" * 64
                ),
                "config-version",
            )
            rejected(
                lambda value: value["vendored_eigen"]["licenses"].reverse(),
                "license",
            )
        finally:
            _write(path, original)

    def test_product_object_observer_and_library_surfaces_are_closed(self) -> None:
        optimized_timing_index = next(
            index
            for index, product in enumerate(self.fixture.manifest["products"])
            if product["method"] == "optimized" and product["role"] == "timing"
        )
        observer = self.fixture.common_objects[
            "registered_fidelity_observer_object_ids"
        ]["optimized"][0]
        with_observer = copy.deepcopy(self.fixture.manifest)
        product = with_observer["products"][optimized_timing_index]
        product["ordered_object_ids"].append(observer)
        product["observer_mode"] = "present"
        with self.assertRaisesRegex(VERIFY.VerificationError, "timing product"):
            self.fixture.verify(with_observer)

        private_object = next(
            record["object_id"]
            for record in self.fixture.manifest["object_graph"]["objects"]
            if record["method"] == "optimized"
            and record["sharing_scope"] == "private-intermediate"
        )
        with_private = copy.deepcopy(self.fixture.manifest)
        with_private["products"][optimized_timing_index]["ordered_object_ids"].append(
            private_object
        )
        with self.assertRaisesRegex(VERIFY.VerificationError, "unclassified|private"):
            self.fixture.verify(with_private)

        extra_library = copy.deepcopy(self.fixture.manifest)
        reference_binary = next(
            product["binary"]
            for product in extra_library["products"]
            if product["method"] == "reference" and product["role"] == "fidelity"
        )
        extra_library["products"][optimized_timing_index][
            "ordered_library_inputs"
        ].append(reference_binary)
        with self.assertRaisesRegex(VERIFY.VerificationError, "absent from link action"):
            self.fixture.verify(extra_library)

        literal_extra = copy.deepcopy(self.fixture.manifest)
        product = literal_extra["products"][optimized_timing_index]
        link = next(
            action
            for action in literal_extra["action_ledger"]["actions"]
            if action["action_id"] == product["link_action_id"]
        )
        extra_identity = self.fixture.fi("repository", "CMakeLists.txt")
        link["argv"].append(str(self.fixture.repo / "CMakeLists.txt"))
        link["declared_inputs"].append(extra_identity)
        link["declared_inputs"].sort(
            key=lambda item: f"{item['root_id']}:{item['relative_path']}".encode("utf-8")
        )
        _rehash_actions(literal_extra)
        with self.assertRaisesRegex(VERIFY.VerificationError, "unclassified literal"):
            self.fixture.verify(literal_extra)


if __name__ == "__main__":
    unittest.main()
