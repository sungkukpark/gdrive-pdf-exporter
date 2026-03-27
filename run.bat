@echo off
setlocal enabledelayedexpansion
echo ============================================================
echo   Google Classroom PDF Exporter
echo ============================================================
echo.

:: Prefer the Windows Python Launcher (py) over any PATH python
set PYTHON=
where py >nul 2>&1 && set PYTHON=py
if "%PYTHON%"=="" (
    where python >nul 2>&1 && set PYTHON=python
)
if "%PYTHON%"=="" (
    echo [ERROR] Python not found. Please run setup.bat first.
    pause
    exit /b 1
)

if "%~1"=="" (
    echo Usage: run.bat "Google Classroom URL" [output folder]
    echo.
    echo Examples:
    echo   run.bat "https://classroom.google.com/u/1/c/Njg3.../m/Njg3.../details"
    echo   run.bat "https://classroom.google.com/..." "D:\MyPDFs"
    echo.
    set /p URL="Enter Google Classroom URL: "
    if "!URL!"=="" (
        echo No URL entered. Exiting.
        pause
        exit /b 1
    )
    %PYTHON% export_pdf.py "!URL!"
) else if "%~2"=="" (
    %PYTHON% export_pdf.py %1
) else (
    %PYTHON% export_pdf.py %1 --output %2
)

echo.
if errorlevel 1 (
    echo [ERROR] Something went wrong. Check the output above for details.
) else (
    echo Done!
)
pause
