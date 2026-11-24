import h5py
import numpy as np
import pytest

GRID = "science/LSAR/GCOV/grids/frequencyA"


@pytest.fixture
def gcov_file(tmp_path):
    """A small synthetic GCOV granule: 300 x 400 px at 20 m in UTM 33N, with a no-data corner."""
    p = tmp_path / "frame.h5"
    rng = np.random.default_rng(0)
    H, W, dx = 300, 400, 20.0
    with h5py.File(p, "w") as h:
        h.create_group("science/LSAR/identification").create_dataset("listOfFrequencies", data=np.array([b"A", b"B"]))
        h.create_group("science/LSAR/GCOV/metadata").create_dataset("note", data=b"meta")
        g = h.create_group(GRID)
        hh = rng.gamma(2.0, 0.05, (H, W)).astype("float32")
        hh[:50, :50] = 0.0
        g.create_dataset("HHHH", data=hh)
        g.create_dataset("HVHV", data=(hh / 4).astype("float32"))
        m = np.ones((H, W), "uint8")
        m[:50, :50] = 255
        g.create_dataset("mask", data=m)
        g.create_dataset("xCoordinates", data=500000 + dx / 2 + dx * np.arange(W))
        g.create_dataset("yCoordinates", data=1500000 - dx / 2 - dx * np.arange(H))
        g.create_dataset("xCoordinateSpacing", data=dx)
        g.create_dataset("yCoordinateSpacing", data=-dx)
        g.create_dataset("projection", data=32633)
    return str(p)
