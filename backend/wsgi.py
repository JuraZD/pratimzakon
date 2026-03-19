import os
import sys

# Ensure app module can be imported
sys.path.insert(0, os.path.dirname(__file__))

# Import the app
from app.main import app

# For WSGI servers
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
