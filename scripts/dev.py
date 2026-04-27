#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import TextIO
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
FRONTEND_DIR = ROOT / "frontend"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the comic library backend and frontend together."
    )
    parser.add_argument("--backend-host", default="127.0.0.1")
    parser.add_argument("--backend-port", type=int, default=8000)
    parser.add_argument("--frontend-host", default="127.0.0.1")
    parser.add_argument(
        "--frontend-port",
        "--port",
        dest="frontend_port",
        type=int,
        default=5173,
        help="Port for the Vite dev server.",
    )
    return parser.parse_args()


def prefixed_stream(prefix: str, stream: TextIO) -> None:
    for line in iter(stream.readline, ""):
        print(f"[{prefix}] {line.rstrip()}", flush=True)
    stream.close()


def required_paths() -> tuple[Path, Path]:
    backend_python = BACKEND_DIR / ".venv" / "bin" / "python"
    frontend_node_modules = FRONTEND_DIR / "node_modules"
    return backend_python, frontend_node_modules


def fail_prereq(message: str) -> int:
    print(message, file=sys.stderr)
    print("Run `npm install` first.", file=sys.stderr)
    return 1


def is_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def find_available_port(host: str, preferred_port: int) -> int:
    port = preferred_port
    while port < 65535:
        if is_port_available(host, port):
            return port
        port += 1
    raise RuntimeError(f"No available ports found at or above {preferred_port}.")


def assign_available_ports(args: argparse.Namespace) -> None:
    backend_port = find_available_port(args.backend_host, args.backend_port)
    frontend_port = find_available_port(args.frontend_host, args.frontend_port)

    if backend_port != args.backend_port:
        print(f"Backend port {args.backend_port} is in use; using {backend_port} instead.", flush=True)
        args.backend_port = backend_port
    if frontend_port != args.frontend_port:
        print(f"Frontend port {args.frontend_port} is in use; using {frontend_port} instead.", flush=True)
        args.frontend_port = frontend_port


def wait_for_backend_ready(
    backend: subprocess.Popen[str],
    *,
    host: str,
    port: int,
    timeout_seconds: float = 30.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    health_url = f"http://{host}:{port}/health"
    request = Request(health_url, headers={"User-Agent": "Codex Dev Launcher"})

    while time.monotonic() < deadline:
        if backend.poll() is not None:
            raise RuntimeError(f"Backend exited with status {backend.returncode} before becoming ready.")
        try:
            with urlopen(request, timeout=2) as response:
                if 200 <= response.status < 300:
                    return
        except URLError:
            pass
        except TimeoutError:
            pass
        time.sleep(0.2)

    raise RuntimeError(f"Backend did not become ready within {timeout_seconds:.0f}s.")


def launch_processes(args: argparse.Namespace) -> tuple[subprocess.Popen[str], subprocess.Popen[str]]:
    backend_python, frontend_node_modules = required_paths()
    if not backend_python.exists():
        raise SystemExit(fail_prereq("Missing backend virtualenv at `backend/.venv`."))
    if not frontend_node_modules.exists():
        raise SystemExit(fail_prereq("Missing frontend dependencies at `frontend/node_modules`."))

    backend_cmd = [
        str(backend_python),
        "-m",
        "uvicorn",
        "app.main:app",
        "--reload",
        "--host",
        args.backend_host,
        "--port",
        str(args.backend_port),
    ]
    frontend_cmd = [
        "npm",
        "run",
        "dev",
        "--",
        "--host",
        args.frontend_host,
        "--port",
        str(args.frontend_port),
        "--strictPort",
    ]

    frontend_env = os.environ.copy()
    frontend_env["VITE_API_BASE_URL"] = "/api"
    frontend_env["VITE_BACKEND_TARGET"] = f"http://{args.backend_host}:{args.backend_port}"

    backend = subprocess.Popen(
        backend_cmd,
        cwd=BACKEND_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if backend.stdout is None:
        raise RuntimeError("Failed to capture backend output.")

    threading.Thread(
        target=prefixed_stream, args=("backend", backend.stdout), daemon=True
    ).start()

    wait_for_backend_ready(backend, host=args.backend_host, port=args.backend_port)

    frontend = subprocess.Popen(
        frontend_cmd,
        cwd=FRONTEND_DIR,
        env=frontend_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    if frontend.stdout is None:
        raise RuntimeError("Failed to capture frontend output.")

    threading.Thread(
        target=prefixed_stream, args=("frontend", frontend.stdout), daemon=True
    ).start()

    return backend, frontend


def terminate(process: subprocess.Popen[str], name: str) -> None:
    if process.poll() is not None:
        return
    print(f"Stopping {name}...")
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    args = parse_args()
    assign_available_ports(args)
    backend, frontend = launch_processes(args)
    processes = {"backend": backend, "frontend": frontend}
    stop_requested = False
    interrupted = False

    def request_stop(_: int, __) -> None:
        nonlocal interrupted, stop_requested
        interrupted = True
        stop_requested = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    print(
        "Starting development stack:\n"
        f"  API:    http://{args.backend_host}:{args.backend_port}\n"
        f"  Viewer: http://{args.frontend_host}:{args.frontend_port}",
        flush=True,
    )

    exit_code = 0
    while True:
        if stop_requested:
            break

        for name, process in processes.items():
            code = process.poll()
            if code is not None:
                if not interrupted:
                    exit_code = code
                if code != 0 and not interrupted:
                    print(f"{name} exited with status {code}.")
                stop_requested = True
                break

        if stop_requested:
            break
        time.sleep(0.2)

    for name, process in processes.items():
        terminate(process, name)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
