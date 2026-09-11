"""Local browser app for people who should not have to use a terminal.

Binds to 127.0.0.1 only. First-run form, then Run → shortlist.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from jobmatch.config import APP_DIR, ENV_PATH, ensure_dirs, load_env
from jobmatch.llm.providers import SIMPLE_PROVIDERS
from jobmatch.simple_setup import apply_simple_setup, is_configured

HOST = "127.0.0.1"
DEFAULT_PORT = 8787
RUN_STATE_PATH = APP_DIR / "app-run.json"
RUN_LOG_PATH = APP_DIR / "logs" / "app-run.log"

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>JobMatch</title>
<style>
  :root { color-scheme: light; }
  body { margin: 0; font: 16px/1.45 Georgia, "Times New Roman", serif; background: #f4efe6; color: #1b1b1b; }
  main { max-width: 36rem; margin: 0 auto; padding: 2rem 1.25rem 4rem; }
  h1 { font-size: 1.6rem; font-weight: 600; margin: 0 0 .4rem; }
  p.lede { margin: 0 0 1.5rem; color: #3a3a3a; }
  label { display: block; font-size: .92rem; margin: 1rem 0 .3rem; }
  input, textarea, select { width: 100%; box-sizing: border-box; padding: .55rem .6rem; border: 1px solid #c8c1b4; background: #fff; font: 16px/1.4 system-ui, sans-serif; }
  textarea { min-height: 9rem; }
  button { margin-top: 1.25rem; padding: .7rem 1rem; border: 0; background: #1b1b1b; color: #fff; font: 600 1rem/1.2 system-ui, sans-serif; cursor: pointer; }
  button:disabled { opacity: .5; cursor: default; }
  .note { font-size: .88rem; color: #555; margin-top: .8rem; }
  .err { background: #f8e4e0; padding: .7rem .8rem; margin-bottom: 1rem; }
  .ok { background: #e4efe6; padding: .7rem .8rem; margin-bottom: 1rem; }
  a { color: #1b1b1b; }
  .row { display: flex; gap: .75rem; flex-wrap: wrap; }
  .row > * { flex: 1 1 10rem; }
</style>
</head>
<body>
<main>
{body}
</main>
</body>
</html>
"""


def _page(body: str) -> bytes:
    return _PAGE.replace("{body}", body).encode("utf-8")


