"""Synthetic sealed-build fixture shared by v1 verifier and v2 attester tests."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import shutil
import struct
import tempfile
from typing import Any, Iterator, Optional


def _source_proof(B: Any, V: Any, method: str) -> dict[str, Any]:
    endpoint = B.ENDPOINTS[method]
    count, digest = V.EXPECTED_TREE_PROOFS[method]
    overlay_count, overlay_digest = V.EXPECTED_OVERLAY_PROOFS[method]
    return {
        "algorithm_revision": endpoint["algorithm_revision"],
        "applied_head": endpoint["applied_head"],
        "archive_entry_count": count,
        "archive_tree_digest": digest,
        "descriptor_post_identities": B._descriptor_post_identity_list(method),
        "endpoint_tree": endpoint["tree"],
        "method": method,
        "overlay_paths": sorted(
            {
                "CMakeLists.txt", "src/main.cpp",
                *[item[1] for item in B.ARCHIVE_OVERLAYS],
                *B.EXPECTED_DESCRIPTOR_PATHS,
            }
        ),
        "post_overlay_cmake_sha256": B.EXPECTED_SINGLE_THREAD_CMAKE_POST[1],
        "post_overlay_file_count": overlay_count,
        "post_overlay_main_sha256": B.EXPECTED_DESCRIPTOR_POST[method]["src/main.cpp"][1],
        "post_overlay_tree_sha256": overlay_digest,
        "single_thread_post_identity": {
            "bytes": B.EXPECTED_SINGLE_THREAD_CMAKE_POST[0],
            "path": "CMakeLists.txt",
            "sha256": B.EXPECTED_SINGLE_THREAD_CMAKE_POST[1],
        },
    }


def _load_string_command(command: int, path: str, header_bytes: int) -> bytes:
    encoded = path.encode("utf-8") + b"\0"
    command_bytes = (header_bytes + len(encoded) + 7) & ~7
    if header_bytes == 24:
        header = struct.pack("<IIIIII", command, command_bytes, header_bytes, 0, 0, 0)
    else:
        header = struct.pack("<III", command, command_bytes, header_bytes)
    return header + encoded + b"\0" * (command_bytes - header_bytes - len(encoded))


def _chained_fixups_payload(
    import_symbol: str,
    import_format: int = 1,
    extra_pool_symbols: tuple[str, ...] = (),
) -> bytes:
    """Return the target linker's sentinel/string/pointer-aligned pool layout."""
    pool_symbols = (import_symbol, *extra_pool_symbols)
    encoded_symbols = tuple(
        item.encode("utf-8", "strict") for item in pool_symbols
    )
    if any(not item or b"\0" in item for item in encoded_symbols):
        raise AssertionError("synthetic chained pool symbol differs")
    first_name_offset = 1
    entries = {
        1: struct.pack("<I", 1 | (first_name_offset << 9)),
        2: struct.pack("<Ii", 1 | (first_name_offset << 9), -7),
        3: struct.pack("<QQ", 1 | (first_name_offset << 32), 9),
    }
    entry = entries.get(import_format)
    if entry is None:
        raise AssertionError("synthetic chained import format differs")
    starts_offset = 32
    starts_blob = struct.pack("<I", 0)
    import_alignment = 8 if import_format == 3 else 4
    imports_offset = (
        starts_offset + len(starts_blob) + import_alignment - 1
    ) & -import_alignment
    symbols_offset = imports_offset + len(entry)
    symbol_pool = b"\0" + b"".join(
        item + b"\0" for item in encoded_symbols
    )
    raw = (
        struct.pack(
            "<7I", 0, starts_offset, imports_offset, symbols_offset,
            1, import_format, 0,
        )
        + b"\0" * (starts_offset - 28)
        + starts_blob
        + b"\0" * (imports_offset - starts_offset - len(starts_blob))
        + entry
        + symbol_pool
    )
    return raw + b"\0" * ((-len(raw)) % 8)


