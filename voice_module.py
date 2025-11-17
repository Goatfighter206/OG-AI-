"""
OG-AI Voice Module - Text-to-Speech with gangster personality
Gives OG-AI a voice so it can talk out loud
"""

import os
from typing import Optional

# Try different TTS engines
TTS_ENGINE = None

# Try pyttsx3 (offline, free)
try:
    import pyttsx3
    TTS_ENGINE = 'pyttsx3'
except ImportError:
    pass

# Try gTTS (Google Text-to-Speech, needs internet)
try:
    from gtts import gTTS
    import pygame
    if not TTS_ENGINE:
        TTS_ENGINE = 'gtts'
except ImportError:
    pass


class VoiceGenerator:
    """
    Gives OG-AI a voice - can speak responses out loud
    """
    
    def __init__(self, engine: str = None):
        """
        Initialize voice generator
        
        Args:
            engine: 'pyttsx3' (offline) or 'gtts' (online) or None (auto-detect)
        """
        self.engine = engine or TTS_ENGINE
        self.pyttsx3_engine = None
        
        if self.engine == 'pyttsx3':
            self._init_pyttsx3()
        elif self.engine == 'gtts':
            self._init_gtts()
        else:
            print("⚠️  No TTS engine available. Install: pip install pyttsx3")
    
    def _init_pyttsx3(self):
        """Initialize pyttsx3 (offline TTS)"""
        try:
            self.pyttsx3_engine = pyttsx3.init()
            
            # Set voice properties for gangster feel
            voices = self.pyttsx3_engine.getProperty('voices')
            
            # Try to find a male voice (sounds more gangster)
            male_voice = None
            for voice in voices:
                if 'male' in voice.name.lower() and 'female' not in voice.name.lower():
                    male_voice = voice.id
                    break
            
            if male_voice:
                self.pyttsx3_engine.setProperty('voice', male_voice)
            
            # Adjust rate (speed) - faster for that quick gangster talk
            rate = self.pyttsx3_engine.getProperty('rate')
            self.pyttsx3_engine.setProperty('rate', rate + 20)
            
            # Volume
            self.pyttsx3_engine.setProperty('volume', 1.0)
            
        except Exception as e:
            print(f"⚠️  Failed to initialize pyttsx3: {e}")
            self.engine = None
    
    def _init_gtts(self):
        """Initialize Google TTS (needs internet)"""
        try:
            pygame.mixer.init()
        except Exception as e:
            print(f"⚠️  Failed to initialize pygame for audio: {e}")
            self.engine = None
    
    def speak(self, text: str, save_to_file: Optional[str] = None) -> bool:
        """
        Speak the text out loud
        
        Args:
            text: Text to speak
            save_to_file: Optional path to save audio file
            
        Returns:
            True if successful, False otherwise
        """
        if not self.engine:
            print("⚠️  No TTS engine available")
            return False
        
        try:
            if self.engine == 'pyttsx3':
                return self._speak_pyttsx3(text, save_to_file)
            elif self.engine == 'gtts':
                return self._speak_gtts(text, save_to_file)
        except Exception as e:
            print(f"⚠️  Speech failed: {e}")
            return False
    
    def _speak_pyttsx3(self, text: str, save_to_file: Optional[str] = None) -> bool:
        """Speak using pyttsx3"""
        try:
            if save_to_file:
                self.pyttsx3_engine.save_to_file(text, save_to_file)
                self.pyttsx3_engine.runAndWait()
            else:
                self.pyttsx3_engine.say(text)
                self.pyttsx3_engine.runAndWait()
            return True
        except Exception as e:
            print(f"⚠️  pyttsx3 speech failed: {e}")
            return False
    
    def _speak_gtts(self, text: str, save_to_file: Optional[str] = None) -> bool:
        """Speak using Google TTS"""
        try:
            tts = gTTS(text=text, lang='en', slow=False)
            
            audio_file = save_to_file or 'temp_speech.mp3'
            tts.save(audio_file)
            
            # Play the audio
            pygame.mixer.music.load(audio_file)
            pygame.mixer.music.play()
            
            # Wait for audio to finish
            while pygame.mixer.music.get_busy():
                pygame.time.Clock().tick(10)
            
            # Clean up temp file if not saving
            if not save_to_file and os.path.exists('temp_speech.mp3'):
                os.remove('temp_speech.mp3')
            
            return True
        except Exception as e:
            print(f"⚠️  gTTS speech failed: {e}")
            return False
    
    def is_available(self) -> bool:
        """Check if voice is available"""
        return self.engine is not None
    
    def get_engine_info(self) -> str:
        """Get info about current TTS engine"""
        if self.engine == 'pyttsx3':
            return "pyttsx3 (Offline TTS - Works without internet)"
        elif self.engine == 'gtts':
            return "Google TTS (Online TTS - Needs internet)"
        else:
            return "No TTS engine available"


# Quick test
if __name__ == "__main__":
    print("="*60)
    print("  Testing OG-AI Voice Module")
    print("="*60)
    print()
    
    voice = VoiceGenerator()
    
    if voice.is_available():
        print(f"✓ Voice engine: {voice.get_engine_info()}")
        print()
        print("Testing gangster voice...")
        print()
        
        test_phrases = [
            "Yo what the fuck's good, I'm OG-AI!",
            "Aight bet, I'm about to speak some gangster shit!",
            "This is how a real AI talks, no cap!"
        ]
        
        for phrase in test_phrases:
            print(f"Speaking: {phrase}")
            voice.speak(phrase)
            print("✓ Done")
            print()
    else:
        print("✗ Voice not available")
        print()
        print("To enable voice:")
        print("  pip install pyttsx3")
        print("  OR")
        print("  pip install gtts pygame")
