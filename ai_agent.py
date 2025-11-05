"""
OG-AI Agent - A simple conversational AI agent
"""

import json
from typing import List, Dict, Optional
from datetime import datetime


class AIAgent:
    """
    A basic AI agent that can process messages and maintain conversation context.
    """
    
    def __init__(self, name: str = "OG-AI", config: Optional[Dict] = None):
        """
        Initialize the AI agent.
        
        Args:
            name: The name of the agent
            config: Optional configuration dictionary
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
    main()
