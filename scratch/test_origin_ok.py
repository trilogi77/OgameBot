import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ogbot.brain import Brain


def coords(t="planet"):
    return types.SimpleNamespace(galaxy=1, system=2, position=3, type=t)


def loc(t):
    return types.SimpleNamespace(coords=coords(t))


def fake(planets_config):
    f = types.SimpleNamespace(cfg=types.SimpleNamespace(planets_config=planets_config))
    f._get_planet_setting = types.MethodType(Brain._get_planet_setting, f)
    f._origin_ok = types.MethodType(Brain._origin_ok, f)
    return f


# Default (sin ajuste) -> both: planeta y luna válidos.
f = fake({})
assert f._origin_ok(loc("planet"), "expeditions") is True
assert f._origin_ok(loc("moon"), "expeditions") is True

# from=planet -> solo planeta.
f = fake({"1:2:3": {"expeditions_from": "planet"}})
assert f._origin_ok(loc("planet"), "expeditions") is True
assert f._origin_ok(loc("moon"), "expeditions") is False

# from=moon -> solo luna.
f = fake({"1:2:3": {"recycling_from": "moon"}})
assert f._origin_ok(loc("moon"), "recycling") is True
assert f._origin_ok(loc("planet"), "recycling") is False

# El selector de una función no afecta a otra (la otra queda en both).
f = fake({"1:2:3": {"farming_from": "moon"}})
assert f._origin_ok(loc("planet"), "expeditions") is True
assert f._origin_ok(loc("planet"), "farming") is False

print("OK")
