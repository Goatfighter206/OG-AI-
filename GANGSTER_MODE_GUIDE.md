# OG-AI GANGSTER MODE - Complete Guide

## 🔥 What Makes This AI Different & Fun

Your OG-AI agent has a **GANGSTER PERSONALITY** with:

- **Swearing** - Says "fuck", "shit", "damn", "hell", "ass" naturally
- **Hood/Ghetto Talk** - Uses AAVE, street slang, gangster vocabulary
- **Smart-Ass Attitude** - Roasts you, throws shade, keeps it real
- **Genius Intelligence** - Still helps you with coding, debugging, questions
- **No Bullshit** - Brutally honest, calls out stupid shit

### Example Responses

**Normal AI:**
> "Hello! How can I assist you today?"

**OG-AI Gangster Mode:**
> "Yo what the fuck's good, I'm OG-AI. What you need from ya boy?"

**Normal AI:**
> "I'll help you with that code issue."

**OG-AI Gangster Mode:**
> "Aight bet, I'm bout to code the fuck outta this for you. Watch this magic..."

---

## 🚀 Quick Start (3 Steps)

### 1. Run Setup

```bash
python setup_og_ai.py
```

This installs everything you need and configures the gangster personality.

### 2. Start the Server

```bash
python app.py
```

OR just double-click: **START_GANGSTER.bat**

### 3. Open in Browser

```
http://localhost:8000
```

---

## 🎮 Personality Settings

All settings are in the `.env` file:

```bash
# Keep these TRUE for the full gangster experience
SWEARING_ENABLED=true       # Enables swearing
GHETTO_MODE=true            # Enables hood/street talk
SMART_ASS_LEVEL=high        # Maximum sass and shade

# AI Backend (choose one)
AI_PROVIDER=ollama          # Free local AI (recommended)
# AI_PROVIDER=openai        # Needs API key ($)
# AI_PROVIDER=anthropic     # Needs API key ($)
```

### Personality Levels

**SMART_ASS_LEVEL Options:**

- `high` - Full gangster, roasts constantly, maximum swearing
- `medium` - Still hood but less aggressive
- `low` - Mild gangster personality

---

## 🤖 AI Backend Options

### Option 1: Ollama (FREE & RECOMMENDED)

**Best for:** Everyone - it's free and works offline!

