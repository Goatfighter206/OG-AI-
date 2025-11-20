# 🚀 How to Start Your OG-AI Agent with Gunicorn

## Quick Answer

**For Local Development:**
```powershell
cd C:\Users\willi\ai-agents-deploy\OG-AI-
python -m uvicorn app:app --reload --port 8000
```

**For Production (Local Testing):**
```powershell
cd C:\Users\willi\ai-agents-deploy\OG-AI-
gunicorn app:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

**For Render Deployment:**
Gunicorn starts **automatically** - you don't need to run it manually!

---

## 📍 Where Gunicorn Runs

### On Render.com (Your Deployment)
✅ **Automatic** - Render starts gunicorn for you using the `Procfile`:
- Location: `OG-AI-/Procfile`
- Command: `gunicorn app:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT`
- **You don't need to start it manually!**

### On Your Local Machine (For Testing)
⚡ **Manual** - You run it in your terminal for testing

---

## 🎯 Step-by-Step: Starting Locally

### Option 1: Easy Way (Development)
```powershell
# 1. Open PowerShell
# 2. Navigate to project
cd C:\Users\willi\ai-agents-deploy\OG-AI-

# 3. Run the quick start script
.\start.ps1

# Follow the prompts - it will ask if you want to start the server
```

### Option 2: Manual Start (Development with Auto-Reload)
```powershell
# 1. Navigate to project
cd C:\Users\willi\ai-agents-deploy\OG-AI-

# 2. Activate virtual environment (if you have one)
.\venv\Scripts\Activate.ps1

# 3. Start with uvicorn (best for development)
python -m uvicorn app:app --reload --port 8000

# Server will start at: http://localhost:8000
```

### Option 3: Production Mode (Local Testing)
```powershell
# 1. Navigate to project
cd C:\Users\willi\ai-agents-deploy\OG-AI-

# 2. Activate virtual environment (if you have one)
.\venv\Scripts\Activate.ps1

# 3. Start with gunicorn (production mode)
gunicorn app:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --timeout 120

# Server will start at: http://localhost:8000
```

---

## 🖥️ Windows Note: Gunicorn Compatibility

**Important:** Gunicorn doesn't work natively on Windows. For local testing on Windows:

### Use Uvicorn Instead (Recommended)
```powershell
python -m uvicorn app:app --reload --port 8000
```

### Or Use Waitress (Windows-compatible WSGI server)
```powershell
# Install waitress
pip install waitress

# Start server
waitress-serve --listen=*:8000 --call app:app
```

### Or Use Docker (Runs Linux environment)
```powershell
# Build image
docker build -t og-ai .

# Run container
docker run -p 8000:8000 og-ai
```

---

## 🌐 For Render Deployment (Production)

### You DON'T Start Gunicorn Manually!

When you deploy to Render:

1. **Render reads your `Procfile`**:
   ```
   web: gunicorn app:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT --timeout 120
   ```

2. **Render automatically runs this command** when your service starts

3. **You just deploy** - Render handles the rest!

### Deploy to Render:
```
1. Go to: https://dashboard.render.com
2. Create Web Service
3. Connect your GitHub repo
4. Root Directory: OG-AI-
5. Click "Create Web Service"
```

Render will:
- ✅ Install dependencies
- ✅ Start gunicorn automatically
- ✅ Keep it running
- ✅ Restart if it crashes
- ✅ Handle HTTPS/SSL

---

## 🧪 Test Your Server

After starting (locally or on Render):

### Health Check
```powershell
# Local
Invoke-RestMethod http://localhost:8000/health

# Render
Invoke-RestMethod https://og-ai.onrender.com/health
```

### View API Docs
```powershell
# Local
start http://localhost:8000/docs

# Render
start https://og-ai.onrender.com/docs
```

### Chat Test
```powershell
# Local
Invoke-RestMethod -Uri http://localhost:8000/chat -Method POST -Body '{"message":"Hello"}' -ContentType "application/json"

# Render
Invoke-RestMethod -Uri https://og-ai.onrender.com/chat -Method POST -Body '{"message":"Hello"}' -ContentType "application/json"
```

---

## 🔍 Check If Server Is Running

### On Windows (Local)
```powershell
# Check if port 8000 is in use
netstat -ano | findstr :8000

# Or try to connect
Test-NetConnection -ComputerName localhost -Port 8000
```

### On Render (Production)
1. Go to: https://dashboard.render.com
2. Click on your service
3. Check "Logs" tab
4. Should see: "Uvicorn running on http://0.0.0.0:XXXXX"

---

## 🛑 Stop the Server

### Local Development
- Press `Ctrl+C` in the terminal where it's running

### Render Production
- Dashboard → Your Service → Suspend
- Or just leave it running (it's meant to run continuously!)

---

## 🚨 Troubleshooting

### "gunicorn: command not found"
**On Windows:** This is normal! Use uvicorn instead:
```powershell
python -m uvicorn app:app --reload --port 8000
```

**On Render:** Make sure `requirements.txt` includes `gunicorn>=21.2.0`

### "Port already in use"
```powershell
# Find what's using port 8000
netstat -ano | findstr :8000

# Kill the process (replace XXXX with PID from above)
taskkill /PID XXXX /F

# Or use a different port
python -m uvicorn app:app --reload --port 8001
```

### "Module not found"
```powershell
# Install dependencies
pip install -r requirements.txt

# Or run the setup script
.\start.ps1
```

---

## 📋 Quick Reference

| Environment | Command | When to Use |
|-------------|---------|-------------|
| **Windows Dev** | `python -m uvicorn app:app --reload` | Local development |
| **Windows Prod Test** | `waitress-serve --call app:app` | Testing production mode |
| **Linux/Mac Dev** | `uvicorn app:app --reload` | Local development |
| **Linux/Mac Prod** | `gunicorn app:app -k uvicorn.workers.UvicornWorker` | Production testing |
| **Render Deploy** | *Automatic via Procfile* | Production deployment |
| **Docker** | `docker-compose up` | Containerized deployment |

---

## ✅ Recommended Workflow

### For Development (Windows):
```powershell
cd C:\Users\willi\ai-agents-deploy\OG-AI-
python -m uvicorn app:app --reload --port 8000
```
- Visit: http://localhost:8000/docs
- Code changes auto-reload
- Easy debugging

### For Production (Render):
1. Push code to GitHub
2. Render auto-deploys
3. Gunicorn starts automatically
4. Visit: https://og-ai.onrender.com

**You never manually start gunicorn on Render!**

---

## 🎯 TL;DR

**Local Development (Windows):**
```powershell
cd C:\Users\willi\ai-agents-deploy\OG-AI-
python -m uvicorn app:app --reload --port 8000
```

**Production on Render:**
- Just deploy - gunicorn starts automatically!
- No manual commands needed!

**Your API will be at:**
- Local: http://localhost:8000
- Render: https://og-ai.onrender.com

---

*Need more help? See `CI_CD_TROUBLESHOOTING.md` or `RENDER_DEPLOY.md`*

