# Publication plan: result-preserving DANCeRS optimization

Status: Tasks 1, 2, and 4 complete. Task 3 host-1 evidence is complete;
independent second-host replication remains in progress. Task 5 now has an
evidence-mapped figure-and-table storyboard plus verified Figure 1–5 production
packages; main figure artwork is complete, while table and artifact integration
remain in progress. Task 6 has started with an
audited external-baseline Results module.

## Recommended article

Prepare a full robotics-systems paper about a result-preserving implementation
optimization of DANCeRS. The work should not be presented as a new motion
planner or a safer collision-avoidance algorithm.

Working title:

> Result-Preserving Optimization of Gaussian Belief Propagation for Scalable
> Multi-Robot Planning

One-sentence message:

> Careful data-layout, reuse, scalar-algebra, and message-ownership changes can
> reduce the computational cost of DANCeRS while preserving its GBP schedule,
> trajectories, paper metrics, and even its observed failure cases.

Intended article class: reproducible robotics-systems/performance paper. A
workshop or software paper is a fallback, not the primary target. Venue
selection should follow the literature and external-baseline review rather than
drive the technical claims in advance.

## Research questions

1. **Fidelity:** Does the optimized implementation preserve the observable
   behaviour of the source-pinned DANCeRS reference?
2. **Efficiency:** How much wall time, CPU time, and memory does each
   optimization remove at fixed work?
3. **Scaling:** How does the improvement change with swarm size and graph
   density?
4. **Planning evidence:** Does result preservation hold across open, maze, and
   controlled-fractal obstacle environments on held-out missions?
5. **Boundary:** Which limitations belong to the underlying controller rather
   than to the optimization?
6. **External context:** What parameter-conditional behavioural differences
   are observed between native DANCeRS and native ORCA on one prospectively
   restricted shared task?

## Proposed contributions

1. Five result-preserving implementation changes:
   contiguous KD-tree inputs and robot IDs; exact sensing-state reuse; reduced
   GBP message copying and redundant solves; scalar information-form operations
   for R1; and single-owner movement of externally staged messages.
2. A paired, source-pinned evaluation protocol that fixes missions, maps,
   controller work, method order, runtime seed, and analysis before admitting a
   performance result.
3. Exact semantic-fidelity evidence covering trajectories, simulator state,
   iterations, goals, paper metrics, and continuous collision geometry.
4. Scaling and held-out planning evidence that distinguishes sparse-swarm
   compute gains from environment-specific end-to-end gains.
5. A reproducible artifact with machine-readable results and synchronized
   offline reference/optimized replay.

## Evidence already available

| Evidence | Current result | Permitted use |
|---|---|---|
| Held-out digest pairs | 600/600 semantically equal | Strong fidelity evidence |
| Held-out position scenes | 198/200 task-valid | Planning evidence with explicit failures |
| Fully passing held-out cells | 8 fixed-suite cells, 20 missions per cell | Descriptive fixed-suite performance result |
| Held-out speedup | 1.2833x to 1.3547x; all registered lower 95% bounds above threshold | End-to-end speed evidence for those eight cells |
| Failed held-out scenes | Two identical reference/optimized contacts at N=4 | Inherited controller boundary and stringent fidelity evidence |
| Sparse scaling checkpoint | 500-robot median 5.52 s to 0.79 s; historical fixed-budget boundary 2,900 robots | Separate sparse-scaling result, subject to an updated reproducible rerun |
| Prospective cumulative sparse ablation | 8.4454x at N=500 and 14.7229x at N=1,000, with all adjacent increments positive | Mechanistic, historical-order contribution evidence; not a modern-planner or order-independent effect claim |
| Prospective host-1 multi-map study | 200/200 digest pairs and 200/200 position pairs exact; all ten wall, CPU, and RSS gates pass across 41 maps | Fixed-work efficiency and tested stochastic-map-family evidence on one machine |
| Prospective restricted ORCA study | Session 002 completed 363/363 processes; all 23 DANCeRS scenes and 26 ORCA repeatability cohorts passed; open-mission wall, CPU, and RSS ratios were 1.3200, 1.3457, and 1.0021 | Added-workload implementation evidence and fixed-input behavioural context; not a universal planner ranking or a DANCeRS/ORCA runtime ratio |
| Replay artifact | Five synchronized, self-contained 200-frame comparisons | Qualitative inspection and artifact demonstration |

