# Live Voice Status - Gamma Dashboard

## Configuration Summary

Environment variables (`.env`):
```
SHANA_DASHBOARD_PUBLIC_HOST=gamma.neety.me
SHANA_DASHBOARD_PUBLIC_PORT=8001
SHANA_DASHBOARD_PUBLIC_SCHEME=https
SHANA_DASHBOARD_COOKIE_SECURE=true
SHANA_PUBLIC_HOST=gamma.neety.me
SHANA_PUBLIC_SCHEME=https
```

## URLs

For local testing:
- Dashboard: `http://localhost:8001/dashboard/live`
- WebSocket: `ws://127.0.0.1:8001/api/voice/live`
- Health: `http://localhost:8001/api/status/runtime`

For production:
- Dashboard: `https://gamma.neety.me:8001/dashboard/live` or `https://gamma.neety.me/dashboard`
- WebSocket: `wss://gamma.neety.me:8001/api/voice/live`

Note: For production deployment, nginx/proxy must use port 443 (HTTPS)

## To Run Tests

```bash
cd /home/neety/.openclaw/workspace/gamma-main
python3 live_voice_test.py
```

## Issues Fixed

1. ✓ WebSocket endpoint: `/api/voice/live` at port 8001
2. ✓ Dashboard binds to 0.0.0.0:8001
3. ✓ Public hostname: gamma.neety.me
4. ✓ Fixed syntax error in main.py
5. ✓ Qwen-TTS configured for audio synthesis

## Running Live Voice

1. Open browser at: `http://localhost:8001/dashboard/live`
2. Click 'Start Voice'
3. Speak into microphone
4. Listen to response after ~3-5 seconds

## Automation Testing

Run automated tests:
```bash
python3 live_voice_test.py
```

This tests:
- WebSocket connection
- HTTP API endpoints
- State transitions
- TTS synthesis (optional)
