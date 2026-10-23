# DANCeRS external-baseline review acquisition plan v1

Status: prospective review plan, frozen before candidate searching.

Date: 2026-08-25. Reviewer: Codex, with final scientific decisions reserved
for the authors. Review type: focused scoping and implementation-feasibility
review, not a systematic effectiveness review or meta-analysis.

## Research question

Which published, open-source decentralised multi-robot navigation or collision-
avoidance method can provide scientifically fair external context for the
result-preserving DANCeRS implementation-optimization paper?

The primary paper question remains reference DANCeRS versus mathematically
identical optimized DANCeRS. An external method is not allowed to replace that
causal implementation baseline. It is eligible for executable comparison only
if a clearly labelled, shared task can be constructed without changing either
method's defining assumptions.

## Sources and access routes

Searches use open, primary-source routes available without institutional login:

1. the DANCeRS paper and its cited algorithm families;
2. publisher or archival paper pages, including IEEE, IJRR, RA-L, RSS, and
   arXiv author manuscripts;
3. official author/project pages and source repositories;
4. DOI/Crossref metadata for identity validation.

Web search is used only for discovery. Claims and extraction must be supported
by an original paper, official documentation, or the method's source
repository. Search-result snippets and third-party surveys are not evidence.

Frozen discovery queries:

```text
"decentralized multi-robot collision avoidance" open source code
"distributed multi-robot trajectory planning" open source code
"decentralized multi-agent motion planning" official implementation
DANCeRS distributed robot swarm path planning related work ORCA MADER
```

The review will also follow candidate names and primary citations found in the
DANCeRS paper. Search date is recorded as 2026-08-25. At least twelve unique
candidate studies are screened; at least six candidates with claimed public
implementations receive full source-and-assumption assessment. Searching stops
when the four frozen queries and the DANCeRS citation pass add no new eligible
method family.

## Inclusion criteria

A study enters title/abstract screening when it:

- addresses multi-robot or multi-agent motion, trajectory planning, navigation,
  or collision avoidance;
- operates in continuous 2D/3D space or has a defensible continuous execution
  interface;
- is decentralised or distributed, or is an important centralized contrast;
- has an archival paper or clearly versioned technical report; and
- reports enough assumptions to assess comparability with DANCeRS.

An executable baseline must additionally have:

- official source code with a redistributable licence and pin-able revision;
- a headless path that can be built and run on the available machine;
- configurable robot starts, goals, radii, speed, and fixed trial duration;
- machine-readable trajectories or a tractable observer insertion point;
- support for at least one shared, prospectively defined DANCeRS task without
  secretly adding a global planner, central coordinator, or learned policy;
- deterministic or explicitly seedable execution; and
- no requirement for unavailable robot hardware, proprietary assets, or a
  hosted service.

## Exclusion criteria

Exclude from executable comparison, while retaining a screening record, when:

- no official runnable implementation can be authenticated;
- the code licence forbids the intended reproducible artifact;
- the method requires a materially different robot model, sensing stack,
  global planner, assignment oracle, or communication model for every shared
  task;
- installation depends on obsolete or unavailable proprietary components;
- only aggregate videos or paper tables are reproducible; or
- a fair result would require reimplementing the method ourselves.

Centralized grid MAPF, learned navigation, aerial trajectory planning, and
reactive velocity-obstacle methods are not excluded by name. Their different
assumptions are extracted explicitly and may make them narrative comparators or
restricted-task baselines rather than full-suite competitors.

## Acquisition and output layout

Raw records are preserved under:

```text
literature/task4_external_baseline/raw/
```

Provenance, screening, and extraction are kept separately under:

```text
literature/task4_external_baseline/provenance/
literature/task4_external_baseline/processed/
docs/publication/task4_external_baseline/
```

For each included candidate, preserve the official metadata or paper file and
record byte count and SHA-256. Repository metadata is captured as a separate
raw record containing URL, queried revision, licence identity, and acquisition
time; source checkouts, if needed, remain outside the immutable paper-record
layer.

## Normalized fields and extraction schema

Each source record contains, when available:

- database/source, stable source ID, DOI/arXiv ID, title, authors, year,
  venue, abstract, language, document type, query batch, and raw-file SHA-256;
- method name and version; official paper/project/code URLs; repository
  revision and licence;
- centralized/decentralized architecture; communication and sensing model;
- robot dynamics, dimensionality, radius/shape, speed and acceleration model;
- goal/assignment assumptions; global-path input; static and dynamic obstacle
  support; deadlock/completeness claims;
- simulator and build dependencies; headless and seed support; trajectory
  output; supported operating systems;
- reported metrics and test environments;
- shared-task compatibility, required adaptations, scientific risks, and
  executable-baseline decision.

Reported facts, reviewer calculations, and reviewer inferences are labelled
separately. Quantitative values require a paper section, table, figure, or
official-code location.

## Deduplication and screening

Deduplicate first by DOI or arXiv ID, then by normalized title/year/first
author. Preserve every acquired raw record and map duplicates to one canonical
study. Screening decisions are `include`, `exclude`, or `unclear`, with one
controlled reason. A method may be included for narrative synthesis but
excluded from executable comparison.

The final synthesis will be a method/assumption table and feasibility decision,
not a pooled performance statistic. Single-reviewer screening, open-web source
coverage, and unavailable full texts will be stated as limitations.

## Decision gate

No external executable is downloaded, modified, or benchmarked until this plan
is committed and candidate screening identifies one preferred method plus one
fallback. The selection record must state the exact comparison question,
permitted claims, forbidden claims, and adaptations before any result is seen.
