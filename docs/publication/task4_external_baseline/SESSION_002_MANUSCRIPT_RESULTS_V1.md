# Manuscript-ready results: DANCeRS–ORCA session 002

Status: insertion-ready Results module; confirmatory fixed-input evidence.

Date: 2026-08-29.

Authoritative machine-readable sources:

- outcome-packet manifest SHA-256
  `3e8b137f9aef3a6f672f79ade6268dad967d57f2f402d656bdb702933babe0b3`;
- full-analysis SHA-256
  `ee7a0f445b5ef0efa55ccc0b06cd2a7a12afdf294dd0d4b8b22dd70cc134be51`;
- sealed-session SHA-256
  `072afe57dda6de3adc73deb8963657c709ee36113107f6c505d8e35c860bb92d`;
  and
- records-ledger SHA-256
  `8f27dacd554bc7cb905862c0eed7546cf9c156d00288a1e420a2c8efa35e6896`.

## Suggested manuscript placement

Place this subsection after the held-out planning evaluation and before the
Discussion. The reference-versus-optimized DANCeRS result is implementation
evidence. The native DANCeRS–ORCA comparison is restricted external context
and must not be presented as a causal runtime comparison or a universal
planner ranking.

## Copy-ready Results text

### Integrity and admissibility

The confirmatory external-baseline session completed all 363 prospectively
scheduled processes without a timeout, nonzero exit, artifact failure, retry,
or selective rerun. Exact reference-versus-optimized DANCeRS fidelity passed
for all 23 digest pairs and all 23 positions pairs. The two registered ORCA
repetitions in each cohort also passed byte-level repeatability and
attempt-normalized summary equality in all 26 main- and sensitivity-cadence
cohorts. Consequently, all 23 scene pairs and all 23 scene-level resource rows
were estimable under the registered failure and missingness rules. Session 001
remained sealed as a failed infrastructure execution and contributed no
process, trajectory, timing observation, or metric to this analysis.

### Result-preserving implementation efficiency

Across the 20 registered open missions, the optimized DANCeRS implementation
preserved the reference trajectory and summary semantics exactly while
reducing fixed-work execution cost. The geometric-mean reference/optimized
wall-time ratio was 1.3200 (registered 95% bootstrap interval 1.3125–1.3279),
and the corresponding total-CPU ratio was 1.3457 (1.3410–1.3504). Both point
estimates exceeded 1.05 and both lower bounds exceeded 1.00, satisfying the
preregistered improvement gates. The optimized/reference peak-RSS ratio was
1.0021 (1.0015–1.0026), so the measured memory change was a small increase
rather than a memory reduction; its upper bound remained far below the
preregistered 1.05 ceiling. These ratios compare only the source-pinned
reference and optimized DANCeRS implementations. They do not compare DANCeRS
runtime with ORCA because the two native methods perform different internal
work.

### Restricted open-world comparison with ORCA

Both native methods completed all agents in every one of the 20 fixed open
missions: the DANCeRS-minus-ORCA differences in all-agent completion and agent
completion fraction were exactly zero. The remaining measures showed a
trade-off rather than dominance. DANCeRS had a 0.677 s longer all-agent
makespan (0.661–0.701 s), a 0.646 s longer mean restricted completion time
(0.640–0.653 s), and a 0.725 m longer mean path (0.712–0.740 m). It also had
0.235 m/s higher mean velocity total variation (0.222–0.253 m/s), but lower
mean acceleration RMS by 1.988 m/s² (−1.996 to −1.977 m/s²) and lower mean
jerk RMS by 33.012 m/s³ (−33.188 to −32.756 m/s³).

DANCeRS recorded no pair-contact episode in the 20 open missions, whereas ORCA
recorded one episode with 0.000273 pair-seconds of exposure. The registered
mean differences were −0.050 episodes (−0.150 to 0.000) and
−1.37 × 10⁻⁵ pair-seconds (−4.10 × 10⁻⁵ to 0.00), respectively. The mean
minimum-clearance difference was 0.030 m (−0.103 to 0.160 m). Because these
intervals include or touch zero, and because inference is restricted to one
fixed set of missions, the contact observations do not establish a general
safety advantage.

### Circle stress cases and cadence sensitivity

The three circle cases were prospectively designated descriptive stress tests
and were not pooled. At the 60 s ceiling, DANCeRS completion fractions were
1.00, 1.00, and 0.74 for 8, 20, and 50 robots, respectively; the corresponding
main-cadence ORCA fractions were 0.00 in all three cases. Neither method was
contact-free across all circle sizes. DANCeRS recorded 5, 40, and 252 contact
episodes, while main-cadence ORCA recorded 0, 985, and 509.

The prospectively registered 0.05 s ORCA sensitivity cadence did not change
completion in any circle case. Relative to main-cadence ORCA, it changed the
contact count by 0, −985, and −208 episodes and contact exposure by 0,
−207.710, and −231.044 pair-seconds at 8, 20, and 50 robots. Other path and
smoothness differences were small at the displayed precision. This strong
contact sensitivity to ORCA's native update cadence reinforces the restricted,
parameter-conditional interpretation of the external baseline.

### Overall interpretation

The external study supports the implementation claim: the optimized DANCeRS
code preserves the registered reference behaviour while improving wall and
CPU performance with less than a 0.3% measured peak-RSS increase on this
workload. It does not support the statement that DANCeRS is universally better
than ORCA. On the fixed open missions the methods had equal completion,
DANCeRS used longer paths and completed later, and its smoothness measures
moved in different directions. The circle cases favoured DANCeRS completion
under the registered parameterization but exposed contacts in both methods and
substantial ORCA cadence sensitivity. These are fixed-input, native-model
comparisons, not population-wide or real-world guarantees.

