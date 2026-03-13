"""Tests for Prometheus metrics module."""

import importlib
import time


def test_metrics_disabled_by_default():
    """PUSHGATEWAY_URL not set → metrics disabled, all objects None."""
    from minisweagent.utils import metrics

    assert not metrics.METRICS_ENABLED
    assert metrics.STEP_DURATION is None
    assert metrics.REGISTRY is None


def test_track_duration_noop():
    """track_duration works as a no-op context manager when disabled."""
    from minisweagent.utils.metrics import track_duration

    with track_duration(None, {}) as _:
        pass  # should not raise


def test_record_functions_noop():
    """All record_* functions are no-ops when disabled."""
    from minisweagent.utils.metrics import (
        init_metrics,
        push_metrics,
        record_model_call,
        record_step_tool_count,
        record_tool_call,
    )

    # None of these should raise
    init_metrics("m", "t", "e")
    record_model_call("m", "t")
    record_tool_call("e", "cmd", "success", 100)
    record_step_tool_count("m", 3)
    push_metrics()


def test_track_duration_records(monkeypatch):
    """When enabled, track_duration observes duration on the histogram."""
    monkeypatch.setenv("MSWEA_PUSHGATEWAY_URL", "http://fake:9091")
    import minisweagent.utils.metrics as mod

    importlib.reload(mod)

    try:
        assert mod.METRICS_ENABLED
        assert mod.STEP_DURATION is not None

        with mod.track_duration(mod.STEP_DURATION, {"model_name": "test", "env_type": "local"}):
            time.sleep(0.01)

        # Verify the histogram got a sample
        samples = [s for s in mod.STEP_DURATION.collect()[0].samples if s.name == "mswea_step_duration_seconds_count"]
        assert any(s.value >= 1 for s in samples)
    finally:
        monkeypatch.delenv("MSWEA_PUSHGATEWAY_URL")
        importlib.reload(mod)


def test_record_tool_call_increments(monkeypatch):
    """record_tool_call increments counter and observes histogram."""
    monkeypatch.setenv("MSWEA_PUSHGATEWAY_URL", "http://fake:9091")
    import minisweagent.utils.metrics as mod

    importlib.reload(mod)

    try:
        mod.record_tool_call("LocalEnv", "find", "success", 256)

        counter_samples = [
            s for s in mod.TOOL_CALLS_TOTAL.collect()[0].samples if s.name == "mswea_tool_calls_total"
        ]
        assert any(s.value >= 1 and s.labels["command_prefix"] == "find" for s in counter_samples)

        bytes_samples = [
            s
            for s in mod.TOOL_CALL_OUTPUT_BYTES.collect()[0].samples
            if s.name == "mswea_tool_call_output_bytes_count"
        ]
        assert any(s.value >= 1 for s in bytes_samples)
    finally:
        monkeypatch.delenv("MSWEA_PUSHGATEWAY_URL")
        importlib.reload(mod)


def test_push_metrics_error_handling(monkeypatch):
    """push_metrics logs warning on failure, does not crash."""
    monkeypatch.setenv("MSWEA_PUSHGATEWAY_URL", "http://fake:9091")
    import minisweagent.utils.metrics as mod

    importlib.reload(mod)

    try:
        # push_to_gateway will fail since http://fake:9091 is not reachable — should not raise
        mod.push_metrics()
    finally:
        monkeypatch.delenv("MSWEA_PUSHGATEWAY_URL")
        importlib.reload(mod)
