@echo off
cd /d "%~dp0"
if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo Created .env: only needed for authorized SHOONYA LIVE mode.
)
where py >nul 2>nul
if %errorlevel%==0 (set "PYTHON=py -3") else (set "PYTHON=python")
if not exist ".venv\Scripts\python.exe" (
  echo Creating local Python virtual environment...
  %PYTHON% -m venv .venv
  if errorlevel 1 (echo Python 3.11+ required. & pause & exit /b 1)
)
".venv\Scripts\python.exe" -m pip --version >nul 2>nul
if errorlevel 1 (
  echo pip is missing or damaged. Attempting ensurepip repair...
  ".venv\Scripts\python.exe" -m ensurepip --upgrade
)
".venv\Scripts\python.exe" -m pip --version >nul 2>nul
if errorlevel 1 (
  echo pip is still damaged. Recreating only the local .venv folder...
  rmdir /s /q ".venv"
  %PYTHON% -m venv .venv
  if errorlevel 1 (echo Could not recreate .venv. Close other Python processes and try again. & pause & exit /b 1)
)
echo Installing/checking dependencies...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
if errorlevel 1 (echo Dependency installation failed. Check internet access and Python version. & pause & exit /b 1)
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://127.0.0.1:8000"
echo Running SignalDesk V5 on http://127.0.0.1:8000
".venv\Scripts\python.exe" -m uvicorn app.server:app --host 127.0.0.1 --port 8000
pause
