from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QDateTime, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCloseEvent, QKeyEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .cleanup import CleanupEngine
from .discovery import discover_vms
from .environment import detect_environment
from .interaction_animation import install_interaction_animations
from .logging_config import configure_logging
from .library import (
    LibraryRemovalResult,
    backup_inventory,
    close_workstation_gracefully,
    is_workstation_running,
    remove_from_library,
)
from .models import CleanupResult, CleanupStatus, VirtualMachine
from .task_manager import ConcurrentTaskManager, TaskCallbacks
from .tool_dialogs import CloneDialog, FolderMoveDialog, SysprepDialog
from .vm_tools import delete_vms, list_running_vms, stop_vms


class WorkerSignals(QObject):
    status = Signal(object, object, str)
    finished = Signal(object)
    all_finished = Signal()
    discovered = Signal(object)
    discovery_failed = Signal(str)
    operation_progress = Signal(int, int, str)
    operation_finished = Signal(str, object)
    operation_failed = Signal(str, str)


class VmTableWidget(QTableWidget):
    """VM table with row highlighting and Space-to-toggle checkboxes."""

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space:
            rows = sorted({index.row() for index in self.selectedIndexes()})
            if not rows and self.currentRow() >= 0:
                rows = [self.currentRow()]
            check_items = [self.item(row, 0) for row in rows if self.item(row, 0)]
            if check_items:
                new_state = (
                    Qt.CheckState.Unchecked
                    if all(item.checkState() == Qt.CheckState.Checked for item in check_items)
                    else Qt.CheckState.Checked
                )
                for item in check_items:
                    item.setCheckState(new_state)
                event.accept()
                return
        super().keyPressEvent(event)


def format_bytes(value: int) -> str:
    number = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if number < 1024 or unit == "TB":
            return f"{number:.1f} {unit}"
        number /= 1024
    return f"{number:.1f} TB"


