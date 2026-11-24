"""One unit of work: one phase of one window and date, or one frame step.

  python -m overture.unit <run_dir> <tag> <window> <filter|threshold|classify>
  python -m overture.unit <run_dir> <tag> _frame   <bound|field>

A window's phases are recorded in its state.json; a finished phase is not run again.
The scratch directory is the complete state between phases, so a run can stop after
any phase and continue later."""
from __future__ import annotations

import glob
import json
import os
import resource
import sys
import time

import numpy as np

from . import frame, plan as planmod

PHASES = ("filter", "threshold", "classify")


def state_path(udir):
    return os.path.join(udir, "state.json")


def read_state(udir):
    try:
        return json.load(open(state_path(udir)))
    except (OSError, ValueError):
        return {}


def write_state(udir, state):
    tmp = state_path(udir) + ".tmp"
    json.dump(state, open(tmp, "w"), indent=1)
    os.replace(tmp, state_path(udir))


def is_done(run_dir, tag, window, phase):
    if window == "_frame":
        return os.path.exists(os.path.join(planmod.frame_dir(run_dir, tag), f"frame_{phase}.json"))
    return bool(read_state(planmod.unit_dir(run_dir, window, tag)).get(phase, {}).get("done"))


def run_window(run_dir, tag, window, phase, threads=None):
    from . import opera
    plan = planmod.load(run_dir)
    udir = planmod.unit_dir(run_dir, window, tag)
    state = read_state(udir)
    if state.get(phase, {}).get("done"):
        return state[phase]
    for earlier in PHASES[:PHASES.index(phase)]:
        if not state.get(earlier, {}).get("done"):
            raise RuntimeError(f"{window} {tag}: phase {earlier} has not finished")
    cfg = opera.load_cfg(os.path.join(udir, "runconfig.yaml"), threads)
    t = time.time()
    info = {}
    if phase == "filter":
        opera.run_filter(cfg)
    elif phase == "threshold":
        if plan["two_pass"]:
            bfile = os.path.join(planmod.frame_dir(run_dir, tag), "frame_bound.json")
            b = json.load(open(bfile))
            rec = opera.run_threshold(cfg, bound=b if b["usable"] else None, capture=True)
            np.savez(os.path.join(udir, "threshold_blocks.npz"), **rec)
            info["blocks_with_value"] = int(sum((rec[f][..., 0] != opera.NODATA).sum()
                                                for f in opera.FIELDS[:1] if f in rec))
        else:
            opera.run_threshold(cfg)
    elif phase == "classify":
        opera.run_classify(cfg)
        info["tiles"] = len(glob.glob(os.path.join(udir, "sas_output", "*_B01_WTR.tif")))
    else:
        raise ValueError(phase)
    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss + resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    state = read_state(udir)
    state[phase] = dict(done=True, seconds=round(time.time() - t, 1), peak_rss_gb=round(ru / 1e6, 2), **info)
    write_state(udir, state)
    return state[phase]


def main(argv=None):
    run_dir, tag, window, phase = (argv or sys.argv[1:])[:4]
    threads = int(os.environ.get("DSWX_MAX_WORKERS", "0")) or None
    if window == "_frame":
        res = frame.bound(run_dir, tag) if phase == "bound" else frame.field(run_dir, tag)
    else:
        res = run_window(run_dir, tag, window, phase, threads)
    print("UNIT_DONE", json.dumps(dict(window=window, tag=tag, phase=phase, **{k: v for k, v in res.items()
                                                                                if not isinstance(v, (list, dict))})))


if __name__ == "__main__":
    main()
