import numpy as np
import rasterio
from rasterio.transform import from_origin

from overture import reduce as red


def tile(path, x0, y0, arr, epsg=32633, res=20.0):
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1, dtype="uint8",
                       crs=f"EPSG:{epsg}", transform=from_origin(x0, y0, res, res), nodata=255) as d:
        d.write(arr, 1)
    return str(path)


def test_mosaic_counts_overlap_and_disagreement(gcov_file, tmp_path):
    tr, crs, H, W = red.frame_geometry(gcov_file, 20.0)
    assert (H, W) == (300, 400) and tr.c == 500000 and tr.f == 1500000
    a = np.zeros((300, 250), np.uint8); a[100:200, 100:200] = 1          # window A: columns 0-249
    b = np.zeros((300, 250), np.uint8); b[100:200, 0:50] = 1             # window B: columns 150-399
    b[0:10, :] = 255
    ta = tile(tmp_path / "a.tif", 500000, 1500000, a)
    tb = tile(tmp_path / "b.tif", 500000 + 150 * 20, 1500000, b)
    mosaic, count, disagree = red.mosaic_windows({"w0000": [ta], "w0001": [tb]}, tr, crs, H, W)
    assert (count[20:, 150:250] == 2).all() and (count[20:, :150] == 1).all() and (count[20:, 250:] == 1).all()
    assert (count[:10, 250:] == 0).all() and (mosaic[:10, 250:] == 255).all()
    # in the overlap (frame columns 150-249) A says water in 150-199, B says water in 150-199: agree
    assert not disagree.any()
    b2 = b.copy(); b2[100:200, 0:50] = 0                                 # B now says land where A says water
    tb2 = tile(tmp_path / "b2.tif", 500000 + 150 * 20, 1500000, b2)
    mosaic, count, disagree = red.mosaic_windows({"w0000": [ta], "w0001": [tb2]}, tr, crs, H, W)
    assert disagree.sum() == 100 * 50 and disagree[100:200, 150:200].all()
    assert (mosaic[100:200, 150:200] == 1).all()                         # first valid window wins


def test_flood_classes(tmp_path):
    wet = np.array([[1, 1, 0, 0, 255, 8]], np.uint8)
    ref = np.array([[1, 0, 1, 0, 0, 255]], np.uint8)
    tile(tmp_path / "frame_WTR_wet.tif", 0, 20, wet)
    tile(tmp_path / "frame_WTR_ref.tif", 0, 20, ref)
    rep = red.flood(str(tmp_path), "wet", "ref", log=lambda m: None)
    with rasterio.open(tmp_path / "frame_FLOOD_wet_vs_ref.tif") as d:
        assert d.read(1).tolist() == [[1, 2, 3, 0, 255, 255]]
    assert rep["flood_km2"] == 0.0004 * 1 or rep["flood_km2"] == 0.0     # one 20 m pixel rounds to 0.0 km2