def format_gb(value: int) -> str:
    return f"{value / 1024**3:.3f} GB"


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("VMware Toolkit")
        self.resize(1080, 800)
        self.setMinimumSize(900, 660)
        self.environment = detect_environment()
        self.settings = QSettings()
        self.theme_name = self.settings.value("ui/theme", "dark", str)
        if self.theme_name not in {"light", "dark"}:
            self.theme_name = "dark"
        self.vms: list[VirtualMachine] = []
        self._row_by_key: dict[str, int] = {}
        self._processed = 0
        self._total = 0
        self._close_when_done = False
        self._discovering = False
        self._vm_action_running = False
        self._vm_action_total = 0

        self.signals = WorkerSignals()
        self.signals.status.connect(self._set_vm_status)
        self.signals.finished.connect(self._task_finished)
        self.signals.all_finished.connect(self._all_finished)
        self.signals.discovered.connect(self._discovery_finished)
        self.signals.discovery_failed.connect(self._discovery_failed)
        self.signals.operation_progress.connect(self._operation_progress)
        self.signals.operation_finished.connect(self._operation_finished)
        self.signals.operation_failed.connect(self._operation_failed)
        callbacks = TaskCallbacks(
            status=self.signals.status.emit,
            finished=self.signals.finished.emit,
            all_finished=self.signals.all_finished.emit,
        )
        self.manager = ConcurrentTaskManager(CleanupEngine(self.environment), callbacks)
        self._build_ui()
        self._append_log(
            f"VMware {self.environment.version}; backend: VMware Clean Up Disks qua UI Automation"
        )
        self._reload()

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(16, 16, 16, 16)
        root_layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("VM Toolkit")
        title.setObjectName("appTitle")
        title.setMinimumWidth(105)
        header.addWidget(title)
        header.addStretch()

        # The selector remains the internal navigation model; users see the
        # horizontal pastel menu alongside the title.
        self.tool_selector = QComboBox()
        self.tool_selector.addItem("Trang chính", "disk_cleaner")
        self.tool_selector.addItem("Tạo Linked Clone", "linked_clone")
        self.tool_selector.addItem("Di chuyển Folder", "folder_move")
        self.tool_selector.currentIndexChanged.connect(self._tool_changed)
        self.tool_selector.setVisible(False)

        self.nav_buttons: dict[str, QPushButton] = {}
        navigation_items = (
            ("Trang chính", "disk_cleaner", "lavender"),
            ("Linked Clone", "linked_clone", "mint"),
            ("Di chuyển Folder", "folder_move", "rose"),
        )
        for label, tool, accent in navigation_items:
            button = QPushButton(label)
            button.setObjectName("navButton")
            button.setProperty("accent", accent)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, value=tool: self._open_tool(value))
            self.nav_buttons[tool] = button
            header.addWidget(button)
        self.save_settings_button = QPushButton("Lưu setting")
        self.save_settings_button.setObjectName("saveSettings")
        self.save_settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.save_settings_button.clicked.connect(self._save_all_settings)
        header.addWidget(self.save_settings_button)
        self.theme_button = QPushButton()
        self.theme_button.setObjectName("themeToggle")
        self.theme_button.setFixedWidth(46)
        self.theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_button.clicked.connect(self._toggle_theme)
        header.addWidget(self.theme_button)
        root_layout.addLayout(header)

        action_bar = QHBoxLayout()
        action_label = QLabel("THAO TÁC TRÊN VM ĐÃ CHỌN")
        action_label.setObjectName("actionLabel")
        action_bar.addWidget(action_label)
        self.action_buttons: dict[str, QPushButton] = {}
        action_items = (
            ("Dọn ổ đĩa", "cleanup", self._show_cleanup),
            ("Tắt VM", "power", self._power_selected_vms),
            ("Xóa toàn bộ", "delete", self._delete_selected_vms),
            ("Xóa hiển thị", "unregister", self._remove_selected_from_library),
        )
        for label, action, callback in action_items:
            button = QPushButton(label)
            button.setObjectName("mainAction")
            button.setProperty("action", action)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(callback)
            self.action_buttons[action] = button
            action_bar.addWidget(button)
        action_bar.addStretch()
        self.sysprep_button = QPushButton("Mở Sysprep")
        self.sysprep_button.setObjectName("systemAction")
        self.sysprep_button.clicked.connect(self._open_sysprep)
        action_bar.addWidget(self.sysprep_button)
        root_layout.addLayout(action_bar)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setObjectName("divider")
        root_layout.addWidget(divider)

        self.tool_stack = QStackedWidget()
        self.disk_page = QWidget()
        layout = QVBoxLayout(self.disk_page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        version = self.environment.version
        backend = "Clean Up Disks UI" if self.environment.vmware_exe else "Không tìm thấy VMware"
        self.environment_label = QLabel(f"VMware {version}  •  {backend}")
        self.environment_label.setObjectName("environmentBadge")
        info_row = QHBoxLayout()
        info_row.addWidget(self.environment_label)
        info_row.addStretch()
        self.summary_label = QLabel("0 VM  •  0 đã chọn")
        self.summary_label.setObjectName("muted")
        info_row.addWidget(self.summary_label)
        layout.addLayout(info_row)

        self.table = VmTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["", "Máy ảo", "Đường dẫn VMX", "Dung lượng", "Trước", "Sau", "Thu hồi", "Trạng thái"]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setToolTip(
            "Kéo chuột để bôi đen nhiều dòng; dùng Ctrl/Shift để chọn thêm; "
            "nhấn Space để chọn hoặc bỏ chọn các VM đang bôi đen."
        )
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.itemChanged.connect(self._update_selection_summary)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for column in range(3, 8):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
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
        saved_mode = self.settings.value("cleaner/mode", "scan", str)
        saved_mode_index = self.mode.findData(saved_mode)
        if saved_mode_index >= 0:
            self.mode.setCurrentIndex(saved_mode_index)
        self.mode.currentIndexChanged.connect(self._update_start_button_text)
        self.mode.currentIndexChanged.connect(self._save_cleaner_settings)
        controls.addWidget(self.mode)
        controls.addWidget(QLabel("Tác vụ"))
        self.workers = QComboBox()
        self.workers.addItems(["1", "2", "3", "4"])
        self.workers.setCurrentText(self.settings.value("cleaner/workers", "1", str))
        self.workers.currentTextChanged.connect(self._save_cleaner_settings)
        self.workers.setToolTip("Chỉ các VM có VMDK chain độc lập mới chạy đồng thời")
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

        log_header = QHBoxLayout()
        log_title = QLabel("Nhật ký hoạt động")
        log_title.setObjectName("sectionTitle")
        log_header.addWidget(log_title)
        log_header.addStretch()
        self.clear_log_button = QPushButton("Xóa log")
        self.clear_log_button.setObjectName("subtle")
        log_header.addWidget(self.clear_log_button)
        layout.addLayout(log_header)

        self.log_box = QPlainTextEdit()
        self.log_box.setObjectName("logBox")
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(500)
        self.log_box.setFixedHeight(115)
        self.log_box.setPlaceholderText("Nhật ký hoạt động")
        layout.addWidget(self.log_box)
        self.clear_log_button.clicked.connect(self.log_box.clear)

        self.clone_page = CloneDialog(self.environment, self.tool_stack, embedded=True)
        self.folder_move_page = FolderMoveDialog(self.tool_stack, embedded=True)
        self.tool_pages = {
            "disk_cleaner": self.disk_page,
            "linked_clone": self.clone_page,
            "folder_move": self.folder_move_page,
        }
        for tool in ("linked_clone", "folder_move", "disk_cleaner"):
            self.tool_stack.addWidget(self.tool_pages[tool])
        root_layout.addWidget(self.tool_stack, 1)
        self.setCentralWidget(root)
        self._update_start_button_text()
        self._apply_theme(self.theme_name, save=False)
        # The application always opens on the main VM table.
        self._open_tool("disk_cleaner")

    def _open_tool(self, tool: str) -> None:
        index = self.tool_selector.findData(tool)
        if index >= 0:
            if self.tool_selector.currentIndex() == index:
                self._tool_changed(index)
            else:
                self.tool_selector.setCurrentIndex(index)

    def _tool_changed(self, _index: int) -> None:
        tool = self.tool_selector.currentData()
        page = self.tool_pages.get(tool)
        if page is None:
            return
        self.tool_stack.setCurrentWidget(page)
        for name, button in self.nav_buttons.items():
            button.setChecked(name == tool)
        self.settings.setValue("ui/current_tool", tool)
        self.settings.sync()
        activate = getattr(page, "activate", None)
        if callable(activate):
            activate()

    def _save_cleaner_settings(self, *_args) -> None:
        self.settings.setValue("cleaner/mode", self.mode.currentData())
        self.settings.setValue("cleaner/workers", self.workers.currentText())
        self.settings.sync()

    def _toggle_theme(self) -> None:
        self._apply_theme("light" if self.theme_name == "dark" else "dark")

    def _apply_theme(self, theme: str, save: bool = True) -> None:
        self.theme_name = theme
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(LIGHT_STYLE if theme == "light" else RETRO_STYLE)
        if theme == "dark":
            self.theme_button.setText("☀")
            self.theme_button.setToolTip("Chuyển sang giao diện sáng")
        else:
            self.theme_button.setText("☾")
            self.theme_button.setToolTip("Chuyển sang giao diện tối")
        if save:
            self.settings.setValue("ui/theme", theme)
            self.settings.sync()

    def _save_all_settings(self) -> None:
        self._save_cleaner_settings()
        self.clone_page._save_settings()
        self.folder_move_page._save_settings()
        self.settings.setValue("ui/current_tool", self.tool_selector.currentData())
        self.settings.sync()
        self.save_settings_button.setText("Đã lưu ✓")
        self.statusBar().showMessage("Đã lưu toàn bộ setting.", 3000)
        QTimer.singleShot(1800, lambda: self.save_settings_button.setText("Lưu setting"))

    def _show_cleanup(self) -> None:
        self._open_tool("disk_cleaner")
        cleanup_index = self.mode.findData("cleanup")
        if cleanup_index >= 0:
            self.mode.setCurrentIndex(cleanup_index)

    def _selected_for_action(self, action_name: str) -> list[VirtualMachine]:
        self._open_tool("disk_cleaner")
        selected = self._selected_vms()
        if not selected:
            QMessageBox.information(
                self, action_name, "Hãy chọn ít nhất một máy ảo trên bảng chính."
            )
        return selected

    def _power_selected_vms(self) -> None:
        if self._vm_action_running or self.manager.running:
            return
        selected = self._selected_for_action("Tắt máy ảo")
        if not selected:
            return
        choices = ["Soft — tắt an toàn qua VMware Tools", "Hard — tắt cưỡng bức"]
        saved_mode = self.settings.value("power/mode", "hard", str)
        choice, accepted = QInputDialog.getItem(
            self, "Chế độ tắt VM", "Chọn chế độ:", choices,
            1 if saved_mode == "hard" else 0, False,
        )
        if not accepted:
            return
        mode = "hard" if choice.startswith("Hard") else "soft"
        self.settings.setValue("power/mode", mode)
        message = f"Gửi lệnh {mode.upper()} cho {len(selected)} VM đã chọn?"
        if mode == "hard":
            message += "\n\nCẢNH BÁO: dữ liệu chưa lưu trong VM có thể mất."
        answer = QMessageBox.warning(
            self, "Xác nhận tắt VM", message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        vmrun = self._action_vmrun("power/vmrun")
        if vmrun is None:
            return
        paths = [vm.vmx_path for vm in selected]
        self._start_vm_action("power", len(paths), f"Đang tắt {len(paths)} VM…")

        def work() -> None:
            try:
                failures = stop_vms(vmrun, paths, mode, self.signals.operation_progress.emit)
                self.signals.operation_finished.emit("power", failures)
            except Exception as exc:
                self.signals.operation_failed.emit("power", str(exc))

        threading.Thread(target=work, name="power-selected-vms", daemon=True).start()

    def _delete_selected_vms(self) -> None:
        if self._vm_action_running or self.manager.running:
            return
        selected = self._selected_for_action("Xóa máy ảo")
        if not selected:
            return
        inventory = self._editable_inventory()
        if inventory is None:
            return
        preview = "\n".join(f"• {vm.name}" for vm in selected[:10])
        if len(selected) > 10:
            preview += f"\n• … và {len(selected) - 10} VM khác"
        answer = QMessageBox.warning(
            self, "Xác nhận xóa VM vĩnh viễn",
            f"Các VM sau sẽ bị xóa:\n\n{preview}\n\nThao tác không thể hoàn tác. Tiếp tục?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        confirm, accepted = QInputDialog.getText(
            self, "Xác nhận lần cuối", "Nhập XOA để xác nhận xóa vĩnh viễn:"
        )
        if not accepted or confirm.strip().upper() != "XOA":
            return
        vmrun = self._action_vmrun("delete_vm/vmrun")
        if vmrun is None:
            return
        paths = [vm.vmx_path for vm in selected]
        self._start_vm_action("delete", len(paths), f"Đang xóa {len(paths)} VM…")

        def work() -> None:
            try:
                backup = backup_inventory(inventory)
                self.signals.operation_progress.emit(
                    0, len(paths), f"Đã sao lưu VMware Library: {backup.name}"
                )
                failures = delete_vms(vmrun, paths, self.signals.operation_progress.emit)
                failed_keys = {str(path.resolve()).casefold() for path, _error in failures}
                deleted_paths = [
                    path for path in paths if str(path.resolve()).casefold() not in failed_keys
                ]
                if deleted_paths:
                    remove_from_library(inventory, deleted_paths)
                self.signals.operation_finished.emit("delete", failures)
            except Exception as exc:
                self.signals.operation_failed.emit("delete", str(exc))

        threading.Thread(target=work, name="delete-selected-vms", daemon=True).start()

    def _remove_selected_from_library(self) -> None:
        if self._vm_action_running or self.manager.running:
            return
        selected = self._selected_for_action("Xóa hiển thị VMware")
        if not selected:
            return
        inventory = self._editable_inventory()
        if inventory is None:
            return
        preview = "\n".join(f"• {vm.name}" for vm in selected[:10])
        if len(selected) > 10:
            preview += f"\n• … và {len(selected) - 10} VM khác"
        answer = QMessageBox.question(
            self,
            "Chỉ xóa hiển thị khỏi VMware",
            f"Gỡ các VM sau khỏi VMware Library:\n\n{preview}\n\n"
            "File VMX, VMDK và toàn bộ dữ liệu vẫn được giữ nguyên. Tiếp tục?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        paths = [vm.vmx_path for vm in selected]
        self._start_vm_action("display", len(paths), "Đang xóa hiển thị khỏi VMware Library…")

        def work() -> None:
            try:
                result = remove_from_library(inventory, paths)
                self.signals.operation_finished.emit("display", result)
            except Exception as exc:
                self.signals.operation_failed.emit("display", str(exc))

        threading.Thread(target=work, name="remove-library-entries", daemon=True).start()

    def _editable_inventory(self) -> Path | None:
        inventory = self.environment.inventory_path
        if inventory is None or not inventory.is_file():
            QMessageBox.warning(self, "Không tìm thấy VMware Library", "Không tìm thấy inventory.vmls.")
            return None
        if is_workstation_running():
            vmrun = self.environment.vmrun_exe
            if vmrun:
                try:
                    running = list_running_vms(vmrun)
                except (OSError, RuntimeError):
                    running = []
                if running:
                    names = "\n".join(f"• {path.stem}" for path in running)
                    QMessageBox.warning(
                        self,
                        "Có VM đang chạy",
                        f"Hãy tắt các VM sau trước khi sửa VMware Library:\n\n{names}",
                    )
                    return None
            answer = QMessageBox.question(
                self,
                "Đóng VMware Workstation",
                "VMware Workstation vẫn đang mở. Tool sẽ yêu cầu VMware đóng bình thường, "
                "chờ tiến trình thoát rồi tiếp tục. Không có tiến trình nào bị cưỡng bức dừng.\n\n"
                "Đóng VMware và tiếp tục?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return None
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                closed = close_workstation_gracefully()
            finally:
                QApplication.restoreOverrideCursor()
            if not closed:
                QMessageBox.warning(
                    self,
                    "VMware chưa đóng",
                    "VMware không thoát sau thời gian chờ. Có thể đang có hộp thoại xác nhận "
                    "trong VMware. Hãy xử lý hộp thoại hoặc đóng VMware rồi thử lại.",
                )
                return None
        return inventory

    def _open_sysprep(self) -> None:
        self._open_tool("disk_cleaner")
        dialog = SysprepDialog(self)
        dialog.exec()

    def _action_vmrun(self, setting_key: str) -> Path | None:
        detected = str(self.environment.vmrun_exe or "")
        saved = self.settings.value(setting_key, detected, str).strip()
        vmrun = Path(saved or detected)
        if not vmrun.is_file():
            QMessageBox.warning(
                self, "Không tìm thấy vmrun.exe",
                "Hãy cài VMware Workstation hoặc chọn đúng vmrun.exe trong tab Linked Clone.",
            )
            return None
        return vmrun

    def _start_vm_action(self, _action: str, total: int, message: str) -> None:
        self._vm_action_running = True
        self._vm_action_total = total
        self.progress.setRange(0, total)
        self.progress.setValue(0)
        self.progress.setFormat(message)
        self.table.setEnabled(False)
        self.reload_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.select_all.setEnabled(False)
        for button in self.nav_buttons.values():
            button.setEnabled(False)
        for button in self.action_buttons.values():
            button.setEnabled(False)
        self.sysprep_button.setEnabled(False)
        self._append_log(message)

    def _operation_progress(self, current: int, total: int, message: str) -> None:
        self.progress.setMaximum(total)
        self.progress.setValue(current)
        self.progress.setFormat(f"{current} / {total}")
        self._append_log(message)

    def _operation_finished(self, action: str, result: object) -> None:
        self._vm_action_running = False
        if action == "display":
            removal = result
            if isinstance(removal, LibraryRemovalResult):
                backup_text = f"\nBản sao lưu: {removal.backup_path}" if removal.backup_path else ""
                QMessageBox.information(
                    self,
                    "Đã cập nhật VMware Library",
                    f"Đã xóa hiển thị của {removal.removed} VM. File VM vẫn được giữ nguyên."
                    f"{backup_text}",
                )
            self._restore_after_vm_action()
            self._reload()
            return
        failures = result if isinstance(result, list) else []
        label = "tắt" if action == "power" else "xóa"
        if failures:
            details = "\n".join(f"• {path.name}: {error}" for path, error in failures)
            QMessageBox.warning(self, f"Có VM chưa {label} được", details)
        else:
            QMessageBox.information(self, "Hoàn tất", f"Đã {label} các VM được chọn.")
        self._restore_after_vm_action()
        self._reload()

    def _operation_failed(self, action: str, message: str) -> None:
        self._vm_action_running = False
        self._append_log(f"Tác vụ {action} thất bại: {message}")
        QMessageBox.critical(self, "Tác vụ VMware thất bại", message)
        self._restore_after_vm_action()

    def _restore_after_vm_action(self) -> None:
        self.table.setEnabled(True)
        self.reload_button.setEnabled(True)
        self.start_button.setEnabled(bool(self.vms))
        self.select_all.setEnabled(True)
        for button in self.nav_buttons.values():
            button.setEnabled(True)
        self.sysprep_button.setEnabled(True)
        self._update_selection_summary()

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
            self.workers.setToolTip("Clean Up Disks qua GUI chỉ chạy tuần tự trên một desktop")
        elif not self.manager.running:
            self.workers.setEnabled(True)
            self.workers.setToolTip("Scan và DryRun có thể chạy đồng thời")

    def _append_log(self, message: str) -> None:
        timestamp = QDateTime.currentDateTime().toString("HH:mm:ss")
        self.log_box.appendPlainText(f"[{timestamp}] {message}")

    def _update_selection_summary(self, *_args) -> None:
        selected = sum(
            1 for row in range(self.table.rowCount())
            if self.table.item(row, 0)
            and self.table.item(row, 0).checkState() == Qt.CheckState.Checked
        )
        self.summary_label.setText(f"{self.table.rowCount()} VM  •  {selected} đã chọn")
        enabled = selected > 0 and not self.manager.running and not self._vm_action_running
        for button in self.action_buttons.values():
            button.setEnabled(enabled)

    def _reload(self) -> None:
        if self.manager.running or self._vm_action_running or self._discovering:
            return
        self._discovering = True
        self.vms = []
        self._row_by_key.clear()
        self.table.setRowCount(0)
        self._update_selection_summary()
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
        self._update_selection_summary()
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
            check.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            check.setCheckState(Qt.CheckState.Unchecked)
            name = QTableWidgetItem(vm.name)
            name.setToolTip(str(vm.vmx_path))
            path = QTableWidgetItem(str(vm.vmx_path))
            path.setToolTip(str(vm.vmx_path))
            size = QTableWidgetItem(format_bytes(vm.size_bytes))
            size.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            before = QTableWidgetItem("—")
            after = QTableWidgetItem("—")
            reclaimed = QTableWidgetItem("—")
            status = QTableWidgetItem(vm.status.value)
            status.setToolTip(vm.status_detail)
            self.table.setItem(row, 0, check)
            self.table.setItem(row, 1, name)
            self.table.setItem(row, 2, path)
            self.table.setItem(row, 3, size)
            self.table.setItem(row, 4, before)
            self.table.setItem(row, 5, after)
            self.table.setItem(row, 6, reclaimed)
            self.table.setItem(row, 7, status)
            for column in (3, 4, 5, 6):
                self.table.item(row, column).setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
        if not self.vms:
            self.progress.setFormat("Không tìm thấy VM trong VMware Library")
            self.start_button.setEnabled(False)

    def _toggle_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(state)
        self.select_all.setText("Bỏ chọn tất cả" if checked else "Chọn tất cả")
        self._update_selection_summary()

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
                "Xác nhận VMware Clean Up Disks",
                "Ứng dụng sẽ điều khiển đúng luồng VMware Workstation:\n"
                "VM > Manage > Clean Up Disks > Clean up now.\n\n"
                "VMware sẽ được mở và hiển thị trên màn hình trong suốt quá trình. "
                "Cleanup chạy tuần tự; không thao tác chuột/phím vào VMware, không tắt máy host "
                "hoặc cưỡng bức dừng VMware trong lúc ghi dữ liệu.\n\n"
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
            row = self._row_by_key.get(vm.key)
            if row is not None:
                for column in (4, 5, 6):
                    self.table.item(row, column).setText("—")
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
        for button in self.nav_buttons.values():
            button.setEnabled(not running)
        for button in self.action_buttons.values():
            button.setEnabled(not running and bool(self._selected_vms()))
        self.sysprep_button.setEnabled(not running)

    def _set_vm_status(self, vm: VirtualMachine, status: CleanupStatus, detail: str) -> None:
        row = self._row_by_key.get(vm.key)
        if row is None:
            return
        vm.status = status
        vm.status_detail = detail
        item = self.table.item(row, 7)
        item.setText(status.value)
        item.setToolTip(detail)
        colors = {
            CleanupStatus.RUNNING: "#b4a27f",
            CleanupStatus.DONE: "#91a397",
            CleanupStatus.FAILED: "#b87a7e",
            CleanupStatus.SKIPPED: "#a99d7d",
        }
        item.setForeground(QColor(colors.get(status, "#bfc3c8")))
        if status != CleanupStatus.WAITING:
            message = f"{vm.name}: {status.value}"
            if detail:
                message += f" — {detail}"
            self._append_log(message)

    def _task_finished(self, result: CleanupResult) -> None:
        row = self._row_by_key.get(result.vm.key)
        if row is not None:
            self.table.item(row, 4).setText(
                format_gb(result.before_bytes) if result.before_bytes else "—"
            )
            self.table.item(row, 5).setText(
                format_gb(result.after_bytes) if result.after_bytes else "—"
            )
            self.table.item(row, 6).setText(
                format_gb(result.reclaimed_bytes) if result.before_bytes else "—"
            )
        self._processed += 1
        self.progress.setValue(self._processed)
        suffix = ""
        if result.reclaimed_bytes:
            suffix = f"  •  Đã thu hồi {format_gb(result.reclaimed_bytes)}"
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
        if self.clone_page._running:
            QMessageBox.information(
                self,
                "Linked Clone đang chạy",
                "Hãy yêu cầu hủy và chờ clone hiện tại hoàn tất trước khi đóng ứng dụng.",
            )
            event.ignore()
            return
        if self._vm_action_running:
            QMessageBox.information(
                self,
                "Tác vụ VM đang chạy",
                "Hãy chờ tác vụ tắt hoặc xóa VM hoàn tất trước khi đóng ứng dụng.",
            )
            event.ignore()
            return
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


RETRO_STYLE = """
QWidget { background: #25272b; color: #d1d4d8; font-size: 13px; }
QMainWindow, QDialog { background: #25272b; }
QLabel#appTitle { color: #d8dadd; font-size: 22px; font-weight: 700; }
QLabel#sectionTitle { color: #c7cacf; font-size: 13px; font-weight: 600; }
QLabel#muted { color: #92979f; }
QLabel#actionLabel { color: #9da2aa; font-size: 11px; font-weight: 700;
    padding-right: 5px; }
QLabel#environmentBadge { background: #32413f; color: #b9cbc6;
    border: 1px solid #4d5e5a; border-radius: 8px; padding: 4px 9px; }
QFrame#divider { color: #41454b; background: #41454b; max-height: 1px; }
QTableWidget { background: #2c2f33; alternate-background-color: #303338;
    color: #d5d8dc; border: 1px solid #454a51; border-radius: 5px;
    gridline-color: #3d4248; selection-background-color: #4b535e;
    selection-color: #edf0f3; }
QHeaderView::section { background: #383c42; color: #c9cdd2; padding: 8px;
    border: 0; border-right: 1px solid #484d54; border-bottom: 1px solid #4c5158;
    font-weight: 700; }
QTableCornerButton::section { background: #383c42; border: 1px solid #484d54; }
QPushButton, QComboBox { background: #34373c; color: #d3d6da;
    border: 1px solid #4d5259; border-radius: 5px; padding: 7px 11px;
    min-height: 26px; }
QPushButton#navButton { border: 1px solid #50555d; border-radius: 6px;
    min-height: 28px; padding: 9px 14px; font-weight: 700; }
QPushButton#navButton[accent="lavender"] { background: #4a4d59; color: #d8d9e0; }
QPushButton#navButton[accent="mint"] { background: #3e504a; color: #cbd8d2; }
QPushButton#navButton[accent="rose"] { background: #52444a; color: #d9cdd1; }
QPushButton#navButton:hover { border: 2px solid #78808a; padding: 8px 13px; }
QPushButton#navButton:checked { background: #555b65; border: 2px solid #858d97;
    color: #eef0f2; padding: 7px 12px; }
QPushButton#saveSettings { background: #555143; color: #d9d1b8;
    border: 1px solid #77715f; border-radius: 6px; font-weight: 700; }
QPushButton#saveSettings:hover { background: #625d4d; border-color: #918975; }
QPushButton#themeToggle { background: #34373c; color: #d8dadd; border: 1px solid #555b63;
    border-radius: 6px; font-size: 18px; padding: 4px; }
QPushButton#themeToggle:hover { background: #41454b; border-color: #747b84; }
QPushButton#mainAction { color: #e7e9ec; border: 1px solid #646a72;
    border-radius: 5px; font-weight: 700; padding: 8px 16px; }
QPushButton#mainAction[action="cleanup"] { background: #515565; }
QPushButton#mainAction[action="power"] { background: #665044; }
QPushButton#mainAction[action="delete"] { background: #66464a; }
QPushButton#mainAction[action="unregister"] { background: #4b5b61; }
QPushButton#mainAction:hover { border: 2px solid #8a919a; padding: 7px 15px; }
QPushButton#systemAction { background: #40595a; color: #d8e0de;
    border: 1px solid #5c7172; border-radius: 5px; font-weight: 700;
    padding: 8px 16px; }
QLineEdit, QSpinBox, QDoubleSpinBox, QListWidget { background: #2b2e32;
    color: #d7dade; border: 1px solid #4d535a; border-radius: 4px; padding: 6px;
    min-height: 26px; selection-background-color: #505a66; }
QComboBox QAbstractItemView { background: #2e3135; color: #d4d7db;
    border: 1px solid #4d535a; selection-background-color: #4b535e; }
QCheckBox { min-height: 26px; spacing: 7px; color: #d0d3d7; }
QGroupBox { background: #292c30; border: 1px solid #44494f; border-radius: 5px;
    margin-top: 9px; padding: 12px 8px 8px 8px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 5px;
    color: #c3c7cb; }
QLabel#warning { background: #403a30; color: #cfc1a5; border: 1px solid #5c5345;
    border-radius: 4px; padding: 8px; }
QLabel#deleteWarning { background: #403134; color: #d3b8b8;
    border: 1px solid #60484c; border-radius: 4px; padding: 9px; font-weight: 600; }
QPushButton:hover, QComboBox:hover, QLineEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus { border-color: #737b85; }
QPushButton:disabled { color: #686d73; background: #2e3034; border-color: #3b3f44; }
QPushButton#primary { background: #4e5f54; color: #dce4df;
    border-color: #687a6e; font-weight: 700; }
QPushButton#danger { background: #603f43; color: #ddc4c4;
    border-color: #7b5055; font-weight: 700; }
QPushButton#subtle { padding: 4px 8px; color: #959aa1; background: transparent; }
QProgressBar { background: #2d3034; color: #d2d5d8; border: 1px solid #484d53;
    border-radius: 4px; text-align: center; min-height: 26px; }
QProgressBar::chunk { background: #58645c; border-radius: 3px; }
QToolTip { background: #35383d; color: #dde0e3; border: 1px solid #60666e; }
QPlainTextEdit#logBox { background: #202226; color: #bfc3c8;
    border: 1px solid #42464c; border-radius: 4px; padding: 5px;
    font-family: Consolas, monospace; font-size: 11px; }
QStatusBar { background: #292c30; color: #979ca3; border-top: 1px solid #41454a; }
QScrollBar:vertical { background: #282b2f; width: 12px; margin: 0; }
QScrollBar::handle:vertical { background: #50555c; min-height: 24px; border-radius: 4px; }
QScrollBar:horizontal { background: #282b2f; height: 12px; margin: 0; }
QScrollBar::handle:horizontal { background: #50555c; min-width: 24px; border-radius: 4px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
"""

LIGHT_STYLE = RETRO_STYLE + """
QWidget { background: #e7e3da; color: #383a3d; }
QMainWindow, QDialog { background: #e7e3da; }
QLabel#appTitle { color: #3f4144; }
QLabel#sectionTitle { color: #4c4f52; }
QLabel#muted, QLabel#actionLabel { color: #71757a; }
QLabel#environmentBadge { background: #d7e1dd; color: #405b55;
    border-color: #aebeb8; }
QFrame#divider { color: #bcb7ad; background: #bcb7ad; }
QTableWidget { background: #f2efe8; alternate-background-color: #ebe7df;
    color: #35383b; border-color: #aaa69e; gridline-color: #cbc6bc;
    selection-background-color: #bcc7c6; selection-color: #25282a; }
QHeaderView::section, QTableCornerButton::section { background: #d4d0c7;
    color: #46494c; border-color: #aaa69e; }
QPushButton, QComboBox { background: #ddd9d0; color: #3f4245;
    border-color: #aaa69e; }
QPushButton#navButton { border-color: #a49f96; }
QPushButton#navButton[accent="lavender"] { background: #c9c7d0; color: #41434b; }
QPushButton#navButton[accent="mint"] { background: #c5d1cb; color: #354940; }
QPushButton#navButton[accent="rose"] { background: #d5c6ca; color: #514045; }
QPushButton#navButton:hover { border-color: #777c82; }
QPushButton#navButton:checked { background: #b7bdc2; border-color: #6f777e;
    color: #292c2e; }
QPushButton#saveSettings { background: #d6ceb4; color: #514b38; border-color: #aaa07c; }
QPushButton#saveSettings:hover { background: #c9bea0; border-color: #8f8361; }
QPushButton#themeToggle { background: #d7d3cb; color: #414448; border-color: #a7a39b; }
QPushButton#themeToggle:hover { background: #c8c4bc; border-color: #85898e; }
QPushButton#mainAction { color: #f4f4f2; border-color: #73777c; }
QPushButton#mainAction[action="cleanup"] { background: #686b78; }
QPushButton#mainAction[action="power"] { background: #806556; }
QPushButton#mainAction[action="delete"] { background: #7b565a; }
QPushButton#mainAction[action="unregister"] { background: #5f7479; }
QPushButton#systemAction { background: #547071; color: #f0f2ef; border-color: #70898a; }
QLineEdit, QSpinBox, QDoubleSpinBox, QListWidget { background: #f2efe8;
    color: #35383b; border-color: #aaa69e; selection-background-color: #bec7c7; }
QComboBox QAbstractItemView { background: #f0ede6; color: #383b3e;
    border-color: #aaa69e; selection-background-color: #c1c9c8; }
QCheckBox { color: #414448; }
QGroupBox { background: #e1ddd4; border-color: #b5b0a7; }
QGroupBox::title { color: #505356; }
QLabel#warning { background: #ded5c4; color: #5d5140; border-color: #b8aa91; }
QLabel#deleteWarning { background: #decaca; color: #62484b; border-color: #b99b9e; }
QPushButton:hover, QComboBox:hover, QLineEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus { border-color: #777d83; }
QPushButton:disabled { color: #999b9d; background: #d8d4cc; border-color: #c1bdb5; }
QPushButton#primary { background: #65776c; color: #f0f2ef; border-color: #7f9186; }
QPushButton#danger { background: #80595c; color: #f4eceb; border-color: #9a7477; }
QPushButton#subtle { color: #6e7276; background: transparent; }
QProgressBar { background: #d4d0c8; color: #3e4144; border-color: #aaa69e; }
QProgressBar::chunk { background: #788a80; }
QToolTip { background: #f2efe8; color: #34373a; border-color: #92979c; }
QPlainTextEdit#logBox { background: #f1eee7; color: #464a4d; border-color: #aaa69e; }
QStatusBar { background: #dcd8d0; color: #666a6e; border-top-color: #b7b2a9; }
QScrollBar:vertical, QScrollBar:horizontal { background: #d4d0c8; }
QScrollBar::handle:vertical, QScrollBar::handle:horizontal { background: #999b9c; }
"""

# Backward-compatible name used by existing integrations and tests.
PASTEL_STYLE = RETRO_STYLE


def run_app() -> int:
    configure_logging()
    app = QApplication(sys.argv)
    app._interaction_animator = install_interaction_animations(app)
    app.setOrganizationName("VMwareToolkit")
    app.setApplicationName("VMware Toolkit")
    app.setStyle("Fusion")
    initial_theme = QSettings().value("ui/theme", "dark", str)
    app.setStyleSheet(LIGHT_STYLE if initial_theme == "light" else RETRO_STYLE)
    window = MainWindow()
    window.show()
    return app.exec()

