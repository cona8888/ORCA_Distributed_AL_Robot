# DANCeRS–ORCA protocol amendment 3 v1

Status: prospective, after the immutable input and ORCA build freezes and
before any confirmatory DANCeRS–ORCA process or outcome.

Date: 2026-08-25.

This amendment applies to the frozen protocol and its first two amendments:

- protocol Git blob `cbf06b0919f5a653195cb5373094434a055daa8c`,
  SHA-256 `04bab9f2da0de290346104e5db3d8881df85a2429d1f9964c4e86943ac7350ff`;
- amendment 1 Git blob `c77d74383297f5a02fb4b3e592dd274e9339749e`,
  SHA-256 `88b7465b61fa565827939a9ffc1b5c9c3c0a681e28af985c91b2b5076c4215b8`;
  and
- amendment 2 Git blob `d3e1a1f2a0a0a2309c9ad7d425b61d3e239bfd87`,
  SHA-256 `f5192e4a304028355a449e8d10b2d6a9c5adb863daa72ea0776ee58a1f77d6b6`.

Those files remain byte-for-byte unchanged. This amendment is authoritative
where it makes their execution, repeatability, failure, or analysis wording
more exact. It changes no scene, method, parameter, cadence, process count,
ceiling, method order, or claim boundary.

## Reason

The pre-execution audit found four details that the original protocol did not
specify sufficiently for a fail-closed runner and analyzer.

1. The source-pinned DANCeRS executables require a provenance `map_sha256`,
   even when no obstacle map exists.
2. The protocol both requires complete attempts and retains crashes/timeouts,
   but did not distinguish a complete terminal receipt from a successful
   fixed-ceiling trajectory.
3. The two ORCA positions summaries necessarily contain different attempt
   sequence and repetition fields. Their trajectory bytes can be identical;
   their complete summary files cannot.
4. The restricted-completion reduction, open-mission bootstrap target, and
   smoothness-state projection require exact machine rules.

All corrections below were fixed without observing a publication trajectory,
completion, contact, resource measurement, or method comparison.

## Frozen DANCeRS runtime reuse

The external study reuses the four already-built, source-pinned DANCeRS
products authenticated by committed build manifest SHA-256
`3c9c4305dae5e5154963ec76bb52e0bb826d6fb5b44a89503235057ad449b3cd`:

| method | role | binary SHA-256 |
|---|---|---|
| reference | timing | `03fd120158382014201d4b3f19db536c65eab7d88a5d5edd1ecd5c13312e5472` |
| reference | fidelity | `26afb1f7e6dd915381333fa47b9f0d8bd7ac2a1b97d4446e9e5788e64fb85a1e` |
| optimized | timing | `4b904a0a61d7b3c46dcb4c824b49429e457b9831c4bc9198601ee99cc75733ee` |
| optimized | fidelity | `97c5f869a17049d87351728829cbba30ecf120d2b3b8cda27872dfdcf545ef72` |

This is binary reuse, not result reuse. The endpoint commits/trees and common
patch are the protocol's unchanged endpoints. No old trajectory, summary, or
resource observation enters this study.

Each DANCeRS process has a new isolated working directory containing a
regular-file, byte-exact copy at
`comparison/publication/dancers-orca-v1/`. The command is exactly:

```text
<role-specific absolute binary> --cfg comparison/publication/dancers-orca-v1/<config_relpath>
```

The frozen endpoint resolves an existing relative config and its embedded
`SCENARIO_FILE` from this working directory before its compiled project-root
fallback. The runner must authenticate the copied 50-file input snapshot before
and after every process. Symlinks, missing files, extra files, path overrides,
and mutation of either the registered input or attempt copy are inadmissible.

For the observer's required `map_sha256`, the absence of a static map is
represented by the SHA-256 of the zero-byte string:

