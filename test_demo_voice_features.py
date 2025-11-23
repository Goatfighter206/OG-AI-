"""
Unit tests for demo_voice_features.py module.
Tests the demo functions to ensure they work correctly.
"""

import unittest
from unittest.mock import patch, MagicMock
import sys
from io import StringIO

# Mock dependencies before importing
sys.modules['speech_recognition'] = MagicMock()
sys.modules['pyttsx3'] = MagicMock()
sys.modules['flask'] = MagicMock()
sys.modules['flask_cors'] = MagicMock()

from demo_voice_features import (
    print_section,
    demo_text_mode,
    demo_voice_mode,
    demo_voice_settings,
    demo_conversation_modes,
    demo_conversation_history,
    print_usage_info,
    main
)


class TestPrintSection(unittest.TestCase):
    """Test the print_section helper function."""
    
    def test_print_section_output(self):
        """Test that print_section outputs formatted header."""
        with patch('sys.stdout', new=StringIO()) as fake_out:
            print_section("Test Section")
            output = fake_out.getvalue()
            
            self.assertIn("Test Section", output)
            self.assertIn("=" * 70, output)
    
    def test_print_section_different_titles(self):
        """Test print_section with different titles."""
        titles = ["Demo 1", "Demo 2", "Another Section"]
        
        for title in titles:
            with patch('sys.stdout', new=StringIO()) as fake_out:
                print_section(title)
                output = fake_out.getvalue()
                self.assertIn(title, output)


class TestDemoTextMode(unittest.TestCase):
    """Test demo_text_mode function."""
    
    @patch('demo_voice_features.AIAgent')
    @patch('demo_voice_features.print_section')
    @patch('builtins.print')
    def test_demo_text_mode_creates_agent(self, _mock_print, _mock_print_section, mock_agent_class):
        """Test that demo_text_mode creates an agent."""
        mock_agent = MagicMock()
        mock_agent.name = "OG-AI Text"
        mock_agent.voice_enabled = False
        mock_agent.process_message.return_value = "Test response"
        mock_agent_class.return_value = mock_agent
        
        demo_text_mode()
        
        mock_agent_class.assert_called_once_with(name="OG-AI Text")
    
    @patch('demo_voice_features.AIAgent')
    @patch('demo_voice_features.print_section')
    @patch('builtins.print')
    def test_demo_text_mode_processes_messages(self, _mock_print, _mock_print_section, mock_agent_class):
        """Test that demo_text_mode processes all test messages."""
        mock_agent = MagicMock()
        mock_agent.name = "OG-AI Text"
        mock_agent.voice_enabled = False
        mock_agent.process_message.return_value = "Response"
        mock_agent_class.return_value = mock_agent
        
        demo_text_mode()
        
        # Should process 3 messages
        self.assertEqual(mock_agent.process_message.call_count, 3)


class TestDemoVoiceMode(unittest.TestCase):
    """Test demo_voice_mode function."""
    
    @patch('demo_voice_features.VOICE_AVAILABLE', False)
    @patch('demo_voice_features.print_section')
    @patch('builtins.print')
    def test_demo_voice_mode_unavailable(self, mock_print, _mock_print_section):
        """Test demo_voice_mode when voice is unavailable."""
        demo_voice_mode()
        
        # Should print warning message
        calls = [str(c) for c in mock_print.call_args_list]
        self.assertTrue(any('not available' in str(c).lower() for c in calls))
    
    @patch('demo_voice_features.VOICE_AVAILABLE', True)
    @patch('demo_voice_features.AIAgent')
    @patch('demo_voice_features.print_section')
    @patch('builtins.print')
    def test_demo_voice_mode_available(self, _mock_print, _mock_print_section, mock_agent_class):
        """Test demo_voice_mode when voice is available."""
        mock_agent = MagicMock()
        mock_agent.name = "OG-AI Voice"
        mock_agent.voice_enabled = True
        mock_agent.voice_assistant = MagicMock()
        mock_agent_class.return_value = mock_agent
        
        with patch('time.sleep'):
            demo_voice_mode()
        
        mock_agent_class.assert_called_once()
        mock_agent.voice_assistant.enable_voice_mode.assert_called_once()


