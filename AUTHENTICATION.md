# JWT Token Authentication Guide

## Overview

OG-AI now supports **JWT (JSON Web Token)** authentication for secure, per-user conversation management. Each authenticated user has their own isolated conversation history.

## Features

- ✅ **User Registration** - Create new user accounts
- ✅ **JWT Token Authentication** - Secure token-based auth
- ✅ **Per-User Conversation History** - Isolated chat sessions
- ✅ **Password Hashing** - Bcrypt encryption for passwords
- ✅ **Token Expiration** - Configurable token lifetime
- ✅ **Protected Endpoints** - Secure access to chat, history, and reset

---

## Environment Variables

Configure these environment variables for JWT authentication:

```bash
# JWT Configuration
JWT_SECRET_KEY=your-super-secret-key-here-change-in-production
ACCESS_TOKEN_EXPIRE_MINUTES=30

# Existing Configuration
PORT=8000
DEVELOPMENT_MODE=false
ALLOWED_ORIGINS='["http://localhost:3000"]'
```

### Important Security Notes

⚠️ **CHANGE THE DEFAULT SECRET KEY** - The default `JWT_SECRET_KEY` is insecure for production. Generate a strong secret:

```bash
# Generate a secure secret key (Python)
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Or using OpenSSL
openssl rand -hex 32
```

---

## API Endpoints

### Public Endpoints (No Authentication Required)

#### Health Check
```http
GET /
GET /health
```

**Response:**
```json
{
  "status": "healthy",
  "agent_name": "OG-AI",
  "message": "OG-AI Agent API is running. Visit /docs for API documentation."
}
```

---

### Authentication Endpoints

#### 1. Register New User

```http
POST /auth/register
Content-Type: application/json

{
  "username": "john_doe",
  "password": "secure_password123"
}
```

**Requirements:**
- Username: 3-50 characters, alphanumeric + underscore/hyphen only
- Password: Minimum 6 characters

**Success Response (201):**
```json
{
  "username": "john_doe",
  "created_at": "2025-11-23T10:00:00.000000"
}
```

**Error Responses:**
- `400` - Username already exists
- `422` - Validation error (invalid username/password format)

---

#### 2. Login (Get JWT Token)

```http
POST /auth/login
Content-Type: application/json

{
  "username": "john_doe",
  "password": "secure_password123"
}
```

**Success Response (200):**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_in": 1800
}
```

**Error Response:**
- `401` - Incorrect username or password

---

#### 3. Get Current User Info

```http
GET /auth/me
Authorization: Bearer <your_jwt_token>
```

**Success Response (200):**
```json
{
  "username": "john_doe",
  "created_at": "2025-11-23T10:00:00.000000"
}
```

---

### Protected Endpoints (Require Authentication)

All requests to protected endpoints must include the JWT token in the `Authorization` header:

```http
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

#### 1. Send Chat Message

```http
POST /chat
Authorization: Bearer <your_jwt_token>
Content-Type: application/json

{
  "message": "Hello, how can you help me today?"
}
```

**Success Response (200):**
```json
{
  "response": "Hello! I'm OG-AI, your AI assistant. I can help you with...",
  "agent_name": "OG-AI",
  "timestamp": "2025-11-23T10:05:00.000000"
}
```

**Error Responses:**
- `400` - Empty message
- `401` - Invalid or expired token
- `403` - Missing authentication token

---

#### 2. Get Conversation History

```http
GET /history
Authorization: Bearer <your_jwt_token>
```

**Success Response (200):**
```json
{
  "conversation": [
    {
      "role": "user",
      "content": "Hello!",
      "timestamp": "2025-11-23T10:05:00.000000"
    },
    {
      "role": "assistant",
      "content": "Hello! How can I help you?",
      "timestamp": "2025-11-23T10:05:01.000000"
    }
  ],
  "history": [...],
  "message_count": 2
}
```

---

#### 3. Reset Conversation History

```http
POST /reset
Authorization: Bearer <your_jwt_token>
```

**Success Response (200):**
```json
{
  "status": "success",
  "agent_name": "OG-AI",
  "message": "Conversation history has been cleared"
}
```

---

#### 4. Clear History (Alias for /reset)

```http
POST /clear
Authorization: Bearer <your_jwt_token>
```

Same response as `/reset`.

---

## Usage Examples

### Python Example

```python
import requests

BASE_URL = "http://localhost:8000"

# 1. Register a new user
register_response = requests.post(
    f"{BASE_URL}/auth/register",
    json={"username": "testuser", "password": "password123"}
)
print(register_response.json())

# 2. Login to get JWT token
login_response = requests.post(
    f"{BASE_URL}/auth/login",
    json={"username": "testuser", "password": "password123"}
)
token = login_response.json()["access_token"]

# 3. Use the token for authenticated requests
headers = {"Authorization": f"Bearer {token}"}

# Send a chat message
chat_response = requests.post(
    f"{BASE_URL}/chat",
    json={"message": "Hello!"},
    headers=headers
)
print(chat_response.json())

# Get conversation history
history_response = requests.get(
    f"{BASE_URL}/history",
    headers=headers
)
print(history_response.json())

# Reset conversation
reset_response = requests.post(
    f"{BASE_URL}/reset",
    headers=headers
)
print(reset_response.json())
```

