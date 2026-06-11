@echo off
setlocal
set "REPO_ROOT=%~dp0.."
set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python312\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%REPO_ROOT%\.venv312\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
  echo Expected Python runtime not found.
  pause
  exit /b 1
)
start "" "%PYTHON_EXE%" "%REPO_ROOT%\scripts\open_gamma.py"
