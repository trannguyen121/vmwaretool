# VMware Disk Cleaner

Ứng dụng Windows tối giản viết bằng Python 3.12 + PySide6 để phát hiện VM từ VMware Workstation Library, kiểm tra an toàn và tự động hóa Clean Up Disks của VMware Workstation.

## Phạm vi backend

Workstation Pro 16.1.2 không công bố lệnh `vmrun` tương đương **VM > Manage > Clean Up Disks**. `vmware-vdiskmanager -k` chỉ là shrink VMDK và không xử lý tương đương GUI, đặc biệt với linked clone. Vì vậy Cleanup thật dùng UI Automation để điều khiển chính VMware Workstation theo luồng **VM → Manage → Clean Up Disks → Clean up now**.

Ứng dụng không dùng VMware Tools, không bật/tắt VM, không xóa snapshot/VMDK/lock và không sửa VMX. UI Automation chỉ điều khiển chức năng cleanup có sẵn của VMware.

## Điều kiện an toàn

Mỗi VM được kiểm tra ngay trước tác vụ:

- `vmrun -T ws list` phải xác minh được VM đang Powered Off.
- VMX và VMDK phải tồn tại; VMDK phải có quyền đọc/ghi.
- Dung lượng trống dự phòng phải ít nhất bằng tổng kích thước chain đang cấp phát, tối thiểu 1 GB.
- Hai VM dùng chung VMDK chain không bao giờ chạy đồng thời.
- Cleanup thật luôn giới hạn một worker vì nhiều thao tác GUI đồng thời trên cùng desktop có thể xung đột. Scan và DryRun vẫn có thể dùng nhiều worker.

`Stop` chỉ bỏ các tác vụ chưa được xếp lịch. Cleanup đang chạy trong VMware không bị cưỡng bức dừng.

Sau mỗi VM, ứng dụng chỉ tự bấm `OK` khi nhận diện chính xác thông báo `Disk cleanup is finished`, đóng hộp thoại cleanup còn lại và tiếp tục VM kế tiếp trong hàng đợi.

Luồng tự động hóa không di chuyển hoặc click chuột: chọn VM bằng UIA SelectionItem, mở menu bằng message gửi trực tiếp tới VMware và kích hoạt nút bằng native `BM_CLICK`. Nếu VMware đang ở màn hình vật lý phụ, ứng dụng giữ nó tại đó để popup không lọt về màn hình chính. Nếu VMware ở màn hình chính, cửa sổ được tạm di chuyển ra ngoài vùng hiển thị. Foreground được trả về ứng dụng người dùng đang làm việc và vị trí VMware được khôi phục khi hoàn tất.

Popup menu của Workstation chỉ tồn tại khi VMware có foreground. Ứng dụng giữ đoạn mở menu thật ngắn và tự thử lại tối đa 8 lần nếu thao tác trên màn hình chính vô tình đóng menu; khi hộp thoại Disk Cleanup đã mở, foreground được trả về ngay.

## Cài đặt và chạy

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe app.py
```

Sau khi đã tạo `.venv`, có thể chạy giao diện trực tiếp bằng cách nhấp đúp `run.bat`. Script dùng `pythonw.exe` nên không giữ cửa sổ CMD ở phía sau.

Alias `python.exe` của Microsoft Store có thể không hoạt động; dùng `py -3.12` hoặc Python trong `.venv`.

Giao diện mặc định ở **Scan**. Chọn **DryRun** để kiểm tra luồng dự kiến mà không bấm nút VMware. Chỉ chế độ **Cleanup** mới điều khiển VMware và luôn yêu cầu xác nhận riêng.

## Kiểm tra chỉ đọc

```powershell
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m vmware_cleaner.cli --mode scan
.venv\Scripts\python.exe -m vmware_cleaner.cli --mode dry-run
```

Scan và DryRun chỉ đọc metadata; không bấm nút `Clean up now`.

Log xoay vòng được ghi tại `%LOCALAPPDATA%\VMwareDiskCleaner\logs\vmware-disk-cleaner.log`.

## Đóng gói EXE

```powershell
.venv\Scripts\pyinstaller.exe --clean vmware_disk_cleaner.spec
```

EXE tạo tại `dist\VMwareDiskCleaner.exe`. CLI VMware không được bundle; ứng dụng tự tìm thư mục cài Workstation từ Registry, các thư mục Program Files và `PATH` lúc chạy.

## Cấu trúc

- `environment.py`: phát hiện Workstation/CLI/inventory, không hardcode đường dẫn máy người dùng.
- `discovery.py`: đọc Library, VMX và descriptor VMDK/parent chain.
- `safety.py`: kiểm tra trạng thái, snapshot, linked clone, lock, quyền và dung lượng.
- `cleanup.py`: Scan, DryRun và điều phối backend cleanup.
- `ui_automation.py`: tự động hóa Clean Up Disks của VMware theo chế độ tuần tự.
- `task_manager.py`: hàng đợi đồng thời có khóa chain/volume và Stop mềm.
- `ui.py`: cửa sổ PySide6 duy nhất.
- `logging_config.py`: log xoay vòng.
