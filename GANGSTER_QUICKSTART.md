# OG-AI GANGSTER QUICK START 🔥

Yo! This is the ULTIMATE guide to get OG-AI running with ALL the fire features!

## Features That Make OG-AI the GOAT

- **Smart as FUCK**: Understands street talk, technical shit, and everything between
- **Code Generator**: Creates complete, production-ready code - APIs, CLIs, scrapers, React components
- **Web Search**: Searches the internet for real-time info
- **Voice Responses**: Actually speaks to you (optional)
- **Self-Learning**: Gets smarter from every conversation
- **Gangster Personality**: Keeps it 100, no corporate bullshit
- **Multiple AI Models**: Works with OpenAI, Claude, or Ollama (local)

## Setup (3 Minutes, No Cap)

### Step 1: Run the Gangster Setup

```bash
SETUP_OG_GANGSTER.bat
```

This installs EVERYTHING you need.

### Step 2: Choose Your AI

**Option A - Ollama (FREE, LOCAL - RECOMMENDED)**

```bash
# Install Ollama from https://ollama.ai
# Then pull the model:
ollama pull llama3.2

# Start Ollama (keep it running):
ollama serve
```

**Option B - OpenAI**

1. Get API key from <https://platform.openai.com>
2. Edit `.env` file:

   ```
   AI_PROVIDER=openai
   OPENAI_API_KEY=your_key_here
   ```

**Option C - Anthropic Claude**

1. Get API key from <https://console.anthropic.com>
2. Edit `.env` file:

   ```
   AI_PROVIDER=anthropic
   ANTHROPIC_API_KEY=your_key_here
   ```

### Step 3: Start That Shit

```bash
python app.py
```

Go to: <http://localhost:8000>

## How to Use Like a Boss

### Generate Code

```
"Yo build me an API for managing tasks"
"Create a web scraper for product prices"
"Make me a React component for user profiles"
"Write a CLI tool called TaskMaster"
```

OG-AI generates COMPLETE working code, not just snippets!

### Search the Web

```
"Search for latest Python frameworks"
"What's the weather in Tokyo?"
"Look up quantum computing"
```

### Execute Code

````
Run this code:
```python
print("OG-AI is the realest!")
```
````

### Voice Mode

Set in `.env`:

```
VOICE_ENABLED=true
```

Now OG-AI will speak responses!

### Daily Self-Improvement

OG-AI learns from conversations and improves itself. Check intelligence:

```
GET http://localhost:8000/intelligence
```

Manually trigger improvement:

```
POST http://localhost:8000/improve
```

## Configuration (.env File)

```bash
# AI Provider
AI_PROVIDER=ollama              # ollama, openai, or anthropic

# Personality (KEEP THESE TRUE FOR MAXIMUM GANGSTER)
SWEARING_ENABLED=true
GHETTO_MODE=true
GANGSTER_MODE=true
SMART_ASS_LEVEL=high

# Features
VOICE_ENABLED=true
ENABLE_SELF_LEARNING=true

# Models
OLLAMA_MODEL=llama3.2
OPENAI_MODEL=gpt-4o-mini
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022
```

## API Endpoints

```
GET  /                  - Web interface
GET  /api               - API info
GET  /health            - Health check
POST /chat              - Send message
GET  /history           - Get conversation history
POST /reset             - Clear history
GET  /intelligence      - Get learning stats
POST /improve           - Trigger self-improvement
```

## Example Conversations

**Code Generation:**

```
You: "Build me an API for managing books"
OG-AI: "🔥 Yo! I just cooked up a COMPLETE FastAPI application... [provides full working code]"
```

**Web Search:**

```
You: "What's the latest on AI developments?"
OG-AI: "Bet, I'm bout to search the web for you... [provides real search results]"
```

**Understanding Slang:**

```
You: "Yo what's good, make me some fire code for a todo list app"
OG-AI: "Ayyy wassup! Say less, I got you. This bout to be straight heat... [generates code]"
```

## Troubleshooting

### "Not recognized" Errors

- Make sure Python is in your PATH
- Run: `python --version` to check

### Ollama Not Working

- Install Ollama: <https://ollama.ai>
- Run: `ollama pull llama3.2`
- Keep `ollama serve` running in background

### Voice Not Working

- Windows: Should work automatically
- Mac/Linux: Install espeak: `sudo apt-get install espeak`

### API Keys Not Working

- Check `.env` file has correct format
- No quotes needed around keys
- OpenAI key starts with `sk-`
- Claude key starts with `sk-ant-`

## Advanced Features

### Custom Code Templates

Edit `llm_code_generator.py` to add your own templates

### Custom Personality

Edit `config.json` to change the system prompt

### Add New Tools

Check `ai_agent_enhanced.py` for adding new capabilities

## Keep It Real

OG-AI is designed to be:

- **Smart**: Actually understands what you mean
- **Helpful**: Generates real working code
- **Real**: No fake corporate politeness
- **Gangster**: Keeps it 100 with you

## Need Help?

Check the other docs:

- `HOW_TO_START.md` - Basic setup
- `GANGSTER_MODE_GUIDE.md` - Full personality guide
- `VOICE_FEATURES.md` - Voice setup
- `ENHANCED_FEATURES.md` - All features explained

---

**Built with 💯 by real ones, for real ones. Keep it gangster!**
