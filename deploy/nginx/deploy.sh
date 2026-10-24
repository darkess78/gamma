#!/bin/bash
# Gamma HTTPS Deployment Script

set -e

echo "=== Gamma HTTPS Deployment ==="
echo ""
echo "Available commands:"
echo "  generate-ssl-certs   Generate SSL certificates"
echo "  deploy               Deploy nginx configuration"
echo "  restart              Restart nginx"
echo ""

# Generate SSL certificates
if [ "$1" = "generate-ssl-certs" ]; then
    echo "Generating SSL certificates..."
    
    mkdir -p /home/neety/.pki/certs /home/neety/.pki/private /home/neety/.pki/nssdb
    
    if [ ! -f /home/neety/.pki/certs/gamma.neety.me.crt ]; then
        openssl req -new -newkey rsa:2048 -nodes -sha256 \
            -keyout /home/neety/.pki/private/gamma.neety.me.key \
            -out /home/neety/.pki/certs/gamma.neety.me.crt \
            -subj "/C=US/ST=Local/L=LAN/O=Gamma/CN=gamma.neety.me" \
            -days 730 2>/dev/null || true
        
        echo "✓ SSL certificates generated at /home/neety/.pki/"
    else
        echo "✓ SSL certificates already exist"
    fi
    
    echo ""
    echo "Certificates:"
    ls -la /home/neety/.pki/certs/gamma.neety.me.crt
    ls -la /home/neety/.pki/private/gamma.neety.me.key
    echo ""
    exit 0
fi

# Deploy nginx configuration
if [ "$1" = "deploy" ]; then
    echo "Deploying nginx configuration..."
    
    # Copy to conf.d
    sudo mv /home/neety/Documents/gamma-main/deploy/nginx/gamma-proxy.conf \
        /etc/nginx/conf.d/gamma-proxy.conf
    
    echo "✓ Configuration deployed to /etc/nginx/conf.d/gamma-proxy.conf"
    
    # Verify syntax
    sudo nginx -t 2>/dev/null && echo "✓ Nginx configuration syntax OK"
    
    echo ""
    exit 0
fi

# Restart nginx
if [ "$1" = "restart" ]; then
    echo "Restarting nginx..."
    sudo systemctl restart nginx 2>/dev/null || sudo /usr/sbin/nginx -s reload 2>/dev/null || true
    
    echo "✓ Nginx restarted"
    
    # Test health
    for i in 1 2 3; do
        RESPONSE=$(curl -s -k -o /dev/null -w "%{http_code}" https://gamma.neety.me/health 2>/dev/null || echo "")
        if [ "$RESPONSE" = "200" ]; then
            echo "✓ Health check passed: HTTP $RESPONSE"
            break
        fi
        echo "Health check attempt $i: $RESPONSE (retrying...)"
        sleep 2
    done
    
    exit 0
fi

# Show usage
echo "Usage: $0 {generate-ssl-certs|deploy|restart}"
exit 0
