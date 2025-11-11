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
from pydantic import BaseModel, ConfigDict
from ai_agent import AIAgent

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
        print(f"Warning: Invalid ALLOWED_ORIGINS environment variable: {e}. Falling back to ['*'].")
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
                print(f"Warning: Could not load config.json: {e}")
        
        agent_name = config.get('agent_name', 'OG-AI')
        agent = AIAgent(name=agent_name, config=config)
    
    return agent


# Pydantic models for request/response
class ChatRequest(BaseModel):
    message: str
    
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "message": "Hello! How are you?"
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


@app.get("/", response_model=StatusResponse)
async def root():
    """
    Root endpoint - provides basic service information.
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
        request: ChatRequest containing the user's message
        
    Returns:
        ChatResponse with the agent's reply
    """
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    
    agent_instance = get_agent()
    
    try:
        response = agent_instance.process_message(request.message.strip())
        
        # Get the latest assistant message from history
        history = agent_instance.get_conversation_history()
        latest_msg = history[-1] if history else None
        
        return {
            "response": response,
            "agent_name": agent_instance.name,
            "timestamp": latest_msg['timestamp'] if latest_msg else ""
        }
    except Exception as e:
        logger.error(f"Error processing message: {str(e)}")
        raise HTTPException(status_code=500, detail="An error occurred while processing your message")


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
        raise HTTPException(status_code=500, detail="An error occurred while retrieving conversation history")


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
        raise HTTPException(status_code=500, detail="An error occurred while resetting conversation")


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
