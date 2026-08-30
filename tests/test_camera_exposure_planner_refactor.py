from backend import nikon_exposure_planner as nikon_backend
from backend import sony_exposure_planner as sony_backend
from plugins.camera import sony_planner as sony_compat


def _sony_views(sequence):
    result = []
    for item in sequence:
        if isinstance(item, sony_backend.SinglePhoto):
            result.append(item.speed)
        else:
            result.extend(item.views)
    return result


def test_sony_compatibility_module_is_same_planner():
    args = ("1/4000", "4", 1.0)

    backend_step, backend_count, backend_seq = sony_backend.plan(*args)
    compat_step, compat_count, compat_seq = sony_compat.plan(*args)

    assert compat_step == backend_step
    assert compat_count == backend_count
    assert _sony_views(compat_seq) == _sony_views(backend_seq)


def test_sony_compatibility_exports_private_helpers():
    assert sony_compat._snap_step(1.0) == sony_backend._snap_step(1.0)
    assert sony_compat._best_composition(5) == sony_backend._best_composition(5)


def test_nikon_reference_totality_grid():
    speeds = nikon_backend._speeds_between(
        "1/4000",
        "4",
        1.0,
    )

    assert speeds[0] == "1/4000"
    assert speeds[-1] == "4"
    assert all(
        nikon_backend._parse(speeds[index])
        < nikon_backend._parse(speeds[index + 1])
        for index in range(len(speeds) - 1)
    )


def test_nikon_planner_never_duplicates_adjacent_speed():
    speeds = nikon_backend._speeds_between(
        "1/8000",
        "1/125",
        0.3,
    )

    assert speeds
    assert all(
        left != right
        for left, right in zip(speeds, speeds[1:])
    )
