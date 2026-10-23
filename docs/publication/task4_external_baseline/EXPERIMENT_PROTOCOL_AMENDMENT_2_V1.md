# DANCeRS–ORCA protocol amendment 2 v1

Status: prospective, after audit of the first uncommitted input tree and before
the publication input freeze or any confirmatory DANCeRS–ORCA outcome.

Date: 2026-08-25.

This amendment applies to the immutable protocol committed at `a5340df7b` and
its first amendment:

- V1 protocol Git blob: `cbf06b0919f5a653195cb5373094434a055daa8c`
- V1 protocol SHA-256:
  `04bab9f2da0de290346104e5db3d8881df85a2429d1f9964c4e86943ac7350ff`
- amendment 1 Git blob: `c77d74383297f5a02fb4b3e592dd274e9339749e`
- amendment 1 SHA-256:
  `88b7465b61fa565827939a9ffc1b5c9c3c0a681e28af985c91b2b5076c4215b8`

The V1 protocol and amendment 1 remain byte-for-byte unchanged. This amendment
clarifies the already registered requirement that method order be balanced.

## Reason

Independent review of the first uncommitted generated tree found that hashing
each warm-up cohort separately produced an unbalanced ordering by chance.
Optimized DANCeRS occupied position 0 in all four warm-up cohorts; reference
DANCeRS occupied positions 1, 1, 1, and 2; and ORCA occupied positions 2, 2, 2,
and 1. The measured timing rows were already exactly position-balanced.

The rejected input manifest was 60,719 bytes with SHA-256
`27722cf70eebe6e6438daa1cbb517af3ace4f5db9028bdbc2b0b02527058bfd6`.
It was never committed or supplied to either planner. The defect was discovered
from schedule metadata alone; no behavioral or resource result informed this
correction.

## Prospective correction

1. The four warm-up strata remain open N=8 and circle N=8, 20, and 50. Each
   method still receives exactly one untimed warm-up per stratum, for 12
   warm-up processes and 363 total processes.
2. A domain-separated SHA-256 rule determines one base method order and a
   ranking of the four strata. Cyclic rotations of the base order are assigned
   across that ranking.
3. Because four cohorts cannot divide equally among three positions, maximal
   balance is required: each method must occupy every position at least once,
   with per-position occurrence counts `[1, 1, 2]` after sorting.
4. The input compiler and its tests must enforce this invariant before writing
   a publication manifest. A new create-only input tree must then be generated,
   independently reproduced byte-for-byte, audited, and committed.

No scene, method, measured attempt, metric, cadence, ceiling, timeout, claim
rule, or process count is changed. Only the order of untimed warm-ups is
corrected to satisfy the existing balance requirement.