### cURL Example

```bash
# Register
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "testuser", "password": "password123"}'

# Login and save token
TOKEN=$(curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "testuser", "password": "password123"}' \
  | jq -r '.access_token')

# Chat with token
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"message": "Hello!"}'

# Get history
curl -X GET http://localhost:8000/history \
  -H "Authorization: Bearer $TOKEN"

# Reset conversation
curl -X POST http://localhost:8000/reset \
  -H "Authorization: Bearer $TOKEN"
```

### JavaScript/Fetch Example

```javascript
const BASE_URL = 'http://localhost:8000';
let authToken;

// 1. Register
async function register() {
  const response = await fetch(`${BASE_URL}/auth/register`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      username: 'testuser',
      password: 'password123'
    })
  });
  return response.json();
}

// 2. Login
async function login() {
  const response = await fetch(`${BASE_URL}/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      username: 'testuser',
      password: 'password123'
    })
  });
  const data = await response.json();
  authToken = data.access_token;
  return data;
}

// 3. Send chat message
async function sendMessage(message) {
  const response = await fetch(`${BASE_URL}/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${authToken}`
    },
    body: JSON.stringify({ message })
  });
  return response.json();
}

// 4. Get history
async function getHistory() {
  const response = await fetch(`${BASE_URL}/history`, {
    headers: {
      'Authorization': `Bearer ${authToken}`
    }
  });
  return response.json();
}

// Usage
await register();
await login();
const chatResponse = await sendMessage('Hello!');
console.log(chatResponse);
```

---

## User Storage

User accounts are stored in `users.json` in the project root directory. This file is automatically created when the first user registers.

**Format:**
```json
{
  "john_doe": {
    "username": "john_doe",
    "hashed_password": "$2b$12$...",
    "created_at": "2025-11-23T10:00:00.000000"
  }
}
```

### Production Considerations

For production deployments, consider:

1. **Database Integration** - Replace JSON file storage with PostgreSQL/MongoDB
2. **User Limits** - Implement rate limiting and user quotas
3. **Email Verification** - Add email confirmation for new accounts
4. **Password Reset** - Implement forgot password functionality
5. **Refresh Tokens** - Add refresh token mechanism for long-lived sessions
6. **HTTPS Only** - Always use HTTPS in production
7. **Token Blacklist** - Implement logout with token invalidation

---

## Security Best Practices

1. **Use HTTPS** - Always use HTTPS in production to protect tokens in transit
2. **Strong Secret Keys** - Use cryptographically secure random secrets
3. **Short Token Expiry** - Keep token expiration times reasonable (15-60 minutes)
4. **Secure Password Policy** - Enforce strong password requirements
5. **Rate Limiting** - Implement rate limiting on auth endpoints
6. **CORS Configuration** - Set specific allowed origins, not wildcard `*`
7. **Regular Updates** - Keep dependencies updated for security patches

---

## Testing

Run the comprehensive test suite:

```bash
# Install dependencies
pip install -r requirements.txt

# Run all tests
pytest

# Run only authentication tests
pytest test_auth.py -v

# Run with coverage
pytest --cov=. --cov-report=html
```

---

## API Documentation

Interactive API documentation is available at:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

These provide interactive testing of all endpoints, including authentication.

---

## Troubleshooting

### "Could not validate credentials" Error
- Your token may have expired (default: 30 minutes)
- Login again to get a fresh token

### 403 Forbidden Error
- You're missing the `Authorization` header
- Ensure you're including `Authorization: Bearer <token>` in requests

### 401 Unauthorized Error
- Invalid username/password
- Token is malformed or tampered with
- Token signature verification failed (secret key mismatch)

### Users Not Persisting
- Check file permissions on `users.json`
- Ensure the application has write access to the directory

---

## Migration from Previous Version

If upgrading from a non-authenticated version:

1. All existing endpoints now require authentication (except `/`, `/health`)
2. Update client code to:
   - Register users or login
   - Include JWT token in all requests
   - Handle 401/403 errors and re-authenticate

3. Each user will have their own conversation history (no shared state)

---

## Support

For issues or questions:
- Check the `/docs` endpoint for interactive API documentation
- Review test files (`test_auth.py`, `test_app.py`) for usage examples
- Open an issue on the project repository

---

**Version**: 2.0.0 (JWT Authentication)
**Last Updated**: 2025-11-23
