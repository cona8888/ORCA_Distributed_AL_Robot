# DANCeRS–ORCA protocol amendment 7 v1

Status: post-session-001 infrastructure correction; prospective for any
session 002 execution.

Date: 2026-08-25.

## Outcome and reason

Session 001 remains a sealed failed confirmatory execution. It completed all
363 registered processes, with 257 `completed` outcomes and 106
`artifact_failure` outcomes. The session, records-ledger, and plan SHA-256
values remain, respectively:

- `807619831db0c4cc2c2fc05598c6ac92f0ef2228b34c4008434c8ec4566cef`;
- `bcf2387e5c493c4324fc5119e54dcf07bc38bbcbf30516d224e7b481c80dd61e`;
  and
- `abea19e3e4ec5274aea80bebfd521444cc485e757731b522884bbaefcb77e7e7`.

All 106 failed child processes returned zero and none timed out. Inspection of
their immutable trace bytes showed that independent C++ serializers emitted
valid finite decimal spellings that differed from Python's reserialization,
although the spellings parsed to the same IEEE-754 values. For example,
`2.6011731222885772` and `2.601173122288577`, and
`-19.871109008789063` and `-19.871109008789062`, have the same relevant binary
value after parsing. The session-001 runner nevertheless required the original
line to equal Python's complete byte-for-byte reserialization.

The observed fact is the cross-serializer byte difference. The interpretation
is that the former trace lexical gate was language-dependent. This correction
does not interpret, compare, or recover planner outcomes from session 001.

## Portable trace JSON rule

For prospective trace admission, each record must still be one nonempty UTF-8
JSON object on one LF-terminated line. The parser must reject malformed JSON,
duplicate object keys, `NaN`, `Infinity`, `-Infinity`, and any value that
materializes as nonfinite. File, line, nesting, frame, and robot bounds remain
unchanged.

The line's nonnumeric syntax is canonical. Equivalently, replace every valid
JSON number token outside strings by the single token `0`; the resulting bytes
must equal Python JSON serialization with sorted object keys, compact
separators, ASCII escaping, finite values, and one final LF. This retains exact
object-key order, punctuation, strings, escapes, booleans, and null spelling,
while allowing any valid finite JSON decimal spelling.

No trace is rewritten. Its original bytes and SHA-256 identity remain the
authenticated artifact. Numeric meaning is then checked by the existing exact
schema and semantic gates. In particular:

- DANCeRS `state` components must still agree with all six registered binary64
  hexadecimal identities;
- ORCA binary32 position, velocity, goal, radius, timestep, and final-time
  values must still agree with their hexadecimal identities;
- unpaired numeric fields must remain finite and satisfy their exact schema,
  inventory, range, timing, fixed-work, and scenario constraints; and
- changed values, changed hexadecimal identities, missing or extra fields,
  reordered robot or frame inventories, and incomplete traces still fail.

The mask applies only to JSON number tokens outside strings. Digits inside a
string remain exact. This rule changes trace lexical admission only. Plans,
schedules, manifests, summaries, provenance files, attempt records, ledgers,
sessions, decisions, and capsule documents retain their existing full
byte-canonical JSON rules.

## Equality gates remain unchanged

Amendment 3's scientific equality gates are not relaxed. The two registered
ORCA repeat traces must still be byte-identical to each other, and their
attempt-normalized summaries must still be exactly equal. DANCeRS
reference/optimized fidelity remains complete normalized semantic equality
over all registered frames, with the same summary projection. Portable lexical
admission only prevents one language's decimal printer from being treated as
the universal serializer.

## Session-001 boundary

Session 001 must not be edited, reclassified, selectively rerun, resumed, or
pooled with a later session. Its 106 terminal `artifact_failure` records remain
failures, and the frozen analyzer's not-estimable results remain authoritative
for that session. Session-001 trajectories and timing may support labelled
infrastructure diagnosis only; they cannot support the confirmatory
DANCeRS–ORCA claims.

## Prospective session-002 gate

Before any session 002 process starts, the corrected comparison utility,
runner, analyzer, this amendment, regression tests, and a fresh execution
capsule must be frozen at one repository anchor. The new plan must bind those
exact bytes. A new session-002 authorization and admission pair must bind a new
absent output root and the complete 363-process inventory, with selective
retry, resume, and overwrite forbidden.

Session 002 must run all 363 registered rows again. It may not contain only the
106 formerly failed rows, reuse a session-001 completed row, or use any
session-001 scientific metric to choose a scene, method, parameter, threshold,
metric, or statistical rule. The scenes, methods, schedule, parameters,
metrics, failure policy, reductions, and claim boundary remain otherwise
unchanged. Only a separately frozen and fully authorized session 002 can
provide new confirmatory evidence.
