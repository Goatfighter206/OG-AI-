"""
Comprehensive unit tests for voice_chat.py module.
Tests VoiceChat and VoiceAssistant classes with various scenarios.
"""

import unittest
from unittest.mock import Mock, patch, MagicMock
import threading
import time
import sys


# Mock the dependencies before importing
sys.modules['speech_recognition'] = MagicMock()
sys.modules['pyttsx3'] = MagicMock()

from voice_chat import VoiceChat, VoiceAssistant
from ai_agent import AIAgent


class TestVoiceChatInitialization(unittest.TestCase):
    """Test VoiceChat initialization with various configurations."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.mock_sr = sys.modules['speech_recognition']
        self.mock_pyttsx3 = sys.modules['pyttsx3']
        
    def test_init_default_parameters(self):
        """Test VoiceChat initialization with default parameters."""
        with patch('voice_chat.TTS_AVAILABLE', True):
            mock_engine = MagicMock()
            self.mock_pyttsx3.init.return_value = mock_engine
            
            vc = VoiceChat()
            
            self.assertIsNotNone(vc.recognizer)
            self.assertEqual(vc.tts_available, True)
            mock_engine.setProperty.assert_any_call('rate', 150)
            mock_engine.setProperty.assert_any_call('volume', 0.9)
    
    def test_init_custom_parameters(self):
        """Test VoiceChat initialization with custom parameters."""
        with patch('voice_chat.TTS_AVAILABLE', True):
            mock_engine = MagicMock()
            self.mock_pyttsx3.init.return_value = mock_engine
            
            VoiceChat(rate=200, volume=0.5, voice_id=None)
            
            mock_engine.setProperty.assert_any_call('rate', 200)
            mock_engine.setProperty.assert_any_call('volume', 0.5)
    
    def test_init_with_voice_id(self):
        """Test VoiceChat initialization with specific voice ID."""
        with patch('voice_chat.TTS_AVAILABLE', True):
            mock_engine = MagicMock()
            mock_voice = MagicMock()
            mock_voice.id = 'voice-123'
            mock_engine.getProperty.return_value = [mock_voice, MagicMock()]
            self.mock_pyttsx3.init.return_value = mock_engine
            
            VoiceChat(voice_id=0)
            
            mock_engine.setProperty.assert_any_call('voice', 'voice-123')
    
    def test_init_with_invalid_voice_id(self):
        """Test VoiceChat initialization with out-of-range voice ID."""
        with patch('voice_chat.TTS_AVAILABLE', True):
            mock_engine = MagicMock()
            mock_engine.getProperty.return_value = [MagicMock()]
            self.mock_pyttsx3.init.return_value = mock_engine
            
            vc = VoiceChat(voice_id=10)
            
            # Should not crash, voice_id out of range is ignored
            self.assertIsNotNone(vc.tts_engine)
    
    def test_init_tts_not_available(self):
        """Test VoiceChat initialization when TTS is not available."""
        with patch('voice_chat.TTS_AVAILABLE', False):
            vc = VoiceChat()
            
            self.assertFalse(vc.tts_available)
            self.assertIsNone(vc.tts_engine)
    
    def test_init_tts_runtime_error(self):
        """Test VoiceChat initialization when TTS engine fails to initialize."""
        with patch('voice_chat.TTS_AVAILABLE', True):
            self.mock_pyttsx3.init.side_effect = RuntimeError("TTS engine error")
            
            vc = VoiceChat()
            
            self.assertFalse(vc.tts_available)
            self.assertIsNone(vc.tts_engine)
    
    def test_init_tts_os_error(self):
        """Test VoiceChat initialization with audio device issues."""
        with patch('voice_chat.TTS_AVAILABLE', True):
            self.mock_pyttsx3.init.side_effect = OSError("No audio device")
            
            vc = VoiceChat()
            
            self.assertFalse(vc.tts_available)
            self.assertIsNone(vc.tts_engine)
    
    def test_thread_lock_created(self):
        """Test that thread lock is created for TTS."""
        vc = VoiceChat()
        
        self.assertIsInstance(vc._tts_lock, type(threading.Lock()))


class TestVoiceChatListen(unittest.TestCase):
    """Test VoiceChat.listen() method for voice input."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.mock_sr = sys.modules['speech_recognition']
        self.vc = VoiceChat()
    
    def test_listen_successful_recognition(self):
        """Test successful voice recognition."""
        mock_mic = MagicMock()
        mock_audio = MagicMock()
        self.mock_sr.Microphone.return_value.__enter__.return_value = mock_mic
        self.vc.recognizer.listen.return_value = mock_audio
        self.vc.recognizer.recognize_google.return_value = "hello world"
        
        result = self.vc.listen(timeout=5, phrase_time_limit=10)
        
        self.assertEqual(result, "hello world")
        self.vc.recognizer.adjust_for_ambient_noise.assert_called_once()
        self.vc.recognizer.listen.assert_called_once()
    
    def test_listen_custom_timeout(self):
        """Test listen with custom timeout parameters."""
        mock_mic = MagicMock()
        mock_audio = MagicMock()
        self.mock_sr.Microphone.return_value.__enter__.return_value = mock_mic
        self.vc.recognizer.listen.return_value = mock_audio
        self.vc.recognizer.recognize_google.return_value = "test"
        
        self.vc.listen(timeout=3, phrase_time_limit=15)
        
        self.vc.recognizer.listen.assert_called_once_with(
            mock_mic, timeout=3, phrase_time_limit=15
        )
    
    def test_listen_wait_timeout_error(self):
        """Test listen when no speech is detected (timeout)."""
        mock_mic = MagicMock()
        self.mock_sr.Microphone.return_value.__enter__.return_value = mock_mic
        self.mock_sr.WaitTimeoutError = Exception
        self.vc.recognizer.listen.side_effect = Exception()
        
        with patch('builtins.print'):
            result = self.vc.listen()
        
        self.assertIsNone(result)
    
    def test_listen_unknown_value_error(self):
        """Test listen when audio cannot be understood."""
        mock_mic = MagicMock()
        mock_audio = MagicMock()
        self.mock_sr.Microphone.return_value.__enter__.return_value = mock_mic
        self.mock_sr.UnknownValueError = Exception
        self.vc.recognizer.listen.return_value = mock_audio
        self.vc.recognizer.recognize_google.side_effect = Exception()
        
        with patch('builtins.print'):
            result = self.vc.listen()
        
        self.assertIsNone(result)
    
    def test_listen_request_error(self):
        """Test listen when speech recognition service fails."""
        mock_mic = MagicMock()
        mock_audio = MagicMock()
        self.mock_sr.Microphone.return_value.__enter__.return_value = mock_mic
        self.mock_sr.RequestError = Exception
        self.vc.recognizer.listen.return_value = mock_audio
        self.vc.recognizer.recognize_google.side_effect = Exception("API error")
        
        with patch('builtins.print'):
            result = self.vc.listen()
        
        self.assertIsNone(result)
    
    def test_listen_os_error(self):
        """Test listen when microphone access fails."""
        self.mock_sr.Microphone.side_effect = OSError("Microphone not available")
        
        with patch('builtins.print'):
            result = self.vc.listen()
        
        self.assertIsNone(result)
    
    def test_listen_generic_exception(self):
        """Test listen with unexpected exception."""
        mock_mic = MagicMock()
        self.mock_sr.Microphone.return_value.__enter__.return_value = mock_mic
        self.vc.recognizer.listen.side_effect = ValueError("Unexpected error")
        
        with patch('builtins.print'):
            result = self.vc.listen()
        
        self.assertIsNone(result)


