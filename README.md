# overture

Run the OPERA DSWx-NI surface-water algorithm on full NISAR L2 GCOV frames by
overlapping windows, with one threshold field for the whole frame.

A NISAR frame is 0.3 to 1.2 billion pixels and the algorithm needs about five times
the image in memory. Overture cuts the frame into windows of about 2 GB each, runs the
unmodified OPERA code on every window, and puts the window products back on the frame
grid. Where windows overlap it measures how often they disagree, which is the chunking
error of the run.

It sits between two open-source packages and installs both:
[OPERA DSWx-SAR](https://github.com/opera-adt/DSWX-SAR) is the algorithm, and
[openSEPPO](https://github.com/EarthBigData/openSEPPO) finds and downloads the data.

Contents: [Requirements](#requirements) · [Install](#install) · [Quick start](#quick-start) ·
[Step 1: Earthdata login](#step-1-earthdata-login) · [Step 2: find the granules](#step-2-find-the-granules) ·
[Step 3: download](#step-3-download) · [Step 4: run](#step-4-run) · [Step 5: outputs](#step-5-outputs) ·
[Options](#options) · [Stopping, resuming, failures](#stopping-resuming-failures) ·
[Time and memory](#time-and-memory) · [Docker](#docker) · [How it works](#how-it-works) ·
[Limits](#limits) · [Tests](#tests)

## Requirements

- Linux, x86-64.
- [pixi](https://pixi.sh) (`curl -fsSL https://pixi.sh/install.sh | sh`) or Docker.
- 16 GB of memory for one window at a time; 32 GB or more to run several.
- Disk: 2 GB per 20 m granule (5 to 8 GB at 10 m), about 10 GB of working files per
  20 m frame and date, and 2 GB for the environment.
- A free [NASA Earthdata login](https://urs.earthdata.nasa.gov) to download granules.

## Install

    git clone https://github.com/nicksteiner/overture.git
    cd overture
    pixi install
    pixi run test

`pixi install` builds one environment with OPERA DSWx (pinned to a commit that carries
fixes needed under GDAL 3.11 and later), overture, and openSEPPO. `pixi run test`
should report 16 tests passed.

Commands below are written as `pixi run overture ...` and are run from the `overture`
directory. `pixi shell` opens a shell in which plain `overture ...` works from anywhere.

Without pixi: install GDAL, rasterio, h5py and the other packages listed in
`pixi.toml`, install OPERA `dswx_sar` at the commit pinned there, then
`pip install -e .`.

## Quick start

With a GCOV granule already on disk:

    pixi run overture run \
        --h5 20260930=/data/granule.h5 \
        --out /data/runs/my_frame --anc-cache /data/ancillary_cache

The water map is `/data/runs/my_frame/frame_WTR_20260930.tif` and the numbers are in
`frame_report.json`. `20260930` is a label of your choice; it names the outputs.

## Step 1: Earthdata login

Put your login in `~/.netrc` once, readable only by you:

    echo "machine urs.earthdata.nasa.gov login <user> password <password>" >> ~/.netrc
    chmod 600 ~/.netrc

## Step 2: find the granules

`overture search` is openSEPPO's NISAR search; every one
of its options is available (`pixi run overture search -h`).

Find what covers a place in a time range:

    pixi run overture search --product GCOV --collection '%' \
        --point 17.5 12.8 \
        --start_time_after 2026-09-01 --start_time_before 2026-10-05 \
        --format csv -o granules.csv

`--point LON LAT` can be replaced by `--bbox MIN_LON MIN_LAT MAX_LON MAX_LAT`,
`--wkt`, or `--geojson FILE`. `--collection '%'` includes provisional products;
without it only validated ones are returned.

Read the granule names in `granules.csv`. The name carries what you need:

    NISAR_L2_PR_GCOV_031_173_A_008_2005_DHDH_A_20260930T042609_...
                     │   │   │ │        │      └ acquisition start
                     │   │   │ │        └ polarization: DHDH or QPDH carry HH and HV
                     │   │   │ └ frame
                     │   │   └ direction: A ascending, D descending
                     │   └ track
                     └ cycle (one cycle is 12 days)

Choose the granule for the date you want. It needs HH and HV (polarization code
starting `DH` or `QP`). To map several dates of one frame in one run, choose granules
with the same track, direction and frame.

## Step 3: download

The same search with `--download` fetches the files:

    pixi run overture search --product GCOV --collection '%' \
        --track 173 --frame 8 --direction A \
        --start_time_after 2026-09-29 --start_time_before 2026-10-01 \
        --https --download /data/granules

Add `--dryrun` to see the query without logging in, or `--format url --https` to list
the URLs without downloading.

## Step 4: run

Give each granule a label; it appears in the output file names.

    pixi run overture run \
        --h5 20260930=/data/granules/NISAR_L2_PR_GCOV_031_173_A_008_..._20260930T042609_....h5 \
        --out /data/runs/chad_173_A_008 \
        --anc-cache /data/ancillary_cache \
        --jobs 4 --threads 8

For a 10 m frame add `--window-km 48 --overlap-km 6`. Several dates of the same frame
can be run together by repeating `--h5` with a different label; each date is
processed on its own and gets its own products.

On the first run for an area the DEM, HAND, land-cover and surface-water tiles are
downloaded into `--anc-cache`; later runs reuse them.

The log prints one line per window and phase. To follow a long run, start it with
`nohup ... > run.log 2>&1 &` and use:

    pixi run overture status /data/runs/chad_173_A_008

## Step 5: outputs

In the run directory:

| file | content |
|---|---|
| `frame_WTR_<tag>.tif` | water classification on the frame grid, OPERA WTR classes |
| `frame_OVERLAP_<tag>.tif` | number of windows that classified each pixel |
| `frame_DISAGREE_<tag>.tif` | pixels where two windows disagree on water |
| `frame_report.json` | observed and water area, window disagreement |
| `w*/dswx_ni/<tag>/sas_output/` | the OPERA MGRS tiles of each window (WTR, BWTR, CONF) |
| `w*/dswx_ni/<tag>/state.json` | finished phases, seconds and peak memory per window |
| `frame_plan.json` | the windows and every setting of the run |

WTR classes counted as water: 1 open water, 2 bright-water fill, 3 inundated
vegetation, 8 partial surface water. The rasters are in the frame's UTM zone at
`--output-spacing`.

Check `disagree_frac` in `frame_report.json`: it is the share of overlap pixels on
which two windows gave different answers. Values of 0.1 to 0.4 % are typical.

## Options

`overture run` and `overture plan`:

| option | meaning | default |
|---|---|---|
| `--h5 TAG=PATH` | one GCOV granule per date of the same frame; TAG is your label | required |
| `--out DIR` | run directory | required |
| `--window-km` | window size; memory per window scales with its area | 96 |
| `--overlap-km` | overlap between neighbouring windows | 10 |
| `--min-valid` | skip windows with less than this fraction inside the swath | 0.02 |
| `--output-spacing` | product posting in metres | 20 |
| `--anc-cache DIR` | where downloaded ancillary tiles are kept | inside the run directory |
| `--algorithm-parameters FILE` | an OPERA algorithm parameter file of your own | the bundled one |
| `--single-pass` | each window fits its own thresholds; no frame threshold field | off |

`overture run` and `overture process`:

| option | meaning | default |
|---|---|---|
| `--jobs` | windows running at the same time | 4 |
| `--threads` | threads per window | 8 |
| `--min-free-gb` | start no new window while free memory is below this | 4 |
| `--tags` | restrict to some dates | all |

Every algorithm setting is OPERA's and lives in the algorithm parameter file: tile
size and selection, threshold method and bounds, fuzzy-logic weights, region growing,
land-cover and terrain masks, the bimodality test, inundated vegetation, the
partial-water count. A copy is written to `<run_dir>/algorithm_parameter_ni.yaml`;
edit a copy of it and pass it with `--algorithm-parameters`.

Commands:

| command | does |
|---|---|
| `overture run ...` | plan, process and reduce |
| `overture plan ...` | cut the windows and write the plan |
| `overture process DIR` | run every phase of every window |
| `overture reduce DIR` | frame products and overlap statistics |
| `overture status DIR` | what is finished and how long it took |
| `overture search ...` | openSEPPO `seppo_nisar_search`, all options passed through |
| `overture credentials ...` | openSEPPO `seppo_earthaccess_credentials`, all options passed through |

## Stopping, resuming, failures

A run can be stopped at any time. Run the same command again and finished work is
skipped: windows already cut, phases already done.

Each window has three phases: `filter`, `threshold`, `classify`. If a window fails,
the run continues with the others and lists the failure with the path of its log,
`<run_dir>/<window>/dswx_ni/<tag>/<phase>.log`. Fix the cause and run
`overture process <run_dir>` then `overture reduce <run_dir>`.

To redo a window from scratch, delete its `dswx_ni/<tag>/` directory contents except
`runconfig.yaml`, or delete the whole run directory and start again.

## Time and memory

Measured on a 48-core, 64 GB workstation.

| | 20 m frame | 10 m frame |
|---|---|---|
| windows per date | 16 of 96 km | about 60 of 48 km |
| memory per window | 2.4 GB peak | 2.4 GB peak |
| time per window | 6 to 16 min | 6 to 16 min |
| one date, 4 to 5 windows at a time | about 30 min | 1.5 to 2 h |

Set `--jobs` so that `jobs × threads` is near the number of cores and
`jobs × 3 GB` fits in memory. On a 16 GB laptop use `--jobs 1 --threads 4`.

## Docker

    docker build -t overture .

The container runs the `overture` command; mount the directory that holds the data
and run as yourself so the output files are yours:

    docker run --rm -u $(id -u):$(id -g) -v /data:/data overture run \
        --h5 20260930=/data/granules/<granule>.h5 \
        --out /data/runs/my_frame --anc-cache /data/ancillary_cache

To search and download from inside the container, mount the Earthdata login too:

    docker run --rm -u $(id -u):$(id -g) -v $HOME/.netrc:/tmp/.netrc:ro -v /data:/data \
        overture search --product GCOV --collection '%' --track 173 --frame 8 --direction A \
        --start_time_after 2026-09-17 --start_time_before 2026-10-01 --https --download /data/granules

## How it works

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
7. **Reduce.** Window tiles are mosaicked on the frame grid and the overlap
   disagreement is written.

OPERA `dswx_sar` is a dependency and is not modified. All contact with it is in
`src/overture/opera.py`: the stages are called through their `run(cfg)` functions, and
two functions of the threshold stage are replaced at run time, in the window's own
process, to inject the frame bound and to record the block thresholds.

## Limits

- Dual-pol or quad-pol GCOV with HH and HV on frequency A; mixed polarization modes
  are not handled.
- Windows are cut from granules on disk. Reading windows straight from the archive
  without downloading the granule is not in this package yet.
- A frame with no tile containing both land and water has no threshold field and the
  run stops with a message.
- The frame dark-water bound is OPERA's statistic over the whole frame; it can be
  looser than a single window's.
- In the mosaic the first window that classified a pixel wins.
- Keep windows much larger than the water bodies in the frame. OPERA's bimodality
  refinement tests each connected water body against the land around it; a window that
  is almost all lake gives it no land to compare with and the lake can be removed in
  that window. With the default 96 km windows this has not occurred; in a test with
  20 km windows on a 2 500 km² lake two windows disagreed on a quarter of their
  overlap. The window disagreement in `frame_report.json` shows it when it happens.

## Tests

    pixi run test

Unit tests on synthetic data, under a second. The full path has been run on NISAR
frames at 20 m.

## License

Apache-2.0. OPERA DSWx-SAR and openSEPPO are separate works under their own licenses.
