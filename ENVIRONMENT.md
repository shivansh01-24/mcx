# Configuration & Environment Variables

The platform uses a unified configuration system loaded from `config.yaml`. Any configuration value can be overridden using environment variables.

---

## 1. Environment Overrides Rule
Environment variables must follow this naming pattern to match nested YAML structure:
* Prefix: `MCX_`
* Nesting Separator: Double underscores (`__`)
* Example: `database.url` is overridden by setting `MCX_DATABASE__URL`.

---

## 2. Configuration Parameters Reference

| Variable | Description | Default |
| :--- | :--- | :--- |
| `MCX_DATABASE__URL` | PostgreSQL connection string | `postgresql://postgres:postgres@db:5432/mcx_platform` |
| `MCX_REDIS__HOST` | Redis Server hostname | `redis` |
| `MCX_REDIS__PORT` | Redis Server port | `6379` |
| `MCX_REDIS__TTL_SECONDS` | TTL for cached LTP keys | `30` |
| `MCX_CONSENSUS__OUTLIER_THRESHOLD_PERCENT` | Deviation boundary for outlier rejection | `1.5` |
| `MCX_CONSENSUS__MIN_REQUIRED_SOURCES` | Min active feeds before failsafe triggers | `2` |
| `MCX_CONSENSUS__FALLBACK_TO_PROXY_ON_FAILURE` | Trigger Yahoo Finance proxy fallback | `true` |
| `MCX_CIRCUIT_BREAKER__FAILURE_THRESHOLD` | Failures count before CB trips | `3` |
| `MCX_CIRCUIT_BREAKER__RECOVERY_TIMEOUT_SECONDS` | CB recovery timeout | `30` |
