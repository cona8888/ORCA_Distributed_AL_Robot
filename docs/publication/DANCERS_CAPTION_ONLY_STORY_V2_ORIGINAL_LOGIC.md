# DANCeRS optimization paper — caption-only story V2

Status: **AUTHOR APPROVED on 2026-09-09. This named version is frozen as the
governing six-main-figure story.**

## Approval record

- The author approved the original-DANCeRS narrative logic and six-main-figure
  sequence on 2026-09-09.
- Figure 5d–e from the earlier five-figure plan moves to Extended Data Figure 1
  as circle-task and ORCA cadence-sensitivity evidence.
- Sparse historical-order speedups and prospective obstacle-map speedups remain
  separate estimands and must not be pooled.
- Figure 1 uses the authenticated perfect-maze, six-robot, seed-11 held-out
  scene as its visual thesis. This safe, structured scene is reused as a teaser;
  Figure 5 retains the full five-family and contact-boundary evidence.

Reference paper: Aalok Patwardhan and Andrew J. Davison, *DANCeRS: A
Distributed Algorithm for Negotiating Consensus in Robot Swarms with Gaussian
Belief Propagation*, arXiv:2508.18153 (2025),
https://arxiv.org/abs/2508.18153.

The reference is used for narrative and visual-grammar analysis. Its artwork
will not be copied or traced.

## What works in the original paper

The original paper introduces the science in this order:

1. **Visual thesis:** Figure 1 immediately shows the two behaviours enabled by
   DANCeRS.
2. **Architecture:** Figure 2 explains the two-layer factor-graph stack and
   inter-robot connections.
3. **Mechanism:** Figure 3 uses a short time sequence to explain occupancy
   weighting with and without the proposed mechanism.
4. **Primary quantitative evidence:** Figure 4 tests communication radius,
   swarm size, sliding-window length, and convergence.
5. **Physical behaviour and breadth:** Figures 5 and 6 show the temporal
   consensus process and the range of attainable formations.
6. **Comparative scaling and sensitivity:** Figures 7 and 8 compare methods and
   expose parameter-dependent convergence.

The transferable logic is therefore:

`show the result` → `explain the architecture` → `explain the mechanism` →
`quantify the main effect` → `show physical behaviour and limitations` →
`compare externally and test sensitivity`.

## Recommended visual style

The new figures should retain the original paper's scientific identity while
meeting an npj Robotics quality level.

- Preserve its recognisable top-view robots, communication graphs, factor-
  graph nodes, trajectory sequences, and compact quantitative small multiples.
- Use one stable semantic palette: reference in neutral grey, optimized
  DANCeRS in blue, GBP/message information in purple, and contacts or failed
  evidence gates in vermilion.
- Use open circles for variables, filled squares for factors, solid lines for
  physical motion, and dashed lines for message or information flow.
- Keep white backgrounds, direct labels, lower-case panel labels, thin vector
  linework, and generous gutters.
- Remove presentation-style title banners, rounded cards, decorative icons,
  shadows, and coloured status badges.
- Let each figure have one dominant scientific panel or narrative spine. Use
  the caption, rather than text boxes inside the figure, for qualifications and
  secondary interpretation.
- Build all line art as editable SVG and native PowerPoint shapes at 183 mm
  maximum main-figure width, using 5–7 pt normal text and approximately 8 pt
  bold panel labels.

## Revised main-figure sequence

### Figure 1 | Visual thesis: identical behaviour at lower implementation cost

**Visual summary of result-preserving DANCeRS optimization.** **a,** A paired
mission is executed by the source-pinned reference and optimized builds using
the same map, robot states, goals, random seed, GBP schedule, and update order.
Their synchronized robot trajectories and registered semantic outputs coincide.
**b,** The optimized build removes repeated data access, computation, message
copying, and ownership work at five implementation surfaces while leaving the
planner decision rule unchanged. **c,** Exact fidelity is tested before wall-
time, CPU-time, or peak-RSS evidence is admitted. The figure is a visual thesis;
the cumulative mechanisms and quantitative results are resolved in Figures
2–4.

**Narrative job:** give the reader the whole paper in one view: same DANCeRS
behaviour, less implementation work.

**Style:** emulate the role of the original DANCeRS Figure 1—a visually
immediate application-level result—using one measured paired trajectory scene
and a restrained implementation/evidence strip. Do not begin with a dense
software-flow diagram.

**Bridge:** Figure 2 explains where the implementation changes sit inside the
unchanged DANCeRS architecture.

### Figure 2 | Unchanged DANCeRS architecture and optimization surfaces

