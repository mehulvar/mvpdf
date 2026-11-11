"""Local viewer service behind the "MVPDF Viewer" Mac app.

    mvpdf_viewer.py open FILE   convert (if PDF), start the service if needed, open a browser tab
    mvpdf_viewer.py serve       run the service in the foreground
    mvpdf_viewer.py home        open the viewer's start page

The service listens on 127.0.0.1 only, on a fixed port so the browser keeps one origin (and
one cached copy of the search model). Files are only served after the local `open` command
registers them, under an unguessable token; arbitrary paths are never served.
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8765
ORIGIN = f"http://{HOST}:{PORT}"
ALLOWED_HOSTS = frozenset({f"{HOST}:{PORT}", f"localhost:{PORT}"})
CLIENT_HEADER = "X-MVPDF-Client"  # custom header: browsers can't send it cross-site without CORS
IDLE_SHUTDOWN_SECONDS = 3 * 60 * 60
IDLE_CHECK_SECONDS = 60
STARTUP_WAIT_SECONDS = 8.0
MAX_REQUEST_BYTES = 8 * 1024

ROOT = Path(__file__).resolve().parents[1]
READER_DIR = ROOT / "reader"
COMPILER_DIR = ROOT / "compiler"
LOG_DIR = Path.home() / "Library" / "Logs" / "MVPDF"
FALLBACK_OUTPUT_DIR = Path.home() / "Documents" / "MVPDF"


class Registry:
    """token -> file path, shared by the request threads."""

    def __init__(self) -> None:
        self._paths: dict[str, Path] = {}
        self._lock = threading.Lock()

    def add(self, path: Path) -> str:
        token = secrets.token_urlsafe(24)
        with self._lock:
            self._paths[token] = path
        return token

    def get(self, token: str) -> Path | None:
        with self._lock:
            return self._paths.get(token)


class ViewerHandler(SimpleHTTPRequestHandler):
    registry: Registry
    activity: list[float]

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"{self.log_date_time_string()} {fmt % args}\n")

    def end_headers(self) -> None:
        if not self.path.startswith("/api/"):
            # Always revalidate the viewer's own files, so updates are never stale.
            self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def _host_ok(self) -> bool:
        # Blocks DNS rebinding: a hostile site resolving its own name to 127.0.0.1.
        return self.headers.get("Host", "") in ALLOWED_HOSTS

    def _json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def list_directory(self, path):  # never expose directory listings
        self.send_error(HTTPStatus.NOT_FOUND)
        return None

    def do_GET(self) -> None:
        self.activity[0] = time.monotonic()
        if not self._host_ok():
            self.send_error(HTTPStatus.FORBIDDEN, "Bad host")
            return
        route = urllib.parse.urlsplit(self.path).path
        if route == "/api/health":
            self._json(HTTPStatus.OK, {"ok": True, "service": "mvpdf"})
        elif route.startswith("/api/doc/"):
            self._send_document(route.removeprefix("/api/doc/"))
        else:
            super().do_GET()

    def _send_document(self, token: str) -> None:
        path = self.registry.get(token)
        if path is None or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Unknown or expired document link")
            return
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-mvpdf")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        self.activity[0] = time.monotonic()
        if not self._host_ok() or self.headers.get(CLIENT_HEADER) != "1":
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if urllib.parse.urlsplit(self.path).path != "/api/open":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length", "0") or 0)
        if not 0 < length <= MAX_REQUEST_BYTES:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Bad request size"})
            return
        try:
            path = Path(json.loads(self.rfile.read(length))["path"]).expanduser().resolve()
        except (ValueError, KeyError, TypeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Expected {\"path\": ...}"})
            return
        if path.suffix.lower() != ".mvpdf" or not path.is_file():
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Not an existing .mvpdf file"})
            return
        self._json(HTTPStatus.OK, {"token": self.registry.add(path)})


def make_handler(registry: Registry, activity: list[float]):
    bound = type("BoundViewerHandler", (ViewerHandler,), {"registry": registry, "activity": activity})
    return partial(bound, directory=str(READER_DIR))


def _watch_idle(server: ThreadingHTTPServer, activity: list[float]) -> None:
    while True:
        time.sleep(IDLE_CHECK_SECONDS)
        if time.monotonic() - activity[0] > IDLE_SHUTDOWN_SECONDS:
            server.shutdown()
            return


def serve() -> int:
    activity = [time.monotonic()]
    try:
        server = ThreadingHTTPServer((HOST, PORT), make_handler(Registry(), activity))
    except OSError as err:
        print(f"Cannot listen on {ORIGIN}: {err}", file=sys.stderr)
        return 1
    threading.Thread(target=_watch_idle, args=(server, activity), daemon=True).start()
    print(f"MVPDF viewer running at {ORIGIN}", file=sys.stderr)
    with server:
        server.serve_forever()
    return 0


def _healthy() -> bool:
    try:
        with urllib.request.urlopen(f"{ORIGIN}/api/health", timeout=0.5) as response:
            return json.load(response).get("service") == "mvpdf"
    except (urllib.error.URLError, OSError, ValueError):
        return False


def ensure_server() -> None:
    if _healthy():
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOG_DIR / "viewer.log", "ab") as log:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "serve"],
            stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True,
        )
    deadline = time.monotonic() + STARTUP_WAIT_SECONDS
    while time.monotonic() < deadline:
        if _healthy():
            return
        time.sleep(0.2)
    raise RuntimeError(f"Viewer service did not start; see {LOG_DIR / 'viewer.log'}")


def register(path: Path) -> str:
    request = urllib.request.Request(
        f"{ORIGIN}/api/open",
        data=json.dumps({"path": str(path)}).encode(),
        headers={"Content-Type": "application/json", CLIENT_HEADER: "1"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.load(response)["token"]


def notify(message: str) -> None:
    """Best-effort macOS notification; skipped where osascript doesn't exist."""
    script = f"display notification {json.dumps(message)} with title \"MVPDF Viewer\""
    try:
        subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
    except FileNotFoundError:
        pass