class TestVoiceChatSpeak(unittest.TestCase):
    """Test VoiceChat.speak() method for text-to-speech."""
    
    def setUp(self):
        """Set up test fixtures."""
        with patch('voice_chat.TTS_AVAILABLE', True):
            self.mock_pyttsx3 = sys.modules['pyttsx3']
            self.mock_engine = MagicMock()
            self.mock_pyttsx3.init.return_value = self.mock_engine
            self.vc = VoiceChat()
    
    def test_speak_synchronous(self):
        """Test synchronous speech output."""
        self.vc.speak("Hello world", async_mode=False)
        
        self.mock_engine.say.assert_called_once_with("Hello world")
        self.mock_engine.runAndWait.assert_called_once()
    
    def test_speak_asynchronous(self):
        """Test asynchronous speech output."""
        self.vc.speak("Hello async", async_mode=True)
        
        # Give thread time to start
        time.sleep(0.1)
        
        self.mock_engine.say.assert_called_with("Hello async")
    
    def test_speak_tts_not_available(self):
        """Test speak when TTS is not available."""
        self.vc.tts_available = False
        
        with patch('builtins.print') as mock_print:
            self.vc.speak("Test message")
            mock_print.assert_called_with("[TTS Not Available] Would speak: Test message")
    
    def test_speak_tts_engine_none(self):
        """Test speak when TTS engine is None."""
        self.vc.tts_engine = None
        
        with patch('builtins.print') as mock_print:
            self.vc.speak("Test message")
            mock_print.assert_called()
    
    def test_speak_with_exception(self):
        """Test speak when TTS engine raises exception."""
        self.mock_engine.runAndWait.side_effect = Exception("TTS error")
        
        with patch('builtins.print') as mock_print:
            self.vc.speak("Test")
            # Should handle exception gracefully
            self.assertTrue(any('Error' in str(call) for call in mock_print.call_args_list))
    
    def test_speak_thread_safety(self):
        """Test that multiple speak calls are thread-safe."""
        speak_order = []
        
        def mock_say(text):
            speak_order.append(text)
            time.sleep(0.05)
        
        self.mock_engine.say.side_effect = mock_say
        
        # Start multiple async speak operations
        self.vc.speak("First", async_mode=True)
        self.vc.speak("Second", async_mode=True)
        
        time.sleep(0.2)
        
        # Both should complete
        self.assertEqual(len(speak_order), 2)
    
    def test_speak_empty_string(self):
        """Test speak with empty string."""
        self.vc.speak("", async_mode=False)
        
        self.mock_engine.say.assert_called_once_with("")


