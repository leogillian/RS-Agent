#!/usr/bin/env bash
# 根据 git 变更文件决定跑 unit / integration / 两者
# 在 RS-Agent 根目录执行；可由 pre-commit / pre-push 调用

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

CHANGED=""
if git rev-parse --git-dir >/dev/null 2>&1; then
  CHANGED=$(git diff --name-only HEAD 2>/dev/null || true)
  # 若暂存区有变更，也纳入（pre-commit 时常用）
  STAGED=$(git diff --name-only --cached 2>/dev/null || true)
  CHANGED=$(echo -e "${CHANGED}\n${STAGED}" | sort -u)
fi

need_unit=0
need_integration=0

if [ -z "$CHANGED" ]; then
  need_unit=1
  need_integration=1
else
  echo "$CHANGED" | grep -qE '^backend/(app\.py|routers/)' && need_integration=1
  echo "$CHANGED" | grep -qE '^backend/(services/|config\.py|db\.py)|^tests/unit/' && need_unit=1
  echo "$CHANGED" | grep -qE '^tests/integration/' && need_integration=1
  echo "$CHANGED" | grep -qE '^backend/' && need_unit=1
  echo "$CHANGED" | grep -qE '^backend/' && need_integration=1
  if [ "$need_unit" -eq 0 ] && [ "$need_integration" -eq 0 ]; then
    need_unit=1
    need_integration=1
  fi
fi

run_failed=0
if [ "$need_unit" -eq 1 ] && [ -d "tests/unit" ]; then
  echo "Running unit tests (tests/unit/) ..."
  python -m pytest tests/unit/ -v --tb=short || run_failed=1
fi
if [ "$need_integration" -eq 1 ] && [ -d "tests/integration" ]; then
  echo "Running integration tests (tests/integration/) ..."
  python -m pytest tests/integration/ -v --tb=short || run_failed=1
fi
exit $run_failed
