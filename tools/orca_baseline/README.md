# Source-pinned RVO2 wrapper

This target is the restricted, obstacle-free ORCA adapter for
`dancers-orca-v1`. It uses only the pinned public RVO2 API, preserves native
binary32 states, and has no numeric tuning flags.

## Admission model

- CMake requires the exact clean RVO2 Git checkout at the frozen commit and
  tree, and independently hashes every parsed or compiled source/header/CMake
  input. Git-less source archives are rejected.
- OpenMP must be absent from the effective compiler flags, and RVO2 is linked
  statically.
- A smoke build uses `ORCA_INPUT_MANIFEST_SHA256=smoke-only-unbound` and cannot
  enter publication `run` mode.
- A publication build embeds the exact frozen `manifest.json` SHA-256. The
  executable rejects every other input root before reading a schedule row.
- Publication mode accepts only `--attempt-sequence`; all scene, cadence,
  observer, step, and physical parameters come from the authenticated schedule.
- `validate` loads and audits one authenticated row, including construction and
  public-getter verification, but never calls `doStep()` and writes no file.

## Non-confirmatory smoke build

```text
cmake -S tools/orca_baseline -B build/orca-wrapper-smoke \
  -DRVO2_SOURCE_DIR=/absolute/path/to/pinned/RVO2 \
  -DORCA_INPUT_MANIFEST_SHA256=smoke-only-unbound \
  -DCMAKE_BUILD_TYPE=Release
cmake --build build/orca-wrapper-smoke --target dancers-orca-wrapper --parallel 4
ctest --test-dir build/orca-wrapper-smoke --output-on-failure
```

The build command is required before CTest. CTest supplies the exact target path
through `ORCA_WRAPPER_BINARY`; a missing explicit binary is a hard test failure.
The suite uses the committed `smoke_crossing.json` and
`smoke_goal_latch.json` bytes and never loads a publication input.

## Publication build and invocation

After the create-only input tree is committed and independently validated,
configure a fresh build with its 64-character lowercase manifest digest:

```text
cmake -S tools/orca_baseline -B /fresh/build/root \
  -DRVO2_SOURCE_DIR=/absolute/path/to/clean/pinned/RVO2-checkout \
  -DORCA_INPUT_MANIFEST_SHA256=<manifest-sha256> \
  -DORCA_WRAPPER_ENABLE_TESTS=OFF \
  -DCMAKE_BUILD_TYPE=Release
```

The schedule-addressed run interface is:

```text
dancers-orca-wrapper run \
  --input-root /absolute/path/to/dancers-orca-v1 \
  --attempt-sequence <orca-row> \
  --summary-out /new/attempt/summary.json \
  [--trace-out /new/attempt/trace.ndjson]
```

`--trace-out` is required only when that authenticated row requests positions.
Both outputs are create-exclusive. Attempt sequence and repetition stay in the
summary but are deliberately absent from the semantic trace header, allowing
the two registered repeatability traces for one scene/cadence to be compared
byte-for-byte.

Before any registered execution, exercise every ORCA row with the same final,
manifest-bound binary:

```text
dancers-orca-wrapper validate \
  --input-root /absolute/path/to/dancers-orca-v1 \
  --attempt-sequence <orca-row>
```

Successful validation prints one JSON object with
`status=accepted_without_simulation`. It initializes the pinned RVO2 object and
checks all exposed parameters, but it does not set goal-directed preferred
velocities, call `doStep()`, or create an output artifact.
