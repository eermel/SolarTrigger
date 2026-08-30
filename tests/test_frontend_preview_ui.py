import re
from pathlib import Path


INDEX = (
    Path(__file__).resolve().parents[1]
    / "flask_app"
    / "templates"
    / "index.html"
).read_text(encoding="utf-8")


def _preview_intents_body():
    match = re.search(
        r"function\s+buildPreviewIntents\s*\(\)\s*\{(?P<body>.*?)\n\}",
        INDEX,
        re.DOTALL,
    )
    assert match
    return match.group("body")


def _request_rig_previews_body():
    match = re.search(
        r"async\s+function\s+requestRigPreviews\s*"
        r"\(rigId,\s*intents\)\s*\{"
        r"(?P<body>.*?)\n\}",
        INDEX,
        re.DOTALL,
    )
    assert match
    return match.group("body")


def _render_rig_previews_body():
    match = re.search(
        r"function\s+renderRigPreviews\s*"
        r"\(responseJson,\s*requestedRigId\)\s*\{"
        r"(?P<body>.*?)\n\}",
        INDEX,
        re.DOTALL,
    )
    assert match
    return match.group("body")


def _function_body(name, signature=r"\(.*?\)"):
    match = re.search(
        rf"(?:async\s+)?function\s+{name}\s*{signature}\s*"
        rf"\{{(?P<body>.*?)\n\}}",
        INDEX,
        re.DOTALL,
    )
    assert match
    return match.group("body")


def test_preview_buttons_exist_once_per_rig_and_start_disabled():
    buttons = re.findall(
        r'<button\s+class="[^"]*\brig-preview-button\b[^"]*"'
        r'(?P<attributes>.*?)>(?P<label>.*?)</button>',
        INDEX,
        re.DOTALL,
    )

    assert len(buttons) == 4

    for rig_id, (attributes, label) in enumerate(buttons, start=1):
        assert re.search(rf'\bdata-rig-id="{rig_id}"', attributes)
        assert re.search(r"\bdisabled\b", attributes)
        assert (
            f'onclick="requestRigPreviews({rig_id}, buildPreviewIntents())"'
            in attributes
        )
        assert "Prévisualiser" in label


def test_cfg_photo_has_per_rig_antiblur_controls():
    for rig_id in range(1, 5):
        assert INDEX.count(f'id="rig-{rig_id}-antiblur-switch"') == 1
        assert INDEX.count(f'id="rig-{rig_id}-focal"') == 1
        assert INDEX.count(f'id="rig-{rig_id}-pixel-tolerance"') == 1
        assert INDEX.count(f'id="rig-{rig_id}-iso-comp-switch"') == 1
        assert INDEX.count(f'id="rig-{rig_id}-iso-max"') == 1


def test_pixel_tolerance_is_a_positive_float_control():
    for rig_id in range(1, 5):
        control = re.search(
            rf'<input[^>]*id="rig-{rig_id}-pixel-tolerance"[^>]*>',
            INDEX,
        )
        assert control
        html = control.group(0)
        assert 'type="number"' in html
        assert 'step="0.1"' in html
        assert 'min="0.1"' in html
        assert 'value="1.0"' in html


def test_focal_length_is_positive_float_mm_control():
    for rig_id in range(1, 5):
        control = re.search(
            rf'<input[^>]*id="rig-{rig_id}-focal"[^>]*>',
            INDEX,
        )
        assert control
        html = control.group(0)
        assert 'type="number"' in html
        assert 'step="0.1"' in html
        assert 'min="0.1"' in html


def test_iso_max_has_supported_iso_grid():
    for rig_id in range(1, 5):
        select = re.search(
            rf'<select[^>]*id="rig-{rig_id}-iso-max"[^>]*>'
            rf'(?P<body>.*?)</select>',
            INDEX,
            re.DOTALL,
        )
        assert select

        body = select.group("body")
        for iso in (100, 200, 400, 800, 1600, 3200, 6400):
            assert re.search(rf"<option[^>]*>{iso}</option>", body) or (
                iso == 6400
                and re.search(r"<option\s+selected>6400</option>", body)
            )


