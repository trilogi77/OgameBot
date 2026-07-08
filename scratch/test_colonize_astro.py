import os, sys, types, logging
from types import MethodType
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import brain
from ogbot.models import Coords


class FakePlanet:
    def __init__(self, g, s, p, colony_ships=0):
        self.id = f"planet-{g}{s}{p}"
        self.coords = Coords(g, s, p, type="planet")
        self.ships = {"colony_ship": colony_ships}


class FakeClient:
    def __init__(self): self.sent = []
    def send_fleet(self, origin, dest, ships, mission="colonize"):
        self.sent.append((str(dest), mission)); return True


def make(planets, astro, inflight=None):
    planets[0].ships["colony_ship"] = 1   # el principal tiene un colonizador
    fs = types.SimpleNamespace(
        client=FakeClient(), api=None, active_slots=0,
        log=logging.getLogger("t"),
        research_levels={"astrophysics": astro},
        inflight_dests=inflight or {},
        cfg=types.SimpleNamespace(max_colonies=9, enable_colonization=True,
                                  colony_target_galaxy=0, colony_target_system=0,
                                  preferred_colony_positions=[4, 5, 6, 7, 8, 9, 10, 11, 12]),
    )
    for name in ("_has_ships", "_colonizers_in_flight", "_deduct_ships",
                 "_colony_pending", "_colonize"):
        setattr(fs, name, MethodType(getattr(brain.Brain, name), fs))
    fs._has_free_slots_for_mission = lambda: True
    fs._guard = lambda: True
    return fs, planets


M = lambda g, s, p: FakePlanet(g, s, p)
C = lambda g, s, p: FakePlanet(g, s, p)

# 1) Caso del usuario: astro 2 -> colony_slots=1 -> 1 colonia; ya hay 1 -> NO manda.
fs, pl = make([M(2, 113, 10), C(2, 113, 5)], astro=2)
fs._colonize(pl)
assert fs.client.sent == [], f"no debía mandar (astro 2 solo permite 1 colonia): {fs.client.sent}"

# 2) astro 2, solo el principal (0 colonias) -> SÍ manda una.
fs, pl = make([M(2, 113, 10)], astro=2)
fs._colonize(pl)
assert len(fs.client.sent) == 1 and fs.client.sent[0][1] == "colonize", fs.client.sent

# 3) astro 3 -> colony_slots=2 -> caben 2 colonias; con 1 existente -> SÍ manda.
fs, pl = make([M(2, 113, 10), C(2, 113, 5)], astro=3)
fs._colonize(pl)
assert len(fs.client.sent) == 1, fs.client.sent

# 4) Colonizador ya en camino (misión 7 en inflight_dests) -> NO manda aunque haya hueco.
fs, pl = make([M(2, 113, 10)], astro=5, inflight={"3:100:8": {"7"}})
fs._colonize(pl)
assert fs.client.sent == [], f"no debía mandar con un colonizador en vuelo: {fs.client.sent}"

# 5) Sin colonizador disponible -> NO manda.
fs, pl = make([M(2, 113, 10)], astro=5)
pl[0].ships["colony_ship"] = 0
fs._colonize(pl)
assert fs.client.sent == []

# 6) _colony_pending refleja el mismo tope (para la reserva de slot vs expediciones).
fs, pl = make([M(2, 113, 10), C(2, 113, 5)], astro=2)
assert fs._colony_pending(pl) is False           # astro 2 lleno
fs, pl = make([M(2, 113, 10), C(2, 113, 5)], astro=3)
assert fs._colony_pending(pl) is True            # astro 3 deja hueco
fs, pl = make([M(2, 113, 10)], astro=5, inflight={"3:100:8": {"7"}})
assert fs._colony_pending(pl) is False           # ya hay uno en camino

# 7) _colonizers_in_flight cuenta bien.
fs, pl = make([M(2, 113, 10)], astro=5,
              inflight={"3:100:8": {"7"}, "4:50:6": {"3"}, "5:20:9": {"7", "1"}})
assert fs._colonizers_in_flight() == 2

print("OK")
