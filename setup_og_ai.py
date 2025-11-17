"""
OG-AI Setup Script - Get this shit running properly
"""

import os
import sys
import subprocess

print("="*70)
print("  OG-AI SETUP - Let's get this motherfucker working!")
print("="*70)
print()

def run_command(cmd, description):
    """Run a command and show what's happening"""
    print(f"🔧 {description}...")
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"   ✓ Done!")
            return True
        else:
            print(f"   ✗ Failed: {result.stderr[:200]}")
            return False
    except Exception as e:
        print(f"   ✗ Error: {e}")
        return False

# Step 1: Install ALL dependencies
print("\n📦 STEP 1: Installing all the packages we need...")
print("-" * 70)
if run_command("pip install -r requirements.txt", "Installing from requirements.txt"):
    print("   🔥 All packages installed!")
else:
    print("   ⚠️  Some packages might have failed. Trying core packages...")
    core = [
        "fastapi>=0.109.1",
        "uvicorn[standard]>=0.24.0",
        "pydantic>=2.0.0",
        "python-dotenv>=1.0.0",
        "requests>=2.28.0"
    ]
    for pkg in core:
        run_command(f"pip install {pkg}", f"Installing {pkg}")

# Step 2: Check .env file
print("\n🔐 STEP 2: Setting up environment variables...")
print("-" * 70)
if not os.path.exists(".env"):
    print("   Creating .env file with default settings...")
    with open(".env", "w") as f:
        f.write("""# OG-AI Environment Configuration
# Keep all the gangster personality settings ON

# Personality Settings (KEEP THESE TRUE FOR THE FUN SHIT)
SWEARING_ENABLED=true
GHETTO_MODE=true
SMART_ASS_LEVEL=high

# AI Provider (use ollama for free local AI, or add API keys below)
AI_PROVIDER=ollama

# OpenAI (optional - add your key if you have one)
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini

# Anthropic Claude (optional - add your key if you have one)
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022

# Ollama (free local AI - install from ollama.ai)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2

# Server Settings
PORT=8000
DEVELOPMENT_MODE=true

# Code Execution
ENABLE_CODE_EXECUTION=true
ALLOWED_LANGUAGES=python,javascript,bash
""")
    print("   ✓ Created .env file with gangster mode ENABLED!")
else:
    print("   ✓ .env file already exists")

# Check if settings are enabled
with open(".env", "r") as f:
    env_content = f.read()
    swearing = "SWEARING_ENABLED=true" in env_content
    ghetto = "GHETTO_MODE=true" in env_content
    
    if swearing and ghetto:
        print("   🔥 HELL YEAH! Swearing and ghetto mode are ENABLED!")
    else:
        print("   ⚠️  WARNING: Personality settings might be turned off")

# Step 3: Test if enhanced agent works
print("\n🧪 STEP 3: Testing the enhanced agent...")
print("-" * 70)
try:
    from ai_agent_enhanced import EnhancedAIAgent
    agent = EnhancedAIAgent(name="OG-AI")
    response = agent.process_message("yo what's good")
    print(f"   ✓ Enhanced agent WORKS!")
    print(f"\n   Agent says: {response[:150]}...")
    enhanced_working = True
except Exception as e:
    print(f"   ✗ Enhanced agent failed: {e}")
    print("   → Will fall back to basic agent (no AI, just pattern matching)")
    enhanced_working = False

# Step 4: Check what's available
print("\n📊 STEP 4: Checking what features are available...")
print("-" * 70)

features = {}
try:
    import openai
    features['OpenAI'] = bool(os.getenv('OPENAI_API_KEY'))
except:
    features['OpenAI'] = False

try:
    import anthropic
    features['Claude'] = bool(os.getenv('ANTHROPIC_API_KEY'))
except:
    features['Claude'] = False

try:
    import ollama
    features['Ollama (Local AI)'] = True
except:
    features['Ollama (Local AI)'] = False

try:
    from duckduckgo_search import DDGS
    features['Web Search'] = True
except:
    features['Web Search'] = False

try:
    import wikipedia
    features['Wikipedia'] = True
except:
    features['Wikipedia'] = False

print()
for feature, available in features.items():
    status = "✓ Available" if available else "✗ Not available"
    print(f"   {feature:20} {status}")

# Final summary
print("\n" + "="*70)
print("  SETUP COMPLETE!")
print("="*70)
print()

if enhanced_working:
    print("🔥 FUCK YEAH! Enhanced agent is WORKING!")
    print("   You got the full gangster personality, swearing, and all that shit!")
else:
    print("⚠️  Enhanced agent not working - you'll get basic responses")
    print("   To fix: Install missing packages or set up API keys")

print("\n📝 HOW TO RUN:")
print("   python app.py")
print("   Then open: http://localhost:8000")
print()

if features.get('Ollama (Local AI)'):
    print("💡 TIP: You have Ollama installed! To use free local AI:")
    print("   1. Install Ollama from ollama.ai")
    print("   2. Run: ollama pull llama3.2")
    print("   3. Keep Ollama running in the background")
    print()

if not any(features.values()):
    print("⚠️  WARNING: No AI backends available!")
    print("   Options:")
    print("   1. Install Ollama (free) - ollama.ai")
    print("   2. Add OpenAI API key to .env")
    print("   3. Add Anthropic API key to .env")
    print()

print("💬 The agent will:")
print("   - Swear like a motherfucker")
print("   - Talk in ghetto slang and hood language")
print("   - Be a smart-ass and throw shade")
print("   - Still help you with coding and questions")
print()
print("🚀 Ready to go! Run: python app.py")
print("="*70)
