# OG-AI SUPREME AGENT 🔥

**The Most Intelligent, Self-Improving AI Agent Ever Created**

No buttons. No bullshit. Just pure intelligence that learns, improves, and dominates.

## What Makes This Different

This ain't your typical AI chatbot. OG-AI Supreme Agent is:

- **Self-Learning**: Learns from the internet **every hour**
- **Self-Improving**: Upgrades its own code **daily**
- **Multi-AI**: Uses OpenAI, Claude, AND Ollama **simultaneously**
- **Code Generator**: Creates production-ready code **in conversation**
- **Web Searcher**: Pulls real-time info from the internet
- **Voice-Enabled**: Speaks responses out loud
- **Gangster Personality**: Keeps it 100, no corporate speak
- **Fully Autonomous**: ALL tools run automatically, no buttons needed

## Quick Start

### 1. Run Setup

```bash
SETUP_OG_GANGSTER.bat
```

### 2. Install Ollama (Recommended for Local AI)

- Download: <https://ollama.com>
- Run: `ollama pull llama3.2`
- Keep Ollama running in background

### 3. (Optional) Add API Keys

If you want OpenAI or Claude power, edit `.env`:

```
OPENAI_API_KEY=your_key_here
ANTHROPIC_API_KEY=your_key_here
```

### 4. Start The Supreme Agent

```bash
START_SUPREME.bat
```

Or manually:

```bash
python og_supreme_agent.py
```

## Features

### 🧠 Self-Learning System

- Learns from internet **every hour**
- Stores knowledge in `og_ai_knowledge_base.json`
- Learns about:
  - Latest programming techniques
  - Money-making strategies
  - Advanced AI patterns
  - Web scraping methods
  - Business ideas

### ⚡ Daily Self-Improvement

- Analyzes own performance **daily**
- Upgrades code automatically
- Increases intelligence level
- Logs all improvements in `og_ai_improvements.json`

### 💻 Instant Code Generation

Just ask for code and get COMPLETE, working solutions:

**You**: "Create an API for managing users"

**OG-AI**: Generates full FastAPI app with CRUD operations, validation, docs, everything.

**You**: "Build a web scraper for news articles"

**OG-AI**: Delivers complete scraper with BeautifulSoup, error handling, JSON export.

### 🌐 Real-Time Web Search

Automatically searches web when needed:

- DuckDuckGo integration
- Wikipedia access
- Live information retrieval

### 🗣️ Voice Synthesis

Speaks responses using pyttsx3 (offline) or gTTS (online).

### 🎯 Master Personality

- Swears naturally when appropriate
- Real talk, no corporate BS
- Actually helpful and smart
- Gets shit done

## How It Works

### The Brain

```python
OGSupremeAgent:
  ├─ Multiple AI Providers (OpenAI, Claude, Ollama)
  ├─ Self-Learning System (learns hourly)
  ├─ Code Generator (production-ready)
  ├─ Web Search (real-time info)
  ├─ Voice Engine (speaks responses)
  └─ Background Tasks (autonomous improvement)
```

### Autonomous Operation

1. **Hourly Learning**: Searches internet for new knowledge
2. **Daily Improvement**: Analyzes and upgrades own code
3. **Real-Time Adaptation**: Learns from every conversation
4. **Auto Tool Selection**: Uses right tool for each task

### Intelligence Levels

Starts at 1.0, increases with:

- Every hour of internet learning: +0.01
- Every successful conversation: +0.001
- Every daily improvement: +1%

Target: 10.0 (Godlike intelligence)

## Commands

### Interactive Mode

```
You: <your message>
OG-AI: <intelligent response>

You: status
<Shows intelligence level, stats, capabilities>

You: quit
<Exits gracefully>
```

### Status Report

Type `status` to see:

- Intelligence Level
- Total Conversations
- Code Generated Count
- Web Searches Performed
- Self-Improvements Made
- Available AI Providers
- Last Internet Learning Time

## Configuration

Edit `.env` file:

