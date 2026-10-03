# OpenTelemetry Trace Delivery Failure 실험 정리

## 1. 실험 목적

OpenTelemetry 기반 tracing 환경에서 데이터 전달 실패가 발생했을 때 각 계층이 어떻게 동작하는지 확인했다.

확인하려는 장애 구간은 다음과 같다.

```text
Application
   ↓
OpenTelemetry SDK
   ↓
OpenTelemetry Collector
   ↓
Persistent Queue
   ↓
Trace Backend
```

주요 확인 항목:

- App → Collector 연결 실패 시 Python SDK retry 여부
- Collector → Backend 실패 시 Collector retry 여부
- Collector queue 동작 여부
- Collector restart 시 persistent queue 복구 여부
- Backend 복구 후 queued trace 재전송 여부
- Queue가 가득 찼을 때 drop/reject 발생 여부
- 장애가 application business logic까지 영향을 주는지 여부

---

## 2. 실험 구성

### Architecture

```text
Python Application
    │
    │ OTLP/HTTP :4318
    ▼
OpenTelemetry Collector
    │
    │ sending_queue
    │ retry_on_failure
    │ file_storage
    ▼
Dummy HTTP Backend
```

### Application

Python OpenTelemetry SDK를 사용했다.

```text
Tracer
 ↓
BatchSpanProcessor
 ↓
OTLPSpanExporter
 ↓
Collector
```

주요 설정:

- `BatchSpanProcessor`
- OTLP/HTTP exporter
- App 내부 memory queue 사용
- `force_flush()`로 export flush 시도

---

## 3. 장애 대응 설계

각 구간별 failure를 다음처럼 처리하도록 구성했다.

| 장애 구간 | 대응 |
|---|---|
| App → Collector | Python OTLP exporter retry |
| App 내부 부하 | `BatchSpanProcessor` memory queue |
| Collector → Backend | `retry_on_failure` |
| Backend 일시 장애 | `sending_queue` |
| Collector restart | `file_storage` persistent queue |
| Queue full | 신규 telemetry reject/drop |
| 장기 장애 | 필요 시 Kafka/S3 등 durable storage 추가 |

전체 fallback 구조:

```text
Application
     │
     ▼
BatchSpanProcessor
     │
     ├─ memory queue
     └─ exporter retry
            │
            ▼
       Collector
            │
            ├─ sending_queue
            ├─ retry_on_failure
            └─ file_storage
                    │
                    ▼
              Trace Backend
```

---

# 4. 실험 1 — 정상 전달

## 실험 방법

Backend와 Collector 모두 정상 상태에서 span 20개를 생성했다.

```bash
python3 app.py -n 20
```

## 결과

```text
generated 20 spans
force_flush: True

[backend] path=/v1/traces bytes=1189 fail=False
```

App에서 생성된 trace가 Collector를 거쳐 Backend까지 정상 도착했다. 붙여넣은 텍스트(1)

## 결과 해석

```text
App
 ↓
Collector
 ↓
Backend
```

정상 경로 동작 확인.

**결과: 성공**

---

# 5. 실험 2 — Backend 장애

## 실험 방법

Backend container를 중지했다.

```bash
docker compose stop backend
```

이후 span 100개 생성:

```bash
python3 app.py -n 100
```

## 기대 동작

```text
App
 ↓
Collector
 ↓
Backend ❌

Collector
 ↓
Queue
 ↓
Retry
```

## 결과

Application은 정상적으로 span을 생성했다.

```text
generated 100 spans
force_flush: True
```

Collector 역시 정상적으로 실행 중이었고 HTTP receiver가 `4318` port에서 요청을 받고 있었다. 붙여넣은 텍스트(1) 붙여넣은 텍스트(1)

Collector는 Backend export를 계속 시도했다.

```text
Preparing to make HTTP request
http://backend:5000/v1/traces
```

그리고 실패 시 retry가 발생했다.

```text
Exporting failed. Will retry the request after interval.
interval: 1.424...
```

붙여넣은 텍스트(1)

## 결과 해석

Backend 장애가 발생해도 Application request 자체는 실패하지 않았다.

```text
Business Logic
      │
      └─ 정상 동작

Telemetry
      ↓
Collector
      ↓
Backend ❌
      ↓
retry
```

즉 observability backend 장애와 application availability가 분리되어 있었다.

**결과: 성공**

---

# 6. 실험 3 — Persistent Queue

## 실험 목적

Backend가 죽은 상태에서 Collector까지 restart되더라도 trace가 보존되는지 확인했다.

## 설정

Collector exporter에 다음을 적용했다.

```yaml
sending_queue:
  enabled: true
  queue_size: 10
  storage: file_storage
```

그리고:

```yaml
extensions:
  file_storage:
    directory: /var/lib/otelcol
```

을 사용했다.

---

## 초기 문제

첫 번째 실험에서는 Collector가 다음 에러로 시작하지 못했다.

```text
open /var/lib/otelcol/exporter_otlphttp_backend_traces:
permission denied
```

persistent queue를 저장할 directory에 container write 권한이 없었다. 붙여넣은 텍스트(1)

### 수정

host의 storage directory에 write 권한을 부여했다.

```bash
rm -rf otel-data
mkdir otel-data
chmod 777 otel-data
```

이후 Collector가 정상적으로 persistent queue를 초기화했다.

```text
Initializing new persistent queue
```

붙여넣은 텍스트(1)

---

# 7. 실험 4 — Collector Restart

