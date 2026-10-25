#!/usr/bin/env bash
# 一键启动本地 RAG 知识库（Git Bash / Linux / macOS）
set -euo pipefail
cd "$(dirname "$0")"

if [ -x ".venv/bin/python" ]; then
  PYTHON=".venv/bin/python"
elif [ -x ".venv/Scripts/python.exe" ]; then
  PYTHON=".venv/Scripts/python.exe"
else
  PYTHON="${PYTHON:-python3}"
fi

echo "启动 RAG 知识库：http://127.0.0.1:8000/"
echo "按 Ctrl+C 停止。"
exec "$PYTHON" -m app.main
