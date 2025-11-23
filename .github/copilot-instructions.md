# GitHub Copilot Instructions for OG-AI

## Repository Overview

OG-AI is a simple yet extensible conversational AI agent built with Python. It provides a RESTful API with FastAPI for web deployment and interactive example scripts for local usage.

## Project Structure

```
OG-AI-/
├── ai_agent.py           # Core AI agent implementation (AIAgent class)
├── app.py               # FastAPI web service with REST endpoints
├── example_usage.py      # Example usage patterns
├── config.json          # Agent configuration
├── requirements.txt     # Python dependencies
├── pyproject.toml       # Project metadata and build configuration
├── render.yaml         # Render deployment configuration
├── Procfile            # Heroku deployment configuration
├── test_app.py         # Comprehensive test suite for FastAPI endpoints
├── test_ai_agent.py    # Unit tests for AIAgent class
├── .gitignore          # Git ignore patterns
└── README.md           # Documentation
```

### Important Files
- **ai_agent.py**: Core AIAgent class with conversation management
- **app.py**: FastAPI application with REST API endpoints
- **config.json**: Configuration for agent behavior and settings
- **requirements.txt**: Python package dependencies
- **pyproject.toml**: Package metadata, dependencies, and tool configurations
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

### FastAPI Application (`app.py`)
- **Framework**: FastAPI with uvicorn ASGI server
- **Endpoints**:
  - `GET /` - API information and service status
  - `GET /health` - Health check endpoint
  - `POST /chat` - Send messages (requires JSON body with "message" field)
  - `GET /history` - Get conversation history
  - `POST /reset` - Clear conversation history
  - `POST /clear` - Clear history (alias for /reset, backward compatibility)
- **Features**:
  - Automatic OpenAPI/Swagger documentation at `/docs`
  - ReDoc documentation at `/redoc`
  - CORS support for cross-origin requests
  - Request/response validation with Pydantic models
  - Global agent instance with lazy initialization
- **Thread-safety**: Uses global agent instance (suitable for single-worker deployments)

## Development Guidelines

### Running the Application

**Web API Mode**:
```bash
python app.py
```
Server starts on `http://0.0.0.0:8000` (or PORT environment variable)

**Interactive Example**:
```bash
python example_usage.py
```

**Production (Uvicorn)**:
```bash
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1
```

**Important**: Use `--workers 1` for consistent conversation state, or implement session-based storage for multi-worker setups.

### Testing

Run the comprehensive test suite:
```bash
pytest
```

Run with coverage report:
```bash
pytest --cov=. --cov-report=html --cov-report=term
```

Run specific test file:
```bash
pytest test_app.py -v
```

This test suite covers:
- All API endpoints (root, health, chat, history, reset/clear)
- Request validation and error handling
- Edge cases and input validation
- Conversation state management
- Configuration loading

### Dependencies

Install dependencies with:
```bash
pip install -r requirements.txt
```

**Core dependencies**:
- `fastapi>=0.109.1` - Modern async web framework
- `uvicorn[standard]>=0.24.0` - ASGI server with WebSocket support
- `pydantic>=2.0.0` - Data validation using Python type hints
- `requests>=2.28.0` - HTTP client library

**Testing dependencies**:
- `pytest>=7.4.0` - Testing framework
- `pytest-asyncio>=0.21.0` - Async test support
- `pytest-cov>=4.0.0` - Coverage reporting
- `httpx>=0.24.0` - Async HTTP client for testing FastAPI

### Configuration

Edit `config.json` to customize:
- `agent_name`: Name of the AI agent
- `system_prompt`: System prompt for future AI model integration
- `max_history_length`: Maximum conversation history length (default: 100)
- `save_conversations`: Enable/disable automatic conversation saving
- `conversation_dir`: Directory for saved conversations

## Code Style and Conventions

### Python Style
- Follow PEP 8 guidelines
- Use type hints for function signatures
- Include docstrings for classes and methods (Google style)
- Keep methods focused and single-purpose

### Linting and Formatting
- Configuration available in `pyproject.toml`:
  - **black**: Code formatter (line length: 120)
  - **isort**: Import sorting (compatible with black)
- To set up development tools:
  ```bash
  pip install black isort mypy
  ```