```text
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

This is a domain-specific no-map provenance sentinel, not the identity of an
obstacle file. The runner statically verifies the same byte-exact no-obstacle
config on every DANCeRS row; every successful fidelity summary must also report
`obstacles_present=false` and an empty obstacle path.

## Attempt integrity, outcomes, and interruption

"Complete attempt" means that each scheduled sequence has exactly one
authenticated terminal record with command, environment, start time, wait4
resource record, termination classification, and identities for every artifact
that was actually produced. It does not mean that a failed child fabricated a
trajectory or summary.

"Successful fixed-work output" is narrower: the child exited zero, did not
time out, produced every method/run-class-required artifact, and reached the
scheduled native step ceiling with the scheduled robot count. Fidelity and
ORCA wrapper outputs must additionally prove finite native state. An
observer-disabled DANCeRS timing/warm-up output instead proves finite wait4
measurements and the scheduled terminal step/count while producing no observer
artifact; its interpretation remains gated by that scene's fidelity bundle.

- Contact, tangency, ordinary noncompletion, and terminal standstill in a
  successful fixed-work trajectory remain behavioral outcomes.
- A natural nonzero exit, signal death, or 300 s hard timeout receives a
  terminal record and execution continues with the next scheduled sequence. It
  is never retried.
- Exit zero with a missing, malformed, non-finite, wrong-count, or non-ceiling
  required output is an `artifact_failure`. It also receives a terminal record
  and execution continues. The diagnostic is frozen in that record; it cannot
  select a rerun.
- A failed designated positions attempt has attempt-level
  `all_agents_completion=0` and is separately labelled as a process failure.
  Continuous trajectory metrics are unavailable, not imputed. A timing or
  warm-up failure never recodes a behavioral completion.
- A user/host interruption or an authentication/integrity change stops future
  execution. A started or ambiguous attempt is never rerun within that session.
  A new session, if authorized later, must begin the complete 363-row schedule
  again; no partial session can be pooled with it.
- No fidelity, repeatability, contact, or performance result is evaluated in a
  way that changes the remaining schedule. All outcome gates are applied only
  after the fixed schedule has terminated.

If any registered open mission lacks a required paired metric, including the
paired completion value, all scene rows and failures are published, but the
corresponding twenty-scene open aggregate is
`not_estimable_due_to_registered_failure`. Complete-case deletion and a
bootstrap of the remaining missions are forbidden. A failed DANCeRS fidelity
attempt blocks the external behavioral interpretation and the causal resource
claim for that scene. A failed timing repetition blocks that scene's
three-repetition resource reduction; the other two repetitions are not used as
a replacement median. If any of the twenty open scene resource ratios is
unavailable, the twenty-scene resource estimate, interval, and improvement gate
are all not estimable; a nineteen-scene or smaller replacement is forbidden.

All derived binary64 values must also be finite. If subtraction or positive
division of otherwise admissible finite inputs overflows, or if positive
division underflows to zero, the affected difference, ratio, or throughput is
unavailable; it is never clipped to a finite bound and infinity is never
reported. An open metric affected in any mission is
`not_estimable_due_to_numeric_range`, or
`not_estimable_due_to_registered_failure_and_numeric_range` when both causes
occur, with no complete-case replacement. A circle sensitivity row lists the
affected metric under `numeric_range_unavailable_metrics` and records its
difference as null. A non-representable DANCeRS resource ratio sets the whole
scene ratio map to null and uses `not_estimable_due_to_numeric_range`; a
non-representable ORCA agent-step throughput uses
`throughput_not_estimable_due_to_numeric_range` while retaining its finite
absolute medians. These are numerical-range diagnostics, not process-failure
or retry labels.

The confirmatory label is `noncompletion_by_fixed_ceiling`. No separate
confirmatory "deadlock" subtype is inferred from an unregistered velocity
threshold or terminal-window length. Any later standstill/deadlock diagnostic
must be labelled exploratory unless separately frozen before execution.

## ORCA repeatability gate

For each main-cadence ORCA positions pair and each 0.05 s sensitivity pair:

1. the complete trace files must be byte-identical; and
2. the parsed summaries must be exactly equal after removing only
   `input.attempt_sequence` and `input.repetition`.

No other summary field is ignored. In particular, source, input, scenario,
cadence, cohort, parameter audit, completion latches, step count, final native
time, trace identity, and finite-state fields must agree. The first trace is
canonical only after this gate passes. "Two byte-identical runs" in the base
protocol therefore means byte-identical behavioral traces plus the exact
attempt-normalized summary equality above, not byte-identical summary files.

The scene-level ORCA behavioral bundle consists only of its two registered
positions attempts at the relevant cadence. Both must be successful fixed-work
outputs and pass the repeatability gate before the first trace supplies any
scene-level ORCA metric. If either fails or the gate disagrees, both attempt
outcomes remain visible but the ORCA scene bundle and every paired scene metric
are not estimable. The surviving duplicate can never be substituted. ORCA
timing and warm-up outcomes do not enter this bundle.

## DANCeRS fidelity and canonical-trace gate

For every scene, the reference/optimized digest pair and the
reference/optimized positions pair must each pass normalized semantic equality
over the complete `2H` DANCeRS trace frames. Before equality comparison, trace
header `metadata` is replaced by an empty object only after its provenance has
been authenticated against that attempt's registered metadata, and exactly the
three header paths `resolved_config.config_file`,
`resolved_config.scenario_file`, and `resolved_config.obstacle_file` are
replaced by their already authenticated registered identities. No other header,
frame, numeric, or planner field is normalized.

Within both pairs, and across all four summaries, the exact projection below
must be equal:

```text
termination_reason, all_goals_reached, goals_reached, goals_total,
first_all_goals_tick, iterations, total_path_length_m,
mean_path_length_m_per_robot, per_robot_goal_tick,
per_robot_path_length_m, obstacle_collision_events,
obstacle_collision_exposure_seconds, pair_collision_events,
pair_collision_exposure_seconds, minimum_obstacle_clearance_m,
minimum_pair_clearance_m, invalid_geometry_evaluations,
invalid_robot_frames, nonfinite_numeric_observations,
nonfinite_state_observations, paper_metrics, final_tick,
final_robot_count, maximum_robot_count, run_valid
```

All four attempts must also be successful fixed-work outputs. Only after the
entire gate passes is the **reference positions trace** the canonical DANCeRS
behavioral trace. A failed attempt or semantic/summary mismatch leaves every
attempt outcome visible but makes the DANCeRS scene bundle and every paired
scene metric, including paired completion, not estimable. No optimized trace,
digest trace, or surviving attempt may replace it. DANCeRS timing and warm-up
outcomes do not enter the behavioral bundle.

## Frozen common-grid reductions

The common trajectory contains integer ticks `0..H` at nominal `dt=0.1 s`.
For DANCeRS it is `pre_update@0` followed by `post_update@1..H`; duplicate
next-step `pre_update` frames are excluded. DANCeRS translational velocity is
the registered six-state vector's zero-based components `state[3]` and
`state[4]`. ORCA translational velocity is `executed_velocity`. Every recorded
trajectory position or velocity component, and every recorded ORCA `goal` or
`radius` value, must agree with its paired binary64 or binary32 hexadecimal
identity before use; DANCeRS `final_goal` and `radius_m`, which have no paired
hexadecimal fields, are outside this hex check.

The frozen scenario JSON values, parsed as binary64, are the common start and
goal centres for both methods' goal discs and straight-distance baselines.
ORCA's recorded starts and goals must equal the binary32 cast of those values,
but the rounded copies are not the common metric centres. Native DANCeRS goal
ticks and ORCA goal latches are integrity/context fields; neither substitutes
for continuous common-grid passage.

For each agent:

- continuous first passage is the earliest closed line-segment intersection
  with the closed 1 m goal disc; contact exactly at the ceiling counts;
- restricted passage time is the first-passage time when observed and the
  fixed scene ceiling otherwise;
- path length sums centre displacement across all common-grid intervals;
- excess path is signed path length minus start-centre-to-goal-centre distance,
  without clamping;
- velocity total variation uses `sum(||v[k]-v[k-1]||)` for `k=1..H`;
- acceleration RMS uses the `H` vectors
  `(v[k]-v[k-1])/0.1`; and
- jerk RMS uses the `H-1` differences between successive acceleration vectors,
  divided by 0.1.

The scene agent-completion fraction, restricted-completion time, path length,
signed excess path, velocity total variation, acceleration RMS, and jerk RMS
are the arithmetic means of the corresponding per-agent values. Total path and
excess are also published descriptively but do not replace the registered mean
paired metrics. All-agent makespan is reported only when every agent has a
passage event. Its twenty-mission paired aggregate is estimable only when all
twenty paired makespans exist; otherwise it is marked not estimable rather than
formed from the complete subset.

For each unordered robot pair, closed contact intervals from the analytic
relative swept segment are translated to nominal time and touching intervals
for that pair are merged. Tangency has zero exposure but counts as one episode.
Robot-pair-seconds is the sum of merged interval durations over pairs. The
unsafe boundary is exactly clearance `<=0`, without an added tolerance.

## Frozen open-mission statistics

Each behavioral paired difference is:

```text
canonical DANCeRS scene value - main-cadence ORCA scene value
```

The completion difference uses `0/1` and is stored in fraction units
`-1/0/1`; multiply by 100 only when the reported unit is explicitly labelled
percentage points. For each estimable metric, publish all twenty mission differences,
their arithmetic mean, their median (the arithmetic mean of sorted zero-based
entries 9 and 10), and one deterministic 100,000-draw percentile interval for
the mean paired difference. The registered behavioral
metric names are:

```text
all_agents_completion
agent_completion_fraction
all_agents_makespan_s
mean_restricted_agent_completion_time_s
mean_path_length_m
mean_path_excess_m
minimum_pair_clearance_m
pair_contact_episodes
pair_contact_exposure_pair_seconds
mean_velocity_total_variation_mps
mean_acceleration_rms_mps2
mean_jerk_rms_mps3
```

The registered resource metric names are `dancers_wall_speedup`,
`dancers_cpu_speedup`, and `dancers_rss_optimized_over_reference`.

Sampling uses no language-runtime pseudorandom generator. For metric name `m`,
draw `d` in `0..99999`, and draw position `j` in `0..19`, the selected mission
index is:

```text
uint64be(SHA256(
  ASCII("dancers-orca-open-bootstrap-v1") || 0x00 || UTF8(m) || 0x00 ||
  uint64be(d) || uint32be(j)
)[0:8]) mod 20
```

Mission-array index `0..19` maps to frozen open mission labels `31..50` in
strictly ascending order; index 0 is mission 31 and index 19 is mission 50.

Every twenty-mission arithmetic mean, including each bootstrap draw, is
evaluated in binary64 as `fsum(value_i / 20 for i=0..19)`. The reported
twenty-mission median is `fsum(x[9] / 2, x[10] / 2)` after sorting. These
scaled sums preserve the registered arithmetic estimands without overflowing
on a mathematically finite mean.

For `n=100000` sorted draw statistics and probability `p`, set
`h=(n-1)*p`, `lo=floor(h)`, `hi=ceil(h)`, and `w=h-lo`; the quantile is the
finite convex sum `fsum((1-w)*x[lo], w*x[hi])`. The interval uses `p=0.025`
and `0.975`. This is fixed-input/fixed-open-world resampling sensitivity, not
population inference. Resource draws apply the same selected indices to scene
log ratios, average the twenty logs with the same scaled-`fsum` rule, and
exponentiate.

Reference-to-optimized DANCeRS wall and CPU ratios remain
`reference/optimized`; RSS remains `optimized/reference`. Three successful
fixed-work repetitions first reduce to a within-scene median. Resource
summaries for a method and scene are admissible only when every one of those
three repetitions has strictly positive `total_cpu_seconds` and
`peak_rss_mib`; otherwise all wall, CPU, and RSS medians for that method and
scene, and every downstream ratio or ORCA context that depends on them, are
unavailable. The twenty open scene ratios use `exp(mean(log(ratio)))`, with the
same mission bootstrap on mean log ratio followed by exponentiation. The
existing improvement and memory gates are unchanged.

Circle counts remain separate descriptive rows with no bootstrap, p-value,
pooled trend, or superiority score. ORCA 0.05 s sensitivity is
`sensitivity minus main` for behavioral metrics only. It has no resource
ranking. ORCA resource context contains absolute within-scene medians and
`robot_count * native_steps / total_cpu_seconds`; no DANCeRS/ORCA speedup ratio
is permitted.

## Authorization boundary

The execution-plan capsule created under this amendment must remain
`execution_authorized=false`. It binds the exact runner, analyzer, protocol,
input, schedule, ORCA capsule/binary, DANCeRS build manifest/binaries, 363-row
plan, exact `libomp` runtime library, environment, result schema, and analysis
rules. The runner reauthenticates `libomp` immediately before and after every
DANCeRS child process.

Confirmatory execution requires a later, separate, create-only authorization
artifact and independent pre-execution audit. The authorization must bind one
capsule digest, one complete session ID, one absolute output root, and the
current host/runtime identities. It may expose no method, sequence, timeout,
cadence, binary, parameter, retry, or result-selection override.
