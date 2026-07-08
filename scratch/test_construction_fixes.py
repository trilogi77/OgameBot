import os, sys, types, time, logging
from types import MethodType
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import brain, economy
from ogbot import gamedata as gd
from ogbot.models import Coords, Resources

B = brain.Brain
log = logging.getLogger("t")


class P:
    def __init__(self, coords, buildings=None, ships=None, m=0, c=0, d=0):
        g, s, p = (int(x) for x in coords.split(":"))
        self.id = f"planet-{g}{s}{p}"
        self.coords = Coords(g, s, p, type="planet")
        self._lv = dict(buildings or {})
        self.ships = dict(ships or {})
        self.resources = Resources(m, c, d)
        self.building_in_progress = False
    def lvl(self, n): return self._lv.get(n, 0)


class Client:
    def __init__(self): self.built = []; self.researched = []; self.ships_built = []
    def build(self, planet, comp, name): self.built.append((comp, name)); return True
    def research(self, name, planet=None): self.researched.append(name); return True
    def build_ships(self, planet, name, qty): self.ships_built.append((name, qty)); return True


def base(cfg_extra=None, **attrs):
    cfg = types.SimpleNamespace(planets_config={}, keep_resources_buffer=0.1,
                                enable_facilities=True, empire_auto=True,
                                enable_build_queue=True, universe_speed=8,
                                max_saving_hours_economy=4.0, enable_fleet_building=True,
                                fleet_auto_build=False, fleet_targets={})
    for k, v in (cfg_extra or {}).items():
        setattr(cfg, k, v)
    fs = types.SimpleNamespace(cfg=cfg, client=Client(), log=log, research_levels={},
                               state_cache={}, last_planets=[])
    for k, v in attrs.items():
        setattr(fs, k, v)
    fs._guard = lambda: True
    fs.record_session_action = lambda *a, **k: None
    fs._mark_build_started = lambda *a, **k: 0.0
    fs._mark_ships_started = lambda *a, **k: None
    fs._save_state_cache = lambda: None
    for n in ("_get_planet_setting", "_active_queue_entry", "_facilities_step",
              "_start_research", "_fleet_step"):
        setattr(fs, n, MethodType(getattr(B, n), fs))
    return fs


# ---- Fix 6: cola desactivada NO debe frenar a economía/instalaciones ----
fs = base(cfg_extra={"build_queue": [{"building": "crystal_mine", "target_level": 20}]})
p = P("2:113:5", {"metal_mine": 5, "crystal_mine": 3})
fs.cfg.enable_build_queue = True
assert fs._active_queue_entry(p) is not None            # activada -> hay entrada
fs.cfg.enable_build_queue = False
assert fs._active_queue_entry(p) is None                # desactivada -> None (no frena)

# ---- Fix 4: no lanzar investigación si ya hay una en curso ----
fs = base()
fs.state_cache = {"research": {"finish_epoch": time.time() + 9999}}
assert fs._start_research(P("2:113:10"), "computer_tech", gd.research_cost("computer_tech", 1)) is False
assert fs.client.researched == []
fs.state_cache = {}
assert fs._start_research(P("2:113:10", {"research_lab": 3}), "computer_tech",
                          gd.research_cost("computer_tech", 1)) is True
assert fs.client.researched == ["computer_tech"]

# ---- Fix 2: instalaciones no se bloquean detrás de una cara inasequible ----
fs = base()
colony = P("2:113:5", {"robotics_factory": 9, "shipyard": 6, "research_lab": 3},
           m=60000, c=40000, d=40000)
fs.last_planets = [P("2:113:10")]          # el principal es OTRO -> esta es colonia
fs._facilities_step(colony)
assert ("facilities", "shipyard") in fs.client.built, fs.client.built
assert ("facilities", "research_lab") not in fs.client.built   # Fix 3: lab NO forzado en colonia

# ---- Fix 3: en el principal SÍ se fuerza el laboratorio (target 12) ----
fs = base()
main = P("2:113:10", {"robotics_factory": 10, "shipyard": 8, "research_lab": 3},
         m=200000, c=400000, d=200000)
fs.last_planets = [main]
fs._facilities_step(main)
assert ("facilities", "research_lab") in fs.client.built, fs.client.built

# ---- Fix 5: la flota reserva lo que 'home' ahorra para su próxima construcción ----
_orig_nb = economy.next_build
big = gd.building_cost("metal_mine", 30)   # coste enorme -> home no lo puede pagar (ahorrando)
economy.next_build = lambda *a, **k: ("metal_mine", big)
try:
    fs = base(cfg_extra={"fleet_targets": {"small_cargo": 1000}})
    fs._shipyard_pending = lambda h: False
    fs._expedition_optimal_cargo_total = lambda: 0
    home = P("2:113:10", {"shipyard": 5}, m=20000, c=20000, d=0)
    fs._fleet_step([home])
    assert fs.client.ships_built == [], fs.client.ships_built  # reserva agota el excedente
    economy.next_build = lambda *a, **k: ("metal_mine", gd.Cost(10, 10, 0))
    fs2 = base(cfg_extra={"fleet_targets": {"small_cargo": 1000}})
    fs2._shipyard_pending = lambda h: False
    fs2._expedition_optimal_cargo_total = lambda: 0
    home2 = P("2:113:10", {"shipyard": 5}, m=20000, c=20000, d=0)
    fs2._fleet_step([home2])
    assert fs2.client.ships_built and fs2.client.ships_built[0][0] == "small_cargo"
finally:
    economy.next_build = _orig_nb

print("OK")