def output_for(pdf: Path) -> Path:
    """Next to the PDF, or ~/Documents/MVPDF when that folder is read-only (e.g. a DMG)."""
    if os.access(pdf.parent, os.W_OK):
        return pdf.with_suffix(".mvpdf")
    FALLBACK_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return FALLBACK_OUTPUT_DIR / f"{pdf.stem}.mvpdf"


def convert(source: Path, output: Path, source_url: str | None = None) -> Path:
    """Run the converter (PDF, HTML or text) in a child process; raises RuntimeError on failure."""
    extra = ["--source-url", source_url] if source_url else []
    result = subprocess.run(
        [sys.executable, "-m", "mvpdf", str(source), "-o", str(output), *extra],
        cwd=COMPILER_DIR, capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr.strip().splitlines() or ["unknown error"])[-1]
        raise RuntimeError(f"Could not convert {source.name}: {detail.removeprefix('error: ')}")
    return output


def viewer_url(path: Path) -> str:
    """Start the service if needed, register the file, and return the tab URL that shows it."""
    ensure_server()
    query = urllib.parse.urlencode({"doc": register(path), "name": path.name})
    return f"{ORIGIN}/?{query}"


def open_in_browser(url: str) -> None:
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.run([opener, url], check=False)


def open_file(raw_path: str) -> int:
    path = Path(raw_path).expanduser().resolve()
    try:
        if not path.is_file():
            raise RuntimeError(f"File not found: {path}")
        if path.suffix.lower() == ".pdf":
            notify(f"Converting {path.name} to .mvpdf…")
            path = convert(path, output_for(path))
        elif path.suffix.lower() != ".mvpdf":
            raise RuntimeError(f"{path.name} is not a .mvpdf or .pdf file")
        url = viewer_url(path)
    except (RuntimeError, OSError, urllib.error.URLError) as err:
        print(f"error: {err}", file=sys.stderr)
        notify(str(err))
        return 1
    open_in_browser(url)
    return 0


def main(argv: list[str]) -> int:
    command = argv[1] if len(argv) > 1 else "home"
    if command == "serve":
        return serve()
    if command == "open" and len(argv) == 3:
        return open_file(argv[2])
    if command == "home":
        ensure_server()
        open_in_browser(f"{ORIGIN}/")
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
