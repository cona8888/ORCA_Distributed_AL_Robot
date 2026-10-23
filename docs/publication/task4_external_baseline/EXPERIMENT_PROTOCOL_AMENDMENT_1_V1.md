# DANCeRS–ORCA protocol amendment 1 v1

Status: prospective, before generation of the publication input tree and
before any DANCeRS-versus-ORCA outcome.

Date: 2026-08-25.

This amendment applies to the immutable protocol committed at `a5340df7b`:

- Git blob: `cbf06b0919f5a653195cb5373094434a055daa8c`
- file SHA-256: `04bab9f2da0de290346104e5db3d8881df85a2429d1f9964c4e86943ac7350ff`

The V1 protocol file remains byte-for-byte unchanged. This amendment is
authoritative wherever the corrections below conflict with its wording.

## Reason

The implementation audit found that DANCeRS deletes a non-`trigrid` robot when
the absolute value of either coordinate exceeds `WORLD_SZ/2`. The original
protocol incorrectly described `WORLD_SZ=100` as only a coordinate/display
field. The audit also found that the current built-in DANCeRS circle generator
chooses a radius from `WORLD_SZ`, whereas this study explicitly froze a 20 m
radius.

## Prospective correction

1. Every derivative DANCeRS config in this external study uses
   `WORLD_SZ=400`. This is a deletion guard, not a collision obstacle.
2. The guard is analytically nonbinding. Open-scene coordinates cannot exceed
   64 m in absolute value (44 m observed input envelope plus 20 m maximum
   travel). Circle-scene coordinates cannot exceed 140 m in absolute value
   (20 m start radius plus 120 m maximum travel). Both are strictly below the
   200 m half-width.
3. The input compiler implements the protocol's explicit 20 m circle equations
   directly. It does not call or infer radius from the built-in generator.
4. The wrapper's goal state is a permanent latch: first sampled centre distance
   `<=1.0` m sets `goal_reached=true`, and preferred velocity remains exactly
   zero for all later native steps even if another agent displaces it.
5. The common trajectory grid is initial state plus each post-step state
   (`H+1` samples). For DANCeRS this means `pre_update@0` plus
   `post_update@1..H`; duplicate next-step `pre_update` records are excluded.
   The 0.05 s sensitivity emits native even steps only.

No scene, method, metric, schedule count, cadence, timeout, or claim rule is
changed. This amendment prevents unintended robot deletion and removes an
ambiguous implementation reference before any external result exists.
