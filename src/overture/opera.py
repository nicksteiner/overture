"""Every contact with OPERA dswx_sar is in this module.

The OPERA package is used as installed. Stages are called through their public
`run(cfg)` functions. Two functions of the threshold stage are replaced at run time, in
the calling process only, to make the threshold frame-consistent:
  compute_threshold_max_bound   returns the dark-water bound computed over the whole frame
  fill_threshold_with_gdal      records the per-block thresholds instead of interpolating
                                them over the window; the interpolation is done once per frame
"""
from __future__ import annotations

import mimetypes
import os

import numpy as np

mimetypes.add_type("text/yaml", ".yaml", strict=True)  # the OPERA loader checks the mime type

NODATA = -50.0
FIELDS = ("intensity_threshold_filled", "mode_tau_filled")


def load_cfg(runconfig_path, threads=None):
    from dswx_sar.nisar.dswx_ni_runconfig import RunConfig, _get_parser
    from dswx_sar.common import _generate_log
    args = _get_parser().parse_args([str(runconfig_path)])
    cfg = RunConfig.load_from_yaml(str(runconfig_path), "dswx_ni", args)
    _generate_log.configure_log_file(cfg.groups.log_file)
    pc = cfg.groups.processing
    if str(pc.polarization_mode).startswith("MIX"):
        raise NotImplementedError(f"polarization mode {pc.polarization_mode} is not supported by overture")
    if threads:
        # OPERA's parameter file leaves the two long stages on one worker; both accept more
        # and give the same product (checked pixel for pixel on a 4800 x 4800 window)
        pc.initial_threshold.number_cpu = int(threads)
        pc.refine_with_bimodality.number_cpu = int(threads)
        pc.masking_ancillary.number_cpu = int(threads)
    return cfg


def polarizations(cfg):
    pols = list(cfg.groups.processing.polarizations)
    opts = cfg.groups.processing.polarimetric_option
    if opts is not None:
        pols += list(opts)
    return pols


def scratch_names(cfg):
    """File names, inside a window's scratch directory, of the rasters the frame steps read."""
    pol_all = "_".join(polarizations(cfg))
    return dict(filtered=f"filtered_image_{pol_all}.tif", no_data=f"no_data_area_{pol_all}.tif",
                reference_water="interpolated_wbd.tif")


def block_size(cfg):
    m = cfg.groups.processing.initial_threshold.maximum_tile_size
    return int(m.y), int(m.x)


def run_filter(cfg):
    """Mosaic and filter: everything before the threshold stage."""
    from dswx_sar.nisar import mosaic_gcov_frame, pre_processing
    mosaic_gcov_frame.run(cfg)
    pre_processing.run(cfg)


def run_threshold(cfg, bound=None, capture=False):
    """The threshold stage. With `bound`, the frame's dark-water bound replaces the window's.
    With `capture`, the per-block thresholds are returned and no window raster is written."""
    from dswx_sar.common import _initial_threshold as it
    saved = it.compute_threshold_max_bound, it.fill_threshold_with_gdal
    rec = {}

    def frame_bound(*args, **kwargs):
        return list(bound["thres_max"]), list(bound["mean"]), list(bound["std"]), bool(bound["is_bimodal"])

    def record(threshold_array, rows, cols, filename, outputdir, pol_list, filled_value,
               no_data=NODATA, average_tile=True):
        if not average_tile:
            raise RuntimeError("the frame threshold field needs initial_threshold.tile_average: True")
        rec[filename] = np.array(threshold_array["array"], dtype="float32")
        rec["rows"], rec["cols"] = int(rows), int(cols)

    try:
        if bound is not None:
            it.compute_threshold_max_bound = frame_bound
        if capture:
            it.fill_threshold_with_gdal = record
        it.run(cfg)
    finally:
        it.compute_threshold_max_bound, it.fill_threshold_with_gdal = saved
    return rec


def run_classify(cfg):
    """Fuzzy membership through MGRS tiles: everything after the threshold stage."""
    from dswx_sar.common import _fuzzy_value_computation as fuzzy
    from dswx_sar.nisar import (region_growing, masking_with_ancillary, refine_with_bimodality,
                                detect_inundated_vegetation, save_mgrs_tiles_ni)
    pc = cfg.groups.processing
    fuzzy.run(cfg)
    region_growing.run(cfg)
    if pc.dswx_workflow == "opera_dswx_ni":
        masking_with_ancillary.run(cfg)
        refine_with_bimodality.run(cfg)
        iv = pc.inundated_vegetation.enabled
        if iv is True or (iv == "auto" and len(pc.polarizations) >= 2):
            detect_inundated_vegetation.run(cfg)
    save_mgrs_tiles_ni.run(cfg)


def frame_bound(cfg, filtered, reference_water, no_data):
    """OPERA's dark-water bound, computed over rasters that cover the whole frame."""
    from dswx_sar.common import _initial_threshold as it
    pc = cfg.groups.processing
    thres_max, mean, std, bimodal = it.compute_threshold_max_bound(
        intensity_path=filtered, reference_water_path=reference_water,
        water_max_value=pc.reference_water.max_value,
        water_threshold=pc.reference_water.permanent_water_value,
        no_data_path=no_data, lines_per_block=pc.initial_threshold.line_per_block)
    return dict(pols=polarizations(cfg), thres_max=[float(v) for v in thres_max], mean=[float(v) for v in mean],
                std=[float(v) for v in std], is_bimodal=bool(bimodal))


def interpolate_field(cfg, blocks, rows, cols, name, out_dir):
    """OPERA's own threshold interpolation, run once on the frame's block grid.
    Writes <out_dir>/<name>_<pol>.tif for every polarization."""
    from dswx_sar.common import _initial_threshold as it
    by, bx = block_size(cfg)
    pols = polarizations(cfg)
    it.fill_threshold_with_gdal(threshold_array={"array": np.array(blocks, dtype="float64"),
                                                 "block_row": by, "block_col": bx},
                                rows=rows, cols=cols, filename=name, outputdir=out_dir, pol_list=pols,
                                filled_value=None, no_data=NODATA, average_tile=True)
    return [os.path.join(out_dir, f"{name}_{p}.tif") for p in pols]
