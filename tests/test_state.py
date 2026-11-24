import json

from overture import plan as planmod, unit


def test_state_roundtrip_and_done(tmp_path):
    run = tmp_path
    udir = planmod.unit_dir(run, "w0000", "wet")
    import os
    os.makedirs(udir)
    assert unit.read_state(udir) == {} and not unit.is_done(run, "wet", "w0000", "filter")
    unit.write_state(udir, {"filter": {"done": True, "seconds": 1.0}})
    assert unit.is_done(run, "wet", "w0000", "filter") and not unit.is_done(run, "wet", "w0000", "classify")
    os.makedirs(planmod.frame_dir(run, "wet"))
    assert not unit.is_done(run, "wet", "_frame", "bound")
    json.dump({}, open(os.path.join(planmod.frame_dir(run, "wet"), "frame_bound.json"), "w"))
    assert unit.is_done(run, "wet", "_frame", "bound")
