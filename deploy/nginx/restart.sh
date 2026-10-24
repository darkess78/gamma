#!/bin/bash
# Nginx restart helper

echo "Restarting nginx..."
sleep 2
sudo systemctl restart nginx 2>/dev/null && echo "✓ Nginx restarted" || echo "⚠ Nginx restart failed (check sudo access)"
