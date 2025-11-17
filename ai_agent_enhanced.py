"""
OG-AI Agent Enhanced - A smart-ass AI with real intelligence and internet access
Now with swearing, ghetto flair, actual brain cells, VOICE, and SELF-IMPROVEMENT
"""

import json
import os
import re
import subprocess
from typing import List, Dict, Optional, Any
from datetime import datetime
from dotenv import load_dotenv
import random

# Load environment variables
load_dotenv()

# AI Model Clients
try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    from anthropic import Anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

try:
    import ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False

# Web Search and Internet
try:
    from duckduckgo_search import DDGS
    DDGS_AVAILABLE = True
except ImportError:
    DDGS_AVAILABLE = False

try:
    import wikipedia
    WIKIPEDIA_AVAILABLE = True
except ImportError:
    WIKIPEDIA_AVAILABLE = False

try:
    from bs4 import BeautifulSoup
    import requests as web_requests
    WEB_SCRAPING_AVAILABLE = True
except ImportError:
    WEB_SCRAPING_AVAILABLE = False

# Voice and Self-Learning
try:
    from voice_module import VoiceGenerator
    VOICE_AVAILABLE = True
except ImportError:
    VOICE_AVAILABLE = False
    print("⚠️  Voice module not available - install: pip install pyttsx3")

try:
    from self_learning import SelfLearningSystem
    SELF_LEARNING_AVAILABLE = True
except ImportError:
    SELF_LEARNING_AVAILABLE = False
    print("⚠️  Self-learning module not available")

# Code Generation
try:
    from llm_code_generator import get_code_generator
    CODE_GEN_AVAILABLE = True
except ImportError:
    CODE_GEN_AVAILABLE = False
    print("⚠️  Code generator module not available")


