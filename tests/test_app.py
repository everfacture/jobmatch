import threading
import urllib.error
import urllib.parse
import urllib.request

from jobmatch.app import HOST, Handler, pick_port
from http.server import ThreadingHTTPServer


def _serve():
    port = pick_port(8791)
    httpd = ThreadingHTTPServer((HOST, port), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, port


def test_app_setup_page_when_not_configured(monkeypatch):
    monkeypatch.setattr("jobmatch.app.is_configured", lambda: False)
    httpd, port = _serve()
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/", timeout=3) as resp:
            body = resp.read().decode("utf-8")
        assert resp.status == 200
        assert "Paste your CV" in body
        assert "API key" in body
        assert "deepseek" in body
    finally:
        httpd.shutdown()


def test_app_setup_post_saves_and_does_not_echo_missing_config(monkeypatch):
    saved = {}

    def fake_apply(**kwargs):
        saved.update(kwargs)

    monkeypatch.setattr("jobmatch.app.apply_simple_setup", fake_apply)
    monkeypatch.setattr("jobmatch.app.is_configured", lambda: True)
    httpd, port = _serve()
    try:
        data = urllib.parse.urlencode(
            {
                "full_name": "Ada",
                "email": "ada@example.com",
                "country": "UK",
                "city": "London",
                "location": "Remote",
                "roles": "Buyer, Planner",
                "provider": "openai",
                "api_key": "sk-secret",
                "resume_text": "Built things.",
            }
        ).encode()
        req = urllib.request.Request(f"http://{HOST}:{port}/setup", data=data, method="POST")
        with urllib.request.urlopen(req, timeout=3) as resp:
            body = resp.read().decode("utf-8")
        assert "Saved" in body
        assert "sk-secret" not in body
        assert saved["api_key"] == "sk-secret"
        assert saved["roles"] == ["Buyer", "Planner"]
        assert saved["provider"] == "openai"
    finally:
        httpd.shutdown()


def test_app_setup_post_shows_validation_error(monkeypatch):
    def fake_apply(**kwargs):
        raise ValueError("Paste an API key.")

    monkeypatch.setattr("jobmatch.app.apply_simple_setup", fake_apply)
    monkeypatch.setattr("jobmatch.app.is_configured", lambda: False)
    httpd, port = _serve()
    try:
        data = urllib.parse.urlencode({"full_name": "Ada", "roles": "Buyer"}).encode()
        req = urllib.request.Request(f"http://{HOST}:{port}/setup", data=data, method="POST")
        try:
            urllib.request.urlopen(req, timeout=3)
            raise AssertionError("expected HTTP error")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            assert exc.code == 400
            assert "Paste an API key." in body
    finally:
        httpd.shutdown()
