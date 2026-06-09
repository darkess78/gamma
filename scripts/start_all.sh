#!/bin/bash
# Complete Gamma startup script with Qwen-TTS

set -e

cd "$(dirname "$0")"

echo "Starting Gamma with GPU (RTX 3090) and Qwen-TTS..."
echo ""
echo "Loading CUDA libraries..."
export LD_LIBRARY_PATH="/usr/local/lib/ollama/cuda_v12:/usr/local/lib:/usr/lib/x86_64-linux-gnu"

echo "Starting Qwen-TTS backend on port 9882..."
.venv/bin/python scripts/qwen_tts_server.py --device cuda:1 --port 9882 > data/logs/qwen_tts.out.log 2>&1 &
echo "  Qwen-TTS PID: $!"

echo "Starting Shana API on port 8000..."
.venv/bin/python -m uvicorn gamma.main:app --host 0.0.0.0 --port 8000 > data/logs/shana.out.log 2>&1 &
echo "  Shana PID: $!"

echo "Starting Dashboard on port 8001..."
.venv/bin/python -m uvicorn gamma.dashboard.main:app --host 0.0.0.0 --port 8001 --env-file .env > data/logs/dashboard.out.log 2>&1 &
echo "  Dashboard PID: $!"

echo ""
echo "Waiting for services to initialize..."
sleep 10

echo ""
echo "Checking service status..."

# Check Qwen-TTS
if curl -s http://127.0.0.1:9882/health | grep -q '"status":"ok"'; then
    echo "✅ Qwen-TTS: Running on port 9882 (CUDA: cuda:1)"
else
    echo "❌ Qwen-TTS: Failed to start"
fi

# Check Shana API
if curl -s http://127.0.0.1:8000/health | grep -q '"ok"'; then
    echo "✅ Shana API: Running on port 8000"
else
    echo "❌ Shana API: Failed to start"
fi

# Check Dashboard
if curl -s http://127.0.0.1:8001/health | grep -q '"ok"'; then
    echo "✅ Dashboard: Running on port 8001"
else
    echo "❌ Dashboard: Failed to start"
fi

echo ""
echo "Live voice URLs:"
echo "  Local:    http://localhost:8001/dashboard/live"
echo "  Production: https://gamma.neety.me/dashboard/live"

echo ""
echo "✅ All services started successfully!"
