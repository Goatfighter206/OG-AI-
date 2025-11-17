@echo off
echo.
echo ======================================================
echo   OG-AI SUPREME AGENT SETUP - GANGSTER MODE!
echo ======================================================
echo.
echo Installing the smartest AI agent in the world...
echo.

REM Check for Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python ain't installed! Get that shit first!
    echo Download from: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/8] Installing base requirements...
pip install -r requirements.txt

echo.
echo [2/8] Installing AI providers (OpenAI, Anthropic, Ollama)...
pip install openai anthropic ollama

echo.
echo [3/8] Installing web search and research tools...
pip install duckduckgo-search wikipedia beautifulsoup4 requests

echo.
echo [4/8] Installing voice synthesis...
pip install pyttsx3 gtts pygame

echo.
echo [5/8] Installing FastAPI and async support...
pip install fastapi uvicorn

echo.
echo [6/8] Installing background task scheduler...
pip install schedule

echo.
echo [7/8] Installing additional dependencies...
pip install python-dotenv aiohttp

echo.
echo [8/8] Setting up environment file...
if not exist .env (
    echo Creating .env file...
    (
    echo # OG-AI SUPREME AGENT Configuration
    echo.
    echo # AI Providers - All enabled for maximum intelligence
    echo # Leave keys empty to use Ollama only
    echo OPENAI_API_KEY=your_openai_key_here
    echo OPENAI_MODEL=gpt-4o-mini
    echo.
    echo ANTHROPIC_API_KEY=your_anthropic_key_here
    echo ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
    echo.
    echo OLLAMA_MODEL=llama3.2
    echo.
    echo # Personality - Keep it gangster
    echo SWEARING_ENABLED=true
    echo GHETTO_MODE=true
    echo GANGSTER_MODE=true
    echo SMART_ASS_LEVEL=maximum
    echo.
    echo # Capabilities
    echo VOICE_ENABLED=true
    echo WEB_SEARCH_ENABLED=true
    echo CODE_GENERATION_ENABLED=true
    echo.
    echo # Self-Learning and Improvement
    echo ENABLE_SELF_LEARNING=true
    echo LEARN_FROM_INTERNET=true
    echo AUTO_IMPROVE_CODE=true
    echo.
    echo # Development
    echo DEVELOPMENT_MODE=true
    ) > .env
    echo .env file created!
)

echo.
echo ======================================================
echo   SUPREME AGENT SETUP COMPLETE!
echo ======================================================
echo.
echo NEXT STEPS:
echo.
echo 1. OLLAMA (Recommended for local AI^):
echo    - Download from: https://ollama.com
echo    - Install Ollama
echo    - Run: ollama pull llama3.2
echo    - Start Ollama: ollama serve
echo.
echo 2. OPTIONAL - OpenAI or Claude for more power:
echo    - Edit .env file
echo    - Add your API keys
echo.
echo 3. START THE SUPREME AGENT:
echo    Run: START_SUPREME.bat
echo    Or: python og_supreme_agent.py
echo.
echo FEATURES:
echo - Learns from internet every hour
echo - Improves own code daily
echo - Uses ALL AI providers simultaneously
echo - Generates production-ready code
echo - Web search and research
echo - Voice responses
echo - Full gangster personality
echo.
echo This AI is on another level. No cap!
echo.
pause
