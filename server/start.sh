#!/usr/bin/env bash
# Linux／macOS 啟動：第一次會建虛擬環境並安裝套件，之後直接啟動。參數照傳給 run.py（例如 ./start.sh --dev）
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  PY=""
  for c in python3.13 python3.12 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(sys.version_info < (3, 12))'; then PY="$c"; break; fi
  done
  [ -n "$PY" ] || { echo "需要 Python 3.12 以上"; exit 1; }
  echo "第一次執行：建立虛擬環境並安裝套件…"
  "$PY" -m venv .venv
  .venv/bin/python -m pip install -q --upgrade pip
  .venv/bin/python -m pip install -q -r requirements.txt
  .venv/bin/python -m pip install -q --no-deps -e .
fi

exec .venv/bin/python run.py "$@"
