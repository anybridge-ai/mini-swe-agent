# Full Stack 실행 가이드

mini-swe-agent로 SWE-bench를 돌리고 metrics/traces를 수집하기 위한 전체 컴포넌트 구성 가이드.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  mini-swe-agent (host)                                      │
│                                                             │
│  ┌──────────────┐   HTTP POST /execute                      │
│  │ SWE-bench     │──────────────────────► [swerex-server]   │
│  │ Runner        │                         Docker :8000     │
│  │ (N threads)   │                                          │
│  │               │   HTTP PUT /metrics/job/mswea            │
│  │               │──────────────────────► [Push Gateway]    │
│  │               │                         Docker :9091     │
│  │               │                              │           │
│  │               │   HTTP POST /v1/traces       │ scrape    │
│  │               │──────────────────────► [Tempo]│     ▼    │
│  │               │                       :4318  │ [Prometheus]
│  └──────────────┘                       :3200   │  (optional)
│                                           │     │           │
│        ┌──────────────────────────────────┘     │           │
│        ▼                                        ▼           │
│  [Grafana :3000]  ◄─── datasource: Tempo, Prometheus        │
│                                                             │
│  [vLLM Server]  ◄─── LLM inference (외부 또는 로컬)          │
│   :8066/v1                                                  │
└─────────────────────────────────────────────────────────────┘
```

총 5개 컴포넌트:

| # | 컴포넌트 | 역할 | 필수 여부 |
|---|----------|------|-----------|
| 1 | **vLLM Server** | LLM inference | 필수 (이미 실행 중이면 skip) |
| 2 | **swerex-server** | tool call 실행 환경 (Docker 컨테이너) | 필수 |
| 3 | **Prometheus Push Gateway** | metrics 수집 | 선택 |
| 4 | **Grafana Tempo** | distributed traces 수집 | 선택 |
| 5 | **Grafana** | 시각화 UI | 선택 |

---

## Prerequisites

```bash
# 1. Python venv 활성화
cd /home/hmchoi/mini-swe-agent
source .venv/bin/activate

# 2. observability 의존성 설치 (최초 1회)
uv pip install -e ".[observability]"

# 3. Docker 확인
docker --version
# 권한 문제 시 sg docker -c "..." 로 실행
```

---

## Step 1: vLLM Server

> 이미 공유 서버(`143.248.136.10:8066`)가 실행 중이면 이 단계 skip.

```bash
# 상태 확인
curl -s http://143.248.136.10:8066/v1/models | python3 -m json.tool
```

정상 응답이 오면 vLLM은 준비 완료. 응답이 없으면 vLLM 서버를 시작해야 함.

<details>
<summary>로컬 vLLM 서버 시작 (필요 시)</summary>

```bash
# GPU 서버에서 실행
python -m vllm.entrypoints.openai.api_server \
  --model Qwen/Qwen2.5-32B-Instruct \
  --port 8066 \
  --tensor-parallel-size 4
```

</details>

**확인:**
```bash
curl -s http://143.248.136.10:8066/v1/models
# {"data": [{"id": "Qwen/Qwen2.5-32B-Instruct", ...}]}
```

---

## Step 2: swerex-server (Tool Execution 환경)

persistent Docker 컨테이너에서 swerex HTTP 서버를 실행. 모든 agent가 이 서버로 tool call을 보냄.

### 2-1. Docker 이미지 빌드 (최초 1회)

```bash
sg docker -c "docker build -t swerex-server docker/swerex-server/"
```

### 2-2. 컨테이너 시작

```bash
sg docker -c "docker run -d \
  -p 8000:8000 \
  --name mswea-swerex \
  swerex-server \
  --auth-token test123"
```

### 2-3. 확인

```bash
# 서버가 뜰 때까지 1-2초 대기 후
curl -s http://localhost:8000/
# {"detail":"Not authenticated"}  ← 정상 (auth 필요하다는 뜻)
```

### SWE-bench용 instance별 swerex 서버

SWE-bench는 instance마다 다른 Docker 이미지(testbed)가 필요하므로, 범용 swerex-server 하나로는 부족함.
현재 SWE-bench runner(`swebench.py`)는 `environment_class: docker`(기본값)로 instance별 컨테이너를 생성함.

**`swerex_remote`가 유용한 경우:**
- concurrent_test.py 같은 **동일 환경에서 여러 agent 동시 실행**
- 개발/디버깅 중 환경을 재사용하고 싶을 때
- 컨테이너 startup 오버헤드를 없애고 싶을 때

**SWE-bench 실행 시에는** 기본 `docker` 또는 `swerex_docker` 환경을 그대로 사용.

---

## Step 3: Prometheus Push Gateway

agent step마다 metrics를 push하는 수신 서버.

```bash
sg docker -c "docker run -d \
  --name pushgateway \
  -p 9091:9091 \
  prom/pushgateway:latest"