- Format code:
  ```bash
  black .
  isort .
  ```
- Type checking:
  ```bash
  mypy ai_agent.py app.py
  ```

### Error Handling
- Use appropriate exception types (ValueError, FileNotFoundError, etc.)
- Document exceptions in docstrings
- Return meaningful error messages in API responses
- Use HTTPException for API errors with appropriate status codes

### API Responses
- All endpoints return JSON (FastAPI automatically handles serialization)
- Use Pydantic models for request/response validation
- Include meaningful error messages
- Use appropriate HTTP status codes:
  - 200 for success
  - 400 for bad requests (missing/invalid input)
  - 500 for server errors

### FastAPI Best Practices
- Define Pydantic models with `model_config` for examples
- Use async/await for I/O-bound operations (future enhancement)
- Leverage dependency injection for shared resources
- Document endpoints with docstrings (appears in OpenAPI docs)
- Use FastAPI's built-in validation (no manual checks needed)

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
When deploying with multiple workers:
- Each worker maintains separate conversation state by default
- For consistent history across workers:
  - Use single worker (`--workers 1`)
  - Implement session-based routing
  - Use shared storage (Redis, database)
  - Consider WebSocket connections for real-time chat

### Future Enhancements
The codebase is designed for extensibility:
- AI model integration ready (OpenAI GPT, Anthropic Claude)
- System prompts can be passed to LLM APIs
- Simple response logic in `_generate_response()` can be replaced with actual AI models
- Async endpoints ready for non-blocking I/O operations

## Deployment

### Render Deployment
- Uses `render.yaml` for configuration
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`
- Health check path: `/health`
- Free tier compatible

### Heroku Deployment
- Uses `Procfile` for process configuration
- Process type: `web: uvicorn app:app --host 0.0.0.0 --port $PORT`
- Automatic Python version detection via `runtime.txt`

### Environment Variables
- `PORT`: Server port (default: 8000)
- `DEVELOPMENT_MODE`: Set to "true" for detailed error messages
- `ALLOWED_ORIGINS`: JSON array of allowed CORS origins (default: ["*"])
- `OG_AI_CONFIG`: Path to config file (default: config.json)

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
2. Add new FastAPI endpoints following existing patterns in `app.py`
3. Create Pydantic models for request/response validation
4. Update README.md with new features/endpoints
5. Add examples to `example_usage.py` if appropriate
6. Write comprehensive tests in `test_app.py` or `test_ai_agent.py`
7. Update API documentation (docstrings appear in `/docs`)
8. Update this copilot-instructions.md if adding new conventions

### Modifying Existing Code
1. Maintain backward compatibility when possible
2. Update docstrings and type hints
3. Test all endpoints after changes
4. Verify error handling and edge cases
5. Run the full test suite: `pytest`
6. Check code coverage: `pytest --cov`

### Testing Changes
- Run tests: `pytest`
- Run with verbose output: `pytest -v`
- Test specific file: `pytest test_app.py -v`
- Check coverage: `pytest --cov=. --cov-report=term`
- Manual API testing: Start server and visit `http://localhost:8000/docs`
- Test example script: `python example_usage.py`

## Common Tasks

### Add a new FastAPI endpoint
```python
@app.post("/new-endpoint", response_model=NewResponse)
async def new_endpoint(request: NewRequest):
    """
    Endpoint description that appears in OpenAPI docs.
    
    Args:
        request: NewRequest Pydantic model
        
    Returns:
        NewResponse with result data
    """
    try:
        agent = get_agent()
        # Your logic here
        return {"result": "success"}
    except Exception as e:
        logger.error(f"Error in new_endpoint: {e}")
        detail = str(e) if DEVELOPMENT_MODE else "An error occurred"
        raise HTTPException(status_code=500, detail=detail)
```

### Add new Pydantic model
```python
class NewRequest(BaseModel):
    field: str
    optional_field: Optional[int] = None
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "field": "example value",
                "optional_field": 42
            }
        }
    )
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

### Add a new test
```python
class TestNewFeature:
    """Test suite for new feature."""
    
    @pytest.mark.usefixtures("reset_agent")
    def test_new_feature_success(self):
        """Test new feature with valid input."""
        response = client.post("/new-endpoint", json={"field": "value"})
        assert response.status_code == 200
        data = response.json()
        assert data["result"] == "success"
