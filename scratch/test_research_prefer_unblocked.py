import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import research as research_mod
from ogbot import economy
from ogbot.models import Coords, Resources


class FakePlanet:
    def __init__(self, lab, m, c, d):
        self.coords = Coords(2, 113, 10, type="planet")
        self._lv = {"research_lab": lab}
        self.resources = Resources(m, c, d)
    def lvl(self, n): return self._lv.get(n, 0)


def cfg(priority):
    return types.SimpleNamespace(
        research_priority=priority, keep_resources_buffer=0.1,
        enable_fusion_reactor=False, max_saving_hours_research=6.0,
        trade_ratio=(2.5, 1.5, 1.0),
    )


# 1) Con armas (bloqueada por lab 4, barata) y computación (disponible), elige la
#    DISPONIBLE aunque sea más cara ponderada; no devuelve la bloqueada.
p = FakePlanet(lab=3, m=50000, c=50000, d=50000)
res = research_mod.next_research({}, p, cfg(["weapons_tech", "computer_tech"]))
assert res is not None and res[0] == "computer_tech" and res[2] is None, res

# 2) Si SOLO hay candidata bloqueada (armas), la devuelve como plan B (con lab_lvl) para
#    que el brain suba el laboratorio.
res = research_mod.next_research({}, p, cfg(["weapons_tech"]))
assert res is not None and res[0] == "weapons_tech" and res[2] == 4, res

# 3) Disponible pero sin recursos y que tarda MUCHO en acumular -> None (no adelanta lab).
_orig = economy.time_to_accumulate
economy.time_to_accumulate = lambda *a, **k: 999.0
try:
    poor = FakePlanet(lab=3, m=1, c=1, d=1)
    res = research_mod.next_research({}, poor, cfg(["computer_tech"]))
    assert res is None, res
finally:
    economy.time_to_accumulate = _orig

print("OK")
