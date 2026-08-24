import re
from pathlib import Path


INDEX_HTML = (
    Path(__file__).resolve().parents[1] / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")


def _source_between(start, end):
    start_index = INDEX_HTML.index(start)
    return INDEX_HTML[start_index : INDEX_HTML.index(end, start_index)]


def test_status_reanchor_fetches_backend_time_and_updates_anchor():
    source = _source_between(
        "async function _reanchorClockFromStatus()",
        "socket.on('connect'",
    )

    assert re.search(r"fetch\(['\"]/api/status['\"]\)", source)
    assert re.search(
        r"const\s+status\s*=\s*await\s+response\.json\(\).*?"
        r"updateTime\(status\.time\)",
        source,
        re.DOTALL,
    )


def test_connect_and_reconnect_reanchor_from_status():
    source = _source_between("socket.on('connect'", "socket.on('disconnect'")

    assert re.search(r"fetch\(['\"]/api/status['\"]\)", source)
    assert re.search(r"updateTime\(status\.time\)", source)
    assert re.search(
        r"socket\.io\.on\(['\"]reconnect['\"],\s*_reanchorClockFromStatus\)",
        INDEX_HTML,
    )


def test_clock_reset_reanchors_directly_from_new_epochs():
    reset_handler = _source_between("socket.on('clock_reset'", "socket.on('status_update'")
    reset_helper = _source_between(
        "function _reanchorClockFromReset(payload)",
        "function updateGPS(",
    )

    assert "_reanchorClockFromReset(d);" in reset_handler
    assert re.search(
        r"updateTime\(\{.*?backend_utc_epoch_ms:\s*payload\.new_utc_epoch_ms,.*?"
        r"backend_local_epoch_ms:\s*payload\.new_local_epoch_ms",
        reset_helper,
        re.DOTALL,
    )


def test_synced_gps_without_time_fetches_status_before_reanchoring():
    source = _source_between("socket.on('gps_update'", "socket.on('trigger_phase'")

    assert re.search(r"d\.synced\s*===\s*true", source)
    assert re.search(
        r"if\s*\(d\.time\)\s*updateTime\(d\.time\);\s*"
        r"else\s+_reanchorClockFromStatus\(\)",
        source,
    )


def test_visible_tab_reanchors_from_status():
    source = _source_between(
        "document.addEventListener('visibilitychange'",
        "socket.on('eclipse_calculated'",
    )

    assert re.search(
        r"if\s*\(!document\.hidden\)\s*_reanchorClockFromStatus\(\)",
        source,
    )