```

## Security Best Practices

### API Security
- Never commit API keys, tokens, or secrets to the repository
- Use environment variables for sensitive configuration
- When adding authentication:
  - Use established libraries (e.g., `python-jose[cryptography]` for JWT)
  - Implement rate limiting to prevent abuse (use `slowapi`)
  - Add input validation (Pydantic handles this automatically)
- Configure CORS appropriately:
  - Use specific origins in production (set `ALLOWED_ORIGINS`)
  - Never use `["*"]` with credentials enabled

### Dependency Security
- Regularly update dependencies to patch security vulnerabilities
- Use `pip list --outdated` to check for available updates
- Security scanning tools (require separate installation):
  - `pip-audit` for vulnerability scanning
  - `safety` for security-specific checks
- Review security advisories for FastAPI and other dependencies
- When adding new dependencies, verify they are from trusted sources

### Data Handling
- Never log sensitive user data (passwords, tokens, personal information)
- Sanitize all user inputs (Pydantic validation helps)
- Use HTTPS in production deployments
- Implement proper error handling that doesn't expose system internals
- Set `DEVELOPMENT_MODE=false` in production

## Troubleshooting

### Common Issues

**FastAPI app won't start:**
- Verify all dependencies are installed: `pip install -r requirements.txt`
- Check port 8000 is not already in use (or set PORT environment variable)
- Ensure Python 3.12+ is being used
- Check for syntax errors: `python -m py_compile app.py`

**Conversation history not persisting:**
- When using multiple uvicorn workers, each worker has separate state
- Solution: Use `--workers 1` or implement shared storage (Redis, database)
- For development testing, use the default single-worker mode

**Import errors:**
- Make sure you're in the correct directory
- Verify virtual environment is activated (if using one)
- Re-install dependencies: `pip install -r requirements.txt`
- Check for circular imports

**Tests failing:**
- Ensure all test dependencies are installed
- Reset agent state between tests (use `reset_agent` fixture)
- Check for port conflicts if running server during tests
- View detailed output: `pytest -v -s`

**Coverage missing pytest-cov:**
- Install it: `pip install pytest-cov`
- Or install all test dependencies: `pip install -e ".[dev]"`

## Constraints and Limitations

### What NOT to Do
- **Do not** remove or modify existing API endpoints without updating documentation
- **Do not** change the conversation history data structure without migration plan
- **Do not** add heavy ML dependencies without discussing performance impact
- **Do not** commit test conversation files:
  - Files like `conversation_example.json` (created during testing)
  - Files in the `conversations/` directory (already in .gitignore)
  - Python cache files (`.pyc`, `__pycache__/`)
- **Do not** make breaking changes to the AIAgent class public interface
- **Do not** disable CORS without understanding security implications
- **Do not** use blocking I/O operations in FastAPI endpoints (use async)
- **Do not** catch all exceptions without logging (defeats error diagnosis)

### Performance Considerations
- Keep conversation history bounded (use `max_history_length` config)
- Consider async operations for I/O-bound tasks
- Use connection pooling for future database integrations
- Monitor memory usage when handling multiple concurrent users
- Profile code before optimizing (use `cProfile` or `line_profiler`)

### Backward Compatibility
- Maintain compatibility with existing conversation JSON format
- Preserve existing API endpoint contracts
- Add new fields as optional in Pydantic models
- Document any deprecations before removing functionality
- Provide migration guides for breaking changes
- Keep `/clear` endpoint as alias for `/reset` (backward compatibility)

## Testing Strategy

### Unit Tests
- Test individual methods in isolation
- Mock external dependencies
- Focus on edge cases and error conditions
- File: `test_ai_agent.py`

### Integration Tests
- Test API endpoints end-to-end
- Use FastAPI TestClient
- Verify request/response formats
- Test error handling paths
- File: `test_app.py`

### Test Coverage Goals
- Aim for >80% code coverage
- Critical paths should have 100% coverage
- Focus on error handling paths
- Document intentionally untested code

### Continuous Testing
- Run tests before committing: `pytest`
- Use pre-commit hooks for automatic testing
- GitHub Actions for CI/CD (if configured)
- Monitor test execution time

