import sys
import types

import pytest


if "gphoto2" not in sys.modules:
    sys.modules["gphoto2"] = types.SimpleNamespace(
        GP_LOG_ERROR=0,
        GP_LOG_VERBOSE=1,
        GP_LOG_DEBUG=2,
        GP_LOG_DATA=3,
        use_python_logging=lambda mapping=None: None,
        check_result=lambda *args, **kwargs: None,
    )

_argv = sys.argv
sys.argv = [sys.argv[0]]
from scripts import eclipse_trigger as trigger
sys.argv = _argv


@pytest.mark.parametrize(
    ("total_size", "target_size", "expected"),
    [
        (9, 8, [0, 1, 2, 3, 5, 6, 7, 8]),
        (8, 5, [0, 2, 4, 5, 7]),
        (10, 6, [0, 2, 4, 5, 7, 9]),
        (11, 7, [0, 2, 3, 5, 7, 8, 10]),
    ],
)
def test_select_uniform_indices(total_size, target_size, expected):
    exposures = [f"exposure-{index}" for index in range(total_size)]

    indices = trigger._select_uniform_indices(exposures, target_size)

    assert indices == expected
    assert indices[0] == 0
    assert indices[-1] == total_size - 1
    assert indices == sorted(indices)


@pytest.mark.parametrize("total_size", range(2, 12))
def test_select_uniform_indices_preserves_endpoints_and_order(total_size):
    exposures = list(range(total_size))

    for target_size in range(2, total_size + 1):
        indices = trigger._select_uniform_indices(exposures, target_size)

        assert indices[0] == 0
        assert indices[-1] == total_size - 1
        assert all(left < right for left, right in zip(indices, indices[1:]))
