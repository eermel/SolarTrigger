import re
from pathlib import Path


INDEX = (
    Path(__file__).resolve().parents[1] / "flask_app" / "templates" / "index.html"
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
        r"async\s+function\s+requestRigPreviews\s*\(intents\)\s*\{"
        r"(?P<body>.*?)\n\}",
        INDEX,
        re.DOTALL,
    )
    assert match
    return match.group("body")


def _render_rig_previews_body():
    match = re.search(
        r"function\s+renderRigPreviews\s*\(responseJson\)\s*\{"
        r"(?P<body>.*?)\n\}",
        INDEX,
        re.DOTALL,
    )
    assert match
    return match.group("body")


def _function_body(name, signature=r"\(.*?\)"):
    match = re.search(
        rf"(?:async\s+)?function\s+{name}\s*{signature}\s*\{{(?P<body>.*?)\n\}}",
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
            'onclick="requestRigPreviews(buildPreviewIntents())"' in attributes
        )
        assert "Prévisualiser" in label


def test_preview_buttons_are_exposed_and_enabled_only_for_enabled_rigs():
    body = _function_body("updateRigs", r"\(rigs\)")

    assert "const enabled = rig.enabled === true" in body
    assert re.search(
        r"if\s*\(cameraColumn\)\s*\{.*?"
        r"cameraColumn\.hidden\s*=\s*!enabled\s*;.*?"
        r"querySelector\('\.rig-preview-button'\).*?"
        r"previewButton\.disabled\s*=\s*!enabled\s*;",
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
            rf"phase:\s*'{phase}'.*?request_id:\s*'{request_id}'", body, re.DOTALL
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


def test_preview_iso_target_comes_from_the_visible_phase_iso_controls():
    read_body = _function_body("_readCameraConfig", r"\(\)")
    preview_body = _preview_intents_body()

    assert re.search(
        r"iso:\s*parseInt\(document\.getElementById\(`cfg-\$\{prefix\}-iso`\)"
        r"\.value,\s*10\)",
        read_body,
    )
    for control_id in ("cfg-partial-iso", "cfg-dr-iso", "cfg-tot-iso"):
        assert len(re.findall(rf'id=["\']{control_id}["\']', INDEX)) == 1
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
        r"if\s*\(eclipse\.TMAX\)\s*\{.*?phase:\s*'totality'.*?\n\s*\}",
        body,
        re.DOTALL,
    )


def test_request_rig_previews_posts_intents_and_forwards_raw_payload():
    body = _request_rig_previews_body()

    assert re.search(
        r"fetch\(\s*'/api/rigs/preview'\s*,\s*\{.*?"
        r"method:\s*'POST'.*?"
        r"headers:\s*\{\s*'Content-Type'\s*:\s*'application/json'\s*\}.*?"
        r"body:\s*JSON\.stringify\(\{\s*intents\s*\}\)",
        body,
        re.DOTALL,
    )
    assert re.search(r"const\s+responseJson\s*=\s*await\s+response\.text\(\)", body)
    assert re.search(r"renderRigPreviews\(responseJson\)", body)
    assert re.search(r"function\s+renderRigPreviews\s*\(responseJson\)", INDEX)


def test_render_rig_previews_distributes_escaped_raw_values_without_conversion():
    body = _render_rig_previews_body()

    assert re.search(r"rigs\.forEach\(rig\s*=>\s*\{\s*let\s+body\s*=\s*null\s*;\s*try\s*\{", body)
    assert re.search(
        r"getElementById\(`rig-preview-\$\{displayValue\(rig\s*&&\s*rig\.rig_id\)\}`\)",
        body,
    )
    assert "document.querySelectorAll('.rig-preview')" in body
    assert "line.textContent" in body
    assert "message.textContent" in body
    assert "innerHTML" not in body

    for field in (
        "phase",
        "target_time",
        "iso_applied",
        "exposures_s",
        "corrections",
        "warnings",
        "motion_policy",
        "error.code",
        "error.message",
    ):
        assert f"appendField('{field}'" in body

    assert "RAW_JSON_NUMBER:" in body
    assert "numberPattern" in body
    assert ".sort(" not in body
    assert "parseFloat(" not in body
    assert "parseInt(" not in body
    assert "Number(" not in body


