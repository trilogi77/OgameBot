"""Tests sin frameworks: python test_client_goto.py
Regresión: server_url vacio/desactualizado producia URLs relativas
("/game/index.php?...") que Playwright rechaza con
'Cannot navigate to invalid URL'.
"""
import logging
from ogbot.client import GameClient
from ogbot.config import Config


class FakePage:
    """Standin de playwright.Page: solo registra las URLs visitadas."""
    def __init__(self, urls_by_visit):
        self._urls_by_visit = list(urls_by_visit)
        self.url = ""
        self.goto_calls = []

    def goto(self, url, **kwargs):
        self.goto_calls.append(url)
        self.url = self._urls_by_visit.pop(0)

    def is_closed(self):
        return False


cfg = Config()
cfg.server_url = ""  # simula config sin servidor / no actualizado aun
log = logging.getLogger("test")
client = GameClient(cfg, log)

# 1) _goto reconstruye la URL con el server_url ACTUAL en cada intento, no con el
#    valor congelado al principio de la llamada.
def fake_login():
    client.cfg.server_url = "https://s272-es.ogame.gameforge.com/"
    return True

client.login = fake_login
client.page = FakePage([
    "https://lobby.ogame.gameforge.com/es_ES/accounts",   # tras el 1er goto: nos echo al lobby
    "https://s272-es.ogame.gameforge.com/game/index.php?page=ingame&component=overview",  # tras re-login
])
client._goto("overview")

assert client.page.goto_calls[0] == "/game/index.php?page=ingame&component=overview", (
    f"1er intento inesperado: {client.page.goto_calls[0]}")
assert client.page.goto_calls[1] == (
    "https://s272-es.ogame.gameforge.com/game/index.php?page=ingame&component=overview"
), f"reintento no uso el server_url actualizado: {client.page.goto_calls[1]}"

# 2) _await_human_login debe capturar server_url de la URL real cuando el humano
#    resuelve el login directamente (sin pasar por _enter_game_via_play).
cfg2 = Config()
cfg2.server_url = ""
client2 = GameClient(cfg2, log)
client2.page = FakePage([])
client2.page.url = "https://s272-es.ogame.gameforge.com/game/index.php?page=ingame&component=overview"
ok = client2._await_human_login("https://lobby.ogame.gameforge.com/es_ES/accounts")
assert ok is True
assert client2.cfg.server_url == "https://s272-es.ogame.gameforge.com/", (
    f"server_url no capturado: {client2.cfg.server_url!r}")

# 3) _reload_config no debe pisar el server_url detectado en vivo con el valor
#    vacio del disco (era la causa del fallo recurrente cada ciclo).
from ogbot.brain import Brain

brain = Brain.__new__(Brain)  # sin __init__: solo probamos _reload_config
brain.cfg = Config()
brain.cfg._path = "config-que-no-existe.yaml"  # Config.load -> defaults (server_url="")
brain.cfg.server_url = "https://s272-es.ogame.gameforge.com/"
brain.log = log
brain._apply_probe_cargo = lambda: None
brain._apply_empire_auto = lambda: None
brain._reload_config()
assert brain.cfg.server_url == "https://s272-es.ogame.gameforge.com/", (
    f"reload piso el server_url detectado: {brain.cfg.server_url!r}")

print("OK")
