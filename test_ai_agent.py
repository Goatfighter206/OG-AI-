"""
Comprehensive unit tests for ai_agent.py
Tests cover AIAgent class functionality including initialization, message processing,
conversation management, and file I/O operations.
"""

import pytest
import json
import os
import tempfile
from datetime import datetime
from ai_agent import AIAgent


class TestAIAgentInitialization:
    """Test AIAgent initialization and configuration."""
    
    def test_default_initialization(self):
        """Test agent initialization with default parameters."""
        agent = AIAgent()
        assert agent.name == "OG-AI"
        assert agent.config == {}
        assert agent.conversation_history == []
        assert agent.system_prompt == "You are a helpful AI assistant."
    
    def test_custom_name_initialization(self):
        """Test agent initialization with custom name."""
        agent = AIAgent(name="CustomBot")
        assert agent.name == "CustomBot"
        assert agent.conversation_history == []
    
    def test_config_initialization(self):
        """Test agent initialization with custom config."""
        config = {
            "agent_name": "TestAgent",
            "system_prompt": "Custom system prompt",
            "max_history": 50
        }
        agent = AIAgent(name="TestBot", config=config)
        assert agent.name == "TestBot"
        assert agent.config == config
        assert agent.system_prompt == "Custom system prompt"
    
    def test_default_system_prompt(self):
        """Test that default system prompt is set when not in config."""
        agent = AIAgent(config={})
        assert agent.system_prompt == "You are a helpful AI assistant."
    
    def test_custom_system_prompt(self):
        """Test custom system prompt from config."""
        config = {"system_prompt": "You are a specialized assistant."}
        agent = AIAgent(config=config)
        assert agent.system_prompt == "You are a specialized assistant."
    
    def test_none_config(self):
        """Test that None config is handled correctly."""
        agent = AIAgent(config=None)
        assert agent.config == {}
        assert agent.conversation_history == []


class TestAddMessage:
    """Test add_message functionality."""
    
    def test_add_user_message(self):
        """Test adding a user message to conversation history."""
        agent = AIAgent()
        agent.add_message('user', 'Hello!')
        
        assert len(agent.conversation_history) == 1
        assert agent.conversation_history[0]['role'] == 'user'
        assert agent.conversation_history[0]['content'] == 'Hello!'
        assert 'timestamp' in agent.conversation_history[0]
    
    def test_add_assistant_message(self):
        """Test adding an assistant message to conversation history."""
        agent = AIAgent()
        agent.add_message('assistant', 'Hello! How can I help?')
        
        assert len(agent.conversation_history) == 1
        assert agent.conversation_history[0]['role'] == 'assistant'
        assert agent.conversation_history[0]['content'] == 'Hello! How can I help?'
    
    def test_add_system_message(self):
        """Test adding a system message to conversation history."""
        agent = AIAgent()
        agent.add_message('system', 'System initialized')
        
        assert len(agent.conversation_history) == 1
        assert agent.conversation_history[0]['role'] == 'system'
    
    def test_add_multiple_messages(self):
        """Test adding multiple messages maintains order."""
        agent = AIAgent()
        agent.add_message('user', 'First message')
        agent.add_message('assistant', 'First response')
        agent.add_message('user', 'Second message')
        
        assert len(agent.conversation_history) == 3
        assert agent.conversation_history[0]['content'] == 'First message'
        assert agent.conversation_history[1]['content'] == 'First response'
        assert agent.conversation_history[2]['content'] == 'Second message'
    
    def test_timestamp_format(self):
        """Test that timestamp is in ISO format."""
        agent = AIAgent()
        agent.add_message('user', 'Test')
        
        timestamp_str = agent.conversation_history[0]['timestamp']
        # Should be parseable as ISO format
        datetime.fromisoformat(timestamp_str)
    
    def test_empty_content(self):
        """Test adding message with empty content."""
        agent = AIAgent()
        agent.add_message('user', '')
        
        assert len(agent.conversation_history) == 1
        assert agent.conversation_history[0]['content'] == ''
    
    def test_special_characters_in_content(self):
        """Test message with special characters."""
        agent = AIAgent()
        special_text = "Hello! @#$% \n\t 你好 🤖"
        agent.add_message('user', special_text)
        
        assert agent.conversation_history[0]['content'] == special_text