The sparse scaling result and held-out planning result answer different
questions and must not be blended into one headline speedup. The former isolates
algorithm infrastructure under a sparse graph; the latter measures the complete
fixed-horizon planner in obstacle environments.

The restricted ORCA study answers a third question. Its reference/optimized
DANCeRS ratios measure implementation efficiency on the added workload, while
its native DANCeRS-minus-ORCA differences provide parameter-conditional
behavioural context. Neither result may be blended with the sparse or obstacle-
map studies into one headline speedup, and native DANCeRS/ORCA runtime is not
an equal-work estimand.

## Claim matrix

### Supported now

- The optimized and reference builds are semantically identical on the
  authenticated paired evidence.
- The optimized build is faster on the eight listed held-out fixed-suite cells.
- The two detected pair contacts occur identically in both methods and are not
  optimization regressions.
- The implementation changes preserve the GBP exchange schedule, internal
  sweep count, update ordering, convergence settings, and paper metrics.
- In the historical sparse workload, all five cumulative optimization stages
  add positive wall-time increments in their recorded order; the largest gains
  come from contiguous KD-tree layout and scalar R1 specialization.
- On host 1, all ten prospective multi-map cells improve in wall time and total
  CPU, with no measured peak-RSS increase above 5%; exact paired fidelity holds
  in all 200 scenes across 40 independently generated stochastic maps and one
  fixed open map.
- On the restricted external workload, all 23 DANCeRS scenes pass exact
  reference/optimized fidelity; the 20 open-mission wall and CPU ratios are
  1.3200 [1.3125, 1.3279] and 1.3457 [1.3410, 1.3504], and the RSS ratio is
  1.0021 [1.0015, 1.0026].
- In the 20 fixed open missions, DANCeRS and ORCA both complete every agent.
  DANCeRS has lower acceleration and jerk RMS but higher velocity total
  variation, longer paths, and later completion under the registered mapping.
- In the three descriptive circle cases, DANCeRS completes a larger agent
  fraction under the registered parameterization; contacts occur in both
  methods, and ORCA contact outcomes are strongly cadence-sensitive.
- Rendering is excluded from timing and used only for replay.

### Requires new evidence before an archival submission

- Hardware portability: requires at least one genuinely independent second
  machine, preferably a second operating-system/toolchain combination.
- Cross-host obstacle-planning resource efficiency: the complete CPU/RSS result
  currently comes from one machine and requires independent replication.
- Broader environment generalization: the stochastic-map result covers the
  registered maze and controlled-fractal generators, not arbitrary maps or the
  population of open maps.
- External-baseline breadth: the confirmatory ORCA result covers one
  obstacle-free fixed-input protocol, one host, and the two native motion
  models. Population-wide environment, hardware, parameter, and real-world
  generalization requires separately planned evidence.
- Updated large-swarm scaling: requires a prospective rerun using the final
  source and measurement protocol.

### Prohibited claims

- A new motion-planning or collision-avoidance algorithm.
- Safer, shorter, or higher-quality paths than DANCeRS.
- Universal collision freedom or positive validation of the complete grid.
- A population-wide environment-family effect from one map per family.
- A general 2,900-robot planning capacity across arbitrary environments.
- Constant-compute scaling for dense or all-to-all communication graphs.
- GPU, parallel, real-time, or interactive-frame-rate gains not directly tested.
- A universal claim that DANCeRS or ORCA is the better, safer, smoother,
  shorter-path, or more complete planner.
- A causal DANCeRS-versus-ORCA speedup or equal-work runtime comparison.
- Pooling, repairing, or hiding the failed session-001 external execution.

## Paper structure

