@echo off
echo ============================================================
echo   Google Classroom PDF Exporter - First-time Setup
echo ============================================================
echo.

:: Prefer the Windows Python Launcher (py) over any PATH python
:: This avoids MSYS2 / Conda / other non-standard Python installs
set PYTHON=
where py >nul 2>&1 && set PYTHON=py
if "%PYTHON%"=="" (
    where python >nul 2>&1 && set PYTHON=python
)
if "%PYTHON%"=="" (
    echo [ERROR] Python is not installed.
    echo         Please install it from https://www.python.org/downloads/
    echo         and make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)

echo Using Python: %PYTHON%
%PYTHON% --version
echo.

echo [1/3] Installing Python packages...
%PYTHON% -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install packages.
    pause
    exit /b 1
)

echo.
echo [2/3] Installing Playwright browser...
%PYTHON% -m playwright install chromium
if errorlevel 1 (
    echo [ERROR] Failed to install Playwright browser.
    pause
    exit /b 1
)

echo.
echo [3/3] Setup complete!
echo.
echo You can now run the exporter with:
echo   py export_pdf.py "Google Classroom URL"
echo   run.bat "Google Classroom URL"
echo.
pause
