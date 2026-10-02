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
# Всегда перезапускаем: раньше «порт занят → пропускаем» оставляло работать СТАРЫЙ процесс
# с устаревшим кодом — правки демона не вступали в силу, пока кто-нибудь не убьёт его руками.
echo "[🔧] Перезапускаю Chrome Launcher Daemon..."
pkill -f "chrome_launcher_daemon.py" 2>/dev/null || true
for i in $(seq 1 10); do
    ss -tlnp 2>/dev/null | grep -q ":${DAEMON_PORT}" || break
    sleep 0.2
done
# `-u` (unbuffered): без него print'ы демона буферизуются и лог /tmp/... остаётся пустым,
# пока буфер не переполнится — то есть диагностика host-запуска Chrome «молчит».
nohup python3 -u "$SCRIPT_DIR/chrome_launcher_daemon.py" > /tmp/contextus_chrome_daemon.log 2>&1 &
sleep 1

if ss -tlnp 2>/dev/null | grep -q ":${DAEMON_PORT}"; then
    echo "[✅] Chrome Launcher Daemon готов на порту ${DAEMON_PORT} (лог: /tmp/contextus_chrome_daemon.log)"
else
    echo "[⚠️] Не удалось запустить Launcher Daemon — Chrome придётся запускать вручную (bash run_real_chrome.sh)."
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
echo "  Браузерная инфраструктура (демон :9224):"
curl -s --max-time 3 http://127.0.0.1:9224/status || echo "  демон не ответил"
echo ""
echo "  Chrome откроется АВТОМАТИЧЕСКИ при первом браузерном"
echo "  запросе агента — демон поднимет Chrome + TCP-forwarder."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
