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


def test_frontend_accepts_decimal_contact_input_and_dryrun_route():
    root=Path(__file__).resolve().parents[1]
    html=(root/'flask_app/templates/index.html').read_text(encoding='utf-8')
    app=(root/'flask_app/app.py').read_text(encoding='utf-8')
    assert r'(?:\.\d{1,3})?' in html
    assert '/api/trigger/dryrun' in html
    assert '@app.route("/api/trigger/dryrun"' in app