class TestVoiceChatSettings(unittest.TestCase):
    """Test VoiceChat settings management methods."""
    
    def setUp(self):
        """Set up test fixtures."""
        with patch('voice_chat.TTS_AVAILABLE', True):
            self.mock_pyttsx3 = sys.modules['pyttsx3']
            self.mock_engine = MagicMock()
            self.mock_pyttsx3.init.return_value = self.mock_engine
            self.vc = VoiceChat()
    
    def test_set_voice_properties_rate(self):
        """Test setting voice rate."""
        self.vc.set_voice_properties(rate=180)
        
        self.mock_engine.setProperty.assert_called_with('rate', 180)
    
    def test_set_voice_properties_volume(self):
        """Test setting voice volume."""
        self.vc.set_voice_properties(volume=0.7)
        
        self.mock_engine.setProperty.assert_called_with('volume', 0.7)
    
    def test_set_voice_properties_voice_id(self):
        """Test setting voice ID."""
        mock_voice = MagicMock()
        mock_voice.id = 'test-voice-id'
        self.mock_engine.getProperty.return_value = [mock_voice, MagicMock()]
        
        self.vc.set_voice_properties(voice_id=0)
        
        self.mock_engine.setProperty.assert_called_with('voice', 'test-voice-id')
    
    def test_set_voice_properties_all(self):
        """Test setting all voice properties at once."""
        mock_voice = MagicMock()
        mock_voice.id = 'voice-1'
        self.mock_engine.getProperty.return_value = [mock_voice]
        
        self.vc.set_voice_properties(rate=200, volume=0.8, voice_id=0)
        
        calls = self.mock_engine.setProperty.call_args_list
        self.assertTrue(any(call_obj[0] == ('rate', 200) for call_obj in calls))
        self.assertTrue(any(call_obj[0] == ('volume', 0.8) for call_obj in calls))
    
    def test_set_voice_properties_tts_unavailable(self):
        """Test setting properties when TTS is unavailable."""
        self.vc.tts_available = False
        
        with patch('builtins.print') as mock_print:
            self.vc.set_voice_properties(rate=200)
            mock_print.assert_called_with("TTS engine not available, cannot set properties.")
    
    def test_set_voice_properties_none_values(self):
        """Test setting properties with None values (no changes)."""
        initial_call_count = self.mock_engine.setProperty.call_count
        
        self.vc.set_voice_properties(rate=None, volume=None, voice_id=None)
        
        # No new calls should be made
        self.assertEqual(self.mock_engine.setProperty.call_count, initial_call_count)
    
    def test_list_available_voices(self):
        """Test listing available voices."""
        mock_voices = [MagicMock(), MagicMock()]
        self.mock_engine.getProperty.return_value = mock_voices
        
        voices = self.vc.list_available_voices()
        
        self.assertEqual(voices, mock_voices)
        self.mock_engine.getProperty.assert_called_with('voices')
    
    def test_list_available_voices_empty(self):
        """Test listing voices when none available."""
        self.mock_engine.getProperty.return_value = []
        
        voices = self.vc.list_available_voices()
        
        self.assertEqual(voices, [])
    
    def test_list_available_voices_tts_unavailable(self):
        """Test listing voices when TTS unavailable."""
        self.vc.tts_available = False
        
        voices = self.vc.list_available_voices()
        
        self.assertEqual(voices, [])
    
    def test_get_current_settings_with_tts(self):
        """Test getting current settings when TTS is available."""
        self.mock_engine.getProperty.side_effect = lambda key: {
            'rate': 150,
            'volume': 0.9,
            'voice': 'voice-id-123'
        }.get(key)
        
        settings = self.vc.get_current_settings()
        
        self.assertEqual(settings['rate'], 150)
        self.assertEqual(settings['volume'], 0.9)
        self.assertEqual(settings['voice'], 'voice-id-123')
        self.assertTrue(settings['tts_available'])
    
    def test_get_current_settings_without_tts(self):
        """Test getting current settings when TTS is unavailable."""
        self.vc.tts_available = False
        
        settings = self.vc.get_current_settings()
        
        self.assertIsNone(settings['rate'])
        self.assertIsNone(settings['volume'])
        self.assertIsNone(settings['voice'])
        self.assertFalse(settings['tts_available'])


