# VMware Toolkit

Ứng dụng Windows viết bằng Python 3.12 + PySide6 để quản lý và bảo trì VMware Workstation từ một giao diện thống nhất.

## Công cụ trong giao diện

Ứng dụng luôn mở ở **Trang chính**, dùng một bảng VM duy nhất để quét VMware Library, hiển thị tên, đường dẫn VMX, dung lượng và trạng thái xử lý. Nhóm nút màu đậm **Dọn ổ đĩa**, **Tắt VM** và **Xóa VM** chỉ hoạt động khi đã chọn VM trên bảng. **Linked Clone** và **Di chuyển Folder** nằm trong nhóm chuyển trang riêng và tạm ẩn bảng VM.

Bảng hỗ trợ bôi đen cả dòng, click-kéo để chọn nhiều dòng, dùng `Ctrl`/`Shift` để mở rộng vùng chọn và nhấn `Space` để bật hoặc bỏ checkbox cho tất cả dòng đang được bôi đen.

Nút biểu tượng mặt trời/mặt trăng cạnh **Lưu setting** chuyển tức thì giữa giao diện sáng và tối; lựa chọn được lưu cho lần mở tiếp theo.

- **Disk Cleaner:** Scan, DryRun và tự động hóa đúng chức năng **VM > Manage > Clean Up Disks**.
- **Tạo Linked Clone:** chọn VMX mẫu, đọc snapshot, đặt thư mục đầu ra, số lượng, tiền tố tên, CPU, RAM và tùy chọn tự khởi động. Tất cả card mạng được chuyển sang NAT và nhận MAC static riêng hợp lệ với VMware.
- **Di chuyển Folder:** đổi tên hoặc chuyển địa chỉ một folder trong cùng ổ đĩa; không ghi đè, không gộp dữ liệu và không cho di chuyển folder vào bên trong chính nó.
- **Tắt tất cả VM:** xem danh sách VM đang chạy và chọn `soft` hoặc `hard`. Chế độ hard luôn yêu cầu xác nhận vì có thể làm mất dữ liệu.
- **Xóa toàn bộ:** xóa file VM bằng lệnh chính thức `vmrun deleteVM`, sau đó dọn entry còn sót khỏi VMware Library; yêu cầu xác nhận hai bước.
- **Xóa hiển thị:** chỉ gỡ VM khỏi VMware Library, giữ nguyên VMX/VMDK và dữ liệu. Tool sao lưu `inventory.vmls` trước khi sửa. VMware Workstation phải được đóng hoàn toàn cho cả hai chế độ xóa.
- **Windows Sysprep:** mở giao diện Sysprep có sẵn của Windows để chuẩn bị hệ điều hành trước khi nhân bản.

Các thiết lập đường dẫn và cấu hình clone được lưu lại giữa các lần mở ứng dụng. Tác vụ clone và tắt VM chạy ở background để giao diện không bị treo.

## Phạm vi backend

Workstation Pro 16.1.2 không có lệnh `vmrun`, VIX API hoặc CLI chính thức gọi trực tiếp **VM > Manage > Clean Up Disks**. `vmware-vdiskmanager -k` và `VixDiskLib_Shrink()` chỉ shrink/compact block zero, không phải entry point của workflow GUI. Vì vậy backend Cleanup mặc định dùng UI Automation để gọi chính chức năng của Workstation.

Ứng dụng không dùng VMware Tools, không bật/tắt VM, không xóa snapshot/VMDK/lock và không sửa VMX. Backend compact `-k` được giữ riêng trong `compact.py`, nhưng không được gọi bởi nút Cleanup.

## Điều kiện an toàn

Mỗi VM được kiểm tra ngay trước tác vụ:

- `vmrun -T ws list` phải xác minh được VM đang Powered Off.
- VMX và VMDK phải tồn tại; VMDK phải có quyền đọc/ghi.
- Dung lượng trống tối thiểu 1 GB.
- Cleanup qua GUI luôn giới hạn một worker vì nhiều thao tác GUI song song trên cùng desktop sẽ xung đột. Scan và DryRun vẫn cho phép chọn 1–4 worker.

`Stop` chỉ bỏ các tác vụ chưa được xếp lịch. Cleanup đang chạy trong VMware không bị cưỡng bức dừng.

Ứng dụng đo tổng số byte của descriptor, extent và parent-chain liên quan trước/sau khi VMware báo hoàn tất, rồi hiển thị dung lượng thực tế đã thu hồi.

## Cài đặt và chạy

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe app.py
```

Sau khi đã tạo `.venv`, có thể chạy giao diện trực tiếp bằng cách nhấp đúp `run.bat`. Script dùng `pythonw.exe` nên không giữ cửa sổ CMD ở phía sau.

Alias `python.exe` của Microsoft Store có thể không hoạt động; dùng `py -3.12` hoặc Python trong `.venv`.

Giao diện mặc định ở **Scan**. Chọn **DryRun** để kiểm tra workflow dự kiến mà không mở Disk Cleanup. Chỉ chế độ **Cleanup** mới điều khiển VMware và luôn yêu cầu xác nhận riêng.

## Kiểm tra chỉ đọc

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m vmware_cleaner.cli --mode scan
.venv\Scripts\python.exe -m vmware_cleaner.cli --mode dry-run
```

Scan và DryRun chỉ đọc metadata; không bấm `Clean up now`.

Log xoay vòng được ghi tại `%LOCALAPPDATA%\VMwareDiskCleaner\logs\vmware-disk-cleaner.log`.

## Đóng gói EXE

```powershell
.venv\Scripts\pyinstaller.exe --clean vmware_disk_cleaner.spec
```

EXE one-file tạo tại `dist\VMwareToolkit.exe`. VMware/CLI không được bundle; ứng dụng tự tìm thư mục cài Workstation từ Registry, các thư mục Program Files và `PATH` lúc chạy.

## Cấu trúc

- `environment.py`: phát hiện Workstation/CLI/inventory, không hardcode đường dẫn máy người dùng.
- `discovery.py`: đọc Library, VMX và descriptor VMDK/parent chain.
- `safety.py`: kiểm tra trạng thái, snapshot, linked clone, lock, quyền và dung lượng.
- `cleanup.py`: Scan, DryRun và điều phối VMware Clean Up Disks.
- `ui_automation.py`: hiển thị VMware Workstation và tự động gọi tuần tự workflow Clean Up Disks; tự mở tab VM nếu cần.
- `compact.py`: backend `vmware-vdiskmanager -k` dự phòng, không nối với nút Cleanup.
- `task_manager.py`: hàng đợi đồng thời có khóa chain/volume và Stop mềm.
- `vm_tools.py`: backend tạo linked clone, đọc snapshot, liệt kê và tắt VM bằng `vmrun`.
- `tool_dialogs.py`: giao diện cấu hình Linked Clone, Power Off và Sysprep.
- `ui.py`: cửa sổ PySide6 duy nhất.
- `logging_config.py`: log xoay vòng.
