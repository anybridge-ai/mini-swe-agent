# Observability Setup Guide

mini-swe-agent의 Prometheus metrics + OpenTelemetry traces를 로컬에서 수집하고 확인하는 방법.

## Prerequisites

```bash
# observability 의존성 설치 (venv 활성화 후)
source .venv/bin/activate
uv pip install -e ".[observability]"

# Docker 필요 (Prometheus Push Gateway, Grafana Tempo)
docker --version
```

---

## 1. 인프라 띄우기

### Prometheus Push Gateway

```bash
docker run -d \
  --name pushgateway \
  -p 9091:9091 \
  prom/pushgateway:latest
```

확인: `curl -s http://localhost:9091/metrics | head` 로 응답이 오면 정상.

### Grafana Tempo

Tempo는 config 파일이 필요합니다. 먼저 최소 설정 파일을 만듭니다:

```bash
mkdir -p /tmp/tempo && cat > /tmp/tempo/tempo.yaml << 'EOF'
server:
  http_listen_port: 3200

distributor:
  receivers:
    otlp:
      protocols:
        http:
          endpoint: "0.0.0.0:4318"
        grpc:
          endpoint: "0.0.0.0:4317"

storage:
  trace:
    backend: local
    local:
      path: /var/tempo/traces
    wal:
      path: /var/tempo/wal
EOF
```

```bash
docker run -d \
  --name tempo \
  -p 3200:3200 \
  -p 4317:4317 \
  -p 4318:4318 \
  -v /tmp/tempo/tempo.yaml:/etc/tempo.yaml \
  grafana/tempo:latest \
  -config.file=/etc/tempo.yaml
```

확인: `curl -s http://localhost:3200/ready` 가 `ready`를 반환하면 정상.

### Grafana (Tempo UI용, 선택사항)

Trace를 시각적으로 보려면 Grafana가 필요합니다:

```bash
docker run -d \
  --name grafana \
  -p 3000:3000 \
  -e GF_AUTH_ANONYMOUS_ENABLED=true \
  -e GF_AUTH_ANONYMOUS_ORG_ROLE=Admin \
  grafana/grafana:latest
```

Grafana에 Tempo datasource 추가:
1. http://localhost:3000 접속
2. Connections > Data sources > Add data source > Tempo
3. URL: `http://host.docker.internal:3200` (Linux에서는 `http://172.17.0.1:3200`)
4. Save & test

---

## 2. SWE-bench 실행

환경변수 두 개를 설정하면 observability가 활성화됩니다. 미설정 시 완전히 비활성화되며 기존 동작에 영향 없음.

### 기본 실행

```bash
source .venv/bin/activate

MSWEA_PUSHGATEWAY_URL=http://localhost:9091 \
MSWEA_OTLP_ENDPOINT=http://localhost:4318 \
python -m minisweagent.run.benchmarks.swebench \
  --subset lite \
  --split dev \
  --slice "0:5" \
  -m "openai/your-vllm-model" \
  -c swebench.yaml \
  -c model.model_kwargs.temperature=0.0 \
  -o ./results/run_001
```

### vLLM 백엔드 사용 시

litellm은 `openai/` prefix + `api_base` 설정으로 vLLM을 지원합니다:

```bash
MSWEA_PUSHGATEWAY_URL=http://localhost:9091 \
MSWEA_OTLP_ENDPOINT=http://localhost:4318 \
OPENAI_API_KEY=dummy \
OPENAI_API_BASE=http://localhost:8000/v1 \
python -m minisweagent.run.benchmarks.swebench \
  --subset lite \
  --split dev \
  --slice "0:5" \
  -m "openai/meta-llama/Llama-3.1-70B-Instruct" \
  -c swebench.yaml \
  -c model.cost_tracking=ignore_errors \
  -o ./results/run_001
```

> `model.cost_tracking=ignore_errors`: vLLM 로컬 모델은 litellm cost 계산이 안 되므로 에러 무시 필요.

### YAML config로 vLLM 설정 관리

반복 사용 시 config 파일로 분리하는 것이 편합니다:

