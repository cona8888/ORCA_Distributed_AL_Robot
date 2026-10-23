# DANCeRS–ORCA session 001 execution audit

Status: sealed execution complete; confirmatory comparison not estimable.

Date: 2026-08-25.

## Outcome first

Session 001 cannot support a confirmatory claim that DANCeRS is better or worse
than ORCA. The session is structurally complete and authenticated, but the
frozen artifact gate classified every required positions trajectory bundle as
failed. Under the preregistered no-complete-case rule, all 12 open-world
behaviour metrics, all three circle descriptive comparisons, all three ORCA
cadence-sensitivity comparisons, and all three open-world DANCeRS resource
ratios are not estimable.

This is a technical validation failure, not evidence of poor planner
behaviour. Session 001 must remain immutable and visible as a failed
confirmatory execution.

## Authenticated execution facts

- Registered and completed process inventory: 363 of 363.
- Terminal outcomes: 257 `completed`; 106 `artifact_failure`.
- Nonzero exits: 0.
- Timeouts: 0.
- Failed child return codes: all 106 were zero.
- Failed scenes: all 23 registered scenes had at least one required failed
  trajectory artifact.
- Session SHA-256:
  `807619831db0c4cc2c2c2fc05598c6ac92f0ef2228b34c4008434c8ec4566cef`.
- Records-ledger SHA-256:
  `bcf2387e5c493c4324fc5119e54dcf07bc38bbcbf30516d224e7b481c80dd61e`.
- Plan SHA-256:
  `abea19e3e4ec5274aea80bebfd521444cc485e757731b522884bbaefcb77e7e7`.
- Frozen-analysis SHA-256:
  `4eaba2bfffa60e01dd3e14e787a020162a151dff740ed6c711272b5e8a4acf61`.
- Frozen analyzer result: `sealed_session_valid=true`.

The committed root ledger authenticates each terminal record; each terminal
record authenticates its attempt artifacts. The complete local immutable
result tree contains 1,693 files and occupies approximately 872 MiB. The raw
attempt tree is not silently reduced to successful rows.

## Failure distribution

All 106 failures have the same class: the trace parsed as strict finite JSON
but its original line bytes differed from Python's reserialization of the
parsed object.

| Method | Artifact failures |
|---|---:|
| DANCeRS reference | 27 |
| DANCeRS optimized | 27 |
| ORCA | 52 |

| Run class | Artifact failures |
|---|---:|
| positions fidelity | 92 |
| cadence sensitivity | 6 |
| digest fidelity | 8 |

Timing remained available at attempt level, but the registered behavioural
gate failed in every scene. The frozen analyzer therefore left the dependent
DANCeRS ratios null rather than publishing timing-only performance claims.

## Root cause evidence

The failure is caused by an over-specific cross-language decimal-text rule.
The C++ producers and Python validator emitted different finite decimal
spellings that round back to the same binary value. Examples from sealed trace
rows include:

| Producer bytes | Python reserialization |
|---|---|
| `2.6011731222885772` | `2.601173122288577` |
| `-19.871109008789063` | `-19.871109008789062` |
| `4.1986163208717417e-20` | `4.1986163208717416e-20` |
| `-0.7844561125634399` | `-0.78445611256344` |

The runner checked byte equality before its method-specific semantic trace
validation. Consequently a valid finite value with a different round-trip
decimal spelling received `artifact_failure`, even where the trace also carried
the registered binary32 or binary64 hexadecimal identity. The uniform
zero-return-code/no-timeout pattern across both independent producers supports
this serialization-contract diagnosis.

## Deeper prospective-gate audit

After defining the portable trace rule prospectively, a read-only diagnostic
application accepted all 106 formerly blocked traces: 27 optimized, 27
reference, and 52 ORCA. Applying the complete prospective success-output gate
then exposed a second check that the frozen byte gate had prevented the runner
from reaching. All 46 DANCeRS positions attempts failed exact tick-0 equality
with the scenario start and configured zero velocity. The 52 ORCA positions
attempts and eight DANCeRS digest attempts passed the complete prospective
gate at this stage.

The DANCeRS planning layer performs ten factor/variable initialization
iterations and reads the resulting belief into the displayed state before the
first `pre_update@0` capture. Across all 23 scenes, the largest recorded
tick-0 position residue was `5.084821452783217e-14 m` and the largest recorded
translational-velocity residue was `5.1648179512973754e-14 m/s`; final goals
remained exact. Amendment 8 therefore defines a prospective absolute integrity
bound tied to half the frozen input's finest `1e-9 m` coordinate quantum,
while retaining the observed state without rounding or clamping.

These diagnostics do not change a session-001 byte or terminal outcome. They
show that the serialization rule was the first blocking validator error and
that the exact tick-0 check was a second independent prospective blocker. A
final read-only application of both prospective corrections accepted the full
success-output contract for all 106 artifacts, with no remaining validation
failure; this is infrastructure evidence only, not a retrospective outcome
change.

## Claim boundary

Observed facts: the registered schedule ran to completion, the session sealed,
and the frozen analyzer applied its failure policy exactly.

Interpretation: the trace-byte canonicalization rule is not portable across
the C++ producers and Python validator.

Not supported: any confirmatory DANCeRS–ORCA behavioural, robustness,
smoothness, contact, completion, or resource-ratio comparison. Descriptive
timing medians and raw trajectories may be used only for infrastructure
diagnosis unless a separately labelled exploratory analysis is authorized.

## Required next gate

Do not relabel the 106 records, edit the sealed tree, selectively rerun failed
rows, or pool session 001 with another session. Instead:

1. define a language-independent trace lexical/semantic acceptance rule while
   keeping duplicate-key, finite-value, binary-identity, schema, framing,
   inventory, and fixed-work checks;
2. define the bounded DANCeRS tick-0 initialization-integrity rule without
   changing or clamping the observed trajectory;
3. add cross-language, boundary-positive, and tamper-negative fixtures;
4. freeze the corrected runner, analyzer, Amendments 7 and 8, capsule, and a
   new full-session authorization; and
5. run all 363 rows again in session 002.

Session 002, not a repaired interpretation of session 001, would provide the
next eligible confirmatory evidence.
