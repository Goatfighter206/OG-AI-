"""
Comprehensive unit tests for ai_agent.py module.
Tests AIAgent class with focus on voice integration and core functionality.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock, mock_open
import json
import tempfile
import os
from datetime import datetime

# Mock voice_chat module before importing ai_agent
sys_modules_patch = patch.dict('sys.modules', {
    'speech_recognition': MagicMock(),
    'pyttsx3': MagicMock(),
    'voice_chat': MagicMock()
})
sys_modules_patch.start()

from ai_agent import AIAgent, VOICE_AVAILABLE


class TestAIAgentInitialization(unittest.TestCase):
    """Test AIAgent initialization with various configurations."""
    
    def test_init_default_parameters(self):
        """Test AIAgent initialization with default parameters."""
        agent = AIAgent()
        
        self.assertEqual(agent.name, "OG-AI")
        self.assertEqual(agent.config, {})
        self.assertEqual(agent.conversation_history, [])
        self.assertEqual(agent.system_prompt, 'You are a helpful AI assistant.')
        self.assertFalse(agent.voice_enabled)
        self.assertIsNone(agent.voice_assistant)
    
    def test_init_custom_name(self):
        """Test AIAgent initialization with custom name."""
        agent = AIAgent(name="CustomBot")
        
        self.assertEqual(agent.name, "CustomBot")
    
    def test_init_with_config(self):
        """Test AIAgent initialization with custom config."""
        config = {
            'agent_name': 'ConfiguredAgent',
            'system_prompt': 'You are a specialized assistant.'
        }
        agent = AIAgent(name="TestAgent", config=config)
        
        self.assertEqual(agent.config, config)
        self.assertEqual(agent.system_prompt, 'You are a specialized assistant.')
    
    def test_init_config_without_system_prompt(self):
        """Test AIAgent with config that doesn't have system_prompt."""
        config = {'other_setting': 'value'}
        agent = AIAgent(config=config)
        
        self.assertEqual(agent.system_prompt, 'You are a helpful AI assistant.')
    
    def test_init_voice_disabled_by_default(self):
        """Test that voice is disabled by default."""
        agent = AIAgent()
        
        self.assertFalse(agent.voice_enabled)
        self.assertIsNone(agent.voice_assistant)
    
    def test_init_enable_voice_when_available(self):
        """Test enabling voice when voice_chat module is available."""
        with patch('ai_agent.VOICE_AVAILABLE', True):
            mock_voice_assistant = MagicMock()
            with patch('ai_agent.VoiceAssistant', return_value=mock_voice_assistant):
                agent = AIAgent(name="VoiceAgent", enable_voice=True)
                
                self.assertTrue(agent.voice_enabled)
                self.assertIsNotNone(agent.voice_assistant)
    
    def test_init_enable_voice_when_unavailable(self):
        """Test enabling voice when voice_chat module is unavailable."""
        with patch('ai_agent.VOICE_AVAILABLE', False):
            with patch('builtins.print'):
                agent = AIAgent(name="VoiceAgent", enable_voice=True)
                
                self.assertFalse(agent.voice_enabled)
                self.assertIsNone(agent.voice_assistant)
    
    def test_init_multiple_agents(self):
        """Test creating multiple agent instances."""
        agent1 = AIAgent(name="Agent1")
        agent2 = AIAgent(name="Agent2")
        
        self.assertNotEqual(agent1.name, agent2.name)
        self.assertNotEqual(id(agent1.conversation_history), id(agent2.conversation_history))