def test_global_atmos_switch_persists_to_all_rigs():
    switch = re.search(
        r'<input[^>]*id="cfg-atmo-switch"[^>]*>',
        INDEX,
    )
    assert switch
    assert 'onchange="persistGlobalAtmosFromUi()"' in switch.group(0)

    body = _function_body(
        "persistGlobalAtmos",
        r"\(enabled,\s*showFeedback\s*=\s*true\)",
    )

    assert "[1, 2, 3, 4].map" in body
    assert "photo: {atmos_enabled: Boolean(enabled)}" in body
    assert "fetch('/api/rigs/photo'" in body


def test_photo_configuration_has_get_and_persistence_flow():
    load_body = _function_body("loadRigPhotoConfig", r"\(\)")
    persist_body = _function_body("persistRigPhoto", r"\(rigId\)")
    read_body = _function_body("readRigPhotoConfig", r"\(rigId\)")

    assert "fetch('/api/rigs/photo')" in load_body

    assert "fetch('/api/rigs/photo'" in persist_body
    assert "method: 'POST'" in persist_body
    assert "readRigPhotoConfig(rigId)" in persist_body

    assert "anti_trailing_enabled:" in read_body
    assert "motion_tolerance_px:" in read_body
    assert "iso_compensation_enabled:" in read_body
    assert "iso_max:" in read_body
    assert "focal_length_mm:" in read_body
    assert "atmos_enabled:" in read_body


def test_photo_config_is_loaded_with_rig_devices():
    body = _function_body(
        "loadRigDevices",
        r"\(inventoryOverride\)",
    )

    assert "renderRigDevices(payload, inventory)" in body
    assert "await loadRigPhotoConfig()" in body


def test_preview_buttons_remain_available_independently_of_trigger_enabled():
    body = _function_body("updateRigs", r"\(rigs\)")

    assert (
        "const triggerEnabled = "
        "defaultRig.rig_id === 1 || rig.enabled === true"
        in body
    )

    assert re.search(
        r"if\s*\(cameraColumn\)\s*\{.*?"
        r"cameraColumn\.classList\.toggle\("
        r"'enabled',\s*triggerEnabled\)\s*;.*?"
        r"cameraColumn\.hidden\s*=\s*false\s*;.*?"
        r"querySelector\('\.rig-preview-button'\).*?"
        r"previewButton\.disabled\s*=\s*false\s*;",
        body,
        re.DOTALL,
    )


def test_preview_intents_uses_expected_fields_and_contacts():
    body = _preview_intents_body()

    assert "_readCameraConfig()" in body
    assert re.search(r"eclipse\.C1\s*\|\|\s*eclipse\.TMAX", body)
    assert re.search(r"eclipse\.C2\s*\|\|\s*eclipse\.C3", body)
    assert "deadline: null" in body
    assert "step_ev: phaseConfig.step_ev ?? 1.0" in body
    assert "shutter_min: phaseConfig.shutter_min" in body
    assert "shutter_max: phaseConfig.shutter_max" in body
    assert "iso_target: phaseConfig.iso" in body

    for phase, request_id in (
        ("partial", "preview-partial"),
        ("diamond_ring", "preview-diamond-ring"),
        ("totality", "preview-totality"),
    ):
        assert re.search(
            rf"phase:\s*'{phase}'.*?"
            rf"request_id:\s*'{request_id}'",
            body,
            re.DOTALL,
        )


def test_preview_intents_keep_phase_order_and_build_times_from_eclipse_state():
    body = _preview_intents_body()

    partial = body.index("phase: 'partial'")
    diamond = body.index("phase: 'diamond_ring'")
    totality = body.index("phase: 'totality'")

    assert partial < diamond < totality
    assert "const eclipse = state.eclipse" in body

    assert re.search(
        r"const\s+eclipseDate\s*=\s*eclipse\s*&&\s*"
        r"\(eclipse\._date\s*\|\|\s*eclipse\._date_utc\)",
        body,
    )

    assert "target_time: `${eclipseDate}T${target.contact}Z`" in body


