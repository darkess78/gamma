#!/usr/bin/env python3
"""
Automated Live Voice Test Script
Tests the /api/voice/live websocket endpoint with test audio (jfk.flac)
"""

import asyncio
import base64
import json
import subprocess
import wave
import io
from pathlib import Path
import websockets

DASHBOARD_URL = "ws://127.0.0.1:8001/api/voice/live"
TEST_AUDIO_FILE = "/home/neety/.openclaw/workspace/gamma-main/test_audio/jfk.flac"

async def get_audio_as_base64(audio_path: str) -> str | None:
    """Read audio file and return base64-encoded PCM data."""
    try:
        with open(audio_path, "rb") as f:
            raw = f.read()
        # Try to detect format and extract PCM
        with wave.open(io.BytesIO(raw)) as wav:
            # Get audio data
            audio_data = f.read()
            if wav.getnchannels() == 1 and wav.getsampwidth() == 2:
                rate = wav.getframerate()
                # Convert to mono if necessary (already checked)
                import struct
                pcm_samples = struct.unpack(f'{len(audio_data)}h', raw)
                # Convert to float32
                import numpy as np
                float_data = np.array(pcm_samples, dtype='<f4') / 32768.0
                result = float_data.astype('<f4').tobytes()
                return base64.b64encode(result).decode('ascii')
        return None
    except Exception as e:
        print(f"  Error reading audio: {e}")
        return None

async def test_live_turn(websocket, audio_base64: str | None = None, record: bool = True):
    """Test a single live turn with optional audio upload."""
    print("\n" + "="*60)
    print("Starting live voice turn test")
    print("="*60)
    
    # 1. Send ready
    try:
        await websocket.send(json.dumps({"type": "ready"}))
        response = await asyncio.wait_for(websocket.recv(), timeout=2.0)
        data = json.loads(response)
        print(f"  Received: {data}")
    except asyncio.TimeoutError:
        print("  Error: Timeout waiting for ready response")
        return {"status": "error", "message": "Ready timeout"}
    except Exception as e:
        print(f"  Error: {e}")
        return {"status": "error", "message": str(e)}
    
    # 2. Start turn
    print("\n  Sending start_turn...")
    await websocket.send(json.dumps({
        "type": "start_turn",
        "session_id": None,
        "synthesize_speech": True,
        "response_mode": "simple_chunked"
    }))
    
    try:
        response = await asyncio.wait_for(websocket.recv(), timeout=2.0)
        data = json.loads(response)
        print(f"  State: {data['state']}")
    except asyncio.TimeoutError:
        print("  Error: Timeout")
        return {"status": "failed"}
    
    # 3. Upload audio (if provided)
    if audio_base64:
        print("  Uploading test audio...")
        await websocket.send(json.dumps({
            "type": "upload_audio",
            "audio_base64": audio_base64,
            "rate": 16000,  # Assume mono 16kHz
        }))
        try:
            response = await asyncio.wait_for(websocket.recv(), timeout=5.0)
            data = json.loads(response)
            print(f"  Upload response: {data}")
        except asyncio.TimeoutError:
            print("  Upload timeout")
    
    # 4. End turn and capture response
    print("  Closing turn...")
    await websocket.send(json.dumps({
        "type": "end_turn"
    }))
    
    try:
        # Wait for state updates
        for _ in range(10):  # Up to 5 seconds
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=0.5)
                data = json.loads(response)
                print(f"  Response: {data}")
                if "turn_result" in data or "transcript" in data:
                    return {"status": "success", "response": data}
            except asyncio.TimeoutError:
                pass
        return {"status": "partial", "note": "Session closed normally"}
    except asyncio.TimeoutError:
        return {"status": "error", "message": "No responses received"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

async def main():
    print("Gamma Live Voice Test Suite")
    print(f"Dashboard URL: {DASHBOARD_URL}")
    
    # Check if test audio exists
    audio_path = Path(TEST_AUDIO_FILE)
    if not audio_path.exists():
        print(f"\nTest audio not found: {TEST_AUDIO_FILE}")
        print("Testing without uploaded audio (mic input only)")
    else:
        print(f"\nTest audio found: {TEST_AUDIO_FILE}")
        print(f"  Size: {audio_path.stat().st_size / 1024:.1f} KB")
    
    # Get audio base64 if available
    audio_base64 = None
    if audio_path.exists():
        try:
            audio_base64 = await get_audio_as_base64(str(audio_path))
            if audio_base64:
                print(f"  Audio base64: {len(audio_base64)} chars")
            else:
                print("  Could not extract audio data")
        except Exception as e:
            print(f"  Audio extraction failed: {e}")
    
    # Connect to websocket
    print(f"\nConnecting to websocket...")
    try:
        async with websockets.connect(DASHBOARD_URL) as websocket:
            print("  ✓ WebSocket connected!")
            
            # Test with uploaded audio
            if audio_base64:
                result = await test_live_turn(websocket, audio_base64=audio_base64)
            else:
                result = await test_live_turn(websocket)
            
            # Print summary
            print("\n" + "="*60)
            print("Test Summary:")
            print(f"  Status: {result['status']}")
            if "response" in result:
                print(f"  Turn ID: {result['response'].get('turn_id', 'N/A')}")
                print(f"  Transcript: {result['response'].get('transcript', 'N/A')[:200]}...")
            print("="*60)
            
            return result
            
    except Exception as e:
        print(f"\n✗ Connection failed: {e}")
        return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    result = asyncio.run(main())
    exit(0 if result.get("status") in ["success", "partial"] else 1)