class TestAIAgentConversationHistory(unittest.TestCase):
    """Test conversation history management."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.agent = AIAgent(name="TestAgent")
    
    def test_add_message_user(self):
        """Test adding a user message."""
        self.agent.add_message('user', 'Hello')
        
        self.assertEqual(len(self.agent.conversation_history), 1)
        self.assertEqual(self.agent.conversation_history[0]['role'], 'user')
        self.assertEqual(self.agent.conversation_history[0]['content'], 'Hello')
        self.assertIn('timestamp', self.agent.conversation_history[0])
    
    def test_add_message_assistant(self):
        """Test adding an assistant message."""
        self.agent.add_message('assistant', 'Hi there!')
        
        self.assertEqual(len(self.agent.conversation_history), 1)
        self.assertEqual(self.agent.conversation_history[0]['role'], 'assistant')
        self.assertEqual(self.agent.conversation_history[0]['content'], 'Hi there!')
    
    def test_add_message_system(self):
        """Test adding a system message."""
        self.agent.add_message('system', 'System initialized')
        
        self.assertEqual(self.agent.conversation_history[0]['role'], 'system')
    
    def test_add_message_timestamp_format(self):
        """Test that timestamp is in ISO format."""
        self.agent.add_message('user', 'Test')
        
        timestamp = self.agent.conversation_history[0]['timestamp']
        # Should be able to parse as ISO format
        datetime.fromisoformat(timestamp)
    
    def test_add_multiple_messages(self):
        """Test adding multiple messages in sequence."""
        self.agent.add_message('user', 'First')
        self.agent.add_message('assistant', 'Second')
        self.agent.add_message('user', 'Third')
        
        self.assertEqual(len(self.agent.conversation_history), 3)
    
    def test_get_conversation_history_empty(self):
        """Test getting conversation history when empty."""
        history = self.agent.get_conversation_history()
        
        self.assertEqual(history, [])
    
    def test_get_conversation_history_with_messages(self):
        """Test getting conversation history with messages."""
        self.agent.add_message('user', 'Hello')
        self.agent.add_message('assistant', 'Hi')
        
        history = self.agent.get_conversation_history()
        
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]['content'], 'Hello')
        self.assertEqual(history[1]['content'], 'Hi')
    
    def test_clear_history(self):
        """Test clearing conversation history."""
        self.agent.add_message('user', 'Test')
        self.agent.add_message('assistant', 'Response')
        
        self.agent.clear_history()
        
        self.assertEqual(len(self.agent.conversation_history), 0)
    
    def test_clear_history_when_empty(self):
        """Test clearing already empty history."""
        self.agent.clear_history()
        
        self.assertEqual(len(self.agent.conversation_history), 0)


class TestAIAgentMessageProcessing(unittest.TestCase):
    """Test message processing and response generation."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.agent = AIAgent(name="TestBot")
    
    def test_process_message_hello(self):
        """Test processing hello message."""
        response = self.agent.process_message("Hello")
        
        self.assertIn("Hello", response)
        self.assertIn(self.agent.name, response)
        self.assertEqual(len(self.agent.conversation_history), 2)
    
    def test_process_message_hi(self):
        """Test processing hi message."""
        response = self.agent.process_message("Hi")
        
        self.assertIn("Hello", response)
    
    def test_process_message_help(self):
        """Test processing help message."""
        response = self.agent.process_message("I need help")
        
        self.assertIn("assist", response.lower())
    
    def test_process_message_name_query(self):
        """Test processing name query."""
        response = self.agent.process_message("What is your name?")
        
        self.assertIn(self.agent.name, response)
    
    def test_process_message_how_are_you(self):
        """Test processing 'how are you' message."""
        response = self.agent.process_message("How are you?")
        
        self.assertIn("functioning", response.lower())
    
    def test_process_message_goodbye(self):
        """Test processing goodbye message."""
        response = self.agent.process_message("Goodbye")
        
        self.assertIn("Goodbye", response)
    
    def test_process_message_bye(self):
        """Test processing bye message."""
        response = self.agent.process_message("Bye")
        
        self.assertIn("Goodbye", response)
    
    def test_process_message_unknown(self):
        """Test processing unknown/generic message."""
        response = self.agent.process_message("Random message")
        
        self.assertIn("Random message", response)
        self.assertIn("basic AI agent", response)
    
    def test_process_message_case_insensitive(self):
        """Test that message processing is case insensitive."""
        response1 = self.agent.process_message("HELLO")
        response2 = self.agent.process_message("hello")
        response3 = self.agent.process_message("HeLLo")
        
        # All should trigger hello response
        for response in [response1, response2, response3]:
            self.assertIn("Hello", response)
    
    def test_process_message_empty_string(self):
        """Test processing empty string."""
        response = self.agent.process_message("")
        
        self.assertIsNotNone(response)
        self.assertEqual(len(self.agent.conversation_history), 2)
    
    def test_process_message_whitespace_only(self):
        """Test processing whitespace-only message."""
        response = self.agent.process_message("   ")
        
        self.assertIsNotNone(response)
    
    def test_process_message_special_characters(self):
        """Test processing message with special characters."""
        response = self.agent.process_message("Hello! @#$%")
        
        self.assertIsNotNone(response)
    
    def test_process_message_unicode(self):
        """Test processing message with unicode characters."""
        response = self.agent.process_message("Hello 你好 🌟")
        
        self.assertIsNotNone(response)
    
    def test_process_message_adds_to_history(self):
        """Test that processing adds both user and assistant messages."""
        initial_count = len(self.agent.conversation_history)
        
        self.agent.process_message("Test message")
        
        self.assertEqual(len(self.agent.conversation_history), initial_count + 2)
    
    def test_process_message_conversation_flow(self):
        """Test a full conversation flow."""
        responses = []
        messages = ["Hello", "What's your name?", "How are you?", "Goodbye"]
        
        for msg in messages:
            response = self.agent.process_message(msg)
            responses.append(response)
        
        self.assertEqual(len(responses), 4)
        self.assertEqual(len(self.agent.conversation_history), 8)


