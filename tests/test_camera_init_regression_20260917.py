from plugins.camera.profile import ProfilePlugin


def _profile_plugin(commands):
    plugin = object.__new__(ProfilePlugin)
    plugin.commands = commands
    plugin.profile = {"model": "TEST"}
    plugin.name = "profile-test"
    return plugin


def test_profile_init_raw_uses_characterized_default_not_generic_raw():
    plugin = _profile_plugin({
        "raw": {
            "path": "/main/capturesettings/imagequality",
            "value": "NEF (Raw)",
        },
        "iso": {
            "path": "/main/imgsettings/iso",
            "value": "100",
            "values": {"100": "100"},
        },
    })

    captured = {}

    def preflight(required):
        captured.update(required)
        return {"ok": True}

    plugin.preflight = preflight

    result = plugin.init_settings(
        iso="100",
        image_format="RAW",
        white_balance=None,
    )

    assert result == {"ok": True}
    assert captured == {
        "iso": "100",
        "image_format": "NEF (Raw)",
    }


def test_profile_init_rejects_non_raw_acquisition():
    plugin = _profile_plugin({
        "raw": {
            "path": "/main/capturesettings/imagequality",
            "value": "NEF (Raw)",
        },
        "iso": {
            "path": "/main/imgsettings/iso",
            "value": "100",
            "values": {"100": "100"},
        },
    })

    try:
        plugin.init_settings(image_format="JPEG")
    except Exception as exc:
        assert "RAW acquisition" in str(exc)
    else:
        raise AssertionError("JPEG initialization must be rejected")
