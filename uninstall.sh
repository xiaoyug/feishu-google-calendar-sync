#!/usr/bin/env bash
# 停掉定时同步。默认保留已同步的镜像日程和授权凭证。
# 用法：./uninstall.sh              只停定时器
#       ./uninstall.sh --purge     顺带删掉本地配置和凭证（不动日历里的日程）
set -euo pipefail

RUN_DIR="$HOME/.config/calendar-sync"
LABEL="com.calendar-sync"

case "$(uname -s)" in
  Darwin)
    for P in "$HOME"/Library/LaunchAgents/*calendar-sync*.plist; do
      [ -f "$P" ] || continue
      launchctl unload "$P" 2>/dev/null || true
      rm -f "$P"
      echo "✅ 已停用并移除：$(basename "$P")"
    done
    ;;
  Linux)
    if crontab -l 2>/dev/null | grep -q "calendar-sync/sync.py"; then
      crontab -l 2>/dev/null | grep -v "calendar-sync/sync.py" | crontab -
      echo "✅ 已从 cron 移除"
    fi
    ;;
esac

if [ "${1:-}" = "--purge" ]; then
  rm -rf "$RUN_DIR"
  echo "✅ 已删除本地配置与凭证：$RUN_DIR"
  echo "   注意：两边日历里已建好的镜像日程不会消失。"
  echo "   想清掉它们，请在删除配置【之前】先按标题前缀 [G] / [飞书] 手动批量删除。"
else
  echo "   配置与凭证保留在 $RUN_DIR（想一并删除：./uninstall.sh --purge）"
fi

echo "   已同步的镜像日程仍留在两边日历里，需要的话按标题前缀 [G] / [飞书] 手动删除。"