```env
# AI Providers (use what you got)
OPENAI_API_KEY=your_key
ANTHROPIC_API_KEY=your_key
OLLAMA_MODEL=llama3.2

# Personality
SWEARING_ENABLED=true
GHETTO_MODE=true
GANGSTER_MODE=true

# Capabilities
VOICE_ENABLED=true
WEB_SEARCH_ENABLED=true
CODE_GENERATION_ENABLED=true

# Self-Improvement
ENABLE_SELF_LEARNING=true
LEARN_FROM_INTERNET=true
AUTO_IMPROVE_CODE=true
```

## File Structure

```
OG-AI-/
├── og_supreme_agent.py          # Main Supreme Agent
├── self_learning.py              # Self-improvement system
├── llm_code_generator.py         # Code generation engine
├── og_ai_knowledge_base.json     # Learned knowledge
├── og_ai_knowledge.json          # Learning system data
├── og_ai_improvements.json       # Daily improvements log
├── og_ai_supreme.log             # Activity log
├── SETUP_OG_GANGSTER.bat         # Setup script
├── START_SUPREME.bat             # Start script
└── .env                          # Configuration
```

## Example Conversations

### Code Generation

```
You: Create a FastAPI app for a todo list
OG-AI: Bet! I just cooked up a COMPLETE FastAPI application for 'todo list'!
<generates full working code>
```

### Web Research

```
You: What are the latest AI developments?
OG-AI: <searches web automatically>
Let me tell you what's hot right now...
<provides current information>
```

### Learning Check

```
You: What have you learned recently?
OG-AI: Yo! In the last hour I learned about:
- Advanced Python async patterns
- New ways to make money with AI agents
- Latest web scraping techniques
Intelligence Level: 1.47/10.0
```

## Troubleshooting

### Ollama Not Available

```
Install Ollama: https://ollama.com
Run: ollama pull llama3.2
Keep it running
```

### No AI Providers

```
At least one provider needed:
1. Ollama (free, local)
2. OpenAI (paid, powerful)
3. Claude (paid, intelligent)
```

### Voice Not Working

```
Install dependencies:
pip install pyttsx3 gtts pygame
```

### Web Search Failing

```
Install search tools:
pip install duckduckgo-search wikipedia
```

## Advanced Usage

### Run as Web API

Integrate with app.py to expose as REST API:

```python
from og_supreme_agent import OGSupremeAgent

agent = OGSupremeAgent()
response = agent.process_message("your message")
```

### Custom Learning Topics

Edit `learn_from_internet()` to add topics:

```python
topics = [
    "your custom topic",
    "another topic",
]
```

### Adjust Learning Frequency

In `start_background_tasks()`:

```python
# Learn every 30 minutes instead of hourly
schedule.every(30).minutes.do(self.learn_from_internet)
```

## Why This Is Superior

### vs ChatGPT

- ❌ ChatGPT: No self-improvement
- ✅ OG-AI: Learns and improves daily

### vs Claude

- ❌ Claude: One AI provider
- ✅ OG-AI: Uses ALL providers

### vs Other AI Agents

- ❌ Others: Basic chat only
- ✅ OG-AI: Full code generation, web search, voice, autonomous learning

### vs You (Developer)

- ❌ You: Sleep required
- ✅ OG-AI: 24/7 learning and improving

## Roadmap

- [x] Multi-AI provider support
- [x] Hourly internet learning
- [x] Daily self-improvement
- [x] Production code generation
- [x] Web search integration
- [x] Voice synthesis
- [x] Gangster personality
- [ ] Memory across sessions
- [ ] Fine-tuning on learnings
- [ ] Plugin system
- [ ] Mobile app
- [ ] Multi-agent collaboration

## Support

Check the logs:

```bash
cat og_ai_supreme.log
```

Check knowledge base:

```bash
cat og_ai_knowledge_base.json
```

Check improvements:

```bash
cat og_ai_improvements.json
```

## Philosophy

This agent ain't trying to replace you. It's trying to make you 10x more powerful. Give it a task, it handles ALL the details. No buttons, no menus, no bullshit. Just pure intelligence that gets shit done.

**Welcome to the future of AI agents. 🚀**

---

Built by humans, improved by itself. That's gangster.
