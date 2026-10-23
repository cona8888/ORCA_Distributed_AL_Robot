# DANCeRS–ORCA protocol amendment 5 v1

Status: prospective, after the immutable execution-capsule and admission-gate
freezes and before any confirmatory DANCeRS–ORCA process or outcome.

Date: 2026-08-25.

This amendment freezes the registered execution-enabled bridge between the two
external decisions defined by Amendment 4 and the internal permit defined by
the runner. It changes no scientific input, command template, process order,
sample size, metric, statistical rule, failure rule, or claim boundary.

## Single launch surface

`scripts/replication/launch_orca_comparison.py` exposes one `launch` command.
It has no resume, retry, overwrite, or sequence-selection option. The caller
must supply:

- the frozen execution capsule and its external manifest SHA-256;
- the canonical absent output root and registered session identifier;
- canonical authorization and admission manifests;
- the independent expected SHA-256 for each decision manifest; and
- the exact case-sensitive confirmation `RUN-ALL-363-ONCE`.

A wrong or absent confirmation is rejected before capsule or decision
authentication. The confirmation is an accident-prevention interlock; it is
not authorization and cannot replace either externally trusted decision.

## Final admission and permit translation

The launcher authenticates the complete binding and both decision files twice.
Between the checks it creates no output path and starts no child process. The
two bindings must be exactly equal. It then reconstructs the input and runtime
paths only from the authenticated capsule and verifies the canonical runtime
contract digest against the admitted binding.

The binding also contains exact byte count, mode, absolute path, and SHA-256
records for Amendments 4 and 5 and for the admission-gate and
execution-launcher source files, plus the canonical digest of that source
contract. The later decisions therefore bind the exact protocol and
translation code reviewed at authorization time.

Only after these checks does it construct one internal `ExecutionPermit`. Each
permit field is copied from the authenticated binding or from the external
decision digests: study, plan, input, schedule, authorization, admission,
output root, process count 363, and `selective_retry_allowed=false`. The
launcher then calls the runner's fixed `execute_authorized` entry point once,
without a replaceable process runner.

The runner independently reauthenticates the plan, registered input, runtime
bytes, and absent output root before creating the result namespace. Natural
attempt failures follow Amendment 3; launcher-level admission or integrity
failure stops before the runner call and produces no partial result namespace.

## Prospective classification

The launcher source and its tests make execution technically possible only
when both still-absent decision files and all exact external digests are later
provided. This freeze does not create either decision, does not invoke the
launcher, and does not authorize a study. Its classification remains zero
publication processes and zero simulation steps. Creating the canonical
authorization/admission pair for a chosen permanent output location is a later
explicit decision gate.
