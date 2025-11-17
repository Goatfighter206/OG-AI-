"""
Quick diagnostic script to check OG-AI setup
"""

import sys
print(f"Python Version: {sys.version}")
print("\n" + "="*60)
print("Checking Dependencies...")
print("="*60 + "\n")

# Core Dependencies
deps = {
    'fastapi': 'FastAPI (Core Web Framework)',
    'uvicorn': 'Uvicorn (ASGI Server)',
    'pydantic': 'Pydantic (Data Validation)',
    'openai': 'OpenAI API Client',
    'anthropic': 'Anthropic/Claude API Client',
    'ollama': 'Ollama (Local LLM)',
    'duckduckgo_search': 'DuckDuckGo Search',
    'wikipedia': 'Wikipedia API',
    'beautifulsoup4': 'BeautifulSoup (Web Scraping)',
    'requests': 'Requests (HTTP)',
    'dotenv': 'Python Dotenv (Environment Variables)'
}

installed = []
missing = []

for package, description in deps.items():
    try:
        __import__(package if package != 'dotenv' else 'dotenv')
        installed.append(f"✓ {package:25} - {description}")
    except ImportError:
        missing.append(f"✗ {package:25} - {description}")

print("INSTALLED:")
for item in installed:
    print(f"  {item}")

if missing:
    print("\nMISSING:")
    for item in missing:
        print(f"  {item}")
else:
    print("\n✓ All dependencies installed!")

# Check environment
print("\n" + "="*60)
print("Checking Environment Variables...")
print("="*60 + "\n")

import os
env_vars = {
    'OPENAI_API_KEY': 'OpenAI API access',
    'ANTHROPIC_API_KEY': 'Claude API access',
    'DEVELOPMENT_MODE': 'Development mode flag'
}

for var, desc in env_vars.items():
    value = os.getenv(var)
    if value:
        print(f"✓ {var:20} - Set ({desc})")
    else:
        print(f"✗ {var:20} - Not set ({desc})")

# Test basic agent
print("\n" + "="*60)
print("Testing Basic Agent...")
print("="*60 + "\n")

try:
    from ai_agent import AIAgent
    agent = AIAgent()
    response = agent.process_message("Hello")
    print(f"✓ Basic agent works: {response[:50]}...")
except Exception as e:
    print(f"✗ Basic agent failed: {e}")

# Test enhanced agent
print("\n" + "="*60)
print("Testing Enhanced Agent...")
print("="*60 + "\n")

try:
    from ai_agent_enhanced import EnhancedAIAgent
    agent = EnhancedAIAgent()
    response = agent.process_message("Hello")
    print(f"✓ Enhanced agent works!")
    print(f"  Response: {response[:100]}...")
except Exception as e:
    print(f"✗ Enhanced agent failed: {e}")

print("\n" + "="*60)
print("DIAGNOSIS COMPLETE")
print("="*60 + "\n")

if missing:
    print("TO FIX: Run this command to install missing packages:")
    print("  pip install -r requirements.txt")
else:
    print("All packages installed! If enhanced agent fails, check:")
    print("  1. API keys in .env file")
    print("  2. Ollama running (if using local LLM)")