class TestAIAgentFileOperations(unittest.TestCase):
    """Test saving and loading conversation history."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.agent = AIAgent(name="FileTestAgent")
        self.temp_dir = tempfile.mkdtemp()
        self.test_file = os.path.join(self.temp_dir, "test_conversation.json")
    
    def tearDown(self):
        """Clean up test files."""
        if os.path.exists(self.test_file):
            os.remove(self.test_file)
        os.rmdir(self.temp_dir)
    
    def test_save_conversation_empty_history(self):
        """Test saving empty conversation history."""
        self.agent.save_conversation(self.test_file)
        
        self.assertTrue(os.path.exists(self.test_file))
        with open(self.test_file, 'r') as f:
            data = json.load(f)
            self.assertEqual(data['agent_name'], 'FileTestAgent')
            self.assertEqual(data['conversation'], [])
    
    def test_save_conversation_with_messages(self):
        """Test saving conversation with messages."""
        self.agent.process_message("Hello")
        self.agent.process_message("How are you?")
        
        self.agent.save_conversation(self.test_file)
        
        with open(self.test_file, 'r') as f:
            data = json.load(f)
            self.assertEqual(len(data['conversation']), 4)
    
    def test_save_conversation_json_format(self):
        """Test that saved conversation is valid JSON."""
        self.agent.add_message('user', 'Test')
        self.agent.save_conversation(self.test_file)
        
        with open(self.test_file, 'r') as f:
            data = json.load(f)
            self.assertIn('agent_name', data)
            self.assertIn('conversation', data)
    
    def test_save_conversation_invalid_path(self):
        """Test saving to invalid path raises IOError."""
        invalid_path = "/nonexistent/directory/file.json"
        
        with self.assertRaises(IOError):
            self.agent.save_conversation(invalid_path)
    
    def test_load_conversation_file_not_found(self):
        """Test loading from non-existent file raises FileNotFoundError."""
        with self.assertRaises(FileNotFoundError):
            self.agent.load_conversation("/nonexistent/file.json")
    
    def test_load_conversation_success(self):
        """Test successfully loading conversation."""
        # Create and save a conversation
        self.agent.process_message("Test message")
        self.agent.save_conversation(self.test_file)
        
        # Create new agent and load
        new_agent = AIAgent(name="NewAgent")
        new_agent.load_conversation(self.test_file)
        
        self.assertEqual(len(new_agent.conversation_history), 2)
        self.assertEqual(new_agent.conversation_history[0]['content'], 'Test message')
    
    def test_load_conversation_invalid_json(self):
        """Test loading invalid JSON raises JSONDecodeError."""
        # Write invalid JSON
        with open(self.test_file, 'w') as f:
            f.write("{ invalid json }")
        
        with self.assertRaises(json.JSONDecodeError):
            self.agent.load_conversation(self.test_file)
    
    def test_load_conversation_missing_keys(self):
        """Test loading JSON without expected keys."""
        # Write JSON without 'conversation' key
        with open(self.test_file, 'w') as f:
            json.dump({'other_key': 'value'}, f)
        
        self.agent.load_conversation(self.test_file)
        
        # Should handle gracefully with empty list
        self.assertEqual(self.agent.conversation_history, [])
    
    def test_save_load_roundtrip(self):
        """Test that save and load preserve conversation exactly."""
        messages = ["Hello", "How are you?", "What's your name?"]
        for msg in messages:
            self.agent.process_message(msg)
        
        original_history = self.agent.get_conversation_history()
        
        self.agent.save_conversation(self.test_file)
        
        new_agent = AIAgent()
        new_agent.load_conversation(self.test_file)
        
        loaded_history = new_agent.get_conversation_history()
        
        self.assertEqual(len(loaded_history), len(original_history))
        for orig, loaded in zip(original_history, loaded_history):
            self.assertEqual(orig['role'], loaded['role'])
            self.assertEqual(orig['content'], loaded['content'])


class TestAIAgentVoiceIntegration(unittest.TestCase):
    """Test voice integration with AIAgent."""
    
    def test_agent_with_voice_available(self):
        """Test agent creation when voice is available."""
        with patch('ai_agent.VOICE_AVAILABLE', True):
            mock_voice_assistant = MagicMock()
            with patch('ai_agent.VoiceAssistant', return_value=mock_voice_assistant):
                agent = AIAgent(name="VoiceAgent", enable_voice=True)
                
                self.assertTrue(agent.voice_enabled)
                self.assertEqual(agent.voice_assistant, mock_voice_assistant)
    
    def test_agent_with_voice_unavailable(self):
        """Test agent creation when voice is unavailable."""
        with patch('ai_agent.VOICE_AVAILABLE', False):
            with patch('builtins.print'):
                agent = AIAgent(name="VoiceAgent", enable_voice=True)
                
                self.assertFalse(agent.voice_enabled)
                self.assertIsNone(agent.voice_assistant)
    
    def test_agent_voice_disabled_by_default(self):
        """Test that voice is disabled when enable_voice=False."""
        agent = AIAgent(name="NoVoiceAgent", enable_voice=False)
        
        self.assertFalse(agent.voice_enabled)
        self.assertIsNone(agent.voice_assistant)
    
    def test_agent_processes_messages_without_voice(self):
        """Test that agent works normally without voice."""
        agent = AIAgent(name="TextAgent", enable_voice=False)
        
        response = agent.process_message("Hello")
        
        self.assertIsNotNone(response)
        self.assertEqual(len(agent.conversation_history), 2)
    
    def test_agent_processes_messages_with_voice_enabled(self):
        """Test that agent works with voice enabled."""
        with patch('ai_agent.VOICE_AVAILABLE', True):
            mock_voice_assistant = MagicMock()
            with patch('ai_agent.VoiceAssistant', return_value=mock_voice_assistant):
                agent = AIAgent(name="VoiceAgent", enable_voice=True)
                
                response = agent.process_message("Hello")
                
                self.assertIsNotNone(response)
                self.assertEqual(len(agent.conversation_history), 2)


class TestAIAgentEdgeCases(unittest.TestCase):
    """Test edge cases and boundary conditions."""
    
    def test_very_long_message(self):
        """Test processing very long message."""
        agent = AIAgent()
        long_message = "word " * 1000
        
        response = agent.process_message(long_message)
        
        self.assertIsNotNone(response)
    
    def test_repeated_messages(self):
        """Test processing same message multiple times."""
        agent = AIAgent()
        
        response1 = agent.process_message("Hello")
        response2 = agent.process_message("Hello")
        
        # Should get similar responses
        self.assertIsNotNone(response1)
        self.assertIsNotNone(response2)
        self.assertEqual(len(agent.conversation_history), 4)
    
    def test_rapid_message_processing(self):
        """Test processing many messages rapidly."""
        agent = AIAgent()
        
        for i in range(100):
            agent.process_message(f"Message {i}")
        
        self.assertEqual(len(agent.conversation_history), 200)
    
    def test_conversation_history_immutability(self):
        """Test that returned history doesn't affect internal state."""
        agent = AIAgent()
        agent.process_message("Hello")
        
        history = agent.get_conversation_history()
        history.append({'role': 'fake', 'content': 'fake'})
        
        # Internal history should remain unchanged
        self.assertEqual(len(agent.conversation_history), 2)
    
    def test_config_empty_dict(self):
        """Test agent with empty config dict."""
        agent = AIAgent(config={})
        
        self.assertEqual(agent.config, {})
        self.assertEqual(agent.system_prompt, 'You are a helpful AI assistant.')
    
    def test_config_none(self):
        """Test agent with None config."""
        agent = AIAgent(config=None)
        
        self.assertEqual(agent.config, {})
    
    def test_message_with_newlines(self):
        """Test processing message with newlines."""
        agent = AIAgent()
        message = "Hello\nWorld\nMultiple\nLines"
        
        response = agent.process_message(message)
        
        self.assertIsNotNone(response)
    
    def test_message_with_tabs(self):
        """Test processing message with tabs."""
        agent = AIAgent()
        message = "Hello\tWorld\twith\ttabs"
        
        response = agent.process_message(message)
        
        self.assertIsNotNone(response)


class TestAIAgentSystemPrompt(unittest.TestCase):
    """Test system prompt configuration."""
    
    def test_default_system_prompt(self):
        """Test default system prompt."""
        agent = AIAgent()
        
        self.assertEqual(agent.system_prompt, 'You are a helpful AI assistant.')
    
    def test_custom_system_prompt_in_config(self):
        """Test custom system prompt from config."""
        config = {'system_prompt': 'You are a specialized bot.'}
        agent = AIAgent(config=config)
        
        self.assertEqual(agent.system_prompt, 'You are a specialized bot.')
    
    def test_system_prompt_empty_string(self):
        """Test system prompt with empty string."""
        config = {'system_prompt': ''}
        agent = AIAgent(config=config)
        
        self.assertEqual(agent.system_prompt, '')


if __name__ == '__main__':
    unittest.main()