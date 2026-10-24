#!/bin/bash
# Gamma Startup with GPU Isolation
# - RTX 3090 (GPU 2000): Main LLM for Shana (Ollama)
# - RTX 3060 Ti (GPU 1): TTS/STT for faster response times

set -e

cd "$(dirname "$0")"

echo "============== GPU ISOLATED STARTUP ==============="
echo ""
echo "GPU Assignment:"
echo "  GPU 0: RTX 3090 (main LLM via Ollama)"
echo "  GPU 1: RTX 3060 Ti (TTS/STT models)"
echo ""

# Function to start a service
start_service() {
    SERVICE_NAME=$1
    CUDA_DEVICE=$2
    PYSCRIPT=$3
    
    env CUDA_VISIBLE_DEVICES="$CUDA_DEVICE" "$PYTHON_BIN" "$PYSCRIPT" > "$LOG_DIR/${SERVICE_NAME%.py}.log" 2>&1 &
    echo "  Started $SERVICE_NAME on GPU $CUDA_DEVICE (PID: $!)"
}

echo "Loading CUDA libraries..."
export LD_LIBRARY_PATH="/usr/local/lib/ollama/cuda_v12:/usr/local/lib:/usr/lib/x86_64-linux-gnu"

# TTS on GPU 1 (3060 Ti)
start_service "Qwen-TTS" "1" "scripts/qwen_tts_server.py"

# STT on GPU 1 (3060 Ti) if needed
if [ -n "$SHANA_STT_DEVICE" ] && echo "$SHANA_STT_DEVICE" | grep -q cuda; then
    start_service "Shana STT" "1" "python3 -c 'import sys; sys.path.insert(0,\".\"); from gamma.stt import create_stt; print(\"STT initialized on GPU\")'"
fi

# Dashboard on GPU 0 (or let PyTorch decide)
start_service "Dashboard" "0" "gamma/dashboard/main.py"

# Wait for TTS to load
echo "Waiting for Qwen-TTS to load (model loading may take ~60s)..."
sleep 60

echo ""
echo "Checking service status.>"
for service in Qwen-TTS Shana Dashboard; do
    if [ "$service" = "Qwen-TTS" ]; then
        curl -s http://127.0.0.1:9882/health
    elif [ "$service" = "Shana" ]; then
        curl -s http://127.0.0.1:8000/health
    elif [ "$service" = "Dashboard" ]; then
        curl -s http://127.0.0.1:8001/health
    fi
    echo ""
done

ps aux | grep -E "qwen_tts|uvicorn.*gamma" | grep -v grep

echo ""
