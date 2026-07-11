"""Ahorro con reserva POR RECURSO: ahorrar deuterio para un motor no debe bloquear
las construcciones que solo cuestan metal/cristal (minas, etc.). Ver
economy.spendable_resources / surplus_after_reserve y brain._special_program_step."""
import os, sys, types, time, logging
from types import MethodType
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot import brain, economy
from ogbot import gamedata as gd
from ogbot.models import Coords, Resources

B = brain.Brain
log = logging.getLogger("t")


class P:
    def __init__(self, coords, buildings=None, m=0, c=0, d=0, max_temp=30):
        g, s, p = (int(x) for x in coords.split(":"))
        self.id = f"planet-{g}{s}{p}"
        self.coords = Coords(g, s, p, type="planet")
        self.buildings = dict(buildings or {})
        self.ships = {}
        self.resources = Resources(m, c, d)
        self.max_temp = max_temp
        self.building_in_progress = False
        self.savings_reserve = None
    def lvl(self, n): return self.buildings.get(n, 0)


class Client:
    def __init__(self): self.built = []; self.researched = []
    def build(self, planet, comp, name): self.built.append((comp, name)); return True
    def research(self, name, planet=None): self.researched.append(name); return True


def cfg_base():
    return types.SimpleNamespace(planets_config={}, keep_resources_buffer=0.0,
                                 universe_speed=8, max_saving_hours_economy=4.0,
                                 max_mine_level=40, trade_ratio=(2.5, 1.5, 1.0))


def fake_brain(cfg):
    fs = types.SimpleNamespace(cfg=cfg, client=Client(), log=log, research_levels={},
                               state_cache={}, last_planets=[])
    fs._guard = lambda: True
    fs.record_session_action = lambda *a, **k: None
    fs._mark_build_started = lambda *a, **k: 0.0
    fs._save_state_cache = lambda: None
    fs._active_queue_entry = lambda p: None
    fs._special_program_step = MethodType(B._special_program_step, fs)
    return fs


# ---- 1: spendable_resources descuenta la reserva POR RECURSO ----
cfg = cfg_base()
p = P("1:1:1", m=10000, c=5000, d=100)
p.savings_reserve = Resources(0, 0, 600)          # ahorrando deuterio para un motor
s = economy.spendable_resources(p, cfg)
assert s.metal == 10000 and s.crystal == 5000 and s.deut == 0.0, (s.metal, s.crystal, s.deut)
p.savings_reserve = None
s = economy.spendable_resources(p, cfg)
assert s.deut == 100                              # sin reserva no se descuenta nada

# ---- 2: affordable_build AHORRANDO para algo con deuterio construye una mina con el excedente ----
# Objetivo inasequible por deuterio (t <= max_wait gracias a deut_synth 20); antes devolvía
# None y paraba el planeta; ahora el excedente de metal/cristal paga una mina alternativa.
_orig_nb = economy.next_build
economy.next_build = lambda *a, **k: ("fusion_reactor", gd.Cost(1000, 500, 40000))
try:
    cfg = cfg_base()
    p = P("1:1:2", {"metal_mine": 10, "crystal_mine": 8, "deut_synth": 20}, m=60000, c=30000, d=5000)
    choice = economy.affordable_build(p, cfg)
    assert choice is not None, "el ahorro de deuterio no debe bloquear las minas"
    name, cost = choice
    assert name in ("metal_mine", "crystal_mine", "deut_synth"), choice
    # La mina cabe en el EXCEDENTE (recursos menos lo que el objetivo necesita)
    assert cost.metal <= 60000 - 1000 and cost.crystal <= 30000 - 500, choice
finally:
    economy.next_build = _orig_nb

# ---- 3: la reserva SÍ protege el recurso que el objetivo necesita ----
# El objetivo se come casi todo el metal: el excedente no da para ninguna mina -> None.
_orig_nb = economy.next_build
economy.next_build = lambda *a, **k: ("nanite_factory", gd.Cost(120000, 500, 100))
try:
    cfg = cfg_base()
    p = P("1:1:3", {"metal_mine": 20, "crystal_mine": 8, "deut_synth": 5}, m=100000, c=30000, d=5000)
    assert economy.affordable_build(p, cfg) is None, "no debe gastar el metal reservado"
