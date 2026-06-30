# Changelog

All notable changes to the MCX Market Data Platform will be documented in this file.

---

## [1.0.0] - 2026-06-30

### Added
* Dynamic module reloader and hot swappable Python collector plugin support.
* Decoupled Event Bus with bounded queue support and backpressure controls.
* Consensus validation engine with outlier rejection and stale fallback modes.
* Standardized envelope JSON response formatting across all routes.
* Developer Dashboard with live charts, statistics, logs, and leaderboard metrics.
* API key authentication with Redis-backed rate-limiting and daily quotas.
* Linux `start.sh` and Windows `start.bat` scripts for automated container setups.
* One-click configuration configs for Railway hosting.

### Changed (Production Hardening)
* Migrated from synchronous `redis` library calls to non-blocking `redis.asyncio` client connections to optimize loop latencies.
* Rewrote daily OHLC aggregation queries to use indexed Postgres `func.max` and `func.min` scalar aggregations.
* Added thread-safe consensus lock loops per commodity to avoid ingestion races.
* Filtered Yahoo Finance proxy estimations out of the live consensus evaluation loop, reserving it strictly for fallback operations.
