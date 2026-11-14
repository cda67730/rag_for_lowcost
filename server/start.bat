@echo off
rem Windows start script. First run creates .venv and installs packages; then starts the server via run.py.
rem Arguments are passed to run.py, e.g.  start.bat --dev
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto run
set "PY="
where py >nul 2>nul && py -3 -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python 3.12 or later is required. Install it from python.org and tick "Add python.exe to PATH".
  pause
  exit /b 1
)
echo First run: creating .venv and installing packages...
%PY% -m venv .venv || goto fail
".venv\Scripts\python.exe" -m pip install -q --upgrade pip || goto fail
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt || goto fail
".venv\Scripts\python.exe" -m pip install -q --no-deps -e . || goto fail

:run
".venv\Scripts\python.exe" run.py %*
set "RC=%errorlevel%"
if not "%RC%"=="0" pause
exit /b %RC%

:fail
echo Installation failed. See the error messages above.
pause
exit /b 1
