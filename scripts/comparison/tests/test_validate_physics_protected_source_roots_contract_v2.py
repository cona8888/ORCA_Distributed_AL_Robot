#!/usr/bin/env python3
"""Temp-only and read-only regressions for protected source-roots contract v2."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import unittest


REPOSITORY = Path(__file__).resolve().parents[3]
VALIDATOR_PATH = REPOSITORY / "scripts/comparison/validate_physics_protected_source_roots_contract_v2.py"
CONTRACT_PATH = REPOSITORY / "docs/simulator/physics_protected_source_roots_contract_v2.json"
SOURCE_RECORD_PATH = REPOSITORY / "docs/simulator/physics_source_lift_record_epoch_1_v1.json"
SPEC = importlib.util.spec_from_file_location("protected_source_roots_v2", VALIDATOR_PATH)
assert SPEC is not None and SPEC.loader is not None
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)

GIT_ENV = {
    "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1",
    "GIT_OPTIONAL_LOCKS": "0", "GIT_PAGER": "cat",
    "GIT_TERMINAL_PROMPT": "0", "HOME": "/nonexistent",
    "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin",
}


def git(*arguments: str) -> bytes:
    return subprocess.check_output(
        ["/usr/bin/git", "--no-pager", "-C", str(REPOSITORY), *arguments],
        env=GIT_ENV, timeout=30,
    )


def projected_git_tree(entries) -> str:
    root = {}
    for entry in entries:
        node = root
        parts = entry["relative_path"].split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = entry

    def tree_oid(node) -> bytes:
        body = bytearray()
        for name in sorted(node, key=lambda item: item.encode("utf-8")):
            child = node[name]
            if "git_oid" in child:
                mode = child["git_mode"].encode("ascii")
                oid = bytes.fromhex(child["git_oid"])
            else:
                mode = b"40000"
                oid = tree_oid(child)
            body.extend(mode + b" " + name.encode("utf-8") + b"\0" + oid)
        return hashlib.sha1(b"tree " + str(len(body)).encode("ascii") + b"\0" + body).digest()

    return tree_oid(root).hex()


class ProtectedSourceRootsV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = CONTRACT_PATH.read_bytes()
        cls.contract = json.loads(cls.raw)
        cls.source_record = json.loads(SOURCE_RECORD_PATH.read_bytes())
        cls.indexes = [cls._reconstruct_index(spec) for spec in validator.ROOT_SPECS]

    @classmethod
    def _reconstruct_index(cls, spec):
        root_id = spec["root_id"]
        method_paths = set(
            cls.source_record["method_inventory"]["controller_aggregate_sources"]
            + cls.source_record["method_inventory"]["fidelity_observer_sources"]
        )
        common_paths = set(cls.source_record["common_source_projection"]["project_paths"])

        def collect(anchor):
            raw_tree = git("ls-tree", "-rz", "--full-tree", "-r", anchor["tree"])
            result = []
            for record in raw_tree.split(b"\0"):
                if not record:
                    continue
                metadata, encoded_path = record.split(b"\t", 1)
                mode, object_type, oid = metadata.decode("ascii").split(" ")
                path = encoded_path.decode("utf-8", "strict")
                selected = (
                    path.startswith("thirdparty/") or path in common_paths
                    if root_id == "common-source"
                    else path.startswith("inc/") or path in method_paths
                )
                if not selected:
                    continue
                blob = git("cat-file", "blob", oid)
                if hashlib.sha1(b"blob " + str(len(blob)).encode("ascii") + b"\0" + blob).hexdigest() != oid:
                    raise AssertionError("Git blob identity differs during read-only reconstruction")
                result.append({
                    "relative_path": path, "git_mode": mode, "git_type": object_type,
                    "git_oid": oid, "bytes": len(blob),
                    "sha256": hashlib.sha256(blob).hexdigest(),
                    "materialized_mode": 0o444 if mode == "100644" else 0o555,
                })
            return sorted(result, key=lambda item: item["relative_path"].encode("utf-8"))

        entries = collect(spec["endpoint_anchors"][0])
        for endpoint in spec["endpoint_anchors"][1:]:
            if collect(endpoint) != entries:
                raise AssertionError("projection differs between its frozen endpoint anchors")
        if projected_git_tree(entries) != spec["projected_git_tree"]:
            raise AssertionError("projected Git tree identity differs")
        compact = b"".join(
            ("%s %s %s %d %s\t%s\n" % (
                item["git_mode"], item["git_type"], item["git_oid"], item["bytes"],
                item["sha256"], item["relative_path"],
            )).encode("utf-8")
            for item in entries
        )
        path_list = b"".join((item["relative_path"] + "\n").encode("utf-8") for item in entries)
        if (len(compact), hashlib.sha256(compact).hexdigest()) != (spec["manifest_bytes"], spec["manifest_sha256"]):
            raise AssertionError("compact projection manifest differs")
        if hashlib.sha256(path_list).hexdigest() != spec["path_list_sha256"]:
            raise AssertionError("projection path list differs")
        index = {
            "source_index_id": "0" * 64,
            "schema_version": validator.SOURCE_INDEX_SCHEMA,
            "epoch_id": validator.EPOCH_ID,
            "root_id": root_id,
            "role": root_id,
            "endpoint_anchors": copy.deepcopy(spec["endpoint_anchors"]),
            "projection": {
                "selection": spec["selection"],
                "path_list_sha256": spec["path_list_sha256"],
                "manifest_bytes": spec["manifest_bytes"],
                "manifest_sha256": spec["manifest_sha256"],
            },
            "entries": entries,
            "entry_count": len(entries),
            "implicit_directory_count": spec["implicit_directory_count"],
            "total_regular_bytes": sum(item["bytes"] for item in entries),
            "projected_git_tree": spec["projected_git_tree"],
            "root_tree_sha256": spec["root_tree_sha256"],
        }
        index["source_index_id"] = validator.derive_source_index_id(index, validate=False)
        validator.validate_source_index(index)
        return index

    @staticmethod
    def _tree_entries(index):
        directories = set()
        for entry in index["entries"]:
            parent = PurePosixPath(entry["relative_path"]).parent
            while parent.as_posix() != ".":
                directories.add(parent.as_posix())
                parent = parent.parent
        root_id = index["root_id"]
        result = [{
            "root_id": root_id, "relative_path": "", "entry_type": "root",
            "mode": 0o555, "bytes": 0, "sha256": None,
        }]
        result.extend({
            "root_id": root_id, "relative_path": path, "entry_type": "directory",
            "mode": 0o555, "bytes": 0, "sha256": None,
        } for path in directories)
        result.extend({
            "root_id": root_id, "relative_path": entry["relative_path"],
            "entry_type": "regular", "mode": entry["materialized_mode"],
            "bytes": entry["bytes"], "sha256": entry["sha256"],
        } for entry in index["entries"])
        return [result[0]] + sorted(result[1:], key=lambda item: item["relative_path"].encode("utf-8"))

    def test_01_live_contract_is_canonical_and_nonauthorizing(self) -> None:
        verdict = validator.validate_contract_file(CONTRACT_PATH)
        self.assertEqual(len(self.raw), validator.CONTRACT_BYTES)
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), validator.CONTRACT_SHA256)
        self.assertTrue(verdict["valid"])
        for key, value in verdict["classification"].items():
            if key not in ():
                self.assertIs(value, False)
        self.assertFalse(verdict["production_roots_materialized"])
        self.assertFalse(verdict["build_input_handoff_present"])
        self.assertFalse(verdict["action_plan_present"])

    def test_02_all_three_indexes_and_typed_root_hashes_reconstruct(self) -> None:
        self.assertEqual([item["entry_count"] for item in self.indexes], [371, 53, 53])
        self.assertEqual([len(self._tree_entries(item)) for item in self.indexes], [426, 61, 61])
        for index, spec in zip(self.indexes, validator.ROOT_SPECS):
            self.assertEqual(validator.derive_root_tree_sha256(self._tree_entries(index)), spec["root_tree_sha256"])
            self.assertEqual(validator.validate_source_index(index)["source_index_id"], index["source_index_id"])

    def test_03_contract_mutations_fail_closed(self) -> None:
        mutations = {
            "authorization": lambda item: item["classification"].__setitem__("build_authorized", True),
            "production": lambda item: item["materialization_policy"].__setitem__("production_materialization_permitted", True),
            "old-mode": lambda item: item["materialization_policy"]["git_mode_to_materialized_mode"].__setitem__("100644", 0o400),
            "same-euid-claim": lambda item: item["materialization_policy"]["requirements"].remove("owner-controlled 0444 0555 modes do not protect against same-euid chmod or mutation and never establish production immutability"),
            "old-root-hash": lambda item: item["projected_roots"][0].__setitem__("root_tree_sha256", "456a535ad389b227de3266a9a63f5bd6564b79f2acadbfe618aac9d798b04d25"),
            "manifest-in-prior-handoff": lambda item: item["schemas"]["source_reference"]["exact_keys"].append("root_manifest_sha256"),
            "action-id": lambda item: item["handoff"].__setitem__("action_id_formula_frozen", True),
            "missing-type": lambda item: item["schemas"]["root_tree_entry"]["field_types"].pop("entry_type"),
            "changed-type": lambda item: item["schemas"]["root_tree_entry"]["field_types"].__setitem__("entry_type", "arbitrary nonempty prose"),
            "changed-schema-version": lambda item: item["schemas"]["root_tree_entry"].__setitem__("schema_version", "arbitrary"),
            "read-only-leaf-create": lambda item: item["materialization_policy"]["descriptor_requirements"].__setitem__(2, "regular leaves are opened descriptor-relatively with O_CREAT O_EXCL O_CLOEXEC and O_NOFOLLOW"),
            "missing-publication-parent-fsync": lambda item: item["materialization_policy"]["descriptor_requirements"].remove("the destination parent directory is fsynced after atomic no-replace publication and before final-name reauthentication or manifest issuance"),
            "missing-blocker": lambda item: item["current_blockers"].pop(),
            "unbounded-integer-profile": lambda item: item["canonical_json"].__setitem__("allowed_values", "all integers"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                changed = copy.deepcopy(self.contract)
                mutate(changed)
                with self.assertRaises(validator.ContractError):
                    validator.validate_contract_value(changed)

    def test_04_index_mutations_cannot_relabel_a_registered_root(self) -> None:
        mutations = {
            "role": lambda item: item.__setitem__("role", "reference-source"),
            "anchor": lambda item: item["endpoint_anchors"][0].__setitem__("tree", "0" * 40),
            "selection": lambda item: item["projection"].__setitem__("selection", "all files"),
            "count": lambda item: item.__setitem__("entry_count", item["entry_count"] - 1),
            "bytes": lambda item: item.__setitem__("total_regular_bytes", item["total_regular_bytes"] - 1),
            "git-tree": lambda item: item.__setitem__("projected_git_tree", "0" * 40),
            "root-tree": lambda item: item.__setitem__("root_tree_sha256", "0" * 64),
            "index-id": lambda item: item.__setitem__("source_index_id", "0" * 64),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                changed = copy.deepcopy(self.indexes[1])
                mutate(changed)
                with self.assertRaises(validator.ContractError):
                    validator.validate_source_index(changed)

    def test_05_typed_tree_rejects_missing_extra_empty_and_wrong_type_entries(self) -> None:
        baseline = self._tree_entries(self.indexes[1])
        changes = []
        missing = copy.deepcopy(baseline); missing.pop(1); changes.append(missing)
        extra = copy.deepcopy(baseline); extra.insert(1, {"root_id": "optimized-source", "relative_path": "empty", "entry_type": "directory", "mode": 0o555, "bytes": 0, "sha256": None}); changes.append(extra)
        wrong_root = copy.deepcopy(baseline); wrong_root[0]["relative_path"] = "."; changes.append(wrong_root)
        wrong_mode = copy.deepcopy(baseline); wrong_mode[-1]["mode"] = 0o400; changes.append(wrong_mode)
        metadata = copy.deepcopy(baseline); metadata[1]["sha256"] = "0" * 64; changes.append(metadata)
        for changed in changes:
            with self.assertRaises(validator.ContractError):
                validator.derive_root_tree_sha256(changed)
        with self.assertRaisesRegex(validator.ContractError, "nonempty list"):
            validator.derive_root_tree_sha256(baseline + [copy.deepcopy(baseline[-1]) for _ in range(513 - len(baseline))])

    def test_06_diagnostic_manifest_is_categorically_not_production(self) -> None:
        index = self.indexes[1]
        with tempfile.TemporaryDirectory() as temporary:
            info = os.stat(temporary)
            manifest = {
                "schema_version": validator.ROOT_MANIFEST_SCHEMA,
                "epoch_id": validator.EPOCH_ID,
                "root_id": index["root_id"],
                "canonical_absolute_path": str(Path(temporary).resolve()),
                "device": info.st_dev, "inode": info.st_ino, "mount_id": 0,
                "owner_uid": os.geteuid(), "root_mode": 0o555, "complete": True,
                "source_index_id": index["source_index_id"],
                "entries": self._tree_entries(index),
                "tree_entry_count": 61, "regular_file_count": 53,
                "implicit_directory_count": 7,
                "total_regular_bytes": index["total_regular_bytes"],
                "root_tree_sha256": index["root_tree_sha256"],
                "protection": {
                    "purpose": "diagnostic-fixture", "created_exclusively": True,
                    "single_writer_enforced": False, "read_only_enforced": False,
                    "symlinks_absent": True, "hardlinks_absent": True,
                    "published_atomically": False, "xattrs_absent": True,
                    "acls_absent": True, "bsd_flags_absent": True,
                    "resource_forks_absent": True, "special_files_absent": True,
                    "casefold_collisions_absent": True, "empty_directories_absent": True,
                    "extra_entries_absent": True,
                },
            }
            validator.validate_root_manifest(manifest, index, require_production=False)
            oversized_path = copy.deepcopy(manifest)
            oversized_path["canonical_absolute_path"] = "/" + "x" * validator.PATH_BYTES_MAX
            with self.assertRaisesRegex(validator.ContractError, "bounded canonical absolute path"):
                validator.validate_root_manifest(oversized_path, index, require_production=False)
            with self.assertRaisesRegex(validator.ContractError, "not production eligible"):
                validator.validate_root_manifest(manifest, index)
            production = copy.deepcopy(manifest)
            production["protection"]["purpose"] = "production-protected"
            for key in validator.PROTECTION_KEYS - {"purpose"}:
                production["protection"][key] = True
            validator.validate_root_manifest(production, index)

    def test_07_handoff_is_prior_acyclic_and_requires_verified_indexes(self) -> None:
        sources = [{
            "root_id": index["root_id"], "source_index_id": index["source_index_id"],
            "root_tree_sha256": index["root_tree_sha256"],
            "projected_git_tree": index["projected_git_tree"],
        } for index in self.indexes]
        handoff = {
            "source_input_handoff_sha256": "0" * 64,
            "schema_version": validator.HANDOFF_SCHEMA,
            "epoch_id": validator.EPOCH_ID,
            "classification": {"build_authorized": False, "capture_complete": False, "execution_authorized": False, "scientific_evidence": False},
            "root_order": [item["root_id"] for item in sources],
            "sources": sources,
        }
        handoff["source_input_handoff_sha256"] = validator.derive_source_input_handoff_sha256(handoff)
        validator.validate_source_input_handoff(handoff, self.indexes)
        self.assertNotIn("capture_id", handoff)
        self.assertNotIn("prospective_action_plan_sha256", handoff)
        self.assertNotIn("root_manifest_sha256", json.dumps(handoff, sort_keys=True))
        changed = copy.deepcopy(handoff); changed["sources"][0]["source_index_id"] = "f" * 64
        changed["source_input_handoff_sha256"] = validator.derive_source_input_handoff_sha256(changed)
        with self.assertRaisesRegex(validator.ContractError, "verified index"):
            validator.validate_source_input_handoff(changed, self.indexes)

    def test_08_strict_json_accepts_empty_root_path_but_rejects_bad_values(self) -> None:
        self.assertEqual(validator.parse_canonical_json(b'{"path":""}\n'), {"path": ""})
        malformed = [b'{"a":1,"a":2}\n', b'{"a":1.0}\n', b'{"a":NaN}\n', b'{ "a":1}\n', b'{"a":1}', b'{"a":9223372036854775808}\n']
        for raw in malformed:
            with self.assertRaises(validator.ContractError):
                validator.parse_canonical_json(raw)
        with self.assertRaises(validator.ContractError):
            validator.canonical_json_bytes({"huge": "x" * (validator.CONTRACT_BYTES_MAX + 1)})
        wider = {"wide": "x" * (validator.CONTRACT_BYTES_MAX + 1)}
        wider_raw = validator.canonical_json_bytes(wider, validator.SOURCE_INDEX_BYTES_MAX)
        self.assertEqual(validator.parse_canonical_json(wider_raw, validator.SOURCE_INDEX_BYTES_MAX), wider)
        with self.assertRaisesRegex(validator.ContractError, "object key exceeds"):
            validator.canonical_fragment_bytes({"x" * 1025: 0}, 1024)

    def test_09_cli_and_contract_reads_are_temp_only_for_mutations(self) -> None:
        accepted = subprocess.run([sys.executable, "-B", str(VALIDATOR_PATH), str(CONTRACT_PATH)], check=False, capture_output=True)
        self.assertEqual(accepted.returncode, 0, accepted.stderr.decode())
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "contract.json"
            changed = copy.deepcopy(self.contract)
            changed["classification"]["execution_authorized"] = True
            path.write_bytes(validator.canonical_json_bytes(changed))
            path.chmod(0o600)
            rejected = subprocess.run([sys.executable, "-B", str(VALIDATOR_PATH), str(path)], check=False, capture_output=True)
            self.assertEqual(rejected.returncode, 1)
            link = Path(temporary) / "link.json"
            link.symlink_to(CONTRACT_PATH)
            with self.assertRaises(validator.ContractError):
                validator.validate_contract_file(link)

    def test_10_validator_has_no_materializer_build_or_network_capability(self) -> None:
        source = VALIDATOR_PATH.read_text(encoding="utf-8")
        for forbidden in ("import socket", "import subprocess", "os.mkdir", "os.rename", "os.link("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
