# OG-AI Deployment Guide

This guide covers multiple deployment options for the OG-AI agent.

## Table of Contents
- [Quick Start](#quick-start)
- [Render Deployment](#render-deployment)
- [Heroku Deployment](#heroku-deployment)
- [Docker Deployment](#docker-deployment)
- [AWS/GCP/Azure Deployment](#cloud-providers)
- [Environment Variables](#environment-variables)

## Quick Start

### Prerequisites
- Python 3.12 or higher
- pip package manager
- Git

### Local Testing
```bash
# Install dependencies
pip install -r requirements.txt

# Run in development mode
python ai_agent.py

# Or run with gunicorn (production-like)
gunicorn --bind 0.0.0.0:5000 --workers 2 --timeout 120 ai_agent:app
```

## Render Deployment

### Option 1: Blueprint Deployment (Recommended)

1. Fork or clone this repository to your GitHub account
2. Log in to [Render Dashboard](https://dashboard.render.com/)
3. Click "New +" → "Blueprint"
4. Connect your GitHub repository
5. Select the repository containing `render.yaml`
6. Click "Apply" - Render will automatically deploy!

### Option 2: Manual Deployment

1. Log in to [Render Dashboard](https://dashboard.render.com/)
2. Click "New +" → "Web Service"
3. Connect your GitHub repository
4. Configure:
   - **Name**: `og-ai-service`
   - **Environment**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 120 ai_agent:app`
   - **Instance Type**: Free tier or higher
5. Add environment variable:
   - `PYTHON_VERSION`: `3.12.0`
6. Set health check path: `/health`
7. Click "Create Web Service"

### After Deployment
Your service will be available at: `https://og-ai-service.onrender.com`

Test it:
```bash
curl https://your-service.onrender.com/health
curl -X POST https://your-service.onrender.com/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello!"}'
```

## Heroku Deployment

### Prerequisites
- [Heroku CLI](https://devcenter.heroku.com/articles/heroku-cli) installed
- Heroku account

### Deployment Steps

1. **Login to Heroku**
```bash
heroku login
```

2. **Create a new Heroku app**
```bash
heroku create your-og-ai-app
```

3. **Set Python runtime** (optional, creates runtime.txt)
```bash
echo "python-3.12.0" > runtime.txt
```

4. **Deploy**
```bash
git push heroku main
```

5. **Scale the dyno**
```bash
heroku ps:scale web=1
```

6. **Open your app**
```bash
heroku open
```

### Configuration
```bash
# View logs
heroku logs --tail

# Set environment variables (if needed)
heroku config:set VAR_NAME=value
```

## Docker Deployment

### Build and Run Locally

1. **Build the image**
```bash
docker build -t og-ai:latest .
```

2. **Run the container**
```bash
docker run -d -p 5000:5000 --name og-ai og-ai:latest
```

3. **Test**
```bash
curl http://localhost:5000/health
```

4. **View logs**
```bash
docker logs -f og-ai
```

5. **Stop and remove**
```bash
docker stop og-ai
docker rm og-ai
```

### Using Docker Compose

1. **Start the service**
```bash
docker-compose up -d
```

2. **View logs**
```bash
docker-compose logs -f
```

3. **Stop the service**
```bash
docker-compose down
```

### Deploy to Docker Registry

1. **Tag your image**
```bash
docker tag og-ai:latest your-registry/og-ai:latest
```

2. **Push to registry**
```bash
docker push your-registry/og-ai:latest
```

## Cloud Providers

### AWS Elastic Beanstalk

1. Install EB CLI: `pip install awsebcli`
2. Initialize: `eb init -p python-3.12 og-ai`
3. Create environment: `eb create og-ai-env`
4. Deploy: `eb deploy`
5. Open: `eb open`

### Google Cloud Run

1. **Build and push to Google Container Registry**
```bash
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/og-ai
```

2. **Deploy to Cloud Run**
```bash
gcloud run deploy og-ai \
  --image gcr.io/YOUR_PROJECT_ID/og-ai \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated
```

### Azure Container Instances

1. **Login to Azure**
```bash
az login
```

2. **Create resource group**
```bash
az group create --name og-ai-rg --location eastus
```

3. **Create container registry**
```bash
az acr create --resource-group og-ai-rg --name ogairegistry --sku Basic
```

4. **Build and push**
```bash
az acr build --registry ogairegistry --image og-ai:latest .
```

5. **Deploy**
```bash
az container create \
  --resource-group og-ai-rg \
  --name og-ai \
  --image ogairegistry.azurecr.io/og-ai:latest \
  --dns-name-label og-ai-unique \
  --ports 5000
```

### DigitalOcean App Platform

1. Go to [DigitalOcean App Platform](https://cloud.digitalocean.com/apps)
2. Click "Create App"
3. Connect your GitHub repository
4. Configure:
   - **Type**: Web Service
   - **Build Command**: `pip install -r requirements.txt`
   - **Run Command**: `gunicorn --bind 0.0.0.0:$PORT --workers 2 --timeout 120 ai_agent:app`
   - **Port**: 5000
5. Click "Create Resources"

## Environment Variables

The application supports the following environment variables:

| Variable | Description | Default | Required |
|----------|-------------|---------|----------|
| `PORT` | Port to run the server on | `5000` | No |
| `PYTHON_VERSION` | Python version (for some platforms) | `3.12.0` | No |

Additional configuration is managed through `config.json`:
```json
{
  "agent_name": "OG-AI",
  "system_prompt": "You are a helpful AI assistant...",
  "max_history_length": 100,
  "save_conversations": true,
  "conversation_dir": "./conversations"
}
```

## Production Considerations

### Workers
- Single worker (`--workers 1`): Maintains consistent conversation history
- Multiple workers: Each worker has independent state
- For multi-worker setups, implement session routing or shared storage (Redis/Database)

### Scaling
- **Vertical**: Increase instance size for more memory/CPU
- **Horizontal**: Use multiple instances with load balancer + shared state store

### Security
- Enable HTTPS (most platforms do this automatically)
- Implement rate limiting (e.g., Flask-Limiter)
- Add authentication if needed
- Set proper CORS configuration

### Monitoring
- Use platform-specific monitoring tools
- Monitor `/health` endpoint
- Track response times and error rates
- Set up alerts for downtime

### Persistence
- Mount persistent volumes for conversation storage
- Use cloud storage (S3, GCS, Azure Blob) for backups
- Consider database for conversation history at scale

## Troubleshooting

### Port Issues
```bash
# Check if port is in use
lsof -i :5000

# Use different port
PORT=8080 python ai_agent.py
```

### Memory Issues
- Reduce number of workers
- Implement conversation history limits
- Use memory profiling: `pip install memory_profiler`

### Connection Issues
- Check firewall settings
- Verify security group rules (AWS/GCP/Azure)
- Ensure correct port exposure in container

### Logs Not Showing
```bash
# Docker
docker logs container-name

# Heroku
heroku logs --tail

# Cloud platforms
# Check platform-specific logging dashboards
```

## Support

For issues specific to:
- **Application**: Check [GitHub Issues](https://github.com/Goatfighter206/OG-AI-/issues)
- **Render**: [Render Documentation](https://render.com/docs)
- **Heroku**: [Heroku Dev Center](https://devcenter.heroku.com/)
- **Docker**: [Docker Documentation](https://docs.docker.com/)

## Next Steps

After deployment:
1. Test all API endpoints
2. Monitor logs for any errors
3. Set up custom domain (optional)
4. Configure SSL/TLS (if not automatic)
5. Implement monitoring and alerts
6. Set up CI/CD pipeline for automatic deployments