```bash
cat > vllm_local.yaml << 'EOF'
model:
  model_name: "openai/meta-llama/Llama-3.1-70B-Instruct"
  model_kwargs:
    api_base: "http://localhost:8000/v1"
    api_key: "dummy"
    drop_params: true
    temperature: 0.0
  cost_tracking: "ignore_errors"
EOF
```

```bash
MSWEA_PUSHGATEWAY_URL=http://localhost:9091 \
MSWEA_OTLP_ENDPOINT=http://localhost:4318 \
python -m minisweagent.run.benchmarks.swebench \
  --subset lite --split dev --slice "0:5" \
  -c swebench.yaml -c vllm_local.yaml \
  -o ./results/run_001
```

### 둘 중 하나만 사용

```bash
# Prometheus만 (traces 없이)
MSWEA_PUSHGATEWAY_URL=http://localhost:9091 \
python -m minisweagent.run.benchmarks.swebench ...

# Traces만 (Prometheus 없이)
MSWEA_OTLP_ENDPOINT=http://localhost:4318 \
python -m minisweagent.run.benchmarks.swebench ...
```

---

## 3. 데이터 확인

### Prometheus 메트릭 확인

```bash
# Push Gateway에 쌓인 메트릭 조회
curl -s http://localhost:9091/metrics | grep mswea_
```

주요 메트릭:

| 메트릭 | 의미 |
|--------|------|
| `mswea_step_duration_seconds` | step 전체 소요 시간 분포 |
| `mswea_query_duration_seconds` | LLM call latency 분포 |
| `mswea_tool_call_duration_seconds` | 개별 tool 실행 시간 (command_prefix별) |
| `mswea_tool_calls_total` | tool 호출 횟수 (status별: success/failure/timeout) |
| `mswea_tool_calls_per_step` | step당 tool call 수 분포 |
| `mswea_model_calls_total` | model API 호출 횟수 |

예시 쿼리:

```bash
# 평균 step duration
curl -s http://localhost:9091/metrics | grep mswea_step_duration_seconds_sum
curl -s http://localhost:9091/metrics | grep mswea_step_duration_seconds_count
# sum / count = average

# 실패한 tool call 수
curl -s http://localhost:9091/metrics | grep 'mswea_tool_calls_total.*status="failure"'
```

### OpenTelemetry Traces 확인

**Tempo HTTP API로 직접 조회:**

```bash
# 최근 trace 목록 (Tempo search API)
curl -s "http://localhost:3200/api/search?limit=5" | python3 -m json.tool

# 특정 trace 조회 (trace_id는 위 결과에서 확인)
curl -s "http://localhost:3200/api/traces/<trace_id>" | python3 -m json.tool
```

**Grafana UI로 조회 (권장):**

1. http://localhost:3000 접속
2. 좌측 메뉴 > Explore
3. 상단 datasource를 Tempo로 선택
4. Search 탭에서:
   - Service Name: `mini-swe-agent`
   - Span Name: `agent.run`, `agent.step`, `tool.execute` 등
5. TraceQL 탭에서 직접 쿼리:

```
# 전체 agent run traces
{resource.service.name = "mini-swe-agent" && name = "agent.run"}

# 5초 넘는 tool call
{name = "tool.execute" && span.latency_seconds > 5}

# 실패한 tool call
{name = "tool.execute" && span.status = "failure"}

# 특정 명령어 찾기
{name = "tool.execute" && span.command_prefix = "find"}
```

---

## 4. 정리

```bash
docker stop pushgateway tempo grafana
docker rm pushgateway tempo grafana
rm -rf /tmp/tempo
```

---

## 환경변수 요약

| 변수 | 용도 | 기본값 |
|------|------|--------|
| `MSWEA_PUSHGATEWAY_URL` | Prometheus Push Gateway URL | `""` (비활성) |
| `MSWEA_OTLP_ENDPOINT` | Grafana Tempo OTLP HTTP endpoint | `""` (비활성) |

둘 다 미설정 시 observability 코드는 완전히 no-op.
