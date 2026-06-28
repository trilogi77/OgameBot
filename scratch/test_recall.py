import os, sys, json, tempfile, logging, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot.brain import Brain


def fake(recall_result):
    calls = []
    client = types.SimpleNamespace(
        recall_fleet=lambda o, d, mission="", arrival=0: (calls.append((o, d, mission, arrival)) or recall_result))
    f = types.SimpleNamespace(log=logging.getLogger("t"), client=client)
    f._process_recall_requests = types.MethodType(Brain._process_recall_requests, f)
    return f, calls


os.chdir(tempfile.mkdtemp())

# 1) Éxito -> ejecuta el recall (con misión y llegada) y consume la petición.
with open("recall_requests.json", "w", encoding="utf-8") as fh:
    json.dump([{"origin": "1:2:3", "destination": "1:2:4", "mission_code": "3", "arrival": 1000}], fh)
f, calls = fake(True)
f._process_recall_requests()
assert calls == [("1:2:3", "1:2:4", "3", 1000)], calls
assert not os.path.exists("recall_requests.json")

# 2) Fallo -> re-encola con _attempts incrementado (no se pierde).
with open("recall_requests.json", "w", encoding="utf-8") as fh:
    json.dump([{"origin": "1:2:3", "destination": "1:2:4", "mission_code": "3"}], fh)
f, _ = fake(False)
f._process_recall_requests()
assert os.path.exists("recall_requests.json")
req = json.load(open("recall_requests.json", encoding="utf-8"))
assert req[0]["_attempts"] == 1, req

# 3) Tras 3 intentos -> se descarta (no re-encola).
with open("recall_requests.json", "w", encoding="utf-8") as fh:
    json.dump([{"origin": "1:2:3", "destination": "1:2:4", "mission_code": "3", "_attempts": 2}], fh)
f, _ = fake(False)
f._process_recall_requests()
assert not os.path.exists("recall_requests.json")

print("OK")
