"""Mientras un planeta sigue el programa especial (SERVER_START_ORDER/NEW_PLANET_ORDER),
_facilities_step NO debe perseguir sus propios objetivos de robotics_factory/shipyard/
research_lab/nanite_factory (target_robotics_factory, empire_auto...): compiten por el
mismo cupo de cola y los mismos recursos que el paso pendiente del programa, generando dos
"ahorros" simultaneos que se pisan (ahorra para una investigacion del programa Y ahorra para
subir robotica a la vez) y el plan de minas nunca avanza. Ver round_economy en brain.cycle."""
import os, sys, types, logging
from types import MethodType
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import brain, startorder
from ogbot.models import Coords, Resources

B = brain.Brain
log = logging.getLogger("t")


class P:
    def __init__(self, coords, buildings=None, m=0, c=0, d=0):
        g, s, p = (int(x) for x in coords.split(":"))
        self.id = f"planet-{g}{s}{p}"
        self.coords = Coords(g, s, p, type="planet")
        self.buildings = dict(buildings or {})
        self.resources = Resources(m, c, d)
        self.max_temp = 30
        self.building_in_progress = False
        self.savings_reserve = None
    def lvl(self, n): return self.buildings.get(n, 0)


class Client:
    def __init__(self): self.built = []; self.researched = []
    def build(self, planet, comp, name): self.built.append((comp, name)); return True
    def research(self, name, planet=None): self.researched.append(name); return True


def fake_brain(cfg):
    fs = types.SimpleNamespace(cfg=cfg, client=Client(), log=log, research_levels={},
                               state_cache={}, last_planets=[])
    fs._guard = lambda: True
    fs.record_session_action = lambda *a, **k: None
    fs._mark_build_started = lambda *a, **k: 0.0
    fs._save_state_cache = lambda: None
    fs._active_queue_entry = lambda p: None
    fs._get_planet_setting = lambda p, key, default=None: getattr(cfg, key, default)
    fs._special_program_for = MethodType(B._special_program_for, fs)
    fs._special_program_step = MethodType(B._special_program_step, fs)
    fs._facilities_step = MethodType(B._facilities_step, fs)
    return fs


def round_economy_one_planet(fs, p, planets):
    """Reproduce el gating de round_economy (brain.py) para un planeta."""
    p.savings_reserve = None
    prog = fs._special_program_for(p, planets)
    if prog is not None and not fs._special_program_step(p, prog):
        if getattr(p, "savings_reserve", None) is None:
            return
    if prog is None:
        fs._facilities_step(p)


# Robotica ya al nivel 2 (tope de SERVER_START_ORDER) y minas 10/8/5: el programa especial
# sigue pendiente (falta deut_synth 6, el ultimo paso) y no puede pagarlo todavia -> ahorra.
# empire_auto fuerza target_robotics_factory=10: sin el guard, _facilities_step intentaria
# subir robotica con el excedente cada ciclo, compitiendo con el ahorro del programa.
cfg = types.SimpleNamespace(
    empire_auto=True, target_robotics_factory=0, target_shipyard=0,
    target_research_lab=0, target_nanite_factory=0,
    special_auto_program=True, keep_resources_buffer=0.0,
)
fs = fake_brain(cfg)
p = P("1:1:1", {"metal_mine": 10, "crystal_mine": 8, "deut_synth": 5, "robotics_factory": 2,
                "solar_plant": 9, "shipyard": 4, "research_lab": 3, "metal_storage": 3,
                "crystal_storage": 3, "deut_tank": 3},
      m=500, c=200, d=50)   # muy poco: el paso deut_synth 6 no se puede pagar todavia
planets = [p]
fs.last_planets = planets

round_economy_one_planet(fs, p, planets)

assert fs.client.built == [], f"no debe construirse nada mientras el programa ahorra: {fs.client.built}"
assert p.savings_reserve is not None, "el programa especial debe fijar su reserva"
assert p.lvl("robotics_factory") == 2

# Con recursos de sobra el programa SI construye su propio paso (deut_synth), y sigue sin
# tocar robotica (facilities_step no corre mientras el programa siga activo).
fs2 = fake_brain(cfg)
fs2.research_levels = {"energy_tech": 1, "combustion_drive": 2, "impulse_drive": 3,
                       "espionage_tech": 4, "astrophysics": 1}
p2 = P("1:1:2", {"metal_mine": 10, "crystal_mine": 8, "deut_synth": 5, "robotics_factory": 2,
                 "solar_plant": 9, "shipyard": 4, "research_lab": 3, "metal_storage": 5,
                 "crystal_storage": 5, "deut_tank": 5},
      m=10**7, c=10**7, d=10**7)
planets2 = [p2]
fs2.last_planets = planets2
round_economy_one_planet(fs2, p2, planets2)
assert fs2.client.built == [("supplies", "deut_synth")], fs2.client.built
assert ("facilities", "robotics_factory") not in fs2.client.built

# Una vez el programa especial ha terminado (next_step -> None), _facilities_step SI puede
# perseguir su propio objetivo de robotica (empire_auto -> target 10).
fs3 = fake_brain(cfg)
fs3.research_levels = {name: 99 for kind, name, _ in startorder.SERVER_START_ORDER if kind == "research"}
p3 = P("1:1:3", {k: v for k, v in {"metal_mine": 99, "crystal_mine": 99, "deut_synth": 99,
                "robotics_factory": 2, "solar_plant": 99, "shipyard": 99, "research_lab": 99,
                "metal_storage": 10, "crystal_storage": 10, "deut_tank": 10}.items()},
      m=10**7, c=10**7, d=10**7)
planets3 = [p3]
fs3.last_planets = planets3
assert startorder.next_step(p3, fs3.research_levels, startorder.SERVER_START_ORDER) is None, \
    "planeta totalmente desarrollado: el programa especial debe darse por completado"
round_economy_one_planet(fs3, p3, planets3)
assert ("facilities", "robotics_factory") in fs3.client.built, \
    "tras completar el programa, las instalaciones deben poder perseguir su propio objetivo"

print("OK")
