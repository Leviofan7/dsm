#!/usr/bin/env python3
"""
chrome_launcher_daemon.py — HTTP-демон на хосте для автозапуска Chrome.

Запускается один раз (через start.sh) и слушает порт 9224.
Когда Docker-контейнер не находит Chrome на CDP-порту, он шлёт
GET http://host.docker.internal:9224/launch — и демон запускает
Chrome + TCP-forwarder на хосте с GUI-окном.

Порты:
  9222 — Chrome CDP (localhost)
  9223 — TCP forwarder (0.0.0.0, для Docker)
  9224 — этот демон (0.0.0.0)
"""

import subprocess
import os
import sys
import json
import socket
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent.resolve()
CDP_PORT = 9222
DAEMON_PORT = 9224


def is_port_open(port: int, host: str = "127.0.0.1") -> bool:
    """Проверяет, слушается ли порт."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


class ChromeLauncherHandler(BaseHTTPRequestHandler):
    """Минимальный HTTP-обработчик для запуска Chrome."""

    _chrome_process = None

    def do_GET(self):
        if self.path == "/launch":
            self._handle_launch()
        elif self.path == "/status":
            self._handle_status()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_launch(self):
        """Запускает Chrome, если ещё не запущен."""
        # Если Chrome уже слушает CDP — ничего не делаем
        if is_port_open(CDP_PORT):
            self._respond(200, {"status": "already_running", "cdp_port": CDP_PORT})
            return

        # Запускаем run_real_chrome.sh
        script_path = SCRIPT_DIR / "run_real_chrome.sh"
        if not script_path.exists():
            self._respond(500, {"error": f"Script not found: {script_path}"})
            return

        try:
            print(f"[🚀 Launcher] Запускаю Chrome через {script_path}...")
            ChromeLauncherHandler._chrome_process = subprocess.Popen(
                ["bash", str(script_path)],
                cwd=str(SCRIPT_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # Ждём готовности CDP (макс. 10 секунд)
            for i in range(20):
                time.sleep(0.5)
                if is_port_open(CDP_PORT):
                    print(f"[✅ Launcher] Chrome готов на порту {CDP_PORT}")
                    self._respond(200, {"status": "launched", "cdp_port": CDP_PORT})
                    return

            print(f"[⚠️ Launcher] Chrome не запустился за 10 секунд")
            self._respond(504, {"error": "Chrome launch timeout"})

        except Exception as e:
            print(f"[❌ Launcher] Ошибка: {e}")
            self._respond(500, {"error": str(e)})

    def _handle_status(self):
        """Возвращает статус Chrome."""
        chrome_running = is_port_open(CDP_PORT)
        forwarder_running = is_port_open(9223)
        self._respond(200, {
            "chrome_cdp": chrome_running,
            "tcp_forwarder": forwarder_running,
            "daemon": True,
        })

    def _respond(self, code: int, data: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        """Тихий лог — только важные события."""
        pass


def main():
    # Проверяем, не занят ли порт
    if is_port_open(DAEMON_PORT):
        print(f"[✅ Launcher] Демон уже запущен на порту {DAEMON_PORT}")
        sys.exit(0)

    server = HTTPServer(("0.0.0.0", DAEMON_PORT), ChromeLauncherHandler)
    print(f"[🔧 Launcher] Chrome Launcher Daemon слушает на порту {DAEMON_PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[🛑 Launcher] Остановлен.")
        server.server_close()


if __name__ == "__main__":
    main()
