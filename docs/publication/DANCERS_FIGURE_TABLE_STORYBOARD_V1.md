# DANCeRS paper figure-and-table storyboard v1

Status: evidence-mapped storyboard; Figures 1–5 masters complete; main artwork
complete, with tables and artifact integration in progress.

This document governs the visual story for the proposed robotics-systems paper.
It maps each main claim to an authenticated source and prevents unlike studies
from being merged into one headline result.

## Figure contract

### Scientific claim

Careful changes to data layout, reuse, scalar algebra, and message ownership
reduce the computational cost of DANCeRS while preserving its registered GBP
schedule and observable behavior, including inherited failure cases.

### Audience and article

- Audience: robotics, distributed inference, and systems-performance reviewers.
- Article type: archival reproducible robotics-systems/performance paper.
- Default main-figure width: 183 mm, double column.
- Default table width: 183 mm; use 89 mm only for compact provenance tables.
- Final venue and page budget: **[AUTHOR TO CONFIRM]**.

### Reading path

1. Figure 1 explains what changed and what did not.
2. Figure 2 shows how the five changes accumulate on the historical sparse
   workload.
3. Figure 3 tests exact preservation and fixed-work efficiency prospectively
   across map families.
4. Figure 4 shows that preserved behavior includes both normal trajectories
   and inherited contacts.
5. Figure 5 places the implementation result beside ORCA without implying an
   equal-work runtime comparison or universal planner ranking.

### Visual language

| Meaning | Color | Shape or line style |
|---|---|---|
| Reference DANCeRS | neutral grey | open circle; dashed line |
| Optimized DANCeRS | blue | filled square; solid line |
| Native ORCA | orange | filled triangle; dash-dot line |
| Exact equality | black | paired link or equals mark |
| Registered interval | same series color | thin whisker with end caps |
| Task-invalid/contact case | vermilion | cross and hatched cell |
| Evidence pending | pale grey | dotted outline and `pending` label |

Color must never be the only carrier of meaning. All ratio plots include a
directly labelled neutral line at 1.0. Wall/CPU ratios are
reference/optimized, while RSS is optimized/reference; those directions must
appear inside the panels, not only in captions. Measured, simulated, and
conceptual content must remain visibly distinct.

## Recommended main-paper architecture

### Figure 1 — Same planner, leaner implementation

**One job:** establish that this is an implementation optimization of DANCeRS,
not a new motion planner.

**Claim:** five implementation changes remove repeated allocation, lookup,
solve, copying, and ownership work while leaving the GBP exchange schedule,
internal sweep count, update order, convergence settings, and paper metrics
unchanged.

**Format:** 183 mm × approximately 90 mm, vector schematic, three panels.

| Panel | Content | Required visual treatment | Evidence source |
|---|---|---|---|
| a | Existing DANCeRS loop with five optimized implementation surfaces | Dominant mechanism-led loop; solid physical motion path, dashed message flow, and matched numbered hotspots for contiguous KD layout, exact sensing-state reuse, reduced copying/redundant solves, scalar R1, and single-owner external movement | `docs/performance/SCALING_RESULTS.md`, Optimizations and fidelity sections |
| b | Preserved invariants | Aligned reference/optimized traces with an equality bracket; schedule, sweeps, ordering, convergence, and metrics listed without performance numbers | `docs/publication/DANCERS_OPTIMIZATION_PAPER_PLAN.md`, Supported claims |
| c | Evaluation gates | Compact pass/fail path: exact fidelity first, then wall/CPU/RSS; behavioral context remains visually separate and conditional | `docs/publication/DANCERS_ABLATION_STUDY_V1.md`; `docs/publication/DANCERS_MULTIMAP_REPLICATION_STUDY_V1.md`; `docs/publication/task4_external_baseline/EXPERIMENT_PROTOCOL_V1.md` plus Amendments 1–9 |

**Production package:** complete and verified at
`figures/publication/fig01_overview/`. The conceptual master contains no
performance magnitude, maps all five cumulative changes onto existing sensing
or GBP implementation surfaces, and makes the fidelity-first evidence gate
explicit.

**Caption draft:**

