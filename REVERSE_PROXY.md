# Reverse Proxy Configuration Guide

Production-ready reverse proxy configurations for LLM Gateway.

## Why Use a Reverse Proxy?

- **SSL/TLS Termination**: Handle HTTPS without modifying the application
- **Load Balancing**: Distribute traffic across multiple Gateway instances
- **Rate Limiting**: Protect against abuse and DDoS
- **Caching**: Cache static assets (dashboard)
- **Compression**: Reduce bandwidth with gzip
- **Security**: Hide backend, add security headers, IP whitelisting
- **Monitoring**: Access logs, health checks, metrics collection

## Nginx Configuration (Recommended)

### Critical Settings for LLM Workloads

LLM Gateway has unique requirements due to streaming responses and long-running requests:

1. **Disable Buffering** - Required for SSE streaming
2. **Long Timeouts** - LLM requests can take 30-60+ seconds
3. **Large Body Sizes** - Requests/responses with context can be 100KB+
4. **Connection Pooling** - Reuse connections to backend

### Basic Configuration

```nginx
# /etc/nginx/conf.d/llm-gateway.conf

upstream llm_gateway {
    least_conn;
    server 127.0.0.1:4000 max_fails=3 fail_timeout=30s;
    # For multiple instances:
    # server 127.0.0.1:4001 max_fails=3 fail_timeout=30s;
    # server 127.0.0.1:4002 max_fails=3 fail_timeout=30s;
    keepalive 32;  # Connection pool
}

server {
    listen 80;
    server_name gateway.example.com;
    
    # Redirect HTTP to HTTPS
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl http2;
    server_name gateway.example.com;
    
    # SSL Configuration
    ssl_certificate /etc/letsencrypt/live/gateway.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/gateway.example.com/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384';
    ssl_prefer_server_ciphers off;
    ssl_session_cache shared:SSL:10m;
    ssl_session_timeout 1d;
    
    # Security Headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    
    # Logging
    access_log /var/log/nginx/llm-gateway.access.log;
    error_log /var/log/nginx/llm-gateway.error.log warn;
    
    # Maximum body size (for large context windows)
    client_max_body_size 10M;
    
    # Proxy Settings
    location / {
        proxy_pass http://llm_gateway;
        
        # CRITICAL: Disable buffering for SSE streaming
        proxy_buffering off;
        proxy_cache off;
        
        # Timeouts (LLM requests can be slow)
        proxy_connect_timeout 60s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
        send_timeout 300s;
        
        # Headers
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Connection reuse
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        
        # Don't modify request body
        proxy_request_buffering off;
    }
    
    # Static assets (optional caching)
    location /static/ {
        proxy_pass http://llm_gateway;
        proxy_buffering on;
        proxy_cache_valid 200 1d;
        expires 1d;
        add_header Cache-Control "public, immutable";
    }
    
    # Health check endpoint (no auth required)
    location /health {
        proxy_pass http://llm_gateway;
        access_log off;
    }
    
    # Metrics endpoint (restrict to monitoring systems)
    location /metrics {
        proxy_pass http://llm_gateway;
        
        # IP whitelist for Prometheus
        allow 10.0.0.0/8;
        allow 192.168.0.0/16;
        deny all;
        
        proxy_buffering off;
    }
    
    # Rate limiting for admin endpoints
    location /admin/ {
        limit_req zone=admin burst=10 nodelay;
        proxy_pass http://llm_gateway;
        proxy_buffering off;
    }
}
```

### Rate Limiting Configuration

Add to `/etc/nginx/nginx.conf` in the `http` block:

```nginx
http {
    # Rate limiting zones
    limit_req_zone $binary_remote_addr zone=general:10m rate=100r/s;
    limit_req_zone $binary_remote_addr zone=admin:10m rate=10r/m;
    limit_req_zone $binary_remote_addr zone=api:10m rate=1000r/s;
    
    # Connection limiting
    limit_conn_zone $binary_remote_addr zone=conn_limit:10m;
    
    # ... rest of config
}
```

### Advanced: Load Balancing with Health Checks

```nginx
upstream llm_gateway {
    least_conn;
    
    server 127.0.0.1:4000 max_fails=3 fail_timeout=30s;
    server 127.0.0.1:4001 max_fails=3 fail_timeout=30s backup;
    
    keepalive 32;
    keepalive_requests 1000;
    keepalive_timeout 60s;
}

# Active health checks (requires nginx-plus or openresty)
# health_check interval=5s fails=3 passes=2 uri=/health;
```

### Docker Compose with Nginx

