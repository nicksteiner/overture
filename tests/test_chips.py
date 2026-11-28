import h5py
import numpy as np
import pytest

from overture import chips, gcov

rasterio = pytest.importorskip("rasterio")


def _chip(path, x0, y1, arr, dx=20.0, epsg=32633):
    from rasterio.transform import from_origin
    with rasterio.open(path, "w", driver="GTiff", height=arr.shape[0], width=arr.shape[1], count=1, dtype="float32",
                       crs=f"EPSG:{epsg}", transform=from_origin(x0, y1, dx, dx), nodata=np.nan) as d:
        d.write(arr.astype("float32"), 1)


def test_pack_mosaics_chips_on_the_granule_grid(gcov_file, tmp_path):
    # two 50 x 60 px chips on the 20 m grid of the synthetic granule, with a 40 px gap between them
    a = np.full((50, 60), 0.2); b = np.full((50, 60), 0.4)
    for pol, k in (("HHHH", 1.0), ("HVHV", 0.25)):
        _chip(tmp_path / f"a__{pol}.tif", 500000, 1500000, a * k)
        _chip(tmp_path / f"b__{pol}.tif", 500000 + 100 * 20, 1500000, b * k)
    hh = [str(tmp_path / "a__HHHH.tif"), str(tmp_path / "b__HHHH.tif")]
    out = tmp_path / "packed.h5"
    info = chips.pack(hh, [t.replace("HHHH", "HVHV") for t in hh], gcov_file, str(out),
                      start_time="2026-05-25T15:12:59", log=lambda m: None)
    assert info["shape"] == [50, 160] and info["posting_m"] == 20.0 and info["epsg"] == 32633
    with h5py.File(out) as h:
        g = h[gcov.GRID]
        assert np.allclose(g["HHHH"][:, :60], 0.2) and np.allclose(g["HHHH"][:, 100:], 0.4)
        assert np.allclose(g["HVHV"][:, :60], 0.05)
        assert np.isnan(g["HHHH"][:, 60:100]).all()                       # between the chips: no data
        m = g["mask"][:]
        assert m.dtype == np.uint8 and (m[:, :60] == 1).all() and (m[:, 60:100] == 255).all()
        assert g["xCoordinates"][0] == 500010.0 and g["yCoordinates"][0] == 1499990.0   # pixel centres
        assert float(g["xCoordinateSpacing"][()]) == 20.0 and float(g["yCoordinateSpacing"][()]) == -20.0
        assert list(g["listOfPolarizations"][:]) == [b"HH", b"HV"]
        ident = h["science/LSAR/identification"]
        assert list(ident["listOfFrequencies"][:]) == [b"A"]
        assert ident["zeroDopplerStartTime"][()] == b"2026-05-25T15:12:59"
        assert ident["boundingPolygon"][()].startswith(b"POLYGON")
        assert h["science/LSAR/GCOV/metadata/note"][()] == b"meta"       # header copied from the granule
    # a packed file is a frame like any other
    assert gcov.frame_grid(str(out))["dx"] == 20.0
    assert gcov.valid_fraction(str(out), 0, 0, 50, 160, step=1) == pytest.approx(120 / 160)