> **Result-preserving optimization of the DANCeRS implementation.** (a) The
> motion-planning and synchronous GBP schedule are retained, while five
> numbered hotspots locate cumulative changes that reduce data-access,
> repeated-computation, dynamic-scalar, message-copying, and ownership
> overhead. (b) The registered schedule, internal sweeps, update order,
> convergence settings, and reported metrics remain fixed. (c) Each experiment
> admits performance evidence only after its
> exact-fidelity gate. The work changes implementation cost, not the planner's
> decision rule.

**Do not draw:** a new algorithm block, altered GBP edges, a safer-path badge,
or a causal arrow from optimization to planning quality.

### Figure 2 — Cumulative sparse-workload ablation

**One job:** show where the historical sparse-workload acceleration appears in
the recorded cumulative order.

**Claim:** every adjacent historical stage has a positive registered wall-time
increment at N=500 and N=1,000; cumulative wall speedup reaches 8.4454× and
14.7229×, respectively, while peak RSS remains broadly unchanged.

**Format:** 183 mm × approximately 105 mm, measured plots, three panels.

| Panel | Content | Encoding | Evidence source |
|---|---|---|---|
| a | Stage wall time at N=500 and N=1,000 | Two directly labelled lines across S0–S5; logarithmic y-axis if needed | `results/publication/dancers-ablation-v1-repair-2/analysis.json` → `stage_summaries`; `analysis.csv` |
| b | Adjacent and cumulative wall speedup | Forest plot; adjacent S0→S1 through S4→S5 plus S0→S5; separate rows for N=500 and N=1,000 | same `analysis.json` → `comparisons`; `FINDINGS.md`, Paired speedups |
| c | Peak RSS by cumulative stage | Dot plot in MiB, not a ratio-to-zero plot | same `analysis.json` → `stage_summaries`; `FINDINGS.md`, Stage timing |

Stage labels must be printed in full once: S0 reference, S1 contiguous KD
layout, S2 topology reuse, S3 GBP inner-loop reuse, S4 scalar R1, S5
single-owner external movement.

**Caption draft:**

> **Historical-order cumulative ablation on the source-pinned sparse
> workload.** (a) Complete-process wall time falls at every cumulative stage
> for 500 and 1,000 robots. (b) All adjacent reference/later-stage wall-time
> ratios exceed one in the recorded order; the cumulative S0→S5 ratios are
> 8.4454 (95% interval 8.2915–8.6021) and 14.7229
> (14.3003–15.1753). (c) Peak RSS changes little across stages. The experiment
> is order-dependent and does not estimate isolated or order-independent causal
> effects; it also does not measure the modern obstacle-planning runtime.

**Do not draw:** a Shapley-style contribution chart, independent bars whose
heights imply isolated effects, or any connection to the 1.21–1.36× obstacle
map results.

### Figure 3 — Prospective multi-map preservation and efficiency

**One job:** provide the main prospective evidence that exact behavior is
preserved while fixed-work wall and CPU cost fall across registered map
families.

**Claim:** all 200 digest pairs and all 200 position pairs are exactly equal on
host 1; all ten family/count resource cells improve wall and total CPU, and
all RSS upper bounds satisfy the preregistered 1.05 ceiling. One contact is
reproduced identically and retained.

**Format:** 183 mm × approximately 115 mm, measured plots, four panels.

| Panel | Content | Encoding | Evidence source |
|---|---|---|---|
| a | Study coverage | Five family rows, robot-count columns, 41-map/200-scene totals; identify fixed open map versus 40 stochastic maps | `results/publication/dancers-multimap-v1-host-1/input_manifest.json`; `analysis.json` → `inventory` |
| b | Exact fidelity | Two paired totals: 200/200 digest and 200/200 positions; an inset marks 199/200 task-valid without treating the invalid scene as an implementation mismatch | `analysis.json` → `fidelity`, `resources`; `FINDINGS.md` |
| c | Wall and total-CPU speedup across ten cells | Aligned two-column forest plot; common family/count rows; x=1 neutral line | `analysis.json` → `resources`; `analysis.csv`; `FINDINGS.md`, Resource results |
| d | Optimized/reference peak RSS | Forest plot with x=1 and registered x=1.05 ceiling; label the small increases honestly | same sources as panel c |

