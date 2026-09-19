import sys
from pathlib import Path

# Ensure repository root is on sys.path so 'backend' imports work cleanly
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.main import app

# Export ASGI app for Vercel Serverless Function detection
__all__ = ["app"]
