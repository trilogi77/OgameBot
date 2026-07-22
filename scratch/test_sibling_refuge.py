import os, sys, time, types, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot.brain import Brain
from ogbot.models import Coords, Planet

# Reproduce el desastre de Scorpius2 (22-jul): la luna [1:16:4]M bajo ataque no debe
# evacuar la flota al planeta hermano [1:16:4]P si ese planeta tiene un ataque pendiente
# detectado en un barrido anterior (recent_attacks). Debe irse remoto al 10%.

MOON = Coords(1, 16, 4, "moon")
PLANET = Coords(1, 16, 4, "planet")
REMOTE = Coords(1, 33, 11, "planet")


def make_moon():
    p = Planet(id="p", name="Mjolnir", coords=PLANET)
    m = Planet(id="m", name="Mjolnir Luna", coords=MOON, ships={"large_cargo": 100})
    p.has_moon = True
    p.moon = m
    return p, m


def stub(recent_attacks):
    sent = []
    client = types.SimpleNamespace(
        send_fleet=lambda o, d, ships, mission="", resources=None, speed_percent=1.0:
            (sent.append({"dest": d, "speed": speed_percent}) or True))
    f = types.SimpleNamespace(log=logging.getLogger("t"), client=client,
                              recent_attacks=recent_attacks, escaped_fleets=[])
    f._save_state = lambda: None
    f._coord_has_pending_attack = types.MethodType(Brain._coord_has_pending_attack, f)
    f._escape_attack_loc = types.MethodType(Brain._escape_attack_loc, f)
    return f, sent


now = time.time()
planet, moon = make_moon()
all_locs = [planet, moon, Planet(id="r", name="Rem", coords=REMOTE)]

# Caso A (bug): el planeta hermano tiene ataque pendiente en memoria; este barrido solo ve
# la luna. NO debe depositar en el planeta -> evasión remota al 10% al destino más lejano.
f, sent = stub({"1:16:4:planet": now + 600})
f._escape_attack_loc(moon, all_locs, {"1:16:4:moon": 60}, attacked_exact={"1:16:4:moon"})
assert len(sent) == 1, sent
assert sent[0]["dest"].tuple() == REMOTE.tuple(), sent[0]["dest"]
assert sent[0]["speed"] == 0.1, sent
assert sent[0]["dest"].tuple() != PLANET.tuple()  # NUNCA al hermano atacado

# Caso B (seguro): sin memoria de ataque al planeta -> deploy rápido al hermano al 100%.
f, sent = stub({})
f._escape_attack_loc(moon, all_locs, {"1:16:4:moon": 60}, attacked_exact={"1:16:4:moon"})
assert len(sent) == 1, sent
assert sent[0]["dest"].tuple() == PLANET.tuple() and sent[0]["dest"].type == "planet", sent[0]
assert sent[0]["speed"] == 1.0, sent

# _coord_has_pending_attack: futuro -> True; pasado (>5min) -> False; sin registro -> False.
f, _ = stub({"a": now + 100, "b": now - 400})
assert f._coord_has_pending_attack("a") is True
assert f._coord_has_pending_attack("b") is False
assert f._coord_has_pending_attack("missing") is False

# _next_own_fleet_recheck: flota propia que aterriza ANTES del impacto -> agenda llegada+20;
# aterriza después / a coord sin ataque / no hay ataques -> 0.
f, _ = stub({})
f._next_own_fleet_recheck = types.MethodType(Brain._next_own_fleet_recheck, f)
f.recent_attacks = {"1:16:4:planet": now + 600}          # ataque impacta en 10 min
mvs = [
    {"is_hostile": True, "destination": "1:16:4", "arrival_epoch": now + 600},   # el ataque (ignorar)
    {"is_hostile": False, "destination": "1:16:4", "arrival_epoch": now + 120},  # vuelta propia -> salvable
    {"is_hostile": False, "destination": "1:99:9", "arrival_epoch": now + 60},   # coord sin ataque
]
assert f._next_own_fleet_recheck(mvs, now) == now + 140, f._next_own_fleet_recheck(mvs, now)

# Aterriza DESPUÉS del impacto -> no se puede salvar -> 0.
f.recent_attacks = {"1:16:4:planet": now + 100}
assert f._next_own_fleet_recheck([{"is_hostile": False, "destination": "1:16:4", "arrival_epoch": now + 300}], now) == 0.0

# Sin ataques pendientes -> 0.
f.recent_attacks = {}
assert f._next_own_fleet_recheck(mvs, now) == 0.0

print("OK")
