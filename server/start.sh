#!/usr/bin/env bash
# Linux／macOS 啟動：第一次建虛擬環境；requirements.txt 有變（例如 git pull 之後）就重裝套件；再啟動。參數照傳給 run.py（例如 ./start.sh --dev）
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  PY=""
  for c in python3.13 python3.12 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then PY="$c"; break; fi
  done
  [ -n "$PY" ] || { echo "需要 Python 3.12 以上"; exit 1; }
  echo "建立虛擬環境（$PY）…"
  "$PY" -m venv .venv
fi

if ! cmp -s requirements.txt .venv/requirements.installed; then
  echo "安裝套件（第一次執行或 requirements.txt 有變），通常要 3～10 分鐘…"
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
  .venv/bin/python -m pip install -q --no-deps -e .
  cp requirements.txt .venv/requirements.installed      # 裝完才記下：中途失敗下次會重裝
fi

exec .venv/bin/python run.py "$@"
