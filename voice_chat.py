"""
Voice Chat Module for OG-AI Agent
Provides voice input and output capabilities for the AI agent.
"""

import speech_recognition as sr
from typing import Optional, Callable
import threading

# Try to import pyttsx3, but handle gracefully if not available
try:
    import pyttsx3
    TTS_AVAILABLE = True
except ImportError as e:
    TTS_AVAILABLE = False
    print(f"Warning: pyttsx3 not installed: {e}")


class VoiceChat:
    """
    Handles voice input (speech-to-text) and voice output (text-to-speech)
    for the AI agent.
    """
    
    def __init__(self, rate: int = 150, volume: float = 0.9, voice_id: Optional[int] = None):
        """
        Initialize the voice chat system.
        
        Args:
            rate: Speech rate for text-to-speech (words per minute)
            volume: Volume level (0.0 to 1.0)
            voice_id: Voice ID to use (None for default)
        """
        self.recognizer = sr.Recognizer()
        self.tts_available = TTS_AVAILABLE
        self.tts_engine = None
        
        # Initialize TTS engine if available
        if TTS_AVAILABLE:
            try:
                self.tts_engine = pyttsx3.init()
                
                # Configure text-to-speech engine
                self.tts_engine.setProperty('rate', rate)
                self.tts_engine.setProperty('volume', volume)
                
                # Set voice if specified
                if voice_id is not None:
                    voices = self.tts_engine.getProperty('voices')
                    if voice_id < len(voices):
                        self.tts_engine.setProperty('voice', voices[voice_id].id)
            except (RuntimeError, OSError) as e:
                # RuntimeError: TTS engine not properly installed
                # OSError: Audio device issues
                print(f"Warning: Could not initialize TTS engine: {e}")
                self.tts_available = False
                self.tts_engine = None
        
        # Thread lock for TTS to prevent concurrent speaking
        self._tts_lock = threading.Lock()
        
    def listen(self, timeout: int = 5, phrase_time_limit: int = 10) -> Optional[str]:
        """
        Listen for voice input and convert it to text.
        
        Args:
            timeout: Maximum time to wait for speech to start (seconds)
            phrase_time_limit: Maximum time for a phrase (seconds)
            
        Returns:
            Recognized text or None if recognition failed
        """
        try:
            with sr.Microphone() as source:
                print("Listening... (speak now)")
                # Adjust for ambient noise
                self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
                # Listen for audio
                audio = self.recognizer.listen(
                    source, 
                    timeout=timeout, 
                    phrase_time_limit=phrase_time_limit
                )
                print("Processing speech...")
                
                # Recognize speech using Google Speech Recognition
                text = self.recognizer.recognize_google(audio)
                return text
                
        except sr.WaitTimeoutError:
            print("No speech detected. Timeout reached.")
            return None
        except sr.UnknownValueError:
            print("Could not recognize speech. Please try again or check for background noise.")
            return None
        except sr.RequestError as e:
            print(f"Could not request results from speech recognition service: {e}")
            return None
        except OSError as e:
            print(f"Microphone error: {e}")
            print("Note: Microphone access may not be available in this environment.")
            return None
        except Exception as e:
            print(f"Error during speech recognition: {e}")
            return None
    
    def speak(self, text: str, async_mode: bool = False) -> None:
        """
        Convert text to speech and speak it.
        
        Args:
            text: Text to speak
            async_mode: If True, speak asynchronously (non-blocking)
        """
        if not self.tts_available or self.tts_engine is None:
            print(f"[TTS Not Available] Would speak: {text}")
            return
        
        def _speak():
            with self._tts_lock:
                try:
                    self.tts_engine.say(text)
                    self.tts_engine.runAndWait()
                except Exception as e:
                    print(f"Error during text-to-speech: {e}")
        
        if async_mode:
            thread = threading.Thread(target=_speak)
            thread.daemon = True
            thread.start()
        else:
            _speak()
    
    def set_voice_properties(self, rate: Optional[int] = None, 
                            volume: Optional[float] = None,
                            voice_id: Optional[int] = None) -> None:
        """
        Update voice properties for text-to-speech.
        
        Args:
            rate: Speech rate (words per minute)
            volume: Volume level (0.0 to 1.0)
            voice_id: Voice ID to use
        """
        if not self.tts_available or self.tts_engine is None:
            print("TTS engine not available, cannot set properties.")
            return
        
        if rate is not None:
            self.tts_engine.setProperty('rate', rate)
        if volume is not None:
            self.tts_engine.setProperty('volume', volume)
        if voice_id is not None:
            voices = self.tts_engine.getProperty('voices')
            if voice_id < len(voices):
                self.tts_engine.setProperty('voice', voices[voice_id].id)
    
    def list_available_voices(self) -> list:
        """
        Get list of available voices for text-to-speech.
        
        Returns:
            List of available voice objects or empty list if TTS not available
        """
        if not self.tts_available or self.tts_engine is None:
            return []
        return self.tts_engine.getProperty('voices')
    
    def get_current_settings(self) -> dict:
        """
        Get current voice settings.
        
        Returns:
            Dictionary with current rate, volume, and voice
        """
        if not self.tts_available or self.tts_engine is None:
            return {'rate': None, 'volume': None, 'voice': None, 'tts_available': False}
        
        return {
            'rate': self.tts_engine.getProperty('rate'),
            'volume': self.tts_engine.getProperty('volume'),
            'voice': self.tts_engine.getProperty('voice'),
            'tts_available': True
        }