def _signed_macho(
    B: Any, V: Any, commands: list[bytes], payload: bytes, identifier: str,
    auxiliary_blobs: tuple[tuple[int, bytes], ...] = (),
    section_sized_exec_limit: bool = False,
    header_flags: Optional[int] = None,
    undefined_symbol: str = "_exit",
    dyld_bind_symbol: Optional[str] = None,
    chained_import_symbol: Optional[str] = None,
    chained_import_format: int = 1,
    chained_pool_extra_symbols: tuple[str, ...] = (),
    chained_unaligned_data_offset: bool = False,
    export_symbol: Optional[str] = None,
    data_in_code_empty_profile: Optional[str] = None,
    data_in_code_entries: Optional[tuple[tuple[int, int, int], ...]] = None,
    data_in_code_trailing_bytes: bytes = b"",
) -> bytes:
    if chained_pool_extra_symbols and chained_import_symbol is None:
        raise AssertionError("synthetic chained pool extras lack an import")
    if data_in_code_empty_profile not in {None, "boundary", "interior", "zero"}:
        raise AssertionError("synthetic LC_DATA_IN_CODE empty profile differs")
    if data_in_code_empty_profile is not None and data_in_code_entries is not None:
        raise AssertionError("synthetic LC_DATA_IN_CODE profiles overlap")
    if data_in_code_entries is None and data_in_code_trailing_bytes:
        raise AssertionError("synthetic LC_DATA_IN_CODE trailing bytes lack entries")
    uuid_bytes = hashlib.sha256(
        b"dancers-synthetic-lc-uuid-v1\0" + identifier.encode("utf-8")
    ).digest()[:16]
    uuid_command = struct.pack("<II", B.LC_UUID, 24) + uuid_bytes
    build_version_command = struct.pack(
        "<IIIIIIII", B.LC_BUILD_VERSION, 32, 1, 0x001A0000,
        0x001A0500, 1, 3, 0x03BC0600,
    )
    base_commands = [*commands, uuid_command, build_version_command]
    identifier_raw = identifier.encode("utf-8") + b"\0"
    identifier_area = identifier_raw + b"\0" * ((-len(identifier_raw)) % 4)
    # Parser-complete synthetic arm64 layout: one RX __TEXT segment with a
    # realistic __text section/LC_MAIN entry, followed by a non-executable
    # __LINKEDIT holding LC_SYMTAB/string bytes and the EOF CodeDirectory.
    text_segment_bytes = 72 + 80
    linkedit_segment_bytes = 72
    fixed_command_bytes = (
        text_segment_bytes + linkedit_segment_bytes
        + sum(len(item) for item in base_commands)
        + 24  # LC_MAIN
        + 24  # LC_SYMTAB
        + (48 if dyld_bind_symbol is not None else 0)
        + (16 if chained_import_symbol is not None else 0)
        + (16 if export_symbol is not None else 0)
        + (
            32
            if data_in_code_empty_profile is not None
            or data_in_code_entries is not None
            else 0
        )
        + 16  # LC_CODE_SIGNATURE
    )
    text_offset = 32 + fixed_command_bytes
    linkedit_offset = (text_offset + len(payload) + 0xFFF) & ~0xFFF
    undefined_raw = undefined_symbol.encode("utf-8", "strict")
    if not undefined_raw or b"\0" in undefined_raw:
        raise AssertionError("synthetic undefined symbol differs")
    strings = b" \0_main\0" + undefined_raw + b"\0"
    main_string_index = 2
    exit_string_index = 8
    symbols = (
        struct.pack("<IBBHQ", main_string_index, 0x0F, 1, 0, 0x100000000 + text_offset)
        + struct.pack("<IBBHQ", exit_string_index, 0x01, 0, 0, 0)
    )
    has_data_in_code = (
        data_in_code_empty_profile is not None or data_in_code_entries is not None
    )
    function_starts_blob = (
        b"\x08\0\0\0\0\0\0\0"
        if has_data_in_code else b""
    )
    function_starts_offset = linkedit_offset
    data_in_code_offset = function_starts_offset + len(function_starts_blob)
    data_in_code_blob = (
        b"".join(
            struct.pack("<IHH", text_offset + offset, size, kind)
            for offset, size, kind in data_in_code_entries
        ) + data_in_code_trailing_bytes
        if data_in_code_entries is not None else b""
    )
    symbol_offset = data_in_code_offset + len(data_in_code_blob)
    string_offset = symbol_offset + len(symbols)
    string_bytes = len(strings)
    linkedit_blobs: list[tuple[int, bytes]] = []
    dyld_commands: list[bytes] = []
    linkedit_cursor = string_offset + string_bytes
    if dyld_bind_symbol is not None:
        encoded = dyld_bind_symbol.encode("utf-8", "strict")
        if not encoded or b"\0" in encoded:
            raise AssertionError("synthetic dyld bind symbol differs")
        bind_blob = b"\x11\x40" + encoded + b"\0\x51\x70\0\x90\0"
        bind_offset = linkedit_cursor
        linkedit_blobs.append((bind_offset, bind_blob))
        linkedit_cursor += len(bind_blob)
        dyld_commands.append(struct.pack(
            "<II10I", B.LC_DYLD_INFO_ONLY, 48,
            0, 0, bind_offset, len(bind_blob), 0, 0, 0, 0, 0, 0,
        ))
    if chained_import_symbol is not None:
        chained_blob = _chained_fixups_payload(
            chained_import_symbol,
            chained_import_format,
            chained_pool_extra_symbols,
        )
        chained_offset = (linkedit_cursor + 7) & ~7
        if chained_unaligned_data_offset:
            chained_offset += 4
        linkedit_blobs.append((chained_offset, chained_blob))
        linkedit_cursor = chained_offset + len(chained_blob)
        dyld_commands.append(struct.pack(
            "<IIII", B.LC_DYLD_CHAINED_FIXUPS, 16,
            chained_offset, len(chained_blob),
        ))
    if export_symbol is not None:
        encoded = export_symbol.encode("utf-8", "strict")
        if not encoded or b"\0" in encoded or len(encoded) > 100:
            raise AssertionError("synthetic export symbol differs")
        child_offset = 4 + len(encoded)
        export_blob = (
            b"\0\x01" + encoded + b"\0" + bytes([child_offset])
            + b"\x02\0\0\0"
        )
        export_offset = linkedit_cursor
        linkedit_blobs.append((export_offset, export_blob))
        linkedit_cursor += len(export_blob)
        dyld_commands.append(struct.pack(
            "<IIII", B.LC_DYLD_EXPORTS_TRIE, 16,
            export_offset, len(export_blob),
        ))
    data_offset = (linkedit_cursor + 15) & ~15
    page_bytes = 1 << 12
    code_slots = (data_offset + page_bytes - 1) // page_bytes
    hash_offset = 88 + len(identifier_area)
    code_directory_bytes = hash_offset + code_slots * 32
    index_bytes = 12 + 8 * (1 + len(auxiliary_blobs))
    code_directory_offset = index_bytes
    blob_offsets: list[tuple[int, int, bytes]] = []
    next_offset = code_directory_offset + code_directory_bytes
    for slot, blob in auxiliary_blobs:
        blob_offsets.append((slot, next_offset, blob))
        next_offset += len(blob)
    superblob_bytes = next_offset
    signature_bytes = (superblob_bytes + 15) & ~15
    total_bytes = data_offset + signature_bytes
    text_file_bytes = linkedit_offset
    text_vm_bytes = (text_file_bytes + 0xFFF) & ~0xFFF
    linkedit_file_bytes = total_bytes - linkedit_offset
    linkedit_vm_bytes = (linkedit_file_bytes + 0xFFF) & ~0xFFF
    text_segment = (
        struct.pack(
            "<II16sQQQQiiII",
            B.LC_SEGMENT_64, text_segment_bytes, b"__TEXT\0" + b"\0" * 9,
            0x100000000, text_vm_bytes, 0, text_file_bytes, 5, 5, 1, 0,
        )
        + struct.pack(
            "<16s16sQQIIIIIIII",
            b"__text\0" + b"\0" * 9,
            b"__TEXT\0" + b"\0" * 9,
            0x100000000 + text_offset, len(payload), text_offset, 4,
            0, 0, 0x80000400, 0, 0, 0,
        )
    )
    linkedit_segment = struct.pack(
        "<II16sQQQQiiII",
        B.LC_SEGMENT_64, linkedit_segment_bytes,
        b"__LINKEDIT\0" + b"\0" * 6,
        0x100000000 + linkedit_offset, linkedit_vm_bytes,
        linkedit_offset, linkedit_file_bytes, 1, 1, 0, 0,
    )
    main_command = struct.pack("<IIQQ", B.LC_MAIN, 24, text_offset, 0)
    symtab_command = struct.pack(
        "<IIIIII", B.LC_SYMTAB, 24, symbol_offset, 2,
        string_offset, string_bytes,
    )
    signature_command = struct.pack(
        "<IIII", B.LC_CODE_SIGNATURE, 16, data_offset, signature_bytes
    )
    linkedit_metadata_commands: list[bytes] = []
    if has_data_in_code:
        if data_in_code_empty_profile is None:
            command_data_offset = data_in_code_offset
            command_data_bytes = len(data_in_code_blob)
        else:
            command_data_offset = {
                "boundary": symbol_offset,
                "interior": symbol_offset + 1,
                "zero": 0,
            }[data_in_code_empty_profile]
            command_data_bytes = 0
        linkedit_metadata_commands.extend((
            struct.pack(
                "<IIII", B.LC_FUNCTION_STARTS, 16,
                function_starts_offset, len(function_starts_blob),
            ),
            struct.pack(
                "<IIII", B.LC_DATA_IN_CODE, 16,
                command_data_offset, command_data_bytes,
            ),
        ))
    commands = [
        text_segment, linkedit_segment, *base_commands,
        main_command, symtab_command, *dyld_commands,
        *linkedit_metadata_commands, signature_command,
    ]
    command_blob = b"".join(commands)
    if len(command_blob) != fixed_command_bytes:
        raise AssertionError("synthetic Mach-O command sizing drifted")
    raw = (
        struct.pack(
            "<IiiIIIII",
            0xFEEDFACF,
            V.MACHO_CPU_ARM64,
            0,
            V.MACHO_EXECUTE,
            len(commands),
            len(command_blob),
            (
                header_flags
                if header_flags is not None
                else (0x00200085 if section_sized_exec_limit else 0x00218085)
            ),
            0,
        )
        + command_blob
        + payload
    )
    raw += b"\0" * (linkedit_offset - len(raw))
    raw += function_starts_blob + data_in_code_blob + symbols + strings
    for offset, blob in linkedit_blobs:
        raw += b"\0" * (offset - len(raw))
        raw += blob
    raw += b"\0" * (data_offset - len(raw))
    code_hashes = b"".join(
        hashlib.sha256(raw[offset : min(offset + page_bytes, data_offset)]).digest()
        for offset in range(0, data_offset, page_bytes)
    )
    code_directory = (
        struct.pack(
            ">9I4BIIIIQQQQ",
            B.CSMAGIC_CODEDIRECTORY,
            code_directory_bytes,
            0x20400,
            0x00020002,
            hash_offset,
            88,
            0,
            code_slots,
            data_offset,
            32,
            2,
            0,
            12,
            0,
            0,
            0,
            0,
            0,
            0,
            len(payload) if section_sized_exec_limit else text_file_bytes,
            1,
        )
        + identifier_area
        + code_hashes
    )
    superblob = (
        struct.pack(
            ">III", B.CSMAGIC_EMBEDDED_SIGNATURE, superblob_bytes,
            1 + len(auxiliary_blobs),
        )
        + struct.pack(">II", 0, code_directory_offset)
        + b"".join(struct.pack(">II", slot, offset) for slot, offset, _ in blob_offsets)
        + code_directory
        + b"".join(blob for _, _, blob in blob_offsets)
    )
    return raw + superblob + b"\0" * (signature_bytes - len(superblob))


