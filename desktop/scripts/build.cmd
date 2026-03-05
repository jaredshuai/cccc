@echo off
REM Unified desktop build wrapper (Windows).
REM Delegates to desktop/scripts/build.py to keep a single source of truth.
REM
REM Example:
REM   build.cmd --stage all --platform windows --channel stable --version auto
REM   build.cmd --stage backend --force backend
REM   build.cmd --clean --stage all

setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "BUILD_PY=%SCRIPT_DIR%build.py"

if not exist "%BUILD_PY%" (
  echo [ERROR] Missing orchestrator script: "%BUILD_PY%"
  exit /b 1
)

set "PY_CMD="
where python >nul 2>&1
if %ERRORLEVEL% EQU 0 set "PY_CMD=python"

if not defined PY_CMD (
  where py >nul 2>&1
  if %ERRORLEVEL% EQU 0 set "PY_CMD=py -3"
)

if not defined PY_CMD (
  echo [ERROR] Python not found in PATH. Install Python 3 first.
  exit /b 1
)

%PY_CMD% "%BUILD_PY%" %*
exit /b %ERRORLEVEL%
