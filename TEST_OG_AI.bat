@echo off
echo.
echo ===============================================
echo   TESTING OG-AI SUPREME AGENT
echo ===============================================
echo.

REM Check Python
echo [1/3] Checking Python installation...
python --version
if errorlevel 1 (
    echo ERROR: Python not found!
    pause
    exit /b 1
)

echo.
echo [2/3] Checking dependencies...
python -c "import fastapi, openai, anthropic, ollama" 2>nul
if errorlevel 1 (
    echo WARNING: Some dependencies missing. Run SETUP_OG_GANGSTER.bat first
    echo.
    echo Do you want to continue anyway? (Y/N)
    set /p continue=
    if /i not "%continue%"=="Y" exit /b 1
)

echo.
echo [3/3] Starting OG-AI SUPREME AGENT...
echo.
python og_supreme_agent.py

pause
