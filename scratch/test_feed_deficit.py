import os, sys, types, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot.brain import Brain
from ogbot.models import Resources

cost = Resources(51200, 15360, 25600)  # fábrica de robots 7->8

def make_fake(round_up):
    return types.SimpleNamespace(
        log=logging.getLogger("t"),
        cfg=types.SimpleNamespace(keep_resources_buffer=0.10, feed_round_up=round_up),
        _target_next_build=lambda p: ("robotics_factory", cost),
    )

# Sin redondeo: debe alimentar hasta coste/(1-buffer) = coste/0.9, no solo coste.
fake = make_fake(0)
planet = types.SimpleNamespace(resources=Resources(0, 0, 0), coords="1:2:3")
need = Brain._feed_deficit(fake, planet)
assert abs(need.metal - 51200 / 0.9) < 1, need.metal
assert abs(need.crystal - 15360 / 0.9) < 1, need.crystal
assert abs(need.deut - 25600 / 0.9) < 1, need.deut
# Y debe superar el coste bruto (antes se quedaba justo en el coste -> corto).
assert need.metal > cost.metal

# Si ya tiene coste/buf, no hace falta alimentar.
full = types.SimpleNamespace(
    resources=Resources(51200 / 0.9, 15360 / 0.9, 25600 / 0.9), coords="1:2:3")
assert Brain._feed_deficit(fake, full) is None

# Con redondeo (+1k): cada componente sube al siguiente múltiplo de 1000 + 1000.
# Ejemplos del usuario: 51k->52k, 20k->21k.
cost2 = Resources(51000, 20000, 0)
fk = make_fake(1000)
fk._target_next_build = lambda p: ("x", cost2)
# keep_resources_buffer=0 para aislar el redondeo del colchón.
fk.cfg.keep_resources_buffer = 0.0
empty = types.SimpleNamespace(resources=Resources(0, 0, 0), coords="1:2:3")
need2 = Brain._feed_deficit(fk, empty)
assert need2.metal == 52000, need2.metal
assert need2.crystal == 21000, need2.crystal
assert need2.deut == 0, need2.deut   # componente ya cubierto -> no se envía

print("OK")
