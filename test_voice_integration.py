"""
Simple test script to verify voice chat integration works correctly.
This is a manual verification script, not a formal unit test.
"""

import sys
from ai_agent import AIAgent, VOICE_AVAILABLE

def test_basic_agent():
    """Test basic agent without voice."""
    print("Test 1: Basic agent (no voice)")
    agent = AIAgent(name="Test Agent")
    response = agent.process_message("Hello")
    assert "Hello" in response or "Hi" in response
    print("✓ Basic agent works")

def test_agent_with_voice_flag():
    """Test agent with voice flag enabled."""
    print("\nTest 2: Agent with voice flag")
    agent = AIAgent(name="Test Voice Agent", enable_voice=True)
    
    # Should have voice_enabled set (True if available, False if not)
    assert hasattr(agent, 'voice_enabled')
    
    # If voice is available, should have voice_assistant
    if VOICE_AVAILABLE:
        assert agent.voice_assistant is not None
        print("✓ Voice assistant initialized")
    else:
        print("✓ Voice assistant gracefully handles unavailability")
    
    # Should still process messages normally
    response = agent.process_message("Hello")
    assert "Hello" in response or "Hi" in response
    print("✓ Agent with voice flag works")

def test_voice_chat_classes():
    """Test voice chat classes if available."""
    print("\nTest 3: Voice chat classes")
    
    if not VOICE_AVAILABLE:
        print("⊘ Voice chat not available, skipping")
        return
    
    from voice_chat import VoiceChat, VoiceAssistant
    
    # Test VoiceChat initialization
    voice_chat = VoiceChat()
    assert hasattr(voice_chat, 'recognizer')
    print("✓ VoiceChat initialized")
    
    # Test VoiceAssistant initialization
    agent = AIAgent(name="Test Agent")
    voice_assistant = VoiceAssistant(agent)
    assert voice_assistant.agent == agent
    assert hasattr(voice_assistant, 'voice_mode')
    print("✓ VoiceAssistant initialized")
    
    # Test toggle voice mode
    initial_state = voice_assistant.voice_mode
    voice_assistant.toggle_voice_mode()
    assert voice_assistant.voice_mode != initial_state
    print("✓ Voice mode toggle works")
    
    # Test process_text_with_voice (should not crash even if TTS unavailable)
    response = voice_assistant.process_text_with_voice("Hello")
    assert len(response) > 0
    print("✓ Text processing with voice mode works")

def test_conversation_history():
    """Test that conversation history works with voice-enabled agent."""
    print("\nTest 4: Conversation history with voice agent")
    agent = AIAgent(name="Test Agent", enable_voice=True)
    
    agent.process_message("Hello")
    agent.process_message("How are you?")
    
    history = agent.get_conversation_history()
    assert len(history) == 4  # 2 user messages + 2 agent responses
    print("✓ Conversation history works with voice-enabled agent")

def test_voice_settings():
    """Test voice settings if available."""
    print("\nTest 5: Voice settings")
    
    if not VOICE_AVAILABLE:
        print("⊘ Voice chat not available, skipping")
        return
    
    from voice_chat import VoiceChat
    
    voice_chat = VoiceChat(rate=200, volume=0.8)
    settings = voice_chat.get_current_settings()
    assert 'tts_available' in settings
    print("✓ Voice settings can be retrieved")
    
    voices = voice_chat.list_available_voices()
    assert isinstance(voices, list)
    print(f"✓ Available voices: {len(voices)}")

def main():
    """Run all tests."""
    print("=" * 60)
    print("Voice Chat Integration Test Suite")
    print("=" * 60)
    
    try:
        test_basic_agent()
        test_agent_with_voice_flag()
        test_voice_chat_classes()
        test_conversation_history()
        test_voice_settings()
        
        print("\n" + "=" * 60)
        print("✅ All tests passed!")
        print("=" * 60)
        return 0
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == "__main__":
    sys.exit(main())
