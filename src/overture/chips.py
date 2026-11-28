"""Pack GeoTIFF chips of HH and HV backscatter into a GCOV-shaped HDF5 that `overture run` accepts.

Chips are small rasters cut from GCOV frames (for example one per river segment). They are
mosaicked on one grid at the frame's posting, and the identification and metadata groups
OPERA reads are copied from a real granule of the same mode (`like`)."""
from __future__ import annotations

import os

import h5py
import numpy as np

from .gcov import GRID, IDENT, META


def _mosaic(tifs, epsg, dx, bounds, resampling="average"):
    from osgeo import gdal
    gdal.UseExceptions()
    ds = gdal.Warp("", list(tifs), format="MEM", dstSRS=f"EPSG:{epsg}", xRes=dx, yRes=dx, outputBounds=bounds,
                   resampleAlg=resampling, srcNodata="nan", dstNodata="nan", outputType=gdal.GDT_Float32,
                   multithread=True)
    a = ds.GetRasterBand(1).ReadAsArray()
    return np.where(np.isfinite(a) & (a > 0), a, np.nan).astype("float32")


def chip_bounds(tifs, epsg, dx):
    """Union of the chip extents in the target projection, snapped outward to the posting."""
    from osgeo import gdal, osr
    gdal.UseExceptions()
    dst = osr.SpatialReference(); dst.ImportFromEPSG(int(epsg)); dst.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    xs, ys = [], []
    for t in tifs:
        d = gdal.Open(t)
        gt = d.GetGeoTransform()
        src = osr.SpatialReference(wkt=d.GetProjection()); src.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        tr = osr.CoordinateTransformation(src, dst)
        for px, py in ((0, 0), (d.RasterXSize, 0), (0, d.RasterYSize), (d.RasterXSize, d.RasterYSize)):
            x, y, _ = tr.TransformPoint(gt[0] + px * gt[1] + py * gt[2], gt[3] + px * gt[4] + py * gt[5])
            xs.append(x); ys.append(y)
    f = lambda v: np.floor(v / dx) * dx
    c = lambda v: np.ceil(v / dx) * dx
    return float(f(min(xs))), float(f(min(ys))), float(c(max(xs))), float(c(max(ys)))


def pack(hh_tifs, hv_tifs, like, dst, start_time=None, bounds=None, log=print):
    """Write `dst`, a GCOV-shaped HDF5 holding the mosaic of the chips.

    hh_tifs, hv_tifs: HH and HV power chips (linear units, as in GCOV), any projection.
    like: a real GCOV granule of the same mode; gives projection, posting and the header groups.
    start_time: acquisition start of the chips, 'YYYY-MM-DDTHH:MM:SS' (default: that of `like`)."""
    with h5py.File(like, "r") as src:
        g = src[GRID]
        epsg = int(g["projection"][()])
        dx = abs(float(g["xCoordinateSpacing"][()]))
    b = tuple(bounds) if bounds else chip_bounds(hh_tifs, epsg, dx)
    hh = _mosaic(hh_tifs, epsg, dx, b)
    hv = _mosaic(hv_tifs, epsg, dx, b)
    ok = np.isfinite(hh) & np.isfinite(hv)
    hh[~ok] = np.nan; hv[~ok] = np.nan
    H, W = hh.shape
    x = b[0] + dx / 2 + dx * np.arange(W)
    y = b[3] - dx / 2 - dx * np.arange(H)
    tmp = str(dst) + ".part"
    with h5py.File(like, "r") as src, h5py.File(tmp, "w") as out:
        g = src[GRID]
        for grp in (IDENT, META):
            if grp in src:
                src.copy(src[grp], out.require_group(grp.rsplit("/", 1)[0]), name=grp.rsplit("/", 1)[1])
        ident = out.require_group(IDENT)
        def put(name, val):
            if name in ident:
                del ident[name]
            ident.create_dataset(name, data=val)
        put("listOfFrequencies", np.array([b"A"]))
        put("isFullFrame", np.bytes_("False"))
        from osgeo import osr
        s = osr.SpatialReference(); s.ImportFromEPSG(epsg); s.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        ll = osr.SpatialReference(); ll.ImportFromEPSG(4326); ll.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
        tr = osr.CoordinateTransformation(s, ll)
        ring = [tr.TransformPoint(px, py)[:2] for px, py in ((b[0], b[3]), (b[2], b[3]), (b[2], b[1]), (b[0], b[1]), (b[0], b[3]))]
        put("boundingPolygon", np.bytes_("POLYGON ((" + ", ".join(f"{lo:.6f} {la:.6f}" for lo, la in ring) + "))"))
        if start_time:
            put("zeroDopplerStartTime", np.bytes_(start_time)); put("zeroDopplerEndTime", np.bytes_(start_time))
        dg = out.require_group(GRID)
        for k, v in g.attrs.items():
            dg.attrs[k] = v
        chunks = (min(512, H), min(512, W))
        for name, arr in (("HHHH", hh), ("HVHV", hv)):
            d = dg.create_dataset(name, data=arr, chunks=chunks, compression="gzip", compression_opts=1)
            for k, v in g[name].attrs.items():
                if k not in ("DIMENSION_LIST",):
                    d.attrs[k] = v
        m = np.where(ok, 1, 255).astype("uint8")
        dm = dg.create_dataset("mask", data=m, chunks=chunks, compression="gzip", compression_opts=1)
        dm.attrs["_FillValue"] = np.uint8(255)
        dg.create_dataset("xCoordinates", data=x)
        dg.create_dataset("yCoordinates", data=y)
        dg.create_dataset("xCoordinateSpacing", data=np.float64(dx))
        dg.create_dataset("yCoordinateSpacing", data=np.float64(-dx))
        src.copy(g["projection"], dg, name="projection")
        dg.create_dataset("listOfPolarizations", data=np.array([b"HH", b"HV"], dtype="S2"))
    os.replace(tmp, dst)
    info = dict(shape=[int(H), int(W)], epsg=epsg, posting_m=dx, bounds=list(b), valid_frac=round(float(ok.mean()), 4),
                chips=len(hh_tifs))
    log(f"packed {len(hh_tifs)} chips into {dst}: {H} x {W} px at {dx:g} m, EPSG:{epsg}, {100 * ok.mean():.1f} % valid")
    return info