**Implementation changes within the retained DANCeRS computation
architecture.** **a,** Each robot retains the planning and global-consensus
factor-graph layers, local sensing, dynamically created inter-robot factors,
two-phase GBP message exchange, state update, and motion to the next timestep.
Open circles denote variables, filled squares denote factors, dashed connectors
denote messages or state information, and solid paths denote robot motion.
**b,** Five numbered hotspots locate the cumulative changes: contiguous KD-tree
positions and robot identifiers; exact reuse of unchanged sensing topology;
reduced GBP copying, redundant solves, and repeated snapshots; scalar R1
information operations with identity-transport bypass; and single-owner
movement of externally staged messages. **c,** Reference and optimized
execution traces retain the GBP schedule, internal sweep count, update order,
convergence settings, and paper metrics. The optimization changes the
implementation of existing operations and introduces no new planner block.

**Narrative job:** connect the new work directly to the factor-graph and robot
architecture readers already recognise from the original paper.

**Style:** use an original, cleaner reinterpretation of the original Figure 2
grammar: paired robot stacks and factor-graph topology dominate; optimization
numbers annotate existing surfaces rather than appearing as separate cards.

**Bridge:** Figure 3 explains which repeated operations were removed and shows
their cumulative measured contribution.

### Figure 3 | How the implementation removes work

**Mechanisms and historical-order cumulative ablation of the five
implementation changes.** **a,** Sensing-side changes replace repeated
map-based position/identifier access with contiguous KD-tree inputs and reuse
the neighbour topology only when the complete sensing state is unchanged.
**b,** GBP-side changes reuse valid snapshots and solutions, specialize
one-dimensional information operations, bypass identity transport, and move
externally staged messages under a single owner. These schematics describe
implementation operations and do not alter graph edges or the GBP schedule.
**c,** On the registered sparse workload, complete-process wall time decreases
at every cumulative stage for 500 and 1,000 robots. Paired earlier/later wall-
time and total-CPU ratios are shown with deterministic 95% cluster-bootstrap
intervals from ten complete seeds. Cumulative S0-to-S5 wall-time speedups are
8.4454× (95% interval 8.2915–8.6021) and 14.7229× (14.3003–15.1753),
respectively. **d,** Peak RSS remains approximately 44 MiB and 55 MiB. The
ablation follows the historical cumulative order and does not estimate
isolated or order-independent causal effects.

**Narrative job:** perform the role of the original paper's mechanism figure
before presenting the broader evaluation: show how work is removed, then show
that every cumulative stage contributes under the controlled workload.

**Style:** use two compact before/after mechanism sequences above one dominant
quantitative ablation plot. Avoid five equal-sized cards.

**Bridge:** Figure 4 asks whether exact preservation and resource improvement
continue beyond the sparse diagnostic workload.

### Figure 4 | Prospective multi-map fidelity and resource performance

**Exact fidelity and fixed-work resource results across registered map
families on host 1.** **a,** The prospective matrix contains one fixed open map
and 40 independently generated perfect-maze or controlled-fractal maps,
producing 200 scenes in ten family-by-robot-count cells. All 2,082 scheduled
processes completed, including 82 warm-ups and 2,000 measured runs. **b,** The
reference and optimized builds match exactly for all 200 semantic-digest pairs
and all 200 position pairs. Swept-geometry analysis classifies 199 of 200
scenes as task-valid; the one contact scene is reproduced identically and
retained. **c,** Reference/optimized wall-time ratios range from 1.2128× to
1.3398× and total-CPU ratios from 1.2397× to 1.3560×; every registered lower
95% bound exceeds one. **d,** Optimized/reference peak-RSS ratios range from
1.0003 to 1.0020, with the largest upper 95% bound at 1.0024, below the
registered 1.05 ceiling. The RSS result is not a memory reduction. Independent
host-2 replication remains pending.

**Narrative job:** provide the main prospective quantitative result across
environment families and swarm sizes.

**Style:** follow the original paper's quantitative small-multiple logic, but
use aligned axes, direct ratio-direction labels, uncertainty intervals, and a
single visual hierarchy. Coverage and fidelity remain compact supporting
panels; wall/CPU evidence is dominant.

**Bridge:** Figure 5 shows the physical meaning of exact equality and preserves
the original paper's movement from plots back to robot behaviour.

### Figure 5 | Physical trajectory fidelity and inherited boundaries

**Exact held-out trajectory fidelity and the retained contact boundary.**
**a–e,** Synchronized reference and optimized trajectories for registered
held-out examples from the open, perfect-maze, and three controlled-fractal
families. Every arena spans 100 m × 100 m. Solid optimized paths and dashed
reference paths use the same measured coordinates; open circles mark initial
positions and filled circles with heading ticks mark final poses. Across the
independent held-out analysis, all 600 paired semantic digests are exactly
equal and 198 of 200 position scenes are task-valid. **f,** In an enlarged view
of one task-invalid scene, robots 0 and 3 first touch at 1.164 s and reach a
minimum clearance of −0.277 m at 1.466 s. Both builds reproduce the contact at
the same scene and trajectory state. It is an inherited controller boundary,
not an optimization-induced regression.

