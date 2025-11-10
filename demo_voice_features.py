#!/usr/bin/env python3
"""
Comprehensive demo of voice chat features for OG-AI Agent.
This script demonstrates all voice capabilities in action.
"""

import sys
import time
from ai_agent import AIAgent, VOICE_AVAILABLE

def print_section(title):
    """Print a formatted section header."""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70 + "\n")

def demo_text_mode():
    """Demonstrate basic text mode (no voice)."""
    print_section("Demo 1: Text Mode (Default)")
    
    agent = AIAgent(name="OG-AI Text")
    
    print("Creating AI agent in text-only mode...")
    print(f"Agent name: {agent.name}")
    print(f"Voice enabled: {agent.voice_enabled}")
    
    # Have a conversation
    messages = ["Hello!", "What's your name?", "How are you?"]
    
    for msg in messages:
        print(f"\n👤 User: {msg}")
        response = agent.process_message(msg)
        print(f"🤖 {agent.name}: {response}")
    
    print("\n✓ Text mode demo complete")

def demo_voice_mode():
    """Demonstrate voice mode with text input."""
    print_section("Demo 2: Voice Mode (Text Input, Voice Output)")
    
    if not VOICE_AVAILABLE:
        print("⚠️  Voice chat module not available.")
        print("Install dependencies: pip install SpeechRecognition pyttsx3")
        return
    
    from voice_chat import VoiceAssistant
    
    agent = AIAgent(name="OG-AI Voice", enable_voice=True)
    
    if not agent.voice_enabled:
        print("⚠️  Voice features could not be initialized.")
        print("TTS engine may not be available on this system.")
        return
    
    print("Creating AI agent with voice capabilities...")
    print(f"Agent name: {agent.name}")
    print(f"Voice enabled: {agent.voice_enabled}")
    
    # Enable voice output
    agent.voice_assistant.enable_voice_mode()
    
    # Have a conversation with voice output
    messages = ["Hi there!", "Tell me about yourself"]
    
    for msg in messages:
        print(f"\n👤 User (text): {msg}")
        response = agent.voice_assistant.process_text_with_voice(msg)
        print(f"🤖 {agent.name} (speaking): {response}")
        time.sleep(0.5)  # Brief pause between messages
    
    print("\n✓ Voice mode demo complete")

def demo_voice_settings():
    """Demonstrate voice settings and customization."""
    print_section("Demo 3: Voice Settings and Customization")
    
    if not VOICE_AVAILABLE:
        print("⚠️  Voice chat module not available.")
        return
    
    from voice_chat import VoiceChat
    
    print("Initializing voice chat with custom settings...")
    voice_chat = VoiceChat(rate=150, volume=0.9)
    
    # Get current settings
    settings = voice_chat.get_current_settings()
    print("\nCurrent voice settings:")
    print(f"  - TTS Available: {settings.get('tts_available', False)}")
    print(f"  - Rate: {settings.get('rate', 'N/A')} words/min")
    print(f"  - Volume: {settings.get('volume', 'N/A')}")
    
    # List available voices
    voices = voice_chat.list_available_voices()
    print(f"\nAvailable voices: {len(voices)}")
    
    if voices:
        for i, voice in enumerate(voices[:3]):  # Show first 3
            print(f"  {i}: {voice.name} ({voice.languages})")
        if len(voices) > 3:
            print(f"  ... and {len(voices) - 3} more")
    
    # Demonstrate voice adjustment
    if voice_chat.tts_available:
        print("\nDemonstrating different speech rates...")
        test_phrase = "This is a test of text to speech."
        
        for rate in [100, 150, 200]:
            print(f"\n  Testing rate: {rate} words/min")
            voice_chat.set_voice_properties(rate=rate)
            voice_chat.speak(test_phrase)
            time.sleep(0.5)
    
    print("\n✓ Voice settings demo complete")

