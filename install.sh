#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
if [ ! -x bin/ffmpeg ] && ! command -v ffmpeg >/dev/null 2>&1; then
  echo '请先安装 ffmpeg 并加入 PATH；参考 https://ffmpeg.org/download.html' >&2
  exit 1
fi
echo '依赖安装完成。运行 ./run.sh 开始转录。'