```

**확인:**
```bash
curl -s http://localhost:9091/metrics | head -5
# HELP, TYPE 등 Prometheus 메트릭 포맷이 출력되면 정상
```

---

## Step 4: Grafana Tempo

OpenTelemetry traces를 수신/저장하는 tracing backend.

### 4-1. Tempo config 생성

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
    wal:
      path: /var/tempo/wal
    local:
      path: /var/tempo/blocks
EOF
```

### 4-2. Tempo 컨테이너 시작

```bash
sg docker -c "docker run -d \
  --name tempo \
  -p 3200:3200 \
  -p 4317:4317 \
  -p 4318:4318 \
  -v /tmp/tempo/tempo.yaml:/etc/tempo.yaml \
  grafana/tempo:latest \
  -config.file=/etc/tempo.yaml"
```

### 4-3. 확인

```bash
curl -s http://localhost:3200/ready
# ready
```

---

## Step 5: Grafana (시각화, 선택사항)

Tempo traces와 Prometheus metrics를 UI로 조회.

```bash
sg docker -c "docker run -d \
  --name grafana \
  -p 3000:3000 \
  -e GF_AUTH_ANONYMOUS_ENABLED=true \
  -e GF_AUTH_ANONYMOUS_ORG_ROLE=Admin \
  grafana/grafana:latest"
```

### Datasource 설정

1. http://localhost:3000 접속
2. **Connections > Data sources > Add data source**

**Tempo datasource:**
- Type: Tempo
- URL: `http://172.17.0.1:3200` (Linux Docker bridge IP)
- Save & test

**Prometheus datasource (Push Gateway 직접 조회 시):**
- Type: Prometheus
- URL: `http://172.17.0.1:9091`
- Save & test

---

## 실행

### 전체 health check (한 번에 확인)

```bash
echo "=== vLLM ===" && curl -sf http://143.248.136.10:8066/v1/models > /dev/null && echo "OK" || echo "FAIL"
echo "=== swerex ===" && curl -sf http://localhost:8000/ > /dev/null; [ $? -eq 22 ] && echo "OK" || echo "FAIL"
echo "=== pushgateway ===" && curl -sf http://localhost:9091/-/ready > /dev/null && echo "OK" || echo "FAIL"
echo "=== tempo ===" && curl -sf http://localhost:3200/ready && echo "" || echo "FAIL"
echo "=== grafana ===" && curl -sf http://localhost:3000/api/health > /dev/null && echo "OK" || echo "FAIL"
```

### concurrent_test.py (10 agents, 동일 환경)

```bash
sg docker -c "bash -c '
  source .venv/bin/activate && \
  OPENAI_API_KEY=EMPTY \
  MSWEA_PUSHGATEWAY_URL=http://localhost:9091 \
  MSWEA_OTLP_ENDPOINT=http://localhost:4318 \
  python /tmp/mswea-tempo/concurrent_test.py
'"
```

### SWE-bench 실행 (instance별 Docker 환경)

SWE-bench는 instance별로 전용 Docker 이미지를 사용하므로, `swerex_remote`가 아닌 기본 `docker` 환경을 사용.

```bash
source .venv/bin/activate

MSWEA_PUSHGATEWAY_URL=http://localhost:9091 \
MSWEA_OTLP_ENDPOINT=http://localhost:4318 \
OPENAI_API_KEY=EMPTY \
python -m minisweagent.run.benchmarks.swebench \
  --subset lite \
  --split dev \
  --slice "0:5" \
  -w 5 \
  -m "openai/Qwen/Qwen2.5-32B-Instruct" \
  -c swebench.yaml \
  -c model.model_kwargs.api_base=http://143.248.136.10:8066/v1 \
  -c model.model_kwargs.drop_params=true \
  -c model.model_kwargs.temperature=0.0 \
  -c model.cost_tracking=ignore_errors \
  -o ./results/run_001
```

