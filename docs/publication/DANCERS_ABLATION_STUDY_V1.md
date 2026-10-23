# DANCeRS cumulative optimization ablation v1

Status: completed and independently audited. The prospective design was
amended only by the two retained pre-measurement infrastructure repairs
described below.

## Question and scope

This study measures the order-dependent incremental contribution of the five
historical result-preserving optimizations to the paper's sparse discrete GBP
workload. It uses the exact historical source chain rather than new feature
toggles.

The study does **not** ablate the modern open, maze, or fractal planner. The raw
historical stages predate common headless obstacle-field and planning-only
logging fixes. Applying those fixes separately to six variants would create a
new derived source experiment. The existing held-out comparison remains the
planning evidence; this ablation isolates the original sparse compute story.

The cumulative results quantify marginal improvements in the recorded
historical order. They are not order-independent causal effects, Shapley
values, or a full factorial interaction analysis.

## Immutable cumulative stages

| ID | Commit | Tree | Newly enabled optimization |
|---|---|---|---|
| S0 | `986f47deec5b079663c7d7f6120bfc20f0467901` | `b564979ee493293a4208116ee7c1c05bd69307f4` | Source-pinned reference |
| S1 | `4c653c1fb2aa1317b6ead76f2fab5064f84c1e8b` | `9f1310ca94c7f9b9a76d098d57d813be21c9eb63` | Contiguous KD-tree positions and robot IDs |
| S2 | `aa58cf8d3a5cb9437843d04b61aa4f8c2f962c52` | `37e24e1ee446d84a225278b0696133604015e36c` | Exact unchanged-sensing topology reuse |
| S3 | `ba036208812b840617557abf7a3621838a76ef74` | `2a5bec3c58787de9e632531e5e9005d92f0107e8` | Reduced GBP copies/redundant solve and reused snapshots |
| S4 | `51d7ef6b6c10f60bbab60c035bd39ca7b7400b2a` | `eade13828cc54526851c1c4cb31af2f8792ba413` | Scalar R1 information operations and identity-transport bypass |
| S5 | `870898dafbadde0e8763e95a18a061d7f3e7f701` | `fc6efbe4f2e6c2e58b805a45e570932db74de9ae` | Single-owner movement of externally staged messages |

The docs-only commits `33bd29f8` and `712770db` occur inside this linear
history but add no runtime source. S3 also changed the UI ceiling and a graphics
path; display is disabled here, so neither participates in measured work.

All six commits contain the same `pixi.lock` bytes, SHA-256
`636ecf1cf31431ffc19f453fef39299c3243718477e321fcea8814225bfe68be`.

## Authenticated external build inputs

- `thirdparty/dist/manif-0.0.5-build-source.tar.gz`, SHA-256
  `3d3a13e93384348946c689ebd4e818bbb9f72cbcc3cdafecb7b531f7d5191715`.
- `thirdparty/dist/raylib-5.5-build-source.tar.gz`, SHA-256
  `9758adff979fe36aa846897ecfd14e6d88ea13df87e9862b770d36b854717709`.
- The local environment resolved by the shared lock: Clang 19.1.7, CMake
  4.4.0, and Ninja 1.13.2. Exact executable identities are recorded at build
  time.

Every stage is archived from Git into a separate source directory, configured
against the same extracted dependency bytes, built in Release mode, and reduced
to the `dancers` target. Builds occur before timing and outside the results
directory.

## Frozen workload

Configuration:
`config/ablation/sparse_consensus_seed1.yaml`, SHA-256
`c48107956401bf912d24a587d94a6cd256c18256c2edf82007c103b987e33d2e`.

- Scenario: sparse triangular grid.
- Communication radius: 6 m.
- Topology/decision seeds: 1 through 10.
- Robot counts: 500 and 1,000.
- Work: exactly 100 timesteps.
- GBP work: one external exchange and two internal sweeps per timestep.
- Display: disabled.
- Threads: `OMP_NUM_THREADS=1`, `OMP_WAIT_POLICY=passive`.
- Runs: serial; no simulator process overlaps another.

## Inventory and order

Each stage/count cell receives one untimed warm-up. Each stage/count/seed cell
then receives three nested timing repetitions:

- warm-ups: 6 stages x 2 counts = 12 processes;
- measured: 6 stages x 2 counts x 10 seeds x 3 repetitions = 360 processes;
- total: 372 simulator processes.

Each seed/repetition pair is one measured block. Block `b` rotates the ordered
stage list by `b mod 6`; across 30 blocks every stage occurs exactly five times
in every stage-order position. Robot count order alternates by block, so each
count is first in 15 blocks. A
two-second idle interval follows every complete measured block. No run is
dropped, replaced, winsorized, or classified as an outlier after observation.

## Measurements

For each process, record:

- monotonic wall time;
- child user and system CPU time;
- child maximum resident set size;
- exit status;
- exact stdout and stderr bytes and SHA-256;
- executable, configuration, commit, tree, order, block, and environment
  identities.

The runner also records macOS version, hardware identity when available, AC
power status, thermal-query output, free disk space, tool versions, and the
complete schedule before the first measured process.

