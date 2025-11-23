"""
FastAPI Web Service for OG-AI Agent
Exposes REST API endpoints for interacting with the AI agent.
"""

import json
import os
import logging
from typing import List, Dict
from datetime import timedelta
from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict
from ai_agent import AIAgent
from auth import (
    user_storage,
    get_current_user,
    create_access_token,
    UserCreate,
    UserLogin,
    Token,
    User,
    ACCESS_TOKEN_EXPIRE_MINUTES
)

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

# Per-user agent instances
# Each user gets their own agent instance with isolated conversation history
user_agents: Dict[str, AIAgent] = {}


def get_agent(username: str = None) -> AIAgent:
    """
    Get or create an agent instance for a specific user.

    Args:
        username: Username to get agent for. If None, returns a shared demo agent.

    Returns:
        AIAgent instance for the user
    """
    # For backward compatibility with health/root endpoints
    if username is None:
        username = "__shared__"

    if username not in user_agents:
        # Load config if exists
        config = {}
        if os.path.exists('config.json'):
            try:
                with open('config.json', 'r') as f:
                    config = json.load(f)
            except Exception as e:
                logger.warning(f"Could not load config.json: {e}")

        agent_name = config.get('agent_name', 'OG-AI')
        user_agents[username] = AIAgent(name=agent_name, config=config)

    return user_agents[username]


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


# ==================== Authentication Endpoints ====================

@app.post("/auth/register", response_model=User, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserCreate):
    """
    Register a new user account.

    Args:
        user_data: UserCreate model with username and password

    Returns:
        User object for the newly created user

    Raises:
        HTTPException: If username already exists
    """
    try:
        user = user_storage.create_user(user_data.username, user_data.password)
        logger.info(f"New user registered: {user.username}")
        return User(username=user.username, created_at=user.created_at)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error during registration: {str(e)}")
        detail = f"Registration failed: {str(e)}" if DEVELOPMENT_MODE else "Registration failed"
        raise HTTPException(status_code=500, detail=detail)


@app.post("/auth/login", response_model=Token)
async def login(user_data: UserLogin):
    """
    Authenticate and receive a JWT access token.

    Args:
        user_data: UserLogin model with username and password

    Returns:
        Token object with JWT access token

    Raises:
        HTTPException: If authentication fails
    """
    user = user_storage.authenticate_user(user_data.username, user_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username},
        expires_delta=access_token_expires
    )

    logger.info(f"User logged in: {user.username}")
    return Token(access_token=access_token)


@app.get("/auth/me", response_model=User)
async def get_current_user_info(current_user: User = Depends(get_current_user)):
    """
    Get information about the currently authenticated user.

    Args:
        current_user: Current user from JWT token (injected by dependency)

    Returns:
        User object with current user information
    """
    return current_user


# ==================== Public Endpoints ====================

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
async def chat(request: ChatRequest, current_user: User = Depends(get_current_user)):
    """
    Send a message to the AI agent and receive a response.

    Requires authentication. Each user has their own isolated conversation history.

    Args:
        request: ChatRequest containing the user's message
        current_user: Current authenticated user (injected by dependency)

    Returns:
        ChatResponse with the agent's reply
    """
    if not request.message or not request.message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    agent_instance = get_agent(current_user.username)

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
        logger.error(f"Error processing message for user {current_user.username}: {str(e)}")
        detail = f"An error occurred while processing your message: {str(e)}" if DEVELOPMENT_MODE else "An error occurred while processing your message"
        raise HTTPException(status_code=500, detail=detail)


@app.get("/history", response_model=HistoryResponse)
async def get_history(current_user: User = Depends(get_current_user)):
    """
    Get the full conversation history for the authenticated user.

    Requires authentication. Returns only the user's own conversation history.

    Args:
        current_user: Current authenticated user (injected by dependency)

    Returns:
        HistoryResponse with all conversation messages
    """
    agent_instance = get_agent(current_user.username)

    try:
        history = agent_instance.get_conversation_history()
        return {
            "conversation": history,
            "history": history,  # Backward compatibility with Flask API
            "message_count": len(history)
        }
    except Exception as e:
        logger.error(f"Error retrieving history for user {current_user.username}: {str(e)}")
        detail = f"An error occurred while retrieving conversation history: {str(e)}" if DEVELOPMENT_MODE else "An error occurred while retrieving conversation history"
        raise HTTPException(status_code=500, detail=detail)


@app.post("/reset", response_model=StatusResponse)
async def reset_conversation(current_user: User = Depends(get_current_user)):
    """
    Clear the conversation history for the authenticated user.

    Requires authentication. Only clears the user's own conversation history.

    Args:
        current_user: Current authenticated user (injected by dependency)

    Returns:
        StatusResponse confirming the reset
    """
    agent_instance = get_agent(current_user.username)

    try:
        agent_instance.clear_history()
        return {
            "status": "success",
            "agent_name": agent_instance.name,
            "message": "Conversation history has been cleared"
        }
    except Exception as e:
        logger.error(f"Error resetting conversation for user {current_user.username}: {str(e)}")
        detail = f"An error occurred while resetting conversation: {str(e)}" if DEVELOPMENT_MODE else "An error occurred while resetting conversation"
        raise HTTPException(status_code=500, detail=detail)


@app.post("/clear", response_model=StatusResponse)
async def clear_history(current_user: User = Depends(get_current_user)):
    """
    Clear the conversation history (Flask API backward compatibility alias for /reset).

    Requires authentication. Only clears the user's own conversation history.

    Args:
        current_user: Current authenticated user (injected by dependency)

    Returns:
        StatusResponse confirming the clear
    """
    return await reset_conversation(current_user)


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