Cell order: open N=8; perfect maze N=2,4,6; fractal low N=2,4;
fractal medium N=2,4; fractal high N=2,4. Stochastic-family intervals use
map-then-mission resampling. The open-map interval resamples missions only and
must not look like a population-of-maps estimate.

**Caption draft:**

> **Prospective exact-fidelity and fixed-work resource results across map
> families on host 1.** (a) The registered matrix contains one fixed open map
> and 40 independently generated maze or controlled-fractal maps, giving 200
> scenes. (b) Reference and optimized DANCeRS match exactly in all 200 digest
> and all 200 position pairs. Swept geometry classifies 199 scenes as
> task-valid; the one contact is reproduced identically and is retained in all
> resource estimates. (c) Reference/optimized wall-time ratios span
> 1.2128–1.3398 and total-CPU ratios span 1.2397–1.3560; every registered lower
> interval bound is above one. (d) Optimized/reference peak-RSS point ratios
> span 1.0003–1.0020 and every upper bound is below 1.003, satisfying the 1.05
> ceiling without supporting a memory-reduction claim. Independent second-host
> replication remains pending.

**Required label:** `Host 1; second host pending` must remain visible until an
authenticated second-machine result exists.

### Figure 4 — Preserved trajectories include inherited boundaries

**One job:** make exact behavioral preservation tangible and show that it does
not mean universal collision freedom.

**Claim:** synchronized reference and optimized trajectories coincide across
the five held-out environment families, and the two held-out pair contacts are
reproduced by both implementations rather than introduced by optimization.

**Format:** 183 mm × approximately 105 mm, simulation trajectories, six
panels.

| Panel | Content | Source asset or data |
|---|---|---|
| a | Open, N=8, seed 11 | `results/comparison/demo/held-out-safe-grid-v1/open-n8-seed11.html` |
| b | Perfect maze, N=6, seed 11 | `results/comparison/demo/held-out-safe-grid-v1/perfect_maze-n6-seed11.html` |
| c | Fractal low, N=2, seed 11 | `results/comparison/demo/held-out-safe-grid-v1/fractal_low-n2-seed11.html` |
| d | Fractal medium, N=2, seed 11 | `results/comparison/demo/held-out-safe-grid-v1/fractal_medium-n2-seed11.html` |
| e | Fractal high, N=4, seed 11 | `results/comparison/demo/held-out-safe-grid-v1/fractal_high-n4-seed11.html` |
| f | One identical-contact case, enlarged at the contact frame | `results/simulator-demo/v1/headless-scientific/epoch-5-validation/held-out-validation-raster-repair-session-001-analysis.json` → `analysis.positions_replay` and `analysis.task_validity`; raw registered reference/optimized traces under `.../attempts/calibration/positions_replay/fractal_low/n-4/seed-17/rep-1/` |

For panels a–e, use the same arena scale where practical, show robot footprint
and direction, and overlay reference and optimized paths with both color and
line-style differences. If the paths coincide perfectly, offset only the
legend samples—not the data. Panel f uses a vermilion contact marker and
states `identical in reference and optimized`.

**Production package:** complete and verified at
`figures/publication/fig04_trajectories/`. The five full arenas use the same
100 m scale. Panel f is a lossless 32 × 32 source-pixel crop spanning 12.5 m;
its plotted pair first touches at 1.163633330933 s and reaches a continuous
minimum clearance of −0.276731315370 m at 1.466071557332 s in both builds.

**Caption draft:**

> **Synchronized held-out trajectories and an inherited controller
> boundary.** (a–e) Representative registered scenes span open, perfect-maze,
> and three controlled-fractal environment families. Reference and optimized
> trajectories coincide under the exact registered comparison. Across the
> full held-out study, all 600 digest pairs are semantically equal and 198 of
> 200 position scenes are task-valid. (f) The two task-invalid scenes contain
> reference/optimized pair contacts at the same scene and trajectory state;
> they are retained as evidence that optimization preserves observed failures,
> not as evidence of collision freedom.

**Do not draw:** success ticks on the two invalid cells, interpolated paths not
present in the replay data, or a contact-free claim.

