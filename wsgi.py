"""
WSGI entrypoint for OG-AI Agent
This file provides WSGI compatibility for deployment on traditional WSGI servers.
For ASGI servers (recommended), use app:app directly with uvicorn/hypercorn.
"""

import os
import sys

# Ensure the app directory is in the path
sys.path.insert(0, os.path.dirname(__file__))

# Import the FastAPI app
from app import app

# For ASGI to WSGI bridge (if needed for legacy servers)
# Note: FastAPI is an ASGI application, not WSGI
# For deployment, use: gunicorn app:app -w 2 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT
application = None
try:
    from asgiref.wsgi import WsgiToAsgi
    application = WsgiToAsgi(app)
except ImportError:
    # If asgiref is not available, provide guidance
    print("WARNING: WSGI mode requires asgiref. Install with: pip install asgiref")
    print("For best performance, use an ASGI server instead:")
    print("  uvicorn app:app --host 0.0.0.0 --port 8000")
    print("  gunicorn app:app -w 4 -k uvicorn.workers.UvicornWorker")
    application = None

if __name__ == "__main__":
    # For local testing only
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)

