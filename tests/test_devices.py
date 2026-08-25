from datetime import datetime, timedelta, timezone
import threading

import pytest

from backend import devices
from backend.state_store import StateStore


def test_ttl_expired_is_strictly_after_72_hours_and_normalizes_dates():
    now = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)

    assert not devices.ttl_expired("2026-08-19T12:00:00Z", now)
    assert devices.ttl_expired("2026-08-19T11:59:59+00:00", now)
    assert not devices.ttl_expired("2026-08-19T12:00:00", now)
    assert not devices.ttl_expired(now - timedelta(hours=72), now)
    assert devices.ttl_expired(None, now)
    assert devices.ttl_expired("not-a-date", now)


@pytest.mark.parametrize(
    ("payload", "expected_plugin", "expected_active"),
    [
        (None, None, False),
        ("", "", False),
        ("none", "none", False),
        ("gpsd", "gpsd", True),
    ],
)
def test_normalize_selection_strings(payload, expected_plugin, expected_active):
    assert devices.normalize_selection(payload) == {
        "plugin": expected_plugin,
        "active": expected_active,
    }


def test_normalize_selection_preserves_mapping_keys_and_overrides_active():
    payload = {"plugin": "onstep", "active": False, "port": "/dev/test"}

    assert devices.normalize_selection(payload) == {
        "plugin": "onstep",
        "active": True,
        "port": "/dev/test",
    }


def _camera_plugin(plugin_id, outcome):
    class Plugin:
        @staticmethod
        def matches(_model):
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    Plugin.plugin_id = plugin_id
    return Plugin


@pytest.mark.parametrize(
    ("plugins", "expected"),
    [
        ([_camera_plugin("sony", True), _camera_plugin("nikon", False)], "sony"),
        ([_camera_plugin("sony", True), _camera_plugin("nikon", True)], None),
        ([_camera_plugin("sony", False)], None),
        ([_camera_plugin("broken", RuntimeError("probe")),
          _camera_plugin("sony", True)], "sony"),
    ],
)
def test_camera_plugin_for_model_requires_one_match(monkeypatch, plugins, expected):
    monkeypatch.setattr(devices.camera, "_load_plugin_classes", lambda: plugins)

    assert devices.camera_plugin_for_model("Test model") == expected


@pytest.mark.parametrize(
    ("matches", "suggested"),
    [
        ([True, False], "camera-0"),
        ([True, True], None),
    ],
)
def test_detect_camera_suggests_only_a_unique_match(monkeypatch, matches, suggested):
    plugins = [
        _camera_plugin(f"camera-{index}", outcome)
        for index, outcome in enumerate(matches)
    ]
    monkeypatch.setattr(devices.camera, "_load_plugin_classes", lambda: plugins)

    result = devices.detect_camera("Test model")

    assert result["detected"] is True
    assert result["suggested_plugin"] == suggested


def test_detect_camera_autodetects_model_when_missing(monkeypatch):
    monkeypatch.setattr(
        devices.camera,
        "get_camera_model",
        lambda _camera: "Detected model",
    )
    monkeypatch.setattr(
        devices,
        "camera_plugin_for_model",
        lambda model: "camera-auto" if model == "Detected model" else None,
    )

    result = devices.detect_camera()

    assert result["detected"] is True
    assert result["detected_model"] == "Detected model"
    assert result["suggested_plugin"] == "camera-auto"


class _Probe:
    def __init__(self, outcome):
        self.outcome = outcome

    def probe(self):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.mark.parametrize(
    ("outcomes", "detected", "suggested"),
    [
        ({"gpsd": True, "nmea": False}, True, "gpsd"),
        ({"gpsd": True, "nmea": True}, True, None),
        ({"gpsd": False, "nmea": False}, False, None),
        ({"gpsd": RuntimeError("unavailable"), "nmea": True}, True, "nmea"),
    ],
)
def test_detect_gps_probes_registry_classes(monkeypatch, outcomes, detected, suggested):
    registry = {plugin_id: _Probe(outcome) for plugin_id, outcome in outcomes.items()}
    monkeypatch.setattr(devices.gps, "available_plugins", lambda: registry)

    result = devices.detect_gps()

    assert result["detected"] is detected
    assert result["detected_info"] == [
        plugin_id for plugin_id, outcome in outcomes.items() if outcome is True
    ]
    assert result["suggested_plugin"] == suggested


