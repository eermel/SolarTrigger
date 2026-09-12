from pathlib import Path
import re

ROOT=Path(__file__).parents[1]
TRIGGER=(ROOT/'scripts/eclipse_trigger.py').read_text(encoding='utf-8')
def test_main_trigger_contains_totality_override_path():
    assert 'SIGUSR1' in TRIGGER
    assert 'totality_override' in TRIGGER


def test_trigger_loads_only_the_three_selected_inputs():
    assert 'args.file' in TRIGGER
    assert 'args.camera' in TRIGGER
    assert 'args.exposure_opt' in TRIGGER
    assert '--execution-plan' not in TRIGGER


def test_plugin_owns_brand_specific_shutter_names():
    sony=(ROOT/'plugins/camera/sony.py').read_text(encoding='utf-8')
    nikon=(ROOT/'plugins/camera/nikon.py').read_text(encoding='utf-8')
    assert '"shutterspeed"' in sony
    assert '"shutterspeed2"' in nikon
    assert '"shutterspeed2"' not in TRIGGER


def test_trigger_contains_no_camera_brand_branching():
    lowered = TRIGGER.lower()
    for brand_marker in ('sony', 'nikon', 'canon', 'fujifilm'):
        assert re.search(rf'\b{brand_marker}\b', lowered) is None
