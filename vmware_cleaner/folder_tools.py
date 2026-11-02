from __future__ import annotations

import os
from pathlib import Path


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def validate_same_drive_move(source: Path, destination: Path) -> tuple[Path, Path]:
    source = _absolute(source)
    destination = _absolute(destination)
    if not source.is_dir():
        raise ValueError(f"Không tìm thấy thư mục nguồn: {source}")
    if source == destination:
        raise ValueError("Địa chỉ mới phải khác thư mục nguồn.")
    if destination.exists():
        raise FileExistsError(f"Địa chỉ đích đã tồn tại: {destination}")
    if not destination.parent.is_dir():
        raise ValueError(f"Thư mục cha của địa chỉ mới không tồn tại: {destination.parent}")
    try:
        destination.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("Không thể di chuyển một thư mục vào bên trong chính nó.")

    source_drive = source.drive.casefold()
    destination_drive = destination.drive.casefold()
    if source_drive or destination_drive:
        if source_drive != destination_drive:
            raise ValueError("Nguồn và địa chỉ mới phải nằm trên cùng một ổ đĩa.")
    elif source.stat().st_dev != destination.parent.stat().st_dev:
        raise ValueError("Nguồn và địa chỉ mới phải nằm trên cùng một volume.")
    return source, destination


def move_folder_same_drive(source: Path, destination: Path) -> Path:
    source, destination = validate_same_drive_move(source, destination)
    try:
        source.rename(destination)
    except OSError as exc:
        raise RuntimeError(f"Không thể di chuyển thư mục: {exc}") from exc
    return destination
