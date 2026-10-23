# DANCeRS multi-map replication and resource study v1

Status: prospective checkpoint committed as `91113939f`; host-1 execution and
final v3 analysis complete. Independent execution on a physically distinct
second Apple-silicon Mac remains outstanding.

## Question and claim boundary

This study asks whether the source-pinned optimized DANCeRS implementation:

1. preserves the reference implementation's complete observed state and
   trajectory on newly generated planning inputs;
2. reduces fixed-work wall and total CPU time without a material peak-RSS
   increase; and
3. does so across multiple independently generated maps for every stochastic
   environment family.

This remains an implementation-optimization study. It does not test a new
planner, better paths, or improved collision avoidance. Identical unsafe
behaviour in both methods is retained as an underlying-controller limitation,
not removed from timing.

The current execution host is one machine. A cross-machine portability claim
requires the same frozen design to be rebuilt and run on a physically distinct
host. Copying the first host's executables is prohibited. Version 1 supports a
second Apple-silicon Mac; the protected build/runtime path is Darwin-specific,
so Linux is a later port rather than a nominal rerun.

## Source endpoints

| method | algorithm revision | applied comparison head | tree |
|---|---|---|---|
| reference | `986f47deec5b079663c7d7f6120bfc20f0467901` | `de0f60d1ad4b944c2b3264b8e3ccd348708b8b8a` | `cb93386e28f3824ea7ffc17605b441b92f13f87d` |
| optimized | `99364a41f1535a4bb0eddbe5a10030127ea77086` | `26c8c6f9b262a2114ac28203419cb03caf8b0d88` | `b6f713f47ea3aa57c824cf2c481f3647ac7c9408` |

Both endpoints receive the same comparison/correctness layer. Its registered
patch-email SHA-256 is
`2ab81c9a9621c89de5854bbd8496cb2124845733140a913855ca61677185a95e`.
The optimization-specific difference remains the registered twelve-file
surface. Every host builds observer-disabled timing and observer-enabled
fidelity products from both endpoints.

Frozen common inputs are the repository `pixi.lock` (SHA-256
`636ecf1cf31431ffc19f453fef39299c3243718477e321fcea8814225bfe68be`),
`manif-0.0.5` source archive (SHA-256
`3d3a13e93384348946c689ebd4e818bbb9f72cbcc3cdafecb7b531f7d5191715`),
and Raylib 5.5 source archive (SHA-256
`9758adff979fe36aa846897ecfd14e6d88ea13df87e9862b770d36b854717709`).

## Prospective maps and missions

The generator is
`scripts/replication/prepare_multimap_inputs.py`. It is an input compiler: it
does not launch a simulator and cannot read a result directory. Publication is
create-exclusive and every output byte is listed in a canonical SHA-256
manifest.

| family | distinct maps | missions per map | robot counts | scenes |
|---|---:|---:|---|---:|
| open | 1 canonical | 20 | 8 | 20 |
| perfect maze | 10 | 2 | 2, 4, 6 | 60 |
| fractal low | 10 | 2 | 2, 4 | 40 |
| fractal medium | 10 | 2 | 2, 4 | 40 |
| fractal high | 10 | 2 | 2, 4 | 40 |
| **total** | **41** | **100 banks** | **10 cells** | **200** |

Open is deliberately one map. Its generator is seed-insensitive; treating
duplicates as independent maps would be pseudoreplication. Open therefore
supports fixed-map mission inference only.

The ten map seeds, selected before planner execution, are:

```text
1124288754611687508  5143576881131483611   364023683178661252
5756175215563340043  7628420574272008056  4761703837298867603
4492167233608332407  6254029921437523734  7378728135909974893
1424198907603555106
```

Maze uses those ten seeds. Low, medium, and high fractal maps use matched seed
triplets so persistence is the only generator parameter changed within a
triplet. Before any planner run, the input gate requires forty distinct
stochastic PNG hashes, forty-one distinct effective 1000 px raster hashes, one
4-connected free-space component per map, and strictly increasing source
boundary density within every matched low/medium/high triplet.

Mission labels are 31 through 50. Each stochastic map receives the consecutive
pair assigned by its index; open receives all twenty. The mission root binds
family, map ID, map SHA-256, and mission label, but not robot count. Counts are
exact nested prefixes of one maximum-N bank.

The existing headless geometry is retained: straight routes through the
four-pixel clearance mask, endpoint obstacle clearance at least 1.25 m,
endpoint pair-centre separation at least 2.25 m, and required goal travel in
[8, 12] m. Static inter-route distance remains a diagnostic rather than an
outcome-selection filter. A generated input is never replaced based on either
planner's result.