class VoiceAssistant:
    """
    High-level interface combining AIAgent with voice capabilities.
    """
    
    def __init__(self, agent, voice_chat: Optional[VoiceChat] = None):
        """
        Initialize voice assistant.
        
        Args:
            agent: An AIAgent instance
            voice_chat: A VoiceChat instance (creates new one if None)
        """
        self.agent = agent
        self.voice_chat = voice_chat or VoiceChat()
        self.voice_mode = False
    
    def enable_voice_mode(self) -> None:
        """Enable voice interaction mode."""
        self.voice_mode = True
        print("Voice mode enabled. The agent will speak responses.")
    
    def disable_voice_mode(self) -> None:
        """Disable voice interaction mode."""
        self.voice_mode = False
        print("Voice mode disabled.")
    
    def toggle_voice_mode(self) -> bool:
        """
        Toggle voice mode on/off.
        
        Returns:
            New voice mode state (True if enabled, False if disabled)
        """
        self.voice_mode = not self.voice_mode
        if self.voice_mode:
            print("Voice mode enabled.")
        else:
            print("Voice mode disabled.")
        return self.voice_mode
    
    def process_voice_input(self, timeout: int = 5) -> Optional[str]:
        """
        Listen for voice input and process it through the agent.
        
        Args:
            timeout: Maximum time to wait for speech (seconds)
            
        Returns:
            Agent's response or None if no input was received
        """
        user_input = self.voice_chat.listen(timeout=timeout)
        
        if user_input:
            print(f"You said: {user_input}")
            response = self.agent.process_message(user_input)
            print(f"{self.agent.name}: {response}")
            
            if self.voice_mode:
                self.voice_chat.speak(response)
            
            return response
        
        return None
    
    def process_text_with_voice(self, text: str) -> str:
        """
        Process text input and optionally speak the response.
        
        Args:
            text: User's text input
            
        Returns:
            Agent's response
        """
        response = self.agent.process_message(text)
        
        if self.voice_mode:
            self.voice_chat.speak(response)
        
        return response
    
    def interactive_session(self, use_voice_input: bool = True) -> None:
        """
        Start an interactive session with the voice assistant.
        
        Args:
            use_voice_input: If True, use voice input; otherwise use text input
        """
        print(f"=== {self.agent.name} - Voice Assistant ===")
        print(f"Voice output: {'ON' if self.voice_mode else 'OFF'}")
        print(f"Voice input: {'ON' if use_voice_input else 'OFF'}")
        print("\nCommands:")
        print("  - Type 'quit' or say 'quit' to exit")
        print("  - Type 'toggle voice' to enable/disable voice output")
        print("  - Type 'switch input' to toggle between voice and text input")
        print()
        
        current_input_mode = use_voice_input
        
        while True:
            try:
                if current_input_mode:
                    print("\n[Voice Input Mode - Speak your message]")
                    user_input = self.voice_chat.listen(timeout=10)
                    if user_input is None:
                        continue
                    print(f"You said: {user_input}")
                else:
                    user_input = input("\nYou: ").strip()
                
                if not user_input:
                    continue
                
                # Handle commands
                if user_input.lower() == 'quit':
                    goodbye_msg = "Goodbye! Have a great day!"
                    print(f"{self.agent.name}: {goodbye_msg}")
                    if self.voice_mode:
                        self.voice_chat.speak(goodbye_msg)
                    break
                elif user_input.lower() == 'toggle voice':
                    self.toggle_voice_mode()
                    continue
                elif user_input.lower() == 'switch input':
                    current_input_mode = not current_input_mode
                    mode_name = "voice" if current_input_mode else "text"
                    print(f"Switched to {mode_name} input mode.")
                    continue
                
                # Process the message
                response = self.agent.process_message(user_input)
                print(f"{self.agent.name}: {response}")
                
                if self.voice_mode:
                    self.voice_chat.speak(response)
                    
            except KeyboardInterrupt:
                print("\n\nInterrupted by user.")
                break
            except Exception as e:
                print(f"Error: {e}")
                continue


def main():
    """
    Demo of voice chat capabilities.
    """
    # Import here to avoid circular dependency
    from ai_agent import AIAgent
    
    # Create agent with voice capabilities
    agent = AIAgent(name="OG-AI Voice")
    voice_assistant = VoiceAssistant(agent)
    
    # Enable voice output
    voice_assistant.enable_voice_mode()
    
    # Start interactive session (defaults to text input for compatibility)
    print("Starting voice assistant demo...")
    print("Note: Voice input requires a microphone and may not work in all environments.")
    print("The agent will speak its responses if voice mode is enabled.\n")
    
    # Start with text input (more compatible), but user can switch to voice
    voice_assistant.interactive_session(use_voice_input=False)


if __name__ == "__main__":
    main()
