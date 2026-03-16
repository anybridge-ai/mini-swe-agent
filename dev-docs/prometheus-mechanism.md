# Prometheus Metrics Pipeline 동작 원리

mini-swe-agent의 Prometheus 메트릭 파이프라인이 어떻게 동작하는지 상세히 설명한다.

---

## 1. 전체 아키텍처

메트릭 파이프라인은 4개의 컴포넌트로 구성된다.

```
+---------------------+          +------------------+          +--------------------+          +-----------+
|   Agent Process     |  HTTP    |  Prometheus      |  scrape  |  Prometheus        |  PromQL  |  Grafana  |
|   (DefaultAgent)    |  PUT     |  Push Gateway    | -------> |  Server            | <------- |           |
|                     | -------> |  :9091           |  (5s)    |  :9090             |  query   |  :3000    |
|  - metrics.py가     |          |                  |          |                    |          |           |
|    메트릭 수집      |          |  최신 push 상태  |          |  시계열 데이터     |          |  대시보드 |
|  - push_metrics()로 |          |  보관 (버퍼)     |          |  영구 저장         |          |  시각화   |
|    일괄 전송        |          |                  |          |                    |          |           |
+---------------------+          +------------------+          +--------------------+          +-----------+
```

### Push Gateway를 사용하는 이유 (Push Model vs Pull Model)

일반적인 Prometheus 운영에서는 Prometheus Server가 대상 서버를 주기적으로 scrape(pull)하는 모델을 사용한다. 그러나 mini-swe-agent는 **short-lived batch job**이다. SWE-bench 인스턴스 하나를 처리하고 프로세스가 종료되는 구조이므로, scrape 기반 접근에는 근본적인 문제가 있다.

- Prometheus의 scrape interval이 15초라면, 10초 만에 끝나는 agent run의 메트릭은 한 번도 수집되지 못한다.
- 프로세스가 종료되면 `/metrics` 엔드포인트 자체가 사라지므로 scrape 대상이 없어진다.
- agent 프로세스마다 HTTP 서버를 띄워서 scrape를 기다리는 것은 batch job에 어울리지 않는다.

Push Gateway는 이 문제를 해결한다. Agent가 능동적으로 메트릭을 Push Gateway에 밀어넣고(push), Push Gateway가 그 상태를 유지하면서 Prometheus Server의 scrape를 받아준다. Agent 프로세스가 종료된 후에도 Push Gateway에 메트릭이 남아 있으므로 Prometheus가 수집할 수 있다.

---

## 2. metrics.py에서 메트릭을 정의하는 방법

메트릭 정의는 `src/minisweagent/utils/metrics.py`에 있다.

### 커스텀 CollectorRegistry 사용

```python
REGISTRY = CollectorRegistry()
```

`prometheus_client`의 기본 전역 Registry(`REGISTRY`)를 사용하지 않고 별도의 `CollectorRegistry` 인스턴스를 만든다. 이유는 다음과 같다.

- 같은 프로세스에서 다른 라이브러리(예: litellm)가 전역 Registry에 메트릭을 등록할 수 있다. 전역 Registry를 공유하면 메트릭 이름 충돌이 발생하거나, push 시 의도하지 않은 메트릭까지 함께 전송된다.
- 커스텀 Registry를 사용하면 `push_to_gateway()` 호출 시 mini-swe-agent의 메트릭만 정확히 전송된다.

### 메트릭 타입

