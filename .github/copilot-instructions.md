# GitHub Copilot Instructions for OG-AI

## Repository Overview

OG-AI is a simple yet extensible conversational AI agent built with Python. It provides both a RESTful API with Flask and a CLI mode for interactive conversations.

## Project Structure

```
OG-AI-/
├── ai_agent.py           # Core AI agent implementation and Flask API
├── example_usage.py      # Example usage patterns
├── config.json          # Agent configuration
├── requirements.txt     # Python dependencies
├── render.yaml         # Render deployment configuration
└── README.md           # Documentation
```

## Key Components

### AIAgent Class (`ai_agent.py`)
- **Core functionality**: Conversation management and message processing
- **Key methods**:
  - `process_message(user_message: str) -> str`: Main message processing
  - `add_message(role: str, content: str)`: Add to conversation history
  - `get_conversation_history() -> List[Dict]`: Retrieve history
  - `clear_history()`: Reset conversation
  - `save_conversation(filepath: str)`: Persist to JSON
  - `load_conversation(filepath: str)`: Load from JSON

### Flask API
- **Endpoints**:
  - `GET /` - API information
  - `GET /health` - Health check
  - `POST /chat` - Send messages (requires JSON body with "message" field)
  - `GET /history` - Get conversation history
  - `POST /clear` - Clear history
- **Thread-safety**: Uses double-checked locking for agent initialization

## Development Guidelines

### Running the Application

**Web API Mode (Default)**:
```bash
python ai_agent.py
```

**Interactive CLI Mode**:
```bash
python ai_agent.py --cli
```

**Production (Gunicorn)**:
```bash
gunicorn --bind 0.0.0.0:5000 --workers 2 --timeout 120 ai_agent:app
```

### Testing

Run the example usage script to test functionality:
```bash
python example_usage.py
```

This demonstrates:
- Basic conversations
- Custom configuration usage
- Saving/loading conversations
- Conversation history access

### Dependencies

Install dependencies with:
```bash
pip install -r requirements.txt
```

**Core dependencies**:
- `Flask>=2.3.0` - Web framework
- `Flask-CORS>=4.0.0` - CORS support
- `gunicorn>=21.2.0` - Production WSGI server

### Configuration

Edit `config.json` to customize:
- `agent_name`: Name of the AI agent
- `system_prompt`: System prompt for future AI model integration
- `max_history_length`: Maximum conversation history length
- `save_conversations`: Enable/disable conversation saving
- `conversation_dir`: Directory for saved conversations

## Code Style and Conventions

### Python Style
- Follow PEP 8 guidelines
- Use type hints for function signatures
- Include docstrings for classes and methods (Google style)
- Keep methods focused and single-purpose

### Error Handling
- Use appropriate exception types (IOError, FileNotFoundError, etc.)
- Document exceptions in docstrings
- Return meaningful error messages in API responses

### API Responses
- Always return JSON
- Include meaningful error messages
- Use appropriate HTTP status codes:
  - 200 for success
  - 400 for bad requests (missing/invalid input)
  - 500 for server errors

## Important Notes

### Multi-Worker Considerations
When using multiple Gunicorn workers:
- Each worker maintains separate conversation state
- Use single worker (`--workers 1`) for consistent history
- Or implement session-based routing/shared storage

### Future Enhancements
The codebase is designed for extensibility:
- AI model integration ready (OpenAI, Anthropic)
- System prompts can be passed to LLM APIs
- Simple response logic can be replaced with actual AI models

## Deployment

### Render Deployment
- Uses `render.yaml` for automatic configuration
- Alternatively, manual setup with Python 3 environment
- Start command: `python ai_agent.py`
- Build command: `pip install -r requirements.txt`

### Environment Variables
- `PORT`: Set server port (default: 5000)

## When Making Changes

### Adding New Features
1. Update the AIAgent class methods if modifying core functionality
2. Add new API endpoints following existing patterns
3. Update README.md with new features/endpoints
4. Add examples to `example_usage.py` if appropriate
5. Ensure thread-safety for API endpoints

### Modifying Existing Code
1. Maintain backward compatibility
2. Update docstrings and type hints
3. Test both CLI and API modes
4. Verify error handling and edge cases

### Testing Changes
- Test CLI mode: `python ai_agent.py --cli`
- Test API mode: Start server and use curl/requests
- Run example script: `python example_usage.py`
- Test with multiple workers if relevant

## Common Tasks

### Add a new API endpoint
```python
@app.route('/new-endpoint', methods=['GET'])
def new_endpoint():
    """Endpoint description."""
    try:
        agent = get_agent()
        # Your logic here
        return jsonify({'result': 'success'})
    except Exception:
        return jsonify({'error': 'Error message'}), 500
```

### Add new agent method
```python
def new_method(self, param: str) -> str:
    """
    Method description.
    
    Args:
        param: Parameter description
        
    Returns:
        Return value description
    """
    # Implementation
    return result
```

### Extend response logic
Modify `_generate_response()` method in the AIAgent class to add new pattern matching or integrate AI models.
