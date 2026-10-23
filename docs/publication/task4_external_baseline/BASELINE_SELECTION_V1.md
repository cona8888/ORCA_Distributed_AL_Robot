# DANCeRS external-baseline selection v1

Status: prospective method selection. Frozen before any candidate source
checkout, adaptation, build, or benchmark.

Date: 2026-08-25. This decision applies to the result-preserving DANCeRS
implementation-optimization paper. It does not redefine that paper as an
algorithm-comparison paper.

## Decision

The preferred external baseline is **Optimal Reciprocal Collision Avoidance
(ORCA)** using the official **RVO2** C++ implementation at commit
`1c25b27258d191e26c2df83ff6e31abc8efbdaed` (Apache-2.0).

The fallback is **Safety Barrier Certificates** using the official Robotarium
Python simulator at commit
`307269846b2761528586e9c3f47d0a8bec21692f` (MIT), subject to a separate
feasibility check. GCBF+ is retained only as a reserve because a fair result
would also depend on learned-controller training and checkpoint provenance.

The unoptimized DANCeRS implementation remains the only causal baseline for
the optimization claims. ORCA may add behavioral context; it cannot replace
reference-versus-optimized DANCeRS timing or equivalence evidence.

## Search and screen flow

The four prospectively frozen discovery queries and a DANCeRS citation pass
produced fourteen external candidate studies. All fourteen were screened, and
thirteen official, paper-linked, or representative external repository records
received source, revision, licence, dependency, and assumption assessment. The
DANCeRS target and its GBP
planning precursor were retained as context, giving sixteen paper records and
fourteen repository records in total.

The screening table is
`literature/task4_external_baseline/processed/screening_v1.csv`; the full code
assessment is `implementation_assessment_v1.csv`. Raw files are preserved
locally and represented by tracked SHA-256 manifests.

## Why ORCA is the cleanest external context

1. **It is an established, independently defined method.** ORCA selects a safe
   instantaneous velocity for each agent using a low-dimensional linear
   program. The official RVO2 library exposes agents, radii, maximum speeds,
   preferred velocities, static obstacles, and stepwise simulation through a
   small C++ API.
2. **Its artifact is unusually clean for reproduction.** The implementation is
   official, open source, pin-able, redistributable under Apache-2.0, headless,
   and does not require a learned checkpoint, proprietary solver, ROS stack,
   robot hardware, or hosted service.
3. **There is direct historical precedent.** The immediate DANCeRS algorithmic
   precursor, *Distributing Collaborative Multi-Robot Planning with Gaussian
   Belief Propagation*, selected ORCA prospectively for circle, polygon-obstacle,
   and junction experiments. It matched ORCA `neighbourDist` to GBP communication
   range and ORCA `timeHorizon` to the GBP planning horizon. This makes ORCA a
   scientifically traceable comparator rather than a post-result choice.
4. **A narrow task can be shared without rewriting either method.** In an open
   antipodal crossing, both methods can receive the same disc centres, goals,
   radii, maximum speed, and sampled duration. RVO2 needs only its documented
   preferred-velocity input; no global route planner or map conversion is
   introduced.

ORCA is not selected because it is expected to lose. It is selected because
the source of any observed difference can be explained and reproduced.

## Assumption boundary

The two methods do not solve identical problems:

| Property | DANCeRS/GBP planner | ORCA/RVO2 |
|---|---|---|
| Decision | forward-window state/trajectory optimization | instantaneous safe velocity |
| Coordination | explicit Gaussian-message exchange | no communication |
| Motion model | continuous planar state with dynamics/nonholonomic constraints in this artifact | 2D holonomic discs in the ORCA model |
| Neighbor input | messages within communication range | observed current position/velocity/radius within neighbor distance |
| Obstacles | raster distance field in this artifact | line segments; preferred velocity/global route supplied by caller |
| Guarantee boundary | implementation- and factor-dependent | local collision-free condition under ORCA's sensing and holonomic-model assumptions |

The ORCA paper explicitly notes that its simplified model ignores kinematics
and dynamics, and that clamping its velocity to such limits removes the strict
collision-avoidance guarantee. We will therefore not convert ORCA output into a
unicycle controller and then call the result ORCA. Native-model differences
will be reported, not hidden.

Static-obstacle maps are excluded from the first external experiment. RVO2 can
avoid line-segment obstacles, but progress through clutter depends on a
preferred velocity normally obtained from a global path planner. Converting the
DANCeRS raster maps to polygons and adding a route planner would introduce two
new algorithms and confound the baseline.