Every scene uses 100 controller timesteps of 0.1 s, 2 m/s maximum speed,
planning horizon 1.5 s, two GBP iterations per timestep, communication radius
20 m, display disabled, and one OpenMP thread. The runtime RNG field cycles
1--10, is documented as inactive for file scenarios, and is not an independent
sampling axis.

## Execution inventory

For every scene:

- one observer-enabled digest pair (reference and optimized);
- one observer-enabled positions pair;
- three observer-disabled timing repetitions per method.

Digest repetition is one because equality is an exact deterministic gate, not
a noisy estimate. Timing retains three repetitions because it is noisy. The
positions trace independently supports continuous swept-disc geometry.

Before timing, each observer-disabled method receives one untimed warm-up on
each of the 41 maps. Thus:

- fidelity: 200 scenes x 2 trace details x 2 methods = 800 processes;
- timing: 200 scenes x 3 repetitions x 2 methods = 1,200 processes;
- timing-product warm-ups: 41 maps x 2 methods = 82 processes;
- total: 2,082 serial simulator processes per host.

Within every pair, method order is a frozen SHA-256-derived assignment. No
simulator process overlaps another. The runner requires AC power at the start
and at each map block, records host/OS/hardware/thermal probe output, and sets
`OMP_NUM_THREADS=1`, `OMP_DYNAMIC=FALSE`, and `OMP_WAIT_POLICY=passive`.

The raw attempt directory is retained but is too large for normal Git storage.
The committed evidence consists of the frozen input/build/schedule identities,
ledger, compact analysis, tables, and findings. Raw traces remain checksummed
by the ledger and must accompany an archival artifact by external storage.

## Measurements and gates

Each child process records monotonic end-to-end wall time, `wait4` user and
system CPU, and `wait4.ru_maxrss`. Total CPU is exactly user plus system. On
Darwin RSS raw units are bytes; on Linux they are KiB. The analyzer rejects an
unsupported platform or any inconsistent raw/unit/MiB conversion.

Timing uses observer-disabled binaries and must create no trace or summary.
Fidelity uses observer-enabled binaries and is excluded from performance
estimation. Wall and CPU cover the entire fixed-work child lifetime, including
initialization and configuration loading; they are not solver-kernel timings.

The study fails closed if any expected process, file, metric, pair, repetition,
or hash is missing/duplicated; a process exits nonzero or times out; fixed work
or robot count differs; a timing observer artifact appears; or any reference
and optimized semantic trace or registered summary field differs. There is no
retry, outlier deletion, winsorization, task-validity filtering, or map/mission
replacement.

Positions are reconstructed as 100 pre/post linear sweeps. Closed robot discs
are checked continuously against each other and the actual nearest-neighbour
effective raster. Contact and tangency are unsafe. Task validity is reported
separately and never used to select resource observations.

## Statistical analysis

For each scene, method, and endpoint, collapse the three timing repetitions to
their median. Compute paired scene ratios:

- wall speedup = reference / optimized;
- total-CPU speedup = reference / optimized;
- RSS ratio = optimized / reference.

For a stochastic family/count cell, equal-weight the two mission log-ratios
within each map and the ten maps within the cell. Report the geometric-mean
ratio and a deterministic two-stage percentile bootstrap interval from
100,000 draws: resample maps, then resample missions within every selected
map. For open, resample its twenty missions and label the result fixed-map
inference. Timing repetitions are never treated as independent observations.

The preregistered wall and CPU improvement gates for each tested cell are a
point speedup greater than 1.05 and a lower 95% bound greater than 1.00. The
secondary memory gate is an upper 95% bound on optimized/reference RSS no
greater than 1.05. Passing it permits “no measured increase above 5%,” not a
memory-reduction claim. Unfavourable cells are valid results and remain shown.

Each physical machine is analyzed separately. Absolute times are not pooled.
A hardware-portability statement requires the wall and CPU gates on both
hosts; two hosts remain fixed replication strata, not a sampled hardware
population.

## Commands after this prospective checkpoint is committed

```bash
python3 scripts/replication/prepare_multimap_inputs.py prepare \
  --output-root comparison/publication/dancers-multimap-v1

python3 scripts/replication/run_multimap_study.py build \
  --input-root comparison/publication/dancers-multimap-v1 \
  --build-root build/publication/dancers-multimap-v1-host-1

python3 scripts/replication/run_multimap_study.py run \
  --input-root comparison/publication/dancers-multimap-v1 \
  --build-root build/publication/dancers-multimap-v1-host-1 \
  --output-root results/publication/dancers-multimap-v1-host-1

python3 scripts/replication/analyze_multimap_study_v3.py \
  --input-root comparison/publication/dancers-multimap-v1 \
  --output-root results/publication/dancers-multimap-v1-host-1
```

