from prometheus_client import Counter, Gauge, Histogram, make_asgi_app

# Counters
FAILURES_RECEIVED = Counter(
    "failures_received_total",
    "Total pipeline failure webhooks received",
    ["provider"],
)
TRIAGE_COMPLETED = Counter(
    "triage_completed_total",
    "Total triage runs completed",
    ["status"],  # "success" | "failed"
)
CLASSIFICATION_DISTRIBUTION = Counter(
    "classification_category_total",
    "Failure classifications by category",
    ["category"],
)
TICKETS_CREATED = Counter(
    "tickets_created_total",
    "Tickets created by provider",
    ["provider"],
)
NOTIFICATIONS_SENT = Counter(
    "notifications_sent_total",
    "Notifications sent by channel",
    ["channel"],
)

# Histograms
TRIAGE_DURATION = Histogram(
    "triage_duration_seconds",
    "End-to-end triage pipeline duration in seconds",
    buckets=[1, 5, 10, 30, 60, 120, 300],
)
CLASSIFICATION_DURATION = Histogram(
    "classification_duration_seconds",
    "Time spent on Claude classification in seconds",
    buckets=[0.5, 1, 2, 5, 10, 30],
)

# Gauges
ACTIVE_TRIAGE_RUNS = Gauge(
    "active_triage_runs",
    "Number of triage runs currently in progress",
)


def mount_metrics_endpoint(app: object) -> None:
    """Mount /metrics Prometheus endpoint on the FastAPI app."""
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)  # type: ignore[attr-defined]
