#!/usr/bin/env python3
"""
Live Voice WebSocket Connection Test
Tests basic websocket functionality states
"""

import asyncio
import json
import websockets

DASHBOARD_URL = "ws://127.0.0.1:8001/api/voice/live"

async def test_connection():
    """Test basic websocket connection and state transitions."""
    print("="*70)
    print("Gamma Live Voice WebSocket Connection Test")
    print("="*70)
    print(f"\nDashboard URL: {DASHBOARD_URL}")
    
    # Connect
    try:
        async with websockets.connect(DASHBOARD_URL) as websocket:
            print("\n✓ WebSocket connected!")
            
            # Get ready
            await websocket.send(json.dumps({"type": "ready"}))
            try:
                response = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                data = json.loads(response)
                print(f"  ✓ Ready: {data}")
            except Exception as e:
                print(f"  ✗ Ready failed: {e}")
                return
            
            # Start turn
            print("\n--- Starting turn ---")
            await websocket.send(json.dumps({
                "type": "start_turn",
                "session_id": None,
                "synthesize_speech": True,
                "response_mode": "simple_chunked"
            }))
            
            response = await asyncio.wait_for(websocket.recv(), timeout=2.0)
            data = json.loads(response)
            print(f"  ✓ State changed to: {data['state']}")
            
            # End turn
            print("\n--- Ending turn ---")
            await websocket.send(json.dumps({"type": "end_turn"}))
            
            # Wait for responses
            responses = []
            for _ in range(10):
                try:
                    response = await asyncio.wait_for(websocket.recv(), timeout=0.3)
                    data = json.loads(response)
                    if data.get('state') == 'idle':
                        print(f"  ✗ Turn ended without processing (empty buffer): {data}")
                        break
                    responses.append(data)
                    print(f"  Response: {data['type'] if 'type' in data else ''}")
                except asyncio.TimeoutError:
                    pass
            
            print(f"\nTotal responses received: {len(responses)}")
            print("\nResponses received:")
            for r in responses:
                t = r.get('type', 'N/A')
                s = r.get('state', r.get('detail', ''))[:60]
                print(f"  - {t}: {s}")
            
            # Test cancel
            print("\n--- Testing cancel_turn ---")
            await websocket.send(json.dumps({"type": "cancel_turn"}))
            for _ in range(5):
                try:
                    response = await asyncio.wait_for(websocket.recv(), timeout=0.3)
                    print(f"  Cancel response: {json.loads(response)}")
                except asyncio.TimeoutError:
                    print("  Cancel command timeout (normal)")
            
            print("\n" + "="*70)
            print("✓ WebSocket test completed successfully!")
            print("="*70)
            return "success"
            
    except Exception as e:
        print(f"\n✗ Connection failed: {e}")
        return "error"

if __name__ == "__main__":
    result = asyncio.run(test_connection())
    exit(0 if result == "success" else 1)
