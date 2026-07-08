import os, sys, types, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import client
from ogbot.client import GameClient

client.time.sleep = lambda *_a, **_k: None   # sin esperas AJAX en el test


class FakePage:
    """evaluate() distingue la sonda is_dir (contiene 'innerText') del JS de reclamo."""
    def __init__(self, is_dir, labels):
        self.is_dir = is_dir            # bool: ¿la página muestra directivas?
        self.labels = labels            # lista de etiquetas (None=fin) o None=modo tope
        self.url = "https://s1.example.com/game/index.php?page=ingame&component=x"
        self.nav = []

    def goto(self, url, wait_until=None):
        self.nav.append(url)

    def evaluate(self, script, arg=None):
        if "innerText" in script:
            return self.is_dir
        if self.labels is None:
            return "x"                  # nunca se seca: prueba el tope duro
        return self.labels.pop(0) if self.labels else None


def make(dry_run, page, cached=None):
    self = types.SimpleNamespace(
        log=logging.getLogger("t"),
        cfg=types.SimpleNamespace(dry_run=dry_run, server_url="https://s1.example.com"),
        page=page,
        _directives_component=cached,
        _act=lambda desc: dry_run,          # como GameClient._act
        _is_game_url=lambda url: True,
    )
    return self


# 1) Descubre el componente y reclama hasta que no queda ninguna.
self = make(False, FakePage(True, ["Recibir recompensa", "Recibir recompensa", None]))
n = GameClient.claim_directive_rewards(self)
assert n == 2, n
assert self._directives_component == "directives", self._directives_component
assert any("component=directives" in u for u in self.page.nav)

# 2) Tope duro: el panel nunca se "seca" -> como mucho 12, sin bucle infinito.
self = make(False, FakePage(True, None))
assert GameClient.claim_directive_rewards(self) == 12

# 3) Ninguna candidata muestra directivas -> se autodesactiva (no reintentar).
self = make(False, FakePage(False, []))
assert GameClient.claim_directive_rewards(self) == 0
assert self._directives_component == ""

# 4) Ya desactivado -> ni navega.
self = make(False, FakePage(True, ["Recibir recompensa", None]), cached="")
assert GameClient.claim_directive_rewards(self) == 0
assert self.page.nav == []

# 5) dry_run -> no hace nada.
self = make(True, FakePage(True, ["Recibir recompensa", None]))
assert GameClient.claim_directive_rewards(self) == 0
assert self.page.nav == []

print("OK")
