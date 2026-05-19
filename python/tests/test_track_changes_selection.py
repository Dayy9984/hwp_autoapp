from modification.track_changes import TrackChangesManager


class _FakeSet:
    def __init__(self):
        self._values = {"List": 0, "Para": 0, "Pos": 0}

    def Item(self, key):
        return self._values[key]


class _FakeHwp:
    def __init__(self, start, end):
        self.SelectionMode = 1
        self._start = start
        self._end = end

    def CreateSet(self, _name):
        return _FakeSet()

    def GetSelectedPosBySet(self, sset, eset):
        sset._values["List"], sset._values["Para"], sset._values["Pos"] = self._start
        eset._values["List"], eset._values["Para"], eset._values["Pos"] = self._end
        return True


def test_selection_range_collapsed_returns_none():
    manager = TrackChangesManager(_FakeHwp((0, 10, 3), (0, 10, 3)))
    assert manager._get_selection_range() is None


def test_selection_range_non_collapsed_returns_range():
    manager = TrackChangesManager(_FakeHwp((0, 10, 3), (0, 10, 9)))
    assert manager._get_selection_range() == ((0, 10, 3), (0, 10, 9))