```yaml
# docker-compose.yml
version: '3.8'

services:
  nginx:
    image: nginx:alpine
    container_name: llm-gateway-nginx
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/conf.d:/etc/nginx/conf.d:ro
      - ./certs:/etc/letsencrypt:ro
      - nginx-logs:/var/log/nginx
    depends_on:
      - llm-gateway
    networks:
      - gateway-network

  llm-gateway:
    image: ghcr.io/rohitpaul/llm-gateway:latest
    container_name: llm-gateway
    restart: unless-stopped
    expose:
      - "4000"
    volumes:
      - llm-gateway-data:/data
      - ./config.yaml:/app/config.yaml:ro
    env_file:
      - .env
    environment:
      - GATEWAY_DB=/data/gateway.db
      - GATEWAY_ADMIN_KEY=${GATEWAY_ADMIN_KEY:?GATEWAY_ADMIN_KEY must be set}
    networks:
      - gateway-network

volumes:
  llm-gateway-data:
  nginx-logs:

networks:
  gateway-network:
    driver: bridge
```

### Performance Tuning

#### System-level Settings

```bash
# /etc/sysctl.conf
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.ipv4.ip_local_port_range = 1024 65535
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 15
```

Apply: `sudo sysctl -p`

#### Nginx Worker Configuration

```nginx
# /etc/nginx/nginx.conf
worker_processes auto;
worker_rlimit_nofile 65535;

events {
    worker_connections 4096;
    use epoll;
    multi_accept on;
}

http {
    # Buffer sizes
    client_body_buffer_size 128k;
    client_max_body_size 10m;
    large_client_header_buffers 4 16k;
    
    # TCP optimizations
    sendfile on;
    tcp_nopush on;
    tcp_nodelay on;
    keepalive_timeout 65;
    
    # Compression
    gzip on;
    gzip_vary on;
    gzip_proxied any;
    gzip_comp_level 6;
    gzip_types text/plain text/css text/xml application/json application/javascript application/xml;
    
    # Logging format
    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" '
                    '"$http_user_agent" "$http_x_forwarded_for" '
                    'rt=$request_time uct="$upstream_connect_time" '
                    'uht="$upstream_header_time" urt="$upstream_response_time"';
    
    access_log /var/log/nginx/access.log main;
}
```

## Common Issues & Solutions

### Issue 1: Streaming Responses Timeout

**Symptoms**: SSE streaming cuts off after 60 seconds

**Solution**:
```nginx
# Increase all timeouts
proxy_connect_timeout 60s;
proxy_send_timeout 600s;    # 10 minutes
proxy_read_timeout 600s;
send_timeout 600s;

# Disable buffering
proxy_buffering off;
proxy_cache off;
```

### Issue 2: 413 Request Entity Too Large

**Symptoms**: Large context requests fail

**Solution**:
```nginx
# Increase max body size
client_max_body_size 50M;  # Adjust based on needs

# Or disable limit (not recommended for production)
# client_max_body_size 0;
```

### Issue 3: 502 Bad Gateway

**Symptoms**: Backend connection refused

**Solutions**:

1. **Check backend is running**:
```bash
curl http://localhost:4000/health
```

2. **Increase connection pool**:
```nginx
upstream llm_gateway {
    server 127.0.0.1:4000;
    keepalive 64;  # Increase pool size
}
```

3. **Check SELinux (CentOS/RHEL)**:
```bash
setsebool -P httpd_can_network_connect 1
```

### Issue 4: Slow Performance Under Load

**Solutions**:

1. **Enable HTTP/2**:
```nginx
listen 443 ssl http2;
```

2. **Enable compression**:
```nginx
gzip on;
gzip_types application/json;
```

3. **Increase worker connections**:
```nginx
events {
    worker_connections 8192;
}
```

4. **Use keepalive connections**:
```nginx
proxy_http_version 1.1;
proxy_set_header Connection "";
```

## Monitoring & Observability

### Nginx Status Page

```nginx
location /nginx_status {
    stub_status on;
    access_log off;
    allow 127.0.0.1;
    deny all;
}
```

### Prometheus Metrics for Nginx

Use `nginx-prometheus-exporter`:

```yaml
# docker-compose.yml addition
nginx-exporter:
  image: nginx/nginx-prometheus-exporter:latest
  command:
    - '-nginx.scrape-uri=http://nginx:8080/nginx_status'
  ports:
    - "9113:9113"
  depends_on:
    - nginx
```

### Log Analysis

```bash
# Real-time error monitoring
tail -f /var/log/nginx/llm-gateway.error.log

# Analyze slow requests
awk '$NF > 5 {print $0}' /var/log/nginx/llm-gateway.access.log

# Top IPs by request count
awk '{print $1}' /var/log/nginx/llm-gateway.access.log | sort | uniq -c | sort -rn | head -10
```

## Security Hardening

### IP Whitelisting for Admin

```nginx
location /admin/ {
    allow 10.0.0.0/8;        # Internal network
    allow 192.168.1.0/24;    # VPN
    deny all;
    
    proxy_pass http://llm_gateway;
}
```

### Basic Auth (Additional Layer)

```bash
# Create password file
sudo apt-get install apache2-utils
htpasswd -c /etc/nginx/.htpasswd admin
```

