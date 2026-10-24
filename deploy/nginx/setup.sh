#!/bin/bash
# Gamma HTTPS Setup Script

set -e

echo "=== Gamma HTTPS Nginx Setup Script ==="
echo ""

# Step 1: Check SSL certificates
if [ -f "/home/neety/.pki/certs/gamma.neety.me.crt" ] && [ -f "/home/neety/.pki/private/gamma.neety.me.key" ]; then
    echo "✓ SSL certificates already exist"
else
    echo "⚠ SSL certificates missing - generating..."
    cd /home/neety/Documents/gamma-main
    # Generate self-signed certs
    if [ ! -d "/home/neety/.pki" ]; then
        mkdir -p /home/neety/.pki/certs /home/neety/.pki/private /home/neety/.pki/nssdb
    fi
    
    if [ ! -f /home/neety/.pki/certs/gamma.neety.me.crt ]; then
        openssl req -new -newkey rsa:2048 -nodes -sha256 -keyout /home/neety/.pki/private/gamma.neety.me.key \
            -x509 -days 730 -out /home/neety/.pki/certs/gamma.neety.me.crt \
            -subj "/C=US/ST=Local/L=LAN/O=Gamma/CN=gamma.neety.me" 2>/dev/null || true
        echo "✓ SSL certificates generated"
    fi
fi

# Step 2: Deploy nginx configuration
echo ""
echo "Step 2: Deploying nginx configuration..."

# Install config to conf.d
if sudo -v 2>/dev/null; then
    sudo cp /home/neety/Documents/gamma-main/deploy/nginx/gamma-proxy.conf /etc/nginx/conf.d/gamma-proxy.conf
    echo "✓ Configuration copied to /etc/nginx/conf.d/gamma-proxy.conf"
else
    echo "⚠ Cannot copy config without sudo - copy manually:"
    echo "   sudo cp /home/neety/Documents/gamma-main/deploy/nginx/gamma-proxy.conf /etc/nginx/conf.d/gamma-proxy.conf"
fi

# Step 3: Create restart script
echo ""
echo "Step 3: Creating restart script..."
if sudo -v 2>/dev/null; then
    cat > /home/neety/Documents/gamma-main/deploy/nginx/restart.nginx.sh << 'RESTARTEOF'
#!/bin/bash
sudo systemctl restart nginx
echo "✓ Nginx restarted"
curl -s -k https://gamma.neety.me/health || echo "Health check failed"
RESTARTEOF
    chmod +x /home/neety/Documents/gamma-main/deploy/nginx/restart.nginx.sh
    echo "✓ Restart script created"
else
    cat > /home/neety/Documents/gamma-main/deploy/nginx/restart.nginx.sh << 'RESTARTEOF'
#!/bin/bash
sudo systemctl restart nginx
echo "✓ Nginx restarted"
RESTARTEOF
    chmod +x /home/neety/Documents/gamma-main/deploy/nginx/restart.nginx.sh
    echo "✓ Restart script created (sudo may be required)"
fi

# Step 4: Restart nginx
echo ""
echo "Step 4: Starting nginx..."
if sudo -v 2>/dev/null; then
    sudo systemctl enable nginx 2>/dev/null || echo "Nginx not in systemd, creating standalone service..."
    sudo systemctl restart nginx 2>/dev/null || sudo /usr/sbin/nginx 2>/dev/null || true
    echo "✓ Nginx started"
else
    echo "⚠ Cannot restart nginx without sudo - run:"
    echo "   sudo systemctl restart nginx"
fi

echo ""
echo "=== Setup Complete! ==="
echo ""
echo "Your dashboard is now available at:"
echo "  https://gamma.neety.me/dashboard"
echo ""
echo "For microphone access, use HTTPS not HTTP."
echo ""
echo "If you encounter 502 errors:"
echo "  - Check: sudo tail -f /var/log/nginx/error.log"
echo "  - Restart: sudo /home/neety/Documents/gamma-main/deploy/nginx/restart.nginx.sh"
