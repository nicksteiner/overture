"""Frame-level steps of the two-pass run: one dark-water bound and one threshold field
for the whole frame, built from what the windows recorded."""
from __future__ import annotations

import json
import math
import os
import subprocess
import time

import numpy as np

from . import plan as planmod

NODATA = -50.0
FIELDS = ("intensity_threshold_filled", "mode_tau_filled")


def pool_blocks(windows, shape, block):
    """Place window block values on the frame's block grid.

    windows: iterable of (row0, col0, rows, cols, array[nby, nbx, bands]) where the array
    holds one value per `block`-sized cell of a window that starts at (row0, col0) of the
    frame and is rows x cols pixels; NODATA or NaN marks cells without a value.
    A frame cell takes the area-weighted mean of the window cells that overlap it.
    Returns array[ceil(H / by), ceil(W / bx), bands] with NODATA where nothing overlaps."""
    H, W = shape
    by, bx = block
    acc = area = None
    for row0, col0, rows, cols, arr in windows:
        arr = np.asarray(arr, dtype="float64")
        if acc is None:
            nb = arr.shape[2]
            acc = np.zeros((math.ceil(H / by), math.ceil(W / bx), nb))
            area = np.zeros_like(acc)
        for ii in range(arr.shape[0]):
            r0, r1 = row0 + ii * by, min(row0 + (ii + 1) * by, row0 + rows)
            for jj in range(arr.shape[1]):
                c0, c1 = col0 + jj * bx, min(col0 + (jj + 1) * bx, col0 + cols)
                v = arr[ii, jj]
                ok = np.isfinite(v) & (v != NODATA)
                if r1 <= r0 or c1 <= c0 or not ok.any():
                    continue
                for fi in range(r0 // by, (r1 - 1) // by + 1):
                    hr = min(r1, (fi + 1) * by) - max(r0, fi * by)
                    for fj in range(c0 // bx, (c1 - 1) // bx + 1):
                        a = hr * (min(c1, (fj + 1) * bx) - max(c0, fj * bx))
                        acc[fi, fj, ok] += v[ok] * a
                        area[fi, fj, ok] += a
    if acc is None:
        return None
    out = np.full(acc.shape, NODATA)
    has = area > 0
    out[has] = acc[has] / area[has]
    return out


def _first_cfg(run_dir, plan, tag, threads=None):
    from . import opera
    w = planmod.kept_windows(plan)[0]
    return opera.load_cfg(os.path.join(planmod.unit_dir(run_dir, w, tag), "runconfig.yaml"), threads)


def bound(run_dir, tag, log=print):
    """Dark-water bound over the whole frame: OPERA's function on a virtual mosaic of every
    window's filtered image, reference water and no-data mask."""
    from . import opera
    plan = planmod.load(run_dir)
    fdir = planmod.frame_dir(run_dir, tag)
    os.makedirs(fdir, exist_ok=True)
    cfg = _first_cfg(run_dir, plan, tag)
    vrts = {}
    names = opera.scratch_names(cfg)
    for key in ("filtered", "reference_water", "no_data"):
        files = [os.path.join(planmod.unit_dir(run_dir, w, tag), "scratch", names[key])
                 for w in planmod.kept_windows(plan)]
        files = [f for f in files if os.path.exists(f)]
        if not files:
            raise RuntimeError(f"no window has a {key} raster for {tag}; run the filter phase first")
        vrts[key] = os.path.join(fdir, f"frame_{key}.vrt")
        subprocess.run(["gdalbuildvrt", "-q", "-overwrite", vrts[key]] + files, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    t = time.time()
    res = opera.frame_bound(cfg, vrts["filtered"], vrts["reference_water"], vrts["no_data"])
    res["seconds"] = round(time.time() - t, 1)
    res["usable"] = bool(np.all(np.isfinite(res["thres_max"])))
    json.dump(res, open(os.path.join(fdir, "frame_bound.json"), "w"), indent=1)
    log(f"frame bound {tag}: " + ", ".join(f"{p} {v:.2f} dB" for p, v in zip(res["pols"], res["thres_max"])))
    return res


def field(run_dir, tag, log=print):
    """One threshold field for the frame, cropped into every window's scratch directory."""
    from . import opera
    plan = planmod.load(run_dir)
    fdir = planmod.frame_dir(run_dir, tag)
    os.makedirs(fdir, exist_ok=True)
    cfg = _first_cfg(run_dir, plan, tag)
    H, W = plan["tags"][tag]["shape"]
    block = opera.block_size(cfg)
    pols = opera.polarizations(cfg)
    recs = {}
    for w in planmod.kept_windows(plan):
        fn = os.path.join(planmod.unit_dir(run_dir, w, tag), "threshold_blocks.npz")
        if os.path.exists(fn):
            recs[w] = np.load(fn)
    rep = {}
    for f in FIELDS:
        wins = []
        for w, z in recs.items():
            if f not in z:
                continue
            inp = plan["windows"][w]["inputs"][tag]
            wins.append((inp["rows"][0], inp["cols"][0], int(z["rows"]), int(z["cols"]), z[f]))
        blocks = pool_blocks(wins, (H, W), block)
        n_valid = 0 if blocks is None else int((blocks[..., 0] != NODATA).sum())
        if n_valid == 0:
            raise RuntimeError(f"{tag}: no window fitted a threshold, so the frame has no threshold field. "
                               "The frame contains no tile with both land and water.")
        np.save(os.path.join(fdir, f"frame_blocks_{f}.npy"), blocks)
        t = time.time()
        opera.interpolate_field(cfg, blocks, H, W, f, fdir)
        rep[f] = dict(blocks_with_value=n_valid, blocks=int(blocks.shape[0] * blocks.shape[1]),
                      seconds=round(time.time() - t, 1))
        log(f"frame field {tag} {f}: {n_valid} of {rep[f]['blocks']} blocks, {rep[f]['seconds']} s")
    # crop into every window
    for w in planmod.kept_windows(plan):
        sdir = os.path.join(planmod.unit_dir(run_dir, w, tag), "scratch")
        if not os.path.isdir(sdir):
            continue
        inp = plan["windows"][w]["inputs"][tag]
        h, wd = inp["rows"][1] - inp["rows"][0], inp["cols"][1] - inp["cols"][0]
        if w in recs and "rows" in recs[w] and int(recs[w]["rows"]) and \
                (int(recs[w]["rows"]), int(recs[w]["cols"])) != (h, wd):
            raise RuntimeError(f"{w}: the filtered image is {int(recs[w]['rows'])}x{int(recs[w]['cols'])} px "
                               f"but the window is {h}x{wd}; cannot crop the frame field")
        for f in FIELDS:
            for pol in pols:
                dst = os.path.join(sdir, f"{f}_{pol}.tif")
                if os.path.exists(dst):
                    os.remove(dst)
                subprocess.run(["gdal_translate", "-q", "-srcwin", str(inp["cols"][0]), str(inp["rows"][0]),
                                str(wd), str(h), "-a_ullr", "0", str(h), str(wd), "0", "-co", "COMPRESS=DEFLATE",
                                os.path.join(fdir, f"{f}_{pol}.tif"), dst], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    json.dump(rep, open(os.path.join(fdir, "frame_field.json"), "w"), indent=1)
    return rep
