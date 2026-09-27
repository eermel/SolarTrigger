import pytest

from plugins.focuser.zwo_eaf import (
    EAF_ERROR_REMOVED,
    EafError,
    ZwoEaf,
    _SDK_OPEN_REFS,
)


class RemovedEafLib:
    def __init__(self):
        self.closed = []

    def EAFGetPosition(self, sdk_id, _position):
        assert sdk_id == 7
        return EAF_ERROR_REMOVED

    def EAFClose(self, sdk_id):
        self.closed.append(sdk_id)
        return 0


def test_removed_eaf_invalidates_session_for_future_reconnect():
    previous_refs = dict(_SDK_OPEN_REFS)
    _SDK_OPEN_REFS.clear()
    _SDK_OPEN_REFS[7] = 1

    try:
        eaf = ZwoEaf.__new__(ZwoEaf)
        eaf.lib = RemovedEafLib()
        eaf.id = 7
        eaf.name = "EAF"
        eaf.max_step = 50000
        eaf._session_acquired = True

        with pytest.raises(EafError) as raised:
            eaf.get_position()

        assert raised.value.code == EAF_ERROR_REMOVED
        assert eaf.connected is False
        assert eaf.id is None
        assert eaf.name is None
        assert eaf.max_step is None
        assert eaf.lib.closed == [7]
        assert 7 not in _SDK_OPEN_REFS
    finally:
        _SDK_OPEN_REFS.clear()
        _SDK_OPEN_REFS.update(previous_refs)