## Exact comparison question

> On prospectively fixed, deterministic, open-space antipodal goal-crossing
> tasks with identical initial disc centres, goal centres, physical radii,
> maximum translational speed, sampled duration, and stopping tolerance, what
> differences are observed between native optimized DANCeRS and native
> ORCA/RVO2 in task completion, inter-robot contact, distance travelled,
> makespan, and trajectory smoothness?

Before any ORCA comparison is interpreted, reference and optimized DANCeRS
must again match their authenticated semantic digests on every DANCeRS trial.
That check links the behavioral result to the already established
result-preserving optimization claim.

## Declared adaptations

Only a thin, auditable RVO2 experiment wrapper is permitted. It may:

- create agents from the frozen starts, radii, speeds, and initial velocities;
- set each preferred velocity directly toward that agent's goal, clipped at the
  common maximum speed and set to zero inside the stopping tolerance;
- set `neighborDist` from the DANCeRS communication radius and `timeHorizon`
  from the DANCeRS planning horizon, following the precursor's matching rule;
- set `maxNeighbors` high enough not to truncate agents already inside
  `neighborDist`;
- use a common fixed simulation timestep if the feasibility gate confirms it
  is stable at the prospectively selected speed;
- force one OpenMP thread for the reproducibility run; and
- record every position and velocity without changing RVO2's solver.

No obstacle router, learned policy, central coordinator, smoothing pass,
unicycle projection, or collision-repair logic may be added.

## Permitted claims

If supported by the frozen experiment, the paper may state that:

- the optimized DANCeRS implementation preserved reference DANCeRS behavior on
  the added task;
- ORCA and DANCeRS exhibited different completion, contact, path-length,
  makespan, or smoothness behavior under the declared native models;
- forward planning and explicit trajectory negotiation are consistent with a
  measured difference, while clearly labelling that explanation as an
  inference; and
- the external result provides context consistent or inconsistent with the
  direct GBP precursor's published ORCA comparison.

## Forbidden claims

This experiment may not support statements that:

- DANCeRS is universally better, safer, faster, more scalable, or more complete
  than ORCA;
- cross-algorithm CPU or wall time isolates algorithm quality;
- a native ORCA trajectory satisfies DANCeRS's nonholonomic/dynamic model;
- open-space behavior predicts performance on the current raster obstacle suite;
- a failure or deadlock under one parameterization proves an inherent failure
  of the method; or
- the external baseline establishes the causal speedup created by the DANCeRS
  implementation optimization.

Cross-method runtime may be logged for diagnostics but is not a confirmatory
outcome and must not appear as a superiority claim.

## Code-feasibility gate

After this selection is committed, the exact RVO2 revision may be checked out
outside the immutable raw-paper layer. The gate passes only if:

1. the checkout resolves to the pinned SHA and contains the expected
   Apache-2.0 licence;
2. the unmodified library and its official examples build headlessly on the
   available machine;
3. a fixed-input example is repeatable with one OpenMP thread;
4. all required experiment inputs can be set through the documented API; and
5. positions and velocities can be observed without modifying the solver.

Failure sends the workflow to the frozen Robotarium fallback; it does not
permit trying candidates until one yields favorable results. Exact numerical
scenario levels and success thresholds will be frozen only after this
code-feasibility gate, and still before any comparative outcome is generated.

### Independent-audit correction

After the selection commit, but before any comparative ORCA outcome, an
independent source audit identified the R011 paper's direct link to
`Vrushabh27/mpc-cbf`. The repository was captured as C014 at commit
`62bdf68c0fc35254677e595f34fcf53c82872aca` (MIT). Its README documents a
single wheeled-robot MPC-CBF implementation, not the paper's decentralized
multi-robot social-mini-game method. The correction changes the provenance
count and exclusion reason but does not change the preferred or fallback
selection.

## Primary evidence

- R002: van den Berg et al., *Reciprocal n-body Collision Avoidance*, official
  ORCA paper.
- C002: official `snape/RVO2` repository and README at the pinned revision.
- R014: Patwardhan, Murai, and Davison, *Distributing Collaborative Multi-Robot
  Planning with Gaussian Belief Propagation*, especially Sections V-VI.
- R016/C013: safety-barrier-certificate paper and official Robotarium simulator.

## Limitations

This was a focused, single-reviewer, open-web scoping and implementation review,
not a systematic effectiveness review. Repository heads and licence metadata
were observed on 2026-08-25 and are pinned for reproducibility. Candidate
exclusion means “not suitable for this narrow executable role,” not “inferior
research.”
