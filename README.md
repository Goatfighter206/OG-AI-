# OG-AI - AI Agent

A simple yet extensible conversational AI agent built with Python. This agent can maintain conversation context, save/load conversation history, and provide interactive responses.

## Features

- 🤖 Basic conversational AI capabilities
- 💬 Conversation history management
- 💾 Save and load conversations to/from JSON
- ⚙️ Configurable agent settings
- 🔧 Extensible architecture for adding AI models
- 🎤 **Voice chat support** - Voice input and output capabilities
- 🔊 Text-to-speech for AI responses
- 🎙️ Speech-to-text for user input
- 🐍 Pure Python implementation (minimal dependencies)

## Installation

1. Clone this repository:
```bash
git clone https://github.com/Goatfighter206/OG-AI-.git
cd OG-AI-
```

2. Install dependencies for voice chat (optional but recommended):
```bash
pip install -r requirements.txt
```

**Note:** Voice chat requires:
- `SpeechRecognition` - For voice input (speech-to-text)
- `pyttsx3` - For voice output (text-to-speech)
- A microphone for voice input
- Audio output device for voice responses

The agent works fine without these dependencies in text-only mode.

## Usage

### Interactive Mode

Run the agent in interactive mode for a conversation:

```bash
# Text mode (default)
python ai_agent.py

# Voice mode (with voice output)
python ai_agent.py --voice
```

Type your messages and the agent will respond. Type `quit` to exit.

### Voice Chat Mode

For full voice interaction capabilities:

```bash
python voice_chat.py
```

This starts an interactive session where you can:
- Use voice input (speak to the agent)
- Use text input (type messages)
- Toggle between voice and text input modes
- Enable/disable voice output
- All agent responses can be spoken aloud

### Programmatic Usage

#### Basic Text Mode

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

#### With Voice Capabilities

```python
from ai_agent import AIAgent
from voice_chat import VoiceAssistant

# Create agent with voice support
agent = AIAgent(name="OG-AI", enable_voice=True)

# Enable voice output
if agent.voice_enabled:
    agent.voice_assistant.enable_voice_mode()
    
    # Process text with voice output
    response = agent.voice_assistant.process_text_with_voice("Hello!")
    
    # Or use voice input
    response = agent.voice_assistant.process_voice_input()
```

#### Voice Chat Only

```python
from ai_agent import AIAgent
from voice_chat import VoiceChat, VoiceAssistant

# Create components
agent = AIAgent(name="OG-AI")
voice_chat = VoiceChat(rate=150, volume=0.9)
voice_assistant = VoiceAssistant(agent, voice_chat)

# Use voice features
voice_assistant.enable_voice_mode()
voice_assistant.interactive_session(use_voice_input=False)
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
AIAgent(name: str = "OG-AI", config: Optional[Dict] = None, enable_voice: bool = False)
```

#### Methods

- `process_message(user_message: str) -> str`: Process a user message and return response
- `add_message(role: str, content: str)`: Add a message to conversation history
- `get_conversation_history() -> List[Dict]`: Get all conversation messages
- `clear_history()`: Clear the conversation history
- `save_conversation(filepath: str)`: Save conversation to JSON file
- `load_conversation(filepath: str)`: Load conversation from JSON file

### VoiceChat Class

Handles voice input and output capabilities.

#### Constructor
```python
VoiceChat(rate: int = 150, volume: float = 0.9, voice_id: Optional[int] = None)
```

#### Methods

- `listen(timeout: int = 5, phrase_time_limit: int = 10) -> Optional[str]`: Listen for voice input and convert to text
- `speak(text: str, async_mode: bool = False)`: Convert text to speech and speak it
- `set_voice_properties(rate, volume, voice_id)`: Update voice settings
- `list_available_voices() -> list`: Get available TTS voices
- `get_current_settings() -> dict`: Get current voice settings

### VoiceAssistant Class

High-level interface combining AIAgent with voice capabilities.

#### Constructor
```python
VoiceAssistant(agent, voice_chat: Optional[VoiceChat] = None)
```

#### Methods

- `enable_voice_mode()`: Enable voice output for responses
- `disable_voice_mode()`: Disable voice output
- `toggle_voice_mode() -> bool`: Toggle voice mode on/off
- `process_voice_input(timeout: int = 5) -> Optional[str]`: Listen and process voice input
- `process_text_with_voice(text: str) -> str`: Process text and speak response
- `interactive_session(use_voice_input: bool = True)`: Start interactive voice session

## Voice Chat Commands

When running in voice mode, you can use these commands:

- **"quit"** - Exit the application
- **"toggle voice"** - Enable/disable voice output
- **"switch input"** - Toggle between voice and text input modes

## Troubleshooting Voice Chat

### Microphone Not Working
- Ensure your microphone is connected and permissions are granted
- Check system audio settings
- Try `python -m speech_recognition` to test microphone setup

### Text-to-Speech Issues
- pyttsx3 uses system TTS engines (espeak on Linux, SAPI5 on Windows, nsss on macOS)
- Ensure system TTS is properly installed
- Try adjusting rate and volume settings

### Dependencies Installation Issues
If you encounter issues installing PyAudio:
- **Windows**: `pip install pipwin && pipwin install pyaudio`
- **macOS**: `brew install portaudio && pip install pyaudio`
- **Linux**: `sudo apt-get install python3-pyaudio` or `pip install pyaudio`

## Future Enhancements

- Integration with OpenAI GPT models
- Integration with Anthropic Claude
- Support for multiple conversation threads
- Advanced context management
- Plugin system for extending capabilities
- Web interface
- Enhanced voice recognition with custom wake words
- Multi-language support for voice chat

## Contributing

Contributions are welcome! Feel free to submit issues and pull requests.

## License

This project is open source and available for educational and commercial use.
