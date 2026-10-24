#!/bin/bash
# Gamma server startup script with GPU support

# CUDA library paths
CUDA_PATH="/usr/local/lib/ollama/cuda_v12"
LD_LIB_PATH="/usr/local/lib/ollama/cuda_v12:/usr/local/lib:/usr/lib/x86_64-linux-gnu"

echo "Starting Gamma with GPU support (RTX 3090)"
echo "LD_LIBRARY_PATH: $LD_LIB_PATH"

# Start Qwen-TTS with CUDA
(
    export LD_LIBRARY_PATH="$LD_LIB_PATH"
    nohup .venv/bin/python -m uvicorn gamma.main:app --host 0.0.0.0 --port 8000 >> /tmp/shana.log 2>&1 &
    echo "Qwen-TTS started with GPU"
)

# Start dashboard
(
    export LD_LIBRARY_PATH="$LD_LIB_PATH"
    nohup .venv/bin/python -m uvicorn gamma.dashboard.main:app --host 0.0.0.0 --port 8001 --env-file .env >> /tmp/dashboard.log 2>&1 &
    echo "Dashboard started with GPU"
)

sleep 3
echo "All servers started"
echo "Check status with: ps aux | grep uvicorn"
