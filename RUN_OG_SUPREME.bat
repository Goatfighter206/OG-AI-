@echo off
echo.
echo ========================================================================
echo   🔥 OG-AI SUPREME AGENT - THE SMARTEST GANGSTER AI EVER MADE 🔥
echo ========================================================================
echo.

REM Check if Ollama is running
echo [1/4] Checking Ollama...
tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I /N "ollama.exe">NUL
if "%ERRORLEVEL%"=="1" (
    echo ⚠️  Ollama not running. Starting it...
    start /B ollama serve
    timeout /t 3 /nobreak > NUL
) else (
    echo ✅ Ollama is running
)

REM Make sure llama3.2 model is pulled
echo.
echo [2/4] Checking Ollama models...
ollama list | find "llama3.2" > NUL
if errorlevel 1 (
    echo 📥 Pulling llama3.2 model (this might take a minute)...
    ollama pull llama3.2
) else (
    echo ✅ llama3.2 model ready
)

REM Install required packages if needed
echo.
echo [3/4] Checking Python dependencies...
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt
pip install --quiet ollama anthropic openai duckduckgo-search wikipedia requests beautifulsoup4 pyttsx3 schedule

REM Run the Supreme Agent
echo.
echo [4/4] Starting OG-AI Supreme Agent...
echo.
python og_supreme_agent.py

pause