def _esc(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _read_run_state() -> dict:
    if not RUN_STATE_PATH.is_file():
        return {"status": "idle"}
    try:
        data = json.loads(RUN_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "idle"}
    return data if isinstance(data, dict) else {"status": "idle"}


def _write_run_state(data: dict) -> None:
    ensure_dirs()
    RUN_STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _tail_log(limit: int = 4000) -> str:
    if not RUN_LOG_PATH.is_file():
        return ""
    text = RUN_LOG_PATH.read_text(encoding="utf-8", errors="replace")
    return text[-limit:]


def _setup_form(error: str = "") -> bytes:
    providers = "\n".join(
        f'<option value="{name}">{name}</option>' for name in SIMPLE_PROVIDERS
    )
    err = f'<div class="err">{_esc(error)}</div>' if error else ""
    body = f"""
{err}
<h1>JobMatch</h1>
<p class="lede">Stays on this computer. Paste your CV, the jobs you want, and one AI key. It does not auto-apply.</p>
<form method="post" action="/setup" enctype="application/x-www-form-urlencoded">
  <label>Your name <input name="full_name" required></label>
  <div class="row">
    <label>Email <input name="email" type="email"></label>
    <label>Country <input name="country" required></label>
  </div>
  <div class="row">
    <label>City <input name="city"></label>
    <label>Where to search <input name="location" value="Remote"></label>
  </div>
  <label>Job titles you want <input name="roles" placeholder="Operations manager, buyer" required></label>
  <label>Paste your CV <textarea name="resume_text" required></textarea></label>
  <label>AI provider
    <select name="provider">{providers}</select>
  </label>
  <label>API key <input name="api_key" type="password" required autocomplete="off"></label>
  <p class="note">DeepSeek is usually cheapest. OpenAI, OpenRouter, and Groq also work. Codex/ChatGPT login is not an API key.</p>
  <button type="submit">Save and continue</button>
</form>
"""
    return _page(body)


def _home(message: str = "") -> bytes:
    state = _read_run_state()
    status = str(state.get("status") or "idle")
    banner = f'<div class="ok">{_esc(message)}</div>' if message else ""
    running = status == "running"
    log = _esc(_tail_log())
    body = f"""
{banner}
<h1>JobMatch</h1>
<p class="lede">Search public job boards, score them against your CV, then open the shortlist. Leave this window open while it runs.</p>
<p>Status: <strong>{_esc(status)}</strong></p>
<form method="post" action="/run">
  <button type="submit" {"disabled" if running else ""}>Find jobs</button>
</form>
<p><a href="/shortlist">Open shortlist</a> · <a href="/setup">Change setup</a></p>
<pre class="note">{log}</pre>
<script>
async function ping() {{
  const r = await fetch("/status");
  const d = await r.json();
  if (d.status === "running") setTimeout(ping, 3000);
  else if (d.status !== "{_esc(status)}") location.reload();
}}
if ("{status}" === "running") ping();
</script>
"""
    return _page(body)


def _start_run() -> None:
    state = _read_run_state()
    if state.get("status") == "running":
        return
    ensure_dirs()
    RUN_LOG_PATH.write_text("Starting search…\n", encoding="utf-8")
    _write_run_state({"status": "running", "started_at": time.time()})

    def worker() -> None:
        load_env()
        env = os.environ.copy()
        env["JOBMATCH_OPEN_DASHBOARD"] = "0"
        env["JOBMATCH_NOTIFY"] = "1"
        cmd = [
            sys.executable,
            "-m",
            "jobmatch",
            "run",
            "--workers",
            "1",
            "--score-limit",
            "40",
        ]
        try:
            with RUN_LOG_PATH.open("a", encoding="utf-8") as log:
                proc = subprocess.run(
                    cmd,
                    env=env,
                    stdout=log,
                    stderr=log,
                    check=False,
                )
            status = "ok" if proc.returncode == 0 else "error"
            _write_run_state({"status": status, "code": proc.returncode})
        except Exception as exc:
            RUN_LOG_PATH.write_text(f"Failed to start: {exc}\n", encoding="utf-8")
            _write_run_state({"status": "error", "error": str(exc)})

    threading.Thread(target=worker, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def _send(self, body: bytes, status: int = 200, content_type: str = "text/html; charset=utf-8") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        parsed = urllib.parse.parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
        return {key: (values[-1] if values else "") for key, values in parsed.items()}

    def do_GET(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == "/status":
            payload = json.dumps(_read_run_state()).encode("utf-8")
            self._send(payload, content_type="application/json")
            return
        if path == "/shortlist":
            from jobmatch.view import generate_dashboard

            try:
                generate_dashboard()
                html = (APP_DIR / "dashboard.html").read_bytes()
            except Exception as exc:
                html = _page(f'<h1>Shortlist</h1><div class="err">{_esc(str(exc))}</div><p><a href="/">Back</a></p>')
            self._send(html)
            return
        if path == "/setup":
            self._send(_setup_form())
            return
        if path in ("/", "/home"):
            if not is_configured():
                self._send(_setup_form())
                return
            self._send(_home())
            return
        self._send(_page("<h1>Not found</h1>"), status=404)

    def do_POST(self) -> None:  # noqa: N802
        path = urllib.parse.urlparse(self.path).path
        if path == "/setup":
            form = self._form()
            roles = [part.strip() for part in form.get("roles", "").split(",") if part.strip()]
            try:
                apply_simple_setup(
                    full_name=form.get("full_name", ""),
                    email=form.get("email", ""),
                    city=form.get("city", ""),
                    country=form.get("country", ""),
                    location=form.get("location", "Remote"),
                    roles=roles,
                    provider=form.get("provider", "deepseek"),
                    api_key=form.get("api_key", ""),
                    resume_text=form.get("resume_text", ""),
                )
            except ValueError as exc:
                self._send(_setup_form(str(exc)), status=400)
                return
            self._send(_home("Saved. Everything is on this computer."))
            return
        if path == "/run":
            if not is_configured():
                self._send(_setup_form("Set up first."), status=400)
                return
            _start_run()
            self._send(_home("Search started. This can take several minutes."))
            return
        self._send(_page("<h1>Not found</h1>"), status=404)


def pick_port(port: int = DEFAULT_PORT) -> int:
    import socket

    for candidate in range(port, port + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((HOST, candidate))
            except OSError:
                continue
            return candidate
    raise RuntimeError("No free local port for JobMatch.")


def serve(port: int | None = None, *, open_browser: bool = True) -> None:
    ensure_dirs()
    if ENV_PATH.is_file():
        load_env()
    chosen = pick_port(port or DEFAULT_PORT)
    httpd = ThreadingHTTPServer((HOST, chosen), Handler)
    url = f"http://{HOST}:{chosen}/"
    print(f"JobMatch is running at {url}")
    print("Leave this window open. Close it to quit.")
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
