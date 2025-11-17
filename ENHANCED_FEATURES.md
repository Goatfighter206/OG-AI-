# OG-AI Enhanced Features Guide

## Your AI Just Got a Whole Lot Smarter

Your OG-AI agent now has REAL intelligence with personality, swearing, ghetto flair, web access, code execution, and smart-ass remarks. No cap! 🔥

## What's New

### 1. Sassy Personality with Attitude

- **Ghetto mode activated**: Your AI speaks with AAVE, slang, and urban vernacular
- **Smart-ass remarks**: Witty, confident, and occasionally roasts you (lightly)
- **Swearing enabled**: Keeps it real when appropriate
- **Personality levels**: Adjustable sass level (low, medium, high)

### 2. Real AI Integration

Choose from multiple AI providers:

- **OpenAI GPT** (gpt-4o-mini, gpt-4, etc.)
- **Anthropic Claude** (claude-3-5-sonnet, claude-3-opus, etc.)
- **Ollama** (local LLMs - llama3.2, mistral, etc.)
- **Fallback mode**: Enhanced pattern matching with personality when no AI is configured

### 3. Internet Access & Web Search

- **DuckDuckGo search**: Free, unlimited web searching
- **Wikipedia integration**: Quick facts and information
- **Web scraping**: Can fetch and read webpage content
- Automatically detects when you need current information

### 4. Code Execution

- **Python execution**: Run Python code directly
- **JavaScript/Node.js**: Execute JavaScript code
- **Bash commands**: Run shell commands
- **Syntax highlighting**: Pretty code display
- **Safe execution**: 5-second timeout limit

### 5. Multi-Language Understanding

- Detects your intent automatically
- Knows when to search the web
- Knows when to execute code
- Intelligent context gathering

## Configuration

### .env File Settings

```env
# AI Provider Selection
AI_PROVIDER=openai              # Options: openai, anthropic, ollama, fallback

# OpenAI Settings
OPENAI_API_KEY=sk-your-key-here
OPENAI_MODEL=gpt-4o-mini        # Or: gpt-4, gpt-3.5-turbo

# Anthropic Settings
ANTHROPIC_API_KEY=sk-ant-your-key-here
ANTHROPIC_MODEL=claude-3-5-sonnet-20241022

# Ollama Settings (local AI)
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2           # Or: mistral, codellama, etc.

# Personality Settings
SWEARING_ENABLED=true           # Allow swearing: true/false
GHETTO_MODE=true                # Urban vernacular: true/false
SMART_ASS_LEVEL=high            # Options: low, medium, high

# Code Execution
ENABLE_CODE_EXECUTION=true
ALLOWED_LANGUAGES=python,javascript,bash

# Server
PORT=8000
DEVELOPMENT_MODE=true
```

## Usage Examples

### 1. Sassy Greetings

**You:** "Yo what's good?"

**OG-AI:** "Ayyy wassup, OG-AI in the house. How can I help your ass today?"

**You:** "What's up bro?"

**OG-AI:** "Well well well, look who decided to hit me up. I'm OG-AI, whatchu want?"

### 2. Web Search

**You:** "Search for the latest Python news"

**OG-AI:** "Aight bet, I found some info for you:

[WEB SEARCH RESULTS]:

1. Python 3.13 Released with Performance Improvements...
2. New FastAPI features announced...
...

That should help you out, fam. Anything else?"

### 3. Code Execution

