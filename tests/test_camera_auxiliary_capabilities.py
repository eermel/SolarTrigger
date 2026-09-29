from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import time

from backend.camera_auxiliary_capabilities import (
    characterize_auxiliary_capabilities,
    format_storage_snapshot,
    read_camera_storage,
    storage_capability_from_snapshot,
    sync_profile_datetime,
)


class FakeWidget:
    def __init__(self, name, value=None, *, label=None, readonly=False, choices=None, widget_type=6, children=None):
        self._name = name
        self._value = value
        self._label = label or name
        self._readonly = readonly
        self._choices = list(choices or [])
        self._type = widget_type
        self._children = list(children or [])

    def get_name(self):
        return self._name

    def get_label(self):
        return self._label

    def get_value(self):
        return self._value

    def set_value(self, value):
        if self._readonly:
            raise RuntimeError("readonly")
        self._value = value

    def get_readonly(self):
        return self._readonly

    def get_choices(self):
        return list(self._choices)

    def get_type(self):
        return self._type

    def get_children(self):
        return list(self._children)

    def get_child_by_name(self, name):
        for child in self._children:
            if child.get_name() == name:
                return child
        raise KeyError(name)


class FakeCamera:
    def __init__(self, leaves):
        settings = FakeWidget("settings", children=leaves, widget_type=0)
        self.root = FakeWidget("main", children=[settings], widget_type=0)
        self.writes = []

    def get_config(self):
        return self.root

    def set_config(self, config):
        self.writes.append(config)


def _leaf(camera, name):
    settings = camera.root.get_child_by_name("settings")
    return settings.get_child_by_name(name)


def test_characterization_proves_clock_timezone_and_fully_electronic_then_restores():
    now = int(time.time())
    camera = FakeCamera(
        [
            FakeWidget("datetime", now, label="Camera Date and Time", widget_type=8),
            FakeWidget("timezone", "UTC+01:00", choices=["UTC+01:00", "UTC+02:00"]),
            FakeWidget(
                "shuttertype",
                "Mechanical Shutter",
                choices=[
                    "Mechanical Shutter",
                    "Electronic Front Curtain Shutter",
                    "Electronic Shutter",
                ],
            ),
        ]
    )

    capabilities, commands = characterize_auxiliary_capabilities(camera)

    clock = capabilities["clock"]
    assert clock["local_datetime"]["set_proven"] is True
    assert clock["timezone"]["set_proven"] is True
    assert clock["timezone"]["offset_values"]["120"] == "UTC+02:00"
    assert clock["local_sync_supported"] is True

    shutter = capabilities["shutter"]
    assert shutter["mechanical_supported"] is True
    assert shutter["electronic_supported"] is True
    assert shutter["efcs_supported"] is True
    assert shutter["electronic_only_selectable"] is True
    assert commands["shutter_mode"] == {
        "path": "/main/settings/shuttertype",
        "value": "Electronic Shutter",
        "get": True,
        "set": True,
    }

    assert _leaf(camera, "datetime").get_value() == now
    assert _leaf(camera, "timezone").get_value() == "UTC+01:00"
    assert _leaf(camera, "shuttertype").get_value() == "Mechanical Shutter"


def test_efcs_is_not_misclassified_as_fully_electronic():
    camera = FakeCamera(
        [
            FakeWidget(
                "shuttermode",
                "Mechanical",
                choices=["Mechanical", "Electronic Front Curtain"],
            )
        ]
    )
    capabilities, commands = characterize_auxiliary_capabilities(camera)
    shutter = capabilities["shutter"]
    assert shutter["mechanical_supported"] is True
    assert shutter["efcs_supported"] is True
    assert shutter["electronic_supported"] is None
    assert shutter["electronic_only_selectable"] is False
    assert commands == {}


def test_explicit_electronic_shutter_toggle_is_qualified_without_inventing_mechanical_presence():
    camera = FakeCamera([FakeWidget("electronicshutter", 0, choices=[], widget_type=4)])
    capabilities, commands = characterize_auxiliary_capabilities(camera)
    shutter = capabilities["shutter"]
    assert shutter["electronic_supported"] is True
    assert shutter["mechanical_supported"] is None
    assert shutter["electronic_only_selectable"] is True
    assert commands["shutter_mode"]["value"] == 1
    assert _leaf(camera, "electronicshutter").get_value() == 0


def test_missing_optional_controls_are_nonfatal_and_do_not_create_commands():
    camera = FakeCamera([FakeWidget("iso", "100", choices=["100", "200"])])
    capabilities, commands = characterize_auxiliary_capabilities(camera)
    assert capabilities["clock"]["local_sync_supported"] is False
    assert capabilities["shutter"]["control_detected"] is False
    assert commands == {}


