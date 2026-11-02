import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTableWidgetItem, QTableWidgetSelectionRange

import vmware_cleaner.ui as ui


class ImmediateThread:
    def __init__(self, target, **_kwargs):
        self.target = target

    def start(self):
        self.target()


def test_vm_table_space_toggles_all_highlighted_rows() -> None:
    app = QApplication.instance() or QApplication([])
    table = ui.VmTableWidget(2, 2)
    for row in range(2):
        check = QTableWidgetItem()
        check.setFlags(
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsUserCheckable
        )
        check.setCheckState(Qt.CheckState.Unchecked)
        table.setItem(row, 0, check)
        table.setItem(row, 1, QTableWidgetItem(f"VM {row}"))
    table.setSelectionBehavior(table.SelectionBehavior.SelectRows)
    table.setSelectionMode(table.SelectionMode.ExtendedSelection)
    table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 1, 1), True)
    table.show()
    table.setFocus()

    QTest.keyClick(table, Qt.Key.Key_Space)
    assert all(table.item(row, 0).checkState() == Qt.CheckState.Checked for row in range(2))
    QTest.keyClick(table, Qt.Key.Key_Space)
    assert all(table.item(row, 0).checkState() == Qt.CheckState.Unchecked for row in range(2))
    table.close()
    app.processEvents()


def test_reload_refreshes_library_and_writes_log(monkeypatch) -> None:
    app = QApplication.instance() or QApplication([])
    calls = []
    monkeypatch.setattr(ui.threading, "Thread", ImmediateThread)
    monkeypatch.setattr(ui, "discover_vms", lambda environment: calls.append(environment) or [])

    window = ui.MainWindow()
    assert len(calls) == 1
    assert window.reload_button.isEnabled()
    assert "Reload hoàn tất" in window.log_box.toPlainText()
    assert window.tool_selector.currentData() == "disk_cleaner"
    assert list(window.nav_buttons) == ["disk_cleaner", "linked_clone", "folder_move"]
    assert list(window.action_buttons) == ["cleanup", "power", "delete", "unregister"]
    assert set(window.tool_pages) == {"linked_clone", "folder_move", "disk_cleaner"}
    assert window.nav_buttons["linked_clone"].property("accent") == "mint"
    assert window.summary_label.text() == "0 VM  •  0 đã chọn"
    assert window.start_button.text() == "Start Scan"
    window.mode.setCurrentIndex(2)
    assert window.start_button.text() == "Start Cleanup"
    assert not window.workers.isEnabled()
    assert window.workers.currentText() == "1"

    window.reload_button.click()
    assert len(calls) == 2
    assert window.reload_button.isEnabled()
    window._open_tool("linked_clone")
    assert window.tool_stack.currentWidget() is window.clone_page
    assert window.nav_buttons["linked_clone"].isChecked()
    window._open_tool("disk_cleaner")
    assert window.tool_stack.currentWidget() is window.disk_page
    window.close()
    app.processEvents()
