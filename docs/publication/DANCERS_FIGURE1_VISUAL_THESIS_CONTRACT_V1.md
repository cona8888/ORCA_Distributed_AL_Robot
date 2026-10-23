# Figure 1 contract — visual thesis

Status: **approved story; wireframe authorized**

## Claim

The optimized implementation performs less computational work while preserving
the registered DANCeRS planner and its observable behaviour exactly on paired
evidence.

## Audience and venue

- Broad robotics readers, multi-robot planning specialists, and systems
  reviewers.
- npj Robotics-quality main-paper figure.
- Original DANCeRS Figure 1 supplies the narrative role and scientific visual
  identity; no published artwork will be copied or traced.

## Final size

- 183 mm double-column width.
- Target height: 80–95 mm after wireframe reduction testing.
- Arial or Helvetica, 5–7 pt normal text, 8 pt bold lower-case panel labels,
  and 0.25–1 pt strokes at final size.

## Authoritative visual asset

- Environment family: perfect maze.
- Robot count: 6.
- Seed: 11.
- Replay: `results/comparison/demo/held-out-safe-grid-v1/perfect_maze-n6-seed11.html`.
- Map: `comparison/environments/perfect_maze/validation/map.png`.
- Plotted state source and hashes: `figures/publication/fig04_trajectories/fig04_trajectory_data.json`.
- Scientific status: registered held-out, task-valid, and asserted exact between
  reference and optimized state sequences before drawing.

Authenticated rasters and trajectories are measured/simulated outputs, not
illustrative decoration. They must not be smoothed, offset, resynthesized, or
generatively altered.

## Reading path

`one fixed mission` → `reference and optimized executions` → `identical
registered trajectory/state` → `five implementation-only changes` → `resource
evidence admitted after fidelity`.

## Panel map

- **a — Fixed paired mission:** dominant perfect-maze plan view with the same
  initial robot states and map supplied to both builds. Use the authenticated
  raster, a 20 m scale bar, and simple top-view robot footprints.
- **b — Same observable result:** synchronized reference and optimized paths
  shown as a matched pair or exact overlay. Use geometry plus direct equality
  annotation; do not offset one trajectory for visibility.
- **c — Less implementation work:** a quiet horizontal strip identifies the
  five changes at sensing and GBP surfaces and routes paired evidence through
  exact fidelity before wall, CPU, and RSS admission. No performance magnitude
  is encoded in this overview.

## Scientific objects and relationships

- Grey map raster: occupied cells; white: free space.
- Open robot footprint: initial state.
- Filled robot footprint with heading tick: final state.
- Neutral dashed path: reference.
- Solid blue path: optimized, using the same coordinates.
- Equality bracket or direct label: exact paired observable state; not
  mathematical identity outside the registered evidence.
- Dashed purple connector: GBP/message information only.
- Solid blue connector: robot motion or time progression only.
- Thin leader without arrowhead: label association only.
- Vermilion: failed admission or boundary only; it should be absent from the
  safe perfect-maze scene.

## Stable colour and symbol system

- Ink `#202124`: labels, axes, factor outlines.
- Neutral `#A7ADB2`: reference/context.
- Light neutral `#E8ECEF`: map or supporting zones.
- Blue `#2F6F9F`: optimized implementation and physical path.
- Purple `#7A6FA8`: GBP or information flow.
- Vermilion `#C94C4C`: failed evidence gate or retained contact boundary.
- Variables: open circles; factors: filled squares; robots: top-view footprint
  plus heading.

## Required in-figure text

- `same mission input`
- `reference DANCeRS`
- `optimized DANCeRS`
- `exact registered state and trajectory`
- `implementation only`
- `fidelity first`
- `wall · CPU · RSS`

All detailed conditions, the five full optimization names, and scope limits
belong in the caption unless reduction testing shows they remain necessary in
the graphic.

## Prohibited implications

- New planner or changed GBP schedule.
- Safer, shorter, or higher-quality path.
- Universal exactness beyond the authenticated paired studies.
- A performance magnitude inferred from arrow length, area, or icon size.
- Any DANCeRS–ORCA comparison in Figure 1.

## Wireframe direction

Use a **narrative ribbon with one dominant measured scene**. The maze and robots
occupy roughly two-thirds of the width; a compact paired-execution/equality
sequence and implementation/evidence strip occupy the remaining third and
lower edge. This preserves the visually immediate role of the original DANCeRS
Figure 1 while avoiding a presentation-dashboard layout.

## Deliverables after wireframe approval

- Editable SVG master with physical dimensions, live text, and named groups.
- Native-shape PowerPoint master; only the authenticated map remains raster.
- PDF, 600 dpi PNG, and 600 dpi TIFF exports.
- Caption/legend, provenance manifest, validation report, five-gate review, and
  checksums.
