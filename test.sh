#!/usr/bin/env bash
set -u

echo "=== 0. start stack ==="
docker compose up -d
sleep 3

echo
echo "=== 1. normal delivery ==="
python3 app.py -n 20 || true
sleep 2
docker compose logs --tail=20 backend

echo
echo "=== 2. backend down -> collector retry/queue ==="
docker compose stop backend
python3 app.py -n 100 || true
sleep 3
docker compose logs --tail=80 collector

echo
echo "=== 3. collector restart -> persistent queue ==="
docker compose restart collector
sleep 3

echo
echo "=== 4. backend restore -> queued traces drain ==="
docker compose start backend
sleep 6
docker compose logs --tail=80 backend
docker compose logs --tail=80 collector

echo
echo "=== 5. queue full test ==="
docker compose stop backend
python3 app.py -n 5000 || true
sleep 4
docker compose logs --tail=200 collector | grep -i -E "queue|enqueue|failed|drop|retry" || true

echo
echo "=== DONE ==="
echo "Restore backend with: docker compose start backend"
echo "Watch logs with:      docker compose logs -f collector backend"