### Figure 5 — Restricted external context: efficiency preserved, behavior traded

**One job:** show that the added ORCA workload independently supports the
DANCeRS implementation claim while the native planner comparison yields
trade-offs rather than dominance.

**Claim:** all 23 DANCeRS scenes preserve exact reference behavior and the
optimized implementation improves wall and CPU cost; on 20 fixed open
missions, DANCeRS and ORCA complete equally but differ in path, timing, and
smoothness measures; circle outcomes are descriptive and cadence-sensitive.

**Format:** 183 mm × approximately 125 mm, measured plots, five panels.

| Panel | Content | Encoding | Evidence source |
|---|---|---|---|
| a | Protocol boundary | Two lanes: reference↔optimized DANCeRS resource comparison; native DANCeRS↔native ORCA behavior comparison. Put a barred connector between runtime lanes | `docs/publication/task4_external_baseline/SESSION_002_MANUSCRIPT_RESULTS_V1.md`, Suggested placement and constraints |
| b | Added-workload DANCeRS resource ratios | Three-row forest plot: wall, CPU, RSS; show ratio direction per row | outcome packet `outcome.json` → `resource_open_fixed_world`; full `analysis.json` → `resources` |
| c | Open-world behavior differences | Twelve DANCeRS-minus-ORCA rows, grouped as completion, time/path, contact/clearance, and smoothness; zero reference line; use facet-specific units rather than a shared numeric scale | outcome packet `outcome.json` → `behavior_open_fixed_world`; full `analysis.json` → `behavior` |
| d | Circle completion and contacts | Three robot-count columns; completion fraction above, contact episodes below; do not pool | full `analysis.json` → `behavior` circle rows; manuscript module Table 3 |
| e | ORCA cadence sensitivity | Main cadence versus 0.05 s sensitivity cadence for completion, contacts, and exposure at N=8,20,50 | full `analysis.json` → `behavior`; manuscript module, Circle stress cases and cadence sensitivity |

Panel c must retain all twelve registered metrics. Acceleration RMS, jerk RMS,
and velocity total variation remain separate; they must not be collapsed into
an invented smoothness score. Because units differ, use aligned small multiples
with individual axes or a sign-and-interval table-plot—not standardized effect
sizes unless prospectively justified.

**Production package:** complete and verified at
`figures/publication/fig05_orca/`. The package authenticates the frozen outcome
packet, audits the full set of 12 registered open-world metrics and ratio
directions, and keeps the equal-work DANCeRS implementation comparison separate
from the native-planner behavioral context.

**Caption draft:**

> **Restricted external context under native motion models.** (a) The
> reference/optimized DANCeRS comparison estimates implementation efficiency;
> the DANCeRS–ORCA comparison reports fixed-input behavior and does not define
> an equal-work runtime ratio. (b) Across 20 open missions, reference/optimized
> DANCeRS wall and total-CPU ratios are 1.3200 (95% interval
> 1.3125–1.3279) and 1.3457 (1.3410–1.3504); optimized/reference RSS is 1.0021
> (1.0015–1.0026). Exact fidelity passes in all 23 DANCeRS scenes. (c) Both
> native methods complete every open-mission agent. DANCeRS has longer paths
> and later completion, lower acceleration and jerk RMS, and higher velocity
> total variation; contact and clearance intervals do not establish a general
> safety advantage. (d) In three descriptive circle cases, DANCeRS completion
> fractions are 1.00, 1.00, and 0.74 and main-cadence ORCA fractions are zero;
> contacts occur in both methods. (e) ORCA contact outcomes change strongly
> under the registered cadence sensitivity. Results are conditional on this
> host, fixed inputs, parameter mapping, and native models, and do not support
> a universal planner ranking.

**Integrity note:** add a small footer: `Session 002: 363/363 completed;
Session 001 retained as failed infrastructure execution and excluded.`

## Main tables

### Table 1 — Optimization and invariance ledger

**Purpose:** connect each implementation change to its computational target
and preserved semantic boundary.

Columns: stage; implementation change; removed work; preserved operation;
source checkpoint/build identity; exact-fidelity evidence. This table supports
Figure 1 and should not repeat timing values from Figure 2.

