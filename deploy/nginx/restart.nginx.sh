#!/bin/bash
sudo systemctl restart nginx && echo "✓ Restarted" || echo "⚠ Restart failed"
