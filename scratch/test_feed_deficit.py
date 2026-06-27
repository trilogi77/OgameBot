import os, sys, types, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot.brain import Brain
from ogbot.models import Resources

cost = Resources(51200, 15360, 25600)  # fábrica de robots 7->8
fake = types.SimpleNamespace(
    log=logging.getLogger("t"),
    cfg=types.SimpleNamespace(keep_resources_buffer=0.10),
    _target_next_build=lambda p: ("robotics_factory", cost),
)

# Planeta vacío: debe alimentar hasta coste/(1-buffer) = coste/0.9, no solo coste.
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

print("OK")
