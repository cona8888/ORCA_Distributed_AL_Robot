# RVO2/ORCA code-feasibility report v1

Status: **pass**. No DANCeRS-versus-ORCA comparative outcome was generated.

Date: 2026-08-25. Host: Apple arm64, macOS 26.5.1 (build 25F80).

## Prospective ordering

The preferred baseline, fallback, comparison question, adaptations, and claim
limits were committed first in DANCeRS commit
`d0597c271210c055ba26c4bddb5c2d7138aa278a` at
`2026-08-25T04:53:02+01:00`. Only then was the official RVO2 source checked out
and built. This report contains artifact-feasibility observations only.

## Source authentication

- Repository: `https://github.com/snape/RVO2`
- Revision: `1c25b27258d191e26c2df83ff6e31abc8efbdaed`
- Git tree: `754114c13a345b1d228871be7e85683e31b3af5d`
- Source worktree after checkout/build: clean
- Root `LICENSE` SHA-256:
  `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`
- `src/RVOSimulator.h` SHA-256:
  `fb99d48cfe5988d188db469f93648bf3f16275a715962b5736ec8ce3fd8d5ba3`

The root licence is Apache License 2.0. Core sources and official examples carry
Apache-2.0 SPDX identifiers. Documentation embedded in `RVO.h` and the README
also contain Creative Commons notices for documentation sections. The planned
artifact will not redistribute the third-party checkout; it will record the
source URL/revision and retain notices for any later redistribution decision.

## Build and official tests

Toolchain:

- CMake/CTest 4.1.2
- Apple Clang 21.0.0 (`arm64-apple-darwin25.5.0`)
- one build job
- OpenMP disabled

Configuration and test command:

```text
cmake -S build/external-baselines/RVO2 \
  -B build/external-baselines/RVO2/_build \
  -DCMAKE_BUILD_TYPE=Release \
  -DENABLE_OPENMP=OFF \
  -DBUILD_DOCUMENTATION=OFF \
  -DBUILD_TESTING=ON \
  -DBUILD_EXAMPLES=ON
cmake --build build/external-baselines/RVO2/_build --parallel 1
ctest --test-dir build/external-baselines/RVO2/_build \
  --output-on-failure --parallel 1
```

The unmodified library and all three official examples built. `Blocks`,
`Circle`, and `Roadmap` all passed: 3/3 tests, zero failures, 2.51 s total test
time.

Build-product identities for this host:

- `libRVO.dylib` SHA-256:
  `79dbb510dd4c31a128fc4ac1fe3309857c9a9d1e7358ddffa6677e7ae4ff2df3`
- `Circle` SHA-256:
  `4bd8104f6840aa3064af3ed757a7d84de5078424edefcbf987de68698ec2ae60`

## Repeatability check

The official `Circle` executable was run twice serially with
`OMP_NUM_THREADS=1`. Each run produced 3,333 lines and 15,580,243 bytes. The
outputs were byte-identical, both with SHA-256:

```text
1a6ffec0821f7c1c16e2734074c2170175dcdfa871ecf790fbcb9d34af685170
```

This establishes repeatability for that fixed official example and build. It
does not prove cross-platform bitwise determinism. The prospective experiment
will keep OpenMP disabled and require the same two-run identity smoke test for
its wrapper before admission.

## API feasibility

The pinned public API directly exposes every required baseline control without
modifying the solver:

- `setTimeStep` for decision cadence;
- `setAgentDefaults` or explicit `addAgent` for neighbor distance, neighbor cap,
  agent/obstacle horizons, radius, maximum speed, position, and velocity;
- `setAgentPrefVelocity` for the declared direct-goal adapter;
- `doStep` for headless stepwise simulation; and
- `getAgentPosition`, `getAgentVelocity`, `getGlobalTime`, and `getNumAgents`
  for trajectory observation.

The official `Circle` example already uses the same basic pattern: explicit
antipodal goals, direct preferred velocities, a fixed step, position output,
and termination inside one radius of every goal.

## Gate decision

All five frozen feasibility conditions pass:

1. exact revision and licence authenticated;
2. unmodified headless library and examples built;
3. the fixed official example repeated byte-for-byte with serial execution;
4. all required inputs are available through the documented API; and
5. positions and velocities are observable without solver changes.

RVO2/ORCA is therefore admitted as the restricted external baseline. The
Robotarium fallback is retained but will not be executed unless a later,
prospectively documented implementation blocker invalidates this pass.