## Table 1. Registered DANCeRS implementation-resource results

Ratios above one favour optimized DANCeRS for wall and CPU. RSS is
optimized/reference, for which the registered gate was an upper interval bound
no greater than 1.05.

| Metric | Geometric-mean ratio | Registered 95% interval | Gate |
|---|---:|---:|---|
| Wall speedup, reference/optimized | 1.3200 | 1.3125–1.3279 | Pass |
| Total-CPU speedup, reference/optimized | 1.3457 | 1.3410–1.3504 | Pass |
| Peak RSS, optimized/reference | 1.0021 | 1.0015–1.0026 | Pass; not a memory-reduction claim |

## Table 2. Registered open-mission behavioural differences

Every difference is canonical DANCeRS minus main-cadence ORCA. Intervals are
the preregistered 100,000-draw fixed-world bootstrap sensitivity intervals
over the same 20 missions; they are not population confidence intervals.

| Metric | Mean difference | Registered 95% interval | Reading |
|---|---:|---:|---|
| All-agent completion | 0.000 | 0.000–0.000 | Equal in all 20 missions |
| Agent completion fraction | 0.000 | 0.000–0.000 | Equal in all 20 missions |
| All-agent makespan (s) | +0.677 | +0.661 to +0.701 | DANCeRS later |
| Mean restricted completion time (s) | +0.646 | +0.640 to +0.653 | DANCeRS later |
| Mean path length (m) | +0.725 | +0.712 to +0.740 | DANCeRS longer |
| Mean path excess (m) | +0.725 | +0.712 to +0.740 | DANCeRS larger |
| Minimum pair clearance (m) | +0.030 | −0.103 to +0.160 | Direction unresolved |
| Pair-contact episodes | −0.050 | −0.150 to 0.000 | One ORCA episode in total; boundary includes zero |
| Contact exposure (pair-s) | −1.37 × 10⁻⁵ | −4.10 × 10⁻⁵ to 0.00 | Boundary includes zero |
| Mean velocity total variation (m/s) | +0.235 | +0.222 to +0.253 | DANCeRS higher |
| Mean acceleration RMS (m/s²) | −1.988 | −1.996 to −1.977 | DANCeRS lower |
| Mean jerk RMS (m/s³) | −33.012 | −33.188 to −32.756 | DANCeRS lower |

## Table 3. Circle stress-case outcomes

These rows are descriptive and must not be pooled into an overall superiority
score. Restricted completion time uses the 60 s ceiling for an agent that does
not complete.

| Robots | DANCeRS completion fraction | ORCA completion fraction | DANCeRS mean restricted time (s) | ORCA mean restricted time (s) | DANCeRS contacts | ORCA contacts |
|---:|---:|---:|---:|---:|---:|---:|
| 8 | 1.00 | 0.00 | 27.925 | 60.000 | 5 | 0 |
| 20 | 1.00 | 0.00 | 29.158 | 60.000 | 40 | 985 |
| 50 | 0.74 | 0.00 | 39.733 | 60.000 | 252 | 509 |

## Claim–evidence table

| Proposed manuscript claim | Evidence | Status and boundary |
|---|---|---|
| Optimized DANCeRS preserves reference behaviour on the external workload. | All 23 digest pairs, all 23 positions pairs, and all cross-detail summaries passed the exact registered gates. | Supported for the authenticated workload and source endpoints. |
| Optimized DANCeRS reduces fixed-work wall and CPU cost. | Wall ratio 1.3200 [1.3125, 1.3279]; CPU ratio 1.3457 [1.3410, 1.3504]. | Supported for reference versus optimized DANCeRS on the 20 fixed open missions. |
| The optimization reduces memory. | RSS ratio 1.0021 [1.0015, 1.0026]. | Not supported. The measured change is a small increase, although the ≤1.05 gate passed. |
| DANCeRS and ORCA completed the open missions equally. | Both completed all agents in all 20 missions; both registered completion differences were zero. | Supported for the fixed open missions only. |
| DANCeRS produces smoother motion than ORCA. | Acceleration and jerk RMS were lower, but velocity total variation was higher. | A blanket smoothness claim is not supported; report the three measures separately. |
| DANCeRS is safer than ORCA. | Open contacts were 0 versus 1, but contact and clearance intervals include or touch zero; both methods contacted in circle stress cases. | Not supported as a general claim. |
| DANCeRS is better than ORCA. | DANCeRS completed more agents in circle cases and had lower acceleration/jerk RMS, but open paths and completion times were larger, velocity variation was higher, and circle contacts occurred in both methods. | Prohibited as a universal ranking; report a parameter-conditional trade-off. |
| The results generalize to arbitrary environments, hardware, or real robots. | One authenticated fixed-input study on one host and native kinematic models. | Not supported. |

## Reporting constraints

- Do not translate a 1.3200× speedup into “32% less time”; the corresponding
  time reduction is a different quantity.
- Do not call the RSS result a reduction or an equality result.
- Do not combine acceleration RMS, jerk RMS, and velocity total variation into
  an unregistered smoothness score.
- Do not convert circle rows into a pooled ranking or infer population effects
  from the fixed-world bootstrap intervals.
- Do not compare native DANCeRS and native ORCA wall or CPU time as equal work.
- Retain session 001 as a separately labelled failed infrastructure execution;
  do not hide, repair, or pool it with session 002.