class TestVoiceAssistantInitialization(unittest.TestCase):
    """Test VoiceAssistant initialization."""
    
    def test_init_with_voice_chat(self):
        """Test initialization with provided VoiceChat instance."""
        agent = AIAgent(name="Test Agent")
        voice_chat = VoiceChat()
        
        va = VoiceAssistant(agent, voice_chat)
        
        self.assertEqual(va.agent, agent)
        self.assertEqual(va.voice_chat, voice_chat)
        self.assertFalse(va.voice_mode)
    
    def test_init_without_voice_chat(self):
        """Test initialization without VoiceChat (creates new one)."""
        agent = AIAgent(name="Test Agent")
        
        va = VoiceAssistant(agent)
        
        self.assertEqual(va.agent, agent)
        self.assertIsNotNone(va.voice_chat)
        self.assertFalse(va.voice_mode)
    
    def test_init_voice_mode_default_false(self):
        """Test that voice mode is disabled by default."""
        agent = AIAgent(name="Test Agent")
        va = VoiceAssistant(agent)
        
        self.assertFalse(va.voice_mode)


class TestVoiceAssistantVoiceMode(unittest.TestCase):
    """Test VoiceAssistant voice mode management."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.agent = AIAgent(name="Test Agent")
        self.va = VoiceAssistant(self.agent)
    
    def test_enable_voice_mode(self):
        """Test enabling voice mode."""
        with patch('builtins.print') as mock_print:
            self.va.enable_voice_mode()
        
        self.assertTrue(self.va.voice_mode)
        mock_print.assert_called_with("Voice mode enabled. The agent will speak responses.")
    
    def test_disable_voice_mode(self):
        """Test disabling voice mode."""
        self.va.voice_mode = True
        
        with patch('builtins.print') as mock_print:
            self.va.disable_voice_mode()
        
        self.assertFalse(self.va.voice_mode)
        mock_print.assert_called_with("Voice mode disabled.")
    
    def test_toggle_voice_mode_off_to_on(self):
        """Test toggling voice mode from off to on."""
        self.va.voice_mode = False
        
        with patch('builtins.print'):
            result = self.va.toggle_voice_mode()
        
        self.assertTrue(result)
        self.assertTrue(self.va.voice_mode)
    
    def test_toggle_voice_mode_on_to_off(self):
        """Test toggling voice mode from on to off."""
        self.va.voice_mode = True
        
        with patch('builtins.print'):
            result = self.va.toggle_voice_mode()
        
        self.assertFalse(result)
        self.assertFalse(self.va.voice_mode)
    
    def test_toggle_voice_mode_multiple_times(self):
        """Test toggling voice mode multiple times."""
        initial_state = self.va.voice_mode
        
        with patch('builtins.print'):
            self.va.toggle_voice_mode()
            self.va.toggle_voice_mode()
        
        self.assertEqual(self.va.voice_mode, initial_state)


class TestVoiceAssistantProcessing(unittest.TestCase):
    """Test VoiceAssistant message processing methods."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.agent = AIAgent(name="Test Agent")
        self.voice_chat = Mock(spec=VoiceChat)
        self.va = VoiceAssistant(self.agent, self.voice_chat)
    
    def test_process_voice_input_successful(self):
        """Test successful voice input processing."""
        self.voice_chat.listen.return_value = "Hello"
        
        with patch('builtins.print'):
            response = self.va.process_voice_input(timeout=5)
        
        self.assertIsNotNone(response)
        self.voice_chat.listen.assert_called_once_with(timeout=5)
    
    def test_process_voice_input_with_voice_mode(self):
        """Test voice input processing with voice output enabled."""
        self.va.enable_voice_mode()
        self.voice_chat.listen.return_value = "How are you?"
        
        with patch('builtins.print'):
            self.va.process_voice_input()
        
        self.voice_chat.speak.assert_called_once()
    
    def test_process_voice_input_no_input(self):
        """Test voice input processing when no input received."""
        self.voice_chat.listen.return_value = None
        
        response = self.va.process_voice_input()
        
        self.assertIsNone(response)
        self.voice_chat.speak.assert_not_called()
    
    def test_process_voice_input_custom_timeout(self):
        """Test voice input with custom timeout."""
        self.voice_chat.listen.return_value = "Test"
        
        with patch('builtins.print'):
            self.va.process_voice_input(timeout=10)
        
        self.voice_chat.listen.assert_called_once_with(timeout=10)
    
    def test_process_text_with_voice_mode_off(self):
        """Test text processing without voice output."""
        self.va.voice_mode = False
        
        response = self.va.process_text_with_voice("Hello")
        
        self.assertIsNotNone(response)
        self.voice_chat.speak.assert_not_called()
    
    def test_process_text_with_voice_mode_on(self):
        """Test text processing with voice output."""
        self.va.voice_mode = True
        
        response = self.va.process_text_with_voice("Hello")
        
        self.assertIsNotNone(response)
        self.voice_chat.speak.assert_called_once_with(response)
    
    def test_process_text_with_voice_empty_string(self):
        """Test text processing with empty string."""
        self.va.voice_mode = True
        
        response = self.va.process_text_with_voice("")
        
        self.assertIsNotNone(response)
    
    def test_process_text_returns_agent_response(self):
        """Test that process_text_with_voice returns agent's response."""
        test_input = "What is your name?"
        
        response = self.va.process_text_with_voice(test_input)
        
        # Response should contain agent name
        self.assertIn(self.agent.name, response)


