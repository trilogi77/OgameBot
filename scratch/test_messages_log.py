import os, sys, json, tempfile, logging, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import brain

class FakeClient:
    def __init__(self, by_tab): self.by_tab = by_tab
    def read_message_reports(self, tab): return self.by_tab.get(tab, [])

cfg = types.SimpleNamespace(enable_spy_watch=False, spy_watch_messages=False,
                            telegram_token="", telegram_chat_id="")
fake = types.SimpleNamespace(
    log=logging.getLogger("t"), cfg=cfg,
    client=FakeClient({
        22: [{"id": "1", "text": "Resultado de la expedicion\nNave de batalla 285\nSonda de espionaje 1", "raw": {}}],
        21: [{"id": "2", "text": "Has ganado la batalla. Metal: 1.000 Cristal: 500", "raw": {}},
             {"id": "3", "text": "El atacante ha ganado. Te han saqueado.", "raw": {}}],
        24: [],
    }),
)

os.chdir(tempfile.mkdtemp())
brain.Brain.update_imperial_stats(fake)

data = json.load(open("messages_read.json", encoding="utf-8"))
msgs = {m["key"]: m for m in data["messages"]}

# Los 3 mensajes quedan registrados (incluido el combate perdido, marcado como no contabilizado).
assert set(msgs) == {"22-1", "21-2", "21-3"}, list(msgs)
# La expedicion resume con nombres en espanol, no claves internas.
s = msgs["22-1"]["summary"].lower()
assert "nave de batalla" in s and "battleship" not in s, msgs["22-1"]["summary"]
assert "sonda de espionaje" in s, msgs["22-1"]["summary"]
# El combate ganado contabiliza recursos; el perdido se marca omitido.
assert "Metal 1.000" in msgs["21-2"]["summary"], msgs["21-2"]["summary"]
assert "no contabilizado" in msgs["21-3"]["summary"], msgs["21-3"]["summary"]
# Texto integro preservado.
assert "Resultado de la expedicion" in msgs["22-1"]["text"]

print("OK", [(m["category"], m["summary"]) for m in data["messages"]])
