#!/bin/bash
# BuildingOS Backend — double-click to start
cd "$(dirname "$0")"

echo ""
echo "================================="
echo "  BuildingOS AI Backend"
echo "================================="
echo ""

# Check Python
if ! command -v python3 &>/dev/null; then
  echo "ERROR: Python 3 is not installed."
  echo "Download it from https://python.org"
  read -p "Press Enter to exit..."
  exit 1
fi

# Kill any existing server on port 8000
if lsof -i :8000 -sTCP:LISTEN &>/dev/null; then
  echo "Stopping existing server on port 8000..."
  lsof -ti :8000 | xargs kill -9 2>/dev/null
  sleep 1
fi

# Force upgrade anthropic + httpx to fix the 'proxies' compatibility error
echo "Upgrading anthropic SDK (required for compatibility)..."
python3 -m pip install "anthropic>=0.40.0" "httpx>=0.27.0" --upgrade --quiet 2>&1 | tail -3

echo "Installing remaining dependencies..."
python3 -m pip install fastapi "uvicorn[standard]" python-multipart python-dotenv --upgrade --quiet 2>&1 | tail -2

echo "Installing cloud integration libraries (Google Drive, OneDrive)..."
python3 -m pip install "google-auth-oauthlib>=1.2.0" "google-api-python-client>=2.130.0" "msal>=1.28.0" --upgrade --quiet 2>&1 | tail -2

echo ""

# Verify anthropic version
ANTHRO_VER=$(python3 -c "import anthropic; print(anthropic.__version__)" 2>/dev/null)
if [ -z "$ANTHRO_VER" ]; then
  echo "ERROR: anthropic package failed to install."
  read -p "Press Enter to exit..."
  exit 1
fi
echo "anthropic version: $ANTHRO_VER"

echo ""
echo "Starting server on http://localhost:8000"
echo "Opening browser in 3 seconds..."
echo "Keep this window open while using BuildingOS."
echo "Press Ctrl+C to stop."
echo ""

# Open browser after a short delay
(sleep 3 && open http://localhost:8000) &

python3 -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
