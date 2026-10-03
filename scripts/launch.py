"""Supervise the local processes; Ctrl+C terminates only processes we started."""

import argparse
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Launch Pixel Station")
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    data = Path(os.environ.get("PIXEL_STATION_DATA", ROOT / "data")).resolve()
    logs = data / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    children: list[subprocess.Popen] = []
    streams = []
    environment = os.environ.copy()
    environment.update(OLLAMA_NO_CLOUD="1", OLLAMA_MAX_LOADED_MODELS="1", OLLAMA_NUM_PARALLEL="1")

    def start(name: str, command: list[str], cwd: Path) -> None:
        stream = (logs / f"{name}.log").open("a", encoding="utf-8")
        streams.append(stream)
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        children.append(subprocess.Popen(command, cwd=cwd, env=environment, stdout=stream, stderr=stream, **options))

    try:
        if port_open(8000) or port_open(5173):
            print("Port 8000 or 5173 is already in use. Close the existing app/server before launching.")
            return 1
        ollama = shutil.which("ollama")
        if ollama and not port_open(11434):
            start("ollama", [ollama, "serve"], ROOT)
        start("backend", [sys.executable, "-m", "uvicorn", "pixel_station.app:app", "--host", "127.0.0.1", "--port", "8000"], ROOT / "backend")
        node = shutil.which("node")
        if not node:
            raise RuntimeError("Node.js is missing. Install Node.js 22+ and restart your terminal.")
        start("frontend", [node, str(ROOT / "frontend/node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", "5173", "--strictPort"], ROOT / "frontend")
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            for process in children:
                if process.poll() is not None:
                    raise RuntimeError(f"A service stopped. Inspect logs in {logs}")
            if port_open(8000) and port_open(5173):
                try:
                    with urllib.request.urlopen("http://127.0.0.1:8000/api/health", timeout=2) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
            time.sleep(0.2)
        else:
            raise RuntimeError(f"Startup timed out. Inspect logs in {logs}")
        print("Pixel Station: http://127.0.0.1:5173")
        print(f"Data: {data}\nLogs: {logs}\nPress Ctrl+C to stop.")
        if not args.no_browser:
            webbrowser.open("http://127.0.0.1:5173")
        while True:
            if any(process.poll() is not None for process in children):
                raise RuntimeError(f"A service stopped. Inspect logs in {logs}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        return 0
    except (OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        for process in reversed(children):
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
                else:
                    os.killpg(process.pid, signal.SIGTERM)
        for stream in streams:
            stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
