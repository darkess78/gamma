@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM Use the folder passed in as an argument, or use the current folder
set "ROOT=%~1"
if "%ROOT%"=="" set "ROOT=%CD%"

if not exist "%ROOT%\" (
    echo Folder not found: "%ROOT%"
    exit /b 1
)

echo Scanning: "%ROOT%"
echo.

for /R "%ROOT%" %%F in (*.png) do (
    set "OLDNAME=%%~nxF"
    set "NEWNAME=!OLDNAME:-Photoroom=!"

    if not "!OLDNAME!"=="!NEWNAME!" (
        if exist "%%~dpF!NEWNAME!" (
            echo SKIPPED: "%%~fF"
            echo Target already exists: "%%~dpF!NEWNAME!"
            echo.
        ) else (
            ren "%%~fF" "!NEWNAME!"
            echo RENAMED: "%%~fF"
            echo      TO: "%%~dpF!NEWNAME!"
            echo.
        )
    )
)

echo Done.
pause