finally:
    economy.next_build = _orig_nb

# ---- 4: programa especial ahorrando para un motor -> fija la reserva y no bloquea ----
cfg = cfg_base()
fs = fake_brain(cfg)
fs.research_levels = {"impulse_drive": 2}
# Almacenes suficientes para que el coste (8000/16000/2400) no tenga storage_blocker.
p = P("1:1:4", {"metal_storage": 2, "crystal_storage": 2, "deut_tank": 1,
                "research_lab": 3}, m=50000, c=30000, d=0)
order = [("research", "impulse_drive", 3)]
done = fs._special_program_step(p, order)
assert done is False
r = p.savings_reserve
assert r is not None, "al ahorrar debe quedar fijada la reserva"
want = gd.research_cost("impulse_drive", 3)
assert (r.metal, r.crystal, r.deut) == (want.metal, want.crystal, want.deut)
# Con la reserva puesta, el planeta sigue teniendo excedente gastable de metal/cristal.
# Además, sin minas de deuterio (deut_synth 0) el cuello de botella tardaría ~24000h en
# llenarse a este ritmo: la producción de metal/cristal en ese tiempo sobra de sobra
# para cubrir toda la reserva, así que se libera entera (no hace falta guardarla ya).
s = economy.spendable_resources(p, cfg)
assert s.metal == 50000 and s.crystal == 30000 and s.deut == 0.0

# ---- 5: programa especial ahorrando para un EDIFICIO -> también reserva ----
cfg = cfg_base()
fs = fake_brain(cfg)
p = P("1:1:5", {"metal_storage": 3, "crystal_storage": 3}, m=10, c=10, d=0)
order = [("building", "solar_plant", 9)]
assert fs._special_program_step(p, order) is False
want = gd.building_cost("solar_plant", 1)
r = p.savings_reserve
assert r is not None and (r.metal, r.crystal, r.deut) == (want.metal, want.crystal, want.deut)

# ---- 6: si el paso del programa SE PUEDE pagar, arranca y no deja reserva ----
cfg = cfg_base()
fs = fake_brain(cfg)
fs.research_levels = {"impulse_drive": 2}
p = P("1:1:6", {"metal_storage": 2, "crystal_storage": 2, "deut_tank": 1,
                "research_lab": 3}, m=50000, c=30000, d=5000)
assert fs._special_program_step(p, [("research", "impulse_drive", 3)]) is False
assert fs.client.researched == ["impulse_drive"]
assert p.savings_reserve is None

# ---- 7: ahorrando deuterio para una investigación, instalaciones NO construye la
# fábrica de robots (cuesta deuterio y el reservado no se toca) ----
cfg = cfg_base()
fs = fake_brain(cfg)
fs.research_levels = {"impulse_drive": 2}
fs._get_planet_setting = lambda p, k, dflt=None: {"target_robotics_factory": 5}.get(k, dflt)
fs._facilities_step = MethodType(B._facilities_step, fs)
p = P("1:1:7", {"metal_storage": 2, "crystal_storage": 2, "deut_tank": 1,
                "research_lab": 3, "deut_synth": 10}, m=50000, c=30000, d=2000)
# impulse_drive 3 cuesta 8000/16000/2400: falta deuterio -> el programa reserva
assert fs._special_program_step(p, [("research", "impulse_drive", 3)]) is False
assert p.savings_reserve is not None and p.savings_reserve.deut == 2400
fs._facilities_step(p)
assert all(n != "robotics_factory" for _, n in fs.client.built), \
    f"robots no debe gastar el deuterio reservado: {fs.client.built}"

# Validez del test: SIN reserva y con los mismos recursos, robots SÍ se construye.
p2 = P("1:1:8", {"deut_synth": 10}, m=50000, c=30000, d=2000)
fs.client.built.clear()
fs._facilities_step(p2)
assert ("facilities", "robotics_factory") in fs.client.built, fs.client.built

print("OK")
