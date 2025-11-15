"""
Simple Python Client for OG-AI Agent API
Use this to interact with your deployed AI agent from Python
"""

import requests
from typing import Dict, List, Optional


class OGAIClient:
    """Client for interacting with the OG-AI Agent API"""

    def __init__(self, base_url: str = "https://og-ai.onrender.com"):
        """
        Initialize the client

        Args:
            base_url: Base URL of the API (default: production deployment)
        """
        self.base_url = base_url.rstrip('/')

    def health_check(self) -> Dict:
        """
        Check if the service is running

        Returns:
            dict: Status information
        """
        response = requests.get(f"{self.base_url}/health")
        response.raise_for_status()
        return response.json()

    def chat(self, message: str) -> Dict:
        """
        Send a message to the AI agent

        Args:
            message: Your message to the AI

        Returns:
            dict: AI response with 'response', 'agent_name', and 'timestamp'
        """
        if not message or not message.strip():
            raise ValueError("Message cannot be empty")

        response = requests.post(
            f"{self.base_url}/chat",
            json={"message": message}
        )
        response.raise_for_status()
        return response.json()

    def get_history(self) -> Dict:
        """
        Get the conversation history

        Returns:
            dict: Conversation history with 'conversation', 'history', and 'message_count'
        """
        response = requests.get(f"{self.base_url}/history")
        response.raise_for_status()
        return response.json()

    def reset(self) -> Dict:
        """
        Reset the conversation history

        Returns:
            dict: Confirmation message
        """
        response = requests.post(f"{self.base_url}/reset")
        response.raise_for_status()
        return response.json()

    def chat_interactive(self):
        """
        Start an interactive chat session in the terminal
        """
        print("=" * 60)
        print("OG-AI Interactive Chat")
        print("=" * 60)
        print("Type your messages and press Enter.")
        print("Commands: 'history', 'reset', 'quit'")
        print("=" * 60)
        print()

        while True:
            try:
                user_input = input("You: ").strip()

                if not user_input:
                    continue

                if user_input.lower() in ['quit', 'exit', 'q']:
                    print("Goodbye!")
                    break

                if user_input.lower() == 'history':
                    history = self.get_history()
                    print(f"\nConversation History ({history['message_count']} messages):")
                    for msg in history['conversation']:
                        role = msg['role'].capitalize()
                        content = msg['content']
                        print(f"  [{role}] {content}")
                    print()
                    continue

                if user_input.lower() == 'reset':
                    result = self.reset()
                    print(f"AI: {result['message']}\n")
                    continue

                # Send message to AI
                response = self.chat(user_input)
                print(f"AI: {response['response']}\n")

            except KeyboardInterrupt:
                print("\nGoodbye!")
                break
            except Exception as e:
                print(f"Error: {e}\n")


def main():
    """Example usage of the OG-AI Client"""

    # Initialize client
    client = OGAIClient()

    print("Testing OG-AI Agent API...")
    print()

    # 1. Health check
    print("1. Health Check:")
    health = client.health_check()
    print(f"   Status: {health['status']}")
    print(f"   Agent: {health['agent_name']}")
    print()

    # 2. Send a message
    print("2. Sending Message:")
    response = client.chat("Hello! What can you do?")
    print(f"   Agent: {response['response']}")
    print()

    # 3. Get history
    print("3. Getting History:")
    history = client.get_history()
    print(f"   Messages: {history['message_count']}")
    print()

    # 4. Interactive chat (optional)
    print("4. Starting Interactive Chat...")
    print()
    client.chat_interactive()


if __name__ == "__main__":
    main()

