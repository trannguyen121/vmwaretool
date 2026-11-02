# ============================================================
# VMware Workstation - Linked Clone hàng loạt
# VM mẫu: D:\MayAo\Windows ATVGv3 3D Canvas 3333\Windows ATVGv3 1111.vmx
#
# Yêu cầu:
# - VMware Workstation đã cài.
# - VM mẫu đã có snapshot.
# - Tắt VM mẫu trước khi chạy để an toàn.
# - Tất cả card mạng của clone sẽ được đặt sang NAT.
# - Linked clone phụ thuộc snapshot/đĩa gốc: không xóa hoặc di chuyển
#   snapshot và các file VMDK của VM mẫu khi clone còn sử dụng chúng.
# ============================================================

$TemplateVMX = "D:\MayAo\Windows ATVGv3 3D Canvas 3333\Windows ATVGv3 1111.vmx"
$OutputRoot  = "D:\MayAo\clone"

# Tên snapshot PHẢI trùng chính xác với tên trong VMware Snapshot Manager.
# Ảnh có file Snapshot2/3 nhưng tên file chưa chắc là tên snapshot.
$SnapshotName = "New"

$VMCount = 80
$NamePrefix = "Opera"
$CPU = 4
$RAM_MB = 6132
$StartVMs = $false

$ErrorActionPreference = "Stop"

# Tìm vmrun.exe ở các đường dẫn thường dùng.
$VmrunCandidates = @(
    "$env:ProgramFiles\VMware\VMware Workstation\vmrun.exe",
    "${env:ProgramFiles(x86)}\VMware\VMware Workstation\vmrun.exe"
)
$Vmrun = $VmrunCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1

if (!(Test-Path $TemplateVMX)) {
    throw "Không tìm thấy VMX mẫu: $TemplateVMX"
}
if (!$Vmrun) {
    throw "Không tìm thấy vmrun.exe. Hãy cài VMware Workstation hoặc sửa đường dẫn `$Vmrun trong script."
}
if ($CPU -lt 1 -or $RAM_MB -lt 512) {
    throw "CPU/RAM không hợp lệ."
}

# Kiểm tra snapshot có tồn tại trong danh sách VMware trả về.
$snapshotOutput = & $Vmrun listSnapshots $TemplateVMX 2>&1
if ($LASTEXITCODE -ne 0) {
    throw "Không đọc được snapshot. Hãy kiểm tra VMX và VMware Workstation. Chi tiết: $snapshotOutput"
}
$snapshotText = $snapshotOutput -join "`n"
if ($snapshotText -notmatch [regex]::Escape($SnapshotName)) {
    Write-Host "Các snapshot VMware báo về:" -ForegroundColor Yellow
    Write-Host $snapshotText
    throw "Không thấy snapshot '$SnapshotName'. Mở Snapshot Manager, lấy đúng tên rồi sửa `$SnapshotName ở đầu script."
}

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
$script:UsedMacs = @()

function New-VMwareMac {
    do {
        # VMware OUI 00:0C:29, 3 byte cuối được random.
        $b1 = Get-Random -Minimum 0 -Maximum 256
        $b2 = Get-Random -Minimum 0 -Maximum 256
        $b3 = Get-Random -Minimum 0 -Maximum 256
        $mac = "00:0C:29:{0:X2}:{1:X2}:{2:X2}" -f $b1, $b2, $b3
    } while ($script:UsedMacs -contains $mac)

    $script:UsedMacs += $mac
    return $mac
}

for ($i = 1; $i -le $VMCount; $i++) {
    $VMName = "{0}-{1}" -f $NamePrefix, $i
    $VMFolder = Join-Path $OutputRoot $VMName
    $DestVMX = Join-Path $VMFolder "$VMName.vmx"

    if (Test-Path $VMFolder) {
        Write-Warning "Bỏ qua $VMName vì thư mục đã tồn tại: $VMFolder"
        continue
    }

    New-Item -ItemType Directory -Path $VMFolder | Out-Null

    Write-Host "`nĐang tạo linked clone: $VMName" -ForegroundColor Cyan
    # vmrun clone linked tạo differencing disk dựa trên snapshot.
    & $Vmrun clone $TemplateVMX $DestVMX linked "-snapshot=$SnapshotName" "-cloneName=$VMName"
    if ($LASTEXITCODE -ne 0 -or !(Test-Path $DestVMX)) {
        Remove-Item $VMFolder -Recurse -Force -ErrorAction SilentlyContinue
        throw "Clone thất bại ở $VMName. Dừng script để tránh tạo các VM lỗi."
    }

    $vmx = Get-Content -LiteralPath $DestVMX -Raw
    $mac = New-VMwareMac
    $uuid = [guid]::NewGuid().ToString()

    # Hàm thay key trong VMX; nếu chưa có thì thêm mới.
    function Set-VmxValue([string]$Text, [string]$Key, [string]$Value) {
        $pattern = '(?m)^' + [regex]::Escape($Key) + '\s*=.*$'
        $line = "$Key = `"$Value`""
        if ($Text -match $pattern) {
            return [regex]::Replace($Text, $pattern, [System.Text.RegularExpressions.MatchEvaluator]{ param($m) $line })
        }
        return $Text.TrimEnd() + "`r`n$line`r`n"
    }

    $vmx = Set-VmxValue $vmx "displayName" $VMName
    $vmx = Set-VmxValue $vmx "numvcpus" "$CPU"
    $vmx = Set-VmxValue $vmx "memsize" "$RAM_MB"
    $vmx = Set-VmxValue $vmx "uuid.bios" $uuid
    $vmx = Set-VmxValue $vmx "uuid.location" $uuid

    # Đặt MAC tĩnh riêng cho ethernet0.
    $vmx = Set-VmxValue $vmx "ethernet0.addressType" "static"
    $vmx = Set-VmxValue $vmx "ethernet0.address" $mac

    # Ép TẤT CẢ card mạng hiện có sang NAT (không để kế thừa Bridged từ VM mẫu).
    # Lấy chỉ số adapter từ các dòng ethernetN.* trong VMX.
    $adapterIds = [regex]::Matches($vmx, '(?m)^ethernet(\d+)\.') |
        ForEach-Object { [int]$_.Groups[1].Value } |
        Sort-Object -Unique
    if (-not $adapterIds -or $adapterIds.Count -eq 0) {
        $adapterIds = @(0)
    }
    foreach ($adapterId in $adapterIds) {
        $vmx = Set-VmxValue $vmx "ethernet$adapterId.connectionType" "nat"
    }

    Set-Content -LiteralPath $DestVMX -Value $vmx -Encoding ASCII

    Write-Host "Tạo xong: $VMName | MAC: $mac | CPU: $CPU | RAM: $RAM_MB MB"

    if ($StartVMs) {
        & $Vmrun start $DestVMX nogui
        if ($LASTEXITCODE -ne 0) {
            Write-Warning "Đã tạo $VMName nhưng không bật được. Kiểm tra VMware."
        }
    }
}

Write-Host "`nHoàn tất. Thư mục clone: $OutputRoot" -ForegroundColor Green
Write-Host "Linked clone dùng chung snapshot/đĩa nền với VM gốc; đừng xóa hoặc di chuyển các file nền."
