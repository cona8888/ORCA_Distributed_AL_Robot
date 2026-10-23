# DANCeRS–ORCA restricted external-comparison protocol v1

Status: prospective and frozen after the RVO2 feasibility pass, before any
DANCeRS-versus-ORCA outcome. Unfavourable outcomes remain valid results.

Date: 2026-08-25. Scope: deterministic, obstacle-free, direct-goal navigation
only. This protocol adds external behavioral context to the DANCeRS
implementation-optimization paper; it is not a general planner contest.

## Questions and estimands

The causal implementation question remains:

> On the added workload, does optimized DANCeRS exactly preserve reference
> DANCeRS semantics while reducing whole-process wall and total CPU time?

The restricted external question is:

> On the same frozen obstacle-free start/goal tasks, what completion, continuous
> pair-clearance, path, and common-grid smoothness outcomes are observed for
> native DANCeRS and native ORCA/RVO2 under the prospectively mapped parameters?

Reference-to-optimized DANCeRS resource ratios are causal implementation
estimands. DANCeRS-to-ORCA differences are conditional artifact/task
observations under different native motion and coordination models.

## Source endpoints

| artifact | source endpoint |
|---|---|
| reference DANCeRS algorithm | `986f47deec5b079663c7d7f6120bfc20f0467901` |
| reference comparison head/tree | `de0f60d1ad4b944c2b3264b8e3ccd348708b8b8a` / `cb93386e28f3824ea7ffc17605b441b92f13f87d` |
| optimized DANCeRS algorithm | `99364a41f1535a4bb0eddbe5a10030127ea77086` |
| optimized comparison head/tree | `26c8c6f9b262a2114ac28203419cb03caf8b0d88` / `b6f713f47ea3aa57c824cf2c481f3647ac7c9408` |
| common comparison patch SHA-256 | `2ab81c9a9621c89de5854bbd8496cb2124845733140a913855ca61677185a95e` |
| ORCA/RVO2 commit/tree | `1c25b27258d191e26c2df83ff6e31abc8efbdaed` / `754114c13a345b1d228871be7e85683e31b3af5d` |

The DANCeRS endpoints and common patch are unchanged from the frozen multi-map
study. RVO2 must be built unmodified with OpenMP disabled. A repository head or
locally modified checkout is inadmissible.

## Frozen scenarios

There are 23 main-cadence scenes in two separately interpreted sets.

### A. Fixed open missions

Use all twenty existing `open/canonical`, `N=8`, mission-31 through mission-50
start/goal files. No mission may be selected, replaced, or removed after
observing either method.

The existing explicit waypoints are retained byte-for-byte. Each mission has
eight robots, radius 1 m, required straight travel in `[8, 12]` m, endpoint
separation at least 2.25 m, and endpoints away from the former raster boundary.
For this external study, the DANCeRS derivative config must omit
`ASSETS_DIR`/`OBSTACLE_FILE`; RVO2 receives no obstacles. Thus both methods run
in genuinely obstacle-free unbounded local geometry. `WORLD_SZ=100` remains a
DANCeRS coordinate/display field, not a collision boundary.

Fixed ceiling: 100 steps at 0.1 s = 10 s. Agents remain present through the
ceiling even after reaching their goal.

### B. Antipodal-circle stress scenes

Create explicit, create-exclusive scenario JSON files for
`N in {8, 20, 50}`. For robot `i`:

```text
theta_i = 2*pi*i/N
start_i = (20*cos(theta_i), 20*sin(theta_i)) metres
goal_i  = -start_i
radius_i = 1 metre
initial translational velocity_i = (0, 0) metres/second
```

The DANCeRS scenario heading points directly from start to goal; ORCA has no
heading state. The explicit scenario bytes, not either executable's internal
circle generator, are the common input. Initial centre separation exceeds 2 m
at all three counts.

Fixed ceiling: 600 steps at 0.1 s = 60 s, three times the 20 s no-conflict
lower bound for travelling the 40 m diameter at 2 m/s. Timeout, non-completion,
contact, and deadlock remain in the result.

### Excluded scenes

Maze, fractal, `circle_cluttered`, and other static-obstacle scenes are excluded
from confirmatory v1. RVO2's line obstacles avoid local collision but do not
choose a route around clutter; its preferred velocity would need a global
planner. Raster-to-polygon conversion plus routing would compare a new pipeline.

## Frozen parameter mapping

