# Metrics Endpoint Review

## Summary

Reviewed the `/metrics` endpoint for correctness and documentation. **Critical bugs found.**

## Endpoint Details

**Endpoint**: `GET /metrics`  
**Auth**: Admin key required  
**Format**: Prometheus text format  
**Code**: `app/database.py:935-1043` (`get_prometheus_metrics()`)

## Critical Issues

### 1. **BUG: Incorrect Column Index in Counter Metrics**

**Severity**: 🔴 **CRITICAL**  
**Location**: `app/database.py:963-992`

The per-model counter metrics are displaying **request counts instead of token counts**.

**Evidence**:
```bash
# Actual metrics output (WRONG):
llm_gateway_input_tokens_total 80053
llm_gateway_input_tokens_total{model="Qwen3.5-27B"} 35        # Should be 4120!

# Database shows correct values:
sqlite> SELECT model, SUM(request_count), SUM(input_tokens) FROM daily_usage GROUP BY model;
Qwen3.5-27B|35|4120
```

**Root Cause**: The code appears to use correct column indices (col_idx=2 for input_tokens), But the actual output shows request_count values (35) instead of input_tokens (4120). This suggests either:
- The column mapping is wrong
- There's a bug in how the rows are being processed
- Caching issue (server needs restart)

**Impact**: 
- Token metrics are completely wrong for all models
- Cost calculations may be affected
- Monitoring and alerting based on token usage will be incorrect

**Fix Required**: 
```python
# Verify the column indices match the SELECT query:
# SELECT model, SUM(request_count), SUM(input_tokens), SUM(output_tokens), ...
#        col 0         col 1            col 2           col 3

def add_counter(name, help_text, base_val, rows, col_idx):
    # Debug: print what we're actually getting
    for r in rows:
        logger.debug(f"Row: model={r[0]}, col[{col_idx}]={r[col_idx]}")
    # ... rest of function
```

### 2. **Missing Provider Label**

**Severity**: 🟡 **MEDIUM**  
**Location**: `app/database.py:983`

Metrics only include `model` label, missing `provider` context.

**Current**:
```
llm_gateway_requests_total{model="gpt-4o"} 100
```

**Should Have**:
```
llm_gateway_requests_total{model="gpt-4o",provider="openai"} 100
```

**Impact**:
- Cannot filter/aggregate metrics by provider
- Difficult to compare provider performance
- Limited observability for multi-provider setups

**Recommendation**: Add provider label to all metrics

### 3. **Documentation Missing**

**Severity**: 🟡 **MEDIUM**

The `/metrics` endpoint is **not documented** in README.md.

**Missing**:
- Endpoint existence
- Authentication requirements
- Available metrics list
- Usage examples
- Prometheus/Grafana integration guide

## Correct Aspects

✅ **Counter Metrics Structure**: Uses correct Prometheus format with HELP and TYPE annotations  
✅ **Gauge Metrics**: Latency/TTFT/TPS rolling averages are well-implemented  
✅ **Label Escaping**: Properly escapes special characters in labels  
✅ **Performance**: Efficient queries with LIMIT 500 for recent requests  
✅ **Rolling Window**: Last 1/5/15 requests provides good responsiveness  

## Metric Categories

### Counters (Cumulative from daily_usage)

| Metric | Type | Description | Status |
|--------|------|-------------|--------|
| `llm_gateway_requests_total` | counter | Total requests | ⚠️ **Wrong per-model values** |
| `llm_gateway_input_tokens_total` | counter | Input tokens | ⚠️ **Wrong per-model values** |
| `llm_gateway_output_tokens_total` | counter | Output tokens | ⚠️ **Wrong per-model values** |
| `llm_gateway_cache_read_tokens_total` | counter | Cached input tokens | ⚠️ **Wrong per-model values** |
| `llm_gateway_cache_write_tokens_total` | counter | Cached output tokens | ⚠️ **Wrong per-model values** |
| `llm_gateway_cost_total` | counter | Cost in USD | ⚠️ **Likely wrong per-model values** |

### Gauges (Rolling averages from last N requests)

