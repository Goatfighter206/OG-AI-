@echo off
echo ============================================================
echo    DIAGNOSING OG-AI ISSUES
echo ============================================================
echo.

echo Checking Python...
python --version 2>nul
if errorlevel 1 (
    echo X Python NOT found!
    echo.
    echo INSTALL PYTHON:
    echo 1. Go to: https://www.python.org/downloads/
    echo 2. Download latest Python 3
    echo 3. IMPORTANT: Check "Add Python to PATH" during install
    echo 4. Restart computer after installing
    echo 5. Run this script again
    echo.
    pause
    exit /b 1
) else (
    python --version
    echo + Python is installed
)
echo.

echo Checking Ollama...
ollama --version 2>nul
if errorlevel 1 (
    echo X Ollama NOT found!
    echo.
    echo INSTALL OLLAMA:
    echo 1. Go to: https://ollama.ai
    echo 2. Download Ollama for Windows
    echo 3. Install it
    echo 4. Restart computer after installing
    echo 5. Run this script again
    echo.
    pause
    exit /b 1
) else (
    ollama --version
    echo + Ollama is installed
)
echo.

echo Checking if Ollama is running...
ollama list 2>nul
if errorlevel 1 (
    echo X Ollama service not running
    echo.
    echo FIX: Restart your computer (Ollama starts automatically)
    echo OR manually run: ollama serve
    echo.
) else (
    echo + Ollama service is running
)
echo.

echo Checking Python packages...
python -c "import fastapi" 2>nul
if errorlevel 1 (
    echo X Required packages not installed
    echo Installing now...
    pip install fastapi uvicorn ollama python-dotenv pyttsx3 --quiet
    if errorlevel 1 (
        echo ! Installation had issues but continuing...
    ) else (
        echo + Packages installed
    )
) else (
    echo + Core packages installed
)
echo.

echo ============================================================
echo    DIAGNOSIS COMPLETE!
echo ============================================================
echo.
echo WHAT YOU HAVE:
python --version
ollama --version
echo.
echo NEXT STEPS:
echo 1. If you just installed something, RESTART YOUR COMPUTER
echo 2. Make sure Ollama is running (check system tray)
echo 3. Run: python app.py
echo 4. Open browser: http://localhost:8000
echo.
echo OR try: START_GANGSTER.bat
echo.
pause