| quantity | DANCeRS | RVO2/ORCA | rule |
|---|---:|---:|---|
| dimension/shape | planar closed disc | planar closed disc | radius 1.0 m |
| native motion | nonholonomic/dynamic planning factors | holonomic velocity execution | preserve; do not convert |
| starts/goals | explicit scenario file | same explicit scenario file | no assignment |
| initial translation | zero | zero via `setAgentVelocity` | exact |
| maximum translation speed | 2.0 m/s | 2.0 m/s | exact |
| main decision step | 0.1 s | 0.1 s | equal cadence |
| look-ahead | 1.5 s planning horizon | `timeHorizon=1.5` s | precursor's matching rule |
| neighbourhood | 20 m communication radius | `neighborDist=20` m | precursor's matching rule |
| neighbour cap | all within range | `maxNeighbors=N-1` | no silent truncation |
| communication failure | 0 | not applicable; no communication | disclose difference |
| preferred motion | native waypoint/horizon factors | vector directly to final goal at 2 m/s | task input only |
| goal tolerance | centre within 1.0 m | centre within 1.0 m | one robot radius |
| static obstacles | none | none | exact |
| parallelism | `OMP_NUM_THREADS=1` | OpenMP disabled | serial |
| stopping | fixed ceiling | fixed ceiling | no early process stop/deletion |

At 2 m/s and a 1.5 s ORCA horizon, the pairwise relevance distance implied by
the two agents' maximum speeds is 6 m; `neighborDist=20` m is therefore not the
binding cutoff. `timeHorizonObst` is set to 1.5 s for API completeness but is
inactive because no obstacle is added.

RVO2 preferred velocity is recomputed before every step as the normalized
current-position-to-goal vector times 2 m/s. It becomes exactly zero once the
centre is within 1 m of the goal and stays zero thereafter. No routing,
coordination, smoothing, acceleration limiting, unicycle projection, recovery,
or collision repair is allowed.

## Input and wrapper freeze

A deterministic input compiler must publish under
`comparison/publication/dancers-orca-v1/`:

- all 23 scenario JSON files;
- derivative DANCeRS configs;
- one canonical manifest with path, bytes, SHA-256, scene ID, robot count,
  ceiling, and every effective parameter;
- a machine-readable method/parameter crosswalk; and
- a deterministic method-order schedule.

The compiler may read the twenty frozen source scenarios and the source code's
declared circle formula. It must not launch a simulator, read a result
directory, or overwrite an existing publication input.

The RVO2 wrapper may parse the common scenario, call only the pinned public API,
and write the common observation schema. Its source, build command, binary hash,
parameter audit, and two-run smoke digest must be committed before confirmatory
execution. It may not modify files in the RVO2 checkout.

## Admission and integrity gates

The study fails closed unless all of the following pass before aggregation:

1. every source, patch, input, config, build product, wrapper, and schedule hash
   matches the frozen manifest;
2. the RVO2 checkout is clean at the pinned commit and its core licence remains
   Apache-2.0;
3. wrapper logs prove every requested RVO2 parameter was accepted;
4. two serial wrapper runs on a non-confirmatory two-agent smoke scene are
   byte-identical;
5. reference and optimized DANCeRS normalized semantic traces and registered
   summaries are exactly equal on every one of the 23 scenes; and
6. process attempts and outputs are complete, finite, uniquely identified, and
   use the frozen step count.

A DANCeRS fidelity failure blocks both the external interpretation and the
optimization claim for that scene. It does not authorize deletion or rerunning
only the unfavorable scene. An ORCA crash, contact, timeout, or deadlock is an
outcome, not an exclusion.

Implementation defects discovered before confirmatory execution require a
versioned amendment and a new wrapper/input freeze. Once any confirmatory
external outcome exists, parameters may not be tuned. The Robotarium fallback
is used only if a documented artifact blocker invalidates feasibility, never
because ORCA performs unfavorably.

## Execution inventory

For each of the 23 scenes:

1. one observer-enabled reference/optimized DANCeRS digest pair;
2. one observer-enabled reference/optimized DANCeRS positions pair;
3. two serial ORCA positions runs, required to be byte-identical, with the first
   retained as the canonical behavioral trace after the check;
4. three observer-disabled whole-process repetitions for reference and
   optimized DANCeRS; and
5. three observer-disabled ORCA repetitions retained only as artifact-level
   engineering context.

Before measured runs, each method receives one untimed warm-up per scene set and
robot count. All processes are serial and non-overlapping. A SHA-256-derived
schedule balances method order independently of results. Set
`OMP_NUM_THREADS=1`, `OMP_DYNAMIC=FALSE`, and `OMP_WAIT_POLICY=passive` for
DANCeRS; RVO2 is built with OpenMP disabled. Every process has a 300 s
whole-process hard timeout. A timeout is retained as a terminal outcome and is
never retried selectively.

