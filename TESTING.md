# Live Voice WebSocket Test

## Quick Test Command

```bash
cd /home/neety/.openclaw/workspace/gamma-main
python3 live_voice_test.py
```

## Manual Testing

1. Open browser at: `http://localhost:8001/dashboard/live`
2. Click 'Start Voice'
3. Speak into microphone
4. Response appears after ~3-5 seconds

## Automated Test

The test script `live_voice_test.py` verifies:
- WebSocket connection to `/api/voice/live`
- State transitions (ready → listening → completed)
- HTTP API health check
- Qwen-TTS endpoint availability

## Expected Response

When working:
- WebSocket connects successfully
- State changes to: `listening`
- Audio captured during turn
- Response generated with transcript and audio

## Troubleshooting

If websocket fails:
1. Check dashboard is running: `ps aux | grep uvicorn`
2. Verify port 8001 is listening: `ss -tlnp | grep 8001`
3. Ensure `DASHBOARD_URL=http://localhost:8001` in code
4. Restart dashboard if needed

Run test:
```bash
python3 live_voice_test.py
```
