# Publication essentials and disk-space recovery

Prepared 23 October 2026. Source, figures, CSVs, compact analyses, schedules,
ledgers, protocols, and local Git history are retained in their existing paths.
The seven figure packages remain in `figures/publication/python_packages_v1/`.

## Raw evidence

`raw_archives/*.tar.zst` replace expanded, untracked `trace.ndjson` files.
Every member was read back and checked against its original SHA-256 before
the expanded original was removed. Matching `*.manifest.json` files list all
original paths, byte counts, member hashes and archive hashes.

To restore an archive from the project root (requires zstd and tar):

```sh
zstd -d -c publication_essentials/raw_archives/ARCHIVE.tar.zst | tar -xf -
```

The original paths are restored, enabling tools that expect raw files at
their registered locations. Plotting each packaged figure needs only its
local CSV/map inputs and does not require restoring raw traces. Raw archives
are local research evidence, not GitHub content.

## Rebuildable files

`cleanup_report.json` records removed build output and local environments.
Source-pinned Git worktrees inside `build/comparison/` are retained; their
generated nested build directories are not. Environments and binaries must
be rebuilt before running simulations again. `pixi.toml`, `pixi.lock`, build
scripts, dependency provenance and pinned source revisions remain.

## GitHub scope

The public companion repository deliberately excludes DANCeRS simulator
source, binaries, source patches, raw attempts, licensed papers and fonts.
Independent publication/analysis Python code and supporting publication data
may be backed up there. Restricted simulator source and its existing Git
history remain local; they must not be treated as remotely backed up merely
because the companion repository exists. No redistribution permission is
assumed by this cleanup.