Wall and CPU measurements cover the complete child-process lifetime, including
configuration loading and initialization (including the ten initial local
sweeps). They are end-to-end fixed-work process measurements, not isolated
solver-kernel timings. AC power is checked at the start of each 12-process
measured block. The host thermal query was unavailable throughout this run;
that absence is retained in all host receipts and prevents a thermal-state
claim.

On Darwin, `wait4.ru_maxrss` is recorded in bytes. The raw integer remains in
the ledger; analysis additionally reports MiB. It is a coarse process peak,
not an allocation-level memory profile.

## Gates

The study fails closed if:

- a commit/tree, lock, configuration, dependency, tool, source archive, or
  binary identity differs from its recorded value;
- a build or simulator process exits nonzero;
- any expected cell/run is missing or duplicated;
- a final log lacks the fixed 100-timestep/100-external-iteration result (each
  external iteration contains the configured two internal GBP sweeps);
- deterministic stdout differs across stages or repetitions for the same robot
  count and seed after removing no fields (exact-byte equality is required);
- the machine is not on AC power at measured-block start; or
- a non-finite/non-positive timing value appears.

The two counts are analyzed separately. A failure at either count is retained
and prevents a complete ablation claim.

## Analysis

Within each count/stage/seed cell, take the median of the three nested timing
repetitions. For every count and stage, report the geometric mean of those ten
seed medians for wall, user CPU, total CPU, and maximum-resident-set size, with
all raw values retained.

For every adjacent pair `S(i-1) -> Si`, and for `S0 -> S5`, calculate paired
within-seed ratios of the stage medians. Report the geometric-mean speedup and
a deterministic 95% percentile interval from 50,000 cluster-bootstrap
resamples of the ten complete seeds. The resampling seed is `20260824`; seeds,
not timing repetitions or process measurements, are the resampling unit.

No minimum effect threshold is imposed. Negative or negligible increments are
valid results. Cumulative `S0 -> S5` speedup is the primary performance
quantity; adjacent increments explain where it arose in this historical order.

Exact stdout equality establishes fidelity of the deterministic printed
summary for this historical sparse workload. It does not expose or prove
equality of hidden internal state; the separate modern-planner digest evidence
provides the stronger state/trajectory fidelity test.

## Completed result

All 372 scheduled processes completed without a retry or dropped observation.
At 500 robots, the cumulative `S0 -> S5` wall-time speedup is 8.4454x (95%
cluster-bootstrap interval 8.2915x to 8.6021x). At 1,000 robots it is 14.7229x
(14.3003x to 15.1753x). The corresponding total-CPU speedups are 8.6101x and
14.8734x.

The largest historical-order increment is contiguous KD-tree layout (`S0 ->
S1`), followed by scalar R1 specialization (`S3 -> S4`). Topology reuse,
GBP-inner-loop reuse, and single-owner external-message movement add smaller
positive increments. Peak RSS remains broadly flat (approximately 44 MiB at
500 robots and 55 MiB at 1,000), so this is not a memory-reduction result.

All stdout artifacts are byte-identical within each count/seed group across
stages, repetitions, and warm-ups. The raw ledger and analysis are in
`results/publication/dancers-ablation-v1-repair-2/`.

## Result namespace

- Build root: `build/publication/dancers-ablation-v1/` (ignored, replaceable).
- Retained infrastructure attempt 1:
  `results/publication/dancers-ablation-v1/` (create-exclusive and committed).
- Retained path-repair attempt 1:
  `results/publication/dancers-ablation-v1-repair-1/` (create-exclusive and
  committed).
- Repaired raw and analyzed result root:
  `results/publication/dancers-ablation-v1-repair-2/` (create-exclusive and
  committed).

The result root must contain the frozen schedule, build manifest, raw run
records, stdout/stderr artifacts, analysis JSON/CSV, and a plain-language
findings document.

## Retained infrastructure attempt 1

After this design was committed at `e859993be`, sequence 0 (the first untimed
S0/N=500 warm-up) aborted while opening the configuration. The legacy DArgs
string parser split the absolute path at the space in the repository directory
name, so fkyaml received no valid stream. No measured process started and
`runs.jsonl` remained empty.

The repair changes no source stage, workload byte, seed, count, repetition,
order, gate, metric, or analysis rule. The runner now passes the same
authenticated configuration through a relative path containing no whitespace
and verifies that it resolves to the frozen file before spawn. The failed root
is retained; the repaired execution uses the create-exclusive successor root
above.

Repair attempt 1 successfully opened the configuration and completed the first
untimed S0/N=500 warm-up. Its log reported 100 timesteps, 500 robots, and 100
external GBP iterations. The runner incorrectly required 200 logged
iterations by conflating the two internal sweeps per exchange with the
historical external-iteration counter. No measured process started and its
ledger also remained empty.

The second repair changes only that predicate: it requires the observed and
source-consistent 100 external iterations while retaining the frozen
`NUM_ITERS=1` configuration, whose simulator loop performs two internal GBP
sweeps per external exchange. An exact log-fixture regression test binds this
interpretation. Repair attempt 1 remains at
`results/publication/dancers-ablation-v1-repair-1/`; execution moves to the
create-exclusive repair-2 root above.