## Host-1 completed result

Host 1 completed the sealed schedule without retry: 82 warm-ups and 2,000
measured processes, covering 200 scenes on 41 maps. All 200 digest pairs and
all 200 position pairs were semantically identical between the reference and
optimized methods. The independent geometry analysis classified 199 of 200
scenes as task-valid.

The retained invalid scene is `fractal_medium-map-05-m40-n4`. Reference and
optimized runs reproduced the same pair contact; the independent minimum pair
clearance was -0.7599741662 m. This scene and all its timing observations remain
in the resource analysis.

All ten preregistered cells passed the wall-time and total-CPU improvement
gates. Wall-time point speedups ranged from 1.2128x to 1.3398x, and CPU point
speedups ranged from 1.2397x to 1.3560x. All ten secondary memory gates also
passed: optimized/reference peak-RSS point ratios ranged from 1.0003 to 1.0020,
and every upper 95% bound was below 1.003, well inside the registered 1.05
limit. This supports fixed-work resource efficiency and stochastic-map
generalization on host 1; it does not yet support cross-machine portability or
population-level generalization to arbitrary map generators.

The compact result is retained in
`results/publication/dancers-multimap-v1-host-1/`. Its principal identities are:

| artifact | SHA-256 |
|---|---|
| input manifest | `719875d1d209de39c51a63f7fc974e10baa510299f826bf5db6912bc3dc6bdce` |
| build manifest | `3c9c4305dae5e5154963ec76bb52e0bb826d6fb5b44a89503235057ad449b3cd` |
| schedule | `cfeef02496c0fc6d7bf78f69e8fe94960d25996b0c677c93b3ce2ba59002a4d4` |
| ledger | `458f01fd71f7dc420ffab3261a3ac03ae8641c60bcf636990409222283ee92d4` |
| analysis JSON | `6c26d278c15abde2cd604fc6ac59bc4af8bd5bbecdbdc87bc3438e57cb05d7fd` |
| analysis CSV | `5465db9f13ecd04d7b6b31c45ac5f723e29729667f21e503cca1830d560aa334` |
| findings | `a8f84fcc3041bbe14d4384ba1c7fec4d8cc2e3d1a76910056c2ee309b654d2ba` |
| final v3 analyzer | `bc3608d378f162b9afb3544d0d768f4698c21f139ba194dbe12d34908bc656a5` |

## Retained analysis infrastructure attempt 1

The sealed host-1 run completed all 2,082 processes. The first analysis attempt
then stopped before completing the first scene's geometry analysis. Its new
frame predicate incorrectly required `pre_update(t)` and `post_update(t)` to
carry the same tick number. The registered observer emits `pre_update(t)` then
`post_update(t+1)`; the established frozen headless analysis core already
pairs those adjacent frames and assigns a separate sweep index.

No resource aggregate, interval, gate, or complete-scene geometry result was
written. The raw evidence and execution runner remain byte-identical. The v2
wrapper changes only this tick predicate and delegates all other
authentication, semantic comparison, geometry, resource, and bootstrap work
to the sealed v1 runner module. The repaired analysis command is:

```bash
python3 scripts/replication/analyze_multimap_study_v2.py \
  --input-root comparison/publication/dancers-multimap-v1 \
  --output-root results/publication/dancers-multimap-v1-host-1
```

Attempt 2 then stopped before reading the session because the build manifest
bound this document's original bytes while the live copy now included the
attempt-1 audit annotation. The analyzer now authenticates the exact original
blob from commit `91113939f`; the current copy is allowed to append retained
infrastructure history. No evidence, metric, gate, or exclusion is relaxed.

Attempt 3 completed aggregation but copied the geometry helper's deliberately
locked `task_valid=false` field, yielding an internally inconsistent 0/200
classification even though 199 detailed rows were contact-free with no
geometry failure. The helper is intentionally unable to authorize science;
the established analyzer derives task validity from exact summary counters and
independent geometry. Version 3 restores that derivation. Resource values,
bootstrap draws, intervals, gates, and scene retention are unchanged.

A second-host transfer capsule is created only after this checkpoint commit:

```bash
python3 scripts/replication/run_multimap_study.py capsule \
  --destination results/publication/dancers-multimap-v1-second-host-capsule
```

The capsule contains the complete Git object graph and checkout instructions,
not reusable first-host executables. The second host must realize the locked
Pixi environment, rebuild all four products, and use a distinct output root.
