# Quick Start Guide - OG-AI Agent

## Your Application is Ready!

Your OG-AI Agent is now fully set up and ready to use!

## How to Start the Application

### Windows
Double-click the `start_server.bat` file, or run in terminal:
```bash
start_server.bat
```

### Manual Start
```bash
python app.py
```

## Access Your Application

Once the server is running, you can access:

1. **Web Interface**: http://localhost:8000
   - Beautiful chat interface to interact with your AI agent
   - Real-time responses
   - Conversation history management

2. **API Documentation**: http://localhost:8000/docs
   - Interactive API documentation (Swagger UI)
   - Test all endpoints directly from the browser

3. **Alternative API Docs**: http://localhost:8000/redoc
   - ReDoc-style documentation

## Available Endpoints

### GET /
- Serves the web chat interface

### GET /health
- Health check endpoint
- Returns: `{"status": "healthy", "agent_name": "OG-AI", "message": "Service is running"}`

### POST /chat
- Send a message to the AI agent
- Request body: `{"message": "Your message here"}`
- Returns: `{"response": "AI response", "agent_name": "OG-AI", "timestamp": "..."}`

### GET /history
- Retrieve conversation history
- Returns all messages with timestamps

### POST /clear or POST /reset
- Clear the conversation history
- Returns: `{"status": "success", "agent_name": "OG-AI", "message": "Conversation history has been cleared"}`

## Testing with cURL

```bash
# Health check
curl http://localhost:8000/health

# Send a message
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d "{\"message\": \"Hello!\"}"

# Get history
curl http://localhost:8000/history

# Clear history
curl -X POST http://localhost:8000/clear
```

## Configuration

Edit `config.json` to customize the agent:

```json
{
  "agent_name": "OG-AI",
  "system_prompt": "You are a helpful AI assistant...",
  "max_history_length": 100,
  "save_conversations": true
}
```

## Features

- Simple conversational AI with pattern matching
- RESTful API with FastAPI
- Beautiful web interface
- Conversation history management
- CORS enabled for cross-origin requests
- Mobile-friendly responsive design
- Real-time message updates
- Error handling and validation

## Next Steps

1. **Integrate AI Models**: The current implementation uses simple pattern matching. You can integrate:
   - OpenAI GPT models (uncomment in requirements.txt)
   - Anthropic Claude API
   - Local LLMs (Ollama, LLaMA, etc.)

2. **Customize Responses**: Edit the `_generate_response` method in [ai_agent.py](ai_agent.py:70) to add more sophisticated logic

3. **Deploy to Production**: Use the included deployment guides:
   - `DEPLOYMENT.md` - General deployment guide
   - `RENDER_DEPLOY.md` - Deploy to Render.com
   - Docker support included

## Troubleshooting

### Server won't start
- Make sure port 8000 is not in use by another application
- Check that all dependencies are installed: `pip install -r requirements.txt`

### Frontend can't connect
- Verify the backend is running on http://localhost:8000
- Check the browser console for errors
- Make sure CORS is properly configured

### Need help?
- Check the full documentation in `README.md`
- Review the API docs at http://localhost:8000/docs
- Check the deployment guides for production setup

## Development Mode

To enable hot-reload during development:

```bash
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

## Current Status

- Backend: Running on http://localhost:8000
- Frontend: Accessible at http://localhost:8000
- All endpoints tested and working
- Ready for use!

Enjoy using your OG-AI Agent!