| 타입 | 메트릭 이름 | 용도 | 레이블 |
|------|-------------|------|--------|
| **Histogram** | `mswea_step_duration_seconds` | step 전체 소요 시간 분포 | `model_name`, `env_type` |
| **Histogram** | `mswea_query_duration_seconds` | LLM API call latency 분포 | `model_name`, `model_type` |
| **Histogram** | `mswea_execute_actions_duration_seconds` | step당 전체 tool 실행 시간 | `env_type`, `model_name` |
| **Histogram** | `mswea_tool_call_duration_seconds` | 개별 tool call 실행 시간 | `env_type`, `command_prefix` |
| **Histogram** | `mswea_tool_call_output_bytes` | tool output 크기 분포 | `env_type`, `command_prefix` |
| **Histogram** | `mswea_tool_calls_per_step` | step당 tool call 수 분포 | `model_name` |
| **Counter** | `mswea_model_calls_total` | 모델 API 호출 누적 횟수 | `model_name`, `model_type` |
| **Counter** | `mswea_tool_calls_total` | tool 호출 누적 횟수 | `env_type`, `command_prefix`, `status` |
| **Gauge** | `mswea_current_step` | 현재 step 번호 | `agent_id` |
| **Info** | `mswea_agent` | agent 설정 메타데이터 | (info는 key-value 쌍으로 저장) |

각 타입의 특성:

- **Histogram**: 값의 분포를 기록한다. 내부적으로 `_bucket`, `_sum`, `_count` 세 가지 시계열을 생성한다. latency 측정에 적합하며 `histogram_quantile()`로 p50, p95, p99 등을 계산할 수 있다.
- **Counter**: 단조 증가하는 누적 값이다. `rate()`로 초당 변화율을 구할 수 있다.
- **Gauge**: 현재 상태 값을 나타낸다. 증가와 감소 모두 가능하다.
- **Info**: 문자열 key-value 메타데이터를 저장한다. 내부적으로는 값이 항상 1인 Gauge로 구현된다.

### 레이블(Labels)

레이블은 메트릭을 다차원으로 분류할 수 있게 한다. 예를 들어 `mswea_tool_calls_total`에 `command_prefix="git"`, `status="success"` 레이블이 붙으면, PromQL에서 `mswea_tool_calls_total{command_prefix="git", status="failure"}`처럼 특정 조건의 tool call만 필터링하거나, `sum by (command_prefix)`로 명령어별 그룹 집계가 가능하다.

### 초기화 시점

모든 메트릭은 **모듈 레벨 전역 변수**로 선언되며, `metrics.py`가 import될 때 초기화된다. `MSWEA_PUSHGATEWAY_URL` 환경변수가 설정되어 있으면 실제 메트릭 객체가 생성되고, 없으면 모두 `None`으로 설정된다.

```python
PUSHGATEWAY_URL = os.getenv("MSWEA_PUSHGATEWAY_URL", "")
METRICS_ENABLED = bool(PUSHGATEWAY_URL)

if METRICS_ENABLED:
    REGISTRY = CollectorRegistry()
    STEP_DURATION = Histogram(...)
    # ...
else:
    REGISTRY = None
    STEP_DURATION = QUERY_DURATION = MODEL_CALLS_TOTAL = None
    # ...
```

---

## 3. Agent 실행 중 메트릭 기록 방식

### track_duration 컨텍스트 매니저

```python
@contextlib.contextmanager
def track_duration(histogram, labels: dict):
    if not METRICS_ENABLED:
        yield
        return
    start = time.perf_counter()
    try:
        yield
    finally:
        histogram.labels(**labels).observe(time.perf_counter() - start)
```

`time.perf_counter()`로 wall clock 시간을 측정한다. `with` 블록 진입 시 시작 시각을 기록하고, 블록을 빠져나올 때(정상/예외 모두) 경과 시간을 `histogram.labels(**labels).observe(duration)`으로 기록한다. `observe()`는 해당 duration 값을 Histogram의 적절한 bucket에 분류하고, `_sum`과 `_count`를 갱신한다.

### record 함수들

- `record_model_call(model_name, model_type)`: `MODEL_CALLS_TOTAL` Counter를 1 증가시킨다.
- `record_tool_call(env_type, command_prefix, status, output_bytes)`: `TOOL_CALLS_TOTAL` Counter를 1 증가시키고, `TOOL_CALL_OUTPUT_BYTES` Histogram에 output 크기를 기록한다.
- `record_step_tool_count(model_name, count)`: `TOOL_CALLS_PER_STEP` Histogram에 해당 step의 tool call 수를 기록한다.

