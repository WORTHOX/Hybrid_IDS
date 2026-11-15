#!/usr/bin/env bash
set -e

PORT="${PORT:-10000}"
API_PORT="${API_PORT:-8000}"
export HNIDS_API_KEY="${HNIDS_API_KEY:-dev-local-key}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-1}"
export KMP_DUPLICATE_LIB_OK=TRUE

echo "=================================================="
echo "  H_IDS — Hybrid Network Intrusion Detection"
echo "  Backend Port:   $API_PORT"
echo "  Dashboard Port: $PORT"
echo "=================================================="

# Start backend engine in background
echo "-> Starting neural inference backend..."
uv run hnids serve -c configs/base.yaml configs/local.yaml --bundle models/b20261111T112951Z &
BACKEND_PID=$!

# Wait for backend readiness
echo "-> Waiting for backend ready signal..."
for i in {1..30}; do
  if curl -s "http://127.0.0.1:${API_PORT}/readyz" | grep -q '"ready":true'; then
    echo "-> Backend ready!"
    break
  fi
  sleep 1
done

# Start unified dashboard proxy
echo "-> Starting dashboard proxy on port ${PORT}..."
python3 serve_dashboard.py --port "${PORT}" --api "http://127.0.0.1:${API_PORT}" &
DASHBOARD_PID=$!

trap "kill $BACKEND_PID $DASHBOARD_PID 2>/dev/null || true" SIGINT SIGTERM EXIT
wait $DASHBOARD_PID
