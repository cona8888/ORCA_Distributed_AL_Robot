# DANCeRS–ORCA protocol amendment 8 v1

Status: post-session-001 infrastructure correction; prospective for any
session 002 execution.

Date: 2026-08-25.

## Outcome and reason

After applying Amendment 7's portable trace rule diagnostically to the sealed
session-001 artifacts, all 106 formerly blocked traces passed their complete
structural and method-specific trace gates. A deeper read-only application of
the prospective full-output gate then exposed a second, previously unreachable
check: all 46 DANCeRS positions attempts failed exact equality between their
recorded tick-0 belief state and the scenario start with zero velocity. The 52
ORCA positions attempts and eight DANCeRS digest attempts passed that deeper
gate.

This is not a session-001 reclassification. Its 106 `artifact_failure`
outcomes, not-estimable analysis, session bytes, and claim boundary remain
unchanged.

The DANCeRS source initializes each planning layer by performing ten factor and
variable iterations and then calling `updateDisplayStats` before the first
`pre_update@0` trace capture. The recorded state is therefore the initialized
belief, not a byte-exact copy of the configured waypoint and zero-velocity
vector. Across the immutable 46 traces, 952 position components, and 952
translational-velocity components, the largest absolute position residue was
`5.084821452783217e-14 m`, the largest absolute translational-velocity residue
was `5.1648179512973754e-14 m/s`, and every recorded final goal exactly matched
the registered scenario goal after strict parsing.

The observed fact is the small deterministic tick-0 residue. The source-linked
interpretation is planning-layer numerical initialization. No behavioural
metric from session 001 is used to change a scene, parameter, estimator, or
claim.

## Prospective initialization-integrity rule

The frozen scenario generator limits coordinates to at most nine decimal
places. For any prospective DANCeRS positions trace, the recorded tick-0
translational state must satisfy both absolute, zero-relative-tolerance checks:

- `abs(recorded_position[d] - registered_start[d]) <= 5e-10 m`; and
- `abs(recorded_velocity[d]) <= 5e-10 m/s`,

for dimensions `d=0,1`. The bound is half of the finest `1e-9 m` frozen input
coordinate quantum. It is fixed before session 002 and is approximately four
orders of magnitude larger than the largest diagnostic session-001 residue.

All six recorded tick-0 state components must still be finite and agree with
their binary64 hexadecimal identities. The registered start remains the exact
scenario value used for the straight-distance baseline. The recorded tick-0
position and velocity remain the trajectory values used in path and smoothness
reductions; they are not rounded, replaced, shifted, or clamped. A component
outside either bound is an artifact failure. No tolerance is added to later
frames, paired hexadecimal identity, goal identity, frame equality, summary
equality, fixed work, or any other integrity gate.

## Scientific and equality boundaries

The methods still receive the same authenticated scenario bytes and configured
zero initial velocity. ORCA retains its exact binary32-cast checks. DANCeRS
reference/optimized fidelity still requires complete normalized semantic trace
equality and the registered summary equality; the initialization bound cannot
make unequal traces equal. Amendment 7's portable lexical rule and Amendment
3's repeatability, missingness, reduction, and claim rules remain unchanged.

## Prospective session-002 gate

The corrected analyzer, its positive and boundary-negative tests, this
amendment, Amendment 7, the runner, comparison utility, and a fresh execution
capsule must be frozen at one repository anchor before session 002. A new
authorization/admission pair must bind that capsule, a new absent result root,
and all 363 registered rows. Selective retry, reuse, pooling, resume, and
overwrite remain forbidden.
