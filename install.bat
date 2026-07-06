@echo off
REM eleon installer — creates a local venv and installs Phase 1 deps.
setlocal

echo [eleon] Creating virtual environment (.venv)...
python -m venv .venv
if errorlevel 1 (
    echo [eleon] Failed to create venv. Is Python on PATH?
    exit /b 1
)

call .venv\Scripts\activate.bat

echo [eleon] Upgrading pip...
python -m pip install --upgrade pip >nul

echo [eleon] Installing requirements...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [eleon] Some packages failed. Core CLI needs only httpx, python-dotenv, psutil.
)

echo.
echo [eleon] Done. Run with:  .venv\Scripts\python run.py
endlocal
