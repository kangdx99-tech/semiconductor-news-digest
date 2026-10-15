#!/usr/bin/env bash
# 安装/更新定时任务（Linux、macOS、WSL 通用）。
#
#   ./scripts/install_cron.sh                      # 默认每天 08:00
#   ./scripts/install_cron.sh "0 23 * * *"         # 每天 23:00
#   ./scripts/install_cron.sh "0 8 * * 1-5"        # 工作日 08:00
#   ./scripts/install_cron.sh --uninstall          # 卸载
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEDULE="${1:-0 8 * * *}"
TAG="# semiconductor-news-digest"
LOG_DIR="$PROJECT_DIR/logs"
MARKER="semiconductor-news-digest/run.py"

PYTHON_BIN="$(command -v python3 || command -v python)"
if [ -z "${PYTHON_BIN:-}" ]; then
  echo "找不到 python3，请先安装 Python 3.9+" >&2
  exit 1
fi

if [ "${1:-}" = "--uninstall" ]; then
  crontab -l 2>/dev/null | grep -v "$MARKER" | grep -v "$TAG" | crontab -
  echo "已卸载定时任务。"
  exit 0
fi

mkdir -p "$LOG_DIR"

# 幂等：先删掉旧的同名条目再写入
CURRENT="$(crontab -l 2>/dev/null | grep -v "$MARKER" | grep -v "$TAG" || true)"
ENTRY="$SCHEDULE cd $PROJECT_DIR && $PYTHON_BIN run.py >> $LOG_DIR/cron.log 2>&1 $TAG"
printf '%s\n%s\n' "$CURRENT" "$ENTRY" | sed '/^$/d' | crontab -

echo "已安装定时任务："
echo "  $SCHEDULE  →  $PROJECT_DIR/run.py"
echo "日志：$LOG_DIR/cron.log"
echo
echo "提示：先用 '$PYTHON_BIN run.py --dry-run' 手动跑通一次，确认推送渠道配置正确。"
crontab -l | grep "$TAG" || true
