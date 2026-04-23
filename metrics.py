
from prometheus_client import Counter, Histogram, Gauge

# --- Counters ---

REQUESTS_TOTAL = Counter(
    "gateway_requests_total",
    "Total number of requests received by the gateway",
    ["status"]  # label: "success" or "error" or "rejected"
)


# --- Histograms ---

INFERENCE_LATENCY = Histogram(
    "gateway_inference_latency_seconds",
    "Time spent waiting for llama.cpp to respond",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
    # these bucket boundaries let Prometheus compute p50/p95/p99
)


QUEUE_WAIT_TIME = Histogram(
    "gateway_queue_wait_seconds",
    "Time a request spent waiting in the queue before a worker picked it up",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0]
)

TOTAL_REQUEST_TIME = Histogram(
    "gateway_total_request_latency_seconds",
    "Total time from request received to response sent (queue wait + inference)",
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
)

# --- Gauges ---

QUEUE_DEPTH = Gauge(
    "gateway_queue_depth",
    "Number of requests currently waiting in the queue"
)

ACTIVE_WORKERS = Gauge(
    "gateway_active_workers",
    "Number of workers currently processing a request"
)