class TestProcessMessage:
    """Test process_message functionality."""
    
    def test_process_simple_message(self):
        """Test processing a simple message."""
        agent = AIAgent()
        response = agent.process_message("Test message")
        
        assert isinstance(response, str)
        assert len(response) > 0
        assert len(agent.conversation_history) == 2  # user + assistant
    
    def test_greeting_response(self):
        """Test greeting message generates appropriate response."""
        agent = AIAgent(name="TestBot")
        response = agent.process_message("Hello")
        
        assert "TestBot" in response
        assert "Hello" in response or "hello" in response.lower()
    
    def test_hi_greeting(self):
        """Test 'hi' greeting."""
        agent = AIAgent()
        response = agent.process_message("Hi there!")
        
        assert "Hello" in response or "Hi" in response
    
    def test_help_message(self):
        """Test help request generates appropriate response."""
        agent = AIAgent()
        response = agent.process_message("I need help")
        
        assert "help" in response.lower() or "assist" in response.lower()
    
    def test_name_query(self):
        """Test asking for agent's name."""
        agent = AIAgent(name="BotName")
        response = agent.process_message("What is your name?")
        
        assert "BotName" in response
    
    def test_how_are_you(self):
        """Test 'how are you' query."""
        agent = AIAgent()
        response = agent.process_message("How are you?")
        
        assert "well" in response.lower() or "good" in response.lower()
    
    def test_goodbye_message(self):
        """Test goodbye message."""
        agent = AIAgent()
        response = agent.process_message("Goodbye")
        
        assert "Goodbye" in response or "goodbye" in response.lower()
    
    def test_bye_message(self):
        """Test bye message."""
        agent = AIAgent()
        response = agent.process_message("bye")
        
        assert "Goodbye" in response or "bye" in response.lower()
    
    def test_unknown_message(self):
        """Test unknown/general message gets default response."""
        agent = AIAgent()
        message = "Random unknown message xyz"
        response = agent.process_message(message)
        
        assert message in response or "I understand" in response
    
    def test_case_insensitive_matching(self):
        """Test that pattern matching is case-insensitive."""
        agent = AIAgent()
        
        responses = [
            agent.process_message("HELLO"),
            agent.process_message("HeLLo"),
            agent.process_message("hello")
        ]
        
        # All should trigger greeting response
        for response in responses:
            assert "Hello" in response or "hello" in response.lower()
    
    def test_conversation_history_after_processing(self):
        """Test conversation history is properly updated after processing."""
        agent = AIAgent()
        user_msg = "Test message"
        response = agent.process_message(user_msg)
        
        history = agent.get_conversation_history()
        assert len(history) == 2
        assert history[0]['role'] == 'user'
        assert history[0]['content'] == user_msg
        assert history[1]['role'] == 'assistant'
        assert history[1]['content'] == response
    
    def test_multiple_messages_processing(self):
        """Test processing multiple messages in sequence."""
        agent = AIAgent()
        
        agent.process_message("Hello")
        agent.process_message("What's your name?")
        agent.process_message("Goodbye")
        
        assert len(agent.conversation_history) == 6  # 3 pairs
    
    def test_empty_string_message(self):
        """Test processing empty string."""
        agent = AIAgent()
        response = agent.process_message("")
        
        assert isinstance(response, str)
        assert len(agent.conversation_history) == 2
    
    def test_whitespace_only_message(self):
        """Test processing whitespace-only message."""
        agent = AIAgent()
        response = agent.process_message("   ")
        
        assert isinstance(response, str)
        assert len(agent.conversation_history) == 2
    
    def test_very_long_message(self):
        """Test processing very long message."""
        agent = AIAgent()
        long_message = "test " * 1000
        response = agent.process_message(long_message)
        
        assert isinstance(response, str)
        assert len(agent.conversation_history) == 2


