#!/usr/bin/env bash
set -euo pipefail

echo "==> Running OpenClaw orchestration healthcheck"

if ! command -v python3 >/dev/null 2>&1; then
  echo "[ERROR] python3 not found in PATH"
  exit 1
fi

echo "[OK] python3 detected: $(python3 --version)"

if [[ ! -d "src/openclaw_orchestration" ]]; then
  echo "[ERROR] src/openclaw_orchestration directory is missing"
  exit 1
fi

if [[ ! -f "scripts/run_task.py" ]]; then
  echo "[ERROR] scripts/run_task.py is missing"
  exit 1
fi

python3 -m compileall src scripts >/dev/null
echo "[OK] Python files compile successfully"

python3 scripts/run_task.py --task tasks/examples/maintenance.yaml --dry-run >/dev/null
echo "[OK] Example maintenance task validates in dry-run mode"

echo "==> Healthcheck passed"