def demo_conversation_modes():
    """Demonstrate different conversation modes."""
    print_section("Demo 4: Conversation Modes")
    
    if not VOICE_AVAILABLE:
        print("⚠️  Voice chat module not available.")
        return
    
    from voice_chat import VoiceAssistant
    
    agent = AIAgent(name="OG-AI Multi-Mode")
    voice_assistant = VoiceAssistant(agent)
    
    print("Voice assistant supports multiple interaction modes:")
    print("\n1. Text Input + Text Output (default)")
    print("   - Type messages, read responses")
    
    print("\n2. Text Input + Voice Output")
    print("   - Type messages, hear spoken responses")
    voice_assistant.enable_voice_mode()
    msg = "Testing voice output mode"
    print(f"   Example: '{msg}'")
    response = voice_assistant.process_text_with_voice(msg)
    print(f"   Response (spoken): {response}")
    
    print("\n3. Voice Input + Text Output")
    print("   - Speak messages, read responses")
    print("   - Requires microphone access")
    
    print("\n4. Voice Input + Voice Output")
    print("   - Speak messages, hear spoken responses")
    print("   - Full voice interaction mode")
    
    print("\nMode switching:")
    print("  - Use 'toggle voice' to enable/disable voice output")
    print("  - Use 'switch input' to toggle voice/text input")
    
    voice_assistant.disable_voice_mode()
    print("\n✓ Conversation modes demo complete")

def demo_conversation_history():
    """Demonstrate conversation history with voice."""
    print_section("Demo 5: Conversation History Management")
    
    agent = AIAgent(name="OG-AI History", enable_voice=True)
    
    print("Having a multi-turn conversation...")
    
    conversation = [
        "Hello!",
        "What's your name?",
        "Can you remember our conversation?",
        "Goodbye!"
    ]
    
    for msg in conversation:
        print(f"\n👤 User: {msg}")
        response = agent.process_message(msg)
        print(f"🤖 Agent: {response}")
    
    # Show history
    history = agent.get_conversation_history()
    print(f"\nConversation history contains {len(history)} messages:")
    
    for i, msg in enumerate(history, 1):
        role_icon = "👤" if msg['role'] == 'user' else "🤖"
        print(f"  {i}. {role_icon} [{msg['role']}]: {msg['content'][:50]}...")
    
    # Save conversation
    filepath = "./demo_conversation.json"
    agent.save_conversation(filepath)
    print(f"\n✓ Conversation saved to {filepath}")
    
    print("\n✓ Conversation history demo complete")

def print_usage_info():
    """Print usage information."""
    print_section("Voice Chat Usage Information")
    
    print("To use voice chat in your own applications:\n")
    
    print("1. Import the required modules:")
    print("   from ai_agent import AIAgent")
    print("   from voice_chat import VoiceAssistant\n")
    
    print("2. Create an agent with voice enabled:")
    print("   agent = AIAgent(name='My Agent', enable_voice=True)\n")
    
    print("3. Enable voice output:")
    print("   agent.voice_assistant.enable_voice_mode()\n")
    
    print("4. Process messages:")
    print("   # Text input with voice output")
    print("   response = agent.voice_assistant.process_text_with_voice('Hello')\n")
    print("   # Voice input (requires microphone)")
    print("   response = agent.voice_assistant.process_voice_input()\n")
    
    print("5. Interactive session:")
    print("   agent.voice_assistant.interactive_session(use_voice_input=False)\n")
    
    print("For more information, see README.md")

def main():
    """Run all demos."""
    print("\n" + "=" * 70)
    print("  OG-AI Voice Chat Features Demo")
    print("=" * 70)
    
    if not VOICE_AVAILABLE:
        print("\n⚠️  Note: Voice chat dependencies not fully available.")
        print("Some demos will show what would happen if voice was enabled.")
        print("Install dependencies: pip install SpeechRecognition pyttsx3\n")
    
    try:
        # Run all demos
        demo_text_mode()
        time.sleep(1)
        
        demo_voice_mode()
        time.sleep(1)
        
        demo_voice_settings()
        time.sleep(1)
        
        demo_conversation_modes()
        time.sleep(1)
        
        demo_conversation_history()
        time.sleep(1)
        
        print_usage_info()
        
        # Summary
        print_section("Demo Complete")
        print("✅ All voice chat features demonstrated successfully!")
        print("\nNext steps:")
        print("  1. Run 'python ai_agent.py --voice' for interactive voice mode")
        print("  2. Run 'python voice_chat.py' for full voice interaction")
        print("  3. See 'example_usage.py' for more code examples")
        print("  4. Read 'README.md' for complete documentation\n")
        
        return 0
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Demo interrupted by user.")
        return 1
    except Exception as e:
        print(f"\n\n❌ Error during demo: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
