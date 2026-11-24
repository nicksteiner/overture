"""NISAR L2 GCOV frame geometry and window cutting. No dependency on the algorithm."""
from __future__ import annotations

import time

import h5py
import numpy as np

GRID = "science/LSAR/GCOV/grids/frequencyA"
IDENT = "science/LSAR/identification"
META = "science/LSAR/GCOV/metadata"


def frame_grid(h5_path):
    """Pixel-centre coordinates, EPSG code and posting of a GCOV frame."""
    with h5py.File(h5_path, "r") as h:
        g = h[GRID]
        x = g["xCoordinates"][:]
        y = g["yCoordinates"][:]
        epsg = int(g["projection"][()])
        dx = float(g["xCoordinateSpacing"][()]) if "xCoordinateSpacing" in g \
            else float(np.median(np.diff(x)))
    return dict(x=x, y=y, epsg=epsg, dx=abs(dx))


def window_origins(extent_px, size_px, overlap_px):
    """Start pixels of windows of `size_px` that cover `extent_px` with at least
    `overlap_px` between neighbours; the last window ends on the frame edge."""
    if extent_px <= size_px:
        return [0]
    if overlap_px >= size_px:
        raise ValueError("overlap must be smaller than the window")
    n = int(np.ceil((extent_px - overlap_px) / (size_px - overlap_px)))
    return [int(round(v)) for v in np.linspace(0, extent_px - size_px, n)]


def valid_fraction(h5_path, r0, c0, nr, nc, step=8):
    """Fraction of a window inside the swath, from the GCOV mask."""
    with h5py.File(h5_path, "r") as h:
        g = h[GRID]
        if "mask" not in g:
            return 1.0
        m = g["mask"][r0:r0 + nr:step, c0:c0 + nc:step]
    return float(((m > 0) & (m < 250)).mean()) if m.size else 0.0


def cut_window(src_path, dst_path, r0, c0, nr, nc, pols=("HH", "HV")):
    """Write rows r0:r0+nr, cols c0:c0+nc of a GCOV granule as a GCOV-shaped HDF5:
    same pixels, no resampling, identification and metadata copied."""
    t0 = time.time()
    with h5py.File(src_path, "r") as src, h5py.File(dst_path, "w") as dst:
        g = src[GRID]
        for grp in (IDENT, META):
            if grp in src:
                parent = dst.require_group(grp.rsplit("/", 1)[0])
                src.copy(src[grp], parent, name=grp.rsplit("/", 1)[1])
        ident = dst.require_group(IDENT)
        if "listOfFrequencies" in ident:
            del ident["listOfFrequencies"]
        ident.create_dataset("listOfFrequencies", data=np.array([b"A"]))
        dg = dst.require_group(GRID)
        for k, v in g.attrs.items():
            dg.attrs[k] = v
        chunks = (min(512, nr), min(512, nc))
        valid = 0.0
        for pol in pols:
            name = pol * 2
            arr = g[name][r0:r0 + nr, c0:c0 + nc]
            # zero and negative backscatter are no-data in GCOV; left in place they
            # become -inf dB in the water statistics
            arr = np.where(np.isfinite(arr) & (arr > 0), arr, np.nan).astype("float32")
            d = dg.create_dataset(name, data=arr, chunks=chunks, compression="gzip", compression_opts=1)
            for k, v in g[name].attrs.items():
                d.attrs[k] = v
            valid = float(np.isfinite(arr).mean()) if arr.size else 0.0
        m = g["mask"][r0:r0 + nr, c0:c0 + nc] if "mask" in g else np.zeros((nr, nc), "uint8")
        dm = dg.create_dataset("mask", data=m.astype("uint8"), chunks=chunks, compression="gzip",
                               compression_opts=1)
        if "mask" in g:
            for k, v in g["mask"].attrs.items():
                dm.attrs[k] = v
        x = g["xCoordinates"][c0:c0 + nc]
        y = g["yCoordinates"][r0:r0 + nr]
        for name, val in (("xCoordinates", x), ("yCoordinates", y)):
            d = dg.create_dataset(name, data=val)
            for k, v in g[name].attrs.items():
                d.attrs[k] = v
        for name in ("xCoordinateSpacing", "yCoordinateSpacing", "projection"):
            src.copy(g[name], dg, name=name)
        dg.create_dataset("listOfPolarizations", data=np.array([p.encode() for p in pols], dtype="S2"))
    return dict(rows=[int(r0), int(r0 + nr)], cols=[int(c0), int(c0 + nc)], valid_frac=round(valid, 4),
                seconds=round(time.time() - t0, 1))


def fixup_subset(path, pols=("HH", "HV")):
    """Restore what an openSEPPO `-of h5` subset lacks to be a DSWx-NI input:
    listOfFrequencies, listOfPolarizations, coordinate spacings, uint8 mask, NaN no-data."""
    with h5py.File(path, "a") as h:
        g = h[GRID]
        x = g["xCoordinates"][:]
        y = g["yCoordinates"][:]
        for name, val in (("xCoordinateSpacing", float(np.median(np.diff(x)))),
                          ("yCoordinateSpacing", float(np.median(np.diff(y))))):
            if name in g:
                del g[name]
            g.create_dataset(name, data=np.float64(val))
        if "listOfPolarizations" in g:
            del g["listOfPolarizations"]
        g.create_dataset("listOfPolarizations", data=np.array([p.encode() for p in pols], dtype="S2"))
        ident = h.require_group(IDENT)
        if "listOfFrequencies" in ident:
            del ident["listOfFrequencies"]
        ident.create_dataset("listOfFrequencies", data=np.array([b"A"]))
        if "mask" in g and g["mask"].dtype != np.uint8:
            m = g["mask"][:]
            skip = ("DIMENSION_LIST", "_Netcdf4Coordinates", "_Netcdf4Dimid", "_FillValue", "valid_min")
            attrs = {k: v for k, v in g["mask"].attrs.items() if k not in skip}
            del g["mask"]
            m8 = np.nan_to_num(m, nan=255).astype(np.uint8)
            dm = g.create_dataset("mask", data=m8, chunks=(min(512, m8.shape[0]), min(512, m8.shape[1])),
                                  compression="gzip", compression_opts=1)
            for k, v in attrs.items():
                dm.attrs[k] = v
            dm.attrs["_FillValue"] = np.uint8(255)