The three circle scenes additionally receive the prospectively declared ORCA
cadence sensitivity: native RVO2 at 0.05 s, with the same physical ceiling,
horizon, starts, goals, radii, and speed. Positions are resampled onto the 0.1 s
grid for behavioral metrics. Each count receives two serial positions runs,
which must be byte-identical; the first is retained and no resource repetition
is measured. This is a discretization diagnostic, not an equal-work comparison;
it receives no compute ranking and is run regardless of the main-cadence
result.

The inventory is therefore 345 main-scene processes, six cadence-sensitivity
processes, and twelve untimed warm-ups: 363 processes total. The schedule must
enumerate exactly these attempts before execution.

## Common behavioral outcomes

All calculations use native states observed at the common 0.1 s grid.

### Primary

- all-agents goal completion by the fixed ceiling;
- per-agent continuous first passage into the closed 1 m goal disc;
- all-agents makespan when complete and restricted mean completion time through
  the fixed ceiling when censored;
- per-agent path length and excess over its straight start-to-goal distance;
- continuous minimum pair clearance in metres;
- number of pair-contact episodes; and
- robot-pair-seconds of contact exposure.

For interval `k`, linearly sweep each centre from `p_k` to `p_(k+1)`. For a pair,
minimize the norm of the relative linear motion on the closed interval and
subtract the sum of radii. Clearance `<= 0` is unsafe, so tangency counts as
contact. Contact exposure is the exact duration within each interval for which
the relative-distance quadratic is at most the squared summed radius. Goal
first passage uses the analogous segment-versus-goal-disc intersection.

### Secondary smoothness descriptors

At `dt=0.1 s`, use the recorded translational velocity `v_k`:

```text
velocity_total_variation = sum_k ||v_k - v_(k-1)||
a_k = (v_k - v_(k-1)) / dt
acceleration_rms = sqrt(mean_k ||a_k||^2)
jerk_rms = sqrt(mean_k ||(a_k - a_(k-1))/dt||^2)
```

These are common-grid trajectory descriptors, not actuator-feasibility tests.
Angular velocity is excluded because native ORCA has no heading. Any smoother
trajectory observation must be qualified by the unequal native motion models.

## Resource measurements

Each observer-disabled process records monotonic whole-process wall time,
`wait4` user and system CPU, total CPU as their sum, and peak RSS with explicit
platform units. Three repetitions collapse first to a within-scene median.

Only reference/optimized DANCeRS paired ratios estimate implementation speedup
or memory change. ORCA wall/CPU/RSS and agent-steps per CPU-second may be shown
as pinned-artifact context in a separate table. A DANCeRS/ORCA runtime ratio is
not an algorithmic speedup because the methods perform different work and the
process wrappers have different initialization boundaries.

## Analysis

Analyze the two scene sets separately.

- For the twenty open missions, show every paired scene outcome and summarize
  paired differences with a median, mean, and deterministic 100,000-draw
  mission bootstrap interval. Label this fixed-input/fixed-open-world
  uncertainty; do not claim a population of open environments.
- For the three circle counts, report each count descriptively. Do not attach
  population p-values or pool the three symmetric scenes as independent random
  environments.
- Show all failures, censored completion times, contacts, and deadlocks in the
  denominator. There is no outlier deletion, winsorization, task-validity
  filtering, or result-based retry.
- Timing repetitions are measurement repeats, not independent experimental
  units.
- Do not pool open and circle results into one superiority score.

For the added workload's causal optimization result, use the existing paired
reference/optimized log-ratio analysis and fidelity gate. Behavioral comparison
may use one common DANCeRS trajectory only after reference/optimized exact
equality has passed.

## Claim boundary

Permitted, when supported:

- exact reference/optimized DANCeRS equivalence on all added scenes;
- reference-to-optimized DANCeRS resource change on this workload;
- conditional completion, clearance, contact, path, makespan, and smoothness
  observations for the exact tasks, pins, and native models; and
- assumption-aware consistency or inconsistency with the direct GBP precursor's
  published ORCA comparison.

Forbidden:

- universal claims that DANCeRS or ORCA is better, safer, smoother, faster,
  more scalable, complete, deadlock-free, or collision-free;
- treating ORCA as equal work or as a substitute for reference DANCeRS;
- applying ORCA's holonomic collision-avoidance theorem to DANCeRS or to a
  hypothetical unicycle-converted ORCA controller;
- generalizing the open/circle result to maze, fractal, cluttered, hardware, or
  arbitrary-environment performance; and
- selecting cadence, count, scene, parameter, metric, or failure handling after
  seeing which method benefits.
