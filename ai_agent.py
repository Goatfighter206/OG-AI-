"""
OG-AI Agent - A simple conversational AI agent
"""

import json
import os
import threading
from typing import List, Dict, Optional
from datetime import datetime
from flask import Flask, request, jsonify
from flask_cors import CORS

# Voice chat is an optional feature
try:
    from voice_chat import VoiceAssistant
    VOICE_AVAILABLE = True
except ImportError:
    VOICE_AVAILABLE = False


class AIAgent:
    """
    A basic AI agent that can process messages and maintain conversation context.
    """
    
    def __init__(self, name: str = "OG-AI", config: Optional[Dict] = None, enable_voice: bool = False):
        """
        Initialize the AI agent.
        
        Args:
            name: The name of the agent
            config: Optional configuration dictionary
            enable_voice: Enable voice chat capabilities (requires voice_chat module)
        """
        self.name = name
        self.config = config or {}
        self.conversation_history: List[Dict] = []
        # System prompt is available for future AI model integration
        # Currently used for configuration but can be passed to LLM APIs
        self.system_prompt = self.config.get(
            'system_prompt', 
            'You are a helpful AI assistant.'
        )
        
        # Initialize voice capabilities if requested and available
        self.voice_enabled = False
        self.voice_assistant = None
        if enable_voice:
            if VOICE_AVAILABLE:
                self.voice_assistant = VoiceAssistant(self)
                self.voice_enabled = True
            else:
                print("Warning: Voice chat module not available. Install required dependencies.")
                print("Run: pip install SpeechRecognition pyttsx3 PyAudio")
        
    def add_message(self, role: str, content: str) -> None:
        """
        Add a message to the conversation history.
        
        Args:
            role: The role of the message sender (user, assistant, system)
            content: The content of the message
        """
        message = {
            'role': role,
            'content': content,
            'timestamp': datetime.now().isoformat()
        }
        self.conversation_history.append(message)
        
    def process_message(self, user_message: str) -> str:
        """
        Process a user message and generate a response.
        
        Args:
            user_message: The message from the user
            
        Returns:
            The agent's response
        """
        # Add user message to history
        self.add_message('user', user_message)
        
        # Simple response logic (can be enhanced with actual AI/ML models)
        response = self._generate_response(user_message)
        
        # Add assistant response to history
        self.add_message('assistant', response)
        
        return response
    
    def _generate_response(self, message: str) -> str:
        """
        Generate a response based on the user message.
        This is a simple implementation that can be extended with actual AI models.
        
        Args:
            message: The user's message
            
        Returns:
            Generated response
        """
        message_lower = message.lower()
        
        # Simple pattern matching responses
        if 'hello' in message_lower or 'hi' in message_lower:
            return f"Hello! I'm {self.name}, your AI assistant. How can I help you today?"
        elif 'help' in message_lower:
            return "I'm here to assist you! You can ask me questions or have a conversation with me."
        elif 'name' in message_lower:
            return f"My name is {self.name}."
        elif 'how are you' in message_lower:
            return "I'm functioning well, thank you for asking! How can I assist you?"
        elif 'bye' in message_lower or 'goodbye' in message_lower:
            return "Goodbye! Feel free to come back anytime you need assistance."
        else:
            return f"I understand you said: '{message}'. I'm a basic AI agent and can respond to greetings and simple queries. How else can I help?"
    
    def get_conversation_history(self) -> List[Dict]:
        """
        Get the full conversation history.
        
        Returns:
            List of conversation messages (copy to prevent external modification)
        """
        return self.conversation_history.copy()
    
    def clear_history(self) -> None:
        """
        Clear the conversation history.
        """
        self.conversation_history = []
        
    def save_conversation(self, filepath: str) -> None:
        """
        Save the conversation history to a JSON file.
        
        Args:
            filepath: Path to save the conversation
            
        Raises:
            IOError: If the file cannot be written
            PermissionError: If insufficient permissions to write file
        """
        try:
            with open(filepath, 'w') as f:
                json.dump({
                    'agent_name': self.name,
                    'conversation': self.conversation_history
                }, f, indent=2)
        except (IOError, PermissionError) as e:
            raise IOError(f"Failed to save conversation to {filepath}: {e}")
    
    def load_conversation(self, filepath: str) -> None:
        """
        Load conversation history from a JSON file.
        
        Args:
            filepath: Path to load the conversation from
            
        Raises:
            FileNotFoundError: If the file does not exist
            json.JSONDecodeError: If the file contains invalid JSON
            IOError: If the file cannot be read
        """
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
                self.conversation_history = data.get('conversation', [])
        except FileNotFoundError:
            raise FileNotFoundError(f"Conversation file not found: {filepath}")
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(f"Invalid JSON in conversation file: {filepath}", e.doc, e.pos)


