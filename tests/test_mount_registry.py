import json
from pathlib import Path

from plugins.mount import available_plugins


def test_available_plugins_exposes_eqmod_indi_and_direct_onstep():
    plugins = {item["id"]: item["name"] for item in available_plugins()}

    assert plugins["indi"] == "INDI / EQMod"
    assert plugins["onstep"] == "OnStep / Tessek Mini 11 (direct LX200 serial)"



def test_default_indi_server_never_starts_onstep_driver():
    root = Path(__file__).resolve().parents[1]
    config = json.loads(
        (root / "configs" / "indi_default.json").read_text(encoding="utf-8")
    )

    assert "indi_eqmod_telescope" in config["drivers"]
    assert "indi_lx200_OnStep" not in config["drivers"]
