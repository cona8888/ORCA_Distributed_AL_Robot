# Pre-freeze ORCA wrapper diagnostic record v1

Date: 2026-08-25. Classification: implementation diagnostic, excluded from the
363-process publication execution and from every behavioral/resource estimand.

During read-only review of the uncommitted wrapper, a reviewer generated a
temporary input tree and invoked two schedule-addressed checks:

1. Attempt 2 (`orca-open-m31-n8`) failed in input validation before simulation
   and created no summary. The cause was that the draft wrapper incorrectly
   required inert DANCeRS `decisions` metadata to be zero. The frozen authority
   policy assigns ORCA semantics only to `num_robots` and numeric-ID-ordered
   waypoints, so the loader was corrected to require integer decision fields
   without interpreting their values.
2. Attempt 4 (`orca-circle-n8`) was inadvertently executed once with
   `phase=warmup`, `run_class=timing`, `cadence=main`, and repetition 0. No
   behavioral value was reported to the primary agent or used to change any
   scene, parameter, schedule, metric, claim, or implementation choice.

The second invocation was an untimed warm-up, not a measured confirmatory
attempt. Nevertheless it is explicitly quarantined because it occurred before
the wrapper/input freeze. The final publication runner must execute its complete
363-row schedule from a new result root; this temporary invocation cannot be
counted, copied, substituted, or selectively reused.

## Commands and immutable identities

Temporary compiler command:

```text
python3 scripts/replication/prepare_orca_inputs.py prepare --output-root /private/tmp/dancers-orca-audit-input-20260825
```

Open invocation that failed during pre-simulation validation:

```text
build/orca-wrapper-smoke/dancers-orca-wrapper run --input-root /private/tmp/dancers-orca-audit-input-20260825 --attempt-sequence 2 --summary-out /private/tmp/dancers-orca-audit-open-summary-20260825.json
```

Quarantined warm-up command:

```text
build/orca-wrapper-smoke/dancers-orca-wrapper run --input-root /private/tmp/dancers-orca-audit-input-20260825 --attempt-sequence 4 --summary-out /private/tmp/dancers-orca-audit-circle-summary-20260825.json
```

- temporary manifest: 60,487 bytes; SHA-256
  `31b9c74a5f69bb3c16d1efbfb6d2443ec72b9c6cfb86ca410b9f4373199a3134`
- uncommitted diagnostic binary: SHA-256
  `17fa28cf43298e321177e1fc0bd30c5b01b4f1c1db1157062fc965d034ef2ef3`
- quarantined summary: 10,782 bytes; SHA-256
  `1d8f5d60b5fd2d8403ed5a2f1c70318c3b2aee4807391bb86fbd6af5603bfbfc`
- open summary: absent, as expected for pre-simulation validation failure

Only identities and attempt metadata were retained here; the quarantined
summary's behavioral fields were not inspected for this record.

The corrective freeze also makes publication mode impossible in an unbound
smoke binary. A final executable must embed the exact new publication-manifest
SHA-256 at build time, and that new manifest authenticates this diagnostic
record. Consequently neither the temporary manifest nor the diagnostic binary
can be admitted by the final execution capsule.