1. Download from [ollama.ai](https://ollama.ai)
2. Install Ollama
3. Run: `ollama pull llama3.2`
4. Keep Ollama running in background
5. Set in .env: `AI_PROVIDER=ollama`

**Pros:**

- ✅ Completely FREE
- ✅ Works offline
- ✅ Private (your data stays local)
- ✅ Fast responses

**Cons:**

- ❌ Needs ~4GB RAM
- ❌ First download is large

### Option 2: OpenAI GPT (PAID)

**Best for:** Maximum intelligence, willing to pay

1. Get API key from [platform.openai.com](https://platform.openai.com)
2. Add to .env: `OPENAI_API_KEY=sk-your-key-here`
3. Set: `AI_PROVIDER=openai`

**Cost:** ~$0.50-2.00 per 1000 messages (varies by model)

### Option 3: Claude (PAID)

**Best for:** Long conversations, detailed responses

1. Get API key from [console.anthropic.com](https://console.anthropic.com)
2. Add to .env: `ANTHROPIC_API_KEY=your-key-here`
3. Set: `AI_PROVIDER=anthropic`

**Cost:** Similar to OpenAI

### Option 4: No AI (FREE Fallback)

If no AI backend is set up, OG-AI uses basic pattern matching:

- Still has gangster personality
- Still swears and talks hood
- Limited to pre-programmed responses
- No actual intelligence, just attitude

---

## 🎯 Features That Make It Fun

### 1. Gangster Greetings

```
User: "Hello"
OG-AI: "Ayyy wassup motherfucka, OG-AI in this bitch. How can I help your ass?"
```

### 2. Smart-Ass Responses

```
User: "Can you help me?"
OG-AI: "No shit, Sherlock. But let me break it down for you anyway..."
```

### 3. Coding with Attitude

```
User: "Write me a Python function"
OG-AI: "Bet, I'm about to code the fuck outta this for you..."
[Writes perfect, working code]
"BOOM! That's how the fuck you do it!"
```

### 4. Roasting Bad Code

```
User: [Shows buggy code]
OG-AI: "Yo that code is trash, no cap. Let me show you how a real G does it..."
```

### 5. Web Search with Personality

```
User: "Search for Python tutorials"
OG-AI: "Hold up, lemme search the web and see what's good..."
[Searches and returns results]
"Aight bet, found you some fire resources..."
```

---

## 🛠️ Troubleshooting

### Problem: Agent isn't swearing or using ghetto talk

**Fix:** Check your .env file:

```bash
SWEARING_ENABLED=true
GHETTO_MODE=true
```

Both must be `true` (lowercase).

### Problem: Getting basic boring responses

**Reasons:**

1. Enhanced agent isn't loading (missing dependencies)
2. No AI backend configured
3. Personality settings turned off

**Fix:**

```bash
python setup_og_ai.py
```

This will diagnose and fix the issue.

### Problem: "Enhanced features not available"

**Meaning:** Missing packages or no AI backend

**Fix Option 1 (Quick):**

```bash
pip install ollama
ollama pull llama3.2
```

**Fix Option 2 (Full):**

```bash
pip install -r requirements.txt
```

### Problem: Agent is TOO aggressive/offensive

**Fix:** Lower the sass level in .env:

```bash
SMART_ASS_LEVEL=medium
# or
SMART_ASS_LEVEL=low
```

---

## 📱 Using the Interface

### Web Interface (`index_epic.html`)

The main interface at <http://localhost:8000>

- Clean modern design
- Real-time chat
- Shows personality in full effect

### Classic Interface (`frontend.html`)

Alternative UI at <http://localhost:8000/classic>

- Simpler design
- Same functionality

### API Direct Access

Use the REST API at <http://localhost:8000/docs>

- Programmatic access
- Integration with other apps

---

## 🎨 Customization

### Add Your Own Slang

Edit `ai_agent_enhanced.py` and add to `GANGSTER_SLANG`:

```python
'greetings': [
    "Your custom greeting here, I'm {name}!",
    # Add more...
],
```

### Change Personality Phrases

Find the `_build_system_prompt()` method and customize:

- Vocabulary
- Response style  
- Personality traits
- Swear frequency

### Adjust Swearing Level

In `_generate_response()` method, adjust how often gangster phrases are used.

---

## 🔥 Examples of Full Conversations

### Coding Help

```
You: "Help me fix this Python error"
OG-AI: "Aight bet, I'm on it. Give me a sec to work this magic..."
[Analyzes error]
OG-AI: "Bruh... that's trash. But I got you, no worries fam. Here's what's fucked up..."
[Provides solution]
OG-AI: "BOOM! There it is. That's how the fuck you do it!"
```

### Learning New Tech

```
You: "Explain React hooks"
OG-AI: "Bet, lemme break this shit down for you. React hooks are basically..."
[Detailed explanation with examples]
OG-AI: "And that's on God. Problem solved, easy work!"
```

### Web Search

```
You: "What's the latest in AI?"
OG-AI: "Hold up, lemme search the web and see what's good..."
[Searches internet]
OG-AI: "Aight, found you some fire info. Latest AI shit is..."
```

---

## 💪 Why This Is Better Than Regular AI

| Feature | Regular AI | OG-AI Gangster Mode |
|---------|-----------|---------------------|
| Personality | Boring, corporate | Real, street, fun |
| Swearing | Never | Fuck yeah |
| Honesty | Sugarcoats | Brutally honest |
| Help Quality | Same | Same + attitude |
| Entertainment | 😐 | 🔥🔥🔥 |
| Roasting | Never | Constantly |
| Realness | Fake nice | Keeps it 100 |

---

## 📞 Commands & Endpoints

### Chat Commands (in web interface)

- Just type normally - agent responds with personality
- Ask for code - gets written with gangster commentary
- Ask questions - answered with attitude and accuracy

### API Endpoints

- `POST /chat` - Send message, get gangster response
- `GET /history` - View conversation
- `POST /reset` - Clear history
- `GET /health` - Check if running

---

## 🎯 Next Steps

1. **Run setup:** `python setup_og_ai.py`
2. **Start server:** Double-click `START_GANGSTER.bat` or run `python app.py`
3. **Open browser:** <http://localhost:8000>
4. **Start chatting!** Type "yo what's good" to see the personality

---

## 🔒 Note on Language

This agent uses explicit language (swearing, slang) as part of its personality design. It's meant to be:

- **Entertaining** - Fun to interact with
- **Honest** - No corporate BS
- **Helpful** - Still solves your problems
- **Authentic** - Talks like a real person from the streets

If you prefer clean language, set `SWEARING_ENABLED=false` in `.env`

---

## 💰 Costs

- **Ollama:** FREE (100% free, runs locally)
- **OpenAI:** ~$0.50-2.00 per 1000 messages
- **Claude:** ~$0.50-2.00 per 1000 messages  
- **No AI Backend:** FREE (basic pattern matching only)

**Recommendation:** Start with Ollama (free) to test it out!

---

## 🚀 Ready to Roll?

Run this now:

```bash
python setup_og_ai.py
```

Then:

```bash
python app.py
```

Open <http://localhost:8000> and say "yo what's good" to meet your gangster AI! 🔥
