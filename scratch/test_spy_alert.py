import os, sys, json, tempfile, logging, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import brain

sent = []
brain.utils.send_telegram_message = lambda token, chat, text, logger=None: sent.append((chat, text))

class FakeClient:
    def __init__(self, by_tab): self.by_tab = by_tab
    def read_message_reports(self, tab): return self.by_tab.get(tab, [])

SPY = {"id": "7",
       "text": "Se ha detectado una flota del planeta [1:2:3] cerca de tu planeta [4:5:6]. "
               "Probabilidad de contraespionaje: 30%", "raw": {}}

def run(stats):
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "ogbot_stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f)
    cfg = types.SimpleNamespace(enable_spy_watch=True, spy_watch_messages=True,
                                telegram_token="T", telegram_chat_id="C")
    fake = types.SimpleNamespace(log=logging.getLogger("t"), cfg=cfg, client=FakeClient({20: [SPY]}))
    os.chdir(d)
    sent.clear()
    brain.Brain.update_imperial_stats(fake)

# Aviso NUEVO (no estaba al arrancar) -> manda Telegram.
run({"parsed_messages": [], "spy_seen_at_boot": []})
assert len(sent) == 1, sent
assert "Te han espiado" in sent[0][1]

# Aviso del backlog (ya estaba al arrancar) -> NO manda Telegram.
run({"parsed_messages": [], "spy_seen_at_boot": ["20-7"]})
assert sent == [], sent

print("OK")
