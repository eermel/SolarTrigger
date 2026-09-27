from plugins.mount import available_plugins


def test_available_plugins_exposes_eqmod_indi_and_direct_onstep():
    plugins = {item["id"]: item["name"] for item in available_plugins()}

    assert plugins["indi"] == "INDI / EQMod"
    assert plugins["onstep"] == "OnStep / Tessek Mini 11 (direct LX200 serial)"
