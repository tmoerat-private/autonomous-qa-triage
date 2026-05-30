from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def configure_tracing(service_name: str, otlp_endpoint: str) -> None:
    """Initialize OpenTelemetry tracing with OTLP gRPC exporter."""
    resource = Resource(attributes={SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)
    # Use OTLP gRPC exporter — import it inside the function to avoid
    # hard failures if the package is missing at import time
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter,
        )

        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))
    except ImportError:
        pass  # Tracing silently disabled if exporter not installed
    trace.set_tracer_provider(provider)


def get_tracer(name: str = "autonomous-qa") -> trace.Tracer:
    return trace.get_tracer(name)


def instrument_fastapi(app: object) -> None:
    """Instrument a FastAPI app with OpenTelemetry. Call after create_app()."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)  # type: ignore[arg-type]
    except ImportError:
        pass
