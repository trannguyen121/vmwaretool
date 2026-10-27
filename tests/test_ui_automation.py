from vmware_cleaner.ui_automation import VMwareUIAutomation


class FakeControl:
    def __init__(self, text="", children=None, enabled=True, handle=None):
        self._text = text
        self._children = children or []
        self._enabled = enabled
        self.clicked = False
        self.handle = handle

    def descendants(self, control_type=None):
        if control_type == "Window":
            return self._children
        if control_type == "Button":
            return [child for child in self._children if child._text in {"OK", "Cancel", "Close"}]
        return self._children

    def window_text(self):
        return self._text

    def is_enabled(self):
        return self._enabled

    def click_input(self):
        self.clicked = True

    def invoke(self):
        self.clicked = True


def test_acknowledges_only_disk_cleanup_finished_dialog() -> None:
    message = FakeControl("Disk cleanup is finished.")
    ok = FakeControl("OK")
    completion_window = FakeControl("VMware Workstation", [message, ok])
    main = FakeControl(children=[completion_window])

    assert VMwareUIAutomation._acknowledge_finished_dialog(main)
    assert ok.clicked


def test_does_not_click_unrelated_ok_dialog() -> None:
    message = FakeControl("Another VMware message")
    ok = FakeControl("OK")
    unrelated_window = FakeControl("VMware Workstation", [message, ok])
    main = FakeControl(children=[unrelated_window])

    assert not VMwareUIAutomation._acknowledge_finished_dialog(main)
    assert not ok.clicked


def test_native_workstation_completion_uses_bm_click(monkeypatch) -> None:
    import win32gui

    sent = []
    native_window = FakeControl("VMware Workstation", handle=100)
    main = FakeControl(children=[native_window])
    monkeypatch.setattr(
        win32gui,
        "EnumChildWindows",
        lambda _root, callback, param: [callback(101, param), callback(102, param)],
    )
    monkeypatch.setattr(
        win32gui, "GetClassName", lambda handle: "VMwareStaticLink" if handle == 101 else "Button"
    )
    monkeypatch.setattr(
        win32gui,
        "GetWindowText",
        lambda handle: "Disk cleanup is finished." if handle == 101 else "OK",
    )
    monkeypatch.setattr(win32gui, "IsWindowEnabled", lambda _handle: True)
    monkeypatch.setattr(
        win32gui, "SendMessage", lambda handle, message, wparam, lparam: sent.append((handle, message))
    )

    assert VMwareUIAutomation._acknowledge_finished_dialog(main)
    assert sent and sent[0][0] == 102
