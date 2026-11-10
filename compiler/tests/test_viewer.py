import http.client
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "viewer"))
import mvpdf_viewer as viewer  # noqa: E402

GOOD_HOST = f"{viewer.HOST}:{viewer.PORT}"


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), viewer.make_handler(viewer.Registry(), [0.0]))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def request(port, method, path, host=GOOD_HOST, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.putrequest(method, path, skip_host=True)
    conn.putheader("Host", host)
    for key, value in (headers or {}).items():
        conn.putheader(key, value)
    data = json.dumps(body).encode() if body is not None else b""
    conn.putheader("Content-Length", str(len(data)))
    conn.endheaders(data)
    response = conn.getresponse()
    return response.status, response.read()


def register(port, path, headers=None):
    return request(port, "POST", "/api/open", body={"path": str(path)},
                   headers=headers if headers is not None else {viewer.CLIENT_HEADER: "1"})


def test_health(server):
    status, body = request(server, "GET", "/api/health")
    assert status == 200 and json.loads(body)["service"] == "mvpdf"


def test_serves_reader_page(server):
    status, body = request(server, "GET", "/")
    assert status == 200 and b"MVPDF Viewer" in body


def test_rejects_foreign_host_header(server):
    assert request(server, "GET", "/api/health", host="evil.example:8765")[0] == 403


def test_registration_requires_client_header(server, tmp_path):
    doc = tmp_path / "a.mvpdf"
    doc.write_bytes(b"data")
    assert register(server, doc, headers={})[0] == 403


def test_registration_rejects_non_mvpdf_files(server, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("private")
    assert register(server, secret)[0] == 400
    assert register(server, tmp_path / "missing.mvpdf")[0] == 400


def test_registered_document_is_served_by_token_only(server, tmp_path):
    doc = tmp_path / "a.mvpdf"
    doc.write_bytes(b"\x89MVPDF payload")
    status, body = register(server, doc)
    token = json.loads(body)["token"]

    assert status == 200 and len(token) >= 24
    assert request(server, "GET", f"/api/doc/{token}") == (200, b"\x89MVPDF payload")
    assert request(server, "GET", "/api/doc/guessed-token")[0] == 404


def test_directory_listing_is_disabled(server):
    assert request(server, "GET", "/src/")[0] == 404


def test_output_for_falls_back_when_folder_is_read_only(tmp_path, monkeypatch):
    pdf = tmp_path / "report.pdf"
    assert viewer.output_for(pdf) == tmp_path / "report.mvpdf"
    monkeypatch.setattr(viewer.os, "access", lambda *_: False)
    monkeypatch.setattr(viewer, "FALLBACK_OUTPUT_DIR", tmp_path / "fallback")
    assert viewer.output_for(pdf) == tmp_path / "fallback" / "report.mvpdf"


def test_open_file_reports_unsupported_types(tmp_path, monkeypatch):
    monkeypatch.setattr(viewer, "notify", lambda message: None)
    other = tmp_path / "x.docx"
    other.write_text("hi")
    assert viewer.open_file(str(other)) == 1
    assert viewer.open_file(str(tmp_path / "missing.mvpdf")) == 1
