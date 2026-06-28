import os, sys, json, tempfile, logging, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import brain


class FakeClient:
    def __init__(self, by_tab): self.by_tab = by_tab
    def read_message_reports(self, tab): return self.by_tab.get(tab, [])


# Combate ganado SIN data-raw -> se extrae del texto. Varios recursos en una línea:
# antes el regex bidireccional daba a Cristal y Deuterio la cifra del recurso anterior.
win = {"id": "1", "raw": {},
       "text": "Has ganado la batalla. Botín: Metal: 1.000 Cristal: 500 Deuterio: 200"}
cfg = types.SimpleNamespace(enable_spy_watch=False, spy_watch_messages=False,
                            telegram_token="", telegram_chat_id="")
fake = types.SimpleNamespace(log=logging.getLogger("t"), cfg=cfg,
                             client=FakeClient({21: [win]}))
os.chdir(tempfile.mkdtemp())
brain.Brain.update_imperial_stats(fake)

farm = json.load(open("ogbot_stats.json", encoding="utf-8"))["total_farming"]
assert farm["metal"] == 1000, farm
assert farm["crystal"] == 500, farm     # antes salía 1000 (robaba la cifra del metal)
assert farm["deut"] == 200, farm        # antes salía 500

print("OK")