def _binary(
    B: Any,
    V: Any,
    repository: Path,
    product_id: str,
    observer: bool,
    timing_marker: bool,
    dynamic_fault: bool,
    runtime_product: bool = True,
) -> bytes:
    dynamic_paths = (
        [
            "/System/Library/Frameworks/Cocoa.framework/Versions/A/Cocoa",
            "/private/untrusted.dylib" if dynamic_fault else "/usr/lib/libc++.1.dylib",
            "/usr/lib/libSystem.B.dylib",
        ]
        if runtime_product
        else [
            "/System/Library/Frameworks/Cocoa.framework/Versions/A/Cocoa",
            "/private/untrusted.dylib" if dynamic_fault else "/usr/lib/libc++.1.dylib",
            "/usr/lib/libSystem.B.dylib",
        ]
    )
    commands = [
        _load_string_command(0x0000000C, path, 24) for path in dynamic_paths
    ]
    commands.append(_load_string_command(B.LC_LOAD_DYLINKER, "/usr/lib/dyld", 12))
    payload = (
        product_id.encode("ascii")
        + b"\0"
        + str(repository).encode("utf-8")
        + b"\0"
        + B.PROBE_ENV_NAME.encode("ascii")
        + b"\0"
        + B.PROBE_ENV_VALUE.encode("ascii")
        + b"\0"
        + B.SENTINEL_PATH.encode("ascii")
        + b"\0"
        + B.SENTINEL_SHA256.encode("ascii")
        + b"\0"
    )
    payload += b"".join(getattr(V, "LEGACY_OBSERVER_ENV_GUARD_MARKERS", ()))
    observer_markers = getattr(
        V, "OBSERVER_ONLY_MARKERS", getattr(V, "OBSERVER_MARKERS", ())
    )
    if observer:
        payload += b"".join(observer_markers)
    elif timing_marker:
        payload += observer_markers[0]
    identifier = (
        B.SHIM_BINARY_NAME
        if not runtime_product
        else (
            B.DESCRIPTOR_TEST_TARGET
            if product_id.endswith("-descriptor-test")
            else "dancers"
        )
    )
    return _signed_macho(
        B, V, commands, payload, identifier,
        section_sized_exec_limit=False,
        header_flags=(0x00200085 if not runtime_product else 0x00218085),
        chained_import_symbol=(
            "_synthetic_descriptor_import"
            if product_id.endswith("-descriptor-test") else None
        ),
        data_in_code_empty_profile=(
            "boundary" if product_id.endswith("-descriptor-test") else None
        ),
    )


