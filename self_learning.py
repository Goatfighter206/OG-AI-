"""
OG-AI Self-Learning Module - Agent improves itself over time
Makes OG-AI smarter every day by learning from conversations
"""

import json
import logging
import os
import queue
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Any
from collections import defaultdict

logger = logging.getLogger(__name__)

# Round 321 DBPOOL batch-5: deferred learning. Learning
# from a conversation rewrites the whole knowledge file on
# every turn, and it ran ON the chat turn's critical path
# (after the reply, before the response returned — under
# the turn's memory lock). With DEFER_LEARNING on,
# learn_from_conversation instead hands the exchange to a
# daemon worker thread and returns at once; the learning
# itself is byte-for-byte the old code, just off the
# response path, and a failure in the worker is logged,
# never raised into a chat. Default OFF — every direct
# caller (the suites, the daily routine) keeps the exact
# synchronous behaviour; app.py turns it on at import for
# the serving process.
DEFER_LEARNING = False


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

        # DBPOOL batch-5: the intelligence report is a pure
        # function of self.knowledge, which changes only
        # through a save — so it is memoized and invalidated
        # by _save_knowledge; and the deferred-learning
        # worker's state (started lazily, only when
        # DEFER_LEARNING is on).
        self._report_cache = None
        self._learn_queue = None
        self._learn_thread = None
        self._learn_lock = threading.Lock()
        
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
        # Every knowledge mutation funnels through here, so
        # this is the report memo's invalidation point.
        self._report_cache = None
        try:
            with open(self.knowledge_file, 'w') as f:
                json.dump(self.knowledge, f, indent=2)
        except Exception as e:
            print(f"⚠️  Failed to save knowledge: {e}")
    
    def learn_from_conversation(self, user_message: str, agent_response: str,
                                 was_helpful: bool = True, user_feedback: str = None):
        """Learn from a conversation interaction.

        With DEFER_LEARNING on (the serving process), the
        exchange is queued to the daemon worker and this
        returns at once — the response path never waits on
        the knowledge file again. Otherwise the learning
        runs inline, exactly as it always did."""
        if DEFER_LEARNING:
            try:
                self._ensure_learn_worker()
                self._learn_queue.put((
                    (user_message, agent_response),
                    {"was_helpful": was_helpful,
                     "user_feedback": user_feedback}))
                return
            except Exception:
                logger.exception(
                    "Deferred learning enqueue failed; "
                    "learning inline instead")
        self._learn_from_conversation_now(
            user_message, agent_response,
            was_helpful=was_helpful, user_feedback=user_feedback)

    def _ensure_learn_worker(self):
        with self._learn_lock:
            if self._learn_thread is not None \
                    and self._learn_thread.is_alive():
                return
            q = queue.Queue()
            self._learn_queue = q
            t = threading.Thread(
                target=self._learn_worker, args=(q,),
                daemon=True, name="og-self-learning")
            t.start()
            self._learn_thread = t

    def _learn_worker(self, q):
        """Drain the deferred-learning queue. An Event item
        is a flush marker: everything queued before it has
        been learned once it is set. A failed learn is
        logged and dropped — learning is best-effort by
        nature and must never surface in a chat."""
        while True:
            item = q.get()
            try:
                if isinstance(item, threading.Event):
                    item.set()
                else:
                    args, kwargs = item
                    try:
                        self._learn_from_conversation_now(
                            *args, **kwargs)
                    except Exception:
                        logger.exception(
                            "Deferred self-learning failed")
            finally:
                q.task_done()

    def _learn_from_conversation_now(self, user_message: str, agent_response: str,
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
        # DBPOOL batch-5 memo: the report is a pure function
        # of self.knowledge, invalidated by _save_knowledge
        # (the one funnel every mutation takes). Callers get
        # a copy, so the memo cannot be poisoned by a caller
        # mutating what it was handed.
        if self._report_cache is not None:
            return dict(self._report_cache)
        report = {
            'intelligence_level': round(self.knowledge['intelligence_level'], 2),
            'total_conversations': self.knowledge['total_conversations'],
            'successful_patterns_learned': len(self.knowledge['successful_patterns']),
            'improvements_made': len(self.knowledge['improvements']),
            'top_topics': sorted(self.knowledge['common_topics'].items(),
                                key=lambda x: x[1], reverse=True)[:5],
            'last_improvement': self.knowledge.get('last_improvement_date'),
            'days_learning': len(self.knowledge['improvements'])
        }
        self._report_cache = report
        return dict(report)
    
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