주요 옵션:
- `-w 5`: 병렬 worker 수 (동시에 5개 instance 처리)
- `--slice "0:5"`: 처음 5개 instance만
- `cost_tracking=ignore_errors`: vLLM 로컬 모델은 비용 계산 불가, 에러 무시

---

## 결과 확인

### Prometheus Metrics

```bash
# 수집된 메트릭 조회
curl -s http://localhost:9091/metrics | grep mswea_

# 주요 메트릭
# mswea_step_duration_seconds       — step 전체 소요 시간
# mswea_query_duration_seconds      — LLM call latency
# mswea_tool_call_duration_seconds  — 개별 tool 실행 시간
# mswea_tool_calls_total            — tool 호출 횟수 (status별)
# mswea_model_calls_total           — model API 호출 횟수
```

### Grafana에서 Traces 조회

1. http://localhost:3000 > Explore > Tempo datasource
2. TraceQL 예시:

```
# 전체 agent run
{resource.service.name = "mini-swe-agent" && name = "agent.run"}

# 5초 넘는 tool call
{name = "tool.execute" && duration > 5s}

# 실패한 tool call
{name = "tool.execute" && span.status = "failure"}
```

### Tempo HTTP API로 직접 조회

```bash
# 최근 traces
curl -s "http://localhost:3200/api/search?limit=5" | python3 -m json.tool

# 특정 trace
curl -s "http://localhost:3200/api/traces/<trace_id>" | python3 -m json.tool
```

---

## 정리 (Teardown)

```bash
# 개별 정리
sg docker -c "docker stop mswea-swerex pushgateway tempo grafana"
sg docker -c "docker rm mswea-swerex pushgateway tempo grafana"

# Tempo 데이터 정리
rm -rf /tmp/tempo
```

모든 컴포넌트를 한 번에 확인하고 정리:

```bash
# 실행 중인 mswea 관련 컨테이너 확인
sg docker -c "docker ps --filter name=mswea --filter name=pushgateway --filter name=tempo --filter name=grafana --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'"
```

---

## 환경변수 요약

| 변수 | 용도 | 값 |
|------|------|-----|
| `OPENAI_API_KEY` | vLLM에 필요 (더미값 OK) | `EMPTY` |
| `MSWEA_PUSHGATEWAY_URL` | Prometheus Push Gateway | `http://localhost:9091` |
| `MSWEA_OTLP_ENDPOINT` | Grafana Tempo OTLP HTTP | `http://localhost:4318` |

두 `MSWEA_*` 변수 모두 미설정 시 observability 코드는 완전히 no-op (오버헤드 0).

---

## 포트 요약

| 포트 | 컴포넌트 | 프로토콜 |
|------|----------|----------|
| 8066 | vLLM | OpenAI-compatible API |
| 8000 | swerex-server | swerex HTTP API |
| 9091 | Push Gateway | Prometheus metrics push |
| 4318 | Tempo | OTLP HTTP (traces) |
| 4317 | Tempo | OTLP gRPC (미사용) |
| 3200 | Tempo | Tempo query API |
| 3000 | Grafana | Web UI |

---

## Troubleshooting

### "No such file or directory: '/workspace/agent_N'"
`SwerexRemoteEnvironment`는 `__init__`에서 `mkdir -p`로 cwd를 자동 생성함.
이 에러가 나면 `swerex_remote.py`가 최신 버전인지 확인.

### swerex 서버 연결 실패
```bash
# 컨테이너 로그 확인
sg docker -c "docker logs mswea-swerex"

# 포트 점유 확인
ss -tlnp | grep 8000
```

### Push Gateway에 메트릭이 안 보임
- `MSWEA_PUSHGATEWAY_URL` 환경변수가 설정되었는지 확인
- agent가 최소 1 step 이상 실행되어야 메트릭이 push됨

### Tempo에 traces가 안 보임
- `MSWEA_OTLP_ENDPOINT` 환경변수가 설정되었는지 확인
- Tempo는 traces를 받은 후 조회 가능까지 수 초 딜레이가 있을 수 있음
- `curl -s http://localhost:3200/ready` 로 Tempo 상태 확인
