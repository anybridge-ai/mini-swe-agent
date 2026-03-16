# Tool Calling 메커니즘과 LitellmTextbasedModel 전환 배경

## 목차

1. [LLM이 명령을 실행하는 두 가지 방식](#1-llm이-명령을-실행하는-두-가지-방식)
2. [LitellmModel (기본값, Tool Call 기반)](#2-litellmmodel-기본값-tool-call-기반)
3. [Qwen2.5-32B + vLLM 환경에서의 문제점](#3-qwen25-32b--vllm-환경에서의-문제점)
4. [LitellmTextbasedModel (해결책)](#4-litellmtextbasedmodel-해결책)
5. [push_metrics() try/finally 수정](#5-push_metrics-tryfinally-수정)
6. [모델 클래스 선택 가이드](#6-모델-클래스-선택-가이드)

---

## 1. LLM이 명령을 실행하는 두 가지 방식

mini-swe-agent에서 LLM이 bash 명령을 실행하려면, LLM의 출력에서 명령어를 추출하는 과정이 필요하다. 이 추출 방식에는 크게 두 가지가 있다.

### 1-1. Tool Calling (구조화된 방식)

LLM API가 **구조화된 JSON**으로 tool call을 반환하는 방식이다. OpenAI, Anthropic 등 주요 API 제공사가 지원한다.

- API 요청 시 `tools` 파라미터에 사용 가능한 도구(예: bash)를 정의하여 전송
- LLM이 도구를 사용해야 한다고 판단하면, 응답의 `tool_calls` 필드에 구조화된 JSON을 반환
- agent는 이 JSON을 파싱하여 명령어를 추출

```
[요청]                          [응답]
tools=[{                        tool_calls: [{
  name: "bash",                   function: {
  parameters: {                     name: "bash",
    command: string                 arguments: '{"command": "ls -la"}'
  }                               },
}]                                id: "call_abc123"
                                }]
```

### 1-2. Text-based (정규식 추출 방식)

LLM이 **일반 텍스트**를 출력하고, agent가 정규식(regex)으로 명령어를 추출하는 방식이다.

- API 요청 시 `tools` 파라미터를 보내지 않음
- 시스템 프롬프트에서 모델에게 특정 코드 블록 형식으로 명령어를 감싸도록 지시
- agent가 정규식 패턴 매칭으로 코드 블록 안의 명령어를 추출

```
[LLM 출력 텍스트]
파일 목록을 확인하겠습니다.

```mswea_bash_command
ls -la
```                              → 정규식으로 "ls -la" 추출
```

---

## 2. LitellmModel (기본값, Tool Call 기반)

`LitellmModel`은 mini-swe-agent의 기본 모델 클래스이다. OpenAI 스타일의 Tool Calling API를 사용한다.

**소스 파일**: `src/minisweagent/models/litellm_model.py`

### 2-1. 동작 흐름

```
LitellmModel._query()
    |
    |  litellm.completion(
    |      model=...,
    |      messages=...,
    |      tools=[BASH_TOOL],     <-- 매 API 호출마다 tools 전송
    |      **model_kwargs
    |  )
    |
    v
LitellmModel._parse_actions()
    |
    |  response.choices[0].message.tool_calls
    |      |
    |      +-- tool_calls가 있는 경우 → parse_toolcall_actions() → [{"command": "...", "tool_call_id": "..."}]
    |      +-- tool_calls가 비어있는 경우 → FormatError 발생
    |
    v
actions 리스트 반환 → agent.execute_actions()에서 env.execute() 호출
```

### 2-2. BASH_TOOL 정의

`src/minisweagent/models/utils/actions_toolcall.py`에 정의된 도구 스키마:

```python
BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Execute a bash command",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute",
                }
            },
            "required": ["command"],
        },
    },
}
```

### 2-3. Tool Call 파싱 (`parse_toolcall_actions`)

`actions_toolcall.py`의 `parse_toolcall_actions()` 함수는 다음 경우에 `FormatError`를 발생시킨다:

| 조건 | 에러 메시지 |
|------|-----------|
| `tool_calls`가 빈 리스트 | "No tool calls found in the response." |
| `function.name`이 "bash"가 아님 | "Unknown tool '...'" |
| arguments JSON 파싱 실패 | "Error parsing tool call arguments: ..." |
| `command` 키 누락 | "Missing 'command' argument in bash tool call." |

`FormatError`는 `InterruptAgentFlow`의 하위 클래스이다. 이 예외가 발생하면 `DefaultAgent.run()`의 while 루프에서 catch되어 사용자 메시지로 변환되고, `execute_actions()`는 **실행되지 않는다**.

### 2-4. FormatError 발생 시 agent 흐름

```
DefaultAgent.run()
    |
    while True:
        try:
            self.step()
                |
                self.query()
                    |
                    model.query(messages)
                        |
                        _parse_actions() → FormatError 발생!
                        |                     |
                        |   FormatError는 InterruptAgentFlow의 하위 클래스
                        |   → step()을 빠져나감
                        |   → execute_actions()는 실행되지 않음
                        |
                self.execute_actions()  ← 여기까지 도달하지 못함
                |
        except InterruptAgentFlow as e:
            self.add_messages(*e.messages)   ← FormatError의 메시지가 추가됨
            # (모델에게 "tool call을 사용하라"는 user 메시지)
```

---

## 3. Qwen2.5-32B + vLLM 환경에서의 문제점

### 3-1. 문제 상황

Qwen2.5-32B-Instruct를 vLLM으로 서빙할 때, tool calling이 제대로 작동하지 않는다.

### 3-2. 시나리오별 분석

#### 시나리오 A: `drop_params: True` 설정 시

```
litellm.completion(
    model="openai/qwen2.5-32b",
    messages=...,
    tools=[BASH_TOOL],        ← drop_params에 의해 제거됨
    drop_params=True
)
```

- litellm의 `drop_params: True` 설정으로 인해 `tools` 파라미터가 API 요청에서 제거됨
- 모델은 도구의 존재를 알지 못하고 일반 텍스트만 생성
- 응답에 `tool_calls`가 없음 → `parse_toolcall_actions()`에서 `FormatError` 발생

#### 시나리오 B: `drop_params` 없이 전송 시

```
litellm.completion(
    model="openai/qwen2.5-32b",
    messages=...,
    tools=[BASH_TOOL]         ← vLLM이 수신하지만...
)
```

- vLLM이 `tools` 파라미터를 수신하고 오류 없이 처리
- 그러나 Qwen2.5-32B가 `tool_calls` 형식의 응답을 **안정적으로 생성하지 못함**
- 일반 텍스트 응답만 생성 → `tool_calls`가 없음 → 동일하게 `FormatError` 발생

### 3-3. 결과

어느 시나리오든 매 step마다 동일한 실패 패턴이 반복된다:

```
[매 step 반복]
query() → model이 텍스트 응답 반환 → tool_calls 없음 → FormatError
    → InterruptAgentFlow → "tool call을 사용하라"는 메시지 추가
    → execute_actions() 실행 안 됨 → tool.execute 스팬 없음

트레이스:
step 1: agent.query → FormatError (tool.execute 스팬 없음)
step 2: agent.query → FormatError (tool.execute 스팬 없음)
step 3: agent.query → FormatError (tool.execute 스팬 없음)
...무한 반복, 아무 명령도 실행되지 않음
```

---

## 4. LitellmTextbasedModel (해결책)

`LitellmTextbasedModel`은 Tool Calling API를 사용하지 않고, 텍스트 기반 정규식 추출로 명령어를 파싱하는 모델 클래스이다.

**소스 파일**: `src/minisweagent/models/litellm_textbased_model.py`

### 4-1. 핵심 차이점

`LitellmTextbasedModel`은 `LitellmModel`을 상속하면서 두 가지 메서드를 오버라이드한다:

#### `_query()`: tools 파라미터 제거

```python
# LitellmModel (기본)
def _query(self, messages, **kwargs):
    return litellm.completion(
        model=..., messages=...,
        tools=[BASH_TOOL],          # <-- tools 전송
        **model_kwargs
    )

# LitellmTextbasedModel (텍스트 기반)
def _query(self, messages, **kwargs):
    return litellm.completion(
        model=..., messages=...,
                                     # <-- tools 없음!
        **model_kwargs
    )
```

#### `_parse_actions()`: 정규식 기반 파싱

```python
# LitellmModel (기본)
def _parse_actions(self, response):
    tool_calls = response.choices[0].message.tool_calls or []
    return parse_toolcall_actions(tool_calls, ...)     # tool_calls JSON 파싱

# LitellmTextbasedModel (텍스트 기반)
def _parse_actions(self, response):
    content = response.choices[0].message.content or ""
    return parse_regex_actions(content, ...)            # 정규식으로 텍스트에서 추출
```

### 4-2. 정규식 패턴

```python
action_regex = r"```mswea_bash_command\s*\n(.*?)\n```"
```

이 정규식은 다음 형식의 코드 블록에서 명령어를 추출한다:

```
```mswea_bash_command
ls -la
```
```

시스템 프롬프트에서 모델에게 이 형식을 사용하도록 지시하면, 모델은 일반 텍스트 출력에 코드 블록을 포함시킨다.

### 4-3. 텍스트 기반 파싱의 FormatError 조건

`parse_regex_actions()`는 **정확히 1개의 액션**을 기대한다:

| 조건 | 결과 |
|------|------|
| 코드 블록 0개 | FormatError: "Expected exactly 1 action, found 0." |
| 코드 블록 1개 | 정상: `[{"command": "..."}]` 반환 |
| 코드 블록 2개 이상 | FormatError: "Expected exactly 1 action, found N." |

### 4-4. 동작 흐름

```
LitellmTextbasedModel._query()
    |
    |  litellm.completion(
    |      model=...,
    |      messages=...,             <-- tools 파라미터 없음
    |      drop_params=True,         <-- 미지원 파라미터 자동 제거
    |      **model_kwargs
    |  )
    |
    v
LitellmTextbasedModel._parse_actions()
    |
    |  content = response.choices[0].message.content
    |  re.findall(r"```mswea_bash_command\s*\n(.*?)\n```", content, re.DOTALL)
    |      |
    |      +-- 1개 매칭 → [{"command": "ls -la"}] → 정상 실행
    |      +-- 0개 또는 2개 이상 → FormatError
    |
    v
actions 리스트 반환 → agent.execute_actions()에서 env.execute() 호출
```

### 4-5. Observation 메시지 형식의 차이

Tool Call 기반에서는 observation이 `role: "tool"` + `tool_call_id`로 전송되지만, 텍스트 기반에서는 `role: "user"` 메시지로 전송된다.

```
# Tool Call 기반 (actions_toolcall.py)
{"role": "tool", "tool_call_id": "call_abc123", "content": "<output>...</output>"}

# 텍스트 기반 (actions_text.py)
{"role": "user", "content": "<output>...</output>"}
```

이는 Tool Calling API를 사용하지 않는 모델에서는 `tool` role이나 `tool_call_id`가 필요 없기 때문이다.

### 4-6. 설정 예시

```yaml
model:
  class: minisweagent.models.litellm_textbased_model.LitellmTextbasedModel
  model_name: openai/qwen2.5-32b-instruct
  model_kwargs:
    api_base: http://localhost:8000/v1
    drop_params: true
```

`drop_params: True`는 vLLM이 지원하지 않는 파라미터(예: `cache_control`)가 전달될 경우 자동으로 제거되도록 보장한다.

---

## 5. push_metrics() try/finally 수정

### 5-1. 원래 코드의 문제

`DefaultAgent.step()`에서 `push_metrics()`가 step 본문의 마지막에 호출되었다. `query()` 단계에서 `FormatError`/`InterruptAgentFlow`가 발생하면, 예외가 `step()`을 빠져나가면서 `push_metrics()`에 도달하지 못했다.

```python
# 수정 전 (문제 있는 코드)
def step(self):
    with start_span("agent.step", ...):
        with track_duration(STEP_DURATION, ...):
            query_result = self.query()        # <-- 여기서 FormatError 발생 시
            result = self.execute_actions(...)  #     여기 이하 실행 안 됨
    push_metrics()                              # <-- 도달 불가!
    return result
```

### 5-2. 수정 내용

`step()` 본문 전체를 `try/finally`로 감싸고, `push_metrics()`를 `finally` 블록에 배치했다.

```python
# 수정 후 (현재 코드)
def step(self):
    try:
        with start_span("agent.step", ...):
            with track_duration(STEP_DURATION, ...):
                query_result = self.query()        # FormatError 발생해도
                result = self.execute_actions(...)
            return result
    finally:
        push_metrics()                              # <-- 항상 실행됨
```

### 5-3. 효과

| 상황 | 수정 전 | 수정 후 |
|------|--------|--------|
| 정상 step (query + execute 성공) | push_metrics() 호출됨 | push_metrics() 호출됨 |
| query()에서 FormatError 발생 | push_metrics() 호출 안 됨 | push_metrics() 호출됨 |
| execute_actions()에서 예외 발생 | push_metrics() 호출 안 됨 | push_metrics() 호출됨 |
| LimitsExceeded 발생 | push_metrics() 호출 안 됨 | push_metrics() 호출됨 |

이를 통해 FormatError가 반복되는 상황에서도 Prometheus 메트릭(query_duration, step_duration 등)이 누락 없이 수집된다.

---

## 6. 모델 클래스 선택 가이드

### 비교 표

| 항목 | LitellmModel | LitellmTextbasedModel |
|------|-------------|----------------------|
| **소스 파일** | `models/litellm_model.py` | `models/litellm_textbased_model.py` |
| **API 방식** | Tool Calling API (`tools` 파라미터 전송) | 일반 텍스트 API (`tools` 없음) |
| **명령 추출 방식** | `tool_calls` JSON 파싱 | 정규식 패턴 매칭 |
| **파싱 함수** | `parse_toolcall_actions()` | `parse_regex_actions()` |
| **파싱 소스** | `actions_toolcall.py` | `actions_text.py` |
| **Observation role** | `"tool"` (with `tool_call_id`) | `"user"` |
| **FormatError 조건** | tool_calls 없음, 알 수 없는 도구, 잘못된 인자 | 코드 블록이 정확히 1개가 아닌 경우 |
| **모델 요구사항** | 안정적인 tool calling 지원 필요 | 코드 블록 지시를 따를 수 있으면 충분 |

### 모델별 권장 클래스

| 모델 | 환경 | 권장 클래스 | 이유 |
|------|------|-----------|------|
| Claude (Anthropic) | API | `LitellmModel` | 안정적인 tool calling 지원 |
| GPT-4, GPT-4o (OpenAI) | API | `LitellmModel` | 안정적인 tool calling 지원 |
| Qwen2.5-32B-Instruct | vLLM | `LitellmTextbasedModel` | tool calling이 불안정 |
| 기타 로컬 모델 | vLLM/Ollama | `LitellmTextbasedModel` | tool calling 미지원 가능성 |

### 전체 흐름 비교

```
=== LitellmModel (Tool Call 기반) ===

DefaultAgent.step()
    |
    +-> query()
    |     +-> LitellmModel.query(messages)
    |           +-> _query(): litellm.completion(tools=[BASH_TOOL])
    |           +-> _parse_actions(): tool_calls JSON 파싱
    |           +-> 반환: {"role": "assistant", "tool_calls": [...], "extra": {"actions": [{"command": "..."}]}}
    |
    +-> execute_actions()
    |     +-> env.execute({"command": "..."})
    |     +-> format_toolcall_observation_messages()
    |     +-> 반환: [{"role": "tool", "tool_call_id": "...", "content": "<output>...</output>"}]
    |
    +-> (finally) push_metrics()


=== LitellmTextbasedModel (텍스트 기반) ===

DefaultAgent.step()
    |
    +-> query()
    |     +-> LitellmTextbasedModel.query(messages)
    |           +-> _query(): litellm.completion()  (tools 없음)
    |           +-> _parse_actions(): 정규식으로 ```mswea_bash_command``` 블록 추출
    |           +-> 반환: {"role": "assistant", "content": "...", "extra": {"actions": [{"command": "..."}]}}
    |
    +-> execute_actions()
    |     +-> env.execute({"command": "..."})
    |     +-> format_observation_messages()
    |     +-> 반환: [{"role": "user", "content": "<output>...</output>"}]
    |
    +-> (finally) push_metrics()
```

### 판단 기준

모델 클래스 선택은 간단하다:

1. 모델이 **Tool Calling API를 안정적으로 지원**하는가?
   - Yes -> `LitellmModel` 사용
   - No -> `LitellmTextbasedModel` 사용

2. 확인 방법: 해당 모델로 `LitellmModel`을 사용해 1-2회 step을 실행해 본다.
   - `tool.execute` 스팬이 트레이스에 나타나면 tool calling이 정상 작동하는 것이다.
   - 매 step마다 `FormatError` -> `InterruptAgentFlow`만 반복되고 `tool.execute` 스팬이 없으면, tool calling이 작동하지 않는 것이므로 `LitellmTextbasedModel`로 전환해야 한다.
