"""Plan a frame run: windows on the frame grid, one shared ancillary set, one runconfig
per window and date. The plan file is the single description of a run directory.

Layout of a run directory:
  frame_plan.json
  _ancillary/{dem,hand,worldcover,reference_water}.tif
  _frame/<tag>/                    frame bound, frame threshold field
  w<row><col>/dswx_input/<tag>.h5  the window, GCOV-shaped
  w<row><col>/dswx_ni/<tag>/       runconfig.yaml, scratch/, sas_output/, state.json, logs
  frame_WTR_<tag>.tif ...          written by the reduce
"""
from __future__ import annotations

import copy
import json
import os
from importlib import resources
from pathlib import Path

import yaml

from . import ancillary, gcov

PLAN = "frame_plan.json"


def load(run_dir):
    return json.load(open(os.path.join(run_dir, PLAN)))


def unit_dir(run_dir, window, tag):
    return os.path.join(run_dir, window, "dswx_ni", tag)


def frame_dir(run_dir, tag):
    return os.path.join(run_dir, "_frame", tag)


def kept_windows(plan):
    return [w for w, v in plan["windows"].items() if not v.get("skipped")]


def _runconfig(template, window_h5, anc, algo, odir, output_spacing):
    cfg = copy.deepcopy(template)
    g = cfg["runconfig"]["groups"]
    g["input_file_group"]["input_file_path"] = [str(window_h5)]
    d = g["dynamic_ancillary_file_group"]
    d["dem_file"] = anc["dem"]
    d["hand_file"] = anc["hand"]
    d["worldcover_file"] = anc["worldcover"]
    d["reference_water_file"] = anc["reference_water"]
    d["algorithm_parameters"] = str(algo)
    st = g["static_ancillary_file_group"]
    st["static_ancillary_inputs_flag"] = False
    st["mgrs_database_file"] = None
    st["mgrs_collection_database_file"] = None
    g["primary_executable"]["product_type"] = "dswx_ni"
    p = g["product_path_group"]
    p["product_path"] = str(odir / "product")
    p["scratch_path"] = str(odir / "scratch")
    p["sas_output_path"] = str(odir / "sas_output")
    p["product_version"] = 0.3
    p["output_spacing"] = output_spacing
    g["browse_image_group"]["save_tif_to_output"] = True
    g["log_file"] = str(odir / "dswx_ni.log")
    return cfg


def make(sources, run_dir, window_km=96.0, overlap_km=10.0, min_valid=0.02, output_spacing=20,
         anc_cache=None, algorithm_parameters=None, two_pass=True, log=print):
    """Cut every date of a frame into windows and write the plan.

    sources: {tag: path of a GCOV .h5}. Windows are placed on the grid of the first source;
    every other date is cut at the same map coordinates."""
    run_dir = Path(run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    data = resources.files("overture") / "data"
    template = yaml.safe_load((data / "runconfig_template.yaml").read_text())
    algo = Path(algorithm_parameters).resolve() if algorithm_parameters else run_dir / "algorithm_parameter_ni.yaml"
    if not algorithm_parameters and not algo.exists():
        algo.write_text((data / "algorithm_parameter_ni.yaml").read_text())

    tags = list(sources)
    grids = {t: gcov.frame_grid(p) for t, p in sources.items()}
    g0 = grids[tags[0]]
    dx = g0["dx"]
    anc = ancillary.build(g0, str(run_dir / "_ancillary"), anc_cache or str(run_dir / "_ancillary" / "cache"), log=log)

    H, W = g0["y"].size, g0["x"].size
    n = int(round(window_km * 1000 / dx))
    ov = int(round(overlap_km * 1000 / dx))
    nr, nc = min(n, H), min(n, W)
    rows, cols = gcov.window_origins(H, nr, ov), gcov.window_origins(W, nc, ov)

    plan = dict(version=1, two_pass=bool(two_pass), window_px=[nr, nc], overlap_px=ov, posting_m=dx,
                output_spacing=output_spacing, grid=[len(rows), len(cols)], ancillary=anc,
                algorithm_parameters=str(algo),
                tags={t: dict(h5=str(Path(sources[t]).resolve()), shape=[int(g["y"].size), int(g["x"].size)],
                              epsg=g["epsg"]) for t, g in grids.items()},
                windows={})
    for i, r0 in enumerate(rows):
        for j, c0 in enumerate(cols):
            name = f"w{i:02d}{j:02d}"
            vf = gcov.valid_fraction(sources[tags[0]], r0, c0, nr, nc)
            if vf < min_valid:
                plan["windows"][name] = dict(valid_frac=round(vf, 4), skipped=True)
                continue
            rep = dict(valid_frac=round(vf, 4), inputs={})
            for t in tags:
                g = grids[t]
                # same map position on this date's grid (identical pixels when the grids coincide)
                rt = int(round((g["y"][0] - g0["y"][r0]) / dx)) if g is not g0 else r0
                ct = int(round((g0["x"][c0] - g["x"][0]) / dx)) if g is not g0 else c0
                rt = max(0, min(rt, g["y"].size - nr))
                ct = max(0, min(ct, g["x"].size - nc))
                dst = run_dir / name / "dswx_input" / f"{t}.h5"
                dst.parent.mkdir(parents=True, exist_ok=True)
                info = dict(rows=[rt, rt + nr], cols=[ct, ct + nc], h5=str(dst))
                if not dst.exists():
                    tmp = dst.with_suffix(".part.h5")
                    info.update(gcov.cut_window(sources[t], str(tmp), rt, ct, nr, nc))
                    tmp.replace(dst)
                rep["inputs"][t] = info
                odir = Path(unit_dir(run_dir, name, t))
                odir.mkdir(parents=True, exist_ok=True)
                cfg = _runconfig(template, dst, anc, algo, odir, output_spacing)
                yaml.dump(cfg, open(odir / "runconfig.yaml", "w"), default_flow_style=False)
            plan["windows"][name] = rep
            log(f"{name} valid {vf:.3f}")
    json.dump(plan, open(run_dir / PLAN, "w"), indent=1)
    log(f"planned {len(kept_windows(plan))} of {len(rows) * len(cols)} windows, {nr}x{nc} px with {ov} px overlap, "
        f"{len(tags)} dates")
    return plan
