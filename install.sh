#!/usr/bin/env bash
# 安装定时同步任务：macOS 用 launchd，Linux 用 cron。默认每 10 分钟一次。
# 用法：./install.sh [间隔分钟数]     卸载：./uninstall.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INTERVAL_MIN="${1:-10}"
INTERVAL_SEC=$((INTERVAL_MIN * 60))
RUN_DIR="$HOME/.config/calendar-sync"
LABEL="com.calendar-sync"

PYTHON3="$(command -v python3 || true)"
[ -n "$PYTHON3" ] || { echo "❌ 找不到 python3"; exit 1; }

mkdir -p "$RUN_DIR"

# 仓库是源码真身，运行时跑的是这份拷贝——改完源码重跑本脚本即可重新部署
cp "$SCRIPT_DIR/sync.py" "$RUN_DIR/sync.py"

# 定时任务的 PATH 很精简，把 node/lark-cli 所在目录带上
NODE_BIN_DIR="$(dirname "$(command -v node 2>/dev/null || echo /usr/local/bin/node)")"
TASK_PATH="${NODE_BIN_DIR}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

case "$(uname -s)" in
  Darwin)
    PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
    mkdir -p "$HOME/Library/LaunchAgents"

    # 清掉任何旧版本留下的同名任务，避免两个定时器同时跑
    for OLD in "$HOME"/Library/LaunchAgents/*calendar-sync*.plist; do
      [ -f "$OLD" ] || continue
      [ "$OLD" = "$PLIST" ] && continue
      launchctl unload "$OLD" 2>/dev/null || true
      rm -f "$OLD"
      echo "   （已清理旧定时任务：$(basename "$OLD")）"
    done

    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON3}</string>
        <string>${RUN_DIR}/sync.py</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>${TASK_PATH}</string>
    </dict>
    <key>StartInterval</key>
    <integer>${INTERVAL_SEC}</integer>
    <key>RunAtLoad</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${RUN_DIR}/task.log</string>
    <key>StandardErrorPath</key>
    <string>${RUN_DIR}/task.log</string>
</dict>
</plist>
EOF

    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "✅ 已安装（macOS launchd）：每 ${INTERVAL_MIN} 分钟同步一次，现在立即先跑一轮"
    ;;

  Linux)
    command -v crontab >/dev/null || { echo "❌ 没有 crontab，请手动配置定时"; exit 1; }
    LINE="*/${INTERVAL_MIN} * * * * PATH=${TASK_PATH} ${PYTHON3} ${RUN_DIR}/sync.py >> ${RUN_DIR}/task.log 2>&1"
    # 去掉旧条目再写入，保证幂等
    ( crontab -l 2>/dev/null | grep -v "calendar-sync/sync.py" ; echo "$LINE" ) | crontab -
    echo "✅ 已安装（Linux cron）：每 ${INTERVAL_MIN} 分钟同步一次"
    echo "   先手动跑一轮…"
    "$PYTHON3" "$RUN_DIR/sync.py" || true
    ;;

  *)
    echo "❌ 暂不支持的系统：$(uname -s)。可手动定时执行：${PYTHON3} ${RUN_DIR}/sync.py"
    exit 1
    ;;
esac

echo "   查看日志：tail -f ${RUN_DIR}/sync.log"
echo "   卸载：    ${SCRIPT_DIR}/uninstall.sh"
