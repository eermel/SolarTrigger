from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

HTML = (
    ROOT
    / "flask_app"
    / "templates"
    / "index.html"
).read_text(encoding="utf-8")


def test_execution_plan_ui_is_per_rig():
    assert (
        'id="sequencer-plan-name"'
        in HTML
    )

    for rig_id in range(1, 5):
        assert (
            f'id="sequencer-plan-prefix-{rig_id}"'
            in HTML
        )
        assert (
            f"runSequencerRig({rig_id})"
            in HTML
        )
        assert (
            f"cleanExecutionPlansForRig({rig_id})"
            in HTML
        )


def test_run_all_replaces_old_global_run():
    assert (
        'id="btn-run-all-sequencers"'
        in HTML
    )
    assert (
        "RUN ALL SEQUENCERS"
        in HTML
    )
    assert (
        'id="btn-run-sequencer"'
        not in HTML
    )
    assert (
        'id="sequencer-output-filename"'
        not in HTML
    )


def test_filename_prefix_contains_required_parts():
    assert (
        "`exec_plan_${sequencerPlanDate}_`"
        in HTML
    )
    assert (
        "`RIG${rigId}_${brand}_${name}`"
        in HTML
    )


def test_sequencer_action_buttons_are_compact_and_equal_height():
    assert (
        ".sequencer-plan-rig-row .btn {"
        in HTML
    )
    assert (
        "#btn-run-all-sequencers {"
        in HTML
    )
    assert "--btn-h:     30px;" in HTML
    assert "height: var(--btn-h);" in HTML
    assert "min-height: var(--btn-h);" in HTML
    assert "height: 36px;" not in HTML
    assert "font-size: 9px;" in HTML
    assert "white-space: nowrap;" in HTML


def test_section_titles_and_labels_are_high_visibility():
    assert ".card-title," in HTML
    assert ".phase-section-title," in HTML
    assert ".camcfg-subsection-title," in HTML
    assert ".field label," in HTML
    assert ".stat-label," in HTML
    assert ".cam-rig-meta-label {" in HTML

    assert "color: #fff !important;" in HTML
    assert "font-weight: 700;" in HTML

    # Destructive actions retain their explicit warning colour.
    assert ".devices-reset-card .card-title {" in HTML
    assert "color: var(--red) !important;" in HTML

def test_sequencer_margin_labels_are_explicit():
    assert "Sequence margin (min)" in HTML
    assert "TSTART = C1 - margin / END = C4 + margin" in HTML

    assert ">Sequence margin</div>" not in HTML
    assert "min — START = C1 − margin / END = C4 + margin" not in HTML
