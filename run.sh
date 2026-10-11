#!/usr/bin/env bash
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then
  echo "[!] 没找到 python3，请先安装 Python 3.8 以上。"
  exit 1
fi
if [ ! -f config.json ]; then
  echo "[i] 还没有 config.json，先启动配置向导。"
  python3 configure.py
else
  python3 alert.py "$@"
fi
