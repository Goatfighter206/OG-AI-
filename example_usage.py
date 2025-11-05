"""
Example usage of the OG-AI Agent
"""

import json
from ai_agent import AIAgent


def example_basic_conversation():
    """
    Demonstrate basic conversation with the AI agent.
    """
    print("=== Example 1: Basic Conversation ===\n")
    
    agent = AIAgent(name="OG-AI")
    
    # Simulate a conversation
    messages = [
        "Hello!",
        "What is your name?",
        "How are you?",
        "Can you help me?",
        "Goodbye!"
    ]
    
    for msg in messages:
        print(f"User: {msg}")
        response = agent.process_message(msg)
        print(f"Agent: {response}\n")


def example_with_config():
    """
    Demonstrate using the agent with custom configuration.
    """
    print("\n=== Example 2: Agent with Custom Config ===\n")
    
    # Load config from file
    with open('config.json', 'r') as f:
        config = json.load(f)
    
    agent = AIAgent(name=config['agent_name'], config=config)
    
    messages = ["Hi there!", "What can you do?"]
    
    for msg in messages:
        print(f"User: {msg}")
        response = agent.process_message(msg)
        print(f"Agent: {response}\n")


def example_save_and_load():
    """
    Demonstrate saving and loading conversation history.
    """
    print("\n=== Example 3: Save and Load Conversation ===\n")
    
    # Create agent and have a conversation
    agent = AIAgent(name="OG-AI")
    agent.process_message("Hello!")
    agent.process_message("Remember this: My favorite color is blue")
    
    # Save the conversation
    agent.save_conversation('./conversation_example.json')
    print("Conversation saved to conversation_example.json")
    
    # Create a new agent and load the conversation
    new_agent = AIAgent(name="OG-AI")
    new_agent.load_conversation('./conversation_example.json')
    
    print(f"\nLoaded {len(new_agent.get_conversation_history())} messages from file")
    print("\nConversation history:")
    for msg in new_agent.get_conversation_history():
        print(f"  [{msg['role']}]: {msg['content']}")


def example_conversation_history():
    """
    Demonstrate accessing conversation history.
    """
    print("\n=== Example 4: Conversation History ===\n")
    
    agent = AIAgent(name="OG-AI")
    
    # Have a short conversation
    agent.process_message("Hi")
    agent.process_message("What's your name?")
    
    # Get and display history
    history = agent.get_conversation_history()
    print(f"Total messages in history: {len(history)}\n")
    
    for i, msg in enumerate(history, 1):
        print(f"Message {i}:")
        print(f"  Role: {msg['role']}")
        print(f"  Content: {msg['content']}")
        print(f"  Timestamp: {msg['timestamp']}\n")


if __name__ == "__main__":
    # Run all examples
    example_basic_conversation()
    example_with_config()
    example_save_and_load()
    example_conversation_history()
    
    print("\n=== All Examples Complete ===")
