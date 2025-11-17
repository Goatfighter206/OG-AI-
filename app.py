"""
FastAPI Web Service for OG-AI Agent
Exposes REST API endpoints for interacting with the AI agent.
"""

import json
import os
import logging
from typing import List, Dict
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

# Try to import enhanced agent, fallback to basic agent
try:
    from ai_agent_enhanced import EnhancedAIAgent as AIAgent
    print("*** Enhanced AI Agent loaded - Full intelligence mode activated! ***")
except ImportError as e:
    print(f"*** Enhanced features not available: {e}")
    print("*** Installing required packages will enable full features")
    from ai_agent import AIAgent

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Check if running in development mode (for error detail control)
DEVELOPMENT_MODE = os.getenv("DEVELOPMENT_MODE", "false").lower() == "true"

# Initialize FastAPI app
app = FastAPI(
    title="OG-AI Agent API",
    description="A conversational AI agent REST API",
    version="1.0.0"
)

# Add CORS middleware to allow cross-origin requests
# NOTE: For production, set the ALLOWED_ORIGINS environment variable to specific allowed origins
# Example: ALLOWED_ORIGINS='["https://yourdomain.com"]'
allowed_origins_env = os.getenv("ALLOWED_ORIGINS")
if allowed_origins_env:
    try:
        allowed_origins = json.loads(allowed_origins_env)
        if not isinstance(allowed_origins, list):
            raise ValueError("ALLOWED_ORIGINS must be a JSON array")
    except Exception as e:
        logger.warning(f"Invalid ALLOWED_ORIGINS environment variable: {e}. Falling back to ['*'].")
        allowed_origins = ["*"]
else:
    allowed_origins = ["*"]  # Default for development/demo

# Security: Only enable credentials when not using wildcard origins
allow_credentials = allowed_origins != ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files directory
if not os.path.exists("static"):
    os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Global agent instance
# NOTE: This is a simplified implementation where all users share the same conversation history.
# For production multi-user scenarios, implement per-session or per-user agent instances
# using session cookies, JWT tokens, or a database-backed session store.
agent = None


def get_agent() -> AIAgent:
    """
    Get or create the global agent instance.
    
    Note: This returns a shared instance. For multi-user support, consider
    implementing session-based agent management.
    """
    global agent
    if agent is None:
        # Load config if exists
        config = {}
        if os.path.exists('config.json'):
            try:
                with open('config.json', 'r') as f:
                    config = json.load(f)
            except Exception as e:
                logger.warning(f"Could not load config.json: {e}")
        
        agent_name = config.get('agent_name', 'OG-AI')
        agent = AIAgent(name=agent_name, config=config)
    
    return agent


# Pydantic models for request/response
class ChatRequest(BaseModel):
    message: str
    speak_response: bool = False
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Hello! How are you?",
                "speak_response": False
            }
        }
    )


class ChatResponse(BaseModel):
    response: str
    agent_name: str
    timestamp: str
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "response": "Hello! I'm OG-AI, your AI assistant. How can I help you today?",
                "agent_name": "OG-AI",
                "timestamp": "2025-11-05T20:00:00.000000"
            }
        }
    )


class HistoryResponse(BaseModel):
    conversation: List[Dict]
    history: List[Dict]  # Backward compatibility with Flask API
    message_count: int
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "conversation": [
                    {
                        "role": "user",
                        "content": "Hello!",
                        "timestamp": "2025-11-05T20:00:00.000000"
                    }
                ],
                "history": [
                    {
                        "role": "user",
                        "content": "Hello!",
                        "timestamp": "2025-11-05T20:00:00.000000"
                    }
                ],
                "message_count": 1
            }
        }
    )


class StatusResponse(BaseModel):
    status: str
    agent_name: str
    message: str
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "healthy",
                "agent_name": "OG-AI",
                "message": "Service is running"
            }
        }
    )


@app.get("/", response_class=FileResponse)
async def root():
    """
    Serve the epic frontend HTML interface.
    """
    return FileResponse("index_epic.html")


@app.get("/classic", response_class=FileResponse)
async def classic_ui():
    """
    Serve the classic frontend HTML interface.
    """
    return FileResponse("frontend.html")


@app.get("/qr", response_class=FileResponse)
async def qr_code():
    """
    Serve the QR code page for mobile access.
    """
    return FileResponse("qr.html")


@app.get("/api", response_model=StatusResponse)
async def api_info():
    """
    API information endpoint.
    """
    agent_instance = get_agent()
    return {
        "status": "healthy",
        "agent_name": agent_instance.name,
        "message": "OG-AI Agent API is running. Visit /docs for API documentation."
    }


