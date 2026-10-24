#!/bin/bash
cd "$(dirname "$0")"
export LD_LIBRARY_PATH="/usr/local/lib/ollama/cuda_v12:/usr/local/lib:/usr/lib/x86_64-linux-gnu"

nohup .venv/bin/python scripts/qwen_tts_server.py --device cuda:1 --port 9882 > data/logs/qwen_tts.out.log 2>&1 &
echo "Qwen-TTS server starting..."
sleep 10
echo "Checking if server is ready..."

# Check health
if curl -s -m 2 http://127.0.0.1:9882/health > /dev/null; then
    echo "✓ Qwen-TTS server ready on port 9882"
    ps aux | grep 9882
else
    echo "✗ Server not ready, checking logs..."
    tail -20 data/logs/qwen_tts.out.log 2>/dev/null || tail -20 data/logs/qwen_tts.err.log 2>/dev/null
fi