class TestDemoVoiceSettings(unittest.TestCase):
    """Test demo_voice_settings function."""
    
    @patch('demo_voice_features.VOICE_AVAILABLE', False)
    @patch('demo_voice_features.print_section')
    @patch('builtins.print')
    def test_demo_voice_settings_unavailable(self, mock_print, _mock_print_section):
        """Test demo_voice_settings when voice is unavailable."""
        demo_voice_settings()
        
        # Should print warning and return early
        calls = [str(c) for c in mock_print.call_args_list]
        self.assertTrue(any('not available' in str(c).lower() for c in calls))
    
    @patch('demo_voice_features.VOICE_AVAILABLE', True)
    @patch('voice_chat.VoiceChat')
    @patch('demo_voice_features.print_section')
    @patch('builtins.print')
    def test_demo_voice_settings_available(self, _mock_print, _mock_print_section, mock_voice_chat_class):
        """Test demo_voice_settings when voice is available."""
        mock_vc = MagicMock()
        mock_vc.get_current_settings.return_value = {
            'tts_available': True,
            'rate': 150,
            'volume': 0.9
        }
        mock_vc.list_available_voices.return_value = []
        mock_vc.tts_available = False
        mock_voice_chat_class.return_value = mock_vc
        
        demo_voice_settings()
        
        mock_voice_chat_class.assert_called_once_with(rate=150, volume=0.9)
        mock_vc.get_current_settings.assert_called_once()


class TestDemoConversationModes(unittest.TestCase):
    """Test demo_conversation_modes function."""
    
    @patch('demo_voice_features.VOICE_AVAILABLE', False)
    @patch('demo_voice_features.print_section')
    @patch('builtins.print')
    def test_demo_conversation_modes_unavailable(self, mock_print, _mock_print_section):
        """Test demo_conversation_modes when voice unavailable."""
        demo_conversation_modes()
        
        calls = [str(c) for c in mock_print.call_args_list]
        self.assertTrue(any('not available' in str(c).lower() for c in calls))
    
    @patch('demo_voice_features.VOICE_AVAILABLE', True)
    @patch('demo_voice_features.AIAgent')
    @patch('voice_chat.VoiceAssistant')
    @patch('demo_voice_features.print_section')
    @patch('builtins.print')
    def test_demo_conversation_modes_available(self, _mock_print, _mock_print_section, 
                                               mock_va_class, mock_agent_class):
        """Test demo_conversation_modes when voice available."""
        mock_agent = MagicMock()
        mock_va = MagicMock()
        mock_agent_class.return_value = mock_agent
        mock_va_class.return_value = mock_va
        
        demo_conversation_modes()
        
        mock_va.enable_voice_mode.assert_called()
        mock_va.disable_voice_mode.assert_called()


class TestDemoConversationHistory(unittest.TestCase):
    """Test demo_conversation_history function."""
    
    @patch('demo_voice_features.AIAgent')
    @patch('demo_voice_features.print_section')
    @patch('builtins.print')
    def test_demo_conversation_history_saves_file(self, _mock_print, _mock_print_section, mock_agent_class):
        """Test that demo saves conversation to file."""
        mock_agent = MagicMock()
        mock_agent.name = "OG-AI History"
        mock_agent.process_message.return_value = "Response"
        mock_agent.get_conversation_history.return_value = [
            {'role': 'user', 'content': 'Hello'},
            {'role': 'assistant', 'content': 'Hi'}
        ]
        mock_agent_class.return_value = mock_agent
        
        demo_conversation_history()
        
        mock_agent.save_conversation.assert_called_once()
        # Should save to ./demo_conversation.json
        call_args = mock_agent.save_conversation.call_args[0][0]
        self.assertIn('demo_conversation.json', call_args)
    
    @patch('demo_voice_features.AIAgent')
    @patch('demo_voice_features.print_section')
    @patch('builtins.print')
    def test_demo_conversation_history_processes_messages(self, _mock_print, 
                                                         _mock_print_section, mock_agent_class):
        """Test that demo processes all conversation messages."""
        mock_agent = MagicMock()
        mock_agent.process_message.return_value = "Response"
        mock_agent.get_conversation_history.return_value = []
        mock_agent_class.return_value = mock_agent
        
        demo_conversation_history()
        
        # Should process 4 messages
        self.assertEqual(mock_agent.process_message.call_count, 4)


class TestPrintUsageInfo(unittest.TestCase):
    """Test print_usage_info function."""
    
    @patch('demo_voice_features.print_section')
    @patch('builtins.print')
    def test_print_usage_info_output(self, mock_print, _mock_print_section):
        """Test that usage info is printed."""
        print_usage_info()
        
        # Should call print multiple times with usage information
        self.assertGreater(mock_print.call_count, 5)