def _toolchain(B: Any, repository: Path) -> dict[str, Any]:
    fake = {
        "bytes": 1,
        "path": "/synthetic/pixi/tool",
        "sha256": hashlib.sha256(b"x").hexdigest(),
    }
    driver_linker_path = str(repository / ".pixi/envs/default/bin/ld")
    return {
        "compiler_driver_entries": {
            name: {
                "path": str(repository / ".pixi/envs/default/bin" / name),
                "resolved_path": fake["path"],
                "target": "clang-19",
            }
            for name in ("clang", "clang++")
        },
        "deployment_target": B.DEPLOYMENT_TARGET,
        "driver_linker_entry": {
            "path": driver_linker_path,
            "resolved_path": fake["path"],
            "target": "arm64-apple-darwin20.0.0-ld",
        },
        "openmp_version_from_lock": "19.1.7",
        "openmp_policy": {
            "compile_activation_forbidden": True,
            "logical_threads": 1,
            "runtime_library_loaded": False,
            "source_layer": B.INPUT_IDENTITIES["single_thread_patch"][0],
            "symbol_representations_scanned": [
                "LC_SYMTAB nlist defined and undefined names",
                "LC_DYLD_INFO bind weak-bind and lazy-bind opcode names",
                "LC_DYLD_CHAINED_FIXUPS import table and complete symbol pool",
                "LC_DYLD_INFO export trie and LC_DYLD_EXPORTS_TRIE names",
            ],
            "symbol_prefixes_forbidden": list(
                B.FORBIDDEN_OPENMP_SYMBOL_PREFIXES
            ),
        },
        "platform": "osx-arm64",
        "provenance_boundary": {
            "entrypoint_bytes_do_not_authenticate_installed_transitive_closure": True,
            "external_host_toolchain_assumptions": [
                "all unrecorded transitive dynamic libraries, resource directories, configuration files, and helper inputs used by the recorded Pixi, CMake, Ninja, Git, clang, ld, nm, ar, ranlib, otool, xcrun, xcodebuild, and uname entrypoints",
                "Xcode SDK 26.5 contents and the relevant macOS system platform closure",
                "recorded lexical tool entrypoints do not daemonize or escape their fresh owned process group",
            ],
            "git_no_lazy_fetch": "1",
            "non_daemonizing_owned_process_group_required": True,
            "quiescent_exclusive_build_required": True,
            "sealed_binary_qualification_independent": True,
            "tool_entrypoint_bytes_lock_flags_and_versions_recorded": True,
            "tool_transitive_closure_exact_provenance": False,
            "use_bound_toolchain_provenance_claim": False,
        },
        "probe_outputs": {
            "architecture": "arm64",
            "cmake": "cmake version 4.4.0",
            "compiler_c": "clang version 19.1.7",
            "compiler_c_linker": driver_linker_path,
            "compiler_cxx": "clang version 19.1.7",
            "compiler_cxx_linker": driver_linker_path,
            "git": "git version 2.55.0",
            "ninja": "1.13.2",
            "sdk_build": "25F70",
            "sdk_path": "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs/MacOSX26.5.sdk",
            "sdk_version": "26.5",
            "xcode": "Xcode 26.6\nBuild version 17F113",
        },
        "tools": {
            "ar": dict(fake), "clang": dict(fake), "clang++": dict(fake),
            "cmake": dict(fake), "git": dict(fake), "ld": dict(fake),
            "ninja": dict(fake), "nm": dict(fake), "ranlib": dict(fake),
            "otool": dict(fake), "uname": dict(fake),
            "xcodebuild": dict(fake), "xcrun": dict(fake),
        },
    }


