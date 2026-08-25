from datetime import datetime, timedelta
from pathlib import Path
import ast


SCRIPT = Path("scripts/eclipse_trigger.py")


def _load_resume_helpers():
    """
    Charge uniquement les helpers de reprise depuis eclipse_trigger.py,
    sans exécuter l'initialisation matérielle du script.
    """
    source = SCRIPT.read_text(encoding="utf-8")
    tree = ast.parse(source)

    wanted = {
        "_phase_is_future",
        "_first_future_grid_slot",
    }

    nodes = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in wanted
    ]

    module = ast.Module(body=nodes, type_ignores=[])
    code = compile(module, str(SCRIPT), "exec")

    namespace = {"timedelta": timedelta}
    exec(code, namespace)

    return namespace


def test_first_future_slot_before_first_slot():
    ns = _load_resume_helpers()

    first = datetime(2027, 8, 2, 10, 0, 0)
    end = datetime(2027, 8, 2, 11, 0, 0)

    ns["now"] = lambda: datetime(2027, 8, 2, 9, 59, 30)

    result = ns["_first_future_grid_slot"](first, 60, end)

    assert result == datetime(2027, 8, 2, 10, 0, 0)


def test_first_future_slot_skips_all_past_slots():
    ns = _load_resume_helpers()

    first = datetime(2027, 8, 2, 10, 0, 0)
    end = datetime(2027, 8, 2, 11, 0, 0)

    # Slots théoriques :
    # 10:00, 10:01, 10:02, 10:03...
    # Reprise à 10:02:17 => prochain slot = 10:03.
    ns["now"] = lambda: datetime(2027, 8, 2, 10, 2, 17)

    result = ns["_first_future_grid_slot"](first, 60, end)

    assert result == datetime(2027, 8, 2, 10, 3, 0)


def test_first_future_slot_keeps_exact_current_slot():
    ns = _load_resume_helpers()

    first = datetime(2027, 8, 2, 10, 0, 0)
    end = datetime(2027, 8, 2, 11, 0, 0)

    # Si START tombe exactement sur un slot, ce slot n'est pas passé.
    ns["now"] = lambda: datetime(2027, 8, 2, 10, 2, 0)

    result = ns["_first_future_grid_slot"](first, 60, end)

    assert result == datetime(2027, 8, 2, 10, 2, 0)


def test_first_future_slot_never_goes_past_phase_end():
    ns = _load_resume_helpers()

    first = datetime(2027, 8, 2, 10, 0, 0)
    end = datetime(2027, 8, 2, 10, 5, 0)

    ns["now"] = lambda: datetime(2027, 8, 2, 10, 7, 0)

    result = ns["_first_future_grid_slot"](first, 60, end)

    assert result >= end


def test_phase_is_future_during_phase():
    ns = _load_resume_helpers()

    ns["now"] = lambda: datetime(2027, 8, 2, 10, 4, 59)

    assert ns["_phase_is_future"](
        datetime(2027, 8, 2, 10, 5, 0)
    ) is True


def test_phase_is_not_future_at_boundary():
    ns = _load_resume_helpers()

    ns["now"] = lambda: datetime(2027, 8, 2, 10, 5, 0)

    assert ns["_phase_is_future"](
        datetime(2027, 8, 2, 10, 5, 0)
    ) is False


def test_phase_is_not_future_after_boundary():
    ns = _load_resume_helpers()

    ns["now"] = lambda: datetime(2027, 8, 2, 10, 5, 1)

    assert ns["_phase_is_future"](
        datetime(2027, 8, 2, 10, 5, 0)
    ) is False


def test_restart_selects_only_current_and_future_totality_phases():
    ns = _load_resume_helpers()

    base = datetime(2027, 8, 2, 10, 0, 0)

    fin_1a = base + timedelta(minutes=10)
    c2 = base + timedelta(minutes=11)
    c3 = base + timedelta(minutes=17)
    fin_3a = base + timedelta(minutes=18)
    tend = base + timedelta(minutes=30)

    cases = [
        (
            "phase1a",
            base + timedelta(minutes=5),
            [True, True, True, True, True],
        ),
        (
            "phase1b",
            base + timedelta(minutes=10, seconds=30),
            [False, True, True, True, True],
        ),
        (
            "phase2",
            base + timedelta(minutes=13),
            [False, False, True, True, True],
        ),
        (
            "phase3a",
            base + timedelta(minutes=17, seconds=30),
            [False, False, False, True, True],
        ),
        (
            "phase3b",
            base + timedelta(minutes=22),
            [False, False, False, False, True],
        ),
        (
            "after_tend",
            base + timedelta(minutes=31),
            [False, False, False, False, False],
        ),
    ]

    boundaries = [fin_1a, c2, c3, fin_3a, tend]

    for phase_name, current, expected in cases:
        ns["now"] = lambda current=current: current

        actual = [
            ns["_phase_is_future"](boundary)
            for boundary in boundaries
        ]

        assert actual == expected, phase_name