Sources:

- `docs/performance/SCALING_RESULTS.md`;
- `results/publication/dancers-ablation-v1-repair-2/build_manifest.json`;
- `results/publication/dancers-ablation-v1-repair-2/analysis.json`; and
- `docs/publication/DANCERS_ABLATION_STUDY_V1.md`.

### Table 2 — Fidelity coverage and retained failures

**Purpose:** show what “result preserving” means across all authenticated study
streams.

| Study | Exact comparison | Coverage | Retained boundary |
|---|---|---:|---|
| Historical sparse ablation | deterministic printed summary/stdout | all registered stage/run comparisons | equality does not cover unobserved internal state |
| Held-out validation | semantic trace, summaries, paper metrics; position replay | 600 digest pairs; 200 position scenes | 198/200 task-valid; two identical contacts |
| Prospective multi-map host 1 | digest and positions | 200/200 each | one of 200 scenes has an identical contact |
| Restricted ORCA workload, DANCeRS only | digest and positions | all 23 scenes | ORCA repeatability is separate from DANCeRS fidelity |

Primary sources are the four analysis bundles listed in the provenance index
below. Do not sum these rows into a single denominator because their units and
protocols differ.

### Table 3 — Reproducibility and machine boundary

**Purpose:** distinguish completed host-1 evidence from the outstanding
portability claim.

Columns: host; OS/toolchain; source/build identity; processes; fidelity;
wall-gate cells; CPU-gate cells; RSS-gate cells; status. Host 1 is complete at
2,082/2,082 processes and 10/10 for each resource gate. Add host 2 only after
the authenticated rebuild-only capsule returns.

Source: `results/publication/dancers-multimap-v1-host-1/build_manifest.json`,
`host_initial.json`, `host_final.json`, `analysis.json`, and `FINDINGS.md`.

**[AUTHOR TO CONFIRM]:** if host 2 is unavailable at submission, title this
table `Host-1 reproducibility record` and state hardware portability as a
limitation; do not leave an empty host-2 row in the final paper.

## Extended Data and Supplementary items

### Extended Data Figure 1 — Historical sparse scaling and fixed-budget boundary

The existing 500-robot 5.52 s→0.79 s checkpoint and historical 2,900-robot
fixed-budget boundary are useful context but should not enter the main paper
until a prospective final-source rerun exists.

- Source: `docs/performance/SCALING_BASELINE.md` and
  `docs/performance/SCALING_RESULTS.md`.
- Plot: reference and optimized wall/CPU against robot count; sparse bounded-
  degree regime only.
- Required label: `historical checkpoint; prospective updated rerun pending`.
- **[AUTHOR TO CONFIRM]:** promote to a main figure only after the updated
  registered scaling bundle is complete.

### Extended Data Figure 2 — Held-out ten-cell validation matrix

Plot all ten held-out family/count rows. Display intervals for the eight
fully task-valid cells and hatched `task-invalid; timing not admitted` entries
for fractal-low N=4 and fractal-medium N=4. Never omit the two invalid cells.

Source: `results/simulator-demo/v1/headless-scientific/epoch-5-validation/held-out-validation-raster-repair-session-001-analysis.json`
→ `analysis.timing_validation`.

### Supplementary Table 1 — Full multi-map resource results

All ten wall, CPU, and RSS rows with interval method, map count, mission count,
and task-valid count. Source:
`results/publication/dancers-multimap-v1-host-1/analysis.csv` and
`analysis.json`.

### Supplementary Table 2 — Full open-world ORCA differences

All twelve registered DANCeRS-minus-ORCA metrics, their units, point
differences, intervals, and fixed-world interpretation. Source:
`comparison/publication/dancers-orca-v1-session-002-outcome-packet/outcome.json`
and `docs/publication/task4_external_baseline/SESSION_002_MANUSCRIPT_RESULTS_V1.md`,
Table 2.

### Supplementary Table 3 — Circle and cadence sensitivity

Retain completion, restricted time, path, clearance, contacts, exposure, and
all three smoothness measures at N=8,20,50 for both registered ORCA cadences.
Do not pool rows or calculate an overall rank.

