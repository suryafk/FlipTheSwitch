from payment_service.metrics import Window, health_status


def test_window_error_rate_and_percentiles():
    w = Window(seconds=15)
    for i in range(98):
        w.record(201, 100.0 + i, now=1000.0)
    w.record(503, 4000.0, now=1000.0)
    w.record(503, 4000.0, now=1000.0)
    s = w.summary(now=1001.0)
    assert s["requests"] == 100
    assert s["error_rate_pct"] == 2.0
    assert 140 <= s["latency_ms_p50"] <= 160


def test_window_drops_old_samples():
    w = Window(seconds=15)
    w.record(503, 10.0, now=1000.0)
    w.record(201, 10.0, now=1020.0)
    assert w.summary(now=1020.0)["requests"] == 1


def test_pool_utilization_is_averaged():
    w = Window(seconds=15)
    w.record_pool(20.0, now=1000.0)
    w.record_pool(40.0, now=1001.0)
    assert w.summary(now=1002.0)["pool_utilization_pct"] == 30.0


def test_health_healthy_and_degraded():
    ok = {"error_rate_pct": 0.2, "latency_ms_p50": 110, "pool_utilization_pct": 24}
    bad = {"error_rate_pct": 17.0, "latency_ms_p50": 3900, "pool_utilization_pct": 100}
    assert health_status(ok) == ("healthy", [])
    status, reasons = health_status(bad)
    assert status == "degraded" and len(reasons) == 3


def test_empty_window():
    s = Window().summary(now=5.0)
    assert s["requests"] == 0 and s["latency_ms_p50"] is None