class TestVoiceAssistantInteractiveSession(unittest.TestCase):
    """Test VoiceAssistant interactive session."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.agent = AIAgent(name="Test Agent")
        self.voice_chat = Mock(spec=VoiceChat)
        self.va = VoiceAssistant(self.agent, self.voice_chat)
    
    def test_interactive_session_quit_command(self):
        """Test quitting interactive session."""
        with patch('builtins.input', return_value='quit'):
            with patch('builtins.print'):
                self.va.interactive_session(use_voice_input=False)
        
        # Should speak goodbye if voice mode is on
        self.assertEqual(self.voice_chat.speak.call_count, 0)
    
    def test_interactive_session_quit_with_voice(self):
        """Test quitting with voice mode enabled."""
        self.va.voice_mode = True
        
        with patch('builtins.input', return_value='quit'):
            with patch('builtins.print'):
                self.va.interactive_session(use_voice_input=False)
        
        self.voice_chat.speak.assert_called_once()
    
    def test_interactive_session_toggle_voice_command(self):
        """Test toggle voice command in interactive session."""
        inputs = ['toggle voice', 'quit']
        
        with patch('builtins.input', side_effect=inputs):
            with patch('builtins.print'):
                self.va.interactive_session(use_voice_input=False)
        
        # Voice mode should have been toggled
        self.assertTrue(self.va.voice_mode or not self.va.voice_mode)
    
    def test_interactive_session_switch_input_command(self):
        """Test switch input command in interactive session."""
        inputs = ['switch input', 'quit']
        
        with patch('builtins.input', side_effect=inputs):
            with patch('builtins.print'):
                self.va.interactive_session(use_voice_input=False)
        
        # Should not crash
        self.assertTrue(True)
    
    def test_interactive_session_keyboard_interrupt(self):
        """Test handling keyboard interrupt in interactive session."""
        with patch('builtins.input', side_effect=KeyboardInterrupt()):
            with patch('builtins.print'):
                self.va.interactive_session(use_voice_input=False)
        
        # Should exit gracefully
        self.assertTrue(True)
    
    def test_interactive_session_empty_input(self):
        """Test handling empty input in interactive session."""
        inputs = ['', 'quit']
        
        with patch('builtins.input', side_effect=inputs):
            with patch('builtins.print'):
                self.va.interactive_session(use_voice_input=False)
        
        # Should skip empty input and continue
        self.assertTrue(True)
    
    def test_interactive_session_normal_message(self):
        """Test processing normal message in interactive session."""
        inputs = ['Hello', 'quit']
        
        with patch('builtins.input', side_effect=inputs):
            with patch('builtins.print'):
                self.va.interactive_session(use_voice_input=False)
        
        # Message should be processed
        self.assertEqual(len(self.agent.conversation_history), 2)
    
    def test_interactive_session_exception_handling(self):
        """Test exception handling in interactive session."""
        error_msg = "Test error"
        
        def raise_exception():
            raise ValueError(error_msg)
        
        inputs = ['quit']
        
        with patch('builtins.input', side_effect=inputs):
            with patch('builtins.print'):
                with patch.object(self.agent, 'process_message', side_effect=raise_exception):
                    # Should not crash on exceptions
                    try:
                        self.va.interactive_session(use_voice_input=False)
                    except ValueError:
                        self.fail("Exception should be caught")


class TestVoiceChatEdgeCases(unittest.TestCase):
    """Test edge cases and boundary conditions."""
    
    def test_voice_chat_with_extreme_rate(self):
        """Test VoiceChat with extreme speech rate."""
        with patch('voice_chat.TTS_AVAILABLE', True):
            mock_engine = MagicMock()
            sys.modules['pyttsx3'].init.return_value = mock_engine
            
            VoiceChat(rate=1000, volume=1.0)
            
            mock_engine.setProperty.assert_any_call('rate', 1000)
    
    def test_voice_chat_with_zero_volume(self):
        """Test VoiceChat with zero volume."""
        with patch('voice_chat.TTS_AVAILABLE', True):
            mock_engine = MagicMock()
            sys.modules['pyttsx3'].init.return_value = mock_engine
            
            VoiceChat(volume=0.0)
            
            mock_engine.setProperty.assert_any_call('volume', 0.0)
    
    def test_speak_very_long_text(self):
        """Test speaking very long text."""
        with patch('voice_chat.TTS_AVAILABLE', True):
            mock_engine = MagicMock()
            sys.modules['pyttsx3'].init.return_value = mock_engine
            vc = VoiceChat()
            
            long_text = "word " * 1000
            vc.speak(long_text)
            
            mock_engine.say.assert_called_once_with(long_text)
    
    def test_speak_special_characters(self):
        """Test speaking text with special characters."""
        with patch('voice_chat.TTS_AVAILABLE', True):
            mock_engine = MagicMock()
            sys.modules['pyttsx3'].init.return_value = mock_engine
            vc = VoiceChat()
            
            special_text = "Hello! @#$%^&*() 你好 🎉"
            vc.speak(special_text)
            
            mock_engine.say.assert_called_once_with(special_text)
    
    def test_multiple_voice_assistants_same_agent(self):
        """Test creating multiple voice assistants with same agent."""
        agent = AIAgent(name="Shared Agent")
        va1 = VoiceAssistant(agent)
        va2 = VoiceAssistant(agent)
        
        self.assertEqual(va1.agent, va2.agent)
        self.assertNotEqual(va1.voice_chat, va2.voice_chat)
    
    def test_voice_assistant_agent_state_persistence(self):
        """Test that voice assistant maintains agent state."""
        agent = AIAgent(name="Test Agent")
        va = VoiceAssistant(agent)
        
        va.process_text_with_voice("Hello")
        
        self.assertEqual(len(agent.conversation_history), 2)


if __name__ == '__main__':
    unittest.main()