**Narrative job:** make exact preservation visible and show that it includes
both successful trajectories and reference failure cases.

**Style:** take the original Figures 5–6 as the narrative reference: use a
regular strip of equal-scale robot scenes, sparse communication/trajectory
overlays, and one enlarged scientific boundary case. Do not add decorative
backgrounds or dashboard panels.

**Bridge:** after equivalence to the original method is established, Figure 6
places optimized DANCeRS in restricted external context.

### Figure 6 | External comparison: conditional trade-offs, not a ranking

**Restricted external context under native DANCeRS and ORCA motion models.**
**a,** The paired reference/optimized DANCeRS comparison estimates
implementation efficiency; the native DANCeRS–ORCA comparison reports
fixed-input behavioural differences and does not define an equal-work runtime
ratio. **b,** Across 20 open missions, reference/optimized DANCeRS wall-time
and total-CPU ratios are 1.3200 (95% interval 1.3125–1.3279) and 1.3457
(1.3410–1.3504), while optimized/reference RSS is 1.0021 (1.0015–1.0026).
Exact fidelity passes in all 23 DANCeRS scenes. **c,** Both native methods
complete every open-mission agent. Under the registered mapping, DANCeRS has
longer paths and later completion, lower acceleration and jerk RMS, and higher
velocity total variation; contact and clearance intervals do not establish a
general safety advantage. Results are conditional on this host, the fixed
inputs, parameter mapping, and native models, and do not support a universal
planner ranking.

**Narrative job:** end the main paper with external evidence that distinguishes
implementation efficiency from planner behaviour and reports trade-offs rather
than declaring a winner.

**Style:** echo the comparative role of the original Figures 7–8 with compact
small multiples and parameter-aware annotations. Keep metric units and ratio
directions directly visible; do not place unlike metrics on a shared scale.

**End point:** optimized DANCeRS executes the registered planner more cheaply
without changing its observed behaviour; comparison with ORCA remains
task-, metric-, parameter-, and cadence-dependent.

## Extended Data sequence

### Extended Data Figure 1 | Circle cases and ORCA cadence sensitivity

**Descriptive circle-task outcomes and registered cadence sensitivity.**
**a,** In the three fixed circle cases, DANCeRS completion fractions are 1.00,
1.00, and 0.74, whereas main-cadence ORCA fractions are zero; contacts occur
in both methods. **b,** ORCA contact outcomes change strongly under the
registered cadence sensitivity. These cases are descriptive and do not support
population-wide completion or safety claims.

**Reason for placement:** this is important sensitivity evidence, but it is not
needed to complete the main open-mission comparison and would otherwise make
Figure 6 carry two endings.

### Extended Data Figure 2 | Historical sparse scaling boundary

**Historical sparse-scaling checkpoint and fixed-budget boundary.** Reference
and optimized wall/CPU measurements are shown against robot count for the
bounded-degree sparse workload. The 500-robot 5.52 s-to-0.79 s checkpoint and
historical 2,900-robot fixed-budget boundary provide context only; a prospective
final-source rerun is required before promotion to a main figure.

### Extended Data Figure 3 | Complete held-out validation matrix

**Held-out task-validity and admitted-timing matrix.** All ten family-by-count
cells are retained. Intervals are shown for the eight fully task-valid cells;
fractal-low N=4 and fractal-medium N=4 are labelled task-invalid and their
timing is not admitted. The two invalid cells are not omitted or repaired.

## Caption-only storyline

`same behaviour, less work` → `unchanged factor-graph architecture` → `five
mechanisms and cumulative contributions` → `prospective multi-map fidelity and
resources` → `physical trajectories and inherited failures` → `restricted
external trade-offs and sensitivity`.

## Why this is closer to the original DANCeRS paper

- It opens with the visible robot-level result instead of implementation
  internals.
- Architecture precedes detailed mechanisms.
- Mechanism explanation precedes quantitative benchmarking.
- Quantitative results are followed by physical robot behaviour.
- External comparison and sensitivity close the evidence sequence.
- Each application or estimand remains in its own figure, avoiding pooled
  claims.

## Required author decisions

1. **Approve six main figures rather than five.** This is the cleanest match to
   the original paper's narrative rhythm and prevents Figure 1 from carrying
   architecture, ablation, and evidence gates simultaneously.
2. **Approve moving circle/cadence panels to Extended Data.** This gives the
   main paper one clear external-comparison ending.
3. **Confirm the Figure 1 representative measured scene** after the sequence is
   approved. It should come from an authenticated paired replay and must not be
   reused as a main Figure 5 example.
4. **Retain separate speedup estimands.** Sparse ablation, prospective
   obstacle-map evaluation, and the restricted added workload must remain
   separately labelled and must never be pooled into one headline speedup.

**AUTHOR GATE 1:** Approve or revise the six-figure sequence, Extended Data
placement, and Figure 1 visual-thesis role before any new artwork is produced.
