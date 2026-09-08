from backend.camera_profiles import one_ev_iso_values


def test_one_ev_iso_values_uses_only_real_supported_full_stops():
    profile = {
        "commands": {
            "iso": {
                "values": {
                    "100": "100",
                    "125": "125",
                    "160": "160",
                    "200": "200",
                    "250": "250",
                    "400": "400",
                    "800": "800",
                    "1600": "1600",
                    "3200": "3200",
                    "6400": "6400",
                    "12800": "12800",
                    "20000": "20000",
                    "25600": "25600",
                    "51200": "51200",
                }
            }
        }
    }

    assert one_ev_iso_values(profile) == [
        100, 200, 400, 800, 1600, 3200, 6400, 12800, 25600
    ]


def test_one_ev_iso_values_skips_missing_camera_step():
    profile = {"commands": {"iso": {"values": {"100": "100", "400": "400"}}}}
    assert one_ev_iso_values(profile) == [100, 400]
