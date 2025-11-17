@echo off
echo ============================================================
echo    OG-AI GANGSTER AGENT - Let's get this shit running!
echo ============================================================
echo.

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo X Python is not installed or not in PATH!
    echo    Download from: https://python.org
    pause
    exit /b 1
)

echo + Python found
echo.

REM Step 1: Quick package check and install
echo ============================================================
echo    STEP 1: Installing required packages...
echo ============================================================
echo.

pip install ollama python-dotenv fastapi uvicorn --quiet
if errorlevel 1 (
    echo WARNING: Some packages might have issues, but continuing...
)

echo + Core packages installed
echo.

REM Step 2: Check if Ollama is installed
echo ============================================================
echo    STEP 2: Checking Ollama...
echo ============================================================
echo.

ollama --version >nul 2>&1
if errorlevel 1 (
    echo X Ollama is NOT installed!
    echo.
    echo    YOU NEED TO:
    echo    1. Download Ollama from: https://ollama.ai
    echo    2. Install it
    echo    3. Run: ollama pull llama3.2
    echo    4. Then run this script again
    echo.
    pause
    exit /b 1
)

echo + Ollama is installed
echo.

REM Step 3: Check if llama3.2 model is pulled
echo ============================================================
echo    STEP 3: Checking llama3.2 model...
echo ============================================================
echo.

ollama list | findstr "llama3.2" >nul 2>&1
if errorlevel 1 (
    echo WARNING: llama3.2 model not found. Downloading now...
    echo    This might take a few minutes (about 2GB download)
    echo.
    ollama pull llama3.2
    if errorlevel 1 (
        echo X Failed to download llama3.2
        echo    Try manually: ollama pull llama3.2
        pause
        exit /b 1
    )
)

echo + llama3.2 model is ready
echo.

REM Step 4: Quick test with Python
echo ============================================================
echo    STEP 4: Testing OG-AI with Ollama...
echo ============================================================
echo.

python test_ollama.py
if errorlevel 1 (
    echo.
    echo X OG-AI test failed! Check the errors above.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo    STEP 5: Starting OG-AI Server...
echo ============================================================
echo.
echo FUCK YEAH! Everything is working!
echo    Gangster mode: ENABLED
echo    Swearing: ENABLED  
echo    AI Backend: Ollama (FREE local AI)
echo.
echo Starting server on http://localhost:8000
echo    Press Ctrl+C to stop the server
echo.
echo ============================================================
echo.

REM Start the server
python app.py

pause
