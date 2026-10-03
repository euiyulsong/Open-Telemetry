# OpenTelemetry Trace 전달 실패 실험 정리

## 1. 먼저 용어부터

### Collector가 뭐야?

`OpenTelemetry Collector`는 **애플리케이션이 만든 trace/log/metric을 받아서 최종 backend로 전달해주는 중간 서버**야.

예를 들면:

```text
Python App
   ↓
OpenTelemetry Collector
   ↓
Tempo / Jaeger / Datadog / Elastic
```

App이 직접 모든 observability backend와 통신하게 하지 않고, Collector가 중간에서 다음 역할을 해.

- telemetry 수신
- batch 처리
- retry
- queueing
- filtering / processing
- backend로 export

즉 **telemetry용 proxy + buffer + retry worker**라고 보면 돼.

---

### Queue가 뭐야?

Queue는 **아직 backend로 보내지 못한 trace를 잠시 저장하는 대기열**이야.

```text
Collector
   ↓
Queue
   ↓
Backend
```

Backend가 잠깐 죽으면:

```text
Collector
   ↓
Queue에 저장
   ↓
Backend 복구
   ↓
다시 전송
```

할 수 있어.

Queue는 크게 두 종류로 보면 돼.

| Queue | 특징 | Collector 죽으면 |
|---|---|---|
| Memory queue | RAM에 저장 | ❌ 유실 |
| Persistent queue | Disk에 저장 | ✅ restart 후 복구 가능 |

이번 실험에서는 `file_storage`를 사용해서 **persistent queue**를 만들었다.

---

# 2. 전체 구조

이번 실험 구조는 다음과 같다.

```text
Application
    │
    │ span 생성
    ▼
OpenTelemetry Python SDK
    │
    │ BatchSpanProcessor
    │ memory queue
    │ retry
    ▼
OpenTelemetry Collector
    │
    │ sending_queue
    │ retry_on_failure
    │ file_storage
    ▼
Trace Backend
```

각 계층마다 서로 다른 failure를 처리한다.

---

# 3. 언제 성공하고 언제 실패하는가?

가장 핵심만 먼저 보면 이렇다.

| 상황 | 결과 | 복구 가능? |
|---|---|---:|
| 모두 정상 | 정상 전송 | ✅ |
| Backend 잠깐 장애 | Collector queue + retry | ✅ |
| Backend 장애 중 Collector restart | persistent queue에서 복구 | ✅ |
| Backend 다시 살아남 | queue에 있던 데이터 재전송 | ✅ |
| Collector 잠깐 장애 | App SDK retry | 경우에 따라 ✅ |
| Collector 오래 장애 | App SDK queue 가득 참 | ❌ 일부 drop |
| Collector queue full | 신규 trace reject | ❌ reject된 데이터 |
| App SDK queue full | `Queue full, dropping Span` | ❌ |
| Persistent disk 손상/삭제 | 저장된 trace 유실 | ❌ |
| Queue에 들어가기 전에 process crash | 해당 trace 유실 가능 | ❌ |

---

# 4. 정상 상황

정상 상태에서는:

```text
App
 ↓
Collector
 ↓
Backend
```

로 바로 전달된다.

실험에서도:

```text
generated 20 spans
force_flush: True

[backend] path=/v1/traces ...
```

가 확인됐다. 붙여넣은 텍스트(1)

### 결과

**성공**

---

# 5. Backend가 잠깐 죽으면

예:

```text
App
 ↓
Collector
 ↓
Backend ❌
```

Collector는 바로 데이터를 버리지 않고:

```text
retry
+
queue
```

를 사용한다.

이번 실험에서도 Collector가:

```text
Exporting failed. Will retry the request after interval.
```

를 출력했다. 붙여넣은 텍스트(1)

### 결과

Backend가 금방 살아나면:

```text
Backend DOWN
 ↓
Collector queue
 ↓
retry
 ↓
Backend UP
 ↓
전송 성공
```

이므로 **복구 가능**.

---

# 6. Backend 장애 중 Collector까지 죽으면

여기서 persistent queue가 중요하다.

Memory queue만 있었다면:

```text
Collector RAM
 ↓
Collector crash
 ↓
데이터 유실
```

하지만 이번 설정에서는 disk에 저장했다.

```text
Collector
 ↓
file_storage
 ↓
Disk
```

Collector restart 후 실제로:

