#!/bin/zsh
set -eu
cd -- "${0:A:h}"
port=8765
url="http://127.0.0.1:$port"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
if ! command -v python3 >/dev/null 2>&1; then
  printf '找不到 Python 3，无法启动。\n'; read -r '?按回车退出。'; exit 1
fi
# This route + exact product marker identify a current local workbench, not a different server on this port.
if /usr/bin/curl --noproxy '*' -fsS --max-time 1 "$url/" 2>/dev/null | /usr/bin/grep -q '视频工作台'; then
  /usr/bin/open "$url/"
  exit
fi
echo '正在启动视频工作台。制作中请保留此终端窗口。'
python3 -B server.py --port "$port" &
pid=$!
trap 'kill "$pid" 2>/dev/null || true' EXIT
trap 'exit 130' INT TERM
for i in {1..60}; do
  if /usr/bin/curl --noproxy '*' -fsS --max-time 1 "$url/" 2>/dev/null | /usr/bin/grep -q '视频工作台'; then
    /usr/bin/open "$url/"
    wait "$pid"
    exit
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    printf '工作台未启动，请查看上面的错误。\n'; read -r '?按回车退出。'; exit 1
  fi
  sleep 0.15
done
printf '工作台启动超时。\n'; read -r '?按回车退出。'; exit 1