def test_preview_iso_target_comes_from_visible_phase_iso_controls():
    read_body = _function_body("_readCameraConfig", r"\(\)")
    preview_body = _preview_intents_body()

    assert re.search(
        r"iso:\s*parseInt\("
        r"document\.getElementById\(`cfg-\$\{prefix\}-iso`\)"
        r"\.value,\s*10\)",
        read_body,
    )

    for control_id in (
        "cfg-partial-iso",
        "cfg-dr-iso",
        "cfg-tot-iso",
    ):
        assert len(
            re.findall(rf'id=["\']{control_id}["\']', INDEX)
        ) == 1

    assert "iso_target: phaseConfig.iso" in preview_body


def test_preview_phase_inclusion_conditions_are_independent():
    body = _preview_intents_body()

    assert re.search(
        r"if\s*\(eclipse\.C2\s*\|\|\s*eclipse\.C3\)\s*\{.*?"
        r"phase:\s*'diamond_ring'.*?\n\s*\}",
        body,
        re.DOTALL,
    )

    assert re.search(
        r"if\s*\(eclipse\.TMAX\)\s*\{.*?"
        r"phase:\s*'totality'.*?\n\s*\}",
        body,
        re.DOTALL,
    )


def test_request_rig_previews_posts_intents_and_targets_requested_rig():
    body = _request_rig_previews_body()

    assert re.search(
        r"fetch\(\s*'/api/rigs/preview'\s*,\s*\{.*?"
        r"method:\s*'POST'.*?"
        r"headers:\s*\{\s*'Content-Type'\s*:\s*"
        r"'application/json'\s*\}.*?"
        r"body:\s*JSON\.stringify\(\{.*?"
        r"intents\s*,.*?"
        r"rig_id:\s*Number\(rigId\).*?"
        r"rig_override:\s*rigOverride.*?"
        r"\}\)",
        body,
        re.DOTALL,
    )

    assert re.search(
        r"const\s+responseJson\s*=\s*await\s+response\.text\(\)",
        body,
    )

    assert "renderRigPreviews(responseJson, rigId)" in body
    assert "readRigPhotoConfig(rigId)" in body
    assert "await persistRigPhoto(rigId)" not in body
    assert "rig_override: rigOverride" in body
    assert "rig_id: Number(rigId)" in body

    assert re.search(
        r"function\s+renderRigPreviews\s*"
        r"\(responseJson,\s*requestedRigId\)",
        INDEX,
    )


def test_render_rig_previews_only_updates_requested_rig():
    body = _render_rig_previews_body()

    assert (
        "document.getElementById(`rig-preview-${requestedRigId}`)"
        in body
    )
    assert "requestedBody.replaceChildren()" in body

    assert re.search(
        r"Number\(rig\s*&&\s*rig\.rig_id\)"
        r"\s*!==\s*Number\(requestedRigId\)",
        body,
    )

    assert (
        "document.getElementById(`rig-preview-${rig.rig_id}`)"
        in body
    )

    assert "line.textContent" in body
    assert "message.textContent" in body
    assert "innerHTML" not in body


def test_preview_renderer_shows_only_phase_and_differences():
    body = _render_rig_previews_body()

    assert "diff_lines" in body
    assert "Aucun impact" in body

    for label in (
        "Partial",
        "Diamond Ring",
        "Totality",
    ):
        assert label in body

    for raw_field in (
        "target_time",
        "iso_applied",
        "exposures_s",
        "corrections",
        "warnings",
        "motion_policy",
    ):
        assert f"appendField('{raw_field}'" not in body

    assert "RAW_JSON_NUMBER:" not in body
    assert "numberPattern" not in body

