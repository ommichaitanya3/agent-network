from collections import defaultdict, deque

import numpy as np


class Metrics:
    """In-process counters and latency histograms (swap for Prometheus in production)."""

    def __init__(self) -> None:
        self.counters: dict[str, int] = defaultdict(int)
        self._lat: dict[str, deque] = defaultdict(lambda: deque(maxlen=1000))

    def inc(self, name: str, n: int = 1) -> None:
        self.counters[name] += n

    def observe(self, name: str, ms: float) -> None:
        self._lat[name].append(ms)

    def latency_summary(self) -> dict:
        out = {}
        for name, values in self._lat.items():
            arr = np.asarray(values)
            out[name] = {
                "count": int(arr.size),
                "p50_ms": round(float(np.percentile(arr, 50)), 3),
                "p95_ms": round(float(np.percentile(arr, 95)), 3),
            }
        return out
