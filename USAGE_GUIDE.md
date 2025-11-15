# 🎯 OG-AI Agent - Complete Usage Guide

## 🌐 Your Deployed API

**Base URL:** `https://og-ai.onrender.com`

**API Endpoints:**
- `GET /` - Service information
- `GET /health` - Health check
- `POST /chat` - Send messages to the AI agent
- `GET /history` - Get conversation history
- `POST /reset` - Clear conversation history
- `GET /docs` - Interactive API documentation (Swagger UI)

---

## 🚀 Quick Start - Test Your API

### 1. Check if Service is Running

```powershell
# Using PowerShell
Invoke-RestMethod -Uri "https://og-ai.onrender.com/health" -Method Get
```

```bash
# Using curl (Git Bash or WSL)
curl https://og-ai.onrender.com/health
```

**Expected Response:**
```json
{
  "status": "healthy",
  "agent_name": "OG-AI",
  "message": "Service is running"
}
```

### 2. Send a Message to Your AI Agent

```powershell
# PowerShell
$body = @{
    message = "Hello! What can you do?"
} | ConvertTo-Json

Invoke-RestMethod -Uri "https://og-ai.onrender.com/chat" `
    -Method Post `
    -Body $body `
    -ContentType "application/json"
```

```bash
# curl
curl -X POST https://og-ai.onrender.com/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello! What can you do?"}'
```

**Expected Response:**
```json
{
  "response": "Hello! I'm OG-AI, your AI assistant...",
  "agent_name": "OG-AI",
  "timestamp": "2025-11-14T12:00:00.000000"
}
```

### 3. View Interactive Documentation

Open in your browser:
```
https://og-ai.onrender.com/docs
```

This gives you a beautiful Swagger UI where you can test all endpoints directly!

---

## 📝 Using Your API from a Website

### Simple HTML + JavaScript Example

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>OG-AI Chat Interface</title>
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 600px;
            margin: 50px auto;
            padding: 20px;
        }
        #chat-box {
            border: 1px solid #ccc;
            height: 400px;
            overflow-y: auto;
            padding: 10px;
            margin-bottom: 10px;
        }
        .message {
            margin: 10px 0;
            padding: 10px;
            border-radius: 5px;
        }
        .user {
            background-color: #e3f2fd;
            text-align: right;
        }
        .agent {
            background-color: #f5f5f5;
        }
        #message-input {
            width: 80%;
            padding: 10px;
        }
        #send-button {
            padding: 10px 20px;
        }
    </style>
</head>
<body>
    <h1>OG-AI Chat</h1>
    <div id="chat-box"></div>
    <input type="text" id="message-input" placeholder="Type your message...">
    <button id="send-button">Send</button>

    <script>
        const API_BASE = 'https://og-ai.onrender.com';
        const chatBox = document.getElementById('chat-box');
        const messageInput = document.getElementById('message-input');
        const sendButton = document.getElementById('send-button');

        function addMessage(content, isUser) {
            const messageDiv = document.createElement('div');
            messageDiv.className = `message ${isUser ? 'user' : 'agent'}`;
            messageDiv.textContent = content;
            chatBox.appendChild(messageDiv);
            chatBox.scrollTop = chatBox.scrollHeight;
        }

        async function sendMessage() {
            const message = messageInput.value.trim();
            if (!message) return;

            // Display user message
            addMessage(message, true);
            messageInput.value = '';

            try {
                const response = await fetch(`${API_BASE}/chat`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({ message })
                });

                const data = await response.json();
                
                if (response.ok) {
                    addMessage(data.response, false);
                } else {
                    addMessage(`Error: ${data.detail || 'Failed to get response'}`, false);
                }
            } catch (error) {
                addMessage(`Error: ${error.message}`, false);
            }
        }

        sendButton.addEventListener('click', sendMessage);
        messageInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') sendMessage();
        });
    </script>
</body>
</html>
```

Save this as `chat.html` and open it in your browser!

---

## 🔧 API Endpoints in Detail

### 1. GET `/` or `/health`
**Purpose:** Check if the service is running

**Response:**
```json
{
  "status": "healthy",
  "agent_name": "OG-AI",
  "message": "Service is running"
}
```

### 2. POST `/chat`
**Purpose:** Send a message to the AI agent

**Request Body:**
```json
{
  "message": "Your message here"
}
```

**Response:**
```json
{
  "response": "AI agent's response",
  "agent_name": "OG-AI",
  "timestamp": "2025-11-14T12:00:00.000000"
}
```

### 3. GET `/history`
**Purpose:** Get the full conversation history

**Response:**
```json
{
  "conversation": [
    {
      "role": "user",
      "content": "Hello!",
      "timestamp": "2025-11-14T12:00:00.000000"
    },
    {
      "role": "assistant",
      "content": "Hi there!",
      "timestamp": "2025-11-14T12:00:05.000000"
    }
  ],
  "history": [...],
  "message_count": 2
}
```

### 4. POST `/reset`
**Purpose:** Clear the conversation history

**Response:**
```json
{
  "status": "success",
  "agent_name": "OG-AI",
  "message": "Conversation history has been reset"
}
```

---

## 🐍 Python Client Example

```python
import requests

class OGAIClient:
    def __init__(self, base_url="https://og-ai.onrender.com"):
        self.base_url = base_url
    
    def health_check(self):
        """Check if the service is running"""
        response = requests.get(f"{self.base_url}/health")
        return response.json()
    
    def chat(self, message):
        """Send a message to the AI agent"""
        response = requests.post(
            f"{self.base_url}/chat",
            json={"message": message}
        )
        return response.json()
    
    def get_history(self):
        """Get conversation history"""
        response = requests.get(f"{self.base_url}/history")
        return response.json()
    
    def reset(self):
        """Reset conversation history"""
        response = requests.post(f"{self.base_url}/reset")
        return response.json()

# Usage
if __name__ == "__main__":
    client = OGAIClient()
    
    # Check health
    print("Health:", client.health_check())
    
    # Chat with agent
    response = client.chat("Hello! Tell me a joke.")
    print("Agent:", response['response'])
    
    # Get history
    history = client.get_history()
    print(f"Messages: {history['message_count']}")
```

---

## 🔑 No API Key Required!

Your current deployment is **open and public** - no API key needed! This is great for:
- ✅ Personal projects
- ✅ Learning and testing
- ✅ Open demos

**Want to add authentication?** See `SECURITY_GUIDE.md` for adding API keys.

---

## 🐛 Troubleshooting

### Service is sleeping (Free Render plan)
Render's free tier puts services to sleep after 15 minutes of inactivity. First request may take 30-60 seconds.

**Solution:** Upgrade to a paid plan or use a service like UptimeRobot to ping your service every 10 minutes.

### CORS Errors from Browser
Your service allows all origins by default. If you need to restrict:

1. Set environment variable in Render Dashboard:
   ```
   ALLOWED_ORIGINS=["https://yourdomain.com"]
   ```

### Connection Timeout
Render's free tier has limitations. For production:
- Upgrade to a paid plan
- Use a CDN
- Implement caching

---

## 📚 Additional Resources

- **API Documentation:** `https://og-ai.onrender.com/docs`
- **Deployment Guide:** `DEPLOYMENT.md`
- **Security Guide:** `SECURITY_GUIDE.md`
- **Troubleshooting:** `CI_CD_TROUBLESHOOTING.md`

---

## 🎉 You're All Set!

Your OG-AI agent is live at:
```
https://og-ai.onrender.com
```

Start building amazing AI-powered applications! 🚀

