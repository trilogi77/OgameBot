import os, sys, types, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import client
from ogbot.client import GameClient

client.time.sleep = lambda *_a, **_k: None   # sin esperas AJAX en el test


class FakePage:
    """Simula el overlay de directivas. evaluate() se distingue por el script:
       - arg != None            -> verificación de estado de una tarea
       - '#ipimenucomponent'    -> badge de pendientes
       - 'ipiInnerMenuContentHolder' -> abrir overlay
       - resto (_load_js)       -> siguiente acción encolada del panel
    """
    def __init__(self, pending, actions, states):
        self.pending = pending
        self.actions = list(actions)   # cola: dicts {action,id} o None
        self.states = states           # taskid -> data-state tras la recogida
        self.url = "https://s1.example.com/game/index.php?component=overview"
        self.opened = 0

    def goto(self, url, wait_until=None):
        pass

    def wait_for_selector(self, sel, timeout=None):
        pass

    def evaluate(self, script, arg=None):
        if arg is not None:                       # verificación de estado
            return self.states.get(arg, "gone")
        if "#ipimenucomponent" in script:         # badge
            return self.pending
        if "ipiInnerMenuContentHolder" in script: # abrir overlay
            self.opened += 1
            return True
        return self.actions.pop(0) if self.actions else None   # panel


def make(dry_run, page):
    return types.SimpleNamespace(
        log=logging.getLogger("t"),
        cfg=types.SimpleNamespace(dry_run=dry_run),
        page=page,
        _goto=lambda *a, **k: None,
        _act=lambda desc: dry_run,   # como GameClient._act
    )


# 1) Dos tareas completadas -> se recogen ambas (estado pasa a 'collected').
p = FakePage(2, [{"action": "collect", "id": "5038"},
                 {"action": "collect", "id": "5040"}, None],
             {"5038": "collected", "5040": "collected"})
assert GameClient.claim_directive_rewards(make(False, p)) == 2
assert p.opened == 1

# 2) Cambio de capítulo intermedio y luego recoge.
p = FakePage(1, [{"action": "chapter", "id": ""},
                 {"action": "collect", "id": "5040"}, None],
             {"5040": "collected"})
assert GameClient.claim_directive_rewards(make(False, p)) == 1

# 3) El click no cambia el estado (selector de recogida equivocado) -> aborta sin contar.
p = FakePage(1, [{"action": "collect", "id": "5040"}, None], {"5040": "completed"})
assert GameClient.claim_directive_rewards(make(False, p)) == 0

# 4) Sin pendientes -> ni abre el overlay.
p = FakePage(0, [{"action": "collect", "id": "5040"}, None], {})
assert GameClient.claim_directive_rewards(make(False, p)) == 0
assert p.opened == 0

# 5) dry_run -> no hace nada.
p = FakePage(2, [{"action": "collect", "id": "5040"}, None], {"5040": "collected"})
assert GameClient.claim_directive_rewards(make(True, p)) == 0
assert p.opened == 0

print("OK")
