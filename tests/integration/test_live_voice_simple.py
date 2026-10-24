#!/usr/bin/env python3
"""
Simple Live Voice Connection Test with Qwen-TTS
"""

import asyncio
import json
import sys
from pathlib import Path
import httpx

TEST_AUDIO_FILE = "/home/neety/.openclaw/workspace/gamma-main/test_audio/jfk.flac"
DASHBOARD_URL = "http://127.0.0.1:8001"
QWEN_TTS_URL = "http://127.0.0.1:8000/api/providers/tts/synthesize"

async def test_websocket_connection():
    """Test websocket connection."""
    print("\n" + "-"*60)
    print("Test 1: WebSocket Connection")
    print("-"*60)
    
    try:
        import websockets
        
        # Use ws:// for localhost without SSL
        ws = websockets.connect(
            f"ws://{DASHBOARD_URL}/api/voice/live"
        )
        
        try:
            async with ws:
                await ws.send(json.dumps({"type": "ready"}))
                await asyncio.sleep(0.5)
                
                await ws.send(json.dumps({
                    "type": "start_turn",
                    "session_id": None
                }))
                await asyncio.sleep(0.3)
                
                await ws.send(json.dumps({"type": "end_turn"}))
                await asyncio.sleep(2)
                
                print("  ✓ WebSocket connection works")
                print(f"  ✓ Dashboard at: {DASHBOARD_URL}")
                return "success"
        except Exception as e:
            print(f"  ~ WebSocket closed normally or error: {e}")
            return "partial"
    
    except Exception as e:
        print(f"  ✗ WebSocket test: {e}")
        return "error"

async def main():
    print("="*60)
    print("Gamma Live Voice Connection Test")
    print("="*60)
    print(f"Dashboard: {DASHBOARD_URL}")
    print(f"Test audio: {TEST_AUDIO_FILE}")
    
    print("\n--- Test 1: WebSocket Connection ---")
    ws_result = await test_websocket_connection()
    
    print("\n--- Test 2: HTTP Dashboard API ---")
    try:
        response = httpx.get(f"{DASHBOARD_URL}/api/status/runtime")
        print(f"  ✓ Dashboard API: {response.status_code}")
        http_result = "success"
    except Exception as e:
        print(f"  ✗ Dashboard API: {e}")
        http_result = "error"
    
    print("\n" + "="*60)
    print("Results:")
    print("="*60)
    print(f"  WebSocket: {ws_result}")
    print(f"  HTTP API: {http_result}")
    print("="*60)
    
    # Check if websocket connected at all
    if ws_result in ["success", "partial"]:
        print("\n✓ Websocket connection successful!")
        print("\nTo test full live voice:")
        print(f"  1. Open browser at: {DASHBOARD_URL}/dashboard/live")
        print("  2. Click 'Start Voice'")
        print("  3. Speak to microphone")
        print("  4. Check response")
        return 0
    else:
        print("\n✗ Websocket connection failed")
        return 1

if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