@pytest.mark.parametrize("category", ["focuser", "mount"])
@pytest.mark.parametrize(
    ("outcomes", "detected", "suggested"),
    [
        ({"first": True, "second": False}, True, "first"),
        ({"first": True, "second": True}, True, None),
        ({"first": False, "second": RuntimeError("unavailable")}, False, None),
    ],
)
def test_detect_loaded_registry_plugins(
    monkeypatch, category, outcomes, detected, suggested
):
    registry_module = getattr(devices, category)
    loader_name = "load_focuser" if category == "focuser" else "load_mount"
    monkeypatch.setattr(
        registry_module,
        "available_plugins",
        lambda: [{"id": "none"}] + [{"id": key} for key in outcomes],
    )
    monkeypatch.setattr(
        registry_module,
        loader_name,
        lambda plugin_id, log_fn: _Probe(outcomes[plugin_id]),
    )

    result = getattr(devices, f"detect_{category}")()

    assert result["detected"] is detected
    assert result["suggested_plugin"] == suggested


def test_detect_all_isolates_timeout_and_error(monkeypatch):
    blocked = threading.Event()
    good_result = {
        "detected": True,
        "detected_info": "ready",
        "detected_model": None,
        "suggested_plugin": "unique",
    }

    def wait_forever_for_test():
        blocked.wait(1)
        return good_result

    monkeypatch.setattr(
        devices,
        "DETECTORS",
        {
            "camera": wait_forever_for_test,
            "gps": lambda: good_result,
            "focuser": lambda: (_ for _ in ()).throw(RuntimeError("failed")),
            "mount": lambda: good_result,
        },
    )
    try:
        result = devices.detect_all(
            {"camera": 0.01, "gps": 0.2, "focuser": 0.2, "mount": 0.2}
        )
    finally:
        blocked.set()

    assert result["camera"]["detected_info"] == {"timeout": True}
    assert result["gps"] == good_result
    assert result["focuser"]["detected_info"] == {"error": "failed"}
    assert result["mount"] == good_result


def test_detect_all_applies_independent_category_deadlines(monkeypatch):
    release = threading.Event()

    def delayed():
        release.wait(0.05)
        return {"detected": True, "detected_info": None,
                "detected_model": None, "suggested_plugin": "found"}

    monkeypatch.setattr(devices, "DETECTORS", dict.fromkeys(devices.CATEGORIES, delayed))
    timer = threading.Timer(0.03, release.set)
    timer.start()
    try:
        result = devices.detect_all(
            {"camera": 0.01, "gps": 0.1, "focuser": 0.1, "mount": 0.1}
        )
    finally:
        release.set()
        timer.cancel()

    assert result["camera"]["detected_info"] == {"timeout": True}
    assert all(result[name]["detected"] for name in ("gps", "focuser", "mount"))


def test_detection_only_probes_without_persisting_or_acting(monkeypatch):
    calls = []

    class ReadOnlyPlugin:
        def __init__(self, plugin_id):
            self.plugin_id = plugin_id

        def probe(self):
            calls.append((self.plugin_id, "probe"))
            return True

        def connect(self):
            pytest.fail("detection must not connect hardware")

        def move(self, *_args):
            pytest.fail("detection must not move hardware")

        def start_tracking(self, *_args):
            pytest.fail("detection must not start hardware")

        def trigger(self, *_args):
            pytest.fail("detection must not trigger hardware")

    monkeypatch.setattr(
        StateStore, "save", lambda _self: pytest.fail("detection must not persist state")
    )
    monkeypatch.setattr(devices.camera, "_load_plugin_classes", lambda: [])
    monkeypatch.setattr(
        devices.gps,
        "available_plugins",
        lambda: {"gps-reader": ReadOnlyPlugin("gps-reader")},
    )
    for category, loader_name in (("focuser", "load_focuser"), ("mount", "load_mount")):
        registry = getattr(devices, category)
        monkeypatch.setattr(
            registry,
            "available_plugins",
            lambda category=category: [{"id": category}],
        )
        monkeypatch.setattr(
            registry,
            loader_name,
            lambda plugin_id, log_fn: ReadOnlyPlugin(plugin_id),
        )

    result = devices.detect_all(dict.fromkeys(devices.CATEGORIES, 0.5))

    assert result["camera"]["detected"] is False
    assert all(result[name]["detected"] for name in ("gps", "focuser", "mount"))
    assert sorted(calls) == [
        ("focuser", "probe"),
        ("gps-reader", "probe"),
        ("mount", "probe"),
    ]