## 실험 방법

Backend가 내려간 상태에서 trace를 queue에 넣은 뒤 Collector를 restart했다.

```bash
docker compose restart collector
```

## 결과

Collector restart 후 다음 로그가 출력됐다.

```text
Loaded queue metadata

readIndex: 2
writeIndex: 3
itemsSize: 100
bytesSize: 11654
dispatchedItems: 1
```

붙여넣은 텍스트(1)

그리고:

```text
Fetching items left for dispatch by consumers
Moved items for dispatching back to queue
```

가 수행됐다. 붙여넣은 텍스트(1)

## 결과 해석

Collector 프로세스가 restart되어도 queue가 memory에서 사라지지 않고 disk에서 복원되었다.

```text
Backend DOWN
     ↓
Collector
     ↓
Persistent Queue
     ↓
Disk

Collector restart
     ↓
Load queue metadata
     ↓
100 items restored
```

즉 `file_storage` 기반 persistent queue가 실제로 동작했다.

**결과: 성공**

---

# 8. 실험 5 — Backend 복구 후 Queue Drain

## 실험 방법

Collector restart 이후 Backend를 다시 실행했다.

```bash
docker compose start backend
```

## 결과

Backend에 다시 OTLP request가 들어오기 시작했다.

```text
[backend] path=/v1/traces bytes=1882 fail=False
[backend] path=/v1/traces bytes=3076 fail=False
```

붙여넣은 텍스트(1)

## 결과 해석

persistent queue에 남아 있던 trace가 Backend 복구 후 다시 export되었다.

```text
Persistent Queue
      ↓
Backend Recovery
      ↓
Retry / Drain
      ↓
Trace delivered
```

따라서 일시적인 backend outage와 Collector restart를 함께 겪어도 데이터를 복구할 수 있음을 확인했다.

**결과: 성공**

---

# 9. 실험 6 — Queue Full

## 실험 방법

Collector의 queue 크기를 일부러 작게 설정했다.

```yaml
queue_size: 10
```

Backend를 중지한 뒤 span 5000개를 생성했다.

```bash
python3 app.py -n 5000
```

## Collector 결과

Collector에서:

```text
Exporting failed. Rejecting data.
error: sending queue is full
rejected_items: 64
```

가 반복적으로 발생했다. 붙여넣은 텍스트(1)

즉:

```text
Backend DOWN
     ↓
Collector retry
     ↓
Persistent queue 증가
     ↓
Queue FULL
     ↓
Incoming telemetry reject
```

가 발생했다.

**결과: 성공**

---

# 10. App SDK까지 Backpressure 전파

Collector queue가 가득 차자 Python App에서도 다음이 발생했다.

```text
Transient error Service Unavailable encountered
retrying in ...
```

붙여넣은 텍스트(1)

즉 Collector가 더 이상 telemetry를 정상적으로 받을 수 없게 되자 App OTLP exporter가 이를 transient failure로 판단하고 retry했다.

장애가 계속되자 결국 Python SDK에서도:

```text
Queue full, dropping Span.
```

이 발생했다. 붙여넣은 텍스트(1)

전체 흐름:

```text
Backend DOWN
      ↓
Collector retry
      ↓
Collector persistent queue
      ↓
Collector queue FULL
      ↓
Collector rejects telemetry
      ↓
503 / Service Unavailable
      ↓
Python OTLP exporter retry
      ↓
Python BatchSpanProcessor queue
      ↓
SDK queue FULL
      ↓
Span DROP
```

---

# 11. 실험 결과 요약

| Failure Scenario | 동작 | 결과 |
|---|---|---|
| 정상 상태 | App → Collector → Backend | ✅ |
| Backend down | Collector retry | ✅ |
| Backend 일시 장애 | Collector queue buffering | ✅ |
| Collector restart | Disk queue 복구 | ✅ |
| Backend recovery | Queue drain 및 재전송 | ✅ |
| Collector queue full | Telemetry reject | ✅ |
| Collector 503 | App exporter retry | ✅ |
| App SDK queue full | Span drop | ✅ |

---

# 12. 최종 결론

이번 실험에서 OpenTelemetry의 failure handling 구조를 실제로 확인했다.

```text
Application
   │
   │ BatchSpanProcessor
   │ Memory Queue
   │ Export Retry
   ▼
OpenTelemetry Collector
   │
   │ retry_on_failure
   │ sending_queue
   │ file_storage
   ▼
Trace Backend
```

단기 장애는 다음 조합으로 상당 부분 흡수할 수 있다.

```text
Retry
  +
Queue
  +
Persistent Disk
```

특히 `file_storage`를 이용하면 Collector restart가 발생해도 queue에 있던 telemetry를 복구해서 다시 전송할 수 있었다.

하지만 장애가 장시간 지속되면:

```text
Backend DOWN
 ↓
Collector queue FULL
 ↓
Collector reject
 ↓
Application exporter retry
 ↓
Application SDK queue FULL
 ↓
Span DROP
```

으로 결국 데이터가 유실된다.

따라서 OpenTelemetry tracing은 기본적으로 **best-effort delivery + retry/buffering 구조**로 보는 것이 적절하다.

trace 유실을 최소화하는 일반적인 구성은:

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
Trace Backend
```

이고, **telemetry 자체가 반드시 유실되면 안 되는 환경**이라면 추가적으로:

```text
Kafka / Pulsar / S3
```

같은 durable storage 계층을 고려할 수 있다.
