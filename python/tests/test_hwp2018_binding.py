import os
import sys


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class _WindowCollectionWithUnsupportedHandle:
    Count = 1

    def Item(self, _index):
        raise RuntimeError("WindowHandle unsupported on HWP 2018")


class _FakeHwp2018:
    XHwpWindows = _WindowCollectionWithUnsupportedHandle()


def test_rot_target_binding_keeps_single_hwp_when_window_handle_access_fails(monkeypatch):
    from engine.connection.rot_access import ROTAccessManager

    hwp = _FakeHwp2018()
    monkeypatch.setattr(ROTAccessManager, "_enumerate_hwp_instances", lambda: [hwp])
    monkeypatch.setattr(ROTAccessManager, "verify_hwp_instance", lambda instance: instance is hwp)

    bound = ROTAccessManager.get_hwp_instance_by_target(
        preferred_pid=3860,
        preferred_hwnd=26216646,
        strict=True,
    )

    assert bound is hwp
