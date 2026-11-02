from __future__ import annotations

import logging
import subprocess
import time

from .models import CleanupResult, CleanupStatus, VirtualMachine, VMwareEnvironment

LOG = logging.getLogger(__name__)
# VMware 16 exposes native/UIA state quickly. Short polling keeps the menu and
# confirmation sequence responsive without shortening disk-write timeouts.
FAST_POLL = 0.02
COMPLETION_POLL = 0.05
RETRY_DELAY = 0.05


class UIAutomationError(RuntimeError):
    pass


class VMwareUIAutomation:
    """Serial automation of Workstation's own VM > Manage > Clean Up Disks flow."""

    def __init__(self, environment: VMwareEnvironment) -> None:
        self.environment = environment

    def cleanup(self, vm: VirtualMachine) -> CleanupResult:
        before = self._disk_bytes(vm)
        try:
            reclaimed = self._run_cleanup(vm)
            after = self._disk_bytes(vm)
            message = "VMware Clean Up Disks hoàn tất"
            if reclaimed:
                message += f"; reclaimable trước khi chạy: {reclaimed}"
            return CleanupResult(
                vm, CleanupStatus.DONE, message,
                before_bytes=before, after_bytes=after,
                reclaimed_bytes=max(0, before - after),
            )
        except UIAutomationError as exc:
            LOG.error("UI Automation failed for %s: %s", vm.name, exc)
            after = self._disk_bytes(vm)
            return CleanupResult(
                vm, CleanupStatus.FAILED, str(exc),
                before_bytes=before, after_bytes=after,
                reclaimed_bytes=max(0, before - after),
            )
        except Exception as exc:
            LOG.exception("Unexpected UI Automation error for %s", vm.name)
            after = self._disk_bytes(vm)
            return CleanupResult(
                vm, CleanupStatus.FAILED, f"UI Automation lỗi: {exc}",
                before_bytes=before, after_bytes=after,
                reclaimed_bytes=max(0, before - after),
            )

    @staticmethod
    def _disk_bytes(vm: VirtualMachine) -> int:
        total = 0
        for path in vm.chain_files:
            try:
                total += path.stat().st_size
            except OSError:
                pass
        return total

    def _run_cleanup(self, vm: VirtualMachine) -> str:
        try:
            import pythoncom
            from pywinauto import Desktop, timings
        except ImportError as exc:
            raise UIAutomationError("Thiếu pywinauto/pywin32; hãy cài lại requirements.txt") from exc

        pythoncom.CoInitialize()
        window_state = None
        try:
            timings.Timings.window_find_retry = FAST_POLL
            timings.Timings.exists_retry = FAST_POLL
            desktop = Desktop(backend="uia")
            main = self._find_or_open_workstation_window(desktop, vm)
            window_state = self._show_workstation_for_cleanup(main)

            dialog = self._open_cleanup_dialog_with_retry(
                desktop, main, vm.name, window_state[1]
            )
            reclaimable = self._reclaimable_text(dialog)
            button = dialog.child_window(title="Clean up now", control_type="Button")
            if not button.exists(timeout=5):
                raise UIAutomationError("Nút Clean up now không khả dụng")
            ready_deadline = time.monotonic() + 60
            while time.monotonic() < ready_deadline and not button.is_enabled():
                time.sleep(FAST_POLL)
            if not button.is_enabled():
                try:
                    import win32con
                    import win32gui

                    win32gui.PostMessage(dialog.handle, win32con.WM_CLOSE, 0, 0)
                except Exception:
                    pass
                raise UIAutomationError(
                    "Nút Clean up now vẫn bị vô hiệu hóa sau 60 giây; "
                    "VMware có thể không tìm thấy dung lượng cần thu hồi"
                )
            reclaimable = self._reclaimable_text(dialog) or reclaimable

            self._click_native_button(button, "Clean up now")
            LOG.info("Clicked VMware Clean up now for %s", vm.name)
            self._wait_for_completion(
                main, dialog, button, reclaimable, window_state[1]
            )
            return reclaimable
        finally:
            if window_state is not None:
                self._restore_workstation_window(main, window_state)
            pythoncom.CoUninitialize()

    @staticmethod
    def _find_workstation_window(desktop, vm_name: str):
        candidates = [w for w in desktop.windows() if w.class_name() == "VMUIFrame"]
        for wrapper in candidates:
            window = desktop.window(handle=wrapper.handle)
            if window.child_window(title=vm_name, control_type="TreeItem").exists(timeout=0.2):
                return window
        if not candidates:
            raise UIAutomationError("Hãy mở VMware Workstation trước khi Cleanup")
        raise UIAutomationError(f"VM {vm_name} không có trong cửa sổ VMware Workstation")

    def _find_or_open_workstation_window(self, desktop, vm: VirtualMachine):
        try:
            return self._find_workstation_window(desktop, vm.name)
        except UIAutomationError:
            if not self.environment.vmware_exe:
                raise UIAutomationError("Không tìm thấy vmware.exe để mở VMware Workstation")
        try:
            subprocess.Popen([str(self.environment.vmware_exe), str(vm.vmx_path)])
        except OSError as exc:
            raise UIAutomationError(f"Không mở được VMware Workstation: {exc}") from exc
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            time.sleep(0.25)
            try:
                return self._find_workstation_window(desktop, vm.name)
            except UIAutomationError:
                continue
        raise UIAutomationError(
            f"Đã mở VMware nhưng không tìm thấy tab/Library item của {vm.name} sau 30 giây"
        )

    @staticmethod
    def _open_vm_menu_without_input(main) -> None:
        """Open VMware's VM menu with targeted messages, without mouse/keyboard input."""
        try:
            import win32api
            import win32con
            import win32gui

            win32gui.PostMessage(main.handle, win32con.WM_CANCELMODE, 0, 0)
            virtual_key = ord("M")
            scan_code = win32api.MapVirtualKey(virtual_key, 0)
            key_down = 1 | (scan_code << 16) | (1 << 29)
            key_up = key_down | (1 << 30) | (1 << 31)
            win32gui.PostMessage(
                main.handle, win32con.WM_SYSKEYDOWN, virtual_key, key_down
            )
            win32gui.PostMessage(
                main.handle, win32con.WM_SYSKEYUP, virtual_key, key_up
            )
        except Exception as exc:
            raise UIAutomationError(f"Không mở được menu VM: {exc}") from exc

    def _open_cleanup_dialog_with_retry(
        self, desktop, main, vm_name: str, foreground_handle: int | None
    ):
        """Open Disk Cleanup, recovering when user input dismisses transient menus."""
        last_error: Exception | None = None
        for attempt in range(1, 9):
            try:
                import win32con
                import win32gui

                win32gui.PostMessage(main.handle, win32con.WM_CANCELMODE, 0, 0)
                tree_item = main.child_window(title=vm_name, control_type="TreeItem")
                if not tree_item.exists(timeout=1):
                    raise UIAutomationError(f"Không tìm thấy {vm_name} trong VMware Library")
                tree_item.wrapper_object().select()

                # VMware only creates its native popup while it is foreground.
                # Keep this critical section short; if a user click dismisses it,
                # retry from a fresh UIA element instead of failing the VM.
                try:
                    win32gui.SetForegroundWindow(main.handle)
                except Exception:
                    pass
                self._open_vm_menu_without_input(main)
                manage_item = self._find_popup_item(desktop, "Manage", timeout=1)
                manage_item.wrapper_object().invoke()
                manage_menu = desktop.window(title="Manage", control_type="Menu")
                if not manage_menu.exists(timeout=1):
                    raise UIAutomationError("Submenu Manage đã bị đóng")
                clean_item = manage_menu.child_window(
                    title_re=r"Clean Up Disks.*", control_type="MenuItem"
                )
                if not clean_item.exists(timeout=1) or not clean_item.is_enabled():
                    raise UIAutomationError("Clean Up Disks không khả dụng")
                clean_item.wrapper_object().invoke()

                dialog = main.child_window(title="Disk Cleanup", control_type="Window")
                if dialog.exists(timeout=3):
                    LOG.info("Opened Disk Cleanup for %s on attempt %d", vm_name, attempt)
                    return dialog
                raise UIAutomationError("Hộp thoại Disk Cleanup chưa xuất hiện")
            except Exception as exc:
                last_error = exc
                LOG.warning(
                    "Disk Cleanup navigation attempt %d/8 failed for %s: %s",
                    attempt, vm_name, exc,
                )
                time.sleep(RETRY_DELAY)
        raise UIAutomationError(
            "Không mở được Disk Cleanup sau 8 lần thử; "
            f"thao tác người dùng có thể đang đóng menu VMware ({last_error})"
        )

    @staticmethod
    def _show_workstation_for_cleanup(main):
        """Restore and foreground VMware so the normal cleanup flow stays visible."""
        try:
            import win32con
            import win32gui

            handle = main.handle
            placement = win32gui.GetWindowPlacement(handle)
            foreground = win32gui.GetForegroundWindow()
            if placement[1] == win32con.SW_SHOWMINIMIZED:
                win32gui.ShowWindow(handle, win32con.SW_RESTORE)
            else:
                win32gui.ShowWindow(handle, win32con.SW_SHOW)
            try:
                win32gui.SetForegroundWindow(handle)
            except Exception:
                pass
            LOG.info("Showing VMware Workstation for visible Clean Up Disks automation")
            return placement, foreground
        except Exception as exc:
            raise UIAutomationError(f"Không thể hiển thị VMware Workstation: {exc}") from exc

    # Compatibility alias retained for code that referenced the previous name.
    _move_workstation_offscreen = _show_workstation_for_cleanup

    @staticmethod
    def _restore_foreground(handle: int | None) -> None:
        if not handle:
            return
        try:
            import win32gui

            if win32gui.IsWindow(handle) and win32gui.GetForegroundWindow() != handle:
                win32gui.SetForegroundWindow(handle)
        except Exception:
            LOG.debug("Could not restore foreground window", exc_info=True)

    @staticmethod
    def _restore_workstation_window(main, state) -> None:
        placement, foreground = state
        try:
            import win32gui

            win32gui.SetWindowPlacement(main.handle, placement)
        except Exception:
            LOG.warning("Could not restore VMware window placement", exc_info=True)
        VMwareUIAutomation._restore_foreground(foreground)

    @staticmethod
    def _find_popup_item(desktop, title: str, timeout: float):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for wrapper in desktop.windows():
                if wrapper.element_info.control_type != "Menu":
                    continue
                window = desktop.window(handle=wrapper.handle)
                item = window.child_window(title=title, control_type="MenuItem")
                if item.exists(timeout=0):
                    return item
            time.sleep(FAST_POLL)
        raise UIAutomationError(f"Không tìm thấy menu {title}")

    @staticmethod
    def _reclaimable_text(dialog) -> str:
        for control in dialog.descendants():
            text = control.window_text().strip()
            if "Reclaimable space" in text:
                return text.split(":", 1)[-1].strip()
        return ""

    @staticmethod
    def _click_native_button(button, expected_title: str) -> None:
        """Use a verified native button message; fall back to physical UIA input."""
        try:
            import win32con
            import win32gui

            wrapper = button.wrapper_object()
            handle = wrapper.handle
            if (
                handle
                and win32gui.GetClassName(handle) == "Button"
                and win32gui.GetWindowText(handle).strip() == expected_title
                and win32gui.IsWindowEnabled(handle)
            ):
                win32gui.SendMessage(handle, win32con.BM_CLICK, 0, 0)
                return
        except Exception:
            LOG.debug("Native button click failed; using UIA input", exc_info=True)
        try:
            button.wrapper_object().invoke()
        except Exception as exc:
            raise UIAutomationError(
                f"Không thể kích hoạt nút {expected_title} mà không dùng chuột: {exc}"
            ) from exc

    def _wait_for_completion(
        self, main, dialog, button, initial_text: str, foreground_handle: int | None
    ) -> None:
        deadline = time.monotonic() + 24 * 60 * 60
        saw_busy = False
        unchanged_enabled_since = time.monotonic()
        while time.monotonic() < deadline:
            time.sleep(COMPLETION_POLL)
            if self._acknowledge_finished_dialog(main):
                self._close_finished_cleanup_dialog(dialog)
                return
            if not dialog.exists(timeout=0.1):
                return
            try:
                enabled = button.exists(timeout=0.1) and button.is_enabled()
            except Exception:
                enabled = False
            if not enabled:
                saw_busy = True
                continue
            current_text = self._reclaimable_text(dialog)
            if saw_busy or (current_text and current_text != initial_text):
                close = dialog.child_window(title="Close", control_type="Button", found_index=0)
                if close.exists(timeout=1):
                    close.wrapper_object().invoke()
                return
            if time.monotonic() - unchanged_enabled_since > 10:
                raise UIAutomationError("VMware không bắt đầu cleanup sau khi bấm Clean up now")
        raise UIAutomationError("Cleanup vượt quá thời gian chờ 24 giờ")

    @staticmethod
    def _acknowledge_finished_dialog(main) -> bool:
        """Click OK only on VMware's explicit disk-cleanup completion message."""
        try:
            windows = main.descendants(control_type="Window")
        except Exception:
            return False
        for window in windows:
            # Workstation 16 is a 32-bit native application. Its completion text is
            # exposed as VMwareStaticLink and UIA click_input() does not actually
            # activate the native OK button. Verify the exact native message, then
            # send BM_CLICK to that dialog's own OK button.
            try:
                import win32con
                import win32gui

                if window.window_text().strip() == "VMware Workstation" and window.handle:
                    children: list[int] = []
                    win32gui.EnumChildWindows(
                        window.handle, lambda handle, _: children.append(handle), None
                    )
                    finished = any(
                        win32gui.GetClassName(handle) == "VMwareStaticLink"
                        and win32gui.GetWindowText(handle).strip() == "Disk cleanup is finished."
                        for handle in children
                    )
                    ok_handle = next(
                        (
                            handle for handle in children
                            if win32gui.GetClassName(handle) == "Button"
                            and win32gui.GetWindowText(handle).strip() == "OK"
                            and win32gui.IsWindowEnabled(handle)
                        ),
                        None,
                    )
                    if finished and ok_handle:
                        win32gui.SendMessage(ok_handle, win32con.BM_CLICK, 0, 0)
                        LOG.info("Acknowledged native VMware disk-cleanup completion dialog")
                        return True
            except Exception:
                LOG.debug("Native completion-dialog detection failed", exc_info=True)

            # Fallback for Workstation versions that expose the message through UIA.
            try:
                texts = [item.window_text().strip() for item in window.descendants()]
                if not any("Disk cleanup is finished" in text for text in texts):
                    continue
                ok_buttons = [
                    item for item in window.descendants(control_type="Button")
                    if item.window_text().strip() == "OK" and item.is_enabled()
                ]
                if not ok_buttons:
                    continue
                ok_buttons[0].invoke()
                LOG.info("Acknowledged VMware disk-cleanup completion dialog")
                return True
            except Exception:
                continue
        return False

    @staticmethod
    def _close_finished_cleanup_dialog(dialog) -> None:
        """Close the underlying Disk Cleanup window after acknowledging completion."""
        deadline = time.monotonic() + 0.12
        while time.monotonic() < deadline:
            if not dialog.exists(timeout=0.1):
                return
            time.sleep(FAST_POLL)
        try:
            wrapper = dialog.wrapper_object()
            buttons = [
                item for item in wrapper.descendants(control_type="Button")
                if item.window_text().strip() in {"Close", "Cancel"} and item.is_enabled()
            ]
            if buttons:
                buttons[0].invoke()
                close_deadline = time.monotonic() + 1
                while time.monotonic() < close_deadline and dialog.exists(timeout=0.1):
                    time.sleep(FAST_POLL)
        except Exception:
            LOG.warning("Could not close the finished Disk Cleanup window")
