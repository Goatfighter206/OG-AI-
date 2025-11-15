# CI/CD Troubleshooting Guide for OG-AI Agent

## 🎯 Overview

This guide helps you troubleshoot common CI/CD issues for the OG-AI Agent deployment.

---

## 🔧 Common Issues and Solutions

### 1. **Gunicorn Not Found / Import Errors**

**Problem:** Deployment fails with `ModuleNotFoundError: No module named 'gunicorn'` or similar

**Solution:**

```bash
# Ensure gunicorn is in requirements.txt
pip install gunicorn uvicorn[standard]

# Verify requirements.txt contains:
# gunicorn>=21.2.0
# uvicorn[standard]>=0.24.0
```

**Check:** Open `requirements.txt` and verify these lines exist.

---

### 2. **WSGI vs ASGI Confusion**

**Problem:** Error about WSGI compatibility or `WsgiToAsgi` issues

**Explanation:**

- FastAPI is an **ASGI** application (async)
- Gunicorn is traditionally a **WSGI** server (sync)
- Solution: Use Gunicorn with Uvicorn worker class

**Correct Command:**

```bash
gunicorn app:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT
```

**What NOT to do:**

- ❌ Don't use `gunicorn wsgi:application` (wrong for FastAPI)
- ❌ Don't try to convert ASGI to WSGI (unnecessary complexity)
- ✅ Use `gunicorn app:app` with Uvicorn worker class

---

### 3. **Gunicorn on Windows**

**Problem:** `gunicorn` command not found on Windows

**Explanation:** Gunicorn doesn't support Windows natively.

**Solutions:**

**Option A: Development (Use Uvicorn directly)**

```powershell
# For local development on Windows
python -m uvicorn app:app --reload --port 8000
```

**Option B: Production (Use WSL, Docker, or Linux CI/CD)**

```bash
# In WSL or Linux
gunicorn app:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

**Option C: Use Docker**

```yaml
# docker-compose.yml already configured
docker-compose up
```

---

### 4. **Render Deployment Issues**

**Problem:** Service fails to start on Render

**Common Causes:**

#### A. Wrong Start Command

**Check:** Your `Procfile` should contain:

```
web: gunicorn app:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT --timeout 120 --access-logfile - --error-logfile -
```

#### B. Python Version Mismatch

**Check:** `runtime.txt` should specify a supported version:

```
python-3.12.0
```

#### C. Missing Dependencies

**Check:** All dependencies in `requirements.txt`:

```
fastapi>=0.109.1
uvicorn[standard]>=0.24.0
gunicorn>=21.2.0
pydantic>=2.0.0
```

#### D. Port Binding Issue

**Solution:** Always use `$PORT` environment variable:

```python
# In app.py or wsgi.py
port = int(os.environ.get("PORT", 8000))
```

---

### 5. **GitHub Actions Self-Hosted Runner Issues**

**Problem:** Self-hosted runner on Windows can't run certain commands

**Solutions:**

#### A. PowerShell Syntax

```yaml
# Use PowerShell syntax for Windows runners
- name: Run tests
  run: |
    if (Test-Path "test_*.py") {
      python -m pytest -v
    }
  shell: powershell
```

#### B. Gunicorn on Self-Hosted Windows Runner

```yaml
# Don't test gunicorn on Windows runners
- name: Test application
  run: |
    # Use uvicorn for testing on Windows
    python -m uvicorn app:app --port 8000 &
    Start-Sleep -Seconds 5
    # Run tests...
  shell: powershell
```

---

### 6. **Port Binding Errors**

**Problem:** `Address already in use` or port binding fails

**Solutions:**

**Check if port is in use:**

```powershell
# Windows
netstat -ano | findstr :8000

# Kill process if needed
taskkill /PID <PID> /F
```

**Use dynamic port:**

```python
import os
port = int(os.environ.get("PORT", 8000))
```

---

### 7. **Environment Variables Not Loading**

**Problem:** Config values not found, API keys missing

**Solutions:**

**Local Development:**

```powershell
# Create .env file
echo "ENVIRONMENT=development" > .env
echo "DEVELOPMENT_MODE=true" >> .env
```

**Render Deployment:**

1. Go to Render Dashboard
2. Select your service
3. Click "Environment"
4. Add variables:
   - `ENVIRONMENT=production`
   - `DEVELOPMENT_MODE=false`
   - `ALLOWED_ORIGINS=["https://og-ai-agent.com"]`

---

## 🔍 Debugging Commands

### Check Service Status

```powershell
# Local
curl http://localhost:8000/health

# Production
curl https://og-ai-agent.com/health
```

### View Logs (Render)

1. Go to Render Dashboard
2. Select your service
3. Click "Logs" tab
4. Look for errors at startup

### Test Locally

```powershell
# Development mode
python -m uvicorn app:app --reload

# Production-like mode
python wsgi.py
```

### Verify Dependencies

```powershell
pip list | findstr gunicorn
pip list | findstr uvicorn
pip list | findstr fastapi
```

---

## 📋 Pre-Deployment Checklist

Before deploying, verify:

- [ ] `app.py` exists and imports correctly
- [ ] `ai_agent.py` exists and imports correctly
- [ ] `requirements.txt` has all dependencies
- [ ] `Procfile` uses correct start command
- [ ] `runtime.txt` specifies Python version
- [ ] `wsgi.py` doesn't contain shell commands
- [ ] Environment variables are set
- [ ] Tests pass locally
- [ ] `/health` endpoint works

---

## 🚀 Quick Commands Reference

### Local Development (Windows)

```powershell
# Start development server
python -m uvicorn app:app --reload --port 8000
```

### Local Testing (WSL/Linux)

```bash
# Production-like testing
gunicorn app:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

### Docker

```bash
# Build and run
docker-compose up --build
```

### Test Your Deployment

```powershell
# Health check
Invoke-RestMethod -Uri "https://og-ai-agent.com/health"

# Send a message
$body = @{ message = "Hello!" } | ConvertTo-Json
Invoke-RestMethod -Uri "https://og-ai-agent.com/chat" -Method Post -Body $body -ContentType "application/json"
```

---

## ✅ Success Indicators

Your deployment is working if:

1. ✅ Health endpoint returns: `{"status": "healthy"}`
2. ✅ Chat endpoint accepts messages
3. ✅ Logs show: "Application startup complete"
4. ✅ No import errors in logs
5. ✅ Service stays running (doesn't crash)

**Test URL:** `https://og-ai-agent.com/health`

---

## 🎓 Key Takeaways

1. **FastAPI = ASGI, not WSGI** - Use Gunicorn with Uvicorn worker class
2. **Gunicorn doesn't work on Windows** - Use Uvicorn for development, deploy on Linux
3. **Environment variables matter** - Use `$PORT` for Render
4. **Test locally before deploying** - Verify imports and endpoints work
5. **Check logs first** - Render Dashboard → Logs for startup errors