### DefaultAgent에서의 호출 위치

`src/minisweagent/agents/default.py`에서 메트릭이 기록되는 흐름은 다음과 같다.

```
DefaultAgent.step()
  |
  |-- CURRENT_STEP.labels(agent_id=...).set(n_calls)    # 현재 step 번호 갱신
  |
  |-- track_duration(STEP_DURATION, ...)                 # step 전체 시간 측정 시작
  |     |
  |     |-- self.query()
  |     |     |-- track_duration(QUERY_DURATION, ...)    # LLM call 시간 측정
  |     |     |     |-- self.model.query(messages)       # 실제 LLM API 호출
  |     |     |-- record_model_call(...)                 # 모델 호출 카운트 +1
  |     |
  |     |-- self.execute_actions(query_result)
  |           |-- track_duration(EXECUTE_ACTIONS_DURATION, ...)  # 전체 tool 실행 시간
  |           |     |-- for action in actions:
  |           |           |-- track_duration(TOOL_CALL_DURATION, ...)  # 개별 tool 시간
  |           |           |     |-- self.env.execute(action)          # 실제 명령 실행
  |           |           |-- record_tool_call(...)                   # tool 카운트/크기 기록
  |           |-- record_step_tool_count(...)             # step당 tool 수 기록
  |
  |-- finally: push_metrics()                            # 메트릭을 Push Gateway로 전송
```

모든 메트릭은 프로세스 내의 `REGISTRY` 객체에 누적된다. Histogram의 bucket 카운트, Counter의 누적 값 등이 메모리에 쌓이며, 아직 외부로 전송되지는 않는다.

---

## 4. push_to_gateway의 동작 방식

### 호출 시점

`push_metrics()`는 `DefaultAgent.step()`의 `finally` 블록에서 호출된다. `finally`이므로 step 실행 중 예외가 발생하더라도 반드시 실행된다. 즉, 매 step 종료 시마다 메트릭이 Push Gateway로 전송된다.

```python
def step(self) -> list[dict]:
    try:
        with track_duration(STEP_DURATION, ...):
            query_result = self.query()
            result = self.execute_actions(query_result)
        return result
    finally:
        push_metrics()
```

### push_metrics 내부 동작

```python
def push_metrics(job: str = "mini_swe_agent"):
    if not METRICS_ENABLED:
        return
    try:
        push_to_gateway(PUSHGATEWAY_URL, job=job, registry=REGISTRY)
    except Exception as e:
        logging.getLogger("minisweagent").warning(f"Failed to push metrics: {e}")
```

`push_to_gateway()`는 다음과 같이 동작한다.

1. `REGISTRY`에 등록된 모든 메트릭 객체를 순회하며 **Prometheus text exposition format**으로 직렬화한다. 예시:

   ```
   # HELP mswea_step_duration_seconds Step total duration
   # TYPE mswea_step_duration_seconds histogram
   mswea_step_duration_seconds_bucket{env_type="LocalEnvironment",model_name="gpt-4",le="0.005"} 0.0
   mswea_step_duration_seconds_bucket{env_type="LocalEnvironment",model_name="gpt-4",le="0.01"} 0.0
   ...
   mswea_step_duration_seconds_sum{env_type="LocalEnvironment",model_name="gpt-4"} 12.345
   mswea_step_duration_seconds_count{env_type="LocalEnvironment",model_name="gpt-4"} 3.0
   ```

2. 직렬화된 텍스트를 **HTTP PUT** 요청으로 Push Gateway의 `/metrics/job/mini_swe_agent` 엔드포인트에 전송한다.

3. Push Gateway는 해당 `job` 레이블에 대해 기존에 저장된 메트릭을 **전체 교체(replace)**한다. PUT이므로 해당 job의 모든 메트릭이 새로 받은 내용으로 덮어씌워진다.

