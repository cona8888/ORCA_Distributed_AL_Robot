# DANCeRS–ORCA protocol amendment 9 v1

Status: prospective session-002 authorization and admission contract, before
any session-002 process or outcome.

Date: 2026-08-26.

## Registered session-002 execution unit

Following the controlling author instruction to continue, this amendment fixes
the next complete confirmatory execution unit as:

- session identifier `dancers-orca-v1-session-002`;
- output root `results/publication/dancers-orca-v1-session-002` within the
  repository at the absolute path recorded in the decision binding;
- execution capsule
  `comparison/publication/dancers-orca-v1-session-002-execution-capsule`;
- external capsule-manifest SHA-256
  `aa1d4229c115e3742c07328d09034d5e62f83be70b97c0d71284a01315cd0c91`;
- execution-plan SHA-256
  `8cc08dac76f74898b4c2593fabe99cb3d5ed797330ca37aceaf693538ba6230f`;
- complete registered inventory of 363 processes, in plan sequence 0 through
  362 exactly once; and
- selective retry, partial execution, reuse, resume, and overwrite forbidden.

The capsule is non-authorizing: its execution plan records
`execution_authorized=false` and `selective_retry_allowed=false`. This
amendment permits creation of the separate affirmative authorization and
admission documents only. It does not itself invoke the launcher or runner.

## Authenticated decision binding

The session-002 authorization and admission documents must bind the exact
capsule manifest and plan above; the authenticated input manifest and schedule;
the runtime and host contracts; the new absent result root; the session
identifier; Amendments 4–9; the admission gate; and the single complete-session
execution launcher. The admission document must additionally bind the exact
authorization-document SHA-256.

The decision packet must contain only `authorization.json`, `admission.json`,
`manifest.json`, and `manifest.sha256`. Its manifest must classify the packet
as affirmative authorization and admission with `launcher_invoked=false`, zero
publication processes, zero simulation steps, and an absent result namespace.
An externally recorded exact packet-manifest SHA-256 is the trust root; the
packet's own sidecar is consistency evidence only.

Any changed capsule, plan, input, schedule, runtime, host, source-contract byte,
decision byte, file mode, session identifier, output path or output state fails
closed. Missing or extra packet files also fail closed.

## Boundary from session 001

Session 001 remains sealed, failed, and not estimable under its frozen
contract. No session-001 process, trace, timing, metric, decision, or result is
part of session 002. No completed row may be reused or pooled, and the 106
failed rows may not be selectively retried. Session 002, if released, must run
all 363 rows anew under Amendments 7 and 8 without changing the registered
scenes, methods, parameters, metrics, statistical rules, failure policy, or
claim boundary.

## Authorization is not execution

Creating and checking the affirmative decision packet must import neither the
launcher nor the runner, start no subprocess, construct no runner permit,
create no result root, and execute no simulation. Actual execution remains a
separate release action through the frozen launcher, requiring authenticated
authorization and admission digests plus the exact case-sensitive confirmation
`RUN-ALL-363-ONCE`.
