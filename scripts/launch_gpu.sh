#!/bin/bash
# GPU-enabled launch script for Gamma

# Set CUDA library path before starting Python
export LD_LIBRARY_PATH="/usr/local/lib/ollama/cuda_v12:${LD_LIBRARY_PATH:-}"
export CUDA_HOME="/usr/local/lib/ollama/cuda_v12"

# Also ensure CUDA paths are loaded
export LD_LIBRARY_PATH="/usr/local/lib/ollama/cuda_v12:/usr/local/lib:/usr/lib:x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"

# Start server with GPU
.venv/bin/python -m uvicorn "$@"