1. **Introduction:** the value and difficulty of accelerating distributed GBP
   without changing its numerical or scheduling behaviour.
2. **Background:** DANCeRS, GBP message exchange, planning layers, and the
   distinction between algorithmic and implementation equivalence.
3. **Optimization design:** describe the five changes and why each is
   mathematically result-preserving.
4. **Experimental protocol:** source pins, fixed work, paired ordering,
   fidelity gates, maps, missions, timing, geometry, and statistics.
5. **Ablation and scaling:** per-change contribution, swarm-size/density trends,
   CPU, memory, and cross-machine replication.
6. **Held-out planning evaluation:** open, maze, and controlled-fractal results,
   including both failed scenes.
7. **Restricted external context:** exact added-workload DANCeRS fidelity and
   efficiency, fixed-open-world DANCeRS–ORCA differences, and descriptive
   circle/cadence sensitivity without a pooled planner ranking.
8. **Discussion:** why scaling gains and end-to-end gains differ; inherited
   safety limitations; native-model comparability; generalization and hardware
   limitations.
9. **Reproducibility:** source identities, scripts, machine-readable results,
   failed-session visibility, and synchronized replay.

## Planned figures and tables

1. Architecture diagram showing unchanged GBP semantics and the five optimized
   implementation surfaces.
2. Ablation plot: cumulative, historical-order wall/CPU contribution and peak
   RSS (without claiming isolated or order-independent effects).
3. Scaling plot: reference and optimized time versus robot count, separated by
   sparse and denser communication regimes.
4. Held-out forest/interval plot for the ten family-count cells, retaining the
   two task-invalid cells rather than plotting admitted timing as valid.
5. Representative synchronized trajectories from all five environment
   families, plus one failed-contact inset.
6. Fidelity table covering hashes, trajectories, metrics, goals, iterations,
   and collision summaries.
7. Cross-machine reproducibility and resource table.
8. Restricted external-baseline table reporting the DANCeRS implementation
   ratios and all 12 fixed-open-world DANCeRS-minus-ORCA metrics.
9. Descriptive circle/cadence-sensitivity table or supplement retaining
   completion, contact, path, clearance, and smoothness outcomes for all three
   registered swarm sizes.

## Sequential execution gates

### Task 1 — story and claims

Complete when this document is reviewed and accepted as the governing claim
boundary. Later work may narrow these claims but must not silently broaden them.

### Task 2 — optimization ablation

Complete. The exact historical reference and five cumulative stages were
frozen before measurement. The sparse N=500/N=1,000 matrix completed all 12
warm-ups and 360 measured runs; exact printed-summary fidelity passed. This
task deliberately does not substitute for modern-planner state/trajectory
fidelity or an order-independent factorial analysis.

### Task 3 — replication and resources

Repeat the final paired protocol on a second machine, retain CPU and peak-memory
measurement, and evaluate multiple prospectively generated maps per family.

The governing design is now
`docs/publication/DANCERS_MULTIMAP_REPLICATION_STUDY_V1.md`: 200 scenes over 41
maps, exact digest/position parity, three observer-free timing repetitions,
CPU/RSS analysis, and a rebuild-only second-Mac capsule. Local execution can
complete the multi-map/resource component. Host 1 has now completed all 2,082
scheduled processes: exact fidelity passed in all 200 scenes, all ten wall/CPU
improvement gates passed, and all ten RSS upper bounds remained below the 1.05
limit. Task 3 remains open until a physically distinct machine returns an
authenticated result.

### Task 4 — external baseline

Complete. A prospectively planned primary-source review screened fourteen
external candidates and fully assessed thirteen official, paper-linked, or
representative code records; the representative record is a third-party MAPF
library and is labelled as such. ORCA/RVO2 was selected at a pinned Apache-2.0
revision, with Robotarium safety barrier certificates as the frozen fallback.
The unmodified RVO2 library built on Apple arm64, passed all three official
tests, and repeated its official Circle output byte-for-byte. The exact
obstacle-free comparison, parameter mapping, adaptations, metrics, and
prohibited claims were frozen before execution.

