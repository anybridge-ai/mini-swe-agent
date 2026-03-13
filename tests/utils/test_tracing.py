"""Tests for OpenTelemetry tracing module."""

import importlib


def test_tracing_disabled_by_default():
    """OTLP_ENDPOINT not set → tracing disabled."""
    from minisweagent.utils import tracing

    assert not tracing.TRACING_ENABLED
    assert tracing.tracer is None


def test_start_span_noop():
    """start_span yields None when disabled."""
    from minisweagent.utils.tracing import start_span

    with start_span("test.span", {"key": "val"}) as span:
        assert span is None


def test_shutdown_tracing_noop():
    """shutdown_tracing is a no-op when disabled."""
    from minisweagent.utils.tracing import shutdown_tracing

    shutdown_tracing()  # should not raise


class _CollectingExporter:
    """Minimal SpanExporter that collects spans in a list."""

    def __init__(self):
        self.spans = []

    def export(self, spans):
        self.spans.extend(spans)
        from opentelemetry.sdk.trace.export import SpanExportResult

        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=None):
        return True


def test_start_span_creates_span(monkeypatch):
    """When enabled, start_span creates a real span with attributes."""
    monkeypatch.setenv("MSWEA_OTLP_ENDPOINT", "http://fake:4318")
    import minisweagent.utils.tracing as mod

    importlib.reload(mod)

    try:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        exporter = _CollectingExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        mod.tracer = provider.get_tracer("test")

        with mod.start_span("test.op", {"key": "value"}) as span:
            assert span is not None
            span.set_attribute("extra", 42)

        assert len(exporter.spans) == 1
        assert exporter.spans[0].name == "test.op"
        assert exporter.spans[0].attributes["key"] == "value"
        assert exporter.spans[0].attributes["extra"] == 42
        assert exporter.spans[0].attributes["latency_seconds"] >= 0
    finally:
        monkeypatch.delenv("MSWEA_OTLP_ENDPOINT")
        importlib.reload(mod)


def test_span_nesting(monkeypatch):
    """Child spans have correct parent-child relationship."""
    monkeypatch.setenv("MSWEA_OTLP_ENDPOINT", "http://fake:4318")
    import minisweagent.utils.tracing as mod

    importlib.reload(mod)

    try:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor

        exporter = _CollectingExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        mod.tracer = provider.get_tracer("test")

        with mod.start_span("parent") as _parent_span:
            with mod.start_span("child") as _child_span:
                pass

        assert len(exporter.spans) == 2
        child, parent = exporter.spans[0], exporter.spans[1]
        assert child.name == "child"
        assert parent.name == "parent"
        assert child.parent.span_id == parent.context.span_id
    finally:
        monkeypatch.delenv("MSWEA_OTLP_ENDPOINT")
        importlib.reload(mod)
