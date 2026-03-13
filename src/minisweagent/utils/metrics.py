"""Prometheus Push Gateway metrics. Activated by MSWEA_PUSHGATEWAY_URL env var."""

import contextlib
import logging
import os
import time

PUSHGATEWAY_URL = os.getenv("MSWEA_PUSHGATEWAY_URL", "")
METRICS_ENABLED = bool(PUSHGATEWAY_URL)

if METRICS_ENABLED:
    try:
        from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, Info, push_to_gateway
    except ImportError as e:
        msg = "prometheus-client is required for metrics. Install with: pip install mini-swe-agent[metrics]"
        raise ImportError(msg) from e

    REGISTRY = CollectorRegistry()

    # Step/Query metrics
    STEP_DURATION = Histogram(
        "mswea_step_duration_seconds", "Step total duration", ["model_name", "env_type"], registry=REGISTRY
    )
    QUERY_DURATION = Histogram(
        "mswea_query_duration_seconds", "LLM call latency", ["model_name", "model_type"], registry=REGISTRY
    )
    MODEL_CALLS_TOTAL = Counter(
        "mswea_model_calls_total", "Model API calls", ["model_name", "model_type"], registry=REGISTRY
    )

    # Tool call metrics
    EXECUTE_ACTIONS_DURATION = Histogram(
        "mswea_execute_actions_duration_seconds",
        "All tool calls duration per step",
        ["env_type", "model_name"],
        registry=REGISTRY,
    )
    TOOL_CALL_DURATION = Histogram(
        "mswea_tool_call_duration_seconds",
        "Individual tool call duration",
        ["env_type", "command_prefix"],
        registry=REGISTRY,
    )
    TOOL_CALLS_TOTAL = Counter(
        "mswea_tool_calls_total",
        "Tool call count by status",
        ["env_type", "command_prefix", "status"],
        registry=REGISTRY,
    )
    TOOL_CALL_OUTPUT_BYTES = Histogram(
        "mswea_tool_call_output_bytes", "Tool output size", ["env_type", "command_prefix"], registry=REGISTRY
    )
    TOOL_CALLS_PER_STEP = Histogram(
        "mswea_tool_calls_per_step", "Tool calls per step", ["model_name"], registry=REGISTRY
    )

    # Agent state
    CURRENT_STEP = Gauge("mswea_current_step", "Current step number", ["agent_id"], registry=REGISTRY)
    AGENT_INFO = Info("mswea_agent", "Agent configuration", registry=REGISTRY)
else:
    REGISTRY = None
    STEP_DURATION = QUERY_DURATION = MODEL_CALLS_TOTAL = None
    EXECUTE_ACTIONS_DURATION = TOOL_CALL_DURATION = TOOL_CALLS_TOTAL = None
    TOOL_CALL_OUTPUT_BYTES = TOOL_CALLS_PER_STEP = CURRENT_STEP = AGENT_INFO = None


@contextlib.contextmanager
def track_duration(histogram, labels: dict):
    """time.perf_counter() based duration measurement. No-op when disabled."""
    if not METRICS_ENABLED:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        histogram.labels(**labels).observe(time.perf_counter() - start)


def push_metrics(job: str = "mini_swe_agent"):
    if not METRICS_ENABLED:
        return
    try:
        push_to_gateway(PUSHGATEWAY_URL, job=job, registry=REGISTRY)
    except Exception as e:
        logging.getLogger("minisweagent").warning(f"Failed to push metrics: {e}")


def init_metrics(model_name: str, model_type: str, env_type: str):
    if not METRICS_ENABLED:
        return
    AGENT_INFO.info({"model_name": model_name, "model_type": model_type, "env_type": env_type})


def record_model_call(model_name: str, model_type: str):
    if not METRICS_ENABLED:
        return
    MODEL_CALLS_TOTAL.labels(model_name=model_name, model_type=model_type).inc()


def record_tool_call(env_type: str, command_prefix: str, status: str, output_bytes: int):
    if not METRICS_ENABLED:
        return
    TOOL_CALLS_TOTAL.labels(env_type=env_type, command_prefix=command_prefix, status=status).inc()
    TOOL_CALL_OUTPUT_BYTES.labels(env_type=env_type, command_prefix=command_prefix).observe(output_bytes)


def record_step_tool_count(model_name: str, count: int):
    if not METRICS_ENABLED:
        return
    TOOL_CALLS_PER_STEP.labels(model_name=model_name).observe(count)
