from pathlib import Path

import pytest

from vmware_cleaner.folder_tools import move_folder_same_drive, validate_same_drive_move


def test_move_folder_same_drive(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "data.txt").write_text("demo", encoding="utf-8")
    destination = tmp_path / "renamed"

    result = move_folder_same_drive(source, destination)

    assert result == destination
    assert not source.exists()
    assert (destination / "data.txt").read_text(encoding="utf-8") == "demo"


def test_move_rejects_existing_destination(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.mkdir()
    destination.mkdir()
    with pytest.raises(FileExistsError, match="đã tồn tại"):
        validate_same_drive_move(source, destination)


def test_move_rejects_destination_inside_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    with pytest.raises(ValueError, match="bên trong chính nó"):
        validate_same_drive_move(source, source / "child")
