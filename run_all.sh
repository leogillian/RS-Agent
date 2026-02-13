#!/usr/bin/env bash

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$ROOT_DIR"

echo "[RS-Agent] 根目录：$ROOT_DIR"

if [ -f ".env" ]; then
  echo "[RS-Agent] 加载 .env 环境变量"
  # shellcheck disable=SC2046
  export $(grep -vE '^\s*#' .env | xargs -0 2>/dev/null || grep -vE '^\s*#' .env | xargs 2>/dev/null || true)
fi

echo "[RS-Agent] 启动后端（FastAPI / Uvicorn）..."
python3 -m uvicorn backend.app:app --reload --host 127.0.0.1 --port 8000 &
BACKEND_PID=$!

echo "[RS-Agent] 启动前端（Vite dev server）..."
cd "$ROOT_DIR/frontend"
npm run dev &
FRONTEND_PID=$!

cd "$ROOT_DIR"

echo "[RS-Agent] 后端 PID: $BACKEND_PID"
echo "[RS-Agent] 前端 PID: $FRONTEND_PID"
echo "[RS-Agent] 所有服务已启动。按 Ctrl+C 结束本脚本，不会自动杀掉子进程，请在各自终端/进程管理器中手动停止。"

wait

