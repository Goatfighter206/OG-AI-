# OG-AI - AI Agent

A simple yet extensible conversational AI agent built with Python. This agent can maintain conversation context, save/load conversation history, and provide interactive responses.

## Features

- 🤖 Basic conversational AI capabilities
- 🌐 RESTful API with Flask for web deployment
- 💬 Conversation history management
- 💾 Save and load conversations to/from JSON
- ⚙️ Configurable agent settings
- 🔧 Extensible architecture for adding AI models
- 🚀 Ready for deployment on Render and other platforms
- 🖥️ CLI mode for local interactive usage

## Installation

1. Clone this repository:
```bash
git clone https://github.com/Goatfighter206/OG-AI-.git
cd OG-AI-
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

**Note:** Flask and Flask-CORS are required for running the web API. For CLI-only usage without the API, you can skip installing dependencies.

## Usage

### Web API Mode (Default)

Run the agent as a web service:

```bash
python ai_agent.py
```

The API server will start on `http://localhost:5000` by default. You can configure the port using the `PORT` environment variable.

### Interactive CLI Mode

Run the agent in interactive mode for a conversation:

```bash
python ai_agent.py --cli
```

Type your messages and the agent will respond. Type `quit` to exit.

### Programmatic Usage

```python
from ai_agent import AIAgent

# Create an agent
agent = AIAgent(name="OG-AI")

# Process a message
response = agent.process_message("Hello!")
print(response)

# Get conversation history
history = agent.get_conversation_history()
```

### Examples

Run the example script to see various usage patterns:

```bash
python example_usage.py
```

This demonstrates:
- Basic conversations
- Using custom configuration
- Saving and loading conversations
- Accessing conversation history

### Configuration

Edit `config.json` to customize the agent:

```json
{
  "agent_name": "OG-AI",
  "system_prompt": "You are a helpful AI assistant...",
  "max_history_length": 100,
  "save_conversations": true
}
```

## API Documentation

When running in Web API mode, the following endpoints are available:

### GET /
Returns API information and available endpoints.

**Response:**
```json
{
  "name": "OG-AI API",
  "version": "1.0.0",
  "status": "running",
  "endpoints": { ... }
}
```

### GET /health
Health check endpoint.

**Response:**
```json
{
  "status": "healthy",
  "agent_name": "OG-AI"
}
```

### POST /chat
Send a message to the AI agent.

**Request Body:**
```json
{
  "message": "Hello, how are you?"
}
```

**Response:**
```json
{
  "message": "Hello, how are you?",
  "response": "I'm functioning well, thank you for asking! How can I assist you?",
  "agent_name": "OG-AI"
}
```

### GET /history
Retrieve the conversation history.

**Response:**
```json
{
  "history": [
    {
      "role": "user",
      "content": "Hello!",
      "timestamp": "2025-11-05T19:43:22.509244"
    },
    {
      "role": "assistant",
      "content": "Hello! I'm OG-AI...",
      "timestamp": "2025-11-05T19:43:22.509254"
    }
  ],
  "message_count": 2
}
```

### POST /clear
Clear the conversation history.

**Response:**
```json
{
  "message": "Conversation history cleared successfully"
}
```

## Deploying to Render

This application is ready to deploy on [Render](https://render.com/). Follow these steps:

### 1. Connect Your GitHub Repository

1. Log in to your [Render dashboard](https://dashboard.render.com/)
2. Click "New +" and select "Web Service"
3. Connect your GitHub account and select the `Goatfighter206/OG-AI-` repository

### 2. Configure the Web Service

Use the following settings:

- **Name**: `og-ai-service` (or your preferred name)
- **Environment**: `Python 3`
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `python ai_agent.py`
- **Instance Type**: Free tier or higher

### 3. Environment Variables (Optional)

You can set the following environment variables in Render:

- `PORT`: The port to run the service on (Render sets this automatically)

### 4. Deploy

Click "Create Web Service" and Render will automatically deploy your application. Once deployed, you'll receive a URL like `https://og-ai-service.onrender.com` where your API will be accessible.

### Testing Your Deployment

Once deployed, test your API:

```bash
# Health check
curl https://your-app-name.onrender.com/health

# Send a message
curl -X POST https://your-app-name.onrender.com/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello!"}'
```

## API Reference

### AIAgent Class

#### Constructor
```python
AIAgent(name: str = "OG-AI", config: Optional[Dict] = None)
```

#### Methods

- `process_message(user_message: str) -> str`: Process a user message and return response
- `add_message(role: str, content: str)`: Add a message to conversation history
- `get_conversation_history() -> List[Dict]`: Get all conversation messages
- `clear_history()`: Clear the conversation history
- `save_conversation(filepath: str)`: Save conversation to JSON file
- `load_conversation(filepath: str)`: Load conversation from JSON file

## Future Enhancements

- Integration with OpenAI GPT models
- Integration with Anthropic Claude
- Support for multiple conversation threads
- Advanced context management
- Plugin system for extending capabilities
- Web-based user interface (currently REST API only)
- Voice interaction support
- Authentication and rate limiting
- Database persistence for conversations

## Contributing

Contributions are welcome! Feel free to submit issues and pull requests.

## License

This project is open source and available for educational and commercial use.
