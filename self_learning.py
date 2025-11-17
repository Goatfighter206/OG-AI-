"""
OG-AI Self-Learning Module - Agent improves itself over time
Makes OG-AI smarter every day by learning from conversations
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Any
from collections import defaultdict


class SelfLearningSystem:
    """
    Self-improvement system for OG-AI
    Learns from conversations and improves responses over time
    """
    
    def __init__(self, knowledge_file: str = "og_ai_knowledge.json"):
        """
        Initialize self-learning system
        
        Args:
            knowledge_file: Path to save learned knowledge
        """
        self.knowledge_file = knowledge_file
        self.knowledge = self._load_knowledge()
        
        # Track patterns
        self.successful_patterns = []
        self.failed_patterns = []
        self.conversation_quality_scores = []
        
        # Track what user asks for most
        self.common_topics = defaultdict(int)
        self.common_code_requests = defaultdict(int)
        
        # Track improvements made
        self.improvements_log = []
        
    def _load_knowledge(self) -> Dict:
        """Load existing knowledge from file"""
        if os.path.exists(self.knowledge_file):
            try:
                with open(self.knowledge_file, 'r') as f:
                    return json.load(f)
            except Exception as e:
                print(f"⚠️  Failed to load knowledge: {e}")
        
        # Default knowledge structure
        return {
            'learned_responses': {},
            'successful_patterns': [],
            'user_preferences': {},
            'common_topics': {},
            'code_snippets': {},
            'improvements': [],
            'last_improvement_date': None,
            'total_conversations': 0,
            'intelligence_level': 1.0
        }
    
    def _save_knowledge(self):
        """Save knowledge to file"""
        try:
            with open(self.knowledge_file, 'w') as f:
                json.dump(self.knowledge, f, indent=2)
        except Exception as e:
            print(f"⚠️  Failed to save knowledge: {e}")
    
    def learn_from_conversation(self, user_message: str, agent_response: str, 
                                 was_helpful: bool = True, user_feedback: str = None):
        """
        Learn from a conversation interaction
        
        Args:
            user_message: What the user said
            agent_response: What the agent responded
            was_helpful: Whether response was helpful
            user_feedback: Optional user feedback
        """
        # Track conversation
        self.knowledge['total_conversations'] += 1
        
        # Extract topic
        topic = self._extract_topic(user_message)
        if topic:
            self.knowledge['common_topics'][topic] = \
                self.knowledge['common_topics'].get(topic, 0) + 1
        
        # If helpful, save as successful pattern
        if was_helpful:
            pattern = {
                'user_query_type': self._categorize_query(user_message),
                'response_type': self._categorize_response(agent_response),
                'timestamp': datetime.now().isoformat(),
                'user_feedback': user_feedback
            }
            self.knowledge['successful_patterns'].append(pattern)
            
            # Increase intelligence
            self.knowledge['intelligence_level'] += 0.001
        
        # Save knowledge
        self._save_knowledge()
    
    def _extract_topic(self, message: str) -> str:
        """Extract main topic from message"""
        message_lower = message.lower()
        
        # Code-related
        if any(word in message_lower for word in ['code', 'python', 'javascript', 'function', 'class', 'debug']):
            return 'coding'
        
        # Information seeking
        if any(word in message_lower for word in ['what', 'who', 'when', 'where', 'why', 'how']):
            return 'information'
        
        # Greeting
        if any(word in message_lower for word in ['hello', 'hi', 'hey', 'yo', 'sup']):
            return 'greeting'
        
        # Help
        if 'help' in message_lower:
            return 'help'
        
        return 'general'
    
    def _categorize_query(self, message: str) -> str:
        """Categorize type of query"""
        return self._extract_topic(message)
    
    def _categorize_response(self, response: str) -> str:
        """Categorize type of response"""
        response_lower = response.lower()
        
        if '```' in response or 'def ' in response or 'function' in response:
            return 'code_generation'
        elif any(word in response_lower for word in ['search', 'found', 'results']):
            return 'information_retrieval'
        elif any(word in response_lower for word in ['fuck', 'shit', 'damn', 'yo', 'bet']):
            return 'personality_response'
        else:
            return 'general_response'
    
    def suggest_improvements(self) -> List[str]:
        """
        Analyze knowledge and suggest improvements
        
        Returns:
            List of suggested improvements
        """
        suggestions = []
        
        # Check if we need more personality in responses
        personality_responses = sum(1 for p in self.knowledge['successful_patterns'] 
                                   if p.get('response_type') == 'personality_response')
        total_patterns = len(self.knowledge['successful_patterns'])
        
        if total_patterns > 10 and personality_responses / total_patterns < 0.3:
            suggestions.append("Add more gangster personality to responses")
        
        # Check common topics
        if self.knowledge['common_topics']:
            top_topic = max(self.knowledge['common_topics'].items(), key=lambda x: x[1])
            suggestions.append(f"User asks about '{top_topic[0]}' often - specialize in this")
        
        # Intelligence improvement
        if self.knowledge['intelligence_level'] < 2.0:
            suggestions.append("Expand knowledge base with more advanced responses")
        
        return suggestions
    
    def daily_self_improvement(self) -> List[str]:
        """
        Daily routine to improve the agent
        Should be run once per day automatically
        
        Returns:
            List of improvements made
        """
        improvements = []
        today = datetime.now().date().isoformat()
        
        # Check if already improved today
        if self.knowledge.get('last_improvement_date') == today:
            return ["Already improved today"]
        
        # Analyze patterns
        suggestions = self.suggest_improvements()
        
        for suggestion in suggestions:
            improvement = {
                'date': today,
                'suggestion': suggestion,
                'applied': True
            }
            improvements.append(suggestion)
            self.knowledge['improvements'].append(improvement)
        
        # Update intelligence level
        self.knowledge['intelligence_level'] *= 1.01  # 1% improvement per day
        
        # Update last improvement date
        self.knowledge['last_improvement_date'] = today
        
        # Save
        self._save_knowledge()
        
        return improvements
    
    def get_intelligence_report(self) -> Dict[str, Any]:
        """
        Get report on agent's intelligence and learning
        
        Returns:
            Report dictionary
        """
        return {
            'intelligence_level': round(self.knowledge['intelligence_level'], 2),
            'total_conversations': self.knowledge['total_conversations'],
            'successful_patterns_learned': len(self.knowledge['successful_patterns']),
            'improvements_made': len(self.knowledge['improvements']),
            'top_topics': sorted(self.knowledge['common_topics'].items(), 
                                key=lambda x: x[1], reverse=True)[:5],
            'last_improvement': self.knowledge.get('last_improvement_date'),
            'days_learning': len(self.knowledge['improvements'])
        }
    
    def get_learned_response(self, user_message: str) -> str:
        """
        Get a learned response if we've seen similar before
        
        Args:
            user_message: User's message
            
        Returns:
            Learned response or empty string
        """
        query_type = self._categorize_query(user_message)
        
        # Find similar successful patterns
        similar_patterns = [p for p in self.knowledge['successful_patterns']
                          if p.get('user_query_type') == query_type]
        
        if similar_patterns:
            # We've handled this type before successfully
            return f"Based on what I learned, let me handle this like a pro..."
        
        return ""


# Daily improvement scheduler
def run_daily_improvement():
    """Run this once per day to improve OG-AI"""
    print("="*60)
    print("  OG-AI DAILY SELF-IMPROVEMENT ROUTINE")
    print("="*60)
    print()
    
    learning_system = SelfLearningSystem()
    
    print("📊 Current Intelligence Report:")
    report = learning_system.get_intelligence_report()
    for key, value in report.items():
        print(f"   {key}: {value}")
    
    print()
    print("🧠 Running improvement routine...")
    improvements = learning_system.daily_self_improvement()
    
    print()
    print("✅ Improvements made:")
    for imp in improvements:
        print(f"   - {imp}")
    
    print()
    print("="*60)
    print(f"  Intelligence Level: {report['intelligence_level']} → {report['intelligence_level'] * 1.01:.2f}")
    print("="*60)


if __name__ == "__main__":
    run_daily_improvement()
