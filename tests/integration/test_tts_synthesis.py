#!/usr/bin/env python3
"""Qwen-TTS Synthesis Test - Integration test for TTS endpoint."""
import httpx
import sys
from pathlib import Path

TEST_AUDIO = Path("/home/neety/.openclaw/workspace/gamma-main/test_audio/jfk.flac")
URL = "http://127.0.0.1:8000/api/providers/tts/synthesize"

def run_test():
    headers = {"Authorization": "Bearer gamma-lan-token"}
    
    try:
        print("="*70)
        print("Qwen-TTS Synthesis Test")
        print("="*70)
        
        print(f"\nUploading: {TEST_AUDIO}")
        files = {"audio": open(TEST_AUDIO, 'rb')}
        
        response = httpx.post(URL, files=files, headers=headers, timeout=60)
        
        print(f"Status: {response.status_code}")
        
        if response.status_code in [200, 201]:
            output = TEST_AUDIO.replace('.flac', '_qwen_suggested.wav')
            with open(output, 'wb') as f:
                f.write(response.content)
            print(f"Output saved: {output}")
            print(f"Size: {len(response.content)} bytes")
            print("\n✓ TTS works correctly!")
            return 0  # Success
        else:
            print(f"Error: {response.text[:300]}")
            return 1  # API error
            
    except Exception as e:
        print(f"✗ Error: {e}")
        return 1  # Exception

if __name__ == "__main__":
    sys.exit(run_test())