Source: `comparison/publication/dancers-orca-v1-session-002-outcome-packet/analysis.json`
→ `behavior`.

### Supplementary Table 4 — Execution integrity ledger

Report planned/completed/failed/retried processes, source hashes, analysis
hashes, and exclusion reasons for each study. Session 001 must remain visible
as a failed infrastructure execution and contribute no estimate.

Sources:

- `docs/publication/task4_external_baseline/SESSION_001_EXECUTION_AUDIT.md`;
- `comparison/publication/dancers-orca-v1-session-002-outcome-packet/manifest.json`;
- `comparison/publication/dancers-orca-v1-session-002-outcome-packet/manifest.sha256`;
- the ablation and multi-map build manifests and schedules.

## Claim-to-evidence map

| ID | Manuscript-level claim | Primary visual | Exact authoritative evidence | Boundary |
|---|---|---|---|---|
| C1 | The work optimizes the DANCeRS implementation rather than introducing a new planner. | Fig. 1 | `docs/performance/SCALING_RESULTS.md`; source/build manifests | No safer/better-path claim |
| C2 | Five cumulative implementation changes preserve the registered schedule and summaries. | Fig. 1; Table 1 | ablation `analysis.json`, `build_manifest.json`, `runs.jsonl` | Historical chain; printed equality does not expose every internal state |
| C3 | Every adjacent historical stage improves wall time in the recorded order. | Fig. 2 | ablation `analysis.json` → `comparisons`; `FINDINGS.md` | Not isolated or order-independent causality |
| C4 | Historical cumulative sparse speedup is 8.4454× at N=500 and 14.7229× at N=1,000. | Fig. 2 | same ablation sources | Not obstacle-planning speedup |
| C5 | Held-out digest fidelity passes 600/600 comparisons. | Fig. 4; Table 2 | held-out analysis → `analysis.fidelity` | Authenticated held-out suite only |
| C6 | Held-out position evaluation has 198/200 task-valid scenes and two identical reference/optimized contacts. | Fig. 4; ED Fig. 2 | held-out analysis → `analysis.positions_replay`, `analysis.task_validity`, `analysis.timing_validation` | No universal collision freedom |
| C7 | Eight admitted held-out cells show 1.2833×–1.3547× wall speedup. | ED Fig. 2 | held-out analysis → `analysis.timing_validation.cell_rows` | Two invalid cells retained and have no admitted interval |
| C8 | Prospective host-1 multi-map fidelity passes 200/200 digest and 200/200 position pairs. | Fig. 3; Table 2 | multi-map `analysis.json` → `fidelity`; `FINDINGS.md` | Host 1 only |
| C9 | All ten prospective cells improve wall and CPU; RSS stays below its registered ceiling. | Fig. 3; Supp. Table 1 | multi-map `analysis.json` → `resources`; `analysis.csv` | RSS is not reduced; second host pending |
| C10 | One multi-map contact is reproduced identically and retained. | Fig. 3b or Fig. 4f | multi-map `analysis.json`; `FINDINGS.md` | Inherited boundary, not regression |
| C11 | On the external workload, all 23 DANCeRS fidelity scenes and all 26 ORCA repeatability cohorts pass. | Fig. 5; Table 2 | outcome-packet `analysis.json` → `dancers_fidelity`, `orca_repeatability`; `outcome.json` → `confirmatory_gates` | Repeatability is not cross-method equality |
| C12 | Open-mission optimized DANCeRS wall and CPU ratios are 1.3200 and 1.3457; RSS is 1.0021. | Fig. 5b | outcome-packet `outcome.json` → `resource_open_fixed_world` | Reference versus optimized DANCeRS only |
| C13 | DANCeRS and ORCA both complete all agents on 20 fixed open missions, with trade-offs across time, path, contact, and smoothness. | Fig. 5c; Supp. Table 2 | outcome-packet `outcome.json` → `behavior_open_fixed_world` | Fixed inputs and native models; no universal ranking |
| C14 | Circle outcomes are descriptive and ORCA contacts are cadence-sensitive. | Fig. 5d–e; Supp. Table 3 | outcome-packet `analysis.json` → `behavior`; manuscript module Table 3 | No pooled superiority score |
| C15 | Session 002 completed 363/363 processes while session 001 remains excluded as failed infrastructure. | Fig. 5 footer; Supp. Table 4 | outcome-packet `analysis.json` → `integrity`; manuscript module Integrity subsection; session-001 audit | Never pool or hide session 001 |
| C16 | The historical 2,900-robot fixed-budget boundary is provisional for the archival paper. | ED Fig. 1 | scaling baseline/results documents | Requires prospective final-source rerun |

