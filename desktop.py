import socket
import threading
import time

import webview

from app import create_app

HOST = "127.0.0.1"
PORT = 5050


def _run_flask():
    app = create_app()
    app.run(host=HOST, port=PORT, debug=False, use_reloader=False)


def _wait_for_server(timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((HOST, PORT), timeout=0.5):
                return
        except OSError:
            time.sleep(0.05)


def main():
    threading.Thread(target=_run_flask, daemon=True).start()
    _wait_for_server()
    webview.create_window(
        "Rulo Financiero",
        f"http://{HOST}:{PORT}",
        width=1280,
        height=900,
        min_size=(900, 600),
    )
    webview.start()


if __name__ == "__main__":
    main()
