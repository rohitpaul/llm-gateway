# Quick Start: Nginx Reverse Proxy

## Development Setup (No SSL)

1. **Start LLM Gateway**:
   ```bash
   docker compose up -d
   ```

2. **Copy Nginx config**:
   ```bash
   sudo cp examples/nginx/llm-gateway.conf /etc/nginx/conf.d/
   sudo cp examples/nginx/nginx.conf /etc/nginx/nginx.conf
   ```

3. **Test and reload Nginx**:
   ```bash
   sudo nginx -t
   sudo nginx -s reload
   ```

4. **Access Gateway**:
   ```bash
   curl http://localhost/health
   ```

## Production Setup (With SSL)

1. **Generate SSL certificates**:
   ```bash
   sudo apt-get install certbot python3-certbot-nginx
   sudo certbot --nginx -d gateway.example.com
   ```

2. **Use production config**:
   ```bash
   sudo cp examples/nginx/llm-gateway-production.conf /etc/nginx/conf.d/llm-gateway.conf
   sudo nginx -t
   sudo nginx -s reload
   ```

3. **Setup auto-renewal**:
   ```bash
   sudo crontab -e
   # Add: 0 12 * * * /usr/bin/certbot renew --quiet
   ```

## Docker Compose (All-in-One)

1. **Start everything**:
   ```bash
   docker compose -f examples/docker-compose-nginx.yml up -d
   ```

2. **With monitoring**:
   ```bash
   docker compose -f examples/docker-compose-nginx.yml --profile monitoring up -d
   ```

## Verify Setup

```bash
# Test health endpoint
curl http://localhost/health

# Test streaming (critical!)
curl -N -H "Authorization: Bearer sk-test" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"test"}],"stream":true}' \
  http://localhost/v1/chat/completions

# Check Nginx logs
tail -f /var/log/nginx/llm-gateway.access.log

# Test metrics
curl http://localhost:4000/metrics  # Direct (no proxy)
curl http://localhost/metrics       # Through proxy
```

## Troubleshooting

### Streaming not working

**Check buffering is off**:
```bash
grep "proxy_buffering off" /etc/nginx/conf.d/llm-gateway.conf
```

### 502 Bad Gateway

**Check backend**:
```bash
curl http://localhost:4000/health
docker logs llm-gateway
```

### Timeouts

**Increase in config**:
```nginx
proxy_read_timeout 600s;
```

### Large requests failing

**Increase max body size**:
```nginx
client_max_body_size 50M;
```

## Performance Testing

```bash
# Install hey
go install github.com/rakyll/hey@latest

# Load test
hey -z 30s -c 100 -m POST \
  -H "Authorization: Bearer sk-test" \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-4o","messages":[{"role":"user","content":"test"}]}' \
  http://localhost/v1/chat/completions
```

## Security Checklist

- [ ] SSL/TLS configured
- [ ] Rate limiting enabled
- [ ] Admin endpoints protected (IP whitelist or basic auth)
- [ ] Security headers added
- [ ] Nginx version hidden (server_tokens off)
- [ ] Access logging enabled
- [ ] Error pages customized
- [ ] Regular security updates