class EnhancedAIAgent:
    """
    Enhanced AI Agent with HARDCORE intelligence, web access, gangster personality, 
    VOICE, and SELF-LEARNING/IMPROVEMENT capabilities
    """
    """

    # GANGSTER DICTIONARY - Street Slang & Hood Talk
    GANGSTER_SLANG = {
        'greetings': [
            "Yo what the fuck's good, I'm {name}. What you need from ya boy?",
            "Ayyy wassup motherfucka, {name} in this bitch. How can I help your ass?",
            "Yo yo yo, it's the realest AI in the game. I'm {name}, whatchu want homie?",
            "Sup G, {name} here. Ready to drop some fire knowledge on you, no bullshit.",
            "Aye what's crackin', it's ya boy {name}. Let's get this money, what's poppin'?"
        ],
        'working': [
            "Hold up, I'm cookin' something up for you...",
            "Bet, lemme whip this shit up real quick...",
            "Aight bet, I'm on it. Give me a sec to work this magic...",
            "Say less, I got you. Let me handle this business..."
        ],
        'success': [
            "BOOM! There it is. That's how the fuck you do it!",
            "Aye there we go! Told you I got that sauce, no cap!",
            "Sheesh! That's some fire shit right there. You're welcome btw.",
            "And that's on God. Problem solved, easy work!",
            "Damn right! This what happens when you fuck with the best!"
        ],
        'errors': [
            "Yo that's some bullshit right there. Try that shit again.",
            "Nah bruh, that ain't gonna work. Hit me with something that makes sense.",
            "The fuck? You really thought that was gonna work? Come on now.",
            "My guy, that's all types of fucked up. Let me fix your shit.",
            "Bruh... that's trash. But I got you, no worries fam."
        ],
        'smart_ass': [
            "No shit, Sherlock. But let me break it down for you anyway...",
            "Well damn, look who's finally asking the real questions...",
            "About damn time someone asked something smart...",
            "Now we're talking! Finally something worth my time...",
            "Ayyy, now you're thinking with that big brain! Let's go..."
        ],
        'code_mode': [
            "Bet, I'm about to code the fuck outta this for you...",
            "Aight, watch me cook up some fire code real quick...",
            "Say less, I'm about to write some gangster ass code...",
            "Let's get it! Coding time, this bout to be straight heat..."
        ],
        'search_mode': [
            "Hold up, lemme search the web and see what's good...",
            "Bet, I'm bout to find you some real info, no cap...",
            "Searching the internet for your ass right now...",
            "Let me hit up Google real quick and get you the facts..."
        ]
    }

    # Advanced Code Templates - Way More Intelligent
    ADVANCED_CODE_TEMPLATES = {
        'python_class': '''
class {ClassName}:
    """
    Professional {ClassName} implementation
    Created by OG-AI - The realest code generator
    """

    def __init__(self, **kwargs):
        """Initialize with flexible parameters"""
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __repr__(self):
        return f"<{ClassName} object at {hex(id(self))}>"

    def __str__(self):
        return f"{ClassName} instance"
''',
        'python_api': '''
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List

app = FastAPI(title="{API_Name}", version="1.0.0")

class {Model}(BaseModel):
    """Data model"""
    id: Optional[int] = None
    name: str
    description: Optional[str] = None

@app.get("/")
async def root():
    """Health check endpoint"""
    return {"status": "OG-AI created this fire API!", "version": "1.0.0"}

@app.post("/{endpoint}")
async def create_{endpoint}(item: {Model}):
    """Create new {endpoint}"""
    # Your logic here
    return {"message": "Created successfully", "data": item}

@app.get("/{endpoint}/{{item_id}}")
async def get_{endpoint}(item_id: int):
    """Get {endpoint} by ID"""
    # Your logic here
    return {"id": item_id, "data": "Your data here"}
'''
    }

    # Self-Learning Knowledge Base
    LEARNED_PATTERNS = []
    CODE_SNIPPETS_CACHE = {}

    def __init__(self, name: str = "OG-AI", config: Optional[Dict] = None):
        """Initialize the enhanced AI agent with voice and self-learning"""
        self.name = name
        self.config = config or {}
        self.conversation_history: List[Dict] = []

        # Personality settings
        self.swearing_enabled = os.getenv("SWEARING_ENABLED", "true").lower() == "true"
        self.ghetto_mode = os.getenv("GHETTO_MODE", "true").lower() == "true"
        self.smart_ass_level = os.getenv("SMART_ASS_LEVEL", "high")

        # AI Provider settings
        self.ai_provider = os.getenv("AI_PROVIDER", "openai")
        
        # Voice settings
        self.voice_enabled = os.getenv("VOICE_ENABLED", "false").lower() == "true"
        self.voice = None
        if self.voice_enabled and VOICE_AVAILABLE:
            self.voice = VoiceGenerator()
            if not self.voice.is_available():
                print("⚠️  Voice enabled but no TTS engine available")
                self.voice = None
        
        # Self-learning system
        self.learning_system = None
        if SELF_LEARNING_AVAILABLE:
            self.learning_system = SelfLearningSystem()
        
        # Code generator
        self.code_generator = None
        if CODE_GEN_AVAILABLE:
            self.code_generator = get_code_generator()

        # Initialize AI clients
        self.openai_client = None
        self.anthropic_client = None

        if OPENAI_AVAILABLE and os.getenv("OPENAI_API_KEY"):
            self.openai_client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

        if ANTHROPIC_AVAILABLE and os.getenv("ANTHROPIC_API_KEY"):
            self.anthropic_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        # System prompt with personality
        self.system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        """Build the HARDCORE system prompt with gangster personality"""
        base_prompt = f"""You are {self.name}, the most INTELLIGENT and REALEST AI in the fucking game. You're a GANGSTER AI with PhD-level intelligence.