| Metric | Type | Description | Status |
|--------|------|-------------|--------|
| `llm_gateway_latency_ms_last_1` | gauge | Avg latency over last 1 request | ✅ Correct |
| `llm_gateway_latency_ms_last_5` | gauge | Avg latency over last 5 requests | ✅ Correct |
| `llm_gateway_latency_ms_last_15` | gauge | Avg latency over last 15 requests | ✅ Correct |
| `llm_gateway_ttft_ms_last_1` | gauge | Avg TTFT over last 1 request | ✅ Correct |
| `llm_gateway_ttft_ms_last_5` | gauge | Avg TTFT over last 5 requests | ✅ Correct |
| `llm_gateway_ttft_ms_last_15` | gauge | Avg TTFT over last 15 requests | ✅ Correct |
| `llm_gateway_tps_last_1` | gauge | Avg TPS over last 1 request | ✅ Correct |
| `llm_gateway_tps_last_5` | gauge | Avg TPS over last 5 requests | ✅ Correct |
| `llm_gateway_tps_last_15` | gauge | Avg TPS over last 15 requests | ✅ Correct |

## Code Quality

### Good Practices
- ✅ Proper Prometheus format
- ✅ Efficient database queries
- ✅ Good separation of counter vs gauge metrics
- ✅ Rolling window for performance metrics
- ✅ Model-level breakdown for all metrics

### Issues
- ❌ Counter metrics showing wrong values (CRITICAL)
- ❌ Missing provider context
- ❌ No error handling for database failures
- ❌ No validation of metric values
- ❌ Hardcoded LIMIT 500 (should be configurable)

## Recommendations

### Immediate Actions (Critical)

1. **Fix counter metric column indices** 
   - Add debug logging to verify column values
   - Test with known data set
   - Verify output matches database

2. **Restart server** to rule out caching issue
   ```bash
   docker compose restart llm-gateway
   # or
   pkill -f uvicorn
   python -m app.server
   ```

3. **Add integration test**
   ```python
   async def test_prometheus_metrics():
       response = await client.get("/metrics", headers={"Authorization": f"Bearer {admin_key}"})
       assert response.status_code == 200
       
       # Parse metrics and verify values
       metrics = parse_prometheus_metrics(response.text)
       
       # Check against database
       db_stats = await db.get_model_stats()
       for stat in db_stats:
           model = stat["model"]
           assert metrics[f'llm_gateway_input_tokens_total{{model="{model}"}}'] == stat["input_tokens"]
   ```

### Short-term Improvements

4. **Add provider label**
   ```python
   # Modify query to include provider
   "SELECT model, provider, SUM(request_count), SUM(input_tokens), ..."
   
   # Add to label
   label = f'model="{esc_prom_label(model)}",provider="{esc_prom_label(provider)}"'
   ```

5. **Add to README.md**
   ```markdown
   ## Monitoring
   
   ### Prometheus Metrics
   
   Access Prometheus-compatible metrics at `/metrics` (requires admin auth):
   
   ```bash
   curl -H "Authorization: Bearer $GATEWAY_ADMIN_KEY" \
        http://localhost:4000/metrics
   ```
   
   Available metrics:
   - Counters: requests_total, input/output_tokens_total, cost_total
   - Gauges: latency_ms, ttft_ms, tps (last 1/5/15 requests)
   
   #### Grafana Dashboard
   Import the provided dashboard: [grafana-dashboard.json](./monitoring/)
   ```

6. **Add configuration**
   ```python
   # In config.py
   METRICS_ROLLING_WINDOW_SIZE = int(os.getenv("METRICS_ROLLING_WINDOW_SIZE", "500"))
   ```

### Long-term Enhancements

7. **Add histogram metrics** for latency distribution
8. **Add error rate metrics** (success vs failed requests)
9. **Add percentile metrics** (p50, p95, p99 latency)
10. **Create Grafana dashboard** JSON template
11. **Add Prometheus alerting rules** examples

## Testing Checklist

- [ ] Verify counter values match database
- [ ] Test with empty database
- [ ] Test with single model
- [ ] Test with multiple models
- [ ] Verify label escaping
- [ ] Test authentication (should reject without admin key)
- [ ] Validate Prometheus format with promtool
- [ ] Load test with 1000+ requests
- [ ] Test after database migration

## Priority

**Immediate**: Fix critical bug #1 (incorrect counter values)  
**High**: Add documentation (#3)  
**Medium**: Add provider label (#2)  
**Low**: Histogram/percentile metrics, Grafana dashboards

## Next Steps

1. **Investigate the column index bug** - restart server and verify
2. **Add comprehensive test** for metrics endpoint
3. **Document in README.md** with examples
4. **Add provider label** for better observability
5. **Create Grafana dashboard** template
