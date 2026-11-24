"""Reduce: window products back onto the frame grid, the chunking error measured where
windows overlap."""
from __future__ import annotations

import glob
import json
import os

import numpy as np
import rasterio
import rasterio.warp
from rasterio.transform import from_origin
from rasterio.windows import Window

from . import gcov, plan as planmod

WATER = (1, 2, 3, 8)  # open water, bright-water fill, inundated vegetation, partial surface water
REPORT = "frame_report.json"


def frame_geometry(h5_path, spacing):
    g = gcov.frame_grid(h5_path)
    x, y, dx = g["x"], g["y"], g["dx"]
    W = int(round((float(x[-1]) - float(x[0]) + dx) / spacing))
    H = int(round((float(y[0]) - float(y[-1]) + dx) / spacing))
    return from_origin(float(x[0]) - dx / 2, float(y[0]) + dx / 2, spacing, spacing), f"EPSG:{g['epsg']}", H, W


def _tile_window(src, tr, crs, H, W):
    b = rasterio.warp.transform_bounds(src.crs, crs, *src.bounds, densify_pts=11)
    c0, c1 = max(0, int(np.floor((b[0] - tr.c) / tr.a))), min(W, int(np.ceil((b[2] - tr.c) / tr.a)))
    r0, r1 = max(0, int(np.floor((tr.f - b[3]) / -tr.e))), min(H, int(np.ceil((tr.f - b[1]) / -tr.e)))
    if c1 <= c0 or r1 <= r0:
        return None
    return Window(c0, r0, c1 - c0, r1 - r0)


def mosaic_windows(window_tiles, tr, crs, H, W):
    """window_tiles: {window name: [WTR tile paths]}. First-valid mosaic on the frame grid,
    number of windows per pixel, and pixels where two windows disagree on water."""
    mosaic = np.full((H, W), 255, np.uint8)
    count = np.zeros((H, W), np.uint8)
    disagree = np.zeros((H, W), bool)
    for name in sorted(window_tiles):
        win = {}
        for t in window_tiles[name]:
            with rasterio.open(t) as s:
                w = _tile_window(s, tr, crs, H, W)
                if w is None:
                    continue
                dst = np.full((int(w.height), int(w.width)), 255, np.uint8)
                rasterio.warp.reproject(rasterio.band(s, 1), dst, dst_transform=rasterio.windows.transform(w, tr),
                                        dst_crs=crs, dst_nodata=255, resampling=rasterio.warp.Resampling.nearest)
            sl = (slice(int(w.row_off), int(w.row_off) + dst.shape[0]),
                  slice(int(w.col_off), int(w.col_off) + dst.shape[1]))
            cur = win.setdefault(sl, np.full(dst.shape, 255, np.uint8))
            fill = (dst < 250) & (cur >= 250)
            cur[fill] = dst[fill]
        for sl, arr in win.items():
            v = arr < 250
            both = v & (count[sl] > 0)
            disagree[sl] |= both & (np.isin(arr, WATER) != np.isin(mosaic[sl], WATER))
            fill = v & (mosaic[sl] >= 250)
            mosaic[sl][fill] = arr[fill]
            count[sl][v] = np.minimum(count[sl][v].astype(np.uint16) + 1, 255).astype(np.uint8)
    return mosaic, count, disagree


def _write(path, arr, tr, crs, nodata):
    prof = dict(driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1, dtype="uint8", crs=crs,
                transform=tr, nodata=nodata, compress="deflate", tiled=True, BIGTIFF="IF_SAFER")
    with rasterio.open(path, "w", **prof) as d:
        d.write(arr, 1)


def reduce(run_dir, tags=None, log=print):
    """Write frame_WTR_<tag>.tif, frame_OVERLAP_<tag>.tif, frame_DISAGREE_<tag>.tif and the report."""
    plan = planmod.load(run_dir)
    tags = tags or list(plan["tags"])
    spacing = float(plan["output_spacing"])
    px_km2 = spacing ** 2 / 1e6
    rpath = os.path.join(run_dir, REPORT)
    report = json.load(open(rpath)) if os.path.exists(rpath) else {}
    for tag in tags:
        tr, crs, H, W = frame_geometry(plan["tags"][tag]["h5"], spacing)
        tiles, missing = {}, []
        for w in planmod.kept_windows(plan):
            t = sorted(glob.glob(os.path.join(planmod.unit_dir(run_dir, w, tag), "sas_output", "*_B01_WTR.tif")))
            if t:
                tiles[w] = t
            else:
                missing.append(w)
        mosaic, count, disagree = mosaic_windows(tiles, tr, crs, H, W)
        obs, ov = mosaic < 250, count > 1
        rep = dict(observed_km2=round(float(obs.sum() * px_km2), 1),
                   water_km2=round(float((np.isin(mosaic, WATER) & obs).sum() * px_km2), 1),
                   overlap_km2=round(float(ov.sum() * px_km2), 1),
                   disagree_km2=round(float(disagree.sum() * px_km2), 1),
                   disagree_frac=round(float(disagree[ov].mean()), 5) if ov.any() else None,
                   windows=len(tiles), windows_without_product=missing)
        report[tag] = rep
        _write(os.path.join(run_dir, f"frame_WTR_{tag}.tif"), mosaic, tr, crs, 255)
        _write(os.path.join(run_dir, f"frame_OVERLAP_{tag}.tif"), count, tr, crs, 0)
        _write(os.path.join(run_dir, f"frame_DISAGREE_{tag}.tif"), disagree.astype(np.uint8), tr, crs, 0)
        log(f"reduce {tag}: observed {rep['observed_km2']:.0f} km2, water {rep['water_km2']:.1f} km2, "
            f"window disagreement {100 * (rep['disagree_frac'] or 0):.2f} %, missing {missing}")
    json.dump(report, open(rpath, "w"), indent=1)
    return report