### Push Gateway의 역할

Push Gateway는 **버퍼/캐시**이다.

- 시간에 따른 집계(aggregation)를 수행하지 않는다.
- 마지막으로 push된 상태를 그대로 보관할 뿐이다.
- agent 프로세스가 종료되어도 Push Gateway에 메트릭이 남아 있으므로 Prometheus가 수집할 수 있다.
- 같은 job 이름으로 다시 push하면 이전 값이 완전히 교체된다.

Push 실패 시 예외를 catch하고 warning 로그만 남긴다. 메트릭 전송 실패가 agent 실행을 중단시키지 않는다.

---

## 5. Prometheus Server가 Push Gateway를 scrape하는 방식

Prometheus Server의 `prometheus.yml` 설정에서 Push Gateway를 scrape 대상으로 등록한다.

```yaml
scrape_configs:
  - job_name: "pushgateway"
    scrape_interval: 5s
    honor_labels: true
    static_configs:
      - targets: ["pushgateway:9091"]
```

### 동작 방식

- **`scrape_interval: 5s`**: Prometheus가 5초마다 Push Gateway의 `/metrics` 엔드포인트를 HTTP GET으로 호출한다.
- **`honor_labels: true`**: Push Gateway를 통해 들어온 메트릭의 원본 레이블(`job`, `model_name` 등)을 그대로 유지한다. 이 설정이 없으면 Prometheus가 자체적으로 `job="pushgateway"` 레이블을 덮어씌워서 원래 agent 코드에서 설정한 레이블이 사라진다.
- Prometheus는 scrape한 데이터를 **타임스탬프가 붙은 시계열(time series)**로 저장한다. 이것이 Push Gateway와의 핵심적 차이다. Push Gateway는 최신 상태만 보관하지만, Prometheus는 매 scrape마다의 값을 시간축 위에 누적한다.

### 시계열 저장 이후

데이터가 Prometheus에 저장되면 PromQL로 시간 범위에 걸친 쿼리가 가능해진다.

- `rate(mswea_model_calls_total[5m])`: 최근 5분간 초당 모델 호출 비율
- `histogram_quantile(0.95, rate(mswea_query_duration_seconds_bucket[10m]))`: LLM call latency의 p95
- `increase(mswea_tool_calls_total{status="failure"}[1h])`: 최근 1시간 동안 실패한 tool call 수

---

## 6. Grafana에서 Prometheus 데이터를 읽는 방식

Grafana는 Prometheus를 데이터 소스로 등록하여 시각화한다.

### 데이터 흐름

1. Grafana 대시보드의 각 패널에 **PromQL 쿼리**가 설정되어 있다.
2. 패널이 렌더링될 때 Grafana가 Prometheus Server의 HTTP API(`/api/v1/query_range` 등)에 PromQL 쿼리를 전송한다.
3. Prometheus가 저장된 시계열 데이터에서 쿼리를 실행하고 결과를 반환한다.
4. Grafana가 결과를 그래프, 테이블, 히트맵 등으로 시각화한다.

### PromQL 쿼리 예시

| 목적 | PromQL |
|------|--------|
| 평균 LLM call latency | `mswea_query_duration_seconds_sum / mswea_query_duration_seconds_count` |
| LLM call latency p95 | `histogram_quantile(0.95, rate(mswea_query_duration_seconds_bucket[5m]))` |
| 평균 step duration | `mswea_step_duration_seconds_sum / mswea_step_duration_seconds_count` |
| 명령어별 tool call 횟수 | `sum by (command_prefix) (mswea_tool_calls_total)` |
| tool call 실패율 | `sum(mswea_tool_calls_total{status="failure"}) / sum(mswea_tool_calls_total)` |
| 모델별 초당 호출 비율 | `rate(mswea_model_calls_total[5m])` |

---

## 7. MSWEA_PUSHGATEWAY_URL이 설정되지 않은 경우

