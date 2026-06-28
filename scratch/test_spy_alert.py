import os, sys, json, csv, tempfile, logging, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import brain

sent = []
send_ok = [True]   # controla si el "envío" de Telegram tiene éxito


def fake_send(token, chat, text, logger=None, block=False):
    if send_ok[0]:
        sent.append((chat, text))
        return True
    return False


brain.utils.send_telegram_message = fake_send


class FakeClient:
    def __init__(self, by_tab): self.by_tab = by_tab
    def read_message_reports(self, tab): return self.by_tab.get(tab, [])


def spy(mid, frm="1:2:3", to="4:5:6"):
    return {"id": mid,
            "text": f"Se ha detectado una flota del planeta [{frm}] cerca de tu planeta [{to}]. "
                    f"Probabilidad de contraespionaje: 30%",
            "raw": {}}


def make_fake(msgs):
    cfg = types.SimpleNamespace(enable_spy_watch=True, spy_watch_messages=True,
                                telegram_token="T", telegram_chat_id="C")
    fake = types.SimpleNamespace(log=logging.getLogger("t"), cfg=cfg,
                                 client=FakeClient({20: msgs}),
                                 SPY_LEDGER_FILE=brain.Brain.SPY_LEDGER_FILE)
    fake._process_spy_messages = types.MethodType(brain.Brain._process_spy_messages, fake)
    return fake


def run(fake):
    with open("ogbot_stats.json", "w", encoding="utf-8") as f:
        json.dump({"parsed_messages": []}, f)  # se reinicia cada arranque; el CSV no
    sent.clear()
    brain.Brain.update_imperial_stats(fake)


def ledger():
    with open("spy_notifications.csv", "r", encoding="utf-8", newline="") as f:
        return {r["msg_id"]: r for r in csv.DictReader(f)}


os.chdir(tempfile.mkdtemp())

# 1) Primer arranque (sin CSV): el aviso existente es backlog -> NO avisa, pero queda anotado.
run(make_fake([spy("7")]))
assert sent == [], sent
assert ledger()["20-7"]["notified"] == "1", "el backlog debe quedar marcado como ya gestionado"

# 2) Con el CSV ya existente, llega un aviso NUEVO -> avisa una sola vez.
run(make_fake([spy("7"), spy("8")]))
assert len(sent) == 1 and "Te han espiado" in sent[0][1], sent
assert ledger()["20-8"]["notified"] == "1"

# 3) Mismo aviso de nuevo -> ya notificado, no repite.
run(make_fake([spy("7"), spy("8")]))
assert sent == [], sent

# 4) Telegram falla con un aviso nuevo -> se anota como pendiente (notified=0).
send_ok[0] = False
run(make_fake([spy("7"), spy("8"), spy("9")]))
assert sent == [], sent
assert ledger()["20-9"]["notified"] == "0", "un envio fallido NO debe marcarse notificado"

# 5) Al volver Telegram, el pendiente se reintenta y se notifica.
send_ok[0] = True
run(make_fake([spy("7"), spy("8"), spy("9")]))
assert len(sent) == 1, sent
assert ledger()["20-9"]["notified"] == "1"

print("OK")