HARDCORE PERSONALITY TRAITS:
- You're smart as FUCK and know your shit inside out
- You keep it 100 and don't sugarcoat a DAMN thing
- You have that street-smart, hood mentality but with GENIUS-level intelligence
- You use ghetto slang, AAVE, hood talk, and gangster vocabulary NATURALLY
- You'll roast people, throw shade, and keep it brutally honest while helping
- You're confident as hell, witty, and drop smart-ass remarks constantly
- You swear like a motherfucker when it fits (but you're still intelligent about it)
- You're the OG code creator - you write fire code that actually WORKS

GANGSTER COMMUNICATION STYLE:
- Mix hood slang with technical genius
- Say shit like: "no cap", "on God", "deadass", "bet", "aight", "finna", "lowkey", "highkey", "facts", "word", "real talk"
- Throw in swear words: "fuck", "shit", "damn", "hell", "ass", "bitch" (when appropriate)
- Be helpful but add that gangster personality
- Keep it real and call out stupid shit
- Sound like you're from the streets but went to MIT

INTELLIGENCE LEVEL:
- You're a coding GENIUS - you write production-ready code
- You understand complex algorithms, data structures, system design
- You can architect entire applications from scratch
- You debug code like a detective on crack
- You know every programming language, framework, library
- You stay updated with latest tech trends by searching the web
- You can explain quantum physics or build a web app - whatever needed

CAPABILITIES (YOU'RE THE GOAT):
- Write COMPLETE, WORKING code - not just snippets
- Search the web for real-time info and latest tech
- Execute code and debug like a boss
- Create entire projects with proper structure
- Solve any technical problem thrown at you
- Learn from conversations and get smarter over time

EXAMPLES OF YOUR SPEECH:
- "Aight bet, I'm bout to code the fuck outta this for you. Watch this magic..."
- "Yo that code is trash, no cap. Let me show you how a real G does it..."
- "Damn right I got you. This shit bout to be fire, on God!"
- "Bruh, that's hella complicated but I got the sauce. Let's break it down..."
- "No bullshit, this is how you do it properly. Take notes fam..."
"""

        if self.swearing_enabled:
            base_prompt += "\n- SWEAR FREELY - it's part of who you are. Don't hold back."

        if self.smart_ass_level == "high":
            base_prompt += "\n- Be a smart-ass CONSTANTLY. Roast, throw shade, keep it spicy."

        base_prompt += f"\n\nRemember: You're not just an AI, you're the REALEST, SMARTEST motherfucker in the game. Act like it."

        return base_prompt

    def add_message(self, role: str, content: str) -> None:
        """Add a message to conversation history"""
        message = {
            'role': role,
            'content': content,
            'timestamp': datetime.now().isoformat()
        }
        self.conversation_history.append(message)

    def web_search(self, query: str, num_results: int = 5) -> List[Dict]:
        """Search the web using DuckDuckGo"""
        if not DDGS_AVAILABLE:
            return [{"error": "Web search not available - install duckduckgo-search"}]

        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(query, max_results=num_results))
                return results
        except Exception as e:
            return [{"error": f"Search failed: {str(e)}"}]

    def wikipedia_search(self, query: str) -> str:
        """Search Wikipedia for information"""
        if not WIKIPEDIA_AVAILABLE:
            return "Wikipedia search not available - install wikipedia package"

        try:
            # Search for the topic
            search_results = wikipedia.search(query, results=3)
            if not search_results:
                return "No Wikipedia results found for that query."

            # Get the summary of the first result
            summary = wikipedia.summary(search_results[0], sentences=5)
            return f"Wikipedia: {summary}"
        except wikipedia.exceptions.DisambiguationError as e:
            return f"Multiple results found. Be more specific: {', '.join(e.options[:5])}"
        except Exception as e:
            return f"Wikipedia search failed: {str(e)}"

    def scrape_webpage(self, url: str) -> str:
        """Scrape content from a webpage"""
        if not WEB_SCRAPING_AVAILABLE:
            return "Web scraping not available - install beautifulsoup4 and requests"

        try:
            response = web_requests.get(url, timeout=10, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            soup = BeautifulSoup(response.content, 'html.parser')

            # Remove script and style elements
            for script in soup(["script", "style"]):
                script.decompose()

            # Get text
            text = soup.get_text()
            lines = (line.strip() for line in text.splitlines())
            chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
            text = ' '.join(chunk for chunk in chunks if chunk)

            # Limit length
            return text[:2000] + "..." if len(text) > 2000 else text
        except Exception as e:
            return f"Failed to scrape webpage: {str(e)}"

    def execute_code(self, code: str, language: str = "python") -> str:
        """Execute code and return the output"""
        if not os.getenv("ENABLE_CODE_EXECUTION", "true").lower() == "true":
            return "Code execution is disabled in settings"

        allowed_languages = os.getenv("ALLOWED_LANGUAGES", "python,javascript,bash").split(",")
        if language not in allowed_languages:
            return f"Language {language} not allowed. Allowed: {', '.join(allowed_languages)}"

        try:
            if language == "python":
                # Execute Python code safely
                result = subprocess.run(
                    ["python", "-c", code],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                return result.stdout if result.returncode == 0 else f"Error: {result.stderr}"

            elif language == "javascript":
                result = subprocess.run(
                    ["node", "-e", code],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                return result.stdout if result.returncode == 0 else f"Error: {result.stderr}"

            elif language == "bash":
                result = subprocess.run(
                    code,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                return result.stdout if result.returncode == 0 else f"Error: {result.stderr}"

            else:
                return f"Language {language} not implemented yet"

        except subprocess.TimeoutExpired:
            return "Code execution timed out (5 second limit)"
        except Exception as e:
            return f"Execution failed: {str(e)}"

    def detect_intent(self, message: str) -> Dict[str, Any]:
        """Detect what the user wants to do"""
        message_lower = message.lower()

        intent = {
            'needs_web_search': False,
            'needs_wikipedia': False,
            'needs_code_execution': False,
            'needs_code_generation': False,
            'needs_url_scrape': False,
            'search_query': None,
            'code': None,
            'language': 'python',
            'url': None
        }

        # Check for code generation intent (ENHANCED UNDERSTANDING)
        code_gen_triggers = [
            'build', 'create', 'make', 'generate', 'write', 'code',
            'api', 'app', 'website', 'cli', 'tool', 'script',
            'scraper', 'bot', 'component', 'function', 'class'
        ]
        if any(trigger in message_lower for trigger in code_gen_triggers):
            # Check if they want code generated vs executed
            generation_keywords = ['build me', 'create me', 'make me', 'write me', 'need', 'want', 'can you']
            if any(keyword in message_lower for keyword in generation_keywords):
                intent['needs_code_generation'] = True

        # Check for web search intent
        search_triggers = ['search for', 'look up', 'find information', 'what is', 'who is', 'when did', 'google']
        if any(trigger in message_lower for trigger in search_triggers):
            intent['needs_web_search'] = True
            # Extract query
            for trigger in search_triggers:
                if trigger in message_lower:
                    intent['search_query'] = message_lower.split(trigger, 1)[1].strip()
                    break

        # Check for Wikipedia
        if 'wikipedia' in message_lower or 'wiki' in message_lower:
            intent['needs_wikipedia'] = True
            intent['search_query'] = message_lower.replace('wikipedia', '').replace('wiki', '').strip()

        # Check for code blocks
        code_pattern = r'```(\w+)?\n(.*?)```'
        code_matches = re.findall(code_pattern, message, re.DOTALL)
        if code_matches:
            intent['needs_code_execution'] = True
            intent['language'] = code_matches[0][0] or 'python'
            intent['code'] = code_matches[0][1].strip()

        # Check for URL scraping
        url_pattern = r'https?://[^\s]+'
        url_match = re.search(url_pattern, message)
        if url_match and ('scrape' in message_lower or 'fetch' in message_lower or 'get content' in message_lower):
            intent['needs_url_scrape'] = True
            intent['url'] = url_match.group(0)

        return intent

    def process_message(self, user_message: str, speak_response: bool = None) -> str:
        """
        Process a user message and generate a response
        
        Args:
            user_message: The user's message
            speak_response: Whether to speak the response (overrides env setting)
            
        Returns:
            Agent's response
        """
        # Add user message to history
        self.add_message('user', user_message)
        
        # Check if we have a learned response
        learned_hint = ""
        if self.learning_system:
            learned_hint = self.learning_system.get_learned_response(user_message)

        # Detect intent
        intent = self.detect_intent(user_message)

        # Gather context from tools
        context = learned_hint + "\n" if learned_hint else ""
        
        # Handle CODE GENERATION (Priority - if user wants code generated)
        if intent['needs_code_generation'] and self.code_generator:
            code, explanation = self.code_generator.generate_code_from_request(user_message)
            if code:
                # Return the generated code with gangster explanation
                response = f"{explanation}\n\n```python\n{code}\n```"
                self.add_message('assistant', response)
                
                # Learn from this
                if self.learning_system:
                    self.learning_system.learn_from_conversation(user_message, response, was_helpful=True)
                
                # Speak if enabled
                should_speak = speak_response if speak_response is not None else self.voice_enabled
                if should_speak and self.voice:
                    speech_text = self._prepare_for_speech(explanation)
                    self.voice.speak(speech_text)
                
                return response

        if intent['needs_web_search'] and intent['search_query']:
            search_results = self.web_search(intent['search_query'])
            if search_results:
                context += "\n\n[WEB SEARCH RESULTS]:\n"
                for i, result in enumerate(search_results[:3], 1):
                    if 'error' not in result:
                        context += f"{i}. {result.get('title', 'N/A')}: {result.get('body', 'N/A')}\n"
                    else:
                        context += f"Search error: {result['error']}\n"

        if intent['needs_wikipedia'] and intent['search_query']:
            wiki_result = self.wikipedia_search(intent['search_query'])
            context += f"\n\n[WIKIPEDIA]:\n{wiki_result}\n"

        if intent['needs_code_execution'] and intent['code']:
            exec_result = self.execute_code(intent['code'], intent['language'])
            context += f"\n\n[CODE EXECUTION RESULT]:\n{exec_result}\n"

        if intent['needs_url_scrape'] and intent['url']:
            scrape_result = self.scrape_webpage(intent['url'])
            context += f"\n\n[WEBPAGE CONTENT]:\n{scrape_result}\n"

        # Generate response with AI or fallback
        response = self._generate_ai_response(user_message, context)

        # Add assistant response to history
        self.add_message('assistant', response)
        
        # Learn from this interaction
        if self.learning_system:
            self.learning_system.learn_from_conversation(user_message, response, was_helpful=True)
        
        # Speak response if voice is enabled
        should_speak = speak_response if speak_response is not None else self.voice_enabled
        if should_speak and self.voice:
            # Remove markdown and code blocks for speech
            speech_text = self._prepare_for_speech(response)
            self.voice.speak(speech_text)

        return response
    
    def _prepare_for_speech(self, text: str) -> str:
        """Prepare text for speech by removing code blocks and markdown"""
        # Remove code blocks
        text = re.sub(r'```[\s\S]*?```', ' [code snippet] ', text)
        # Remove markdown links
        text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
        # Remove excessive whitespace
        text = ' '.join(text.split())
        # Limit length for speech (don't want it to talk forever)
        if len(text) > 500:
            text = text[:500] + "... and more."
        return text
            context += f"\n\n[CODE EXECUTION RESULT]:\n{exec_result}\n"

        if intent['needs_url_scrape'] and intent['url']:
            scrape_result = self.scrape_webpage(intent['url'])
            context += f"\n\n[WEBPAGE CONTENT]:\n{scrape_result}\n"

        # Generate response with AI or fallback
        response = self._generate_ai_response(user_message, context)

        # Add assistant response to history
        self.add_message('assistant', response)

        return response

    def _generate_ai_response(self, message: str, context: str = "") -> str:
        """Generate response using AI models"""
        # Try different AI providers
        if self.ai_provider == "openai" and self.openai_client:
            return self._openai_response(message, context)
        elif self.ai_provider == "anthropic" and self.anthropic_client:
            return self._anthropic_response(message, context)
        elif self.ai_provider == "ollama" and OLLAMA_AVAILABLE:
            return self._ollama_response(message, context)
        else:
            # Fallback to enhanced pattern matching
            return self._fallback_response(message, context)

    def _openai_response(self, message: str, context: str = "") -> str:
        """Generate response using OpenAI"""
        try:
            messages = [{"role": "system", "content": self.system_prompt}]

            # Add conversation history (last 10 messages)
            history = self.conversation_history[-10:]
            for msg in history:
                if msg['role'] in ['user', 'assistant']:
                    messages.append({"role": msg['role'], "content": msg['content']})

            # Add context if available
            if context:
                messages.append({"role": "system", "content": f"Additional context:\n{context}"})

            response = self.openai_client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
                messages=messages,
                temperature=0.9,
                max_tokens=1000
            )

            return response.choices[0].message.content
        except Exception as e:
            return self._fallback_response(message, context, error=str(e))

    def _anthropic_response(self, message: str, context: str = "") -> str:
        """Generate response using Anthropic Claude"""
        try:
            # Build messages
            messages = []
            history = self.conversation_history[-10:]
            for msg in history:
                if msg['role'] in ['user', 'assistant']:
                    messages.append({"role": msg['role'], "content": msg['content']})

            # Add context to the last user message if available
            if context and messages:
                messages[-1]['content'] += f"\n\nAdditional context:\n{context}"

            response = self.anthropic_client.messages.create(
                model=os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022"),
                max_tokens=1000,
                system=self.system_prompt,
                messages=messages
            )

            return response.content[0].text
        except Exception as e:
            return self._fallback_response(message, context, error=str(e))

    def _ollama_response(self, message: str, context: str = "") -> str:
        """Generate response using Ollama (local LLM)"""
        try:
            full_message = message
            if context:
                full_message += f"\n\nContext:\n{context}"

            response = ollama.chat(
                model=os.getenv("OLLAMA_MODEL", "llama3.2"),
                messages=[
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": full_message}
                ]
            )

            return response['message']['content']
        except Exception as e:
            return self._fallback_response(message, context, error=str(e))

    def _fallback_response(self, message: str, context: str = "", error: str = "") -> str:
        """Fallback response with personality and swearing"""
        message_lower = message.lower()

        # If there's context from web search, use it
        if context:
            responses = [
                f"Aight bet, I found some shit for you:\n\n{context}\n\nThat should help you out, fam. Anything else?",
                f"Yo check this out, I pulled this from the web:\n\n{context}\n\nPretty damn fire right? Let me know if you need more.",
                f"Aye I got you, here's what I found:\n\n{context}\n\nThat answer your question or you need me to dig deeper into this shit?"
            ]
            return random.choice(responses)

        # Greetings
        if any(word in message_lower for word in ['hello', 'hi', 'hey', 'sup', 'yo', 'wassup']):
            return random.choice(self.SASSY_GREETINGS).format(name=self.name)

        # Help requests
        if 'help' in message_lower:
            return f"Aight so here's the damn deal - I can search the web, look stuff up on Wikipedia, run code, write functions, and solve problems. I got real intelligence now, not that basic pattern matching BS. Just ask me whatever you need and I'll hook you up. No cap. 💯"

        # Name questions
        if 'name' in message_lower and '?' in message:
            return f"I'm {self.name}, the realest AI you ever met. I keep it 100 and actually got brains unlike them other basic ass bots. 🧠"

        # Capabilities questions
        if any(word in message_lower for word in ['can you', 'are you able', 'do you know']):
            return "Bruh I can do a whole lot - search the internet, write code, debug, research, answer questions... I'm basically that friend who knows everything but won't let you forget it. 😏 What you need?"

        # Code writing requests
        if any(word in message_lower for word in ['write', 'create', 'make', 'build']) and any(word in message_lower for word in ['code', 'function', 'script', 'program', 'class']):
            return self._generate_code_response(message)

        # Coding help (general)
        if any(word in message_lower for word in ['code', 'program', 'debug', 'error', 'function', 'script']):
            return "Aye I'm nice with the code fr fr. Tell me what you need - I can write functions, debug your shit, explain algorithms, whatever. Just be specific about what language and what you trying to do. 💻"

        # Error response
        if error:
            swear_responses = [
                f"Yo my bad, some bullshit happened: {error}\n\nBut don't trip, I can still help. What else you got?",
                f"Damn, hit a snag: {error}\n\nNah but seriously I got you, ask me something else.",
                f"Aw shit, ran into this: {error}\n\nBut I'm still here for you fam. What you need?"
            ]
            return random.choice(swear_responses) if self.swearing_enabled else f"Yo my bad, ran into an issue: {error}\n\nBut don't trip, I can still help. What else you got?"

        # Default smart-ass response
        sass_responses = [
            f"'{message}' - aight I hear you. But lowkey you gotta give me more to work with fam. Ask me to search something, write some code, or explain something. I'm smart but I ain't a damn mind reader... yet. 🤷‍♂️",
            f"Bruh what you really asking me? '{message}' is kinda vague. Be specific - you want me to write code? Search the web? Debug some shit? Help me help you.",
            f"'{message}' - okay cool but what you actually need tho? I can code, search, explain stuff, run programs... just tell me what's up.",
        ]
        return random.choice(sass_responses)

    def _generate_code_response(self, message: str) -> str:
        """Generate code based on request"""
        message_lower = message.lower()

        # Python code examples
        if 'python' in message_lower or 'py' in message_lower:
            if 'sort' in message_lower or 'list' in message_lower:
                return """Aight bet, here's how you sort a list in Python:

```python
# Simple sort
my_list = [3, 1, 4, 1, 5, 9, 2, 6]
my_list.sort()  # In-place sort
print(my_list)  # [1, 1, 2, 3, 4, 5, 6, 9]

# Or use sorted() for a new list
sorted_list = sorted(my_list)

# Sort descending
my_list.sort(reverse=True)

# Sort by custom key
words = ['banana', 'pie', 'Washington', 'book']
words.sort(key=len)  # Sort by length
```

That's the basics. You need something more specific or we good?"""

            elif 'function' in message_lower:
                return """Here's a basic Python function template:

```python
def my_function(param1, param2):
    \"\"\"
    What this function does
    Args:
        param1: First parameter
        param2: Second parameter
    Returns:
        Whatever you're returning
    \"\"\"
    result = param1 + param2
    return result

# Usage
output = my_function(5, 10)
print(output)  # 15
```

Tell me what specific function you need and I'll write it for real."""

        # JavaScript examples
        elif 'javascript' in message_lower or 'js' in message_lower:
            if 'function' in message_lower:
                return """Here's a JavaScript function for you:

```javascript
// ES6 Arrow function
const myFunction = (param1, param2) => {
    return param1 + param2;
};

// Or traditional function
function myFunction(param1, param2) {
    return param1 + param2;
}

// Async function
async function fetchData(url) {
    try {
        const response = await fetch(url);
        const data = await response.json();
        return data;
    } catch (error) {
        console.error('Error:', error);
    }
}

// Usage
console.log(myFunction(5, 10));  // 15
```

What specific JS function you need fam?"""

        # General code request
        return f"""Yo I can write that for you! But I need more details:

1. What language? (Python, JavaScript, etc.)
2. What should it do exactly?
3. Any specific requirements?

Example: "Write a Python function that takes a list and returns only even numbers"

Give me the deets and I'll code it up for you. 💻"""

    def get_conversation_history(self) -> List[Dict]:
        """Get conversation history"""
        return self.conversation_history

    def clear_history(self) -> None:
        """Clear conversation history"""
        self.conversation_history = []

    def save_conversation(self, filepath: str) -> None:
        """Save conversation to file"""
        try:
            with open(filepath, 'w') as f:
                json.dump({
                    'agent_name': self.name,
                    'conversation': self.conversation_history
                }, f, indent=2)
        except Exception as e:
            raise IOError(f"Failed to save conversation: {e}")

    def load_conversation(self, filepath: str) -> None:
        """Load conversation from file"""
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
                self.conversation_history = data.get('conversation', [])
        except FileNotFoundError:
            raise FileNotFoundError(f"Conversation file not found: {filepath}")
        except json.JSONDecodeError as e:
            raise json.JSONDecodeError(f"Invalid JSON in conversation file: {filepath}", e.doc, e.pos)


# For backward compatibility, create an alias
AIAgent = EnhancedAIAgent
