# 🚀 OG-AI Agent - Quick Start

## Your API is LIVE! ✓

**URL:** https://og-ai.onrender.com
**Docs:** https://og-ai.onrender.com/docs

---

## Test It Now (30 seconds)

### PowerShell
```powershell
# Quick test
.\test_deployment.ps1

# Or manually
Invoke-RestMethod -Uri "https://og-ai.onrender.com/health"
```

### Python
```python
# Install requests if needed: pip install requests
from client_example import OGAIClient

client = OGAIClient()
print(client.chat("Hello!"))
```

### Browser
Open: https://og-ai.onrender.com/docs

---

## API Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/health` | Check service status |
| POST | `/chat` | Send message to AI |
| GET | `/history` | Get conversation history |
| POST | `/reset` | Clear conversation |
| GET | `/docs` | Interactive documentation |

---

## Example Usage

### PowerShell
```powershell
$body = @{ message = "Tell me a joke" } | ConvertTo-Json
Invoke-RestMethod -Uri "https://og-ai.onrender.com/chat" -Method Post -Body $body -ContentType "application/json"
```

### Python
```python
import requests
response = requests.post(
    "https://og-ai.onrender.com/chat",
    json={"message": "Tell me a joke"}
)
print(response.json()["response"])
```

### JavaScript
```javascript
fetch('https://og-ai.onrender.com/chat', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({message: 'Tell me a joke'})
})
.then(r => r.json())
.then(d => console.log(d.response));
```

### curl
```bash
curl -X POST https://og-ai.onrender.com/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Tell me a joke"}'
```

---

## Where Does Gunicorn Run?

### Production (Render.com)
✓ **Runs automatically** - no action needed!
- Render reads `Procfile` and starts gunicorn
- Already running at https://og-ai.onrender.com

### Local Development (Windows)
```powershell
# Use Uvicorn (Gunicorn doesn't work on Windows)
python -m uvicorn app:app --reload --port 8000
```

### Local Development (Linux/WSL)
```bash
# Can use Gunicorn
gunicorn app:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000
```

---

## Documentation

| File | What's In It |
|------|-------------|
| `SETUP_SUMMARY.md` | ⭐ Complete overview - START HERE |
| `USAGE_GUIDE.md` | API usage examples |
| `CI_CD_TROUBLESHOOTING.md` | Fix problems |
| `test_deployment.ps1` | Test script |
| `client_example.py` | Python client |

---

## Common Issues

### Service is slow to respond
**Why:** Free tier sleeps after 15 min of inactivity
**Fix:** First request takes 30-60 sec to wake up (normal)

### Want to run locally
```powershell
# Development mode
python -m uvicorn app:app --reload --port 8000

# Access at: http://localhost:8000
```

### Need help
1. Read `SETUP_SUMMARY.md` (complete guide)
2. Read `CI_CD_TROUBLESHOOTING.md` (fixes)
3. Check Render logs (Dashboard → Logs)

---

## What Was Fixed

✓ wsgi.py syntax error (removed duplicate gunicorn command)
✓ All endpoints working and tested
✓ Documentation created
✓ Test scripts created
✓ Everything deployed and operational

---

## Next Steps

1. **Test:** Run `.\test_deployment.ps1`
2. **Explore:** Open https://og-ai.onrender.com/docs
3. **Use:** Integrate API into your projects
4. **Customize:** Edit `ai_agent.py` for your needs

---

## Summary

- ✅ API is live and working
- ✅ No deployment needed (already done)
- ✅ No server/hosting needed (using Render)
- ✅ Gunicorn running automatically
- ✅ All problems fixed
- ✅ Ready to use anywhere

**Start using your API now!** 🎉

Base URL: `https://og-ai.onrender.com`

