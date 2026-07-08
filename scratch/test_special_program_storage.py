import os, sys, types, logging
from types import MethodType
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import brain
from ogbot.models import Coords, Resources

R = "research"


class FakePlanet:
    def __init__(self, buildings, m, c, d):
        self.coords = Coords(2, 113, 10, type="planet")
        self._lv = dict(buildings)
        self.resources = Resources(m, c, d)
        self.building_in_progress = False
    def lvl(self, n): return self._lv.get(n, 0)


class FakeClient:
    def __init__(self): self.built = []; self.researched = []
    def build(self, planet, comp, name): self.built.append((comp, name)); return True
    def research(self, name, planet=None): self.researched.append(name); return True


def make():
    fs = types.SimpleNamespace(
        client=FakeClient(), log=logging.getLogger("t"),
        research_levels={}, state_cache={},
        cfg=types.SimpleNamespace(universe_speed=8),
    )
    fs._special_program_step = MethodType(brain.Brain._special_program_step, fs)
    fs._active_queue_entry = lambda p: None
    fs._guard = lambda: True
    fs._mark_build_started = lambda *a, **k: 0.0
    fs.record_session_action = lambda *a, **k: None
    fs._save_state_cache = lambda: None
    return fs


# 1) DEADLOCK EVITADO: impulse_drive 3 pide 16000 cristal pero el tope es 10000 (crystal_storage 0)
#    -> en vez de quedarse "ahorrando" para siempre, construye crystal_storage.
fs = make()
fs.research_levels = {"impulse_drive": 2}
planet = FakePlanet({"crystal_storage": 0}, m=20000, c=10000, d=10000)
order = [(R, "impulse_drive", 3)]
fs._special_program_step(planet, order)
assert fs.client.built == [("supplies", "crystal_storage")], fs.client.built
assert fs.client.researched == [], fs.client.researched

# 2) Si el coste SÍ cabe (impulse_drive 1 = 4000 cristal <= 10000) -> investiga normal.
fs = make()
planet = FakePlanet({"crystal_storage": 0}, m=20000, c=10000, d=10000)
order = [(R, "impulse_drive", 1)]
fs._special_program_step(planet, order)
assert fs.client.researched == ["impulse_drive"], fs.client.researched
assert fs.client.built == [], fs.client.built

# 3) Con almacén suficiente (crystal_storage 1 -> cap 20000) impulse 3 (16000) ya investiga.
fs = make()
fs.research_levels = {"impulse_drive": 2}
planet = FakePlanet({"crystal_storage": 1}, m=20000, c=20000, d=10000)
order = [(R, "impulse_drive", 3)]
fs._special_program_step(planet, order)
assert fs.client.researched == ["impulse_drive"], fs.client.researched
assert fs.client.built == []

print("OK")
