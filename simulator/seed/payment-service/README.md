# payment-service

Demo payment API used by the BLACKBOX incident simulation.

- `POST /payments` — capture a payment (holds one pooled DB connection while scoring).
- `GET /health`, `GET /health/live`, `GET /metrics/summary`.

Run tests: `pytest -q tests`.
