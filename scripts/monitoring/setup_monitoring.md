# Token Monitoring Setup

## Enable Claude Code telemetry

Add to `E:\Corvus_Careebridge\.env`:
```
CLAUDE_CODE_ENABLE_TELEMETRY=1
OTEL_METRICS_EXPORTER=otlp
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
OTEL_EXPORTER_OTLP_PROTOCOL=grpc
```

Restart Claude Code after adding these.

## Key metric

`claude_code.token.usage` — split by:
- `type`: input | output | cache_read | cache_creation
- `model`: claude-sonnet-4-6 | etc.

## Option A: Full Grafana stack (recommended for production)

1. Install OTel Collector: https://opentelemetry.io/docs/collector/getting-started/
2. Run: `otelcol --config scripts/monitoring/otel_collector_config.yaml`
3. Install Prometheus: https://prometheus.io/download/
4. Run: `prometheus --config.file=scripts/monitoring/prometheus.yml`
5. Install Grafana: https://grafana.com/grafana/download
6. Add Prometheus as data source (http://localhost:9090)
7. Import dashboard: scripts/monitoring/grafana_dashboard.json

## Option B: Lightweight — ccusage CLI (no infrastructure)

```bash
npx ccusage                          # usage this month
npx ccusage --since 2026-06-01       # since a date
npx ccusage --model claude-sonnet    # by model
```

## Option C: Token tracker hook (already configured)

`hooks/hook_token_tracker.py` logs tool call frequency to SQLite.
Query: `SELECT tool_name, COUNT(*) FROM token_events GROUP BY tool_name ORDER BY 2 DESC LIMIT 20`

The health sweep skill (`skill_health_sweep.md`) reads this and reports weekly.

## Alerts to configure in Grafana

- Token burn rate > 10k tokens/min for > 5 minutes → runaway loop
- Cache hit rate < 20% over 1 hour → CLAUDE.md too large or cron intervals too long
- Output tokens > 2× input tokens → verbosity problem in a skill
- Sudden spike on `mcp__gemini__` tools → video annotation running unexpectedly
