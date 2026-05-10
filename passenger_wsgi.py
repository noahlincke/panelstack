from __future__ import annotations

import os
from pathlib import Path
import sys

from a2wsgi import ASGIMiddleware


APP_ROOT = Path(__file__).resolve().parent
ENV_PATH = APP_ROOT / ".env"

if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


load_env_file(ENV_PATH)
if "APP_SESSION_SECRET" not in os.environ and "SESSION_SECRET" in os.environ:
    os.environ["APP_SESSION_SECRET"] = os.environ["SESSION_SECRET"]
os.environ.setdefault("PANELSTACK_STATIC_DIR", str(APP_ROOT / "frontend" / "dist"))
os.environ.setdefault("PANELSTACK_BASE_PATH", "/panels")
os.environ.setdefault("PANELSTACK_SYNC_PROVIDERS_ON_STARTUP", "0")
os.environ.setdefault("PANELSTACK_HOSTED_DEPLOYMENT", "1")
os.environ.setdefault("PANELSTACK_ENABLE_REMOTE_COVER_FETCH", "0")

from backend.app.passenger import passenger_asgi_app  # noqa: E402


asgi_wsgi_application = ASGIMiddleware(passenger_asgi_app)


def application(environ, start_response):
    script_name = environ.get("SCRIPT_NAME", "")
    path_info = environ.get("PATH_INFO", "")
    if script_name and path_info.startswith("/") and not path_info.startswith(script_name):
        environ = environ.copy()
        environ["SCRIPT_NAME"] = ""
        environ["PATH_INFO"] = f"{script_name}{path_info}"
    return asgi_wsgi_application(environ, start_response)
