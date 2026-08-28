from plugins.mount.indi_client import IndiSubprocessClient


def test_device_pattern_prefixes_relative_pattern():
    assert (
        IndiSubprocessClient._device_pattern("EQMod Mount", "CONNECTION.*")
        == "EQMod Mount.CONNECTION.*"
    )


def test_device_pattern_preserves_qualified_pattern():
    pattern = "EQMod Mount.EQUATORIAL_COORD.*"

    assert IndiSubprocessClient._device_pattern("EQMod Mount", pattern) == pattern


def test_parse_props_groups_properties_by_device():
    output = (
        "EQMod Mount.CONNECTION.CONNECT=On\n"
        "EQMod Mount.EQUATORIAL_COORD.RA=12.5\n"
        "Another Mount.CONNECTION.CONNECT=Off\n"
        "Another Mount.EQUATORIAL_COORD.RA=3.25\n"
    )

    assert IndiSubprocessClient._parse_props(output) == {
        "EQMod Mount": {
            "CONNECTION": {"CONNECT": "On"},
            "EQUATORIAL_COORD": {"RA": "12.5"},
        },
        "Another Mount": {
            "CONNECTION": {"CONNECT": "Off"},
            "EQUATORIAL_COORD": {"RA": "3.25"},
        },
    }