```text
Loaded queue metadata
itemsSize: 100
bytesSize: 11654
```

가 확인됐다. 붙여넣은 텍스트(1)

그리고 queue 데이터를 다시 dispatch했다. 붙여넣은 텍스트(1)

### 결과

```text
Backend DOWN
 ↓
Persistent Queue
 ↓
Collector restart
 ↓
Disk에서 queue 복구
```

**복구 가능**

---

# 7. Backend가 다시 살아나면

Backend를 다시 시작하자 실제로:

```text
[backend] path=/v1/traces ...
[backend] path=/v1/traces ...
```

가 다시 들어왔다. 붙여넣은 텍스트(1)

즉:

```text
Disk Queue
 ↓
Backend recovery
 ↓
재전송
```

이 성공했다.

### 결과

**성공적으로 복구**

---

# 8. 언제부터 진짜 실패하는가?

핵심은 **queue capacity를 넘어가는 순간**이다.

이번 실험에서는 일부러:

```yaml
queue_size: 10
```

으로 매우 작게 만들었다.

Backend를 죽이고 5000 span을 보내자:

```text
sending queue is full
rejected_items: 64
```

가 발생했다. 붙여넣은 텍스트(1)

이 상태는:

```text
Backend DOWN
 ↓
Collector queue 계속 쌓임
 ↓
Queue FULL
 ↓
새 데이터 수용 불가
 ↓
REJECT
```

이다.

### 중요한 점

**이미 queue에 안전하게 들어간 데이터와, queue full 이후 reject된 데이터를 구분해야 한다.**

Queue에 들어간 것:

```text
✅ 나중에 복구 가능
```

Queue가 꽉 찬 뒤 reject된 것:

```text
❌ Collector에서는 복구 불가능
```

---

# 9. Collector가 reject하면 App에서는 어떻게 되나

Collector가 데이터를 못 받으면 App의 OTLP exporter는 `503 Service Unavailable` 같은 응답을 받는다.

실험에서도:

```text
Transient error Service Unavailable encountered
retrying in ...
```

가 발생했다. 붙여넣은 텍스트(1)

즉 Python SDK가 다시 retry한다.

```text
Collector queue full
 ↓
503
 ↓
Python exporter retry
```

이 단계까지는 아직 기회가 있다.

---

# 10. App SDK queue까지 가득 차면

장애가 계속되면 App의 `BatchSpanProcessor` memory queue도 계속 쌓인다.

결국:

```text
Queue full, dropping Span.
```

이 발생했다. 붙여넣은 텍스트(1)

여기까지 오면 해당 span은 끝이다.

```text
App SDK memory queue FULL
 ↓
Span DROP
 ↓
❌ 복구 불가능
```

왜냐하면 이미 OpenTelemetry SDK가 그 span을 버렸기 때문이다.

---

# 11. 복구 가능한 것 / 불가능한 것

## 복구 가능

다음은 정상적인 resilience 범위 안에 있다.

### Backend 일시 장애

```text
Collector queue
+
retry
```

로 복구 가능.

### Collector restart

persistent queue라면:

```text
Disk
 ↓
restart
 ↓
queue reload
```

가능.

### Backend 복구

queue에 남아있는 데이터는 다시 전달 가능.

---

## 복구 불가능

다음은 telemetry가 이미 사라진 상태다.

### 1. App SDK가 drop한 span

```text
Queue full, dropping Span
```

가 발생했다면:

```text
❌ 복구 불가능
```

원본 데이터가 더 이상 남아있지 않다.

---

### 2. Collector가 queue full로 reject했는데 App도 더 이상 재시도 못한 경우

```text
Collector reject
 ↓
App retry
 ↓
App queue full
 ↓
drop
```

이면 복구 불가능.

---

### 3. Persistent queue disk가 삭제되거나 손상

예:

```bash
rm -rf otel-data
```

를 해버리면:

```text
❌ 기존 queue 복구 불가능
```

이다.

---

### 4. Persistent queue 없이 Collector crash

Memory queue만 썼다면:

```text
Collector RAM
 ↓
crash
 ↓
RAM 데이터 사라짐
```

이므로 복구 불가능.

---

### 5. App process가 trace export 전에 죽음

예:

```text
span 생성
 ↓
SDK memory queue
 ↓
process kill -9
```

이면 아직 Collector까지 전달되지 않은 span은 유실될 수 있다.

---

