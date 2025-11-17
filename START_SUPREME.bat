@echo off
echo.
echo ========================================
echo   STARTING OG-AI SUPREME AGENT
echo ========================================
echo.
echo 🔥 The most intelligent AI agent...
echo.

REM Check if Ollama is running
echo Checking Ollama status...
curl -s http://localhost:11434 >nul 2>&1
if errorlevel 1 (
    echo.
    echo ⚠️  WARNING: Ollama might not be running!
    echo.
    echo To use local AI:
    echo 1. Install Ollama: https://ollama.com
    echo 2. Run: ollama pull llama3.2
    echo 3. Keep ollama running in background
    echo.
    echo Press any key to continue anyway...
    pause >nul
)

echo.
echo Starting Supreme Agent...
echo.

python og_supreme_agent.py

pause
