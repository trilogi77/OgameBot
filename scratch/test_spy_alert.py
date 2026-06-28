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
    def __init__(self, by_tab, full=None):
        self.by_tab = by_tab
        self.full = full or {}   # raw_id -> texto completo al "abrir" el mensaje
    def read_message_reports(self, tab): return self.by_tab.get(tab, [])
    def read_message_full(self, raw_id): return self.full.get(raw_id, "")


def spy(mid, frm="1:2:3", to="4:5:6"):
    return {"id": mid,
            "text": f"Se ha detectado una flota del planeta [{frm}] cerca de tu planeta [{to}]. "
                    f"Probabilidad de contraespionaje: 30%",
            "raw": {}}


def spy_compact(mid, enemy="Stadtholder Hubble", to="1:125:8"):
    """Formato real del OGame nuevo: fila compacta sin frase, coords = planeta MÍO."""
    return {"id": mid, "text": f"8m 5s\n{enemy}\n-\n-\n[{to}]", "raw": {}}


class _Coords:
    def __init__(self, g, s, p): self.galaxy, self.system, self.position = g, s, p


class _Planet:
    def __init__(self, g, s, p): self.coords = _Coords(g, s, p)


def make_fake(msgs, planets=None, full=None):
    cfg = types.SimpleNamespace(enable_spy_watch=True, spy_watch_messages=True,
                                telegram_token="T", telegram_chat_id="C")
    fake = types.SimpleNamespace(log=logging.getLogger("t"), cfg=cfg,
                                 client=FakeClient({20: msgs}, full),
                                 last_planets=planets or [],
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

# 6) Formato compacto real (OGame nuevo): coords del mensaje = planeta MÍO -> avisa, y al
#    abrir el mensaje saca el origen y el % de contra-espionaje del cuerpo completo.
planets = [_Planet(1, 125, 8)]
full10 = ("43m 30s\nStadtholder Hubble\n-\n-\n[1:125:8]\nInforme de espionaje en Luna [1:201:8]\n"
          "Se ha detectado una flota del planeta Luna [1:201:8] (FINAL BOSS) cerca de tu "
          "planeta Luna [1:125:8]. La probabilidad de contra-espionaje es del 0 %")
run(make_fake([spy("7"), spy("8"), spy("9"), spy_compact("10")], planets, {"10": full10}))
assert len(sent) == 1 and "Te han espiado" in sent[0][1], sent
assert "[1:201:8]" in sent[0][1] and "Stadtholder Hubble" in sent[0][1], sent
assert "0%" in sent[0][1], sent          # contra-espionaje sacado del cuerpo
assert ledger()["20-10"]["notified"] == "1"

# 7) Fila compacta cuyas coords NO son mías (mi propio informe de un inactivo) -> se ignora.
run(make_fake([spy_compact("11", enemy="NodStar", to="1:134:10")], planets))
assert sent == [], sent

# 8) Sin last_planets pero con planets_cache.json: own_coords sale del caché (planeta+luna).
with open("planets_cache.json", "w", encoding="utf-8") as f:
    json.dump([{"coords": "5:5:5", "moon": {"coords": "6:6:6"}}], f)
run(make_fake([spy_compact("12", enemy="Raider", to="6:6:6")]))   # luna propia, planets=[]
assert len(sent) == 1 and "[6:6:6]" in sent[0][1], sent

print("OK")
