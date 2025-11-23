"""
Authentication and Authorization Module for OG-AI
Handles JWT token generation, validation, password hashing, and user management.
"""

import os
import json
from datetime import datetime, timedelta
from typing import Optional, Dict
from passlib.context import CryptContext
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field, ConfigDict

# JWT Configuration
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-this-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# HTTP Bearer token scheme
security = HTTPBearer()


# ==================== Pydantic Models ====================

class UserBase(BaseModel):
    """Base user model with common fields."""
    username: str = Field(..., min_length=3, max_length=50, pattern="^[a-zA-Z0-9_-]+$")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "username": "john_doe"
            }
        }
    )


class UserCreate(UserBase):
    """Model for user registration."""
    password: str = Field(..., min_length=6, max_length=100)

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "username": "john_doe",
                "password": "secure_password123"
            }
        }
    )


class UserLogin(BaseModel):
    """Model for user login."""
    username: str
    password: str

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "username": "john_doe",
                "password": "secure_password123"
            }
        }
    )


class Token(BaseModel):
    """JWT token response model."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int = ACCESS_TOKEN_EXPIRE_MINUTES * 60  # in seconds

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "token_type": "bearer",
                "expires_in": 1800
            }
        }
    )


class TokenData(BaseModel):
    """Data extracted from JWT token."""
    username: Optional[str] = None


class User(UserBase):
    """User model with all fields."""
    created_at: str

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "username": "john_doe",
                "created_at": "2025-11-23T10:00:00.000000"
            }
        }
    )


class UserInDB(User):
    """User model as stored in database (includes hashed password)."""
    hashed_password: str


# ==================== Password Utilities ====================

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain password against a hashed password.

    Args:
        plain_password: The plain text password
        hashed_password: The hashed password to compare against

    Returns:
        True if password matches, False otherwise
    """
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """
    Hash a password using bcrypt.

    Args:
        password: Plain text password to hash

    Returns:
        Hashed password
    """
    return pwd_context.hash(password)


# ==================== JWT Token Utilities ====================

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token.

    Args:
        data: Dictionary of claims to encode in the token
        expires_delta: Optional custom expiration time

    Returns:
        Encoded JWT token string
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)

    to_encode.update({"exp": expire, "iat": datetime.utcnow()})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> TokenData:
    """
    Decode and validate a JWT token.

    Args:
        token: JWT token string

    Returns:
        TokenData with username extracted from token

    Raises:
        HTTPException: If token is invalid or expired
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        token_data = TokenData(username=username)
        return token_data
    except JWTError:
        raise credentials_exception


# ==================== User Storage ====================

class UserStorage:
    """
    Simple in-memory and file-based user storage.

    For production, replace this with a proper database (SQLite, PostgreSQL, etc.)
    """

    def __init__(self, storage_file: str = "users.json"):
        """
        Initialize user storage.

        Args:
            storage_file: Path to JSON file for persistent storage
        """
        self.storage_file = storage_file
        self.users: Dict[str, UserInDB] = {}
        self._load_users()

    def _load_users(self):
        """Load users from storage file if it exists."""
        if os.path.exists(self.storage_file):
            try:
                with open(self.storage_file, 'r') as f:
                    data = json.load(f)
                    for username, user_data in data.items():
                        self.users[username] = UserInDB(**user_data)
            except Exception as e:
                print(f"Warning: Could not load users from {self.storage_file}: {e}")

    def _save_users(self):
        """Save users to storage file."""
        try:
            data = {username: user.model_dump() for username, user in self.users.items()}
            with open(self.storage_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Warning: Could not save users to {self.storage_file}: {e}")

    def get_user(self, username: str) -> Optional[UserInDB]:
        """
        Get a user by username.

        Args:
            username: Username to lookup

        Returns:
            UserInDB object if found, None otherwise
        """
        return self.users.get(username)

    def create_user(self, username: str, password: str) -> UserInDB:
        """
        Create a new user.

        Args:
            username: Username for the new user
            password: Plain text password (will be hashed)

        Returns:
            Created UserInDB object

        Raises:
            ValueError: If username already exists
        """
        if username in self.users:
            raise ValueError(f"Username '{username}' already exists")

        user = UserInDB(
            username=username,
            hashed_password=get_password_hash(password),
            created_at=datetime.utcnow().isoformat()
        )
        self.users[username] = user
        self._save_users()
        return user

    def authenticate_user(self, username: str, password: str) -> Optional[UserInDB]:
        """
        Authenticate a user by username and password.

        Args:
            username: Username to authenticate
            password: Plain text password to verify

        Returns:
            UserInDB object if authentication succeeds, None otherwise
        """
        user = self.get_user(username)
        if not user:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        return user


# Global user storage instance
user_storage = UserStorage()


# ==================== FastAPI Dependencies ====================

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> User:
    """
    FastAPI dependency to get the current authenticated user from JWT token.

    Args:
        credentials: HTTP Bearer credentials from request header

    Returns:
        User object for authenticated user

    Raises:
        HTTPException: If authentication fails
    """
    token = credentials.credentials
    token_data = decode_access_token(token)

    user = user_storage.get_user(username=token_data.username)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Return User without hashed_password
    return User(
        username=user.username,
        created_at=user.created_at
    )