def test_request_rig_previews_guards_missing_intents():
    body = _request_rig_previews_body()

    assert re.search(
        r"if\s*\(\s*!Array\.isArray\(intents\)\s*\|\|\s*"
        r"intents\.length\s*===\s*0\s*\)",
        body,
    )
    assert "flash('configuration/circumstances incomplete', 'red')" in body
    guard = body.index("configuration/circumstances incomplete")
    request = body.index("fetch('/api/rigs/preview'")
    assert "return;" in body[guard:request]


def test_request_rig_previews_locks_until_finally():
    body = _request_rig_previews_body()

    assert re.search(r"let\s+rigPreviewInFlight\s*=\s*false\s*;", INDEX)
    assert re.search(r"if\s*\(rigPreviewInFlight\)\s*return\s*;", body)
    assert re.search(r"rigPreviewInFlight\s*=\s*true\s*;", body)
    assert re.search(
        r"finally\s*\{\s*rigPreviewInFlight\s*=\s*false\s*;\s*\}",
        body,
        re.DOTALL,
    )


def test_each_preview_click_and_successful_save_request_one_preview_only():
    request_body = _request_rig_previews_body()
    save_body = _function_body("saveCameraConfig", r"\(\)")

    assert INDEX.count('onclick="requestRigPreviews(buildPreviewIntents())"') == 4
    assert request_body.count("fetch('/api/rigs/preview'") == 1
    assert save_body.count("requestRigPreviews(buildPreviewIntents())") == 1
    success = re.search(
        r"if\s*\(d\.status\s*===\s*'ok'\)\s*\{(?P<body>.*?)\n\s*\}",
        save_body,
        re.DOTALL,
    )
    assert success
    assert "requestRigPreviews(buildPreviewIntents())" in success.group("body")


def test_incomplete_context_and_failed_save_do_not_request_preview():
    intents_body = _preview_intents_body()
    save_body = _function_body("saveCameraConfig", r"\(\)")

    assert re.search(
        r"if\s*\(!phases\s*\|\|\s*!eclipse\s*\|\|\s*!eclipseDate\)\s*"
        r"return null",
        intents_body,
    )
    assert re.search(
        r"if\s*\(!phaseConfig\s*\|\|\s*!target\.contact.*?\)\s*\{\s*"
        r"return null;",
        intents_body,
        re.DOTALL,
    )
    preview_call = save_body.index("requestRigPreviews(buildPreviewIntents())")
    success_guard = save_body.index("if (d.status === 'ok')")
    failure_branch = save_body.index("else flash", preview_call)
    assert success_guard < preview_call < failure_branch
    assert "requestRigPreviews" not in save_body[failure_branch:]


def test_preview_flow_has_no_hardware_route_timer_or_legacy_photo_hook():
    preview_flow = "\n".join(
        (
            _preview_intents_body(),
            _request_rig_previews_body(),
            _render_rig_previews_body(),
        )
    )

    assert "/api/rigs/photo" not in INDEX
    for forbidden in (
        "setTimeout(",
        "setInterval(",
        "/mount/",
        "/focuser/",
        "/camera/",
        "/trigger/",
        "/api/rigs/photo",
    ):
        assert forbidden not in preview_flow


def test_rendering_is_distributed_by_rig_and_one_rig_error_stays_local():
    body = _render_rig_previews_body()

    assert re.search(
        r"rigs\.forEach\(rig\s*=>\s*\{\s*let\s+body\s*=\s*null\s*;\s*try\s*\{",
        body,
    )
    error_handler = re.search(
        r"catch\s*\(error\)\s*\{(?P<body>.*?)\n\s*\}\s*\);",
        body,
        re.DOTALL,
    )
    assert error_handler
    assert re.search(r"if\s*\(!body\)\s*return\s*;", error_handler.group("body"))
    assert "body.appendChild(message)" in error_handler.group("body")
    assert "throw error" not in body
