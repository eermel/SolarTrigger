import json
import sys
import subprocess
from pathlib import Path


def run_check(path):
    return subprocess.run(
        [sys.executable, str(Path('scripts')/ 'eclipse_trigger.py'), '--check', '--file', str(path)],
        capture_output=True, text=True
    )


def test_check_good(tmp_path):
    cfg = {
        "C1": "10:00:00",
        "C2": "11:00:00",
        "TMAX": "11:30:00",
        "C3": "12:00:00",
        "C4": "13:00:00",
    }
    p = tmp_path / 'good.json'
    p.write_text(json.dumps(cfg), encoding='utf-8')
    r = run_check(p)
    assert r.returncode == 0
    assert 'CHECK OK' in r.stdout
    assert 'Audio Jack OK' not in r.stdout
    assert 'pygame' not in r.stdout


def test_check_missing_time(tmp_path):
    cfg = {
        "C1": "10:00:00",
        "C2": "11:00:00",
        # TMAX missing
        "C3": "12:00:00",
        "C4": "13:00:00",
    }
    p = tmp_path / 'bad_missing.json'
    p.write_text(json.dumps(cfg), encoding='utf-8')
    r = run_check(p)
    assert r.returncode == 1
    assert 'Missing TMAX' in r.stdout
    assert 'CHECK OK' not in r.stdout
    assert 'Audio Jack OK' not in r.stdout


def test_check_bad_format(tmp_path):
    cfg = {
        "C1": "10:00:00",
        "C2": "11:00:00",
        "TMAX": "11:xx:00",
        "C3": "12:00:00",
        "C4": "13:00:00",
    }
    p = tmp_path / 'bad_format.json'
    p.write_text(json.dumps(cfg), encoding='utf-8')
    r = run_check(p)
    assert r.returncode == 1
    assert 'Invalid TMAX' in r.stdout
    assert 'Audio Jack OK' not in r.stdout


def test_check_order(tmp_path):
    cfg = {
        "C1": "10:00:00",
        "C2": "12:00:00",
        "TMAX": "12:00:00",  # equal to C2 -> strict order violated
        "C3": "13:00:00",
        "C4": "14:00:00",
    }
    p = tmp_path / 'bad_order.json'
    p.write_text(json.dumps(cfg), encoding='utf-8')
    r = run_check(p)
    assert r.returncode == 1
    assert 'Order error' in r.stdout


def test_check_atmo_missing(tmp_path):
    cfg = {
        "C1": "10:00:00",
        "C2": "11:00:00",
        "TMAX": "11:30:00",
        "C3": "12:00:00",
        "C4": "13:00:00",
        "atmo_compensation": True,
        # Missing some altitudes and altitude_m
        "C1_alt_deg": 30.0,
        "C2_alt_deg": None,
    }
    p = tmp_path / 'atmo_missing.json'
    p.write_text(json.dumps(cfg), encoding='utf-8')
    r = run_check(p)
    assert r.returncode == 1
    assert 'Missing altitudes for atmo_compensation' in r.stdout
    assert 'Missing _circumstances_location.altitude_m' in r.stdout


def test_check_atmo_ok(tmp_path):
    cfg = {
        "C1": "10:00:00",
        "C2": "11:00:00",
        "TMAX": "11:30:00",
        "C3": "12:00:00",
        "C4": "13:00:00",
        "atmo_compensation": True,
        "C1_alt_deg": 30.0,
        "C2_alt_deg": 40.0,
        "TMAX_alt_deg": 45.0,
        "C3_alt_deg": 35.0,
        "C4_alt_deg": 20.0,
        "_circumstances_location": {"altitude_m": 100.0},
    }
    p = tmp_path / 'atmo_ok.json'
    p.write_text(json.dumps(cfg), encoding='utf-8')
    r = run_check(p)
    assert r.returncode == 0
    assert 'CHECK OK' in r.stdout
