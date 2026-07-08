import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import brain, startorder
from ogbot.models import Coords

SS = startorder.SERVER_START_ORDER
NP = startorder.NEW_PLANET_ORDER


class FakePlanet:
    def __init__(self, coords, levels=None):
        self.coords = coords
        self._lv = levels or {}
    def lvl(self, name):
        return self._lv.get(name, 0)


def brain_with(cfg_kwargs, research=None):
    cfg = types.SimpleNamespace(
        special_auto_program=True, special_new_planet="",
        special_server_start=False, special_new_planet_auto=False,
    )
    for k, v in cfg_kwargs.items():
        setattr(cfg, k, v)
    return types.SimpleNamespace(cfg=cfg, research_levels=(research or {}))


# Niveles "desarrollado": por encima de todos los objetivos de ambos órdenes.
DEV = {n: 50 for n in ("metal_mine", "crystal_mine", "deut_synth", "solar_plant",
                       "robotics_factory", "shipyard", "research_lab", "metal_storage")}
DEV_RESEARCH = {n: 50 for n in ("energy_tech", "combustion_drive", "impulse_drive",
                                "espionage_tech", "astrophysics")}

fresh_main = FakePlanet(Coords(2, 113, 10))          # niveles 0
dev_main = FakePlanet(Coords(2, 113, 10), DEV)
fresh_col = FakePlanet(Coords(2, 113, 5))
dev_col = FakePlanet(Coords(2, 113, 5), DEV)

# 1) AUTO: principal joven -> inicio de servidor.
b = brain_with({}, DEV_RESEARCH)  # da igual la investigación para el principal joven
planets = [fresh_main, fresh_col]
assert brain.Brain._special_program_for(b, fresh_main, planets) is SS

# 2) AUTO: colonia joven -> orden de colonia.
assert brain.Brain._special_program_for(b, fresh_col, planets) is NP

# 3) AUTO: principal desarrollado -> None (economía normal).
b = brain_with({}, DEV_RESEARCH)
planets = [dev_main, dev_col]
assert brain.Brain._special_program_for(b, dev_main, planets) is None

# 4) AUTO: colonia desarrollada -> None.
assert brain.Brain._special_program_for(b, dev_col, planets) is None

# 5) AUTO OFF y sin flags antiguos -> None en todo.
b = brain_with({"special_auto_program": False})
planets = [fresh_main, fresh_col]
assert brain.Brain._special_program_for(b, fresh_main, planets) is None
assert brain.Brain._special_program_for(b, fresh_col, planets) is None

# 6) AUTO OFF pero flag antiguo server_start -> principal sigue inicio de servidor.
b = brain_with({"special_auto_program": False, "special_server_start": True})
assert brain.Brain._special_program_for(b, fresh_main, planets) is SS

# 7) Override por coordenadas -> ese planeta (aunque sea colonia) sigue orden de colonia,
#    incluso con AUTO OFF.
b = brain_with({"special_auto_program": False, "special_new_planet": "2:113:5"})
assert brain.Brain._special_program_for(b, fresh_col, planets) is NP

# 8) Colonia a medias (minas altas pero sin completar el orden) -> sigue el orden de
#    colonia (el escenario que motiva la feature: no la deja a medias).
b = brain_with({})
midcol = FakePlanet(Coords(2, 113, 7),
                    {"metal_mine": 9, "crystal_mine": 5, "solar_plant": 8, "deut_synth": 3})
planets = [fresh_main, midcol]
assert brain.Brain._special_program_for(b, midcol, planets) is NP

print("OK")
