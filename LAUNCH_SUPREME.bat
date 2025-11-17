@echo off
echo ===============================================
echo    OG SUPREME AI AGENT - ULTIMATE LAUNCHER
echo ===============================================
echo.
echo Installing all requirements...
echo.

REM Install Python packages
python -m pip install --upgrade pip
python -m pip install flask flask-cors requests pyttsx3 schedule wikipedia duckduckgo-search

REM Try to install AI providers
python -m pip install openai anthropic ollama 2>nul

echo.
echo ===============================================
echo    Checking Ollama...
echo ===============================================
echo.

REM Check if Ollama is installed
where ollama >nul 2>&1
if %errorlevel% neq 0 (
    echo WARNING: Ollama not found!
    echo Download from: https://ollama.ai
    echo.
    echo Continuing anyway...
) else (
    echo Ollama found! Starting Ollama server...
    start /B ollama serve
    timeout /t 3 >nul
    
    echo Pulling llama3.2 model...
    ollama pull llama3.2
)

echo.
echo ===============================================
echo    LAUNCHING OG SUPREME AGENT
echo ===============================================
echo.
echo The REALEST AI agent is now running!
echo Open your browser to: http://localhost:5000
echo.
echo Press Ctrl+C to stop the agent
echo.

python og_supreme_agent.py

pause
