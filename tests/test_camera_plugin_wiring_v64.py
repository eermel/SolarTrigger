from pathlib import Path
import re

ROOT=Path(__file__).parents[1]
TRIGGER=(ROOT/'scripts/eclipse_trigger.py').read_text(encoding='utf-8')
def test_main_trigger_contains_totality_override_path():
    assert '_photo_override_event' in TRIGGER
    assert 'SIGUSR1' in TRIGGER
    assert 'TOTALITY OVERRIDE' in TRIGGER


def test_camera_profile_is_applied_before_eclipse_file():
    # Camera profile is applied before eclipse file: sequence values win.
    assert TRIGGER.index('if args.camera:') < TRIGGER.index('if args.file:')


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
