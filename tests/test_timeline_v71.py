from tests.frontend_source import frontend_source
from datetime import date, datetime, timedelta
from pathlib import Path
import json

from backend.timeline import (
    parse_hms_seconds, format_hms_ms, build_timeline, rebase_timeline, sequence_seconds
)
from backend.trigger_service import validate_eclipse
from scripts.eclipse_calculator_jubier import generate_json


def test_parse_decimal_seconds_without_truncation():
    assert abs(parse_hms_seconds('09:21:15.682') - (9*3600+21*60+15.682)) < 1e-9


def test_build_timeline_uses_date_plus_independent_hms():
    cfg={'_date':'2027-08-02','TSTART':'08:00:00.125','C1':'08:10:00.250','C2':'09:00:00.375',
         'TMAX':'09:03:00.500','C3':'09:06:00.625','C4':'10:00:00.750','TEND':'11:00:00.875'}
    tl=build_timeline(cfg)
    assert tl['C2'] == datetime(2027,8,2,9,0,0,375000)
    assert format_hms_ms(tl['TEND']) == '11:00:00.875'


def test_dryrun_rebase_preserves_every_interval_to_microsecond():
    cfg={'_date':'2027-08-02','TSTART':'08:00:00.125','C1':'08:10:00.250','C2':'09:00:00.375',
         'TMAX':'09:03:00.500','C3':'09:06:00.625','C4':'10:00:00.750','TEND':'11:00:00.875'}
    real=build_timeline(cfg)
    rebased=rebase_timeline(real, datetime(2026,8,20,12,0,30,333000))
    keys=list(real)
    for a,b in zip(keys,keys[1:]):
        assert rebased[b]-rebased[a] == real[b]-real[a]
    assert rebased['TSTART'] == datetime(2026,8,20,12,0,30,333000)


def test_midnight_rollover_with_decimal_seconds():
    cfg={'TSTART':'23:59:58.900','C1':'23:59:59.100','C2':'23:59:59.500',
         'C3':'00:00:00.250','C4':'00:00:01.125','TEND':'00:00:02.875'}
    validate_eclipse(cfg)
    seq=sequence_seconds(cfg)
    assert abs((seq[3]-seq[2]) - 0.750) < 1e-9


def test_calculator_json_keeps_ms_and_location(tmp_path):
    res={
        'C1_utc':'08:24:10.347','C2_utc':'09:21:15.682','TMAX_utc':'09:24:28.126',
        'C3_utc':'09:27:42.491','C4_utc':'10:28:50.903',
        'C1_local':'11:24:10.347','C2_local':'12:21:15.682','TMAX_local':'12:24:28.126',
        'C3_local':'12:27:42.491','C4_local':'13:28:50.903',
        'eclipse_type':'Totale','magnitude':1.0,'moon_sun_ratio':1.02,
        'duration_str':'6m 26s','sun_alt_tmax':'74.0°'
    }
    out=tmp_path/'e.json'
    cfg=generate_json(res, 23.0, 35.0, 12.5, 3, '2027-08-02', str(out))
    assert cfg['C2']=='09:21:15.682'
    assert cfg['_date']=='2027-08-02'
    assert cfg['_circumstances_location']['latitude']==23.0
    assert cfg['_circumstances_location']['longitude']==35.0
    assert cfg['_circumstances_location']['altitude_m']==12.5
    assert 'contacts_utc' not in cfg


def test_frontend_preserves_decimal_contacts_and_dryrun_route():
    root=Path(__file__).resolve().parents[1]
    html=frontend_source()
    app=(root/'flask_app/app.py').read_text(encoding='utf-8')

    # Les contacts ne sont plus validés par l'ancien regexp d'input.
    # Leur parsing numérique accepte les secondes décimales et leur
    # restitution conserve la précision milliseconde.
    assert "hms.split(':').map(Number)" in html
    assert "sec.toFixed(3)" in html

    assert '/api/trigger/dryrun' in html
    assert '@app.route("/api/trigger/dryrun"' in app



def test_partial_calculator_json_keeps_c2_c3_null(tmp_path):
    res = {
        "C1_utc": "17:22:13.063",
        "C2_utc": None,
        "TMAX_utc": "18:17:18.675",
        "C3_utc": None,
        "C4_utc": "19:09:25.103",
        "C1_local": "19:22:13.063",
        "C2_local": None,
        "TMAX_local": "20:17:18.675",
        "C3_local": None,
        "C4_local": "21:09:25.103",
        "eclipse_type": "Partielle",
        "magnitude": 0.9,
        "moon_sun_ratio": 1.0,
        "duration_str": "0m 0s",
        "sun_alt_tmax": "7.6°",
        "C1_alt_deg": 16.5,
        "C2_alt_deg": None,
        "TMAX_alt_deg": 7.6,
        "C3_alt_deg": None,
        "C4_alt_deg": -0.5,
    }

    out = tmp_path / "partial.json"
    cfg = generate_json(
        res,
        48.87,
        2.38,
        69,
        2,
        "2026-08-12",
        str(out),
    )

    assert cfg["C2"] is None
    assert cfg["C3"] is None
    assert cfg["C2_local"] is None
    assert cfg["C3_local"] is None
    assert cfg["C2_alt_deg"] is None
    assert cfg["C3_alt_deg"] is None

    timeline = build_timeline(cfg)
    assert "C2" not in timeline
    assert "C3" not in timeline
    assert timeline["C1"] < timeline["TMAX"] < timeline["C4"]
