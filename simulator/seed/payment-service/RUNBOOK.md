# payment-service runbook

## Capacity
Every payment holds one database connection for about 100 ms (fraud-score query).
At the measured peak of 120 requests/second about 12 connections are busy on average, with
bursts up to 30. The pool (`config/database.yml`: `pool_size + max_overflow`) must be at least
`peak_concurrent_db_sessions` in `config/capacity.yml`. When the pool is too small, requests
queue for a connection and fail after `pool_timeout_seconds` with HTTP 503 `DB_POOL_TIMEOUT`.

## Deploys
Every deploy is recorded with its deploy id and git sha (see /health).