def test_runtime_sync_uses_characterized_local_datetime_and_matching_timezone_offset():
    camera = FakeCamera(
        [
            FakeWidget("datetime", 0, widget_type=8),
            FakeWidget("timezone", "UTC+01:00", choices=["UTC+01:00", "UTC+02:00"]),
        ]
    )
    profile = {
        "model": "Test Camera",
        "capabilities": {
            "clock": {
                "local_datetime": {
                    "path": "/main/settings/datetime",
                    "set_proven": True,
                },
                "timezone": {
                    "detected": True,
                    "path": "/main/settings/timezone",
                    "set_proven": True,
                    "choices": ["UTC+01:00", "UTC+02:00"],
                    "offset_values": {"60": "UTC+01:00", "120": "UTC+02:00"},
                },
            }
        },
    }
    local = datetime(2027, 8, 2, 13, 4, 5, tzinfo=timezone(timedelta(hours=2)))
    ref = SimpleNamespace(
        datetime_local=local,
        datetime_utc=local.astimezone(timezone.utc),
        timezone_name="Africa/Cairo",
        utc_offset_minutes=120,
    )

    result = sync_profile_datetime(camera, profile, ref, plugin_name="profile-test")

    expected_wallclock = int(time.mktime(local.replace(tzinfo=None).timetuple()))
    assert _leaf(camera, "datetime").get_value() == expected_wallclock
    assert _leaf(camera, "timezone").get_value() == "UTC+02:00"
    assert result["status"] == "ok"
    assert result["datetime_synced"] is True
    assert result["timezone_synced"] is True
    assert result["datetime_applied"] == local.isoformat()


def test_runtime_sync_refuses_unproven_local_datetime():
    camera = FakeCamera([FakeWidget("datetime", 0, widget_type=8)])
    profile = {
        "model": "Test Camera",
        "capabilities": {
            "clock": {
                "local_datetime": {
                    "path": "/main/settings/datetime",
                    "set_proven": False,
                }
            }
        },
    }
    local = datetime.now(timezone.utc)
    ref = SimpleNamespace(
        datetime_local=local,
        datetime_utc=local,
        timezone_name="UTC",
        utc_offset_minutes=0,
    )
    result = sync_profile_datetime(camera, profile, ref)
    assert result["status"] == "unsupported"
    assert result["datetime_synced"] is False
    assert camera.writes == []


def test_profile_preflight_applies_characterized_electronic_shutter_before_plan():
    import pytest

    profile_module = pytest.importorskip("plugins.camera.profile")
    ProfilePlugin = profile_module.ProfilePlugin

    camera = FakeCamera(
        [
            FakeWidget("manual", "M", choices=["M"]),
            FakeWidget("target", "card+sdram", choices=["card+sdram"]),
            FakeWidget("format", "RAW", choices=["RAW"]),
            FakeWidget("iso", "100", choices=["100", "200"]),
            FakeWidget("speed", "1/500", choices=["1/500"]),
            FakeWidget(
                "shuttertype",
                "Mechanical Shutter",
                choices=["Mechanical Shutter", "Electronic Shutter"],
            ),
        ]
    )
    profile = {
        "schema_version": 1,
        "config_type": "camera_profile",
        "backend": "profile-test_camera",
        "manufacturer": "Test",
        "model": "Test Camera",
        "strategy": "sequential",
        "commands": {
            "manual_mode": {"path": "/main/settings/manual", "value": "M"},
            "capture_target": {"path": "/main/settings/target", "value": "card+sdram"},
            "raw": {"path": "/main/settings/format", "value": "RAW"},
            "iso": {"path": "/main/settings/iso", "values": {"100": "100", "200": "200"}},
            "shutter": {"path": "/main/settings/speed", "values": {"1/500": "1/500"}},
            "shutter_mode": {
                "path": "/main/settings/shuttertype",
                "value": "Electronic Shutter",
                "get": True,
                "set": True,
            },
            "trigger_single": {"method": "trigger_capture"},
        },
        "brackets": {},
    }

    plugin = ProfilePlugin(camera, log_fn=lambda *_args, **_kwargs: None, profile=profile)
    result = plugin.preflight()

    assert _leaf(camera, "shuttertype").get_value() == "Electronic Shutter"
    assert "shutter_mode" in result["changed"]


