"""Command line.

  overture run --h5 <tag>=<gcov.h5> [--h5 <tag>=<gcov.h5> ...] --out <run_dir>
      plan, process and reduce in one go; safe to re-run, finished work is skipped

  overture plan    --h5 TAG=PATH ... --out DIR   cut the windows and write the plan
  overture process DIR                           run every phase of every window
  overture reduce  DIR                           frame products and overlap statistics
  overture status  DIR                           what is done
  overture pack    --hh CHIP.tif ... --like GCOV.h5 --out FAKE.h5
      pack HH/HV GeoTIFF chips into a GCOV-shaped HDF5 that `overture run` accepts

  overture search      [any seppo_nisar_search option]            find and download granules at ASF
  overture credentials [any seppo_earthaccess_credentials option] Earthdata token and AWS credentials
      both hand every option through to openSEPPO unchanged; `overture search -h` lists them
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from . import __version__, fleet, plan as planmod, reduce as reducemod, unit


def _log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _plan_args(p):
    p.add_argument("--h5", action="append", required=True, metavar="TAG=PATH",
                   help="one GCOV granule per date of the same frame; TAG is a label of your choice, e.g. the date")
    p.add_argument("--out", required=True, help="run directory")
    p.add_argument("--window-km", type=float, default=96.0)
    p.add_argument("--overlap-km", type=float, default=10.0)
    p.add_argument("--min-valid", type=float, default=0.02, help="skip windows with less swath than this fraction")
    p.add_argument("--output-spacing", type=int, default=20, help="product posting in metres")
    p.add_argument("--anc-cache", help="directory for downloaded DEM/HAND/WorldCover/GSW tiles")
    p.add_argument("--algorithm-parameters", help="OPERA algorithm parameter yaml (default: the bundled one)")
    p.add_argument("--single-pass", action="store_true",
                   help="each window fits its own thresholds (no frame threshold field)")


def _run_args(p):
    p.add_argument("--jobs", type=int, default=0,
                   help="windows at a time (default: from cores, --threads and free memory)")
    p.add_argument("--threads", type=int, default=8, help="threads per window")
    p.add_argument("--min-free-gb", type=float, default=4.0, help="hold new windows below this much free memory")
    p.add_argument("--tags", nargs="+")


def _sources(a):
    src = dict(kv.split("=", 1) for kv in a.h5)
    for t, p in src.items():
        if not os.path.exists(p):
            sys.exit(f"{t}: {p} not found")
    return src


def cmd_plan(a):
    return planmod.make(_sources(a), a.out, a.window_km, a.overlap_km, a.min_valid, a.output_spacing,
                        a.anc_cache, a.algorithm_parameters, not a.single_pass, _log)


def cmd_process(a, run_dir=None):
    jobs = a.jobs or fleet.default_jobs(a.threads)
    bad = fleet.process(run_dir or a.run_dir, a.tags, jobs, a.threads, a.min_free_gb, _log)
    for (w, t), p in sorted(bad.items()):
        _log(f"FAILED {w} {t} in {p}: see {planmod.unit_dir(run_dir or a.run_dir, w, t)}/{p}.log")
    return bad


def cmd_reduce(a, run_dir=None):
    run_dir = run_dir or a.run_dir
    plan = planmod.load(run_dir)
    tags = getattr(a, "tags", None) or list(plan["tags"])
    return reducemod.reduce(run_dir, tags, _log)


def cmd_status(a):
    plan = planmod.load(a.run_dir)
    print(f"{a.run_dir}: {len(planmod.kept_windows(plan))} windows, dates {list(plan['tags'])}, "
          f"{'two-pass' if plan['two_pass'] else 'single-pass'}")
    for t in plan["tags"]:
        n = {p: 0 for p in unit.PHASES}
        secs = {p: 0.0 for p in unit.PHASES}
        for w in planmod.kept_windows(plan):
            st = unit.read_state(planmod.unit_dir(a.run_dir, w, t))
            for p in unit.PHASES:
                if st.get(p, {}).get("done"):
                    n[p] += 1
                    secs[p] += st[p].get("seconds", 0)
        print(f"  {t}: " + ", ".join(f"{p} {n[p]} ({secs[p] / 60:.0f} min)" for p in unit.PHASES))
    rp = os.path.join(a.run_dir, reducemod.REPORT)
    if os.path.exists(rp):
        print(json.dumps(json.load(open(rp)), indent=1))


def cmd_run(a):
    t0 = time.time()
    _log(f"overture {__version__}: plan")
    cmd_plan(a)
    bad = cmd_process(a, a.out)
    cmd_reduce(a, a.out)
    _log(f"done in {(time.time() - t0) / 60:.1f} min; {len(bad)} window-dates failed; report: "
         f"{os.path.join(a.out, reducemod.REPORT)}")
    return 1 if bad else 0


def cmd_pack(a):
    from . import chips
    hv = a.hv or [t.replace("HHHH", "HVHV") for t in a.hh]
    missing = [t for t in list(a.hh) + list(hv) if not os.path.exists(t)]
    if missing:
        sys.exit(f"not found: {missing[0]}" + (f" and {len(missing) - 1} more" if len(missing) > 1 else ""))
    chips.pack(a.hh, hv, a.like, a.out, a.time, a.bounds, _log)


SEPPO = {"search": "seppo_nisar_search", "credentials": "seppo_earthaccess_credentials"}


def seppo(tool, args):
    """Hand a command through to openSEPPO with every option unchanged."""
    import shutil
    import subprocess
    exe = shutil.which(SEPPO[tool]) or os.path.join(os.path.dirname(sys.executable), SEPPO[tool])
    if not os.path.exists(exe):
        sys.exit(f"{SEPPO[tool]} not found; install openSEPPO (conda-forge: openseppo)")
    return subprocess.call([exe] + list(args))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in SEPPO:
        sys.exit(seppo(argv[0], argv[1:]))
    ap = argparse.ArgumentParser(prog="overture", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run"); _plan_args(p); _run_args(p); p.set_defaults(f=cmd_run)
    p = sub.add_parser("plan"); _plan_args(p); p.set_defaults(f=cmd_plan)
    p = sub.add_parser("process"); p.add_argument("run_dir"); _run_args(p); p.set_defaults(f=cmd_process)
    p = sub.add_parser("reduce"); p.add_argument("run_dir"); p.add_argument("--tags", nargs="+"); p.set_defaults(f=cmd_reduce)
    p = sub.add_parser("status"); p.add_argument("run_dir"); p.set_defaults(f=cmd_status)
    p = sub.add_parser("pack", help="pack HH/HV GeoTIFF chips into a GCOV-shaped HDF5 for `overture run`")
    p.add_argument("--hh", nargs="+", required=True, help="HH chips (names containing HHHH)")
    p.add_argument("--hv", nargs="+", help="HV chips (default: the HH names with HHHH replaced by HVHV)")
    p.add_argument("--like", required=True, help="a real GCOV granule of the same mode: projection, posting, header")
    p.add_argument("--out", required=True, help="the HDF5 to write")
    p.add_argument("--time", help="acquisition start of the chips, YYYY-MM-DDTHH:MM:SS")
    p.add_argument("--bounds", nargs=4, type=float, metavar=("XMIN", "YMIN", "XMAX", "YMAX"), help="in the granule's projection")
    p.set_defaults(f=cmd_pack)
    for name, tool in SEPPO.items():
        sub.add_parser(name, help=f"pass-through to openSEPPO {tool}", add_help=False)
    a = ap.parse_args(argv)
    r = a.f(a)
    sys.exit(r if isinstance(r, int) else 0)


if __name__ == "__main__":
    main()
