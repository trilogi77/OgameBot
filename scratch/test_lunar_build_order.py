"""Verifica la construcción lunar: ahora incluye fábrica de robots y astillero, respeta el
ORDEN (base -> robótica -> astillero -> falange -> puerta) y las claves de objetivo de la
luna NO chocan con las del planeta (misma coords)."""
import os, sys, types, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import brain, gamedata as gd


class _Coords:
    def __init__(self, g, s, p, t="moon"):
        self.galaxy, self.system, self.position, self.type = g, s, p, t
    def tuple(self): return (self.galaxy, self.system, self.position)


class _Moon:
    def __init__(self, buildings=None):
        self.coords = _Coords(1, 2, 3, "moon")
        self.buildings = buildings or {}
        self.building_queue = []
    def lvl(self, b): return self.buildings.get(b, 0)


def make_brain(planet_cfg, research=None):
    cfg = types.SimpleNamespace(
        planets_config={"1:2:3": planet_cfg},
        # Defaults globales (no deben usarse si hay ajuste por planeta):
        target_lunar_base=0, target_lunar_robotics_factory=0, target_lunar_shipyard=0,
        target_sensor_phalanx=0, target_jump_gate=0,
        # Objetivos del PLANETA (mismo coords): NO deben afectar a la luna.
        target_robotics_factory=99, target_shipyard=99)
    b = types.SimpleNamespace(log=logging.getLogger("t"), cfg=cfg,
                              research_levels=research or {})
    b._get_planet_setting = types.MethodType(brain.Brain._get_planet_setting, b)
    b._next_lunar_build = types.MethodType(brain.Brain._next_lunar_build, b)
    return b


# 1) Fábrica de robots ahora es construible en la luna (antes: imposible).
b = make_brain({"target_lunar_robotics_factory": 3})
choice = b._next_lunar_build(_Moon())
assert choice and choice[0] == "robotics_factory", choice

# 2) ORDEN: con base+robótica+puerta objetivo, primero la BASE lunar (da campos/desbloquea).
b = make_brain({"target_lunar_base": 2, "target_lunar_robotics_factory": 5,
                "target_jump_gate": 1}, research={"hyperspace_tech": 7})
assert b._next_lunar_build(_Moon())[0] == "lunar_base", "la base va primero"
# Con la base ya al objetivo -> toca la robótica antes que la puerta.
m = _Moon({"lunar_base": 2})
assert b._next_lunar_build(m)[0] == "robotics_factory", "la robótica va antes que la puerta"

# 3) El astillero pide robótica 2: si se pide astillero sin robótica, se inserta la robótica.
b = make_brain({"target_lunar_shipyard": 1})
assert b._next_lunar_build(_Moon())[0] == "robotics_factory", "astillero -> robótica primero"
# Con robótica 2 ya, construye el astillero.
assert b._next_lunar_build(_Moon({"robotics_factory": 2}))[0] == "shipyard", "ya se puede el astillero"

# 4) SIN colisión con el planeta: aunque el planeta pida robótica/astillero 99, la luna con
#    objetivos 0 (default) no construye nada.
b = make_brain({})  # ningún objetivo lunar -> None pese a target_robotics_factory=99 del planeta
assert b._next_lunar_build(_Moon()) is None, "los objetivos del planeta NO se aplican a la luna"

# 5) La puerta de salto pide base lunar 1: se inserta la base antes.
b = make_brain({"target_jump_gate": 1}, research={"hyperspace_tech": 7})
assert b._next_lunar_build(_Moon())[0] == "lunar_base", "puerta -> base lunar 1 primero"

# 6) Puerta sin hiperespacio 7: bloqueada (resolver devuelve research) -> None.
b = make_brain({"target_jump_gate": 1}, research={"hyperspace_tech": 0})
m = _Moon({"lunar_base": 1})  # base ya cubierta; solo falta la puerta, bloqueada por research
assert b._next_lunar_build(m) is None, "puerta bloqueada por hiperespacio -> None"

print("OK")
