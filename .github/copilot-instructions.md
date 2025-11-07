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
├── .gitignore          # Git ignore patterns
└── README.md           # Documentation
```

### Important Files
- **ai_agent.py**: Main application file containing AIAgent class and Flask API
- **config.json**: Configuration for agent behavior and settings
- **requirements.txt**: Python package dependencies
- **.gitignore**: Prevents committing generated files, virtual environments, and sensitive data

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

### Linting and Formatting
- No formal linter is currently configured for this project
- When adding linting tools (e.g., `pylint`, `flake8`, `black`):
  - Create a separate `requirements-dev.txt` file for development dependencies
  - Or use modern `pyproject.toml` with `[project.optional-dependencies]`
  - Document the commands to run them in this section
  - Configure them to follow PEP 8 standards

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

### Git Workflow
- **Branch naming**: Use descriptive names (e.g., `feature/add-auth`, `fix/conversation-bug`, `docs/update-readme`)
- **Commit messages**: Use clear, descriptive commit messages in present tense (e.g., "Add user authentication", "Fix conversation history bug")
- **Pull requests**: 
  - Keep PRs focused on a single feature or fix
  - Include a clear description of changes
  - Reference related issues using `#issue_number`
  - Ensure all tests pass before requesting review

## Important Notes

### Multi-Worker Considerations
When using multiple Gunicorn workers:
- Each worker maintains separate conversation state
- Use single worker (`--workers 1`) for consistent history
- Or implement session-based routing/shared storage

### Future Enhancements
The codebase is designed for extensibility:
- AI model integration ready (OpenAI GPT, Anthropic Claude)
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

### Issue Workflow
- **Before starting work:**
  - Ensure the issue is clearly defined with acceptance criteria
  - Comment on the issue to claim it or discuss approach
  - Create a feature branch from the main branch
- **Well-scoped issues should include:**
  - Clear objective and description
  - Specific files or modules to change
  - Expected behavior after the change
  - Test criteria
- **For complex issues:**
  - Break them down into smaller, manageable tasks
  - Create sub-issues if needed
  - Discuss approach before implementation

### Adding New Features
1. Update the AIAgent class methods if modifying core functionality
2. Add new API endpoints following existing patterns
3. Update README.md with new features/endpoints
4. Add examples to `example_usage.py` if appropriate
5. Ensure thread-safety for API endpoints
6. Update this copilot-instructions.md if adding new conventions

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
    except Exception as e:
        # Log the exception for debugging
        app.logger.error(f"Error in new_endpoint: {e}")
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

## Security Best Practices

### API Security
- Never commit API keys, tokens, or secrets to the repository
- Use environment variables for sensitive configuration
- When adding authentication:
  - Use established libraries (e.g., `Flask-Login`, `JWT`)
  - Implement rate limiting to prevent abuse
  - Add input validation to prevent injection attacks

### Dependency Security
- Regularly update dependencies to patch security vulnerabilities
- Use `pip list --outdated` to check for available updates
- Use security scanning tools (require separate installation):
  - `pip-audit` for vulnerability scanning
  - `safety` for security-specific checks
- Review security advisories for Flask and other dependencies
- When adding new dependencies, verify they are from trusted sources

### Data Handling
- Never log sensitive user data (passwords, tokens, personal information)
- Sanitize all user inputs before processing
- Use HTTPS in production deployments
- Implement proper error handling that doesn't expose system internals

## Troubleshooting

### Common Issues

**Flask app won't start:**
- Verify all dependencies are installed: `pip install -r requirements.txt`
- Check port 5000 is not already in use
- Ensure Python 3.7+ is being used

**Conversation history not persisting:**
- When using multiple Gunicorn workers, each worker has separate state
- Solution: Use `--workers 1` or implement shared storage (Redis, database)
- For development testing, use the built-in Flask server

**Import errors:**
- Make sure you're in the correct directory
- Verify virtual environment is activated (if using one)
- Re-install dependencies

**CLI mode not working:**
- Ensure you're using the `--cli` flag: `python ai_agent.py --cli`
- Check for any syntax errors in recent changes

## Constraints and Limitations

### What NOT to Do
- **Do not** remove or modify existing API endpoints without updating documentation
- **Do not** change the conversation history data structure without migration plan
- **Do not** add heavy ML dependencies without discussing performance impact
- **Do not** modify the thread-safety mechanisms without careful consideration
- **Do not** commit test conversation files:
  - Files like `conversation_example.json` (created during testing)
  - Files in the `conversations/` directory (already in .gitignore)
  - Python cache files (`.pyc`, `__pycache__/`)
- **Do not** make breaking changes to the AIAgent class public interface
- **Do not** disable CORS without understanding security implications

### Performance Considerations
- Keep conversation history bounded (use `max_history_length` config)
- Avoid blocking operations in API endpoints
- Consider async operations for future AI model integrations
- Monitor memory usage when handling multiple concurrent users

### Backward Compatibility
- Maintain compatibility with existing conversation JSON format
- Preserve existing API endpoint contracts
- Document any deprecations before removing functionality
- Provide migration guides for breaking changes
