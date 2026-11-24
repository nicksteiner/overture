import h5py
import numpy as np
import pytest

from overture import gcov


@pytest.mark.parametrize("extent,size,overlap", [(16631, 4800, 500), (16955, 4800, 500), (35000, 4800, 300),
                                                 (4800, 4800, 500), (3000, 4800, 500), (4801, 4800, 500)])
def test_window_origins_cover_the_frame(extent, size, overlap):
    size = min(size, extent)
    o = gcov.window_origins(extent, size, overlap)
    assert o[0] == 0 and o[-1] + size == extent           # first starts, last ends, on the frame edges
    assert all(b > a for a, b in zip(o, o[1:]))
    assert all(a + size - b >= overlap for a, b in zip(o, o[1:]))  # at least the requested overlap


def test_cut_window_keeps_pixels_and_geometry(gcov_file, tmp_path):
    out = tmp_path / "w.h5"
    info = gcov.cut_window(gcov_file, str(out), 40, 30, 100, 120)
    assert info["rows"] == [40, 140] and info["cols"] == [30, 150]
    with h5py.File(gcov_file) as s, h5py.File(out) as d:
        g, w = s[gcov.GRID], d[gcov.GRID]
        src = g["HHHH"][40:140, 30:150]
        cut = w["HHHH"][:]
        valid = src > 0
        assert np.array_equal(cut[valid], src[valid])     # same pixels, no resampling
        assert np.isnan(cut[~valid]).all()                 # zero backscatter becomes no-data
        assert np.array_equal(w["xCoordinates"][:], g["xCoordinates"][30:150])
        assert np.array_equal(w["yCoordinates"][:], g["yCoordinates"][40:140])
        assert w["mask"].dtype == np.uint8 and np.array_equal(w["mask"][:], g["mask"][40:140, 30:150])
        assert int(w["projection"][()]) == 32633
        assert list(w["listOfPolarizations"][:]) == [b"HH", b"HV"]
        assert list(d["science/LSAR/identification/listOfFrequencies"][:]) == [b"A"]
        assert d["science/LSAR/GCOV/metadata/note"][()] == b"meta"


def test_valid_fraction(gcov_file):
    assert gcov.valid_fraction(gcov_file, 0, 0, 50, 50, step=1) == 0.0
    assert gcov.valid_fraction(gcov_file, 100, 100, 50, 50, step=1) == 1.0
    assert gcov.valid_fraction(gcov_file, 0, 0, 100, 100, step=1) == pytest.approx(0.75)


def test_fixup_restores_what_a_bare_subset_lacks(gcov_file, tmp_path):
    out = tmp_path / "w.h5"
    gcov.cut_window(gcov_file, str(out), 0, 0, 64, 64)
    with h5py.File(out, "a") as h:
        g = h[gcov.GRID]
        m = g["mask"][:].astype("float32")
        for k in ("mask", "listOfPolarizations", "xCoordinateSpacing", "yCoordinateSpacing"):
            del g[k]
        g.create_dataset("mask", data=m)
        del h["science/LSAR/identification/listOfFrequencies"]
    gcov.fixup_subset(str(out))
    with h5py.File(out) as h:
        g = h[gcov.GRID]
        assert g["mask"].dtype == np.uint8
        assert float(g["xCoordinateSpacing"][()]) == 20.0 and float(g["yCoordinateSpacing"][()]) == -20.0
        assert list(g["listOfPolarizations"][:]) == [b"HH", b"HV"]
