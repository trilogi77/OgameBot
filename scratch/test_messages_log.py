import os, sys, json, tempfile, logging, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import brain

class FakeClient:
    def __init__(self, by_tab): self.by_tab = by_tab
    def read_message_reports(self, tab): return self.by_tab.get(tab, [])

def run(by_tab, cfg, cwd):
    fake = types.SimpleNamespace(log=logging.getLogger("t"), cfg=cfg, client=FakeClient(by_tab))
    os.chdir(cwd)
    brain.Brain.update_imperial_stats(fake)
    with open("messages_read.json", encoding="utf-8") as f:
        return {m["key"]: m for m in json.load(f)["messages"]}

# --- 1) Caso base: combate ganado/perdido + expedicion, sin espionaje ---
cfg_base = types.SimpleNamespace(enable_spy_watch=False, spy_watch_messages=False,
                                 telegram_token="", telegram_chat_id="")
msgs = run({
    22: [{"id": "1", "text": "Resultado de la expedicion\nNave de batalla 285\nSonda de espionaje 1", "raw": {}}],
    21: [{"id": "2", "text": "Has ganado la batalla. Metal: 1.000", "raw": {}},
         {"id": "3", "text": "El atacante ha ganado. Te han saqueado.", "raw": {}}],
}, cfg_base, tempfile.mkdtemp())
assert set(msgs) == {"22-1", "21-2", "21-3"}, list(msgs)
s = msgs["22-1"]["summary"].lower()
assert "nave de batalla" in s and "battleship" not in s, msgs["22-1"]["summary"]
assert "sonda de espionaje" in s, msgs["22-1"]["summary"]
assert "no contabilizado" in msgs["21-3"]["summary"], msgs["21-3"]["summary"]
assert "Resultado de la expedicion" in msgs["22-1"]["text"]

# --- 2) Arranque: un mensaje ya marcado leido debe registrar su texto igual ---
d = tempfile.mkdtemp()
with open(os.path.join(d, "ogbot_stats.json"), "w", encoding="utf-8") as f:
    json.dump({"parsed_messages": ["22-9"]}, f)
msgs = run({22: [{"id": "9", "text": "Expedicion vieja ya leida al arrancar", "raw": {}}]}, cfg_base, d)
assert "22-9" in msgs, "mensaje preexistente NO registrado (bug de arranque)"
assert "ya leida al arrancar" in msgs["22-9"]["text"]

# --- 3) Espionaje (tab 20) se registra aunque Telegram NO este configurado ---
cfg_spy = types.SimpleNamespace(enable_spy_watch=True, spy_watch_messages=True,
                                telegram_token="", telegram_chat_id="")
msgs = run({20: [{"id": "5", "text": "Se ha detectado una flota del planeta [1:2:3] cerca de tu planeta [4:5:6]", "raw": {}}]},
           cfg_spy, tempfile.mkdtemp())
assert "20-5" in msgs and msgs["20-5"]["category"] == "Espionaje", msgs
assert "Te han espiado" in msgs["20-5"]["summary"] and "Telegram" not in msgs["20-5"]["summary"]

print("OK")
