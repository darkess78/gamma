#!/bin/bash
echo "Starting Gamma with GPU (RTX 3090 for Qwen-TTS)..."

set -e

# Start Qwen-TTS
export LD_LIBRARY_PATH="/usr/local/lib/ollama/cuda_v12:/usr/local/lib:/usr/lib/x86_64-linux-gnu"
export PYTHONPATH="/home/neety/.openclaw/workspace/gamma-main:$PYTHONPATH"

echo "1. Starting Qwen-TTS on port 9882.>"
.venv/bin/python scripts/qwen_tts_server.py > data/logs/qwen_tts.log 2>&1 &
QWEN_PID=$!
echo "   Qwen-TTS PID: $QWEN_PID"

# Start Shana API
echo "2. Starting Shana API on port 8000.>"
.venv/bin/python -m uvicorn gamma.main:app --host 0.0.0.0 --port 8000 > data/logs/shana.log 2>&1 &
SHANA_PID=$!
echo "   Shana API PID: $SHANA_PID"

# Start Dashboard
echo "3. Starting Dashboard on port 8001.>"
.venv/bin/python -m uvicorn gamma.dashboard.main:app --host 0.0.0.0 --port 8001 --env-file .env > data/logs/dashboard.log 2>&1 &
DASH_PID=$!
echo "   Dashboard PID: $DASH_PID"

# Wait for services
echo ""
echo "Waiting for services to initialize... (may take 60s for Qwen-TTS)"
sleep 60

echo ""
echo "4. Checking service status.>"
if curl -s http://127.0.0.1:9882/health | grep -q '"ok"'; then
    echo "   ✅ Qwen-TTS: Running"
else
    echo "   ❌ Qwen-TTS: Not ready"
fi

if curl -s http://127.0.0.1:8000/health | grep -q '"ok"'; then
    echo "   ✅ Shana API: Running"
else
    echo "   ❌ Shana API: Not ready"
fi

if curl -s http://127.0.0.1:8001/health | grep -q '"ok"'; then
    echo "   ✅ Dashboard: Running"
else
    echo "   ❌ Dashboard: Not ready"
fi

echo ""
echo "5. Live Voice URL:"
echo "   Local:    http://localhost:8001/dashboard/live"
echo "   Production: https://gamma.neety.me/dashboard/live"
echo ""
echo "✅ All services running!"

echo ""
echo "To stop all services:"
echo "   pkill -f 'qwen_tts_server\|uvicorn.*gamma'"
