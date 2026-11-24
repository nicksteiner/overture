# overture

Run the OPERA DSWx-NI surface-water algorithm on full NISAR L2 GCOV frames by
overlapping windows, with one threshold field for the whole frame.

A NISAR frame is 0.3 to 1.2 billion pixels and the algorithm needs about five times
the image in memory. `overture` cuts the frame into windows of about 2 GB each, runs
the unmodified OPERA code on every window, and puts the window products back on the
frame grid. Where windows overlap it measures how often they disagree, which is the
chunking error of the run.

## Install

The OPERA algorithm, [`dswx_sar`](https://github.com/opera-adt/DSWX-SAR), must be
importable, with GDAL, rasterio and h5py. Then:

    pip install -e .          # or put src/ on PYTHONPATH and use `python -m overture`

## Use

    overture run --h5 wet=<event GCOV .h5> --h5 ref=<reference GCOV .h5> --out <run_dir>

The first `--h5` is the event date, the second the reference. The command plans the
windows, runs them, and reduces. It can be stopped and started again; finished work is
skipped. Useful options: `--window-km 96 --overlap-km 10` (use 48 and 6 for 10 m
frames), `--jobs 4` windows at a time, `--threads 8` per window, `--anc-cache DIR` for
the downloaded DEM, HAND, WorldCover and surface-water tiles, `--single-pass` to let
each window fit its own thresholds.

The steps are also available one at a time: `plan`, `process`, `reduce`, `status`.

## What a run does

1. **Plan.** Windows are placed on the frame's pixel grid; windows outside the swath
   are dropped. Each window is written as a GCOV-shaped HDF5 with the same pixels.
   One ancillary set is built for the frame and shared by all windows.
2. **Pass one.** Each window runs mosaic and filter and stops. The scratch directory
   is the complete state between stages, so stopping here costs nothing.
3. **Frame bound.** OPERA's dark-water bound is computed once over all windows.
4. **Threshold stage.** Each window fits its tile thresholds with the frame bound.
   The per-block thresholds are recorded, not interpolated.
5. **Frame field.** All windows' blocks are placed on the frame's block grid and
   interpolated once with OPERA's own function. The field is cropped into every
   window, so two windows see identical thresholds where they overlap.
6. **Pass two.** Each window resumes from the fuzzy stage through to MGRS tiles.
7. **Reduce.** Window tiles are mosaicked on the frame grid.

## Outputs, in the run directory

| file | content |
|---|---|
| `frame_WTR_<tag>.tif` | water classification on the frame grid (OPERA WTR classes) |
| `frame_OVERLAP_<tag>.tif` | number of windows that classified each pixel |
| `frame_DISAGREE_<tag>.tif` | pixels where two windows disagree on water |
| `frame_FLOOD_<wet>_vs_<ref>.tif` | 1 water on both dates, 2 flood, 3 water on the reference date only |
| `frame_report.json` | areas and the window disagreement per date, flood area |
| `w*/dswx_ni/<tag>/sas_output/` | the OPERA MGRS tiles of each window |
| `w*/dswx_ni/<tag>/state.json` | finished phases, seconds and peak memory per window |

## How it uses OPERA

`dswx_sar` is a dependency and is not modified. All contact with it is in
`overture/opera.py`: the stages are called through their `run(cfg)` functions, and
two functions of the threshold stage are replaced at run time, in the window's own
process, to inject the frame bound and to record the block thresholds.

## Limits

- Dual-pol or quad-pol GCOV with HH and HV on frequency A; mixed polarization modes
  are not handled.
- Windows are cut from granules on disk. Reading windows straight from the archive
  works in the prototype scripts (openSEPPO) and is not yet in this package.
- A frame with no tile containing both land and water has no threshold field and the
  run stops with a message.
- The frame dark-water bound is OPERA's statistic over the whole frame; it can be
  looser than a single window's.

## Tests

    pytest                    # unit tests, synthetic data, under a second
