#!/bin/bash
# ─── Contextus: Auto-launch Chrome with Remote Debugging ───
# Запускает реальный Google Chrome с GUI-окном и открытым CDP-портом 9222.
# Docker-контейнер подключится к нему автоматически.

CDP_PORT=9222
CHROME_BIN="google-chrome-stable"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Проверяем, не запущен ли уже Chrome с CDP
if ss -tlnp 2>/dev/null | grep -q ":${CDP_PORT}"; then
    echo "[✅ Chrome] Уже запущен на порту ${CDP_PORT}"
    exit 0
fi

# Убираем leftover lock (бывает после kill -9)
rm -f ~/.config/google-chrome/SingletonLock 2>/dev/null

# Убиваем старый forwarder, если был
pkill -f "python3.*tcp_forward.py" || true

# Запускаем TCP forwarder в бэкграунде
python3 "$SCRIPT_DIR/tcp_forward.py" > /dev/null 2>&1 &

echo "[🚀 Chrome] Запускаю Chrome с GUI и CDP на порту ${CDP_PORT}..."
exec "$CHROME_BIN" \
    --remote-debugging-port=${CDP_PORT} \
    --remote-debugging-address=127.0.0.1 \
    --disable-blink-features=AutomationControlled \
    --test-type \
    --no-first-run \
    --no-default-browser-check \
    --user-data-dir="$HOME/.config/contextus-chrome" \
    --start-maximized \
    --disable-background-timer-throttling \
    --disable-backgrounding-occluded-windows \
    --disable-renderer-backgrounding \
    "about:blank"

