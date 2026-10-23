#!/usr/bin/env python3
"""Synthetic adversarial tests for the independent ISO receipt verifier."""

from __future__ import annotations

import copy
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import tempfile
import threading
import types
import unittest
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[3]
VERIFIER_PATH = REPOSITORY / "scripts/comparison/verify_physics_iso_source_capsule_qualification_v1.py"
VERIFIER_BYTES = 113_107
VERIFIER_SHA256 = "42d0763df1db68808d41b327d049446f810e432bc5860bdc50fce552e19c2c59"


def load_authenticated_verifier():
    raw = VERIFIER_PATH.read_bytes()
    if len(raw) != VERIFIER_BYTES or hashlib.sha256(raw).hexdigest() != VERIFIER_SHA256:
        raise AssertionError("verifier source identity differs before compile")
    module = types.ModuleType("iso_receipt_verifier_v1")
    module.__file__ = str(VERIFIER_PATH)
    module.__package__ = ""
    exec(compile(raw, str(VERIFIER_PATH), "exec"), module.__dict__)
    return module, raw


verifier, VERIFIER_RAW = load_authenticated_verifier()
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
QUALIFIER_RAW = b"#!/usr/bin/env python3\nAUTHENTICATED_SYNTHETIC_QUALIFIER = True\n"


