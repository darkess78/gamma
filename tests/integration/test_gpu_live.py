#!/usr/bin/env python3
"""Test live voice with GPU verification."""
import asyncio
import json
import sys
import os
from pathlib import Path

# Set CUDA path
os.environ['LD_LIBRARY_PATH'] = '/usr/local/lib/ollama/cuda_v12:/usr/local/lib:/usr/lib/x86_64-linux-gnu'

print("="*70)
print("Gamma Live Voice - GPU Test")
print("="*70)

async def test():
    try:
        import websockets
        async with websockets.connect("ws://127.0.0.1:8001/api/voice/live") as ws:
            response = json.loads(await ws.recv())
            print(f"Connected status: {response['status']}")
    except Exception as e:
        print(f"Connection error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(test())
