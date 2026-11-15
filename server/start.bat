@echo off
rem Windows start script. Creates .venv on first run, (re)installs packages when requirements.txt changes,
rem then starts the server via run.py. Arguments are passed to run.py, e.g.  start.bat --dev
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if exist ".venv\Scripts\python.exe" goto install
set "PY="
for %%V in (3.13 3.12) do if not defined PY py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V"
if not defined PY py -3 -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>nul && set "PY=py -3"
if not defined PY python -c "import sys; sys.exit(sys.version_info < (3, 12))" >nul 2>nul && set "PY=python"
if not defined PY goto nopython
echo Creating .venv with %PY% ...
%PY% -m venv .venv || goto fail

:install
fc /b requirements.txt ".venv\requirements.installed" >nul 2>nul && goto run
echo Installing packages (first run or requirements.txt changed). This usually takes 3-10 minutes ...
".venv\Scripts\python.exe" -m pip install -q --upgrade pip || goto fail
".venv\Scripts\python.exe" -m pip install --progress-bar on -r requirements.txt || goto fail
".venv\Scripts\python.exe" -m pip install -q --no-deps -e . || goto fail
copy /y requirements.txt ".venv\requirements.installed" >nul

:run
".venv\Scripts\python.exe" run.py %*
set "RC=%errorlevel%"
if not "%RC%"=="0" pause
exit /b %RC%

:nopython
echo Python 3.12 or later is required. Install it from python.org and tick "Add python.exe to PATH".
pause
exit /b 1

:fail
echo Installation failed. See the error messages above, then run start.bat again.
echo If it keeps failing, delete the .venv folder and retry.
pause
exit /b 1
