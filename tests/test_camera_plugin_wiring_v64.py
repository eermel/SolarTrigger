from pathlib import Path
import re

ROOT=Path(__file__).parents[1]
TRIGGER=(ROOT/'scripts/eclipse_trigger.py').read_text(encoding='utf-8')
TOTALITY=(ROOT/'scripts/totality_only.py').read_text(encoding='utf-8')


def test_main_and_emergency_paths_use_camera_service():
    assert 'CameraService' in TRIGGER
    assert 'CameraService' in TOTALITY
    assert 'trigger_capture' not in TOTALITY
    assert 'set_config(' not in TOTALITY


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
