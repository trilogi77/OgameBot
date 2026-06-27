import os, sys, io, json, types, urllib.request, urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import gui

def call(body, urlopen):
    raw = json.dumps(body).encode()
    captured = {}
    fake = types.SimpleNamespace(
        rfile=io.BytesIO(raw),
        headers={"Content-Length": str(len(raw))},
        send_json=lambda status, data: captured.update(status=status, data=data),
    )
    orig = urllib.request.urlopen
    urllib.request.urlopen = urlopen
    try:
        gui.GUIRequestHandler.test_telegram(fake, account=None)
    finally:
        urllib.request.urlopen = orig
    return captured

class _Resp:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return b'{"ok":true}'

# 1) Falta token/chat_id -> 400 con error.
r = call({"token": "", "chat_id": ""}, None)
assert r["status"] == 400 and "error" in r["data"], r

# 2) Envío correcto -> success.
r = call({"token": "T", "chat_id": "C"}, lambda req, timeout=10: _Resp())
assert r["data"].get("status") == "success", r

# 3) Telegram rechaza (401) -> error legible.
def boom(req, timeout=10):
    raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {},
                                 io.BytesIO(b'{"description":"Unauthorized"}'))
r = call({"token": "BAD", "chat_id": "C"}, boom)
assert "error" in r["data"] and "401" in r["data"]["error"], r

print("OK")
