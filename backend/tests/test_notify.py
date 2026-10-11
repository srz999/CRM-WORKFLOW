"""Optional n8n webhook: payload contract (receiver mocked locally)."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.api.conversations import upsert_conversation
from app.config import get_settings
from app.workflow.runner import run_workflow

from .fake_llm import SAMPLE_TEXT, FakeLLM


def test_ticket_created_webhook_payload(monkeypatch):
    received = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(get_settings(), "n8n_webhook_url", f"http://127.0.0.1:{srv.server_port}/webhook")

    run_workflow(upsert_conversation(SAMPLE_TEXT["S01"], "CUST-1001"), FakeLLM())
    for _ in range(50):
        if received:
            break
        time.sleep(0.05)
    srv.shutdown()
    assert received and received[0]["event"] == "tickets.created"
    prios = sorted(t["priority"] for t in received[0]["tickets"])
    assert prios == ["MEDIUM", "URGENT"]
    assert all(t["ticket_number"].startswith("TKT-") and t["team"] for t in received[0]["tickets"])
