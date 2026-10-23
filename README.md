# DANCeRS/ORCA publication research companion

This repository preserves independent analysis, experiment-orchestration and
figure-generation Python code, publication planning, and compact plotted data.
It is a **source-excluded companion**, not a redistributed simulator checkout.

## Seven main figures

See `figures/publication/python_packages_v1/`. Each `fig01`–`fig07` folder has
Python code, CSV data, editable SVG, map inputs where required, a separate
caption and provenance records. Run `python draw.py` inside one figure folder
after installing its requirements. Arial must be installed separately.

The artwork is designed at 183 mm width with Arial text at least 14 pt and no
visible figure titles. Captions are separate. Figure results reflect the
registered simulator experiments; they are not physical-robot validation.

## Code and provenance

- `scripts/comparison/` and `scripts/replication/`: independent retained Python
  analysis, verification and orchestration tooling. Some tools require the
  separately licensed local simulator and original registered input capsules.
- `scripts/publication/`: lossless local disk compaction and companion backup
  inventory tooling.
- `docs/publication/`: the publication storyline, protocols and audit notes.
- Figure-local `data.csv` and `provenance.json`: compact plotting inputs and
  identities of the registered source results.

This is a preservation snapshot, not a complete standalone simulator release.
Not all simulation/analysis entry points run from this companion alone.

## Deliberate exclusions

DANCeRS simulator C/C++ source, source patches, third-party source, compiled
binaries, raw attempt trees, licensed paper copies and fonts are not uploaded.
Python files containing embedded C++ markers are conservatively excluded.
The full licensed simulator checkout, local Git history and excluded Python
files remain on the research computer. No permission to redistribute them is
assumed. DANCeRS was made available under licence from Imperial College London;
its authors and upstream project must be credited when reporting its results.

## Local evidence retention

Expanded raw traces were replaced locally with lossless `.tar.zst` archives
whose members were checked against their original SHA-256 values before
removing expanded copies. Publication evidence is retained, not discarded.
Those archives are **not backed up by this GitHub repository**. See
`publication_essentials/README.md` for restoration instructions.
