# DANCeRS optimization paper — caption-only story V3

Status: **Draft for author review.** Edited 25 September 2026. V2 remains the
approved baseline; V3 adds an engineering guide as the seventh main figure.

## Main message and figure sequence

On the tested workloads, the optimized implementation reproduces the
registered DANCeRS outputs exactly while reducing execution time with nearly
unchanged peak memory use. Paired measurements provide a practical first
screen against a specified computation budget.

The story proceeds from the visible result to architecture, mechanisms,
multi-map evidence, physical trajectories, external comparison, and a worked
engineering calculation. The Park and Santamarina papers provide the model
for this final step: an explicit relation, its range of use, and an example.

Terminology: GBP means Gaussian belief propagation; peak RSS is peak resident
set size, the process-memory measure used here. Fidelity means equality of the
registered outputs. It does not by itself establish task success or safety.

## Main-figure captions

### Figure 1 | Visual thesis: identical behaviour at lower implementation cost

**Visual summary of result-preserving DANCeRS optimization.** **a,** A paired
mission is executed by the source-pinned reference and optimized builds using
the same map, robot states, goals, random seed, GBP schedule, and update order.
Their synchronized robot trajectories and registered semantic outputs
coincide. **b,** The optimized build removes repeated data access,
computation, message copying, and ownership work at five implementation
surfaces while leaving the planner decision rule unchanged. **c,** Exact
fidelity is tested before wall-time, CPU-time, or peak-RSS evidence is
admitted. The figure is a visual thesis; the mechanisms and quantitative
results are resolved in Figures 2–4.

**Narrative job:** show the whole result in one view: the same DANCeRS
behaviour is obtained with less implementation work.

### Figure 2 | Unchanged DANCeRS architecture and optimization surfaces

**Implementation changes within the retained DANCeRS computation
architecture.** **a,** Each robot retains the planning and global-consensus
factor-graph layers, local sensing, dynamically created inter-robot factors,
two-phase GBP message exchange, state update, and motion to the next timestep.
Open circles denote variables, filled squares denote factors, dashed
connectors denote messages or state information, and solid paths denote robot
motion. **b,** Five numbered hotspots locate the cumulative changes:
contiguous KD-tree positions and robot identifiers; exact reuse of unchanged
sensing topology; reduced GBP copying, redundant solves, and repeated
snapshots; scalar R1 information operations with identity-transport bypass;
and single-owner movement of externally staged messages. **c,** Reference and
optimized execution traces retain the GBP schedule, internal sweep count,
update order, convergence settings, and paper metrics. The optimization
changes the implementation of existing operations and introduces no new
planner block.

**Narrative job:** locate every optimization within the unchanged algorithmic
architecture.

### Figure 3 | How the implementation removes work

**Mechanisms and historical-order cumulative ablation of the five
implementation changes.** **a,** Sensing-side changes replace repeated
map-based position and identifier access with contiguous KD-tree inputs and
reuse the neighbour topology only when the complete sensing state is
unchanged. **b,** GBP-side changes reuse valid snapshots and solutions,
specialize one-dimensional information operations, bypass identity transport,
and move externally staged messages under a single owner. These changes do not
alter graph edges or the GBP schedule. **c,** On the registered sparse
workload, complete-process wall time decreases at every cumulative stage for
500 and 1,000 robots. Paired earlier/later wall-time and total-CPU ratios are
shown with deterministic 95% cluster-bootstrap intervals from ten complete
seeds. Cumulative S0-to-S5 wall-time speedups are 8.4454× (95% interval
8.2915–8.6021) and 14.7229× (14.3003–15.1753), respectively. **d,** Peak RSS
remains approximately 44 MiB and 55 MiB. The ablation follows the historical
cumulative order and does not estimate isolated or order-independent causal
effects.

**Narrative job:** explain the removed work and measure the cumulative
contribution of the implementation changes.

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

**Narrative job:** establish the main prospective evidence before any
engineering translation is attempted.

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

**Narrative job:** make preservation physically visible, including the
preservation of a reference failure case.

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

**Narrative job:** place the optimized implementation in restricted external
context without confusing implementation efficiency with planner behaviour.

### Figure 7 | Engineering budget guide and worked example

**Paired execution measurements support an initial computation-budget
screen.** **a,** The procedure checks exact output fidelity, median execution
time against the specified time budget, and median peak RSS against the
specified memory budget. Task validity is assessed separately. **b,** The
design map plots measured speedup $S=T_{\mathrm{ref}}/T_{\mathrm{opt}}$ against
required speedup $S_{\mathrm{req}}=T_{\mathrm{ref}}/T_b$, where the two measured
times are complete-process medians and $T_b$ is the available time budget.
Points with $S\geq S_{\mathrm{req}}$ pass the median-time screen. **c,** For
six robots in the registered perfect-maze case (map 05, mission 39), median
wall time decreases from 0.449964 s to 0.342231 s: a speedup of 1.3148× and a
23.94% reduction in execution time. With an illustrative 0.400 s budget, the
optimized median leaves 0.057769 s of headroom; the reference median exceeds
the budget. Both registered outputs match exactly over 200 frames, and the
scene passes the registered task-validity checks. Median peak RSS changes
from 40.484 to 40.563 MiB. **d,** Each resource value is the median of three
timing runs per build on host 1. These measurements support preliminary
screening; they do not establish the probability of meeting a deadline or a
worst-case bound.

