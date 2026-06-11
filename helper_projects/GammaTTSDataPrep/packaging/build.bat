@echo off
cd /d "%~dp0..\..\.."
call .venv\Scripts\pyinstaller helper_projects\GammaTTSDataPrep\packaging\tts_dataset_gui.spec --noconfirm --workpath helper_projects\GammaTTSDataPrep\build
if %ERRORLEVEL% neq 0 (
    echo.
    echo Build failed with exit code %ERRORLEVEL%.
    pause
    exit /b %ERRORLEVEL%
)
echo.
echo Build complete: dist\GammaTTSDataPrep\GammaTTSDataPrep.exe
pause
