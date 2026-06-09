#!/usr/bin/env python3
"""
Live Voice Test with Uploaded Audio (Roundtrip API)
Tests the full TTS pipeline with jfk.flac as test audio
"""

import asyncio
import base64
import json
import subprocess
from pathlib import Path

DASHBOARD_HTTP_URL = "http://127.0.0.1:8001"
TEST_AUDIO_FILE = "/home/neety/.openclaw/workspace/gamma-main/test_audio/jfk.flac"

async def test_with_roundtrip():
    """Test using roundtrip API with uploaded audio."""
    print("="*70)
    print("Gamma Live Voice Roundtrip Test with jfk.flac")
    print("="*70)
    
    audio_path = Path(TEST_AUDIO_FILE)
    if not audio_path.exists():
        print(f"\n✗ Test audio not found: {TEST_AUDIO_FILE}")
        return "not_found"
    
    audio_size = audio_path.stat().st_size
    print(f"\nTest audio: {TEST_AUDIO_FILE}")
    print(f"  Size: {audio_size / 1024:.1f} KB")
    
    # Read audio file
    try:
        with open(TEST_AUDIO_FILE, "rb") as f:
            audio_bytes = f.read()
        audio_base64 = base64.b64encode(audio_bytes).decode('ascii')
        print(f"  ✓ Audio loaded ({len(audio_base64)} chars)")
    except Exception as e:
        print(f"  ✗ Failed to load audio: {e}")
        return "error"
    
    # Test roundtrip API
    print("\n--- Testing roundtrip API ---")
    
    # Upload audio
    try:
        upload_url = f"{DASHBOARD_HTTP_URL}/api/voice/live"
        headers = {"Authorization": "Bearer gamma-lan-token"}
        
        async with websockets.connect("wss://" + upload_url, ssl=False) as ws:
            await ws.send(json.dumps({"type": "ready"}))
            try:
                resp = await asyncio.wait_for(ws.recv(), timeout=2)
                print(f"  Ready: {resp}")
            except:
                pass
            
            # Simulate receiving audio
            print("  Uploading audio...")
            await ws.send(json.dumps({
                "type": "upload_audio",
                "audio_base64": audio_base64,
                "rate": 16000
            }))
            
            # Wait for processing
            print("  Processing audio...")
            turn_id = None
            for _ in range(15):  # Wait up to 8 seconds
                try:
                    resp = await asyncio.wait_for(ws.recv(), timeout=0.3)
                    data = json.loads(resp)
                    print(f"  State: {data.get('state', 'N/A')}")
                    
                    if data.get('type') == 'turn_result' and turn_id is None:
                        transcript = data.get('transcript', '')[:200]
                        print(f"  ✓ Turn result! Transcript: {transcript}...")
                        return "success"
                    
                    if data.get('status') in ['completed', 'failed']:
                        if turn_id is None and data.get('type') == 'turn_result':
                            transcript = data.get('transcript', '')[:200]
                            print(f"  ✓ Turn result: {transcript}...")
                            return "success"
                except asyncio.TimeoutError:
                    pass
            
            print("  No complete turn result received")
            return "partial"
            
    except Exception as e:
        print(f"  ✗ Roundtrip test failed: {e}")
        return "error"

def test_with_tts():
    """Test TTS directly using the backend."""
    print("\n\n--- Testing TTS directly ---")
    
    # Use the GPT-SoVITS API directly
    tts_url = "http://127.0.0.1:9882/tts"
    
    try:
        # Test TTS with a simple prompt
        payload = {
            "text": "The President John F. Kennedy",
            "text_lang": "en",
            "ref_audio_path": TEST_AUDIO_FILE,
            "prompt_lang": "en",
            "prompt_text": "The President John F. Kennedy",
            "top_k": 15,
            "top_p": 1,
            "temperature": 1,
            "text_split_method": "cut5",
            "batch_size": 1,
            "batch_threshold": 0.75,
            "split_bucket": True,
            "speed_factor": 1.0,
            "fragment_interval": 0.3,
            "seed": -1,
            "media_type": "wav",
            "streaming_mode": 3,
            "parallel_infer": True,
            "repetition_penalty": 1.35,
            "sample_steps": 32,
            "super_sampling": False,
            "overlap_length": 2,
            "min_chunk_length": 16
        }
        
        import requests
        response = requests.post(tts_url, json=payload, stream=True)
        
        if response.status_code == 200:
            # Download and save audio
            output_path = TEST_AUDIO_FILE.replace('.flac', '_synthesized.wav')
            with open(output_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            print(f"  ✓ TTS generated: {output_path}")
            print(f"    Size: {Path(output_path).stat().st_size / 1024:.1f} KB")
            return "success"
        else:
            print(f"  ✗ TTS failed: {response.status_code}")
            return "error"
            
    except Exception as e:
        print(f"  ✗ TTS request failed: {e}")
        return "error"

async def main():
    print(f"\nDashboard: {DASHBOARD_HTTP_URL}")
    print(f"Test audio: {TEST_AUDIO_FILE}")
    
    # Test roundtrip
    roundtrip_result = await test_with_roundtrip()
    
    # Test TTS directly
    tts_result = test_with_tts()
    
    # Summary
    print("\n" + "="*70)
    print("Test Summary")
    print("="*70)
    print(f"  Roundtrip test: {roundtrip_result}")
    print(f"  TTS test: {tts_result}")
    print("="*70)
    
    return roundtrip_result, tts_result

if __name__ == "__main__":
    import websockets
    import sys
    try:
        result = asyncio.run(main())
        exit(0 if result != "error" else 1)
    except KeyboardInterrupt:
        print("\nInterrupted")
        sys.exit(0)
    except Exception as e:
        print(f"\nFailed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
