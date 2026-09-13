from datetime import datetime, timezone
from backend.dryrun_circumstances import generate_debug_now
from backend.phase_trigger import build_phase_schedule
from backend.timeline import build_timeline

def test_debug_contacts_are_exact_and_have_no_diamond_ring_constants():
    result=generate_debug_now(datetime(2026,9,13,12,0,0,tzinfo=timezone.utc))
    assert result["TSTART"]=="12:03:00.000"
    assert result["C1"]=="12:08:12.000"
    assert result["C2"]=="12:14:18.000"
    assert result["TMAX"]=="12:16:00.000"
    assert result["C3"]=="12:17:42.000"
    assert result["C4"]=="12:22:54.000"
    assert result["TEND"]=="12:26:12.000"
    assert not any("diamond" in key.lower() for key in result)

def test_debug_bounds_and_diamond_photo_setup():
    c=generate_debug_now(datetime(2026,9,13,12,0,0,tzinfo=timezone.utc)); tl=build_timeline(c)
    photo={"sequence_margin_min":60,"phases":{"partial":{"interval_s":10},"diamond_ring":{"duration_s":17,"totality_overlap_s":7,"interval_s":1}}}
    s=build_phase_schedule(tl,photo,honor_timeline_bounds=True); w={x.name:x for x in s.windows}
    assert s.tstart==tl["TSTART"] and s.tend==tl["TEND"]
    assert (tl["C2"]-w["diamond_ring_c2"].start).total_seconds()==17
    assert (w["diamond_ring_c2"].end-tl["C2"]).total_seconds()==7
    assert (tl["C3"]-w["diamond_ring_c3"].start).total_seconds()==7
    assert (w["diamond_ring_c3"].end-tl["C3"]).total_seconds()==17