def git_blob_oid(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def git_tree(entries) -> bytes:
    body = bytearray()
    for mode, name, oid in sorted(
        entries, key=lambda item: item[1].encode() + (b"/" if item[0] == "40000" else b""),
    ):
        body.extend(mode.encode() + b" " + name.encode() + b"\0" + bytes.fromhex(oid))
    return bytes(body)


QUALIFIER_OID = git_blob_oid(QUALIFIER_RAW)
VERIFIER_OID = git_blob_oid(VERIFIER_RAW)
COMPARISON_TREE_RAW = git_tree([
    ("100644", Path(verifier.QUALIFIER_PATH).name, QUALIFIER_OID),
    ("100644", Path(verifier.VERIFIER_PATH).name, VERIFIER_OID),
])
COMPARISON_TREE_OID = verifier._git_tree_oid(COMPARISON_TREE_RAW)
SCRIPTS_TREE_RAW = git_tree([("40000", "comparison", COMPARISON_TREE_OID)])
SCRIPTS_TREE_OID = verifier._git_tree_oid(SCRIPTS_TREE_RAW)
ROOT_TREE_RAW = git_tree([("40000", "scripts", SCRIPTS_TREE_OID)])
CHECKPOINT_TREE = verifier._git_tree_oid(ROOT_TREE_RAW)
SYNTHETIC_TREES = {
    CHECKPOINT_TREE: ROOT_TREE_RAW,
    SCRIPTS_TREE_OID: SCRIPTS_TREE_RAW,
    COMPARISON_TREE_OID: COMPARISON_TREE_RAW,
}
CHECKPOINT_PAYLOAD = (
    "tree %s\nparent %s\nauthor Synthetic <s@example.test> 0 +0000\n"
    "committer Synthetic <s@example.test> 0 +0000\n\nsynthetic\n"
    % (CHECKPOINT_TREE, verifier.CONTRACT_COMMIT)
).encode()
CHECKPOINT_COMMIT = verifier._git_commit_oid(CHECKPOINT_PAYLOAD)
CONTRACT_COMMIT_PAYLOAD = (
    "tree 577b9dba177f5f87918421e68f34ca2ac7e2113c\n"
    "parent 02ec5f1d5d9b48d5c49254ed1ab67d3b8ffeb941\n"
    "author Codex <codex@localhost> 1786276647 +0100\n"
    "committer Codex <codex@localhost> 1786276647 +0100\n\n"
    "physics: freeze ISO source qualification contract\n"
).encode()
assert verifier._git_commit_oid(CONTRACT_COMMIT_PAYLOAD) == verifier.CONTRACT_COMMIT


def canonical_file(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8") + b"\n"


def sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def command(argv, stdout_bytes=0):
    return {
        "argv": list(argv), "exit_code": 0,
        "stderr_bytes": 0, "stderr_sha256": EMPTY_SHA256,
        "stdout_bytes": stdout_bytes,
        "stdout_sha256": EMPTY_SHA256 if stdout_bytes == 0 else "e" * 64,
        "timed_out": False,
    }


def binding_value():
    return {
        "checkpoint_commit": CHECKPOINT_COMMIT,
        "checkpoint_tree": CHECKPOINT_TREE,
        "contract_sha256": verifier.CONTRACT_SHA256,
        "implementations": [
            {
                "bytes": len(QUALIFIER_RAW),
                "path": verifier.QUALIFIER_PATH,
                "role": "ISO qualifier",
                "sha256": hashlib.sha256(QUALIFIER_RAW).hexdigest(),
            },
            {
                "bytes": len(VERIFIER_RAW),
                "path": verifier.VERIFIER_PATH,
                "role": "ISO receipt verifier",
                "sha256": hashlib.sha256(VERIFIER_RAW).hexdigest(),
            },
        ],
        "schema_version": verifier.BINDING_SCHEMA,
    }


def mount_ledger(contract, entries):
    digest = hashlib.sha256(
        verifier._domain_bytes(contract["hash_domains"]["mount_ledger"])
        + b"".join(verifier._framed(item) for item in entries)
    ).hexdigest()
    return {"entries": entries, "entry_count": len(entries), "mount_ledger_sha256": digest}


def device_ledger(contract, entries):
    digest = hashlib.sha256(
        verifier._domain_bytes(contract["hash_domains"]["device_association_ledger"])
        + b"".join(verifier._framed(item) for item in entries)
    ).hexdigest()
    return {
        "device_association_ledger_sha256": digest,
        "entries": entries, "entry_count": len(entries),
    }


def resign(contract, receipt):
    receipt["receipt_sha256"] = "0" * 64
    receipt["receipt_sha256"] = verifier._receipt_digest(contract, receipt)
    return receipt


def success_receipt(contract, binding, expected_trees):
    private = "/private/tmp/physics-iso-synthetic-v1"
    mountpoint = private + "/mount"
    whole = "/dev/disk9"
    volume = "/dev/disk9s1"
    image_sha = "2" * 64
    image_id = "1" * 64
    image_bytes = 16_000_000
    snapshot = {
        "bytes": image_bytes, "device": 7, "inode": 101,
        "link_count": 1, "mode": 0o400, "owner_uid": os.geteuid(),
        "sha256": image_sha,
    }
    image = {
        "atomic_publish": {
            "after_snapshot": copy.deepcopy(snapshot),
            "before_snapshot": copy.deepcopy(snapshot),
            "combined_flags": 0x34,
            "destination_parent_fsynced": True,
            "final_name_bound": True,
            "result": "published-no-replace",
            "staging_name_absent": True,
        },
        "byte_count": image_bytes,
        "declared_format": verifier._literal(contract, "image_evidence", "declared_format"),
        "image_id": image_id,
        "image_sha256": image_sha,
        "makehybrid": command(verifier._resolve_command(contract, "makehybrid", private)),
        "mounted_view_count": 1,
        "per_run_identity": True,
        "raw_iso_parser_complete": False,
        "stage_after_sha256": expected_trees["staged_tree_sha256"],
        "stage_before_sha256": expected_trees["staged_tree_sha256"],
        "stage_unchanged": True,
    }
    mount_body = {
        "fs_type": "cd9660", "image_id": image_id,
        "media_read_only": True, "mount_flags": 1,
        "mount_root_device": 9, "mount_root_inode": 202,
        "mount_root_mode": 0o555, "mount_root_owner_uid": os.geteuid(),
        "mountpoint": mountpoint, "statfs_fsid_words": [17, -4],
        "volume_device": volume, "volume_read_only": True,
        "whole_device": whole,
    }
    mount_digest = hashlib.sha256(
        verifier._domain_bytes(contract["hash_domains"]["diagnostic_mount_identity"])
        + verifier._canonical_fragment(mount_body)
    ).hexdigest()
    target_mount = {
        "fs_type": "cd9660", "is_target": True, "mount_flags": 1,
        "mounted_from_sha256": sha_text(volume),
        "mountpoint_sha256": sha_text(mountpoint),
        "statfs_fsid_words": [17, -4],
        "target_mounted_from": volume, "target_mountpoint": mountpoint,
    }
    target_device = {
        "entities": [
            {"device": whole, "is_whole": True, "mountpoint_sha256": None},
            {"device": volume, "is_whole": False, "mountpoint_sha256": sha_text(mountpoint)},
        ],
        "image_path_sha256": sha_text(private + "/source.iso"),
        "is_target_image": True, "target_image_id": image_id,
        "whole_device": whole,
    }
    pre_mount = mount_ledger(contract, [])
    post_mount = mount_ledger(contract, [target_mount])
    pre_device = device_ledger(contract, [])
    post_device = device_ledger(contract, [target_device])
    attachment = {
        "attach": command(verifier._resolve_command(contract, "attach", private), 1),
        "diagnostic_mount_identity_sha256": mount_digest,
        "diskutil_volume": command(
            verifier._resolve_command(contract, "diskutil_info_volume", private,
                                      volume_device=volume), 1,
        ),
        "diskutil_whole": command(
            verifier._resolve_command(contract, "diskutil_info_whole", private,
                                      whole_device=whole), 1,
        ),
        "entities": [
            {"device": whole, "is_whole": True, "mountpoint": None,
             "volume_kind": "whole-device"},
            {"device": volume, "is_whole": False, "mountpoint": mountpoint,
             "volume_kind": "cd9660-volume"},
        ],
        "entity_count": 2, "extra_entities_absent": True,
        "mount_identity_body": mount_body, "mounted_view_count": 1,
        "partial_attach_detected": False,
        "post_device_ledger": post_device, "post_mount_ledger": post_mount,
        "pre_device_ledger": pre_device, "pre_mount_ledger": pre_mount,
    }
    post_unlink = copy.deepcopy(snapshot)
    post_unlink["link_count"] = 0
    backing = {
        "after_attach_sha256": image_sha, "after_detach_sha256": image_sha,
        "after_unlink_sha256": image_sha, "before_attach_sha256": image_sha,
        "before_attach_snapshot": copy.deepcopy(snapshot),
        "fd_identity_stable": True, "initial_link_count": 1,
        "lock_observation": {
            "conflict_length": 0, "conflict_pid": 0, "conflict_start": 0,
            "conflict_type": "F_UNLCK", "result": "unlocked",
        },
        "named_path_absent": True,
        "named_write_open": {"errno_name": "EACCES", "succeeded": False},
        "post_unlink_link_count": 0, "post_unlink_snapshot": post_unlink,
        "unlinked": True,
    }
    inventories = []
    mounted_digests = []
    for root in contract["image_profile"]["roots"]:
        digest = expected_trees["mounted_root_sha256"][root["root_id"]]
        mounted_digests.append(digest)
        inventories.append({
            "after_mutations_root_sha256": digest,
            "after_unlink_root_sha256": digest,
            "before_unlink_root_sha256": digest,
            "implicit_directory_count": root["implicit_directory_count"],
            "regular_file_count": root["regular_file_count"],
            "root_id": root["root_id"],
            "root_tree_sha256": root["root_tree_sha256"],
            "total_regular_bytes": root["total_regular_bytes"],
            "tree_entry_count": root["tree_entry_count"],
        })
    probes = [
        {
            "errno_name": entry["acceptable_errno_names"][0],
            "probe_id": entry["probe_id"], "result": "denied",
            "root_digests_after": list(mounted_digests),
        }
        for entry in contract["qualification_protocol"]["mutation_probe_registry"]
    ]
    directory_snapshot = {
        "child_count": 0, "device": 7, "inode": 303,
        "link_count": 2, "mode": 0o700, "owner_uid": os.geteuid(),
    }
    cleanup = {
        "anonymous_image_fd_closed": True, "complete": True,
        "detach": command(
            verifier._resolve_command(contract, "detach", private,
                                      whole_device=whole),
        ),
        "device_association_absent": True,
        "final_device_ledger": copy.deepcopy(pre_device),
        "final_mount_ledger": copy.deepcopy(pre_mount),
        "mount_association_absent": True,
        "mountpoint_after_snapshot": copy.deepcopy(directory_snapshot),
        "mountpoint_before_snapshot": copy.deepcopy(directory_snapshot),
        "mountpoint_restored": True, "private_parent_absent": True,
        "repository_status_unchanged": True,
        "stage_directories_removed": 71, "stage_files_removed": 477,
        "temp_residuals": [],
    }
    implementation_bindings = []
    for item in binding["implementations"]:
        implementation_bindings.append({
            "bytes": item["bytes"], "checkpoint_commit": binding["checkpoint_commit"],
            "checkpoint_tree": binding["checkpoint_tree"],
            "executed_authenticated_bytes": True,
            "independently_expected_by_verifier": True,
            "path": item["path"], "role": item["role"], "sha256": item["sha256"],
        })
    receipt = {
        "attachment": attachment, "backing_file": backing,
        "classification": copy.deepcopy(contract["receipt_contract"]["required_classification"]),
        "cleanup": cleanup, "contract_file_sha256": verifier.CONTRACT_SHA256,
        "empirical_blockers": [], "empirical_qualification_passed": True,
        "epoch_id": contract["epoch_id"],
        "host_profile_sha256": contract["host_profile"]["profile_sha256"],
        "image": image, "implementation_bindings": implementation_bindings,
        "inventories": inventories,
        "limitations": copy.deepcopy(contract["current_unqualified_receipt"]["limitations"]),
        "metadata_view": {
            "acl_nodes": 0, "bsd_flag_nodes": 0,
            "checked_nodes": contract["hard_caps"]["metadata_nodes_exact"],
            "nonempty_xattr_nodes": 0, "outer_mount_root_checked": True,
            "resource_fork_nodes": 0,
            "xattr_empty_nodes": contract["hard_caps"]["metadata_nodes_exact"],
            "xattr_enotsup_nodes": 0,
        },
        "mutation_probes": probes, "production_backend_eligible": False,
        "production_blockers": [
            item["blocker_id"]
            for item in contract["current_blockers"]["permanent_production_blockers"]
        ],
        "receipt_sha256": "0" * 64, "schema_version": verifier.RECEIPT_SCHEMA,
        "source_input_handoff_sha256": contract["exact_bindings"]["source_index_checkpoint"]["source_input_handoff_sha256"],
        "tool_profile_sha256": contract["tool_profile"]["profile_sha256"],
    }
    return resign(contract, receipt)


def fake_git(binding, qualifier_raw=QUALIFIER_RAW, verifier_raw=VERIFIER_RAW,
             checkpoint_tree=None, checkpoint_parent=None):
    parent = verifier.CONTRACT_COMMIT if checkpoint_parent is None else checkpoint_parent
    blobs = {
        binding["implementations"][0]["path"]: qualifier_raw,
        binding["implementations"][1]["path"]: verifier_raw,
    }
    oids = {path: git_blob_oid(raw) for path, raw in blobs.items()}
    tree_in_commit = CHECKPOINT_TREE if checkpoint_tree is None else checkpoint_tree
    checkpoint = (
        "tree %s\nparent %s\nauthor Synthetic <s@example.test> 0 +0000\n"
        "committer Synthetic <s@example.test> 0 +0000\n\nsynthetic\n"
        % (tree_in_commit, parent)
    ).encode()

    def call(arguments, maximum):
        args = tuple(arguments)
        if args[:2] == ("cat-file", "commit"):
            if args[2] == CHECKPOINT_COMMIT:
                return checkpoint
            if args[2] == verifier.CONTRACT_COMMIT:
                return CONTRACT_COMMIT_PAYLOAD
            raise verifier.VerificationError("unknown synthetic commit")
        if args[:2] == ("cat-file", "tree"):
            if args[2] in SYNTHETIC_TREES:
                return SYNTHETIC_TREES[args[2]]
        if args[:2] == ("cat-file", "blob"):
            for path, oid in oids.items():
                if args[2] == oid:
                    return blobs[path]
        raise verifier.VerificationError("unknown synthetic Git request")

    return call


class IsoReceiptVerifierV1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = verifier.authenticate_contract()
        cls.binding = binding_value()
        cls.expected_trees = verifier.load_expected_source_trees(cls.contract)
        cls.receipt = success_receipt(cls.contract, cls.binding, cls.expected_trees)

    def assert_semantic_mutation_fails(self, mutate) -> None:
        changed = copy.deepcopy(self.receipt)
        mutate(changed)
        resign(self.contract, changed)
        with self.assertRaises(verifier.VerificationError):
            verifier.validate_success_receipt(
                changed, self.contract, self.binding, self.expected_trees,
            )

    def test_01_authenticated_raw_loader_and_contract_vectors(self) -> None:
        self.assertEqual(len(VERIFIER_RAW), VERIFIER_BYTES)
        self.assertEqual(hashlib.sha256(VERIFIER_RAW).hexdigest(), VERIFIER_SHA256)
        self.assertEqual(self.contract["schema_version"], verifier.CONTRACT_SCHEMA)
        verifier._validate_contract_hash_vectors(self.contract)

    def test_02_valid_synthetic_success_receipt(self) -> None:
        accepted = verifier.validate_success_receipt(
            copy.deepcopy(self.receipt), self.contract, self.binding,
            self.expected_trees,
        )
        self.assertTrue(accepted["empirical_qualification_passed"])
        self.assertFalse(accepted["production_backend_eligible"])
        self.assertTrue(all(value is False for value in accepted["classification"].values()))

    def test_03_full_file_path_with_fake_git_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            temporary_root = Path(temporary).resolve()
            root = temporary_root / "repository"
            for identity, raw in zip(self.binding["implementations"], (QUALIFIER_RAW, VERIFIER_RAW)):
                path = root / identity["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                path.chmod(0o644)
            receipt_path = temporary_root / "receipt.json"
            receipt_path.write_bytes(canonical_file(self.receipt))
            receipt_path.chmod(0o600)
            verdict = verifier.verify_receipt_file(
                receipt_path, self.binding, git_call=fake_git(self.binding), repository=root,
            )
            self.assertTrue(verdict["valid"])
            self.assertFalse(verdict["production_backend_eligible"])

    def test_04_strict_json_limits_fail_closed(self) -> None:
        fixtures = [
            (b'{"a":1,"a":2}\n', {}),
            (b'{"a":1.0}\n', {}),
            (b'{ "a":1}\n', {}),
            (b'{"a":9223372036854775808}\n', {}),
            (b'{"a":"\\ud800"}\n', {}),
            (b'{"a":{"b":1}}\n', {"maximum_depth": 1}),
            (b'{"a":1,"b":2}\n', {"maximum_nodes": 2}),
            ('{"a":"é"}\n'.encode(), {"maximum_string_bytes": 1}),
        ]
        for raw, options in fixtures:
            with self.subTest(raw=raw):
                with self.assertRaises(verifier.VerificationError):
                    verifier._strict_json(raw, "fixture", **options)

    def test_05_expected_binding_mutations_fail(self) -> None:
        mutations = [
            lambda x: x.__setitem__("checkpoint_commit", "0" * 40),
            lambda x: x.__setitem__("contract_sha256", "0" * 64),
            lambda x: x["implementations"].reverse(),
            lambda x: x["implementations"][0].__setitem__("path", "elsewhere.py"),
            lambda x: x["implementations"][0].__setitem__("bytes", True),
            lambda x: x["implementations"][0].__setitem__("sha256", "F" * 64),
            lambda x: x.__setitem__("extra", False),
        ]
        for mutate in mutations:
            changed = copy.deepcopy(self.binding)
            mutate(changed)
            with self.assertRaises(verifier.VerificationError):
                verifier.validate_expected_implementation_binding(changed)

    def test_06_checkpoint_tree_ancestry_blob_and_live_bytes_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for identity, raw in zip(self.binding["implementations"], (QUALIFIER_RAW, VERIFIER_RAW)):
                path = root / identity["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                path.chmod(0o644)
            authenticated_stat = verifier.os.stat

            def reject_absolute_path_restat(path, *args, **kwargs):
                if kwargs.get("dir_fd") is None and os.path.isabs(os.fspath(path)):
                    raise AssertionError("live implementation was restated by pathname")
                return authenticated_stat(path, *args, **kwargs)

            with mock.patch.object(
                verifier.os, "stat", side_effect=reject_absolute_path_restat,
            ):
                verifier.authenticate_implementation_checkpoint(
                    self.binding, fake_git(self.binding), root,
                )
            changed = copy.deepcopy(self.binding)
            changed["checkpoint_tree"] = "c" * 40
            with self.assertRaisesRegex(verifier.VerificationError, "commit-to-tree"):
                verifier.authenticate_implementation_checkpoint(
                    changed, fake_git(changed, checkpoint_tree=CHECKPOINT_TREE), root,
                )
            with self.assertRaises(verifier.VerificationError):
                verifier.authenticate_implementation_checkpoint(
                    self.binding, fake_git(self.binding, checkpoint_parent="d" * 40), root,
                )
            (root / self.binding["implementations"][0]["path"]).write_bytes(b"different\n")
            with self.assertRaises(verifier.VerificationError):
                verifier.authenticate_implementation_checkpoint(
                    self.binding, fake_git(self.binding), root,
                )

    def test_06b_raw_tree_and_commit_object_hashes_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            for identity, raw in zip(self.binding["implementations"], (QUALIFIER_RAW, VERIFIER_RAW)):
                path = root / identity["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
                path.chmod(0o644)
            baseline = fake_git(self.binding)

            def bad_tree(arguments, maximum):
                raw = baseline(arguments, maximum)
                if tuple(arguments) == ("cat-file", "tree", CHECKPOINT_TREE):
                    return raw + b"x"
                return raw

            with self.assertRaisesRegex(verifier.VerificationError, "tree object SHA-1"):
                verifier.authenticate_implementation_checkpoint(self.binding, bad_tree, root)

            def bad_commit(arguments, maximum):
                raw = baseline(arguments, maximum)
                if tuple(arguments) == ("cat-file", "commit", CHECKPOINT_COMMIT):
                    return raw + b"x"
                return raw

            with self.assertRaisesRegex(verifier.VerificationError, "commit object SHA-1"):
                verifier.authenticate_implementation_checkpoint(self.binding, bad_commit, root)

    def test_07_false_gates_blockers_and_schema_confusion_fail(self) -> None:
        mutations = [
            lambda x: x["classification"].__setitem__("build_authorized", True),
            lambda x: x["classification"].__setitem__("build_authorized", 0),
            lambda x: x.__setitem__("production_backend_eligible", True),
            lambda x: x.__setitem__("empirical_qualification_passed", False),
            lambda x: x["production_blockers"].pop(),
            lambda x: x["limitations"].pop(),
            lambda x: x.__setitem__("schema_version", "dancers-planar-physics-source-root-manifest-v2"),
        ]
        for mutate in mutations:
            self.assert_semantic_mutation_fails(mutate)

    def test_08_image_and_atomic_publication_mutations_fail(self) -> None:
        def coherent_wrong_stage(receipt):
            receipt["image"]["stage_before_sha256"] = "9" * 64
            receipt["image"]["stage_after_sha256"] = "9" * 64

        def coherent_wrong_image_owner(receipt):
            wrong_uid = os.geteuid() + 1
            receipt["image"]["atomic_publish"]["before_snapshot"]["owner_uid"] = wrong_uid
            receipt["image"]["atomic_publish"]["after_snapshot"]["owner_uid"] = wrong_uid
            receipt["backing_file"]["before_attach_snapshot"]["owner_uid"] = wrong_uid
            receipt["backing_file"]["post_unlink_snapshot"]["owner_uid"] = wrong_uid

        mutations = [
            lambda x: x["image"].__setitem__("mounted_view_count", True),
            lambda x: x["image"].__setitem__("stage_after_sha256", "4" * 64),
            coherent_wrong_stage,
            coherent_wrong_image_owner,
            lambda x: x["image"]["makehybrid"]["argv"].append("-joliet"),
            lambda x: x["image"]["atomic_publish"].__setitem__("combined_flags", 4),
            lambda x: x["image"]["atomic_publish"]["after_snapshot"].__setitem__("inode", 999),
            lambda x: x["image"].__setitem__("raw_iso_parser_complete", True),
            lambda x: x["image"].__setitem__("image_id", x["image"]["image_sha256"]),
        ]
        for mutate in mutations:
            self.assert_semantic_mutation_fails(mutate)

    def test_09_attachment_mount_and_ledger_mutations_fail(self) -> None:
        def disguise_preexisting_mount(receipt):
            attachment = receipt["attachment"]
            target = attachment["post_mount_ledger"]["entries"][0]
            disguised = copy.deepcopy(target)
            disguised["is_target"] = False
            disguised["mounted_from_sha256"] = "0" * 64
            disguised["target_mounted_from"] = None
            disguised["target_mountpoint"] = None
            pre_entries = [disguised]
            post_entries = sorted(
                [copy.deepcopy(disguised), target],
                key=lambda item: (item["mountpoint_sha256"], item["mounted_from_sha256"]),
            )
            attachment["pre_mount_ledger"] = mount_ledger(self.contract, pre_entries)
            attachment["post_mount_ledger"] = mount_ledger(self.contract, post_entries)
            receipt["cleanup"]["final_mount_ledger"] = copy.deepcopy(
                attachment["pre_mount_ledger"]
            )

        def disguise_preexisting_image(receipt):
            attachment = receipt["attachment"]
            target = attachment["post_device_ledger"]["entries"][0]
            disguised = {
                "entities": [{"device": "/dev/disk8", "is_whole": True,
                              "mountpoint_sha256": None}],
                "image_path_sha256": target["image_path_sha256"],
                "is_target_image": False, "target_image_id": None,
                "whole_device": "/dev/disk8",
            }
            pre_entries = [disguised]
            post_entries = sorted(
                [copy.deepcopy(disguised), target],
                key=lambda item: (item["image_path_sha256"], item["whole_device"]),
            )
            attachment["pre_device_ledger"] = device_ledger(self.contract, pre_entries)
            attachment["post_device_ledger"] = device_ledger(self.contract, post_entries)
            receipt["cleanup"]["final_device_ledger"] = copy.deepcopy(
                attachment["pre_device_ledger"]
            )

        def add_target_device_entity(receipt):
            ledger = receipt["attachment"]["post_device_ledger"]
            target = ledger["entries"][0]
            target["entities"].append({
                "device": "/dev/disk99", "is_whole": True,
                "mountpoint_sha256": None,
            })
            target["entities"].sort(key=lambda item: (
                item["device"], "" if item["mountpoint_sha256"] is None
                else item["mountpoint_sha256"],
            ))
            receipt["attachment"]["post_device_ledger"] = device_ledger(
                self.contract, ledger["entries"],
            )

        def add_unrelated_labelled_mount(receipt):
            attachment = receipt["attachment"]
            target = copy.deepcopy(attachment["post_mount_ledger"]["entries"][0])
            unrelated_device = "/dev/disk8s1"
            unrelated_mountpoint = "/private/tmp/unrelated-mount"
            unrelated = {
                "fs_type": "apfs", "is_target": True, "mount_flags": 0,
                "mounted_from_sha256": sha_text(unrelated_device),
                "mountpoint_sha256": sha_text(unrelated_mountpoint),
                "statfs_fsid_words": [23, 24],
                "target_mounted_from": unrelated_device,
                "target_mountpoint": unrelated_mountpoint,
            }
            entries = sorted(
                [target, unrelated],
                key=lambda item: (item["mountpoint_sha256"], item["mounted_from_sha256"]),
            )
            attachment["post_mount_ledger"] = mount_ledger(self.contract, entries)

        def add_unrelated_labelled_device(receipt):
            attachment = receipt["attachment"]
            target = copy.deepcopy(attachment["post_device_ledger"]["entries"][0])
            unrelated = {
                "entities": [{"device": "/dev/disk8", "is_whole": True,
                              "mountpoint_sha256": None}],
                "image_path_sha256": "8" * 64,
                "is_target_image": True, "target_image_id": "7" * 64,
                "whole_device": "/dev/disk8",
            }
            entries = sorted(
                [target, unrelated],
                key=lambda item: (item["image_path_sha256"], item["whole_device"]),
            )
            attachment["post_device_ledger"] = device_ledger(self.contract, entries)

        def replace_device_delta(receipt, pre_entries):
            attachment = receipt["attachment"]
            target = copy.deepcopy(attachment["post_device_ledger"]["entries"][0])
            post_entries = sorted(
                copy.deepcopy(pre_entries) + [target],
                key=lambda item: (item["image_path_sha256"], item["whole_device"]),
            )
            attachment["pre_device_ledger"] = device_ledger(
                self.contract, copy.deepcopy(pre_entries),
            )
            attachment["post_device_ledger"] = device_ledger(
                self.contract, post_entries,
            )
            receipt["cleanup"]["final_device_ledger"] = copy.deepcopy(
                attachment["pre_device_ledger"],
            )

        def disguise_preexisting_chosen_whole_device(receipt):
            target = receipt["attachment"]["post_device_ledger"]["entries"][0]
            whole = target["whole_device"]
            disguised = {
                "entities": [{"device": whole, "is_whole": True,
                              "mountpoint_sha256": None}],
                "image_path_sha256": "3" * 64,
                "is_target_image": False, "target_image_id": None,
                "whole_device": whole,
            }
            replace_device_delta(receipt, [disguised])

        def disguise_preexisting_chosen_volume_entity(receipt):
            volume = receipt["attachment"]["mount_identity_body"]["volume_device"]
            disguised = {
                "entities": [{"device": volume, "is_whole": False,
                              "mountpoint_sha256": None}],
                "image_path_sha256": "4" * 64,
                "is_target_image": False, "target_image_id": None,
                "whole_device": "/dev/disk8",
            }
            replace_device_delta(receipt, [disguised])

        def duplicate_unrelated_whole_device(receipt):
            entries = [
                {
                    "entities": [{"device": "/dev/disk8", "is_whole": True,
                                  "mountpoint_sha256": None}],
                    "image_path_sha256": digest * 64,
                    "is_target_image": False, "target_image_id": None,
                    "whole_device": "/dev/disk8",
                }
                for digest in ("5", "6")
            ]
            replace_device_delta(receipt, entries)

        def coherent_wrong_mount_root_owner(receipt):
            attachment = receipt["attachment"]
            body = attachment["mount_identity_body"]
            body["mount_root_owner_uid"] = os.geteuid() + 1
            attachment["diagnostic_mount_identity_sha256"] = hashlib.sha256(
                verifier._domain_bytes(
                    self.contract["hash_domains"]["diagnostic_mount_identity"]
                ) + verifier._canonical_fragment(body)
            ).hexdigest()

        def coherent_impossible_private_component(receipt, component):
            private = "/private/tmp/" + component
            mountpoint = private + "/mount"
            attachment = receipt["attachment"]
            body = attachment["mount_identity_body"]
            body["mountpoint"] = mountpoint
            attachment["diagnostic_mount_identity_sha256"] = hashlib.sha256(
                verifier._domain_bytes(
                    self.contract["hash_domains"]["diagnostic_mount_identity"]
                ) + verifier._canonical_fragment(body)
            ).hexdigest()
            receipt["image"]["makehybrid"]["argv"] = verifier._resolve_command(
                self.contract, "makehybrid", private,
            )
            attachment["attach"]["argv"] = verifier._resolve_command(
                self.contract, "attach", private,
            )
            attachment["diskutil_whole"]["argv"] = verifier._resolve_command(
                self.contract, "diskutil_info_whole", private,
                whole_device=body["whole_device"],
            )
            attachment["diskutil_volume"]["argv"] = verifier._resolve_command(
                self.contract, "diskutil_info_volume", private,
                volume_device=body["volume_device"],
            )
            receipt["cleanup"]["detach"]["argv"] = verifier._resolve_command(
                self.contract, "detach", private,
                whole_device=body["whole_device"],
            )
            for entity in attachment["entities"]:
                if entity["volume_kind"] == "cd9660-volume":
                    entity["mountpoint"] = mountpoint
            mount_entry = attachment["post_mount_ledger"]["entries"][0]
            mount_entry["mountpoint_sha256"] = sha_text(mountpoint)
            mount_entry["target_mountpoint"] = mountpoint
            attachment["post_mount_ledger"] = mount_ledger(
                self.contract, [mount_entry],
            )
            device_entry = attachment["post_device_ledger"]["entries"][0]
            device_entry["image_path_sha256"] = sha_text(private + "/source.iso")
            for entity in device_entry["entities"]:
                if not entity["is_whole"]:
                    entity["mountpoint_sha256"] = sha_text(mountpoint)
            attachment["post_device_ledger"] = device_ledger(
                self.contract, [device_entry],
            )

        mutations = [
            lambda x: x["attachment"].__setitem__("mounted_view_count", True),
            lambda x: x["attachment"].__setitem__("partial_attach_detected", True),
            lambda x: x["attachment"].__setitem__("diagnostic_mount_identity_sha256", "0" * 64),
            lambda x: x["attachment"]["attach"]["argv"].append("-readwrite"),
            lambda x: x["attachment"]["mount_identity_body"].__setitem__("volume_read_only", False),
            lambda x: x["attachment"]["entities"].pop(),
            lambda x: x["attachment"]["post_mount_ledger"].__setitem__("entries", []),
            disguise_preexisting_mount,
            disguise_preexisting_image,
            add_target_device_entity,
            add_unrelated_labelled_mount,
            add_unrelated_labelled_device,
            disguise_preexisting_chosen_whole_device,
            disguise_preexisting_chosen_volume_entity,
            duplicate_unrelated_whole_device,
            coherent_wrong_mount_root_owner,
            lambda x: coherent_impossible_private_component(x, "nul\x00component"),
            lambda x: coherent_impossible_private_component(x, "backslash\\component"),
            lambda x: coherent_impossible_private_component(x, "x" * 256),
        ]
        for mutate in mutations:
            self.assert_semantic_mutation_fails(mutate)

    def test_10_backing_file_observation_mutations_fail(self) -> None:
        mutations = [
            lambda x: x["backing_file"].__setitem__("after_detach_sha256", "0" * 64),
            lambda x: x["backing_file"].__setitem__("initial_link_count", True),
            lambda x: x["backing_file"].__setitem__("post_unlink_link_count", False),
            lambda x: x["backing_file"].__setitem__("post_unlink_link_count", 1),
            lambda x: x["backing_file"]["post_unlink_snapshot"].__setitem__("inode", 404),
            lambda x: x["backing_file"].__setitem__("named_path_absent", False),
            lambda x: x["backing_file"]["lock_observation"].__setitem__("result", "conflicting"),
            lambda x: x["backing_file"]["named_write_open"].__setitem__("errno_name", None),
        ]
        for mutate in mutations:
            self.assert_semantic_mutation_fails(mutate)

    def test_11_inventory_metadata_and_probe_mutations_fail(self) -> None:
        def coherent_wrong_mounted_roots(receipt):
            wrong = []
            for index, inventory in enumerate(receipt["inventories"]):
                digest = ("%x" % (index + 7)) * 64
                inventory["before_unlink_root_sha256"] = digest
                inventory["after_unlink_root_sha256"] = digest
                inventory["after_mutations_root_sha256"] = digest
                wrong.append(digest)
            for probe in receipt["mutation_probes"]:
                probe["root_digests_after"] = list(wrong)

        mutations = [
            lambda x: x["inventories"][0].__setitem__("regular_file_count", 370),
            lambda x: x["inventories"][1].__setitem__("after_unlink_root_sha256", "0" * 64),
            lambda x: x["inventories"].reverse(),
            lambda x: x["metadata_view"].__setitem__("nonempty_xattr_nodes", 1),
            lambda x: x["metadata_view"].__setitem__("acl_nodes", False),
            lambda x: x["metadata_view"].__setitem__("checked_nodes", 548),
            lambda x: x["mutation_probes"][0].__setitem__("errno_name", "EACCES"),
            lambda x: x["mutation_probes"][1]["root_digests_after"].__setitem__(0, "0" * 64),
            lambda x: x["mutation_probes"].pop(),
            coherent_wrong_mounted_roots,
        ]
        for mutate in mutations:
            self.assert_semantic_mutation_fails(mutate)

    def test_12_cleanup_and_final_ledger_mutations_fail(self) -> None:
        def coherent_wrong_mountpoint_owner(receipt):
            wrong_uid = os.geteuid() + 1
            receipt["cleanup"]["mountpoint_before_snapshot"]["owner_uid"] = wrong_uid
            receipt["cleanup"]["mountpoint_after_snapshot"]["owner_uid"] = wrong_uid

        mutations = [
            lambda x: x["cleanup"].__setitem__("complete", False),
            lambda x: x["cleanup"].__setitem__("stage_files_removed", 476),
            lambda x: x["cleanup"]["temp_residuals"].append("residue"),
            lambda x: x["cleanup"]["mountpoint_after_snapshot"].__setitem__("inode", 999),
            lambda x: x["cleanup"]["detach"]["argv"].append("-force"),
            lambda x: x["cleanup"]["final_mount_ledger"].__setitem__("entry_count", True),
            lambda x: x["cleanup"].__setitem__("anonymous_image_fd_closed", False),
            coherent_wrong_mountpoint_owner,
        ]
        for mutate in mutations:
            self.assert_semantic_mutation_fails(mutate)

    def test_13_receipt_digest_exact_keys_and_implementation_binding_fail(self) -> None:
        changed = copy.deepcopy(self.receipt)
        changed["receipt_sha256"] = "0" * 64
        with self.assertRaises(verifier.VerificationError):
            verifier.validate_success_receipt(changed, self.contract, self.binding)
        mutations = [
            lambda x: x.__setitem__("extra", False),
            lambda x: x["implementation_bindings"][0].__setitem__("sha256", "0" * 64),
            lambda x: x["implementation_bindings"][1].__setitem__("independently_expected_by_verifier", False),
            lambda x: x.__setitem__("contract_file_sha256", "0" * 64),
        ]
        for mutate in mutations:
            self.assert_semantic_mutation_fails(mutate)

    def test_14_unqualified_contract_receipt_and_missing_binding_are_rejected(self) -> None:
        with self.assertRaises(verifier.VerificationError):
            verifier.validate_success_receipt(
                self.contract["current_unqualified_receipt"], self.contract, self.binding,
                self.expected_trees,
            )
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / "receipt.json"
            receipt.write_bytes(canonical_file(self.receipt))
            receipt.chmod(0o600)
            with self.assertRaisesRegex(verifier.VerificationError, "authenticated expected implementation binding"):
                verifier.verify_receipt_file(receipt, None)

    def test_15_cli_refuses_without_separately_pinned_binding(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(verifier.main([]), 2)

    def test_16_descriptor_alias_mode_link_and_required_flags_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            real = root / "real"
            real.mkdir(mode=0o700)
            source = real / "input"
            source.write_bytes(b"identity\n")
            source.chmod(0o600)
            alias = root / "alias"
            alias.symlink_to(real, target_is_directory=True)
            with self.assertRaises(verifier.VerificationError):
                verifier._read_regular(alias / "input", 1024)
            final_alias = real / "final-alias"
            final_alias.symlink_to(source)
            with self.assertRaises(verifier.VerificationError):
                verifier._read_regular(final_alias, 1024)
            hardlink = real / "hardlink"
            os.link(source, hardlink)
            with self.assertRaises(verifier.VerificationError):
                verifier._read_regular(source, 1024)
            hardlink.unlink()
            source.chmod(0o660)
            with self.assertRaises(verifier.VerificationError):
                verifier._read_regular(source, 1024)
            source.chmod(0o600)
            with mock.patch.object(verifier.os, "O_NOFOLLOW", 0):
                with self.assertRaisesRegex(verifier.VerificationError, "required descriptor flag"):
                    verifier._read_regular(source, 1024)
            descriptors_before = set(os.listdir("/dev/fd"))
            real_fstat = verifier.os.fstat
            fstat_calls = [0]

            def interrupt_first_child_fstat(descriptor):
                fstat_calls[0] += 1
                if fstat_calls[0] == 2:
                    raise KeyboardInterrupt("synthetic descriptor-walk cancellation")
                return real_fstat(descriptor)

            with mock.patch.object(
                verifier.os, "fstat", side_effect=interrupt_first_child_fstat,
            ):
                with self.assertRaises(KeyboardInterrupt):
                    verifier._read_regular(source, 1024)
            self.assertEqual(set(os.listdir("/dev/fd")), descriptors_before)

    def test_17_git_argv_absolute_path_flood_timeout_and_reap_fail_closed(self) -> None:
        forbidden = [
            ("hash-object", "-w", "x"),
            ("cat-file", "--batch", verifier.CONTRACT_COMMIT),
            ("cat-file", "blob", "--help"),
            ("ls-tree", "-z", verifier.CONTRACT_TREE, "--", verifier.QUALIFIER_PATH,
             verifier.VERIFIER_PATH),
        ]
        for argv in forbidden:
            with self.assertRaises(verifier.VerificationError):
                verifier._validate_git_arguments(argv)
        with mock.patch.dict(os.environ, {"PATH": "/tmp/fake-git"}, clear=False):
            with mock.patch.object(verifier.subprocess, "Popen", side_effect=OSError("blocked")) as popen:
                with self.assertRaises(verifier.VerificationError):
                    verifier._git(("cat-file", "commit", verifier.CONTRACT_COMMIT), 1024)
                self.assertEqual(popen.call_args.args[0][0], "/usr/bin/git")
                self.assertEqual(popen.call_args.kwargs["env"], verifier.GIT_ENV)

        class FakeProcess:
            def __init__(self, stdout, stderr, wait_error=None):
                self.stdout = stdout
                self.stderr = stderr
                self.pid = 424242
                self.returncode = None
                self.wait_error = wait_error

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                if self.wait_error is not None:
                    raise self.wait_error
                self.returncode = -9
                return self.returncode

        out_read, out_write = os.pipe()
        err_read, err_write = os.pipe()
        setup = FakeProcess(os.fdopen(out_read, "rb", buffering=0),
                            os.fdopen(err_read, "rb", buffering=0))
        setup_killed = []

        def fail_selector_construction():
            raise RuntimeError("selector unavailable")

        with self.assertRaisesRegex(verifier.VerificationError, "bounded transport failed"):
            verifier._collect_bounded_process(
                setup, 16, 16, 1,
                selector_factory=fail_selector_construction,
                killpg=lambda pid, sig: setup_killed.append((pid, sig)),
            )
        self.assertEqual(setup_killed, [(424242, verifier.signal.SIGKILL)])
        self.assertIsNotNone(setup.poll())
        self.assertTrue(setup.stdout.closed)
        self.assertTrue(setup.stderr.closed)
        os.close(out_write)
        os.close(err_write)

        out_read, out_write = os.pipe()
        err_read, err_write = os.pipe()
        interrupted = FakeProcess(os.fdopen(out_read, "rb", buffering=0),
                                  os.fdopen(err_read, "rb", buffering=0))
        interrupted_kills = []

        def interrupt_selector_construction():
            raise KeyboardInterrupt("synthetic selector cancellation")

        with self.assertRaises(KeyboardInterrupt):
            verifier._collect_bounded_process(
                interrupted, 16, 16, 1,
                selector_factory=interrupt_selector_construction,
                killpg=lambda pid, sig: interrupted_kills.append((pid, sig)),
            )
        self.assertEqual(interrupted_kills, [(424242, verifier.signal.SIGKILL)])
        self.assertIsNotNone(interrupted.poll())
        self.assertTrue(interrupted.stdout.closed)
        self.assertTrue(interrupted.stderr.closed)
        os.close(out_write)
        os.close(err_write)

        handoff = FakeProcess(None, None)
        handoff_kills = []
        with mock.patch.object(verifier.subprocess, "Popen", return_value=handoff):
            with mock.patch.object(
                verifier, "_collect_bounded_process",
                side_effect=KeyboardInterrupt("synthetic post-spawn cancellation"),
            ):
                with mock.patch.object(
                    verifier.os, "killpg",
                    side_effect=lambda pid, sig: handoff_kills.append((pid, sig)),
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        verifier._git(
                            ("cat-file", "commit", verifier.CONTRACT_COMMIT), 1024,
                        )
        self.assertEqual(handoff_kills, [(424242, verifier.signal.SIGKILL)])
        self.assertIsNotNone(handoff.poll())

        out_read, out_write = os.pipe()
        err_read, err_write = os.pipe()
        os.write(out_write, b"x" * 128)
        os.close(out_write)
        os.close(err_write)
        process = FakeProcess(os.fdopen(out_read, "rb", buffering=0),
                              os.fdopen(err_read, "rb", buffering=0))
        killed = []
        with self.assertRaisesRegex(verifier.VerificationError, "stdout exceeded"):
            verifier._collect_bounded_process(
                process, 16, 16, 1, killpg=lambda pid, sig: killed.append((pid, sig)),
            )
        self.assertEqual(killed, [(424242, verifier.signal.SIGKILL)])

        class NoEventSelector:
            def __init__(self):
                self.mapping = {}

            def register(self, fileobj, events, data):
                self.mapping[fileobj] = data

            def get_map(self):
                return self.mapping

            def select(self, timeout):
                return []

            def close(self):
                self.mapping.clear()

        out_read, out_write = os.pipe()
        err_read, err_write = os.pipe()
        timed = FakeProcess(os.fdopen(out_read, "rb", buffering=0),
                            os.fdopen(err_read, "rb", buffering=0))
        moments = iter([0.0, 2.0])
        with self.assertRaisesRegex(verifier.VerificationError, "timed out"):
            verifier._collect_bounded_process(
                timed, 16, 16, 1, selector_factory=NoEventSelector,
                monotonic=lambda: next(moments), killpg=lambda pid, sig: None,
            )
        os.close(out_write)
        os.close(err_write)

        dead = FakeProcess(None, None)
        with self.assertRaisesRegex(verifier.VerificationError, "kill failed"):
            verifier._kill_and_wait(
                dead, lambda pid, sig: (_ for _ in ()).throw(OSError("denied")),
            )
        unreapable = FakeProcess(None, None, wait_error=TimeoutError("stuck"))
        with self.assertRaisesRegex(verifier.VerificationError, "could not be reaped"):
            verifier._kill_and_wait(unreapable, lambda pid, sig: None)

    def test_18_synthetic_repository_policy_rejects_remote_alternate_and_promisor(self) -> None:
        def make_repository(base):
            repository = base / "repo"
            (repository / ".git/objects/info").mkdir(parents=True)
            (repository / ".git/objects/pack").mkdir()
            (repository / ".git/refs").mkdir()
            config = repository / ".git/config"
            config.write_text(
                "[core]\n\trepositoryformatversion = 0\n\tbare = false\n",
                encoding="utf-8",
            )
            config.chmod(0o600)
            return repository

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repository = make_repository(root)
            verifier._verify_repository_object_policy(repository)
            (repository / ".git/objects/info/alternates").write_text("/tmp/objects\n")
            with self.assertRaises(verifier.VerificationError):
                verifier._verify_repository_object_policy(repository)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repository = make_repository(root)
            (repository / ".git/config").write_text(
                "[core]\n\trepositoryformatversion = 0\n[remote \"origin\"]\n\turl = x\n"
            )
            with self.assertRaises(verifier.VerificationError):
                verifier._verify_repository_object_policy(repository)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repository = make_repository(root)
            (repository / ".git/objects/pack/x.promisor").write_bytes(b"")
            with self.assertRaises(verifier.VerificationError):
                verifier._verify_repository_object_policy(repository)

    def test_20_pending_signal_fd_acquisitions_leave_no_descriptor(self) -> None:
        self.assertIs(threading.current_thread(), threading.main_thread())
        self.assertTrue(callable(getattr(signal, "pthread_sigmask", None)))
        prior_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        unblocked = set(prior_mask)
        unblocked.discard(signal.SIGINT)
        signal.pthread_sigmask(signal.SIG_SETMASK, unblocked)
        active = [""]
        deliveries = []

        def deliver_interrupt(signum, frame):
            deliveries.append(active[0])
            raise KeyboardInterrupt("pending acquisition cancellation: " + active[0])

        prior_handler = signal.signal(signal.SIGINT, deliver_interrupt)
        try:
            with tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary).resolve() / "input"
                source.write_bytes(b"bounded input\n")
                source.chmod(0o600)

                baseline = set(os.listdir("/dev/fd"))
                active[0] = "directory-root"
                real_open = verifier.os.open
                sent = [False]

                def root_open_then_signal(path, flags, *args, **kwargs):
                    descriptor = real_open(path, flags, *args, **kwargs)
                    if path == "/" and not sent[0]:
                        sent[0] = True
                        os.kill(os.getpid(), signal.SIGINT)
                        if callable(getattr(signal, "sigpending", None)):
                            self.assertIn(signal.SIGINT, signal.sigpending())
                    return descriptor

                with mock.patch.object(
                    verifier.os, "open", side_effect=root_open_then_signal,
                ):
                    with self.assertRaisesRegex(
                        KeyboardInterrupt, "directory-root",
                    ):
                        verifier._read_regular(source, 1024)
                self.assertTrue(sent[0])
                self.assertEqual(set(os.listdir("/dev/fd")), baseline)

                active[0] = "chain-return"
                real_chain = verifier._open_directory_chain
                sent = [False]

                def chain_then_signal(path):
                    owned = real_chain(path)
                    sent[0] = True
                    os.kill(os.getpid(), signal.SIGINT)
                    if callable(getattr(signal, "sigpending", None)):
                        self.assertIn(signal.SIGINT, signal.sigpending())
                    return owned

                with mock.patch.object(
                    verifier, "_open_directory_chain", side_effect=chain_then_signal,
                ):
                    with self.assertRaisesRegex(KeyboardInterrupt, "chain-return"):
                        verifier._read_regular(source, 1024)
                self.assertTrue(sent[0])
                self.assertEqual(set(os.listdir("/dev/fd")), baseline)

                active[0] = "leaf-open"
                real_open = verifier.os.open
                sent = [False]

                def leaf_open_then_signal(path, flags, *args, **kwargs):
                    descriptor = real_open(path, flags, *args, **kwargs)
                    if path == source.name and not sent[0]:
                        sent[0] = True
                        os.kill(os.getpid(), signal.SIGINT)
                        if callable(getattr(signal, "sigpending", None)):
                            self.assertIn(signal.SIGINT, signal.sigpending())
                    return descriptor

                with mock.patch.object(
                    verifier.os, "open", side_effect=leaf_open_then_signal,
                ):
                    with self.assertRaisesRegex(KeyboardInterrupt, "leaf-open"):
                        verifier._read_regular(source, 1024)
                self.assertTrue(sent[0])
                self.assertEqual(set(os.listdir("/dev/fd")), baseline)
            self.assertEqual(
                deliveries, ["directory-root", "chain-return", "leaf-open"],
            )
        finally:
            signal.signal(signal.SIGINT, prior_handler)
            signal.pthread_sigmask(signal.SIG_SETMASK, prior_mask)

    def test_21_pending_signal_process_and_selector_are_reaped_and_closed(self) -> None:
        self.assertIs(threading.current_thread(), threading.main_thread())
        prior_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        unblocked = set(prior_mask)
        unblocked.discard(signal.SIGINT)
        signal.pthread_sigmask(signal.SIG_SETMASK, unblocked)
        active = [""]
        deliveries = []

        def deliver_interrupt(signum, frame):
            deliveries.append(active[0])
            raise KeyboardInterrupt("pending transport cancellation: " + active[0])

        prior_handler = signal.signal(signal.SIGINT, deliver_interrupt)

        class FakeProcess:
            def __init__(self, stdout, stderr):
                self.stdout = stdout
                self.stderr = stderr
                self.pid = 424242
                self.returncode = None
                self.wait_calls = 0

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                self.wait_calls += 1
                self.returncode = -9
                return self.returncode

        try:
            baseline = set(os.listdir("/dev/fd"))
            out_read, out_write = os.pipe()
            err_read, err_write = os.pipe()
            process = FakeProcess(
                os.fdopen(out_read, "rb", buffering=0),
                os.fdopen(err_read, "rb", buffering=0),
            )
            killed = []
            active[0] = "popen-return"

            def popen_then_signal(*args, **kwargs):
                os.kill(os.getpid(), signal.SIGINT)
                if callable(getattr(signal, "sigpending", None)):
                    self.assertIn(signal.SIGINT, signal.sigpending())
                return process

            try:
                with mock.patch.object(
                    verifier.subprocess, "Popen", side_effect=popen_then_signal,
                ), mock.patch.object(
                    verifier.os, "killpg",
                    side_effect=lambda pid, sig: killed.append((pid, sig)),
                ):
                    with self.assertRaisesRegex(
                        KeyboardInterrupt, "popen-return",
                    ):
                        verifier._git(
                            ("cat-file", "commit", verifier.CONTRACT_COMMIT), 1024,
                        )
                self.assertEqual(killed, [(424242, signal.SIGKILL)])
                self.assertEqual(process.wait_calls, 1)
                self.assertIsNotNone(process.poll())
                self.assertTrue(process.stdout.closed)
                self.assertTrue(process.stderr.closed)
            finally:
                os.close(out_write)
                os.close(err_write)
            self.assertEqual(set(os.listdir("/dev/fd")), baseline)

            out_read, out_write = os.pipe()
            err_read, err_write = os.pipe()
            process = FakeProcess(
                os.fdopen(out_read, "rb", buffering=0),
                os.fdopen(err_read, "rb", buffering=0),
            )
            killed = []
            active[0] = "selector-return"

            class TrackedSelector:
                def __init__(self):
                    self.closed = False

                def close(self):
                    self.closed = True

            selected = [None]

            def selector_then_signal():
                selected[0] = TrackedSelector()
                os.kill(os.getpid(), signal.SIGINT)
                if callable(getattr(signal, "sigpending", None)):
                    self.assertIn(signal.SIGINT, signal.sigpending())
                return selected[0]

            try:
                with self.assertRaisesRegex(
                    KeyboardInterrupt, "selector-return",
                ):
                    verifier._collect_bounded_process(
                        process, 16, 16, 1,
                        selector_factory=selector_then_signal,
                        killpg=lambda pid, sig: killed.append((pid, sig)),
                    )
                self.assertEqual(killed, [(424242, signal.SIGKILL)])
                self.assertEqual(process.wait_calls, 1)
                self.assertTrue(selected[0].closed)
                self.assertTrue(process.stdout.closed)
                self.assertTrue(process.stderr.closed)
            finally:
                os.close(out_write)
                os.close(err_write)
            self.assertEqual(set(os.listdir("/dev/fd")), baseline)
            self.assertEqual(deliveries, ["popen-return", "selector-return"])
        finally:
            signal.signal(signal.SIGINT, prior_handler)
            signal.pthread_sigmask(signal.SIG_SETMASK, prior_mask)

    def test_22_transport_close_errors_reject_success_after_bounded_retry(self) -> None:
        class FakeProcess:
            def __init__(self, stdout, stderr):
                self.stdout = stdout
                self.stderr = stderr
                self.pid = 424242
                self.returncode = None

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                self.returncode = 0
                return 0

        class FailOnceStream:
            def __init__(self, raw, fail):
                self.raw = raw
                self.fail = fail
                self.close_calls = 0

            @property
            def closed(self):
                return self.raw.closed

            def fileno(self):
                return self.raw.fileno()

            def close(self):
                self.close_calls += 1
                if self.fail and self.close_calls == 1:
                    raise OSError("synthetic pre-effect stream close failure")
                self.raw.close()

        class EmptySelector:
            def __init__(self, fail_close=False):
                self.fail_close = fail_close
                self.close_calls = 0

            def register(self, fileobj, events, data):
                return None

            def get_map(self):
                return {}

            def close(self):
                self.close_calls += 1
                if self.fail_close and self.close_calls == 1:
                    raise OSError("synthetic pre-effect selector close failure")

        for site in ("stdout", "stderr", "selector"):
            with self.subTest(site=site):
                baseline = set(os.listdir("/dev/fd"))
                out_read, out_write = os.pipe()
                err_read, err_write = os.pipe()
                stdout = FailOnceStream(
                    os.fdopen(out_read, "rb", buffering=0), site == "stdout",
                )
                stderr = FailOnceStream(
                    os.fdopen(err_read, "rb", buffering=0), site == "stderr",
                )
                selector = EmptySelector(site == "selector")
                process = FakeProcess(stdout, stderr)
                try:
                    with self.assertRaisesRegex(
                        verifier.VerificationError,
                        "resource close was not clean",
                    ):
                        verifier._collect_bounded_process(
                            process, 16, 16, 1,
                            selector_factory=lambda: selector,
                        )
                    self.assertEqual(process.poll(), 0)
                    self.assertTrue(stdout.closed)
                    self.assertTrue(stderr.closed)
                    self.assertEqual(
                        stdout.close_calls, 2 if site == "stdout" else 1,
                    )
                    self.assertEqual(
                        stderr.close_calls, 2 if site == "stderr" else 1,
                    )
                    self.assertEqual(
                        selector.close_calls, 2 if site == "selector" else 1,
                    )
                finally:
                    os.close(out_write)
                    os.close(err_write)
                self.assertEqual(set(os.listdir("/dev/fd")), baseline)

    def test_23_transport_cleanup_base_precedence_closes_every_stream(self) -> None:
        class FakeProcess:
            def __init__(self, stdout, stderr, poll_error=None):
                self.stdout = stdout
                self.stderr = stderr
                self.pid = 424242
                self.killed = False
                self.reaped = False
                self.poll_error = poll_error
                self.poll_calls = 0
                self.kill_calls = 0
                self.wait_calls = 0

            def poll(self):
                self.poll_calls += 1
                if self.poll_error is not None and self.poll_calls == 1:
                    raise self.poll_error
                return -9 if self.reaped else None

            def wait(self, timeout=None):
                self.wait_calls += 1
                if not self.killed:
                    raise TimeoutError("process was not killed")
                self.reaped = True
                return -9

        for primary_kind in ("ordinary", "base"):
            with self.subTest(primary=primary_kind, cleanup="kill-base"):
                baseline = set(os.listdir("/dev/fd"))
                out_read, out_write = os.pipe()
                err_read, err_write = os.pipe()
                process = FakeProcess(
                    os.fdopen(out_read, "rb", buffering=0),
                    os.fdopen(err_read, "rb", buffering=0),
                )
                primary_base = KeyboardInterrupt("primary transport cancellation")
                kill_base = KeyboardInterrupt("cleanup kill cancellation")

                def fail_selector():
                    if primary_kind == "base":
                        raise primary_base
                    raise RuntimeError("ordinary selector setup failure")

                def interrupt_first_kill(pid, signum):
                    process.kill_calls += 1
                    if process.kill_calls == 1:
                        raise kill_base
                    process.killed = True

                expected = primary_base if primary_kind == "base" else kill_base
                try:
                    with self.assertRaises(KeyboardInterrupt) as caught:
                        verifier._collect_bounded_process(
                            process, 16, 16, 1,
                            selector_factory=fail_selector,
                            killpg=interrupt_first_kill,
                        )
                    self.assertIs(caught.exception, expected)
                    self.assertEqual(process.kill_calls, 2)
                    self.assertEqual(process.wait_calls, 1)
                    self.assertTrue(process.killed)
                    self.assertTrue(process.reaped)
                    self.assertTrue(process.stdout.closed)
                    self.assertTrue(process.stderr.closed)
                    if primary_kind == "base":
                        self.assertIs(
                            primary_base.resource_cleanup_error, kill_base,
                        )
                    else:
                        self.assertIsInstance(kill_base.prior_error, verifier.VerificationError)
                finally:
                    os.close(out_write)
                    os.close(err_write)
                self.assertEqual(set(os.listdir("/dev/fd")), baseline)

        baseline = set(os.listdir("/dev/fd"))
        out_read, out_write = os.pipe()
        err_read, err_write = os.pipe()
        close_base = KeyboardInterrupt("post-effect stream-close cancellation")

        class PostEffectBaseStream:
            def __init__(self, raw):
                self.raw = raw
                self.calls = 0

            @property
            def closed(self):
                return self.raw.closed

            def fileno(self):
                return self.raw.fileno()

            def close(self):
                self.calls += 1
                self.raw.close()
                if self.calls == 1:
                    raise close_base

        stdout = PostEffectBaseStream(os.fdopen(out_read, "rb", buffering=0))
        stderr = os.fdopen(err_read, "rb", buffering=0)
        process = FakeProcess(
            stdout, stderr, poll_error=OSError("ordinary process poll failure"),
        )

        def kill_process(pid, signum):
            process.kill_calls += 1
            process.killed = True

        try:
            with self.assertRaises(KeyboardInterrupt) as caught:
                verifier._cleanup_spawned_git_process(process, kill_process)
            self.assertIs(caught.exception, close_base)
            self.assertIsInstance(close_base.resource_cleanup_error, OSError)
            self.assertTrue(process.killed)
            self.assertTrue(process.reaped)
            self.assertTrue(stdout.closed)
            self.assertTrue(stderr.closed)
        finally:
            os.close(out_write)
            os.close(err_write)
        self.assertEqual(set(os.listdir("/dev/fd")), baseline)

    def test_24_kill_wait_pre_post_effect_matrix_is_bounded_and_terminal(self) -> None:
        class FakeProcess:
            def __init__(self):
                self.pid = 424242
                self.killed = False
                self.reaped = False
                self.kill_calls = 0
                self.wait_calls = 0

            def poll(self):
                return -9 if self.reaped else None

        for operation in ("kill", "wait"):
            for timing in ("pre", "post"):
                for error_kind in ("ordinary", "base"):
                    with self.subTest(
                        operation=operation, timing=timing,
                        error_kind=error_kind,
                    ):
                        process = FakeProcess()
                        injected = (
                            OSError("synthetic %s %s ordinary" % (operation, timing))
                            if error_kind == "ordinary"
                            else KeyboardInterrupt(
                                "synthetic %s %s base" % (operation, timing)
                            )
                        )

                        def killpg(pid, signum):
                            process.kill_calls += 1
                            if operation == "kill" and process.kill_calls == 1:
                                if timing == "post":
                                    process.killed = True
                                raise injected
                            process.killed = True

                        def wait(timeout=None):
                            process.wait_calls += 1
                            if operation == "wait" and process.wait_calls == 1:
                                if timing == "post":
                                    process.reaped = True
                                raise injected
                            if not process.killed:
                                raise TimeoutError("wait before kill")
                            process.reaped = True
                            return -9

                        process.wait = wait
                        if error_kind == "base":
                            with self.assertRaises(KeyboardInterrupt) as caught:
                                verifier._kill_and_wait(process, killpg)
                            self.assertIs(caught.exception, injected)
                            self.assertTrue(caught.exception.git_process_reaped)
                        else:
                            with self.assertRaises(verifier.VerificationError):
                                verifier._kill_and_wait(process, killpg)
                        self.assertTrue(process.killed)
                        self.assertTrue(process.reaped)
                        self.assertLessEqual(process.kill_calls, 2)
                        self.assertLessEqual(process.wait_calls, 2)

        process = FakeProcess()
        persistent = KeyboardInterrupt("persistent pre-effect kill cancellation")

        def never_kill(pid, signum):
            process.kill_calls += 1
            raise persistent

        def never_reap(timeout=None):
            process.wait_calls += 1
            raise TimeoutError("persistent wait timeout")

        process.wait = never_reap
        with self.assertRaises(KeyboardInterrupt) as caught:
            verifier._kill_and_wait(process, never_kill)
        self.assertIs(caught.exception, persistent)
        self.assertFalse(caught.exception.git_process_killed_or_absent)
        self.assertFalse(caught.exception.git_process_reaped)
        self.assertEqual(process.kill_calls, 2)
        self.assertEqual(process.wait_calls, 2)
        self.assertIsNone(process.poll())

    def test_25_explicit_scandir_empty_nonempty_and_pack_policy(self) -> None:
        def make_repository(base):
            repository = base / "repo"
            (repository / ".git/objects/info").mkdir(parents=True)
            (repository / ".git/objects/pack").mkdir()
            (repository / ".git/refs").mkdir()
            config = repository / ".git/config"
            config.write_text(
                "[core]\n\trepositoryformatversion = 0\n\tbare = false\n",
                encoding="utf-8",
            )
            config.chmod(0o600)
            return repository

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            empty = root / "empty"
            empty.mkdir()
            baseline = set(os.listdir("/dev/fd"))
            verifier._require_absent_or_empty_directory(empty, "synthetic empty")
            self.assertEqual(set(os.listdir("/dev/fd")), baseline)
            (empty / "entry").write_bytes(b"x")
            with self.assertRaises(verifier.VerificationError):
                verifier._require_absent_or_empty_directory(
                    empty, "synthetic nonempty",
                )
            self.assertEqual(set(os.listdir("/dev/fd")), baseline)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            repository = make_repository(root)
            pack = repository / ".git/objects/pack"
            (pack / "synthetic.pack").write_bytes(b"")
            baseline = set(os.listdir("/dev/fd"))
            verifier._verify_repository_object_policy(repository)
            self.assertEqual(set(os.listdir("/dev/fd")), baseline)
            (pack / "synthetic.promisor").write_bytes(b"")
            with self.assertRaisesRegex(
                verifier.VerificationError, "promisor pack",
            ):
                verifier._verify_repository_object_policy(repository)
            self.assertEqual(set(os.listdir("/dev/fd")), baseline)

    def test_26_scandir_iteration_and_close_exception_precedence_matrix(self) -> None:
        def make_repository(base):
            repository = base / "repo"
            (repository / ".git/objects/info").mkdir(parents=True)
            (repository / ".git/objects/pack").mkdir()
            (repository / ".git/refs").mkdir()
            config = repository / ".git/config"
            config.write_text(
                "[core]\n\trepositoryformatversion = 0\n\tbare = false\n",
                encoding="utf-8",
            )
            config.chmod(0o600)
            return repository

        class OwnedIterator:
            def __init__(self, next_error, close_error, timing):
                self.descriptor = os.open("/dev/null", os.O_RDONLY)
                self.next_error = next_error
                self.close_error = close_error
                self.timing = timing
                self.next_calls = 0
                self.close_calls = 0
                self.closed = False

            def __iter__(self):
                return self

            def __next__(self):
                self.next_calls += 1
                if self.next_calls == 1:
                    raise self.next_error
                raise StopIteration

            def close(self):
                self.close_calls += 1
                if self.closed:
                    return
                if self.close_calls == 1 and self.timing == "pre":
                    raise self.close_error
                os.close(self.descriptor)
                self.closed = True
                if self.close_calls == 1 and self.timing == "post":
                    raise self.close_error

        for site in ("empty-policy", "pack-policy"):
            for primary_kind in ("ordinary", "base"):
                for close_kind in ("ordinary", "base"):
                    for timing in ("pre", "post"):
                        with self.subTest(
                            site=site, primary=primary_kind,
                            close=close_kind, timing=timing,
                        ):
                            with tempfile.TemporaryDirectory() as temporary:
                                root = Path(temporary).resolve()
                                empty = root / "empty"
                                empty.mkdir()
                                repository = make_repository(root)
                                baseline = set(os.listdir("/dev/fd"))
                                primary = (
                                    OSError("synthetic iterator ordinary failure")
                                    if primary_kind == "ordinary"
                                    else KeyboardInterrupt(
                                        "synthetic iterator base failure"
                                    )
                                )
                                close_error = (
                                    OSError("synthetic iterator close ordinary")
                                    if close_kind == "ordinary"
                                    else KeyboardInterrupt(
                                        "synthetic iterator close base"
                                    )
                                )
                                iterator = OwnedIterator(
                                    primary, close_error, timing,
                                )
                                expected_base = (
                                    primary if primary_kind == "base"
                                    else close_error if close_kind == "base"
                                    else None
                                )
                                with mock.patch.object(
                                    verifier.os, "scandir", return_value=iterator,
                                ):
                                    if expected_base is None:
                                        with self.assertRaises(
                                            verifier.VerificationError,
                                        ):
                                            if site == "empty-policy":
                                                verifier._require_absent_or_empty_directory(
                                                    empty, "synthetic empty policy",
                                                )
                                            else:
                                                verifier._verify_repository_object_policy(
                                                    repository,
                                                )
                                    else:
                                        with self.assertRaises(
                                            KeyboardInterrupt,
                                        ) as caught:
                                            if site == "empty-policy":
                                                verifier._require_absent_or_empty_directory(
                                                    empty, "synthetic empty policy",
                                                )
                                            else:
                                                verifier._verify_repository_object_policy(
                                                    repository,
                                                )
                                        self.assertIs(
                                            caught.exception, expected_base,
                                        )
                                self.assertEqual(iterator.next_calls, 1)
                                self.assertEqual(iterator.close_calls, 2)
                                self.assertTrue(iterator.closed)
                                self.assertEqual(
                                    set(os.listdir("/dev/fd")), baseline,
                                )

    def test_27_scandir_close_faults_and_pending_signal_never_return_success(self) -> None:
        def make_repository(base):
            repository = base / "repo"
            (repository / ".git/objects/info").mkdir(parents=True)
            (repository / ".git/objects/pack").mkdir()
            (repository / ".git/refs").mkdir()
            config = repository / ".git/config"
            config.write_text(
                "[core]\n\trepositoryformatversion = 0\n\tbare = false\n",
                encoding="utf-8",
            )
            config.chmod(0o600)
            return repository

        class EmptyFaultyIterator:
            def __init__(self, close_error, timing):
                self.descriptor = os.open("/dev/null", os.O_RDONLY)
                self.close_error = close_error
                self.timing = timing
                self.close_calls = 0
                self.closed = False

            def __iter__(self):
                return self

            def __next__(self):
                raise StopIteration

            def close(self):
                self.close_calls += 1
                if self.closed:
                    return
                if self.close_calls == 1 and self.timing == "pre":
                    raise self.close_error
                os.close(self.descriptor)
                self.closed = True
                if self.close_calls == 1 and self.timing == "post":
                    raise self.close_error

        for site in ("empty-policy", "pack-policy"):
            for close_kind in ("ordinary", "base"):
                for timing in ("pre", "post"):
                    with self.subTest(
                        site=site, close=close_kind, timing=timing,
                    ):
                        with tempfile.TemporaryDirectory() as temporary:
                            root = Path(temporary).resolve()
                            empty = root / "empty"
                            empty.mkdir()
                            repository = make_repository(root)
                            baseline = set(os.listdir("/dev/fd"))
                            close_error = (
                                OSError("close-only ordinary failure")
                                if close_kind == "ordinary"
                                else KeyboardInterrupt("close-only base failure")
                            )
                            iterator = EmptyFaultyIterator(
                                close_error, timing,
                            )
                            with mock.patch.object(
                                verifier.os, "scandir", return_value=iterator,
                            ):
                                if close_kind == "ordinary":
                                    with self.assertRaises(
                                        verifier.VerificationError,
                                    ):
                                        if site == "empty-policy":
                                            verifier._require_absent_or_empty_directory(
                                                empty, "synthetic empty policy",
                                            )
                                        else:
                                            verifier._verify_repository_object_policy(
                                                repository,
                                            )
                                else:
                                    with self.assertRaises(
                                        KeyboardInterrupt,
                                    ) as caught:
                                        if site == "empty-policy":
                                            verifier._require_absent_or_empty_directory(
                                                empty, "synthetic empty policy",
                                            )
                                        else:
                                            verifier._verify_repository_object_policy(
                                                repository,
                                            )
                                    self.assertIs(caught.exception, close_error)
                            self.assertEqual(iterator.close_calls, 2)
                            self.assertTrue(iterator.closed)
                            self.assertEqual(
                                set(os.listdir("/dev/fd")), baseline,
                            )

        self.assertIs(threading.current_thread(), threading.main_thread())
        prior_mask = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        unblocked = set(prior_mask)
        unblocked.discard(signal.SIGINT)
        signal.pthread_sigmask(signal.SIG_SETMASK, unblocked)
        active = [""]
        deliveries = []

        def deliver_interrupt(signum, frame):
            deliveries.append(active[0])
            raise KeyboardInterrupt("pending scandir cancellation: " + active[0])

        prior_handler = signal.signal(signal.SIGINT, deliver_interrupt)
        try:
            for site in ("empty-return", "pack-return"):
                with self.subTest(site=site):
                    with tempfile.TemporaryDirectory() as temporary:
                        root = Path(temporary).resolve()
                        empty = root / "empty"
                        empty.mkdir()
                        repository = make_repository(root)
                        pack = repository / ".git/objects/pack"
                        baseline = set(os.listdir("/dev/fd"))
                        real_scandir = verifier.os.scandir
                        sent = [False]
                        active[0] = site

                        def scandir_then_signal(path):
                            iterator = real_scandir(path)
                            target = (
                                site == "empty-return"
                                or os.path.abspath(path) == os.path.abspath(str(pack))
                            )
                            if target and not sent[0]:
                                sent[0] = True
                                os.kill(os.getpid(), signal.SIGINT)
                                if callable(getattr(signal, "sigpending", None)):
                                    self.assertIn(
                                        signal.SIGINT, signal.sigpending(),
                                    )
                            return iterator

                        with mock.patch.object(
                            verifier.os, "scandir", side_effect=scandir_then_signal,
                        ):
                            with self.assertRaisesRegex(
                                KeyboardInterrupt, site,
                            ):
                                if site == "empty-return":
                                    verifier._require_absent_or_empty_directory(
                                        empty, "pending empty policy",
                                    )
                                else:
                                    verifier._verify_repository_object_policy(
                                        repository,
                                    )
                        self.assertTrue(sent[0])
                        self.assertEqual(set(os.listdir("/dev/fd")), baseline)
            self.assertEqual(deliveries, ["empty-return", "pack-return"])
        finally:
            signal.signal(signal.SIGINT, prior_handler)
            signal.pthread_sigmask(signal.SIG_SETMASK, prior_mask)


if __name__ == "__main__":
    unittest.main()
