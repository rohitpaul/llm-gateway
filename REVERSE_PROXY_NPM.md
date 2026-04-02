# Nginx Proxy Manager Setup

[Nginx Proxy Manager](https://nginxproxymanager.com/) provides a web UI for managing Nginx proxy hosts with SSL termination. Perfect for home labs and small-to-medium deployments.

## Why Nginx Proxy Manager?

- ✅ **Web UI** - No manual config editing
- ✅ **Let's Encrypt** - Automatic SSL certificates
- ✅ **Docker-native** - Easy deployment
- ✅ **Access Lists** - IP whitelisting
- ✅ **Custom Nginx configurations** - For advanced use cases

## Quick Start

### 1. Deploy Nginx Proxy Manager

```bash
# Create directory
mkdir -p ~/nginx-proxy-manager/{data,letsencrypt}

# Create docker-compose.yml
cat > ~/nginx-proxy-manager/docker-compose.yml <<'EOF'
version: '3.8'
services:
  app:
    image: 'jc21/nginx-proxy-manager:latest'
    restart: unless-stopped
    ports:
      - '80:80'    # Public HTTP Port
      - '443:443'   # Public HTTPS Port
      - '81:81'    # Admin Web Port
    volumes:
      - ./data:/data
      - ./letsencrypt:/etc/letsencrypt
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:81"]
      interval: 30s
      timeout: 10s
      retries: 3
EOF

# Start
cd ~/nginx-proxy-manager
docker compose up -d

# Access UI
open http://localhost:81
# Default login:
# Email: admin@example.com
# Password: changeme
```

### 2. Configure LLM Gateway Proxy Host

#### Via Web UI

1. **Add Proxy Host**
   - Domain Names: `gateway.example.com`
   - Forward Hostname: `llm-gateway` (Docker service name) or `127.0.0.1` (local)
   - Forward Port: `4000`

2. **SSL Certificate**
   - SSL Certificate: "Request a new SSL Certificate"
   - Let's Encrypt Email: `your-email@example.com`
   - Domain: `gateway.example.com`

3. **Advanced Configuration** (Critical for LLM workloads)
   
   Click "Advanced" tab and paste:

```nginx
# CRITICAL: Disable buffering for SSE streaming
proxy_buffering off;
proxy_cache off;

# Timeouts for LLM requests
proxy_connect_timeout 60s;
proxy_send_timeout 600s;
proxy_read_timeout 600s;
send_timeout 600s;

# Large body size for context windows
client_max_body_size 50M;

# Connection reuse
proxy_http_version 1.1;
proxy_set_header Connection "";

# Don't buffer request body
proxy_request_buffering off;
```

### 3. Docker Compose Integration

```yaml
# docker-compose.yml
version: '3.8'

services:
  # Nginx Proxy Manager
  nginx-proxy-manager:
    image: jc21/nginx-proxy-manager:latest
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
      - "81:81"
    volumes:
      - npm-data:/data
      - npm-letsencrypt:/etc/letsencrypt
    networks:
      - gateway-network

  # LLM Gateway
  llm-gateway:
    image: ghcr.io/rohitpaul/llm-gateway:latest
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
      - GATEWAY_ADMIN_KEY=${GATEWAY_ADMIN_KEY}
    networks:
      - gateway-network
    labels:
      # For Nginx Proxy Manager discovery (optional)
      - "com.github.nginx-proxy-manager.enable=true"

volumes:
  npm-data:
  npm-letsencrypt:
  llm-gateway-data:

networks:
  gateway-network:
    driver: bridge
```

### 4. Custom Nginx Configuration

For advanced scenarios, create a custom configuration:

```nginx
# In Nginx Proxy Manager: Advanced → Custom Configuration

# Rate limiting
limit_req_zone $binary_remote_addr zone=api:10m rate=100r/s;
limit_req_zone $binary_remote_addr zone=admin:10m rate=10r/m;

# Location blocks
location / {
    limit_req zone=api burst=50 nodelay;
    
    # LLM-specific settings
    proxy_buffering off;
    proxy_cache off;
    proxy_read_timeout 600s;
    client_max_body_size 50M;
    
    proxy_pass http://llm-gateway:4000;
}

location /admin/ {
    limit_req zone=admin burst=5 nodelay;
    proxy_pass http://llm-gateway:4000;
}

location /metrics {
    # IP whitelist
    allow 10.0.0.0/8;
    allow 192.168.0.0/16;
    deny all;
    
    proxy_pass http://llm-gateway:4000;
}

location /health {
    proxy_pass http://llm-gateway:4000;
    access_log off;
}
```

## Production Setup

### 1. Access Lists (IP Whitelisting)

Create Access List in NPM:
- **Name**: `Internal Networks`
- **Addresses**: 
  - `10.0.0.0/8`
  - `172.16.0.0/12`
  - `192.168.0.0/16`

Apply to `/admin/*` and `/metrics` endpoints.

### 2. Custom SSL Certificate

For wildcard domains or custom certs:

1. **Add SSL Certificate** in NPM
2. Upload certificate files or use DNS challenge for Let's Encrypt
3. Assign to proxy host

### 3. Health Checks

```yaml
# In docker-compose.yml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:4000/health"]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 40s
```

### 4. Multiple Instances

```yaml
# Scale LLM Gateway
llm-gateway:
  # ... existing config
  deploy:
    replicas: 3
    update_config:
      parallelism: 1
      delay: 10s
      failure_action: rollback
```

Update NPM to load balance:
- Forward Hostname: `llm-gateway` (Docker DNS rounds robin)
- Or use Nginx upstream block in Advanced config

## Troubleshooting

### Issue: Streaming Cuts Off

**Symptoms**: SSE streams disconnect after 60s

**Solution**: Add to Advanced configuration:
```nginx
proxy_read_timeout 600s;
proxy_send_timeout 600s;
proxy_buffering off;
```

### Issue: 502 Bad Gateway

**Symptoms**: Random 502 errors

**Solutions**:
1. Check container health: `docker ps`
2. Check logs: `docker logs llm-gateway`
3. Increase keepalive: `proxy_http_version 1.1;`
4. Check Docker network: `docker network inspect gateway-network`

### Issue: Large Requests Fail (413)

**Symptoms**: Requests with large context fail

**Solution**: Add to Advanced configuration:
```nginx
client_max_body_size 50M;
```

### Issue: Can't Access Admin UI

**Symptoms**: 403 Forbidden on port 81

**Solution**: 
1. Check container logs: `docker logs nginx-proxy-manager`
2. Reset admin password:
```bash
docker exec -it nginx-proxy-manager-1 /bin/bash
sqlite3 /data/database.sqlite
UPDATE user SET email='admin@example.com' WHERE id=1;
```

## Monitoring

### Enable Nginx Status

Add to Advanced configuration:
```nginx
location /nginx_status {
    stub_status on;
    access_log off;
    allow 127.0.0.1;
    deny all;
}
```

### Prometheus Metrics

Nginx Proxy Manager doesn't expose native Prometheus metrics. Use one of these alternatives:

1. **nginx-prometheus-exporter** (separate container)
2. **File-based logs** → Promtail → Loki
3. **Custom endpoint** in your app

## Backup & Restore

### Backup NPM Configuration

```bash
# Backup volumes
docker run --rm \
  -v nginx-proxy-manager_npm-data:/data \
  -v $(pwd):/backup \
  alpine tar czf /backup/npm-backup-$(date +%Y%m%d).tar.gz /data
```

### Restore

```bash
docker run --rm \
  -v nginx-proxy-manager_npm-data:/data \
  -v $(pwd):/backup \
  alpine tar xzf /backup/npm-backup-20240402.tar.gz -C /
```

## Best Practices

1. ✅ **Change default credentials** immediately after first login
2. ✅ **Enable 2FA** for admin accounts
3. ✅ **Regular backups** of NPM data volume
4. ✅ **Use Docker secrets** for sensitive data
5. ✅ **Monitor certificate expiry** (set calendar reminder)
6. ✅ **Test failover** regularly
7. ✅ **Keep NPM updated** (watch releases)
8. ✅ **Document all custom configs** in version control

## Security Checklist

- [ ] Changed default admin password
- [ ] Enabled HTTPS for admin UI (port 81)
- [ ] Set up access lists for sensitive endpoints
- [ ] Configured rate limiting
- [ ] Enabled HSTS headers
- [ ] Set up fail2ban for brute force protection
- [ ] Regular security updates
- [ ] Backup encryption enabled

## Comparison: NPM vs Manual Nginx

| Feature | Nginx Proxy Manager | Manual Nginx |
|---------|---------------------|--------------|
| **Web UI** | ✅ Yes | ❌ No |
| **Auto SSL** | ✅ Let's Encrypt | ⚠️ Manual/Certbot |
| **Config Management** | ✅ Database | ✅ Text files |
| **Access Control** | ✅ Built-in UI | ⚠️ Manual config |
| **Load Balancing** | ⚠️ Basic | ✅ Advanced |
| **Performance** | ⚠️ Slight overhead | ✅ Optimal |
| **Learning Curve** | ✅ Easy | ⚠️ Steep |
| **Best For** | Home labs, small-medium | Large scale, advanced |

## Next Steps

1. Deploy Nginx Proxy Manager with Docker Compose
2. Change default credentials
3. Add proxy host for LLM Gateway
4. Request SSL certificate
5. Add advanced LLM configuration
6. Set up access lists
7. Configure monitoring
8. Test streaming endpoints
9. Document your setup
10. Schedule regular backups
