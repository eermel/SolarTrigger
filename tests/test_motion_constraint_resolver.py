import pytest

from backend.motion_constraint_resolver import resolve_motion_constraint


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ({"photo": {"anti_trailing_enabled": False}}, "none"),
        ({"photo": {"anti_trailing_enabled": True}, "devices": {}}, "fixed_trailing"),
        (
            {
                "photo": {"anti_trailing_enabled": True},
                "devices": {
                    "mount": {
                        "control": "none",
                        "geometry": "equatorial",
                        "tracking": "solar",
                    }
                },
            },
            "fixed_trailing",
        ),
        (
            {
                "photo": {"anti_trailing_enabled": True},
                "devices": {
                    "mount": {
                        "control": "indi",
                        "geometry": "equatorial",
                        "tracking": "off",
                    }
                },
            },
            "fixed_trailing",
        ),
        (
            {
                "photo": {"anti_trailing_enabled": True},
                "devices": {
                    "mount": {
                        "control": "indi",
                        "geometry": "equatorial",
                        "tracking": "solar",
                    }
                },
            },
            "none",
        ),
        (
            {
                "photo": {"anti_trailing_enabled": True},
                "devices": {
                    "mount": {
                        "control": "indi",
                        "geometry": "equatorial",
                        "tracking": "sidereal",
                    }
                },
            },
            "none",
        ),
        (
            {
                "photo": {"anti_trailing_enabled": True},
                "devices": {
                    "mount": {
                        "control": "indi",
                        "geometry": "altaz",
                        "tracking": "solar",
                    }
                },
            },
            "field_rotation",
        ),
        (
            {
                "photo": {"anti_trailing_enabled": True},
                "devices": {
                    "mount": {
                        "control": "indi",
                        "geometry": "altaz",
                        "tracking": "sidereal",
                    }
                },
            },
            "field_rotation",
        ),
        (
            {
                "photo": {"anti_trailing_enabled": True},
                "devices": {
                    "mount": {
                        "control": "indi",
                        "geometry": "unknown",
                        "tracking": "unknown",
                    }
                },
            },
            "none",
        ),
    ],
)
def test_resolve_motion_constraint(policy, expected):
    assert resolve_motion_constraint(policy) == expected
