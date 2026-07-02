"""Self-check de startorder (configuraciones especiales: inicio de servidor / colonia)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import gamedata as gd
from ogbot.startorder import (B, R, SERVER_START_ORDER, NEW_PLANET_ORDER,
                              next_step, storage_blocker, storage_capacity)


class PlanetStub:
    def __init__(self, buildings=None):
        self.buildings = dict(buildings or {})

    def lvl(self, name):
        return self.buildings.get(name, 0)


def validate(order, name):
    b_lvls, r_lvls = {}, {}
    for kind, item, lvl in order:
        if kind == B:
            assert item in gd.BUILDING_COST, f"{name}: edificio desconocido {item}"
            for req, req_lvl in gd.BUILDING_PREREQS.get(item, {}).items():
                have = b_lvls.get(req, r_lvls.get(req, 0))
                assert have >= req_lvl, f"{name}: {item} requiere {req} {req_lvl}, orden lleva {have}"
            assert lvl > b_lvls.get(item, 0), f"{name}: paso no incremental {item} {lvl}"
            b_lvls[item] = lvl
        else:
            assert item in gd.RESEARCH_COST, f"{name}: tecnología desconocida {item}"
            assert b_lvls.get("research_lab", 0) >= gd.RESEARCH_LAB_REQ.get(item, 0), \
                f"{name}: {item} requiere laboratorio {gd.RESEARCH_LAB_REQ.get(item)}"
            for req, req_lvl in gd.RESEARCH_PREREQS.get(item, {}).items():
                assert r_lvls.get(req, 0) >= req_lvl, \
                    f"{name}: {item} requiere {req} {req_lvl}, orden lleva {r_lvls.get(req, 0)}"
            assert lvl > r_lvls.get(item, 0), f"{name}: paso no incremental {item} {lvl}"
            r_lvls[item] = lvl
    return b_lvls, r_lvls


b_lvls, r_lvls = validate(SERVER_START_ORDER, "SERVER_START")
validate(NEW_PLANET_ORDER, "NEW_PLANET")

# Al acabar el inicio de servidor se puede fabricar la nave colonizadora
for req, req_lvl in gd.SHIP_PREREQS["colony_ship"].items():
    have = b_lvls.get(req, r_lvls.get(req, 0))
    assert have >= req_lvl, f"colony_ship requiere {req} {req_lvl}, orden lleva {have}"

# next_step camina el orden y devuelve None al completarlo
p = PlanetStub()
assert next_step(p, {}, SERVER_START_ORDER) == (B, "metal_mine", 2)
p = PlanetStub({name: lvl for k, name, lvl in SERVER_START_ORDER if k == B})
assert next_step(p, {}, SERVER_START_ORDER) == (R, "energy_tech", 1)
assert next_step(p, r_lvls, SERVER_START_ORDER) is None

# storage_blocker: coste que no cabe en el almacén nivel 0 -> subir almacén antes
class CostStub:
    metal, crystal, deut = 15000, 0, 0
assert storage_capacity(0) == 10000
assert storage_blocker(CostStub(), PlanetStub()) == "metal_storage"
assert storage_blocker(CostStub(), PlanetStub({"metal_storage": 2})) is None

print("startorder self-check OK:",
      len(SERVER_START_ORDER), "pasos servidor /", len(NEW_PLANET_ORDER), "pasos colonia")
