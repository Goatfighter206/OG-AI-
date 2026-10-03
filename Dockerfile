# Use Python 3.12 slim image for smaller size
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

# Copy requirements first for better caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files (app.py plus every module it imports and serves)
COPY app.py ai_agent.py ai_agent_enhanced.py llm_code_generator.py ./
COPY self_learning.py voice_module.py config.json ./
COPY index_epic.html frontend.html qr.html ./
COPY static ./static

# Create directory for conversations (if needed)
RUN mkdir -p /app/conversations

# Expose port
EXPOSE $PORT

# Health check using Python (curl is not available in the slim image)
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://localhost:%s/health' % os.environ.get('PORT', '8000'))" || exit 1

# Run the FastAPI app with gunicorn + uvicorn workers (same command as the Procfile)
CMD ["sh", "-c", "gunicorn app:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT --timeout 120 --access-logfile - --error-logfile -"]
