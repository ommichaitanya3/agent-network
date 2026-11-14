from app.delivery import CircuitBreaker


def test_circuit_opens_after_threshold_and_recovers_after_cooldown():
    now = [0.0]
    cb = CircuitBreaker(threshold=3, cooldown=30, clock=lambda: now[0])
    assert cb.allow()
    for _ in range(3):
        cb.record_failure()
    assert not cb.allow()
    now[0] = 31
    assert cb.allow()               # half-open probe
    cb.record_success()
    assert cb.allow() and cb.failures == 0


def test_circuit_reopens_if_probe_fails():
    now = [0.0]
    cb = CircuitBreaker(threshold=2, cooldown=10, clock=lambda: now[0])
    cb.record_failure(); cb.record_failure()
    now[0] = 11
    assert cb.allow()
    cb.record_failure()
    assert not cb.allow()
