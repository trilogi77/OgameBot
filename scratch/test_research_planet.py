"""Tests de research_planet: elegir desde qué planeta se investiga.

- Vacío -> el planeta con el laboratorio de mayor nivel (comportamiento de siempre).
- Coordenadas "g:s:p" válidas -> ese planeta, aunque su laboratorio sea menor.
- Coordenadas que no casan con ningún planeta -> aviso (una vez) y mejor laboratorio.
- _try_metal_dump solo actúa desde el planeta de investigación elegido.
"""
import os, sys, types, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import brain
from ogbot.models import Coords


class FakePlanet:
    def __init__(self, coords, levels=None):
        self.coords = coords
        self._lv = levels or {}
    def lvl(self, name):
        return self._lv.get(name, 0)


def brain_with(cfg_kwargs):
    cfg = types.SimpleNamespace(research_planet="")
    for k, v in cfg_kwargs.items():
        setattr(cfg, k, v)
    return types.SimpleNamespace(cfg=cfg, log=logging.getLogger("test"),
                                 _research_planet=None)


main = FakePlanet(Coords(2, 113, 10), {"research_lab": 12})
col = FakePlanet(Coords(2, 113, 5), {"research_lab": 4})
planets = [main, col]

# 1) Sin research_planet -> el de mejor laboratorio.
b = brain_with({})
assert brain.Brain._research_planet(b, planets) is main

# 2) Coordenadas de la colonia -> la colonia, aunque su lab sea menor.
b = brain_with({"research_planet": "2:113:5"})
assert brain.Brain._research_planet(b, planets) is col

# 3) Coordenadas del principal -> el principal.
b = brain_with({"research_planet": "2:113:10"})
assert brain.Brain._research_planet(b, planets) is main

# 4) Coordenadas que no existen -> mejor laboratorio (fallback) y sin excepción.
b = brain_with({"research_planet": "9:999:9"})
assert brain.Brain._research_planet(b, planets) is main
assert getattr(b, "_warned_research_planet", False) is True

# 5) Espacios alrededor -> se recortan.
b = brain_with({"research_planet": "  2:113:5  "})
assert brain.Brain._research_planet(b, planets) is col

# 6) Lista vacía -> None (no revienta).
b = brain_with({})
assert brain.Brain._research_planet(b, planets=[]) is None

# 7) _try_metal_dump: con research_planet fijado en la colonia, el principal (mejor lab)
#    ya NO hace metal dump; el que corresponde es la colonia.
def dump_gate(planet, cfg_kwargs, last_planets):
    """Reproduce la guarda de planeta de _try_metal_dump."""
    b = brain_with(cfg_kwargs)
    rp = brain.Brain._research_planet(b, last_planets or [planet])
    return not (rp is not None and rp.coords != planet.coords)

assert dump_gate(main, {}, planets) is True                                # sin override: mejor lab
assert dump_gate(col, {}, planets) is False
assert dump_gate(main, {"research_planet": "2:113:5"}, planets) is False   # override: solo la colonia
assert dump_gate(col, {"research_planet": "2:113:5"}, planets) is True

print("OK")