def _chmod_publication(root: Path) -> None:
    for directory, dirnames, filenames in os.walk(root, topdown=False):
        for name in filenames:
            path = Path(directory) / name
            os.chmod(path, 0o555 if path.lstat().st_mode & 0o111 else 0o444)
        for name in dirnames:
            os.chmod(Path(directory) / name, 0o555)
    os.chmod(root, 0o555)


@contextmanager
def sealed_fixture(
    source_root: Path,
    B: Any,
    V: Any,
    *,
    dynamic_fault: bool = False,
    runtime_signature_fault: bool = False,
    shim_dynamic_fault: bool = False,
    shim_signature_fault: bool = False,
    timing_marker: bool = False,
) -> Iterator[tuple[Path, Path]]:
    with tempfile.TemporaryDirectory(prefix="headless-runtime-v1-fixture-") as name:
        repository = Path(name).resolve()
        for relative, _, _ in B.INPUT_IDENTITIES.values():
            source = source_root / relative
            destination = repository / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        for relative in (
            "scripts/comparison/build_headless_runtime_artifacts_v1.py",
            "scripts/comparison/verify_headless_runtime_build_v1.py",
            "scripts/comparison/attest_headless_binaries_v2.py",
        ):
            destination = repository / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_root / relative, destination)
        build_root = repository / B.BUILD_PREFIX / B.DESIGN_SHA256
        build_root.mkdir(parents=True)
        sentinel = (repository / B.SENTINEL_PATH).read_bytes()
        pass_records: list[dict[str, Any]] = []
        binaries: dict[str, bytes] = {}
        descriptor_binaries: dict[str, bytes] = {}
        runtime_macho: dict[str, dict[str, Any]] = {}
        clean_shim_binary = _binary(
            B, V, repository, B.SHIM_PRODUCT_ID, False, False, shim_dynamic_fault,
            runtime_product=False,
        )
        shim_macho = B._parse_macho(
            clean_shim_binary, "synthetic spawn shim", B.MACHO_EXECUTE
        )
        if shim_signature_fault:
            changed = bytearray(clean_shim_binary)
            changed[shim_macho["code_signature"]["data_offset"] - 1] ^= 1
            shim_binary = bytes(changed)
        else:
            shim_binary = clean_shim_binary
        for product_id, method, role, observer in B.MATRIX:
            clean_binary = _binary(
                B, V, repository, product_id, observer, timing_marker, dynamic_fault
            )
            runtime_macho[product_id] = B._parse_macho(
                clean_binary, "synthetic dancers", B.MACHO_EXECUTE
            )
            if runtime_signature_fault and product_id == B.MATRIX[0][0]:
                changed = bytearray(clean_binary)
                changed[
                    runtime_macho[product_id]["code_signature"]["data_offset"] - 1
                ] ^= 1
                binaries[product_id] = bytes(changed)
            else:
                binaries[product_id] = clean_binary
            descriptor_binaries[product_id] = _binary(
                B, V, repository, product_id + "-descriptor-test", False, False,
                False, runtime_product=True,
            )
        for pass_number in (1, 2):
            for product_id, method, role, observer in B.MATRIX:
                binary = binaries[product_id]
                descriptor_binary = descriptor_binaries[product_id]
                pass_records.append({
                    "binary_bytes": len(binary),
                    "binary_sha256": B._sha256(binary),
                    "configure_receipt": {
                        "cmake_cache_bytes": 1,
                        "cmake_cache_sha256": B._sha256(b"c"),
                        "compiler_driver_flag_receipt": {
                            "authenticated_compile_action_count": (
                                len(B.GBP_PROJECT_SOURCES)
                                + len(B.DANCERS_TIMING_SOURCES)
                                + (len(B.OBSERVER_PROJECT_SOURCES) if observer else 0)
                                + sum(
                                    len(sources)
                                    for sources in B.CONFIGURED_TEST_SOURCES.values()
                                )
                                + sum(
                                    len(sources)
                                    for sources in B.CONFIGURED_AUXILIARY_SOURCES.values()
                                )
                            ),
                            "delivery": (
                                "explicit-cmake-language-flags-not-ambient-environment"
                            ),
                            "exact_cardinality_per_action": 1,
                            "flag_count": 5,
                            "flag_list_sha256": B._sha256(
                                b"synthetic authenticated global compiler flags"
                            ),
                            "unused_argument_suppression": (
                                B.DRIVER_UNUSED_ARGUMENT_SUPPRESSION
                            ),
                        },
                        "compile_commands_bytes": 1,
                        "compile_commands_sha256": B._sha256(b"j"),
                        "configured_auxiliary_compile_action_count": sum(
                            len(sources)
                            for sources in B.CONFIGURED_AUXILIARY_SOURCES.values()
                        ),
                        "configured_project_compile_action_count": (
                            len(B.GBP_PROJECT_SOURCES)
                            + len(B.DANCERS_TIMING_SOURCES)
                            + (len(B.OBSERVER_PROJECT_SOURCES) if observer else 0)
                            + sum(
                                len(sources)
                                for sources in B.CONFIGURED_TEST_SOURCES.values()
                            )
                            + sum(
                                len(sources)
                                for sources in B.CONFIGURED_AUXILIARY_SOURCES.values()
                            )
                        ),
                        "configured_test_compile_action_count": sum(
                            len(sources)
                            for sources in B.CONFIGURED_TEST_SOURCES.values()
                        ),
                        "descriptor_test_compile_action_count": len(B.DESCRIPTOR_TEST_SOURCES),
                        "gbp_compile_action_count": len(B.GBP_PROJECT_SOURCES),
                        "ninja_build_graph_bytes": 1,
                        "ninja_build_graph_sha256": B._sha256(b"n"),
                        "openmp_compile_or_link_input_present": False,
                        "product_compile_action_count": len(
                            B.GBP_PROJECT_SOURCES | B.DANCERS_TIMING_SOURCES
                            | (B.OBSERVER_PROJECT_SOURCES if observer else set())
                        ),
                        "project_compile_action_count": (
                            len(B.GBP_PROJECT_SOURCES)
                            + len(B.DANCERS_TIMING_SOURCES)
                            + (len(B.OBSERVER_PROJECT_SOURCES) if observer else 0)
                            + sum(
                                len(sources)
                                for sources in B.CONFIGURED_TEST_SOURCES.values()
                            )
                            + sum(
                                len(sources)
                                for sources in B.CONFIGURED_AUXILIARY_SOURCES.values()
                            )
                        ),
                    },
                    "dependency_sources": {
                        dependency: {
                            key: expected[key]
                            for key in ("file_count", "regular_bytes", "tree_sha256")
                        }
                        for dependency, expected in B.DEPENDENCY_SOURCE_PROOFS.items()
                    },
                    "descriptor_test_binary_bytes": len(descriptor_binary),
                    "descriptor_test_binary_sha256": B._sha256(descriptor_binary),
                    "descriptor_test_macho": B._parse_macho(
                        descriptor_binary, "synthetic descriptor test", B.MACHO_EXECUTE
                    ),
                    "descriptor_test_nm_undefined_symbols_sha256": B._sha256(
                        b"_exit"
                    ),
                    "macho": runtime_macho[product_id],
                    "method": method,
                    "observer_compiled": observer,
                    "nm_undefined_symbols_sha256": B._sha256(b"_exit"),
                    "pass": pass_number,
                    "product_id": product_id,
                    "role": role,
                    "source_proof": _source_proof(B, V, method),
                    "quiescent_drift_detection": {
                        "active_aba_provenance_claim": False,
                        "binary_path_pre_post_drift_checked": True,
                        "quiescent_exclusive_build_required": True,
                        "source_path_pre_post_drift_checked": True,
                        "tool_path_pre_post_drift_checked": True,
                        "use_bound_provenance_claim": False,
                    },
                })
                log_base = build_root / "logs" / f"pass-{pass_number}"
                for suffix in (
                    "build.log", "configure.log", "descriptor-nm-undefined.log",
                    "nm-undefined.log", "otool-load.log", "otool-rpath.log",
                ):
                    path = log_base / f"{product_id}.{suffix}"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(f"synthetic {product_id} {suffix}\n".encode("ascii"))
        toolchain = _toolchain(B, repository)
        synthetic_staging = build_root.parent / ".staging-123-0000000000000000"
        shim_passes: list[dict[str, Any]] = []
        for pass_number in (1, 2):
            shim_source = (
                synthetic_staging / "work" / f"shim-pass-{pass_number}"
                / "source" / Path(B.SHIM_SOURCE_PATH).name
            )
            shim_output = (
                synthetic_staging / "work" / f"shim-pass-{pass_number}"
                / "output" / B.SHIM_BINARY_NAME
            )
            shim_passes.append({
                "binary_bytes": len(shim_binary),
                "binary_sha256": B._sha256(shim_binary),
                "compile_argv": B._shim_compile_argv(
                    Path(toolchain["compiler_driver_entries"]["clang"]["path"]),
                    Path(toolchain["tools"]["ld"]["path"]),
                    Path(toolchain["probe_outputs"]["sdk_path"]),
                    shim_source,
                    shim_output,
                ),
                "environment": B._shim_build_env(
                    repository,
                    synthetic_staging / "work" / f"shim-pass-{pass_number}" / "home",
                    Path(toolchain["compiler_driver_entries"]["clang"]["path"]),
                    Path(toolchain["tools"]["ld"]["path"]),
                ),
                "macho": shim_macho,
                "nm_undefined_symbols_sha256": B._sha256(b"_exit"),
                "pass": pass_number,
                "product_id": B.SHIM_PRODUCT_ID,
                "runtime_descriptor_contract": {
                    "descriptor_fds": B.SHIM_DESCRIPTOR_FDS,
                    "launch": B.SHIM_LAUNCH_CONTRACT,
                },
                "sdk": {
                    "build": toolchain["probe_outputs"]["sdk_build"],
                    "deployment_target": toolchain["deployment_target"],
                    "path": toolchain["probe_outputs"]["sdk_path"],
                    "version": toolchain["probe_outputs"]["sdk_version"],
                },
                "source_identities": [
                    B._identity(
                        B.SHIM_SOURCE_PATH, (repository / B.SHIM_SOURCE_PATH).read_bytes()
                    ),
                    B._identity(
                        B.SHIM_HEADER_PATH, (repository / B.SHIM_HEADER_PATH).read_bytes()
                    ),
                ],
                "tool_identities": {
                    "clang": toolchain["tools"]["clang"],
                    "clang_driver_entry": toolchain["compiler_driver_entries"]["clang"],
                    "driver_linker_entry": toolchain["driver_linker_entry"],
                    "ld": toolchain["tools"]["ld"],
                    "nm": toolchain["tools"]["nm"],
                },
                "quiescent_drift_detection": {
                    "active_aba_provenance_claim": False,
                    "binary_path_pre_post_drift_checked": True,
                    "quiescent_exclusive_build_required": True,
                    "source_path_pre_post_drift_checked": True,
                    "tool_path_pre_post_drift_checked": True,
                    "use_bound_provenance_claim": False,
                },
            })
            shim_log = (
                build_root / "logs" / f"pass-{pass_number}"
                / f"{B.SHIM_PRODUCT_ID}.build.log"
            )
            shim_log.parent.mkdir(parents=True, exist_ok=True)
            shim_log.write_bytes(f"synthetic shim pass {pass_number}\n".encode("ascii"))
            shim_nm_log = (
                build_root / "logs" / f"pass-{pass_number}"
                / f"{B.SHIM_PRODUCT_ID}.nm-undefined.log"
            )
            shim_nm_log.write_bytes(
                f"synthetic shim nm pass {pass_number}\n".encode("ascii")
            )
        for product_id, binary in binaries.items():
            path = build_root / "products" / product_id / "dancers"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(binary)
            os.chmod(path, 0o700)
            descriptor_path = path.parent / B.DESCRIPTOR_TEST_TARGET
            descriptor_path.write_bytes(descriptor_binaries[product_id])
            os.chmod(descriptor_path, 0o700)
        shim_path = build_root / "products" / B.SHIM_PRODUCT_ID / B.SHIM_BINARY_NAME
        shim_path.parent.mkdir(parents=True, exist_ok=True)
        shim_path.write_bytes(shim_binary)
        os.chmod(shim_path, 0o500)
        artifacts = B._collect_artifacts(build_root)
        design_raw = (repository / B.DESIGN_PATH).read_bytes()
        manifest = {
            "artifacts": artifacts,
            "build": {
                "configure_count": 8,
                "descriptor_test_target": B.DESCRIPTOR_TEST_TARGET,
                "direct_compile_count": 2,
                "matrix_order": [item[0] for item in B.MATRIX],
                "pass_count": 2,
                "published_product_count": 5,
                "runtime_product_count": 4,
                "shim_product_id": B.SHIM_PRODUCT_ID,
                "two_pass_binary_identity": True,
            },
            "classification": {
                **B.SCIENCE_FLAGS,
                "build_complete": True,
                "build_only_engineering_artifact": True,
                "host_bound": True,
                "portable": False,
            },
            "design": B._identity(B.DESIGN_PATH, design_raw),
            "inputs": B.authenticate_repository_inputs(repository),
            "operations": {
                **B.OPERATIONS,
                "build_invoked": True,
                "configure_invoked": True,
                "ctest_invoked": False,
                "descriptor_test_product_invoked": False,
                "runtime_product_invoked": False,
                "runtime_root_activation_smoke": "pending-qualified-suspended-image-cdhash-launcher",
                "shim_compile_invoked": True,
                "shim_product_invoked": False,
            },
            "passes": pass_records,
            "repository": {
                "design_base_commit": B.BASE_HEAD,
                "design_base_tree": B.BASE_TREE,
                "head": B.IMPLEMENTATION_ANCESTOR,
                "implementation_ancestor": B.IMPLEMENTATION_ANCESTOR,
                "implementation_ancestor_parent": B.IMPLEMENTATION_ANCESTOR_PARENT,
                "implementation_ancestor_tree": B.IMPLEMENTATION_ANCESTOR_TREE,
                "immutable_design_receipts": {
                    "cmake_patch_commit": "80ca2738505d59cabe7f5b77ec7735046663dc59",
                    "cmake_patch_parent": "f77437d47fbe3251317f7405cc2a2fd32f5575f4",
                    "cross_method_path_count": 12,
                    "cross_method_path_list_sha256": "e9439411a8b16f8cb459f4345091d44d945543eedaf0470ca391386aa44637c8",
                    "descriptor_patch_path_count": 12,
                    "descriptor_patch_path_list_sha256": "251154ccd86b4489614af8607da030b42735c12fb2216aede83b2755b6b31099",
                    "design_base_tree_verified": True,
                    "endpoint_cmake_and_main_bytes_identical": True,
                },
                "tree": B.IMPLEMENTATION_ANCESTOR_TREE,
            },
            "runtime_artifact_root": {
                "canonical_absolute_path": str(repository),
                "identical_for_all_products": True,
                "sentinel": B._identity(B.SENTINEL_PATH, sentinel),
                "source_copy_roots_forbidden": True,
            },
            "schema_version": B.MANIFEST_SCHEMA,
            "shim_passes": shim_passes,
            "toolchain": toolchain,
        }
        manifest_raw = B._canonical_json(manifest)
        (build_root / "build-manifest-v1.json").write_bytes(manifest_raw)
        digest = hashlib.sha256(b"dancers-headless-runtime-artifacts-v1\0")
        for artifact in artifacts:
            digest.update(B._canonical_json(artifact))
        seal = {
            "artifact_count": len(artifacts),
            "artifact_inventory_sha256": digest.hexdigest(),
            "build_complete": True,
            "design_sha256": B.DESIGN_SHA256,
            "manifest": B._identity("build-manifest-v1.json", manifest_raw),
            "operations": B.OPERATIONS,
            "schema_version": B.SEAL_SCHEMA,
            "science_flags": B.SCIENCE_FLAGS,
        }
        (build_root / "BUILD_COMPLETE.seal.json").write_bytes(B._canonical_json(seal))
        _chmod_publication(build_root)
        try:
            yield repository, build_root
        finally:
            for directory, dirnames, _ in os.walk(repository, topdown=True, followlinks=False):
                try:
                    os.chmod(directory, 0o700)
                except OSError:
                    pass
                for child in dirnames:
                    try:
                        os.chmod(Path(directory) / child, 0o700)
                    except OSError:
                        pass
