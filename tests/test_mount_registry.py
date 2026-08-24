from plugins.mount import available_plugins


def test_available_plugins_includes_indi():
    plugins = {item["id"]: item["name"] for item in available_plugins()}

    assert plugins["indi"] == "INDI / EQMod compatible"
