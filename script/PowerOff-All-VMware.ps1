# ============================================================
# VMware Workstation - Tắt tất cả VM đang chạy
# Mặc định dùng soft shutdown (yêu cầu VMware Tools trong guest).
# Đổi $StopMode = "hard" nếu cần tắt cưỡng bức như rút điện.
# ============================================================

$StopMode = "hard"   # "soft" = shutdown an toàn; "hard" = tắt cưỡng bức
$ConfirmBeforeStop = $true

$ErrorActionPreference = "Stop"

# Tìm vmrun.exe ở các vị trí thường gặp
$VmrunCandidates = @(
    "$env:ProgramFiles\VMware\VMware Workstation\vmrun.exe",
    "${env:ProgramFiles(x86)}\VMware\VMware Workstation\vmrun.exe"
)
$Vmrun = $VmrunCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if (!$Vmrun) {
    Write-Host "Không tìm thấy vmrun.exe." -ForegroundColor Red
    Write-Host "Hãy sửa biến `$Vmrun trong script thành đường dẫn vmrun.exe trên máy của bạn."
    Read-Host "Nhấn Enter để thoát"
    exit 1
}

if ($StopMode -notin @("soft", "hard")) {
    throw '$StopMode phải là "soft" hoặc "hard".'
}

Write-Host "Đang lấy danh sách VM đang chạy..." -ForegroundColor Cyan
$listOutput = & $Vmrun -T ws list 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host ($listOutput -join "`n") -ForegroundColor Red
    throw "Không lấy được danh sách VM đang chạy."
}

$RunningVMs = @(
    $listOutput |
    ForEach-Object { "$_".Trim() } |
    Where-Object { $_ -match '\.vmx$' }
)

if ($RunningVMs.Count -eq 0) {
    Write-Host "Không có VM nào đang chạy." -ForegroundColor Green
    Read-Host "Nhấn Enter để thoát"
    exit 0
}

Write-Host "`nTìm thấy $($RunningVMs.Count) VM đang chạy:" -ForegroundColor Yellow
$RunningVMs | ForEach-Object { Write-Host " - $_" }

if ($StopMode -eq "hard") {
    Write-Host "`nCẢNH BÁO: HARD STOP sẽ tắt cưỡng bức VM; dữ liệu chưa lưu có thể mất." -ForegroundColor Red
} else {
    Write-Host "`nChế độ SOFT: yêu cầu hệ điều hành khách tắt bình thường." -ForegroundColor Cyan
    Write-Host "VM cần VMware Tools đang hoạt động để soft shutdown thành công."
}

if ($ConfirmBeforeStop) {
    $answer = Read-Host "`nGõ y để tắt tất cả VM ở chế độ '$StopMode'"
    if ($answer -cne "y") {
        Write-Host "Đã hủy, không VM nào bị tắt." -ForegroundColor Yellow
        exit 0
    }
}

$failed = @()
foreach ($vmx in $RunningVMs) {
    Write-Host "`nĐang tắt: $vmx" -ForegroundColor Cyan
    & $Vmrun -T ws stop $vmx $StopMode
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Đã gửi lệnh tắt: $vmx" -ForegroundColor Green
    } else {
        Write-Host "Lệnh tắt thất bại: $vmx" -ForegroundColor Red
        $failed += $vmx
    }
}

Write-Host "`n========== KẾT QUẢ ==========" -ForegroundColor Cyan
if ($failed.Count -eq 0) {
    Write-Host "Đã gửi lệnh tắt cho tất cả VM." -ForegroundColor Green
} else {
    Write-Host "$($failed.Count) VM chưa tắt được:" -ForegroundColor Yellow
    $failed | ForEach-Object { Write-Host " - $_" }
    Write-Host "Kiểm tra VMware Tools hoặc thử lại. Chỉ chuyển sang hard nếu chấp nhận nguy cơ mất dữ liệu."
}
Write-Host "`nLưu ý: script xử lý các VM mà vmrun của VMware Workstation hiện tại liệt kê."