Session 001 completed the full schedule but remains a sealed failed
infrastructure execution and is not part of the confirmatory estimates. After
the prospective trace-portability and initialization-integrity corrections,
session 002 completed all 363 newly executed processes, with all 23 DANCeRS
fidelity scenes and all 26 ORCA repeatability cohorts passing. All 23 paired
scene bundles were estimable. The 20-open-mission reference/optimized DANCeRS
wall, CPU, and RSS ratios were 1.3200 [1.3125, 1.3279], 1.3457 [1.3410,
1.3504], and 1.0021 [1.0015, 1.0026]. The native behavioural comparison shows
a trade-off rather than universal dominance. The authenticated outcome packet
is `comparison/publication/dancers-orca-v1-session-002-outcome-packet/`, and
the audited manuscript module is
`docs/publication/task4_external_baseline/SESSION_002_MANUSCRIPT_RESULTS_V1.md`.

### Task 5 — synthesis and artifact

In progress. The external-baseline statistical analysis, authenticated outcome
packet, manuscript-ready Results tables, and consolidated caption-led visual
storyboard are complete. The governing panel-level claim/evidence map is
`docs/publication/DANCERS_FIGURE_TABLE_STORYBOARD_V1.md`. The first production
packages are now complete for the conceptual implementation overview (Figure 1,
`figures/publication/fig01_overview/`), historical-order cumulative ablation
(Figure 2, `figures/publication/fig02_ablation/`), prospective host-1 multi-map
evaluation (Figure 3, `figures/publication/fig03_multimap/`), held-out
trajectory fidelity with its inherited contact boundary (Figure 4,
`figures/publication/fig04_trajectories/`), and restricted external context
with an explicit implementation/behavior boundary (Figure 5,
`figures/publication/fig05_orca/`). Main figure artwork is complete. Remaining
work is to render and audit the final tables, integrate the replay gallery, and
provide one-command artifact verification without merging unlike speedup
estimands.

### Task 6 — manuscript

Started. The restricted external-baseline Results module has been drafted and
audited against the frozen outcome packet. The full manuscript must now be
assembled from the synchronized claim-evidence map, subjected to an internal
reviewer-style audit, revised, and only then fitted to the final venue format.

## Task 2 outcome

The raw, machine-readable result is
`results/publication/dancers-ablation-v1-repair-2/`. At N=500, cumulative wall
speedup is 8.4454x (95% interval 8.2915x to 8.6021x); at N=1,000 it is 14.7229x
(14.3003x to 15.1753x). Peak RSS is broadly unchanged. These results remain
separate from the 1.28x to 1.35x held-out obstacle-planning result because the
workloads answer different questions.

## Task 3 host-1 outcome

The compact, machine-readable result is
`results/publication/dancers-multimap-v1-host-1/`. Across the ten registered
family/count cells, wall-time point speedups are 1.2128x to 1.3398x and total-
CPU point speedups are 1.2397x to 1.3560x; every lower 95% bound exceeds 1.00.
Optimized/reference peak-RSS point ratios are 1.0003 to 1.0020, with every
upper 95% bound below 1.003. One of 200 scenes contains a pair contact,
reproduced identically by both methods and retained in every resource estimate.
These data complete the local multi-map/resource gate, but not the cross-machine
gate.

## Task 4 outcome

The compact, authenticated outcome is
`comparison/publication/dancers-orca-v1-session-002-outcome-packet/`. The
session completed 363/363 processes with no terminal failure. Exact
reference/optimized DANCeRS fidelity passed across all 23 scenes, and all 26
ORCA repeatability cohorts passed. Across the 20 fixed open missions, both
methods completed every agent. DANCeRS had lower acceleration and jerk RMS but
higher velocity total variation, longer paths, and later completion. Circle
outcomes were descriptive and cadence-sensitive. These results support the
implementation-efficiency claim and restricted external context, but not a
universal DANCeRS-over-ORCA claim.