class TestGenerateResponse:
    """Test _generate_response method (internal logic)."""
    
    def test_generate_response_hello(self):
        """Test response generation for hello."""
        agent = AIAgent(name="TestAgent")
        response = agent._generate_response("hello")
        
        assert "Hello" in response
        assert "TestAgent" in response
    
    def test_generate_response_help(self):
        """Test response generation for help."""
        agent = AIAgent()
        response = agent._generate_response("help me please")
        
        assert "assist" in response.lower() or "help" in response.lower()
    
    def test_generate_response_name(self):
        """Test response generation for name query."""
        agent = AIAgent(name="CustomName")
        response = agent._generate_response("what is your name")
        
        assert "CustomName" in response
    
    def test_generate_response_unknown(self):
        """Test response generation for unknown input."""
        agent = AIAgent()
        response = agent._generate_response("some random input")
        
        assert "I understand" in response


class TestConversationHistory:
    """Test conversation history management."""
    
    def test_get_empty_history(self):
        """Test getting history when empty."""
        agent = AIAgent()
        history = agent.get_conversation_history()
        
        assert history == []
        assert isinstance(history, list)
    
    def test_get_history_with_messages(self):
        """Test getting history with messages."""
        agent = AIAgent()
        agent.add_message('user', 'Message 1')
        agent.add_message('assistant', 'Response 1')
        
        history = agent.get_conversation_history()
        assert len(history) == 2
        assert history[0]['content'] == 'Message 1'
        assert history[1]['content'] == 'Response 1'
    
    def test_history_returns_copy_or_reference(self):
        """Test that history maintains integrity."""
        agent = AIAgent()
        agent.add_message('user', 'Test')
        
        history = agent.get_conversation_history()
        assert len(history) == 1
    
    def test_clear_empty_history(self):
        """Test clearing already empty history."""
        agent = AIAgent()
        agent.clear_history()
        
        assert agent.conversation_history == []
    
    def test_clear_history_with_messages(self):
        """Test clearing history with messages."""
        agent = AIAgent()
        agent.add_message('user', 'Message')
        agent.add_message('assistant', 'Response')
        
        agent.clear_history()
        assert len(agent.conversation_history) == 0
        assert agent.conversation_history == []
    
    def test_clear_history_multiple_times(self):
        """Test clearing history multiple times."""
        agent = AIAgent()
        agent.add_message('user', 'Test')
        agent.clear_history()
        agent.clear_history()
        
        assert agent.conversation_history == []
    
    def test_add_after_clear(self):
        """Test adding messages after clearing history."""
        agent = AIAgent()
        agent.add_message('user', 'First')
        agent.clear_history()
        agent.add_message('user', 'Second')
        
        assert len(agent.conversation_history) == 1
        assert agent.conversation_history[0]['content'] == 'Second'


