# Root entry point for FastAPI Cloud and local development
# This imports the actual app from backend/main.py
import importlib.util
import os
import sys

# Load backend/main.py as a module
backend_main_path = os.path.join(os.path.dirname(__file__), 'backend', 'main.py')
spec = importlib.util.spec_from_file_location("backend_main", backend_main_path)
backend_main = importlib.util.module_from_spec(spec)
sys.modules['backend_main'] = backend_main
spec.loader.exec_module(backend_main)

# Export the app
app = backend_main.app

__all__ = ['app']