# Flask app setup for API
app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Global agent instance for the API
api_agent = None


# Lock for thread-safe agent initialization
_agent_lock = threading.Lock()


def get_agent():
    """
    Get or initialize the global API agent.
    Thread-safe lazy initialization using double-checked locking.
    
    Returns:
        The initialized AIAgent instance
        
    Raises:
        RuntimeError: If agent initialization fails
    """
    global api_agent
    if api_agent is None:
        with _agent_lock:
            # Double-check pattern to prevent race conditions
            if api_agent is None:
                try:
                    initialize_agent()
                except Exception as e:
                    raise RuntimeError(f"Failed to initialize AI agent: {e}") from e
    return api_agent


def load_config():
    """
    Load configuration from config.json file.
    
    Returns:
        Dictionary containing configuration settings
    """
    config_path = os.path.join(os.path.dirname(__file__), 'config.json')
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def initialize_agent():
    """
    Initialize the global API agent with configuration.
    """
    global api_agent
    config = load_config()
    agent_name = config.get('agent_name', 'OG-AI')
    api_agent = AIAgent(name=agent_name, config=config)


@app.route('/', methods=['GET'])
def home():
    """
    Home endpoint providing API information.
    """
    return jsonify({
        'name': 'OG-AI API',
        'version': '1.0.0',
        'status': 'running',
        'endpoints': {
            '/': 'GET - API information',
            '/health': 'GET - Health check',
            '/chat': 'POST - Send a message to the AI agent',
            '/history': 'GET - Get conversation history',
            '/clear': 'POST - Clear conversation history'
        }
    })


@app.route('/health', methods=['GET'])
def health():
    """
    Health check endpoint.
    """
    agent = get_agent()
    return jsonify({
        'status': 'healthy',
        'agent_name': agent.name
    })


@app.route('/chat', methods=['POST'])
def chat():
    """
    Process a chat message from the user.
    
    Expected JSON body:
    {
        "message": "user message here"
    }
    
    Returns:
        JSON response with agent's reply
    """
    try:
        agent = get_agent()
        
        data = request.get_json()
        
        if not data or 'message' not in data:
            return jsonify({
                'error': 'Missing "message" field in request body'
            }), 400
        
        user_message = data['message']
        
        if not user_message or not user_message.strip():
            return jsonify({
                'error': 'Message cannot be empty'
            }), 400
        
        # Process the message
        response = agent.process_message(user_message)
        
        return jsonify({
            'message': user_message,
            'response': response,
            'agent_name': agent.name
        })
    
    except Exception:
        return jsonify({
            'error': 'Error processing message'
        }), 500


@app.route('/history', methods=['GET'])
def get_history():
    """
    Get the conversation history.
    
    Returns:
        JSON response with conversation history
    """
    try:
        agent = get_agent()
        history = agent.get_conversation_history()
        return jsonify({
            'history': history,
            'message_count': len(history)
        })
    except Exception:
        return jsonify({
            'error': 'Error retrieving history'
        }), 500


@app.route('/clear', methods=['POST'])
def clear_history():
    """
    Clear the conversation history.
    
    Returns:
        JSON response confirming the history was cleared
    """
    try:
        agent = get_agent()
        agent.clear_history()
        return jsonify({
            'message': 'Conversation history cleared successfully'
        })
    except Exception:
        return jsonify({
            'error': 'Error clearing history'
        }), 500


def main():
    """
    Example usage of the AI agent.
    """
    # Create an agent instance
    agent = AIAgent(name="OG-AI Master")
    
    print(f"=== {agent.name} - AI Agent Demo ===")
    print("Type 'quit' to exit\n")
    
    while True:
        user_input = input("You: ").strip()
        
        if user_input.lower() == 'quit':
            print("Exiting...")
            break
            
        if not user_input:
            continue
            
        response = agent.process_message(user_input)
        print(f"{agent.name}: {response}\n")


if __name__ == "__main__":
    # Check if we should run as API or CLI
    import sys
    
    if '--cli' in sys.argv:
        # Run in CLI mode
        main()
    else:
        # Run as Flask API (development server only)
        # For production, use: gunicorn ai_agent:app
        # Agent will be initialized lazily on first request per worker via get_agent()
        port = int(os.environ.get('PORT', 5000))
        app.run(host='0.0.0.0', port=port, debug=False)
