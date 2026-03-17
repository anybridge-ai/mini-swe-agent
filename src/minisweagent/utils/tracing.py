"""OpenTelemetry tracing. Activated by MSWEA_OTLP_ENDPOINT env var."""

import atexit
import contextlib
import logging
import os
import threading
import time

OTLP_ENDPOINT = os.getenv("MSWEA_OTLP_ENDPOINT", "")
TRACING_ENABLED = bool(OTLP_ENDPOINT)

if TRACING_ENABLED:
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as e:
        msg = "opentelemetry packages are required for tracing. Install with: pip install mini-swe-agent[tracing]"
        raise ImportError(msg) from e

    resource = Resource.create({"service.name": "mini-swe-agent"})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=f"{OTLP_ENDPOINT}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter, schedule_delay_millis=1000))
    trace.set_tracer_provider(provider)
    tracer = trace.get_tracer("mini-swe-agent")
    atexit.register(provider.shutdown)
    _flush_lock = threading.Lock()

    # Auto-inject traceparent header into outgoing HTTP requests (e.g., to vLLM)
    # so that downstream services can join the same trace.
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except ImportError:
        pass
else:
    tracer = None
    _flush_lock = None


@contextlib.contextmanager
def start_span(name: str, attributes: dict | None = None):
    """Start a span context manager. No-op when disabled (yields None).
    Automatically records latency_seconds attribute on span close."""
    if not TRACING_ENABLED:
        yield None
        return
    with tracer.start_as_current_span(name) as span:
        if attributes:
            for k, v in attributes.items():
                span.set_attribute(k, v)
        start = time.perf_counter()
        yield span
        span.set_attribute("latency_seconds", time.perf_counter() - start)


def shutdown_tracing():
    """Flush pending spans without shutting down the provider.
    Serialized with a lock so concurrent agents don't race on flush."""
    if not TRACING_ENABLED:
        return
    with _flush_lock:
        try:
            trace.get_tracer_provider().force_flush(timeout_millis=10000)
        except Exception as e:
            logging.getLogger("minisweagent").warning(f"Failed to flush traces: {e}")
