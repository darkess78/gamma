@echo off
cd /d "%~dp0.."
call .venv\Scripts\pyinstaller packaging\tts_dataset_gui.spec --noconfirm
if %ERRORLEVEL% neq 0 (
    echo.
    echo Build failed with exit code %ERRORLEVEL%.
    pause
    exit /b %ERRORLEVEL%
)
echo.
echo Build complete: dist\GammaTTSDataPrep\GammaTTSDataPrep.exe
pause
