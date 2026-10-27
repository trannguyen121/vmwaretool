@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "app.py"
    exit /b 0
)

where pyw.exe >nul 2>nul
if %errorlevel% equ 0 (
    start "" pyw.exe -3.12 "app.py"
    exit /b 0
)

echo Khong tim thay Python 3.12 hoac .venv.
echo Chay: py -3.12 -m venv .venv
echo Sau do: .venv\Scripts\python.exe -m pip install -r requirements.txt
pause
exit /b 1

