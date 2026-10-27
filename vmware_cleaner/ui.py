from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal, QDateTime
from PySide6.QtGui import QColor, QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .cleanup import CleanupEngine
from .discovery import discover_vms
from .environment import detect_environment
from .logging_config import configure_logging
from .models import CleanupResult, CleanupStatus, VirtualMachine
from .task_manager import ConcurrentTaskManager, TaskCallbacks


class WorkerSignals(QObject):
    status = Signal(object, object, str)
    finished = Signal(object)
    all_finished = Signal()
    discovered = Signal(object)
    discovery_failed = Signal(str)


def format_bytes(value: int) -> str:
    number = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if number < 1024 or unit == "TB":
            return f"{number:.1f} {unit}"
        number /= 1024
    return f"{number:.1f} TB"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("VMware Disk Cleaner")
        self.resize(650, 550)
        self.setMinimumSize(600, 450)
        self.environment = detect_environment()
        self.vms: list[VirtualMachine] = []
        self._row_by_key: dict[str, int] = {}
        self._processed = 0
        self._total = 0
        self._close_when_done = False
        self._discovering = False

        self.signals = WorkerSignals()
        self.signals.status.connect(self._set_vm_status)
        self.signals.finished.connect(self._task_finished)
        self.signals.all_finished.connect(self._all_finished)
        self.signals.discovered.connect(self._discovery_finished)
        self.signals.discovery_failed.connect(self._discovery_failed)
        callbacks = TaskCallbacks(
            status=self.signals.status.emit,
            finished=self.signals.finished.emit,
            all_finished=self.signals.all_finished.emit,
        )
        self.manager = ConcurrentTaskManager(CleanupEngine(self.environment), callbacks)
        self._build_ui()
        self._append_log(
            f"VMware {self.environment.version}; backend Cleanup: UI Automation nền tuần tự"
        )
        self._reload()

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        version = self.environment.version
        backend = "UI Automation nền tuần tự" if self.environment.vmware_exe else "Không tìm thấy VMware"
        self.environment_label = QLabel(f"VMware {version}  •  {backend}")
        self.environment_label.setObjectName("muted")
        layout.addWidget(self.environment_label)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "Máy ảo", "Dung lượng", "Trạng thái"])
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

        controls = QHBoxLayout()
        self.select_all = QPushButton("Chọn tất cả")
        self.select_all.setCheckable(True)
        self.select_all.clicked.connect(self._toggle_all)
        controls.addWidget(self.select_all)
        self.reload_button = QPushButton("Reload")
        self.reload_button.setToolTip("Quét lại toàn bộ VM trong VMware Workstation Library")
        self.reload_button.clicked.connect(self._reload)
        controls.addWidget(self.reload_button)
        controls.addStretch()
        controls.addWidget(QLabel("Chế độ"))
        self.mode = QComboBox()
        self.mode.addItem("Scan", "scan")
        self.mode.addItem("DryRun", "dry_run")
        self.mode.addItem("Cleanup", "cleanup")
        self.mode.currentIndexChanged.connect(self._update_start_button_text)
        controls.addWidget(self.mode)
        controls.addWidget(QLabel("Tác vụ"))
        self.workers = QComboBox()
        self.workers.addItems(["1", "2", "3", "4"])
        self.workers.setCurrentText("1")
        self.workers.setToolTip("Các VM trên cùng volume host vẫn được xử lý tuần tự")
        controls.addWidget(self.workers)
        layout.addLayout(controls)

        actions = QHBoxLayout()
        self.start_button = QPushButton("Start Cleanup")
        self.start_button.setObjectName("primary")
        self.start_button.clicked.connect(self._start)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop)
        actions.addWidget(self.start_button, 1)
        actions.addWidget(self.stop_button)
        layout.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("Sẵn sàng")
        layout.addWidget(self.progress)

        self.log_box = QPlainTextEdit()
        self.log_box.setObjectName("logBox")
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(500)
        self.log_box.setFixedHeight(115)
        self.log_box.setPlaceholderText("Nhật ký hoạt động")
        layout.addWidget(self.log_box)
        self.setCentralWidget(root)
        self._update_start_button_text()

    def _update_start_button_text(self) -> None:
        labels = {
            "scan": "Start Scan",
            "dry_run": "Start DryRun",
            "cleanup": "Start Cleanup",
        }
        self.start_button.setText(labels.get(self.mode.currentData(), "Start"))
        cleanup_mode = self.mode.currentData() == "cleanup"
        if cleanup_mode:
            self.workers.setCurrentText("1")
            self.workers.setEnabled(False)
            self.workers.setToolTip("UI Automation chỉ được chạy tuần tự trên một desktop")
        elif not self.manager.running:
            self.workers.setEnabled(True)
            self.workers.setToolTip("Các VM trên cùng volume host vẫn được xử lý tuần tự")

    def _append_log(self, message: str) -> None:
        timestamp = QDateTime.currentDateTime().toString("HH:mm:ss")
        self.log_box.appendPlainText(f"[{timestamp}] {message}")

    def _reload(self) -> None:
        if self.manager.running or self._discovering:
            return
        self._discovering = True
        self.vms = []
        self._row_by_key.clear()
        self.table.setRowCount(0)
        self.select_all.setChecked(False)
        self.select_all.setText("Chọn tất cả")
        self.start_button.setEnabled(False)
        self.reload_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.setFormat("Đang quét VMware Library…")
        self._append_log("Đang reload VMware Library…")
        threading.Thread(target=self._discover, name="vm-discovery", daemon=True).start()

    def _discover(self) -> None:
        try:
            self.signals.discovered.emit(discover_vms(self.environment))
        except Exception as exc:
            self.signals.discovery_failed.emit(str(exc))

    def _discovery_finished(self, vms: list[VirtualMachine]) -> None:
        self._discovering = False
        self.vms = vms
        self._populate()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat(
            f"Đã phát hiện {len(vms)} VM" if vms else "Không tìm thấy VM trong VMware Library"
        )
        self.start_button.setEnabled(bool(vms))
        self.reload_button.setEnabled(True)
        self._append_log(f"Reload hoàn tất: phát hiện {len(vms)} VM.")

    def _discovery_failed(self, message: str) -> None:
        self._discovering = False
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("Không thể đọc VMware Library")
        self.reload_button.setEnabled(True)
        self._append_log(f"Reload thất bại: {message}")
        QMessageBox.critical(self, "Lỗi discovery", message)

    def _populate(self) -> None:
        self.table.setRowCount(len(self.vms))
        for row, vm in enumerate(self.vms):
            self._row_by_key[vm.key] = row
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
            check.setCheckState(Qt.CheckState.Unchecked)
            name = QTableWidgetItem(vm.name)
            name.setToolTip(str(vm.vmx_path))
            size = QTableWidgetItem(format_bytes(vm.size_bytes))
            size.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            status = QTableWidgetItem(CleanupStatus.WAITING.value)
            self.table.setItem(row, 0, check)
            self.table.setItem(row, 1, name)
            self.table.setItem(row, 2, size)
            self.table.setItem(row, 3, status)
        if not self.vms:
            self.progress.setFormat("Không tìm thấy VM trong VMware Library")
            self.start_button.setEnabled(False)

    def _toggle_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(state)
        self.select_all.setText("Bỏ chọn tất cả" if checked else "Chọn tất cả")

    def _selected_vms(self) -> list[VirtualMachine]:
        return [
            vm for row, vm in enumerate(self.vms)
            if self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        ]

    def _start(self) -> None:
        selected = self._selected_vms()
        if not selected:
            QMessageBox.information(self, "VMware Disk Cleaner", "Hãy chọn ít nhất một máy ảo.")
            return
        mode = self.mode.currentData()
        if mode == "cleanup":
            answer = QMessageBox.warning(
                self,
                "Xác nhận compact VMDK",
                "Ứng dụng sẽ điều khiển VMware Workstation và bấm "
                "VM > Manage > Clean Up Disks > Clean up now cho từng VM.\n\n"
                "Các VM luôn được xử lý tuần tự ở ngoài vùng hiển thị. Bạn có thể tiếp tục "
                "dùng ứng dụng khác, nhưng không tự thao tác VMware trong lúc chạy và "
                "không tắt máy host.\n\n"
                "Bạn đã có bản sao lưu và muốn tiếp tục?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._processed = 0
        self._total = len(selected)
        self.progress.setRange(0, self._total)
        self.progress.setValue(0)
        self.progress.setFormat(f"0 / {self._total}")
        for vm in selected:
            self._set_vm_status(vm, CleanupStatus.WAITING, "")
        self._set_controls_running(True)
        self._append_log(
            f"Bắt đầu {self.mode.currentText()} cho {len(selected)} VM; "
            f"workers={self.workers.currentText()}."
        )
        try:
            workers = 1 if mode == "cleanup" else int(self.workers.currentText())
            self.manager.start(selected, mode, workers)
        except RuntimeError as exc:
            self._set_controls_running(False)
            QMessageBox.critical(self, "Lỗi", str(exc))

    def _stop(self) -> None:
        self.stop_button.setEnabled(False)
        self.progress.setFormat("Đang dừng xếp lịch; chờ tác vụ hiện tại…")
        self._append_log("Đã yêu cầu Stop; không nhận tác vụ mới.")
        self.manager.stop_scheduling()

    def _set_controls_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running and bool(self.vms))
        self.stop_button.setEnabled(running)
        self.mode.setEnabled(not running)
        self.workers.setEnabled(not running and self.mode.currentData() != "cleanup")
        self.select_all.setEnabled(not running)
        self.reload_button.setEnabled(not running and not self._discovering)
        self.table.setEnabled(not running)

    def _set_vm_status(self, vm: VirtualMachine, status: CleanupStatus, detail: str) -> None:
        row = self._row_by_key.get(vm.key)
        if row is None:
            return
        vm.status = status
        vm.status_detail = detail
        item = self.table.item(row, 3)
        item.setText(status.value)
        item.setToolTip(detail)
        colors = {
            CleanupStatus.RUNNING: "#60a5fa",
            CleanupStatus.DONE: "#34d399",
            CleanupStatus.FAILED: "#f87171",
            CleanupStatus.SKIPPED: "#fbbf24",
        }
        item.setForeground(QColor(colors.get(status, "#d1d5db")))
        if status != CleanupStatus.WAITING:
            message = f"{vm.name}: {status.value}"
            if detail:
                message += f" — {detail}"
            self._append_log(message)

    def _task_finished(self, result: CleanupResult) -> None:
        self._processed += 1
        self.progress.setValue(self._processed)
        suffix = ""
        if result.reclaimed_bytes:
            suffix = f"  •  Đã thu hồi {format_bytes(result.reclaimed_bytes)}"
        self.progress.setFormat(f"{self._processed} / {self._total}{suffix}")

    def _all_finished(self) -> None:
        self._set_controls_running(False)
        self.progress.setValue(self._total)
        self.progress.setFormat(f"Hoàn tất {self._processed} / {self._total}")
        self._append_log(f"Đợt xử lý hoàn tất: {self._processed}/{self._total} VM.")
        if self._close_when_done:
            self._close_when_done = False
            self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self.manager.running:
            event.accept()
            return
        answer = QMessageBox.question(
            self,
            "Cleanup đang chạy",
            "Dừng nhận tác vụ mới và đóng cửa sổ sau khi các tác vụ đang chạy hoàn tất?\n"
            "Tiến trình VMware đang ghi dữ liệu sẽ không bị cưỡng bức dừng.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._close_when_done = True
            self.manager.stop_scheduling()
            self.hide()
        event.ignore()


DARK_STYLE = """
QWidget { background: #111827; color: #e5e7eb; font-size: 13px; }
QMainWindow { background: #111827; }
QLabel#muted { color: #9ca3af; }
QTableWidget { background: #172033; alternate-background-color: #141c2c;
    border: 1px solid #2d3748; border-radius: 6px; gridline-color: #273449; }
QHeaderView::section { background: #1f2937; color: #d1d5db; padding: 7px;
    border: 0; border-bottom: 1px solid #374151; }
QPushButton, QComboBox { background: #253247; border: 1px solid #3b4b64;
    border-radius: 5px; padding: 7px 10px; }
QPushButton:hover, QComboBox:hover { border-color: #60a5fa; }
QPushButton:disabled { color: #6b7280; background: #1f2937; }
QPushButton#primary { background: #2563eb; border-color: #3b82f6; font-weight: 600; }
QProgressBar { background: #1f2937; border: 1px solid #374151; border-radius: 5px;
    text-align: center; min-height: 21px; }
QProgressBar::chunk { background: #2563eb; border-radius: 4px; }
QToolTip { background: #0f172a; color: #f3f4f6; border: 1px solid #475569; }
QPlainTextEdit#logBox { background: #0b1220; color: #cbd5e1;
    border: 1px solid #2d3748; border-radius: 5px; padding: 5px;
    font-family: Consolas, monospace; font-size: 11px; }
"""


def run_app() -> int:
    configure_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("VMware Disk Cleaner")
    app.setStyle("Fusion")
    app.setStyleSheet(DARK_STYLE)
    window = MainWindow()
    window.show()
    return app.exec()

