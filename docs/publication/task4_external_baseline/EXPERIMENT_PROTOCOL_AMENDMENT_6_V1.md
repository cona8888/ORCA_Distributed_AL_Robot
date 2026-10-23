# DANCeRS–ORCA protocol amendment 6 v1

Status: prospective, affirmative execution decisions recorded, before any
confirmatory DANCeRS–ORCA process or outcome.

Date: 2026-08-25.

Following the explicit instruction to proceed in the controlling author
workflow, this amendment fixes the first complete session as:

- session identifier `dancers-orca-v1-session-001`;
- output root `results/publication/dancers-orca-v1-session-001` within the
  repository at the absolute path recorded in the decision binding;
- complete registered inventory of 363 processes; and
- selective retry, resume, and overwrite forbidden.

The individual author/operator identity is not encoded in a self-declared JSON
field. Authorization is instead represented by the exact canonical decision
bytes and the SHA-256 trust roots reported outside those files. This avoids
presenting unauthenticated name text as identity evidence.

## Decision packet

The create-only decision packet contains `authorization.json`,
`admission.json`, `manifest.json`, and `manifest.sha256`. The external packet
manifest SHA-256 authenticates its complete inventory. The manifest records the
individual authorization/admission file digests needed by the frozen launcher.

Both decisions bind the registered capsule, plan, input, schedule, runtime,
host, result root, session identifier, Amendments 4–6, admission gate, and
execution launcher. The admission file additionally binds the authorization
file digest. Any changed source, protocol, runtime byte, host identity, output
state, decision byte, mode, missing file, or extra packet file fails closed.

## Authorization versus execution

This amendment and packet record affirmative authorization and admission, but
they do not execute the study. Packet creation imports neither the launcher nor
the runner, starts no subprocess, and creates no result namespace. The packet
classification is therefore `execution_authorized=true`,
`admission_granted=true`, `launcher_invoked=false`, zero publication processes,
and zero simulation steps.

Actual execution remains a separate final release action requiring the frozen
launcher and the exact case-sensitive confirmation `RUN-ALL-363-ONCE`.