def test_auxiliary_shutter_probe_accepts_initial_discovery_without_label_norm():
    camera = FakeCamera(
        [
            FakeWidget(
                "shuttertype",
                "Mechanical",
                choices=["Mechanical", "Electronic"],
            )
        ]
    )
    items = [
        {
            "path": "/main/settings/shuttertype",
            "name": "shuttertype",
            "label": "Shutter Type",
            "value": "Mechanical",
            "choices": ["Mechanical", "Electronic"],
            "readonly": True,
        }
    ]

    capabilities, commands = characterize_auxiliary_capabilities(
        camera, items=items
    )

    assert capabilities["shutter"]["control_detected"] is True
    assert capabilities["shutter"]["mechanical_supported"] is True
    assert capabilities["shutter"]["electronic_supported"] is True
    assert commands == {}


def test_auxiliary_clock_probe_accepts_initial_discovery_without_widget_metadata():
    now = int(time.time())
    camera = FakeCamera(
        [FakeWidget("datetime", now, label="Camera Date and Time", widget_type=8)]
    )
    items = [
        {
            "path": "/main/settings/datetime",
            "name": "datetime",
            "config_name": "datetime",
            "label": "Camera Date and Time",
            "value": now,
            "choices": [],
            "readonly": False,
        }
    ]

    capabilities, commands = characterize_auxiliary_capabilities(
        camera, items=items
    )

    assert capabilities["clock"]["local_datetime"]["detected"] is True
    assert capabilities["clock"]["local_datetime"]["date_widget"] is True
    assert capabilities["clock"]["local_datetime"]["set_proven"] is True
    assert capabilities["clock"]["local_sync_supported"] is True
    assert commands == {}
    assert _leaf(camera, "datetime").get_value() == now


class FakeStorageInfo:
    def __init__(
        self,
        *,
        basedir="/store_00010001",
        label="SD Card",
        description="Memory card",
        capacitykbytes=64 * 1024 * 1024,
        freekbytes=16 * 1024 * 1024,
        freeimages=1234,
    ):
        self.basedir = basedir
        self.label = label
        self.description = description
        self.capacitykbytes = capacitykbytes
        self.freekbytes = freekbytes
        self.freeimages = freeimages


def test_storage_snapshot_reports_capacity_free_space_and_free_images():
    camera = FakeCamera([])
    camera.get_storageinfo = lambda: [
        FakeStorageInfo(
            capacitykbytes=64 * 1024 * 1024,
            freekbytes=0,
            freeimages=0,
        )
    ]

    snapshot = read_camera_storage(camera)

    assert snapshot["supported"] is True
    assert snapshot["status"] == "ok"
    assert snapshot["media_count"] == 1
    assert snapshot["total_capacity_kib"] == 64 * 1024 * 1024
    assert snapshot["total_free_kib"] == 0
    assert snapshot["total_used_kib"] == 64 * 1024 * 1024
    assert snapshot["total_free_percent"] == 0.0
    assert snapshot["total_free_images"] == 0
    assert "free=0.00 GiB" in format_storage_snapshot(snapshot)

    capability = storage_capability_from_snapshot(snapshot)
    assert capability == {
        "query_supported": True,
        "media_count_at_characterization": 1,
        "capacity_reported": True,
        "free_space_reported": True,
        "free_images_reported": True,
    }


def test_storage_snapshot_aggregates_multiple_media_when_all_sizes_are_known():
    camera = FakeCamera([])
    camera.get_storageinfo = lambda: [
        FakeStorageInfo(
            label="Slot 1",
            capacitykbytes=32 * 1024 * 1024,
            freekbytes=8 * 1024 * 1024,
            freeimages=500,
        ),
        FakeStorageInfo(
            label="Slot 2",
            capacitykbytes=64 * 1024 * 1024,
            freekbytes=16 * 1024 * 1024,
            freeimages=900,
        ),
    ]

    snapshot = read_camera_storage(camera)

    assert snapshot["media_count"] == 2
    assert snapshot["total_capacity_kib"] == 96 * 1024 * 1024
    assert snapshot["total_free_kib"] == 24 * 1024 * 1024
    assert snapshot["total_free_percent"] == 25.0
    assert snapshot["total_free_images"] == 1400


def test_storage_snapshot_is_nonfatal_when_camera_does_not_support_query():
    camera = FakeCamera([])

    snapshot = read_camera_storage(camera)

    assert snapshot == {
        "supported": False,
        "status": "unsupported",
        "media": [],
        "media_count": 0,
    }
    capability = storage_capability_from_snapshot(snapshot)
    assert capability["query_supported"] is False
    assert capability["capacity_reported"] is False
    assert capability["free_space_reported"] is False


def test_storage_snapshot_is_nonfatal_when_libgphoto_query_fails():
    camera = FakeCamera([])

    def fail():
        raise RuntimeError("storage unavailable")

    camera.get_storageinfo = fail

    snapshot = read_camera_storage(camera)

    assert snapshot["supported"] is False
    assert snapshot["status"] == "error"
    assert "RuntimeError: storage unavailable" in snapshot["error"]
