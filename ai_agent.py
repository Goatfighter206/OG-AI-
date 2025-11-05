"""
OG-AI Agent - A simple conversational AI agent
"""

import json
from typing import List, Dict, Optional
from datetime import datetime

# Voice chat is an optional feature
try:
    from voice_chat import VoiceChat, VoiceAssistant
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
                print("Run: pip install SpeechRecognition pyttsx3")
        
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
            List of conversation messages
        """
        return self.conversation_history
    
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


def main():
    """
    Example usage of the AI agent.
    """
    import sys
    
    # Check if user wants voice mode
    use_voice = '--voice' in sys.argv or '-v' in sys.argv
    
    if use_voice and not VOICE_AVAILABLE:
        print("Voice mode requested but voice_chat module is not available.")
        print("Install required dependencies: pip install SpeechRecognition pyttsx3")
        print("Falling back to text mode.\n")
        use_voice = False
    
    # Create an agent instance
    agent = AIAgent(name="OG-AI Master", enable_voice=use_voice)
    
    if use_voice and agent.voice_enabled:
        print("Starting in voice mode...")
        agent.voice_assistant.enable_voice_mode()
        agent.voice_assistant.interactive_session(use_voice_input=False)
    else:
        print(f"=== {agent.name} - AI Agent Demo ===")
        print("Type 'quit' to exit")
        if VOICE_AVAILABLE:
            print("Tip: Run with --voice or -v flag to enable voice mode\n")
        else:
            print()
        
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
    main()
