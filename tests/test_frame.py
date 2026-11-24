import numpy as np

from overture.frame import NODATA, pool_blocks


def blocks(rows, cols, block, value, bands=2):
    by, bx = block
    return np.full((-(-rows // by), -(-cols // bx), bands), value, dtype="float32")


def test_aligned_window_is_reproduced():
    a = blocks(800, 1200, (400, 400), NODATA)
    a[0, 1] = [-15.0, -22.0]
    a[1, 2] = [-14.0, -21.0]
    f = pool_blocks([(0, 0, 800, 1200, a)], (800, 1200), (400, 400))
    assert f.shape == (2, 3, 2)
    assert np.allclose(f, a)


def test_overlapping_windows_average_and_empty_cells_stay_empty():
    a = blocks(400, 400, (400, 400), -16.0)
    b = blocks(400, 400, (400, 400), -12.0)
    f = pool_blocks([(0, 0, 400, 400, a), (0, 0, 400, 400, b)], (800, 800), (400, 400))
    assert np.allclose(f[0, 0], -14.0)
    assert (f[1, 1] == NODATA).all() and (f[0, 1] == NODATA).all()


def test_offset_window_spreads_by_area():
    # a window that starts 100 px into the frame: its block covers 300 px of frame cell 0 and 100 px of cell 1
    a = blocks(400, 400, (400, 400), -10.0, bands=1)
    f = pool_blocks([(0, 100, 400, 400, a)], (400, 800), (400, 400))
    assert np.allclose(f[0, :, 0], [-10.0, -10.0])
    b = blocks(400, 400, (400, 400), -20.0, bands=1)
    f = pool_blocks([(0, 100, 400, 400, a), (0, 400, 400, 400, b)], (400, 800), (400, 400))
    assert np.isclose(f[0, 0, 0], -10.0)
    assert np.isclose(f[0, 1, 0], (-10.0 * 100 + -20.0 * 400) / 500)


def test_constant_field_stays_constant_for_any_layout():
    wins = [(r, c, 4800, 4800, blocks(4800, 4800, (400, 400), -13.5)) for r in (0, 3944, 7888) for c in (0, 4052)]
    f = pool_blocks(wins, (12688, 8852), (400, 400))
    assert f.shape == (32, 23, 2)
    assert np.allclose(f, -13.5)


def test_bands_are_independent_and_nan_is_nodata():
    a = blocks(400, 400, (400, 400), -15.0)
    a[0, 0, 1] = np.nan
    f = pool_blocks([(0, 0, 400, 400, a)], (400, 400), (400, 400))
    assert f[0, 0, 0] == -15.0 and f[0, 0, 1] == NODATA
    assert pool_blocks([], (400, 400), (400, 400)) is None
