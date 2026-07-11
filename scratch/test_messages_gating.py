"""Fase de lectura de mensajes con gating por no-leídos:
- sobre a 0 -> ni siquiera se entra en mensajes;
- solo se abren las pestañas/grupos que marcan mensajes nuevos (Universo/OGame incluidos);
- cliente antiguo (sin contadores) -> comportamiento clásico (leer todo);
- primera vez sin libro mayor de espionaje -> se siembra aunque el sobre marque 0."""
import os, sys, json, tempfile, logging, types
from types import MethodType
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import brain

log = logging.getLogger("t")


class GatedClient:
    """Cliente falso con la interfaz nueva; registra cada acción en .calls."""
    def __init__(self, unread, categories=None, fleet_tabs=None, by_tab=None, by_cat=None):
        self.unread = unread
        self.categories = categories or {}
        self.fleet_tabs = fleet_tabs
        self.by_tab = by_tab or {}
        self.by_cat = by_cat or {}
        self.calls = []
    def unread_messages_count(self):
        self.calls.append("unread"); return self.unread
    def read_messages_overview(self, expected_unread=None):
        self.calls.append("overview")
        return {"categories": self.categories, "fleet_tabs": self.fleet_tabs}
    def read_message_reports(self, tab):
        self.calls.append(f"tab{tab}"); return self.by_tab.get(tab, [])
    def read_message_category(self, cid):
        self.calls.append(f"cat{cid}"); return self.by_cat.get(cid, [])


class OldClient:
    """Cliente sin contadores (como los tests antiguos): debe leerse todo."""
    def __init__(self, by_tab): self.by_tab = by_tab; self.calls = []
    def read_message_reports(self, tab):
        self.calls.append(f"tab{tab}"); return self.by_tab.get(tab, [])


def run(client, cfg):
    cwd = tempfile.mkdtemp()
    fake = types.SimpleNamespace(log=log, cfg=cfg, client=client,
                                 SPY_LEDGER_FILE=brain.Brain.SPY_LEDGER_FILE)
    fake._process_spy_messages = MethodType(brain.Brain._process_spy_messages, fake)
    os.chdir(cwd)
    brain.Brain.update_imperial_stats(fake)
    try:
        with open("messages_read.json", encoding="utf-8") as f:
            return {m["key"]: m for m in json.load(f)["messages"]}
    except FileNotFoundError:
        return {}


cfg_nospy = types.SimpleNamespace(enable_spy_watch=False, spy_watch_messages=False,
                                  telegram_token="", telegram_chat_id="")

# ---- 1: sobre a 0 -> no se entra en mensajes (ninguna lectura) ----
c = GatedClient(unread=0)
msgs = run(c, cfg_nospy)
assert c.calls == ["unread"], c.calls
assert msgs == {}, msgs

# ---- 2: solo se abren las pestañas con mensajes nuevos (21, Universo, OGame) ----
c = GatedClient(
    unread=4,
    categories={2: 1, 1: 0, 3: 0, 5: 2, 4: 1, 6: 0},
    fleet_tabs={20: 0, 21: 1, 22: 0, 23: 0, 24: 0},
    by_tab={21: [{"id": "7", "text": "Has ganado la batalla. Metal: 1.000", "raw": {}}]},
    by_cat={5: [{"id": "50", "text": "Retransmisión al espacio profundo", "raw": {}}],
            4: [{"id": "60", "text": "¡Logro desbloqueado!", "raw": {}}]},
)
msgs = run(c, cfg_nospy)
assert "tab21" in c.calls and "cat5" in c.calls and "cat4" in c.calls, c.calls
for forbidden in ("tab20", "tab22", "tab23", "tab24"):
    assert forbidden not in c.calls, c.calls
assert set(msgs) == {"21-7", "5-50", "4-60"}, list(msgs)
assert msgs["5-50"]["category"] == "Universo" and msgs["4-60"]["category"] == "OGame", msgs

# ---- 3: Uniones/transporte (23) se abre y registra si marca mensajes ----
c = GatedClient(unread=1, categories={2: 1}, fleet_tabs={20: 0, 21: 0, 22: 0, 23: 1, 24: 0},
                by_tab={23: [{"id": "9", "text": "Tu transporte llegó", "raw": {}}]})
msgs = run(c, cfg_nospy)
assert "tab23" in c.calls and "23-9" in msgs, (c.calls, list(msgs))
assert msgs["23-9"]["category"] == "Uniones/transporte", msgs["23-9"]