## Authoritative provenance index

1. **Historical cumulative ablation**
   `results/publication/dancers-ablation-v1-repair-2/analysis.json`,
   `analysis.csv`, `FINDINGS.md`, `build_manifest.json`, `runs.jsonl`, and
   `schedule.json`.
2. **Held-out validation**
   `results/simulator-demo/v1/headless-scientific/epoch-5-validation/held-out-validation-raster-repair-session-001-analysis.json`
   and its retained session ledger.
3. **Prospective multi-map host 1**
   `results/publication/dancers-multimap-v1-host-1/analysis.json`,
   `analysis.csv`, `FINDINGS.md`, `input_manifest.json`,
   `build_manifest.json`, `ledger.ndjson`, and `schedule.json`.
4. **Restricted ORCA session 002**
   `comparison/publication/dancers-orca-v1-session-002-outcome-packet/manifest.json`,
   `manifest.sha256`, `outcome.json`, and `analysis.json`.
5. **Replay gallery**
   `results/comparison/demo/held-out-safe-grid-v1/index.html` and the five
   scene-specific HTML bundles listed under Figure 4.
6. **Historical scaling checkpoint**
   `docs/performance/SCALING_BASELINE.md` and
   `docs/performance/SCALING_RESULTS.md`.

The authenticated ORCA outcome-packet manifest SHA-256 is
`3e8b137f9aef3a6f672f79ade6268dad967d57f2f402d656bdb702933babe0b3`;
its full-analysis SHA-256 is
`ee7a0f445b5ef0efa55ccc0b06cd2a7a12afdf294dd0d4b8b22dd70cc134be51`.

## Alternative compact layout

If the final venue permits only four main figures, use this compression:

1. Combine Figure 1 with a small evaluation-gate strip.
2. Keep Figure 2 unchanged.
3. Combine Figures 3 and 4 by replacing the five trajectory panels with one
   task-valid example and one identical-contact inset; move the full five-
   family gallery to Extended Data.
4. Keep Figure 5 unchanged because its protocol boundary is essential to
   avoiding a false DANCeRS-versus-ORCA runtime claim.

The five-figure claim spine is preferred because it gives mechanism,
prospective performance, trajectory preservation, and external context one
clear job each.

## Artwork production order and gates

1. **Complete:** Figure 2 was produced from the authenticated ablation JSON,
   verified against `FINDINGS.md`, and packaged at
   `figures/publication/fig02_ablation/`.
2. **Complete:** Figure 3 was produced from the authenticated multi-map JSON,
   retains the host-1 limitation in-panel, and is packaged at
   `figures/publication/fig03_multimap/`.
3. **Complete:** Figure 4 was extracted from the registered replay payloads and
   raw position traces, its continuous contact state was verified against the
   authenticated analysis and summary, and it is packaged at
   `figures/publication/fig04_trajectories/`.
4. **Complete:** Figure 5 was produced from the authenticated ORCA outcome
   packet, all 12 metrics and ratio directions were audited, and it is packaged
   at `figures/publication/fig05_orca/`.
5. **Complete:** Figure 1 was drawn last with the stable manuscript terminology,
   redesigned against a recent npj Robotics reference corpus using a
   mechanism-led three-panel visual grammar, and is packaged at
   `figures/publication/fig01_overview/`.
6. **Complete:** final-size QA was run across Figures 1–5: sans-serif text and
   panel labels meet the artwork floor, labels are unclipped, no claim relies
   on color alone, and the vector masters retain live text.

The final venue/page budget remains **[AUTHOR TO CONFIRM]** before typesetting.
The second-host result and updated scaling rerun are evidence gates whose
pending status must remain explicit until resolved.
