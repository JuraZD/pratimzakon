#!/usr/bin/env python3
import os
import sys

# Get the absolute path of the backend directory
backend_dir = os.path.abspath(os.path.dirname(__file__))

# Add backend to path
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# Now import
from app.main import app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
