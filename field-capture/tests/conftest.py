"""Pytest configuration for BuildingOS Field Capture tests."""
import sys
from pathlib import Path

# Add the app directory to the path so we can import from app.main
sys.path.insert(0, str(Path(__file__).parent.parent))
