# Deployment Guide for OG-AI Agent on Render

This guide will help you deploy your OG-AI agent to Render at https://og-ai.onrender.com

## Quick Deploy

### Option 1: Using Render Dashboard (Recommended)

1. **Go to Render Dashboard**: https://dashboard.render.com
2. **Create New Web Service**:
   - Click "New +" → "Web Service"
   - Connect your GitHub repository
   - Or choose "Deploy from Git URL"

3. **Configure the service**:
   ```
   Name: og-ai
   Region: Choose closest to your users
   Branch: main
   Root Directory: OG-AI-
   Runtime: Python 3
   Build Command: pip install -r requirements.txt
   Start Command: uvicorn app:app --host 0.0.0.0 --port $PORT
   ```

4. **Set Environment Variables** (Optional):
   ```
   PYTHON_VERSION=3.12.0
   ALLOWED_ORIGINS=["https://og-ai.onrender.com"]
   DEVELOPMENT_MODE=false
   ```

5. **Deploy**: Click "Create Web Service"

### Option 2: Using render.yaml (Infrastructure as Code)

Your project already has a `render.yaml` file. To use it:

1. Go to Render Dashboard
2. Click "New +" → "Blueprint"
3. Connect your repository
4. Render will automatically detect and use the `render.yaml` configuration

## Post-Deployment

### Check Service Status

1. Go to your service dashboard on Render
2. Check the "Logs" tab to see if the service started successfully
3. Visit your health endpoint: `https://og-ai.onrender.com/health`
   - You should see: `{"status": "ok"}`

### Test the API

```bash
# Health check
curl https://og-ai.onrender.com/health

# Test chat endpoint
curl -X POST https://og-ai.onrender.com/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello!"}'

# Get conversation history
curl https://og-ai.onrender.com/history
```

## API Endpoints

Your deployed API will have these endpoints:

- `GET /health` - Health check endpoint
- `GET /` - Root endpoint with API info
- `POST /chat` - Send a message to the AI agent
- `GET /history` - Get conversation history
- `DELETE /history` - Clear conversation history
- `GET /config` - Get agent configuration

## Troubleshooting

### Service Won't Start

1. **Check Logs**: Go to Render Dashboard → Your Service → Logs
2. **Common Issues**:
   - Missing dependencies: Make sure `requirements.txt` is complete
   - Port configuration: Render automatically sets `$PORT` environment variable
   - Python version: Specified in `runtime.txt` or environment variable

### 502 Bad Gateway

This usually means the app isn't responding on the correct port:
- Ensure your start command uses `--port $PORT`
- Check that uvicorn is installed: `uvicorn[standard]>=0.24.0` in requirements.txt

### Slow Cold Starts

Free tier services sleep after 15 minutes of inactivity:
- First request after sleeping takes 30-60 seconds
- Consider upgrading to a paid plan for always-on service
- Or use a service like UptimeRobot to ping your service periodically

## Using WSGI Servers (Alternative)

If you need WSGI compatibility:

### With Gunicorn + Uvicorn Workers (Recommended for production)
```bash
gunicorn app:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT
```

Update your Render start command to:
```
gunicorn app:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT
```

### With WSGI Bridge (Legacy compatibility)
```bash
gunicorn wsgi:application --bind 0.0.0.0:$PORT
```

Note: This requires `asgiref` to be installed (already in requirements.txt)

## Connecting Your Website

Once deployed, update your website's API configuration to point to:
```
https://og-ai.onrender.com
```

Example JavaScript fetch:
```javascript
async function chatWithAgent(message) {
  const response = await fetch('https://og-ai.onrender.com/chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({ message })
  });
  const data = await response.json();
  return data.response;
}
```

## Security Best Practices

1. **CORS Configuration**: Set `ALLOWED_ORIGINS` environment variable
   ```
   ALLOWED_ORIGINS=["https://yourdomain.com"]
   ```

2. **Rate Limiting**: Consider adding rate limiting for production
3. **API Keys**: Implement API key authentication if needed
4. **HTTPS**: Render provides free SSL certificates automatically

## Monitoring

- **Render Dashboard**: View logs, metrics, and deployment history
- **Health Checks**: Render automatically monitors `/health` endpoint
- **Custom Monitoring**: Integrate with services like:
  - DataDog
  - New Relic
  - Sentry

## Scaling

Free tier limitations:
- 512 MB RAM
- Sleeps after 15 minutes of inactivity
- Limited CPU time

To scale:
1. Upgrade to Starter plan ($7/month) or higher
2. Add more instances for redundancy
3. Use a database for persistent conversation history
4. Implement caching for better performance

## Support

- **Render Documentation**: https://render.com/docs
- **Service URL**: https://og-ai.onrender.com
- **Dashboard**: https://dashboard.render.com

---

**Your service is ready to use!** 🎉

Visit: https://og-ai.onrender.com/health to verify it's running.