# ---- 4: cliente antiguo (sin contadores) -> lee todas las pestañas como siempre ----
c = OldClient({22: [{"id": "1", "text": "Resultado de la expedicion", "raw": {}}]})
msgs = run(c, cfg_nospy)
assert {"tab21", "tab22", "tab24"} <= set(c.calls), c.calls
assert "22-1" in msgs

# ---- 5: sobre a 0 pero SIN libro mayor de espionaje -> se siembra (lee tab 20) ----
cfg_spy = types.SimpleNamespace(enable_spy_watch=True, spy_watch_messages=True,
                                telegram_token="", telegram_chat_id="")
c = GatedClient(unread=0, categories={2: 0}, fleet_tabs={20: 0, 21: 0, 22: 0, 23: 0, 24: 0})
run(c, cfg_spy)   # cwd nuevo: spy_notifications.csv no existe todavía
assert "tab20" in c.calls, c.calls

# ---- 6: contadores rotos (fleet_tabs=None) con sobre > 0 -> leer todo (fail-open) ----
c = GatedClient(unread=2, categories={}, fleet_tabs=None)
run(c, cfg_nospy)
assert {"tab21", "tab22", "tab24"} <= set(c.calls), c.calls

# ---- 7: aviso pendiente de Telegram (notified=0) -> la fase corre aunque el sobre marque 0
# y, si el mensaje ya no existe en la pestaña, la fila se cierra (no bloquea para siempre) ----
cfg_tg = types.SimpleNamespace(enable_spy_watch=True, spy_watch_messages=True,
                               telegram_token="tok", telegram_chat_id="42")
d7 = tempfile.mkdtemp()
with open(os.path.join(d7, brain.Brain.SPY_LEDGER_FILE), "w", encoding="utf-8", newline="") as f:
    f.write("msg_id,from,to,detected_at,notified,notified_at\n")
    f.write("20-99,[1:2:3],[4:5:6],2026-07-10 10:00:00,0,\n")
c = GatedClient(unread=0, categories={2: 0}, fleet_tabs={20: 0, 21: 0, 22: 0, 23: 0, 24: 0})
os.chdir(d7)
fake = types.SimpleNamespace(log=log, cfg=cfg_tg, client=c,
                             SPY_LEDGER_FILE=brain.Brain.SPY_LEDGER_FILE)
fake._process_spy_messages = MethodType(brain.Brain._process_spy_messages, fake)
brain.Brain.update_imperial_stats(fake)
assert "tab20" in c.calls, c.calls               # el reintento pendiente fuerza la lectura
import csv as _csv
with open(os.path.join(d7, brain.Brain.SPY_LEDGER_FILE), encoding="utf-8", newline="") as f:
    rows = {r["msg_id"]: r for r in _csv.DictReader(f)}
# GatedClient no implementa message_tab_active -> lectura vacía NO verificada -> no se cierra
assert rows["20-99"]["notified"] == "0", rows
# Con lectura verificada (message_tab_active True) y el mensaje desaparecido -> se cierra
c.message_tab_active = lambda tab: True
brain.Brain.update_imperial_stats(fake)
with open(os.path.join(d7, brain.Brain.SPY_LEDGER_FILE), encoding="utf-8", newline="") as f:
    rows = {r["msg_id"]: r for r in _csv.DictReader(f)}
assert rows["20-99"]["notified"] == "1", rows
# Cerrada la fila, el siguiente ciclo con sobre a 0 vuelve a omitir la fase entera
c.calls.clear()
brain.Brain.update_imperial_stats(fake)
assert c.calls == ["unread"], c.calls

# ---- 8: el aterrizaje consume no-leídos de la subpestaña visible -> el overview los
# reasigna con el déficit del sobre (client.read_messages_overview) ----
from ogbot.client import GameClient

class FakePage:
    def __init__(self, st):
        self.st = st
        self.url = "https://s1-es.ogame.gameforge.com/game/index.php?page=ingame&component=messages"
    def evaluate(self, js): return self.st
    def wait_for_selector(self, sel, timeout=None): return None

st = {"cats": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0, "6": 0}, "active": 2, "activeSub": 20,
      "subs": {"20": 0, "21": 0, "22": 0, "23": 0, "24": 0}}
gc = object.__new__(GameClient)
gc.page = FakePage(st)
gc.log = log
ov = gc.read_messages_overview(expected_unread=1)   # el sobre decía 1; los badges suman 0
assert ov["fleet_tabs"][20] == 1 and ov["categories"][2] == 1, ov
ov = gc.read_messages_overview(expected_unread=0)   # sin déficit no se inventa nada
assert ov["fleet_tabs"] == {20: 0, 21: 0, 22: 0, 23: 0, 24: 0}, ov

print("OK")
