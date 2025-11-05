# OG-AI - AI Agent

A simple yet extensible conversational AI agent built with Python. This agent can maintain conversation context, save/load conversation history, and provide interactive responses.

## Features

- 🤖 Basic conversational AI capabilities
- 💬 Conversation history management
- 💾 Save and load conversations to/from JSON
- ⚙️ Configurable agent settings
- 🔧 Extensible architecture for adding AI models
- 🐍 Pure Python implementation (no external dependencies required)

## Installation

1. Clone this repository:
```bash
git clone https://github.com/Goatfighter206/OG-AI-.git
cd OG-AI-
```

2. (Optional) Install dependencies if you plan to extend with AI APIs:
```bash
pip install -r requirements.txt
```

## Usage

### Interactive Mode

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

## Future Enhancements

- Integration with OpenAI GPT models
- Integration with Anthropic Claude
- Support for multiple conversation threads
- Advanced context management
- Plugin system for extending capabilities
- Web interface
- Voice interaction support

## Contributing

Contributions are welcome! Feel free to submit issues and pull requests.

## License

This project is open source and available for educational and commercial use.
