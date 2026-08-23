#!/bin/bash
# ─── Contextus 2.0 — Полный автозапуск ───
# Одна команда: ./start.sh
# 1. Запускает Chrome Launcher Daemon (автозапуск Chrome по запросу из Docker)
# 2. Запускает Docker-контейнеры
# Chrome запустится автоматически при первом запросе агента в чат.

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DAEMON_PORT=9224

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Contextus 2.0 — Автозапуск"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Шаг 1: Chrome Launcher Daemon ──────────────
if ss -tlnp 2>/dev/null | grep -q ":${DAEMON_PORT}"; then
    echo "[✅] Chrome Launcher Daemon уже запущен на порту ${DAEMON_PORT}"
else
    echo "[🔧] Запускаю Chrome Launcher Daemon..."
    python3 "$SCRIPT_DIR/chrome_launcher_daemon.py" &
    sleep 1
    
    if ss -tlnp 2>/dev/null | grep -q ":${DAEMON_PORT}"; then
        echo "[✅] Chrome Launcher Daemon готов на порту ${DAEMON_PORT}"
    else
        echo "[⚠️] Не удалось запустить Launcher Daemon. Chrome придётся запускать вручную."
    fi
fi

# ── Шаг 2: Docker Compose ───────────────────
echo "[🐳] Запускаю Docker-контейнеры..."
cd "$SCRIPT_DIR"
docker compose up -d --build

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅ Contextus 2.0 запущен!"
echo ""
echo "  Backend:    http://localhost:8000"
echo "  Логи:       docker compose logs -f fastapi_backend"
echo ""
echo "  Chrome откроется АВТОМАТИЧЕСКИ при первом"
echo "  запросе агента в чат (через Launcher Daemon)."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