환경변수가 비어 있으면 메트릭 시스템 전체가 완전히 비활성화된다.

```python
PUSHGATEWAY_URL = os.getenv("MSWEA_PUSHGATEWAY_URL", "")
METRICS_ENABLED = bool(PUSHGATEWAY_URL)  # False
```

이 경우 동작:

- `prometheus_client` 패키지가 import되지 않는다. 설치되어 있지 않아도 에러가 발생하지 않는다.
- 모든 메트릭 객체(`STEP_DURATION`, `QUERY_DURATION`, `MODEL_CALLS_TOTAL` 등)가 `None`이다.
- `REGISTRY`도 `None`이다.

각 함수의 비활성 경로:

```python
# track_duration: 즉시 yield하고 반환. 시간 측정 없음.
def track_duration(histogram, labels):
    if not METRICS_ENABLED:
        yield          # with 블록 내부 코드는 정상 실행됨
        return         # observe() 호출 없이 종료

# record_* 함수들: 즉시 반환.
def record_model_call(model_name, model_type):
    if not METRICS_ENABLED:
        return         # Counter.inc() 호출 없음

# push_metrics: 즉시 반환.
def push_metrics(job="mini_swe_agent"):
    if not METRICS_ENABLED:
        return         # HTTP 요청 없음
```

결과적으로 agent 실행 경로에 **추가 오버헤드가 전혀 없다**. 각 함수 진입 시 `if not METRICS_ENABLED: return` 한 줄의 boolean 체크만 수행하고 즉시 반환한다. 타이머 시작, 메트릭 객체 접근, 네트워크 호출 등이 모두 건너뛰어진다.

---

## 전체 데이터 흐름 요약

```
[Agent 프로세스]
  |
  |  1) DefaultAgent.__init__()
  |     - init_metrics() -> AGENT_INFO에 모델/환경 메타데이터 기록
  |
  |  2) DefaultAgent.step() 실행 (매 step마다 반복)
  |     |
  |     |  CURRENT_STEP gauge 갱신
  |     |
  |     |  track_duration(STEP_DURATION)  ----+
  |     |    |                                |
  |     |    |  query()                       |
  |     |    |    track_duration(QUERY_DURATION)   --> Histogram.observe()
  |     |    |    record_model_call()              --> Counter.inc()
  |     |    |                                |
  |     |    |  execute_actions()              |
  |     |    |    track_duration(EXECUTE_ACTIONS_DURATION)
  |     |    |      for each action:           |
  |     |    |        track_duration(TOOL_CALL_DURATION) --> Histogram.observe()
  |     |    |        record_tool_call()                 --> Counter.inc() + Histogram.observe()
  |     |    |    record_step_tool_count()              --> Histogram.observe()
  |     |    |                                |
  |     |  <-- STEP_DURATION.observe() -------+
  |     |
  |     |  finally: push_metrics()
  |     |    |
  |     |    v
  |     |  REGISTRY의 모든 메트릭 -> Prometheus text format으로 직렬화
  |     |    |
  |     |    v
  |     |  HTTP PUT --> Push Gateway :9091 /metrics/job/mini_swe_agent
  |     |               (job별 메트릭 전체 교체)
  |
  |  3) step 반복 -> 누적된 메트릭이 매번 push됨
  |
  |  4) agent 종료 (프로세스 종료되어도 Push Gateway에 메트릭 잔존)
  |
  v

[Push Gateway :9091]
  |  최신 push 상태를 /metrics 엔드포인트로 노출
  |
  |  <-- Prometheus Server가 5초마다 scrape (HTTP GET /metrics)
  v

[Prometheus Server :9090]
  |  타임스탬프 붙인 시계열로 영구 저장
  |  PromQL 쿼리 엔진 제공
  |
  |  <-- Grafana가 PromQL 쿼리 전송 (HTTP /api/v1/query_range)
  v

[Grafana :3000]
     대시보드에서 그래프/테이블로 시각화
```
