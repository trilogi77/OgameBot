import os, sys, types, logging
from types import MethodType
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import brain
from ogbot.models import Coords, Resources

brain.economy.time_to_accumulate = lambda *a, **k: 1.0   # ahorro "rápido" en el test


class FakePlanet:
    def __init__(self, lab, m, c, d):
        self.coords = Coords(2, 113, 10, type="planet")
        self._lv = {"research_lab": lab}
        self.resources = Resources(m, c, d)
        self.building_in_progress = False
    def lvl(self, n): return self._lv.get(n, 0)


class FakeClient:
    def __init__(self): self.built = []; self.researched = []
    def build(self, planet, comp, name): self.built.append((comp, name)); return True
    def research(self, name, planet=None): self.researched.append(name); return True


def make(choice, facilities=True):
    fs = types.SimpleNamespace(
        client=FakeClient(), log=logging.getLogger("t"),
        research_levels={}, state_cache={},
        cfg=types.SimpleNamespace(keep_resources_buffer=0.1, planets_config={},
                                  enable_facilities=facilities, enable_research=True,
                                  research_unlock_all=False, universe_speed=8),
    )
    for n in ("_raise_research_lab", "_research_step", "_research_planet",
              "_get_planet_setting", "_start_research", "_follow_research_unlock"):
        setattr(fs, n, MethodType(getattr(brain.Brain, n), fs))
    fs._guard = lambda: True
    fs._active_queue_entry = lambda p: None
    fs._mark_build_started = lambda *a, **k: 0.0
    fs.record_session_action = lambda *a, **k: None
    fs._save_state_cache = lambda: None
    brain.research_mod.next_research = lambda rl, best, cfg: choice
    return fs


# research_lab 4 cuesta (1600,3200,1600): "rico" lo paga, "pobre" no.
rich = lambda lab=3: FakePlanet(lab, 50000, 50000, 50000)
poor = lambda lab=3: FakePlanet(lab, 100, 100, 100)

# 1) Investigación bloqueada por lab -> sube el laboratorio, NO intenta investigar.
fs = make(("weapons_tech", None, 4))
fs._research_step([rich()])
assert fs.client.built == [("facilities", "research_lab")], fs.client.built
assert fs.client.researched == [], fs.client.researched

# 2) Investigación NO bloqueada -> investiga, no toca el lab.
import ogbot.gamedata as gd
fs = make(("computer_tech", gd.research_cost("computer_tech", 3), None))
fs._research_step([rich()])
assert fs.client.researched == ["computer_tech"], fs.client.researched
assert fs.client.built == [], fs.client.built

# 3) Bloqueada pero SIN recursos -> no construye (ahorra), no falla.
fs = make(("weapons_tech", None, 4))
fs._research_step([poor()])
assert fs.client.built == [] and fs.client.researched == []

# 4) Bloqueada pero planeta OCUPADO -> no construye.
fs = make(("weapons_tech", None, 4))
p = rich(); p.building_in_progress = True
fs._research_step([p])
assert fs.client.built == []

# 5) Bloqueada pero instalaciones DESACTIVADAS -> no construye (avisa).
fs = make(("weapons_tech", None, 4), facilities=False)
fs._research_step([rich()])
assert fs.client.built == []

# 6) Ya cumple el nivel de lab pedido -> no reconstruye.
fs = make(("weapons_tech", None, 4))
fs._research_step([rich(lab=4)])
assert fs.client.built == []

# --- _follow_research_unlock (fase de desbloqueo) ---
# 7) Paso pide más laboratorio del que hay -> sube el laboratorio (no investiga).
fs = make(("x", None, None))
fs._follow_research_unlock(rich(lab=3), ("weapons_tech", 1))   # weapons pide lab 4
assert fs.client.built == [("facilities", "research_lab")] and fs.client.researched == []

# 8) Paso con lab suficiente y recursos -> investiga ese nivel.
fs = make(("x", None, None))
fs._follow_research_unlock(rich(lab=3), ("computer_tech", 1))  # computer pide lab 1
assert fs.client.researched == ["computer_tech"] and fs.client.built == []

# 9) Lab suficiente pero SIN recursos -> aprovecha para subir el laboratorio (plan necesita 7).
fs = make(("x", None, None))
fs._follow_research_unlock(poor(lab=3), ("computer_tech", 1))
assert fs.client.researched == []          # no investiga sin recursos
# poor tampoco puede pagar el lab -> _raise_research_lab ahorra; no debe petar
assert fs.client.built == []

print("OK")
