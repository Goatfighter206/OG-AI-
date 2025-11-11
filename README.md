# OG-AI - AI Agent

A simple yet extensible conversational AI agent built with Python. This agent can maintain conversation context, save/load conversation history, and provide interactive responses. Now available as a **REST API web service** deployable on Render!

## Features

- 🤖 Basic conversational AI capabilities
- 💬 Conversation history management
- 💾 Save and load conversations to/from JSON
- ⚙️ Configurable agent settings
- 🔧 Extensible architecture for adding AI models
- 🌐 **FastAPI REST API** for web service integration
- ☁️ **Ready for deployment** on Render
- 🐍 Pure Python implementation

## Installation

1. Clone this repository:
```bash
git clone https://github.com/Goatfighter206/OG-AI-.git
cd OG-AI-
```

2. Create a virtual environment (recommended):
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

## Usage

### Web Service Mode (Recommended)

Start the FastAPI web service:

```bash
python app.py
```

Or use uvicorn directly:

```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

The API will be available at `http://localhost:8000`. Visit `http://localhost:8000/docs` for interactive API documentation.

### Interactive CLI Mode

Run the agent in interactive mode for a conversation:

```bash
python ai_agent.py
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

### API Endpoints

The web service exposes the following REST API endpoints:

#### `GET /` or `GET /health`
Check the service health status.

**Response:**
```json
{
  "status": "healthy",
  "agent_name": "OG-AI",
  "message": "Service is running"
}
```

#### `POST /chat`
Send a message to the AI agent.

**Request:**
```json
{
  "message": "Hello! How are you?"
}
```

**Response:**
```json
{
  "response": "Hello! I'm OG-AI, your AI assistant. How can I help you today?",
  "agent_name": "OG-AI",
  "timestamp": "2025-11-05T20:00:00.000000"
}
```

#### `GET /history`
Get the conversation history.

**Response:**
```json
{
  "conversation": [
    {
      "role": "user",
      "content": "Hello!",
      "timestamp": "2025-11-05T20:00:00.000000"
    },
    {
      "role": "assistant",
      "content": "Hello! I'm OG-AI...",
      "timestamp": "2025-11-05T20:00:01.000000"
    }
  ],
  "message_count": 2
}
```

#### `POST /reset`
Clear the conversation history.

**Response:**
```json
{
  "status": "success",
  "agent_name": "OG-AI",
  "message": "Conversation history has been cleared"
}
```

### Testing the API

Use curl to test the endpoints:

```bash
# Test health
curl http://localhost:8000/health

# Send a message
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello!"}'

# Get conversation history
curl http://localhost:8000/history

# Reset conversation
curl -X POST http://localhost:8000/reset
```

Or visit `http://localhost:8000/docs` for interactive Swagger UI documentation.

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

## Deployment on Render

### Prerequisites
- A [Render account](https://render.com/) (free tier available)
- This repository pushed to GitHub

### Deployment Steps

1. **Login to Render** and click "New +" → "Web Service"

2. **Connect your repository:**
   - Select "Build and deploy from a Git repository"
   - Connect your GitHub account
   - Choose the `OG-AI-` repository

3. **Configure the service:**
   - **Name:** `og-ai-service` (or your preferred name)
   - **Region:** Choose your preferred region
   - **Branch:** `main` (or your default branch)
   - **Root Directory:** Leave blank
   - **Runtime:** `Python 3`
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn app:app --host 0.0.0.0 --port $PORT --workers 4 --proxy-headers --forwarded-allow-ips='*'`
     > **Note:** Using multiple workers (`--workers 4`) will cause each worker to have separate conversation state. For consistent history, use `--workers 1` or implement shared storage (see concurrency note in `ai_agent.py`).

4. **Environment Variables (Optional):**
   - Add any required environment variables
   - `PORT` is automatically set by Render

5. **Select Plan:**
   - Free tier is sufficient for testing
   - Upgrade to paid plan for production use

6. **Deploy:**
   - Click "Create Web Service"
   - Render will build and deploy your service
   - Wait for the deployment to complete (usually 2-5 minutes)

7. **Access your service:**
   - Your service will be available at `https://your-service-name.onrender.com`
   - Visit `https://your-service-name.onrender.com/docs` for API documentation
   - Test the API: `https://your-service-name.onrender.com/health`

### Local Testing Before Deployment

Before deploying to Render, test the service locally:

```bash
# Install dependencies
pip install -r requirements.txt

# Start the service
python app.py
```

**Option 1: Use the automated test script (recommended):**

In another terminal:

```bash
python test_api.py
```

This will run a comprehensive test suite covering all API endpoints.

**Option 2: Manual testing with curl:**

```bash
# Test health
curl http://localhost:8000/health

# Send a message
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello!"}'

# Get history
curl http://localhost:8000/history

# Reset conversation
curl -X POST http://localhost:8000/reset
```

**Option 3: Interactive API documentation:**

Visit `http://localhost:8000/docs` to explore the interactive Swagger UI documentation where you can test all endpoints directly in your browser.

### Deployment Files

This repository includes the following deployment files:

- **`app.py`**: FastAPI web service application
- **`requirements.txt`**: Python dependencies including FastAPI and uvicorn
- **`Procfile`**: Process file for Render (alternative start command)
- **`.env.example`**: Example environment variables file

### Monitoring and Logs

Once deployed on Render:
- View logs in the Render dashboard under "Logs"
- Monitor service health at `/health` endpoint
- Set up alerts and notifications in Render settings

### Troubleshooting

**Service not starting:**
- Check the build logs in Render dashboard
- Verify all dependencies are in `requirements.txt`
- Ensure `PORT` environment variable is properly configured

**502 Bad Gateway:**
- Service might still be starting (wait 1-2 minutes)
- Check if the service is binding to `0.0.0.0` and using `$PORT`

**Slow cold starts (Free tier):**
- Free tier services spin down after periods of inactivity
- First request after spin-down may take 30-60 seconds

## Future Enhancements

- Integration with OpenAI GPT models
- Integration with Anthropic Claude
- Support for multiple conversation threads
- Advanced context management
- Plugin system for extending capabilities
- Web interface
- Voice interaction support
- User authentication and session management
- Database integration for persistent storage

## Contributing

Contributions are welcome! Feel free to submit issues and pull requests.

## License

This project is open source and available for educational and commercial use.