class TestSaveConversation:
    """Test save_conversation functionality."""
    
    def test_save_empty_conversation(self):
        """Test saving empty conversation."""
        agent = AIAgent(name="TestAgent")
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            filepath = f.name
        
        try:
            agent.save_conversation(filepath)
            
            with open(filepath, 'r') as f:
                data = json.load(f)
            
            assert data['agent_name'] == 'TestAgent'
            assert data['conversation'] == []
        finally:
            os.unlink(filepath)
    
    def test_save_conversation_with_messages(self):
        """Test saving conversation with messages."""
        agent = AIAgent(name="SaveBot")
        agent.add_message('user', 'Hello')
        agent.add_message('assistant', 'Hi there!')
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            filepath = f.name
        
        try:
            agent.save_conversation(filepath)
            
            with open(filepath, 'r') as f:
                data = json.load(f)
            
            assert data['agent_name'] == 'SaveBot'
            assert len(data['conversation']) == 2
            assert data['conversation'][0]['role'] == 'user'
            assert data['conversation'][0]['content'] == 'Hello'
        finally:
            os.unlink(filepath)
    
    def test_save_creates_parent_directory(self):
        """Test that save handles paths correctly."""
        agent = AIAgent()
        agent.add_message('user', 'Test')
        
        with tempfile.TemporaryDirectory() as tmpdir:
            filepath = os.path.join(tmpdir, 'test.json')
            agent.save_conversation(filepath)
            
            assert os.path.exists(filepath)
    
    def test_save_invalid_path(self):
        """Test saving to invalid path raises error."""
        agent = AIAgent()
        
        # Try to save to a directory that doesn't exist and can't be created
        with pytest.raises(IOError):
            agent.save_conversation('/nonexistent/path/to/file.json')
    
    def test_save_no_write_permission(self):
        """Test saving to path without write permission."""
        agent = AIAgent()
        
        # Try to save to root directory (usually no permission)
        with pytest.raises(IOError):
            agent.save_conversation('/root_test_file.json')
    
    def test_save_overwrites_existing_file(self):
        """Test that save overwrites existing file."""
        agent = AIAgent(name="Agent1")
        agent.add_message('user', 'First message')
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            filepath = f.name
        
        try:
            # Save first time
            agent.save_conversation(filepath)
            
            # Create new agent and save to same path
            agent2 = AIAgent(name="Agent2")
            agent2.add_message('user', 'Second message')
            agent2.save_conversation(filepath)
            
            # Verify second save overwrote first
            with open(filepath, 'r') as f:
                data = json.load(f)
            
            assert data['agent_name'] == 'Agent2'
            assert data['conversation'][0]['content'] == 'Second message'
        finally:
            os.unlink(filepath)
    
    def test_save_preserves_timestamps(self):
        """Test that timestamps are preserved in saved file."""
        agent = AIAgent()
        agent.add_message('user', 'Test')
        
        original_timestamp = agent.conversation_history[0]['timestamp']
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            filepath = f.name
        
        try:
            agent.save_conversation(filepath)
            
            with open(filepath, 'r') as f:
                data = json.load(f)
            
            assert data['conversation'][0]['timestamp'] == original_timestamp
        finally:
            os.unlink(filepath)


class TestLoadConversation:
    """Test load_conversation functionality."""
    
    def test_load_empty_conversation(self):
        """Test loading empty conversation."""
        agent = AIAgent(name="LoadBot")
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            filepath = f.name
            json.dump({
                'agent_name': 'SavedAgent',
                'conversation': []
            }, f)
        
        try:
            agent.load_conversation(filepath)
            assert len(agent.conversation_history) == 0
        finally:
            os.unlink(filepath)
    
    def test_load_conversation_with_messages(self):
        """Test loading conversation with messages."""
        agent = AIAgent()
        
        test_conversation = [
            {'role': 'user', 'content': 'Hello', 'timestamp': '2024-01-01T12:00:00'},
            {'role': 'assistant', 'content': 'Hi!', 'timestamp': '2024-01-01T12:00:01'}
        ]
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            filepath = f.name
            json.dump({
                'agent_name': 'TestAgent',
                'conversation': test_conversation
            }, f)
        
        try:
            agent.load_conversation(filepath)
            
            assert len(agent.conversation_history) == 2
            assert agent.conversation_history[0]['content'] == 'Hello'
            assert agent.conversation_history[1]['content'] == 'Hi!'
        finally:
            os.unlink(filepath)
    
    def test_load_nonexistent_file(self):
        """Test loading from nonexistent file raises error."""
        agent = AIAgent()
        
        with pytest.raises(FileNotFoundError):
            agent.load_conversation('/nonexistent/file.json')
    
    def test_load_invalid_json(self):
        """Test loading invalid JSON raises error."""
        agent = AIAgent()
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            filepath = f.name
            f.write("This is not valid JSON {{{")
        
        try:
            with pytest.raises(json.JSONDecodeError):
                agent.load_conversation(filepath)
        finally:
            os.unlink(filepath)
    
    def test_load_missing_conversation_key(self):
        """Test loading file without conversation key."""
        agent = AIAgent()
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            filepath = f.name
            json.dump({'agent_name': 'Test'}, f)
        
        try:
            agent.load_conversation(filepath)
            # Should handle gracefully and set empty conversation
            assert agent.conversation_history == []
        finally:
            os.unlink(filepath)
    
    def test_load_replaces_existing_history(self):
        """Test that loading replaces existing conversation history."""
        agent = AIAgent()
        agent.add_message('user', 'Original message')
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            filepath = f.name
            json.dump({
                'agent_name': 'LoadedAgent',
                'conversation': [
                    {'role': 'user', 'content': 'Loaded message', 'timestamp': '2024-01-01T12:00:00'}
                ]
            }, f)
        
        try:
            agent.load_conversation(filepath)
            
            assert len(agent.conversation_history) == 1
            assert agent.conversation_history[0]['content'] == 'Loaded message'
        finally:
            os.unlink(filepath)
    
    def test_save_and_load_roundtrip(self):
        """Test saving and loading maintains conversation integrity."""
        agent1 = AIAgent(name="Agent1")
        agent1.add_message('user', 'Question 1')
        agent1.add_message('assistant', 'Answer 1')
        agent1.add_message('user', 'Question 2')
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json') as f:
            filepath = f.name
        
        try:
            agent1.save_conversation(filepath)
            
            agent2 = AIAgent()
            agent2.load_conversation(filepath)
            
            assert len(agent2.conversation_history) == 3
            assert agent2.conversation_history[0]['content'] == 'Question 1'
            assert agent2.conversation_history[1]['content'] == 'Answer 1'
            assert agent2.conversation_history[2]['content'] == 'Question 2'
        finally:
            os.unlink(filepath)