class TestMainFunction(unittest.TestCase):
    """Test main function."""
    
    @patch('demo_voice_features.VOICE_AVAILABLE', True)
    @patch('demo_voice_features.demo_text_mode')
    @patch('demo_voice_features.demo_voice_mode')
    @patch('demo_voice_features.demo_voice_settings')
    @patch('demo_voice_features.demo_conversation_modes')
    @patch('demo_voice_features.demo_conversation_history')
    @patch('demo_voice_features.print_usage_info')
    @patch('demo_voice_features.print_section')
    @patch('time.sleep')
    @patch('builtins.print')
    def test_main_runs_all_demos(self, _mock_print, _mock_sleep, _mock_print_section,
                                mock_usage, mock_history, mock_modes, 
                                mock_settings, mock_voice, mock_text):
        """Test that main runs all demo functions."""
        result = main()
        
        mock_text.assert_called_once()
        mock_voice.assert_called_once()
        mock_settings.assert_called_once()
        mock_modes.assert_called_once()
        mock_history.assert_called_once()
        mock_usage.assert_called_once()
        self.assertEqual(result, 0)
    
    @patch('demo_voice_features.VOICE_AVAILABLE', False)
    @patch('demo_voice_features.demo_text_mode')
    @patch('demo_voice_features.print_section')
    @patch('time.sleep')
    @patch('builtins.print')
    def test_main_with_voice_unavailable(self, mock_print, _mock_sleep, 
                                        _mock_print_section, _mock_text):
        """Test main when voice is unavailable."""
        result = main()
        
        # Should still run and show warning
        calls = [str(c) for c in mock_print.call_args_list]
        self.assertTrue(any('not fully available' in str(c).lower() for c in calls))
        self.assertEqual(result, 0)
    
    @patch('demo_voice_features.demo_text_mode')
    @patch('demo_voice_features.print_section')
    @patch('time.sleep')
    @patch('builtins.print')
    def test_main_keyboard_interrupt(self, mock_print, _mock_sleep, 
                                    _mock_print_section, mock_text):
        """Test main handles keyboard interrupt."""
        mock_text.side_effect = KeyboardInterrupt()
        
        result = main()
        
        self.assertEqual(result, 1)
        calls = [str(c) for c in mock_print.call_args_list]
        self.assertTrue(any('interrupted' in str(c).lower() for c in calls))
    
    @patch('demo_voice_features.demo_text_mode')
    @patch('demo_voice_features.print_section')
    @patch('time.sleep')
    @patch('builtins.print')
    def test_main_exception_handling(self, mock_print, _mock_sleep, 
                                    _mock_print_section, mock_text):
        """Test main handles exceptions."""
        mock_text.side_effect = ValueError("Test error")
        
        result = main()
        
        self.assertEqual(result, 1)
        calls = [str(c) for c in mock_print.call_args_list]
        self.assertTrue(any('error' in str(c).lower() for c in calls))


class TestDemoIntegration(unittest.TestCase):
    """Integration tests for demo functions."""
    
    @patch('demo_voice_features.VOICE_AVAILABLE', True)
    @patch('demo_voice_features.AIAgent')
    @patch('voice_chat.VoiceChat')
    @patch('voice_chat.VoiceAssistant')
    @patch('time.sleep')
    @patch('builtins.print')
    def test_demos_work_together(self, _mock_print, _mock_sleep, mock_va_class,
                                 mock_vc_class, mock_agent_class):
        """Test that all demos can run in sequence."""
        # Set up mocks
        mock_agent = MagicMock()
        mock_agent.name = "Test"
        mock_agent.voice_enabled = True
        mock_agent.process_message.return_value = "Response"
        mock_agent.get_conversation_history.return_value = []
        mock_agent_class.return_value = mock_agent
        
        mock_vc = MagicMock()
        mock_vc.get_current_settings.return_value = {'tts_available': False}
        mock_vc.list_available_voices.return_value = []
        mock_vc.tts_available = False
        mock_vc_class.return_value = mock_vc
        
        mock_va = MagicMock()
        mock_va_class.return_value = mock_va
        
        # Run demos
        with patch('demo_voice_features.print_section'):
            demo_text_mode()
            demo_voice_mode()
            demo_voice_settings()
            demo_conversation_modes()
            demo_conversation_history()
            print_usage_info()
        
        # Verify they all executed without errors
        self.assertTrue(True)


if __name__ == '__main__':
    unittest.main()