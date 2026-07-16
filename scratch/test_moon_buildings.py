"""Instalaciones lunares + alimentación planeta -> luna.

Cubre lo que se rompe en silencio: el orden/bloqueo de las instalaciones lunares y
que la ronda de alimentación elija la luna como DESTINO y su planeta como fuente.
"""
import os, sys, types, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot.brain import Brain
from ogbot.models import Planet, Coords, Resources


def make_pair(target_base=1, target_phalanx=0, target_gate=0, moon_buildings=True,
              feed_moon=True):
    p = Planet(id="planet-1", name="Casa", coords=Coords(1, 2, 3, type="planet"),
               resources=Resources(500_000, 500_000, 500_000),
               ships={"large_cargo": 50})
    p.has_moon = True
    p.moon = Planet(id="planet-99", name="Casa (Luna)", coords=Coords(1, 2, 3, type="moon"),
                    resources=Resources(0, 0, 0))
    cfg = types.SimpleNamespace(
        planets_config={"1:2:3": {
            "enable_moon_buildings": moon_buildings, "feed_moon": feed_moon,
            "target_lunar_base": target_base, "target_sensor_phalanx": target_phalanx,
            "target_jump_gate": target_gate,
        }},
        keep_resources_buffer=0.0, feed_min_send=5000, feed_round_up=1000,
        empire_auto=False,
    )
    b = types.SimpleNamespace(cfg=cfg, log=logging.getLogger("t"), research_levels={},
                              active_slots=0)
    for m in ("_get_planet_setting", "_next_lunar_build", "_target_next_build",
              "_feed_deficit", "_feed_sendable", "_feed_step", "_moon_step", "_loc_key"):
        setattr(b, m, types.MethodType(getattr(Brain, m), b))
    b._build_finish_pending = lambda loc: False
    b._has_free_slots_for_mission = lambda extra_reserve=0: True
    return b, p, p.moon


def with_client(b, moon_cache_entry):
    """Completa el stub para poder llamar a _moon_step: caché de estado + cliente falso."""
    built = []
    b.state_cache = {"planets": {"1:2:3:moon": moon_cache_entry} if moon_cache_entry else {}}
    b._resync_targets = set()
    b._guard = lambda: True
    b._mark_build_started = lambda loc, name, cost: 0.0
    b.record_session_action = lambda *a, **k: None
    b.client = types.SimpleNamespace(
        build=lambda planet, comp, what: built.append((str(planet.coords), comp, what)) or True)
    return built


# --- 1) Orden y prerequisitos de las instalaciones lunares -------------------
b, p, moon = make_pair(target_base=2, target_phalanx=3, target_gate=1)
name, lvl, cost = b._next_lunar_build(moon)
assert (name, lvl) == ("lunar_base", 1), (name, lvl)   # sin base, todo empieza por la base
assert (cost.metal, cost.crystal, cost.deut) == (20000, 40000, 20000), cost

# Con base 1, la falange ya se puede: es la siguiente pendiente por orden.
moon.buildings["lunar_base"] = 2
name, lvl, _ = b._next_lunar_build(moon)
assert (name, lvl) == ("sensor_phalanx", 1), (name, lvl)

# La puerta de salto pide hiperespacio 7: bloqueada, se salta (no devuelve investigación
# ni un laboratorio, que en una luna no existe).
moon.buildings["sensor_phalanx"] = 3
assert b._next_lunar_build(moon) is None
b.research_levels = {"hyperspace_tech": 7}
name, lvl, _ = b._next_lunar_build(moon)
assert (name, lvl) == ("jump_gate", 1), (name, lvl)

# Objetivo cumplido -> nada que hacer.
moon.buildings["jump_gate"] = 1
assert b._next_lunar_build(moon) is None

# --- 2) Una luna nunca cae en la economía de planetas (minas) ----------------
b, p, moon = make_pair(target_base=1)
name, cost = b._target_next_build(moon)
assert name == "lunar_base", name
# Sin objetivos lunares no pide nada (y NO se inventa una mina en la luna).
b2, p2, moon2 = make_pair(target_base=0)
assert b2._target_next_build(moon2) is None

# --- 3) La alimentación manda del planeta a SU luna --------------------------
b, p, moon = make_pair(target_base=1)
sent = []
b._feed_transport = lambda src, dst, need: sent.append((src, dst, need)) or need
b._feed_step([p], movements=[])
assert len(sent) == 1, sent
src, dst, need = sent[0]
assert src is p and dst is moon, (src.coords, dst.coords)
# Déficit = coste redondeado hacia arriba (la luna no tiene nada).
assert (need.metal, need.crystal, need.deut) == (21000, 41000, 21000), need

# Sin 'Alimentar luna' la luna no es destino: no se manda nada.
b, p, moon = make_pair(target_base=1, feed_moon=False)
sent = []
b._feed_transport = lambda src, dst, need: sent.append((src, dst, need)) or need
b._feed_step([p], movements=[])
assert not sent, sent

# La luna ya paga su objetivo -> no hace falta transporte.
b, p, moon = make_pair(target_base=1)
moon.resources = Resources(50_000, 50_000, 50_000)
sent = []
b._feed_transport = lambda src, dst, need: sent.append((src, dst, need)) or need
b._feed_step([p], movements=[])
assert not sent, sent

# --- 4) Caché obsoleta: releer antes de gastar --------------------------------
# Una caché escrita por la versión que descartaba los ids lunares no tiene NINGUNA clave
# lunar: con ella creeríamos que todo está a 0 y reconstruiríamos lo ya construido.
b, p, moon = make_pair(target_base=1, target_gate=1)
b.research_levels = {"hyperspace_tech": 7}
moon.resources = Resources(9_000_000, 9_000_000, 9_000_000)   # de sobra para la puerta
built = with_client(b, {"buildings": {}, "scanned_at": 0.0})
b._moon_step(moon)
assert not built, built                                # no construye a ciegas...
assert b._resync_targets == {"1:2:3:moon"}, b._resync_targets   # ...pide releer la luna

# Caché ya escaneada con el código nuevo: las claves están (aunque sean 0) -> construye.
b, p, moon = make_pair(target_base=1)
moon.resources = Resources(100_000, 100_000, 100_000)
built = with_client(b, {"buildings": {"lunar_base": 0, "sensor_phalanx": 0}, "scanned_at": 0.0})
b._moon_step(moon)
assert built == [("[1:2:3]M", "facilities", "lunar_base")], built
assert not b._resync_targets, b._resync_targets

# Desactivado por planeta -> ni mira.
b, p, moon = make_pair(target_base=1, moon_buildings=False)
moon.resources = Resources(100_000, 100_000, 100_000)
built = with_client(b, {"buildings": {"lunar_base": 0}, "scanned_at": 0.0})
b._moon_step(moon)
assert not built, built

# Sin recursos no construye (los trae la alimentación, no se inventa nada).
b, p, moon = make_pair(target_base=1)
built = with_client(b, {"buildings": {"lunar_base": 0}, "scanned_at": 0.0})
b._moon_step(moon)
assert not built, built

print("OK")
