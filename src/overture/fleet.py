"""Run units as separate processes, several at a time, within a memory budget."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import plan as planmod, unit


def free_gb():
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable"):
            return int(line.split()[1]) / 1e6
    return float("inf")


def _env(threads):
    env = dict(os.environ, OMP_NUM_THREADS=str(threads), DSWX_MAX_WORKERS=str(threads), PYTHONNOUSERSITE="1")
    env.setdefault("DSWX_USE_GPU", "0")
    for var, sub in (("PROJ_DATA", "share/proj"), ("GDAL_DATA", "share/gdal")):
        p = Path(sys.prefix) / sub
        if var not in env and p.is_dir():
            env[var] = str(p)
    if "PROJ_DATA" in env:
        env.setdefault("PROJ_LIB", env["PROJ_DATA"])
    src = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join([src] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    return env


def run_units(run_dir, units, jobs=4, threads=8, min_free_gb=4.0, log=print):
    """units: list of (window, tag, phase). Finished units are skipped. Returns the failed ones."""
    todo = [u for u in units if not unit.is_done(run_dir, u[1], u[0], u[2])]
    skipped = len(units) - len(todo)
    if skipped:
        log(f"  {skipped} already done")
    failed, lock, gate = [], threading.Lock(), threading.Lock()
    env = _env(threads)

    def one(u):
        window, tag, phase = u
        with gate:  # start one at a time, and only when memory allows
            waited = 0
            while free_gb() < min_free_gb and waited < 3600:
                time.sleep(10)
                waited += 10
            time.sleep(1)
        udir = planmod.frame_dir(run_dir, tag) if window == "_frame" else planmod.unit_dir(run_dir, window, tag)
        os.makedirs(udir, exist_ok=True)
        t = time.time()
        with open(os.path.join(udir, f"{phase}.log"), "w") as lf:
            rc = subprocess.run([sys.executable, "-u", "-m", "overture.unit", str(run_dir), tag, window, phase],
                                env=env, stdout=lf, stderr=subprocess.STDOUT, cwd=udir).returncode
        with lock:
            if rc != 0:
                failed.append(u)
            log(f"  {phase:9s} {window} {tag} {'ok' if rc == 0 else 'FAILED rc=%d' % rc} {time.time() - t:.0f} s")

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as ex:
        list(ex.map(one, todo))
    return failed


def process(run_dir, tags=None, jobs=4, threads=8, min_free_gb=4.0, log=print):
    """Run every phase of every window and date. Returns {(window, tag): phase that failed}."""
    plan = planmod.load(run_dir)
    tags = tags or list(plan["tags"])
    alive = [(w, t) for t in tags for w in planmod.kept_windows(plan)]
    bad = {}

    def stage(phase, name):
        nonlocal alive
        t0 = time.time()
        log(f"{name}: {len(alive)} window-dates, {jobs} at a time")
        for w, t, p in run_units(run_dir, [(w, t, phase) for w, t in alive], jobs, threads, min_free_gb, log):
            bad[(w, t)] = p
        alive = [u for u in alive if u not in bad]
        log(f"{name} done in {time.time() - t0:.0f} s")

    def frame_step(phase):
        f = run_units(run_dir, [("_frame", t, phase) for t in tags], min(len(tags), 2), threads, min_free_gb, log)
        if f:
            raise RuntimeError(f"frame step {phase} failed for {[t for _, t, _ in f]}; see _frame/<tag>/{phase}.log")

    stage("filter", "pass one (mosaic, filter)")
    if plan["two_pass"]:
        frame_step("bound")
    stage("threshold", "threshold stage")
    if plan["two_pass"]:
        frame_step("field")
    stage("classify", "pass two (fuzzy to tiles)")
    return bad