**You:** "Run this code:
\`\`\`python
print('Hello World!')
for i in range(5):
    print(f'Number {i}')
\`\`\`"

**OG-AI:** "Yo check it, I ran that code for you:

[CODE EXECUTION RESULT]:
Hello World!
Number 0
Number 1
Number 2
Number 3
Number 4

Clean output fam. Need anything else?"

### 4. Wikipedia Lookup

**You:** "Look up Python programming on Wikipedia"

**OG-AI:** "Aye I got you, here's what I found:

[WIKIPEDIA]:
Python is a high-level, interpreted programming language...

That answer your question or you need me to dig deeper?"

### 5. Coding Help

**You:** "Can you help me code?"

**OG-AI:** "Aye I'm nice with the code fr fr. Show me what you working on and I'll help you out. I can run Python, JavaScript, or bash. Just wrap your code in triple backticks like \`\`\`python or \`\`\`javascript and I'll execute that joint for you. 💻"

## Using Different AI Providers

### Option 1: OpenAI GPT (Recommended for Best Results)

1. Get an API key from <https://platform.openai.com/>
2. Add to `.env`:

   ```
   OPENAI_API_KEY=sk-your-key-here
   AI_PROVIDER=openai
   ```

3. Restart the server

### Option 2: Anthropic Claude (Great for Coding)

1. Get an API key from <https://console.anthropic.com/>
2. Add to `.env`:

   ```
   ANTHROPIC_API_KEY=sk-ant-your-key-here
   AI_PROVIDER=anthropic
   ```

3. Restart the server

### Option 3: Ollama (Free, Local, No API Key)

1. Download Ollama from <https://ollama.com/>
2. Install and run: `ollama run llama3.2`
3. Update `.env`:

   ```
   AI_PROVIDER=ollama
   OLLAMA_MODEL=llama3.2
   ```

4. Restart the server

### Option 4: Fallback Mode (No AI API Required)

- Uses enhanced pattern matching
- Still has personality and sass
- Includes web search and code execution
- Set `AI_PROVIDER=fallback` in `.env`

## Advanced Features

### Web Scraping

**You:** "Scrape content from <https://example.com>"

The agent will fetch and read the webpage content.

### Code Debugging

**You:** "This code has an error:
\`\`\`python
print('hello'
\`\`\`"

**OG-AI:** Will detect the syntax error and help fix it.

### Current Events

**You:** "What's the latest news about AI?"

The agent will search the web for current information.

## Personality Customization

### Adjust Sass Level

In `.env`:

- `SMART_ASS_LEVEL=low` - Minimal sass, mostly helpful
- `SMART_ASS_LEVEL=medium` - Balanced personality
- `SMART_ASS_LEVEL=high` - Maximum sass and attitude

### Enable/Disable Swearing

```env
SWEARING_ENABLED=true   # Allows occasional swearing
SWEARING_ENABLED=false  # Family-friendly mode
```

### Ghetto Mode

```env
GHETTO_MODE=true   # Full urban vernacular and slang
GHETTO_MODE=false  # More standard English (but still sassy)
```

## API Endpoints

All the same endpoints as before, but now with enhanced responses:

### POST /chat

Send messages and get sassy, intelligent responses.

### GET /history

View your conversation with timestamps.

### POST /clear

Clear conversation history.

### GET /health

Check server status.

## Tips for Best Results

1. **Be specific**: The AI understands context better with details
2. **Use code blocks**: Wrap code in triple backticks for execution
3. **Ask for searches**: Say "search for" or "look up" to trigger web search
4. **Experiment**: Try different questions and see the personality shine

## Troubleshooting

### "API key not found" error

- Make sure you've created a `.env` file (not `.env.example`)
- Add your actual API key
- Restart the server

### "Ollama connection failed"

- Install Ollama from <https://ollama.com/>
- Run `ollama run llama3.2` before starting the server
- Or switch to fallback mode: `AI_PROVIDER=fallback`

### Web search not working

- Check internet connection
- DuckDuckGo search is free but may have rate limits
- Try again in a few seconds

### Code execution disabled

- Set `ENABLE_CODE_EXECUTION=true` in `.env`
- Restart the server

## Examples of the Sass

**Generic question:**
"'{your question}' - aight I hear you. But lowkey you gotta give me more to work with fam."

**Error occurs:**
"Bruh, that ain't it. Try again."

**Success:**
"Boom! That's how it's done. You're welcome btw."

**Help request:**
"I got you, no cap. Here's what you need to know..."

## Security Notes

- Code execution is sandboxed with a 5-second timeout
- Only allowed languages can be executed (set in `.env`)
- Web scraping respects robots.txt
- API keys stored in `.env` (never commit this file!)

## What's Next?

Your OG-AI agent is now one of the smartest, sassiest AI assistants around. It can:

- Search the internet for real-time info
- Execute and debug code
- Integrate with GPT, Claude, or local LLMs
- Keep it 100 with personality and attitude

Open <http://localhost:8000> and start chatting with your enhanced AI! 🔥

No cap, this thing is fire now! 💯
