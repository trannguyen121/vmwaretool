import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import vmware_cleaner.ui as ui


class ImmediateThread:
    def __init__(self, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


def test_reload_refreshes_library_and_writes_log(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    calls = []
    monkeypatch.setattr(ui.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(ui, "discover_vms", lambda environment: calls.append(environment) or [])

    window = ui.MainWindow()
    assert len(calls) == 1
    assert window.reload_button.isEnabled()
    assert "Reload hoàn tất" in window.log_box.toPlainText()
    assert window.start_button.text() == "Start Scan"
    window.mode.setCurrentIndex(2)
    assert window.start_button.text() == "Start Cleanup"

    window.reload_button.click()
    assert len(calls) == 2
    assert window.reload_button.isEnabled()
    window.close()
    app.processEvents()
