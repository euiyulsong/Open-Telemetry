# OpenTelemetry Failure Lab

빠르게 아래 failure path를 실험하는 최소 구성입니다.

```text
Python App
   |
   | OTLP/HTTP
   v
OpenTelemetry Collector
   |
   | sending_queue + retry_on_failure
   | file_storage
   v
Dummy Backend
```

## 준비

- Docker / Docker Compose
- Python 3.10+
- pip

의존성 설치:

```bash
python3 -m pip install -r requirements.txt
```

## 실행

```bash
docker compose up -d
python3 app.py -n 20
docker compose logs -f collector backend
```

## 전체 자동 실험

```bash
chmod +x test.sh
./test.sh
```

## 실험 내용

### 1. 정상 전달

```bash
python3 app.py -n 20
```

Backend 로그에 `/v1/traces` 요청이 보이면 정상입니다.

### 2. Backend 장애

```bash
docker compose stop backend
python3 app.py -n 100
docker compose logs -f collector
```

Collector retry 로그와 queue 동작을 확인합니다.

### 3. Collector restart + persistent queue

Backend가 내려간 상태에서 span을 생성한 후:

```bash
docker compose restart collector
docker compose start backend
docker compose logs -f collector backend
```

`file_storage`를 사용하므로 Collector 재시작 뒤에도 queue에 남은 데이터를 다시 export하는지 확인할 수 있습니다.

### 4. Queue full

현재 Collector queue size는 일부러 매우 작게 `10`으로 설정했습니다.

```bash
docker compose stop backend
python3 app.py -n 5000
docker compose logs collector | grep -i -E "queue|enqueue|failed|drop|retry"
```

queue overflow / enqueue failure / drop 관련 로그를 확인합니다.

## 정리

```text
App -> Collector 장애
  Python OTLP exporter의 transient retry
  실패가 지속되면 SDK 메모리 데이터는 유실 가능

Collector -> Backend 장애
  retry_on_failure
  sending_queue

Collector restart
  file_storage 기반 persistent queue

Queue full
  enqueue failure / drop 가능
```

## 종료

```bash
docker compose down
```

완전히 초기화:

```bash
docker compose down
rm -rf otel-data
```
