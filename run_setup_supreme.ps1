Write-Host ""
Write-Host "======================================================"
Write-Host "  OG-AI SUPREME AGENT SETUP - GANGSTER MODE ACTIVATED! "
Write-Host "======================================================"
Write-Host ""
Write-Host "Yo, installing the most intelligent AI agent ever..."
Write-Host ""

# Check for Python
try {
    python --version
}
catch {
    Write-Host "ERROR: Python ain't installed, bruh! Get that shit first!"
    Write-Host "Download from: https://www.python.org/downloads/"
    pause
    exit 1
}

Write-Host "[1/8] Installing base requirements..."
pip install -r requirements.txt

Write-Host "[2/8] Installing AI providers (OpenAI, Anthropic, Ollama)..."
pip install openai anthropic ollama

Write-Host "[3/8] Installing web search and research tools..."
pip install duckduckgo-search wikipedia beautifulsoup4 requests

Write-Host "[4/8] Installing voice synthesis..."
pip install pyttsx3

Write-Host "[5/8] Installing FastAPI and async support..."
pip install fastapi uvicorn

Write-Host "[6/8] Installing background task scheduler..."
pip install schedule

Write-Host "[7/8] Installing additional dependencies..."
pip install python-dotenv aiohttp

Write-Host "[8/8] Setting up environment..."
if (-not (Test-Path .env)) {
    Write-Host "Creating .env file..."
    @"
# OG-AI SUPREME AGENT Configuration

# AI Providers - All enabled for maximum intelligence
# Leave keys empty to use Ollama only
OPENAI_API_KEY=your_openai_key_here
OPENAI_MODEL=gpt-4o-mini

ANTHROPIC_API_KEY=your_anthropic_key_here
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022

OLLAMA_MODEL=llama3.2

# Personality - Keep it gangster
SWEARING_ENABLED=true
GHETTO_MODE=true
GANGSTER_MODE=true
SMART_ASS_LEVEL=maximum

# Capabilities
VOICE_ENABLED=true
WEB_SEARCH_ENABLED=true
CODE_GENERATION_ENABLED=true

# Self-Learning and Improvement
ENABLE_SELF_LEARNING=true
LEARN_FROM_INTERNET=true
AUTO_IMPROVE_CODE=true

# Development
DEVELOPMENT_MODE=true
"@ | Out-File -FilePath .env -Encoding UTF8
    Write-Host ".env file created!"
}

Write-Host ""
Write-Host "======================================================"
Write-Host "  SUPREME AGENT SETUP COMPLETE!"
Write-Host "======================================================"
Write-Host ""
Write-Host "NEXT STEPS:"
Write-Host ""
Write-Host "1. OLLAMA (Recommended for local AI):"
Write-Host "   - Install: https://ollama.com"
Write-Host "   - Run: ollama pull llama3.2"
Write-Host "   - Keep ollama running in background"
Write-Host ""
Write-Host "2. OPTIONAL - OpenAI or Claude for more power:"
Write-Host "   - Edit .env file"
Write-Host "   - Add your API keys"
Write-Host ""
Write-Host "3. START THE SUPREME AGENT:"
Write-Host "   python og_supreme_agent.py"
Write-Host ""
Write-Host "FEATURES:"
Write-Host "- Learns from internet every hour"
Write-Host "- Improves own code daily"
Write-Host "- Uses ALL AI providers simultaneously"
Write-Host "- Generates production-ready code"
Write-Host "- Web search and research"
Write-Host "- Voice responses"
Write-Host "- Full gangster personality"
Write-Host ""
Write-Host "This AI is on another level. No cap!"
Write-Host ""