# 12. 얼마나 안전하게 만들 수 있나

일반적인 production tracing에서는 이 정도가 가장 현실적이다.

```text
App
 ↓
BatchSpanProcessor
 ↓
OTLP
 ↓
Collector
 ↓
Persistent Queue
 ↓
Retry
 ↓
Backend
```

Collector 설정:

```yaml
sending_queue:
  enabled: true
  queue_size: 충분히 크게
  storage: file_storage

retry_on_failure:
  enabled: true
```

그리고 disk를 persistence 가능한 volume에 둔다.

```text
Collector container filesystem ❌

Persistent Volume / host disk ✅
```

---

# 13. 운영에서 반드시 모니터링할 것

단순히 retry를 켜는 것보다 **queue 상태를 보는 게 훨씬 중요하다.**

특히:

```text
queue 사용률
enqueue failure
rejected spans
dropped spans
export failure
retry count
```

를 alert 대상으로 잡아야 한다.

예:

```text
Queue 70% → warning

Queue 90% → critical

enqueue_failed > 0
→ 즉시 alert

dropped_spans > 0
→ 데이터 유실 발생
```

---

# 14. "절대 유실되면 안 된다"면?

여기서 중요한 결론이 있다.

**OpenTelemetry tracing pipeline만으로는 absolute guaranteed delivery를 만들기 어렵다.**

현재 구조는:

```text
retry
+
memory queue
+
persistent queue
```

로 상당히 강하지만 여전히:

```text
queue full
disk failure
app crash
network prolonged outage
```

에서 유실 가능하다.

정말 절대 잃으면 안 되는 데이터라면 tracing 시스템과 별도로 durable system을 둬야 한다.

예:

```text
App
 ↓
Kafka
 ↓
Collector / Processor
 ↓
Backend
```

또는:

```text
          ┌→ Trace Backend
Collector
          └→ S3 archive
```

처럼 구성할 수 있다.

다만 이건 일반 observability trace에는 과할 수 있다.

---

# 15. 실무적으로 추천하는 수준

## 일반 서비스 Trace

이 정도면 충분하다.

```text
App
 ↓
BatchSpanProcessor
 ↓
Collector
 ↓
Persistent Queue
 ↓
Retry
 ↓
Tracing Backend
```

**추천**

- App request는 tracing 실패 때문에 막지 않기
- Collector 사용
- `sending_queue` 활성화
- `file_storage` 활성화
- retry 활성화
- queue/dropped/rejected metric alert

---

## 중요한 Trace

장애 분석에 매우 중요한 trace라면:

```text
Persistent Queue
+
큰 queue capacity
+
충분한 disk
+
Collector HA
```

정도를 추가한다.

---

## 절대 유실되면 안 되는 데이터

예:

```text
결제 원장
Audit log
법적 증적
금융 transaction event
```

같은 것은 OpenTelemetry trace를 source of truth로 쓰면 안 된다.

```text
Business DB / Kafka / durable event log
```

등에 별도로 기록해야 한다.

Tracing은 그 데이터를 **관찰하는 수단**으로 사용해야 한다.

---

# 16. 최종 흐름

이번 실험 결과를 한 그림으로 보면 가장 이해하기 쉽다.

```text
① 정상

App
 ↓
Collector
 ↓
Backend
 ✅ 성공


② Backend 잠깐 장애

App
 ↓
Collector
 ↓
Persistent Queue
 ↓
Retry
 ↓
Backend 복구
 ✅ 성공


③ Collector restart

App
 ↓
Collector
 ↓
Disk Queue
 ↓
Collector restart
 ↓
Queue reload
 ↓
Backend
 ✅ 성공


④ 장애가 너무 오래 지속

Backend DOWN
 ↓
Collector queue
 ↓
FULL
 ↓
Collector reject
 ↓
App retry
 ↓
App memory queue
 ↓
FULL
 ↓
DROP
 ❌ 복구 불가능
```

## 핵심 결론

**성공하는 구간**

> 장애가 queue capacity와 retry 가능한 시간 안에서 복구될 때.

**실패하는 구간**

> Collector queue와 App SDK queue까지 모두 소진되어 span이 `drop`되는 순간.

그리고 가장 중요한 원칙은:

> **queue에 아직 남아 있는 데이터는 살릴 수 있지만, `dropped/rejected`되어 원본이 사라진 telemetry는 OpenTelemetry만으로 복구할 수 없다.**
