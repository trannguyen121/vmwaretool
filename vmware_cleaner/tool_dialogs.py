from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from .models import VMwareEnvironment
from .discovery import discover_vms
from .folder_tools import move_folder_same_drive, validate_same_drive_move
from .script_defaults import load_script_defaults
from .vm_tools import (
    CloneSettings,
    create_linked_clones,
    delete_vms,
    list_running_vms,
    list_snapshots,
    stop_vms,
)


class ToolSignals(QObject):
    progress = Signal(int, int, str)
    log = Signal(str)
    finished = Signal(object)
    failed = Signal(str)
    items = Signal(object)


def _path_row(edit: QLineEdit, button: QPushButton) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    row.addWidget(edit, 1)
    row.addWidget(button)
    return row


def _configure_responsive_form(form: QFormLayout) -> None:
    """Keep fields usable when an embedded tool page is resized."""
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
    form.setHorizontalSpacing(12)
    form.setVerticalSpacing(9)


class CloneDialog(QDialog):
    def __init__(self, environment: VMwareEnvironment, parent=None, embedded: bool = False) -> None:
        super().__init__(parent)
        self.embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.environment = environment
        self.settings = QSettings()
        self.script_defaults = load_script_defaults()
        self._import_script_defaults()
        self._apply_hardware_defaults()
        self.cancel_event = threading.Event()
        self.signals = ToolSignals()
        self._running = False
        self.setWindowTitle("Tạo VMware Linked Clone")
        self.resize(720, 700)
        self.setMinimumWidth(0 if embedded else 620)
        self._build_ui()
        self._load_settings()
        self._connect_auto_save()
        self.signals.progress.connect(self._on_progress)
        self.signals.log.connect(self.log_box.appendPlainText)
        self.signals.finished.connect(self._on_finished)
        self.signals.failed.connect(self._on_failed)
        self.signals.items.connect(self._on_snapshots)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Tạo nhiều linked clone từ một snapshot, sau đó đặt CPU, RAM, NAT và MAC riêng "
            "cho từng card mạng. Clone vẫn phụ thuộc vào snapshot và đĩa của VM mẫu."
        )
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        layout.addWidget(intro)

        source_group = QGroupBox("Nguồn và VMware")
        source_form = QFormLayout(source_group)
        _configure_responsive_form(source_form)
        self.vmrun_edit = QLineEdit()
        vmrun_browse = QPushButton("Chọn…")
        vmrun_browse.clicked.connect(self._browse_vmrun)
        source_form.addRow("vmrun.exe", _path_row(self.vmrun_edit, vmrun_browse))
        self.template_edit = QLineEdit()
        template_browse = QPushButton("Chọn…")
        template_browse.clicked.connect(self._browse_template)
        source_form.addRow("VMX mẫu", _path_row(self.template_edit, template_browse))
        snapshot_row = QHBoxLayout()
        self.snapshot_combo = QComboBox()
        self.snapshot_combo.setEditable(True)
        snapshot_row.addWidget(self.snapshot_combo, 1)
        self.snapshot_button = QPushButton("Đọc snapshot")
        self.snapshot_button.clicked.connect(self._refresh_snapshots)
        snapshot_row.addWidget(self.snapshot_button)
        source_form.addRow("Snapshot", snapshot_row)
        layout.addWidget(source_group)

        config_group = QGroupBox("Cấu hình clone")
        config_form = QFormLayout(config_group)
        _configure_responsive_form(config_form)
        self.output_edit = QLineEdit()
        output_browse = QPushButton("Chọn…")
        output_browse.clicked.connect(self._browse_output)
        config_form.addRow("Thư mục đầu ra", _path_row(self.output_edit, output_browse))
        self.prefix_edit = QLineEdit("Clone")
        config_form.addRow("Tiền tố tên", self.prefix_edit)
        self.count_spin = QSpinBox()
        self.count_spin.setRange(1, 999)
        self.count_spin.setValue(1)
        config_form.addRow("Số lượng", self.count_spin)
        self.cpu_spin = QSpinBox()
        self.cpu_spin.setRange(1, 128)
        self.cpu_spin.setValue(2)
        config_form.addRow("CPU / VM", self.cpu_spin)
        self.ram_spin = QDoubleSpinBox()
        self.ram_spin.setDecimals(3)
        self.ram_spin.setRange(0.5, 1024.0)
        self.ram_spin.setSingleStep(0.5)
        self.ram_spin.setValue(4.0)
        self.ram_spin.setSuffix(" GB")
        config_form.addRow("RAM / VM", self.ram_spin)
        self.start_check = QCheckBox("Tự bật VM bằng chế độ nogui sau khi tạo")
        config_form.addRow("", self.start_check)
        layout.addWidget(config_group)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("Sẵn sàng")
        layout.addWidget(self.progress)
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumBlockCount(500)
        self.log_box.setMinimumHeight(110)
        self.log_box.setObjectName("logBox")
        layout.addWidget(self.log_box, 1)

        buttons = QHBoxLayout()
        self.create_button = QPushButton("Tạo linked clone")
        self.create_button.setObjectName("primary")
        self.create_button.clicked.connect(self._start)
        self.cancel_button = QPushButton("Hủy sau clone hiện tại")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        self.close_button = QPushButton("Đóng")
        self.close_button.clicked.connect(self.close)
        self.close_button.setVisible(not self.embedded)
        buttons.addWidget(self.create_button, 1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    def _load_settings(self) -> None:
        defaults = self.script_defaults
        detected = str(self.environment.vmrun_exe or "")
        saved_vmrun = self.settings.value("clone/vmrun", detected, str).strip()
        self.vmrun_edit.setText(saved_vmrun or detected)
        self.template_edit.setText(self.settings.value("clone/template", defaults.template_vmx, str))
        self.output_edit.setText(self.settings.value("clone/output", defaults.output_root, str))
        self.snapshot_combo.setCurrentText(self.settings.value("clone/snapshot", defaults.snapshot_name, str))
        self.prefix_edit.setText(self.settings.value("clone/prefix", defaults.name_prefix, str))
        self.count_spin.setValue(self.settings.value("clone/count", defaults.vm_count, int))
        self.cpu_spin.setValue(self.settings.value("clone/cpu", 4, int))
        ram_mb = self.settings.value("clone/ram", 4096, int)
        self.ram_spin.setValue(ram_mb / 1024)
        self.start_check.setChecked(self.settings.value("clone/start", defaults.start_vms, bool))

    def _import_script_defaults(self) -> None:
        if self.settings.value("defaults/clone_script_v1", False, bool):
            return
        defaults = self.script_defaults
        if not defaults.template_vmx:
            return
        migrations = (
            ("clone/template", defaults.template_vmx, {"", None}),
            ("clone/output", defaults.output_root, {"", None}),
            ("clone/snapshot", defaults.snapshot_name, {"", None}),
            ("clone/prefix", defaults.name_prefix, {"", "Clone", None}),
            ("clone/count", defaults.vm_count, {1, "1", None}),
            ("clone/cpu", defaults.cpu, {2, "2", None}),
            ("clone/ram", defaults.ram_mb, {4096, "4096", None}),
        )
        for key, value, old_defaults in migrations:
            current = self.settings.value(key, None)
            if value not in {"", None} and current in old_defaults:
                self.settings.setValue(key, value)
        if not self.settings.contains("clone/start"):
            self.settings.setValue("clone/start", defaults.start_vms)
        self.settings.setValue("defaults/clone_script_v1", True)
        self.settings.sync()

    def _apply_hardware_defaults(self) -> None:
        if self.settings.value("defaults/hardware_4core_4gb_v1", False, bool):
            return
        current_cpu = self.settings.value("clone/cpu", None)
        current_ram = self.settings.value("clone/ram", None)
        if current_cpu in {None, 2, "2", self.script_defaults.cpu, str(self.script_defaults.cpu)}:
            self.settings.setValue("clone/cpu", 4)
        if current_ram in {None, 4096, "4096", self.script_defaults.ram_mb, str(self.script_defaults.ram_mb)}:
            self.settings.setValue("clone/ram", 4096)
        self.settings.setValue("defaults/hardware_4core_4gb_v1", True)
        self.settings.sync()

    def _save_settings(self, *_args) -> None:
        self.settings.setValue("clone/vmrun", self.vmrun_edit.text().strip())
        self.settings.setValue("clone/template", self.template_edit.text().strip())
        self.settings.setValue("clone/output", self.output_edit.text().strip())
        self.settings.setValue("clone/snapshot", self.snapshot_combo.currentText().strip())
        self.settings.setValue("clone/prefix", self.prefix_edit.text().strip())
        self.settings.setValue("clone/count", self.count_spin.value())
        self.settings.setValue("clone/cpu", self.cpu_spin.value())
        self.settings.setValue("clone/ram", round(self.ram_spin.value() * 1024))
        self.settings.setValue("clone/start", self.start_check.isChecked())
        self.settings.sync()

    def _connect_auto_save(self) -> None:
        for edit in (self.vmrun_edit, self.template_edit, self.output_edit, self.prefix_edit):
            edit.textChanged.connect(self._save_settings)
        self.snapshot_combo.currentTextChanged.connect(self._save_settings)
        self.count_spin.valueChanged.connect(self._save_settings)
        self.cpu_spin.valueChanged.connect(self._save_settings)
        self.ram_spin.valueChanged.connect(self._save_settings)
        self.start_check.toggled.connect(self._save_settings)

    def _browse_vmrun(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Chọn vmrun.exe", self.vmrun_edit.text(), "vmrun.exe (vmrun.exe);;EXE (*.exe)")
        if path:
            self.vmrun_edit.setText(path)

    def _browse_template(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Chọn VM mẫu", self.template_edit.text(), "VMware VMX (*.vmx)")
        if path:
            self.template_edit.setText(path)

    def _browse_output(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Chọn thư mục chứa clone", self.output_edit.text())
        if path:
            self.output_edit.setText(path)

    def _refresh_snapshots(self) -> None:
        vmrun, vmx = Path(self.vmrun_edit.text().strip()), Path(self.template_edit.text().strip())
        self.snapshot_button.setEnabled(False)
        self.log_box.appendPlainText("Đang đọc danh sách snapshot…")

        def work() -> None:
            try:
                self.signals.items.emit(list_snapshots(vmrun, vmx))
            except Exception as exc:
                self.signals.failed.emit(str(exc))

        threading.Thread(target=work, name="snapshot-list", daemon=True).start()

    def _on_snapshots(self, snapshots: list[str]) -> None:
        current = self.snapshot_combo.currentText()
        self.snapshot_combo.clear()
        self.snapshot_combo.addItems(snapshots)
        if current and current in snapshots:
            self.snapshot_combo.setCurrentText(current)
        self.snapshot_button.setEnabled(True)
        self.log_box.appendPlainText(f"Đã tìm thấy {len(snapshots)} snapshot.")

    def _clone_settings(self) -> CloneSettings:
        return CloneSettings(
            template_vmx=Path(self.template_edit.text().strip()),
            output_root=Path(self.output_edit.text().strip()),
            snapshot_name=self.snapshot_combo.currentText().strip(),
            vm_count=self.count_spin.value(),
            name_prefix=self.prefix_edit.text().strip(),
            cpu=self.cpu_spin.value(),
            ram_mb=round(self.ram_spin.value() * 1024),
            start_vms=self.start_check.isChecked(),
        )

    def _start(self) -> None:
        try:
            if not self.output_edit.text().strip():
                raise ValueError("Hãy chọn thư mục đầu ra.")
            clone_settings = self._clone_settings()
            clone_settings.validate()
            vmrun = Path(self.vmrun_edit.text().strip())
            if not vmrun.is_file():
                raise ValueError(f"Không tìm thấy vmrun.exe: {vmrun}")
        except ValueError as exc:
            QMessageBox.warning(self, "Cấu hình chưa hợp lệ", str(exc))
            return
        answer = QMessageBox.question(
            self,
            "Xác nhận tạo linked clone",
            f"Tạo tối đa {clone_settings.vm_count} linked clone trong:\n{clone_settings.output_root}\n\n"
            "Không xóa hoặc di chuyển snapshot/đĩa của VM mẫu khi clone còn được sử dụng. Tiếp tục?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._save_settings()
        self.cancel_event.clear()
        self._running = True
        self.create_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, clone_settings.vm_count)
        self.progress.setValue(0)
        self.log_box.appendPlainText("Bắt đầu tạo linked clone…")

        def work() -> None:
            try:
                result = create_linked_clones(
                    vmrun, clone_settings, self.signals.progress.emit,
                    self.signals.log.emit, self.cancel_event,
                )
                self.signals.finished.emit(result)
            except Exception as exc:
                self.signals.failed.emit(str(exc))

        threading.Thread(target=work, name="linked-clone", daemon=True).start()

    def _cancel(self) -> None:
        self.cancel_event.set()
        self.cancel_button.setEnabled(False)
        self.log_box.appendPlainText("Đã yêu cầu hủy sau clone hiện tại…")

    def _on_progress(self, current: int, total: int, message: str) -> None:
        self.progress.setMaximum(total)
        self.progress.setValue(current)
        self.progress.setFormat(f"{current} / {total}")

    def _on_finished(self, created: list[Path]) -> None:
        self._running = False
        self.create_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.setFormat(f"Hoàn tất: đã tạo {len(created)} VM")
        self.log_box.appendPlainText(f"Hoàn tất. Đã tạo {len(created)} linked clone.")

    def _on_failed(self, message: str) -> None:
        self.snapshot_button.setEnabled(True)
        if self._running:
            self._running = False
            self.create_button.setEnabled(True)
            self.cancel_button.setEnabled(False)
        self.log_box.appendPlainText(f"LỖI: {message}")
        QMessageBox.critical(self, "Lỗi VMware", message)

    def closeEvent(self, event) -> None:
        if self._running:
            QMessageBox.information(self, "Đang chạy", "Hãy hủy và chờ clone hiện tại hoàn tất trước khi đóng.")
            event.ignore()
            return
        self._save_settings()
        event.accept()


class FolderMoveDialog(QDialog):
    def __init__(self, parent=None, embedded: bool = False) -> None:
        super().__init__(parent)
        self.embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.settings = QSettings()
        self.setWindowTitle("Di chuyển hoặc đổi địa chỉ Folder")
        self.resize(720, 480)
        self._build_ui()
        self._load_settings()
        self.source_edit.textChanged.connect(self._save_settings)
        self.destination_edit.textChanged.connect(self._save_settings)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Di chuyển hoặc đổi tên một folder trong cùng ổ đĩa. Thao tác cùng volume thường "
            "chỉ cập nhật địa chỉ nên hoàn tất nhanh và không sao chép lại toàn bộ dữ liệu."
        )
        intro.setWordWrap(True)
        intro.setObjectName("muted")
        layout.addWidget(intro)

        form_group = QGroupBox("Địa chỉ Folder")
        form = QFormLayout(form_group)
        _configure_responsive_form(form)
        self.source_edit = QLineEdit()
        source_button = QPushButton("Chọn…")
        source_button.clicked.connect(self._browse_source)
        form.addRow("Folder nguồn", _path_row(self.source_edit, source_button))
        self.destination_edit = QLineEdit()
        destination_button = QPushButton("Chọn thư mục cha…")
        destination_button.clicked.connect(self._browse_destination_parent)
        form.addRow("Địa chỉ mới", _path_row(self.destination_edit, destination_button))
        layout.addWidget(form_group)

        warning = QLabel(
            "Địa chỉ mới phải cùng ổ đĩa, chưa tồn tại và có thư mục cha hợp lệ. "
            "Công cụ không ghi đè hoặc gộp dữ liệu vào folder khác."
        )
        warning.setObjectName("warning")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        self.log_box = QPlainTextEdit()
        self.log_box.setObjectName("logBox")
        self.log_box.setReadOnly(True)
        self.log_box.setPlaceholderText("Kết quả di chuyển folder")
        layout.addWidget(self.log_box, 1)

        buttons = QHBoxLayout()
        self.move_button = QPushButton("Di chuyển / đổi địa chỉ")
        self.move_button.setObjectName("primary")
        self.move_button.clicked.connect(self._move)
        self.close_button = QPushButton("Đóng")
        self.close_button.clicked.connect(self.close)
        self.close_button.setVisible(not self.embedded)
        buttons.addWidget(self.move_button, 1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    def _load_settings(self) -> None:
        self.source_edit.setText(self.settings.value("folder_move/source", "", str))
        self.destination_edit.setText(self.settings.value("folder_move/destination", "", str))

    def _save_settings(self, *_args) -> None:
        self.settings.setValue("folder_move/source", self.source_edit.text().strip())
        self.settings.setValue("folder_move/destination", self.destination_edit.text().strip())
        self.settings.sync()

    def _browse_source(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Chọn folder nguồn", self.source_edit.text())
        if path:
            self.source_edit.setText(path)
            if not self.destination_edit.text().strip():
                source = Path(path)
                self.destination_edit.setText(str(source.parent / f"{source.name}-Moi"))

    def _browse_destination_parent(self) -> None:
        current = self.destination_edit.text().strip()
        start = str(Path(current).parent) if current else self.source_edit.text()
        parent = QFileDialog.getExistingDirectory(self, "Chọn thư mục cha cho địa chỉ mới", start)
        if not parent:
            return
        source_name = Path(self.source_edit.text().strip()).name or "Folder-Moi"
        candidate = Path(parent) / source_name
        if self.source_edit.text().strip() and candidate == Path(self.source_edit.text().strip()):
            candidate = Path(parent) / f"{source_name}-Moi"
        self.destination_edit.setText(str(candidate))

    def _move(self) -> None:
        try:
            source, destination = validate_same_drive_move(
                Path(self.source_edit.text().strip()),
                Path(self.destination_edit.text().strip()),
            )
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Địa chỉ chưa hợp lệ", str(exc))
            return
        answer = QMessageBox.warning(
            self,
            "Xác nhận di chuyển Folder",
            f"Di chuyển:\n{source}\n\nSang địa chỉ mới:\n{destination}\n\nTiếp tục?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = move_folder_same_drive(source, destination)
        except (OSError, RuntimeError, ValueError) as exc:
            self.log_box.appendPlainText(f"LỖI: {exc}")
            QMessageBox.critical(self, "Không di chuyển được Folder", str(exc))
            return
        self.log_box.appendPlainText(f"Đã chuyển thành công:\n{source}\n→ {result}\n")
        self.source_edit.setText(str(result))
        self.destination_edit.clear()
        QMessageBox.information(self, "Hoàn tất", f"Folder hiện ở địa chỉ:\n{result}")


class PowerDialog(QDialog):
    def __init__(self, environment: VMwareEnvironment, parent=None, embedded: bool = False) -> None:
        super().__init__(parent)
        self.embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.environment = environment
        self.settings = QSettings()
        self.script_defaults = load_script_defaults()
        self._import_script_defaults()
        self.signals = ToolSignals()
        self.cancel_event = threading.Event()
        self._running = False
        self._loaded_once = False
        self.setWindowTitle("Tắt các máy ảo đang chạy")
        self.resize(680, 560)
        self._build_ui()
        self._load_settings()
        self.vmrun_edit.textChanged.connect(self._save_settings)
        self.mode_combo.currentIndexChanged.connect(self._save_settings)
        self.signals.items.connect(self._set_vms)
        self.signals.progress.connect(self._on_progress)
        self.signals.finished.connect(self._on_finished)
        self.signals.failed.connect(self._on_failed)
        if not embedded:
            self._refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        detected = str(self.environment.vmrun_exe or "")
        saved_vmrun = self.settings.value("power/vmrun", detected, str).strip()
        self.vmrun_edit = QLineEdit(saved_vmrun or detected)
        browse = QPushButton("Chọn…")
        browse.clicked.connect(self._browse_vmrun)
        form.addRow("vmrun.exe", _path_row(self.vmrun_edit, browse))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Soft — tắt an toàn qua VMware Tools", "soft")
        self.mode_combo.addItem("Hard — tắt cưỡng bức, có thể mất dữ liệu", "hard")
        form.addRow("Chế độ", self.mode_combo)
        layout.addLayout(form)
        self.warning = QLabel("Nên dùng Soft. Hard tương đương rút điện và có thể làm mất dữ liệu hoặc hỏng filesystem trong VM.")
        self.warning.setWordWrap(True)
        self.warning.setObjectName("warning")
        layout.addWidget(self.warning)
        self.vm_list = QListWidget()
        layout.addWidget(self.vm_list, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setFormat("Đang đọc VM đang chạy…")
        layout.addWidget(self.progress)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton("Làm mới")
        self.refresh_button.clicked.connect(self._refresh)
        self.stop_button = QPushButton("Tắt tất cả VM trong danh sách")
        self.stop_button.setObjectName("danger")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._stop_all)
        self.close_button = QPushButton("Đóng")
        self.close_button.clicked.connect(self.close)
        self.close_button.setVisible(not self.embedded)
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.stop_button, 1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    def _load_settings(self) -> None:
        mode = self.settings.value("power/mode", self.script_defaults.stop_mode, str)
        index = self.mode_combo.findData(mode)
        if index >= 0:
            self.mode_combo.setCurrentIndex(index)

    def _import_script_defaults(self) -> None:
        if self.settings.value("defaults/power_script_v1", False, bool):
            return
        if not self.script_defaults.template_vmx:
            return
        current = self.settings.value("power/mode", None)
        if current in {None, "", "soft"} and self.script_defaults.stop_mode in {"soft", "hard"}:
            self.settings.setValue("power/mode", self.script_defaults.stop_mode)
        self.settings.setValue("defaults/power_script_v1", True)
        self.settings.sync()

    def _save_settings(self, *_args) -> None:
        self.settings.setValue("power/vmrun", self.vmrun_edit.text().strip())
        self.settings.setValue("power/mode", self.mode_combo.currentData())
        self.settings.sync()

    def _browse_vmrun(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Chọn vmrun.exe", self.vmrun_edit.text(), "vmrun.exe (vmrun.exe);;EXE (*.exe)")
        if path:
            self.vmrun_edit.setText(path)

    def activate(self) -> None:
        if not self._loaded_once and not self._running:
            self._loaded_once = True
            self._refresh()

    def _refresh(self) -> None:
        self.refresh_button.setEnabled(False)
        self.stop_button.setEnabled(False)
        self.progress.setRange(0, 0)

        def work() -> None:
            try:
                self.signals.items.emit(list_running_vms(Path(self.vmrun_edit.text().strip())))
            except Exception as exc:
                self.signals.failed.emit(str(exc))

        threading.Thread(target=work, name="running-vms", daemon=True).start()

    def _set_vms(self, vms: list[Path]) -> None:
        self.vm_list.clear()
        for vmx in vms:
            self.vm_list.addItem(str(vmx))
        self.refresh_button.setEnabled(True)
        self.stop_button.setEnabled(bool(vms))
        self.progress.setRange(0, max(1, len(vms)))
        self.progress.setValue(0)
        self.progress.setFormat(f"{len(vms)} VM đang chạy" if vms else "Không có VM đang chạy")

    def _stop_all(self) -> None:
        vms = [Path(self.vm_list.item(i).text()) for i in range(self.vm_list.count())]
        mode = self.mode_combo.currentData()
        message = f"Tắt {len(vms)} VM bằng chế độ {mode.upper()}?"
        if mode == "hard":
            message += "\n\nCẢNH BÁO: dữ liệu chưa lưu có thể mất."
        answer = QMessageBox.warning(
            self, "Xác nhận tắt VM", message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._save_settings()
        self._running = True
        self.cancel_event.clear()
        self.stop_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.progress.setRange(0, len(vms))

        def work() -> None:
            try:
                failures = stop_vms(
                    Path(self.vmrun_edit.text().strip()), vms, mode,
                    self.signals.progress.emit, self.cancel_event,
                )
                self.signals.finished.emit(failures)
            except Exception as exc:
                self.signals.failed.emit(str(exc))

        threading.Thread(target=work, name="stop-vms", daemon=True).start()

    def _on_progress(self, current: int, total: int, message: str) -> None:
        self.progress.setMaximum(total)
        self.progress.setValue(current)
        self.progress.setFormat(message)

    def _on_finished(self, failures: list[tuple[Path, str]]) -> None:
        self._running = False
        self.refresh_button.setEnabled(True)
        if failures:
            detail = "\n".join(f"• {path.name}: {error}" for path, error in failures)
            QMessageBox.warning(self, "Có VM chưa tắt được", detail)
        else:
            QMessageBox.information(self, "Hoàn tất", "Đã gửi lệnh tắt cho tất cả VM.")
        self._refresh()

    def _on_failed(self, message: str) -> None:
        self._running = False
        self.refresh_button.setEnabled(True)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("Không đọc được trạng thái VMware")
        QMessageBox.critical(self, "Lỗi VMware", message)

    def closeEvent(self, event) -> None:
        if self._running:
            QMessageBox.information(self, "Đang chạy", "Hãy chờ các lệnh tắt VM hoàn tất.")
            event.ignore()
            return
        event.accept()


class DeleteVmDialog(QDialog):
    def __init__(self, environment: VMwareEnvironment, parent=None, embedded: bool = False) -> None:
        super().__init__(parent)
        self.environment = environment
        self.embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.settings = QSettings()
        self.signals = ToolSignals()
        self.cancel_event = threading.Event()
        self._loaded_once = False
        self._running = False
        self._delete_total = 0
        self.setWindowTitle("Xóa máy ảo VMware")
        self.resize(760, 620)
        self._build_ui()
        self.signals.items.connect(self._set_vms)
        self.signals.progress.connect(self._on_progress)
        self.signals.finished.connect(self._on_finished)
        self.signals.failed.connect(self._on_failed)
        self.vmrun_edit.textChanged.connect(self._save_settings)
        if not embedded:
            self.activate()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        detected = str(self.environment.vmrun_exe or "")
        saved_vmrun = self.settings.value("delete_vm/vmrun", detected, str).strip()
        self.vmrun_edit = QLineEdit(saved_vmrun or detected)
        browse = QPushButton("Chọn…")
        browse.clicked.connect(self._browse_vmrun)
        form.addRow("vmrun.exe", _path_row(self.vmrun_edit, browse))
        layout.addLayout(form)

        warning = QLabel(
            "Xóa VM là thao tác vĩnh viễn. VMware sẽ xóa cấu hình và các file thuộc VM bằng "
            "lệnh deleteVM. VM đang chạy sẽ không bị xóa. Hãy sao lưu dữ liệu quan trọng trước."
        )
        warning.setObjectName("deleteWarning")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        list_controls = QHBoxLayout()
        self.select_all_check = QCheckBox("Chọn tất cả")
        self.select_all_check.toggled.connect(self._toggle_all)
        list_controls.addWidget(self.select_all_check)
        list_controls.addStretch()
        self.refresh_button = QPushButton("Quét lại danh sách")
        self.refresh_button.clicked.connect(self._refresh)
        list_controls.addWidget(self.refresh_button)
        layout.addLayout(list_controls)

        self.vm_list = QListWidget()
        layout.addWidget(self.vm_list, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setFormat("Chưa quét danh sách VM")
        layout.addWidget(self.progress)
        self.log_box = QPlainTextEdit()
        self.log_box.setObjectName("logBox")
        self.log_box.setReadOnly(True)
        self.log_box.setMaximumHeight(120)
        layout.addWidget(self.log_box)

        buttons = QHBoxLayout()
        self.delete_button = QPushButton("Xóa vĩnh viễn VM đã chọn")
        self.delete_button.setObjectName("danger")
        self.delete_button.setEnabled(False)
        self.delete_button.clicked.connect(self._delete_selected)
        self.cancel_button = QPushButton("Dừng sau VM hiện tại")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        self.close_button = QPushButton("Đóng")
        self.close_button.clicked.connect(self.close)
        self.close_button.setVisible(not self.embedded)
        buttons.addWidget(self.delete_button, 1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    def _browse_vmrun(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Chọn vmrun.exe", self.vmrun_edit.text(),
            "vmrun.exe (vmrun.exe);;EXE (*.exe)",
        )
        if path:
            self.vmrun_edit.setText(path)

    def _save_settings(self, *_args) -> None:
        self.settings.setValue("delete_vm/vmrun", self.vmrun_edit.text().strip())
        self.settings.sync()

    def activate(self) -> None:
        if not self._loaded_once and not self._running:
            self._loaded_once = True
            self._refresh()

    def _refresh(self) -> None:
        if self._running:
            return
        self.refresh_button.setEnabled(False)
        self.delete_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.setFormat("Đang quét VMware Library…")

        def work() -> None:
            try:
                self.signals.items.emit(discover_vms(self.environment))
            except Exception as exc:
                self.signals.failed.emit(str(exc))

        threading.Thread(target=work, name="delete-vm-discovery", daemon=True).start()

    def _set_vms(self, vms) -> None:
        self.vm_list.clear()
        for vm in vms:
            item = QListWidgetItem(f"{vm.name}   —   {vm.vmx_path}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, str(vm.vmx_path))
            self.vm_list.addItem(item)
        self.select_all_check.setChecked(False)
        self.refresh_button.setEnabled(True)
        self.delete_button.setEnabled(bool(vms))
        self.progress.setRange(0, max(1, len(vms)))
        self.progress.setValue(0)
        self.progress.setFormat(f"Đã tìm thấy {len(vms)} VM")

    def _toggle_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for index in range(self.vm_list.count()):
            self.vm_list.item(index).setCheckState(state)

    def _selected_paths(self) -> list[Path]:
        return [
            Path(self.vm_list.item(index).data(Qt.ItemDataRole.UserRole))
            for index in range(self.vm_list.count())
            if self.vm_list.item(index).checkState() == Qt.CheckState.Checked
        ]

    def _delete_selected(self) -> None:
        selected = self._selected_paths()
        if not selected:
            QMessageBox.information(self, "Chưa chọn VM", "Hãy chọn ít nhất một máy ảo cần xóa.")
            return
        preview = "\n".join(f"• {path.stem}" for path in selected[:10])
        if len(selected) > 10:
            preview += f"\n• … và {len(selected) - 10} VM khác"
        answer = QMessageBox.warning(
            self,
            "Xác nhận xóa VM vĩnh viễn",
            f"Các VM sau sẽ bị xóa vĩnh viễn:\n\n{preview}\n\nThao tác này không thể hoàn tác. Tiếp tục?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        confirm, accepted = QInputDialog.getText(
            self,
            "Xác nhận lần cuối",
            "Nhập XOA để xác nhận xóa vĩnh viễn:",
        )
        if not accepted or confirm.strip().upper() != "XOA":
            QMessageBox.information(self, "Đã hủy", "Không VM nào bị xóa.")
            return

        vmrun = Path(self.vmrun_edit.text().strip())
        if not vmrun.is_file():
            QMessageBox.warning(self, "Không tìm thấy vmrun.exe", str(vmrun))
            return
        self._running = True
        self._delete_total = len(selected)
        self.cancel_event.clear()
        self.delete_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self.progress.setRange(0, len(selected))
        self.log_box.appendPlainText(f"Bắt đầu xóa {len(selected)} VM…")

        def work() -> None:
            try:
                failures = delete_vms(
                    vmrun, selected, self.signals.progress.emit, self.cancel_event,
                )
                self.signals.finished.emit(failures)
            except Exception as exc:
                self.signals.failed.emit(str(exc))

        threading.Thread(target=work, name="delete-vms", daemon=True).start()

    def _cancel(self) -> None:
        self.cancel_event.set()
        self.cancel_button.setEnabled(False)
        self.log_box.appendPlainText("Đã yêu cầu dừng sau VM hiện tại…")

    def _on_progress(self, current: int, total: int, message: str) -> None:
        self.progress.setMaximum(total)
        self.progress.setValue(current)
        self.progress.setFormat(f"{current} / {total}")
        self.log_box.appendPlainText(message)

    def _on_finished(self, failures: list[tuple[Path, str]]) -> None:
        self._running = False
        self.cancel_button.setEnabled(False)
        if self.cancel_event.is_set():
            QMessageBox.information(self, "Đã dừng", "Đã dừng trước khi nhận thêm VM để xóa.")
        elif failures:
            details = "\n".join(f"• {path.name}: {error}" for path, error in failures)
            QMessageBox.warning(self, "Có VM chưa xóa được", details)
        else:
            QMessageBox.information(self, "Hoàn tất", "Đã xóa các VM được chọn.")
        self._refresh()

    def _on_failed(self, message: str) -> None:
        self._running = False
        self.refresh_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.progress.setFormat("Tác vụ thất bại")
        self.log_box.appendPlainText(f"LỖI: {message}")
        QMessageBox.critical(self, "Lỗi xóa VM", message)

    def closeEvent(self, event) -> None:
        if self._running:
            QMessageBox.information(self, "Đang xóa VM", "Hãy chờ VM hiện tại hoàn tất trước khi đóng.")
            event.ignore()
            return
        event.accept()


class SysprepDialog(QDialog):
    def __init__(self, parent=None, embedded: bool = False) -> None:
        super().__init__(parent)
        self.embedded = embedded
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        self.setWindowTitle("Windows Sysprep")
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        description = QLabel(
            "Mở công cụ Sysprep có sẵn của Windows để chuẩn bị hệ điều hành trước khi nhân bản. "
            "Hãy tạo snapshot hoặc bản sao lưu trước khi chạy Generalize."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        self.path = str(Path(system_root) / "System32" / "Sysprep" / "Sysprep.exe")
        path_label = QLineEdit(self.path)
        path_label.setReadOnly(True)
        layout.addWidget(path_label)
        buttons = QHBoxLayout()
        open_button = QPushButton("Mở Sysprep")
        open_button.setObjectName("primary")
        open_button.clicked.connect(self._open)
        self.close_button = QPushButton("Đóng")
        self.close_button.clicked.connect(self.close)
        self.close_button.setVisible(not self.embedded)
        buttons.addWidget(open_button, 1)
        buttons.addWidget(self.close_button)
        layout.addLayout(buttons)

    def _open(self) -> None:
        executable = Path(self.path)
        if not executable.is_file():
            QMessageBox.critical(self, "Không tìm thấy Sysprep", str(executable))
            return
        try:
            subprocess.Popen([str(executable)])
            self.accept()
        except OSError as exc:
            QMessageBox.critical(self, "Không mở được Sysprep", str(exc))
