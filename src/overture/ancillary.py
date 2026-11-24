"""One ancillary set for the whole frame, from the public sources OPERA DSWx uses:
Copernicus GLO-30 DEM, ASF GLO-30 HAND, ESA WorldCover v200, JRC Global Surface Water occurrence."""
from __future__ import annotations

import math
import os
import subprocess


def _ll(v, pos, neg, width=2):
    return f"{pos if v >= 0 else neg}{abs(v):0{width}d}"


def _cop(la, lo):
    return f"Copernicus_DSM_COG_10_{_ll(la, 'N', 'S')}_00_{_ll(lo, 'E', 'W', 3)}_00"


# key -> (cache subdirectory, url(lat, lon), tile size in degrees, tile named by upper-left corner)
SOURCES = {
    "dem": ("dem", lambda la, lo: f"https://copernicus-dem-30m.s3.amazonaws.com/{_cop(la, lo)}_DEM/{_cop(la, lo)}_DEM.tif",
            1, False),
    "hand": ("hand", lambda la, lo: f"https://glo-30-hand.s3.amazonaws.com/v1/2021/{_cop(la, lo)}_HAND.tif", 1, False),
    "worldcover": ("worldcover", lambda la, lo: "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/"
                   f"ESA_WorldCover_10m_2021_v200_{_ll(la, 'N', 'S')}{_ll(lo, 'E', 'W', 3)}_Map.tif", 3, False),
    "reference_water": ("gsw", lambda la, lo: "https://storage.googleapis.com/global-surface-water/downloads2021/"
                        f"occurrence/occurrence_{abs(lo)}{'E' if lo >= 0 else 'W'}_{abs(la)}{'N' if la >= 0 else 'S'}"
                        "v1_4_2021.tif", 10, True),
}


def tiles_for(bbox, deg, upper_left=False):
    """Integer tile origins (lat, lon) of size `deg` covering bbox = (w, s, e, n)."""
    w, s, e, n = bbox
    lo0, lo1 = math.floor(w / deg) * deg, math.floor((e - 1e-9) / deg) * deg
    la0, la1 = math.floor(s / deg) * deg, math.floor((n - 1e-9) / deg) * deg
    return [(la + deg if upper_left else la, lo)
            for la in range(la0, la1 + 1, deg) for lo in range(lo0, lo1 + 1, deg)]


def _fetch(url, dst):
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        return True
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".part"
    r = subprocess.run(["curl", "-sS", "-L", "--fail", "-o", tmp, url], capture_output=True, text=True)
    if r.returncode != 0:
        if os.path.exists(tmp):
            os.remove(tmp)
        return False  # ocean tiles do not exist
    os.replace(tmp, dst)
    return True


def build(grid, out_dir, cache_dir, margin=0.05, log=print):
    """Build dem/hand/worldcover/reference_water GeoTIFFs in EPSG:4326 covering a frame.
    `grid` is gcov.frame_grid(). Returns {key: path}. Existing files are kept."""
    from osgeo import gdal
    from rasterio.warp import transform_bounds
    gdal.UseExceptions()
    x, y = grid["x"], grid["y"]
    w, s, e, n = transform_bounds(f"EPSG:{grid['epsg']}", "EPSG:4326", float(x.min()), float(y.min()),
                                  float(x.max()), float(y.max()), densify_pts=21)
    bbox = (w - margin, s - margin, e + margin, n + margin)
    os.makedirs(out_dir, exist_ok=True)
    out = {}
    for key, (sub, urlf, deg, ul) in SOURCES.items():
        tif = os.path.join(out_dir, key + ".tif")
        out[key] = tif
        if os.path.exists(tif):
            continue
        files = []
        for la, lo in tiles_for(bbox, deg, upper_left=ul):
            url = urlf(la, lo)
            dst = os.path.join(cache_dir, sub, url.rsplit("/", 1)[1])
            if _fetch(url, dst):
                files.append(dst)
        if not files:
            raise RuntimeError(f"no {key} tiles found for bbox {bbox}")
        vrt = os.path.join(out_dir, f"_{key}.vrt")
        gdal.BuildVRT(vrt, files)
        gdal.Warp(tif + ".tmp.tif", vrt, outputBounds=bbox, dstSRS="EPSG:4326",
                  creationOptions=["COMPRESS=DEFLATE", "TILED=YES", "BIGTIFF=IF_SAFER"])
        os.replace(tif + ".tmp.tif", tif)
        log(f"ancillary {key}: {len(files)} tiles")
    return out