**Narrative job:** show how an engineer can translate paired measurements
into a budget calculation, with its evidence limits stated beside the result.

## Engineering calculation behind Figure 7

For the same fixed workload and hardware, let $T_{\mathrm{ref}}$ and
$T_{\mathrm{opt}}$ denote median complete-process wall times. Then

$$
S=\frac{T_{\mathrm{ref}}}{T_{\mathrm{opt}}},\qquad
S_{\mathrm{req}}=\frac{T_{\mathrm{ref}}}{T_b},\qquad
h=1-\frac{T_{\mathrm{opt}}}{T_b}
=1-\frac{S_{\mathrm{req}}}{S}.
$$

Here $h$ is the fraction of the time budget left after the median execution.
The median-time screen passes when $h\geq0$, or equivalently
$S\geq S_{\mathrm{req}}$. Exact output fidelity and the memory check are
additional requirements. Passing these checks does not replace validation of
the intended robot task.

For the worked case:

| Quantity | Value | Basis |
|---|---:|---|
| Reference wall time, $T_{\mathrm{ref}}$ | 0.449964 s | Measured median |
| Optimized wall time, $T_{\mathrm{opt}}$ | 0.342231 s | Measured median |
| Time budget, $T_b$ | 0.400 s | Illustrative requirement |
| Measured speedup, $S$ | 1.3148× | Ratio of medians |
| Required speedup, $S_{\mathrm{req}}$ | 1.1249× | Reference median / budget |
| Optimized headroom, $T_b-T_{\mathrm{opt}}$ | 0.057769 s | Calculated |
| Fractional headroom, $h$ | 14.44% | Calculated |
| Reference / optimized peak RSS | 40.484 / 40.563 MiB | Medians of per-run peaks |

Time saved is $\Delta T=T_{\mathrm{ref}}-T_{\mathrm{opt}}=0.107733$ s,
or 23.94% of the reference median. The 23.94% saving and 14.44% budget headroom
use different denominators and should remain distinct. A specified memory
budget is still needed to complete the resource screen; the table reports
memory use without assuming an available hardware capacity.

The interval $T_{\mathrm{opt}}\leq T_b<T_{\mathrm{ref}}$ identifies budgets
that the optimized median satisfies while the reference median does not.
It gives no per-run deadline guarantee. A confidence interval for a cell's
speedup also gives no prediction bound for an individual execution. Hard
real-time use requires a worst-case timing bound under specified operating
conditions; probabilistic deadline targets require suitable tail-latency
evidence. Complete-process timings cannot establish per-update latency.

Source: [registered host-1 analysis](</Users/w.cha@bham.ac.uk/Documents/Beyond Dancers/results/publication/dancers-multimap-v1-host-1/analysis.json>),
scene `perfect_maze-map-05-m39-n6`, using `resources.scene_rows`,
`fidelity.pair_rows`, and `fidelity.task_rows`. The example is an illustrative
reuse of an existing scene, not an independent validation experiment. Reported
calculations use unrounded source values.

## Extended Data sequence

### Extended Data Figure 1 | Circle cases and ORCA cadence sensitivity

**Descriptive circle-task outcomes and registered cadence sensitivity.**
**a,** In the three fixed circle cases, DANCeRS completion fractions are 1.00,
1.00, and 0.74, whereas main-cadence ORCA fractions are zero; contacts occur
in both methods. **b,** ORCA contact outcomes change strongly under the
registered cadence sensitivity. These cases are descriptive and do not
support population-wide completion or safety claims.

### Extended Data Figure 2 | Historical sparse scaling boundary

**Historical sparse-scaling checkpoint and fixed-budget boundary.** Reference
and optimized wall/CPU measurements are shown against robot count for the
bounded-degree sparse workload. The 500-robot 5.52 s-to-0.79 s checkpoint and
historical 2,900-robot fixed-budget boundary provide context only; a
prospective final-source rerun is required before promotion to a main figure
or use in a sizing law.

### Extended Data Figure 3 | Complete held-out validation matrix

**Held-out task-validity and admitted-timing matrix.** All ten
family-by-count cells are retained. Intervals are shown for the eight fully
task-valid cells; fractal-low N=4 and fractal-medium N=4 are labelled
task-invalid and their timing is not admitted. The two invalid cells are not
omitted or repaired.

## Research note: prospective capacity model

The following model is a hypothesis for a future calibration study and is
excluded from the Figure 7 result. For a fixed execution horizon, a candidate
cost model is

$$
T_{\mathrm{opt}}=c_0+c_NN+c_EE+c_{IE}IE,
$$

where $N$ is robot count, $E$ is the number of active inter-robot graph edges,
and $I$ is the number of GBP sweeps. If the graph degree is approximately
constant, $E\approx\bar{k}N/2$. The model would then give

$$
N_{\max}=\left\lfloor
\frac{T_b-c_0}
{c_N+(\bar{k}/2)(c_E+Ic_{IE})}
\right\rfloor,
$$

provided $T_b\geq c_0$, the denominator is positive, and the result lies in
the validated range. This relation assumes fixed mean degree, sweep count,
horizon, and platform. Its model form, coefficients, uncertainty, and
predictive error remain unvalidated. It cannot yet support a swarm-capacity
claim or a hardware recommendation.

## Review status

The proposed seventh figure and its worked example are ready for author
review. The six-figure V2 baseline remains unchanged. This cleanup updates
the caption draft and calculation notes only; it does not add experimental
evidence or final artwork.