@app.get("/health", response_model=StatusResponse)
async def health_check():
    """
    Health check endpoint for monitoring service status.
    """
    agent_instance = get_agent()
    return {
        "status": "healthy",
        "agent_name": agent_instance.name,
        "message": "Service is running"
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    Send a message to the AI agent and receive a response.
    
    Args:
        request: ChatRequest containing the user's message and optional voice setting
        
    Returns:
        ChatResponse with the agent's reply
    """
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    
    agent_instance = get_agent()
    
    try:
        # Check if agent has voice/learning capabilities
        has_voice = hasattr(agent_instance, 'voice') and agent_instance.voice is not None
        has_learning = hasattr(agent_instance, 'learning_system') and agent_instance.learning_system is not None
        
        # Process message with voice option if available
        if has_voice:
            response = agent_instance.process_message(request.message.strip(), speak_response=request.speak_response)
        else:
            response = agent_instance.process_message(request.message.strip())
        
        # Get the latest assistant message from history
        history = agent_instance.get_conversation_history()
        latest_msg = history[-1] if history else None
        
        result = {
            "response": response,
            "agent_name": agent_instance.name,
            "timestamp": latest_msg['timestamp'] if latest_msg else ""
        }
        
        # Add intelligence info if learning is enabled
        if has_learning:
            report = agent_instance.learning_system.get_intelligence_report()
            result["intelligence"] = report.get("intelligence_level", 1.0)
        
        return result
    except Exception as e:
        logger.error(f"Error processing message: {str(e)}")
        detail = f"An error occurred while processing your message: {str(e)}" if DEVELOPMENT_MODE else "An error occurred while processing your message"
        raise HTTPException(status_code=500, detail=detail)


@app.get("/history", response_model=HistoryResponse)
async def get_history():
    """
    Get the full conversation history.
    
    Returns:
        HistoryResponse with all conversation messages
    """
    agent_instance = get_agent()
    
    try:
        history = agent_instance.get_conversation_history()
        return {
            "conversation": history,
            "history": history,  # Backward compatibility with Flask API
            "message_count": len(history)
        }
    except Exception as e:
        logger.error(f"Error retrieving history: {str(e)}")
        detail = f"An error occurred while retrieving conversation history: {str(e)}" if DEVELOPMENT_MODE else "An error occurred while retrieving conversation history"
        raise HTTPException(status_code=500, detail=detail)


@app.post("/reset", response_model=StatusResponse)
async def reset_conversation():
    """
    Clear the conversation history.
    
    Returns:
        StatusResponse confirming the reset
    """
    agent_instance = get_agent()
    
    try:
        agent_instance.clear_history()
        return {
            "status": "success",
            "agent_name": agent_instance.name,
            "message": "Conversation history has been cleared"
        }
    except Exception as e:
        logger.error(f"Error resetting conversation: {str(e)}")
        detail = f"An error occurred while resetting conversation: {str(e)}" if DEVELOPMENT_MODE else "An error occurred while resetting conversation"
        raise HTTPException(status_code=500, detail=detail)


@app.post("/clear")
async def clear_conversation():
    """
    Clear the conversation history (alternative endpoint for compatibility).
    """
    return await reset_conversation()


@app.get("/intelligence")
async def get_intelligence():
    """
    Get the agent's intelligence report (self-learning stats).
    
    Returns:
        Intelligence report with learning statistics
    """
    agent_instance = get_agent()
    
    try:
        # Check if agent has learning system
        if hasattr(agent_instance, 'learning_system') and agent_instance.learning_system:
            report = agent_instance.learning_system.get_intelligence_report()
            return report
        else:
            return {
                "status": "unavailable",
                "message": "Self-learning system not enabled. Set ENABLE_SELF_LEARNING=true in .env",
                "intelligence_level": 1.0,
                "total_conversations": 0,
                "successful_patterns_learned": 0
            }
    except Exception as e:
        logger.error(f"Error getting intelligence report: {str(e)}")
        return {
            "status": "error",
            "message": str(e),
            "intelligence_level": 1.0
        }


@app.post("/improve")
async def manual_improvement():
    """
    Manually trigger daily self-improvement routine.
    
    Returns:
        Improvement report
    """
    agent_instance = get_agent()
    
    try:
        if hasattr(agent_instance, 'learning_system') and agent_instance.learning_system:
            improvements = agent_instance.learning_system.daily_self_improvement()
            return {
                "status": "success",
                "improvements": improvements,
                "message": "Self-improvement routine completed"
            }
        else:
            return {
                "status": "unavailable",
                "message": "Self-learning system not enabled"
            }
    except Exception as e:
        logger.error(f"Error during improvement: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/clear", response_model=StatusResponse)
async def clear_history():
    """
    Clear the conversation history (Flask API backward compatibility alias for /reset).
    
    Returns:
        StatusResponse confirming the clear
    """
    return await reset_conversation()


if __name__ == "__main__":
    import uvicorn
    
    # Get port from environment variable or default to 8000
    port = int(os.environ.get("PORT", 8000))
    
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=port,
        reload=False  # Set to True for development
    )