class TestEdgeCases:
    """Test edge cases and unusual scenarios."""
    
    def test_unicode_characters(self):
        """Test handling of unicode characters."""
        agent = AIAgent()
        message = "Hello 你好 مرحبا שלום 🤖"
        response = agent.process_message(message)
        
        assert isinstance(response, str)
        assert len(agent.conversation_history) == 2
    
    def test_very_long_name(self):
        """Test agent with very long name."""
        long_name = "A" * 1000
        agent = AIAgent(name=long_name)
        
        assert agent.name == long_name
    
    def test_special_characters_in_name(self):
        """Test agent name with special characters."""
        agent = AIAgent(name="Agent-123_Test!@#")
        assert agent.name == "Agent-123_Test!@#"
    
    def test_config_with_extra_keys(self):
        """Test config with extra/unknown keys."""
        config = {
            "system_prompt": "Test prompt",
            "unknown_key": "unknown_value",
            "another_key": 123
        }
        agent = AIAgent(config=config)
        
        assert agent.config == config
        assert agent.system_prompt == "Test prompt"
    
    def test_message_with_newlines(self):
        """Test message containing newlines."""
        agent = AIAgent()
        message = "Line 1\nLine 2\nLine 3"
        response = agent.process_message(message)
        
        assert isinstance(response, str)
    
    def test_message_with_tabs(self):
        """Test message containing tabs."""
        agent = AIAgent()
        message = "Tab\tseparated\tvalues"
        response = agent.process_message(message)
        
        assert isinstance(response, str)
    
    def test_multiple_agents_independent_history(self):
        """Test that multiple agents maintain independent histories."""
        agent1 = AIAgent(name="Agent1")
        agent2 = AIAgent(name="Agent2")
        
        agent1.add_message('user', 'Message to agent 1')
        agent2.add_message('user', 'Message to agent 2')
        
        assert len(agent1.conversation_history) == 1
        assert len(agent2.conversation_history) == 1
        assert agent1.conversation_history[0]['content'] == 'Message to agent 1'
        assert agent2.conversation_history[0]['content'] == 'Message to agent 2'
    
    def test_null_bytes_in_message(self):
        """Test handling of null bytes in message."""
        agent = AIAgent()
        # Python strings can contain null bytes
        message = "Test\x00message"
        response = agent.process_message(message)
        
        assert isinstance(response, str)