```nginx
location /admin/ {
    auth_basic "Admin Area";
    auth_basic_user_file /etc/nginx/.htpasswd;
    
    proxy_pass http://llm_gateway;
}
```

### DDoS Protection

```nginx
# Limit connections per IP
limit_conn_zone $binary_remote_addr zone=addr:10m;

location / {
    limit_conn addr 10;  # Max 10 connections per IP
    proxy_pass http://llm_gateway;
}
```

## SSL/TLS Best Practices

### Generate Let's Encrypt Certificate

```bash
# Install certbot
sudo apt-get install certbot python3-certbot-nginx

# Generate certificate
sudo certbot --nginx -d gateway.example.com

# Auto-renewal
sudo crontab -e
0 12 * * * /usr/bin/certbot renew --quiet
```

### SSL Configuration (Mozilla Intermediate)

```nginx
ssl_protocols TLSv1.2 TLSv1.3;
ssl_ciphers 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256:DHE-RSA-AES256-GCM-SHA384';
ssl_prefer_server_ciphers off;

# HSTS
add_header Strict-Transport-Security "max-age=63072000" always;

# OCSP Stapling
ssl_stapling on;
ssl_stapling_verify on;
resolver 8.8.8.8 8.8.4.4 valid=300s;
resolver_timeout 5s;
```

## Alternative Reverse Proxies

### Traefik (Docker-native)

```yaml
# docker-compose.yml
version: '3.8'

services:
  traefik:
    image: traefik:v2.10
    command:
      - "--api.insecure=true"
      - "--providers.docker=true"
      - "--entrypoints.web.address=:80"
      - "--entrypoints.websecure.address=:443"
    ports:
      - "80:80"
      - "443:443"
      - "8080:8080"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock

  llm-gateway:
    image: ghcr.io/rohitpaul/llm-gateway:latest
    labels:
      - "traefik.enable=true"
      - "traefik.http.routers.gateway.rule=Host(`gateway.example.com`)"
      - "traefik.http.routers.gateway.entrypoints=websecure"
      - "traefik.http.routers.gateway.tls.certresolver=myresolver"
      - "traefik.http.middlewares.gateway-timeout.stripprefix.forceslash=false"
      # Critical: Disable buffering for streaming
      - "traefik.http.services.gateway.loadbalancer.responseforwarding.flushinterval=-1"
```

### Caddy (Automatic HTTPS)

```caddyfile
# Caddyfile
gateway.example.com {
    # Automatic HTTPS
    encode gzip
    
    # Reverse proxy with streaming support
    reverse_proxy localhost:4000 {
        # Critical: Flush immediately for SSE
        flush_interval -1
        
        # Long timeouts
        transport http {
            read_timeout 300s
            write_timeout 300s
            dial_timeout 30s
        }
    }
    
    # Rate limiting
    rate_limit {
        zone dynamic {
            key {remote_host}
            events 100
            window 1m
        }
    }
}
```

## Checklist for Production

- [ ] SSL/TLS certificates configured
- [ ] Timeouts adjusted for LLM workloads (300s+)
- [ ] Buffering disabled for streaming endpoints
- [ ] Rate limiting configured
- [ ] Security headers added
- [ ] Monitoring/logging enabled
- [ ] Health checks configured
- [ ] Connection pooling enabled
- [ ] Max body size increased (10M+)
- [ ] Gzip compression enabled
- [ ] Access logs with timing information
- [ ] Error pages customized
- [ ] IP whitelisting for admin endpoints
- [ ] DDoS protection enabled
- [ ] Backup/recovery plan documented

## Testing Your Configuration

### Test Streaming

```bash
# Test SSE streaming
curl -N -H "Authorization: Bearer sk-test" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"test"}],"stream":true}' \
  https://gateway.example.com/v1/chat/completions
```

### Test Timeouts

```bash
# Long-running request (should not timeout)
time curl -X POST https://gateway.example.com/v1/chat/completions \
  -H "Authorization: Bearer sk-test" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"Write a 5000 word essay"}]}'
```

### Test Large Payloads

```bash
# Large context (should not 413)
python3 -c "print('x' * 5000000)" | curl -X POST \
  -H "Authorization: Bearer sk-test" \
  -H "Content-Type: application/json" \
  -d @- https://gateway.example.com/v1/chat/completions
```

### Load Testing

```bash
# Using hey (https://github.com/rakyll/hey)
hey -z 30s -c 100 -m POST \
  -H "Authorization: Bearer sk-test" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"test"}]}' \
  https://gateway.example.com/v1/chat/completions
```

## Additional Resources

- [Nginx Official Documentation](https://nginx.org/en/docs/)
- [Nginx Reverse Proxy Guide](https://docs.nginx.com/nginx/admin-guide/web-server/reverse-proxy/)
- [Mozilla SSL Configuration Generator](https://ssl-config.mozilla.org/)
- [Nginx Performance Tuning](https://www.nginx.com/blog/tuning-nginx/)
- [Let's Encrypt Documentation](https://letsencrypt.org/docs/)
